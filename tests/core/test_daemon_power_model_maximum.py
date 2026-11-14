"""A34 (box H): `lhpc stack start daemon@r4` printed "[warn] 868: POWER=17 refused by the daemon (ERR INVALID)" for a
PineDio stick (pinedio-usb-1262, power_max_dbm 14): the daemon stack's POWER default 17 was sent to every radio, and
POWER was checked by chip family only. A radio's POWER default is capped at its model's maximum, and a save above it
is refused. Through the console's Apply (the SET set the start sends) and the radio panel's Save."""
import dataclasses

from lhpc.core import radios
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService

PINE = radios.Radio("r4", "pine64 868", "pinedio-usb-1262", "868", usb_port="1-1")
ROWS = (radios.Radio("r1", "loraham 433", "loraham", "433"), radios.Radio("r2", "loraham 868", "loraham", "868"), PINE)


@dataclasses.dataclass
class _Daemon(FakeSystem):
    """A daemon per CONF socket in `serve` (path -> STATUS fields) whose chip takes POWER up to `top[path]`."""
    serve: dict = dataclasses.field(default_factory=dict)
    top: dict = dataclasses.field(default_factory=dict)

    def request(self, path, payload, timeout, max_bytes):
        if path not in self.serve:
            raise ConnectionRefusedError(f"no daemon on {path}")
        st, line = self.serve[path], payload.decode("ascii").strip()
        if line.startswith("SET "):
            self.sent.append((path, line))
            k, _, v = line[4:].partition("=")
            if k == "POWER" and int(v) > self.top.get(path, 20):
                return b"ERR INVALID\n"
            st[k] = v
            return b"OK\n"
        if line == "GET STATUS":
            return ("STATUS " + " ".join(f"{k}={v}" for k, v in st.items()) + "\n").encode()
        if line == "GET STATS":
            return b"STATS TXOK=0 RXOK=0\n"
        if line.startswith("GET CHANNEL"):
            return f"CHANNEL MODE={st.get('MODE', 'LORA')} RSSI=-120 CADSTATE=NOTSCANNED BUSY=0\n".encode()
        return b"ERR UNKNOWN\n"


def _svc(tmp_path):
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    (tmp_path / "config" / "radios.toml").write_text(radios.render(ROWS))
    sock = "/tmp/loraconf-r4.sock"
    fake = _Daemon(serve={sock: {"RADIO": "READY", "ID": "r4", "BOUND": "1-1", "BAND": "868", "TXMODE": "MANAGED",
                                 "POWER": "10"}}, top={sock: 14})
    return ControllerService(system=fake.system, paths=Paths(runtime_root=tmp_path)), fake


def test_the_pinedio_gets_its_models_maximum_not_17(tmp_path):
    # red-before (b3fc6e60): "SET POWER=17" sent, refused by the daemon (ERR INVALID)
    svc, fake = _svc(tmp_path)
    res = svc.apply_daemon_params("daemon", rid="r4")
    text = "\n".join([res.summary, *res.details])
    assert ("/tmp/loraconf-r4.sock", "SET POWER=14") in fake.sent, fake.sent
    assert "POWER=17" not in text and "refused by the daemon" not in text, text


def test_a_saved_power_above_the_models_maximum_is_refused(tmp_path):
    # red-before (b3fc6e60): saved (17 is inside the SX1262 family range 0-20)
    svc, _fake = _svc(tmp_path)
    res = svc.save_daemon_params("daemon", {"POWER": "17"}, rid="r4")
    assert not res.ok and "POWER must be in [0, 14]" in res.summary, res.summary
    assert svc.save_daemon_params("daemon", {"POWER": "14"}, rid="r4").ok


def test_the_panel_shows_the_models_maximum(tmp_path):
    svc, _fake = _svc(tmp_path)
    row = next(r for r in svc.daemon_params_view("daemon", rid="r4")["rows"] if r["name"] == "POWER")
    assert (row["default"], row["num"]["max"]) == ("14", "14")


def test_an_sx127x_board_keeps_17(tmp_path):
    svc, _fake = _svc(tmp_path)
    assert svc.save_daemon_params("daemon", {"POWER": "17"}, rid="r1").ok
    row = next(r for r in svc.daemon_params_view("daemon", rid="r1")["rows"] if r["name"] == "POWER")
    assert row["default"] == "17"


def test_the_high_power_extra_stays_above_the_continuous_maximum(tmp_path):
    # loraham's power_max_dbm is 17 (the continuous window); exactly 20 with the +20 dBm grant stays allowed
    from lhpc.core import daemon_control
    assert daemon_control.validate_set("POWER", "20", "sx127x", True, 17) is None
    assert daemon_control.validate_set("POWER", "18", "sx127x", True, 17) is not None
    assert daemon_control.validate_set("POWER", "20", "sx127x", False, 17) is not None


