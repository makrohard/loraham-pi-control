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
# It ONLY performs a fresh install. It is NOT an updater, repair, migration or
# stack-installer. Update an existing controller with:  lhpc self-update
#
# Usage:  ./install.sh [--target <dir>] [--no-service] [--no-path]
#
# Runs as your normal user — no root, no sudo. It refuses to touch an existing
# checkout and never runs destructive git operations.

set -euo pipefail
umask 077          # everything this script creates is owner-only (0700/0600) — the
                   # controller-identity boundary needs no group/other write.

# --------------------------------------------------------------------------- fixed inputs
readonly REPO_URL="https://github.com/makrohard/loraham-pi-control.git"   # canonical, fixed
readonly BRANCH="main"                                                    # supported, fixed

TARGET_DIR="${HOME}/loraham-pi-control"
WITH_SERVICE=1
LINK_PATH=1
SERVICE_UP=0

usage() {
	cat <<'EOF'
LoRaHAM Pi Control — controller installer (INITIAL install only).

Usage:
  ./install.sh [--target <dir>] [--no-service] [--no-path]

  --target <dir>  where to install (default: ~/loraham-pi-control)
  --no-service    do not install/enable the web-console systemd user service
  --no-path       do not symlink `lhpc` into ~/.local/bin
  -h, --help      show this help

Installs a fresh self-hosted controller from the canonical repository (branch `main`).
It refuses to touch an existing checkout and runs no destructive git operations.

This is for INITIAL installation only. To UPDATE an existing controller, use:
  lhpc self-update
EOF
	exit "${1:-0}"
}

die()  { printf 'ERR  %s\n' "$*" >&2; exit 1; }
note() { printf 'OK   %s\n' "$*"; }
warn() { printf 'WARN %s\n' "$*" >&2; }
step() { printf '\n==> %s\n' "$*"; }
no_symlink() { [ ! -L "$1" ] || die "$2 is a symlink — refusing (controller directories must not be symlinks)."; }

while [ $# -gt 0 ]; do
	case "$1" in
		--target)      TARGET_DIR="${2:?--target needs a directory}"; shift 2 ;;
		--no-service)  WITH_SERVICE=0; shift ;;
		--no-path)     LINK_PATH=0; shift ;;
		-h|--help)     usage 0 ;;
		*)             die "unknown argument: $1 (try --help)" ;;
	esac
done

# Expand a leading tilde and make the target absolute (it need not exist yet).
if [ "$TARGET_DIR" = "~" ]; then
	TARGET_DIR="$HOME"
elif [ "${TARGET_DIR#\~/}" != "$TARGET_DIR" ]; then
	TARGET_DIR="${HOME}/${TARGET_DIR#\~/}"
fi
mkdir -p "$TARGET_DIR"
no_symlink "$TARGET_DIR" "runtime root $TARGET_DIR"
TARGET_DIR="$(cd "$TARGET_DIR" && pwd -P)"

readonly CHECKOUT="${TARGET_DIR}/src/loraham-pi-control"
readonly VENV="${TARGET_DIR}/venv/lhpc"
readonly LOCAL_BIN="${HOME}/.local/bin"
readonly UNIT_DIR="${HOME}/.config/systemd/user"
readonly UNIT="${UNIT_DIR}/lhpc-web.service"

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

# --------------------------------------------------------------------------- safe target
# Refuse a tangled layout and an existing checkout WITHOUT touching either; refuse a symlink
# anywhere in the chain we manage (never mutate through a symlink / reused path).
[ ! -e "${TARGET_DIR}/.git" ] || die "$TARGET_DIR is itself a git checkout (tangled) — move it aside and re-run."
[ ! -e "${TARGET_DIR}/src" ]  || no_symlink "${TARGET_DIR}/src"  "src"
[ ! -e "${TARGET_DIR}/venv" ] || no_symlink "${TARGET_DIR}/venv" "venv"
if [ -e "$CHECKOUT" ]; then
	die "a controller checkout already exists at $CHECKOUT — refusing. This installer only does fresh installs and never touches an existing checkout; update it with: lhpc self-update"
fi

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
"${VENV}/bin/pip" install --quiet waitress 2>/dev/null || warn "waitress not installed — the dev-server fallback will be used."
note "venv ready: ${VENV}"

# --------------------------------------------------------------------------- 3. bootstrap root
step "3/4  Bootstrap the runtime root"
LHPC_RUNTIME_ROOT="$TARGET_DIR" "${VENV}/bin/lhpc" bootstrap --yes

