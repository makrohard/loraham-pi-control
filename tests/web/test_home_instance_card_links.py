"""A23 web part: an instance's component row on its radio's card links what the 0.13.x card linked (config, logs, the
web UI per viewer: local, LAN, client network; proxied or direct), each for the instance's own radio. Through the
rendered Home page, each assertion scoped to one instance's `data-stack="meshcom@<rid>"` block, so an orphan entry can
never satisfy it. Two instances run: meshcom@r1 and meshcom@r3, each with its own ports."""
import radios_fixture
from htmlq import parse
from seams import entry_args, exe

from lhpc.core import commands, config, instances, radios
from lhpc.core.lifecycle import Lifecycle
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem, Listener
from lhpc.core.services import ControllerService

ROWS = radios_fixture.RADIOS + (radios.Radio("r3", "rfm98 433", "ch341-rfm98pw-433", "433", usb_serial="CHE1W433"),)
WEB = {"r1": 18083, "r3": 18349}
CONSOLE = {"r1": 12323, "r3": 12589}
LOCAL = {"X-LHPC-Peer": "loopback", "Host": "127.0.0.1:8443"}
LAN = {"X-LHPC-Peer": "remote", "Host": "198.51.100.7"}
AP = {"X-LHPC-Peer": "remote", "Host": "10.42.0.1"}
SETTINGS = "/stacks?open=meshcom&cfg=meshcom&radio={}#stack-settings-meshcom"


def _svc(tmp_path, exposed=(), extra=()):
    """meshcom@r1 + meshcom@r3 running; a port in `exposed` listens on 0.0.0.0, the others on 127.0.0.1; `extra`:
    more listeners (a stackweb proxy's port, as (ip, port))."""
    radios_fixture.write(tmp_path, ROWS)
    paths = Paths(runtime_root=tmp_path)
    plain = ControllerService(system=FakeSystem().system, paths=paths)
    comp = plain.stack("meshcom").component("meshcom-qemu")
    table = {}
    for n, rid in enumerate(("r1", "r3")):
        opt, val = instances._token(plain, comp, commands.Instance(rid, band="433"), "433")
        table[1000 + n] = [comp.process.exec_name, opt, val]
    ports = [p for r in ("r1", "r3") for p in (WEB[r], CONSOLE[r])]
    listeners = [Listener("ipv4", "0.0.0.0" if p in exposed else "127.0.0.1", p, p) for p in ports]
    listeners += [Listener("ipv4", ip, port, port) for ip, port in extra]
    return ControllerService(system=FakeSystem(cmdlines_data=table, listeners=listeners).system, paths=paths)


def _remote(tmp_path):
    config.save_webserver_config(Paths(runtime_root=tmp_path), remote_exposed=True)   # an IP-literal Host passes


def _block(web, svc, headers, rid):
    r = web(service_factory=lambda: svc).get("/", headers=headers)
    assert r.status_code == 200
    doc = parse(r.get_data(as_text=True))
    (block,) = [doc.within(d) for d in doc.find("div", class_="runstack")
                if doc.within(d).find("p", **{"data-stack": f"meshcom@{rid}"})]
    return block


def _hrefs(block, cls):
    return [a["href"] for a in block.find("a", class_=cls)]


def _row(block, cid):
    """The row of component `cid` in an instance's block (K1: the block lists every component of the stack)."""
    (row,) = [block.within(li) for li in block.find("li")
              if block.within(li).find("a", **{"aria-label": f"{cid} logs"})]
    return row


def test_a_local_viewer_gets_each_instances_own_web_ui(tmp_path, web):
    svc = _svc(tmp_path)
    assert _hrefs(_block(web, svc, LOCAL, "r3"), "iface-web") == ["http://127.0.0.1:18349"]
    assert _hrefs(_block(web, svc, LOCAL, "r1"), "iface-web") == ["http://127.0.0.1:18083"]


