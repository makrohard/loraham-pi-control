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


def _bound_life(tmp_path, monkeypatch):
    """A real Lifecycle bound to a LIVE main (this test process), so the required post-start path
    — which now refuses to run without a verified main launch — can execute its steps."""
    import os
    life = _real_life(tmp_path)
    st = life._proc_identity(os.getpid()) or {}
    monkeypatch.setattr(life, "_binding_for", lambda cid, band: {
        "main_launch_id": "test-main", "main_pid": os.getpid(),
        "main_starttime": int(st.get("starttime", 1)),
        "main_pgid": int(st.get("pgid", os.getpgrp())),
        "main_sid": int(st.get("sid", os.getsid(0)))})
    return life


def test_required_post_start_failure_is_typed(tmp_path, monkeypatch):
    comp = Component(id="c", name="c", kind=ComponentKind.SERVICE, readiness="process",
                     run_argv=("true",),
                     post_steps=({"kind": "exec", "argv": ["false"], "required": True},))
    life = _bound_life(tmp_path, monkeypatch)
    assert life.has_required_post_start(comp)
    jr = life.run_required_post_start(STK, comp)
    assert not jr.ok and jr.returncode != 0


def test_required_post_start_success_is_typed(tmp_path, monkeypatch):
    comp = Component(id="c", name="c", kind=ComponentKind.SERVICE, readiness="process",
                     run_argv=("true",),
                     post_steps=({"kind": "exec", "argv": ["true"], "required": True},))
    life = _bound_life(tmp_path, monkeypatch)
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
    life = _bound_life(tmp_path, monkeypatch)
    def boom(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(runtime_fs, "write_launcher", boom)
    jr = life.run_required_post_start(STK, _req_comp())          # must NOT raise
    assert jr.state == JobState.FAILED and not jr.ok
    assert any("launcher write failed" in t for t in jr.tail)


def test_required_post_start_runner_spawn_failure_is_typed(tmp_path, monkeypatch):
    from lhpc.core import lifecycle as L
    from lhpc.core.jobs import JobState
    life = _bound_life(tmp_path, monkeypatch)
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
    def cap(self, life, stack, comp, comp_cfg, band, announce=None, strict=False):
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
        callsign = "N0CALL"
    script = commands.render_post_launcher(steps, _C(), {}, _Op(), "/rt", "/src", "433")
    assert "'repeat': 5" in script and "'interval': 10" in script
    # The per-attempt sleep list is precomputed at render time (one runner code path for
    # fixed and stepped cadence); fixed interval renders as N-1 identical entries.
    assert "'intervals': [10.0, 10.0, 10.0, 10.0]" in script
    assert "range(reps)" in script and "time.sleep(iv)" in script


# --- PS1: truthful tcp_send retry semantics -------------------------------------------------

def _free_port():
    import socket
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close()
    return p


def _run_launcher(steps, timeout=25, result_path="", runtime="/rt"):
    # A sidecar path is now validated against the RUNTIME ROOT at render time and walked
    # descriptor-anchored by the runner, so a caller that wants one must name the root the
    # result lives under (see _sidecar_root).
    import subprocess, sys, tempfile, os
    from lhpc.core import commands
    class _C:
        run_params = []
    class _Op:
        callsign = "N0CALL"
    script = commands.render_post_launcher(steps, _C(), {}, _Op(), runtime, "/src", "",
                                           result_path=result_path)
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
        callsign = "N0CALL"
    for bad in ({"repeat": 0}, {"repeat": -1}, {"interval": -1}, {"interval": float("inf")}):
        step = {"kind": "tcp_send", "port": 1234, "data": "x\n", **bad}
        with pytest.raises(commands.CommandError):
            commands.render_post_launcher([step], _C(), {}, _Op(), "/rt", "/src", "")


# --- PS4: stepped backoff schedule + terminal-outcome result sidecar --------------------------

def _render_steps(steps, result_path="", runtime="/rt"):
    from lhpc.core import commands
    class _C:
        run_params = []
    class _Op:
        callsign = "N0CALL"
    return commands.render_post_launcher(steps, _C(), {}, _Op(), runtime, "/src", "",
                                         result_path=result_path)


def _sidecar_root(tmp_path):
    """A real runtime root with the state/post parent the sidecar is published into."""
    root = tmp_path / "rt"
    (root / "state" / "post").mkdir(parents=True, exist_ok=True)
    return root, root / "state" / "post" / "r.json"


def test_schedule_renders_precomputed_intervals():
    # schedule [[2,1],[2,5]] = 4 attempts; sleeps AFTER attempts 1..3 -> [1, 1, 5].
    script = _render_steps([{"kind": "tcp_send", "port": 1, "data": "x\n",
                             "schedule": [[2, 1], [2, 5]]}])
    assert "'repeat': 4" in script
    assert "'intervals': [1.0, 1.0, 5.0]" in script


def test_schedule_rejects_malformed_and_ambiguous():
    from lhpc.core import commands
    bads = (
        {"schedule": [[2, 1]], "repeat": 2},          # ambiguous: two window definitions
        {"schedule": [[2, 1]], "interval": 1},
        {"schedule": []},                              # empty
        {"schedule": [[0, 1]]},                        # zero count
        {"schedule": [[1, -1]]},                       # negative interval
        {"schedule": [[True, 1]]},                     # boolean count
        {"schedule": [[1]]},                           # not a pair
        {"schedule": "x"},                             # not a list
        {"schedule": [[1, float("inf")]]},             # non-finite interval
    )
    for bad in bads:
        with pytest.raises(commands.CommandError):
            _render_steps([{"kind": "tcp_send", "port": 1, "data": "x\n", **bad}])


def test_schedule_spacing_measured():
    # Real deliveries: schedule [[2, 0.2], [2, 0.8]] -> gaps ~0.2, ~0.2, ~0.8 between the four
    # sends. Assert the ORDER of magnitude (early tight, late backed off), not exact timing.
    import socket, threading, time as _t
    port = _free_port()
    stamps = []
    def run():
        s = socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("127.0.0.1", port)); s.listen(4)
        for _ in range(4):
            c, _a = s.accept(); stamps.append(_t.monotonic()); c.recv(64); c.close()
        s.close()
    t = threading.Thread(target=run, daemon=True); t.start()
    assert _run_launcher([{"kind": "tcp_send", "port": port, "data": "x\n", "optional": True,
                           "schedule": [[2, 0.2], [2, 0.8]]}]) == 0
    t.join(5)
    assert len(stamps) == 4
    gaps = [b - a for a, b in zip(stamps, stamps[1:])]
    assert gaps[0] < 0.6 and gaps[1] < 0.6           # tight early cadence
    assert gaps[2] >= 0.6                             # backed-off tail


