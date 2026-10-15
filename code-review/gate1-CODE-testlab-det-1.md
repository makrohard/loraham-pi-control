# Gate 1: testlab-det-1 (packet P1 = plan items M1 + S1), v3

v3 after the auditor's round 5 (GREEN WITH NOTES, 3 findings): the committed M1 test is now red on the parent THROUGH the API (the signature is unchanged; `svc` supplies the stack model); the report says P1 and P5 share CHANGELOG.md; the S1 message no longer says "POST-only network call". Lanes on the exact code sha b379911f.

**Request.** Judge the two code commits below against the plan's items M1 and S1. That means: correctness; whether each red-before test can fail for the defect named; whether behaviour changes only where the plan says; the docs and CHANGELOG lines; and the report's claims against the diff. The plan passed gate 1 earlier (round 4, GREEN); it is included for reference. Only its items M1 (§2) and S1 (§2) are implemented here.

This file is your whole input: you have no repository access; use no connector, tool or web lookup.

**Answer form.**

| commit | verdict (OK / FINDING) | what |
|---|---|---|

Then numbered findings (file:line, why it matters, smallest fix), then one final line: GREEN / GREEN WITH NOTES / RED.

Redaction: none needed. The diff contains no home path, LAN address, host name or call sign; it is `git diff` output verbatim.

## Commits (full messages, `git log --reverse --format=%B`)
### b0c4750a testlab: slow-build times the pinned Graywolf fetch, without the GitHub API

```text
testlab: slow-build times the pinned Graywolf fetch, without the GitHub API

The slow-build lane's L6 case ("upstream .deb fetch", slow_target.LIMITS) asked the
unauthenticated GitHub releases API for the newest Graywolf release only to choose which
version to time; on 2026-10-13 that call failed ("network/API error") and stopped the bot's
release candidate (testlab run 38338362029). The API is not what L6 measures: the timer starts
after the check, and the check itself is tested in tests/stacks/test_graywolf_upstream.py.

`_deb_fetch(svc, component, tmp)` keeps its signature but now reads the version from the
manifest's fetch step through `svc`'s stack model (slow_target._fetch_version, the reading the
build key already uses) and times `graywolf-fetch.sh <pinned> --from-upstream`; it never asks
the API. The evidence key becomes `deb:<pinned>`, the key of the carried Zero row
(tests/data/slow-target-builds.toml). The row-A recipe in docs/test-matrix.md names the pinned
version; the `svc` fixture's docstring no longer mentions the upstream state.

Red-before: test_the_deb_fetch_times_the_pinned_release_and_asks_github_nothing, as committed,
run against the parent (eb9673a4): its service's upstream check fails as on 10-13, and the
parent's `_deb_fetch` calls it and stops: "AssertionError: the upstream release check of
graywolf failed: upstream check failed for 'graywolf': network/API error". Green after: the
check is never asked (`svc.asked == []`) and the fetch argv ends in [<pinned>,
"--from-upstream"]; testlab/tests/unit/test_slow_build_lane.py 53 passed, 2 skipped.

```

### b379911f graywolf: the update check keeps curl's exit code and message

```text
graywolf: the update check keeps curl's exit code and message

A failed Graywolf upstream check reported only "network/API error" and stored "could not reach
the GitHub releases API": curl's exit code and stderr were dropped, so an unreachable host, a
timeout and an HTTP 403/429 (rate limit) read the same. On 2026-10-13 that left the cause of the
slow-build failure unrecordable.

The result's summary and the kept status (`error` in state/graywolf-upstream.json) now carry
`curl rc <n>` and the last 200 characters of curl's message, flattened to one line; the result
also carries `data["curl_rc"]`. Nothing else changes: the last known `latest` is still kept on a
failed check, and the check is still reached only from the web's POST route
(app.py:1294), never from a GET route; curl itself issues a GET to the API.

Red-before: tests/stacks/test_graywolf_upstream.py::test_a_failed_check_keeps_curls_exit_code_and_message,
run against the parent (eb9673a4) as a copy beside its tests: KeyError on `r.data["curl_rc"]`
(the parent returns `data={}`). Green after: 19 passed (tests/stacks/test_graywolf_upstream.py).

```


## The report

