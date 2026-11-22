"""A hardware list for the existing tests at stage L5: the daemon of a band is that band's radio's, so a test that
starts the daemon writes `config/radios.toml` first. RADIOS has r1 on 433 and r2 on 868 (a LoRaHAM board each), the
shape of a 0.13.x dual-band box after the conversion."""
from __future__ import annotations

from lhpc.core import radios

RADIOS = (radios.Radio("r1", "low", "loraham", "433"), radios.Radio("r2", "high", "loraham", "868"))
RID = {"433": "r1", "868": "r2"}


def write(root, rows=RADIOS):
    """Write `rows` as the hardware list under the runtime root `root`."""
    (root / "config").mkdir(parents=True, exist_ok=True)
    (root / "config" / "radios.toml").write_text(radios.render(tuple(rows)))


def reticulum_backend(root, rids, value="spi"):
    """Save Reticulum's `backend` (plan reti v21 §1, B1) on each radio of `rids` under the runtime root `root`: a test
    of the direct-SPI node (its claims, requirements, no daemon) saves `spi`; the default is `daemon`."""
    (root / "config" / "stacks").mkdir(parents=True, exist_ok=True)
    for rid in rids:
        (root / "config" / "stacks" / f"reticulum@{rid}.toml").write_text(f'backend = "{value}"\n')


def conf(band: str) -> str:
    """The CONF socket path of `band`'s radio in RADIOS (the daemon's /tmp socket)."""
    return f"/tmp/loraconf-{RID[band]}.sock"


def ready(band: str, extra: bytes = b"") -> bytes:
    """A READY STATUS line of `band`'s radio, naming it (ID=) and its SPI binding (BOUND=-)."""
    return b"STATUS RADIO=READY ID=" + RID[band].encode() + b" BOUND=-" + (b" " + extra if extra else b"") + b"\n"


def by_radio(replies: dict) -> dict:
    """A 0.13.x-shaped FakeSystem reply table (`/tmp/loraconf<band>.sock` → its STATUS line) at L5: each band socket
    becomes its radio's (`/tmp/loraconf-<rid>.sock`), and each STATUS line gains `ID=<rid> BOUND=-` when it has no ID."""
    out = {}
    for path, reply in replies.items():
        band = next((b for b in RID if path == f"/tmp/loraconf{b}.sock"), None)
        if band is None:
            out[path] = reply
            continue
        if reply.startswith(b"STATUS ") and b" ID=" not in reply:
            reply = b"STATUS ID=" + RID[band].encode() + b" BOUND=- " + reply[len(b"STATUS "):]
        out[conf(band)] = reply
    return out


# Item 7 (stack instances): three radios, r1 and r2 on 433, a USB stick r3 on 868, with READY CONF replies for r1 and r3.
R1, R2 = radios.Radio("r1", "low", "loraham", "433"), radios.Radio("r2", "wave", "waveshare-sx1262", "433")
R3 = radios.Radio("r3", "stick", "meshstick-1262", "868", usb_serial="MSTICK01")
# K4ac: a box whose r3 is a copy's radio (868's second; the hardware list gives the copies, nothing is listed).
R2_868 = radios.Radio("r2", "high", "loraham", "868")
INSTANCE_READY = {f"/tmp/loraconf-{r}.sock": f"STATUS RADIO=READY ID={r} BOUND=- TXMODE=MANAGED\n".encode() for r in ("r1", "r3")}


def kiss_on(rid):                                      # a kiss-tnc process of radio `rid`, by its instance token
    return ["loraham-kiss-tnc", "--data-socket", f"/tmp/loraf-{rid}.sock"]


# STATUS-DEAD-SOCKET: box H's radios of 11-15 (r3 the CH341 433 stick, r4 the PineDio 868 stick) and its launch records.
BOX_H = RADIOS + (radios.Radio("r3", "rfm98 433", "ch341-rfm98pw-433", "433", usb_serial="CHE1W433"),
                  radios.Radio("r4", "pine64 868", "pinedio-usb-1262", "868", usb_port="1-1"))
NO_PID = 4194304                 # the largest pid_max is 2**22: a pid from here on is no process, ever


def launch_record(root, sid, cid, rid, band, pid, *, boot=None):
    """Write the launch record Lifecycle.start leaves for `<sid>@<rid>`'s `cid` (box H r3's shape, 11-15) under the
    runtime root `root`; `boot`: its boot id (default: this boot's)."""
    import json

    from lhpc.core.lifecycle import current_boot_id
    lid = f"{cid}__{band}__{pid}__{rid}"
    rec = {"rid": rid, "starttime": 4705741, "pgid": pid, "sid": pid, "exec": cid, "version": 1, "launch_id": lid,
           "stack": sid, "component": cid, "requested_target": sid, "start_scope": "stack", "band": band, "pid": pid,
           "role": "", "launched_at": 1794776015, "boot_id": current_boot_id() if boot is None else boot}
    (root / "state" / "owned").mkdir(parents=True, exist_ok=True)
    (root / "state" / "owned" / f"{lid}.json").write_text(json.dumps(rec))


def live_records(monkeypatch, pids):
    """Records of `pids` verify as live (Lifecycle.verify_owned): a fake process has no /proc identity to verify, and
    the daemon's instances are told by their records alone (its argv names no instance)."""
    from lhpc.core.lifecycle import Lifecycle
    live = set(pids)
    monkeypatch.setattr(Lifecycle, "verify_owned",
                        lambda self, rec: (True, "verified") if rec.get("pid") in live else (False, "process gone"))
