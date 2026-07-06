#!/usr/bin/env bash
#
# LoRaHAM Pi Control — self-hosted installer.
#
# Sets up a SELF-HOSTED deployment: the target runtime root is a plain container, LHPC's own
# checkout lives under it at src/loraham-pi-control (like the managed stacks), and the venv is
# OUTSIDE the checkout at venv/lhpc. That way `lhpc self-update` and the code it runs are one
# tree, and self-update's `git clean` can never reach the venv.
#
# Usage:
#   ./install.sh [--source <git-url>] [--target <dir>] [--branch <name>] [--force] [--no-waitress]
#
# Defaults:
#   --source   https://github.com/makrohard/loraham-pi-control.git
#   --target   ~/loraham-pi-control
#   --branch   main
#
# It is safe to re-run: it refuses to clobber an existing checkout unless --force is given.
# Runs entirely as your normal user — no root, no sudo.

set -euo pipefail

# Owner-only from the start. The controller-identity boundary requires the runtime root, src/
# and the checkout to have NO group/other write; a default umask (0002) would make the fresh
# `git clone` group-writable ("identity UNSAFE: checkout is group/other-writable"). Setting
# umask here makes everything this script creates — checkout, venv, bootstrap dirs (bootstrap
# inherits this umask) — mode 0700/0600, so the fix does not depend on the cloned code version.
umask 077

# --------------------------------------------------------------------------- defaults + args
SOURCE_REPO="https://github.com/makrohard/loraham-pi-control.git"
TARGET_DIR="${HOME}/loraham-pi-control"
BRANCH="main"
FORCE=0
WITH_WAITRESS=1
WITH_SERVICE=0
LINK_PATH=1

usage() {
	cat <<'EOF'
LoRaHAM Pi Control — self-hosted installer.

Sets up a SELF-HOSTED deployment: the target runtime root is a plain container, LHPC's own
checkout lives under it at src/loraham-pi-control, and the venv is OUTSIDE the checkout at
venv/lhpc — so `lhpc self-update` and the code it runs are one tree.

Usage:
  ./install.sh [--source <git-url>] [--target <dir>] [--branch <name>]
               [--service] [--no-path] [--force] [--no-waitress]

Defaults:
  --source   https://github.com/makrohard/loraham-pi-control.git
  --target   ~/loraham-pi-control
  --branch   main

  --service      install + enable the user systemd unit (auto-starts the web console on
                 boot, with lingering) — otherwise it is set up but not started
  --no-path      do NOT symlink `lhpc` into ~/.local/bin (leave it off PATH)
  --force        update an existing checkout in place instead of refusing
  --no-waitress  skip installing the production WSGI server

Safe to re-run. Runs as your normal user — no root, no sudo.
EOF
	exit "${1:-0}"
}

die()  { printf 'ERR  %s\n' "$*" >&2; exit 1; }
note() { printf 'OK   %s\n' "$*"; }
step() { printf '\n==> %s\n' "$*"; }

while [ $# -gt 0 ]; do
	case "$1" in
		--source)   SOURCE_REPO="${2:?--source needs a git URL}"; shift 2 ;;
		--target)   TARGET_DIR="${2:?--target needs a directory}"; shift 2 ;;
		--branch)   BRANCH="${2:?--branch needs a name}"; shift 2 ;;
		--service)  WITH_SERVICE=1; shift ;;
		--no-path)  LINK_PATH=0; shift ;;
		--force)    FORCE=1; shift ;;
		--no-waitress) WITH_WAITRESS=0; shift ;;
		-h|--help)  usage 0 ;;
		*)          die "unknown argument: $1 (try --help)" ;;
	esac
done

# Expand a leading tilde (a quoted "~/x" argument does not expand on its own), then
# normalize to an absolute path — without requiring the directory to exist yet.
if [ "$TARGET_DIR" = "~" ]; then
	TARGET_DIR="$HOME"
elif [ "${TARGET_DIR#\~/}" != "$TARGET_DIR" ]; then
	TARGET_DIR="${HOME}/${TARGET_DIR#\~/}"
fi
mkdir -p "$TARGET_DIR"
TARGET_DIR="$(cd "$TARGET_DIR" && pwd)"

CHECKOUT="${TARGET_DIR}/src/loraham-pi-control"
VENV="${TARGET_DIR}/venv/lhpc"

# --------------------------------------------------------------------------- preflight checks
step "Preflight"

command -v git >/dev/null 2>&1 || die "git is not installed (sudo apt install git)"

