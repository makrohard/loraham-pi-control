"""§7 — required vs optional post-start, and §5 typed start aggregation."""
import pytest

import time

from lhpc.core.lifecycle import Lifecycle
from conftest import real_spawn
from lhpc.core.services import ControllerService
from lhpc.core.config import Config, OperatorConfig
from lhpc.core.model import Component, ComponentKind, Stack
from lhpc.core.paths import Paths
from lhpc.core.probes import RealSystem
from lhpc.core.probes.backends import FakeSystem
from conftest import set_call


def _real_life(tmp_path):
    return Lifecycle(Paths(runtime_root=tmp_path), (), Config(operator=OperatorConfig()),
                     RealSystem())


STK = Stack(id="s", name="s", main="c")


def test_required_post_start_failure_is_typed(tmp_path):
    comp = Component(id="c", name="c", kind=ComponentKind.SERVICE, readiness="process",
                     run_argv=("true",),
                     post_steps=({"kind": "exec", "argv": ["false"], "required": True},))
    life = _real_life(tmp_path)
    assert life.has_required_post_start(comp)
    jr = life.run_required_post_start(STK, comp)
    assert not jr.ok and jr.returncode != 0


def test_required_post_start_success_is_typed(tmp_path):
    comp = Component(id="c", name="c", kind=ComponentKind.SERVICE, readiness="process",
                     run_argv=("true",),
                     post_steps=({"kind": "exec", "argv": ["true"], "required": True},))
    life = _real_life(tmp_path)
    jr = life.run_required_post_start(STK, comp)
    assert jr.ok and jr.returncode == 0


def test_optional_post_start_not_required(tmp_path):
    comp = Component(id="c", name="c", kind=ComponentKind.SERVICE, readiness="process",
                     run_argv=("true",),
                     post_steps=({"kind": "exec", "argv": ["true"]},))   # no required flag
    life = _real_life(tmp_path)
    assert life.has_required_post_start(comp) is False


def test_start_action_carries_typed_results(tmp_path):
    # A dependent of an uninstalled daemon -> BLOCKED, and ActionResult exposes the
    # typed CompResult objects; ok is derived from them.
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    res = svc.start("kiss", apply=True)
    assert not res.ok
    assert res.results and all(hasattr(r, "outcome") for r in res.results)
    assert any(r.outcome.value in ("blocked", "failed") for r in res.results)


# --- P0.1 integration: required post-start via ControllerService.start() ------

def _fake_life_factory(svc):
    return Lifecycle(svc._paths, svc.stacks(), svc.config(), svc._system,
                     spawn=real_spawn)


def _igate_svc(tmp_path):
    from lhpc.core.probes.backends import FakeSystem
    (tmp_path / "src" / "LoRaHAM_Daemon").mkdir(parents=True)
    (tmp_path / "src" / "LoRaHAM_Daemon" / "loraham_igate").write_text("#bin")  # built
    sys = FakeSystem(unix_replies={"/tmp/loraconf433.sock":
                                   b"STATUS RADIO=READY TXMODE=MANAGED\n"}).system
    return ControllerService(system=sys, paths=Paths(runtime_root=tmp_path))


@pytest.mark.needs_session
def test_start_required_post_start_failure_is_unverified(tmp_path, monkeypatch):
    # Drives ControllerService.start() and exercises the CALLER's handling of the typed
    # required-post-start result (the bug was the caller using a nonexistent Outcome.ok).
    svc = _igate_svc(tmp_path)
    set_call(svc)
    monkeypatch.setattr(ControllerService, "_lifecycle", _fake_life_factory)
    monkeypatch.setattr(ControllerService, "_run_post_start",
                        lambda self, *a, **k: (False, "required post-start failed (rc 7)"))
    res = svc.start("igate", apply=True)            # must NOT raise AttributeError
    assert not res.ok
    assert any("required post-start failed" in d for d in res.details)
    assert any(r.component == "loraham-igate" and r.outcome.value == "unverified"
               for r in res.results)


@pytest.mark.needs_session
def test_start_required_post_start_success_verifies(tmp_path, monkeypatch):
    svc = _igate_svc(tmp_path)
    monkeypatch.setattr(ControllerService, "_lifecycle", _fake_life_factory)
    monkeypatch.setattr(ControllerService, "_run_post_start",
                        lambda self, *a, **k: (True, "required post-start completed"))
    set_call(svc)
    res = svc.start("igate", apply=True)
    assert any(r.component == "loraham-igate" and r.outcome.value == "verified"
               for r in res.results)
    assert any("required post-start completed" in d for d in res.details)


# --- P0.4 required post-start never raises past the typed boundary ------------

