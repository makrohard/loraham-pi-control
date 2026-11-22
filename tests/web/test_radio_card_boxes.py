"""F18/F22/F25 (radio card, approved mockup v7/v8): every stack block folds (the daemon's closed, others open), every
component box folds and starts closed; a box header shows the name, its web UI's working link as a pill (only there)
and its state pill (a library/firmware row: its kind); the stack title carries no link. F25: meshtasticd's web GUI
shows on the "Meshtastic web client" box with the connect steps and the host:port to copy, per viewer. Through the
rendered Home page (and the manifest loader for the new key)."""
import copy
import tomllib

import pytest
import radios_fixture
from htmlq import parse
from seams import entry_args, exe

from lhpc.core import instances, manifest
from lhpc.core.manifest import ManifestError
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem, Listener
from lhpc.core.services import ControllerService

LOCAL = {"X-LHPC-Peer": "loopback", "Host": "127.0.0.1:8443"}
LAN = {"X-LHPC-Peer": "remote", "Host": "198.51.100.7"}
MT_API, MT_WEB = 4403, 9443                                          # meshtastic@r1's own ports


def _mt(tmp_path, web_ip="127.0.0.1", token=True):
    """meshtasticd (meshtastic@r1, 433) running with its API and web listeners up (`web_ip`); without `token` its
    process names no radio (an orphan entry of the band)."""
    radios_fixture.write(tmp_path, radios_fixture.RADIOS)
    paths = Paths(runtime_root=tmp_path)
    plain = ControllerService(system=FakeSystem().system, paths=paths)
    comp = plain.stack("meshtastic").component("meshtastic")
    argv = [exe(comp.process, comp.process.exec_name), *entry_args(comp.process), *comp.process.all_args]
    if token:
        opt, val = instances._token(plain, comp, instances.parse_target(plain, "meshtastic@r1", "start")[1], "433")
        argv = [a for a in argv if a != opt] + [opt, val]
    fake = FakeSystem(cmdlines_data={900: argv}, listeners=[Listener("ipv4", "127.0.0.1", MT_API, 1),
                                                            Listener("ipv4", web_ip, MT_WEB, 2)])
    return ControllerService(system=fake.system, paths=paths)


def _home(web, svc, headers=LOCAL):
    r = web(service_factory=lambda: svc).get("/", headers=headers)
    assert r.status_code == 200
    return parse(r.get_data(as_text=True))


def _block(doc, stack):
    (b,) = [doc.within(d) for d in doc.find("details", class_="runstack")
            if doc.within(d).find("summary", **{"data-stack": stack})]
    return b


def _box(block, name):
    (box,) = [block.within(d) for d in block.find("details", class_="cbox")
              if block.within(d).find("span", class_="cname")[0].text.removesuffix(" opt").strip() == name]
    return box


def _head(box):
    return box.within(box.find("summary", class_="shead")[0])


def test_stacks_fold_daemon_closed_others_open_boxes_closed_and_no_title_link(tmp_path, web):
    # red before F18: a stack block was a div with a linked title; component rows did not fold
    doc = _home(web, _mt(tmp_path))
    stacks = {d["id"]: d for d in doc.find("details", class_="runstack")}
    assert stacks["stk-r1-meshtastic@r1"].has_attr("open")       # every stack but the daemon starts open (the daemon's
    #                                                              closed instance: test_dash_daemon_start_entry.py)
    mt = _block(doc, "meshtastic@r1")
    assert not mt.within(mt.find("summary", class_="stitle")[0]).find("a")                 # the title: no link
    boxes = mt.find("details", class_="cbox")
    assert boxes and not any(b.has_attr("open") for b in boxes)
    assert all(b["id"].startswith("cbox-r1-meshtastic@r1-") for b in boxes)                  # remembered by id
    head = _head(_box(mt, "Meshtastic (meshtasticd)"))
    assert [s.text for s in head.find("span", class_="sstate")] == ["running"]               # its state, in the header


def test_a_library_row_keeps_its_kind_label_in_the_header(tmp_path, web):
    head = _head(_box(_block(_home(web, _mt(tmp_path)), "meshtastic@r1"), "Meshtastic web client"))
    assert [(s.text, "badge-kind" in s.classes) for s in head.find("span", class_="sstate")] == [("library", True)]


