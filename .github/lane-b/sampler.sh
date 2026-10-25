#!/usr/bin/env bash
# Runs IN the guest as root while the install runs (the observation only): every 15 s the three systemctl calls
# a unit-presence check can use, each with its rc, its duration in ms and its stderr, and the load average:
# `full` = the unfiltered `list-unit-files --no-legend` (bootstrap-deps' queryability probe today),
# `unit` = the filtered listing of one unit, `light` = `show -p Version --value` (a single property),
# `ls-unit`/`ls-none` = `show -p LoadState --value` of nginx.service and of a unit that does not exist (each loaded
# on demand); `ls-gpsd` the same for gpsd.service, installed by bootstrap and not yet referenced, with its VALUE
# (not-found before the package, then loaded). One line per call.
probe() {
  local name=$1 s e r ms; shift
  s=$(date +%s%N); e=$("$@" 2>&1 > /dev/null); r=$?; ms=$(( ($(date +%s%N) - s) / 1000000 ))
  printf '%s %s rc=%s ms=%s load=%s err=%s\n' "$(date -u +%FT%TZ)" "$name" "$r" "$ms" "$(cut -d' ' -f1-3 /proc/loadavg)" "${e//$'\n'/ | }"
}
while :; do
  probe full systemctl list-unit-files --no-legend
  probe unit systemctl list-unit-files --no-legend nginx.service
  probe light systemctl show -p Version --value
  probe ls-unit systemctl show -p LoadState --value nginx.service
  probe ls-none systemctl show -p LoadState --value lhpc-lane-absent.service
  s=$(date +%s%N); v=$(systemctl show -p LoadState --value gpsd.service 2>&1); r=$?; ms=$(( ($(date +%s%N) - s) / 1000000 ))
  printf '%s ls-gpsd rc=%s ms=%s load=%s val=%s\n' "$(date -u +%FT%TZ)" "$r" "$ms" "$(cut -d' ' -f1-3 /proc/loadavg)" "${v//$'\n'/ | }"
  sleep 15
done
