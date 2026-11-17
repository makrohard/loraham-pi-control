#!/bin/bash
# fw-boxproof.sh dry|live: the firewall box proof on box H. It starts from box H on clean main at FWSHA (the update itself
#  is done before it) and proves the claims below, each change journalled with its undo first.
# Claims: (1) a radio change without an apply: the console stays remote over a reboot; an unchanged radio's Meshtastic starts;
#  the changed radio's is refused naming the apply; (2) one apply; (3) a page exposed from the console is reachable from the
#  LAN with no sudo; (4b) one apply clears the re-apply notice. An apply that ends TRANSITIONAL is the designed two-phase
#  (firewall_helper.py _transition_sets): the transition record /etc/lhpc/firewall.transition.json is read; an old_allow row on
#  a page's SAVED port is a STOP; else the product's cleanup runs and the state must read Active.
# Row order: FW0 read -> FW3 r4 disabled -> FW3a a lab node identity -> FW4 apply (4b) -> FW5 r4 enabled (the radio change, no
#  apply) -> FW6 starts (1) -> FW7 reboot (1) -> FW8 apply (2) -> FW9 page (3) -> FE undo -> RE.
# Lab values: testlab/hardware/lab.local.env (git-ignored; dry reads lab.example.env): BOX_H_HOST, BOX_H_USER (default lhpc),
#  BOX_H_RUNTIME (default ~/loraham-pi-control), EVD_ROOT. Box H's password: live reads it from stdin, line 1; sudo -S with it.
#  A password EXACTLY equal to the login name (a lab box) is not masked; REPORT.md and run.log open with a NOTE that says so.
# INPUTS (env): FWSHA (40 hex: the sha under proof, on FWREF, default cons/b4-cln-r1), FWFROM (40 hex: the box's sha before
#  the update; FWSHA must contain it), FWCI (a green CI run of FWSHA), LANCIDR (the console's allowed CIDR, read at FW0 when
#  empty), LABNAME/LABSHORT (the lab node identity; never a callsign: one equal to OPERATOR_CALL, any case, any SSID, STOPs
#  before any box contact).
# RF: FW6 and FW9 start meshtastic@r2, FW6 tries meshtastic@r4 (normal power). BEFORE any start (mtpre): the radio's row (one,
#  no high-power grant), its model's power_max_dbm, and the stack's max_power (meshtasticd's cap before its first frame) <=
#  min(17, that cap), else STOP; after a start, its "Final Tx power" <= 17 (corroboration), else STOP. r4's expected refusal
#  counts only when a read of the running set shows it is not running.
# Box writes, each with its undo journalled BEFORE it: r4 enabled off/on (undo: on); the running stacks stopped (undo:
#  started); the lab identity (undo: --reset); the proxy of meshtastic@r2 (undo: --mode local + webserver apply); the firewall
#  applies (root, /etc/lhpc + nft; not undone: the state at FWSHA; a return to FWFROM's rules = reset the checkout, then the
#  same apply sequence); one planned reboot (FW7). Any live exit before FE's end (a STOP, a signal) replays the journalled
#  undos last first, after the safety stop (each checked; signals held off); one that fails is named UNRESOLVED in STOP.txt.
# Exit: 0 done, 1 a FAIL row, 2 a STOP, 130 a signal, 3 any of them when the safety stop of a started stack failed (it may
#  still be running), else 4 when an undo did not replay (the box may be left changed).
# Evidence: $EVD_ROOT/fw-boxproof-<UTC time> (run.log, REPORT.md, UNDO.txt, SHA256SUMS).
set -uo pipefail
MODE=${1:-dry}; case "$MODE" in dry|live) ;; *) echo "usage: fw-boxproof.sh dry|live"; exit 2;; esac
KIT=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd); REPO=$(git -C "$KIT" rev-parse --show-toplevel) || exit 2
LAB=$KIT/lab.local.env; [ "$MODE" = dry ] && LAB=$KIT/lab.example.env
# shellcheck source=/dev/null
RFPROOF_SOURCE_ONLY=1 . "$KIT/rf-proof.sh" || { echo "STOP: rf-proof.sh does not read"; exit 2; }   # its one redactor, capture
unset OUT XRC   # inherited, OUT would send rf-proof.sh's captures (${OUT:-$EVD}) out of this run's evidence; the redactor's values are set below
# the lab file is data, read only by rf-proof.sh's labread (its keys' grammars checked; a key set in the environment wins)
labread "$LAB"
# the inputs from the environment: each against its grammar before any use, as labcheck does for the lab keys
for k in FWSHA FWFROM FWCI FWREF LANCIDR LABNAME LABSHORT; do
  case $k in FWSHA|FWFROM) g='^([0-9a-f]{40})?$';; FWCI) g='^[0-9]*$';; FWREF) g='^([A-Za-z0-9._][A-Za-z0-9._/-]*)?$';;
    LANCIDR) g='^(([0-9]{1,3}\.){3}[0-9]{1,3}/[0-9]{1,2})?$';; LABNAME) g='^([A-Za-z0-9][A-Za-z0-9-]*)?$';; LABSHORT) g='^[A-Za-z0-9]*$';; esac
  [[ ${!k:-} =~ $g ]] || stop "$k: $(printf %q "${!k}") is not of its form ($g)"; done
