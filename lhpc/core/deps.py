"""Grouped dependency diagnosis for a stack — read-only, bounded, no network.

Three kinds, kept strictly separate (LHPC NEVER installs system packages itself —
every unmet system prerequisite is presented as an exact copy/pasteable command the
OPERATOR runs manually):

  * ``system``  — declared `require` prerequisites (packages, headers, device nodes);
  * ``build``   — `build_requires` source checkouts this component's build consumes
                  (e.g. loraham-daemon -> RadioLib at src/RadioLib);
  * ``runtime`` — `depends_on` start-ordering dependencies (components that must be
                  running first).
"""

from __future__ import annotations

import re as _re
from dataclasses import dataclass

from .lifecycle import (
    GROUP_MISSING_HINT,
    GROUP_RESTART_CMD,
    GROUP_RESTART_HINT,
    GUI_MISSING_HINT,
)

NOT_EXECUTED_NOTE = "not executed by LHPC — run it yourself"


def _local_gpsd_configured(paths) -> bool:
    """Is the global position source a gpsd on THIS box?

    Read defensively: dependency reporting runs on read-only status paths that must never
    raise, and a box with no GPS configured is the common case.
    """
    try:
        from .config import load_config
        return bool(load_config(paths).gps.local_gpsd)
    except (OSError, ValueError, AttributeError):
        return False


@dataclass(frozen=True)
class DepItem:
    kind: str            # "system" | "build" | "runtime"
    component: str       # the component declaring the dependency
    label: str           # human description of WHAT is needed
    satisfied: bool
    detail: str = ""     # current state / why unsatisfied
    install_cmd: str = ""  # exact operator command ("" when none applies)
    note: str = NOT_EXECUTED_NOTE
    runtime: bool = False  # run-time capability (e.g. group membership) — "grant" not "install"
    restart_pending: bool = False  # groups grant CONFIGURED but not yet EFFECTIVE — restart, not usermod
    gui: bool = False    # GUI-ONLY: excluded from the headless-safe default bootstrap (opt-in
    #                      --with-gui). Still SHOWN — it is warn-level, never a mandatory core miss.


def stack_report(lifecycle, paths, stacks, stack_id: str, comp_index: dict,
                 build_free=None, classify=None, artifact_note="", artifact_repair=None) -> list:
    """Every dependency of `stack_id`'s components, grouped by kind. `lifecycle`
    supplies the bounded `missing_requirements` probe; `comp_index` maps component
    id -> Component manifest-wide (for build/runtime edge resolution).

    `build_free(component_id) -> bool` reports that a component's build is provided by a
    prebuilt artifact (the binary channel). Its BUILD prerequisites are then not merely
    satisfied-by-other-means, they are IRRELEVANT: `build` is refused on that channel, so a
    missing compiler input is nothing the operator can or should act on. Without this the
    dashboard demanded a RadioLib checkout and PlatformIO on a box that had installed the
    daemon and MeshCom as binaries."""
    _free = build_free if callable(build_free) else (lambda _cid: False)
    # `classify(comp_id, req) -> "blocker"|"irrelevant"|"artifact-missing"` is the SAME predicate
    # the start gate uses (`ControllerService.binary_requirement_class`). Absent (tests, non-binary
    # callers) it degrades to: provisioned + build-free == not needed.
    _classify = classify if callable(classify) else (
        lambda cid, _req: "irrelevant" if _free(cid) else "blocker")
    _artifact_note = artifact_note
    _repair = artifact_repair if callable(artifact_repair) else (lambda _cid: "")
    stack = next((s for s in stacks if s.id == stack_id), None)
    if stack is None:
        return []
    out: list = []
    seen_sys: set = set()
    # CORE WINS across the whole stack: a command declared GUI-only by one component and plainly by
    # another is NOT GUI-only. Resolve that with an AND-merge BEFORE the first-wins dedup below,
    # which would otherwise let declaration order decide the classification.
    gui_eff: dict = {}
    for c in stack.components:
        for req in c.requires:
            k = req.install or req.cmd or req.check_file
            if not k:
                continue
            g = bool(getattr(req, "gui", False))
            gui_eff[k] = (gui_eff[k] and g) if k in gui_eff else g
    # A LOCAL-gpsd dependency is only real when the configured position source actually is a
    # local gpsd. With the source off, remote, or reading a device directly, there is nothing
    # to install here — telling an operator whose gpsd runs on another box that they are
    # missing a package would simply be false.
    gps_needed = _local_gpsd_configured(paths)
    for c in stack.components:
        missing = lifecycle.missing_requirements(c)
        for req in c.requires:
            if getattr(req, "gps", False) and not gps_needed:
                continue
            key = req.install or req.cmd or req.check_file
            if not key or key in seen_sys:
                continue
            seen_sys.add(key)
            sat = req not in missing
            # A groups grant that is configured but not yet effective (restart pending) is still
            # unsatisfied, but the fix is a restart, not another usermod — swap the detail + suppress
            # the grant command.
            pending = (not sat) and bool(req.groups) and lifecycle.group_grant_pending(req)
            if sat:
                detail = "not active" if req.absent_file else "present"
            elif req.groups:                       # state-specific, never both at once
                detail = GROUP_RESTART_HINT if pending else GROUP_MISSING_HINT
            elif req.absent_file:                  # inverse: the conflicting service is PRESENT
                detail = req.note or "a conflicting service is enabled/active — disable it"
            elif gui_eff.get(key):
                detail = GUI_MISSING_HINT
            else:
                detail = f"missing: {req.check_file or req.cmd or req.module}"
            _fix = req.install or ""
            if not sat and getattr(req, "provisioned", False):
                # THE SHARED CLASSIFIER decides — this report and the start gate must never
                # disagree. They did: everything provisioned read "not needed" here while the start
                # gate refused to start without the artifact-delivered ones.
                verdict = _classify(c.id, req)
                if verdict == "irrelevant":
                    # A pure BUILD tool (PlatformIO, the QEMU toolchain). There is no build on the
                    # binary channel, so it is nothing the operator can or should act on.
                    sat, detail = True, "not needed — this component came from a binary artifact"
                elif verdict == "artifact-missing":
                    # The artifact was supposed to deliver this and it is gone. Cheap receipt
                    # validation only restats proof paths, so nothing else would notice — and the
                    # remedy is NOT the source-build command the manifest carries.
                    detail = _artifact_note or detail
                    _fix = _repair(c.id) or _fix
            out.append(DepItem(
                kind="system", component=c.id,
                label=req.note or req.cmd or req.check_file or req.absent_file or req.module,
                satisfied=sat,
                detail=detail,
                # restart-pending shows the copyable restart command (re-running usermod would not help);
                # a genuinely-missing grant shows the usermod grant command.
                install_cmd=GROUP_RESTART_CMD if pending else _fix,
                runtime=bool(req.groups or req.absent_file), restart_pending=pending,
                gui=bool(gui_eff.get(key))))
        from . import source_fs
        for dep_id in c.build_requires:
            dep = comp_index.get(dep_id)
            present = bool(dep and dep.source
                           and source_fs.source_present(paths, paths.resolve_source(dep.source.path)))
            if _free(c.id):
                out.append(DepItem(
                    kind="build", component=c.id,
                    label=f"{dep_id} source checkout"
                          + (f" ({dep.source.path})" if dep and dep.source else ""),
                    satisfied=True,
                    detail="not needed — this component came from a binary artifact",
                    note="build is provided by the artifact"))
                continue
            out.append(DepItem(
                kind="build", component=c.id,
                label=f"{dep_id} source checkout"
                      + (f" ({dep.source.path})" if dep and dep.source else ""),
                satisfied=present,
                detail=("installed" if present else
                        "source not installed — install it before building"),
                install_cmd="" if present else f"lhpc install {_stack_of(stacks, dep_id)}",
                note=("consumed by the build" if present else NOT_EXECUTED_NOTE)))
        for dep_id in c.depends_on:
            dep = comp_index.get(dep_id)
            out.append(DepItem(
                kind="runtime", component=c.id,
                label=f"{dep_id} must be running first",
                satisfied=True,          # an ORDERING fact, not a current-state probe
                detail="start ordering handled by LHPC",
                note="runtime ordering"))
    return out


def _stack_of(stacks, comp_id: str) -> str:
    for s in stacks:
        if any(c.id == comp_id for c in s.components):
            return s.id
    return comp_id


def grouped(report: list) -> dict:
    """{kind: [DepItem...]} preserving order — the render shape for doctor/pages."""
    out: dict = {"system": [], "build": [], "runtime": []}
    for item in report:
        out.setdefault(item.kind, []).append(item)
    return out


# --- bootstrap-deps.sh generator ----------------------------------------------------------------
# A STANDALONE single-line apt install (mergeable into one deduplicated, non-interactive apt call —
# flag tokens like -y are dropped and re-added once). Multi-line blocks (the OBS repo bootstrap) are
# emitted verbatim and never merged out of order. Group-grant and SPI/config.txt commands are NOT
# emitted verbatim — they are re-rendered as hardened, operator-safe, mode-gated, idempotent sections.
_APT_INSTALL_RE = _re.compile(r"^sudo apt(?:-get)? install\s+(.+)$")
# Manifest remediation commands are written for an operator shell (`sudo …`); the bootstrap script
# runs as root and never invokes sudo, so `sudo` at command position is dropped when a command is
# emitted verbatim (line start, after a pipe, `&&`, `;`, `(`, `$(`).
_SUDO_RE = _re.compile(r"(^[ \t]*|[|&;(][ \t]*|\$\([ \t]*)sudo\s+", _re.MULTILINE)


def _desudo(block: str) -> str:
    return _SUDO_RE.sub(r"\1", block)
_USERMOD_RE = _re.compile(r"usermod\s+-a?G\s+([A-Za-z0-9,_-]+)")
_OVERLAY_RE = _re.compile(r"dtoverlay=([A-Za-z0-9_.-]+)")
_DISABLE_UNIT_RE = _re.compile(r"systemctl\s+disable\s+(?:--now\s+)?([A-Za-z0-9@._-]+)")

# Package-name patterns a HEADLESS install must never pull — GUI toolkits, X/Wayland, GPU/Mesa/LLVM,
# audio SERVERS, input stacks, icon themes/fonts, and whole desktop environments. Used by the
# generated `--dry-run` guard. GUI-opt-in packages are installed only behind --with-gui and are
# deliberately NOT part of that verdict.
# NOT denied: ALSA (`libasound`). Voice's ncurses terminal variant is a first-class headless
# component, so libasound2-dev is part of the DEFAULT transaction this guard vets — denying it
# made the dry run reject its own declared package set. ALSA is a kernel-level sound API, not a
# desktop audio server; PulseAudio (`libpulse`) stays denied.
_DENY_RE = (r"^(libgtk-|libgdk-|python3-tk|tk[0-9]|libsdl|libx11|libxcb|libxext|libxrandr"
            r"|libxcursor|libxi[0-9]|libxfixes|libxss|xserver-|xwayland|x11-common|xauth"
            r"|libwayland-|libgbm|libdrm|libegl|libgl[0-9x]|mesa-|libllvm|libpulse"
            r"|libinput|libxkbcommon|adwaita-|gnome-|kde-|xfce4|lxde|cups|fonts-)")


def _is_repo_block(low: str) -> bool:
    return any(t in low for t in ("opensuse", "sources.list", "signed-by", "apt/keyrings",
                                  "add-apt-repo", "trusted.gpg", "release.key"))


def _is_spi_block(low: str) -> bool:
    return "config.txt" in low or "dtparam=spi" in low or "dtoverlay=spi" in low


# --- Power controls (Reboot / Shut down from the console) -----------------------------------
# The console's power buttons act through logind (`systemctl reboot|poweroff`), which needs a
# polkit authorization for the unprivileged operator user. lhpc NEVER installs this itself
# (it never runs privileged commands): the rule arrives via bootstrap-deps on a fresh install
# (opt-out: --no-power-controls) or via the dependency-panel copybox on an existing box.
# ONE source of truth: the bootstrap scaffold and the copybox both render from these helpers.
POWER_RULE_PATH = "/etc/polkit-1/rules.d/49-lhpc-power.rules"


def power_rule_text(user: str) -> str:
    """The polkit rule granting `user` the four logind power action ids. `user` may be a
    literal account name (copybox) or a shell variable reference like `$OP` (bootstrap
    scaffold — expanded at script runtime by an unquoted heredoc)."""
    return (
        "// Installed by LoRaHAM Pi Control — lets the operator user reboot/shut down via logind.\n"
        "polkit.addRule(function(action, subject) {\n"
        f'    if (subject.user == "{user}" &&\n'
        '        (action.id == "org.freedesktop.login1.reboot" ||\n'
        '         action.id == "org.freedesktop.login1.reboot-multiple-sessions" ||\n'
        '         action.id == "org.freedesktop.login1.power-off" ||\n'
        '         action.id == "org.freedesktop.login1.power-off-multiple-sessions")) {\n'
        "        return polkit.Result.YES;\n"
        "    }\n"
        "});\n"
    )


def power_rule_install_cmd(user: str) -> str:
    """Paste-ready command(s) to authorize `user` for the console power buttons on THIS box:
    polkitd (systemd only Suggests it on Trixie) + the rule file. `install -D -m 0644`, never
    bare `tee` — the mode must be guaranteed even when the file (or rules.d) already exists."""
    return (
        "sudo apt install -y polkitd\n"
        f"sudo install -D -m 0644 /dev/stdin {POWER_RULE_PATH} <<'POWERRULE'\n"
        + power_rule_text(user)
        + "POWERRULE"
    )


# --- network controls (console Wi-Fi client mode with AP fallback) ---------------------------
# The Network panel joins the box to an existing WLAN via NetworkManager and, when a join is
# undone ("Back to AP mode"), re-activates the box's OWN AP so there is always a way home.
# Unprivileged nmcli can scan and list, but joining/saving profiles need `network-control` +
# `settings.modify.system`, AND re-activating the AP — an `ipv4.method=shared` connection —
# additionally needs `wifi.share.open`/`wifi.share.protected`. Without the share actions NM
# refuses the AP with "Not authorized to share connections via wifi" and the box strands with
# no active connection. Granted by this rule, delivered exactly like the power rule (bootstrap
# scaffold with opt-out, or the dependency-panel copybox on an existing box).
NETWORK_RULE_PATH = "/etc/polkit-1/rules.d/49-lhpc-network.rules"


