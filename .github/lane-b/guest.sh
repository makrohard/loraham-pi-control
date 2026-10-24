# Sourced by lane-b.yml's U0-B jobs (bash, set -euo pipefail): the Pi OS guest's preparation, boot and waits.
# Needs WORK (pios.img, deb.raw, key, key.pub there) and EV (the evidence dir). Every wait is a bounded poll.
G_SSH=(ssh -q -o BatchMode=yes -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=5
       -i "$WORK/key" -p 2222 lhpc@127.0.0.1)
g() { "${G_SSH[@]}" "$@"; }
gcp() { scp -q -o BatchMode=yes -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -i "$WORK/key" -P 2222 "$1" "lhpc@127.0.0.1:$2"; }

# The image grown to 16 GiB; the Debian kernel, initrd and modules in the Pi OS root (form B, test-only); the
# job's user and key in the image's own cloud-init user-data on the boot partition (the documented first boot).
prepare() {
  qemu-img resize -q -f raw "$WORK/pios.img" 16G
  echo ', +' | sudo -n sfdisk -q -N 2 --no-reread "$WORK/pios.img"
  local pl dl ver
  pl=$(sudo -n losetup -fP --show "$WORK/pios.img"); dl=$(sudo -n losetup -fP --show "$WORK/deb.raw")
  sudo -n e2fsck -fy "${pl}p2" > /dev/null; sudo -n resize2fs "${pl}p2" > /dev/null
  mkdir -p "$WORK/pboot" "$WORK/proot" "$WORK/droot"
  sudo -n mount "${pl}p1" "$WORK/pboot"; sudo -n mount "${pl}p2" "$WORK/proot"; sudo -n mount "${dl}p1" "$WORK/droot"
  ver=$(basename "$(ls -d "$WORK"/droot/lib/modules/* | head -1)")
  sudo -n cp "$WORK/droot/boot/vmlinuz-$ver" "$WORK/vmlinuz"; sudo -n cp "$WORK/droot/boot/initrd.img-$ver" "$WORK/initrd"
  sudo -n chmod 0644 "$WORK/vmlinuz" "$WORK/initrd"
  sudo -n cp -a "$WORK/droot/lib/modules/$ver" "$WORK/proot/lib/modules/"
  echo "guest_kernel=$ver" >> "$EV/image.txt"
  sudo -n cp "$WORK/pboot/user-data" "$EV/user-data.image-default"
  printf '#cloud-config\nusers:\n  - name: lhpc\n    groups: [sudo]\n    shell: /bin/bash\n    sudo: "ALL=(ALL) NOPASSWD:ALL"\n    lock_passwd: true\n    ssh_authorized_keys:\n      - %s\nssh_pwauth: false\nruncmd:\n  - [systemctl, enable, --now, ssh]\n' \
    "$(cat "$WORK/key.pub")" | sudo -n tee "$WORK/pboot/user-data" > /dev/null
  sudo -n blkid -s PARTUUID -o value "${pl}p2" > "$WORK/partuuid"
  sudo -n umount "$WORK/pboot" "$WORK/proot" "$WORK/droot"; sudo -n losetup -d "$pl" "$dl"
  test -s "$WORK/partuuid"
}

# The stated fallback when cloud-init does not deliver: the user and key written offline (QB-1's way).
offline_user() {
  local pl u gid
  pl=$(sudo -n losetup -fP --show "$WORK/pios.img"); sudo -n mount "${pl}p2" "$WORK/proot"
  sudo -n chroot "$WORK/proot" id lhpc > /dev/null 2>&1 || sudo -n chroot "$WORK/proot" useradd -m -s /bin/bash -G sudo lhpc
  u=$(sudo -n chroot "$WORK/proot" id -u lhpc); gid=$(sudo -n chroot "$WORK/proot" id -g lhpc)
  sudo -n install -d -m 0700 -o "$u" -g "$gid" "$WORK/proot/home/lhpc/.ssh"
  sudo -n install -m 0600 -o "$u" -g "$gid" "$WORK/key.pub" "$WORK/proot/home/lhpc/.ssh/authorized_keys"
  echo 'lhpc ALL=(ALL) NOPASSWD:ALL' | sudo -n tee "$WORK/proot/etc/sudoers.d/010-lane-b" > /dev/null
  sudo -n chroot "$WORK/proot" systemctl enable ssh
  sudo -n umount "$WORK/proot"; sudo -n losetup -d "$pl"
}

