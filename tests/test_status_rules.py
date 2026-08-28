"""Tests for the component status state rules (running/degraded/stopped/failed/
unknown/not-installed), driven entirely by fakes."""

from __future__ import annotations

from pathlib import Path

from lhpc.core.model import (
    Component,
    ComponentKind,
    EndpointSpec,
    ProcessSpec,
    RunState,
    SourceSpec,
    UnitRef,
)
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import CommandResult, FakeSystem, Listener
from lhpc.core.status import StatusProber

_PROPS = "ActiveState,SubState,LoadState,UnitFileState"


def _unit_argv(unit: str) -> tuple[str, ...]:
    return ("systemctl", "show", unit, "--property", _PROPS)


def _show(active: str, load: str = "loaded") -> str:
    return f"ActiveState={active}\nSubState=x\nLoadState={load}\nUnitFileState=enabled\n"


def _prober(fake: FakeSystem, tmp_path: Path) -> StatusProber:
    return StatusProber(fake.system, Paths(runtime_root=tmp_path))


def _svc(**kw) -> Component:
    kw.setdefault("name", kw["id"])
    kw.setdefault("kind", ComponentKind.SERVICE)
    return Component(**kw)


def test_running_with_active_unit_and_listening_endpoint(tmp_path):
    comp = _svc(id="x", units=(UnitRef("x.service"),),
                endpoints=(EndpointSpec(kind="tcp", address="127.0.0.1:7000"),))
    fake = FakeSystem(
        commands={_unit_argv("x.service"): CommandResult(0, _show("active"), "")},
        listeners=[Listener("ipv4", "127.0.0.1", 7000, 1)],
    )
    assert _prober(fake, tmp_path).assess_component(comp).run_state is RunState.RUNNING


def test_degraded_when_active_but_endpoint_absent(tmp_path):
    comp = _svc(id="x", units=(UnitRef("x.service"),),
                endpoints=(EndpointSpec(kind="tcp", address="127.0.0.1:7000"),))
    fake = FakeSystem(commands={_unit_argv("x.service"): CommandResult(0, _show("active"), "")})
    assert _prober(fake, tmp_path).assess_component(comp).run_state is RunState.DEGRADED


def test_stopped_when_inactive_and_no_process(tmp_path):
    comp = _svc(id="x", units=(UnitRef("x.service"),))
    fake = FakeSystem(commands={_unit_argv("x.service"): CommandResult(0, _show("inactive"), "")})
    assert _prober(fake, tmp_path).assess_component(comp).run_state is RunState.STOPPED


def test_failed_unit(tmp_path):
    comp = _svc(id="x", units=(UnitRef("x.service"),))
    fake = FakeSystem(commands={_unit_argv("x.service"): CommandResult(0, _show("failed"), "")})
    assert _prober(fake, tmp_path).assess_component(comp).run_state is RunState.FAILED


def test_unknown_when_probe_unavailable(tmp_path):
    comp = _svc(id="x", units=(UnitRef("x.service"),))
    fake = FakeSystem(commands={_unit_argv("x.service"): CommandResult(127, "", "", not_found=True)})
    assert _prober(fake, tmp_path).assess_component(comp).run_state is RunState.UNKNOWN


def test_running_by_process_only_no_systemd(tmp_path):
    # Proves a verdict needs real evidence (matched process), not a PID file.
    comp = _svc(id="x", process=ProcessSpec(exec_name="loraham_daemon", any_args=("433",)))
    fake = FakeSystem(cmdlines_data={42: ["loraham_daemon", "--radio", "433"]})
    st = _prober(fake, tmp_path).assess_component(comp)
    assert st.run_state is RunState.RUNNING and st.pids == [42]


