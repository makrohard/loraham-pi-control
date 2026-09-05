# Documentation

Guide to the `lhpc` docs, grouped by what you're trying to do.

> **New here?** Read [Architecture](architecture.md) for the model, then the
> [CLI reference](cli.md). **Running the console for others to reach?** Start with
> [Web console & remote access](#web-console--remote-access). **Adding hardware or
> apps?** See [Stacks](#stacks).

## Understand it

- [Architecture](architecture.md) — the runtime root, how state is reconstructed
  (never from stale PID files), and why `lhpc` is not a supervisor.

## Use it

- [CLI reference](cli.md) — every command. The web console is a front-end to these.
- [Operations & safety](operations.md) — operating rules, install channels, TX safety, secrets,
  backup & restore.
- [Maintenance](maintenance.md) — routine upkeep of an installed box, incl. recompiling the binary stacks.
- [Field notes](field-notes.md) — fresh-install checklist, Pi Zero 2 W vs Pi 5 build
  durations, QEMU-stack expectations, log naming, and per-board `gpiochip`/GPIO notes.
- [Release test matrix](test-matrix.md) — the leading live test before a tag: every stack
  purged, installed, built and started on the box, install/build/start times, memory watched
  during the heavy compiles, auto-install consistency, and the result table per release.
- [Silicon test 2026-09-05](silicon-test-2026-09-05.md) — the stacks on the air against real
  ESP32 peers running each project's own original firmware: what passed, what failed, and the
  open items.
- [GPS](gps.md) — the one global position source, what lhpc automates and what stays
  yours (gpsd itself), and the per-stack behaviour.
- [Backlog](backlog.md) — accepted deferrals, and what holds the line for each
  until they are fixed. Read before changing a systemd unit template.

## Web console & remote access

- [Local deployment](deployment.md) — run the console persistently under a systemd
  user service (loopback only) and how one-click self-update works.
- [Production webserver (HTTPS + mTLS)](webserver.md) — using the console (Dashboard /
  Stack pages / Settings), the nginx front end, client-certificate auth, and exposing it to your LAN.
- [SSH tunnel](ssh-tunnel.md) — reach the console and every stack UI from a remote machine over
  SSH alone, with remote exposure left off; one copyable tunnel per stack.
- [WiFi access point (field)](wifi-access-point.md) — turn the Pi into its own WiFi
  network so a phone can reach it with no infrastructure WiFi.
- [Firewalling the Pi](firewall.md) — the opt-in **managed nftables firewall** (one sudo command)
  + by-hand recipes for local-only, LAN, public and
  WiFi-AP access; closes the stack ports `lhpc` cannot gate (meshtasticd `:4403`/`:9443`).
- [Deployment migration](deployment-migration.md) — relocate an existing deployment
  (operator runbook).

## Stacks

- [Adding & maintaining a stack](adding-a-stack.md) — the single-manifest model for
  extending `lhpc`.
- Per stack: [daemon](stacks/daemon.md) · [KISS/TCP TNC](stacks/kiss.md) ·
  [Graywolf APRS](stacks/graywolf.md) ·
  [Chat & iGate (APRS)](stacks/aprs.md) · [MeshCore](stacks/meshcore.md) ·
  [MeshCom](stacks/meshcom.md) · [Meshtastic](stacks/meshtastic.md) ·
  [Reticulum](stacks/reticulum.md) · [Voice](stacks/voice.md)

## Reference & policy

- [Hardening & safety model](hardening-0.1.md) — what the controller guarantees and
  how each guarantee is enforced.
- [Source provenance policy](provenance.md) — supply-chain rules for managed source.
