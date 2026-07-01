"""Tests for dependency run-order and the daemon auto-start/reconfigure logic."""

from __future__ import annotations

from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService


def _svc(tmp_path):
    return ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))


def test_run_order_puts_daemon_before_app(tmp_path):
    order = _svc(tmp_path)._run_order("kiss")
    ids = [c.id for _, c in order]
    assert ids == ["loraham-daemon", "loraham-kiss-tnc"]


def test_daemon_needs_band_and_tx_from_app(tmp_path):
    svc = _svc(tmp_path)
    radio, tx = svc._daemon_needs(svc._run_order("kiss"), None)
    assert radio == "433" and tx == "MANAGED"
    radio, tx = svc._daemon_needs(svc._run_order("meshcom-bridge"), None)
    assert radio == "433" and tx == "MANAGED"         # bridge requires MANAGED
    # CADIDLE is no longer forced via requires_daemon_cadidle — it is a configurable per-stack
    # daemon param (default 28 for meshcom) applied at start:
    assert svc._daemon_param_applies("meshcom", "433").get("CADIDLE") == "28"


def test_daemon_stack_uses_radio_param_override(tmp_path):
    svc = _svc(tmp_path)
    order = svc._run_order("daemon")
    assert [c.id for _, c in order] == ["loraham-daemon"]
    radio, tx = svc._daemon_needs(order, {"radio": "868"})
    assert radio == "868"                              # explicit override honoured


def test_optional_component_soft_unless_autostart(tmp_path):
    from lhpc.core.config import save_stack_config
    svc = _svc(tmp_path)
    gps = "meshcom-gps-relay"
    assert gps not in [c.id for _, c in svc._run_order("meshcom")]   # soft by default
    save_stack_config(svc._paths, "meshcom", {f"autostart_{gps}": "on"})
    assert gps in [c.id for _, c in svc._run_order("meshcom")]       # opted in
    assert [c.id for _, c in svc._run_order(gps)] == [gps]           # explicit run allowed


def test_radio_overview_maps_stacks_to_bands(tmp_path):
    radios = _svc(tmp_path).radio_overview()
    by_band = {r["band"]: r for r in radios}
    assert set(by_band) == {"433", "868"}
    s433 = {s["id"] for s in by_band["433"]["startable"]}
    s868 = {s["id"] for s in by_band["868"]["startable"]}
    assert {"igate", "kiss", "meshcom"} <= s433
    assert {"meshtastic", "meshcore"} <= s868
    assert "daemon" not in s433 and "daemon" not in s868   # daemon is the radio itself
    # interactive stacks (chat/voice) sit in the dropdown until "run", then become
    # a dismissable command block — none are active here, so no interactive blocks.
    assert {"chat", "voice"} <= s433
    assert by_band["433"]["interactive"] == [] and by_band["868"]["interactive"] == []


def test_interactive_run_shows_block_then_dismiss(tmp_path):
    svc = _svc(tmp_path)
    svc.mark_interactive("chat", "433")                  # chat is 433-only
    ro = {r["band"]: r for r in svc.radio_overview()}
    block = [s["id"] for s in ro["433"]["interactive"]]
    assert "chat" in block                               # block in its band column
    assert "chat" not in {s["id"] for s in ro["433"]["startable"]}   # no longer in dropdown
    svc.dismiss_interactive("chat")
    ro = {r["band"]: r for r in svc.radio_overview()}
    assert "chat" in {s["id"] for s in ro["433"]["startable"]}       # back in dropdown


def test_client_interfaces_marked_and_transport_excluded(tmp_path):
    svc = _svc(tmp_path)
    # user-facing interfaces are flagged client=true
    kiss_tcp = [e for e in svc.stack("kiss").component("loraham-kiss-tnc").endpoints
                if e.address.endswith(":8001")][0]
    assert kiss_tcp.client and kiss_tcp.scheme == "kiss"
    web = [e for e in svc.stack("meshcom").component("meshcom-qemu").endpoints
           if e.address.endswith(":18083")][0]
    assert web.client and web.scheme == "http"
    # the daemon's CONF/data sockets are transport — never client-facing
    for e in svc.stack("daemon").component("loraham-daemon").endpoints:
        assert not e.client


def test_log_running_false_when_idle(tmp_path):
    svc = _svc(tmp_path)
    assert svc.log_running("daemon") is False           # nothing running
    assert svc.log_running("daemon", job="nope.log") is False


def test_run_blockers_empty_when_idle(tmp_path):
    # Nothing running -> no ownership conflicts.
    assert _svc(tmp_path).run_blockers("meshcore") == []