def _req_comp():
    return Component(id="c", name="c", kind=ComponentKind.SERVICE, readiness="process",
                     run_argv=("true",),
                     post_steps=({"kind": "exec", "argv": ["true"], "required": True},))


def test_required_post_start_launcher_write_failure_is_typed(tmp_path, monkeypatch):
    from lhpc.core import runtime_fs
    from lhpc.core.jobs import JobState
    life = _real_life(tmp_path)
    def boom(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(runtime_fs, "write_launcher", boom)
    jr = life.run_required_post_start(STK, _req_comp())          # must NOT raise
    assert jr.state == JobState.FAILED and not jr.ok
    assert any("launcher write failed" in t for t in jr.tail)


def test_required_post_start_runner_spawn_failure_is_typed(tmp_path, monkeypatch):
    from lhpc.core import lifecycle as L
    from lhpc.core.jobs import JobState
    life = _real_life(tmp_path)
    def boom(*a, **k):
        raise OSError("no exec")
    monkeypatch.setattr(L, "run_job", boom)
    jr = life.run_required_post_start(STK, _req_comp())          # must NOT raise
    assert jr.state == JobState.FAILED and "runner failed to start" in " ".join(jr.tail)


def _opt_comp():
    return Component(id="c", name="c", kind=ComponentKind.SERVICE, readiness="process",
                     run_argv=("true",), post_steps=({"kind": "exec", "argv": ["true"]},))


_FAKE_BINDING = {"main_launch_id": "m", "main_pid": 1, "main_starttime": 1,
                 "main_pgid": 1, "main_sid": 1}


def test_optional_post_start_spawn_failure_is_typed_and_visible(tmp_path, monkeypatch):
    # REAL Lifecycle with a verified main binding stubbed: a runner spawn that raises OSError ->
    # typed PostStartSchedule (ok=False, NOT unverified), surfaced NON-gating by _run_post_start.
    from lhpc.core.lifecycle import PostStartSchedule
    from lhpc.core.services import ControllerService
    from lhpc.core.probes.backends import FakeSystem
    life = _real_life(tmp_path)
    monkeypatch.setattr(life, "_binding_for", lambda cid, band: dict(_FAKE_BINDING))
    def boom(*a, **k):
        raise OSError("cannot spawn")
    monkeypatch.setattr(life, "_spawn_post_runner", boom)
    sched = life.spawn_post_start(STK, _opt_comp(), {}, "")
    assert isinstance(sched, PostStartSchedule) and sched.ok is False and not sched.unverified
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    ok, summary = svc._run_post_start(life, STK, _opt_comp(), {}, "")
    assert ok is None and "could NOT be scheduled" in summary    # non-gating, visible


def test_detached_runner_requires_verified_main_binding(tmp_path):
    # No verified main record -> spawn NOTHING, typed integrity failure that GATES the main start.
    from lhpc.core.services import ControllerService
    from lhpc.core.probes.backends import FakeSystem
    life = _real_life(tmp_path)
    comp, stack = _pr(_free_port())
    sched = life.spawn_post_start(stack, comp, {}, "")
    assert sched.ok is False and sched.unverified is True
    assert life.owned_records("pr", role="post") == []           # nothing spawned/owned
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    gated, summary = svc._run_post_start(life, stack, comp, {}, "")
    assert gated is False and "integrity failure" in summary      # gates VERIFIED


def test_optional_post_start_containment_failure_is_typed(tmp_path):
    # REAL Lifecycle: a logs/ parent swapped to a symlink -> PathContainmentError from
    # the anchored log setup becomes a typed (ok=False) schedule result, not an exception.
    import os
    from lhpc.core.lifecycle import Lifecycle
    from lhpc.core.config import Config, OperatorConfig
    from lhpc.core.probes.backends import FakeSystem
    rt = tmp_path / "rt"; rt.mkdir()
    outside = tmp_path / "outside"; outside.mkdir()
    os.symlink(outside, rt / "logs")                  # logs/ -> outside the runtime root
    life = Lifecycle(Paths(runtime_root=rt), (), Config(operator=OperatorConfig()),
                     FakeSystem().system, spawn=lambda *a, **k: 1)
    sched = life.spawn_post_start(STK, _opt_comp(), {}, "")
    assert sched.ok is False and sched.detail        # typed, not raised
    assert not any(outside.iterdir())                # nothing created outside the root


@pytest.mark.needs_session
def test_optional_post_start_success_is_scheduled(tmp_path):
    # A real, ownable runner (long retry window) bound to a verified main schedules ok.
    life = _real_life(tmp_path)
    comp, stack = _pr(_free_port())
    p = _record_main(life, comp, stack)
    try:
        sched = life.spawn_post_start(stack, comp, {}, "")
        assert sched.ok is True
        life._cancel_post_runners(comp, None)
    finally:
        p.terminate(); p.wait()


@pytest.mark.needs_session
def test_ephemeral_start_params_override_saved_config(tmp_path, monkeypatch):
    # A value set on the start-confirm page (ephemeral params) must reach the launch + post-start,
    # overriding the saved config — fixes meshtastic NodeName / igate params not applying on start.
    svc = _igate_svc(tmp_path)
    svc.save_config("igate", {"tx_freq": "433.900"})            # saved default
    monkeypatch.setattr(ControllerService, "_lifecycle", _fake_life_factory)
    seen = {}
    def cap(self, life, stack, comp, comp_cfg, band):
        seen["cfg"] = dict(comp_cfg)
        return (None, "")
    monkeypatch.setattr(ControllerService, "_run_post_start", cap)
    set_call(svc)
    svc.start("igate", apply=True, params={"tx_freq": "434.500"})
    assert seen["cfg"]["tx_freq"] == "434.500"                  # ephemeral wins over saved 433.900


def test_tcp_send_retry_render():
    # A slow guest may open its port before it can process a command; tcp_send repeat/interval
    # re-sends until it lands (fixes MeshCom --setcall not applying before QEMU firmware boots).
    from lhpc.core import commands
    steps = [{"kind": "tcp_send", "port": 12323, "data": "--setcall X\n", "repeat": 5, "interval": 10}]
    class _C:
        run_params = []
    class _Op:
        callsign = "N0CALL"; locator = ""
    script = commands.render_post_launcher(steps, _C(), {}, _Op(), "/rt", "/src", "433")
    assert "'repeat': 5" in script and "'interval': 10" in script
    assert "range(reps)" in script and "time.sleep(s[\"interval\"])" in script


# --- PS1: truthful tcp_send retry semantics -------------------------------------------------

def _free_port():
    import socket
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close()
    return p


def _run_launcher(steps, timeout=25):
    import subprocess, sys, tempfile, os
    from lhpc.core import commands
    class _C:
        run_params = []
    class _Op:
        callsign = "N0CALL"; locator = ""
    script = commands.render_post_launcher(steps, _C(), {}, _Op(), "/rt", "/src", "")
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as f:
        f.write(script); path = f.name
    try:
        return subprocess.run([sys.executable, path], timeout=timeout).returncode
    finally:
        os.unlink(path)


def _serve_once(port, delay=0.0, accepts=1):
    import threading, socket, time
    def run():
        if delay:
            time.sleep(delay)
        s = socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("127.0.0.1", port)); s.listen(1)
        for _ in range(accepts):
            c, _a = s.accept(); c.recv(64); c.close()
        s.close()
    t = threading.Thread(target=run, daemon=True); t.start()
    return t