[ -n "${BOX_H_HOST:-}" ] && [ -n "${EVD_ROOT:-}" ] || { echo "STOP: $LAB sets no BOX_H_HOST or EVD_ROOT"; exit 2; }
HOSTIP=$BOX_H_HOST; HH=${BOX_H_USER:-lhpc}@$HOSTIP; EVD=${EVD_ROOT/#\~/$HOME}/fw-boxproof-$(date -u +%Y%m%dT%H%M%SZ)
FROM=${FWFROM:-}; REF=${FWREF:-cons/b4-cln-r1}
RTB=$(shq "${BOX_H_RUNTIME:-~/loraham-pi-control}")   # one shell word in every box command; ~/ stays the box login's home
LH="$RTB/venv/lhpc/bin/lhpc"; CK="$RTB/src/loraham-pi-control"; PY="cd $RTB && ./venv/lhpc/bin/python"
FWSHA=${FWSHA:-}; FWCI=${FWCI:-}; LANCIDR=${LANCIDR:-}; LABNAME=${LABNAME:-LabH2}; LABSHORT=${LABSHORT:-LH2}
PW=; [ "$MODE" = dry ] || IFS= read -r PW || true; exec 0< /dev/null   # stdin: one line, then closed
[ "$MODE" = dry ] || [ -n "$PW" ] || { echo "STOP: no box H password on stdin (line 1)"; exit 2; }
PW_H=$PW; PW_Z=; MC_PIN=; TRACKER_CALL=; OPERATOR_CALL=${OPERATOR_CALL:-}   # what the redactor masks
# EXACTLY the login name (rf-proof.sh's pweq): a lab box, allowed unmasked and noted in REPORT.md and run.log; a part of it: STOP
pweq H || case "lhpc ${HH%@*}" in *"$PW"*) [ "$MODE" = dry ] || { echo "STOP: the box password is part of the login name"; exit 2; };; esac
# the lab identity is never the operator's call: compared without case and SSID, before any box contact (it would reach the
#  box command's argv and the node's identity on air)
callbase() { local c=${1^^}; printf '%s' "${c%%-*}"; }
for k in LABNAME LABSHORT; do [ -z "$OPERATOR_CALL" ] || [ "$(callbase "${!k}")" != "$(callbase "$OPERATOR_CALL")" ] \
  || { echo "STOP: $k is the operator's call (the lab identity is never a callsign)"; exit 2; }; done
RUNSET_PY='from lhpc.core import instances; from lhpc.core.services import ControllerService; s = ControllerService(); [print(k) for st in s.stacks() for k in instances.running_keys(s, st.id)]'
RUNSET="$PY -c '$RUNSET_PY'"
ENABLE_PY='import sys; from lhpc.core import radio_start; from lhpc.core.services import ControllerService; ok, msg = radio_start.set_enabled(ControllerService(), sys.argv[1], sys.argv[2] == "on"); print(("OK " if ok else "REFUSED ") + msg); sys.exit(0 if ok else 3)'
ROWR4_PY='from lhpc.core import paths, radios; [print("ROW", r.id, r.model, r.band, r.enabled) for r in radios.load(paths.resolve_paths()).radios if r.id == "r4"]'
# the apply: the root steps read the password ONCE (sudo -S -v on the only stdin line), then sudo -n; lhpc reads no stdin
APPLY="cd $RTB && ./venv/lhpc/bin/lhpc firewall --script > /dev/null && sudo -S -p '' -v && sudo -n bash $RTB/config/files/firewall/firewall-apply.sh < /dev/null && sudo -n systemctl start lhpc-firewall-check.service < /dev/null && ./venv/lhpc/bin/lhpc webserver apply < /dev/null"
PORT_PY='from lhpc.core.services import ControllerService; print("PORT", ControllerService()._page_ports().get("meshtastic@r2", 0))'
FTX="grep -ac 'Final Tx power' $RTB/logs/start-meshtastic-868-r2.log 2>/dev/null || true"
FSZ="stat -c %s $RTB/logs/start-meshtastic-868-r2.log 2>/dev/null || echo 0"   # the start log's size: a shrink = the product capped it
# fwapply <tag>: the apply; a TRANSITIONAL result -> the transition judge, then the product's cleanup (designed two-phase)
TRANS_PY='import json
from lhpc.core.services import ControllerService
saved = {port: sid for sid, port, keeps, _w in ControllerService()._stack_webs_candidates() if port and keeps}
t = json.load(open("/etc/lhpc/firewall.transition.json")); bad = []
print("OLD_ALLOW", len(t.get("old_allow") or []), "OLD_DROP", len(t.get("old_drop") or []))
for e in t.get("old_allow") or []:
    p = e.get("port"); print("ALLOW", e.get("proto"), e.get("family"), e.get("addr"), p, "saved-page=" + saved.get(p, "-"))
    if p in saved: bad.append(p)
