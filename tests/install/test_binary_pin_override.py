"""A binary install never goes over the pin check (`--accept-pin-mismatch` is removed, decision D1):
no published binary built for this lhpc is ONE typed refusal (`none_compatible`) with the newest
entry's lagging pairs, self-update first and source second; a malformed commit is refused before
anything is offered. Receipts written by an older lhpc with a recorded acceptance are still READ:
gate 2 honours one only while it is exact and the component is not clone_required now; status,
doctor and boot restore name it while it is in force."""
import dataclasses

import pytest

from lhpc.core import binary_install as bi
from lhpc.core import binary_receipt as brx
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService

A, B, C = "a" * 40, "b" * 40, "c" * 40


def _svc(tmp_path, monkeypatch):
    monkeypatch.setattr(ControllerService, "binary_target", lambda self: "aarch64-trixie")
    return ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))


def _lagging_entry(svc, stack, cid, commit, stub_pipeline, *, sha="a" * 64, download=None):
    """The published entry of `stack` with `cid` built from `commit` (the others at the pins)."""
    entry = stub_pipeline(svc, download=download)

    def _e(idx, sid):
        e = entry(sid)
        return dataclasses.replace(e, sha256=sha, filename=f"{sid}-{sha}.tar.zst",
                                   components={**e.components, cid: commit})
    return _e


def _daemon_cid(svc):
    return next(iter(svc._binary_pins("daemon")))


def _never_download(*a, **k):
    raise AssertionError("must not download")


# ---- gate 1: the one map, the typed refusal ------------------------------------------------------

def test_check_pins_raises_the_structured_mismatch():
    entry = type("E", (), {})()
    entry.components = {"x": A, "y": B}
    with pytest.raises(bi.BinaryNoneCompatible) as ei:
        bi.check_pins(entry, {"x": C, "y": B})
    assert ei.value.mismatch == {"x": (A, C)}          # only the lagging pair, (binary, pin)


@pytest.mark.parametrize("apply", [False, True])
@pytest.mark.parametrize("op", ["install", "update"])
@pytest.mark.parametrize("stack,cid", [("daemon", None), ("meshcom", "meshcom-qemu")])
def test_none_compatible_is_one_typed_refusal_self_update_first(tmp_path, monkeypatch, stub_pipeline,
                                                                apply, op, stack, cid):
    """Every stack, clone_required or not, gets the same two ways and no third: nothing is
    installed over the pin check, nothing is downloaded, no receipt is written."""
    svc = _svc(tmp_path, monkeypatch)
    cid = cid or _daemon_cid(svc)
    monkeypatch.setattr(bi, "index_entry", _lagging_entry(svc, stack, cid, A, stub_pipeline,
                                                          download=_never_download))
    r = (svc.install if op == "install" else svc.update)(stack, apply=apply, source="binary")
    assert not r.ok and r.data["binary_refusal"] == "none_compatible"
    assert r.data["pin_mismatch"] == {cid: [A, svc._binary_pins(stack)[cid]]}
    assert r.next_commands == ["lhpc self-update --apply",
                               f"lhpc install {stack} --source pinned --yes"]
    assert [d.strip()[:2] for d in r.details[:2]] == ["1.", "2."]
    assert not any("accept-pin-mismatch" in x for x in [r.summary, *r.details, *r.next_commands])
    assert not (tmp_path / "state" / "binary").exists()


def test_the_manifest_clone_required_list_is_meshcom_qemu_only(tmp_path, monkeypatch):
    """The receipt reader's B1 rule reads the spec, not a name; a new clone_required entry shows up here."""
    svc = _svc(tmp_path, monkeypatch)
    listed = sorted(c for st in svc.stacks() if svc.binary_spec(st.id)
                    for c in svc.binary_spec(st.id).clone_required)
    assert listed == ["meshcom-qemu"]


# ---- B2: a malformed commit is refused, not reported as a mismatch -------------------------------

