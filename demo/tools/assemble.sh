#!/usr/bin/env bash
# Assemble the deployable demo bundle into demo/web/{wheels,static} + wheels.json.
# Rebuilds BOTH wheels fresh (no staleness) and copies lhpc's browser static assets.
# The Pages workflow runs this before deploying; it is also the local test bundler.
set -euo pipefail
HERE="$(cd "$(dirname "$0")/.." && pwd)"          # demo/
REPO="$(cd "$HERE/.." && pwd)"                     # repo root (lhpc source)
OUT="$HERE/web"
PYBIN="${PYTHON:-python3}"
rm -rf "$OUT/wheels" "$OUT/static"
mkdir -p "$OUT/wheels" "$OUT/static"
"$PYBIN" -m build --wheel -o "$OUT/wheels" "$REPO"   >/dev/null   # lhpc (the product)
"$PYBIN" -m build --wheel -o "$OUT/wheels" "$HERE"   >/dev/null   # lhpc_demo (this)
cp "$REPO"/lhpc/adapters/web/static/* "$OUT/static/"
# manifest so boot.js never hardcodes a version
( cd "$OUT/wheels" && printf '[' && \
  ls *.whl | sed 's/.*/"wheels\/&"/' | paste -sd, - && printf ']\n' ) > "$OUT/wheels.json"
echo "assembled: $(ls "$OUT/wheels" | tr '\n' ' ')"
echo "manifest: $(cat "$OUT/wheels.json")"
