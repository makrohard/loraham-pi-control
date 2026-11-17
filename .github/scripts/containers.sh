# Sourced by a workflow step that starts containers: one bounded cleanup of named containers (G2-TESTSD fix plan v1).
#
# Each container is named "${LHPC_CI_PREFIX}<what>-<pid>..." (this run and attempt) and its step owns the name until
# lhpc_clean has removed it. lhpc_clean NAME GROUP removes the name while the client's process group GROUP (setsid)
# still has a member (the client can still be creating it), then once more after the group was seen empty. One deadline
# (LHPC_CLEAN_S, inside the runner's cancel: INT, then TERM 7.5 s later, then KILL 2.5 s after that) bounds the
# cleanup, and every docker call in it runs under `timeout`. A group still there at the deadline is killed. Any
# failure is an ::error:: and turns the step's status 0 into 1. The cleanup runs when a client ends and on EXIT (an
# error, INT/TERM, the step's end). A second INT/TERM during it never cuts it short.
#
# Boundary: a client that dies mid-create (killed alone by OOM or KILL, killed at the deadline, or killed by the
# runner's KILL) can leave a create the daemon still holds, which lands later. The job's last step (an inline,
# bounded sweep of this run's names) removes it if it is there by then and fails the job; after the job, the
# GitHub-hosted runner's machine is discarded.
#
#   crun ARGS...                   docker run --rm ARGS... as such a container
#   lhpc_wait_for NAME CMD...      CMD, which starts the container NAME itself (the quota step's sizing program)
#   lhpc_start NAME CMD...         CMD starts NAME and returns (docker run -d): the step owns NAME until lhpc_done
#   lhpc_done                      remove the name lhpc_start owns, now
#
# The command runs in the background, in its own process group (setsid), under `wait`: bash runs a trap only after
# a FOREGROUND child exits, while `wait` returns at once. Never call these inside $(...): bash holds its traps until
# a substitution ends.
LHPC_CI_PREFIX="lhpc-ci-${GITHUB_RUN_ID:-0}-${GITHUB_RUN_ATTEMPT:-0}-"
LHPC_CLEAN_S=${LHPC_CLEAN_S:-6}   # loop <= 6 s + its last rm <= 1 s + poll + the kill's rm <= 1 s: before the KILL at 10 s
LHPC_POLL_S=${LHPC_POLL_S:-0.5}
LHPC_CONTAINER=""                 # the name this step owns now
LHPC_GROUP=""                     # its client's process group while that may live
LHPC_PENDING=""                   # the status of an INT/TERM that came during a cleanup

_lhpc_bound() {  # _lhpc_bound END_US: LHPC_B = the seconds left until END_US, at least 1
  local left=$(( $1 - ${EPOCHREALTIME//[!0-9]/} ))
  [ "$left" -ge 1000000 ] || left=1000000
  printf -v LHPC_B '%d.%06d' $((left / 1000000)) $((left % 1000000))
}

lhpc_clean() {  # lhpc_clean NAME GROUP: 0 once NAME is gone; 1 and an ::error:: otherwise
  local name=$1 group=$2 end out rc alive killed=""
  end=$(( ${EPOCHREALTIME//[!0-9]/} + LHPC_CLEAN_S * 1000000 ))
  while :; do
    alive=""
    if [ -n "$group" ] && kill -0 -- "-$group" 2>/dev/null; then alive=1; fi
    if [ -z "$alive" ]; then
      group=""                                    # seen empty: never signalled again (its number may be reused)
    elif [ "${EPOCHREALTIME//[!0-9]/}" -ge "$end" ]; then
      kill -KILL -- "-$group" 2>/dev/null || :
      group="" alive="" killed=1
    fi
    _lhpc_bound "$end"
    rc=0
    out=$(timeout -k 1 "$LHPC_B" docker rm -f "$name" 2>&1) || rc=$?
    [ -n "$alive" ] || break                      # this removal came after the group was seen empty (or killed)
    sleep "$LHPC_POLL_S"
  done
  if [ "$rc" -ne 0 ] && ! grep -qi 'no such container' <<<"$out"; then
    echo "::error::container $name: its removal failed (docker rm -f rc $rc): $out"
    return 1
  fi
  if [ -n "$killed" ]; then
    echo "::error::container $name: its client was still there after ${LHPC_CLEAN_S} s and was killed; a create it held can land after this step (the job's sweep, then the runner's discard, end it)"
    return 1
  fi
  return 0
}

lhpc_release() {  # clean the name this step owns, now; an INT/TERM that came meanwhile then ends the step
  local rc=0
  if [ -n "$LHPC_CONTAINER" ]; then
    lhpc_clean "$LHPC_CONTAINER" "$LHPC_GROUP" || rc=1
  fi
  LHPC_CONTAINER="" LHPC_GROUP=""
  [ -z "$LHPC_PENDING" ] || exit "$LHPC_PENDING"
  return "$rc"
}

lhpc_on_exit() {
  local rc=$?
  trap '' INT TERM                                # from here on no signal reaches the step; lhpc_on_signal covers the line above
  if [ -n "$LHPC_CONTAINER" ]; then
    lhpc_clean "$LHPC_CONTAINER" "$LHPC_GROUP" || { [ "$rc" -ne 0 ] || rc=1; }
  fi
  LHPC_CONTAINER="" LHPC_GROUP=""
  exit "$rc"
}

lhpc_on_signal() {  # lhpc_on_signal STATUS: a function, so its `return` leaves only itself, never the cleanup
  case " ${FUNCNAME[*]:1} " in
    *" lhpc_clean "* | *" lhpc_on_exit "*) LHPC_PENDING=${LHPC_PENDING:-$1}; return 0 ;;   # mid-cleanup: it goes on
  esac
  trap '' INT TERM
  exit "$1"
}
trap lhpc_on_exit EXIT
trap 'lhpc_on_signal 130' INT
trap 'lhpc_on_signal 143' TERM

lhpc_wait_for() {  # lhpc_wait_for NAME CMD...
  local rc=0
  LHPC_CONTAINER=$1
  shift
  setsid "$@" &                                   # a background job is no group leader: setsid does not fork, $! leads
  LHPC_GROUP=$!
  wait $! || rc=$?
  lhpc_release || { [ "$rc" -ne 0 ] || rc=1; }
  return "$rc"
}

crun() {  # crun <docker run args...>
  local name="${LHPC_CI_PREFIX}$$-$RANDOM$RANDOM"
  lhpc_wait_for "$name" docker run --rm --name "$name" "$@"
}

lhpc_start() {  # lhpc_start NAME CMD...: CMD starts NAME and returns; the step owns NAME until lhpc_done or EXIT
  local rc=0
  LHPC_CONTAINER=$1
  shift
  setsid "$@" &
  LHPC_GROUP=$!
  wait $! || rc=$?
  LHPC_GROUP=""                                   # a client of `run -d` leaves no member behind: never signal its number
  return "$rc"
}

lhpc_done() {  # remove the name lhpc_start owns, now
  lhpc_release
}
