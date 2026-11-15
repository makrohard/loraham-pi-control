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
    row.locator('a.logslink[aria-label="meshcom-qemu config"]').click()   # K1: each part has its row and links
    page.wait_for_url("**/stacks?cfg=meshcom&radio=r1#stack-settings-meshcom")
    assert page.locator("#stack-settings-meshcom").is_visible()
    page.context.close()


def test_a_cards_rows_are_two_lines_with_their_button_on_the_right(flask_page, tmp_path):
    # U2 layout B: meshcore@r3 runs node + gps; its stopped web UI has a Run. At 390 and 1280 px each row is two lines
    # (state, name, button; then note/address/config/logs), its button at the row's right edge, nothing scrolls
    # sideways.
    from lhpc.core import radios
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    (tmp_path / "config" / "radios.toml").write_text(radios.render((
        radios.Radio("r1", "low", "loraham", "433"), radios.Radio("r3", "mstick", "meshstick-1262", "868",
                                                                  usb_serial="MSTICK01"))))
    plain = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    inst, table = instances.parse_target(plain, "meshcore@r3", "start")[1], {}
    for cid in ("meshcore-node", "meshcore-gps"):
        comp = plain.stack("meshcore").component(cid)
        opt, val = instances._token(plain, comp, inst, "868")
        table[700 + len(table)] = [comp.process.exec_name, *[a for a in comp.process.all_args if a != opt], opt, val]
    page, _posts = flask_page(path="/", cmdlines=table, listeners=[Listener("ipv4", "127.0.0.1", 5399, 1)])
    block = page.locator('div.runstack:has(p[data-stack="meshcore@r3"])')
    assert block.count() == 1
    run = block.locator('form.act:has(input[name="target"][value="meshcore-webui@r3"]) button')
    assert run.count() == 1 and run.inner_text() == "Run"                    # the stopped web UI's own Run
    for width in (390, 1280):
        page.set_viewport_size({"width": width, "height": 900})
        overflow = page.evaluate("() => document.documentElement.scrollWidth - document.documentElement.clientWidth")
        assert overflow <= 1, f"{overflow}px of horizontal overflow at {width}px"
        rows = block.locator("li.crow")
        assert rows.count() >= 4
        for i in range(rows.count()):
            row = rows.nth(i)
            head, sub = row.locator("div.chead").bounding_box(), row.locator("div.csub").bounding_box()
            assert sub["y"] >= head["y"] + head["height"] - 1, (width, i, head, sub)       # line 2 below line 1
            assert head["x"] + head["width"] <= width                                       # inside the viewport
            buttons = row.locator("div.chead .cact button")
            if buttons.count():
                b = buttons.first.bounding_box()
                assert abs((b["x"] + b["width"]) - (head["x"] + head["width"])) <= 2, (width, i, b, head)   # right
                assert row.locator("span.badge").bounding_box()["y"] < b["y"] + b["height"]  # same line as the state
    page.context.close()
