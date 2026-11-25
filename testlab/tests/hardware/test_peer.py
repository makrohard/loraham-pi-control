"""testlab/hardware/peer.py, the RF proof's peer driver, against fake devices on a fake clock: the MeshCom power read (two
`--lora` queries, each window one echo and one answer, both agreeing), every power SET exiting non-zero on a readback mismatch,
the devices named by the lab's local file, and rns-exchange's second send waiting for the runner's go file.
PEER=<path> picks the module under test (a red-before runs these against an older driver)."""
import ast
import asyncio
import hashlib
import importlib.util
import os
import pathlib
import re
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


TRANSPORT_ID = b"T" * 64                                         # the shared rnsd's transport identity (its private key)


@pytest.fixture(autouse=True)
def rnsd_config(monkeypatch, tmp_path):
    """The shared rnsd's config directory the runner exports (RFPROOF_RNS_CONFIG): no rpc_key, a transport identity."""
    d = tmp_path / "rnsd"
    (d / "storage").mkdir(parents=True)
    (d / "config").write_text("[reticulum]\n  share_instance = Yes\n")
    (d / "storage" / "transport_identity").write_bytes(TRANSPORT_ID)
    monkeypatch.setenv("RFPROOF_RNS_CONFIG", str(d))
    return d


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
RX_LABEL = {"meshcom-read": b"RX-LoRa2", "aprs-read": b"[LoRa Rx]"}


@pytest.mark.parametrize("act,line", [(a, x) for a, xs in NOT_RX.items() for x in xs])
def test_a_line_that_is_no_received_frame_is_not_seen(clock, monkeypatch, capsys, act, line):
    port = Port(clock, pending=line)
    monkeypatch.setitem(sys.modules, "serial", FakeSerialModule(port))
    assert peer.main([act, N1, "5"]) == peer.NOT_SEEN and capsys.readouterr().out.strip() == "NOT-SEEN"


@pytest.mark.parametrize("act", sorted(IS_RX))
def test_a_received_frame_with_the_nonce_in_its_text_is_seen(clock, monkeypatch, capsys, act):
    line = IS_RX[act].replace(RX_LABEL[act], RX_LABEL[act] + b"\x1b[0m")
    assert line != IS_RX[act]
    port = Port(clock, pending=b"\x1b[32m" + line)              # colour codes before and inside the frame change nothing
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


@pytest.mark.parametrize("sig,raised", [("SIGINT", KeyboardInterrupt), ("SIGTERM", SystemExit)])
def test_a_signal_during_the_setinfo_off_is_held_until_it_is_sent_then_ends_the_read(clock, monkeypatch, sig, raised):
    # B6: the off is written whole with SIGINT/SIGTERM held off; G2C-SR N1: one that arrives meanwhile is raised after it,
    # never dropped (red-before 3a4a0af3: ignored, so the read ended SEEN as if no signal had come)
    import signal
    s, before = getattr(signal, sig), (signal.getsignal(signal.SIGINT), signal.getsignal(signal.SIGTERM))
    port = Port(clock, pending=rx_meshcom(N1))
    write = port.write

    def signalling(b):
        if b == b"--setinfo off\r\n":
            signal.raise_signal(s)                                 # delivered here, before the off is written
        write(b)
    port.write = signalling
    monkeypatch.setitem(sys.modules, "serial", FakeSerialModule(port))
    with pytest.raises(raised) as e:
        peer.main(["meshcom-read", N1, "5"])
    assert port.written == [b"--setinfo on\r\n", b"--setinfo off\r\n"]
    assert raised is KeyboardInterrupt or e.value.code == 128 + signal.SIGTERM
    assert (signal.getsignal(signal.SIGINT), signal.getsignal(signal.SIGTERM)) == before


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


