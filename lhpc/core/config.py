"""Layered configuration.

Five concerns, kept strictly separate (see docs/operations.md):

  1. tracked defaults        lhpc/data/defaults.toml        (shipped package data)
  2. known-working compositions  runtime profiles/known-working/ (operator-confirmed)
  3. local operator overrides <runtime>/config/local.toml   (git-ignored, operator settings + callsign)
  4. local secrets           <runtime>/config/secrets.toml  (git-ignored, mode 0600)
  5. generated runtime state  <runtime>/state/              (never sole source of truth)

This module loads and merges layers 1+3 into an effective `Config`, and reads
secrets (layer 4) separately and lazily. It never writes secrets and never emits
them in status output. Callsign and other operator identity live ONLY in the
runtime-local layer, never in the tracked repo.
"""

from __future__ import annotations

import fcntl
import json
import math
import os
import re
import tomllib
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from pathlib import Path

from .assets import asset_path
from .paths import PathContainmentError, Paths

# Tracked defaults shipped with the controller (package data, wheel-safe).
_DEFAULTS_PATH = asset_path("defaults.toml")


class ConfigError(Exception):
    """A config file could not be parsed — surfaced as a diagnostic, never a crash."""


class ConfigLockBusy(ConfigError):
    """The exclusive config lock could not be acquired within the bounded timeout —
    a long-running operation holds it; the mutation should be retried shortly."""


def _atomic_write(paths: Paths, path: Path, text: str, mode: int = 0o644) -> None:
    """Atomically write a RUNTIME-OWNED config leaf THROUGH the safe runtime FS
    (`runtime_fs.atomic_write`): containment, no-follow leaf, parent fsync. Runtime-state
    config writes never bypass `runtime_fs`; source-tree config generation uses a separate
    contained writer in the service layer."""
    from . import runtime_fs
    runtime_fs.atomic_write(paths, path, text, mode)


CONFIG_LOCK_TIMEOUT_S = 15.0
_CONFIG_LOCK_POLL_S = 0.1


@contextmanager
def config_lock(paths: Paths, timeout: float = CONFIG_LOCK_TIMEOUT_S):
    """Serialize config mutations within a runtime root (a single exclusive flock).
    The lock file is opened with O_NOFOLLOW so a symlinked `.lock` leaf is refused,
    and its path is containment-checked; if the lock cannot be acquired safely the
    mutation is blocked (the exception propagates), never silently bypassed.

    BOUNDED acquire (AUDIT CC1): the exclusive lock is polled non-blocking up to
    `timeout`, then raises `ConfigLockBusy`. A blocking `LOCK_EX` here would wedge — a
    auto-install run holds the SHARED config-stability lock for its ENTIRE duration (minutes),
    so a Settings save on one of the web server's fixed thread pool would block that
    thread until the run ended; repeated retries could freeze the whole UI. Failing fast
    with a truthful 'busy, retry shortly' keeps the server responsive."""
    import time as _time

    from . import runtime_fs
    # Single safe API: contained path + O_NOFOLLOW open (a symlinked .lock leaf or an
    # escaping parent raises here, blocking mutation rather than being bypassed).
    fh = runtime_fs.open_lock(paths, paths.under("config", ".lock"))
    deadline = _time.monotonic() + max(0.0, timeout)
    try:
        while True:
            try:
                fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if _time.monotonic() >= deadline:
                    raise ConfigLockBusy(
                        "config is busy — a long-running operation holds it; "
                        "try again shortly") from None
                _time.sleep(_CONFIG_LOCK_POLL_S)
        yield
    finally:
        try:
            fcntl.flock(fh, fcntl.LOCK_UN)
        finally:
            fh.close()


@dataclass(frozen=True)
class OperatorConfig:
    """Operator identity/settings — sourced ONLY from the runtime-local layer."""

    callsign: str = ""

    @property
    def configured(self) -> bool:
        return bool(self.callsign)


# Radio HARDWARE setup: which physical board(s) the box has, and the daemon `--hw` wire preset per
# SERVED band. `unset` (default) = NO hardware configured yet — the daemon refuses to start until the
# operator picks a setup in the daemon Hardware settings. Illegal board combinations (Waveshare+Uputronics,
# two Waveshare) are simply ABSENT from this catalog, so they can never be selected. The daemon's
# `--hw loraham` is our original LoRaHAM_Pi dual-module board (renamed from `legacy`, which the daemon
# removed entirely — a stored `legacy` now fails the daemon's usage check, hence the migration below).
# Each entry: (label, {band: "--hw" wire preset}). Insertion order = UI display order.
HW_SETUPS: dict = {
    "unset": ("Not configured", {}),
    "loraham": ("LoRaHAM dual-module (SX1278 + RFM95)", {"433": "loraham", "868": "loraham"}),
    "uputronics": ("Uputronics dual (CE0 433 + CE1 868)",
                   {"433": "uputronics-ce0", "868": "uputronics-ce1"}),
    "uputronics-x": ("Uputronics dual, crossed modules (CE0 868 + CE1 433)",
                     {"433": "uputronics-ce1", "868": "uputronics-ce0"}),
    "uputronics-433": ("Uputronics 433 (CE0)", {"433": "uputronics-ce0"}),
    "uputronics-868": ("Uputronics 868 (CE1)", {"868": "uputronics-ce1"}),
    "waveshare-433": ("Waveshare SX1262 (433)", {"433": "waveshare-sx1262"}),
    "waveshare-868": ("Waveshare SX1262 (868)", {"868": "waveshare-sx1262"}),
}
HW_DEFAULT = "unset"

# Every daemon `--hw` wire preset the catalog can launch — used to validate a probe request.
HW_PRESETS = frozenset(preset for _label, _map in HW_SETUPS.values() for preset in _map.values())

# Friendly display name per `--hw` wire preset (the raw preset name is never shown in the GUI).
HW_PRESET_LABELS = {
    "loraham": "LoRaHAM",
    "uputronics-ce0": "Uputronics CE0",
    "uputronics-ce1": "Uputronics CE1",
    "waveshare-sx1262": "Waveshare SX1262",
}


def hw_preset_label(preset: str) -> str:
    """Friendly name for a `--hw` wire preset (falls back to the raw value if unknown)."""
    return HW_PRESET_LABELS.get(preset, preset)


@dataclass(frozen=True)
class BootConfig:
    """[boot] — boot auto-restore switch. FAIL-CLOSED like the firewall's ap_enabled (strict
    boolean): an autonomous process-starter must never be enabled by a mistyped value, so a
    non-boolean `restore` yields valid=False and the driver starts NOTHING (the deliberate
    deviation from the fail-soft config convention; precedent: _parse_firewall)."""
    restore: bool = True
    valid: bool = True
    reason: str = ""


def _parse_boot(merged: dict, diagnostics: list) -> BootConfig:
    raw = merged.get("boot")
    if raw is None:
        return BootConfig()
    if not isinstance(raw, dict):
        diagnostics.append(f"[boot] is not a table ({type(raw).__name__}); restore DISABLED")
        return BootConfig(restore=False, valid=False, reason="[boot] is not a table")
    val = raw.get("restore", True)
    if not isinstance(val, bool):
        diagnostics.append(f"non-boolean [boot] restore {val!r}; restore DISABLED (fail closed)")
        return BootConfig(restore=False, valid=False,
                          reason=f"non-boolean [boot] restore {val!r}")
    return BootConfig(restore=val)


# `auto` (the default) is best-effort: it resolves to gpsd when one is listening on
# localhost:2947 and to "no position" otherwise — it never refuses a start. The EXPLICIT
# sources keep fail-closed semantics: an operator who named a source gets a refusal, not a
# silently position-blind stack, when it cannot be used.
GPS_SOURCES = ("off", "auto", "gpsd", "nmea", "fixed")

# Baud rates a POSIX termios port can actually be set to (`termios.B<rate>`). The direct-NMEA
# reader configures the port itself, so an unsupported rate is a CONFIG error, not a runtime one.
GPS_BAUDS = (4800, 9600, 19200, 38400, 57600, 115200)

GPS_DEFAULT_PORT = 2947
GPS_DEFAULT_BAUD = 9600


@dataclass(frozen=True)
class GpsConfig:
    """[gps] — THE authoritative position source for every stack that can use one.

    Per-stack settings may only turn GPS on or off; they are never independent source
    selectors. That is the whole point: one setting, one answer to "where does position
    come from", so two stacks can never disagree about it.

    FAIL-CLOSED, like [boot] and the firewall's ap_enabled: anything malformed yields
    source="off" with a reason, because a half-parsed position source is worse than none
    (a stack would silently beacon a wrong or stale position). `valid` reports whether the
    section parsed cleanly; `source` is already forced to "off" when it did not.

    The DEFAULT (section absent, or `source` unset) is `auto`: use a gpsd if one is
    listening on localhost:2947, otherwise run without position. A default must never
    refuse a start — nobody expressed intent — so `auto` is soft where the explicit
    sources stay fail-closed. Malformed input still yields `off`, never `auto`: broken
    config must not quietly become best-effort.
    """

    source: str = "auto"
    host: str = "127.0.0.1"
    port: int = GPS_DEFAULT_PORT
    device: str = ""
    nmea_baud: int = GPS_DEFAULT_BAUD
    fixed_lat: str = ""
    fixed_lon: str = ""
    fixed_alt: str = ""
    valid: bool = True
    reason: str = ""
    # `auto` ONLY: the probe verdict, resolved ONCE per `load_config` and frozen into this
    # object — so run order, claims, config rendering and post-steps computed from one loaded
    # config can never see two different answers mid-operation. None = not resolved yet (a
    # hand-built GpsConfig); the plan resolver then probes itself, once.
    auto_listening: bool | None = None

    @property
    def enabled(self) -> bool:
        return self.source != "off"

    @property
    def local_gpsd(self) -> bool:
        """A gpsd on THIS box is actually part of the configured position source. Drives the
        gpsd soft dependency: a remote-gpsd operator must never be told to install a local
        package. `auto` counts ONLY when it resolved to a local gpsd (one is listening) —
        "auto might use one" made the dep row appear on every gpsd-less box (CI-found: the
        install gate grew a host-dependent advisory), while an auto that found nothing needs
        no package to run: it IS the no-gpsd path."""
        if self.source == "auto":
            return bool(self.auto_listening)
        return self.source == "gpsd" and _is_loopback_host(self.host)

    @property
    def claims_local_serial(self) -> bool:
        """Only direct NMEA opens a local character device. off / fixed / remote gpsd must
        claim nothing — claiming a device they never touch would refuse valid combinations."""
        return self.source == "nmea" and bool(self.device)


def _is_loopback_host(host: str) -> bool:
    h = (host or "").strip().strip("[]").lower()
    if h in ("localhost", "localhost."):
        return True
    try:
        import ipaddress
        return ipaddress.ip_address(h).is_loopback
    except ValueError:
        return False


def _gps_off(diagnostics: list, reason: str) -> GpsConfig:
    diagnostics.append(f"[gps] {reason}; position source DISABLED (fail closed)")
    return GpsConfig(source="off", valid=False, reason=reason)


