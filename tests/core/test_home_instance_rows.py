"""A23 core part (with the web bundle B6): Home's instance rows carry configurable, writes_log and interfaces, each from
the instance's own endpoints and its own `<page>@<rid>` policy; "booting" is a badge only, links follow the listeners
(his Q-B2-5 rule). Through radio_start.running_instances, the band entries Home renders from, and the rendered Home
page for the band card (Home renders an instance row's state only; its links are the web bundle's)."""
import dataclasses

import radios_fixture
from seams import entry_args, exe
from htmlq import parse

from lhpc.core import commands, instances, radio_start, radios
from lhpc.core.lifecycle import Lifecycle
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem, Listener
from lhpc.core.services import ControllerService

ROWS = radios_fixture.RADIOS + (radios.Radio("r3", "rfm98 433", "ch341-rfm98pw-433", "433", usb_serial="CHE1W433"),
                                radios.Radio("r30", "rfm98 b", "ch341-rfm98pw-433", "433", usb_serial="CHE2W433"))
WEB = {"r1": 18083, "r3": 18349}
CONSOLE = {"r1": 12323, "r3": 12589}
LEGACY_QEMU = ["qemu-system-xtensa", "-nographic"]


def _svc(tmp_path, rids=("r1", "r3"), ports=None, legacy=False):
    radios_fixture.write(tmp_path, ROWS)
    paths = Paths(runtime_root=tmp_path)
    plain = ControllerService(system=FakeSystem().system, paths=paths)
    comp = plain.stack("meshcom").component("meshcom-qemu")
    table = {}
    for n, rid in enumerate(rids):
        opt, val = instances._token(plain, comp, commands.Instance(rid, band="433"), "433")
        table[1000 + n] = [comp.process.exec_name, opt, val]
    if legacy:
        table[2000] = LEGACY_QEMU
    ports = [p for r in rids for p in (WEB[r], CONSOLE[r])] if ports is None else ports
    fake = FakeSystem(cmdlines_data=table, listeners=[Listener("ipv4", "127.0.0.1", p, p) for p in ports])
    return ControllerService(system=fake.system, paths=paths)


def _rows(svc):
    live = {c: cs for ss in svc.build_snapshot().stacks for c, cs in ss.components.items()}
    return {e["rid"]: next(r for r in e["components"] if r["id"] == "meshcom-qemu")
            for e in radio_start.running_instances(svc, "433", live) if e["sid"] == "meshcom"}


def _web(row):
    return [i for i in row["interfaces"] if i["is_web"]]


def _proxy(svc, page, port):
    r = svc.stack_web_configure(page, mode="local", port=port, scheme="http", access_mode="no-auth")
    assert r.ok, (r.summary, r.details)


def _booting(monkeypatch, *rids):
    """Live detached post runners of meshcom-qemu, one per rid in `rids` ("" = a runner without a radio)."""
    real = Lifecycle.owned_records

    def owned_records(self, comp_id, band=None, role="", rid=None):
        if role == "post" and comp_id == "meshcom-qemu":
            return [{"pid": 3000 + n, "rid": r} if r else {"pid": 3000 + n} for n, r in enumerate(rids)]
        return real(self, comp_id, band, role, rid)
    monkeypatch.setattr(Lifecycle, "owned_records", owned_records)
    monkeypatch.setattr(Lifecycle, "_original_ceased", lambda self, rec: False)


def test_each_instance_row_links_its_own_web_ui(tmp_path):
    # red-before (b3fc6e60): the rows carry no interfaces/configurable/writes_log (KeyError)
    rows = _rows(_svc(tmp_path))
    assert [i["address"] for i in _web(rows["r1"])] == ["127.0.0.1:18083"]
    assert [i["address"] for i in _web(rows["r3"])] == ["127.0.0.1:18349"]
    assert rows["r3"]["configurable"] is True and rows["r3"]["writes_log"] is True


def test_the_link_kinds_are_the_band_entrys(tmp_path):
    svc = _svc(tmp_path)
    band = next(r for e in svc._band_entries() if "meshcom" in e.get("entries", {})
                for r in e["entries"]["meshcom"]["components"] if r["id"] == "meshcom-qemu")
    row = _rows(svc)["r3"]
    assert (row["configurable"], row["writes_log"]) == (band["configurable"], band["writes_log"])


def test_each_instance_uses_its_own_page_policy(tmp_path):
    svc = _svc(tmp_path)
    _proxy(svc, "meshcom@r3", 8470)
    rows = _rows(svc)
    (r3,), (r1,) = _web(rows["r3"]), _web(rows["r1"])
    assert (r3["proxy_port"], r3["pending"]) == (8470, True)                # r3's proxy, not yet applied
    assert (r1["proxy_port"], r1["link"]) == (0, "http://127.0.0.1:18083")  # r1 a direct listener