@pytest.mark.parametrize("sig", ["SIGINT", "SIGTERM"])   # G2-HWKITC-E N1: Ctrl-C pinned as well as SIGTERM
def test_a_signal_while_the_off_fails_leaves_the_failed_restore_as_the_error(clock, monkeypatch, sig):
    # G2-HWKITC-D N2: the deferred signal no longer replaces the error that names the device state; it is named in it
    # (red-before 3ea308da: the SIGTERM raised in the finally turned the RuntimeError into a bare SystemExit)
    import signal
    port = Port(clock, pending=rx_meshcom(N1))
    write = port.write

    def failing(b):
        if b == b"--setinfo off\r\n":
            signal.raise_signal(getattr(signal, sig))
            raise OSError("the port is gone")
        write(b)
    port.write = failing
    monkeypatch.setitem(sys.modules, "serial", FakeSerialModule(port))
    with pytest.raises(RuntimeError, match=rf"may keep the changed mode; {sig} came meanwhile"):
        peer.main(["meshcom-read", N1, "5"])


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
ANNOUNCES = []                                                   # per PC announce: was the box's announce heard before it


class _ConfigObj(dict):
    """RNS.vendor.configobj's ConfigObj, as far as the driver reads it: `[section]` and `key = value` lines."""
    def __init__(self, path):
        super().__init__()
        sec = None
        for raw in (pathlib.Path(path).read_text().splitlines() if os.path.exists(path) else []):
            line = raw.strip()
            if line.startswith("[") and line.endswith("]"):
                sec = self.setdefault(line[1:-1], {})
            elif "=" in line and sec is not None:
                k, v = line.split("=", 1)
                sec[k.strip()] = v.strip()


# rns-exchange's second TX (the LXMF message) waits for the runner's go file; no go file = no handle_outbound
def _fake_rns(monkeypatch, sent, box=None):
    # `box` = {"answers": True} makes the box send its LXMF message with IN1, {"confirms": True} its delivery proof
    rns = types.ModuleType("RNS")
    lxmf = types.ModuleType("LXMF")
    rns.LOG_ERROR = 0
    rns.vendor = types.ModuleType("RNS.vendor")
    rns.vendor.configobj = types.ModuleType("RNS.vendor.configobj")
    rns.vendor.configobj.ConfigObj = _ConfigObj
    ANNOUNCES.clear()
    heard = []

    class Ret:
        is_connected_to_shared_instance = True

        def __init__(self, configdir=None, loglevel=None):
            CONFIGS.append(pathlib.Path(configdir, "config").read_text())

        def get_path_table(self, max_hops=None):                 # the shared rnsd's path table (its RPC)
            return (box or {}).get("paths", [])
    rns.Reticulum = Ret
    handlers = []
    if box is not None:
        box["handlers"] = handlers                               # a test's clock delivers the box's later frames through them

    class Transport:
        @staticmethod
        def register_announce_handler(h):
            handlers.append(h)

        @staticmethod
        def request_path(h):                                     # answered by the shared rnsd: a path response
            (box or {}).setdefault("asked", []).append(h)
            for x in handlers:
                if getattr(x, "receive_path_responses", False):
                    x.received_announce(h, None, b"app")
    rns.Transport = Transport

    class Ident:
        @staticmethod
        def recall(h):
            return "ident"

        @staticmethod
        def from_file(path):                                     # RNS: None when the data is not a valid identity
            data = pathlib.Path(path).read_bytes() if os.path.isfile(path) else b""
            return types.SimpleNamespace(get_private_key=lambda: data) if len(data) == 64 else None

        @staticmethod
        def full_hash(data):                                     # RNS: SHA-256
            return hashlib.sha256(data).digest()

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
            ANNOUNCES.append(bool(heard))
            if "clock" in (box or {}):
                box.setdefault("at", []).append(box["clock"].t)  # when the PC announced, on the test's clock
                box["clock"].t += box.get("announce_takes", 0.0) if heard else 0.0   # the second announce's own time
            for x in handlers if not (box or {}).get("silent") and not heard else []:   # the box's announce: at once, once
                for h in (box or {}).get("others", []):           # another node's announce, before the box's
                    x.received_announce(h, None, b"app")
                x.received_announce(BOXH, (box or {}).get("ident"), b"app")
                heard.append(1)

        MAX_DELIVERY_ATTEMPTS = 5                                # LXMF 1.1.1's value

        def handle_outbound(self, m):
            sent.append((m, lxmf.LXMRouter.MAX_DELIVERY_ATTEMPTS))
            if "clock" in (box or {}):
                box.setdefault("sent_at", []).append(box["clock"].t)   # when the message went, on the test's clock
            for x in handlers if (box or {}).get("again") else []:   # another node, then the box once more (row 12: the call)
                x.received_announce(bytes(16), None, b"app")
                x.received_announce(BOXH, None, b"app")
            if (box or {}).get("confirms"):
                m.proof(m)
            if (box or {}).get("answers"):
                self.incoming(types.SimpleNamespace(content=IN1.encode()))
    lxmf.LXMRouter = Router
    lxmf.display_name_from_app_data = lambda d: "Anonymous Peer"

    class Msg:
        OPPORTUNISTIC = 1

        def __init__(self, *a, **k):
            pass

        def register_delivery_callback(self, cb):
            self.proof = cb
    lxmf.LXMessage = Msg
    monkeypatch.setitem(sys.modules, "RNS", rns)
    monkeypatch.setitem(sys.modules, "RNS.vendor", rns.vendor)
    monkeypatch.setitem(sys.modules, "RNS.vendor.configobj", rns.vendor.configobj)
    monkeypatch.setitem(sys.modules, "LXMF", lxmf)


