# PLAN testlab-determinism (v1)

Base `origin/dev` = `eb9673a4` (0.12.9). Scope: `testlab/`, `.github/workflows/testlab.yml`, one product function (S1); a bot change on its own branch later (B1).
Source: the flakiness analysis of 2026-10-15. Of 100 testlab runs, 21 jobs failed. 5 were flakes (1 network, 4 timing); 3 of them stopped bot proves and 1 stopped the 10-13 release.
Rules: tests/README.md rules 1–10 and "Where a new test goes"; no waiver, no lane exclusion, no weakened assertion; red-before for every fix; a flake never freezes a stack.

## 1. The rule (one place: docs/testlab.md, "Running the verification lanes")

> A lane depends on an external service only where that service is what the lane proves.
> - Everything a lane needs from outside (container image, Python packages, upstream sources) is acquired BEFORE the lane, in a workflow step of its own that retries and names what it fetched. The lane then runs from what that step left.
> - What a lane still has to fetch itself (a published binary, a release download, a build step's packages) fails with one `EXTERNAL-UNAVAILABLE <what>: <tool's error line>` line. It is never marked `STACK-REGRESSION` and never retried inside the lane.
> - Readiness is marked `STACK-REGRESSION` only on evidence that the component broke: its process is gone, or it drew its own error. A timeout while the process still runs is UNMARKED and carries the run-log tail.

A lane never retries (run order 6: a test is never retried into green). "Retried once" lives in exactly two places: the acquisition step (`retry.sh`, as for pip today) and the bot's existing one re-run (B1, decision c1).

## 2. Items [packet]

**M1 [P1] slow-build fetches the pinned graywolf.**
- Today: `_deb_fetch` (testlab/tests/slowbuild/test_slow_build.py:210–224) calls `graywolf_upstream_check` (service_maintenance.py:884–893, unauthenticated api.github.com) only to pick "latest". It then times `graywolf-fetch.sh <latest> --from-upstream`.
- What is under test is the fetch time: the timer starts at :218, after the check.
- Change: take the version from the manifest's graywolf fetch step (manifest.example.toml:906, `0.14.14`, read via `svc`'s stack model), keep `--from-upstream`, and drop the check and its assert.
- Risk: `FETCHED` (:217) feeds `stt.current_key(…, fetched=)` (:141), so the budget key changes from "latest" to the pin. Before coding, read the carried evidence keys; if one names a version, the code commit states the new key.
- Red-before: testlab/tests/unit/test_slowbuild_fetch.py (new). A fake check returns ok=False; assert the runner argv carries `0.14.14` (rule 10: an argv token). On the parent this fails with "the upstream release check of graywolf failed".
- Lane: `slow-build`.

**M2 [P2] readiness is marked only on evidence.**
- Today: 12 asserts in test_release_verify.py (:113, 125, 243, 249, 261, 264, 280, 297, 333, 349, 392, 446) put `stack_regression(<s>, 'readiness')` first on a pure timeout. A slow runner can therefore freeze a moved stack (the finding-139 class). `pty_readiness` (release.py:416–509) already follows the rule.
- Change: one function in release.py, `readiness_mark(env, stack, component)`.
  - It returns the marker line only when `lhpc status <stack>` shows the component not running, else "".
  - When unmarked, the message appends the last 1500 characters of the run log.
  - The 12 sites call it instead of the literal marker. Timeouts and checks are unchanged; only the attribution changes.
- Red-before (testlab/tests/unit/test_release_attribution.py):
  - (a) A component alive but never listening gives a message with no line matching the lane's own marker pattern (release.py:61). Red on the parent.
  - (b) Twin: an exited component is marked `phase=readiness`. Green before and after.
- Lane: `release-verify`.

**M3 [P3] sources come from a pre-fetched mirror.**
- Today: every lane clones live (test_chain_roundtrip.py:37–38, 131–132; `install_build` release.py:322; slow-build `_install` :227–231).
- The product runs a plain `git clone <remote>` (lhpc/core/install.py:2600–2690) in the process environment. So git's own `url.<mirror>.insteadOf` redirects it with no product change, as slow-build already does for self-update (test_slow_build.py:474–475).
- `grep -rn GIT_CONFIG lhpc testlab` finds one use: ops.py:656, the lab's own fake sources. lhpc/ has none.
- Change, per job: a step "Acquire upstream sources" before `docker run`.
  - `retry 3 git clone --mirror` of every remote the manifest names, written by a new `.github/scripts/mirror-sources.py` that reads the manifest through `lhpc.core.manifest`.
  - The mirrors and a `gitconfig` of `insteadOf` lines go to `srcmirror/`, mounted read-only; `GIT_CONFIG_GLOBAL` points at the gitconfig.
  - Also: `docker build` under `retry 3`, and Xvfb waits for its socket.
- The real clone code still runs, against a file remote. The pins stay under test: a mirror holds what pin-validation proved reachable.
- Risk: a remote missing from the mirror silently goes to the network. Ruled out by the `--network none` proof below.
- Red-before: test_mirror_sources.py (new). Over a two-stack test manifest, `--gitconfig` emits exactly one `insteadOf` per distinct remote (exact text; rule 2: a persisted format). Red: the script is absent.
- Lane: all three jobs, and the acceptance lane once on `--network none` (Phase 3).

**M4 [P4] what still fetches inside a lane says so.**
- The binary channel (https only, binary_install.py:101–116; reset ops.py:632–649), graywolf's .deb (graywolf-fetch.sh:109, curl `--retry 3`) and build-step pip cannot be served locally without a product change (decision c2).
- Change: `external_unavailable(output) -> str` in release.py.
  - It matches only the product's typed transport lines: `download failed:` (BinaryInstallError), graywolf-fetch's `download failed`, git `Could not resolve host`/`Failed to connect`, pip `ConnectionError`/`ReadTimeoutError`/`HTTP error 5`.
  - On a match it returns `EXTERNAL-UNAVAILABLE <what>: <line>`, which is printed as a `::error title=EXTERNAL-UNAVAILABLE::` annotation and goes first in the message.
  - `install_build` (:322–340) consults it before any mark, so the two never coexist.
- Red-before: a fake `lhpc install` printing `download failed: <urlopen error [Errno -3]>` gives a message that starts with `EXTERNAL-UNAVAILABLE` and has no marker. Red on the parent.
- Lane: `testlab`, `release-verify`.

**S2 [P4] reset no longer hides a failed binary download.**
- Today: `_install_binary_stacks` (ops.py:632–649) notes a failure in `details` and reset returns ok. Meshcom then fails later with an unrelated message (test_chain_roundtrip.py:160–162).
- Change: where the channel is available, a failed binary install fails reset with the M4 line. "Channel unavailable" (x86) stays a skip.
- Red-before: fake `svc.install` ok=False for meshcom → `reset(...).ok is False`.

**S1 [P5, product] the Graywolf check keeps curl's reason.**
- Today: service_maintenance.py:888–893 discards `returncode`/`stderr`.
- Change: the summary and the cache `error` carry `curl rc <n>` and the last 200 characters of stderr, flattened to one line.
- Red-before (tests/install): FakeSystem curl rc 22 with stderr `… 403` → `ActionResult.data["curl_rc"] == 22` (rule 2: a typed field).
- CHANGELOG: "The Graywolf update check now says why GitHub could not be reached (curl's exit code and message)."

**S5 [P6] the browser lane cannot go green having proved nothing.**
- Today: testlab/tests/browser/conftest.py:12–39 skips every case when Chromium is unusable.
- Change: under `CI`, an unusable Chromium ends the pytest run nonzero. Outside CI the skip stays.
- Red-before: `CI=1` with a launcher that raises → nonzero exit.
- Lane: `testlab`.

## 3. The 27 inventory rows

| rows | disposition |
|---|---|
| 1 image+apt · 14 Xvfb | M3 step (`docker build` under retry; Xvfb socket wait) |
| 2 workflow PyPI/Playwright | already safe: acquisition step under `retry 3` |
| 3 reset downloads · 5 chain .deb | S2 + M4 |
| 4 chain clones+pip · 6 release installs | M3 (git) + M4 (pip, binaries) |
| 7 GitHub API · 8 "latest" deb | M1 (+ M4 for the download) |
| 9 budgets include network | decision (a) |
| 10 calibration on runner hardware · 11 sudo/overlay | left: the lane's own proof, fails closed with a reason. Slow-build's failures before 10-13 were missing lane data (baselines, calibration, evidence lines): real, not flakes |
| 12 `cancel-in-progress` | left: the bot proves its own `pins/*` branch; a cancelled run is "unproven" to it |
| 13 browser skip | S5 |
| 15 `free_port` · 16 fixed ports · 21 fixture 30 s bounds | left: 0 failures in 100 runs; each fails loudly with a log tail |
| 17 marked waits · 19 `wait_for` :333 | M2 |
| 18 `pty_readiness` · 20 subprocess timeouts | already safe (146/149; a TimeoutExpired is unmarked) |
| 22 product bounds · 25 power/AX.25 windows · 26 1 Hz timer | under test |
| 23 unit spawn/socket windows | 147 fixed; the rest 0 failures |
| 24 pty 1.5 s cases | left: those cases EXPECT a timeout (`sleep 30`), so load cannot red them |
| 27 finding 106 (ci.yml) | fixed in f15c672b |

## 4. Bot side (B1, lhpc-release-bot, after P3/P4 land)

- Prove requires `testlab` and `release-verify` (bot/cli.py:67) but judges the whole run's conclusion, so slow-build gates releases (decision b).
- An `EXTERNAL-UNAVAILABLE` red is unmarked, so it can never freeze (autofreeze.py:137–140). It only stops the release.
- Smallest change: `RUNNER_LOST` (bot/gh.py:27–28) gets an `EXTERNAL = ("EXTERNAL-UNAVAILABLE",)` tuple, and `_said_runner_lost` (:232–238) accepts either. The existing one-time re-run (:185–230) then repeats such a job once; a second red stays an ordinary unmarked failure.
- Test: tests/test_rerun.py, where an annotation with the word asks for a re-run (red before).

## 5. Decisions for the human gate

- **(a) slow-build budgets include network time.**
  - *Keep (recommended):* a Zero really downloads what it fetches, so the budget must cover that. A budget failure on a slow mirror is then a real "timeout too tight", not a flake. Slow-build keeps live fetches for its timed operations (no mirror there), and a failed fetch says `EXTERNAL-UNAVAILABLE`.
  - *Separate the network share:* slow-build uses the mirror too. That is deterministic, but the Zero budgets would then not cover a real download, and the per-operation network allowance is unmeasured.
- **(b) slow-build gates releases through the whole-run conclusion.**
  - *Add it to `REQUIRED_TESTLAB` deliberately (recommended):* it already blocks (10-13), and naming it also makes "did not run" a refusal.
  - *Exclude it from prove:* a lane exclusion (run order 6), releasing without row C.
  - *Keep as is:* it blocks without being named.
- **(c1) where "retried once" lives.**
  - *Recommended:* only in the acquisition step and the bot's one re-run (B1); never in a lane.
  - *Alternative:* one in-lane retry of an install that failed before any build step, with the reason logged. Cheaper for push runs, but close to "retried into green".
- **(c2) the fetches M4 cannot remove** (binary channel, .deb, build-step pip).
  - *Recommended: attribute now (M4).* Serving them locally needs a lab https server trusted by `_http_get` (a product change at a security boundary) or a per-venv wheelhouse; each is its own item.
  - *Alternative:* do it now, which roughly doubles this item.
- **(c3) release-verify uses the mirror** (pins under test, transport not). *Recommended: yes.* CI `pin-validation` proves each pin is reachable on its branch.

## 6. Packets and landing

| packet | files | lane proof |
|---|---|---|
| P1 M1 | test_slow_build.py; tests/unit/test_slowbuild_fetch.py (new) | slow-build |
| P2 M2 | release.py (`readiness_mark`), test_release_verify.py, test_release_attribution.py | release-verify |
| P3 M3 | testlab.yml; .github/scripts/mirror-sources.py (new); tests/unit/test_mirror_sources.py (new); docs/testlab.md (the rule) | all three + `--network none` acceptance |
| P4 M4+S2 | release.py (`external_unavailable`), ops.py, test_chain_roundtrip.py, tests/unit/test_testlab.py | testlab + release-verify |
| P5 S1 | lhpc/core/service_maintenance.py + its tests/install owner | CI |
| P6 S5 | tests/browser/conftest.py + its unit test | testlab |

- P1, P2, P3, P5 and P6 are file-disjoint and run in parallel. P4 follows P2 (both edit release.py).
- Each packet: branch `cons/testlab-det-<n>-r<round>` → gate-1 file ≤ 90 KB → dev after GREEN + CI.
- Then one full testlab run + CI on the dev sha, and the Phase-3 injections: `--network none` acceptance (M3); the graywolf check stubbed down (M1/S1); a component that never listens (M2).
- CHANGELOG lines go under the version the release fixes (0.12.10 if they land before it is cut). B1 follows P4.

## 7. Numbers, measured

| figure | command | output |
|---|---|---|
| base | `git log --oneline -1 origin/dev` | `eb9673a4 0.12.9` |
| marked readiness sites | `sed -n "<l>,<l+2>p" test_release_verify.py \| grep -c stack_regression` for the 12 lines | ≥ 1 at each of the 12 |
| GIT_CONFIG uses | `grep -rn GIT_CONFIG lhpc testlab` | 1 (testlab/lhpc_testlab/ops.py:656) |
| graywolf pin | `grep -n graywolf-fetch.sh lhpc/data/manifest.example.toml` | :906 `"0.14.14"` |
| history | `gh run list --workflow testlab.yml --limit 100` + per-job tally | 21 failed jobs; 5 flakes |
| bot required jobs | `git show origin/main:bot/cli.py \| grep -n REQUIRED_TESTLAB` | :67 `("testlab", "release-verify")` |
| bot runner-loss lines | `git show origin/main:bot/gh.py \| grep -n "_said_runner_lost\|return any"` | :232, :238 |