@pytest.mark.parametrize("bad", ["abc123", "A" * 40, "g" * 40])
@pytest.mark.parametrize("side", ["binary", "pin"])
@pytest.mark.parametrize("apply", [False, True])
@pytest.mark.parametrize("op", ["install", "update"])
def test_malformed_commit_refused_before_download(tmp_path, monkeypatch, stub_pipeline, bad, side,
                                                  op, apply):
    svc = _svc(tmp_path, monkeypatch)
    cid = _daemon_cid(svc)
    if side == "binary":
        monkeypatch.setattr(bi, "index_entry", _lagging_entry(svc, "daemon", cid, bad,
                                                              stub_pipeline,
                                                              download=_never_download))
    else:
        fixed = stub_pipeline(svc, download=_never_download)("daemon")   # at the REAL pins
        pins = dict(svc._binary_pins("daemon"))
        monkeypatch.setattr(ControllerService, "_binary_pins",
                            lambda self, sid: {**pins, cid: bad})
        monkeypatch.setattr(bi, "index_entry", lambda idx, sid: fixed)
    call = (svc.install if op == "install" else svc.update)
    r = call("daemon", apply=apply, source="binary")
    assert not r.ok and ("malformed" in r.summary or "not a full commit id" in r.summary)
    assert "pin_mismatch" not in r.data and "binary_refusal" not in r.data
    assert not (tmp_path / "state" / "binary").exists()


# ---- gate 2: the start, against a receipt an older lhpc may have written -------------------------

def test_a_lagging_covered_library_does_not_block_the_start(tmp_path, monkeypatch, binary_receipt):
    """The daemon's artifact also covers RadioLib, a library that never runs. Its lag, with or
    without an acceptance, no longer blocks a start (the maintainer's decision after 0.11.5); it
    still reads "behind" in the update status. The daemon's own lag still blocks."""
    from lhpc.core import config as cfgmod
    svc = _svc(tmp_path, monkeypatch)
    cfgmod.save_hardware_setup(svc._paths, "uputronics")
    svc._invalidate_config()
    pins = svc._binary_pins("daemon")
    lib = "radiolib"
    assert svc.stack("daemon").component(lib).kind.value == "library"
    daemon = svc.stack("daemon").component("loraham-daemon")
    (tmp_path / daemon.source.path).mkdir(parents=True, exist_ok=True)
    binary_receipt(svc, commits={**pins, lib: A}, probe="loraham_daemon 0.9.0")
    assert svc.binary_active_override("daemon") == {}                  # no acceptance at all
    assert svc.binary_behind(daemon) == ""
    assert "behind the manifest" not in svc.install_blocker(daemon)
    assert svc.binary_freshness("daemon")["behind"] == [lib]           # still shown as behind
    spawned = []
    monkeypatch.setattr(type(svc._lifecycle()), "start",
                        lambda self, *a, **k: spawned.append(a) or (_ for _ in ()).throw(
                            AssertionError("spawned")))
    try:
        svc.start("daemon", apply=True)
    except AssertionError:
        pass
    assert spawned                                                     # reached the spawn
    binary_receipt(svc, commits={**pins, daemon.id: A}, probe="loraham_daemon 0.9.0")
    assert svc.binary_behind(daemon).startswith(
        "installed binary artifact is behind the manifest (built from")


@pytest.mark.parametrize(("lagging", "blocks"), [("meshcom-bridge", True),
                                                 ("meshcom-firmware", False)])
