row U0 <<'ROW'
cap U0-before.txt "set -o pipefail; cd $RT/src/loraham-pi-control && git rev-parse HEAD && git rev-parse --abbrev-ref HEAD && git status --porcelain | wc -l && lhpc --version"
eq "on main before the update" "$(sed -n 2p "$LIVE/U0-before.txt")" main
eq "clean before the update" "$(sed -n 3p "$LIVE/U0-before.txt")" 0
if [ "$(sed -n 1p "$LIVE/U0-before.txt")" != "$REL" ]; then
  cap U0-check.txt "lhpc self-update"; has "the update offered is v0.13.3" "$(cat "$LIVE/U0-check.txt")" "(v0.13.3)"
  rc=0; Hx "lhpc self-update --apply --yes" > "$LIVE/U0-apply.txt" 2>&1 || rc=$?; echo "rc=$rc" >> "$LIVE/U0-apply.txt"
  A=$(cat "$LIVE/U0-apply.txt")
  case "$rc:$A" in
    0:*) eq "no ERR line in the apply" "$(count -E '^ERR' "$LIVE/U0-apply.txt")" 0
         eq "no units-stale / venv / dependency line" "$(count -i -E 'units-stale|venv|Dependencies changed' "$LIVE/U0-apply.txt")" 0;;
    1:"ERR   Update applied — units-stale:"*)
         echo "units-stale from a <= 0.12.10 apply: the documented recovery (CHANGELOG 0.13.0)"
         cap U0-status-stale.txt "lhpc status"; sed -n '/^\[controller\]/,/^$/p' "$LIVE/U0-status-stale.txt" > "$LIVE/U0-controller-stale.txt"
         cap U0-repair.txt "lhpc self-update --repair-integration"
         has "the repair reports success" "$(cat "$LIVE/U0-repair.txt")" "integration installed/repaired"
         cap U0-webserver-apply.txt "lhpc webserver apply";;
    *) fail "the apply failed (rc $rc), not the documented units-stale case (see U0-apply.txt)";;
  esac
else
  echo "already at v0.13.3: no apply" > "$LIVE/U0-apply.txt"
fi
cap U0-recheck.txt "lhpc self-update"; has "up to date" "$(cat "$LIVE/U0-recheck.txt")" "Up to date"
if Hx "test -e $RT/state/selfupdate.incomplete"; then cap U0-incomplete.txt "cat $RT/state/selfupdate.incomplete"; fail "an incomplete update is recorded (see U0-incomplete.txt)"; fi
eq "HEAD is v0.13.3 (git)" "$(Hx "git -C $RT/src/loraham-pi-control rev-parse HEAD")" "$REL"
eq "branch main (git)" "$(Hx "git -C $RT/src/loraham-pi-control rev-parse --abbrev-ref HEAD")" main
eq "clean after" "$(Hx "git -C $RT/src/loraham-pi-control status --porcelain")" ""
cap U0-version.txt "lhpc --version"; eq "version" "$(sed -n 1p "$LIVE/U0-version.txt")" "lhpc 0.13.3"
cap U0-venv.txt "$RT/venv/lhpc/bin/python -c 'import importlib.metadata as m; print(m.version(\"loraham-pi-control\"))' && $RT/venv/lhpc/bin/python -m pip check"
eq "the venv's installed version" "$(sed -n 1p "$LIVE/U0-venv.txt")" "0.13.3"
cap U0-doctor.txt "lhpc doctor"; eq "no units-compatible note" "$(count -F 'units-compatible' "$LIVE/U0-doctor.txt")" 0
cap U0-status.txt "lhpc status"; sed -n '/^\[controller\]/,/^$/p' "$LIVE/U0-status.txt" > "$LIVE/U0-controller.txt"
eq "no stack running after the update" "$(count -E '^[0-9]+ stacks, .*running=' "$LIVE/U0-status.txt")" 0
[ -z "$(dpid)" ] || stop "a daemon runs after the update"
cap U0-web.txt "systemctl --user is-active lhpc-web.service"
ROW
