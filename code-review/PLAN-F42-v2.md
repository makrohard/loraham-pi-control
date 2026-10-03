# PLAN v2 — F42: a build is not ended for being slow

Base `e5187f70` (v0.11.10). Read-only on the code. It replaces `code-review/PLAN-F42.md` (v1, review RED). It
assumes F39/F40 A+B+C (0.11.11) has landed: the launcher spec carries `step_timeout` (the manifest value),
`_step_timeout(spec_value)` reads env, then spec, then 1800, and a timed-out web step's detail starts "timed out".
File:line refer to `e5187f70` and were re-read for this version. Maintainer's rule: *a non-updatable stack is bad;
just raising the timeout is a bad solution.*

## 0. What changed from v1 (one line per review finding)
| # | v1 | v2 |
|---|---|---|
| 1 | A sample counted as progress only above a threshold (0.25 s CPU, 64 KiB I/O), so smaller gains were thrown away | Progress is **any** increase of a cumulative counter against the last sample. There are no thresholds and nothing is lost (§2) |
| 2 | The web launcher used `spec["step_timeout"]` (1800) as its ceiling, so it kept the old cap | One function, `progress.build_limits`, owns the precedence. The launcher calls it too, so a web Build gets the same ceiling as the CLI (§3) |
| 3 | The auto-install path was never named | Its call chain is named (§4) and has its own test (§7) |
| 4 | A 4 h hard ceiling contradicted "never fail because slow" | A **24 h runaway guard**, shared by all paths and documented as a guard, not as a performance limit (§3, §8) |
| 5 | "Never recovers" stated as a fact | Now worded as policy: 600 s without any observable activity counts as stalled. Q3 records 600 s as a policy choice |
| 6 | The sample interval was fixed; no `/proc` races; PIDs keyed by number | The interval is injectable. A `/proc` ENOENT/EACCES read means "unknown", which counts as progress. Members are keyed by `(pid, starttime)` |
| 7 | The live proof STOPped only the pip PID | It STOPs the **whole session** (`pkill -STOP -s <sid>`) (§9) |
| 8 | `--prefer-binary` on unpinned steps | Only on meshcore-cli, together with its new constraints file. The pinned steps get a no-op note; meshcore-node's unpinned `install .` gets nothing (§6) |

## 1. The problem
At present a build step has exactly one limit: wall-clock time.
- **CLI:** `Lifecycle.build` (`lifecycle.py:369`) → `run_job` (`lifecycle.py:395`) → `run_streaming` (`jobs.py:128`).
  The step then ends either in `proc.wait(timeout=…)` (fast path, `backends.py:345`) or at the `deadline`
  (controlled path, `backends.py:413-426`).
- **Web Build:** the launcher stops a step in `p.wait(timeout=timeout)` (`build_launcher_runtime.py:142`).

A wall clock cannot tell a Pi that is compiling `dbus-fast`/`pycryptodome` for 40 minutes from a step blocked on a
dead socket. v2 ends a step when it **shows no activity**. The wall clock stays only as a 24 h guard against
runaway steps.

## 2. The stall rule (one module, every build path)
**Unit of measurement: the step's session.** Every runner already starts the step with `start_new_session=True`
and captures a `proctree.SessionToken` (`backends.py:331/341`, `:369/379`; `build_launcher_runtime.py:131/140`). `proctree.session_member_details` (`proctree.py:72-96`) already enumerates the
members of a session through `/proc/*/stat`.

**One sample** reads three things:
- **Per member, keyed by `(pid, starttime)`:**
  - `utime+stime+cutime+cstime` from `/proc/<pid>/stat`. starttime is field 22, the same field that
    `SessionToken` uses (`proctree.py:20-27`).
  - `rchar+wchar` from `/proc/<pid>/io`.
- **For the whole step:** the size of the job log.
  - Fast path: `os.fstat(log_fh.fileno())`.
  - Controlled path: the count of bytes drained.
  - Launcher: `os.fstat(1)`. The launcher's stdout *is* the log (`lifecycle.py:222`).