def test_socket_present_but_no_process_is_not_running(tmp_path):
    # A provider socket existing is NOT sufficient to call a service running.
    comp = _svc(id="x", units=(UnitRef("x.service"),),
                process=ProcessSpec(exec_name="loraham_daemon", any_args=("433",)),
                endpoints=(EndpointSpec(kind="unix", address="/tmp/loraconf433.sock", role="provider"),))
    fake = FakeSystem(
        commands={_unit_argv("x.service"): CommandResult(0, _show("inactive"), "")},
        sockets={"/tmp/loraconf433.sock"},
    )
    assert _prober(fake, tmp_path).assess_component(comp).run_state is RunState.STOPPED


def test_not_installed_when_source_missing(tmp_path):
    comp = _svc(id="x", units=(UnitRef("x.service"),),
                source=SourceSpec(path="src/x", pin_commit="a" * 40))
    fake = FakeSystem(commands={_unit_argv("x.service"): CommandResult(0, _show("inactive"), "")})
    # runtime root (tmp_path) exists, but the component source path does not.
    assert _prober(fake, tmp_path).assess_component(comp).run_state is RunState.NOT_INSTALLED


def test_daemon_ready_endpoint_makes_running(tmp_path):
    comp = _svc(id="d", units=(UnitRef("d.service"),),
                endpoints=(EndpointSpec(kind="unix", address="/tmp/loraconf433.sock",
                                        role="provider", readiness="daemon-status"),))
    fake = FakeSystem(
        commands={_unit_argv("d.service"): CommandResult(0, _show("active"), "")},
        sockets={"/tmp/loraconf433.sock"},
        unix_replies={"/tmp/loraconf433.sock": b"STATUS RADIO=READY TXMODE=DIRECT\n"},
    )
    assert _prober(fake, tmp_path).assess_component(comp).run_state is RunState.RUNNING


def test_daemon_not_ready_endpoint_makes_degraded(tmp_path):
    comp = _svc(id="d", units=(UnitRef("d.service"),),
                endpoints=(EndpointSpec(kind="unix", address="/tmp/loraconf433.sock",
                                        role="provider", readiness="daemon-status"),))
    fake = FakeSystem(
        commands={_unit_argv("d.service"): CommandResult(0, _show("active"), "")},
        sockets={"/tmp/loraconf433.sock"},
        unix_replies={"/tmp/loraconf433.sock": b"STATUS RADIO=UNINITIALIZED\n"},
    )
    assert _prober(fake, tmp_path).assess_component(comp).run_state is RunState.DEGRADED


def test_path_endpoint_symlink_to_outside_device_reads_present(tmp_path):
    """A `path` endpoint that is a SYMLINK to a node OUTSIDE the runtime root (e.g. a socat
    PTY link `state/loraham_kiss -> /dev/pts/N`) must resolve to the IN-ROOT leaf and read
    PRESENT — not be mistaken for a containment escape and reported absent. Strict `under()`
    realpath-follows the leaf, sees it escape, and refuses (the bug that stuck the KISS
    serial bridge in DEGRADED); the lenient (path) resolution contains it lexically."""
    import os

    from lhpc.core.probes import RealSystem
    (tmp_path / "state").mkdir()
    outside = tmp_path.parent / (tmp_path.name + "_dev_target")
    outside.write_text("x")                               # stands in for /dev/pts/N
    (tmp_path / "state" / "loraham_kiss").symlink_to(outside)
    prober = StatusProber(RealSystem(), Paths(runtime_root=tmp_path))
    lenient = prober._resolve_addr("state/loraham_kiss", lenient=True)
    strict = prober._resolve_addr("state/loraham_kiss", lenient=False)
    # lenient -> the in-root leaf, which os.path.exists follows to the (existing) target
    assert lenient.endswith("state/loraham_kiss") and os.path.exists(lenient)
    # strict -> a guaranteed-absent sentinel (containment refusal), i.e. the old buggy path
    assert strict.endswith(".unresolved-endpoint") and not os.path.exists(strict)


def test_path_endpoint_lenient_still_rejects_dotdot_escape(tmp_path):
    """Lexical leniency for path endpoints must NOT allow a `..` escape — only a symlink
    leaf to an external node is tolerated, never a path that lexically leaves the root."""
    from lhpc.core.probes import RealSystem
    prober = StatusProber(RealSystem(), Paths(runtime_root=tmp_path))
    resolved = prober._resolve_addr("../evil", lenient=True)
    assert resolved.endswith(".unresolved-endpoint")     # refused -> absent sentinel