for e in t.get("old_drop") or []: print("DROP", e.get("proto"), e.get("family"), e.get("addr"), e.get("port"))
print("VERDICT", "STOP" if bad else "OK")'
CLEANUP_CMD="sudo -S -p '' -v && sudo -n bash $RTB/config/files/firewall/firewall-cleanup.sh < /dev/null"
fwapply() { XIN "$1-apply.txt" "$PW" "$APPLY"; [ "$XRC" = 0 ] || stop "$1: the apply sequence rc $XRC"
  if has "$1-apply.txt" "TRANSITIONAL" && [ "$MODE" = live ] || [ "$MODE" = dry ]; then
    must "$1-transition.txt" "$PY -c '$TRANS_PY'"
    [ "$MODE" = dry ] || grep -qx 'VERDICT OK' "$EVD/$1-transition.txt" || stop "$1: the transition record holds an old_allow row on a page's saved port ($1-transition.txt)"
    XIN "$1-cleanup.txt" "$PW" "$CLEANUP_CMD"; [ "$XRC" = 0 ] || stop "$1: the cleanup rc $XRC"
    [ "$MODE" = dry ] || ! grep -qF "TRANSITIONAL" "$EVD/$1-cleanup.txt" || fail "$1: still TRANSITIONAL after the cleanup"
    must "$1-transition-after.txt" "test -e /etc/lhpc/firewall.transition.json && echo PRESENT || echo ABSENT"
    [ "$MODE" = dry ] || grep -qx ABSENT "$EVD/$1-transition-after.txt" || fail "$1: the transition record is still there after the cleanup"; fi; }