def test_required_tcp_send_no_listener_exits_nonzero():
    assert _run_launcher([{"kind": "tcp_send", "port": _free_port(), "data": "x\n", "required": True}]) != 0


def test_required_retry_all_fail_exits_nonzero():
    assert _run_launcher([{"kind": "tcp_send", "port": _free_port(), "data": "x\n",
                           "required": True, "repeat": 3, "interval": 0}]) != 0


def test_optional_retry_all_fail_exits_zero():
    assert _run_launcher([{"kind": "tcp_send", "port": _free_port(), "data": "x\n",
                           "optional": True, "repeat": 3, "interval": 0}]) == 0   # optional never gates


def test_retry_later_listener_accepts_exits_zero():
    port = _free_port()
    _serve_once(port, delay=1.5)
    assert _run_launcher([{"kind": "tcp_send", "port": port, "data": "x\n",
                           "required": True, "repeat": 8, "interval": 1}]) == 0


def test_first_success_then_failed_repeats_exits_zero():
    port = _free_port()
    _serve_once(port, delay=0.0, accepts=1)                          # serves ONE then stops
    import time; time.sleep(0.3)
    assert _run_launcher([{"kind": "tcp_send", "port": port, "data": "x\n",
                           "required": True, "repeat": 3, "interval": 1}]) == 0


def test_oneshot_tcp_send_unchanged():
    # one-shot (no repeat): optional with no listener exits 0; required with no listener exits != 0.
    assert _run_launcher([{"kind": "tcp_send", "port": _free_port(), "data": "x\n",
                           "optional": True}]) == 0
    assert _run_launcher([{"kind": "tcp_send", "port": _free_port(), "data": "x\n",
                           "required": True}]) != 0


def test_malformed_retry_fails_at_render():
    import pytest
    from lhpc.core import commands
    class _C:
        run_params = []
    class _Op:
        callsign = "N0CALL"; locator = ""
    for bad in ({"repeat": 0}, {"repeat": -1}, {"interval": -1}, {"interval": float("inf")}):
        step = {"kind": "tcp_send", "port": 1234, "data": "x\n", **bad}
        with pytest.raises(commands.CommandError):
            commands.render_post_launcher([step], _C(), {}, _Op(), "/rt", "/src", "")