def test_the_page_is_exactly_the_instances_never_a_prefix(tmp_path):
    svc = _svc(tmp_path)
    _proxy(svc, "meshcom@r30", 8471)                                       # r30's page proxied, r3's not
    (r3,) = _web(_rows(svc)["r3"])
    assert (r3["proxy_port"], r3["link"]) == (0, "http://127.0.0.1:18349")


def test_an_unclaimed_process_does_not_add_other_addresses_to_a_row(tmp_path):
    # G1-B1-PLAN-RE2 BLOCKING 2: the snapshot's status of meshcom-qemu also holds the template's 18083 (a legacy qemu);
    # r3's row lists r3's own address only
    rows = _rows(_svc(tmp_path, rids=("r3",), ports=(18349, 12589, 18083, 12323), legacy=True))
    assert [i["address"] for i in rows["r3"]["interfaces"]] == ["127.0.0.1:18349"]


def test_a_booting_instance_shows_the_badge_and_its_link(tmp_path, monkeypatch):
    # his Q-B2-5 rule; the web contract's NOTE D as changed: r3 booting, its web listener up -> "booting" + its link
    _booting(monkeypatch, "r3")
    rows = _rows(_svc(tmp_path))
    assert rows["r3"]["state"] == "booting" and [i["address"] for i in _web(rows["r3"])] == ["127.0.0.1:18349"]
    assert rows["r1"]["state"] == "running" and [i["address"] for i in _web(rows["r1"])] == ["127.0.0.1:18083"]


def test_a_booting_instance_without_its_listener_has_no_link(tmp_path, monkeypatch):
    _booting(monkeypatch, "r3")
    rows = _rows(_svc(tmp_path, ports=(18083, 12323, 12589)))
    assert rows["r3"]["state"] == "booting" and _web(rows["r3"]) == []


def test_a_runner_without_a_radio_counts_for_every_instance(tmp_path, monkeypatch):
    _booting(monkeypatch, "")
    rows = _rows(_svc(tmp_path))
    assert rows["r1"]["state"] == rows["r3"]["state"] == "booting"


def test_no_runner_no_badge(tmp_path, monkeypatch):
    _booting(monkeypatch)
    assert {r: row["state"] for r, row in _rows(_svc(tmp_path)).items()} == {"r1": "running", "r3": "running"}


def test_the_band_card_shows_booting_and_the_link(tmp_path, monkeypatch):
    # red-before (b3fc6e60): a booting band row had interfaces = [] (service_lifecycle_ops.py:2982)
    _booting(monkeypatch, "r3")
    svc = _svc(tmp_path)
    row = next(r for e in svc._band_entries() if "meshcom" in e.get("entries", {})
               for r in e["entries"]["meshcom"]["components"] if r["id"] == "meshcom-qemu")
    assert row["state"] == "booting" and any(i["is_web"] for i in row["interfaces"])


def test_home_renders_a_booting_band_card_with_its_web_link(tmp_path, monkeypatch, web):
    # red-before (085034cf): the booting band card rendered no link (G1-B1A-CODE N1, through GET /)
    _booting(monkeypatch, "")
    svc = _svc(tmp_path, rids=(), ports=(18083, 12323), legacy=True)            # an unclaimed MeshCom: the band card
    r = web(service_factory=lambda: svc).get("/", headers={"Host": "localhost:8443"})
    assert r.status_code == 200
    doc = parse(r.get_data(as_text=True))
    cards = [doc.within(d) for d in doc.find("div", class_="runstack")
             if doc.within(d).find("p", **{"data-stack": "meshcom"})]
    assert len(cards) == 3                                                       # each 433 radio's card: r1, r3, r30
    for card in cards:
        assert card.find("span", class_="badge-booting")
        assert [a["href"] for a in card.find("a", class_="iface-web")] == ["http://127.0.0.1:18083"]


def test_without_its_own_page_an_instance_never_takes_a_longer_rids(tmp_path):
    # G1-B1A-CODE N2: meshcom@r3's page absent (r3 disabled), meshcom@r30's proxied -> no proxied page for r3
    svc = _svc(tmp_path)
    _proxy(svc, "meshcom@r30", 8471)
    cs = next(ss.components["meshcom-qemu"] for ss in svc.build_snapshot().stacks if "meshcom-qemu" in ss.components)
    radios_fixture.write(tmp_path, tuple(dataclasses.replace(r, enabled=False) if r.id == "r3" else r for r in ROWS))
    assert "meshcom@r3" not in [p.page_id for p in svc.stack_web_pages("meshcom")]
    web_itfs = [i for i in svc._client_interfaces(cs, "meshcom", rid="r3") if i["is_web"]]
    assert web_itfs and all(i["proxy_port"] == 0 for i in web_itfs), web_itfs


