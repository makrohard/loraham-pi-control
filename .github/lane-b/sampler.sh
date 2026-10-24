#!/usr/bin/env bash
# Runs IN the guest as root while the install runs (the observation only): every 15 s the call bootstrap-deps'
# unit_present relies on, `systemctl list-unit-files --no-legend`, with its rc, its duration in ms, its stderr
# and the load average. One line per sample on stdout.
while :; do
  s=$(date +%s%N)
  e=$(systemctl list-unit-files --no-legend 2>&1 > /dev/null); r=$?
  ms=$(( ($(date +%s%N) - s) / 1000000 ))
  printf '%s rc=%s ms=%s load=%s err=%s\n' "$(date -u +%FT%TZ)" "$r" "$ms" "$(cut -d' ' -f1-3 /proc/loadavg)" "${e//$'\n'/ | }"
  sleep 15
done