def test_only_a_covered_service_blocks_the_start(short_tmp_path, monkeypatch, binary_receipt,
                                                 lagging, blocks):
    """`start meshcom` with one covered component off its pin: a lagging bridge (a service)
    refuses before any spawn, and QEMU's own gate names it too; a lagging firmware image no
    longer blocks (the maintainer's decision after 0.11.5): the bridge and QEMU both reach the
    spawn. Either lag still reads "behind" in the update status."""
    from lhpc.core import config as cfgmod
    from lhpc.core.lifecycle import BUILD_MARKER_TEXT, StartLaunch
    svc = _svc(short_tmp_path, monkeypatch)
    cfgmod.save_hardware_setup(svc._paths, "uputronics")
    svc._invalidate_config()
    assert svc.set_operator_identity(callsign="XX0XXA").ok
    pins = svc._binary_pins("meshcom")
    stack = svc.stack("meshcom")
    for c in stack.components:
        if c.source is not None:
            (short_tmp_path / c.source.path).mkdir(parents=True, exist_ok=True)
        if c.source is not None and c.build_marker:          # QEMU built from today's inputs
            marker = svc._lifecycle().source_dir(c) / c.build_marker
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.write_text(BUILD_MARKER_TEXT + svc._consumed_source_lines(c))
            svc.build_inputs_path(c).parent.mkdir(parents=True, exist_ok=True)
            svc.build_inputs_path(c).write_text(svc.build_inputs_text(c))
    binary_receipt(svc, "meshcom", commits={**pins, lagging: A})
    assert stack.component(lagging).kind.value == ("service" if blocks else "firmware")
    why = svc.binary_behind(stack.component("meshcom-qemu"))
    if blocks:
        assert why.startswith(f"installed binary artifact is behind the manifest ({lagging}: built from")
    else:
        assert why == ""
    assert svc.binary_freshness("meshcom")["behind"] == [lagging]      # still shown as behind
    # Everything outside the gate is stubbed: the daemon is ready, the host has the packages,
    # a launched bridge is ready; the spawn records the component and QEMU's launch "fails".
    monkeypatch.setattr(type(svc), "_ensure_daemon", lambda self, *a, **k: ([], True, ""))
    monkeypatch.setattr(type(svc), "start_blocking_requirements", lambda self, comp: [])
    monkeypatch.setattr(type(svc), "_ready_endpoints_present", lambda self, comp: (True, []))
    spawned = []
    monkeypatch.setattr(type(svc._lifecycle()), "start",
                        lambda self, stack, comp, *a, **k: spawned.append(comp.id) or StartLaunch(
                            comp.id != "meshcom-qemu", "", "stubbed spawn"))
    r = svc.start("meshcom", apply=True)
    res = {x.component: x for x in r.results}
    if blocks:
        assert spawned == []                                           # never reached the spawn
        assert res[lagging].outcome.value == "blocked"
        assert res[lagging].summary.startswith("installed binary artifact is behind the manifest")
    else:
        assert spawned == ["meshcom-bridge", "meshcom-qemu"]           # both reached the spawn
        assert res["meshcom-qemu"].summary.startswith("start failed: stubbed spawn")


def test_an_install_never_records_an_override(tmp_path, monkeypatch, stub_pipeline):
    """The receipt writer has no override any more: `build_receipt` is called without one."""
    svc = _svc(tmp_path, monkeypatch)
    spec = svc.binary_spec("daemon")
    files = sorted({*spec.proof_paths, *(next(iter(a)) for a in spec.probes)})
    stub_pipeline(svc, download=lambda e, d: None)          # the entry matches the pins
    monkeypatch.setattr(bi, "validate_and_extract", lambda tar, stage, roots: files)
    monkeypatch.setattr(bi, "publish", lambda *a, **k: None)
    monkeypatch.setattr(bi, "run_probe", lambda paths, argv: "ok")
    monkeypatch.setattr(ControllerService, "_binary_provision", lambda self, *a: [])
    seen = {}

    def _capture(*a, **k):
        seen.update(k)
        raise bi.BinaryInstallError("CAPTURED")
    monkeypatch.setattr(bi, "build_receipt", _capture)
    r = svc.binary_install("daemon", apply=True)
    assert not r.ok and r.data["rolled_back"] is True
    assert "override" not in seen


# ---- the receipt ------------------------------------------------------------------------------

def _override(cid, installed, pin):
    return {"at": 1_760_000_000, "lhpc_version": "0.11.3", "mismatch": {cid: [installed, pin]}}


def test_receipt_override_round_trips(tmp_path, monkeypatch, binary_receipt):
    svc = _svc(tmp_path, monkeypatch)
    cid = _daemon_cid(svc)
    pin = svc._binary_pins("daemon")[cid]
    rec = binary_receipt(svc, commits={**svc._binary_pins("daemon"), cid: A})
    assert brx.write_receipt(svc._paths, dataclasses.replace(rec, override=_override(cid, A, pin)))
    state, back, _ = brx.receipt_state(svc._paths, "daemon")
    assert state == "valid" and back.override == _override(cid, A, pin)
    assert brx.RECEIPT_VERSION == 1


