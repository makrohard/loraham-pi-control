#!/bin/bash
# Hand the node's single Companion client slot to an interactive meshcore-cli, CRASH-SAFELY.
#
# openHop's Companion frame server serves ONE client at a time: a new connection EVICTS the old
# one. While this CLI runs it holds a KERNEL flock LEASE on the lock file; the WebUI supervisor
# yields the slot only while that lease is actually held. The kernel drops the lease on ANY exit —
# normal, Ctrl-C, SIGKILL, or power loss/reboot — so a leftover lock PATHNAME is harmless: the
# WebUI re-acquires the (now free) lease and reconnects. File existence alone is never ownership.
#
# Usage: meshcli-run.sh <lock-path> <cmd> [args...]
set -u
LOCK="${1:?lock path required}"; shift
mkdir -p "$(dirname "$LOCK")"
# Open the lock file on fd 9 and take an exclusive lease. BLOCKING (with a timeout): the WebUI
# holds this same lease briefly while it establishes its own connection, so we WAIT for that
# transient hold rather than racing in and getting evicted. Once acquired, the lease is held for
# this shell's lifetime and released by the kernel when the process dies, however it dies — no
# trap can run on SIGKILL/power-loss, so ownership must NOT depend on one. A leftover lock
# pathname with no live owner is therefore harmless.
exec 9>"$LOCK"
if ! flock -w 30 9; then
  echo "meshcli: could not acquire the Companion slot within 30s (another client holds it)" >&2
  exit 1
fi
"$@"
