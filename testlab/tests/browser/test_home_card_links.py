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
    row = page.locator('details.runstack:has(summary[data-stack="meshcom@r1"])')
    assert row.count() == 1                                    # Home is rendered by the server: no wait needed
    assert [a.get_attribute("href") for a in row.locator("a.webpill").all()] == ["http://127.0.0.1:18083"]
    row.locator('details.cbox:has(a.logslink[aria-label="meshcom-qemu config"]) > summary').click(position={"x": 6, "y": 8})   # F18: opened first (its marker, not the Web UI pill)
    row.locator('a.logslink[aria-label="meshcom-qemu config"]').click()   # K1: each part has its row and links
    page.wait_for_url("**/stacks?cfg=meshcom&radio=r1#stack-settings-meshcom")
    assert page.locator("#stack-settings-meshcom").is_visible()
    page.context.close()


def test_a_cards_rows_are_boxes_with_their_state_right_and_their_button_last_left(flask_page, tmp_path):
    # F18 (his word; was U2 layout B's button at the row's right edge): meshcore@r3 runs node + gps; its stopped web UI
    # has a Run. At 390 and 1280 px each row is a box whose header (name, state pill right) stays inside the viewport;
    # opened, its button is the box's last thing, left-aligned; nothing scrolls sideways.
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
        table[700 + len(table)] = [comp.process.exec_path or comp.process.exec_name, *(("-m", comp.process.entry) if comp.process.entry else ()),
                                       *[a for a in comp.process.all_args if a != opt], opt, val]
    page, _posts = flask_page(path="/", cmdlines=table, listeners=[Listener("ipv4", "127.0.0.1", 5399, 1)])
    block = page.locator('details.runstack:has(summary[data-stack="meshcore@r3"])')
    assert block.count() == 1
    run = block.locator('form.act:has(input[name="target"][value="meshcore-webui@r3"]) button')
    assert run.count() == 1 and run.text_content().strip() == "Run"          # the stopped web UI's own Run (box closed)
    for width in (390, 1280):
        page.set_viewport_size({"width": width, "height": 900})
        overflow = page.evaluate("() => document.documentElement.scrollWidth - document.documentElement.clientWidth")
        assert overflow <= 1, f"{overflow}px of horizontal overflow at {width}px"
        rows = block.locator("li.crow")
        assert rows.count() >= 4
        for i in range(rows.count()):
            row = rows.nth(i)
            head = row.locator("summary.shead").bounding_box()
            assert head["x"] + head["width"] <= width                                       # inside the viewport
            pill = row.locator("summary.shead .sstate").bounding_box()
            assert abs((pill["x"] + pill["width"]) - (head["x"] + head["width"])) <= 2, (width, i, pill, head)  # right
            box = row.locator("details.cbox")
            if not box.evaluate("d => d.open"):
                row.locator("summary.shead").click(position={"x": 6, "y": 8})              # opened to see its body
            buttons = row.locator("details.cbox > div.cact button")
            if buttons.count():
                b, sub = buttons.first.bounding_box(), row.locator("div.csub").bounding_box()
                assert b["y"] >= sub["y"] + sub["height"] - 1, (width, i, b, sub)           # below the body: last
                assert abs(b["x"] - sub["x"]) <= 4, (width, i, b, sub)                      # left-aligned
    page.context.close()
