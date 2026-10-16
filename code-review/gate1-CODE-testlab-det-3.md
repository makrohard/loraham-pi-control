# Gate 1: testlab-det-3 (P3: the mirror), v4

**Round.** v4, superseding v3 (round 12 was held): (1) the lanes' mirror config now reaches the product's git through the runner (`~/.gitconfig` in HOME, not `GIT_CONFIG_GLOBAL`, which RealCommandRunner drops), proved by a new `resolved` check through the real runner in each lane and by a runner test; (2) round 12's two points: exclusions keyed by component with a twin test, and the stale report text. Judge first whether these are closed (report, section v4), then the whole commit again.

**Request.** Judge the one code commit below against plan v4 §1 (class A: what a lane needs from outside is acquired before it, with the transfer backoff) and §2 P3, and the maintainer's decisions c1 (transfers are retried long, never an assertion or a test body) and c3 (release-verify installs from the mirror): the mirror script, the backoff helper, the workflow wiring (acquisition, the no-network completeness proof, the lane's `GIT_CONFIG_GLOBAL`), the Xvfb socket wait, the docs paragraph and the CHANGELOG line. Judge whether the product's own clone path is really unchanged, whether anything can retry a verdict, whether the tests can fail, and the report's claims against the diff. The lane run on the branch is extra evidence; landing follows the 2026-10-16 rule (gate 1 + local checks).

Judge also: does anything in the lane still reach git or a remote outside what `resolved` proves; is `verify`'s temporary HOME equivalent to the lane's.

This file is your whole input: you have no repository access; use no connector, tool or web lookup.

**Answer form.**

| commit | verdict (OK / FINDING) | what |
|---|---|---|

Then numbered findings (file:line, why it matters, smallest fix), then one final line: GREEN / GREEN WITH NOTES / RED.

Redaction: none needed; the diff is `git diff` output verbatim.

## Commits (full messages, `git log --reverse --format=%B`)

### 6e7903e0 testlab: the lanes clone from pre-fetched mirrors; acquisition has the transfer backoff

```text
testlab: the lanes clone from pre-fetched mirrors; acquisition has the transfer backoff

Every testlab and release-verify run cloned 14 upstream remotes live from GitHub inside the
lane (the chain fixture, `install_build`), with no retry: a GitHub hiccup reddened a lane whose
subject is LHPC, not GitHub's availability (plan v4 §1, class A; decision c1).

- `.github/scripts/mirror-sources.py` lists EVERY remote the manifest names except those of a
  small NOT_MIRRORED table of components (radiolib: the lab's fake; meshtastic,
  meshcom-firmware and meshcom-bridge: the binary channel), each with its reason. A component
  added later is mirrored by default, also when it reuses an excluded component's remote; an
  exclusion naming a component the manifest no longer has is an error. The result covers the 14
  remotes green run 37426359349's lanes cloned. It names one mirror directory per remote, writes
  the git config (`insteadOf` per remote + `safe.directory = *`, the mirrors being owned by the
  runner's user), verifies every remote clones through it, and (`resolved`) checks through
  lhpc's own command runner that every remote resolves to its mirror.
- Both jobs: an acquisition step clones each remote `--mirror` under the backoff before the
  image build; a step with `--network none` proves the list complete; the lane mounts the
  mirror read-only, copies the config to the lab user's ~/.gitconfig, and runs `resolved`
  before its tests. The file in HOME, not GIT_CONFIG_GLOBAL: the product clones through
  RealCommandRunner, whose children get a fixed environment that keeps HOME and drops
  GIT_CONFIG_GLOBAL (`_FIXED_ENV`, lhpc/core/probes/backends.py). The product's own,
  unchanged `git clone <remote>` resolves to the mirror through git's `insteadOf`.
- `backoff` (retry.sh, beside `retry`): retry at once, then after 1, 2, 5 and 10 min, then every
  10 min until the waits would pass 30 min; each wait logged, the last status returned. It now
  wraps `docker build`, the lab's pip installs and the Chromium download in these two jobs.
- The testlab job waits (≤ 30 s) for Xvfb's socket before the lanes use the display.

New invariants (new tooling), with negative tests: tests/repo/test_mirror_sources.py (the list
covers every remote a green run cloned; a remote added to the manifest is mirrored by default,
and so is an excluded component's remote reused by a new component (the twin); a stale exclusion
is an error; the exact git config; a clone through it resolves to the mirror while an unmirrored
remote fails: offline, `.invalid` host; through the product's runner a clone of a nonexistent
remote succeeds only with the config in HOME, and fails with GIT_CONFIG_GLOBAL, `resolved`
reporting each) and tests/repo/test_workflow_shell.py (the backoff schedule read exactly with a
recording `sleep`: 0/60/120/300/600/600, the command's status 7 returned; recovery after two
waits; and the existing never-wrap-a-verdict guard now also reads `backoff`). Red-before against
the previous version of this commit (917be744): both runner cases and the twin fail there.
tests/repo: 591 passed, 1 skipped.

```

## The report

