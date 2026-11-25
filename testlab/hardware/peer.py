"""The RF proof's peer driver: one action per call on one lab peer of this PC (testlab/hardware/rf-proof.sh runs it).

The devices come from the environment the runner exports from the lab's local file (testlab/hardware/lab.local.env):
`PEER_<name>="<kind> <by-id name | BLE address> <hub> ..."` for tdeck, tracker, g2 and tdpro; a missing one is refused by name.

Exit status: 0 = the expected result (a read: `SEEN <nonce> <time>`; a send: `SENT <nonce> <time>`; a power action:
`POWER <readback>`), 3 = `NOT-SEEN` (a read that ended without the nonce: the only negative result), anything else = an error
(the runner STOPs). Output is sanitised: a received line is never printed; only the fixed fields below are.
Power: every power action re-reads the hardware value after a set and prints that readback; the runner refuses a
peer transmission unless the readback is the low value. The PIN comes on stdin only (never argv, a file or a log).
The call itself is checked before any device opens (exit 2 otherwise): a power value is 1-2 ASCII digits in THE power
domain 0..17 dBm (mt-power 1..17: Meshtastic's 0 is its maximum), the runner's one; a power SET runs only with
RFPROOF_PEER_CTX=set|restore (the runner's one setter), a send and rns-exchange only with RFPROOF_PEER_CTX=tx (inside the
runner's tx); a nonce is the runner's form (<a-z prefix> <8 hex>). The context is a convention against an accidental call
(a hand-run call can set it); the power domain is the hard bound.
A read counts only a frame the peer RECEIVED: the T-Deck's RX-LoRa2 text frame, the tracker's [LoRa Rx] APRS message, the
nonce in that frame's text; a console echo or the peer's own TX line never counts.
The runner holds an exclusive flock around every call, so two peers never act at once (they share USB hubs).

  peer.py meshcom-power [<p>]          T-Deck (MeshCom): `--lora` read (its RF-Power line); with <p>: `--txpower <p>` then the read again
  peer.py meshcom-send  <nonce>        T-Deck: `::<nonce>`
  peer.py meshcom-read  <nonce> <s>    T-Deck: `--setinfo on` for the read (prints RX-LoRa2 lines; RAM only), `--setinfo off` after,
                                       also on SIGINT/SIGTERM; an off that cannot be sent exits non-zero, naming it
  peer.py aprs-read     <nonce> <s>    CA2RXU tracker console, read-only
  peer.py mt-power      [<p>]          Station G2: lora.tx_power (with <p>: set, then a NEW connection reads it back)
  peer.py mt-send       <nonce>        Station G2: one broadcast text
  peer.py mt-read       <nonce> <s>    Station G2: a received text with the nonce
  peer.py mc-power      [<p>]          T-Deck Pro (BLE): tx power (with <p>: set, then the device query reads it back)
  peer.py mc-send       <nonce>        T-Deck Pro: one public-channel message
  peer.py mc-read       <nonce> <s>    T-Deck Pro: a channel or contact message with the nonce
  peer.py rns-exchange  <nonce-in> <box-dest> <s> <dest-file> <nonce-out> <go-file> <n> [known]  a client of the PC's shared rnsd:
                                       announce, write the PC's LXMF destination hash to <dest-file>, wait for the box's announce
                                       (its display name; `BOX-ANNOUNCE heard <time>`, each later one `BOX-ANNOUNCE again
                                       <time>`), send <nonce-out> to it and wait for its delivery proof (the box's
                                       receipt: `DELIVERED <nonce-out> <time>`), and wait for an LXMF message carrying <nonce-in>;
                                       prints SEEN on it. The message, and right before it one more announce (`PC-ANNOUNCE
                                       again <UTC ms>`: the box's node now runs; only once the box's <n> LXMF announces of
                                       this phase were heard and the box was quiet QUIET_S), go only once the runner
                                       has written <go-file> (after its TX checks passed again), and exactly once (no retry);
                                       without it: `TX2-NOT-GATED`,
                                       NOT-SEEN. The client config carries the shared rnsd's RPC key (RFPROOF_RNS_CONFIG). `known`: no box announce is awaited; the box's path comes from
                                       the shared rnsd's path table (a request it answers locally, no RF); not cached there:
                                       `BOX-PATH not cached`, NOT-SEEN, and no request is sent (it would go out on the RNode)
"""
import os
import re
import signal
import sys
import time

