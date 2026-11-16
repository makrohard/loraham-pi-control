#!/bin/bash
# rf-proof.sh dry|live: the hardware test line. Every radio that is present sends and receives a nonce frame through the
# daemon (RF fast), and every stack goes on air with its real peer (RF full). It reads the lab from lab.local.env (the dry
# run: lab.example.env with its simulated bench) and the box passwords (and the MeshCore PIN) from stdin.
#
# RUN:    LANE=fast|full bash rf-proof.sh dry                       (first; no box, no peer, no TX)
#         printf '%s\n' "$PW_H" "$PW_Z" ["$PIN"] | LANE=... INTHEAD=... INTCI=... FORMS_OK=... FORMS_HEAD=... \
#           bash rf-proof.sh live 2>&1 | tee <log under /home>
# A live run needs the transmit and lab permission and the GO for that run, with the hardware connected and set up.
#
# INPUTS (env): LANE (fast|full, default fast); ROWS_ONLY (row ids, overrides the lane); LAB_ENV (the lab file); INTHEAD (the
#  Zero's head, 40 hex), INTHEAD_H (box H's head, default INTHEAD), INTCI (a green CI run of INTHEAD); FORMS_OK (the confirmed
#  form names at FORMS_HEAD = INTHEAD); TRACKER_CALL (row 2: the tracker's call-SSID, on stdin to the box only); TRACKER_DBM
#  (row 2: the tracker's power from its portal); NONCE_PREFIX (default rfkit). Dry takes placeholders for INTHEAD/INTCI.
# STDIN (live only, read once before R0, then closed): line 1 box H's password, line 2 the Zero's; an EMPTY line = that box is
#  ABSENT (its rows NOT RUN, no ssh). Line 3 = the MeshCore PIN, only when row 4 runs (empty = the stored bond).
#
# SAFETY (every check below is a STOP, never NOT RUN; absent hardware is NOT RUN, never PASS):
#  - ONE dispatcher `tx` for every send. It checks the command (no --high-power, no power >= 18), the radio's power read in
#    this row (a box radio <= min(17, its model's power_max_dbm) with HIGHPOWER=0 and no high-power grant; a peer low or one
#    of the as-is peers), the message (the row's nonce, <= 16 characters), the budget (TXMAX frames per physical radio per
#    run, reserved at the worst case), both boxes' boot ids and the USB lists; then it runs the send itself.
#  - R0 sums the worst cases of the chosen rows per physical radio; a sum above TXMAX is a STOP before any TX.
#  - Every change pushes its undo first; a row FAIL runs its undos and the run goes on; a STOP or an interrupt runs all.
#  - The USB guard before and after each row; one peer at a time under the peer lock (two peers on one hub never transmit
#    at once); the RF logs' TX count per radio at most 15 after the run (RS).
#  - Every capture goes through the one redactor before it is written (the passwords, the PIN, the operator's call with any
#    SSID); the RX judges write marker lines only; the end scan (RE) checks the evidence again.
# EXIT: 0 = every run row PASS or NOT RUN; 1 = a FAIL; 2 = a STOP. Dry exits 0 or 2.
set -uo pipefail
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd) || exit 2; REPO=$(cd "$HERE/../.." && pwd) || exit 2
TXMAX=10; STICK_MIN=2; WEBPORT=8443; NPREFIX=${NONCE_PREFIX:-rfkit}
PEER=${PEER:-$HERE/peer.py}; FAKE_PEER=${FAKE_PEER:-$HERE/fake_peer.py}; VMT=${VMT:-python3}; VRNS=${VRNS:-python3}; LOCK=${LOCK:-${TMPDIR:-/tmp}/rf-proof-peer.lock}
# ---- 1. the ONE row table (the table order is the run order) ---------------------------------------------------------------
# id lane kind  endpoint A    endpoint B    power        flags
ROWS=(
  "7  fast frame H:H433        H:HSTK        min,min      same-box"
  "8  fast frame H:HMS         Z:Z868        min,as-is    -"
  "9  fast frame H:HPINE       Z:Z868        min,as-is    -"
  "10 fast frame H:H433        Z:Z433        as-is,as-is  -"
  "11 fast frame H:H868        Z:Z868        as-is,as-is  -"
  "1  full stack H:H433        peer:tdeck    as-is,-      -"
  "2  full stack H:H433        peer:tracker  as-is,as-is  -"
  "3  full stack Z:Z868        peer:g2       as-is,low    -"
  "4  full stack Z:Z868        peer:tdpro    as-is,read   -"
  "5  full stack Z:Z868        peer:rnode    as-is,read   -"
  "6  full stack H:H433        Z:Z433        as-is,as-is  -"
)
# The worst-case frames of a row per endpoint ("A B"): frame rows one each way; row 1 three console attempts; row 5 the box's
# announce + delivery proof + own send, the RNode's announce + message + path request.
declare -A WORST=([1]="3 0" [2]="1 1" [3]="1 1" [4]="1 1" [5]="3 3" [6]="1 1")
declare -A RROW=(); RIDS=
for l in "${ROWS[@]}"; do read -r id lane kind a b pw fl <<< "$l"; RROW[$id]="$l"; RIDS="$RIDS $id"; done
rowf() { local id lane kind a b pw fl; read -r id lane kind a b pw fl <<< "${RROW[$1]}"; case $2 in lane) echo "$lane";; kind) echo "$kind";;
  a) echo "$a";; b) echo "$b";; power) echo "$pw";; flags) echo "$fl";; esac; }
rowname() { case $1 in 1) echo meshcom;; 2) echo "kiss + graywolf";; 3) echo meshtastic;; 4) echo meshcore;; 5) echo "reticulum + meshchat";; 6) echo chat;; *) echo daemon;; esac; }
dirs() { if [ "$(rowf "$1" kind)" = frame ]; then echo "A->B B->A"; [ "$(rowf "$1" flags)" = same-box ] && echo stop-isolation; else echo "box->peer peer->box"; fi; }
dirtext() { case $1 in box-\>peer) echo "box -> peer";; peer-\>box) echo "peer -> box";; *) echo "$1";; esac; }

# ---- 3. FORMS: the box-side commands ({RT} = the box's runtime path, {LH} = its lhpc) ---------------------------------------
declare -A F=(
  [start]='{LH} stack start {inst} --yes'
  [stop]='{LH} stack stop {inst} --yes'
  [dset]='{LH} daemon {rid} --set POWER={p} --yes'
  [meshcom_send]="python3 -c \"import socket,sys; s=socket.create_connection(('127.0.0.1',{port}),5); s.sendall(('::'+sys.argv[1]+'\\\\r\\\\n').encode()); s.close()\" '{n}'"
  [mt_send]='{LH} meshtastic@{rid} --sendtext "{n}" --ack'
  [mt_pget]='{LH} meshtastic@{rid} --get lora.tx_power'
  [mc_send]="{RT}/src/meshcore-cli/.venv/bin/meshcore-cli -t 127.0.0.1 -p {cport} public '{n}'"
  [mc_pget]="{RT}/src/meshcore-cli/.venv/bin/meshcore-cli -t 127.0.0.1 -p {cport} get tx"
  [mt_cap]="{LH} config meshtastic@{rid} max_power | sed -n 's/^ *value *= *//p'"
  [rt_get]="{LH} config reticulum@{rid} {k} | sed -n 's/^ *value *= *//p'"
  [rt_pget]="{LH} config reticulum@{rid} txpower | sed -n 's/^ *value *= *//p'"
  [rt_set]='{LH} config reticulum@{rid} {k} {v}'
  [rt_send]='{LH} meshchat@{rid} send {dest} "{n}"'
  [chat_client]='{LH} stack start chat@{rid} --yes'
  [tx_text]="python3 -c \"import os,socket,struct,sys; p='/run/loraham/loraf-{rid}.sock'; p=p if os.path.exists(p) else '/tmp/loraf-{rid}.sock'; s=socket.socket(socket.AF_UNIX); s.connect(p); b=sys.argv[1].encode(); s.sendall(bytes([2])+struct.pack('<H',len(b))+b); s.close()\" '{n}'"
  [rx_rf]="grep -hF -- '{n}' {RT}/logs/{rid}/rf-*.log 2>/dev/null | grep -F ' RX '"
  [rx_mt]="{LH} rflog meshtastic@{rid} --decrypt 2>/dev/null | grep -F ' RX ' | grep -F -- '{n}'"
  [rx_mc]="{LH} rflog meshcore@{rid} --decrypt 2>/dev/null | grep -F ' RX ' | grep -F -- '{n}'"
  [rx_mchat]="grep -rlF -- '{n}' {RT}/state/meshchat 2>/dev/null"
)
CHAT_READY='Ctrl-K: Konf'   # the chat client's footer
# The forms each row needs (rt_send is optional: row 5 box -> peer is NOT RUN without it).
declare -A RFORMS=([1]="start stop" [2]="start stop rx_rf" [3]="start stop mt_cap mt_send mt_pget rx_mt" [4]="start stop mc_send mc_pget rx_mc"
  [5]="start stop rt_get rt_pget rt_set rx_mchat" [6]="start stop chat_client rx_rf")
rforms() { if [ "$(rowf "$1" kind)" = frame ]; then echo "start stop tx_text rx_rf$(case "$(rowf "$1" power)" in *min*) echo " dset";; esac)"; else echo "${RFORMS[$1]}"; fi; }
# ---- 4. the box-side helpers (python, run on the box) ----------------------------------------------------------------------
STATUS_PY='import socket,sys,os; r=sys.argv[1]; p=next((d+"/loraconf-"+r+".sock" for d in ("/run/loraham","/tmp") if os.path.exists(d+"/loraconf-"+r+".sock")),None); assert p, "no conf socket of "+r; s=socket.socket(socket.AF_UNIX); s.settimeout(30); s.connect(p); s.sendall(b"GET STATUS\n"); b=b""
while not b.endswith(b"\n") and len(b) < 4096:
 c=s.recv(4096 - len(b)); assert c, "EOF before the end of the STATUS line"; b+=c
