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
#  before any box contact). OPERATOR_CALL (lab file or env) is required, a callsign without an SSID.
# RF: FW6 and FW9 start meshtastic@r2, FW6 tries meshtastic@r4 (normal power), FE and the undo replay start FW0's running
#  units on the row radios (r2, r4) again: FW0 power-checks each before any change (pwrok: a Meshtastic max_power in 1..17,
#  so 0 = the firmware maximum STOPs; a daemon's STATUS POWER and HIGHPOWER=0; both <= min(17, the model cap); any other
#  stack there STOPs), and so does every restart (FE, the replay's PSTART) and, after FW7's reboot, every unit running on a
#  row radio. Before FW7's reboot (bootcheck), every unit boot restore may start on a row radio (an owned record with no
#  operator stop intent, running or not) is power-checked too, else STOP: no reboot (a daemon there that is not running has
#  no STATUS to read, so it STOPs). Units on other radios are never stopped or started (they stay as they are). BEFORE any start (mtpre): the radio's row (one,
#  no high-power grant), its model's power_default_dbm, and the stack's max_power (meshtasticd's cap before its first frame) <=
#  min(17, that cap), else STOP; after a start, its "Final Tx power" <= 17 (corroboration), else STOP. r4's expected refusal
#  counts only when a read of the running set shows it is not running.
# Box writes, each with its undo journalled BEFORE it: r4 enabled off/on (undo: on); the row radios' running units stopped
#  (undo: PSTART, a start after its power check); the lab identity (undo: --reset); the proxy of meshtastic@r2 (undo: --mode local + webserver apply); the firewall
#  applies (root, /etc/lhpc + nft; not undone: the state at FWSHA; a return to FWFROM's rules = reset the checkout, then the
#  same apply sequence); one planned reboot (FW7). Any live exit before FE's end (a STOP, a signal) replays the journalled
#  undos last first, after the safety stop (each checked; signals held off); one that fails is named UNRESOLVED in STOP.txt.
#  FE's own restore is checked (identity, running set, console); a check that does not hold is a STOP, so the replay runs.
#  Limit: each recovery command has ssh's 10 s connect timeout but no overall deadline (a remote command that hangs holds
#  the recovery; the operator's kill -9 ends it and STOP.txt then lacks its UNRESOLVED line).
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
# LAN_CERT (lab file, optional): a PEM file with a client cert + its unencrypted key, for the console's remote-auth (a LAN request without a
# client cert gets 403 by design); a plain absolute path (or ~/), a readable regular file of mode 600 (a symlink reads 777 here:
# stat does not follow it); the kit reads no passphrase
LANCERT=; if [ -n "${LAN_CERT:-}" ]; then LANCERT=${LAN_CERT/#\~/$HOME}
  [[ "$LANCERT" == /* ]] && [ -f "$LANCERT" ] && [ -r "$LANCERT" ] && [ "$(stat -c %a "$LANCERT")" = 600 ] \
    || stop "LAN_CERT: $(printf %q "$LAN_CERT") is not a readable regular file of mode 600 at an absolute path"; fi
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
#  the call itself is required, of rf-proof.sh's form (its own check does not run when sourced): no call, no comparison and no mask
[[ "$OPERATOR_CALL" =~ ^[A-Z0-9]{3,8}$ ]] || { echo "STOP: OPERATOR_CALL is not a callsign without an SSID (3..8 of A-Z 0-9)"; exit 2; }
callbase() { local c=${1^^}; printf '%s' "${c%%-*}"; }
for k in LABNAME LABSHORT; do [ "$(callbase "${!k}")" != "$(callbase "$OPERATOR_CALL")" ] \
  || { echo "STOP: $k is the operator's call (the lab identity is never a callsign)"; exit 2; }; done
RUNSET_PY='from lhpc.core import instances; from lhpc.core.services import ControllerService; s = ControllerService(); [print(k) for st in s.stacks() for k in instances.running_keys(s, st.id)]'
RUNSET="$PY -c '$RUNSET_PY'"
ENABLE_PY='import sys; from lhpc.core import radio_start; from lhpc.core.services import ControllerService; ok, msg = radio_start.set_enabled(ControllerService(), sys.argv[1], sys.argv[2] == "on"); print(("OK " if ok else "REFUSED ") + msg); sys.exit(0 if ok else 3)'
# the units lhpc-boot-restore.service may start at the next boot, as its classification (service_boot_restore
#  _classify_boot_evidence): an owned record of a stack (role ""), with a radio, minus the operator stop intents; read
#  before FW7's reboot, so every record is the next boot's evidence; unreadable intents subtract none (fail closed)
BOOTSET_PY='from lhpc.core import boot_restore as b; from lhpc.core.services import ControllerService; s = ControllerService(); v = s._lifecycle().owned_inventory()[0]; n = s._stop_intent_stacks() or set(); [print("BOOT", k) for k in sorted({b.instance_key(r.get("stack", ""), b.rid_of(r.get("rid"))) for r in v if r.get("role", "") == ""} - n)]'
ROWR4_PY='from lhpc.core import paths, radios; [print("ROW", r.id, r.model, r.band, r.enabled) for r in radios.load(paths.resolve_paths()).radios if r.id == "r4"]'
# the apply: the root steps read the password ONCE (sudo -S -v on the only stdin line), then sudo -n; lhpc reads no stdin
APPLY="cd $RTB && ./venv/lhpc/bin/lhpc firewall --script > /dev/null && sudo -S -p '' -v && sudo -n bash $RTB/config/files/firewall/firewall-apply.sh < /dev/null && sudo -n systemctl start lhpc-firewall-check.service < /dev/null && ./venv/lhpc/bin/lhpc webserver apply < /dev/null"
PORT_PY='from lhpc.core.services import ControllerService; print("PORT", ControllerService()._page_ports().get("meshtastic@r2", 0))'
FTX="if test -e $RTB/logs/start-meshtastic-868-r2.log; then grep -ac 'Final Tx power' $RTB/logs/start-meshtastic-868-r2.log; [ \$? -le 1 ]; else echo 0; fi"   # no log yet (a first start): 0; grep rc 2 (unreadable): no number, rc 1 (STOP)
FSZ="if test -e $RTB/logs/start-meshtastic-868-r2.log; then stat -c %s $RTB/logs/start-meshtastic-868-r2.log; else echo 0; fi"   # the start log's size: a shrink = the product capped it
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
onexit() { local rc=$? k i u; trap - EXIT; trap '' INT TERM HUP; [ "$MODE" = live ] || exit "$rc"   # the recovery runs to its end
  INEXIT=1   # X no longer STOPs on an ssh transport error: the recovery runs every step, each checked
  for k in "${STARTED[@]}"; do
    for i in 1 2; do capture "SAFESTOP-$k-$i.txt" sshx n "$(gstop "$k")"; [ "$XRC" = 0 ] && { say "safety stop: $k stopped"; continue 2; }; done
    say "UNSAFE: $k may still be running, TX power unverified"; echo "UNSAFE: $k may still be running, TX power unverified" >> "$EVD/STOP.txt"; rc=3
  done
  [ "${#UNDOS[@]}" = 0 ] || say "undo: replaying ${#UNDOS[@]} journalled change(s), last first"
  for ((i = ${#UNDOS[@]} - 1; i >= 0; i--)); do u=${UNDOS[$i]}
    # PSTART <unit>: the restart of a unit FW3 stopped, only after its power check (pwrok, reads only); a failed check leaves it
    #  stopped and UNRESOLVED (fail closed)
    if [[ $u == "PSTART "* ]]; then pstart "UNDO-$i" "${u#PSTART }"; else capture "UNDO-$i.txt" sshx n "$u"; fi
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
  capture "$f" sshx n "$*"; [ "$XRC" != 255 ] || [ -n "${INEXIT:-}" ] || stop "$f: ssh transport rc 255"; }
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
lan() { local cert=(); [ -z "${LANCERT:-}" ] || cert=(--cert "$LANCERT")   # the client cert (LAN_CERT) only when set
  if [ "$MODE" = dry ]; then echo "  [dry] PC > $1: curl -sk ${cert[*]:+${cert[*]} }https://$HOSTIP:$2/"; LANCODE=200; return 0; fi
  LANCODE=$(curl -sk "${cert[@]}" --max-time 10 -o /dev/null -w '%{http_code}' "https://$HOSTIP:$2/" 2>/dev/null); echo "$LANCODE" > "$EVD/$1"; }   # 000 = not reachable
fwstate() { X "$1" "$LH firewall"; }
ROWRID='^r[24]$'   # the radios this proof's rows use: r2 (meshtastic@r2 starts) and r4 (FW3 off, FW5 on, FW6's try)
pwrok() {   # pwrok <tag> <key>: rc 0 when the unit's power reads within min(17, its radio model's cap) and its radio has one row with no
  # high-power grant; else WHY = the reason. Reads only and never STOPs (the recovery uses it too). The cap table is CAPS_PY's
  # (radio_models power_default_dbm), the one mtpre always read. Meshtastic: max_power (meshtasticd's cap before its first frame) in
  # pdom's mt-power domain 1..17 (0 is no cap: LHPC's own domain starts at 1, manifest.example.toml max_power min = 1, and
  # Meshtastic reads a 0 power as "the maximum"; no max_power line, meshtasticd's default 20, is no value). A daemon: its STATUS
  # (jstatus: one POWER in pdom, HIGHPOWER=0). Any other stack: no check here, so no start. MP = the value read.
  # pwrok <tag> <key> cold: a stopped daemon's restart (FE, the replay), which has no STATUS: the POWER it starts with (DCFG_PY:
  # the product's post_power, its high-power grant off) must equal its FW0 STATUS POWER (P0) and lie within the cap; pstart
  # checks the STATUS again once it runs.
  local st=${2%@*} r=${2#*@} o m c w; WHY= MP=
  case $st in meshtastic|daemon) ;; *) WHY="no power check for a $st stack"; return 1;; esac
  X "$1-radios.txt" "$PY -c '$RADIOS_PY'"; [ "$XRC" = 0 ] || { WHY="the radio rows were not read (rc $XRC)"; return 1; }
  X "$1-caps.txt" "$PY -c '$CAPS_PY'"; [ "$XRC" = 0 ] || { WHY="the model caps were not read (rc $XRC)"; return 1; }
  if [ "$st" = meshtastic ]; then X "$1-maxpower.txt" "$LH config $2 list"
  elif [ "${3:-}" = cold ]; then X "$1-dcfg.txt" "$PY -c '$DCFG_PY' $r"
  else X "$1-status.txt" "$PY -c '$STATUS_PY' $r"; fi
  [ "$XRC" = 0 ] || { WHY="the power was not read (rc $XRC)"; return 1; }; [ "$MODE" = dry ] && return 0
  if [ "$st" = meshtastic ]; then w=max_power; MP=$(body "$1-maxpower.txt" | sed -nE 's/^ *max_power *= *([0-9]+).*/\1/p' | head -n 1)
    case "$MP" in ''|*[!0-9]*) WHY="no max_power value"; return 1;; esac; [ "$MP" -le 17 ] || { WHY="max_power $MP above 17"; return 1; }
    pdom "$MP" mt-power || { WHY="max_power $MP is outside 1..17 (0 = the firmware maximum, no cap)"; return 1; }
  elif [ "${3:-}" = cold ]; then w="start POWER"; MP=$(body "$1-dcfg.txt" | sed -n 's/^POWER //p')
    [[ "$MP" =~ ^[0-9]{1,2}$ ]] && [ "$(body "$1-dcfg.txt" | sed -n 's/^HP //p')" = False ] || { WHY="no start POWER, or a high-power grant ($1-dcfg.txt)"; MP=; return 1; }
    [ "$MP" = "${P0[$2]:-}" ] || { WHY="start POWER $MP differs from its FW0 STATUS POWER '${P0[$2]:-}'"; return 1; }
  else w=POWER; MP=$(jstatus "$(body "$1-status.txt" | head -n 1)") || { WHY=$MP; MP=; return 1; }; fi
  o=$(jgrant "$(body "$1-radios.txt")" "$r") || { WHY=$o; return 1; }
  m=$(body "$1-radios.txt" | awk -v r="$r" '$1 == "ROW" && $2 == r {print $3}'); c=$(body "$1-caps.txt" | awk -v m="$m" '$1 == "CAP" && $2 == m {print $3}')
  [ -n "$m" ] && [[ "$c" =~ ^[0-9]{1,2}$ ]] || { WHY="no single power cap of $r's model ('$m')"; return 1; }
  [ "$c" -le 17 ] || c=17; [ "$MP" -le "$c" ] || { WHY="$w $MP above $c dBm (min(17, $m's power_default_dbm))"; return 1; }; }
mtpre() { pwrok "$1" "meshtastic@$2" || stop "$1: $WHY: no start"; }   # mtpre <file-tag> <rid>: BEFORE any start of meshtastic@<rid>
declare -A P0=()   # a daemon's POWER at FW0 (runset0), for its restart
DCFG_PY='import sys; from lhpc.core import radio_start; from lhpc.core.services import ControllerService; s = ControllerService(); r = sys.argv[1]; print("POWER", radio_start.post_power(s, "daemon", r) or "-"); print("HP", radio_start.effective_high_power(s, r))'
pstart() {   # pstart <tag> <key>: the restart of a unit FW3 stopped (FE, the replay): its power checked (a daemon: cold, then its
  # STATUS once it runs; one that does not hold is stopped again); rc 0 = it runs, checked; else WHY, XRC
  pwrok "$1" "$2" cold || { XRC="power check: $WHY"; return 1; }; X "$1-start.txt" "$(gstart "$2")"; [ "$XRC" = 0 ] || { WHY="start rc $XRC"; return 1; }
  [ "${2%@*}" = daemon ] || return 0; pwrok "$1-after" "$2" && return 0
  local why=$WHY; X "$1-stop.txt" "$(gstop "$2")"; WHY="$why after its start (stopped again: rc $XRC)"; XRC="power check: $WHY"; return 1; }
runset0() {   # FW0 (read-only): the running set (KEYS) and, of it, the units on the row radios (TOUCH): FW3 stops those and FE (or a
  # STOP's replay) starts them again, so each needs its power check now, before any change: one that fails STOPs here, no undo
  # journalled. A unit on another radio is never stopped or started by this proof (it stays as it is: no check, no STOP).
  local k; must F0-runset.txt "$RUNSET"; KEYS=$(body F0-runset.txt | grep -E '^[a-z0-9_-]+@r[0-9]+$' | tr '\n' ' '); TOUCH=
  for k in $KEYS; do [[ ${k#*@} =~ $ROWRID ]] || continue; TOUCH+="$k "; pwrok "F0-$k" "$k" || stop "F0-$k: $WHY: no change"
    [ "${k%@*}" != daemon ] || P0[$k]=$MP; done; }
rowcheck() {   # rowcheck <tag>: after FW7's reboot (FW5 changed r4's config): every unit running on a row radio is power-checked
  # before the next step; each is safety-stopped on any STOP (started), FE's end releases those it restored
  local k; must "$1-runset.txt" "$RUNSET"
  for k in $(body "$1-runset.txt" | grep -E '^[a-z0-9_-]+@r[0-9]+$'); do [[ ${k#*@} =~ $ROWRID ]] || continue
    started "$k"; pwrok "$1-$k" "$k" || stop "$1-$k: $WHY: it runs after the reboot"; done; }
bootcheck() {   # bootcheck <tag>: BEFORE FW7 schedules the reboot (G2-HWKITC-E B1): every unit boot restore may start on a
  # row radio (BOOTSET_PY: running or not) is power-checked now, as each restart is (pwrok: a Meshtastic's stored max_power; a
  # daemon's STATUS, so a daemon there that is not running STOPs: its start power has no one stored value); a failed check
  # STOPs before the reboot. A unit on another radio is never checked (this proof leaves those as they are).
  local k; must "$1-bootset.txt" "$PY -c '$BOOTSET_PY'"
  for k in $(body "$1-bootset.txt" | sed -n 's/^BOOT //p' | grep -E '^[a-z0-9_-]+@r[0-9]+$'); do [[ ${k#*@} =~ $ROWRID ]] || continue
    pwrok "$1-$k" "$k" || stop "$1-$k: $WHY: boot restore may start it at the reboot: no reboot"; done; }
fw7reboot() {   # FW7's reboot, scheduled only after bootcheck held
  bootcheck F7-pre
  [ "$MODE" = dry ] || echo "$(date -u +%FT%TZ) none: a planned reboot" >> "$EVD/UNDO.txt"; XIN F7-reboot.txt "$PW" "sudo -S -p '' systemd-run --on-active=3 systemctl reboot"; [ "$XRC" = 0 ] || stop "FW7: the reboot was not scheduled (rc $XRC)"; say "reboot scheduled (3 s)"; }
fe() {   # FE: this run's changes undone, each restore checked; one that did not hold is a STOP, so the exit replays the journal
  # (every undo idempotent); the list clears (undone) only after every check held
  must FE-proxy.txt "$LH webserver proxy meshtastic@r2 --mode local < /dev/null && $LH webserver apply < /dev/null"
  must FE-stop.txt "$(gstop meshtastic@r2)"; stopped meshtastic@r2
  must FE-reset.txt "$LH config meshtastic@r2 --reset --yes < /dev/null"; must FE-mtid.txt "$LH config meshtastic@r2 list"
  [ "$MODE" = dry ] || { grep -qE 'node_name \* = \(empty\)' "$EVD/FE-mtid.txt" && grep -qE 'node_short \* = \(empty\)' "$EVD/FE-mtid.txt"; } || stop "FE: meshtastic@r2's identity is not empty after the reset"
  local k; for k in $TOUCH; do pstart "FE-$k" "$k" || stop "FE-$k: $WHY: not restarted"; done
  must FE-runset.txt "$RUNSET"; [ "$MODE" = dry ] || for k in $TOUCH; do grep -qxF -- "$k" "$EVD/FE-runset.txt" || stop "FE: $k does not run again (the running set differs from FW0)"; done
  [ "$MODE" = dry ] || [ "$(body FE-runset.txt | sort | tr '\n' ' ')" = "$(body F0-runset.txt | sort | tr '\n' ' ')" ] || say "note: FE's running set differs from FW0 off the row radios (units this proof never touched; the reboot's resting state)"
  fwstate FE-fw.txt; must FE-ws.txt "$LH webserver status"; lan FE-lan.txt 8443; page_ok "$LANCODE" || stop "FE: the console is not reachable from the LAN after the undo (code $LANCODE)"
  for k in $TOUCH; do stopped "$k"; done; ok "FE: the row radios' units as at FW0, console LAN code $LANCODE"; undone; }
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

rend() {   # RE's end (live): the scan, then the manifest as the last write: the lines after it are plain echo (say appends to
  # run.log, a file it lists); rc 1 when a row FAILED
  endscan
  ( cd "$EVD" && find . -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 sha256sum > SHA256SUMS ) || stop "RE: SHA256SUMS not written"
  echo "$(date -u +%FT%TZ) evidence $EVD, SHA256SUMS $(sha256sum "$EVD/SHA256SUMS" | cut -c1-8)"
  [ ! -s "$EVD/FAILED.txt" ] || { echo "$(date -u +%FT%TZ) FAILED rows: $(tr '\n' '|' < "$EVD/FAILED.txt")"; return 1; }; }

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
runset0
must F0-r4.txt "$PY -c '$ROWR4_PY'"; [ "$MODE" = dry ] || grep -qx 'ROW r4 pinedio-usb-1262 868 True' "$EVD/F0-r4.txt" || stop "FW0: r4 is not the enabled pinedio 868 row"
must F0-mtid.txt "$LH config meshtastic@r2 list"; [ "$MODE" = dry ] || { grep -qE 'node_name \* = \(empty\)' "$EVD/F0-mtid.txt" && grep -qE 'node_short \* = \(empty\)' "$EVD/F0-mtid.txt"; } || stop "FW0: meshtastic@r2 already has a node identity (the --reset undo would remove it)"
must F0-etc.txt "sha256sum /etc/lhpc/firewall.snapshot.json /etc/lhpc/firewall.candidate.json"
lan F0-lan.txt 8443; page_ok "$LANCODE" || stop "FW0: the console is not reachable from the LAN (code $LANCODE)"; ok "FW0: console remote, LAN code $LANCODE, running [$KEYS]"

echo "== FW1/FW2: the update to FWSHA is done before this runner (FW0 checked clean main at FWSHA)"

echo "== FW3 the base: r4 disabled, the row radios' running units stopped"
for k in $TOUCH; do undo "PSTART $k"; must "F3-stop-$k.txt" "$(gstop "$k")"; done
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
fw7reboot
if [ "$MODE" = live ]; then sleep 60; for i in $(seq 1 60); do SSHPASS=$PW sshpass -e ssh -n -o PubkeyAuthentication=no -o ConnectTimeout=5 "$HH" true 2>/dev/null && break; sleep 5; done; fi
must F7-state.txt "echo boot \$(cat /proc/sys/kernel/random/boot_id)"; BOOT1=$(body F7-state.txt | sed -n 's/^boot //p')
if [ "$MODE" = live ]; then [ -n "$BOOT1" ] && [ "$BOOT1" != "$BOOT0" ] || stop "FW7: no new boot id after the reboot"; fi
must F7-ws.txt "for i in \$(seq 1 60); do $LH webserver status 2>&1 | grep -qF 'Remote listener active' && { $LH webserver status; exit 0; }; sleep 5; done; $LH webserver status; exit 1"
rowcheck F7; lan F7-lan.txt 8443; fwstate F7-fw.txt
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
fe

echo "== RE"
[ "$MODE" = live ] && { rend || exit 1; }
echo "DONE ($MODE)"