# --- PS3: MeshCom N0CALL/empty placeholder-call guard (declarative) --------------------------

def _meshcom_launcher(mc_callsign, saved=None):
    import tempfile, pathlib
    from lhpc.core.paths import Paths
    from lhpc.core.probes.backends import FakeSystem
    from lhpc.core.services import ControllerService
    from lhpc.core import commands
    svc = ControllerService(system=FakeSystem().system,
                            paths=Paths(runtime_root=pathlib.Path(tempfile.mkdtemp())))
    qemu = svc.stack("meshcom").component("meshcom-qemu")
    cfg = dict(svc.stack_config("meshcom"))
    if saved is not None:
        cfg["mc_callsign"] = saved
    if mc_callsign is not None:
        cfg["mc_callsign"] = mc_callsign
    return commands.render_post_launcher(qemu.post_steps, qemu, cfg, svc.config().operator,
                                         "/rt", "/src", "433"), svc


def test_meshcom_legacy_shell_post_start_removed():
    _, svc = _meshcom_launcher("DJ0CHE")
    assert svc.stack("meshcom").component("meshcom-qemu").post_start == ""


def test_meshcom_n0call_schedules_no_setcall():
    script, _ = _meshcom_launcher("N0CALL")
    assert "setcall" not in script


def test_meshcom_empty_call_schedules_no_setcall():
    script, _ = _meshcom_launcher("")
    assert "setcall" not in script


def test_meshcom_saved_call_schedules_retrying_setcall():
    script, _ = _meshcom_launcher("DJ0CHE")
    assert "setcall DJ0CHE" in script and "'repeat': 21" in script


def test_meshcom_ephemeral_overrides_saved_and_leaves_config():
    script, svc = _meshcom_launcher("DJ0CHE-3", saved="DJ0CHE")
    assert "setcall DJ0CHE-3" in script                       # ephemeral wins for this launch
    assert svc.stack_config("meshcom").get("mc_callsign") != "DJ0CHE-3"   # saved not mutated


# --- PS2: detached post-start runners are owned + cancellable --------------------------------

def _pr(port, data="A\n"):
    from lhpc.core.model import Component, ComponentKind, Stack
    comp = Component(id="pr", name="pr", kind=ComponentKind.SERVICE,
                     post_steps=({"kind": "tcp_send", "port": port, "data": data,
                                  "optional": True, "repeat": 100, "interval": 1},))
    return comp, Stack(id="prs", name="prs", components=(comp,), main="pr")


def _record_main(life, comp, stack):
    # A real main session leader recorded as the verified role="" launch, so spawn_post_start has a
    # main to bind its runner to (a detached runner must never be unbound).
    import subprocess
    p = subprocess.Popen(["sleep", "60"], start_new_session=True)
    for _ in range(50):
        idn = life._capture_identity(p.pid)
        if idn and idn.get("exec") == "sleep":
            break
        time.sleep(0.05)
    assert life.record_launch(stack, comp, p.pid, "", ident=life._capture_identity(p.pid), role="")
    return p


@pytest.mark.needs_session
def test_post_runner_owned_and_cancellable(tmp_path):
    life = _real_life(tmp_path)
    comp, stack = _pr(_free_port())
    p = _record_main(life, comp, stack)
    try:
        assert life.spawn_post_start(stack, comp).ok
        recs = life.owned_records("pr", role="post")
        assert len(recs) == 1                              # runner bound to the launch
        pid = recs[0]["pid"]
        assert not life._proc_ceased(pid)                  # runner alive (long retry window)
        assert len(life.owned_records("pr")) == 1          # only the main; post runner not counted
        notes, unverified = life._cancel_post_runners(comp, None)
        assert not unverified and life.owned_records("pr", role="post") == []
        assert life._proc_ceased(pid)                      # runner terminated (gone/zombie)
    finally:
        p.terminate(); p.wait()


@pytest.mark.needs_session
def test_restart_isolation_old_runner_payload_not_delivered(tmp_path):
    import socket
    life = _real_life(tmp_path)
    port = _free_port()
    c1, s1 = _pr(port, "OLD\n")
    p = _record_main(life, c1, s1)
    try:
        assert life.spawn_post_start(s1, c1).ok
        life._cancel_post_runners(c1, None)                # stop before restart -> cancel old runner
        c2, s2 = _pr(port, "NEW\n")
        assert life.spawn_post_start(s2, c2).ok            # restart -> new runner (its own payload)
        srv = socket.socket(); srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("127.0.0.1", port)); srv.listen(2); srv.settimeout(8)
        got = b""
        try:
            conn, _a = srv.accept(); got = conn.recv(64); conn.close()
        finally:
            srv.close()
            life._cancel_post_runners(c2, None)
        assert b"NEW" in got and b"OLD" not in got         # only the new launch's payload arrives
    finally:
        p.terminate(); p.wait()


