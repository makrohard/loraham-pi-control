"""U8 + U4 (PLAN-UPDATE-v6): one build evaluation, two outcomes. A component whose last build finished
against today's sources but whose recorded build INPUTS changed (an lhpc update shipped a new
`meshcore_host`) starts; the stack page offers "Rebuild available" instead of "Build required to start",
and the web start is not refused. A provider with changed inputs is still rebuilt before its dependent.
Through `lhpc stack start meshcore@r3`, the web POST op=start and the stack page."""
from radios_fixture import R1, R2_868, R3

from lhpc.core import config as cfgmod
from lhpc.core.commands import Instance
from lhpc.core.lifecycle import BUILD_MARKER_TEXT
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService

_UNDONE = ("install_blocker", "is_built", "_build_complete")      # the box fixture's readiness stubs
_REAL = {n: getattr(ControllerService, n, None) for n in (*_UNDONE, "_asset_digest")}


def _built(tmp_path, monkeypatch, sid, moved_asset):
    """Every component of `sid` built against today's sources and inputs, then `moved_asset` changed
    (its digest differs from the one recorded in the sidecar). The box fixture's readiness stubs are
    undone so the real readiness decides. The marker's consumed-source lines are fixed (no git here)."""
    for n in _UNDONE:
        if _REAL[n] is not None:
            monkeypatch.setattr(ControllerService, n, _REAL[n])
    monkeypatch.setattr(ControllerService, "_consumed_source_lines", lambda self, c: "")
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    for c in svc.stack(sid).components:
        if c.build_marker and c.build_steps:
            marker = svc._lifecycle().source_dir(c) / c.build_marker
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.write_text(BUILD_MARKER_TEXT)
            if c.build_inputs or c.asset_inputs:
                svc.build_inputs_path(c).parent.mkdir(parents=True, exist_ok=True)
                svc.build_inputs_path(c).write_text(svc.build_inputs_text(c))
        elif c.source is not None:
            svc._lifecycle().source_dir(c).mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(ControllerService, "_asset_digest", lambda self, rel: "0" * 64 if rel == moved_asset
                        else _REAL["_asset_digest"](self, rel))
    return ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))


def _node_started(launches) -> bool:
    return any(comp == "meshcore-node" and rid == "r3" for comp, _band, rid in launches)


def test_a_changed_meshcore_host_digest_starts(box, tmp_path, monkeypatch):
    run = box((R1, R2_868, R3), ports=(5266, 9054))
    cfgmod.update_stack_config(Paths(runtime_root=tmp_path), "meshcore", {"file_node_name": "Node R3"},
                               inst=Instance("r3"))
    svc = _built(tmp_path, monkeypatch, "meshcore", "meshcore_host")
    node = svc.stack("meshcore").component("meshcore-node")
    assert not svc.is_built(node)                                   # the planners still see a rebuild
    rc, out, launches = run("start", "meshcore@r3")
    assert rc == 0 and _node_started(launches), out
    assert "sources changed since the last build" not in out and "not built" not in out, out
    assert svc.start_readiness("meshcore") == ([], ["meshcore-node"])


def test_the_page_offers_a_rebuild_and_the_web_start_is_not_refused(box, web, csrf, tmp_path, monkeypatch):
    box((R1, R2_868, R3))
    _built(tmp_path, monkeypatch, "meshcore", "meshcore_host")
    c = web()
    body = c.get("/stacks/meshcore/body").get_data(as_text=True)
    assert "Rebuild available" in body and "meshcore-node starts as built" in body, body
    assert "Build required to start" not in body and "Build needed" not in body
    resp = c.post("/action", data={"_csrf": csrf(c), "op": "start", "target": "meshcore@r3"})
    assert resp.status_code == 302 and "#stack-install-" not in resp.location, resp.location   # past the build gate
    page = c.get(resp.location.split("#")[0]).get_data(as_text=True)
    assert "cannot start yet" not in page and "needs building" not in page


