# Sourced by a workflow step that starts containers: `crun ARGS...` is `docker run --rm ARGS...` under a name of
# this step, and the step removes every container it named when it ends (success, failure, or the signals a job
# timeout or a cancel sends). The docker client's own end never ends its container: a client killed by the runner
# left the container running, and `--rm` removes a container only after it exits.
#
# The names are kept in a file, so a container started in `$(crun ...)` (a subshell) is removed too. The client
# runs in the background and the step waits for it: a trapped signal ends the wait at once, while a foreground
# client would hold its trap back until the container ended.
LHPC_CONTAINERS=$(mktemp "${RUNNER_TEMP:-/tmp}/lhpc-containers.XXXXXX")

lhpc_remove_containers() {
  if [ -s "$LHPC_CONTAINERS" ]; then
    xargs docker rm -f < "$LHPC_CONTAINERS" >/dev/null 2>&1 || true   # absent (already removed by --rm): fine
  fi
  rm -f "$LHPC_CONTAINERS"
}
trap lhpc_remove_containers EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

crun() {  # crun <docker run args...>: docker run --rm, named, removed when the step ends
  local name rc=0
  name="lhpc-ci-$$-$BASHPID-$RANDOM$RANDOM"
  echo "$name" >> "$LHPC_CONTAINERS"
  docker run --rm --name "$name" "$@" &
  wait $! || rc=$?
  return "$rc"
}
