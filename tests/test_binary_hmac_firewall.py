"""meshcom on the binary channel: open auth, HMAC gates, truthful firewall (B10) + B9 removal.

The published meshcom firmware is built with an EMPTY XR password. So the binary install
switches the bridge to open auth transactionally, every HMAC mutation path refuses with ONE
reason, and the firewall model must classify that listener as auth="none" — otherwise its
exposure warning would claim a password that does not exist.
"""


from lhpc.core import binary_receipt as brx
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService


def _svc(tmp_path, monkeypatch):
    monkeypatch.setattr(ControllerService, "binary_target", lambda self: "aarch64-trixie")
    return ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))


def _lay_down(svc, tmp_path, stack="meshcom"):
    import hashlib
    spec = svc.binary_spec(stack)
    files, hashes = [], {}
    for rel in spec.proof_paths:
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_bytes(b"ELF")
        files.append(rel)
        hashes[rel] = hashlib.sha256(b"ELF").hexdigest()
    assert brx.write_receipt(svc._paths, brx.BinaryReceipt(
        stack=stack, artifact_sha256="ab" * 32, artifact_size=9,
        filename=f"{stack}-{'ab' * 32}.tar.zst", url="https://example.invalid/a.tar.zst",
        components=dict(svc._binary_pins(stack)), provenance={}, files=tuple(files),
        file_hashes=hashes, proof_paths=tuple(spec.proof_paths), registry_baseline={},
        probe="qemu 9.0"))
    svc.invalidate_snapshot()


# --- HMAC gates ---------------------------------------------------------------------------------

def test_hmac_applies_stays_true_but_blocks_with_reason(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch)
    _lay_down(svc, tmp_path)
    # NOT flipped to "does not apply" — the UI must show the reason
    assert svc.hmac_applies("meshcom") is True
    reason = svc.hmac_binary_block("meshcom")
    assert "NO mesh password" in reason and "open auth" in reason


def test_hmac_apply_start_refused(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch)
    _lay_down(svc, tmp_path)
    res = svc.hmac_apply_start("meshcom", "enable")
    assert not res.ok and res.data.get("binary_channel") is True
    assert any("--source pinned" in c for c in res.next_commands)


def test_hmac_cli_refused(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch)
    _lay_down(svc, tmp_path)
    lines = []
    assert svc.hmac_apply_cli("meshcom", "enable", lines.append) == 1
    assert any("NO mesh password" in ln for ln in lines)


def test_hmac_driver_refused_authoritatively(tmp_path, monkeypatch):
    # The shared step runner gates too — a CLI/web-only check could be bypassed.
    svc = _svc(tmp_path, monkeypatch)
    _lay_down(svc, tmp_path)
    lines = []
    assert svc._hmac_run_steps("meshcom", "enable", "f" * 32, lines.append) == 1
    assert any("refused" in ln and "NO mesh password" in ln for ln in lines)


def test_hmac_set_secret_enable_refused_disable_allowed(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch)
    _lay_down(svc, tmp_path)
    assert svc.hmac_set_secret("meshcom", "enable").ok is False
    # `disable` is what the binary install itself performs — it must stay available
    assert "NO mesh password" not in svc.hmac_set_secret("meshcom", "disable").summary


def test_hmac_unblocked_without_receipt(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch)
    assert svc.hmac_binary_block("meshcom") == ""


def test_hmac_page_shows_reason(tmp_path, monkeypatch):
    from lhpc.adapters.web.app import create_app
    svc = _svc(tmp_path, monkeypatch)
    _lay_down(svc, tmp_path)
    client = create_app(service_factory=lambda: svc).test_client()
    body = client.get("/stacks/meshcom/hmac/enable").get_data(as_text=True)
    assert "not available" in body and "NO mesh password" in body
    assert "--source pinned" in body


# --- firewall truthfulness ----------------------------------------------------------------------

def _bridge_scope(svc):
    """Resolve the meshcom bridge listener's firewall scope directly — a loopback-bound
    listener is not offered as a selectable candidate row, but its AUTH classification is what
    the exposure model reasons with."""
    st = svc.stack("meshcom")
    for comp in st.components:
        for ep in comp.endpoints:
            if ep.kind == "tcp" and ep.role == "listener" and ep.firewall:
                return svc._fw_resolve_scope(st, comp, ep)
    return None


def test_bridge_listener_is_password_auth_on_source(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch)
    ep = _bridge_scope(svc)
    assert ep is not None and ep["auth"] == "password"


def test_bridge_listener_is_open_auth_on_binary(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch)
    _lay_down(svc, tmp_path)
    ep = _bridge_scope(svc)
    assert ep is not None and ep["auth"] == "none"


