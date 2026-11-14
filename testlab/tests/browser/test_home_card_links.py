"""A23: a radio card's instance row links its own Settings and web UI, and the config link opens that radio's Settings
(Home served by the console app, meshcom@r1 running with its web listener up)."""
from lhpc.core import commands, instances
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem, Listener
from lhpc.core.services import ControllerService


def test_an_instances_config_link_opens_its_radios_settings(flask_page, tmp_path):
    plain = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    comp = plain.stack("meshcom").component("meshcom-qemu")
    opt, val = instances._token(plain, comp, commands.Instance("r1", band="433"), "433")
    page, _posts = flask_page(path="/", cmdlines={1000: [comp.process.exec_name, opt, val]},
                              listeners=[Listener("ipv4", "127.0.0.1", 18083, 1), Listener("ipv4", "127.0.0.1", 12323, 2)])
    row = page.locator('div.runstack:has(p[data-stack="meshcom@r1"])')
    assert row.count() == 1                                    # Home is rendered by the server: no wait needed
    assert [a.get_attribute("href") for a in row.locator("a.iface-web").all()] == ["http://127.0.0.1:18083"]
    row.locator("a.logslink", has_text="config").click()
    page.wait_for_url("**/stacks?cfg=meshcom&radio=r1#stack-settings-meshcom")
    assert page.locator("#stack-settings-meshcom").is_visible()
    page.context.close()