def _parse_gps(merged: dict, diagnostics: list) -> GpsConfig:
    raw = merged.get("gps")
    if raw is None:
        return GpsConfig()
    if not isinstance(raw, dict):
        return _gps_off(diagnostics, f"section is not a table ({type(raw).__name__})")

    source = raw.get("source", "auto")
    if not isinstance(source, str) or source.strip().lower() not in GPS_SOURCES:
        return _gps_off(diagnostics, f"unknown source {source!r} (allowed: {', '.join(GPS_SOURCES)})")
    source = source.strip().lower()
    if source == "off":
        # EXPLICIT off — must not fall back to the auto default the bare constructor carries.
        return GpsConfig(source="off")
    if source == "auto":
        # auto probes localhost:2947 only; host/port/device in the section are ignored, not
        # errors — a remote or device source is an explicit decision, never auto-discovered.
        return GpsConfig(source="auto")

    host = raw.get("host", "127.0.0.1")
    if not isinstance(host, str) or not host.strip():
        return _gps_off(diagnostics, f"invalid host {host!r}")
    host = host.strip()

    port = raw.get("port", GPS_DEFAULT_PORT)
    if isinstance(port, bool) or not isinstance(port, int) or not (1 <= port <= 65535):
        return _gps_off(diagnostics, f"invalid port {port!r}")

    device = raw.get("device", "")
    if not isinstance(device, str):
        return _gps_off(diagnostics, f"invalid device {device!r}")
    device = device.strip()

    baud = raw.get("nmea_baud", GPS_DEFAULT_BAUD)
    if isinstance(baud, bool) or not isinstance(baud, int) or baud not in GPS_BAUDS:
        return _gps_off(diagnostics,
                        f"unsupported nmea_baud {baud!r} (allowed: {', '.join(map(str, GPS_BAUDS))})")

    # A direct-NMEA source with no device is not a usable source — the reader would have
    # nothing to open, and "enabled but silently dead" is exactly what fail-closed prevents.
    if source == "nmea":
        if not device:
            return _gps_off(diagnostics, "source = nmea requires a device")
        if not device.startswith("/"):
            return _gps_off(diagnostics, f"device {device!r} must be an absolute path")

    lat, lon, alt = (raw.get("fixed_lat", ""), raw.get("fixed_lon", ""), raw.get("fixed_alt", ""))
    lat, lon, alt = (str(lat).strip() if lat not in (None, "") else "",
                     str(lon).strip() if lon not in (None, "") else "",
                     str(alt).strip() if alt not in (None, "") else "")
    if source == "fixed":
        # COMPLETE and FINITE: a station that reports half a position, or a NaN that survives
        # float(), would beacon nonsense to the mesh.
        ok, why = _finite_position(lat, lon, alt)
        if not ok:
            return _gps_off(diagnostics, why)

    return GpsConfig(source=source, host=host, port=port, device=device, nmea_baud=baud,
                     fixed_lat=lat, fixed_lon=lon, fixed_alt=alt)


def _finite_position(lat: str, lon: str, alt: str) -> tuple[bool, str]:
    """(ok, reason). Altitude is optional; latitude and longitude are not."""
    import math
    if not lat or not lon:
        return False, "source = fixed requires both fixed_lat and fixed_lon"
    try:
        flat, flon = float(lat), float(lon)
    except (TypeError, ValueError):
        return False, "fixed_lat/fixed_lon must be decimal degrees"
    if not (math.isfinite(flat) and math.isfinite(flon)):
        return False, "fixed_lat/fixed_lon must be finite"
    if not (-90.0 <= flat <= 90.0) or not (-180.0 <= flon <= 180.0):
        return False, "fixed_lat must be -90..90 and fixed_lon -180..180"
    if alt:
        try:
            falt = float(alt)
        except (TypeError, ValueError):
            return False, "fixed_alt must be a number (metres)"
        if not math.isfinite(falt):
            return False, "fixed_alt must be finite"
    return True, ""


@dataclass(frozen=True)
class RadioConfig:
    """Radio HARDWARE setup — sourced ONLY from the runtime-local layer. Default `unset` means no
    hardware is configured; the daemon refuses to start until a setup is chosen."""

    hardware: str = HW_DEFAULT

    @property
    def _preset_map(self) -> dict:
        # {band: --hw preset} for the selected setup (empty when unset/unknown).
        return HW_SETUPS.get(self.hardware, HW_SETUPS[HW_DEFAULT])[1]

    @property
    def configured(self) -> bool:
        return self.hardware != "unset" and bool(self._preset_map)

    @property
    def active_bands(self) -> tuple:
        # SERVED bands, ascending. Empty () when unconfigured.
        return tuple(b for b in ("433", "868") if b in self._preset_map)

    def hw_preset(self, band: str) -> str:
        # Daemon `--hw` wire preset for a served band, or "" if this setup does not serve it.
        return self._preset_map.get(band, "")

    @property
    def radio_mode(self) -> str:
        # DERIVED band-mode for dashboard narrowing / labels: both | 433 | 868 | unset.
        bands = self.active_bands
        if len(bands) == 2:
            return "both"
        return bands[0] if bands else "unset"


# Webserver access modes (browser client-certificate authentication policy). There are
# NO user accounts/roles — a client certificate is a named device credential with equal
# full access. See the webserver plan/docs.
WEBSERVER_ACCESS_MODES = (
    "local-open-remote-auth",   # default: loopback open; non-loopback requires a client cert
    "auth-everywhere",          # every client (incl. loopback) requires a client cert
    "no-auth",                  # HTTPS only, no client cert anywhere (dangerous when remote)
)
WEBSERVER_DEFAULT_BIND = "127.0.0.1"
WEBSERVER_DEFAULT_PORT = 8443

# Public listener scheme. NOT the upstream scheme (that comes from the manifest endpoint).
WEBSERVER_SCHEMES = ("https", "http")

# Per-stack web-UI proxy exposure modes.
STACKWEB_MODES = (
    "local",     # nginx listens on 127.0.0.1 only
    "lan",       # listens on the console bind; only the configured CIDRs pass
    "public",    # listens; 0.0.0.0/0 (elevated confirmation)
)
STACKWEB_MIN_PORT = 1024        # rootless nginx cannot bind below this


@dataclass(frozen=True)
class WebserverConfig:
    """DESIRED webserver configuration (config/local.toml `[webserver]`). This is intent
    only — it is NEVER the source of truth for whether the proxy is actually active/exposed;
    that lives in the separate last-proven effective evidence (state/webserver.json). List
    fields are persisted as comma-separated scalar strings (local.toml is flat-scalar) and
    surfaced here as normalized tuples."""

    bind: str = WEBSERVER_DEFAULT_BIND
    port: int = WEBSERVER_DEFAULT_PORT
    access_mode: str = "local-open-remote-auth"
    remote_exposed: bool = False
    allowed_cidrs: tuple = ()
    dns_sans: tuple = ()
    ip_sans: tuple = ()
    server_cert_days: int = 825
    client_cert_days: int = 825
    # The PUBLIC LISTENER scheme. `http` cannot do client-certificate auth at all (a client cert
    # is presented during the TLS handshake), so it forces access_mode="no-auth".
    scheme: str = "https"


@dataclass(frozen=True)
class StackWebConfig:
    """DESIRED reverse-proxy exposure for ONE stack's web UI (`[stackweb]` in local.toml).

    `scheme` is the PUBLIC LISTENER scheme (what a browser speaks to nginx). The UPSTREAM scheme is
    fixed by the manifest endpoint (http for MeshCom :18083, https for Meshtastic :9443) and is never
    operator-settable — conflating the two would drop outside TLS because the inside hop is cleartext.

    `port == 0` means NOT PROXIED: no nginx block is emitted at all, which is the default and keeps a
    fresh deployment's rendered config unchanged."""

    stack_id: str
    mode: str = "local"
    port: int = 0                       # 0 = not proxied
    scheme: str = "https"               # listener scheme
    access_mode: str = "local-open-remote-auth"
    allowed_cidrs: tuple = ()

    @property
    def enabled(self) -> bool:
        return self.port > 0

    @property
    def remote(self) -> bool:
        return self.mode in ("lan", "public")


# `[stackweb]` keys are `<stack_id>_<field>`, and BOTH sides may contain underscores
# (`meshcom_access_mode`, and a future `my_stack_port`). Match by known field suffix,
# LONGEST FIRST, then validate the remaining prefix as a stack id. A naive
# `key.split("_", 1)` reads `meshcom_access_mode` as field "access" and silently drops the
# operator's access mode — the worst failure mode for a security setting.
# Sorted LONGEST FIRST at definition, not by hand: `meshcom_access_mode` ends with `_mode` as well as
# with `_access_mode`, so the order is load-bearing and must not depend on how the tuple was typed.
_STACKWEB_FIELDS = tuple(sorted(
    ("_access_mode", "_allowed_cidrs", "_scheme", "_port", "_mode"), key=len, reverse=True))


def _split_stackweb_key(key: str):
    """(stack_id, field) for a `[stackweb]` key, or None when it matches no known field.

    A stack id that itself ends in a field name (`x_access` + `_mode`) is inherently ambiguous with
    (`x` + `_access_mode`); the longest suffix wins, deterministically."""
    for suffix in _STACKWEB_FIELDS:
        if key.endswith(suffix) and len(key) > len(suffix):
            return key[: -len(suffix)], suffix[1:]
    return None


FIREWALL_MODES = ("secure-default", "compatibility")


@dataclass(frozen=True)
class FirewallConfig:
    """DESIRED managed-firewall configuration (`[firewall]` in local.toml). Intent only —
    the live/persistent truth lives in the root-written receipt, never here. `mode` picks the
    strategy; `allow_endpoints` are the stable endpoint IDs the operator ticked
    "Allow direct access"; AP is strictly opt-in with an explicit interface + CIDR;
    `extra_allow`/`ssh_ports` are advanced escape hatches. Persisted as flat scalars
    (comma-joined lists) like the other sections."""

    mode: str = "secure-default"
    allow_endpoints: tuple = ()
    ssh_ports: tuple = ()
    ap_enabled: bool = False
    ap_interface: str = ""
    ap_cidr: str = ""
    extra_allow: tuple = ()          # tuple of dicts: {proto,family,addr,port,cidr}


@dataclass
class Config:
    """Effective configuration after merging defaults + local overrides."""

    values: dict = field(default_factory=dict)
    operator: OperatorConfig = field(default_factory=OperatorConfig)
    radio: RadioConfig = field(default_factory=RadioConfig)
    webserver: WebserverConfig = field(default_factory=WebserverConfig)
    stackweb: dict = field(default_factory=dict)   # stack_id -> StackWebConfig (web-UI proxy)
    firewall: FirewallConfig = field(default_factory=FirewallConfig)
    boot: BootConfig = field(default_factory=BootConfig)
    gps: GpsConfig = field(default_factory=GpsConfig)   # THE position source for every stack
    sources: dict = field(default_factory=dict)   # per-component runtime overrides
    remotes: dict = field(default_factory=dict)   # per-component GitHub remote overrides
    local_path: Path | None = None
    secrets_path: Path | None = None
    diagnostics: list = field(default_factory=list)   # config-parse problems (non-fatal)

    def get(self, section: str, key: str, default=None):
        # A hand-edited wrong-type section (e.g. `install = "x"`) must never crash a
        # caller with AttributeError — treat a non-table section as absent (safe default).
        sec = self.values.get(section, {})
        if not isinstance(sec, dict):
            return default
        return sec.get(key, default)


