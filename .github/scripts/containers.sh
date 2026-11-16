# Sourced by a workflow step that starts containers. Every container is named before it is created. If the step ends
# while one is still running or being created (EXIT, or INT/TERM: what a job timeout or a cancel sends the step's
# shell), the step kills the whole process group of its client (the client and anything it started, such as the quota
# program's own docker client), then removes the container by that name. The client's end never ends its container
# (--rm removes it only once it exits), and a create the daemon had already received still completes after the
# client's death: so the name is checked again for a few seconds and removed whenever it is there. A container that
# survives that is reported, and before a name is used it is removed (a create that landed after those checks
# leaves a container under it, which would refuse the next create of that name). The step keeps its own exit status.
#
#   crun ARGS...                 docker run --rm ARGS... as such a container
#   lhpc_wait_for NAME CMD...    CMD, which starts the container NAME itself (the quota step's sizing program)
#
# The command runs in the background, in its own process group (setsid), under `wait`: bash runs a trap only after
# a FOREGROUND child exits, while `wait` returns at once. Never call them inside $(...): bash holds its traps until a
# substitution ends.
LHPC_CONTAINER=""
LHPC_RECHECKS=5                                   # x LHPC_RECHECK_S: how long a late create is looked for
LHPC_RECHECK_S=${LHPC_RECHECK_S:-1}

lhpc_rm() {  # lhpc_rm NAME: remove the container NAME; one that is absent is fine, any other failure is said
  local out
  out=$(docker rm -f "$1" 2>&1) || grep -qi 'no such container' <<<"$out" || echo "::warning::docker rm -f $1: $out"
}

lhpc_remove_container() {
  local rc=$? pids pid i
  pids=$(jobs -p)
  for pid in $pids; do                            # each leads its own process group (setsid)
    kill -KILL -- "-$pid" 2>/dev/null || kill -KILL "$pid" 2>/dev/null || :   # gone already: nothing to end
  done
  [ -z "$pids" ] || wait 2>/dev/null
  if [ -n "$LHPC_CONTAINER" ]; then
    for ((i = 0; i <= LHPC_RECHECKS; i++)); do
      if [ "$i" -eq 0 ] || docker container inspect "$LHPC_CONTAINER" >/dev/null 2>&1; then
        lhpc_rm "$LHPC_CONTAINER"
      fi
      [ "$i" -eq "$LHPC_RECHECKS" ] || sleep "$LHPC_RECHECK_S"
    done
    if docker container inspect "$LHPC_CONTAINER" >/dev/null 2>&1; then
      echo "::warning::container $LHPC_CONTAINER is still there after the step's removals"
    fi
  fi
  LHPC_CONTAINER=""
  exit "$rc"
}
trap lhpc_remove_container EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

lhpc_wait_for() {  # lhpc_wait_for NAME CMD...
  local rc=0
  LHPC_CONTAINER=$1
  shift
  lhpc_rm "$LHPC_CONTAINER"                       # a leftover of this name (see above) never blocks this create
  setsid "$@" &                                   # a background job is no group leader: setsid does not fork, $! leads
  wait $! || rc=$?
  LHPC_CONTAINER=""                               # ended: --rm has removed it
  return "$rc"
}

crun() {  # crun <docker run args...>
  local name="lhpc-ci-$$-$RANDOM$RANDOM"
  lhpc_wait_for "$name" docker run --rm --name "$name" "$@"
}