def network_rule_text(user: str) -> str:
    """The polkit rule granting `user` the NetworkManager actions the Network panel needs —
    joining/saving a client profile (`network-control`, `settings.modify.system`) AND
    re-activating the box's shared AP (`wifi.share.open`, `wifi.share.protected`), without
    which the "way home" is denied. `user` may be a literal account name (copybox) or `$OP`
    (bootstrap scaffold — expanded at script runtime by an unquoted heredoc)."""
    return (
        "// Installed by LoRaHAM Pi Control — lets the operator user join Wi-Fi networks "
        "from the console and switch the box back to its own AP.\n"
        "polkit.addRule(function(action, subject) {\n"
        f'    if (subject.user == "{user}" &&\n'
        '        (action.id == "org.freedesktop.NetworkManager.network-control" ||\n'
        '         action.id == "org.freedesktop.NetworkManager.settings.modify.system" ||\n'
        '         action.id == "org.freedesktop.NetworkManager.wifi.share.open" ||\n'
        '         action.id == "org.freedesktop.NetworkManager.wifi.share.protected")) {\n'
        "        return polkit.Result.YES;\n"
        "    }\n"
        "});\n"
    )


def network_rule_install_cmd(user: str) -> str:
    """Paste-ready command(s) to authorize `user` for the console's Network panel on THIS
    box: polkitd + the rule file, `install -D -m 0644` (same rationale as the power rule)."""
    return (
        "sudo apt install -y polkitd\n"
        f"sudo install -D -m 0644 /dev/stdin {NETWORK_RULE_PATH} <<'NETWORKRULE'\n"
        + network_rule_text(user)
        + "NETWORKRULE"
    )


# --- time source (chrony disciplines the clock; gpsd feeds it GPS time over SHM) --------------
# A Pi has no battery-backed clock (only the Pi 5 has an RTC), so an offline box boots with the
# last time it happened to write, and every log line, certificate and receipt after that is
# wrong. chrony fixes that from whatever it can reach; gpsd hands it the receiver's time through
# SHM unit 0 when there is one. Default ON (--no-time-source opts out), because a fresh image
# with a receiver plugged in must discipline its clock without the operator knowing this exists.
#
# NTP ALWAYS wins when it is available. That is `prefer` on the NTP declarations, not a distance
# or stratum weight: the requirement is about identity ("NTP, whatever its measured error"), and
# `prefer` is the only chrony directive that expresses it. It also removes the GPS from the
# COMBINATION -- chrony narrows the selectable set to preferred sources and combines only those,
# so without it a close GPS keeps pulling the correction even when a worse NTP source is
# selected. No distance tuning can do that. It cannot let a bad server beat a good GPS (the
# falseticker test runs first) and it cannot strand an offline box (with no preferred source
# selectable, selection proceeds normally and the GPS is chosen).
#
# Same delivery as the two polkit rules above: bootstrap scaffold with an opt-out, or the
# dependency-panel copybox on an existing box. ONE source of truth: both render from these.
CHRONY_DROPIN_PATH = "/etc/chrony/conf.d/10-lhpc-gps.conf"
CHRONY_CONF_PATH = "/etc/chrony/chrony.conf"
CHRONY_SOURCES_DIR = "/etc/chrony/sources.d"
GPSD_DEFAULT_PATH = "/etc/default/gpsd"
# fake-hwclock keeps the last known time across a reboot: saved hourly (its systemd timer) and at
# shutdown, restored early at boot (before fsck, long before chrony). Without it a box that
# cannot reach NTP or GPS boots at the release's clock floor, days or weeks behind, and nginx
# then sees newer client certificates and the CRL as "not yet valid". LHPC owns this one file.
FAKE_HWCLOCK_DEFAULT_PATH = "/etc/default/fake-hwclock"


def fake_hwclock_default_text() -> str:
    """`FORCE=true` makes `fake-hwclock load` FORWARD-ONLY. Measured on Debian's fake-hwclock 0.14
    (trixie): with the default it sets the clock to the saved time even when the clock is LATER,
    i.e. it would step a Pi 5's correct RTC time back to the last save after every reboot; with
    FORCE=true it sets the clock only when the saved time is ahead. (The package names the
    variable after `save`'s "time travel" override; for `load` it is the forward-only switch.)"""
    return (
        "# Installed by LoRaHAM Pi Control.\n"
        "# FORCE=true: `fake-hwclock load` only moves the clock FORWARD, never back.\n"
        "FORCE=true\n"
    )
CLOCK_EPOCH_PATH = "/usr/lib/clock-epoch"
# The per-run success witness. REMOVED at the start of every setup and written only after the
# verdict passes, so it says "the most recent run completed" rather than "a run once completed".
# The boot floor cannot do this job: it is persistent by design, so after one good install it
# survives every later failed re-run -- and re-running bootstrap is the documented recovery path,
# so the dependency panel would hide the repair copybox exactly when it is needed.
TIME_SOURCE_STAMP_PATH = CLOCK_EPOCH_PATH + ".ok"

# The boot floor. systemd advances a clock below this at startup. The epoch is the HIGHEST of
# systemd's own build time, this file's mtime, and /var/lib/systemd/timesync/clock's mtime, so a
# value in the past is always safe and can only ever be ignored. ONE constant, ONE rule: a human
# edits it in the commit that ships a release. NEVER derived from the current clock, a commit
# timestamp or a checkout -- the standalone script and the image build have none of those.
CLOCK_EPOCH_FLOOR = "2026-09-14"


def chrony_dropin_text() -> str:
    """The refclock, declared honestly. gpsd publishes the receiver's time into SHM unit 0;
    `delay 1.0 precision 0.1` states the real uncertainty of coarse serial time (~0.6 s root
    distance), so chrony ranks it truthfully instead of trusting it. `poll 2` (4 s) gets a first
    clock update ~12 s after the first sample instead of ~37 s at the default -- chronyd needs
    three filtered samples before it will touch the clock. No `offset`: the gpsd howto's example
    value is a placeholder it warns is "not a number to be used in production", and the residual
    NMEA latency is receiver-specific and well inside the declared second."""
    return (
        "# Installed by LoRaHAM Pi Control - GPS as a time source of last resort.\n"
        "# NTP takeover is `prefer` on the NTP declarations in chrony.conf, not a number here.\n"
        "#\n"
        "# EVERY comment here is on its OWN LINE. chrony honours a comment character only as the\n"
        "# FIRST non-space character of a line (cmdparse.c: `if (first && strchr(\"!;#%\", *p))`),\n"
        "# so a trailing comment is parsed as extra ARGUMENTS and chronyd refuses to start:\n"
        "#   Fatal error : Too many arguments for makestep directive\n"
        "# Which, with systemd-timesyncd already removed, leaves the box with no time daemon at\n"
        "# all. Do not move these onto the directive lines.\n"
        "refclock SHM 0 refid GPS delay 1.0 precision 0.1 poll 2\n"
        "# an RTC-less box can boot years out; allow a step at any time\n"
        "makestep 1.0 -1\n"
        "# write back to the RTC where the board has one (Pi 5)\n"
        "rtcsync\n"
    )


