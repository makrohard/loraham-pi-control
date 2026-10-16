"""The box's SPI chip-select mode, and what it means for the components that drive the bus.

Exactly two boot configurations are supported:

* soft: `dtparam=spi=on` + `dtoverlay=spi0-0cs`. The kernel releases CE0/CE1 and every radio
  drives its own chip select as a GPIO on `/dev/spidev0.0`.
* kernel: `dtparam=spi=on` with no other spi0 overlay. The kernel owns CE0 = GPIO 8 and
  CE1 = GPIO 7, so a radio on GPIO 8 uses `/dev/spidev0.0` and one on GPIO 7 `/dev/spidev0.1`,
  and every transfer is one kernel message with its chip select held by the SPI core.

The mode is valid only when the boot file and the BOOTED device tree agree. A file edited but not
yet rebooted, or any other spi0 overlay (one chip select, remapped pins), refuses every component
that drives the bus. The daemon and the RNS node decide their own spidev from the live kernel; the
meshtasticd config is rewritten here, because meshtasticd reads its chip select from its YAML.
"""
from __future__ import annotations

import re
from pathlib import Path

SOFT, KERNEL, OFF, UNSUPPORTED = "soft", "kernel", "off", "unsupported"
DT = Path("/sys/firmware/devicetree/base")
CONFIG_TXT = (Path("/boot/firmware/config.txt"), Path("/boot/config.txt"))
CE_PINS = (8, 7)                 # CE0, CE1 as the Pi 5 and Zero 2 W device trees declare them
ACTIVE_LOW = 1                   # the specifier flags those trees give: <&gpio 8 1>, <&gpio 7 1>


def boot_mode(text: str | None) -> str:
    """What config.txt asks for: soft, kernel, off (no SPI, or no file) or unsupported."""
    if text is None:
        return OFF
    lines = [ln.split("#", 1)[0].strip() for ln in text.splitlines()]
    if "dtparam=spi=on" not in lines:
        return OFF
    overlays = [ln for ln in lines if ln.startswith("dtoverlay=spi0-")]
    if not overlays:
        return KERNEL
    return SOFT if overlays == ["dtoverlay=spi0-0cs"] else UNSUPPORTED


def _u32(raw: bytes) -> list[int]:
    return [int.from_bytes(raw[i:i + 4], "big") for i in range(0, len(raw) - 3, 4)]


def live_mode(dt: Path = DT) -> str:
    """What the running kernel booted with, from spi0's `cs-gpios` (big-endian u32 triplets:
    the GPIO controller's phandle, the pin, the flags). Empty = soft; exactly
    `<&gpio 8 1>, <&gpio 7 1>` = kernel; no spi0 alias, or the node's `status` not "okay"
    (SPI disabled; the base tree keeps the alias) = off; anything else = unsupported."""
    try:
        node = (dt / "aliases" / "spi0").read_bytes().rstrip(b"\0").decode().lstrip("/")
    except (OSError, UnicodeDecodeError):
        return OFF
    try:
        if not (dt / node / "status").read_bytes().startswith(b"okay"):
            return OFF
    except OSError:
        pass                                  # no status property = enabled (DT default)
    try:
        raw = (dt / node / "cs-gpios").read_bytes()
    except OSError:
        return UNSUPPORTED
    if not raw:
        return SOFT
    try:
        gpio = (dt / "__symbols__" / "gpio").read_bytes().rstrip(b"\0").decode().lstrip("/")
        phandle = _u32((dt / gpio / "phandle").read_bytes())[0]
    except (OSError, UnicodeDecodeError, IndexError):
        return UNSUPPORTED
    want = [v for pin in CE_PINS for v in (phandle, pin, ACTIVE_LOW)]
    return KERNEL if len(raw) == 4 * len(want) and _u32(raw) == want else UNSUPPORTED


def read_config_txt(paths=CONFIG_TXT) -> str | None:
    for p in paths:
        try:
            return Path(p).read_text(errors="replace")
        except OSError:
            continue
    return None


def refusal(boot: str, live: str) -> str:
    """Why no component may drive the SPI bus now, or "" when the mode is valid."""
    if UNSUPPORTED in (boot, live):
        return ("unsupported SPI boot configuration: use `dtparam=spi=on` alone (kernel chip "
                "selects) or with `dtoverlay=spi0-0cs` (soft), then reboot")
    if boot != live:
        return (f"reboot required: config.txt asks for {boot} SPI chip selects, but the running "
                f"kernel booted with {live}")
    return ""


