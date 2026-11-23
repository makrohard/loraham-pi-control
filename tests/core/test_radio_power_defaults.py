"""Radio power (RADIO-POWER item 1, the maintainer's decision 1b377343, R1-R3, and his P7): the chip range is the only
POWER limit, a USB radio's default is 14 dBm and an SPI radio's 17, every stack's default is the lower of its own and
its radio's, and nothing warns above the default. The Meshstick is rated 22 dBm (its daemon preset's ceiling), so LHPC
admits 21..22 there with its high-power switch on (daemon 1.5.0: `--high-power`) and 0..20 on every other SX1262. Through the console's Apply, the panel's Save and view, the Hardware
page and `lhpc hardware`, and the stacks' own config files. Replaces the A34 model-maximum tests."""
import dataclasses

import pytest

from lhpc.core import commands, radio_models, radios
from lhpc.core import config as cfgmod
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService

LORA = radios.Radio("r1", "loraham 433", "loraham", "433")
DIY = radios.Radio("r3", "rfm98 433", "ch341-rfm98pw-433", "433", usb_serial="CHE1W433")
PINE = radios.Radio("r4", "pine64 868", "pinedio-usb-1262", "868", usb_port="1-1")
STICK = radios.Radio("r5", "mstick 868", "meshstick-1262", "868", usb_serial="MS123456")
ROWS = (LORA, radios.Radio("r2", "loraham 868", "loraham", "868"), DIY, PINE, STICK)


@dataclasses.dataclass
class _Daemon(FakeSystem):
    """A daemon per CONF socket in `serve` (path -> STATUS fields) that takes every SET."""
    serve: dict = dataclasses.field(default_factory=dict)

    def request(self, path, payload, timeout, max_bytes):
        if path not in self.serve:
            raise ConnectionRefusedError(f"no daemon on {path}")
        st, line = self.serve[path], payload.decode("ascii").strip()
        if line.startswith("SET "):
            self.sent.append((path, line))
            k, _, v = line[4:].partition("=")
            st[k] = v
            return b"OK\n"
        if line == "GET STATUS":
            return ("STATUS " + " ".join(f"{k}={v}" for k, v in st.items()) + "\n").encode()
        if line == "GET STATS":
            return b"STATS TXOK=0 RXOK=0\n"
        if line.startswith("GET CHANNEL"):
            return f"CHANNEL MODE={st.get('MODE', 'LORA')} RSSI=-120 CADSTATE=NOTSCANNED BUSY=0\n".encode()
        return b"ERR UNKNOWN\n"


def _status(rid, band, port):
    return {"RADIO": "READY", "ID": rid, "BOUND": port, "BAND": band, "TXMODE": "MANAGED", "POWER": "10",
            "CHIPFAMILY": "SX1262"}


def _svc(tmp_path, rows=ROWS):
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    (tmp_path / "config" / "radios.toml").write_text(radios.render(rows))
    fake = _Daemon(serve={"/tmp/loraconf-r4.sock": _status("r4", "868", "1-1"),
                          "/tmp/loraconf-r5.sock": _status("r5", "868", "MS123456")})
    return ControllerService(system=fake.system, paths=Paths(runtime_root=tmp_path)), fake


def _power_row(svc, target, rid):
    return next(r for r in svc.daemon_params_view(target, rid=rid)["rows"] if r["name"] == "POWER")


def test_a_pinedio_save_takes_the_sx1262_range(tmp_path):
    # red-before: 17 and 20 refused "POWER must be in [0, 14]" (the model maximum R1 removes)
    svc, _fake = _svc(tmp_path)
    for value in ("17", "20"):
        res = svc.save_daemon_params("daemon", {"POWER": value}, rid="r4")
        assert res.ok, (value, res.summary)
    res = svc.save_daemon_params("daemon", {"POWER": "21"}, rid="r4")
    assert not res.ok and "POWER must be in [0, 20]" in res.summary, res.summary


def test_the_panel_input_shows_the_chip_range(tmp_path):
    # red-before: the PineDio's input stopped at 14
    svc, _fake = _svc(tmp_path)
    assert _power_row(svc, "daemon", "r4")["num"]["max"] == "20"
    assert _power_row(svc, "daemon", "r3")["num"]["max"] == "17"                  # DIY 433 (SX127x), no grant
    granted = dataclasses.replace(DIY, high_power=True, high_power_band="433")
    svc, _fake = _svc(tmp_path, (*ROWS[:2], granted, *ROWS[3:]))
    assert _power_row(svc, "daemon", "r3")["num"]["max"] == "20"                  # exactly 20 with the grant


