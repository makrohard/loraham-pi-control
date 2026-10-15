# Gate 1: testlab-det-4b (fork fix + S1 surface test)

**Request.** Judge the two test-only commits below: (1) the killed-mid-staging case runs its child in a fresh interpreter instead of `os.fork()` (CI's xdist workers are multi-threaded since parallel CI), with the `installer` fixture's builder moved to an importable module the child also uses; (2) the Graywolf upstream check's test asserts the promised surface (summary and stored status: `curl rc <n>` and curl's message, one line, ≤ 200 chars) — finding W1b-5. Judge whether the tests still prove what they claim, the honesty of "no red-before" for (1), and the report against the diff.

This file is your whole input: you have no repository access; use no connector, tool or web lookup.

**Answer form.**

| commit | verdict (OK / FINDING) | what |
|---|---|---|

Then numbered findings (file:line, why it matters, smallest fix), then one final line: GREEN / GREEN WITH NOTES / RED.

Redaction: none needed; the diff is `git diff` output verbatim.

## Commits (full messages, `git log --reverse --format=%B`)

### 0e87103b tests: the killed-mid-staging case runs its child in a fresh interpreter, not os.fork()

```text
tests: the killed-mid-staging case runs its child in a fresh interpreter, not os.fork()

test_recovery_removes_a_clone_killed_before_its_journal forked the pytest process to stage a
source and SIGKILL itself mid-copy. Under `-n auto` (CI since P3a) the xdist worker has an
execnet thread, and Python warns on the fork: "This process (pid=…) is multi-threaded, use of
fork() may lead to deadlocks in the child" (CI run 38819553965, test (3.13)). The child then
runs `adopt_source` (git, file I/O), so a lock held by the other thread at the fork could hang
it: a latent hang in CI, never observed.

The Installer builder of the `installer` fixture moves to tests/install/installers.py (the
fixture now calls it), and the same module, run as a script, is the child:
`installers.py kill-mid-staging <root>` builds the identical installer, stages `app` and is
SIGKILLed right after its copy into the candidate. The test runs it with `subprocess`
(PYTHONPATH = this checkout) and asserts `returncode == -SIGKILL`; the recovery assertions are
unchanged.

No red-before: the defect is a POSSIBLE deadlock, signalled by CI's DeprecationWarning, not a
failure that can be produced on demand (locally the worker is single-threaded at that point and
the old test passes under `-n 4 -W error::DeprecationWarning` too). Green after: 3/3 under
`-n 4 -W error::DeprecationWarning`; tests/install below.

```

### 2ecef9d6 tests: the Graywolf check's kept reason is asserted on the summary and the stored status

```text
tests: the Graywolf check's kept reason is asserted on the summary and the stored status

The S1 test (landed in 254bbae3) asserted only `data["curl_rc"] == 22` and "403" in the stored
error, not the promised surface: the summary and the stored status carry `curl rc <n>` and the
END of curl's message, flattened to ONE line of at most 200 characters (finding W1b-5).

The case now feeds a multi-line message longer than 200 characters and asserts, for both the
result's summary and the kept `error`: `curl rc 22: ` present, curl's last line kept as one
line ("curl: (22) The requested URL returned error: 403"), no newline, and at most 200
characters after `curl rc 22: `.

Red-before: the case run against the tree before S1 (eb9673a4) fails on the summary:
"assert ('curl rc 22: ' in "upstream check failed for 'graywolf': network/API error")".
Green after: tests/stacks/test_graywolf_upstream.py 19 passed.

```

## The report