def _load_toml(path: Path) -> dict:
    """Parse an EXTERNAL toml (shipped package-data defaults). Runtime-owned toml uses
    `_load_runtime_toml` (descriptor-anchored, no-follow)."""
    if not path.is_file():
        return {}
    try:
        with path.open("rb") as fh:
            return tomllib.load(fh)
    except (tomllib.TOMLDecodeError, OSError) as exc:
        raise ConfigError(f"{path}: {exc}") from exc


def _load_runtime_toml(paths: Paths, path: Path) -> dict:
    """Parse a RUNTIME-OWNED toml leaf via a descriptor-anchored, NO-FOLLOW read
    (`runtime_fs.read_bytes`): an absent file -> {} (benign default); an unreadable,
    symlinked, escaping, or malformed file raises `ConfigError` so its content can NEVER
    contribute data from outside the runtime root and the caller surfaces a diagnostic."""
    from . import runtime_fs
    try:
        raw = runtime_fs.read_bytes(paths, path)
    except FileNotFoundError:
        return {}
    except (OSError, PathContainmentError) as exc:
        raise ConfigError(f"{path}: {exc}") from exc
    try:
        return tomllib.loads(raw.decode("utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
        raise ConfigError(f"{path}: {exc}") from exc
    except RecursionError as exc:
        # AUDIT IN2: pathologically deep inline-table nesting makes tomllib recurse past
        # the interpreter limit — a malformed config must be a diagnostic, not a crash.
        raise ConfigError(f"{path}: config nesting too deep") from exc


def _deep_merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def _split_csv(value) -> list:
    """Split a comma-separated scalar (the flat-scalar local.toml representation of a list)
    into stripped, non-empty tokens. A non-string is treated as empty."""
    if not isinstance(value, str):
        return []
    return [tok.strip() for tok in value.split(",") if tok.strip()]


def _valid_ip_san(tok: str):
    """Return the normalized IP literal for a certificate SAN, or None if invalid.
    `0.0.0.0` is rejected — it is a bind wildcard, never a certificate SAN."""
    import ipaddress
    try:
        ip = ipaddress.ip_address(tok)
    except ValueError:
        return None
    if int(ip) == 0:                       # 0.0.0.0 / :: — never a SAN
        return None
    return str(ip)


def _parse_webserver(merged: dict, diagnostics: list) -> WebserverConfig:
    """Structure-validate the merged `[webserver]` section into a typed, NORMALIZED
    `WebserverConfig`. A wrong-typed section or field becomes a diagnostic + safe default;
    malformed list entries (CIDR/SAN) are DROPPED with a diagnostic. Never crashes, never
    leaks an unsafe value downstream. This is DESIRED config only — not proof of effective
    state."""
    from . import validators
    d = WebserverConfig()
    ws = merged.get("webserver", {})
    if not isinstance(ws, dict):
        diagnostics.append(f"ignored non-table [webserver] ({type(ws).__name__}); using defaults")
        ws = {}

    bind = d.bind
    try:
        bind = validators.host(ws.get("bind", d.bind), field="webserver.bind")
    except validators.ValidationError as exc:
        diagnostics.append(f"ignored invalid webserver.bind ({exc}); using {d.bind}")

    port_v = d.port
    raw_port = ws.get("port", d.port)
    if isinstance(raw_port, bool) or not isinstance(raw_port, int) or not (1 <= raw_port <= 65535):
        diagnostics.append(f"ignored invalid webserver.port {raw_port!r}; using {d.port}")
    else:
        port_v = raw_port

    mode = ws.get("access_mode", d.access_mode)
    if mode not in WEBSERVER_ACCESS_MODES:
        diagnostics.append(f"ignored unknown webserver.access_mode {mode!r}; using {d.access_mode}")
        mode = d.access_mode

    exposed = ws.get("remote_exposed", d.remote_exposed)
    if not isinstance(exposed, bool):
        diagnostics.append("ignored non-bool webserver.remote_exposed; using false")
        exposed = False

    def _list(key, validate):
        out = []
        for tok in _split_csv(ws.get(key, "")):
            try:
                out.append(validate(tok))
            except validators.ValidationError as exc:
                diagnostics.append(f"dropped invalid {key} entry {tok!r} ({exc})")
        return tuple(dict.fromkeys(out))    # de-dup, preserve order

    cidrs = _list("allowed_cidrs", lambda t: validators.cidr(t, field="webserver.allowed_cidrs"))
    dns = _list("dns_sans", lambda t: validators.host(t, field="webserver.dns_sans"))
    ips = []
    for tok in _split_csv(ws.get("ip_sans", "")):
        norm = _valid_ip_san(tok)
        if norm is None:
            diagnostics.append(f"dropped invalid ip_sans entry {tok!r}")
        else:
            ips.append(norm)
    ips = tuple(dict.fromkeys(ips))

    def _days(key, dflt):
        v = ws.get(key, dflt)
        if isinstance(v, bool) or not isinstance(v, int) or not (1 <= v <= 3650):
            diagnostics.append(f"ignored invalid webserver.{key} {v!r}; using {dflt}")
            return dflt
        return v

    scheme = ws.get("scheme", d.scheme)
    if scheme not in WEBSERVER_SCHEMES:
        diagnostics.append(f"ignored unknown webserver.scheme {scheme!r}; using {d.scheme}")
        scheme = d.scheme
    # http cannot carry a client certificate (no TLS handshake to verify one in). Rather than render
    # a config that silently ignores the operator's access mode, fall back to the only mode http can
    # actually honour, and SAY SO.
    if scheme == "http" and mode != "no-auth":
        diagnostics.append(f"webserver.scheme=http cannot do client-certificate auth; "
                           f"access_mode {mode!r} downgraded to 'no-auth'")
        mode = "no-auth"

    return WebserverConfig(
        bind=bind, port=port_v, access_mode=mode, remote_exposed=exposed,
        allowed_cidrs=cidrs, dns_sans=dns, ip_sans=ips, scheme=scheme,
        server_cert_days=_days("server_cert_days", d.server_cert_days),
        client_cert_days=_days("client_cert_days", d.client_cert_days))


def _parse_firewall(merged: dict, diagnostics: list) -> FirewallConfig:
    """Parse [firewall]; a wrong-typed section falls back to safe defaults (a hand-edit must
    never crash config load). AP stays disabled unless BOTH interface and cidr are present."""
    raw = merged.get("firewall", {})
    if not isinstance(raw, dict):
        diagnostics.append(f"ignored non-table [firewall] ({type(raw).__name__})")
        return FirewallConfig()
    mode = raw.get("mode", "secure-default")
    if mode not in FIREWALL_MODES:
        if "mode" in raw:
            diagnostics.append(f"invalid [firewall] mode {mode!r}; using secure-default")
        mode = "secure-default"

    def _csv(key):
        v = raw.get(key, "")
        if isinstance(v, (list, tuple)):
            return tuple(str(x).strip() for x in v if str(x).strip())
        return tuple(p.strip() for p in str(v).split(",") if p.strip())

    ssh = []
    for tok in _csv("ssh_ports"):
        try:
            n = int(tok)
            if 1 <= n <= 65535:
                ssh.append(n)
        except ValueError:
            diagnostics.append(f"ignored non-numeric [firewall] ssh_port {tok!r}")
    # STRICT boolean: a hand-edited scalar like `ap_enabled = "false"` is truthy under bool()
    # and would silently ENABLE the AP rules. Accept a real bool only; any other type stays
    # disabled with a diagnostic (never fail-open on a mistyped flag).
    ap_raw = raw.get("ap_enabled", False)
    if not isinstance(ap_raw, bool):
        if "ap_enabled" in raw:
            diagnostics.append(f"ignored non-boolean [firewall] ap_enabled {ap_raw!r}; "
                               "AP stays disabled")
        ap_raw = False
    ap_en = ap_raw and bool(raw.get("ap_interface")) and bool(raw.get("ap_cidr"))
    extra = raw.get("extra_allow", ())
    if isinstance(extra, str) and extra.strip():        # flat-scalar JSON round-trip
        try:
            import json as _json
            extra = _json.loads(extra)
        except ValueError:
            extra = ()
    extra = tuple(e for e in extra if isinstance(e, dict)) if isinstance(extra, (list, tuple)) else ()
    return FirewallConfig(mode=mode, allow_endpoints=_csv("allow_endpoints"),
                          ssh_ports=tuple(ssh), ap_enabled=ap_en,
                          ap_interface=str(raw.get("ap_interface", "")),
                          ap_cidr=str(raw.get("ap_cidr", "")), extra_allow=extra)


def _parse_stackweb(merged: dict, diagnostics: list) -> dict:
    """Structure-validate `[stackweb]` into `{stack_id: StackWebConfig}`.

    Fail-soft like `_parse_webserver`: an unknown key, an invalid stack id, or a malformed value is
    DROPPED with a diagnostic, leaving its siblings intact. Never crashes, never half-parses."""
    from . import validators
    sw = merged.get("stackweb", {})
    if not isinstance(sw, dict):
        diagnostics.append(f"ignored non-table [stackweb] ({type(sw).__name__}); using defaults")
        return {}

    raw: dict = {}
    for key, value in sw.items():
        split = _split_stackweb_key(str(key))
        if split is None:
            diagnostics.append(f"dropped unknown stackweb key {key!r}")
            continue
        sid, field = split
        try:
            sid = validators.path_component(sid, field="stackweb stack id")
        except validators.ValidationError as exc:
            diagnostics.append(f"dropped stackweb key {key!r} (bad stack id: {exc})")
            continue
        raw.setdefault(sid, {})[field] = value

    out: dict = {}
    for sid, fields in raw.items():
        d = StackWebConfig(stack_id=sid)

        mode = fields.get("mode", d.mode)
        if mode not in STACKWEB_MODES:
            diagnostics.append(f"ignored unknown stackweb.{sid}_mode {mode!r}; using {d.mode}")
            mode = d.mode

        port = fields.get("port", d.port)
        if isinstance(port, bool) or not isinstance(port, int) or not (
                port == 0 or STACKWEB_MIN_PORT <= port <= 65535):
            diagnostics.append(f"ignored invalid stackweb.{sid}_port {port!r}; not proxied")
            port = 0

        scheme = fields.get("scheme", d.scheme)
        if scheme not in WEBSERVER_SCHEMES:
            diagnostics.append(f"ignored unknown stackweb.{sid}_scheme {scheme!r}; using {d.scheme}")
            scheme = d.scheme

        access_mode = fields.get("access_mode", d.access_mode)
        if access_mode not in WEBSERVER_ACCESS_MODES:
            diagnostics.append(f"ignored unknown stackweb.{sid}_access_mode {access_mode!r}; "
                               f"using {d.access_mode}")
            access_mode = d.access_mode
        if scheme == "http" and access_mode != "no-auth":
            diagnostics.append(f"stackweb.{sid}_scheme=http cannot do client-certificate auth; "
                               f"access_mode {access_mode!r} downgraded to 'no-auth'")
            access_mode = "no-auth"

        cidrs = []
        for tok in _split_csv(fields.get("allowed_cidrs", "")):
            try:
                cidrs.append(validators.cidr(tok, field=f"stackweb.{sid}_allowed_cidrs"))
            except validators.ValidationError as exc:
                diagnostics.append(f"dropped invalid stackweb.{sid}_allowed_cidrs entry "
                                   f"{tok!r} ({exc})")
        out[sid] = StackWebConfig(stack_id=sid, mode=mode, port=port, scheme=scheme,
                                  access_mode=access_mode,
                                  allowed_cidrs=tuple(dict.fromkeys(cidrs)))
    return out


def load_config(paths: Paths, defaults_path: Path | None = None) -> Config:
    """Merge tracked defaults with the runtime-local override layer (read-only)."""
    defaults = _load_toml(defaults_path or _DEFAULTS_PATH)
    local_path = paths.runtime_root / "config" / "local.toml"
    # Malformed operator config is a DIAGNOSTIC, not a crash: fall back to defaults
    # and surface the parse error so the operator can fix local.toml.
    diagnostics: list = []
    try:
        local = _load_runtime_toml(paths, local_path)
    except ConfigError as exc:
        local, diagnostics = {}, [f"ignored malformed local config — {exc}"]
        local_layer_failed = True
    else:
        local_layer_failed = False
    merged = _deep_merge(defaults, local)

    # STRUCTURE validation (not just syntax): a wrong-typed section — e.g. a hand-edited
    # `operator = "x"` or `remotes = "x"` — must become a diagnostic + safe default, never
    # a crash (a str has no `.get`) and never leak a bad value into command/config/Git.
    op = merged.get("operator", {})
    if not isinstance(op, dict):
        diagnostics.append(f"ignored non-table [operator] (got {type(op).__name__}); using defaults")
        op = {}

    def _str_field(name: str) -> str:
        v = op.get(name, "")
        if not isinstance(v, str):
            diagnostics.append(f"ignored non-string operator.{name} ({type(v).__name__}); treating as unset")
            return ""
        return v

    operator = OperatorConfig(callsign=_str_field("callsign"))

    # [radio] hardware setup. Fail-OPEN: an absent/malformed/unknown value falls back to `unset`
    # (no hardware configured — the daemon refuses to start until the operator picks a board), with a
    # diagnostic. Pre-release: no migration from any older `[radio].mode` key.
    radio_raw = merged.get("radio", {})
    if not isinstance(radio_raw, dict):
        diagnostics.append(f"ignored non-table [radio] (got {type(radio_raw).__name__}); using unset")
        radio_raw = {}
    hardware = radio_raw.get("hardware", HW_DEFAULT)
    # MIGRATION: the daemon renamed the dual-module `--hw` preset `legacy` -> `loraham` and removed
    # `legacy` entirely (it now fails the daemon usage check). A stored `legacy` -> the `loraham` setup
    # so existing installs keep working instead of refusing to start.
    if hardware == "legacy":
        diagnostics.append("migrated radio.hardware 'legacy' -> 'loraham' (daemon renamed the --hw preset)")
        hardware = "loraham"
    if hardware not in HW_SETUPS:
        diagnostics.append(f"ignored invalid radio.hardware {hardware!r}; using {HW_DEFAULT}")
        hardware = HW_DEFAULT
    radio = RadioConfig(hardware=hardware)

    remotes_raw = local.get("remotes", {})   # runtime-local only, never tracked
    if not isinstance(remotes_raw, dict):
        diagnostics.append(f"ignored non-table [remotes] (got {type(remotes_raw).__name__}); using none")
        remotes = {}
    else:
        # Drop any non-string remote value here so a malformed hand-edit can never reach
        # Git (URL syntax is validated separately at save/use time).
        remotes = {}
        for k, v in remotes_raw.items():
            if isinstance(v, str):
                remotes[k] = v
            else:
                diagnostics.append(f"ignored non-string remote '{k}' ({type(v).__name__})")

    sources = merged.get("sources", {})
    if not isinstance(sources, dict):
        diagnostics.append(f"ignored non-table [sources] ({type(sources).__name__}); using defaults")
        sources = {}

    webserver = _parse_webserver(merged, diagnostics)
    stackweb = _parse_stackweb(merged, diagnostics)
    firewall = _parse_firewall(merged, diagnostics)
    boot = _parse_boot(merged, diagnostics)
    gps = _parse_gps(merged, diagnostics)
    if gps.source == "auto" and gps.auto_listening is None:
        # Resolve `auto` HERE, once per load: every consumer of this Config object — the
        # service (which caches it per operation), Lifecycle (handed the same object) — then
        # shares ONE frozen verdict, instead of re-probing live /proc state mid-start.
        from . import gps as _gps_mod
        gps = replace(gps, auto_listening=_gps_mod.local_gpsd_listening())
    # FAIL CLOSED (plan §3, deliberate deviation from the fail-soft convention above): when the
    # LOCAL layer itself could not be read (malformed/unreadable/symlinked local.toml), the
    # operator's boot-restore switch is unknown — an autonomous process-starter must not fall
    # back to the default-ON. Every other consumer keeps the fail-soft defaults.
    if local_layer_failed:
        boot = BootConfig(restore=False, valid=False,
                          reason="local config unreadable/malformed — boot restore disabled "
                                 "(fail closed)")
        # `[gps]` lives in that same unreadable layer, so its ABSENCE from `merged` means
        # "could not be read", not "not configured". Reporting a clean `off` would be a lie
        # with consequences: a stack whose GPS is enabled would start believing there is
        # simply no source, instead of refusing because the setting is unknown.
        gps = GpsConfig(source="off", valid=False,
                        reason="local config unreadable/malformed — position source unknown "
                               "(fail closed)")

    return Config(
        values=merged,
        operator=operator,
        radio=radio,
        webserver=webserver,
        stackweb=stackweb,
        firewall=firewall,
        boot=boot,
        gps=gps,
        sources=sources,
        remotes=remotes,
        local_path=local_path,
        secrets_path=paths.runtime_root / "config" / "secrets.toml",
        diagnostics=diagnostics,
    )


def load_secrets(paths: Paths) -> dict:
    """Read the local secrets layer (never tracked). Returns {} if absent.

    Refuses a file readable by group or others: the generated RNS config carrying
    the IFAC key is written 0600, which is pointless if the source of that key is
    world-readable. Create it with `install -m 0600` (or chmod 600 after editing —
    some editors restore 0644 on save).
    """
    path = paths.runtime_root / "config" / "secrets.toml"
    try:
        mode = path.stat().st_mode
    except OSError:
        return _load_runtime_toml(paths, path)
    if mode & 0o077:
        raise ConfigError(
            f"{path} is readable beyond its owner (mode {mode & 0o777:04o}) — "
            f"refusing to load secrets; run: chmod 600 {path}")
    return _load_runtime_toml(paths, path)


_WEB_SESSION_KEY = ("config", "secrets", "web_session.key")


def _web_session_path(paths: Paths) -> Path:
    return paths.runtime_root.joinpath(*_WEB_SESSION_KEY)


def web_session_secret(paths: Paths) -> bytes:
    """Return the PERSISTENT web session secret (>=32 bytes), generating + persisting it once
    at 0600 if absent/short. Survives restarts (so sessions/CSRF tokens are not invalidated on
    every process restart) and is NEVER cleared by 'Reset to default'. Never logged. An
    unsafe/symlinked leaf raises out of `atomic_write_bytes` (the caller fails safe)."""
    import secrets as _secrets

    from . import runtime_fs
    p = _web_session_path(paths)
    try:
        raw = runtime_fs.read_bytes(paths, p)
        if len(raw) >= 32:
            return raw
    except FileNotFoundError:
        pass
    except (OSError, PathContainmentError):
        pass
    secret = _secrets.token_bytes(48)
    # Persist ONLY on an existing (bootstrapped) runtime root. Never create the runtime root
    # just to store a secret — so constructing the web app against an absent/unbootstrapped
    # root mutates nothing (the console never serves there). Ephemeral fallback otherwise.
    if paths.runtime_root.exists():
        try:
            runtime_fs.atomic_write_bytes(paths, p, secret, mode=0o600)
        except (OSError, PathContainmentError):
            pass
    return secret


def rotate_web_session_secret(paths: Paths) -> bytes:
    """Explicitly rotate the persistent session secret — a deliberate operator action that
    invalidates every existing session (all clients must re-establish)."""
    import secrets as _secrets

    from . import runtime_fs
    secret = _secrets.token_bytes(48)
    runtime_fs.atomic_write_bytes(paths, _web_session_path(paths), secret, mode=0o600)
    return secret


def _toml_value(kind: str, value: str) -> str:
    """Format a value as TOML scalar for a flat key update."""
    v = str(value)
    if kind in ("int", "float"):
        return v if v.strip() != "" else "0"
    if kind == "flag":
        return "true" if v not in ("", "0", "false", "off") else "false"
    return '"' + v.replace("\\", "\\\\").replace('"', '\\"') + '"'


def render_keyval(params, values, subst, sep: str = " = ", comment: bool = True) -> str:
    """Render a flat `key<sep>value` config file from FileParams. `sep="="` (no
    spaces) suits parsers that split on the first '=' (e.g. lorachat.conf)."""
    lines = ["# Generated by lhpc — edit via the web Config page."] if comment else []
    for p in params:
        v = values.get(p.name, p.default)
        if p.kind == "flag":
            v = "1" if str(v) not in ("", "0", "false", "off") else "0"
        lines.append(f"{subst(p.key)}{sep}{subst(str(v))}")   # key may hold {band}
    return "\n".join(lines) + "\n"


_INI_UNSAFE = "".join(chr(c) for c in range(0x20)) + "\x7f"


def _ini_scalar(raw: str) -> str:
    """Render a value the way ConfigObj will read it back.

    Rejects control characters outright: a newline in a generated config does
    not corrupt one value, it invents a new key.
    """
    if any(ch in raw for ch in _INI_UNSAFE):
        raise ValueError("control characters are not allowed in a config value")
    if raw == "":
        return '""'
    if raw != raw.strip() or any(ch in raw for ch in "#,'\"") :
        return '"' + raw.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return raw


def _ini_section_path(section: str) -> tuple[str, ...]:
    """`section` is a "/"-separated path: "interfaces/LoRa 868" -> [[LoRa 868]]
    nested inside [interfaces]. "" means the top level."""
    return tuple(part for part in str(section).split("/") if part)


def update_ini(text: str, params, values, subst) -> str:
    """Update declared keys in a ConfigObj-style nested INI, preserving the rest.

    Sections are addressed by full path, so `[[LoRa 868]]` under `[interfaces]`
    is unambiguous even if another section reuses the name. A declared key that
    is absent is APPENDED to its section; only the FIRST occurrence is updated,
    so a hand-added duplicate can never be silently split.
    """
    want: dict = {}
    for prm in params:
        raw = subst(str(values.get(prm.name, prm.default)))
        if getattr(prm, "omit_if_empty", False) and raw.strip() == "":
            continue
        key = subst(prm.key)
        if any(ch in key for ch in _INI_UNSAFE) or not key.strip():
            raise ValueError(f"invalid config key {key!r}")
        want[(_ini_section_path(prm.section), key)] = _ini_scalar(raw)

    lines = text.splitlines()
    out: list[str] = []
    path: tuple[str, ...] = ()
    done: set = set()

    def flush(section_path, indent):
        """Append any declared keys this section never had."""
        for (sec, key), val in want.items():
            if sec == section_path and (sec, key) not in done:
                out.append(f"{indent}{key} = {val}")
                done.add((sec, key))

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            depth = len(stripped) - len(stripped.lstrip("["))
            name = stripped.strip("[]").strip()
            flush(path, "  " * (len(path)))          # close the previous section
            path = (*path[:depth - 1], name)
            out.append(line)
            continue
        candidate = stripped[1:].strip() if stripped.startswith("#") else stripped
        if "=" in candidate:
            key = candidate.split("=", 1)[0].strip()
            slot = (path, key)
            if slot in want and slot not in done:
                indent = line[:len(line) - len(line.lstrip())] or "  " * len(path)
                out.append(f"{indent}{key} = {want[slot]}")
                done.add(slot)
                continue
        out.append(line)
    flush(path, "  " * len(path))

    missing = {sec for (sec, _k) in want} - {sec for (sec, _k) in done}
    if missing:
        raise ValueError(f"config base has no section {'/'.join(min(missing))!r}")
    return "\n".join(out) + "\n"


def update_toml(text: str, params, values, subst) -> str:
    """Update declared keys (by section) in an existing TOML file, preserving the rest.

    A set value updates the key — uncommenting a `# key = …` line if needed — and is
    APPENDED to its section when the template has no such line at all (a declared key that
    silently vanished used to look configured while doing nothing).

    A blank value leaves the base as-is, EXCEPT for an `omit_if_empty` param, which REMOVES
    an active key: those are controller-owned (the MeshCore position), and leaving the
    previous line in place would keep publishing a stale value after the source of it was
    switched off. A COMMENTED example line is never removed — it documents the key.

    A declared section the template does not have is an error, as in `update_ini`: silently
    dropping it would ship a config missing settings the operator set. The result is parsed
    before it is returned, so a malformed edit fails here rather than at app start.
    """
    want, drop = {}, set()
    for p in params:
        raw = subst(str(values.get(p.name, p.default)))
        blank = p.kind != "flag" and raw.strip() == ""
        if blank and getattr(p, "omit_if_empty", False):
            drop.add((p.section, p.key))
            continue
        if blank:
            continue                       # blank -> don't touch the base
        want[(p.section, p.key)] = _toml_value(p.kind, raw)

    out: list = []
    section = ""
    done = set()                           # update the FIRST occurrence of each key only

    def flush(sec: str) -> None:
        """Append declared keys this section never had (order follows `want`)."""
        for (s, key), val in want.items():
            if s == sec and (s, key) not in done:
                out.append(f"{key} = {val}")
                done.add((s, key))

    for line in text.splitlines():
        st = line.strip()
        if st.startswith("[") and st.endswith("]"):
            flush(section)                 # close the previous section first
            section = st[1:-1]
            out.append(line)
            continue
        commented = st.startswith("#")
        candidate = st[1:].strip() if commented else st
        if "=" in candidate:
            key = candidate.split("=", 1)[0].strip()
            slot = (section, key)
            if slot in want and slot not in done:
                indent = line[: len(line) - len(line.lstrip())]
                out.append(f"{indent}{key} = {want[slot]}")
                done.add(slot)
                continue
            if slot in drop and not commented:
                continue                   # drop the ACTIVE line; keep commented examples
        out.append(line)
    flush(section)

    missing = {sec for (sec, _k) in want} - {sec for (sec, _k) in done}
    if missing:
        raise ValueError(f"config base has no section {min(missing)!r}")
    rendered = "\n".join(out) + "\n"
    try:
        tomllib.loads(rendered)
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"generated TOML is invalid: {exc}") from exc
    return rendered


def _yaml_value(kind: str, value: str) -> str:
    v = str(value)
    if kind == "flag":
        return "true" if v not in ("", "0", "false", "off") else "false"
    return v   # YAML bare scalar (ints/strings unquoted, as meshtasticd uses)


def update_yaml(text: str, params, values, subst) -> str:
    """Update declared `section.key` entries in a 2-space-indented YAML file,
    preserving everything else. Updates the FIRST occurrence of each key in its
    section (uncommenting a `#  key: …` line if that is the first occurrence), so
    the active value is set while commented alternative blocks are left untouched.
    Blank non-flag values leave the base as-is — UNLESS the param is `omit_if_empty`
    (OPTIONAL-ABSENT), in which case an active key line inherited from the base is
    REMOVED so the key is omitted from the generated file entirely (never an empty
    value). Commented example lines for the key are left commented (already inactive)."""
    want = {}
    remove = set()
    for p in params:
        raw = subst(str(values.get(p.name, p.default)))
        if p.kind != "flag" and raw.strip() == "":
            if getattr(p, "omit_if_empty", False):
                remove.add((p.section, p.key))     # optional-absent + unset -> OMIT the key
            continue
        want[(p.section, p.key)] = _yaml_value(p.kind, raw)
    lines = text.splitlines()
    out: list[str] = []
    section = ""
    done = set()
    for line in lines:
        bare = line.strip()
        if not bare or bare.startswith("---"):
            out.append(line)
            continue
        # Section header: an UNcommented top-level `Key:` with no inline value.
        if (not bare.startswith("#") and bare.endswith(":")
                and (len(line) - len(line.lstrip())) == 0 and ":" not in bare[:-1]):
            section = bare[:-1].strip()
            out.append(line)
            continue
        # Analyse a possibly-commented key line, preserving the key's own indent.
        analysed = line
        if bare.startswith("#"):
            h = line.index("#")
            analysed = line[:h] + line[h + 1:]      # drop one '#', keep indentation
        a = analysed.strip()
        if not a or a.startswith("#") or ":" not in a:
            out.append(line)
            continue
        indent = len(analysed) - len(analysed.lstrip())
        key = a.split(":", 1)[0].strip()
        sec = "" if indent == 0 else section
        # OMIT an ACTIVE (uncommented) line for an optional-absent unset param — drop it entirely.
        # (A commented `#  key:` example is already inactive; leave it commented.)
        if (sec, key) in remove and not bare.startswith("#"):
            continue
        if (sec, key) in want and (sec, key) not in done:
            out.append(f"{' ' * indent}{key}: {want[(sec, key)]}")
            done.add((sec, key))
            continue
        out.append(line)
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def _patch_local_table(data: dict, table: str, updates: dict) -> None:
    """Patch ONLY the named keys of a managed local table, IN PLACE on `data`. Contract:
        missing table                -> create a new flat table from `updates`;
        existing flat table           -> patch only the named keys (all other keys preserved);
        existing non-table value      -> `ConfigError` (before any write — fail closed on a valid-
                                          but-wrong TOML shape, e.g. ``operator = "text"``).
    A value of ``None`` in `updates` REMOVES that key (used to clear a remote override)."""
    cur = data.get(table)
    if cur is None:
        base: dict = {}
    elif isinstance(cur, dict):
        base = dict(cur)                          # keep every existing key/type
    else:
        raise ConfigError(f"local.toml [{table}] is a {type(cur).__name__}, not a table; "
                          f"refused (file unchanged)")
    for key, value in updates.items():
        if value is None:
            base.pop(key, None)
        else:
            base[key] = value
    data[table] = base


def _write_local_tables(paths: Paths, path: Path, updates: dict) -> Path:
    """PATCH managed tables into <runtime>/config/local.toml. `updates` is
    ``{table: {key: value_or_None}}`` — each table is patched by owned keys only (see
    `_patch_local_table`): other keys in that table, all other tables, and every root scalar are
    preserved with their exact types. A value of ``None`` clears that key.

    Fail closed: a malformed existing file, an incompatible managed-table shape (a non-table
    ``operator``/``remotes`` value), or an unsupported value/key raises `ConfigError` WITHOUT
    writing — the prior file is preserved byte-for-byte."""
    existing = _load_runtime_toml(paths, path)   # no-follow read; ConfigError on corrupt
    data = dict(existing)                         # keep root scalars + every other table
    for table, kv in updates.items():
        _patch_local_table(data, table, kv)      # patch owned keys; ConfigError on non-table shape
    # Type-safe + fail-closed render (raises before any write on an unsupported value/key).
    _atomic_write(paths, path, render_local_tables(data), mode=0o600)   # local layer: 0600
    return path


def save_operator_config(paths: Paths, callsign: str) -> Path:
    """Persist operator identity into the runtime-local layer (git-ignored). Patches only the
    `callsign` key — any other existing `[operator]` scalar is preserved."""
    path = paths.runtime_root / "config" / "local.toml"
    with config_lock(paths):
        return _write_local_tables(paths, path, {"operator": {"callsign": callsign}})


def save_gps(paths: Paths, *, recheck=None, **fields) -> Path:
    """Persist the GLOBAL position source into the runtime-local layer (git-ignored).

    Validates EVERYTHING before any write and writes the whole `[gps]` table, because the
    fields are interdependent: `nmea` needs a device, `fixed` needs complete coordinates.
    Patching one key at a time could leave a combination on disk that `_parse_gps` then
    rejects wholesale, silently disabling position — the operator would have "set" a source
    and got none.

    Only keys the caller passed are changed; the rest carry over from the current config,
    so `lhpc gps --host X` does not wipe the device.

    `recheck` (optional) runs UNDER the exclusive lock and may veto the write by returning a
    reason string — that is how "no consumer may be running" stays true at the moment of the
    write rather than only at the moment it was asked.
    """
    with config_lock(paths):
        # EVERYTHING under one exclusive lock: the recheck, the read, the merge, the
        # validation and the write.
        #
        # Reading the current table before taking the lock loses a concurrent update — two
        # partial saves each merge onto the value they read and the second silently discards
        # the first. And checking "is a consumer running" before the lock lets a start
        # complete in between, so the source changes under a stack whose claims and generated
        # config were derived from the old one.
        if recheck is not None:
            blocked = recheck()
            if blocked:
                raise ConfigError(blocked)
        return _write_gps_locked(paths, fields)


def _write_gps_locked(paths: Paths, fields: dict) -> Path:
    """Read-merge-validate-write for `[gps]`. Caller MUST hold the config lock."""
    cur = load_config(paths).gps
    merged = {
        "source": fields.get("source", cur.source),
        "host": fields.get("host", cur.host),
        "port": fields.get("port", cur.port),
        "device": fields.get("device", cur.device),
        "nmea_baud": fields.get("nmea_baud", cur.nmea_baud),
        "fixed_lat": fields.get("fixed_lat", cur.fixed_lat),
        "fixed_lon": fields.get("fixed_lon", cur.fixed_lon),
        "fixed_alt": fields.get("fixed_alt", cur.fixed_alt),
    }
    unknown = set(fields) - set(merged)
    if unknown:
        raise ConfigError(f"unknown [gps] field(s): {', '.join(sorted(unknown))}")

    src = str(merged["source"]).strip().lower()
    if src not in GPS_SOURCES:
        raise ConfigError(f"invalid GPS source {merged['source']!r} "
                          f"(allowed: {', '.join(GPS_SOURCES)})")
    merged["source"] = src

    try:
        port = int(merged["port"])
    except (TypeError, ValueError):
        raise ConfigError(f"invalid gpsd port {merged['port']!r}") from None
    if not (1 <= port <= 65535):
        raise ConfigError(f"gpsd port {port} out of range (1-65535)")
    merged["port"] = port

    try:
        baud = int(merged["nmea_baud"])
    except (TypeError, ValueError):
        raise ConfigError(f"invalid nmea_baud {merged['nmea_baud']!r}") from None
    if baud not in GPS_BAUDS:
        raise ConfigError(f"unsupported nmea_baud {baud} "
                          f"(allowed: {', '.join(map(str, GPS_BAUDS))})")
    merged["nmea_baud"] = baud

    host = str(merged["host"]).strip()
    if not host:
        raise ConfigError("gpsd host must not be empty")
    merged["host"] = host

    device = str(merged["device"]).strip()
    if device and not device.startswith("/"):
        raise ConfigError(f"GPS device {device!r} must be an absolute path")
    merged["device"] = device

    for key in ("fixed_lat", "fixed_lon", "fixed_alt"):
        merged[key] = "" if merged[key] in (None, "") else str(merged[key]).strip()

    if src == "nmea" and not device:
        raise ConfigError("source = nmea requires a device (e.g. --device /dev/ttyACM0)")
    if src == "fixed":
        ok, why = _finite_position(merged["fixed_lat"], merged["fixed_lon"], merged["fixed_alt"])
        if not ok:
            raise ConfigError(why)

    path = paths.runtime_root / "config" / "local.toml"
    return _write_local_tables(paths, path, {"gps": merged})


def save_hardware_setup(paths: Paths, setup_id: str) -> Path:
    """Persist the radio hardware setup into the runtime-local layer (git-ignored). Validates the
    setup id BEFORE any write (fail closed); patches only `[radio].hardware`."""
    if setup_id not in HW_SETUPS:
        raise ConfigError(f"invalid hardware setup {setup_id!r} (allowed: {', '.join(HW_SETUPS)})")
    path = paths.runtime_root / "config" / "local.toml"
    with config_lock(paths):
        return _write_local_tables(paths, path, {"radio": {"hardware": setup_id}})


def save_install_config(paths: Paths, *, adopt_search_root: str | None = None,
                        source_strategy: str | None = None) -> Path:
    """Persist install-channel settings into the runtime-local layer; patches only the
    named `[install]` keys. Used by the test lab to point adoption at its materialized
    fake sources — the values are ordinary, operator-settable config."""
    updates = {}
    if adopt_search_root is not None:
        updates["adopt_search_root"] = adopt_search_root
    if source_strategy is not None:
        if source_strategy not in ("adopt", "copy", "link"):
            raise ConfigError(f"invalid source_strategy {source_strategy!r}")
        updates["source_strategy"] = source_strategy
    path = paths.runtime_root / "config" / "local.toml"
    with config_lock(paths):
        return _write_local_tables(paths, path, {"install": updates})


def save_boot_restore(paths: Paths, enabled: bool) -> Path:
    """Persist the boot auto-restore switch. Accepts ONLY an actual bool (internal callers must
    not smuggle strings/ints — a truthy "false" is exactly the failure mode the strict parser
    guards against); patches only `[boot].restore`."""
    if not isinstance(enabled, bool):
        raise ConfigError(f"boot restore switch must be a bool, got {type(enabled).__name__}")
    path = paths.runtime_root / "config" / "local.toml"
    with config_lock(paths):
        return _write_local_tables(paths, path, {"boot": {"restore": enabled}})


def _cert_days(value, field: str) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        raise ConfigError(f"invalid {field} {value!r}") from None
    if not (1 <= n <= 3650):
        raise ConfigError(f"{field} out of range 1..3650: {n}")
    return n


def save_webserver_config(paths: Paths, *, bind=None, port=None, access_mode=None,
                          remote_exposed=None, allowed_cidrs=None, dns_sans=None,
                          ip_sans=None, server_cert_days=None, client_cert_days=None,
                          scheme=None, hold_lock=True) -> Path:
    """Persist DESIRED webserver settings into the runtime-local layer (git-ignored).
    Every supplied value is validated BEFORE any write (fail closed); a ``None`` argument
    means 'leave that key unchanged'. List fields are stored as comma-separated scalar
    strings (local.toml is flat-scalar). This writes INTENT only — it activates nothing and
    is never the source of truth for effective/exposed state. `hold_lock=False` skips the
    internal config_lock — the CALLER already holds it (same contract as
    `save_firewall_config`), which is what lets a read-union-save stay atomic without
    self-contending."""
    from . import validators
    patch: dict = {}
    if bind is not None:
        patch["bind"] = validators.host(bind, field="webserver.bind")
    if port is not None:
        patch["port"] = int(validators.port(port, field="webserver.port"))
    if access_mode is not None:
        if access_mode not in WEBSERVER_ACCESS_MODES:
            raise ConfigError(f"invalid access_mode {access_mode!r}")
        patch["access_mode"] = access_mode
    if remote_exposed is not None:
        patch["remote_exposed"] = bool(remote_exposed)
    if allowed_cidrs is not None:
        norm = [validators.cidr(c, field="webserver.allowed_cidrs") for c in allowed_cidrs]
        patch["allowed_cidrs"] = ",".join(dict.fromkeys(norm))
    if dns_sans is not None:
        norm = [validators.host(h, field="webserver.dns_sans") for h in dns_sans]
        patch["dns_sans"] = ",".join(dict.fromkeys(norm))
    if ip_sans is not None:
        out = []
        for t in ip_sans:
            v = _valid_ip_san(str(t).strip())
            if v is None:
                raise ConfigError(f"invalid IP SAN {t!r}")
            out.append(v)
        patch["ip_sans"] = ",".join(dict.fromkeys(out))
    if server_cert_days is not None:
        patch["server_cert_days"] = _cert_days(server_cert_days, "server_cert_days")
    if client_cert_days is not None:
        patch["client_cert_days"] = _cert_days(client_cert_days, "client_cert_days")
    if scheme is not None:
        if scheme not in WEBSERVER_SCHEMES:
            raise ConfigError(f"invalid scheme {scheme!r}")
        patch["scheme"] = scheme
    # FAIL CLOSED on the impossible combination, rather than writing a config whose access mode nginx
    # would silently ignore. Resolve against the CURRENT persisted values, not just this patch: saving
    # scheme=http alone must still be refused when the stored access_mode requires a client cert.
    _reject_http_with_cert_auth(paths, patch, "webserver")
    path = paths.runtime_root / "config" / "local.toml"
    if not hold_lock:
        return _write_local_tables(paths, path, {"webserver": patch})
    with config_lock(paths):
        return _write_local_tables(paths, path, {"webserver": patch})


def save_firewall_config(paths: Paths, *, mode=None, allow_endpoints=None, ssh_ports=None,
                         ap_enabled=None, ap_interface=None, ap_cidr=None,
                         extra_allow=None, hold_lock=True) -> Path:
    """Persist `[firewall]` intent to local.toml (flat scalars; lists comma-joined). AP is
    only meaningfully enabled when interface + cidr are both present (the parser re-checks).
    `hold_lock=False` skips the internal config_lock — the CALLER already holds it (so
    `firewall_configure` can validate, render scripts and commit config under ONE lock)."""
    from . import validators
    patch: dict = {}
    if mode is not None:
        if mode not in FIREWALL_MODES:
            raise ConfigError(f"invalid firewall mode {mode!r}")
        patch["mode"] = mode
    if allow_endpoints is not None:
        patch["allow_endpoints"] = ",".join(dict.fromkeys(
            str(e).strip() for e in allow_endpoints if str(e).strip()))
    if ssh_ports is not None:
        ports = []
        for p in ssh_ports:
            ports.append(str(int(validators.port(p, field="firewall.ssh_ports"))))
        patch["ssh_ports"] = ",".join(dict.fromkeys(ports))
    if ap_enabled is not None:
        patch["ap_enabled"] = bool(ap_enabled)
    if ap_interface is not None:
        patch["ap_interface"] = str(ap_interface).strip()
    if ap_cidr is not None:
        patch["ap_cidr"] = (validators.cidr(ap_cidr, field="firewall.ap_cidr")
                            if str(ap_cidr).strip() else "")
    if extra_allow is not None:
        # Stored as a JSON string in a flat scalar (local.toml is flat); the parser tolerates a
        # non-list and the candidate validator re-checks each entry's full scope.
        import json as _json
        patch["extra_allow"] = _json.dumps(list(extra_allow))
    path = paths.runtime_root / "config" / "local.toml"
    if not hold_lock:                                    # caller already holds the config lock
        return _write_local_tables(paths, path, {"firewall": patch})
    with config_lock(paths):
        return _write_local_tables(paths, path, {"firewall": patch})


def _reject_http_with_cert_auth(paths: Paths, patch: dict, what: str,
                                current: WebserverConfig | StackWebConfig | None = None) -> None:
    """A client certificate is presented during the TLS handshake, so a plain-http listener has
    nothing to verify. Refuse `scheme=http` together with a cert-based access mode — checking the
    EFFECTIVE result (patch merged over what is already stored), so neither half can sneak in alone."""
    if "scheme" not in patch and "access_mode" not in patch:
        return
    if current is None:
        current = load_config(paths).webserver
    scheme = patch.get("scheme", current.scheme)
    access_mode = patch.get("access_mode", current.access_mode)
    if scheme == "http" and access_mode != "no-auth":
        raise ConfigError(
            f"{what}: scheme=http cannot do client-certificate authentication (a client cert is "
            f"presented during the TLS handshake); access_mode must be 'no-auth', got "
            f"{access_mode!r}")


def _stackweb_table_patch(paths: Paths, stack_id: str, *, mode=None, port=None, scheme=None,
                          access_mode=None, allowed_cidrs=None, cfg=None) -> dict:
    """THE stackweb field validation: one stack's `[stackweb]` key patch (`<sid>_<field>` flat
    scalars), validated field-by-field; `None` = leave unchanged. Both the single-stack and the
    bulk writer build their tables here, so there is exactly one place that knows the rules —
    including the effective http/cert-auth rejection against the currently stored entry.
    `cfg` is an optional pre-loaded LHPCConfig (the bulk writer loads once for N stacks)."""
    from . import validators
    sid = validators.path_component(stack_id, field="stackweb stack id")
    patch: dict = {}
    if mode is not None:
        if mode not in STACKWEB_MODES:
            raise ConfigError(f"invalid stackweb mode {mode!r}")
        patch["mode"] = mode
    if port is not None:
        p = int(port)
        if p != 0 and not (STACKWEB_MIN_PORT <= p <= 65535):
            raise ConfigError(f"invalid stackweb port {port!r} "
                              f"(0 = not proxied, else {STACKWEB_MIN_PORT}..65535)")
        patch["port"] = p
    if scheme is not None:
        if scheme not in WEBSERVER_SCHEMES:
            raise ConfigError(f"invalid stackweb scheme {scheme!r}")
        patch["scheme"] = scheme
    if access_mode is not None:
        if access_mode not in WEBSERVER_ACCESS_MODES:
            raise ConfigError(f"invalid stackweb access_mode {access_mode!r}")
        patch["access_mode"] = access_mode
    if allowed_cidrs is not None:
        norm = [validators.cidr(c, field=f"stackweb.{sid}_allowed_cidrs") for c in allowed_cidrs]
        patch["allowed_cidrs"] = ",".join(dict.fromkeys(norm))
    if cfg is None:
        cfg = load_config(paths)
    current = cfg.stackweb.get(sid) or StackWebConfig(stack_id=sid)
    _reject_http_with_cert_auth(paths, patch, f"stackweb.{sid}", current)
    return {f"{sid}_{k}": v for k, v in patch.items()}


def save_stackweb_config(paths: Paths, stack_id: str, *, mode=None, port=None, scheme=None,
                         access_mode=None, allowed_cidrs=None, hold_lock=True) -> Path:
    """Persist DESIRED web-UI proxy exposure for ONE stack into `[stackweb]` (flat scalars,
    `<stack_id>_<field>`). Validated before any write; `None` = leave unchanged. INTENT only —
    activation is `webserver apply`. `hold_lock=False` skips the internal config_lock — the
    CALLER already holds it (same contract as `save_webserver_config`)."""
    table = _stackweb_table_patch(paths, stack_id, mode=mode, port=port, scheme=scheme,
                                  access_mode=access_mode, allowed_cidrs=allowed_cidrs)
    path = paths.runtime_root / "config" / "local.toml"
    if not hold_lock:
        return _write_local_tables(paths, path, {"stackweb": table})
    with config_lock(paths):
        return _write_local_tables(paths, path, {"stackweb": table})


def save_stackweb_configs(paths: Paths, updates: dict, hold_lock=True) -> Path:
    """Persist DESIRED web-UI proxy exposure for SEVERAL stacks in ONE locked atomic
    `local.toml` write — the bulk-policy path: every eligible stack gets the identical policy
    in the same transaction, so a partial save can never leave the set divergent. `updates`
    maps stack_id -> the same keyword fields `save_stackweb_config` takes. Every entry is
    validated (the shared `_stackweb_table_patch`) BEFORE any write; one bad entry refuses
    the whole set. `hold_lock=False` skips the internal config_lock — the CALLER already
    holds it (the bulk service op computes its candidates under that same lock, so the
    validated snapshot and the written one are the same configuration)."""
    path = paths.runtime_root / "config" / "local.toml"

    def _write():
        cfg = load_config(paths)
        table: dict = {}
        for sid, fields in updates.items():
            table.update(_stackweb_table_patch(paths, sid, cfg=cfg, **fields))
        return _write_local_tables(paths, path, {"stackweb": table})
    if not hold_lock:
        return _write()
    with config_lock(paths):
        return _write()


def save_component_remote(paths: Paths, component_id: str, url: str) -> Path:
    """Override a component's GitHub remote in the runtime-local layer. An empty
    url clears the override. The URL is validated to a safe remote policy BEFORE
    any file change (raises ValidationError on an unsafe/option-like value)."""
    return save_component_remotes(paths, {component_id: url})


def save_component_remotes(paths: Paths, updates: dict) -> Path:
    """Override (or clear, for an empty url) SEVERAL components' remotes in ONE atomic
    locked write — the shared-source propagation path: every consumer of one checkout gets
    the identical remote in the same transaction, so per-component divergence can never be
    left behind by a partial save."""
    from . import validators
    patch = {}
    for component_id, url in updates.items():
        cid = validators.path_component(component_id, field="component id")
        clean = validators.remote_url(url, field="remote")
        patch[cid] = clean or None                     # None clears the override
    path = paths.runtime_root / "config" / "local.toml"
    with config_lock(paths):
        # Patch ONLY these component keys, preserving every other remote. A non-table
        # `remotes` value is rejected inside the patch (ConfigError), never a raw
        # `dict("string")` ValueError.
        return _write_local_tables(paths, path, {"remotes": patch})


def render_local_tables(data: dict) -> str:
    """Render a complete local.toml from a parsed structure ``{key: scalar | {key: scalar}}`` —
    TYPE-SAFE and FAIL-CLOSED. Root scalar keys are preserved (never dropped), then each flat
    ``[section]`` table. Keys/values go through `_toml_key`/`_toml_scalar`, so bool/int/finite-float
    keep their type and quotes/backslashes/control chars/Unicode round-trip; a nested table, array,
    datetime, non-finite float, unsupported object, or control-character key raises `ConfigError`
    BEFORE any write. Finally the document is parsed with `tomllib` and its structure is verified
    to equal `data` — a mismatch (an unsafe key/value) is refused."""
    root = {k: v for k, v in data.items() if not isinstance(v, dict)}
    tables = {k: v for k, v in data.items() if isinstance(v, dict)}
    lines = ["# Local operator overrides (managed by lhpc — git-ignored)."]
    for key, value in root.items():
        lines.append(f"{_toml_key(key)} = {_toml_scalar(value)}")
    for section, table in tables.items():
        lines.append(f"\n[{_toml_key(section)}]")
        for key, value in table.items():
            if isinstance(value, dict):
                raise ConfigError(f"nested table [{section}.{key}] is not supported in local.toml")
            lines.append(f"{_toml_key(key)} = {_toml_scalar(value)}")
    rendered = "\n".join(lines) + "\n"
    try:
        parsed = tomllib.loads(rendered)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"generated local.toml is not valid TOML (refused): {exc}") from exc
    if parsed != data:
        raise ConfigError("generated local.toml did not round-trip (unsafe key/value); refused")
    return rendered


