# PLAN testlab-determinism (v3)

Base `origin/dev` = `eb9673a4` (0.12.9). Scope: `testlab/`, `.github/workflows/testlab.yml`, one product function (S1); a bot change only if decision c1 approves it (B1). Rules: tests/README.md rules 1–10 and "Where a new test goes"; no waiver, no lane exclusion, no weakened assertion; red-before for every fix; a flake never freezes a stack.

## Decisions (DECISIONS-testlab-determinism.md, the maintainer, 2026-10-15; they win where this plan differs)

- (a) SEPARATE, cheap: each timed operation records its transfer time as its own number; the budget is judged on the non-network part only (replaces both §5(a) options).
- (b) slow-build joins the bot's required jobs with a GENEROUS budget: a stuck-lane guard, never killing a build that still progresses.
- (c1) supersedes §1's "no retry inside a lane" for TRANSFERS: every transfer (acquisition step, the lane's unavoidable class-B transfers, the bot) is retried long with growing waits (at once, then 1, 2, 5, 10 min … to a generous total); the lane wraps the transfer, never an assertion; a test body is never re-run. B1 becomes: the bot re-runs an EXTERNAL-UNAVAILABLE job after growing waits within its run window, then reports "unproven, external", never a regression.
- (c2) as recommended, plus the long-retry wrapper now. (c3) yes.
- (c4) REMOVE the 146 NomadNet retry AND fix its cause inside P2 (cause from the saved failed-run logs; instrumentation first if they are not enough); M2 keeps a timeout UNMARKED meanwhile.

## 0. Changes

v3 (round-2 findings, all accepted; F3 with added evidence rather than a weaker word):

