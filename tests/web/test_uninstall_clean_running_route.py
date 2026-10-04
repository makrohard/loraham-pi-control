"""Uninstall and clean from the console refuse while the stack runs, and remove nothing.

The widest seam of the P0.5 promise: a confirmed `POST /action` (`op=uninstall`, and `op=clean`
with the typed stack id) for a running kiss stack whose source is a registered, identity-proven
LHPC adoption — so the ONLY thing that keeps the checkout is the running refusal. The same POST
with the stack stopped removes it (the control case), so a refusal that held for another reason
could not pass here.
"""
from __future__ import annotations

import os
import time

import pytest

from lhpc.core import source_registry
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import CommandResult, FakeSystem
from lhpc.core.services import ControllerService

pytestmark = [pytest.mark.contract, pytest.mark.safety("P0.5")]

SRC = "src/loraham-kiss-tnc"
REMOTE = "https://github.com/makrohard/loraham-kiss-tnc.git"
RUNNING = {555: ["loraham-kiss-tnc"]}


def _client(tmp_path, web, cmdlines):
    """kiss's source checked out, recorded as LHPC's adoption, and answering the identity queries
    with its canonical remote; `cmdlines` is the host's process table."""
    (tmp_path / SRC).mkdir(parents=True)
    assert source_registry.write_record(
        Paths(runtime_root=tmp_path),
        source_registry.RegistryRecord(SRC, "", "pinned", "", time.time(), "",
                                       ("loraham-kiss-tnc", "loraham-kiss-serial")))
    svc = ControllerService(system=FakeSystem(cmdlines_data=cmdlines).system,
                            paths=Paths(runtime_root=tmp_path))
    real_run, dest = svc._system.runner.run, os.path.realpath(tmp_path / SRC)

    def run(argv, timeout, *a, **k):
        if (list(argv[:2]) == ["git", "-C"] and os.path.realpath(argv[2]) == dest
                and list(argv[3:]) == ["config", "--get", "remote.origin.url"]):
            return CommandResult(0, REMOTE + "\n", "")
        return real_run(argv, timeout, *a, **k)
    svc._system.runner.run = run
    return web(service_factory=lambda: svc)


def _post(client, csrf, op):
    form = {"_csrf": csrf(client), "op": op, "target": "kiss", "confirmed": "yes"}
    if op == "clean":
        form["confirm_text"] = "kiss"
    r = client.post("/action", data=form)
    assert r.status_code in (302, 303)
    with client.session_transaction() as sess:
        return sess.get("_flashes", [])


@pytest.mark.parametrize("op", ["uninstall", "clean"])
def test_a_running_stack_is_refused_and_keeps_its_source(tmp_path, web, csrf, op):
    flashes = _post(_client(tmp_path, web, RUNNING), csrf, op)
    assert (tmp_path / SRC).is_dir()
    assert source_registry.read_record(Paths(runtime_root=tmp_path), SRC) is not None
    assert [cat for cat, _msg in flashes] == ["warn"]
    assert "running" in flashes[0][1].lower()


@pytest.mark.parametrize("op", ["uninstall", "clean"])
def test_the_same_post_for_a_stopped_stack_removes_the_source(tmp_path, web, csrf, op):
    _post(_client(tmp_path, web, {}), csrf, op)
    assert not (tmp_path / SRC).exists()
