"""testlab/hardware/peer.py, the RF proof's peer driver, against fake devices on a fake clock: the MeshCom power read (two
`--lora` queries, each window one echo and one answer, both agreeing), every power SET exiting non-zero on a readback mismatch,
the devices named by the lab's local file, and rns-exchange's second send waiting for the runner's go file.
PEER=<path> picks the module under test (a red-before runs these against an older driver)."""
import ast
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


N1, IN1, OUT1 = "rfkit 0000aaaa", "rfkit 0000bbbb", "rfkit 0000cccc"     # the runner's nonce form
CTX = "RFPROOF_PEER_CTX"


@pytest.fixture(autouse=True)
def lab_peers(monkeypatch):
    for name, value in LAB_PEERS.items():
        monkeypatch.setenv(f"PEER_{name}", value)
    monkeypatch.setenv(CTX, "tx")       # the runner's tx context for the sends these rows make; the refusal rows set their own


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


@pytest.mark.parametrize("o", [2, 22])
def test_a_restore_above_17_through_the_cli_is_refused_before_the_port_opens(clock, monkeypatch, capsys, o):
    # THE power domain 0..17 covers the restore too (the runner's pdom; its restore-above-17 was superseded): the undo's
    # `peer.py meshcom-power 22` is a usage error, the T-Deck's port never opens and nothing is written
    port = Port(clock, power=o)
    monkeypatch.setitem(sys.modules, "serial", FakeSerialModule(port))
    monkeypatch.setenv(CTX, "restore")
    assert peer.main(["meshcom-power", "22"]) == 2 and port.written == [] and port.power == o


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


def rx_meshcom(text, label="RX-LoRa2"):
    """A MeshCom console line of printBuffer_aprs's form (loop_functions.cpp), a text frame from XX0XXX-1."""
    return (f"12:00:01 {label} 041 : x1A2B3C4D H02 S0 T0 M00 XX0XXX-1>*:{text} HW:44 MOD:3/1 FCS:1234 FW:35:t LH:44\r\n").encode()


def rx_aprs(text, label="LoRa Rx"):
    """A CA2RXU tracker console line (lora_utils.cpp receivePacket: logger "LoRa Rx", "---> <TNC2 frame>"), an APRS message."""
    return f"[INFO][{label}] ---> XX0XXX-2>APGRWO,WIDE1-1::XX0XXX-7 :{text}{{001\r\n".encode()


def test_meshcom_read_switches_setinfo_on_for_the_read_and_off_after(clock, monkeypatch, capsys):
    # The T-Deck prints received MeshCom frames (RX-LoRa2) only with --setinfo on; --debug never printed them.
    port = Port(clock, pending=rx_meshcom(N1))
    monkeypatch.setitem(sys.modules, "serial", FakeSerialModule(port))
    assert peer.main(["meshcom-read", N1, "5"]) == 0
    assert port.written == [b"--setinfo on\r\n", b"--setinfo off\r\n"]
    assert capsys.readouterr().out.split()[:3] == ["SEEN", *N1.split()]


# G2-HWKITC B2: a console line that merely holds the nonce (a command echo, the peer's own TX, another frame kind) is never
# reception evidence; only a frame the peer received, the nonce in its text
NOT_RX = {"meshcom-read": [f"TX command: {N1}\r\n".encode(), f"{N1}\r\n".encode(), f"::{N1}\r\n".encode(),
                           rx_meshcom(N1, "NEW-TXT"), rx_meshcom(N1, "TX-LoRa "),
                           rx_meshcom("other").replace(b"HW:44", f"HW:44 {N1}".encode())],
          "aprs-read": [f"TX command: {N1}\r\n".encode(), rx_aprs(N1, "LoRa Tx"), f"[LoRa Rx] Rx failed {N1}\r\n".encode(),
                        rx_aprs("other").replace(b"{001", f"{{{N1}".encode())]}
IS_RX = {"meshcom-read": rx_meshcom(N1), "aprs-read": rx_aprs(N1)}


@pytest.mark.parametrize("act,line", [(a, x) for a, xs in NOT_RX.items() for x in xs])
def test_a_line_that_is_no_received_frame_is_not_seen(clock, monkeypatch, capsys, act, line):
    port = Port(clock, pending=line)
    monkeypatch.setitem(sys.modules, "serial", FakeSerialModule(port))
    assert peer.main([act, N1, "5"]) == peer.NOT_SEEN and capsys.readouterr().out.strip() == "NOT-SEEN"


