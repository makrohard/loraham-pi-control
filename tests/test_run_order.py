"""Tests for dependency run-order and the daemon auto-start/reconfigure logic."""

from __future__ import annotations
import pytest

from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ActionResult, ControllerService
from conftest import set_call


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
    # Chat's box shows only while its daemon band is USABLE (see the daemon-down test below), so fake a
    # READY daemon on 433 for the marked interactive app to show its command block.
    reply = b"STATUS RADIO=READY TXMODE=MANAGED CADWAIT=1500 CADIDLE=250\n"
    fake = FakeSystem(unix_replies={"/tmp/loraconf433.sock": reply})
    svc = ControllerService(system=fake.system, paths=Paths(runtime_root=tmp_path))
    svc.mark_interactive("chat", "433")                  # chat is 433-only
    ro = {r["band"]: r for r in svc.radio_overview()}
    block = [s["id"] for s in ro["433"]["interactive"]]
    assert "chat" in block                               # block in its band column
    assert "chat" not in {s["id"] for s in ro["433"]["startable"]}   # no longer in dropdown
    svc.dismiss_interactive("chat")
    ro = {r["band"]: r for r in svc.radio_overview()}
    assert "chat" in {s["id"] for s in ro["433"]["startable"]}       # back in dropdown


def test_interactive_box_closes_when_daemon_not_usable(tmp_path):
    # BUG FIX: chat "started" (marker set) but not actually running; with NO usable daemon on its band
    # the dashboard box must CLOSE — chat returns to the startable dropdown, not the interactive column.
    # (Reproduces "start chat, then stop the daemon" — the daemon is no longer usable on 433.)
    svc = _svc(tmp_path)                                  # no daemon reachable/usable
    svc.mark_interactive("chat", "433")
    ro = {r["band"]: r for r in svc.radio_overview()}
    assert ro["433"]["interactive"] == []                                # box closed
    assert "chat" in {s["id"] for s in ro["433"]["startable"]}           # back in the dropdown


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


@pytest.mark.needs_session
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
    set_call(svc)
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
    set_call(svc)
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


@pytest.mark.needs_session
def test_dual_band_both_absent_starts_per_band(tmp_path):
    svc = _daemon_svc(tmp_path, {})
    res = svc.start("daemon", apply=True)
    # lhpc NEVER launches --radio both: one --radio <band> instance per band.
    assert _band_starts(res.details) == {"433", "868"}


@pytest.mark.needs_session
def test_dual_band_433_ready_starts_only_868(tmp_path):
    svc = _daemon_svc(tmp_path, {"/tmp/loraconf433.sock": _RDY})
    text = "\n".join(svc.start("daemon", apply=True).details)
    assert _band_starts([text]) == {"868"}                # ONLY the missing band...
    assert "daemon already serving 433" in text           # ...433 retained, no --radio both
    assert "--radio both" not in text


@pytest.mark.needs_session
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


# --- M5: stop cascade both directions -------------------------------------------------------

def test_daemon_stop_forces_cascade_to_dependents(tmp_path, monkeypatch):
    # Stopping the daemon ALWAYS stops its dependents, even without cascade requested.
    svc = _svc(tmp_path)
    monkeypatch.setattr(ControllerService, "stop_dependents", lambda self, t, bands=None: ["kiss"])
    res = svc.stop("daemon", apply=True)                 # cascade NOT passed
    assert any("dependent] kiss" in d for d in res.details)


def test_client_stop_releases_daemon_only_when_unused(tmp_path, monkeypatch):
    svc = _svc(tmp_path)
    voice = svc.stack("voice")
    # daemon reachable on 433
    class _V: reachable = True
    monkeypatch.setattr(ControllerService, "daemon_view", lambda self, b: _V())
    # no other running stack needs the daemon -> release the ACTUAL band it ran on (433)
    monkeypatch.setattr(ControllerService, "stop_dependents", lambda self, t, bands=None: [])
    dsid, rel = svc._daemon_bands_to_release(voice, "voice", {"433"})
    assert dsid == "daemon" and rel == ["433"]
    # another 433 stack still depends on the daemon -> do NOT release
    monkeypatch.setattr(ControllerService, "stop_dependents", lambda self, t, bands=None: ["igate"])
    _, rel2 = svc._daemon_bands_to_release(voice, "voice", {"433"})
    assert rel2 == []
    # stopping the daemon stack itself never "releases" a daemon
    assert svc._daemon_bands_to_release(svc.stack("daemon"), "daemon", {"433"})[1] == []


