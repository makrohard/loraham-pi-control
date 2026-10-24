#!/usr/bin/env bash
# Runs IN the guest as its user: the documented install at FROM (README "Manual install" steps 4 and 5).
#   guest-install.sh <FROM tag>
# Step 4 as written (the curl fetches main, as the README says). Step 5: the README's manual block, with
# the first stated test-only deviation of the plan (RUN QEMU-B): `git reset --hard FROM` right after the clone,
# so the rest of the block is read from the README AT FROM and run as written there. Prints t_<step>=<s>.
set -euo pipefail
FROM=$1
SRC=$HOME/loraham-pi-control/src/loraham-pi-control
t() { local name=$1 s; shift; s=$(date +%s); "$@"; echo "t_$name=$(( $(date +%s) - s ))"; }

deps() {
  curl -fsSL https://raw.githubusercontent.com/makrohard/loraham-pi-control/main/bootstrap-deps.sh -o bootstrap-deps.sh
  bash bootstrap-deps.sh --dry-run
  sudo bash bootstrap-deps.sh --spi-mode soft-cs < /dev/null
}

lhpc_install() {
  mkdir -p "$HOME/loraham-pi-control/src"
  git clone https://github.com/makrohard/loraham-pi-control.git "$SRC"
  git -C "$SRC" reset --hard "$FROM"
  # The rest of the README block at FROM, after its clone line, up to the closing fence.
  awk '/Manual — clone \/ venv \/ bootstrap/{f=1} f&&/^```bash$/{b=1;next} b&&/^```$/{exit} b{print}' "$SRC/README.md" \
    | sed -n '/^git clone /,$p' | tail -n +2 > "$HOME/manual-block.sh"
  echo "--- the README block at $FROM after the clone (run as written):"; cat "$HOME/manual-block.sh"
  [ -s "$HOME/manual-block.sh" ]
  bash -euo pipefail "$HOME/manual-block.sh" < /dev/null
  # The plan's second stated deviation: the manual block at a released tag never creates the CLI link, which
  # only install.sh makes (install.sh:247-248), so `lhpc` is not on a login PATH after the reboot (a docs defect
  # in that block, its own item). The lane adds install.sh's own link line, as that script runs it.
  mkdir -p ~/.local/bin && ln -sfn ~/loraham-pi-control/venv/lhpc/bin/lhpc ~/.local/bin/lhpc
}

t deps deps
t install lhpc_install
echo "HEAD=$(git -C "$SRC" rev-parse HEAD) branch=$(git -C "$SRC" rev-parse --abbrev-ref HEAD)"
