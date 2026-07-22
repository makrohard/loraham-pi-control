#!/usr/bin/env bash
#
# meshtastic-link-gate.sh <binary> [label]
#
# FAIL-CLOSED link-time gate for a binary that must NOT touch a display or audio stack. Originally the
# managed server-only meshtasticd (`-e native`); now also the headless source-built qemu-system-xtensa
# (build-qemu.sh passes a label). Pass a label to describe the artifact; without one the message keeps
# the meshtasticd-specific remediation.
#
# The whole point is that the result must not link a display/audio library — an ASSERTION ABOUT THE
# PRODUCED BINARY, checked on the binary itself, not inferred from build flags (which can silently
# change upstream). A build whose output links any forbidden library must never be published, so this
# runs as a build step and a nonzero exit stops the completion marker being written.
#
# Checks BOTH the direct NEEDED entries (readelf -d) and the full transitive closure (ldd): a forbidden
# library pulled in indirectly is exactly as fatal as a direct one. Output is CAPTURED first and
# inspected after — never `producer | grep -q` under `pipefail`, whose failure would be silently eaten.
# The gate fails CLOSED: if readelf cannot inspect the binary, if ldd errors, if any dependency is
# `=> not found`, or if the object is unexpectedly not a dynamic executable, that is a FAILURE (an
# unproven closure is not a pass), evaluated BEFORE the forbidden-set match.
set -euo pipefail

BIN="${1:?usage: meshtastic-link-gate.sh <binary> [label]}"
LABEL="${2:-}"

if [ ! -f "$BIN" ]; then
	echo "ERROR: link gate: no such binary: $BIN" >&2
	exit 2
fi

# SDL / X11 / Wayland / Mesa / LLVM / PulseAudio / ALSA / libinput / xkbcommon / GTK — none may appear.
FORBIDDEN='libSDL|libX11|libXext|libXcursor|libXi|libXrandr|libXfixes|libXss|libwayland|libgbm|libdrm|libEGL|libGL|libLLVM|libpulse|libasound|libinput|libxkbcommon|libgtk'

fail=0

# ---- direct NEEDED entries (readelf -d) -------------------------------------------------------------
# Capture readelf's raw output AND exit status first; a readelf that cannot inspect the file is a
# failure, not a pass (fail-closed). `|| rc=$?` keeps the captured status without tripping `set -e`.
readelf_rc=0
readelf_out="$(readelf -d "$BIN" 2>&1)" || readelf_rc=$?
if [ "$readelf_rc" -ne 0 ]; then
	echo "ERROR: link gate: readelf could not inspect $BIN (rc=$readelf_rc):" >&2
	printf '%s\n' "$readelf_out" | sed 's/^/  /' >&2
	exit 2
fi
direct="$(printf '%s\n' "$readelf_out" | sed -n 's/.*Shared library: \[\(.*\)\].*/\1/p' \
	| grep -E "$FORBIDDEN" | sort -u | tr '\n' ' ' || true)"

# ---- transitive closure (ldd) -----------------------------------------------------------------------
# ldd resolves the full closure. It must be available AND succeed; any `=> not found` is a fail-closed
# failure (a headless box legitimately lacking a forbidden lib would otherwise slip through as absent).
if ! command -v ldd >/dev/null 2>&1; then
	echo "ERROR: link gate: ldd unavailable — cannot prove the transitive closure is clean." >&2
	exit 2
fi
ldd_rc=0
ldd_out="$(ldd "$BIN" 2>&1)" || ldd_rc=$?
if printf '%s\n' "$ldd_out" | grep -q 'not a dynamic executable'; then
	echo "ERROR: link gate: $BIN is not a dynamic executable — unexpected, refusing." >&2
	exit 2
fi
if [ "$ldd_rc" -ne 0 ]; then
	echo "ERROR: link gate: ldd failed on $BIN (rc=$ldd_rc):" >&2
	printf '%s\n' "$ldd_out" | sed 's/^/  /' >&2
	exit 2
fi
notfound="$(printf '%s\n' "$ldd_out" | grep -F '=> not found' | sort -u | tr '\n' ' ' || true)"
if [ -n "$notfound" ]; then
	echo "ERROR: link gate: $BIN has unresolved shared libraries (fail-closed):" >&2
	printf '  %s\n' $notfound >&2
	exit 2
fi
trans="$(printf '%s\n' "$ldd_out" | awk '{print $1}' | grep -E "$FORBIDDEN" | sort -u | tr '\n' ' ' || true)"

# ---- forbidden-set verdict --------------------------------------------------------------------------
report() {  # $1 = source label, $2 = matches
	if [ -n "$2" ]; then
		echo "ERROR: link gate: $BIN links forbidden libraries ($1):" >&2
		printf '  %s\n' $2 >&2
		fail=1
	fi
}
report "direct NEEDED" "$direct"
report "transitive closure" "$trans"

if [ "$fail" -ne 0 ]; then
	if [ -n "$LABEL" ]; then
		echo "ERROR: refusing to publish $LABEL built against a display/audio stack." >&2
	else
		echo "ERROR: refusing to publish a meshtasticd built against a display/audio stack." >&2
		echo "       Expected upstream PlatformIO env 'native' (never 'native-tft')." >&2
		# Most likely cause, a HOST condition: env:native ends with an OPTIONAL
		# `pkg-config --cflags --libs sdl2 --silence-errors || :`, so SDL links itself in whenever SDL2
		# development files happen to be installed. lhpc never declares libsdl2-dev; if it is present it
		# came from something else on this machine.
		if command -v pkg-config >/dev/null 2>&1 && pkg-config --exists sdl2 2>/dev/null; then
			echo "       CAUSE: SDL2 development files are installed on this machine, and upstream's" >&2
			echo "       env:native links SDL opportunistically when pkg-config finds them. Remove" >&2
			echo "       libsdl2-dev (lhpc does not declare it) and rebuild." >&2
		fi
	fi
	exit 3
fi

echo "[link-gate] link gate OK — no SDL/X11/Wayland/Mesa/LLVM/PulseAudio/ALSA/libinput/xkbcommon/GTK in ${LABEL:-$BIN}"
