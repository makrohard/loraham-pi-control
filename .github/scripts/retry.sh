#!/usr/bin/env bash
# retry <attempts> <argv...> — run argv until it exits 0, at most <attempts> times.
#
# Deliberately tiny. It does NOT classify failures: it cannot tell a transport fault from a real
# defect, and pretending otherwise is how a retry turns a red verdict green. The safety comes from
# WHERE this is used — only around idempotent acquisition/setup (package installs, `git fetch`,
# browser downloads), never around a command whose exit status is a verdict about the product.
# `pip-audit` is the cautionary case: it exits 1 on a real vulnerability.
#
# Sleeps RETRY_DELAY seconds between attempts, doubling. Tests set RETRY_DELAY=0.
# Locals are prefixed because `retry` runs the command in the CALLER's shell: a plain `local n`
# here shadows the caller's own `n` for the duration of the call, which is a silent corruption of
# whatever it was counting.
retry() {
  local _retry_max="$1"; shift
  local _retry_n=1 _retry_status _retry_delay="${RETRY_DELAY-5}"
  while true; do
    # `$?` must be read INSIDE the else branch: after a closed `if`, it is the status of the
    # compound, which is 0. Reading it there also keeps `set -e` callers safe, since a command in
    # an `if` condition never triggers the errexit trap.
    if "$@"; then
      return 0
    else
      _retry_status=$?
    fi
    if [ "$_retry_n" -ge "$_retry_max" ]; then
      echo "retry: '$1' failed $_retry_max time(s); giving up (status $_retry_status)" >&2
      return "$_retry_status"
    fi
    echo "retry: '$1' failed (status $_retry_status); attempt $((_retry_n + 1)) of $_retry_max in ${_retry_delay}s" >&2
    sleep "$_retry_delay"
    _retry_delay=$((_retry_delay * 2))
    _retry_n=$((_retry_n + 1))
  done
}

# backoff <argv...> — the TRANSFER backoff (docs/testlab.md, "Running the verification lanes"):
# run argv until it exits 0, retrying at once, then after 1, 2, 5 and 10 min, then every 10 min,
# until the waits would pass 30 min; every wait is logged with the failed status, and the last
# status is returned. Like `retry`, only around a transfer (an acquisition or a download), never
# around a verdict. BACKOFF_WAITS (seconds) and BACKOFF_TOTAL replace the schedule in tests; a
# last wait of 0 is not repeated.
backoff() {
  local -a _bo_waits
  read -r -a _bo_waits <<< "${BACKOFF_WAITS-0 60 120 300 600}"
  local _bo_total="${BACKOFF_TOTAL-1800}" _bo_spent=0 _bo_i=0 _bo_w _bo_status
  while true; do
    if "$@"; then
      return 0
    else
      _bo_status=$?
    fi
    if [ "$_bo_i" -lt "${#_bo_waits[@]}" ]; then
      _bo_w="${_bo_waits[$_bo_i]}"
    else
      _bo_w="${_bo_waits[${#_bo_waits[@]}-1]}"
      [ "$_bo_w" -gt 0 ] || _bo_w=$((_bo_total + 1))
    fi
    if [ $((_bo_spent + _bo_w)) -gt "$_bo_total" ]; then
      echo "backoff: '$1' still failing after ${_bo_spent}s of waits; giving up (status $_bo_status)" >&2
      return "$_bo_status"
    fi
    echo "backoff: '$1' failed (status $_bo_status); retrying in ${_bo_w}s" >&2
    sleep "$_bo_w"
    _bo_spent=$((_bo_spent + _bo_w))
    _bo_i=$((_bo_i + 1))
  done
}
