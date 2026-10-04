"""The board chosen on the console is the board the daemon launches for.

`POST /hardware` saves `[radio].hardware`; its effect is what the next daemon start spawns: one
`loraham_daemon` per band the setup serves, each with that band's `--hw` preset (the SPI board
wiring), and nothing for a band the setup does not serve. An unknown setup, or a POST without the
CSRF token, changes nothing.
"""
from __future__ import annotations

import pytest

from lhpc.core.lifecycle import Lifecycle
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService
from seams import seed_built

pytestmark = [pytest.mark.contract, pytest.mark.needs_session]


@pytest.fixture
def box(tmp_path, web, monkeypatch, real_spawn):
    """The console over a box whose daemon is built; returns (client, launched) — `launched`
    lists the (radio, hw) of every daemon a start spawns. The spawn is the seam: it records the
    argv and launches the suite's harmless stand-in process instead (reaped at session end)."""
    seed_built(tmp_path, "loraham-daemon/loraham_daemon/loraham_daemon")
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    launched = []

    def spawn(self, argv, log_path, cwd=None, env=None):
        if "--radio" in argv:
            launched.append((argv[argv.index("--radio") + 1], argv[argv.index("--hw") + 1]))
        return real_spawn(argv, log_path, cwd, env)
    monkeypatch.setattr(Lifecycle, "_real_spawn", spawn)
    return web(service_factory=lambda: svc), svc, launched


def _choose(client, csrf, setup, *, token=True):
    form = {"hardware": setup}
    if token:
        form["_csrf"] = csrf(client)
    return client.post("/hardware", data=form)


def _start(svc, launched):
    launched.clear()
    svc.start("daemon", apply=True)     # the stand-in never opens a CONF socket: not verified
    return sorted(launched)


@pytest.mark.parametrize("setup,daemons", [
    ("waveshare-433", [("433", "waveshare-sx1262")]),
    ("uputronics-868", [("868", "uputronics-ce1")]),
    ("uputronics", [("433", "uputronics-ce0"), ("868", "uputronics-ce1")]),
])
def test_the_chosen_setup_is_what_the_daemon_launches(box, csrf, setup, daemons):
    client, svc, launched = box
    assert _choose(client, csrf, setup).status_code in (302, 303)
    assert svc.hardware_setup() == setup
    assert _start(svc, launched) == daemons


@pytest.mark.parametrize("token", [True, False], ids=["unknown-setup", "no-csrf"])
def test_a_refused_choice_changes_nothing(box, csrf, tmp_path, token):
    client, svc, launched = box
    assert _choose(client, csrf, "waveshare-433").status_code in (302, 303)
    before = (tmp_path / "config" / "local.toml").read_bytes()
    r = _choose(client, csrf, "nosuchboard" if token else "uputronics", token=token)
    assert r.status_code == (302 if token else 400)
    assert (tmp_path / "config" / "local.toml").read_bytes() == before
    assert _start(svc, launched) == [("433", "waveshare-sx1262")]
