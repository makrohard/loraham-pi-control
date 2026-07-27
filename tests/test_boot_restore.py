"""Boot auto-restore, step 1: boot-aware, collision-safe ownership (lifecycle layer).

Covers the audit-mandated lifecycle cases: nonce'd launch ids (pid reuse can neither overwrite
nor mis-prune records), the reserved v1 field set constructed last, boot-id-aware cessation and
verification (foreign boot with an identical numeric starttime; unreadable current boot id stays
conservative), compare-before-delete pruning, scope threading, and the single schema-versioned
ownership inventory (v0 legacy + v1, integrity diagnostics, directory states).
"""

import json
import subprocess

import pytest

from lhpc.core import lifecycle as lifecycle_mod
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService

pytestmark = pytest.mark.needs_session


def _svc(tmp_path):
    return ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))


def _life(tmp_path):
    return _svc(tmp_path)._lifecycle()


def _spawn():
    p = subprocess.Popen(["sleep", "60"], start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return p


def _stack_comp(svc, stack_id="kiss"):
    st = next(s for s in svc.stacks() if s.id == stack_id)
    comp = next(c for c in st.components if c.id == st.main)
    return st, comp


def _write_record(tmp_path, rec, name=None):
    d = tmp_path / "state" / "owned"
    d.mkdir(parents=True, exist_ok=True)
    name = name or f"{rec['launch_id']}.json"
    (d / name).write_text(json.dumps(rec))
    return d / name


def _v0(pid=999999, starttime="123", **over):
    rec = {"launch_id": f"c__x__{pid}", "stack": "kiss", "component": "loraham-kiss-tnc",
           "band": "", "pid": pid, "role": "", "launched_at": 1000,
           "starttime": starttime, "pgid": pid, "sid": pid}
    rec.update(over)
    return rec


# --- record_launch: nonce, reserved fields, scope, boot id --------------------------------------

def test_record_launch_writes_v1_reserved_fields_and_nonce(tmp_path):
    svc = _svc(tmp_path)
    life = svc._lifecycle()
    st, comp = _stack_comp(svc)
    proc = _spawn()
    try:
        assert life.record_launch(st, comp, proc.pid, band="433",
                                  requested_target="kiss", start_scope="stack",
                                  extra={"stack": "EVIL", "boot_id": "EVIL",
                                         "launch_id": "EVIL"})
        recs = life.owned_records(comp.id, role="")
        assert len(recs) == 1
        rec = recs[0]
        # reserved fields constructed LAST: the malicious extra could not override them
        assert rec["version"] == 1 and rec["stack"] == st.id
        assert rec["requested_target"] == "kiss" and rec["start_scope"] == "stack"
        assert rec["boot_id"] == lifecycle_mod.current_boot_id() != "EVIL"
        # nonce'd id: deterministic prefix + 32 hex chars
        prefix = f"{comp.id}__433__{proc.pid}__"
        assert rec["launch_id"].startswith(prefix)
        assert len(rec["launch_id"]) == len(prefix) + 32
    finally:
        proc.kill(); proc.wait()


def test_same_component_band_pid_relaunch_never_overwrites(tmp_path):
    # The old deterministic filename made a pid-reuse relaunch OVERWRITE the prior record;
    # with the nonce both records coexist.
    svc = _svc(tmp_path)
    life = svc._lifecycle()
    st, comp = _stack_comp(svc)
    proc = _spawn()
    try:
        assert life.record_launch(st, comp, proc.pid, band="433")
        assert life.record_launch(st, comp, proc.pid, band="433")
        assert len(life.owned_records(comp.id, role="")) == 2
    finally:
        proc.kill(); proc.wait()


# --- compare-before-delete ----------------------------------------------------------------------

def test_remove_record_leaves_a_replaced_record(tmp_path):
    life = _life(tmp_path)
    old = _v0()
    path = _write_record(tmp_path, old)
    # the same path now holds a DIFFERENT (newer) record
    newer = dict(old, launch_id="c__x__other")
    path.write_text(json.dumps(newer))
    stale_view = dict(old, _path=str(path))
    assert life._remove_record(stale_view) is False      # refused: not the expected record
    assert path.exists()


def test_remove_record_removes_the_exact_record(tmp_path):
    life = _life(tmp_path)
    rec = _v0()
    path = _write_record(tmp_path, rec)
    assert life._remove_record(dict(rec, _path=str(path))) is True
    assert not path.exists()


# --- boot-id rules ------------------------------------------------------------------------------

def test_foreign_boot_identical_starttime_is_ceased(tmp_path, monkeypatch):
    # A live pid with a coincidentally identical numeric starttime, but a FOREIGN boot id,
    # must be provably ceased (it cannot have survived the reboot).
    life = _life(tmp_path)
    proc = _spawn()
    try:
        ident = life._proc_identity(proc.pid)
        rec = _v0(pid=proc.pid, starttime=str(ident["starttime"]),
                  boot_id="00000000-dead-beef-0000-000000000000")
        monkeypatch.setattr(lifecycle_mod, "current_boot_id", lambda: "11111111-live-0000-0000-000000000000")
        assert life._original_ceased(rec) is True
        ok, why = life.verify_owned(rec)
        assert ok is False and "previous boot" in why
    finally:
        proc.kill(); proc.wait()


def test_verify_owned_rejects_foreign_boot(tmp_path, monkeypatch):
    life = _life(tmp_path)
    proc = _spawn()
    try:
        ident = life._proc_identity(proc.pid)
        rec = _v0(pid=proc.pid, starttime=str(ident["starttime"]),
                  pgid=ident["pgid"], sid=ident["sid"], boot_id="aaaa")
        monkeypatch.setattr(lifecycle_mod, "current_boot_id", lambda: "bbbb")
        ok, why = life.verify_owned(rec)
        assert ok is False and "previous boot" in why
    finally:
        proc.kill(); proc.wait()


def test_unreadable_boot_id_preserves_ownership(tmp_path, monkeypatch):
    # An unreadable CURRENT boot id must never disown a possibly-live process: with the reader
    # returning "", the foreign-boot rule cannot fire and ordinary identity verification rules.
    life = _life(tmp_path)
    proc = _spawn()
    try:
        ident = life._proc_identity(proc.pid)
        rec = _v0(pid=proc.pid, starttime=str(ident["starttime"]),
                  pgid=ident["pgid"], sid=ident["sid"], boot_id="aaaa")
        monkeypatch.setattr(lifecycle_mod, "current_boot_id", lambda: "")
        ok, _why = life.verify_owned(rec)
        assert ok is True                                # still owned — nothing proven foreign
        assert life._original_ceased(rec) is False
    finally:
        proc.kill(); proc.wait()


def test_legacy_record_without_boot_id_keeps_stale_detection(tmp_path):
    life = _life(tmp_path)
    rec = _v0(pid=999999)                                # dead pid, no boot_id field
    assert life._original_ceased(rec) is True            # proven gone via /proc as before


# --- inventory: schema versions, diagnostics, directory states ----------------------------------

def test_inventory_missing_dir_is_valid_empty(tmp_path):
    valid, issues, state = _life(tmp_path).owned_inventory()
    assert (valid, issues, state) == ([], [], "missing")


def test_inventory_v0_and_v1_records_valid(tmp_path):
    life = _life(tmp_path)
    _write_record(tmp_path, _v0())
    v1 = _v0(pid=4242, version=1, requested_target="kiss", start_scope="stack", boot_id="b")
    v1["launch_id"] = "c__x__4242__deadbeef"
    _write_record(tmp_path, v1)
    valid, issues, state = life.owned_inventory()
    assert state == "ok" and not issues and len(valid) == 2
    assert all("_path" in r for r in valid)


@pytest.mark.parametrize("mutate,reason", [
    (lambda r: r.update(version=99), "unknown record version"),
    (lambda r: r.pop("pid"), "missing field"),
    (lambda r: r.update(pid="12"), "invalid pid"),
    (lambda r: r.update(version=1), "v1 field"),         # v1 without the reserved extras
])
def test_inventory_flags_malformed_records(tmp_path, mutate, reason):
    life = _life(tmp_path)
    bad = _v0()
    mutate(bad)
    _write_record(tmp_path, bad, name=f"{bad.get('launch_id', 'x')}.json")
    good = _v0(pid=777)
    good["launch_id"] = "c__x__777"
    _write_record(tmp_path, good)
    valid, issues, state = life.owned_inventory()
    assert state == "ok"
    assert len(valid) == 1 and valid[0]["pid"] == 777    # unrelated valid record survives
    assert len(issues) == 1 and reason in issues[0]["reason"]


def test_inventory_filename_mismatch_is_integrity_issue(tmp_path):
    life = _life(tmp_path)
    rec = _v0()
    _write_record(tmp_path, rec, name="not-the-launch-id.json")
    valid, issues, _state = life.owned_inventory()
    assert not valid and issues and "filename" in issues[0]["reason"]


def test_owned_records_filters_the_shared_inventory(tmp_path):
    life = _life(tmp_path)
    _write_record(tmp_path, _v0())                       # kiss tnc, band ""
    other = _v0(pid=555)
    other.update(launch_id="d__433__555", component="loraham-igate", band="433")
    _write_record(tmp_path, other)
    recs = life.owned_records("loraham-kiss-tnc", role="")
    assert len(recs) == 1 and recs[0]["component"] == "loraham-kiss-tnc"
    assert life.owned_records("loraham-igate", band="868", role="") == []
    assert len(life.owned_records("loraham-igate", band="433", role="")) == 1


# --- scope threading through a real service start ----------------------------------------------

def test_direct_component_start_records_component_scope(tmp_path, monkeypatch):
    # The SAME public operation stamps its original target/scope on every launch it causes;
    # a direct main-component start is scope "component", a stack start scope "stack".
    reply = b"STATUS RADIO=READY TX=0 TXMODE=MANAGED CADWAIT=1500 CADRSSI=-90\n"
    fake = FakeSystem(unix_replies={"/tmp/loraconf433.sock": reply})   # daemon already serving
    (tmp_path / "src" / "loraham-kiss-tnc").mkdir(parents=True)       # kiss installed
    svc = ControllerService(system=fake.system, paths=Paths(runtime_root=tmp_path))
    from conftest import set_call
    set_call(svc)
    captured = {}
    monkeypatch.setattr(ControllerService, "is_built", lambda self, comp: True)
    monkeypatch.setattr(lifecycle_mod.Lifecycle, "missing_requirements",
                        lambda self, comp: [])
    monkeypatch.setattr(
        lifecycle_mod.Lifecycle, "start",
        lambda self, stack, comp, params=None, band="", *, requested_target="",
               start_scope="":
        (captured.setdefault(comp.id, []).append((requested_target, start_scope)) or
         lifecycle_mod.StartLaunch(True, "")))
    svc.start("loraham-kiss-tnc", apply=True)
    assert captured.get("loraham-kiss-tnc") == [("loraham-kiss-tnc", "component")]
    captured.clear()
    svc.start("kiss", apply=True)
    assert captured.get("loraham-kiss-tnc") == [("kiss", "stack")]


# ================================================================================================
# Steps 2-8: journal, config, planner, driver, CLI, banner, web
# ================================================================================================

from lhpc.core import boot_restore as br  # noqa: E402
from lhpc.core import service_boot_restore as sbr  # noqa: E402
from lhpc.core.boot_restore import Evidence, MarkerView, StackMeta  # noqa: E402
from lhpc.core.service_base import ActionResult  # noqa: E402


def _v1(pid=999999, stack="kiss", comp="loraham-kiss-tnc", band="433", boot="OLDBOOT",
        nonce="ab" * 16, **over):
    rec = {"launch_id": f"{comp}__{band or 'x'}__{pid}__{nonce}", "stack": stack,
           "component": comp, "band": band, "pid": pid, "role": "", "launched_at": 1000,
           "version": 1, "requested_target": stack, "start_scope": "stack", "boot_id": boot,
           "starttime": "123", "pgid": pid, "sid": pid}
    rec.update(over)
    return rec


def _drv(tmp_path, monkeypatch, *, cur_boot="CURBOOT", enabled=True, web=(True, "")):
    """A driver-ready service: boot id + config switch + web gate under test control."""
    svc = _svc(tmp_path)
    monkeypatch.setattr(sbr, "current_boot_id", lambda: cur_boot)
    monkeypatch.setattr(ControllerService, "_web_integration_proven", lambda self: web)
    monkeypatch.setattr(ControllerService, "boot_restore_enabled",
                        lambda self: (enabled, "" if enabled else "disabled by [boot] restore"))
    return svc


def _stub_start(record, *, ok=True, call_hook=True, summary="started"):
    def stub(self, target, apply=False, params=None, stop_owners=False, band="",
             daemon_overrides=None, file_overrides=None, auto_install_ctx=None, *,
             _before_start_locked=None):
        record.append({"target": target, "params": params, "band": band})
        if call_hook and _before_start_locked is not None:
            refusal = _before_start_locked()
            if refusal is not None:
                return refusal
        return ActionResult(ok, summary)
    return stub


def _journal_on_disk(tmp_path):
    p = tmp_path / "state" / "boot-restore.json"
    return json.loads(p.read_text()) if p.exists() else None


# --- journal module -----------------------------------------------------------------------------

def test_journal_round_trip(tmp_path):
    svc = _svc(tmp_path)
    item = br.new_item("i1", "stack", target="kiss", band="433", evidence_ids=("a",))
    j = br.new_journal(boot_id="B", pid=42, process_start_time=7, items=[item])
    assert br.write_journal(svc._paths, j) is True
    loaded, state = br.load_journal(svc._paths)
    assert state == "valid" and loaded == j


def test_journal_absent(tmp_path):
    assert br.load_journal(_svc(tmp_path)._paths) == (None, "absent")


@pytest.mark.parametrize("mutate,why", [
    (lambda j: j.update(version=2), "unknown journal version"),
    (lambda j: j.update(run_id=""), "run_id missing"),
    (lambda j: j.update(pid=True), "pid invalid"),
    (lambda j: j.update(state="odd"), "unknown run state"),
    (lambda j: j["items"].append(dict(j["items"][0])), "duplicate item id"),
    (lambda j: j["items"][0].update(kind="x"), "unknown item kind"),
    (lambda j: j["items"][0].update(state="x"), "unknown item state"),
    (lambda j: j["items"][0].update(bands=["999"]), "item bands invalid"),
    (lambda j: j["items"][0].update(evidence_ids=[""]), "item evidence_ids invalid"),
])
def test_journal_validation_rejects(mutate, why):
    j = br.new_journal(boot_id="B", pid=42, process_start_time=7,
                       items=[br.new_item("i1", "stack", target="kiss")])
    mutate(j)
    assert why in br.validate_journal(j)


def test_write_journal_refuses_invalid(tmp_path):
    svc = _svc(tmp_path)
    assert br.write_journal(svc._paths, {"version": 1}) is False
    assert not (tmp_path / "state" / "boot-restore.json").exists()


def test_load_journal_malformed_is_unsafe(tmp_path):
    svc = _svc(tmp_path)
    p = tmp_path / "state" / "boot-restore.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("{nope")
    _j, state = br.load_journal(svc._paths)
    assert state.startswith("unsafe:")


def test_unpruned_consumed_selection():
    mk = lambda state, ev, prune: dict(br.new_item(f"{state}{prune}", "stack", target="t",
                                                   evidence_ids=ev), state=state, prune=prune)
    j = br.new_journal(boot_id="B", pid=1, process_start_time=1, items=[])
    pending = mk("pending", ("e1",), None)
    no_ev = dict(br.new_item("noev", "stack", target="t"), state="failed")
    pruned = mk("succeeded", ("e2",), {"ok": True})
    unpruned = mk("failed", ("e3",), {"ok": False, "left": ["e3"]})
    never_pruned = mk("cancelled", ("e4",), None)
    j["items"] = [pending, no_ev, pruned, unpruned, never_pruned]
    assert br.unpruned_consumed(j) == [unpruned, never_pruned]


# --- config: fail-closed switch ------------------------------------------------------------------

def test_boot_config_default_on(tmp_path):
    assert _svc(tmp_path).boot_restore_enabled() == (True, "")


@pytest.mark.parametrize("toml_text,reason_fragment", [
    pytest.param("[boot]\nrestore = false\n", "disabled", id="explicit-off"),
    pytest.param('[boot]\nrestore = "true"\n', "non-boolean", id="non-bool-fails-closed"),
    # A local.toml that cannot be parsed loses the operator's switch — the autonomous starter
    # must NOT fall back to the default-ON (plan §3; every other consumer stays fail-soft).
    pytest.param("this is { not toml", "fail closed", id="malformed-toml-fails-closed"),
])
def test_boot_config_off_or_fails_closed(tmp_path, toml_text, reason_fragment):
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    (tmp_path / "config" / "local.toml").write_text(toml_text)
    on, reason = _svc(tmp_path).boot_restore_enabled()
    assert on is False and reason_fragment in reason


def test_set_boot_restore_round_trip(tmp_path):
    svc = _svc(tmp_path)
    assert svc.set_boot_restore(False).ok
    assert svc.boot_restore_enabled()[0] is False
    assert svc.set_boot_restore(True).ok
    assert svc.boot_restore_enabled() == (True, "")


def test_set_boot_restore_rejects_non_bool(tmp_path):
    assert _svc(tmp_path).set_boot_restore("on").ok is False


# --- pure planner -------------------------------------------------------------------------------

_KISS = StackMeta(stack_id="kiss", main="loraham-kiss-tnc", interactive_main=False,
                  declared_bands=(), fixed_band="433")
_SWITCH = StackMeta(stack_id="kiss", main="loraham-kiss-tnc", interactive_main=False,
                    declared_bands=("433", "868"), fixed_band="")


def _ev(**over):
    base = dict(launch_id="L1", stack="kiss", component="loraham-kiss-tnc", band="433",
                launched_at=1000.0, start_scope="stack", requested_target="kiss")
    base.update(over)
    return Evidence(**base)


def test_planner_one_item_and_daemon_last():
    metas = {"kiss": _KISS,
             "daemon": StackMeta(stack_id="daemon", main="loraham-daemon",
                                 interactive_main=False, declared_bands=(), fixed_band="")}
    plan = br.derive_plan(
        [_ev(),
         _ev(launch_id="D1", stack="daemon", component="loraham-daemon", band="868"),
         _ev(launch_id="D2", stack="daemon", component="loraham-daemon", band="433")],
        metas, {}, "daemon")
    assert [i["kind"] for i in plan.items] == ["stack", "daemon-reconcile"]
    assert plan.items[0]["target"] == "kiss" and plan.items[0]["band"] == "433"
    assert plan.items[1]["bands"] == ["433", "868"]
    assert sorted(plan.items[1]["evidence_ids"]) == ["D1", "D2"]


def test_planner_component_scope_never_widens():
    plan = br.derive_plan([_ev(start_scope="component", requested_target="loraham-kiss-tnc")],
                          {"kiss": _KISS}, {}, "daemon")
    assert not plan.items
    assert "never widened" in plan.skipped[0]["reason"]


def test_planner_unknown_interactive_and_missing_main_skip():
    plan = br.derive_plan(
        [_ev(stack="gone"),
         _ev(launch_id="L2", stack="chat", component="loraham-chat"),
         _ev(launch_id="L3", stack="igate", component="not-the-main")],
        {"chat": StackMeta(stack_id="chat", main="loraham-chat", interactive_main=True,
                           declared_bands=(), fixed_band="433"),
         "igate": StackMeta(stack_id="igate", main="loraham-igate", interactive_main=False,
                            declared_bands=(), fixed_band="433")},
        {}, "daemon")
    assert not plan.items
    reasons = {s["stack"]: s["reason"] for s in plan.skipped}
    assert "unknown stack" in reasons["gone"]
    assert "interactive" in reasons["chat"]
    assert "no main-component evidence" in reasons["igate"]


def test_planner_conflicting_bands_on_two_records_skip():
    plan = br.derive_plan([_ev(), _ev(launch_id="L2", band="868", launched_at=1001.0)],
                          {"kiss": _SWITCH}, {}, "daemon")
    assert not plan.items and "different bands" in plan.skipped[0]["reason"]


@pytest.mark.parametrize("ev_band,mk,expect_band,why_part", [
    # ownership authoritative, markers agree
    ("433", MarkerView(running_band_state="valid", running_band="433",
                       last_start_state="valid", last_start_band="433", last_start_at=5.0),
     "433", ""),
    # valid running marker DISAGREES with ownership -> ambiguity
    ("433", MarkerView(running_band_state="valid", running_band="868"), "", "disagree"),
    # valid last-start DISAGREES -> ambiguity
    ("433", MarkerView(last_start_state="valid", last_start_band="868", last_start_at=5.0),
     "", "disagree"),
    # legacy empty band: ONE agreeing marker supplies the candidate
    ("", MarkerView(running_band_state="valid", running_band="868"), "868", ""),
    # legacy empty band, two disagreeing markers -> ambiguity
    ("", MarkerView(running_band_state="valid", running_band="433",
                    last_start_state="valid", last_start_band="868", last_start_at=5.0),
     "", "disagree"),
    # any unsafe source blocks
    ("433", MarkerView(running_band_state="unsafe"), "", "unsafe band marker"),
    ("433", MarkerView(last_start_state="unsafe"), "", "unsafe band marker"),
    # switchable with NO band source: never guess
    ("", MarkerView(), "", "no valid band source"),
    # recorded band not declared by the stack
    ("999", MarkerView(), "", "not declared"),
])
def test_planner_band_matrix_switchable(ev_band, mk, expect_band, why_part):
    band, why = br._band_candidates(_ev(band=ev_band), _SWITCH, mk)
    assert band == expect_band
    assert why_part in why


def test_planner_fixed_band_rules():
    # fixed-band stack: manifest band rules; recorded band must be empty or equal
    assert br._band_candidates(_ev(band=""), _KISS, MarkerView()) == ("433", "")
    assert br._band_candidates(_ev(band="433"), _KISS, MarkerView()) == ("433", "")
    band, why = br._band_candidates(_ev(band="868"), _KISS, MarkerView())
    assert band == "" and "contradicts manifest band" in why


def test_planner_orders_by_start_time():
    metas = {"kiss": _KISS,
             "igate": StackMeta(stack_id="igate", main="loraham-igate",
                                interactive_main=False, declared_bands=(), fixed_band="433")}
    plan = br.derive_plan(
        [_ev(launched_at=2000.0),
         _ev(launch_id="I1", stack="igate", component="loraham-igate", launched_at=1500.0,
             requested_target="igate")],
        metas, {"igate": MarkerView(last_start_state="valid", last_start_band="433",
                                    last_start_at=2500.0)}, "daemon")
    # igate's last-start (2500) beats kiss's launched_at (2000) -> kiss first
    assert [i["target"] for i in plan.items] == ["kiss", "igate"]


# --- driver -------------------------------------------------------------------------------------

def test_driver_full_run_restores_and_prunes(tmp_path, monkeypatch):
    svc = _drv(tmp_path, monkeypatch)
    path = _write_record(tmp_path, _v1())
    calls = []
    monkeypatch.setattr(ControllerService, "start", _stub_start(calls))
    res = svc.boot_restore_run()
    assert res.ok and res.data.get("driver_completed") is True
    assert calls == [{"target": "kiss", "params": None, "band": "433"}]
    assert not path.exists()                              # evidence consumed + pruned
    j = _journal_on_disk(tmp_path)
    assert j["state"] == "done"
    assert [i["state"] for i in j["items"]] == ["succeeded"]
    assert j["items"][0]["prune"]["ok"] is True


def test_driver_failed_item_exits_completed(tmp_path, monkeypatch):
    # Failure consumes the evidence (no automatic retries) but the DRIVER completed:
    # data["driver_completed"] keeps the RemainAfterExit unit active (CLI exit 0).
    svc = _drv(tmp_path, monkeypatch)
    path = _write_record(tmp_path, _v1())
    calls = []
    monkeypatch.setattr(ControllerService, "start", _stub_start(calls, ok=False))
    res = svc.boot_restore_run()
    assert res.ok is False and res.data.get("driver_completed") is True
    assert not path.exists()                              # consumed even on failure
    j = _journal_on_disk(tmp_path)
    assert j["state"] == "failed"
    assert [i["state"] for i in j["items"]] == ["failed"]


def test_driver_hook_never_ran_leaves_item_pending(tmp_path, monkeypatch):
    # start() returned before the claim hook ran (pre-hook lock/validation failure): the item
    # must NOT be consumed — evidence intact, run truncated, no prune.
    svc = _drv(tmp_path, monkeypatch)
    path = _write_record(tmp_path, _v1())
    calls = []
    monkeypatch.setattr(ControllerService, "start", _stub_start(calls, ok=False, call_hook=False))
    res = svc.boot_restore_run()
    assert res.ok is False and res.data.get("driver_completed") is True
    assert "pending" in res.summary and "truncated" in res.summary
    assert path.exists()                                  # NOT consumed
    j = _journal_on_disk(tmp_path)
    assert [i["state"] for i in j["items"]] == ["pending"]
    assert j["items"][0]["prune"] is None


def test_driver_hook_cancels_when_evidence_removed(tmp_path, monkeypatch):
    # Operator-stop race: the evidence disappears between planning and the locked attempt. The
    # hook durably writes `cancelled` BEFORE refusing; the run finishes clean.
    svc = _drv(tmp_path, monkeypatch)
    path = _write_record(tmp_path, _v1())
    def vanish_then_hook(self, target, apply=False, params=None, stop_owners=False, band="",
                         daemon_overrides=None, file_overrides=None, auto_install_ctx=None, *,
                         _before_start_locked=None):
        path.unlink()
        refusal = _before_start_locked()
        assert refusal is not None                        # the hook refused
        return refusal
    monkeypatch.setattr(ControllerService, "start", vanish_then_hook)
    res = svc.boot_restore_run()
    assert res.ok and res.data.get("driver_completed") is True
    j = _journal_on_disk(tmp_path)
    assert j["state"] == "done"
    assert [i["state"] for i in j["items"]] == ["cancelled"]


def test_driver_boot_id_unavailable_touches_nothing(tmp_path, monkeypatch):
    # TEMPORARY integrity failure: exit nonzero, journal byte-identical, evidence intact.
    svc = _drv(tmp_path, monkeypatch, cur_boot="")
    path = _write_record(tmp_path, _v1())
    item = br.new_item("i1", "stack", target="kiss", band="433", evidence_ids=(_v1()["launch_id"],))
    item["state"] = "failed"                              # a consumed-but-unpruned prior item
    old = br.new_journal(boot_id="OLDBOOT", pid=1, process_start_time=1, items=[item])
    assert br.write_journal(svc._paths, old)
    before = (tmp_path / "state" / "boot-restore.json").read_bytes()
    res = svc.boot_restore_run()
    assert res.ok is False and not res.data.get("driver_completed")
    assert (tmp_path / "state" / "boot-restore.json").read_bytes() == before
    assert path.exists()                                  # no deletion of ANY kind


def test_driver_malformed_journal_blocks_even_gate_retirement(tmp_path, monkeypatch):
    # A malformed journal blocks restoration AND gate-based retirement (switch off): the driver
    # cannot know what was previously consumed, so it must not delete anything.
    svc = _drv(tmp_path, monkeypatch, enabled=False)
    path = _write_record(tmp_path, _v1())
    jp = tmp_path / "state" / "boot-restore.json"
    jp.parent.mkdir(parents=True, exist_ok=True)
    jp.write_text("{nope")
    res = svc.boot_restore_run()
    assert res.ok is False and not res.data.get("driver_completed")
    assert path.exists() and jp.read_text() == "{nope"


def test_driver_recovery_prunes_consumed_evidence_first(tmp_path, monkeypatch):
    # A prior run consumed an item but could not prune its evidence. The next invocation cleans
    # it up (cleanup ONLY — never start) before replacing the journal.
    svc = _drv(tmp_path, monkeypatch)
    rec = _v1()
    path = _write_record(tmp_path, rec)
    item = br.new_item("i1", "stack", target="kiss", band="433",
                       evidence_ids=(rec["launch_id"],))
    item["state"] = "failed"
    item["prune"] = {"ok": False, "left": [rec["launch_id"]]}
    old = br.new_journal(boot_id="OLDBOOT", pid=1, process_start_time=1, items=[item])
    old["state"] = "failed"
    assert br.write_journal(svc._paths, old)
    calls = []
    monkeypatch.setattr(ControllerService, "start", _stub_start(calls))
    res = svc.boot_restore_run()
    assert res.ok and res.data.get("driver_completed") is True
    assert not calls                                      # recovery never starts anything
    assert not path.exists()                              # cleaned up
    j = _journal_on_disk(tmp_path)
    assert j["state"] == "no-plan" and j["run_id"] != old["run_id"]


def test_driver_switch_off_retires_evidence(tmp_path, monkeypatch):
    svc = _drv(tmp_path, monkeypatch, enabled=False)
    path = _write_record(tmp_path, _v1())
    calls = []
    monkeypatch.setattr(ControllerService, "start", _stub_start(calls))
    res = svc.boot_restore_run()
    assert res.ok and res.data.get("driver_completed") is True
    assert not calls and not path.exists()                # nothing started, evidence retired
    j = _journal_on_disk(tmp_path)
    assert j["state"] == "disabled" and "disabled by [boot] restore" in j["reason"]
    # a later re-enable finds no evidence -> no resurrection
    svc2 = _drv(tmp_path, monkeypatch, enabled=True)
    res2 = svc2.boot_restore_run()
    assert res2.ok and _journal_on_disk(tmp_path)["state"] == "no-plan"


def test_driver_web_gate_off_disables(tmp_path, monkeypatch):
    svc = _drv(tmp_path, monkeypatch, web=(False, "lhpc-web.service is not enabled"))
    path = _write_record(tmp_path, _v1())
    calls = []
    monkeypatch.setattr(ControllerService, "start", _stub_start(calls))
    res = svc.boot_restore_run()
    assert res.ok and not calls and not path.exists()
    j = _journal_on_disk(tmp_path)
    assert j["state"] == "disabled" and "web console integration" in j["reason"]


def test_driver_admission_refusal_consumes_nothing(tmp_path, monkeypatch):
    import contextlib
    from lhpc.core.service_base import AdmissionRefused
    svc = _drv(tmp_path, monkeypatch)
    path = _write_record(tmp_path, _v1())
    @contextlib.contextmanager
    def refused(self, op, target=""):
        raise AdmissionRefused("self-update in progress")
        yield
    monkeypatch.setattr(ControllerService, "_admission_guard", refused)
    res = svc.boot_restore_run()
    # NOT a completed run: exit must be nonzero (the RemainAfterExit unit would otherwise
    # report success and silently skip this boot's restore) — but nothing is consumed.
    assert res.ok is False and not res.data.get("driver_completed")
    assert "restart lhpc-boot-restore" in res.summary
    assert path.exists() and _journal_on_disk(tmp_path) is None


def test_driver_same_boot_records_are_not_evidence(tmp_path, monkeypatch):
    svc = _drv(tmp_path, monkeypatch)
    path = _write_record(tmp_path, _v1(boot="CURBOOT"))
    calls = []
    monkeypatch.setattr(ControllerService, "start", _stub_start(calls))
    res = svc.boot_restore_run()
    assert res.ok and not calls
    assert path.exists()                                  # same-boot record untouched
    assert _journal_on_disk(tmp_path)["state"] == "no-plan"


def test_driver_journal_unwritable_is_integrity_failure(tmp_path, monkeypatch):
    svc = _drv(tmp_path, monkeypatch)
    _write_record(tmp_path, _v1())
    monkeypatch.setattr(br, "write_journal", lambda paths, j: False)
    monkeypatch.setattr(sbr.boot_restore, "write_journal", lambda paths, j: False)
    res = svc.boot_restore_run()
    assert res.ok is False and not res.data.get("driver_completed")


# --- daemon reconciliation ----------------------------------------------------------------------

def _daemon_env(tmp_path, monkeypatch, *, kept=("433", "868"), served=(), bands=("433", "868")):
    svc = _drv(tmp_path, monkeypatch)
    for i, b in enumerate(bands):
        _write_record(tmp_path, _v1(pid=100 + i, stack="daemon", comp="loraham-daemon",
                                    band=b, nonce=f"{i:02d}" * 16))
    monkeypatch.setattr(ControllerService, "_daemon_arbitrated_bands",
                        lambda self, radio: (list(kept), {}))
    monkeypatch.setattr(ControllerService, "_daemon_claimed_bands", lambda self: list(served))
    return svc


def test_daemon_reconcile_two_residual_bands_is_all_active(tmp_path, monkeypatch):
    svc = _daemon_env(tmp_path, monkeypatch)
    calls = []
    monkeypatch.setattr(ControllerService, "start", _stub_start(calls))
    res = svc.boot_restore_run()
    assert res.ok
    assert calls == [{"target": "daemon", "params": None, "band": ""}]   # params=None = all-active


def test_daemon_reconcile_one_residual_band_is_explicit(tmp_path, monkeypatch):
    svc = _daemon_env(tmp_path, monkeypatch, served=("433",))
    calls = []
    monkeypatch.setattr(ControllerService, "start", _stub_start(calls))
    res = svc.boot_restore_run()
    assert res.ok
    assert calls == [{"target": "daemon", "params": {"radio": "868"}, "band": ""}]


def test_daemon_reconcile_no_residual_succeeds_without_start(tmp_path, monkeypatch):
    svc = _daemon_env(tmp_path, monkeypatch, served=("433", "868"))
    calls = []
    monkeypatch.setattr(ControllerService, "start", _stub_start(calls))
    res = svc.boot_restore_run()
    assert res.ok and not calls
    j = _journal_on_disk(tmp_path)
    assert [i["state"] for i in j["items"]] == ["succeeded"]
    assert "no residual band" in j["items"][0]["result"]["summary"]


def test_daemon_reconcile_runs_after_client_stacks(tmp_path, monkeypatch):
    svc = _daemon_env(tmp_path, monkeypatch, bands=("433",))
    _write_record(tmp_path, _v1())                        # kiss client too
    calls = []
    monkeypatch.setattr(ControllerService, "start", _stub_start(calls))
    assert svc.boot_restore_run().ok
    assert [c["target"] for c in calls] == ["kiss", "daemon"]


# --- classifier: legacy scope proof -------------------------------------------------------------

def _legacy_candidate(tmp_path, stack="kiss", band="433", started_at=1000):
    d = tmp_path / "state" / "last-start"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{stack}.json").write_text(json.dumps(
        {"version": 1, "stack": stack, "band": band, "started_at": started_at,
         "entries": {}, "hash": "x"}))