print(b.decode().strip())'
CPORT_PY='import sys; from lhpc.core import instances; from lhpc.core.services import ControllerService; s = ControllerService(); sid, inst, _b, why = instances.resolve(s, sys.argv[1]); print("WHY", why or "-"); [print("CPORT", e.address.rsplit(":", 1)[1]) for c in (s.stack(sid).components if inst is not None else ()) for t, e in zip(c.endpoints, instances.endpoints_for(c.endpoints, inst)) if t.kind == "tcp" and t.address.endswith(":5000")]; life = s._lifecycle(); [print("OWNED", r.get("pgid")) for r in (life.owned_records("meshcore-node", rid=inst.rid) if inst is not None else []) if life.verify_owned(r)[0]]'
RADIOS_PY='from lhpc.core import paths, radios; s = radios.load(paths.resolve_paths()); print("STATE", s.kind); [print("ROW", r.id, r.model, r.band, r.enabled, "serial=" + r.usb_serial, "port=" + r.usb_port, "hp=" + str(r.high_power)) for r in s.radios]'
CAPS_PY='import sys; sys.argv[1:] and sys.path.insert(0, sys.argv[1]); from lhpc.core import radio_models; [print("CAP", m.id, m.power_max_dbm) for m in radio_models.load().values()]'
RUNSET_PY='from lhpc.core import instances; from lhpc.core.services import ControllerService; s = ControllerService(); [print(k) for st in s.stacks() for k in instances.running_keys(s, st.id)]'
RUNSET="cd {RT} && ./venv/lhpc/bin/python -c '$RUNSET_PY'"
USBLIST='for d in /sys/bus/usb/devices/[0-9]*-*; do case $d in *:*) continue;; esac; echo "${d##*/} $(cat $d/idVendor 2>/dev/null):$(cat $d/idProduct 2>/dev/null) dev=$(cat $d/devnum 2>/dev/null) ser=$(cat $d/serial 2>/dev/null)"; done'
USBEV='USB disconnect|new (full|high|low|super)-speed USB|reset (full|high|low|super)-speed USB|over-current'
rport() { local n=${2#r}; case "$n" in ''|*[!0-9]*) stop "rport: rid '$2'";; esac; echo $(( $1 + 133 * (n - 1) )); }   # a listed instance's port
GW_PY='import http.cookiejar, json, os, sys, time, urllib.request
BASE = "http://127.0.0.1:" + sys.argv[2]   # argv = nonce port rid; TO (the call) is the first stdin line
pw = open(os.path.expanduser("{RT}/state/" + sys.argv[3] + "/graywolf/graywolf-admin.txt")).read().strip()
op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
def call(m, p, b=None):
    r = op.open(urllib.request.Request(BASE + p, method=m, headers={"Content-Type": "application/json"}, data=json.dumps(b).encode() if b is not None else None), timeout=15); raw = r.read(); return json.loads(raw) if raw else None
call("POST", "/api/auth/login", {"username": "admin", "password": pw})
ch = call("GET", "/api/channels"); ch = ch if isinstance(ch, list) else ch.get("channels", ch)
t0 = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(time.time() - 2))
r = call("POST", "/api/messages", {"to": TO, "text": sys.argv[1], "channel": ch[0]["id"]}); mid = (r or {}).get("id")
assert mid is not None, "no message id"; print("SENT", sys.argv[1], time.strftime("%H:%M:%SZ", time.gmtime()))
import re, sqlite3                                       # the Graywolf record (messages.ack_state), read-only
RT = os.path.expanduser("{RT}"); db = RT + "/state/" + sys.argv[3] + "/graywolf/graywolf.db"
for _ in range(24):
    time.sleep(5)
    try:
        c = sqlite3.connect("file:" + db + "?mode=ro", uri=True, timeout=5)
        row = c.execute("select ack_state, msg_id from messages where direction = ? and (instr(text, ?) > 0 or instr(raw_tnc2, ?) > 0) order by id desc limit 1", ("out", sys.argv[1], sys.argv[1])).fetchone(); c.close()
    except sqlite3.Error as e:
        print("DB-READ", type(e).__name__); continue
    if row and row[0] == "acked":
        print("ACKED", time.strftime("%H:%M:%SZ", time.gmtime()), "msg", row[1]); src = None
        for l in open(RT + "/logs/" + sys.argv[3] + "/rf-kiss.log", errors="replace"):
            if " RX " in l and l[:19] >= t0 and ("ack" + str(row[1])) in l:
                m = re.search(r"([A-Z0-9]{1,6}(?:-[0-9]{1,2})?)>", l); src = m.group(1) if m else ""
        print("CALL-MATCH" if src == TO else ("NO-ACK-FRAME" if src is None else "CALL-DIFF")); sys.exit(0)
print("NOT-ACKED"); sys.exit(3)'
# this start's section of the MeshCom start log (after its last 'node image:' line): wait for the provisioning check to end
PROVWAIT='f={RT}/logs/start-meshcom-qemu-%s.log; [ -s "$f" ] || { echo NO-START-LOG; exit 3; }; for i in $(seq 1 120); do s=$(awk '"'"'/^\\[run\\] node image:/ {b=""} {b = b $0 ORS} END {printf "%%s", b}'"'"' "$f") || { echo LOG-READ-FAILED; exit 3; }; case "$s" in *"[run] node image:"*) ;; *) echo NO-START-SECTION; exit 3;; esac; case "$s" in *"provisioning the node"*) ;; *) echo NO-PROVISIONING; exit 0;; esac; case "$s" in *"provisioning verified"*) echo VERIFIED $i; exit 0;; *"provisioning FAILED"*) echo PROVISIONING-FAILED; exit 3;; esac; sleep 5; done; echo STILL-VERIFYING; exit 3'
# ONE MeshCom console attempt, run on the box from stdin (argv: text port): banner, '::<text>' + LF, stay until the node echoes
# it; prints markers only, never console text. rc 0 = echoed, 4 = no banner, 3 = no echo. The runner calls it at most 3 times.
MCSEND_PY=$(cat <<'MCS'
import socket, sys, time
n, port = sys.argv[1], int(sys.argv[2])
s = socket.create_connection(("127.0.0.1", port), 5); s.settimeout(0.5); buf = b""
def until(pat, secs):
    global buf
    end = time.monotonic() + secs
    while time.monotonic() < end and pat not in buf:
        try:
            c = s.recv(4096)
        except socket.timeout:
            continue
        if not c:
            break
        buf += c
    return pat in buf
if not until(b"Type --help for commands", 10):
    print("NO-BANNER"); s.close(); sys.exit(4)
print("BANNER", time.strftime("%H:%M:%SZ", time.gmtime())); k = len(buf)
s.sendall(("::" + n + "\n").encode())
ok = until(("::" + n).encode(), 15)
until(b"\x00never", 3)                              # stay 3 s after the echo: the node acts on the line in its loop
print("NOTSENT-TEXT" if b"not sent" in buf[k:] else "NO-NOTSENT-TEXT")
s.close()
if ok:
    print("ECHOED", time.strftime("%H:%M:%SZ", time.gmtime())); sys.exit(0)
print("NO-ECHO"); sys.exit(3)
MCS
)
# The PC's serial peer ports: held by a process we can see, or claimed by the local gpsd (?DEVICES) = HELD; an unreadable
# gpsd answer = rc 3.
PORTS_PY=$(cat <<'PPY'
import json, os, socket, sys
devs = {os.path.realpath(d): d for d in sys.argv[1:]}; held = {}
for p in (x for x in os.listdir("/proc") if x.isdigit()):
    try: fds = os.listdir(f"/proc/{p}/fd")
    except OSError: continue
    for fd in fds:
        try: t = os.readlink(f"/proc/{p}/fd/{fd}")
        except OSError: continue
        if t in devs:
            try: who = open(f"/proc/{p}/comm").read().strip()
            except OSError: who = p
            held.setdefault(t, set()).add(who)
try:
    s = socket.create_connection(("127.0.0.1", 2947), 3)
except OSError:
    s = None
if s is not None:
    s.settimeout(3); s.sendall(b"?DEVICES;\n"); b = b""; seen = None
    try:
        while seen is None:
            c = s.recv(4096)
            if not c: break
            b += c
            for line in b.split(b"\n")[:-1]:
                try: j = json.loads(line)
                except ValueError: continue
                if j.get("class") == "DEVICES": seen = j
    except OSError:
        pass
    if seen is None: print("GPSD-UNREADABLE"); sys.exit(3)
    for d in seen.get("devices", []):
        r = os.path.realpath(d.get("path", ""))
        if r in devs: held.setdefault(r, set()).add("gpsd")
for r, d in devs.items():
    print(("HELD " + d + " " + ",".join(sorted(held[r]))) if r in held else ("FREE " + d))
PPY
)
HPOWER_PY=$(cat <<'HPY'
import json, os, re, subprocess, sys
cfg = os.path.realpath(sys.argv[1]); f = os.path.join(cfg, "config"); t = open(f).read()
m = re.search(r"\[\[RNode 868\]\](.*?)(?=\n\s*\[\[|\Z)", t, re.S)
if not m: sys.exit(f"no [[RNode 868]] interface in {f}")
en = re.findall(r"^\s*(?:enabled|interface_enabled)\s*=\s*(\S+)\s*$", m.group(1), re.M)
tp = re.findall(r"^\s*txpower\s*=\s*(-?\d+)\s*$", m.group(1), re.M)
if len(en) != 1 or en[0].lower() not in ("yes", "true"): sys.exit("the RNode 868 interface is not enabled exactly once")
if len(tp) != 1: sys.exit(f"{len(tp)} txpower lines in [[RNode 868]]")
pids = []
for p in os.listdir("/proc"):
    if not p.isdigit(): continue
    try: a = [x.decode(errors="replace") for x in open(f"/proc/{p}/cmdline", "rb").read().split(b"\0") if x]
    except OSError: continue
    if any(os.path.basename(x) == "rnsd" for x in a) and "--config" in a[:-1] and os.path.realpath(a[a.index("--config") + 1]) == cfg:
        pids.append(int(p))
if len(pids) != 1: sys.exit(f"{len(pids)} rnsd processes run {cfg} (want exactly one)")
st = int(open(f"/proc/{pids[0]}/stat").read().rsplit(")", 1)[1].split()[19]); hz = os.sysconf("SC_CLK_TCK")
btime = int(next(l for l in open("/proc/stat") if l.startswith("btime")).split()[1]); started = btime + st / hz
j = json.loads(subprocess.run([sys.argv[2], "--config", cfg, "-j"], capture_output=True, text=True, timeout=30).stdout or "{}")
up = [i for i in j.get("interfaces", []) if "RNode 868" in i.get("name", "")]
print("TXPOWER", tp[0]); print("PID", pids[0]); print("NEWER", "yes" if started > os.stat(f).st_mtime else "no")
print("UP", "yes" if len(up) == 1 and up[0].get("status") is True else "no")
HPY
)

dbm_ok() { [[ "$1" =~ ^-?[0-9]{1,2}$ ]] && [ "$1" -le 17 ]; }
# the send forms of the contract's lint (the dry self-check sends-only-in-tx: each only inside a tx call); the two
# python send helpers (the MeshCom console, Graywolf) span lines in declare -f: every-send-gated checks them by their FRAMEs
SENDS_RE='form (tx_text|meshcom_send|mt_send|mc_send|rt_send)|tmux send-keys|/api/v1/announce|rns-exchange|(mt|mc|meshcom)-send'
# hipower <text>: rc 0 (refuse) when the text asks for high power (--high-power, --highpower, any case) or sets a power at
# 18 dBm or more: every power key (power, tx-power, tx_power, txpower, txp, 'set tx') followed by = : or blanks has its value
# taken, quotes stripped and compared as a base-10 integer (leading zeros, a sign). A value that is not an integer is refused
# too; a key with no value (the end, or a shell operator: a read like 'txpower | sed') is no setting. Every executor runs it.
hipower() {
  local t rest v; t=$(printf '%s' "$*" | tr '[:upper:]' '[:lower:]'); [[ $t =~ (^|[^a-z0-9])-?-high-?power ]] && return 0; rest=$t
  while [[ $rest =~ (^|[^a-z0-9])(power|tx[-_]?power|txp|set\ tx)[\ =:]+([^\ \|\;\&\<\>]*) ]]; do
    rest=${rest#*"${BASH_REMATCH[0]}"}; v=${BASH_REMATCH[3]//[\'\"]/}; [ -n "$v" ] || continue
    [[ $v =~ ^[+-]?[0-9]+$ ]] || return 0; v=${v#+}; [ "${v:0:1}" = - ] && continue; [ "$((10#$v))" -ge 18 ] && return 0; done
  return 1; }
# ---- 5. state, forms, output ------------------------------------------------------------------------------------------------
say() { echo "$*"; }; data() { echo "data: $*"; }; ok() { echo "ok: $*"; }
fail() { echo "FAIL: $*" >&2; printf '%s\n' "$*" > "$OUT/rowfail.txt"; exit 1; }   # a row FAIL (the marker); its undos run
eq() { [ "$2" = "$3" ] && ok "$1" || fail "$1: got '$2', want '$3'"; }
stop() { echo "STOP: $*" >&2; exit 2; }
pcpath() { case "$1" in "~"/*) printf '%s\n' "$HOME/${1#\~/}";; *) printf '%s\n' "$1";; esac; }
keep() { echo "$1=$2" >> "$OUT/state.env" || stop "state.env: append failed"; }
getk() { local l; l=$(grep "^$1=" "$OUT/state.env" 2>/dev/null | tail -n 1)   # a recorded empty value is a value
  if [ -n "$l" ]; then printf '%s\n' "${l#*=}"; else printf '%s\n' "${2-}"; fi; }
hask() { [ "$MODE" = dry ] && return 0; grep -q "^$1=" "$OUT/state.env"; }
form() { local n=$1 t=${F[$1]}; shift; t=${t//\{LH\}/\{RT\}/venv/lhpc/bin/lhpc}; local kv; for kv in "$@"; do t=${t//\{${kv%%=*}\}/${kv#*=}}; done
  if [[ $t == *"{cport}"* ]]; then for kv in "$@"; do [ "${kv%%=*}" = rid ] && t=${t//\{cport\}/$(rport 5000 "${kv#*=}")}; done; fi
  [ "$MODE" = live ] || echo "$n" >> "$SCRATCH/formed"; printf '%s' "$t"; }
gstart() { printf '( o=$(%s) || { echo RUNSET-READ-FAILED; exit 4; }; if printf "%%s\\n" "$o" | grep -qxF -- %s; then echo ALREADY-RUNNING %s; else %s; fi )' "$RUNSET" "$1" "$1" "$(form start inst="$1")"; }
gstop() { printf '( o=$(%s) || { echo RUNSET-READ-FAILED; exit 4; }; if printf "%%s\\n" "$o" | grep -qxF -- %s; then %s; else echo ALREADY-STOPPED %s; fi )' "$RUNSET" "$1" "$(form stop inst="$1")" "$1"; }
ordkeys() {   # stdin: a status text; args: instance keys -> the keys in status header order, by rid within a stack; rc 1 if one is not placed
  local hdr s out=; hdr=$(sed -nE 's/^\[([a-z0-9_-]+)(@r[0-9]+)?\] .*/\1/p' | grep -vx controller | awk '!seen[$0]++')
  for s in $hdr; do out="$out $(printf '%s\n' "$@" | grep -E "^$s@r[0-9]+$" | sort -t@ -k2.2n | tr '\n' ' ')"; done
  [ "$(printf '%s\n' $out | grep -c .)" = "$#" ] || return 1; echo $out; }

# ---- 6. the executors and the ONE capture helper -----------------------------------------------------------------------------
# X <H|Z|PC> <file> <command>: runs it; its redacted output in $OUT/<file>, its exit status in XRC (and $OUT/<file>.rc).
# XIN <H|Z> <file> <stdin-text> <command>: the same with a text on the command's stdin (a script, or the password for sudo -S).
# must = X, and a non-zero exit is a STOP. peer/peerpin: one peer.py action under the peer lock (BG=1: in the background, the
# pid in SPAWN; `reap <pid>` waits for it). Dry prints each command ([dry]) and runs nothing on a box or a peer: the fake box
# answers from the dry inventory and prints the FRAME lines of a send, the fake peer is fake_peer.py.
redact() {   # the passwords, the PIN, the tracker call and the operator's call (any SSID or none, any case) -> masked
  local t s c=${OPERATOR_CALL:-}; t=$(cat; echo x); t=${t%x}
  for s in "${PW_H:-}" "${PW_Z:-}" "${MC_PIN:-}" "${TRACKER_CALL:-}"; do [ -n "$s" ] || continue
    t=${t//"$s"/<REDACTED>}; done
  [[ "$c" =~ ^[A-Za-z0-9]+$ ]] || { printf '%s' "$t"; return; }; printf '%s' "$t" | sed -E "s/\\b$c(-[0-9]{1,2})?\\b/<CALL>/Ig"; }
capture() {   # capture <file> <cmd...>: THE one writer of an evidence file and its .rc: the command's output, redacted
  local f=$1 o=${OUT:-$EVD} s; shift; "$@" 2>&1 | redact > "$o/$f"; s=("${PIPESTATUS[@]}"); XRC=${s[0]}
  [ "${s[1]}" = 0 ] || stop "$f: the redactor failed"; echo "$XRC" > "$o/$f.rc" || stop "$f.rc: not written"; }
boxvar() { local v="BOX_${1}_$2" d=; case $2 in USER) d=lhpc;; RUNTIME) d='~/loraham-pi-control';; esac; printf '%s' "${!v:-$d}"; }
rt() { [ "$1" = PC ] && { [[ $2 != *"{RT}"* ]] || stop "a PC command names a box path ({RT})"; printf '%s' "$2"; return; }; local t=$2; printf '%s' "${t//\{RT\}/$(boxvar "$1" RUNTIME)}"; }
sshrun() { local b=$1 in=$2 c=$3 pw; [ "$b" = H ] && pw=$PW_H || pw=$PW_Z
  if [ "$in" = - ]; then SSHPASS=$pw sshpass -e ssh -n -o PubkeyAuthentication=no -o ConnectTimeout=10 "$(boxvar "$b" USER)@$(boxvar "$b" HOST)" "bash -lc $(printf %q "$c")"
  else printf '%s\n' "$in" | SSHPASS=$pw sshpass -e ssh -o PubkeyAuthentication=no -o ConnectTimeout=10 "$(boxvar "$b" USER)@$(boxvar "$b" HOST)" "bash -lc $(printf %q "$c")"; fi; }
drylog() { [ "$MODE" = dry ] && printf '%s\n' "$*" >> "$DRYLOG"; return 0; }
X() { local w=$1 f=$2; shift 2; local c; c=$(rt "$w" "$*"); drylog "CMD $w: $c"
  ! hipower "$c" || stop "$f: the command asks for high power or 18 dBm or more: never run"
  if [ "$MODE" = dry ]; then echo "  [dry] $w > $f: $c"; dryout "$w" "$f" "$c"; XRC=0; echo 0 > "$OUT/$f.rc"; dryframes "$w" "$c" ""; return 0; fi
  case $w in H|Z) capture "$f" sshrun "$w" - "$c";; PC) capture "$f" bash -c "$c";; *) stop "X: '$w'";; esac
  [ "$XRC" -ne 255 ] || [ "${XNOSTOP:-0}" = 1 ] || stop "$f: ssh transport rc 255"; }
XIN() { local w=$1 f=$2 in; in=$(rt "$w" "$3"); shift 3; local c; c=$(rt "$w" "$*"); drylog "CMD $w: (stdin) $c"
  ! hipower "$c" || stop "$f: the command asks for high power or 18 dBm or more: never run"
  if [ "$MODE" = dry ]; then echo "  [dry] $w > $f: (stdin) $c"; dryout "$w" "$f" "$c"; XRC=0; echo 0 > "$OUT/$f.rc"; dryframes "$w" "$c" "$in"; return 0; fi
  capture "$f" sshrun "$w" "$in" "$c"; [ "$XRC" -ne 255 ] || [ "${XNOSTOP:-0}" = 1 ] || stop "$f: ssh transport rc 255"; }
must() { X "$@"; [ "$XRC" = 0 ] || stop "$2: exit status $XRC ($OUT/$2)"; }
declare -A SPAWNF=()
peerrun() {   # peerrun <file> <pin|-> <py> <action> <args...>: THE one place a peer process starts (dry: fake_peer.py, empty stdin)
  local f=$1 pin=$2 py=$3; shift 3; local c="flock -n -E 75 $LOCK $py -I $PEER $(printf '%q ' "$@")"
  ! hipower "$*" || stop "$f: the peer action asks for high power or 18 dBm or more: never run"
  if [ "$pin" = pin ]; then drylog "CMD PC: (PIN on stdin) peer.py $*"; [ "$MODE" = dry ] && echo "  [dry] PC > $f: peer.py $* (the PIN on stdin)"
  else drylog "CMD PC: $c"; [ "$MODE" = dry ] && echo "  [dry] PC > $f: $c"; fi
  local -a run; if [ "$MODE" = dry ]; then run=(python3 -I "$FAKE_PEER" "$@")
  elif [ "$pin" = pin ]; then run=(pinrun "$py" "$@"); else run=(bash -c "$c"); fi
  if [ "${BG:-0}" = 1 ]; then ( capture "$f" "${run[@]}" < /dev/null ) & SPAWN=$!; SPAWNF[$SPAWN]=$f
  else capture "$f" "${run[@]}" < /dev/null; peerdone "$f"; fi; }
pinrun() { local py=$1; shift; printf '%s\n' "$MC_PIN" | flock -n -E 75 "$LOCK" "$py" -I "$PEER" "$@"; }
peerdone() { XRC=$(cat "${OUT:-$EVD}/$1.rc" 2>/dev/null || echo 99); [ "$XRC" != 75 ] || stop "$1: the peer lock is held (one peer at a time)"
  [ "$MODE" = live ] || { local l; while IFS= read -r l; do set -- $l; frame "$3" "${*:5}"; done < <(grep '^FRAME | ' "${OUT:-$EVD}/$1"); }; }
reap() { [ -n "$1" ] || return 0; wait "$1"; peerdone "${SPAWNF[$1]}"; }
peer() { local f=$1 py=$2; shift 2; peerrun "$f" - "$py" "$@"; }
peerpin() { local f=$1; shift; peerrun "$f" pin "$VMT" "$@"; }
body() { cat "${OUT:-$EVD}/$1" 2>/dev/null; return 0; }
# the fake box (dry): canned answers from the dry inventory, and one FRAME line per frame a box command would send
drystatus() { local v="DRY_STATUS_${1}_$2" bd f sf; [ -n "${!v:-}" ] && { printf '%s\n' "${!v}"; return; }
  bd=$(dryradios "$1" | awk -v r="$2" '$1 == "ROW" && $2 == r {print $4}'); case "$bd" in 433) f=433.775000; sf=11;; *) f=869.525000; sf=11;; esac
  echo "STATUS RADIO=READY TX=0 TXMODE=MANAGED TXQUEUE=1 RXREADY=1 HIGHPOWER=0 CHIPFAMILY=SX127x OCP=100 USBWAIT=- BOUND=- ID=$2 FREQ=$f SF=$sf POWER=10"; }
dryradios() { local v="DRY_RADIOS_$1"; printf '%s\n' "${!v:-STATE valid}"; }
dryout() { local w=$1 f=$2 c=$3; : > "$OUT/$f"
  case "$c" in *"$RADIOS_PY"*) dryradios "$w" > "$OUT/$f";; *"$STATUS_PY"*) drystatus "$w" "${c##* }" > "$OUT/$f";; esac; }