def test_config_and_logs_name_the_instances_radio(tmp_path, web):
    block = _block(web, _svc(tmp_path), LOCAL, "r3")
    assert _hrefs(_row(block, "meshcom-qemu"), "logslink") == ["/stacks?cfg=meshcom&radio=r3#stack-settings-meshcom",
                                                               "/logs/meshcom-qemu?radio=r3"]
    assert all("radio=r3" in h for h in _hrefs(block, "logslink")), _hrefs(block, "logslink")   # its stopped parts too


def test_a_lan_viewer_gets_an_exposed_instance_at_the_host_it_reached(tmp_path, web):
    _remote(tmp_path)
    svc = _svc(tmp_path, exposed=(WEB["r3"],))
    assert _hrefs(_block(web, svc, LAN, "r3"), "iface-web") == ["http://198.51.100.7:18349/"]
    r1 = _block(web, svc, LAN, "r1")                                  # r1's listener stays on loopback
    assert _hrefs(r1, "iface-web") == [] and _hrefs(r1, "iface-local") == [SETTINGS.format("r1")]


def test_a_client_network_viewer_gets_the_access_point_address(tmp_path, web):
    _remote(tmp_path)
    svc = _svc(tmp_path, exposed=(WEB["r3"],))
    assert _hrefs(_block(web, svc, AP, "r3"), "iface-web") == ["http://10.42.0.1:18349/"]


def test_a_loopback_instance_seen_remotely_links_its_own_settings(tmp_path, web):
    _remote(tmp_path)
    svc = _svc(tmp_path)
    block = _block(web, svc, LAN, "r3")
    (local,) = block.find("a", class_="iface-local")
    assert local["href"] == SETTINGS.format("r3") and "local only" in local.text
    page = web(service_factory=lambda: svc).get(local["href"], headers=LAN)
    assert page.status_code == 200
    assert parse(page.get_data(as_text=True)).present("stack-settings-meshcom")


def _proxy(svc, page, port):
    r = svc.stack_web_configure(page, mode="local", port=port, scheme="http", access_mode="no-auth")
    assert r.ok, (r.summary, r.details)


def test_the_instances_own_proxied_page_decides_its_link(tmp_path, web):
    for listen, want_web, want_local in (
            (("0.0.0.0", 8470), ["http://127.0.0.1:8470/"], []),            # exposed, a local viewer
            (("127.0.0.1", 8470), ["http://127.0.0.1:8470/"], []),          # loopback, a local viewer
            (None, [], [SETTINGS.format("r3")])):                           # absent: Apply
        svc = _svc(tmp_path, extra=[listen] if listen else [])
        _proxy(svc, "meshcom@r3", 8470)
        block = _block(web, svc, LOCAL, "r3")
        assert (_hrefs(block, "iface-web"), _hrefs(block, "iface-local")) == (want_web, want_local), listen
        assert _hrefs(_block(web, svc, LOCAL, "r1"), "iface-web") == ["http://127.0.0.1:18083"]   # r1: direct


def test_a_proxied_instance_seen_remotely(tmp_path, web):
    _remote(tmp_path)
    for listen, want_web, label in ((("0.0.0.0", 8470), ["http://198.51.100.7:8470/"], None),
                                    (("127.0.0.1", 8470), [], "local only"),
                                    (None, [], "proxy not active — Apply")):
        svc = _svc(tmp_path, extra=[listen] if listen else [])
        _proxy(svc, "meshcom@r3", 8470)
        block = _block(web, svc, LAN, "r3")
        assert _hrefs(block, "iface-web") == want_web, listen
        if label:
            (local,) = block.find("a", class_="iface-local")
            assert local["href"] == SETTINGS.format("r3") and label in local.text, listen


def test_another_instances_proxied_page_does_not_decide_this_ones(tmp_path, web):
    _remote(tmp_path)
    svc = _svc(tmp_path, extra=[("0.0.0.0", 8471)])
    _proxy(svc, "meshcom@r1", 8471)                                   # r1 proxied and exposed, r3 not proxied
    assert _hrefs(_block(web, svc, LAN, "r1"), "iface-web") == ["http://198.51.100.7:8471/"]
    assert _hrefs(_block(web, svc, LAN, "r3"), "iface-local") == [SETTINGS.format("r3")]