@pytest.mark.needs_session
def test_optional_post_start_scheduling_non_gating(tmp_path):
    # scheduling a runner returns ok (non-gating) and does not block on the retry window.
    life = _real_life(tmp_path)
    comp, stack = _pr(_free_port())
    p = _record_main(life, comp, stack)
    try:
        t0 = time.time()
        sched = life.spawn_post_start(stack, comp)
        assert sched.ok and (time.time() - t0) < 10        # returns promptly, not after ~100s
        life._cancel_post_runners(comp, None)
    finally:
        p.terminate(); p.wait()


# --- PB: main-launch binding, cleanup closure, and fail-closed metadata ----------------------

def _self_binding(**override):
    import os
    idn = Lifecycle._proc_identity(os.getpid())
    b = {"main_launch_id": "x", "main_pid": os.getpid(), "main_starttime": idn["starttime"],
         "main_pgid": idn["pgid"], "main_sid": idn["sid"]}
    b.update(override)
    return b


def _run_bound(binding, port, data="P\n", repeat=4, interval=1, timeout=18):
    import subprocess, sys, tempfile, os, socket, threading
    from lhpc.core import commands
    class _C:
        run_params = []
    class _Op:
        callsign = "N0CALL"; locator = ""
    step = {"kind": "tcp_send", "port": port, "data": data, "optional": True,
            "repeat": repeat, "interval": interval}
    script = commands.render_post_launcher([step], _C(), {}, _Op(), "/rt", "/src", "", binding=binding)
    got = {"b": b""}
    def serve():
        srv = socket.socket(); srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("127.0.0.1", port)); srv.listen(1); srv.settimeout(timeout)
        try:
            conn, _a = srv.accept(); got["b"] = conn.recv(64); conn.close()
        except socket.timeout:
            pass
        finally:
            srv.close()
    t = threading.Thread(target=serve, daemon=True); t.start()
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as f:
        f.write(script); path = f.name
    try:
        subprocess.run([sys.executable, path], timeout=timeout + 5)
    finally:
        os.unlink(path)
    t.join(timeout=2)
    return got["b"]


@pytest.mark.needs_session
def test_bound_runner_sends_when_main_matches():
    assert _run_bound(_self_binding(), _free_port()) == b"P\n"


@pytest.mark.needs_session
def test_bound_runner_skips_send_when_main_starttime_changed():
    # main pid reused with a NEW start time (crash + later launch) -> old runner must not send.
    assert _run_bound(_self_binding(main_starttime=1), _free_port()) == b""


def test_bound_runner_skips_send_when_main_gone():
    dead = {"main_launch_id": "x", "main_pid": 2 ** 31 - 1, "main_starttime": 1,
            "main_pgid": 1, "main_sid": 1}
    assert _run_bound(dead, _free_port()) == b""


def _main_leader(life, port):
    # A real main session leader recorded under the SAME component that has the post_steps, so
    # spawn_post_start can bind its runner to this main launch.
    comp, stack = _pr(port)                       # comp id "pr" WITH post_steps
    p = _record_main(life, comp, stack)
    return p, comp, stack


@pytest.mark.needs_session
def test_post_runner_binding_recorded_from_main(tmp_path):
    life = _real_life(tmp_path)
    p, comp, stack = _main_leader(life, _free_port())
    try:
        assert life.spawn_post_start(stack, comp, {}, "").ok
        prec = life.owned_records("pr", role="post")[0]
        assert prec.get("main_pid") == p.pid and prec.get("main_starttime")
    finally:
        life._cancel_post_runners(comp, None)
        p.terminate(); p.wait()


@pytest.mark.needs_session
def test_stop_aborts_main_when_runner_unverified(tmp_path, monkeypatch):
    life = _real_life(tmp_path)
    p, comp, stack = _main_leader(life, _free_port())
    try:
        assert life.spawn_post_start(stack, comp, {}, "").ok
        # Force the post-runner to look live-but-unverifiable.
        monkeypatch.setattr(life, "verify_owned", lambda rec: (False, "forced"))
        monkeypatch.setattr(life, "_original_ceased", lambda rec: False)
        signalled = []
        monkeypatch.setattr(life, "_wait_ceased", lambda rec: signalled.append(1) or False)
        res = life.stop(comp)
        assert res.outcome.value == "unverified"                 # non-success
        assert "main NOT signalled" in res.summary               # main never signalled
    finally:
        p.terminate(); p.wait()