def test_a_power_saved_above_the_models_maximum_before_the_cap_is_named_not_an_invalid_list(tmp_path, monkeypatch,
                                                                                             capsys, web):
    # G1-B3-CODE B2: a list saved before the cap with dp_POWER 17 on the PineDio still loads VALID and the start's
    # gate passes the unaffected r1 (G1-B3-CODE-RE2 NOTE 1); the Hardware page and `lhpc hardware` name the row
    # (G1-B3-CODE-RE N2: through the route and the CLI); Apply sends it as before and the daemon refuses it
    old = dataclasses.replace(PINE, dp=(("POWER", "17"),))
    svc, fake, page_notes, cli_power_lines = _public(tmp_path, monkeypatch, capsys, web, (*ROWS[:2], old))
    note = "POWER 17 is above the model maximum 14 dBm: lower it"
    assert svc.hardware_configured() and svc.hardware_block("daemon@r1") == ""          # the start's gate passes r1
    assert radios.load(svc._paths).kind == radios.VALID
    assert f"⚠ {note}" in page_notes()
    (line,) = cli_power_lines()
    assert line.split()[0] == "r4" and f"({note})" in line, line
    res = svc.apply_daemon_params("daemon", rid="r4")
    assert ("/tmp/loraconf-r4.sock", "SET POWER=17") in fake.sent and "POWER=17" in "\n".join(res.details), res
    at_max = dataclasses.replace(PINE, dp=(("POWER", "14"),))
    (tmp_path / "config" / "radios.toml").write_text(radios.render((*ROWS[:2], at_max)))
    assert not any("POWER" in n for n in page_notes()) and cli_power_lines() == []          # its maximum is fine


def test_an_instance_sets_power_saved_before_the_cap_is_sent_as_before(tmp_path):
    # second read B1: meshcore@r4's own set holds dp_POWER 17 (saved before the cap): the panel shows it and the
    # start sends it, as before (the daemon refuses it), never a silent 14
    from lhpc.core import commands, config as cfgmod
    svc, fake = _svc(tmp_path)
    cfgmod.save_stack_config(svc._paths, "meshcore", {"dp_POWER": "17"}, inst=commands.Instance("r4"))
    row = next(r for r in svc.daemon_params_view("meshcore", rid="r4")["rows"] if r["name"] == "POWER")
    assert row["value"] == "17", row
    lines, _tx_ok, _gate = svc._apply_stack_daemon_params("meshcore", "868", "r4")
    assert ("/tmp/loraconf-r4.sock", "SET POWER=17") in fake.sent, (fake.sent, lines)


def _public(tmp_path, monkeypatch, capsys, web, rows):
    """The service on `rows`, and its two public readers of the row notes: the Hardware page's notes (GET /hardware)
    and the POWER lines of `lhpc hardware` (no USB stick is attached)."""
    from htmlq import parse
    from lhpc.adapters.cli import main as cli
    from lhpc.core import usb_access
    monkeypatch.setattr(usb_access, "scan", lambda *a, **k: usb_access.Scan(usb_access.Probe.PRESENT, ()))
    svc, fake = _svc(tmp_path)
    (tmp_path / "config" / "radios.toml").write_text(radios.render(rows))
    monkeypatch.setattr(cli, "ControllerService", lambda *a, **k: svc)

    def page_notes():
        r = web(service_factory=lambda: svc).get("/hardware", headers={"Host": "localhost:8443"})
        assert r.status_code == 200
        return [e.text for e in parse(r.get_data(as_text=True)).find("span", class_="note-bad")]

    def cli_power_lines():
        assert cli.main(["hardware"]) == 0
        return [x for x in capsys.readouterr().out.splitlines() if "POWER" in x]
    return svc, fake, page_notes, cli_power_lines


def test_a_granted_plus_20_on_an_sx127x_row_is_no_note(tmp_path, monkeypatch, capsys, web):
    # second read B2: loraham's maximum is 17, and exactly 20 with the +20 grant on its band stays allowed: no note
    granted = dataclasses.replace(ROWS[0], high_power=True, high_power_band="433", dp=(("POWER", "20"),))
    _svc_, _fake, page_notes, cli_power_lines = _public(tmp_path, monkeypatch, capsys, web, (granted, *ROWS[1:]))
    assert radios.load(_svc_._paths).kind == radios.VALID
    assert not any("POWER" in n for n in page_notes()) and cli_power_lines() == []