> **Rule.** A sample is **progress** when, compared with the previous sample, any of these is true:
> - a counter of a member present in both samples increased by any amount (≥ 1 tick or 1 byte);
> - a new `(pid, starttime)` appeared (a process was spawned);
> - the log grew;
> - the sample is **unknown**.
>
> A step is **stalled** after `stall_s` (default **600 s**, a policy choice) with no progress sample. It is
> **over budget** when its runtime exceeds `ceiling_s`. Samples are taken every `sample_s` (default 15 s,
> injectable).

- **Nothing is discarded.** The counters are cumulative, and the baseline is simply the previous sample. A
  contended compile that gains 1 tick (10 ms) per sample is progress at every sample. Even a member that gains
  1 tick every 2 minutes counts.
- **"Unknown"** = `/proc` cannot be listed, a listed member's `stat`/`io` raises `OSError` (ENOENT: it exited
  mid-sample; EACCES), or its `stat` cannot be parsed. Unknown counts as progress, never as a stall. The build runs as the same user, so `io` can be
  read on supported boxes. If `io` stays unreadable, the result degrades to the 24 h guard, never to a false kill.
- **Members that exit.** A member that disappears is simply absent from the comparison, so no delta can turn
  negative. When it is reaped, its CPU time appears in the parent's `cutime`, which can count the same work
  twice. That bias is toward "alive" and is intended; a test covers it.
- **PID reuse.** A reused PID has a different starttime, so it counts as a new member. Its lower counters are
  never compared with the old process's counters.
- **Not added:** `pip -v` (Q4); cgroups (the launcher shares `lhpc-web`'s cgroup; `systemd-run --user` fails
  on these boxes, `updater_units.py:14,126`).

## 3. The limits: one precedence rule, in one function
```
progress.build_limits(manifest_s, environ, explicit_s=None) -> (stall_s, ceiling_s)
  stall_s   = parse(environ["LHPC_BUILD_STALL_S"])        if set, else STALL_S = 600
  ceiling_s = explicit_s                                  if given  (Lifecycle.build(timeout=…))
            else parse(environ["LHPC_BUILD_STEP_TIMEOUT_S"]) if set (operator/test override, used AS IS)
            else max(manifest_s or 0, BUILD_CEILING_S)    # BUILD_CEILING_S = 86400 (24 h)
  parse(v): float(v), and math.isfinite(v) and v > 0; otherwise ValueError
            (so "0", "-1", "nan", "inf", "x" are all rejected)
```
- **Both limits always apply, and whichever fires first ends the step.**
  - An explicit or env ceiling is honoured even when it is short. The tests that force a timeout rely on this
    (`test_webjob.py:115` at 0.3 s, `test_build_launcher_runtime.py:367` at 0.6 s).
  - Without one, the ceiling is ≥ 24 h on every path. No manifest value can lower it.
- **`LHPC_BUILD_STEP_TIMEOUT_S` is defined by this rule for builds on every path.** At present only the launcher
  reads it (`build_launcher_runtime.py:38`); `grep` finds no other reader in `lhpc/`.
- **`+inf` is rejected.** `_step_timeout`'s check `not (t > 0)` (`:39`) would accept `inf`, and an infinite stall
  window would quietly disable the stall check. `parse` therefore requires `isfinite`. The same fix applies to
  `_step_timeout` for the test path.
- **A malformed value never means "no limit":** the CLI's `Lifecycle.build` returns a typed `FAILED` before any
  step (the marker `BLOCKED` pattern, `lifecycle.py:377-380`); the launcher exits 3 (as `_step_timeout`, `:34-44`).
- **Host tests are unchanged (Q2):** env, then `test_timeout`, then `TEST_TIMEOUT_S`, with no stall rule
  (`lifecycle.py:1491`, F40 A).
- **About the guard.** A step that is busy-looping, or wakes periodically, counts as progress. Only the guard
  ends it. That is the price of never discarding progress, and we accept it. We know of no build step that
  needs 24 h; meshcom-qemu on a Zero is ~68 min (`docs/maintenance.md:268`). The guard therefore ends only
  runaway steps.

