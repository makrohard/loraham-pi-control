#!/usr/bin/env bash
#
# LoRaHAM Pi Control — controller UNINSTALLER.
#
# Removes the selected LHPC CONTROLLER deployment. It STOPS and VERIFIES the managed stacks
# (daemon, MeshCom, MeshCore, direct-radio apps) FIRST — and aborts without removing anything if it
# cannot prove they ceased or a build/test/auto-install/HMAC job is unresolved.
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

Default keeps config/, backups/ and .lhpc-root. Managed stacks are STOPPED and VERIFIED first;
if they cannot be proven stopped, the uninstall aborts and removes nothing.
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
readonly RESTART_UNIT="${UNIT_DIR}/lhpc-nginx-restart.service"
readonly RESTART_PATH_UNIT="${UNIT_DIR}/lhpc-nginx-restart.path"
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
printf 'Managed stacks are STOPPED and VERIFIED before removal (clients before the shared daemon);\nif quiescence cannot be proven, this aborts and removes nothing.\n\n'
if [ "$ASSUME_YES" -ne 1 ]; then
	printf 'Proceed? [y/N] '
	read -r reply </dev/tty 2>/dev/null || reply=""
	case "$reply" in [yY]|[yY][eE][sS]) ;; *) die "aborted." ;; esac
fi

# --------------------------------------------------------------------------- teardown guard
# Written FIRST (root-level, with process identity). The web unit conditions on its ABSENCE, so
# the updater's OnFailure=lhpc-web.service cannot resurrect the console mid-teardown. Recovery
# can clear a stale guard after proving this pid is gone.
# Capture config-only-ness BEFORE writing the guard — the guard file itself would otherwise make
# `is_config_only` false (it is not in the allowed remainder set).
CONFIG_ONLY=0; is_config_only && CONFIG_ONLY=1

# Guard identity: a per-invocation nonce + this shell's pid/start-time, so a live owner is
# distinguishable from PID reuse and the RELEASE removes only the guard THIS run owns.
NONCE="${RANDOM}${RANDOM}${RANDOM}"
GUARD_START="$(awk '{print $22}' /proc/$$/stat 2>/dev/null || echo 0)"
guard_release() {   # remove ONLY the guard this invocation owns (never a pre-existing/foreign one)
	if [ -x "${VENV}/bin/lhpc" ]; then
		"${VENV}/bin/lhpc" _uninstall-guard-release --root "$TARGET_DIR" --nonce "$NONCE" >/dev/null 2>&1 || true
	else
		rm -f "$GUARD" 2>/dev/null || true   # config-only remainder: no controller op, no web/tasks
	fi
}

abort_die() {   # a refusal BEFORE any teardown mutation: release the guard THIS run owns, then die.
	# An aborted uninstall means "not uninstalling" — the web console must be startable again. After
	# the FIRST stop/disable/stage mutation, refusals must use plain `die` instead (guard RETAINED: a
	# partially dismantled install must keep the console blocked); the recorded owner pid is then
	# dead, so `lhpc self-update --recover-request` is the documented escape for a kept guard.
	# The release goes through the nonce-checked controller op; a release FAILURE is reported
	# truthfully instead of silently leaving a stranded guard behind a clean-looking abort.
	local _rel_note=""
	if [ -x "${VENV}/bin/lhpc" ]; then
		if ! "${VENV}/bin/lhpc" _uninstall-guard-release --root "$TARGET_DIR" --nonce "$NONCE" >/dev/null 2>&1; then
			_rel_note=" NOTE: the uninstall guard could not be released — clear it with \`lhpc self-update --recover-request\` (or verify no uninstall runs, then remove ${GUARD})."
		fi
	else
		rm -f "$GUARD" 2>/dev/null || _rel_note=" NOTE: could not remove ${GUARD} — remove it by hand."
	fi
	die "$1${_rel_note}"
}

