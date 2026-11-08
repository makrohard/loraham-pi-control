#!/usr/bin/env bash
# backoff <argv...> — the TRANSFER backoff (docs/testlab.md, "Running the verification lanes"):
# run argv until it exits 0, repeating it only when it failed on a transfer, by the one transfer
# policy, lhpc/core/transfer.py: the first retry at once, then after 1, 2, 5 and 10 min, then
# every 10 min; no attempt starts past the 30 min retry-start deadline (the attempts' wall time
# plus the waits); a running attempt is bounded by its own timeout. A failure repeats only when
# every error line of its stdout and of its stderr is a failed transfer (the network, an HTTP 5xx);
# an HTTP 4xx, a hash mismatch, any other error line, or no error line at all returns at once.
# Every wait is logged with the failed status, and the command's own status is returned.
#
# Only around a transfer (an acquisition or a download), never around a verdict: a retry of
# `pytest` or `pip-audit` could turn a true red green (tests/repo/test_workflow_shell.py pins it).
#
# The command runs in the CALLER's shell (a function's side effects stay visible); its stdout and
# stderr pass through unchanged, each also copied to a file through a FIFO and `tee`, and the
# copies are judged only after both `tee`s have ended. LHPC_TRANSFER_WAITS and
# LHPC_TRANSFER_TOTAL replace the schedule in tests (transfer.py reads them). Locals are prefixed:
# a plain `local n` here would shadow the caller's own `n` while the command runs.
_BACKOFF_POLICY="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)/lhpc/core/transfer.py"

backoff() {
  local _bo_dir _bo_status _bo_retries=0 _bo_used=0 _bo_t0 _bo_w _bo_out _bo_err
  _bo_dir="$(mktemp -d)" || return 1
  while true; do
    rm -f "$_bo_dir/o" "$_bo_dir/e"
    mkfifo "$_bo_dir/o" "$_bo_dir/e" || { rm -rf "$_bo_dir"; return 1; }
    tee "$_bo_dir/out" < "$_bo_dir/o" &
    _bo_out=$!
    tee "$_bo_dir/err" < "$_bo_dir/e" >&2 &
    _bo_err=$!
    _bo_t0=$SECONDS
    # `$?` must be read INSIDE the else branch: after a closed `if`, it is the status of the
    # compound, which is 0. A command in an `if` condition also never trips a caller's `set -e`.
    if "$@" > "$_bo_dir/o" 2> "$_bo_dir/e"; then
      _bo_status=0
    else
      _bo_status=$?
    fi
    wait "$_bo_out" "$_bo_err" || true
    _bo_used=$((_bo_used + SECONDS - _bo_t0))
    if [ "$_bo_status" -eq 0 ]; then
      rm -rf "$_bo_dir"
      return 0
    fi
    if ! _bo_w="$(python3 "$_BACKOFF_POLICY" next "$_bo_retries" "$_bo_used" \
                  "$_bo_dir/out" "$_bo_dir/err")"; then
      echo "backoff: '$1' failed (status $_bo_status); giving up: $_bo_w" >&2
      rm -rf "$_bo_dir"
      return "$_bo_status"
    fi
    echo "backoff: '$1' failed (status $_bo_status); retrying in ${_bo_w}s" >&2
    sleep "$_bo_w"
    _bo_used=$((_bo_used + _bo_w))
    _bo_retries=$((_bo_retries + 1))
  done
}
