# PLAN — F42: a build must never fail only because the hardware is slow

Base `e5187f70` (v0.11.10). Read-only on the code. Assumes F39/F40 (0.11.11) has landed: the launcher carries
the manifest per-step timeout in its spec (`step_timeout`), meshcore-cli declares 1800 s + a marker, and a
timed-out web job's detail says "timed out after Ns" (`code-review/PLAN-F39-F40.md` A+B+C). File:line refer to
`e5187f70`. The maintainer's rule: *a non-updatable stack is bad; just raising the timeout is a bad solution.*

## 1. The problem in one paragraph
Every build step today has one wall-clock limit: `run_job` → `run_streaming` (`jobs.py:128-131`,
`backends.py:345` fast path / `:413-426` controlled path) and the launcher (`build_launcher_runtime.py:141`,
`p.wait(timeout=…)`). The limit cannot tell a Pi that is compiling `dbus-fast`/`PyNaCl`/`pycryptodome` for 35 min
(armv7 source builds, a slow SD card, a throttled CPU, three parallel niced pip jobs) from a step that sleeps
forever on a dead socket. Raising the number only moves the cliff and makes a real hang cost longer. The fix is
to kill on **lack of progress**, and keep the wall-clock only as a generous outer ceiling.

## 2. Slow vs stuck — what the box can measure
All three runners already start the step with `start_new_session=True` and capture a `proctree.SessionToken`
(`backends.py:335-341`, `:365-379`; `build_launcher_runtime.py:131,139`). `proctree.session_member_details`
(`proctree.py:72-97`) already walks `/proc/*/stat` for the session. So the **session** is the measurement unit:

| Signal | Source | Slow compile | Stuck step | Notes |
|---|---|---|---|---|
| **CPU time** | `/proc/<pid>/stat` fields 14-17 (utime, stime, cutime, cstime) of every live session member | grows (gcc/cc1/cython children; their time lands in the parent's `cutime` when reaped) | ~0 | world-readable; nice 10 + ionice idle (`backends.py:281-297`) slows it but never stops it |
| **Output** | size of the job log: `os.fstat(log_fh)` (fast path), bytes drained (controlled path), `os.fstat(1)` in the launcher (its stdout *is* the log, `lifecycle.py:222`) | sometimes (pip prints "Building wheel … started", then nothing until "finished") | none | `PYTHONUNBUFFERED=1` is already set on both paths (`jobs.py:129`, `build_launcher_runtime.py:135`) |
| **I/O** | `/proc/<pid>/io` `rchar + wchar` per member | grows | ~0 | same-uid read (OK); a download writes to disk → `wchar` grows even if `recv()` is not counted in `rchar` |

- **Cgroup:** not usable. The launcher runs inside `lhpc-web.service`'s cgroup (shared with the controller), and
  `systemd-run --user` scopes are known to fail on these boxes (`updater_units.py:14,126`).
- **Per-pid deltas, not a sum:** progress = Σ over pids seen in both samples of (now − before) + Σ totals of new
  pids. A member that exits is then never a "negative" sample, and short-lived `cc1` processes count.
- **pip verbosity:** do **not** add `pip -v`. It would put every compiler line into the log (MiBs on a Zero) and
  is not needed: the compile is visible as CPU. `--progress-bar off` changes nothing off a TTY.

### The stall rule (one rule, three runners)
> Sample every 15 s. A sample is **progress** when the session gained ≥ 0.25 s CPU, or the log grew by ≥ 1 byte,
> or `rchar+wchar` grew by ≥ 64 KiB since the previous sample. A step is **stalled** when no sample was progress
> for `STALL_S` (default **600 s**). A step is **over budget** when it ran longer than its ceiling.

- Ceiling = `max(comp.build_timeout, BUILD_CEILING_S)`, with `BUILD_CEILING_S = 14400` (4 h). meshtastic
  (21600) and meshcom-qemu (28800) keep their larger value. The ceiling only stops runaway loops.
- `STALL_S` env override `LHPC_BUILD_STALL_S`, the same strict parse as `_step_timeout` (`:34-44`; malformed,
  ≤0 or NaN → exit 3, never "no stall check").
- **Host tests stay as they are** (Q2): ceiling = `test_timeout or TEST_TIMEOUT_S`, no stall rule. A test is
  meant to be bounded; slow hardware is a build problem.

## 3. Where it lives
- **New module `lhpc/core/progress.py`** (~70 lines): `SessionSampler(sid, exclude_pid, out_size)` with
  `sample() -> bool` (progress since the last call), and `Watch(stall_s, ceiling_s, clock, sampler)` with
  `check() -> None | "stalled" | "budget"`. `clock` and `sampler` are injectable, so the tests need no real
  minutes. `/proc` parsing reuses the `rindex(")")` idiom of `proctree.py:87-89`.
- **`RealCommandRunner.run_streaming`** (`backends.py:303`): it gets `stall_s: float | None = None`. The fast path
  replaces `proc.wait(timeout=…)` (`:345`) with a 1 s wait loop plus `watch.check()`. The controlled path adds
  the check to its existing 0.1 s loop (`:413-426`), sampled every 15 s. `CommandResult` gets
  `stalled: bool = False` (`backends.py:29-45`). `timed_out` stays True for both reasons, so every existing caller
  (`JobState.TIMEOUT`, unsafe handling `jobs.py:181-195`) is unchanged. `run()` (generic, `:224`) is untouched.
- **`run_job`** (`jobs.py:57`) passes `stall_s` through. The tail marker (`:153`) becomes one of:
  - `[STALLED: no CPU, output or I/O for 10 min — job was KILLED; result is INCOMPLETE]`
  - `[EXCEEDED the build budget of 4 h — job was KILLED; result is INCOMPLETE]`
  `Lifecycle.build` (`lifecycle.py:369,395-398`) passes `stall_s=STALL_S` and the ceiling. `host_test` passes none.
- **Launcher** (`build_launcher_runtime.py`): `_run_step` (`:117-153`) uses the same `Watch` and returns
  `(rc, reason, unverified)`. `run()` reads `spec["step_timeout"]` (F40 A) as the ceiling and
  `spec["stall_s"]` (new, put there by `_spawn_build` for op build only, `service_lifecycle_ops.py:3494`).
  Detail texts (`:306,309`): `stalled for 10 min: <argv>` / `exceeded the budget of 4 h: <argv>`. The
  unverified branch keeps its "cessation UNPROVEN" text and adds the reason.
- `service_lifecycle_ops.py:3219-3222` (`[timeout] build <id>`) prints the reason from the tail marker unchanged —
  no edit needed, because the marker is folded into the tail.

## 4. Cheap wins first (separate commits, independent of the stall rule)
1. **Constraints for meshcore-cli** (option G of F39/F40): `lhpc/data/meshcore-cli-constraints.txt` pinning the
   closure (meshcore, bleak, dbus-fast, pycryptodome, pyserial-asyncio-fast, prompt_toolkit, requests…). Use the
   **same `dbus-fast==5.0.22`** as `meshcore-webui-constraints.txt:20`, so one wheel serves both venvs from
   `PIP_CACHE_DIR`. Then a new upstream sdist-only release can no longer trigger a surprise compile.
2. **`--prefer-binary`** on the unpinned MeshCore pip steps only (manifest :2240, :2730). This matters on aarch64
   too: a fresh release often uploads its sdist hours before its wheels.
3. **piwheels: no.** We would be adding a third-party index to every box. It helps only armv7, and the
   documented target is 64-bit (`README.md:94`, `deps.py:853`). Raspberry Pi OS's own `/etc/pip.conf` (if it
   lists piwheels) is honoured by pip anyway; the plan does not write one.
4. **Can armv7 be verified?** No. The testlab runner and `release-verify` are aarch64
   (`docs/maintenance.md:37-39`), and the images are 64-bit. An armv7 build is field-box evidence only (§7).