# --- runtime-band overlay (dual-radio truth: status must show the ACTUAL band) ----------------

def _kiss_snapshot(svc, run_state):
    """A snapshot shaped like the prober's output for the real manifest kiss stack: both components
    at `run_state`, kiss-serial's dependency band prefilled with the MANIFEST default (what the
    prober records before the service-layer overlay)."""
    from lhpc.core.model import ComponentStatus, DependencyObservation
    from lhpc.core.status import Snapshot, StackStatus
    kiss = next(s for s in svc.stacks() if s.id == "kiss")
    snap = Snapshot(runtime_root_exists=True)
    ss = StackStatus(stack=kiss)
    for comp in kiss.components:
        ss.components[comp.id] = ComponentStatus(component_id=comp.id, run_state=run_state)
    tnc = next(c for c in kiss.components if c.id == "loraham-kiss-tnc")
    ss.components["loraham-kiss-serial"].dependencies.append(
        DependencyObservation(component_id="loraham-kiss-tnc", run_state=run_state, band=tnc.band))
    snap.stacks.append(ss)
    return snap, kiss, ss


def test_runtime_band_overlay_shows_actual_band_not_manifest_default(tmp_path):
    # Live dual-radio find: kiss STARTED ON 868 (lhpc even named the log
    # start-loraham-kiss-tnc-868.log) but `lhpc status` said "band 433" and "running on 433 MHz" —
    # the manifest default. The overlay must stamp the running-band marker onto RUNNING components
    # AND rewrite the dependency line to the band the dependency ACTUALLY runs on.
    from lhpc.core.services import ControllerService, _render_component
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    assert svc._set_running_band("kiss", "868")           # the marker lifecycle writes at start
    snap, kiss, ss = _kiss_snapshot(svc, RunState.RUNNING)
    svc._overlay_runtime_bands(snap)
    tnc = next(c for c in kiss.components if c.id == "loraham-kiss-tnc")
    serial = next(c for c in kiss.components if c.id == "loraham-kiss-serial")
    assert ss.components["loraham-kiss-tnc"].band == "868"
    assert "band 868" in _render_component(tnc, ss.components["loraham-kiss-tnc"])[0]
    rendered = _render_component(serial, ss.components["loraham-kiss-serial"])
    assert any("depends on loraham-kiss-tnc" in ln and "on 868 MHz" in ln for ln in rendered), rendered


def test_runtime_band_overlay_stopped_keeps_manifest_default(tmp_path):
    # STOPPED components keep the manifest label even when a stale marker exists — the overlay is
    # gated on run_state, so the single-radio rendering is unchanged.
    from lhpc.core.services import ControllerService, _render_component
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    assert svc._set_running_band("kiss", "868")           # stale marker from an earlier run
    snap, kiss, ss = _kiss_snapshot(svc, RunState.STOPPED)
    svc._overlay_runtime_bands(snap)
    tnc = next(c for c in kiss.components if c.id == "loraham-kiss-tnc")
    assert ss.components["loraham-kiss-tnc"].band == ""    # no overlay
    assert f"band {tnc.band}" in _render_component(tnc, ss.components["loraham-kiss-tnc"])[0]


# --- GUI-unavailable overlay (headless truth: skipped-by-design is not "not-installed") -------

def _meshcore_snapshot(svc):
    """Prober-shaped snapshot for the real manifest meshcore stack: core installed-but-stopped,
    the OPTIONAL Tk GUI helper not-installed (headless box that never cloned it)."""
    from lhpc.core.model import ComponentStatus
    from lhpc.core.status import Snapshot, StackStatus
    mc = next(s for s in svc.stacks() if s.id == "meshcore")
    snap = Snapshot(runtime_root_exists=True)
    ss = StackStatus(stack=mc)
    for comp in mc.components:
        state = RunState.NOT_INSTALLED if comp.id == "meshcore-nodegui" else RunState.STOPPED
        ss.components[comp.id] = ComponentStatus(component_id=comp.id, run_state=state)
    snap.stacks.append(ss)
    return snap, ss