def render_stack_config(stack_id: str, values: dict) -> str:
    """Render a per-stack config file — type-aware, fully escaped, parse-validated before write
    (see `_render_stack_config`)."""
    return _render_stack_config(stack_id, values)


def _txn_journal(paths: Paths) -> Path:
    return paths.under("state", "config-txn.json")


_JOURNAL_VERSION = 1
# Only these logical config targets may ever appear in a transaction journal. Recovery
# maps a logical kind + a validated runtime-relative path through the safe path API —
# it never trusts or touches an arbitrary absolute path from journal content.
_ALLOWED_KINDS = {"local", "stack", "state"}


def _resolve_journal_target(paths: Paths, rec) -> Path:
    """Map ONE journal target record through the allowlist to a safe runtime path, or
    raise ConfigError. Rejects unknown kinds, absolute/traversal/escaping paths, the
    wrong shape per kind, and a symlink-leaf target."""
    if not isinstance(rec, dict):
        raise ConfigError("malformed journal target record")
    kind, rel = rec.get("kind"), rec.get("rel")
    if kind not in _ALLOWED_KINDS:
        raise ConfigError(f"unknown journal target kind {kind!r}")
    if (not isinstance(rel, str) or not rel or os.path.isabs(rel)
            or rel != os.path.normpath(rel) or ".." in rel.split("/")):
        raise ConfigError(f"unsafe journal target path {rel!r}")
    parts = rel.split("/")
    if kind == "local" and parts != ["config", "local.toml"]:
        raise ConfigError("local journal target must be config/local.toml")
    if kind == "stack" and (len(parts) != 3 or parts[:2] != ["config", "stacks"]
                            or not parts[2].endswith(".toml")):
        raise ConfigError("stack journal target must be config/stacks/<name>.toml")
    if kind == "state" and (len(parts) != 3 or parts[:2] != ["state", "restart-required"]
                            or not parts[2].endswith(".json")):
        # The ONLY state marker written through the config transaction: the durable
        # restart-required flag, atomic WITH the config change that caused it.
        raise ConfigError("state journal target must be state/restart-required/<name>.json")
    try:
        p = paths.under(*parts)        # lexical + symlink-parent containment
    except PathContainmentError as exc:
        raise ConfigError(f"journal target escapes runtime root: {exc}") from exc
    if p.is_symlink():
        raise ConfigError(f"refusing a symlink-leaf journal target: {p}")
    return p


