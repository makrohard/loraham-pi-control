# Sourced by a workflow step that starts containers. Every container is named before it is created, and when the step
# ends (EXIT, or INT/TERM: what a job timeout or a cancel sends the step's shell) the step kills and reaps its client,
# then removes the container by that name. The client's own end never ends its container (--rm removes it only once it
# exits), and a reaped client creates nothing more.
#
#   crun ARGS...                 docker run --rm ARGS... as such a container
#   lhpc_wait_for NAME CMD...    CMD, which starts the container NAME itself (the quota step's sizing program)
#
# The client runs in the background under `wait`: bash runs a trap only after a FOREGROUND child exits, while `wait`
# returns at once. Never call them inside $(...): bash holds its traps until a substitution ends.
LHPC_CONTAINER=""

lhpc_remove_container() {
  local out
  kill -KILL $(jobs -p) 2>/dev/null   # the client first (KILL: an attached client forwards TERM to its container)
  wait 2>/dev/null
  if [ -n "$LHPC_CONTAINER" ] && ! out=$(docker rm -f "$LHPC_CONTAINER" 2>&1) \
      && ! grep -qi 'no such container' <<<"$out"; then
    echo "::warning::container $LHPC_CONTAINER may still be running: docker rm -f: $out"
  fi
  LHPC_CONTAINER=""
}
trap lhpc_remove_container EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

lhpc_wait_for() {  # lhpc_wait_for NAME CMD...
  local rc=0
  LHPC_CONTAINER=$1
  shift
  "$@" &
  wait $! || rc=$?
  LHPC_CONTAINER=""                   # ended: --rm has removed it
  return "$rc"
}

crun() {  # crun <docker run args...>
  local name="lhpc-ci-$$-$RANDOM$RANDOM"
  lhpc_wait_for "$name" docker run --rm --name "$name" "$@"
}
