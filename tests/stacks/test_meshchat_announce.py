"""MeshChat announces itself once at every start (the maintainer's 0.14.0 word "A").

LHPC writes nothing into MeshChat: MeshChat's own post-start step makes its Announce button's call,
`GET /api/v1/announce` (meshchat.py:1189-1192, answer {"message": "announcing"}), bounded as a whole by
ONE wall-clock deadline (a real-time interval timer in the script). The script tests run the script as
the post-start runner does, as its own process (the timer needs its main thread), each under a
subprocess timeout so a regression fails fast instead of holding the run. The start-path tests run
`lhpc stack start meshchat@r2` through the CLI: the real service start, the real Lifecycle with a real
(sleeping) process for each launch, the real post-start scheduling and detached runner, against a fake
MeshChat on loopback that records the call. The service reads a copy of the shipped manifest in which
only the step's `--api` port (and, for the trickle case, its `--timeout`) is the fake's; the box reports
MeshChat's 8790 listener and the node's ready file. A failed announce is logged and never fails the start.
"""

import json
import socket
import subprocess
import sys
import threading
import time

import pytest

import repo_paths
import radios_fixture
from lhpc.adapters.cli import main as cli
from lhpc.core.lifecycle import Lifecycle
from lhpc.core.manifest import load_manifest
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem, Listener
from lhpc.core.services import ControllerService
from seams import seed_framing_driver

_SCRIPT = repo_paths.REPO / "lhpc" / "data" / "scripts" / "meshchat-announce.py"
_OK = b'{"message": "announcing"}'


def _response(code=200, body=_OK, length=None, chunked=False):
    """MeshChat's answer as bytes, and where its header block ends."""
    if chunked:
        head = f"HTTP/1.1 {code} OK\r\nTransfer-Encoding: chunked\r\nConnection: close\r\n\r\n".encode()
        return head + f"{len(body):x}\r\n".encode() + body + b"\r\n0\r\n\r\n", len(head)
    n = len(body) if length is None else length
    head = f"HTTP/1.1 {code} OK\r\nContent-Length: {n}\r\nConnection: close\r\n\r\n".encode()
    return head + body, len(head)


class FakeMeshChat:
    """MeshChat's announce route on a loopback socket; records every request path. `drip` sends the
    answer from `drip_from` on one byte every `drip` seconds: "status" (from the first byte),
    "headers" (after the status line), "body" (after the header block); `chunked` frames the body."""

    def __init__(self, code=200, body=_OK, length=None, chunked=False, drip=0.0, drip_from="body",
                 raw=None, stream=None):
        self.calls, self.called, self.stop = [], threading.Event(), threading.Event()
        self.stream = stream                           # "chunked" | "unframed": 8 MB fast, then it stalls
        if stream == "chunked":
            raw = b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\nConnection: close\r\n\r\n"
        elif stream == "unframed":
            raw = b"HTTP/1.1 200 OK\r\nConnection: close\r\n\r\n"
        self.data, end_of_head = (raw, len(raw)) if raw is not None else _response(code, body, length, chunked)
        self.cut = {"status": 0, "headers": self.data.index(b"\r\n") + 2, "body": end_of_head}[drip_from] if drip else len(self.data)
        self.drip = drip
        self.srv = socket.socket()
        self.srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.srv.bind(("127.0.0.1", 0))
        self.srv.listen(8)
        self.srv.settimeout(0.2)
        self.api = f"127.0.0.1:{self.srv.getsockname()[1]}"
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self):
        while not self.stop.is_set():
            try:
                conn, _a = self.srv.accept()
            except (TimeoutError, OSError):
                continue
            threading.Thread(target=self._answer, args=(conn,), daemon=True).start()

    def _answer(self, conn):
        with conn:
            req = b""
            while b"\r\n\r\n" not in req:
                got = conn.recv(4096)
                if not got:
                    return
                req += got
            self.calls.append(req.split(b" ", 2)[1].decode())
            self.called.set()
            try:
                conn.sendall(self.data[:self.cut])
                if self.stream:                            # far over the 64 KB cap, then it stalls
                    piece = b"x" * 4096
                    for _ in range(2048):
                        conn.sendall(b"1000\r\n" + piece + b"\r\n" if self.stream == "chunked" else piece)
                    self.stop.wait()
                    return
                for i in range(self.cut, len(self.data)):  # one byte every `drip` seconds
                    if self.stop.wait(self.drip):
                        return
                    conn.sendall(self.data[i:i + 1])
            except OSError:
                return

    def close(self):
        self.stop.set()
        self.srv.close()