# --------------------------------------------------------------------------- 3b. command link
if [ "$LINK_PATH" -eq 1 ]; then
	mkdir -p "$LOCAL_BIN"
	ln -sfn "${VENV}/bin/lhpc" "${LOCAL_BIN}/lhpc"
	note "symlinked ${LOCAL_BIN}/lhpc -> ${VENV}/bin/lhpc"
	case ":${PATH}:" in
		*":${LOCAL_BIN}:"*) : ;;
		*) warn "${LOCAL_BIN} is not on PATH in this shell — open a new login shell (or reboot), or run: export PATH=\"${LOCAL_BIN}:\$PATH\"" ;;
	esac
fi

# --------------------------------------------------------------------------- 4. verify identity
# Before reporting success, prove the deployed controller passes LHPC's OWN controller-identity
# rules (self-hosted, canonical origin, main, owner-only perms, repo == checkout == package).
step "4/4  Verify controller identity"
# `python -I` (isolated): do NOT put cwd / PYTHONPATH on sys.path, so running install.sh from
# inside another checkout can't make `import lhpc` resolve to the wrong tree.
if ! LHPC_RUNTIME_ROOT="$TARGET_DIR" "${VENV}/bin/python" -I - <<'PYIDENT'
import sys
from lhpc.core.probes import RealSystem
from lhpc.core.services import ControllerService
v = ControllerService(system=RealSystem()).controller_identity_live()
print("     identity: %s — %s" % (v["status"], v["reason"]))
sys.exit(0 if v["status"] == "ok" else 1)
PYIDENT
then
	die "the installed controller did NOT pass identity validation (see the reason above). No service was enabled."
fi
note "controller identity: ok"

# --------------------------------------------------------------------------- web service
if [ "$WITH_SERVICE" -eq 1 ]; then
	step "Install + enable the web-console user service"
	if ! command -v systemctl >/dev/null 2>&1; then
		warn "systemctl not available — skipping the service (start manually with: lhpc web)."
	else
		mkdir -p "$UNIT_DIR"
		cat > "$UNIT" <<UNIT
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
		systemctl --user daemon-reload 2>/dev/null || true
		if systemctl --user enable --now lhpc-web.service 2>/dev/null; then
			loginctl enable-linger "$USER" >/dev/null 2>&1 || warn "could not enable lingering — the service may not start before login."
			SERVICE_UP=1
			note "lhpc-web.service enabled (http://127.0.0.1:8770/); logs: journalctl --user -u lhpc-web -f"
		else
			warn "could not start the service now (no user systemd session?). The unit is at ${UNIT}. Finish after login with: systemctl --user enable --now lhpc-web.service && loginctl enable-linger ${USER}"
		fi
	fi
fi

# --------------------------------------------------------------------------- done
LHPC="lhpc"; [ "$LINK_PATH" -eq 1 ] || LHPC="${VENV}/bin/lhpc"
cat <<EOF

Install complete — self-hosted controller at ${TARGET_DIR}

  CLI : $([ "$LINK_PATH" -eq 1 ] && echo "${LOCAL_BIN}/lhpc (open a new shell if not yet on PATH)" || echo "${VENV}/bin/lhpc")

Next:
  ${LHPC} status              # controller row reads: identity ok
  ${LHPC} install daemon --yes && ${LHPC} build daemon
  ${LHPC} self-update         # update the controller later
EOF

# The web console status is the LAST thing the operator sees — running + link + controls.
if [ "$SERVICE_UP" -eq 1 ]; then
	cat <<EOF

────────────────────────────────────────────────────────────────────────
The web console is RUNNING:   http://127.0.0.1:8770/   (loopback only)

  Stop it now                : systemctl --user stop lhpc-web
  Start it again             : systemctl --user start lhpc-web
  Status / live logs         : systemctl --user status lhpc-web
                               journalctl --user -u lhpc-web -f
  Do NOT auto-start on boot  : systemctl --user disable lhpc-web
  Re-enable auto-start       : systemctl --user enable lhpc-web

(It currently auto-starts on boot. Stop it before running \`${LHPC} self-update\`.)
────────────────────────────────────────────────────────────────────────
EOF
else
	cat <<EOF

────────────────────────────────────────────────────────────────────────
The web console is NOT running (no service enabled).

  Start it manually (foreground; Ctrl-C to stop):  ${LHPC} web
  It serves:                                        http://127.0.0.1:8770/   (loopback only)

To run it as an auto-starting background service, re-run install.sh without --no-service.
────────────────────────────────────────────────────────────────────────
EOF
fi