> # Code report: testlab-det-3 v4 (packet P3: the lanes clone from mirrors; acquisition has the transfer backoff)
>
> Branch `cons/testlab-det-3-r1` on `origin/dev` = `d77e34f4` (P4b landed). One code commit, 6e7903e0. Plan v4 §1 (class A, transfer backoff) and §2 P3; the maintainer's decisions c1 (transfers retried long, never an assertion) and c3 (release-verify installs from the mirror).
>
> ## v4: what changed since v3 (917be744), and why
>
> 1. **A real defect: v3's lanes never cloned from the mirrors** (second read of P5; it hits P3). The lane exported `GIT_CONFIG_GLOBAL`, but the product clones through `RealCommandRunner`, whose children get `{**_FIXED_ENV, **env}`. `_FIXED_ENV` is PATH/LANG/LC_ALL plus a short whitelist that includes `HOME` and `XDG_CONFIG_HOME` and not `GIT_CONFIG_GLOBAL` (lhpc/core/probes/backends.py:188–203, used at :262, :374, :430). So run 38823954555's green lanes cloned from GitHub; the mirror was fetched and proved complete, but not used. Fix, with no product change: the lane copies the config to the lab user's `~/.gitconfig` (`su lhpclab` sets `HOME`), and the runner forwards `HOME`. `verify` now uses the same mechanism (a temporary `HOME`).
> 2. **The proof goes through the real runner.** New `mirror-sources.py resolved <dir>`: for every remote, `git ls-remote --get-url <remote>` run through `RealCommandRunner`. It prints the URL after `insteadOf` without touching the network, and exits 1 on any remote that does not resolve to its mirror. Each lane runs it after installing lhpc and before its tests. The test `test_only_a_gitconfig_in_home_redirects_a_clone_through_the_products_runner` clones a NONEXISTENT `file:///nonexistent/upstream/up.git` through `RealCommandRunner` in a fresh interpreter (so `_FIXED_ENV` is built from that test's `HOME`). With the config in `HOME` the clone succeeds and `resolved` prints `ok`. With `GIT_CONFIG_GLOBAL` (v3's mechanism) the clone fails and `resolved` prints `NOT REDIRECTED … (resolves to <remote>)`, exit 1. No network either way.
> 3. **Exclusions are keyed by component, not by remote URL** (round 12 point 1). In v3, a future component reusing an excluded URL (say the meshtastic firmware) would have been dropped from the list and cloned live. `NOT_MIRRORED` now names components (`radiolib`, `meshtastic`, `meshcom-firmware`, `meshcom-bridge`). A remote is excluded only through those components, and a stale component id is an error. Twin test: the real manifest plus a new component on the meshtastic firmware remote lists that remote.
> 4. **Stale text** (round 12 point 2): this report is rewritten for v4; `LANE_COMPONENTS` and the "14-component list" (v2) are gone from code and report.
> 5. **Nothing landed relied on the dropped env.**
>    - P2's `PYTHONFAULTHANDLER` reaches stack programs through `lifecycle`'s spawn (`{**os.environ, **env}`, lhpc/core/lifecycle.py:1485, wrapped by testlab/lhpc_testlab/spawn.py), not through the runner.
>    - The interactive case sets it on its own `_pty_launch`.
>    - P3a and P1 pass no env to the runner. P1's slow-build Graywolf fetch runs `graywolf-fetch.sh` by direct `subprocess.run`.
>    - The lab's fake sources use `GIT_CONFIG_GLOBAL=/dev/null` in their OWN direct git calls (testlab/lhpc_testlab/ops.py:656), unaffected.
>
> Red-before against v3 (the new test file on a `git archive` of 917be744): 4 failed, 4 passed.
> - Both runner cases fail on the missing `resolved` (`(2, '')`).
> - The twin fails through the behaviour: the reused firmware remote is absent from the list.
> - `a remote added … mirrored by default` fails because its fixture now names excluded COMPONENTS (v3 keyed URLs). That is a fixture shape, not a behaviour.
>
> ## Commit
>
> | commit | files | tests | red-before |
> |---|---|---|---|
> | 6e7903e0 | .github/scripts/mirror-sources.py (new), .github/scripts/retry.sh (`backoff`), .github/workflows/testlab.yml (testlab + release-verify jobs), docs/testlab.md, CHANGELOG.md, tests/repo/test_mirror_sources.py (new), tests/repo/test_workflow_shell.py | tests/repo: 9 mirror cases (2 through the runner) + 3 backoff cases; the existing verdict guard also reads `backoff` | new tooling: negative cases; for v4's fixes, red against v3 as above |
>
> ## HOUSE RULES KEPT (dev's tools/check_houserules.py on origin/dev..HEAD)
>
> ```
> HOUSE RULES KEPT  origin/dev..HEAD
> changelog    +1 bullets (limit 2, each ≤ 3 lines; longest 2), top section 22 lines   OK
> docs         1 files, +17 lines; fact "What a CI lane may fetch" -> docs/testlab.md; fact "mirror-sources.py" -> docs/testlab.md   OK
> tests        10 new tests; layers tests/repo=repo; red-before 1/1   OK
> patterns     getsource 0 assert-True 0 source-read 0 sleep 0 startswith 2 network 0   WARN
> markers      0 without slow   OK
> attribution  authors makrohard; trailers 0; words 0   OK
> modules      no family file touched; family net +0 lines, +0 public   OK
> VERDICT      PASS  (hard 0, warn 1)
> ```
>
> The WARN, answered: **`startswith 2`**, both in test_workflow_shell.py, are list FILTERS inside the assert (`[… for ln in … if ln.startswith("slept ")]`); the assertion compares the whole list exactly (`== ["0", "60", "120", "300", "600", "600"]`).
>
> ## What the commit does
>
> - **The mirror.**
>   - `mirror-sources.py list` derives the remotes from the manifest: all, minus those of the `NOT_MIRRORED` components, each with its reason. Today that is 14 remotes, covering the 14 green run 37426359349 cloned.
>   - The acquisition step clones each `--mirror` under `backoff`, and each attempt first removes a partial directory.
>   - `config` writes one `insteadOf` per remote plus `safe.directory = *`: the mirrors are mounted read-only and owned by the runner's user.
>   - The lane copies it to `~/.gitconfig` and runs `resolved`. `remote.origin.url` stays the https URL, because `insteadOf` rewrites at use.
> - **The completeness proof.** `verify` runs in `docker run --network none` and clones each listed remote through the config, exiting 1 on any failure.
> - **The backoff.**
>   - `backoff` sits beside `retry` in retry.sh: at once, then after 1, 2, 5 and 10 min, then every 10 min until 30 min. Each wait is logged; the last status is returned.
>   - It wraps `docker build`, the lab's pip installs and the Chromium download in the two jobs.
>   - The guard test refuses `backoff pytest|…` as it refuses `retry N …`.
>   - A deterministic failure there turns red only after the waits (about 28 min). That is the accepted cost of c1, stated in docs/testlab.md.
> - **Xvfb.** The testlab job waits up to 60 × 0.5 s for `/tmp/.X11-unix/X99`.
>
> ## The 6-point block
>
> 1. **Contracts.**
>    - `list <manifest>`: remotes, one per line; exit 1 naming a stale excluded component.
>    - `name <remote>`: `host_owner_repo.git`.
>    - `config <remotes-file> <dir>`: the exact git config text (test-pinned).
>    - `verify <dir>`: `ok` / `NOT MIRRORED` per remote, exit 1 on any.
>    - `resolved <dir>`: `ok` / `NOT REDIRECTED  <remote>  (resolves to <url>)` per remote, exit 1 on any. It imports lhpc; the rest is stdlib.
>    - `backoff <argv…>`: 0, or the command's last status.
> 2. **Invariants.**
>    - No assertion or test body is retried.
>    - The product's clone path is unchanged (no lhpc/ file changed); the lane proves, through the product's runner, that it resolves to the mirrors.
> 3. **Failure classes.**
>    - A missing mirror fails `verify` with no network.
>    - A config that does not reach the runner's git fails `resolved` before the tests.
>    - A new component is mirrored by default, also on a reused excluded remote.
>    - A stale exclusion is an error.
>    - Residual: `resolved` proves the rewrite for every LISTED remote. A lane clone of a remote the manifest does not name (none today) is not covered.
> 4. **Test rules:** repository layer, no network (an `.invalid` host only `insteadOf` resolves; nonexistent `file://` paths); exact text where the format is the contract; the runner cases in a fresh interpreter.
> 5. **Directories:**
>    - `pytest -q -n 8 tests/repo` → `591 passed, 1 skipped`.
>    - `ruff check lhpc testlab` and `ruff check tests --select F,E9` → `All checks passed!`.
> 6. **Adversarial self-review.**
>    - (a) `safe.directory = *` now lives in the lab user's `~/.gitconfig` inside the throwaway lane container (it was in the exported global file before): the same scope, one container.
>    - (b) The v3 claim "implied by `insteadOf`" was wrong, and v3's report said "not observed". v4 now observes it on every lane.
>    - (c) Lane evidence: v4 has no lane run yet; the batch run on dev, or a branch run on request, shows `resolved`'s `ok` lines in the job log.
>
> ## Numbers, measured
>
> | figure | command | output |
> |---|---|---|
> | base | `git log --oneline -1 origin/dev` | `d77e34f4` |
> | diff | `git diff --shortstat origin/dev 6e7903e0` | 7 files changed, 434 insertions(+), 12 deletions(-) |
> | remotes | `mirror-sources.py list lhpc/data/manifest.example.toml \| wc -l` | 14 |
> | tests | `pytest -q -n 8 tests/repo` | 591 passed, 1 skipped |
> | red-before | the new test file on `git archive 917be744` | 4 failed, 4 passed |
> | runner whitelist | `sed -n 188,203p lhpc/core/probes/backends.py` | HOME, XDG_RUNTIME_DIR, XDG_CONFIG_HOME, DBUS_SESSION_BUS_ADDRESS, PLATFORMIO_CORE_DIR, IDF_TOOLS_PATH, XDG_CACHE_HOME, PIP_CACHE_DIR, TMPDIR, LHPC_QEMU_MIN_FREE_INODES |

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