# --- M6: band-aware radio conflicts + non-disruptive daemon (re)start -----------------------

_RDY6 = b"STATUS RADIO=READY TXMODE=MANAGED\n"


def test_no_false_conflict_daemon_433_meshtastic_868(tmp_path):
    # daemon serving ONLY 433 (for a 433 client) + meshtastic on 868 must NOT conflict.
    svc = ControllerService(system=FakeSystem(
        cmdlines_data={100: ["loraham_daemon", "--radio", "433"], 200: ["loraham_chat"],
                       300: ["meshtasticd"]},
        unix_replies={"/tmp/loraconf433.sock": _RDY6}).system,
        paths=Paths(runtime_root=tmp_path))
    svc._set_running_band("meshtastic", "868")
    assert svc._observed_conflicts() == []


def test_real_conflict_daemon_both_vs_meshtastic_868(tmp_path):
    # daemon serving BOTH + meshtastic on 868 IS a real conflict — on 868 only.
    svc = ControllerService(system=FakeSystem(
        cmdlines_data={100: ["loraham_daemon", "--radio", "both"], 300: ["meshtasticd"]},
        unix_replies={"/tmp/loraconf433.sock": _RDY6, "/tmp/loraconf868.sock": _RDY6}).system,
        paths=Paths(runtime_root=tmp_path))
    svc._set_running_band("meshtastic", "868")
    msgs = [c.message for c in svc._observed_conflicts()]
    assert any("loraham.radio.868" in m for m in msgs)
    assert not any("loraham.radio.433" in m for m in msgs)


@pytest.mark.needs_session
def test_daemon_restart_does_not_reconfigure_band_in_use(tmp_path):
    # Starting the daemon in FSK must NOT re-apply params to a band already serving a running
    # stack (433 voice stays as-is); only freshly-started bands get this start's params.
    import os
    b = tmp_path / "src" / "loraham-daemon" / "loraham_daemon" / "loraham_daemon"
    b.parent.mkdir(parents=True); b.write_text("#!/bin/sh\nsleep .1\n"); os.chmod(b, 0o755)
    svc = ControllerService(system=FakeSystem(
        cmdlines_data={100: ["loraham_daemon", "--radio", "433"], 200: ["loraham_voice"]},
        unix_replies={"/tmp/loraconf433.sock": _RDY6}).system,
        paths=Paths(runtime_root=tmp_path))
    svc._set_running_band("voice", "433")
    res = svc.run_action("start", "daemon", apply=True,
                         daemon_overrides={"433": {"MODE": "FSK"}, "868": {"MODE": "FSK"}})
    text = "\n".join(res.details)
    assert "[keep] 433 in use by voice" in text          # 433 left untouched
    assert "daemon already serving 433" in text


# --- A1: daemon stop must not orphan dependents ---------------------------------------------

def test_daemon_stop_blocked_by_interactive_dependent(tmp_path, monkeypatch):
    svc = _svc(tmp_path)
    monkeypatch.setattr(ControllerService, "stop_dependents", lambda self, t, bands=None: ["chat"])
    res = svc.stop("daemon", apply=True)                     # chat's main is interactive
    assert not res.ok
    dres = [r for r in res.results if r.component == svc.DAEMON_ID]
    assert dres and dres[0].outcome.value == "blocked"       # daemon NOT stopped
    assert any(r.component == "chat" and r.outcome.value == "manual_required" for r in res.results)


def test_daemon_stop_blocked_by_failed_dependent(tmp_path, monkeypatch):
    svc = _svc(tmp_path)
    monkeypatch.setattr(ControllerService, "stop_dependents", lambda self, t, bands=None: ["kiss"])
    orig = ControllerService.stop
    def fake(self, target, apply=False, cascade=False, band="", release_daemon=True):
        if target == "kiss":
            return ActionResult(False, "kiss stop NOT verified")
        return orig(self, target, apply=apply, cascade=cascade, band=band)
    monkeypatch.setattr(ControllerService, "stop", fake)
    res = svc.stop("daemon", apply=True)
    assert not res.ok
    dres = [r for r in res.results if r.component == svc.DAEMON_ID]
    assert dres and dres[0].outcome.value == "blocked"


