"""testlab/hardware/peer.py, the RF proof's peer driver, against fake devices on a fake clock: the MeshCom power read (two
`--lora` queries, each window one echo and one answer, both agreeing), every power SET exiting non-zero on a readback mismatch,
the devices named by the lab's local file, and rns-exchange's second send waiting for the runner's go file.
PEER=<path> picks the module under test (a red-before runs these against an older driver)."""
import asyncio
import importlib.util
import os
import pathlib
import sys
import tempfile
import types
from typing import ClassVar

import pytest

PEER = os.environ.get("PEER", str(pathlib.Path(__file__).parents[2] / "hardware" / "peer.py"))
spec = importlib.util.spec_from_file_location("peer_under_test", PEER)
peer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(peer)

# the T-Deck's answer to `--lora`, MeshCom 4.40a, captured 2026-11-10 (byte for byte, its echo included)
TDECK_LORA = (b"--lora\r\r\nMeshCom 4.40a\r\n...LoRa RF-Frequ: <433.1750 MHz>\r\n...LoRa RF-Power: <22 dBm>\r\n"
              b"...LoRa RF-BW:    <250 kHz>\r\n...LoRa RF-SF:    <11>\r\n...LoRa RF-CR:    <4/6>\r\n\n")
STALE_LORA = TDECK_LORA.replace(b"<22 dBm>", b"<2 dBm>")        # an earlier query's answer at another power
BOOT = b"...FREQ 433.1750 MHz TXPWR 2 dBm RXBOOST off\r\n"     # MeshCom's boot line (boot1.log:81)


LAB_PEERS = {"tdeck": "serial usb-Example_TDeck-if00 hub1", "tracker": "serial usb-Example_Tracker-if00 hub2",
             "g2": "serial usb-Example_G2-if00 hub2", "tdpro": "ble 00:00:5E:00:53:01 -",
             "rnode": "rnode usb-Example_RNode-if00-port0 hub1 serial_unique=false instance=lab-rns port=40001 control=40002"}


@pytest.fixture(autouse=True)
def lab_peers(monkeypatch):
    for name, value in LAB_PEERS.items():
        monkeypatch.setenv(f"PEER_{name}", value)


class Clock:
    t = 0.0

    def monotonic(self):
        return self.t


def answer(p):
    return TDECK_LORA.replace(b"<22 dBm>", f"<{p} dBm>".encode())


STALE2 = answer(2)                                             # an earlier query's answer at 2 dBm (the verdict's STALE_LORA)


class Port:
    """The MeshCom T-Deck on a fake clock: it holds `power`; a `--txpower v` sets it when `takes`; each `--lora` gets the answer of
    `windows[i]` = (late, mode): `late` (a delayed earlier answer) arrives first, then mode "now" = the answer of the device's CURRENT
    power, None = no answer. After the listed windows every query is answered "now". A line costs 0.01 s, an empty read its timeout."""
    def __init__(self, clock, power=22, takes=True, windows=(), pending=b""):
        self.c, self.power, self.takes, self.windows, self.n = clock, power, takes, list(windows), 0
        self.buf = pending.splitlines(keepends=True)
        self.written, self.timeout = [], 0.5

    def reset_input_buffer(self):
        self.buf = []

    def write(self, b):
        self.written.append(b)
        if b == b"--lora\r\n":
            late, mode = self.windows[self.n] if self.n < len(self.windows) else (b"", "now")
            self.n += 1
            self.buf += late.splitlines(keepends=True) + (answer(self.power).splitlines(keepends=True) if mode == "now" else [])
        elif b.startswith(b"--txpower ") and self.takes:
            self.power = int(b.split()[1])

    def readline(self):
        if self.buf:
            self.c.t += 0.01
            return self.buf.pop(0)
        self.c.t += self.timeout
        return b""

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.fixture
def clock(monkeypatch):
    c = Clock()
    monkeypatch.setattr(peer.time, "monotonic", c.monotonic)
    monkeypatch.setattr(peer.time, "sleep", lambda s: setattr(c, "t", c.t + s))
    return c


