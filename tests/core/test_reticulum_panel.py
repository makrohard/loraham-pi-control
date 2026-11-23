"""Reticulum's daemon panel (plan reti v21 §1b R7, §1d RED-2, B2): on the daemon backend a radio's
"Daemon radio parameters" panel holds all its radio values, starting from Reticulum's profile; the panel and the
rendered `[[LoRa <rid>]]` section are one function (the instance's own set over the radio row's POWER, at most the
band cap, over the profile; no other row of the radio's);
every row is operator-owned and Save-only (the interface sends the section at every (re)connect, LHPC sends nothing).
The Config page keeps frequency/SF/BW/CR/power for `backend = spi` only. Box H: r1 loraham 433, r2 loraham 868 (SPI),
r3 CH341-RFM98 433 and r4 PineDio 868 (USB)."""
from __future__ import annotations

import dataclasses
import re

import radios_fixture

from lhpc.adapters.cli import main as cli
from lhpc.core import daemon_params, instances, radio_start
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService

BOX_H = radios_fixture.BOX_H
CONFIG_RADIO = ("frequency", "bandwidth", "spreadingfactor", "codingrate", "txpower")


def _svc(tmp_path, rows=BOX_H):
    radios_fixture.write(tmp_path, rows)
    return ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))


def _row_dp(rid, **dp):
    """Box H with radio `rid`'s row holding the daemon values `dp` (`dp_<PARAM>`)."""
    return tuple(dataclasses.replace(r, dp=tuple(sorted(dp.items()))) if r.id == rid else r for r in BOX_H)


def _rows(svc, rid, stack="reticulum"):
    view = svc.daemon_params_view(stack, rid=rid)
    assert view, f"no daemon panel of {stack} on {rid}"
    return {r["name"]: r for r in view["rows"]}


def _section(tmp_path, svc, rid):
    """{key: value} of `[[LoRa <rid>]]` as the node's file renders it."""
    inst = instances.copy_of(svc, "reticulum", rid)[0]
    svc.write_config_files("rns", inst.band, inst=inst)
    lines = [ln.strip() for ln in (tmp_path / "state" / "reticulum" / "config").read_text().splitlines()]
    out = {}
    for ln in lines[lines.index(f"[[LoRa {rid}]]") + 1:]:
        if ln.startswith("["):
            break
        if "=" in ln:
            k, _, v = ln.partition("=")
            out[k.strip()] = v.strip()
    return out


# ---- the panel --------------------------------------------------------------------------------------------------------

def test_the_panel_shows_on_a_daemon_radio_and_not_on_spi(tmp_path):
    svc = _svc(tmp_path)
    view = svc.daemon_params_view("reticulum", rid="r2")
    # red-before (82730d2d): {} — Reticulum had no daemon panel
    assert view and view["save_only"] and not view["can_apply"]
    radios_fixture.reticulum_backend(tmp_path, ["r2"])
    assert svc.daemon_params_view("reticulum", rid="r2") == {}
    assert "backend spi" in svc.save_daemon_params("reticulum", {"CADIDLE": "60"}, rid="r2").summary


def test_the_panel_starts_from_reticulums_profile_every_row_editable_and_at_reconnect(tmp_path):
    svc = _svc(tmp_path)
    rows = _rows(svc, "r2")
    assert {n: rows[n]["value"] for n in ("FREQ", "SF", "BW", "CR", "POWER", "CADIDLE", "TXQUEUE", "PREAMBLE")} == {
        "FREQ": "868.500", "SF": "8", "BW": "125.0", "CR": "5", "POWER": "14", "CADIDLE": "50", "TXQUEUE": "1",
        "PREAMBLE": ""}
    assert svc.daemon_params_view("reticulum", rid="r1") == {}             # r1 is 433: Reticulum runs on 868 only
    assert not any(r["app_owned"] for r in rows.values()) and all(r["at_start"] for r in rows.values())
    assert rows["MODE"]["choices"] == ["LORA"]
    assert (daemon_params.default_value("reticulum", "868", "CADIDLE"),
            daemon_params.default_value("kiss", "868", "CADIDLE")) == ("50", "250")


