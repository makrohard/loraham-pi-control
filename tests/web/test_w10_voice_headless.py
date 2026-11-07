"""Week item 10 (W205): the console's Build of the voice stack on a box without GTK builds no GTK app.

`lhpc build voice` dropped the GTK app on a headless box (its GUI preflight), but the console's Build is a
detached job per component (spawn_web_job) that never asked: on the Zero it ran the GTK app's step, which
failed in pkg-config ("Package gtk+-3.0 was not found"). The console's Build now takes the same GUI decision:
a gui_optional or optional component whose GUI toolkit is absent is skipped and named, a mandatory one refuses.

Driven through POST /action op=build confirmed; the job spawn is recorded at its seam (Lifecycle.spawn_job),
answering "could not start" so that nothing runs and the PRIMARY job's name is what the console chose first.
"""
from __future__ import annotations

import pytest

from lhpc.core.lifecycle import Lifecycle
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService

GTK = "/usr/include/gtk-3.0/gtk/gtk.h"
TERMINAL_HEADERS = ("/usr/include/codec2/codec2.h", "/usr/include/alsa/asoundlib.h", "/usr/include/ncurses.h")


def _console(web, tmp_path, monkeypatch, *, gtk: bool):
    svc0 = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    src = tmp_path / svc0.stack("voice").main_component.source.path
    (src / ".git").mkdir(parents=True)
    present = {str(src), f"{src}/.git", *TERMINAL_HEADERS, *((GTK,) if gtk else ())}
    svc = ControllerService(system=FakeSystem(paths=present).system, paths=Paths(runtime_root=tmp_path))
    spawned = []

    def spawn_job(self, name, argv, cwd, env=None):
        spawned.append(name)
        return f"{name}.log", 0                         # "could not start": nothing runs, no secondary
    monkeypatch.setattr(Lifecycle, "spawn_job", spawn_job)
    return web(service_factory=lambda: svc), spawned


def _build(client, csrf, target):
    r = client.post("/action", data={"_csrf": csrf(client), "op": "build", "target": target, "confirmed": "yes"},
                    follow_redirects=True)
    assert r.status_code == 200, r.status_code
    return r.get_data(as_text=True)


def test_r1_without_gtk_the_console_build_spawns_no_gtk_app_job(web, csrf, tmp_path, monkeypatch):
    """RED before: the primary job was build-loraham-voice (the GTK app)."""
    client, spawned = _console(web, tmp_path, monkeypatch, gtk=False)
    _build(client, csrf, "voice")
    assert spawned == ["build-loraham-voice-cli"]


def test_r2_control_with_gtk_the_gtk_app_is_built_first(web, csrf, tmp_path, monkeypatch):
    client, spawned = _console(web, tmp_path, monkeypatch, gtk=True)
    _build(client, csrf, "voice")
    assert spawned == ["build-loraham-voice"]


def test_r3_without_gtk_building_the_gtk_app_alone_starts_nothing_and_says_why(web, csrf, tmp_path, monkeypatch):
    """RED before: it spawned the GTK app's job."""
    client, spawned = _console(web, tmp_path, monkeypatch, gtk=False)
    page = _build(client, csrf, "loraham-voice")
    assert spawned == []
    assert "GUI toolkit" in page


@pytest.mark.parametrize("gtk", [False, True], ids=["headless", "gtk"])
def test_r4_control_the_cli_build_and_the_console_build_take_the_same_components(tmp_path, gtk):
    from lhpc.core import web_build
    present = set(TERMINAL_HEADERS) | ({GTK} if gtk else set())
    svc = ControllerService(system=FakeSystem(paths=present).system, paths=Paths(runtime_root=tmp_path))
    comps = list(svc.stack("voice").components)
    keep, dropped, refused = web_build.split(svc, comps)
    skip = set(svc.gui_unavailable_components(svc.stack("voice")))
    assert {c.id for c in dropped} == {c.id for c in comps if c.id in skip}
    assert refused == [] and {c.id for c in keep} == {c.id for c in comps} - skip


def test_r5_the_skip_note_names_each_dropped_component():
    from types import SimpleNamespace as C

    from lhpc.core import web_build
    assert web_build.note([]) == ""
    msg = web_build.note([C(id="loraham-voice"), C(id="x-gui")])
    assert ("loraham-voice" in msg and "x-gui" in msg and web_build.HINT in msg)


def test_r6_control_the_test_op_is_unaffected(web, csrf, tmp_path, monkeypatch):
    """The GUI decision is the build's: host tests take no part in it (and must not trip over it)."""
    client, spawned = _console(web, tmp_path, monkeypatch, gtk=False)
    r = client.post("/action", data={"_csrf": csrf(client), "op": "test", "target": "voice", "confirmed": "yes"})
    assert r.status_code == 302


def test_r7_a_mandatory_component_without_its_gui_toolkit_refuses_the_build():
    """The shipped manifest has no mandatory GUI component, so a stand-in service carries one: split refuses it,
    and the console's round spawns nothing and says why."""
    from types import SimpleNamespace as C

    from lhpc.core import web_build
    gui, opt, plain = (C(id="gui-app", optional=False, gui_optional=False, build_steps=[1], build_requires=()),
                       C(id="gui-extra", optional=True, gui_optional=False, build_steps=[1], build_requires=()),
                       C(id="cli", optional=False, gui_optional=False, build_steps=[1], build_requires=()))
    stack = C(id="s", components=[gui, opt, plain])
    svc = C(stack_of=lambda cid: "s", stack=lambda sid: stack, is_built=lambda c: False,
            gui_unavailable_components=lambda st: ("gui-app", "gui-extra"))
    assert web_build.split(svc, [gui, opt, plain]) == ([plain], [opt], [gui])
    comps, note, why = web_build.round_for(svc, [gui, opt, plain], None)
    assert comps == [] and note == "" and "gui-app" in why and web_build.HINT in why


def test_r8_a_round_that_skips_a_gui_component_names_it():
    from types import SimpleNamespace as C

    from lhpc.core import web_build
    app = C(id="gtk-app", name="GTK app", optional=False, gui_optional=True, build_steps=[1], build_requires=())
    cli = C(id="cli", name="CLI", optional=False, gui_optional=False, build_steps=[1], build_requires=())
    stack = C(id="s", components=[app, cli])
    svc = C(stack_of=lambda cid: "s", stack=lambda sid: stack, is_built=lambda c: False,
            gui_unavailable_components=lambda st: ("gtk-app",))
    comps, note, why = web_build.round_for(svc, [app, cli], None)
    assert [c.id for c in comps] == ["cli"] and why == ""
    assert note == web_build.note([app]) and "gtk-app" in note