NOT_SEEN = 3
PCAP = 17                                  # THE power cap (dBm), the runner's PCAP: every power value lies in 0..17
CTX = "RFPROOF_PEER_CTX"                   # the runner's context of this call: set | restore (its one setter), tx (its tx)
NONCE = re.compile(r"[a-z]{2,6} [0-9a-f]{8}")   # the runner's nonce: NONCE_PREFIX, a space, 8 hex

# The actions, one table for the driver and its dry fake (fake_peer.py loads it from here): the peer each acts on and
# its kind; the kind fixes the arguments. A call that does not fit is a usage error: exit 2, before any device opens.
ACTIONS = {"meshcom-power": ("tdeck", "power"), "meshcom-send": ("tdeck", "send"), "meshcom-read": ("tdeck", "read"),
           "aprs-read": ("tracker", "read"),
           "mt-power": ("g2", "power"), "mt-send": ("g2", "send"), "mt-read": ("g2", "read"),
           "mc-power": ("tdpro", "power"), "mc-send": ("tdpro", "send"), "mc-read": ("tdpro", "read"),
           "rns-exchange": ("rnode", "exchange")}
ARGS = {"power": ("[<p>]",), "send": ("<nonce>",), "read": ("<nonce>", "<s>"),
        "exchange": ("<nonce-in>", "<box-dest>", "<s>", "<dest-file>", "<nonce-out>", "<go-file>", "<n>")}


def power_ok(act, v):
    """`v` is a power value of THE domain: 1-2 ASCII digits, 0..17 dBm (mt-power 1..17: Meshtastic's 0 is its maximum)."""
    return re.fullmatch(r"[0-9]{1,2}", v, re.ASCII) is not None and (1 if act == "mt-power" else 0) <= int(v) <= PCAP


def usage_error(a):
    """None when argv `a` is a call of the table in the runner's context (RFPROOF_PEER_CTX), else the reason (the caller
    exits 2, before any device opens): the arguments' forms, a power value in the domain, a SET only from the runner's
    setter, a transmission only from its tx."""
    if not a or a[0] not in ACTIONS:
        return f"unknown action {a[0] if a else '(none)'}"
    kind, args = ACTIONS[a[0]][1], a[1:]
    if kind == "exchange" and len(args) == 8 and args[7] == "known":     # the box's path from the shared rnsd
        args = args[:7]
    want, ctx = ARGS[kind], os.environ.get(CTX, "")
    if kind == "power":
        if len(args) > 1 or (args and not power_ok(a[0], args[0])):
            return f"{a[0]}: [<p>], <p> 1-2 digits in {1 if a[0] == 'mt-power' else 0}..{PCAP} dBm"
        if args and ctx not in ("set", "restore"):
            return f"{a[0]} <p>: a power set runs only from the runner's one setter ({CTX}=set|restore)"
        return None
    ok = len(args) == len(want) and all(
        re.fullmatch(r"\d+(\.\d+)?", v, re.ASCII) if w == "<s>" else NONCE.fullmatch(v) if w.startswith("<nonce")
        else re.fullmatch(r"[0-9a-f]{32}", v, re.ASCII) if w == "<box-dest>"
        else re.fullmatch(r"[0-9]", v, re.ASCII) if w == "<n>" else True
        for w, v in zip(want, args, strict=True))
    if not ok:
        return f"{a[0]}: {' '.join(want)} (a nonce: <a-z prefix> <8 hex>)"
    if kind in ("send", "exchange") and ctx != "tx":
        return f"{a[0]}: a transmission runs only inside the runner's tx ({CTX}=tx)"
    return None