## 4. Wiring (every build path calls `build_limits`)
- **New `lhpc/core/progress.py`** (~90 lines):
  - `STALL_S`, `BUILD_CEILING_S`, `SAMPLE_S`, `build_limits`, `parse`.
  - `SessionSampler(sid, exclude_pid, out_size)`, whose `sample()` returns `bool` (unknown → True). It parses
    `/proc` with the `rindex(")")` idiom (`proctree.py:89`).
  - `Watch(stall_s, ceiling_s, sample_s, clock, sampler)`, whose `check()` returns `None`, `"stalled"` or
    `"budget"`. It samples only when `sample_s` has elapsed; `clock` and `sampler` are injectable.
- **`run_streaming`** (`backends.py:303`) gets `stall_s=None` and `sample_s=SAMPLE_S`.
  - Fast path: `:345` becomes a 1 s `proc.wait` loop with `watch.check()`.
  - Controlled path: the check joins the existing 0.1 s loop, next to the deadline (`:413-426`).
  - `CommandResult` (`:29-45`) gets `stop_reason: str = ""`. `timed_out` stays True for both reasons, so
    `JobState.TIMEOUT` and the unsafe handling in `jobs.py:181-195` are unchanged. `run()` (`:224`) is
    untouched.
- **`run_job`** (`jobs.py:57`) passes `stall_s` through. The marker at `:153` keeps its "TIMED OUT" prefix, so
  existing greps still match:
  - `[TIMED OUT — stalled: no CPU, output or I/O for 10 min — job was KILLED; result is INCOMPLETE]`
  - `[TIMED OUT — runaway guard of 24 h reached — job was KILLED; result is INCOMPLETE]`
- **`Lifecycle.build`:** `:369` becomes `stall_s, eff = progress.build_limits(comp.build_timeout, os.environ,
  timeout)`. It passes `stall_s` to `run_job` (`:395`). `host_test` passes no `stall_s`.
- **Launcher (web Build):**
  - `run()` (`:174`): for `op == "build"` (already read at `:180`) it calls
    `progress.build_limits(spec.get("step_timeout"), os.environ)`. For `op == "test"` it keeps
    `_step_timeout(...)`.
  - Env is read at exec time (unit env inherited, `:300`): **no new spec key**; v1's `spec["stall_s"]` is dropped.
  - `_run_step` (`:117-153`) uses the same `Watch`, with `sample_s = spec.get("sample_s", SAMPLE_S)`. Only tests
    write that key. It returns `(rc, reason, unverified)`.
  - Detail at `:309`: `timed out (stalled, no activity for 10 min): <argv>` or `timed out (runaway guard 24 h):
    <argv>`. The unproven branch at `:306` keeps "cessation UNPROVEN" and adds the reason.
- **Auto-install uses the same chain as the CLI:** `service_auto_install.py:1382` `self.build(st.id, …)` →
  `LifecycleOps.build` (`service_lifecycle_ops.py:3029`) → `life.build(comp, …)` (`:3213`, no `timeout` argument)
  → `Lifecycle.build`. HMAC apply (`service_hmac.py:963`) and web **Install** take the same route; web Install
  runs `python -m lhpc install`. So they all get the rule with no extra code. A test pins this down (§7).
- `[timeout] build <id>` (`service_lifecycle_ops.py:3219-3222`) prints the tail: the reason shows, no edit.

## 5. Manifest (Q1 — yes, because the ceiling is shared)
- **The values become inert.** Under `max(…, 86400)` all ten `build_timeout` values become inert: manifest :924,
  :1220, :1943, :2226, :2626, :2849, :3262, :3308, :3361, :3571, all ≤ 28800. Commit 3 deletes them.
- **The key stays.** The parser keeps `build_timeout` for a future step that truly needs more than 24 h.
- `Lifecycle.BUILD_TIMEOUT_S` (`lifecycle.py:331`) is replaced by `progress.BUILD_CEILING_S`; F40 A's
  `_spawn_build` then passes the raw manifest value (0 = absent), which `build_limits` handles. `test_timeout`
  values are untouched.

## 6. Cheap wins (commit 1, independent)
1. **Constraints file for meshcore-cli.** `lhpc/data/meshcore-cli-constraints.txt` pins the full closure. The
   step at manifest :2730 becomes `pip install -c {asset}/meshcore-cli-constraints.txt .`.
   - It uses the same `dbus-fast==5.0.22` as `meshcore-webui-constraints.txt:20`, so one cached wheel serves
     both venvs.
   - `tests/repo/test_packaging.py:56` already globs `*constraints.txt`, so the new file is checked
     automatically.