def recover_config_transaction(paths: Paths) -> str | None:
    """Recover a pending config journal. Returns a message if it restored cleanly,
    None if there was NO journal, or "" if recovery is required but could not complete
    (journal retained — caller must block). A journal that EXISTS but is malformed,
    unreadable, wrong-schema, duplicate, or names a non-allowlisted target is NEVER
    treated as absent — it blocks (fail-closed)."""
    from . import runtime_fs
    try:
        jp = _txn_journal(paths)
    except PathContainmentError:
        # The journal's OWN location escapes the runtime root (e.g. a journal symlink
        # whose target leaves the root): a pending journal that cannot be safely located
        # is recovery-required, never absent and never an uncaught containment exception.
        return ""
    # Presence is decided WITHOUT following the leaf: ANY directory entry at the journal
    # path -- a regular file, OR a symlink (including a dangling or escaping one) -- is a
    # pending journal that must be recovered/blocked. `Path.exists()` follows the link and
    # would report a dangling-symlink journal as absent; `os.path.lexists` does not.
    if not os.path.lexists(jp):
        return None
    try:
        journal = json.loads(runtime_fs.read_text(paths, jp))   # no-follow read
    except (OSError, ValueError, PathContainmentError):
        return ""                       # exists but unreadable/symlinked/malformed -> BLOCK
    if (not isinstance(journal, dict) or journal.get("version") != _JOURNAL_VERSION
            or not isinstance(journal.get("targets"), list) or not journal["targets"]):
        return ""                       # wrong schema -> BLOCK
    resolved, seen = [], set()
    try:
        for rec in journal["targets"]:
            p = _resolve_journal_target(paths, rec)
            if str(p) in seen:
                return ""               # duplicate target -> BLOCK
            seen.add(str(p))
            resolved.append((p, rec))
    except ConfigError:
        return ""                       # unknown/escaping/symlink target -> BLOCK
    for p, rec in resolved:
        try:
            if rec.get("existed"):
                _atomic_write(paths, p, rec.get("pre") or "", int(rec.get("mode", 0o644)))
            else:
                runtime_fs.unlink(paths, p)           # descriptor-anchored, no-follow
        except (OSError, PathContainmentError):
            return ""                   # recovery FAILED -> keep journal, BLOCK
    try:
        runtime_fs.unlink(paths, jp)
    except (OSError, PathContainmentError):
        return ""                       # journal could not be removed -> recovery-required
    return f"recovered a pending config transaction ({len(resolved)} file(s))"


