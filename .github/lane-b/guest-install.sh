#!/usr/bin/env bash
# Runs IN the guest as its user: the documented install of one LHPC commit (README "Manual install" steps 4 and 5).
#   guest-install.sh <commit sha, 40 hex>
# Step 4: the README's curl, the sha in place of main, so bootstrap-deps.sh is the commit's own. Step 5: the README's
# manual block, with the stated test-only deviation (RUN QEMU-B): `git reset --hard FROM` right after the clone, so
# the rest of the block is read from the README AT FROM and run as written there; the lane adds nothing to it (a
# block that does not put `lhpc` on the login PATH fails the job's PATH check). The order is the README's (step 4
# installs git, which the clone in step 5 needs). Prints t_<step>=<s>.
set -euo pipefail
FROM=$1
[[ $FROM =~ ^[0-9a-f]{40}$ ]] || { echo "usage: guest-install.sh <commit sha, 40 hex>" >&2; exit 2; }
SRC=$HOME/loraham-pi-control/src/loraham-pi-control
t() { local name=$1 s; shift; s=$(date +%s); "$@"; echo "t_$name=$(( $(date +%s) - s ))"; }

deps() {
  curl -fsSL "https://raw.githubusercontent.com/makrohard/loraham-pi-control/$FROM/bootstrap-deps.sh" -o bootstrap-deps.sh
  bash bootstrap-deps.sh --dry-run
  sudo bash bootstrap-deps.sh --spi-mode soft-cs < /dev/null
}

clone() {
  mkdir -p "$HOME/loraham-pi-control/src"
  git clone https://github.com/makrohard/loraham-pi-control.git "$SRC"
  git -C "$SRC" reset --hard "$FROM"
}

lhpc_install() {
  [ -d "$SRC/.git" ] || clone
  # The rest of the README block at FROM, after its clone line, up to the closing fence.
  awk '/Manual — clone \/ venv \/ bootstrap/{f=1} f&&/^```bash$/{b=1;next} b&&/^```$/{exit} b{print}' "$SRC/README.md" \
    | sed -n '/^git clone /,$p' | tail -n +2 > "$HOME/manual-block.sh"
  echo "--- the README block at $FROM after the clone (run as written):"; cat "$HOME/manual-block.sh"
  [ -s "$HOME/manual-block.sh" ]
  bash -euo pipefail "$HOME/manual-block.sh" < /dev/null
}

t deps deps
t install lhpc_install
echo "HEAD=$(git -C "$SRC" rev-parse HEAD) branch=$(git -C "$SRC" rev-parse --abbrev-ref HEAD)"