# literals asserted below, each checked in FWSHA's source at prep (a literal the code no longer has = STOP before any box change)
LITS=("re-apply the firewall before you reboot" "firewall-apply.sh" "Firewall: Active" "Firewall: Changes pending" "Remote listener active" "def _page_ports" "TRANSITIONAL" "firewall-cleanup.sh" "def _stack_webs_candidates" "firewall.transition.json")   # meshtasticd's 'Final Tx power' is the firmware's own log text (measured on the Zero 2026-11-15)
LOG=/dev/null; [ "$MODE" = live ] && { mkdir -p "$EVD" || exit 2; LOG=$EVD/run.log; }
say() { echo "$(date -u +%FT%TZ) $*" | tee -a "$LOG"; }
[ "$MODE" = live ] && pweq H && { pwnote | awk '{ print } END { print "" }' > "$EVD/REPORT.md" || exit 2; say "$(pwnote)"; }
stop() { say "STOP: $*"; [ "$MODE" = live ] && echo "STOP: $*" >> "$EVD/STOP.txt"; exit 2; }
# The safety stop: a stack the proof starts is marked (started) before its start and unmarked once its stop is checked
#  (stopped). On ANY exit of a live run (a STOP, a FAIL, the end, a signal) the EXIT trap stops every marked stack: its guarded
#  stop form, through the one redactor, checked (any rc but 0, ssh 255 too, failed), one retry. A stop that fails twice
#  makes the exit 3: the stack may still be running and its TX power is unverified.
#  Then the undo replay: every journalled undo (UNDOS, the commands of UNDO.txt) last first, each through the redactor and
#  checked; FE's end clears the list (it ran them itself). Every undo is idempotent (a start/stop of the running set, r4 on,
#  the identity reset, the proxy local), so one whose change never happened is harmless. One that fails: UNRESOLVED.
STARTED=() UNDOS=()
started() { STARTED+=("$1"); }
stopped() { local k keep=(); for k in "${STARTED[@]}"; do [ "$k" = "$1" ] || keep+=("$k"); done; STARTED=("${keep[@]}"); }
onexit() { local rc=$? k i; trap - EXIT; trap '' INT TERM HUP; [ "$MODE" = live ] || exit "$rc"   # the recovery runs to its end
  for k in "${STARTED[@]}"; do
    for i in 1 2; do capture "SAFESTOP-$k-$i.txt" sshx n "$(gstop "$k")"; [ "$XRC" = 0 ] && { say "safety stop: $k stopped"; continue 2; }; done
    say "UNSAFE: $k may still be running, TX power unverified"; echo "UNSAFE: $k may still be running, TX power unverified" >> "$EVD/STOP.txt"; rc=3
  done
  [ "${#UNDOS[@]}" = 0 ] || say "undo: replaying ${#UNDOS[@]} journalled change(s), last first"
  for ((i = ${#UNDOS[@]} - 1; i >= 0; i--)); do capture "UNDO-$i.txt" sshx n "${UNDOS[$i]}"
    if [ "$XRC" = 0 ]; then say "undo $i: replayed"; else say "UNRESOLVED: undo $i (rc $XRC, UNDO-$i.txt): ${UNDOS[$i]}"
      echo "UNRESOLVED: undo $i (rc $XRC): ${UNDOS[$i]}" >> "$EVD/STOP.txt"; [ "$rc" = 3 ] || rc=4; fi; done
  exit "$rc"; }
trap onexit EXIT; trap 'exit 130' INT TERM HUP
fail() { say "FAIL: $*"; [ "$MODE" = live ] && echo "$*" >> "$EVD/FAILED.txt"; }
ok() { say "ok: $*"; }
report() { [ "$MODE" = live ] && echo "| $* |" >> "$EVD/REPORT.md"; say "REPORT | $*"; }
# X <file> <command>: on box H, output in $EVD/<file>, rc in XRC. XIN <file> <stdin> <command>: the same with stdin.
# The output goes through rf-proof.sh's capture: redacted (the password, the call) before it is written.
sshx() { local how=$1; shift   # sshx n <command> (no stdin) | sshx i <stdin> <command>
  if [ "$how" = n ]; then SSHPASS=$PW sshpass -e ssh -n -o PubkeyAuthentication=no -o ConnectTimeout=10 "$HH" "bash -lc $(printf %q "$*")"
  else local in=$1; shift; printf '%s\n' "$in" | SSHPASS=$PW sshpass -e ssh -o PubkeyAuthentication=no -o ConnectTimeout=10 "$HH" "bash -lc $(printf %q "$*")"; fi; }
X() { local f=$1; shift; if [ "$MODE" = dry ]; then echo "  [dry] H > $f: $*"; XRC=0; return 0; fi
  capture "$f" sshx n "$*"; [ "$XRC" != 255 ] || stop "$f: ssh transport rc 255"; }
XIN() { local f=$1 in=$2; shift 2; if [ "$MODE" = dry ]; then echo "  [dry] H > $f: (stdin) $*"; XRC=0; return 0; fi
  capture "$f" sshx i "$in" "$*"; [ "$XRC" != 255 ] || stop "$f: ssh transport rc 255"; }
must() { X "$@"; [ "$XRC" = 0 ] || stop "$1: rc $XRC"; }
body() { [ "$MODE" = dry ] && return 0; cat "$EVD/$1"; }
has() { [ "$MODE" = dry ] && return 0; grep -qF -- "$2" "$EVD/$1"; }
undo() { [ "$MODE" = dry ] && { echo "  [dry] undo += $*"; return 0; }; echo "$(date -u +%FT%TZ) $*" >> "$EVD/UNDO.txt" || stop "UNDO.txt: not written"; UNDOS+=("$*"); }
undone() { UNDOS=(); }   # FE ran every undo itself: an exit after it replays none
gstart() { echo "o=\$($RUNSET) || exit 4; if printf '%s\n' \"\$o\" | grep -qxF -- $1; then echo ALREADY-RUNNING $1; else $LH stack start $1 --yes; fi"; }
gstop() { echo "o=\$($RUNSET) || exit 4; if printf '%s\n' \"\$o\" | grep -qxF -- $1; then $LH stack stop $1 --yes; else echo ALREADY-STOPPED $1; fi"; }
page_ok() { [[ "$1" =~ ^[23][0-9][0-9]$ ]]; }   # a page answers 2xx or 3xx; 000 (no answer), 4xx and 5xx are not reachable
lan() { if [ "$MODE" = dry ]; then echo "  [dry] PC > $1: curl -sk https://$HOSTIP:$2/"; LANCODE=200; return 0; fi
  LANCODE=$(curl -sk --max-time 10 -o /dev/null -w '%{http_code}' "https://$HOSTIP:$2/" 2>/dev/null); echo "$LANCODE" > "$EVD/$1"; }   # 000 = not reachable
fwstate() { X "$1" "$LH firewall"; }
mtpre() {   # mtpre <file-tag> <rid>: BEFORE any start of meshtastic@<rid>: its max_power (meshtasticd's cap, read before its first
  # frame) <= min(17, the power_max_dbm of the rid's radio model), the rid's one radio row with no high-power grant; else STOP. MP = max_power
  must "$1-maxpower.txt" "$LH config meshtastic@$2 list"; MP=$(body "$1-maxpower.txt" | sed -nE 's/^ *max_power *= *([0-9]+).*/\1/p' | head -n 1)
  must "$1-radios.txt" "$PY -c '$RADIOS_PY'"; must "$1-caps.txt" "$PY -c '$CAPS_PY'"; [ "$MODE" = dry ] && return 0
  local o m c; case "$MP" in ''|*[!0-9]*) stop "$1: no max_power value";; esac; [ "$MP" -le 17 ] || stop "$1: max_power $MP above 17: no start"
  o=$(jgrant "$(body "$1-radios.txt")" "$2") || stop "$1: $o: no start"
  m=$(body "$1-radios.txt" | awk -v r="$2" '$1 == "ROW" && $2 == r {print $3}'); c=$(body "$1-caps.txt" | awk -v m="$m" '$1 == "CAP" && $2 == m {print $3}')
  [ -n "$m" ] && [[ "$c" =~ ^[0-9]{1,2}$ ]] || stop "$1: no single power cap of $2's model ('$m'): no start"
  [ "$c" -le 17 ] || c=17; [ "$MP" -le "$c" ] || stop "$1: max_power $MP above $c dBm (min(17, $m's power_max_dbm)): no start"; }
mtstart() {   # mtstart <file-tag>: meshtastic@r2 with the power guards (RF: normal power, <= 17): mtpre before, Final Tx power after
  mtpre "$1" r2; local mp=$MP
  must "$1-ftx0.txt" "echo SIZE \$($FSZ); $FTX"; local n0 n1 s0 s1 b; n0=$(body "$1-ftx0.txt" | tail -n 1); s0=$(body "$1-ftx0.txt" | sed -n 's/^SIZE //p')
  [ "$MODE" = dry ] || { [[ "$n0" =~ ^[0-9]+$ ]] && [[ "$s0" =~ ^[0-9]+$ ]]; } || stop "$1: no count or size of the start log before the start"
  undo "$(gstop meshtastic@r2)"; started meshtastic@r2; must "$1-start.txt" "$(gstart meshtastic@r2)"
  # the start may cap its log (lifecycle -> runtime_fs.cap_start_log: over 8 MiB it is truncated): a log smaller than before
  # the start is all this start's, so its lines count from 0
  must "$1-txpower.txt" "s=\$($FSZ); b=${n0:-0}; [ \"\$s\" -lt ${s0:-0} ] && b=0; echo SIZE \$s; for i in \$(seq 1 30); do [ \"\$($FTX)\" -gt \$b ] && break; sleep 2; done; echo COUNT \$($FTX); grep -a 'Final Tx power' $RTB/logs/start-meshtastic-868-r2.log | tail -n 1"
  s1=$(body "$1-txpower.txt" | sed -n 's/^SIZE //p'); b=${n0:-0}; [[ "$s1" =~ ^[0-9]+$ ]] && [ "$s1" -lt "${s0:-0}" ] && b=0
  n1=$(body "$1-txpower.txt" | sed -n 's/^COUNT //p')   # this start wrote a new line, or the last one is an older start's
  [ "$MODE" = dry ] || { [[ "$n1" =~ ^[0-9]+$ ]] && [ "$n1" -gt "$b" ]; } || stop "$1: no Final Tx power line of this start (count ${n1:-none}, ${b} before it)"
  local fp; fp=$(body "$1-txpower.txt" | sed -nE 's/.*Final Tx power: (-?[0-9]+) dBm.*/\1/p'); [ "$MODE" = dry ] || { case "$fp" in ''|*[!0-9-]*) stop "$1: no power value in this start's Final Tx power line";; esac; [ "$fp" -le 17 ] || stop "$1: Final Tx power $fp above 17"; }
  ok "$1: meshtastic@r2 runs, Final Tx power ${fp:-dry} dBm (max_power ${mp:-dry})"; }

r4refused() {   # FW6 (claim 1b): meshtastic@r4's start (the changed radio) is refused naming the apply: its power checked first
  # (mtpre), and the refusal counts only when a read of the running set shows it is not running (a start that half-ran is none)
  local r4rc; mtpre F6-r4 r4; started meshtastic@r4; X F6-r4.txt "$(gstart meshtastic@r4)"; r4rc=$XRC
  must F6-r4-runset.txt "$RUNSET"
  if [ "$MODE" = dry ] || { [ "$r4rc" != 0 ] && grep -qF "firewall-apply.sh" "$EVD/F6-r4.txt" && ! grep -qxF meshtastic@r4 "$EVD/F6-r4-runset.txt"; }; then
    report "1b | meshtastic@r4 (changed radio) refused, naming the apply, not running | PASS"; stopped meshtastic@r4
  else fail "FW6: meshtastic@r4 rc $r4rc: not refused naming firewall-apply.sh, or it runs"; report "1b | meshtastic@r4 refused naming the apply | FAIL"
    X F6-r4-stop.txt "$(gstop meshtastic@r4)"; [ "$XRC" != 0 ] || stopped meshtastic@r4; fi; }

endscan() {   # RE: the captures are redacted, so any occurrence of the password in the evidence is a leak
  pweq H && { ok "RE: box H password equals its login name (lab box): not scanned"; return 0; }
  local c; c=$(grep -rlF -- "$PW" "$EVD" | wc -l); [ "$c" = 0 ] || stop "RE: the box password is in $c evidence file(s): do not publish"; }

[ "${FWPROOF_SOURCE_ONLY:-0}" = 1 ] && return 0   # tests: the functions only, as a dry start; no prep, box or evidence
echo "== PREP (the PC; no box touched)"
[[ "$FWSHA" =~ ^[0-9a-f]{40}$ ]] || stop "FWSHA is not a 40-hex sha"; [[ "$FROM" =~ ^[0-9a-f]{40}$ ]] || stop "FWFROM is not a 40-hex sha"; [ -n "$FWCI" ] || stop "FWCI is not set"
[[ "$LABNAME" =~ ^[A-Za-z0-9-]{3,20}$ ]] && [[ "$LABSHORT" =~ ^[A-Za-z0-9]{1,4}$ ]] || stop "LABNAME/LABSHORT: a lab name (3-20 [A-Za-z0-9-]) and short name (1-4 [A-Za-z0-9])"
if [ "$MODE" = live ]; then [ ! -e "$EVD/state" ] || stop "$EVD was used before"; : > "$EVD/state"
  [ "$(cd "$REPO" && gh run view "$FWCI" --json headSha,conclusion -q '.headSha + " " + .conclusion')" = "$FWSHA success" ] || stop "FWCI is not a green run of FWSHA"; fi
git -C "$REPO" fetch -q origin "$REF" || stop "fetch $REF"; git -C "$REPO" merge-base --is-ancestor "$FWSHA" FETCH_HEAD || stop "FWSHA is not on $REF"
git -C "$REPO" merge-base --is-ancestor "$FROM" "$FWSHA" || { [ "$MODE" = dry ] && say "dry: FWSHA does not contain FROM (a live run STOPs here)" || stop "FWSHA does not contain $FROM (no fast-forward)"; }
moved=$(git -C "$REPO" diff --name-only "$FROM" "$FWSHA" -- pyproject.toml requirements.lock deploy/ | tr '\n' ' '); [ -z "$moved" ] || stop "FROM..FWSHA moves $moved: the hand fast-forward is not enough (needs the documented update path)"
for l in "${LITS[@]}"; do [ "$(git -C "$REPO" grep -c -F -- "$l" "$FWSHA" -- lhpc | awk -F: '{n+=$NF} END{print n+0}')" -gt 0 ] || stop "literal '$l' is not in FWSHA's lhpc source"; done
ok "prep: FWSHA on $REF, contains FROM, no dependency/unit move, ${#LITS[@]} literals present"

echo "== FW0 (read-only)"
must F0-state.txt "echo boot \$(cat /proc/sys/kernel/random/boot_id); echo head \$(git -C $CK rev-parse HEAD) branch \$(git -C $CK rev-parse --abbrev-ref HEAD) porcelain \$(git -C $CK status --porcelain | wc -l); test -d /var/log/journal && echo journal persistent"
[ "$MODE" = dry ] || grep -qx "head $FWSHA branch main porcelain 0" "$EVD/F0-state.txt" || stop "FW0: box H is not on clean main at FWSHA"
[ "$MODE" = dry ] || grep -qx 'journal persistent' "$EVD/F0-state.txt" || stop "FW0: journal not persistent"
BOOT0=$(body F0-state.txt | sed -n 's/^boot //p')
fwstate F0-fw.txt; [ "$MODE" = dry ] || grep -qE "Firewall: (Active|Changes pending|Transition cleanup pending)" "$EVD/F0-fw.txt" || stop "FW0: an unexpected firewall state"
must F0-ws.txt "$LH webserver status"; has F0-ws.txt "Remote listener active" || stop "FW0: the console is not remote"
[ -n "$LANCIDR" ] || LANCIDR=$(body F0-ws.txt | sed -nE 's/^ *allowed CIDRs: *([0-9./]+).*/\1/p' | head -n 1); [ "$MODE" = dry ] && LANCIDR=${LANCIDR:-192.0.2.0/24}
[[ "$LANCIDR" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}/[0-9]{1,2}$ ]] || stop "FW0: no single LAN CIDR (got '$LANCIDR')"
must F0-runset.txt "$RUNSET"; KEYS=$(body F0-runset.txt | grep -E '^[a-z0-9_-]+@r[0-9]+$' | tr '\n' ' ')
must F0-r4.txt "$PY -c '$ROWR4_PY'"; [ "$MODE" = dry ] || grep -qx 'ROW r4 pinedio-usb-1262 868 True' "$EVD/F0-r4.txt" || stop "FW0: r4 is not the enabled pinedio 868 row"
must F0-mtid.txt "$LH config meshtastic@r2 list"; [ "$MODE" = dry ] || { grep -qE 'node_name \* = \(empty\)' "$EVD/F0-mtid.txt" && grep -qE 'node_short \* = \(empty\)' "$EVD/F0-mtid.txt"; } || stop "FW0: meshtastic@r2 already has a node identity (the --reset undo would remove it)"
must F0-etc.txt "sha256sum /etc/lhpc/firewall.snapshot.json /etc/lhpc/firewall.candidate.json"
lan F0-lan.txt 8443; page_ok "$LANCODE" || stop "FW0: the console is not reachable from the LAN (code $LANCODE)"; ok "FW0: console remote, LAN code $LANCODE, running [$KEYS]"