def test_a_booting_instance_shows_the_badge_and_its_links(tmp_path, web, monkeypatch):
    # the plan's NOTE D: r3's post-start runner still works, its web listener is up -> "booting" + its links
    real = Lifecycle.owned_records

    def owned_records(self, comp_id, band=None, role="", rid=None):
        return [{"pid": 3000, "rid": "r3"}] if role == "post" and comp_id == "meshcom-qemu" else real(
            self, comp_id, band, role, rid)
    monkeypatch.setattr(Lifecycle, "owned_records", owned_records)
    monkeypatch.setattr(Lifecycle, "_original_ceased", lambda self, rec: False)
    block = _block(web, _svc(tmp_path), LOCAL, "r3")
    assert block.find("span", class_="badge-booting")
    assert _hrefs(block, "iface-web") == ["http://127.0.0.1:18349"]
    assert len(_hrefs(_row(block, "meshcom-qemu"), "logslink")) == 2


# --- K1 / layout B (U2): every part of a running instance on its radio's card, its own Run or Stop ------------------

R4 = radios.Radio("r4", "stick", "meshstick-1262", "868", usb_serial="MS123456")
MC_NODE = {"r2": 5133, "r4": 5399}
MC_WEB = {"r2": 8921, "r4": 9187}


def _shows_port(text, port):
    """The port as a card shows it (an address `:port`, the CLI's `-p port`): a bare number would also match the paths
    the cards carry (tmp_path's, the checkout's)."""
    return f":{port}" in text or f"-p {port}" in text


def _mc(tmp_path, running, ports=()):
    """meshcore on r2 and r4 (868): `running` {rid: the component ids running as that instance, by own argv token};
    `ports`: the 127.0.0.1 listeners that are up."""
    radios_fixture.write(tmp_path, (radios_fixture.R2_868, R4))
    paths = Paths(runtime_root=tmp_path)
    plain = ControllerService(system=FakeSystem().system, paths=paths)
    table = {}
    for rid, comps in running.items():
        inst = instances.parse_target(plain, f"meshcore@{rid}", "start")[1]
        for cid in comps:
            comp = plain.stack("meshcore").component(cid)
            opt, val = instances._token(plain, comp, inst, "868")
            table[700 + len(table)] = [exe(comp.process, comp.process.exec_name), *entry_args(comp.process), *[a for a in comp.process.all_args if a != opt], opt, val]
    fake = FakeSystem(cmdlines_data=table, listeners=[Listener("ipv4", "127.0.0.1", p, p) for p in ports])
    return ControllerService(system=fake.system, paths=paths)


def _mc_block(web, svc, rid, headers=LOCAL):
    """meshcore@<rid>'s block, looked for on <rid>'s own card only."""
    r = web(service_factory=lambda: svc).get("/", headers=headers)
    assert r.status_code == 200
    doc = parse(r.get_data(as_text=True))
    (card,) = [doc.within(d) for d in doc.find("div", class_="radio-col", **{"data-radio-rid": rid})]
    (block,) = [card.within(d) for d in card.find("div", class_="runstack")
                if card.within(d).find("p", **{"data-stack": f"meshcore@{rid}"})]
    return block


def _forms(scope):
    return [(f.field_default("op"), f.field_default("target")) for f in
            (scope.within(x) for x in scope.find("form", action="/action"))]


def test_a_stopped_web_ui_of_a_running_instance_has_its_row_and_run_on_its_card(tmp_path, web):
    # red-before (6b82239a): card r4 listed node + gps only; the stopped web UI had no row and no Run
    block = _mc_block(web, _mc(tmp_path, {"r4": ["meshcore-node", "meshcore-gps"]}, ports=[MC_NODE["r4"]]), "r4")
    assert "MeshCore Web UI" in block.text and ("start", "meshcore-webui@r4") in _forms(block), _forms(block)
    row = _row(block, "meshcore-webui")
    assert row.find("span", class_="badge-stopped") and "MeshCore Web UI" in row.text
    assert _forms(row) == [("start", "meshcore-webui@r4")]
    assert _hrefs(row, "logslink") == ["/stacks?cfg=meshcore&radio=r4#stack-settings-meshcore",
                                       "/logs/meshcore-webui?radio=r4"]
    assert _hrefs(row, "iface-web") == [] and _hrefs(row, "iface-local") == []          # stopped: no interface
    assert _forms(_row(block, "meshcore-gps")) == [("stop", "meshcore-gps@r4")]        # running optional: Stop
    assert _forms(_row(block, "meshcore-node")) == []                                  # the main: the stack's Stop
    assert _forms(block)[-2:] == [("stop", "meshcore@r4"), ("restart", "meshcore@r4")]  # W325, kept