def test_result_file_on_exhaustion(tmp_path):
    import json
    root, rp = _sidecar_root(tmp_path)
    assert _run_launcher([{"kind": "tcp_send", "port": _free_port(), "data": "x\n",
                           "optional": True, "repeat": 2, "interval": 0}],
                         result_path=str(rp), runtime=str(root)) == 0
    data = json.loads(rp.read_text())
    st = data["steps"][0]
    assert st["outcome"] == "exhausted" and st["attempts"] == 2
    assert st["kind"] == "tcp_send" and isinstance(st["elapsed_s"], (int, float))
    assert data["done"] is True                       # optional exhaustion completes the runner


def test_result_file_on_required_exhaustion_flushed_before_exit(tmp_path):
    import json
    root, rp = _sidecar_root(tmp_path)
    assert _run_launcher([{"kind": "tcp_send", "port": _free_port(), "data": "x\n",
                           "required": True, "repeat": 2, "interval": 0}],
                         result_path=str(rp), runtime=str(root)) != 0
    data = json.loads(rp.read_text())                 # written despite the nonzero exit
    assert data["steps"][0]["outcome"] == "exhausted" and data["done"] is False


def test_result_file_label_and_sent_unacked(tmp_path):
    # A send that completes but never sees the ack pattern is 'sent-unacked' (visible
    # degradation, not silence); the step's label is carried into the result.
    import json
    port = _free_port()
    _serve_once(port)                                  # accepts, replies nothing
    import time as _t; _t.sleep(0.2)
    root, rp = _sidecar_root(tmp_path)
    assert _run_launcher([{"kind": "tcp_send", "port": port, "data": "x\n", "optional": True,
                           "label": "callsign", "stop_on": "NEVER", "repeat": 1}],
                         result_path=str(rp), runtime=str(root)) == 0
    st = json.loads(rp.read_text())["steps"][0]
    assert st["label"] == "callsign" and st["outcome"] == "sent-unacked"


def test_no_result_path_renders_inert_sidecar():
    # Without a result_path the runner's sidecar machinery is inert (RESULT = '').
    script = _render_steps([{"kind": "tcp_send", "port": 1, "data": "x\n"}])
    assert "RESULT_REL = ()" in script


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
    # The shipped step uses the STEPPED schedule [[8,8],[6,15],[22,30]] = 36 attempts over a
    # ~13 min window (Zero-2W QEMU/TCG cold boot takes minutes; the old 168 s window expired
    # with zero sends and the node ran with the default callsign — live finding).
    script, _ = _meshcom_launcher("DJ0CHE")
    assert "setcall DJ0CHE" in script and "'repeat': 36" in script
    assert "'label': 'callsign'" in script


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
        callsign = "N0CALL"
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
        callsign = "N0CALL"
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


@pytest.mark.needs_session
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
        callsign = "N0CALL"
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
        callsign = "N0CALL"
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
        callsign = "N0CALL"
    base = {"kind": "tcp_send", "port": 1, "data": "x\n", "skip_if_param": "call"}
    def render(step):
        return commands.render_post_launcher([step], _C(), {}, _Op(), "/rt", "/src", "")
    for bad in (None, False, 0, "", {}, [True], [1], [1.5], [None], ["ok", 1]):
        with pytest.raises(commands.CommandError):
            render({**base, "skip_values": bad})
    for good in ([], (), ["", "N0CALL"]):
        render({**base, "skip_values": good})                  # valid -> no raise
    render(base)                                               # absent key defaults to [] -> valid


# --- PS5: terminal-outcome surfacing (status) + the poststart re-apply verb -------------------

