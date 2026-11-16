"""The RF proof's peer driver: one action per call on one lab peer of this PC (testlab/hardware/rf-proof.sh runs it).

The devices come from the environment the runner exports from the lab's local file (testlab/hardware/lab.local.env):
`PEER_<name>="<kind> <by-id name | BLE address> <hub> ..."` for tdeck, tracker, g2 and tdpro; a missing one is refused by name.

Exit status: 0 = the expected result (a read: `SEEN <nonce> <time>`; a send: `SENT <nonce> <time>`; a power action:
`POWER <readback>`), 3 = `NOT-SEEN` (a read that ended without the nonce: the only negative result), anything else = an error
(the runner STOPs). Output is sanitised: a received line is never printed; only the fixed fields below are.
Power: every power action re-reads the hardware value after a set and prints that readback; the runner refuses a
peer transmission unless the readback is the low value. The PIN comes on stdin only (never argv, a file or a log).
The runner holds an exclusive flock around every call, so two peers never act at once (they share USB hubs).

  peer.py meshcom-power [<p>]          T-Deck (MeshCom): `--lora` read (its RF-Power line); with <p>: `--txpower <p>` then the read again
  peer.py meshcom-send  <nonce>        T-Deck: `::<nonce>`
  peer.py meshcom-read  <nonce> <s>    T-Deck: `--setinfo on` for the read (prints RX-LoRa2 lines; RAM only), `--setinfo off` after
  peer.py aprs-read     <nonce> <s>    CA2RXU tracker console, read-only
  peer.py mt-power      [<p>]          Station G2: lora.tx_power (with <p>: set, then a NEW connection reads it back)
  peer.py mt-send       <nonce>        Station G2: one broadcast text
  peer.py mt-read       <nonce> <s>    Station G2: a received text with the nonce
  peer.py mc-power      [<p>]          T-Deck Pro (BLE): tx power (with <p>: set, then the device query reads it back)
  peer.py mc-send       <nonce>        T-Deck Pro: one public-channel message
  peer.py mc-read       <nonce> <s>    T-Deck Pro: a channel or contact message with the nonce
  peer.py rns-exchange  <nonce-in> <box-name> <s> <dest-file> <nonce-out> <go-file>   a client of the PC's shared rnsd:
                                       announce, write the PC's LXMF destination hash to <dest-file>, wait for the box's announce
                                       (its display name), send <nonce-out> to it and wait for its delivery proof (the box's
                                       receipt: `DELIVERED <nonce-out> <time>`), and wait for an LXMF message carrying <nonce-in>;
                                       prints SEEN on it. The message is sent only once the runner has written
                                       <go-file> (after its TX checks passed again), and exactly once (no retry); without it:
                                       `TX2-NOT-GATED`, NOT-SEEN
"""
import os
import re
import sys
import time

NOT_SEEN = 3

# The actions, one table for the driver and its dry fake (fake_peer.py loads it from here): the peer each acts on and
# its kind; the kind fixes the arguments. A call that does not fit is a usage error: exit 2, before any device opens.
ACTIONS = {"meshcom-power": ("tdeck", "power"), "meshcom-send": ("tdeck", "send"), "meshcom-read": ("tdeck", "read"),
           "aprs-read": ("tracker", "read"),
           "mt-power": ("g2", "power"), "mt-send": ("g2", "send"), "mt-read": ("g2", "read"),
           "mc-power": ("tdpro", "power"), "mc-send": ("tdpro", "send"), "mc-read": ("tdpro", "read"),
           "rns-exchange": ("rnode", "exchange")}
ARGS = {"power": ("[<p>]",), "send": ("<nonce>",), "read": ("<nonce>", "<s>"),
        "exchange": ("<nonce-in>", "<box-name>", "<s>", "<dest-file>", "<nonce-out>", "<go-file>")}


def usage_error(a):
    """None when argv `a` is a call of the table, else the reason (the caller exits 2)."""
    if not a or a[0] not in ACTIONS:
        return f"unknown action {a[0] if a else '(none)'}"
    kind, args = ACTIONS[a[0]][1], a[1:]
    want = ARGS[kind]
    if kind == "power":
        ok = len(args) == 0 or (len(args) == 1 and re.fullmatch(r"-?\d+", args[0]))
    else:
        ok = len(args) == len(want) and all(re.fullmatch(r"\d+(\.\d+)?", v) for w, v in zip(want, args, strict=True) if w == "<s>")
    return None if ok else f"{a[0]}: {' '.join(want)}"