2. **`--prefer-binary` goes on this one step only**, the meshcore-cli step behind its constraints file.
   - With `==` pins it cannot change any pinned version. It only affects a transitive dependency that the file
     does not list yet: pip then picks that dependency's newest wheel over a newer sdist-only release.
   - It is **not** added to the steps that are already fully pinned: webui :2622, repeater :2247, meshchat
     :3609. There it would be a no-op.
   - It is **not** added to meshcore-node's unpinned `install . pytest pytest-asyncio` (:2240). There it would
     change version resolution.
3. **piwheels: no** (Q5). **armv7 cannot be verified here:** the testlab and release-verify runners are aarch64
   (`docs/maintenance.md:37-39`).

## 7. Tests (RED before, GREEN after; no sleep longer than ~3 s)
| module::name | asserts | why it is RED today |
|---|---|---|
| `core/test_progress.py::test_any_increase_is_progress` (param: 1 cpu tick / 1 io byte / 1 log byte / new member) | `sample()` is True | module absent |
| `::test_tiny_steady_progress_never_stalls` | fake clock; +1 tick per sample for 10 × `stall_s` → `check()` stays None (the reviewer's 0.20 s/15 s case) | module absent |
| `::test_idle_session_stalls_after_stall_s_not_before` | no change → None at `stall_s − ε`, `"stalled"` at `stall_s` | module absent |
| `::test_unknown_counts_as_progress` (param: listdir OSError / stat ENOENT after listing / io EACCES / unparsable stat) | True, no exception | module absent |
| `::test_reused_pid_is_a_new_member` | same pid, new starttime, lower counters → progress, no negative delta | module absent |
| `::test_reaped_child_shows_in_parent_cutime` | child gone, parent `cutime` up → progress | module absent |
| `::test_guard_ends_a_busy_step` | progress at every sample, clock past ceiling → `"budget"` | module absent |
| `::test_build_limits_precedence` (param) | (1800, {}) → (600, 86400); (90000, {}) → ceiling 90000; STEP=2 → ceiling 2; STALL=60 → 60; explicit 5 beats env | module absent |
| `::test_build_limits_rejects` (param `0`,`-1`,`nan`,`inf`,`x`, for both vars) | ValueError | module absent |
| `core/test_bounded_runner.py::test_sleeping_step_is_stalled` (fast + controlled path, `slow`) | `sleep 30`, `stall_s=1`, `sample_s=0.2` → `timed_out`, `stop_reason=="stalled"`, no member left, < 5 s | no kwarg; runs until timeout |
| `core/test_build_timeout.py::test_busy_build_outlives_its_manifest_value` (`slow`) | component `build_timeout=1`, step = 3 s CPU loop, `STALL_S=1`, `SAMPLE_S=0.2` (monkeypatched) → DONE | killed at 1 s today |
| `::test_malformed_env_fails_typed` (param `0`,`inf`,`x`) | `Lifecycle.build` FAILED, no step ran | env ignored on CLI |
| `::test_host_test_has_no_stall_rule` | `host_test` → run_job without `stall_s`, timeout 900 | — (guard against scope creep) |
| `core/test_jobs.py::test_stop_reason_markers` (param) | log tail has `stalled: no CPU` resp. `runaway guard` | one generic marker |
| `core/test_build_launcher_runtime.py::test_web_build_outlives_spec_step_timeout` (`slow`) | spec op build, `step_timeout=1`, `sample_s=0.2`, env STALL=1, 3 s CPU step → done | spec 1 kills it (F40 A) |
| `::test_web_build_stalled_detail` | `sleep 30`, as above → detail starts `timed out (stalled` | detail "timed out after" |
| `::test_web_build_rejects_bad_stall` (param `0`,`nan`,`inf`,`x`) | SystemExit(3), no step ran | env ignored |
| `install/test_auto_install.py::test_auto_install_build_uses_build_limits` | harness of `:55-75`, but the real `Lifecycle.build` with `lifecycle_mod.run_job` captured → meshcore-cli gets `timeout=86400`, `stall_s=600` | no `stall_s`; timeout 900 |

**Updated:** `test_build_timeout.py:62-77` (1800/21600) and `:80-87` expect the 24 h ceiling; the env-forced
timeout tests keep rc 124 and `timed_out`, only the detail wording changes. **Before each push:** `python -m pytest tests/core tests/web tests/install tests/stacks tests/repo -n 8`.

## 8. Docs and CHANGELOG
**`docs/maintenance.md:268-271`**, replacing "The per-step build timeout defaults to 900 s …":
> A build step is not ended for being slow. LHPC ends it when its processes show no CPU time, no output and no
> disk/network I/O for 10 minutes (`LHPC_BUILD_STALL_S`, a policy value: a wait that would have recovered later
> is ended too). It also ends a step after 24 h. That limit is a runaway guard for a step that loops forever,
> not a performance limit: no supported build comes near it. `LHPC_BUILD_STEP_TIMEOUT_S` replaces that guard
> when set.

The "judge by CPU" hint stays, since it is the operator's view of the same rule.

**CHANGELOG:** "A build on a slow Pi is no longer killed after 15 or 30 minutes while it is still compiling: a
step is stopped only after 10 minutes with no CPU, output or disk/network activity (*stalled*), or by a 24-hour
runaway guard, and says which." · "The MeshCore CLI is built from a pinned set of packages, so a new upstream
release can no longer force a surprise compile."

## 9. Live proof (Pi 5)
1. **A slow compile survives.** `systemctl --user edit lhpc-web`: `Environment=LHPC_BUILD_STALL_S=60` and
   `Environment=PIP_NO_BINARY=dbus-fast,pycryptodome`; restart. Web **Build** MeshCore, then `lhpc build
   meshcore-cli`. Expected: pip silent for minutes while `top` shows `cc1`; green, marker present.
2. **A stuck session is ended.** Same settings; during meshcore-cli's pip step:
   `sid=$(ps -o sid= -p <pip pid>); pkill -STOP -s $sid` stops **every** member, `cc1` included;
   `ps -s $sid -o pid,stat,time` shows all `T` with frozen `TIME`. After ~60–75 s: web detail
   `timed out (stalled …)` / CLI log `[TIMED OUT — stalled …]`, and `pgrep -s $sid` is empty (SIGKILL ends
   stopped processes; `terminate_session` escalates to it, `proctree.py:220-225`).
3. **Normal path unchanged:** remove the overrides, restart, Build again → green.
4. **Field box (not verifiable here):** `uname -m`, `/etc/pip.conf`, the real step duration.

## 10. Risks
- **Too eager:** a wait that would recover after > 10 min with no CPU and no I/O (a network outage) is ended —
  the stated policy. pip's retry chain yields CPU and "Retrying" lines (progress). `git clone` is not a build
  step (`_CLONE_TIMEOUT_S`, `install.py:1962`).
- **Too lax:** a busy or periodically waking hang ends only at the 24 h guard. Accepted.
- **Invisible members:** a `setsid()` descendant leaves the session (`proctree.py:52-57`); that can only make a
  step look *more* idle. No current step does it.
- **Swap thrash on 512 MB:** CPU and `rchar` still accrue; 10 min with neither is effectively hung.

## 11. Commits
1. `F42: meshcore-cli builds against a constraints file` (§6).
2. `F42: progress module — activity sampler, stall/guard watch, build_limits` (`progress.py`, `test_progress.py`).
3. `F42: CLI and auto-install builds end on a stall, not on the clock` (backends, jobs, lifecycle, manifest
   deletions, their tests).
4. `F42: the web launcher uses the same rule` (launcher, launcher tests, docs, CHANGELOG).

## 12. Open questions
- **Q1** delete the inert `build_timeout` values? **Yes** (§5), the ceiling is shared. **Q2** stall rule for host
  tests? **No.** **Q4** `pip -v`? **No.** **Q5** piwheels? **No.**
- **Q3** 600 s? A **policy** default, not a proof that the hang is permanent; comfortably longer than pip's
  retry chain; env override for the field.