> # Code report: testlab-det-4b (packet P4b: the fork fix and the S1 surface test)
>
> Branch `cons/testlab-det-4b-r1` on `origin/dev` = `78c9fac7`. Two test-only commits; no product code, no CHANGELOG line (tests change nothing an operator sees). Plan v4 §2 P4: the S1 test (W1b-5), plus the fork-under-xdist risk that P3a's report named and the review order put here.
>
> ## Commits
>
> | commit | files | tests | red-before |
> |---|---|---|---|
> | 0e87103b | tests/install/installers.py (new: the `installer` fixture's builder + the child script), tests/install/conftest.py (the fixture calls it), tests/install/test_source.py | `test_recovery_removes_a_clone_killed_before_its_journal` (install layer): the child runs `installers.py kill-mid-staging <root>` in a fresh interpreter, `returncode == -SIGKILL`, the recovery assertions unchanged | NONE: the defect is a possible deadlock of a forked child in a multi-threaded xdist worker, signalled by CI's DeprecationWarning (run 38819553965, test (3.13)), not producible on demand. Locally the old test passes even under `-n 4 -W error::DeprecationWarning`. Stated in the commit message |
> | 2ecef9d6 | tests/stacks/test_graywolf_upstream.py | `test_a_failed_check_keeps_curls_exit_code_and_message` (stacks layer): for the summary AND the kept `error`: `curl rc 22: `, curl's last line kept as one line, no newline, ≤ 200 chars after the marker | yes, against the tree before S1 (eb9673a4): `assert ('curl rc 22: ' in "upstream check failed for 'graywolf': network/API error")` |
>
> ## HOUSE RULES KEPT (dev's tools/check_houserules.py)
>
> ```
> HOUSE RULES KEPT  origin/dev..HEAD
> changelog    +0 bullets (limit 2, each ≤ 3 lines; longest 0), top section 16 lines   OK
> docs         0 files, +0 lines; no fact given   OK
> tests        1 new tests; layers tests/install=install tests/stacks=stacks; red-before 1/1   OK
> patterns     getsource 0 assert-True 0 source-read 0 sleep 0 startswith 0 network 0   OK
> markers      0 without slow   OK
> attribution  authors makrohard; trailers 0; words 0   OK
> modules      no family file touched; family net +0 lines, +0 public   OK
> VERDICT      PASS  (hard 0, warn 0)
> ```
>
> ## The 6-point block
>
> 1. **Contracts.** `installers.build_installer(root, comp=None, *, extra, search_root, system, values)`: the old fixture body, unchanged in behaviour; the fixture keeps its signature and default root (`tmp_path/"rt"`). `installers.py kill-mid-staging <root>`: exit by SIGKILL after the copy into the candidate, exit 1 if the staging returned.
> 2. **Invariants.** The recovery assertions of the killed-staging case are unchanged (one staged candidate holding `file.txt`, no journal, recovery removes it, a fresh adopt is `done`). The S1 behaviour is unchanged; only its test is stronger.
> 3. **Failure classes.** The child imports lhpc from this checkout (`PYTHONPATH` = the repo), not from whatever is installed (the stale-editable trap). A child that fails to start shows its stderr tail in the assertion. 174 `installer(` uses in tests/install still go through the fixture (`grep -rn "installer(" tests/install/*.py | wc -l`).
> 4. **Test rules.** No private patching in the test any more (the monkeypatch moved into the child script, where it IS the scenario); the S1 case asserts the kept fields, not a sentence.
> 5. **Directories:** `pytest -q -n 8 tests/install tests/stacks tests/repo` → `3481 passed, 1 skipped`; `ruff check lhpc testlab`, `ruff check tests --select F,E9` → `All checks passed!`.
> 6. **Adversarial self-review.** The fork fix has no red-before, said plainly rather than dressed up; the conftest lost 7 now-unused imports (ruff F401).
>
> ## Numbers, measured
>
> | figure | command | output |
> |---|---|---|
> | diff | `git diff --shortstat origin/dev 2ecef9d6` | 4 files changed, 76 insertions(+), 35 deletions(-) |
> | lines | `git show origin/dev:<f> \| wc -l`; `wc -l <f>` | conftest.py 223 → 210; test_source.py 5369 → 5363; test_graywolf_upstream.py 402 → 407; installers.py 55 (new) |
> | tests | the pytest command above | 3481 passed, 1 skipped |

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

## The full diff (`git diff origin/dev 2ecef9d6`)

```diff
diff --git a/tests/install/conftest.py b/tests/install/conftest.py
index 555073bc..175884de 100644
--- a/tests/install/conftest.py
+++ b/tests/install/conftest.py
@@ -16,10 +16,8 @@ from pathlib import Path
 import pytest
 
 import gitrepo
+import installers
 from lhpc.core import selfupdate
-from lhpc.core.config import Config
-from lhpc.core.install import Installer
-from lhpc.core.model import Component, ComponentKind, SourceSpec, Stack
 from lhpc.core.paths import Paths
 from lhpc.core.probes.backends import RealSystem
 
@@ -49,13 +47,6 @@ def make_repo(git):
     return _make
 
 
-def _app_component():
-    """The plain managed-source component the install suites adopt: `app`, sourced from
-    `src/app`, with a local fallback directory `app` under the adopt search root."""
-    return Component(id="app", name="app", kind=ComponentKind.SERVICE,
-                     source=SourceSpec(path="src/app", local_dir="app"))
-
-
 @pytest.fixture
 def installer(tmp_path):
     """`installer(comp=None, *, extra=(), root=None, search_root=None, system=None, values=None)`
@@ -64,12 +55,9 @@ def installer(tmp_path):
     `extra`. `search_root` is the adopt search root (default `<root>/local`); `values` are
     further config values merged beside it."""
     def _build(comp=None, *, extra=(), root=None, search_root=None, system=None, values=None):
-        root = Path(root) if root is not None else tmp_path / "rt"
-        comp = comp or _app_component()
-        cfg = Config(values={"install": {"adopt_search_root": str(search_root or root / "local")},
-                             **(values or {})})
-        stacks = (Stack(id="s", name="s", main=comp.id, components=(comp, *extra)),)
-        return Installer(Paths(runtime_root=root), stacks, cfg, system or RealSystem())
+        return installers.build_installer(root if root is not None else tmp_path / "rt", comp,
+                                          extra=extra, search_root=search_root, system=system,
+                                          values=values)
     return _build
 
 
@@ -105,7 +93,6 @@ def op_svc(tmp_path, monkeypatch):
         import sys
 
         from lhpc.core import selfupdate, updater_units
-        from lhpc.core.paths import Paths
         from lhpc.core.probes.backends import CommandResult, FakeSystem
         from lhpc.core.services import ControllerService
 
diff --git a/tests/install/installers.py b/tests/install/installers.py
new file mode 100644
index 00000000..97f6e1e4
--- /dev/null
+++ b/tests/install/installers.py
@@ -0,0 +1,55 @@
+"""The `Installer` the install suites build (the `installer` fixture of conftest.py), importable,
+so a test can build the very same one in a FRESH interpreter:
+
+    python tests/install/installers.py kill-mid-staging <runtime root>
+
+stages the `app` source and is SIGKILLed right after its copy into the candidate, before any
+journal exists (a box losing power mid-clone). A fresh interpreter, not `os.fork()`: under
+pytest-xdist the worker process has threads, and a forked child of a multi-threaded process can
+deadlock on a lock another thread held at the fork.
+"""
+from __future__ import annotations
+
+import os
+import signal
+import sys
+from pathlib import Path
+
+from lhpc.core.config import Config
+from lhpc.core.install import Installer
+from lhpc.core.model import Component, ComponentKind, SourceSpec, Stack
+from lhpc.core.paths import Paths
+from lhpc.core.probes.backends import RealSystem
+
+
+def app_component():
+    """The plain managed-source component the install suites adopt: `app`, sourced from
+    `src/app`, with a local fallback directory `app` under the adopt search root."""
+    return Component(id="app", name="app", kind=ComponentKind.SERVICE,
+                     source=SourceSpec(path="src/app", local_dir="app"))
+
+
+def build_installer(root, comp=None, *, extra=(), search_root=None, system=None, values=None):
+    root = Path(root)
+    comp = comp or app_component()
+    cfg = Config(values={"install": {"adopt_search_root": str(search_root or root / "local")},
+                         **(values or {})})
+    stacks = (Stack(id="s", name="s", main=comp.id, components=(comp, *extra)),)
+    return Installer(Paths(runtime_root=root), stacks, cfg, system or RealSystem())
+
+
+def _kill_mid_staging(root: Path) -> None:
+    real_copy = Installer._copy_into_candidate
+
+    def copy_then_die(local, cand):           # the one seam inside the staging
+        real_copy(local, cand)
+        os.kill(os.getpid(), signal.SIGKILL)
+    Installer._copy_into_candidate = staticmethod(copy_then_die)
+    build_installer(root).adopt_source(app_component(), source="dev")
+
+
+if __name__ == "__main__":
+    if sys.argv[1:2] != ["kill-mid-staging"] or len(sys.argv) != 3:
+        sys.exit("usage: installers.py kill-mid-staging <runtime root>")
+    _kill_mid_staging(Path(sys.argv[2]))
+    sys.exit(1)                               # the staging returned: it was not killed
diff --git a/tests/install/test_source.py b/tests/install/test_source.py
index 5d564a2a..48202422 100644
--- a/tests/install/test_source.py
+++ b/tests/install/test_source.py
@@ -371,31 +371,25 @@ def test_parent_swap_after_fd_cannot_redirect_clone_outside(tmp_path, git, make_
     assert (moved / ".app.candidate-x" / "MARK").read_text() == "payload"   # landed in held inode
 
 
-def test_recovery_removes_a_clone_killed_before_its_journal(tmp_path, make_repo, installer, monkeypatch):
+def test_recovery_removes_a_clone_killed_before_its_journal(tmp_path, make_repo, installer):
     # Power lost while the candidate was still being staged (a clone may take 15 minutes), before
     # any journal exists: nothing named the partial tree, so it stayed beside the source for good.
     import signal
+    import subprocess
+    import sys
     make_repo(tmp_path / "rt" / "local" / "app")
     comp = _comp()
     inst = installer(comp)
-    real_copy = Installer._copy_into_candidate
-
-    def copy_then_die(local, cand):           # stubs the copy: the one seam inside the staging
-        real_copy(local, cand)
-        os.kill(os.getpid(), signal.SIGKILL)
-    monkeypatch.setattr(Installer, "_copy_into_candidate", staticmethod(copy_then_die))
-    pid = os.fork()
-    if pid == 0:                              # the box: dies mid-staging, no cleanup runs
-        try:
-            inst.adopt_source(comp, source="dev")
-        finally:
-            os._exit(1)
-    _, status = os.waitpid(pid, 0)
-    assert os.WIFSIGNALED(status) and os.WTERMSIG(status) == signal.SIGKILL
+    # The box: the same installer in a fresh interpreter (installers.py), killed mid-staging, no
+    # cleanup runs. Not os.fork(): an xdist worker has threads, and a fork can deadlock there.
+    r = subprocess.run([sys.executable, str(Path(__file__).with_name("installers.py")),
+                        "kill-mid-staging", str(tmp_path / "rt")], capture_output=True, text=True,
+                       timeout=120, check=False,
+                       env={**os.environ, "PYTHONPATH": str(Path(__file__).parents[2])})
+    assert r.returncode == -signal.SIGKILL, r.stderr[-1500:]
     staged = list(inst.paths.under("src").glob(".app.candidate-*"))
     assert len(staged) == 1 and (staged[0] / "file.txt").is_file()
     assert not inst._pending_journals()
-    monkeypatch.undo()
     msgs = inst.recover_source_activations()
     assert not staged[0].exists()
     assert any("removed an interrupted clone" in m for m in msgs), msgs
diff --git a/tests/stacks/test_graywolf_upstream.py b/tests/stacks/test_graywolf_upstream.py
index ad1514bb..1f892f35 100644
--- a/tests/stacks/test_graywolf_upstream.py
+++ b/tests/stacks/test_graywolf_upstream.py
@@ -159,11 +159,16 @@ def test_a_failed_check_keeps_curls_exit_code_and_message(tmp_path, monkeypatch)
     class R:
         returncode = 22
         stdout = ""
-        stderr = "curl: (22) The requested URL returned error: 403\n"
+        # A multi-line message longer than the kept 200 characters: kept as its END, one line.
+        stderr = "x" * 300 + "\ncurl: (22) The requested URL\n  returned error: 403\n"
     monkeypatch.setattr(svc._system.runner, "run", lambda a, timeout=None: R())
     r = svc.graywolf_upstream_check("graywolf")
+    error = svc.graywolf_upstream_state("graywolf")["error"]
+    kept = "curl: (22) The requested URL returned error: 403"
+    for text in (r.summary, error):                 # the result and the status it keeps
+        assert "curl rc 22: " in text and kept in text and "\n" not in text
+        assert len(text.split("curl rc 22: ", 1)[1].rstrip(")")) <= 200
     assert r.ok is False and r.data["curl_rc"] == 22
-    assert "403" in svc.graywolf_upstream_state("graywolf")["error"]
 
 
 def test_transient_check_preserves_the_last_known_version(tmp_path, monkeypatch):
```