def _sidecar_svc(tmp_path, steps, alive_pid=None):
    """A service whose runtime root carries a fabricated CEASED role='post' record with a
    result sidecar (no live main -> the binding cross-check is skipped)."""
    import json
    from lhpc.core import runtime_fs
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    life = svc._lifecycle()
    result = tmp_path / "state" / "post" / "u1.result.json"
    runtime_fs.ensure_dir(life.paths, result.parent)
    result.write_text(json.dumps({"v": 1, "steps": steps, "done": True}))
    rec = {"launch_id": "meshcom-qemu__x__post-99999999", "stack": "meshcom",
           "component": "meshcom-qemu", "band": "", "pid": alive_pid or 99999999,
           "role": "post", "result_path": str(result),
           "log_path": str(tmp_path / "logs" / "post-u1.log")}
    if alive_pid:
        # _original_ceased treats a starttime mismatch as confirmed pid reuse -> record the
        # LIVE starttime so the runner reads as still alive (retry window active).
        rec["starttime"] = (life._proc_identity(alive_pid) or {}).get("starttime")
    owned = life._owned_dir()
    runtime_fs.ensure_dir(life.paths, owned)
    (owned / f"{rec['launch_id']}.json").write_text(json.dumps(rec))
    return svc


def test_status_shows_confirmed_outcome_line(tmp_path):
    svc = _sidecar_svc(tmp_path, [{"kind": "tcp_send", "label": "callsign",
                                   "outcome": "acked", "attempts": 7, "elapsed_s": 214.3}])
    lines = svc._post_start_outcomes("meshcom-qemu")
    assert lines == ["post-start: callsign confirmed on attempt 7 after 214.3s"]
    # The line reaches BOTH the global and the SCOPED status (the operator lands on
    # `lhpc status meshcom` from the run's next_commands hint).
    assert any("callsign confirmed on attempt 7" in d for d in svc.status().details)
    assert any("callsign confirmed on attempt 7" in d
               for d in svc.status("meshcom").details)


def test_status_shows_not_applied_line_with_reapply_hint(tmp_path):
    svc = _sidecar_svc(tmp_path, [{"kind": "tcp_send", "label": "callsign",
                                   "outcome": "exhausted", "attempts": 36,
                                   "elapsed_s": 790.1}])
    lines = svc._post_start_outcomes("meshcom-qemu")
    assert len(lines) == 1
    assert "callsign NOT applied" in lines[0] and "console never became ready" in lines[0]
    assert "lhpc stack poststart meshcom" in lines[0]
    assert any("NOT applied" in d for d in svc.status("meshcom").details)


def test_status_outcome_unknown_on_unreadable_sidecar(tmp_path):
    svc = _sidecar_svc(tmp_path, [])
    # corrupt the sidecar -> fail-soft 'outcome unknown', never an exception into status
    (tmp_path / "state" / "post" / "u1.result.json").write_text("{not json")
    lines = svc._post_start_outcomes("meshcom-qemu")
    assert len(lines) == 1 and "outcome unknown" in lines[0]
    assert svc.status("meshcom").ok


def test_status_running_window_line_while_runner_alive(tmp_path):
    import os
    svc = _sidecar_svc(tmp_path, [], alive_pid=os.getpid())   # this pid is alive -> window active
    lines = svc._post_start_outcomes("meshcom-qemu")
    assert len(lines) == 1 and "retry window active" in lines[0]


def test_status_empty_steps_reports_nothing(tmp_path):
    # Render-time-skipped step (empty/N0CALL callsign) -> empty steps -> NO line, never a failure.
    svc = _sidecar_svc(tmp_path, [])
    assert svc._post_start_outcomes("meshcom-qemu") == []


def test_poststart_unknown_target_refused(tmp_path):
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    assert not svc.poststart("nope", apply=True).ok


def test_poststart_target_without_post_steps_refused(tmp_path):
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    res = svc.poststart("loraham-daemon", apply=True)
    assert not res.ok and "no post-start steps" in res.summary


def test_poststart_plan_lists_components(tmp_path):
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    res = svc.poststart("meshcom", apply=False)
    assert res.ok and any("meshcom-qemu" in d for d in res.details)


@pytest.mark.needs_session
def test_poststart_not_running_component_blocked(tmp_path):
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    set_call(svc)
    res = svc.poststart("meshcom", apply=True)
    assert not res.ok
    assert any(r.component == "meshcom-qemu" and r.outcome.value == "blocked"
               for r in res.results)
    assert any("start it first" in d for d in res.details)


@pytest.mark.needs_session
def test_poststart_running_component_cancels_then_reruns(tmp_path, monkeypatch):
    # A RUNNING component: poststart cancels any live runner FIRST (one-exchange console must
    # never see two senders), then re-runs via the SAME _run_post_start the start path uses.
    import types
    from lhpc.core.model import RunState
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    set_call(svc)
    snap = svc.build_snapshot()
    for ss in snap.stacks:
        if "meshcom-qemu" in ss.components:
            ss.components["meshcom-qemu"] = types.SimpleNamespace(run_state=RunState.RUNNING)
    monkeypatch.setattr(ControllerService, "build_snapshot", lambda self: snap)
    calls = {"cancel": 0, "rerun": []}
    class _LifeStub:
        def _cancel_post_runners(self, comp, band):
            calls["cancel"] += 1
            return (["post-runner pid 1: cancelled"], False)
    monkeypatch.setattr(ControllerService, "_lifecycle", lambda self: _LifeStub())
    def cap(self, life, stack, comp, comp_cfg, band, announce=None, strict=False):
        calls["rerun"].append(comp.id)
        assert strict is True             # the verb must surface a scheduling failure as FAILED
        return (None, "optional post-start scheduled")
    monkeypatch.setattr(ControllerService, "_run_post_start", cap)
    res = svc.poststart("meshcom", apply=True)
    assert res.ok
    assert calls["cancel"] == 1 and calls["rerun"] == ["meshcom-qemu"]
    # SCHEDULED, not applied: ok (the scheduling worked) but NOT verified until the sidecar says so.
    row = next(r for r in res.results if r.component == "meshcom-qemu")
    assert row.outcome.value == "started" and row.ok is True and row.verified is False


