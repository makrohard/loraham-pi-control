"""A23 core part (with the web bundle B6): Home's instance rows carry configurable, writes_log and interfaces, each from
the instance's own endpoints and its own `<page>@<rid>` policy; "booting" is a badge only, links follow the listeners
(his Q-B2-5 rule). Through radio_start.running_instances and the band entries Home renders from."""
import radios_fixture

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