| finding | change |
|---|---|
| r2-1 decision (a) option 1 breaks class A | §1 class A names the lanes' clones EXCEPT slow-build's; (a) option 1 states that slow-build's clone, CLI-venv and .deb transfers are then class B (live transfer deliberately under test, with no mirror); option 2 makes them class A |
| r2-2 M4 precedence not causally scoped | M4 is rewritten around the DECISIVE step (the one whose failure ended the operation, named by LHPC's own typed line); EXTERNAL only from that step's own output; an attributable decisive step is always marked; a mixed-log twin test (unrelated transport line, attributable failure ⇒ STACK-REGRESSION) and an earlier-step twin (⇒ UNMARKED) |
| r2-3 "same hang" for 37291605525 | kept, with its measured line added: `did not draw anything matching 'Conversations' within 90 s` (§6) |

v2 (round-1 findings, all accepted):

| finding | change |
|---|---|
| 1 the rule contradicts itself (pre-acquired vs in-lane fetch; "never retries" vs `curl --retry`) | §1 now has three exclusive classes. A lane step is never repeated; a product's own transport retry is product behaviour and stays. The existing 146 readiness retry is named and becomes decision (c4) |
| 2 M2 does not cover "drew its own error" | §1 narrowed: for wait-based checks the evidence is "process gone". The "drew an error" evidence stays where it is implemented (`pty_readiness`, `_DREW_AN_ERROR`, release.py:401). Failure of `lhpc status` ⇒ UNMARKED |
| 3 `--network none` scope undefined | M3: the proof is a workflow step, not the lane. It clones every listed remote through the mirror config in a `--network none` container |
| 4 M4: no slow-build proof, no fail-safe | M4 hooks slow-build's `_judged` too, and slow-build is in its lane proof. Precedence: EXTERNAL > marker; unrecognised ⇒ UNMARKED; tests for both (precedence SUPERSEDED by v3 r2-2: the decisive step decides; an attributable step is always marked) |
| 5 S2 has no lane proof | Phase 3 gets a `workflow_dispatch` input `inject` (none / binaries-unreachable / github-api-unreachable). It adds `--add-host` lines, so the testlab job's reset fails with the EXTERNAL line |
| 6 decision (a) framed one-sidedly | §5 (a) states both options with both arguments and no recommendation (the maintainer's call) |
| 7 B1 written as settled | B1 exists only if c1 approves it. If not, a typed external failure stays a red, unmarked failure with no re-run |
| 8 P2/P4 share release.py | merged into one packet P2 (M2+M4+S2). P2 branches from dev only after P1 has landed (both touch test_slow_build.py) |
| 9 classifications beyond the evidence | §6 carries the measured run rows. The "12 sites" claim is shown from the code; rows 10/11 are reworded to what the logs say |
| BOT-STATE delta F3 | M1 also updates `docs/test-matrix.md:110` (row A `<latest>` → the pin). The key risk is resolved: the pinned key `deb:0.14.14` is the Zero row's key (tests/data/slow-target-builds.toml:350–355) |

## 1. The rule (one place: docs/testlab.md, "Running the verification lanes")

> Every external access by a testlab job is in exactly one class.
> **(A) Acquisition, before the lane.** The container image, the lab's Python packages, Chromium, and the upstream git sources the testlab and release-verify lanes clone (as mirrors; slow-build's per decision a). A workflow step of its own does it; it may retry (`retry.sh`) and names what failed.
> **(B) Under test, inside the lane.** What a lane proves needs the real service: the published binaries and the pinned release downloads a stack installs, and a stack's build-step packages. These run exactly as the product runs them, including the product's own transport retries (`graywolf-fetch.sh`'s `curl --retry 3`, pip's own retries), which are product behaviour. The lane never repeats a test, a step or an assertion. A failure that the product's typed output shows to be transport is reported as one `EXTERNAL-UNAVAILABLE <what>: <line>` line, never marked `STACK-REGRESSION`.
> **(C) Everything else is forbidden.** A lane reaches no other external service.
> **Attribution.** `STACK-REGRESSION` needs typed evidence of the component itself: for a wait-based readiness check, its process is gone; for an interactive program (`pty_readiness`), it exited or drew a traceback/exception line; for a build, a failure at a step the recipe declares `attributable`. Without that evidence, a failure, including an unrecognised one, is UNMARKED.

Known exception, open: `pty_readiness` repeats a first start that timed out once (finding 146, release.py:416–509, recorded via `record_property`). That is a lane retry. Decision (c4).

## 2. Items

**M1 [P1] slow-build times the fetch of the pinned graywolf.**
- Today `_deb_fetch` (test_slow_build.py:210–224) calls `graywolf_upstream_check` (service_maintenance.py:884–893, unauthenticated api.github.com) to pick "latest", then times `graywolf-fetch.sh <latest> --from-upstream` from :218.
- The op is "L6 upstream .deb fetch", budgeted on its wall clock (slow_target.py:79–87, :107). The check's behaviour has its owner in tests/stacks/test_graywolf_upstream.py (:62, :156).
- Change: the version comes from the manifest fetch step (manifest.example.toml:906, `0.14.14`). The check goes. `docs/test-matrix.md:110` names the pin.
- Red-before: testlab/tests/unit/test_slowbuild_fetch.py. A fake check ok=False; the runner argv carries `0.14.14`. Parent: "the upstream release check of graywolf failed".
- Lane: slow-build. Phase 3: `inject=github-api-unreachable` stays green.

**M2 [P2] wait-based readiness is marked only when the process is gone.**
- Today, 12 asserts mark a `False` from `wait_tcp`/`wait_http`/`wait_for`, and those return `False` only after their deadline (release.py:350–369). So the mark rides on a pure timeout. The sites in test_release_verify.py: :113, 125, 243, 249, 261, 264, 280, 297, 333, 349, 392, 446.
- Change: one function `readiness_mark(env, stack, component)` in release.py; it returns the marker only when `lhpc status <stack>` reports that component stopped/failed; a status call that fails or cannot be parsed returns "" (UNMARKED); the unmarked message appends the run-log tail (1500 chars). Timeouts and checks are unchanged.
- Red-before (test_release_attribution.py): (a) An alive, never-listening fake component gives no marker line (pattern release.py:61). Red on the parent; (b) An exited component is marked. That is the twin, green on both; (c) A failing status gives no marker.
- Lane: release-verify (positive path); the unit lane in the testlab job (the three cases, real processes).

**M3 [P3] the lanes clone from a pre-fetched mirror.**
- The product's `git clone <remote>` (lhpc/core/install.py:2600–2690) honours git's `url.<m>.insteadOf`, as slow-build already uses (test_slow_build.py:474–475).
- `grep -rn GIT_CONFIG lhpc testlab`: one use, ops.py:656 (the lab's fake sources only).
- Change: a step "Acquire upstream sources" in the testlab and release-verify jobs; `retry 3 git clone --mirror` of the components the lanes install, listed in `.github/scripts/mirror-sources.py` and derived from the manifest by component id. That is 14 remotes, measured from green run 37426359349's adopt logs; a `gitconfig` of `insteadOf` lines; the lane exports `GIT_CONFIG_GLOBAL`; `docker build` goes under `retry 3`, and Xvfb waits for `/tmp/.X11-unix/X99`.
- The `--network none` proof is a separate step after acquisition. A container with no network runs `git clone --no-checkout <remote>` for each listed remote with that gitconfig. It is green only if every clone resolves locally. It proves mirror completeness for the list, not the lane.
- A unit test pins the list to the components the testlab and release-verify lanes install. A new stack that is not listed fails it.
- Red-before (test_mirror_sources.py): over a two-stack test manifest, the gitconfig text is exact (rule 2). Red: the script is absent.
- Slow-build uses the mirror only if decision (a) says so.

**M4 [P2] class-B transport failures say so; unknown stays unmarked.**
- Fetches inside lanes: the binary channel (https only, binary_install.py:101–116; reset ops.py:632–649), graywolf's .deb (graywolf-fetch.sh:109), and build-step pip.
- Change: `external_unavailable(output) -> str` in release.py; it matches only typed product lines: `download failed:` (BinaryInstallError); graywolf-fetch `download failed`; git `Could not resolve host` / `Failed to connect`; pip `ConnectionError` / `ReadTimeoutError` / `HTTP error 5`; it is printed as a `::error title=EXTERNAL-UNAVAILABLE::` annotation and goes first in the message.
- The DECISIVE step is the one whose failure ended the operation, as LHPC itself names it; the classifier reads only that step's own text:
  - install: the failing action's own line(s), i.e. `[failed] …` / `ERR …` lines of `lhpc install` (e.g. `[failed] clone … — <git's words>`, `Binary install … download failed: …`), never the rest of the output;
  - build: the step log named by LHPC's typed line `[failed] build <comp> (rc N, log <path>)` (`TYPED_BUILD_FAILURE`, lhpc/core/build_regression.py:29; a build stops at its first failing step). If that step is `attributable` ⇒ STACK-REGRESSION, unchanged, whatever any other text says (an attributable step only compiles/patches what earlier steps fetched, release.py docstring :289–299). Otherwise EXTERNAL iff that step log's tail (last 40 lines) has a typed transport line, else UNMARKED;
  - slow-build `_judged` (test_slow_build.py:105–109): the one timed command; it never marks; EXTERNAL iff its own `[failed]`/`ERR`/`download failed` line matches;
  - readiness: never EXTERNAL (no fetch).
- Safety property: EXTERNAL never removes a marker that the existing rules give; the EXTERNAL/UNMARKED difference is diagnostic (both unmarked, neither freezes).
- Red-before / twins (test_release_attribution.py and test_slowbuild_fetch.py):
  - (a) install whose `[failed]` line says `download failed: <urlopen error [Errno -3]>` ⇒ message starts `EXTERNAL-UNAVAILABLE`, no marker (red on the parent);
  - (b) MIXED-LOG TWIN: decisive attributable step fails with a compile error while the output and an earlier step log carry `ConnectionError` ⇒ STACK-REGRESSION, no EXTERNAL (green before and after: the guard);
  - (c) decisive non-attributable pip step whose log ends in `ReadTimeoutError` ⇒ EXTERNAL, no marker (red on the parent);
  - (d) EARLIER-STEP TWIN: a transport line only in an earlier, successful step's log, decisive non-attributable step fails with `ResolutionImpossible` ⇒ UNMARKED, no EXTERNAL;
  - (e) unknown error text ⇒ neither line;
  - (f) slow-build: a timed `graywolf-fetch.sh` failing with `download failed` ⇒ EXTERNAL (red on the parent).
- Lanes: testlab, release-verify, slow-build.

**S2 [P2] reset no longer hides a failed binary download.**
- Today `_install_binary_stacks` (ops.py:632–649) notes the failure and reset returns ok. Meshcom then fails later with an unrelated message (test_chain_roundtrip.py:160–162).
- Change: where the channel is available, a failed binary install fails reset with the M4 line. "Unavailable" (x86) stays a skip.
- Red-before: fake `svc.install` ok=False ⇒ `reset(...).ok is False`.
- Lane: testlab green path. Phase 3: `inject=binaries-unreachable` ⇒ the testlab job is red with `EXTERNAL-UNAVAILABLE binary meshcom` and no marker.

**S1 [P5, product] the Graywolf check keeps curl's reason.**
- service_maintenance.py:888–893: the summary and cache `error` carry `curl rc <n>` plus the last 200 chars of stderr, one line.
- Red-before (tests/stacks/test_graywolf_upstream.py, the owner): FakeSystem curl rc 22 ⇒ `data["curl_rc"] == 22`.
- CHANGELOG: "The Graywolf update check now says why GitHub could not be reached (curl's exit code and message)."

**S5 [P6] the browser lane cannot go green having proved nothing.**
- testlab/tests/browser/conftest.py:12–39: under `CI`, an unusable Chromium ends pytest nonzero; outside CI the skip stays.
- Red-before: `CI=1` and a raising launcher ⇒ nonzero.
- Lane: testlab.

## 3. The 27 inventory rows (analysis §2)

| rows | disposition |
|---|---|
| 1 image+apt · 2 lab pip/Chromium · 14 Xvfb | class A (M3 step: retry; Xvfb socket wait) |
| 3 reset downloads · 5 chain .deb · 6 release installs (binaries, .deb, pip) | class B → M4 (+ S2) |
| 4 chain clones · 6 release clones | class A → M3 |
| 7 GitHub API · 8 "latest" .deb | M1 (the .deb becomes a pinned class-B fetch) |
| 9 budgets include network | decision (a) |
| 10 calibration on runner hardware · 11 sudo/overlay | left: the lane's own proof, fails closed with a reason. The six slow-build failures before 10-13 (§6) report a non-positive measurement, missing baseline/evidence entries or a missing `[venv]` line, not a network or timing error |
| 12 `cancel-in-progress` | left: the bot proves its own `pins/*` branch; a cancelled run is "unproven" to it |
| 13 browser skip | S5 |
| 15 `free_port` · 16 fixed ports · 21 fixture 30 s bounds | left: 0 failures in the 100 runs; each fails loudly with a log tail |
| 17 marked waits · 19 `wait_for` :333 | M2 |
| 18 `pty_readiness` | evidence rule kept; its retry → decision (c4) |
| 20 subprocess timeouts | already unmarked (a TimeoutExpired carries no marker) |
| 22 product bounds · 25 power/AX.25 windows · 26 1 Hz timer | under test |
| 23 unit spawn/socket windows | 147 fixed; the rest 0 failures |
| 24 pty 1.5 s cases | left: those cases expect a timeout (`sleep 30`), so load cannot red them |
| 27 finding 106 (ci.yml) | fixed in f15c672b |

## 4. Bot side (B1): only if c1 approves it

- Facts: prove requires `testlab` and `release-verify` (bot/cli.py:67) and judges the whole run's conclusion. An EXTERNAL red is unmarked, so it cannot freeze (autofreeze.py:137–140).
- If c1 = approved: `RUNNER_LOST` (bot/gh.py:27–28) gains `"EXTERNAL-UNAVAILABLE"` via a second tuple, and `_said_runner_lost` (:232–238) accepts it. The existing once-per-run re-run (:185–230) then repeats the job once; the second attempt is judged as it is. Test: tests/test_rerun.py (red before).
- If c1 = rejected: no B1. An EXTERNAL red stops the release unmarked, and a person re-runs it.

## 5. Decisions for the human gate

- **(a) slow-build budgets and network time.** Measured facts: The budgeted limits L5 (CLI venv), L6 (upstream .deb) and L7a (clone) are the product's own timeouts, which on a box include the transfer (slow_target.py:79–87, :102–110); the rule fails when `limit < 2 × max(Zero, past CI, this run)`; the runner's network is a datacentre's, not a Zero's Wi-Fi; *Option 1, keep live transfers in slow-build* (slow-build's clone, CLI-venv and .deb transfers are then class B: live transfer deliberately under test, no mirror there): the timeout a box uses really has to cover a download, so the transfer is part of what the budget protects. The auditor's argument against: a slow but successful download can fail the budget without any EXTERNAL line, a release-gating red that this plan cannot make deterministic; *Option 2, slow-build times local work only* (those transfers become class A): it uses the mirror, and pre-acquired .deb/wheels (class A) where possible. That is deterministic, but the Zero budgets then cover no real download, and a per-op network allowance would be new, unmeasured data.
- **(b) slow-build as a release gate.** *Name it in `REQUIRED_TESTLAB` (recommended):* it already blocks via the whole-run conclusion (10-13). *Exclude it:* a lane exclusion. *Keep:* it blocks unnamed.
- **(c1) the bot re-runs a job that said EXTERNAL-UNAVAILABLE once (B1).**; *Approve:* fewer releases stopped by a passing outage. But it is a whole-job retry that can turn a run green; *Reject:* an EXTERNAL red stays red until a person re-runs it.
- **(c2) class-B fetches stay in the lanes** (binaries, pinned .deb, build-step pip). *Recommended: yes for now.* Serving them locally needs a lab https server trusted by `_http_get` (a security-boundary change) or a per-venv wheelhouse, each its own item.
- **(c3) release-verify clones from the mirror.** *Recommended: yes.* The pins stay under test; CI `pin-validation` proves reachability.
- **(c4) the 146 readiness retry** (one repeat of a NomadNet first start that timed out, decided in the last run to unblock the bot); *Keep as the one named exception:* the 146 root cause (likely `rns.get_interface_stats` with no timeout) is unproven; *Remove:* a first-start hang fails, unmarked; it fits run order 6 strictly, at the cost of the bot proves it rescued on 10-06.

## 6. Packets, landing, and the measured evidence

| packet | files | lane proof |
|---|---|---|
| P1 M1 | test_slow_build.py (`_deb_fetch`); tests/unit/test_slowbuild_fetch.py (new); docs/test-matrix.md:110 | slow-build |
| P2 M2+M4+S2 (after P1 has landed; branched from that dev sha) | release.py, ops.py, test_release_verify.py, test_slow_build.py (`_judged`), test_chain_roundtrip.py, test_release_attribution.py, tests/unit/test_testlab.py | all three + `inject=binaries-unreachable` |
| P3 M3 + `inject` input | testlab.yml; .github/scripts/mirror-sources.py (new); tests/unit/test_mirror_sources.py (new); docs/testlab.md (the rule) | testlab, release-verify, the `--network none` step |
| P5 S1 | lhpc/core/service_maintenance.py; tests/stacks/test_graywolf_upstream.py | CI |
| P6 S5 | tests/browser/conftest.py + its unit test | testlab |

- P1, P3, P5 and P6 are file-disjoint and run in parallel; P2 follows P1. Each packet: `cons/testlab-det-<n>-r<round>` → gate-1 file ≤ 90 KB → dev after GREEN + CI.
- Phase 3: one full testlab run + CI on the dev sha, plus the `inject` runs (github-api-unreachable: slow-build green; binaries-unreachable: an EXTERNAL red, no marker).
- CHANGELOG lines go under the version the release fixes. B1 only if c1 approves it.

**Measured (logs: `gh run view <id> --log-failed`; history `gh run list --workflow testlab.yml --limit 100`, 2026-10-01T01:45Z → 10-13).**

| run | job | decisive line | reading |
|---|---|---|---|
| 38338362029 | slow-build | `upstream check failed for 'graywolf': network/API error` | external dependency, cause unrecorded |
| 37410182407 | testlab | `test_check_fails_when_the_aprs_is_sink_is_down … assert (False is True)` | spawn race (147), fixed 31ac0844 |
| 37401912454, 37395375156 | release-verify | `did not draw anything matching 'Conversations' within 90 s` | first-start hang (146): the retry |
| 37291605525 | release-verify | `STACK-REGRESSION stack=reticulum phase=readiness` + `did not draw anything matching 'Conversations' within 90 s` | same hang, marked (finding 139) |
| 37145791526 | slow-build | `['seconds must be a positive number']` | lane defect at introduction |
| 37148794385, 37162748975, 37261689237, 37275585327 | slow-build | `N budget failure(s): no row C evidence … / no Zero baseline …` | missing lane data |
| 37239771769 | slow-build | ``no `[venv] <n> s` line from the binary install`` | missing evidence line |

Other figures: 21 failed jobs (job tally over the 100 runs); 12 marked readiness sites (`sed -n "<l>,<l+2>p" test_release_verify.py \| grep -c stack_regression` ≥ 1 at each); `GIT_CONFIG` uses: 1 (ops.py:656); 14 cloned remotes (`grep -rhoE '\[clone\] [^ ]+ from [^ ]+'` over run 37426359349's artifacts); bot `REQUIRED_TESTLAB` at cli.py:67, `_said_runner_lost` at gh.py:232.