@pytest.mark.parametrize("bad", [
    {"at": True, "lhpc_version": "x", "mismatch": {"CID": [A, B]}},
    {"at": 1, "lhpc_version": "x", "mismatch": {"CID": ["abc", B]}},
    {"at": 1, "lhpc_version": "x", "mismatch": {"CID": [A.upper(), B]}},
    {"at": 1, "lhpc_version": "x", "mismatch": {"not-a-component": [A, B]}},
    {"at": 1, "lhpc_version": "x", "mismatch": {}},
    {"at": 1, "lhpc_version": "x" * 65, "mismatch": {"CID": [A, B]}},
    "not a dict",
])
def test_malformed_override_keeps_receipt_valid_and_gate2_refuses(tmp_path, monkeypatch,
                                                                  binary_receipt, bad):
    svc = _svc(tmp_path, monkeypatch)
    cid = _daemon_cid(svc)
    rec = binary_receipt(svc, commits={**svc._binary_pins("daemon"), cid: A})
    import json
    p = brx.receipt_path(svc._paths, "daemon")
    d = json.loads(p.read_text())
    d["override"] = json.loads(json.dumps(bad).replace("CID", cid))
    p.write_text(json.dumps(d))
    svc.invalidate_snapshot()
    state, back, _ = brx.receipt_state(svc._paths, "daemon")
    assert state == "valid" and back.override is None
    comp = next(c for c in svc.stack("daemon").components if c.id == cid)
    assert "behind the manifest" in svc.binary_behind(comp)
    assert rec.stack == "daemon"


def test_an_older_reader_keeps_the_receipt_valid_and_refuses(tmp_path, monkeypatch, binary_receipt):
    """Rollback: an lhpc that does not know `override` validates named fields only, so the
    receipt stays valid; without the field its gate 2 refuses (the safe direction)."""
    svc = _svc(tmp_path, monkeypatch)
    cid = _daemon_cid(svc)
    pin = svc._binary_pins("daemon")[cid]
    rec = binary_receipt(svc, commits={**svc._binary_pins("daemon"), cid: A})
    assert brx.write_receipt(svc._paths, dataclasses.replace(rec, override=_override(cid, A, pin)))
    monkeypatch.setattr(brx, "_parse_override", lambda raw, comps: None)   # the old reader
    svc.invalidate_snapshot()
    assert brx.receipt_state(svc._paths, "daemon")[0] == "valid"
    comp = next(c for c in svc.stack("daemon").components if c.id == cid)
    assert "behind the manifest" in svc.binary_behind(comp)


# ---- gate 2 and the one helper ---------------------------------------------------------------

def test_active_override_staleness():
    rec = type("R", (), {"components": {"x": A}, "override": _override("x", A, B)})()
    assert brx.active_override(rec, {"x": B}) == {"x": [A, B]}         # exact pair: active
    assert brx.active_override(rec, {"x": A}) == {}                    # pin caught up
    assert brx.active_override(rec, {"x": C}) == {}                    # pin moved to a third value
    assert brx.active_override(rec, {"x": B}, clone_required=("x",)) == {}   # B1 at use


def test_gate2_allows_the_exact_recorded_pair_and_refuses_a_third_pin(tmp_path, monkeypatch,
                                                                     binary_receipt):
    svc = _svc(tmp_path, monkeypatch)
    cid = _daemon_cid(svc)
    pin = svc._binary_pins("daemon")[cid]
    rec = binary_receipt(svc, commits={**svc._binary_pins("daemon"), cid: A})
    assert brx.write_receipt(svc._paths, dataclasses.replace(rec, override=_override(cid, A, pin)))
    svc.invalidate_snapshot()
    comp = next(c for c in svc.stack("daemon").components if c.id == cid)
    assert svc.binary_behind(comp) == ""
    assert svc.binary_active_override("daemon") == {cid: [A, pin]}
    moved = {**svc._binary_pins("daemon"), cid: C}
    monkeypatch.setattr(ControllerService, "_binary_pins", lambda self, sid: moved)
    moved_comp = dataclasses.replace(comp, source=dataclasses.replace(comp.source, pin_commit=C))
    assert "behind the manifest" in svc.binary_behind(moved_comp)   # the pin moved on: refused
    assert svc.binary_active_override("daemon") == {}


