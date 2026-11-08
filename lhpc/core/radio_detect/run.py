"""Detect live (DEC-W43 W1/W2): find a radio's chip without its daemon, on the radio's own bus.

`detect_radio` (a radio of the hardware list), `detect_stick` (a listed USB stick before Add) and
`detect_board` (a HAT before it is in the list) each return one `Detected`. The order is fixed:
1. the daemon check comes FIRST, before any line or stick is touched: a radio whose daemon runs is refused
   ("Detect refused: r3's daemon is running. Stop it first."), with the same answer Remove uses
   (radio_start.daemon_running);
2. then the other refusals: a direct-radio stack (meshtastic, rns) holds the band, the list is invalid, the
   stick is not plugged in, a line or the stick is held by another process;
3. then the bus opens (SpiBus, or Ch341Bus on the selected stick), `chips` searches for the model's chip
   (RadioLib's 10 tries), and the bus is closed again in every case.
Nothing is written to config or state; nothing transmits.
"""
from __future__ import annotations

from dataclasses import dataclass

from . import DetectRefused, ch341, chips, spi

LABEL = {"sx127x": "SX127x", "sx1262": "SX1262"}


@dataclass(frozen=True)
class Detected:
    outcome: str            # "found" | "not-found" | "refused"
    line: str               # the one line to show; red when refused
    chip: str = ""          # the family searched for ("" when refused before the row resolved)
    version: str = ""       # what the chip answered ("0x12", "SX1261 …"); "" otherwise


def _refused(who: str, why: str, chip: str = "") -> Detected:
    return Detected("refused", f"Detect refused: {who}: {why}", chip)


def _daemon_refusal(rid: str, chip: str = "") -> Detected:
    return Detected("refused", f"Detect refused: {rid}'s daemon is running. Stop it first.", chip)


# The bus and USB seams (tests replace them; Detect itself never builds a bus any other way).
spi_bus = spi.SpiBus
libusb = ch341.Libusb


def _scan():
    from .. import usb_access
    return usb_access.scan()


def _lock_path(svc):
    """The daemon's `spi0.lock`, in its runtime directory (LORAHAM_RUNTIME_DIR), so a transfer never interleaves."""
    return svc._paths.runtime_root / "state" / "loraham" / "spi0.lock"


def _port_tuple(port: str) -> tuple:
    """A sysfs port name ("1-1.3") as libusb names it: (bus, port, port, …)."""
    bus, _, chain = port.partition("-")
    return (int(bus), *(int(p) for p in chain.split(".")))


def _stick_port(serial: str, port: str):
    """(port, "") of the stick to open, or ("", why): radio_start.stick_port on this module's scan (DEC-W35)."""
    from .. import radio_start
    return radio_start.stick_port(serial, port, _scan())


def _search(bus, chip: str):
    """(found, version text) from RadioLib's search for `chip` on an open bus."""
    if chip == "sx127x":
        ok, version = chips.find_sx127x(bus)
        return ok, (f"0x{version:02x}" if version is not None else "")
    ok, text = chips.find_sx1262(bus)
    return ok, bytes(b for b in text if 32 <= b < 127).decode("ascii").strip()


def _run(who: str, chip: str, open_bus) -> Detected:
    """Open the bus, search, close it again in every case; a refusal from the bus is the line."""
    label = LABEL.get(chip, chip)
    try:
        bus = open_bus()
    except DetectRefused as exc:
        return _refused(who, str(exc), chip)
    except OSError as exc:
        return _refused(who, f"the bus cannot be opened: {exc}", chip)
    try:
        ok, version = _search(bus, chip)
    except DetectRefused as exc:
        return _refused(who, str(exc), chip)
    except OSError as exc:
        return _refused(who, f"the bus failed during the search: {exc}", chip)
    finally:
        bus.close()
    if ok:
        return Detected("found", f"{who}: {label} found ({'version ' + version if chip == 'sx127x' else version})",
                        chip, version)
    return Detected("not-found", f"{who}: no {label} answered", chip, version)


def _open_spi(svc, pins: dict, chip: str):
    return lambda: spi_bus(pins, chip, _lock_path(svc))


def _open_usb(model, pins: dict, serial: str, port: str):
    def open_bus():
        usb = libusb()
        try:
            handle = ch341.select(usb, int(model.usb["vid"], 16), int(model.usb["pid"], 16),
                                  serial=serial, port=_port_tuple(port))
        except BaseException:
            usb.close()
            raise
        try:
            return ch341.Ch341Bus(usb, handle, pins)
        except BaseException:
            usb.release(handle)
            usb.close()
            raise
    return open_bus


def _direct_owner(svc, band: str) -> str:
    owner = svc._direct_radio_owners({band}).get(band)
    return (svc.stack_of(owner) or owner) if owner else ""