PY=""
for cand in python3.13 python3.12 python3.11 python3; do
	if command -v "$cand" >/dev/null 2>&1; then PY="$cand"; break; fi
done
[ -n "$PY" ] || die "python3 not found"

# Require >= 3.11 (the codebase uses 3.11+ syntax / tomllib).
if ! "$PY" -c 'import sys; raise SystemExit(0 if sys.version_info[:2] >= (3, 11) else 1)'; then
	die "Python 3.11+ is required (found $("$PY" -V 2>&1))"
fi

# `python3 -m venv` needs the venv module (python3-venv on Debian/Ubuntu).
if ! "$PY" -c 'import venv, ensurepip' >/dev/null 2>&1; then
	die "the venv module is missing (sudo apt install python3-venv)"
fi

note "git present, using $("$PY" -V 2>&1) at $(command -v "$PY")"
note "target runtime root: $TARGET_DIR"
note "checkout:            $CHECKOUT"
note "venv:                $VENV"

# The target root must not itself be a git checkout — that is the TANGLED layout this
# installer exists to avoid (self-hosted keeps .git only under src/loraham-pi-control).
if [ -e "${TARGET_DIR}/.git" ]; then
	die "${TARGET_DIR} is itself a git checkout (tangled layout). Move it aside and re-run, so the runtime root stays a plain container."
fi

if [ -e "$CHECKOUT" ]; then
	if [ "$FORCE" -eq 1 ]; then
		note "checkout exists — updating it in place (--force)"
	else
		die "$CHECKOUT already exists. Re-run with --force to update it in place, or remove it first."
	fi
fi

# --------------------------------------------------------------------------- 1. clone / update
step "1/4  Fetch LHPC (${BRANCH}) into the checkout"

if [ -d "${CHECKOUT}/.git" ]; then
	git -C "$CHECKOUT" remote set-url origin "$SOURCE_REPO"
	git -C "$CHECKOUT" fetch --quiet origin "$BRANCH"
	git -C "$CHECKOUT" checkout --quiet "$BRANCH"
	git -C "$CHECKOUT" reset --hard --quiet "origin/${BRANCH}"
else
	mkdir -p "${TARGET_DIR}/src"
	git clone --quiet --branch "$BRANCH" "$SOURCE_REPO" "$CHECKOUT"
fi
note "at $(git -C "$CHECKOUT" rev-parse --short HEAD) on ${BRANCH}"

# --------------------------------------------------------------------------- 2. venv (outside)
step "2/4  Create the venv (outside the checkout)"

if [ ! -x "${VENV}/bin/python" ]; then
	mkdir -p "$(dirname "$VENV")"
	"$PY" -m venv "$VENV"
fi
"${VENV}/bin/python" -m pip install --quiet --upgrade pip
note "venv ready: ${VENV}"

# --------------------------------------------------------------------------- 3. editable install
step "3/4  Install LHPC (editable) into the venv"

"${VENV}/bin/pip" install --quiet -e "$CHECKOUT"
if [ "$WITH_WAITRESS" -eq 1 ]; then
	"${VENV}/bin/pip" install --quiet waitress || \
		printf 'WARN could not install waitress — the dev server fallback will be used.\n' >&2
fi
note "installed: $("${VENV}/bin/lhpc" --version 2>/dev/null || echo lhpc)"

# --------------------------------------------------------------------------- 4. bootstrap root
step "4/4  Bootstrap the runtime root"

LHPC_RUNTIME_ROOT="$TARGET_DIR" "${VENV}/bin/lhpc" bootstrap --yes

# --------------------------------------------------------------------------- put lhpc on PATH
# A venv is NOT on PATH by default, and `export PATH=…` from this script cannot persist to
# your shell. Symlink `lhpc` into ~/.local/bin (the standard user bin, added to PATH at
# login on Pi OS/Debian) so `lhpc` just works in new shells / after reboot. Opt out: --no-path.
LHPC_ON_PATH=0
LOCAL_BIN="${HOME}/.local/bin"
if [ "$LINK_PATH" -eq 1 ]; then
	step "Link lhpc onto PATH"
	mkdir -p "$LOCAL_BIN"
	ln -sfn "${VENV}/bin/lhpc" "${LOCAL_BIN}/lhpc"
	note "symlinked ${LOCAL_BIN}/lhpc -> ${VENV}/bin/lhpc"
	case ":${PATH}:" in
		*":${LOCAL_BIN}:"*) LHPC_ON_PATH=1 ;;
		*) printf 'WARN %s is not on PATH in THIS shell — open a new login shell (or reboot), or run: export PATH="%s:$PATH"\n' \
			"$LOCAL_BIN" "$LOCAL_BIN" >&2 ;;
	esac
