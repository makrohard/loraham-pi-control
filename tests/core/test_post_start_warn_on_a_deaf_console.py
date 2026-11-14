"""A required tcp_send step with `on_exhausted = "warn"` (Q-B2-7, MeshCom's callsign): when the console never answers
within its window, the step records `exhausted`, writes its warning and passes, so the start keeps the node running
and its summary names the unconfirmed callsign, the poststart command and the post-start log. A required step
without the field still fails the start; the manifest admits the field on tcp_send only, with the one value "warn"."""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys

import pytest

from lhpc.core import commands
from lhpc.core.config import Config, OperatorConfig
from lhpc.core.lifecycle import Lifecycle
from lhpc.core.manifest import ManifestError, load_manifest, parse_manifest
from lhpc.core.model import Component, ComponentKind, Stack
from lhpc.core.paths import Paths
from lhpc.core.probes import RealSystem
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService


def _closed_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _step(**kw):
    return {"kind": "tcp_send", "port": _closed_port(), "label": "callsign", "data": "--setcall X\n",
            "required": True, "repeat": 2, "interval": 0, **kw}


def _run(tmp_path, step):
    root = tmp_path / "rt"
    (root / "state" / "post").mkdir(parents=True)
    rp = root / "state" / "post" / "r.json"

    class _C:
        run_params = []

    class _Op:
        callsign = "N0CALL"
    script = commands.render_post_launcher([step], _C(), {}, _Op(), str(root), "/src", "", result_path=str(rp))
    path = tmp_path / "launcher.py"
    path.write_text(script)
    cp = subprocess.run([sys.executable, str(path)], timeout=60, capture_output=True, text=True)
    return cp, json.loads(rp.read_text())["steps"][0]


def test_a_deaf_console_warns_and_the_required_step_passes(tmp_path):
    cp, st = _run(tmp_path, _step(on_exhausted="warn"))
    assert cp.returncode == 0, cp.stderr
    assert st["outcome"] == "exhausted" and st["attempts"] == 2
    assert "all 2 attempt(s) failed" in cp.stderr


def test_a_required_step_without_the_field_still_fails(tmp_path):
    cp, st = _run(tmp_path, _step())
    assert cp.returncode != 0 and st["outcome"] == "exhausted"


@pytest.mark.needs_session
def test_the_start_summary_names_the_unconfirmed_callsign_the_command_and_the_log(tmp_path, monkeypatch):
    life = Lifecycle(Paths(runtime_root=tmp_path), (), Config(operator=OperatorConfig()), RealSystem())
    st = life._proc_identity(os.getpid()) or {}
    monkeypatch.setattr(life, "_binding_for", lambda cid, band, inst=None: {
        "main_launch_id": "test-main", "main_pid": os.getpid(), "main_starttime": int(st.get("starttime", 1)),
        "main_pgid": int(st.get("pgid", os.getpgrp())), "main_sid": int(st.get("sid", os.getsid(0)))})
    comp = Component(id="c", name="c", kind=ComponentKind.SERVICE, readiness="process", run_argv=("true",),
                     post_steps=(_step(on_exhausted="warn"),))
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    ok, summary = svc._run_post_start(life, Stack(id="s", name="s", main="c"), comp, {}, "")
    assert ok is True, summary
    assert "callsign NOT applied" in summary and "lhpc stack poststart" in summary, summary
    assert "(log " in summary and "post-c-" in summary, summary


def _mf(step):
    return {"stack": [{"id": "s", "main": "a", "component": [
        {"id": "a", "name": "a", "post_steps": [step]}]}]}


@pytest.mark.parametrize("step", [
    {"kind": "tcp_wait", "port": 1, "on_exhausted": "warn"},                       # another kind
    {"kind": "tcp_send", "port": 1, "data": "x\n", "on_exhausted": "fail"},        # another value
])
def test_the_manifest_admits_on_exhausted_on_tcp_send_with_warn_only(step):
    with pytest.raises(ManifestError, match="on_exhausted"):
        parse_manifest(_mf(step))
    ok = {"kind": "tcp_send", "port": 1, "data": "x\n", "on_exhausted": "warn"}
    assert parse_manifest(_mf(ok))


def test_meshcoms_callsign_step_warns():
    meshcom = next(s for s in load_manifest() if s.id == "meshcom")
    steps = [p for c in meshcom.components for p in c.post_steps if p.get("label") == "callsign"]
    assert [p.get("on_exhausted") for p in steps] == ["warn"], steps