def test_stop_daemon_cascades_to_dependents(tmp_path):
    from lhpc.core.paths import Paths
    from lhpc.core.probes.backends import FakeSystem
    from lhpc.core.services import ControllerService
    fake = FakeSystem(cmdlines_data={4242: ["loraham_igate", "-c", "X"]})
    (tmp_path / "x").mkdir()
    svc = ControllerService(system=fake.system, paths=Paths(runtime_root=tmp_path))
    assert "igate" in svc.stop_dependents("daemon")          # igate depends on daemon
    assert "igate" in svc.stop("daemon", apply=False).data["dependents"]  # plan surfaces it


def test_blockers_only_real_band_conflicts(tmp_path):
    from lhpc.core.paths import Paths
    from lhpc.core.probes.backends import FakeSystem
    from lhpc.core.services import ControllerService
    reply = b"STATUS RADIO=READY TX=0 TXMODE=MANAGED CADWAIT=1500 CADRSSI=-90\n"

    def make(sub, cmdlines=None, socks=None):
        p = tmp_path / sub
        p.mkdir()
        fake = FakeSystem(cmdlines_data=cmdlines or {}, unix_replies=socks or {})
        return ControllerService(system=fake.system, paths=Paths(runtime_root=p))

    # daemon serving 433 must NOT block meshcom (a 433 daemon-backed stack uses it)
    s = make("a", {100: ["loraham_daemon"]}, {"/tmp/loraconf433.sock": reply})
    assert s.run_blockers("meshcom") == []

    # meshtastic running on 868 must NOT block a 433 stack, but MUST block 868 ones
    s = make("b", {200: ["meshtasticd"]})
    s._set_running_band("meshtastic", "868")
    assert s.run_blockers("meshcom") == []                       # 433 unaffected
    assert any("868" in b["resource"] for b in s.run_blockers("meshcore"))  # real 868 conflict


def test_same_frequency_blocks_second_stack(tmp_path):
    from lhpc.core.paths import Paths
    from lhpc.core.probes.backends import FakeSystem
    from lhpc.core.services import ControllerService
    # iGate (433) is running; starting another 433 stack must be blocked by it.
    fake = FakeSystem(cmdlines_data={4242: ["loraham_igate", "-c", "X"]})
    (tmp_path / "x").mkdir()
    svc = ControllerService(system=fake.system, paths=Paths(runtime_root=tmp_path))
    bl = svc.run_blockers("kiss")
    assert any(b["holder_stack"] == "igate" and "433" in b["resource"] for b in bl)
    # An 868 stack is unaffected by a 433 occupant.
    assert svc.run_blockers("meshcore") == []


def test_active_jobs_tracks_live_and_prunes_dead(tmp_path):
    import os
    svc = _svc(tmp_path)
    svc._write_job_marker("test-a.log", os.getpid(), "loraham-daemon", "test")  # alive
    svc._write_job_marker("test-b.log", 2147480000, "loraham-daemon", "build")  # dead pid
    jobs = svc.active_jobs()
    names = {j["log"] for j in jobs}
    assert "test-a.log" in names and "test-b.log" not in names   # dead pruned
    assert any(j["stack"] == "daemon" for j in jobs)


def test_run_plan_lists_daemon_then_app(tmp_path):
    (tmp_path / "x").mkdir()  # runtime root exists
    res = _svc(tmp_path).start("kiss", apply=False)
    assert res.ok and "[daemon]" in "\n".join(res.details)
    assert any("loraham-kiss-tnc" in d for d in res.details)


def test_start_log_omits_unconfirmed_boilerplate(tmp_path):
    # Radio params aren't echoed by the daemon; the start log stays concise (no verbose
    # "SENT but UNCONFIRMED — a radio param the daemon does not report back …").
    reply = b"STATUS RADIO=READY TXMODE=MANAGED CADWAIT=1500 CADIDLE=250\n"
    fake = FakeSystem(unix_replies={"/tmp/loraconf433.sock": reply})
    (tmp_path / "x").mkdir()
    svc = ControllerService(system=fake.system, paths=Paths(runtime_root=tmp_path))
    text = "\n".join(svc.start("meshcom", apply=True).details)
    assert "UNCONFIRMED" not in text and "does not report back" not in text
    assert "SF=10 sent" in text                          # concise radio-param line instead


def test_cli_start_same_sequence_as_web(tmp_path):
    # CLI (`lhpc start` -> run_action "start") and web (op=start -> run_action "start") share the
    # SAME _start_impl, so both perform the identical sequence: ensure the daemon (READY) -> apply
    # this stack's radio params -> start the stack's own components.
    reply = b"STATUS RADIO=READY TXMODE=MANAGED CADWAIT=1500 CADIDLE=250\n"
    fake = FakeSystem(unix_replies={"/tmp/loraconf433.sock": reply})
    (tmp_path / "x").mkdir()
    svc = ControllerService(system=fake.system, paths=Paths(runtime_root=tmp_path))
    text = "\n".join(svc.run_action("start", "meshcom", apply=True).details)   # CLI entry point
    i_daemon = text.index("daemon already serving 433")     # 1) daemon ensured READY
    i_params = text.index("SF=10")                          # 2) radio params applied (before app)
    i_stack = text.index("meshcom-bridge")                 # 3) then the stack's own component
    assert i_daemon < i_params < i_stack                    # exact ordering
    assert "CADIDLE=28" in text                             # meshcom's configured timing applied


