"""A3: `--accept-pin-mismatch`, the override of the binary pin check (plan rev 9).

The operator accepts ONE exact mismatch, once, on a manual install or update; the consent is
bound to the artifact and the pairs that were shown; the receipt records it; gate 2 honours it
only while it is exact and the component is not clone_required now (B1); a malformed commit is
refused before anything is offered (B2). A lag in MeshCom's `meshcom-qemu` (clone_required)
cannot be accepted."""
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


# ---- gate 1: the one map, the refusal, the offer ------------------------------------------------

def test_check_pins_raises_the_structured_mismatch():
    entry = type("E", (), {})()
    entry.components = {"x": A, "y": B}
    with pytest.raises(bi.BinaryPinMismatch) as ei:
        bi.check_pins(entry, {"x": C, "y": B})
    assert ei.value.mismatch == {"x": (A, C)}          # only the lagging pair, (binary, pin)


@pytest.mark.parametrize("apply", [False, True])
def test_refusal_offers_three_ways_in_order_with_the_map(tmp_path, monkeypatch, stub_pipeline, apply):
    svc = _svc(tmp_path, monkeypatch)
    cid = _daemon_cid(svc)
    monkeypatch.setattr(bi, "index_entry", _lagging_entry(svc, "daemon", cid, A, stub_pipeline,
                                                          download=_never_download))
    r = svc.binary_install("daemon", apply=apply)
    assert not r.ok
    assert [d.strip()[:2] for d in r.details[:3]] == ["1.", "2.", "3."]
    cmd = "lhpc install daemon --source binary --accept-pin-mismatch --yes"
    assert r.next_commands == ["lhpc self-update --apply",
                               "lhpc install daemon --source pinned --yes", cmd]
    assert "may not work with this LHPC" in r.details[2]
    assert r.data["pin_mismatch"] == {cid: [A, svc._binary_pins("daemon")[cid]]}
    assert r.data["override_command"] == cmd


def test_override_command_only_when_overridable(tmp_path, monkeypatch, stub_pipeline):
    """A daemon lag's flag-less refusal carries option (3)'s command; MeshCom's lagging
    `meshcom-qemu` is clone_required: its refusal keeps P1.15's two ways and names no flag."""
    svc = _svc(tmp_path, monkeypatch)
    monkeypatch.setattr(bi, "index_entry", _lagging_entry(svc, "daemon", _daemon_cid(svc), A,
                                                          stub_pipeline, download=_never_download))
    assert svc.binary_install("daemon", apply=False).data["override_command"] == \
        "lhpc install daemon --source binary --accept-pin-mismatch --yes"
    monkeypatch.setattr(bi, "index_entry", _lagging_entry(svc, "meshcom", "meshcom-qemu", A,
                                                          stub_pipeline, download=_never_download))
    r = svc.binary_install("meshcom", apply=False)
    assert not r.ok and "override_command" not in r.data and "consent" not in r.data
    assert r.next_commands == ["lhpc self-update --apply",
                               "lhpc install meshcom --source pinned --yes"]
    assert r.details[0].strip().startswith("1. Update LHPC first")
    assert not any("--accept-pin-mismatch" in x for x in [*r.details, *r.next_commands])


def test_the_manifest_clone_required_list_is_meshcom_qemu_only(tmp_path, monkeypatch):
    """The B1 rule reads the spec, not a name; a new clone_required entry shows up here."""
    svc = _svc(tmp_path, monkeypatch)
    listed = sorted(c for st in svc.stacks() if svc.binary_spec(st.id)
                    for c in svc.binary_spec(st.id).clone_required)
    assert listed == ["meshcom-qemu"]


@pytest.mark.parametrize("op", ["install", "update"])
def test_override_refused_for_a_lagging_clone_required_component(tmp_path, monkeypatch,
                                                                 stub_pipeline, op):
    svc = _svc(tmp_path, monkeypatch)
    monkeypatch.setattr(bi, "index_entry", _lagging_entry(svc, "meshcom", "meshcom-qemu", A,
                                                          stub_pipeline, download=_never_download))
    call = (svc.install if op == "install" else svc.update)
    for apply in (False, True):
        r = call("meshcom", apply=apply, source="binary", accept_pin_mismatch="yes")
        assert not r.ok and r.data["override_refused"] == "clone_required"
        assert "refused even with --accept-pin-mismatch" in r.summary
        assert r.details[0].strip().startswith("1. Update LHPC first")
        assert r.next_commands == ["lhpc self-update --apply",
                                   "lhpc install meshcom --source pinned --yes"]
    assert not (tmp_path / "state" / "binary" / "meshcom.json").exists()