def test_a_radio_row_cadidle_reaches_neither_reticulums_panel_nor_its_section(tmp_path):
    # G1 row 180 B1 / row 182 red-before: radio row dp_CADIDLE=250, a Reticulum instance without one -> 50 in both
    svc = _svc(tmp_path, _row_dp("r2", CADIDLE="250"))
    # red-before (82730d2d): no panel; with the merge of every other stack: 250
    assert _rows(svc, "r2")["CADIDLE"]["value"] == "50"
    assert _section(tmp_path, svc, "r2")["cadidle"] == "50"


def test_the_rendered_section_equals_the_saved_panel_values(tmp_path):
    svc = _svc(tmp_path)
    saved = {"FREQ": "869.4", "BW": "250.0", "SF": "9", "CR": "6", "POWER": "10", "SYNC": "0x34", "TXQUEUE": "0",
             "CADIDLE": "80", "PREAMBLE": "24", "TXMODE": "DIRECT", "CADTXAFTERTIMEOUT": "1"}
    assert svc.save_daemon_params("reticulum", saved, rid="r2").ok
    sec = _section(tmp_path, svc, "r2")
    # red-before (82730d2d): the save is refused (no daemon parameters) and the section has the Config defaults
    assert {k: sec.get(k) for k in ("frequency", "bandwidth", "spreadingfactor", "codingrate", "txpower", "syncword",
                                    "txqueue", "cadidle", "preamble", "txmode", "cadtxaftertimeout")} == {
        "frequency": "869400000", "bandwidth": "250000", "spreadingfactor": "9", "codingrate": "6", "txpower": "10",
        "syncword": "0x34", "txqueue": "0", "cadidle": "80", "preamble": "24", "txmode": "DIRECT",
        "cadtxaftertimeout": "1"}


def test_an_unsaved_preamble_is_left_to_the_interface_and_the_config_values_are_not_read(tmp_path):
    svc = _svc(tmp_path)
    (tmp_path / "config" / "stacks").mkdir(parents=True, exist_ok=True)
    (tmp_path / "config" / "stacks" / "reticulum@r2.toml").write_text('file_frequency = "869525000"\n')
    sec = _section(tmp_path, svc, "r2")
    assert "preamble" not in sec and sec["frequency"] == "868500000" and sec["bandwidth"] == "125000"


def test_on_spi_the_section_keeps_the_config_values_and_no_daemon_keys(tmp_path):
    svc = _svc(tmp_path)
    radios_fixture.reticulum_backend(tmp_path, ["r2"])
    sec = _section(tmp_path, svc, "r2")
    assert sec["frequency"] == "868500000" and sec["txpower"] == "14"
    assert not {"cadidle", "txqueue", "syncword", "txmode", "preamble"} & set(sec)


def test_mode_is_lora_only(tmp_path):
    svc = _svc(tmp_path)
    res = svc.save_daemon_params("reticulum", {"MODE": "FSK"}, rid="r2")
    assert not res.ok and "MODE is LORA for reticulum" in res.summary


# ---- one runtime applier (RED-2) --------------------------------------------------------------------------------------

def test_lhpc_sends_nothing_to_the_daemon_for_reticulum_and_apply_live_is_refused(tmp_path):
    svc = _svc(tmp_path)
    assert svc.save_daemon_params("reticulum", {"CADIDLE": "70"}, rid="r2").ok
    assert svc._daemon_param_applies("reticulum", "r2") == {}       # the stack start's apply step sends no SET
    res = svc.apply_daemon_params("reticulum", rid="r2")
    assert not res.ok and "apply at Save" in res.summary


# ---- the Config page ----------------------------------------------------------------------------------------------------

def _shown(svc, rid):
    inst = instances.copy_of(svc, "reticulum", rid)[0]
    return {r["name"] for g in svc.config_param_groups("reticulum", inst=inst) for r in g["rows"]}