def time_source_setup_sh() -> str:
    """The whole privileged setup, as ONE shell script fed to `sh -s` through a QUOTED heredoc.

    One heredoc rather than three separately-quoted one-liners is the entire point: the body is
    literal, so the awk program keeps its own quoting and nothing is expanded by an intermediate
    shell. Three nested quoting levels is how `$0` silently becomes the shell's name.

    Every step is idempotent: re-running changes nothing. Written to be read by an operator who
    is about to paste it as root."""
    return (
        "set -eu\n"
        "\n"
        "# Did anything that the feature PROMISES fail? On a box with a running systemd, the\n"
        "# promises are: NTP outranks GPS (`prefer` applied), chrony runs, gpsd runs. If any of\n"
        "# those could not be established the setup exits NONZERO, so the caller cannot print\n"
        "# \"time source: chrony disciplines the clock\" over a box where it does not. Inside a\n"
        "# container/image build with no systemd, daemon activation is genuinely not possible and\n"
        "# stays best-effort -- that branch never sets this.\n"
        "TS_FAILED=0\n"
        "\n"
        "# Every path is a seam with its real value as the default, so the test harness can point\n"
        "# the whole step at a tmp tree. On a real box nothing sets these and the defaults apply.\n"
        'CHRONY_DROPIN="${CHRONY_DROPIN:-' + CHRONY_DROPIN_PATH + '}"\n'
        'CHRONY_CONF="${CHRONY_CONF:-' + CHRONY_CONF_PATH + '}"\n'
        'CHRONY_SOURCES_DIR="${CHRONY_SOURCES_DIR:-' + CHRONY_SOURCES_DIR + '}"\n'
        'GPSD_DEFAULT="${GPSD_DEFAULT:-' + GPSD_DEFAULT_PATH + '}"\n'
        'FAKE_HWCLOCK_DEFAULT="${FAKE_HWCLOCK_DEFAULT:-' + FAKE_HWCLOCK_DEFAULT_PATH + '}"\n'
        'CLOCK_EPOCH="${CLOCK_EPOCH:-' + CLOCK_EPOCH_PATH + '}"\n'
        # Derived from $CLOCK_EPOCH rather than hardcoded, so anything that redirects the
        # floor redirects the witness with it -- a harness cannot accidentally leave one of the
        # two pointing at the real /usr/lib.
        'TS_STAMP="${TS_STAMP:-$CLOCK_EPOCH.ok}"\n'
        'LHPC_TMPDIR="${LHPC_TMPDIR:-/run}"\n'
        "\n"
        "# Retract any previous success BEFORE changing anything: from here until the verdict, the\n"
        "# most recent run has not completed, and nothing should claim otherwise.\n"
        'rm -f "$TS_STAMP"\n'
        "\n"
        "# 1. The refclock. LHPC owns this file outright; chrony.conf ends with `confdir`.\n"
        "install -D -m 0644 /dev/stdin \"$CHRONY_DROPIN\" <<'LHPC_DROPIN'\n"
        + chrony_dropin_text()
        + "LHPC_DROPIN\n"
        "\n"
        "# 1b. The last known time survives a reboot: fake-hwclock saves it hourly and at shutdown\n"
        "#     and restores it early at boot. Its default file is LHPC's (see\n"
        "#     fake_hwclock_default_text: FORCE=true makes the restore forward-only).\n"
        "install -D -m 0644 /dev/stdin \"$FAKE_HWCLOCK_DEFAULT\" <<'LHPC_FAKE_HWCLOCK'\n"
        + fake_hwclock_default_text()
        + "LHPC_FAKE_HWCLOCK\n"
        "if ! command -v fake-hwclock >/dev/null 2>&1; then\n"
        "  echo \"[bootstrap-deps] ERROR: fake-hwclock is not installed, so the clock falls back to\" >&2\n"
        "  echo \"[bootstrap-deps]        the release floor at every reboot: sudo apt install fake-hwclock\" >&2\n"
        "  TS_FAILED=1\n"
        "fi\n"
        "\n"
        "# 2. `prefer` on every NTP declaration chrony already has, so any of them outranks the\n"
        "#    GPS. EDITED, never rewritten: operator options, comments and every other line come\n"
        "#    back byte-identical, and a line that already has the option is left alone. chrony.conf\n"
        "#    is ucf --three-way managed, so a narrow local edit survives package upgrades.\n"
        "#    `prefer` goes in as the LAST OPTION OF THE DIRECTIVE, which on a line with a trailing\n"
        "#    `#` means BEFORE it. Note what that does and does NOT claim: chrony honours a comment\n"
        "#    character only at the START of a line, so a directive with a trailing comment is\n"
        "#    ALREADY fatal to chronyd before LHPC touches it. Inserting before the `#` is the\n"
        "#    least surprising edit and preserves the operator\'s bytes; it is not a rescue.\n"
        "#    awk, not sed: mawk is the Debian default and has no `-i inplace`. Write a temp, fsync\n"
        "#    it, then RENAME over the target. Three separate reasons, none of them optional:\n"
        "#      - rename is atomic for READERS, so chronyd never sees a half-written file;\n"
        "#      - `cat` back into place would keep the inode but TRUNCATES first, so an interrupted\n"
        "#        write leaves a chrony.conf that still PARSES and has lost its pool and keyfile\n"
        "#        lines -- which no 'is this valid?' check would ever catch;\n"
        "#      - rename is NOT atomic against power loss. Without the sync, a crash can leave the\n"
        "#        directory entry pointing at data that never reached the disk: a present,\n"
        "#        correctly named, EMPTY chrony.conf. That is the same end state, by another road,\n"
        "#        and an RTC-less Pi losing power is this feature\'s entire premise.\n"
        "#    Mode and owner are copied across, the only thing the changed inode costs. ucf is\n"
        "#    unaffected: it compares the file\'s CONTENT hash to its record, not its inode, so a\n"
        "#    rename looks exactly like the local edit ucf exists to merge on upgrade.\n"
        'cat > "$LHPC_TMPDIR/lhpc-prefer.awk" <<\'LHPC_AWK\'\n'
        "{\n"
        "  line = $0; cr = \"\"\n"
        "  if (line ~ /\\r$/) { cr = \"\\r\"; sub(/\\r$/, \"\", line) }\n"
        "  body = line; comment = \"\"\n"
        "  hash = index(line, \"#\")\n"
        "  if (hash > 0) { body = substr(line, 1, hash - 1); comment = substr(line, hash) }\n"
        "  if (body ~ /^[ \\t]*(pool|server|peer)[ \\t]/) {\n"
        "    n = split(body, field, /[ \\t]+/); have = 0\n"
        "    for (i = 1; i <= n; i++) if (field[i] == \"prefer\") have = 1\n"
        "    if (have == 0) {\n"
        "      trimmed = body; sub(/[ \\t]*$/, \"\", trimmed)\n"
        "      print trimmed \" prefer\" substr(body, length(trimmed) + 1) comment cr\n"
        "      next\n"
        "    }\n"
        "  }\n"
        "  print line cr\n"
        "}\n"
        "LHPC_AWK\n"
        'for conf in "$CHRONY_CONF" "$CHRONY_SOURCES_DIR"/*.sources; do\n'
        "  [ -f \"$conf\" ] || continue\n"
        '  # Every failure below happens BEFORE the rename, i.e. before $conf is touched, so\n'
        '  # skipping is safe. It is never SILENT: a file that was not migrated is one whose NTP\n'
        '  # sources will not outrank the GPS, and an operator who is not told will not know.\n'
        '  if ! awk -f "$LHPC_TMPDIR/lhpc-prefer.awk" "$conf" > "$conf.lhpc-new"; then\n'
        '    rm -f "$conf.lhpc-new"\n'
        '    echo "[bootstrap-deps] ERROR: could not rewrite $conf - NTP will NOT be preferred over" >&2\n'
        '    echo "[bootstrap-deps]        GPS for its sources. Add `prefer` to them by hand." >&2\n'
        '    TS_FAILED=1\n'
        '    continue\n'
        '  fi\n'
        '  # FAIL CLOSED on the mode, do not `|| true` it. The temp was created by a shell\n'
        '  # redirect, so it carries the UMASK, not the original mode -- if --reference fails on a\n'
        '  # 0600 sources file, renaming it into place would silently WIDEN its permissions. That\n'
        '  # reads as harmless best-effort and is not.\n'
        '  if ! chmod --reference="$conf" "$conf.lhpc-new" 2>/dev/null \\\n'
        '     || ! chown --reference="$conf" "$conf.lhpc-new" 2>/dev/null; then\n'
        '    rm -f "$conf.lhpc-new"\n'
        '    echo "[bootstrap-deps] ERROR: could not preserve mode/owner of $conf - left unchanged" >&2\n'
        '    echo "[bootstrap-deps]        rather than replaced with different permissions, so NTP is" >&2\n'
        '    echo "[bootstrap-deps]        NOT preferred over GPS for its sources." >&2\n'
        '    TS_FAILED=1\n'
        '    continue\n'
        '  fi\n'
        '  sync "$conf.lhpc-new" 2>/dev/null || sync\n'
        '  mv -f "$conf.lhpc-new" "$conf"\n'
        '  sync "$(dirname "$conf")" 2>/dev/null || true\n'
        "done\n"
        'rm -f "$LHPC_TMPDIR/lhpc-prefer.awk"\n'
        "\n"
        "# 3. gpsd must poll without waiting for a client: chrony is not a gpsd client, it reads\n"
        "#    SHM, so without -n there are never any samples. EDITED, never overwritten -- this\n"
        "#    file carries the operator\'s DEVICES and USBAUTO.\n"
        "#\n"
        "#    Two things this has to get right, both learned the hard way:\n"
        "#      - The VALUE is shell-quoted and may carry trailing whitespace outside the quotes,\n"
        "#        which systemd discards. Detecting the closing quote before trimming wrapped an\n"
        "#        already-quoted value in a second pair and produced `\\\"\\\"-G -D 2\\\" -n\\\"`.\n"
        "#      - The question is whether `-n` is present as an ARGUMENT, not as a substring:\n"
        "#        `-P /run/gpsd-node.pid` contains the letters but supplies no such option.\n"
        "#    awk therefore trims first, then unquotes, then splits the value into fields and\n"
        "#    compares whole tokens.\n"
        'touch "$GPSD_DEFAULT"\n'
        "cat > \"$LHPC_TMPDIR/lhpc-gpsd.awk\" <<'LHPC_GPSD_AWK'\n"
        "# Emits the rewritten line on stdout and its verdict on fd 3:\n"
        "#   have  - the option was already present, nothing to do\n"
        "#   added - the line was rewritten with -n appended\n"
        "#   none  - no GPSD_OPTIONS assignment exists at all\n"
        "function unquote(v,   q, n) {\n"
        "  # \\r too, not just spaces and tabs: a CRLF environment file left the carriage return\n"
        "  # attached, so the closing quote was not the last character and the value got wrapped\n"
        "  # in a second pair -- the same corruption as an untrimmed trailing space. systemd\n"
        "  # discards carriage returns outside quotes, so CRLF input is ordinary valid input.\n"
        "  sub(/^[ \\t\\r]+/, \"\", v); sub(/[ \\t\\r]+$/, \"\", v)\n"
        "  q = substr(v, 1, 1); n = length(v)\n"
        "  # 047 is the octal escape for a single quote; a literal one cannot appear in this\n"
        "  # program, which is wrapped in a single-quoted shell heredoc. No apostrophes.\n"
        "  if (n >= 2 && (q == \"\\\"\" || q == \"\\047\") && substr(v, n, 1) == q) {\n"
        "    QUOTE = q; return substr(v, 2, n - 2)\n"
        "  }\n"
        "  QUOTE = \"\\\"\"; return v\n"
        "}\n"
        "/^GPSD_OPTIONS=/ && !seen {\n"
        "  seen = 1\n"
        "  val = unquote(substr($0, index($0, \"=\") + 1))\n"
        "  k = split(val, f, /[ \\t]+/)\n"
        "  for (i = 1; i <= k; i++) if (f[i] == \"-n\") { print; verdict = \"have\"; next }\n"
        "  printf \"GPSD_OPTIONS=%s%s%s-n%s\\n\", QUOTE, val, (val == \"\" ? \"\" : \" \"), QUOTE\n"
        "  verdict = \"added\"; next\n"
        "}\n"
        "{ print }\n"
        "END { print (seen ? verdict : \"none\") > \"/dev/fd/3\" }\n"
        "LHPC_GPSD_AWK\n"
        "# Redirection ORDER matters: 3>&1 must duplicate the command-substitution pipe BEFORE\n"
        "# stdout is pointed at the new file, or fd 3 ends up writing the verdict into the file.\n"
        'GPSD_VERDICT="$(awk -f "$LHPC_TMPDIR/lhpc-gpsd.awk" "$GPSD_DEFAULT" \\\n'
        '  3>&1 1>"$GPSD_DEFAULT.lhpc-new")" || GPSD_VERDICT=error\n'
        'rm -f "$LHPC_TMPDIR/lhpc-gpsd.awk"\n'
        'case "$GPSD_VERDICT" in\n'
        "  added)\n"
        "    # Same transactional shape as the chrony edit: this file is the operator\'s, `cat`\n"
        "    # back would TRUNCATE it first, and a power cut on an RTC-less Pi is the case this\n"
        "    # whole feature exists for. Losing DEVICES leaves gpsd with no receiver at all.\n"
        '    chmod --reference="$GPSD_DEFAULT" "$GPSD_DEFAULT.lhpc-new" 2>/dev/null || exit 1\n'
        '    chown --reference="$GPSD_DEFAULT" "$GPSD_DEFAULT.lhpc-new" 2>/dev/null || exit 1\n'
        '    sync "$GPSD_DEFAULT.lhpc-new" 2>/dev/null || sync\n'
        '    mv -f "$GPSD_DEFAULT.lhpc-new" "$GPSD_DEFAULT"\n'
        '    sync "$(dirname "$GPSD_DEFAULT")" 2>/dev/null || true\n'
        "    ;;\n"
        "  have)\n"
        '    rm -f "$GPSD_DEFAULT.lhpc-new"\n'
        "    ;;\n"
        "  none)\n"
        '    rm -f "$GPSD_DEFAULT.lhpc-new"\n'
        '    printf "%s\\n" \'GPSD_OPTIONS="-n"\' >> "$GPSD_DEFAULT"\n'
        "    ;;\n"
        "  *)\n"
        '    rm -f "$GPSD_DEFAULT.lhpc-new"\n'
        '    echo "[bootstrap-deps] ERROR: could not rewrite $GPSD_DEFAULT; gpsd will not feed" >&2\n'
        '    echo "[bootstrap-deps]        chrony. Add -n to GPSD_OPTIONS by hand." >&2\n'
        "    TS_FAILED=1\n"
        "    ;;\n"
        "esac\n"
        "\n"
        "# 5. Installing and configuring does not make a running daemon adopt any of it. BEST\n"
        "#    EFFORT, deliberately: the image build runs this inside a container where there may\n"
        "#    be no running systemd, and `set -eu` would turn that into a failed image build. The\n"
        "#    packages own their own enablement at install time; this only covers the case where\n"
        "#    the daemon was already there. A failure is reported, never swallowed.\n"
        "# gpsd is restarted UNCONDITIONALLY whenever the time source is set up, and the result is\n"
        "# checked -- exactly like chrony below. Two reasons, both found by audit:\n"
        "#   - `enable --now` starts an INACTIVE unit and does nothing to an active one, so a\n"
        "#     refused restart used to be swallowed and the old process kept its old arguments\n"
        "#     while setup published success;\n"
        "#   - conditioning the restart on \"we just edited the file\" makes the failure permanent:\n"
        "#     on the retry the file already has -n, so nothing would ever restart it again.\n"
        "# Rerunning setup is the documented repair, so it must repair this too.\n"
        "systemctl enable gpsd >/dev/null 2>&1 || true\n"
        "if ! systemctl restart gpsd >/dev/null 2>&1; then\n"
        "  if [ -d /run/systemd/system ]; then\n"
        "    echo \"[bootstrap-deps] WARNING: gpsd could not be enabled/started. GPS time will not be\" >&2\n"
        "    echo \"[bootstrap-deps]          available until it is: sudo systemctl status gpsd\" >&2\n"
        "    TS_FAILED=1\n"
        "  else\n"
        "    echo \"[bootstrap-deps] NOTE: no running systemd here; enable gpsd after boot with\"\n"
        "    echo \"[bootstrap-deps]       sudo systemctl enable --now gpsd\"\n"
        "  fi\n"
        "fi\n"
        "if ! systemctl restart chrony >/dev/null 2>&1; then\n"
        "  # Distinguish the two reasons, because they need opposite reactions and the previous\n"
        "  # wording covered both with 'no action is needed'. On a box with a live systemd, a\n"
        "  # refused start is chronyd REJECTING the configuration -- and since this run already\n"
        "  # removed systemd-timesyncd, the box now has NO time daemon at all. Saying nothing is\n"
        "  # needed there is worse than saying nothing: it is a false all-clear. (This is exactly\n"
        "  # how a fatal drop-in error survived a full install on box E, 2026-09-14.)\n"
        "  if [ -d /run/systemd/system ]; then\n"
        "    echo \"[bootstrap-deps] ERROR: chrony REFUSED to start and systemd-timesyncd has been\" >&2\n"
        "    echo \"[bootstrap-deps]        removed, so this box now has NO time daemon. See why:\" >&2\n"
        "    echo \"[bootstrap-deps]          sudo systemctl status chrony\" >&2\n"
        "    echo \"[bootstrap-deps]          sudo /usr/sbin/chronyd -Q -f /etc/chrony/chrony.conf\" >&2\n"
        "    TS_FAILED=1\n"
        "  else\n"
        "    echo \"[bootstrap-deps] NOTE: no running systemd here (a container/image build), so\"\n"
        "    echo \"[bootstrap-deps]       chrony was not started. It reads this configuration at boot.\"\n"
        "  fi\n"
        "fi\n"
        "\n"
        "# The verdict. `set -eu` cannot carry this: every failure above is deliberately caught so\n"
        "# the remaining steps still run (a box that got chrony but not gpsd is better off than\n"
        "# one that got neither). What must NOT happen is reporting success over it.\n"
        "if [ \"$TS_FAILED\" -ne 0 ]; then\n"
        "  echo \"[bootstrap-deps] time source setup INCOMPLETE - see the errors above.\" >&2\n"
        "  exit 1\n"
        "fi\n"
        "\n"
        "# The boot floor, published LAST and only on success (see CLOCK_EPOCH_FLOOR). A floor, not\n"
        "# a clock: systemd takes the HIGHEST of its build time, this mtime and timesync/clock, so a\n"
        "# past date is inert.\n"
        "#\n"
        "# It is written after the verdict so a FAILED FIRST install does not leave it behind.\n"
        "# It is NOT a witness that the latest run succeeded, and nothing may treat it as one: it\n"
        "# is a persistent boot floor, so after one good install it survives every later failed\n"
        "# re-run. The dependency panel therefore reports what it can actually see (the files are\n"
        "# present). What keeps the repair reachable is the per-run stamp beside this file: a setup\n"
        "# that failed leaves the row UNSATISFIED, and dependencies.html renders the copybox only\n"
        "# then. Re-running bootstrap is the documented recovery path; hiding it was the bug.\n"
        'install -D -m 0644 /dev/null "$CLOCK_EPOCH"\n'
        "touch -d " + CLOCK_EPOCH_FLOOR + ' "$CLOCK_EPOCH"\n'
        "\n"
        "# Only now: this run did everything it promised.\n"
        'install -D -m 0644 /dev/null "$TS_STAMP"\n'
    )


def time_source_install_cmd(_user: str = "") -> str:
    """Paste-ready: everything the time source needs on THIS box. The copybox rendering; the
    bootstrap scaffold runs the same script from the same helper. NOTE it installs chrony,
    which REMOVES systemd-timesyncd -- on Trixie both declare Provides/Conflicts/Replaces:
    time-daemon, so apt resolves them as one time daemon."""
    return (
        # The install is NOT first. gpsd with Debian's USBAUTO claims the receiver the moment it
        # is installed, and on a u-blox that is irreversible from software (UBX binary mode
        # survives gpsd stopping). The ownership question therefore has to be settled before apt
        # runs, not after -- and for this surface it already is: the controller refuses to render
        # this command at all when its own config says `[gps] source = nmea` (see
        # `time_source_offer` below). The reminder stays in the text because the operator may
        # paste it somewhere other than the box it was rendered for.
        "# Only run this if lhpc is NOT configured for [gps] source = nmea: gpsd would take the\n"
        "# receiver, and a u-blox stays in UBX binary mode afterwards. Check with: lhpc gps show\n"
        "sudo apt install -y chrony gpsd fake-hwclock\n"
        "sudo sh -s <<'LHPC_TIME_SOURCE'\n"
        + time_source_setup_sh()
        + "LHPC_TIME_SOURCE"
    )


# The controller-side half of the same policy. The standalone script keeps its own shell
# pre-flight because it may run before lhpc exists at all; the running controller does NOT need
# to re-derive any of that -- it already knows its effective GPS source. One policy, two
# boundaries, no duplicated parser.
TIME_SOURCE_NMEA_REFUSAL = (
    "not offered: lhpc reads the receiver directly ([gps] source = nmea). Installing gpsd would "
    "take the device, and a u-blox stays in UBX binary mode after gpsd stops. Switch [gps] "
    "source to gpsd first, or keep nmea and set the clock another way."
)


TIME_SOURCE_UNKNOWN_REFUSAL = (
    "not offered: lhpc could not read its own [gps] configuration, so it cannot tell whether this "
    "box reads the receiver directly (source = nmea). Installing gpsd would take the device, and a "
    "u-blox stays in UBX binary mode after gpsd stops. Fix the configuration, or run "
    "`sudo bash bootstrap-deps.sh --spi-mode skip` which performs the same check as root."
)

# The sentinel for "could not determine", kept DISTINCT from the absent-config default. Absent
# config legitimately means `auto` (the fresh-image case) and is safe to offer; an unreadable or
# unparseable one means ownership is unknown, and unknown must fail the same way it does in the
# bootstrap pre-flight.
TIME_SOURCE_SOURCE_UNKNOWN = "\x00unknown"


