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
import re as _re
import threading as _threading
import time
from dataclasses import dataclass

# Consumers that can be fed a device-shaped NMEA stream by the bridge. Each gets its OWN
# bridge instance: no shared process, no reference counting, and the two want different
# output shapes anyway (a PTY for meshtasticd, a UNIX socket for MeshCom's QEMU UART1).
CONSUMER_MESHTASTIC = "meshtastic"
CONSUMER_MESHCOM = "meshcom"
CONSUMER_GRAYWOLF = "graywolf"
CONSUMER_MESHCORE = "meshcore"

# How the bridge hands the position to a consumer.
OUT_PTY = "pty"
OUT_UNIX = "unix"
# Normalized line-JSON position feed served on a Unix socket the consumer connects to:
# {"fix": true, "lat": .., "lon": ..} / {"fix": false}. MeshCore's openHop host consumes
# this — the consumer needs a position, not a
# simulated GPS chip, so no probe-drain complexity and no NMEA parsing on its side.
OUT_POSJSON = "posjson"

_OUTPUT_FOR = {CONSUMER_MESHTASTIC: OUT_PTY, CONSUMER_MESHCOM: OUT_UNIX,
               CONSUMER_MESHCORE: OUT_POSJSON}

# consumer -> the manifest component that carries its production feed. THE one mapping:
# every site that needs it derives from here.
FEED_COMPONENTS = {CONSUMER_MESHTASTIC: "meshtastic-gps",
                   CONSUMER_MESHCOM: "meshcom-gps",
                   CONSUMER_MESHCORE: "meshcore-gps"}