def test_gui_unavailable_overlay_marks_component_not_applicable(tmp_path, monkeypatch):
    # Live headless find (Zero): the whole meshcore stack rolled up "(not-installed)" although
    # meshcore-pi was installed and merely stopped — only the deliberately skipped Tk GUI helper
    # was missing. The overlay must read it NOT_APPLICABLE so the badge tells the truth.
    from lhpc.core.services import ControllerService
    from lhpc.core.status import rollup_states
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    monkeypatch.setattr(ControllerService, "gui_unavailable_components",
                        lambda self, stack: ("meshcore-nodegui",))
    snap, ss = _meshcore_snapshot(svc)
    svc._overlay_gui_unavailable(snap)
    assert ss.components["meshcore-nodegui"].run_state is RunState.NOT_APPLICABLE
    assert ss.components["meshcore-pi"].run_state is RunState.STOPPED     # untouched
    assert rollup_states(snap)["meshcore"] == "stopped"


def test_gui_unavailable_overlay_leaves_gui_capable_box_alone(tmp_path, monkeypatch):
    # With the GUI dependency PRESENT the predicate returns nothing and not-installed stays
    # not-installed — the overlay never hides a genuinely missing install.
    #
    # Two different jobs, deliberately: the OVERLAY answers "can this component work on this
    # box at all", so on a GUI-capable box an absent Node Manager is honestly not-installed.
    # The ROLLUP separately declines to let a never-installed OPTIONAL component speak for
    # the whole stack (see test_meshcore.py) — which is why the badge below reads "stopped"
    # while the component still reads "not-installed".
    from lhpc.core.services import ControllerService
    from lhpc.core.status import rollup_states
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    monkeypatch.setattr(ControllerService, "gui_unavailable_components",
                        lambda self, stack: ())
    snap, ss = _meshcore_snapshot(svc)
    svc._overlay_gui_unavailable(snap)
    assert ss.components["meshcore-nodegui"].run_state is RunState.NOT_INSTALLED
    assert rollup_states(snap)["meshcore"] == "stopped"


# ---- licensed TX overlay -----------------------------------------------------------------

def _tx_snap(tmp_path):
    """A snapshot with every component's run/tx state pinned, ready for the overlay."""
    from lhpc.core.services import ControllerService
    from lhpc.core.model import TxState
    svc = ControllerService(paths=Paths(runtime_root=tmp_path))
    snap = svc.build_snapshot()
    for ss in snap.stacks:
        for st in ss.components.values():
            st.run_state, st.tx_state = RunState.STOPPED, TxState.DISABLED
    return svc, snap


def _st(snap, cid):
    return next(ss.components[cid] for ss in snap.stacks if cid in ss.components)


_REAL_BRIDGE = ["build/meshcom-loraham-bridge", "--backend", "loraham", "--port", "7000"]
_EXTRADIO = ["/x/qemu-system-xtensa", "-drive",
             "file=/p/.pio/build/qemu-headless-extradio-gpsd/flash.bin,if=mtd"]
_DAEMON_433 = ["/opt/loraham_daemon", "--radio", "433", "--tx-mode", "MANAGED"]


