"""MeshChat announces itself once at every start (the maintainer's 0.14.0 word "A").

LHPC writes nothing into MeshChat: MeshChat's own post-start step makes its Announce button's call,
`GET /api/v1/announce` (meshchat.py:1189-1192, answer {"message": "announcing"}). The start-path
tests run the REAL manifest step through the real detached post-start runner, bound to a live main,
against a fake MeshChat on loopback that records the call (MeshChat's fixed port is swapped for the
fake's, nothing else). A failed announce is logged and never fails the start.
"""

import dataclasses
import importlib.util
import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

import repo_paths
from lhpc.core.config import Config, OperatorConfig
from lhpc.core.lifecycle import Lifecycle
from lhpc.core.manifest import load_manifest
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import RealSystem

_SCRIPT = repo_paths.REPO / "lhpc" / "data" / "scripts" / "meshchat-announce.py"


def _load():
    spec = importlib.util.spec_from_file_location("meshchat_announce", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class FakeMeshChat:
    """MeshChat's announce route over real HTTP; records every call."""

    def __init__(self, code=200, body=b'{"message": "announcing"}'):
        self.calls, self.called = [], threading.Event()
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_a):
                pass

            def do_GET(self):
                fake.calls.append(self.path)
                self.send_response(code)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                fake.called.set()

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.api = f"127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
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


def _run(api, capsys, timeout="5"):
    rc = _load().main(["--api", api, "--timeout", timeout])
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
        mod.announce(f"127.0.0.1:{port}", 5.0, clock=lambda: now[0], pause=pause)
    assert 1 <= len(pauses) <= 6


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
            mod.announce(f"127.0.0.1:{port}", 5.0, clock=lambda: 0.0, pause=pauses.append)
    finally:
        t.join(5)
        srv.close()
    assert pauses == []


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


def _start(tmp_path, fake):
    """MeshChat's post-start as a start schedules it: a real Lifecycle, a live main recorded as the
    verified launch, then `spawn_post_start` (the detached, armed runner) with the real step."""
    import subprocess
    stack, comp = _meshchat()
    step = dict(comp.post_steps[0])
    step["argv"] = [fake.api if a == "127.0.0.1:8790" else a for a in step["argv"]]
    comp = dataclasses.replace(comp, post_steps=(step,))
    life = Lifecycle(Paths(runtime_root=tmp_path), (), Config(operator=OperatorConfig()), RealSystem())
    main = subprocess.Popen(["sleep", "60"], start_new_session=True)  # wallclock: a live main to bind to, ended in finally; never paces
    seen = threading.Event()
    for _ in range(100):
        idn = life._capture_identity(main.pid)
        if idn and idn.get("exec") == "sleep":
            break
        seen.wait(0.05)
    assert life.record_launch(stack, comp, main.pid, "", ident=life._capture_identity(main.pid), role="")
    return life, comp, main, life.spawn_post_start(stack, comp)


def _runner_result(life, comp, deadline_s=30.0):
    """The runner's own result file, once it has ended (bounded; no fixed sleep)."""
    (rec,) = life.owned_records(comp.id, role="post")
    tick = threading.Event()
    for _ in range(int(deadline_s / 0.05)):
        if life._proc_ceased(rec["pid"]):
            break
        tick.wait(0.05)
    return json.loads(open(rec["result_path"]).read())


@pytest.mark.needs_session
def test_every_meshchat_start_sends_one_announce(meshchat, tmp_path):
    fake = meshchat()
    life, comp, main, sched = _start(tmp_path, fake)
    try:
        assert sched.ok, sched
        assert fake.called.wait(30), "the start's runner never called MeshChat"
        step = _runner_result(life, comp)["steps"][0]
        assert (step["label"], step["outcome"], step["rc"]) == ("announce", "ok", 0)
        assert fake.calls == ["/api/v1/announce"], "once per start"
    finally:
        life._cancel_post_runners(comp, None)
        main.terminate(); main.wait()


@pytest.mark.needs_session
def test_a_failed_announce_is_logged_and_never_fails_the_start(meshchat, tmp_path):
    fake = meshchat(code=500, body=b"{}")
    life, comp, main, sched = _start(tmp_path, fake)
    try:
        assert sched.ok, "an optional post step never gates the start"
        assert fake.called.wait(30)
        result = _runner_result(life, comp)
        step = result["steps"][0]
        assert (step["label"], step["outcome"], step["rc"]) == ("announce", "failed", 1), result
    finally:
        life._cancel_post_runners(comp, None)
        main.terminate(); main.wait()
