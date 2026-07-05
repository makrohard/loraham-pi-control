"""Core data model shared by every adapter.

These dataclasses describe stacks, components, resource claims, probe targets,
observed status and confirmed-working profiles: per-band daemons,
provider/consumer/cooperative resource semantics, structured probe targets and
evidence-backed status.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class ComponentKind(str, Enum):
    """How a component relates to the controller."""

    SERVICE = "service"          # long-running background process (systemd or manual)
    ONESHOT = "oneshot"          # CLI / build step / flash — run on demand, not "up/down"
    LIBRARY = "library"          # build-time dependency, never a running process
    FIRMWARE = "firmware"        # flashed to a device, not executed on the Pi


class RunState(str, Enum):
    """Observed runtime state of a component (read-only assessment)."""

    RUNNING = "running"          # process present and expected endpoints healthy
    DEGRADED = "degraded"        # running but an expected endpoint/readiness is missing
    STOPPED = "stopped"          # not running
    FAILED = "failed"            # systemd reports the unit failed
    UNKNOWN = "unknown"          # probe unavailable / permission denied / timeout
    NOT_APPLICABLE = "not-applicable"   # library/firmware — no continuous run state
    NOT_INSTALLED = "not-installed"     # source/runtime artifact absent


class SourceState(str, Enum):
    """State of a component's local source checkout vs its pinned commit."""

    MATCH = "match"              # present, HEAD == pinned commit, clean
    DIFFERS = "differs"          # present, HEAD != pinned commit
    DIRTY = "dirty"              # present but the worktree has local modifications
    NOT_A_REPO = "not-a-repo"    # path exists but is not a git repo
    MISSING = "missing"          # configured source path does not exist
    NOT_APPLICABLE = "not-applicable"   # component declares no source
    UNKNOWN = "unknown"          # probe failed


class TxState(str, Enum):
    """Whether a component is currently able to transmit (RF)."""

    DISABLED = "disabled"
    ENABLED = "enabled"
    UNKNOWN = "unknown"


class ProfileState(str, Enum):
    """Lifecycle state of a component's source/build/validation."""

    CONFIRMED_WORKING = "confirmed-working"
    INSTALLED_UNVALIDATED = "installed-unvalidated"
    CANDIDATE_AVAILABLE = "candidate-available"
    LOCALLY_MODIFIED = "locally-modified"
    FAILED_VALIDATION = "failed-validation"
    UNKNOWN = "unknown"


class ResourceKind(str, Enum):
    """Classes of system resources a component may claim."""

    RADIO_BAND = "radio-band"            # loraham.radio.433 / .868
    DAEMON_SOCKET = "daemon-socket"      # loraham.daemon-socket.433 / .868
    DAEMON_PROFILE = "daemon-profile"    # loraham.profile.433 / .868 (TX mode etc.)
    TCP_PORT = "tcp-port"                # tcp.port.<n>
    UNIX_SOCKET = "unix-socket"
    SERIAL = "serial"                    # serial.<device>
    AUDIO = "audio"                      # audio.<device>
    GPSD = "gpsd"                        # gpsd.local
    SPI_BUS = "spi-bus"                  # /dev/spidev0.0 + /run/lock/loraham/spi0.lock
    GPIO = "gpio"                        # /dev/gpiochip0


class ResourceMode(str, Enum):
    """Compatibility semantics of a claim — drives conflict interpretation.

    EXCLUSIVE   one active claimant at a time (e.g. a direct SPI client, a TCP port).
    COOPERATIVE members of the same `group` may co-exist (e.g. LoRaHAM daemon 433/868
                instances that serialize SPI internally). They conflict with any
                EXCLUSIVE claim on the same resource.
    PROVIDER    the component *creates* the resource (e.g. the daemon owning a CONF
                socket). Two providers of the same resource conflict.
    CONSUMER    the component *uses* a resource provided by another; never conflicts
                on its own, but records a dependency on a provider.
    REQUIREMENT a band-scoped configuration requirement (e.g. profile.433 = DIRECT)
                the daemon is set to satisfy when the stack starts.
    """

    EXCLUSIVE = "exclusive"
    COOPERATIVE = "cooperative"
    PROVIDER = "provider"
    CONSUMER = "consumer"
    REQUIREMENT = "requirement"