# ATOMIC, EXCLUSIVE, NO-FOLLOW guard claim — NEVER truncates/follows/replaces a pre-existing guard of
# any type, and a live concurrent uninstall is refused. Descriptor-based controller op for a real
# deployment; a `set -C` (noclobber) create as the fallback for a config-only remainder with no lhpc.
if [ -x "${VENV}/bin/lhpc" ]; then
	"${VENV}/bin/lhpc" _uninstall-guard-claim --root "$TARGET_DIR" --pid "$$" --nonce "$NONCE" --start "$GUARD_START" \
		|| die "could not claim the uninstall guard — a concurrent/interrupted uninstall may own it, or ${GUARD} is unsafe. Recover it (verify no uninstall is running, then remove ${GUARD}), and re-run."
else
	( set -C; printf '{"pid": %s, "nonce": "%s", "start_time": %s}\n' "$$" "$NONCE" "$GUARD_START" > "$GUARD" ) 2>/dev/null \
		|| die "an uninstall guard already exists at ${GUARD} — refusing (a concurrent or interrupted uninstall). Remove it once you are sure none is running, then re-run."
fi

# --------------------------------------------------------------------------- quiescence gate
# BEFORE removing any controller code/state: prove the managed workloads are stopped. The guard above
# already blocks NEW task admission; this controller command additionally REFUSES on active/unsafe
# build/test/web jobs or unresolved auto-install/HMAC state, blocks on any UNKNOWN component state,
# then STOPS the managed stacks (clients before the shared daemon) and VERIFIES cessation. If it cannot
# prove quiescence it fails closed — we remove the guard we just wrote and abort WITHOUT deleting
# anything. Skipped only for a legacy config-only remainder (no venv/executable/state to inspect).
if [ -x "${VENV}/bin/lhpc" ]; then
	step "Prepare uninstall — stop managed stacks and verify cessation"
	if ! "${VENV}/bin/lhpc" _controller-uninstall-prep --root "$TARGET_DIR"; then
		# Preparation safely REFUSED before teardown began — release the guard (truthfully), retain
		# everything else, and exit nonzero.
		abort_die "Uninstall preparation could not prove the managed stacks are stopped (see the message above) — aborting. Nothing was removed; the checkout, state, and units are untouched."
	fi
elif [ "$CONFIG_ONLY" -eq 1 ]; then
	# The ONLY case that skips workload prep: a legacy config-only remainder with no executable/state
	# to inspect (nothing can be running). (Captured BEFORE the guard was written.)
	note "no ${VENV}/bin/lhpc — config-only remainder, nothing to stop"
else
	# A normal deployment whose controller command is missing/broken — we cannot prove quiescence, so
	# we must NOT delete anything. Release the guard THIS run owns and abort.
	abort_die "the controller command ${VENV}/bin/lhpc is missing, but $TARGET_DIR is not a config-only remainder — cannot prove the managed stacks are stopped. Aborting without removing anything (reinstall/repair, then retry)."
fi

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
		|| grep -qF "PathExists=${TARGET_DIR}/state/selfupdate.request" "$1" \
		|| grep -qF "PathExists=${TARGET_DIR}/state/nginx-restart.request" "$1"
}

# --------------------------------------------------------------------------- units (ordered)
# Order matters: DISABLE the .path FIRST (no new request can trigger the helper), stop the helper,
# stop the console — THEN clear request/in-flight — THEN remove the CANONICAL unit files. Only
# byte-exact canonical units are stopped/removed; a noncanonical same-root unit is left + warned.
step "Managed systemd units"
UNITS_REMOVED=0
# The canonical units, kind:path, in teardown order (used identically by PASS 0/1/2 below).
UNIT_SPECS=(
	"lhpc-nginx-restart.path:${RESTART_PATH_UNIT}"
	"lhpc-nginx-restart.service:${RESTART_UNIT}"
	"lhpc-nginx.service:${NGINX_UNIT}"
	"lhpc-selfupdate.path:${PATH_UNIT}"
	"lhpc-selfupdate.service:${HELPER_UNIT}"
	"lhpc-web.service:${WEB_UNIT}"
)

