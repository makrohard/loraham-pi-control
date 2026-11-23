#!/bin/bash
# rf-proof.sh dry|live: the hardware test line. Every radio that is present sends and receives a nonce frame through the
# daemon (RF fast), and every stack goes on air with its real peer (RF full). It reads the lab from lab.local.env (the dry
# run: lab.example.env with its simulated bench) and the box passwords (and the MeshCore PIN) from stdin.
#
# RUN:    LANE=fast|full|rns bash rf-proof.sh dry                      (first; no box, no peer, no TX)
#         printf '%s\n' "$PW_H" "$PW_Z" ["$PIN"] | LANE=... INTHEAD=... INTCI=... FORMS_OK=... FORMS_HEAD=... \
#           bash rf-proof.sh live 2>&1 | tee <log under /home>
#         A Ctrl-C that also ends tee does not cut the recovery (the script ignores SIGPIPE): the evidence directory (undo
#         journal, RESULT.md, UNRESOLVED.txt) is the record.
# A live run needs the transmit and lab permission and the GO for that run, with the hardware connected and set up.
#
# INPUTS (env): LANE (fast|full|rns, default fast); ROWS_ONLY (row ids, overrides the lane); LAB_ENV (the lab file); INTHEAD (the
#  Zero's head, 40 hex), INTHEAD_H (box H's head, default INTHEAD), INTCI (a green CI run of INTHEAD); FORMS_OK (the confirmed
#  form names at FORMS_HEAD = INTHEAD); TRACKER_CALL (row 2: the tracker's call-SSID, on stdin to the box only); TRACKER_DBM
#  (row 2: the tracker's power from its portal); TRACKER_ASIS (1: row 2 runs the tracker as-is, no TRACKER_DBM, no power check;
#  his word 11-18); NONCE_PREFIX (default rfkit); MCSET (on|off, the pre-step M; default on in
#  LANE=full); MCSET_J1 (M's row-1 callsign[-SSID] or 'global'; on the box's stdin only). Dry takes placeholders for INTHEAD/INTCI.
# STDIN (live only, read once before R0, then closed): line 1 box H's password, line 2 the Zero's; an EMPTY line = that box is
#  ABSENT (its rows NOT RUN, no ssh). Line 3 = the MeshCore PIN, only when row 4 runs (empty = the stored bond).
#
# SAFETY (every check below is a STOP, never NOT RUN; absent hardware is NOT RUN, never PASS):
#  - ONE dispatcher `tx` for every send. It checks the command (no --high-power, no power >= 18), the radio's power read in
#    this row (a box radio <= min(17, its model's power_default_dbm) with HIGHPOWER=0 and no high-power grant; a peer low or one
#    of the as-is peers), the message (the row's nonce, <= 16 characters), the budget (TXMAX frames per physical radio per
#    run, reserved at the worst case), both boxes' boot ids and the USB lists; then it runs the send itself.
#  - R0 sums the worst cases of the chosen rows per physical radio; a sum above TXMAX is a STOP before any TX.
#  - ONE power domain (pdom), 0..17 dBm, for every power read, set and restore: a pre-state outside it STOPs the row before
#    any change. The guard at X/XIN/peer: no power token on stdin in any context; outside pset/pread no power token at all;
#    under pset/pread (PCTX) only that call's own rendered command (PAUTH), and no value that is not a literal number.
#  - Every change pushes its undo first; a row FAIL runs its undos and the run goes on; a STOP or an interrupt runs all.
#  - The USB guard before and after each row; one peer at a time under the peer lock (two peers on one hub never transmit
#    at once); each radio's physical frames of this run, each counted once, at most TXMAX (RS; TXC_PY + txjudge: a count
#    that cannot be proven complete fails).
#  - Every capture goes through the one redactor before it is written (the passwords, the PIN, the operator's call with any
#    SSID); the RX judges write marker lines only; the end scan (RE) checks the evidence again. A box password EXACTLY equal
#    to that box's login name (a lab box) is neither masked nor scanned: REPORT.md's header says so (pweq).
# EXIT: 0 = every run row PASS or NOT RUN; 1 = a FAIL; 2 = a STOP. Dry exits 0 or 2.
set -uo pipefail
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd) || exit 2; REPO=$(cd "$HERE/../.." && pwd) || exit 2
readonly PCAP=17 TXMAX=10   # THE power cap (dBm) and the frames per physical radio: constants, never an input
readonly ANNF=2              # the frames of one MeshChat announce: LXMF delivery + audio call (45f89a8 meshchat.py:2073, :2080)
# the lab's cap per model where it is below the model's power_default_dbm (a constant): the RFM98PW stick (r3) up to setting 14
# (its PA adds ~10-13 dB; 15 and above also drop it off USB). R0 records min(the model cap, this) as CAP_<box>_<model>
declare -rA KITCAP=([ch341-rfm98pw-433]=14)
STICK_MIN=2; WEBPORT=8443; NPREFIX=${NONCE_PREFIX:-rfkit}
PEER=${PEER:-$HERE/peer.py}; FAKE_PEER=${FAKE_PEER:-$HERE/fake_peer.py}; VMT=${VMT:-python3}; VRNS=${VRNS:-python3}; LOCK=${LOCK:-${TMPDIR:-/tmp}/rf-proof-peer.lock}
# ---- 1. the ONE row table (the table order is the run order) ---------------------------------------------------------------
# id lane kind  endpoint A    endpoint B    power        flags
ROWS=(
  "7  fast frame H:H433        H:HSTK        min,min      same-box"
  "8  fast frame H:HMS         Z:Z868        min,min      -"
  "9  fast frame H:HPINE       Z:Z868        min,min      -"
  "10 fast frame H:H433        Z:Z433        as-is,as-is  -"
  "11 fast frame H:H868        Z:Z868        as-is,as-is  -"
  "1  full stack H:H433        peer:tdeck    as-is,read   -"
  "2  full stack H:H433        peer:tracker  as-is,as-is  -"
  "3  full stack Z:Z868        peer:g2       as-is,low    -"
  "4  full stack Z:Z868        peer:tdpro    as-is,read   -"
  "5  full stack Z:Z868        peer:rnode    as-is,read   -"
  "12 full stack H:HMS         peer:rnode    as-is,read   -"
  "6  full stack H:H433        Z:Z433        as-is,as-is  -"
  "13 rns  stack H:H868        peer:rnode    as-is,read   member=H:HMS,zero=Z:Z868"
)
# The worst-case frames of a row per endpoint ("A B"): frame rows one each way; row 1 the box's three console attempts, and
# the T-Deck's one send plus its mesh's relays of those three attempts (3 frames the kit never dispatches; its sends stay one,
# `tx R1-tdeck 1`; the box's node runs with mesh off: it repeats nothing); row 5 the box's announce (inside its MeshChat start)
# + delivery proof + own send + the one retry a late cancel can let slip (LATE ends the run), the RNode's announce + message +
# path request + its proof of the box's message (row 12 the same); row 13 ("A B member zero"): two RNode exchanges of row 5's
# kind (owner, owner after the member's stop; no box send: rnsback "in the member leg"), the RNode 3 each; the member leg is H's
# member <-> the Zero on 868.3 (the RNode hears neither): the owner 3 (start's announce, proof) + 5 (the announce call, the send,
# its slip, the proof of the Zero's message: reserved on every node radio, the node picks one) + 1 (the leave's proof) = 9; the
# member 5; the Zero 5 (its MeshChat start's announce, its send and slip, its proof of H's message).
declare -A WORST=([1]="3 4" [2]="1 1" [3]="1 1" [4]="1 1" [5]="5 4" [6]="1 1" [12]="5 4" [13]="9 6 5 5")
declare -A RROW=(); RIDS=
for l in "${ROWS[@]}"; do read -r id lane kind a b pw fl <<< "$l"; RROW[$id]="$l"; RIDS="$RIDS $id"; done
rowf() { local id lane kind a b pw fl; read -r id lane kind a b pw fl <<< "${RROW[$1]}"; case $2 in lane) echo "$lane";; kind) echo "$kind";;
  a) echo "$a";; b) echo "$b";; power) echo "$pw";; flags) echo "$fl";; esac; }
rowname() { case $1 in 1) echo meshcom;; 2) echo "kiss + graywolf";; 3) echo meshtastic;; 4) echo meshcore;; 5) echo "reticulum + meshchat";; 6) echo chat;;
  12) echo "reticulum (daemon backend) + meshchat";; 13) echo "reticulum: two radios, one node";; *) echo daemon;; esac; }
rflag() { rowf "$1" flags | tr ',' '\n' | sed -n "s/^$2=//p"; }   # rflag <row> <name>: a row's flag value (flags a=<v>,b=<v>)
rmember() { rflag "$1" member; }   # a row's third endpoint (flags member=<box:KEY>)
rzero() { rflag "$1" zero; }       # row 13's fourth: the Zero's radio, the member leg's peer (flags zero=<box:KEY>)
reps() { rowf "$1" a; rowf "$1" b; rmember "$1"; rzero "$1"; }   # every endpoint of a row: A, B, the member, the Zero
declare -A LEGS=([13]="owner join member leave rejoin off")   # a stack row's own legs (default: box->peer peer->box)
dirs() { if [ "$(rowf "$1" kind)" = frame ]; then echo "A->B B->A"; [ "$(rowf "$1" flags)" = same-box ] && echo stop-isolation; else echo "${LEGS[$1]:-box->peer peer->box}"; fi; }
dirtext() { case $1 in box-\>peer) echo "box -> peer";; peer-\>box) echo "peer -> box";; owner) echo "owner both ways";; join) echo "member joins";;
  member) echo "member both ways";; leave) echo "member stops";; rejoin) echo "member joins again";; off) echo "owner stops";; *) echo "$1";; esac; }

# ---- 3. FORMS: the box-side commands ({RT} = the box's runtime path, {LH} = its lhpc) ---------------------------------------
# A Reticulum radio on the daemon backend: its txpower as its section renders it (the panel's one function; the CLI has no read
# of a daemon panel). A refusal (no panel, POWER below the interface's floor) prints no number: no TX.
RTPOW_PY='import sys; from lhpc.core import radio_start; from lhpc.core.services import ControllerService; s, why = radio_start.panel_section(ControllerService(), "reticulum", sys.argv[1]); print(s.get("txpower") or "NO txpower: " + why)'
# Row 13: an instance's OWN stored value of <key> (reticulum@<rid>.toml; "" = none), journalled before the kit saves another
# channel: the member's dp_FREQ (MHz), the Zero's file_frequency (Hz); a blank restore clears it (renders the default).
RTOWN_PY='import sys; from lhpc.core import commands, config as c; from lhpc.core.services import ControllerService; print("OWN", c.load_stack_config(ControllerService()._paths, "reticulum", inst=commands.Instance(sys.argv[1])).get(sys.argv[2], ""))'
# The box's message to the PC through MeshChat's own API (meshchat.py 45f89a8 :1771, :1837), held to one frame: only with the
# PC's path in the node's table (rnpath -t: local, no RF; else NO-PATH, exit 4), opportunistic (one packet), cancelled at t0+8 s,
# t0 the clock BEFORE the send's POST (LXMF's next attempt comes >= 10 s after the first, which is after t0: LXMRouter.py
# :1810); a send not fully answered by t0+5 s (the socket timeout bounds each read, not the whole answer: the clock is read
# after it), a cancel ending after t0+9.5 s or not answered in 1.5 s is LATE (exit 5); an
# interval timer armed at t0 kills the python at t0+9.5 s whatever it does (SIGALRM, exit 142); any exit but 0 and 4 stops the
# node first (the runner: LXMF retries until its node is down). Prints SENT <hash>, CANCEL <s since t0> <state>.
RTSEND_PY='import json, subprocess, sys, time, urllib.error, urllib.request as u
from lhpc.core import reticulum_members as m
from lhpc.core.services import ControllerService
d, n = sys.argv[1], sys.argv[2]; s = ControllerService(); src = s._lifecycle().source_dir(m._comp(s))
t = subprocess.run([str(src / ".venv" / "bin" / "rnpath"), "--config", str(m._config_dir(s)), "-t"], capture_output=True, text=True, timeout=30)
if t.returncode != 0 or d not in t.stdout: print("NO-PATH: the node has no path to the PC (rnpath -t): nothing sent"); sys.exit(4)
def post(url, body, to): return json.loads(u.urlopen(u.Request(url, json.dumps(body).encode(), {"Content-Type": "application/json"}), timeout=to).read().decode())
t0 = time.monotonic(); import signal; signal.setitimer(signal.ITIMER_REAL, 9.5)
try: h = post("http://127.0.0.1:8790/api/v1/lxmf-messages/send", {"delivery_method": "opportunistic", "lxmf_message": {"destination_hash": d, "content": n}}, 5)["lxmf_message"]["hash"]
except urllib.error.HTTPError as e: print("SEND-FAILED", e.code, e.read().decode()[:200]); sys.exit(6)
except Exception as e: print("LATE: no send answer within 5 s", type(e).__name__); sys.exit(5)
if time.monotonic() - t0 > 5: print("LATE: the send answered after %.1f s (over 5 s): not cancelled" % (time.monotonic() - t0)); sys.exit(5)
print("SENT", h, flush=True); time.sleep(max(0.0, t0 + 8 - time.monotonic()))
try: c = post("http://127.0.0.1:8790/api/v1/lxmf-messages/" + h + "/cancel", {}, 1.5)
except Exception as e: print("LATE: no cancel answer", type(e).__name__); sys.exit(5)
w = time.monotonic() - t0; print("CANCEL", "%.1f" % w, (c.get("lxmf_message") or {}).get("state")); sys.exit(5 if w > 9.5 else 0)'
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
  [rt_dpget]="cd {RT} && ./venv/lhpc/bin/python -c '$RTPOW_PY' {rid}"
  [rt_send]="cd {RT} && timeout -k 2 60 ./venv/lhpc/bin/python -c '$RTSEND_PY' {dest} '{n}'"
  [rt_own]="cd {RT} && ./venv/lhpc/bin/python -c '$RTOWN_PY' {rid} {k}"
  [rt_dfreq]='{LH} config reticulum@{rid} --daemon-param FREQ={f}'
  [rt_zfreq]='{LH} config reticulum@{rid} frequency "{hz}"'   # quoted: the undo's blank must reach the save (unquoted = a show)
  [rt_join]='{LH} stack start reticulum@{rid} --join --yes'
  [rt_announce]="python3 -c \"import urllib.request as u; print(u.urlopen('http://127.0.0.1:8790/api/v1/announce', timeout=30).read().decode()[:80])\""
  [chat_client]='{LH} stack start chat@{rid} --yes'
  [tx_text]="python3 -c \"import os,socket,struct,sys; p='/run/loraham/loraf-{rid}.sock'; p=p if os.path.exists(p) else '/tmp/loraf-{rid}.sock'; s=socket.socket(socket.AF_UNIX); s.connect(p); b=sys.argv[1].encode(); s.sendall(bytes([2])+struct.pack('<H',len(b))+b); s.close()\" '{n}'"
  [rx_rf]="grep -hF -- '{n}' {RT}/logs/{rid}/rf-*.log 2>/dev/null | grep -F ' RX '"
  [rx_mt]="{LH} rflog meshtastic@{rid} --decrypt 2>/dev/null | grep -F ' RX ' | grep -F -- '{n}'"
  [rx_mc]="{LH} rflog meshcore@{rid} --decrypt 2>/dev/null | grep -F ' RX ' | grep -F -- '{n}'"
  # the word in Meshtastic on the box: a listener bounded on the box itself; its own end is a line too (LISTENER-RC 124 = its timeout)
  [rx_mt_app]="{ timeout -k 5 {s} {LH} meshtastic@{rid} --listen 2>&1; echo \"LISTENER-RC \$?\"; } | grep -m1 -F -e \"'text': '{n}'\" -e 'LISTENER-RC '"
  # the word in MeshCore on the box: sync_msgs POPS the node's queue (a read deletes it, no undo); each poll bounded
  [rx_mc_app]="timeout -k 2 10 {RT}/src/meshcore-cli/.venv/bin/meshcore-cli -j -t 127.0.0.1 -p {cport} sync_msgs 2>/dev/null | grep -F -- '{n}\"'"
  [rx_mchat]="grep -rlF -- '{n}' {RT}/state/meshchat 2>/dev/null"
  # MeshCom group text <src>>*:<text>, matched in the frame's hex: `>*:` (3e2a3a), the nonce ({nh}), its 0x00 terminator
  # (aprs_functions.cpp:1097; HW, MOD, FCS, FW, LH follow, so the ascii field never ends at the text). The line forms are the
  # daemon's RF-log writer's (daemon_rflog.cpp:153 RX, :167 TX); a TX counts only with outcome=ok (daemon_tx.cpp:222).
  [rx_mcom]="grep -hE -- '^[^ ]+ RX rssi=[^ ]+ snr=[^ ]+ len=[0-9]+ .*hex=([0-9a-f]{2})*3e2a3a{nh}00' {RT}/logs/{rid}/rf-*.log 2>/dev/null"
  [tx_mcom]="grep -hE -- '^[^ ]+ TX rssi=- snr=- len=[0-9]+ outcome=ok .*hex=([0-9a-f]{2})*3e2a3a{nh}00' {RT}/logs/{rid}/rf-*.log 2>/dev/null"
  # inside MeshCom on the box: the node's own print of a text it ACCEPTED (after its dedup; --setinfo on only), source token
  # RX-LoRa2 (lora_functions.cpp:1819-1822, printBuffer_aprs loop_functions.cpp:3818), in its guest's UART log, the file LHPC
  # waits on at boot ({source}/.run/{instance_index_dir}uart-latest.log; {idx} = rN's N-1 dir, "" for r1)
  [rx_mcom_app]="grep -hE -- '(^| )RX-LoRa2 [0-9]{3} : x[0-9A-Fa-f]{8} ([^ ]+ ){4}[^ >]+>[*]:{n} HW:' {RT}/src/meshcom-qemu-raspi/.run/{idx}uart-latest.log 2>/dev/null"
)
CHAT_READY='Ctrl-K: Konf'   # the chat client's footer
# The forms each row needs (rt_send is optional in rows 5 and 12: their box -> peer is NOT RUN without it; row 13's member leg is
# its two bounded sends, so it needs it).
declare -A RFORMS=([1]="start stop tx_mcom rx_mcom rx_mcom_app" [2]="start stop" [3]="start stop mt_cap mt_send mt_pget rx_mt rx_mt_app" [4]="start stop mc_send mc_pget rx_mc rx_mc_app"
  [5]="start stop rt_get rt_pget rx_mchat" [6]="start stop chat_client rx_rf" [12]="start stop rt_get rt_dpget rx_mchat"
  [13]="start stop rt_get rt_dpget rt_pget rx_mchat rt_join rt_own rt_dfreq rt_zfreq rt_announce rt_send")
# The pre-step M (mcset.py, from the RF sheet's MCSET-PRESTEP-v4): per stack row, the instance settings it copies from the stack's
# source file into <sid>@<rid> of endpoint A's radio ({band} = that radio's band). J1's value is the input MCSET_J1.
declare -A MJOB=([1]="J1 meshcom meshcom.toml mc_callsign" [2]="J2 graywolf graywolf@{band}.toml call"
  [3]="J3 meshtastic meshtastic@{band}.toml node_name,node_short" [4]="J4 meshcore meshcore.toml file_node_name")
rforms() { if [ "$(rowf "$1" kind)" = frame ]; then echo "start stop tx_text rx_rf$(case "$(rowf "$1" power)" in *min*) echo " dset";; esac)"; else echo "${RFORMS[$1]}"; fi; }
# ---- 4. the box-side helpers (python, run on the box) ----------------------------------------------------------------------
STATUS_PY='import socket,sys,os; r=sys.argv[1]; p=next((d+"/loraconf-"+r+".sock" for d in ("/run/loraham","/tmp") if os.path.exists(d+"/loraconf-"+r+".sock")),None); assert p, "no conf socket of "+r; s=socket.socket(socket.AF_UNIX); s.settimeout(30); s.connect(p); s.sendall(b"GET STATUS\n"); b=b""
while not b.endswith(b"\n") and len(b) < 4096:
 c=s.recv(4096 - len(b)); assert c, "EOF before the end of the STATUS line"; b+=c