def test_legacy_record_scope_proof_boundary(tmp_path, monkeypatch):
    # v0 records (no scope fields) are eligible ONLY with a full-stack last-start proof whose
    # started_at >= launched_at — EQUAL timestamps eligible (whole-second precision).
    svc = _drv(tmp_path, monkeypatch)
    monkeypatch.setattr(lifecycle_mod, "current_boot_id", lambda: "CURBOOT")
    _write_record(tmp_path, _v0(pid=999999, launched_at=1000))   # dead pid -> provably stale
    _legacy_candidate(tmp_path, started_at=1000)
    evidence, skipped, _issues, _state = svc._classify_boot_evidence("CURBOOT")
    assert len(evidence) == 1 and evidence[0].start_scope == "stack"
    # now the proof is OLDER than the launch -> scope unknown -> skipped
    _legacy_candidate(tmp_path, started_at=999)
    evidence, skipped, _issues, _state = svc._classify_boot_evidence("CURBOOT")
    assert not evidence and "legacy record scope unknown" in skipped[0]["reason"]


def test_legacy_live_record_is_not_evidence(tmp_path, monkeypatch):
    svc = _drv(tmp_path, monkeypatch)
    proc = _spawn()
    try:
        ident = svc._lifecycle()._proc_identity(proc.pid)
        _write_record(tmp_path, _v0(pid=proc.pid, starttime=str(ident["starttime"]),
                                    pgid=ident["pgid"], sid=ident["sid"]))
        _legacy_candidate(tmp_path)
        evidence, _sk, _is, _st = svc._classify_boot_evidence("CURBOOT")
        assert not evidence                               # live legacy process: leave it alone
    finally:
        proc.kill(); proc.wait()