def test_a_meshstick_starts_at_14_and_its_apply_sends_14(tmp_path):
    # red-before: the default was min(17, 22) = 17 and Apply sent SET POWER=17
    svc, fake = _svc(tmp_path)
    assert _power_row(svc, "daemon", "r5")["default"] == "14"
    res = svc.apply_daemon_params("daemon", rid="r5")
    assert ("/tmp/loraconf-r5.sock", "SET POWER=14") in fake.sent, (fake.sent, res.summary)


@pytest.mark.parametrize("stack", ["daemon", "chat", "kiss", "voice", "meshcom"])
def test_the_diy_433_stick_starts_every_stack_at_14(tmp_path, stack):
    # red-before: 17 (min(17, the model maximum 17))
    svc, _fake = _svc(tmp_path)
    assert _power_row(svc, stack, "r3")["default"] == "14"


def test_an_spi_board_keeps_17(tmp_path):
    svc, _fake = _svc(tmp_path)
    assert _power_row(svc, "daemon", "r1")["default"] == "17"
    assert svc.save_daemon_params("daemon", {"POWER": "17"}, rid="r1").ok
    assert {m.id: m.power_default_dbm for m in radio_models.load().values() if m.transport == "spi"} == \
        {"loraham": 17, "uputronics": 17, "waveshare-sx1262": 17}


def _public(tmp_path, monkeypatch, capsys, web, rows):
    """The service on `rows`, and its two public readers of the row notes: the Hardware page's notes (GET /hardware)
    and the POWER lines of `lhpc hardware` (no USB stick is attached)."""
    from htmlq import parse
    from lhpc.adapters.cli import main as cli
    from lhpc.core import usb_access
    monkeypatch.setattr(usb_access, "scan", lambda *a, **k: usb_access.Scan(usb_access.Probe.PRESENT, ()))
    svc, fake = _svc(tmp_path, rows)
    monkeypatch.setattr(cli, "ControllerService", lambda *a, **k: svc)

    def page_notes():
        r = web(service_factory=lambda: svc).get("/hardware", headers={"Host": "localhost:8443"})
        assert r.status_code == 200
        return [e.text for e in parse(r.get_data(as_text=True)).find("span", class_="note-bad")]

    def cli_power_lines():
        assert cli.main(["hardware"]) == 0
        return [x for x in capsys.readouterr().out.splitlines() if "POWER" in x]
    return svc, fake, page_notes, cli_power_lines


def test_a_stored_power_above_the_default_is_sent_as_stored_and_named_nowhere(tmp_path, monkeypatch, capsys, web):
    # red-before: "POWER 17 is above the model maximum 14 dBm: lower it" on the Hardware page and in `lhpc hardware`
    # (R3: no warning above the default)
    stored = dataclasses.replace(PINE, dp=(("POWER", "17"),))
    svc, fake, page_notes, cli_power_lines = _public(tmp_path, monkeypatch, capsys, web, (*ROWS[:3], stored, STICK))
    assert radios.load(svc._paths).kind == radios.VALID
    assert not any("POWER" in n for n in page_notes()) and cli_power_lines() == []
    res = svc.apply_daemon_params("daemon", rid="r4")
    assert ("/tmp/loraconf-r4.sock", "SET POWER=17") in fake.sent, (fake.sent, res.summary)


def test_a_stacks_own_power_file_default_follows_its_radio(tmp_path):
    # red-before: voice's 433_power rendered 17 on the DIY stick; the file param takes min(its default, the radio's)
    svc, _fake = _svc(tmp_path)
    diy = commands.Instance("r3", band="433")
    assert svc.file_config_values("voice", "433", diy)["power"] == "14"
    assert svc.file_config_values("voice", "433", commands.Instance("r1", band="433"))["power"] == "17"
    assert svc.file_config_values("voice", "433")["power"] == "17"                 # the band caller: r1, its first radio
    only_diy = (DIY, *ROWS[3:])
    svc, _fake = _svc(tmp_path, only_diy)
    assert svc.file_config_values("voice", "433")["power"] == "14"                 # the band caller: r3
    stick = commands.Instance("r5", band="868")
    assert svc.file_config_values("meshcore", "868", stick)["txpower"] == "14"      # unchanged (14 <= 14)
    r2 = commands.Instance("r2", band="868")                                         # the SPI board: model default 17
    assert svc.file_config_values("reticulum", "868", r2)["txpower"] == "14"        # G2: a lower stack default stays (min)