def peer_fields(name):
    """The fields of `PEER_<name>` in the lab's local file (the runner exports it)."""
    return os.environ.get(f"PEER_{name}", "").split()


def device(name):
    """The device of peer `name` from `PEER_<name>` (its second field): a by-id name or a BLE address."""
    fields = peer_fields(name)
    if len(fields) < 2:
        raise RuntimeError(f"PEER_{name} is not set (the lab's local file names each peer)")
    return fields[1]


def rns_instance():
    """The PC's shared rnsd from PEER_rnode's fields instance=<name> port=<n> control=<n>: no defaults; a missing or malformed
    one, or port = control, is refused by name."""
    fields = dict(f.split("=", 1) for f in peer_fields("rnode")[1:] if "=" in f)
    name, ports = fields.get("instance", ""), [fields.get(k, "") for k in ("port", "control")]
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", name):
        raise RuntimeError("PEER_rnode has no valid instance=<name> (the PC's shared rnsd)")
    for key, value in zip(("port", "control"), ports, strict=True):
        if not re.fullmatch(r"[0-9]{1,5}", value) or not 1 <= int(value) <= 65535:
            raise RuntimeError(f"PEER_rnode has no valid {key}=<1..65535>")
    port, control = int(ports[0]), int(ports[1])          # compared as numbers: 04001 is 4001
    if port == control:
        raise RuntimeError("PEER_rnode: port and control are the same")
    return name, port, control


def port(name):
    return "/dev/serial/by-id/" + device(name)


def ts():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# The receive frames a read counts; anything else on the console (an echo, a TX line, a status line) is never evidence.
# MeshCom (printBuffer_aprs): "<time> RX-LoRa2 <len> <type> x<id> H.. S. T. M.. <src>><dst><type><text> HW:..", printed only
# for a frame received over LoRa, with --setinfo on (lora_functions.cpp); a text frame's type is ':'.
MESHCOM_RX = re.compile(r"\bRX-LoRa2 \d{3} : x[0-9A-Fa-f]{8} (?:\S+ ){4}[^\s>]+>[^\s:]*:(?P<text>.*) HW:")
# CA2RXU tracker (lora_utils.cpp receivePacket): its logger's "LoRa Rx" line "---> <TNC2 frame>"; an APRS message frame's
# information field is ":<addressee, 9 characters>:<text>[{<id>]". Its own sends are "LoRa Tx" lines.
APRS_RX = re.compile(r"\[LoRa Rx\]\s*---> [^\s>]+>[^:\s]*::.{9}:(?P<text>[^{]*)")
ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def rx_text(form, line):
    """The text of the received frame `line` holds in `form`, else None."""
    m = form.search(ANSI.sub("", line))
    return m.group("text") if m else None


def on_term(signum, _frame):
    raise SystemExit(128 + signum)             # a SIGTERM unwinds like a SIGINT: every finally (the console restore) runs


def restore(s, post):
    """Send `post` (the console restore) with SIGINT/SIGTERM held off (deferred: raised after it); if it cannot be sent the
    call fails naming the device state, never a clean result."""
    got, sent = [], False                              # a SIGINT/SIGTERM meanwhile is kept and raised once the restore is sent
    held = {sig: signal.signal(sig, lambda n, _f: got.append(n)) for sig in (signal.SIGINT, signal.SIGTERM)}
    try:
        for c in post:
            s.write(c)
            time.sleep(0.5)
        sent = True
    except Exception as e:                             # the failed restore stays the error; a signal meanwhile is named in it
        note = f"; {signal.Signals(got[0]).name} came meanwhile" if got else ""
        raise RuntimeError(f"the console restore {b''.join(post)!r} was not sent ({e}): the peer may keep the changed mode{note}") from e
    finally:
        for sig, h in held.items():
            signal.signal(sig, h)
        if got and sent:
            signal.raise_signal(got[0])


