"""The 0.13.x board choice on the console at stage L5: `POST /hardware` still saves `[radio].hardware`, but the
daemon launches what the hardware list (`config/radios.toml`) holds: one `loraham_daemon` per radio of a band the
setup serves, with the radio's model's `--hw` preset, and nothing for a band without a radio. An unknown setup, or a
POST without the CSRF token, changes nothing. (The Hardware section of B5 replaces the board choice.)
"""
from __future__ import annotations

import pytest
import radios_fixture

from lhpc.core import radios
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
    radios_fixture.write(tmp_path, (radios.Radio("r1", "low", "waveshare-sx1262", "433"),))   # the box's one radio
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
    ("uputronics-868", []),                                       # no radio on 868 in the list: nothing
    ("uputronics", [("433", "waveshare-sx1262")]),                # the list's radio and model, not the board's
])
def test_the_daemon_launches_the_hardware_lists_radios_of_the_served_bands(box, csrf, setup, daemons):
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


def test_the_old_board_choice_says_the_hardware_list_decides(box):
    # B4-D26 (stage L5): the page states, in plain words, that the hardware list decides what launches.
    client, _svc, _launched = box
    html = client.get("/stacks/daemon", follow_redirects=True).get_data(as_text=True)
    assert ("The hardware list decides which radios launch. This page's board choice only names the served bands "
            "until the Hardware section replaces it.") in " ".join(html.split())
    assert "preset each radio launches with" not in html
