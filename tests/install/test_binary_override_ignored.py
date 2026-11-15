"""U10a (plan v6, D6): the pin-mismatch override an older lhpc recorded in a binary receipt is ignored. The
receipt still reads valid; status, doctor and boot restore name nothing; an update to the installed artifact
downloads nothing. The receipt key is written into the file as an older lhpc wrote it."""
from __future__ import annotations

import json

from lhpc.core import binary_receipt as brx
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService

A = "a" * 40


def _svc(tmp_path, monkeypatch):
    monkeypatch.setattr(ControllerService, "binary_target", lambda self: "aarch64-trixie")
    return ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))


def _with_legacy_override(svc, binary_receipt, stack="daemon", sha="ab" * 32):
    """A receipt whose first covered component is built from A, with the acceptance an older lhpc recorded."""
    pins = svc._binary_pins(stack)
    cid = next(iter(pins))
    binary_receipt(svc, stack, sha=sha, commits={**pins, cid: A})
    p = brx.receipt_path(svc._paths, stack)
    d = json.loads(p.read_text())
    d["override"] = {"at": 1, "lhpc_version": "0.11.3", "mismatch": {cid: [A, pins[cid]]}}
    p.write_text(json.dumps(d))
    svc.invalidate_snapshot()
    return cid


def test_a_legacy_override_leaves_the_receipt_valid_and_status_quiet(tmp_path, monkeypatch, binary_receipt):
    svc = _svc(tmp_path, monkeypatch)
    cid = _with_legacy_override(svc, binary_receipt)
    assert svc.binary_receipt_state("daemon")[0] == "valid"
    status = "\n".join(svc.status("daemon").details)
    assert cid in status                                              # the component's own line is there
    assert "installed over the pin check" not in status


def test_doctor_names_no_override(tmp_path, monkeypatch, binary_receipt):
    svc = _svc(tmp_path, monkeypatch)
    _with_legacy_override(svc, binary_receipt)
    doctor = svc.doctor()
    assert doctor.details                                             # the report itself is there
    assert not any("installed over the pin check" in d for d in doctor.details)


def test_boot_restore_records_no_override_note(tmp_path, monkeypatch, binary_receipt):
    from lhpc.core import boot_restore as br
    svc = _svc(tmp_path, monkeypatch)
    _with_legacy_override(svc, binary_receipt)
    meta = br.StackMeta(stack_id="daemon", main="loraham-daemon", interactive_main=False,
                        declared_bands=(), fixed_band="")
    ev = br.Evidence(launch_id="D1", stack="daemon", component="loraham-daemon", band="433", launched_at=1000.0)
    [item] = br.derive_plan([ev], {"daemon": meta}, "daemon").items
    item["state"] = "attempting"
    journal = br.new_journal(boot_id="b", pid=1, process_start_time=1, items=[item])
    monkeypatch.setattr(type(svc), "_boot_start_ok", lambda res: True)
    monkeypatch.setattr(type(svc), "_boot_prune_evidence", lambda self, ids: None)
    svc._boot_settle_item(journal, item, type("R", (), {"summary": "started", "ok": True})())
    assert "result" in item and "override" not in item["result"]


def test_an_update_to_the_installed_artifact_with_a_legacy_override_downloads_nothing(
        tmp_path, monkeypatch, binary_receipt, stub_pipeline):
    svc = _svc(tmp_path, monkeypatch)
    _with_legacy_override(svc, binary_receipt, sha="a" * 64)          # the stub's entry is "a" * 64
    stub_pipeline(svc)
    res = svc.update("daemon", apply=True, source="binary")
    assert res.ok and res.data["changes"] == 0, res.summary
