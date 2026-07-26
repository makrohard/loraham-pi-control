"""Binary -> source switching: the requested selector is ENFORCED, and the retirement commits
only after the whole switch succeeded.

An existing checkout left over from the binary channel (meshcom keeps its pinned clone) must not
be accepted as "already installed" for a different selector — that reported a channel switch
while the tree stayed pinned. And the artifact stays restorable from local disk until every
source group, its ownership record and the MeshCom password step have all succeeded.
"""

import hashlib

import pytest

from lhpc.core import binary_install as bi
from lhpc.core import binary_receipt as brx
from lhpc.core import source_registry
from lhpc.core.paths import Paths
from lhpc.core.probes import RealSystem
from lhpc.core.service_base import ActionResult
from lhpc.core.services import ControllerService

DAEMON_PATH = "src/loraham-daemon"
RADIOLIB_PATH = "src/RadioLib"


def _svc(tmp_path, monkeypatch):
    """A REAL runner: these tests turn on actual git checkouts (HEAD, remote, dirtiness), which
    is exactly what the switch pre-flight reads. No network — every repo is local."""
    monkeypatch.setattr(ControllerService, "binary_target", lambda self: "aarch64-trixie")
    return ControllerService(system=RealSystem(), paths=Paths(runtime_root=tmp_path))


def _lay_down(svc, tmp_path, stack="daemon"):
    """A completed binary install: the artifact's files plus its receipt."""
    spec = svc.binary_spec(stack)
    files, hashes = [], {}
    for rel in spec.proof_paths:
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_bytes(b"ELF")
        files.append(rel)
        hashes[rel] = hashlib.sha256(b"ELF").hexdigest()
    rec = brx.BinaryReceipt(
        stack=stack, artifact_sha256="ab" * 32, artifact_size=9,
        filename=f"{stack}-{'ab' * 32}.tar.zst", url="https://example.invalid/a.tar.zst",
        components=dict(svc._binary_pins(stack)), provenance={}, files=tuple(files),
        file_hashes=hashes, proof_paths=tuple(spec.proof_paths), registry_baseline={},
        probe="ok")
    assert brx.write_receipt(svc._paths, rec)
    svc.invalidate_snapshot()
    return rec


def _git(svc, cwd, *args):
    r = svc._system.runner.run(["git", "-C", str(cwd), *args], 20.0)
    assert r.returncode == 0, f"git {' '.join(args)}: {r.stderr}"
    return (r.stdout or "").strip()


def _checkout(svc, tmp_path, rel, comp_id, *, commits=2, remote=None, dirty=False,
              at_first=False):
    """A REAL managed checkout at `rel` with an ownership record — the state an operator has
    after a source install (or, for meshcom, after a binary install kept its pinned clone).

    Returns the commit the checkout sits at.
    """
    comp = next(c for st in svc.stacks() for c in st.components if c.id == comp_id)
    origin = remote if remote is not None else (comp.source.remote or "https://x.invalid/r.git")
    d = tmp_path / rel
    d.mkdir(parents=True, exist_ok=True)
    _git(svc, d, "init", "-q", "-b", "main")
    _git(svc, d, "config", "user.email", "t@example.invalid")
    _git(svc, d, "config", "user.name", "t")
    shas = []
    for i in range(commits):
        (d / f"f{i}").write_text(str(i))
        _git(svc, d, "add", "-A")
        _git(svc, d, "commit", "-q", "-m", f"c{i}")
        shas.append(_git(svc, d, "rev-parse", "HEAD"))
    _git(svc, d, "remote", "add", "origin", origin)
    if at_first:
        _git(svc, d, "checkout", "-q", shas[0])
    head = _git(svc, d, "rev-parse", "HEAD")
    assert source_registry.write_record(svc._paths, source_registry.RegistryRecord(
        source_rel=rel, remote=origin, selector="pinned", resolved_commit=head,
        adopted_at=1.0, txn_id="txn-" + comp_id, strategy="adopt", components=(comp_id,)))
    if dirty:
        (d / "operator-notes.txt").write_text("mine")
    return head