# PASS 0 — RECOVER from a PRIOR interrupted uninstall. A `*.uninstall-staged` artifact with no live
# canonical counterpart means an earlier run staged a unit aside, then failed to reload AND failed to
# fully restore it. We MUST resurrect such a unit — and prove a successful daemon-reload — before doing
# anything else, so the reload requirement can NEVER be bypassed by the canonical filename being
# temporarily absent. We fail closed on any ambiguity: a staged file that is not byte-exact canonical
# (customized/malformed), a live canonical file coexisting with its staged counterpart, or a restore
# that does not complete all abort while retaining ALL controller code, state and the guard.
RECOVERED=0
for spec in "${UNIT_SPECS[@]}"; do
	kind="${spec%%:*}"; file="${spec#*:}"; staged="${file}.uninstall-staged"
	{ [ -e "$staged" ] || [ -L "$staged" ]; } || continue
	if [ -e "$file" ] || [ -L "$file" ]; then
		die "found BOTH ${file} and ${staged} from a prior interrupted uninstall — refusing to overwrite either. Keep the correct one by hand, then re-run. Nothing was removed."
	fi
	is_canonical "$kind" "$staged" || die "leftover ${staged} is not a byte-exact canonical ${kind} (customized or malformed) — refusing to restore or delete it. Resolve it by hand, then re-run. Nothing was removed."
	mv -f "$staged" "$file" 2>/dev/null || die "could not restore leftover ${staged} -> ${file} — retaining all controller code, state and the uninstall guard. Resolve it, then re-run."
	{ [ -f "$file" ] && [ ! -e "$staged" ]; } || die "restore of leftover ${staged} did not complete — retaining all controller code, state and the uninstall guard. Resolve it, then re-run."
	RECOVERED=1; note "recovered leftover ${kind} from a prior interrupted uninstall"
done
if [ "$RECOVERED" -eq 1 ]; then
	sysctl_ok || die "recovered leftover units but systemctl is unavailable to reload — retaining all controller code, state and the uninstall guard. Re-run when systemctl is available."
	systemctl --user daemon-reload 2>/dev/null || die "recovered leftover units but systemctl --user daemon-reload FAILED — the units are restored; retaining all controller code, state and the uninstall guard. Re-run to retry."
fi

# PASS 1a — READ-ONLY PREFLIGHT over EVERY unit BEFORE any systemd mutation. Every refusal here uses
# `abort_die` (nothing has been touched, so the guard is RELEASED — an aborted uninstall must leave
# the console startable): an unavailable systemctl while a canonical unit exists, or a CUSTOMIZED
# same-root unit. A foreign unit (another deployment) is left untouched and does NOT block. A fully
# completed PASS-0 restoration + successful daemon-reload above is a COHERENT state again, so these
# releases are safe; PASS-0's own dies (ambiguous/partial restore) deliberately retain the guard.
for spec in "${UNIT_SPECS[@]}"; do
	kind="${spec%%:*}"; file="${spec#*:}"
	if is_canonical "$kind" "$file"; then
		sysctl_ok || abort_die "systemctl is unavailable but a canonical ${kind} exists — cannot prove it can be stopped. Nothing was removed."
	elif [ -e "$file" ] || [ -L "$file" ]; then
		if owns_root "$file"; then
			abort_die "${kind} is a CUSTOMIZED unit that references THIS runtime root (${file}) — refusing to delete a root its unit still points at. Run \`lhpc self-update --repair-integration\` to restore the canonical managed units, then retry uninstall (or remove/repoint it by hand). Nothing was removed."
		else
			note "${kind} belongs to a different deployment — left untouched"
		fi
	fi
done
# PASS 1b — the MUTATING stop/disable phase. From the FIRST stop onward a refusal RETAINS the guard
# (plain `die`): a partially dismantled install must keep the console blocked, and the recorded owner
# pid is dead after the abort, so `lhpc self-update --recover-request` is the documented escape.
for spec in "${UNIT_SPECS[@]}"; do
	kind="${spec%%:*}"; file="${spec#*:}"
	if is_canonical "$kind" "$file"; then
		systemctl --user stop "$kind" 2>/dev/null || die "could not stop ${kind} — retaining all controller code, state and the uninstall guard. Resolve it, then re-run uninstall."
		systemctl --user disable "$kind" 2>/dev/null || true
	fi