def test_daemon_stop_cascade_stops_dependents_before_daemon(tmp_path, monkeypatch):
    svc = _svc(tmp_path)
    monkeypatch.setattr(ControllerService, "stop_dependents", lambda self, t, bands=None: ["kiss"])
    res = svc.stop("daemon", apply=True)                     # kiss not running -> stops clean
    order = [r.component for r in res.results]
    assert order.index("kiss") < order.index(svc.DAEMON_ID)  # dependent recorded before daemon
    dres = [r for r in res.results if r.component == svc.DAEMON_ID]
    assert dres and dres[0].outcome.value != "blocked"       # daemon stop attempted


# --- A2: client stop releases the ACTUAL daemon band ----------------------------------------

def test_client_release_actual_band_868(tmp_path):
    svc = ControllerService(system=FakeSystem(
        unix_replies={"/tmp/loraconf868.sock": _RDY6}).system, paths=Paths(runtime_root=tmp_path))
    svc._set_running_band("voice", "868")                    # voice ran on 868
    res = svc.stop("voice", apply=True)
    rel = [r for r in res.results if r.component == svc.DAEMON_ID]
    assert rel and "868" in rel[0].summary and "433" not in rel[0].summary


def test_client_release_actual_band_433(tmp_path):
    svc = ControllerService(system=FakeSystem(
        unix_replies={"/tmp/loraconf433.sock": _RDY6}).system, paths=Paths(runtime_root=tmp_path))
    svc._set_running_band("kiss", "433")                     # kiss ran on 433
    res = svc.stop("kiss", apply=True)
    rel = [r for r in res.results if r.component == svc.DAEMON_ID]
    assert rel and "433" in rel[0].summary and "868" not in rel[0].summary


def test_daemon_release_failure_makes_client_stop_nonsuccess(tmp_path, monkeypatch):
    svc = ControllerService(system=FakeSystem(
        unix_replies={"/tmp/loraconf433.sock": _RDY6}).system, paths=Paths(runtime_root=tmp_path))
    svc._set_running_band("voice", "433")
    orig = ControllerService.stop
    def fake(self, target, apply=False, cascade=False, band="", release_daemon=True):
        if target == "daemon":
            return ActionResult(False, "daemon release NOT verified")
        return orig(self, target, apply=apply, cascade=cascade, band=band)
    monkeypatch.setattr(ControllerService, "stop", fake)
    res = svc.stop("voice", apply=True)
    assert not res.ok                                         # release failure -> whole stop fails
    rel = [r for r in res.results if r.component == svc.DAEMON_ID]
    assert rel and rel[0].outcome.value == "unverified"


# --- A3: band-aware daemon locks + blockers -------------------------------------------------

def _msvc(tmp_path, band):
    svc = ControllerService(system=FakeSystem(cmdlines_data={200: ["meshtasticd"]}).system,
                            paths=Paths(runtime_root=tmp_path))
    svc._set_running_band("meshtastic", band)
    return svc


def test_same_band_meshtastic_blocks_daemon_start(tmp_path):
    svc = _msvc(tmp_path, "868")
    assert any("868" in bl["resource"] for bl in svc.run_blockers("daemon", radio="868"))


def test_opposite_band_meshtastic_permits_daemon_start(tmp_path):
    svc = _msvc(tmp_path, "868")
    assert svc.run_blockers("daemon", radio="433") == []


def test_radio_both_blocked_by_direct_owner_either_band(tmp_path):
    assert any("868" in bl["resource"] for bl in _msvc(tmp_path, "868").run_blockers("daemon", radio="both"))
    assert any("433" in bl["resource"] for bl in _msvc(tmp_path, "433").run_blockers("daemon", radio="both"))


def test_daemon_start_lock_keys_track_radio_mode(tmp_path):
    svc = _svc(tmp_path)
    r433 = [k for k in svc._operation_resource_keys("daemon", radio="433") if "radio" in k]
    rboth = [k for k in svc._operation_resource_keys("daemon", radio="both") if "radio" in k]
    assert r433 == ["loraham.radio.433"]
    assert rboth == ["loraham.radio.433", "loraham.radio.868"]