def device(name):
    """The device of peer `name` from `PEER_<name>` (its second field): a by-id name or a BLE address."""
    fields = os.environ.get(f"PEER_{name}", "").split()
    if len(fields) < 2:
        raise RuntimeError(f"PEER_{name} is not set (the lab's local file names each peer)")
    return fields[1]


def port(name):
    return "/dev/serial/by-id/" + device(name)


def ts():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def serial_wait(port, nonce, secs, pre=(), post=()):
    import serial
    with serial.Serial(port, 115200, timeout=0.5) as s:
        for c in pre:
            s.write(c)
            time.sleep(0.5)
        end = time.monotonic() + secs
        try:
            while time.monotonic() < end:
                line = s.readline().decode("utf-8", "replace")
                if nonce in line:
                    return ts()
        finally:
            for c in post:
                s.write(c)
                time.sleep(0.5)
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
    """The sentinel power of a SET to `p` from `o`: the lowest value of 2..17 dBm that is neither `o` nor `p`, so a SET
    above 17 (the T-Deck's restore to 22) has one too."""
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


def rns_exchange(inn, box_name, secs, dest_file, out, go_file):
    """One exchange through the PC's shared rnsd, in a scratch config removed afterwards; the local file must name the
    RNode peer (PEER_rnode) before anything is opened."""
    import shutil
    import tempfile
    device("rnode")
    d = tempfile.mkdtemp(prefix="rfproof-rns-")
    try:
        return rns_run(d, inn, box_name, secs, dest_file, out, go_file)
    finally:
        shutil.rmtree(d, ignore_errors=True)


def rns_run(d, inn, box_name, secs, dest_file, out, go_file):
    import LXMF
    import RNS
    with open(os.path.join(d, "config"), "w") as f:
        f.write("[reticulum]\n  enable_transport = False\n  share_instance = Yes\n"
                "  instance_name = lhpc-rf-host\n  shared_instance_port = 37438\n  instance_control_port = 37439\n[interfaces]\n")   # the PC's shared rnsd (its AF_UNIX instance name; the ports are not used on Linux)
    r = RNS.Reticulum(configdir=d, loglevel=RNS.LOG_ERROR)
    if not getattr(r, "is_connected_to_shared_instance", False):
        raise RuntimeError("not attached to the shared rnsd (refusing to open the RNode port)")
    router = LXMF.LXMRouter(storagepath=os.path.join(d, "lxmf"))
    me = router.register_delivery_identity(RNS.Identity(), display_name="RFPROOF-PC")
    hit, box = [], []

    def on_delivery(m):
        c = m.content.decode("utf-8", "replace") if isinstance(m.content, bytes) else str(m.content)
        if inn in c:
            hit.append(ts())
    router.register_delivery_callback(on_delivery)

    class Ann:
        aspect_filter = "lxmf.delivery"

        def received_announce(self, destination_hash, announced_identity, app_data):
            if app_data and LXMF.display_name_from_app_data(app_data) == box_name and not box:
                box.append((ts(), destination_hash))
    RNS.Transport.register_announce_handler(Ann())
    with open(dest_file, "w") as f:
        f.write(RNS.hexrep(me.hash, delimit=False) + "\n")
    router.announce(me.hash)
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
        h = serial_wait(port("tdeck"), nonce, float(a[2]), pre=[b"--setinfo on\r\n"], post=[b"--setinfo off\r\n"])   # --setinfo on prints received frames; --debug does not
    elif act == "aprs-read":
        h = serial_wait(port("tracker"), nonce, float(a[2]))
    elif act == "mt-read":
        h = mt_read(nonce, float(a[2]))
    elif act == "mc-read":
        h = mc_read(nonce, float(a[2]))
    else:
        h = rns_exchange(nonce, a[2], float(a[3]), a[4], a[5], a[6])
    if h:
        print(f"SEEN {nonce} {h}")
        return 0
    print("NOT-SEEN")
    return NOT_SEEN


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]) or 0)