# --- status projection --------------------------------------------------------------------------

def test_status_none_when_absent(tmp_path):
    assert _svc(tmp_path).boot_restore_status() is None


def test_status_unsafe_on_malformed(tmp_path):
    jp = tmp_path / "state" / "boot-restore.json"
    jp.parent.mkdir(parents=True, exist_ok=True)
    jp.write_text("[]")
    st = _svc(tmp_path).boot_restore_status()
    assert st["state"] == "unsafe" and "unsafe" in st["reason"]


def test_status_running_with_dead_driver_is_truncated(tmp_path):
    svc = _svc(tmp_path)
    j = br.new_journal(boot_id="B", pid=999999, process_start_time=123,
                       items=[br.new_item("i1", "stack", target="kiss",
                                          evidence_ids=("e",))])
    assert br.write_journal(svc._paths, j)
    assert svc.boot_restore_status()["state"] == "truncated"


def test_status_running_with_live_driver(tmp_path, monkeypatch):
    import os as _os
    from lhpc.core import procident
    svc = _svc(tmp_path)
    ident = procident.proc_identity(_os.getpid())
    j = br.new_journal(boot_id=lifecycle_mod.current_boot_id(), pid=_os.getpid(),
                       process_start_time=ident["starttime"],
                       items=[br.new_item("i1", "stack", target="kiss")])
    assert br.write_journal(svc._paths, j)
    assert svc.boot_restore_status()["state"] == "running"