@pytest.mark.parametrize("act", sorted(IS_RX))
def test_a_received_frame_with_the_nonce_in_its_text_is_seen(clock, monkeypatch, capsys, act):
    port = Port(clock, pending=b"\x1b[32m" + IS_RX[act])        # a colour code before it changes nothing
    monkeypatch.setitem(sys.modules, "serial", FakeSerialModule(port))
    assert peer.main([act, N1, "5"]) == 0 and capsys.readouterr().out.split()[:3] == ["SEEN", *N1.split()]


# G2-HWKITC B6: --setinfo on is inside the protected region, a SIGTERM unwinds like a SIGINT, and an off that cannot be sent
# fails the read naming the device state
def test_an_interrupt_right_after_setinfo_on_still_sends_setinfo_off(clock, monkeypatch):
    port = Port(clock)

    def sleep(s):                                                  # SIGINT in the pause after the first write
        if port.written == [b"--setinfo on\r\n"]:
            raise KeyboardInterrupt
        clock.t += s
    monkeypatch.setattr(peer.time, "sleep", sleep)
    monkeypatch.setitem(sys.modules, "serial", FakeSerialModule(port))
    with pytest.raises(KeyboardInterrupt):
        peer.main(["meshcom-read", N1, "5"])
    assert port.written == [b"--setinfo on\r\n", b"--setinfo off\r\n"]


def test_a_sigterm_during_the_read_still_sends_setinfo_off(clock, monkeypatch):
    import signal
    port = Port(clock)
    term = []

    def readline():                                                # SIGTERM arrives: the handler installed for the read runs
        h = signal.getsignal(signal.SIGTERM)
        assert callable(h), "no SIGTERM handler during the read: a TERM ends it without --setinfo off"
        term.append(h)
        h(signal.SIGTERM, None)
    port.readline = readline
    monkeypatch.setitem(sys.modules, "serial", FakeSerialModule(port))
    with pytest.raises(SystemExit) as e:
        peer.main(["meshcom-read", N1, "5"])
    assert e.value.code == 128 + signal.SIGTERM and port.written == [b"--setinfo on\r\n", b"--setinfo off\r\n"]
    assert signal.getsignal(signal.SIGTERM) is not term[0]          # the read's handler is gone after it


def test_the_setinfo_off_is_written_with_sigint_and_sigterm_held_off(clock, monkeypatch):
    import signal
    port = Port(clock, pending=rx_meshcom(N1))
    write, held = port.write, []

    def watching(b):
        if b == b"--setinfo off\r\n":
            held.append((signal.getsignal(signal.SIGINT), signal.getsignal(signal.SIGTERM)))
        write(b)
    port.write = watching
    monkeypatch.setitem(sys.modules, "serial", FakeSerialModule(port))
    assert peer.main(["meshcom-read", N1, "5"]) == 0
    assert held == [(signal.SIG_IGN, signal.SIG_IGN)] and signal.getsignal(signal.SIGINT) is not signal.SIG_IGN


def test_a_setinfo_off_that_cannot_be_sent_fails_the_read_by_name(clock, monkeypatch, capsys):
    port = Port(clock, pending=rx_meshcom(N1))
    write = port.write

    def failing(b):
        if b == b"--setinfo off\r\n":
            raise OSError("the port is gone")
        write(b)
    port.write = failing
    monkeypatch.setitem(sys.modules, "serial", FakeSerialModule(port))
    with pytest.raises(RuntimeError, match=r"--setinfo off.*was not sent .*may keep the changed mode"):
        peer.main(["meshcom-read", N1, "5"])
    assert "SEEN" not in capsys.readouterr().out


# G2-HWKITC B1: the driver itself refuses a power value outside THE domain, a set outside the runner's setter and a
# transmission outside its tx, before any device opens (`peer.py mt-power 20`, `peer.py mt-send rfkit123` ran on their own)
@pytest.mark.parametrize("act,ctx", [(["mt-power", "20"], "set"), (["mt-power", "30"], "set"), (["mt-power", "-20"], "set"),
                                     (["mt-power", "0"], "set"), (["mt-power", "5"], ""), (["mt-power", "5"], "tx"),
                                     (["mt-send", "rfkit123"], "tx"), (["mt-send", N1], ""), (["mt-send", N1], "set"),
                                     (["meshcom-power", "18"], "restore"), (["meshcom-send", N1], ""),
                                     (["mc-power", "18"], "set"), (["mc-send", N1], "restore")])
def test_the_driver_refuses_a_call_outside_the_runners_bounds_before_any_device(monkeypatch, capsys, act, ctx):
    opened = []
    monkeypatch.setattr(peer, "mt_iface", lambda: opened.append("g2"))
    monkeypatch.setattr(peer, "mc_run", lambda fn: opened.append("tdpro"))
    monkeypatch.setitem(sys.modules, "serial", types.SimpleNamespace(Serial=lambda *a, **k: opened.append("serial")))
    monkeypatch.setenv(CTX, ctx)
    assert peer.main(act) == 2 and opened == [] and capsys.readouterr().err.strip()


