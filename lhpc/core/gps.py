"""The ONE typed GPS resolver.

Every consumer of the global `[gps]` setting — the UI, config generation, lifecycle
planning, resource claims, post-start steps, status, stop and boot restore — asks this
module, and they all get the SAME answer for a given config. That is deliberate: the
failure mode this design exists to prevent is two subsystems disagreeing about where
position comes from (one claiming a serial device the other thinks is free, or a config
rendered for a source the lifecycle never planned for).

Nothing here does blind string substitution. `commands.py` keeps its narrow allow-list of
controller-derived argv values; the only GPS value that reaches a generated config file is
the controller-owned PTY path, and it goes through the typed plan below.

The bridge process itself reads `[gps]` directly rather than being handed a rendered
string, so there is no path by which a saved or ephemeral user value becomes its source.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

# Consumers that can be fed a device-shaped NMEA stream by the bridge. Each gets its OWN
# bridge instance: no shared process, no reference counting, and the two want different
# output shapes anyway (a PTY for meshtasticd, a UNIX socket for MeshCom's QEMU UART1).
CONSUMER_MESHTASTIC = "meshtastic"
CONSUMER_MESHCOM = "meshcom"

# How the bridge hands NMEA to a consumer.
OUT_PTY = "pty"
OUT_UNIX = "unix"

_OUTPUT_FOR = {CONSUMER_MESHTASTIC: OUT_PTY, CONSUMER_MESHCOM: OUT_UNIX}


@dataclass(frozen=True)
class GpsPlan:
    """The resolved, effective GPS decision. Computed ONCE before lifecycle locking and
    reused verbatim everywhere, so run order, claims and rendering can never diverge."""

    source: str = "off"
    valid: bool = True
    reason: str = ""
    # Absolute path of the real character device this plan opens locally, or "" when it
    # opens none (off / fixed / remote gpsd). Drives the exclusive serial claim.
    device: str = ""
    # Stable lock key for that device, resolved through its actual identity (st_rdev), so
    # /dev/ttyACM0 and /dev/serial/by-id/... cannot both be claimed as if they were two.
    device_key: str = ""
    host: str = ""
    port: int = 0
    nmea_baud: int = 0
    fixed_lat: str = ""
    fixed_lon: str = ""
    fixed_alt: str = ""

    @property
    def enabled(self) -> bool:
        return self.source != "off"

    @property
    def is_fixed(self) -> bool:
        return self.source == "fixed"

    def disabled_for_stack(self) -> GpsPlan:
        """The same plan as seen by a stack whose `use_gps` is off: nothing opened, nothing
        claimed, nothing rendered. `reason` keeps WHY, so status can say "the stack opted out"
        rather than "no position source configured", which are different problems."""
        return GpsPlan(source="off", valid=self.valid,
                       reason="this stack's GPS switch is off (use_gps)")

    @property
    def claims_device(self) -> bool:
        """Whether this plan opens a LOCAL character device. Only direct NMEA does; off,
        fixed and remote gpsd must claim nothing, or they would refuse valid combinations."""
        return bool(self.device)

    def needs_bridge(self, consumer: str) -> bool:
        """Does `consumer` need a bridge instance under this plan?

        Meshtastic does NOT for `fixed`: meshtasticd has native fixed-position support
        (`--setlat/--setlon/--setalt`), which is both simpler and avoids the ~37 s chip
        probe a synthesized stream would still incur. It also does not for `nmea`, where
        pointing meshtasticd straight at the real device gives it a chip it can actually
        detect. MeshCom always needs one when GPS is on, because its pinned relay supports
        only a LOCAL gpsd and cannot serve remote gpsd, direct NMEA, or a fixed position.
        """
        if not self.enabled:
            return False
        if consumer == CONSUMER_MESHTASTIC:
            return self.source == "gpsd"
        return consumer == CONSUMER_MESHCOM

    def output_kind(self, consumer: str) -> str:
        return _OUTPUT_FOR.get(consumer, OUT_PTY)


def meshtastic_post_step_values(plan: GpsPlan) -> dict:
    """Controller-owned values for Meshtastic's GPS post-steps.

    `gps_mode` is applied in BOTH directions: turning GPS off must actively push
    NOT_PRESENT, or a node enabled earlier keeps its old device state and goes on beaconing.

    `gps_fixed_args` uses meshtasticd's NATIVE fixed position rather than a synthesized NMEA
    stream — simpler, and it skips the ~37 s chip probe entirely. When the source is NOT
    fixed we always push `--remove-position`, with no memory of what was set before: `[gps]`
    is authoritative, so "not fixed" must mean the node holds no fixed position, whoever set
    it. Remembering instead would let a hand-set position survive and be beaconed forever.
    """
    if plan.is_fixed:
        args = ["--setlat", str(plan.fixed_lat), "--setlon", str(plan.fixed_lon)]
        if plan.fixed_alt:
            args += ["--setalt", str(plan.fixed_alt)]
        # A fixed node must not also run the GPS thread hunting for a chip.
        return {"gps_mode": "NOT_PRESENT", "gps_fixed_args": args}
    return {"gps_mode": "ENABLED" if plan.enabled else "NOT_PRESENT",
            "gps_fixed_args": ["--remove-position"]}


def plan_from_config(cfg, *, resolve_device=True) -> GpsPlan:
    """Resolve `[gps]` into the effective plan. Pure with respect to the config: it reads
    the filesystem only to resolve a device's identity, and a failure there is reported,
    never guessed."""
    g = getattr(cfg, "gps", None)
    if g is None:
        return GpsPlan()
    if not getattr(g, "valid", True):
        return GpsPlan(source="off", valid=False, reason=g.reason)
    if not g.enabled:
        return GpsPlan()

    device = g.device if g.claims_local_serial else ""
    key, reason = ("", "")
    if device and resolve_device:
        key, reason = device_lock_key(device)
        if not key:
            # A device we cannot identify must not be claimed by name and hoped for — that
            # is exactly how an alias slips past an exclusive claim.
            return GpsPlan(source="off", valid=False,
                           reason=f"cannot identify GPS device {device}: {reason}")
    return GpsPlan(source=g.source, device=device, device_key=key, host=g.host, port=g.port,
                   nmea_baud=g.nmea_baud, fixed_lat=g.fixed_lat, fixed_lon=g.fixed_lon,
                   fixed_alt=g.fixed_alt)


def device_lock_key(path: str) -> tuple[str, str]:
    """(key, error). Identify a character device by its REAL identity, not its path.

    `/dev/ttyACM0` and `/dev/serial/by-id/usb-u-blox_...` are the same receiver; a claim
    keyed on the string would let one stack take each and both think they had it
    exclusively. `st_rdev` is the device number, so both paths collapse to one key.
    """
    try:
        st = os.stat(path)
    except OSError as exc:
        return "", f"{type(exc).__name__}: {exc.strerror or exc}"
    import stat as _stat
    if not _stat.S_ISCHR(st.st_mode):
        return "", "not a character device"
    return f"serial.dev.{os.major(st.st_rdev)}:{os.minor(st.st_rdev)}", ""


def gpsd_devices(host: str, port: int, timeout: float = 3.0) -> tuple[list, str]:
    """(device paths gpsd reports, error). Used to refuse direct-NMEA on a device gpsd
    already owns — opening it behind gpsd's back yields two readers fighting over one
    receiver, which presents as intermittent position loss rather than a clean failure."""
    import json
    import socket
    try:
        with socket.create_connection((host, port), timeout) as s:
            s.settimeout(timeout)
            s.sendall(b'?DEVICES;\n')
            buf = b""
            deadline_reads = 40
            while deadline_reads > 0:
                deadline_reads -= 1
                chunk = s.recv(4096)
                if not chunk:
                    break
                buf += chunk
                for raw in buf.split(b"\n"):
                    line = raw.strip()
                    if not line.startswith(b"{"):
                        continue
                    try:
                        msg = json.loads(line)
                    except ValueError:
                        continue
                    if msg.get("class") == "DEVICES":
                        return [d.get("path", "") for d in msg.get("devices", [])
                                if d.get("path")], ""
    except (TimeoutError, OSError) as exc:
        return [], f"{type(exc).__name__}: {exc}"
    return [], "gpsd did not report a DEVICES message"


def gpsd_owns_device(device: str, host: str, port: int) -> tuple[bool | None, str]:
    """(owned, detail). None means "could not be proven either way" — the caller must
    refuse, not assume it is free.

    A REFUSED connection is not an unknown: nothing is listening, so there is no gpsd, so it
    cannot be holding the receiver. Treating that as unprovable would make direct-NMEA mode
    impossible in precisely the situation it exists for — a box running no gpsd at all.
    Anything else (a timeout, a reachable gpsd that never answers `?DEVICES`) stays unknown,
    because there the daemon may well be alive and holding the device.
    """
    key, err = device_lock_key(device)
    if not key:
        return None, err
    paths, err = gpsd_devices(host, port)
    if err:
        if "ConnectionRefused" in err or "Errno 111" in err:
            return False, "no gpsd is listening"
        return None, err
    for p in paths:
        pkey, _ = device_lock_key(p)
        if pkey and pkey == key:
            return True, p
    return False, ""


# QEMU creates this inside the MeshCom source tree (`server=on,wait=off`) and the feed
# CONNECTS to it. Kept in one place so the orientation cannot be re-guessed at a call site.
MESHCOM_SOURCE_REL = ("src", "meshcom-qemu-raspi", ".run", "gps-uart1.sock")


def bridge_state_dir(runtime_root, consumer: str) -> str:
    """Where the feed keeps its OWN state (readiness, and the PTY link when it publishes one).

    Always under the runtime root: the managed systemd units are byte-frozen
    (docs/backlog.md), so anything new that must be writable belongs here rather than in a
    unit's ReadWritePaths.
    """
    return str(os.path.join(str(runtime_root), "state", "gps", consumer))


def bridge_endpoint_path(runtime_root, consumer: str) -> str:
    """The endpoint the feed uses for `consumer` — note the two are NOT symmetrical.

    * Meshtastic: a PTY we CREATE and publish, because meshtasticd opens a serial device.
    * MeshCom: QEMU's own UART1 server socket, which we CONNECT to. QEMU is the server; a
      feed that listened would publish a socket nothing ever connects to — healthy-looking
      and completely inert.
    """
    if _OUTPUT_FOR.get(consumer, OUT_PTY) == OUT_UNIX:
        return str(os.path.join(str(runtime_root), *MESHCOM_SOURCE_REL))
    return str(os.path.join(bridge_state_dir(runtime_root, consumer), "nmea0"))


def redact(text: str) -> str:
    """Strip anything position-shaped from text bound for a log or an error message.

    Coordinates are the operator's location. They must never reach a log file, a status
    line, or an exception — including indirectly via a raw NMEA sentence or gpsd JSON.
    """
    import re
    if not text:
        return text
    out = re.sub(r"\$G[A-Z]{3,4}[^\r\n]*", "<nmea redacted>", text)
    out = re.sub(r'"lat"\s*:\s*-?\d+(\.\d+)?', '"lat":<redacted>', out)
    out = re.sub(r'"lon"\s*:\s*-?\d+(\.\d+)?', '"lon":<redacted>', out)
    return out