@pytest.mark.needs_session
def test_poststart_unverified_cancel_refuses_second_sender(tmp_path, monkeypatch):
    import types
    from lhpc.core.model import RunState
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    set_call(svc)
    snap = svc.build_snapshot()
    for ss in snap.stacks:
        if "meshcom-qemu" in ss.components:
            ss.components["meshcom-qemu"] = types.SimpleNamespace(run_state=RunState.RUNNING)
    monkeypatch.setattr(ControllerService, "build_snapshot", lambda self: snap)
    class _LifeStub:
        def _cancel_post_runners(self, comp, band):
            return (["post-runner pid 1: SIGTERM sent but NOT verified stopped"], True)
    monkeypatch.setattr(ControllerService, "_lifecycle", lambda self: _LifeStub())
    ran = []
    monkeypatch.setattr(ControllerService, "_run_post_start",
                        lambda self, *a, **k: ran.append(1) or (None, ""))
    res = svc.poststart("meshcom", apply=True)
    assert not res.ok and not ran                       # never a second concurrent sender
    assert any(r.outcome.value == "unverified" for r in res.results)


@pytest.mark.needs_session
def test_cancel_post_runners_removes_result_and_launcher_sidecars(tmp_path):
    from pathlib import Path as _P
    life = _real_life(tmp_path)
    comp, stack = _pr(_free_port())
    p = _record_main(life, comp, stack)
    try:
        sched = life.spawn_post_start(stack, comp)
        assert sched.ok and sched.log_path.endswith(".log")
        rec = life.owned_records("pr", role="post")[0]
        lp, rp = rec["launcher_path"], rec["result_path"]
        assert _P(lp).exists()                          # launcher written for the spawn
        notes, unverified = life._cancel_post_runners(comp, None)
        assert not unverified
        assert not _P(lp).exists() and not _P(rp).exists()   # sidecars die with the record
    finally:
        p.terminate(); p.wait()


# --- Item B: post-start log discoverability ([log] tail lines) --------------------------------

def test_required_post_start_announces_log(tmp_path, monkeypatch):
    # The synchronous required path forwards on_log_open into run_job -> the post log is
    # announced the moment it exists (before the possibly-long runner executes).
    life = _bound_life(tmp_path, monkeypatch)
    seen = []
    jr = life.run_required_post_start(STK, _req_comp(),
                                      on_log_open=lambda n, p: seen.append((n, p)))
    assert jr.ok
    assert len(seen) == 1
    name, path = seen[0]
    assert name.startswith("post-c-") and path.endswith(f"{name}.log")


def test_run_post_start_announces_detached_runner_log(tmp_path):
    # The detached optional path: _run_post_start announces the schedule's log_path through
    # the same per-component announcer the start details use.
    from lhpc.core.lifecycle import PostStartSchedule
    from lhpc.core.probes.backends import FakeSystem
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    class _L:
        def has_required_post_start(self, comp):
            return False
        def spawn_post_start(self, stack, comp, cfg, band=""):
            return PostStartSchedule(True, "scheduled", log_path="/rt/logs/post-c-1-2.log")
    details = []
    ok, summary = svc._run_post_start(_L(), STK, _opt_comp(), {}, "",
                                      announce=svc._log_announcer("c", details))
    assert ok is None and "scheduled" in summary
    assert details == ["  [log] c -> tail -f /rt/logs/post-c-1-2.log"]


@pytest.mark.needs_session
def test_start_details_announce_start_capture_log(tmp_path, monkeypatch):
    # Item B: the start flow announces the start capture log as a copy-pasteable tail line.
    svc = _igate_svc(tmp_path)
    set_call(svc)
    monkeypatch.setattr(ControllerService, "_lifecycle", _fake_life_factory)
    res = svc.start("igate", apply=True)
    assert any("[log] loraham-igate -> tail -f" in d and "start-loraham-igate" in d
               for d in res.details), res.details


# --- P1b: the poststart verb never claims more than it can prove ------------------------------

def _running_meshcom_svc(tmp_path, monkeypatch):
    """A service whose meshcom-qemu reads RUNNING, with the lifecycle stubbed out."""
    import types
    from lhpc.core.model import RunState
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    set_call(svc)
    snap = svc.build_snapshot()
    for ss in snap.stacks:
        if "meshcom-qemu" in ss.components:
            ss.components["meshcom-qemu"] = types.SimpleNamespace(run_state=RunState.RUNNING)
    monkeypatch.setattr(ControllerService, "build_snapshot", lambda self: snap)
    class _LifeStub:
        def _cancel_post_runners(self, comp, band):
            return ([], False)
    monkeypatch.setattr(ControllerService, "_lifecycle", lambda self: _LifeStub())
    return svc


