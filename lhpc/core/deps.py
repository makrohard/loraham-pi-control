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

from .lifecycle import GUI_MISSING_HINT, GROUP_MISSING_HINT, GROUP_RESTART_CMD, GROUP_RESTART_HINT

NOT_EXECUTED_NOTE = "not executed by LHPC — run it yourself"


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


def stack_report(lifecycle, paths, stacks, stack_id: str, comp_index: dict) -> list:
    """Every dependency of `stack_id`'s components, grouped by kind. `lifecycle`
    supplies the bounded `missing_requirements` probe; `comp_index` maps component
    id -> Component manifest-wide (for build/runtime edge resolution)."""
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
    for c in stack.components:
        missing = lifecycle.missing_requirements(c)
        for req in c.requires:
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
            out.append(DepItem(
                kind="system", component=c.id,
                label=req.note or req.cmd or req.check_file or req.absent_file or req.module,
                satisfied=sat,
                detail=detail,
                # restart-pending shows the copyable restart command (re-running usermod would not help);
                # a genuinely-missing grant shows the usermod grant command.
                install_cmd=GROUP_RESTART_CMD if pending else (req.install or ""),
                runtime=bool(req.groups or req.absent_file), restart_pending=pending,
                gui=bool(gui_eff.get(key))))
        for dep_id in c.build_requires:
            dep = comp_index.get(dep_id)
            present = bool(dep and dep.source
                           and paths.resolve_source(dep.source.path).is_dir())
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
_USERMOD_RE = _re.compile(r"usermod\s+-a?G\s+([A-Za-z0-9,_-]+)")
_OVERLAY_RE = _re.compile(r"dtoverlay=([A-Za-z0-9_.-]+)")
_DISABLE_UNIT_RE = _re.compile(r"systemctl\s+disable\s+(?:--now\s+)?([A-Za-z0-9@._-]+)")

# Package-name patterns a HEADLESS install must never pull — GUI toolkits, X/Wayland, GPU/Mesa/LLVM,
# audio servers, input stacks, icon themes/fonts, and whole desktop environments. Used by the
# generated `--dry-run` guard. GUI-opt-in packages are installed only behind --with-gui and are
# deliberately NOT part of that verdict.
_DENY_RE = (r"^(libgtk-|libgdk-|python3-tk|tk[0-9]|libsdl|libx11|libxcb|libxext|libxrandr"
            r"|libxcursor|libxi[0-9]|libxfixes|libxss|xserver-|xwayland|x11-common|xauth"
            r"|libwayland-|libgbm|libdrm|libegl|libgl[0-9x]|mesa-|libllvm|libpulse|libasound"
            r"|libinput|libxkbcommon|adwaita-|gnome-|kde-|xfce4|lxde|cups|fonts-)")


def _is_repo_block(low: str) -> bool:
    return any(t in low for t in ("opensuse", "sources.list", "signed-by", "apt/keyrings",
                                  "add-apt-repo", "trusted.gpg", "release.key"))


def _is_spi_block(low: str) -> bool:
    return "config.txt" in low or "dtparam=spi" in low or "dtoverlay=spi" in low


