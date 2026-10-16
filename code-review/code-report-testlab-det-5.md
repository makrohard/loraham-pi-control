# Code report: testlab-det-5 v3 (packet P5: transfer separation in slow-build)

Branch `cons/testlab-det-5-r1`, stacked on P4a (`45348dbc`, its packet commit, rebased unchanged), which is stacked on P3 v4 (`225ef6a4`) on `origin/dev` = `d77e34f4`. One code commit, d3e252b3. Plan v4 §2 P5 and decision (a): a timed operation's transfer is its own number and never fails a budget.

## v3: round 13 (RED, 2 findings), the maintainer's ruling, and what changed

**F1: build-internal transfers stayed inside judged build timings.** The ruling under decision (a), "separate, the CHEAP way", is recorded in the plan note on `cons/testlab-det-plan` @ 5e982825:
- **pip inside a timed build: separated now.**
  - New `.github/scripts/build-wheels.py`, run in the acquisition step inside the lab image. It takes every `<venv>/pip install` build step of every component but the baseline's `excluded` ones: 13 steps of 7 builds (meshcore-node, meshcore-webui, meshcore-cli, rns, nomadnet, lxmd, meshchat).
  - Each step runs as `pip wheel -w /xfer/wheels` from the component's source, checked out at its `pin_commit` from its mirror, with `{runtime}`, `{asset}` and `{source}` resolved. `--force-reinstall` and `--no-build-isolation` are dropped; they have no wheel meaning.
  - The `[build-system] requires` of every local project the step names are fetched too, with setuptools' default when none is declared.
  - Each component's wall time is appended to `transfers.toml`.
  - In the lane, every timed build runs with the same `_offline(_local_pip(...))` env as the CLI venv: pip.conf through `XDG_CONFIG_HOME`, no index, checked through the runner before timing.
- **Still under test inside a timed build, by the ruling: the graywolf build's `.deb`.** Its only build step, `graywolf-fetch.sh`, runs through the runner, which drops `GRAYWOLF_BASE_URL`, so it fetches from GitHub.
  - No timed build runs npm (the inventory of every lane build step is in the plan note).
  - The build logs carry no per-step timestamps, so no transfer share can be recorded apart. For graywolf the build IS that one step.
  - Stated in docs/testlab.md and in the plan.
- Both acquisition containers now run as root (`--user root`, `PIP_ROOT_USER_ACTION=ignore`), because the image's user `lhpclab` cannot be assumed to write the runner-owned `xfer/`.

**F2: plan tests (a) and (c) are added** (testlab/tests/unit/test_slow_build_lane.py):
- `test_a_transfer_however_slow_leaves_the_budget_verdict_unchanged` runs the real `test_slow_build_budget` on one timed clone with a `transfers.toml` beside it. With that clone's transfer at 1 s and at 10^6 s, verdict and summary are identical (pass).
- The twin, `test_a_slow_local_timing_does_fail_the_budget`: the same instrument with the LOCAL clone at 400 s against a 600 s limit fails with `**FAIL** rns clone: limit 600 s < 2 x 400 s measured`.
- Both are green on the parent too: the budget never read `transfers.toml`. They are a new invariant with a negative twin, not a red-before.

## v2: what changed since v1 (8e81808e), and why

**A real defect, found by the second reader.** v1 exported `GIT_CONFIG_GLOBAL`, `PIP_FIND_LINKS` and `PIP_NO_INDEX` for the timed operations. But the clones (L7a), the CLI venv's pip (L5) and the self-update's pip sync (L4) run as children of lhpc's `RealCommandRunner`. That runner gives its children `{**_FIXED_ENV, **env}`: PATH/LANG/LC_ALL plus a whitelist that keeps `HOME` and `XDG_CONFIG_HOME` and drops the rest (lhpc/core/probes/backends.py:188–203; :262, :374, :430). So v1's L7a/L5/L4 timings would still have included the network. Only L6 worked, because `graywolf-fetch.sh` runs by direct `subprocess.run`, which inherits `GRAYWOLF_BASE_URL`. v2 uses channels the runner forwards, and checks each one THROUGH the runner before anything is timed:

