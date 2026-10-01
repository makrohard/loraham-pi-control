# Documentation

The `lhpc` docs by task; the top-level README covers install. Start with Architecture, then the CLI.

## Contents

- [Understand](#understand)
- [Operate](#operate)
- [Reach it](#reach-it)
- [Stacks](#stacks)
- [Verify](#verify)
- [Policy](#policy)

## Understand

- [Architecture](architecture.md) — the model and the safety invariants.

## Operate

- [CLI](cli.md) — every command and flag.
- [Operations](operations.md) — running the box: boot restore, TX safety, secrets, backup, the console.
- [GPS](gps.md) — the global position source and how each stack uses it.
- [Maintenance](maintenance.md) — CI, branches and releases, moving a pin, Pi gotchas.
- [Backlog](backlog.md) — accepted deferrals; read before changing a unit template.

## Reach it

- [Deployment](deployment.md) — the console's systemd units and self-update.
- [Webserver (HTTPS + mTLS)](webserver.md) — the nginx front end and remote access.
- [SSH tunnel](ssh-tunnel.md) — the console and stack UIs over SSH only.
- [WiFi access point](wifi-access-point.md) — the Pi as its own WiFi network.
- [Firewall](firewall.md) — the managed nftables firewall.

## Stacks

- [Adding a stack](adding-a-stack.md) — the extension contract.
- [daemon](stacks/daemon.md) — LoRaHAM daemon; owns the radios.
- [kiss](stacks/kiss.md) — KISS TNC over TCP.
- [graywolf](stacks/graywolf.md) — Graywolf APRS station over `kiss`.
- [chat](stacks/chat.md) — APRS/chat TUI.
- [voice](stacks/voice.md) — LoRa voice.
- [meshtastic](stacks/meshtastic.md) — rootless `meshtasticd`.
- [meshcom](stacks/meshcom.md) — MeshCom firmware in QEMU.
- [meshcore](stacks/meshcore.md) — MeshCore chat node and/or repeater.
- [reticulum](stacks/reticulum.md) — Reticulum node over SPI.

## Verify

- [Test matrix](test-matrix.md) — the release test procedure on the box.
- [Test lab](testlab.md) — stacks against simulated hardware; the verification lanes.
- [Live tests](live-tests/live-test.md) — recorded runs on the reference box; dated reports sit beside it.
- [Test suite](../tests/README.md) — where a test goes and how to run it.
- [Test-lab package](../testlab/README.md) — the `lhpc-testlab` package.

## Policy

- [Contributing](../CONTRIBUTING.md) — opening a PR.
- [Maintaining](../MAINTAINING.md) — maintainer entry point: repositories, house rules, release checklists.
- [Provenance](provenance.md) — trust in managed source and the binary channel.