@pytest.fixture
def meshchat():
    made = []

    def make(**kw):
        made.append(FakeMeshChat(**kw))
        return made[-1]
    yield make
    for f in made:
        f.close()


def _script(api, timeout="5", env=None):
    """The script as the runner runs it: its own process, here under a 30 s subprocess timeout (a
    regression of the bound fails the test instead of holding the run). -> (rc, output, seconds)."""
    t0 = time.monotonic()                            # wallclock: measures the script's real bound
    p = subprocess.run([sys.executable, str(_SCRIPT), "--api", api, "--timeout", timeout],
                       capture_output=True, text=True, timeout=30, env=env)
    return p.returncode, p.stdout + p.stderr, time.monotonic() - t0  # wallclock: the real bound


def _free_port():
    with socket.socket() as s:                       # bound, closed: nothing listens there
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# --- the script, as its own process ---

def test_the_announce_is_the_buttons_call_and_its_answer_is_checked(meshchat):
    fake = meshchat()
    rc, out, _s = _script(fake.api)
    assert (rc, fake.calls) == (0, ["/api/v1/announce"]) and "announced" in out, out


@pytest.mark.parametrize("kw,why", [
    pytest.param({"code": 500, "body": b"{}"}, "HTTP 500", id="http-500"),
    pytest.param({"body": b"<html>"}, "not JSON", id="not-json"),
    pytest.param({"body": b'{"message": "nope"}'}, "did not confirm", id="wrong-answer"),
    pytest.param({"length": 200}, "IncompleteRead", id="body-cut-short"),      # the body ends before its length
    pytest.param({"length": 70000}, "larger than 65536 bytes", id="body-too-large"),
])
def test_an_announce_it_cannot_confirm_fails_by_name(meshchat, kw, why):
    rc, out, _s = _script(meshchat(**kw).api)
    assert rc == 1 and why in out and "not announced" in out, out


@pytest.mark.wallclock("the bound is the script's real deadline")
def test_a_meshchat_that_never_listens_ends_by_the_deadline():
    """Only a refused connect is retried, until the one deadline."""
    rc, out, secs = _script(f"127.0.0.1:{_free_port()}", timeout="1")
    assert rc == 1 and "announce timed out" in out and secs < 4, (out, secs)


@pytest.mark.wallclock("a dropped connection fails at once, well inside the deadline")
def test_an_error_other_than_refused_is_not_retried():
    """A MeshChat that accepts and drops the connection is not "not listening yet": it fails at once."""
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)

    def drop():
        conn, _a = srv.accept()
        conn.close()
    t = threading.Thread(target=drop, daemon=True)
    t.start()
    try:
        rc, out, secs = _script(f"127.0.0.1:{srv.getsockname()[1]}", timeout="10")
    finally:
        t.join(5)
        srv.close()
    assert rc == 1 and "GET /api/v1/announce" in out and secs < 5, (out, secs)


@pytest.mark.wallclock("a stalled server: the bound is the script's real deadline")
def test_a_meshchat_that_accepts_and_never_answers_ends_by_the_deadline():
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)                                      # accepted by the kernel; nobody ever reads
    try:
        rc, out, secs = _script(f"127.0.0.1:{srv.getsockname()[1]}", timeout="1")
    finally:
        srv.close()
    assert rc == 1 and "announce timed out" in out and secs < 4, (out, secs)


