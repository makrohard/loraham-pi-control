"""F18 in a real browser: a radio card's stack blocks and component boxes keep the viewer's open/closed choice across a
reload (dash.js, localStorage); a fresh browser shows the defaults (daemon stack closed, other stacks open, boxes closed)."""
from lhpc.core import instances, radios
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem, Listener
from lhpc.core.services import ControllerService


def _meshcore_r3(tmp_path):
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    (tmp_path / "config" / "radios.toml").write_text(radios.render((
        radios.Radio("r1", "low", "loraham", "433"), radios.Radio("r3", "mstick", "meshstick-1262", "868",
                                                                  usb_serial="MSTICK01"))))
    plain = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    inst, table = instances.parse_target(plain, "meshcore@r3", "start")[1], {}
    comp = plain.stack("meshcore").component("meshcore-node")
    opt, val = instances._token(plain, comp, inst, "868")
    table[700] = [comp.process.exec_path or comp.process.exec_name, *(("-m", comp.process.entry) if comp.process.entry else ()),
                  *[a for a in comp.process.all_args if a != opt], opt, val]
    return table


def test_stack_and_box_states_are_remembered_across_a_reload(flask_page, tmp_path):
    page, _posts = flask_page(path="/", cmdlines=_meshcore_r3(tmp_path), listeners=[Listener("ipv4", "127.0.0.1", 5399, 1)])
    stack = page.locator("details#stk-r3-meshcore\\@r3")
    box = page.locator("details#cbox-r3-meshcore\\@r3-meshcore-node")
    assert stack.evaluate("d => d.open") and not box.evaluate("d => d.open")              # the defaults
    box.locator("> summary").click(position={"x": 6, "y": 8})                             # open the box
    page.wait_for_function("() => localStorage.getItem('lhpc:cbox-r3-meshcore@r3-meshcore-node') === '1'")
    stack.locator("> summary").click(position={"x": 6, "y": 8})                           # close the stack
    page.wait_for_function("() => localStorage.getItem('lhpc:stk-r3-meshcore@r3') === '0'")
    page.reload()
    assert not page.locator("details#stk-r3-meshcore\\@r3").evaluate("d => d.open")
    assert page.locator("details#cbox-r3-meshcore\\@r3-meshcore-node").evaluate("d => d.open")
    page.evaluate("() => localStorage.clear()")                                          # a fresh browser
    page.reload()
    assert page.locator("details#stk-r3-meshcore\\@r3").evaluate("d => d.open")
    assert not page.locator("details#cbox-r3-meshcore\\@r3-meshcore-node").evaluate("d => d.open")
    page.context.close()


def test_running_on_and_start_a_stack_folds_persist_independently(flask_page, tmp_path):
    # F30 (his words): both boxes fold, open by default; each keeps its own state across a reload
    page, _posts = flask_page(path="/", cmdlines=_meshcore_r3(tmp_path), listeners=[Listener("ipv4", "127.0.0.1", 5399, 1)])
    run, start = page.locator("details#runon-r3"), page.locator("details#starton-r3")
    assert run.evaluate("d => d.open") and start.evaluate("d => d.open")
    run.locator("> summary").click(position={"x": 6, "y": 8})
    page.wait_for_function("() => localStorage.getItem('lhpc:runon-r3') === '0'")
    page.reload()
    assert not page.locator("details#runon-r3").evaluate("d => d.open")
    assert page.locator("details#starton-r3").evaluate("d => d.open")                 # the other keeps its own state
    page.context.close()


def test_a_tabs_older_snapshot_never_overwrites_a_newer_choice_from_another_tab(flask_page, tmp_path):
    # tab B shows "Start a stack" away from its rendered default; tab A then sets it back to the default (the newer
    # choice); B's signature reload restores B's older snapshot (it wins in B), and that restore's toggle event never
    # stores it: B's next ordinary reload shows A's newer choice. Holds for either rendered default.
    a, _posts = flask_page(path="/", cmdlines=_meshcore_r3(tmp_path), listeners=[Listener("ipv4", "127.0.0.1", 5399, 1)])
    box, key = "details#starton-r3", "lhpc:starton-r3"
    default = a.locator(box).evaluate("d => d.open")
    old, new = ("0", "1") if default else ("1", "0")
    a.locator(f"{box} > summary").click(position={"x": 6, "y": 8})                       # A: away from the default
    a.wait_for_function(f"() => localStorage.getItem('{key}') === '{old}'")
    b = a.context.new_page()
    b.goto(a.url)
    assert b.locator(box).evaluate("d => d.open") == (not default)                       # B shows A's first choice
    a.locator(f"{box} > summary").click(position={"x": 6, "y": 8})                       # A: back, the newer choice
    a.wait_for_function(f"() => localStorage.getItem('{key}') === '{new}'")
    b.evaluate("(old) => { sessionStorage.setItem('dashDetails', JSON.stringify({'starton-r3': old}));"
               " location.reload(); }", int(old))                                       # dash.js's signature reload
    b.wait_for_load_state("load")
    b.wait_for_function(f"() => document.querySelector('{box}').open === {'false' if default else 'true'}")
    b.wait_for_timeout(300)                                                              # its toggle event has run
    assert b.evaluate(f"() => localStorage.getItem('{key}')") == new                     # the newer choice stays
    b.reload()
    assert b.locator(box).evaluate("d => d.open") == default                             # B shows A's newer choice
    a.context.close()
