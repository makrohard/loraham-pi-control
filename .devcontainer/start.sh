#!/usr/bin/env bash
# postStart / postAttach: bring up the LHPC console FAST, then heal provisioning in the
# background. A Codespaces prebuild has already run onCreate (setup + populate baked in), so
# the guards below no-op and this just launches the server on :8770 (Codespaces forwards it
# and opens a browser). Without a prebuild — or after a partial provision — it completes the
# `setup` prerequisite synchronously (the web server needs the lab package), serves the
# console, then re-runs `populate` in the BACKGROUND until every stack is in — so first paint
# is never blocked on multi-minute stack builds.
#
# NOT `set -e`: a transient provisioning failure must never stop the console from coming up.
set -uo pipefail
cd "$(dirname "$0")/.."
DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$HOME/loraham-pi-control"
LOCK="$HOME/.lhpc-provision.lock"

pgrep -x Xvfb >/dev/null 2>&1 || (setsid Xvfb :99 -screen 0 1280x800x24 \
    >/dev/null 2>&1 < /dev/null &)

# Cold path — a Codespace created WITHOUT a prebuild never ran onCreate, so the lab package
# and baseline are missing. `setup` is a prerequisite for the web server, so it (only) runs
# synchronously here. flock serializes it against a concurrent postAttach launching start.sh,
# and the re-check inside the lock avoids a redundant second setup.
if [ ! -e "$ROOT/state/testlab/enabled" ]; then
    echo "no prebuild detected — running one-off setup (installs; slow)…"
    ( exec 9>"$LOCK"; flock 9
      [ -e "$ROOT/state/testlab/enabled" ] || bash "$DIR/provision.sh" setup ) || true
fi

# Serve the console FIRST — never block first paint on stack builds. The spawn is guarded by
# its OWN flock so postStart and postAttach can't both bind :8770. The lock is held only until
# the server is LISTENING (binds in ~1s), NOT until it is healthy — so a slow-to-healthy server
# neither makes the loser double-bind nor makes a concurrent attach wait 30s.
WEBLOCK="$HOME/.lhpc-web.lock"
LOG="$HOME/lhpc-web.log"
listening() { timeout 1 bash -c 'exec 3<>/dev/tcp/127.0.0.1/8770' 2>/dev/null; }
(
    exec 8>"$WEBLOCK"; flock 8
    if ! listening; then
        setsid bash -c 'exec lhpc-testlab web --port 8770' >>"$LOG" 2>&1 < /dev/null &
        # Hold the lock until the socket is BOUND (generous, so a cold import that binds
        # slowly never releases the lock un-bound and lets a waiter double-bind :8770).
        for _ in $(seq 1 120); do listening && break; sleep 0.25; done   # up to ~30s to bind
    fi
)
# Health-wait for the EXIT-CODE decision happens OUTSIDE the lock (a bound-but-slow server gets
# time to answer /healthz without any concurrent attach blocking on us).
console_up=0
for _ in $(seq 1 60); do
    curl -fsS --max-time 2 http://127.0.0.1:8770/healthz >/dev/null 2>&1 && { console_up=1; break; }
    sleep 0.5
done
if [ "$console_up" = 1 ]; then
    echo "LHPC console up on :8770 — the Ports tab forwards it (opens in your browser)."
else
    echo "console did not come up — see $LOG" >&2
fi

# Meshtastic web bridge (lab only): meshtasticd serves its UI on :9443 over HTTPS with a
# SELF-SIGNED cert, which a Codespace's forwarding proxy 502s on (it validates the upstream
# cert and has no -k). Bridge a PLAIN HTTP port :9080 to it with socat, re-wrapping to TLS
# with the cert unverified (a loopback lab service) — so the forwarded :9080 opens the UI
# directly. Detached + idempotent; harmless while meshtastic is down (connections just fail).
bridged() { timeout 1 bash -c 'exec 3<>/dev/tcp/127.0.0.1/9080' 2>/dev/null; }
if command -v socat >/dev/null 2>&1 && ! bridged; then
    setsid socat TCP-LISTEN:9080,fork,reuseaddr,bind=127.0.0.1 \
        OPENSSL:127.0.0.1:9443,verify=0 >/dev/null 2>&1 < /dev/null &
fi

# Heal populate in the BACKGROUND — stacks light up as each finishes, never blocking the
# console. No-op when the `populated` marker is present (prebuild, or already healed). Because
# populate writes that marker ONLY when every stack is ready, a box with a failed stack has no
# marker and re-populates. Gate on the CONTAINER-START id (PID 1's start time, stable across
# attaches, new per Codespace stop/start) so a genuinely-unbuildable stack retries once per
# start — self-healing across reboots — NOT on every VS Code attach. flock -n guards the tree.
RUNID="$(sed 's/^.*) //' /proc/1/stat 2>/dev/null | awk '{print $20}')"
RUNMARK="$ROOT/state/testlab/populate-run"
# Run populate when the box is not fully populated AND either we can't identify the container
# start (no RUNID -> fall back to the old "run whenever not done" behaviour, never worse) or
# this start hasn't attempted it yet. Record the id only when we have one.
if [ ! -e "$ROOT/state/testlab/populated" ] && \
   { [ -z "$RUNID" ] || [ "$(cat "$RUNMARK" 2>/dev/null || echo none)" != "$RUNID" ]; }; then
    [ -n "$RUNID" ] && echo "$RUNID" > "$RUNMARK" 2>/dev/null || true
    setsid bash -c '
        exec 9>"'"$LOCK"'"
        flock -n 9 || exit 0
        exec bash "'"$DIR"'/provision.sh" populate
    ' >>"$HOME/lhpc-populate.log" 2>&1 < /dev/null &
fi

# Surface a real failure to Codespaces instead of reporting success with no console (postStart
# swallowing the failure hid a down lab). populate still launched above, so a cold box that
# only needed builds keeps healing even on this exit path.
[ "$console_up" = 1 ] || exit 1
