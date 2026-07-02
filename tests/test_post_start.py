"""§7 — required vs optional post-start, and §5 typed start aggregation."""

import time

from lhpc.core.lifecycle import Lifecycle
from conftest import real_spawn
from lhpc.core.services import ControllerService
from lhpc.core.config import Config, OperatorConfig
from lhpc.core.model import Component, ComponentKind, Stack
from lhpc.core.paths import Paths
from lhpc.core.probes import RealSystem
from lhpc.core.probes.backends import FakeSystem


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


def test_start_required_post_start_failure_is_unverified(tmp_path, monkeypatch):
    # Drives ControllerService.start() and exercises the CALLER's handling of the typed
    # required-post-start result (the bug was the caller using a nonexistent Outcome.ok).
    svc = _igate_svc(tmp_path)
    monkeypatch.setattr(ControllerService, "_lifecycle", _fake_life_factory)
    monkeypatch.setattr(ControllerService, "_run_post_start",
                        lambda self, *a, **k: (False, "required post-start failed (rc 7)"))
    res = svc.start("igate", apply=True)            # must NOT raise AttributeError
    assert not res.ok
    assert any("required post-start failed" in d for d in res.details)
    assert any(r.component == "loraham-igate" and r.outcome.value == "unverified"
               for r in res.results)


def test_start_required_post_start_success_verifies(tmp_path, monkeypatch):
    svc = _igate_svc(tmp_path)
    monkeypatch.setattr(ControllerService, "_lifecycle", _fake_life_factory)
    monkeypatch.setattr(ControllerService, "_run_post_start",
                        lambda self, *a, **k: (True, "required post-start completed"))
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


def test_optional_post_start_spawn_failure_is_typed_and_visible(tmp_path):
    # REAL Lifecycle: an injected spawn that raises OSError -> typed PostStartSchedule
    # (ok=False), surfaced by _run_post_start as a visible NON-gating detail.
    from lhpc.core.lifecycle import Lifecycle, PostStartSchedule
    from lhpc.core.services import ControllerService
    from lhpc.core.config import Config, OperatorConfig
    from lhpc.core.probes.backends import FakeSystem
    def boom_spawn(*a, **k):
        raise OSError("cannot spawn")
    life = Lifecycle(Paths(runtime_root=tmp_path), (), Config(operator=OperatorConfig()),
                     FakeSystem().system, spawn=boom_spawn)
    sched = life.spawn_post_start(STK, _opt_comp(), {}, "")
    assert isinstance(sched, PostStartSchedule) and sched.ok is False and sched.detail
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    ok, summary = svc._run_post_start(life, STK, _opt_comp(), {}, "")
    assert ok is None and "could NOT be scheduled" in summary    # non-gating, visible


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


def test_optional_post_start_success_is_scheduled(tmp_path):
    # A real, ownable runner (long retry window) schedules ok.
    life = _real_life(tmp_path)
    comp, stack = _pr(_free_port())
    sched = life.spawn_post_start(stack, comp, {}, "")
    assert sched.ok is True
    life._cancel_post_runners(comp, None)


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
    assert "setcall DJ0CHE" in script and "'repeat': 14" in script


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


def test_post_runner_owned_and_cancellable(tmp_path):
    import os
    life = _real_life(tmp_path)
    comp, stack = _pr(_free_port())
    assert life.spawn_post_start(stack, comp).ok
    recs = life.owned_records("pr", role="post")
    assert len(recs) == 1                                  # runner bound to the launch
    pid = recs[0]["pid"]
    assert not life._proc_ceased(pid)                      # runner alive (long retry window)
    assert life.owned_records("pr") == []                  # NOT counted as the main component
    notes, unverified = life._cancel_post_runners(comp, None)
    assert not unverified and life.owned_records("pr", role="post") == []
    assert life._proc_ceased(pid)                          # runner terminated (gone/zombie)


def test_restart_isolation_old_runner_payload_not_delivered(tmp_path):
    import socket
    life = _real_life(tmp_path)
    port = _free_port()
    c1, s1 = _pr(port, "OLD\n")
    assert life.spawn_post_start(s1, c1).ok
    life._cancel_post_runners(c1, None)                    # stop before restart -> cancel old runner
    c2, s2 = _pr(port, "NEW\n")
    assert life.spawn_post_start(s2, c2).ok                # restart -> new runner (its own payload)
    srv = socket.socket(); srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", port)); srv.listen(2); srv.settimeout(8)
    got = b""
    try:
        conn, _a = srv.accept(); got = conn.recv(64); conn.close()
    finally:
        srv.close()
        life._cancel_post_runners(c2, None)
    assert b"NEW" in got and b"OLD" not in got             # only the new launch's payload arrives


def test_optional_post_start_scheduling_non_gating(tmp_path):
    # scheduling a runner returns ok (non-gating) and does not block on the retry window.
    life = _real_life(tmp_path)
    comp, stack = _pr(_free_port())
    t0 = time.time()
    sched = life.spawn_post_start(stack, comp)
    assert sched.ok and (time.time() - t0) < 10           # returns promptly, not after ~100s
    life._cancel_post_runners(comp, None)


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


def test_bound_runner_sends_when_main_matches():
    assert _run_bound(_self_binding(), _free_port()) == b"P\n"


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
    import subprocess
    comp, stack = _pr(port)                       # comp id "pr" WITH post_steps
    p = subprocess.Popen(["sleep", "60"], start_new_session=True)
    for _ in range(50):
        idn = life._capture_identity(p.pid)
        if idn and idn.get("exec") == "sleep":
            break
        time.sleep(0.05)
    assert life.record_launch(stack, comp, p.pid, "", ident=life._capture_identity(p.pid), role="")
    return p, comp, stack


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


def test_spawn_record_fail_verified_cleanup_leaves_no_runner(tmp_path, monkeypatch):
    life = _real_life(tmp_path)
    comp, stack = _pr(_free_port())
    monkeypatch.setattr(life, "record_launch", lambda *a, **k: False)     # persistence fails
    sched = life.spawn_post_start(stack, comp, {}, "")
    assert sched.ok is False and "terminated" in sched.detail and not sched.unverified


def test_spawn_record_fail_unverified_cleanup_is_typed(tmp_path, monkeypatch):
    life = _real_life(tmp_path)
    comp, stack = _pr(_free_port())
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


def test_preflight_cancels_stale_runner_before_new_start(tmp_path):
    life = _real_life(tmp_path)
    comp, stack = _pr_main(_free_port())
    assert life.spawn_post_start(stack, comp, {}, "").ok           # a stale runner (no main yet)
    stale = life.owned_records("pr", role="post")[0]["pid"]
    try:
        res = life.start(stack, comp, {}, "")                     # preflights + cancels it
        assert res.ok
        assert life.owned_records("pr", role="post") == []        # stale runner gone
        assert life._proc_ceased(stale)
    finally:
        life.stop(comp)


def test_preflight_blocks_new_start_when_stale_runner_unverifiable(tmp_path, monkeypatch):
    life = _real_life(tmp_path)
    comp, stack = _pr_main(_free_port())
    assert life.spawn_post_start(stack, comp, {}, "").ok
    try:
        monkeypatch.setattr(life, "verify_owned", lambda rec: (False, "forced"))
        monkeypatch.setattr(life, "_original_ceased", lambda rec: False)
        monkeypatch.setattr(life, "_wait_ceased", lambda rec: False)
        res = life.start(stack, comp, {}, "")
        assert not res.ok and "could not be verified stopped" in res.detail   # new launch blocked
    finally:
        monkeypatch.undo()
        life._cancel_post_runners(comp, None)