print(b.decode().strip())'
# MCHASH_PY: the LXMF delivery address of the box's MeshChat, read only from its identity file (the manifest runs MeshChat with
# --storage-dir {runtime}/state/meshchat and no --identity-file: its identity is <storage>/identity); prints the hash only
MCHASH_PY='import sys, RNS; i = RNS.Identity.from_file(sys.argv[1]); assert i, "no identity"; print("LXMF", RNS.Destination.hash(i, "lxmf", "delivery").hex())'
CPORT_PY='import sys; from lhpc.core import instances; from lhpc.core.services import ControllerService; s = ControllerService(); sid, inst, _b, why = instances.resolve(s, sys.argv[1]); print("WHY", why or "-"); [print("CPORT", e.address.rsplit(":", 1)[1]) for c in (s.stack(sid).components if inst is not None else ()) for t, e in zip(c.endpoints, instances.endpoints_for(c.endpoints, inst)) if t.kind == "tcp" and t.address.endswith(":5000")]; life = s._lifecycle(); [print("OWNED", r.get("pgid")) for r in (life.owned_records("meshcore-node", rid=inst.rid) if inst is not None else []) if life.verify_owned(r)[0]]; [print("OTHER", c) for c in ("meshcore-webui", "meshcore-cli") for r in (life.owned_records(c, rid=inst.rid) if inst is not None else []) if life.verify_owned(r)[0]]'
RADIOS_PY='from lhpc.core import paths, radios; s = radios.load(paths.resolve_paths()); print("STATE", s.kind); [print("ROW", r.id, r.model, r.band, r.enabled, "serial=" + r.usb_serial, "port=" + r.usb_port, "hp=" + str(r.high_power)) for r in s.radios]'
CAPS_PY='import sys; sys.argv[1:] and sys.path.insert(0, sys.argv[1]); from lhpc.core import radio_models; [print("CAP", m.id, m.power_default_dbm) for m in radio_models.load().values()]'
RUNSET_PY='from lhpc.core import instances; from lhpc.core.services import ControllerService; s = ControllerService(); [print(k) for st in s.stacks() for k in instances.running_keys(s, st.id)]'
RUNSET="cd {RT} && ./venv/lhpc/bin/python -c '$RUNSET_PY'"
# TXC_PY <T0> <rid>:<band>:<head bytes>...: the facts of each radio's TX since T0 (the box clock at R0), for txjudge. Each RF
# log is read with the product's one parser (rflog.parse_line: the common line and the meshtastic JSON), <log>.1 + <log>
# (the copy-truncate roll): F <rid> <kind> <file> <records> <oldest record|-> <TX records with ts >= T0, plus every line
# that does not parse after the last record before T0 (safe side)>. kind: daemon (rf-daemon.log: the frames the radio
# aired), mirror (rf-kiss/-meshcom/-meshcore.log: the same frames again, logged on the daemon result), rns (rf-reticulum.log:
# each radio of the node its own, product 85fa9df5; a TX since T0 in the node-wide logs/rf-reticulum.log of an older product
# makes every radio's count LEGACY: UNREADABLE), direct (any other rf-*.log of the radio: meshtastic over SPI). L <rid> <size>
# <sha of the first <head bytes>> <transmit ERRORs> <prev state>: the daemon run log (no time stamps; daemon pin
# f9b16468 daemon_tx.cpp:212 is the one airing path, its RF line is written only on success (:223), an error that may
# have aired prints "transmit ERROR" (:219) to this log only); prev state = its cut file (inode:size:mtime_ns) or absent.
TXC_PY='import calendar, hashlib, json, os, sys, time
from lhpc.core import rflog, radio_start, runtime_fs
from lhpc.core.services import ControllerService
t0 = int(sys.argv[1])
KIND = {"rf-daemon.log": "daemon", "rf-kiss.log": "mirror", "rf-meshcom.log": "mirror", "rf-meshcore.log": "mirror", "rf-reticulum.log": "rns"}
def epoch(ts):
    try:
        return calendar.timegm(time.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S"))
    except (TypeError, ValueError):
        return None
def jline(ln):
    try:
        j = json.loads(ln) if ln.lstrip().startswith("{") else None
    except ValueError:
        return None
    return j if isinstance(j, dict) else None
def selfnums(path):   # meshtastic: a packet to the node itself is handled locally, never aired; its number = from == to
    out = set()
    for p in (path + ".1", path):
        if os.path.exists(p):
            with open(p, errors="replace") as f:
                for ln in f:
                    j = jline(ln)
                    if j and j.get("from") == j.get("to") and j.get("to") not in (None, 0, 4294967295):
                        out.add(j["to"])
    return out
def count(rid, kind, path, out=True):
    seen = False; recs = tx = loose = 0; old = None; own = selfnums(path)
    for p in (path + ".1", path):
        if not os.path.exists(p):
            continue
        seen = True
        with open(p, errors="replace") as f:
            for ln in f:
                if not ln.strip():
                    continue
                r = rflog.parse_line(ln); e = epoch(r.get("ts") or "")
                if e is None or r.get("dir") not in ("TX", "RX"):
                    loose += 1
                    continue
                recs += 1; old = e if old is None else min(old, e)
                if e < t0:
                    loose = 0
                elif r["dir"] == "TX" and not (own and (jline(ln) or {}).get("to") in own):
                    tx += 1
    if seen and out:
        print("F", rid, kind, os.path.basename(path), recs, "-" if old is None else old, tx + loose)
    return tx + loose
for a in sys.argv[2:]:
    rid, band, hb = a.split(":"); d = "logs/" + rid
    for n in sorted(os.listdir(d)) if os.path.isdir(d) else []:
        if n.startswith("rf-") and n.endswith(".log"):
            count(rid, KIND.get(n, "direct"), d + "/" + n)
    p = "logs/" + radio_start.start_log_name(ControllerService.DAEMON_ID, band, rid)
    b = open(p, "rb").read() if os.path.exists(p) else b""
    q = "logs/" + runtime_fs.prev_log_name(os.path.basename(p))
    s = os.stat(q) if os.path.exists(q) else None
    print("L", rid, len(b), hashlib.sha256(b[:int(hb)]).hexdigest()[:16], b.count(b" transmit ERROR:"),
          "absent" if s is None else "%d:%d:%d" % (s.st_ino, s.st_size, s.st_mtime_ns))
# the node-wide Reticulum log of a product before 85fa9df5 names no radio: a TX in it since T0 -> every radio UNREADABLE
if count("-", "rns", "logs/rf-reticulum.log", out=False):
    for a in sys.argv[2:]:
        print("F", a.split(":")[0], "rns", "rf-reticulum.log", 0, "-", "LEGACY")'
RUNTX="cd {RT} && ./venv/lhpc/bin/python -c '$TXC_PY'"
USBLIST='for d in /sys/bus/usb/devices/[0-9]*-*; do case $d in *:*) continue;; esac; echo "${d##*/} $(cat $d/idVendor 2>/dev/null):$(cat $d/idProduct 2>/dev/null) dev=$(cat $d/devnum 2>/dev/null) ser=$(cat $d/serial 2>/dev/null)"; done'
USBEV='USB disconnect|new (full|high|low|super)-speed USB|reset (full|high|low|super)-speed USB|over-current'
rport() { local n=${2#r}; case "$n" in ''|*[!0-9]*) stop "rport: rid '$2'";; esac; echo $(( $1 + 133 * (n - 1) )); }   # a listed instance's port
GW_PY='import http.cookiejar, json, os, sys, time, urllib.request
BASE = "http://127.0.0.1:" + sys.argv[2]   # argv = nonce port rid; the assignment of TO (the call) is the first line of this script on stdin (the caller prefixes it)
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
# The box node's `--setinfo on|off` (RAM only, never saved: command_functions.cpp:844 TG_DIRTY_NONE), one console attempt
# (argv: the line, port): banner, the line + LF, its echo. rc 0 = echoed, 4 = no banner, 3 = no echo; markers only. No frame.
MCINFO_PY='import socket,sys,time
c, port = sys.argv[1], int(sys.argv[2]); assert c in ("--setinfo on", "--setinfo off"), c
s = socket.create_connection(("127.0.0.1", port), 5); s.settimeout(0.5); buf = b""
def until(pat, secs):
    global buf
    end = time.monotonic() + secs
    while time.monotonic() < end and pat not in buf:
        try:
            r = s.recv(4096)
        except socket.timeout:
            continue
        if not r:
            break
        buf += r
    return pat in buf
if not until(b"Type --help for commands", 10):
    print("NO-BANNER"); sys.exit(4)
k = len(buf); s.sendall((c + "\n").encode()); ok = until(c.encode(), 15) and c.encode() in buf[k:]
print("ECHOED" if ok else "NO-ECHO"); s.close(); sys.exit(0 if ok else 3)'
setinfo() { printf 'python3 -I -c %q %q %s' "$MCINFO_PY" "--setinfo $1" "$(rport 12323 "$2")"; }   # setinfo on|off <rid>
mcidx() { local n=${1#r}; [ "$n" -gt 1 ] && echo "$((n - 1))/" || echo ""; }   # rN -> its guest's .run/<N-1>/ ("" for r1)
nhex() { printf '%s' "$1" | od -An -tx1 | tr -d ' \n'; }   # a nonce's bytes, hex
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
# --rns=<config dir>: an rnsd of this user running that config is named rnsd-shared (the Reticulum rows' shared RNode), any other rnsd
rns = os.path.realpath(sys.argv.pop(1)[6:]) if sys.argv[1:2] and sys.argv[1].startswith("--rns=") else None
devs = {os.path.realpath(d): d for d in sys.argv[1:]}; held = {}
def shared(p):
    try:
        a = [x.decode(errors="replace") for x in open(f"/proc/{p}/cmdline", "rb").read().split(b"\0") if x]
        mine = os.stat(f"/proc/{p}").st_uid == os.getuid()
    except OSError:
        return False
    return (rns is not None and mine and any(os.path.basename(x) == "rnsd" for x in a) and "--config" in a[:-1]
            and os.path.realpath(a[a.index("--config") + 1]) == rns)
for p in (x for x in os.listdir("/proc") if x.isdigit()):
    try: fds = os.listdir(f"/proc/{p}/fd")
    except OSError: continue
    for fd in fds:
        try: t = os.readlink(f"/proc/{p}/fd/{fd}")
        except OSError: continue
        if t in devs:
            try: who = open(f"/proc/{p}/comm").read().strip()
            except OSError: who = p
            held.setdefault(t, set()).add("rnsd-shared" if who == "rnsd" and shared(p) else who)
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

# pdom <value> [<form|peer action>]: THE power domain of every power read (a pre-state), set and restore: 0..17 dBm, 1-2 ASCII
# digits; Meshtastic (mt_pget, mt-power) 1..17, its 0 being the firmware's maximum. A value outside it is never set, journalled
# or restored: a pre-state outside it STOPs the row before any change (the restore could not set it back within 0..17).
pdom() { [[ "$1" =~ ^[0-9]{1,2}$ ]] && [ "$((10#$1))" -le "$PCAP" ] || return 1; case "${2:-}" in mt_pget|mt-power) [ "$((10#$1))" -ge 1 ];; esac; }
# THE power boundary. pnorm: a text normalised (every quote, backslash and blank removed, lowercase), so no quoting, tab or
# escape can split a key. A normalised text with a power token (power, txp, settx) runs only through the one setter (pset)
# or the one reader (pread); anything else carrying one is a STOP at the X/XIN/peer boundary (guard).
pnorm() { printf '%s' "$1" | tr -d "\"'\\\\[:space:]" | tr '[:upper:]' '[:lower:]'; }
ptoken() { local t; t=$(pnorm "$1"); [[ $t == *power* || $t == *txp* || $t == *settx* ]]; }
# pover <text> [read]: rc 0 when a text carries a high-power flag or a value after a power token above the cap (as an integer,
# any zeros); with `read` (the one reader's context) also any set form of a power (a read never sets one); the attribute
# high_power is read by the radios read, so it counts only outside `read`
pover() { local t n nl='(power|txp|settx)[^0-9a-z]{0,3}[$`]'; t=$(pnorm "$1"); [[ $t == *highpower* || $t == *high-power* ]] && return 0
  [[ $t =~ $nl ]] && return 0   # a value after a power token that is not a literal number (an expansion) counts as over
  if [ "${2:-}" = read ]; then [[ $t =~ --set[a-z0-9_.]*(power|txp) || $t =~ set[-_]?power || $t == *settx* ]] && return 0
  else [[ $t == *high_power* ]] && return 0; fi
  for n in $(printf '%s' "$t" | grep -oE '(power|txp|settx)[^0-9a-z]{0,3}-?[0-9]+' | grep -oE '[0-9]+$'); do n=${n#"${n%%[!0]*}"}
    [ "${#n}" -le 2 ] && [ "${n:-0}" -le "$PCAP" ] || return 0; done; return 1; }
# issend: rc 0 when a command (or its stdin) is a send form: the frame, Meshtastic, MeshCore and MeshChat sends, the chat
# line, the MeshChat announce, a Reticulum row's release, the MeshCom console and Graywolf helpers, a peer's send or exchange
issend() { local t i; t=$(pnorm "$1"); i=$(pnorm "${2:-}")
  case "$t" in *loraf-*struct.pack*|*--sendtext*|*meshcore-cli*public*|*meshchat@r*send*|*tmuxsend-keys*|*/api/v1/announce*|*/api/v1/lxmf-messages/send*|*[0-9]-tx2.go*|*"::+sys.argv"*|*mt-send*|*mc-send*|*meshcom-send*|*rns-exchange*) return 0;; esac
  case "$i" in *type--helpforcommands*|*/api/messages*) return 0;; esac; return 1; }
# ---- 5. state, forms, output ------------------------------------------------------------------------------------------------
say() { echo "$*"; }; data() { echo "data: $*"; }; ok() { echo "ok: $*"; }
fail() { echo "FAIL: $*" >&2; printf '%s\n' "$*" > "$OUT/rowfail.txt"; exit 1; }   # a row FAIL (the marker); its undos run
leg() { dirtext "$1" > "$OUT/rowleg.txt" || stop "rowleg.txt: not written"; }   # leg <dir>: the direction a stack row runs now
eq() { [ "$2" = "$3" ] && ok "$1" || fail "$1: got '$2', want '$3'"; }
stop() { echo "STOP: $*" >&2; exit 2; }
pcpath() { case "$1" in "~"/*) printf '%s\n' "$HOME/${1#\~/}";; *) printf '%s\n' "$1";; esac; }
keep() { echo "$1=$2" >> "$OUT/state.env" || stop "state.env: append failed"; }
getk() { local l; l=$(grep "^$1=" "$OUT/state.env" 2>/dev/null | tail -n 1)   # a recorded empty value is a value
  if [ -n "$l" ]; then printf '%s\n' "${l#*=}"; else printf '%s\n' "${2-}"; fi; }
hask() { [ "$MODE" = dry ] && return 0; grep -q "^$1=" "$OUT/state.env"; }
form() { local n=$1 t=${F[$1]}; shift; local kv; for kv in "$@"; do t=${t//\{${kv%%=*}\}/${kv#*=}}; done
  if [[ $t == *"{cport}"* ]]; then for kv in "$@"; do [ "${kv%%=*}" = rid ] && t=${t//\{cport\}/$(rport 5000 "${kv#*=}")}; done; fi
  [ "$MODE" = live ] || echo "${CURROW:-0} $n" >> "$SCRATCH/formed"; printf '%s' "$t"; }
gstart() { printf '( o=$(%s) || { echo RUNSET-READ-FAILED; exit 4; }; if printf "%%s\\n" "$o" | grep -qxF -- %s; then echo ALREADY-RUNNING %s; else %s; fi )' "$RUNSET" "$1" "$1" "$(form start inst="$1")"; }
gstop() { printf '( o=$(%s) || { echo RUNSET-READ-FAILED; exit 4; }; if printf "%%s\\n" "$o" | grep -qxF -- %s; then %s; else echo ALREADY-STOPPED %s; fi )' "$RUNSET" "$1" "$(form stop inst="$1")" "$1"; }
ordkeys() {   # stdin: a status text; args: instance keys -> the keys in status header order, by rid within a stack; rc 1 if one is not placed
  local hdr s out=; hdr=$(sed -nE 's/^\[([a-z0-9_-]+)(@r[0-9]+)?\] .*/\1/p' | grep -vx controller | awk '!seen[$0]++')
  for s in $hdr; do out="$out $(printf '%s\n' "$@" | grep -E "^$s@r[0-9]+$" | sort -t@ -k2.2n | tr '\n' ' ')"; done
  [ "$(printf '%s\n' $out | grep -c .)" = "$#" ] || return 1; echo $out; }

# ---- 6. the executors and the ONE capture helper -----------------------------------------------------------------------------
# X <H|Z|PC> <file> <command>: runs it; its redacted output in $OUT/<file>, its exit status in XRC (and $OUT/<file>.rc).
# XBG=1 (a box only): in the background, the pid in SPAWN, `reapx <pid>` ends it; its own name, so a caller's BG=1 (peer
# runs, `BG=1 tx`) never puts tx's box checks in the background.
# XIN <H|Z> <file> <stdin-text> <command>: the same with a text on the command's stdin (a script, or the password for sudo -S).
# must = X, and a non-zero exit is a STOP. peer/peerpin: one peer.py action under the peer lock (BG=1: in the background, the
# pid in SPAWN; `reap <pid>` waits for it). Dry prints each command ([dry]) and runs nothing on a box or a peer: the fake box
# answers from the dry inventory and prints the FRAME lines of a send, the fake peer is fake_peer.py.
redact() {   # the passwords, the PIN, the tracker call, the operator's call (any SSID or none, any case) and an LXMF address -> masked
  local t s c=${OPERATOR_CALL:-} m=(); t=$(cat; echo x); t=${t%x}
  if [ -s "${SCRATCH:-/nonexistent}/lxmf.list" ]; then while IFS= read -r s; do [ -z "$s" ] || m+=("$s"); done < "$SCRATCH/lxmf.list"; fi   # read by the Reticulum rows
  t=$(printf '%s' "$t" | sed -E 's/^LXMF [0-9a-f]{32}$/LXMF <LXMF>/'; echo x); t=${t%x}
  pweq H || m+=("${PW_H:-}"); pweq Z || m+=("${PW_Z:-}")   # a password equal to its login name stays (masking it = the login)
  local j1=${MCSET_J1:-}; j1=${j1#global}
  for s in "${m[@]}" "${MC_PIN:-}" "${TRACKER_CALL:-}" "$j1"; do [ -n "$s" ] || continue
    t=${t//"$s"/<REDACTED>}; done
  [[ "$c" =~ ^[A-Za-z0-9]+$ ]] || { printf '%s' "$t"; return; }; printf '%s' "$t" | sed -E "s/\\b$c(-[0-9]{1,2})?\\b/<CALL>/Ig"; }
capture() {   # capture <file> <cmd...>: THE one writer of an evidence file and its .rc: the command's output, redacted
  local f=$1 o=${OUT:-$EVD} s; shift
  { : > "$o/$f" && : > "$o/$f.rc"; } 2>/dev/null || stop "$f: the evidence file cannot be written: the command did not run"
  if [ -n "${XRAW:-}" ]; then   # XRAW=<file>: also the raw output, into the private scratch only (never the evidence)
    case "$XRAW" in "${SCRATCH:-/nonexistent}"/*) ;; *) stop "$f: XRAW '$XRAW' is not in the private scratch";; esac
    "$@" 2>&1 | tee "$XRAW" | redact > "$o/$f"; s=("${PIPESTATUS[@]}"); XRC=${s[0]}; [ "${s[1]}" = 0 ] || stop "$f: the raw copy failed"; s[1]=${s[2]}
  else "$@" 2>&1 | redact > "$o/$f"; s=("${PIPESTATUS[@]}"); XRC=${s[0]}; fi
  [ "${s[1]}" = 0 ] || stop "$f: the redactor failed"; echo "$XRC" > "$o/$f.rc" || stop "$f.rc: not written"; }
boxvar() { local v="BOX_${1}_$2" d=; case $2 in USER) d=lhpc;; RUNTIME) d='~/loraham-pi-control';; esac; printf '%s' "${!v:-$d}"; }
# pweq <H|Z>: box <b>'s password is EXACTLY its login name (a lab box): allowed, not masked, not scanned; pwnote: the header lines
pweq() { local p="PW_$1"; [ -n "${!p:-}" ] && [ "${!p}" = "$(boxvar "$1" USER)" ]; }
pwnote() { local b; for b in H Z; do pweq "$b" && echo "NOTE: box $b password equals its login name (lab box); not masked"; done; return 0; }
# shq <value>: the value as one shell word (printf %q); a leading ~/ stays the login's home on the box ("$HOME"/...)
shq() { case "$1" in "~/"*) printf '"$HOME"/%s' "$(printf %q "${1#\~/}")";; *) printf %q "$1";; esac; }
# rt <box|PC> <text> [raw]: {LH} -> {RT}/venv/lhpc/bin/lhpc, then {RT} -> the box's runtime path, quoted for a shell command (raw:
# as it is, for a script's text on stdin); the one expansion of both, for every command X/XIN run (forms, raw commands, undos)
rt() { local t=${2//\{LH\}/\{RT\}/venv/lhpc/bin/lhpc} r
  [ "$1" = PC ] && { [[ $t != *"{RT}"* ]] || stop "a PC command names a box path ({RT} or {LH})"; printf '%s' "$t"; return; }
  r=$(boxvar "$1" RUNTIME); [ "${3:-}" = raw ] || r=$(shq "$r"); printf '%s' "${t//\{RT\}/$r}"; }
sshrun() { local b=$1 in=$2 c=$3 pw; [ "$b" = H ] && pw=$PW_H || pw=$PW_Z
  if [ "$in" = - ]; then SSHPASS=$pw sshpass -e ssh -n -o PubkeyAuthentication=no -o ConnectTimeout=10 "$(boxvar "$b" USER)@$(boxvar "$b" HOST)" "bash -lc $(printf %q "$c")"
  else printf '%s\n' "$in" | SSHPASS=$pw sshpass -e ssh -o PubkeyAuthentication=no -o ConnectTimeout=10 "$(boxvar "$b" USER)@$(boxvar "$b" HOST)" "bash -lc $(printf %q "$c")"; fi; }
drylog() { [ "$MODE" = dry ] && printf '%s\n' "$*" >> "$DRYLOG"; return 0; }
X() { local w=$1 f=$2; shift 2; local c; c=$(rt "$w" "$*"); drylog "CMD[${PCTX:-}] $w: $c"
  guard "$f" "$c" -
  if [ "$MODE" = dry ]; then echo "  [dry] $w > $f: $c"; dryout "$w" "$f" "$c"; XRC=0; echo 0 > "$OUT/$f.rc"; dryframes "$w" "$c" ""; SPAWN=
    [ "${XBG:-0}" = 1 ] && { ( exec -a "rfkit-dry-listener${DRY_TAG:+:$DRY_TAG}" sleep 60 ) </dev/null >/dev/null 2>&1 & SPAWN=$!; SPAWNF[$SPAWN]=$f; }; return 0; fi
  if [ "${XBG:-0}" = 1 ]; then case $w in H|Z) ( capture "$f" sshrun "$w" - "$c" ) & SPAWN=$!; SPAWNF[$SPAWN]=$f; return 0;; *) stop "X: XBG only on a box";; esac; fi
  case $w in H|Z) capture "$f" sshrun "$w" - "$c";; PC) capture "$f" bash -c "$c";; *) stop "X: '$w'";; esac
  [ "$XRC" -ne 255 ] || [ "${XNOSTOP:-0}" = 1 ] || stop "$f: ssh transport rc 255"; }
XIN() { local w=$1 f=$2 in; in=$(rt "$w" "$3" raw); shift 3; local c; c=$(rt "$w" "$*"); drylog "CMD[${PCTX:-}] $w: (stdin) $c"
  guard "$f" "$c" "$in"
  if [ "$MODE" = dry ]; then echo "  [dry] $w > $f: (stdin) $c"; dryout "$w" "$f" "$c"; XRC=0; echo 0 > "$OUT/$f.rc"; dryframes "$w" "$c" "$in"; return 0; fi
  capture "$f" sshrun "$w" "$in" "$c"; [ "$XRC" -ne 255 ] || [ "${XNOSTOP:-0}" = 1 ] || stop "$f: ssh transport rc 255"; }
must() { X "$@"; [ "$XRC" = 0 ] || stop "$2: exit status $XRC ($OUT/$2)"; }
guard() {   # guard <file> <command> <stdin|->: THE runtime boundary of X, XIN and the peer process, before anything runs
  local f=$1 c=$2 in=$3
  [ "$in" = - ] || { [ -n "${PW_H:-}" ] && [ "$in" = "$PW_H" ]; } || { [ -n "${PW_Z:-}" ] && [ "$in" = "$PW_Z" ]; } \
    || ! ptoken "$in" || stop "$f: a power token in the stdin payload: never run"   # in every context; the box password (sudo -S) is not a command
  if [ -z "${PCTX:-}" ]; then ! ptoken "$c" || stop "$f: a power token outside the one power setter/reader: never run"
  else if [ "$PCTX" = read ]; then ! pover "$c" read || stop "$f: the reader's command carries a power set, a high-power flag or a power above $PCAP dBm: never run"
    else ! pover "$c" || stop "$f: the setter's command carries a power above $PCAP dBm or a high-power flag: never run"; fi   # set, restore
    [ -n "${PAUTH:-}" ] && [ "$c" = "$PAUTH" ] && [ "$in" = - ] || stop "$f: under the power setter or reader only its own command runs: never run"; fi
  ! issend "$c" "$([ "$in" = - ] || printf '%s' "$in")" || [ "${TXCTX:-}" = 1 ] || stop "$f: a send outside tx: never run"; }
pset() {   # pset [--restore] <box|PC> <file> <rid|peer-action> <value>: THE one power setter: the value is its own argument,
  # leading zeros stripped, then 1-2 ASCII digits in the power domain (pdom) and <= the radio's model cap, else STOP. --restore (the undo replay):
  # exactly the pre-state value journalled for that radio when the row changed it (PRE_<box|PC>_<target>), and nothing else
  local restore=; [ "$1" = --restore ] && { restore=1; shift; }
  local w=$1 f=$2 tg=$3 v=$4 cap=$PCAP m c pre
  [[ $v =~ ^[0-9]+$ ]] || stop "$f: power value '$v' is not ASCII digits"; v=${v#"${v%%[!0]*}"}; v=${v:-0}
  [[ $v =~ ^[0-9]{1,2}$ ]] || stop "$f: power value '$4' has more than 2 digits"
  pdom "$v" "$tg" || stop "$f: power $v outside 0..17 dBm (Meshtastic 1..17): never set"
  if [ -n "$restore" ]; then pre=$(getk "PRE_${w}_$tg"); pre=${pre#"${pre%%[!0]*}"}
    [ -n "$(getk "PRE_${w}_$tg")" ] && [ "${pre:-0}" = "$v" ] || stop "$f: restore $w $tg to $v: not its journalled pre-state ('$(getk "PRE_${w}_$tg")')"
  elif [ "$w" != PC ]; then m=$(getk "MODEL_${w}_$tg"); c=$(getk "CAP_${w}_$m"); [[ $c =~ ^[0-9]{1,2}$ ]] || stop "$f: no model cap for $w:$tg ('$m')"
    { [ "${#c}" -lt 2 ] || [[ ! "$c" > "$PCAP" ]]; } || c=$PCAP; cap=$c; fi
  [ -n "$restore" ] || { [ "${#v}" -lt "${#cap}" ] || { [ "${#v}" = "${#cap}" ] && [[ ! "$v" > "$cap" ]]; }; } || stop "$f: power $v above the cap $cap dBm"
  local PCTX=set PAUTH= t; [ -z "$restore" ] || PCTX=restore   # restore: the journalled pre-state, checked above
  if [ "$w" = PC ]; then case "$tg" in mt-power|meshcom-power|mc-power) ;; *) stop "$f: '$tg' is not a peer power action";; esac
    PAUTH="$tg $v"; peer "$f" "$VMT" "$tg" "$v"
  else t=$(form dset rid="$tg" p="$v"); PAUTH=$(rt "$w" "$t"); X "$w" "$f" "$t"; fi; }   # PAUTH: the one command the guard lets through
jpower() {   # jpower <box|PC> <rid|peer-action> <value>: journal a radio's pre-state power before the row changes it: the
  # value the restore may set (PRE_<box|PC>_<target>) and its undo (@pset, replayed by pset --restore)
  pdom "$3" "$2" || stop "$1:$2: pre-state power '$3' outside 0..17 dBm (Meshtastic 1..17): nothing changed; set it into the domain by hand first"
  local o; [ "$1" = PC ] || o=$(jmodel "$1" "$2" "$3") || stop "$1:$2: pre-state $o: nothing changed; set it to its cap or below by hand first"
  keep "PRE_$1_$2" "$3"; undo_push "$1" "@pset $1 $2 $3"; }
rnsinst() {   # rnsinst <name> <the key's fields>: the RNS shared instance of an rnode peer: instance=<name> port=<n> control=<n>,
  # no defaults: instance [A-Za-z0-9_.-]+, port and control 1..65535 and different; a missing or malformed one is a STOP
  local t k inst= port= ctl=; for t in $2; do case "$t" in instance=*) inst=${t#*=};; port=*) port=${t#*=};; control=*) ctl=${t#*=};; esac; done
  [[ $inst =~ ^[A-Za-z0-9_.-]+$ ]] || stop "PEER_$1: instance= missing or malformed ('$inst')"
  for k in port ctl; do t=${!k}; [[ $t =~ ^[0-9]{1,5}$ ]] && [ "$((10#$t))" -ge 1 ] && [ "$((10#$t))" -le 65535 ] \
    || stop "PEER_$1: $([ $k = ctl ] && echo control || echo port)= missing or not 1..65535 ('$t')"; done
  [ "$((10#$port))" != "$((10#$ctl))" ] || stop "PEER_$1: port= and control= are the same ($port)"; }
pread() {   # pread <box|PC> <file> <name> [<rid>]: THE one power reader: a fixed read form, rendered here from its name
  local w=$1 f=$2 n=$3 r=${4:-} c PCTX=read PAUTH=   # PAUTH: the one command the guard lets through under PCTX
  case "$n" in
    mt_pget|rt_pget|rt_dpget|mt_cap) c=$(form "$n" rid="$r");;
    radios) c="cd {RT} && ./venv/lhpc/bin/python -c '$RADIOS_PY'";;
    caps) c="cd {RT} && ./venv/lhpc/bin/python -c '$CAPS_PY'";;
    hpower) w=PC; c="python3 -I $(shq "$SCRATCH/hpower.py") $(shq "$RNSCFG") $(shq "$(dirname "$VRNS")/rnstatus")";;
    mt-power|meshcom-power) PAUTH=$n; peer "$f" "$VMT" "$n"; return;;
    mc-power) PAUTH=$n; peerpin "$f" mc-power; return;;
    *) stop "pread: '$n' is not a power read";; esac
  PAUTH=$(rt "$w" "$c"); X "$w" "$f" "$c"; }
pmust() { pread "$@"; [ "$XRC" = 0 ] || stop "$2: exit status $XRC ($OUT/$2)"; }
declare -A SPAWNF=()
peerrun() {   # peerrun <file> <pin|-> <py> <action> <args...>: THE one place a peer process starts (dry: fake_peer.py, empty stdin)
  local f=$1 pin=$2 py=$3; shift 3; local c="flock -n -E 75 $(shq "$LOCK") $(shq "$py") -I $(shq "$PEER") $(printf '%q ' "$@")"
  guard "$f" "$*" -
  if [ "$pin" = pin ]; then drylog "CMD[${PCTX:-}] PC: (PIN on stdin) peer.py $*"; [ "$MODE" = dry ] && echo "  [dry] PC > $f: peer.py $* (the PIN on stdin)"
  else drylog "CMD[${PCTX:-}] PC: $c"; [ "$MODE" = dry ] && echo "  [dry] PC > $f: $c"; fi
  local x=; case "${PCTX:-}" in set|restore) x=$PCTX;; esac; [ "${TXCTX:-}" = 1 ] && x=tx   # peer.py's RFPROOF_PEER_CTX: it refuses a
  # power set outside set|restore and a send outside tx; always given (empty for a read), so an inherited value never counts
  local -a run; if [ "$MODE" = dry ]; then run=(env "RFPROOF_PEER_CTX=$x" python3 -I "$FAKE_PEER" "$@")
  elif [ "$pin" = pin ]; then run=(pinrun "$x" "$py" "$@"); else run=(env "RFPROOF_PEER_CTX=$x" bash -c "$c"); fi
  if [ "${BG:-0}" = 1 ]; then ( capture "$f" "${run[@]}" < /dev/null ) & SPAWN=$!; SPAWNF[$SPAWN]=$f
    echo "$SPAWN $(pst "$SPAWN")" >> "$(bgfile)" || stop "$f: the background job list is not written (the job runs: $SPAWN)"
  else capture "$f" "${run[@]}" < /dev/null; peerdone "$f"; fi; }
pinrun() { local x=$1 py=$2; shift 2; printf '%s\n' "$MC_PIN" | RFPROOF_PEER_CTX=$x flock -n -E 75 "$LOCK" "$py" -I "$PEER" "$@"; }
peerdone() { XRC=$(cat "${OUT:-$EVD}/$1.rc" 2>/dev/null || echo 99); [ "$XRC" != 75 ] || stop "$1: the peer lock is held (one peer at a time)"
  [ "$MODE" = live ] || { local l; while IFS= read -r l; do set -- $l; frame "$3" "${*:5}"; done < <(grep '^FRAME | ' "${OUT:-$EVD}/$1"); }; }
reap() { [ -n "$1" ] || return 0; wait "$1"; peerdone "${SPAWNF[$1]}"; }
# bgend: every background peer job (BG=1, listed in bg.pids with its start time) that still runs ends now, its whole process
# tree (the flock holding the peer lock and the peer under it): TERM, after 5 s KILL, waited for (10 s at most). It runs before
# any undo replays (runrow, recover): a reader left by a STOP or FAIL would hold the lock and refuse the restore (rc 75).
# rc 1 when one is still there after 10 s.
bgfile() { printf '%s/bg.pids' "${SCRATCH:-$EVD}"; }
pst() { local s; s=$(sed -E 's/^.*\) //' "/proc/$1/stat" 2>/dev/null) || return 0; set -- $s; [ "$1" = Z ] || echo "${20}"; }   # start time; ended/zombie: none
ptree() { local c; for c in $(pgrep -P "$1"); do ptree "$c"; done; echo "$1 $(pst "$1")"; }   # the tree, children first
bgend() { local f p st t all= left; f=$(bgfile); [ -s "$f" ] || return 0
  while read -r p st; do [ -n "$st" ] && [ "$(pst "$p")" = "$st" ] && all="$all$(ptree "$p")"$'\n'; done < "$f"
  for t in $(seq 1 20); do left=$(while read -r p st; do [ -n "$p" ] && [ -n "$st" ] && [ "$(pst "$p")" = "$st" ] && echo "$p"; done <<< "$all")
    [ -n "$left" ] || { : > "$f"; [ "$MODE" != live ] || flock -n "$LOCK" true || { echo "the peer lock is still held ($LOCK)" >&2; return 1; }; return 0; }; [ "$t" = 1 ] && kill -TERM $left 2>/dev/null; [ "$t" = 11 ] && kill -KILL $left 2>/dev/null; sleep 0.5; done
  echo "background peer job(s) still running: $(echo $left)" >&2; return 1; }
peer() { local f=$1 py=$2; shift 2; peerrun "$f" - "$py" "$@"; }
peerpin() { local f=$1; shift; peerrun "$f" pin "$VMT" "$@"; }
body() { cat "${OUT:-$EVD}/$1" 2>/dev/null; return 0; }
# the fake box (dry): canned answers from the dry inventory, and one FRAME line per frame a box command would send
drystatus() { local v="DRY_STATUS_${1}_$2" bd f sf; [ -n "${!v:-}" ] && { printf '%s\n' "${!v}"; return; }
  bd=$(dryradios "$1" | awk -v r="$2" '$1 == "ROW" && $2 == r {print $4}'); case "$bd" in 433) f=433.775000; sf=11;; *) f=869.525000; sf=11;; esac
  echo "STATUS RADIO=READY TX=0 TXMODE=MANAGED TXQUEUE=1 RXREADY=1 HIGHPOWER=0 CHIPFAMILY=SX127x OCP=100 USBWAIT=- BOUND=- ID=$2 FREQ=$f SF=$sf POWER=10"; }
dryradios() { local v="DRY_RADIOS_$1"; printf '%s\n' "${!v:-STATE valid}"; }
dryout() { local w=$1 f=$2 c=$3; : > "$OUT/$f"
  case "$c" in *"$RADIOS_PY"*) dryradios "$w" > "$OUT/$f";; *"$STATUS_PY"*) drystatus "$w" "${c##* }" > "$OUT/$f";;
    *"$CPORT_PY"*) [ -z "${DRY_MC_OTHER:-}" ] || echo "OTHER $DRY_MC_OTHER" > "$OUT/$f";;
    *"$MCHASH_PY"*) echo "LXMF ${DRY_MCHASH:-00112233445566778899aabbccddeeff}" > "$OUT/$f";; esac; }   # another MeshCore client
frame() { echo "FRAME | $1 | $2"; drylog "FRAME $1 $2"; }
dryframes() {   # dryframes <where> <command> <stdin>: the FRAME of a send form, from the command alone (never from a tx)
  local w=$1 c=$2 in=$3 send= base= rid='*' cls
  if [[ $c =~ LHPC_PROOF=([HZ]):[a-z]+@(r[0-9]+) ]]; then frame "${BASH_REMATCH[1]}:${BASH_REMATCH[2]}" control; return 0; fi   # the release: the box's delivery proof
  case "$c" in *loraf-*struct.pack*|*"--sendtext"*|*meshcore-cli*" public "*|*meshchat@r*" send "*|*"tmux send-keys"*|*"/api/v1/announce"*|*"/api/v1/lxmf-messages/send"*|*" stack start meshchat@r"*|*"create_connection(('127.0.0.1',"*) send=1;; esac
  [[ $c == *meshcore-cli* ]] && base=5000; [[ $in == *"Type --help for commands"* ]] && { send=1; base=12323; }; [[ $in == *'"/api/messages"'* ]] && { send=1; base=8080; }
  [ -n "$send" ] || return 0
  if [[ $c =~ @(r[0-9]+) ]] || [[ $c =~ lora[a-z]*-(r[0-9]+)\.sock ]] || [[ $c =~ rfchat-(r[0-9]+) ]]; then rid=${BASH_REMATCH[1]}
  elif [ -n "$base" ] && [[ $c =~ \ ([0-9]{4,5})(\ |$) ]] && [ $(( (BASH_REMATCH[1] - base) % 133 )) = 0 ]; then rid=r$(( (BASH_REMATCH[1] - base) / 133 + 1 )); fi
  cls=$(printf '%s' "$c" | grep -oE "$NPREFIX [0-9a-f]{8}" | head -n 1); frame "$w:$rid" "${cls:-control}"
  [[ $in == *'"/api/messages"'* ]] && frame PC:tracker control; return 0; }   # the tracker's ACK, tied to the box's message

# ---- 7. the ONE journal and the ONE recovery path -----------------------------------------------------------------------------
rec() { printf '%s\n' "$*" >> "$OUT/UNRESOLVED.txt" && return 0
  echo "STOP: unresolved and NOT RECORDED (UNRESOLVED.txt not writable): $*" >&2; [ "${RECOVERING:-0}" = 1 ] && return 1; exit 2; }
undo_push() { [ "$MODE" = dry ] && { echo "  [dry] undo += $1: $([[ $2 == "@pset "* ]] && printf '%s' "$2" || rt "$1" "$2")"; return 0; }; local n
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
    else local urc; case "$c" in   # each undo in a subshell: a refused or failed one is reported, the older ones still run
        "@pset "*) read -r _ uw ut uv <<< "$c"; ( XNOSTOP=1 pset --restore "$uw" "UNDO-$id.txt" "$ut" "$uv"; exit "${XRC:-1}" ); urc=$?;;
        *) ( XNOSTOP=1 X "$w" "UNDO-$id.txt" "$c"; exit "$XRC" ); urc=$?;; esac
      if [ "$urc" != 0 ]; then rec "$w: $c (rc $urc)" || true; rc=1; n=$((n - 1)); continue; fi
      echo "$id" >> "$EVD/undo.done" || { rec "undo journal: $id ran but is not recorded as run" || true; rc=1; }; fi
    sed -i "${n}d" "$EVD/undo.list" || { echo "undo journal: line $n ($id) not removed: $w: $c" >&2; rec "undo journal: line $n ($id) not removed: $w: $c" || true; rc=1; }
    n=$((n - 1)); done; return $rc; }
ctxclear() { local i; for i in 1 2 3 4; do unset PCTX TXCTX PAUTH; done; }   # every scope's flag: an exit inside pset/pread/tx keeps its local
recover() { trap - EXIT INT TERM; trap '' HUP QUIT; ctxclear; [ "$MODE" = live ] && [ -d "$EVD" ] || return 0; RECOVERING=1; local lost=0
  ! hask BOXES || BOXES=$(getk BOXES)   # R0 narrowed it in the worker (supervise): the journal has the value
  ( bgend ) || rec "recover: a background peer job does not end: an undo through the peer lock may be refused" || lost=1
  if [ -f "$EVD/undo.list" ]; then echo "== RECOVERY: undoing every recorded change, newest first"; ( undo_to 0 ) || lost=1; XNOSTOP=1 verify_set || true; fi
  ( secret_scan ) || rec "recover: the evidence scan found a secret or failed: do not publish $EVD" || lost=1
  echo "== RESULT"; ( restable STOPPED ) || rec "recover: RESULT.md not written" || lost=1   # a STOP after the table: it says STOPPED
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
  ! ptoken "$*" || stop "tx $k: a send carries a power token (power is set only by the one setter): no TX"
  [ "$(getk "PWROW_${rad//:/_}")" = "${CURROW:-}" ] && [ -n "${CURROW:-}" ] || stop "tx $k: $rad has no verified power in this row: no TX"
  if [ "$cls" != control ]; then [ "${#cls}" -le 16 ] && grep -qxF -- "$cls" "$OUT/nonces" 2>/dev/null || stop "tx $k: '$cls' is not a nonce of this run"
    [[ "$*" == *"$cls"* ]] || stop "tx $k: the command does not carry its nonce '$cls'"; fi
  n=$(( $(getk "TXR_${rad//:/_}" 0) + c )); [ "$n" -le $TXMAX ] || stop "budget $rad: $n of $TXMAX"; keep "TXR_${rad//:/_}" "$n"
  for b in $BOXES; do X $b "TXG-boot-$b.txt" "cat /proc/sys/kernel/random/boot_id"; [ "$MODE" = dry ] || [ "$(body "TXG-boot-$b.txt")" = "$(getk BOOT_$b)" ] || stop "tx $k: box $b rebooted (boot id changed)"; done
  usbsnap TXG-usb; [ "$MODE" = dry ] || { cmp -s "$OUT/R0-usb-byid.txt" "$OUT/TXG-usb-byid.txt" || stop "tx $k: a PC peer re-enumerated or vanished"
    ! grep -qiE "$USBEV" "$OUT/TXG-usb-kern.txt" || stop "tx $k: a USB event in the PC's kernel log since R0 (TXG-usb-kern.txt)"; }
  case "$rad" in [HZ]:*) b=${rad%%:*}; must "$b" "TXG-$b-usb.txt" "$USBLIST"; [ "$MODE" = dry ] || cmp -s "$OUT/R0-$b-usb.txt" "$OUT/TXG-$b-usb.txt" || stop "tx $k: box $b's USB device list changed since R0";; esac
  say "TX | $rad | $c | $cls | $([ "$e" = reserve ] && echo reserve || echo "$e $f")"; drylog "TX $rad $c $cls $e"
  [ "$e" = reserve ] || { local TXCTX=1; "$@"; }; }
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
jstatus() {   # jstatus "<daemon STATUS line>": exactly one POWER in the power domain (pdom) and exactly one HIGHPOWER, = 0
  local -a ws; local w p= h= np=0 nh=0; read -ra ws <<< "$1"
  for w in "${ws[@]}"; do case "$w" in POWER=*) p=${w#POWER=}; np=$((np + 1));; HIGHPOWER=*) h=${w#HIGHPOWER=}; nh=$((nh + 1));; esac; done
  [ "$np" = 1 ] || { echo "$np POWER field(s) in the STATUS (want 1)"; return 1; }; [ "$nh" = 1 ] || { echo "$nh HIGHPOWER field(s) in the STATUS (want 1)"; return 1; }
  pdom "$p" || { echo "POWER '$p' outside 0..17 dBm (no restore could set it back)"; return 1; }
  [ "$h" = 0 ] || { echo "HIGHPOWER is '$h', not 0"; return 1; }; echo "$p"; }
jfield() { local -a ws; local w v= n=0; read -ra ws <<< "$1"; for w in "${ws[@]}"; do case "$w" in "$2="*) v=${w#*=}; n=$((n + 1));; esac; done
  [ "$n" = 1 ] && [ -n "$v" ] || { echo "$n $2 field(s) in the STATUS (want 1)"; return 1; }; echo "$v"; }
jgrant() {   # jgrant "<radio rows>" <rid>: exactly one row of the rid, its high-power grant None or False
  local rows n hp; rows=$(printf '%s\n' "$1" | grep -E "^ROW $2 "); n=$(printf '%s\n' "$rows" | grep -c .)
  [ "$n" = 1 ] || { echo "$n hardware row(s) of $2 (want 1)"; return 1; }; hp=$(printf '%s\n' "$rows" | grep -oE ' hp=[^ ]*$' | cut -d= -f2)
  case "$hp" in None|False) echo "$hp";; *) echo "the high-power grant of $2 is '$hp' (want None or False)"; return 1;; esac; }
jspower() {   # jspower <form> <value>: a stack's own power value in the power domain (pdom: Meshtastic's 0 is its maximum)
  pdom "$2" "$1" || { echo "power '$2' outside 0..17 dBm (Meshtastic 1..17: its 0 is the firmware's maximum)"; return 1; }; echo "$2"; }
jcap() { [[ "$1" =~ ^[0-9]{1,2}$ ]] && [ "$1" -ge 1 ] && [ "$1" -le "$PCAP" ]; }   # Meshtastic's max_power cap, 1..17
jmodel() {   # jmodel <box> <rid> <value>: the value <= min(17, the radio's model's power_default_dbm on that box), else the reason
  local m c; m=$(getk "MODEL_$1_$2"); c=$(getk "CAP_$1_$m"); [ -n "$m" ] && [[ "$c" =~ ^[0-9]{1,2}$ ]] || { echo "no model cap for $1:$2 ('$m' '$c')"; return 1; }
  [ "$c" -gt "$PCAP" ] && c=$PCAP; [ "$3" -le "$c" ] || { echo "power $3 above $c dBm (min(17, $m's power_default_dbm, KITCAP))"; return 1; }; echo "$3"; }
pnum() {
  local all v; all=$(body "$1" | grep -v '^[[:space:]]*$')
  case $2 in mt_pget) v=$(printf '%s\n' "$all" | sed -nE 's/^lora\.(tx_power|txPower): (-?[0-9]+)$/\2/p')
      [ "$(printf '%s\n' "$v" | grep -c .)" = 1 ] || return 0;;
    mc_pget) all=$(printf '%s\n' "$all" | grep -v '^INFO:meshcore:')                                  # meshcore-cli's logging lines only
      [ "$(printf '%s\n' "$all" | grep -c .)" = 1 ] || return 0; v=$(printf '%s\n' "$all" | grep -xE -- '-?[0-9]+');;
    *) [ "$(printf '%s\n' "$all" | grep -c .)" = 1 ] || return 0; v=$(printf '%s\n' "$all" | grep -xE -- '-?[0-9]+');;
  esac; printf '%s' "$v"; }
hpgrant() { pmust "$1" "HG-$3.txt" radios; [ "$MODE" = dry ] && return 0
  local o; o=$(jgrant "$(body "HG-$3.txt")" "$2") || stop "$3: $o: no TX"; }
dpower() {   # dpower <box> <rid> <key>: the daemon's STATUS of that radio: POWER <= min(17, model cap), HIGHPOWER=0, no grant
  hpgrant "$1" "$2" "$3"; must "$1" "PW-$3.txt" "{RT}/venv/lhpc/bin/python -c '$STATUS_PY' $2"; radk "$3" "$1:$2"; [ "$MODE" = dry ] && { pwok "$1:$2"; return 0; }
  local p o; p=$(jstatus "$(body "PW-$3.txt" | head -n 1)") || stop "$3: $p: no TX"; o=$(jmodel "$1" "$2" "$p") || stop "$3: $o: no TX"
  keep "P_$3" "$p"; pwok "$1:$2"; ok "$3: power as-is $p dBm, HIGHPOWER=0, no high-power grant, within its model's cap"; }
spower() {   # spower <box> <form> <rid> <key>: a stack's own power value (MeshCore transmits through the daemon: its STATUS too)
  hpgrant "$1" "$3" "$4"; [ "$2" != mc_pget ] || dpower "$1" "$3" "${4}D"
  if [ "$2" = mc_pget ]; then must "$1" "PW-$4.txt" "$(form "$2" rid="$3")"; else pmust "$1" "PW-$4.txt" "$2" "$3"; fi; radk "$4" "$1:$3"; [ "$MODE" = dry ] && { pwok "$1:$3"; return 0; }
  local p o; p=$(jspower "$2" "$(pnum "PW-$4.txt" "$2")") || stop "$4: $p (raw output in PW-$4.txt): no TX"; o=$(jmodel "$1" "$3" "$p") || stop "$4: $o: no TX"
  keep "P_$4" "$p"; pwok "$1:$3"; ok "$4: power as-is $p dBm, within its model's cap"; }
ppower() {   # ppower <key> <peer> <action> <low>: a PC peer set LOW for its row (the original journalled first), read back low
  local k=$1 pr=$2 a=$3 lo=$4 o r; pdom "$lo" "$a" || stop "$k: the row's low power '$lo' is outside 0..17 dBm"; radk "$k" "PC:$pr"
  pread PC "PP-$k-0.txt" "$a"; [ "$XRC" = 0 ] || stop "$k: the peer power read failed (rc $XRC)"; o=$(body "PP-$k-0.txt" | sed -n 's/^POWER //p' | tail -n 1)
  [ "$MODE" = dry ] || case "$o" in ''|*[!0-9-]*) stop "$k: no power readback";; esac
  jpower PC "$a" "${o:-10}"
  pset PC "PP-$k-1.txt" "$a" "$lo"; [ "$XRC" = 0 ] || stop "$k: the peer power set failed (rc $XRC)"; r=$(body "PP-$k-1.txt" | sed -n 's/^POWER //p' | tail -n 1)
  [ "$MODE" = dry ] || [ "$r" = "$lo" ] || stop "$k: the peer reads back '$r', not the low power $lo: no TX"; keep "P_$k" "$lo"; pwok "PC:$pr"; data "$k: peer power ${o:-?} -> $lo for the row"; }
hpower() {   # hpower <key> [<capture>]: the shared RNode through the PC's rnsd: <= 7 dBm, the running rnsd's config, Up; else STOP
  local k=$1 f=${2:-${CURROW:-0}-rnode-power.txt}; radk "$k" PC:rnode
  [ "$MODE" = dry ] && { echo "  [dry] PC > $f: hpower (rns-host config txpower, one rnsd started after its mtime, rnstatus Up)"; pwok PC:rnode; return 0; }
  printf '%s\n' "$HPOWER_PY" > "$SCRATCH/hpower.py"; pread PC "$f" hpower
  [ "$XRC" = 0 ] || stop "rnode: the shared RNode's power cannot be read now ($(tr '\n' ' ' < "$OUT/$f")): no TX"
  local tp nw up; tp=$(sed -n 's/^TXPOWER //p' "$OUT/$f"); nw=$(sed -n 's/^NEWER //p' "$OUT/$f"); up=$(sed -n 's/^UP //p' "$OUT/$f")
  pdom "$tp" || stop "rnode: no TXPOWER in 0..17 dBm in the read ($f): no TX"
  [ "$nw" = yes ] || stop "rnode: the rnsd runs an older config than the file (it was edited after the start): no TX"
  [ "$up" = yes ] || stop "rnode: the RNode 868 interface is not Up: no TX"
  [ "$tp" -le 7 ] || stop "rnode: the shared RNode is set to $tp dBm (above 7): no TX"
  keep "P_$k" "$tp"; pwok PC:rnode; ok "rnode: the shared RNode at $tp dBm (read now, <= 7)"; }
started() { keep "STARTED_$1_${3##*@}" 1; undo_push "$1" "$(gstop "$3")"; must "$1" "$2" "$(form start inst="$3")"; }   # its guarded stop, journalled BEFORE the start;
# STARTED_<box>_<rid>: RS counts that radio as one that could send (runrids), even if the row ends before its first tx

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
needs() { local f m=; for f in "$@"; do [ "$MODE" = live ] || echo "${CURROW:-0} $f" >> "$SCRATCH/needed"; case " $FORMS_OK " in *" $f "*) ;; *) m="$m $f";; esac; done
  [ -z "$m" ] && return 0; NEEDS_MISSING=${m# }; return 1; }
nonce() { local n; while :; do n="$NPREFIX $(od -An -N4 -tx1 /dev/urandom | tr -d ' \n')"
  grep -qxF -- "$n" "$OUT/nonces" 2>/dev/null && continue; echo "$n" >> "$OUT/nonces" || stop "nonces: append failed"; echo "$n"; return; done; }
boxrx() {   # boxrx <box> <file> <form> <rid> <nonce> <secs> [<key>=<value>...]: a marker line only (SEEN <nonce> <time> rssi=<n>),
  # never the RF-log or UART line; the extra keys fill the form's other placeholders
  X "$1" "$2" "for i in \$(seq 1 $6); do l=\$($(form "$3" rid="$4" n="$5" "${@:7}") | head -n 1); [ -n \"\$l\" ] && { echo \"SEEN $5 \$(date -u +%H:%M:%SZ) rssi=\$(printf '%s' \"\$l\" | sed -nE 's/.*rssi=(-?[0-9.]+).*/\\1/p')\"; exit 0; }; sleep 1; done; echo NOT-SEEN; exit 3"
  result "$2" "$5"; }
chatrx() {   # chatrx <box> <file> <rid> <nonce> <secs>: the nonce on the chat client's screen (its pane rfchat-<rid>, wrapped lines
  # joined); marker lines only, never a screen line (it holds received on-air text): SEEN <nonce> <time> screen, or on a miss
  # the pane's count of non-empty lines and NOT-SEEN (rc 3)
  X "$1" "$2" "for i in \$(seq 1 $5); do tmux capture-pane -p -J -t rfchat-$3 2>/dev/null | grep -qF -- '$4' && { echo \"SEEN $4 \$(date -u +%H:%M:%SZ) screen\"; exit 0; }; sleep 1; done; echo \"pane lines=\$(tmux capture-pane -p -J -t rfchat-$3 2>/dev/null | grep -c .)\"; echo NOT-SEEN; exit 3"
  result "$2" "$4"; }
result() { [ "$MODE" = dry ] && return 0
  if [ "$XRC" = 0 ] && grep -qxE "SEEN $(printf '%s' "$2" | sed 's/[.[\*^$]/\\&/g') .*" "$OUT/$1"; then return 0; fi
  if [ "$XRC" = 3 ] && [ "$(tail -n 1 "$OUT/$1")" = NOT-SEEN ]; then return 1; fi
  stop "$1: neither SEEN '$2' (rc 0) nor NOT-SEEN (rc 3): rc $XRC"; }
# apprx <box> <file> <rendered form> <nonce> <secs|once>: the word inside the app on the box: a marker line only (SEEN <nonce>
# <time>), never the app's output; <secs> polls the form (a queue read, MeshCore), `once` runs it once (a listener that holds the
# app's connection until it sees the word or times out, Meshtastic; XBG=1: started before the peer sends, ended by reapx)
apprx() { local seen="echo \"SEEN $4 \$(date -u +%H:%M:%SZ)\"; exit 0"
  if [ "$5" = once ]; then   # a listener: its own end is a line too (LISTENER-RC <rc>, 124 = its timeout): ended early is not "unseen"
    X "$1" "$2" "l=\$($3 | head -n 1); case \"\$l\" in 'LISTENER-RC 124'|'') echo NOT-SEEN; exit 3;; 'LISTENER-RC '*) echo \"LISTENER-ENDED \${l#LISTENER-RC }\"; exit 4;; *) $seen;; esac"
  else X "$1" "$2" "for i in \$(seq 1 $5); do l=\$($3 | head -n 1); [ -n \"\$l\" ] && { $seen; }; sleep 1; done; echo NOT-SEEN; exit 3"; fi; }
reapx() { [ -n "$1" ] || return 0; [ "$MODE" = dry ] && kill "$1" 2>/dev/null; wait "$1" 2>/dev/null   # a box run of XBG=1 X
  [ "$MODE" = dry ] && { XRC=0; return 0; }; XRC=$(cat "$OUT/${SPAWNF[$1]}.rc" 2>/dev/null || echo 99)
  [ "$XRC" -ne 255 ] || stop "${SPAWNF[$1]}: ssh transport rc 255"; }
lkill() { [ -n "${1:-}" ] || return 0; pkill -TERM -P "$1" 2>/dev/null; kill "$1" 2>/dev/null; wait "$1" 2>/dev/null; return 0; }   # a row's EXIT trap
appseen() {   # appseen <file> <nonce>: 0 the app shows it, 1 it does not, 2 the listener ended early (its own marker); dry:
  # DRY_APPMISS=<file> rehearses the word missing, DRY_APPMISS=<file>:ended the listener ending early
  [ "$MODE" = dry ] && { case "${DRY_APPMISS:-}" in "$1") return 1;; "$1:ended") return 2;; esac; return 0; }
  [ "$XRC" = 4 ] && grep -qxE 'LISTENER-ENDED [0-9]+' "$OUT/$1" && return 2; result "$1" "$2"; }
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

# restable [STOPPED]: THE result table for people: the verdict on top, then one line per chosen row with each direction's
# PASS / FAIL / NOT RUN and its reason; printed, and (live) written to RESULT.md; rc 1 if the file cannot be written
restable() {
  local id d r c cells rows= n=0 np=0 nf=0 nn=0 nd=0 v out
  for id in $RUN; do cells=
    for d in $(dirs "$id"); do d=$(dirtext "$d"); n=$((n + 1))
      r=$(awk -F'|' -v i="$id" -v d="$d" '$1 == i && $2 == d {sub(/^[^|]*[|][^|]*[|]/, ""); l = $0} END {print l}' "$OUT/results.txt" 2>/dev/null)
      case "$r" in PASS) np=$((np + 1)); c="✅ PASS";; DRY-PASS) nd=$((nd + 1)); c="⚪ DRY (rehearsed)";;
        "FAIL: "*) nf=$((nf + 1)); c="❌ FAIL: ${r#FAIL: }";; "NOT RUN: "*) nn=$((nn + 1)); c="⚪ NOT RUN: ${r#NOT RUN: }";;
        *) nn=$((nn + 1)); if [ "${1:-}" = STOPPED ]; then c="⚪ NOT RUN: not reached (the run stopped)"; else c="⚪ NOT RUN: no result"; fi;; esac
      cells="${cells:+$cells · }$d ${c//|//}"; done
    rows="$rows| $id | $(rowname "$id") | $cells |"$'\n'; done
  if [ "${1:-}" = STOPPED ]; then v="⛔ STOPPED: the run ended early ($np passed, $nf failed, $nn not run)"
  elif [ "$nf" -gt 0 ]; then v="❌ FAIL: $nf of $n directions failed"; [ "$nn" = 0 ] || v="$v, $nn not run"
  elif [ "$nd" -gt 0 ]; then v="⚪ DRY RUN: a rehearsal, nothing sent: $nd of $n directions rehearsed"; [ "$nn" = 0 ] || v="$v, $nn not run"
  elif [ "$nn" -gt 0 ] || [ "$n" = 0 ]; then v="⚪ NOT COMPLETE: $nn of $n directions not run"
  else v="✅ PASS: all $n directions passed"; fi
  out="RESULT: $v"$'\n\n'"| row | stack | directions |"$'\n'"|---|---|---|"$'\n'"$rows"; printf '%s' "$out"
  [ "$MODE" = dry ] || printf '%s' "$out" > "$EVD/RESULT.md"; }
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
  [ "$kind" != rnode ] || rnsinst "$1" "$rest"
  if [ "$kind" = meshcore-ble ]; then
    if [ "$MODE" = dry ]; then case " ${DRY_BLE:-} " in *" $id "*) ;; *) PK="absent: peer $1";; esac
    else pread PC "R0-$1-power.txt" mc-power; [ "$XRC" = 0 ] || PK="absent: peer $1"; fi   # its read-only power read: the connect
  else grep -q "^$id " "$OUT/R0-usb-byid.txt" || PK="absent: peer $1"; fi; }
