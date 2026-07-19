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

from .lifecycle import GROUP_MISSING_HINT, GROUP_RESTART_CMD, GROUP_RESTART_HINT

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


def stack_report(lifecycle, paths, stacks, stack_id: str, comp_index: dict) -> list:
    """Every dependency of `stack_id`'s components, grouped by kind. `lifecycle`
    supplies the bounded `missing_requirements` probe; `comp_index` maps component
    id -> Component manifest-wide (for build/runtime edge resolution)."""
    stack = next((s for s in stacks if s.id == stack_id), None)
    if stack is None:
        return []
    out: list = []
    seen_sys: set = set()
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
            else:
                detail = f"missing: {req.check_file or req.cmd}"
            out.append(DepItem(
                kind="system", component=c.id,
                label=req.note or req.cmd or req.check_file or req.absent_file,
                satisfied=sat,
                detail=detail,
                # restart-pending shows the copyable restart command (re-running usermod would not help);
                # a genuinely-missing grant shows the usermod grant command.
                install_cmd=GROUP_RESTART_CMD if pending else (req.install or ""),
                runtime=bool(req.groups or req.absent_file), restart_pending=pending))
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


def _is_repo_block(low: str) -> bool:
    return any(t in low for t in ("opensuse", "sources.list", "signed-by", "apt/keyrings",
                                  "add-apt-repo", "trusted.gpg", "release.key"))


def _is_spi_block(low: str) -> bool:
    return "config.txt" in low or "dtparam=spi" in low or "dtoverlay=spi" in low