class SystemdScope(str, Enum):
    SYSTEM = "system"
    USER = "user"


@dataclass(frozen=True)
class ResourceClaim:
    """A claim a component makes on a named resource, with compatibility mode."""

    key: str                     # canonical id, e.g. "loraham.radio.868" / "tcp.port.7000"
    kind: ResourceKind
    mode: ResourceMode = ResourceMode.EXCLUSIVE
    group: str = ""              # cooperative group id (RESERVED: parsed + exposed via
                                 # group_id, not yet consumed by conflict logic)
    requirement: str = ""        # for REQUIREMENT mode, the required value (e.g. "DIRECT")
    note: str = ""

    @property
    def group_id(self) -> str:
        """Reserved cooperative-group identity (defaults to `key`); kept as schema."""
        return self.group or self.key


@dataclass(frozen=True)
class Requirement:
    """A dependency a component needs, with how to install it. Satisfied when the
    command `cmd` is on PATH, or (if `check_file` is set) when that file exists —
    used for -dev library packages that ship a header rather than a command.
    """

    cmd: str = ""
    install: str = ""           # e.g. "sudo apt install socat"
    check_file: str = ""        # e.g. "/usr/include/ncurses.h" (a header to test for)
    note: str = ""


@dataclass(frozen=True)
class UnitRef:
    """A systemd unit a component is supervised by, with its manager scope."""

    name: str
    scope: SystemdScope = SystemdScope.SYSTEM


@dataclass(frozen=True)
class ProcessSpec:
    """Structured process-identity match (never a bare substring match).

    A process matches when its executable basename equals `exec_name`, ALL of
    `all_args` appear among its argv, and (if `any_args` is non-empty) at least
    one of `any_args` appears.
    """

    exec_name: str
    all_args: tuple[str, ...] = ()
    any_args: tuple[str, ...] = ()


@dataclass(frozen=True)
class EndpointSpec:
    """A local endpoint a component exposes that can be probed read-only."""

    kind: str                    # "tcp" | "unix"
    address: str                 # "127.0.0.1:7000" | "/tmp/loraconf433.sock"
    role: str = "listener"       # "listener" (tcp) | "provider"/"data" (unix)
    readiness: str = "none"      # "none" | "daemon-status" (bounded GET STATUS probe)
    ready: bool = False          # participates in start readiness + stop endpoint-cessation
    external: bool = False       # an external endpoint (observe-only; never a ready gate)
    description: str = ""
    # A user-facing interface a client connects to (KISS TCP, a web UI, a serial
    # PTY) — shown on the dashboard. False for internal transport (daemon sockets).
    client: bool = False
    scheme: str = ""             # "http"|"kiss"|"tcp"|"serial"… (http renders a link)


@dataclass(frozen=True)
class RunParam:
    """A user-choosable run parameter (e.g. the daemon's --radio / --debug).

    The run command embeds `{name}` placeholders. For kind="enum" the placeholder
    is replaced by the chosen value; for kind="flag" it is replaced by `flag` when
    the value is truthy, otherwise the empty string.
    """

    name: str
    kind: str = "enum"           # "enum" | "flag" | "int" | "str"
    choices: tuple[str, ...] = ()
    default: str = ""
    flag: str = ""               # for kind="flag", the text to inject when enabled
    label: str = ""
    min: int | None = None       # for kind="int"
    max: int | None = None
    advanced: bool = False       # hide under "Advanced config" by default
    arg: str = ""                # CLI flag prefix; "-t 433.900" emitted only when set
    # When does a change take effect? "live" (runtime, applied at once / via socket),
    # "restart" (startup option — needs a stack restart), "build" (compile-time —
    # needs a rebuild). Drives the Config-page apply warnings.
    apply_mode: str = "restart"
    band_defaults: tuple = ()    # ((band, value), …) — per-band default overrides
    validator: str = ""          # named validator for kind="str" (callsign/freq/host/port/band/node)