## The full diff (`git diff origin/dev HEAD`)

```diff
diff --git a/.github/scripts/mirror-sources.py b/.github/scripts/mirror-sources.py
new file mode 100644
index 00000000..db69caf0
--- /dev/null
+++ b/.github/scripts/mirror-sources.py
@@ -0,0 +1,129 @@
+#!/usr/bin/env python3
+"""The upstream git sources the testlab and release-verify lanes clone, served from local mirrors.
+
+    mirror-sources.py list <manifest>          every manifest remote but NOT_MIRRORED's
+    mirror-sources.py name <remote>            the mirror directory name of one remote
+    mirror-sources.py config <remotes> <dir>   a git config: each remote → file://<dir>/<name>
+    mirror-sources.py verify <dir>             clone every remote in <dir>/remotes.txt with
+                                               <dir>/gitconfig as ~/.gitconfig; exit 1 if any fails
+    mirror-sources.py resolved <dir>           through lhpc's own command runner, every remote in
+                                               <dir>/remotes.txt resolves to its mirror; exit 1 if not
+
+The workflow fetches each mirror before the lane (with the transfer backoff) and the lane copies
+<dir>/gitconfig to the lab user's ~/.gitconfig, so the product's own `git clone <remote>` resolves
+to the mirror through git's `insteadOf`, unchanged. The file, not GIT_CONFIG_GLOBAL: lhpc's
+command runner gives its children a fixed environment that keeps HOME and drops
+GIT_CONFIG_GLOBAL (`_FIXED_ENV`, lhpc/core/probes/backends.py). `verify` runs in a container with
+no network, so a lane remote missing from the list fails there instead of reaching the network;
+`resolved` runs in the lane and proves the redirection reaches git through that runner. Stdlib
+only, except `resolved`, which imports lhpc: the rest runs on the CI host before the product is
+installed.
+"""
+from __future__ import annotations
+
+import re
+import subprocess
+import sys
+import tempfile
+import tomllib
+from pathlib import Path
+
+# Every remote the manifest names is mirrored, except those of these components, each with its
+# reason. Deriving the list from the manifest, not from a list of lane components, means a
+# component added later is mirrored by default: an unneeded mirror only costs time, a missing one
+# would clone live. Keyed by component, not by remote: a new component naming an excluded
+# component's remote still gets it mirrored.
+NOT_MIRRORED = {
+    "radiolib": "the lab substitutes a local fake for RadioLib",
+    "meshtastic": "meshtastic comes from the binary channel on the lab's aarch64 runner",
+    "meshcom-firmware": "meshcom comes from the binary channel",
+    "meshcom-bridge": "meshcom comes from the binary channel",
+}
+
+
+def lane_remotes(manifest: Path) -> list[str]:
+    """Every distinct remote of a component not in NOT_MIRRORED. An exclusion naming a component
+    the manifest no longer has is an error, so the table cannot silently go stale."""
+    found, ids = set(), set()
+    for stack in tomllib.loads(manifest.read_text())["stack"]:
+        for comp in stack.get("component", ()):
+            ids.add(comp.get("id"))
+            remote = (comp.get("source") or {}).get("remote", "")
+            if remote and comp.get("id") not in NOT_MIRRORED:
+                found.add(remote)
+    stale = sorted(set(NOT_MIRRORED) - ids)
+    if stale:
+        raise SystemExit(f"NOT_MIRRORED names components {manifest} no longer has: {', '.join(stale)}")
+    return sorted(found)
+
+
+def mirror_name(remote: str) -> str:
+    """`https://github.com/owner/repo.git` → `github.com_owner_repo.git` (one directory per remote)."""
+    name = re.sub(r"^[a-z]+://", "", remote).strip("/")
+    name = re.sub(r"[^A-Za-z0-9._-]+", "_", name)
+    return name if name.endswith(".git") else name + ".git"
+
+
+def git_config(remotes: list[str], directory: str) -> str:
+    """`insteadOf` for every remote, and `safe.directory = *`: the mirrors are mounted read-only
+    and owned by the runner's user, not by the lab user who clones from them."""
+    out = ["[safe]\n\tdirectory = *\n"]
+    for remote in remotes:
+        out.append(f'[url "file://{directory}/{mirror_name(remote)}"]\n\tinsteadOf = {remote}\n')
+    return "".join(out)
+
+
+def _home_with(gitconfig: Path, home: str) -> dict:
+    """The environment git sees in the lane: a $HOME whose .gitconfig is `gitconfig`."""
+    Path(home, ".gitconfig").write_text(gitconfig.read_text())
+    return {"HOME": home, "GIT_CONFIG_NOSYSTEM": "1", "GIT_TERMINAL_PROMPT": "0",
+            "PATH": "/usr/bin:/bin"}
+
+
+def verify(directory: Path) -> int:
+    failed = 0
+    with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as home:
+        env = _home_with(directory / "gitconfig", home)
+        for i, remote in enumerate((directory / "remotes.txt").read_text().split()):
+            r = subprocess.run(["git", "clone", "--quiet", "--no-checkout", remote, f"{tmp}/{i}"],
+                               capture_output=True, text=True, env=env, check=False)
+            failed += r.returncode != 0
+            print(f"{'ok' if r.returncode == 0 else 'NOT MIRRORED'}  {remote}"
+                  + (f"  ({' '.join(r.stderr.split())[:200]})" if r.returncode else ""))
+    return 1 if failed else 0
+
+
+def resolved(directory: Path) -> int:
+    """`git ls-remote --get-url` applies `insteadOf` without touching the network; run through
+    the runner the product clones with, it prints what that clone would fetch from."""
+    from lhpc.core.probes.backends import RealCommandRunner
+    runner, failed = RealCommandRunner(), 0
+    for remote in (directory / "remotes.txt").read_text().split():
+        want = f"file://{directory}/{mirror_name(remote)}"
+        got = runner.run(["git", "ls-remote", "--get-url", remote], 30).stdout.strip()
+        failed += got != want
+        print(f"{'ok' if got == want else 'NOT REDIRECTED'}  {remote}"
+              + ("" if got == want else f"  (resolves to {got or 'nothing'})"))
+    return 1 if failed else 0
+
+
+def main(argv: list[str]) -> int:
+    cmd, *args = argv or ["help"]
+    if cmd == "list" and len(args) == 1:
+        print("\n".join(lane_remotes(Path(args[0]))))
+    elif cmd == "name" and len(args) == 1:
+        print(mirror_name(args[0]))
+    elif cmd == "config" and len(args) == 2:
+        sys.stdout.write(git_config(Path(args[0]).read_text().split(), args[1]))
+    elif cmd == "verify" and len(args) == 1:
+        return verify(Path(args[0]))
+    elif cmd == "resolved" and len(args) == 1:
+        return resolved(Path(args[0]))
+    else:
+        print(__doc__, file=sys.stderr)
+        return 2
+    return 0
+
+
+if __name__ == "__main__":
+    sys.exit(main(sys.argv[1:]))
diff --git a/.github/scripts/retry.sh b/.github/scripts/retry.sh
index eb4753bc..f1298a76 100755
--- a/.github/scripts/retry.sh
+++ b/.github/scripts/retry.sh
@@ -33,3 +33,36 @@ retry() {
     _retry_n=$((_retry_n + 1))
   done
 }