@pytest.mark.needs_session
def test_poststart_scheduling_failure_is_a_failed_result(tmp_path, monkeypatch):
    # A runner that could NOT be scheduled is a FAILURE of the verb (whose only job is this
    # work) — never a VERIFIED row. The transport detail must reach the operator.
    svc = _running_meshcom_svc(tmp_path, monkeypatch)
    monkeypatch.setattr(
        ControllerService, "_run_post_start",
        lambda self, life, stack, comp, cfg, band, announce=None, strict=False:
        (False if strict else None,
         "optional post-start could NOT be scheduled: launcher write failed: disk full"))
    res = svc.poststart("meshcom", apply=True)
    assert not res.ok
    assert any(r.component == "meshcom-qemu" and r.outcome.value == "failed"
               for r in res.results)
    assert any("could NOT be scheduled" in d and "disk full" in d for d in res.details)
    assert "did not fully apply" in res.summary


@pytest.mark.needs_session
def test_poststart_success_says_scheduled_not_applied(tmp_path, monkeypatch):
    # The sidecar is the only source of "applied": a detached re-run has only been SCHEDULED,
    # so the verb points at where the real outcome will show up.
    svc = _running_meshcom_svc(tmp_path, monkeypatch)
    monkeypatch.setattr(
        ControllerService, "_run_post_start",
        lambda self, life, stack, comp, cfg, band, announce=None, strict=False:
        (None, "optional post-start scheduled"))
    res = svc.poststart("meshcom", apply=True)
    assert res.ok
    blob = res.summary + " " + " ".join(res.details)
    assert "re-scheduled" in blob and "lhpc status meshcom" in blob
    assert "applied" not in res.summary                     # never claims the callsign landed


@pytest.mark.needs_session
def test_poststart_required_steps_report_completion(tmp_path, monkeypatch):
    # A REQUIRED step runs synchronously, so its completion IS proven — keep that wording.
    svc = _running_meshcom_svc(tmp_path, monkeypatch)
    monkeypatch.setattr(
        ControllerService, "_run_post_start",
        lambda self, life, stack, comp, cfg, band, announce=None, strict=False:
        (True, "required post-start completed"))
    res = svc.poststart("meshcom", apply=True)
    assert res.ok
    assert any("required post-start completed" in d for d in res.details)


def test_run_post_start_strict_flag_only_changes_the_optional_failure(tmp_path, monkeypatch):
    # The START path must stay NON-gating on an optional scheduling failure (strict=False),
    # while the verb sees the same failure as False.
    from lhpc.core.lifecycle import PostStartSchedule
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    class _L:
        def has_required_post_start(self, comp):
            return False
        def spawn_post_start(self, stack, comp, cfg, band=""):
            return PostStartSchedule(False, "launcher write failed: disk full")
    lax, s1 = svc._run_post_start(_L(), STK, _opt_comp(), {}, "")
    strict, s2 = svc._run_post_start(_L(), STK, _opt_comp(), {}, "", strict=True)
    assert lax is None and strict is False
    assert s1 == s2 and "could NOT be scheduled" in s1


# --- P2a: the result sidecar is written to the runtime-file standard --------------------------

def test_result_tmp_is_exclusive_nofollow_and_mode_0600(tmp_path):
    # The generated runner must not clobber-open its temp leaf, and it must publish
    # DESCRIPTOR-RELATIVE: os.rename with src/dst dir-fds (os.rename is the call listed in
    # os.supports_dir_fd), never os.replace on a re-resolved absolute path.
    import json
    import os as _os
    import stat as _stat
    root, rp = _sidecar_root(tmp_path)
    script = _render_steps([{"kind": "tcp_send", "port": _free_port(), "data": "x\n",
                             "optional": True, "repeat": 1}], result_path=str(rp),
                           runtime=str(root))
    for frag in ("O_EXCL", "O_NOFOLLOW", "O_DIRECTORY", "0o600", "os.fsync",
                 "os.rename", "dir_fd"):
        assert frag in script, frag
    assert 'open(tmp, "w")' not in script                 # never the clobbering builtin open
    assert "os.replace(" not in script                    # absolute-path publish is gone
    assert _run_launcher([{"kind": "tcp_send", "port": _free_port(), "data": "x\n",
                           "optional": True, "repeat": 1}], result_path=str(rp),
                         runtime=str(root)) == 0
    assert json.loads(rp.read_text())["steps"][0]["outcome"] == "exhausted"
    assert _stat.S_IMODE(_os.stat(rp).st_mode) == 0o600
    assert not list(rp.parent.glob(".r.json.tmp-*"))      # no temp survives a publish