def serial_wait(port, nonce, secs, form, pre=(), post=()):
    """SEEN time when a frame the peer RECEIVED (`form`) carries `nonce` in its text before `secs`, else None. `pre` changes the
    console (MeshCom's --setinfo on) inside the protected region: `post` is sent on every exit, a signal's included."""
    import serial
    term = signal.signal(signal.SIGTERM, on_term)
    try:
        with serial.Serial(port, 115200, timeout=0.5) as s:
            try:
                for c in pre:
                    s.write(c)
                    time.sleep(0.5)
                end = time.monotonic() + secs
                while time.monotonic() < end:
                    text = rx_text(form, s.readline().decode("utf-8", "replace"))
                    if text is not None and nonce in text:
                        return ts()
            finally:
                if post:
                    restore(s, post)
    finally:
        signal.signal(signal.SIGTERM, term)
    return None


# The MeshCom power form: MeshCom 4.40a's `--lora` answer line "...LoRa RF-Power: <22 dBm>" (the T-Deck,
# captured 2026-11-10). Only the lines after the echo of the query count; "TXPWR N dBm" is MeshCom's BOOT line
# ("...FREQ 433.1750 MHz TXPWR 17 dBm RXBOOST off"), never part of a --lora answer: inside the answer it means the device
# restarted, and the read is refused.
POWER_FORM = re.compile(r"LoRa RF-Power:\s*<(-?\d+) dBm>")
POWER_WORD = re.compile(r"RF-Power", re.IGNORECASE)
BOOT_WORD = re.compile(r"TXPWR", re.IGNORECASE)


def meshcom_power_of(lines):
    """The one power value of this query's `--lora` answer, strictly, else a RuntimeError that names the problem (fail
    closed: the runner STOPs). The window holds exactly one echo of `--lora` and nothing before it; every RF-Power
    declaration on every line after it must be in POWER_FORM, and all of them must agree."""
    echoes = [i for i, line in enumerate(lines) if line.strip() == "--lora"]
    if not echoes:
        raise RuntimeError("no echo of --lora: no answer of this query in the T-Deck's output")
    if len(echoes) > 1:
        raise RuntimeError(f"{len(echoes)} echoes of --lora in one answer window: an earlier query's answer arrived late")
    echo = echoes[0]
    if any(line.strip() for line in lines[:echo]):
        raise RuntimeError("output before the echo of --lora: not this query's answer alone")
    vals = []
    for line in lines[echo + 1:]:
        if BOOT_WORD.search(line):
            raise RuntimeError("a boot line (TXPWR) inside the --lora answer: the device restarted")
        words, found = len(POWER_WORD.findall(line)), POWER_FORM.findall(line)
        if words != len(found):
            raise RuntimeError("a power declaration of the T-Deck's --lora answer is in no known form")
        vals += [int(v) for v in found]
    if not vals:
        raise RuntimeError("no power line (LoRa RF-Power) in the T-Deck's --lora answer")
    if len(set(vals)) != 1:
        raise RuntimeError(f"the T-Deck's --lora answer names {len(set(vals))} different powers")
    return vals[0]


QUIET_S, DRAIN_MAX_S, ANSWER_S = 1.0, 10.0, 5.0     # the quiet before the query, its bound, the answer's window


def read_until(s, end):
    """The lines that arrive before `end`, each readline with the time left as its timeout: a line that would
    complete after `end` is not waited for."""
    lines = []
    while True:
        left = end - time.monotonic()
        if left <= 0:
            return lines
        s.timeout = min(0.5, left)
        lines.append(s.readline().decode("utf-8", "replace"))


def drain_quiet(s):
    """Drop what is pending and wait until the console shows QUIET_S of silence, else refuse by name."""
    s.reset_input_buffer()
    start = quiet_from = time.monotonic()
    while True:
        now = time.monotonic()
        if now - quiet_from >= QUIET_S:
            return
        if now - start >= DRAIN_MAX_S:
            raise RuntimeError(f"the T-Deck's console did not go quiet within {DRAIN_MAX_S:g} s before the query")
        s.timeout = 0.25
        if s.readline():
            quiet_from = time.monotonic()