def render_bootstrap_script(raw_cmds, revision: str = "", gui_cmds=()) -> str:
    """Render every declared dependency-remediation command into ONE hardened, executable bootstrap
    script. Standalone `sudo apt install` commands merge into a single deduplicated `apt-get install`
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
        "# loraham-pi-control. Works BOTH as an ordinary user (it calls sudo internally) AND via",
        "# `sudo bash bootstrap-deps.sh`. lhpc itself never runs privileged commands.",
        "#",
        "#   bootstrap-deps.sh --spi-mode <soft-cs|hardware-cs|skip> [--operator-user <name>]"
        " [--no-swapfile] [--swap-size <MB>] [--with-gui]",
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
        "# Exit codes: 2 usage · 3 conflicting SPI config · 4 required swap unprovisioned ·",
        "#             5 --dry-run could not resolve · 6 --dry-run found graphical packages ·",
        "#             7 hardware group grant failed · 8 system nginx.service could not be confirmed"
        " stopped ·",
        "#             9 systemd unit files could not be inspected (fail-closed — a competing service",
        "#             may be active). Steps that can legitimately fail on a CLEAN",
        "#             image (no packaged meshtasticd unit) are GUARDED — the script runs under",
        "#             `set -e`, so an unguarded one would abort before this summary.",
        "#     --dry-run          simulate the DEFAULT apt transaction and exit WITHOUT touching the",
        "#                        system. Exits 0 only when the transaction resolves cleanly and pulls",
        "#                        no graphical/audio stack; nonzero when it cannot be resolved or would",
        "#                        install one. Run this FIRST on a fresh image — the package closure is",
        "#                        then known before anything is installed, not discovered mid-install.",
        "#     --with-gui         ALSO install the GUI-only dependencies (GTK/Tk) that the desktop"
        " components need. OMITTED BY DEFAULT: this script must never pull a graphical stack onto a"
        " headless image. It installs GUI application LIBRARIES only — never a desktop environment,"
        " display manager or X/Wayland server — and assumes you already run a graphical session.",
        "#",
        "# The apt package set is IDENTICAL on a Pi Zero 2W and a Pi 5; only the SPI mode is hardware-",
        "# specific. QEMU + PlatformIO are provisioned later by the MANAGED build (`lhpc build`), not here.",
        "set -euo pipefail",
        "")

    out("usage() {",
        '\techo "usage: bootstrap-deps.sh --spi-mode <soft-cs|hardware-cs|skip> [--operator-user <name>]'
        ' [--no-swapfile] [--swap-size <MB>] [--with-gui]" >&2',
        '\techo "       bootstrap-deps.sh --dry-run   (simulate the default apt transaction; no changes)" >&2',
        "}",
        "")

    out('SPI_MODE=""',
        'OPERATOR_USER=""',
        'NO_SWAPFILE=""',
        'SWAP_SIZE_MB=""',
        'WITH_GUI=""',
        'DRY_RUN=""',
        "while [ $# -gt 0 ]; do",
        '\tcase "$1" in',
        '\t\t--spi-mode) SPI_MODE="${2:?--spi-mode needs a value}"; shift 2 ;;',
        '\t\t--operator-user) OPERATOR_USER="${2:?--operator-user needs a value}"; shift 2 ;;',
        '\t\t--no-swapfile) NO_SWAPFILE=1; shift ;;',
        '\t\t--with-gui) WITH_GUI=1; shift ;;',
        '\t\t--dry-run) DRY_RUN=1; shift ;;',
        '\t\t--swap-size) SWAP_SIZE_MB="${2:?--swap-size needs a value (MB)}"; shift 2 ;;',
        "\t\t-h|--help) usage; exit 0 ;;",
        '\t\t*) echo "ERROR: unknown argument: $1" >&2; usage; exit 2 ;;',
        "\tesac",
        "done",
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
            "# The blocker this guards against: the real package closure used to be discovered only while",
            "# installing on hardware. `apt-get install -s` resolves it against the local apt database",
            "# without touching the system, so a from-zero run can be vetted first. The GUI opt-in is NOT",
            "# part of this verdict — the default transaction is what a headless image gets.",
            'if [ -n "$DRY_RUN" ]; then',
            '	echo "[bootstrap-deps] DRY RUN — simulating the default apt transaction; nothing is installed or changed."',
            "	if ! command -v apt-get >/dev/null 2>&1; then",
            '		echo "ERROR: dry-run: apt-get is not available — cannot simulate the transaction." >&2',
            "		exit 5",
            "	fi",
            f'	DRY_PKGS="{pkgs_line}"',
            "	# Simulate EXACTLY what the install below runs (same flags, same package set).",
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

    # READ-ONLY PRE-FLIGHT (item P): fail-closed environment checks that used to abort MID-RUN — a
    # conflicting boot config aborted only at the config.txt step, an unqueryable systemd only at the
    # disable step — after apt had installed ~70 packages and the system nginx had been disabled. They
    # run HERE instead, in the up-front validation block before ANY apt / service / config / swap /
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

    if groups_csv:
        out("# Operator for the group grants: explicit --operator-user, else SUDO_USER when run under",
            "# sudo, else the invoking user. NEVER grant hardware groups to root; require a real account.",
            'OP="$OPERATOR_USER"',
            'if [ -z "$OP" ]; then',
            '\tif [ -n "${SUDO_USER:-}" ]; then OP="$SUDO_USER"; else OP="$(id -un)"; fi',
            "fi",
            'if [ -z "$OP" ] || [ "$OP" = "root" ]; then',
            '\techo "ERROR: no non-root operator for group grants — re-run as the operator with sudo, or pass --operator-user <name>." >&2; exit 2',
            "fi",
            'if ! id -u "$OP" >/dev/null 2>&1; then',
            '\techo "ERROR: --operator-user \\"$OP\\" is not a real account." >&2; exit 2',
            "fi",
            'if [ "$(id -u "$OP")" -eq 0 ]; then',
            '\techo "ERROR: refusing to grant hardware groups to a uid-0 account (\\"$OP\\")." >&2; exit 2',
            "fi",
            "")

    out('if [ "$(id -u)" -ne 0 ] && ! sudo -n true 2>/dev/null; then',
        '\techo "[bootstrap-deps] you may be prompted for sudo (system packages + boot config)." >&2',
        "fi",
        "")

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
            "sudo apt-get update",
            "sudo apt-get install -y --no-install-recommends \\")
        pk = sorted(apt_pkgs)
        for i, p in enumerate(pk):
            out(f"    {p}" + (" \\" if i < len(pk) - 1 else ""))
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
                "\t\t\tif sudo systemctl disable --now nginx.service; then",
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
            b, "")

    if gui_pkgs or gui_blocks:
        # Emitted AFTER the repo block on purpose: the default package list must stay the
        # contiguous first apt section (the ordering gate greps the span between `apt-get install`
        # and the OBS URL to prove curl/gpg land before the repo that uses them).
        out("# --- GUI-only dependencies (opt-in: --with-gui) ---------------------------------------------",
            "# NOT installed by default. These are the toolkit libraries the DESKTOP components need",
            "# (voice's GTK app, the MeshCore Node Manager's Tk GUI). On a headless image installing them",
            "# would drag in the whole X11/Wayland dev chain for software that can never render, so they",
            "# are opt-in. This installs LIBRARIES ONLY — never a desktop, display manager or X/Wayland",
            "# server: it assumes the machine already has a graphical session.",
            'if [ -n "$WITH_GUI" ]; then')
        if gui_pkgs:
            out("\tsudo apt-get install -y \\")
            gp = sorted(gui_pkgs)
            for i, pkg in enumerate(gp):
                out(f"\t\t{pkg}" + (" \\" if i < len(gp) - 1 else ""))
        for b in gui_blocks:
            out("\t" + b.replace("\n", "\n\t"))
        out("else",
            '\techo "[bootstrap-deps] GUI dependencies skipped (headless-safe default). On a machine'
            ' with a display, re-run with --with-gui."',
            "fi",
            "")

    out("# --- SPI / boot config (idempotent; only the chosen --spi-mode mutates config.txt) ----------",
        "# The read-only CONFLICT check ran UP FRONT (before any mutation, item P); here we only APPEND",
        "# the idempotent lines for the chosen mode. CONFIG_TXT was set in the pre-flight above.",
        "add_cfg() {  # append $1 iff absent (idempotent)",
        '\tif ! grep -qxF "$1" "$CONFIG_TXT" 2>/dev/null; then printf "%s\\n" "$1" | sudo tee -a "$CONFIG_TXT" >/dev/null; fi',
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
        '\t\t_ftmp="$(sudo mktemp "$(dirname "$FSTAB")/.fstab.lhpc.XXXXXX")" || return 1',
        '\t\tif [ -f "$FSTAB" ]; then',
        "\t\t\t# cp carries mode+ownership onto the 0600 root-owned temp; the filter then rewrites",
        "\t\t\t# its CONTENT: every EXACT first-field match is dropped (stale options, duplicates),",
        "\t\t\t# while comments and longer paths keep their own first field and survive verbatim.",
        '\t\t\tif ! sudo cp --preserve=mode,ownership "$FSTAB" "$_ftmp"; then',
        '\t\t\t\tsudo rm -f "$_ftmp"; return 1',
        "\t\t\tfi",
        '\t\t\tif ! sudo awk -v f="$SWAPFILE" \'$1 != f\' "$FSTAB" | sudo tee "$_ftmp"'
        " >/dev/null; then",
        '\t\t\t\tsudo rm -f "$_ftmp"; return 1',
        "\t\t\tfi",
        '\t\telif ! sudo chmod 644 "$_ftmp"; then',
        '\t\t\tsudo rm -f "$_ftmp"; return 1',
        "\t\tfi",
        '\t\tif ! printf "%s\\n" "$SWAP_FSTAB_LINE" | sudo tee -a "$_ftmp" >/dev/null; then',
        '\t\t\tsudo rm -f "$_ftmp"; return 1',
        "\t\tfi",
        "\t\t# Durable BEFORE the rename: a power loss must not publish an empty/partial fstab.",
        '\t\tif ! sudo sync "$_ftmp"; then sudo rm -f "$_ftmp"; return 1; fi',
        '\t\tif ! sudo mv -f "$_ftmp" "$FSTAB"; then sudo rm -f "$_ftmp"; return 1; fi',
        '\t\tsudo sync "$(dirname "$FSTAB")" || true   # dir entry durable (best-effort)',
        "\t}",
        "\t# Fresh allocation NEVER writes to the final path: a UNIQUE same-directory temp is",
        "\t# allocated, chmod 600'd, formatted and FSYNCED, and only a complete, valid swap image is",
        "\t# renamed into place. An interrupted run leaves an inert .swap.lhpc.XXXXXX, never a",
        "\t# half-formatted $SWAPFILE that the next run would try to swapon.",
        '\t_swap_alloc_temp() {',
        '\t\t_stmp="$(sudo mktemp "$(dirname "$SWAPFILE")/.swap.lhpc.XXXXXX")" || return 1',
        '\t\tif ! sudo fallocate -l "${SWAP_TARGET_MB}M" "$_stmp" 2>/dev/null; then',
        '\t\t\tif ! sudo dd if=/dev/zero of="$_stmp" bs=1M count="$SWAP_TARGET_MB" status=none;'
        " then",
        '\t\t\t\tsudo rm -f "$_stmp"; return 1',
        "\t\t\tfi",
        "\t\tfi",
        '\t\tif ! sudo chmod 600 "$_stmp"; then sudo rm -f "$_stmp"; return 1; fi',
        '\t\tif ! sudo mkswap "$_stmp" >/dev/null; then sudo rm -f "$_stmp"; return 1; fi',
        '\t\tif ! sudo sync "$_stmp"; then sudo rm -f "$_stmp"; return 1; fi',
        '\t\tprintf "%s\\n" "$_stmp"',
        "\t}",
        "\t# swapon stderr is NOT suppressed: its message ('read swap header failed', 'Device or",
        "\t# resource busy') is the operator's only clue. LOWER priority than zram (zram-generator",
        "\t# default 100): the file is overflow, zram stays the fast tier.",
        '\t_swap_install() {',
        '\t\t_new="$(_swap_alloc_temp)" || return 1',
        "\t\tif _swap_active; then",
        '\t\t\tif ! sudo swapoff "$SWAPFILE"; then',
        '\t\t\t\techo "[bootstrap-deps] swap: $SWAPFILE is in use and swapoff failed — refusing'
        ' to replace it." >&2',
        '\t\t\t\tsudo rm -f "$_new"; return 1',
        "\t\t\tfi",
        "\t\tfi",
        '\t\tif ! sudo mv -f "$_new" "$SWAPFILE"; then sudo rm -f "$_new"; return 1; fi',
        '\t\tsudo sync "$(dirname "$SWAPFILE")" || true',
        '\t\tsudo swapon -p 10 "$SWAPFILE"',
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
        '\t\telif sudo swapon -p 10 "$SWAPFILE" && _swap_active; then',
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

    if groups_csv:
        # usermod exits nonzero when a named group does not exist (exit 6). On the target Raspberry
        # Pi OS image spi/gpio are present, but under `set -e` an unexpected absence would abort the
        # run mid-way and lose the summary — and a FAILED grant is not something to swallow either:
        # it is exactly what makes the rootless stacks unable to reach the radio. So it is reported
        # loudly and settled at the END, the same way the swap verdict is.
        out("# --- hardware group membership (granted to the resolved operator, never root) ------------",
            f'if sudo usermod -aG {groups_csv} "$OP"; then',
            f'\techo "[bootstrap-deps] granted {groups_csv} to $OP — log out/in (or reboot) to take effect."',
            "else",
            f'\techo "ERROR: could not grant {groups_csv} to $OP — do those groups exist on this'
            ' system? Rootless SPI/GPIO access will NOT work until this is resolved." >&2',
            "\t_GROUPS_FAILED=1",
            "fi",
            "")

    for b in disable_blocks:
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
            b, "")

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
        'echo "[bootstrap-deps] done. Reboot (SPI/groups), then: clone loraham-pi-control, ./install.sh."')
    return "\n".join(L) + "\n"