def test_daemon_perband_stop_locks_band_and_both_collateral(tmp_path):
    _RDY = b"STATUS RADIO=READY TXMODE=MANAGED\n"
    # separate per-band instances: stopping 433 locks only 433
    svc = ControllerService(system=FakeSystem(
        cmdlines_data={10: ["loraham_daemon", "--radio", "433"], 11: ["loraham_daemon", "--radio", "868"]},
        unix_replies={"/tmp/loraconf433.sock": _RDY, "/tmp/loraconf868.sock": _RDY}).system,
        paths=Paths(runtime_root=tmp_path))
    assert svc._operation_bands("daemon", band="433", op="stop") == {"433"}
    # ONE --radio both process serves both: stopping via 433 is collateral for 868 -> lock both
    svc2 = ControllerService(system=FakeSystem(
        cmdlines_data={20: ["loraham_daemon", "--radio", "both"]},
        unix_replies={"/tmp/loraconf433.sock": _RDY, "/tmp/loraconf868.sock": _RDY}).system,
        paths=Paths(runtime_root=tmp_path))
    assert svc2._operation_bands("daemon", band="433", op="stop") == {"433", "868"}


# --- A4: no-side-effect Start for already-healthy targets -----------------------------------

def _healthy_igate(tmp_path):
    _RDY = b"STATUS RADIO=READY TXMODE=MANAGED\n"
    return ControllerService(system=FakeSystem(
        cmdlines_data={100: ["loraham_daemon", "--radio", "433"], 200: ["loraham_igate", "-c", "X"]},
        unix_replies={"/tmp/loraconf433.sock": _RDY}).system, paths=Paths(runtime_root=tmp_path))


def test_healthy_client_start_sends_no_daemon_set(tmp_path):
    svc = _healthy_igate(tmp_path)
    set_call(svc)
    res = svc.start("igate", apply=True)
    assert res.ok and all(r.outcome.value == "already_healthy" for r in res.results)
    assert not any("sent" in d.lower() or "serving" in d for d in res.details)   # no CONF SET


def test_healthy_start_with_ephemeral_fsk_sends_no_set(tmp_path):
    svc = _healthy_igate(tmp_path)
    set_call(svc)
    res = svc.start("igate", apply=True, daemon_overrides={"433": {"MODE": "FSK"}})
    assert res.ok and all(r.outcome.value == "already_healthy" for r in res.results)
    assert not any("MODE" in d or "sent" in d.lower() for d in res.details)       # FSK not applied


def test_stopped_client_with_ready_daemon_applies_before_launch(tmp_path):
    _RDY = b"STATUS RADIO=READY TXMODE=MANAGED\n"
    svc = ControllerService(system=FakeSystem(       # daemon ready 433; igate NOT running
        cmdlines_data={100: ["loraham_daemon", "--radio", "433"]},
        unix_replies={"/tmp/loraconf433.sock": _RDY}).system, paths=Paths(runtime_root=tmp_path))
    set_call(svc)
    res = svc.start("igate", apply=True)
    text = "\n".join(res.details)
    assert "daemon already serving 433" in text and "sent" in text.lower()        # params applied
    igate_line = next((i for i, d in enumerate(res.details) if "loraham-igate" in d), None)
    apply_line = next((i for i, d in enumerate(res.details) if "sent" in d.lower()), None)
    assert apply_line is not None and igate_line is not None and apply_line < igate_line  # before launch


# --- P1: topology-based lifecycle band resolution -------------------------------------------

_RDYP1 = b"STATUS RADIO=READY TXMODE=MANAGED\n"


def _keys(svc, op, target, band="", radio=""):
    return svc._lifecycle_lock_keys(op, target, band=band, radio=radio)


def test_voice_868_stop_locks_only_868(tmp_path):
    svc = _svc(tmp_path); svc._set_running_band("voice", "868")
    keys = _keys(svc, "stop", "voice")
    assert "claim.loraham.radio.868" in keys and "claim.loraham.radio.433" not in keys


def test_kiss_868_restart_locks_868(tmp_path):
    svc = _svc(tmp_path); svc._set_running_band("kiss", "868")
    assert "claim.loraham.radio.868" in _keys(svc, "restart", "kiss", band="868")


