"""The bounded system-service calls of bootstrap-deps.sh (BOOTSTRAP-RETRY-TABLES-v5, decision LBS-D10), as shell text.

Every retried `systemctl` call runs under `timeout`; a call that timed out is retried only after the state it was
meant to reach has been read again; the bounds are measured on the first field of /proc/uptime (monotonic, checked),
never on the wall clock, because the run restarts chrony. Exit 12 is the helper's own plumbing (its files, its
clock, `timeout` itself), so it never shares a code with a meaningful status.

The values were measured on box H (systemd 257, evidence-lhpc-0.14.0/BOOTSTRAP-RETRY-measure-H): `list-unit-files
UNIT` is rc 0 with a line when present and rc 1 with nothing when absent; `show --property=UnitFileState
--property=ActiveState --value` is rc 0 for every unit (an absent one prints an empty UnitFileState) and prints the
two values in its own order, so each value is classified by the set it belongs to; a dead bus is rc 1 with
"Failed to connect to … bus".
"""
from __future__ import annotations

# The named constants (section 2), each overridable by the environment variable of the same name for tests.
CONSTANTS = (("SVC_CALL_BOUND_S", 90), ("SVC_RETRIES", 4), ("SVC_RETRY_PAUSE_S", 5), ("SVC_READ_BOUND_S", 60),
             ("SVC_KILL_GRACE_S", 5), ("SVC_READS_PER_CHECK_D", 2), ("SVC_READS_PER_CHECK_R", 1),
             ("SVC_SCRIPT_BOUND_S", 1800))
UNIT_FILE_STATES = ("enabled", "enabled-runtime", "linked", "linked-runtime", "disabled", "masked", "masked-runtime",
                    "static", "indirect", "generated", "alias", "transient", "bad", "not-found")
ACTIVE_STATES = ("active", "reloading", "inactive", "failed", "activating", "deactivating")


def total_d(c: dict) -> int:
    """SVC_TOTAL_BOUND_D_S: the D-class total (a disable): attempts x (call + reads) + pauses."""
    k = c["SVC_KILL_GRACE_S"]
    return (c["SVC_RETRIES"] * ((c["SVC_CALL_BOUND_S"] + k) + c["SVC_READS_PER_CHECK_D"] * (c["SVC_READ_BOUND_S"] + k))
            + (c["SVC_RETRIES"] - 1) * c["SVC_RETRY_PAUSE_S"])


def total_r(c: dict) -> int:
    """SVC_TOTAL_BOUND_R_S: the R-class total (a restart): the baseline read, attempts x (call + reads), pauses."""
    k = c["SVC_KILL_GRACE_S"]
    return ((c["SVC_READ_BOUND_S"] + k)
            + c["SVC_RETRIES"] * ((c["SVC_CALL_BOUND_S"] + k) + c["SVC_READS_PER_CHECK_R"] * (c["SVC_READ_BOUND_S"] + k))
            + (c["SVC_RETRIES"] - 1) * c["SVC_RETRY_PAUSE_S"])