## 5. Tests (each RED before, GREEN after; `tests/core/`, no sleeps longer than ~3 s)
| module::name | Asserts | Why red before |
|---|---|---|
| `test_progress.py::test_idle_session_is_stalled` | fake clock + fake sampler returning no progress → `check()` = `"stalled"` after `stall_s`, never before | module absent |
| `test_progress.py::test_cpu_only_progress_is_not_stalled` (param: cpu / output / io) | one signal grows each sample, clock advanced to 10× `stall_s` → `None` | module absent |
| `test_progress.py::test_ceiling_wins_over_progress` | progress every sample, clock past ceiling → `"budget"` | module absent |
| `test_progress.py::test_exited_member_is_not_negative` | sampler over two snapshots where a pid vanished and a new one appeared → progress counted, no exception | module absent |
| `test_bounded_runner.py::test_sleeping_step_is_killed_as_stalled` (real process, both fast and controlled path) | `sleep 30` with `stall_s=1`, sample 0.2 s → `timed_out and stalled`, session terminated, < 5 s | wait runs until the timeout |
| `test_bounded_runner.py::test_cpu_burning_step_outlives_the_stall_window` | `python -c` busy loop for 3 s, `stall_s=1`, ceiling 30 → rc 0, not stalled (the scaled "longer than the old 1800 s") | killed at the 1 s wall-clock if `stall_s` were the timeout; red today because the kwarg does not exist |
| `test_jobs.py::test_stalled_and_budget_markers` (param) | log tail contains `STALLED` resp. `EXCEEDED the build budget` | one generic marker |
| `test_build_launcher_runtime.py::test_stalled_step_detail` / `::test_budget_step_detail` | `_read_raw` detail starts `stalled for` / `exceeded the budget of` | detail is "timed out after" |
| `test_build_launcher_runtime.py::test_malformed_stall_fails_safe` (param `0`, `-1`, `nan`, `x`) | `SystemExit(3)`, no step ran | env ignored |
| `test_build_timeout.py::test_build_ceiling_is_hours` | `Lifecycle.build` passes ceiling ≥ 14400 for meshcore-cli and 28800 for meshcom; `host_test` passes no `stall_s` | 1800 / no stall |

Mark the two real-process tests `slow` (they are timed loops, `tests/README.md` Markers). Existing tests
that force a timeout with `LHPC_BUILD_STEP_TIMEOUT_S` keep working: the env still sets the ceiling.
`test_default_build_timeout_is_hardware_realistic` (`test_build_timeout.py:80-87`) changes its expectation to the
ceiling. Run `python -m pytest tests/core tests/web tests/install tests/stacks tests/repo -n 8` before each push.

## 6. Live proof (Pi 5, needed)
1. **A slow compile is not killed:** `systemctl --user edit lhpc-web` →
   `Environment=LHPC_BUILD_STALL_S=60` and `Environment=PIP_NO_BINARY=dbus-fast,pycryptodome`, then restart.
   The launcher inherits the unit env (`build_launcher_runtime.py:300`). Web **Build** MeshCore. The pip step
   is silent for several minutes while `top` shows `cc1`. All three jobs go green and the meshcore-cli marker exists.
2. **A stuck step is killed:** same settings. During meshcore-cli's pip step, `kill -STOP <pip pid>`. About
   60–75 s later the banner reads `stalled for 1 min: .venv/bin/pip install .`, the log ends with `[STALLED …]`
   on the CLI path (repeat with `lhpc build meshcore-cli`), and no member of the session survives.
3. Remove the override, restart, and Build again: green with the wheels (the normal path is unchanged).
4. On the field box only (§7): one `lhpc build meshcore-cli` with the fixed log, to see the real step duration.

## 7. Risks and what cannot be verified here
- **Too eager:**
  - A recovering network stall. pip retries every 15 s; each retry gains CPU and log lines ("Retrying …").
  - A slow `git clone` is **not** a build step. It has its own `_CLONE_TIMEOUT_S` (`install.py:1962`) and is
    out of scope.
  - A download writes to disk (`wchar`).
  - Ten minutes with zero CPU, zero output and zero I/O is not a wait that recovers on its own.
  - The ≥ 0.25 s / 15 s threshold is deliberately low. A niced compile on a busy Zero still gains seconds per
    sample.
- **Too lax:** a busy-polling hang (CPU > 1.7 %) is not a stall. The 4 h ceiling ends it. Accepted.
- **Invisible members:** a descendant that `setsid()`s leaves the session (`proctree.py:52-57`), so its CPU is
  not counted. This can only make a step look *more* idle. No current build step does this. If one appears, its
  step gets a per-step `stall_timeout` manifest key (not planned now).