def test_a_running_web_ui_has_its_stop_its_link_and_the_head_link(tmp_path, web):
    block = _mc_block(web, _mc(tmp_path, {"r4": ["meshcore-node", "meshcore-webui", "meshcore-gps"]},
                               ports=[MC_NODE["r4"], MC_WEB["r4"]]), "r4")
    row = _row(block, "meshcore-webui")
    assert _forms(row) == [("stop", "meshcore-webui@r4")]
    assert _hrefs(row, "iface-web") == [f"http://127.0.0.1:{MC_WEB['r4']}"]
    assert _hrefs(block, "headlink") == [f"http://127.0.0.1:{MC_WEB['r4']}"]
    head = block.within(block.find("p", **{"data-stack": "meshcore@r4"})[0])
    assert "Web UI ↗" in head.text and head.find("a", class_="headlink")


def test_the_head_has_no_web_ui_link_while_the_web_ui_is_stopped(tmp_path, web):
    block = _mc_block(web, _mc(tmp_path, {"r4": ["meshcore-node"]}, ports=[MC_NODE["r4"]]), "r4")
    assert _row(block, "meshcore-webui").find("span", class_="badge-stopped")           # anchor: the row is there
    assert _hrefs(block, "headlink") == [] and "Web UI ↗" not in block.text


def test_each_cards_rows_and_links_are_its_own_instances(tmp_path, web):
    # G2-N1: r2 runs node + web UI with its listeners; r4 runs its node only, listener absent
    svc = _mc(tmp_path, {"r2": ["meshcore-node", "meshcore-webui"], "r4": ["meshcore-node"]},
              ports=[MC_NODE["r2"], MC_WEB["r2"]])
    r2, r4 = _mc_block(web, svc, "r2"), _mc_block(web, svc, "r4")
    assert "MeshCore Web UI" in r4.text, r4.text                                        # r4's stopped part is listed
    assert _row(r2, "meshcore-webui").find("span", class_="badge-running")
    assert _row(r4, "meshcore-webui").find("span", class_="badge-stopped")
    assert _row(r2, "meshcore-node").find("span", class_="badge-running")
    assert _row(r4, "meshcore-node").find("span", class_="badge-degraded")
    assert f"127.0.0.1:{MC_NODE['r4']}" in _row(r4, "meshcore-node").text                # r4's own note
    assert f"tcp: 127.0.0.1:{MC_NODE['r2']}" in _row(r2, "meshcore-node").text            # r2's own address
    assert not _shows_port(r4.text, MC_NODE["r2"]) and not _shows_port(r2.text, MC_NODE["r4"])
    assert _hrefs(r2, "iface-web") == [f"http://127.0.0.1:{MC_WEB['r2']}"] and _hrefs(r4, "iface-web") == []
    assert _forms(_row(r2, "meshcore-webui")) == [("stop", "meshcore-webui@r2")]
    assert _forms(_row(r4, "meshcore-webui")) == [("start", "meshcore-webui@r4")]
    assert all("radio=r4" in h for h in _hrefs(r4, "logslink")) and all("radio=r2" in h for h in _hrefs(r2, "logslink"))