class _Adopted:
    status, detail, provenance = "done", "", ""


class _Failed:
    status, detail, provenance = "failed", "clone failed", ""


def _stub_adopt(svc, monkeypatch, *, fail_paths=(), record=True):
    """A successful adoption INCLUDING the ownership record it writes (the switch is not
    complete until every adopted path is recorded). Paths in `fail_paths` fail instead."""
    seen = []

    def _adopt(self, inst, st, comp, selector, resolved, force=False, locked=False):
        path = comp.source.path
        seen.append((path, selector, force))
        if path in fail_paths:
            return _Failed()
        (svc._paths.resolve_source(path)).mkdir(parents=True, exist_ok=True)
        if record:
            source_registry.write_record(svc._paths, source_registry.RegistryRecord(
                source_rel=path, remote=comp.source.remote or "https://x.invalid/r.git",
                selector=selector, resolved_commit="e" * 40, adopted_at=2.0,
                txn_id="txn-new-" + comp.id, strategy="adopt", components=(comp.id,)))
        return _Adopted()
    monkeypatch.setattr(ControllerService, "_adopt_dev_fallback", _adopt)
    return seen


# --- 1/2: the requested selector is enforced ----------------------------------------------------

def test_pinned_checkout_switching_to_dev_is_replaced(tmp_path, monkeypatch):
    """The reported case: meshcom keeps its PINNED clone on the binary channel, the operator
    asks for `dev`, and the pinned tree used to be accepted as "already installed"."""
    svc = _svc(tmp_path, monkeypatch)
    _lay_down(svc, tmp_path)
    _checkout(svc, tmp_path, DAEMON_PATH, "loraham-daemon")
    monkeypatch.setattr(ControllerService, "_frozen_ref",
                        lambda self, comp, sel: (("f" * 40, "dev tip"), ""))
    seen = _stub_adopt(svc, monkeypatch)
    res = svc.install("daemon", apply=True, source="dev")
    assert res.ok, res.summary
    assert (DAEMON_PATH, "dev", True) in seen, "the pinned tree must be REPLACED, not skipped"


def test_dev_checkout_switching_to_pinned_reaches_the_pin(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch)
    _lay_down(svc, tmp_path)
    _checkout(svc, tmp_path, DAEMON_PATH, "loraham-daemon")   # at some other commit
    seen = _stub_adopt(svc, monkeypatch)
    res = svc.install("daemon", apply=True, source="pinned")
    assert res.ok, res.summary
    assert (DAEMON_PATH, "pinned", True) in seen


def test_checkout_already_at_the_pin_is_a_no_op(tmp_path, monkeypatch):
    """An already-correct checkout must not be re-cloned."""
    svc = _svc(tmp_path, monkeypatch)
    _lay_down(svc, tmp_path)
    comp = next(c for st in svc.stacks() for c in st.components if c.id == "loraham-daemon")
    head = _checkout(svc, tmp_path, DAEMON_PATH, "loraham-daemon")
    monkeypatch.setattr(type(comp.source), "pin_commit", property(lambda _s: head), raising=False)
    replace, refusals = svc.switch_source_plan(
        [(DAEMON_PATH, comp, "pinned", (head, ""))], owned_files=())
    assert replace == set() and refusals == []


# --- 3: an unprovable checkout refuses, with the binary untouched -------------------------------

@pytest.mark.parametrize("kind", ["dirty", "wrong-remote"])
def test_unprovable_checkout_refuses_without_retiring_the_binary(tmp_path, monkeypatch, kind):
    svc = _svc(tmp_path, monkeypatch)
    rec = _lay_down(svc, tmp_path)
    _checkout(svc, tmp_path, DAEMON_PATH, "loraham-daemon",
              dirty=(kind == "dirty"),
              remote=("https://elsewhere.invalid/other.git" if kind == "wrong-remote" else None))
    monkeypatch.setattr(ControllerService, "binary_retire",
                        lambda *a, **k: pytest.fail("the binary must not be touched"))
    res = svc.install("daemon", apply=True, source="pinned")
    assert not res.ok and "cannot be taken over" in res.summary
    assert brx.receipt_state(svc._paths, "daemon")[0] == "valid"
    assert (tmp_path / rec.proof_paths[0]).exists()
    assert bi.read_journal(svc._paths)[1] == "absent"