def test_gate2_needs_no_override_once_the_pin_equals_the_installed_commit(tmp_path, monkeypatch,
                                                                         binary_receipt):
    svc = _svc(tmp_path, monkeypatch)
    rec = binary_receipt(svc)                              # installed == pins
    cid = _daemon_cid(svc)
    pin = svc._binary_pins("daemon")[cid]
    assert brx.write_receipt(svc._paths, dataclasses.replace(rec, override=_override(cid, A, pin)))
    svc.invalidate_snapshot()
    comp = next(c for c in svc.stack("daemon").components if c.id == cid)
    assert svc.binary_behind(comp) == ""
    assert svc.binary_active_override("daemon") == {}      # stale: not shown, not needed


def test_gate2_refuses_an_injected_exact_override_for_a_clone_required_component(
        tmp_path, monkeypatch, binary_receipt):
    """B1 at USE: a well-formed receipt override for meshcom-qemu whose pair matches exactly is
    not honoured — gate 2 refuses, and the helper shows nothing."""
    svc = _svc(tmp_path, monkeypatch)
    pin = svc._binary_pins("meshcom")["meshcom-qemu"]
    rec = binary_receipt(svc, "meshcom", commits={**svc._binary_pins("meshcom"), "meshcom-qemu": A})
    assert brx.write_receipt(svc._paths, dataclasses.replace(
        rec, override=_override("meshcom-qemu", A, pin)))
    svc.invalidate_snapshot()
    comp = next(c for c in svc.stack("meshcom").components if c.id == "meshcom-qemu")
    assert "behind the manifest" in svc.binary_behind(comp)
    assert svc.binary_active_override("meshcom") == {}


def test_override_is_inactive_once_its_component_becomes_clone_required(tmp_path, monkeypatch,
                                                                       binary_receipt):
    """An override recorded while the component was not clone_required; a later LHPC lists it."""
    svc = _svc(tmp_path, monkeypatch)
    cid = _daemon_cid(svc)
    pin = svc._binary_pins("daemon")[cid]
    rec = binary_receipt(svc, commits={**svc._binary_pins("daemon"), cid: A})
    assert brx.write_receipt(svc._paths, dataclasses.replace(rec, override=_override(cid, A, pin)))
    svc.invalidate_snapshot()
    comp = next(c for c in svc.stack("daemon").components if c.id == cid)
    assert svc.binary_behind(comp) == ""                   # honoured while not clone_required
    spec = svc.binary_spec("daemon")
    later = dataclasses.replace(spec, clone_required=(cid,))
    monkeypatch.setattr(ControllerService, "binary_spec",
                        lambda self, sid: later if sid == "daemon" else spec)
    assert "behind the manifest" in svc.binary_behind(comp)
    assert svc.binary_active_override("daemon") == {}


# ---- visible while it matters: status, doctor, boot restore (§5, D) ------------------------------

def _overridden(tmp_path, monkeypatch, binary_receipt, stack="daemon"):
    svc = _svc(tmp_path, monkeypatch)
    cid = next(iter(svc._binary_pins(stack)))
    pin = svc._binary_pins(stack)[cid]
    rec = binary_receipt(svc, stack, commits={**svc._binary_pins(stack), cid: A})
    assert brx.write_receipt(svc._paths, dataclasses.replace(rec, override=_override(cid, A, pin)))
    svc.invalidate_snapshot()
    return svc, cid, pin, rec


def test_status_and_doctor_show_the_override_while_it_is_active(tmp_path, monkeypatch, binary_receipt):
    svc, cid, pin, _rec = _overridden(tmp_path, monkeypatch, binary_receipt)
    status = "\n".join(svc.status("daemon").details)
    assert f"installed over the pin check: built from {A[:9]}, pins {pin[:9]}" in status
    doctor = svc.doctor()
    assert any("WARNING installed over the pin check" in d and cid in d for d in doctor.details)
    assert any(d.strip() == "| run yourself: lhpc self-update --apply" for d in doctor.details)
    assert doctor.ok                                      # a warning, not a failed doctor


def test_status_and_doctor_are_quiet_once_the_override_is_stale(tmp_path, monkeypatch, binary_receipt):
    """A normal install writes a receipt WITHOUT the override: it is cleared."""
    svc, _cid, _pin, rec = _overridden(tmp_path, monkeypatch, binary_receipt)
    assert brx.write_receipt(svc._paths, dataclasses.replace(rec, override=None))
    svc.invalidate_snapshot()
    assert "installed over the pin check" not in "\n".join(svc.status("daemon").details)
    assert not any("pin check" in d for d in svc.doctor().details)