row_R0() {
  [ "$MODE" = dry ] || { [ ! -e "$EVD" ] || stop "$EVD exists"; mkdir -p "$EVD" || stop "$EVD: mkdir"; : > "$EVD/state.env"; : > "$EVD/undo.list"; : > "$EVD/undo.done"
    { pwnote | awk '{ print } END { if (NR) print "" }'
      printf '| row | stack | box | band | peer | direction | nonce seen | power used | result |\n|---|---|---|---|---|---|---|---|---|\n'; } > "$EVD/REPORT.md" || stop "REPORT.md: not written"
    printf '| job | box | instance | verdict |\n|---|---|---|---|\n' > "$EVD/MCSET.md" || stop "MCSET.md: not written"; }
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
    pmust $b "R0-$b-radios.txt" radios
    radios_ok "R0-$b-radios.txt" || stop "R0 $b: the hardware list is not valid (want exactly one 'STATE valid' and well-formed ROW lines): $(body "R0-$b-radios.txt" | grep -v '^ROW ' | head -n 1)"
    if [ "$MODE" = dry ]; then echo "  [dry] PC > R0-$b-caps.txt: the repo's radio_models (dry uses the repo copy)"; python3 -I -c "$CAPS_PY" "$REPO" > "$OUT/R0-$b-caps.txt" 2>&1 || stop "R0 $b: the repo's radio_models cannot be read"
    else pmust $b "R0-$b-caps.txt" caps; fi   # the installed copy (its loader); unreadable = STOP
    grep -qE '^CAP [a-z0-9-]+ [0-9]+$' "$OUT/R0-$b-caps.txt" || stop "R0 $b: no model power cap read (R0-$b-caps.txt)"
    while read -r _ m c; do [ -n "${KITCAP[$m]:-}" ] && [ "$c" -gt "${KITCAP[$m]}" ] && c=${KITCAP[$m]}; keep "CAP_${b}_$m" "$c"; done < <(grep -E '^CAP ' "$OUT/R0-$b-caps.txt")
    must $b "R0-$b-tx.txt" "$(txcount $b)"   # each radio's RF-log and run-log facts at T0 (TXC_PY), for RS
    must $b "R0-$b-status.txt" "{LH} status"; must $b "R0-$b-runset.txt" "$RUNSET"
    [ "$MODE" = dry ] && continue
    body "R0-$b-status.txt" | zs_check || stop "R0 $b: an incomplete status"
    keyset_ok "R0-$b-runset.txt" || stop "R0 $b: the key list has a line that is not an instance key (R0-$b-runset.txt)"
    local keys s ord; keys=$(body "R0-$b-runset.txt" | grep -v '^$' | tr '\n' ' ')
    for s in $(body "R0-$b-status.txt" | zs_running); do s=${s%%@*}; case " $keys" in *" $s@"*) ;; *) stop "R0 $b: '$s' runs (status) but has no instance key: unclaimed";; esac; done
    ord=$(body "R0-$b-status.txt" | ordkeys $keys) || stop "R0 $b: an instance key's stack has no status header ($keys)"
    keep "RUN_$b" "$ord"; data "R0 $b: running instances: '${ord}'"
  done
  local r e ep k o why devs= rdevs= pr pk bud held wv i w; declare -A SUM=()
  for r in $RUN; do why=; for ep in $(reps "$r"); do case "$ep" in -) continue;; esac
      if [ "${ep%%:*}" = peer ]; then peerok "${ep#*:}"; o=$PK; [ -z "$o" ] || why="$why$o; "
        k="PEER_${ep#*:}"; read -r pk pr _ <<< "${!k:-}"; [ -n "$o" ] || [[ "$pr" == *:*:* ]] || devs="$devs $(shq "/dev/serial/by-id/$pr")"
        [ -n "$o" ] || [ "$pk" != rnode ] || rdevs="$rdevs /dev/serial/by-id/$pr "   # the Reticulum rows' RNode: the PC's rnsd holds it (hpower)
      else k=${ep#*:}; radiokey "$k"; o=$RK; case "$o" in "why "*) why="$why${o#why }; ";;
          rid*) read -r _ v bb m <<< "$o"; [ "$bb" = "${ep%%:*}" ] || stop "row $r: $k is on box $bb, the table says ${ep%%:*}"
            [[ "$v" =~ ^r([1-9]|[1-9][0-9])$ ]] || stop "R0: $k = '$v' is not a radio id"; keep "$k" "$v"; keep "MODEL_${bb}_$v" "$m"; data "R0: row $r: $k = $bb:$v ($m)";; esac; fi; done
    [ -z "$why" ] || { keep "SKIP_$r" "$(printf '%s' "${why%; }" | awk -v RS='; ' '!s[$0]++' | paste -sd ';' | sed 's/;/; /g')"; continue; }
    w=(1 1); [ "$(rowf "$r" kind)" != stack ] || read -ra w <<< "${WORST[$r]}"   # the worst case per endpoint (A B member)
    i=0; for ep in $(reps "$r"); do wv=${w[$i]:-0}; i=$((i + 1)); [ "$wv" -gt 0 ] || continue
      if [ "${ep%%:*}" = peer ]; then pr="PC:${ep#*:}"; else pr="${ep%%:*}:$(getk "${ep#*:}")"; fi; SUM[$pr]=$(( ${SUM[$pr]:-0} + wv )); done; done
  r0grants
  [ -z "$devs" ] || { X PC R0-peer-ports.txt "python3 -I -c $(printf %q "$PORTS_PY")${rdevs:+ $(shq "--rns=$RNSCFG")} $devs"; [ "$MODE" = dry ] || { [ "$XRC" = 0 ] || stop "R0: the peer ports cannot be checked (gpsd answer unreadable?)"
    held=$(heldports R0-peer-ports.txt "$rdevs"); [ -z "$held" ] || stop "R0: a peer port is held: $held (free it first: gpsd, a console; the RNode's rnsd only as your user with --config RNS_CONFIG)"
    ! grep '^HELD ' "$OUT/R0-peer-ports.txt" | grep -q ' rnsd-shared$' || data "R0: the RNode's port is held by rnsd alone (the shared RNode of the Reticulum rows)"; }; }
  for pr in $(printf '%s\n' "${!SUM[@]}" | sort); do say "BUDGET | $pr | ${SUM[$pr]} | $TXMAX"; done
  for pr in "${!SUM[@]}"; do [ "${SUM[$pr]}" -le $TXMAX ] || stop "budget $pr: ${SUM[$pr]} of $TXMAX"; done; }