> # Code report: testlab-det-1 (packet P1 = plan items M1 + S1)
>
> Branch `cons/testlab-det-1-r1` on `origin/dev` = `eb9673a4` (0.12.9). Plan: `plans/PLAN-testlab-determinism.md` v3 (gate 1 GREEN in round 4), items M1 and S1. The two packets the plan named P1 (M1) and P5 (S1) are one packet here, by the review order; their only shared file is CHANGELOG.md (one line each).
>
> ## Commits
>
> | commit | subject | files | test | red-before |
> |---|---|---|---|---|
> | b0c4750a | testlab: slow-build times the pinned Graywolf fetch, without the GitHub API | testlab/tests/slowbuild/test_slow_build.py, testlab/tests/slowbuild/conftest.py (docstring only), testlab/tests/unit/test_slow_build_lane.py, docs/test-matrix.md, CHANGELOG.md | `test_the_deb_fetch_times_the_pinned_release_and_asks_github_nothing` (testlab unit layer; rule 10: the service's API check is never called (`svc.asked == []`), the fetch argv ends with `[<pinned>, "--from-upstream"]`, and `FETCHED` holds the pin) | yes, against eb9673a4, the test AS COMMITTED (copied beside the parent's lane, the copy removed afterwards): its service's API check fails as on 10-13, the parent's `_deb_fetch(svc, component, tmp)` calls it and stops with `AssertionError: the upstream release check of graywolf failed: upstream check failed for 'graywolf': network/API error`. The signature is unchanged, so this is the defect's red, not a signature's |
> | b379911f | graywolf: the update check keeps curl's exit code and message | lhpc/core/service_maintenance.py, tests/stacks/test_graywolf_upstream.py, CHANGELOG.md | `test_a_failed_check_keeps_curls_exit_code_and_message` (ordinary tests/stacks; rule 2/10: the typed field `data["curl_rc"] == 22`, and curl's `403` is kept in the stored status) | yes, against eb9673a4: `KeyError: 'curl_rc'` |
>
> Commands: red-before = `python -m pytest -q -p no:cacheprovider <copied test> -k <case>` in a worktree of eb9673a4 (the copy removed afterwards; `git status --short` empty). Green = `PYTHONPATH=<worktree>/testlab:<worktree> python -m pytest -q -p no:cacheprovider -n 8 tests/stacks tests/repo testlab/tests/unit` → `1925 passed, 8 skipped, 1 warning in 42.53s` (on b379911f). `ruff check lhpc testlab` → `All checks passed!`. `ruff check tests --select F,E9` → `All checks passed!`.
> `PYTHONPATH` is needed because the shared venv's editable `lhpc_testlab` resolves to another checkout. Without it, 33 testlab unit cases fail with `TypeError: pty_readiness() got an unexpected keyword argument 'logs'`, and the same 33 fail on the parent.
>
> ## Lane proof (GitHub, on b379911f = the code commits' head, exact sha)
>
> Round 1's runs (38803769860, 38803773861 on 1a9e2310) are superseded: the M1 test and code changed after the auditor's round 5.
>
> | run | workflow | jobs → conclusion |
> |---|---|---|
> | 38810815611 | testlab.yml (`release_verify=true`: testlab, release-verify, slow-build) | testlab success · release-verify success · slow-build success (`11 passed in 2557.11s`) |
> | 38810819791 | ci.yml | guards, meshcore-host, pin-validation, rfdecode-meshtastic, rfdecode-reticulum, test (3.11), test (3.12), test (3.13): all success |
>
> The `inject` input of the plan arrives with P3; this packet's lane proof is the ordinary slow-build case on the pinned version. Its evidence (artifact `slow-build-evidence`): `component = "graywolf"`, `op = "deb-fetch"`, `seconds = 9.7`, `key = "deb:0.14.14"`; summary line `graywolf deb-fetch: E=10 s Z=3 s (fresh) E/Z=3.23 limit=900 s`. The Zero row matches FRESH. The same fetch took 1.7 s on round 1's run: the transfer time varies between runs, which decision (a) separates from the budget in a later packet.
>
> ## Simplicity guardrails
>
> - No new abstraction, class, registry or module. M1 reuses `slow_target._fetch_version`, the reader the build key already uses, instead of a second parse of the fetch step. S1 adds three local lines in the one failure branch.
> - Lines (measured with `wc -l` before → after): test_slow_build.py 560 → 561; slowbuild/conftest.py 50 → 50; test_slow_build_lane.py 461 → 485; service_maintenance.py 2883 → 2891.
> - Dependencies, before → after:
>   - `_deb_fetch`: {`svc.graywolf_upstream_check`, `svc.graywolf_upstream_state`, `asset_path`, `subprocess.run`, `_judged`, `_record`} → {`svc.stack`, `_STACK_OF`, `stt._fetch_version`, `asset_path`, `subprocess.run`, `_judged`, `_record`}. The network dependency is gone; the signature, the `svc` fixture and both callers are unchanged.
>   - `graywolf_upstream_check`: unchanged (runner, cache helpers).
>
> ## Deviations
>
> - The M1 test was added to the existing `testlab/tests/unit/test_slow_build_lane.py`, the module's canonical owner, which already loads the lane by path, not to a new `test_slowbuild_fetch.py` as the plan named.
> - The CHANGELOG lines are under `## Unreleased` (the review order's line; docs/maintenance.md), renamed by the release commit.
> - The plan's P1 (M1) and P5 (S1) are one packet, by the review order.
>
> ## The 6-point block
>
> 1. **Contracts.**
>    - `graywolf_upstream_check(target) -> ActionResult` (service_maintenance.py:857 ff.). A success is unchanged.
>    - When curl exits non-zero: `ok=False`, the summary `upstream check failed for '<target>': network/API error (curl rc <n>[: <last 200 chars of curl's message, whitespace runs → one space>])`, `data={"curl_rc": <int>}`, `next_commands` unchanged.
>    - The cache `state/graywolf-upstream.json` keeps its keys (`latest`, `error`, `checked_at`). `error` becomes `could not reach the GitHub releases API (<same why>)`, and `latest` is still the previously known one.
>    - `_deb_fetch(svc, component, tmp)` is lane-internal, signature unchanged (two callers: test_slow_build.py:392 and the unit test's path table); `svc` must offer `stack(<id>)`.
> 2. **Invariants + tests.**
>    - "No GET route runs a network call": the check is still reached only from the web's POST route (app.py:1294; curl itself issues a GET to the API), unchanged (`tests/web/test_web.py::test_get_routes_make_no_network_calls`, not touched, green in CI).
>    - "A transient failure keeps the last known version": `test_transient_check_preserves_the_last_known_version`, green.
>    - No secret can reach the new text: the call carries no token or header beyond `Accept`, so curl's message holds at most the public URL.
> 3. **Known failure classes.**
>    - The fake runner has the real call shape (`run(argv, timeout)`, as the module's other tests). `stderr` is absent or `None` → `""` (`getattr(..., "") or ""`).
>    - Same decision everywhere: CLI and web both read `ActionResult`. The web flashes `res.summary` (app.py:1294–1295, Jinja-escaped).
>    - Stacked conflicts: none; no other packet of this item touches these files.
> 4. **Test rules.**
>    - Red-before per test: above, and in each commit message.
>    - No `startswith` on a decision-bearing string: the M1 test compares argv tokens exactly, and the S1 test asserts a typed field.
>    - The S1 test's `"403" in error` checks that curl's own words are kept, not our sentence.
>    - No network: the runner and `subprocess` are faked. `_record` is stubbed with a comment, because it refuses outside the throttled box.
> 5. **Whole directories:**
>    - Run: tests/stacks, tests/repo and testlab/tests/unit (counts above), and ruff (both).
>    - Signature grep: `grep -rn "_deb_fetch\|graywolf_upstream_check" tests testlab lhpc` gives every caller updated or unchanged in shape.
>    - No full suite, no background job. The first directory run exceeded the 2-minute tool timeout and was moved to the background automatically; it was discarded, and the counts above come from the foreground re-run.
> 6. **Adversarial self-review.**
>    - (a) `stt._fetch_version` is a private of lhpc used by the lane. Accepted: making it public would grow the product diff, and the lane already loads `slow_target` internals (`stt.required`, `stt.current_key`).
>    - (b) The M1 test derives the expected version from `build_marker` (`.lhpc-built-<version>`), an independent field from the argv the code parses; a manifest whose two disagree fails it.
>    - (c) "Only the last 200 characters" can cut a long message mid-word. Accepted: curl's error is one short line.
>    - (d) Docs: docs/test-matrix.md:110 was the only sentence that named `<latest>`; `grep -rn "<latest>" docs testlab` → none left.
>    - (e) The CHANGELOG goes under `## Unreleased` by the review order's line (docs/maintenance.md: "changes go under a `## Unreleased` changelog heading").
>
> ## Precision checklist
>
> 1. Absolute words: "nothing else changes" (S1 commit message) is limited to the check's observable outputs listed in point 1.
> 2. One line: curl's message is flattened (`" ".join(s.split())`).
> 3. Fault injector: the S1 fake fails exactly the curl call; the case asserts the branch was reached (`curl_rc`).
> 4. Nothing is moved.
> 5. No helper swallows anything new.
> 6. No run is cited on a sha that is not on the branch.
>
> ## Numbers, measured
>
> | figure | command | output |
> |---|---|---|
> | base | `git log --oneline -1 origin/dev` | `eb9673a4 0.12.9` |
> | diff | `git diff --shortstat origin/dev b379911f` | 7 files changed, 75 insertions(+), 19 deletions(-) |
> | line counts | `git show origin/dev:<f> \| wc -l`; `wc -l <f>` | as in the guardrails |
> | local tests | `pytest -q -n 8 testlab/tests/unit tests/repo tests/stacks` on b379911f | 1925 passed, 8 skipped |
> | slow-build lane unit file alone | `pytest -q testlab/tests/unit/test_slow_build_lane.py` | 53 passed, 2 skipped |
> | graywolf file alone | `pytest -q tests/stacks/test_graywolf_upstream.py` | 19 passed |
> | lane runs | `gh run view <id> --json jobs` | as in the lane table |
> | slow-build evidence | `gh run download 38803769860 -n slow-build-evidence`; `grep -A5 'op = "deb-fetch"'` | key `deb:0.14.14`, 1.7 s, fresh |

## The plan (v3, for reference)

````markdown
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
````

## The full diff (`git diff origin/dev b379911f7874778ea7e67aa2e539a6fc9f1a7e9a`)

```diff
diff --git a/CHANGELOG.md b/CHANGELOG.md
index 41b5ae3f..9e41cefe 100644
--- a/CHANGELOG.md
+++ b/CHANGELOG.md
@@ -1,5 +1,15 @@
 # Changelog
 
+## Unreleased
+
+Fixed
+
+- The slow-target release check times the download of the Graywolf release the manifest pins and no longer asks
+  GitHub's API for the newest one, so the API's rate limit can no longer stop a release candidate (the download itself
+  still comes from GitHub).
+- Graywolf's update check now says why GitHub could not be reached: curl's exit code and the end of its message,
+  in the result and in the status it keeps.
+
 ## 0.12.9
 
 Fixed
diff --git a/docs/test-matrix.md b/docs/test-matrix.md
index c943cb37..f165a9b7 100644
--- a/docs/test-matrix.md
+++ b/docs/test-matrix.md
@@ -107,7 +107,7 @@ that runs it on the Zero.
 | `cli-venv` | row 8: the largest `[venv] <n> s` line `lhpc install meshtastic` prints |
 | `selfupdate-helper` | a one-click update from the previous release: `systemctl --user show lhpc-selfupdate.service -p ExecMainStartTimestamp -p ExecMainExitTimestamp`, the difference |
 | `selfupdate-pip` | the `[selfupdate] pip sync <n> s` line of that run in `logs/lhpc-selfupdate.log`; which release has it: see [maintenance](maintenance.md#branches-and-releases), the slow-target build row |
-| `deb-fetch` | `t bash lhpc/data/scripts/graywolf-fetch.sh /tmp/gw <latest> --from-upstream` |
+| `deb-fetch` | `t bash lhpc/data/scripts/graywolf-fetch.sh /tmp/gw <the manifest's graywolf version> --from-upstream` |
 | calibration | `bash testlab/slowbuild/calibrate.sh` (from the installed checkout `<runtime root>/src/loraham-pi-control` with `<runtime root>/state` present, so its work dir is `<runtime root>/state/lhpc-calib` on the SD card; otherwise `$HOME/.cache/lhpc-calib`) prints `cpu= io= mem= workload=`: one `[[calibration]]` entry |
 
 `key` is `pin:<the component's manifest pin_commit>` (the `cli-venv` entry: meshtastic's),
diff --git a/lhpc/core/service_maintenance.py b/lhpc/core/service_maintenance.py
index 2f467326..c769c7c3 100644
--- a/lhpc/core/service_maintenance.py
+++ b/lhpc/core/service_maintenance.py
@@ -886,12 +886,20 @@ class MaintenanceOpsMixin:
         r = self._system.runner.run(
             ["curl", "-fsSL", "--max-time", "12", "-H", "Accept: application/vnd.github+json",
              url], 15.0)
-        if getattr(r, "returncode", 1) != 0:
+        rc = getattr(r, "returncode", 1)
+        if rc != 0:
+            # curl's own reason (exit code + the end of its message, one line) — "unreachable",
+            # "rate-limited" and "timed out" need different answers.
+            why = f"curl rc {rc}"
+            said = " ".join(str(getattr(r, "stderr", "") or "").split())[-200:]
+            if said:
+                why += f": {said}"
             # A transient failure must NOT wipe a previously found version — keep
             # the last known `latest` so the pill/Update button survive a flaky/rate-limited check.
-            _write(_prev_latest(), "could not reach the GitHub releases API")
-            return ActionResult(False, f"upstream check failed for '{target}': network/API error",
-                                next_commands=[f"lhpc status {target}"])
+            _write(_prev_latest(), f"could not reach the GitHub releases API ({why})")
+            return ActionResult(False, f"upstream check failed for '{target}': network/API error "
+                                       f"({why})",
+                                next_commands=[f"lhpc status {target}"], data={"curl_rc": rc})
         try:
             tag = str(_json.loads(r.stdout or "{}").get("tag_name") or "").strip().lstrip("v")
         except ValueError:
diff --git a/testlab/tests/slowbuild/conftest.py b/testlab/tests/slowbuild/conftest.py
index d9dd88f7..96434fc9 100644
--- a/testlab/tests/slowbuild/conftest.py
+++ b/testlab/tests/slowbuild/conftest.py
@@ -39,8 +39,8 @@ def env(lab):
 
 @pytest.fixture(scope="session")
 def svc(lab):
-    """An in-process ControllerService over the SAME lab root: reads the manifest and the
-    graywolf upstream state. Every build and install goes through the real executable."""
+    """An in-process ControllerService over the SAME lab root: reads the manifest. Every build
+    and install goes through the real executable."""
     from lhpc.core.paths import Paths
     from lhpc.core.services import ControllerService
 
diff --git a/testlab/tests/slowbuild/test_slow_build.py b/testlab/tests/slowbuild/test_slow_build.py
index 13ffe925..8b9ccf0f 100644
--- a/testlab/tests/slowbuild/test_slow_build.py
+++ b/testlab/tests/slowbuild/test_slow_build.py
@@ -208,19 +208,20 @@ def _build(env: dict, component: str) -> None:
 
 
 def _deb_fetch(svc, component: str, tmp: Path) -> None:
+    """L6 times the upstream fetch of the release the manifest pins. The version is not asked of
+    the GitHub releases API: that call is not what L6 measures, and its availability is not under
+    test here (the check itself is tested in tests/stacks/test_graywolf_upstream.py)."""
     from lhpc.core.assets import asset_path
-    stack = _STACK_OF[component]
-    chk = svc.graywolf_upstream_check(stack)
-    assert chk.ok, f"the upstream release check of {stack} failed: {chk.summary}"
-    latest = svc.graywolf_upstream_state(stack).get("latest", "")
-    assert latest, f"no upstream release of {stack} is known after the check"
-    FETCHED[component] = latest
+    comp = next(c for c in svc.stack(_STACK_OF[component]).components if c.id == component)
+    version = stt._fetch_version(comp)
+    assert version, f"the manifest names no release for {component}'s fetch step"
+    FETCHED[component] = version
     t0 = time.monotonic()
     r = subprocess.run(["bash", str(asset_path("scripts/graywolf-fetch.sh")), str(tmp / "deb"),
-                        latest, "--from-upstream"], capture_output=True, text=True,
+                        version, "--from-upstream"], capture_output=True, text=True,
                        timeout=HARNESS_S, check=False)
     seconds = time.monotonic() - t0
-    _judged(f"the upstream fetch of {latest}", r)
+    _judged(f"the upstream fetch of {version}", r)
     _record(component, "deb-fetch", seconds)
 
 
diff --git a/testlab/tests/unit/test_slow_build_lane.py b/testlab/tests/unit/test_slow_build_lane.py
index d66bead5..759901b3 100644
--- a/testlab/tests/unit/test_slow_build_lane.py
+++ b/testlab/tests/unit/test_slow_build_lane.py
@@ -41,11 +41,18 @@ def step(monkeypatch):
 
 
 class _Svc:
-    def graywolf_upstream_check(self, stack):
-        return types.SimpleNamespace(ok=True, summary="")
+    """The service the lane reads the manifest through, whose GitHub releases API check fails
+    as it did on 2026-10-13. `asked` records every call of that check."""
+    def __init__(self):
+        self.asked = []
+
+    def stack(self, stack_id):
+        return next(st for st in lane.STACKS if st.id == stack_id)
 
-    def graywolf_upstream_state(self, stack):
-        return {"latest": "1.2.3"}
+    def graywolf_upstream_check(self, stack):
+        self.asked.append(stack)
+        return types.SimpleNamespace(
+            ok=False, summary=f"upstream check failed for '{stack}': network/API error")
 
 
 def _paths(tmp_path):
@@ -85,6 +92,23 @@ def test_a_non_zero_status_fails_every_path(step, path, tmp_path):
         _paths(tmp_path)[path]()
 
 
+def test_the_deb_fetch_times_the_pinned_release_and_asks_github_nothing(monkeypatch, tmp_path):
+    # The release the manifest pins, read from the build marker the fetch step stamps
+    # (`.lhpc-built-<version>`), not from the step argv the lane parses.
+    comp = next(c for st in lane.STACKS for c in st.components if c.id == GRAYWOLF)
+    pinned = comp.build_marker.rsplit("-", 1)[1]
+    argv = []
+    monkeypatch.setattr(lane, "subprocess", types.SimpleNamespace(
+        run=lambda a, **k: argv.append(a) or _done(), CompletedProcess=subprocess.CompletedProcess))
+    # `_record` refuses outside the throttled box; stubbed so the case reaches the fetch.
+    monkeypatch.setattr(lane, "_record", lambda *a, **k: None)
+    svc = _Svc()
+    lane._deb_fetch(svc, GRAYWOLF, tmp_path)
+    assert svc.asked == []
+    assert len(argv) == 1 and argv[0][-2:] == [pinned, "--from-upstream"]
+    assert lane.FETCHED[GRAYWOLF] == pinned
+
+
 @pytest.mark.parametrize("line", ["[fail] x", "[FAILED] x", "  [timeout]",
                                   "[TIMED OUT after 900s — job was KILLED; result is INCOMPLETE]"])
 def test_every_marker_form_is_rejected(line):
diff --git a/tests/stacks/test_graywolf_upstream.py b/tests/stacks/test_graywolf_upstream.py
index c7a12d49..ad1514bb 100644
--- a/tests/stacks/test_graywolf_upstream.py
+++ b/tests/stacks/test_graywolf_upstream.py
@@ -153,6 +153,19 @@ def test_a_rollback_pin_still_surfaces_via_needs_build(tmp_path):
     assert "graywolf" in svc.unbuilt_components("graywolf")   # -> needs_build banner + Build
 
 
+def test_a_failed_check_keeps_curls_exit_code_and_message(tmp_path, monkeypatch):
+    svc = _svc(tmp_path)
+
+    class R:
+        returncode = 22
+        stdout = ""
+        stderr = "curl: (22) The requested URL returned error: 403\n"
+    monkeypatch.setattr(svc._system.runner, "run", lambda a, timeout=None: R())
+    r = svc.graywolf_upstream_check("graywolf")
+    assert r.ok is False and r.data["curl_rc"] == 22
+    assert "403" in svc.graywolf_upstream_state("graywolf")["error"]
+
+
 def test_transient_check_preserves_the_last_known_version(tmp_path, monkeypatch):
     """REVIEW-FOUND #1: a flaky/rate-limited re-check must NOT wipe a previously found update."""
     svc = _svc(tmp_path, installed="0.14.12")
```
