# F39 / F40 — MeshCore build timeouts (read-only analysis)

Base: `e5187f70` (v0.11.10). No code changed. The box's logs were not available. Every claim cites file:line at that commit.

## TL;DR
- There are **two build runners with two different timeout rules**, and the two messages each come from **exactly one** of them:
  - `[TIMED OUT after 900s — job was KILLED; result is INCOMPLETE]` is written only by `run_job` (`lhpc/core/jobs.py:153`). A build reaches `run_job` only through `Lifecycle.build()` (`lifecycle.py:396-398`). The callers are CLI `lhpc build`/`lhpc install`, auto-install, HMAC apply, and the **web Install** button. Web Install starts `python -m lhpc install …` (`service_lifecycle_ops.py:3456-3466`).
  - `step timed out after 1800.0s: <argv>` is written only by the detached launcher (`build_launcher_runtime.py:152`). Only the **web Build/Test** buttons use it (`spawn_web_job` → `render_build_launcher`, `service_lifecycle_ops.py:3494`; the only caller is `adapters/web/app.py:1666`).
  - So the channel labels in the findings look **swapped**. F40's text cannot come from the CLI. It comes from a web Build, or from reading that job's log with the CLI. F39's 900 s marker comes from the CLI or a web *Install*, not the web *Build* button.
- **Both findings most likely point at `meshcore-cli` step 1 (`.venv/bin/pip install .`).**
  - That exact argv belongs only to meshcore-cli (`manifest.example.toml:2730`). meshcore-node's step prints `.venv/bin/pip install . pytest pytest-asyncio` (`:2240`).
  - meshcore-cli is also the only MeshCore component **without** a `build_timeout`, so it gets the 900 s default (`lifecycle.py:331, 369`). meshcore-node and meshcore-webui declare 1800 (`:2226`, `:2626`).
  - Caveat: meshcore-node's host test is also capped at 900 s (`test_timeout = 900.0`, `:2227`; `lifecycle.py:1491`) and goes through `run_job`. If F39's log was named `…test-meshcore-node…`, F39 is the test suite, not the build.
- **Latent bug found along the way:** the web launcher **ignores the manifest's `build_timeout` and `test_timeout`**. Every step gets `LHPC_BUILD_STEP_TIMEOUT_S`, default 1800 (`build_launcher_runtime.py:34-44, 174, 300`), and no deploy unit sets it.
  - meshtastic (`build_timeout = 21600`, `:1220`) and meshcom-qemu (`28800`, `:1943`) are therefore capped at **1800 s per step** when built from the web Build button.
  - The CLI path honours the manifest values (`tests/core/test_build_timeout.py:62-68, 165-172`).

## 1. Every timeout on the build path
| Path | Value | Scope | Source | Reported as |
|---|---|---|---|---|
| `Lifecycle.build` (CLI build/install, auto-install, HMAC, web Install) | `comp.build_timeout` or `BUILD_TIMEOUT_S = 900.0` | **per step** (each step is one `run_job`) | `lifecycle.py:331, 369, 385-398` | log + tail marker `jobs.py:149-160`; state `TIMEOUT` `jobs.py:170-171`; line `[timeout] build <id>` in `service_lifecycle_ops.py:3219-3222` |
| `Lifecycle.host_test` | `comp.test_timeout` or `TEST_TIMEOUT_S` | per test argv | `lifecycle.py:1487-1502` | same `run_job` marker |
| web Build/Test launcher | `LHPC_BUILD_STEP_TIMEOUT_S` (env), default **1800** | **per step**, same value for every component and for tests | `build_launcher_runtime.py:34-44, 174, 300` | stderr `step timed out after {t}s: argv` `:152`; attempt record detail **`step failed: argv`** `:309` — the record does not say "timed out" |
| manifest | `build_timeout`, `test_timeout` (0 = default) | per component | `model.py:466, 469`; `manifest.py:857-858` | — |
| `jobs.DEFAULT_TIMEOUT_S` | 600 | unused by build (Lifecycle always passes one) | `jobs.py:29, 65` | — |
| git clone in install | `_CLONE_TIMEOUT_S = 900.0` | per clone | `install.py:1962, 2022-2045` | `[fail] clone: timed out after 900s` `install.py:2001` — a different text, so not F39 |
| web-job gate | `LHPC_WEBJOB_GATE_TIMEOUT_S` = 8 s | handshake only | `webjob_gate.py:22-32` | — |

The web job has **no overall per-job cap**. The 900 s is the per-step default (`lifecycle.py:331`), not a job limit.