@pytest.mark.needs_session
def test_stale_record_removal_failure_is_unverified(tmp_path, monkeypatch):
    life = _real_life(tmp_path)
    p, comp, stack = _main_leader(life, _free_port())
    try:
        assert life.spawn_post_start(stack, comp, {}, "").ok
        monkeypatch.setattr(life, "verify_owned", lambda rec: (False, "gone"))
        monkeypatch.setattr(life, "_original_ceased", lambda rec: True)   # runner ended
        monkeypatch.setattr(life, "_remove_record", lambda rec: False)    # but record won't clear
        notes, unverified = life._cancel_post_runners(comp, None)
        assert unverified                                        # stale-record removal failure typed
    finally:
        life._cancel_post_runners(comp, None)
        p.terminate(); p.wait()


@pytest.mark.needs_session
def test_spawn_record_fail_verified_cleanup_leaves_no_runner(tmp_path, monkeypatch):
    # A real gated runner is spawned; record write fails -> it is never armed and exits doing
    # nothing; proven cessation -> typed scheduling failure, no runner left.
    life = _real_life(tmp_path)
    comp, stack = _pr(_free_port())
    monkeypatch.setattr(life, "_binding_for", lambda cid, band: dict(_FAKE_BINDING))
    monkeypatch.setattr(life, "record_launch", lambda *a, **k: False)     # persistence fails
    sched = life.spawn_post_start(stack, comp, {}, "")
    assert sched.ok is False and "terminated" in sched.detail and not sched.unverified


def test_spawn_record_fail_unverified_cleanup_is_typed(tmp_path, monkeypatch):
    life = _real_life(tmp_path)
    comp, stack = _pr(_free_port())
    monkeypatch.setattr(life, "_binding_for", lambda cid, band: dict(_FAKE_BINDING))
    monkeypatch.setattr(life, "record_launch", lambda *a, **k: False)
    monkeypatch.setattr(life, "_terminate_unobserved", lambda pid, ident=None: False)  # not proved
    sched = life.spawn_post_start(stack, comp, {}, "")
    assert sched.ok is False and sched.unverified is True         # never "scheduled"/"cancelled"
    assert "scheduled" not in sched.detail and "cancelled" not in sched.detail


def test_render_rejects_bad_retry_and_guard_metadata():
    import pytest
    from lhpc.core import commands
    class _C:
        class _P:
            name = "call"; default = "x"; kind = "str"; validator = ""
        run_params = [_P()]
    class _Op:
        callsign = "N0CALL"; locator = ""
    base = {"kind": "tcp_send", "port": 1234, "data": "x\n"}
    for bad in ({"repeat": 1.5}, {"repeat": True}, {"interval": True},
                {"skip_if_param": "unknown_param"}):
        with pytest.raises(commands.CommandError):
            commands.render_post_launcher([{**base, **bad}], _C(), {}, _Op(), "/rt", "/src", "")


def _pr_main(port):
    from lhpc.core.model import Component, ComponentKind, Stack
    comp = Component(id="pr", name="pr", kind=ComponentKind.SERVICE, run_argv=("sleep", "3"),
                     post_steps=({"kind": "tcp_send", "port": port, "data": "X\n",
                                  "optional": True, "repeat": 100, "interval": 1},))
    return comp, Stack(id="prs", name="prs", components=(comp,), main="pr")


@pytest.mark.needs_session
def test_preflight_cancels_stale_runner_before_new_start(tmp_path):
    life = _real_life(tmp_path)
    comp, stack = _pr_main(_free_port())
    prior = _record_main(life, comp, stack)                        # prior launch's main (stays live)
    try:
        assert life.spawn_post_start(stack, comp, {}, "").ok       # a LIVE stale runner
        stale = life.owned_records("pr", role="post")[0]["pid"]
        res = life.start(stack, comp, {}, "")                     # preflights + cancels it
        assert res.ok
        assert life.owned_records("pr", role="post") == []        # stale runner gone
        assert life._proc_ceased(stale)
    finally:
        life.stop(comp)
        prior.terminate(); prior.wait()


@pytest.mark.needs_session
def test_preflight_blocks_new_start_when_stale_runner_unverifiable(tmp_path, monkeypatch):
    life = _real_life(tmp_path)
    comp, stack = _pr_main(_free_port())
    prior = _record_main(life, comp, stack)
    try:
        assert life.spawn_post_start(stack, comp, {}, "").ok
        monkeypatch.setattr(life, "verify_owned", lambda rec: (False, "forced"))
        monkeypatch.setattr(life, "_original_ceased", lambda rec: False)
        monkeypatch.setattr(life, "_wait_ceased", lambda rec: False)
        res = life.start(stack, comp, {}, "")
        assert not res.ok and "could not be verified stopped" in res.detail   # new launch blocked
    finally:
        monkeypatch.undo()
        life._cancel_post_runners(comp, None)
        prior.terminate(); prior.wait()