+
+# backoff <argv...> — the TRANSFER backoff (docs/testlab.md, "Running the verification lanes"):
+# run argv until it exits 0, retrying at once, then after 1, 2, 5 and 10 min, then every 10 min,
+# until the waits would pass 30 min; every wait is logged with the failed status, and the last
+# status is returned. Like `retry`, only around a transfer (an acquisition or a download), never
+# around a verdict. BACKOFF_WAITS (seconds) and BACKOFF_TOTAL replace the schedule in tests; a
+# last wait of 0 is not repeated.
+backoff() {
+  local -a _bo_waits
+  read -r -a _bo_waits <<< "${BACKOFF_WAITS-0 60 120 300 600}"
+  local _bo_total="${BACKOFF_TOTAL-1800}" _bo_spent=0 _bo_i=0 _bo_w _bo_status
+  while true; do
+    if "$@"; then
+      return 0
+    else
+      _bo_status=$?
+    fi
+    if [ "$_bo_i" -lt "${#_bo_waits[@]}" ]; then
+      _bo_w="${_bo_waits[$_bo_i]}"
+    else
+      _bo_w="${_bo_waits[${#_bo_waits[@]}-1]}"
+      [ "$_bo_w" -gt 0 ] || _bo_w=$((_bo_total + 1))
+    fi
+    if [ $((_bo_spent + _bo_w)) -gt "$_bo_total" ]; then
+      echo "backoff: '$1' still failing after ${_bo_spent}s of waits; giving up (status $_bo_status)" >&2
+      return "$_bo_status"
+    fi
+    echo "backoff: '$1' failed (status $_bo_status); retrying in ${_bo_w}s" >&2
+    sleep "$_bo_w"
+    _bo_spent=$((_bo_spent + _bo_w))
+    _bo_i=$((_bo_i + 1))
+  done
+}
diff --git a/.github/workflows/testlab.yml b/.github/workflows/testlab.yml
index d842123f..a0584bcb 100644
--- a/.github/workflows/testlab.yml
+++ b/.github/workflows/testlab.yml
@@ -39,6 +39,21 @@ jobs:
         with:
           persist-credentials: false
 