def detect_radio(svc, rid: str) -> Detected:
    """W1: Detect on radio `rid` of the hardware list."""
    from .. import radio_models, radio_start, radios
    state = radios.load(svc._paths)
    row = next((r for r in state.radios if r.id == rid), None) if state.kind == radios.VALID else None
    if row is not None and radio_start.daemon_running(svc, row):           # FIRST: nothing touched yet
        return _daemon_refusal(rid)
    if state.kind != radios.VALID:
        return _refused(rid, "the hardware list is " + ("absent" if state.kind == radios.ABSENT else "not valid"))
    if row is None:
        return _refused(rid, "there is no such radio in the hardware list")
    model = radio_models.load().get(row.model)
    variant = radios.variant(row, model) if model else None
    if variant is None:
        return _refused(rid, f"its model {row.model!r} or its variant is unknown")
    if owner := _direct_owner(svc, row.band):
        return _refused(rid, f"{owner} drives the {row.band} MHz radio itself; stop it first", model.chip)
    if model.transport == "spi":
        return _run(rid, model.chip, _open_spi(svc, dict(variant.pins), model.chip))
    port, why = _stick_port(row.usb_serial, row.usb_port)
    if why:
        return _refused(rid, why, model.chip)
    return _run(rid, model.chip, _open_usb(model, dict(variant.pins), row.usb_serial, port))


_UNKNOWN_DAEMON = "Detect refused: a daemon process of unknown identity is running. Stop it first."


def _list_refusal(state, who: str):
    """G1-C5: Detect before an Add refuses an invalid hardware list (an absent one is fine: nothing to add to yet)."""
    from .. import radios
    return _refused(who, "the hardware list is not valid") if state.kind == radios.INVALID else None


def _running(svc, rows, listed, band: str = "") -> str:
    """The first of `rows` whose daemon runs (radio_start.daemon_running, the answer Remove uses); else a daemon no
    `listed` radio explains (R7v6): "?" for one of unknown identity, its id for an `--id` daemon whose row is absent,
    "the <band> MHz radio" for a no-id (0.13.x) daemon on `band`; else ""."""
    from .. import radio_start
    procs = radio_start._daemon_procs(svc)
    if hit := next((r.id for r in rows if radio_start.daemon_running(svc, r, procs)), ""):
        return hit
    ids = {r.id for r in listed}
    p = next((p for p in procs if p.kind == "unknown" or (p.kind == "id" and p.rid not in ids)
              or (p.kind == "no-id" and band and p.band == band)), None)
    return "" if p is None else "?" if p.kind == "unknown" else p.rid or f"the {band} MHz radio"


def _daemon_line(rid: str, chip: str = "") -> Detected:
    return Detected("refused", _UNKNOWN_DAEMON, chip) if rid == "?" else _daemon_refusal(rid, chip)


def detect_stick(svc, model_id: str, port: str) -> Detected:
    """W2: Detect on the listed USB stick on sysfs `port` as model `model_id`, before it is added."""
    from .. import radio_models, radio_start, radios
    from ..usb_access import Probe
    model = radio_models.load().get(model_id) if isinstance(model_id, str) else None
    who = f"{model_id} on port {port}"
    if model is None or model.transport == "spi":
        return _refused(who, "not a USB model")
    state = radios.load(svc._paths)
    if (bad := _list_refusal(state, who)) is not None:
        return bad
    rows = state.radios if state.kind == radios.VALID else ()
    scan = _scan()
    dev = next((d for d in scan.devices if d.port == port), None)
    serial = dev.serial.value if dev is not None and dev.serial.state is Probe.PRESENT else ""
    unread = dev is not None and dev.serial.state is Probe.UNKNOWN
    # the radios this stick may be (G1-C5): every USB row whose stick resolves to this port; with its serial
    # unreadable, also every serial row whose stick is listed nowhere else
    holders = [r for r in rows if (r.usb_serial or r.usb_port) and (
        radio_start.stick_port(r.usb_serial, r.usb_port, scan)[0] == port
        or (unread and r.usb_serial and not radio_start.stick_port(r.usb_serial, r.usb_port, scan)[0]))]
    if rid := _running(svc, holders, rows):                                # FIRST (a no-id daemon is a board's)
        return _daemon_line(rid)
    if dev is None:
        return _refused(who, f"no stick is plugged in on port {port}", model.chip)
    variant = model.variants[0] if len(model.variants) == 1 else None
    if variant is None:
        return _refused(who, f"{model_id} has {len(model.variants)} variants; add it first", model.chip)
    if owner := _direct_owner(svc, model.bands[0]):
        return _refused(who, f"{owner} drives the {model.bands[0]} MHz radio itself; stop it first", model.chip)
    return _run(who, model.chip, _open_usb(model, dict(variant.pins), serial, port))


def detect_board(svc, model_id: str, variant_key: str, band: str) -> Detected:
    """W2: Detect on a HAT (`model_id`, its variant `variant_key`) for `band`, before it is in the list."""
    from .. import radio_models, radios
    model = radio_models.load().get(model_id) if isinstance(model_id, str) else None
    who = f"{model_id} {band} MHz"
    if model is None or model.transport != "spi":
        return _refused(who, "not a board (SPI) model")
    state = radios.load(svc._paths)
    if (bad := _list_refusal(state, who)) is not None:
        return bad
    rows = state.radios if state.kind == radios.VALID else ()
    if rid := _running(svc, [r for r in rows if r.band == band], rows, band):   # FIRST: the band's radios' daemons
        return _daemon_line(rid, model.chip)
    variant = next((v for v in model.variants if v.key == variant_key), None)
    if variant is None or band not in model.bands or (variant.band and variant.band != band):
        return _refused(who, f"{model_id} has no variant {variant_key!r} on {band} MHz", model.chip)
    if owner := _direct_owner(svc, band):
        return _refused(who, f"{owner} drives the {band} MHz radio itself; stop it first", model.chip)
    return _run(who, model.chip, _open_spi(svc, dict(variant.pins), model.chip))
