#!/usr/bin/env bash
#
# meshchat-assets.sh <dist_dir> <dest_dir>
#
# Put MeshChat's prebuilt browser UI where MeshChat looks for it.
#
# Upstream gitignores `/public/` and builds it with vite, so the pinned checkout carries a backend
# and no UI. LHPC ships the built bundle as package data instead (the meshcore-webui pattern) —
# npm stays off the box. `get_file_path()` resolves against the script's own directory and takes
# no override, so the bundle has to land at <checkout>/public rather than be pointed at.
#
# Staged and swapped, not copied in place: `build_marker` is written after the LAST build step, so
# a run interrupted mid-copy would otherwise leave a half-populated UI behind a completed build.
# Copying into an existing directory would also accumulate assets from an older bundle, because
# vite content-hashes its filenames.
set -euo pipefail

DIST="${1:?usage: meshchat-assets.sh <dist_dir> <dest_dir>}"
DEST="${2:?missing <dest_dir>}"

[ -d "$DIST" ] || { echo "ERROR: bundle not found at $DIST" >&2; exit 2; }
[ -f "$DIST/index.html" ] || { echo "ERROR: $DIST has no index.html — not a built bundle" >&2; exit 2; }

rm -rf "$DEST.new" "$DEST.old"
cp -r "$DIST" "$DEST.new"
if [ -e "$DEST" ]; then mv "$DEST" "$DEST.old"; fi
mv "$DEST.new" "$DEST"
rm -rf "$DEST.old"

echo "[meshchat] browser UI installed at $DEST ($(find "$DEST" -type f | wc -l) files)"
