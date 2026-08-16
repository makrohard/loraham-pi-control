#!/usr/bin/env bash
# LHPC test-lab provisioning, split into two verbs so the PREBUILD path and the COLD path
# share exactly one implementation:
#   provision.sh setup      -> editable installs + Playwright + lab init + reset (idempotent)
#   provision.sh populate   -> install + build every stack, RETRYING until the completion
#                              marker lands, so a transient build failure self-heals
# onCreate (prebuild-cached) runs BOTH end to end. start.sh runs `setup` only when a cold
# Codespace never ran onCreate, then backgrounds `populate` so the web console is never
# blocked on multi-minute stack builds.
#
# NOTE: intentionally NOT `set -e`. Provisioning is best-effort and self-healing; a transient
# failure (network blip, one stack's build) must not abort the caller — start.sh still has to
# serve the console, and populate retries on the next pass.
set -uo pipefail
cd "$(dirname "$0")/.."
ROOT="$HOME/loraham-pi-control"

setup() {
    pip install -e .            # the product
    pip install -e ./testlab    # the SEPARATE lab package (not shipped in the product)
    pip install pytest ruff playwright
    # distlib/setuptools/wheel: meshcom's qemu build (mkvenv) bootstraps its own venv seeded
    # from this Python and needs distlib present, or it aborts "found no usable distlib".
    pip install distlib setuptools wheel
    python -m playwright install chromium || echo "playwright browsers unavailable (offline?)"
    lhpc-testlab init
    lhpc-testlab reset
}

populate() {
    # _populate marks a stack READY only when installed AND (binary or source-built), and
    # writes the `populated` marker ONLY when every present stack is ready. Loop until the
    # marker lands, bounded so a single pass can't spin: a stack still failing after these
    # tries is deliberately left WITHOUT the marker, so the next boot's start.sh re-runs
    # populate and it self-heals once its cause is fixed (cross-boot recovery).
    local marker="$ROOT/state/testlab/populated" i=0
    while [ ! -e "$marker" ] && [ "$i" -lt 3 ]; do
        i=$((i + 1))
        lhpc-testlab _populate || true
    done
}

case "${1:-}" in
    # setup is the PREREQUISITE (installs + baseline); a failure must be loud so onCreate
    # fails the prebuild instead of baking a half-provisioned image. populate is best-effort.
    setup) set -e; setup ;;
    populate) populate ;;
    *) echo "usage: provision.sh {setup|populate}" >&2; exit 2 ;;
esac
