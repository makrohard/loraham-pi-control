#!/usr/bin/env bash
#
# LoRaHAM Pi Control — controller UNINSTALLER.
#
# Removes the selected LHPC CONTROLLER deployment. It does NOT inspect, stop, or wait for
# managed stacks (daemon, MeshCom, MeshCore, direct-radio apps, builds, tests, bulk jobs).
#
#   default : remove src/, venv/, state/, logs/ and generated controller dirs, the
#             ~/.local/bin/lhpc link and the managed systemd units — KEEP config/, backups/,
#             .lhpc-root (so a reinstall reuses them).
#   --purge : also remove config/ and the runtime root itself.
#
# Usage:  ./uninstall.sh [--target <dir>] [--purge] [--purge-legacy-config-only] [--yes]
#
# Runs as your normal user — no root. Only touches the CANONICAL web/updater units + PATH link
# that provably belong to the selected target; noncanonical/foreign units are left untouched.

set -euo pipefail
: "${USER:=$(id -un)}"   # $USER can be unset in minimal envs (systemd/su); bind it so set -u never aborts.

TARGET_DIR="${HOME}/loraham-pi-control"
PURGE=0
LEGACY_ACK=0
ASSUME_YES=0
INCOMPLETE=0            # set when a noncanonical same-root unit is left behind
GUARD=""               # teardown guard path (set after resolution)

usage() {
	cat <<'EOF'
LoRaHAM Pi Control — controller uninstaller.

Usage:
  ./uninstall.sh [--target <dir>] [--purge] [--purge-legacy-config-only] [--yes]

  --target <dir>              runtime root to remove (default: ~/loraham-pi-control)
  --purge                     COMPLETE wipe — also remove config/ + secrets and the root
  --purge-legacy-config-only  allow --purge of a config-only LEGACY root that has no marker
                              (needed only when identity cannot otherwise be proven)
  --yes, -y                   do not prompt for confirmation
  -h, --help                  show this help

Default keeps config/, backups/ and .lhpc-root. Managed stacks are never stopped.
EOF
	exit "${1:-0}"
}

die()  { printf 'ERR  %s\n' "$*" >&2; exit 1; }
note() { printf 'OK   %s\n' "$*"; }
warn() { printf 'WARN %s\n' "$*" >&2; INCOMPLETE=1; }
step() { printf '\n==> %s\n' "$*"; }

while [ $# -gt 0 ]; do
	case "$1" in
		--target)                     TARGET_DIR="${2:?--target needs a directory}"; shift 2 ;;
		--purge)                      PURGE=1; shift ;;
		--purge-legacy-config-only)   PURGE=1; LEGACY_ACK=1; shift ;;
		--yes|-y)                     ASSUME_YES=1; shift ;;
		-h|--help)                    usage 0 ;;
		*)                            die "unknown argument: $1 (try --help)" ;;
	esac
done

# --------------------------------------------------------------------------- resolve target
if [ "$TARGET_DIR" = "~" ]; then
	TARGET_DIR="$HOME"
elif [ "${TARGET_DIR#\~/}" != "$TARGET_DIR" ]; then
	TARGET_DIR="${HOME}/${TARGET_DIR#\~/}"
fi
[ -e "$TARGET_DIR" ] || die "$TARGET_DIR does not exist — nothing to uninstall."
[ ! -L "$TARGET_DIR" ] || die "$TARGET_DIR is a symlink — refusing (never delete through a symlinked root)."
[ -d "$TARGET_DIR" ] || die "$TARGET_DIR is not a directory."
TARGET_DIR="$(cd "$TARGET_DIR" && pwd -P)"
HOME_ABS="$(cd "$HOME" && pwd -P)"
case "$TARGET_DIR" in "$HOME"|"$HOME_ABS"|"/"|"") die "refusing to uninstall '$TARGET_DIR'." ;; esac
# reused controller remainder members must be real (never delete through a swapped symlink)
for _r in config backups .lhpc-root; do
	[ ! -e "${TARGET_DIR}/${_r}" ] || [ ! -L "${TARGET_DIR}/${_r}" ] \
		|| die "${TARGET_DIR}/${_r} is a symlink — refusing."
done