def test_result_write_survives_a_pre_planted_stale_tmp(tmp_path):
    # A killed earlier runner can leave a temp leaf behind. The random nonce ALONE is what makes
    # the next run publish safely — nothing is pre-unlinked, so a planted leaf (a name the runner
    # never chose and must not trust) is left exactly as it was.
    import json
    root, rp = _sidecar_root(tmp_path)
    planted = rp.parent / ".r.json.tmp-99999-deadbeefdeadbeef"
    planted.write_text("{ garbage")
    assert _run_launcher([{"kind": "tcp_send", "port": _free_port(), "data": "x\n",
                           "optional": True, "repeat": 1}], result_path=str(rp),
                         runtime=str(root)) == 0
    assert json.loads(rp.read_text())["steps"][0]["outcome"] == "exhausted"
    assert planted.read_text() == "{ garbage"      # never consumed, never pre-unlinked


def test_result_leaf_symlink_is_refused_not_replaced(tmp_path):
    # A symlinked PUBLISHED leaf is now REFUSED outright (lstat via dir_fd), where the old
    # absolute os.replace merely swapped the link. The victim is untouched either way, but the
    # link must survive: we never publish through it.
    root, rp = _sidecar_root(tmp_path)
    victim = tmp_path / "victim"
    victim.write_text("KEEP")
    rp.symlink_to(victim)
    assert _run_launcher([{"kind": "tcp_send", "port": _free_port(), "data": "x\n",
                           "optional": True, "repeat": 1}], result_path=str(rp),
                         runtime=str(root)) == 0        # sidecar is best-effort: run still ok
    assert victim.read_text() == "KEEP"
    assert rp.is_symlink()                              # refused, not replaced


# --- F1: exec/tcp_wait typed records + the required-run sidecar -------------------------------

def test_exec_records_ok_failed_and_missing_binary(tmp_path):
    # Every effectful exec records a bounded typed result — a failed one can never leave a
    # "successful empty sidecar" behind.
    import json
    root, rp = _sidecar_root(tmp_path)
    steps = [{"kind": "exec", "argv": ["true"], "optional": True, "label": "fine"},
             {"kind": "exec", "argv": ["false"], "optional": True, "label": "boom"},
             {"kind": "exec", "argv": ["/nonexistent/meshtastic"], "optional": True,
              "label": "absent"}]
    assert _run_launcher(steps, result_path=str(rp), runtime=str(root)) == 0
    got = {s["label"]: s for s in json.loads(rp.read_text())["steps"]}
    assert got["fine"]["outcome"] == "ok" and got["fine"]["rc"] == 0
    assert got["boom"]["outcome"] == "failed" and got["boom"]["rc"] == 1
    assert got["absent"]["outcome"] == "failed" and got["absent"]["rc"] is None
    assert all(isinstance(s["elapsed_s"], (int, float)) for s in got.values())


def test_required_exec_failure_flushes_the_sidecar_before_exiting(tmp_path):
    # The launcher exits non-zero AND the failure is already durable — the whole point of
    # flushing on every append.
    import json
    root, rp = _sidecar_root(tmp_path)
    assert _run_launcher([{"kind": "exec", "argv": ["false"], "required": True,
                           "label": "region"}], result_path=str(rp), runtime=str(root)) != 0
    data = json.loads(rp.read_text())
    assert data["steps"][0]["outcome"] == "failed" and data["steps"][0]["label"] == "region"
    assert data["done"] is False              # the runner did not reach its normal end


def test_exec_label_defaults_to_basename_not_the_resolved_path(tmp_path):
    # `paths` resolves argv[0] to an absolute managed binary; the STATUS label must stay the
    # original basename so an operator reads "true", never a venv location.
    root, rp = _sidecar_root(tmp_path)
    real = tmp_path / "bin" / "true"
    real.parent.mkdir(parents=True)
    real.write_text("#!/bin/sh\nexit 0\n")
    real.chmod(0o755)
    script = _render_steps([{"kind": "exec", "argv": ["true"], "optional": True,
                             "paths": [str(real)]}], result_path=str(rp), runtime=str(root))
    assert repr(str(real)) in script                     # resolved to the absolute path ...
    assert "'label': 'true'" in script                   # ... but labelled by basename


def test_exec_detail_is_bounded_and_traceback_free(tmp_path):
    # A child's stderr reaches the LOG verbatim but the sidecar keeps only a short sanitised
    # excerpt — and the status view never renders it at all.
    import json
    import sys as _sys
    root, rp = _sidecar_root(tmp_path)
    noisy = ("import sys;"
             "sys.stderr.write('Traceback (most recent call last):\\n');"
             "sys.stderr.write('  File \"/secret/path.py\", line 3, in f\\n');"
             "sys.stderr.write('boom ' * 500 + '\\n');"
             "sys.exit(3)")
    assert _run_launcher([{"kind": "exec", "argv": [_sys.executable, "-c", noisy],
                           "optional": True, "label": "noisy"}],
                         result_path=str(rp), runtime=str(root)) == 0
    st = json.loads(rp.read_text())["steps"][0]
    assert st["outcome"] == "failed" and st["rc"] == 3
    assert len(st["detail"]) <= 200
    assert "Traceback" not in st["detail"] and "/secret/path.py" not in st["detail"]
    assert '"' not in st["detail"] and "`" not in st["detail"]


