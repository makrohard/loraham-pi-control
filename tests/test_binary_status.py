"""Status/provenance on the binary channel (B6).

Without a receipt-aware source assessment a binary stack reads MISSING/NOT_A_REPO and therefore
NOT_INSTALLED (status.py `_run_state_for_service`) — the stack would look broken while being
perfectly installed. The BINARY state reports the artifact's provenance instead.
"""


from lhpc.core import binary_receipt as brx
from lhpc.core.model import SourceState
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService



def _h(root, rel):
    """sha256 of an installed test file — the receipt validator requires one hash per file."""
    import hashlib
    return hashlib.sha256((root / rel).read_bytes()).hexdigest()

def _svc(tmp_path, monkeypatch):
    monkeypatch.setattr(ControllerService, "binary_target", lambda self: "aarch64-trixie")
    return ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))


def _install_binary(svc, tmp_path, stack="daemon", sha="ab" * 32, commits=None):
    spec = svc.binary_spec(stack)
    for rel in spec.proof_paths:
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_bytes(b"ELF")
    comps = commits or {c: "cd" * 20 for c in spec.covers}
    assert brx.write_receipt(svc._paths, brx.BinaryReceipt(
        stack=stack, artifact_sha256=sha, artifact_size=9,
        filename=f"{stack}-{sha}.tar.zst", url="https://example.invalid/a.tar.zst",
        components=comps, provenance={"lhpc_commit": "ee" * 20},
        files=tuple(spec.proof_paths),
        file_hashes={r: _h(tmp_path, r) for r in spec.proof_paths},
        proof_paths=tuple(spec.proof_paths), registry_baseline={}, probe="ok"))
    svc._snapshot_state.cache = None


def _cs(svc, stack, cid):
    return svc.build_snapshot().stack(stack).components[cid]


# --- source state -------------------------------------------------------------------------------

def test_without_receipt_source_reads_missing(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch)
    st = _cs(svc, "daemon", "loraham-daemon")
    assert st.source_state is SourceState.MISSING
    assert st.run_state.value == "not-installed"


def test_binary_receipt_gives_binary_state_and_provenance(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch)
    _install_binary(svc, tmp_path)
    st = _cs(svc, "daemon", "loraham-daemon")
    assert st.source_state is SourceState.BINARY
    assert st.source_version == "binary@" + ("ab" * 32)[:9]
    assert st.source_head == "cd" * 20                      # the artifact's component commit
    assert st.run_state.value != "not-installed"            # THE bug this branch prevents


def test_every_covered_component_reports_binary(tmp_path, monkeypatch):
    # RadioLib has no clone at all in binary mode; it must not read "missing".
    svc = _svc(tmp_path, monkeypatch)
    _install_binary(svc, tmp_path)
    assert _cs(svc, "daemon", "radiolib").source_state is SourceState.BINARY


def test_uncovered_stacks_are_untouched(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch)
    _install_binary(svc, tmp_path)
    assert _cs(svc, "kiss", "loraham-kiss-tnc").source_state is SourceState.MISSING


def test_superseded_receipt_falls_back_to_git_probe(tmp_path, monkeypatch):
    from lhpc.core import source_registry
    svc = _svc(tmp_path, monkeypatch)
    spec = svc.binary_spec("daemon")
    for rel in spec.proof_paths:
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_bytes(b"ELF")
    assert brx.write_receipt(svc._paths, brx.BinaryReceipt(
        stack="daemon", artifact_sha256="ab" * 32, artifact_size=9,
        filename="daemon-x.tar.zst", url="https://example.invalid/a", components={},
        provenance={}, files=tuple(spec.proof_paths),
        file_hashes={r: _h(tmp_path, r) for r in spec.proof_paths},
        proof_paths=tuple(spec.proof_paths),
        registry_baseline={"src/loraham-daemon": ""}, probe="ok"))
    svc._snapshot_state.cache = None
    assert _cs(svc, "daemon", "loraham-daemon").source_state is SourceState.BINARY
    source_registry.write_record(svc._paths, source_registry.RegistryRecord(
        source_rel="src/loraham-daemon", remote="https://example.invalid/d.git",
        selector="pinned", resolved_commit="c" * 40, adopted_at=1.0, txn_id="txn-1",
        strategy="adopt", components=("loraham-daemon",)))
    svc._snapshot_state.cache = None
    # receipt superseded -> the ordinary git probe answers again
    assert _cs(svc, "daemon", "loraham-daemon").source_state is not SourceState.BINARY


# --- renderers ----------------------------------------------------------------------------------

def test_status_versions_shows_artifact_provenance(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch)
    _install_binary(svc, tmp_path)
    res = svc.status_versions()
    line = next(d for d in res.details if "loraham-daemon" in d)
    assert "binary" in line and "binary@" in line and "built_from=" in line


def test_status_versions_unchanged_for_source_stacks(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch)
    _install_binary(svc, tmp_path)
    line = next(d for d in svc.status_versions().details if "loraham-kiss-tnc" in d)
    assert "pin=" in line and "tag=" in line and "binary@" not in line


def test_web_pill_renders_provenance(tmp_path, monkeypatch):
    from lhpc.adapters.web.app import create_app
    svc = _svc(tmp_path, monkeypatch)
    _install_binary(svc, tmp_path)
    client = create_app(service_factory=lambda: svc).test_client()
    # the source pill lives in the stack SUMMARY row on the overview page
    body = client.get("/stacks").get_data(as_text=True)
    assert "src: binary" in body and "binary@" + ("ab" * 32)[:9] in body