## 2. MeshCore build steps and dependencies
**meshcore-node** (`manifest.example.toml:2236-2248`, `build_timeout 1800`, `build_marker .venv/.lhpc-build-complete`):
1. `python3 -m venv --system-site-packages .venv`
2. `pip install . pytest pytest-asyncio` — openhop_core @ `cedb26b` (`:2467-2470`)
3. `pip install --no-deps {asset}/meshcore_host`
4. repeater deps, constrained (`openhop-repeater-constraints.txt`)
5. `pip install --no-deps --no-build-isolation src/openhop-repeater`

The upstream `pyproject.toml` at `cedb26b` (fetched from GitHub) declares `pycryptodome>=3.23`, `PyNaCl>=1.5`, `pyyaml>=6` and builds with setuptools. The manifest comment says "pycryptodomex" (`:2237`), but upstream uses `pycryptodome`; this is cosmetic.

**meshcore-webui** (`:2608-2623`, 1800 s): `pip install -c meshcore-webui-constraints.txt ./backend`.

**meshcore-cli** (`:2728-2736`, **no `build_timeout`, no `build_marker`**):
1. `python3 -m venv .venv` (no `--system-site-packages`)
2. `pip install .` — meshcore-cli v1.6.4 @ `d4eac61` (`:2788-2790`)
3. `python -m compileall -q src`

The upstream `pyproject` uses hatchling and declares `meshcore>=2.3.11`, `bleak>=0.22`, `prompt_toolkit>=3.0.50`, `requests>=2.28`. **None of these is pinned and there is no constraints file.** The transitive dependencies:
- meshcore 2.3.14 needs `bleak`, `pycayennelpp`, `pycryptodome`, `pyserial-asyncio-fast`.
- bleak 3.0.2 needs `dbus-fast` on Linux.
- requests needs `charset_normalizer`, `idna`, `urllib3`, `certifi`.

**ARM wheel status** (PyPI JSON queried 2026-10-03, latest versions):
- Native packages: `dbus-fast` 5.2.0 (Cython), `pycryptodome` 3.23.0, `pycryptodomex`, `PyNaCl` 1.6.2 (bundles libsodium) and `charset_normalizer`. All have **aarch64** manylinux wheels (dbus-fast cp39–cp315; pycryptodome abi3).
- **None except charset_normalizer has an armv7l wheel on PyPI.**
- On **64-bit Raspberry Pi OS** (the documented target, `README.md:94`), nothing in either closure should compile from source.
- On a **32-bit (armv7l) OS**, `dbus-fast`, `pycryptodome(x)` and `PyNaCl` compile from source unless piwheels supplies wheels. `dbus-fast` (Cython) and `PyNaCl` (libsodium configure+make) take tens of minutes each on a Zero 2 W, which would explain both caps.
- The rest is pure Python.

**piwheels / `--prefer-binary` / wheel cache:**
- The repo configures no `--prefer-binary`, `--only-binary`, `--index-url`/`--extra-index-url` or `pip.conf` anywhere: no hits in `lhpc/`, `install.sh`, `bootstrap-deps.sh`, `deploy/`, `tools/`. `manifest.example.toml:1169` deliberately rejects `--only-binary` for meshtastic.
- The only pip setting is `PIP_CACHE_DIR=<root>/build/tool-cache/pip` for the web/selfupdate/boot units (`deploy/lhpc-web.service:43`, `updater_units.py:124,185,379`). It is forwarded by the runner env allow-list (`probes/backends.py:193-198`), which drops every other `PIP_*` variable.
- A CLI build from a login shell uses pip's default `~/.cache/pip`.
- Any `/etc/pip.conf` that Raspberry Pi OS ships is read by pip regardless; whether the box has one could not be established.

## 3. State left behind, and whether a retry is clean
**Lifecycle path (F39):**
- The marker is removed **before** step 1 (`lifecycle.py:374-380`). A timeout returns at `:399-400`, so the marker is never written and meshcore-node/webui read NOT built (`service_lifecycle_ops.py:4763-4787`).
- **meshcore-cli has no marker.** `is_built` falls back to `bin = .venv/bin/meshcli` existing (`:4788-4795`, manifest `:2738`). A timeout in step 3 (compileall), after pip finished, reads "built". A timeout during pip is very unlikely to leave `meshcli` (the console script is written last).
- The `run_job` result is `TIMEOUT`. It is `unsafe` only if cessation was unproven (`jobs.py:181-195`; `backends.py:423-424`). Auto-install records `fail` and moves on (`service_auto_install.py:1388-1396`); unsafe goes to `mark_unsafe` (`:1388-1389`).

**Web launcher (F40):**
- Proven termination → rc 124 → attempt `failed` (non-blocking), detail `step failed: …` (`build_launcher_runtime.py:300-310, 340`).
- Unproven termination → `unsafe`, which blocks the same source until Recover (`:302-307`; `jobresult.recover`, `jobresult.py:266`; `_web_unsafe_source_block`, `service_lifecycle_ops.py:3336`).
- Source locks are released in `finally` (`:327-338`). The marker was already invalidated (`:266-282`).

