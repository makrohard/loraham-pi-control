#!/usr/bin/env bash
#
# meshtastic-web-assets.sh <source_dir> <dest_dir> [pin_commit] [pinned_sha256]
#
# Provision the Meshtastic browser UI that belongs to THIS firmware checkout.
#
# The required web release is declared BY THE SOURCE (`bin/web.version`), so the assets always
# follow the selected revision — pinned, stable or dev. Assets from a different revision are
# never reused: a stale UI against a newer API is exactly the kind of silent mismatch that
# looks like a working install until the browser talks to the node.
#
# WHICH VERIFICATION APPLIES IS DECIDED BY THE CHECKOUT, NOT BY THE CALLER. The declared hash
# describes ONE revision, so it may only be enforced when the checkout actually IS that revision:
#
#   HEAD == pin_commit   -> PINNED: the download MUST match pinned_sha256, or this fails.
#   anything else        -> explicit dev/stable: use that checkout's bin/web.version, validate and
#                           extract the corresponding asset, RECORD its observed hash and mark the
#                           provenance unpinned. Asserting the pinned digest here would fail every
#                           dev build; claiming reproducibility we do not have would be worse.
#
# Offline: set LHPC_MESHTASTIC_WEB_TARBALL to a local build.tar (same verification applies).
set -euo pipefail

SRC="${1:?usage: meshtastic-web-assets.sh <source_dir> <dest_dir> [pin_commit] [pinned_sha256]}"
DEST="${2:?missing <dest_dir>}"
PIN_COMMIT="${3:-}"
PINNED_SHA="${4:-}"

# Resolve the checkout's HEAD FIRST: it decides the verification mode below.
REV="$(git -C "$SRC" rev-parse HEAD 2>/dev/null || echo unknown)"
if [ -n "$PIN_COMMIT" ] && [ -n "$PINNED_SHA" ] && [ "$REV" = "$PIN_COMMIT" ]; then
	EXPECTED="$PINNED_SHA"
	echo "[meshtastic] checkout is the pinned revision ${REV:0:12} — enforcing the declared web asset hash"
else
	EXPECTED=""
	echo "[meshtastic] checkout is NOT the pinned revision (HEAD ${REV:0:12}) — dev/stable: recording the asset hash, not asserting one"
fi

VERSION_FILE="$SRC/bin/web.version"
if [ ! -f "$VERSION_FILE" ]; then
	echo "ERROR: $VERSION_FILE not found — cannot determine the web release for this checkout." >&2
	exit 2
fi
VERSION="$(tr -d '[:space:]' < "$VERSION_FILE")"
if ! printf '%s' "$VERSION" | grep -qE '^[0-9]+\.[0-9]+\.[0-9]+$'; then
	echo "ERROR: unexpected web version $VERSION in $VERSION_FILE" >&2
	exit 2
fi

WORK="$(mktemp -d "${TMPDIR:-/tmp}/lhpc-mtweb.XXXXXX")"
cleanup() { rm -rf "$WORK"; }
trap cleanup EXIT

TAR="$WORK/build.tar"
if [ -n "${LHPC_MESHTASTIC_WEB_TARBALL:-}" ]; then
	echo "[meshtastic] web assets $VERSION from LHPC_MESHTASTIC_WEB_TARBALL"
	cp -- "$LHPC_MESHTASTIC_WEB_TARBALL" "$TAR"
else
	URL="https://github.com/meshtastic/web/releases/download/v${VERSION}/build.tar"
	echo "[meshtastic] fetching web assets $VERSION — $URL"
	curl -fsSL --retry 3 --retry-delay 2 -o "$TAR" "$URL"
fi

ACTUAL="$(sha256sum "$TAR" | cut -d' ' -f1)"
if [ -n "$EXPECTED" ]; then
	if [ "$ACTUAL" != "$EXPECTED" ]; then
		echo "ERROR: web asset checksum mismatch for v$VERSION" >&2
		echo "       expected $EXPECTED" >&2
		echo "       actual   $ACTUAL" >&2
		exit 3
	fi
	echo "[meshtastic] web assets v$VERSION sha256 VERIFIED against the declared pin"
else
	echo "[meshtastic] web assets v$VERSION sha256 $ACTUAL (recorded; not a pinned build)"
fi

# Unpack, then gunzip in place: the release ships its files gzipped (same handling as upstream's
# own packaging). Built in a temp dir and swapped in, so an interrupted run never leaves a
# half-extracted UI behind a completed build marker.
STAGE="$WORK/web"
mkdir -p "$STAGE"
tar -xf "$TAR" -C "$STAGE"
gunzip -r "$STAGE" 2>/dev/null || true
if [ -z "$(ls -A "$STAGE")" ]; then
	echo "ERROR: web asset archive extracted to nothing" >&2
	exit 3
fi

mkdir -p "$(dirname "$DEST")"
rm -rf "$DEST.new"
mv "$STAGE" "$DEST.new"
rm -rf "$DEST.old"
[ -e "$DEST" ] && mv "$DEST" "$DEST.old"
mv "$DEST.new" "$DEST"
rm -rf "$DEST.old"

# Provenance next to the assets: which firmware revision asked for which web release, and the
# digest actually installed. This is what makes a later mismatch diagnosable instead of folklore.
cat > "$(dirname "$DEST")/web.provenance" <<EOF
firmware_rev=$REV
web_version=$VERSION
web_sha256=$ACTUAL
pinned=$([ -n "$EXPECTED" ] && echo yes || echo no)
EOF

echo "[meshtastic] web UI v$VERSION installed at $DEST"