def test_a_marker_from_older_sources_still_refuses_and_the_page_says_build_required(box, web, csrf, tmp_path,
                                                                                    monkeypatch):
    """The anchor: a build that did not finish against today's sources refuses the start, by name."""
    run = box((R1, R2_868, R3), ports=(5266, 9054))
    cfgmod.update_stack_config(Paths(runtime_root=tmp_path), "meshcore", {"file_node_name": "Node R3"},
                               inst=Instance("r3"))
    svc = _built(tmp_path, monkeypatch, "meshcore", "")
    node = svc.stack("meshcore").component("meshcore-node")
    (svc._lifecycle().source_dir(node) / node.build_marker).write_text(BUILD_MARKER_TEXT + "consumed x 1\n")
    rc, out, launches = run("start", "meshcore@r3")
    assert rc == 1 and not _node_started(launches), out
    assert "meshcore-node: its sources changed since the last build" in out, out
    blocked, rebuild = svc.start_readiness("meshcore")
    assert [cid for cid, _why in blocked] == ["meshcore-node"] and rebuild == []
    c = web()
    body = c.get("/stacks/meshcore/body").get_data(as_text=True)
    assert "Build required to start" in body and "meshcore-node: not built" in body, body
    assert "Rebuild available" not in body
    resp = c.post("/action", data={"_csrf": csrf(c), "op": "start", "target": "meshcore@r3"})
    assert resp.status_code == 302 and "#stack-install-meshcore" in resp.location, resp.location
    page = c.get(resp.location.split("#")[0]).get_data(as_text=True)
    assert "&#39;meshcore&#39; cannot start yet — meshcore-node: not built — run: lhpc build meshcore." in page


def _start_flash(c, csrf, target):
    """The web start (POST op=start) of `target`: the page it lands on (its flash)."""
    resp = c.post("/action", data={"_csrf": csrf(c), "op": "start", "target": target})
    assert resp.status_code == 302, resp.status_code
    return c.get(resp.location.split("#")[0]).get_data(as_text=True)


def test_a_component_whose_gui_toolkit_is_absent_is_not_a_start_blocker(box, web, csrf, tmp_path, monkeypatch):
    """The same scope as `unbuilt_components`: voice's GTK app on a box without the toolkit can never be
    built here, so it neither blocks the start nor offers a rebuild; its terminal variant still counts.
    Through the web start gate too: the refusal names the terminal variant only."""
    box((R1, R2_868))
    svc = _built(tmp_path, monkeypatch, "voice", "")
    monkeypatch.setattr(ControllerService, "gui_unavailable_components", lambda self, stack: ())   # the toolkit here
    blocked = dict(svc.start_readiness("voice")[0])
    assert set(blocked) == {"loraham-voice", "loraham-voice-cli"}, blocked     # nothing built yet
    monkeypatch.setattr(ControllerService, "gui_unavailable_components", lambda self, stack: ("loraham-voice",))
    assert [cid for cid, _why in svc.start_readiness("voice")[0]] == ["loraham-voice-cli"]
    assert svc.set_operator_identity(callsign="XX0XXA").ok        # voice's identity gate comes first
    page = _start_flash(web(), csrf, "voice@r1")
    assert "&#39;voice&#39; cannot start yet — loraham-voice-cli: not built" in page, page
    assert "loraham-voice: " not in page


def test_a_provider_with_changed_inputs_is_still_rebuilt_before_its_dependent(box, tmp_path, monkeypatch):
    box((R1, R2_868))
    svc = _built(tmp_path, monkeypatch, "reticulum", "scripts/rns-wheel.sh")
    assert "rns" in svc.unbuilt_build_deps("reticulum")             # the build order is unchanged
    blocked, rebuild = svc.start_readiness("reticulum")
    assert blocked == [] and "rns" in rebuild                       # the start is not refused


def test_a_provider_with_changed_inputs_shows_no_dependency_warning(box, web, tmp_path, monkeypatch):
    """G1-UPD-U8-CODE N1: the provider starts as built, so the page offers the rebuild and names no
    dependency that must be built first (that warning follows the start readiness, not the build plan)."""
    box((R1, R2_868))
    svc = _built(tmp_path, monkeypatch, "reticulum", "scripts/rns-wheel.sh")
    assert "rns" in svc.unbuilt_build_deps("reticulum")
    body = web().get("/stacks/reticulum/body").get_data(as_text=True)
    assert "Rebuild available" in body, body
    assert "Build this dependency first" not in body and "rns not built" not in body, body