heldports() {   # heldports <PORTS_PY capture> "<the RNode ports, each with a space either side>": the HELD lines that STOP, joined
  # by '|'. The RNode's port held only by this user's rnsd running RNS_CONFIG (rnsd-shared) is the shared setup the Reticulum rows need
  # (hpower then checks that rnsd); any other or extra holder, and any holder of another peer's port, STOPs.
  local d w; grep '^HELD ' "$OUT/$1" | while read -r _ d w; do case "$2" in *" $d "*) [ "$w" = rnsd-shared ] && continue;; esac
    echo "HELD $d $w"; done | paste -sd '|'; }
r0grants() {   # before anything starts, every resolved radio of the chosen rows has no high-power grant (hp= None/False), else STOP
  local r ep k v o; for r in $RUN; do [ -z "$(getk "SKIP_$r")" ] || continue; for ep in $(reps "$r"); do case "$ep" in peer:*|-) continue;; esac
    k=${ep#*:}; v=$(getk "$k"); o=$(jgrant "$(body "R0-${ep%%:*}-radios.txt")" "$v") || stop "R0: row $r, $k = $v: $o: nothing starts"; done; done
  ok "R0: no high-power grant on any radio of rows $RUN"; }
row_R1() { local b i keys rev; for b in $BOXES; do   # instance keys; starts journalled first, dependents stopped first
  if [ "$MODE" = dry ]; then keys="daemon@r1 kiss@r1"; else hask "RUN_$b" || stop "R1: RUN_$b was never recorded"; keys=$(getk "RUN_$b"); fi
  [ -n "$keys" ] || { data "R1 $b: nothing runs (the recorded set is empty): nothing to stop"; continue; }
  rev=$(printf '%s\n' $keys | tac | tr '\n' ' ')
  for i in $rev; do undo_push $b "$(gstart "$i")"; done          # newest first in recovery = the daemon first, dependents after
  for i in $rev; do must $b "R1-$b-stop-$i.txt" "$(gstop "$i")"; done; done; }

row_M() {   # the pre-step M, after R1 (nothing of ours runs): one mcset.py job per stack row of this run; its changes are KEPT
  # row 4 runs only after a J4 PASS (it reads MeshCore's mode, chat only: a repeater relays frames no tx counts): fail closed
  [ "$MCSET" = on ] || { say "M: MCSET=off: the pre-step is not run"; [ "$MODE" = dry ] || echo "| — | — | — | NOT RUN: MCSET=off |" >> "$EVD/MCSET.md"
    m4skip "MCSET=off: J4 did not check MeshCore's mode"; return 0; }
  local r j sid src ks ep b k rid band v ov
  for r in $RUN; do [ -n "${MJOB[$r]:-}" ] || continue; read -r j sid src ks <<< "${MJOB[$r]}"
    [ -z "$(getk "SKIP_$r")" ] || { mrec "$j" - "$sid" "NOT RUN: row $r: $(getk "SKIP_$r")"; continue; }
    ep=$(rowf "$r" a); b=${ep%%:*}; k=${ep#*:}; rid=$(getk "$k"); v="RADIO_$k"; read -r _ _ band _ <<< "${!v}"; src=${src//\{band\}/$band}
    ov=-; if [ "$j" = J1 ]; then [ -n "$MCSET_J1" ] || { mrec "$j" "$b" "$sid@$rid" "NOT RUN: no per-radio callsign given (MCSET_J1)"; continue; }; ov=$MCSET_J1; fi
    XIN "$b" "M-$j-$sid@$rid.txt" "OVERRIDE = '$ov'"$'\n'"$MCSET_PY" "cd {RT} && ./venv/lhpc/bin/python - $j $sid $src $rid $ks $WEBPORT {RT}"; [ "$MODE" = dry ] && continue   # the value on stdin only
    v=$(grep '^JOB ' "$OUT/M-$j-$sid@$rid.txt" | tail -n 1)
    if [ "$XRC" != 0 ] || [ -z "$v" ]; then rec "M $j $sid@$rid: the helper ended rc $XRC without a verdict: the instance's settings are unknown (M-$j-$sid@$rid.txt)"; v="JOB $j $sid@$rid UNKNOWN: helper rc $XRC"; fi
    case "$v" in *" UNRESOLVED"*) rec "M: $v";; esac
    mrec "$j" "$b" "$sid@$rid" "${v#JOB $j $sid@$rid }"
    case "$j ${v#JOB $j $sid@$rid }" in "J4 PASS"*|J[!4]*) ;; *) m4skip "J4: ${v#JOB $j $sid@$rid }";; esac; done; }