@pytest.mark.wallclock("a trickling answer: the bound is the script's real deadline")
@pytest.mark.parametrize("kw", [
    pytest.param({"drip_from": "status"}, id="status-line"),
    pytest.param({"drip_from": "headers"}, id="headers"),
    pytest.param({"drip_from": "body"}, id="body"),
    pytest.param({"drip_from": "body", "chunked": True}, id="chunked"),
])
def test_an_answer_that_trickles_in_ends_by_the_deadline(meshchat, kw):
    """One byte every 0.3 s from the status line, the headers, the body or the chunk framing on:
    every read completes in time, only the one deadline ends it — by its 1 s (code re-rounds 1, 2)."""
    rc, out, secs = _script(meshchat(drip=0.3, **kw).api, timeout="1")
    assert rc == 1 and "announce timed out" in out and secs < 3, (out, secs)


@pytest.mark.parametrize("stream", ["chunked", "unframed"])
def test_an_answer_without_a_declared_length_is_capped_by_the_bytes_read(meshchat, stream):
    """Chunked, or no Content-Length at all, streaming far past 64 KB: refused once more than 64 KB
    has arrived — not read on until the deadline (code re-round 3, B1)."""
    rc, out, secs = _script(meshchat(stream=stream).api, timeout="10")
    assert rc == 1 and "larger than 65536 bytes" in out and secs < 5, (out, secs)


def test_a_redirect_is_refused_and_never_followed(meshchat):
    """The call is direct: a 302 to another address is the named failure; that address is never
    contacted (code re-round 3, B2)."""
    elsewhere = meshchat()
    to = f"http://{elsewhere.api}/api/v1/announce"
    fake = meshchat(raw=f"HTTP/1.1 302 Found\r\nLocation: {to}\r\nContent-Length: 0\r\nConnection: close\r\n\r\n".encode())
    rc, out, _s = _script(fake.api)
    assert rc == 1 and "HTTP 302" in out, out
    assert fake.calls == ["/api/v1/announce"] and elsewhere.calls == []


def test_a_proxy_in_the_environment_is_not_used(meshchat):
    """The call is direct: with http_proxy / HTTP_PROXY set (no no_proxy), MeshChat on loopback
    gets the call and the proxy gets nothing (code re-round 3, B2)."""
    import os
    proxy, fake = meshchat(), meshchat()
    env = {k: v for k, v in os.environ.items() if k.lower() not in ("no_proxy", "http_proxy")}
    env.update(http_proxy=f"http://{proxy.api}", HTTP_PROXY=f"http://{proxy.api}")
    rc, out, _s = _script(fake.api, env=env)
    assert rc == 0 and "announced" in out, out
    assert fake.calls == ["/api/v1/announce"] and proxy.calls == []


def test_a_tiny_deadline_ends_as_the_named_failure_never_a_traceback():
    """An alarm that fires at any moment, even right after the timer is armed, is the named failure
    (code re-round 3, note 1). Ten tries, each on a port nothing listens on."""
    for _ in range(10):
        rc, out, _s = _script(f"127.0.0.1:{_free_port()}", timeout="0.000001")
        assert rc == 1 and "Traceback" not in out and "not announced" in out, out


def test_an_alarm_pending_at_the_disarm_never_turns_a_success_into_a_failure(monkeypatch):
    """In process, deterministic (SR delta note 1): the timer's disarm is faked to deliver a real
    SIGALRM at that very moment — the alarm "pending after cleanup". Cleanup ignores SIGALRM before it
    disarms, so a confirmed announce stays a success (rc 0). Without that order the alarm becomes
    "announce timed out" after the announce went out."""
    import importlib.util
    import signal
    spec = importlib.util.spec_from_file_location("meshchat_announce_inproc", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "announce", lambda api: None)               # the announce went out
    real = signal.setitimer

    def setitimer(which, seconds, *rest):
        if seconds == 0:
            signal.raise_signal(signal.SIGALRM)                          # the alarm lands at the disarm
        return real(which, 0)                                            # never a real timer here
    monkeypatch.setattr(mod.signal, "setitimer", setitimer)
    before = signal.getsignal(signal.SIGALRM)
    try:
        assert mod.main(["--api", "127.0.0.1:1", "--timeout", "5"]) == 0
    finally:
        signal.signal(signal.SIGALRM, before)