def apply_config_transaction(paths: Paths, targets: list[tuple[str, Path, str, int]]) -> None:
    """Write several config files all-or-recoverable under one lock. Each target is
    (logical-kind, path, content, mode). Steps: recover/\u200bblock any pending journal;
    journal each pre-image with a logical kind + runtime-relative path; atomically
    replace each; roll back all on failure; remove the journal only on success.
    Raises ConfigError("recovery-required: …") if a restore fails (journal kept)."""
    with config_lock(paths):
        _apply_config_transaction_locked(paths, targets)


def _apply_config_transaction_locked(paths: Paths, targets: list[tuple[str, Path, str, int]]) -> None:
    """MODULE-PRIVATE transaction body — assumes the config lock is ALREADY held EXCLUSIVELY by the caller.
    NOT a general lock-bypass API: the ONLY external caller is `ControllerService.save_config_bundle`, and
    ONLY when it holds the EXCLUSIVE config-stability guard (`_holds_config_exclusive()`, the auto-install
    auto-install boundary). Everyone else MUST use `apply_config_transaction()`, which acquires the lock. Steps
    (unchanged): recover/block any pending journal; journal each pre-image; atomically replace; roll back
    all on failure; remove the journal on success."""
    if recover_config_transaction(paths) == "":
        raise ConfigError("recovery-required: a pending config journal could not be "
                          "recovered; resolve it before saving config again")
    jp = _txn_journal(paths)
    journal = {"version": _JOURNAL_VERSION, "targets": []}
    for kind, p, _content, mode in targets:
        if p.is_symlink():
            raise ConfigError(f"refusing a symlink-leaf config target: {p}")
        rel = os.path.relpath(str(p), str(paths.runtime_root))
        from . import runtime_fs
        try:
            pre, existed = runtime_fs.read_text(paths, p), True   # no-follow read
        except FileNotFoundError:
            pre, existed = None, False
        except (OSError, PathContainmentError) as exc:   # unreadable/unsafe -> NOT "nonexistent"
            raise ConfigError(f"config target exists but is unreadable: {p} ({exc})") from exc
        journal["targets"].append({"kind": kind, "rel": rel, "pre": pre,
                                   "existed": existed, "mode": mode})
    for rec in journal["targets"]:        # prove every target resolves safely first
        _resolve_journal_target(paths, rec)
    _atomic_write(paths, jp, json.dumps(journal), 0o600)   # anchored write creates parents
    try:
        for _kind, p, content, mode in targets:
            # `content` may be a callable rendered INSIDE this lock (merge-in-transaction),
            # so it reads the LATEST file and preserves keys owned by another writer. A raise
            # here (e.g. an unsupported manual value) triggers the rollback below.
            _atomic_write(paths, p, content(paths) if callable(content) else content, mode)
    except Exception as failure:
        for rec in journal["targets"]:        # roll back everything
            p = _resolve_journal_target(paths, rec)
            try:
                if rec["existed"]:
                    _atomic_write(paths, p, rec["pre"], int(rec["mode"]))
                else:
                    runtime_fs.unlink(paths, p)   # descriptor-anchored, no-follow
            except (OSError, PathContainmentError) as exc:
                raise ConfigError(f"recovery-required: rollback failed ({exc}); "
                                  "journal retained") from exc
        try:
            runtime_fs.unlink(paths, jp)          # rolled back cleanly
        except (OSError, PathContainmentError) as exc:
            raise ConfigError(f"recovery-required: journal cleanup failed ({exc}); "
                              "journal retained") from exc
        raise ConfigError("config transaction failed and was rolled back: "
                          f"{failure}") from failure
    try:
        runtime_fs.unlink(paths, jp)              # success — remove the journal
    except (OSError, PathContainmentError) as exc:
        raise ConfigError(f"recovery-required: journal cleanup failed ({exc}); "
                          "journal retained") from exc