@pytest.mark.needs_session
def test_main_exit_before_post_scheduling_yields_no_unbound_runner(tmp_path):
    # The main dies between launch/readiness and post scheduling -> no verified main -> the runner
    # is never spawned (it can never come up unbound).
    life = _real_life(tmp_path)
    comp, stack = _pr(_free_port())
    p = _record_main(life, comp, stack)
    p.terminate(); p.wait()
    sched = life.spawn_post_start(stack, comp, {}, "")
    assert sched.ok is False and sched.unverified is True
    assert life.owned_records("pr", role="post") == []


def test_record_write_failure_delivers_no_payload(tmp_path, monkeypatch):
    import socket
    life = _real_life(tmp_path)
    port = _free_port()
    comp, stack = _pr(port, "PAY\n")
    monkeypatch.setattr(life, "_binding_for", lambda cid, band: dict(_FAKE_BINDING))
    monkeypatch.setattr(life, "record_launch", lambda *a, **k: False)   # record never durable
    srv = socket.socket(); srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", port)); srv.listen(1); srv.settimeout(4)
    sched = life.spawn_post_start(stack, comp, {}, "")               # gated runner, never armed
    got = b""
    try:
        conn, _a = srv.accept(); got = conn.recv(64)
    except socket.timeout:
        pass
    finally:
        srv.close()
    assert sched.ok is False and got == b""                          # no side effect ever


def test_bound_runner_skips_send_when_main_zombie(tmp_path):
    import subprocess
    z = subprocess.Popen(["true"])                                   # exits immediately -> zombie
    try:
        rest = None
        for _ in range(100):
            try:
                with open("/proc/%d/stat" % z.pid, "rb") as f:
                    rest = f.read().rsplit(b") ", 1)[1].split()
            except OSError:
                break
            if rest[0] == b"Z":
                break
            time.sleep(0.02)
        assert rest and rest[0] == b"Z"                              # confirmed zombie
        # binding matches the zombie's start time + session/group -> ONLY the state gates it
        binding = {"main_launch_id": "z", "main_pid": z.pid, "main_starttime": int(rest[19]),
                   "main_pgid": int(rest[2]), "main_sid": int(rest[3])}
        assert _run_bound(binding, _free_port()) == b""              # zombie -> no connect, no send
    finally:
        z.wait()


@pytest.mark.needs_session
def test_bound_runner_skips_send_when_session_or_group_changed():
    assert _run_bound(_self_binding(main_sid=999999), _free_port()) == b""
    assert _run_bound(_self_binding(main_pgid=999999), _free_port()) == b""


def test_render_rejects_malformed_binding_and_nonstring_skip_values():
    import pytest
    from lhpc.core import commands
    class _C:
        class _P:
            name = "call"; default = "x"; kind = "str"; validator = ""
        run_params = [_P()]
    class _Op:
        callsign = "N0CALL"; locator = ""
    send = {"kind": "tcp_send", "port": 1, "data": "x\n"}
    for badb in ({"main_launch_id": "m", "main_pid": 0, "main_starttime": 1, "main_pgid": 1,
                  "main_sid": 1},                                     # non-positive pid
                 {"main_launch_id": "m", "main_pid": True, "main_starttime": 1, "main_pgid": 1,
                  "main_sid": 1},                                     # boolean pid
                 {"main_pid": 1, "main_starttime": 1, "main_pgid": 1, "main_sid": 1}):  # no id
        with pytest.raises(commands.CommandError):
            commands.render_post_launcher([send], _C(), {}, _Op(), "/rt", "/src", "", binding=badb)
    for badsv in ([True], [1], [1.5], [None], ["ok", 1]):
        step = {"kind": "tcp_send", "port": 1, "data": "x\n", "skip_if_param": "call",
                "skip_values": badsv}
        with pytest.raises(commands.CommandError):
            commands.render_post_launcher([step], _C(), {}, _Op(), "/rt", "/src", "")
    ok = {"kind": "tcp_send", "port": 1, "data": "x\n", "skip_if_param": "call",
          "skip_values": ["", "N0CALL"]}
    commands.render_post_launcher([ok], _C(), {}, _Op(), "/rt", "/src", "")   # valid -> no raise


# --- corrections: retry spacing, arm-failure closure, strict falsey skip_values --------------