+      - name: Acquire the upstream sources the lanes clone (mirrors, transfer backoff)
+        # Class A of the rule in docs/testlab.md: the lane itself reaches no git remote. Each
+        # mirror is fetched here with the transfer backoff; the lane clones through the
+        # `insteadOf` lines of srcmirror/gitconfig, copied to the lab user's ~/.gitconfig, so the
+        # product's own clone code runs unchanged.
+        run: |
+          . .github/scripts/retry.sh
+          mkdir -p srcmirror
+          python3 .github/scripts/mirror-sources.py list lhpc/data/manifest.example.toml > srcmirror/remotes.txt
+          while read -r remote; do
+            dir="srcmirror/$(python3 .github/scripts/mirror-sources.py name "$remote")"
+            backoff sh -c 'rm -rf "$1" && git clone --quiet --mirror "$2" "$1"' _ "$dir" "$remote"
+          done < srcmirror/remotes.txt
+          python3 .github/scripts/mirror-sources.py config srcmirror/remotes.txt /srcmirror > srcmirror/gitconfig
+
       - name: Build the lab image from THIS commit's Dockerfile
         # Build (never pull) so the image deps always match the checkout — a stale
         # published `latest` (e.g. racing the image-publish workflow) would miss deps a
@@ -46,16 +61,22 @@ jobs:
         # the heavy acceptance lane below boots the binary meshcom and meshtastic legs on
         # this native aarch64 runner.
         run: |
+          . .github/scripts/retry.sh
           IMG=lhpc-testlab:ci
-          docker build -t "$IMG" -f .devcontainer/Dockerfile .devcontainer
+          backoff docker build -t "$IMG" -f .devcontainer/Dockerfile .devcontainer
           echo "IMG=$IMG" >> "$GITHUB_ENV"
 
+      - name: Prove the mirror is complete (no network)
+        run: |
+          docker run --rm --network none -v "$PWD/srcmirror":/srcmirror:ro \
+            -v "$PWD/.github/scripts":/scripts:ro "$IMG" python3 /scripts/mirror-sources.py verify /srcmirror
+
       - name: Run the lab lanes in the image
         # Root copies the checkout in and DROPS to the unprivileged lab user — the
         # same privilege boundary users get (uid mapping of -v mounts is not assumed).
         run: |
           docker run --rm --user root -v "$PWD":/repo:ro \
-            -v lhpc_lab_tmp:/tmp \
+            -v "$PWD/srcmirror":/srcmirror:ro -v lhpc_lab_tmp:/tmp \
             "$IMG" bash -lc '
               set -e
               cp -r /repo /tmp/repo && chown -R lhpclab /tmp/repo
@@ -63,19 +84,26 @@ jobs:
                 set -e
                 cd /tmp/repo
                 export LHPC_SYSTEM_PROVIDER=lhpc_testlab.provider:build
+                # Clones resolve to the mirrors. A file in HOME, because the product runner
+                # forwards HOME to git and drops GIT_CONFIG_GLOBAL (mirror-sources.py).
+                cp /srcmirror/gitconfig ~/.gitconfig
                 python3 -m venv /tmp/venv && . /tmp/venv/bin/activate
                 # Sourced from the COPY inside the container: a helper sourced in the host shell
                 # does not survive docker run, let alone the su below.
                 . /tmp/repo/.github/scripts/retry.sh
-                retry 3 pip install -q -e .            # the product
-                retry 3 pip install -q -e ./testlab    # the SEPARATE lab package
-                retry 3 pip install -q pytest playwright
-                retry 3 python -m playwright install chromium
+                backoff pip install -q -e .            # the product
+                python3 .github/scripts/mirror-sources.py resolved /srcmirror
+                backoff pip install -q -e ./testlab    # the SEPARATE lab package
+                backoff pip install -q pytest playwright
+                backoff python -m playwright install chromium
                 # A virtual display for the ACCEPTANCE lane only: voice and sideband are
                 # GTK/Kivy programs, and without one the lab would skip starting them
                 # instead of proving they start. The browser lane below runs with no
                 # DISPLAY at all — headless Chromium needs no desktop.
                 (Xvfb :99 -screen 0 1280x800x24 >/dev/null 2>&1 &)
+                # Its socket, before the lanes rely on it (60 x 0.5 s).
+                n=0; until [ -S /tmp/.X11-unix/X99 ] || [ \$n -ge 60 ]; do sleep 0.5; n=\$((n+1)); done
+                [ -S /tmp/.X11-unix/X99 ] || { echo Xvfb did not open its socket; exit 1; }
                 python -m pytest testlab/tests/unit -q
                 # LHPC_CHAIN_HEAVY=1: this runner is NATIVE aarch64 (not nested emulation),
                 # so the binary meshcom (qemu-system-xtensa) and meshtastic START legs can
@@ -130,12 +158,33 @@ jobs:
         with:
           persist-credentials: false
 