def parent_lines() -> list[str]:
    """The bash helper of the parent script, placed once immediately before the first systemctl call (L1).

    Usage errors in an override are exit 2; a missing `timeout`, an unreadable clock or a helper file that cannot be
    made are exit 12; neither can occur under --dry-run or -h, which exit before this point."""
    names = " ".join(n for n, _ in CONSTANTS)
    defaults = " ".join(f"{n}:{v}" for n, v in CONSTANTS)
    return [
        "# --- bounded system-service calls (BOOTSTRAP-RETRY-TABLES-v5) -------------------------------------",
        "# Each retried systemctl call runs under `timeout`; a call that timed out is retried only after its",
        "# state was read again; the bounds use the first field of /proc/uptime, never the wall clock (this",
        "# run restarts chrony). Exit 12 = this helper cannot run (no `timeout`, no clock, its own files).",
        'SVC_UPTIME="${SVC_UPTIME:-/proc/uptime}"',
        "svc_plumb() {   # the helper's own failure: exit 12 with the reason",
        '\techo "ERROR: the service-call helper cannot run: $1. (exit 12)" >&2',
        "\texit 12",
        "}",
        f"for _sv in {defaults}; do",
        '\t_sn=${_sv%%:*}',
        '\t_sx=${!_sn-${_sv#*:}}',
        "\tcase \"$_sx\" in ''|*[!0123456789]*)",
        '\t\techo "ERROR: $_sn must be 1 to 6 ASCII digits, at least 1 (got \'$_sx\')." >&2; exit 2 ;;',
        "\tesac",
        '\tif [ "${#_sx}" -gt 6 ] || [ "$((10#$_sx))" -lt 1 ]; then',
        '\t\techo "ERROR: $_sn must be 1 to 6 ASCII digits, at least 1 (got \'$_sx\')." >&2; exit 2',
        "\tfi",
        '\tprintf -v "$_sn" \'%s\' "$((10#$_sx))"',
        "done",
        f"# the totals per call (section 2): D = a disable, R = a restart with its baseline read ({names})",
        "SVC_TOTAL_BOUND_D_S=$(( SVC_RETRIES * ((SVC_CALL_BOUND_S + SVC_KILL_GRACE_S)"
        " + SVC_READS_PER_CHECK_D * (SVC_READ_BOUND_S + SVC_KILL_GRACE_S)) + (SVC_RETRIES - 1) * SVC_RETRY_PAUSE_S ))",
        "SVC_TOTAL_BOUND_R_S=$(( (SVC_READ_BOUND_S + SVC_KILL_GRACE_S) + SVC_RETRIES * ((SVC_CALL_BOUND_S + SVC_KILL_GRACE_S)"
        " + SVC_READS_PER_CHECK_R * (SVC_READ_BOUND_S + SVC_KILL_GRACE_S)) + (SVC_RETRIES - 1) * SVC_RETRY_PAUSE_S ))",
        "command -v timeout >/dev/null 2>&1 || svc_plumb \"coreutils' timeout is not installed\"",
        "svc_now() {   # whole seconds of the monotonic clock; status 12 when it cannot be read or is malformed",
        "\tlocal u='' r=0",
        '\tread -r u _ < "$SVC_UPTIME" || r=$?',
        '\t[ "$r" -eq 0 ] || return 12',
        "\tcase \"$u\" in ''|.*|*.*.*|*[!0123456789.]*) return 12 ;; esac",
        '\tu=${u%%.*}',
        '\t[ -n "$u" ] && [ "${#u}" -le 12 ] || return 12',
        "\tprintf '%s\\n' \"$((10#$u))\"",
        "}",
        "_st=0; _sx=$(svc_now) || _st=$?",
        '[ "$_st" -eq 0 ] || svc_plumb "the clock $SVC_UPTIME cannot be read"',
        "# the run's accounting (the per-run bound) and the stderr buffer: fresh files, made once",
        '_st=0; SVC_ACCT=$(mktemp) || _st=$?',
        '[ "$_st" -eq 0 ] || svc_plumb "mktemp failed"',
        '_st=0; SVC_ERRF=$(mktemp) || _st=$?',
        '[ "$_st" -eq 0 ] || svc_plumb "mktemp failed"',
        "printf '0\\n' > \"$SVC_ACCT\" || svc_plumb \"the accounting file cannot be written\"",
        "export SVC_ACCT",
        '_PLUMB_FAILED=""   # a deferred 12 (the packaged-service step, the time-source step), reported last',
        "svc_acct_add() {   # add $1 s to the run's total: 0 below the bound, 5 at or above it, 12 for a bad file",
        "\tlocal v='' r=0",
        '\t[ -f "$SVC_ACCT" ] && [ ! -L "$SVC_ACCT" ] || return 12',
        '\tv=$(cat "$SVC_ACCT") || r=$?',
        '\t[ "$r" -eq 0 ] || return 12',
        "\tcase \"$v\" in ''|*[!0123456789]*) return 12 ;; esac",
        '\t[ "${#v}" -le 6 ] || return 12',
        "\tv=$((10#$v + $1))",
        "\tprintf '%s\\n' \"$v\" > \"$SVC_ACCT\" || return 12",
        '\t[ "$v" -lt "$SVC_SCRIPT_BOUND_S" ] || return 5',
        "}",
        "svc_attempt() {   # svc_attempt BOUND ARGS...: one bounded systemctl call. Sets SVC_OUT and SVC_LAST (its last",
        "\t# stderr line, or \"no answer within BOUND s\"). Returns 0 done, 1 refused (an rc that is not a timeout),",
        "\t# 3 timed out (systemd's \"Connection timed out\", or no answer within BOUND), 5 the run's bound reached,",
        "\t# 12 plumbing (timeout itself failed, or a helper file or the clock).",
        "\tlocal b=$1 t0 t1 rc=0 a=0",
        "\tshift",
        '\tt0=$(svc_now) || return 12',
        '\tSVC_OUT=$(LC_ALL=C timeout -k "$SVC_KILL_GRACE_S" "$b" systemctl "$@" 2>"$SVC_ERRF") || rc=$?',
        '\tSVC_RC=$rc',
        '\tt1=$(svc_now) || return 12',
        '\tSVC_LAST=$(tail -n 1 "$SVC_ERRF") || return 12',
        '\t[ ! -s "$SVC_ERRF" ] || cat "$SVC_ERRF" >&2 || return 12',
        '\tsvc_acct_add "$((t1 - t0))" || a=$?',
        '\t[ "$a" -ne 12 ] || return 12',
        '\tcase "$rc" in',
        "\t\t0) return 0 ;;",
        '\t\t124|137) SVC_LAST="no answer within ${b} s"; [ "$a" -eq 0 ] || return 5; return 3 ;;',
        "\t\t125|126|127) return 12 ;;",
        "\tesac",
        '\t[ "$a" -eq 0 ] || return 5',
        "\tcase \"$SVC_LAST\" in *'Connection timed out') return 3 ;; esac",
        "\treturn 1",
        "}",
        "svc_read_retry() {   # svc_read_retry ARGS...: an idempotent read, retried on a timeout within SVC_RETRIES and",
        "\t# SVC_TOTAL_BOUND_D_S. Returns as svc_attempt; SVC_TRIES = the attempts made.",
        "\tlocal i=1 r t0 now",
        '\tt0=$(svc_now) || return 12',
        "\twhile :; do",
        '\t\tr=0; svc_attempt "$SVC_READ_BOUND_S" "$@" || r=$?',
        "\t\tSVC_TRIES=$i",
        '\t\t[ "$r" -eq 3 ] || return "$r"',
        '\t\t[ "$i" -lt "$SVC_RETRIES" ] || return 3',
        '\t\tnow=$(svc_now) || return 12',
        '\t\t[ $((now - t0 + SVC_RETRY_PAUSE_S)) -lt "$SVC_TOTAL_BOUND_D_S" ] || return 3',
        '\t\tsleep "$SVC_RETRY_PAUSE_S" || return 12',
        "\t\ti=$((i + 1))",
        "\tdone",
        "}",
        *_state_lines(),
        "",
    ]