# --- 4/5/6: the retirement commits only after the COMPLETE switch -------------------------------

def test_second_group_failing_restores_the_binary_and_undoes_what_it_created(tmp_path,
                                                                             monkeypatch):
    """One source group succeeds, a later one fails: the previous binary must be back, and the
    checkout this switch created must be gone again (a pre-existing one is never touched)."""
    svc = _svc(tmp_path, monkeypatch)
    rec = _lay_down(svc, tmp_path)
    seen = _stub_adopt(svc, monkeypatch, fail_paths=(RADIOLIB_PATH,))
    res = svc.install("daemon", apply=True, source="pinned")
    assert not res.ok and "FAILED" in res.summary
    assert {p for p, _s, _f in seen} == {DAEMON_PATH, RADIOLIB_PATH}
    assert brx.receipt_state(svc._paths, "daemon")[0] == "valid"      # the binary is back
    assert (tmp_path / rec.proof_paths[0]).read_bytes() == b"ELF"
    assert source_registry.record_state(svc._paths, DAEMON_PATH)[0] == "absent"
    assert bi.read_journal(svc._paths)[1] == "absent"


def test_incomplete_ownership_record_rolls_the_switch_back(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch)
    rec = _lay_down(svc, tmp_path)
    _stub_adopt(svc, monkeypatch, record=False)          # adoption "succeeds" but records nothing
    res = svc.install("daemon", apply=True, source="pinned")
    assert not res.ok and "ownership record" in res.summary
    assert brx.receipt_state(svc._paths, "daemon")[0] == "valid"
    assert (tmp_path / rec.proof_paths[0]).exists()


def test_failed_hmac_enablement_restores_the_binary_and_open_auth(tmp_path, monkeypatch):
    """MeshCom source adoption succeeds but the password cannot be enabled: the switch is not
    complete, so the binary (which runs OPEN auth) must be restored unchanged."""
    svc = _svc(tmp_path, monkeypatch)
    rec = _lay_down(svc, tmp_path, stack="meshcom")
    _stub_adopt(svc, monkeypatch)
    monkeypatch.setattr(ControllerService, "hmac_set_secret",
                        lambda self, sid, action, **k: ActionResult(False, "keyfile unwritable"))
    res = svc.install("meshcom", apply=True, source="pinned")
    assert not res.ok and "HMAC password" in res.summary
    assert brx.receipt_state(svc._paths, "meshcom")[0] == "valid"
    assert (tmp_path / rec.proof_paths[0]).exists()
    hc = svc._hmac_component("meshcom")
    assert svc._resolved_param_value("meshcom", "run", hc.id, "password_file") == ""
    assert bi.read_journal(svc._paths)[1] == "absent"


def test_complete_switch_commits_the_retirement(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch)
    rec = _lay_down(svc, tmp_path)
    _stub_adopt(svc, monkeypatch)
    res = svc.install("daemon", apply=True, source="pinned")
    assert res.ok, res.summary
    assert brx.receipt_state(svc._paths, "daemon")[0] == "absent"
    assert not (tmp_path / rec.proof_paths[0]).exists()
    assert bi.read_journal(svc._paths)[1] == "absent"
    assert list((tmp_path / "state" / "binary").glob(".backup-*")) == []


# --- a REPLACED pre-existing checkout is restored too, record and all --------------------------

