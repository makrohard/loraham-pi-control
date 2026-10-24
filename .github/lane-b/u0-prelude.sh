export DSHA REPO WS RT B=868 LIVE=$WS/dl1-lhpc-live ST=$WS/dl1-lhpc-live/state.env
export REL=9f18f26c68567e0571d4f16726ed4ae7a35232ec DMN=660be550e3af762120f41a02d48b53af45ad109c LIBC=b0694ec84091b293cb1e97e00a821ff88353274f
export IDX=https://github.com/makrohard/lhpc-binaries/releases/download/binaries/index-v3.json
export LIBM=src/loraham-daemon/loraham_daemon/lib/libloraham-ch341.so BINM=src/loraham-daemon/loraham_daemon/loraham_daemon
export SLOG=logs/start-loraham-daemon-868.log
export WHP='WARNING OCP 100 mA under --high-power: may limit output power (give --ocp above 100)'
export WOFF='WARNING OCP disabled (--ocp 0): warranty void, hardware may be in danger'
set -o pipefail
Hx() { SSHPASS=$HPASS sshpass -e ssh -o PubkeyAuthentication=no "$HHOST" "bash -lc $(printf %q "$1")"; }
fail()  { echo "FAIL: $*" >&2; exit 1; }
stop()  { echo "STOP: $*" >&2; exit 2; }
open()  { echo "OPEN: $*"; exit 3; }
num()   { case "$2" in ''|*[!0-9]*) fail "$1 is not a number: '$2'";; esac; }
hex40() { case "$2" in *[!0-9a-f]*|'') fail "$1 is not a commit";; esac; [ ${#2} -eq 40 ] || fail "$1 is not a commit"; }
hex64() { case "$2" in *[!0-9a-f]*|'') fail "$1 is not a sha256";; esac; [ ${#2} -eq 64 ] || fail "$1 is not a sha256"; }
eq()    { [ "$2" = "$3" ] || fail "$1: got '$2', want '$3'"; }
has()   { case "$2" in *"$3"*) ;; *) fail "$1: '$3' not found";; esac; }
ge()    { num "$1" "$2"; [ "$2" -ge "$3" ] || fail "$1: $2 < $3"; }
same()  { diff "$1" "$2" || fail "differs: $1 $2"; }
count() { local n; n=$(grep -c "$@") || [ $? -eq 1 ] || fail "grep -c $*: error"; printf '%s\n' "$n"; }
keep()  { printf '%s=%q\n' "$1" "$2" >> "$ST"; }
cap()   { local f=$1 rc=0; shift; Hx "$*" > "$LIVE/$f" 2>&1 || rc=$?; echo "rc=$rc" >> "$LIVE/$f"; [ $rc -eq 0 ] || fail "$f: rc $rc"; }
capx()  { local f=$1 want=$2 rc=0; shift 2; Hx "$*" > "$LIVE/$f" 2>&1 || rc=$?; echo "rc=$rc" >> "$LIVE/$f"; eq "$f: the refusal rc" "$rc" "$want"; }
dpid()  { local p rc=0; p=$(Hx "ps=\$(pgrep -x loraham_daemon); r=\$?; [ \$r -le 1 ] || exit \$r; for q in \$ps; do if [ \"\$(readlink /proc/\$q/exe)\" = '$RT/$BINM' ]; then echo \$q; fi; done") || rc=$?
          [ $rc -eq 0 ] || stop "pgrep on box H: rc $rc"; printf '%s' "$p"; }
st()    { Hx "python3 -c 'import socket,sys; s=socket.socket(socket.AF_UNIX); s.settimeout(3); s.connect(sys.argv[1]); s.sendall(b\"GET STATUS\\n\"); print(s.makefile().readline().strip())' /tmp/loraconf$B.sock"; }
ready() { local i s; for i in $(seq 1 60); do s=$(st 2>/dev/null) || s=; case "$s" in *"RADIO=READY "*) echo "$s"; return 0;; esac; sleep 0.25; done; fail "868 not READY within 15 s"; }
gone()  { local i; for i in $(seq 1 60); do [ -n "$(dpid)" ] || return 0; sleep 0.25; done; fail "the daemon still runs 15 s after the stop"; }
lsize() { local n; n=$(Hx "if [ -f $RT/$SLOG ]; then stat -c %s $RT/$SLOG; else echo 0; fi"); num "start-log size" "$n"; printf '%s\n' "$n"; }
# one start, read end to end: the start-log cursor, start, READY, argv, STATUS, this start's log part, stop
case_run() { local tag=$1 c0 c1 s p
  c0=$(lsize)
  cap "$tag-start.txt" "lhpc stack start daemon --band $B --yes"
  s=$(ready); printf '%s\n' "$s" > "$LIVE/$tag-STATUS.txt"
  p=$(dpid); num pid "$p"
  cap "$tag-argv.txt" "tr '\\0' ' ' < /proc/$p/cmdline"
  c1=$(lsize); if [ "$c1" -lt "$c0" ]; then c0=0; fi     # the cap rotated the log: this start is the whole file
  cap "$tag-startlog.txt" "tail -c +$((c0 + 1)) $RT/$SLOG"
  cap "$tag-stop.txt" "lhpc stack stop daemon --band $B --yes"; gone; }
row() { local n=$1 rc; ( set -euo pipefail; [ ! -f "$ST" ] || . "$ST"; eval "$(cat)" ); rc=$?
        case $rc in 0) echo "ROW $n: PASS";; 3) echo "ROW $n: OPEN";; 2) echo "ROW $n: STOP - the slot ends; run R9";; *) echo "ROW $n: FAIL (rc $rc)";; esac; }