echo "== FW1/FW2: the update to FWSHA is done before this runner (FW0 checked clean main at FWSHA)"

echo "== FW3 the base: r4 disabled, the running stacks stopped"
for k in $KEYS; do undo "$(gstart "$k")"; must "F3-stop-$k.txt" "$(gstop "$k")"; done
undo "$PY -c '$ENABLE_PY' r4 on"; must F3-r4off.txt "$PY -c '$ENABLE_PY' r4 off"; must F3-r4.txt "$PY -c '$ROWR4_PY'"
[ "$MODE" = dry ] || grep -qx 'ROW r4 pinedio-usb-1262 868 False' "$EVD/F3-r4.txt" || stop "FW3: r4 not disabled"

echo "== FW3a a lab node identity for meshtastic@r2 (a local node name, never a callsign)"
undo "$LH config meshtastic@r2 --reset --yes"
must F3a-name.txt "$LH config meshtastic@r2 node_name $(shq "$LABNAME") < /dev/null"; must F3a-short.txt "$LH config meshtastic@r2 node_short $(shq "$LABSHORT") < /dev/null"
must F3a-list.txt "$LH config meshtastic@r2 list"; [ "$MODE" = dry ] || { grep -qE "node_name \* = $LABNAME( |\$)" "$EVD/F3a-list.txt" && grep -qE "node_short \* = $LABSHORT( |\$)" "$EVD/F3a-list.txt"; } || stop "FW3a: the identity does not read back"