m4skip() { case " $RUN " in *" 4 "*) [ -n "$(getk SKIP_4)" ] || keep SKIP_4 "$1";; esac; }   # row 4 NOT RUN by name (runrow)
mrec() {   # mrec <job> <box> <instance> <verdict>: one MCSET.md line; anything but PASS/NOOP is also named for the coordinator
  say "M $1 $3: $4"; [ "$MODE" = dry ] && return 0; echo "| $1 | $2 | $3 | $4 |" >> "$EVD/MCSET.md" || stop "MCSET.md: append failed"
  case "$4" in PASS*|NOOP*) ;; *) echo "M $1 $3: $4" >> "$EVD/NOT-RUN.txt" || stop "NOT-RUN.txt: append failed";; esac; }

# ---- 11. the rows -------------------------------------------------------------------------------------------------------------
usbcheck() {   # usbcheck <file> <KEY>: a USB radio (serial= or port= in its key) is still there after a frame, else STOP
  local v="RADIO_$2" b m bd sel; read -r b m bd sel <<< "${!v}"; case "$sel" in
    serial=*) must "$b" "$1" "grep -lxF -- $(shq "${sel#serial=}") /sys/bus/usb/devices/*/serial 2>/dev/null | wc -l";;
    port=*) must "$b" "$1" "test -e /sys/bus/usb/devices/$(shq "${sel#port=}")/idVendor && echo 1 || echo 0";; *) return 0;; esac
  [ "$MODE" = dry ] || [ "$(body "$1")" = 1 ] || stop "$2 ($b) is gone after a frame (brown-out / re-enumeration)"; }
frame_row() {   # frame_row <id> <box:KEY> <box:KEY> <power a,b> <flags>: the daemons of both radios, one nonce frame each way
  local id=$1 pa=${4%,*} pb=${4#*,} fl=$5 m ka=${2#*:} kb=${3#*:} ba=${2%%:*} bb=${3%%:*} ra rb e x n p0
  needs $(rforms "$id") || { notrun_row "$id" "form not confirmed: $NEEDS_MISSING"; return 0; }
  m=$(undo_mark) || stop "undo journal: unreadable (no mark)"; ra=$(getk "$ka"); rb=$(getk "$kb")
  started "$ba" "$id-start-a.txt" "daemon@$ra"; started "$bb" "$id-start-b.txt" "daemon@$rb"
  local qa="R$id-$ba-$ra" qb="R$id-$bb-$rb"; dpower "$ba" "$ra" "$qa"; dpower "$bb" "$rb" "$qb"
  local la lb f; la=$(body "PW-$qa.txt" | head -n 1); lb=$(body "PW-$qb.txt" | head -n 1)   # the parameters both ends must share
  for f in FREQ SF; do x=$(jfield "$la" $f) || stop "row $id: $ba:$ra: $x"; n=$(jfield "$lb" $f) || stop "row $id: $bb:$rb: $n"
    [ "$x" = "$n" ] || fail "incompatible: $f $x vs $n"; done
  data "row $id: FREQ and SF match ($(jfield "$la" FREQ), SF $(jfield "$la" SF)); BW, CR and the sync word are not in the daemon's STATUS: not compared"
  for e in "a $ba $ra $qa $pa" "b $bb $rb $qb $pb"; do set -- $e; [ "$5" = min ] || continue
    p0=$(getk "P_$4"); [ "$MODE" = live ] || p0=${p0:-10}   # the undo restores only the recorded value: none = STOP
    [ -n "$p0" ] || stop "row $id: no recorded power of $2:$3: no undo, no set, no TX"
    jpower "$2" "$3" "$p0"; pset "$2" "$id-set-$1.txt" "$3" "$STICK_MIN"; [ "$XRC" = 0 ] || stop "$id-set-$1.txt: exit status $XRC"; dpower "$2" "$3" "$4"
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

row_1() {   # MeshCom box radio 433.175 <-> the MeshCom T-Deck. Peer -> box: the T-Deck sends at its OWN stored power, read first
  # (two agreeing --lora windows) and never changed: outside THE power domain 0..17 dBm (its 22 of 11-10) is a STOP before any
  # change of the row; it is set into the domain once by hand (on the order, its undo saved), as the G2's 27 -> 17.
  # Each direction passes only with both its checks: inside MeshCom on the receiver (the T-Deck's RX-LoRa2 print; the box node's
  # RX-LoRa2 line in its UART log) and r's RF log (the box radio's TX of the frame; its RX of the T-Deck's frame).
  local b=${1%%:*} k=${1#*:}
  needs $(rforms 1) || { notrun_row 1 "form not confirmed: $NEEDS_MISSING"; return 0; }
  local m r n n2 a src pr tp; m=$(undo_mark) || stop "undo journal: unreadable (no mark)"; r=$(getk "$k"); n=$(nonce); n2=$(nonce)
  pread PC 1-peer-power.txt meshcom-power; [ "$XRC" = 0 ] || stop "row 1: the T-Deck's power read failed (rc $XRC): no TX"   # a READ, never a write
  tp=$(body 1-peer-power.txt | sed -n 's/^POWER //p' | tail -n 1)
  [ "$MODE" = dry ] || pdom "$tp" || stop "row 1: the T-Deck reads '${tp}' dBm (outside 0..17 or unreadable); this run never changes it: set its stored power to 17 or less by hand first (peer.py meshcom-power <p>, on the order, its undo saved): no TX"
  keep P_R1-tdeck "${tp:-dry}"; radk R1-tdeck PC:tdeck; pwok PC:tdeck; data "row 1: the T-Deck at its own ${tp:-dry} dBm, not changed"
  started "$b" 1-daemon.txt "daemon@$r"; dpower "$b" "$r" "R1-$b-$r-d"   # the daemon's STATUS before the stack's own start
  started "$b" 1-start.txt "meshcom@$r"; dpower "$b" "$r" "R1-$b-$r"
  X "$b" 1-provision.txt "$(printf "$PROVWAIT" "$r")"; [ "$MODE" = dry ] || [ "$XRC" = 0 ] || fail "row 1: the node's provisioning check did not finish ($(tail -n 1 "$OUT/1-provision.txt"))"
  leg "box->peer"   # the leg that runs now: a FAIL from here is its FAIL (runrow)
  BG=1 peer 1-peer-read.txt "$VMT" meshcom-read "$n" 150; pr=$SPAWN; [ "$MODE" = dry ] || sleep 8
  for a in 1 2 3; do [ "$a" = 1 ] || [ "$MODE" = dry ] || sleep 5   # at most 3 console attempts, one tx each (dry rehearses all three)
    tx "R1-$b-$r" 1 "$n" -- XIN "$b" "1-box-send-$a.txt" "$MCSEND_PY" "python3 - '$n' $(rport 12323 "$r")"; src=$XRC
    [ "$MODE" = dry ] || [ "$src" != 0 ] || break; done
  reap "$pr"
  [ "$MODE" = dry ] || [ "$src" = 0 ] || fail "row 1 box -> peer: the node's console did not take '$n' (rc $src: 4 = no banner, 3 = no echo)"
  result 1-peer-read.txt "$n" || fail "box -> peer: not in MeshCom on the T-Deck: $n"
  boxrx "$b" 1-box-tx.txt tx_mcom "$r" "$n" 30 nh="$(nhex "$n")" || fail "box -> peer: in MeshCom on the T-Deck, but no TX (outcome=ok) of '$n' on $r's RF log"
  REPORT 1 meshcom "$b ($r)" "433.175 MHz" "MeshCom T-Deck" "box -> peer" "yes (in MeshCom on the T-Deck + TX on $r)" "$(getk "P_R1-$b-$r" as-is) dBm (as-is)" PASS
  # peer -> box: the box node prints what it accepts only with --setinfo on (RAM only); its off is journalled BEFORE the on, so
  # the row's end, a FAIL (runrow's undo_to) and a STOP or signal (recover) all restore it; a lost box H loses the flag with its node
  leg "peer->box"   # the leg that runs now: a FAIL from here is its FAIL (runrow)
  undo_push "$b" "$(setinfo off "$r")"; X "$b" 1-setinfo-on.txt "$(setinfo on "$r")"
  [ "$MODE" = dry ] || [ "$XRC" = 0 ] || fail "peer -> box: the box node did not take --setinfo on (rc $XRC: 4 = no banner, 3 = no echo)"
  tx R1-tdeck 1 "$n2" -- peer 1-peer-send.txt "$VMT" meshcom-send "$n2"; [ "$XRC" = 0 ] || stop "row 1: the T-Deck send failed (rc $XRC)"
  boxrx "$b" 1-box-app.txt rx_mcom_app "$r" "$n2" 90 idx="$(mcidx "$r")" || fail "peer -> box: not in MeshCom on $r (no RX-LoRa2 line of '$n2' in its UART log)"
  boxrx "$b" 1-box-rx.txt rx_mcom "$r" "$n2" 30 nh="$(nhex "$n2")" || fail "peer -> box: in MeshCom on $r, but no RX of '$n2' on $r's RF log"
  REPORT 1 meshcom "$b ($r)" "433.175 MHz" "MeshCom T-Deck" "peer -> box" "yes (in MeshCom on $r + RX on $r)" "the T-Deck at its own ${tp:-dry} dBm (not changed)" PASS
  undo_to "$m"; }

row_2() {   # KISS + Graywolf box radio 433.775 <-> the APRS tracker (as-is): box -> peer = the message on the tracker; peer -> box = its ACK
  local b=${1%%:*} k=${1#*:}
  needs $(rforms 2) || { notrun_row 2 "form not confirmed: $NEEDS_MISSING"; return 0; }
  [ -n "$TRACKER_CALL" ] || { notrun_row 2 "no tracker call-SSID given at GO (TRACKER_CALL)"; return 0; }
  [ -n "$TRACKER_DBM" ] || [ "${TRACKER_ASIS:-}" = 1 ] || { notrun_row 2 "no tracker power given at GO (TRACKER_DBM, from its portal; or TRACKER_ASIS=1): its ACK is a TX"; return 0; }
  [ -n "$TRACKER_DBM" ] || say "row 2: the tracker as-is (his word 11-18: run as-is without power check)"
  local m r n pr grc; m=$(undo_mark) || stop "undo journal: unreadable (no mark)"; r=$(getk "$k"); n=$(nonce)
  started "$b" 2-daemon.txt "daemon@$r"; dpower "$b" "$r" "R2-$b-$r-d"
  started "$b" 2-start.txt "graywolf@$r"; undo_push "$b" "$(gstop "kiss@$r")"; dpower "$b" "$r" "R2-$b-$r"
  BG=1 peer 2-peer-read.txt "$VMT" aprs-read "$n" 150; pr=$SPAWN; [ "$MODE" = dry ] || sleep 8
  keep P_R2-tracker "${TRACKER_DBM:-as-is}"; radk R2-tracker PC:tracker; pwok PC:tracker   # its power from its portal, or as-is (TRACKER_ASIS)
  leg "box->peer"   # the leg that runs now: a FAIL from here is its FAIL (runrow)
  tx R2-tracker 1 control --reserve                                                # its ACK, which the message triggers
  tx "R2-$b-$r" 1 "$n" -- XIN "$b" 2-box-send.txt "TO = '$TRACKER_CALL'"$'\n'"$GW_PY" "{RT}/venv/lhpc/bin/python - '$n' $(rport 8080 "$r") $r"; grc=$XRC
  reap "$pr"
  [ "$MODE" = dry ] || grep -q '^SENT ' "$OUT/2-box-send.txt" || fail "row 2 box -> peer: the Graywolf send failed (rc $grc): no message was sent"
  result 2-peer-read.txt "$n" || fail "not heard: $n"
  REPORT 2 "kiss + graywolf (APRS)" "$b ($r)" "433.775 MHz" "the APRS tracker" "box -> peer" yes "$(getk "P_R2-$b-$r" as-is) dBm (as-is)" PASS
  leg "peer->box"   # the leg that runs now: a FAIL from here is its FAIL (runrow)
  [ "$MODE" = dry ] || { [ "$grc" = 0 ] && grep -q '^ACKED ' "$OUT/2-box-send.txt"; } || fail "row 2 peer -> box: no ACK: call not verified ('$n', graywolf rc $grc)"
  [ "$MODE" = dry ] || ackjudge 2-box-send.txt "$n"
  REPORT 2 "kiss + graywolf (APRS)" "$b ($r)" "433.775 MHz" "the APRS tracker" "peer -> box" "the tracker's ACK (acked in Graywolf; the frame's source = the given call)" "$([ -n "$TRACKER_DBM" ] && echo "the tracker's $TRACKER_DBM dBm, from its portal (not read)" || echo "the tracker as-is (his word 11-18: no power check)")" PASS
  undo_to "$m"; }

ackjudge() {   # ackjudge <file> <nonce>: row 2 peer -> box: the Graywolf helper found the ACK frame from the given call, else FAIL
  ! grep -qx CALL-DIFF "$OUT/$1" || fail "row 2 peer -> box: the ACK frame's source differs from the given call (not the tracker's ACK)"
  grep -qx CALL-MATCH "$OUT/$1" || fail "row 2 peer -> box: Graywolf has '$2' acked, but no ACK frame in the rf log"; }

row_3() {   # Meshtastic box radio 868 (EU_868 LongFast) <-> the Station G2 (set to 1, its lowest, restored after)
  local b=${1%%:*} k=${1#*:}
  needs $(rforms 3) || { notrun_row 3 "form not confirmed: $NEEDS_MISSING"; return 0; }
  local m r n1 n2 cap ap wp got pr lp seen=; m=$(undo_mark) || stop "undo journal: unreadable (no mark)"; r=$(getk "$k"); n1=$(nonce); n2=$(nonce)
  pmust "$b" 3-cap.txt mt_cap "$r"; cap=$(body 3-cap.txt | tail -n 1)   # the cap every frame of the node is clamped to
  [ "$MODE" = dry ] || jcap "$cap" || stop "row 3: meshtastic@$r max_power '$cap' is not 1..17: nothing starts"
  started "$b" 3-start.txt "meshtastic@$r"
  ap=$(rport 4403 "$r"); wp=$(rport 9443 "$r")
  must "$b" 3-ready.txt "for i in \$(seq 1 30); do (exec 3<>/dev/tcp/127.0.0.1/$ap) 2>/dev/null && { echo READY $ap; exit 0; }; sleep 1; done; echo NOT-READY $ap; exit 3"
  must "$b" 3-listen.txt "ss -Htlnp | grep -F '\"meshtasticd\"' || true"
  [ "$MODE" = dry ] || { got=$(body 3-listen.txt | mt_ports); [ "$got" = "$(printf '%s\n' "$ap" "$wp" | sort -n | tr '\n' ' ')" ] \
    || fail "row 3: meshtastic@$r's meshtasticd listens on '$got', want exactly '$ap $wp' (its own API and web ports)"; ok "row 3: meshtastic@$r binds $ap + $wp, ready on $ap"; }
  spower "$b" mt_pget "$r" "R3-$b-$r"
  ppower R3-g2 g2 mt-power 1
  leg "peer->box"   # the leg that runs now: a FAIL from here is its FAIL (runrow)
  # Meshtastic on the box listens BEFORE the G2 sends (no history to read later); reaped before any check that can end the row
  XBG=1 apprx "$b" 3-box-app.txt "$(form rx_mt_app rid="$r" n="$n1" s=90)" "$n1" once; lp=$SPAWN; trap 'lkill "$lp"' EXIT
  [ "$MODE" = dry ] || sleep 10
  tx R3-g2 1 "$n1" -- peer 3-peer-send.txt "$VMT" mt-send "$n1"; [ "$XRC" = 0 ] || stop "row 3: the G2 send failed"
  reapx "$lp"; trap - EXIT; lp=; appseen 3-box-app.txt "$n1"; seen=$?
  boxrx "$b" 3-box-rx.txt rx_mt "$r" "$n1" 90 || fail "not heard: $n1"
  case $seen in 0) ;; 2) fail "the Meshtastic listener on the box ended before its time: $n1";;
    *) fail "in the radio log, not in Meshtastic on the box: $n1";; esac
  REPORT 3 meshtastic "$b ($r)" "868, EU_868 LongFast" "Station G2" "peer -> box" "yes (RX on $r, in Meshtastic)" "the G2 at 1 (lowest)" PASS
  leg "box->peer"   # the leg that runs now: a FAIL from here is its FAIL (runrow)
  BG=1 peer 3-peer-read.txt "$VMT" mt-read "$n2" 90; pr=$SPAWN; [ "$MODE" = dry ] || sleep 10
  keep "MTX_${b}_$r" $(( $(getk "MTX_${b}_$r" 0) + 1 ))   # its log never carries this send: RS adds it (mtled)
  tx "R3-$b-$r" 1 "$n2" -- must "$b" 3-box-send.txt "$(form mt_send rid="$r" n="$n2")"; reap "$pr"
  result 3-peer-read.txt "$n2" || fail "not heard: $n2"
  REPORT 3 meshtastic "$b ($r)" "868, EU_868 LongFast" "Station G2" "box -> peer" yes "$(getk "P_R3-$b-$r" as-is) dBm (as-is)" PASS
  undo_to "$m"; }

row_4() {   # MeshCore box radio 869.618 (eu_uk_narrow) <-> the T-Deck Pro (BLE; its power is read, never changed)
  local b=${1%%:*} k=${1#*:}
  needs $(rforms 4) || { notrun_row 4 "form not confirmed: $NEEDS_MISSING"; return 0; }
  local m r n1 n2 cp tp pr ot; m=$(undo_mark) || stop "undo journal: unreadable (no mark)"; r=$(getk "$k"); n1=$(nonce); n2=$(nonce)
  started "$b" 4-daemon.txt "daemon@$r"; dpower "$b" "$r" "R4-$b-$r-d"
  started "$b" 4-start.txt "meshcore@$r"
  must "$b" 4-cport.txt "cd {RT} && ./venv/lhpc/bin/python -c '$CPORT_PY' meshcore@$r"; cp=$(rport 5000 "$r")
  [ "$MODE" = dry ] || eq "row 4: meshcore@$r's own companion endpoint is the derived port" "$(body 4-cport.txt | sed -n 's/^CPORT //p')" "$cp"
  must "$b" 4-listen.txt "ss -Htlnp 'sport = :$cp'"; [ "$MODE" = dry ] || check_listener 4-listen.txt "$cp"
  must "$b" 4-listener.txt "ps -o pgid= -p ${LP:-0} | tr -d ' '"; [ "$MODE" = dry ] || body 4-cport.txt | grep -qx "OWNED $(body 4-listener.txt)" \
    || stop "row 4: the listener pid $LP on $cp is not in a live owned meshcore-node group of meshcore@$r"
  # the app check pops the node's queue: another client of it (the web UI, an interactive meshcore-cli) would drain the word first
  ot=$(body 4-cport.txt | sed -n 's/^OTHER //p' | tr '\n' ' '); [ -z "$ot" ] || { undo_to "$m" || stop "row 4: undo failed"
    notrun_row 4 "another MeshCore client of meshcore@$r runs (${ot% }): it would drain the messages the app check reads"; return 0; }
  spower "$b" mc_pget "$r" "R4-$b-$r"
  pread PC 4-peer-power.txt mc-power; [ "$XRC" = 0 ] || stop "row 4: the T-Deck Pro power read failed (rc $XRC)"   # a READ, never a write
  tp=$(body 4-peer-power.txt | sed -n 's/^POWER //p' | tail -n 1)
  [ "$MODE" = dry ] || pdom "$tp" || stop "row 4: the T-Deck Pro reads '${tp}' dBm (outside 0..17 or unreadable); its power is never changed: no TX"
  keep P_R4-tdpro "${tp:-dry}"; radk R4-tdpro PC:tdpro; pwok PC:tdpro; data "row 4: the T-Deck Pro at its own ${tp:-dry} dBm, not changed"
  leg "peer->box"   # the leg that runs now: a FAIL from here is its FAIL (runrow)
  tx R4-tdpro 1 "$n1" -- peerpin 4-peer-send.txt mc-send "$n1"; [ "$XRC" = 0 ] || stop "row 4: the T-Deck Pro send failed"
  boxrx "$b" 4-box-rx.txt rx_mc "$r" "$n1" 90 || fail "not heard: $n1"
  apprx "$b" 4-box-app.txt "$(form rx_mc_app rid="$r" n="$n1")" "$n1" 30   # MeshCore on the box queues it for its clients
  appseen 4-box-app.txt "$n1" || fail "in the radio log, not in MeshCore on the box: $n1"
  REPORT 4 meshcore "$b ($r)" "869.618 MHz, eu_uk_narrow" "T-Deck Pro (BLE companion)" "peer -> box" "yes (RX on $r, in MeshCore)" "the T-Deck Pro at its own ${tp:-dry} dBm (not changed)" PASS
  leg "box->peer"   # the leg that runs now: a FAIL from here is its FAIL (runrow)
  BG=1 peerpin 4-peer-read.txt mc-read "$n2" 90; pr=$SPAWN; [ "$MODE" = dry ] || sleep 15
  tx "R4-$b-$r" 1 "$n2" -- must "$b" 4-box-send.txt "$(form mc_send rid="$r" n="$n2")"; reap "$pr"
  result 4-peer-read.txt "$n2" || fail "not heard: $n2"
  REPORT 4 meshcore "$b ($r)" "869.618 MHz, eu_uk_narrow" "T-Deck Pro (BLE companion)" "box -> peer" yes "$(getk "P_R4-$b-$r" as-is) dBm (as-is)" PASS
  undo_to "$m"; }

# ---- the Reticulum rows (5, 12, 13): Reticulum + MeshChat on box radios <-> the RNode through the PC's shared rnsd (<= 7 dBm) --
rnsread() {   # rnsread <id> <box> <rid> <backend> <tag>: the shipped defaults, read only (the kit sets nothing): rnode_framing yes
  # and the row's backend (5: spi, 12 and 13: daemon), else STOP; RS counts the radio's Reticulum log by that backend (rnsrids)
  local id=$1 b=$2 r=$3 be=$4 t=$5 o
  must "$b" "$id-framing$t.txt" "$(form rt_get rid="$r" k=rnode_framing)"; o=$(body "$id-framing$t.txt" | tail -n 1)
  [ "$MODE" = dry ] || [ "$o" = yes ] || stop "row $id: rnode_framing reads '$o', not yes (the product default): the kit sets nothing, no TX"
  must "$b" "$id-backend$t.txt" "$(form rt_get rid="$r" k=backend)"; o=$(body "$id-backend$t.txt" | tail -n 1)
  [ "$MODE" = dry ] || [ "$o" = "$be" ] || stop "row $id: backend reads '$o', not $be (the row's backend): the kit sets nothing, no TX"
  keep "RNS_${b}_$r" "$be"; }
rnsx() {   # rnsx <id> <tag> <box> <rid> <rnode key> <key>...: one exchange with the RNode through the node of meshchat@<rid>, whose
  # radios are the keys (the first one MeshChat's own): files <tag>-*; the peer -> box judge here, the box -> peer one the
  # caller's (rnsback). Every radio of the node is reserved for the box's announce (RNSANN), the delivery proof and the box's send.
  # RNSANN: start (default) = MeshChat's start, which announces; call = one announce call to the running MeshChat; known = no
  # announce: the peer takes the box's path from the PC's shared rnsd (answered locally, no RF; absent: the exchange fails). One
  # announce is 2 frames on every radio of the node: LXMF delivery + audio call (MeshChat 45f89a8 meshchat.py:2073, :2080).
  local id=$1 tg=$2 b=$3 r=$4 pk=$5; shift 5; local q=$1 x n1 n2 pr bh dest kn=()
  [ "${RNSANN:-start}" != known ] || kn=(known)
  local go=$SCRATCH/$tg-tx2.go pcd=$SCRATCH/$tg-pcdest.txt raw=$SCRATCH/$tg-lxmf.raw   # the peer's control files: private scratch, never the evidence
  n1=$(nonce); n2=$(nonce)
  # 1. launch: the exchange announces now, waits for the box's announce, and holds its message until the release file exists;
  # it listens BEFORE the MeshChat start, whose own announce (the product's start step) it must hear
  XRAW=$raw must "$b" "$tg-lxmf.txt" "cd {RT} && src/meshchat/.venv/bin/python -c '$MCHASH_PY' state/meshchat/identity"   # read only
  [ "$MODE" = live ] || cp "$OUT/$tg-lxmf.txt" "$raw" || stop "row $id: the dry raw copy"
  bh=$(sed -n 's/^LXMF //p' "$raw" 2>/dev/null | tail -n 1)   # the address goes to the evidence masked (redact), raw only to the private scratch
  [[ "$bh" =~ ^[0-9a-f]{32}$ ]] || stop "row $id: no LXMF address of the box's MeshChat ($tg-lxmf.txt): the PC cannot tell its announce"
  printf '%s\n' "$bh" >> "$SCRATCH/lxmf.list" || stop "row $id: lxmf.list not written"; data "row $id: the box's MeshChat LXMF address read (masked in the evidence)"
  BG=1 tx "$pk" 1 control -- peer "$tg-peer.txt" "$VRNS" rns-exchange "$n2" "$bh" 420 "$pcd" "$n1" "$go" "${kn[@]}"; pr=$SPAWN
  [ "$MODE" = dry ] || sleep 10
  # 3. the box's announce, on every radio of the node: MeshChat's start announces once (the product's start step), or one call
  case ${RNSANN:-start} in
    start) keep "STARTED_${b}_$r" 1; undo_push "$b" "$(gstop "meshchat@$r")"
      for x in "${@:2}"; do tx "$x" "$ANNF" control --reserve; done
      tx "$q" "$ANNF" control -- must "$b" "$tg-start.txt" "$(form start inst="meshchat@$r")";;
    call) for x in "${@:2}"; do tx "$x" "$ANNF" control --reserve; done
      tx "$q" "$ANNF" control -- must "$b" "$tg-announce.txt" "$(form rt_announce)";;
    known) ;;
    *) stop "row $id: RNSANN '$RNSANN' (start, call or known)";;
  esac
  # 2. release, every check right before the second phase: the RNode's power again, its path request, the box's delivery proof
  hpower "$pk" "$tg-rnode-power-2.txt"; tx "$pk" 1 control --reserve; for x in "$@"; do tx "$x" 1 control --reserve; done
  tx "$pk" 1 "$n1" -- X PC "$tg-tx2-go.txt" "LHPC_PROOF=$b:meshchat@$r printf '%s\n' '$n1' > $(shq "$go")"
  [ "$MODE" = live ] || printf '%s\n' "$n1" > "$go" || stop "row $id: the dry release file"   # dry: right after its tx
  RNSNOPATH=; if [ "${RNSSEND:-1}" = 1 ] && needs rt_send; then [ "$MODE" = dry ] || sleep 60; dest=$(cat "$pcd" 2>/dev/null || echo dry)
    [ "$MODE" = dry ] || [[ "$dest" =~ ^[0-9a-f]{32}$ ]] || stop "row $id: no PC destination hash"
    tx "$pk" 1 control --reserve   # the PC's delivery proof of the box's message: an RNode frame
    rtsend "$tg-box-send" "$b" "$r" "$dest" "$n2" "$@" || RNSNOPATH=1; fi
  reap "$pr"; RNSRC=$XRC; RNSN2=$n2
  [ "$MODE" = dry ] || grep -qxE "DELIVERED $n1 .*" "$OUT/$tg-peer.txt" || fail "row $id peer -> box: no delivery proof of '$n1' from the box ($tg-peer.txt)"
  boxrx "$b" "$tg-box-rx.txt" rx_mchat "$r" "$n1" 30 || fail "not heard: $n1"; }