def test_an_interactive_part_keeps_its_banner_and_this_radios_command(tmp_path, web):
    block = _mc_block(web, _mc(tmp_path, {"r4": ["meshcore-node"]}, ports=[MC_NODE["r4"]]), "r4")
    assert "MeshCore CLI" in block.text, block.text                                     # its row is listed
    (row,) = [block.within(li) for li in block.find("li") if "MeshCore CLI" in li.text]
    assert "Interactive — run local:" in row.text and _forms(row) == []                 # never a Run for it
    assert [a["href"] for a in row.find("a") if "View" in a.text] == [
        "/stacks?open=meshcore&radio=r4#stackrow-meshcore"]                              # its blocker: this radio
    (box,) = row.find("pre")
    # this card's radio: its own lock and node port, never r2's (by those parts: the paths in the box are tmp_path's and
    # the checkout's, which may contain "r2" themselves)
    assert "/state/r4/" in box.text and f"-p {MC_NODE['r4']}" in box.text, box.text
    assert "/state/r2/" not in box.text and not _shows_port(box.text, MC_NODE["r2"]), box.text


def test_the_cards_run_and_stop_post_with_csrf_and_name_the_instance(tmp_path, web, monkeypatch):
    svc = _mc(tmp_path, {"r4": ["meshcore-node", "meshcore-gps"]}, ports=[MC_NODE["r4"]])
    calls, real = [], ControllerService.run_action

    def spy(self, op, target, *a, inst=None, **k):
        calls.append((op, target, inst.rid if inst is not None else None))
        return real(self, op, target, *a, inst=inst, **k)
    monkeypatch.setattr(ControllerService, "run_action", spy)
    c = web(service_factory=lambda: svc)
    doc = parse(c.get("/", headers=LOCAL).get_data(as_text=True))
    for comp, op in (("meshcore-webui", "start"), ("meshcore-gps", "stop")):
        forms = [doc.within(f) for f in doc.find("form", action="/action")
                 if doc.within(f).field_default("target") == f"{comp}@r4"]
        assert len(forms) == 1, (comp, len(forms))                                       # the card's own button
        (form,) = forms
        token = form.field_default("_csrf")
        assert token and form.field_default("op") == op and form.field_default("from") == "dash"
        r = c.post("/action", headers=LOCAL, data={"_csrf": token, "op": op, "target": f"{comp}@r4", "from": "dash"})
        assert r.status_code in (200, 302), r.status_code
        assert (op, comp, "r4") in calls, calls
    calls.clear()
    r = c.post("/action", headers=LOCAL, data={"op": "start", "target": "meshcore-webui@r4", "from": "dash"})
    assert r.status_code == 400 and calls == []                                          # no CSRF: refused


def test_an_unapplied_port_move_shows_the_live_port_pending_on_the_card(tmp_path, web):
    # E3-2, rendered: the card names the port nginx still serves (8845), never the saved 9000, and says Apply pends
    from lhpc.core import webserver
    svc = _svc(tmp_path, extra=[("127.0.0.1", 8845)])
    _proxy(svc, "meshcom@r3", 9000)
    webserver.write_evidence(svc._paths, {"applied_snapshot": {
        "console": {"bind": "127.0.0.1", "port": 8443},
        "proxies": [{"stack_id": "meshcom@r3", "bind": "127.0.0.1", "port": 8845, "mode": "local",
                     "scheme": "http", "access_mode": "no-auth"}]}})
    block = _block(web, svc, LOCAL, "r3")
    assert _hrefs(block, "iface-web") == ["http://127.0.0.1:8845/"] and "9000" not in block.text
    assert "(pending Apply)" in block.text


def test_an_orphan_keeps_its_state_stop_and_note_and_gets_no_run(tmp_path, web):
    # G2-B2: the orphan Run is withdrawn (his word): an entry no instance claims keeps state, Stop and a note only
    from radios_fixture import RADIOS
    radios_fixture.write(tmp_path, RADIOS)
    svc = ControllerService(system=FakeSystem(cmdlines_data={2000: ["qemu-system-xtensa", "-nographic"]},
                                              listeners=[Listener("ipv4", "127.0.0.1", 18083, 1)]).system,
                            paths=Paths(runtime_root=tmp_path))
    r = web(service_factory=lambda: svc).get("/", headers=LOCAL)
    assert r.status_code == 200
    doc = parse(r.get_data(as_text=True))
    (block,) = [doc.within(d) for d in doc.find("div", class_="runstack")
                if doc.within(d).find("p", **{"data-stack": "meshcom"})]
    rows = block.find("li")
    assert len(rows) > 1 and any("stopped" in li.text and "opt" in li.text for li in rows)   # a stopped optional part
    assert block.find("p", id="noinst-r1-meshcom")                                          # its note
    assert {op for op, _t in _forms(block)} == {"stop"}                                     # Stop only, no Run
    assert ("stop", "meshcom@r1") in _forms(block)