echo "== FW4 (claim 4b) one apply clears the notice"
fwapply F4
fwstate F4-fw.txt
if has F4-fw.txt "Firewall: Active" && ! { [ "$MODE" = live ] && grep -qF "re-apply the firewall before you reboot" "$EVD/F4-fw.txt"; }; then report "4b | one apply clears the notice (two-phase cleanup when transitional; Active) | PASS"; else fail "FW4: after the apply: not Active or the notice still shows"; report "4b | one apply clears the notice | FAIL"; fi

echo "== FW5 the radio change without an apply: r4 enabled"
must F5-r4on.txt "$PY -c '$ENABLE_PY' r4 on"; must F5-r4.txt "$PY -c '$ROWR4_PY'"; [ "$MODE" = dry ] || grep -qx 'ROW r4 pinedio-usb-1262 868 True' "$EVD/F5-r4.txt" || stop "FW5: r4 not enabled"
fwstate F5-fw.txt

echo "== FW6 (claim 1) the unchanged radio's Meshtastic starts; the changed radio's is refused naming the apply"
mtstart F6-r2; report "1a | meshtastic@r2 (unchanged radio) starts after the radio change, no apply | PASS"
r4refused
must F6-stop.txt "$(gstop meshtastic@r2)"; stopped meshtastic@r2

