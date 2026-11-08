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
