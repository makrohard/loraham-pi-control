"""Switching MeshCom between the binary and the source channel from the console.

The widest seam: `POST /action op=install source=binary|pinned` (confirmed) starts a detached
install job; the job is the `lhpc install … --source <channel>` command the route put on its
argv. Each case captures that argv at the job spawn and runs exactly that command in-process
(network collaborators stubbed: the index, the download, the clone), then observes what the switch
promises: on the binary channel a valid receipt and open auth (the published firmware has no mesh
password); switching back to source retires the receipt and puts the mesh password back; a switch
whose adoption fails restores the binary install from disk — receipt, files and open auth — and
leaves no transaction open. "From disk" is proven, not assumed: for the failing switch the index,
the download and the two HTTP seams under them refuse, so a restore that reached for the network
would fail it.
"""
from __future__ import annotations

import os

import pytest

from lhpc.adapters.cli import main as cli_main
from lhpc.core import binary_install as bi
from lhpc.core.install import Installer
from lhpc.core.lifecycle import Lifecycle
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService

pytestmark = pytest.mark.contract


@pytest.fixture
def box(tmp_path, monkeypatch, web, stub_pipeline):
    """MeshCom with its mesh password set, the console over it, and the binary pipeline local:
    returns (client, svc)."""
    monkeypatch.setattr(ControllerService, "binary_target", lambda self: "aarch64-trixie")
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    assert svc.hmac_set_secret("meshcom", "enable").ok
    spec = svc.binary_spec("meshcom")
    files = sorted({*spec.proof_paths, *(next(iter(a)) for a in spec.probes)})
    stub_pipeline(svc, download=lambda entry, path: None)

    def stage(tar, stage_dir, roots):
        for rel in files:
            os.makedirs(os.path.dirname(os.path.join(stage_dir, rel)), exist_ok=True)
            with open(os.path.join(stage_dir, rel), "w") as fh:
                fh.write("artifact")
        return files
    monkeypatch.setattr(bi, "validate_and_extract", stage)
    monkeypatch.setattr(bi, "run_probe", lambda paths, argv: "ok")
    monkeypatch.setattr(ControllerService, "_binary_provision", lambda self, *a: [])
    monkeypatch.setattr(ControllerService, "install_dep_gate",
                        lambda self, target: {"block": [], "warn": []})
    monkeypatch.setattr(cli_main, "_print_install_dep_gate", lambda svc, stack, check=False: False)
    monkeypatch.setattr(cli_main, "ControllerService", lambda: svc)
    return web(service_factory=lambda: svc), svc


def _switch(client, csrf, monkeypatch, channel):
    """POST the confirmed install on `channel`; run the job command the route spawned."""
    jobs = []

    def spawn_job(self, name, argv, cwd, env=None):
        jobs.append(list(argv))
        return None, None                           # nothing detached: run in-process below
    monkeypatch.setattr(Lifecycle, "spawn_job", spawn_job)
    r = client.post("/action", data={"_csrf": csrf(client), "op": "install", "target": "meshcom",
                                     "source": channel, "confirmed": "yes"})
    assert r.status_code in (302, 303)
    (argv,) = jobs
    command = argv[argv.index("install"):]
    command = command[:command.index("--web-result")] if "--web-result" in command else command
    assert command[:2] == ["install", "meshcom"] and \
        command[command.index("--source") + 1] == channel
    return cli_main.main(command)


def _state(svc):
    """(receipt state, the bridge's mesh password setting, its listener's auth)."""
    svc.invalidate_snapshot()
    comp = svc._hmac_component("meshcom")
    st = svc.stack("meshcom")
    auth = next(svc._fw_resolve_scope(st, c, ep)["auth"] for c in st.components
                for ep in c.endpoints if ep.kind == "tcp" and ep.role == "listener" and ep.firewall)
    return (svc.binary_receipt_state("meshcom")[0],
            bool(svc._resolved_param_value("meshcom", "run", comp.id, "password_file")), auth)


def test_binary_then_back_to_source(box, csrf, monkeypatch, stub_adopt):
    client, svc = box
    monkeypatch.setattr(Installer, "adopt_source",
                        lambda self, comp, **k: type("A", (), {"status": "done", "detail": ""})())
    assert _switch(client, csrf, monkeypatch, "binary") == 0
    assert _state(svc) == ("valid", False, "none")
    stub_adopt(svc)
    assert _switch(client, csrf, monkeypatch, "pinned") == 0
    assert _state(svc) == ("absent", True, "password")
    assert bi.read_journal(svc._paths)[1] == "absent"


def test_a_failed_switch_to_source_restores_the_binary_install(box, csrf, monkeypatch, stub_adopt,
                                                                tmp_path):
    client, svc = box
    monkeypatch.setattr(Installer, "adopt_source",
                        lambda self, comp, **k: type("A", (), {"status": "done", "detail": ""})())
    assert _switch(client, csrf, monkeypatch, "binary") == 0
    proof = tmp_path / svc.binary_spec("meshcom").proof_paths[0]
    stub_adopt(svc, fail_paths=("src/MeshCom-Firmware",))
    network = []

    def refuse(what):
        def offline(*a, **k):
            network.append(what)
            raise bi.BinaryInstallError(f"no network in this phase: {what}")
        return offline
    for name in ("fetch_index", "index_entry", "download_artifact", "_http_get", "_open_stream"):
        monkeypatch.setattr(bi, name, refuse(name))
    assert _switch(client, csrf, monkeypatch, "pinned") != 0
    assert network == []                            # restored without asking the index or the net
    assert _state(svc) == ("valid", False, "none")
    assert proof.read_text() == "artifact"
    assert bi.read_journal(svc._paths)[1] == "absent"
    assert list((tmp_path / "state" / "binary").glob(".backup-*")) == []
