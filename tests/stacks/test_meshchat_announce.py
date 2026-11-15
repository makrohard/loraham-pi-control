"""MeshChat announces itself once at every start (the maintainer's 0.14.0 word "A").

LHPC writes nothing into MeshChat: MeshChat's own post-start step makes its Announce button's call,
`GET /api/v1/announce` (meshchat.py:1189-1192, answer {"message": "announcing"}). The start-path
tests run `lhpc stack start meshchat@r2` through the CLI: the real service start, the real Lifecycle
with a real (sleeping) process for each launch, the real post-start scheduling and detached runner,
against a fake MeshChat on loopback that records the call. The service reads a copy of the shipped
manifest in which only the step's `--api` port is the fake's (MeshChat's 8790 may be taken on the
test host); the box reports MeshChat's 8790 listener and the node's ready file. A failed announce is logged and never fails the start.
"""

import importlib.util
import json
import socket
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

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


def _load():
    spec = importlib.util.spec_from_file_location("meshchat_announce", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class FakeMeshChat:
    """MeshChat's announce route over real HTTP; records every call."""

    def __init__(self, code=200, body=b'{"message": "announcing"}', length=None, drip=0.0):
        self.calls, self.called, self.stop = [], threading.Event(), threading.Event()
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_a):
                pass

            def do_GET(self):
                fake.calls.append(self.path)
                self.send_response(code)
                self.send_header("Content-Length", str(len(body) if length is None else length))
                self.end_headers()
                fake.called.set()
                if not drip:
                    self.wfile.write(body)
                    return
                for i in range(len(body)):                 # one byte every `drip` seconds
                    if fake.stop.wait(drip):
                        return
                    try:
                        self.wfile.write(body[i:i + 1])
                        self.wfile.flush()
                    except OSError:
                        return

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.api = f"127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.stop.set()
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def meshchat():
    made = []

    def make(**kw):
        made.append(FakeMeshChat(**kw))
        return made[-1]
    yield make
    for f in made:
        f.close()


def _bounded(fn, seconds=20.0):
    """Run `fn` in a daemon thread and fail if it has not returned within `seconds`: a regression of
    the announce's bound fails this test fast instead of holding the run (no timeout plugin here)."""
    box = {}

    def go():
        try:
            box["value"] = fn()
        except BaseException as exc:                  # re-raised in the test's own thread
            box["error"] = exc
    t = threading.Thread(target=go, daemon=True)
    t.start()
    t.join(seconds)
    assert not t.is_alive(), f"did not return within {seconds:g} s"
    if "error" in box:
        raise box["error"]
    return box.get("value")


def _run(api, capsys, timeout="5"):
    rc = _bounded(lambda: _load().main(["--api", api, "--timeout", timeout]))
    out = capsys.readouterr()
    return rc, out.out + out.err


# --- the script ---

def test_the_announce_is_the_buttons_call_and_its_answer_is_checked(meshchat, capsys):
    fake = meshchat()
    rc, out = _run(fake.api, capsys)
    assert (rc, fake.calls) == (0, ["/api/v1/announce"]) and "announced" in out


@pytest.mark.parametrize("kw,why", [
    pytest.param({"code": 500, "body": b"{}"}, "HTTP 500", id="http-500"),
    pytest.param({"body": b"<html>"}, "not JSON", id="not-json"),
    pytest.param({"body": b'{"message": "nope"}'}, "did not confirm", id="wrong-answer"),
    pytest.param({"length": 200}, "IncompleteRead", id="body-cut-short"),     # the body ends before its length
])
def test_an_announce_it_cannot_confirm_fails_by_name(meshchat, capsys, kw, why):
    rc, out = _run(meshchat(**kw).api, capsys)
    assert rc == 1 and why in out and "not announced" in out


