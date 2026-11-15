"""Through the real start (Q-B2-7, B2a N3): a stack whose required callsign-style step warns on a deaf console starts,
its node keeps running, the run is not FAILED and its details name the unconfirmed setting, the poststart command and
the post-start log; MeshCom's guest gets run.sh's longer provisioning deadline in its launch environment."""
from __future__ import annotations

import os
import socket
from pathlib import Path

import pytest
import radios_fixture

from lhpc.core import commands
from lhpc.core.lifecycle import Lifecycle
from lhpc.core.paths import Paths
from lhpc.core.probes import RealSystem
from lhpc.core.probes.backends import FakeSystem, Listener
from lhpc.core.services import ControllerService

MANIFEST = Path(__file__).resolve().parents[2] / "lhpc" / "data" / "manifest.example.toml"


def _closed_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _kiss_box(tmp_path, step):
    """The kiss stack over a ready 433 daemon, its TNC built and carrying one required post-step `step`."""
    text = MANIFEST.read_text()
    anchor = 'id = "loraham-kiss-tnc"\n'
    assert text.count(anchor) == 1
    fields = ", ".join(f"{k} = {v!r}".replace("'", '"') if isinstance(v, str) else f"{k} = {str(v).lower()}"
                       for k, v in step.items())
    mf = tmp_path / "manifest.toml"
    mf.write_text(text.replace(anchor, anchor + f"  post_steps = [ {{ {fields} }} ]\n"))
    (tmp_path / "src" / "loraham-kiss-tnc").mkdir(parents=True)
    (tmp_path / "src" / "loraham-kiss-tnc" / "loraham-kiss-tnc").write_text("#bin")
    radios_fixture.write(tmp_path)
    sys_ = FakeSystem(listeners=[Listener(family="ipv4", ip="127.0.0.1", port=8001, inode=1)],
                      unix_replies=radios_fixture.by_radio({"/tmp/loraconf-r1.sock":
                                                            b"STATUS RADIO=READY TXMODE=MANAGED\n"})).system
    return ControllerService(system=sys_, paths=Paths(runtime_root=tmp_path), manifest_path=mf)


@pytest.mark.slow
@pytest.mark.needs_session
def test_a_deaf_console_leaves_the_node_running_and_the_start_names_the_poststart(tmp_path, monkeypatch, set_call,
                                                                                real_spawn):
    svc = _kiss_box(tmp_path, {"kind": "tcp_send", "port": _closed_port(), "label": "callsign", "required": True,
                               "data": "--setcall X\n", "repeat": 2, "interval": 0, "on_exhausted": "warn"})
    set_call(svc)
    # the lifecycle runs the REAL post-start launcher (RealSystem's runner) beside a real stand-in process
    life = Lifecycle(svc._paths, svc.stacks(), svc.config(), RealSystem(), spawn=real_spawn)
    monkeypatch.setattr(ControllerService, "_lifecycle", lambda self: life)
    res = svc.start("kiss", apply=True)
    tnc = [r for r in res.results if r.component == "loraham-kiss-tnc"]
    assert res.ok and tnc and tnc[0].outcome.value == "verified", (res.summary, res.details)
    text = "\n".join(res.details)
    assert "callsign NOT applied" in text and "lhpc stack poststart" in text and "(log " in text, text
    rec = next(r for r in life.owned_records("loraham-kiss-tnc") if r.get("role") == "")
    try:
        os.kill(rec["pid"], 0)                                   # the node was NOT stopped
    finally:
        svc.stop("kiss", apply=True)


@pytest.mark.needs_session
def test_meshcoms_guest_starts_with_the_longer_provisioning_deadline(tmp_path, monkeypatch):
    radios_fixture.write(tmp_path)
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    (tmp_path / "src" / "meshcom-qemu-raspi").mkdir(parents=True)
    seen = []

    def spawn(argv, log, cwd=None, env=None):
        seen.append(dict(env or {}))
        raise OSError("spawn refused by the test: the environment is what it checks")
    life = Lifecycle(svc._paths, svc.stacks(), svc.config(), svc._system, spawn=spawn)
    stack = svc.stack("meshcom")
    res = life.start(stack, stack.component("meshcom-qemu"), {}, band="433", inst=commands.Instance("r1", band="433"))
    assert not res.ok and seen, res
    assert seen[-1].get("PROVISION_TIMEOUT") == "1200"
