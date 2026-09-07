#!/usr/bin/env bash
#
# meshtastic-web-assets.sh <dest_dir> <web_version> <sha256>
#
# Provision the Meshtastic browser UI that LHPC pins: one meshtastic/web release, named by version
# in the manifest together with the sha256 of its `build.tar`, and verified on EVERY install.
#
# The web client is LHPC's own pin, independent of the firmware checkout's `bin/web.version`:
# that file is upstream's last-known-good for the ESP32 embedded web server (size-bound, moved by a
# bot, with a history of downgrades) and has stayed at 2.6.7 across the 2.7.x and 2.8.0 firmware
# lines while meshtastic/web kept releasing. The Linux-native meshtasticd serves the client from a
# directory, so LHPC ships the newest release and moves it deliberately (docs/maintenance.md,
# "Moving a pin"), testing the pairing on the reference box.
#
# Offline: set LHPC_MESHTASTIC_WEB_TARBALL to a local build.tar (the same verification applies).
set -euo pipefail

DEST="${1:?usage: meshtastic-web-assets.sh <dest_dir> <web_version> <sha256>}"
VERSION="${2:?missing <web_version>}"
EXPECTED="${3:?missing <sha256>}"

if ! printf '%s' "$VERSION" | grep -qE '^[0-9]+\.[0-9]+\.[0-9]+$'; then
	echo "ERROR: unexpected web version '$VERSION' (want MAJOR.MINOR.PATCH)" >&2
	exit 2
fi
if ! printf '%s' "$EXPECTED" | grep -qE '^[0-9a-f]{64}$'; then
	echo "ERROR: the web asset pin must be a 64-hex sha256" >&2
	exit 2
fi

WORK="$(mktemp -d "${TMPDIR:-/tmp}/lhpc-mtweb.XXXXXX")"
cleanup() { rm -rf "$WORK"; }
trap cleanup EXIT

TAR="$WORK/build.tar"
if [ -n "${LHPC_MESHTASTIC_WEB_TARBALL:-}" ]; then
	echo "[meshtastic] web client v$VERSION from LHPC_MESHTASTIC_WEB_TARBALL"
	cp -- "$LHPC_MESHTASTIC_WEB_TARBALL" "$TAR"
else
	URL="https://github.com/meshtastic/web/releases/download/v${VERSION}/build.tar"
	echo "[meshtastic] fetching web client v$VERSION — $URL"
	curl -fsSL --retry 3 --retry-delay 2 -o "$TAR" "$URL"
fi

ACTUAL="$(sha256sum "$TAR" | cut -d' ' -f1)"
if [ "$ACTUAL" != "$EXPECTED" ]; then
	echo "ERROR: web client checksum mismatch for v$VERSION" >&2
	echo "       expected $EXPECTED" >&2
	echo "       actual   $ACTUAL" >&2
	exit 3
fi
echo "[meshtastic] web client v$VERSION sha256 VERIFIED against the manifest pin"

# Unpack, then gunzip in place: the release ships its files gzipped (same handling as upstream's
# own packaging). Built in a temp dir and swapped in, so an interrupted run never leaves a
# half-extracted UI behind a completed build marker.
STAGE="$WORK/web"
mkdir -p "$STAGE"
tar -xf "$TAR" -C "$STAGE"
gunzip -r "$STAGE" 2>/dev/null || true
if [ -z "$(ls -A "$STAGE")" ]; then
	echo "ERROR: web client archive extracted to nothing" >&2
	exit 3
fi

mkdir -p "$(dirname "$DEST")"
rm -rf "$DEST.new"
mv "$STAGE" "$DEST.new"
rm -rf "$DEST.old"
[ -e "$DEST" ] && mv "$DEST" "$DEST.old"
mv "$DEST.new" "$DEST"
rm -rf "$DEST.old"

# Provenance next to the assets: which release is installed and the digest that was verified.
cat > "$(dirname "$DEST")/web.provenance" <<PROV
web_version=$VERSION
web_sha256=$ACTUAL
pinned=yes
source=lhpc-manifest
PROV

echo "[meshtastic] web client v$VERSION installed at $DEST"
