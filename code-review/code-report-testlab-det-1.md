# Code report: testlab-det-1 (packet P1 = plan items M1 + S1)

Branch `cons/testlab-det-1-r1` on `origin/dev` = `eb9673a4` (0.12.9). Plan: `plans/PLAN-testlab-determinism.md` v3 (gate 1 GREEN in round 4), items M1 and S1. The two packets the plan named P1 (M1) and P5 (S1) are one packet here, by the review order; their only shared file is CHANGELOG.md (one line each).

## Commits

| commit | subject | files | test | red-before |
|---|---|---|---|---|
| b0c4750a | testlab: slow-build times the pinned Graywolf fetch, without the GitHub API | testlab/tests/slowbuild/test_slow_build.py, testlab/tests/slowbuild/conftest.py (docstring only), testlab/tests/unit/test_slow_build_lane.py, docs/test-matrix.md, CHANGELOG.md | `test_the_deb_fetch_times_the_pinned_release_and_asks_github_nothing` (testlab unit layer; rule 10: the service's API check is never called (`svc.asked == []`), the fetch argv ends with `[<pinned>, "--from-upstream"]`, and `FETCHED` holds the pin) | yes, against eb9673a4, the test AS COMMITTED (copied beside the parent's lane, the copy removed afterwards): its service's API check fails as on 10-13, the parent's `_deb_fetch(svc, component, tmp)` calls it and stops with `AssertionError: the upstream release check of graywolf failed: upstream check failed for 'graywolf': network/API error`. The signature is unchanged, so this is the defect's red, not a signature's |
| b379911f | graywolf: the update check keeps curl's exit code and message | lhpc/core/service_maintenance.py, tests/stacks/test_graywolf_upstream.py, CHANGELOG.md | `test_a_failed_check_keeps_curls_exit_code_and_message` (ordinary tests/stacks; rule 2/10: the typed field `data["curl_rc"] == 22`, and curl's `403` is kept in the stored status) | yes, against eb9673a4: `KeyError: 'curl_rc'` |

Commands: red-before = `python -m pytest -q -p no:cacheprovider <copied test> -k <case>` in a worktree of eb9673a4 (the copy removed afterwards; `git status --short` empty). Green = `PYTHONPATH=<worktree>/testlab:<worktree> python -m pytest -q -p no:cacheprovider -n 8 tests/stacks tests/repo testlab/tests/unit` → `1925 passed, 8 skipped, 1 warning in 42.53s` (on b379911f). `ruff check lhpc testlab` → `All checks passed!`. `ruff check tests --select F,E9` → `All checks passed!`.
`PYTHONPATH` is needed because the shared venv's editable `lhpc_testlab` resolves to another checkout. Without it, 33 testlab unit cases fail with `TypeError: pty_readiness() got an unexpected keyword argument 'logs'`, and the same 33 fail on the parent.

## Lane proof (GitHub, on b379911f = the code commits' head, exact sha)

Round 1's runs (38803769860, 38803773861 on 1a9e2310) are superseded: the M1 test and code changed after the auditor's round 5.

| run | workflow | jobs → conclusion |
|---|---|---|
| 38810815611 | testlab.yml (`release_verify=true`: testlab, release-verify, slow-build) | testlab success · release-verify success · slow-build success (`11 passed in 2557.11s`) |
| 38810819791 | ci.yml | guards, meshcore-host, pin-validation, rfdecode-meshtastic, rfdecode-reticulum, test (3.11), test (3.12), test (3.13): all success |

The `inject` input of the plan arrives with P3; this packet's lane proof is the ordinary slow-build case on the pinned version. Its evidence (artifact `slow-build-evidence`): `component = "graywolf"`, `op = "deb-fetch"`, `seconds = 9.7`, `key = "deb:0.14.14"`; summary line `graywolf deb-fetch: E=10 s Z=3 s (fresh) E/Z=3.23 limit=900 s`. The Zero row matches FRESH. The same fetch took 1.7 s on round 1's run: the transfer time varies between runs, which decision (a) separates from the budget in a later packet.

## Simplicity guardrails

- No new abstraction, class, registry or module. M1 reuses `slow_target._fetch_version`, the reader the build key already uses, instead of a second parse of the fetch step. S1 adds three local lines in the one failure branch.
- Lines (measured with `wc -l` before → after): test_slow_build.py 560 → 561; slowbuild/conftest.py 50 → 50; test_slow_build_lane.py 461 → 485; service_maintenance.py 2883 → 2891.
- Dependencies, before → after:
  - `_deb_fetch`: {`svc.graywolf_upstream_check`, `svc.graywolf_upstream_state`, `asset_path`, `subprocess.run`, `_judged`, `_record`} → {`svc.stack`, `_STACK_OF`, `stt._fetch_version`, `asset_path`, `subprocess.run`, `_judged`, `_record`}. The network dependency is gone; the signature, the `svc` fixture and both callers are unchanged.
  - `graywolf_upstream_check`: unchanged (runner, cache helpers).

## Deviations

