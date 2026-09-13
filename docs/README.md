# Documentation

The `lhpc` docs, grouped by what you are doing. New here: read Architecture for the
model, then the CLI. Every file is linked once (the dated live-test reports through their
folder); the README covers install.

## Contents

- [Understand](#understand)
- [Operate](#operate)
- [Reach it](#reach-it)
- [Stacks](#stacks)
- [Verify](#verify)
- [Policy](#policy)

## Understand

- [Architecture](architecture.md) — the runtime root, the package layout and its dependency
  rule, the identity rules, how state is reconstructed, radios, bands and resource claims, the
  safety model, the controller identity and self-update.

## Operate

- [CLI](cli.md) — every command.
- [Operations](operations.md) — boot restore, the binary channel in operation, TX safety,
  passwords and secrets, backup and restore, operating the console and the start flow, Reboot /
  Shut down, the Network panel, identity drift on clean or uninstall.
- [GPS](gps.md) — the one global position source and what each stack does with it.
- [Maintenance](maintenance.md) — what CI proves, branches and releases, the pin bump recipe, Pi
  gotchas, job logs and RF logs.
- [Backlog](backlog.md) — accepted deferrals; read before changing a unit template.

## Reach it

- [Deployment](deployment.md) — the console as a rootless user service, identity, self-update.
- [Webserver (HTTPS + mTLS)](webserver.md) — the nginx front end, client-certificate auth, the
  remote exposure runbook, proxying stack web UIs.
- [SSH tunnel](ssh-tunnel.md) — console and every stack UI over SSH alone, nothing exposed.
- [WiFi access point](wifi-access-point.md) — the Pi as its own WiFi network, the Network panel.
- [Firewall](firewall.md) — the managed nftables firewall and the by-hand recipes.

## Stacks

One file per stack. The pinned commit of every managed source is in
`lhpc/data/manifest.example.toml` and nowhere else; `lhpc status` shows what a box has installed.

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

- [Test matrix](test-matrix.md) — the full minor-release procedure: every stack purged, installed,
  built and started on the box.
- [Test lab](testlab.md) — real stack processes against simulated hardware, locally or in a
  Codespace; the four verification lanes.
- [Live tests](live-tests/live-test.md) — the `docs/live-tests/` folder: `live-test.md` is the
  newest run on the reference box; the dated per-feature reports (`<feature>-test-<date>.md`)
  live beside it.
- [Test suite](../tests/README.md) — where a test goes, the rules, the markers, how to run.
- [Test-lab package](../testlab/README.md) — the separate `lhpc-testlab` package and its install.

## Policy

- [Contributing](../CONTRIBUTING.md) — how to open a PR and what should be green.
- [Provenance](provenance.md) — supply-chain rules for managed source and the binary channel.
