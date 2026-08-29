#!/bin/bash
# Apply an LHPC-shipped patch to the openhop_core source checkout, idempotently.
# $1 = source dir (the pinned openhop_core checkout), $2 = patch file (asset).
# A refreshed checkout is pristine (patch applies); a rebuild without refresh
# already carries it (reverse-check succeeds -> nothing to do). Anything else
# is a real conflict and must fail the build rather than run unpatched code.
set -eu
src="$1"; patch="$2"
cd "$src"
if git apply --reverse --check "$patch" 2>/dev/null; then
    echo "[patch] already applied: $(basename "$patch")"
    exit 0
fi
git apply "$patch"
echo "[patch] applied: $(basename "$patch")"
