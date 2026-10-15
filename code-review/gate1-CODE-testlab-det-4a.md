# Gate 1: testlab-det-4a (EXTERNAL-UNAVAILABLE + transfer backoff, S2), v2

**Request.** Judge the two code commits below against plan v4 §2 P4 (M4: a lane's failed transfer is retried with the transfer backoff and reported with the first line `EXTERNAL-UNAVAILABLE what=<token>`, judged by the DECISIVE step's terminal error line; S2: the lab reset fails on a failed binary download) and the contract with the release bot. Judge above all: can a flake still be marked STACK-REGRESSION; can a real stack failure be retried or reported external (the mixed-log and same-step twins); is anything but a transfer ever repeated; the pip-exhausted deviation; whether each red-before fails through the behaviour; and the report against the diff. The packet is stacked on P3 (the mirror), which is at gate 1 in parallel; its diff base is P3's head.

This file is your whole input: you have no repository access; use no connector, tool or web lookup.

**Answer form.**

| commit | verdict (OK / FINDING) | what |
|---|---|---|

Then numbered findings (file:line, why it matters, smallest fix), then one final line: GREEN / GREEN WITH NOTES / RED.

Redaction: none needed; the diff is `git diff` output verbatim.

## Commits (full messages, `git log --reverse --format=%B`)

### 415cdd02 testlab: a lane's failed transfer is retried with the backoff and reported EXTERNAL-UNAVAILABLE

```text
testlab: a lane's failed transfer is retried with the backoff and reported EXTERNAL-UNAVAILABLE

The release lane's installs and builds fetch what the product fetches (a published binary, a
pinned release download, a build step's packages). A transfer outage there failed the case
UNMARKED with the same words as any other failure, and nothing retried it, so a passing GitHub
or PyPI hiccup stopped a release candidate (plan v4 §2 P4, M4; the maintainer's decision c1).

- `external_unavailable(what, text)`: the step's DECISIVE line is the last error line its own
  output ends with (`[failed]`, `ERR`, `ERROR:`, `error:`, `fatal:`, `curl: (n)`, `download
  failed`). It is a transfer only when that line names a NETWORK or SERVER cause (a name that does not
  resolve, a connection refused/reset/timed out, ConnectionError, read/connect timeouts,
  `urlopen error [Errno …]`, HTTP 5xx, curl 5/6/7/28/35/52/56); an HTTP 4xx (a binary not yet
  published, a repository that is gone) never is. graywolf-fetch's bare "download failed" is
  judged by curl's own `curl: (n)` line before it. Or the line is pip's "Could not find a version that
  satisfies the requirement" AFTER pip's retries ran out on a connection error (pip's outage
  shape; a version that does not exist has no exhausted retry). A transfer error the step
  recovered from earlier does not count. The result is the contract's first line,
  `EXTERNAL-UNAVAILABLE what=<token>`, then the decisive line.
- `transfer(what, run, …)`: repeats `run()` ONLY while it fails on a transfer, with the backoff
  (at once, then 1, 2, 5, 10 min, then every 10 min within 30 min; each wait warned); any other
  failure and a success return at once.
- `install_build` runs the install and the build through it. A build typed at a step the recipe
  declares its own is never a transfer: it keeps its STACK-REGRESSION marker and is not repeated
  (the mixed-log twin). Otherwise the failed step's own log decides, else `lhpc build`'s output
  without LHPC's `[failed] build` summary line.
- slow-build's `_judged` leads a failed step's message with the EXTERNAL line (no retry there:
  P5 moves the timed transfers out of the lane).
- docs/testlab.md: the lane's transfers and the EXTERNAL line, in "What a CI lane may fetch".

Deviation from plan v4: the pip-exhausted rule is added. pip's real outage ends with "Could not
find a version …", which the transport words alone would leave UNMARKED and unretried.

Red-before (testlab/tests/unit/test_release_attribution.py, the test module as committed, run
against the parent 22026f23): 4 failed, 5 passed. The install-transfer case fails ("installing
kiss failed … UNMARKED", no EXTERNAL line); the recovers-after-backoff case fails at the first
install; the two external build cases (read timeout; pip retries exhausted) fail with no EXTERNAL
line. The 5 that pass are the controls and guards: not a transfer, the same-step twin
(ResolutionImpossible after a recovered ConnectionError), a missing version, unknown text, and the
attributable mixed-log twin. The slow-build cases (test_slow_build_lane.py) are new: the parent
has no EXTERNAL line. Green after: testlab/tests/unit + tests/repo 744 passed, 8 skipped.

Second read (4 points): the transfer words were unanchored, so an HTTP 404 (publish-before-tag)
or a git 403 was retried 30 min and reported EXTERNAL; now a cause is required. Red-before against
the first version (155f4e57): the binary-404, git-403 and curl-404 cases fail (EXTERNAL, retried);
green after. New coverage: the readable-log branch (a real tmp log decides; a timeout elsewhere in
`lhpc build`'s output does not) and a real failing install's output (run 37403409245: refusal,
not repeated). The marker lane's schedule is pinned by an autouse monkeypatch fixture.

```

### fa4b687b testlab: the lab reset fails on a failed binary download instead of hiding it

```text
testlab: the lab reset fails on a failed binary download instead of hiding it

`_install_binary_stacks` noted a failed binary install in the reset's details and the reset still
returned ok, so a failed GitHub release download surfaced later as an unrelated meshcom start
failure (test_chain_roundtrip.py; plan v4 §2 P4, S2).

Where the binary channel is available (aarch64), each binary install now runs through
`release.transfer`: a failed download is retried with the transfer backoff, and an install that
still fails is returned to `reset`, which fails with it: first line `EXTERNAL-UNAVAILABLE
what=binary:<stack>` when it was the transfer, else "install <stack> (binary): <summary>" at once.
An unavailable channel (x86) stays a skip, as before.

Red-before (testlab/tests/unit/test_testlab.py, as committed, against the parent 22026f23): the
two failure cases fail (the parent's function returns None: the failure was swallowed). The skip
case is a CONTROL: the parent skipped too and fails it only on the new return value ("" vs None). Green after: testlab/tests/unit + tests/repo
744 passed, 8 skipped.

```