def test_a_stack_whose_lag_is_outside_clone_required_stays_overridable(tmp_path, monkeypatch,
                                                                      stub_pipeline):
    svc = _svc(tmp_path, monkeypatch)
    other = next(c for c in svc._binary_pins("meshcom") if c != "meshcom-qemu")
    monkeypatch.setattr(bi, "index_entry", _lagging_entry(svc, "meshcom", other, A,
                                                          stub_pipeline, download=_never_download))
    r = svc.binary_install("meshcom", apply=False, accept_pin_mismatch="yes")
    assert r.ok and r.data["consent"]


# ---- B2: a malformed commit is refused, not reported as a mismatch -------------------------------

@pytest.mark.parametrize("bad", ["abc123", "A" * 40, "g" * 40])
@pytest.mark.parametrize("side", ["binary", "pin"])
@pytest.mark.parametrize("flag", ["", "yes"])
@pytest.mark.parametrize("op", ["install", "update"])
def test_malformed_commit_refused_before_download_even_with_the_flag(tmp_path, monkeypatch,
                                                                     stub_pipeline, bad, side,
                                                                     flag, op):
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
    for apply in (False, True):
        r = call("daemon", apply=apply, source="binary", accept_pin_mismatch=flag)
        assert not r.ok and ("malformed" in r.summary or "not a full commit id" in r.summary)
        assert "pin_mismatch" not in r.data and "override_command" not in r.data
        assert "consent" not in r.data
    assert not (tmp_path / "state" / "binary").exists()


# ---- the consent token (§2b) ------------------------------------------------------------------

def test_consent_token_is_order_independent():
    one = {"x": (A, B), "y": (B, C)}
    two = {"y": [B, C], "x": [A, B]}
    assert bi.consent_token("f" * 64, one) == bi.consent_token("f" * 64, two)
    assert bi.consent_token("f" * 64, one) != bi.consent_token("e" * 64, one)


def test_override_consent_token_matches_same_entry(tmp_path, monkeypatch, stub_pipeline):
    """The dry run's token equals the one the apply recomputes from the SAME entry: the apply
    passes the gate and reaches the download."""
    svc = _svc(tmp_path, monkeypatch)
    cid = _daemon_cid(svc)
    monkeypatch.setattr(bi, "index_entry", _lagging_entry(svc, "daemon", cid, A, stub_pipeline))
    plan = svc.binary_install("daemon", apply=False, accept_pin_mismatch="yes")
    assert plan.ok and plan.data["changes"] == 1
    assert plan.data["pin_mismatch"] == {cid: [A, svc._binary_pins("daemon")[cid]]}
    assert any("OVER THE PIN CHECK" in d for d in plan.details)
    r = svc.binary_install("daemon", apply=True, accept_pin_mismatch=plan.data["consent"])
    assert not r.ok and "DOWNLOAD-REACHED" in r.summary


@pytest.mark.parametrize("op", ["install", "update"])
def test_override_refused_when_index_moved_between_consent_and_start(tmp_path, monkeypatch,
                                                                     stub_pipeline, op):
    """Consent for artifact A; the index now serves artifact B: refused before any download or
    write, typed `consent_stale`, with the NEW map."""
    svc = _svc(tmp_path, monkeypatch)
    cid = _daemon_cid(svc)
    monkeypatch.setattr(bi, "index_entry", _lagging_entry(svc, "daemon", cid, A, stub_pipeline))
    token = svc.binary_install("daemon", apply=False, accept_pin_mismatch="yes").data["consent"]
    monkeypatch.setattr(bi, "index_entry", _lagging_entry(svc, "daemon", cid, B, stub_pipeline,
                                                          sha="b" * 64,
                                                          download=_never_download))
    call = (svc.install if op == "install" else svc.update)
    r = call("daemon", apply=True, source="binary", accept_pin_mismatch=token)
    assert not r.ok and r.data["override_refused"] == "consent_stale"
    assert r.data["pin_mismatch"] == {cid: [B, svc._binary_pins("daemon")[cid]]}
    assert "review the install again" in r.summary
    assert not (tmp_path / "state" / "binary").exists()