- The M1 test was added to the existing `testlab/tests/unit/test_slow_build_lane.py`, the module's canonical owner, which already loads the lane by path, not to a new `test_slowbuild_fetch.py` as the plan named.
- The CHANGELOG lines are under `## Unreleased` (the review order's line; docs/maintenance.md), renamed by the release commit.
- The plan's P1 (M1) and P5 (S1) are one packet, by the review order.

## The 6-point block

1. **Contracts.**
   - `graywolf_upstream_check(target) -> ActionResult` (service_maintenance.py:857 ff.). A success is unchanged.
   - When curl exits non-zero: `ok=False`, the summary `upstream check failed for '<target>': network/API error (curl rc <n>[: <last 200 chars of curl's message, whitespace runs → one space>])`, `data={"curl_rc": <int>}`, `next_commands` unchanged.
   - The cache `state/graywolf-upstream.json` keeps its keys (`latest`, `error`, `checked_at`). `error` becomes `could not reach the GitHub releases API (<same why>)`, and `latest` is still the previously known one.
   - `_deb_fetch(svc, component, tmp)` is lane-internal, signature unchanged (two callers: test_slow_build.py:392 and the unit test's path table); `svc` must offer `stack(<id>)`.
2. **Invariants + tests.**
   - "No GET route runs a network call": the check is still reached only from the web's POST route (app.py:1294; curl itself issues a GET to the API), unchanged (`tests/web/test_web.py::test_get_routes_make_no_network_calls`, not touched, green in CI).
   - "A transient failure keeps the last known version": `test_transient_check_preserves_the_last_known_version`, green.
   - No secret can reach the new text: the call carries no token or header beyond `Accept`, so curl's message holds at most the public URL.
3. **Known failure classes.**
   - The fake runner has the real call shape (`run(argv, timeout)`, as the module's other tests). `stderr` is absent or `None` → `""` (`getattr(..., "") or ""`).
   - Same decision everywhere: CLI and web both read `ActionResult`. The web flashes `res.summary` (app.py:1294–1295, Jinja-escaped).
   - Stacked conflicts: none; no other packet of this item touches these files.
4. **Test rules.**
   - Red-before per test: above, and in each commit message.
   - No `startswith` on a decision-bearing string: the M1 test compares argv tokens exactly, and the S1 test asserts a typed field.
   - The S1 test's `"403" in error` checks that curl's own words are kept, not our sentence.
   - No network: the runner and `subprocess` are faked. `_record` is stubbed with a comment, because it refuses outside the throttled box.
5. **Whole directories:**
   - Run: tests/stacks, tests/repo and testlab/tests/unit (counts above), and ruff (both).
   - Signature grep: `grep -rn "_deb_fetch\|graywolf_upstream_check" tests testlab lhpc` gives every caller updated or unchanged in shape.
   - No full suite, no background job. The first directory run exceeded the 2-minute tool timeout and was moved to the background automatically; it was discarded, and the counts above come from the foreground re-run.
6. **Adversarial self-review.**
   - (a) `stt._fetch_version` is a private of lhpc used by the lane. Accepted: making it public would grow the product diff, and the lane already loads `slow_target` internals (`stt.required`, `stt.current_key`).
   - (b) The M1 test derives the expected version from `build_marker` (`.lhpc-built-<version>`), an independent field from the argv the code parses; a manifest whose two disagree fails it.
   - (c) "Only the last 200 characters" can cut a long message mid-word. Accepted: curl's error is one short line.
   - (d) Docs: docs/test-matrix.md:110 was the only sentence that named `<latest>`; `grep -rn "<latest>" docs testlab` → none left.
   - (e) The CHANGELOG goes under `## Unreleased` by the review order's line (docs/maintenance.md: "changes go under a `## Unreleased` changelog heading").

## Precision checklist

1. Absolute words: "nothing else changes" (S1 commit message) is limited to the check's observable outputs listed in point 1.
2. One line: curl's message is flattened (`" ".join(s.split())`).
3. Fault injector: the S1 fake fails exactly the curl call; the case asserts the branch was reached (`curl_rc`).
4. Nothing is moved.
5. No helper swallows anything new.
6. No run is cited on a sha that is not on the branch.

## Numbers, measured

| figure | command | output |
|---|---|---|
| base | `git log --oneline -1 origin/dev` | `eb9673a4 0.12.9` |
| diff | `git diff --shortstat origin/dev b379911f` | 7 files changed, 75 insertions(+), 19 deletions(-) |
| line counts | `git show origin/dev:<f> \| wc -l`; `wc -l <f>` | as in the guardrails |
| local tests | `pytest -q -n 8 testlab/tests/unit tests/repo tests/stacks` on b379911f | 1925 passed, 8 skipped |
| slow-build lane unit file alone | `pytest -q testlab/tests/unit/test_slow_build_lane.py` | 53 passed, 2 skipped |
| graywolf file alone | `pytest -q tests/stacks/test_graywolf_upstream.py` | 19 passed |
| lane runs | `gh run view <id> --json jobs` | as in the lane table |
| slow-build evidence | `gh run download 38803769860 -n slow-build-evidence`; `grep -A5 'op = "deb-fetch"'` | key `deb:0.14.14`, 1.7 s, fresh |