def _state_lines() -> list[str]:
    """The reads that decide a unit's state and the disable built on them (rows 1-5, 8, 12-17, 30), on the forms
    measured on box H: `list-unit-files UNIT` (present rc 0 with a line, absent rc 1 with nothing on either stream,
    a dead bus rc 1 with "Failed to connect"), and one `show` of UnitFileState and ActiveState (rc 0, the two values
    in systemd's order, so each is placed by the set it belongs to; an absent unit has no UnitFileState)."""
    uf_todo, uf_done = "enabled|enabled-runtime|linked|linked-runtime", "disabled|masked|masked-runtime"
    return [
        "svc_present() {   # svc_present UNIT: 0 present, 1 absent, 2 unknown (never read as absent), 12 plumbing.",
        "\t# Absent needs a successful unfiltered listing AND the unit's own listing answering rc 1 with nothing.",
        "\tlocal r=0",
        "\tsvc_read_retry list-unit-files --no-legend || r=$?",
        '\t[ "$r" -ne 12 ] || return 12',
        '\t[ "$r" -eq 0 ] || return 2',
        '\tr=0; svc_read_retry list-unit-files --no-legend "$1" || r=$?',
        '\t[ "$r" -ne 12 ] || return 12',
        '\tif [ "$r" -eq 0 ] && [ -n "$SVC_OUT" ]; then return 0; fi',
        '\tif [ "$r" -eq 1 ] && [ "$SVC_RC" -eq 1 ] && [ -z "$SVC_OUT" ] && [ -z "$SVC_LAST" ]; then return 1; fi',
        "\treturn 2",
        "}",
        "svc_state() {   # svc_state UNIT: one bounded read of the unit's state, classified. Sets SVC_UF, SVC_AS and",
        "\t# SVC_STATE: done (disabled or masked, and inactive or failed), todo (something to disable or stop), gone (no",
        "\t# unit file, inactive), unknown (a failed read, a job in flight, a value no rule decides). 12 = plumbing.",
        "\tlocal r=0 v",
        '\tSVC_UF=""; SVC_AS=""; SVC_STATE=unknown',
        '\tsvc_attempt "$SVC_READ_BOUND_S" show --property=UnitFileState --property=ActiveState --value "$1" || r=$?',
        '\t[ "$r" -ne 12 ] || return 12',
        '\t[ "$r" -eq 0 ] || return 0',
        "\tfor v in $SVC_OUT; do",
        '\t\tcase "$v" in',
        "\t\t\tactive|reloading|inactive|failed|activating|deactivating) SVC_AS=$v ;;",
        "\t\t\t*) SVC_UF=$v ;;",
        "\t\tesac",
        "\tdone",
        '\tcase "$SVC_UF:$SVC_AS" in',
        "\t\t:inactive) SVC_STATE=gone ;;",
        f"\t\t{'|'.join(f'{u}:inactive|{u}:failed' for u in uf_done.split('|'))}) SVC_STATE=done ;;",
        f"\t\t{'|'.join(f'{u}:active|{u}:reloading|{u}:inactive|{u}:failed' for u in uf_todo.split('|'))}) SVC_STATE=todo ;;",
        f"\t\t{'|'.join(f'{u}:active|{u}:reloading' for u in uf_done.split('|'))}) SVC_STATE=todo ;;",
        "\tesac",
        "\treturn 0",
        "}",
        "svc_disable() {   # svc_disable UNIT: `disable --now`, each round reading the state first (a timed-out call",
        "\t# that took effect is NOOP, a half-done one is called again). 0 done (SVC_DONE: disabled, already,",
        "\t# confirmed, absent), 1 refused, 2 the state stays unknown, 3 no answer, 5 the run's bound, 12 plumbing.",
        "\tlocal n=0 r t0 now",
        '\tSVC_TRIES=0; SVC_DONE=""; SVC_LAST=""',
        '\tt0=$(svc_now) || return 12',
        "\twhile :; do",
        "\t\tn=$((n + 1))",
        '\t\tr=0; svc_state "$1" || r=$?',
        '\t\t[ "$r" -ne 12 ] || return 12',
        '\t\tcase "$SVC_STATE" in',
        '\t\t\tdone) [ "$SVC_TRIES" -eq 0 ] && SVC_DONE=already || SVC_DONE=confirmed; return 0 ;;',
        "\t\t\tgone)",
        '\t\t\t\tr=0; svc_present "$1" || r=$?',
        '\t\t\t\t[ "$r" -ne 12 ] || return 12',
        '\t\t\t\tif [ "$r" -eq 1 ]; then SVC_DONE=absent; return 0; fi',
        "\t\t\t\tr=2 ;;",
        "\t\t\ttodo)",
        '\t\t\t\tSVC_TRIES=$((SVC_TRIES + 1)); r=0',
        '\t\t\t\tsvc_attempt "$SVC_CALL_BOUND_S" disable --now "$1" || r=$?',
        '\t\t\t\tcase "$r" in 0) SVC_DONE=disabled; return 0 ;; 1|5|12) return "$r" ;; esac ;;',
        "\t\t\t*) r=2 ;;",
        "\t\tesac",
        '\t\t[ "$n" -lt "$SVC_RETRIES" ] || return "$r"',
        '\t\tnow=$(svc_now) || return 12',
        '\t\t[ $((now - t0 + SVC_RETRY_PAUSE_S)) -lt "$SVC_TOTAL_BOUND_D_S" ] || return "$r"',
        '\t\tsleep "$SVC_RETRY_PAUSE_S" || return 12',
        "\tdone",
        "}",
    ]