def test_the_card_offers_no_run_for_a_gps_feed_or_a_test_fixture(tmp_path, web):
    # second read N1/N2: the row's Run follows the operator surfaces' one rule (optional_role): a production GPS feed
    # (its position plan starts it; MeshCom's GPS init is one-shot at boot) and a `test_fixture` (MeshCom's synthetic
    # NMEA relay) get none. red-before (f43418d1): Run meshcom-gps@r3, meshcom-gps-relay@r3, meshcore-gps@r4
    mc = _block(web, _svc(tmp_path), LOCAL, "r3")
    for cid in ("meshcom-gps", "meshcom-gps-relay"):
        row = _row(mc, cid)
        assert row.find("span", class_="badge-stopped") and _forms(row) == [], (cid, _forms(row))
    core = _mc_block(web, _mc(tmp_path / "b", {"r4": ["meshcore-node"]}, ports=[MC_NODE["r4"]]), "r4")
    assert _row(core, "meshcore-gps").find("span", class_="badge-stopped") and _forms(_row(core, "meshcore-gps")) == []
    assert _forms(_row(core, "meshcore-webui")) == [("start", "meshcore-webui@r4")]     # an optional service keeps it


def _orphan_meshcom(tmp_path, web):
    """An unclaimed MeshCom on 433 (no launch record or argv names a radio): its QEMU node with its web listener up and
    its GPS feed running; the meshcom block of card r1."""
    from radios_fixture import RADIOS
    radios_fixture.write(tmp_path, RADIOS)
    plain = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    bridge = plain.stack("meshcom").component("meshcom-gps").process.exec_path     # this install's interpreter
    svc = ControllerService(system=FakeSystem(
        cmdlines_data={2000: ["qemu-system-xtensa", "-nographic"],
                       2001: [bridge, "-m", "lhpc.adapters.cli.main", "_gps-bridge", "meshcom"]},
        listeners=[Listener("ipv4", "127.0.0.1", 18083, 1)]).system, paths=Paths(runtime_root=tmp_path))
    r = web(service_factory=lambda: svc).get("/", headers=LOCAL)
    assert r.status_code == 200
    doc = parse(r.get_data(as_text=True))
    (card,) = [doc.within(d) for d in doc.find("div", class_="radio-col", **{"data-radio-rid": "r1"})]
    (block,) = [card.within(d) for d in card.find("div", class_="runstack")
                if card.within(d).find("p", **{"data-stack": "meshcom"})]
    return block


def test_an_orphans_running_part_has_its_stop_on_its_rows_first_line(tmp_path, web):
    # second read M1 (mutant X2: the Stop rendered after the row instead of on its line 1 survived)
    row = _row(_orphan_meshcom(tmp_path, web), "meshcom-gps")
    (head,) = [row.within(d) for d in row.find("div", class_="chead")]
    (act,) = [head.within(s) for s in head.find("span", class_="cact")]
    assert row.find("span", class_="badge-degraded") or row.find("span", class_="badge-running")
    assert _forms(act) == [("stop", "meshcom-gps@r1")] and _forms(row) == _forms(act)


def test_an_orphan_with_a_working_web_link_shows_it_in_its_head_and_offers_no_run(tmp_path, web):
    # layout B's head link for every running entry (accepted for orphans): it adds no action; the orphan Run stays
    # withdrawn
    block = _orphan_meshcom(tmp_path, web)
    assert _hrefs(block, "headlink") == ["http://127.0.0.1:18083"] and "Web UI ↗" in block.text
    assert _hrefs(_row(block, "meshcom-qemu"), "iface-web") == ["http://127.0.0.1:18083"]   # the row's own link
    assert {op for op, _t in _forms(block)} == {"stop"}