+      - name: Acquire the upstream sources the lanes clone (mirrors, transfer backoff)
+        # Class A of the rule in docs/testlab.md: the lane itself reaches no git remote. Each
+        # mirror is fetched here with the transfer backoff; the lane clones through the
+        # `insteadOf` lines of srcmirror/gitconfig, copied to the lab user's ~/.gitconfig, so the
+        # product's own clone code runs unchanged.
+        run: |
+          . .github/scripts/retry.sh
+          mkdir -p srcmirror
+          python3 .github/scripts/mirror-sources.py list lhpc/data/manifest.example.toml > srcmirror/remotes.txt
+          while read -r remote; do
+            dir="srcmirror/$(python3 .github/scripts/mirror-sources.py name "$remote")"
+            backoff sh -c 'rm -rf "$1" && git clone --quiet --mirror "$2" "$1"' _ "$dir" "$remote"
+          done < srcmirror/remotes.txt
+          python3 .github/scripts/mirror-sources.py config srcmirror/remotes.txt /srcmirror > srcmirror/gitconfig
+
       - name: Build the lab image from THIS commit's Dockerfile
         run: |
+          . .github/scripts/retry.sh
           IMG=lhpc-testlab:ci
-          docker build -t "$IMG" -f .devcontainer/Dockerfile .devcontainer
+          backoff docker build -t "$IMG" -f .devcontainer/Dockerfile .devcontainer
           echo "IMG=$IMG" >> "$GITHUB_ENV"
 
+      - name: Prove the mirror is complete (no network)
+        run: |
+          docker run --rm --network none -v "$PWD/srcmirror":/srcmirror:ro \
+            -v "$PWD/.github/scripts":/scripts:ro "$IMG" python3 /scripts/mirror-sources.py verify /srcmirror
+
       # The lane's script is written to a FILE and run, never nested inside quoted strings.
       # It was nested twice before, and both times a quote in a COMMENT broke it: an apostrophe
       # ended the outer single-quoted string and the job died, and a double quote ended the
@@ -152,11 +201,15 @@ jobs:
           set -e
           cd /tmp/repo
           export LHPC_SYSTEM_PROVIDER=lhpc_testlab.provider:build
+          # Clones resolve to the mirrors. A file in HOME, because the product runner forwards
+          # HOME to git and drops GIT_CONFIG_GLOBAL (mirror-sources.py).
+          cp /srcmirror/gitconfig ~/.gitconfig
           python3 -m venv /tmp/venv && . /tmp/venv/bin/activate
           . /tmp/repo/.github/scripts/retry.sh
-          retry 3 pip install -q -e .
-          retry 3 pip install -q -e ./testlab
-          retry 3 pip install -q pytest
+          backoff pip install -q -e .
+          python3 .github/scripts/mirror-sources.py resolved /srcmirror
+          backoff pip install -q -e ./testlab
+          backoff pip install -q pytest
           # -x: STOP AT THE FIRST FAILURE. The lane chains stacks over one radio pair, so a
           # case that fails leaves the run in a state later cases cannot be judged in — and the
           # automated release reads this JUnit to decide which stack to freeze. Continuing
@@ -171,7 +224,7 @@ jobs:
             python -m pytest testlab/tests/release -q -rs -x \
               --junitxml=/out/junit-release.xml
           LANE
-          docker run --rm --user root -v "$PWD":/repo:ro \
+          docker run --rm --user root -v "$PWD":/repo:ro -v "$PWD/srcmirror":/srcmirror:ro \
             -v lhpc_release_tmp:/tmp -v "$PWD/releaseevidence":/out \
             "$IMG" bash -lc '
               set -e
diff --git a/CHANGELOG.md b/CHANGELOG.md
index ba902936..75a7ec14 100644
--- a/CHANGELOG.md
+++ b/CHANGELOG.md
@@ -10,6 +10,8 @@ Fixed
   `config/secrets/` and is part of `lhpc secrets backup`.
 - MeshCore repeater dashboard: the radio presets list loads (it reported "Presets file not found"); radio settings
   are still changed in LHPC's Settings.
+- The test lab's testlab and release-verify lanes clone upstream sources from mirrors fetched before the run, and
+  what those jobs download before the lane is retried for up to 30 minutes, so a short outage there no longer fails them.
 - The slow-target release check times the download of the Graywolf release the manifest pins and no longer asks
   GitHub's API for the newest one, so the API's rate limit can no longer stop a release candidate (the download itself
   still comes from GitHub).
diff --git a/docs/testlab.md b/docs/testlab.md
index c724258f..4ba72165 100644
--- a/docs/testlab.md
+++ b/docs/testlab.md
@@ -114,6 +114,23 @@ LHPC_SLOW_BUILD=1 SLOW_CPUS=0.25 SLOW_MEM=416m SLOW_WRITE_IOPS=120 SLOW_READ_IOP
 subprocesses import `lhpc` from the editable install, i.e. whichever checkout was
 `pip install -e`'d, and can pass on another checkout's code. CI installs from the commit itself.
 
+**What a CI lane may fetch.** A lane reaches an external service only where that service is what it
+proves. Everything else is acquired before the lane's tests run, with the transfer backoff
+(`backoff` in `.github/scripts/retry.sh`: retry at once, then after 1, 2, 5 and 10 min, then
+every 10 min until the waits would pass 30 min): the lab image and the upstream git sources of the
+testlab and release-verify lanes in workflow steps of their own, the lab's Python packages and
+Chromium at the start of the lane's step. The sources are git mirrors
+(`.github/scripts/mirror-sources.py`): the lane copies a git config with one `insteadOf` per
+remote to the lab user's `~/.gitconfig`, so the product's own, unchanged `git clone` resolves to
+them. It is a file in `HOME` because the product's command runner passes `HOME` to git and drops
+`GIT_CONFIG_GLOBAL`. A step with no network proves every listed remote resolves locally, and the
+lane proves, through that runner, that each one resolves to its mirror before its tests start;
+the list is every manifest remote but the few that script names, each with its reason. A
+published binary, a pinned release download and a build step's packages are still fetched in
+the lane, as the product fetches them. A deterministic
+failure of an acquisition (a broken Dockerfile, say) turns red only after the backoff's waits,
+about half an hour.
+
 `testlab/tests/unit` tests the simulator itself; the four opt-in lanes:
 
 - **acceptance** drives the running server and the real `lhpc` executable and verifies the
