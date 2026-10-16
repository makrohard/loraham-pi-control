# Gate 1: testlab-det-5 (P5: transfer separation in slow-build), v3

**Round.** v3, answering round 13 (RED, 2 findings) under the maintainer's ruling: F1, every timed build's pip is now pre-acquired and installed offline; the graywolf build's .deb stays under test inside its build timing, stated in docs and plan. F2, plan tests (a) and (c) are added with a twin. Judge first whether F1/F2 are closed as ruled (report, section v3), then the whole commit.

**Request.** Judge the one code commit below against plan v4 §2 P5 and the maintainer's decision (a): every transfer a timed slow-build operation makes is acquired before the lane, timed and recorded on its own, and never judged; the budget judges local work only. Judge above all: can a transfer still hide inside a judged timing (L7a clone, L6 deb fetch, L5 CLI venv, L4 self-update pip sync); is the product's own code path unchanged apart from the one `GRAYWOLF_BASE_URL` line; whether plan tests (a) and (c), argued as structural in the report, really need no test; whether each red-before fails through the behaviour; and the report against the diff. The packet is stacked on P4a (and P4a on P3), both at gate 1; its diff base is P4a's head.

This file is your whole input: you have no repository access; use no connector, tool or web lookup.

**Answer form.**

| commit | verdict (OK / FINDING) | what |
|---|---|---|

Then numbered findings (file:line, why it matters, smallest fix), then one final line: GREEN / GREEN WITH NOTES / RED.

Redaction: none needed; the diff is `git diff` output verbatim.

## Commits (full messages, `git log --reverse --format=%B`)

### d3e252b3 testlab: slow-build times its transfers apart and judges local work

```text
testlab: slow-build times its transfers apart and judges local work

Decision (a): a timed operation's transfer is measured as its own number and never fails a
budget. The slow-build job now acquires, before the lane and with the transfer backoff, what
the transferring timed operations fetch:

- the git sources of the timed clones (L7a): the mirrors of mirror-sources.py;
- the pinned Graywolf .deb and its checksums.txt (L6), at the version the manifest's
  graywolf-fetch.sh step carries (`mirror-sources.py graywolf-version`);
- the wheels of the Meshtastic CLI venv (L5, `mirror-sources.py cli-requirements`) and of
  lhpc's own requirements for the self-update's pip sync (L4), via `pip download` in the lab
  image;
- the wheels of every timed build's `pip install` (L1, `.github/scripts/build-wheels.py`): each
  step runs as `pip wheel` from the component's pinned source checked out from its mirror,
  plus the build requirements of every local project it names. Every component but the
  baseline's `excluded` ones: 13 steps of 7 builds today.

Each transfer's wall time goes to slowevidence/transfers.toml: recorded, never judged. The lane
then times the operations against the local copies, each by a channel that reaches the child
lhpc's command runner starts (RealCommandRunner gives its children a fixed environment that
keeps HOME and XDG_CONFIG_HOME and drops GIT_CONFIG_GLOBAL and PIP_*):

- clones: the mirror config as the lab user's ~/.gitconfig, checked with `mirror-sources.py
  resolved` through that runner before the tests;
- the fetch: GRAYWOLF_BASE_URL=file:///xfer/graywolf (graywolf-fetch.sh runs by direct
  subprocess);
- pip, for the meshtastic install, every timed build and the self-update helper: `_local_pip`
  writes a pip.conf (no-index, find-links = the wheels) and points XDG_CONFIG_HOME at it; `_offline`
  reads pip's config through the runner in a fresh interpreter and fails before anything is
  timed unless pip sees both. With no index, a missing wheel fails the operation; it cannot
  fetch.

The budget (test_slow_build_budget) judges only the lane's own timings; it never reads
transfers.toml. One transfer stays under test inside a timed build, by the maintainer's ruling
(decision a, "the cheap way"): the graywolf build's .deb, fetched by its only build step through
the runner, which drops GRAYWOLF_BASE_URL. No timed build runs npm.

The one product line: graywolf-fetch.sh reads its download base from GRAYWOLF_BASE_URL,
default unchanged (https://github.com/chrissnell/graywolf/releases/download); the sha256
check is unchanged, so a local copy must match the pinned checksum.

Also: the job's `docker build` gets the backoff, and the lane's own pip installs use `backoff`
instead of `retry 3`. The evidence upload, a transfer after the verdict, is tried twice two
minutes apart, both attempts `continue-on-error`; failing twice gives a warning and a summary
line, never a red job (run 38822953533 measured all green and went red on `CreateArtifact:
… ETIMEDOUT` alone). A workflow change: proved by a lane run, not by a test.

Tests, red-before on the parent commit (8 failed):
- tests/stacks/test_graywolf_fetch.py: the script runs for real with curl/dpkg shims; the
  download URL is GitHub by default (guard, green on the parent) and the override when set
  (red: the parent ignores GRAYWOLF_BASE_URL and asks GitHub);
- tests/repo/test_mirror_sources.py: the two subcommands read the manifest's versions, and a
  manifest without those steps is an error naming the step (both red: no such subcommands);
- testlab/tests/unit/test_slow_build_lane.py: through the real runner, a pip sees the
  pip.conf's no-index and wheels, and `_offline` refuses the PIP_* variables the first version
  exported (both red: no `_offline`); without wheels the env is unchanged (red: no
  `_local_pip`); the budget's verdict and summary are identical with the acquisition 10^6
  times slower, while a slow LOCAL timing does fail it (plan tests a and c; a new invariant
  with its twin, green on the parent too: the budget never read transfers.toml);
- tests/repo/test_build_wheels.py: every pip step of a timed build is covered and sideband's
  (excluded) is not (red: no script); a step becomes `pip wheel` from its pinned checkout, with
  --force-reinstall dropped and the build requirements of both local projects fetched.

```

## The report