- **git:** the mirror config as the lab user's `~/.gitconfig`. `mirror-sources.py resolved` (P3 v4) runs through the runner in the lane and fails unless every remote resolves to its mirror. The stack cases use `lab_env`, which keeps the lab user's `HOME`. The self-update case uses its own `HOME` but only fetches from a local remote (a repo-local `insteadOf`, unchanged).
- **pip:** `_local_pip(env, conf)` writes `<conf>/pip/pip.conf` (`no-index = true`, `find-links = <wheels>`) and sets `XDG_CONFIG_HOME=<conf>`, only for the meshtastic install and the self-update helper. `_offline(env)` runs `pip config list` through `RealCommandRunner` in a fresh interpreter (so the runner's fixed env is built from `env`, as in the product process) and fails before the timing unless pip shows both settings. With no index, a missing wheel fails the operation; it cannot fetch.
- **Graywolf fetch:** unchanged (`GRAYWOLF_BASE_URL`, direct subprocess).

Test through the real runner: `test_only_a_pip_conf_reaches_a_pip_the_products_runner_starts`.
- `pip_conf`: pip, as the runner's child, shows `global.no-index='true'` and the wheels.
- `pip_env` (v1's mechanism): `_offline` refuses it ("would reach the index"), since the runner dropped the variables.

**The evidence upload (the handler's order after the dev batch run 38822953533).** That run measured everything green (`11 passed in 2561.59s`), then went red on the upload step alone: `Failed to CreateArtifact: Unable to make request: ETIMEDOUT`. The upload is a transfer after the verdict (class A).
- It is now tried twice, 120 s apart. Both attempts are `continue-on-error: true`, and the second has `overwrite: true` in case the first left a partial artifact.
- A double failure prints `::warning::` and a summary line. It never turns the job red: the job's verdict is the lane step's (the JUnit gate) and nothing else.
- Cost: such a run has no evidence artifact, and docs/testlab.md says to re-run the job for one.
- A workflow change, proved by a lane run, not a test.

## Commit

| commit | files | tests | red-before |
|---|---|---|---|
| d3e252b3 | .github/workflows/testlab.yml (slow-build acquisition: mirrors, Graywolf, CLI wheels, build wheels, all as transfers; the lane's `~/.gitconfig` + `resolved` + `GRAYWOLF_BASE_URL`; `backoff`; the twice-tried evidence upload), .github/scripts/build-wheels.py (new), .github/scripts/mirror-sources.py (`graywolf-version`, `cli-requirements`), lhpc/data/scripts/graywolf-fetch.sh (`GRAYWOLF_BASE_URL`, the one product line), testlab/tests/slowbuild/test_slow_build.py (`WHEELS`, `_local_pip`, `_offline`, builds with the offline env), docs/testlab.md | tests/stacks/test_graywolf_fetch.py (new, 2), tests/repo/test_build_wheels.py (new, 2), tests/repo/test_mirror_sources.py (+2), testlab/tests/unit/test_slow_build_lane.py (+5) | the four files on a worktree of the parent `45348dbc`: 8 failed, 68 passed, 2 skipped; every red is a new case. Guards green there: the default-URL case and the two budget tests (a/c) |

No CHANGELOG line: test tooling; the product line changes nothing for an operator.

## HOUSE RULES KEPT (dev's tools/check_houserules.py on HEAD~1..HEAD, the P5 commit only)

```
HOUSE RULES KEPT  HEAD~1..HEAD
changelog    +0 bullets (limit 2, each ≤ 3 lines; longest 0), top section 25 lines   OK
docs         1 files, +16 lines; fact "recorded, never judged" -> docs/testlab.md; fact "build-wheels.py" -> docs/testlab.md   OK
tests        9 new tests; layers testlab/tests/slowbuild=testlab testlab/tests/unit=testlab tests/repo=repo tests/stacks=stacks; red-before 1/1   OK
patterns     getsource 0 assert-True 0 source-read 0 sleep 0 startswith 1 network 0   WARN
markers      3 without slow   WARN
attribution  authors makrohard; trailers 0; words 0   OK
modules      no family file touched; family net +0 lines, +0 public   OK
VERDICT      PASS  (hard 0, warn 2)
```

The WARNs, answered:
- **`startswith 1`** is a FILTER inside an assert (`not any(s.startswith("sideband ") for s in want)`), not a prefix assertion on a sentence.
- **`markers 3 without slow`** is the checker's text heuristic ("venv" / "pip install" in a body).
  - Two hits are `test_slow_build_stack` and `test_slow_build_selfupdate`, which are `slow` through the module's `pytestmark = pytest.mark.slow` (test_slow_build.py:44).
  - The third is `test_every_pip_step_of_a_timed_build_is_covered…`, which only reads the manifest (`.venv/bin/pip` strings) and runs in milliseconds.

## What it does

- **Acquisition** (a new step before the lane), each transfer with `backoff` and timed into `slowevidence/transfers.toml`, which the uploaded artifact carries (`path: slowevidence/**`):
  - the mirrors (P3's script and derived list; every clone the lane times is of a mirrored remote);
  - the Graywolf `.deb` + `checksums.txt` at the manifest's pinned version;
  - `pip download` of the CLI venv's requirements, `setuptools>=68` (the build backend) and this checkout, inside the lab image.
- **The budget** (`test_slow_build_budget`) iterates `LANE_OPS` over `EVIDENCE`, which only `_record` fills from the lane's own timings; tests (a)/(c) above pin that a transfer, however slow, leaves its verdict unchanged.
- **Not separated (the ruling):** the graywolf build's `.deb` (v3 section above).
- **Comparability.** Row A (the Zero) times its clones, fetch and pip with the network; row C now times them locally. The budget gets easier for those four operations, never harder. This is decision (a).

## The 6-point block

1. **Contracts.**
   - `graywolf-fetch.sh`: `BASE_URL="${GRAYWOLF_BASE_URL:-https://github.com/chrissnell/graywolf/releases/download}"`; the sha256 check is untouched.
   - `mirror-sources.py graywolf-version|cli-requirements <manifest>`: exit 1 naming the missing step.
   - `_local_pip(env, conf)`: env unchanged without wheels, else `XDG_CONFIG_HOME=<conf>` with the pip.conf.
   - `_offline(env)`: returns env, or fails with pip's config as the runner's child sees it.
2. **Invariants.**
   - The budget judges only lane timings.
   - Every transfer is retried long (c1), never a test.
   - The product's code path is unchanged apart from the URL line; only its sources are local, and the lane proves through the product's runner that git and pip see them.
3. **Failure classes.**
   - An acquisition failing after the backoff: red before the lane.
   - A config not reaching git: `resolved` fails before the tests.
   - A pip.conf not reaching pip: `_offline` fails before the timing.
   - A wheel missing from the directory: pip's "No matching distribution", a named red lane; the gap is in the acquisition.
   - A Graywolf copy with a wrong checksum: the product's own sha256 check.
4. **Test rules.** No source reading, no sleep, no network: curl is a shim, and pip only reads its config. The runner checks run in a fresh interpreter, because `_FIXED_ENV` is built at import.
5. **Directories.**
   - `pytest -q -n 8 tests/repo tests/stacks/test_graywolf_fetch.py testlab/tests/unit/test_slow_build_lane.py` → `663 passed, 3 skipped`.
   - `ruff check lhpc testlab` and `ruff check tests --select F,E9` → `All checks passed!`.
6. **Adversarial self-review.**
   - (a) `XDG_CONFIG_HOME` also moves where other programs in those two operations look for config. lhpc's Python never reads it (`grep`: only backends.py forwards it), and the lab simulates systemctl, so no user unit is looked up there.
   - (b) `_offline` checks with the pytest venv's pip, not the CLI venv's or the helper's. The mechanism (pip's XDG config file) is the same for every pip, and the runner forwards it to all.
   - (c) v1's lane run 38826850523 was dispatched without `release_verify=true`, so slow-build never ran; it proved nothing for P5. v2's run is dispatched with it.

   - (d) Lane evidence for v2: run 38828384068 on 172a8991. `testlab` (incl. `resolved` through the runner) and `release-verify` passed, which is the lane evidence for P3 v4 and P4a. Its slow-build was still in the acquisition step when v3 was built, and v3's own run supersedes it (a dispatch on the branch cancels the older run's remaining job).
   - (e) `build-wheels.py fetch` is proved here only with a stubbed `pip wheel` (composition, pins, dropped options, build requirements); the real fetch and the offline builds are proved by the v3 lane run, not yet available.

## Numbers, measured

| figure | command | output |
|---|---|---|
| diff | `git diff --shortstat HEAD~1 HEAD` | 10 files changed, 539 insertions(+), 10 deletions(-) |
| red-before | the four test files on a worktree of `45348dbc` | `8 failed, 68 passed, 2 skipped` |
| on this commit | same four files | `76 passed, 2 skipped` |
| timed builds' pip steps | `build-wheels.py steps <manifest> <baseline> \| wc -l` | 13 (7 components) |
| runner whitelist | `sed -n 188,203p lhpc/core/probes/backends.py` | HOME, XDG_RUNTIME_DIR, XDG_CONFIG_HOME, … (no GIT_*, no PIP_* but PIP_CACHE_DIR) |
