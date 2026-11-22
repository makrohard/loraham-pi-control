"""#75 (maintainer's word 11-18: "rf logs of stacks are also per radio and need the radio toggle"): a stack whose RF log
every instance writes as its own `logs/<rid>/rf-<stack>.log` (KISS, MeshCom, MeshCore, Meshtastic: their writers take
{instance_dir}) opens that radio's file from every link, as the daemon's does: the stack's tree entry (with the page's
radio when the stack can run on it, else its first radio; r1's without any radio), Home's band links (one per running
instance, from its launch record) and the log page's radio pills, also on the band-less file's page. The band-less
(0.13.x) file stays viewable on its own URL, never a default (OPEN-A waits for his word); Reticulum keeps its one file."""

from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

import pytest
from htmlq import parse

from lhpc.core import config as cfgmod
from lhpc.core import instances, radio_start
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService

# (stack, writer, file, a radio of the uputronics pair it can run on, one it cannot)
STACKS = [("meshcore", "meshcore-node", "rf-meshcore.log", "r2", "r1"),
          ("meshtastic", "meshtastic", "rf-meshtastic.log", "r2", "r9"),
          ("meshcom", "meshcom-bridge", "rf-meshcom.log", "r1", "r2"),
          ("kiss", "loraham-kiss-tnc", "rf-kiss.log", "r1", "r9")]


def _client(web, tmp_path, radios=True, records=None, monkeypatch=None):
    """A box with the uputronics pair (r1 433, r2 868) when `radios`; `records` = {stack: {rid: band}} launch records."""
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    p = Paths(runtime_root=tmp_path)
    if radios:
        assert radio_start.setup_radios(p, "uputronics")[0]
        cfgmod.save_hardware_setup(p, "uputronics")
    if records is not None:
        real = instances.recorded_bands
        monkeypatch.setattr(instances, "recorded_bands", lambda svc, sid: dict(records.get(sid, {})) or real(svc, sid))
    svc = ControllerService(system=FakeSystem().system, paths=p)
    if monkeypatch is not None:
        monkeypatch.setattr(svc, "_daemon_installed", lambda _st: True)    # Home's card links need an installed daemon
    return web(service_factory=lambda: svc), svc


def _tree_rf_log(c, sid, radio=""):
    r = c.get(f"/stacks/{sid}/menu/rflog-{sid}" + (f"?radio={radio}" if radio else ""))
    assert r.status_code == 302, (sid, r.status_code)
    q = parse_qs(urlsplit(r.headers["Location"]).query)
    return urlsplit(r.headers["Location"]).path, q.get("job", [""])[0], q.get("radio", [""])[0]


@pytest.mark.parametrize("sid, writer, file, can, _cannot", STACKS)
def test_a_stacks_tree_rf_log_opens_the_pages_radios_own_file(tmp_path, web, sid, writer, file, can, _cannot):
    # red-before (0188b31c): job=<file> (the band-less file) with the radio appended — the finding's URL shape
    c, _svc = _client(web, tmp_path)
    assert _tree_rf_log(c, sid, can) == (f"/logs/{writer}", f"{can}/{file}", can)


@pytest.mark.parametrize("sid, writer, file, _can, cannot", STACKS)
def test_a_radio_the_stack_cannot_run_on_falls_back_to_its_first_radio(tmp_path, web, sid, writer, file, _can, cannot):
    # C2: the URL's radio counts only when the stack can run on it (another band's radio, or no radio of the list)
    c, svc = _client(web, tmp_path)
    first = instances.can_run_on(svc, svc.stack(sid))[0]
    assert first != cannot and _tree_rf_log(c, sid, cannot) == (f"/logs/{writer}", f"{first}/{file}", first)


@pytest.mark.no_default_hardware
@pytest.mark.parametrize("sid, writer, file, _can, _cannot", STACKS)
def test_without_any_radio_the_tree_rf_log_opens_r1s_file(tmp_path, web, sid, writer, file, _can, _cannot):
    c, _svc = _client(web, tmp_path, radios=False)
    assert _tree_rf_log(c, sid)[:2] == (f"/logs/{writer}", f"r1/{file}")


def test_reticulum_keeps_its_one_file(tmp_path, web):
    # OPEN-B (pick: as is): one node, one file
    c, _svc = _client(web, tmp_path)
    assert _tree_rf_log(c, "reticulum")[1] == "rf-reticulum.log"


def test_home_links_each_running_instance_of_the_band_to_its_own_file(tmp_path, web, monkeypatch, daemon_record):
    # C3: two MeshCore instances on 868 (r2, r4: two listed radios, a launch record each) = two links, each on its own
    # radio's card (radio_start keeps a card's own files); none on the 433 card; never the band-less file
    import radios_fixture

    from lhpc.core import radios
    c, _svc = _client(web, tmp_path, radios=False, records={"meshcore": {"r2": "868", "r4": "868"}},
                      monkeypatch=monkeypatch)
    radios_fixture.write(tmp_path, (radios.Radio("r1", "a", "uputronics", "433", ce="ce0"),
                                    radios.Radio("r2", "b", "uputronics", "868", ce="ce1"),
                                    radios.Radio("r4", "c", "meshstick-1262", "868", usb_serial="26073012")))
    for rid in ("r2", "r4"):                     # dash plan c3: a card's RF links sit in its recorded daemon's row
        daemon_record(rid, "868")
    doc = parse(c.get("/").get_data(as_text=True))
    def jobs(band):
        return [parse_qs(urlsplit(a["href"]).query)["job"][0] for col in doc.find("div", **{"data-radio-band": band})
                for a in doc.within(col).find("a") if urlsplit(a["href"]).path == "/logs/meshcore-node"]
    assert sorted(set(jobs("868"))) == ["r2/rf-meshcore.log", "r4/rf-meshcore.log"], jobs("868")
    assert jobs("433") == []


def test_an_instance_launched_on_another_band_than_its_cards_has_no_link_there(tmp_path, web, monkeypatch):
    # C3 by its record: r1's MeshCore was launched on 868 (the hardware list changed after the launch; r1 is 433 now):
    # r1's 433 card does not call it its band's RF log
    c, _svc = _client(web, tmp_path, records={"meshcore": {"r1": "868"}}, monkeypatch=monkeypatch)
    doc = parse(c.get("/").get_data(as_text=True))
    links = [a["href"] for col in doc.find("div", **{"data-radio-band": "433"}) for a in doc.within(col).find("a")
             if urlsplit(a["href"]).path == "/logs/meshcore-node"]
    assert links == [], links


def test_the_band_less_files_page_stays_viewable_and_its_radio_pills_open_each_radios_file(tmp_path, web):
    # OPEN-A keep side: the flat file's own URL still opens (200); every pill leads to a radio's own file
    c, _svc = _client(web, tmp_path)
    r = c.get("/logs/meshcore-node?job=rf-meshcore.log&radio=r2")
    assert r.status_code == 200
    doc = parse(r.get_data(as_text=True))
    nav = doc.within(doc.find("nav", **{"aria-label": "RF log radios"})[0])
    jobs = {parse_qs(urlsplit(a["href"]).query)["radio"][0]: parse_qs(urlsplit(a["href"]).query)["job"][0]
            for a in nav.find("a")}
    assert jobs == {"r1": "r1/rf-meshcore.log", "r2": "r2/rf-meshcore.log"}, jobs
