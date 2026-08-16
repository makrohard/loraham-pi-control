#!/usr/bin/env bash
# LHPC test-lab onCreateCommand — the ONE-TIME heavy provisioning that a Codespaces PREBUILD
# caches. IMPORTANT: prebuilds run onCreate/updateContent, NOT postCreate/postStart, so all
# the slow work must live here to boot fast from a prebuild. Runs the shared provisioner end
# to end: setup (editable installs + lab baseline) then populate (install + build every
# stack). Codespaces are x86-only, so meshcom/meshtastic BUILD FROM SOURCE here (there is no
# x86 prebuilt binary); on an ARM host they'd use our binaries. start.sh re-runs the same
# `populate` in the background so a stack that failed here still self-heals on next boot.
# set -e so a SETUP failure fails the prebuild LOUD (never bake a half-provisioned image
# that then reports success). populate is best-effort: a stack that fails to build here is
# left without the `populated` marker, and start.sh re-runs populate on boot until it heals.
set -euo pipefail
cd "$(dirname "$0")/.."
DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$HOME/loraham-pi-control"
bash "$DIR/provision.sh" setup
bash "$DIR/provision.sh" populate || true       # retries within itself; may still fall short
# A prebuild must NOT be accepted incomplete: populate writes the `populated` marker only when
# EVERY stack is ready, so its absence means a stack failed to build. Fail loud so the prebuild
# is rejected rather than baking an image that boots missing stacks yet reports success. (A cold
# Codespace never runs onCreate, so this gate is the prebuild's alone; start.sh self-heals cold.)
if [ ! -e "$ROOT/state/testlab/populated" ]; then
    echo "onCreate: populate did not complete — a stack failed to build. Refusing to bake an incomplete prebuild." >&2
    exit 1
fi
echo "onCreate: fully provisioned — every stack installed & built."
