#!/usr/bin/env bash
#
# LoRaHAM Pi Control — controller INSTALLER (initial bootstrap only).
#
# Installs a NEW self-hosted LHPC controller deployment so that `lhpc` is usable:
#
#   <target>/                     runtime root (config/ state/ logs/ …)
#   <target>/src/loraham-pi-control   LHPC's own checkout (clone of main)
#   <target>/venv/lhpc                the venv, OUTSIDE the checkout
#
# It ONLY performs a fresh install. It is NOT an updater, repair or stack-installer.
# Update an existing controller with `lhpc self-update`; install/repair the managed web +
# one-click updater units on an existing controller with `lhpc self-update --repair-integration`.
#
# Usage:  ./install.sh [--target <dir>] [--no-service] [--no-path]
#
# Runs as your normal user — no root, no sudo. It refuses unsafe targets, foreign command
# links / systemd units, and any existing checkout, and rolls back on a mid-install failure.

set -euo pipefail
: "${USER:=$(id -un)}"   # $USER can be unset in minimal envs (systemd/su); bind it so set -u never aborts.
umask 077          # everything this script creates is owner-only (0700/0600).

# --------------------------------------------------------------------------- fixed inputs
readonly REPO_URL="https://github.com/makrohard/loraham-pi-control.git"   # canonical, fixed
readonly BRANCH="main"                                                    # supported, fixed

TARGET_DIR="${HOME}/loraham-pi-control"
WITH_SERVICE=1
LINK_PATH=1
SERVICE_UP=0
HTTPS_UP=0
MANUAL_STEPS=""          # mandatory operator commands, printed as ONE block at the very bottom
ROLLBACK_ARMED=0
TARGET_CREATED=0
PRE_ENTRIES=""
CREATED_LINK=""
CREATED_UNITS=""

usage() {
	cat <<'EOF'
LoRaHAM Pi Control — controller installer (INITIAL install only).

Usage:
  ./install.sh [--target <dir>] [--no-service] [--no-path]

  --target <dir>  where to install (default: ~/loraham-pi-control)
  --no-service    do not install/enable the web-console + updater systemd units
  --no-path       do not symlink `lhpc` into ~/.local/bin
  -h, --help      show this help

Installs a fresh self-hosted controller from the canonical repository (branch `main`).
Refuses unsafe targets, foreign integration, and existing checkouts; rolls back on failure.

For an EXISTING controller: update with `lhpc self-update`; (re)install the managed
web console + one-click updater with `lhpc self-update --repair-integration`.
EOF
	exit "${1:-0}"
}

die()  { printf 'ERR  %s\n' "$*" >&2; exit 1; }
note() { printf 'OK   %s\n' "$*"; }
warn() { printf 'WARN %s\n' "$*" >&2; }
step() { printf '\n==> %s\n' "$*"; }
no_symlink() { [ ! -L "$1" ] || die "${2:-$1} is a symlink — refusing (controller paths must not be symlinks)."; }

while [ $# -gt 0 ]; do
	case "$1" in
		--target)      TARGET_DIR="${2:?--target needs a directory}"; shift 2 ;;
		--no-service)  WITH_SERVICE=0; shift ;;
		--no-path)     LINK_PATH=0; shift ;;
		-h|--help)     usage 0 ;;
		*)             die "unknown argument: $1 (try --help)" ;;
	esac
done

# --------------------------------------------------------------------------- target resolution
# Expand a leading tilde; make absolute WITHOUT creating anything or following symlinks.
if [ "$TARGET_DIR" = "~" ]; then
	TARGET_DIR="$HOME"
elif [ "${TARGET_DIR#\~/}" != "$TARGET_DIR" ]; then
	TARGET_DIR="${HOME}/${TARGET_DIR#\~/}"