BOXHEX = "c0ffee00d15ea5e0b0a710ad5eed1e55"            # a made-up MeshChat LXMF address (never a real device's)
BOXH = bytes.fromhex(BOXHEX)


class FastClock:
    t = 0.0

    def monotonic(self):
        return self.t

    def sleep(self, s):
        self.t += s


class EventClock(FastClock):
    """FastClock that delivers the box's later frames at their times: events = [(t, destination hash | go file), ...]."""

    def __init__(self, box, events):
        self.t, self.box, self.events = 0.0, box, sorted(events)

    def sleep(self, s):
        if s < 0:
            raise ValueError("sleep length must be non-negative")   # as time.sleep
        self.t += s
        while self.events and self.events[0][0] <= self.t:
            _, dest = self.events.pop(0)
            if isinstance(dest, pathlib.Path):                  # the runner's release file appears now
                dest.write_text("")
                continue
            for x in list(self.box["handlers"]):
                x.received_announce(dest, None if dest == bytes(16) else BOXID, b"app")


BOXID = types.SimpleNamespace(hash=b"I" * 16)           # the box's identity: its LXMF and its audio-call destination share it
BOXCALL = b"C" * 16                                     # the box's audio-call destination (MeshChat's second announce frame)


def quiet_run(monkeypatch, capsys, tmp_path, announces, events, secs="30", go_at=None, announce_takes=0.0):
    """One exchange on an EventClock, the go file there from the start or at `go_at`: (rc, output lines, the PC's announce
    times, the messages sent)."""
    sent, box = [], {"answers": True, "confirms": True, "ident": BOXID, "announce_takes": announce_takes}
    _fake_rns(monkeypatch, sent, box)
    c = box["clock"] = EventClock(box, events + ([(go_at, tmp_path / "go")] if go_at is not None else []))
    monkeypatch.setattr(peer.time, "monotonic", c.monotonic)
    monkeypatch.setattr(peer.time, "sleep", c.sleep)
    if go_at is None:
        (tmp_path / "go").write_text("")
    rc = peer.main(["rns-exchange", IN1, BOXHEX, secs, str(tmp_path / "dest"), OUT1, str(tmp_path / "go"), announces])
    quiet_run.clock = c
    return rc, capsys.readouterr().out.splitlines(), box["at"], sent


