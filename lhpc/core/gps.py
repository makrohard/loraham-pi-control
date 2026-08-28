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
CONSUMER_GRAYWOLF = "graywolf"
CONSUMER_MESHCORE = "meshcore"

# How the bridge hands NMEA to a consumer.
OUT_PTY = "pty"
OUT_UNIX = "unix"

_OUTPUT_FOR = {CONSUMER_MESHTASTIC: OUT_PTY, CONSUMER_MESHCOM: OUT_UNIX,
               CONSUMER_MESHCORE: OUT_PTY}

# consumer -> the manifest component that carries its production feed. THE one mapping:
# every site that needs it derives from here. There used to be four independent copies of
# this knowledge (run-order selection, the start gate's readiness reader, the status
# prober, and the bridge's own accepted-consumer list), and adding a feed to some but not
# all of them produced a component that started, streamed, and was still reported as
# "no readiness marker" forever — healthy and invisible at the same time.
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
    follow the manifest default, which is "on" now that the global source defaults to `auto`.
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

    AUDIT-FOUND: matching only state+port also matched an ::1-only, a 192.168.x-bound, or a
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

    `timeout` is the TOTAL budget for the whole exchange, connect included. It used to be a
    per-`recv` timeout applied across up to 40 reads, so a chatty-but-unhelpful gpsd could hold
    the caller for 40x the number it was given — which is not a bound at all, and `doctor`
    promises a bounded check.
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
