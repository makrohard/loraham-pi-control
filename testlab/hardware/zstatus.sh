# zstatus.sh: the one fail-closed reader of an `lhpc status` text for rf-proof.sh (sourced; pure text on stdin, no box call).
# `lhpc status` renders "<N> stacks, <M> components: ..." and one "[<stack>] <title>  (<state>)" header per stack (or
# "[<stack>@<rid>]"), plus "[controller]", which the summary does not count. A header without one of the product's rollup
# states (running|degraded|stopped|failed|not-applicable|not-installed) makes the status incomplete ("unknown" fails closed).
#
# zs_check:    rc 0 if the text is a complete status (a summary line; exactly N stack headers besides [controller]; each id
#              once), else rc 1 and the reason on stderr. Every caller STOPs on rc 1: an incomplete status is never read as
#              "nothing runs".
# zs_running:  the running stacks in display order, space-separated; compare with `zs_sorted`.
# zs_bands:    "stack:band" of each running stack's first running component, sorted (a map, not display order).
# zs_badstate: rc 0 if a stack is degraded or failed.
# zs_sorted:   its arguments sorted, space-separated (for set comparison).
zs_check() {
  local t n h u; t=$(cat)
  n=$(printf '%s\n' "$t" | sed -nE 's/^([0-9]+) stacks, [0-9]+ components:.*/\1/p' | head -n 1)
  [ -n "$n" ] || { echo "zs_check: no summary line" >&2; return 1; }
  local bad; bad=$(printf '%s\n' "$t" | grep '^\[' | grep -v '^\[controller\] ' \
    | grep -cvE '^\[[a-z0-9_@-]+\] .*  \((running|degraded|stopped|failed|not-applicable|not-installed)\)$') || true
  [ "$bad" = 0 ] || { echo "zs_check: $bad stack header line(s) without a recognised state (incomplete status)" >&2; return 1; }
  h=$(printf '%s\n' "$t" | grep -E '^\[[a-z0-9_@-]+\] ' | grep -cv '^\[controller\] ') || true
  u=$(printf '%s\n' "$t" | sed -nE 's/^\[([a-z0-9_@-]+)\] .*/\1/p' | grep -vx controller | sort -u | wc -l)
  [ "$h" = "$n" ] && [ "$u" = "$n" ] || { echo "zs_check: $h stack headers ($u unique) for $n stacks (incomplete status)" >&2; return 1; }
}
zs_running() { sed -n '/^\[controller\]/d; s/^\[\([a-z0-9_@-]*\)\] .*(running)$/\1/p' | tr '\n' ' ' | sed 's/ $//'; }
zs_bands() { awk '/^\[/ {cur=""; if ($0 ~ /\(running\)$/) {cur=substr($1,2,length($1)-2)}; next}
  cur!="" && $2=="running" && match($0, /band [0-9]+/) {print cur ":" substr($0,RSTART+5,RLENGTH-5); cur=""}' | sort | tr '\n' ' ' | sed 's/ $//'; }
zs_badstate() { grep -qE '^\[[a-z0-9_@-]+\] .*\((degraded|failed)\)$'; }
zs_sorted() { printf '%s\n' "$@" | grep -v '^$' | sort | tr '\n' ' ' | sed 's/ $//'; }