> # Code report: testlab-det-5 v3 (packet P5: transfer separation in slow-build)
>
> Branch `cons/testlab-det-5-r1`, stacked on P4a (`45348dbc`, its packet commit, rebased unchanged), which is stacked on P3 v4 (`225ef6a4`) on `origin/dev` = `d77e34f4`. One code commit, d3e252b3. Plan v4 §2 P5 and decision (a): a timed operation's transfer is its own number and never fails a budget.
>
> ## v3: round 13 (RED, 2 findings), the maintainer's ruling, and what changed
>
> **F1: build-internal transfers stayed inside judged build timings.** The ruling under decision (a), "separate, the CHEAP way", is recorded in the plan note on `cons/testlab-det-plan` @ 5e982825:
> - **pip inside a timed build: separated now.**
>   - New `.github/scripts/build-wheels.py`, run in the acquisition step inside the lab image. It takes every `<venv>/pip install` build step of every component but the baseline's `excluded` ones: 13 steps of 7 builds (meshcore-node, meshcore-webui, meshcore-cli, rns, nomadnet, lxmd, meshchat).
>   - Each step runs as `pip wheel -w /xfer/wheels` from the component's source, checked out at its `pin_commit` from its mirror, with `{runtime}`, `{asset}` and `{source}` resolved. `--force-reinstall` and `--no-build-isolation` are dropped; they have no wheel meaning.
>   - The `[build-system] requires` of every local project the step names are fetched too, with setuptools' default when none is declared.
>   - Each component's wall time is appended to `transfers.toml`.
>   - In the lane, every timed build runs with the same `_offline(_local_pip(...))` env as the CLI venv: pip.conf through `XDG_CONFIG_HOME`, no index, checked through the runner before timing.
> - **Still under test inside a timed build, by the ruling: the graywolf build's `.deb`.** Its only build step, `graywolf-fetch.sh`, runs through the runner, which drops `GRAYWOLF_BASE_URL`, so it fetches from GitHub.
>   - No timed build runs npm (the inventory of every lane build step is in the plan note).
>   - The build logs carry no per-step timestamps, so no transfer share can be recorded apart. For graywolf the build IS that one step.
>   - Stated in docs/testlab.md and in the plan.
> - Both acquisition containers now run as root (`--user root`, `PIP_ROOT_USER_ACTION=ignore`), because the image's user `lhpclab` cannot be assumed to write the runner-owned `xfer/`.
>
> **F2: plan tests (a) and (c) are added** (testlab/tests/unit/test_slow_build_lane.py):
> - `test_a_transfer_however_slow_leaves_the_budget_verdict_unchanged` runs the real `test_slow_build_budget` on one timed clone with a `transfers.toml` beside it. With that clone's transfer at 1 s and at 10^6 s, verdict and summary are identical (pass).
> - The twin, `test_a_slow_local_timing_does_fail_the_budget`: the same instrument with the LOCAL clone at 400 s against a 600 s limit fails with `**FAIL** rns clone: limit 600 s < 2 x 400 s measured`.
> - Both are green on the parent too: the budget never read `transfers.toml`. They are a new invariant with a negative twin, not a red-before.
>
> ## v2: what changed since v1 (8e81808e), and why
>
> **A real defect, found by the second reader.** v1 exported `GIT_CONFIG_GLOBAL`, `PIP_FIND_LINKS` and `PIP_NO_INDEX` for the timed operations. But the clones (L7a), the CLI venv's pip (L5) and the self-update's pip sync (L4) run as children of lhpc's `RealCommandRunner`. That runner gives its children `{**_FIXED_ENV, **env}`: PATH/LANG/LC_ALL plus a whitelist that keeps `HOME` and `XDG_CONFIG_HOME` and drops the rest (lhpc/core/probes/backends.py:188–203; :262, :374, :430). So v1's L7a/L5/L4 timings would still have included the network. Only L6 worked, because `graywolf-fetch.sh` runs by direct `subprocess.run`, which inherits `GRAYWOLF_BASE_URL`. v2 uses channels the runner forwards, and checks each one THROUGH the runner before anything is timed:
>
> - **git:** the mirror config as the lab user's `~/.gitconfig`. `mirror-sources.py resolved` (P3 v4) runs through the runner in the lane and fails unless every remote resolves to its mirror. The stack cases use `lab_env`, which keeps the lab user's `HOME`. The self-update case uses its own `HOME` but only fetches from a local remote (a repo-local `insteadOf`, unchanged).
> - **pip:** `_local_pip(env, conf)` writes `<conf>/pip/pip.conf` (`no-index = true`, `find-links = <wheels>`) and sets `XDG_CONFIG_HOME=<conf>`, only for the meshtastic install and the self-update helper. `_offline(env)` runs `pip config list` through `RealCommandRunner` in a fresh interpreter (so the runner's fixed env is built from `env`, as in the product process) and fails before the timing unless pip shows both settings. With no index, a missing wheel fails the operation; it cannot fetch.
> - **Graywolf fetch:** unchanged (`GRAYWOLF_BASE_URL`, direct subprocess).
>
> Test through the real runner: `test_only_a_pip_conf_reaches_a_pip_the_products_runner_starts`.
> - `pip_conf`: pip, as the runner's child, shows `global.no-index='true'` and the wheels.
> - `pip_env` (v1's mechanism): `_offline` refuses it ("would reach the index"), since the runner dropped the variables.
>
> **The evidence upload (the handler's order after the dev batch run 38822953533).** That run measured everything green (`11 passed in 2561.59s`), then went red on the upload step alone: `Failed to CreateArtifact: Unable to make request: ETIMEDOUT`. The upload is a transfer after the verdict (class A).
> - It is now tried twice, 120 s apart. Both attempts are `continue-on-error: true`, and the second has `overwrite: true` in case the first left a partial artifact.
> - A double failure prints `::warning::` and a summary line. It never turns the job red: the job's verdict is the lane step's (the JUnit gate) and nothing else.
> - Cost: such a run has no evidence artifact, and docs/testlab.md says to re-run the job for one.
> - A workflow change, proved by a lane run, not a test.
>
> ## Commit
>
> | commit | files | tests | red-before |
> |---|---|---|---|
> | d3e252b3 | .github/workflows/testlab.yml (slow-build acquisition: mirrors, Graywolf, CLI wheels, build wheels, all as transfers; the lane's `~/.gitconfig` + `resolved` + `GRAYWOLF_BASE_URL`; `backoff`; the twice-tried evidence upload), .github/scripts/build-wheels.py (new), .github/scripts/mirror-sources.py (`graywolf-version`, `cli-requirements`), lhpc/data/scripts/graywolf-fetch.sh (`GRAYWOLF_BASE_URL`, the one product line), testlab/tests/slowbuild/test_slow_build.py (`WHEELS`, `_local_pip`, `_offline`, builds with the offline env), docs/testlab.md | tests/stacks/test_graywolf_fetch.py (new, 2), tests/repo/test_build_wheels.py (new, 2), tests/repo/test_mirror_sources.py (+2), testlab/tests/unit/test_slow_build_lane.py (+5) | the four files on a worktree of the parent `45348dbc`: 8 failed, 68 passed, 2 skipped; every red is a new case. Guards green there: the default-URL case and the two budget tests (a/c) |
>
> No CHANGELOG line: test tooling; the product line changes nothing for an operator.
>
> ## HOUSE RULES KEPT (dev's tools/check_houserules.py on HEAD~1..HEAD, the P5 commit only)
>
> ```
> HOUSE RULES KEPT  HEAD~1..HEAD
> changelog    +0 bullets (limit 2, each ≤ 3 lines; longest 0), top section 25 lines   OK
> docs         1 files, +16 lines; fact "recorded, never judged" -> docs/testlab.md; fact "build-wheels.py" -> docs/testlab.md   OK
> tests        9 new tests; layers testlab/tests/slowbuild=testlab testlab/tests/unit=testlab tests/repo=repo tests/stacks=stacks; red-before 1/1   OK
> patterns     getsource 0 assert-True 0 source-read 0 sleep 0 startswith 1 network 0   WARN
> markers      3 without slow   WARN
> attribution  authors makrohard; trailers 0; words 0   OK
> modules      no family file touched; family net +0 lines, +0 public   OK
> VERDICT      PASS  (hard 0, warn 2)
> ```
>
> The WARNs, answered:
> - **`startswith 1`** is a FILTER inside an assert (`not any(s.startswith("sideband ") for s in want)`), not a prefix assertion on a sentence.
> - **`markers 3 without slow`** is the checker's text heuristic ("venv" / "pip install" in a body).
>   - Two hits are `test_slow_build_stack` and `test_slow_build_selfupdate`, which are `slow` through the module's `pytestmark = pytest.mark.slow` (test_slow_build.py:44).
>   - The third is `test_every_pip_step_of_a_timed_build_is_covered…`, which only reads the manifest (`.venv/bin/pip` strings) and runs in milliseconds.
>
> ## What it does
>
> - **Acquisition** (a new step before the lane), each transfer with `backoff` and timed into `slowevidence/transfers.toml`, which the uploaded artifact carries (`path: slowevidence/**`):
>   - the mirrors (P3's script and derived list; every clone the lane times is of a mirrored remote);
>   - the Graywolf `.deb` + `checksums.txt` at the manifest's pinned version;
>   - `pip download` of the CLI venv's requirements, `setuptools>=68` (the build backend) and this checkout, inside the lab image.
> - **The budget** (`test_slow_build_budget`) iterates `LANE_OPS` over `EVIDENCE`, which only `_record` fills from the lane's own timings; tests (a)/(c) above pin that a transfer, however slow, leaves its verdict unchanged.
> - **Not separated (the ruling):** the graywolf build's `.deb` (v3 section above).
> - **Comparability.** Row A (the Zero) times its clones, fetch and pip with the network; row C now times them locally. The budget gets easier for those four operations, never harder. This is decision (a).
>
> ## The 6-point block
>
> 1. **Contracts.**
>    - `graywolf-fetch.sh`: `BASE_URL="${GRAYWOLF_BASE_URL:-https://github.com/chrissnell/graywolf/releases/download}"`; the sha256 check is untouched.
>    - `mirror-sources.py graywolf-version|cli-requirements <manifest>`: exit 1 naming the missing step.
>    - `_local_pip(env, conf)`: env unchanged without wheels, else `XDG_CONFIG_HOME=<conf>` with the pip.conf.
>    - `_offline(env)`: returns env, or fails with pip's config as the runner's child sees it.
> 2. **Invariants.**
>    - The budget judges only lane timings.
>    - Every transfer is retried long (c1), never a test.
>    - The product's code path is unchanged apart from the URL line; only its sources are local, and the lane proves through the product's runner that git and pip see them.
> 3. **Failure classes.**
>    - An acquisition failing after the backoff: red before the lane.
>    - A config not reaching git: `resolved` fails before the tests.
>    - A pip.conf not reaching pip: `_offline` fails before the timing.
>    - A wheel missing from the directory: pip's "No matching distribution", a named red lane; the gap is in the acquisition.
>    - A Graywolf copy with a wrong checksum: the product's own sha256 check.
> 4. **Test rules.** No source reading, no sleep, no network: curl is a shim, and pip only reads its config. The runner checks run in a fresh interpreter, because `_FIXED_ENV` is built at import.
> 5. **Directories.**
>    - `pytest -q -n 8 tests/repo tests/stacks/test_graywolf_fetch.py testlab/tests/unit/test_slow_build_lane.py` → `663 passed, 3 skipped`.
>    - `ruff check lhpc testlab` and `ruff check tests --select F,E9` → `All checks passed!`.
> 6. **Adversarial self-review.**
>    - (a) `XDG_CONFIG_HOME` also moves where other programs in those two operations look for config. lhpc's Python never reads it (`grep`: only backends.py forwards it), and the lab simulates systemctl, so no user unit is looked up there.
>    - (b) `_offline` checks with the pytest venv's pip, not the CLI venv's or the helper's. The mechanism (pip's XDG config file) is the same for every pip, and the runner forwards it to all.
>    - (c) v1's lane run 38826850523 was dispatched without `release_verify=true`, so slow-build never ran; it proved nothing for P5. v2's run is dispatched with it.
>
>    - (d) Lane evidence for v2: run 38828384068 on 172a8991. `testlab` (incl. `resolved` through the runner) and `release-verify` passed, which is the lane evidence for P3 v4 and P4a. Its slow-build was still in the acquisition step when v3 was built, and v3's own run supersedes it (a dispatch on the branch cancels the older run's remaining job).
>    - (e) `build-wheels.py fetch` is proved here only with a stubbed `pip wheel` (composition, pins, dropped options, build requirements); the real fetch and the offline builds are proved by the v3 lane run, not yet available.
>
> ## Numbers, measured
>
> | figure | command | output |
> |---|---|---|
> | diff | `git diff --shortstat HEAD~1 HEAD` | 10 files changed, 539 insertions(+), 10 deletions(-) |
> | red-before | the four test files on a worktree of `45348dbc` | `8 failed, 68 passed, 2 skipped` |
> | on this commit | same four files | `76 passed, 2 skipped` |
> | timed builds' pip steps | `build-wheels.py steps <manifest> <baseline> \| wc -l` | 13 (7 components) |
> | runner whitelist | `sed -n 188,203p lhpc/core/probes/backends.py` | HOME, XDG_RUNTIME_DIR, XDG_CONFIG_HOME, … (no GIT_*, no PIP_* but PIP_CACHE_DIR) |

## The plan (v4, for reference)

````markdown
# PLAN testlab-determinism (v4)

Base: `origin/dev` = `254bbae3` (P1 landed). Scope: `testlab/`, `.github/workflows/`, the Graywolf fetch script (P5), one product function (S1, landed). The bot's re-runs are NOT in this plan: they belong to the bot-stability item, on the contract `CONTRACT-external-unavailable.md`.

Rules: tests/README.md rules 1–13; no waiver, no lane exclusion, no weakened assertion; red-before for every fix ("new invariant" with a negative test otherwise); a flake never freezes a stack.

## 0. Changes

**v4** (the maintainer's DECISIONS a–c4, binding, and the W1b verdict, Item A, 6 findings, all accepted):

| finding | change |
|---|---|
| W1b-1 transfer-time separation missing (decision a) | new P5 (§2): every timed transfer is acquired before the lane and recorded as its own number; the budget is judged on the local work only; a test proves a slower transfer leaves the verdict unchanged |
| W1b-2 retry policy against c1 | §1: one backoff (at once, then 1, 2, 5, 10 min, then 10 min steps to a 30-min total) for every TRANSFER, in the acquisition steps and around the lane's unavoidable class-B transfers; never around an assertion or a test body. Bot re-runs: not this plan (B1 removed) |
| W1b-3 c4 missing from P2 | §2 P2 = M2 + c4 step 1 (retry removed, instrumentation); c4 step 2 (the cause fix with a reproducer) is a later packet, triggered by the first recorded hang |
| W1b-4 same-step attribution hole | M4: EXTERNAL only when the decisive step's TERMINAL error line is a transport line; a recovered transport line earlier in the same step does not count; twin test |
| W1b-5 S1 proof surface | P4 adds the test that the summary and the kept status carry `curl rc <n>` and curl's message as one line of ≤ 200 chars; red on the parent |
| W1b-6 CHANGELOG destination | `## Unreleased`, one or two lines per packet |

v3 and v2 change tables: see the frozen copies PLAN-testlab-determinism-v2.md / -v3.md.

## 1. The rule (one place: docs/testlab.md, "Running the verification lanes")

> Every external access by a testlab job is in exactly one class.
> - **(A) Acquisition, before the lane.** The container image, the lab's Python packages, Chromium, the upstream git sources (mirrors), and in slow-build every artifact a timed operation transfers. A workflow step of its own does it, with the transfer backoff, and names what failed.
> - **(B) Under test, inside the lane.** The published binaries, the pinned release downloads and the build steps' packages that a stack installs in testlab and release-verify. They run as the product runs them, including the product's own retries. Their transfer is wrapped in the transfer backoff by the lane helper that starts it; the lane never repeats an assertion or a test body. A transfer still failing after the backoff is reported as the first line `EXTERNAL-UNAVAILABLE what=<token>` (the contract), never `STACK-REGRESSION`.
> - **(C) Everything else is forbidden.**
> - **Attribution.** `STACK-REGRESSION` needs typed evidence of the component itself: a wait-based readiness check → its process is gone; an interactive program → it exited or drew a traceback/exception line; a build → its decisive step is one the recipe declares `attributable`. Anything else, an unrecognised failure included, is UNMARKED.
> - **Transfer backoff:** retry at once, then after 1, 2, 5 and 10 min, then every 10 min, until 30 min have passed; each wait and the reason logged.

## 2. Packets and items

**P1 (LANDED, 254bbae3):** M1 slow-build fetches the pinned Graywolf; S1 the update check keeps curl's reason.

**P2 (in review, cons/testlab-det-2-r1):**
- **M2:** 21 readiness asserts go through `readiness_failed`, marked only on `stopped`/`failed`.
- **c4 step 1:**
  - the 0.12.5 retry is removed;
  - a timeout runs `on_timeout()` before the abort;
  - NomadNet's case records the shared instance's sockets and every Reticulum process's stack (`reticulum_peers_snapshot`, `PYTHONFAULTHANDLER=1`).

**P3a (in review, cons/testlab-det-3a-r1):**
- CI's test job runs `-n auto --dist loadfile`;
- only `cons/*` cancels superseded runs;
- `slow-build needs: testlab`.

**P3 M3, the mirror:**
- an acquisition step in testlab and release-verify clones the 14 lane remotes as mirrors with the transfer backoff; `GIT_CONFIG_GLOBAL` → `insteadOf`;
- a `--network none` step proves the list complete;
- `docker build` gets the backoff; Xvfb waits for its socket.
- Red-before: the gitconfig text test (new script).
- Lanes: testlab, release-verify.

**P4: M4 + S2 + the transfer wrapper + the S1 test.**
- **M4, the decisive-step rule** (`external_unavailable(output)`, release.py):
  - install: the failing action's own `[failed]`/`ERR` line(s) only;
  - build: the step log named by `[failed] build <comp> (rc N, log <path>)` (build_regression.py:29):
    - an `attributable` step → STACK-REGRESSION, unchanged;
    - otherwise the step's TERMINAL error line is decisive: the last line matching `^(ERROR|error|fatal):|^curl: \(\d+\)|download failed`. EXTERNAL only if that line is a transport line (`download failed`, `Could not resolve host`, `Failed to connect`, `ConnectionError`, `ReadTimeoutError`, `HTTP error 5\d\d`, `curl: \((6|7|28|35|56)\)`); an earlier, recovered transport line in the same step does not count;
  - slow-build `_judged`: the timed command's terminal error line, by the same rule.
- **M4 tests:**
  - install `download failed` ⇒ EXTERNAL (red on the parent);
  - attributable step + transport text elsewhere ⇒ STACK-REGRESSION (guard);
  - a non-attributable pip step whose terminal line is `ReadTimeoutError` ⇒ EXTERNAL (red);
  - SAME-STEP TWIN: a `ConnectionError` retried earlier in the step, the terminal line `ResolutionImpossible` ⇒ UNMARKED (guard);
  - unknown ⇒ neither line.
- **Transfer wrapper:** the lane helpers that run a class-B transfer (`install_build`'s install, reset's binary install, slow-build's fetch steps) repeat ONLY an attempt that ended EXTERNAL, with the backoff. Any other failure is reported at once. Tests: a fake install failing EXTERNAL twice then passing ⇒ passes, both waits logged (red on the parent); a fake failing non-externally once ⇒ fails at once (guard).
- **S2:** reset fails on a failed binary download, with the EXTERNAL line.
- **S1 test:** `tests/stacks/test_graywolf_upstream.py` asserts that the summary and the kept `error` contain `curl rc 22` and curl's message, flattened to one line of ≤ 200 chars. Red on the parent (the summary says only "network/API error").
- Lanes: testlab, release-verify, slow-build.

**P5 (decision a), transfer separation in slow-build.**
- The timed operations that transfer (L7a clone, L6 deb fetch, L5 CLI venv, L4 self-update pip sync) get their artifacts in an acquisition step of the slow-build job:
  - git mirrors (P3's script);
  - the pinned Graywolf `.deb` + `checksums.txt`;
  - `pip download` wheelhouses for the CLI venv and lhpc's own requirements, inside the lab image.
- Each transfer's wall time is written to the evidence as `op = "transfer"` with the operation it serves; it is recorded, never judged.
- The lane then times the operation against the local copies:
  - clone through `insteadOf`;
  - the fetch with `GRAYWOLF_BASE_URL=file://…` (one line in graywolf-fetch.sh: `BASE_URL="${GRAYWOLF_BASE_URL:-https://github.com/…}"`; curl reads `file://`);
  - pip with `PIP_FIND_LINKS` + `PIP_NO_INDEX=1`, which the product's spawned steps inherit.
- The budget (`slow_target.compare`) judges only those local timings.
- Tests:
  - (a) a `transfer` entry is never judged, however large: new invariant, negative test;
  - (b) the fetch script uses `GRAYWOLF_BASE_URL` when set (red on the parent: it ignores it);
  - (c) slowing the acquisition (a stub transfer + 60 s) leaves the budget verdict unchanged.
- Lane: slow-build.

**P5 scope after round 13 (the maintainer's ruling under decision a, "the cheap way"; 2026-10-16):**
- Separated, as above, plus every timed build's `pip install`: its wheels are fetched before the lane (`.github/scripts/build-wheels.py`) and the build's pip reads a `pip.conf` with no index through `XDG_CONFIG_HOME`.
- The channels are ones lhpc's command runner forwards to its children: `~/.gitconfig` (via `HOME`) for git, `XDG_CONFIG_HOME` for pip. The runner drops `GIT_CONFIG_GLOBAL` and `PIP_*`. Each is checked through that runner before anything is timed.
- Under test, inside a timed build, NOT pre-acquired (no registry mirror): the graywolf build's `.deb` (its only build step, `graywolf-fetch.sh`, runs through the runner, which drops `GRAYWOLF_BASE_URL`). No timed build runs npm. The build logs carry no per-step timestamps, so no transfer share is recorded apart; the graywolf build's time is that transfer.
- Tests (a) and (c) are kept as written: the budget's verdict and summary do not change when the acquisition is 10^6 times slower, and a slow local timing does fail it (the twin).

**c4 step 2 (after the first recorded hang):** the fix follows the measured cause. If it is the RPC hold, the smallest real fix is a socket timeout on the shared instance's RPC listener in our `loraham-rns-node`. The reproducer: a peer that holds the RPC connection while NomadNet starts, red before the fix.

## 3. The 27 inventory rows

The v3 table stands, with these changes:
- rows 1, 2 and 4: acquisition with the transfer backoff;
- rows 3, 5 and 6: class B with the transfer wrapper;
- row 9: P5;
- row 18: the retry is removed (P2).

## 4. Landing order

P2 → P3a → P3 → P4 → P5. Each: branch `cons/testlab-det-<n>-r<round>` on the then-current dev; checker block; gate-1 packet ≤ 90 KB; second reader; auditor; land on GREEN + lanes on the exact sha. Then one full testlab + CI run on dev and the injections: `--network none` (P3), binaries unreachable (P4: an EXTERNAL red, no marker), a slow transfer (P5: verdict unchanged). CHANGELOG under `## Unreleased`.

## 5. Numbers, measured

| figure | command | output |
|---|---|---|
| base | `git log --oneline -1 origin/dev` | `254bbae3` |
| typed build line | `grep -n TYPED_BUILD_FAILURE lhpc/core/build_regression.py` | :29 |
| the fetch URL | `grep -n BASE_URL lhpc/data/scripts/graywolf-fetch.sh` | :29, one hard-coded https URL |
| product timing lines | `grep -n "\[git\] \|\[venv\]\|pip sync" lhpc/core/install.py lhpc/core/service_binary_ops.py lhpc/core/service_selfupdate.py` | install.py:2582, service_binary_ops.py:767, service_selfupdate.py:1180 |
| history | `gh run list --workflow testlab.yml --limit 100` + per-job tally | 21 failed jobs, 5 flakes (v3 §6 table) |
````

## The full diff (`git diff HEAD~1 HEAD`)

```diff
diff --git a/.github/scripts/build-wheels.py b/.github/scripts/build-wheels.py
new file mode 100644
index 00000000..f7ae4fb1
--- /dev/null
+++ b/.github/scripts/build-wheels.py
@@ -0,0 +1,130 @@
+#!/usr/bin/env python3
+"""The wheels the slow-build lane's timed builds install, fetched before the lane (decision a).
+
+    build-wheels.py steps <manifest> <baseline>
+        the timed builds' `pip install` steps: one line per step, `<component> <args...>`
+    build-wheels.py fetch <manifest> <baseline> <mirrors> <asset> <wheels> <transfers>
+        check out each component's pinned source from <mirrors>, run every step of `steps` as
+        `pip wheel -w <wheels>` from that source (plus the build requirements of every local
+        project it names) and append each component's wall time to <transfers>
+
+A timed build installs from these wheels with no index (the lane's pip.conf), so its timing holds
+local work only. Every build the slow-target row times is covered: all components but the
+baseline's `excluded` ones, a superset of the lane's stacks (an extra build only costs time).
+Runs inside the lab image, so the wheels match the lane's Python. Stdlib only.
+"""
+from __future__ import annotations
+
+import importlib.util
+import subprocess
+import sys
+import tempfile
+import time
+import tomllib
+from pathlib import Path
+
+HERE = Path(__file__).resolve().parent
+_spec = importlib.util.spec_from_file_location("mirror_sources", HERE / "mirror-sources.py")
+mirror_sources = importlib.util.module_from_spec(_spec)
+_spec.loader.exec_module(mirror_sources)
+
+# `pip install` options with no `pip wheel` meaning. --no-build-isolation: the lane's venv has the
+# build requirements installed by an earlier step; `pip wheel` builds in isolation instead.
+NOT_FOR_WHEEL = {"--force-reinstall", "--no-build-isolation"}
+# setuptools' own default when a project declares no [build-system] (PEP 518).
+DEFAULT_BUILD_REQUIRES = ["setuptools>=40.8.0", "wheel"]
+
+
+def _components(manifest: Path, baseline: Path):
+    excluded = tomllib.loads(baseline.read_text()).get("excluded", {})
+    for stack in tomllib.loads(manifest.read_text())["stack"]:
+        yield from (c for c in stack.get("component", ()) if c["id"] not in excluded)
+
+
+def steps(manifest: Path, baseline: Path) -> list[tuple[str, list[str]]]:
+    """(component, the arguments after `install`) of every timed build's `<venv>/pip install`."""
+    out = []
+    for comp in _components(manifest, baseline):
+        for step in comp.get("build_steps", ()):
+            argv = [str(a) for a in step.get("argv", ())]
+            if len(argv) > 1 and argv[0].endswith("/pip") and argv[1] == "install":
+                out.append((comp["id"], argv[2:]))
+    return out
+
+
+def _local_projects(args: list[str], cwd: Path) -> list[Path]:
+    """The arguments that name a local directory (not an option, not a -c/-r file)."""
+    out, skip = [], False
+    for a in args:
+        if skip or a.startswith("-"):
+            skip = a in ("-c", "-r", "--constraint", "--requirement")
+            continue
+        if (cwd / a).is_dir():
+            out.append(cwd / a)
+    return out
+
+
+def _build_requires(project: Path) -> list[str]:
+    pyproject = project / "pyproject.toml"
+    if not pyproject.is_file():
+        return DEFAULT_BUILD_REQUIRES
+    system = tomllib.loads(pyproject.read_text()).get("build-system", {})
+    return system.get("requires", DEFAULT_BUILD_REQUIRES)
+
+
+def _run(argv: list[str], cwd: Path | None = None) -> None:
+    """With the transfer backoff (retry.sh): a fetch fails the job only after it."""
+    subprocess.run(["bash", "-c", f'. "{HERE}/retry.sh" && backoff "$@"', "backoff", *argv],
+                   cwd=cwd, check=True)
+
+
+def fetch(manifest: Path, baseline: Path, mirrors: Path, asset: Path, wheels: Path,
+          transfers: Path) -> None:
+    runtime = Path(tempfile.mkdtemp(prefix="build-wheels-"))
+    sources = {c["id"]: c["source"] for c in _components(manifest, baseline)
+               if (c.get("source") or {}).get("pin_commit")}
+    todo = steps(manifest, baseline)
+    # Each step runs from its component's source and may name other pinned sources by path.
+    need = {sources[c]["path"] for c, _ in todo}
+    need |= {s["path"] for s in sources.values()
+             if any(f"{{runtime}}/{s['path']}" in a for _, args in todo for a in args)}
+    for src in {s["path"]: s for s in sources.values()}.values():
+        if src["path"] in need:
+            dest = runtime / src["path"]
+            mirror = mirrors / mirror_sources.mirror_name(src["remote"])
+            subprocess.run(["git", "-c", "safe.directory=*", "clone", "--quiet", str(mirror),
+                            str(dest)], check=True)
+            subprocess.run(["git", "-C", str(dest), "-c", "advice.detachedHead=false",
+                            "checkout", "--quiet", src["pin_commit"]], check=True)
+    seconds: dict[str, float] = {}
+    for component, step in todo:
+        cwd = runtime / sources[component]["path"]
+        args = [a.replace("{runtime}", str(runtime)).replace("{asset}", str(asset))
+                .replace("{source}", str(cwd)) for a in step if a not in NOT_FOR_WHEEL]
+        t0 = time.monotonic()
+        _run([sys.executable, "-m", "pip", "wheel", "-q", "-w", str(wheels), *args], cwd=cwd)
+        for project in _local_projects(args, cwd):
+            _run([sys.executable, "-m", "pip", "wheel", "-q", "-w", str(wheels),
+                  *_build_requires(project)])
+        seconds[component] = seconds.get(component, 0.0) + time.monotonic() - t0
+    with transfers.open("a") as f:
+        for component, s in seconds.items():
+            f.write(f'[[transfer]]\nwhat = "wheels for the build of {component}"\n'
+                    f"seconds = {s:.1f}\n\n")
+
+
+def main(argv: list[str]) -> int:
+    cmd, *args = argv or ["help"]
+    if cmd == "steps" and len(args) == 2:
+        for component, rest in steps(Path(args[0]), Path(args[1])):
+            print(component, *rest)
+    elif cmd == "fetch" and len(args) == 6:
+        fetch(*map(Path, args))
+    else:
+        print(__doc__, file=sys.stderr)
+        return 2
+    return 0
+
+
+if __name__ == "__main__":
+    sys.exit(main(sys.argv[1:]))
diff --git a/.github/scripts/mirror-sources.py b/.github/scripts/mirror-sources.py
index db69caf0..407824cc 100644
--- a/.github/scripts/mirror-sources.py
+++ b/.github/scripts/mirror-sources.py
@@ -8,6 +8,10 @@
                                                <dir>/gitconfig as ~/.gitconfig; exit 1 if any fails
     mirror-sources.py resolved <dir>           through lhpc's own command runner, every remote in
                                                <dir>/remotes.txt resolves to its mirror; exit 1 if not
+    mirror-sources.py graywolf-version <manifest>   the Graywolf release the manifest pins
+    mirror-sources.py cli-requirements <manifest>   the Meshtastic CLI venv's pip requirements
+
+The last two serve the slow-build job's acquisition of the timed operations' transfers.
 
 The workflow fetches each mirror before the lane (with the transfer backoff) and the lane copies
 <dir>/gitconfig to the lab user's ~/.gitconfig, so the product's own `git clone <remote>` resolves
@@ -73,6 +77,30 @@ def git_config(remotes: list[str], directory: str) -> str:
     return "".join(out)
 
 
+def _build_steps(manifest: Path):
+    for stack in tomllib.loads(manifest.read_text())["stack"]:
+        for comp in stack.get("component", ()):
+            for step in comp.get("build_steps", ()):
+                yield [str(a) for a in step.get("argv", ())]
+
+
+def graywolf_version(manifest: Path) -> str:
+    """The version argument of the manifest's `graywolf-fetch.sh <dest> <version>` step."""
+    for argv in _build_steps(manifest):
+        for i, tok in enumerate(argv):
+            if tok.endswith("graywolf-fetch.sh") and len(argv) > i + 2:
+                return argv[i + 2]
+    raise SystemExit(f"no graywolf-fetch.sh step in {manifest}")
+
+
+def cli_requirements(manifest: Path) -> str:
+    """The requirements of the `pip install … meshtastic==<v> …` build step, space-separated."""
+    for argv in _build_steps(manifest):
+        if "install" in argv and any(a.startswith("meshtastic==") for a in argv):
+            return " ".join(a for a in argv[argv.index("install") + 1:] if not a.startswith("-"))
+    raise SystemExit(f"no `pip install meshtastic==…` step in {manifest}")
+
+
 def _home_with(gitconfig: Path, home: str) -> dict:
     """The environment git sees in the lane: a $HOME whose .gitconfig is `gitconfig`."""
     Path(home, ".gitconfig").write_text(gitconfig.read_text())
@@ -119,6 +147,10 @@ def main(argv: list[str]) -> int:
         return verify(Path(args[0]))
     elif cmd == "resolved" and len(args) == 1:
         return resolved(Path(args[0]))
+    elif cmd == "graywolf-version" and len(args) == 1:
+        print(graywolf_version(Path(args[0])))
+    elif cmd == "cli-requirements" and len(args) == 1:
+        print(cli_requirements(Path(args[0])))
     else:
         print(__doc__, file=sys.stderr)
         return 2
diff --git a/.github/workflows/testlab.yml b/.github/workflows/testlab.yml
index a0584bcb..84652449 100644
--- a/.github/workflows/testlab.yml
+++ b/.github/workflows/testlab.yml
@@ -308,10 +308,52 @@ jobs:
 
       - name: Build the lab image from THIS commit's Dockerfile
         run: |
+          . .github/scripts/retry.sh
           IMG=lhpc-testlab:ci
-          docker build -t "$IMG" -f .devcontainer/Dockerfile .devcontainer
+          backoff docker build -t "$IMG" -f .devcontainer/Dockerfile .devcontainer
           echo "IMG=$IMG" >> "$GITHUB_ENV"
 
+      - name: Acquire the timed operations' transfers (timed, recorded, never judged)
+        # Decision (a): a timed operation's transfer is measured as its own number and never
+        # fails a budget. The git sources (mirrors), the pinned Graywolf .deb with its
+        # checksums.txt, and the wheels of the Meshtastic CLI venv, of the self-update's pip
+        # sync and of every timed build's pip install are fetched here with the transfer
+        # backoff, each timed into transfers.toml; the lane then times those operations
+        # against these local copies. Still inside a timed build: the graywolf build's .deb.
+        run: |
+          . .github/scripts/retry.sh
+          mkdir -p srcmirror xfer/graywolf xfer/wheels slowevidence
+          timed() {  # timed <what> <argv...>: run it, append its wall time to transfers.toml
+            local what=$1 t0 t1; shift; t0=$(date +%s%N)
+            "$@"; t1=$(date +%s%N)
+            printf '[[transfer]]\nwhat = "%s"\nseconds = %s\n\n' "$what" \
+              "$(awk -v d=$((t1 - t0)) 'BEGIN { printf "%.1f", d / 1e9 }')" >> slowevidence/transfers.toml
+          }
+          python3 .github/scripts/mirror-sources.py list lhpc/data/manifest.example.toml > srcmirror/remotes.txt
+          while read -r remote; do
+            dir="srcmirror/$(python3 .github/scripts/mirror-sources.py name "$remote")"
+            timed "clone $remote" backoff sh -c 'rm -rf "$1" && git clone --quiet --mirror "$2" "$1"' _ "$dir" "$remote"
+          done < srcmirror/remotes.txt
+          python3 .github/scripts/mirror-sources.py config srcmirror/remotes.txt /srcmirror > srcmirror/gitconfig
+          gw=$(python3 .github/scripts/mirror-sources.py graywolf-version lhpc/data/manifest.example.toml)
+          base=https://github.com/chrissnell/graywolf/releases/download/v$gw
+          mkdir -p "xfer/graywolf/v$gw"
+          for f in "graywolf_${gw}_arm64.deb" checksums.txt; do
+            timed "graywolf $f" backoff curl -fsSL -o "xfer/graywolf/v$gw/$f" "$base/$f"
+          done
+          # In the lab image (the Python of the lane), as root: xfer/ belongs to the runner user.
+          cli=$(python3 .github/scripts/mirror-sources.py cli-requirements lhpc/data/manifest.example.toml)
+          timed "wheels: $cli setuptools>=68 ./ (this checkout)" docker run --rm --user root \
+            -e PIP_ROOT_USER_ACTION=ignore -v "$PWD":/repo:ro -v "$PWD/xfer":/xfer "$IMG" bash -lc \
+            ". /repo/.github/scripts/retry.sh && backoff python3 -m pip download -q -d /xfer/wheels $cli 'setuptools>=68' /repo"
+          # The pip install of every timed build (build-wheels.py times each component itself).
+          docker run --rm --user root -e PIP_ROOT_USER_ACTION=ignore -v "$PWD":/repo:ro \
+            -v "$PWD/srcmirror":/srcmirror:ro -v "$PWD/xfer":/xfer -v "$PWD/slowevidence":/out \
+            "$IMG" python3 /repo/.github/scripts/build-wheels.py fetch \
+              /repo/lhpc/data/manifest.example.toml /repo/tests/data/slow-target-builds.toml \
+              /srcmirror /repo/lhpc/data /xfer/wheels /out/transfers.toml
+          cat slowevidence/transfers.toml
+
       # io.max takes whole disks. REQUIRED: the disk behind the container's writable layer,
       # where the calibration work dir under the lab user's home lives, and the disk behind
       # Docker's storage, which holds the /tmp volume. The writable layer is measured, not
@@ -396,11 +438,18 @@ jobs:
           export LHPC_SYSTEM_PROVIDER=lhpc_testlab.provider:build
           python3 -m venv /tmp/venv && . /tmp/venv/bin/activate
           . /tmp/repo/.github/scripts/retry.sh
-          retry 3 pip install -q -e .
-          retry 3 pip install -q -e ./testlab
-          retry 3 pip install -q pytest
+          backoff pip install -q -e .
+          backoff pip install -q -e ./testlab
+          backoff pip install -q pytest
+          # The timed operations take their transfers from the copies acquired above (decision a):
+          # the clones through ~/.gitconfig (HOME reaches the product runner, GIT_CONFIG_GLOBAL
+          # does not; mirror-sources.py), the fetch through its URL, the pip operations through
+          # a pip.conf the lane writes for them (LHPC_SLOW_BUILD_WHEELS).
+          cp /srcmirror/gitconfig ~/.gitconfig
+          python3 .github/scripts/mirror-sources.py resolved /srcmirror
+          export GRAYWOLF_BASE_URL=file:///xfer/graywolf
           # No -x: the budget case needs every measurement the run could make.
-          LHPC_SLOW_BUILD=1 LHPC_SLOW_BUILD_OUT=/out \
+          LHPC_SLOW_BUILD=1 LHPC_SLOW_BUILD_OUT=/out LHPC_SLOW_BUILD_WHEELS=/xfer/wheels \
             python -m pytest testlab/tests/slowbuild -q -rs \
               --junitxml=/out/junit-slow-build.xml
           LANE
@@ -410,6 +459,7 @@ jobs:
             -e SLOW_CPUS -e SLOW_MEM -e SLOW_WRITE_IOPS -e SLOW_READ_IOPS -e SLOW_IO_ROOT_DISK \
             -e LHPC_SLOW_BUILD_RUN_URL="$GITHUB_SERVER_URL/$GITHUB_REPOSITORY/actions/runs/$GITHUB_RUN_ID" \
             -v "$PWD":/repo:ro -v lhpc_slow_tmp:/tmp -v "$PWD/slowevidence":/out \
+            -v "$PWD/srcmirror":/srcmirror:ro -v "$PWD/xfer":/xfer:ro \
             "$IMG" bash -lc '
               set -e
               cp -r /repo /tmp/repo && chown -R lhpclab /tmp/repo && chmod a+w /out
@@ -462,11 +512,39 @@ jobs:
             cat slowevidence/slow-build-summary.md >> "$GITHUB_STEP_SUMMARY"
           fi
 
+      # The upload is a transfer after the verdict (class A): it is tried twice, and neither
+      # attempt can turn the job red. Run 38822953533 measured everything green, then went red
+      # on `CreateArtifact: … ETIMEDOUT` alone. A job whose evidence could not be uploaded says
+      # so in a warning and in its summary.
       - name: Upload the slow-build evidence
+        id: upload
         if: always()
+        continue-on-error: true
         uses: actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a # v7.0.1
         with:
           name: slow-build-evidence
           path: slowevidence/**
           if-no-files-found: warn
           retention-days: 90
+
+      - name: Wait before the second upload attempt
+        if: always() && steps.upload.outcome == 'failure'
+        run: sleep 120
+
+      - name: Upload the slow-build evidence (second attempt)
+        id: upload2
+        if: always() && steps.upload.outcome == 'failure'
+        continue-on-error: true
+        uses: actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a # v7.0.1
+        with:
+          name: slow-build-evidence
+          path: slowevidence/**
+          if-no-files-found: warn
+          retention-days: 90
+          overwrite: true
+
+      - name: Report an evidence upload that failed twice
+        if: always() && steps.upload.outcome == 'failure' && steps.upload2.outcome == 'failure'
+        run: |
+          echo "::warning::the slow-build evidence could not be uploaded (two attempts); the verdict above stands"
+          echo "**Evidence upload failed twice: no slow-build-evidence artifact for this run.**" >> "$GITHUB_STEP_SUMMARY"
diff --git a/docs/testlab.md b/docs/testlab.md
index edb9c56a..29b0d034 100644
--- a/docs/testlab.md
+++ b/docs/testlab.md
@@ -188,7 +188,19 @@ about half an hour.
   each lane stack's install (clone, checkout, the Meshtastic CLI venv), every component's build
   (wall time and longest quiet gap), the graywolf upstream fetch and a self-update from the
   previous release tag, then checks them with `lhpc.core.slow_target` against
-  `tests/data/slow-target-builds.toml`.
+  `tests/data/slow-target-builds.toml`. The transfers of the timed clones, the graywolf fetch,
+  the Meshtastic CLI venv, the self-update's pip sync and every timed build's `pip install`
+  (`.github/scripts/build-wheels.py`) are made before the lane, in a step of their own with the
+  transfer backoff, and each one's wall time is written to `transfers.toml` in the evidence:
+  recorded, never judged. The lane times those operations against the local copies (the git
+  mirrors through `~/.gitconfig`, `GRAYWOLF_BASE_URL=file://…`, and for every pip a `pip.conf`
+  with no index and the wheel directory, through `XDG_CONFIG_HOME`; the product's command runner
+  forwards those two and drops `GIT_CONFIG_GLOBAL` and `PIP_*`), and checks through that runner,
+  before timing, that git and pip see them. So the budget judges local work, with one transfer
+  still under test inside a timed build: the graywolf build's `.deb`, which its only build step
+  (`graywolf-fetch.sh`, run through the runner, which drops `GRAYWOLF_BASE_URL`) fetches from
+  GitHub, so that build's time is that transfer. No timed build runs npm, and the build logs carry
+  no per-step timestamps.
   - `test_slow_build_env` fails unless the throttle is in force — `io.max` holds a tight line for
     the writable layer's disk the job measured (`SLOW_IO_ROOT_DISK`) and for the disk behind the
     calibration work dir, each by name — and no
@@ -201,7 +213,9 @@ about half an hour.
     (a skip is not a pass; the one exception is the
     [bootstrap state](maintenance.md#branches-and-releases)) and uploads
     `slow-build-evidence`: `slow-target-builds.toml` (`source = "throttled-ci"`), the JUnit and the
-    E/Z summary, which is also the job summary.
+    E/Z summary, which is also the job summary. The upload is a transfer after the verdict: it
+    is tried twice, two minutes apart, and never fails the job; a run whose upload failed twice
+    says so in a warning and in its summary, and has no evidence artifact (re-run the job for one).
 
 ## Codespaces costs
 
diff --git a/lhpc/data/scripts/graywolf-fetch.sh b/lhpc/data/scripts/graywolf-fetch.sh
index 2c6c4a9f..1404f7e5 100755
--- a/lhpc/data/scripts/graywolf-fetch.sh
+++ b/lhpc/data/scripts/graywolf-fetch.sh
@@ -26,7 +26,8 @@ VERSION="${2:?usage: graywolf-fetch.sh <dest-dir> <version> [--from-upstream]}"
 FROM_UPSTREAM=""
 [ "${3:-}" = "--from-upstream" ] && FROM_UPSTREAM=1
 
-BASE_URL="https://github.com/chrissnell/graywolf/releases/download"
+# GRAYWOLF_BASE_URL: the test lab serves a pre-fetched copy (file://…); the sha256 check is unchanged.
+BASE_URL="${GRAYWOLF_BASE_URL:-https://github.com/chrissnell/graywolf/releases/download}"
 
 # sha256 of the upstream release assets, from the release's own checksums.txt.
 #   key: <version>/<debian-arch>
diff --git a/testlab/tests/slowbuild/test_slow_build.py b/testlab/tests/slowbuild/test_slow_build.py
index 81ce74ac..c42a29e3 100644
--- a/testlab/tests/slowbuild/test_slow_build.py
+++ b/testlab/tests/slowbuild/test_slow_build.py
@@ -46,6 +46,11 @@ pytestmark = pytest.mark.slow
 REPO = next(p for p in Path(__file__).resolve().parents if (p / "lhpc" / "version.py").is_file())
 BASELINE = tomllib.loads((REPO / "tests" / "data" / "slow-target-builds.toml").read_text())
 OUT = Path(os.environ.get("LHPC_SLOW_BUILD_OUT", "/out"))
+# Decision (a): the wheels the timed pip operations (L5 the CLI venv, L4 the self-update's pip
+# sync, every timed build's `pip install`) install were fetched before the lane, and their
+# transfer time was recorded on its own (the workflow's transfers.toml). Those operations install
+# from them, offline, and the lane proves that before timing them (`_offline`).
+WHEELS = os.environ.get("LHPC_SLOW_BUILD_WHEELS", "")
 STACKS = load_manifest(default_manifest_path())
 PYPROJECT = (REPO / "pyproject.toml").read_text()
 # L8: the harness's own per-command limit. It must stay far above every product limit, or it
@@ -228,6 +233,40 @@ def _deb_fetch(svc, component: str, tmp: Path) -> None:
     _record(component, "deb-fetch", seconds)
 
 
+def _local_pip(env: dict, conf: Path) -> dict:
+    """`env` for a timed pip operation: pip reads `<conf>/pip/pip.conf` (the pre-fetched wheels,
+    no index) through XDG_CONFIG_HOME, which lhpc's command runner forwards to its children; it
+    drops PIP_* variables. Unchanged without wheels."""
+    if not WHEELS:
+        return env
+    (conf / "pip").mkdir(parents=True, exist_ok=True)
+    (conf / "pip" / "pip.conf").write_text(f"[global]\nno-index = true\nfind-links = {WHEELS}\n")
+    return {**env, "XDG_CONFIG_HOME": str(conf)}
+
+
+_RUNNER_PIP_CONFIG = ("import sys; from lhpc.core.probes.backends import RealCommandRunner; "
+                      "print(RealCommandRunner().run([sys.executable, '-m', 'pip', 'config', "
+                      "'list'], 120).stdout, end='')")
+
+
+def _pip_config_through_the_runner(env: dict) -> str:
+    """pip's configuration as a child of lhpc's command runner sees it, the way the timed pip
+    operations run: in a fresh interpreter, whose runner builds its fixed environment from
+    `env`."""
+    return subprocess.run([sys.executable, "-c", _RUNNER_PIP_CONFIG], env=env, check=True,
+                          capture_output=True, text=True, timeout=HARNESS_S).stdout
+
+
+def _offline(env: dict) -> dict:
+    """Fails unless a pip the runner starts under `env` has no index and the wheels: then the
+    timed operation cannot transfer, it can only fail on a wheel the acquisition missed."""
+    seen = _pip_config_through_the_runner(env)
+    assert ("global.no-index='true'" in seen.splitlines()
+            and f"global.find-links='{WHEELS}'" in seen.splitlines()), (
+        f"a timed pip operation would reach the index; pip's config under the runner:\n{seen}")
+    return env
+
+
 def _install(env: dict, stack: str):
     t0 = time.monotonic()
     r = run_lhpc(env, "install", stack, "--yes", timeout=HARNESS_S)
@@ -384,13 +423,16 @@ def test_slow_build_stack(env, svc, stack, tmp_path):
         # measures a first install, so it uninstalls that one first.
         _judged("uninstalling the lab's meshtastic",
                 run_lhpc(env, "uninstall", stack, "--yes", timeout=HARNESS_S))
-    r = _install(env, stack)
+    # The timed pip operations (the CLI venv inside the meshtastic install, every build's pip)
+    # install from the pre-fetched wheels with no index; the clones resolve to the mirrors.
+    local = _offline(_local_pip(env, tmp_path / "pipconf")) if WHEELS else env
+    r = _install(local if stack == "meshtastic" else env, stack)
     mine = [(c, o) for c, o in LANE_OPS if _STACK_OF.get(c) == stack]
     for component, op in mine:
         if op == "clone":
             _adoption(env, component)
         elif op == "build":
-            _build(env, component)
+            _build(local, component)
         elif op == "deb-fetch":
             _deb_fetch(svc, component, tmp_path)
     if stack == "meshtastic":
@@ -483,6 +525,8 @@ def test_slow_build_selfupdate(tmp_path):
         subprocess.run([*pip, "-e", str(target)], check=True, timeout=HARNESS_S)
     _install_units(venv / "bin" / "python", root, env)
     (root / "state" / "selfupdate.request").write_text("normal\n")
+    if WHEELS:
+        env = _offline(_local_pip(env, tmp_path / "pipconf"))
     seconds, out = _helper(venv / "bin" / "lhpc", env)
     assert _git("-C", str(co), "rev-parse", "HEAD") == cand, f"{prev} was not updated to {cand}"
     _record(stt.SELFUPDATE_COMPONENT, "selfupdate-helper", seconds)
diff --git a/testlab/tests/unit/test_slow_build_lane.py b/testlab/tests/unit/test_slow_build_lane.py
index 862a35cd..9bee9672 100644
--- a/testlab/tests/unit/test_slow_build_lane.py
+++ b/testlab/tests/unit/test_slow_build_lane.py
@@ -7,7 +7,9 @@ helpers run in the ordinary unit lane."""
 from __future__ import annotations
 
 import importlib.util
+import os
 import subprocess
+import sys
 import types
 from pathlib import Path
 
@@ -512,3 +514,73 @@ def test_the_fetch_scripts_bare_download_failed_is_judged_by_curls_own_line(stde
     with pytest.raises(pytest.fail.Exception) as red:
         lane._judged("the upstream fetch of 0.14.14", _done(rc=1, stderr=stderr))
     assert str(red.value).startswith("EXTERNAL-UNAVAILABLE what=") is external
+
+
+def test_without_wheels_the_timed_pip_operations_run_unchanged(monkeypatch, tmp_path):
+    env = {"LHPC_RUNTIME_ROOT": "/r"}
+    monkeypatch.setattr(lane, "WHEELS", "")
+    assert lane._local_pip(env, tmp_path / "conf") == env
+    assert not (tmp_path / "conf").exists()
+
+
+@pytest.mark.parametrize("via", ["pip_conf", "pip_env"])
+def test_only_a_pip_conf_reaches_a_pip_the_products_runner_starts(monkeypatch, tmp_path, via):
+    """Decision (a): the timed pip operations install offline from the pre-fetched wheels. The
+    first version exported PIP_NO_INDEX / PIP_FIND_LINKS, which lhpc's command runner drops, so
+    those pips still used the index; `_offline` now refuses that before anything is timed."""
+    wheels = str(tmp_path / "wheels")
+    monkeypatch.setattr(lane, "WHEELS", wheels)
+    env = {"PATH": os.environ["PATH"], "HOME": str(tmp_path / "home"),
+           "PYTHONPATH": os.pathsep.join(sys.path)}
+    if via == "pip_conf":
+        env = lane._local_pip(env, tmp_path / "conf")
+        assert lane._offline(env) is env
+        seen = lane._pip_config_through_the_runner(env).splitlines()
+        assert "global.no-index='true'" in seen and f"global.find-links='{wheels}'" in seen
+    else:
+        env = {**env, "PIP_NO_INDEX": "1", "PIP_FIND_LINKS": wheels}
+        with pytest.raises(AssertionError, match="would reach the index"):
+            lane._offline(env)
+
+
+# ---- decision (a): a transfer is recorded, never judged (plan P5 tests a and c) -------------
+
+def _budget(monkeypatch, tmp_path, clone_s: float, transfer_s: float) -> tuple[str, str]:
+    """The budget case's verdict and summary for one timed clone of `clone_s` seconds, with the
+    acquisition's transfers.toml beside it recording that clone's transfer at `transfer_s`."""
+    out = tmp_path / f"{clone_s}-{transfer_s}"
+    out.mkdir()
+    (out / "transfers.toml").write_text(
+        '[[transfer]]\nwhat = "clone https://github.com/markqvist/Reticulum.git"\n'
+        f"seconds = {transfer_s}\n")
+    monkeypatch.setattr(lane, "OUT", out)
+    # Stubs the collaborators: git (the previous tag, not an introducing release) and the
+    # product's clone limit, so the margin rule is the only judge.
+    monkeypatch.setattr(lane, "_prev_tag", lambda: "v9.9.9")
+    monkeypatch.setattr(lane, "_introducing", lambda prev: False)
+    monkeypatch.setattr(lane.stt, "limit", lambda op: 600.0)
+    monkeypatch.setattr(lane, "LANE_OPS", [("rns", "clone")])
+    monkeypatch.setattr(lane, "BASELINE", {"measured": [
+        {"component": "rns", "op": "clone", "seconds": 100.0, "source": "zero2w", "key": "k",
+         "date": "2026-10-01"}]})
+    monkeypatch.setattr(lane, "EVIDENCE", {("rns", "clone"): {
+        "component": "rns", "op": "clone", "seconds": clone_s, "key": "k"}})
+    try:
+        lane.test_slow_build_budget()
+        verdict = "pass"
+    except AssertionError:
+        verdict = "fail"
+    return verdict, (out / "slow-build-summary.md").read_text()
+
+
+def test_a_transfer_however_slow_leaves_the_budget_verdict_unchanged(monkeypatch, tmp_path):
+    """(a) + (c): the acquisition 10^6 times slower changes neither the verdict nor the summary.
+    New invariant: the twin below shows the same instrument does turn on a slow LOCAL timing."""
+    fast = _budget(monkeypatch, tmp_path, clone_s=100.0, transfer_s=1.0)
+    slow = _budget(monkeypatch, tmp_path, clone_s=100.0, transfer_s=1e6)
+    assert fast == slow and fast[0] == "pass"
+
+
+def test_a_slow_local_timing_does_fail_the_budget(monkeypatch, tmp_path):
+    verdict, summary = _budget(monkeypatch, tmp_path, clone_s=400.0, transfer_s=1.0)
+    assert verdict == "fail" and "**FAIL** rns clone: limit 600 s < 2 x 400 s measured" in summary
diff --git a/tests/repo/test_build_wheels.py b/tests/repo/test_build_wheels.py
new file mode 100644
index 00000000..aefd137d
--- /dev/null
+++ b/tests/repo/test_build_wheels.py
@@ -0,0 +1,100 @@
+"""`.github/scripts/build-wheels.py`: the wheels every timed slow-build build installs are fetched
+before the lane (decision a). These cases pin which steps it covers and how each one becomes a
+`pip wheel` from the pinned source; the fetch itself is the slow-build job's acquisition step. A
+new invariant (new tooling): negative cases instead of a parent-red.
+"""
+import importlib.util
+import re
+import subprocess
+import sys
+
+import repo_paths
+
+SCRIPT = repo_paths.REPO / ".github" / "scripts" / "build-wheels.py"
+MANIFEST = repo_paths.REPO / "lhpc" / "data" / "manifest.example.toml"
+BASELINE = repo_paths.REPO / "tests" / "data" / "slow-target-builds.toml"
+GIT = ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", "-c", "commit.gpgsign=false"]
+
+
+def _module():
+    spec = importlib.util.spec_from_file_location("build_wheels", SCRIPT)
+    mod = importlib.util.module_from_spec(spec)
+    spec.loader.exec_module(mod)
+    return mod
+
+
+def test_every_pip_step_of_a_timed_build_is_covered_and_no_excluded_one():
+    import tomllib
+    excluded = tomllib.loads(BASELINE.read_text())["excluded"]
+    want = []
+    for stack in tomllib.loads(MANIFEST.read_text())["stack"]:
+        for comp in stack.get("component", ()):
+            for step in comp.get("build_steps", ()):
+                argv = [str(a) for a in step["argv"]]
+                if argv[0].endswith("/pip") and argv[1] == "install" and comp["id"] not in excluded:
+                    want.append(f"{comp['id']} {' '.join(argv[2:])}")
+    r = subprocess.run([sys.executable, str(SCRIPT), "steps", MANIFEST, BASELINE],
+                       capture_output=True, text=True, check=True)
+    assert r.stdout.splitlines() == want and len(want) == 13
+    # sideband's build installs with pip too, but the row excludes it (GTK/Kivy only).
+    assert "sideband" in excluded and not any(s.startswith("sideband ") for s in want)
+
+
+def _repo(path, files):
+    subprocess.run([*GIT, "init", "-q", str(path)], check=True)
+    for name, text in files.items():
+        (path / name).write_text(text)
+    subprocess.run([*GIT, "-C", str(path), "add", "."], check=True)
+    subprocess.run([*GIT, "-C", str(path), "commit", "-q", "-m", "c"], check=True)
+    return subprocess.run(["git", "-C", str(path), "rev-parse", "HEAD"], capture_output=True,
+                          text=True, check=True).stdout.strip()
+
+
+def test_a_build_step_becomes_pip_wheel_from_its_pinned_source(tmp_path, monkeypatch):
+    """One step that names its own source, another pinned source and a constraints file: run
+    from the component's checkout, with --force-reinstall dropped, then the build requirements of
+    both local projects (the default one, and hatchling where pyproject declares it)."""
+    bw = _module()
+    mirrors = tmp_path / "mirrors"
+    mirrors.mkdir()
+    pins = {}
+    for name, files in (("a", {"setup.py": ""}),
+                        ("b", {"pyproject.toml": '[build-system]\nrequires = ["hatchling"]\n'})):
+        pins[name] = _repo(tmp_path / f"up-{name}", files)
+        remote = f"https://example.invalid/x/{name}.git"
+        subprocess.run(["git", "clone", "-q", "--mirror", str(tmp_path / f"up-{name}"),
+                        str(mirrors / bw.mirror_sources.mirror_name(remote))], check=True)
+    manifest = tmp_path / "m.toml"
+    manifest.write_text(
+        '[[stack]]\nid = "s"\n'
+        '[[stack.component]]\nid = "a"\n'
+        '[stack.component.source]\npath = "src/a"\nremote = "https://example.invalid/x/a.git"\n'
+        f'pin_commit = "{pins["a"]}"\n'
+        '[[stack.component.build_steps]]\nargv = [".venv/bin/pip", "install", '
+        '"--force-reinstall", ".", "{runtime}/src/b", "-c", "{asset}/c.txt"]\n'
+        '[[stack.component]]\nid = "b"\n'
+        '[stack.component.source]\npath = "src/b"\nremote = "https://example.invalid/x/b.git"\n'
+        f'pin_commit = "{pins["b"]}"\n'
+        '[[stack.component]]\nid = "skipped"\n'
+        '[[stack.component.build_steps]]\nargv = [".venv/bin/pip", "install", "x"]\n')
+    baseline = tmp_path / "b.toml"
+    baseline.write_text('[excluded]\nskipped = "not timed"\n')
+    calls = []
+    # Stubs the collaborator, the network fetch (pip wheel under the backoff): records it.
+    monkeypatch.setattr(bw, "_run", lambda argv, cwd=None: calls.append((argv, cwd)))
+    monkeypatch.setattr(bw.tempfile, "mkdtemp", lambda prefix: str(tmp_path / "rt"))
+    wheels, transfers = tmp_path / "w", tmp_path / "transfers.toml"
+    bw.fetch(manifest, baseline, mirrors, tmp_path / "asset", wheels, transfers)
+    rt = tmp_path / "rt"
+    pip = [sys.executable, "-m", "pip", "wheel", "-q", "-w", str(wheels)]
+    assert calls == [
+        ([*pip, ".", f"{rt}/src/b", "-c", f"{tmp_path}/asset/c.txt"], rt / "src" / "a"),
+        ([*pip, "setuptools>=40.8.0", "wheel"], None),
+        ([*pip, "hatchling"], None),
+    ]
+    for name in ("a", "b"):
+        head = subprocess.run(["git", "-C", str(rt / "src" / name), "rev-parse", "HEAD"],
+                              capture_output=True, text=True, check=True).stdout.strip()
+        assert head == pins[name]
+    assert re.fullmatch(r'\[\[transfer\]\]\nwhat = "wheels for the build of a"\nseconds = '
+                        r"\d+\.\d\n\n", transfers.read_text())
diff --git a/tests/repo/test_mirror_sources.py b/tests/repo/test_mirror_sources.py
index 25e0b756..cc384c34 100644
--- a/tests/repo/test_mirror_sources.py
+++ b/tests/repo/test_mirror_sources.py
@@ -154,3 +154,22 @@ def test_only_a_gitconfig_in_home_redirects_a_clone_through_the_products_runner(
     expected = (f"ok  {remote}\n" if redirected
                 else f"NOT REDIRECTED  {remote}  (resolves to {remote})\n")
     assert (check.returncode, check.stdout) == (0 if redirected else 1, expected), check.stderr
+
+
+def test_the_slow_build_acquisition_reads_its_versions_from_the_manifest():
+    """New tooling (an invariant, a negative case below): the pinned Graywolf release and the
+    Meshtastic CLI venv's requirements, exactly as the manifest's build steps carry them."""
+    gw = _run("graywolf-version", MANIFEST)
+    cli = _run("cli-requirements", MANIFEST)
+    assert gw.returncode == 0 and gw.stdout.strip().count(".") == 2
+    assert f'"{gw.stdout.strip()}"' in MANIFEST.read_text()
+    reqs = cli.stdout.split()
+    assert cli.returncode == 0 and [r.split("==")[0] for r in reqs] == ["meshtastic", "pycryptodomex"]
+
+
+def test_a_manifest_without_those_steps_is_an_error(tmp_path):
+    manifest = tmp_path / "m.toml"
+    manifest.write_text('[[stack]]\nid = "s"\n[[stack.component]]\nid = "c"\n')
+    gw, cli = _run("graywolf-version", manifest), _run("cli-requirements", manifest)
+    assert gw.returncode == 1 and f"no graywolf-fetch.sh step in {manifest}" in gw.stderr
+    assert cli.returncode == 1 and f"no `pip install meshtastic==…` step in {manifest}" in cli.stderr
diff --git a/tests/stacks/test_graywolf_fetch.py b/tests/stacks/test_graywolf_fetch.py
new file mode 100644
index 00000000..efa14dde
--- /dev/null
+++ b/tests/stacks/test_graywolf_fetch.py
@@ -0,0 +1,39 @@
+"""graywolf-fetch.sh's download base: GitHub by default, `GRAYWOLF_BASE_URL` when set (the test
+lab's slow-build serves a pre-fetched copy, so the timed fetch measures local work). The script
+runs for real with `curl`, `dpkg` and `dpkg-deb` replaced on PATH by recorders: no network, and
+the URL it asked for is read back exactly.
+"""
+import subprocess
+
+import pytest
+
+from lhpc.core.assets import asset_path
+
+SHIMS = {
+    "curl": '#!/bin/sh\nprintf "%s\\n" "$@" >> "$CURL_LOG"\nexit 22\n',   # every download fails
+    "dpkg": "#!/bin/sh\necho arm64\n",
+    "dpkg-deb": "#!/bin/sh\nexit 0\n",
+}
+
+
+def _fetch(tmp_path, env_extra):
+    shims = tmp_path / "shims"
+    shims.mkdir()
+    for name, body in SHIMS.items():
+        (shims / name).write_text(body)
+        (shims / name).chmod(0o755)
+    log = tmp_path / "curl.log"
+    r = subprocess.run(["bash", str(asset_path("scripts/graywolf-fetch.sh")), str(tmp_path / "gw"),
+                        "0.14.14"], capture_output=True, text=True, check=False,
+                       env={"PATH": f"{shims}:/usr/bin:/bin", "CURL_LOG": str(log), **env_extra})
+    return r, log.read_text().split() if log.exists() else []
+
+
+@pytest.mark.parametrize("env_extra, url", [
+    ({}, "https://github.com/chrissnell/graywolf/releases/download/v0.14.14/graywolf_0.14.14_arm64.deb"),
+    ({"GRAYWOLF_BASE_URL": "file:///srv/gw"}, "file:///srv/gw/v0.14.14/graywolf_0.14.14_arm64.deb"),
+], ids=["default-github", "base-url-override"])
+def test_the_download_base_is_github_unless_overridden(tmp_path, env_extra, url):
+    r, curl_args = _fetch(tmp_path, env_extra)
+    assert r.returncode == 1 and "download failed" in r.stderr
+    assert curl_args[-1] == url
```