# --- per-stack user configuration (set via the web Config page) -----------

def _stack_config_path(paths: Paths, stack_id: str, band: str = "") -> Path:
    # Band-switchable stacks keep a separate config per band: "<id>@<band>.toml".
    # Defence in depth: the id is a single path component and the band must be a
    # real radio band, so neither can introduce a separator or "..". The result is
    # then proven to stay inside config/stacks/ (rejects any symlink/escape).
    from . import validators
    sid = validators.path_component(stack_id, field="stack id")
    if band:
        band = validators.band(band, allow_both=True)
    name = f"{sid}@{band}.toml" if band else f"{sid}.toml"
    # House no-follow containment discipline (lexical + realpath symlink-escape), consistent with
    # every other runtime write path; present the ValidationError callers already handle.
    try:
        resolved = paths.under("config", "stacks", name)
    except PathContainmentError as exc:
        raise validators.ValidationError(f"config path escapes stacks dir: {name!r}") from exc
    # A stack config leaf is always a real LHPC-written file (atomic rename), never a symlink. Refuse
    # a symlink leaf here so it surfaces as a typed "unsafe config" refusal to the caller rather than
    # a silent default (a no-follow read would otherwise ELOOP and be swallowed as an empty config).
    if resolved.is_symlink():
        raise validators.ValidationError(f"config path is a symlink leaf: {name!r}")
    return resolved