echo "== FW7 (claim 1) the console stays remote over a reboot (planned)"
[ "$MODE" = dry ] || echo "$(date -u +%FT%TZ) none: a planned reboot" >> "$EVD/UNDO.txt"; XIN F7-reboot.txt "$PW" "sudo -S -p '' systemd-run --on-active=3 systemctl reboot"; [ "$XRC" = 0 ] || stop "FW7: the reboot was not scheduled (rc $XRC)"; say "reboot scheduled (3 s)"
if [ "$MODE" = live ]; then sleep 60; for i in $(seq 1 60); do SSHPASS=$PW sshpass -e ssh -n -o PubkeyAuthentication=no -o ConnectTimeout=5 "$HH" true 2>/dev/null && break; sleep 5; done; fi
must F7-state.txt "echo boot \$(cat /proc/sys/kernel/random/boot_id)"; BOOT1=$(body F7-state.txt | sed -n 's/^boot //p')
if [ "$MODE" = live ]; then [ -n "$BOOT1" ] && [ "$BOOT1" != "$BOOT0" ] || stop "FW7: no new boot id after the reboot"; fi
must F7-ws.txt "for i in \$(seq 1 60); do $LH webserver status 2>&1 | grep -qF 'Remote listener active' && { $LH webserver status; exit 0; }; sleep 5; done; $LH webserver status; exit 1"
lan F7-lan.txt 8443; fwstate F7-fw.txt
if page_ok "$LANCODE" && has F7-ws.txt "Remote listener active"; then report "1c | the console stays remote over a reboot (LAN code $LANCODE) | PASS"; else fail "FW7: the console is not remote after the reboot (LAN $LANCODE)"; report "1c | console remote over a reboot | FAIL"; fi