- **Swap thrash on 512 MB:** CPU still accrues slowly and `majflt` I/O shows in `rchar`. If a box thrashes for
  10 min with neither, it is effectively dead, and killing the build frees the RAM.
- **Not verifiable without the field box:** `uname -m` (armv7l vs aarch64), its `/etc/pip.conf`, the real per-step
  duration, whether `rchar` counts socket reads on its kernel (wchar covers downloads either way), and whether
  it ran 3 parallel web builds.

## 8. Commits
1. `F42: meshcore-cli builds against a constraints file and prefers wheels` (§4.1-4.2, manifest + constraints
   file + its pin-consistency entry). Small, independently valuable, lands first.
2. `F42: progress module — stall and budget watch` (`progress.py` + `test_progress.py`, no caller yet).
3. `F42: run_streaming/run_job end a build on a stall, not on the clock` (backends, jobs, lifecycle, ceiling
   constant, bounded-runner/jobs/build-timeout tests).
4. `F42: the web launcher uses the same stall rule` (launcher, `_spawn_build` spec key, launcher tests, docs,
   CHANGELOG).

**Docs (one place):** `docs/maintenance.md:268-276` replaces "The per-step build timeout defaults to 900 s …"
with: a build step is ended when it shows no CPU, output or disk/network activity for 10 min
(`LHPC_BUILD_STALL_S`), or when it exceeds its budget (4 h, more where the manifest says so). The existing
"judge by CPU" hint stays, now as the operator's view of the same rule.

**CHANGELOG (operator's words):**
- "A build on a slow Pi is no longer killed after 15 or 30 minutes while it is still compiling. LHPC now stops a
  build step only when it has done nothing — no CPU, no output, no disk or network activity — for 10 minutes,
  or when it runs longer than 4 hours (8 h for the MeshCom firmware)."
- "A stopped build now says why: *stalled for 10 min* or *exceeded the budget of 4 h*."
- "The MeshCore CLI is built from a pinned set of packages and prefers ready-made wheels, so a new upstream
  release can no longer force a long compile."

## 9. Open questions (recommendation)
- **Q1** Should the now-smaller `build_timeout` values (900/1800/3600 at manifest :924, :2226, :2626, :2849,
  :3262, :3308, :3361, :3571) be deleted, given that they become inert under `max(…, 4 h)`? → **Yes, in commit 3.**
  Otherwise they mislead whoever reads them. Keep the key for meshtastic and meshcom.
- **Q2** Should the stall rule also apply to host tests? → **No.** A hung test already ends at `test_timeout`
  (≤ 900 s), sooner than a 10 min stall would. Revisit only if a slow box fails a test on its timeout.
- **Q3** Is `STALL_S = 600` right? → **Yes** for the release; it is twice the longest silent, CPU-free phase I can
  name (pip's 5 × 15 s retry chain plus backoff). Env override for the field.
- **Q4** Should pip run with `-v` so that output alone proves progress? → **No.** CPU is the stronger signal, and
  `-v` bloats logs on the Zero.
- **Q5** piwheels for armv7? → **No** (§4.3). First ask the field box for `uname -m`. If it is armv7, the answer is
  the 64-bit image, not a third-party index.

## 10. Self-check
- Re-read at `e5187f70`:
  - `jobs.py:57-196`; `backends.py:29-45,224-430`; `proctree.py:1-130`.
  - `build_launcher_runtime.py:34-44,117-153,174,280-340`; `lifecycle.py:222,331-400,1479-1502`.
  - `service_lifecycle_ops.py:3213-3222,3486-3500`; manifest `build_timeout`/`test_timeout` lines (grep, 13 hits).
  - `docs/maintenance.md:37-39,262-296`; `tests/README.md`; `updater_units.py:14,126`.
  - constraints files in `lhpc/data/`.
- One rule in one module, used by all three runners. Callers keep `JobState.TIMEOUT` and the unsafe semantics.
  Tests use an injected clock and sampler, plus two short real-process `slow` tests.
- Not run: no prototype. Line counts are estimates (~70 progress, ~40 backends, ~15 jobs/lifecycle, ~30 launcher).