def test_voice_433_restart_to_868_locks_both(tmp_path):
    svc = _svc(tmp_path); svc._set_running_band("voice", "433")
    keys = _keys(svc, "restart", "voice", band="868")
    assert "claim.loraham.radio.433" in keys and "claim.loraham.radio.868" in keys


def _daemon_both(tmp_path, socks):
    return ControllerService(system=FakeSystem(
        cmdlines_data={20: ["loraham_daemon", "--radio", "both"]}, unix_replies=socks).system,
        paths=Paths(runtime_root=tmp_path))


def test_daemon_both_restart_to_433_locks_both(tmp_path):
    svc = _daemon_both(tmp_path, {"/tmp/loraconf433.sock": _RDYP1, "/tmp/loraconf868.sock": _RDYP1})
    keys = _keys(svc, "restart", "daemon", radio="433")
    assert "claim.loraham.radio.433" in keys and "claim.loraham.radio.868" in keys


def test_daemon_both_perband_stop_locks_both_even_if_socket_down(tmp_path):
    svc = _daemon_both(tmp_path, {"/tmp/loraconf433.sock": _RDYP1})   # 868 socket unreachable
    assert svc._operation_bands("daemon", band="433", op="stop") == {"433", "868"}


def test_whole_daemon_stop_locks_process_served_band_socket_down(tmp_path):
    svc = _daemon_both(tmp_path, {"/tmp/loraconf433.sock": _RDYP1})   # 868 socket unreachable
    assert svc._operation_bands("daemon", op="stop") == {"433", "868"}


def _spy_stop(monkeypatch):
    calls = []
    orig = ControllerService.stop
    def spy(self, target, apply=False, cascade=False, band="", release_daemon=True):
        calls.append((target, band, release_daemon))
        return orig(self, target, apply=apply, cascade=cascade, band=band, release_daemon=release_daemon)
    monkeypatch.setattr(ControllerService, "stop", spy)
    return calls


def test_daemon_cascade_no_nested_daemon_release(tmp_path, monkeypatch):
    svc = _svc(tmp_path)
    monkeypatch.setattr(ControllerService, "stop_dependents", lambda self, t, bands=None: ["kiss"])
    calls = _spy_stop(monkeypatch)
    svc.stop("daemon", apply=True)
    assert ("kiss", "", False) in calls                       # dependent stopped WITHOUT release
    assert len([c for c in calls if c[0] == "daemon"]) == 1   # outer daemon stop, exactly once


def test_daemon_cascade_both_bands_no_inner_release(tmp_path, monkeypatch):
    svc = _svc(tmp_path)
    monkeypatch.setattr(ControllerService, "stop_dependents", lambda self, t, bands=None: ["kiss", "meshcore"])
    calls = _spy_stop(monkeypatch)
    svc.stop("daemon", apply=True)
    assert all(rd is False for (t, b, rd) in calls if t in ("kiss", "meshcore"))  # no inner release
    assert len([c for c in calls if c[0] == "daemon"]) == 1


def test_standalone_client_releases_daemon_once(tmp_path, monkeypatch):
    svc = ControllerService(system=FakeSystem(
        unix_replies={"/tmp/loraconf868.sock": _RDYP1}).system, paths=Paths(runtime_root=tmp_path))
    svc._set_running_band("voice", "868")
    calls = _spy_stop(monkeypatch)
    svc.stop("voice", apply=True)
    assert [c for c in calls if c[0] == "daemon"] == [("daemon", "868", True)]   # released once, on 868


def test_no_false_conflict_meshcom433_meshtastic868_without_marker(tmp_path):
    # Reported bug: MeshCom(433) + Meshtastic(868) showed false conflicts on BOTH bands when the
    # meshtastic running-band marker was absent (it kept both radio claims). It must limit to 868.
    svc = ControllerService(system=FakeSystem(
        cmdlines_data={100: ["loraham_daemon", "--radio", "433"], 200: ["meshtasticd"],
                       300: ["qemu-system-arm"]},
        unix_replies={"/tmp/loraconf433.sock": _RDYP1}).system,   # daemon serves only 433
        paths=Paths(runtime_root=tmp_path))
    # deliberately NO meshtastic running-band marker set
    assert svc._observed_conflicts() == []