class FakeSerialModule(types.ModuleType):
    def __init__(self, port):
        super().__init__("serial")
        self.port = port

    def Serial(self, *a, **k):
        return self.port


def txpowers(port):
    """The power SETs the driver wrote, in order."""
    return [w for w in port.written if w.split(b" ")[0] == b"--txpower"]


def power(port, p, monkeypatch):
    monkeypatch.setitem(sys.modules, "serial", FakeSerialModule(port))
    peer.meshcom_power(p)


def test_a_read_reads_the_current_power(clock, monkeypatch, capsys):
    port = Port(clock)
    power(port, None, monkeypatch)
    assert capsys.readouterr().out.strip() == "POWER 22" and port.written == [b"--lora\r\n"] * 2


def test_a_set_that_takes_walks_o_then_the_sentinel_then_p(clock, monkeypatch, capsys):
    port = Port(clock)
    power(port, "2", monkeypatch)
    assert capsys.readouterr().out.strip().splitlines()[-1] == "POWER 2" and port.power == 2
    assert txpowers(port) == [b"--txpower 3\r\n", b"--txpower 2\r\n"]


def test_the_verdicts_case_an_ignored_set_and_two_stale_windows_never_reads_as_done(clock, monkeypatch):
    # an ignored --txpower 2, then the current queries unanswered and each window filled by a delayed low-power answer of an
    # earlier query: a read of two windows alone printed "POWER 2" with no exception
    port = Port(clock, takes=False, windows=[(STALE2, None)] * 6)
    with pytest.raises(RuntimeError):
        power(port, "2", monkeypatch)


@pytest.mark.parametrize("kw,why", [
    ({"takes": False}, "readback 22 is not the requested 3"),                          # the device ignores every SET
    ({"takes": False, "windows": [(b"", "now"), (b"", "now"), (STALE2, None), (STALE2, None)]}, "readback 2 is not the requested 3"),
    ({"windows": [(b"", "now")] * 4 + [(b"", None)]}, "no echo of --lora"),            # the target's read gets no answer
])
def test_a_chain_that_does_not_hold_is_refused_by_name(clock, monkeypatch, kw, why):
    with pytest.raises(RuntimeError, match=why):
        power(Port(clock, **kw), "2", monkeypatch)


def test_a_device_already_at_the_target_still_walks_through_the_sentinel(clock, monkeypatch, capsys):
    port = Port(clock, power=2)
    power(port, "2", monkeypatch)
    assert capsys.readouterr().out.strip().splitlines()[-1] == "POWER 2"
    assert txpowers(port) == [b"--txpower 3\r\n", b"--txpower 2\r\n"]


@pytest.mark.parametrize("o,p,s", [(22, 2, 3), (3, 2, 4), (2, 2, 3), (4, 3, 2), (2, 22, 3), (22, 22, 2), (17, 17, 2), (2, 3, 4)])
def test_the_sentinel_is_the_lowest_value_of_2_to_17_that_is_neither_o_nor_p(o, p, s):
    assert peer.meshcom_sentinel(o, p) == s


def test_every_o_and_p_has_a_sentinel_of_at_most_17():
    for o in range(-5, 31):
        for p in range(-5, 31):
            s = peer.meshcom_sentinel(o, p)
            assert 2 <= s <= 17 and s not in (o, p)


@pytest.mark.parametrize("o,p,sets", [(2, 22, [b"--txpower 3\r\n", b"--txpower 22\r\n"]),       # the runner's undo: back to 22 from 2
                                      (22, 22, [b"--txpower 2\r\n", b"--txpower 22\r\n"])])
def test_a_restore_above_17_through_the_cli_walks_the_sentinel_and_reads_p(clock, monkeypatch, capsys, o, p, sets):
    # the meshcom-power CLI path the runner's undo runs (`peer.py meshcom-power 22`): a sentinel exists above 17 too
    port = Port(clock, power=o)
    monkeypatch.setitem(sys.modules, "serial", FakeSerialModule(port))
    assert (peer.main(["meshcom-power", str(p)]) or 0) == 0
    assert capsys.readouterr().out.strip().splitlines()[-1] == f"POWER {p}" and port.power == p
    assert txpowers(port) == sets


