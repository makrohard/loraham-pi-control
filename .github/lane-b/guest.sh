# Sourced by lane-b.yml's from-topic job (bash, set -euo pipefail) and its evidence steps: the Pi OS guest's
# preparation, boot and waits, and the stop of every writer into the evidence.
# Needs WORK (pios.img, deb.raw, key, key.pub there) and EV (the evidence dir). Every wait is a bounded poll.
G_SSH=(ssh -q -o BatchMode=yes -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=5
       -i "$WORK/key" -p 2222 lhpc@127.0.0.1)
g() { "${G_SSH[@]}" "$@"; }
gcp() { scp -q -o BatchMode=yes -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -i "$WORK/key" -P 2222 "$1" "lhpc@127.0.0.1:$2"; }

# Every writer into the evidence (a pidfile <f>) has a state record <f>.state: "none" from the job's first step,
# "launching" written just before its launch. Written whole: a temp file, then a rename.
wstate() { printf '%s\n' "$2" > "$1.state.tmp" && mv -f "$1.state.tmp" "$1.state"; }

# One clock read for a bounded wait: seconds since the epoch, or rc 1 (a failed or non-numeric read stops the wait).
now() { local n; n=$(date +%s) || return 1; case "$n" in ''|*[!0-9]*) return 1;; esac; echo "$n"; }

# The disk's PARTUUID as blkid gives it: 8 hex digits and the partition number (MBR), or a GPT UUID.
partuuid_ok() { local p; p=$(cat "$1") || return 1
  [[ $p =~ ^[0-9a-f]{8}-[0-9a-f]{2}$ || $p =~ ^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$ ]] \
    || { echo "partuuid: not a PARTUUID: '$p'" >&2; return 1; }; }

# The image grown to 16 GiB; the Debian kernel, initrd and modules in the Pi OS root (form B, test-only); the
# job's user and key in the image's own cloud-init user-data on the boot partition (the documented first boot).
prepare() {
  qemu-img resize -q -f raw "$WORK/pios.img" 16G
  echo ', +' | sudo -n sfdisk -q -N 2 --no-reread "$WORK/pios.img"
  local pl dl ver pub
  local -a mods
  pl=$(sudo -n losetup -fP --show "$WORK/pios.img"); dl=$(sudo -n losetup -fP --show "$WORK/deb.raw")
  sudo -n e2fsck -fy "${pl}p2" > /dev/null; sudo -n resize2fs "${pl}p2" > /dev/null
  mkdir -p "$WORK/pboot" "$WORK/proot" "$WORK/droot"
  sudo -n mount "${pl}p1" "$WORK/pboot"; sudo -n mount "${pl}p2" "$WORK/proot"; sudo -n mount "${dl}p1" "$WORK/droot"
  # Exactly one kernel's modules in the Debian image: its version names the kernel and initrd to take.
  mods=("$WORK"/droot/lib/modules/*)
  [ "${#mods[@]}" -eq 1 ] && [ -d "${mods[0]}" ] || { echo "prepare: not exactly one modules directory: ${mods[*]}" >&2; return 1; }
  ver=${mods[0]##*/}
  sudo -n cp "$WORK/droot/boot/vmlinuz-$ver" "$WORK/vmlinuz"; sudo -n cp "$WORK/droot/boot/initrd.img-$ver" "$WORK/initrd"
  sudo -n chmod 0644 "$WORK/vmlinuz" "$WORK/initrd"
  sudo -n cp -a "$WORK/droot/lib/modules/$ver" "$WORK/proot/lib/modules/"
  echo "guest_kernel=$ver" >> "$EV/image.txt"
  sudo -n cp "$WORK/pboot/user-data" "$EV/user-data.image-default"
  pub=$(cat "$WORK/key.pub") || { echo "prepare: the job's public key cannot be read" >&2; return 1; }
  [[ $pub == ssh-* && $pub != *$'\n'* ]] || { echo "prepare: the job's public key is not one ssh- line" >&2; return 1; }
  printf '#cloud-config\nusers:\n  - name: lhpc\n    groups: [sudo]\n    shell: /bin/bash\n    sudo: "ALL=(ALL) NOPASSWD:ALL"\n    lock_passwd: true\n    ssh_authorized_keys:\n      - %s\nssh_pwauth: false\nruncmd:\n  - [systemctl, enable, --now, ssh]\n' \
    "$pub" | sudo -n tee "$WORK/pboot/user-data" > /dev/null
  sudo -n blkid -s PARTUUID -o value "${pl}p2" > "$WORK/partuuid"
  sudo -n umount "$WORK/pboot" "$WORK/proot" "$WORK/droot"; sudo -n losetup -d "$pl" "$dl"
  partuuid_ok "$WORK/partuuid"
}