diff --git a/tests/repo/test_mirror_sources.py b/tests/repo/test_mirror_sources.py
new file mode 100644
index 00000000..25e0b756
--- /dev/null
+++ b/tests/repo/test_mirror_sources.py
@@ -0,0 +1,156 @@
+"""`.github/scripts/mirror-sources.py`: the lanes' git remotes are served from local mirrors.
+
+The workflow fetches the mirrors before the lane and proves them complete with no network; these
+cases pin what that proof stands on: the remote list derived from the manifest, the exact git
+configuration the lane writes to ~/.gitconfig, that a clone through it resolves locally while a
+remote without a mirror fails, and that the redirection survives the product's own command
+runner, which keeps HOME and drops GIT_CONFIG_GLOBAL. A new invariant (new tooling): negative
+cases instead of a parent-red.
+"""
+import os
+import subprocess
+import sys
+
+import pytest
+
+import repo_paths
+
+SCRIPT = repo_paths.REPO / ".github" / "scripts" / "mirror-sources.py"
+MANIFEST = repo_paths.REPO / "lhpc" / "data" / "manifest.example.toml"
+GIT = ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", "-c", "commit.gpgsign=false"]
+
+
+def _run(*args, **kw):
+    return subprocess.run([sys.executable, str(SCRIPT), *map(str, args)], capture_output=True,
+                          text=True, check=False, **kw)
+
+
+# The 14 remotes the testlab and release-verify lanes cloned in green run 37426359349 (their
+# `[clone] … from <remote>` lines): the derived list must cover at least these.
+CLONED_IN_A_GREEN_RUN = {
+    f"https://github.com/{r}.git" for r in (
+        "makrohard/LoRaHAM_Daemon", "makrohard/loraham-kiss-tnc", "makrohard/LoRaHAM_Voice",
+        "markqvist/LXMF", "liamcottle/reticulum-meshchat", "makrohard/meshcom-qemu-raspi",
+        "meshcore-dev/meshcore-cli", "openhop-dev/openhop_core", "adradr/meshcore-webui",
+        "markqvist/NomadNet", "openhop-dev/openhop_repeater", "markqvist/Reticulum",
+        "makrohard/loraham-rns-interface", "markqvist/Sideband")}
+
+
+def _manifest(tmp_path, components):
+    """A manifest of one stack whose components are `{id: remote}`."""
+    comps = "".join(f'[[stack.component]]\nid = "{c}"\n[stack.component.source]\nremote = "{r}"\n'
+                    for c, r in components.items())
+    path = tmp_path / "m.toml"
+    path.write_text('[[stack]]\nid = "s"\n' + comps)
+    return path
+
+
+def _not_mirrored():
+    import importlib.util
+    spec = importlib.util.spec_from_file_location("mirror_sources", SCRIPT)
+    mod = importlib.util.module_from_spec(spec)
+    spec.loader.exec_module(mod)
+    return mod.NOT_MIRRORED
+
+
+def test_the_mirror_list_covers_every_remote_the_lanes_cloned():
+    r = _run("list", MANIFEST)
+    remotes = r.stdout.split()
+    assert r.returncode == 0 and len(remotes) == len(set(remotes))
+    assert CLONED_IN_A_GREEN_RUN <= set(remotes)
+
+
+def _excluded_components():
+    return {c: f"https://example.invalid/excluded/{c}.git" for c in sorted(_not_mirrored())}
+
+
+def test_a_remote_added_to_the_manifest_is_mirrored_by_default(tmp_path):
+    new = "https://example.invalid/owner/new-stack.git"
+    manifest = _manifest(tmp_path, {**_excluded_components(), "new-stack": new})
+    assert _run("list", manifest).stdout.split() == [new]
+
+
+def test_a_new_component_on_an_excluded_components_remote_is_mirrored(tmp_path):
+    """The twin: exclusion is by component, so a lane component reusing the remote of an
+    excluded one (here the meshtastic firmware) does not escape the mirror."""
+    reused = "https://github.com/meshtastic/firmware.git"
+    assert reused not in _run("list", MANIFEST).stdout.split()
+    manifest = tmp_path / "m.toml"
+    manifest.write_text(MANIFEST.read_text() + '\n[[stack]]\nid = "new"\n[[stack.component]]\n'
+                        f'id = "new-firmware"\n[stack.component.source]\nremote = "{reused}"\n')
+    assert reused in _run("list", manifest).stdout.split()
+
+
+def test_an_exclusion_the_manifest_no_longer_names_is_an_error(tmp_path):
+    excluded = _excluded_components()
+    gone = sorted(excluded)[0]
+    r = _run("list", _manifest(tmp_path, {c: u for c, u in excluded.items() if c != gone}))
+    assert r.returncode == 1 and r.stdout == "" and gone in r.stderr
+
+
+def test_the_git_config_is_exact(tmp_path):
+    remotes = tmp_path / "remotes.txt"
+    remotes.write_text("https://github.com/a/b.git\nhttps://example.invalid/c\n")
+    assert _run("config", remotes, "/srcmirror").stdout == (
+        "[safe]\n\tdirectory = *\n"
+        '[url "file:///srcmirror/github.com_a_b.git"]\n\tinsteadOf = https://github.com/a/b.git\n'
+        '[url "file:///srcmirror/example.invalid_c.git"]\n\tinsteadOf = https://example.invalid/c\n')
+
+
+def _mirrored(tmp_path, remote):
+    """A mirror directory as the acquisition step leaves it, serving one commit for `remote`."""
+    upstream = tmp_path / "upstream"
+    subprocess.run([*GIT, "init", "-q", str(upstream)], check=True)
+    (upstream / "f").write_text("x\n")
+    subprocess.run([*GIT, "-C", str(upstream), "add", "f"], check=True)
+    subprocess.run([*GIT, "-C", str(upstream), "commit", "-q", "-m", "c"], check=True)
+    mirror = tmp_path / "srcmirror"
+    mirror.mkdir()
+    name = _run("name", remote).stdout.strip()
+    subprocess.run(["git", "clone", "-q", "--mirror", str(upstream), str(mirror / name)], check=True)
+    (mirror / "remotes.txt").write_text(remote + "\n")
+    (mirror / "gitconfig").write_text(_run("config", mirror / "remotes.txt", mirror).stdout)
+    return mirror
+
+
+def test_a_clone_through_the_config_resolves_to_the_mirror_and_a_missing_one_fails(tmp_path):
+    # `.invalid` never resolves: only the `insteadOf` rewrite can make this clone succeed.
+    remote = "https://example.invalid/owner/up.git"
+    mirror = _mirrored(tmp_path, remote)
+    ok = _run("verify", mirror)
+    assert ok.returncode == 0 and ok.stdout == f"ok  {remote}\n"
+    (mirror / "remotes.txt").write_text(remote + "\nfile:///nonexistent/not-mirrored.git\n")
+    bad = _run("verify", mirror)
+    assert bad.returncode == 1 and "NOT MIRRORED  file:///nonexistent/not-mirrored.git" in bad.stdout
+
+
+# The product clones with RealCommandRunner, whose children get `_FIXED_ENV` plus the call's own
+# env: the lane's config reaches git only through what that runner forwards.
+RUNNER_CLONE = ("import sys; from lhpc.core.probes.backends import RealCommandRunner; "
+                "sys.exit(RealCommandRunner().run(['git', 'clone', '-q', *sys.argv[1:]], 60)"
+                ".returncode)")
+
+
+@pytest.mark.parametrize("via, redirected", [("home", True), ("git_config_global", False)])
+def test_only_a_gitconfig_in_home_redirects_a_clone_through_the_products_runner(
+        tmp_path, via, redirected):
+    """Run 38823954555 passed with GIT_CONFIG_GLOBAL exported, but the runner drops it, so the
+    product's clones never used the mirrors. The remote does not exist: only the redirection can
+    make the clone succeed, and no network is touched either way."""
+    remote = "file:///nonexistent/upstream/up.git"
+    mirror = _mirrored(tmp_path, remote)
+    home = tmp_path / "home"
+    home.mkdir()
+    env = {"PATH": os.environ["PATH"], "PYTHONPATH": str(repo_paths.REPO), "HOME": str(home)}
+    if via == "home":
+        (home / ".gitconfig").write_text((mirror / "gitconfig").read_text())
+    else:
+        env["GIT_CONFIG_GLOBAL"] = str(mirror / "gitconfig")
+    clone = subprocess.run([sys.executable, "-c", RUNNER_CLONE, remote, str(tmp_path / "co")],
+                           env=env, capture_output=True, text=True, check=False)
+    assert (clone.returncode == 0) is redirected
+    assert (tmp_path / "co" / "f").is_file() is redirected
+    check = _run("resolved", mirror, env=env)
+    expected = (f"ok  {remote}\n" if redirected
+                else f"NOT REDIRECTED  {remote}  (resolves to {remote})\n")
+    assert (check.returncode, check.stdout) == (0 if redirected else 1, expected), check.stderr
diff --git a/tests/repo/test_workflow_shell.py b/tests/repo/test_workflow_shell.py
index 5e28c619..4792fc01 100644
--- a/tests/repo/test_workflow_shell.py
+++ b/tests/repo/test_workflow_shell.py
@@ -127,7 +127,7 @@ def test_the_helper_does_not_wrap_a_verdict():
             stripped = line.strip()
             if stripped.startswith("#"):
                 continue