@pytest.fixture
def rns_sent(monkeypatch):
    sent, c = [], FastClock()
    _fake_rns(monkeypatch, sent)
    monkeypatch.setattr(peer.time, "monotonic", c.monotonic)
    monkeypatch.setattr(peer.time, "sleep", c.sleep)
    return sent


def test_rns_exchange_no_go_file_no_second_tx(rns_sent, tmp_path, capsys):
    assert peer.main(["rns-exchange", IN1, BOXHEX, "10", str(tmp_path / "dest"), OUT1, str(tmp_path / "go"), "1"]) == peer.NOT_SEEN
    assert rns_sent == [] and "TX2-NOT-GATED" in capsys.readouterr().out   # the public path: the runner's own call form


def test_rns_exchange_go_file_sends_once(rns_sent, tmp_path, capsys):
    (tmp_path / "go").write_text("")
    peer.rns_exchange(IN1, BOXHEX, 10, str(tmp_path / "dest"), OUT1, str(tmp_path / "go"), 1)
    assert len(rns_sent) == 1 and "TX2-NOT-GATED" not in capsys.readouterr().out


def test_an_rnode_the_local_file_does_not_name_is_refused_before_anything_opens(rns_sent, monkeypatch, tmp_path):
    made = []
    monkeypatch.setattr(tempfile, "mkdtemp", lambda **k: made.append("mkdtemp") or str(tmp_path / "scratch"))
    monkeypatch.setattr(sys.modules["RNS"], "Reticulum", lambda **k: made.append("reticulum"))
    monkeypatch.delenv("PEER_rnode")
    with pytest.raises(RuntimeError, match="PEER_rnode is not set"):
        peer.main(["rns-exchange", IN1, BOXHEX, "10", str(tmp_path / "dest"), OUT1, str(tmp_path / "go"), "1"])
    assert made == [] and rns_sent == []


def test_rns_exchange_removes_its_scratch_config(rns_sent, monkeypatch, tmp_path):
    made = []
    real = tempfile.mkdtemp
    monkeypatch.setattr(tempfile, "mkdtemp", lambda **k: made.append(real(dir=tmp_path, **k)) or made[-1])
    peer.main(["rns-exchange", IN1, BOXHEX, "10", str(tmp_path / "dest"), OUT1, str(tmp_path / "go"), "1"])
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
    assert peer.main(["rns-exchange", IN1, BOXHEX, "10", str(tmp_path / "dest"), OUT1, str(tmp_path / "go"), "1"]) == rc
    assert " ".join(capsys.readouterr().out.splitlines()[-1].split()[:3]) == last


@pytest.mark.parametrize("go, want", [(True, [False, True]), (False, [False])])
def test_rns_exchange_announces_again_after_the_boxs_announce_and_the_go_file(rns_sent, tmp_path, capsys, go, want):
    # red-before (a21's kit run, row 5 box -> peer NO-PATH): the PC announced once, at launch, before the box's node ran
    # (MeshChat's start brings it up), so the node never had a path to the PC. Now once more after the box's announce,
    # gated like the message by the runner's go file (its TX checks passed again).
    if go:
        (tmp_path / "go").write_text("")
    peer.main(["rns-exchange", IN1, BOXHEX, "10", str(tmp_path / "dest"), OUT1, str(tmp_path / "go"), "1"])
    assert want == ANNOUNCES and ("PC-ANNOUNCE again" in capsys.readouterr().out) == go