@pytest.mark.parametrize("windows,why", [
    (((STALE2, None), (b"", None)), "no echo of --lora"),
    (((STALE2, None), (b"", "now")), "two --lora answers disagree: 2 vs 22"),
    (((STALE2, "now"), (b"", "now")), "2 echoes of --lora"),
])
def test_a_plain_read_without_two_agreeing_fresh_answers_is_refused(clock, monkeypatch, windows, why):
    with pytest.raises(RuntimeError, match=why):
        power(Port(clock, windows=windows), None, monkeypatch)


class FakeIface:
    held: ClassVar[dict] = {"tx": 22}

    def __init__(self, takes):
        self.takes = takes
        lora = types.SimpleNamespace(tx_power=FakeIface.held["tx"])
        self.localNode = types.SimpleNamespace(localConfig=types.SimpleNamespace(lora=lora), writeConfig=self._write)

    def _write(self, _what):
        if self.takes:
            FakeIface.held["tx"] = self.localNode.localConfig.lora.tx_power

    def close(self):
        pass


@pytest.mark.parametrize("takes,ok", [(True, True), (False, False)])
def test_a_meshtastic_set_exits_non_zero_unless_the_node_holds_it(clock, monkeypatch, takes, ok):
    FakeIface.held["tx"] = 22
    monkeypatch.setattr(peer, "mt_iface", lambda: FakeIface(takes))
    if ok:
        peer.mt_power("1")
    else:
        with pytest.raises(RuntimeError, match="readback 22 is not the requested 1"):
            peer.mt_power("1")


@pytest.mark.parametrize("takes,ok", [(True, True), (False, False)])
def test_a_meshcore_set_exits_non_zero_unless_the_device_answers_it(monkeypatch, takes, ok):
    state = {"tx": 17}

    class Cmds:
        async def set_tx_power(self, p):
            if takes:
                state["tx"] = p

        async def send_appstart(self):
            return types.SimpleNamespace(payload={"tx_power": state["tx"]})
    monkeypatch.setattr(peer, "mc_run", lambda fn: asyncio.run(fn(types.SimpleNamespace(commands=Cmds()))))
    if ok:
        peer.mc_power("5")
    else:
        with pytest.raises(RuntimeError, match="readback 17 is not the requested 5"):
            peer.mc_power("5")


def test_meshcom_read_switches_setinfo_on_for_the_read_and_off_after(clock, monkeypatch, capsys):
    # The T-Deck prints received MeshCom frames (RX-LoRa2) only with --setinfo on; --debug never printed them.
    port = Port(clock, pending=b"RX-LoRa2 ... abc123 ...\r\n")
    monkeypatch.setitem(sys.modules, "serial", FakeSerialModule(port))
    assert peer.main(["meshcom-read", "abc123", "5"]) == 0
    assert port.written == [b"--setinfo on\r\n", b"--setinfo off\r\n"]
    assert capsys.readouterr().out.split()[:2] == ["SEEN", "abc123"]


@pytest.mark.parametrize("act", [["meshcom-send", "abc123"], ["aprs-read", "abc123", "5"], ["meshcom-power"]])
def test_a_peer_the_local_file_does_not_name_is_refused_by_name(clock, monkeypatch, act):
    monkeypatch.setitem(sys.modules, "serial", FakeSerialModule(Port(clock)))
    name = "tracker" if act[0].startswith("aprs") else "tdeck"
    monkeypatch.delenv(f"PEER_{name}")
    with pytest.raises(RuntimeError, match=f"PEER_{name} is not set"):
        peer.main(act)


def test_the_device_is_the_by_id_name_the_local_file_gives(clock, monkeypatch):
    opened = []

    class Mod(FakeSerialModule):
        def Serial(self, *a, **k):
            opened.append(a[0])
            return self.port
    monkeypatch.setitem(sys.modules, "serial", Mod(Port(clock)))
    peer.main(["meshcom-send", "abc123"])
    assert opened == ["/dev/serial/by-id/usb-Example_TDeck-if00"]


CONFIGS = []                                                     # the scratch configs the fake Reticulum was given