def render_bootstrap_script(raw_cmds, revision: str = "") -> str:
    """Render every declared dependency-remediation command into ONE hardened, executable bootstrap
    script. Standalone `sudo apt install` commands merge into a single deduplicated `apt-get install`
    run FIRST (so tools like curl/gpg exist before the blocks that use them). Group grants are
    re-rendered to a validated non-root operator; SPI/config.txt is re-rendered behind a required
    `--spi-mode` (soft-cs | hardware-cs | skip), idempotent and fail-closed on a conflicting existing
    config; the OBS repo block is emitted verbatim (already scoped-keyring + HTTPS). On a small-RAM
    machine (MemTotal < ~600MB) it also provisions a disk swapfile (default 768M, at a priority BELOW
    zram) as OOM insurance for the firmware build — idempotent, guarded on free space, opt-out via
    `--no-swapfile`. Output is deterministic (packages sorted) so the shipped snapshot is stable.
    lhpc NEVER runs these."""
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
        " [--no-swapfile] [--swap-size <MB>]",
        "#     soft-cs      meshtasticd software CS (/dev/spidev0.0): dtparam=spi=on + dtoverlay="
        + spi_overlay + "  (single-radio LoRaHAM Pi / Uputronics)",
        "#     hardware-cs  hardware CE0+CE1, no overlay: dtparam=spi=on only  (e.g. dual Uputronics)",
        "#     skip         no boot-config change (SPI already configured)",
        "#     --no-swapfile      do NOT provision the small-RAM disk swapfile (see below)",
        "#     --swap-size <MB>   swapfile size when provisioned (default 768)",
        "#",
        "# The apt package set is IDENTICAL on a Pi Zero 2W and a Pi 5; only the SPI mode is hardware-",
        "# specific. QEMU + PlatformIO are provisioned later by the MANAGED build (`lhpc build`), not here.",
        "set -euo pipefail",
        "")

    out("usage() {",
        '\techo "usage: bootstrap-deps.sh --spi-mode <soft-cs|hardware-cs|skip> [--operator-user <name>]'
        ' [--no-swapfile] [--swap-size <MB>]" >&2',
        "}",
        "")

    out('SPI_MODE=""',
        'OPERATOR_USER=""',
        'NO_SWAPFILE=""',
        'SWAP_SIZE_MB=""',
        "while [ $# -gt 0 ]; do",
        '\tcase "$1" in',
        '\t\t--spi-mode) SPI_MODE="${2:?--spi-mode needs a value}"; shift 2 ;;',
        '\t\t--operator-user) OPERATOR_USER="${2:?--operator-user needs a value}"; shift 2 ;;',
        '\t\t--no-swapfile) NO_SWAPFILE=1; shift ;;',
        '\t\t--swap-size) SWAP_SIZE_MB="${2:?--swap-size needs a value (MB)}"; shift 2 ;;',
        "\t\t-h|--help) usage; exit 0 ;;",
        '\t\t*) echo "ERROR: unknown argument: $1" >&2; usage; exit 2 ;;',
        "\tesac",
        "done",
        "")

    out("# Validate ALL options up front — BEFORE any apt / repository / boot-config / group mutation.",
        'case "$SPI_MODE" in',
        "\tsoft-cs|hardware-cs|skip) ;;",
        '\t"") echo "ERROR: --spi-mode is required (soft-cs | hardware-cs | skip)." >&2; usage; exit 2 ;;',
        '\t*) echo "ERROR: unknown --spi-mode: $SPI_MODE (soft-cs | hardware-cs | skip)." >&2; exit 2 ;;',
        "esac",
        'if [ -n "$SWAP_SIZE_MB" ] && ! printf "%s" "$SWAP_SIZE_MB" | grep -qE "^[1-9][0-9]*$"; then',
        '\techo "ERROR: --swap-size must be a positive integer number of MB: $SWAP_SIZE_MB" >&2; exit 2',
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

    if apt_pkgs:
        out("# --- APT packages (merged; installed FIRST so curl/gpg/etc. exist before the blocks below) -",
            "sudo apt-get update",
            "sudo apt-get install -y \\")
        pk = sorted(apt_pkgs)
        for i, p in enumerate(pk):
            out(f"    {p}" + (" \\" if i < len(pk) - 1 else ""))
        out("")

    for b in repo_blocks:
        out("# --- third-party apt repository (dedicated keyring + signed-by, HTTPS) -------------------",
            b, "")

    out("# --- SPI / boot config (idempotent; only the chosen --spi-mode mutates config.txt) ----------",
        'CONFIG_TXT="${CONFIG_TXT:-/boot/firmware/config.txt}"',
        "add_cfg() {  # append $1 iff absent (idempotent)",
        '\tif ! grep -qxF "$1" "$CONFIG_TXT" 2>/dev/null; then printf "%s\\n" "$1" | sudo tee -a "$CONFIG_TXT" >/dev/null; fi',
        "}",
        'case "$SPI_MODE" in',
        "\tsoft-cs)",
        f'\t\tif grep -qE "^dtparam=spi=on" "$CONFIG_TXT" 2>/dev/null && ! grep -qxF "dtoverlay={spi_overlay}" "$CONFIG_TXT" 2>/dev/null; then',
        '\t\t\techo "ERROR: $CONFIG_TXT enables SPI without the soft-CS overlay (hardware-CS?) — refusing a conflicting overlay. Resolve by hand." >&2; exit 3',
        "\t\tfi",
        '\t\tadd_cfg "dtparam=spi=on"',
        f'\t\tadd_cfg "dtoverlay={spi_overlay}"',
        "\t\t;;",
        "\thardware-cs)",
        f'\t\tif grep -qxF "dtoverlay={spi_overlay}" "$CONFIG_TXT" 2>/dev/null; then',
        '\t\t\techo "ERROR: $CONFIG_TXT has the soft-CS overlay — incompatible with hardware-cs (needs CE0/CE1). Resolve by hand." >&2; exit 3',
        "\t\tfi",
        '\t\tadd_cfg "dtparam=spi=on"',
        "\t\t;;",
        '\tskip) echo "[bootstrap-deps] SPI: skipped (no boot-config change)." ;;',
        "esac",
        "")

    out("# --- swap: disk-backed OOM insurance for small-RAM builds (idempotent; skips when unneeded) --",
        'if [ -n "$NO_SWAPFILE" ]; then',
        '\techo "[bootstrap-deps] swap: skipped (--no-swapfile)."',
        "else",
        '\tSWAPFILE="${LHPC_SWAPFILE:-/var/swap.lhpc}"',
        '\tMEMINFO="${MEMINFO:-/proc/meminfo}"',
        '\tSWAPS="${SWAPS:-/proc/swaps}"',
        '\tFSTAB="${FSTAB:-/etc/fstab}"',
        '\tSWAP_TARGET_MB="${SWAP_SIZE_MB:-768}"',
        "\t_memmb=$(( $(awk '/^MemTotal:/{m=$2} END{print m+0}' \"$MEMINFO\" 2>/dev/null || echo 0) / 1024 ))",
        "\t# disk-backed swap only — zram is compressed RAM (no real backing store), so EXCLUDE it: an",
        "\t# OOM can happen with zram present (it did — 414Mi zram, 78Mi used, at the kill).",
        "\t_diskswap_mb=$(( $(awk 'NR>1 && $1 !~ /zram/ {s+=$3} END{print s+0}' \"$SWAPS\" 2>/dev/null"
        " || echo 0) / 1024 ))",
        '\tif [ -e "$SWAPFILE" ] || grep -qsF "$SWAPFILE" "$FSTAB"; then',
        '\t\tif ! grep -qsF "$SWAPFILE" "$SWAPS"; then sudo swapon "$SWAPFILE" 2>/dev/null || true; fi',
        '\t\techo "[bootstrap-deps] swap: already present ($SWAPFILE)."',
        '\telif [ "$_memmb" -ge 600 ]; then',
        '\t\techo "[bootstrap-deps] swap: skipped (MemTotal ${_memmb}MB >= 600MB — enough RAM)."',
        '\telif [ "$_diskswap_mb" -ge "$SWAP_TARGET_MB" ]; then',
        '\t\techo "[bootstrap-deps] swap: skipped (disk-backed swap ${_diskswap_mb}MB already >='
        ' ${SWAP_TARGET_MB}MB target)."',
        "\telse",
        '\t\t_swapdir="$(dirname "$SWAPFILE")"',
        "\t\t_freemb=$(( $(df -Pk \"$_swapdir\" 2>/dev/null | awk 'NR==2{f=$4} END{print f+0}'"
        " || echo 0) / 1024 ))",
        "\t\t_need_mb=$(( SWAP_TARGET_MB * 2 ))",
        '\t\tif [ "$_freemb" -lt "$_need_mb" ]; then',
        '\t\t\techo "[bootstrap-deps] swap: skipped (only ${_freemb}MB free on $_swapdir; need >='
        ' ${_need_mb}MB = 2x target — refusing to fill the card)." >&2',
        "\t\telse",
        '\t\t\tif ! sudo fallocate -l "${SWAP_TARGET_MB}M" "$SWAPFILE" 2>/dev/null; then',
        '\t\t\t\tsudo rm -f "$SWAPFILE"',
        '\t\t\t\tsudo dd if=/dev/zero of="$SWAPFILE" bs=1M count="$SWAP_TARGET_MB" status=none',
        "\t\t\tfi",
        '\t\t\tsudo chmod 600 "$SWAPFILE"',
        '\t\t\tsudo mkswap "$SWAPFILE" >/dev/null',
        "\t\t\t# LOWER priority than zram (zram-generator default 100): the file is overflow, zram stays fast.",
        '\t\t\tsudo swapon -p 10 "$SWAPFILE"',
        '\t\t\tif ! grep -qsF "$SWAPFILE" "$FSTAB"; then',
        '\t\t\t\tprintf "%s\\n" "$SWAPFILE none swap sw,pri=10 0 0" | sudo tee -a "$FSTAB" >/dev/null',
        "\t\t\tfi",
        '\t\t\techo "[bootstrap-deps] swap: created $SWAPFILE (${SWAP_TARGET_MB}MB, priority 10 — below zram)."',
        "\t\tfi",
        "\tfi",
        "fi",
        "")

    if groups_csv:
        out("# --- hardware group membership (granted to the resolved operator, never root) ------------",
            f'sudo usermod -aG {groups_csv} "$OP"',
            f'echo "[bootstrap-deps] granted {groups_csv} to $OP — log out/in (or reboot) to take effect."',
            "")

    for b in disable_blocks:
        out("# --- disable the OS-managed service (lhpc manages its own) -------------------------------",
            b, "")

    for b in other_blocks:
        out("# --- extra setup step -------------------------------------------------------------------",
            b, "")

    out('echo "[bootstrap-deps] done. Reboot (SPI/groups), then: clone loraham-pi-control, ./install.sh."')
    return "\n".join(L) + "\n"