fi

# --------------------------------------------------------------------------- optional: service
# The web console does NOT auto-start unless a systemd user service is installed + enabled.
# `--service` sets that up (with lingering, so it survives logout/reboot). The unit is
# generated with THIS install's absolute paths, so it is correct for any --target.
if [ "$WITH_SERVICE" -eq 1 ]; then
	step "Install + enable the web console user service"
	if ! command -v systemctl >/dev/null 2>&1; then
		printf 'WARN systemctl not available — skipping --service.\n' >&2
	else
		UNIT_DIR="${HOME}/.config/systemd/user"
		mkdir -p "$UNIT_DIR"
		cat > "${UNIT_DIR}/lhpc-web.service" <<UNIT
[Unit]
Description=LoRaHAM Pi Control web console (loopback-only)
Documentation=file://${CHECKOUT}/docs/deployment.md
After=network-online.target
Wants=network-online.target
StartLimitIntervalSec=60
StartLimitBurst=5

[Service]
Type=simple
Environment=LHPC_RUNTIME_ROOT=${TARGET_DIR}
WorkingDirectory=${CHECKOUT}
ExecStart=${VENV}/bin/lhpc web --host 127.0.0.1 --port 8770
Restart=on-failure
RestartSec=3
StandardOutput=journal
StandardError=journal
SyslogIdentifier=lhpc-web
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths=${TARGET_DIR}
PrivateTmp=false

[Install]
WantedBy=default.target
UNIT
		# Best-effort: `systemctl --user` needs a user D-Bus session, which may be absent on
		# a headless/SSH box. Never hard-abort the install — give the exact manual commands.
		systemctl --user daemon-reload 2>/dev/null || true
		if systemctl --user enable --now lhpc-web.service 2>/dev/null; then
			loginctl enable-linger "$USER" >/dev/null 2>&1 || \
				printf 'WARN could not enable lingering — the service may not start before login.\n' >&2
			note "lhpc-web.service enabled (http://127.0.0.1:8770/); logs: journalctl --user -u lhpc-web -f"
		else
			printf 'WARN could not enable the service now (no user systemd session?). The unit is written to %s.\n      Finish after login with:\n        systemctl --user daemon-reload && systemctl --user enable --now lhpc-web.service\n        loginctl enable-linger %s\n' \
				"$UNIT_DIR" "$USER" >&2
		fi
	fi
fi

# --------------------------------------------------------------------------- verify + next steps
step "Verify"

if LHPC_RUNTIME_ROOT="$TARGET_DIR" "${VENV}/bin/lhpc" status >/dev/null 2>&1; then
	note "self-hosted deployment ready at ${TARGET_DIR}"
else
	printf 'WARN `lhpc status` returned non-zero — inspect it manually.\n' >&2
fi

# The command to use in the printed next-steps: bare `lhpc` if it is (or will be) on PATH.
if [ "$LINK_PATH" -eq 1 ]; then LHPC="lhpc"; else LHPC="${VENV}/bin/lhpc"; fi

cat <<EOF

Self-hosted install complete.

  Runtime root : ${TARGET_DIR}
  Checkout     : ${CHECKOUT}
  Venv         : ${VENV}
  CLI          : ${LOCAL_BIN}/lhpc$([ "$LINK_PATH" -eq 0 ] && echo " (skipped --no-path; use ${VENV}/bin/lhpc)")
  Web service  : $([ "$WITH_SERVICE" -eq 1 ] && echo "enabled (auto-starts on boot)" || echo "not enabled (re-run with --service, or see docs/deployment.md)")

EOF

if [ "$LINK_PATH" -eq 1 ] && [ "$LHPC_ON_PATH" -eq 0 ]; then
	printf 'Open a new login shell or reboot so `lhpc` is on PATH (or: export PATH="%s:$PATH").\n\n' "$LOCAL_BIN"
fi

cat <<EOF
Next:
  ${LHPC} status                 # the controller row should read: identity ok
  ${LHPC} install daemon --yes   # adopt + verify a stack's source
  ${LHPC} build daemon           # build it
EOF
if [ "$WITH_SERVICE" -eq 0 ]; then
	printf '  %s web                    # http://127.0.0.1:8770/  (loopback only)\n' "$LHPC"
fi