def meshcom_query(s):
    """One `--lora` query and its window (one echo, nothing before it: meshcom_power_of)."""
    s.write(b"--lora\r\n")
    return meshcom_power_of(read_until(s, time.monotonic() + ANSWER_S))


def meshcom_txpwr(s):
    """After a quiet console, two `--lora` queries in sequence, each its own window with exactly one echo
    and one answer; both must name the same power, else refused by name. An earlier query's answer arriving alone fills at
    most one window: the other window has no echo, or names another power."""
    drain_quiet(s)
    a = meshcom_query(s)
    drain_quiet(s)
    b = meshcom_query(s)
    if a != b:
        raise RuntimeError(f"two --lora answers disagree: {a} vs {b}")
    return a


def readback_ok(p, got):
    """Print the readback; a SET whose readback is not the requested value exits non-zero (the runner and the
    undo then refuse to count it as done)."""
    print(f"POWER {got}")
    if p is not None and str(got) != str(int(p)):
        raise RuntimeError(f"the readback {got} is not the requested {int(p)}")


def meshcom_sentinel(o, p):
    """The sentinel power of a SET to `p` from `o`: the lowest value of 2..17 dBm that is neither `o` nor `p` (a pre-state `o`
    above 17 only by the read: the runner never sets one there)."""
    return next(v for v in range(2, 18) if v not in (o, p))


def meshcom_set_read(s, v):
    """SET `v` and read it back by two agreeing windows; the readback must be `v` (exits non-zero otherwise)."""
    s.write(f"--txpower {int(v)}\r\n".encode())
    time.sleep(1.5)
    got = meshcom_txpwr(s)
    if got != v:
        raise RuntimeError(f"the readback {got} is not the requested {v}")
    return got


def meshcom_power(p):
    """No value: the current power (two agreeing windows). With a value: o, then the sentinel s set and read,
    then p set and read, on ONE open port; each answer of the chain names a value the device did not have before it, so it is
    newer than the SET that made it (FIFO): a stale backlog fails the chain."""
    import serial
    with serial.Serial(port("tdeck"), 115200, timeout=0.5) as s:
        if p is None:
            readback_ok(None, meshcom_txpwr(s))
            return
        p = int(p)
        o = meshcom_txpwr(s)
        meshcom_set_read(s, meshcom_sentinel(o, p))
        readback_ok(p, meshcom_set_read(s, p))


def meshcom_send(nonce):
    import serial
    with serial.Serial(port("tdeck"), 115200, timeout=0.5) as s:
        s.write(f"::{nonce}\r\n".encode())
        time.sleep(1)
    print(f"SENT {nonce} {ts()}")


def mt_iface():
    import meshtastic.serial_interface
    return meshtastic.serial_interface.SerialInterface(devPath=port("g2"))


def mt_power(p):
    if p is not None:
        i = mt_iface()
        try:
            i.localNode.localConfig.lora.tx_power = int(p)
            i.localNode.writeConfig("lora")
            time.sleep(3)
        finally:
            i.close()
        time.sleep(5)                                  # the node may reboot after a config write
    i = mt_iface()                                     # a NEW connection: the value the node holds, not the request
    try:
        readback_ok(p, i.localNode.localConfig.lora.tx_power)
    finally:
        i.close()


def mt_send(nonce):
    i = mt_iface()
    try:
        i.sendText(nonce)
        time.sleep(2)
        print(f"SENT {nonce} {ts()}")
    finally:
        i.close()


def mt_read(nonce, secs):
    from pubsub import pub
    hit = []

    def on_text(packet, interface):
        if nonce in ((packet.get("decoded") or {}).get("text") or ""):
            hit.append(f"{ts()} rssi {packet.get('rxRssi')} snr {packet.get('rxSnr')}")
    pub.subscribe(on_text, "meshtastic.receive.text")
    i = mt_iface()
    try:
        end = time.monotonic() + secs
        while time.monotonic() < end and not hit:
            time.sleep(0.5)
    finally:
        i.close()
    return hit[0] if hit else None