@pytest.mark.parametrize("op", ["install", "update"])
def test_the_flagged_plan_names_the_command_that_repeats_the_acceptance(tmp_path, monkeypatch,
                                                                         stub_pipeline, op):
    """Found in release 3's box row: the dry run WITH the flag named `lhpc install <stack> --yes`,
    which is refused again. It now names option 3's command; an unflagged plan keeps its line."""
    svc = _svc(tmp_path, monkeypatch)
    call = (svc.install if op == "install" else svc.update)
    stub_pipeline(svc, download=_never_download)            # the entry matches the pins
    plain = call("daemon", apply=False, source="binary")
    assert plain.ok and plain.next_commands == ["lhpc install daemon --yes"]
    monkeypatch.setattr(bi, "index_entry", _lagging_entry(svc, "daemon", _daemon_cid(svc), A,
                                                          stub_pipeline, download=_never_download))
    plan = call("daemon", apply=False, source="binary", accept_pin_mismatch="yes")
    assert plan.ok and plan.next_commands == [
        "lhpc install daemon --source binary --accept-pin-mismatch --yes"]


@pytest.mark.parametrize("op", ["install", "update"])
def test_a_consent_token_is_refused_when_the_mismatch_has_gone(tmp_path, monkeypatch,
                                                             stub_pipeline, op):
    """Gate 2's P2: consent for mismatching artifact A; the index now serves artifact B, which
    matches the pins. A's token matches nothing now: refused `consent_stale` with an empty map,
    before any download or write (B was downloaded before)."""
    svc = _svc(tmp_path, monkeypatch)
    cid = _daemon_cid(svc)
    monkeypatch.setattr(bi, "index_entry", _lagging_entry(svc, "daemon", cid, A, stub_pipeline))
    token = svc.binary_install("daemon", apply=False, accept_pin_mismatch="yes").data["consent"]
    stub_pipeline(svc, download=_never_download)                  # B: the entry matches the pins
    call = (svc.install if op == "install" else svc.update)
    r = call("daemon", apply=True, source="binary", accept_pin_mismatch=token)
    assert not r.ok and r.data["override_refused"] == "consent_stale"
    assert r.data["pin_mismatch"] == {}
    assert "review the install again" in r.summary
    assert "The published binary or LHPC's pins changed since you confirmed" in r.summary
    assert r.next_commands == ["lhpc install daemon --source binary --yes"]
    assert not (tmp_path / "state" / "binary").exists()
    # the bare flag (a human's dry run) with nothing to accept stays an ordinary plan
    assert call("daemon", apply=False, source="binary", accept_pin_mismatch="yes").ok


def test_a_stale_acceptance_of_a_covered_library_blocks_the_start(tmp_path, monkeypatch,
                                                                  binary_receipt):
    """Gate 2's P1: the daemon's artifact also covers RadioLib, which is never started itself.
    Gate 2 judges EVERY covered component: a lagging library blocks the start unless its exact
    pair is accepted and still in force; once its pin moves on, the start refuses before the
    spawn."""
    from lhpc.core import config as cfgmod
    svc = _svc(tmp_path, monkeypatch)
    cfgmod.save_hardware_setup(svc._paths, "uputronics")
    svc._invalidate_config()
    pins = svc._binary_pins("daemon")
    lib = "radiolib"
    daemon = svc.stack("daemon").component("loraham-daemon")
    (tmp_path / daemon.source.path).mkdir(parents=True, exist_ok=True)
    rec = binary_receipt(svc, commits={**pins, lib: A}, probe="loraham_daemon 0.9.0")
    assert "(radiolib: built from" in svc.binary_behind(daemon)       # no acceptance: refused
    assert brx.write_receipt(svc._paths, dataclasses.replace(rec, override=_override(lib, A, pins[lib])))
    svc.invalidate_snapshot()
    assert svc.binary_behind(daemon) == ""                             # the exact pair: allowed
    moved = {**pins, lib: C}
    monkeypatch.setattr(ControllerService, "_binary_pins", lambda self, sid: moved)
    assert svc.binary_active_override("daemon") == {}
    why = svc.binary_behind(daemon)
    assert why.startswith("installed binary artifact is behind the manifest (radiolib: built from")
    spawned = []
    monkeypatch.setattr(type(svc._lifecycle()), "start",
                        lambda self, *a, **k: spawned.append(a) or (_ for _ in ()).throw(
                            AssertionError("spawned")))
    r = svc.start("daemon", apply=True)
    assert not r.ok and any(why in str(d) for d in r.details), (r.summary, r.details)
    assert spawned == []                                               # never reached the spawn