def doctor_lines(paths, stacks, source_dir) -> list[str]:
    """`lhpc doctor`'s read-only SPI report: what config.txt asks for, what the running kernel
    booted with, and the start gate's refusal while the two disagree or either is unsupported; then
    one line per installed SPI bus user built from a `kernel_cs_since` component: built with kernel
    chip selects, or the provenance refusal its start gets once the kernel owns them."""
    boot, live = boot_mode(read_config_txt()), live_mode()
    why = refusal(boot, live)
    lines = [f"SPI chip selects: config.txt {boot}, running kernel {live} — "
             + (f"SPI stacks are refused: {why}" if why else "they agree")]
    note = "" if live == KERNEL else " (checked at start only with kernel chip selects)"
    for stack in stacks:
        by_id = {c.id: c for c in stack.components}
        for comp in stack.components:
            owners = [comp] + [by_id[d] for d in (comp.build_requires or ()) if d in by_id]
            if (drives_spi(comp) and any(getattr(o, "kernel_cs_since", "") for o in owners)
                    and Path(source_dir(comp)).is_dir()):
                state = provenance_refusal(paths, stack, comp, source_dir) or "built with kernel chip selects"
                lines.append(f"SPI user {stack.id}/{comp.id}: {state}{note}")
    return lines


def installed_commit(paths, stack, comp, owner, src, owner_src) -> str:
    """The commit of `owner` (the SPI user itself or one of its build dependencies) that `comp`
    will run, from the provenance LHPC already keeps: the binary channel's receipt (valid, its
    files verified); else a `consumed <owner> <sha>` line in `comp`'s completion marker or its
    `.rev` side file (both written only after every build step); else the owner's checkout HEAD
    (the daemon's source build writes no marker). "" when none can be read."""
    import subprocess

    from . import binary_receipt
    state, rec, _ = binary_receipt.receipt_state(paths, stack.id)
    if state == "valid" and rec is not None:
        return rec.components.get(owner.id, "") if binary_receipt.verify_files(paths, rec)[0] else ""
    if state != "absent":
        return ""          # unsafe or superseded (both faults lhpc doctor names): refused
    if comp.build_marker:
        for name in (comp.build_marker, comp.build_marker + ".rev"):
            try:
                for line in (Path(src) / name).read_text().splitlines():
                    f = line.split()
                    if len(f) == 3 and f[0] == "consumed" and f[1] == owner.id:
                        return f[2]
            except OSError:
                continue
        return ""
    r = subprocess.run(["git", "-C", str(owner_src), "rev-parse", "HEAD"], capture_output=True,
                       text=True, check=False)
    return r.stdout.strip() if r.returncode == 0 else ""


def provenance_refusal(paths, stack, comp, source_dir) -> str:
    """With kernel chip selects, what `comp` runs must contain the `kernel_cs_since` commit of
    every component it is built from that declares one (itself or a build dependency); an older
    build would claim GPIO 8/7 the kernel owns."""
    import subprocess
    by_id = {c.id: c for c in stack.components}
    for owner in [comp] + [by_id[d] for d in (comp.build_requires or ()) if d in by_id]:
        since = getattr(owner, "kernel_cs_since", "")
        if not since:
            continue
        built = installed_commit(paths, stack, comp, owner, source_dir(comp), source_dir(owner))
        if built == since or (built and subprocess.run(
                ["git", "-C", str(source_dir(owner)), "merge-base", "--is-ancestor", since, built],
                capture_output=True, check=False).returncode == 0):
            continue
        what = (f"was built from {built[:9]}, which predates kernel chip selects" if built else
                "has no recorded commit to check against kernel chip selects")
        return (f"{owner.id} {what} ({since[:9]}): update it "
                f"(`lhpc update {stack.id} --yes`) before starting it with kernel chip selects")
    return ""


def drives_spi(comp) -> bool:
    return any(getattr(r.kind, "value", r.kind) == "spi-bus" for r in comp.resources)


def meshtasticd_yaml(text: str, live: str) -> str:
    """meshtasticd's config for the live mode: with kernel chip selects the `Lora` section's
    `CS: 8|7` becomes `spidev: spidev0.0|spidev0.1` (meshtasticd then leaves CS to the kernel).
    Any other CS in that mode is refused (a transfer on spidev0.0 would also assert CE0)."""
    if live != KERNEL:
        return text
    out, section = [], ""
    for line in text.splitlines(keepends=True):
        if line[:1] not in (" ", "\t", "#", "\n", "\r") and line.strip():
            section = line.split(":", 1)[0].strip()
        m = re.match(r"^(\s+)(CS|spidev):\s*([^#\s]*)", line) if section == "Lora" else None
        if m and m.group(2) == "spidev":
            continue                                  # replaced below
        if m:
            cs = m.group(3)
            if cs not in ("8", "7"):
                raise ValueError(f"the kernel owns the chip selects (GPIO 8 and 7) and "
                                 f"meshtastic's CS is GPIO {cs}: use soft chip selects "
                                 f"(dtoverlay=spi0-0cs, then reboot) or CS 8/7")
            out.append(f"{m.group(1)}spidev: spidev0.{CE_PINS.index(int(cs))}\n")
            continue
        out.append(line)
    return "".join(out)


def render(fmt: str, text: str) -> str:
    """The generated-config hook: meshtasticd's YAML (the only `yaml-update` file) follows the
    live chip-select mode; every other format is returned as it is."""
    return meshtasticd_yaml(text, live_mode()) if fmt == "yaml-update" else text