# rns-exchange's second TX (the LXMF message) waits for the runner's go file; no go file = no handle_outbound
def _fake_rns(monkeypatch, sent, box=None):
    # `box` = {"answers": True} makes the box send its LXMF message with "in1", {"confirms": True} its delivery proof
    rns = types.ModuleType("RNS")
    lxmf = types.ModuleType("LXMF")
    rns.LOG_ERROR = 0

    class Ret:
        is_connected_to_shared_instance = True

        def __init__(self, configdir=None, loglevel=None):
            CONFIGS.append(pathlib.Path(configdir, "config").read_text())
    rns.Reticulum = Ret
    handlers = []

    class Transport:
        @staticmethod
        def register_announce_handler(h):
            handlers.append(h)
    rns.Transport = Transport

    class Ident:
        @staticmethod
        def recall(h):
            return "ident"

        def __init__(self):
            pass
    rns.Identity = Ident

    class Dest:
        OUT = SINGLE = 0

        def __init__(self, *a):
            pass
    rns.Destination = Dest
    rns.hexrep = lambda b, delimit=False: "ab" * 16

    class Me:
        hash = b"x"

    class Router:
        def __init__(self, storagepath=None):
            pass

        def register_delivery_identity(self, i, display_name=None):
            return Me()

        def register_delivery_callback(self, cb):
            self.incoming = cb

        def announce(self, h):
            for x in handlers:                                   # the box's announce is heard at once
                x.received_announce(b"box", None, b"app")

        MAX_DELIVERY_ATTEMPTS = 5                                # LXMF 1.1.1's value

        def handle_outbound(self, m):
            sent.append((m, lxmf.LXMRouter.MAX_DELIVERY_ATTEMPTS))
            if (box or {}).get("confirms"):
                m.proof(m)
            if (box or {}).get("answers"):
                self.incoming(types.SimpleNamespace(content=b"in1"))
    lxmf.LXMRouter = Router
    lxmf.display_name_from_app_data = lambda d: "LHPC"

    class Msg:
        OPPORTUNISTIC = 1

        def __init__(self, *a, **k):
            pass

        def register_delivery_callback(self, cb):
            self.proof = cb
    lxmf.LXMessage = Msg
    monkeypatch.setitem(sys.modules, "RNS", rns)
    monkeypatch.setitem(sys.modules, "LXMF", lxmf)


class FastClock:
    t = 0.0

    def monotonic(self):
        return self.t

    def sleep(self, s):
        self.t += s


@pytest.fixture
def rns_sent(monkeypatch):
    sent, c = [], FastClock()
    _fake_rns(monkeypatch, sent)
    monkeypatch.setattr(peer.time, "monotonic", c.monotonic)
    monkeypatch.setattr(peer.time, "sleep", c.sleep)
    return sent


def test_rns_exchange_no_go_file_no_second_tx(rns_sent, tmp_path, capsys):
    assert peer.main(["rns-exchange", "in1", "LHPC", "5", str(tmp_path / "dest"), "out1", str(tmp_path / "go")]) == peer.NOT_SEEN
    assert rns_sent == [] and "TX2-NOT-GATED" in capsys.readouterr().out   # the public path: the runner's own call form


def test_rns_exchange_go_file_sends_once(rns_sent, tmp_path, capsys):
    (tmp_path / "go").write_text("")
    peer.rns_exchange("in1", "LHPC", 5, str(tmp_path / "dest"), "out1", str(tmp_path / "go"))
    assert len(rns_sent) == 1 and "TX2-NOT-GATED" not in capsys.readouterr().out


def test_an_rnode_the_local_file_does_not_name_is_refused_before_anything_opens(rns_sent, monkeypatch, tmp_path):
    made = []
    monkeypatch.setattr(tempfile, "mkdtemp", lambda **k: made.append("mkdtemp") or str(tmp_path / "scratch"))
    monkeypatch.setattr(sys.modules["RNS"], "Reticulum", lambda **k: made.append("reticulum"))
    monkeypatch.delenv("PEER_rnode")
    with pytest.raises(RuntimeError, match="PEER_rnode is not set"):
        peer.main(["rns-exchange", "in1", "LHPC", "5", str(tmp_path / "dest"), "out1", str(tmp_path / "go")])
    assert made == [] and rns_sent == []