def _daemon_reconcile_item():
    """The item a real boot builds for the daemon: `derive_plan` turns its evidence into ONE
    `daemon-reconcile` item with no target."""
    from lhpc.core import boot_restore as br
    meta = br.StackMeta(stack_id="daemon", main="loraham-daemon", interactive_main=False,
                        declared_bands=(), fixed_band="")
    ev = br.Evidence(launch_id="D1", stack="daemon", component="loraham-daemon", band="433",
                     launched_at=1000.0)
    [item] = br.derive_plan([ev], {"daemon": meta}, {}, "daemon").items
    assert item["kind"] == "daemon-reconcile" and item["target"] == ""
    item["state"] = "attempting"
    return item


def _meshtastic_stack_item():
    """The item a real boot builds for a restored stack: `derive_plan`'s "stack" item."""
    from lhpc.core import boot_restore as br
    meta = br.StackMeta(stack_id="meshtastic", main="meshtastic", interactive_main=False,
                        declared_bands=(), fixed_band="868")
    ev = br.Evidence(launch_id="M1", stack="meshtastic", component="meshtastic", band="868",
                     launched_at=1000.0, start_scope="stack", requested_target="meshtastic")
    [item] = br.derive_plan([ev], {"meshtastic": meta}, {}, "daemon").items
    assert item["kind"] == "stack" and item["target"] == "meshtastic"
    item["state"] = "attempting"
    return item


@pytest.mark.parametrize("stack,make_item", [("daemon", _daemon_reconcile_item),
                                             ("meshtastic", _meshtastic_stack_item)])
def test_boot_restore_success_records_active_binary_override_note(tmp_path, monkeypatch,
                                                                  binary_receipt, stack, make_item):
    from lhpc.core import boot_restore
    svc, cid, pin, _rec = _overridden(tmp_path, monkeypatch, binary_receipt, stack)
    item = make_item()
    journal = boot_restore.new_journal(boot_id="b", pid=1, process_start_time=1, items=[item])
    monkeypatch.setattr(type(svc), "_boot_start_ok", lambda res: True)   # a staticmethod
    monkeypatch.setattr(type(svc), "_boot_prune_evidence", lambda self, ids: None)
    svc._boot_settle_item(journal, item, type("R", (), {"summary": "started", "ok": True})())
    assert item["result"]["override"] == {cid: [A, pin]}


def test_boot_restore_success_without_an_override_has_no_note(tmp_path, monkeypatch, binary_receipt):
    from lhpc.core import boot_restore
    svc = _svc(tmp_path, monkeypatch)
    binary_receipt(svc)
    item = _daemon_reconcile_item()
    journal = boot_restore.new_journal(boot_id="b", pid=1, process_start_time=1, items=[item])
    monkeypatch.setattr(type(svc), "_boot_start_ok", lambda res: True)   # a staticmethod
    monkeypatch.setattr(type(svc), "_boot_prune_evidence", lambda self, ids: None)
    svc._boot_settle_item(journal, item, type("R", (), {"summary": "started", "ok": True})())
    assert "override" not in item["result"]



def test_gate2_honours_an_override_only_on_a_receipt_it_read_valid(tmp_path, monkeypatch,
                                                                   binary_receipt):
    """`binary_behind` reads the receipt twice (the coverage check, then the commits). If the
    second read is not valid, an exact override is not honoured: gate 2 refuses the mismatch."""
    svc, cid, _pin, rec = _overridden(tmp_path, monkeypatch, binary_receipt)
    rec = brx.receipt_state(svc._paths, "daemon")[1]             # the override, as read
    comp = next(c for c in svc.stack("daemon").components if c.id == cid)
    assert svc.binary_behind(comp) == ""                          # valid: honoured
    monkeypatch.setattr(ControllerService, "binary_receipt_state",
                        lambda self, sid: ("unsafe", rec, "changed between the two reads"))
    monkeypatch.setattr(ControllerService, "on_binary_channel", lambda self, sid: True)
    assert "behind the manifest" in svc.binary_behind(comp)