frame() { echo "FRAME | $1 | $2"; drylog "FRAME $1 $2"; }
dryframes() {   # dryframes <where> <command> <stdin>: the FRAME of a send form, from the command alone (never from a tx)
  local w=$1 c=$2 in=$3 send= base= rid='*' cls
  if [[ $c =~ LHPC_PROOF=([HZ]):[a-z]+@(r[0-9]+) ]]; then frame "${BASH_REMATCH[1]}:${BASH_REMATCH[2]}" control; return 0; fi   # the release: the box's delivery proof
  case "$c" in *loraf-*struct.pack*|*"--sendtext"*|*meshcore-cli*" public "*|*meshchat@r*" send "*|*"tmux send-keys"*|*"/api/v1/announce"*|*"create_connection(('127.0.0.1',"*) send=1;; esac
  [[ $c == *meshcore-cli* ]] && base=5000; [[ $in == *"Type --help for commands"* ]] && { send=1; base=12323; }; [[ $in == *'"/api/messages"'* ]] && { send=1; base=8080; }
  [ -n "$send" ] || return 0
  if [[ $c =~ @(r[0-9]+) ]] || [[ $c =~ lora[a-z]*-(r[0-9]+)\.sock ]] || [[ $c =~ rfchat-(r[0-9]+) ]]; then rid=${BASH_REMATCH[1]}
  elif [ -n "$base" ] && [[ $c =~ \ ([0-9]{4,5})(\ |$) ]] && [ $(( (BASH_REMATCH[1] - base) % 133 )) = 0 ]; then rid=r$(( (BASH_REMATCH[1] - base) / 133 + 1 )); fi
  cls=$(printf '%s' "$c" | grep -oE "$NPREFIX [0-9a-f]{8}" | head -n 1); frame "$w:$rid" "${cls:-control}"
  [[ $in == *'"/api/messages"'* ]] && frame PC:tracker control; return 0; }   # the tracker's ACK, tied to the box's message

# ---- 7. the ONE journal and the ONE recovery path -----------------------------------------------------------------------------
rec() { printf '%s\n' "$*" >> "$OUT/UNRESOLVED.txt" && return 0
  echo "STOP: unresolved and NOT RECORDED (UNRESOLVED.txt not writable): $*" >&2; [ "${RECOVERING:-0}" = 1 ] && return 1; exit 2; }
undo_push() { [ "$MODE" = dry ] && { echo "  [dry] undo += $1: $(rt "$1" "$2")"; return 0; }; local n
  n=$(( $(cat "$EVD/undo.seq" 2>/dev/null || echo 0) + 1 )) && echo "$n" > "$EVD/undo.seq" || stop "undo journal: id counter not written ('$2' not dispatched)"
  printf 'u%s\t%s\t%s\n' "$n" "$1" "$2" >> "$EVD/undo.list" || stop "undo journal: append failed for '$2' (not dispatched)"; }
undo_mark() { [ "$MODE" = dry ] && { echo 0; return; }; local n; n=$(wc -l < "$EVD/undo.list") || { echo "undo journal: unreadable (no mark)" >&2; return 1; }; echo "$n"; }
undo_to() {   # undo_to <mark>: run and remove every undo above <mark>, newest first; rc 1 if one failed (every caller STOPs)
  # An undo is marked done only AFTER it ran successfully (an interrupt before that leaves it for the recovery to run again;
  # the undos are idempotent: guarded starts/stops and the recorded original values). A failing undo goes to UNRESOLVED.txt
  # and stays in the journal, unmarked, so the recovery runs it again.
  [ "$MODE" = dry ] && { echo "  [dry] undo back to $1"; return 0; }; local n id w c rc=0
  n=$(wc -l < "$EVD/undo.list") && [[ "$1" =~ ^[0-9]+$ ]] || {
    rec "undo journal: unreadable or bad mark '$1' at undo_to: the undos above it did NOT run" || true
    [ "${RECOVERING:-0}" = 1 ] && return 1; stop "undo journal: unreadable or bad mark '$1'"; }
  while [ "$n" -gt "$1" ]; do IFS=$'\t' read -r id w c < <(sed -n "${n}p" "$EVD/undo.list")
    if grep -qxF -- "$id" "$EVD/undo.done" 2>/dev/null; then echo "undo journal: $id already ran (its removal had failed), not run again" >&2
    else XNOSTOP=1 X "$w" "UNDO-$id.txt" "$c"
      if [ "$XRC" != 0 ]; then rec "$w: $c (rc $XRC)" || true; rc=1; n=$((n - 1)); continue; fi
      echo "$id" >> "$EVD/undo.done" || { rec "undo journal: $id ran but is not recorded as run" || true; rc=1; }; fi
    sed -i "${n}d" "$EVD/undo.list" || { echo "undo journal: line $n ($id) not removed: $w: $c" >&2; rec "undo journal: line $n ($id) not removed: $w: $c" || true; rc=1; }
    n=$((n - 1)); done; return $rc; }
recover() { trap - EXIT INT TERM; [ "$MODE" = live ] && [ -d "$EVD" ] || return 0; RECOVERING=1; local lost=0
  if [ -f "$EVD/undo.list" ]; then echo "== RECOVERY: undoing every recorded change, newest first"; ( undo_to 0 ) || lost=1; XNOSTOP=1 verify_set || true; fi
  ( secret_scan ) || rec "recover: the evidence scan found a secret or failed: do not publish $EVD" || lost=1
  [ -s "$EVD/UNRESOLVED.txt" ] && echo "RECOVERY: unresolved, for the coordinator: $(tr '\n' '|' < "$EVD/UNRESOLVED.txt")" >&2
  [ "$lost" = 0 ] || echo "RECOVERY INCOMPLETE: an unreadable journal or a record that could not be written (see the STOP lines above)" >&2; return 0; }
keyset_ok() { local bad; bad=$(body "$1" | grep -v '^$' | grep -vE '^[a-z0-9_-]+@r([1-9]|[1-9][0-9])$') || true; [ -z "$bad" ]; }   # every line one instance key
verify_set() { local b rc=0 s1 s2; for b in $BOXES; do X $b "VS-$b.txt" "{LH} status"; s1=$XRC; X $b "VS-$b-runset.txt" "$RUNSET"; s2=$XRC; [ "$MODE" = dry ] && continue
    [ "$s1" = 0 ] && [ "$s2" = 0 ] || { rec "verify: $b status/key read failed (rc $s1/$s2)"; rc=1; continue; }
    body "VS-$b.txt" | zs_check || { rec "verify: $b status incomplete"; rc=1; continue; }
    keyset_ok "VS-$b-runset.txt" || { rec "verify: $b key list has a line that is not an instance key"; rc=1; continue; }
    hask "RUN_$b" || { rec "verify: RUN_$b was never recorded"; rc=1; continue; }
    [ "$(zs_sorted $(body "VS-$b-runset.txt"))" = "$(zs_sorted $(getk "RUN_$b"))" ] && ok "$b: the recorded running instances run again" \
      || { rec "verify: $b runs '$(zs_sorted $(body "VS-$b-runset.txt"))', recorded '$(getk "RUN_$b")'"; rc=1; }; done; return $rc; }

# ---- 8. the ONE dispatcher `tx`, the power reads and the USB guard ---------------------------------------------------------
# tx <key> <frames> <nonce> -- <executor> <args...>     a text message of ours (the nonce is checked)
# tx <key> <frames> control -- <executor> <args...>     protocol frames a command of ours sends (an announce)
# tx <key> <frames> control --reserve                  an automatic send by a protocol endpoint (an ACK, a delivery proof,
#                                                       a path request): every check and the budget, no command; it comes
#                                                       right before the command that triggers that send.
radk() { keep "RAD_$1" "$2"; }   # a key -> its physical radio (<box>:<rid> or PC:<peer>), recorded by the key's power read
pwok() { keep "PWROW_${1//:/_}" "${CURROW:-}"; }   # this physical radio's power was verified in this row
tx() { local k=$1 c=$2 cls=$3 sep=${4:-} rad n e f b
  [[ "$c" =~ ^[1-9]$ ]] || stop "tx $k: frames '$c' (1..9)"
  case "$sep" in --) shift 4; [ $# -ge 2 ] || stop "tx $k: no executor"; e=$1; case "$e" in must|X|XIN) f=$3;; peer|peerpin) f=$2;; *) stop "tx $k: executor '$e'";; esac;;
    --reserve) [ "$cls" = control ] && [ $# = 4 ] || stop "tx $k: a reservation is 'tx <key> <frames> control --reserve'"; shift 4; e=reserve;;
    *) stop "tx $k: '$sep' (want -- or --reserve)";; esac
  rad=$(getk "RAD_$k"); [ -n "$rad" ] || stop "tx $k: the key resolves to no radio (no power read for it): no TX"
  ! hipower "$*" || stop "tx $k: the command carries --high-power or a power of 18 dBm or more: no TX"
  [ "$(getk "PWROW_${rad//:/_}")" = "${CURROW:-}" ] && [ -n "${CURROW:-}" ] || stop "tx $k: $rad has no verified power in this row: no TX"
  if [ "$cls" != control ]; then [ "${#cls}" -le 16 ] && grep -qxF -- "$cls" "$OUT/nonces" 2>/dev/null || stop "tx $k: '$cls' is not a nonce of this run"
    [[ "$*" == *"$cls"* ]] || stop "tx $k: the command does not carry its nonce '$cls'"; fi
  n=$(( $(getk "TXR_${rad//:/_}" 0) + c )); [ "$n" -le $TXMAX ] || stop "budget $rad: $n of $TXMAX"; keep "TXR_${rad//:/_}" "$n"
  for b in $BOXES; do X $b "TXG-boot-$b.txt" "cat /proc/sys/kernel/random/boot_id"; [ "$MODE" = dry ] || [ "$(body "TXG-boot-$b.txt")" = "$(getk BOOT_$b)" ] || stop "tx $k: box $b rebooted (boot id changed)"; done
  usbsnap TXG-usb; [ "$MODE" = dry ] || { cmp -s "$OUT/R0-usb-byid.txt" "$OUT/TXG-usb-byid.txt" || stop "tx $k: a PC peer re-enumerated or vanished"
    ! grep -qiE "$USBEV" "$OUT/TXG-usb-kern.txt" || stop "tx $k: a USB event in the PC's kernel log since R0 (TXG-usb-kern.txt)"; }
  case "$rad" in [HZ]:*) b=${rad%%:*}; must "$b" "TXG-$b-usb.txt" "$USBLIST"; [ "$MODE" = dry ] || cmp -s "$OUT/R0-$b-usb.txt" "$OUT/TXG-$b-usb.txt" || stop "tx $k: box $b's USB device list changed since R0";; esac
  say "TX | $rad | $c | $cls | $([ "$e" = reserve ] && echo reserve || echo "$e $f")"; drylog "TX $rad $c $cls $e"
  [ "$e" = reserve ] || "$@"; }
usbsnap() { if [ "$MODE" = dry ]; then echo "  [dry] PC USB snapshot $1"; printf '%s\n' ${DRY_BYID:-} | awk '{print $1, "../../dry"}' > "$OUT/$1-byid.txt"; : > "$OUT/$1-kern.txt"; return 0; fi
  capture "$1-byid.txt" bash -c "ls -l /dev/serial/by-id/ 2>/dev/null | awk 'NR > 1 {print \$9, \$11}'"   # both through the redactor
  capture "$1-kern.txt" journalctl -k --since "@$T0" --no-pager -o short-iso; [ "$XRC" = 0 ] || stop "journalctl -k (rc $XRC)"; }
boxkern() {   # boxkern <box> <file>: the box's kernel log since its R0 clock, as root (sudo -S, the password on stdin); first line uid 0
  local pw; [ "$1" = H ] && pw=$PW_H || pw=$PW_Z
  XIN "$1" "$2" "$pw" "sudo -S -p '' sh -c 'id -u; journalctl -k --since @$(getk "T0_$1" 0) --no-pager -o short-iso'"; [ "$MODE" = dry ] && return 0
  [ "$XRC" = 0 ] && [ "$(head -n 1 "$OUT/$2")" = 0 ] || stop "$2: box $1's kernel log is not readable as root (rc $XRC)"; }
usbguard() {   # usbguard <tag>: PC by-id + kernel log, each present box's USB list + kernel log, all against R0; any change = STOP
  [ "$MODE" = dry ] && { echo "  [dry] USB guard $1: PC by-id + kernel log; boxes $BOXES: sysfs USB list + kernel log (sudo -S, stdin)"; return 0; }
  usbsnap "G-$1-usb"; cmp -s "$OUT/R0-usb-byid.txt" "$OUT/G-$1-usb-byid.txt" || stop "USB guard $1: a PC serial device re-enumerated or vanished"
  ! grep -qiE "$USBEV" "$OUT/G-$1-usb-kern.txt" || stop "USB guard $1: a USB event in the PC's kernel log since R0"
  local b ev; for b in $BOXES; do must $b "G-$1-$b-usb.txt" "$USBLIST"; cmp -s "$OUT/R0-$b-usb.txt" "$OUT/G-$1-$b-usb.txt" || stop "USB guard $1: box $b's USB device list changed since R0"
    boxkern $b "G-$1-$b-kern.txt"; ev=$(sed 1d "$OUT/G-$1-$b-kern.txt" | grep -ciE "$USBEV") || true
    [ "$ev" = 0 ] || stop "USB guard $1: $ev USB event line(s) in box $b's kernel log since R0"; done
  ok "USB guard $1: no re-enumeration on the PC and the boxes $BOXES"; }
# the pure power judges; each prints the value, or the reason with rc 1
jstatus() {   # jstatus "<daemon STATUS line>": exactly one POWER in -9..17 and exactly one HIGHPOWER, = 0
  local -a ws; local w p= h= np=0 nh=0; read -ra ws <<< "$1"
  for w in "${ws[@]}"; do case "$w" in POWER=*) p=${w#POWER=}; np=$((np + 1));; HIGHPOWER=*) h=${w#HIGHPOWER=}; nh=$((nh + 1));; esac; done
  [ "$np" = 1 ] || { echo "$np POWER field(s) in the STATUS (want 1)"; return 1; }; [ "$nh" = 1 ] || { echo "$nh HIGHPOWER field(s) in the STATUS (want 1)"; return 1; }
  [[ "$p" =~ ^-?[0-9]{1,2}$ ]] || { echo "POWER '$p' is not a number"; return 1; }; [ "$p" -ge -9 ] && [ "$p" -le 17 ] || { echo "POWER=$p outside -9..17"; return 1; }
  [ "$h" = 0 ] || { echo "HIGHPOWER is '$h', not 0"; return 1; }; echo "$p"; }
jfield() { local -a ws; local w v= n=0; read -ra ws <<< "$1"; for w in "${ws[@]}"; do case "$w" in "$2="*) v=${w#*=}; n=$((n + 1));; esac; done
  [ "$n" = 1 ] && [ -n "$v" ] || { echo "$n $2 field(s) in the STATUS (want 1)"; return 1; }; echo "$v"; }
jgrant() {   # jgrant "<radio rows>" <rid>: exactly one row of the rid, its high-power grant None or False
  local rows n hp; rows=$(printf '%s\n' "$1" | grep -E "^ROW $2 "); n=$(printf '%s\n' "$rows" | grep -c .)
  [ "$n" = 1 ] || { echo "$n hardware row(s) of $2 (want 1)"; return 1; }; hp=$(printf '%s\n' "$rows" | grep -oE ' hp=[^ ]*$' | cut -d= -f2)
  case "$hp" in None|False) echo "$hp";; *) echo "the high-power grant of $2 is '$hp' (want None or False)"; return 1;; esac; }
jspower() {   # jspower <form> <value>: a stack's own power value in -9..17; Meshtastic's 0 means the firmware's maximum
  [[ "$2" =~ ^-?[0-9]{1,2}$ ]] || { echo "no strict power value ('$2')"; return 1; }; [ "$2" -ge -9 ] && [ "$2" -le 17 ] || { echo "power $2 outside -9..17"; return 1; }
  [ "$1" != mt_pget ] || [ "$2" -ge 1 ] || { echo "Meshtastic tx_power $2 (0 = the firmware's maximum)"; return 1; }; echo "$2"; }
jcap() { [[ "$1" =~ ^[0-9]{1,2}$ ]] && [ "$1" -ge 1 ] && [ "$1" -le 17 ]; }   # Meshtastic's max_power cap, 1..17
jmodel() {   # jmodel <box> <rid> <value>: the value <= min(17, the radio's model's power_max_dbm on that box), else the reason
  local m c; m=$(getk "MODEL_$1_$2"); c=$(getk "CAP_$1_$m"); [ -n "$m" ] && [[ "$c" =~ ^[0-9]{1,2}$ ]] || { echo "no model cap for $1:$2 ('$m' '$c')"; return 1; }
  [ "$c" -gt 17 ] && c=17; [ "$3" -le "$c" ] || { echo "power $3 above $c dBm (min(17, $m's power_max_dbm))"; return 1; }; echo "$3"; }
pnum() {
  local all v; all=$(body "$1" | grep -v '^[[:space:]]*$')
  case $2 in mt_pget) v=$(printf '%s\n' "$all" | sed -nE 's/^lora\.(tx_power|txPower): (-?[0-9]+)$/\2/p')
      [ "$(printf '%s\n' "$v" | grep -c .)" = 1 ] || return 0;;
    mc_pget) all=$(printf '%s\n' "$all" | grep -v '^INFO:meshcore:')                                  # meshcore-cli's logging lines only
      [ "$(printf '%s\n' "$all" | grep -c .)" = 1 ] || return 0; v=$(printf '%s\n' "$all" | grep -xE -- '-?[0-9]+');;
    *) [ "$(printf '%s\n' "$all" | grep -c .)" = 1 ] || return 0; v=$(printf '%s\n' "$all" | grep -xE -- '-?[0-9]+');;
  esac; printf '%s' "$v"; }