## The report

> # Code report: testlab-det-4a (packet P4a: M4 the EXTERNAL-UNAVAILABLE line + the lane's transfer backoff; S2), v2
>
> Branch `cons/testlab-det-4a-r1`, stacked on P3 (`cons/testlab-det-3-r1` @ 22026f23); rebased onto dev once P3 lands. Plan v4 §1/§2 P4; decisions c1 and c2; the bot contract (the first assertion line `EXTERNAL-UNAVAILABLE what=<token>`, searched, never with a marker).
>
> ## v2: the second read's 4 points and 3 notes (all accepted)
>
> 1. **REAL: unanchored transfer words.** `download failed` / `unable to access` alone counted as a transfer, so an HTTP 404 (a binary not yet published: publish-before-tag) or a git 403/404 was retried 30 min and reported EXTERNAL.
>    - Fixed: a decisive line is a transfer only when it names a network or server CAUSE (resolve, connect refused/reset/timed out, `urlopen error [Errno …]`, ConnectionError/timeouts, HTTP 5xx, curl 5/6/7/28/35/52/56); an HTTP 4xx never is. graywolf-fetch's bare "download failed" is judged by curl's own `curl: (n)` line before it.
>    - Red-before vs v1 (155f4e57): binary-404, git-403 and curl-404 fail (EXTERNAL, retried); green after.
> 2. **CHANGELOG** said the reset "fails at once on a failed binary download", but a failed download is retried. It now reads: fails on a binary download that stays unavailable (after the retries) or is refused.
> 3. **The readable-log branch** (the lane's normal path) was untested: every build case used a nonexistent log. Two cases now write a real step log:
>    - a log ending in a read timeout → EXTERNAL;
>    - a log ending in ResolutionImpossible while `lhpc build`'s output carries a timeout → unmarked, 1 attempt.
> 4. **The install path** judged `lhpc install`'s whole output. A real failing install's output (run 37403409245) is now a case: an `ERR <summary>` line, then numbered advice and `Next:`. The decisive line is the summary's `ERR` line, a refusal → unmarked, 1 attempt. For a clone failure the `[failed] clone … — <git's words>` detail follows the summary and is the last `[failed]`/`ERR` line.
> - **N1:** the marker lane's schedule is pinned by an autouse `monkeypatch` fixture, reverted after each case.
> - **N2:** the S2 skip case is labelled a control in the commit message.
> - **N3:** follows from 4.
>
> ## Commits
>
> | commit | files | tests | red-before |
> |---|---|---|---|
> | 415cdd02 | testlab/lhpc_testlab/release.py (`external_unavailable` :382, `_step_text`, `transfer` :416, `install_build`), testlab/tests/slowbuild/test_slow_build.py (`_judged`), testlab/tests/unit/test_release_attribution.py, testlab/tests/unit/test_slow_build_lane.py, docs/testlab.md | unit layer (13 new): install transfer → EXTERNAL, 3 attempts; recovers → passes, 2 warnings; not a transfer / real refusal / binary-404 / git-403 → 1 attempt, no EXTERNAL; build decisive-line parametrize (read timeout, pip exhausted → EXTERNAL; same-step twin, missing version, unknown → neither); readable log ×2; attributable mixed-log twin → STACK-REGRESSION, 1 attempt; slow-build: EXTERNAL first, other failure none, curl-dns vs curl-404 | vs 22026f23: the transfer cases fail with no EXTERNAL line (4 failed, 5 passed in v1's set); vs 155f4e57 (v1): the three 4xx cases fail |
> | fa4b687b | testlab/lhpc_testlab/ops.py (`_install_binary_stacks` → str; `reset` fails on it), testlab/tests/unit/test_testlab.py, CHANGELOG.md | unit layer: a download staying unavailable → `EXTERNAL-UNAVAILABLE what=binary:meshcom`, 3 attempts; refused otherwise → the exact `install meshcom (binary): <summary>`, 1 attempt; no channel → "" (control) | vs 22026f23: the two failure cases fail (the parent swallowed); the skip case is a control |
>
> ## HOUSE RULES KEPT (dev's tools/check_houserules.py on 22026f23..fa4b687b)
>
> ```
> HOUSE RULES KEPT  22026f23..HEAD
> changelog    +1 bullets (limit 2, each ≤ 3 lines; longest 3), top section 21 lines   OK
> docs         1 files, +6 lines; fact "EXTERNAL-UNAVAILABLE what=" -> docs/testlab.md   OK
> tests        13 new tests; layers testlab/tests/slowbuild=testlab testlab/tests/unit=testlab; red-before 1/1   OK
> patterns     getsource 0 assert-True 0 source-read 0 sleep 0 startswith 2 network 0   WARN
> markers      0 without slow   OK
> attribution  authors makrohard; trailers 0; words 0   OK
> modules      no family file touched; family net +0 lines, +0 public   OK
> VERDICT      PASS  (hard 0, warn 1)
> ```
>
> The WARN, answered: both are slow-build lane cases.
> - `assert not str(red.value).startswith("EXTERNAL-UNAVAILABLE")` is a negative check.
> - `assert str(red.value).startswith("EXTERNAL-UNAVAILABLE what=") is external` compares the line's presence with the expected boolean, in both directions.
>
> ## The 6-point block
>
> 1. **Contracts.**
>    - `external_unavailable(what, text) -> str`: `EXTERNAL-UNAVAILABLE what=<what>\n<decisive line (+ curl's cause), one line, ≤ 300 chars>\n` or `""`.
>    - `transfer(what, run, *, decisive_text, failed, sleep) -> (result, ext)`: repeats only while `ext`; `decisive_text` returning None means not a transfer (an attributable build step).
>    - `_install_binary_stacks(svc, details) -> str`; `reset` fails on a non-empty one.
>    - Tokens `install:<stack>`, `build:<stack>`, `binary:<stack>`, `slowbuild:<slug>` match the contract grammar.
> 2. **Invariants.**
>    - Finding 139: an EXTERNAL line never sits beside a marker, and an attributable step is never repeated (the mixed-log twin).
>    - No assertion or test body is repeated.
>    - An HTTP 4xx is never retried or called external (the three 4xx cases).
> 3. **Failure classes.** The fakes have the real shapes. A `svc.install` exception stays a reset failure. The marker lane is pinned to one zero-wait retry by a fixture.
> 4. **Test rules.** Red-before is behavioural (the schedule patches use `raising=False`, so the parent runs its own path); controls are labelled; no network; no real waits.
> 5. **Directories:** `pytest -q -n 8 testlab/tests/unit tests/repo` → `751 passed, 8 skipped`; ruff ×2 clean.
> 6. **Adversarial self-review:** v1's four found-and-fixed items stand (loop off-by-one; the fallback text kept LHPC's typed line; the S2 red-before claim corrected; an exact equality instead of `startswith`). v2's points 1–4 are above.
>
> ## Numbers, measured
>
> | figure | command | output |
> |---|---|---|
> | diff | `git diff --shortstat 22026f23 fa4b687b` | 8 files changed, 407 insertions(+), 19 deletions(-) |
> | lines | `wc -l` | release.py 734 → 826; test_release_attribution.py 836 → 1023; test_slow_build_lane.py 485 → 514 |
> | tests | the pytest command above | 751 passed, 8 skipped |
> | 4xx red-before | the committed modules beside 155f4e57 | 3 failed, 4 passed |

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

## The full diff (`git diff 22026f2372a84e6637ec106e4e5893f9f452c504 fa4b687b`)

```diff
diff --git a/CHANGELOG.md b/CHANGELOG.md
index 7336482c..42d17e1a 100644
--- a/CHANGELOG.md
+++ b/CHANGELOG.md
@@ -4,6 +4,9 @@
 
 Fixed
 
+- The release check retries a download that fails on the network inside its lanes for up to 30 minutes and reports
+  a lasting outage as external, never as a stack's regression; the lab's reset now fails on a binary download that
+  stays unavailable or is refused, instead of a later start failing for no stated reason.
 - The GPS sky view no longer alternates with "no satellites reported" when gpsd reports the satellites only
   every few seconds: the console keeps each receiver's last satellite list for up to 20 s.
 - The test lab's testlab and release-verify lanes clone upstream sources from mirrors fetched before the run, and
diff --git a/docs/testlab.md b/docs/testlab.md
index 41239185..7939d9ca 100644
--- a/docs/testlab.md
+++ b/docs/testlab.md
@@ -123,7 +123,12 @@ Chromium at the start of the lane's step. The sources are git mirrors
 (`.github/scripts/mirror-sources.py`): the lane exports `GIT_CONFIG_GLOBAL` with one `insteadOf`
 per remote, so the product's own clone runs unchanged against them, and a step with no network
 proves every listed remote resolves locally. A published binary, a pinned release download and a
-build step's packages are still fetched in the lane, as the product fetches them. A deterministic
+build step's packages are still fetched in the lane, as the product fetches them; an install, a
+build or the lab reset's binary install whose DECISIVE error line (the last one its step ended
+with) is a failed transfer is repeated with the same backoff, only that attempt, never a test.
+One still failing leads its failure with `EXTERNAL-UNAVAILABLE what=<token>` (`transfer` in
+`testlab/lhpc_testlab/release.py`), never with a stack-regression marker; a transfer error the
+step recovered from does not count. A deterministic
 failure of an acquisition (a broken Dockerfile, say) turns red only after the backoff's waits,
 about half an hour.
 
diff --git a/testlab/lhpc_testlab/ops.py b/testlab/lhpc_testlab/ops.py
index 4ceb8542..2695d60d 100644
--- a/testlab/lhpc_testlab/ops.py
+++ b/testlab/lhpc_testlab/ops.py
@@ -246,7 +246,10 @@ def reset(svc) -> ActionResult:
     if not inst.ok:
         return ActionResult(False, "Reset failed installing the fake daemon.",
                             details=details + inst.details[:8])
-    _install_binary_stacks(svc, details)
+    failure = _install_binary_stacks(svc, details)
+    if failure:
+        return ActionResult(False, f"Reset failed installing a binary stack: {failure}",
+                            details=details)
     scenarios.log_event(svc._paths, "reset")
     return ActionResult(True, "Test lab reset to the healthy baseline.", details=details,
                         next_commands=["lhpc-testlab status", "lhpc status"])
@@ -629,11 +632,15 @@ def populate(svc) -> ActionResult:
     return ActionResult(True, summary, details=details)
 
 
-def _install_binary_stacks(svc, details: list) -> None:
+def _install_binary_stacks(svc, details: list) -> str:
     """On aarch64 (matching the Pi), pull meshcom and meshtastic from OUR binaries
     (lhpc-binaries) via the production binary channel — the real qemu-system-xtensa and
-    meshtasticd, no compile. Non-fatal: on a non-aarch64 host the channel is unavailable
-    and the stack is simply left uninstalled (its acceptance leg then skips)."""
+    meshtasticd, no compile. On a non-aarch64 host the channel is unavailable and the stack is
+    simply left uninstalled (its acceptance leg then skips). Where the channel IS available, a
+    download that fails is retried with the transfer backoff (`release.transfer`), and an install
+    that still fails is returned as the reset's failure — first line `EXTERNAL-UNAVAILABLE …`
+    when it was the transfer — instead of surfacing later as an unrelated start failure."""
+    from .release import transfer
     for sid in ("meshcom", "meshtastic"):
         try:
             avail, _why = svc.binary_available(sid)
@@ -643,11 +650,18 @@ def _install_binary_stacks(svc, details: list) -> None:
             details.append(f"  {sid}: binary channel unavailable (not aarch64) — skipped")
             continue
         try:
-            r = svc.install(sid, apply=True, source=svc.BINARY_CHANNEL)
-            details.append(f"  install {sid} (binary): {'ok' if r.ok else r.summary}")
+            r, ext = transfer(f"binary:{sid}",
+                              lambda sid=sid: svc.install(sid, apply=True,
+                                                          source=svc.BINARY_CHANNEL),
+                              decisive_text=lambda res: "\n".join([res.summary,
+                                                                    *(res.details or [])]),
+                              failed=lambda res: not res.ok)
         except Exception as exc:
-            details.append(f"  install {sid} (binary): error ({exc})")
-
+            return f"install {sid} (binary): error ({exc})"
+        details.append(f"  install {sid} (binary): {'ok' if r.ok else r.summary}")
+        if not r.ok:
+            return f"{ext}install {sid} (binary): {r.summary}"
+    return ""
 
 def _materialize_sources(svc) -> dict:
     import shutil
diff --git a/testlab/lhpc_testlab/release.py b/testlab/lhpc_testlab/release.py
index 9a423b9f..ad2129e5 100644
--- a/testlab/lhpc_testlab/release.py
+++ b/testlab/lhpc_testlab/release.py
@@ -18,6 +18,7 @@ import subprocess
 import time
 import urllib.error
 import urllib.request
+import warnings
 from pathlib import Path
 
 from lhpc.core import build_regression as br
@@ -323,26 +324,117 @@ def install_build(env, svc, stack: str, *, build: bool = True,
       typed before a step's log exists, and the pattern above needs that log to name a step.
     - an unknown shape: anything this lane cannot resolve to a declared step is not attributed.
     """
-    r = run_lhpc(env, "install", stack, "--yes", timeout=timeout)
+    r, ext = transfer(f"install:{stack}",
+                      lambda: run_lhpc(env, "install", stack, "--yes", timeout=timeout))
     assert r.returncode == 0, (
-        f"installing {stack} failed (rc {r.returncode}) — UNMARKED: an install clones and "
+        f"{ext}installing {stack} failed (rc {r.returncode}) — UNMARKED: an install clones and "
         f"downloads, so this is as likely to be the network as the stack: "
         f"{r.stdout[-2000:]}\n{r.stderr[-800:]}")
     if build and not on_binary(env, stack):
-        r = run_lhpc(env, "build", stack, "--yes", timeout=timeout)
         own = _own_recipe_step_logs(svc, stack)
-        typed = next((m for m in _TYPED_BUILD_FAILURE.finditer(r.stdout)
-                      if Path(m.group(2)).name in own), None)
+
+        def attributable(res):
+            return next((m for m in _TYPED_BUILD_FAILURE.finditer(res.stdout)
+                         if Path(m.group(2)).name in own), None)
+        r, ext = transfer(f"build:{stack}",
+                          lambda: run_lhpc(env, "build", stack, "--yes", timeout=timeout),
+                          decisive_text=lambda res: None if attributable(res) else _step_text(res))
+        typed = attributable(r)
         mark = f"{stack_regression(stack, 'build')}\n" if typed else ""
         why = (f"LHPC typed {typed.group(1)}'s own build as failed at {Path(typed.group(2)).name}"
                f", a step this recipe declares its own" if typed else
                "UNMARKED: no step this recipe declares its own (`attributable`) failed, so the "
                "cause is not provably the recipe — a fetch, a refusal or a timeout is not")
         assert r.returncode == 0, (
-            f"{mark}building {stack} failed (rc {r.returncode}) — {why}: "
+            f"{mark or ext}building {stack} failed (rc {r.returncode}) — {why}: "
             f"{r.stdout[-2000:]}\n{r.stderr[-800:]}")
 
 
+# --- a transfer that failed: the EXTERNAL-UNAVAILABLE line and the transfer backoff ------------
+#
+# The contract with the release bot (agreed 2026-10-16): the FIRST line of the assertion message
+# is exactly `EXTERNAL-UNAVAILABLE what=<token>`, <token> = [a-z0-9][a-z0-9_.:-]*, and the tool's
+# own error line follows. The bot SEARCHES for it in the JUnit failure; it never coexists with a
+# `STACK-REGRESSION` line.
+
+# The DECISIVE line of a failed step: the last error line its own output ends with.
+_DECISIVE = re.compile(r"^\s*(?:\[failed\]|ERR\b|ERROR:|error:|fatal:|curl: \(\d+\))"
+                       r"|download failed")
+# A decisive line that says WHY a transfer failed, and the why is the network or the server: a
+# name that does not resolve, a connection refused, reset or timed out, an HTTP 5xx. An HTTP 4xx
+# (a binary not yet published, a repository that is gone) is NOT a transfer: it is never retried.
+_TRANSPORT = re.compile(
+    r"Could not resolve host|Failed to connect|Connection (?:refused|reset|timed out)|timed out"
+    r"|Temporary failure in name resolution|Name or service not known|Network is unreachable"
+    r"|ConnectionError|ReadTimeoutError|ConnectTimeoutError|urlopen error \[Errno"
+    r"|(?:returned error|HTTP [Ee]rror):? 5\d\d|curl: \((?:5|6|7|28|35|52|56)\)")
+_CURL = re.compile(r"curl: \(\d+\)")
+# pip's outage shape: its retries ran out on a connection error, and only then it reports that
+# no version satisfies the requirement. A version that does not exist has no exhausted retry.
+_PIP_EXHAUSTED = re.compile(r"Retry\(total=0\b.*(?:NewConnectionError|ConnectTimeoutError"
+                            r"|ReadTimeoutError|name resolution)")
+_PIP_NOT_FOUND = "Could not find a version that satisfies the requirement"
+
+TRANSFER_WAITS = (0, 60, 120, 300, 600)      # then TRANSFER_WAITS[-1] again, while the total
+TRANSFER_TOTAL = 1800                        # of waits stays within this (docs/testlab.md)
+
+
+def external_unavailable(what: str, text: str) -> str:
+    """`EXTERNAL-UNAVAILABLE what=<what>` + the decisive line + a newline when the step's
+    DECISIVE line — its last error line — is a failed transfer, else "". A transfer error the
+    step recovered from earlier does not count: only the line the step ended with decides."""
+    lines = text.splitlines()
+    at = next((i for i in range(len(lines) - 1, -1, -1) if _DECISIVE.search(lines[i])), None)
+    if at is None:
+        return ""
+    line = lines[at].strip()
+    if "download failed" in line and not _TRANSPORT.search(line):
+        # graywolf-fetch.sh's own last word names no reason: curl's line before it does.
+        cause = next((x.strip() for x in reversed(lines[:at]) if _CURL.search(x)), "")
+        if _TRANSPORT.search(cause):
+            line = f"{line} ({cause})"
+    if not (_TRANSPORT.search(line)
+            or (_PIP_NOT_FOUND in line
+                and any(_PIP_EXHAUSTED.search(x) for x in lines[:at]))):
+        return ""
+    return f"EXTERNAL-UNAVAILABLE what={what}\n{' '.join(line.split())[:300]}\n"
+
+
+def _step_text(res) -> str:
+    """The text a failed build is judged by: the log of the step LHPC typed as failed when it
+    can be read (its own output), else what `lhpc build` printed."""
+    found = list(_TYPED_BUILD_FAILURE.finditer(res.stdout))
+    m = found[-1] if found else None
+    if m:
+        with contextlib.suppress(OSError):
+            return Path(m.group(2)).read_text(encoding="utf-8", errors="replace")[-20000:]
+    # LHPC's own `[failed] build …` summary is not the step's output: it would always be "last".
+    return "\n".join(ln for ln in f"{res.stdout}\n{res.stderr}".splitlines()
+                     if not _TYPED_BUILD_FAILURE.search(ln))
+
+
+def transfer(what: str, run, *, decisive_text=lambda res: f"{res.stdout}\n{res.stderr}",
+             failed=lambda res: res.returncode != 0, sleep=time.sleep):
+    """`run()` once; while it FAILS on a transfer (`external_unavailable` of `decisive_text(r)`;
+    None = not a transfer), repeat it with the transfer backoff, each wait warned. Returns
+    `(result, external line or "")`. Only a failed TRANSFER is repeated: any other failure, and a
+    success, returns at once. The callers repeat an install or a build, never an assertion."""
+    retries, spent = 0, 0
+    while True:
+        r = run()
+        text = decisive_text(r) if failed(r) else None
+        ext = external_unavailable(what, text) if text is not None else ""
+        if retries < len(TRANSFER_WAITS):
+            wait = TRANSFER_WAITS[retries]
+        else:                                   # past the list: its last wait, if it is one
+            wait = TRANSFER_WAITS[-1] or TRANSFER_TOTAL + 1
+        if not ext or spent + wait > TRANSFER_TOTAL:
+            return r, ext
+        warnings.warn(f"{what}: {ext.splitlines()[1]} — retrying in {wait} s", stacklevel=2)
+        sleep(wait)
+        spent, retries = spent + wait, retries + 1
+
+
 def on_binary(env: dict, stack: str) -> bool:
     """Installed from an artifact? The receipt is the authority — the same file
     `lhpc status --versions` renders `binary@…` from."""
diff --git a/testlab/tests/slowbuild/test_slow_build.py b/testlab/tests/slowbuild/test_slow_build.py
index 8b9ccf0f..81ce74ac 100644
--- a/testlab/tests/slowbuild/test_slow_build.py
+++ b/testlab/tests/slowbuild/test_slow_build.py
@@ -34,7 +34,7 @@ import uuid
 from pathlib import Path
 
 import pytest
-from lhpc_testlab.release import on_binary
+from lhpc_testlab.release import external_unavailable, on_binary
 from lhpc_testlab.testing import LabServer, lab_env, run_lhpc
 
 from lhpc.core import slow_target as stt
@@ -105,8 +105,11 @@ def _judged(what: str, r) -> str:
     out = f"{r.stdout or ''}\n{r.stderr or ''}"
     _reject(what, out)
     if r.returncode != 0:
-        pytest.fail(f"{what} failed (rc {r.returncode}) — not evidence: {out[-2000:]}",
-                    pytrace=False)
+        # A step that failed on a transfer says so first (the bot's EXTERNAL-UNAVAILABLE
+        # contract); it is not retried here: P5 moves the timed transfers out of the lane.
+        token = "slowbuild:" + (re.sub(r"[^a-z0-9_.:-]+", "-", what.lower()).strip("-")[:60] or "step")
+        pytest.fail(f"{external_unavailable(token, out)}{what} failed (rc {r.returncode}) — "
+                    f"not evidence: {out[-2000:]}", pytrace=False)
     return out
 
 
diff --git a/testlab/tests/unit/test_release_attribution.py b/testlab/tests/unit/test_release_attribution.py
index 9d6caab3..d2d3cf57 100644
--- a/testlab/tests/unit/test_release_attribution.py
+++ b/testlab/tests/unit/test_release_attribution.py
@@ -173,7 +173,14 @@ MARKER_IO = "  [failed] build loraham-kiss-tnc (rc 1, log )\\n"
 
 def _script(*results):
     it = iter(results)
-    rel.run_lhpc = lambda env, *a, **kw: next(it)
+    rel.run_lhpc = lambda env, *a, **kw: next(it, results[-1])   # a repeat sees the last again
+
+import pytest
+
+@pytest.fixture(autouse=True)
+def _one_retry_no_wait(monkeypatch):
+    monkeypatch.setattr(rel, "TRANSFER_WAITS", (0,))
+    monkeypatch.setattr(rel, "TRANSFER_TOTAL", 0)
 
 ENV = {'LHPC_RUNTIME_ROOT': '/nonexistent'}
 
@@ -834,3 +841,183 @@ def test_a_read_error_other_than_a_closed_terminal_propagates_unmarked(tmp_path,
                               ready_timeout=3, hold=0.2, stack="reticulum")
     assert exc.value.errno == errno.EBADF
     assert "STACK-REGRESSION" not in str(exc.value)
+
+
+# --- a failed transfer: the EXTERNAL-UNAVAILABLE line, the decisive step, the transfer backoff ----
+
+# The bot's search (the agreed contract): never anchored, so pytest's prefixes cannot hide it.
+_EXTERNAL = re.compile(r"EXTERNAL-UNAVAILABLE what=([a-z0-9][a-z0-9_.:-]*)(?=\s|$)")
+
+
+class _Done:
+    def __init__(self, rc, out=""):
+        self.returncode, self.stdout, self.stderr = rc, out, ""
+
+
+def _lhpc(monkeypatch, **by_verb):
+    """`lhpc <verb> …` answers from `by_verb[verb]`, a list consumed in order (the last one
+    repeats); returns the per-verb call counts. One retry with no wait: the schedule itself is
+    pinned in test_workflow_shell.py's twin and below."""
+    import lhpc_testlab.release as rel
+    calls = dict.fromkeys(by_verb, 0)
+
+    def run(env, verb, *a, **kw):
+        calls[verb] += 1
+        seq = by_verb[verb]
+        return seq[min(calls[verb], len(seq)) - 1]
+    monkeypatch.setattr(rel, "run_lhpc", run)
+    monkeypatch.setattr(rel, "on_binary", lambda env, stack: False)   # stub: no receipt read
+    monkeypatch.setattr(rel, "TRANSFER_WAITS", (0, 0), raising=False)
+    monkeypatch.setattr(rel, "TRANSFER_TOTAL", 0, raising=False)
+    return calls
+
+
+def _install_build_message(stack="kiss", build=True):
+    import warnings
+
+    import lhpc_testlab.release as rel
+
+    from lhpc.core.manifest import default_manifest_path, load_manifest
+
+    class Svc:
+        def stack(self, sid):
+            return next(s for s in load_manifest(default_manifest_path()) if s.id == sid)
+    with pytest.raises(AssertionError) as exc, warnings.catch_warnings():
+        warnings.simplefilter("ignore")
+        rel.install_build({"LHPC_RUNTIME_ROOT": "/nonexistent"}, Svc(), stack, build=build)
+    return str(exc.value)
+
+
+CLONE_DNS = ("  [failed] clone loraham-kiss-tnc — fatal: unable to access "
+             "'https://h.invalid/k.git/': Could not resolve host: h.invalid\n")
+
+
+def test_an_install_that_failed_on_a_transfer_is_external_and_never_marked(monkeypatch):
+    calls = _lhpc(monkeypatch, install=[_Done(1, CLONE_DNS)])
+    msg = _install_build_message(build=False)
+    assert msg.splitlines()[0] == "EXTERNAL-UNAVAILABLE what=install:kiss"
+    assert "Could not resolve host" in msg.splitlines()[1]
+    assert "STACK-REGRESSION" not in msg and calls["install"] == 3      # 1 + 2 retries
+
+
+def test_a_transfer_that_recovers_passes_after_the_backoff(monkeypatch):
+    import warnings
+
+    import lhpc_testlab.release as rel
+    calls = _lhpc(monkeypatch, install=[_Done(1, CLONE_DNS), _Done(1, CLONE_DNS), _Done(0)])
+    with warnings.catch_warnings(record=True) as seen:
+        warnings.simplefilter("always")
+        rel.install_build({"LHPC_RUNTIME_ROOT": "/nonexistent"}, None, "kiss", build=False)
+    assert calls["install"] == 3
+    assert sum("retrying in 0 s" in str(w.message) for w in seen) == 2
+
+
+def test_a_failure_that_is_not_a_transfer_is_never_repeated(monkeypatch):
+    calls = _lhpc(monkeypatch, install=[
+        _Done(1, "  [failed] clone loraham-kiss-tnc — fatal: repository not found\n")])
+    msg = _install_build_message(build=False)
+    assert not _EXTERNAL.search(msg) and calls["install"] == 1
+
+
+def _meshcore_steps():
+    from lhpc.core.manifest import default_manifest_path, load_manifest
+    st = next(s for s in load_manifest(default_manifest_path()) if s.id == "meshcore")
+    node = next(c for c in st.components if c.build_steps
+                and any(s.get("attributable") for s in c.build_steps)
+                and any(str(s["argv"][0]).endswith("pip") and "install" in s["argv"]
+                        for s in c.build_steps))
+    net = next(i for i, s in enumerate(node.build_steps)
+               if str(s["argv"][0]).endswith("pip") and "install" in s["argv"])
+    own = next(i for i, s in enumerate(node.build_steps) if s.get("attributable"))
+    def typed(step):
+        return f"  [failed] build {node.id} (rc 1, log /nonexistent/logs/build-{node.id}-{step}.log)\n"
+    return typed(net), typed(own)
+
+
+@pytest.mark.parametrize("step_output, expect", [
+    # the decisive (last) error line of a fetching step is a transport failure → EXTERNAL
+    ("ERROR: Could not install packages due to an OSError: HTTPSConnectionPool(host='pypi.org',"
+     " port=443): Read timed out.\n", "external"),
+    # pip's outage shape: retries exhausted on name resolution, then "no version satisfies"
+    ("WARNING: Retrying (Retry(total=0, connect=None)) after connection broken by "
+     "'NewConnectionError(...: Temporary failure in name resolution)': /simple/pynacl/\n"
+     "ERROR: Could not find a version that satisfies the requirement pynacl\n", "external"),
+    # SAME-STEP TWIN: a transport hiccup pip recovered from, then a genuine resolution failure
+    ("WARNING: Retrying (Retry(total=4, connect=None)) after connection broken by "
+     "'ConnectionError(...)': /simple/pynacl/\n"
+     "ERROR: ResolutionImpossible: for help visit https://pip.pypa.io/\n", "unmarked"),
+    # a version that does not exist: no exhausted retry, so not a transfer
+    ("ERROR: Could not find a version that satisfies the requirement pynacl==99\n", "unmarked"),
+    ("something nobody classified\n", "unmarked"),
+])
+def test_a_fetching_build_step_is_judged_by_its_decisive_line(monkeypatch, step_output, expect):
+    net_typed, _ = _meshcore_steps()
+    calls = _lhpc(monkeypatch, install=[_Done(0)], build=[_Done(1, step_output + net_typed)])
+    msg = _install_build_message("meshcore")
+    assert "STACK-REGRESSION" not in msg
+    if expect == "external":
+        assert msg.splitlines()[0] == "EXTERNAL-UNAVAILABLE what=build:meshcore"
+        assert calls["build"] == 3
+    else:
+        assert not _EXTERNAL.search(msg) and calls["build"] == 1
+
+
+def test_an_attributable_step_is_marked_whatever_transport_text_the_output_carries(monkeypatch):
+    """The MIXED-LOG TWIN: the decisive step is the recipe's own, so it is the stack's
+    regression, even though an earlier step's output carries a transport error."""
+    _, own_typed = _meshcore_steps()
+    calls = _lhpc(monkeypatch, install=[_Done(0)], build=[_Done(
+        1, "ERROR: Could not install packages: Read timed out.\n"
+           "error: patch does not apply\n" + own_typed)])
+    msg = _install_build_message("meshcore")
+    assert msg.splitlines()[0] == "STACK-REGRESSION stack=meshcore phase=build"
+    assert not _EXTERNAL.search(msg) and calls["build"] == 1
+
+
+# A real failing install's output (release-verify run 37403409245, 2026-10-06): the summary is
+# the `ERR` line, then numbered advice and `Next:` — a refusal, not a transfer.
+REAL_INSTALL_REFUSAL = (
+    "ERR   Binary install of 'meshcom' refused: the published binary was built from different "
+    "commits than this lhpc pins (meshcom-firmware: binary e1e2acea6, pin 62095658c)\n"
+    "  1. Update LHPC first, then install again (about a minute): lhpc self-update --apply\n"
+    "  2. Or build from source: lhpc install meshcom --source pinned --yes\n"
+    "  Staying on the binary keeps meshcom exactly as it is.\n\nNext:\n  lhpc self-update --apply\n")
+
+
+@pytest.mark.parametrize("output", [
+    REAL_INSTALL_REFUSAL,
+    # an HTTP 4xx is not the network: a binary not yet published, a repository that is gone
+    "ERR   Binary install of 'meshcom' refused: download failed: HTTP Error 404: Not Found\n",
+    "  [failed] clone loraham-kiss-tnc — fatal: unable to access 'https://h.invalid/k.git/': "
+    "The requested URL returned error: 403\n",
+], ids=["real-refusal", "binary-404", "git-403"])
+def test_an_install_that_failed_for_a_reason_other_than_the_network_is_not_repeated(
+        monkeypatch, output):
+    calls = _lhpc(monkeypatch, install=[_Done(1, output)])
+    msg = _install_build_message(build=False)
+    assert not _EXTERNAL.search(msg) and calls["install"] == 1
+
+
+@pytest.mark.parametrize("log_tail, stdout_extra, expect", [
+    # the step's own log ends on a read timeout → EXTERNAL, though `lhpc build` printed nothing
+    ("Collecting pynacl\nERROR: Could not install packages due to an OSError: "
+     "HTTPSConnectionPool(host='pypi.org', port=443): Read timed out.\n", "", "external"),
+    # the log ends on a genuine resolution failure; a timeout in `lhpc build`'s own output
+    # (another step, a recovered hiccup) does not decide
+    ("ERROR: ResolutionImpossible: for help visit https://pip.pypa.io/\n",
+     "WARNING: Retrying after connection broken by ReadTimeoutError\n", "unmarked"),
+])
+def test_the_failed_steps_readable_log_decides(monkeypatch, tmp_path, log_tail, stdout_extra,
+                                               expect):
+    net_typed, _ = _meshcore_steps()
+    log = tmp_path / net_typed.split("log ", 1)[1].rstrip(")\n").rsplit("/", 1)[1]
+    log.write_text(log_tail)
+    typed = net_typed.replace(net_typed.split("log ", 1)[1].rstrip(")\n"), str(log))
+    calls = _lhpc(monkeypatch, install=[_Done(0)], build=[_Done(1, stdout_extra + typed)])
+    msg = _install_build_message("meshcore")
+    assert "STACK-REGRESSION" not in msg
+    if expect == "external":
+        assert msg.splitlines()[0] == "EXTERNAL-UNAVAILABLE what=build:meshcore"
+        assert calls["build"] == 3
+    else:
+        assert not _EXTERNAL.search(msg) and calls["build"] == 1
diff --git a/testlab/tests/unit/test_slow_build_lane.py b/testlab/tests/unit/test_slow_build_lane.py
index 759901b3..862a35cd 100644
--- a/testlab/tests/unit/test_slow_build_lane.py
+++ b/testlab/tests/unit/test_slow_build_lane.py
@@ -483,3 +483,32 @@ def test_the_disk_throttle_must_be_named(tmp_path, monkeypatch):
     monkeypatch.setenv("SLOW_WRITE_IOPS", "1")
     monkeypatch.setenv("SLOW_READ_IOPS", "1")
     assert lane._io_problems(tmp_path / "nowhere", REQUIRED)[0].startswith("io.max is unreadable")
+
+
+def test_a_step_that_failed_on_a_transfer_says_external_first():
+    """The bot reads slow-build's JUnit: a timed step that failed on a download leads with the
+    EXTERNAL-UNAVAILABLE line, and is never a STACK-REGRESSION."""
+    with pytest.raises(pytest.fail.Exception) as red:
+        lane._judged("the upstream fetch of 0.14.14", _done(
+            rc=22, stderr="[graywolf-fetch] download failed: curl: (6) Could not resolve host\n"))
+    lines = str(red.value).splitlines()
+    assert lines[0] == "EXTERNAL-UNAVAILABLE what=slowbuild:the-upstream-fetch-of-0.14.14"
+    assert "STACK-REGRESSION" not in str(red.value)
+
+
+def test_a_step_that_failed_otherwise_has_no_external_line():
+    with pytest.raises(pytest.fail.Exception) as red:
+        lane._judged("building meshcore-cli", _done(rc=1, stderr="error: compile failed\n"))
+    assert not str(red.value).startswith("EXTERNAL-UNAVAILABLE")
+
+
+@pytest.mark.parametrize("stderr, external", [
+    ("curl: (6) Could not resolve host: github.com\n[graywolf-fetch] download failed: "
+     "https://github.com/x.deb\n", True),
+    ("curl: (22) The requested URL returned error: 404\n[graywolf-fetch] download failed: "
+     "https://github.com/x.deb\n", False),
+], ids=["curl-dns", "curl-404"])
+def test_the_fetch_scripts_bare_download_failed_is_judged_by_curls_own_line(stderr, external):
+    with pytest.raises(pytest.fail.Exception) as red:
+        lane._judged("the upstream fetch of 0.14.14", _done(rc=1, stderr=stderr))
+    assert str(red.value).startswith("EXTERNAL-UNAVAILABLE what=") is external
diff --git a/testlab/tests/unit/test_testlab.py b/testlab/tests/unit/test_testlab.py
index 30c78e07..83455a2d 100644
--- a/testlab/tests/unit/test_testlab.py
+++ b/testlab/tests/unit/test_testlab.py
@@ -1102,3 +1102,58 @@ def test_meshtastic_dashboard_link_forwards_to_bridge(monkeypatch):
     assert "https://demo-cs-9080.app.github.dev/" in body      # 9443 -> 9080 bridge
     assert "demo-cs-9443" not in body                          # NOT the broken self-signed URL
     assert "https://demo-cs-8080.app.github.dev/" in body      # other lab ports unchanged
+
+
+# --- the reset's binary stacks: a failed download is the reset's failure, never hidden --------
+
+def _binary_svc(*, available, outcomes):
+    """A service whose binary channel is (un)available and whose installs answer `outcomes` in
+    order (the last repeats). Returns (svc, calls)."""
+    from types import SimpleNamespace
+    calls = []
+
+    class Svc:
+        BINARY_CHANNEL = "binary"
+
+        def binary_available(self, sid):
+            return (available, "")
+
+        def install(self, sid, apply=False, source="pinned"):
+            calls.append(sid)
+            ok, summary = outcomes[min(len(calls), len(outcomes)) - 1]
+            return SimpleNamespace(ok=ok, summary=summary, details=[])
+    return Svc(), calls
+
+
+_DL_FAILED = ("Binary install of 'meshcom' refused: download failed: "
+              "<urlopen error [Errno -3] Temporary failure in name resolution>")
+
+
+def test_a_binary_download_that_stays_unavailable_fails_the_reset_as_external(monkeypatch):
+    import warnings
+
+    import lhpc_testlab.release as rel
+    monkeypatch.setattr(rel, "TRANSFER_WAITS", (0, 0), raising=False)
+    monkeypatch.setattr(rel, "TRANSFER_TOTAL", 0, raising=False)
+    svc, calls = _binary_svc(available=True, outcomes=[(False, _DL_FAILED)])
+    details = []
+    with warnings.catch_warnings():
+        warnings.simplefilter("ignore")
+        failure = ops._install_binary_stacks(svc, details)
+    assert failure.splitlines()[0] == "EXTERNAL-UNAVAILABLE what=binary:meshcom"
+    assert calls == ["meshcom"] * 3                   # 1 + 2 retries, then the reset fails
+
+
+def test_a_binary_install_that_fails_otherwise_fails_the_reset_at_once(monkeypatch):
+    refused = ("Binary install of 'meshcom' refused: the published binary was built from "
+               "different commits")
+    svc, calls = _binary_svc(available=True, outcomes=[(False, refused)])
+    assert ops._install_binary_stacks(svc, []) == f"install meshcom (binary): {refused}"
+    assert calls == ["meshcom"]
+
+
+def test_without_a_binary_channel_the_reset_skips_the_binary_stacks():
+    svc, calls = _binary_svc(available=False, outcomes=[(True, "")])
+    details = []
+    assert ops._install_binary_stacks(svc, details) == "" and calls == []
+    assert sum("binary channel unavailable" in d for d in details) == 2
```