def test_a_meshchat_that_never_listens_fails_within_the_bound():
    """Only a refused connection is retried, and only until the deadline (clock injected)."""
    mod = _load()
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    now, pauses = [0.0], []

    def pause(seconds):
        pauses.append(seconds)
        now[0] += seconds
    with pytest.raises(mod.Failure, match="did not answer"):
        _bounded(lambda: mod.announce(f"127.0.0.1:{port}", 5.0, clock=lambda: now[0], pause=pause))
    assert 1 <= len(pauses) <= 6


def test_the_pause_after_a_refused_connect_never_passes_the_deadline():
    """Refused, refused, refused with 2.5 s in all: the pauses are 1, 1 and the 0.5 s left (re-round
    note 2), never a full second past the deadline (clock injected)."""
    mod = _load()
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    now, pauses = [0.0], []

    def pause(seconds):
        pauses.append(seconds)
        now[0] += seconds
    with pytest.raises(mod.Failure, match="did not answer"):
        _bounded(lambda: mod.announce(f"127.0.0.1:{port}", 2.5, clock=lambda: now[0], pause=pause))
    assert pauses == [1.0, 1.0, 0.5]


def test_an_error_other_than_refused_is_not_retried():
    """A MeshChat that accepts and drops the connection is not "not listening yet": it fails at
    once, with no pause (only a refused connection waits for the bound)."""
    mod = _load()
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]

    def drop():
        conn, _a = srv.accept()
        conn.close()
    t = threading.Thread(target=drop, daemon=True)
    t.start()
    pauses = []
    try:
        with pytest.raises(mod.Failure, match="GET /api/v1/announce"):
            _bounded(lambda: mod.announce(f"127.0.0.1:{port}", 5.0, clock=lambda: 0.0, pause=pauses.append))
    finally:
        t.join(5)
        srv.close()
    assert pauses == []


@pytest.mark.wallclock("a stalled server: the bound is the real socket timeout, capped by the time left")
def test_a_meshchat_that_accepts_and_never_answers_ends_within_the_whole_bound():
    """A server that accepts the connection and never answers: the attempt's socket timeout is the
    time left (here 1 s), not a fixed 10 s, so the announce ends by its overall deadline."""
    mod = _load()
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)                                      # accepted by the kernel; nobody ever reads
    try:
        with pytest.raises(mod.Failure, match="timed out|did not answer"):
            _bounded(lambda: mod.announce(f"127.0.0.1:{srv.getsockname()[1]}", 1.0), seconds=8.0)
    finally:
        srv.close()


@pytest.mark.wallclock("a trickling answer: the bound is the real deadline across the body read")
def test_an_answer_that_trickles_in_ends_by_the_overall_deadline(meshchat):
    """Valid headers, then one body byte every 0.3 s: every read completes in time, so only a deadline
    checked across the whole read ends it — by the 1 s bound, not after the body (G1 re-round)."""
    import time
    mod = _load()
    fake = meshchat(drip=0.3)
    t0 = time.monotonic()
    with pytest.raises(mod.Failure, match="did not answer|timed out"):
        _bounded(lambda: mod.announce(fake.api, 1.0), seconds=8.0)
    assert time.monotonic() - t0 < 2.5


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
    """The re-round's case through `lhpc stack start`: MeshChat answers one byte every 0.3 s; the step
    (its --timeout set to 2 s in the copied manifest) ends `failed` rc 1 by its bound, the failure is
    in the post-start log, and the start exits 0."""
    fake = meshchat(drip=0.3)
    rc, out, svc = box(fake, step_timeout="2")
    assert rc == 0, out
    assert fake.called.wait(30)
    (step,) = _runner_result(svc, deadline_s=20.0)
    assert (step["label"], step["outcome"], step["rc"]) == ("announce", "failed", 1)
    assert step["elapsed_s"] < 6.0, step
    (rec,) = svc._lifecycle().owned_records("meshchat", role="post")
    assert "not announced" in open(rec["log_path"]).read()
