"""Dash plan c14: a Home card row's "password" link (meshcore-node of meshcore@r3) opens the Apps page on that stack and
radio with its Password section open (stacks_state.js opens the details the hash names)."""
from lhpc.core import instances, radios
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem, Listener
from lhpc.core.services import ControllerService


def test_the_password_link_opens_the_apps_password_section(flask_page, tmp_path):
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
    page, _posts = flask_page(path="/", cmdlines=table, listeners=[Listener("ipv4", "127.0.0.1", 5399, 1)])
    link = page.locator('div.runstack:has(p[data-stack="meshcore@r3"]) a.logslink[aria-label="meshcore-node password"]')
    assert link.count() == 1 and link.inner_text() == "password"
    link.click()
    page.wait_for_url("**/stacks?open=meshcore&radio=r3#stack-password-meshcore-node")
    section = page.locator("details#stack-password-meshcore-node")
    assert section.count() == 1
    page.wait_for_function("() => document.getElementById('stack-password-meshcore-node').open")
    assert section.is_visible()
    page.context.close()