def mc_run(fn):
    import asyncio

    from meshcore import MeshCore
    pin = sys.stdin.readline().strip() if not sys.stdin.isatty() else ""

    async def main():
        m = await asyncio.wait_for(MeshCore.create_ble(device("tdpro"), pin=pin or None), 60)
        try:
            return await fn(m)
        finally:
            await m.disconnect()
    return asyncio.run(main())


def mc_power(p):
    async def f(m):
        if p is not None:
            await m.commands.set_tx_power(int(p))
        r = await m.commands.send_appstart()           # the device's own answer, not the request
        tx = (getattr(r, "payload", None) or {}).get("tx_power")
        if tx is None:
            raise RuntimeError("no tx_power in the T-Deck Pro's self info")
        readback_ok(p, tx)
    mc_run(f)


def mc_send(nonce):
    async def f(m):
        r = await m.commands.send_chan_msg(0, nonce)
        if r.type.name == "ERROR":
            raise RuntimeError("the send was refused")
        print(f"SENT {nonce} {ts()}")
    mc_run(f)


def mc_read(nonce, secs):
    import asyncio

    from meshcore import EventType
    hit = []

    async def f(m):
        async def on_msg(ev):
            if nonce in ((ev.payload or {}).get("text") or ""):
                hit.append(f"{ts()} {ev.type.name} snr {(ev.payload or {}).get('SNR')}")
        m.subscribe(EventType.CHANNEL_MSG_RECV, on_msg)
        m.subscribe(EventType.CONTACT_MSG_RECV, on_msg)
        await m.start_auto_message_fetching()
        end = time.monotonic() + secs
        while time.monotonic() < end and not hit:
            await asyncio.sleep(0.5)
        await m.stop_auto_message_fetching()
    mc_run(f)
    return hit[0] if hit else None


def rns_exchange(inn, box_dest, secs, dest_file, out, go_file, announces, known=False):
    """One exchange through the PC's shared rnsd, in a scratch config removed afterwards; the local file must name the
    RNode peer (PEER_rnode) before anything is opened."""
    import shutil
    import tempfile
    device("rnode")
    instance = rns_instance()
    key = rns_rpc_key(rns_config_dir())
    d = tempfile.mkdtemp(prefix="rfproof-rns-")
    try:
        return rns_run(d, instance, key, inn, box_dest, secs, dest_file, out, go_file, announces, known)
    finally:
        shutil.rmtree(d, ignore_errors=True)


def rns_config_dir():
    """The PC's shared rnsd config directory: the lab's RNS_CONFIG, which the runner exports as RFPROOF_RNS_CONFIG."""
    d = os.environ.get("RFPROOF_RNS_CONFIG", "")
    if not d or not os.path.isdir(d):
        raise RuntimeError("RFPROOF_RNS_CONFIG names no directory (the shared rnsd's config, the lab's RNS_CONFIG)")
    return d


def rns_rpc_key(cfgdir):
    """The shared rnsd's RPC key (hex), derived as rnsd derives it (RNS 1.5.4 Reticulum.py:360-361, :499-505): its
    config's [reticulum] rpc_key when that is hex, else the full hash of its transport identity's private key. A client
    without it derives the key from its own new identity, and the rnsd refuses every RPC of it. Never printed."""
    import RNS
    from RNS.vendor.configobj import ConfigObj
    section = ConfigObj(os.path.join(cfgdir, "config")).get("reticulum", {})
    if "rpc_key" in section:
        try:
            return bytes.fromhex(section["rpc_key"]).hex()
        except (TypeError, ValueError):
            pass                                          # rnsd falls back to the derived key as well
    path = os.path.join(cfgdir, "storage", "transport_identity")
    ident = RNS.Identity.from_file(path) if os.path.isfile(path) else None
    if ident is None:
        raise RuntimeError("the shared rnsd's transport identity is not readable (storage/transport_identity under "
                           "RFPROOF_RNS_CONFIG): no RPC key for its clients")
    return RNS.Identity.full_hash(ident.get_private_key()).hex()