# QEMU publishes its own pidfile (-daemonize -pidfile); its state record says "launching" first. What the launch
# prints (a failure before it daemonizes) is kept in qemu-launch.log, the launch-failure capture.
boot() { local pu
  partuuid_ok "$WORK/partuuid" || return 1
  pu=$(cat "$WORK/partuuid") || return 1
  wstate "$WORK/qemu.pid" launching || return 1
  qemu-system-aarch64 -M virt -accel tcg -cpu max -smp 3 -m 4G -kernel "$WORK/vmlinuz" -initrd "$WORK/initrd" \
    -append "root=PARTUUID=$pu rw rootwait console=ttyAMA0" \
    -drive "file=$WORK/pios.img,format=raw,if=virtio" \
    -netdev user,id=n0,hostfwd=tcp:127.0.0.1:2222-:22 -device virtio-net-pci,netdev=n0 \
    -display none -serial "file:$EV/console-$1.log" -daemonize -pidfile "$WORK/qemu.pid" 2>> "$WORK/qemu-launch.log"
}

# Diagnostics (the observation, not the install): in the guest as root, the systemctl call bootstrap-deps relies
# on (unit_present), sampled every 15 s with its rc, duration, stderr and the load; on the runner, load, memory
# and QEMU's CPU share every 15 s. Both run in the background until collect_samples.
start_samplers() {
  gcp "$GITHUB_WORKSPACE/.github/lane-b/sampler.sh" sampler.sh
  g 'sudo nohup bash sampler.sh > sampler.log 2> sampler.err < /dev/null & true'
  # Its own process group (setsid), so that stop_writer ends it with every child that holds the file open. The
  # state record says "launching" and the pidfile holds a sentinel until the sampler itself, inside its new
  # group, publishes "grp:<its group id>" (a temp file, then a rename) before its first line: only a value
  # published from inside the group lets stop_writer stop it (LANEB-G2-TABLES-v7 A1e-A9). The launch waits a
  # bounded time for that publication; none means the job fails here, and the evidence step refuses.
  local i t
  wstate "$WORK/runner-sampler.pid" launching || return 1
  echo "unpublished" > "$WORK/runner-sampler.pid" || return 1
  WORK=$WORK setsid bash -c 'printf "grp:%s\n" "$$" > "$WORK/runner-sampler.pid.tmp" && mv -f "$WORK/runner-sampler.pid.tmp" "$WORK/runner-sampler.pid" || exit 1
    while :; do printf "%s load=%s mem_avail_mb=%s qemu_cpu=%s\n" "$(date -u +%FT%TZ)" "$(cut -d" " -f1-3 /proc/loadavg)" \
      "$(awk "/MemAvailable/{print int(\$2/1024)}" /proc/meminfo)" "$(ps -o %cpu= -p "$(cat "$WORK/qemu.pid")" 2>/dev/null | tr -d " ")"
      sleep 15; done' >> "$EV/runner-load.txt" 2>&1 &
  for (( i = 0; i < 50; i++ )); do
    t=$(cat "$WORK/runner-sampler.pid") || return 1
    case "$t" in grp:*) return 0;; esac
    sleep 0.1
  done
  echo "start_samplers: the sampler published no group within 50 polls" >&2; return 1
}

# The guest's samples and journal around the install (also after a failure). The runner sampler is stopped by
# the evidence step's stop_writer.
collect_samples() {
  g 'sudo pkill -f "bash sampler.sh"; cat sampler.err 2>/dev/null; true' > "$EV/guest/sampler-stop.txt" 2>&1 || true
  g 'sudo journalctl -b --no-pager -o short-iso -u dbus.service -u systemd-logind.service -u polkit.service -p warning; true' > "$EV/guest/journal-warnings.txt" 2>&1 || true
  g 'sudo journalctl -b --no-pager -o short-iso _PID=1 | tail -n 300; true' > "$EV/guest/journal-pid1.txt" 2>&1 || true
}

# Diagnostics after a failed units line (the observation, not a fix): the user unit's status and journal, then one
# timed `systemctl --user start` purely as a measurement of how long the start takes under TCG.
observe_nginx() {
  g 'systemctl --user status --no-pager lhpc-nginx.service; true' > "$EV/guest/nginx-status.txt" 2>&1 || true
  g 'journalctl --user --no-pager -o short-iso -u lhpc-nginx.service; true' > "$EV/guest/nginx-journal.txt" 2>&1 || true
  g 's=$(date +%s%N); systemctl --user start lhpc-nginx.service; r=$?; echo "rc=$r ms=$(( ($(date +%s%N) - s) / 1000000 ))"; systemctl --user is-active lhpc-nginx.service; true' \
    > "$EV/guest/nginx-start-timed.txt" 2>&1 || true
}