def test_meshtastics_web_gui_sits_on_the_web_client_box_with_the_steps_and_the_address(tmp_path, web):
    # red before F25: the link was meshtasticd's row link (and the stack head's)
    mt = _block(_home(web, _mt(tmp_path)), "meshtastic@r1")
    serving, client = _box(mt, "Meshtastic (meshtasticd)"), _box(mt, "Meshtastic web client")
    assert not serving.find("a", class_="webpill") and not serving.find("a", class_="iface-web")
    assert [a["href"] for a in _head(client).find("a", class_="webpill")] == [f"https://127.0.0.1:{MT_WEB}"]
    assert [a["href"] for a in mt.find("a", class_="webpill")] == [f"https://127.0.0.1:{MT_WEB}"]   # only there
    assert not mt.find("a", class_="iface-web")                                             # no second link in a body
    steps = [li.text for li in client.within(client.find("ul", class_="steps")[0]).find("li")]
    assert len(steps) == 4 and "Add connection" in steps[1] and "Use HTTPS" in steps[2]
    (pre,) = client.find("pre", class_="next")
    assert pre.text.strip() == f"127.0.0.1:{MT_WEB}"


def test_a_lan_viewer_gets_the_address_that_works_for_them_or_none(tmp_path, web):
    # reader C2 / review N2: pill and copy box from ONE per-viewer href; never 127.0.0.1 to a remote viewer
    from lhpc.core import config
    for root in (tmp_path, tmp_path / "lo"):
        root.mkdir(parents=True, exist_ok=True)
        config.save_webserver_config(Paths(runtime_root=root), remote_exposed=True)    # an IP-literal Host passes
    mt = _block(_home(web, _mt(tmp_path, web_ip="0.0.0.0"), LAN), "meshtastic@r1")
    client = _box(mt, "Meshtastic web client")
    assert [a["href"] for a in client.find("a", class_="webpill")] == [f"https://198.51.100.7:{MT_WEB}/"]
    assert [p.text.strip() for p in client.find("pre", class_="next")] == [f"198.51.100.7:{MT_WEB}"]
    mt = _block(_home(web, _mt(tmp_path / "lo"), LAN), "meshtastic@r1")              # loopback only, seen remotely
    client = _box(mt, "Meshtastic web client")
    assert not client.find("a", class_="webpill") and not client.find("pre", class_="next")
    assert client.find("a", class_="iface-local")                                         # the explanation instead
    assert "127.0.0.1" not in client.text


def test_both_row_builders_move_card_interfaces_through_the_one_helper(tmp_path, web, monkeypatch):
    # review B2b: the band entries (an orphan: no launch record or argv names the radio) and the instances' rows both
    # pass through radio_start.relocate_card_interfaces. An unclaimed MeshCom renders as an orphan entry; the spy sees
    # its rows (the instance builder's call is proven by the Meshtastic tests above, which need the move).
    from lhpc.core import radio_start
    seen, real = [], radio_start.relocate_card_interfaces
    monkeypatch.setattr(radio_start, "relocate_card_interfaces", lambda rows: seen.append([r["id"] for r in rows]) or real(rows))
    radios_fixture.write(tmp_path, radios_fixture.RADIOS)
    svc = ControllerService(system=FakeSystem(cmdlines_data={2000: ["qemu-system-xtensa", "-nographic"]},
                                              listeners=[Listener("ipv4", "127.0.0.1", 18083, 1)]).system,
                            paths=Paths(runtime_root=tmp_path))
    doc = _home(web, svc)
    assert [d for d in doc.find("details", class_="runstack")
            if doc.within(d).find("summary", **{"data-stack": "meshcom"})]           # the orphan entry is there
    assert any("meshcom-qemu" in ids for ids in seen), seen


def test_the_helper_moves_a_card_interface_to_its_named_row_only():
    from lhpc.core.radio_start import relocate_card_interfaces
    web = {"label": "GUI", "is_web": True, "card_component": "client"}
    api = {"label": "API", "is_web": False, "card_component": ""}
    rows = [{"id": "server", "interfaces": [api, web]}, {"id": "client", "interfaces": []}, {"id": "cli", "interfaces": []}]
    out = relocate_card_interfaces(rows)
    assert [r["interfaces"] for r in out] == [[api], [web], []]
    lone = [{"id": "server", "interfaces": [web]}]                                     # its row absent: stays put
    assert relocate_card_interfaces(lone)[0]["interfaces"] == [web]