# --- K1 (U2): a row per component of a running instance, stopped ones too; the state is process membership ---------

R4 = radios.Radio("r4", "stick", "meshstick-1262", "868", usb_serial="MS123456")
MC_NODE = {"r2": 5133, "r4": 5399}                  # meshcore-node's own TCP port per instance
MC_WEB = {"r2": 8921, "r4": 9187}                   # meshcore-webui's own port per instance
K1_KEYS = {"id", "name", "optional", "state", "note", "configurable", "writes_log", "interfaces", "runnable",
           "interactive", "commands", "blocker"}


def _mc(tmp_path, running, ports=()):
    """meshcore on r2 and r4 (868): `running` {rid: the component ids that run as that instance, each by its own argv
    token}; `ports`: the 127.0.0.1 listeners that are up."""
    radios_fixture.write(tmp_path, (radios_fixture.R2_868, R4))
    paths = Paths(runtime_root=tmp_path)
    plain = ControllerService(system=FakeSystem().system, paths=paths)
    table = {}
    for rid, comps in running.items():
        inst = instances.parse_target(plain, f"meshcore@{rid}", "start")[1]
        for cid in comps:
            spec = plain.stack("meshcore").component(cid).process
            opt, val = instances._token(plain, plain.stack("meshcore").component(cid), inst, "868")
            table[700 + len(table)] = [exe(spec, spec.exec_name), *entry_args(spec), *[a for a in spec.all_args if a != opt], opt, val]
    fake = FakeSystem(cmdlines_data=table, listeners=[Listener("ipv4", "127.0.0.1", p, p) for p in ports])
    return ControllerService(system=fake.system, paths=paths)


def _mc_rows(svc):
    """{rid: {component id: row}} of Home's meshcore instance entries on 868."""
    live = {c: cs for ss in svc.build_snapshot().stacks for c, cs in ss.components.items()}
    return {e["rid"]: {r["id"]: r for r in e["components"]}
            for e in radio_start.running_instances(svc, "868", live) if e["sid"] == "meshcore"}


def test_a_stopped_part_of_a_running_instance_is_its_own_row_with_every_key(tmp_path):
    # red-before (6b82239a): meshcore@r4 runs node + gps; its web UI had no row (radio_start.py:1221 skipped it)
    rows = _mc_rows(_mc(tmp_path, {"r4": ["meshcore-node", "meshcore-gps"]}, ports=[MC_NODE["r4"]]))["r4"]
    stack = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path)).stack("meshcore")
    assert list(rows) == [c.id for c in stack.components]                 # every part, in the stack's order
    assert all(set(r) == K1_KEYS for r in rows.values()), {c: sorted(r) for c, r in rows.items()}
    web = rows["meshcore-webui"]
    assert (web["state"], web["interfaces"], web["note"]) == ("stopped", [], "")
    assert web["runnable"] and web["optional"] and web["configurable"] and web["writes_log"]
    assert (rows["meshcore-node"]["state"], rows["meshcore-gps"]["state"]) == ("running", "running")
    assert list(rows["meshcore-cli"]["commands"]) == ["r4"]               # this radio's command only
    assert rows["openhop-repeater-src"]["runnable"] is False              # a library: a row, never a Run


def test_each_radios_rows_read_its_own_processes_and_endpoints(tmp_path):
    # N1 / G2-N1: r2 runs node + web UI + gps with its listeners up; r4 runs its node only, its listener absent. r4's
    # web UI and gps read stopped (never r2's live state), r4's node degraded on r4's own address.
    svc = _mc(tmp_path, {"r2": ["meshcore-node", "meshcore-webui", "meshcore-gps"], "r4": ["meshcore-node"]},
              ports=[MC_NODE["r2"], MC_WEB["r2"]])
    rows = _mc_rows(svc)
    state = {rid: {c: r["state"] for c, r in rows[rid].items() if c != "openhop-repeater-src"} for rid in rows}
    assert state == {"r2": {"meshcore-gps": "running", "meshcore-node": "running", "meshcore-webui": "running",
                            "meshcore-cli": "stopped"},
                     "r4": {"meshcore-gps": "stopped", "meshcore-node": "degraded", "meshcore-webui": "stopped",
                            "meshcore-cli": "stopped"}}
    assert rows["r2"]["meshcore-node"]["note"] == ""
    assert f"127.0.0.1:{MC_NODE['r4']}" in rows["r4"]["meshcore-node"]["note"]
    assert [i["address"] for i in rows["r2"]["meshcore-node"]["interfaces"]] == [f"127.0.0.1:{MC_NODE['r2']}"]
    assert [i["address"] for i in rows["r2"]["meshcore-webui"]["interfaces"]] == [f"127.0.0.1:{MC_WEB['r2']}"]
    assert rows["r4"]["meshcore-node"]["interfaces"] == rows["r4"]["meshcore-webui"]["interfaces"] == []


