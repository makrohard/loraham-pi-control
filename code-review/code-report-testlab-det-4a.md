# Code report: testlab-det-4a (packet P4a: M4 the EXTERNAL-UNAVAILABLE line + the lane's transfer backoff; S2), v2

Branch `cons/testlab-det-4a-r1`, stacked on P3 (`cons/testlab-det-3-r1` @ 22026f23); rebased onto dev once P3 lands. Plan v4 §1/§2 P4; decisions c1 and c2; the bot contract (the first assertion line `EXTERNAL-UNAVAILABLE what=<token>`, searched, never with a marker).

## v2: the second read's 4 points and 3 notes (all accepted)

1. **REAL: unanchored transfer words.** `download failed` / `unable to access` alone counted as a transfer, so an HTTP 404 (a binary not yet published: publish-before-tag) or a git 403/404 was retried 30 min and reported EXTERNAL.
   - Fixed: a decisive line is a transfer only when it names a network or server CAUSE (resolve, connect refused/reset/timed out, `urlopen error [Errno …]`, ConnectionError/timeouts, HTTP 5xx, curl 5/6/7/28/35/52/56); an HTTP 4xx never is. graywolf-fetch's bare "download failed" is judged by curl's own `curl: (n)` line before it.
   - Red-before vs v1 (155f4e57): binary-404, git-403 and curl-404 fail (EXTERNAL, retried); green after.
2. **CHANGELOG** said the reset "fails at once on a failed binary download", but a failed download is retried. It now reads: fails on a binary download that stays unavailable (after the retries) or is refused.
3. **The readable-log branch** (the lane's normal path) was untested: every build case used a nonexistent log. Two cases now write a real step log:
   - a log ending in a read timeout → EXTERNAL;
   - a log ending in ResolutionImpossible while `lhpc build`'s output carries a timeout → unmarked, 1 attempt.
4. **The install path** judged `lhpc install`'s whole output. A real failing install's output (run 37403409245) is now a case: an `ERR <summary>` line, then numbered advice and `Next:`. The decisive line is the summary's `ERR` line, a refusal → unmarked, 1 attempt. For a clone failure the `[failed] clone … — <git's words>` detail follows the summary and is the last `[failed]`/`ERR` line.
- **N1:** the marker lane's schedule is pinned by an autouse `monkeypatch` fixture, reverted after each case.
- **N2:** the S2 skip case is labelled a control in the commit message.
- **N3:** follows from 4.

## Commits

| commit | files | tests | red-before |
|---|---|---|---|
| 415cdd02 | testlab/lhpc_testlab/release.py (`external_unavailable` :382, `_step_text`, `transfer` :416, `install_build`), testlab/tests/slowbuild/test_slow_build.py (`_judged`), testlab/tests/unit/test_release_attribution.py, testlab/tests/unit/test_slow_build_lane.py, docs/testlab.md | unit layer (13 new): install transfer → EXTERNAL, 3 attempts; recovers → passes, 2 warnings; not a transfer / real refusal / binary-404 / git-403 → 1 attempt, no EXTERNAL; build decisive-line parametrize (read timeout, pip exhausted → EXTERNAL; same-step twin, missing version, unknown → neither); readable log ×2; attributable mixed-log twin → STACK-REGRESSION, 1 attempt; slow-build: EXTERNAL first, other failure none, curl-dns vs curl-404 | vs 22026f23: the transfer cases fail with no EXTERNAL line (4 failed, 5 passed in v1's set); vs 155f4e57 (v1): the three 4xx cases fail |
| fa4b687b | testlab/lhpc_testlab/ops.py (`_install_binary_stacks` → str; `reset` fails on it), testlab/tests/unit/test_testlab.py, CHANGELOG.md | unit layer: a download staying unavailable → `EXTERNAL-UNAVAILABLE what=binary:meshcom`, 3 attempts; refused otherwise → the exact `install meshcom (binary): <summary>`, 1 attempt; no channel → "" (control) | vs 22026f23: the two failure cases fail (the parent swallowed); the skip case is a control |

## HOUSE RULES KEPT (dev's tools/check_houserules.py on 22026f23..fa4b687b)

```
HOUSE RULES KEPT  22026f23..HEAD
changelog    +1 bullets (limit 2, each ≤ 3 lines; longest 3), top section 21 lines   OK
docs         1 files, +6 lines; fact "EXTERNAL-UNAVAILABLE what=" -> docs/testlab.md   OK
tests        13 new tests; layers testlab/tests/slowbuild=testlab testlab/tests/unit=testlab; red-before 1/1   OK
patterns     getsource 0 assert-True 0 source-read 0 sleep 0 startswith 2 network 0   WARN
markers      0 without slow   OK
attribution  authors makrohard; trailers 0; words 0   OK
modules      no family file touched; family net +0 lines, +0 public   OK
VERDICT      PASS  (hard 0, warn 1)
```

The WARN, answered: both are slow-build lane cases.
- `assert not str(red.value).startswith("EXTERNAL-UNAVAILABLE")` is a negative check.
- `assert str(red.value).startswith("EXTERNAL-UNAVAILABLE what=") is external` compares the line's presence with the expected boolean, in both directions.

## The 6-point block

1. **Contracts.**
   - `external_unavailable(what, text) -> str`: `EXTERNAL-UNAVAILABLE what=<what>\n<decisive line (+ curl's cause), one line, ≤ 300 chars>\n` or `""`.
   - `transfer(what, run, *, decisive_text, failed, sleep) -> (result, ext)`: repeats only while `ext`; `decisive_text` returning None means not a transfer (an attributable build step).
   - `_install_binary_stacks(svc, details) -> str`; `reset` fails on a non-empty one.
   - Tokens `install:<stack>`, `build:<stack>`, `binary:<stack>`, `slowbuild:<slug>` match the contract grammar.
2. **Invariants.**
   - Finding 139: an EXTERNAL line never sits beside a marker, and an attributable step is never repeated (the mixed-log twin).
   - No assertion or test body is repeated.
   - An HTTP 4xx is never retried or called external (the three 4xx cases).
3. **Failure classes.** The fakes have the real shapes. A `svc.install` exception stays a reset failure. The marker lane is pinned to one zero-wait retry by a fixture.
4. **Test rules.** Red-before is behavioural (the schedule patches use `raising=False`, so the parent runs its own path); controls are labelled; no network; no real waits.
5. **Directories:** `pytest -q -n 8 testlab/tests/unit tests/repo` → `751 passed, 8 skipped`; ruff ×2 clean.
6. **Adversarial self-review:** v1's four found-and-fixed items stand (loop off-by-one; the fallback text kept LHPC's typed line; the S2 red-before claim corrected; an exact equality instead of `startswith`). v2's points 1–4 are above.

## Numbers, measured

| figure | command | output |
|---|---|---|
| diff | `git diff --shortstat 22026f23 fa4b687b` | 8 files changed, 407 insertions(+), 19 deletions(-) |
| lines | `wc -l` | release.py 734 → 826; test_release_attribution.py 836 → 1023; test_slow_build_lane.py 485 → 514 |
| tests | the pytest command above | 751 passed, 8 skipped |
| 4xx red-before | the committed modules beside 155f4e57 | 3 failed, 4 passed |
