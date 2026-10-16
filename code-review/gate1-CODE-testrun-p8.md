# Gate 1: testrun-p8 v3 (the golden box's TNC endpoint is its own socket)

**Request.** Round 10: the correction of v1 (your round 8: RED, 1 finding: per-box ownership lets a stale TNC of the same box read as a later spawn's readiness); change table at the top of the report. Judge: is the per-box contract stated and enforced (a spawn refused while an earlier TNC of the box lives, by pid + start time); does the regression test prove that refusal and can it fail (its negative case); does the foreign-listener fix of v1 stand unchanged; is any claim stronger than the diff.

This file is your whole input: you have no repository access; use no connector, tool or web lookup.

**Answer form.**

| commit | verdict (OK / FINDING) | what |
|---|---|---|

Then numbered findings (file:line, why it matters, smallest fix), then one final line: GREEN / GREEN WITH NOTES / RED.

Redaction: none needed; the diff is `git diff` output verbatim.

## Commit (full message, `git log -1 --format=%B`)

### 7ecd1fbe testrun-p8: the golden box's TNC endpoint is the box's own socket, not any listener on its port

```text
testrun-p8: the golden box's TNC endpoint is the box's own socket, not any listener on its port

The golden kiss box picked a free loopback port (bind to 0, read it,
release it) and the TNC process bound it later; `_TncEndpoint` took ANY
127.0.0.1 listener on that port for the TNC's. Under a loaded parallel run
another process could take the port in between: a foreign socket then read
as the TNC being ready (or not yet gone), and
test_golden_boot_restore.py::test_failed_after_the_hook_consumes_the_evidence
failed once under -n 12.

The TNC now listens on a port the kernel picks, and `_TncEndpoint` keeps
only a 127.0.0.1 LISTEN socket whose inode one of the box's own TNC
processes holds (found by its argv, which carries the box's runtime root as
a token). The free-port helper and the box's port are gone; entry_host.py
passes the token instead of the port. Ownership is per box, as production's
readiness is an address check; so that the listener seen is always the one
live TNC's, `KissBox.spawn` refuses a spawn while an earlier TNC of the box
still lives (pid + start time, the harness's identity model).

Red-before: tests/golden/test_harness.py::
test_a_foreign_loopback_listener_is_never_the_tncs_endpoint, run against
the parent d77e34f4: FAILED (a foreign listener on the box's port was
reported as Listener(..., port=8001)); passes after. New invariant of the
harness, with its negative case: tests/golden/test_harness.py::
test_a_later_spawn_is_refused_while_an_earlier_tnc_of_the_box_lives
(against the v1 harness of this branch: "DID NOT RAISE"). tests/golden:
63 passed.

```

## The report

> # Code report: testrun-p8 v3 (the golden box's TNC endpoint is its own socket)
>
> ## Changes v1 → v3 (round 8 verdict, RED, 1 finding; accepted)
>
> | finding | change |
> |---|---|
> | ownership per box root: a stale, still-listening TNC of the same box could make a later spawn read ready | the contract is stated (ownership per box, as production's readiness is an address check) and enforced: `KissBox.spawn` refuses a spawn while an earlier TNC of the box still lives (pid + start time, the harness's `_alive`/`_starttime`); regression test `test_a_later_spawn_is_refused_while_an_earlier_tnc_of_the_box_lives`. No golden spawns while an earlier TNC lives (all 63 pass with the refusal in place) |
>
> (v2 changed the report only: the second observed instance below.)
>
> Branch `cons/testrun-p8-r1` on `origin/dev` = `d77e34f4`. Found under load by another run (a pre-existing flake);
> the handler put it in the X3 test run as its own packet (no planned packet owns `tests/golden/`). Tests only.
>
> ## Commit
>
> | commit | files | tests | red-before |
> |---|---|---|---|
> | 7ecd1fbe | tests/golden/conftest.py, tests/golden/entry_host.py, tests/golden/test_harness.py | golden layer: `test_a_foreign_loopback_listener_is_never_the_tncs_endpoint` asserts `list(box.fake.listeners) == []` (the endpoint the box's `FakeSystem` reports) while a foreign process listens on 127.0.0.1; `test_a_later_spawn_is_refused_while_an_earlier_tnc_of_the_box_lives` asserts the second `spawn` raises and only one process was started | yes: the new test against the parent's `conftest.py` + `entry_host.py` (d77e34f4): `1 failed` — `Left contains one more item: Listener(family='ipv4', ip='127.0.0.1', port=8001, …)`; after: passed. The second test is a new invariant of the harness; its negative case: against the v1 harness (f42abc17's conftest.py) it fails, `DID NOT RAISE AssertionError` |
>
> ## HOUSE RULES KEPT (tools/check_houserules.py origin/dev..HEAD, before the report commit)
>
> ```
> HOUSE RULES KEPT  origin/dev..HEAD
> changelog    +0 bullets (limit 2, each ≤ 3 lines; longest 0), top section 20 lines   OK
> docs         0 files, +0 lines; no fact given   OK
> tests        2 new tests; layers tests/golden=golden; red-before 1/1   OK
> patterns     getsource 0 assert-True 0 source-read 0 sleep 0 startswith 0 network 0   OK
> markers      0 without slow   OK
> attribution  authors makrohard; trailers 0; words 0   OK
> modules      no family file touched; family net +0 lines, +0 public   OK
> VERDICT      PASS  (hard 0, warn 0)
> ```
>
> ## The defect and the change
>
> - **Before.** `kiss_box` called `_free_port()` (bind 127.0.0.1:0, read the port, close), and the TNC process bound
>   that port later, at its spawn. `_TncEndpoint(port)` kept every LISTEN socket on 127.0.0.1:`port`. In the window
>   between the close and the TNC's bind, or while the TNC never listens (`endpoint_never_up`), another process may
>   listen on that port; the box then read it as the TNC's endpoint: a start could verify ready on a foreign socket,
>   a stop could see an endpoint that is not the TNC's.
> - **After.**
>   - The TNC binds `('127.0.0.1', 0)` itself (argv[1] `listen`), so no port is chosen in advance.
>   - `_TncEndpoint(token)` keeps a 127.0.0.1 LISTEN socket only when its inode is in `_tnc_socket_inodes(token)`:
>     the socket fds of the live processes whose argv is `[python, -c, _TNC, <listen>, <term>, <token>]`, the token
>     being the box's runtime root.
>   - It is still reported as port 8001 (the manifest's), the only translation, as before.
>   - `_free_port`, `KissBox.port` and `host.json`'s `port` are removed; `entry_host.py` rebuilds the endpoint from
>     `d["root"]`. Across processes this finds the same TNCs, because they are matched by argv, not by a parent's
>     pid list.
> - **A second observed instance** (after the first, in a loaded local `-n 12` run): CI run 38827223542 on the
>   dev tip, job `test (3.13)`: `tests/golden/test_same_decision_across_entry_paths.py:282` AssertionError, the
>   start reporting `loraham-kiss-tnc: ready endpoint(s) never came up (127.0.0.1:8001: absent (family=ipv4))`
>   (1 failed, 7672 passed; 3.11 and 3.12 green). An absent endpoint fits the race's other half: the port taken
>   between `_free_port()`'s release and the TNC's bind, so the TNC's bind fails and it never listens; this change
>   removes that half too (no port is chosen in advance). Not excluded from the log alone: a TNC that bound but
>   took longer than the box's 3 s endpoint wait to start under load (its stderr goes to /dev/null); this change
>   does not alter that wait.
> - **One TNC per box.** Ownership is per box (the token), not per spawn: as in production, where readiness is
>   an address check, whatever TNC of the box listens counts. So that this listener is always the one live TNC's,
>   `KissBox.spawn` asserts that no earlier TNC of the box (pid with its recorded start time) is alive before it
>   starts another. No golden reaches that state today (the survives-SIGTERM cases end without a new spawn); a
>   future one fails loudly at the spawn instead of reading the old listener as the new TNC's.
> - Readiness and cessation stay observed: the listener exists only while the TNC process holds it; a TNC that
>   never listens or has exited is absent.
>
> ## The 6-point block
>
> 1. **Contracts (test harness):**
>    - `_TncEndpoint(token)` iterates `Listener`s (port 8001) held by the box's TNC processes.
>    - `_tnc_socket_inodes(token) -> set[int]`.
>    - `KissBox(root, fake, svc)` lost its `port` argument; both callers are updated (`grep -rn 'KissBox(' tests` →
>      conftest.py, entry_host.py).
> 2. **Invariants:** a foreign loopback listener is never the box's endpoint; one box never runs two TNCs at once
>    (the two new tests). Readiness through the box's own TNC is still proven by every start golden that verifies
>    ready (tests/golden: 63 passed).
> 3. **Failure classes:**
>    - A process that exits between the `/proc` listing and the fd read raises `OSError`; it is skipped.
>    - A process of another user cannot be read: `OSError`, skipped (a TNC is always ours).
>    - Two boxes in parallel workers never share a token: each has its own tmp root.
> 4. **Test rules:** rule 4 (no ambient host dependency: a host listener can no longer change a golden's verdict);
>    rule 12 (red before, shown); the case is tagged `intended:` as the golden docstring rule requires.
> 5. **Directories:** `setsid -w python -m pytest -q -p no:cacheprovider -n 12 --dist loadfile tests/golden tests/repo`
>    → `641 passed, 1 skipped in 33.24s`; `ruff check lhpc testlab` and `ruff check tests --select F,E9` → `All checks
>    passed!`.
> 6. **Adversarial self-review:**
>    - (a) The new test binds `getattr(box, "port", 0)`: on the parent it takes the box's chosen port (the race made
>      certain), after the fix any free port. Either way a foreign listener exists while the box's TNC is not
>      running; the assertion is the same.
>    - (b) The `import socket` the harness no longer uses was removed (ruff F401).
>    - (c) Not shown: the original flake itself under load (it needs the race to hit). The red-before reproduces its
>      mechanism deterministically instead; the CI instance above is consistent with it but not proven to be it.
>
> ## Numbers, measured
>
> | figure | command | output |
> |---|---|---|
> | base | `git log -1 --format=%h origin/dev` | d77e34f4 |
> | diff | `git diff --stat origin/dev 7ecd1fbe` | 3 files changed, 87 insertions(+), 31 deletions(-) |
> | red-before | `git checkout d77e34f4 -- tests/golden/conftest.py tests/golden/entry_host.py; pytest tests/golden/test_harness.py -k foreign` | 1 failed |
> | after | the same with the branch's files | 1 passed |
> | directories | the command in 5 | 641 passed, 1 skipped |
> | new-invariant negative | v1 harness + `pytest tests/golden/test_harness.py -k later_spawn` | 1 failed (DID NOT RAISE) |

## The full diff (`git diff d77e34f4c775d85ba0822b373ec4243651be5cc9 7ecd1fbe9904c5126f143d6db39303572b04ad55`)

```diff
diff --git a/tests/golden/conftest.py b/tests/golden/conftest.py
index 2df02380..96215171 100644
--- a/tests/golden/conftest.py
+++ b/tests/golden/conftest.py
@@ -15,7 +15,7 @@ the kernel), its cessation the real one. The substitutions, every one by name:
 - `ControllerService._lifecycle` (`kiss_box`): the real `Lifecycle` with the box's spawn
   callable, which launches the TNC process instead of the component binary;
 - the `System`: a `FakeSystem` (daemon CONF socket answering READY; `listeners` =
-  `_TncEndpoint`, the kernel's table filtered to the TNC's port, reported as 8001), and the
+  `_TncEndpoint`, the kernel's table filtered to the box's own TNC sockets, reported as 8001), and the
   host data a scenario puts into it: process-table entries (`cmdlines_data`: a band owner, an
   unowned TNC, a running chat) and the build steps' command results (`commands`, build);
 - two bounded waits, shortened: `ControllerService.ENDPOINT_VERIFY_TIMEOUT_S` 3 s (production
@@ -42,7 +42,6 @@ import json
 import os
 import re
 import signal
-import socket
 import subprocess
 import sys
 from pathlib import Path
@@ -290,16 +289,17 @@ def _tagged(request):
 
 _READY = b"STATUS RADIO=READY TX=0 TXMODE=MANAGED CADWAIT=1500 CADRSSI=-90\n"
 
-# The TNC process the box spawns: argv[1] is the loopback port it listens on (0: it never opens
-# one), argv[2] "ignore" makes it ignore SIGTERM (a process that really outlives its stop signal).
+# The TNC process the box spawns: argv[1] "listen" opens a loopback listener on a port the kernel
+# picks (anything else: it never opens one), argv[2] "ignore" makes it ignore SIGTERM (a process
+# that really outlives its stop signal), argv[3] is the box's token (its runtime root), by which
+# `_TncEndpoint` finds the box's own TNC processes.
 _TNC = ("import signal, socket, sys, time\n"
-        "port, term = int(sys.argv[1]), sys.argv[2]\n"
+        "listen, term = sys.argv[1], sys.argv[2]\n"
         "if term == 'ignore':\n"
         "    signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
-        "if port:\n"
+        "if listen == 'listen':\n"
         "    s = socket.socket()\n"
-        "    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)\n"
-        "    s.bind(('127.0.0.1', port))\n"
+        "    s.bind(('127.0.0.1', 0))\n"
         "    s.listen()\n"
         "time.sleep(300)\n")
 
@@ -321,35 +321,55 @@ def _starttime(pid: int) -> str | None:
         return None
 
 
-def _free_port() -> int:
-    with socket.socket() as s:
-        s.bind(("127.0.0.1", 0))
-        return s.getsockname()[1]
+def _tnc_socket_inodes(token: str) -> set[int]:
+    """The socket inodes held by the live TNC processes of the box `token` names (found by their
+    argv: the `_TNC` program with that token), from `/proc/<pid>/fd`."""
+    inodes: set[int] = set()
+    for pid in filter(str.isdigit, os.listdir("/proc")):
+        try:
+            with open(f"/proc/{pid}/cmdline", "rb") as fh:
+                argv = fh.read().split(b"\0")
+            if len(argv) < 6 or argv[2] != _TNC.encode() or argv[5] != token.encode():
+                continue
+            for fd in os.listdir(f"/proc/{pid}/fd"):
+                m = re.fullmatch(r"socket:\[(\d+)\]", os.readlink(f"/proc/{pid}/fd/{fd}"))
+                if m:
+                    inodes.add(int(m.group(1)))
+        except OSError:                              # gone meanwhile, or not ours to read
+            continue
+    return inodes
 
 
 class _TncEndpoint:
     """The TNC's ready listener as the kernel reports it: the host's real `/proc/net/tcp` table
     (ipv4 — the endpoint is 127.0.0.1), parsed by the production parser `parse_proc_net_tcp` (the
-    suite's `_no_host_processes` blanks `RealProcFs.tcp_listeners` itself), keeping only the LISTEN
-    socket on the loopback port this box's TNC process binds — no other host listener is seen.
-    The manifest pins the endpoint to 127.0.0.1:8001 and a test cannot own a fixed host port, so
-    that one port number is reported as 8001 — the only translation; whether the listener exists,
-    its host and its LISTEN state are observed. A TNC that never opens its port, or one whose
-    socket closed with its process, is simply absent."""
-
-    def __init__(self, port: int):
-        self.port = port
+    suite's `_no_host_processes` blanks `RealProcFs.tcp_listeners` itself), keeping only a LISTEN
+    socket on 127.0.0.1 that one of this box's own TNC processes holds (its inode is in that
+    process's fds) — no other host listener is seen, whatever its port. The manifest pins the
+    endpoint to 127.0.0.1:8001 and a test cannot own a fixed host port, so the TNC listens on a
+    port the kernel picks and it is reported as 8001 — the only translation; whether the listener
+    exists, its host and its LISTEN state are observed. A TNC that never opens its port, or one
+    whose socket closed with its process, is simply absent.
+
+    Ownership is per box, not per spawn, as in production, where readiness is an address check
+    (whatever process listens on 127.0.0.1:8001 counts). Two TNCs of one box never run at once:
+    `KissBox.spawn` refuses a spawn while an earlier TNC of the box still lives, so the listener
+    seen is always the one live TNC's."""
+
+    def __init__(self, token: str):
+        self.token = token
 
     def __iter__(self):
         with open("/proc/net/tcp", encoding="ascii", errors="replace") as fh:
             table = parse_proc_net_tcp(fh.read(), "ipv4")
+        own = _tnc_socket_inodes(self.token)
         return iter([dataclasses.replace(lst, port=8001) for lst in table
-                     if (lst.family, lst.ip, lst.port) == ("ipv4", "127.0.0.1", self.port)])
+                     if (lst.family, lst.ip) == ("ipv4", "127.0.0.1") and lst.inode in own])
 
 
 class KissBox:
-    def __init__(self, root: Path, fake: FakeSystem, svc: ControllerService, port: int):
-        self.root, self.fake, self.svc, self.port = root, fake, svc, port
+    def __init__(self, root: Path, fake: FakeSystem, svc: ControllerService):
+        self.root, self.fake, self.svc = root, fake, svc
         self.listens, self.term = True, "default"
         self.spawned: list[tuple[int, str | None, str]] = []   # (pid, start time, component)
 
@@ -366,9 +386,13 @@ class KissBox:
         session, so it is an LHPC-ownable session leader); the log path is created so callers
         that read it work."""
         def _spawn(argv, log, cwd=None, env=None):
+            live = [pid for pid, start, _c in self.spawned if _alive(pid) and _starttime(pid) == start]
+            assert not live, (f"the box would run a second TNC while {live} still lives: its "
+                              "listener would read as the new spawn's (one TNC per box)")
             open(str(log), "a").close()
             p = subprocess.Popen(
-                [sys.executable, "-c", _TNC, str(self.port if self.listens else 0), self.term],
+                [sys.executable, "-c", _TNC, "listen" if self.listens else "no", self.term,
+                 str(self.root)],
                 start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
             procs.append(p)
             self.spawned.append((p.pid, _starttime(p.pid), Path(argv[0]).name))
@@ -419,12 +443,11 @@ def kiss_box(tmp_path, monkeypatch, set_call):
         (root / "src" / "loraham-kiss-tnc").mkdir(parents=True)
         (root / "src" / "loraham-kiss-tnc" / "loraham-kiss-tnc").write_text("#bin")
         fake = FakeSystem(unix_replies={"/tmp/loraconf433.sock": _READY})
-        port = _free_port()
-        fake.listeners = _TncEndpoint(port)
+        fake.listeners = _TncEndpoint(str(root))
         svc = ControllerService(system=fake.system, paths=Paths(runtime_root=root))
         seeded = svc.bootstrap(apply=True)
         assert seeded.ok, f"seeding: bootstrap failed: {seeded.summary}"
-        box = KissBox(root, fake, svc, port)
+        box = KissBox(root, fake, svc)
         spawn = box.spawn(procs)
         monkeypatch.setattr(ControllerService, "_lifecycle", lambda s: Lifecycle(
             s._paths, s.stacks(), s.config(), s._system, spawn=spawn))
diff --git a/tests/golden/entry_host.py b/tests/golden/entry_host.py
index 5877ea9e..c0831ae7 100644
--- a/tests/golden/entry_host.py
+++ b/tests/golden/entry_host.py
@@ -63,7 +63,7 @@ def describe(box, out_dir: Path) -> Path:
     from lhpc.core.services import ControllerService
     out_dir.mkdir(parents=True, exist_ok=True)
     fw = ControllerService._fw_integration_state(box.svc)
-    desc = {"root": str(box.root), "port": box.port, "listens": box.listens, "term": box.term,
+    desc = {"root": str(box.root), "listens": box.listens, "term": box.term,
             "cmdlines": {str(k): v for k, v in box.fake.cmdlines_data.items()},
             "manifest": str(box.manifest) if box.manifest else None,
             "waits": {"endpoint": ControllerService.ENDPOINT_VERIFY_TIMEOUT_S,
@@ -187,8 +187,8 @@ def _rebuilt(d):
     _install(d)
     fake = FakeSystem(unix_replies={"/tmp/loraconf433.sock": golden._READY},
                       cmdlines_data={int(k): v for k, v in d["cmdlines"].items()})
-    fake.listeners = golden._TncEndpoint(d["port"])
-    box = golden.KissBox(Path(d["root"]), fake, None, d["port"])
+    fake.listeners = golden._TncEndpoint(d["root"])
+    box = golden.KissBox(Path(d["root"]), fake, None)
     box.listens, box.term = d["listens"], d["term"]
     procs: list = []
     tnc = box.spawn(procs)
diff --git a/tests/golden/test_harness.py b/tests/golden/test_harness.py
index 8a9895f7..8b7a84f9 100644
--- a/tests/golden/test_harness.py
+++ b/tests/golden/test_harness.py
@@ -21,3 +21,36 @@ def test_a_write_moved_outside_its_lock_is_caught(tmp_path, run_op, phases, monk
     assert run.fields == SAVED and run.phases == WRITES
     assert phases.outside == WRITES[2:]
     phases.outside.clear()
+
+
+def test_a_foreign_loopback_listener_is_never_the_tncs_endpoint(kiss_box):
+    """intended: the box's TNC endpoint is a listener the box's own TNC process holds. Another
+    process listening on 127.0.0.1 — on the port the box would use, if it keeps one — is not it,
+    so a parallel test's socket can neither make a start ready nor keep a stop from ceasing."""
+    import socket
+    box = kiss_box()
+    foreign = socket.socket()
+    try:
+        foreign.bind(("127.0.0.1", getattr(box, "port", 0)))
+        foreign.listen()
+        assert list(box.fake.listeners) == []
+    finally:
+        foreign.close()
+
+
+def test_a_later_spawn_is_refused_while_an_earlier_tnc_of_the_box_lives(kiss_box, tmp_path):
+    """intended: an old TNC that still listens can never make a later spawn of the same box read
+    ready: the box refuses that spawn (a new invariant of the harness; its negative case)."""
+    import pytest
+    box = kiss_box()
+    procs: list = []
+    spawn = box.spawn(procs)
+    try:
+        spawn(["loraham-kiss-tnc"], tmp_path / "a.log")
+        with pytest.raises(AssertionError, match="second TNC"):
+            spawn(["loraham-kiss-tnc"], tmp_path / "b.log")
+        assert len(procs) == 1
+    finally:
+        for p in procs:
+            p.kill()
+            p.wait(timeout=5)
```
