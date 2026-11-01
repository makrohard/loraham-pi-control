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
        "",
    ]