def emit_param(p: "RunParam", value) -> list[str]:
    """Render a run parameter into ZERO OR MORE argv TOKENS (never a joined string).

    flag      -> [flag] when truthy, else []  (the option is its own token).
    arg       -> [arg, value] when value is non-empty, else []  (two separate tokens).
    positional-> [value] when non-empty, else []  (exactly one token).

    The value is always its own token, so a user value can never merge with an
    option or alter argv boundaries.
    """
    if p.kind == "flag":
        on = value and str(value) not in ("0", "false", "off", "")
        return [p.flag] if (on and p.flag) else []
    v = str(value)
    if not v.strip():
        return []
    return [p.arg, v] if p.arg else [v]


@dataclass(frozen=True)
class FileParam:
    """A setting written into a component's own config FILE (not a CLI arg).

    `key` is the file key; `section` is the TOML table it lives in ("" = flat /
    key=value file). Same kinds as RunParam.
    """

    name: str
    key: str
    section: str = ""
    kind: str = "str"            # "str" | "int" | "enum" | "flag" | "float"
    choices: tuple[str, ...] = ()
    default: str = ""
    label: str = ""
    advanced: bool = False
    apply_mode: str = "restart"
    min: int | None = None
    max: int | None = None
    band_defaults: tuple = ()    # ((band, value), …) — per-band default overrides
    hidden: bool = False         # written to the file but not shown on the Config page
    validator: str = ""          # named validator for kind="str" (callsign/freq/host/port/band/node)


@dataclass(frozen=True)
class FileConfig:
    """A config file the controller writes for a component from FileParams.

    fmt "keyval"      -> generate a flat `key = value` file from scratch.
    fmt "toml-update" -> read `base` (relative to the source dir), update the
                          declared keys in place (preserving structure), write `path`.
    `path` may contain {runtime}; otherwise it is relative to the source dir.
    """

    path: str
    fmt: str = "keyval"          # "keyval" | "toml-update" | "yaml-update"
    base: str = ""               # base file to update (rel. to source dir, or absolute)
    apply_cmd: str = ""          # copyable command to apply the generated file ({path})
    params: tuple[FileParam, ...] = ()


@dataclass(frozen=True)
class SourceSpec:
    """A local source checkout the controller tracks against a pinned commit."""

    path: str                    # runtime-root-relative, e.g. "src/loraham-daemon"
    pin_commit: str = ""         # full 40-char commit hash where known
    pin_tag: str = ""            # human tag (NOT used as immutable verification)
    remote: str = ""
    branch: str = ""
    local_dir: str = ""          # dir name under the adopt search root (defaults to basename(path))
    strategy: str = ""           # "" (use config default), "copy", or "link" (symlink in place)
    artifact: bool = False       # single-file/artifact-style source: EVERY selector resolves to
                                 # the same declared artifact (default-branch HEAD); no fake
                                 # pin/branch/tag semantics are invented for it

    @property
    def adopt_dir(self) -> str:
        return self.local_dir or self.path.rsplit("/", 1)[-1]


