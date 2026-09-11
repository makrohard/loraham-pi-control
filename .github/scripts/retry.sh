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