def load_stack_config(paths: Paths, stack_id: str, band: str = "") -> dict:
    """User-defined configuration for a stack/band (runtime-local, git-ignored).

    FAIL-CLOSED: ONLY an absent file yields `{}` (use-defaults). A present-but-malformed, unreadable,
    non-regular, oversized, symlinked, or wrong-typed persisted file raises a typed `ConfigError`
    (from `_load_runtime_toml`) rather than silently degrading to defaults — the bad file is left in
    place for diagnosis, and start/restart/reset/status/config-views/web must surface the error and
    refuse to continue with defaults (CLI: typed failure, no side effects; web: 409, no traceback/echo)."""
    return _load_runtime_toml(paths, _stack_config_path(paths, stack_id, band))


# TOML basic-string control-character escapes (TOML v1.0 §String). Other C0 controls + DEL are
# emitted as \uXXXX; a raw control character is NEVER placed in a one-line basic string.
_TOML_STR_ESC = {"\\": "\\\\", '"': '\\"', "\b": "\\b", "\t": "\\t",
                 "\n": "\\n", "\f": "\\f", "\r": "\\r"}
_BARE_KEY = re.compile(r"[A-Za-z0-9_-]+")


def _toml_basic_string(s: str) -> str:
    """`s` as a TOML basic string (double-quoted), fully escaped so it round-trips exactly:
    backslash/quote/backspace/tab/newline/formfeed/CR use short escapes, every other C0 control
    and DEL become \\uXXXX, and Unicode text is preserved verbatim. Invalid (non-UTF-8-encodable)
    text — e.g. a lone surrogate — is rejected BEFORE any write."""
    try:
        s.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ConfigError(f"string is not valid Unicode (lone surrogate?): {exc}") from exc
    out = []
    for ch in s:
        if ch in _TOML_STR_ESC:
            out.append(_TOML_STR_ESC[ch])
        elif ord(ch) < 0x20 or ord(ch) == 0x7F:
            out.append(f"\\u{ord(ch):04X}")
        else:
            out.append(ch)
    return '"' + "".join(out) + '"'


def _toml_scalar(value) -> str:
    """TOML representation of a SUPPORTED stack-config scalar, preserving its type: str (basic
    string, fully escaped), bool (`true`/`false`), int (decimal), finite float (round-trippable
    decimal). Anything else — list, table/mapping, datetime, NaN, ±inf, other objects — raises
    `ConfigError` BEFORE any write. NB: bool is checked before int (bool is an int subclass)."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ConfigError(f"non-finite float not allowed in stack config: {value!r}")
        return repr(value)                       # shortest round-trippable decimal
    if isinstance(value, str):
        return _toml_basic_string(value)
    raise ConfigError(f"unsupported stack-config value type "
                      f"{type(value).__name__}: {value!r}")


def _toml_key(key: str) -> str:
    """A TOML key that stays a single FLAT literal key: bare `[A-Za-z0-9_-]+`, else a quoted basic
    string (so `custom.key`, `spaced key`, `a#b` never become a dotted/nested path). A control
    character in a key is rejected before any write."""
    if not isinstance(key, str) or key == "":
        raise ConfigError(f"invalid config key: {key!r}")
    if any(ord(c) < 0x20 or ord(c) == 0x7F for c in key):
        raise ConfigError(f"control character in config key: {key!r}")
    return key if _BARE_KEY.fullmatch(key) else _toml_basic_string(key)


def _render_stack_config(stack_id: str, values: dict) -> str:
    """Render a stack config, then PARSE-BEFORE-WRITE: keys and scalars are type-aware and fully
    escaped, and the result is validated with `tomllib` so a malformed line can never reach disk
    (raises `ConfigError` before any write; the caller keeps the prior file)."""
    lines = [f"# {stack_id} configuration (managed by lhpc — git-ignored)."]
    for key, value in values.items():
        lines.append(f"{_toml_key(key)} = {_toml_scalar(value)}")
    rendered = "\n".join(lines) + "\n"
    try:
        parsed = tomllib.loads(rendered)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"generated stack config is not valid TOML (refused): {exc}") from exc
    if set(parsed) != set(values):       # a key became nested/dotted -> refuse
        raise ConfigError("generated stack config changed the key set (unsafe key); refused")
    return rendered


def save_stack_config(paths: Paths, stack_id: str, values: dict, band: str = "") -> Path:
    """Persist a stack/band's configuration (flat key/value, stored as strings).
    Atomic + locked so concurrent web saves cannot corrupt or interleave."""
    path = _stack_config_path(paths, stack_id, band)
    with config_lock(paths):
        _atomic_write(paths, path, _render_stack_config(stack_id, values), mode=0o644)
    return path


def merge_stack_values(paths: Paths, stack_id: str, band: str, updates: dict,
                       clear_empty: bool = True) -> dict:
    """Read the LATEST stack config (MUST be called inside `config_lock`) and merge `updates`,
    keeping every OTHER key with its parsed type — so one owner's write never drops another's
    keys (a daemon-profile save keeps normal params; a normal save keeps `dp_*`; a manual scalar
    survives both). When `clear_empty`, a value of ""/None removes that key; otherwise it is
    stored (normal-config semantics keep an explicit empty value). Returns the merged dict."""
    current = dict(_load_runtime_toml(paths, _stack_config_path(paths, stack_id, band)))
    for key, value in updates.items():
        if clear_empty and value in (None, ""):
            current.pop(key, None)
        else:
            current[key] = value
    return current


def conditional_clear_stack_config(paths: Paths, stack_id: str, band: str, expected: dict,
                                   matches) -> int:
    """Race-safe removal of legacy default-equal keys under ONE config lock. Re-reads the LATEST
    config, and for each key in `expected` removes it ONLY if `matches(key, str(current[key]),
    expected[key])` is True — i.e. the stored value is STILL semantically the pre-update default
    captured for that key. A value a concurrent save changed to a genuine override (or an intentional
    empty override) therefore fails the predicate and survives untouched; there is no stale
    snapshot-to-delete window. Returns the number removed; the write is atomic, so a write failure
    raises (ConfigError/OSError) and removes nothing (the caller keeps the candidates pending)."""
    path = _stack_config_path(paths, stack_id, band)
    with config_lock(paths):
        current = dict(_load_runtime_toml(paths, path))
        to_del = [k for k, exp in expected.items()
                  if k in current and matches(k, str(current[k]), exp)]
        if to_del:
            for k in to_del:
                del current[k]
            _atomic_write(paths, path, _render_stack_config(stack_id, current), mode=0o644)
        return len(to_del)


def update_stack_config(paths: Paths, stack_id: str, updates: dict, band: str = "",
                        clear_empty: bool = True) -> Path:
    """Locked read-merge-write of a stack's config under ONE lock: read the LATEST config, merge
    `updates` (see `merge_stack_values`), and atomic-write. Preserves every other key (run params,
    file values, autostart, `dp_*`, manual scalars …) and any concurrent change committed before
    the lock was taken. The render is type-aware and validates before the write, so a corrupt
    manual entry blocks the save and leaves the file unchanged. Never nests `config_lock`."""
    path = _stack_config_path(paths, stack_id, band)
    with config_lock(paths):
        merged = merge_stack_values(paths, stack_id, band, updates, clear_empty)
        _atomic_write(paths, path, _render_stack_config(stack_id, merged), mode=0o644)
    return path
