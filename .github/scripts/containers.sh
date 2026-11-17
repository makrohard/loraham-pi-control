# Sourced by a workflow step that starts containers. Every container is named before it is created. If the step ends
# while one is still running or being created (EXIT, or INT/TERM: what a job timeout or a cancel sends the step's
# shell), the step kills the whole process group of its client (the client and anything it started, such as the quota
# program's own docker client), then removes the container by that name. The client's end never ends its container
# (--rm removes it only once it exits), and a create the daemon had already received still completes after the
# client's death: so the name is checked again for a few seconds and removed whenever it is there. A container that
# survives that is reported, and before a name is used it is removed (a create that landed after those checks
# leaves a container under it, which would refuse the next create of that name). The step keeps its own exit status,
# and a second INT/TERM (the runner sends TERM 7.5 s after INT) does not cut its removal short. A client that ends
# while the step goes on (killed on its own, by OOM, or failed) is followed by the same removal of its name. A create
# that lands after all of that is removed by the job's last step (lhpc_sweep, every lhpc-ci-* name); after the job
# the hosted runner's machine is discarded.
#
#   crun ARGS...                 docker run --rm ARGS... as such a container
#   lhpc_wait_for NAME CMD...    CMD, which starts the container NAME itself (the quota step's sizing program)
#   lhpc_sweep                   remove every container named lhpc-ci-* (the job's last step, always)
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

lhpc_there() {  # lhpc_there NAME: 0 if the container NAME exists or its inspection failed otherwise (said), 1 if absent
  local out
  out=$(docker container inspect "$1" 2>&1) && return 0
  grep -qi 'no such' <<<"$out" && return 1
  echo "::warning::docker container inspect $1: $out"
  return 0
}

lhpc_remove_container() {
  local rc=$? pids pid i
  trap '' INT TERM                                # a second signal never cuts the removal short
  pids=$(jobs -p)
  for pid in $pids; do                            # each leads its own process group (setsid)
    kill -KILL -- "-$pid" 2>/dev/null || kill -KILL "$pid" 2>/dev/null || :   # gone already: nothing to end
  done
  [ -z "$pids" ] || wait 2>/dev/null
  if [ -n "$LHPC_CONTAINER" ]; then
    for ((i = 0; i <= LHPC_RECHECKS; i++)); do
      if [ "$i" -eq 0 ] || lhpc_there "$LHPC_CONTAINER"; then
        lhpc_rm "$LHPC_CONTAINER"
      fi
      [ "$i" -eq "$LHPC_RECHECKS" ] || sleep "$LHPC_RECHECK_S"
    done
    if lhpc_there "$LHPC_CONTAINER"; then
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
  lhpc_rm "$LHPC_CONTAINER"                       # the client's end is not its container's: absent after --rm, else gone now
  LHPC_CONTAINER=""
  return "$rc"
}

crun() {  # crun <docker run args...>
  local name="lhpc-ci-$$-$RANDOM$RANDOM"
  lhpc_wait_for "$name" docker run --rm --name "$name" "$@"
}

lhpc_sweep() {  # the job's last step: a create the daemon finished after its step's removals is removed here
  local ids
  ids=$(docker ps -aq --filter name=lhpc-ci-) || { echo "::warning::docker ps (the job's container sweep) failed"; return 0; }
  [ -n "$ids" ] || return 0
  echo "::warning::removing containers a step left: $(echo $ids)"
  docker rm -f $ids >/dev/null || echo "::warning::docker rm -f (the job's container sweep) failed"
}