def consumer_for_component(component_id: str) -> str:
    """The GPS consumer a feed component serves, or "" when it is not a feed."""
    return next((c for c, cid in FEED_COMPONENTS.items() if cid == component_id), "")


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

        MeshCore needs one for every LIVE source: the pinned node reads NMEA from a device
        path and knows nothing about gpsd, so a bridge is the only way its position follows
        the box while it runs. For `fixed` it needs none — the coordinates go straight into
        its config, which is simpler and has nothing to keep alive.
        """
        if not self.enabled:
            return False
        if consumer == CONSUMER_MESHTASTIC:
            return self.source == "gpsd"
        if consumer == CONSUMER_MESHCORE:
            return not self.is_fixed
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


def graywolf_post_step_values(plan: GpsPlan) -> dict:
    """Controller-owned values for graywolf's GPS provisioning step.

    graywolf needs NO bridge: it speaks gpsd natively (host/port) and reads a serial NMEA
    device natively, so the plan maps straight onto its own `/api/gps` settings. That is why
    there is no `graywolf-gps` component next to `meshtastic-gps`/`meshcom-gps`.

    Applied in BOTH directions, for the same reason meshtastic's `gps_mode` is: turning the
    global source off (or a stack opting out) must actively push `none`, or a station enabled
    earlier keeps its old source and goes on reporting a position from it.

    `fixed` maps to `none`, not to a synthesized stream: graywolf's GPS subsystem has no
    fixed-position mode, and a station's fixed position belongs to its beacons, which are the
    operator's to set — LHPC must not invent coordinates.
    """
    if plan.source == "gpsd" and plan.host and plan.port:
        return {"gps_args": ["--gps-source", "gpsd",
                             "--gps-host", str(plan.host), "--gps-port", str(plan.port)]}
    if plan.source == "nmea" and plan.device:
        args = ["--gps-source", "serial", "--gps-device", str(plan.device)]
        if plan.nmea_baud:
            args += ["--gps-baud", str(plan.nmea_baud)]
        return {"gps_args": args}
    return {"gps_args": ["--gps-source", "none"]}


USE_GPS_PARAM = "use_gps"


def use_gps_default(stacks, stack_id: str) -> str:
    """The manifest-declared default of a stack's `use_gps` switch ("on"/"off"; "off" for a
    stack without the param). Saved config knows only what was SAVED — an untouched box must
    follow the manifest default, which is "on" (the global source defaults to `auto`)..
    Shared by the service and Lifecycle so their answers cannot diverge."""
    for s in stacks:
        if s.id != stack_id:
            continue
        for c in s.components:
            for p in c.run_params:
                if p.name == USE_GPS_PARAM:
                    return str(p.default or "off").strip().lower()
    return "off"


# Where `auto` looks, and nowhere else: a remote gpsd or a serial device is an explicit
# operator decision, never auto-discovered.
AUTO_GPSD_HOST = "127.0.0.1"
AUTO_GPSD_PORT = 2947

# The controller hands its own auto verdict to the GPS-bridge process it launches (see
# `Lifecycle.start`), so one applied start cannot resolve `auto` twice across the process
# boundary. The value is a bare verdict ("gpsd"/"off"), never a host or port — the bridge
# still derives every endpoint from code and `[gps]`, keeping its no-handed-in-values rule.
AUTO_ENV = "LHPC_GPS_AUTO_RESOLVED"

# /proc/net/tcp local_address values (hex, kernel byte order) that make the plan's
# advertised endpoint — 127.0.0.1:2947 — actually reachable.
_V4_LOOPBACK = "0100007F"
_V4_ANY = "00000000"


def _tcp4_shows_local_gpsd(text: str) -> bool:
    """Does one /proc/net/tcp dump show a listener REACHABLE at 127.0.0.1:2947?

    Matching only state+port also matched an ::1-only, a 192.168.x-bound, or a
    non-gpsd 2947 listener — and the plan then told every consumer to dial 127.0.0.1:2947,
    where nothing listened: the soft "no gpsd → run without position" promise turned into
    GPS-feed start failures. Only 127.0.0.1 and the IPv4 wildcard are provably that
    endpoint. IPv6-only listeners are deliberately NOT counted (v6-wildcard reachability
    depends on bindv6only, which /proc does not show); missing one fails SOFT — no
    position — never a wrong endpoint.
    """
    port_hex = f"{AUTO_GPSD_PORT:04X}"
    for line in text.splitlines()[1:]:
        parts = line.split()
        # local_address is field 1 ("addr:port" hex), state is field 3 (0A = LISTEN)
        if len(parts) > 3 and parts[3] == "0A":
            addr, _, port = parts[1].rpartition(":")
            if port == port_hex and addr in (_V4_LOOPBACK, _V4_ANY):
                return True
    return False


def local_gpsd_listening() -> bool:
    """Is a gpsd endpoint LISTENING at 127.0.0.1:2947 right now?

    PASSIVE — parses /proc/net/tcp, never opens a connection (a probe connection per config
    load would spam a real gpsd's log). "Answers but owns no receiver" deliberately counts
    as listening: consumers wait for a fix natively, so a receiver plugged in later starts
    working without a stack restart; the doctor diagnoses a receiver-less gpsd.

    Called ONCE per config load (`load_config` resolves `auto` into `GpsConfig`), so every
    consumer of one loaded config shares one frozen verdict — there is deliberately no
    time-based cache here. Tests monkeypatch THIS function.
    """
    try:
        with open("/proc/net/tcp", encoding="ascii", errors="replace") as fh:
            return _tcp4_shows_local_gpsd(fh.read())
    except OSError:
        return False


def plan_from_config(cfg, *, resolve_device=True, auto_hint: bool | None = None) -> GpsPlan:
    """Resolve `[gps]` into the effective plan. Pure with respect to the config: it reads
    the filesystem only to resolve a device's identity, and a failure there is reported,
    never guessed.

    `auto` resolves in the one shared resolver, from a verdict FROZEN at config-load time
    (`GpsConfig.auto_listening`), so every consumer of one loaded config — run order,
    claims, rendering, post-steps — sees the same answer even if gpsd starts or stops
    mid-operation. `auto_hint` overrides it: the bridge process passes the CONTROLLER's
    verdict through (see `AUTO_ENV`), so one applied start is one decision across the
    process boundary too. A listening gpsd becomes an ordinary gpsd plan; nothing listening
    becomes "off" with a reason — soft, because a DEFAULT must never refuse a start.
    """
    g = getattr(cfg, "gps", None)
    if g is None:
        return GpsPlan()
    if not getattr(g, "valid", True):
        return GpsPlan(source="off", valid=False, reason=g.reason)
    if not g.enabled:
        return GpsPlan()
    if g.source == "auto":
        listening = auto_hint
        if listening is None:
            listening = getattr(g, "auto_listening", None)
        if listening is None:                      # a hand-built GpsConfig — probe once
            listening = local_gpsd_listening()
        if listening:
            return GpsPlan(source="gpsd", host=AUTO_GPSD_HOST, port=AUTO_GPSD_PORT)
        return GpsPlan(source="off", valid=True,
                       reason=f"auto: no gpsd on this box "
                              f"({AUTO_GPSD_HOST}:{AUTO_GPSD_PORT}) — running without position")

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
    receiver, which presents as intermittent position loss rather than a clean failure.

    `timeout` is the TOTAL budget for the whole exchange, connect included (never a per-`recv`
    timeout: across up to 40 reads a chatty-but-unhelpful gpsd would hold the caller for 40x the
    number given, and `doctor` promises a bounded check).
    """
    import json
    import socket
    import time as _time
    deadline = _time.monotonic() + max(0.05, timeout)

    def _left() -> float:
        return deadline - _time.monotonic()

    try:
        with socket.create_connection((host, port), timeout) as s:
            s.sendall(b'?DEVICES;\n')
            buf = b""
            while _left() > 0:
                s.settimeout(_left())
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
def gpsd_owns_device(device: str, host: str, port: int,
                     timeout: float = 3.0) -> tuple[bool | None, str]:
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
    paths, err = gpsd_devices(host, port, timeout=timeout)
    if err:
        if "ConnectionRefused" in err or "Errno 111" in err:
            return False, "no gpsd is listening"
        return None, err
    unresolved: list[str] = []
    for p in paths:
        pkey, _ = device_lock_key(p)
        if pkey and pkey == key:
            return True, p
        # A reported LOCAL device path we cannot resolve is not evidence of freedom: gpsd
        # opens the path it was given, and that path can be a symlink that was repointed or
        # removed while the receiver stays open. Answering "free" here (as this once did) let a
        # sampler open a receiver gpsd still held. A non-path source (`tcp://`, `udp://`,
        # `gpsd://`, a pty name) is not this device and is skipped.
        if not pkey and p.startswith("/"):
            unresolved.append(p)
    if unresolved:
        return None, (f"gpsd reports {unresolved[0]}, which cannot be identified — cannot "
                      "establish that the device is free")
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
    kind = _OUTPUT_FOR.get(consumer, OUT_PTY)
    if kind == OUT_UNIX:
        return str(os.path.join(str(runtime_root), *MESHCOM_SOURCE_REL))
    if kind == OUT_POSJSON:
        # A server socket WE publish in our own state dir; the consumer connects.
        return str(os.path.join(bridge_state_dir(runtime_root, consumer), "position.sock"))
    return str(os.path.join(bridge_state_dir(runtime_root, consumer), "nmea0"))


# A GPS feed refreshes its readiness marker every ~10 s; anything older is not this run.
MARKER_MAX_AGE_S = 60.0


def marker_is_fresh(got: dict, now: float, max_age: float = MARKER_MAX_AGE_S) -> bool:
    """Was this readiness marker refreshed within `max_age` seconds of `now`? A missing,
    non-numeric or non-positive `updated` is never fresh."""
    try:
        updated = float(got.get("updated", 0) or 0)
    except (TypeError, ValueError):
        return False
    return not (updated <= 0 or (now - updated) > max_age)


def marker_owner_pid(got: dict) -> int | None:
    """The feed pid a readiness marker names, or None. The pid is REQUIRED, not a bonus: the
    bridge writes it on every refresh, so a marker without one is not from a feed we are running.
    `bool` is an `int` in Python — True would otherwise sail through as "pid 1", always alive."""
    pid = got.get("pid")
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return None
    return pid


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


# =============================================================================== NMEA classifier
# Moved here from gps_bridge.py (which imports this module) so the monitor parser below and
# the bridge share ONE classifier. Behaviour byte-identical to the bridge's original.

_NAV_SENTENCES = (b"GGA", b"RMC", b"GLL", b"GNS")


def nmea_checksum_ok(line: bytes) -> bool:
    """Does `line` carry a well-formed `*HH` checksum that matches its payload?

    Readiness is a claim about the SOURCE, so it must not be built on corrupt input: a garbled
    line can present any flag value at all. Sentences without a checksum are rejected too —
    every navigation sentence a receiver emits has one.
    """
    if not line.startswith(b"$") or b"*" not in line:
        return False
    body, _, tail = line[1:].partition(b"*")
    if len(tail) < 2:
        return False
    try:
        want = int(tail[:2], 16)
    except ValueError:
        return False
    got = 0
    for b in body:
        got ^= b
    return got == want


def _coords_present(f: list, lat_i: int) -> bool:
    """Are the lat/lon fields populated with plausible hemispheres? (Never parsed as numbers —
    the bridge must not handle coordinates, only notice that they exist.)"""
    try:
        lat, ns, lon, ew = f[lat_i], f[lat_i + 1], f[lat_i + 2], f[lat_i + 3]
    except IndexError:
        return False
    return bool(lat.strip() and lon.strip()
                and ns.strip().upper() in (b"N", b"S")
                and ew.strip().upper() in (b"E", b"W"))


def classify_sentence(line: bytes) -> tuple[bool, bool]:
    """(is_navigation, has_fix) for one NMEA line.

    `is_navigation` — a checksum-valid GGA/RMC/GLL/GNS with a legal status field. This is what
    "the receiver is talking to us" means; a `$GPTXT` banner is NOT navigation traffic, and a
    receiver stuck in UBX binary mode emits exactly that and nothing else.

    `has_fix` — the same, and its status says the fix is usable AND the coordinate fields are
    populated. An RMC can be flagged `A` with empty coordinates; that is not a position.
    """
    if not nmea_checksum_ok(line) or len(line) < 7:
        return False, False
    kind = line[3:6]
    if kind not in _NAV_SENTENCES:
        return False, False
    f = line.split(b",")
    try:
        if kind == b"GGA":
            q = f[6].strip()
            if not q.isdigit() or int(q) > 8:       # legal quality indicators are 0..8
                return False, False
            return True, q != b"0" and _coords_present(f, 2)
        if kind == b"RMC":
            st = f[2].strip().upper()
            if st not in (b"A", b"V"):
                return False, False
            return True, st == b"A" and _coords_present(f, 3)
        if kind == b"GLL":
            st = f[6].strip().upper()
            if st not in (b"A", b"V"):
                return False, False
            return True, st == b"A" and _coords_present(f, 1)
        # GNS: one mode character per constellation; N = no fix from that one.
        mode = f[6].strip().upper()
        if not mode or not all(c in b"NAEDFMPRS" for c in mode):
            return False, False
        return True, any(c not in b"N" for c in mode) and _coords_present(f, 2)
    except IndexError:
        return False, False                          # truncated -> not usable evidence


def carries_position(line: bytes) -> bool:
    """True when the sentence carries a VALID position. See `classify_sentence`."""
    return classify_sentence(line)[1]


# ================================================================== the NMEA snapshot parser
# ONE parser for both places that watch a direct receiver: the bridge's monitor socket (a
# `source=nmea` feed that owns the device) and the console's one bounded idle sample. Pure —
# no I/O, one lock — and it never raises out of `feed()`: malformed input is counted, not
# thrown, so a monitor can never take a feed down (docs/gps.md, "Monitor").

NMEA_STALE_S = 20.0            # the bridge's readiness boundary; every retained value ages at it
NMEA_TAIL_LINES = 22           # the console's fixed 22-line pane
_NMEA_MAX_GSV_GROUPS = 32      # bounded retained state: (talker, signal) groups
_NMEA_MAX_SATS_PER_GROUP = 64

_STATE_LABELS = {
    "3d": "3D fix", "2d": "2D fix", "fix": "fix (dimension unknown)", "no-fix": "no fix",
    "stale": "stale — no navigation data for a while", "no-data": "no NMEA data",
    "no-device": "no gpsd device", "gpsd-no-data": "gpsd device present, no position data yet",
    "ambiguous": "several position sources", "unavailable": "gpsd unavailable",
    "fixed": "fixed position (configured)", "off": "no position source",
    "auto-off": "auto: no gpsd on this box — no position", "held": "held",
    "via-feed": "via the GPS feed",
}


def monitor_label(state: str) -> str:
    return _STATE_LABELS.get(state, state)


def _finite(x) -> bool:
    import math
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def _nmea_coord(value: bytes, hemi: bytes, is_lat: bool) -> float | None:
    """ddmm.mmmm / dddmm.mmmm with hemisphere -> signed decimal degrees, validated: finite,
    minutes < 60, |lat| <= 90, |lon| <= 180. None for anything else."""
    try:
        text = value.strip().decode("ascii")
        head = 2 if is_lat else 3
        # fixed width, digits only: ddmm.m / dddmm.m. The hemisphere carries the sign, so a
        # signed, spaced or short degree prefix is a malformed field — it must never parse as a
        # different, plausible coordinate
        if not _re.fullmatch(rf"\d{{{head + 2}}}(\.\d*)?", text):
            return None
        deg = int(text[:head])
        minutes = float(text[head:])
    except (ValueError, UnicodeDecodeError):
        return None
    if not _finite(minutes) or minutes < 0 or minutes >= 60:
        return None
    val = deg + minutes / 60.0
    h = hemi.strip().upper()
    if h in (b"S", b"W"):
        val = -val
    elif h not in (b"N", b"E"):
        return None
    limit = 90.0 if is_lat else 180.0
    return val if _finite(val) and -limit <= val <= limit else None


def _nmea_float(field: bytes) -> float | None:
    try:
        v = float(field.strip().decode("ascii"))
    except (ValueError, UnicodeDecodeError):
        return None
    return v if _finite(v) else None


def _nmea_int(field: bytes) -> int | None:
    t = field.strip()
    return int(t) if t.isdigit() else None


def _nmea_time(field: bytes) -> str | None:
    t = field.strip().decode("ascii", "replace")
    if len(t) < 6 or not t[:6].isdigit():
        return None
    hh, mm, ss = int(t[:2]), int(t[2:4]), int(t[4:6])
    if hh > 23 or mm > 59 or ss > 60:
        return None
    return f"{hh:02d}:{mm:02d}:{ss:02d}"


def _nmea_date(field: bytes) -> str | None:
    t = field.strip().decode("ascii", "replace")
    if len(t) != 6 or not t.isdigit():
        return None
    dd, mm, yy = int(t[:2]), int(t[2:4]), int(t[4:6])
    if not (1 <= dd <= 31 and 1 <= mm <= 12):
        return None
    return f"20{yy:02d}-{mm:02d}-{dd:02d}"


class NmeaSnapshot:
    """Current receiver state from a stream of NMEA sentences, with per-value provenance and age.

    Validity comes from the NAVIGATION sentences exactly as `classify_sentence()` decides it;
    GSA only refines the dimension (2D/3D) and supplies the satellites in use; GSV supplies the
    sky. Every retained value has its own monotonic timestamp and expires at `NMEA_STALE_S`, so
    a value that stopped being reported disappears instead of living forever in a long-lived
    bridge. A navigation sentence WITHOUT a fix clears the position and altitude at once. A
    "fix"-status sentence whose coordinates fail numeric validation counts as no fix — the
    source asserted a position that is unusable.
    """

    def __init__(self, clock=None):
        import collections
        import threading
        self._clock = clock or time.monotonic
        self._lock = threading.Lock()
        self.errors = 0
        self._last_nav_at: float | None = None
        self._has_fix = False
        self._lat = self._lon = None
        self._alt = None
        self._alt_at: float | None = None
        self._sats_used = None
        self._sats_used_at: float | None = None
        self._time = None
        self._time_at: float | None = None
        self._date = None
        self._date_at: float | None = None
        self._gsa: dict = {}          # system/talker -> {"mode", "used", "at"}
        self._gsv_partial: dict = {}  # (talker, signal) -> {"total", "seen", "sats", "at"}
        self._gsv: dict = {}          # (talker, signal) -> {"sats", "at"}
        self._tail = collections.deque(maxlen=NMEA_TAIL_LINES)
        self.raw_bytes = 0
        self.sentences = 0

    # ---- input ----------------------------------------------------------------------------
    def feed(self, line: bytes) -> None:
        """One line (with or without the trailing CR/LF). Never raises."""
        try:
            with self._lock:
                self._feed(line.rstrip(b"\r\n"))
        except Exception:               # a parser bug must never take a feed down
            self.errors += 1

    def _feed(self, line: bytes) -> None:
        self.raw_bytes += len(line) + 1
        if len(line) < 7 or not nmea_checksum_ok(line):
            return
        self.sentences += 1
        self._tail.append(line.decode("ascii", "replace"))
        now = self._clock()
        talker, kind = line[1:3], line[3:6]
        f = line.split(b",")
        if kind in _NAV_SENTENCES:
            self._nav(kind, f, now)
        elif kind == b"GSA":
            self._gsa_sentence(talker, f, now)
        elif kind == b"GSV":
            self._gsv_sentence(talker, f, now)

    def _nav(self, kind: bytes, f: list, now: float) -> None:
        is_nav, has_fix = classify_sentence(b",".join(f))
        if not is_nav:
            return
        self._last_nav_at = now
        lat = lon = None
        if has_fix:
            idx = {b"GGA": 2, b"RMC": 3, b"GLL": 1, b"GNS": 2}[kind]
            lat = _nmea_coord(f[idx], f[idx + 1], True)
            lon = _nmea_coord(f[idx + 2], f[idx + 3], False)
            if lat is None or lon is None:
                has_fix = False             # asserted, unusable -> no fix
        if has_fix:
            self._has_fix, self._lat, self._lon = True, lat, lon
        else:
            self._has_fix, self._lat, self._lon = False, None, None
            self._alt, self._alt_at = None, None
        # time-of-day: GGA 1, RMC 1, GLL 5, GNS 1; RMC also carries the date (field 9)
        t_idx = 5 if kind == b"GLL" else 1
        t = _nmea_time(f[t_idx]) if len(f) > t_idx else None
        if t is not None:
            self._time, self._time_at = t, now
        if kind == b"RMC" and len(f) > 9:
            d = _nmea_date(f[9])
            if d is not None:
                self._date, self._date_at = d, now
        # MSL altitude and satellites in use: GGA fields 9/7, GNS fields 9/7 — only when supplied
        if kind in (b"GGA", b"GNS"):
            if has_fix and len(f) > 9:
                alt = _nmea_float(f[9])
                if alt is not None:
                    self._alt, self._alt_at = alt, now
            if len(f) > 7:
                n = _nmea_int(f[7])
                if n is not None:
                    self._sats_used, self._sats_used_at = n, now

    def _gsa_sentence(self, talker: bytes, f: list, now: float) -> None:
        if len(f) < 3:
            return
        mode = _nmea_int(f[2])
        if mode not in (1, 2, 3):
            return
        used = set()
        for field in f[3:15]:
            prn = _nmea_int(field)
            if prn is not None:
                used.add(prn)
        # NMEA 4.10+: an 18th field (index 18) is the system id; without it, key on the talker
        sys_id = _nmea_int(f[18].split(b"*")[0]) if len(f) > 18 else None
        key = f"sys{sys_id}" if sys_id is not None else talker.decode("ascii", "replace")
        self._gsa[key] = {"mode": mode, "used": used, "at": now}

    def _gsv_sentence(self, talker: bytes, f: list, now: float) -> None:
        if len(f) < 4:
            return
        total, num, count = _nmea_int(f[1]), _nmea_int(f[2]), _nmea_int(f[3].split(b"*")[0])
        if not total or not num or count is None or num > total:
            return
        body = f[4:]
        if body:
            body[-1] = body[-1].split(b"*")[0]
        # NMEA 4.10+: one extra trailing field is the signal id
        sig = None
        if body and len(body) % 4 == 1:
            sig = _nmea_int(body[-1])
            body = body[:-1]
        key = (talker.decode("ascii", "replace"), sig)
        part = self._gsv_partial.get(key)
        if num == 1 or part is None or part["total"] != total:
            if num != 1:
                self._gsv_partial.pop(key, None)     # a fragment of a set we never saw start
                return
            if len(self._gsv_partial) >= _NMEA_MAX_GSV_GROUPS and key not in self._gsv_partial:
                return
            part = self._gsv_partial[key] = {"total": total, "seen": set(), "sats": {}, "at": now}
        for i in range(0, len(body) - 3, 4):
            prn = _nmea_int(body[i])
            if prn is None or len(part["sats"]) >= _NMEA_MAX_SATS_PER_GROUP:
                continue
            el, az, ss = _nmea_float(body[i + 1]), _nmea_float(body[i + 2]), _nmea_float(body[i + 3])
            if el is not None and not -90.0 <= el <= 90.0:
                el = None
            if az is not None:
                az = az % 360.0
            if ss is not None and ss < 0:
                ss = None
            part["sats"][prn] = {"prn": prn, "el": el, "az": az, "ss": ss}
        part["seen"].add(num)
        part["at"] = now
        if part["seen"] == set(range(1, total + 1)):
            self._gsv[key] = {"sats": part["sats"], "at": now}
            del self._gsv_partial[key]

    # ---- output ---------------------------------------------------------------------------
    def snapshot(self) -> dict:
        with self._lock:
            return self._snapshot(self._clock())

    def _fresh(self, at: float | None, now: float) -> bool:
        return at is not None and (now - at) <= NMEA_STALE_S

    def _snapshot(self, now: float) -> dict:
        out = {"ok": True, "error": "", "state": "no-data", "mode": None,
               "lat": None, "lon": None, "alt": None, "alt_kind": None,
               "time": None, "time_has_date": False, "sats_used": None, "sats_seen": None,
               "satellites": [], "nmea": list(self._tail), "nav_age": None,
               "raw_bytes": self.raw_bytes, "sentences": self.sentences}
        # expire incomplete assemblies and old groups
        for key in [k for k, v in self._gsv_partial.items() if not self._fresh(v["at"], now)]:
            del self._gsv_partial[key]
        gsa = {k: v for k, v in self._gsa.items() if self._fresh(v["at"], now)}
        gsv = {k: v for k, v in self._gsv.items() if self._fresh(v["at"], now)}
        if self._last_nav_at is None:
            pass                              # no navigation yet: the sky below still counts
        elif not self._fresh(self._last_nav_at, now):
            out["state"] = "stale"
        elif not self._has_fix:
            out["state"] = "no-fix"
            out["mode"] = 1
        else:
            modes = [v["mode"] for v in gsa.values()]
            if 3 in modes:
                out["state"], out["mode"] = "3d", 3
            elif 2 in modes:
                out["state"], out["mode"] = "2d", 2
            else:
                out["state"], out["mode"] = "fix", None
            out["lat"], out["lon"] = self._lat, self._lon
            if self._fresh(self._alt_at, now) and self._alt is not None:
                out["alt"], out["alt_kind"] = self._alt, "msl"
        if self._last_nav_at is not None:
            out["nav_age"] = round(now - self._last_nav_at, 1)
        if self._fresh(self._sats_used_at, now):
            out["sats_used"] = self._sats_used
        if self._fresh(self._time_at, now):
            out["time"] = self._time
            if self._fresh(self._date_at, now) and self._date:
                out["time"] = f"{self._date} {self._time}"
                out["time_has_date"] = True
        # sky: dedupe by (talker, prn) across signal sets, strongest SNR wins
        talkers = {k[0] for k in gsv}
        sats: dict = {}
        for (talker, _sig), grp in gsv.items():
            for prn, sat in grp["sats"].items():
                cur = sats.get((talker, prn))
                if cur is None or (sat["ss"] or -1) > (cur["ss"] if cur["ss"] is not None else -1):
                    sats[(talker, prn)] = dict(sat)
        for (talker, prn), sat in sats.items():
            sat["used"] = _used_verdict(talker, prn, gsa, talkers)
            sat["talker"] = talker
        out["satellites"] = sorted(sats.values(), key=lambda s: (s["talker"], s["prn"]))
        if gsv:
            out["sats_seen"] = len(out["satellites"])
        # a cold receiver: satellites reported, no navigation sentence yet — that is "no fix",
        # not "no NMEA data" (the sky IS NMEA data)
        if out["state"] == "no-data" and out["satellites"]:
            out["state"] = "no-fix"
        return out


# GSV talker -> NMEA 4.11 GSA system id. Satellite numbers are per constellation, so a GSA
# used-set applies only to the constellation it names (GP 4 used says nothing about GA 4).
_SYS_OF_TALKER = {"GP": 1, "GL": 2, "GA": 3, "GB": 4, "BD": 4, "GQ": 5, "GI": 6}


def _used_verdict(talker: str, prn: int, gsa: dict, talkers: set) -> bool | None:
    """Whether (talker, prn) is in the fix, from the fresh GSA sets: the set keyed by this
    talker's system id, else the one keyed by the talker itself; with per-system sets present and
    none for this system, not used; with only a combined (GN) set and a single constellation in
    view, that set; anything else is unknown (None), never a confident wrong answer."""
    if not gsa:
        return None
    sys_id = _SYS_OF_TALKER.get(talker)
    if sys_id is not None and f"sys{sys_id}" in gsa:
        return prn in gsa[f"sys{sys_id}"]["used"]
    if talker in gsa:
        return prn in gsa[talker]["used"]
    if any(k.startswith("sys") for k in gsa):
        return False
    if len(talkers) == 1:
        return any(prn in v["used"] for v in gsa.values())
    return None


# ========================================================================= gpsd JSON readers

GPSD_PROTO_MAJOR = 3                  # the JSON API major this reader understands
_GPSD_MAX_LINE = 16384                # a report may run to 10 240 chars; longer is not a report
_GPSD_MAX_BUFFER = 65536
_resolving: set = set()               # hosts with a name resolution still pending (bounded workers)
_resolving_lock = _threading.Lock()   # membership test + add + discard are one step


def _gpsd_connect(host: str, port: int, deadline: float):
    """Connect under one ABSOLUTE deadline: resolution (with a bounded worker, since
    `getaddrinfo` has no timeout), then every address attempt. Returns (sock, error)."""
    import socket
    import threading
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        return None, "budget exhausted before connecting"
    key = f"{host}:{port}"
    result: list = []

    def _resolve():
        try:
            result.append(socket.getaddrinfo(host, port, type=socket.SOCK_STREAM))
        except OSError as exc:
            result.append(exc)
        finally:
            with _resolving_lock:
                _resolving.discard(key)   # the worker retires itself, however late
    with _resolving_lock:
        if key in _resolving:
            return None, "name resolution still pending from an earlier request"
        _resolving.add(key)
    t = threading.Thread(target=_resolve, name="gpsd-resolve", daemon=True)
    t.start()
    t.join(remaining)
    if t.is_alive():
        return None, "name resolution timed out"      # no second worker while this one lingers
    infos = result[0] if result else []
    if isinstance(infos, Exception):
        return None, f"{type(infos).__name__}: {infos}"
    last = "no address"
    for family, stype, proto, _canon, addr in infos:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None, "budget exhausted while connecting"
        s = socket.socket(family, stype, proto)
        s.settimeout(remaining)
        try:
            s.connect(addr)
            return s, ""
        except OSError as exc:
            last = f"{type(exc).__name__}: {exc}"
            s.close()
    return None, last


def _gpsd_lines(host: str, port: int, request: bytes, deadline: float):
    """Yield (kind, payload) from gpsd until the deadline: kind "json" with a parsed object,
    "nmea" with a `$` line, or "error" with a message (final). Newline-framed with a bounded
    partial buffer; an overlong partial and malformed or truncated JSON are dropped."""
    import json
    sock, err = _gpsd_connect(host, port, deadline)
    if sock is None:
        yield "error", err
        return
    buf = b""
    try:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            yield "error", "budget exhausted before the request was sent"
            return
        sock.settimeout(remaining)
        sock.sendall(request)
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            sock.settimeout(remaining)
            try:
                chunk = sock.recv(8192)
            except TimeoutError:
                return
            except OSError as exc:
                yield "error", f"{type(exc).__name__}: {exc}"
                return
            if not chunk:
                yield "error", "gpsd closed the connection"
                return
            buf += chunk
            if len(buf) > _GPSD_MAX_BUFFER:
                buf = b""                      # a stream without newlines is not gpsd
                continue
            lines = buf.split(b"\n")
            buf = lines.pop()
            if len(buf) > _GPSD_MAX_LINE:
                buf = b""
            for raw in lines:
                line = raw.strip()
                if not line or len(line) > _GPSD_MAX_LINE:
                    continue
                if line.startswith(b"{"):
                    try:
                        msg = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(msg, dict):
                        yield "json", msg
                elif line.startswith(b"$"):
                    yield "nmea", line
    except OSError as exc:
        yield "error", f"{type(exc).__name__}: {exc}"
    finally:
        try:
            sock.close()
        except OSError:
            pass


def _altitude(tpv: dict) -> tuple[float | None, str | None]:
    """altMSL first, altHAE second, the deprecated `alt` last — with its datum named."""
    for field, kind in (("altMSL", "msl"), ("altHAE", "hae"), ("alt", "legacy")):
        v = tpv.get(field)
        if _finite(v):
            return float(v), kind
    return None, None


def gpsd_snapshot(host: str, port: int, timeout: float = 2.5) -> dict:
    """One bounded read of gpsd's JSON stream, collecting the latest coherent per-device state
    for the WHOLE budget (several TPV per epoch are normal; the first may carry no fix).

    Attribution: `device` present -> keyed by it; absent with exactly one listed device ->
    that device; absent with several -> unattributed, never combined with anything, and it
    keeps the ambiguity visible (`nmea_ok` false). Early exit only on `DEVICES=[]` or a
    connection failure. Missing optional fields neither refresh nor clear a value, except that
    losing a usable fix clears lat, lon and altitude.
    """
    deadline = time.monotonic() + max(0.05, timeout)
    out = {"ok": False, "error": "", "state": "unavailable", "devices": [], "device": None,
           "mode": None, "lat": None, "lon": None, "alt": None, "alt_kind": None,
           "time": None, "time_has_date": False, "sats_used": None, "sats_seen": None,
           "satellites": [], "nmea": [], "nmea_ok": True, "note": ""}
    devices: list | None = None
    records: dict = {}                        # device path -> record
    untagged_seen = False
    version_ok = None
    req = b'?WATCH={"enable":true,"json":true}\n'

    def rec_for(msg: dict) -> dict | None:
        dev = msg.get("device")
        if not isinstance(dev, str) or not dev:
            if devices is not None and len(devices) == 1:
                dev = devices[0]
            else:
                return None
        return records.setdefault(dev, {"tpv": False, "mode": None, "lat": None, "lon": None,
                                        "alt": None, "alt_kind": None, "time": None,
                                        "sats_used": None, "sats_seen": None,
                                        "satellites": None})

    for kind, payload in _gpsd_lines(host, port, req, deadline):
        if kind == "error":
            out["error"] = payload
            return out
        if kind != "json":
            continue
        msg = payload
        cls = msg.get("class")
        if cls == "VERSION":
            major = msg.get("proto_major")
            version_ok = major == GPSD_PROTO_MAJOR
            if not version_ok:
                out["error"] = f"unsupported gpsd protocol major {major!r}"
                return out
        elif cls == "DEVICES":
            devs = msg.get("devices")
            devices = [d.get("path") for d in devs if isinstance(d, dict) and d.get("path")] \
                if isinstance(devs, list) else []
            out["devices"] = list(devices)
            if not devices:
                out.update(ok=True, state="no-device")
                return out
        elif cls == "TPV":
            r = rec_for(msg)
            if r is None:
                untagged_seen = True
                continue
            mode = msg.get("mode")
            if isinstance(mode, int) and not isinstance(mode, bool) and 0 <= mode <= 3:
                r["mode"] = mode
                r["tpv"] = True
                if mode < 2:
                    r["lat"] = r["lon"] = r["alt"] = r["alt_kind"] = None
            if r["mode"] is not None and r["mode"] >= 2:
                lat, lon = msg.get("lat"), msg.get("lon")
                if _finite(lat) and _finite(lon) and -90 <= lat <= 90 and -180 <= lon <= 180:
                    r["lat"], r["lon"] = float(lat), float(lon)
                alt, akind = _altitude(msg)
                if alt is not None:
                    r["alt"], r["alt_kind"] = alt, akind
            t = msg.get("time")
            if isinstance(t, str) and t:
                r["time"] = t
        elif cls == "SKY":
            r = rec_for(msg)
            if r is None:
                untagged_seen = True
                continue
            sats_in = msg.get("satellites")
            if isinstance(sats_in, list):
                seen: dict = {}
                for sat in sats_in:
                    if not isinstance(sat, dict):
                        continue
                    prn = sat.get("PRN")
                    el, az, ss = sat.get("el"), sat.get("az"), sat.get("ss")
                    if not isinstance(prn, int) or isinstance(prn, bool):
                        continue
                    el = float(el) if _finite(el) and -90 <= el <= 90 else None
                    az = float(az) % 360.0 if _finite(az) else None
                    ss = float(ss) if _finite(ss) and ss >= 0 else None
                    used = sat.get("used")
                    entry = {"prn": prn, "el": el, "az": az, "ss": ss,
                             "used": used if isinstance(used, bool) else None}
                    cur = seen.get(prn)
                    if cur is None or (ss or -1) > (cur["ss"] if cur["ss"] is not None else -1):
                        seen[prn] = entry
                r["satellites"] = sorted(seen.values(), key=lambda s_: s_["prn"])
                usat, nsat = msg.get("uSat"), msg.get("nSat")
                r["sats_used"] = usat if isinstance(usat, int) and not isinstance(usat, bool) \
                    else sum(1 for s_ in r["satellites"] if s_["used"])
                r["sats_seen"] = nsat if isinstance(nsat, int) and not isinstance(nsat, bool) \
                    else len(r["satellites"])
    if devices is None:
        out["error"] = ("gpsd answered nothing usable in the budget" if version_ok is None
                        else "gpsd sent no DEVICES list in the budget")
        return out
    out["ok"] = True
    with_tpv = [d for d, r in records.items() if r["tpv"]]
    if devices is not None and len(devices) > 1 and untagged_seen:
        out["nmea_ok"] = False
        out["note"] = "position reports without a device tag beside several gpsd devices — unresolved second source"
    if len(with_tpv) > 1:
        out.update(state="ambiguous", nmea_ok=False,
                   note="several position sources: " + ", ".join(sorted(with_tpv)))
        return out
    if not with_tpv:
        if untagged_seen and devices is not None and len(devices) > 1:
            out.update(state="ambiguous")
            return out
        out["state"] = "gpsd-no-data"
        return out
    dev = with_tpv[0]
    r = records[dev]
    out["device"] = dev
    mode = r["mode"]
    out["mode"] = mode
    if mode is None or mode < 2:
        out["state"] = "no-fix"
    else:
        out["state"] = "3d" if mode == 3 else "2d"
        out["lat"], out["lon"] = r["lat"], r["lon"]
        out["alt"], out["alt_kind"] = r["alt"], r["alt_kind"]
    out["time"] = r["time"]
    out["time_has_date"] = bool(r["time"])
    out["sats_used"], out["sats_seen"] = r["sats_used"], r["sats_seen"]
    out["satellites"] = r["satellites"] or []
    return out


def gpsd_nmea_window(host: str, port: int, max_lines: int = NMEA_TAIL_LINES,
                     timeout: float = 2.0) -> list[str]:
    """gpsd's NMEA OUTPUT (pseudo-NMEA for a receiver gpsd runs in binary mode; combined across
    devices) — the bridge's proven `?WATCH nmea`. Checksum-valid `$` sentences only, bounded."""
    deadline = time.monotonic() + max(0.05, timeout)
    lines: list[str] = []
    for kind, payload in _gpsd_lines(host, port, b'?WATCH={"enable":true,"nmea":true}\n',
                                     deadline):
        if kind == "error":
            break
        if kind == "nmea" and nmea_checksum_ok(payload):
            lines.append(payload.decode("ascii", "replace"))
            if len(lines) >= max_lines:
                break
    return lines
