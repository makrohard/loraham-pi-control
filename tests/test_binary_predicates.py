"""Channel-aware predicates + action refusals (B5).

`is_built` stays byte-identical (artifacts satisfy the physical probes); the receipt only
answers where the physical world cannot — a covered component has no clone by design. Build and
host-test actions refuse on a binary-installed stack with the switch-back command.
"""


from lhpc.core import binary_receipt as brx
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


def _install_daemon_binary(svc, tmp_path):
    """Lay down exactly what the artifact lays down (daemon binary only — NO clone, no
    RadioLib) plus the receipt."""
    spec = svc.binary_spec("daemon")
    for rel in spec.proof_paths:
        p = tmp_path
        for seg in rel.split("/")[:-1]:
            p = p / seg
        p.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_bytes(b"ELF")
    rec = brx.BinaryReceipt(
        stack="daemon", artifact_sha256="a" * 64, artifact_size=10,
        filename=f"daemon-{'a' * 64}.tar.zst", url="https://example.invalid/d.tar.zst",
        components={c: "b" * 40 for c in spec.covers}, provenance={},
        files=tuple(spec.proof_paths),
        file_hashes={r: _h(tmp_path, r) for r in spec.proof_paths},
        proof_paths=tuple(spec.proof_paths),
        registry_baseline={}, probe="loraham_daemon 1.0")
    assert brx.write_receipt(svc._paths, rec)


def _comp(svc, cid):
    for st in svc.stacks():
        for c in st.components:
            if c.id == cid:
                return c
    raise AssertionError(cid)


# --- predicates ---------------------------------------------------------------------------------

def test_is_built_is_unchanged_for_binary_artifacts(tmp_path, monkeypatch):
    # The artifact lands exactly at the manifest `bin` path, so the PHYSICAL probe answers
    # "built" with no receipt involvement at all.
    svc = _svc(tmp_path, monkeypatch)
    _install_daemon_binary(svc, tmp_path)
    assert svc.is_built(_comp(svc, "loraham-daemon")) is True


def test_install_blocker_accepts_covered_component_without_clone(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch)
    daemon = _comp(svc, "loraham-daemon")
    assert "not installed" in svc.install_blocker(daemon)     # nothing there yet
    _install_daemon_binary(svc, tmp_path)
    assert svc.install_blocker(daemon) == ""                  # binary-covered: no clone needed


def test_install_blocker_unchanged_for_uncovered_component(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch)
    _install_daemon_binary(svc, tmp_path)
    kiss = _comp(svc, "loraham-kiss-tnc")
    assert "not installed" in svc.install_blocker(kiss)       # different stack, still source


def test_auto_install_installed_predicate_ignores_covered_clones(tmp_path, monkeypatch):
    # RadioLib has NO clone in binary mode; the stack must still read "installed".
    svc = _svc(tmp_path, monkeypatch)
    st = svc.stack("daemon")
    assert svc._auto_install_stack_installed(st) is False
    _install_daemon_binary(svc, tmp_path)
    assert svc._auto_install_stack_installed(st) is True


def test_predicates_revert_when_receipt_retired(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch)
    _install_daemon_binary(svc, tmp_path)
    assert svc._auto_install_stack_installed(svc.stack("daemon")) is True
    assert brx.remove_receipt(svc._paths, "daemon")
    # without the receipt the source-dir requirement is back (RadioLib is missing)
    assert svc._auto_install_stack_installed(svc.stack("daemon")) is False


# --- action refusals ----------------------------------------------------------------------------

def test_build_refused_on_binary_stack(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch)
    _install_daemon_binary(svc, tmp_path)
    res = svc.build("daemon", apply=True)
    assert not res.ok and "prebuilt binary" in res.summary
    assert res.data.get("binary_channel") is True
    assert any("--source pinned" in c for c in res.next_commands)


def test_host_test_refused_on_binary_stack(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch)
    _install_daemon_binary(svc, tmp_path)
    res = svc.test("daemon", apply=True)
    assert not res.ok and "host tests" in res.summary
    assert res.data.get("skipped") == "binary-install"


def test_build_and_test_allowed_without_receipt(tmp_path, monkeypatch):
    # No receipt -> the historical behaviour must be byte-identical (these fail for the
    # ordinary "not installed" reasons, never the binary refusal).
    svc = _svc(tmp_path, monkeypatch)
    for res in (svc.build("daemon", apply=False), svc.test("daemon", apply=False)):
        assert "prebuilt binary" not in res.summary


def test_web_job_refuses_build_on_binary_stack(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch)
    _install_daemon_binary(svc, tmp_path)
    _job, state, reason = svc.spawn_web_job("build", "daemon")
    assert state == "blocked" and "prebuilt binary" in reason


def test_web_job_accepts_binary_channel_for_install(tmp_path, monkeypatch):
    # The web install path must accept the new channel (and still reject nonsense).
    svc = _svc(tmp_path, monkeypatch)
    _job, state, reason = svc.spawn_web_job("install", "kiss", source="binary")
    assert state == "blocked" and "binary channel unavailable" in reason
    _job2, state2, reason2 = svc.spawn_web_job("install", "daemon", source="bogus")
    assert state2 == "blocked" and "invalid source" in reason2