# The PC's second announce waits until the box has been quiet this long. The box's announce is 2 frames (LXMF delivery +
# audio call) and the PC hears the first at its end: the second follows at once, <= toa(255 B) 0.723 s at 868.5 SF8 + CAD
# + TX result (0.69 s measured end to end), and the box cannot hear while it transmits (half duplex): an announce of the
# PC inside it is lost. 3 s >= 4x that; it runs inside the exchange's own deadline.
QUIET_S = 3.0
# The message waits this long after the PC's second announce: the shared rnsd re-sends a client's announce from its announce
# table at its next check (RNS 1.5.4 Transport.py:254 announces_check_interval 1.0 s, :766) but forwards the message at once,
# so the message went first and the announce after it, into the box's delivery proof (half duplex: a21's kit run 00:58Z).
# >= 1.0 s + the 0.25 s job + toa(255 B) 0.723 s + CAD.
ANNOUNCE_AIR_S = 3.0


def tsms():
    t = time.time()
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(t)) + f".{int(t % 1 * 1000):03d}Z"


def rns_run(d, instance, key, inn, box_dest, secs, dest_file, out, go_file, announces, known=False):
    import LXMF
    import RNS
    with open(os.path.join(d, "config"), "w") as f:
        f.write("[reticulum]\n  enable_transport = False\n  share_instance = Yes\n"   # a client of the PC's shared rnsd
                f"  instance_name = {instance[0]}\n  shared_instance_port = {instance[1]}\n"
                f"  instance_control_port = {instance[2]}\n  rpc_key = {key}\n[interfaces]\n")   # the rnsd's own key
    r = RNS.Reticulum(configdir=d, loglevel=RNS.LOG_ERROR)
    if not getattr(r, "is_connected_to_shared_instance", False):
        raise RuntimeError("not attached to the shared rnsd (refusing to open the RNode port)")
    router = LXMF.LXMRouter(storagepath=os.path.join(d, "lxmf"))
    me = router.register_delivery_identity(RNS.Identity(), display_name="RFPROOF-PC")
    hit, box, last, anns = [], [], [], []   # last: each frame of the box's identity heard; anns: its LXMF announces

    def on_delivery(m):
        c = m.content.decode("utf-8", "replace") if isinstance(m.content, bytes) else str(m.content)
        if inn in c:
            hit.append(ts())
    router.register_delivery_callback(on_delivery)

    class Ann:
        aspect_filter = None                                 # every aspect: the box's audio-call frame is part of its announce
        receive_path_responses = known                       # `known`: the shared rnsd's cached announce of the box

        def received_announce(self, destination_hash, announced_identity, app_data):
            ih = getattr(announced_identity, "hash", None)
            if destination_hash.hex() != box_dest:              # the box's LXMF address; its name only for the log
                if box and ih is not None and ih == box[0][2]:  # another destination of the box's identity: its time only
                    last.append(time.monotonic())
                return
            last.append(time.monotonic())
            anns.append(last[-1])
            if not box:
                print(f"BOX-NAME {LXMF.display_name_from_app_data(app_data) if app_data else '-'}")
                box.append((ts(), destination_hash, ih))
            else:                                                # each later one, by the PC's second (row 12: the call's)
                print(f"BOX-ANNOUNCE again {ts()}")
    RNS.Transport.register_announce_handler(Ann())
    with open(dest_file, "w") as f:
        f.write(RNS.hexrep(me.hash, delimit=False) + "\n")
    router.announce(me.hash)
    if known:   # only a path the shared rnsd holds: answered on the local interface, never a path request on the RNode
        if not any(e.get("hash") == bytes.fromhex(box_dest) for e in r.get_path_table() or []):
            print("BOX-PATH not cached: no path request sent")
            return None
        RNS.Transport.request_path(bytes.fromhex(box_dest))
    end = time.monotonic() + secs
    while time.monotonic() < end and not box:
        time.sleep(0.5)
    if not box:
        print("BOX-ANNOUNCE not heard")
        return None
    print(f"BOX-ANNOUNCE heard {box[0][0]}")
    while time.monotonic() < end and not os.path.exists(go_file):   # the second TX only after the runner's TX checks passed again
        time.sleep(0.5)
    if not os.path.exists(go_file):
        print("TX2-NOT-GATED: no go file from the sheet, the message is not sent")
        return None
    # once more, now that the box's node runs (its announce came): the launch announce went out before MeshChat's start
    # brought that node up, so it had no path to the PC for its own send. Only once the box is quiet: all `announces` LXMF
    # announces of this phase heard (row 12: the start's and the call's), then QUIET_S after its last frame heard and
    # after the go file (it follows the box's last announce of the phase, even a frame not heard here)
    seen = time.monotonic()

    def quiet():
        return len(anns) >= announces and time.monotonic() >= max(last[-1], seen) + QUIET_S
    while time.monotonic() < end and not quiet():
        time.sleep(0.5)
    if not quiet():
        print(f"PC-ANNOUNCE NOT SENT: {len(anns)} of {announces} box announces heard" if len(anns) < announces
              else "PC-ANNOUNCE NOT SENT: the box did not go quiet")
        return None
    router.announce(me.hash)
    print(f"PC-ANNOUNCE again {tsms()}")
    time.sleep(ANNOUNCE_AIR_S)                          # the announce on air alone, before the message (ANNOUNCE_AIR_S)
    dest = RNS.Destination(RNS.Identity.recall(box[0][1]), RNS.Destination.OUT, RNS.Destination.SINGLE, "lxmf", "delivery")
    msg = LXMF.LXMessage(dest, me, out, title="rfproof", desired_method=LXMF.LXMessage.OPPORTUNISTIC)
    done = []
    msg.register_delivery_callback(lambda m: done.append(ts()))
    LXMF.LXMRouter.MAX_DELIVERY_ATTEMPTS = 0   # one send, no retries (LXMF process_outbound: attempts 0 <= 0 sends, then fails)
    router.handle_outbound(msg)
    while time.monotonic() < end and not (done and hit):
        time.sleep(0.5)
    if not done:                                   # the box never confirmed our message: the exchange failed
        print(f"NOT-DELIVERED {out}")
        return None
    print(f"DELIVERED {out} {done[0]}")
    return hit[0] if hit else None