echo "== FW8 (claim 2) one apply"
fwapply F8; fwstate F8-fw.txt
if has F8-fw.txt "Firewall: Active"; then report "2 | one apply (Active) | PASS"; else fail "FW8: not Active after the apply"; report "2 | one apply | FAIL"; fi

echo "== FW9 (claim 3) a page exposed from the console, reachable from the LAN, no sudo"
mtstart F9-r2
undo "$LH webserver proxy meshtastic@r2 --mode local < /dev/null && $LH webserver apply < /dev/null"
must F9-port.txt "$PY -c '$PORT_PY'"; PORT=$(body F9-port.txt | sed -n 's/^PORT //p'); [ "$MODE" = dry ] && PORT=8450
[[ "$PORT" =~ ^[0-9]{4,5}$ ]] && [ "$PORT" != 0 ] || stop "FW9: no allocated page port for meshtastic@r2 (the one the firewall pre-opened)"
must F9-proxy.txt "$LH webserver proxy meshtastic@r2 --mode lan --port $(shq "$PORT") --cidr $(shq "$LANCIDR") --confirm-phrase enable-remote < /dev/null"
must F9-wsapply.txt "$LH webserver apply < /dev/null"; must F9-ws.txt "$LH webserver status"
lan F9-lan.txt "$PORT"; fwstate F9-fw.txt
if page_ok "$LANCODE" && ! { [ "$MODE" = live ] && grep -qF "Firewall: Changes pending" "$EVD/F9-fw.txt"; }; then report "3 | meshtastic@r2's page exposed, LAN code $LANCODE on :$PORT, no sudo, no change pending | PASS"
else fail "FW9: the page on :$PORT gives LAN code $LANCODE (or a change is pending)"; report "3 | exposed page reachable from the LAN | FAIL"; fi

echo "== FE undo (this run's changes; the firewall stays at FWSHA's apply, the checkout at FWSHA)"
must FE-proxy.txt "$LH webserver proxy meshtastic@r2 --mode local < /dev/null && $LH webserver apply < /dev/null"
must FE-stop.txt "$(gstop meshtastic@r2)"; stopped meshtastic@r2
must FE-reset.txt "$LH config meshtastic@r2 --reset --yes < /dev/null"; must FE-mtid.txt "$LH config meshtastic@r2 list"
[ "$MODE" = dry ] || { grep -qE 'node_name \* = \(empty\)' "$EVD/FE-mtid.txt" && grep -qE 'node_short \* = \(empty\)' "$EVD/FE-mtid.txt"; } || fail "FE: meshtastic@r2's identity is not empty after the reset"
for k in $KEYS; do must "FE-start-$k.txt" "$(gstart "$k")"; done
must FE-runset.txt "$RUNSET"; [ "$MODE" = dry ] || [ "$(body FE-runset.txt | sort | tr '\n' ' ')" = "$(body F0-runset.txt | sort | tr '\n' ' ')" ] || fail "FE: the running set differs from FW0"
fwstate FE-fw.txt; must FE-ws.txt "$LH webserver status"; lan FE-lan.txt 8443; page_ok "$LANCODE" || fail "FE: the console is not reachable from the LAN after the undo (code $LANCODE)"; ok "FE: running set as at FW0, console LAN code $LANCODE"
undone

echo "== RE"
if [ "$MODE" = live ]; then
  endscan
  ( cd "$EVD" && find . -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 sha256sum > SHA256SUMS ); say "evidence $EVD, SHA256SUMS $(sha256sum "$EVD/SHA256SUMS" | cut -c1-8)"
  [ ! -s "$EVD/FAILED.txt" ] || { say "FAILED rows: $(tr '\n' '|' < "$EVD/FAILED.txt")"; exit 1; }
fi
echo "DONE ($MODE)"
