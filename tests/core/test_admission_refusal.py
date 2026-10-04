"""An operation that task admission turns away names its remedy (docs/operations.md, the refused
update row): the self-update recovery while an uninstall or a self-update is in flight, else a
"nothing to run here" line naming what to do. Driven through the real admission gate."""

from __future__ import annotations

import pytest

from remedy_contract import NOTHING_TO_RUN
from lhpc.core import updater_units
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService


def _svc(tmp_path):
    (tmp_path / "state").mkdir()
    return ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))


def _uninstalling(svc, monkeypatch):
    svc._paths.under(updater_units.UNINSTALL_GUARD).write_text("")


def _update_pending(svc, monkeypatch):
    monkeypatch.setattr(ControllerService, "classify_request", lambda self: "pending")


def _unverifiable(svc, monkeypatch):
    def unreadable(self):
        raise OSError("the request marker is unreadable")
    monkeypatch.setattr(ControllerService, "classify_request", unreadable)


def _power_pending(svc, monkeypatch):
    # Stubbed: the power gate's own marker reading (boot id, uptime TTL) is not this test's
    # subject; its (reason, tag) answer is what admission turns into the refusal.
    monkeypatch.setattr(ControllerService, "_power_pending_blocked",
                        lambda self: ("a reboot is pending — refusing new work", "power-pending"))


@pytest.mark.parametrize("setup, tag, command", [
    (_uninstalling, "uninstalling", "lhpc self-update --recover-request"),
    (_update_pending, "pending", "lhpc self-update --recover-request"),
    (_unverifiable, "unverifiable", None),
    (_power_pending, "power-pending", None),
])
def test_an_admission_refusal_names_its_remedy(tmp_path, monkeypatch, setup, tag, command):
    svc = _svc(tmp_path)
    setup(svc, monkeypatch)
    res = svc.update("kiss", apply=True)
    assert not res.ok and res.data["admission_blocked"] == tag
    if command:
        assert res.next_commands == [command]
    else:
        assert res.next_commands == [] and NOTHING_TO_RUN in res.details[0]
