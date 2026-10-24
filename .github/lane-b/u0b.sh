#!/usr/bin/env bash
# One U0-B job (lane-b.yml): a fresh Pi OS guest, the documented install at FROM, then runsheet D-L1 v5's U0 block
# as written, which must take the branch this job exists for.
#   u0b.sh <units-stale|rc0> <FROM tag>
# Needs WORK (the verified images, key, key.pub) and EV. Measurements go to $EV/measurements.txt.
set -euo pipefail
WANT=$1 FROM=$2
LB=$GITHUB_WORKSPACE/.github/lane-b
. "$LB/guest.sh"
REL=9f18f26c68567e0571d4f16726ed4ae7a35232ec
BOUND=1200
m() { echo "$1=$2" | tee -a "$EV/measurements.txt"; }
fail() { echo "FAIL: $*" >&2; exit 1; }

# The target of the update is v0.13.3: upstream main must be it before anything is installed.
main=$(git ls-remote https://github.com/makrohard/loraham-pi-control refs/heads/main | cut -f1)
[ "$main" = "$REL" ] || { echo "STOP: upstream main is $main, not $REL" >&2; exit 2; }

prepare
boot first
if s=$(wait_ssh "$BOUND"); then m first_boot_path cloud-init
else
  halt || fail "the guest did not stop"
  m first_boot_path offline-fallback
  offline_user; boot fallback
  s=$(wait_ssh "$BOUND") || fail "no ssh within $BOUND s on either path"
fi
m boot_to_ssh_s "$s"
up=$(wait_up "$BOUND") || fail "systemd not running or degraded within $BOUND s"
m ssh_to_up_s_state "$up"

mkdir -p "$EV/guest"
g uname -m > "$EV/guest/machine"; g uname -r > "$EV/guest/kernel"; g cat /etc/os-release > "$EV/guest/os_release"
g 'test -f /etc/rpi-issue && echo yes || echo no' > "$EV/guest/rpi_issue"
python3 "$GITHUB_WORKSPACE/.github/scripts/lane_b.py" identity "$(uname -r)" "$EV/guest"

# README steps 4 and 5 at FROM (the stated reset), then step 6 (reboot), then step 8's units line for a manual install.
gcp "$LB/guest-install.sh" guest-install.sh
start_samplers
t0=$(date +%s); irc=0; g "bash guest-install.sh $FROM" > "$EV/install.txt" 2>&1 || irc=$?
g 'cat sampler.log 2>/dev/null; true' > "$EV/guest/systemctl-samples.txt" 2>&1 || true
collect_samples
[ "$irc" -eq 0 ] || fail "the install at $FROM, rc $irc (see install.txt; guest/systemctl-samples.txt, runner-load.txt)"
m install_s $(( $(date +%s) - t0 )); grep -E '^t_' "$EV/install.txt" | tee -a "$EV/measurements.txt"
b0=$(g cat /proc/sys/kernel/random/boot_id) && [ -n "$b0" ] || fail "the boot id before the reboot could not be read"
# The reboot's own rc says nothing (ssh drops as the guest goes down); a new boot id says it happened.
t0=$(date +%s); g 'sudo systemctl reboot' || true
b1=$(wait_reboot "$BOUND" "$b0") || fail "no new boot id within $BOUND s: the guest did not reboot"
wait_up "$BOUND" > /dev/null || fail "not up after the reboot"
m reboot_s $(( $(date +%s) - t0 )); m boot_id_before "$b0"; m boot_id_after "$b1"
line=$(g "grep -m1 '# installs the units' ~/loraham-pi-control/src/loraham-pi-control/README.md" | sed 's/ *#.*$//')
printf '%s\n' "$line" > "$EV/units-line.txt"
case "$line" in "lhpc self-update --repair-integration"*) ;; *) fail "the README at $FROM has no units line" ;; esac
g 'bash -lc "command -v lhpc"' > "$EV/command-v.txt" 2>&1 || fail "lhpc is not on the login PATH (see command-v.txt)"
g "bash -lc $(printf %q "$line")" > "$EV/units.txt" 2>&1 || fail "the units line (see units.txt)"
g 'stat -c "%Y %y %n" ~/.config/systemd/user/lhpc-*; systemctl --user cat lhpc-web.service lhpc-nginx.service lhpc-boot-restore.service; true' > "$EV/units-before.txt" 2>&1

# U0 as written: the runsheet's prelude and U0 block (their shas checked by the workflow); the transport Hx is the
# ssh into the guest, as v5's Hx is the ssh into box H.
export DSHA=$REL REPO=$GITHUB_WORKSPACE WS=$EV RT=/home/lhpc/loraham-pi-control
mkdir -p "$EV/dl1-lhpc-live"
t0=$(date +%s)
( set +e; . "$LB/u0-prelude.sh"; Hx() { "${G_SSH[@]}" "bash -lc $(printf %q "$1")"; }; . "$LB/u0-block.sh" ) > "$EV/u0-row.txt" 2>&1 || true
m u0_s $(( $(date +%s) - t0 ))
cat "$EV/u0-row.txt"
grep -qx 'ROW U0: PASS' "$EV/u0-row.txt" || fail "U0 did not pass (see u0-row.txt)"
python3 "$GITHUB_WORKSPACE/.github/scripts/lane_b.py" branch "$WANT" "$EV/dl1-lhpc-live"

g 'stat -c "%Y %y %n" ~/.config/systemd/user/lhpc-*; systemctl --user cat lhpc-web.service lhpc-nginx.service lhpc-boot-restore.service; true' > "$EV/units-after.txt" 2>&1
g "test -e ~/loraham-pi-control/state/selfupdate.incomplete && echo present || echo absent" > "$EV/selfupdate-incomplete.txt"
g free -m > "$EV/guest/free-after.txt"