**CR1-6 (`ccb0059`)** settles a reserved attempt when `write_launcher` fails (`service_lifecycle_ops.py:3504-3508`). It does not touch timeouts. The timeout path was already settled by `_record()` in `finally` (`build_launcher_runtime.py:340`), so a timed-out web job does **not** leave an attempt stuck in "starting".

**Retry:**
- Clean by the marker contract, but **not from scratch**. The partial `.venv` is kept, and `python3 -m venv` over it runs without `--clear` (`manifest :2239, :2729`).
- pip skips already-satisfied packages and reuses `PIP_CACHE_DIR`, so a retry is cheaper and can succeed under the same cap.
- A package killed mid-unpack is normally reinstalled because its `dist-info` is missing. I could not prove this for every pip version.

## 4. Earlier duration evidence in the repo
- `docs/stacks/meshcore.md:162`: `lhpc build meshcore --yes  # ~8 min on a Zero 2 W` (whole stack).
- Manifest `:2224`: the MeshCore venv + pip build "overran the 600 s default on a Pi Zero 2W".
- `docs/live-tests/live-test.md:65-66`: full auto-install 14 min 12 s (e293) / 5 min 01 s (Pi 5); meshcore host tests 154 s / 82 s.
- testlab allows `install_build(... "meshcore", timeout=2400)` (`testlab/tests/release/test_release_verify.py:238`).
- There is no per-step measurement for meshcore-cli. Against ~8 min for the whole stack, a single `pip install .` exceeding 900 s or 1800 s points to something environmental: an armv7 source build, a slow or failing index (e.g. piwheels from `/etc/pip.conf`), or three parallel pip jobs on 512 MB (web Build spawns every component at once, `service_lifecycle_ops.py:3538-3575`, all niced plus ionice idle, `backends.py:281-297`). This is not a code regression.

## 5. Fix options
| Option | Size | Risk | Effect |
|---|---|---|---|
| **A. Launcher honours the manifest:** `render_build_launcher` carries `step_timeout` = `comp.build_timeout or Lifecycle.BUILD_TIMEOUT_S` (op build) / `test_timeout or TEST_TIMEOUT_S` (op test); `_step_timeout()` uses the spec, env stays an explicit override | ~20 lines + tests | low | one rule for CLI and web; fixes meshtastic/meshcom web cap |
| **B. `build_timeout = 1800.0` (+ `build_marker`) for meshcore-cli** | 2–3 manifest lines | very low | removes F39's 900 s; marker closes the step-3 "built" gap |
| C. Launcher detail says `timed out after Ns` instead of `step failed` | 3 lines | very low | attempt banner tells the truth |
| D. Longer default for every source build | 1 line | low, but hides real hangs for 30+ min on every component | blunt |
| E. Progress-aware (idle) timeout | medium (run_streaming + launcher) | medium | **counter-productive for pip**: pip is silent off-TTY (manifest `:2240, :2730`), so an idle timer fires sooner |
| F. `--prefer-binary` + piwheels for build venvs | small per step | medium: changes resolution and adds a third-party index | no effect on aarch64 (wheels already preferred for the same version); helps only armv7 |
| G. Constraints file for meshcore-cli (like webui/repeater) | small + maintenance | low | reproducible, no surprise sdist-only release |
| H. MeshCore via the binary channel | large (arch- and Python-ABI-specific venv artifacts) | high | removes on-box pip entirely |

**Recommendation: A + B + C together.** This is the simplest robust set: one timeout rule wherever a build runs, a declared budget for the one under-budgeted MeshCore component, and an honest record. Consider G as a follow-up. Do not do E. Do F only if the box turns out to be 32-bit.

**Regression tests:**
1. `tests/core/test_build_launcher_runtime.py`: the spec `step_timeout` is used, the env override wins, and a malformed or ≤0 value still fails safe (exit 3).
2. `tests/web/test_webjob.py`: `spawn_web_job("build", "meshcom")` renders a spec whose `step_timeout == comp.build_timeout` (28800); `op="test"` → `test_timeout`.
3. `tests/core/test_build_timeout.py`: meshcore-cli has `build_timeout >= 1800` and a `build_marker`. Optionally, every component whose `build_steps` run `pip install` declares `build_timeout >= 1800`.
4. The launcher timeout's attempt `detail` contains `timed out after`.

## Not established (needs the box)
- CPU architecture and userland (aarch64 vs armv7l), Python version, `/etc/pip.conf` (piwheels), network/index latency.
- The exact log file names: `build-meshcore-cli*.log` vs `…test-meshcore-node…` decides F39's component; whether F40's quoted argv was truncated.
- Which button or command was actually used for each finding (the labels contradict the code, see TL;DR), whether three parallel web jobs ran, and how long each step really took.