def test_tcp_wait_records_ready_and_timeout(tmp_path):
    import json
    import socket as _socket
    root, rp = _sidecar_root(tmp_path)
    srv = _socket.socket(); srv.bind(("127.0.0.1", 0)); srv.listen(1)
    port = srv.getsockname()[1]
    try:
        assert _run_launcher([{"kind": "tcp_wait", "port": port, "timeout": 5,
                               "optional": True, "label": "api"}],
                             result_path=str(rp), runtime=str(root)) == 0
        st = json.loads(rp.read_text())["steps"][0]
        assert st["outcome"] == "ready" and st["attempts"] >= 1
    finally:
        srv.close()
    root2, rp2 = _sidecar_root(tmp_path / "b")
    assert _run_launcher([{"kind": "tcp_wait", "port": _free_port(), "timeout": 1,
                           "optional": True, "label": "api"}],
                         result_path=str(rp2), runtime=str(root2)) == 0
    assert json.loads(rp2.read_text())["steps"][0]["outcome"] == "timeout"


def test_delay_only_set_records_nothing(tmp_path):
    import json
    root, rp = _sidecar_root(tmp_path)
    assert _run_launcher([{"kind": "delay", "seconds": 0}],
                         result_path=str(rp), runtime=str(root)) == 0
    data = json.loads(rp.read_text())
    assert data["steps"] == [] and data["done"] is True


@pytest.mark.needs_session
def test_required_post_start_refuses_without_a_verified_main_binding(tmp_path):
    # The synchronous runner used to get BINDING=None, making its _main_ok() a no-op — it would
    # push settings at a main that had died or been replaced. It now refuses, typed.
    from lhpc.core.jobs import JobState
    life = _real_life(tmp_path)
    jr = life.run_required_post_start(STK, _req_comp())
    assert jr.state == JobState.FAILED and not jr.ok
    assert any("no verified main launch" in t for t in jr.tail)


def test_required_sidecar_leaf_is_unique_per_launch():
    from lhpc.core.lifecycle import Lifecycle
    a = Lifecycle.required_result_leaf({"main_launch_id": "comp__x__1234"})
    b = Lifecycle.required_result_leaf({"main_launch_id": "comp__x__5678"})
    assert a != b                                        # a new launch writes a NEW leaf
    assert a.startswith("required-") and a.endswith(".result.json")
    assert "/" not in a and ".." not in a                # hashed: never shaped by the id


# --- F5: a scheduled-but-incomplete re-run is STARTED, never VERIFIED -------------------------

@pytest.mark.needs_session
def test_poststart_detached_scheduling_is_started_not_verified(tmp_path, monkeypatch):
    from lhpc.core.outcomes import Outcome
    svc = _running_meshcom_svc(tmp_path, monkeypatch)
    monkeypatch.setattr(
        ControllerService, "_run_post_start",
        lambda self, life, stack, comp, cfg, band, announce=None, strict=False:
        (None, "optional post-start scheduled"))
    res = svc.poststart("meshcom", apply=True)
    row = next(r for r in res.results if r.component == "meshcom-qemu")
    assert res.ok is True                      # the action did its job: it scheduled the work
    assert row.outcome is Outcome.STARTED
    assert row.ok is True and row.verified is False   # not proven applied until the sidecar lands
    assert "re-scheduled" in " ".join(res.details) + res.summary


@pytest.mark.needs_session
def test_poststart_synchronous_required_run_stays_verified(tmp_path, monkeypatch):
    # A REQUIRED step runs synchronously, so its completion IS proven — it must stay VERIFIED
    # (the STARTED change must not over-broaden).
    from lhpc.core.outcomes import Outcome
    svc = _running_meshcom_svc(tmp_path, monkeypatch)
    monkeypatch.setattr(
        ControllerService, "_run_post_start",
        lambda self, life, stack, comp, cfg, band, announce=None, strict=False:
        (True, "required post-start completed"))
    res = svc.poststart("meshcom", apply=True)
    row = next(r for r in res.results if r.component == "meshcom-qemu")
    assert row.outcome is Outcome.VERIFIED and row.verified is True


# --- P2-2: the required post-start sidecar is looked up on the RUNNING BAND -------------------

def _band_svc(tmp_path):
    """A service whose lifecycle spawns real processes, so ownership records are genuinely
    verifiable (`_binding_for` demands a live, identity-matching main)."""
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    set_call(svc)
    return svc


def _record_main_on_band(life, stack, comp, band):
    """Record a REAL running main for comp on `band`, exactly as a start would."""
    import subprocess
    p = subprocess.Popen(["sleep", "60"], start_new_session=True)
    for _ in range(50):
        idn = life._capture_identity(p.pid)
        if idn and idn.get("exec") == "sleep":
            break
        time.sleep(0.05)
    assert life.record_launch(stack, comp, p.pid, band,
                             ident=life._capture_identity(p.pid), role="")
    return p


def _write_required_sidecar(life, binding, *, comp_id, band, outcome="ok", label="region"):
    """The sidecar the SYNCHRONOUS required run writes: leaf derived from the launch binding."""
    import json as _json
    from lhpc.core import runtime_fs as _rfs
    path = life.paths.under("state", "post", life.required_result_leaf(binding))
    _rfs.ensure_dir(life.paths, path.parent)
    path.write_text(_json.dumps({
        "v": 1, "meta": {"comp": comp_id, "band": band, "role": "required"},
        "binding": binding,
        "steps": [{"kind": "exec", "label": label, "outcome": outcome, "rc": 0,
                   "attempts": 1, "elapsed_s": 1.0}],
        "done": True}))
    return path