def test_an_alarm_at_the_arm_is_the_named_failure(monkeypatch, capsys):
    """Its sibling: the arm itself is faked to deliver SIGALRM at once. The timer is armed inside the
    protected region, so main returns the named failure (rc 1) — nothing escapes (SR delta note)."""
    import importlib.util
    import signal
    spec = importlib.util.spec_from_file_location("meshchat_announce_inproc2", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    real = signal.setitimer

    def setitimer(which, seconds, *rest):
        real(which, 0)                                                   # never a real timer here
        if seconds > 0:
            signal.raise_signal(signal.SIGALRM)                          # the alarm lands at the arm
    monkeypatch.setattr(mod.signal, "setitimer", setitimer)
    before = signal.getsignal(signal.SIGALRM)
    try:
        assert mod.main(["--api", "127.0.0.1:1", "--timeout", "5"]) == 1
    finally:
        signal.signal(signal.SIGALRM, before)
    assert "not announced — announce timed out" in capsys.readouterr().err


@pytest.mark.parametrize("kw", [
    pytest.param({"body": b'{"message": "unexpected", "token": "CANARY_SECRET"}'}, id="answer-values"),
    pytest.param({"raw": b"HTTP/1.1 CANARY_SECRET\r\n\r\n"}, id="status-line"),
    pytest.param({"code": 500, "body": b'{"error": "CANARY_SECRET"}'}, id="error-body"),
])
def test_a_failed_announce_never_logs_meshchats_answer(meshchat, kw):
    """G2-GROUP11 note: the post-start log gets a fixed message plus at most an exception type or the
    HTTP status — never a value from the answer, nor an exception's text that carries it."""
    rc, out, _s = _script(meshchat(**kw).api)
    assert rc == 1 and "not announced" in out, out
    assert "CANARY" not in out, out


def test_a_chunked_answer_in_time_is_read_whole(meshchat):
    rc, out, _s = _script(meshchat(chunked=True).api)
    assert rc == 0 and "announced" in out, out


def test_a_timeout_of_zero_is_refused():
    rc, out, _s = _script("127.0.0.1:1", timeout="0")
    assert rc == 2 and "--timeout must be > 0" in out


# --- the real manifest step through the real post-start runner ---

def _meshchat():
    stack = next(s for s in load_manifest() if s.id == "reticulum")
    return stack, stack.component("meshchat")


def test_the_manifest_announces_meshchat_once_per_start_as_an_optional_step():
    _stack, comp = _meshchat()
    (step,) = comp.post_steps
    assert step["kind"] == "exec" and step["label"] == "announce" and not step.get("required")
    assert step["argv"] == ["{controller_python}", "{asset}/scripts/meshchat-announce.py",
                            "--api", "127.0.0.1:8790", "--timeout", "60"]     # MeshChat's fixed loopback port


_SHIPPED = repo_paths.REPO / "lhpc" / "data" / "manifest.example.toml"
_API = '"--api", "127.0.0.1:8790", "--timeout", "60"'


@pytest.fixture
def box(tmp_path, monkeypatch, capsys):
    """A built box with a hardware list (Reticulum on r2) whose `lhpc` runs the real start path;
    each launch is a real detached `sleep` (ownable), the node's ready file and MeshChat's listener
    are what the box reports. `start(fake)` -> (rc, out, svc)."""
    spawned = []

    def spawn(argv, log, cwd=None, env=None):
        open(str(log), "a").close()
        spawned.append(subprocess.Popen(["sleep", "120"], start_new_session=True,   # wallclock: a live launch, ended after the test; never paces
                                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        return spawned[-1].pid

    def start(fake, *argv, step_timeout="60"):
        man = _SHIPPED.read_text()                       # the step itself is pinned by the manifest test
        api = _API.replace("127.0.0.1:8790", fake.api).replace('"--timeout", "60"', f'"--timeout", "{step_timeout}"')
        (tmp_path / "manifest.toml").write_text(man.replace(_API, api))
        radios_fixture.write(tmp_path, (radios_fixture.R1, radios_fixture.R2_868))
        seed_framing_driver(tmp_path)
        for name, value in (("hardware_configured", lambda self: True), ("radio_bands", lambda self: ["433", "868"]),
                            ("binary_behind", lambda self, c: ""), ("is_built", lambda self, c: True),
                            ("_source_present", lambda self, c: True)):
            monkeypatch.setattr(ControllerService, name, value)
        monkeypatch.setattr(Lifecycle, "missing_requirements", lambda self, comp: [])   # the PC has no SPI
        system = FakeSystem(listeners=[Listener(family="ipv4", ip="127.0.0.1", port=8790, inode=1)],
                            paths={str(tmp_path / "state" / "reticulum" / "ready")}).system
        svc = ControllerService(system=system, paths=Paths(runtime_root=tmp_path), manifest_path=tmp_path / "manifest.toml")
        monkeypatch.setattr(ControllerService, "_lifecycle",
                            lambda self: Lifecycle(self._paths, self.stacks(), self.config(), self._system, spawn=spawn))
        monkeypatch.setattr(cli, "ControllerService", lambda *a, **k: svc)
        rc = cli.main(["stack", "start", "meshchat@r2", "--yes", *argv])
        got = capsys.readouterr()
        return rc, got.out + got.err, svc

    yield start
    for p in spawned:
        p.terminate()
        p.wait()


def _runner_result(svc, deadline_s=30.0):
    """MeshChat's post-start runner's own result, once it has ended (bounded; no fixed sleep)."""
    life = svc._lifecycle()
    (rec,) = life.owned_records("meshchat", role="post")
    tick = threading.Event()
    for _ in range(int(deadline_s / 0.05)):
        if life._proc_ceased(rec["pid"]):
            break
        tick.wait(0.05)
    return json.loads(open(rec["result_path"]).read())["steps"]


@pytest.mark.needs_session
def test_a_meshchat_start_sends_exactly_one_announce(meshchat, box):
    fake = meshchat()
    rc, out, svc = box(fake)
    assert rc == 0, out
    assert "[verified] meshchat" in out and "optional post-start scheduled" in out, out
    assert fake.called.wait(30), "the start's runner never called MeshChat"
    (step,) = _runner_result(svc)
    assert (step["label"], step["outcome"], step["rc"]) == ("announce", "ok", 0)
    assert fake.calls == ["/api/v1/announce"], "one announce per start"


@pytest.mark.needs_session
def test_a_failed_announce_is_logged_and_never_fails_the_start(meshchat, box):
    fake = meshchat(code=500, body=b"{}")
    rc, out, svc = box(fake)
    assert rc == 0, out
    assert fake.called.wait(30)
    (step,) = _runner_result(svc)
    assert (step["label"], step["outcome"], step["rc"]) == ("announce", "failed", 1)


@pytest.mark.needs_session
@pytest.mark.wallclock("a trickling answer through the real runner: its bound is the step's real deadline")
def test_a_trickling_meshchat_ends_the_step_by_its_bound_and_the_start_stands(meshchat, box):
    """The re-rounds' case through `lhpc stack start`: MeshChat sends its headers one byte every 0.3 s;
    the step (its --timeout 2 s in the copied manifest) ends `failed` rc 1 by its bound, "announce timed
    out" is in the post-start log, and the start exits 0."""
    fake = meshchat(drip=0.3, drip_from="headers")
    rc, out, svc = box(fake, step_timeout="2")
    assert rc == 0, out
    assert fake.called.wait(30)
    (step,) = _runner_result(svc, deadline_s=20.0)
    assert (step["label"], step["outcome"], step["rc"]) == ("announce", "failed", 1)
    assert step["elapsed_s"] < 6.0, step
    (rec,) = svc._lifecycle().owned_records("meshchat", role="post")
    assert "not announced — announce timed out" in open(rec["log_path"]).read()
