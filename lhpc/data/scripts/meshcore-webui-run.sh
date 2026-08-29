#!/bin/bash
# Launch the meshcore-webui backend (FastAPI/uvicorn) as an LHPC-managed Companion CLIENT.
#   $1 = source dir (the pinned meshcore-webui checkout; backend venv lives in backend/.venv)
#   $2 = runtime root ({runtime})
#   $3 = static dir (the LHPC-shipped prebuilt frontend dist)
#   $4 = companion host   $5 = companion port   $6 = bind addr   $7 = bind port
# LHPC owns identity/GPS/radio/lifecycle; this process is only a browser-facing client of
# the local Companion TCP endpoint. Backend binds loopback; LHPC's nginx is the perimeter.
set -eu
src="$1"; runtime="$2"; static="$3"; mc_host="$4"; mc_port="$5"; bind="$6"; port="$7"
venv="$src/backend/.venv"
secrets="$runtime/config/secrets"
data="$runtime/state/meshcore-webui"
# Restrictive umask for everything this launcher creates (key + state dirs).
umask 077
mkdir -p "$data/tiles"
mkdir -p "$secrets"; chmod 700 "$secrets" 2>/dev/null || true
vapid="$secrets/meshcore_webui_vapid.pem"
# Web Push needs a VAPID key; the backend fails closed without one. LHPC owns it (0600),
# generated once, never rotated on restart. It is NOT the MeshCore identity — purely a
# push-signing key local to the GUI. The python helper creates the file 0600 ATOMICALLY
# (O_CREAT|O_EXCL, mode 0o600) so there is no world-readable window, validates an existing
# key, and regenerates a missing/corrupt one; the backend also refuses a group/other-readable
# key, so a bad mode fails closed rather than being trusted.
"$venv/bin/python" - "$vapid" <<'PY'
import os, sys
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import serialization

path = sys.argv[1]

def _valid(p):
    try:
        with open(p, "rb") as fh:
            serialization.load_pem_private_key(fh.read(), password=None)
        return True
    except Exception:
        return False

if os.path.exists(path):
    os.chmod(path, 0o600)            # re-assert perms on a pre-existing key
    if _valid(path):
        sys.exit(0)
    os.unlink(path)                  # corrupt/truncated -> regenerate

k = ec.generate_private_key(ec.SECP256R1())
pem = k.private_bytes(serialization.Encoding.PEM,
                      serialization.PrivateFormat.PKCS8,
                      serialization.NoEncryption())
fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
try:
    os.write(fd, pem)
finally:
    os.close(fd)
PY
export MESHCORE_HOST="$mc_host" MESHCORE_PORT="$mc_port"
export STATIC_DIR="$static"
export DATABASE_URL="sqlite+aiosqlite:///$data/meshcore-webui.db"
export VAPID_PRIVATE_KEY_PATH="$vapid"
export MESHCORE_WEBUI_TILE_CACHE_DIR="$data/tiles"
# No external elevation calls by default on a headless node; keep the SSRF-guarded default
# but the operator can override. Bind LOOPBACK — the LHPC proxy is the only public path.
cd "$src/backend"
exec "$venv/bin/python" -m uvicorn app.main:app --host "$bind" --port "$port"
