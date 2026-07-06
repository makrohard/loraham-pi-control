#!/usr/bin/env bash
#
# LoRaHAM Pi Control — uninstaller.
#
# By DEFAULT this preserves your configuration (config/, including secrets.toml) so a later
# reinstall keeps your callsign/settings — it removes the code (the checkout + managed stack
# sources), the venv, runtime state, logs and build artifacts, plus the things install.sh set
# up outside the runtime root (the ~/.local/bin/lhpc symlink and the systemd user service).
#
# With --purge it is a COMPLETE wipe: the entire runtime root is removed, config and secrets
# included.
#
# Usage:
#   ./uninstall.sh [--target <dir>] [--purge] [--yes]
#
# Runs as your normal user — no root, no sudo. It refuses to touch a directory that does not
# look like an LHPC runtime root.

set -euo pipefail

# --------------------------------------------------------------------------- defaults + args
TARGET_DIR="${HOME}/loraham-pi-control"
PURGE=0
ASSUME_YES=0

usage() {
	cat <<'EOF'
LoRaHAM Pi Control — uninstaller.

Usage:
  ./uninstall.sh [--target <dir>] [--purge] [--yes]

  --target <dir>  runtime root to remove (default: ~/loraham-pi-control)
  --purge         COMPLETE wipe — also remove config/ + secrets and the whole runtime root
  --yes           do not prompt for confirmation
  -h, --help      show this help

Default (no --purge): removes code, venv, state, logs and build artifacts but KEEPS config/
(including secrets.toml). Always removes the ~/.local/bin/lhpc symlink and the systemd user
service that install.sh created.
EOF
	exit "${1:-0}"
}

die()  { printf 'ERR  %s\n' "$*" >&2; exit 1; }
note() { printf 'OK   %s\n' "$*"; }
warn() { printf 'WARN %s\n' "$*" >&2; }
step() { printf '\n==> %s\n' "$*"; }

while [ $# -gt 0 ]; do
	case "$1" in
		--target)  TARGET_DIR="${2:?--target needs a directory}"; shift 2 ;;
		--purge)   PURGE=1; shift ;;
		--yes|-y)  ASSUME_YES=1; shift ;;
		-h|--help) usage 0 ;;
		*)         die "unknown argument: $1 (try --help)" ;;
	esac
done

# Expand a leading tilde, then require the directory to already exist and be absolute.
if [ "$TARGET_DIR" = "~" ]; then
	TARGET_DIR="$HOME"
elif [ "${TARGET_DIR#\~/}" != "$TARGET_DIR" ]; then
	TARGET_DIR="${HOME}/${TARGET_DIR#\~/}"
fi
[ -d "$TARGET_DIR" ] || die "$TARGET_DIR does not exist — nothing to uninstall."
TARGET_DIR="$(cd "$TARGET_DIR" && pwd)"

VENV="${TARGET_DIR}/venv/lhpc"
LOCAL_BIN_LINK="${HOME}/.local/bin/lhpc"
UNIT_FILE="${HOME}/.config/systemd/user/lhpc-web.service"

# --------------------------------------------------------------------------- safety guards
# NEVER operate on $HOME or / , and only proceed if the target actually looks like an LHPC
# runtime root (the bootstrap layout) — so a mistyped --target can't `rm -rf` something else.
case "$TARGET_DIR" in
	"$HOME"|"/"|"") die "refusing to uninstall '$TARGET_DIR' (that is not a runtime root)." ;;
esac
looks_like_root=0
if [ -f "${TARGET_DIR}/config/local.toml" ]; then
	looks_like_root=1        # the marker bootstrap always writes (survives a default uninstall)
elif [ -d "${TARGET_DIR}/config" ] && { [ -d "${TARGET_DIR}/state" ] || [ -d "${TARGET_DIR}/src" ] \
	|| [ -d "$VENV" ]; }; then
	looks_like_root=1
fi
[ "$looks_like_root" -eq 1 ] || \
	die "$TARGET_DIR does not look like an LHPC runtime root (no config/local.toml). Refusing."

# --------------------------------------------------------------------------- plan + confirm
if [ "$PURGE" -eq 1 ]; then
	printf '\nThis will COMPLETELY REMOVE the runtime root and ALL its contents:\n\n  %s\n\n' "$TARGET_DIR"
	printf 'Including config/ and secrets.toml. This cannot be undone.\n'
else
	printf '\nThis will uninstall LoRaHAM Pi Control from:\n\n  %s\n\n' "$TARGET_DIR"
	printf 'REMOVE : src/ (checkout + managed stack sources), venv/, state/, logs/, build/, bin/, profiles/, systemd/, docs/\n'
	printf 'KEEP   : config/ (including secrets.toml) and backups/\n'
fi
printf 'Also removes: %s (if it points here) and the systemd user service.\n\n' "$LOCAL_BIN_LINK"

if [ "$ASSUME_YES" -ne 1 ]; then
	printf 'Proceed? [y/N] '
	read -r reply </dev/tty 2>/dev/null || reply=""
	case "$reply" in [yY]|[yY][eE][sS]) ;; *) die "aborted." ;; esac
fi

# --------------------------------------------------------------------------- 1. stop service
step "Stop + remove the web service"
if command -v systemctl >/dev/null 2>&1; then
	systemctl --user stop lhpc-web.service 2>/dev/null || true
	systemctl --user disable lhpc-web.service 2>/dev/null || true
fi
if [ -e "$UNIT_FILE" ]; then
	rm -f "$UNIT_FILE"
	command -v systemctl >/dev/null 2>&1 && systemctl --user daemon-reload 2>/dev/null || true
	note "removed $UNIT_FILE"
else
	note "no systemd user service installed"
fi
warn "any running managed stacks (daemon/apps) are NOT stopped by this script — stop them first with 'lhpc stop <stack>' if needed."

# --------------------------------------------------------------------------- 2. PATH symlink
step "Remove the PATH symlink"
if [ -L "$LOCAL_BIN_LINK" ]; then
	link_dest="$(readlink -f "$LOCAL_BIN_LINK" 2>/dev/null || true)"
	case "$link_dest" in
		"${TARGET_DIR}/"*) rm -f "$LOCAL_BIN_LINK"; note "removed $LOCAL_BIN_LINK" ;;
		*) warn "$LOCAL_BIN_LINK points elsewhere ($link_dest) — left in place." ;;
	esac
else
	note "no ~/.local/bin/lhpc symlink"
fi

# --------------------------------------------------------------------------- 3. remove files
if [ "$PURGE" -eq 1 ]; then
	step "Purge the entire runtime root"
	rm -rf -- "$TARGET_DIR"
	note "removed $TARGET_DIR (complete wipe)"
else
	step "Remove code, venv, state, logs, build artifacts (config preserved)"
	for sub in src venv state logs build bin profiles systemd docs; do
		if [ -e "${TARGET_DIR}/${sub}" ]; then
			rm -rf -- "${TARGET_DIR:?}/${sub}"
			note "removed ${sub}/"
		fi
	done
	note "kept ${TARGET_DIR}/config/ (settings + secrets preserved)"
fi

# --------------------------------------------------------------------------- done
step "Done"
if [ "$PURGE" -eq 1 ]; then
	cat <<EOF

LoRaHAM Pi Control completely removed.

(User lingering, if you enabled it, is a per-user setting and was left untouched —
disable it with: loginctl disable-linger "$USER")
EOF
else
	cat <<EOF

LoRaHAM Pi Control uninstalled; your config was preserved at:

  ${TARGET_DIR}/config/

Reinstall any time with install.sh — it reuses that config. To also remove the config,
re-run with --purge.
EOF
fi