def test_app_start_brings_up_daemon_when_band_not_served(tmp_path):
    # No CONF socket -> daemon not serving 433 and not running -> it is started first.
    binp = tmp_path / "src" / "loraham-daemon" / "loraham_daemon" / "loraham_daemon"
    binp.parent.mkdir(parents=True)       # daemon source present
    binp.write_text("#!/bin/sh\n")        # and its binary is built
    res = _svc(tmp_path).start("kiss", apply=True)
    text = "\n".join(res.details)
    assert "start daemon --radio 433" in text


def test_app_start_uses_running_daemon_when_serving_band(tmp_path):
    # CONF socket reachable on 433 + TXMODE already MANAGED (what kiss needs) ->
    # daemon is already serving the band; do NOT (re)start it.
    from lhpc.core.paths import Paths
    from lhpc.core.probes.backends import FakeSystem
    from lhpc.core.services import ControllerService
    reply = b"STATUS RADIO=READY TX=0 TXMODE=MANAGED CADWAIT=1500 CADRSSI=-90\n"
    fake = FakeSystem(unix_replies={"/tmp/loraconf433.sock": reply})
    (tmp_path / "x").mkdir()
    svc = ControllerService(system=fake.system, paths=Paths(runtime_root=tmp_path))
    res = svc.start("kiss", apply=True)
    text = "\n".join(res.details)
    assert "daemon already serving 433" in text
    assert "start daemon" not in text


# --- Area 2: partial dual-band daemon startup -------------------------------------------------

def _daemon_svc(tmp_path, replies):
    import os
    binp = tmp_path / "src" / "loraham-daemon" / "loraham_daemon" / "loraham_daemon"
    binp.parent.mkdir(parents=True)
    binp.write_text("#!/bin/sh\nsleep 0.1\n")
    os.chmod(binp, 0o755)
    return ControllerService(system=FakeSystem(unix_replies=replies).system,
                             paths=Paths(runtime_root=tmp_path))


def _band_starts(details):
    import re
    return set(re.findall(r"start daemon --radio (\w+)", "\n".join(details)))


_RDY = b"STATUS RADIO=READY TXMODE=MANAGED CADWAIT=1500 CADIDLE=250\n"
_UNINIT = b"STATUS RADIO=UNINITIALIZED\n"


def test_dual_band_both_absent_starts_radio_both(tmp_path):
    svc = _daemon_svc(tmp_path, {})
    res = svc.start("daemon", apply=True)
    assert _band_starts(res.details) == {"both"}          # one --radio both process, not two


def test_dual_band_433_ready_starts_only_868(tmp_path):
    svc = _daemon_svc(tmp_path, {"/tmp/loraconf433.sock": _RDY})
    text = "\n".join(svc.start("daemon", apply=True).details)
    assert _band_starts([text]) == {"868"}                # ONLY the missing band...
    assert "daemon already serving 433" in text           # ...433 retained, no --radio both
    assert "--radio both" not in text


def test_dual_band_868_ready_starts_only_433(tmp_path):
    svc = _daemon_svc(tmp_path, {"/tmp/loraconf868.sock": _RDY})
    text = "\n".join(svc.start("daemon", apply=True).details)
    assert _band_starts([text]) == {"433"}
    assert "daemon already serving 868" in text and "--radio both" not in text


def test_dual_band_reachable_not_ready_fails_without_relaunch(tmp_path):
    # 433 reachable but NOT READY -> fail that band, never relaunch a conflicting instance on it.
    svc = _daemon_svc(tmp_path, {"/tmp/loraconf433.sock": _UNINIT, "/tmp/loraconf868.sock": _RDY})
    res = svc.start("daemon", apply=True)
    text = "\n".join(res.details)
    assert not res.ok and "not READY" in text
    assert "start daemon --radio 433" not in text and "--radio both" not in text


def test_client_single_band_startup_unchanged(tmp_path):
    # A single-band client with its band already served applies once, no daemon relaunch.
    svc = ControllerService(system=FakeSystem(unix_replies={"/tmp/loraconf433.sock": _RDY}).system,
                            paths=Paths(runtime_root=tmp_path))
    (tmp_path / "x").mkdir()
    text = "\n".join(svc.start("kiss", apply=True).details)
    assert "daemon already serving 433" in text
    assert "start daemon" not in text                     # not relaunched