readonly VENV="${TARGET_DIR}/venv/lhpc"
readonly CHECKOUT="${TARGET_DIR}/src/loraham-pi-control"
readonly LOCAL_BIN_LINK="${HOME}/.local/bin/lhpc"
readonly UNIT_DIR="${HOME}/.config/systemd/user"
readonly WEB_UNIT="${UNIT_DIR}/lhpc-web.service"
readonly HELPER_UNIT="${UNIT_DIR}/lhpc-selfupdate.service"
readonly PATH_UNIT="${UNIT_DIR}/lhpc-selfupdate.path"
readonly NGINX_UNIT="${UNIT_DIR}/lhpc-nginx.service"
GUARD="${TARGET_DIR}/.lhpc-uninstalling"

# --------------------------------------------------------------------------- identity proof
marker_valid() {                          # a regular, root-bound .lhpc-root
	local m="${TARGET_DIR}/.lhpc-root"
	[ -f "$m" ] && [ ! -L "$m" ] || return 1
	grep -qF "\"root\": \"${TARGET_DIR}\"" "$m"
}
has_triple() {
	[ -f "${TARGET_DIR}/config/local.toml" ] && [ -d "${TARGET_DIR}/venv/lhpc" ] \
		&& [ -d "${TARGET_DIR}/src/loraham-pi-control" ]
}
is_config_only() {                        # entries ⊆ {config, backups, .lhpc-root}, config present
	local e
	for e in "$TARGET_DIR"/* "$TARGET_DIR"/.[!.]* "$TARGET_DIR"/..?*; do
		[ -e "$e" ] || continue
		case "$(basename "$e")" in config|backups|.lhpc-root) ;; *) return 1 ;; esac
	done
	[ -e "${TARGET_DIR}/config" ]
}

step "Identity"
if marker_valid; then
	note "identity: valid .lhpc-root marker"
elif has_triple; then
	note "identity: full controller structure (config + venv + checkout)"
elif [ "$PURGE" -eq 1 ] && [ "$LEGACY_ACK" -eq 1 ] && is_config_only; then
	warn "purging a config-only LEGACY root without a marker (explicit --purge-legacy-config-only)"
else
	if [ "$PURGE" -eq 1 ] && is_config_only; then
		die "$TARGET_DIR looks like a config-only remainder with no valid .lhpc-root — its LHPC identity cannot be proven. If you are sure, re-run with --purge-legacy-config-only."
	fi
	die "$TARGET_DIR does not prove it is an LHPC controller root (need a valid .lhpc-root or config/local.toml + venv/lhpc + src/loraham-pi-control). Refusing."
fi

# --------------------------------------------------------------------------- plan + confirm
if [ "$PURGE" -eq 1 ]; then
	printf '\nCOMPLETE removal of the runtime root and ALL its contents (config + secrets included):\n\n  %s\n\nThis cannot be undone.\n' "$TARGET_DIR"
else
	printf '\nUninstall LoRaHAM Pi Control from:\n\n  %s\n\nREMOVE : src/ venv/ state/ logs/ build/ bin/ profiles/ systemd/ docs/ + managed units + PATH link\nKEEP   : config/ (settings + secrets), backups/, .lhpc-root\n' "$TARGET_DIR"
fi
printf 'Managed stacks are NOT stopped by this script.\n\n'
if [ "$ASSUME_YES" -ne 1 ]; then
	printf 'Proceed? [y/N] '
	read -r reply </dev/tty 2>/dev/null || reply=""
	case "$reply" in [yY]|[yY][eE][sS]) ;; *) die "aborted." ;; esac
fi

# --------------------------------------------------------------------------- teardown guard
# Written FIRST (root-level, with process identity). The web unit conditions on its ABSENCE, so
# the updater's OnFailure=lhpc-web.service cannot resurrect the console mid-teardown. Recovery
# can clear a stale guard after proving this pid is gone.
printf '{"pid": %s, "nonce": "%s", "started": %s}\n' "$$" "${RANDOM}${RANDOM}" "$(date +%s 2>/dev/null || echo 0)" > "$GUARD"

sysctl_ok() { command -v systemctl >/dev/null 2>&1; }
render_unit() { "${VENV}/bin/python" -m lhpc.core.updater_units render "$1" "$TARGET_DIR" "$CHECKOUT" "$VENV" 2>/dev/null; }
is_canonical() {                          # $1=kind $2=file — byte-exact match to the render
	[ -f "$2" ] && [ ! -L "$2" ] || return 1
	[ -x "${VENV}/bin/python" ] || return 1
	diff -q <(render_unit "$1") "$2" >/dev/null 2>&1
}
owns_root() {                             # $1=file — provenance names THIS root (noncanonical but ours)
	[ -f "$1" ] || return 1
	grep -qxF "Environment=LHPC_RUNTIME_ROOT=${TARGET_DIR}" "$1" \
		|| grep -qF "PathExists=${TARGET_DIR}/state/selfupdate.request" "$1"
}

# --------------------------------------------------------------------------- units (ordered)
# Order matters: DISABLE the .path FIRST (no new request can trigger the helper), stop the helper,
# stop the console — THEN clear request/in-flight — THEN remove the CANONICAL unit files. Only
# byte-exact canonical units are stopped/removed; a noncanonical same-root unit is left + warned.
step "Managed systemd units"
UNITS_REMOVED=0
for spec in "lhpc-nginx.service:${NGINX_UNIT}" \
            "lhpc-selfupdate.path:${PATH_UNIT}" \
            "lhpc-selfupdate.service:${HELPER_UNIT}" \
            "lhpc-web.service:${WEB_UNIT}"; do
	kind="${spec%%:*}"; file="${spec#*:}"
	if is_canonical "$kind" "$file"; then
		if sysctl_ok; then
			systemctl --user stop "$kind" 2>/dev/null || warn "could not stop $kind cleanly — continuing."
			systemctl --user disable "$kind" 2>/dev/null || true
		fi
	elif [ -e "$file" ] || [ -L "$file" ]; then
		if owns_root "$file"; then
			warn "$kind exists but is NOT the canonical unit (customized) — left in place; it may still reference this deleted root. Remove it by hand: rm ${file}"
		else
			note "$kind belongs to a different deployment — left untouched"
		fi
	fi
done
# clear any pending/in-flight request AFTER the watcher + helper are down
rm -f "${TARGET_DIR}/state/selfupdate.request" "${TARGET_DIR}/state/selfupdate.inflight" 2>/dev/null || true
# now remove ONLY the canonical unit files, then reload
for spec in "lhpc-nginx.service:${NGINX_UNIT}" \
            "lhpc-selfupdate.path:${PATH_UNIT}" \
            "lhpc-selfupdate.service:${HELPER_UNIT}" \
            "lhpc-web.service:${WEB_UNIT}"; do
	kind="${spec%%:*}"; file="${spec#*:}"
	if is_canonical "$kind" "$file"; then rm -f "$file"; UNITS_REMOVED=1; note "removed $kind"; fi
done
[ "$UNITS_REMOVED" -eq 1 ] && sysctl_ok && systemctl --user daemon-reload 2>/dev/null || true

# --------------------------------------------------------------------------- PATH symlink
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

# --------------------------------------------------------------------------- remove files
if [ "$PURGE" -eq 1 ]; then
	step "Purge the runtime root"
	rm -rf -- "${TARGET_DIR:?}"          # guard + everything goes with the root
	note "removed $TARGET_DIR (complete wipe)"
else
	step "Remove controller files (config preserved)"
	for sub in src venv state logs build bin profiles systemd docs; do
		if [ -e "${TARGET_DIR}/${sub}" ]; then rm -rf -- "${TARGET_DIR:?}/${sub}"; note "removed ${sub}/"; fi
	done
	rm -f "$GUARD"                        # clear the teardown guard so a reinstall's console can start
	note "kept ${TARGET_DIR}/config/, backups/, .lhpc-root (settings + secrets preserved)"
fi

# --------------------------------------------------------------------------- done
step "Done"
if [ "$INCOMPLETE" -eq 1 ]; then
	printf '\nController files removed, but some steps were INCOMPLETE (see WARN above) — unmanaged\nsystemd units may remain. Review with: systemctl --user list-unit-files "lhpc-*"\n'
fi
if [ "$PURGE" -eq 1 ]; then
	printf '\nLoRaHAM Pi Control completely removed. (Lingering, if enabled, left untouched —\ndisable with: loginctl disable-linger "%s")\n' "$USER"
else
	printf '\nLoRaHAM Pi Control uninstalled; config preserved at %s/config/.\nReinstall with install.sh (it reuses that config); add --purge to also remove it.\n' "$TARGET_DIR"
fi