# Stops a writer into the evidence that the pidfile $1 names and waits until no such process is left, deciding on
# the writer's state record $1.state (LANEB-G2-TABLES-v7, table A). Returns 0 when the writer is shown gone, or
# was never launched (state "none" and no pidfile); 3 when qemu's launch failed (state "launching", no pidfile:
# only the launch-failure capture may leave the job, LBS-D2); 1 for everything else: no or another state record,
# a pidfile beside "none", content not published as stated, a liveness check that fails, or the 60 s bound.
# qemu.pid holds one decimal pid > 1 and a final newline (qemu's own pidfile); runner-sampler.pid holds
# "grp:<group id > 1>" as the sampler published it from inside its group.
stop_writer() { local f=$1 st t n sz last i r again
  [ -e "$f.state" ] || { echo "$f: no state record" >&2; return 1; }
  st=$(cat "$f.state") || { echo "$f: the state record cannot be read" >&2; return 1; }
  case "$st" in
    none) [ -e "$f" ] || return 0; echo "$f: state none, but a pidfile exists" >&2; return 1;;
    launching) ;;
    *) echo "$f: unknown state '$st'" >&2; return 1;;
  esac
  if [ ! -e "$f" ]; then
    [ "${f##*/}" = qemu.pid ] && { echo "$f: launching, no pidfile: the launch failed" >&2; return 3; }
    echo "$f: launching, nothing published" >&2; return 1
  fi
  t=$(cat "$f") || { echo "$f: the pidfile cannot be read" >&2; return 1; }
  if [ "${f##*/}" = qemu.pid ]; then
    [[ $t =~ ^[1-9][0-9]{0,9}$ ]] && [ "$t" -gt 1 ] || { echo "$f: not a pid: '$t'" >&2; return 1; }
    # the bytes: the pid and one final newline, read without command substitution's newline stripping
    sz=$(wc -c < "$f") || return 1
    last=$(od -An -c -j "$(( sz - 1 ))" -N 1 "$f") || return 1
    [ "$sz" -eq "$(( ${#t} + 1 ))" ] && [ "${last// /}" = '\n' ] || { echo "$f: not one pid and a final newline" >&2; return 1; }
    n=$t
  else
    [[ $t =~ ^grp:[1-9][0-9]{0,9}$ ]] && [ "${t#grp:}" -gt 1 ] || { echo "$f: not a group published from inside: '$t'" >&2; return 1; }
    n=-${t#grp:}
  fi
  kill -- "$n" 2>/dev/null || true
  for (( i = 0; i < 60; i++ )); do
    r=0; if [ "${n#-}" = "$n" ]; then ps -p "$n" > /dev/null || r=$?; else pgrep -g "${n#-}" > /dev/null || r=$?; fi
    case $r in
      1) # gone: the group is still empty and the pidfile still holds the value the stop acted on
         [ "${n#-}" = "$n" ] && return 0
         r=0; pgrep -g "${n#-}" > /dev/null || r=$?
         again=$(cat "$f") || { echo "$f: the pidfile cannot be read after the stop" >&2; return 1; }
         [ "$r" -eq 1 ] && [ "$again" = "$t" ] && return 0
         echo "$f: not shown gone (pgrep rc $r, pidfile '$again')" >&2; return 1;;
      0) sleep 1;;
      *) echo "$f: the liveness check failed (rc $r)" >&2; return 1;;
    esac
  done; echo "$f: still runs after 60 s" >&2; return 1; }

# The waits below poll every 5 s and stop at the bound in seconds ($1) or, as a second bound, after $1/5 + 1 polls;
# a clock read that fails stops the wait (rc 1), so a bound that cannot fire never lets it run on.

# Polls until ssh answers; returns 1 at the bound.
wait_ssh() { local t0 n i=0; t0=$(now) || return 1
  until g true; do n=$(now) || return 1; i=$(( i + 1 ))
    [ $(( n - t0 )) -lt "$1" ] && [ "$i" -le $(( $1 / 5 + 1 )) ] || return 1; sleep 5; done
  n=$(now) || return 1; echo $(( n - t0 )); }

# Polls until the guest answers with a boot id other than $2, i.e. it has rebooted; prints the new id. An ssh that
# fails, an empty read and the old id all keep polling; returns 1 at the bound.
wait_reboot() { local t0 n b i=0; t0=$(now) || return 1
  until b=$(g cat /proc/sys/kernel/random/boot_id) && [ -n "$b" ] && [ "$b" != "$2" ]; do
    n=$(now) || return 1; i=$(( i + 1 ))
    [ $(( n - t0 )) -lt "$1" ] && [ "$i" -le $(( $1 / 5 + 1 )) ] || return 1; sleep 5; done; echo "$b"; }

# Polls until the guest's systemd is up (running or degraded); returns 1 at the bound. Up is only the state with
# the status systemctl gives it: 0 with running, 1 (its failure status for every other state) with degraded.
wait_up() { local t0 n s r i=0; t0=$(now) || return 1
  while :; do r=0; s=$(g 'systemctl is-system-running') || r=$?
    n=$(now) || return 1
    case "$r $s" in "0 running"|"1 degraded") echo "$(( n - t0 )) $s"; return 0;; esac
    i=$(( i + 1 ))
    [ $(( n - t0 )) -lt "$1" ] && [ "$i" -le $(( $1 / 5 + 1 )) ] || { echo "still rc $r: $s" >&2; return 1; }; sleep 5; done; }