# --- AU: process-topology truth + no-side-effect lifecycle ----------------------------------

def test_dead_conf_433_process_blocks_replacement_daemon(tmp_path):
    # observed --radio 433 with unreachable CONF -> a replacement 433 daemon is NOT launched.
    svc = ControllerService(system=FakeSystem(
        cmdlines_data={10: ["loraham_daemon", "--radio", "433"]}).system,   # no 433 socket = CONF down
        paths=Paths(runtime_root=tmp_path))
    res = svc.run_action("start", "daemon", apply=True)
    assert any("still holds the radio" in d and "433" in d for d in res.details)
    assert not any("start daemon --radio 433" in d for d in res.details)


def test_dead_conf_868_of_both_blocks_868_daemon_and_direct_stack(tmp_path):
    # --radio both with 868 CONF down still claims 868.
    svc = ControllerService(system=FakeSystem(
        cmdlines_data={20: ["loraham_daemon", "--radio", "both"], 200: ["meshtasticd"]},
        unix_replies={"/tmp/loraconf433.sock": _RDYP1}).system,          # 868 CONF down
        paths=Paths(runtime_root=tmp_path))
    assert "868" in svc._daemon_claimed_bands()
    assert any("868" in b["resource"] for b in svc.run_blockers("meshtastic"))   # blocks direct-SPI 868
    svc._set_running_band("meshtastic", "868")
    assert any("loraham.radio.868" in c.message for c in svc._observed_conflicts())  # true conflict


def test_independent_433_868_daemons_non_conflicting(tmp_path):
    svc = ControllerService(system=FakeSystem(
        cmdlines_data={30: ["loraham_daemon", "--radio", "433"], 31: ["loraham_daemon", "--radio", "868"]},
        unix_replies={"/tmp/loraconf433.sock": _RDYP1, "/tmp/loraconf868.sock": _RDYP1}).system,
        paths=Paths(runtime_root=tmp_path))
    assert svc._observed_conflicts() == []


def test_perband_daemon_stop_blocks_unknown_band_dependent(tmp_path):
    svc = ControllerService(system=FakeSystem(
        cmdlines_data={20: ["loraham_daemon", "--radio", "both"], 300: ["loraham-kiss-tnc"]},
        unix_replies={"/tmp/loraconf433.sock": _RDYP1, "/tmp/loraconf868.sock": _RDYP1}).system,
        paths=Paths(runtime_root=tmp_path))
    # kiss running, band-switchable, NO marker -> per-band stop is blocked, stops nothing
    res = svc.stop("daemon", apply=True, band="433")
    assert not res.ok
    assert any(r.component == "kiss" and r.outcome.value == "blocked" for r in res.results)


def test_whole_daemon_stop_cascades_unknown_band_dependent(tmp_path, monkeypatch):
    # a whole-daemon stop (no band) still cascades a markerless dependent normally.
    svc = _svc(tmp_path)
    monkeypatch.setattr(ControllerService, "stop_dependents", lambda self, t, bands=None: ["kiss"])
    res = svc.stop("daemon", apply=True)              # band="" -> whole daemon
    assert "unknown active band" not in (res.summary or "")
    assert any(r.component == "kiss" for r in res.results)


def test_healthy_start_stop_owners_stops_no_owner(tmp_path, monkeypatch):
    svc = ControllerService(system=FakeSystem(
        cmdlines_data={100: ["loraham_daemon", "--radio", "433"], 200: ["loraham_igate", "-c", "X"]},
        unix_replies={"/tmp/loraconf433.sock": _RDYP1}).system, paths=Paths(runtime_root=tmp_path))
    calls = _spy_stop(monkeypatch)
    set_call(svc)
    res = svc.start("igate", apply=True, stop_owners=True)
    assert res.ok and all(r.outcome.value == "already_healthy" for r in res.results)
    assert calls == []                                # no owner stopped


def test_default_restart_locks_only_running_band(tmp_path):
    svc = _svc(tmp_path); svc._set_running_band("kiss", "868")
    # emulate restart()'s pre-guard band resolution:
    rband = svc._effective_band("kiss", "")
    keys2 = [k for k in svc._lifecycle_lock_keys("restart", "kiss", band=rband) if "radio" in k]
    assert keys2 == ["claim.loraham.radio.868"]        # only the running band