hpgrant() { must "$1" "HG-$3.txt" "cd {RT} && ./venv/lhpc/bin/python -c '$RADIOS_PY'"; [ "$MODE" = dry ] && return 0
  local o; o=$(jgrant "$(body "HG-$3.txt")" "$2") || stop "$3: $o: no TX"; }
dpower() {   # dpower <box> <rid> <key>: the daemon's STATUS of that radio: POWER <= min(17, model cap), HIGHPOWER=0, no grant
  hpgrant "$1" "$2" "$3"; must "$1" "PW-$3.txt" "{RT}/venv/lhpc/bin/python -c '$STATUS_PY' $2"; radk "$3" "$1:$2"; [ "$MODE" = dry ] && { pwok "$1:$2"; return 0; }
  local p o; p=$(jstatus "$(body "PW-$3.txt" | head -n 1)") || stop "$3: $p: no TX"; o=$(jmodel "$1" "$2" "$p") || stop "$3: $o: no TX"
  keep "P_$3" "$p"; pwok "$1:$2"; ok "$3: power as-is $p dBm, HIGHPOWER=0, no high-power grant, within its model's cap"; }
spower() {   # spower <box> <form> <rid> <key>: a stack's own power value (MeshCore transmits through the daemon: its STATUS too)
  hpgrant "$1" "$3" "$4"; [ "$2" != mc_pget ] || dpower "$1" "$3" "${4}D"
  must "$1" "PW-$4.txt" "$(form "$2" rid="$3")"; radk "$4" "$1:$3"; [ "$MODE" = dry ] && { pwok "$1:$3"; return 0; }
  local p o; p=$(jspower "$2" "$(pnum "PW-$4.txt" "$2")") || stop "$4: $p (raw output in PW-$4.txt): no TX"; o=$(jmodel "$1" "$3" "$p") || stop "$4: $o: no TX"
  keep "P_$4" "$p"; pwok "$1:$3"; ok "$4: power as-is $p dBm, within its model's cap"; }
ppower() {   # ppower <key> <peer> <action> <low>: a PC peer set LOW for its row (the original journalled first), read back low
  local k=$1 pr=$2 a=$3 lo=$4 o r; dbm_ok "$lo" || stop "$k: the row's low power '$lo' is not <= 17"; radk "$k" "PC:$pr"
  peer "PP-$k-0.txt" "$VMT" "$a"; [ "$XRC" = 0 ] || stop "$k: the peer power read failed (rc $XRC)"; o=$(body "PP-$k-0.txt" | sed -n 's/^POWER //p' | tail -n 1)
  [ "$MODE" = dry ] || case "$o" in ''|*[!0-9-]*) stop "$k: no power readback";; esac
  undo_push PC "flock -E 75 $LOCK $VMT -I $PEER $a ${o:-10}"
  peer "PP-$k-1.txt" "$VMT" "$a" "$lo"; [ "$XRC" = 0 ] || stop "$k: the peer power set failed (rc $XRC)"; r=$(body "PP-$k-1.txt" | sed -n 's/^POWER //p' | tail -n 1)
  [ "$MODE" = dry ] || [ "$r" = "$lo" ] || stop "$k: the peer reads back '$r', not the low power $lo: no TX"; keep "P_$k" "$lo"; pwok "PC:$pr"; data "$k: peer power ${o:-?} -> $lo for the row"; }
hpower() {   # hpower <key> [<capture>]: the shared RNode through the PC's rnsd: <= 7 dBm, the running rnsd's config, Up; else STOP
  local k=$1 f=${2:-5-rnode-power.txt}; radk "$k" PC:rnode
  [ "$MODE" = dry ] && { echo "  [dry] PC > $f: hpower (rns-host config txpower, one rnsd started after its mtime, rnstatus Up)"; pwok PC:rnode; return 0; }
  printf '%s\n' "$HPOWER_PY" > "$SCRATCH/hpower.py"; X PC "$f" "python3 -I $SCRATCH/hpower.py $RNSCFG $(dirname "$VRNS")/rnstatus"
  [ "$XRC" = 0 ] || stop "rnode: the shared RNode's power cannot be read now ($(tr '\n' ' ' < "$OUT/$f")): no TX"
  local tp nw up; tp=$(sed -n 's/^TXPOWER //p' "$OUT/$f"); nw=$(sed -n 's/^NEWER //p' "$OUT/$f"); up=$(sed -n 's/^UP //p' "$OUT/$f")
  [[ "$tp" =~ ^-?[0-9]{1,2}$ ]] || stop "rnode: no TXPOWER in the read ($f): no TX"
  [ "$nw" = yes ] || stop "rnode: the rnsd runs an older config than the file (it was edited after the start): no TX"
  [ "$up" = yes ] || stop "rnode: the RNode 868 interface is not Up: no TX"
  [ "$tp" -le 7 ] || stop "rnode: the shared RNode is set to $tp dBm (above 7): no TX"
  keep "P_$k" "$tp"; pwok PC:rnode; ok "rnode: the shared RNode at $tp dBm (read now, <= 7)"; }
started() { undo_push "$1" "$(gstop "$3")"; must "$1" "$2" "$(form start inst="$3")"; }   # its guarded stop, journalled BEFORE the start

# ---- 9. the report, the results and the COVERAGE table ----------------------------------------------------------------------
REPORT() {   # REPORT <id> <stack> <box (rid)> <band> <peer> <direction> <nonce seen> <power used> <result>
  [ "$MODE" = live ] || [ "$9" != PASS ] || set -- "${@:1:8}" DRY-PASS   # dry never says PASS: a rehearsal
  local line; line=$(IFS='|'; echo "$*" | sed 's/|/ | /g'); say "REPORT | $line"
  printf '%s|%s|%s\n' "$1" "$6" "$9" >> "$OUT/results.txt" || stop "results.txt: append failed"
  [ "$MODE" = dry ] || echo "| $line |" >> "$EVD/REPORT.md" || stop "REPORT.md: append failed"; }
notrun() {   # notrun <id> <direction> <why>
  REPORT "$1" "$(rowname "$1")" — — — "$(dirtext "$2")" — — "NOT RUN: $3"; say "NOT RUN: row $1 $(rowname "$1") $(dirtext "$2"): $3"
  [ "$MODE" = dry ] || echo "row $1 $(dirtext "$2"): $3" >> "$EVD/NOT-RUN.txt"; }
notrun_row() { local d; for d in $(dirs "$1"); do notrun "$1" "$d" "$2"; done; }
needs() { local f m=; for f in "$@"; do [ "$MODE" = live ] || echo "$f" >> "$SCRATCH/needed"; case " $FORMS_OK " in *" $f "*) ;; *) m="$m $f";; esac; done
  [ -z "$m" ] && return 0; NEEDS_MISSING=${m# }; return 1; }
nonce() { local n; while :; do n="$NPREFIX $(od -An -N4 -tx1 /dev/urandom | tr -d ' \n')"
  grep -qxF -- "$n" "$OUT/nonces" 2>/dev/null && continue; echo "$n" >> "$OUT/nonces" || stop "nonces: append failed"; echo "$n"; return; done; }
boxrx() {   # boxrx <box> <file> <form> <rid> <nonce> <secs>: a marker line only (SEEN <nonce> <time> rssi=<n>), never the RF-log line
  X "$1" "$2" "for i in \$(seq 1 $6); do l=\$($(form "$3" rid="$4" n="$5") | head -n 1); [ -n \"\$l\" ] && { echo \"SEEN $5 \$(date -u +%H:%M:%SZ) rssi=\$(printf '%s' \"\$l\" | sed -nE 's/.*rssi=(-?[0-9.]+).*/\\1/p')\"; exit 0; }; sleep 1; done; echo NOT-SEEN; exit 3"
  result "$2" "$5"; }
result() { [ "$MODE" = dry ] && return 0
  if [ "$XRC" = 0 ] && grep -qxE "SEEN $(printf '%s' "$2" | sed 's/[.[\*^$]/\\&/g') .*" "$OUT/$1"; then return 0; fi
  if [ "$XRC" = 3 ] && [ "$(tail -n 1 "$OUT/$1")" = NOT-SEEN ]; then return 1; fi
  stop "$1: neither SEEN '$2' (rc 0) nor NOT-SEEN (rc 3): rc $XRC"; }
covres() { local r; r=$(grep -F "$1|$(dirtext "$2")|" "$OUT/results.txt" 2>/dev/null | tail -n 1 | cut -d'|' -f3-)
  case "$r" in PASS) echo "proven|";; DRY-PASS) echo "rehearsed|";; "FAIL: "*) echo "FAIL|${r#FAIL: }";; "NOT RUN: "*) echo "NOT RUN|${r#NOT RUN: }";; *) echo "NOT RUN|no result";; esac; }
coverage() {   # one line per item (a model, a stack, a check), row and direction; every supported model no radio carries: not present
  local id d e k m res seen=" " line; [ "$MODE" = dry ] || printf '| item | kind | row | direction | result | why |\n|---|---|---|---|---|---|\n' > "$EVD/COVERAGE.md"
  covline() { line="$1 | $2 | $3 | $4 | $5 | ${6:--}"; say "COVERAGE | $line"; [ "$MODE" = dry ] || echo "| $line |" >> "$EVD/COVERAGE.md"; }
  for id in $RUN; do
    if [ "$(rowf "$id" kind)" = frame ]; then
      for d in $(dirs "$id"); do res=$(covres "$id" "$d")
        if [ "$d" = stop-isolation ]; then covline stop-isolation check "$id" - "${res%%|*}" "${res#*|}"; continue; fi
        for m in $(for e in a b; do k=$(rowf "$id" "$e"); k=${k#*:}; v="RADIO_$k"; set -- ${!v:-}; echo "${2:-?}"; done | awk '!s[$0]++'); do
          covline "$m" model "$id" "$d" "${res%%|*}" "${res#*|}"; seen="$seen$m "; done; done
    else for d in $(dirs "$id"); do res=$(covres "$id" "$d"); covline "$(rowname "$id")" stack "$id" "$(dirtext "$d")" "${res%%|*}" "${res#*|}"; done; fi; done
  for m in $(sed -n 's/^CAP \([^ ]*\) .*/\1/p' "$OUT"/R0-*-caps.txt 2>/dev/null | awk '!s[$0]++'); do
    case "$seen" in *" $m "*) ;; *) covline "$m" model - - "not present" "no row carries this model";; esac; done; }

# ---- 10. R0 (what is present) and R1 (stop what runs) -----------------------------------------------------------------------
radios_ok() {   # radios_ok <file>: exactly one 'STATE valid' line, and every other line a well-formed ROW (blank lines aside)
  local t; t=$(body "$1" | grep -v '^[[:space:]]*$'); [ "$(printf '%s\n' "$t" | grep -c '^STATE ')" = 1 ] && printf '%s\n' "$t" | grep -qx 'STATE valid' \
    && ! printf '%s\n' "$t" | grep -vxE 'STATE valid|ROW r[1-9][0-9]? [a-z0-9][a-z0-9-]* (433|868) (True|False) serial=[^ ]* port=[^ ]* hp=(None|True|False)' | grep -q .; }
radiokey() {   # radiokey <KEY>: RK = "rid <rid> <box> <model>", or "why <reason>" (absent/ambiguous); called directly, so its STOP ends the run
  local v="RADIO_$1" b m bd sel rows n; read -r b m bd sel <<< "${!v:-}"
  [ -n "$b" ] || { RK="why absent: radio $1"; return 0; }; [ -z "${ABSENT[$b]:-}" ] || { RK="why ${ABSENT[$b]}"; return 0; }
  rows=$(body "R0-$b-radios.txt" | awk -v m="$m" -v bd="$bd" '$1 == "ROW" && $3 == m && $4 == bd && $5 == "True"')
  case "$sel" in serial=*|port=*) rows=$(printf '%s\n' "$rows" | grep -F " $sel ");; '') ;; *) stop "RADIO_$1: '$sel' (want serial=<s> or port=<p>)";; esac
  n=$(printf '%s\n' "$rows" | grep -c .); case "$n" in 0) RK="why absent: radio $1";; 1) RK="rid $(printf '%s\n' "$rows" | cut -d' ' -f2) $b $m";; *) RK="why ambiguous: radio $1 ($n match)";; esac; }
peerok() {   # peerok <name>: PK = "" when the peer is present, else the why; called directly, so its STOP ends the run
  local v="PEER_$1" kind id rest; read -r kind id rest <<< "${!v:-}"; PK=; [ -n "$kind" ] || { PK="absent: peer $1"; return 0; }   # unset or empty
  case "$kind" in meshcom|aprs|meshtastic|meshcore-ble|rnode) ;; *) stop "PEER_$1: unknown kind '$kind' (want meshcom, aprs, meshtastic, meshcore-ble or rnode)";; esac
  [ -n "$id" ] || stop "PEER_$1: kind '$kind' without its by-id name or BLE address (an empty or unset key = the peer is absent)"
  if [ "$kind" = meshcore-ble ]; then
    if [ "$MODE" = dry ]; then case " ${DRY_BLE:-} " in *" $id "*) ;; *) PK="absent: peer $1";; esac
    else peerpin "R0-$1-power.txt" mc-power; [ "$XRC" = 0 ] || PK="absent: peer $1"; fi   # its read-only power read: the connect
  else grep -q "^$id " "$OUT/R0-usb-byid.txt" || PK="absent: peer $1"; fi; }
