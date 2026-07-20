#!/usr/bin/env bash
#
# meshtastic-link-gate.sh <binary>
#
# FAIL-CLOSED link-time gate for the managed server-only meshtasticd.
#
# The whole point of building `-e native` instead of installing the OBS package is that the
# result must not touch a display or audio stack. That is an ASSERTION ABOUT THE PRODUCED
# BINARY, so it is checked on the binary itself — not inferred from build flags, which can
# silently change upstream. A build whose output links any forbidden library must never be
# published, so this runs as a build step and a nonzero exit stops the marker being written.
#
# Checks BOTH the direct NEEDED entries (readelf -d) and the full transitive closure (ldd):
# a forbidden library pulled in indirectly is exactly as fatal as a direct one.
set -euo pipefail

BIN="${1:?usage: meshtastic-link-gate.sh <binary>}"

if [ ! -f "$BIN" ]; then
	echo "ERROR: link gate: no such binary: $BIN" >&2
	exit 2
fi

# SDL / X11 / Wayland / Mesa / LLVM / PulseAudio / libinput / xkbcommon — none may appear.
FORBIDDEN='libSDL|libX11|libXext|libXcursor|libXi|libXrandr|libXfixes|libXss|libwayland|libgbm|libdrm|libEGL|libGL|libLLVM|libpulse|libasound|libinput|libxkbcommon|libgtk'

fail=0
report() {  # $1 = source label, $2 = matches
	if [ -n "$2" ]; then
		echo "ERROR: link gate: $BIN links forbidden libraries ($1):" >&2
		printf '  %s\n' $2 >&2
		fail=1
	fi
}

direct="$(readelf -d "$BIN" 2>/dev/null | sed -n 's/.*Shared library: \[\(.*\)\].*/\1/p' \
	| grep -E "$FORBIDDEN" | sort -u | tr '\n' ' ' || true)"
report "direct NEEDED" "$direct"

# ldd resolves the transitive closure. If it is unavailable we do NOT pass silently — the
# gate's job is to prove absence, and an unproven closure is a failure, not a pass.
if command -v ldd >/dev/null 2>&1; then
	trans="$(ldd "$BIN" 2>/dev/null | awk '{print $1}' \
		| grep -E "$FORBIDDEN" | sort -u | tr '\n' ' ' || true)"
	report "transitive closure" "$trans"
else
	echo "ERROR: link gate: ldd unavailable — cannot prove the transitive closure is clean." >&2
	fail=1
fi

if [ "$fail" -ne 0 ]; then
	echo "ERROR: refusing to publish a meshtasticd built against a display/audio stack." >&2
	echo "       Expected upstream PlatformIO env 'native' (never 'native-tft')." >&2
	# Most likely cause, and it is a HOST condition rather than an upstream one: env:native ends with
	# an OPTIONAL `pkg-config --cflags --libs sdl2 --silence-errors || :`, so SDL links itself in
	# whenever SDL2 development files happen to be installed. lhpc never declares libsdl2-dev; if it
	# is present it came from something else on this machine.
	if command -v pkg-config >/dev/null 2>&1 && pkg-config --exists sdl2 2>/dev/null; then
		echo "       CAUSE: SDL2 development files are installed on this machine, and upstream's" >&2
		echo "       env:native links SDL opportunistically when pkg-config finds them. Remove" >&2
		echo "       libsdl2-dev (lhpc does not declare it) and rebuild." >&2
	fi
	exit 3
fi

echo "[meshtastic] link gate OK — no SDL/X11/Wayland/Mesa/LLVM/PulseAudio/libinput/xkbcommon in $BIN"
