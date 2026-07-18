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
- [Operations & safety](operations.md) — operating rules, TX safety, secrets,
  backup & restore.

## Web console & remote access

- [Local deployment](deployment.md) — run the console persistently under a systemd
  user service (loopback only) and how one-click self-update works.
- [Production webserver (HTTPS + mTLS)](webserver.md) — the nginx front end,
  client-certificate auth, and exposing the console to your LAN.
- [WiFi access point (field)](wifi-access-point.md) — turn the Pi into its own WiFi
  network so a phone can reach it with no infrastructure WiFi.
- [Deployment migration](deployment-migration.md) — relocate an existing deployment
  (operator runbook).

## Stacks

- [Adding & maintaining a stack](adding-a-stack.md) — the single-manifest model for
  extending `lhpc`.
- Per stack: [daemon](stacks/daemon.md) · [KISS/TCP TNC](stacks/kiss.md) ·
  [Chat & iGate (APRS)](stacks/aprs.md) · [MeshCore](stacks/meshcore.md) ·
  [MeshCom](stacks/meshcom.md) · [Meshtastic](stacks/meshtastic.md) ·
  [Voice](stacks/voice.md)

## Reference & policy

- [Hardening & safety model](hardening-0.1.md) — what the controller guarantees and
  how each guarantee is enforced.
- [Source provenance policy](provenance.md) — supply-chain rules for managed source.

## Project records

- [Live-test log](live-test-2026-07-14.md) — dated end-to-end test runs (newest first).