row_R0() {
  [ "$MODE" = dry ] || { [ ! -e "$EVD" ] || stop "$EVD exists"; mkdir -p "$EVD" || stop "$EVD: mkdir"; : > "$EVD/state.env"; : > "$EVD/undo.list"; : > "$EVD/undo.done"
    printf '| row | stack | box | band | peer | direction | nonce seen | power used | result |\n|---|---|---|---|---|---|---|---|---|\n' > "$EVD/REPORT.md"; }
  for v in INTHEAD INTCI; do [ -n "${!v:-}" ] || stop "$v is not set"; done; [[ "$INTHEAD" =~ ^[0-9a-f]{40}$ ]] || stop "INTHEAD: 40 hex"
  INTHEAD_H=${INTHEAD_H:-$INTHEAD}; [[ "$INTHEAD_H" =~ ^[0-9a-f]{40}$ ]] || stop "INTHEAD_H: 40 hex"; data "R0: lane $LANE, rows $RUN; boxes present: '${BOXES}'"
  [ "$MODE" = dry ] || [ "$FORMS_HEAD" = "$INTHEAD" ] || stop "FORMS_HEAD '$FORMS_HEAD' is not INTHEAD: confirm the forms at this head first"
  [ "$MODE" = dry ] || [ "$(cd "$REPO" && gh run view "$INTCI" --json headSha,conclusion -q '.headSha + " " + .conclusion')" = "$INTHEAD success" ] || stop "INTCI is not a green run of INTHEAD"
  T0=$(date -u +%s); keep T0 "$T0"; usbsnap R0-usb; local b want p
  for b in $BOXES; do [ "$MODE" = dry ] && break   # a box that does not answer one short probe is ABSENT (its rows NOT RUN)
    XNOSTOP=1 X $b "R0-$b-probe.txt" "true"; [ "$XRC" = 0 ] || { ABSENT[$b]="absent: box $b"; data "R0: box $b does not answer (rc $XRC): absent"; }; done
  p=; for b in $BOXES; do [ -n "${ABSENT[$b]:-}" ] || p="$p $b"; done; BOXES=${p# }; keep BOXES "$BOXES"
  for b in $BOXES; do
    want=$INTHEAD; [ $b = H ] && want=$INTHEAD_H
    must $b "R0-$b-head.txt" "git -C {RT}/src/loraham-pi-control rev-parse HEAD"; [ "$MODE" = dry ] || eq "R0 $b: the real install is its expected head" "$(body "R0-$b-head.txt")" "$want"
    must $b "R0-$b-journal.txt" "test -d /var/log/journal && echo persistent || echo volatile"; [ "$MODE" = dry ] || eq "R0 $b: the journal is persistent" "$(body "R0-$b-journal.txt")" persistent
    must $b "R0-$b-boot.txt" "cat /proc/sys/kernel/random/boot_id"; keep "BOOT_$b" "$(body "R0-$b-boot.txt")"
    must $b "R0-$b-ssh.txt" "ss -Htn state established '( sport = :22 )' | wc -l"; [ "$MODE" = dry ] || eq "R0 $b: exactly one ssh connection (ours)" "$(body "R0-$b-ssh.txt")" 1
    must $b "R0-$b-clock.txt" "date -u +%s"; [ "$MODE" = dry ] || { [[ "$(body "R0-$b-clock.txt")" =~ ^[0-9]+$ ]] || stop "R0 $b: no clock"; keep "T0_$b" "$(body "R0-$b-clock.txt")"; }
    must $b "R0-$b-usb.txt" "$USBLIST"; boxkern $b "R0-$b-kern.txt"                       # the box's USB baseline; its kernel log readable as root (uid 0)
    must $b "R0-$b-radios.txt" "cd {RT} && ./venv/lhpc/bin/python -c '$RADIOS_PY'"
    radios_ok "R0-$b-radios.txt" || stop "R0 $b: the hardware list is not valid (want exactly one 'STATE valid' and well-formed ROW lines): $(body "R0-$b-radios.txt" | grep -v '^ROW ' | head -n 1)"
    if [ "$MODE" = dry ]; then echo "  [dry] PC > R0-$b-caps.txt: the repo's radio_models (dry uses the repo copy)"; python3 -I -c "$CAPS_PY" "$REPO" > "$OUT/R0-$b-caps.txt" 2>&1 || stop "R0 $b: the repo's radio_models cannot be read"
    else must $b "R0-$b-caps.txt" "cd {RT} && ./venv/lhpc/bin/python -c '$CAPS_PY'"; fi   # the installed copy (its loader); unreadable = STOP
    grep -qE '^CAP [a-z0-9-]+ [0-9]+$' "$OUT/R0-$b-caps.txt" || stop "R0 $b: no model power cap read (R0-$b-caps.txt)"
    while read -r _ m c; do keep "CAP_${b}_$m" "$c"; done < <(grep -E '^CAP ' "$OUT/R0-$b-caps.txt")
    must $b "R0-$b-tx.txt" "for d in {RT}/logs/r*; do echo \"\${d##*/} \$(cat \$d/rf-*.log 2>/dev/null | grep -c ' TX ')\"; done"
    must $b "R0-$b-status.txt" "{LH} status"; must $b "R0-$b-runset.txt" "$RUNSET"
    [ "$MODE" = dry ] && continue
    body "R0-$b-status.txt" | zs_check || stop "R0 $b: an incomplete status"
    keyset_ok "R0-$b-runset.txt" || stop "R0 $b: the key list has a line that is not an instance key (R0-$b-runset.txt)"
    local keys s ord; keys=$(body "R0-$b-runset.txt" | grep -v '^$' | tr '\n' ' ')
    for s in $(body "R0-$b-status.txt" | zs_running); do s=${s%%@*}; case " $keys" in *" $s@"*) ;; *) stop "R0 $b: '$s' runs (status) but has no instance key: unclaimed";; esac; done
    ord=$(body "R0-$b-status.txt" | ordkeys $keys) || stop "R0 $b: an instance key's stack has no status header ($keys)"
    keep "RUN_$b" "$ord"; data "R0 $b: running instances: '${ord}'"
  done
  local r e ep k o why devs= pr bud; declare -A SUM=()
  for r in $RUN; do why=; for e in a b; do ep=$(rowf "$r" "$e"); case "$ep" in -) continue;; esac
      if [ "${ep%%:*}" = peer ]; then peerok "${ep#*:}"; o=$PK; [ -z "$o" ] || why="$why$o; "
        k="PEER_${ep#*:}"; read -r _ pr _ <<< "${!k:-}"; [ -n "$o" ] || [[ "$pr" == *:*:* ]] || devs="$devs /dev/serial/by-id/$pr"
      else k=${ep#*:}; radiokey "$k"; o=$RK; case "$o" in "why "*) why="$why${o#why }; ";;
          rid*) read -r _ v bb m <<< "$o"; [ "$bb" = "${ep%%:*}" ] || stop "row $r: $k is on box $bb, the table says ${ep%%:*}"
            [[ "$v" =~ ^r([1-9]|[1-9][0-9])$ ]] || stop "R0: $k = '$v' is not a radio id"; keep "$k" "$v"; keep "MODEL_${bb}_$v" "$m"; data "R0: row $r: $k = $bb:$v ($m)";; esac; fi; done
    [ -z "$why" ] || { keep "SKIP_$r" "$(printf '%s' "${why%; }" | awk -v RS='; ' '!s[$0]++' | paste -sd ';' | sed 's/;/; /g')"; continue; }
    [ "$(rowf "$r" kind)" = stack ] && read -r wa wb <<< "${WORST[$r]}" || { wa=1; wb=1; }   # the worst case per endpoint
    for e in "a $wa" "b $wb"; do set -- $e; [ "$2" -gt 0 ] || continue; ep=$(rowf "$r" "$1")
      if [ "${ep%%:*}" = peer ]; then pr="PC:${ep#*:}"; else pr="${ep%%:*}:$(getk "${ep#*:}")"; fi; SUM[$pr]=$(( ${SUM[$pr]:-0} + $2 )); done; done
  r0grants
  [ -z "$devs" ] || { X PC R0-peer-ports.txt "python3 -I -c $(printf %q "$PORTS_PY") $devs"; [ "$MODE" = dry ] || { [ "$XRC" = 0 ] || stop "R0: the peer ports cannot be checked (gpsd answer unreadable?)"
    ! grep -q '^HELD ' "$OUT/R0-peer-ports.txt" || stop "R0: a peer port is held: $(grep '^HELD ' "$OUT/R0-peer-ports.txt" | tr '\n' '|') (free it first: gpsd, a console)"; }; }
  for pr in $(printf '%s\n' "${!SUM[@]}" | sort); do say "BUDGET | $pr | ${SUM[$pr]} | $TXMAX"; done
  for pr in "${!SUM[@]}"; do [ "${SUM[$pr]}" -le $TXMAX ] || stop "budget $pr: ${SUM[$pr]} of $TXMAX"; done; }
r0grants() {   # before anything starts, every resolved radio of the chosen rows has no high-power grant (hp= None/False), else STOP
  local r e ep k v o; for r in $RUN; do [ -z "$(getk "SKIP_$r")" ] || continue; for e in a b; do ep=$(rowf "$r" "$e"); case "$ep" in peer:*|-) continue;; esac
    k=${ep#*:}; v=$(getk "$k"); o=$(jgrant "$(body "R0-${ep%%:*}-radios.txt")" "$v") || stop "R0: row $r, $k = $v: $o: nothing starts"; done; done
  ok "R0: no high-power grant on any radio of rows $RUN"; }
row_R1() { local b i keys rev; for b in $BOXES; do   # instance keys; starts journalled first, dependents stopped first
  if [ "$MODE" = dry ]; then keys="daemon@r1 kiss@r1"; else hask "RUN_$b" || stop "R1: RUN_$b was never recorded"; keys=$(getk "RUN_$b"); fi
  [ -n "$keys" ] || { data "R1 $b: nothing runs (the recorded set is empty): nothing to stop"; continue; }
  rev=$(printf '%s\n' $keys | tac | tr '\n' ' ')
  for i in $rev; do undo_push $b "$(gstart "$i")"; done          # newest first in recovery = the daemon first, dependents after
  for i in $rev; do must $b "R1-$b-stop-$i.txt" "$(gstop "$i")"; done; done; }

# ---- 11. the rows -------------------------------------------------------------------------------------------------------------
usbcheck() {   # usbcheck <file> <KEY>: a USB radio (serial= or port= in its key) is still there after a frame, else STOP
  local v="RADIO_$2" b m bd sel; read -r b m bd sel <<< "${!v}"; case "$sel" in
    serial=*) must "$b" "$1" "grep -lxF -- '${sel#serial=}' /sys/bus/usb/devices/*/serial 2>/dev/null | wc -l";;
    port=*) must "$b" "$1" "test -e /sys/bus/usb/devices/${sel#port=}/idVendor && echo 1 || echo 0";; *) return 0;; esac
  [ "$MODE" = dry ] || [ "$(body "$1")" = 1 ] || stop "$2 ($b) is gone after a frame (brown-out / re-enumeration)"; }