def main(a):
    err = usage_error(a)
    if err:
        print(err, file=sys.stderr)
        return 2
    act = a[0]
    if act == "meshcom-power":
        return meshcom_power(a[1] if len(a) > 1 else None)
    if act == "mt-power":
        return mt_power(a[1] if len(a) > 1 else None)
    if act == "mc-power":
        return mc_power(a[1] if len(a) > 1 else None)
    if act == "meshcom-send":
        return meshcom_send(a[1])
    if act == "mt-send":
        return mt_send(a[1])
    if act == "mc-send":
        return mc_send(a[1])
    nonce = a[1]
    if act == "meshcom-read":
        h = serial_wait(port("tdeck"), nonce, float(a[2]), MESHCOM_RX, pre=[b"--setinfo on\r\n"],
                        post=[b"--setinfo off\r\n"])   # --setinfo on prints received frames; --debug does not
    elif act == "aprs-read":
        h = serial_wait(port("tracker"), nonce, float(a[2]), APRS_RX)
    elif act == "mt-read":
        h = mt_read(nonce, float(a[2]))
    elif act == "mc-read":
        h = mc_read(nonce, float(a[2]))
    else:
        h = rns_exchange(nonce, a[2], float(a[3]), a[4], a[5], a[6], int(a[7]), a[8:] == ["known"])
    if h:
        print(f"SEEN {nonce} {h}")
        return 0
    print("NOT-SEEN")
    return NOT_SEEN


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]) or 0)