@dataclass(frozen=True)
class Component:
    """One software component within a stack."""

    id: str
    name: str
    kind: ComponentKind
    purpose: str = ""
    band: str = ""               # "433" | "868" | "" (band-agnostic); default band
    bands: tuple[str, ...] = ()  # if set, the operator may choose among these bands
    tx_capable: bool = False
    resources: tuple[ResourceClaim, ...] = ()
    units: tuple[UnitRef, ...] = ()
    process: ProcessSpec | None = None
    endpoints: tuple[EndpointSpec, ...] = ()
    depends_on: tuple[str, ...] = ()     # component ids this one needs at runtime
    build_requires: tuple[str, ...] = ()  # source component ids whose CHECKOUT this one's
                                          # build consumes (e.g. daemon -> radiolib); enforced
                                          # for update inclusion + uninstall refcounting
    source: SourceSpec | None = None
    log_paths: tuple[str, ...] = ()
    start_order: int | None = None
    note: str = ""
    # A short green confirmation shown (then auto-hidden) on the dashboard right after
    # this component is started — e.g. how to connect a just-launched GUI to its node.
    start_note: str = ""
    # Human-readable commands (relative to the component's source dir). Used to
    # drive starts and build/test jobs (manual start/ wrappers are retired).
    build_cmd: str = ""
    run_cmd: str = ""
    test_cmd: str = ""
    pre_cmd: str = ""            # optional pre-start hook (e.g. mkdir a lock dir)
    post_start: str = ""         # optional command spawned (detached) after start (e.g. set region)
    # --- structured command model (preferred; replaces the shell strings above) ---
    run_argv: tuple[str, ...] = ()        # argv token template (literals + {param:…}/{operator:…})
    run_cwd: str = ""                     # working dir ({runtime}/{source} substituted)
    run_env: tuple[tuple[str, str], ...] = ()   # extra env (value may be @file:/@env:/path)
    pre_steps: tuple[dict, ...] = ()      # typed controller pre-steps (mkdir/chmod/symlink)
    post_steps: tuple[dict, ...] = ()     # typed post-start steps (delay/exec)
    build_steps: tuple[dict, ...] = ()    # typed build steps ({argv, env, pkgconfig})
    test_argv: tuple[str, ...] = ()       # structured host-test argv (no shell)
    readiness: str = ""                   # process | endpoint | daemon-band | manual | external-systemd
    bin: str = ""                # built binary path (relative to source) for the 'is built' check
    requires: tuple[Requirement, ...] = ()   # external commands needed to run
    optional: bool = False       # an optional dependency component within a stack
    run_params: tuple[RunParam, ...] = ()    # user-choosable run parameters
    requires_daemon_tx: str = ""             # daemon TX mode this component needs (MANAGED/DIRECT)
    interactive: bool = False    # must be run by the operator in a terminal (e.g. a TUI);
                                 # the controller tracks it but never starts it
    config_file: FileConfig | None = None   # a config FILE the controller writes


@dataclass(frozen=True)
class Stack:
    """A named stack: a main component plus its (ordered, partly optional)
    dependency components."""

    id: str
    name: str
    summary: str = ""
    components: tuple[Component, ...] = ()
    main: str = ""               # id of the primary component (the app itself)
    operator_box: bool = True    # show the shared Operator (callsign/locator) box on the config
                                 # page; false when the stack edits its callsign in its own config

    def component(self, component_id: str) -> Component | None:
        for c in self.components:
            if c.id == component_id:
                return c
        return None

    @property
    def main_component(self) -> Component | None:
        return self.component(self.main) if self.main else (
            self.components[-1] if self.components else None)


# --------------------------------------------------------------------------
# Observed status (produced by the probe layer, consumed by adapters)
# --------------------------------------------------------------------------


@dataclass
class EndpointObservation:
    """Observed state of one endpoint at probe time."""

    spec: EndpointSpec
    present: bool = False        # tcp: listening; unix: socket exists & is a socket
    detail: str = ""             # human note (e.g. "RADIO=READY TXMODE=DIRECT")
    owner_pid: int | None = None
    owner_incomplete: bool = False  # owner lookup hit its time budget / not resolved


@dataclass
class DependencyObservation:
    component_id: str
    run_state: RunState
    band: str = ""


@dataclass
class ComponentStatus:
    """A read-only status assessment for a single component."""

    component_id: str
    run_state: RunState = RunState.UNKNOWN
    source_state: SourceState = SourceState.UNKNOWN
    source_version: str = ""     # git describe of the installed source
    tx_state: TxState = TxState.UNKNOWN
    profile_state: ProfileState = ProfileState.UNKNOWN
    endpoints: list[EndpointObservation] = field(default_factory=list)
    dependencies: list[DependencyObservation] = field(default_factory=list)
    pids: list[int] = field(default_factory=list)
    evidence: dict[str, str] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


@dataclass
class ResourceConflict:
    """A declared or observed conflict on a resource."""

    resource_key: str
    holders: tuple[str, ...]     # component ids involved
    observed: bool               # True = both components currently running
    message: str = ""


# NOTE: the per-component ConfirmedProfile schema (dead code — nothing ever wrote it) was
# replaced by OPERATOR-CONFIRMED per-stack known-working COMPOSITIONS (core/known_working.py).
