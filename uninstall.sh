#!/usr/bin/env bash
#
# LoRaHAM Pi Control — controller UNINSTALLER.
#
# Removes the selected LHPC CONTROLLER deployment. It does NOT inspect, stop, or wait for
# managed stacks (daemon, MeshCom, MeshCore, direct-radio apps, builds, tests, bulk jobs) —
# those are not part of the controller and may keep running after LHPC is removed.
#
#   default : remove src/, venv/, state/ (incl. locks), logs/ and generated controller dirs,
#             the ~/.local/bin/lhpc symlink and the web service — but KEEP config/.
#   --purge : also remove config/ and the runtime root itself.
#
# Usage:  ./uninstall.sh [--target <dir>] [--purge] [--yes]
#
# Runs as your normal user — no root. Only touches the web service / PATH symlink that
# demonstrably belong to the selected target; never deletes outside it.

set -euo pipefail

TARGET_DIR="${HOME}/loraham-pi-control"
PURGE=0
ASSUME_YES=0

usage() {
	cat <<'EOF'
LoRaHAM Pi Control — controller uninstaller.

Usage:
  ./uninstall.sh [--target <dir>] [--purge] [--yes]

  --target <dir>  runtime root to remove (default: ~/loraham-pi-control)
  --purge         COMPLETE wipe — also remove config/ + secrets and the runtime root
  --yes, -y       do not prompt for confirmation
  -h, --help      show this help

Default keeps config/ (settings + secrets). Removes ONLY LHPC itself — managed stacks are
never inspected or stopped and may keep running.
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

# --------------------------------------------------------------------------- resolve target
# The runtime root must be a REAL directory (not a symlink) so we never recurse-delete through
# a symlinked root; resolve it physically and reject $HOME / root / an escaping path.
if [ "$TARGET_DIR" = "~" ]; then
	TARGET_DIR="$HOME"
elif [ "${TARGET_DIR#\~/}" != "$TARGET_DIR" ]; then
	TARGET_DIR="${HOME}/${TARGET_DIR#\~/}"
fi
[ -e "$TARGET_DIR" ] || die "$TARGET_DIR does not exist — nothing to uninstall."
[ ! -L "$TARGET_DIR" ] || die "$TARGET_DIR is a symlink — refusing (never delete through a symlinked runtime root)."
[ -d "$TARGET_DIR" ] || die "$TARGET_DIR is not a directory."
TARGET_DIR="$(cd "$TARGET_DIR" && pwd -P)"
case "$TARGET_DIR" in
	"$HOME"|"/"|"") die "refusing to uninstall '$TARGET_DIR' (that is not a runtime root)." ;;
esac
# Must look like an LHPC runtime root (so a mistyped --target cannot delete something else).
[ -f "${TARGET_DIR}/config/local.toml" ] || [ -d "${TARGET_DIR}/venv/lhpc" ] \
	|| [ -d "${TARGET_DIR}/src/loraham-pi-control" ] \
	|| die "$TARGET_DIR does not look like an LHPC runtime root. Refusing."

readonly VENV="${TARGET_DIR}/venv/lhpc"
readonly LOCAL_BIN_LINK="${HOME}/.local/bin/lhpc"
readonly UNIT="${HOME}/.config/systemd/user/lhpc-web.service"

# --------------------------------------------------------------------------- plan + confirm
if [ "$PURGE" -eq 1 ]; then
	printf '\nCOMPLETE removal of the runtime root and ALL its contents (config + secrets included):\n\n  %s\n\nThis cannot be undone.\n' "$TARGET_DIR"
else
	printf '\nUninstall LoRaHAM Pi Control from:\n\n  %s\n\nREMOVE : src/ venv/ state/ logs/ build/ bin/ profiles/ systemd/ docs/ (+ web service, PATH link)\nKEEP   : config/ (settings + secrets), backups/\n' "$TARGET_DIR"
fi
printf 'Managed stacks are NOT stopped by this script.\n\n'
if [ "$ASSUME_YES" -ne 1 ]; then
	printf 'Proceed? [y/N] '
	read -r reply </dev/tty 2>/dev/null || reply=""
	case "$reply" in [yY]|[yY][eE][sS]) ;; *) die "aborted." ;; esac
fi

# --------------------------------------------------------------------------- 1. web service
# Only touch the unit if it PROVABLY belongs to THIS target (its LHPC_RUNTIME_ROOT / ExecStart
# points here). A unit for another runtime root is left completely alone.
step "Web service"
if [ -f "$UNIT" ] && { grep -qxF "Environment=LHPC_RUNTIME_ROOT=${TARGET_DIR}" "$UNIT" \
		|| grep -qF "ExecStart=${VENV}/bin/lhpc" "$UNIT"; }; then
	if command -v systemctl >/dev/null 2>&1; then
		systemctl --user stop lhpc-web.service 2>/dev/null \
			|| warn "could not stop lhpc-web.service cleanly — continuing with removal."
		systemctl --user disable lhpc-web.service 2>/dev/null || true
	fi
	rm -f "$UNIT"
	command -v systemctl >/dev/null 2>&1 && systemctl --user daemon-reload 2>/dev/null || true
	note "removed this target's web service ($UNIT)"
elif [ -f "$UNIT" ]; then
	note "the installed lhpc-web.service belongs to a different runtime root — left untouched"
else
	note "no web service for this target"
fi

# --------------------------------------------------------------------------- 2. PATH symlink
step "Command integration"
if [ -L "$LOCAL_BIN_LINK" ]; then
	dest="$(readlink -f "$LOCAL_BIN_LINK" 2>/dev/null || true)"
	case "$dest" in
		"${TARGET_DIR}/"*) rm -f "$LOCAL_BIN_LINK"; note "removed $LOCAL_BIN_LINK" ;;
		*) note "$LOCAL_BIN_LINK points elsewhere ($dest) — left untouched" ;;
	esac
else
	note "no ~/.local/bin/lhpc symlink for this target"
fi

# --------------------------------------------------------------------------- 3. remove files
# rm never follows a symlinked child (it removes the link, not its target); TARGET_DIR is the
# resolved physical path, so nothing outside the selected root is touched.
if [ "$PURGE" -eq 1 ]; then
	step "Purge the runtime root"
	rm -rf -- "${TARGET_DIR:?}"
	note "removed $TARGET_DIR (complete wipe)"
else
	step "Remove controller files (config preserved)"
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
	printf '\nLoRaHAM Pi Control completely removed. (Per-user lingering, if enabled, was left\nuntouched — disable with: loginctl disable-linger "%s")\n' "$USER"
else
	printf '\nLoRaHAM Pi Control uninstalled; config preserved at %s/config/.\nReinstall with install.sh (it reuses that config); add --purge to also remove it.\n' "$TARGET_DIR"
fi
