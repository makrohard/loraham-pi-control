#!/usr/bin/env bash
# The from-topic job (lane-b.yml, a stated lane deviation): a fresh Pi OS guest booted the documented way, then the
# documented install of one LHPC commit (its own bootstrap-deps.sh and its README's manual block), the reboot and
# step 8's units line; lhpc-web and lhpc-nginx must then both be active.
#   topic.sh <commit sha, 40 hex>
# Needs WORK (the verified images, key, key.pub) and EV. Measurements go to $EV/measurements.txt.
set -euo pipefail
FROM=$1
[[ $FROM =~ ^[0-9a-f]{40}$ ]] || { echo "usage: topic.sh <commit sha, 40 hex>" >&2; exit 2; }
LB=$GITHUB_WORKSPACE/.github/lane-b
. "$LB/guest.sh"
BOUND=1200
m() { echo "$1=$2" | tee -a "$EV/measurements.txt"; }
fail() { echo "FAIL: $*" >&2; exit 1; }

prepare
boot first
s=$(wait_ssh "$BOUND") || fail "no ssh within $BOUND s (the image's cloud-init user-data)"
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
t0=$(date +%s); urc=0; g "bash -lc $(printf %q "$line")" > "$EV/units.txt" 2>&1 || urc=$?
m units_line_rc "$urc"; m units_line_s "$(( $(date +%s) - t0 ))"
[ "$urc" -eq 0 ] || { observe_nginx; fail "the units line (see units.txt, guest/nginx-*.txt)"; }
g 'stat -c "%Y %y %n" ~/.config/systemd/user/lhpc-*; systemctl --user cat lhpc-web.service lhpc-nginx.service lhpc-boot-restore.service; true' > "$EV/units-installed.txt" 2>&1
g 'systemctl --user is-active lhpc-web.service lhpc-nginx.service; true' > "$EV/units-active.txt" 2>&1
python3 "$GITHUB_WORKSPACE/.github/scripts/lane_b.py" active 2 "$EV/units-active.txt" \
  || fail "lhpc-web and lhpc-nginx are not both active after the units line (see units-active.txt)"
echo "ROW TOPIC: PASS"