def _mesh(svc):
    stack = next(s for s in svc.stacks() if s.id == "meshtastic")
    return stack, next(c for c in stack.components if c.id == "meshtastic")


@pytest.mark.needs_session
@pytest.mark.parametrize("band", ["433", "868"])
def test_required_outcome_is_rendered_for_the_running_band(tmp_path, band):
    # REGRESSION: the lookup used to be band-less, so it could never match meshtastic's real
    # band-scoped ownership record and the required region outcome silently never rendered.
    svc = _band_svc(tmp_path)
    life = svc._lifecycle()
    stack, comp = _mesh(svc)
    p = _record_main_on_band(life, stack, comp, band)
    try:
        svc._set_running_band(stack.id, band)
        binding = life._binding_for(comp.id, band)
        assert binding is not None, "the band-scoped main must be verifiable"
        _write_required_sidecar(life, binding, comp_id=comp.id, band=band)
        lines = svc._required_post_outcomes(comp.id)
        assert any("region applied" in ln for ln in lines), lines
        assert any("region applied" in ln for ln in svc._post_start_outcomes(comp.id))
    finally:
        p.terminate(); p.wait()


@pytest.mark.needs_session
def test_required_outcome_of_another_band_is_not_rendered(tmp_path):
    # The stack is running on 868; a sidecar left by a 433 launch must never be displayed.
    svc = _band_svc(tmp_path)
    life = svc._lifecycle()
    stack, comp = _mesh(svc)
    p = _record_main_on_band(life, stack, comp, "868")
    try:
        svc._set_running_band(stack.id, "868")
        binding = life._binding_for(comp.id, "868")
        assert binding is not None
        # same launch, but the file claims it belongs to the OTHER band
        _write_required_sidecar(life, binding, comp_id=comp.id, band="433")
        assert svc._required_post_outcomes(comp.id) == []
    finally:
        p.terminate(); p.wait()


@pytest.mark.needs_session
def test_required_outcome_from_a_stale_launch_is_not_rendered(tmp_path):
    svc = _band_svc(tmp_path)
    life = svc._lifecycle()
    stack, comp = _mesh(svc)
    p = _record_main_on_band(life, stack, comp, "433")
    try:
        svc._set_running_band(stack.id, "433")
        binding = dict(life._binding_for(comp.id, "433"))
        import json as _json
        from lhpc.core import runtime_fs as _rfs
        stale = dict(binding, main_pid=binding["main_pid"] + 100000,
                     main_launch_id=binding["main_launch_id"] + "-old")
        # Placed at the CURRENT leaf (so it IS found) but recording a PREVIOUS launch's binding:
        # the binding comparison, not the filename, is what must reject it.
        path = life.paths.under("state", "post", life.required_result_leaf(binding))
        _rfs.ensure_dir(life.paths, path.parent)
        path.write_text(_json.dumps({
            "v": 1, "meta": {"comp": comp.id, "band": "433", "role": "required"},
            "binding": stale,
            "steps": [{"kind": "exec", "label": "region", "outcome": "ok", "rc": 0}],
            "done": True}))
        assert svc._required_post_outcomes(comp.id) == []
    finally:
        p.terminate(); p.wait()


@pytest.mark.needs_session
@pytest.mark.parametrize("meta", [
    {"comp": "meshtastic", "band": "433"},                       # no role
    {"comp": "other-comp", "band": "433", "role": "required"},   # another component
    {"role": "required"},                                        # missing comp/band
    "not-a-dict",
])
def test_required_outcome_with_malformed_meta_is_not_rendered(tmp_path, meta):
    import json as _json
    from lhpc.core import runtime_fs as _rfs
    svc = _band_svc(tmp_path)
    life = svc._lifecycle()
    stack, comp = _mesh(svc)
    p = _record_main_on_band(life, stack, comp, "433")
    try:
        svc._set_running_band(stack.id, "433")
        binding = life._binding_for(comp.id, "433")
        path = life.paths.under("state", "post", life.required_result_leaf(binding))
        _rfs.ensure_dir(life.paths, path.parent)
        path.write_text(_json.dumps({"v": 1, "meta": meta, "binding": binding,
                                     "steps": [{"kind": "exec", "label": "region",
                                                "outcome": "ok", "rc": 0}], "done": True}))
        assert svc._required_post_outcomes(comp.id) == []
    finally:
        p.terminate(); p.wait()


@pytest.mark.needs_session
def test_required_outcome_unchanged_for_a_bandless_stack(tmp_path):
    # A single-band/bandless stack has no band marker: the lookup must still resolve with "".
    svc = _band_svc(tmp_path)
    life = svc._lifecycle()
    stack = next(s for s in svc.stacks() if s.id == "meshcom")
    comp = next(c for c in stack.components if c.id == "meshcom-qemu")
    assert svc.stack_bands(stack.id) == ()          # genuinely bandless
    p = _record_main_on_band(life, stack, comp, "")
    try:
        binding = life._binding_for(comp.id, "")
        assert binding is not None
        _write_required_sidecar(life, binding, comp_id=comp.id, band="", label="callsign")
        assert any("callsign applied" in ln for ln in svc._required_post_outcomes(comp.id))
    finally:
        p.terminate(); p.wait()