# --- task banner --------------------------------------------------------------------------------

def _banner(svc):
    return [t for t in svc.running_tasks() if t.get("kind") == "boot-restore"]


def test_banner_states(tmp_path):
    import time as _time
    svc = _svc(tmp_path)
    assert _banner(svc) == []                             # no journal -> no banner

    # truncated: a `running` journal whose recorded driver is provably dead
    j = br.new_journal(boot_id="B", pid=999999, process_start_time=1,
                       items=[br.new_item("i1", "stack", target="kiss", evidence_ids=("e",))])
    assert br.write_journal(svc._paths, j)
    (entry,) = _banner(svc)
    assert entry["state"] == "failed" and "restart lhpc-boot-restore" in entry["hint"]

    # failed items -> red with the consumed-evidence remedy
    j["state"] = "failed"
    j["items"][0]["state"] = "failed"
    j["finished_at"] = _time.time()
    assert br.write_journal(svc._paths, j)
    (entry,) = _banner(svc)
    assert entry["state"] == "failed" and "lhpc stack start" in entry["hint"]

    # done -> green within the 60s epoch, absent after
    j["state"] = "done"
    j["items"][0]["state"] = "succeeded"
    assert br.write_journal(svc._paths, j)
    (entry,) = _banner(svc)
    assert entry["state"] == "done" and "Restored 1" in entry["label"]
    j["finished_at"] = _time.time() - 3600
    assert br.write_journal(svc._paths, j)
    assert _banner(svc) == []                             # old green results expire

    # unsafe journal -> unsafe banner
    (tmp_path / "state" / "boot-restore.json").write_text("{nope")
    (entry,) = _banner(svc)
    assert entry["state"] == "unsafe"