def _baseline_receipt(svc, tmp_path, stack, paths_):
    """A binary receipt whose registry baseline records the CURRENT txn id of each covered
    source path — the comparison that decides valid vs superseded."""
    rec = _lay_down(svc, tmp_path, stack=stack)
    import dataclasses
    base = {}
    for rel in paths_:
        state, rrec, _why = source_registry.record_state(svc._paths, rel)
        base[rel] = rrec.txn_id if (state == "valid" and rrec) else ""
    rec = dataclasses.replace(rec, registry_baseline=base)
    assert brx.write_receipt(svc._paths, rec)
    svc.invalidate_snapshot()
    return rec


def test_meshcom_pinned_clone_is_restored_when_hmac_fails(tmp_path, monkeypatch):
    """THE realistic case: meshcom keeps its PINNED clone on the binary channel, `--source dev`
    replaces it, and the HMAC step then fails. The clone, its ownership record AND the binary
    receipt must all be back — a restored receipt whose baseline no longer matches the registry
    reads SUPERSEDED, which is not a restored install (audit finding)."""
    svc = _svc(tmp_path, monkeypatch)
    qemu_path = next(c.source.path for st in svc.stacks() for c in st.components
                     if c.id == "meshcom-qemu")
    head = _checkout(svc, tmp_path, qemu_path, "meshcom-qemu")
    old_txn = source_registry.record_state(svc._paths, qemu_path)[1].txn_id
    rec = _baseline_receipt(svc, tmp_path, "meshcom", [qemu_path])
    assert brx.receipt_state(svc._paths, "meshcom")[0] == "valid"

    monkeypatch.setattr(ControllerService, "_frozen_ref",
                        lambda self, comp, sel: (("f" * 40, "dev tip"), ""))
    _stub_adopt(svc, monkeypatch)                       # replaces the clone, writes a NEW record
    monkeypatch.setattr(ControllerService, "hmac_set_secret",
                        lambda self, sid, action, **k: ActionResult(False, "keyfile unwritable"))

    res = svc.install("meshcom", apply=True, source="dev")
    assert not res.ok and "HMAC password" in res.summary
    # the pinned clone is back, at its old commit and under its old ownership record
    assert _git(svc, tmp_path / qemu_path, "rev-parse", "HEAD") == head
    state, rrec, _why = source_registry.record_state(svc._paths, qemu_path)
    assert state == "valid" and rrec.txn_id == old_txn
    # …so the restored receipt is VALID, not superseded
    assert brx.receipt_state(svc._paths, "meshcom")[0] == "valid"
    assert (tmp_path / rec.proof_paths[0]).exists()
    assert bi.read_journal(svc._paths)[1] == "absent"


def test_replaced_first_source_is_restored_when_a_later_group_fails(tmp_path, monkeypatch):
    """First group: a pre-existing checkout is REPLACED. Second group fails. The first checkout
    and its record must return to their pre-switch state."""
    svc = _svc(tmp_path, monkeypatch)
    head = _checkout(svc, tmp_path, DAEMON_PATH, "loraham-daemon")
    old_txn = source_registry.record_state(svc._paths, DAEMON_PATH)[1].txn_id
    rec = _baseline_receipt(svc, tmp_path, "daemon", [DAEMON_PATH])
    monkeypatch.setattr(ControllerService, "_frozen_ref",
                        lambda self, comp, sel: (("f" * 40, "dev tip"), ""))
    _stub_adopt(svc, monkeypatch, fail_paths=(RADIOLIB_PATH,))

    res = svc.install("daemon", apply=True, source="dev")
    assert not res.ok and "FAILED" in res.summary
    assert _git(svc, tmp_path / DAEMON_PATH, "rev-parse", "HEAD") == head
    state, rrec, _why = source_registry.record_state(svc._paths, DAEMON_PATH)
    assert state == "valid" and rrec.txn_id == old_txn
    assert brx.receipt_state(svc._paths, "daemon")[0] == "valid"
    assert (tmp_path / rec.proof_paths[0]).read_bytes() == b"ELF"
    assert bi.read_journal(svc._paths)[1] == "absent"