@pytest.mark.parametrize("rnsd, want", [
    ("", hashlib.sha256(TRANSPORT_ID).hexdigest()),                              # derived from its transport identity
    ("  rpc_key = 00ff10ab\n", "00ff10ab"),                                      # its config's own key
    ("  rpc_key = 0zz\n", hashlib.sha256(TRANSPORT_ID).hexdigest()),             # invalid: rnsd falls back to the derived
])
def test_the_client_config_carries_the_shared_rnsds_rpc_key(rns_sent, rnsd_config, tmp_path, capsys, rnsd, want):
    # red-before (a21's kit run: "digest rejected"): without rpc_key the client derived the key from its own new
    # identity (RNS 1.5.4 Reticulum.py:360-361), so every RPC to the shared rnsd (path table, first-hop timeout) was refused
    (rnsd_config / "config").write_text("[reticulum]\n  share_instance = Yes\n" + rnsd)
    CONFIGS.clear()
    (tmp_path / "go").write_text("")
    peer.main(["rns-exchange", IN1, BOXHEX, "10", str(tmp_path / "dest"), OUT1, str(tmp_path / "go"), "1"])
    assert len(CONFIGS) == 1 and [ln.strip() for ln in CONFIGS[0].splitlines() if "rpc_key" in ln] == [f"rpc_key = {want}"]
    out = capsys.readouterr()
    assert want not in out.out + out.err                                          # the key is never printed


def test_an_explicit_rpc_key_needs_no_transport_identity(rns_sent, rnsd_config, tmp_path):
    # the identity is read only to derive the default key: a valid rpc_key in the rnsd's config is taken as it is
    (rnsd_config / "config").write_text("[reticulum]\n  rpc_key = 00ff10ab\n")
    (rnsd_config / "storage" / "transport_identity").unlink()
    CONFIGS.clear()
    (tmp_path / "go").write_text("")
    peer.main(["rns-exchange", IN1, BOXHEX, "10", str(tmp_path / "dest"), OUT1, str(tmp_path / "go"), "1"])
    assert len(CONFIGS) == 1 and "  rpc_key = 00ff10ab\n" in CONFIGS[0]


NODIR = "RFPROOF_RNS_CONFIG names no directory"


@pytest.mark.parametrize("break_it, why", [("unset", NODIR), ("no-dir", NODIR),
                                           ("no-identity", "transport identity"), ("bad-identity", "transport identity"),
                                           ("bad-key-no-identity", "transport identity")])
def test_no_shared_rnsd_key_is_refused_before_anything_opens(rns_sent, rnsd_config, monkeypatch, tmp_path, break_it, why):
    made = []
    monkeypatch.setattr(tempfile, "mkdtemp", lambda **k: made.append("mkdtemp") or str(tmp_path / "scratch"))
    monkeypatch.setattr(sys.modules["RNS"], "Reticulum", lambda **k: made.append("reticulum"))
    if break_it == "unset":
        monkeypatch.delenv("RFPROOF_RNS_CONFIG")
    elif break_it == "no-dir":
        monkeypatch.setenv("RFPROOF_RNS_CONFIG", str(tmp_path / "absent"))
    elif break_it == "no-identity":
        (rnsd_config / "storage" / "transport_identity").unlink()
    elif break_it == "bad-key-no-identity":                     # an invalid rpc_key falls back to the derived key
        (rnsd_config / "config").write_text("[reticulum]\n  rpc_key = 0zz\n")
        (rnsd_config / "storage" / "transport_identity").unlink()
    else:
        (rnsd_config / "storage" / "transport_identity").write_bytes(b"short")
    with pytest.raises(RuntimeError, match=why) as e:
        peer.main(["rns-exchange", IN1, BOXHEX, "10", str(tmp_path / "dest"), OUT1, str(tmp_path / "go"), "1"])
    assert made == [] and rns_sent == [] and hashlib.sha256(TRANSPORT_ID).hexdigest() not in str(e.value)


def test_the_shared_rnsd_instance_comes_from_the_local_file(rns_sent, tmp_path):
    CONFIGS.clear()
    (tmp_path / "go").write_text("")
    peer.main(["rns-exchange", IN1, BOXHEX, "10", str(tmp_path / "dest"), OUT1, str(tmp_path / "go"), "1"])
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
        peer.main(["rns-exchange", IN1, BOXHEX, "10", str(tmp_path / "dest"), OUT1, str(tmp_path / "go"), "1"])
    assert made == [] and rns_sent == []