-            m = re.search(r"\bretry\s+\d+\s+(.*)", stripped)
+            m = re.search(r"\b(?:retry\s+\d+|backoff)\s+(.*)", stripped)
             if not m:
                 continue
             argv = m.group(1).split()
@@ -142,3 +142,35 @@ def test_the_helper_does_not_wrap_a_verdict():
                 or (program == "node" and "tests/" in rest)
             )
             assert not verdict, f"{wf.name}:{lineno} retries a verdict: {stripped}"
+
+
+# --- backoff: the transfer backoff in the same helper file ----------------------------------------
+
+def _backoff(script: str, **env):
+    """The helper with `sleep` replaced by a recorder, so the schedule is read, not waited."""
+    return subprocess.run(
+        ["bash", "-c", f'. "{RETRY}"\nsleep() {{ echo "slept $1"; }}\n{script}'],
+        capture_output=True, text=True, env={"PATH": "/usr/bin:/bin", **env},
+    )
+
+
+def test_the_backoff_schedule_is_the_documented_one():
+    """At once, then 1, 2, 5 and 10 min, then every 10 min until the waits would pass 30 min
+    (docs/testlab.md); the command's own status comes back."""
+    r = _backoff('c() { return 7; }; backoff c; echo "status=$?"')
+    assert [ln.split()[1] for ln in r.stdout.splitlines() if ln.startswith("slept ")] == [
+        "0", "60", "120", "300", "600", "600"]
+    assert "status=7" in r.stdout
+    assert r.stderr.count("failed (status 7); retrying in") == 6 and "giving up" in r.stderr
+
+
+def test_a_transfer_that_recovers_is_a_success_after_the_waits():
+    r = _backoff('n=0; c() { n=$((n+1)); [ "$n" -ge 3 ]; }; backoff c; echo "attempts=$n status=$?"')
+    assert "attempts=3 status=0" in r.stdout
+    assert [ln for ln in r.stdout.splitlines() if ln.startswith("slept ")] == ["slept 0", "slept 60"]
+
+
+def test_a_test_schedule_without_a_last_wait_stops_after_the_list():
+    r = _backoff('n=0; c() { n=$((n+1)); return 3; }; backoff c; echo "attempts=$n status=$?"',
+                 BACKOFF_WAITS="0 0", BACKOFF_TOTAL="0")
+    assert "attempts=3 status=3" in r.stdout
```