@pytest.mark.parametrize("edit, reason", [
    (("power_default_dbm = 14", "power_default_dbm = 21"), "power_default_dbm"),     # above the SX1262 range
    (('chip = "sx1262"', 'chip = "sx127x"'), "power_max_dbm"),                       # a raised top on an SX127x
    (("power_max_dbm = 22", "power_max_dbm = 23"), "power_max_dbm"),                 # above the SX1262's own 22
])
def test_the_loader_holds_the_power_fields_to_the_chip(tmp_path, edit, reason):
    row = ('[[model]]\nid = "fake"\nlabel = "Fake"\nshort = "fake"\ntransport = "usb-ch341"\nchip = "sx1262"\n'
           'bands = ["868"]\npower_default_dbm = 14\npower_max_dbm = 22\nhigh_power_switch = false\n'
           'usb = { vid = "1a86", pid = "5512" }\n  [[model.variant]]\n  key = "default"\n  preset = "fake"\n'
           '  pins = {}\n')
    path = tmp_path / "models.toml"
    path.write_text(row)
    assert radio_models.load(path)["fake"].power_max_dbm == 22
    path.write_text(row.replace(*edit))
    with pytest.raises(radio_models.ModelError, match=reason):
        radio_models.load(path)


def test_the_meshstick_takes_22_only_with_its_high_power_switch(tmp_path):
    # his P7 ("Let LPHC allow 22") under daemon 1.5.0 (21-22 need --high-power; plan 1.5.0 §8c, his word "High-power
    # option shall surface in LHPC"): the save gate, the panel range, the argv and the live gate move together.
    # red-before: 22 saved and sent without the switch; the Meshstick's grant was refused ("has no high-power switch")
    svc, fake = _svc(tmp_path)
    res = svc.save_daemon_params("daemon", {"POWER": "22"}, rid="r5")
    assert not res.ok and "POWER must be in [0, 20] (or up to 22 with the radio's high-power switch on)" in res.summary
    assert svc.save_daemon_params("daemon", {"POWER": "20"}, rid="r5").ok
    assert _power_row(svc, "daemon", "r5")["num"]["max"] == "20"
    assert "--high-power" not in _argv(svc, "r5")
    assert svc.high_power_state("868", rid="r5")["switch"] is True                    # the panel's control is live
    assert svc.set_high_power("r5", "on").ok                                         # the per-radio grant
    assert "--high-power" in _argv(svc, "r5")                                        # at the next start
    assert svc.save_daemon_params("daemon", {"POWER": "22"}, rid="r5").ok
    res = svc.save_daemon_params("daemon", {"POWER": "23"}, rid="r5")
    assert not res.ok and "POWER must be in [0, 22]" in res.summary, res.summary
    assert _power_row(svc, "daemon", "r5")["num"]["max"] == "22"
    assert radios.load(svc._paths).kind == radios.VALID                              # the list keeps the 22
    fake.serve["/tmp/loraconf-r5.sock"]["HIGHPOWER"] = "0"                          # still the old daemon
    res = svc.apply_daemon_params("daemon", rid="r5")
    assert ("/tmp/loraconf-r5.sock", "SET POWER=22") not in fake.sent, fake.sent
    fake.serve["/tmp/loraconf-r5.sock"]["HIGHPOWER"] = "1"                          # restarted with the flag
    res = svc.apply_daemon_params("daemon", rid="r5")
    assert ("/tmp/loraconf-r5.sock", "SET POWER=22") in fake.sent, (fake.sent, res.summary, res.details)
    res = svc.save_daemon_params("daemon", {"POWER": "21"}, rid="r4")                # no other SX1262
    assert not res.ok and "POWER must be in [0, 20]" in res.summary, res.summary
    assert svc.high_power_state("868", rid="r4")["switch"] is False
    svc, _fake = _svc(tmp_path / "instance", (*ROWS[:4], dataclasses.replace(STICK, high_power=True,
                                                                              high_power_band="868")))
    cfgmod.save_stack_config(svc._paths, "meshcore", {"dp_POWER": "22"}, inst=commands.Instance("r5"))
    assert _power_row(svc, "meshcore", "r5")["value"] == "22"                        # an instance's own 22 is read


def _argv(svc, rid):
    from lhpc.core import radio_start
    from lhpc.core.config import Config
    from lhpc.core.manifest import load_manifest
    daemon = next(c for st in load_manifest() for c in st.components if c.id == "loraham-daemon")
    params, _refusal, _notes = radio_start.start_plan(svc, rid)
    return commands.expand_argv(daemon.run_argv, daemon, params, Config().operator, "/rt", "/rt/src/x", "868")