# --- B9: uninstall / clean remove the artifact ---------------------------------------------------

def test_clean_force_retires_binary(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch)
    _lay_down(svc, tmp_path)
    proof = tmp_path / svc.binary_spec("meshcom").proof_paths[0]
    assert proof.exists()
    svc.clean("meshcom", apply=True, purge=True)
    assert not proof.exists()
    assert brx.receipt_state(svc._paths, "meshcom")[0] == "absent"


def test_uninstall_retires_binary(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch)
    _lay_down(svc, tmp_path, stack="daemon")
    proof = tmp_path / svc.binary_spec("daemon").proof_paths[0]
    assert proof.exists()
    res = svc.uninstall("daemon", apply=True)
    assert not proof.exists()
    assert brx.receipt_state(svc._paths, "daemon")[0] == "absent"
    assert any("binary" in d for d in res.details)


def test_clean_keeps_the_binary_when_a_component_started_mid_flight(tmp_path, monkeypatch):
    """`clean` retires the artifact FORCEFULLY, so it must happen only after the authoritative
    post-lock running recheck — otherwise a start that slipped in loses its binary and the
    clean then aborts with nothing else done (audit finding)."""
    from lhpc.core.model import RunState
    svc = _svc(tmp_path, monkeypatch)
    _lay_down(svc, tmp_path, stack="daemon")
    proof = tmp_path / svc.binary_spec("daemon").proof_paths[0]
    real = svc.build_snapshot

    def _started(fresh=False):
        snap = real(fresh=fresh)
        if fresh:                                  # the UNDER-LOCK read sees it running
            for ss in snap.stacks:
                for cid, cs in ss.components.items():
                    if cid == "loraham-daemon":
                        cs.run_state = RunState.RUNNING
        return snap
    monkeypatch.setattr(svc, "build_snapshot", _started)
    res = svc.clean("daemon", apply=True, purge=True)
    assert not res.ok and "started while" in res.summary
    assert proof.exists()                          # ZERO mutation
    assert brx.receipt_state(svc._paths, "daemon")[0] == "valid"


# --- crash atomicity across files, receipt AND auth ----------------------------------------------

def test_interrupted_install_restores_the_mesh_password(tmp_path, monkeypatch):
    """The install switches meshcom to open auth BEFORE downloading. The journal is opened
    first and carries the previous value, so an interrupted run puts password auth back —
    the crash used to leave the bridge open with nothing to recover from (audit finding)."""
    from lhpc.core import binary_install as bi
    svc = _svc(tmp_path, monkeypatch)
    r = svc.hmac_set_secret("meshcom", "enable")
    assert r.ok, r.summary
    before = svc._resolved_param_value("meshcom", "run",
                                       svc._hmac_component("meshcom").id, "password_file")
    assert before
    # exactly what binary_install does before touching anything else
    bi.open_txn(svc._paths, "meshcom", "txnH", old_receipt=None,
                auth={"param": "password_file", "previous": before})
    assert svc.save_config_bundle("meshcom", values={"password_file": ""},
                                  _allow_managed_params=frozenset({"password_file"})).ok
    svc.invalidate_snapshot()
    assert svc._resolved_param_value("meshcom", "run",
                                     svc._hmac_component("meshcom").id, "password_file") == ""
    ok, why = svc.binary_recover()                    # …the box comes back up
    assert ok, why
    assert svc._resolved_param_value("meshcom", "run",
                                     svc._hmac_component("meshcom").id,
                                     "password_file") == before
    assert bi.read_journal(svc._paths)[1] == "absent"


def test_committed_transaction_keeps_open_auth(tmp_path, monkeypatch):
    """Past the commit point the NEW install is the truth: recovery must NOT put the password
    back (the installed firmware has none)."""
    from lhpc.core import binary_install as bi
    svc = _svc(tmp_path, monkeypatch)
    assert svc.hmac_set_secret("meshcom", "enable").ok
    prev = svc._resolved_param_value("meshcom", "run",
                                     svc._hmac_component("meshcom").id, "password_file")
    bi.open_txn(svc._paths, "meshcom", "txnI", auth={"param": "password_file",
                                                     "previous": prev})
    assert svc.save_config_bundle("meshcom", values={"password_file": ""},
                                  _allow_managed_params=frozenset({"password_file"})).ok
    j, _st = bi.read_journal(svc._paths)
    assert bi.write_journal(svc._paths, {**j, "state": "committed"})
    ok, why = svc.binary_recover()
    assert ok, why
    svc.invalidate_snapshot()
    assert svc._resolved_param_value("meshcom", "run",
                                     svc._hmac_component("meshcom").id, "password_file") == ""