frame_row() {   # frame_row <id> <box:KEY> <box:KEY> <power a,b> <flags>: the daemons of both radios, one nonce frame each way
  local id=$1 pa=${4%,*} pb=${4#*,} fl=$5 m ka=${2#*:} kb=${3#*:} ba=${2%%:*} bb=${3%%:*} ra rb e x n
  needs $(rforms "$id") || { notrun_row "$id" "form not confirmed: $NEEDS_MISSING"; return 0; }
  m=$(undo_mark) || stop "undo journal: unreadable (no mark)"; ra=$(getk "$ka"); rb=$(getk "$kb")
  started "$ba" "$id-start-a.txt" "daemon@$ra"; started "$bb" "$id-start-b.txt" "daemon@$rb"
  local qa="R$id-$ba-$ra" qb="R$id-$bb-$rb"; dpower "$ba" "$ra" "$qa"; dpower "$bb" "$rb" "$qb"
  local la lb f; la=$(body "PW-$qa.txt" | head -n 1); lb=$(body "PW-$qb.txt" | head -n 1)   # the parameters both ends must share
  for f in FREQ SF; do x=$(jfield "$la" $f) || stop "row $id: $ba:$ra: $x"; n=$(jfield "$lb" $f) || stop "row $id: $bb:$rb: $n"
    [ "$x" = "$n" ] || fail "incompatible: $f $x vs $n"; done
  data "row $id: FREQ and SF match ($(jfield "$la" FREQ), SF $(jfield "$la" SF)); BW, CR and the sync word are not in the daemon's STATUS: not compared"
  for e in "a $ba $ra $qa $pa" "b $bb $rb $qb $pb"; do set -- $e; [ "$5" = min ] || continue
    undo_push "$2" "$(form dset rid="$3" p="$(getk "P_$4" 10)")"; must "$2" "$id-set-$1.txt" "$(form dset rid="$3" p=$STICK_MIN)"; dpower "$2" "$3" "$4"
    [ "$MODE" = dry ] || [ "$(getk "P_$4")" = "$STICK_MIN" ] || stop "row $id: $2:$3 reads back '$(getk "P_$4")' dBm after POWER=$STICK_MIN: no TX"; done
  local sa sb; sa=$([ "$pa" = min ] && echo "$STICK_MIN dBm (minimum)" || echo "$(getk "P_$qa" as-is) dBm (as-is)"); sb=$([ "$pb" = min ] && echo "$STICK_MIN dBm (minimum)" || echo "$(getk "P_$qb" as-is) dBm (as-is)")
  for x in "A->B $qa $ba $ra $qb $bb $rb $sa" "B->A $qb $bb $rb $qa $ba $ra $sb"; do read -r d q1 b1 r1 q2 b2 r2 pw <<< "$x"; n=$(nonce)
    tx "$q1" 1 "$n" -- must "$b1" "$id-tx-$b1-$r1.txt" "$(form tx_text rid="$r1" n="$n")"
    usbcheck "$id-usb-$b1-$r1-a.txt" "$ka"; usbcheck "$id-usb-$b1-$r1-b.txt" "$kb"
    boxrx "$b2" "$id-rx-$b2-$r2.txt" rx_rf "$r2" "$n" 30 || fail "not heard: $n"
    REPORT "$id" daemon "$b1 ($r1) -> $b2 ($r2)" "$(v="RADIO_$ka"; set -- ${!v}; echo "$3")" "$b2 ($r2)" "$d" "yes ('$n' RX on $b2:$r2)" "$pw" PASS; done
  if [ "$fl" = same-box ]; then   # one box: stopping one radio's daemon leaves the other's running (its pid and start time)
    pid() { echo "p=\$(pgrep -f 'loraham[_]daemon.*--id $1( |\$)' | head -n 1); [ -n \"\$p\" ] || { echo NO-PID $1; exit 3; }; echo \$p \$(awk '{print \$22}' /proc/\$p/stat)"; }
    must "$ba" "$id-pa0.txt" "$(pid "$ra")"; must "$ba" "$id-stop-b.txt" "$(form stop inst="daemon@$rb")"; must "$ba" "$id-pa1.txt" "$(pid "$ra")"
    [ "$MODE" = dry ] || eq "row $id: daemon@$ra keeps its pid and start time when $rb stops" "$(body "$id-pa1.txt")" "$(body "$id-pa0.txt")"
    must "$ba" "$id-restart-b.txt" "$(form start inst="daemon@$rb")"; must "$ba" "$id-pb0.txt" "$(pid "$rb")"; must "$ba" "$id-stop-a.txt" "$(form stop inst="daemon@$ra")"; must "$ba" "$id-pb1.txt" "$(pid "$rb")"
    [ "$MODE" = dry ] || eq "row $id: daemon@$rb keeps its pid and start time when $ra stops" "$(body "$id-pb1.txt")" "$(body "$id-pb0.txt")"
    must "$ba" "$id-restart-a.txt" "$(form start inst="daemon@$ra")"
    REPORT "$id" daemon "$ba ($ra, $rb)" "$(v="RADIO_$ka"; set -- ${!v}; echo "$3")" — stop-isolation "stop $rb: $ra runs on; stop $ra: $rb runs on" — PASS; fi
  undo_to "$m"; }

row_1() {   # MeshCom box radio 433.175 <-> the MeshCom T-Deck (receive only: its TX power is not provable over its serial answers)
  local b=${1%%:*} k=${1#*:}; notrun 1 peer-\>box "the T-Deck's TX power is not provable over its serial answers: it only receives"
  needs $(rforms 1) || { notrun 1 box-\>peer "form not confirmed: $NEEDS_MISSING"; return 0; }
  local m r n a src pr; m=$(undo_mark) || stop "undo journal: unreadable (no mark)"; r=$(getk "$k"); n=$(nonce)
  started "$b" 1-daemon.txt "daemon@$r"; dpower "$b" "$r" "R1-$b-$r-d"   # the daemon's STATUS before the stack's own start
  started "$b" 1-start.txt "meshcom@$r"; dpower "$b" "$r" "R1-$b-$r"
  X "$b" 1-provision.txt "$(printf "$PROVWAIT" "$r")"; [ "$MODE" = dry ] || [ "$XRC" = 0 ] || fail "row 1: the node's provisioning check did not finish ($(tail -n 1 "$OUT/1-provision.txt"))"
  BG=1 peer 1-peer-read.txt "$VMT" meshcom-read "$n" 150; pr=$SPAWN; [ "$MODE" = dry ] || sleep 8
  for a in 1 2 3; do [ "$a" = 1 ] || [ "$MODE" = dry ] || sleep 5   # at most 3 console attempts, one tx each (dry rehearses all three)
    tx "R1-$b-$r" 1 "$n" -- XIN "$b" "1-box-send-$a.txt" "$MCSEND_PY" "python3 - '$n' $(rport 12323 "$r")"; src=$XRC
    [ "$MODE" = dry ] || [ "$src" != 0 ] || break; done
  reap "$pr"
  [ "$MODE" = dry ] || [ "$src" = 0 ] || fail "row 1 box -> peer: the node's console did not take '$n' (rc $src: 4 = no banner, 3 = no echo)"
  result 1-peer-read.txt "$n" || fail "not heard: $n"
  REPORT 1 meshcom "$b ($r)" "433.175 MHz" "MeshCom T-Deck" "box -> peer" yes "$(getk "P_R1-$b-$r" as-is) dBm (as-is)" PASS
  undo_to "$m"; }

row_2() {   # KISS + Graywolf box radio 433.775 <-> the APRS tracker (as-is): box -> peer = the message on the tracker; peer -> box = its ACK
  local b=${1%%:*} k=${1#*:}
  needs $(rforms 2) || { notrun_row 2 "form not confirmed: $NEEDS_MISSING"; return 0; }
  [ -n "$TRACKER_CALL" ] || { notrun_row 2 "no tracker call-SSID given at GO (TRACKER_CALL)"; return 0; }
  [ -n "$TRACKER_DBM" ] || { notrun_row 2 "no tracker power given at GO (TRACKER_DBM, from its portal): its ACK is a TX"; return 0; }
  local m r n pr grc; m=$(undo_mark) || stop "undo journal: unreadable (no mark)"; r=$(getk "$k"); n=$(nonce)
  started "$b" 2-daemon.txt "daemon@$r"; dpower "$b" "$r" "R2-$b-$r-d"
  started "$b" 2-start.txt "graywolf@$r"; undo_push "$b" "$(gstop "kiss@$r")"; dpower "$b" "$r" "R2-$b-$r"
  BG=1 peer 2-peer-read.txt "$VMT" aprs-read "$n" 150; pr=$SPAWN; [ "$MODE" = dry ] || sleep 8
  keep P_R2-tracker "$TRACKER_DBM"; radk R2-tracker PC:tracker; pwok PC:tracker   # the tracker's power from its portal (as-is)
  tx R2-tracker 1 control --reserve                                                # its ACK, which the message triggers
  tx "R2-$b-$r" 1 "$n" -- XIN "$b" 2-box-send.txt "TO = '$TRACKER_CALL'"$'\n'"$GW_PY" "{RT}/venv/lhpc/bin/python - '$n' $(rport 8080 "$r") $r"; grc=$XRC
  reap "$pr"
  [ "$MODE" = dry ] || grep -q '^SENT ' "$OUT/2-box-send.txt" || fail "row 2 box -> peer: the Graywolf send failed (rc $grc): no message was sent"
  result 2-peer-read.txt "$n" || fail "not heard: $n"
  REPORT 2 "kiss + graywolf (APRS)" "$b ($r)" "433.775 MHz" "the APRS tracker" "box -> peer" yes "$(getk "P_R2-$b-$r" as-is) dBm (as-is)" PASS
  [ "$MODE" = dry ] || { [ "$grc" = 0 ] && grep -q '^ACKED ' "$OUT/2-box-send.txt"; } || fail "row 2 peer -> box: no ACK: call not verified ('$n', graywolf rc $grc)"
  if [ "$MODE" != dry ]; then
    ! grep -qx CALL-DIFF "$OUT/2-box-send.txt" || fail "row 2 peer -> box: the ACK frame's source differs from the given call (not the tracker's ACK)"
    grep -qx CALL-MATCH "$OUT/2-box-send.txt" || fail "row 2 peer -> box: Graywolf has '$n' acked, but no ACK frame in the rf log"; fi
  REPORT 2 "kiss + graywolf (APRS)" "$b ($r)" "433.775 MHz" "the APRS tracker" "peer -> box" "the tracker's ACK (acked in Graywolf; the frame's source = the given call)" "the tracker's $TRACKER_DBM dBm, from its portal (not read)" PASS
  undo_to "$m"; }

row_3() {   # Meshtastic box radio 868 (EU_868 LongFast) <-> the Station G2 (set to 1, its lowest, restored after)
  local b=${1%%:*} k=${1#*:}
  needs $(rforms 3) || { notrun_row 3 "form not confirmed: $NEEDS_MISSING"; return 0; }
  local m r n1 n2 cap ap wp got pr; m=$(undo_mark) || stop "undo journal: unreadable (no mark)"; r=$(getk "$k"); n1=$(nonce); n2=$(nonce)
  must "$b" 3-cap.txt "$(form mt_cap rid="$r")"; cap=$(body 3-cap.txt | tail -n 1)   # the cap every frame of the node is clamped to
  [ "$MODE" = dry ] || jcap "$cap" || stop "row 3: meshtastic@$r max_power '$cap' is not 1..17: nothing starts"
  started "$b" 3-start.txt "meshtastic@$r"
  ap=$(rport 4403 "$r"); wp=$(rport 9443 "$r")
  must "$b" 3-ready.txt "for i in \$(seq 1 30); do (exec 3<>/dev/tcp/127.0.0.1/$ap) 2>/dev/null && { echo READY $ap; exit 0; }; sleep 1; done; echo NOT-READY $ap; exit 3"
  must "$b" 3-listen.txt "ss -Htlnp | grep -F '\"meshtasticd\"' || true"
  [ "$MODE" = dry ] || { got=$(body 3-listen.txt | mt_ports); [ "$got" = "$(printf '%s\n' "$ap" "$wp" | sort -n | tr '\n' ' ')" ] \
    || fail "row 3: meshtastic@$r's meshtasticd listens on '$got', want exactly '$ap $wp' (its own API and web ports)"; ok "row 3: meshtastic@$r binds $ap + $wp, ready on $ap"; }
  spower "$b" mt_pget "$r" "R3-$b-$r"
  ppower R3-g2 g2 mt-power 1
  tx R3-g2 1 "$n1" -- peer 3-peer-send.txt "$VMT" mt-send "$n1"; [ "$XRC" = 0 ] || stop "row 3: the G2 send failed"
  boxrx "$b" 3-box-rx.txt rx_mt "$r" "$n1" 90 || fail "not heard: $n1"
  REPORT 3 meshtastic "$b ($r)" "868, EU_868 LongFast" "Station G2" "peer -> box" "yes (RX on $r)" "the G2 at 1 (lowest)" PASS
  BG=1 peer 3-peer-read.txt "$VMT" mt-read "$n2" 90; pr=$SPAWN; [ "$MODE" = dry ] || sleep 10
  tx "R3-$b-$r" 1 "$n2" -- must "$b" 3-box-send.txt "$(form mt_send rid="$r" n="$n2")"; reap "$pr"
  result 3-peer-read.txt "$n2" || fail "not heard: $n2"
  REPORT 3 meshtastic "$b ($r)" "868, EU_868 LongFast" "Station G2" "box -> peer" yes "$(getk "P_R3-$b-$r" as-is) dBm (as-is)" PASS
  undo_to "$m"; }

row_4() {   # MeshCore box radio 869.618 (eu_uk_narrow) <-> the T-Deck Pro (BLE; its power is read, never changed)
  local b=${1%%:*} k=${1#*:}
  needs $(rforms 4) || { notrun_row 4 "form not confirmed: $NEEDS_MISSING"; return 0; }
  local m r n1 n2 cp tp pr; m=$(undo_mark) || stop "undo journal: unreadable (no mark)"; r=$(getk "$k"); n1=$(nonce); n2=$(nonce)
  started "$b" 4-daemon.txt "daemon@$r"; dpower "$b" "$r" "R4-$b-$r-d"
  started "$b" 4-start.txt "meshcore@$r"
  must "$b" 4-cport.txt "cd {RT} && ./venv/lhpc/bin/python -c '$CPORT_PY' meshcore@$r"; cp=$(rport 5000 "$r")
  [ "$MODE" = dry ] || eq "row 4: meshcore@$r's own companion endpoint is the derived port" "$(body 4-cport.txt | sed -n 's/^CPORT //p')" "$cp"
  must "$b" 4-listen.txt "ss -Htlnp 'sport = :$cp'"; [ "$MODE" = dry ] || check_listener 4-listen.txt "$cp"
  must "$b" 4-listener.txt "ps -o pgid= -p ${LP:-0} | tr -d ' '"; [ "$MODE" = dry ] || body 4-cport.txt | grep -qx "OWNED $(body 4-listener.txt)" \
    || stop "row 4: the listener pid $LP on $cp is not in a live owned meshcore-node group of meshcore@$r"
  spower "$b" mc_pget "$r" "R4-$b-$r"
  peerpin 4-peer-power.txt mc-power; [ "$XRC" = 0 ] || stop "row 4: the T-Deck Pro power read failed (rc $XRC)"   # a READ, never a write
  tp=$(body 4-peer-power.txt | sed -n 's/^POWER //p' | tail -n 1)
  [ "$MODE" = dry ] || { [[ "$tp" =~ ^-?[0-9]+$ ]] && [ "$tp" -le 17 ]; } || stop "row 4: the T-Deck Pro reads '${tp}' dBm (above 17 or unreadable); its power is never changed: no TX"
  keep P_R4-tdpro "${tp:-dry}"; radk R4-tdpro PC:tdpro; pwok PC:tdpro; data "row 4: the T-Deck Pro at its own ${tp:-dry} dBm, not changed"
  tx R4-tdpro 1 "$n1" -- peerpin 4-peer-send.txt mc-send "$n1"; [ "$XRC" = 0 ] || stop "row 4: the T-Deck Pro send failed"
  boxrx "$b" 4-box-rx.txt rx_mc "$r" "$n1" 90 || fail "not heard: $n1"
  REPORT 4 meshcore "$b ($r)" "869.618 MHz, eu_uk_narrow" "T-Deck Pro (BLE companion)" "peer -> box" "yes (RX on $r)" "the T-Deck Pro at its own ${tp:-dry} dBm (not changed)" PASS
  BG=1 peerpin 4-peer-read.txt mc-read "$n2" 90; pr=$SPAWN; [ "$MODE" = dry ] || sleep 15
  tx "R4-$b-$r" 1 "$n2" -- must "$b" 4-box-send.txt "$(form mc_send rid="$r" n="$n2")"; reap "$pr"
  result 4-peer-read.txt "$n2" || fail "not heard: $n2"
  REPORT 4 meshcore "$b ($r)" "869.618 MHz, eu_uk_narrow" "T-Deck Pro (BLE companion)" "box -> peer" yes "$(getk "P_R4-$b-$r" as-is) dBm (as-is)" PASS
  undo_to "$m"; }

# the MeshChat port from the start's own ready line ("[verified] meshchat: started; ready endpoint(s) up (127.0.0.1:<port>: present")
mcport() { body "$1" | sed -nE 's/.*\[verified\] meshchat: started; ready endpoint\(s\) up \(127\.0\.0\.1:([0-9]+): present.*/\1/p' | head -n 1; }
row_5() {   # Reticulum + MeshChat box radio 868.5 SF8 <-> the RNode through the PC's shared rnsd (<= 7 dBm)
  local b=${1%%:*} k=${1#*:}
  needs $(rforms 5) || { notrun_row 5 "form not confirmed: $NEEDS_MISSING"; return 0; }
  local m r n1 n2 o pr mp dest prc; m=$(undo_mark) || stop "undo journal: unreadable (no mark)"; r=$(getk "$k"); n1=$(nonce); n2=$(nonce)
  local q="R5-$b-$r" go=$SCRATCH/5-tx2.go pcd=$SCRATCH/5-pcdest.txt   # the peer's control files: private scratch, never the evidence
  must "$b" 5-framing0.txt "$(form rt_get rid="$r" k=rnode_framing)"; o=$(body 5-framing0.txt | tail -n 1)
  [ "$MODE" = dry ] || case "$o" in yes|no) ;; *) stop "row 5: rnode_framing read '$o'";; esac
  undo_push "$b" "$(form rt_set rid="$r" k=rnode_framing v="${o:-no}")"; must "$b" 5-framing1.txt "$(form rt_set rid="$r" k=rnode_framing v=yes)"
  spower "$b" rt_pget "$r" "$q"; undo_push "$b" "$(gstop "reticulum@$r")"; started "$b" 5-start.txt "meshchat@$r"
  hpower R5-rnode 5-rnode-power.txt
  # 1. launch: the exchange announces now, waits for the box's announce, and holds its message until the release file exists
  BG=1 tx R5-rnode 1 control -- peer 5-peer.txt "$VRNS" rns-exchange "$n2" "${RT_BOXNAME:-LHPC}" 240 "$pcd" "$n1" "$go"; pr=$SPAWN
  mp=$(mcport 5-start.txt); [ "$MODE" = dry ] && mp=8790
  [[ "$mp" =~ ^[0-9]{2,5}$ ]] || stop "row 5: no MeshChat ready endpoint port in 5-start.txt"
  [ "$MODE" = dry ] || sleep 10
  tx "$q" 1 control -- must "$b" 5-announce.txt "LHPC_INST=meshchat@$r curl -fsS --max-time 15 http://127.0.0.1:$mp/api/v1/announce"   # 3. the box's announce
  # 2. release, every check right before the second phase: the RNode's power again, its path request, the box's delivery proof
  hpower R5-rnode 5-rnode-power-2.txt; tx R5-rnode 1 control --reserve; tx "$q" 1 control --reserve
  tx R5-rnode 1 "$n1" -- X PC 5-tx2-go.txt "LHPC_PROOF=$b:meshchat@$r printf '%s\n' '$n1' > $go"
  [ "$MODE" = live ] || printf '%s\n' "$n1" > "$go" || stop "row 5: the dry release file"   # dry: right after its tx
  if needs rt_send; then [ "$MODE" = dry ] || sleep 60; dest=$(cat "$pcd" 2>/dev/null || echo dry)
    [ "$MODE" = dry ] || [[ "$dest" =~ ^[0-9a-f]{32}$ ]] || stop "row 5: no PC destination hash"
    tx "$q" 1 "$n2" -- must "$b" 5-box-send.txt "$(form rt_send rid="$r" dest="$dest" n="$n2")"; fi
  reap "$pr"; prc=$XRC
  [ "$MODE" = dry ] || grep -qxE "DELIVERED $n1 .*" "$OUT/5-peer.txt" || fail "row 5 peer -> box: no delivery proof of '$n1' from the box (5-peer.txt)"
  boxrx "$b" 5-box-rx.txt rx_mchat "$r" "$n1" 30 || fail "not heard: $n1"
  REPORT 5 "reticulum + meshchat" "$b ($r)" "868.5 MHz, SF8" "the RNode (the PC's shared rnsd)" "peer -> box" "yes: LXMF delivered (the box's proof) and stored" "the RNode at $(getk P_R5-rnode unrecorded) dBm" PASS
  XRC=$prc; if needs rt_send; then result 5-peer.txt "$n2" || fail "not heard: $n2"
    REPORT 5 "reticulum + meshchat" "$b ($r)" "868.5 MHz, SF8" "the RNode (the PC's shared rnsd)" "box -> peer" yes "$(getk "P_$q" as-is) dBm (as-is)" PASS
  else notrun 5 box-\>peer "form not confirmed: rt_send"; fi
  undo_to "$m"; }

# the chat client's command from the start's own output (the line after "run it yourself in a terminal:"), in the measured form
chatcmd() { local cc re; if [ "$MODE" = dry ]; then cc="cd /dry/files/$2/ && /dry/LoRaHAM_Daemon/loraham_chat --data-socket /tmp/lora-$2.sock --conf-socket /tmp/loraconf-$2.sock; stty sane 2>/dev/null"
  else cc=$(body "$1" | grep -A1 -F 'run it yourself in a terminal:' | sed -n '2s/^ *//p'); fi
  re="^cd [^';&|]+ && [^';&| ]+/loraham_chat --data-socket [^' ]*/lora-$2[.]sock --conf-socket [^' ]*/loraconf-$2[.]sock; stty sane 2>/dev/null\$"
  echo "$cc"; [[ "$cc" =~ $re ]]; }
row_6() {   # chat: the chat CLIENT in the foreground of a tmux pane on each box, ready before typing; one line each way
  local ba=${1%%:*} bb=${2%%:*} ra rb; ra=$(getk "${1#*:}"); rb=$(getk "${2#*:}")
  needs $(rforms 6) || { notrun_row 6 "form not confirmed: $NEEDS_MISSING"; return 0; }
  local m na nb x b rr cc wt; m=$(undo_mark) || stop "undo journal: unreadable (no mark)"; na=$(nonce); nb=$(nonce)
  for x in "$ba $ra" "$bb $rb"; do read -r b rr <<< "$x"
    undo_push "$b" "tmux kill-session -t rfchat-$rr 2>/dev/null; $(gstop "chat@$rr") && $(gstop "daemon@$rr")"
    must "$b" "6-start-$b.txt" "$(form chat_client rid="$rr")"            # ensures the daemon, prints the client's command
    cc=$(chatcmd "6-start-$b.txt" "$rr") || stop "row 6: the chat client's command on $b is not the measured form: '$cc'"
    must "$b" "6-tmux-$b.txt" "tmux new-session -d -s rfchat-$rr -x 120 -y 30 '$cc; echo CLIENT-EXIT; sleep 600'"   # the pane stays
    wt=30; [ "$b" = Z ] && wt=90
    X "$b" "6-ready-$b.txt" "for i in \$(seq 1 $wt); do tmux capture-pane -p -t rfchat-$rr 2>/dev/null | grep -qF -- '$CHAT_READY' && pgrep -f '^[^ ]*/loraham_chat --data-socket [^ ]*/lora-$rr[.]sock' > /dev/null && { echo READY; exit 0; }; sleep 1; done; echo NOT-READY; exit 1"
    if [ "$MODE" != dry ] && [ "$XRC" != 0 ]; then X "$b" "6-pane-$b.txt" "tmux capture-pane -p -t rfchat-$rr 2>&1"
      fail "row 6: the chat client on $b ($rr) is not ready after $wt s (pane: 6-pane-$b.txt)"; fi
    dpower "$b" "$rr" "R6-$b-$rr"; done
  tx "R6-$ba-$ra" 1 "$na" -- must "$ba" "6-say-$ba.txt" "tmux send-keys -t rfchat-$ra -- '$na' Enter"
  boxrx "$bb" "6-rx-$bb.txt" rx_rf "$rb" "$na" 60 || fail "not heard: $na"
  REPORT 6 chat "$ba ($ra) -> $bb ($rb)" "433.775 MHz" "the other box" "box -> peer" "yes (RX on $bb:$rb)" "$(getk "P_R6-$ba-$ra" as-is) dBm (as-is)" PASS
  tx "R6-$bb-$rb" 1 "$nb" -- must "$bb" "6-say-$bb.txt" "tmux send-keys -t rfchat-$rb -- '$nb' Enter"
  boxrx "$ba" "6-rx-$ba.txt" rx_rf "$ra" "$nb" 60 || fail "not heard: $nb"
  REPORT 6 chat "$bb ($rb) -> $ba ($ra)" "433.775 MHz" "the other box" "peer -> box" "yes (RX on $ba:$ra)" "$(getk "P_R6-$bb-$rb" as-is) dBm (as-is)" PASS
  undo_to "$m"; }

runrow() {   # one row in a subshell; a FAIL (the marker) = recorded, its undos run, the run goes on; any other rc = STOP
  local r=$1 m rc sk d; sk=$(getk "SKIP_$r"); [ -z "$sk" ] || { notrun_row "$r" "$sk"; return 0; }
  m=$(undo_mark) || stop "undo journal: unreadable (no mark)"; usbguard "pre-$r"; : > "$OUT/rowfail.txt"
  if [ "$(rowf "$r" kind)" = frame ]; then ( CURROW=$r; frame_row "$r" "$(rowf "$r" a)" "$(rowf "$r" b)" "$(rowf "$r" power)" "$(rowf "$r" flags)" ); rc=$?
  else ( CURROW=$r; "row_$r" "$(rowf "$r" a)" "$(rowf "$r" b)" ); rc=$?; fi
  if [ "$rc" = 1 ] && [ -s "$OUT/rowfail.txt" ]; then
    for d in $(dirs "$r"); do grep -qF "$r|$(dirtext "$d")|" "$OUT/results.txt" 2>/dev/null && continue
      REPORT "$r" "$(rowname "$r")" — — — "$(dirtext "$d")" no — "FAIL: $(head -n 1 "$OUT/rowfail.txt")"; done
    [ "$MODE" = dry ] || echo "row $r: $(head -n 1 "$OUT/rowfail.txt")" >> "$EVD/FAILED.txt" || stop "FAILED.txt: append failed"
    say "FAIL: row $r (its undos run now; the next row follows)"; undo_to "$m" || stop "row $r: its undos did not all run (UNRESOLVED.txt)"
  elif [ "$rc" != 0 ]; then stop "row $r ended rc $rc (a STOP or a shell error, see above): the run ends"; fi
  usbguard "post-$r"; }

row_RS() {
  undo_to 0 || stop "RS: an undo failed (UNRESOLVED.txt): the recovery runs it again"; [ "$MODE" = dry ] || { [ ! -s "$EVD/UNRESOLVED.txt" ] || fail "RS: unresolved undo(s): $(tr '\n' '|' < "$EVD/UNRESOLVED.txt")"; }
  verify_set || fail "RS: the recorded running set is not back ($(tr '\n' '|' < "$EVD/UNRESOLVED.txt" 2>/dev/null))"
  [ "$MODE" = dry ] || { [ ! -s "$EVD/UNRESOLVED.txt" ] || fail "RS: unresolved after verify: $(tr '\n' '|' < "$EVD/UNRESOLVED.txt")"; }
  local b pr; for b in $BOXES; do must $b "RS-$b-tx.txt" "for d in {RT}/logs/r*; do echo \"\${d##*/} \$(cat \$d/rf-*.log 2>/dev/null | grep -c ' TX ')\"; done"
    must $b "RS-$b-boot.txt" "cat /proc/sys/kernel/random/boot_id"; [ "$MODE" = dry ] && continue; eq "RS $b: no reboot" "$(body "RS-$b-boot.txt")" "$(getk BOOT_$b)"
    awk 'NR == FNR {a[$1] = $2; next} {print $1, $2 - (a[$1] + 0)}' "$OUT/R0-$b-tx.txt" "$OUT/RS-$b-tx.txt" > "$OUT/RS-$b-tx-run.txt"
    data "RS $b: TX frames of this run per radio: $(tr '\n' '|' < "$OUT/RS-$b-tx-run.txt")"; awk '$2 > 15 {exit 1}' "$OUT/RS-$b-tx-run.txt" || fail "RS $b: a radio sent more than 15 frames in this run (duty)"; done
  usbguard RS
  for pr in $(sed -n 's/^TXR_\([^=]*\)=.*/\1/p' "$OUT/state.env" | awk '!s[$0]++' | sort); do say "BUDGET | ${pr/_/:} | $(getk "TXR_$pr") | $TXMAX"; done
  [ "$MODE" = dry ] || { [ ! -s "$EVD/NOT-RUN.txt" ] || say "NOT RUN, for the coordinator: $(tr '\n' '|' < "$EVD/NOT-RUN.txt")"; }; }
mt_ports() { awk '{print $4}' | sed -n 's/.*:\([0-9][0-9]*\)$/\1/p' | sort -un | tr '\n' ' '; }
check_listener() {
  local recs n pids; recs=$(body "$1" | grep -v '^[[:space:]]*$'); n=$(printf '%s\n' "$recs" | grep -c .)
  [ "$n" = 1 ] || stop "row 4: $n listening socket line(s) on 127.0.0.1:$2, want exactly one"
  pids=$(printf '%s\n' "$recs" | grep -oE 'pid=[0-9]+' | cut -d= -f2); [ "$(printf '%s\n' "$pids" | grep -c .)" = 1 ] \
    || stop "row 4: the socket on :$2 carries $(printf '%s\n' "$pids" | grep -c .) pid= (want exactly one)"
  LP=$pids; }
secret_scan() {
  scan() { local w=$1 rc=0; shift; grep -rlq "$@" "$EVD" || rc=$?; case $rc in 0) fail "RE: $w in the evidence";; 1) ok "RE: no $w";; *) stop "RE: the scan for $w failed (rc $rc)";; esac; }
  local pw; for pw in "${PW_H:-}" "${PW_Z:-}"; do [ -z "$pw" ] || scan "box password" -F -- "$pw"; done
  [ -z "${MC_PIN:-}" ] || scan "PIN" -F -- "$MC_PIN"
  scan "the operator's call (any SSID)" -iE -- "\\b$OPERATOR_CALL(-[0-9]{1,2})?\\b"; [ -z "${TRACKER_CALL:-}" ] || scan "tracker call" -iF -- "$TRACKER_CALL"
  scan "full node id" -E -- '![0-9a-f]{8}'; coords; }
coords() {   # a coordinate-shaped number in the evidence = FAIL, except a 'FREQ=' value (the daemon status line)
  local o rc c; o=$(grep -rhoE -- '(FREQ=)?-?[0-9]{1,3}\.[0-9]{5,}' "$EVD"); rc=$?; [ $rc -le 1 ] || stop "RE: the coordinate scan failed (rc $rc)"
  c=$(printf '%s\n' "$o" | grep -c -v -e '^FREQ=' -e '^$'); [ "$c" = 0 ] && ok "RE: no coordinate-shaped number (FREQ= values excepted)" || fail "RE: $c coordinate-shaped number(s) in the evidence"; }
row_RE() { [ "$MODE" = dry ] && { echo "  [dry] RE: secret and peer-data scans (grep 0 = hit = FAIL, 1 = clean, 2+ = STOP), SHA256SUMS, read-only"; return 0; }
  secret_scan
  ( cd "$EVD" && find . -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 sha256sum > SHA256SUMS && chmod -R a-w . ) || stop "RE: sums"; echo "evidence: $EVD"
  [ ! -s "$EVD/UNRESOLVED.txt" ] || fail "RE: the evidence is frozen, but recovery is unresolved: $(tr '\n' '|' < "$EVD/UNRESOLVED.txt")"; }


# ---- 12. the dry self-check (each line `dry self-check OK <name>`; a failure is a STOP) -------------------------------------
selfcheck() {
  local SCN=0; scok() { echo "dry self-check OK $1"; SCN=$((SCN + 1)); }; sc() { stop "dry self-check $1: $2"; }
  local n c t L G g p w st
  n=0; while IFS= read -r t; do ! hipower "$t" || n=$((n + 1)); done < <(grep '^CMD ' "$DRYLOG")
  [ "$n" = 0 ] || sc no-high-power "$n printed command(s) carry --high-power or a power of 18 dBm or more"; scok no-high-power
  c=$(grep '^CMD ' "$DRYLOG" | grep -ciE -- "\\b$OPERATOR_CALL(-[0-9]{1,2})?\\b") || true; [ "$c" = 0 ] || sc no-call-in-commands "the operator's call is in $c logged command(s)"
  [ -z "$TRACKER_CALL" ] || { c=$(grep -ciF -- "$TRACKER_CALL" "$DRYLOG") || true; [ "$c" = 0 ] || sc no-call-in-commands "the tracker call is in the command log ($c)"; }; scok no-call-in-commands
  n=$(grep '^CMD ' "$DRYLOG" | grep -c 'peer.py') || true; w=$(grep '^CMD ' "$DRYLOG" | grep -c 'flock -n -E 75') || true; p=$(grep '^CMD ' "$DRYLOG" | grep -c '(PIN on stdin)') || true
  [ $((w + p)) = "$n" ] || sc peer-calls-locked "$((n - w - p)) peer call(s) outside the lock"; scok peer-calls-locked
  # every FRAME is covered by an earlier TX of its radio (a '*' rid: any credit on that box), and every TX with an executor is
  # directly followed by that executor's command
  t=$(awk '$1 == "TX" {cr[$2] += $3; if ($5 != "reserve") want = NR + 1; next}
    $1 == "CMD" {want = 0; next}
    $1 == "FRAME" {r = $2; if (r ~ /:\*$/) {b = substr(r, 1, 2); r = ""; for (k in cr) if (index(k, b) == 1 && cr[k] > 0) {r = k; break}}
      if (r == "" || cr[r] < 1) {print "FRAME " $2 " (line " NR ") without a TX credit"; exit}; cr[r]--}
    want && NR == want && $1 != "CMD" {print "TX on line " NR - 1 " is not followed by its command"; exit}' "$DRYLOG")
  [ -z "$t" ] || sc every-send-gated "$t"; scok every-send-gated
  # the static twin: in every function of this script, a send form appears only on a tx line (`tx ... -- <executor> ...`)
  local fn; t=$(for fn in $(declare -F | awk '{print $3}'); do case $fn in dryframes|selfcheck|frame) continue;; esac
      declare -f "$fn" | grep -E "$SENDS_RE" | grep -vE '(^|[^a-zA-Z0-9_])tx [^|]* -- ' | sed "s/^ */$fn: /"; done)
  [ -z "$t" ] || sc sends-only-in-tx "a send outside tx: $(printf '%s' "$t" | head -n 3 | tr '\n' '|')"; scok sends-only-in-tx
  t=$(sort -u "$SCRATCH/needed" 2>/dev/null | grep -vxF -f <(sort -u "$SCRATCH/formed" 2>/dev/null) | grep -vx rt_send | tr '\n' ' ') || true
  [ -z "$t" ] || sc all-forms-printed "needed but never printed: $t"; scok all-forms-printed
  ( MODE=live; body() { printf '%s\n' "  [manual] loraham-chat is interactive — the daemon is ensured, then run it yourself in a terminal:" "    $CC"; }
    CC="cd /home/u/loraham-pi-control/config/files/r1/ && /home/u/loraham-pi-control/src/LoRaHAM_Daemon/loraham_chat --data-socket /tmp/lora-r1.sock --conf-socket /tmp/loraconf-r1.sock; stty sane 2>/dev/null"
    chatcmd x r1 > /dev/null || exit 1; ! chatcmd x r2 > /dev/null || exit 1
    CC="cd /x/ && /x/loraham_chat --data-socket /tmp/lora-r1.sock --conf-socket /tmp/loraconf-r1.sock; rm -rf ~; stty sane 2>/dev/null"; ! chatcmd x r1 > /dev/null || exit 1
    CC="cd /x/ && /x/lorachat --data-socket /tmp/lora-r1.sock --conf-socket /tmp/loraconf-r1.sock; stty sane 2>/dev/null"; ! chatcmd x r1 > /dev/null ) || sc chat-judge "the chat command judge"
  printf '%s\n' "$MCSEND_PY" | python3 -I -c 'import ast, sys; ast.parse(sys.stdin.read())' || sc chat-judge "the MeshCom send helper does not parse"; scok chat-judge
  ( d=$(mktemp -d "$SCRATCH/pw.XXXXXX") || exit 1; T=$(printf "$PROVWAIT" r9 | sed "s|{RT}/logs|$d|; s/seq 1 120/seq 1 2/; s/sleep 5/sleep 0/")
    pw() { local want=$1; shift; printf '%s\n' "$@" > "$d/start-meshcom-qemu-r9.log"; bash -c "$T" > /dev/null; [ $? = "$want" ]; }
    pw 0 "[run] node image: x" "[run] provisioning the node" "[run] provisioning verified" "[run] node image: y" "[run] web UI: z" \
    && pw 3 "[run] node image: x" "[run] provisioning the node" "[run] provisioning check: lacks" \
    && pw 3 "[run] node image: x" "[run] provisioning the node" "[run] provisioning FAILED: x" \
    && pw 0 "[run] node image: x" "[run] provisioning the node" "[run] provisioning verified" \
    && pw 3 "[run] QEMU: x" && rm "$d/start-meshcom-qemu-r9.log" && { bash -c "$T" > /dev/null; [ $? = 3 ]; } ) || sc provisioning-wait "the provisioning wait judge"
  printf "TO = 'N0CALL-9'\n%s\n" "$GW_PY" | python3 -I -c 'import ast, sys; ast.parse(sys.stdin.read())' || sc provisioning-wait "the Graywolf helper does not parse"; scok provisioning-wait
  ( MODE=live; pv() { local want=$1; shift; body() { printf '%s\n' "${PV_LINES[@]}"; }; PV_LINES=("$@"); [ "$(pnum x mc_pget)" = "$want" ]; }
    I1='INFO:meshcore:TCP Connection started'; I2='INFO:meshcore:Connected to Node running on a 14.0 (14) fw.'
    pv 14 "$I1" "$I2" 14 && pv "" "$I1" "$I2" 14 15 && pv "" 'WARNING:meshcore:x' 14 && pv "" "$I1" 'tx: 14' && pv "" 'INFO:asyncio:x' 14 ) || sc meshcore-power-read "the MeshCore power read"
  scok meshcore-power-read
  ( L='2026-11-15T02:26:23.127Z RX rssi=-56.00 snr=11.25 len=37 channel Public: SENDER: rfkit 250b74dd'; g=$(form rx_mc rid=r2 n='rfkit 250b74dd' | sed 's/^.*2>\/dev\/null | //')
    [ "$(printf '%s\n' "$L" | bash -c "$g" | grep -c .)" = 1 ] && [ "$(printf '%s\n' '2026-11-15T02:26:23.126Z RX rssi=-56.00 snr=11.25 len=37 hex=0a1b2c' | bash -c "$g" | grep -c .)" = 0 ] \
    && [ "$(printf '%s\n' '2026-11-15T02:26:23.127Z TX rssi=- len=37 channel Public: SENDER: rfkit 250b74dd' | bash -c "$g" | grep -c .)" = 0 ] ) || sc rx-mc-judge "the rx_mc judge"
  scok rx-mc-judge
  g='LISTEN 0 4096 0.0.0.0:4536 0.0.0.0:* users:(("meshtasticd",pid=7,fd=9))
LISTEN 0 4096 [::]:9576 [::]:* users:(("meshtasticd",pid=7,fd=10))'
  [ "$(printf '%s\n' "$g" | mt_ports)" = "4536 9576 " ] || sc row3-port-judge "mt_ports does not read r2's two ports"
  [ "$(printf '%s\n' "$g" 'LISTEN 0 4096 0.0.0.0:4403 0.0.0.0:* users:(("meshtasticd",pid=7,fd=11))' | mt_ports)" != "4536 9576 " ] || sc row3-port-judge "mt_ports hides a third port"
  [ "$(printf '' | mt_ports)" = "" ] && [ "$(rport 4403 r2) $(rport 9443 r2)" = "4536 9576" ] || sc row3-port-judge "mt_ports of no line / rport r2"; scok row3-port-judge
  t=$(mktemp -d "$SCRATCH/sc.XXXXXX") || sc state-helpers mktemp
  ( MODE=live; OUT=$t; printf 'RUN_H=\nX=a\nX=b\n' > "$OUT/state.env"
    [ "$(getk RUN_H dflt)" = "" ] && hask RUN_H && ! hask RUN_Z && [ "$(getk RUN_Z dflt)" = dflt ] && [ "$(getk X)" = b ] ) || sc state-helpers "getk/hask: an empty value or the last value is misread"
  st='[daemon] LoRaHAM daemon  (running)
[kiss] LoRaHAM KISS TNC  (running)
[graywolf] Graywolf APRS  (running)
[reticulum] Reticulum (RNS)  (running)
[controller] LoRaHAM Pi Control  (up to date)'
  [ "$(printf '%s\n' "$st" | ordkeys reticulum@r2 daemon@r10 graywolf@r1 daemon@r2 kiss@r1)" = "daemon@r2 daemon@r10 kiss@r1 graywolf@r1 reticulum@r2" ] || sc state-helpers "ordkeys: header order / rid order"
  ! printf '%s\n' "$st" | ordkeys meshcom@r1 daemon@r1 >/dev/null && [ "$(printf '%s\n' "$st" | ordkeys)" = "" ] || sc state-helpers "ordkeys places a key with no header / of no key"
  ( RUNSET='printf "kiss@r1\nkiss@r10\n"'; F[start]='echo START {inst}'; F[stop]='echo STOP {inst}'
    [ "$(bash -c "$(gstart kiss@r1)")" = "ALREADY-RUNNING kiss@r1" ] && [ "$(bash -c "$(gstart kiss@r2)")" = "START kiss@r2" ] \
    && [ "$(bash -c "$(gstop kiss@r10)")" = "STOP kiss@r10" ] && [ "$(bash -c "$(gstop daemon@r1)")" = "ALREADY-STOPPED daemon@r1" ] || exit 1
    RUNSET=false; bash -c "$(gstop kiss@r1)" >/dev/null; [ $? = 4 ] || exit 1; bash -c "$(gstart kiss@r1)" >/dev/null; [ $? = 4 ] ) || sc state-helpers "the guarded start/stop forms"
  scok state-helpers
  # the power guards: the judges on the STATUS line and on bad forms; dpower/spower/hpower on a stubbed box (every bad input = a
  # STOP); a model's cap; no power guard ends in NOT RUN; the power read before each stack start; the PIN not in a child's env
  L='STATUS RADIO=READY TX=0 TXMODE=MANAGED TXQUEUE=1 RXREADY=1 HIGHPOWER=0 CHIPFAMILY=SX127x OCP=100 FREQ=433.174988 SF=11 POWER=17'
  [ "$(jstatus "$L")" = 17 ] && ! jstatus "${L/HIGHPOWER=0/HIGHPOWER=1}" >/dev/null && ! jstatus "${L/ HIGHPOWER=0/}" >/dev/null \
    && ! jstatus "${L/ POWER=17/ POWER=18}" >/dev/null && ! jstatus "$L POWER=14" >/dev/null && ! jstatus "$L HIGHPOWER=0" >/dev/null \
    && ! jstatus "${L/ POWER=17/ POWER=x}" >/dev/null && ! jstatus "" >/dev/null && [ "$(jstatus "${L/ POWER=17/ POWER=-9}")" = -9 ] \
    && [ "$(jfield "$L" FREQ)" = 433.174988 ] && ! jfield "$L FREQ=1" FREQ >/dev/null && ! jfield "${L/ SF=11/}" SF >/dev/null || sc power-guards "the STATUS judges"
  G='ROW r1 loraham 433 True serial= port= hp=None
ROW r3 ch341-rfm98pw-433 433 True serial=X port= hp=False
ROW r4 pinedio-usb-1262 868 True serial= port=1-1 hp=True'
  jgrant "$G" r1 >/dev/null && jgrant "$G" r3 >/dev/null && ! jgrant "$G" r4 >/dev/null && ! jgrant "$G" r9 >/dev/null || sc power-guards "the high-power grant judge"
  jspower mt_pget 1 >/dev/null && ! jspower mt_pget 0 >/dev/null && jspower rt_pget 17 >/dev/null && ! jspower rt_pget 18 >/dev/null && ! jspower mc_pget 1x >/dev/null \
    && jcap 17 && ! jcap 18 && ! jcap 0 && dbm_ok 17 && ! dbm_ok 18 && ! dbm_ok '' || sc power-guards "the stack power / cap / dBm judges"
  ( MODE=live; OUT=$t; EVD=$t; : > "$OUT/state.env"; keep MODEL_H_r1 loraham; keep CAP_H_loraham 17; keep MODEL_H_r4 pinedio-usb-1262; keep CAP_H_pinedio-usb-1262 14
    RA='ROW r1 loraham 433 True serial= port= hp=None'
    X() { case $2 in HG-*) printf '%s\n' "STATE valid" "$DP_ROW" > "$OUT/$2";; PW-*D.txt) printf '%s\n' "$DP_ST" > "$OUT/$2";; PW-*) printf '%s\n' "$DP_V" > "$OUT/$2";; *) printf '%s\n' "$HP_OUT" > "$OUT/$2";; esac; XRC=${HP_RC:-0}; }
    dp() { DP_ROW=$1; DP_ST=$2; DP_V=$2; ( dpower H "${3:-r1}" T ) >/dev/null 2>&1; }
    sp() { DP_ROW=$RA; DP_ST=$2; DP_V=$3; ( spower H "$1" r1 K ) >/dev/null 2>&1; }
    hp() { HP_OUT=$1; HP_RC=$2; ( hpower R ) >/dev/null 2>&1; }
    dp "$RA" "$L" || exit 1; dp "${RA/hp=None/hp=True}" "$L"; [ $? = 2 ] || exit 1; dp "$RA" "${L/HIGHPOWER=0/HIGHPOWER=1}"; [ $? = 2 ] || exit 1
    dp "$RA" "${L/ POWER=17/ POWER=20}"; [ $? = 2 ] || exit 1; dp "${RA/r1/r2}" "$L"; [ $? = 2 ] || exit 1
    dp "ROW r4 pinedio-usb-1262 868 True serial= port=1-1 hp=None" "${L/ POWER=17/ POWER=15}" r4; [ $? = 2 ] || exit 1   # above its model's 14
    sp mc_pget "$L" 14 || exit 1; sp mc_pget "${L/HIGHPOWER=0/HIGHPOWER=1}" 14; [ $? = 2 ] || exit 1; sp mc_pget "$L" 18; [ $? = 2 ] || exit 1
    sp mt_pget "$L" 'lora.tx_power: 1' || exit 1; sp mt_pget "$L" 'lora.tx_power: 0'; [ $? = 2 ] || exit 1
    hp $'TXPOWER 7\nPID 1\nNEWER yes\nUP yes' 0 || exit 1; hp $'TXPOWER 8\nPID 1\nNEWER yes\nUP yes' 0; [ $? = 2 ] || exit 1
    hp $'TXPOWER 7\nPID 1\nNEWER no\nUP yes' 0; [ $? = 2 ] || exit 1; hp $'TXPOWER 7\nPID 1\nNEWER yes\nUP no' 0; [ $? = 2 ] || exit 1
    hp 'no read' 1; [ $? = 2 ] ) || sc power-guards "dpower/spower/hpower: a bad power, grant, cap or read is not a STOP"
  ! declare -f | grep -nE 'notrun[_a-z]* [0-9].*(power|HIGHPOWER|above (7|17))' | grep -v -e TRACKER_DBM -e 'serial answers' | grep -q . || sc power-guards "a power guard ends in NOT RUN"
  before() { case "$(declare -f "$1")" in *"$2"*"$3"*) return 0;; *) return 1;; esac; }
  before row_1 'R1-$b-$r-d' 1-start.txt && before row_2 'R2-$b-$r-d' 2-start.txt && before row_3 'form mt_cap' 3-start.txt && before row_4 'R4-$b-$r-d' 4-start.txt \
    || sc power-guards "a stack starts before its power read"
  before row_5 'tx R5-rnode 1 control --reserve' '5-tx2-go.txt' && before row_5 'tx "$q" 1 control --reserve' '5-tx2-go.txt' && before row_2 'tx R2-tracker 1 control --reserve' '2-box-send.txt' \
    || sc power-guards "an automatic send is not reserved before the send that triggers it"
  [ "$(bash -c 'printf %s "${MC_PIN-unset}${PW_H-unset}${PW_Z-unset}${OPERATOR_CALL-unset}"')" = unsetunsetunsetunset ] || sc power-guards "a secret is in a child's environment"
  scok power-guards
  # the dispatcher: each refusal is a STOP (rc 2) and runs nothing; a good call runs its executor once
  ( MODE=live; OUT=$t; EVD=$t; BOXES=; CURROW=9; : > "$OUT/state.env"; printf '%s\n' "rfkit 0000aaaa" > "$OUT/nonces"
    usbsnap() { :; }; must() { echo "RAN $*" >> "$OUT/ran"; XRC=0; }; : > "$OUT/ran"; : > "$OUT/R0-usb-byid.txt"; : > "$OUT/TXG-usb-byid.txt"; : > "$OUT/TXG-usb-kern.txt"
    keep RAD_K PC:g2; keep PWROW_PC_g2 9; keep TXR_PC_g2 9
    ( tx K 1 'rfkit 0000aaaa' -- must PC f "send 'rfkit 0000aaaa'" ) >/dev/null 2>&1 || exit 1; [ "$(wc -l < "$OUT/ran")" = 1 ] || exit 1
    ( tx K 1 'rfkit 0000aaaa' -- must PC f "send 'rfkit 0000aaaa'" ) >/dev/null 2>&1; [ $? = 2 ] || exit 1   # the 11th frame of the radio
    keep TXR_PC_g2 0
    ( tx Q 1 control -- must PC f x ) >/dev/null 2>&1; [ $? = 2 ] || exit 1                                  # an unresolved key
    ( tx K 1 control -- must PC f "x --high-power" ) >/dev/null 2>&1; [ $? = 2 ] || exit 1
    local pf; for pf in "set POWER=20" "--power 20" "--tx-power=20" "tx_power: 18" "txpower = 20" "--highpower"; do
      ( tx K 1 control -- must PC f "x $pf" ) >/dev/null 2>&1; [ $? = 2 ] || exit 1; done
    ( tx K 1 'rfkit 0000bbbb' -- must PC f "send 'rfkit 0000bbbb'" ) >/dev/null 2>&1; [ $? = 2 ] || exit 1   # not a nonce of this run
    ( tx K 1 'rfkit 0000aaaa' -- must PC f "send" ) >/dev/null 2>&1; [ $? = 2 ] || exit 1                     # the command lacks its nonce
    ( CURROW=8; tx K 1 control -- must PC f x ) >/dev/null 2>&1; [ $? = 2 ] || exit 1                         # no power read in this row
    ( tx K 1 nonce --reserve ) >/dev/null 2>&1; [ $? = 2 ] || exit 1; ( tx K 0 control --reserve ) >/dev/null 2>&1; [ $? = 2 ] || exit 1
    ( tx K 2 control --reserve ) >/dev/null 2>&1 || exit 1; [ "$(wc -l < "$OUT/ran")" = 1 ] && [ "$(getk TXR_PC_g2)" = 2 ] ) || sc tx-dispatch "a refused send ran, or a good one did not"
  scok tx-dispatch
  echo "dry self-check: all $SCN OK"; }