def test_an_orphan_entry_folds_by_its_stack_id(tmp_path, web):
    # review B2a: an orphan entry (no launch record or argv names the radio) has no `sid`; its stack id decides the
    # default (here: open, not the daemon) and keys its remembered state. A daemon never renders as such an entry (a
    # stray band daemon gets its own card), so the daemon-closed default is pinned on an instance
    # (test_dash_daemon_start_entry.py).
    radios_fixture.write(tmp_path, radios_fixture.RADIOS)
    svc = ControllerService(system=FakeSystem(cmdlines_data={2000: ["qemu-system-xtensa", "-nographic"]},
                                              listeners=[Listener("ipv4", "127.0.0.1", 18083, 1)]).system,
                            paths=Paths(runtime_root=tmp_path))
    doc = _home(web, svc)
    entries = [d for d in doc.find("details", class_="runstack")
               if doc.within(d).find("summary", **{"data-stack": "meshcom"})]
    assert entries and all(d.has_attr("open") and d["id"].endswith("-meshcom") for d in entries)


def _raw():
    with open(manifest._DEFAULT_MANIFEST, "rb") as fh:
        return tomllib.load(fh)


def test_card_component_must_name_a_component_of_the_same_stack():
    # review B3: an interface may never vanish: another stack's component (or none) is a load error
    raw = _raw()
    assert manifest.parse_manifest(copy.deepcopy(raw))                                    # as shipped: loads
    for bad in ("kiss-tnc-does-not-exist", "loraham-daemon"):
        data = copy.deepcopy(raw)
        mt = next(s for s in data["stack"] if s["id"] == "meshtastic")
        ep = next(e for c in mt["component"] if c["id"] == "meshtastic" for e in c["endpoint"]
                  if e.get("card_component"))
        ep["card_component"] = bad
        with pytest.raises(ManifestError, match="card_component"):
            manifest.parse_manifest(data)


MC_NODE_R2 = 5133


def _mc_r2(tmp_path, mode="chat"):
    """meshcore@r2 (868) running its node in `mode` (saved; the running role is read from it when no marker says)."""
    radios_fixture.write(tmp_path, (radios_fixture.R1, radios_fixture.R2_868))
    paths = Paths(runtime_root=tmp_path)
    plain = ControllerService(system=FakeSystem().system, paths=paths)
    inst = instances.parse_target(plain, "meshcore@r2", "start")[1]
    if mode != "chat":
        assert plain.save_config_bundle("meshcore", values={"file_repeater_name": "RPT2"}, inst=inst).ok
        assert plain.save_config_bundle("meshcore", values={"file_mode": mode}, inst=inst).ok
    comp = plain.stack("meshcore").component("meshcore-node")
    opt, val = instances._token(plain, comp, inst, "868")
    argv = [exe(comp.process, comp.process.exec_name), *entry_args(comp.process),
            *[a for a in comp.process.all_args if a != opt], opt, val]
    return ControllerService(system=FakeSystem(cmdlines_data={700: argv},
                                               listeners=[Listener("ipv4", "127.0.0.1", MC_NODE_R2, 1)]).system,
                             paths=paths)


@pytest.mark.parametrize("mode", ["chat", "chat+repeater"])
def test_the_meshcore_block_starts_with_its_mode_line(tmp_path, web, mode):
    # F29 (his item; red before: no mode line): display only, the first line after the stack title
    mc = _block(_home(web, _mc_r2(tmp_path, mode)), "meshcore@r2")
    [title] = mc.find("summary", class_="stitle")
    [line] = mc.find("p", class_="smode")
    assert line.index == title.end + 1 and line.text.strip() == f"Mode: {mode}"   # no running marker: the saved mode


def test_other_stacks_have_no_mode_line(tmp_path, web):
    doc = _home(web, _mt(tmp_path))
    assert not _block(doc, "meshtastic@r1").find("p", class_="smode")