def test_a_bare_flag_does_not_apply_without_the_dry_runs_token(tmp_path, monkeypatch, stub_pipeline):
    svc = _svc(tmp_path, monkeypatch)
    monkeypatch.setattr(bi, "index_entry", _lagging_entry(svc, "daemon", _daemon_cid(svc), A,
                                                          stub_pipeline,
                                                          download=_never_download))
    r = svc.binary_install("daemon", apply=True, accept_pin_mismatch="yes")
    assert not r.ok and r.data["override_refused"] == "consent_stale"


# ---- the flag lifts ONLY the commit comparison ----------------------------------------------------

def test_the_flag_does_not_lift_the_runtime_deps(tmp_path, monkeypatch, stub_pipeline):
    svc = _svc(tmp_path, monkeypatch)
    monkeypatch.setattr(bi, "index_entry", _lagging_entry(svc, "daemon", _daemon_cid(svc), A,
                                                          stub_pipeline,
                                                          download=_never_download))
    monkeypatch.setattr(bi, "missing_runtime_deps", lambda e, f: ["libfoo1"])
    r = svc.binary_install("daemon", apply=False, accept_pin_mismatch="yes")
    assert not r.ok and r.data.get("missing_runtime_deps") == ["libfoo1"]


def test_the_flag_does_not_lift_the_target_or_the_smoke_record(tmp_path, monkeypatch, stub_pipeline):
    svc = _svc(tmp_path, monkeypatch)
    stub_pipeline(svc, download=_never_download)
    monkeypatch.setattr(bi, "check_target", lambda e, t: (_ for _ in ()).throw(
        bi.BinaryInstallError("built for another target")))
    r = svc.binary_install("daemon", apply=False, accept_pin_mismatch="yes")
    assert not r.ok and "another target" in r.summary and "consent" not in r.data
    monkeypatch.setattr(bi, "index_entry", lambda idx, sid: (_ for _ in ()).throw(
        bi.BinaryInstallError("did not pass a mandatory smoke test")))
    r = svc.binary_install("daemon", apply=False, accept_pin_mismatch="yes")
    assert not r.ok and "smoke" in r.summary


def test_the_apply_records_the_accepted_pairs_in_the_receipt(tmp_path, monkeypatch, stub_pipeline):
    """The override reaches `build_receipt` with the accepted pairs, the time and the version."""
    svc = _svc(tmp_path, monkeypatch)
    cid = _daemon_cid(svc)
    spec = svc.binary_spec("daemon")
    files = sorted({*spec.proof_paths, *(next(iter(a)) for a in spec.probes)})
    monkeypatch.setattr(bi, "index_entry", _lagging_entry(svc, "daemon", cid, A, stub_pipeline,
                                                          download=lambda e, d: None))
    monkeypatch.setattr(bi, "validate_and_extract", lambda tar, stage, roots: files)
    monkeypatch.setattr(bi, "publish", lambda *a, **k: None)
    monkeypatch.setattr(bi, "run_probe", lambda paths, argv: "ok")
    monkeypatch.setattr(ControllerService, "_binary_provision", lambda self, *a: [])
    seen = {}

    def _capture(*a, **k):
        seen.update(k)
        raise bi.BinaryInstallError("CAPTURED")
    monkeypatch.setattr(bi, "build_receipt", _capture)
    token = svc.binary_install("daemon", apply=False, accept_pin_mismatch="yes").data["consent"]
    r = svc.binary_install("daemon", apply=True, accept_pin_mismatch=token)
    assert "CAPTURED" in r.summary
    ov = seen["override"]
    assert ov["mismatch"] == {cid: [A, svc._binary_pins("daemon")[cid]]}
    assert isinstance(ov["at"], int) and ov["lhpc_version"]


def test_without_the_flag_no_override_is_recorded(tmp_path, monkeypatch, stub_pipeline):
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
    svc.binary_install("daemon", apply=True)
    assert seen["override"] is None


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
    monkeypatch.setattr(type(svc), "_boot_start_ok", lambda self, res: True)
    monkeypatch.setattr(type(svc), "_boot_prune_evidence", lambda self, ids: None)
    svc._boot_settle_item(journal, item, type("R", (), {"summary": "started", "ok": True})())
    assert item["result"]["override"] == {cid: [A, pin]}


def test_boot_restore_success_without_an_override_has_no_note(tmp_path, monkeypatch, binary_receipt):
    from lhpc.core import boot_restore
    svc = _svc(tmp_path, monkeypatch)
    binary_receipt(svc)
    item = _daemon_reconcile_item()
    journal = boot_restore.new_journal(boot_id="b", pid=1, process_start_time=1, items=[item])
    monkeypatch.setattr(type(svc), "_boot_start_ok", lambda self, res: True)
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