boot() {
  qemu-system-aarch64 -M virt -accel tcg -cpu max -smp 3 -m 4G -kernel "$WORK/vmlinuz" -initrd "$WORK/initrd" \
    -append "root=PARTUUID=$(cat "$WORK/partuuid") rw rootwait console=ttyAMA0" \
    -drive "file=$WORK/pios.img,format=raw,if=virtio" \
    -netdev user,id=n0,hostfwd=tcp:127.0.0.1:2222-:22 -device virtio-net-pci,netdev=n0 \
    -display none -serial "file:$EV/console-$1.log" -daemonize -pidfile "$WORK/qemu.pid"
}

# Diagnostics (the observation, not the install): in the guest as root, the systemctl call bootstrap-deps relies
# on (unit_present), sampled every 15 s with its rc, duration, stderr and the load; on the runner, load, memory
# and QEMU's CPU share every 15 s. Both run in the background until collect_samples.
start_samplers() {
  gcp "$GITHUB_WORKSPACE/.github/lane-b/sampler.sh" sampler.sh
  g 'sudo nohup bash sampler.sh > sampler.log 2> sampler.err < /dev/null & true'
  ( while :; do printf '%s load=%s mem_avail_mb=%s qemu_cpu=%s\n' "$(date -u +%FT%TZ)" "$(cut -d' ' -f1-3 /proc/loadavg)" \
      "$(awk '/MemAvailable/{print int($2/1024)}' /proc/meminfo)" "$(ps -o %cpu= -p "$(cat "$WORK/qemu.pid")" 2>/dev/null | tr -d ' ')"
      sleep 15; done ) >> "$EV/runner-load.txt" 2>&1 &
  echo $! > "$WORK/runner-sampler.pid"
}

# The guest's samples and journal around the install (also after a failure), and the runner sampler stopped.
collect_samples() {
  g 'sudo pkill -f "bash sampler.sh"; cat sampler.err 2>/dev/null; true' > "$EV/guest/sampler-stop.txt" 2>&1 || true
  g 'sudo journalctl -b --no-pager -o short-iso -u dbus.service -u systemd-logind.service -u polkit.service -p warning; true' > "$EV/guest/journal-warnings.txt" 2>&1 || true
  g 'sudo journalctl -b --no-pager -o short-iso _PID=1 | tail -n 300; true' > "$EV/guest/journal-pid1.txt" 2>&1 || true
  kill "$(cat "$WORK/runner-sampler.pid" 2>/dev/null)" 2>/dev/null || true
}

halt() { kill "$(cat "$WORK/qemu.pid")" 2>/dev/null || true; local i; for i in $(seq 1 60); do kill -0 "$(cat "$WORK/qemu.pid")" 2>/dev/null || return 0; sleep 1; done; return 1; }

# Polls until ssh answers; returns 1 at the bound.
wait_ssh() { local t0; t0=$(date +%s); until g true; do [ $(( $(date +%s) - t0 )) -lt "$1" ] || return 1; sleep 5; done; echo $(( $(date +%s) - t0 )); }

# Polls until the guest answers with a boot id other than $2, i.e. it has rebooted; prints the new id. An ssh that
# fails, an empty read and the old id all keep polling; returns 1 at the bound.
wait_reboot() { local t0 b; t0=$(date +%s)
  until b=$(g cat /proc/sys/kernel/random/boot_id) && [ -n "$b" ] && [ "$b" != "$2" ]; do
    [ $(( $(date +%s) - t0 )) -lt "$1" ] || return 1; sleep 5; done; echo "$b"; }

# Polls until the guest's systemd is up (running or degraded); returns 1 at the bound.
wait_up() { local t0 s; t0=$(date +%s)
  while :; do s=$(g 'systemctl is-system-running || true'); case "$s" in running|degraded) echo "$(( $(date +%s) - t0 )) $s"; return 0;; esac
    [ $(( $(date +%s) - t0 )) -lt "$1" ] || { echo "still $s" >&2; return 1; }; sleep 5; done; }