def test_rns_exchange_needs_the_go_file_argument(capsys):
    assert peer.main(["rns-exchange", IN1, BOXHEX, "5", "/x/dest", OUT1]) == 2
    assert peer.main(["rns-exchange", IN1, BOXHEX, "5", "/x/dest", OUT1, "/x/go", "cached"]) == 2   # `known` only


@pytest.mark.parametrize("known, cached, rc, asked", [
    (True, True, 0, [BOXH]),               # the shared rnsd holds the box's path: asked, answered locally, no announce needed
    (True, False, peer.NOT_SEEN, []),      # not cached: no path request at all (it would go out on the RNode)
    (False, True, peer.NOT_SEEN, []),      # without `known` the box's live announce is awaited, never a request
])
def test_rns_exchange_known_takes_the_boxs_path_from_the_shared_rnsd_only_when_cached(monkeypatch, tmp_path, capsys, known,
                                                                                    cached, rc, asked):
    # red-before (da24e83c): `known` is a usage error (rc 2): the exchange after the member's stop had no box announce to hear
    sent, c = [], FastClock()
    box = {"answers": True, "confirms": True, "silent": True, "paths": [{"hash": BOXH if cached else b"\0" * 16}]}
    _fake_rns(monkeypatch, sent, box)
    monkeypatch.setattr(peer.time, "monotonic", c.monotonic)
    monkeypatch.setattr(peer.time, "sleep", c.sleep)
    (tmp_path / "go").write_text("")
    argv = ["rns-exchange", IN1, BOXHEX, "10", str(tmp_path / "dest"), OUT1, str(tmp_path / "go")] + (["0", "known"] if known
                                                                                                   else ["1"])
    assert peer.main(argv) == rc
    out = capsys.readouterr().out
    assert box.get("asked", []) == asked and len(sent) == (1 if rc == 0 else 0)
    assert ("BOX-PATH not cached" in out) == (known and not cached)


def test_rns_exchange_message_sent_once_no_retries(rns_sent, tmp_path):
    # the opportunistic message goes with MAX_DELIVERY_ATTEMPTS 0 = exactly one send (no retry)
    (tmp_path / "go").write_text("")
    peer.main(["rns-exchange", IN1, BOXHEX, "10", str(tmp_path / "dest"), OUT1, str(tmp_path / "go"), "1"])
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


