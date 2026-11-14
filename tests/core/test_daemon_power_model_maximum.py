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


def test_a_power_saved_above_the_models_maximum_before_the_cap_is_named_not_an_invalid_list(tmp_path):
    # G1-B3-CODE B2: a list saved before the cap with dp_POWER 17 on the PineDio stays valid (every other radio still
    # starts); the Hardware page and `lhpc hardware` name the row; Apply sends it as before and the daemon refuses it
    from lhpc.core import radio_start
    old = dataclasses.replace(PINE, dp=(("POWER", "17"),))
    svc, fake = _svc(tmp_path)
    (tmp_path / "config" / "radios.toml").write_text(radios.render((*ROWS[:2], old)))
    assert radios.load(svc._paths).kind == radios.VALID
    note = "POWER 17 is above the model maximum 14 dBm: lower it"
    view = radio_start.hardware_view(svc)
    assert [note in r["problems"] for r in view["rows"]] == [False, False, True], view["rows"]
    assert [note in line for line in radio_start.radio_list_lines(svc)] == [False, False, True]
    res = svc.apply_daemon_params("daemon", rid="r4")
    assert ("/tmp/loraconf-r4.sock", "SET POWER=17") in fake.sent and "POWER=17" in "\n".join(res.details), res
    at_max = dataclasses.replace(PINE, dp=(("POWER", "14"),))
    (tmp_path / "config" / "radios.toml").write_text(radios.render((*ROWS[:2], at_max)))
    assert not any("POWER" in p for r in radio_start.hardware_view(svc)["rows"] for p in r["problems"])   # its maximum