done
# The watchers + helper are now PROVEN stopped -> safe to clear any pending/in-flight requests
# (self-update AND the nginx-restart escape hatch — explicit, never assumed covered by a dir wipe).
rm -f "${TARGET_DIR}/state/selfupdate.request" "${TARGET_DIR}/state/selfupdate.inflight" \
      "${TARGET_DIR}/state/nginx-restart.request" "${TARGET_DIR}/state/nginx-restart.inflight" 2>/dev/null || true
# PASS 2 — TRANSACTIONAL unit removal. STAGE each canonical unit ASIDE (a rename, NOT a delete), then
# daemon-reload. If reload FAILS (or systemctl is gone), RESTORE the staged units so systemd still sees
# the still-installed units, and abort — retaining ALL controller code, state and the guard. A retry
# re-stages and re-reloads. Controller code/state is deleted ONLY AFTER a successful reload, so a
# reload failure can NEVER be bypassed by a subsequent run finding the files already gone.
STAGED=()
# Restore every staged unit to its canonical path and VERIFY each restore — a rename back that does not
# leave the canonical file in place (and its staged counterpart gone) is a restore FAILURE. Returns
# nonzero if ANY unit could not be restored, so callers never claim "restored" without proof.
restore_staged() {
	local _f _rc=0
	for _f in ${STAGED[@]+"${STAGED[@]}"}; do
		mv -f "${_f}.uninstall-staged" "$_f" 2>/dev/null || true
		if [ ! -f "$_f" ] || [ -e "${_f}.uninstall-staged" ]; then _rc=1; fi
	done
	return "$_rc"
}
for spec in "${UNIT_SPECS[@]}"; do
	kind="${spec%%:*}"; file="${spec#*:}"
	if is_canonical "$kind" "$file"; then
		# A staged counterpart alongside a live canonical file is the both-exist ambiguity PASS 0
		# resolves; if one is present now, refuse to clobber it rather than overwrite blindly.
		{ [ -e "${file}.uninstall-staged" ] || [ -L "${file}.uninstall-staged" ]; } && \
			die "unexpected ${file}.uninstall-staged alongside a canonical ${file} — refusing to overwrite it. Resolve it by hand, then re-run. Nothing further was removed."
		mv -f "$file" "${file}.uninstall-staged" \
			|| { restore_staged || true; die "could not stage ${kind} (${file}) for removal — retaining controller code, state and the guard."; }
		STAGED+=("$file")
		UNITS_REMOVED=1; note "staged $kind for removal"
	fi
done
# If we staged canonical units, daemon-reload MUST succeed before we finalize (and before any code/state
# deletion downstream) — otherwise restore the units and retain everything so a retry converges. If a
# restore itself fails, the staged files remain: a retry recovers them via PASS 0 (which re-proves the
# reload), so the reload requirement is still never bypassed.
if [ "$UNITS_REMOVED" -eq 1 ]; then
	if ! sysctl_ok; then
		if restore_staged; then
			die "systemctl became unavailable — restored the units and retained controller code, state and the uninstall guard."
		fi
		die "systemctl became unavailable AND one or more units could NOT be restored — retaining ALL controller code, state and the guard. Re-run to recover from the staged unit files."
	fi
	if ! systemctl --user daemon-reload 2>/dev/null; then
		if restore_staged; then
			die "systemctl --user daemon-reload FAILED — restored the units (systemd still has them) and retained controller code, state and the guard. Re-run to retry."
		fi
		die "systemctl --user daemon-reload FAILED AND one or more units could NOT be restored — retaining ALL controller code, state and the guard. Re-run to recover from the staged unit files."
	fi
	for _f in ${STAGED[@]+"${STAGED[@]}"}; do rm -f "${_f}.uninstall-staged" 2>/dev/null || true; done   # reload OK -> finalize
fi

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