def test_banner_running_with_live_driver(tmp_path):
    import os as _os
    from lhpc.core import procident
    svc = _svc(tmp_path)
    ident = procident.proc_identity(_os.getpid())
    j = br.new_journal(boot_id=lifecycle_mod.current_boot_id(), pid=_os.getpid(),
                       process_start_time=ident["starttime"],
                       items=[br.new_item("i1", "stack", target="kiss")])
    assert br.write_journal(svc._paths, j)
    (entry,) = _banner(svc)
    assert entry["state"] == "running"
    assert entry["href"] == "/controller/logs?src=boot-restore"


# --- CLI ----------------------------------------------------------------------------------------

@pytest.mark.contract
def test_cli_autostart_show_and_toggle(tmp_path, monkeypatch, capsys):
    from lhpc.adapters.cli.main import main
    monkeypatch.setenv("LHPC_RUNTIME_ROOT", str(tmp_path))
    assert main(["autostart"]) == 0
    out = capsys.readouterr().out
    assert "Boot auto-restore: ON" in out and "last result: (none)" in out
    assert main(["autostart", "off"]) == 0
    capsys.readouterr()
    assert main(["autostart"]) == 0
    assert "Boot auto-restore: OFF" in capsys.readouterr().out


def test_cli_autostart_run_service_rejects_switch(tmp_path, monkeypatch, capsys):
    from lhpc.adapters.cli.main import main
    monkeypatch.setenv("LHPC_RUNTIME_ROOT", str(tmp_path))
    assert main(["autostart", "off", "--run-service"]) == 2