def _meshcom_tx(tmp_path, *, bridge=_REAL_BRIDGE, qemu=_EXTRADIO, daemon=_DAEMON_433):
    """MeshCom's TX state after the overlay, with each link of the RF chain supplied or withheld.

    `None` for a link means that component is NOT running (no PID, no argv) — the chain is
    incomplete, which is a different fact from a component running with a no-RF argv.
    """
    from lhpc.core.model import TxState
    from lhpc.core.paths import Paths
    from lhpc.core.probes.backends import FakeSystem
    from lhpc.core.services import ControllerService
    live = {cid: argv for cid, argv in (("meshcom-bridge", bridge), ("meshcom-qemu", qemu),
                                        ("loraham-daemon", daemon)) if argv is not None}
    pids = {cid: 101 + i for i, cid in enumerate(sorted(live))}
    sysm = FakeSystem(cmdlines_data={pids[cid]: live[cid] for cid in live}).system
    svc = ControllerService(system=sysm, paths=Paths(runtime_root=tmp_path))
    snap = svc.build_snapshot()
    for ss in snap.stacks:
        for st in ss.components.values():
            st.run_state, st.tx_state = RunState.STOPPED, TxState.DISABLED
    for cid in live:
        st = _st(snap, cid)
        st.run_state, st.tx_state, st.pids = RunState.RUNNING, TxState.UNKNOWN, [pids[cid]]
    svc._overlay_licensed_tx_enabled(snap)
    # The STACK's verdict: did any live MeshCom component come out claiming TX? A withheld link
    # simply stays STOPPED/DISABLED, so asking one fixed component would silently pass.
    upgraded = [cid for cid in live if _st(snap, cid).tx_state is TxState.ENABLED]
    return TxState.ENABLED if upgraded else TxState.UNKNOWN


def test_meshcom_tx_enabled_needs_the_whole_live_rf_chain(tmp_path):
    # TxState.ENABLED had ZERO producers: status.py emits only DISABLED/UNKNOWN, so meshcom read
    # "tx unknown" while the daemon's own counters showed completed transmissions. But "licensed +
    # running" is NOT capability: the emulated node has no radio of its own, so ENABLED demands the
    # COMPLETE live chain — bridge on the real backend, a QEMU guest booted from an extradio image,
    # and the daemon that actually owns the 433 radio. Any missing link is UNKNOWN, not ENABLED.
    from lhpc.core.model import TxState
    assert _meshcom_tx(tmp_path) is TxState.ENABLED
    assert _meshcom_tx(tmp_path, qemu=None) is TxState.UNKNOWN        # bridge alone
    assert _meshcom_tx(tmp_path, bridge=None) is TxState.UNKNOWN      # QEMU alone
    assert _meshcom_tx(tmp_path, daemon=None) is TxState.UNKNOWN      # no radio behind it


def test_meshcom_tx_rejects_every_no_rf_argv(tmp_path):
    # Each of these is a LIVE, complete-looking chain that still cannot transmit. The verdict is
    # read from argv TOKENS and from the `-drive` specification — never from a substring of the
    # joined command line, which is what let a stray `qemu-headless-extradio` token vouch for a
    # guest booting a plain image.
    from lhpc.core.model import TxState
    no_rf = {
        "backend absent (upstream defaults to fake)": ["build/meshcom-loraham-bridge",
                                                       "--port", "7000"],
        "fake backend, space form": ["build/meshcom-loraham-bridge", "--backend", "fake"],
        "fake backend, equals form": ["build/meshcom-loraham-bridge", "--backend=fake"],
        "receive only": [*_REAL_BRIDGE, "--rx-only"],
    }
    for why, argv in no_rf.items():
        assert _meshcom_tx(tmp_path, bridge=argv) is TxState.UNKNOWN, why

    plain = ["/x/qemu-system-xtensa", "-drive",
             "file=/p/.pio/build/qemu-headless-gpsd/flash.bin,if=mtd"]
    # The WORD present, the IMAGE plain: a log path (or an --env label) carrying the string must
    # not outvote the drive the guest actually boots from.
    word_only = ["/x/qemu-system-xtensa", "-serial",
                 "file:/var/log/qemu-headless-extradio-gpsd.log", "-drive",
                 "file=/p/.pio/build/qemu-headless-gpsd/flash.bin,if=mtd"]
    assert _meshcom_tx(tmp_path, qemu=plain) is TxState.UNKNOWN
    assert _meshcom_tx(tmp_path, qemu=word_only) is TxState.UNKNOWN
    # ... and the equals spelling of -drive still proves a genuine extradio image.
    assert _meshcom_tx(tmp_path, qemu=["/x/qemu-system-xtensa",
                                       "-drive=file=/p/.pio/build/qemu-headless-extradio/flash.bin"]
                       ) is TxState.ENABLED