rtsend() {   # rtsend <tag> <box> <node rid> <dest> <nonce> <key>...: the box's ONE bounded send (rt_send) to <dest>, the keys the
  # node's radios (the node picks one: each reserved); rc 1 = NO-PATH (nothing sent). Any other end than SENT (LATE 5, SEND-FAILED
  # 6, SIGALRM 142, timeout 124, ssh 255, ...) stops that box's node FIRST, whatever HTTP answered (LXMF retries until it is down)
  local tg=$1 b=$2 r=$3 d=$4 n=$5 x; shift 5
  for x in "$@"; do tx "$x" 1 control --reserve; done        # the one retry a late cancel can let slip (LATE ends the run)
  for x in "${@:2}"; do tx "$x" 1 control --reserve; done    # the node picks the radio
  tx "$1" 1 "$n" -- X "$b" "$tg.txt" "$(form rt_send dest="$d" n="$n")"
  if [ "$MODE" = dry ]; then case " ${DRY_NOPATH:-} " in *" $tg "*) XRC=4;; esac; fi   # DRY_NOPATH=<tag>...: rehearses that send's NO-PATH
  case "$XRC" in 0) return 0;; 4) return 1;; esac
  X "$b" "$tg-node-stop.txt" "$(gstop "reticulum@$r")"
  stop "$tg.txt: the box's send ended $XRC, not cancelled in time: $b's node is stopped first, no further TX"; }
rnsback() {   # rnsback <tag> [<where>]: the exchange's box -> peer word at the PC (rt_send only), else FAIL; RNSB = the REPORT's
  # note; <where>: this exchange sends nothing on purpose (row 13: the box -> peer proof is in that leg)
  XRC=$RNSRC; if [ -n "${2:-}" ]; then RNSB=" (box -> peer: in $2)"; elif ! needs rt_send; then RNSB=" (box -> peer NOT RUN: rt_send not confirmed)"
  elif [ -n "${RNSNOPATH:-}" ]; then RNSB=" (box -> peer NOT RUN: the node had no path to the PC, nothing sent)"
  else result "$1-peer.txt" "$RNSN2" || fail "not heard: $RNSN2"; RNSB=; fi; }
row_rns() {   # row_rns <id> <box:KEY> <backend>: Reticulum + MeshChat on one box radio (868.5 SF8, the row's backend) <-> the RNode
  local id=$1 b=${2%%:*} k=${2#*:} be=$3
  needs $(rforms "$id") || { notrun_row "$id" "form not confirmed: $NEEDS_MISSING"; return 0; }
  local m r q; m=$(undo_mark) || stop "undo journal: unreadable (no mark)"; r=$(getk "$k"); q="R$id-$b-$r"
  rnsread "$id" "$b" "$r" "$be" 0
  spower "$b" "$([ "$be" = daemon ] && echo rt_dpget || echo rt_pget)" "$r" "$q"; undo_push "$b" "$(gstop "reticulum@$r")"
  hpower "R$id-rnode" "$id-rnode-power.txt"
  leg "peer->box"   # the leg that runs now: a FAIL from here is its FAIL (runrow)
  rnsx "$id" "$id" "$b" "$r" "R$id-rnode" "$q"
  REPORT "$id" "$(rowname "$id")" "$b ($r)" "868.5 MHz, SF8" "the RNode (the PC's shared rnsd)" "peer -> box" "yes: LXMF delivered (the box's proof) and stored" "the RNode at $(getk "P_R$id-rnode" unrecorded) dBm" PASS
  leg "box->peer"   # the leg that runs now: a FAIL from here is its FAIL (runrow)
  if needs rt_send && [ -z "${RNSNOPATH:-}" ]; then rnsback "$id"
    REPORT "$id" "$(rowname "$id")" "$b ($r)" "868.5 MHz, SF8" "the RNode (the PC's shared rnsd)" "box -> peer" yes "$(getk "P_$q" as-is) dBm (as-is)" PASS
  elif [ -n "${RNSNOPATH:-}" ]; then notrun "$id" box-\>peer "the node had no path to the PC, nothing sent"
  else notrun "$id" box-\>peer "form not confirmed: rt_send"; fi
  undo_to "$m"; }
row_5() { row_rns 5 "$1" spi; }       # the Zero's board radio, direct SPI (backend = spi saved there: hardware-proof.md)
row_12() { row_rns 12 "$1" daemon; }  # box H's Meshstick through the daemon (the product default)

rnspid() { echo "p=\$(pgrep -f '[l]oraham-rns-node --config' | head -n 1); [ -n \"\$p\" ] || { echo NO-PID; exit 3; }; echo \$p \$(awk '{print \$22}' /proc/\$p/stat)"; }   # the node's pid + start time
# the node's interfaces (rnstatus -j of its config): IF <up|down> <name>, the name holding "LoRa <rid>"
RNSIF="cd {RT} && src/reticulum/.venv/bin/rnstatus --config state/reticulum -j | python3 -c 'import json, sys; [print(\"IF\", \"up\" if i.get(\"status\") is True else \"down\", i.get(\"name\", \"\")) for i in json.load(sys.stdin).get(\"interfaces\", [])]'"
MCREADY='for i in $(seq 1 15); do (exec 3<>/dev/tcp/127.0.0.1/8790) 2>/dev/null && { echo READY 8790; exit 0; }; sleep 1; done; echo NOT-READY 8790; exit 3'   # MeshChat's port
rnslog() { echo "for d in RX TX; do echo \"\$d \$(grep -cF -- \" \$d \" {RT}/logs/$1/rf-reticulum.log 2>/dev/null)\"; done"; }   # rnslog <rid>: its RF log's RX and TX lines
ifis() { local n u; n=$(body "$1" | grep -cE "^IF .*LoRa $2(]|\$)"); u=$(body "$1" | grep -cE "^IF up .*LoRa $2(]|\$)")   # ifis <file> <rid> <up|gone>
  case $3 in up) [ "$n" = 1 ] && [ "$u" = 1 ];; *) [ "$n" = 0 ];; esac; }
rfgrew() { local d a c m; for d in RX TX; do a=$(body "$1" | sed -n "s/^$d //p"); c=$(body "$2" | sed -n "s/^$d //p")   # rfgrew <before> <after> [min TX]
  m=1; [ "$d" = RX ] || m=${3:-1}; [[ $a =~ ^[0-9]+$ && $c =~ ^[0-9]+$ ]] && [ $((c - a)) -ge "$m" ] || return 1; done; }
memgrew() { rfgrew "$1" "$2" $((ANNF + 1)); }   # memgrew <before> <after>: row 13's member leg: an RX, and a TX beyond the announce
rnsnode() {   # rnsnode <tag> <box> <member rid> <up|gone> [meshchat]: the node kept its pid and start time (13-pid0.txt), the
  # member's interface is up in it (or gone from it: the radio is free), and (meshchat) MeshChat still answers
  must "$2" "$1-pid.txt" "$(rnspid)"; must "$2" "$1-if.txt" "$RNSIF"; [ -z "${5:-}" ] || X "$2" "$1-meshchat.txt" "$MCREADY"; [ "$MODE" = dry ] && return 0
  [ -z "${5:-}" ] || [ "$XRC" = 0 ] || fail "row 13 $1: MeshChat does not answer on 127.0.0.1:8790 ($1-meshchat.txt)"
  [ "$(body "$1-pid.txt")" = "$(body 13-pid0.txt)" ] || fail "row 13 $1: the node's pid or start time changed ($1-pid.txt): not restart-free"
  ifis "$1-if.txt" "$3" "$4" || fail "row 13 $1: LoRa $3 is not $4 in the node ($1-if.txt)"; }