def test_cli_run_service_disabled_path_exits_zero(tmp_path, monkeypatch, capsys):
    # No enabled/canonical web unit in this HOME -> gate off -> terminal `disabled`; the driver
    # COMPLETED, so the oneshot exit code is 0 and the unit stays active.
    from lhpc.adapters.cli.main import main
    monkeypatch.setenv("LHPC_RUNTIME_ROOT", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    assert main(["autostart", "--run-service"]) == 0
    j = _journal_on_disk(tmp_path)
    assert j["state"] == "disabled"


@pytest.mark.contract
def test_cli_run_service_unsafe_journal_exits_nonzero(tmp_path, monkeypatch, capsys):
    from lhpc.adapters.cli.main import main
    monkeypatch.setenv("LHPC_RUNTIME_ROOT", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    jp = tmp_path / "state" / "boot-restore.json"
    jp.parent.mkdir(parents=True, exist_ok=True)
    jp.write_text("{nope")
    assert main(["autostart", "--run-service"]) == 1


# --- web ----------------------------------------------------------------------------------------

def _web_client(svc):
    from lhpc.adapters.web.app import create_app
    return create_app(service_factory=lambda: svc).test_client()


def _csrf_of(client):
    import re
    body = client.get("/stacks").get_data(as_text=True)
    m = re.search(r'name="_csrf" value="([^"]+)"', body)
    assert m, "no CSRF token on /stacks"
    return m.group(1)


@pytest.mark.contract
def test_web_toggle_round_trip(tmp_path):
    svc = _svc(tmp_path)
    c = _web_client(svc)
    body = c.get("/stacks").get_data(as_text=True)
    assert "Boot restore" in body and 'name="restore"' in body and "checked" in body
    tok = _csrf_of(c)
    r = c.post("/boot-restore", data={"_csrf": tok})       # unchecked checkbox -> off
    assert r.status_code == 302
    assert svc.boot_restore_enabled()[0] is False
    r = c.post("/boot-restore", data={"_csrf": tok, "restore": "on"})
    assert r.status_code == 302
    assert svc.boot_restore_enabled() == (True, "")


@pytest.mark.contract
def test_web_toggle_requires_csrf(tmp_path):
    c = _web_client(_svc(tmp_path))
    assert c.post("/boot-restore", data={"restore": "on"}).status_code == 400


def test_web_log_source_never_aliases(tmp_path):
    c = _web_client(_svc(tmp_path))
    ok = c.get("/controller/logs?src=boot-restore").get_data(as_text=True)
    assert "lhpc-boot-restore.service" in ok
    # unknown src normalizes to the web console log — never an arbitrary unit/path
    other = c.get("/controller/logs?src=../../etc/passwd").get_data(as_text=True)
    assert "lhpc-web.service" in other and "passwd" not in other


# --- lock-order + scoped feed clear on the REAL start path --------------------------------------

def _serving_svc(tmp_path, monkeypatch, *, hardware=False, sockets=("/tmp/loraconf433.sock",)):
    """A start-capable service: daemon already serving on `sockets`, kiss installed/built."""
    reply = b"STATUS RADIO=READY TX=0 TXMODE=MANAGED CADWAIT=1500 CADRSSI=-90\n"
    fake = FakeSystem(unix_replies={s: reply for s in sockets})
    (tmp_path / "src" / "loraham-kiss-tnc").mkdir(parents=True, exist_ok=True)
    if hardware:
        (tmp_path / "config").mkdir(parents=True, exist_ok=True)
        (tmp_path / "config" / "local.toml").write_text('[radio]\nhardware = "loraham"\n')
    svc = ControllerService(system=fake.system, paths=Paths(runtime_root=tmp_path))
    from conftest import set_call
    set_call(svc)
    monkeypatch.setattr(ControllerService, "is_built", lambda self, comp: True)
    monkeypatch.setattr(lifecycle_mod.Lifecycle, "missing_requirements", lambda self, comp: [])
    return svc


def test_daemon_all_active_start_locks_every_band(tmp_path, monkeypatch):
    # An EMPTY requested radio = serve every active band after arbitration. The lock set must
    # cover BOTH bands — the old `stack_config(...).radio` fallback under-locked a dual-band
    # serve-all start (locked 433 while both bands were launched).
    svc = _serving_svc(tmp_path, monkeypatch, hardware=True,
                      sockets=("/tmp/loraconf433.sock", "/tmp/loraconf868.sock"))
    assert set(svc.active_bands()) == {"433", "868"}      # precondition: dual-band hardware
    assert svc._operation_bands("daemon", "", "", "start") == {"433", "868"}
    keys = svc._operation_resource_keys("daemon", "", "", "start")
    assert {"loraham.radio.433", "loraham.radio.868"} <= set(keys)


def test_start_feed_clear_scoped_to_operation_bands(tmp_path, monkeypatch):
    # A 433 client start clears ONLY 433 — never the other band's RX/TX window.
    svc = _serving_svc(tmp_path, monkeypatch)
    cleared = []
    monkeypatch.setattr(ControllerService, "clear_daemon_feed",
                        lambda self, b: cleared.append(b))
    monkeypatch.setattr(lifecycle_mod.Lifecycle, "start",
                        lambda self, stack, comp, params=None, band="", **kw:
                        lifecycle_mod.StartLaunch(True, ""))
    svc.start("kiss", apply=True)         # post-start endpoint verify fails on the fake box —
    assert cleared == ["433"]             # irrelevant: the clear happened, scoped to 433 only


def test_hook_runs_after_every_lock_before_any_mutation(tmp_path, monkeypatch):
    # LOCK-ORDER REGRESSION: admission -> config-stability -> every resource lock -> HOOK ->
    # feed clear -> spawn. The hook must never run before a lock, and nothing may mutate
    # before the hook.
    import contextlib
    svc = _serving_svc(tmp_path, monkeypatch)
    events = []
    real_admission = ControllerService._admission_guard
    real_stable = ControllerService._config_stable
    real_acquire = ControllerService._acquire_key
    @contextlib.contextmanager
    def adm(self, op, target=""):
        events.append("admission")
        with real_admission(self, op, target):
            yield
    @contextlib.contextmanager
    def stable(self, exclusive=False):
        events.append("config-stable")
        with real_stable(self, exclusive):
            yield
    def acquire(self, stack, k, op, target):
        events.append(f"lock:{k}")
        return real_acquire(self, stack, k, op, target)
    monkeypatch.setattr(ControllerService, "_admission_guard", adm)
    monkeypatch.setattr(ControllerService, "_config_stable", stable)
    monkeypatch.setattr(ControllerService, "_acquire_key", acquire)
    monkeypatch.setattr(ControllerService, "clear_daemon_feed",
                        lambda self, b: events.append(f"clear:{b}"))
    monkeypatch.setattr(lifecycle_mod.Lifecycle, "start",
                        lambda self, stack, comp, params=None, band="", **kw:
                        (events.append("spawn"), lifecycle_mod.StartLaunch(True, ""))[1])
    svc.start("kiss", apply=True, _before_start_locked=lambda: events.append("hook"))
    assert "hook" in events and "spawn" in events
    hook_at = events.index("hook")
    assert "admission" in events[:hook_at] and "config-stable" in events[:hook_at]
    locks = [e for e in events if e.startswith("lock:claim.loraham.radio.")
             or e.startswith("lock:lifecycle.") or e.startswith("lock:source.")]
    assert locks and all(events.index(e) < hook_at for e in locks)   # every radio lock first
    assert all(events.index(e) > hook_at for e in events if e.startswith("clear:") or e == "spawn")


def test_hook_refusal_cancels_with_zero_side_effects(tmp_path, monkeypatch):
    svc = _serving_svc(tmp_path, monkeypatch)
    cleared, spawned = [], []
    monkeypatch.setattr(ControllerService, "clear_daemon_feed",
                        lambda self, b: cleared.append(b))
    monkeypatch.setattr(lifecycle_mod.Lifecycle, "start",
                        lambda self, stack, comp, params=None, band="", **kw:
                        (spawned.append(comp.id), lifecycle_mod.StartLaunch(True, ""))[1])
    refusal = ActionResult(False, "boot-restore item cancelled")
    res = svc.start("kiss", apply=True, _before_start_locked=lambda: refusal)
    assert res is refusal                                 # the refusal IS the result
    assert cleared == [] and spawned == []                # zero side effects


def test_crash_after_attempting_never_retries_but_pending_survives(tmp_path, monkeypatch):
    # Crash window: a prior driver died between the durable `attempting` claim and the start's
    # outcome. The item is CONSUMED (recovery prunes its evidence; never restarted); a still-
    # `pending` item's evidence survives recovery and is re-planned.
    svc = _drv(tmp_path, monkeypatch)
    consumed = _v1()                                       # kiss — was `attempting` at the crash
    survivor = _v1(pid=500, stack="igate", comp="loraham-igate", nonce="cd" * 16,
                   requested_target="igate")
    p_consumed = _write_record(tmp_path, consumed)
    p_survivor = _write_record(tmp_path, survivor)
    items = [dict(br.new_item("a1", "stack", target="kiss", band="433",
                              evidence_ids=(consumed["launch_id"],)), state="attempting"),
             br.new_item("p1", "stack", target="igate", band="433",
                         evidence_ids=(survivor["launch_id"],))]
    old = br.new_journal(boot_id="OLDBOOT", pid=1, process_start_time=1, items=items)
    assert br.write_journal(svc._paths, old)
    calls = []
    monkeypatch.setattr(ControllerService, "start", _stub_start(calls))
    res = svc.boot_restore_run()
    assert res.ok
    assert not p_consumed.exists()                         # cleaned up, never restarted
    assert not p_survivor.exists()                         # consumed by the NEW run's attempt
    assert [c["target"] for c in calls] == ["igate"]       # kiss NOT retried
    j = _journal_on_disk(tmp_path)
    assert j["run_id"] != old["run_id"] and j["state"] == "done"


def test_client_failure_keeps_daemon_reconciliation(tmp_path, monkeypatch):
    svc = _daemon_env(tmp_path, monkeypatch, bands=("433",))
    _write_record(tmp_path, _v1())                         # kiss client, will FAIL
    calls = []
    monkeypatch.setattr(ControllerService, "start", _stub_start(calls, ok=False))
    res = svc.boot_restore_run()
    assert res.ok is False and res.data.get("driver_completed") is True
    assert [c["target"] for c in calls] == ["kiss", "daemon"]   # reconcile still ran


def test_banner_failed_is_dismissible_truncated_is_not(tmp_path):
    import time as _time
    svc = _svc(tmp_path)
    j = br.new_journal(boot_id="B", pid=999999, process_start_time=1,
                       items=[dict(br.new_item("i1", "stack", target="kiss",
                                               evidence_ids=("e",)), state="failed")])
    j["state"] = "failed"
    j["finished_at"] = _time.time()
    assert br.write_journal(svc._paths, j)
    assert svc.task_dismiss("boot-restore", j["run_id"]) is True
    assert _banner(svc) == []                              # dismissed
    # a truncated run (journal still `running`, driver dead) must NOT be dismissible —
    # its remainder is restorable via a unit restart
    j2 = br.new_journal(boot_id="B", pid=999999, process_start_time=1,
                        items=[br.new_item("i2", "stack", target="kiss",
                                           evidence_ids=("e",))])
    assert br.write_journal(svc._paths, j2)
    assert svc.task_dismiss("boot-restore", j2["run_id"]) is False
    (entry,) = _banner(svc)
    assert entry["state"] == "failed" and "restart lhpc-boot-restore" in entry["hint"]


# --- audit-round fixes --------------------------------------------------------------------------

# (the malformed-local.toml fail-closed case now lives in the parametrized
# test_boot_config_off_or_fails_closed above)


def test_hook_claim_write_failure_leaves_item_restorable(tmp_path, monkeypatch):
    # The durable `attempting` write fails -> the claim NEVER happened. The item must stay
    # `pending` on disk (restorable) and the run must exit as an integrity failure — never a
    # fabricated `cancelled` that recovery would then prune.
    svc = _drv(tmp_path, monkeypatch)
    path = _write_record(tmp_path, _v1())
    real_write = br.write_journal
    writes = {"n": 0}
    def flaky(paths, j):
        writes["n"] += 1
        if writes["n"] == 2:                       # 1st = plan journal, 2nd = the claim
            return False
        return real_write(paths, j)
    monkeypatch.setattr(br, "write_journal", flaky)
    monkeypatch.setattr(sbr.boot_restore, "write_journal", flaky)
    calls = []
    monkeypatch.setattr(ControllerService, "start", _stub_start(calls))
    res = svc.boot_restore_run()
    assert res.ok is False and not res.data.get("driver_completed")
    assert path.exists()                           # evidence NOT consumed
    j = _journal_on_disk(tmp_path)
    assert [i["state"] for i in j["items"]] == ["pending"]


def test_hook_unsafe_owned_dir_never_consumes(tmp_path, monkeypatch):
    # A transient inventory failure at claim time must not consume the item — the evidence may
    # still exist, and cancelling would let recovery delete never-attempted evidence.
    svc = _drv(tmp_path, monkeypatch)
    rec = _v1()
    _write_record(tmp_path, rec)
    owned = tmp_path / "state" / "owned"
    def sabotage_then_start(self, target, apply=False, params=None, stop_owners=False, band="",
                            daemon_overrides=None, file_overrides=None, auto_install_ctx=None, *,
                            _before_start_locked=None):
        import shutil
        shutil.rmtree(owned)
        owned.symlink_to(tmp_path)                 # containment failure -> dir_state unsafe
        refusal = _before_start_locked()
        assert refusal is not None
        return refusal
    monkeypatch.setattr(ControllerService, "start", sabotage_then_start)
    res = svc.boot_restore_run()
    assert res.ok is False and not res.data.get("driver_completed")   # integrity failure
    j = _journal_on_disk(tmp_path)
    assert [i["state"] for i in j["items"]] == ["pending"]            # NOT consumed


def test_status_completed_run_with_pending_projects_truncated(tmp_path, monkeypatch):
    svc = _drv(tmp_path, monkeypatch)
    path = _write_record(tmp_path, _v1())
    calls = []
    monkeypatch.setattr(ControllerService, "start", _stub_start(calls, ok=False, call_hook=False))
    svc.boot_restore_run()                          # hook never ran -> pending item, run "failed"
    assert path.exists()
    st = svc.boot_restore_status()
    assert st["state"] == "truncated"               # remedy: restart the unit
    (entry,) = _banner(svc)
    assert "restart lhpc-boot-restore" in entry["hint"]


def test_status_unreadable_boot_id_is_blocked_not_truncated(tmp_path, monkeypatch):
    # Round-4 contract: an unreadable CURRENT boot id short-circuits to the live `blocked`
    # projection — never `truncated` (whose restart remedy would SIGTERM a live driver) and
    # never a stale prior result.
    import os as _os
    from lhpc.core import procident
    svc = _svc(tmp_path)
    ident = procident.proc_identity(_os.getpid())
    j = br.new_journal(boot_id="THEBOOT", pid=_os.getpid(),
                       process_start_time=ident["starttime"],
                       items=[br.new_item("i1", "stack", target="kiss")])
    assert br.write_journal(svc._paths, j)
    monkeypatch.setattr(sbr, "current_boot_id", lambda: "")
    st = svc.boot_restore_status()
    assert st["state"] == "blocked" and "boot identity unavailable" in st["reason"]


def test_disabled_retirement_journal_precedes_prune(tmp_path, monkeypatch):
    # The `disabled` journal (with every cancelled item) is durable BEFORE evidence is deleted:
    # sabotage the prune and the journal must still record the cancelled items unpruned.
    svc = _drv(tmp_path, monkeypatch, enabled=False)
    path = _write_record(tmp_path, _v1())
    monkeypatch.setattr(ControllerService, "_boot_prune_evidence",
                        lambda self, ids: {"ok": False, "left": list(ids)})
    res = svc.boot_restore_run()
    assert res.ok and path.exists()                 # nothing deleted by the sabotaged prune
    j = _journal_on_disk(tmp_path)
    assert j["state"] == "disabled"
    assert [i["state"] for i in j["items"]] == ["cancelled"]
    assert br.unpruned_consumed(j)                  # recovery will finish the retirement


def test_inventory_unreadable_dir_is_unsafe_not_missing(tmp_path, monkeypatch):
    # EACCES/EIO on the owned dir must read UNSAFE (records may exist but be unreadable) —
    # a fail-open "missing" would consume items as "evidence removed" and later prune
    # still-existing evidence.
    import errno
    from lhpc.core import runtime_fs
    life = _life(tmp_path)
    _write_record(tmp_path, _v0())
    def denied(paths, d):
        raise OSError(errno.EACCES, "denied")
    monkeypatch.setattr(runtime_fs, "scandir_nofollow", denied)
    _valid, _issues, state = life.owned_inventory()
    assert state == "unsafe"


def test_web_integration_proven_real_function(tmp_path, monkeypatch):
    # The REAL gate (no monkeypatching): wants symlink + byte-exact canonical web unit -> proven.
    # This is the test that would have caught the live str-vs-Path crash on the Zero.
    from lhpc.core import updater_units as U
    home = tmp_path / "home"
    ud = home / ".config" / "systemd" / "user"
    (ud / "default.target.wants").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    root = tmp_path                                        # runtime root == tmp_path
    _r, checkout, venv = U.deployment_paths(str(root))
    svc = _svc(tmp_path)
    ok, why = svc._web_integration_proven()
    assert ok is False and "no wants symlink" in why       # nothing installed yet
    (ud / U.WEB_UNIT).write_text(U.render(U.WEB_UNIT, str(root), checkout, venv))
    (ud / "default.target.wants" / U.WEB_UNIT).symlink_to(ud / U.WEB_UNIT)
    assert svc._web_integration_proven() == (True, "")
    # a customized unit breaks the proof
    (ud / U.WEB_UNIT).write_text(U.render(U.WEB_UNIT, str(root), checkout, venv) + "# edited\n")
    ok, why = svc._web_integration_proven()
    assert ok is False and "not canonical" in why
    # a dangling/foreign wants symlink breaks it too
    (ud / U.WEB_UNIT).write_text(U.render(U.WEB_UNIT, str(root), checkout, venv))
    (ud / "default.target.wants" / U.WEB_UNIT).unlink()
    (ud / "default.target.wants" / U.WEB_UNIT).symlink_to("/dev/null")
    ok, why = svc._web_integration_proven()
    assert ok is False and "wants symlink points at" in why


def test_driver_defect_is_clean_integrity_failure(tmp_path, monkeypatch):
    # Any unexpected exception inside the driver -> clean nonzero failure, never a traceback
    # escaping to systemd as the unit's only diagnostic.
    svc = _drv(tmp_path, monkeypatch)
    monkeypatch.setattr(ControllerService, "_boot_restore_run_admitted",
                        lambda self: (_ for _ in ()).throw(TypeError("boom")))
    res = svc.boot_restore_run()
    assert res.ok is False and not res.data.get("driver_completed")
    assert "TypeError" in res.summary


# --- audit round 4: tri-state prune, strict prune schema, blocked projection --------------------

def test_prune_present_but_unreadable_leaf_is_not_pruned(tmp_path, monkeypatch):
    # A malformed-but-present evidence leaf must stay in `left` (blocking journal replacement) —
    # treating it as absent would let it come back readable and be restored AGAIN.
    svc = _drv(tmp_path, monkeypatch)
    rec = _v1()
    path = _write_record(tmp_path, rec)
    path.write_text("{malformed")                          # present, unreadable
    res = svc._boot_prune_evidence([rec["launch_id"]])
    assert res["ok"] is False and res["left"] == [rec["launch_id"]]
    assert path.exists()


def test_recovery_blocks_while_consumed_leaf_unreadable(tmp_path, monkeypatch):
    # Consumed item + its leaf temporarily malformed: cleanup must NOT report success, the old
    # journal must NOT be replaced, and the run must fail with an integrity result.
    svc = _drv(tmp_path, monkeypatch)
    rec = _v1()
    path = _write_record(tmp_path, rec)
    path.write_text("{malformed")
    item = dict(br.new_item("i1", "stack", target="kiss", band="433",
                            evidence_ids=(rec["launch_id"],)), state="attempting")
    old = br.new_journal(boot_id="OLDBOOT", pid=1, process_start_time=1, items=[item])
    assert br.write_journal(svc._paths, old)
    res = svc.boot_restore_run()
    assert res.ok is False and not res.data.get("driver_completed")
    assert path.exists()                                   # leaf untouched
    j = _journal_on_disk(tmp_path)
    assert j["run_id"] == old["run_id"]                    # journal NOT replaced
    assert j["state"] == "unsafe"


def test_claim_hook_unreadable_leaf_leaves_item_pending(tmp_path, monkeypatch):
    # At the locked claim boundary a present-but-unreadable leaf is a transient integrity
    # state — never an operator removal, never a durable `cancelled`.
    svc = _drv(tmp_path, monkeypatch)
    rec = _v1()
    path = _write_record(tmp_path, rec)
    def corrupt_then_hook(self, target, apply=False, params=None, stop_owners=False, band="",
                          daemon_overrides=None, file_overrides=None, auto_install_ctx=None, *,
                          _before_start_locked=None):
        path.write_text("{malformed")
        refusal = _before_start_locked()
        assert refusal is not None
        return refusal
    monkeypatch.setattr(ControllerService, "start", corrupt_then_hook)
    res = svc.boot_restore_run()
    assert res.ok is False and not res.data.get("driver_completed")
    j = _journal_on_disk(tmp_path)
    assert [i["state"] for i in j["items"]] == ["pending"]  # NOT cancelled/consumed
    assert path.exists()


@pytest.mark.parametrize("prune,why", [
    ({"ok": True}, "item prune invalid"),                  # the decisive bare-ok case
    ({"left": []}, "item prune invalid"),                  # ok missing
    ({"ok": True, "left": [], "extra": 1}, "item prune invalid"),
    ({"ok": "yes", "left": []}, "item prune.ok invalid"),
    ({"ok": True, "left": ["not-an-evidence-id"]}, "item prune.left invalid"),
    ({"ok": True, "left": ["e1"]}, "item prune ok/left contradiction"),
    ({"ok": False, "left": []}, "item prune failure without left/reason"),
    ({"ok": False, "left": [], "reason": 7}, "item prune.reason invalid"),
])
def test_journal_prune_schema_is_strict(prune, why):
    it = dict(br.new_item("i1", "stack", target="kiss", evidence_ids=("e1",)),
              state="failed", prune=prune)
    j = br.new_journal(boot_id="B", pid=1, process_start_time=1, items=[it])
    assert why in br.validate_journal(j)


def test_status_blocked_while_boot_id_unreadable(tmp_path, monkeypatch):
    # The LIVE projection must say WHY nothing restored — never show an older result or nothing.
    svc = _svc(tmp_path)
    monkeypatch.setattr(sbr, "current_boot_id", lambda: "")
    st = svc.boot_restore_status()                          # absent journal
    assert st["state"] == "blocked" and "boot identity unavailable" in st["reason"]
    j = br.new_journal(boot_id="B", pid=1, process_start_time=1, items=[])
    j["state"] = "done"
    j["finished_at"] = 1000
    assert br.write_journal(svc._paths, j)
    st = svc.boot_restore_status()                          # prior terminal journal
    assert st["state"] == "blocked"                         # NOT the stale "done"
    # journal untouched, and the banner shows it red with the reason
    assert _journal_on_disk(tmp_path)["state"] == "done"
    (entry,) = _banner(svc)
    assert entry["state"] == "unsafe" and "boot identity unavailable" in entry["hint"]


def test_cli_autostart_shows_blocked_reason(tmp_path, monkeypatch, capsys):
    from lhpc.adapters.cli.main import main
    monkeypatch.setenv("LHPC_RUNTIME_ROOT", str(tmp_path))
    monkeypatch.setattr(sbr, "current_boot_id", lambda: "")
    assert main(["autostart"]) == 0
    out = capsys.readouterr().out
    assert "restore BLOCKED" in out and "boot identity unavailable" in out