fi
case "$TARGET_DIR" in /*) ;; *) TARGET_DIR="${PWD}/${TARGET_DIR}" ;; esac
# collapse a trailing slash (but keep "/")
TARGET_DIR="${TARGET_DIR%/}"; [ -n "$TARGET_DIR" ] || TARGET_DIR="/"

HOME_ABS="$(cd "$HOME" && pwd -P)"
readonly CHECKOUT="${TARGET_DIR}/src/loraham-pi-control"
readonly VENV="${TARGET_DIR}/venv/lhpc"
readonly LOCAL_BIN="${HOME}/.local/bin"
readonly LOCAL_BIN_LINK="${LOCAL_BIN}/lhpc"
readonly UNIT_DIR="${HOME}/.config/systemd/user"
readonly WEB_UNIT="${UNIT_DIR}/lhpc-web.service"
readonly HELPER_UNIT="${UNIT_DIR}/lhpc-selfupdate.service"
readonly PATH_UNIT="${UNIT_DIR}/lhpc-selfupdate.path"
readonly NGINX_UNIT="${UNIT_DIR}/lhpc-nginx.service"

# --------------------------------------------------------------------------- target safety
step "Target safety"
# 1) representable, not a protected path, no symlinked ancestor (anywhere, not just under $HOME).
case "$TARGET_DIR" in
	*[!A-Za-z0-9._/-]*) die "target path has unsafe characters — use only [A-Za-z0-9._/-]: $TARGET_DIR" ;;
esac
[ "$TARGET_DIR" != "/" ]         || die "refusing to install at /"
[ "$TARGET_DIR" != "$HOME" ]     || die "refusing to install directly at \$HOME"
[ "$TARGET_DIR" != "$HOME_ABS" ] || die "refusing to install directly at \$HOME"
_anc="$TARGET_DIR"
while :; do
	[ ! -L "$_anc" ] || die "path component is a symlink — refusing: $_anc"
	_anc="$(dirname "$_anc")"
	{ [ "$_anc" = "/" ] || [ "$_anc" = "." ]; } && break
done
# 2) freshness: absent, empty, or ONLY a recognised controller remainder {config, backups, .lhpc-root}.
if [ -e "$TARGET_DIR" ]; then
	no_symlink "$TARGET_DIR" "runtime root $TARGET_DIR"
	[ -d "$TARGET_DIR" ] || die "$TARGET_DIR exists and is not a directory."
	for _e in "$TARGET_DIR"/* "$TARGET_DIR"/.[!.]* "$TARGET_DIR"/..?*; do
		[ -e "$_e" ] || continue
		case "$(basename "$_e")" in
			config|backups|.lhpc-root) ;;
			*) die "$TARGET_DIR is not empty and not a config-only remainder (found $(basename "$_e")) — refusing." ;;
		esac
	done
	# a reused config/backups/.lhpc-root must itself be a real file/dir, never a symlink.
	for _r in config backups .lhpc-root; do
		[ ! -e "${TARGET_DIR}/${_r}" ] || no_symlink "${TARGET_DIR}/${_r}" "${TARGET_DIR}/${_r}"
	done
	[ ! -e "${TARGET_DIR}/.git" ] || die "$TARGET_DIR is itself a git checkout (tangled) — move it aside."
fi
# 3) refuse foreign command link / systemd units (never overwrite another deployment's integration).
if [ -e "$LOCAL_BIN_LINK" ] || [ -L "$LOCAL_BIN_LINK" ]; then
	if [ ! -L "$LOCAL_BIN_LINK" ] || [ "$(readlink "$LOCAL_BIN_LINK")" != "${VENV}/bin/lhpc" ]; then
		[ "$LINK_PATH" -eq 0 ] || die "$LOCAL_BIN_LINK already exists and is not this target's link — pass --no-path to keep it, or remove it."
	fi
fi
if [ "$WITH_SERVICE" -eq 1 ] && [ -e "$UNIT_DIR" ]; then
	no_symlink "$UNIT_DIR" "$UNIT_DIR"
	for _u in "$WEB_UNIT" "$HELPER_UNIT" "$PATH_UNIT" "$NGINX_UNIT"; do
		if [ -e "$_u" ] || [ -L "$_u" ]; then
			die "$_u already exists — a fresh install never overwrites systemd units. Remove it, or pass --no-service and later run: lhpc self-update --repair-integration"
		fi
		[ ! -e "${_u}.d" ] || die "$(basename "$_u") has a drop-in dir (${_u}.d) — resolve it first, or pass --no-service."
	done
fi
note "target safe: $TARGET_DIR"

# --------------------------------------------------------------------------- rollback arming
# Snapshot the pre-existing top-level entries; on ANY failure after this point we remove exactly
# what we created (all bootstrap-made dirs + units + link), leaving a config-only remainder intact.
[ -e "$TARGET_DIR" ] || TARGET_CREATED=1
[ ! -d "$TARGET_DIR" ] || PRE_ENTRIES="$(cd "$TARGET_DIR" && ls -A 2>/dev/null || true)"

rollback() {
	local ec=$?
	trap - EXIT
	[ "$ROLLBACK_ARMED" -eq 1 ] || exit "$ec"
	warn "install failed (exit $ec) — rolling back what this run created"
	for _u in $CREATED_UNITS; do rm -f "$_u"; done
	[ -z "$CREATED_LINK" ] || rm -f "$CREATED_LINK"
	command -v systemctl >/dev/null 2>&1 && systemctl --user daemon-reload 2>/dev/null || true
	if [ -d "$TARGET_DIR" ]; then
		for _e in "$TARGET_DIR"/* "$TARGET_DIR"/.[!.]* "$TARGET_DIR"/..?*; do
			[ -e "$_e" ] || continue
			_b="$(basename "$_e")"
			case " $PRE_ENTRIES " in *" $_b "*) : ;; *) rm -rf "$_e" ;; esac
		done
		[ "$TARGET_CREATED" -eq 1 ] && rmdir "$TARGET_DIR" 2>/dev/null || true
	fi
	warn "rollback done: created items removed; any pre-existing config left intact."
	exit "$ec"
}
trap rollback EXIT
ROLLBACK_ARMED=1

# --------------------------------------------------------------------------- preflight
step "Preflight"
command -v git >/dev/null 2>&1 || die "git is not installed (sudo apt install git)"
PY=""
for cand in python3.13 python3.12 python3.11 python3; do
	command -v "$cand" >/dev/null 2>&1 && { PY="$cand"; break; }
done
[ -n "$PY" ] || die "python3 not found"
"$PY" -c 'import sys; raise SystemExit(0 if sys.version_info[:2] >= (3, 11) else 1)' \
	|| die "Python 3.11+ is required (found $("$PY" -V 2>&1))"
"$PY" -c 'import venv, ensurepip' >/dev/null 2>&1 \
	|| die "the venv module is missing (sudo apt install python3-venv)"
note "using $("$PY" -V 2>&1); target $TARGET_DIR"

# --------------------------------------------------------------------------- 1. clone (fresh)
step "1/4  Clone LHPC (${BRANCH}) — fresh, non-destructive"
mkdir -p "${TARGET_DIR}/src"
git clone --quiet --branch "$BRANCH" --single-branch "$REPO_URL" "$CHECKOUT"
note "cloned $(git -C "$CHECKOUT" rev-parse --short HEAD) on ${BRANCH}"

# --------------------------------------------------------------------------- 2. venv (outside)
step "2/4  Create the venv (outside the checkout) + install"
mkdir -p "$(dirname "$VENV")"
"$PY" -m venv "$VENV"
"${VENV}/bin/python" -m pip install --quiet --upgrade pip
"${VENV}/bin/pip" install --quiet -e "$CHECKOUT"
# waitress + cryptography are DECLARED dependencies (installed by the editable install above);
# waitress is required for productive serving (no dev-server fallback on the socket path).
"${VENV}/bin/python" -c 'import waitress, cryptography' 2>/dev/null || \
	warn "waitress/cryptography missing — reinstall the venv (both are required dependencies)."
# nginx is a REQUIRED SYSTEM dependency of the production HTTPS/mTLS webserver. LHPC never
# installs system packages itself (see docs/webserver.md, lhpc.core.deps) — detect + instruct
# in operator context. Absence does NOT abort the base install (the loopback console + radio
# stacks run without it); the production webserver is gated on it at `lhpc webserver apply`.
if command -v nginx >/dev/null 2>&1; then
	HAVE_NGINX=1
else
	HAVE_NGINX=0
	warn "REQUIRED system dependency 'nginx' is not installed — the HTTPS/mTLS webserver stays off until you install it (exact commands repeated at the very end)."
	# Record the mandatory manual steps; they are printed as one block at the bottom so they are
	# the LAST thing on screen, not lost in the middle of the log.
	MANUAL_STEPS="$(printf '%s\n%s' \
		'sudo apt install -y nginx' \
		'lhpc webserver init && lhpc webserver start-service   # -> https://127.0.0.1:8443/ (local: no auth)')"
fi
note "venv ready: ${VENV}"

# --------------------------------------------------------------------------- 3. bootstrap root
step "3/4  Bootstrap the runtime root"
LHPC_RUNTIME_ROOT="$TARGET_DIR" "${VENV}/bin/lhpc" bootstrap --yes
# Durable, root-bound deployment marker (owner-only). Repair/uninstall rely on it.
printf '{"schema_version": 1, "root": "%s"}\n' "$TARGET_DIR" > "${TARGET_DIR}/.lhpc-root"
chmod 600 "${TARGET_DIR}/.lhpc-root"

# --------------------------------------------------------------------------- 3b. command link
if [ "$LINK_PATH" -eq 1 ]; then
	mkdir -p "$LOCAL_BIN"
	ln -sfn "${VENV}/bin/lhpc" "$LOCAL_BIN_LINK"
	CREATED_LINK="$LOCAL_BIN_LINK"
	note "symlinked ${LOCAL_BIN_LINK} -> ${VENV}/bin/lhpc"
	case ":${PATH}:" in
		*":${LOCAL_BIN}:"*) : ;;
		*) warn "${LOCAL_BIN} is not on PATH in this shell — open a new login shell, or run: export PATH=\"${LOCAL_BIN}:\$PATH\"" ;;
	esac
fi

# --------------------------------------------------------------------------- 4. verify identity
step "4/4  Verify controller identity"
if ! LHPC_RUNTIME_ROOT="$TARGET_DIR" "${VENV}/bin/python" -I - <<'PYIDENT'
import sys
from lhpc.core.probes import RealSystem
from lhpc.core.services import ControllerService
v = ControllerService(system=RealSystem()).controller_identity_live()
print("     identity: %s — %s" % (v["status"], v["reason"]))
sys.exit(0 if v["status"] == "ok" else 1)
PYIDENT
then
	die "the installed controller did NOT pass identity validation (see the reason above)."
fi
note "controller identity: ok"

# --------------------------------------------------------------------------- web + updater units
if [ "$WITH_SERVICE" -eq 1 ]; then
	step "Install + enable the managed web console + one-click updater"
	if ! command -v systemctl >/dev/null 2>&1; then
		warn "systemctl not available — skipping the units (start manually with: lhpc web; add one-click later with: lhpc self-update --repair-integration)."
	else
		mkdir -p "$UNIT_DIR"
		# CANONICAL units come from the single renderer (byte-exact with what the integrity
		# proof expects); no heredoc duplication. The web unit blocks the user-systemd bus and
		# Wants= the .path watcher; the helper is sandboxed + declarative (no systemctl).
		render_unit() { "${VENV}/bin/python" -m lhpc.core.updater_units render "$1" "$TARGET_DIR" "$CHECKOUT" "$VENV"; }
		render_unit lhpc-web.service        > "$WEB_UNIT"
		render_unit lhpc-selfupdate.service > "$HELPER_UNIT"
		render_unit lhpc-selfupdate.path    > "$PATH_UNIT"
		render_unit lhpc-nginx.service      > "$NGINX_UNIT"
		CREATED_UNITS="$WEB_UNIT $HELPER_UNIT $PATH_UNIT $NGINX_UNIT"
		systemctl --user daemon-reload 2>/dev/null || true
		# Enable the request watcher + the console (the .path is also pulled up by the web unit's
		# Wants=, but enabling it makes it survive a manual `systemctl stop lhpc-web`).
		systemctl --user enable lhpc-selfupdate.path >/dev/null 2>&1 || true
		# Enable the nginx TLS front-end ONLY when nginx is installed (NOT --now: it starts once
		# LHPC has generated a proxy config via `lhpc webserver init && lhpc webserver start-service`; a
		# ConditionPathExists gates it until then). If nginx is absent the unit file is still
		# written (byte-exact canonical) but not enabled — install nginx then re-run integration.
		if [ "${HAVE_NGINX:-0}" -eq 1 ]; then
			systemctl --user enable lhpc-nginx.service >/dev/null 2>&1 || true
		else
			warn "lhpc-nginx.service written but NOT enabled (nginx missing) — after 'sudo apt install -y nginx' run: systemctl --user enable lhpc-nginx.service"
		fi
		if systemctl --user enable --now lhpc-web.service 2>/dev/null; then
			loginctl enable-linger "$USER" >/dev/null 2>&1 || warn "could not enable lingering — the service may not start before login."
			SERVICE_UP=1
			# The managed web unit serves a Unix socket (no TCP) behind nginx — NOT :8770.
			note "lhpc-web.service enabled (Waitress on a Unix socket, behind nginx); one-click self-update ready."
		else
			warn "could not start the service now (no user systemd session?). Finish after login with: systemctl --user enable --now lhpc-web.service && loginctl enable-linger ${USER}"
		fi
		# HTTPS bring-up: when nginx is present, generate the PKI + proxy config and start the TLS
		# front-end NOW, so the console is reachable at https://127.0.0.1:8443/ (loopback OPEN, no
		# auth; remote requires a client cert) right after install — the default profile. `init`
		# is idempotent (keeps existing PKI); `start-service` validates + promotes the config and
		# starts lhpc-nginx.service. Loopback bind only — nothing is exposed to the network.
		if [ "${HAVE_NGINX:-0}" -eq 1 ]; then
			"${VENV}/bin/lhpc" webserver init >/dev/null 2>&1 || true
			if WS_OUT="$("${VENV}/bin/lhpc" webserver start-service 2>&1)"; then
				HTTPS_UP=1
				note "HTTPS console started: https://127.0.0.1:8443/  (local: no auth; remote: client cert required)"
			else
				warn "HTTPS front-end could not start yet: ${WS_OUT##*$'\n'}"
				MANUAL_STEPS='lhpc webserver init && lhpc webserver start-service   # -> https://127.0.0.1:8443/ (local: no auth)'
			fi
		fi
	fi
fi

# --------------------------------------------------------------------------- done (disarm rollback)
ROLLBACK_ARMED=0
trap - EXIT
LHPC="lhpc"; [ "$LINK_PATH" -eq 1 ] || LHPC="${VENV}/bin/lhpc"
cat <<EOF

Install complete — self-hosted controller at ${TARGET_DIR}

  CLI : $([ "$LINK_PATH" -eq 1 ] && echo "${LOCAL_BIN_LINK} (open a new shell if not yet on PATH)" || echo "${VENV}/bin/lhpc")

Next:
  ${LHPC} status              # controller row reads: identity ok
  ${LHPC} install daemon --yes && ${LHPC} build daemon
  ${LHPC} self-update         # (or one-click Update in the web console)
EOF

if [ "$HTTPS_UP" -eq 1 ]; then
	cat <<EOF

────────────────────────────────────────────────────────────────────────
The HTTPS console is RUNNING:   https://127.0.0.1:8443/
  Local access is OPEN (no client certificate); remote access requires a
  client cert and is OFF until you explicitly expose it. Your browser will
  warn about the self-signed CA on first visit — that is expected.

  Update the controller      : click "Update now" in the console (one-click), or
                               run \`${LHPC} self-update --apply\`
  Stop / start / status      : systemctl --user stop|start|status lhpc-nginx lhpc-web
  Live logs                  : journalctl --user -u lhpc-web -f
  Do NOT auto-start on boot  : systemctl --user disable lhpc-nginx lhpc-web
────────────────────────────────────────────────────────────────────────
EOF
elif [ "$SERVICE_UP" -eq 1 ]; then
	cat <<EOF

────────────────────────────────────────────────────────────────────────
The managed web service is running (behind nginx, on a Unix socket).
The HTTPS front-end at https://127.0.0.1:8443/ is not up yet — see the
commands below to finish it.

  Quick local console (no nginx): ${LHPC} web   (loopback http://127.0.0.1:8770/, non-productive)
  Live logs                     : journalctl --user -u lhpc-web -f
────────────────────────────────────────────────────────────────────────
EOF
else
	cat <<EOF

────────────────────────────────────────────────────────────────────────
The web console is NOT running (no service enabled).

  Start it manually (foreground; Ctrl-C to stop):  ${LHPC} web
  It serves:                                        http://127.0.0.1:8770/   (loopback only)

To run it as an auto-starting service WITH one-click self-update, run:
  ${LHPC} self-update --repair-integration
────────────────────────────────────────────────────────────────────────
EOF
fi

# Mandatory operator commands — ALWAYS the very last thing printed, so nothing manual is missed.
if [ -n "$MANUAL_STEPS" ]; then
	printf '\n'
	printf '========================================================================\n'
	printf 'TO FINISH SETUP — run these commands, in order:\n\n'
	printf '%s\n' "$MANUAL_STEPS" | while IFS= read -r _cmd; do printf '  %s\n' "$_cmd"; done
	printf '========================================================================\n'
fi