def test_tcp_send_spaces_successful_deliveries_by_interval(tmp_path):
    # Three SUCCESSFUL deliveries (listener accepts every attempt) are measurably spaced by the
    # validated interval — the sleep applies after a success, not only after a failure.
    import socket, threading, subprocess, sys, tempfile, os
    from lhpc.core import commands
    class _C:
        run_params = []
    class _Op:
        callsign = "N0CALL"; locator = ""
    srv = socket.socket(); srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0)); port = srv.getsockname()[1]; srv.listen(8); srv.settimeout(20)
    stamps = []
    def serve():
        for _ in range(3):
            try:
                c, _a = srv.accept(); c.recv(64); stamps.append(time.monotonic()); c.close()
            except socket.timeout:
                break
    t = threading.Thread(target=serve, daemon=True); t.start()
    step = {"kind": "tcp_send", "port": port, "data": "x\n", "optional": True,
            "repeat": 3, "interval": 1.0}
    script = commands.render_post_launcher([step], _C(), {}, _Op(), "/rt", "/src", "")
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as f:
        f.write(script); path = f.name
    try:
        subprocess.run([sys.executable, path], timeout=20)
    finally:
        t.join(3); os.unlink(path); srv.close()
    assert len(stamps) == 3                                     # every attempt delivered
    assert stamps[1] - stamps[0] >= 0.8                        # spaced by ~interval
    assert stamps[2] - stamps[1] >= 0.8


@pytest.mark.needs_session
def test_arm_write_exception_is_not_scheduled(tmp_path, monkeypatch):
    # os.write raising AFTER a durable record: no payload, not scheduled, record removed once
    # cessation is proven.
    import socket
    life = _real_life(tmp_path)
    port = _free_port()
    comp, stack = _pr(port, "PAY\n")
    p = _record_main(life, comp, stack)
    def boom(fd):
        raise OSError("EPIPE")
    monkeypatch.setattr(life, "_arm", boom)
    srv = socket.socket(); srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", port)); srv.listen(1); srv.settimeout(4)
    try:
        sched = life.spawn_post_start(stack, comp, {}, "")
        got = b""
        try:
            conn, _a = srv.accept(); got = conn.recv(64)
        except socket.timeout:
            pass
        assert sched.ok is False and "scheduled" not in sched.detail and not sched.unverified
        assert got == b""                                      # no side effect ever
        assert life.owned_records("pr", role="post") == []     # record removed after cessation
    finally:
        srv.close(); p.terminate(); p.wait()


@pytest.mark.needs_session
def test_arm_write_zero_is_not_scheduled(tmp_path, monkeypatch):
    # A short/zero write (os.write -> 0) is treated identically: non-success, record removed.
    life = _real_life(tmp_path)
    comp, stack = _pr(_free_port())
    p = _record_main(life, comp, stack)
    monkeypatch.setattr(life, "_arm", lambda fd: False)
    try:
        sched = life.spawn_post_start(stack, comp, {}, "")
        assert sched.ok is False and "scheduled" not in sched.detail and not sched.unverified
        assert life.owned_records("pr", role="post") == []
    finally:
        p.terminate(); p.wait()


@pytest.mark.needs_session
def test_arm_failure_uncertain_cessation_is_unverified(tmp_path, monkeypatch):
    # Arm fails and the runner's cessation cannot be proven: unverified=True, not scheduled, and the
    # ownership record is RETAINED.
    life = _real_life(tmp_path)
    comp, stack = _pr(_free_port())
    p = _record_main(life, comp, stack)
    real_verify = life.verify_owned
    monkeypatch.setattr(life, "_arm", lambda fd: False)
    monkeypatch.setattr(life, "verify_owned",
                        lambda rec: (False, "forced") if rec.get("role") == "post"
                        else real_verify(rec))
    monkeypatch.setattr(life, "_original_ceased", lambda rec: False)
    try:
        sched = life.spawn_post_start(stack, comp, {}, "")
        assert sched.ok is False and sched.unverified is True and "scheduled" not in sched.detail
        assert life.owned_records("pr", role="post")           # ownership retained
    finally:
        monkeypatch.undo()
        life._cancel_post_runners(comp, None)
        p.terminate(); p.wait()


def test_render_skip_values_strict_falsey_and_valid():
    import pytest
    from lhpc.core import commands
    class _C:
        class _P:
            name = "call"; default = "x"; kind = "str"; validator = ""
        run_params = [_P()]
    class _Op:
        callsign = "N0CALL"; locator = ""
    base = {"kind": "tcp_send", "port": 1, "data": "x\n", "skip_if_param": "call"}
    def render(step):
        return commands.render_post_launcher([step], _C(), {}, _Op(), "/rt", "/src", "")
    for bad in (None, False, 0, "", {}, [True], [1], [1.5], [None], ["ok", 1]):
        with pytest.raises(commands.CommandError):
            render({**base, "skip_values": bad})
    for good in ([], (), ["", "N0CALL"]):
        render({**base, "skip_values": good})                  # valid -> no raise
    render(base)                                               # absent key defaults to [] -> valid