def test_a_binary_receipt_failure_offers_install_not_build(box, web, tmp_path, monkeypatch, binary_receipt):
    """G1-UPD-U8-CODE N2: `lhpc build` is refused on the binary channel, so a refusal by the receipt
    (here a recorded file gone) links to the Install section as an install, never "Build"."""
    box((R1, R2_868))
    for n in _UNDONE:
        if _REAL[n] is not None:
            monkeypatch.setattr(ControllerService, n, _REAL[n])
    monkeypatch.setattr(ControllerService, "binary_target", lambda self: "aarch64-trixie")
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    binary_receipt(svc, "meshtastic")
    (tmp_path / svc.binary_spec("meshtastic").proof_paths[0]).unlink()
    blocked = dict(ControllerService(system=FakeSystem().system,
                                     paths=Paths(runtime_root=tmp_path)).start_readiness("meshtastic")[0])
    assert "is gone" in blocked.get("meshtastic", ""), blocked
    body = web().get("/stacks/meshtastic/body").get_data(as_text=True)
    assert "Install required to start" in body and "meshtastic: binary artifact file" in body, body
    assert "Build required to start" not in body
    banner = body.split("Install required to start", 1)[1].split("</div>", 1)[0]   # its body and its link
    assert "Install &rarr;" in banner and "Build &rarr;" not in banner, banner


def test_an_unsafe_daemon_receipt_names_no_build_dependency(box, web, tmp_path, monkeypatch, binary_receipt):
    """G1-UPD-U8-CODE v2 N1: on an unsafe daemon receipt every covered component (RadioLib too) refuses by the
    receipt's reason. RadioLib's checkout is there and unbuilt (a build-planning dependency), yet the page shows
    no "Build this dependency first" (`lhpc build` is refused on the binary channel), only the Install banner."""
    box((R1, R2_868))
    for n in _UNDONE:
        if _REAL[n] is not None:
            monkeypatch.setattr(ControllerService, n, _REAL[n])
    monkeypatch.setattr(ControllerService, "binary_target", lambda self: "aarch64-trixie")
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    radiolib = svc.stack("daemon").component("radiolib")
    svc._lifecycle().source_dir(radiolib).mkdir(parents=True, exist_ok=True)
    binary_receipt(svc, "daemon", probe="loraham_daemon 0.9.0")
    (tmp_path / svc.binary_spec("daemon").proof_paths[0]).unlink()
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    assert "radiolib" in svc.unbuilt_build_deps("daemon")                    # the build plan still lists it
    assert "is gone" in dict(svc.start_readiness("daemon")[0])["radiolib"]
    body = web().get("/stacks/daemon/body").get_data(as_text=True)
    assert "Build this dependency first" not in body and "radiolib not built" not in body, body
    assert "Install required to start" in body and "radiolib: binary artifact file" in body, body


def test_an_unbuilt_source_dependency_is_named_to_build_first(box, web, tmp_path, monkeypatch):
    """The anchor of the test above, off the binary channel: RadioLib's checkout is there and unbuilt, so its
    start refusal is a source build's and the page names it to build first."""
    box((R1, R2_868))
    for n in _UNDONE:
        if _REAL[n] is not None:
            monkeypatch.setattr(ControllerService, n, _REAL[n])
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    for cid in ("loraham-daemon", "radiolib"):
        svc._lifecycle().source_dir(svc.stack("daemon").component(cid)).mkdir(parents=True, exist_ok=True)
    assert dict(svc.start_readiness("daemon")[0])["radiolib"] == f"not built — run: {svc.build_remedy('daemon', 'radiolib')}"
    body = web().get("/stacks/daemon/body").get_data(as_text=True)
    assert "radiolib not built" in body and "Build this dependency first" in body, body