row_13() {   # Reticulum: two radios, one node: the owner (A) opens as row 12 does, the member (flags member=) joins it restart-free
  local b=${1%%:*} k=${1#*:} mk zk zb zr qz o nh nz bh zh; mk=$(rmember 13); mk=${mk#*:}; zk=$(rzero 13); zb=${zk%%:*}; zk=${zk#*:}
  needs $(rforms 13) || { notrun_row 13 "form not confirmed: $NEEDS_MISSING"; return 0; }
  local m mp ro rm q qm p0 pk=R13-rnode rn="the RNode (the PC's shared rnsd)" bd="868.5 MHz, SF8"
  m=$(undo_mark) || stop "undo journal: unreadable (no mark)"; ro=$(getk "$k"); rm=$(getk "$mk"); q="R13-$b-$ro"; qm="R13-$b-$rm"
  zr=$(getk "$zk"); qz="R13-$zb-$zr"
  rnsread 13 "$b" "$ro" daemon 0; rnsread 13 "$b" "$rm" daemon -m; rnsread 13 "$zb" "$zr" spi -z
  spower "$b" rt_dpget "$ro" "$q"; spower "$b" rt_dpget "$rm" "$qm"; spower "$zb" rt_pget "$zr" "$qz"; undo_push "$b" "$(gstop "reticulum@$ro")"
  hpower "$pk" 13-rnode-power.txt
  leg owner   # the leg that runs now: a FAIL from here is its FAIL (runrow)
  RNSSEND=0 rnsx 13 13-owner "$b" "$ro" "$pk" "$q"; rnsback 13-owner "row 12 and the member leg"
  REPORT 13 "$(rowname 13)" "$b ($ro)" "$bd" "$rn" "$(dirtext owner)" "yes: LXMF delivered (the box's proof) and stored$RNSB" "the RNode at $(getk "P_$pk" unrecorded) dBm; $ro $(getk "P_$q" as-is) dBm (as-is)" PASS
  leg join; must "$b" 13-pid0.txt "$(rnspid)"
  # the member on its own channel (one node's radios on one channel collide): its OWN FREQ journalled (MHz, "" = none: the
  # undo's blank clears), then 868.300 saved (Save-only: it takes effect at the join); its undo runs after the member's stop
  must "$b" 13-member-freq0.txt "$(form rt_own rid="$rm" k=dp_FREQ)"; o=$(sed -n 's/^OWN //p' "$OUT/13-member-freq0.txt")
  [ "$MODE" = dry ] || [[ $o =~ ^([0-9]{3}(\.[0-9]+)?)?$ ]] || stop "row 13: $rm's own FREQ reads '$o': nothing changed"
  undo_push "$b" "$(form rt_dfreq rid="$rm" f="$o")"; must "$b" 13-member-freq.txt "$(form rt_dfreq rid="$rm" f=868.300)"
  keep "STARTED_${b}_$rm" 1; undo_push "$b" "$(gstop "reticulum@$rm")"; must "$b" 13-join.txt "$(form rt_join rid="$rm")"
  rnsnode 13-join "$b" "$rm" up meshchat
  REPORT 13 "$(rowname 13)" "$b ($ro, $rm)" "$bd" — "$(dirtext join)" "the node's pid unchanged, LoRa $rm up, MeshChat answers" — PASS
  # the member's exchange: H's member <-> the Zero's SPI radio, both on 868.300 (the RNode, on 868.5, hears neither): the Zero's
  # channel journalled (its own file_frequency, "" = none), set, its node + MeshChat started (MeshChat's start announces), H's
  # announce call, then one bounded send each way; the undo stops the Zero's node first, then its MeshChat, then its channel
  leg member; mp=$(undo_mark) || stop "undo journal: unreadable (no mark)"
  must "$zb" 13-zero-freq0.txt "$(form rt_own rid="$zr" k=file_frequency)"; o=$(sed -n 's/^OWN //p' "$OUT/13-zero-freq0.txt")
  [ "$MODE" = dry ] || [[ $o =~ ^([0-9]{9})?$ ]] || stop "row 13: the Zero's own frequency reads '$o': nothing changed"
  undo_push "$zb" "$(form rt_zfreq rid="$zr" hz="$o")"; must "$zb" 13-zero-freq.txt "$(form rt_zfreq rid="$zr" hz=868300000)"
  XRAW=$SCRATCH/13-zero-lxmf.raw must "$zb" 13-zero-lxmf.txt "cd {RT} && src/meshchat/.venv/bin/python -c '$MCHASH_PY' state/meshchat/identity"
  XRAW=$SCRATCH/13-member-lxmf.raw must "$b" 13-member-lxmf.txt "cd {RT} && src/meshchat/.venv/bin/python -c '$MCHASH_PY' state/meshchat/identity"
  [ "$MODE" = live ] || { cp "$OUT/13-zero-lxmf.txt" "$SCRATCH/13-zero-lxmf.raw" && cp "$OUT/13-member-lxmf.txt" "$SCRATCH/13-member-lxmf.raw"; } || stop "row 13: the dry raw copies"
  zh=$(sed -n 's/^LXMF //p' "$SCRATCH/13-zero-lxmf.raw" | tail -n 1); bh=$(sed -n 's/^LXMF //p' "$SCRATCH/13-member-lxmf.raw" | tail -n 1)
  [ "$MODE" = dry ] || { [[ "$zh" =~ ^[0-9a-f]{32}$ ]] && [[ "$bh" =~ ^[0-9a-f]{32}$ ]]; } || stop "row 13: no LXMF address of a MeshChat (13-zero/member-lxmf.txt)"
  keep "STARTED_${zb}_$zr" 1; undo_push "$zb" "$(gstop "meshchat@$zr")"; undo_push "$zb" "$(gstop "reticulum@$zr")"   # newest first: the node
  tx "$qz" "$ANNF" control -- must "$zb" 13-zero-start.txt "$(form start inst="meshchat@$zr")"
  must "$b" 13-member-rf0.txt "$(rnslog "$rm")"
  for x in "$qm"; do tx "$x" "$ANNF" control --reserve; done; tx "$q" "$ANNF" control -- must "$b" 13-member-announce.txt "$(form rt_announce)"
  [ "$MODE" = dry ] || sleep 30
  # each direction's own outcome: delivered (its receive check passed) or NOT RUN (NO-PATH: nothing sent); the leg is a PASS
  # only with both delivered, else NOT RUN naming each direction
  local ok=() nr=()
  nh=$(nonce); nz=$(nonce)
  tx "$qz" 1 control --reserve   # the Zero's delivery proof of H's message
  if rtsend 13-member-send "$b" "$ro" "${zh:-dry}" "$nh" "$q" "$qm"; then boxrx "$zb" 13-zero-rx.txt rx_mchat "$zr" "$nh" 60 || fail "not heard on the Zero: $nh"
    ok+=("H -> Zero delivered and stored"); else nr+=("H -> Zero: H's node had no path to the Zero, nothing sent"); fi
  for x in "$q" "$qm"; do tx "$x" 1 control --reserve; done   # H's delivery proof of the Zero's message (the node picks the radio)
  if rtsend 13-zero-send "$zb" "$zr" "${bh:-dry}" "$nz" "$qz"; then boxrx "$b" 13-member-rx.txt rx_mchat "$ro" "$nz" 60 || fail "not heard on H: $nz"
    ok+=("Zero -> H delivered and stored"); else nr+=("Zero -> H: the Zero's node had no path to H, nothing sent"); fi
  must "$b" 13-member-rf1.txt "$(rnslog "$rm")"
  # TX >= 3: the announce call's 2 frames go out on every interface; the exchange's proof or send over the member is the third
  # (the node relays nothing: enable_transport = No), so an exchange that went over the owner fails here (no exchange: no check)
  [ "$MODE" = dry ] || [ ${#ok[@]} = 0 ] || memgrew 13-member-rf0.txt 13-member-rf1.txt || fail "row 13 member: no new RX line, or fewer than 3 new TX lines (the announce's 2 + the exchange), on $rm's own RF log (13-member-rf0/1.txt)"
  undo_to "$mp" || stop "row 13: the Zero's stop or channel restore failed (UNRESOLVED.txt)"
  if [ ${#nr[@]} = 0 ]; then
    REPORT 13 "$(rowname 13)" "$b ($rm)" "868.3 MHz, SF8" "the Zero ($zb:$zr, SPI)" "$(dirtext member)" "yes: LXMF H -> Zero and Zero -> H on 868.3 MHz, stored on each side; RX and >= 3 TX (the announce's 2 + the exchange) on $rm's RF log" "$rm $(getk "P_$qm" as-is) dBm, the Zero's $zr $(getk "P_$qz" as-is) dBm (as-is)" PASS
  else notrun 13 member "$(IFS=';'; echo "${nr[*]}${ok[*]:+;${ok[*]}}" | sed 's/;/; /g')"; fi
  leg leave; must "$b" 13-leave.txt "$(form stop inst="reticulum@$rm")"; rnsnode 13-leave "$b" "$rm" gone
  RNSSEND=0 RNSANN=known rnsx 13 13-leave "$b" "$ro" "$pk" "$q"; rnsback 13-leave "the member leg"
  REPORT 13 "$(rowname 13)" "$b ($ro)" "$bd" "$rn" "$(dirtext leave)" "yes: the node's pid unchanged, LoRa $rm gone, LXMF delivered over $ro$RNSB" "$ro $(getk "P_$q" as-is) dBm (as-is)" PASS
  leg rejoin; must "$b" 13-rejoin.txt "$(form rt_join rid="$rm")"; rnsnode 13-rejoin "$b" "$rm" up meshchat
  REPORT 13 "$(rowname 13)" "$b ($ro, $rm)" "$bd" — "$(dirtext rejoin)" "the node's pid unchanged, LoRa $rm up, MeshChat answers" — PASS
  leg off; must "$b" 13-off-mstop.txt "$(gstop "meshchat@$ro")"; must "$b" 13-off.txt "$(form stop inst="reticulum@$ro")"
  X "$b" 13-off-pid.txt "$(rnspid)"; [ "$MODE" = dry ] || { [ "$XRC" = 3 ] && [ "$(body 13-off-pid.txt)" = NO-PID ]; } || fail "row 13 off: the node still runs after the owner's stop (13-off-pid.txt)"
  must "$b" 13-off-runset.txt "$RUNSET"; [ "$MODE" = dry ] || ! body 13-off-runset.txt | grep -q '^reticulum@' || fail "row 13 off: a Reticulum instance still runs after the owner's stop (13-off-runset.txt)"
  REPORT 13 "$(rowname 13)" "$b ($ro, $rm)" "$bd" — "$(dirtext off)" "no node process, no Reticulum instance running" — PASS
  undo_to "$m"; }

# the chat client's command from the start's own output (the line after "run it yourself in a terminal:"), in the measured form
# chatcmd <file> <rid>: the chat client's call, BUILT from four paths parsed out of the box's start output as data, never the
# box's line itself: each a plain absolute path (/[A-Za-z0-9._/-]), the client .../loraham_chat, the sockets .../lora-<rid>.sock
# and .../loraconf-<rid>.sock. Prints the built call (rc 0), or the box's line (rc 1) when it is not that form.
chatcmd() { local l p='(/[A-Za-z0-9._/-]+)' re; if [ "$MODE" = dry ]; then l="cd /dry/files/$2/ && /dry/LoRaHAM_Daemon/loraham_chat --data-socket /tmp/lora-$2.sock --conf-socket /tmp/loraconf-$2.sock; stty sane 2>/dev/null"
  else l=$(body "$1" | grep -A1 -F 'run it yourself in a terminal:' | sed -n '2s/^ *//p'); fi
  re="^cd $p && $p/loraham_chat --data-socket $p/lora-$2[.]sock --conf-socket $p/loraconf-$2[.]sock; stty sane 2>/dev/null\$"
  [[ "$l" =~ $re ]] || { echo "$l"; return 1; }
  printf 'cd %s && %s/loraham_chat --data-socket %s/lora-%s.sock --conf-socket %s/loraconf-%s.sock; stty sane 2>/dev/null' \
    "${BASH_REMATCH[1]}" "${BASH_REMATCH[2]}" "${BASH_REMATCH[3]}" "$2" "${BASH_REMATCH[4]}" "$2"; }
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
  local w1 w2   # a direction passes only when its word is in the receiving radio's log AND on the other box's chat screen
  tx "R6-$ba-$ra" 1 "$na" -- must "$ba" "6-say-$ba.txt" "tmux send-keys -t rfchat-$ra -- '$na' Enter"
  chatdir "$ba" "$ra" "$bb" "$rb" "$na" "box -> peer"; w1=$WHY
  tx "R6-$bb-$rb" 1 "$nb" -- must "$bb" "6-say-$bb.txt" "tmux send-keys -t rfchat-$rb -- '$nb' Enter"
  chatdir "$bb" "$rb" "$ba" "$ra" "$nb" "peer -> box"; w2=$WHY
  [ -z "$w1$w2" ] || fail "${w1:-$w2}"
  undo_to "$m"; }
chatdir() {   # chatdir <from box> <rid> <to box> <rid> <nonce> <direction>: one row-6 direction's checks and its REPORT; WHY = a FAIL's reason
  WHY=; boxrx "$3" "6-rx-$3.txt" rx_rf "$4" "$5" 60 || WHY="not heard: $5"
  [ -n "$WHY" ] || chatrx "$3" "6-screen-$3.txt" "$4" "$5" 30 || WHY="not on the chat screen of $3 ($4): $5"
  if [ -z "$WHY" ]; then REPORT 6 chat "$1 ($2) -> $3 ($4)" "433.775 MHz" "the other box" "$6" "yes (RX on $3:$4 and on its chat screen)" "$(getk "P_R6-$1-$2" as-is) dBm (as-is)" PASS
  else REPORT 6 chat "$1 ($2) -> $3 ($4)" "433.775 MHz" "the other box" "$6" no "$(getk "P_R6-$1-$2" as-is) dBm (as-is)" "FAIL: $WHY"; fi; }

runrow() {   # one row in a subshell; a FAIL (the marker) = recorded, its undos run, the run goes on; any other rc = STOP
  local r=$1 m rc sk d; sk=$(getk "SKIP_$r"); [ -z "$sk" ] || { notrun_row "$r" "$sk"; return 0; }
  m=$(undo_mark) || stop "undo journal: unreadable (no mark)"; usbguard "pre-$r"; : > "$OUT/rowfail.txt"; : > "$OUT/rowleg.txt"
  if [ "$(rowf "$r" kind)" = frame ]; then ( CURROW=$r; frame_row "$r" "$(rowf "$r" a)" "$(rowf "$r" b)" "$(rowf "$r" power)" "$(rowf "$r" flags)" ); rc=$?
  else ( CURROW=$r; "row_$r" "$(rowf "$r" a)" "$(rowf "$r" b)" ); rc=$?; fi
  bgend || stop "row $r: a background peer job does not end (its undos would find the peer lock held)"
  if [ "$rc" = 1 ] && [ -s "$OUT/rowfail.txt" ]; then
    # a stack row: the leg it named last (leg) FAILed, else (a FAIL before any leg) its first direction without a result;
    # every other direction without a result did not run (NOT RUN, naming that FAIL). A frame row's FAIL (an incompatible
    # radio) is every direction's.
    local why lg fd= ds= d2; why=$(head -n 1 "$OUT/rowfail.txt"); lg=$(head -n 1 "$OUT/rowleg.txt" 2>/dev/null)
    for d in $(dirs "$r"); do d2=$(dirtext "$d")
      awk -F'|' -v i="$r" -v d="$d2" '$1 == i && $2 == d {f = 1} END {exit !f}' "$OUT/results.txt" 2>/dev/null && continue
      ds="$ds $d"; [ "$d2" != "$lg" ] || fd=$d2; done
    ds=${ds# }; [ -n "$fd" ] || fd=$(dirtext "${ds%% *}")
    for d in $ds; do d2=$(dirtext "$d")
      if [ "$d2" = "$fd" ] || [ "$(rowf "$r" kind)" = frame ]; then REPORT "$r" "$(rowname "$r")" — — — "$d2" no — "FAIL: $why"
      else notrun "$r" "$d" "after $fd FAIL: $why"; fi; done
    [ "$MODE" = dry ] || echo "row $r: $(head -n 1 "$OUT/rowfail.txt")" >> "$EVD/FAILED.txt" || stop "FAILED.txt: append failed"
    for x in needed formed; do [ "$MODE" = live ] || [ ! -f "$SCRATCH/$x" ] || { awk -v r="$r" '$1 != r' "$SCRATCH/$x" > "$SCRATCH/$x.n" \
      && mv -f "$SCRATCH/$x.n" "$SCRATCH/$x"; } || stop "$x: not rewritten"; done   # a FAILed row renders no later forms
    say "FAIL: row $r (its undos run now; the next row follows)"; undo_to "$m" || stop "row $r: its undos did not all run (UNRESOLVED.txt)"
  elif [ "$rc" != 0 ]; then stop "row $r ended rc $rc (a STOP or a shell error, see above): the run ends"; fi
  usbguard "post-$r"; }

# txcount <box> [<R0 count file>]: the TXC_PY command for the box's radios (R0's hardware list): T0, then <rid>:<band>:<head
# bytes> each; the head is 4096 bytes at R0 and min(4096, the R0 size) at RS, so a run log that only grew keeps its head
txcount() { local a= rid band h; while read -r _ rid _ band _; do h=4096
    [ -n "${2:-}" ] && { h=$(awk -v r="$rid" '$1 == "L" && $2 == r {print ($3 < 4096 ? $3 : 4096)}' "$OUT/$2" | tail -n 1); [[ $h =~ ^[0-9]+$ ]] || h=0; }
    a="$a $rid:$band:$h"; done < <(grep -E '^ROW ' "$OUT/R0-$1-radios.txt" 2>/dev/null); printf '%s %s%s' "$RUNTX" "$(getk "T0_$1" 0)" "$a"; }
# rnsrids <box>: <rid>=<backend> of each radio a Reticulum row ran the node on (RNS_<box>_<rid>, read live by the row)
rnsrids() { sed -n "s/^RNS_$1_\(r[1-9][0-9]*\)=\(daemon\|spi\)$/\1=\2/p" "$OUT/state.env" | sort -u | tr '\n' ' '; }
# txjudge <R0 facts> <RS facts> <T0> <reticulum backends> <kit radios> <running radios>: "<rid> <frames>" per radio, else "<rid>
# UNREADABLE". frames = max(the daemon's TX + its transmit ERRORs of the run, the mirrors' TX) + the direct TX. A radio's own
# Reticulum log is a mirror where the kit read its backend as daemon (the frames went through the daemon), else direct (spi,
# or a backend not read: the safe side). UNREADABLE (fail closed: no evidence is never 0): a count not a number; an RF log present at R0 (even
# empty) gone at RS; a file with records at R0 that keeps none from before T0 (lost lines: two rolls, a Clear); a run log
# smaller, with another head, ERRORs gone, or its cut file changed (any cut loses lines: runtime_fs.cap_start_log); a radio of
# R0 without its facts at RS; a radio that could send (the kit sent on it: TXR > 0, an instance ran on it at R0, or the kit
# started one there) with no RF
# log at all (the daemon creates its log at start: daemon_rflog.cpp:67, so a running radio without one logs nothing); a radio
# the kit sent on that counts 0.
txjudge() {
  awk -v t0="$3" -v rr="$4" -v kr="${5:-}" -v ar="${6:-}" -v ml="${7:-}" '
    function n(v) {return v ~ /^[0-9]+$/}
    FNR == NR {if ($1 == "F") {f0[$2 " " $4] = 1; cov[$2] = 1; if (n($5) && $5 > 0) had[$2 " " $4] = 1}
      if ($1 == "L") l0[$2] = $3 " " $4 " " $5 " " $6; next}
    $1 == "F" {k = $2 " " $4; seen[k] = 1; cov[$2] = 1; id[$2] = 1; if ($4 == "rf-meshtastic.log") mf[$2] = 1
      if (!n($5) || !n($7) || (k in had && ($6 == "-" || $6 > t0))) u[$2] = 1; else c[$2, $3] += $7; next}
    $1 == "L" {id[$2] = 1; ls[$2] = 1; split(l0[$2], a, " ")
      if (($2 in l0) && n($3) && n($5) && n(a[1]) && n(a[3]) && $3 >= a[1] && $4 "" == a[2] "" && $5 >= a[3] && $6 "" == a[4] "") e[$2] = $5 - a[3]; else u[$2] = 1}
    END {for (k in f0) if (!(k in seen)) {split(k, p, " "); u[p[1]] = 1}
      for (r in l0) if (!(r in ls)) {id[r] = 1; u[r] = 1}
      split(rr, R, " "); for (i in R) {split(R[i], q, "="); if (q[2] == "daemon") vd[q[1]] = 1}; split(kr, K, " "); for (i in K) {kit[K[i]] = 1; id[K[i]] = 1}
      split(ar, A, " "); for (i in A) {act[A[i]] = 1; id[A[i]] = 1}
      split(ml, M, " "); for (i in M) {split(M[i], q, "="); if (n(q[2])) mled[q[1]] = q[2]}
      for (r in id) {if ((r in kit || r in act) && !(r in cov)) u[r] = 1
        if (r in u) {print r, "UNREADABLE"; continue}
        d = c[r, "daemon"] + e[r]; g = c[r, "mirror"] + (r in vd ? c[r, "rns"] : 0); t = (d > g ? d : g) + c[r, "direct"] + (r in vd ? 0 : c[r, "rns"]) + (r in mf ? mled[r] : 0)
        print r, (r in kit && t == 0 ? "UNREADABLE" : t)}}' \
    "$1" "$2" | sort; }
# txlines <file> <radios>: exactly one "<rid> <frames|UNREADABLE>" per radio named, no other radio
txlines() { awk -v want="$2" 'BEGIN {split(want, W, " "); for (i in W) w[W[i]] = 1}
    $0 !~ /^r[1-9][0-9]? ([0-9]+|UNREADABLE)$/ || !($1 in w) || ($1 in s) {bad = 1} {s[$1] = 1}
    END {for (k in w) if (!(k in s)) bad = 1; exit bad}' "$1"; }
# kitrids / runrids <box>: the box radios the kit reserved frames on (TXR > 0), and the radios with an instance at R0 or one
# the kit started (STARTED_<box>_<rid>: a stack can send at its start, e.g. meshtastic's nodeinfo)
mtled() { sed -n "s/^MTX_$1_\(r[1-9][0-9]*\)=\([0-9]*\)$/\1=\2/p" "$OUT/state.env" | awk -F= '{v[$1] = $2} END {for (k in v) print k "=" v[k]}' | sort | tr '\n' ' '; }   # the kit's own Meshtastic sends per radio
kitrids() { sed -n "s/^TXR_$1_\(r[1-9][0-9]*\)=[0-9]*[1-9][0-9]*$/\1/p" "$OUT/state.env" | sort -u | tr '\n' ' '; }
runrids() { { body "R0-$1-runset.txt" | sed -n 's/^[a-z0-9-]*@\(r[1-9][0-9]*\)$/\1/p'; sed -n "s/^STARTED_$1_\(r[1-9][0-9]*\)=.*/\1/p" "$OUT/state.env"; } | sort -u | tr '\n' ' '; }
row_RS() {
  undo_to 0 || stop "RS: an undo failed (UNRESOLVED.txt): the recovery runs it again"; [ "$MODE" = dry ] || { [ ! -s "$EVD/UNRESOLVED.txt" ] || fail "RS: unresolved undo(s): $(tr '\n' '|' < "$EVD/UNRESOLVED.txt")"; }
  verify_set || fail "RS: the recorded running set is not back ($(tr '\n' '|' < "$EVD/UNRESOLVED.txt" 2>/dev/null))"
  [ "$MODE" = dry ] || { [ ! -s "$EVD/UNRESOLVED.txt" ] || fail "RS: unresolved after verify: $(tr '\n' '|' < "$EVD/UNRESOLVED.txt")"; }
  local b pr; for b in $BOXES; do must $b "RS-$b-tx.txt" "$(txcount $b "R0-$b-tx.txt")"
    must $b "RS-$b-boot.txt" "cat /proc/sys/kernel/random/boot_id"; [ "$MODE" = dry ] && continue; eq "RS $b: no reboot" "$(body "RS-$b-boot.txt")" "$(getk BOOT_$b)"
    txjudge "$OUT/R0-$b-tx.txt" "$OUT/RS-$b-tx.txt" "$(getk "T0_$b" 0)" "$(rnsrids $b)" "$(kitrids $b)" "$(runrids $b)" "$(mtled $b)" > "$OUT/RS-$b-tx-run.txt" \
      || fail "RS $b: the TX count judge failed (rc $?): no count, no pass"
    txlines "$OUT/RS-$b-tx-run.txt" "$(grep -E '^ROW ' "$OUT/R0-$b-radios.txt" | awk '{print $2}' | tr '\n' ' ')" \
      || fail "RS $b: the TX count has not exactly one valid line per radio of R0's hardware list ($(tr '\n' '|' < "$OUT/RS-$b-tx-run.txt"))"
    data "RS $b: TX frames of this run per radio: $(tr '\n' '|' < "$OUT/RS-$b-tx-run.txt")"
    awk -v m="$TXMAX" '$2 !~ /^-?[0-9]+$/ || $2 < 0 || $2 > m {exit 1}' "$OUT/RS-$b-tx-run.txt" \
      || fail "RS $b: a radio sent more than $TXMAX frames in this run (the physical TX count), or its count is missing or unreadable (RS-$b-tx-run.txt)"; done
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
  local b p; for b in H Z; do p="PW_$b"; [ -n "${!p:-}" ] || continue
    if pweq "$b"; then ok "RE: box $b password equals its login name (lab box): not scanned"; else scan "box password" -F -- "${!p}"; fi; done
  [ -z "${MC_PIN:-}" ] || scan "PIN" -F -- "$MC_PIN"
  scan "the operator's call (any SSID)" -iE -- "\\b$OPERATOR_CALL(-[0-9]{1,2})?\\b"; [ -z "${TRACKER_CALL:-}" ] || scan "tracker call" -iF -- "$TRACKER_CALL"
  local j1=${MCSET_J1:-}; j1=${j1#global}; [ -z "$j1" ] || scan "the J1 callsign" -iF -- "$j1"
  if [ -s "${SCRATCH:-/nonexistent}/lxmf.list" ]; then local a; while IFS= read -r a; do [ -z "$a" ] || scan "the box's LXMF address" -F -- "$a"; done < "$SCRATCH/lxmf.list"; fi
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
  n=0; while IFS= read -r t; do ! ptoken "$t" || n=$((n + 1)); done < <(grep '^CMD\[\] ' "$DRYLOG")   # outside the setter/reader
  [ "$n" = 0 ] || sc no-high-power "$n printed command(s) outside the setter/reader carry a power token"
  n=0; while IFS= read -r t; do ! pover "$t" || n=$((n + 1)); done < <(grep '^CMD\[set\] ' "$DRYLOG")   # the setter's own
  [ "$n" = 0 ] || sc no-high-power "$n setter command(s) carry a high-power flag or a power above $PCAP dBm"; scok no-high-power
  c=$(grep '^CMD' "$DRYLOG" | grep -ciE -- "\\b$OPERATOR_CALL(-[0-9]{1,2})?\\b") || true; [ "$c" = 0 ] || sc no-call-in-commands "the operator's call is in $c logged command(s)"
  [ -z "$TRACKER_CALL" ] || { c=$(grep -ciF -- "$TRACKER_CALL" "$DRYLOG") || true; [ "$c" = 0 ] || sc no-call-in-commands "the tracker call is in the command log ($c)"; }; scok no-call-in-commands
  n=$(grep '^CMD' "$DRYLOG" | grep -c 'peer.py') || true; w=$(grep '^CMD' "$DRYLOG" | grep -c 'flock -n -E 75') || true; p=$(grep '^CMD' "$DRYLOG" | grep -c '(PIN on stdin)') || true
  [ $((w + p)) = "$n" ] || sc peer-calls-locked "$((n - w - p)) peer call(s) outside the lock"; scok peer-calls-locked
  # every FRAME is covered by an earlier TX of its radio (a '*' rid: any credit on that box), and every TX with an executor is
  # directly followed by that executor's command
  t=$(awk '$1 == "TX" {cr[$2] += $3; if ($5 != "reserve") want = NR + 1; next}
    $1 ~ /^CMD/ {want = 0; next}
    $1 == "FRAME" {r = $2; if (r ~ /:\*$/) {b = substr(r, 1, 2); r = ""; for (k in cr) if (index(k, b) == 1 && cr[k] > 0) {r = k; break}}
      if (r == "" || cr[r] < 1) {print "FRAME " $2 " (line " NR ") without a TX credit"; exit}; cr[r]--}
    want && NR == want && $1 !~ /^CMD/ {print "TX on line " NR - 1 " is not followed by its command"; bad = 1; exit}
    END {if (want && !bad) print "TX on line " want - 1 " is not followed by its command"}' "$DRYLOG")
  [ -z "$t" ] || sc every-send-gated "$t"; scok every-send-gated
  t=$(cut -d' ' -f2 "$SCRATCH/needed" 2>/dev/null | sort -u | grep -vxF -f <(cut -d' ' -f2 "$SCRATCH/formed" 2>/dev/null | sort -u) | grep -vx rt_send | tr '\n' ' ') || true
  [ -z "$t" ] || sc all-forms-printed "needed but never printed: $t"; scok all-forms-printed
  # a form a row prints is one it needs (RFORMS): else a live run would send it unconfirmed (FORMS_OK)
  t=$(sort -u "$SCRATCH/formed" 2>/dev/null | grep -v '^0 ' | grep -vxF -f <(sort -u "$SCRATCH/needed" 2>/dev/null) | tr '\n' ',') || true
  [ -z "$t" ] || sc every-form-needed "printed but not needed by its row: ${t%,}"; scok every-form-needed
  ( MODE=live; body() { printf '%s\n' "  [manual] loraham-chat is interactive — the daemon is ensured, then run it yourself in a terminal:" "    $CC"; }
    CC="cd /home/u/loraham-pi-control/config/files/r1/ && /home/u/loraham-pi-control/src/LoRaHAM_Daemon/loraham_chat --data-socket /tmp/lora-r1.sock --conf-socket /tmp/loraconf-r1.sock; stty sane 2>/dev/null"
    chatcmd x r1 > /dev/null || exit 1; ! chatcmd x r2 > /dev/null || exit 1
    CC="cd /x/ && /x/loraham_chat --data-socket /tmp/lora-r1.sock --conf-socket /tmp/loraconf-r1.sock; rm -rf ~; stty sane 2>/dev/null"; ! chatcmd x r1 > /dev/null || exit 1
    CC="cd /x/ && /x/lorachat --data-socket /tmp/lora-r1.sock --conf-socket /tmp/loraconf-r1.sock; stty sane 2>/dev/null"; ! chatcmd x r1 > /dev/null ) || sc chat-judge "the chat command judge"
  printf '%s\n' "$MCSEND_PY" | python3 -I -c 'import ast, sys; ast.parse(sys.stdin.read())' || sc chat-judge "the MeshCom send helper does not parse"; scok chat-judge
  ( d=$(mktemp -d "$SCRATCH/pw.XXXXXX") || exit 1; T=$(printf "$PROVWAIT" r9 | sed 's|{RT}/logs|"$PWLOGS"|; s/seq 1 120/seq 1 2/; s/sleep 5/sleep 0/')
    pw() { local want=$1; shift; printf '%s\n' "$@" > "$d/start-meshcom-qemu-r9.log"; PWLOGS=$d bash -c "$T" > /dev/null; [ $? = "$want" ]; }   # the path as data
    pw 0 "[run] node image: x" "[run] provisioning the node" "[run] provisioning verified" "[run] node image: y" "[run] web UI: z" \
    && pw 3 "[run] node image: x" "[run] provisioning the node" "[run] provisioning check: lacks" \
    && pw 3 "[run] node image: x" "[run] provisioning the node" "[run] provisioning FAILED: x" \
    && pw 0 "[run] node image: x" "[run] provisioning the node" "[run] provisioning verified" \
    && pw 3 "[run] QEMU: x" && rm "$d/start-meshcom-qemu-r9.log" && { PWLOGS=$d bash -c "$T" > /dev/null; [ $? = 3 ]; } ) || sc provisioning-wait "the provisioning wait judge"
  printf "TO = 'N0CALL-9'\n%s\n" "$GW_PY" | python3 -I -c 'import ast, sys; ast.parse(sys.stdin.read())' || sc provisioning-wait "the Graywolf helper does not parse"; scok provisioning-wait
  ( MODE=live; pv() { local want=$1; shift; body() { printf '%s\n' "${PV_LINES[@]}"; }; PV_LINES=("$@"); [ "$(pnum x mc_pget)" = "$want" ]; }
    I1='INFO:meshcore:TCP Connection started'; I2='INFO:meshcore:Connected to Node running on a 14.0 (14) fw.'
    pv 14 "$I1" "$I2" 14 && pv "" "$I1" "$I2" 14 15 && pv "" 'WARNING:meshcore:x' 14 && pv "" "$I1" 'tx: 14' && pv "" 'INFO:asyncio:x' 14 ) || sc meshcore-power-read "the MeshCore power read"
  scok meshcore-power-read
  ( L='2026-11-15T02:26:23.127Z RX rssi=-56.00 snr=11.25 len=37 channel Public: SENDER: rfkit 250b74dd'; g=$(form rx_mc rid=r2 n='rfkit 250b74dd' | sed 's/^.*2>\/dev\/null | //')
    [ "$(printf '%s\n' "$L" | bash -c "$g" | grep -c .)" = 1 ] && [ "$(printf '%s\n' '2026-11-15T02:26:23.126Z RX rssi=-56.00 snr=11.25 len=37 hex=0a1b2c' | bash -c "$g" | grep -c .)" = 0 ] \
    && [ "$(printf '%s\n' '2026-11-15T02:26:23.127Z TX rssi=- len=37 channel Public: SENDER: rfkit 250b74dd' | bash -c "$g" | grep -c .)" = 0 ] ) || sc rx-mc-judge "the rx_mc judge"
  scok rx-mc-judge
  # the MeshCom judges on real line forms: the daemon's RF log (daemon_rflog.cpp:153 RX, :167 TX), a text frame as the firmware
  # lays it out (aprs_functions.cpp:1082-1130: header, <src>>*:<text>, 0x00, HW MOD FCS FW LH ..), and the node's prints
  # (printBuffer_aprs, loop_functions.cpp:3818; time getTimeString :3689)
  ( d=$(mktemp -d -p "$SCRATCH") && mkdir -p "$d/logs/r1" && nh=$(nhex 'rfkit 250b74dd') \
    && j() { printf '%s\n' "$2" > "$d/logs/r1/rf-daemon.log"; g=$(form "$1" rid=r1 n='rfkit 250b74dd' nh="$nh"); g=${g//\{RT\}/$d}; [ "$(bash -c "$g" | grep -c .)" = "$3" ]; } \
    && R='2026-11-17T18:33:54.126Z RX rssi=-53.00 snr=13.50 len=38 band=433 hex=' T='2026-11-17T18:33:54.126Z TX rssi=- snr=- len=38 outcome=ok band=433 hex=' \
    && j rx_mcom "${R}3a4d3c2b1a075858305858582d313e2a3a72666b6974203235306237346464002c031234232c ascii=\":M<+..XX0XXX-1>*:rfkit 250b74dd.,..4#,\"" 1 && j rx_mcom "${T}3a4d3c2b1a075858305858582d313e2a3a72666b6974203235306237346464002c031234232c ascii=\":M<+..XX0XXX-1>*:rfkit 250b74dd.,..4#,\"" 0 && j rx_mcom "${R}3a4d3c2b1a075858305858582d313e444c3141423a72666b6974203235306237346464002c031234232c ascii=\":M<+..XX0XXX-1>DL1AB:rfkit 250b74dd.,..4#,\"" 0 && j rx_mcom "${R}3a4d3c2b1a075858305858582d313e2a3a6563686f2072666b6974203235306237346464002c031234232c ascii=\":M<+..XX0XXX-1>*:echo rfkit 250b74dd.,..4#,\"" 0 && j rx_mcom "${R}3a4d3c2b1a075858305858582d313e2a3a72666b697420323530623734646478002c031234232c ascii=\":M<+..XX0XXX-1>*:rfkit 250b74ddx.,..4#,\"" 0 \
    && j rx_mcom "${R}3a4d3c2b1a07${nh}00 ascii=\"x\"" 0 \
    && j tx_mcom "${T}3a4d3c2b1a075858305858582d313e2a3a72666b6974203235306237346464002c031234232c ascii=\":M<+..XX0XXX-1>*:rfkit 250b74dd.,..4#,\"" 1 && j tx_mcom "${T/outcome=ok/outcome=unconfirmed}3a4d3c2b1a075858305858582d313e2a3a72666b6974203235306237346464002c031234232c ascii=\":M<+..XX0XXX-1>*:rfkit 250b74dd.,..4#,\"" 0 && j tx_mcom "${R}3a4d3c2b1a075858305858582d313e2a3a72666b6974203235306237346464002c031234232c ascii=\":M<+..XX0XXX-1>*:rfkit 250b74dd.,..4#,\"" 0 && j tx_mcom "${T}3a4d3c2b1a075858305858582d313e444c3141423a72666b6974203235306237346464002c031234232c ascii=\":M<+..XX0XXX-1>DL1AB:rfkit 250b74dd.,..4#,\"" 0 \
    && j tx_mcom "${T}3a4d3c2b1a075858305858582d313e2a3a72666b697420323530623734646478002c031234232c ascii=\":M<+..XX0XXX-1>*:rfkit 250b74ddx.,..4#,\"" 0 ) || sc rx-mcom-judge "the rx_mcom / tx_mcom judges"
  scok rx-mcom-judge
  ( d=$(mktemp -d -p "$SCRATCH") && mkdir -p "$d/src/meshcom-qemu-raspi/.run/1" \
    && j() { rm -f "$d"/src/meshcom-qemu-raspi/.run/uart-latest.log "$d"/src/meshcom-qemu-raspi/.run/1/uart-latest.log
      printf '%s\r\n' "$2" > "$d/src/meshcom-qemu-raspi/.run/$1uart-latest.log"; g=$(form rx_mcom_app rid=r2 n='rfkit 250b74dd' idx="$(mcidx "$3")"); g=${g//\{RT\}/$d}; [ "$(bash -c "$g" | grep -c .)" = "$4" ]; } \
    && L=' 038 : x1A2B3C4D H02 S0 T0 M00 XX0XXX-1>*:rfkit 250b74dd HW:44 MOD:3/1 FCS:1234 FW:35:t LH:44' \
    && j 1/ "18:33:54 RX-LoRa2$L" r2 1 && j "" "18:33:54 RX-LoRa2$L" r1 1 && j 1/ "18:33:54 RX-LoRa2$L" r1 0 \
    && j 1/ "18:33:54 NEW-TXT$L" r2 0 && j 1/ "18:33:54 TX-LoRa $L" r2 0 && j 1/ "18:33:54 [LOG]$L" r2 0 && j 1/ "18:33:54 MH-LoRa$L" r2 0 \
    && j 1/ "18:33:54 RX-LoRa2${L/>\*:/>DL1AB:}" r2 0 && j 1/ "18:33:54 RX-LoRa2${L/>\*:/>*:echo }" r2 0 && j 1/ "18:33:54 RX-LoRa2${L% HW:*}" r2 0 \
    && j 1/ "18:33:54 RX-LoRa2${L/250b74dd/250b74ddx}" r2 0 ) || sc rx-mcom-app-judge "the rx_mcom_app judge"
  scok rx-mcom-app-judge
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
    && ! jstatus "${L/ POWER=17/ POWER=x}" >/dev/null && ! jstatus "" >/dev/null && ! jstatus "${L/ POWER=17/ POWER=-3}" >/dev/null && [ "$(jstatus "${L/ POWER=17/ POWER=0}")" = 0 ] \
    && [ "$(jfield "$L" FREQ)" = 433.174988 ] && ! jfield "$L FREQ=1" FREQ >/dev/null && ! jfield "${L/ SF=11/}" SF >/dev/null || sc power-guards "the STATUS judges"
  G='ROW r1 loraham 433 True serial= port= hp=None
ROW r3 ch341-rfm98pw-433 433 True serial=X port= hp=False
ROW r4 pinedio-usb-1262 868 True serial= port=1-1 hp=True'
  jgrant "$G" r1 >/dev/null && jgrant "$G" r3 >/dev/null && ! jgrant "$G" r4 >/dev/null && ! jgrant "$G" r9 >/dev/null || sc power-guards "the high-power grant judge"
  jspower mt_pget 1 >/dev/null && ! jspower mt_pget 0 >/dev/null && jspower rt_pget 17 >/dev/null && ! jspower rt_pget 18 >/dev/null && ! jspower mc_pget 1x >/dev/null \
    && jcap 17 && ! jcap 18 && ! jcap 0 && pdom 17 && pdom 0 && ! pdom 18 && ! pdom -1 && ! pdom '' && ! pdom 0 mt-power || sc power-guards "the stack power / cap / dBm judges"
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
  before row_1 'R1-$b-$r-d' 1-start.txt && before row_2 'R2-$b-$r-d' 2-start.txt && before row_3 'pmust "$b" 3-cap.txt mt_cap' 3-start.txt && before row_4 'R4-$b-$r-d' 4-start.txt \
    || sc power-guards "a stack starts before its power read"
  before rnsx 'tx "$pk" 1 control --reserve' '-tx2-go.txt' && before rnsx 'tx "$x" 1 control --reserve' '-tx2-go.txt' && before row_2 'tx R2-tracker 1 control --reserve' '2-box-send.txt' \
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
# ---- the lab file: DATA, never run ---------------------------------------------------------------------------------------
LABKEYS='^(BOX_[HZ]_(HOST|USER|RUNTIME)|RADIO_[A-Z0-9]+|PEER_[a-z0-9]+|OPERATOR_CALL|VENV_MT|VENV_RNS|RNS_CONFIG|PEER_LOCK|EVD_ROOT|LAN_CERT|DRY_[A-Za-z0-9_]+)$'
labread() {   # labread <file>: blank and # lines, else one KEY=VALUE entry, KEY a lab key (LABKEYS), VALUE one of: a bare word of
  # [A-Za-z0-9_.,:/@+=-], '...' (may span lines), "..." (one line, no $ ` \ "), each with an optional ' # comment' after it.
  # Anything else is a STOP: no form, constant, table, function or command of the script can come from the lab file. A key
  # already set in the environment wins.
  local f=$1 l k v n=0 env=" " rb rs rd
  rb='^([A-Za-z0-9_.,:/@+=-]*)([[:space:]]+#.*)?$'; rs="^'([^']*)'([[:space:]]+#.*)?\$"; rd='^"([^"$`\\]*)"([[:space:]]+#.*)?$'
  for k in $(compgen -v); do [[ $k =~ $LABKEYS ]] && env+="$k "; done
  [ -f "$f" ] && [ -r "$f" ] || stop "$f: not readable"
  while IFS= read -r l || [ -n "$l" ]; do n=$((n + 1))
    [[ $l =~ ^[[:space:]]*(#.*)?$ ]] && continue
    [[ $l =~ ^([A-Za-z_][A-Za-z0-9_]*)=(.*)$ ]] || stop "$f:$n: not a KEY=VALUE line (the lab file is data, never run)"
    k=${BASH_REMATCH[1]}; v=${BASH_REMATCH[2]}
    [[ $k =~ $LABKEYS ]] || stop "$f:$n: '$k' is not a lab key"
    if [[ $v == "'"* ]]; then while [[ ${v:1} != *"'"* ]]; do IFS= read -r l || stop "$f:$n: the quote of $k is not closed"; n=$((n + 1)); v+=$'\n'$l; done
      [[ $v =~ $rs ]] || stop "$f:$n: $k: text after its closing quote"
    elif [[ $v == '"'* ]]; then [[ $v =~ $rd ]] || stop "$f:$n: $k: a \"...\" value is one line without \$ \` \\ or \""
    else [[ $v =~ $rb ]] || stop "$f:$n: $k: an unquoted value is one word of [A-Za-z0-9_.,:/@+=-] (quote it)"; fi
    v=${BASH_REMATCH[1]}; case "$env" in *" $k "*) ;; *) printf -v "$k" '%s' "$v";; esac
  done < "$f"; labcheck; }
# labcheck: every lab key's value (from the file or the environment) against its key's grammar, else a STOP before R0. A field
# is - or [A-Za-z0-9._/@:+=-] not starting with -: no blank, quote, ; | & $ ` # \ or newline (DRY_*: lines of fields)
labcheck() { local k v w g fd='(-|[A-Za-z0-9._/@:+=][A-Za-z0-9._/@:+=-]*)'
  for k in $(compgen -v); do [[ $k =~ $LABKEYS ]] || continue; v=${!k}
    case "$k" in
      BOX_?_RUNTIME|VENV_MT|VENV_RNS|RNS_CONFIG|PEER_LOCK|EVD_ROOT|LAN_CERT) w="a path (or empty)"; g='^((~/)?[A-Za-z0-9._/@:+][A-Za-z0-9._/@:+-]*)?$';;
      BOX_?_HOST) w="a host (or empty)"; g='^([A-Za-z0-9][A-Za-z0-9._:-]*)?$';;
      BOX_?_USER) w="a login (or empty)"; g='^([A-Za-z0-9_][A-Za-z0-9._-]*)?$';;
      OPERATOR_CALL) w="a call"; g='^[A-Za-z0-9-]*$';;   # its exact form is checked at the start
      *) w="fields (or empty)"; g="^($fd( $fd)*)?\$";; esac   # RADIO_*, PEER_*, and each line of a DRY_* value
    while IFS= read -r v; do [[ $v =~ $g ]] || stop "$k: $(printf %q "${!k}") is not $w: a lab value is [A-Za-z0-9._/@:+=-] words only"
    done < <(if [[ $k == DRY_* ]]; then printf '%s\n' "$v"; else printf '%s' "$v" | tr '\n' '\r'; echo; fi); done; }
# ---- the run's supervision: the main shell only waits; the run's work runs in ONE worker --------------------------------
# bash 5.2 loses a signal that lands while the shell expands a $(...): its trap fails to parse ("trap: line 2: unexpected
# EOF"), the trap never runs and the run goes on (fixed in bash 5.3). So the shell that traps INT/TERM/HUP/QUIT does nothing
# but `wait` (and its own paths below run no $(...)): the work runs in a worker in its own process group with every signal
# at its default, and on a signal the main shell ends that whole group BEFORE the recovery runs (no command of the run
# overlaps it). The main shell ignores SIGPIPE: a console that went away (a Ctrl-C that also ended `tee`) cannot end it
# before or during the recovery, whose record is the journal on disk; the worker gives the rows the default back.
runtraps() { trap 'rc=$?; trap "" HUP QUIT; [ "$rc" = 0 ] || recover; rm -rf "$SCRATCH"; exit $rc' EXIT; trap onsig INT TERM HUP QUIT; trap '' PIPE; }
supervise() {   # supervise <fn>: run <fn> as the worker, wait; its rc 1 (a FAIL) and 2 (a STOP) are the run's, as before
  set -m; ( set +m; trap - INT TERM HUP QUIT PIPE; "$1"; exit $? ) & WORKER=$!; set +m
  wait "$WORKER"; local wrc=$?; [ "$wrc" = 0 ] && return 0
  workerend   # any other end too: its whole group has ended before the recovery
  case $wrc in 1|2) exit "$wrc";; *) stop "the run's worker ended rc $wrc";; esac; }
onsig() { trap '' INT TERM HUP QUIT; [ -z "${WORKER:-}" ] || workerend; stop "interrupted (signal): the recovery runs"; }
workerend() {   # TERM to the worker's process group, reap the worker, then until the group is empty: KILL at 5 s, 10 s at most
  local t; kill -TERM -- -"$WORKER" 2>/dev/null; wait "$WORKER" 2>/dev/null
  for ((t = 1; t <= 20; t++)); do grpleft "$WORKER" || return 0; [ "$t" != 11 ] || kill -KILL -- -"$WORKER" 2>/dev/null; sleep 0.5; done
  rec "a process of the interrupted run did not end (process group $WORKER)" || true; }
grpleft() {   # grpleft <pgid>: a process of that group still runs; a zombie does not (an init that never reaps keeps
  # one, and `kill -0` counts it). Read from /proc with no $(...): it runs while the signals are trapped
  local g=$1 f s; for f in /proc/[0-9]*/stat; do { read -r s < "$f"; } 2>/dev/null || continue; s=${s##*) }
    set -- $s; [ "$3" = "$g" ] && [ "$1" != Z ] && return 0; done; return 1; }
rowgate() { [ -n "$(pst "$$")" ] || exit 2; }   # in the worker, $$ is the main shell: a KILLed one (gone or a zombie) ends the run here
runall() {   # the run's rows, in the worker
  local r; for r in R0 R1 M; do rowgate; echo "== ROW $r"; "row_$r"; done
  for r in $RUN; do rowgate; echo "== ROW $r"; runrow "$r"; done
  rowgate; echo "== ROW RS"; row_RS; coverage; echo "== RESULT"; restable || stop "RESULT.md: not written"; rowgate; echo "== ROW RE"; row_RE
  [ "$MODE" = live ] || selfcheck; }

# The guard's two context flags come only from tx (TXCTX) and the one setter/reader (PCTX), each local to its one call.
# One inherited from the environment would open the guard: STOP (also before the test-only return below).
[ -z "${PCTX:-}${TXCTX:-}" ] || stop "PCTX/TXCTX are set in the environment: the guard's context comes only from tx, pset and pread"
unset PCTX TXCTX
[ "${RFPROOF_SOURCE_ONLY:-0}" = 1 ] && return 0   # tests: the functions and constants only; no args, stdin, lab file, box or peer

# ==== the run ==================================================================================================================
MODE=${1:-dry}; case "$MODE" in dry|live) ;; *) echo "usage: rf-proof.sh dry|live" >&2; exit 2;; esac
# the lab file (a key set in the environment wins)
LAB_ENV=${LAB_ENV:-$HERE/lab.$([ "$MODE" = dry ] && echo example || echo local).env}
[ -f "$LAB_ENV" ] || stop "no lab file $LAB_ENV (copy lab.example.env to lab.local.env and fill it in)"
! grep -qiE '^[[:space:]]*(export[[:space:]]+)?([A-Za-z0-9]+_)*(PASS|PASSWD|PASSWORD|PW|PIN|SECRET)(_[A-Za-z0-9]+)*=' "$LAB_ENV" \
  || stop "$LAB_ENV sets a *PASS*/*PIN*/*SECRET* variable: secrets come on stdin only"
labread "$LAB_ENV"
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
LANE=${LANE:-fast}; case "$LANE" in fast|full|rns) ;; *) stop "LANE=fast|full|rns";; esac
if [ -n "${ROWS_ONLY:-}" ]; then for r in $ROWS_ONLY; do [ -n "${RROW[$r]:-}" ] || stop "ROWS_ONLY: '$r' is not a row id"; done
  [ -z "$(printf '%s\n' $ROWS_ONLY | sort | uniq -d)" ] || stop "ROWS_ONLY names a row twice"
  RUN=$(for r in $RIDS; do case " $ROWS_ONLY " in *" $r "*) echo "$r";; esac; done | tr '\n' ' ')
else RUN=$(for r in $RIDS; do case "$LANE $(rowf "$r" lane)" in "fast fast"|"full fast"|"full full"|"rns rns") echo "$r";; esac; done | tr '\n' ' '); fi   # rns: its own
RUN=${RUN% }
# stdin: the box passwords, then the PIN (live only), read once, then closed
unset MC_PIN PW_H PW_Z
if [ "$MODE" = live ]; then
  IFS= read -r PW_H || PW_H=; IFS= read -r PW_Z || PW_Z=
  case " $RUN " in *" 4 "*) IFS= read -r MC_PIN || MC_PIN=;; *) MC_PIN=;; esac
else PW_H=; PW_Z=; MC_PIN=; fi
declare +x PW_H PW_Z MC_PIN; exec </dev/null
for b in H Z; do p="PW_$b"; [ -n "${!p}" ] || continue   # a password inside a fixed name could not be masked or scanned: STOP before anything
  pweq "$b" && { say "$(pwnote | grep -F "box $b ")"; continue; }   # EXACTLY its login name: a lab box, unmasked, noted in REPORT.md
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
[ -z "$TRACKER_DBM" ] || pdom "$TRACKER_DBM" || stop "TRACKER_DBM is not a power value in 0..17 dBm"
TRACKER_ASIS=${TRACKER_ASIS:-}; case "$TRACKER_ASIS" in ''|1) ;; *) stop "TRACKER_ASIS is '' or 1";; esac   # his word 11-18: the tracker as-is
[ -z "$TRACKER_CALL" ] || [[ "$TRACKER_CALL" =~ ^[A-Z0-9]{3,8}-[0-9]{1,2}$ ]] || stop "TRACKER_CALL is not a call-SSID"
MCSET=${MCSET:-$([ "$LANE" = full ] && echo on || echo off)}; case "$MCSET" in on|off) ;; *) stop "MCSET=on|off";; esac
MCSET_J1=${MCSET_J1:-}; case "$MCSET_J1" in ''|global) ;; *) [[ "$MCSET_J1" =~ ^[A-Z0-9]{3,8}(-[0-9]{1,2})?$ ]] || stop "MCSET_J1 is not a callsign[-SSID] or 'global'";; esac
declare +x MCSET_J1; MCSET_PY=$(cat "$HERE/mcset.py") || stop "mcset.py: unreadable"
# the forms: dry confirms every form (every row prints all its commands); live needs every form of the chosen rows
if [ "$MODE" = dry ]; then FORMS_OK="${!F[*]}"; FORMS_HEAD=$INTHEAD
else FORMS_OK=${FORMS_OK:-}; FORMS_HEAD=${FORMS_HEAD:-}; [ -n "$FORMS_OK" ] || stop "FORMS_OK is empty: confirm the forms at INTHEAD first"
  miss=$(for r in $RUN; do for f in $(rforms "$r"); do case " $FORMS_OK " in *" $f "*) ;; *) echo "$f";; esac; done; done | sort -u | tr '\n' ' ')
  [ -z "$miss" ] || stop "form not confirmed: ${miss% } (FORMS_OK must hold every form of the chosen rows)"; fi