def test_rns_exchange_removes_its_scratch_config(rns_sent, monkeypatch, tmp_path):
    made = []
    real = tempfile.mkdtemp
    monkeypatch.setattr(tempfile, "mkdtemp", lambda **k: made.append(real(dir=tmp_path, **k)) or made[-1])
    peer.main(["rns-exchange", "in1", "LHPC", "5", str(tmp_path / "dest"), "out1", str(tmp_path / "go")])
    assert len(made) == 1 and not os.path.exists(made[0])


@pytest.mark.parametrize("box,rc,last", [({"answers": True, "confirms": True}, 0, "SEEN in1"),
                                         ({"answers": True}, 3, "NOT-SEEN"),          # heard, but our message unconfirmed
                                         ({"confirms": True}, 3, "NOT-SEEN")])
def test_rns_exchange_succeeds_only_with_the_boxs_message_and_its_delivery_proof(monkeypatch, tmp_path, capsys, box, rc, last):
    sent, c = [], FastClock()
    _fake_rns(monkeypatch, sent, box)
    monkeypatch.setattr(peer.time, "monotonic", c.monotonic)
    monkeypatch.setattr(peer.time, "sleep", c.sleep)
    (tmp_path / "go").write_text("")
    assert peer.main(["rns-exchange", "in1", "LHPC", "5", str(tmp_path / "dest"), "out1", str(tmp_path / "go")]) == rc
    assert " ".join(capsys.readouterr().out.splitlines()[-1].split()[:2]) == last


def test_the_shared_rnsd_instance_comes_from_the_local_file(rns_sent, tmp_path):
    CONFIGS.clear()
    (tmp_path / "go").write_text("")
    peer.main(["rns-exchange", "in1", "LHPC", "5", str(tmp_path / "dest"), "out1", str(tmp_path / "go")])
    assert len(CONFIGS) == 1 and [line.strip() for line in CONFIGS[0].splitlines() if "instance" in line] == [
        "share_instance = Yes", "instance_name = lab-rns", "shared_instance_port = 40001", "instance_control_port = 40002"]


@pytest.mark.parametrize("fields,why", [
    ("port=40001 control=40002", "no valid instance"), ("instance=a/b port=40001 control=40002", "no valid instance"),
    ("instance=lab-rns control=40002", "no valid port"), ("instance=lab-rns port=0 control=40002", "no valid port"),
    ("instance=lab-rns port=70000 control=40002", "no valid port"), ("instance=lab-rns port=4x control=40002", "no valid port"),
    ("instance=lab-rns port=40001", "no valid control"), ("instance=lab-rns port=40001 control=40001", "are the same"),
])
def test_a_missing_or_malformed_rns_instance_is_refused_before_anything_opens(rns_sent, monkeypatch, tmp_path, fields, why):
    made = []
    monkeypatch.setattr(tempfile, "mkdtemp", lambda **k: made.append("mkdtemp") or str(tmp_path / "scratch"))
    monkeypatch.setenv("PEER_rnode", f"rnode usb-Example_RNode-if00-port0 hub1 {fields}")
    with pytest.raises(RuntimeError, match=why):
        peer.main(["rns-exchange", "in1", "LHPC", "5", str(tmp_path / "dest"), "out1", str(tmp_path / "go")])
    assert made == [] and rns_sent == []


def test_rns_exchange_needs_the_go_file_argument(capsys):
    assert peer.main(["rns-exchange", "in1", "LHPC", "5", "/x/dest", "out1"]) == 2


def test_rns_exchange_message_sent_once_no_retries(rns_sent, tmp_path):
    # the opportunistic message goes with MAX_DELIVERY_ATTEMPTS 0 = exactly one send (no retry)
    (tmp_path / "go").write_text("")
    peer.main(["rns-exchange", "in1", "LHPC", "5", str(tmp_path / "dest"), "out1", str(tmp_path / "go")])
    assert [a for _m, a in rns_sent] == [0]