@pytest.mark.parametrize("act,ctx", [(["mt-power", "1"], "set"), (["mt-power", "17"], "restore")])
def test_a_set_in_the_domain_from_the_runners_setter_runs(clock, monkeypatch, capsys, act, ctx):
    FakeIface.held["tx"] = 22
    monkeypatch.setattr(peer, "mt_iface", lambda: FakeIface(True))
    monkeypatch.setenv(CTX, ctx)
    assert (peer.main(act) or 0) == 0 and capsys.readouterr().out.strip().splitlines()[-1] == f"POWER {act[1]}"


@pytest.mark.parametrize("act", [["meshcom-send", N1], ["aprs-read", N1, "5"], ["meshcom-power"]])
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
    peer.main(["meshcom-send", N1])
    assert opened == ["/dev/serial/by-id/usb-Example_TDeck-if00"]


CONFIGS = []                                                     # the scratch configs the fake Reticulum was given


# rns-exchange's second TX (the LXMF message) waits for the runner's go file; no go file = no handle_outbound
def _fake_rns(monkeypatch, sent, box=None):
    # `box` = {"answers": True} makes the box send its LXMF message with IN1, {"confirms": True} its delivery proof
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
                self.incoming(types.SimpleNamespace(content=IN1.encode()))
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
    assert peer.main(["rns-exchange", IN1, "LHPC", "5", str(tmp_path / "dest"), OUT1, str(tmp_path / "go")]) == peer.NOT_SEEN
    assert rns_sent == [] and "TX2-NOT-GATED" in capsys.readouterr().out   # the public path: the runner's own call form


def test_rns_exchange_go_file_sends_once(rns_sent, tmp_path, capsys):
    (tmp_path / "go").write_text("")
    peer.rns_exchange(IN1, "LHPC", 5, str(tmp_path / "dest"), OUT1, str(tmp_path / "go"))
    assert len(rns_sent) == 1 and "TX2-NOT-GATED" not in capsys.readouterr().out


def test_an_rnode_the_local_file_does_not_name_is_refused_before_anything_opens(rns_sent, monkeypatch, tmp_path):
    made = []
    monkeypatch.setattr(tempfile, "mkdtemp", lambda **k: made.append("mkdtemp") or str(tmp_path / "scratch"))
    monkeypatch.setattr(sys.modules["RNS"], "Reticulum", lambda **k: made.append("reticulum"))
    monkeypatch.delenv("PEER_rnode")
    with pytest.raises(RuntimeError, match="PEER_rnode is not set"):
        peer.main(["rns-exchange", IN1, "LHPC", "5", str(tmp_path / "dest"), OUT1, str(tmp_path / "go")])
    assert made == [] and rns_sent == []


def test_rns_exchange_removes_its_scratch_config(rns_sent, monkeypatch, tmp_path):
    made = []
    real = tempfile.mkdtemp
    monkeypatch.setattr(tempfile, "mkdtemp", lambda **k: made.append(real(dir=tmp_path, **k)) or made[-1])
    peer.main(["rns-exchange", IN1, "LHPC", "5", str(tmp_path / "dest"), OUT1, str(tmp_path / "go")])
    assert len(made) == 1 and not os.path.exists(made[0])


@pytest.mark.parametrize("box,rc,last", [({"answers": True, "confirms": True}, 0, f"SEEN {IN1}"),
                                         ({"answers": True}, 3, "NOT-SEEN"),          # heard, but our message unconfirmed
                                         ({"confirms": True}, 3, "NOT-SEEN")])
def test_rns_exchange_succeeds_only_with_the_boxs_message_and_its_delivery_proof(monkeypatch, tmp_path, capsys, box, rc, last):
    sent, c = [], FastClock()
    _fake_rns(monkeypatch, sent, box)
    monkeypatch.setattr(peer.time, "monotonic", c.monotonic)
    monkeypatch.setattr(peer.time, "sleep", c.sleep)
    (tmp_path / "go").write_text("")
    assert peer.main(["rns-exchange", IN1, "LHPC", "5", str(tmp_path / "dest"), OUT1, str(tmp_path / "go")]) == rc
    assert " ".join(capsys.readouterr().out.splitlines()[-1].split()[:3]) == last