[[ ${TMPDIR:-/tmp} =~ ^/[A-Za-z0-9._/-]+$ ]] || stop "TMPDIR '${TMPDIR:-}' is not a plain absolute path ([A-Za-z0-9._/-]): the scratch root is spliced into commands"
SCRATCH=$(mktemp -d "${TMPDIR:-/tmp}/rf-proof.XXXXXX") || stop "mktemp"; DRYLOG=$SCRATCH/commands.log; : > "$DRYLOG"
if [ "$MODE" = dry ]; then OUT=$SCRATCH/out; EVD=$EVDROOT/rf-proof-dry; mkdir -p "$OUT" || stop "scratch"
else EVD=$EVDROOT/rf-proof-$(date -u +%Y%m%dT%H%M%SZ)-$LANE; OUT=$EVD; fi
runtraps

[ "$MODE" = dry ] || echo "LIVE: this run transmits. It needs the transmit and lab permission and the GO for this run, with the hardware connected, reachable and set up."
supervise runall
trap - EXIT; rm -rf "$SCRATCH"
if [ "$MODE" = live ] && [ -s "$EVD/FAILED.txt" ]; then echo "FAIL rows, for the coordinator: $(tr '\n' '|' < "$EVD/FAILED.txt")" >&2; exit 1; fi
echo "DONE ($MODE)"