def time_source_offer(gps_source: str, user: str = "") -> tuple[bool, str]:
    """(offer, text) for the dependency panel's time-source copybox.

    Returns the paste-ready command, or a refusal. Two things are refused, for the same reason:
    a box configured for direct NMEA, and a box whose configuration could not be read at all.

    The second case used to render an installable command, on the reasoning that the standalone
    script would catch it later. It would not: the copybox does not invoke the standalone script,
    it runs `apt install` directly a few lines below its own caution. Uncertainty about receiver
    ownership fails safe here exactly as it does in the bootstrap pre-flight (audit).
    """
    source = (gps_source or "").strip().lower()
    if source == TIME_SOURCE_SOURCE_UNKNOWN:
        return False, TIME_SOURCE_UNKNOWN_REFUSAL
    if source == "nmea":
        return False, TIME_SOURCE_NMEA_REFUSAL
    return True, time_source_install_cmd(user)


def render_bootstrap_script(raw_cmds, revision: str = "", gui_cmds=(), gps_cmds=()) -> str:
    """Render every declared dependency-remediation command into ONE hardened, executable bootstrap
    script. Standalone `apt install` commands merge into a single deduplicated `apt-get install`
    run FIRST (so tools like curl/gpg exist before the blocks that use them). Group grants are
    re-rendered to a validated non-root operator; SPI/config.txt is re-rendered behind a required
    `--spi-mode` (soft-cs | hardware-cs | skip), idempotent and fail-closed on a conflicting existing
    config; the OBS repo block is emitted verbatim (already scoped-keyring + HTTPS). On a small-RAM
    machine (MemTotal < ~600MB) it also provisions a disk swapfile (default 768M, range 64-16384,
    at a priority BELOW zram) as OOM insurance for the firmware build: the image is built in a
    same-directory temp and renamed into place (an interrupted run never leaves a half-formatted
    swapfile), the fstab entry is published transactionally so there is always EXACTLY ONE canonical
    line, and success requires BOTH an active swap and that persistent declaration — so a re-run
    repairs whichever is missing rather than merely no-oping. A non-regular leaf (symlink, dir,
    FIFO, device) at the swap path is refused untouched. When swap is REQUIRED (low RAM, no other
    disk swap) and cannot be provisioned, the script exits 4; `--no-swapfile` is the way to proceed
    without it. Output is deterministic (packages sorted) so the shipped snapshot is stable.
    lhpc NEVER runs these."""
    # GUI-ONLY commands are bucketed separately and emitted behind --with-gui; they are NEVER part
    # of the default package list (a headless image must not grow an X/Wayland dev chain).
    gui_pkgs: list[str] = []
    gui_blocks: list[str] = []
    seen_gui: set[str] = set()
    # GPS-ONLY packages: needed only if the operator later points the position source at a
    # gpsd on THIS box. A fresh image has no GPS setting yet, so installing a daemon for a
    # feature most boxes never enable would be wrong — opt in with --with-gps.
    gps_pkgs: list[str] = []
    seen_gps: set[str] = set()
    for cmd in (gps_cmds or ()):
        c = (cmd or "").strip()
        if not c:
            continue
        m = _APT_INSTALL_RE.match(c) if "\n" not in c else None
        if m:
            for pkg in m.group(1).split():
                if pkg.startswith("-") or pkg in seen_gps:
                    continue
                seen_gps.add(pkg)
                gps_pkgs.append(pkg)
    for cmd in (gui_cmds or ()):
        c = (cmd or "").strip()
        if not c:
            continue
        m = _APT_INSTALL_RE.match(c) if "\n" not in c else None
        if m:
            for pkg in m.group(1).split():
                if pkg.startswith("-") or pkg in seen_gui:
                    continue
                seen_gui.add(pkg)
                gui_pkgs.append(pkg)
        elif c not in seen_gui:
            seen_gui.add(c)
            gui_blocks.append(c)
    apt_pkgs: list[str] = []
    seen_pkgs: set[str] = set()
    blocks: list[str] = []
    seen_blocks: set[str] = set()
    for cmd in raw_cmds:
        c = (cmd or "").strip()
        if not c:
            continue
        m = _APT_INSTALL_RE.match(c) if "\n" not in c else None
        if m:
            for pkg in m.group(1).split():
                if pkg.startswith("-"):               # drop flag tokens (-y, --no-install-recommends…)
                    continue
                if pkg not in seen_pkgs:
                    seen_pkgs.add(pkg)
                    apt_pkgs.append(pkg)
        elif c not in seen_blocks:
            seen_blocks.add(c)
            blocks.append(c)

    # Classify the non-apt blocks; group grants + SPI are transformed, not emitted verbatim.
    repo_blocks: list[str] = []
    disable_blocks: list[str] = []
    other_blocks: list[str] = []
    group_names: list[str] = []
    spi_overlay = ""
    for b in blocks:
        low = b.lower()
        mg = _USERMOD_RE.search(b)
        if mg:
            for g in mg.group(1).split(","):
                if g and g not in group_names:
                    group_names.append(g)
            continue
        if _is_spi_block(low):
            mo = _OVERLAY_RE.search(b)
            if mo:
                spi_overlay = mo.group(1)
            continue
        if "systemctl disable" in low:
            disable_blocks.append(b)
            continue
        if _is_repo_block(low):
            repo_blocks.append(b)
            continue
        other_blocks.append(b)
    spi_overlay = spi_overlay or "spi0-0cs"
    groups_csv = ",".join(group_names)

    L: list[str] = []

    def out(*xs: str) -> None:
        L.extend(xs)

    out("#!/usr/bin/env bash",
        "#",
        "# bootstrap-deps.sh — GENERATED by `lhpc deps --script`; do NOT hand-edit (regenerate instead).",
        (f"# Source manifest dependency revision: {revision}" if revision else "#"),
        "#",
        "# Run ONCE on a fresh Raspberry Pi OS Trixie (arm64) image, BEFORE cloning/installing",
        "# loraham-pi-control. MUST run as root: `sudo bash bootstrap-deps.sh` (the documented call).",
        "# The script itself never invokes sudo — once root, every privileged command runs directly, so",
        "# it works where sudo is absent, unconfigured, or cannot prompt (unattended runs). A non-root",
        "# invocation refuses up front (exit 10) — EXCEPT `--dry-run` and -h/--help, which stay",
        "# unprivileged on purpose: the pre-flight is read-only and zero-trust, meant to be vetted",
        "# BEFORE the script is ever granted root. lhpc itself never runs privileged commands.",
        "#",
        "#   bootstrap-deps.sh --spi-mode <soft-cs|hardware-cs|skip> [--operator-user <name>]"
        " [--no-swapfile] [--swap-size <MB>] [--with-gui] [--with-gps] [--no-time-source]"
        " [--keep-wifi-powersave] [--no-power-controls] [--no-network-controls]",
        "#   bootstrap-deps.sh --dry-run        PRE-FLIGHT: simulate only, change nothing",
        "#     soft-cs      software CS (/dev/spidev0.0): dtparam=spi=on + dtoverlay="
        + spi_overlay + "  (LoRaHAM Pi / Uputronics rigs, single-radio AND dual Uputronics:"
        " daemon + meshtasticd drive CS7/CS8 as GPIOs — the kernel must NOT claim CE0/CE1)",
        "#     hardware-cs  kernel-driven CE0+CE1, no overlay: dtparam=spi=on only  (only for boards"
        " that really use kernel chip-selects; NOT for Uputronics — CE0/CE1=GPIO7/8 would collide"
        " with the daemon's GPIO chip-selects)",
        "#     skip         no boot-config change (SPI already configured)",
        "#     --no-swapfile      do NOT provision the small-RAM disk swapfile (see below)",
        "#     --swap-size <MB>   swapfile size when provisioned (default 768)",
        "#     --no-power-controls  do NOT install the polkit rule that lets the operator",
        "#                        use the console's Reboot/Shut down buttons (installed by default;",
        "#                        the rule file is " + POWER_RULE_PATH + ")",
        "#     --no-network-controls  do NOT install the polkit rule that lets the operator join",
        "#                        Wi-Fi networks from the console's Network panel (installed by",
        "#                        default; the rule file is " + NETWORK_RULE_PATH + "; the panel",
        "#                        itself appears only on AP-managed boxes — desktop images pass",
        "#                        this flag). polkitd is installed unless BOTH power AND network",
        "#                        controls are opted out.",
        "# Exit codes: 2 usage · 3 conflicting SPI config · 4 required swap unprovisioned ·",
        "#             5 --dry-run could not resolve · 6 --dry-run found graphical packages ·",
        "#             7 hardware group grant failed · 8 system nginx.service could not be confirmed"
        " stopped ·",
        "#             9 systemd unit files could not be inspected (fail-closed — a competing service",
        "#             may be active) · 10 not running as root (run: sudo bash bootstrap-deps.sh ...).",
        "#             Steps that can legitimately fail on a CLEAN",
        "#             image (no packaged meshtasticd unit) are GUARDED — the script runs under",
        "#             `set -e`, so an unguarded one would abort before this summary.",
        "#     --dry-run          simulate the DEFAULT apt transaction and exit WITHOUT touching the",
        "#                        system. Exits 0 only when the transaction resolves cleanly and pulls",
        "#                        no graphical/audio stack; nonzero when it cannot be resolved or would",
        "#                        install one. Run this FIRST on a fresh image — the package closure is",
        "#                        then known before anything is installed, not discovered mid-install.",
        "#     --with-gps         COMPATIBILITY ONLY: gpsd is installed by the default time source,",
        "#                        so this flag now installs nothing extra and only prints a note",
        "#                        as the shared position source (see `lhpc gps`). Not needed for a",
        "#                        gpsd on another machine.",
        "#     --no-time-source   do NOT install chrony/gpsd or configure the clock (default is ON;",
        "#                        chrony REPLACES systemd-timesyncd, and a direct-NMEA box needs this)",
        "#     --with-gui         ALSO install the GUI-only dependencies (GTK/Tk) that the desktop"
        " components need. OMITTED BY DEFAULT: this script must never pull a graphical stack onto a"
        " headless image. It installs GUI application LIBRARIES only — never a desktop environment,"
        " display manager or X/Wayland server — and assumes you already run a graphical session.",
        "#     --keep-wifi-powersave  Do NOT touch Wi-Fi settings. BY DEFAULT — but ONLY when the install"
        " actually runs over Wi-Fi (the default route's device is a NetworkManager TYPE=wifi device, or"
        " no route/type is determinable); a LAN-carried install leaves Wi-Fi untouched automatically —"
        " this script DISABLES Wi-Fi"
        " power-save on a NetworkManager-managed wlan interface, because on a Pi Zero 2W the brcmfmac"
        " Wi-Fi firmware drops (the interface can vanish until reboot) under a long build's sustained"
        " CPU load — which breaks a headless install over Wi-Fi. It writes ONE NetworkManager drop-in"
        " (revert: rm /etc/NetworkManager/conf.d/wifi-nopowersave.conf && systemctl restart"
        " NetworkManager). Pass this flag to leave Wi-Fi untouched (e.g. Ethernet, or you manage it"
        " yourself); a warning then explains the risk.",
        "#",
        "# The apt package set is IDENTICAL on a Pi Zero 2W and a Pi 5; only the SPI mode is hardware-",
        "# specific. QEMU + PlatformIO are provisioned later by the MANAGED build (`lhpc build`), not here.",
        "set -euo pipefail",
        "")

    out("usage() {",
        '\techo "usage: bootstrap-deps.sh --spi-mode <soft-cs|hardware-cs|skip> [--operator-user <name>]'
        ' [--no-swapfile] [--swap-size <MB>] [--with-gui] [--with-gps] [--no-time-source]'
        ' [--keep-wifi-powersave]'
        ' [--no-power-controls] [--no-network-controls]" >&2',
        '\techo "       bootstrap-deps.sh --dry-run   (simulate the default apt transaction; no changes)" >&2',
        "}",
        "")

    out('BOOTSTRAP_FAILED=""',
        'SPI_MODE=""',
        'OPERATOR_USER=""',
        'NO_SWAPFILE=""',
        'NO_POWER=""',
        'NO_NETWORK=""',
        'SWAP_SIZE_MB=""',
        'WITH_GUI=""',
        'WITH_GPS=""',
        'NO_TIME_SOURCE=""',
        'KEEP_WIFI=""',
        'DRY_RUN=""',
        "while [ $# -gt 0 ]; do",
        '\tcase "$1" in',
        '\t\t--spi-mode) SPI_MODE="${2:?--spi-mode needs a value}"; shift 2 ;;',
        '\t\t--operator-user) OPERATOR_USER="${2:?--operator-user needs a value}"; shift 2 ;;',
        '\t\t--no-swapfile) NO_SWAPFILE=1; shift ;;',
        '\t\t--no-power-controls) NO_POWER=1; shift ;;',
        '\t\t--no-network-controls) NO_NETWORK=1; shift ;;',
        '\t\t--with-gui) WITH_GUI=1; shift ;;',
        '\t\t--with-gps) WITH_GPS=1; shift ;;',
        '\t\t--no-time-source) NO_TIME_SOURCE=1; shift ;;',
        '\t\t--keep-wifi-powersave) KEEP_WIFI=1; shift ;;',
        '\t\t--dry-run) DRY_RUN=1; shift ;;',
        '\t\t--swap-size) SWAP_SIZE_MB="${2:?--swap-size needs a value (MB)}"; shift 2 ;;',
        "\t\t-h|--help) usage; exit 0 ;;",
        '\t\t*) echo "ERROR: unknown argument: $1" >&2; usage; exit 2 ;;',
        "\tesac",
        "done",
        "",
        "# polkitd (authorization daemon for the console's power buttons AND its Network panel —",
        "# systemd only Suggests it) joins the ONE merged apt transaction below AND its --dry-run",
        "# simulation. It is omitted only when BOTH features are opted out — a rule without its",
        "# daemon would be dead weight, and either rule alone still needs polkitd.",
        'POLKIT_PKG="polkitd"',
        '[ -n "$NO_POWER" ] && [ -n "$NO_NETWORK" ] && POLKIT_PKG=""',
        "",
        "# The time source (chrony disciplines the clock, gpsd feeds it GPS time). Default ON:",
        "# a Pi has no battery-backed clock, so an offline box boots with the last time it wrote",
        "# and every log line and certificate after that is wrong. Subtracted by --no-time-source,",
        "# exactly like $POLKIT_PKG above, so it rides the SAME merged transaction the dry-run",
        "# simulates. NOTE chrony REPLACES systemd-timesyncd (both Provides/Conflicts/Replaces:",
        "# time-daemon on Trixie) — that is why the opt-out exists.",
        'TIME_PKGS="chrony gpsd fake-hwclock"',
        "",
        "# PRE-FLIGHT, before the packages are even chosen: gpsd must never claim a receiver that",
        "# an LHPC `nmea` source reads DIRECTLY. This is not only device contention — gpsd switches",
        "# u-blox receivers into UBX binary mode and they STAY there after gpsd stops, so a later",
        "# `nmea` read finds a stream with no NMEA in it until the chip is reset with an external",
        "# tool. Uncertainty fails SAFE (skip): a skipped box is one re-run away from the feature,",
        "# a UBX-locked receiver is not. Absent config = fresh box = proceed, which is the case the",
        "# whole feature exists for.",
        'if [ -z "$NO_TIME_SOURCE" ]; then',
        '\t# Resolve the operator the SAME WAY the script does further down (explicit',
        '\t# --operator-user, else SUDO_USER). The documented command is `sudo bash',
        '\t# bootstrap-deps.sh --spi-mode soft-cs` with NO --operator-user, so keying this check',
        '\t# on the flag alone left the ordinary existing-box path with no check at all -- the',
        '\t# image build passes the flag and was safe, which is exactly why it went unnoticed.',
        '\tTS_USER="$OPERATOR_USER"',
        '\tif [ -z "$TS_USER" ] && [ -n "${SUDO_USER:-}" ] && [ "${SUDO_USER:-}" != "root" ]; then',
        '\t\tTS_USER="$SUDO_USER"',
        '\tfi',
        '\t# `|| true`, and only with a user to look up: this runs BEFORE operator validation,',
        '\t# and `getent passwd ""` exits 2 -- under set -e + pipefail that killed the whole',
        '\t# script before it printed anything at all.',
        '\tTS_HOME=""',
        '\tif [ -n "$TS_USER" ]; then',
        '\t\tTS_HOME="$(getent passwd "$TS_USER" 2>/dev/null | cut -d: -f6 || true)"',
        '\tfi',
        '\tTS_ROOT="${LHPC_RUNTIME_ROOT:-}"',
        '\tif [ -z "$TS_ROOT" ] && [ -n "$TS_HOME" ]; then TS_ROOT="$TS_HOME/loraham-pi-control"; fi',
        '\t# A DRY RUN mutates nothing, so there is no receiver to lose and no reason to',
        '\t# suppress anything: the simulation must show the DEFAULT package set, which is the',
        '\t# whole point of the closure gate for two newly-default packages. Only a config that',
        '\t# POSITIVELY reads `source = nmea` still suppresses, so the simulation matches what a',
        '\t# real run on that box would do.',
        '\tif [ -z "$TS_ROOT" ] && [ -z "$DRY_RUN" ]; then',
        '\t\t# Cannot locate an lhpc configuration at all, so direct NMEA cannot be ruled out.',
        '\t\t# Fail SAFE: a skipped box is one re-run away from the feature, a u-blox left in UBX',
        '\t\t# mode needs an external tool. A fresh image is unaffected -- the image build passes',
        '\t\t# --operator-user explicitly, and a `sudo bash` install has SUDO_USER.',
        '\t\tNO_TIME_SOURCE=1',
        '\t\techo "[bootstrap-deps] time source SKIPPED: could not determine the operator (no"',
        '\t\techo "                  --operator-user, no SUDO_USER) so an lhpc [gps] source = nmea"',
        '\t\techo "                  cannot be ruled out. Re-run with --operator-user <user>, or with"',
        '\t\techo "                  --no-time-source to skip it deliberately."',
        '\tfi',
        '\tTS_CFG="$TS_ROOT/config/local.toml"',
        '\tif [ -n "$TS_ROOT" ] && [ -e "$TS_CFG" ]; then',
        '\t\tif [ ! -r "$TS_CFG" ] && [ -z "$DRY_RUN" ]; then',
        '\t\t\tNO_TIME_SOURCE=1',
        '\t\t\techo "[bootstrap-deps] time source SKIPPED: $TS_CFG exists but cannot be read, so an"',
        '\t\t\techo "                  lhpc [gps] source = nmea cannot be ruled out. Re-run with"',
        '\t\t\techo "                  --no-time-source to silence this, or make the file readable."',
        '\t\telif awk \'/^[ \\t]*\\[/ { insect = ($0 ~ /^[ \\t]*\\[gps\\]/) }',
        '\t\t              insect && /^[ \\t]*source[ \\t]*=/ && /nmea/ { found = 1 }',
        '\t\t              END { exit(found ? 0 : 1) }\' "$TS_CFG"; then',
        '\t\t\tNO_TIME_SOURCE=1',
        '\t\t\techo "[bootstrap-deps] time source SKIPPED: lhpc [gps] source = nmea reads the receiver"',
        '\t\t\techo "                  directly, and gpsd must not also own it (it would also leave a"',
        '\t\t\techo "                  u-blox in UBX binary mode). Switch [gps] source to gpsd and"',
        '\t\t\techo "                  re-run to enable GPS as a time source."',
        '\t\tfi',
        '\tfi',
        "fi",
        '[ -n "$NO_TIME_SOURCE" ] && TIME_PKGS=""',
        "")

    # The denylist scan as ONE shell line, composed here so the nested awk/grep quoting stays
    # readable instead of being escaped through a Python string literal.
    dry_bad_line = ('\tDRY_BAD="$(printf \'%s\\n\' "$DRY_OUT" | awk \'/^Inst /{print $2}\''
                    ' | grep -E \'' + _DENY_RE + '\' | sort -u || true)"')

    # PRE-FLIGHT: simulate and exit BEFORE every other validation and before any mutation. It needs
    # no --spi-mode/--operator-user because it only answers "what would apt install here?".
    if apt_pkgs:
        pkgs_line = " ".join(sorted(apt_pkgs))
        out("# --- --dry-run: simulate the DEFAULT apt transaction, change NOTHING -----------------------",
            "# The blocker this guards against: discovering the real package closure only while",
            "# installing on hardware. `apt-get install -s` resolves it against the local apt database",
            "# without touching the system, so a from-zero run can be vetted first. The GUI opt-in is NOT",
            "# part of this verdict — the default transaction is what a headless image gets.",
            'if [ -n "$DRY_RUN" ]; then',
            '	echo "[bootstrap-deps] DRY RUN — simulating the DEFAULT (fresh-image) apt transaction; nothing is installed or changed."',
            '	echo "[bootstrap-deps]   It resolves against an EMPTY package database, so the verdict is the FULL closure a fresh image gets, not a delta for this box."',
            '	echo "[bootstrap-deps]   The time-source pre-flight HAS already run, so a box configured for [gps] source = nmea shows the set without chrony/gpsd."',
            "	if ! command -v apt-get >/dev/null 2>&1; then",
            '		echo "ERROR: dry-run: apt-get is not available — cannot simulate the transaction." >&2',
            "		exit 5",
            "	fi",
            f'	DRY_PKGS="{pkgs_line} $POLKIT_PKG $TIME_PKGS"',
            "	# Simulate the DEFAULT transaction: same flags, same package set. The time-source",
            "	# pre-flight runs ABOVE this block, so $TIME_PKGS is already empty on a box configured",
            "	# for direct NMEA and the simulation reflects that (verified on box E, 2026-09-14).",
            "	# What it is still NOT is a per-box delta: -o Dir::State::status=/dev/null resolves",
            "	# against an EMPTY installed-package database, so the verdict is the full closure a",
            "	# FRESH image would get. Both halves have to be said, or the gate reads as a promise",
            "	# about this machine.",
            "\t# -o Dir::State::status=/dev/null resolves against an EMPTY installed-package",
            "\t# database, so the verdict is the FULL closure a fresh image would get, not the delta",
            "\t# for THIS machine. Without it a box that already has the packages reports a clean",
            "\t# transaction and the cascade a fresh install would pull stays invisible.",
            '\tif ! DRY_OUT="$(apt-get install -s -y --no-install-recommends '
            '-o Dir::State::status=/dev/null $DRY_PKGS 2>&1)"; then',
            '		printf "%s\\n" "$DRY_OUT" >&2',
            '		echo "ERROR: dry-run: apt could not resolve the declared package set (see above)." >&2',
            '		echo "       Run: sudo apt-get update  — a stale or incomplete package list is the usual cause." >&2',
            "		exit 5",
            "	fi",
            '	printf "%s\\n" "$DRY_OUT" | grep -E "^Inst " || true',
            '	DRY_N="$(printf "%s\\n" "$DRY_OUT" | grep -cE "^Inst " || true)"',
            '	echo "[bootstrap-deps] the default transaction would install/upgrade ${DRY_N} package(s)."',
            "	# Fail-closed denylist: a headless rig must never acquire a display or audio stack, whether",
            "	# it arrives as a hard Depends or as a Recommends.",
            dry_bad_line,
            '	if [ -n "$DRY_BAD" ]; then',
            '		echo "ERROR: dry-run: the default transaction would install graphical/audio packages:" >&2',
            "		printf '  %s\\n' $DRY_BAD >&2",
            '		echo "       A headless install must pull none of these. Report this rather than proceeding." >&2',
            "		exit 6",
            "	fi",
            '	echo "[bootstrap-deps] dry run OK — resolved cleanly, no GTK/Tk/SDL/X11/Wayland/Mesa/LLVM/PulseAudio/libinput/xkbcommon, no display server or desktop environment."',
            '	[ -n "$WITH_GUI" ] && echo "[bootstrap-deps] note: --with-gui additionally installs the GUI-only packages on a real run; they are opt-in and not part of this verdict."',
            "	exit 0",
            "fi",
            "")

    out("# ROOT REQUIRED (exit 10) for everything EXCEPT -h/--help and --dry-run, which are answered",
        "# above: the pre-flight is deliberately READ-ONLY and ZERO-TRUST — an operator vets what the",
        "# script would install BEFORE ever granting it root. Every mutating path below runs as root:",
        "# the documented call is `sudo bash bootstrap-deps.sh`, and the script never invokes sudo",
        "# itself, so it also works where sudo is absent, unconfigured, or cannot prompt (unattended",
        "# runs). Operator resolution: $OP comes from SUDO_USER (or --operator-user) and uid-0",
        "# operators are refused — root never receives group grants.",
        'if [ "$(id -u)" -ne 0 ]; then',
        '\techo "ERROR: bootstrap-deps.sh must run as root — run: sudo bash bootstrap-deps.sh ${SPI_MODE:+--spi-mode $SPI_MODE}" >&2',
        '\techo "       (only --dry-run and -h/--help work unprivileged)" >&2',
        "\texit 10",
        "fi",
        "")

    out("# Validate ALL options up front — BEFORE any apt / repository / boot-config / group mutation.",
        'case "$SPI_MODE" in',
        "\tsoft-cs|hardware-cs|skip) ;;",
        '\t"") echo "ERROR: --spi-mode is required (soft-cs | hardware-cs | skip)." >&2; usage; exit 2 ;;',
        '\t*) echo "ERROR: unknown --spi-mode: $SPI_MODE (soft-cs | hardware-cs | skip)." >&2; exit 2 ;;',
        "esac",
        'if [ -n "$SWAP_SIZE_MB" ]; then',
        '\tif ! printf "%s" "$SWAP_SIZE_MB" | grep -qE "^[1-9][0-9]*$"; then',
        '\t\techo "ERROR: --swap-size must be a positive integer number of MB: $SWAP_SIZE_MB"'
        " >&2; exit 2",
        "\tfi",
        "\t# Digit CAP first: a 20-digit argument would overflow the `[ -gt ]` arithmetic below,",
        "\t# so bound the LENGTH before the value is ever compared numerically.",
        '\tif ! printf "%s" "$SWAP_SIZE_MB" | grep -qE "^[0-9]{1,5}$" \\',
        '\t\t\t|| [ "$SWAP_SIZE_MB" -lt 64 ] || [ "$SWAP_SIZE_MB" -gt 16384 ]; then',
        '\t\techo "ERROR: --swap-size out of range (64-16384 MB): $SWAP_SIZE_MB" >&2; exit 2',
        "\tfi",
        "fi",
        "")

    # READ-ONLY PRE-FLIGHT: fail-closed environment checks that must never abort MID-RUN (a
    # conflicting boot config at the config.txt step, an unqueryable systemd at the disable step)
    # after apt has installed ~70 packages and the system nginx has been disabled. They
    # run HERE, in the up-front validation block before ANY apt / service / config / swap /
    # group mutation, so a refusal leaves the system COMPLETELY untouched. Exit codes are unchanged
    # (3 = conflicting SPI config, 8 = systemd not inspectable). The SPI section below now only
    # APPENDS its idempotent lines; the conflict DETECTION lives here.
    out("# --- read-only pre-flight: reject a conflicting/blocked environment BEFORE any mutation ------",
        'CONFIG_TXT="${CONFIG_TXT:-/boot/firmware/config.txt}"',
        'case "$SPI_MODE" in',
        "\tsoft-cs)",
        f'\t\tif grep -qE "^dtparam=spi=on" "$CONFIG_TXT" 2>/dev/null && ! grep -qxF "dtoverlay={spi_overlay}" "$CONFIG_TXT" 2>/dev/null; then',
        f'\t\t\techo "ERROR: $CONFIG_TXT enables SPI without the soft-CS overlay (hardware-CS?) —'
        f" refusing to add a conflicting overlay. Either add 'dtoverlay={spi_overlay}' to $CONFIG_TXT"
        " (soft-CS — the LoRaHAM Pi / Uputronics case), or re-run with --spi-mode hardware-cs if this"
        ' rig really uses kernel chip-selects, or --spi-mode skip to leave the boot config alone." >&2; exit 3',
        "\t\tfi ;;",
        "\thardware-cs)",
        f'\t\tif grep -qxF "dtoverlay={spi_overlay}" "$CONFIG_TXT" 2>/dev/null; then',
        f"\t\t\techo \"ERROR: $CONFIG_TXT has the soft-CS overlay (dtoverlay={spi_overlay}) —"
        " incompatible with hardware-cs (needs kernel CE0/CE1). Either remove that overlay line to use"
        " hardware chip-selects, or re-run with --spi-mode soft-cs (the LoRaHAM Pi / Uputronics case),"
        ' or --spi-mode skip to leave the boot config alone." >&2; exit 3',
        "\t\tfi ;;",
        "esac",
        "")

    if any(pkg == "nginx" or pkg.startswith("nginx-") for pkg in apt_pkgs) or disable_blocks:
        out("# systemd must be queryable NOW (a broken/absent manager aborts BEFORE ~70 packages land,",
            "# not after): the disable steps below need it to find a competing system nginx.service and",
            "# any packaged meshtasticd.service. Fail CLOSED, up front, system untouched.",
            "if ! systemctl list-unit-files --no-legend >/dev/null 2>&1; then",
            '\techo "ERROR: cannot inspect systemd unit files (is this a systemd system, and is it'
            " reachable?). Bootstrap must check for a competing system nginx.service (and any packaged"
            " meshtasticd.service) before it can safely proceed — refusing to continue so the system"
            ' stays untouched. Resolve systemd access and re-run." >&2',
            "\texit 8",
            "fi",
            "")

    # $OP is needed by the hardware-group grants AND by the power/network-controls rules. With
    # groups declared it must ALWAYS resolve (grants are unconditional); with none, it resolves
    # only when at least one of the rules is enabled — a box run with both --no-*-controls
    # flags and no grants is never refused for an operator it does not need.
    _op_lines = (
        "# Operator for the group grants / power controls: explicit --operator-user, else SUDO_USER",
        "# when run under sudo, else the invoking user. NEVER grant to root; require a real account.",
        'OP="$OPERATOR_USER"',
        'if [ -z "$OP" ]; then',
        '\tif [ -n "${SUDO_USER:-}" ]; then OP="$SUDO_USER"; else OP="$(id -un)"; fi',
        "fi",
        'if [ -z "$OP" ] || [ "$OP" = "root" ]; then',
        '\techo "ERROR: no non-root operator for group grants / power controls — re-run as the operator with sudo, or pass --operator-user <name>." >&2; exit 2',
        "fi",
        'if ! id -u "$OP" >/dev/null 2>&1; then',
        '\techo "ERROR: --operator-user \\"$OP\\" is not a real account." >&2; exit 2',
        "fi",
        'if [ "$(id -u "$OP")" -eq 0 ]; then',
        '\techo "ERROR: refusing to grant hardware groups or power controls to a uid-0 account (\\"$OP\\")." >&2; exit 2',
        "fi",
    )
    if groups_csv:
        out(*_op_lines, "")
    else:
        out('if [ -z "$NO_POWER" ] || [ -z "$NO_NETWORK" ]; then')
        out(*("\t" + ln for ln in _op_lines))
        out("fi", "")


    # TRI-STATE, FAIL-CLOSED unit-file presence. Two hazards are handled here:
    #
    #  (1) PIPELINE INVERSION: `systemctl list-unit-files | grep -q '^unit'` INVERTS under
    #      `set -o pipefail` — grep -q exits at the (early) match, systemctl dies of SIGPIPE (141),
    #      pipefail makes the pipeline non-zero → the guard wrongly takes its ELSE branch. So no pipe
    #      is used; presence is read from command-substitution OUTPUT.
    #
    #  (2) FAIL-OPEN INSPECTION: `list-unit-files UNIT` exits 1 for BOTH 'no match' AND its own
    #      errors, so the filtered exit code cannot tell 'genuinely absent' from 'could not inspect'.
    #      Treating an inspection FAILURE as 'absent' would fail OPEN — skipping the disable and
    #      reporting success while a competing root service (nginx / packaged meshtasticd) is still
    #      active. So systemd is first probed for queryability (an unfiltered listing); if THAT
    #      fails we return 2 (inspection failed) and the caller ABORTS rather than assuming absence.
    #
    # Returns: 0 present · 1 genuinely absent · 2 inspection failed.
    out("unit_present() {",
        '\tsystemctl list-unit-files --no-legend >/dev/null 2>&1 || return 2   # systemd not queryable',
        '\t[ -n "$(systemctl list-unit-files --no-legend "$1" 2>/dev/null)" ]  # 0 present, 1 absent',
        "}",
        "")

    if apt_pkgs:
        out("# --- APT packages (merged; installed FIRST so curl/etc. exist before the blocks below) ----",
            "# --no-install-recommends: Recommends are what turned a headless install into a desktop one.",
            "# Nothing here needs them — e.g. git Recommends openssh-client, which Recommends xauth, which",
            "# Depends on libX11. Only hard Depends are installed, so the closure stays display-free.",
            "apt-get update",
            "apt-get install -y --no-install-recommends \\")
        pk = sorted(apt_pkgs)
        for p in pk:
            out(f"    {p} \\")
        # $POLKIT_PKG (polkitd, or empty when BOTH power and network controls are opted out)
        # rides the SAME merged transaction the dry-run simulates — an empty unquoted
        # expansion adds no argument.
        out("    $POLKIT_PKG $TIME_PKGS")
        out("")

        if any(pkg == "nginx" or pkg.startswith("nginx-") for pkg in pk):
            # Debian's nginx package ENABLES AND STARTS a root `nginx.service` on install. LHPC wants
            # the BINARY only: its canonical frontend is the user-manager unit `lhpc-nginx.service`,
            # and a root nginx left running owns the ports that unit needs. So the package stays
            # installed and the SYSTEM service is turned off.
            #
            # NOT tolerated the way the packaged-meshtasticd step is: there, "no such unit" is the
            # normal clean-image state. Here the unit exists because we just installed it, so a
            # FAILURE to stop/disable means a root nginx is still holding those ports — that is a
            # broken frontend, not a cosmetic warning, and `|| true` would hide it.
            # `rc=0; unit_present … || rc=$?` captures the tri-state WITHOUT tripping `set -e` (the
            # command is part of an || list). An inspection FAILURE (rc 2) must abort, not fall
            # through to "nothing to disable" — otherwise a root nginx we could not even inspect
            # stays up while bootstrap claims success. exit 8 is reused (same meaning: the system
            # nginx could not be confirmed stopped).
            out("# --- system nginx: keep the package, disable the ROOT service ----------------------------",
                "nginx_rc=0; unit_present nginx.service || nginx_rc=$?",
                'case "$nginx_rc" in',
                "\t0)",
                "\t\t# Only ANNOUNCE a change if there is one: an already-disabled+inactive unit is",
                "\t\t# reported as such rather than claiming we 'disabled' it.",
                "\t\tif systemctl is-enabled --quiet nginx.service 2>/dev/null"
                " || systemctl is-active --quiet nginx.service 2>/dev/null; then",
                "\t\t\tif systemctl disable --now nginx.service; then",
                '\t\t\t\techo "[bootstrap-deps] disabled the system nginx.service (lhpc serves via the'
                ' lhpc-nginx user unit; the nginx PACKAGE stays installed)."',
                "\t\t\telse",
                '\t\t\t\techo "ERROR: could not stop/disable the system nginx.service. A root nginx still'
                ' owns the web ports, so the lhpc frontend cannot bind them. Resolve this and re-run."'
                " >&2",
                "\t\t\t\texit 8",
                "\t\t\tfi",
                "\t\telse",
                '\t\t\techo "[bootstrap-deps] system nginx.service is already disabled — nothing to change."',
                "\t\tfi ;;",
                '\t1) echo "[bootstrap-deps] no system nginx.service present — nothing to disable." ;;',
                '\t2) echo "ERROR: could not inspect systemd unit files — cannot confirm the system'
                ' nginx.service is stopped. Refusing to continue with a possibly-active root nginx'
                ' holding the web ports." >&2; exit 8 ;;',
                "esac",
                "")

    for b in repo_blocks:
        out("# --- third-party apt repository (dedicated keyring + signed-by, HTTPS) -------------------",
            _desudo(b), "")

    if gui_pkgs or gui_blocks:
        # Emitted AFTER the repo block on purpose: the default package list must stay the
        # contiguous first apt section (the ordering gate greps the span between `apt-get install`
        # and the OBS URL to prove curl/gpg land before the repo that uses them).
        out("# --- GUI-only dependencies (opt-in: --with-gui) ---------------------------------------------",
            "# NOT installed by default. These are the toolkit libraries the DESKTOP components need",
            "# (voice's GTK app — its ncurses terminal variant builds without any of this — and",
            "# Sideband's Kivy app; the MeshCore GUI is the headless browser meshcore-webui). Installing them",
            "# would drag in the whole X11/Wayland dev chain for software that can never render, so they",
            "# are opt-in. This installs LIBRARIES ONLY — never a desktop, display manager or X/Wayland",
            "# server: it assumes the machine already has a graphical session.",
            'if [ -n "$WITH_GUI" ]; then')
        if gui_pkgs:
            out("\tapt-get install -y \\")
            gp = sorted(gui_pkgs)
            for i, pkg in enumerate(gp):
                out(f"\t\t{pkg}" + (" \\" if i < len(gp) - 1 else ""))
        for b in gui_blocks:
            out("\t" + _desudo(b).replace("\n", "\n\t"))
        out("else",
            '\techo "[bootstrap-deps] GUI dependencies skipped (headless-safe default). On a machine'
            ' with a display, re-run with --with-gui."',
            "fi",
            "")

    if gps_pkgs:
        # --with-gps is a COMPATIBILITY NO-OP, and that is now true of the code and not only of
        # the documentation. gpsd is installed by the default-on time-source scope ($TIME_PKGS),
        # which is the ONE boundary that asks whether this box reads the receiver directly.
        #
        # The old block installed gpsd independently of that boundary, so a direct-NMEA box could
        # still lose its u-blox to `--with-gps`, and `--no-time-source --with-gps` installed the
        # very package --no-time-source promises not to install. A second installer is a second
        # ownership decision, and there must only be one.
        out("# --- GPS (compatibility) --------------------------------------------------------------------",
            "# --with-gps used to install gpsd here. gpsd is now part of the default time-source",
            "# scope, which is the only place that checks whether lhpc reads the receiver directly",
            "# ([gps] source = nmea). Installing it from a second place would bypass that check.",
            'if [ -n "$WITH_GPS" ]; then',
            '\techo "[bootstrap-deps] --with-gps: gpsd is already part of the default time-source'
            ' scope; the flag is retained for compatibility and installs nothing extra."',
            "fi",
            "")

    # --- time source (default ON; --no-time-source subtracts it) ----------------------------------
    # The packages rode the merged transaction above as $TIME_PKGS. What is left is the
    # configuration, and it is emitted as ONE quoted heredoc fed to `sh -s`: the body stays
    # literal, so the awk program keeps its own quoting and no intermediate shell expands
    # anything. Rendered from the SAME helper the dependency-panel copybox uses.
    out("# --- time source (chrony + gpsd; opt out with --no-time-source) ------------------------------",
        'if [ -z "$NO_TIME_SOURCE" ]; then',
        "\tif sh -s <<'LHPC_TIME_SOURCE'")
    # NOT indented: `sh -s <<'X'` is a QUOTED, non-dash heredoc, so every byte reaches the inner
    # shell verbatim. Indenting the body would push the NESTED terminators (LHPC_DROPIN, LHPC_AWK)
    # off column 0 -- where the inner shell never recognises them -- and would prepend tabs to the
    # chrony drop-in itself. `bash -n` parses the indented version happily; only running it fails.
    for line in time_source_setup_sh().splitlines():
        out(line)
    out("LHPC_TIME_SOURCE",
        # The setup exits nonzero when a promise it makes could not be established on a box that
        # CAN establish it (see TS_FAILED). Announcing success over that is the exact failure the
        # live matrix caught: the box had no time daemon and the script said so was fine.
        "\tthen",
        '\t\techo "[bootstrap-deps] time source: chrony disciplines the clock (this REPLACED'
        ' systemd-timesyncd); gpsd feeds it GPS time when a receiver is attached."',
        "\telse",
        '\t\techo "[bootstrap-deps] time source setup did NOT complete — this box may have no'
        ' working time daemon and systemd-timesyncd was removed. Fix the errors above and re-run." >&2',
        "\t\t# The CALLER has to learn this too. Printing the failure and returning 0 means any",
        "\t\t# automation running the full bootstrap -- including the image build -- sees success",
        "\t\t# over a box whose timesyncd was replaced by a chrony that will not start.",
        "\t\tBOOTSTRAP_FAILED=1",
        "\tfi",
        "else",
        '\techo "[bootstrap-deps] time source skipped — systemd-timesyncd left in place. This box'
        ' keeps whatever was already disciplining its clock."',
        "fi",
        "")


    out("# --- SPI / boot config (idempotent; only the chosen --spi-mode mutates config.txt) ----------",
        "# The read-only CONFLICT check ran UP FRONT (before any mutation, item P); here we only APPEND",
        "# the idempotent lines for the chosen mode. CONFIG_TXT was set in the pre-flight above.",
        "add_cfg() {  # append $1 iff absent (idempotent)",
        '\tif ! grep -qxF "$1" "$CONFIG_TXT" 2>/dev/null; then printf "%s\\n" "$1" | tee -a "$CONFIG_TXT" >/dev/null; fi',
        "}",
        'case "$SPI_MODE" in',
        "\tsoft-cs)",
        '\t\tadd_cfg "dtparam=spi=on"',
        f'\t\tadd_cfg "dtoverlay={spi_overlay}" ;;',
        "\thardware-cs)",
        '\t\tadd_cfg "dtparam=spi=on" ;;',
        '\tskip) echo "[bootstrap-deps] SPI: skipped (no boot-config change)." ;;',
        "esac",
        "")

    out("# --- swap: disk-backed OOM insurance for small-RAM builds (idempotent; skips when unneeded) --",
        "# Deferred verdict: a REQUIRED-but-unprovisioned swap fails the run (exit 4) at the very",
        "# END, so the apt/SPI/group work still lands and the operator gets one loud, actionable",
        "# failure instead of losing a configured machine to a full SD card.",
        '_SWAP_FAILED=""',
        '_GROUPS_FAILED=""',
        'if [ -n "$NO_SWAPFILE" ]; then',
        '\techo "[bootstrap-deps] swap: skipped (--no-swapfile)."',
        "else",
        '\tSWAPFILE="${LHPC_SWAPFILE:-/var/swap.lhpc}"',
        '\tMEMINFO="${MEMINFO:-/proc/meminfo}"',
        '\tSWAPS="${SWAPS:-/proc/swaps}"',
        '\tFSTAB="${FSTAB:-/etc/fstab}"',
        '\tSWAP_TARGET_MB="${SWAP_SIZE_MB:-768}"',
        '\tSWAP_FSTAB_LINE="$SWAPFILE none swap sw,pri=10 0 0"',
        "\t_memmb=$(( $(awk '/^MemTotal:/{m=$2} END{print m+0}' \"$MEMINFO\" 2>/dev/null || echo 0) / 1024 ))",
        "\t# disk-backed swap only — zram is compressed RAM (no real backing store), so EXCLUDE it: an",
        "\t# OOM can happen with zram present (it did — 414Mi zram, 78Mi used, at the kill). OUR OWN",
        "\t# file is excluded too: \"does this host still need us?\" must not be answered by our own swap.",
        "\t_diskswap_mb=$(( $(awk -v f=\"$SWAPFILE\" 'NR>1 && $1 !~ /zram/ && $1 != f {s+=$3}"
        " END{print s+0}' \"$SWAPS\" 2>/dev/null || echo 0) / 1024 ))",
        "\t# REQUIRED = this host cannot be trusted to build without OUR disk swap. Required-but-",
        "\t# missing is a HARD failure; --no-swapfile is the operator's only way to proceed without it.",
        '\t_swap_required=""',
        '\tif [ "$_memmb" -lt 600 ] && [ "$_diskswap_mb" -lt "$SWAP_TARGET_MB" ]; then'
        " _swap_required=1; fi",
        "\t# ACTIVE is the ONLY proof the swap does anything, and every lookup matches the FIRST FIELD",
        "\t# exactly: a substring/`grep -F` test also matches a commented-out line, a longer path",
        "\t# (/var/swap.lhpc.old) or a mount option — which is how an inactive swap got reported as",
        "\t# 'already present'.",
        '\t_swap_active() { awk -v f="$SWAPFILE" \'NR>1 && $1 == f {hit=1} END{exit !hit}\''
        ' "$SWAPS" 2>/dev/null; }',
        '\t_fstab_declared() { awk -v f="$SWAPFILE" \'$1 == f {hit=1} END{exit !hit}\''
        ' "$FSTAB" 2>/dev/null; }',
        "\t# EXACTLY ONE canonical entry. A first-field match with the WRONG options, or two matches,",
        "\t# is NOT a valid declaration — it is rewritten. Anything else (comment, .old path) is not ours.",
        '\t_fstab_canonical() {',
        '\t\tawk -v f="$SWAPFILE" -v l="$SWAP_FSTAB_LINE" \'$1 == f {n++; if ($0 == l) ok++}'
        ' END{exit !(n == 1 && ok == 1)}\' "$FSTAB" 2>/dev/null',
        "\t}",
        "\t# lstat, not stat: [ -L ] is the ONLY test that does NOT follow the link, and it must come",
        "\t# FIRST because [ -e ]/[ -f ]/[ -d ] all resolve. A symlink, directory, FIFO, socket or",
        "\t# device node at the swap path is a mistake or an attack (chmod 600 + mkswap would land on",
        "\t# an operator-chosen target). Refuse WITHOUT touching it — no chmod, no rm, no mkswap.",
        '\t_swap_leaf_ok() {',
        '\t\tif [ -L "$SWAPFILE" ]; then',
        '\t\t\techo "[bootstrap-deps] swap: $SWAPFILE is a SYMLINK — refusing to touch it'
        ' (remove it by hand)." >&2; return 1',
        "\t\tfi",
        '\t\tif [ ! -e "$SWAPFILE" ]; then return 0; fi',
        '\t\tif [ -d "$SWAPFILE" ]; then',
        '\t\t\techo "[bootstrap-deps] swap: $SWAPFILE is a DIRECTORY — refusing to touch it'
        ' (remove it by hand)." >&2; return 1',
        "\t\tfi",
        '\t\tif [ ! -f "$SWAPFILE" ]; then',
        '\t\t\techo "[bootstrap-deps] swap: $SWAPFILE is not a regular file (FIFO/socket/device?)'
        ' — refusing to touch it." >&2; return 1',
        "\t\tfi",
        "\t\treturn 0",
        "\t}",
        "\t# TRANSACTIONAL fstab publication. `tee -a` can neither remove a stale/duplicate/wrong-",
        "\t# option line nor survive an interruption (a half-written line can make the next boot",
        "\t# unmountable). Build the WHOLE file in a same-directory temp, fsync it, then rename over",
        "\t# the original and fsync the directory. NEVER follow a symlink: this script is privileged,",
        "\t# so a planted/configured link would let it overwrite an arbitrary target.",
        '\t_fstab_publish() {',
        '\t\tif [ -L "$FSTAB" ]; then',
        '\t\t\techo "[bootstrap-deps] swap: $FSTAB is a SYMLINK — refusing to publish through it."'
        " >&2; return 1",
        "\t\tfi",
        '\t\tif [ -e "$FSTAB" ] && [ ! -f "$FSTAB" ]; then',
        '\t\t\techo "[bootstrap-deps] swap: $FSTAB is not a regular file — refusing to publish."'
        " >&2; return 1",
        "\t\tfi",
        '\t\t_ftmp="$(mktemp "$(dirname "$FSTAB")/.fstab.lhpc.XXXXXX")" || return 1',
        '\t\tif [ -f "$FSTAB" ]; then',
        "\t\t\t# cp carries mode+ownership onto the 0600 root-owned temp; the filter then rewrites",
        "\t\t\t# its CONTENT: every EXACT first-field match is dropped (stale options, duplicates),",
        "\t\t\t# while comments and longer paths keep their own first field and survive verbatim.",
        '\t\t\tif ! cp --preserve=mode,ownership "$FSTAB" "$_ftmp"; then',
        '\t\t\t\trm -f "$_ftmp"; return 1',
        "\t\t\tfi",
        '\t\t\tif ! awk -v f="$SWAPFILE" \'$1 != f\' "$FSTAB" | tee "$_ftmp"'
        " >/dev/null; then",
        '\t\t\t\trm -f "$_ftmp"; return 1',
        "\t\t\tfi",
        '\t\telif ! chmod 644 "$_ftmp"; then',
        '\t\t\trm -f "$_ftmp"; return 1',
        "\t\tfi",
        '\t\tif ! printf "%s\\n" "$SWAP_FSTAB_LINE" | tee -a "$_ftmp" >/dev/null; then',
        '\t\t\trm -f "$_ftmp"; return 1',
        "\t\tfi",
        "\t\t# Durable BEFORE the rename: a power loss must not publish an empty/partial fstab.",
        '\t\tif ! sync "$_ftmp"; then rm -f "$_ftmp"; return 1; fi',
        '\t\tif ! mv -f "$_ftmp" "$FSTAB"; then rm -f "$_ftmp"; return 1; fi',
        '\t\tsync "$(dirname "$FSTAB")" || true   # dir entry durable (best-effort)',
        "\t}",
        "\t# Fresh allocation NEVER writes to the final path: a UNIQUE same-directory temp is",
        "\t# allocated, chmod 600'd, formatted and FSYNCED, and only a complete, valid swap image is",
        "\t# renamed into place. An interrupted run leaves an inert .swap.lhpc.XXXXXX, never a",
        "\t# half-formatted $SWAPFILE that the next run would try to swapon.",
        '\t_swap_alloc_temp() {',
        '\t\t_stmp="$(mktemp "$(dirname "$SWAPFILE")/.swap.lhpc.XXXXXX")" || return 1',
        '\t\tif ! fallocate -l "${SWAP_TARGET_MB}M" "$_stmp" 2>/dev/null; then',
        '\t\t\tif ! dd if=/dev/zero of="$_stmp" bs=1M count="$SWAP_TARGET_MB" status=none;'
        " then",
        '\t\t\t\trm -f "$_stmp"; return 1',
        "\t\t\tfi",
        "\t\tfi",
        '\t\tif ! chmod 600 "$_stmp"; then rm -f "$_stmp"; return 1; fi',
        '\t\tif ! mkswap "$_stmp" >/dev/null; then rm -f "$_stmp"; return 1; fi',
        '\t\tif ! sync "$_stmp"; then rm -f "$_stmp"; return 1; fi',
        '\t\tprintf "%s\\n" "$_stmp"',
        "\t}",
        "\t# swapon stderr is NOT suppressed: its message ('read swap header failed', 'Device or",
        "\t# resource busy') is the operator's only clue. LOWER priority than zram (zram-generator",
        "\t# default 100): the file is overflow, zram stays the fast tier.",
        '\t_swap_install() {',
        '\t\t_new="$(_swap_alloc_temp)" || return 1',
        "\t\tif _swap_active; then",
        '\t\t\tif ! swapoff "$SWAPFILE"; then',
        '\t\t\t\techo "[bootstrap-deps] swap: $SWAPFILE is in use and swapoff failed — refusing'
        ' to replace it." >&2',
        '\t\t\t\trm -f "$_new"; return 1',
        "\t\t\tfi",
        "\t\tfi",
        '\t\tif ! mv -f "$_new" "$SWAPFILE"; then rm -f "$_new"; return 1; fi',
        '\t\tsync "$(dirname "$SWAPFILE")" || true',
        '\t\tswapon -p 10 "$SWAPFILE"',
        "\t}",
        '\t_provision=""',
        '\t_swap_state="fail"',
        "\tif ! _swap_leaf_ok; then",
        '\t\t_swap_state="refused"',
        "\telif _swap_active; then",
        '\t\t_swap_state="active"',
        '\telif [ -f "$SWAPFILE" ] || _fstab_declared; then',
        "\t\t# Declared/allocated but NOT active — an earlier run interrupted between allocate and",
        "\t\t# swapon, or a stale fstab line. Try ONE in-place activation (cheapest, no SD writes);",
        "\t\t# else (re)create below. A successful reactivation STILL has to pass the fstab check.",
        '\t\tif [ ! -f "$SWAPFILE" ]; then',
        "\t\t\t# fstab still DECLARES the swapfile but the file itself is gone (deleted leftover). A",
        "\t\t\t# swapon here can ONLY print 'cannot open ... No such file or directory' — an expected",
        "\t\t\t# probe result, not a failure — so skip it and recreate rather than leak a scary line.",
        "\t\t\t# This is scoped to the file-absent case ONLY: P1a's unsuppressed swapon errors stay",
        "\t\t\t# unsuppressed on the REAL activation attempts (reactivate below, and _swap_install).",
        '\t\t\techo "[bootstrap-deps] swap: fstab entry pointed at a missing file — recreating."',
        '\t\t\t_provision="reuse"',
        '\t\telif swapon -p 10 "$SWAPFILE" && _swap_active; then',
        '\t\t\t_swap_state="reactivated"',
        "\t\telse",
        '\t\t\t_provision="reuse"',
        "\t\tfi",
        '\telif [ "$_memmb" -ge 600 ]; then',
        '\t\t_swap_state="skip-ram"',
        '\telif [ "$_diskswap_mb" -ge "$SWAP_TARGET_MB" ]; then',
        '\t\t_swap_state="skip-disk"',
        "\telse",
        '\t\t_provision="fresh"',
        "\tfi",
        '\tif [ -n "$_provision" ]; then',
        '\t\t_swapdir="$(dirname "$SWAPFILE")"',
        "\t\t_freemb=$(( $(df -Pk \"$_swapdir\" 2>/dev/null | awk 'NR==2{f=$4} END{print f+0}'"
        " || echo 0) / 1024 ))",
        "\t\t# The image is built ALONGSIDE any existing file, so a FRESH run needs 2x the target",
        "\t\t# (image + headroom) and a REUSE needs 1x (the old file's blocks are charged until mv).",
        '\t\tif [ "$_provision" = "fresh" ]; then _need_mb=$(( SWAP_TARGET_MB * 2 ));'
        ' else _need_mb="$SWAP_TARGET_MB"; fi',
        '\t\tif [ "$_freemb" -lt "$_need_mb" ]; then',
        '\t\t\techo "[bootstrap-deps] swap: only ${_freemb}MB free on $_swapdir; need >='
        ' ${_need_mb}MB — refusing to fill the card." >&2',
        "\t\telif _swap_install || _swap_install; then",
        "\t\t\t# ONE recreate: a truncated/corrupt leftover cannot be formatted in place.",
        '\t\t\t_swap_state="created"',
        "\t\tfi",
        "\tfi",
        "\t# SUCCESS = ACTIVE **AND** PERSISTENTLY DECLARED. That conjunction is what makes an",
        "\t# interruption after swapon but before fstab publication self-repair: the next run finds",
        "\t# it active, finds no canonical entry, and publishes one.",
        '\tcase "$_swap_state" in',
        "\t\tactive|reactivated|created)",
        "\t\t\tif _fstab_canonical || _fstab_publish; then",
        '\t\t\t\tcase "$_swap_state" in',
        '\t\t\t\t\tcreated) echo "[bootstrap-deps] swap: created $SWAPFILE (${SWAP_TARGET_MB}MB,'
        ' priority 10 — below zram)." ;;',
        '\t\t\t\t\treactivated) echo "[bootstrap-deps] swap: already present ($SWAPFILE —'
        ' reactivated)." ;;',
        '\t\t\t\t\t*) echo "[bootstrap-deps] swap: already present and active ($SWAPFILE)." ;;',
        "\t\t\t\tesac",
        "\t\t\telse",
        '\t\t\t\techo "[bootstrap-deps] swap: $SWAPFILE is ACTIVE but $FSTAB could not be updated'
        ' — it will NOT survive a reboot." >&2',
        '\t\t\t\tif [ -n "$_swap_required" ]; then _SWAP_FAILED=1; fi',
        "\t\t\tfi",
        "\t\t\t;;",
        '\t\tskip-ram) echo "[bootstrap-deps] swap: skipped (MemTotal ${_memmb}MB >= 600MB —'
        ' enough RAM)." ;;',
        '\t\tskip-disk) echo "[bootstrap-deps] swap: skipped (disk-backed swap ${_diskswap_mb}MB'
        ' already >= ${SWAP_TARGET_MB}MB target)." ;;',
        "\t\t*)",
        '\t\t\techo "[bootstrap-deps] swap: FAILED to activate $SWAPFILE — builds on this machine'
        ' may OOM" >&2',
        '\t\t\tif [ -n "$_swap_required" ]; then _SWAP_FAILED=1; fi',
        "\t\t\t;;",
        "\tesac",
        "fi",
        "")

    # --- Wi-Fi power-save: default-DISABLE only when the install runs over Wi-Fi -------------------
    # Live finding on a Pi Zero 2W: the brcmfmac Wi-Fi firmware drops (the wlan interface can vanish
    # until reboot) under a long build's sustained -j1 CPU load with power-save ON — which stalls a
    # headless install over Wi-Fi. Default: disable power-save via ONE NetworkManager drop-in — but
    # ONLY when Wi-Fi actually carries the install: a default route classified TYPE!=wifi means LAN is
    # used and Wi-Fi is LEFT UNTOUCHED (a note gives the manual command). Classification is TYPE-based
    # via nmcli, never a wlan* name glob (wlp2s0/wlx... must classify as Wi-Fi); an absent route or
    # unclassifiable device falls back to disabling — a mis-detection must never REMOVE the protection
    # this feature exists for. --keep-wifi-powersave leaves Wi-Fi untouched in EVERY case. Accepted
    # residual (documented in docs/maintenance.md): a dual-link box with a wired default route but an SSH
    # session over the wlan address gets the skip. Idempotent; a no-op without a Wi-Fi device.
    out("# --- Wi-Fi power-save (default: DISABLE when the install runs over Wi-Fi; LAN install: leave",
        "# --- untouched; --keep-wifi-powersave: never touch) ------------------------------------------",
        'WIFI_PSAVE_CONF="${WIFI_PSAVE_CONF:-/etc/NetworkManager/conf.d/wifi-nopowersave.conf}"',
        "# ONE nmcli call feeds presence, device pick AND default-route classification — all TYPE-based,",
        "# never a wlan* name glob (predictable-naming Wi-Fi like wlp2s0 must not be mis-read as LAN).",
        '_NM_DEVS="$(nmcli -t -f DEVICE,TYPE device 2>/dev/null || true)"',
        'WIFI_DEV="$(printf \'%s\\n\' "$_NM_DEVS" | awk -F: \'$2=="wifi"{print $1; exit}\')"',
        'WIFI_DEFROUTE_DEV="$(ip -o route show default 2>/dev/null | awk \'{for(i=1;i<NF;i++) if ($i=="dev") {print $(i+1); exit}}\' || true)"',
        'WIFI_DEFROUTE_TYPE="$(printf \'%s\\n\' "$_NM_DEVS" | awk -F: -v d="$WIFI_DEFROUTE_DEV" \'$1==d{print $2; exit}\')"',
        'if [ -n "$KEEP_WIFI" ]; then',
        '\techo "[bootstrap-deps] Wi-Fi: left untouched (--keep-wifi-powersave)."',
        '\techo "[bootstrap-deps]   WARNING: with power-save ON, a Pi Zero 2W can DROP Wi-Fi (the wlan"',
        '\techo "[bootstrap-deps]   interface may vanish until reboot) during a long build — a headless"',
        '\techo "[bootstrap-deps]   install over Wi-Fi may stall. Omit this flag to let bootstrap disable it."',
        'elif [ -z "$WIFI_DEV" ]; then',
        '\techo "[bootstrap-deps] Wi-Fi: no NetworkManager-managed wlan interface — nothing to do (wired/other)."',
        'elif [ -n "$WIFI_DEFROUTE_DEV" ] && [ -n "$WIFI_DEFROUTE_TYPE" ] && [ "$WIFI_DEFROUTE_TYPE" != "wifi" ]; then',
        "\t# LAN carries the install (default route classified non-wifi): the drop-protection rationale",
        "\t# does not apply — do NOT mutate an unrelated Wi-Fi setting. An absent route or an",
        "\t# unclassifiable device falls through to the disable path instead: mis-detection must never",
        "\t# REMOVE protection.",
        '\techo "[bootstrap-deps] Wi-Fi: left untouched — the install runs over LAN (default route via $WIFI_DEFROUTE_DEV, type $WIFI_DEFROUTE_TYPE)."',
        '\techo "[bootstrap-deps]   If this box will later run headless over Wi-Fi, disable power-save manually:"',
        "\techo \"[bootstrap-deps]   printf '[connection]\\nwifi.powersave = 2\\n' | sudo tee $WIFI_PSAVE_CONF && sudo systemctl restart NetworkManager\"",
        "else",
        '\techo "[bootstrap-deps] Wi-Fi: DISABLING power-save on $WIFI_DEV (default)."',
        '\techo "[bootstrap-deps]   Reason: brcmfmac Wi-Fi on a Pi Zero 2W crashes/drops under a long build"',
        '\techo "[bootstrap-deps]   with power-save on; --keep-wifi-powersave leaves Wi-Fi untouched."',
        '\t# Persistent config, fail-closed: refuse a symlink/non-regular leaf (never write THROUGH it as',
        '\t# root); write a ROOT-OWNED same-dir temp, chmod 0644, atomically rename into place,',
        '\t# and clean the temp up on ANY failure after creation. _persisted tracks success so the live-apply',
        '\t# report below never promises "after reboot" when nothing was actually written.',
        '\t_persisted=0',
        '\tif [ -L "$WIFI_PSAVE_CONF" ] || { [ -e "$WIFI_PSAVE_CONF" ] && [ ! -f "$WIFI_PSAVE_CONF" ]; }; then',
        '\t\techo "[bootstrap-deps]   WARNING: $WIFI_PSAVE_CONF is a symlink or non-regular file - NOT touching it; power-save config left as-is." >&2',
        "\telse",
        '\t\tmkdir -p "$(dirname "$WIFI_PSAVE_CONF")" 2>/dev/null || true',
        '\t\tif _wtmp="$(mktemp "$(dirname "$WIFI_PSAVE_CONF")/.wifi-nopowersave.XXXXXX")" \\',
        '\t\t\t\t&& printf \'[connection]\\nwifi.powersave = 2\\n\' | tee "$_wtmp" >/dev/null \\',
        '\t\t\t\t&& chmod 0644 "$_wtmp" \\',
        '\t\t\t\t&& mv -- "$_wtmp" "$WIFI_PSAVE_CONF"; then',
        '\t\t\t_persisted=1',
        '\t\t\techo "[bootstrap-deps]   persistent config written: $WIFI_PSAVE_CONF (wifi.powersave=2)"',
        '\t\t\techo "[bootstrap-deps]   REVERT: sudo rm $WIFI_PSAVE_CONF && sudo systemctl restart NetworkManager"',
        "\t\telse",
        '\t\t\t[ -n "${_wtmp:-}" ] && rm -f -- "$_wtmp"',
        '\t\t\techo "[bootstrap-deps]   WARNING: could not write $WIFI_PSAVE_CONF - power-save NOT persisted." >&2',
        "\t\tfi",
        "\tfi",
        '\t# Live apply (best-effort), reported SEPARATELY. "live" is claimed ONLY when a live operation',
        '\t# PROVABLY succeeded (a reapplied NM profile or an iw set): _live_ok starts FALSE, so the',
        '\t# no-op path (no active profile, no iw) never reads as success. `nmcli connection modify`',
        '\t# alone only edits the PROFILE — the active device needs `nmcli device reapply` to change.',
        '\t_live_ok=0',
        '\t_wcon="$(nmcli -t -f NAME,DEVICE connection show --active 2>/dev/null | grep ":${WIFI_DEV}$" | cut -d: -f1 | head -1 || true)"',
        '\tif [ -n "$_wcon" ]; then',
        '\t\tif nmcli connection modify "$_wcon" wifi.powersave 2 >/dev/null 2>&1 \\',
        '\t\t\t\t&& nmcli device reapply "$WIFI_DEV" >/dev/null 2>&1; then',
        "\t\t\t_live_ok=1",
        "\t\tfi",
        "\tfi",
        '\tif command -v iw >/dev/null 2>&1; then',
        '\t\tiw dev "$WIFI_DEV" set power_save off >/dev/null 2>&1 && _live_ok=1',
        "\tfi",
        '\tif [ "$_live_ok" = 1 ]; then',
        '\t\techo "[bootstrap-deps]   Wi-Fi power-save disabled now (live)."',
        '\telif [ "$_persisted" = 1 ]; then',
        '\t\techo "[bootstrap-deps]   Wi-Fi power-save NOT applied live - takes effect after the next reboot."',
        "\telse",
        '\t\techo "[bootstrap-deps]   Wi-Fi power-save NOT applied live and NOT persisted - power-save is UNCHANGED (fix $WIFI_PSAVE_CONF, then re-run)." >&2',
        "\tfi",
        "fi",
        "")

    if groups_csv:
        # usermod exits nonzero when a named group does not exist (exit 6). On the target Raspberry
        # Pi OS image spi/gpio are present, but under `set -e` an unexpected absence would abort the
        # run mid-way and lose the summary — and a FAILED grant is not something to swallow either:
        # it is exactly what makes the rootless stacks unable to reach the radio. So it is reported
        # loudly and settled at the END, the same way the swap verdict is.
        out("# --- hardware group membership (granted to the resolved operator, never root) ------------",
            f'if usermod -aG {groups_csv} "$OP"; then',
            f'\techo "[bootstrap-deps] granted {groups_csv} to $OP — log out/in (or reboot) to take effect."',
            "else",
            f'\techo "ERROR: could not grant {groups_csv} to $OP — do those groups exist on this'
            ' system? Rootless SPI/GPIO access will NOT work until this is resolved." >&2',
            "\t_GROUPS_FAILED=1",
            "fi",
            "")

    # Power controls: the polkit rule that authorizes $OP for the console's Reboot / Shut down
    # buttons (logind action ids). polkitd itself rides the merged apt transaction above via
    # $POLKIT_PKG. The rule text is the SAME source the dependency-panel copybox renders
    # (power_rule_text) — the unquoted heredoc expands $OP at script runtime. install -D
    # creates rules.d if needed and guarantees mode 0644 even over an existing file.
    out("# --- power controls: polkit rule for the console's Reboot/Shut down (opt-out) ------------",
        'if [ -n "$NO_POWER" ]; then',
        '\techo "[bootstrap-deps] power controls: skipped (--no-power-controls)."',
        "else",
        f"\tinstall -D -m 0644 /dev/stdin {POWER_RULE_PATH} <<POWERRULE")
    for ln in power_rule_text("$OP").rstrip("\n").split("\n"):
        out(ln)
    out("POWERRULE",
        '\techo "[bootstrap-deps] power controls: reboot/shutdown polkit rule installed for $OP'
        f' ({POWER_RULE_PATH})."',
        "fi",
        "")

    # Network controls: the polkit rule that authorizes $OP for the console's Network panel
    # (join Wi-Fi via NetworkManager). Same delivery as the power rule; the PANEL itself
    # additionally gates on the lhpc-ap profile existing, so a box without the managed AP
    # (desktop) never shows the feature even when the rule is present.
    out("# --- network controls: polkit rule for the console's Wi-Fi Network panel (opt-out) --------",
        'if [ -n "$NO_NETWORK" ]; then',
        '\techo "[bootstrap-deps] network controls: skipped (--no-network-controls)."',
        "else",
        f"\tinstall -D -m 0644 /dev/stdin {NETWORK_RULE_PATH} <<NETWORKRULE")
    for ln in network_rule_text("$OP").rstrip("\n").split("\n"):
        out(ln)
    out("NETWORKRULE",
        '\techo "[bootstrap-deps] network controls: Wi-Fi polkit rule installed for $OP'
        f' ({NETWORK_RULE_PATH})."',
        "fi",
        "")

    for blk in disable_blocks:
        b = _desudo(blk)
        out("# --- disable an OS-packaged service (lhpc manages its own) --------------------------------")
        mo = _DISABLE_UNIT_RE.search(b)
        unit = mo.group(1) if mo else ""
        if unit:
            # GUARDED: the script runs under `set -e`, and on a CLEAN image this unit does not exist
            # (lhpc builds its own daemon — nothing installs a packaged one), so an unguarded
            # `systemctl disable` exits 1 and aborts the bootstrap before its final summary. This
            # step only ever matters on a box carrying a PRE-EXISTING packaged service, so ask
            # whether the unit exists rather than disabling blind — which also stops printing an
            # alarming failure line on the systems where there is simply nothing to do.
            svc_unit = unit if unit.endswith(".service") else unit + ".service"
            # TRI-STATE: 'genuinely absent' is the normal clean-image state (tolerated), but an
            # inspection FAILURE must NOT be read as absent — that would fail OPEN, leaving a
            # pre-existing packaged service active while bootstrap reports success. exit 9 =
            # systemd could not be inspected.
            out(f"unit_rc=0; unit_present {svc_unit} || unit_rc=$?",
                'case "$unit_rc" in',
                "\t0)",
                "\t\t" + b.replace("\n", "\n\t\t"),
                f'\t\techo "[bootstrap-deps] disabled the OS-packaged {unit} (lhpc manages its own)." ;;',
                f'\t1) echo "[bootstrap-deps] no packaged {unit} service present — nothing to disable." ;;',
                f'\t2) echo "ERROR: could not inspect systemd unit files — cannot confirm a packaged'
                f' {unit} is stopped. Refusing to continue." >&2; exit 9 ;;',
                "esac", "")
        else:
            # Unparseable disable command: never leave it bare under `set -e`.
            out(b + " || true", "")

    for b in other_blocks:
        out("# --- extra setup step -------------------------------------------------------------------",
            _desudo(b), "")

    # The swap verdict is reported LAST so the apt/SPI/group work above always completes: the
    # operator ends up with a configured machine AND an unambiguous nonzero exit, rather than
    # losing their group grants because the card was full.
    out('if [ -n "$_GROUPS_FAILED" ]; then',
        '\techo "[bootstrap-deps] hardware group membership could not be granted (see above) — the'
        ' rootless stacks cannot reach the radio until it is." >&2',
        "\texit 7",
        "fi",
        'if [ -n "$_SWAP_FAILED" ]; then',
        '\techo "[bootstrap-deps] swap was REQUIRED on this low-memory host but could not be'
        ' provisioned (see above). Fix the reported problem and re-run, or pass --no-swapfile to'
        ' proceed without it (builds may be OOM-killed)." >&2',
        "\texit 4",
        "fi",
        # The time source is a DEFAULT-ON feature that removes systemd-timesyncd, so a failure
        # there has to reach the caller. It is reported last, and as a distinct exit code, so it
        # is distinguishable from the group and swap failures above and from a clean run.
        'if [ -n "$BOOTSTRAP_FAILED" ]; then',
        '\techo "[bootstrap-deps] the time source did not come up (see above). systemd-timesyncd'
        ' was replaced, so this box may have NO working time daemon: fix the reported problem and'
        ' re-run, or re-run with --no-time-source to leave the clock alone." >&2',
        "\t# 11: 2-10 are already taken by other refusals in this script, and an exit code that",
        "\t# collides with another cause is worse than no exit code at all.",
        "\texit 11",
        "fi",
        'echo "[bootstrap-deps] done. Next: install lhpc (install.sh), then ONE reboot applies '
        'SPI + groups + PATH — see README steps 5-6."')
    return "\n".join(L) + "\n"