def test_tx_overlay_needs_a_live_daemon_for_a_daemon_backed_client(tmp_path):
    # The daemon owns the radio. A licensed client that `depends_on` it cannot be transmitting
    # while it is down, however healthy the client's own process looks — and a daemon serving only
    # 868 is no radio for a 433 client.
    from lhpc.core.model import TxState
    assert _meshcom_tx(tmp_path, daemon=["/opt/loraham_daemon", "--radio", "868"]) is TxState.UNKNOWN
    assert _meshcom_tx(tmp_path, daemon=["/opt/loraham_daemon", "--radio=433"]) is TxState.ENABLED


def test_tx_overlay_never_upgrades_disabled(tmp_path):
    # DISABLED is authoritative — it means "not tx_capable" or "not running". Upgrading it would
    # claim TX for a component the prober already ruled out.
    from lhpc.core.model import TxState
    svc, snap = _tx_snap(tmp_path)
    st = _st(snap, "meshcom-bridge")
    st.run_state, st.tx_state = RunState.RUNNING, TxState.DISABLED
    svc._overlay_licensed_tx_enabled(snap)
    assert _st(snap, "meshcom-bridge").tx_state is TxState.DISABLED


def test_tx_overlay_leaves_daemon_and_unlicensed_stacks_unknown(tmp_path):
    # The daemon is exempt in _identity_field; meshtastic/meshcore are "unlicensed" (node name).
    # Neither may gain ENABLED from a licensed-stack inference.
    from lhpc.core.model import TxState
    svc, snap = _tx_snap(tmp_path)
    for cid in ("loraham-daemon", "meshtastic", "meshcore-pi"):
        s = _st(snap, cid)
        s.run_state, s.tx_state = RunState.RUNNING, TxState.UNKNOWN
    svc._overlay_licensed_tx_enabled(snap)
    for cid in ("loraham-daemon", "meshtastic", "meshcore-pi"):
        assert _st(snap, cid).tx_state is TxState.UNKNOWN, cid


def test_tx_overlay_ignores_stopped_licensed_components(tmp_path):
    from lhpc.core.model import TxState
    svc, snap = _tx_snap(tmp_path)
    st = _st(snap, "loraham-chat")                        # chat == licensed, but stopped
    st.run_state, st.tx_state = RunState.STOPPED, TxState.UNKNOWN
    svc._overlay_licensed_tx_enabled(snap)
    assert _st(snap, "loraham-chat").tx_state is TxState.UNKNOWN


def test_tx_overlay_is_fail_soft(tmp_path):
    # Fail-soft like the sibling overlays: a broken identity lookup must not take the whole
    # snapshot (and every caller composing it) down with it.
    from lhpc.core.model import TxState
    from lhpc.core.services import ControllerService
    svc, snap = _tx_snap(tmp_path)
    st = _st(snap, "meshcom-bridge")
    st.run_state, st.tx_state = RunState.RUNNING, TxState.UNKNOWN
    def boom(self, target):
        raise RuntimeError("identity layer unavailable")
    ControllerService._identity_field = boom
    try:
        svc._overlay_licensed_tx_enabled(snap)            # must NOT raise
    finally:
        del ControllerService._identity_field
    assert _st(snap, "meshcom-bridge").tx_state is TxState.UNKNOWN


def test_tx_overlay_needs_readable_argv_for_every_link(tmp_path):
    # No readable argv is NOT "no negative marker found" — it is no evidence at all, and the
    # overlay must read UNKNOWN rather than green a chain it cannot see.
    from lhpc.core.model import TxState
    assert _meshcom_tx(tmp_path, bridge=[], qemu=[]) is TxState.UNKNOWN
    assert _meshcom_tx(tmp_path, qemu=[]) is TxState.UNKNOWN