def _fields(svc, rid):
    return {f["name"] for f in svc.config_param_fields("reticulum", "", instances.copy_of(svc, "reticulum", rid)[0])}


def test_the_config_page_drops_the_radio_values_on_the_daemon_backend_and_keeps_them_on_spi(tmp_path):
    svc = _svc(tmp_path)
    # red-before (82730d2d): all five on the Config page of every radio
    assert not set(CONFIG_RADIO) & _shown(svc, "r2") and "rnode_framing" in _shown(svc, "r2")
    assert not set(CONFIG_RADIO) & _fields(svc, "r2")              # the save's fields: a Config save writes none of them
    radios_fixture.reticulum_backend(tmp_path, ["r2"])
    assert set(CONFIG_RADIO) <= _shown(svc, "r2") and set(CONFIG_RADIO) <= _fields(svc, "r2")


def test_the_cli_names_the_daemon_panel_for_a_radio_value(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("LHPC_RUNTIME_ROOT", str(tmp_path))
    radios_fixture.write(tmp_path, BOX_H)
    rc = cli.main(["config", "reticulum@r2", "frequency"])
    out = capsys.readouterr().out
    assert rc == 1 and "--daemon-param FREQ=VALUE" in out


# ---- a Save while the node runs (plan v22 §8.3 B2b) -------------------------------------------------------------------

def test_the_owners_panel_save_is_checked_first_and_marks_the_node_restart_required(tmp_path, monkeypatch):
    from lhpc.core import reticulum_members
    svc, seen = _svc(tmp_path), []
    monkeypatch.setattr(reticulum_members, "check",
                        lambda s, rid, block: seen.append(block) or ("txpower: above the band cap" if "13" in block else ""))
    assert svc.save_daemon_params("reticulum", {"POWER": "13"}, rid="r2").ok       # the node is down: its start checks
    assert seen == [] and svc.restart_required("reticulum@r2") is None
    monkeypatch.setattr(reticulum_members, "owner_record", lambda s: {"rid": "r2", "node_owner": True})
    res = svc.save_daemon_params("reticulum", {"POWER": "12", "CADIDLE": "60", "SF": "8"}, rid="r2")
    # red-before (da24e83c): no check and no marker — the running interface kept 13 with nothing to say so
    assert res.ok and "take effect at its restart" in res.summary and len(seen) == 1
    assert {"dp_POWER", "dp_CADIDLE"} <= set(svc.restart_required("reticulum@r2")["params"])
    assert "dp_SF" not in svc.restart_required("reticulum@r2")["params"]                    # SF 8 = as it was
    res = svc.save_daemon_params("reticulum", {"POWER": "13"}, rid="r2")
    assert not res.ok and "the interface would refuse these settings: txpower" in res.summary
    assert _rows(svc, "r2")["POWER"]["value"] == "12"
    seen.clear()
    assert svc.save_daemon_params("reticulum", {"POWER": "12"}, rid="r2").ok and seen == []     # nothing changed


# ---- the radio's POWER (plan RETI-POWER v2) ---------------------------------------------------------------------------

def test_the_radios_power_reaches_reticulums_panel_and_section_at_most_its_band_cap(tmp_path):
    svc = _svc(tmp_path, _row_dp("r4", POWER="2"))
    # red-before (93419bfe): 14 in both — the profile's band value over the radio's POWER 2 (live proof R-P1 case,
    # on 868: Reticulum runs on 868 only)
    assert (_rows(svc, "r4")["POWER"]["value"], _section(tmp_path, svc, "r4")["txpower"]) == ("2", "2")
    svc = _svc(tmp_path, _row_dp("r2", POWER="17"))
    assert (_rows(svc, "r2")["POWER"]["value"], _section(tmp_path, svc, "r2")["txpower"]) == ("14", "14")
    svc = _svc(tmp_path, _row_dp("r4", POWER="2"))
    assert svc.save_daemon_params("reticulum", {"POWER": "5"}, rid="r4").ok          # its own set wins over the row
    assert (_rows(svc, "r4")["POWER"]["value"], _section(tmp_path, svc, "r4")["txpower"]) == ("5", "5")


def test_saving_the_radios_power_stores_none_so_reticulum_follows_the_radio(tmp_path):
    svc = _svc(tmp_path, _row_dp("r4", POWER="2"))
    assert svc.save_daemon_params("reticulum", {"POWER": "2", "SF": "9"}, rid="r4").ok
    assert "POWER" not in radio_start.instance_dp(svc, "reticulum", "r4")
    radios_fixture.write(tmp_path, _row_dp("r4", POWER="5"))
    assert (_rows(svc, "r4")["POWER"]["value"], _section(tmp_path, svc, "r4")["txpower"]) == ("5", "5")


def test_a_save_while_the_node_runs_compares_the_section_it_renders(tmp_path, monkeypatch):
    from lhpc.core import reticulum_members
    svc, seen = _svc(tmp_path, _row_dp("r4", POWER="2")), []
    monkeypatch.setattr(reticulum_members, "check", lambda s, rid, block: seen.append(block) or "")
    monkeypatch.setattr(reticulum_members, "owner_record", lambda s: {"rid": "r4", "node_owner": True})
    assert svc.save_daemon_params("reticulum", {"SF": "8", "POWER": "2"}, rid="r4").ok          # = what it renders
    assert seen == [] and svc.restart_required("reticulum@r4") is None
    assert svc.save_daemon_params("reticulum", {"SF": "9"}, rid="r4").ok
    # red-before (inherited POWER without the would-be merge): dp_POWER too, the checked section at 14
    assert svc.restart_required("reticulum@r4")["params"] == ["dp_SF"]
    assert len(seen) == 1 and re.search(r"^\s*txpower = 2$", seen[0], re.M)


def test_a_power_below_reticulums_floor_is_refused_never_raised(tmp_path):
    svc = _svc(tmp_path, _row_dp("r4", POWER="1"))                                 # sx1262: POWER 0/1 are valid
    why = "r4's POWER 1 dBm is below Reticulum's minimum 2 dBm — set POWER 2 or more on the radio or on Reticulum's panel"
    assert radio_start.panel_section(svc, "reticulum", "r4") == ({}, why)
    res = svc.save_daemon_params("reticulum", {"SF": "9"}, rid="r4")
    # red-before (a25 C1): the Save passed with no check
    assert not res.ok and why in res.summary and "SF" not in radio_start.instance_dp(svc, "reticulum", "r4")
    assert svc.save_daemon_params("reticulum", {"POWER": "2"}, rid="r4").ok
    assert radio_start.panel_section(svc, "reticulum", "r4")[0]["txpower"] == "2"
    svc = _svc(tmp_path, _row_dp("r4", POWER="5"))
    res = svc.save_daemon_params("reticulum", {"POWER": "1"}, rid="r4")                      # its own 1: the same
    assert not res.ok and "r4's POWER 1 dBm is below Reticulum's minimum 2 dBm" in res.summary


def test_an_owner_radio_below_the_floor_fails_the_nodes_render_so_its_start_is_blocked(tmp_path):
    svc = _svc(tmp_path, _row_dp("r4", POWER="1"))
    writes = svc.write_config_files("rns", "868", inst=instances.copy_of(svc, "reticulum", "r4")[0])
    # red-before (93419bfe): rendered with txpower 14, over the radio's POWER 1
    assert [(w.status, w.detail) for w in writes if w.component == "rns"] == [("failed", (
        "r4's POWER 1 dBm is below Reticulum's minimum 2 dBm — set POWER 2 or more on the radio or on Reticulum's "
        "panel"))]
    assert not (tmp_path / "state" / "reticulum" / "config").exists()
    radios_fixture.write(tmp_path, _row_dp("r4", POWER="2"))
    assert _section(tmp_path, svc, "r4")["txpower"] == "2"