def test_rns_exchange_tells_the_box_by_its_lxmf_address_not_its_name(monkeypatch, tmp_path, capsys):
    # red-before (81c0f431, GO-F row 5): the exchange waited for the display name "LHPC"; the box's MeshChat announces its
    # default "Anonymous Peer" (meshchat.py:3402), so its announce was never taken. Another node's announce stays ignored.
    sent, c = [], FastClock()
    _fake_rns(monkeypatch, sent, {"answers": True, "confirms": True, "others": [bytes(16)]})
    monkeypatch.setattr(peer.time, "monotonic", c.monotonic)
    monkeypatch.setattr(peer.time, "sleep", c.sleep)
    (tmp_path / "go").write_text("")
    assert peer.main(["rns-exchange", IN1, BOXHEX, "10", str(tmp_path / "dest"), OUT1, str(tmp_path / "go"), "1"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert "BOX-NAME Anonymous Peer" in out and any(ln.startswith("BOX-ANNOUNCE heard ") for ln in out)


def test_rns_exchange_records_each_later_box_announce_by_the_pcs_second(monkeypatch, tmp_path, capsys):
    # red-before (6e3b790b): only the box's first announce was kept; row 12 must also see its kit's call on air (a later one).
    # Another node's announce and then the box's call come 1 s in; the PC's second announce follows the box's last one
    rc, out, _, _ = quiet_run(monkeypatch, capsys, tmp_path, "2", [(1.0, bytes(16)), (1.0, BOXH)])
    assert rc == 0, out
    stamp = r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ"
    names = [ln for ln in out if ln.startswith("BOX-NAME ")]
    heard = [ln for ln in out if ln.startswith("BOX-ANNOUNCE heard")]
    again = [i for i, ln in enumerate(out) if ln.startswith("BOX-ANNOUNCE again")]
    assert names == ["BOX-NAME Anonymous Peer"], out
    assert len(heard) == 1 and re.fullmatch("BOX-ANNOUNCE heard " + stamp, heard[0]), out
    assert len(again) == 1 and re.fullmatch("BOX-ANNOUNCE again " + stamp, out[again[0]]), out    # the other node's: none
    pc = [i for i, ln in enumerate(out) if re.fullmatch(r"PC-ANNOUNCE again \d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z", ln)]
    assert len(pc) == 1 and pc[0] > again[0], out                              # after the box's last announce, with ms


def test_the_pcs_second_announce_waits_until_the_box_is_quiet_restarted_by_its_later_frame(monkeypatch, tmp_path, capsys):
    # red-before (4078ef72, a21's kit run 00:01Z: row 12 box -> peer NO-PATH): the PC announced again at once after the box's
    # announce and the go file, inside the box's second frame (its audio call, same identity): half duplex, lost. Now
    # QUIET_S after the box's last frame of any destination of its identity: here the call's frame 2 s in restarts it
    rc, out, at, sent = quiet_run(monkeypatch, capsys, tmp_path, "1", [(2.0, BOXCALL)])
    assert rc == 0 and len(at) == 2 and sent, out
    assert 2.0 + peer.QUIET_S <= at[1] < 2.0 + peer.QUIET_S + 1, at                       # within one poll of the quiet's end


def test_the_pcs_second_announce_waits_for_the_expected_box_announces(monkeypatch, tmp_path, capsys):
    # v3 B2: the go file can come before the PC hears the box's call (rt_announce returns on the request): with 2 expected,
    # the quiet starts only at the second, though the first one's quiet ended long before it
    rc, out, at, sent = quiet_run(monkeypatch, capsys, tmp_path, "2", [(5.0, BOXH)])
    assert rc == 0 and len(at) == 2 and sent, out
    assert 5.0 + peer.QUIET_S <= at[1] < 5.0 + peer.QUIET_S + 1, at


def test_the_message_waits_until_the_pcs_second_announce_has_aired(monkeypatch, tmp_path, capsys):
    # red-before (fa6a68bc, a21's kit run 00:58Z: row 12 NOT-DELIVERED): the message went right after the announce call; the
    # shared rnsd forwards it at once but re-sends the announce only at its next check, so the announce aired after the
    # message, into the box's delivery proof. Now the message waits ANNOUNCE_AIR_S after the announce
    sent, box = [], {"answers": True, "confirms": True, "ident": BOXID}
    _fake_rns(monkeypatch, sent, box)
    c = box["clock"] = EventClock(box, [])
    monkeypatch.setattr(peer.time, "monotonic", c.monotonic)
    monkeypatch.setattr(peer.time, "sleep", c.sleep)
    (tmp_path / "go").write_text("")
    assert peer.main(["rns-exchange", IN1, BOXHEX, "30", str(tmp_path / "dest"), OUT1, str(tmp_path / "go"), "1"]) == 0
    assert len(box["at"]) == 2 and box["sent_at"][0] >= box["at"][1] + peer.ANNOUNCE_AIR_S, (box["at"], box["sent_at"])
    # the wait itself, not only its constant: >= the rnsd's 1.0 s announce check + its 0.25 s job + toa(255 B) 0.723 s
    assert peer.ANNOUNCE_AIR_S >= 2.0 and box["sent_at"][0] - box["at"][1] >= 2.0, (box["at"], box["sent_at"])


@pytest.mark.parametrize("secs", ["5.0", "5.9"])
def test_a_deadline_inside_the_announces_airtime_sends_no_message(monkeypatch, tmp_path, capsys, secs):
    # red-before (4ab4de65, G2 280 B1): the airtime wait ran its full 3 s past the deadline and the message still went. Now
    # it waits at most until the deadline (here the quiet ends at 3 s, 2 s / 2.9 s before it) and then sends nothing more
    rc, out, at, sent = quiet_run(monkeypatch, capsys, tmp_path, "1", [], secs=secs)
    assert rc == peer.NOT_SEEN and len(at) == 2 and sent == [], out
    assert quiet_run.clock.t <= float(secs) and f"NOT-SENT {OUT1}: the deadline passed while the PC's announce aired" in out, out


def test_an_announce_that_ends_past_the_deadline_sends_no_message_and_raises_nothing(monkeypatch, tmp_path, capsys):
    # a33 C1 on 9ab90d27: the announce itself can take the clock past the deadline; the airtime wait was then negative and
    # time.sleep raised ValueError. Now it waits 0 s and sends no message
    rc, out, at, sent = quiet_run(monkeypatch, capsys, tmp_path, "1", [], secs="3.2", announce_takes=0.5)
    assert rc == peer.NOT_SEEN and len(at) == 2 and sent == [], out
    assert f"NOT-SENT {OUT1}: the deadline passed while the PC's announce aired" in out, out


def test_a_quiet_reached_only_at_the_deadline_sends_no_announce(monkeypatch, tmp_path, capsys):
    # red-before (4ab4de65, G2 280 B1): the quiet came at the deadline (3 s) and the PC still announced
    rc, out, at, sent = quiet_run(monkeypatch, capsys, tmp_path, "1", [], secs="3")
    assert rc == peer.NOT_SEEN and len(at) == 1 and sent == [], out
    assert "PC-ANNOUNCE NOT SENT: the deadline passed" in out, out


def test_the_quiet_also_counts_from_the_go_file(monkeypatch, tmp_path, capsys):
    # the go file follows the box's last announce of the phase, even a frame of it the PC did not hear: QUIET_S after it too
    rc, out, at, sent = quiet_run(monkeypatch, capsys, tmp_path, "1", [], go_at=10.0)
    assert rc == 0 and len(at) == 2 and sent, out
    assert 10.0 + peer.QUIET_S <= at[1] < 10.0 + peer.QUIET_S + 1, at


def test_a_box_announce_not_heard_holds_the_pcs_announce_and_its_message(monkeypatch, tmp_path, capsys):
    # G1 279 B2: the release file alone (the runner's call returned) never lets the PC announce: the call's announce, not
    # heard, keeps it back until the deadline, then nothing more is sent
    rc, out, at, sent = quiet_run(monkeypatch, capsys, tmp_path, "2", [], secs="20")
    assert rc == peer.NOT_SEEN and len(at) == 1 and sent == [], out
    assert "PC-ANNOUNCE NOT SENT: 1 of 2 box announces heard" in out, out


def test_rns_exchange_ignores_an_announce_of_another_address(monkeypatch, tmp_path, capsys):
    sent, c = [], FastClock()
    _fake_rns(monkeypatch, sent, {"answers": True, "confirms": True})
    monkeypatch.setattr(peer.time, "monotonic", c.monotonic)
    monkeypatch.setattr(peer.time, "sleep", c.sleep)
    (tmp_path / "go").write_text("")
    assert peer.main(["rns-exchange", IN1, "ff" * 16, "10", str(tmp_path / "dest"), OUT1, str(tmp_path / "go"), "1"]) == peer.NOT_SEEN
    assert "BOX-ANNOUNCE not heard" in capsys.readouterr().out and sent == []


@pytest.mark.parametrize("dest", ["LHPC", "C0FFEE00D15EA5E0B0A710AD5EED1E55", "c0ffee00", "c0ffee00d15ea5e0b0a710ad5eed1e55ff"])
def test_rns_exchange_refuses_a_box_address_that_is_not_32_hex(dest, tmp_path):
    assert peer.main(["rns-exchange", IN1, dest, "10", str(tmp_path / "dest"), OUT1, str(tmp_path / "go"), "1"]) == 2
