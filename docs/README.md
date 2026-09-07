# Documentation

The `lhpc` docs, grouped by what you are doing. New here: read Architecture for the
model, then the CLI. Every file is linked once; the README covers install.

## Contents

- [Understand](#understand)
- [Operate](#operate)
- [Reach it](#reach-it)
- [Stacks](#stacks)
- [Verify](#verify)
- [Policy](#policy)

## Understand

- [Architecture](architecture.md) — the runtime root, how state is reconstructed, radios, bands
  and resource claims, the identity rules, and the safety model.

## Operate

- [CLI](cli.md) — every command; the web console is a front end to these.
- [Operations](operations.md) — operating the console, start flow, boot restore, passwords and
  secrets, backup and restore, the binary channel in operation.
- [GPS](gps.md) — the one global position source and what each stack does with it.
- [Maintenance](maintenance.md) — routine upkeep of an installed box, the pin bump recipe, Pi
  gotchas.
- [Backlog](backlog.md) — open follow-ups and accepted deferrals; read before changing a unit
  template.

## Reach it

- [Deployment](deployment.md) — the console as a rootless user service, identity, self-update.
- [Webserver (HTTPS + mTLS)](webserver.md) — the nginx front end, client-certificate auth, the
  remote exposure runbook, proxying stack web UIs.
- [SSH tunnel](ssh-tunnel.md) — console and every stack UI over SSH alone, nothing exposed.
- [WiFi access point](wifi-access-point.md) — the Pi as its own WiFi network, the Network panel.
- [Firewall](firewall.md) — the managed nftables firewall and the by-hand recipes.

## Stacks

- [Adding a stack](adding-a-stack.md) — the single-manifest model for extending `lhpc`, the
  validator table.
- [daemon](stacks/daemon.md) — LoRaHAM daemon, owns the radios; radio parameters.
- [kiss](stacks/kiss.md) — KISS TNC over TCP.
- [graywolf](stacks/graywolf.md) — Graywolf APRS station over `kiss`.
- [chat](stacks/chat.md) — APRS/chat TUI.
- [voice](stacks/voice.md) — LoRa voice.
- [meshtastic](stacks/meshtastic.md) — rootless `meshtasticd`.
- [meshcom](stacks/meshcom.md) — MeshCom firmware in QEMU, bridged to the daemon.
- [meshcore](stacks/meshcore.md) — MeshCore on openHop: chat node and/or repeater.
- [reticulum](stacks/reticulum.md) — Reticulum node over SPI.

## Verify

- [Test matrix](test-matrix.md) — the per-release procedure: every stack purged, installed,
  built and started on the box.
- [Live tests](live-test.md) — dated validation evidence: on-air runs and their outcomes.
- [Test lab](testlab.md) — real stack processes against simulated hardware, locally or in a
  Codespace.

## Policy

- [Contributing](../CONTRIBUTING.md) — the branch model and what should be green before a PR.
- [Provenance](provenance.md) — supply-chain rules for managed source and the binary channel.