def test_an_instance_that_runs_no_part_is_on_no_card(tmp_path):
    # a pin (green before): both copies are listed; only the one with a running part has an entry
    assert list(_mc_rows(_mc(tmp_path, {"r2": ["meshcore-gps"]}))) == ["r2"]
    assert _mc_rows(_mc(tmp_path, {})) == {}


def test_the_instance_rows_and_the_band_rows_are_one_schema(tmp_path):
    # E3-4: one builder for both; red-before (6b82239a): the instance rows lacked runnable/interactive/commands/blocker
    svc = _svc(tmp_path, rids=("r3",), legacy=True)                     # meshcom@r3 + an unclaimed (band) qemu
    band = [r for e in svc._band_entries() if "meshcom" in e.get("entries", {})
            for r in e["entries"]["meshcom"]["components"]]
    inst = [r for e in radio_start.running_instances(
        svc, "433", {c: cs for ss in svc.build_snapshot().stacks for c, cs in ss.components.items()})
        if e["sid"] == "meshcom" for r in e["components"]]
    assert band and inst and all(set(r) == K1_KEYS for r in band + inst)
    pick = ("id", "name", "optional", "runnable", "interactive", "configurable", "writes_log", "blocker")
    assert [{k: r[k] for k in pick} for r in inst] == [{k: r[k] for k in pick} for r in band]


# --- E3-2: the card's proxied web link names the proxy's EFFECTIVE listener, never the desired port ----------------

def _seed_applied(svc, page, port):
    """The last Apply's snapshot: `page`'s proxy as nginx serves it now (local, http, no auth, on 127.0.0.1:port)."""
    from lhpc.core import webserver
    webserver.write_evidence(svc._paths, {"applied_snapshot": {
        "console": {"bind": "127.0.0.1", "port": 8443},
        "proxies": [{"stack_id": page, "bind": "127.0.0.1", "port": port, "mode": "local", "scheme": "http",
                     "access_mode": "no-auth"}]}})


def test_an_unapplied_port_move_links_the_port_that_answers(tmp_path):
    # red-before (6b82239a): the link took the saved 9000 (nothing listens there): "proxy not active — Apply", no URL
    svc = _svc(tmp_path, ports=[18083, 12323, 18349, 12589, 8845])
    _proxy(svc, "meshcom@r3", 9000)                                       # saved, never applied
    _seed_applied(svc, "meshcom@r3", 8845)                                # nginx still serves 8845
    (r3,) = _web(_rows(svc)["r3"])
    assert (r3["proxy_port"], r3["proxy_scope"], r3["link"], r3["pending"]) == (
        8845, "loopback", "http://127.0.0.1:8845/", True)
    assert svc.config().stackweb["meshcom@r3"].port == 9000               # the desired config stays, for editing
    (r1,) = _web(_rows(svc)["r1"])
    assert (r1["proxy_port"], r1["link"]) == (0, "http://127.0.0.1:18083")


def test_a_saved_disable_keeps_the_live_proxy_link_until_apply(tmp_path):
    # red-before (6b82239a): a saved Disable dropped the proxy at once: the direct upstream link, nothing pending
    svc = _svc(tmp_path, ports=[18083, 12323, 18349, 12589, 8845])
    _proxy(svc, "meshcom@r3", 8845)
    _seed_applied(svc, "meshcom@r3", 8845)
    assert svc.stack_web_configure("meshcom@r3", port=0, confirm=True).ok    # Disable, no Apply
    (r3,) = _web(_rows(svc)["r3"])
    assert (r3["proxy_port"], r3["link"], r3["pending"]) == (8845, "http://127.0.0.1:8845/", True)


def test_an_applied_proxy_reads_in_sync(tmp_path):
    # the other side (a pin): applied on the saved port -> that port, nothing pending
    svc = _svc(tmp_path, ports=[18083, 12323, 18349, 12589, 8845])
    _proxy(svc, "meshcom@r3", 8845)
    _seed_applied(svc, "meshcom@r3", 8845)
    (r3,) = _web(_rows(svc)["r3"])
    assert (r3["proxy_port"], r3["link"], r3["pending"]) == (8845, "http://127.0.0.1:8845/", False)