def test_the_shared_rnsd_instance_comes_from_the_local_file(rns_sent, tmp_path):
    CONFIGS.clear()
    (tmp_path / "go").write_text("")
    peer.main(["rns-exchange", IN1, "LHPC", "5", str(tmp_path / "dest"), OUT1, str(tmp_path / "go")])
    assert len(CONFIGS) == 1 and [line.strip() for line in CONFIGS[0].splitlines() if "instance" in line] == [
        "share_instance = Yes", "instance_name = lab-rns", "shared_instance_port = 40001", "instance_control_port = 40002"]


@pytest.mark.parametrize("fields,why", [
    ("port=40001 control=40002", "no valid instance"), ("instance=a/b port=40001 control=40002", "no valid instance"),
    ("instance=lab-rns control=40002", "no valid port"), ("instance=lab-rns port=0 control=40002", "no valid port"),
    ("instance=lab-rns port=70000 control=40002", "no valid port"), ("instance=lab-rns port=4x control=40002", "no valid port"),
    ("instance=lab-rns port=40001", "no valid control"), ("instance=lab-rns port=40001 control=40001", "are the same"),
    ("instance=lab-rns port=04001 control=4001", "are the same"),
])
def test_a_missing_or_malformed_rns_instance_is_refused_before_anything_opens(rns_sent, monkeypatch, tmp_path, fields, why):
    made = []
    monkeypatch.setattr(tempfile, "mkdtemp", lambda **k: made.append("mkdtemp") or str(tmp_path / "scratch"))
    monkeypatch.setenv("PEER_rnode", f"rnode usb-Example_RNode-if00-port0 hub1 {fields}")
    with pytest.raises(RuntimeError, match=why):
        peer.main(["rns-exchange", IN1, "LHPC", "5", str(tmp_path / "dest"), OUT1, str(tmp_path / "go")])
    assert made == [] and rns_sent == []


def test_rns_exchange_needs_the_go_file_argument(capsys):
    assert peer.main(["rns-exchange", IN1, "LHPC", "5", "/x/dest", OUT1]) == 2


def test_rns_exchange_message_sent_once_no_retries(rns_sent, tmp_path):
    # the opportunistic message goes with MAX_DELIVERY_ATTEMPTS 0 = exactly one send (no retry)
    (tmp_path / "go").write_text("")
    peer.main(["rns-exchange", IN1, "LHPC", "5", str(tmp_path / "dest"), OUT1, str(tmp_path / "go")])
    assert [a for _m, a in rns_sent] == [0]


SPAWN = {"subprocess", "pty", "pexpect", "sh"}                              # modules that start a process
SPAWN_CALLS = ("system", "popen", "exec", "spawn", "posix_spawn", "fork")   # os.<these>, and create_subprocess_* anywhere


def spawns(src):
    """What in a Python source starts a process: an import of a SPAWN module, a spawn function of os (or posix) by its
    module, by an alias of it or imported by name, or a create_subprocess_* call."""
    tree, out = ast.parse(src), []
    osnames = {"os", "posix"}
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            out += [a.name for a in n.names if a.name.split(".")[0] in SPAWN]
            osnames |= {a.asname for a in n.names if a.name in ("os", "posix") and a.asname}
        elif isinstance(n, ast.ImportFrom) and n.module:
            if n.module.split(".")[0] in SPAWN:
                out.append(n.module)
            elif n.module in ("os", "posix"):
                out += [a.name for a in n.names if a.name.startswith(SPAWN_CALLS) or a.name == "*"]
    for n in ast.walk(tree):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute):
            f = n.func
            if f.attr.startswith("create_subprocess") or (isinstance(f.value, ast.Name) and f.value.id in osnames
                                                           and f.attr.startswith(SPAWN_CALLS)):
                out.append(f.attr)
    return out


@pytest.mark.parametrize("src", [
    "import subprocess", "import subprocess as sp", "from subprocess import run", "import pty",
    "import os\nos.system('x')", "import os as o\no.popen('x')", "from os import system\nsystem('x')",
    "from os import system as s\ns('x')", "from os import execv", "from os import *", "import posix\nposix.fork()",
    "import asyncio\nasyncio.create_subprocess_exec('x')"])
def test_the_spawn_scan_finds_each_form(src):
    assert spawns(src)


def test_the_spawn_scan_passes_a_module_loader():
    assert spawns("import importlib.util\nspec.loader.exec_module(m)\nimport os\nos.environ.get('x')") == []


@pytest.mark.parametrize("name", ["peer.py", "fake_peer.py"])
def test_the_peer_side_starts_no_process(name):
    # The peer driver talks to its radio in-process: no module or call that starts a process, so no PEER_* field or
    # other lab value ever enters a shell string (a field is only an argument or a device name).
    assert spawns((pathlib.Path(PEER).parent / name).read_text()) == []