[ "${RFPROOF_SOURCE_ONLY:-0}" = 1 ] && return 0   # tests: the functions and constants only; no args, stdin, lab file, box or peer

# ==== the run ==================================================================================================================
MODE=${1:-dry}; case "$MODE" in dry|live) ;; *) echo "usage: rf-proof.sh dry|live" >&2; exit 2;; esac
# the lab file (a key set in the environment wins)
LAB_ENV=${LAB_ENV:-$HERE/lab.$([ "$MODE" = dry ] && echo example || echo local).env}
[ -f "$LAB_ENV" ] || stop "no lab file $LAB_ENV (copy lab.example.env to lab.local.env and fill it in)"
! grep -qiE '^[[:space:]]*(export[[:space:]]+)?([A-Za-z0-9]+_)*(PASS|PASSWD|PASSWORD|PW|PIN|SECRET)(_[A-Za-z0-9]+)*=' "$LAB_ENV" \
  || stop "$LAB_ENV sets a *PASS*/*PIN*/*SECRET* variable: secrets come on stdin only"
LABKEYS='^(BOX_[HZ]_(HOST|USER|RUNTIME)|RADIO_[A-Z0-9]+|PEER_[a-z0-9]+|OPERATOR_CALL|VENV_MT|VENV_RNS|RNS_CONFIG|PEER_LOCK|EVD_ROOT|DRY_[A-Za-z0-9_]+)$'
_saved=$(for _v in $(compgen -v); do [[ $_v =~ $LABKEYS ]] && [ -n "${!_v+x}" ] && declare -p "$_v"; done)
. "$LAB_ENV" || stop "$LAB_ENV: not readable"
eval "$_saved"; unset _saved _v
for b in H Z; do for v in USER:lhpc RUNTIME:'~/loraham-pi-control'; do n="BOX_${b}_${v%%:*}"; [ -n "${!n:-}" ] || declare "$n=${v#*:}"; done; done
OPERATOR_CALL=${OPERATOR_CALL:-}; [[ "$OPERATOR_CALL" =~ ^[A-Z0-9]{3,8}$ ]] || stop "OPERATOR_CALL is not a callsign without an SSID (3..8 of A-Z 0-9)"
declare +x OPERATOR_CALL
for v in VENV_MT VENV_RNS RNS_CONFIG PEER_LOCK EVD_ROOT; do [ -n "${!v:-}" ] || stop "$v is not set in $LAB_ENV"; done
VMT=$(pcpath "$VENV_MT")/bin/python; VRNS=$(pcpath "$VENV_RNS")/bin/python; RNSCFG=$(pcpath "$RNS_CONFIG"); LOCK=$(pcpath "$PEER_LOCK")
EVDROOT=$(pcpath "$EVD_ROOT"); case "$EVDROOT" in /tmp|/tmp/*) stop "EVD_ROOT is under /tmp: the evidence lives under /home";; /*) ;; *) stop "EVD_ROOT is not an absolute path";; esac
. "$HERE/zstatus.sh" || stop "zstatus.sh"
for v in $(compgen -v PEER_); do [[ $v =~ ^PEER_[a-z0-9]+$ ]] && export "$v"; done   # peer.py reads its devices from these
[[ "$NPREFIX" =~ ^[a-z]{2,6}$ ]] || stop "NONCE_PREFIX: 2..6 of a-z"
# the rows of this run (the table order)
LANE=${LANE:-fast}; case "$LANE" in fast|full) ;; *) stop "LANE=fast|full";; esac
if [ -n "${ROWS_ONLY:-}" ]; then for r in $ROWS_ONLY; do [ -n "${RROW[$r]:-}" ] || stop "ROWS_ONLY: '$r' is not a row id"; done
  [ -z "$(printf '%s\n' $ROWS_ONLY | sort | uniq -d)" ] || stop "ROWS_ONLY names a row twice"
  RUN=$(for r in $RIDS; do case " $ROWS_ONLY " in *" $r "*) echo "$r";; esac; done | tr '\n' ' ')
else RUN=$(for r in $RIDS; do [ "$LANE" = full ] || [ "$(rowf "$r" lane)" = fast ] && echo "$r"; done | tr '\n' ' '); fi
RUN=${RUN% }
# stdin: the box passwords, then the PIN (live only), read once, then closed
unset MC_PIN PW_H PW_Z
if [ "$MODE" = live ]; then
  IFS= read -r PW_H || PW_H=; IFS= read -r PW_Z || PW_Z=
  case " $RUN " in *" 4 "*) IFS= read -r MC_PIN || MC_PIN=;; *) MC_PIN=;; esac
else PW_H=; PW_Z=; MC_PIN=; fi
declare +x PW_H PW_Z MC_PIN; exec </dev/null
for b in H Z; do p="PW_$b"; [ -n "${!p}" ] || continue   # a password inside a fixed name could not be masked or scanned: STOP before anything
  case " lhpc $(boxvar H USER) $(boxvar Z USER) " in *"${!p}"*) stop "box $b's password is part of a login name ('lhpc' or BOX_<B>_USER): it cannot be masked in the evidence; set another password";; esac; done
BOXES=; declare -A ABSENT=()
for b in H Z; do h="BOX_${b}_HOST"; p="PW_$b"
  if [ "$MODE" = dry ]; then [ -n "${!h:-}" ] && BOXES="$BOXES $b" || ABSENT[$b]="absent: box $b"
  elif [ -z "${!p}" ]; then ABSENT[$b]="absent: box $b"
  else [ -n "${!h:-}" ] || stop "BOX_${b}_HOST is not set, but box $b has a password line"; BOXES="$BOXES $b"; fi; done
BOXES=${BOXES# }
INTHEAD=${INTHEAD:-}; INTCI=${INTCI:-}; [ "$MODE" = live ] || { INTHEAD=${INTHEAD:-0000000000000000000000000000000000000000}; INTCI=${INTCI:-dry}; }
TRACKER_CALL=${TRACKER_CALL:-}; TRACKER_DBM=${TRACKER_DBM:-}; [ "$MODE" = live ] || { TRACKER_CALL=${TRACKER_CALL:-$OPERATOR_CALL-9}; TRACKER_DBM=${TRACKER_DBM:-10}; }
declare +x TRACKER_CALL
[ -z "$TRACKER_DBM" ] || dbm_ok "$TRACKER_DBM" || stop "TRACKER_DBM is not a power value <= 17 dBm"
[ -z "$TRACKER_CALL" ] || [[ "$TRACKER_CALL" =~ ^[A-Z0-9]{3,8}-[0-9]{1,2}$ ]] || stop "TRACKER_CALL is not a call-SSID"
# the forms: dry confirms every form (every row prints all its commands); live needs every form of the chosen rows
if [ "$MODE" = dry ]; then FORMS_OK="${!F[*]}"; FORMS_HEAD=$INTHEAD
else FORMS_OK=${FORMS_OK:-}; FORMS_HEAD=${FORMS_HEAD:-}; [ -n "$FORMS_OK" ] || stop "FORMS_OK is empty: confirm the forms at INTHEAD first"
  miss=$(for r in $RUN; do for f in $(rforms "$r"); do case " $FORMS_OK " in *" $f "*) ;; *) echo "$f";; esac; done; done | sort -u | tr '\n' ' ')
  [ -z "$miss" ] || stop "form not confirmed: ${miss% } (FORMS_OK must hold every form of the chosen rows)"; fi
SCRATCH=$(mktemp -d "${TMPDIR:-/tmp}/rf-proof.XXXXXX") || stop "mktemp"; DRYLOG=$SCRATCH/commands.log; : > "$DRYLOG"
if [ "$MODE" = dry ]; then OUT=$SCRATCH/out; EVD=$EVDROOT/rf-proof-dry; mkdir -p "$OUT" || stop "scratch"
else EVD=$EVDROOT/rf-proof-$(date -u +%Y%m%dT%H%M%SZ)-$LANE; OUT=$EVD; fi
trap 'rc=$?; [ "$rc" = 0 ] || recover; rm -rf "$SCRATCH"; exit $rc' EXIT; trap 'exit 130' INT TERM

[ "$MODE" = dry ] || echo "LIVE: this run transmits. It needs the transmit and lab permission and the GO for this run, with the hardware connected, reachable and set up."
for r in R0 R1; do echo "== ROW $r"; "row_$r"; done
for r in $RUN; do echo "== ROW $r"; runrow "$r"; done
echo "== ROW RS"; row_RS; coverage; echo "== ROW RE"; row_RE
[ "$MODE" = live ] || selfcheck
trap - EXIT; rm -rf "$SCRATCH"
if [ "$MODE" = live ] && [ -s "$EVD/FAILED.txt" ]; then echo "FAIL rows, for the coordinator: $(tr '\n' '|' < "$EVD/FAILED.txt")" >&2; exit 1; fi
echo "DONE ($MODE)"
