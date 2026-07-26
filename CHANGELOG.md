# Changelog

## unreleased

- Managed firewall
- System monitor
- Boot auto-restore: stacks that were running before a reboot are restarted at boot
  (`lhpc autostart on|off`, default on; new `lhpc-boot-restore.service` user unit)
- Binary install channel: prebuilt, sha256-verified artifacts for daemon/meshtastic/meshcom
  (`lhpc install <stack>` uses it where published; `--source pinned|dev|stable` builds from
  source). Provenance-pinned to the manifest commits, smoke-gated, never a silent fallback.
- Binary channel: switching to a source channel sets the artifact aside locally instead of
  deleting it — a failed source install restores the previous binary without touching the network
- Source transactions: a checkout that a stack RUNS FROM may hold a runtime socket (MeshCom's
  `.run/`). Archiving it no longer aborts the cleanup halfway — which used to leave the archived
  copy unremovable and every source operation on the box blocked.

## 0.1.5
- Hardware setups: `lhpc hardware` selects the radio rig (LoRaHAM / Uputronics dual / Waveshare); daemon v112 multi-hardware, per-band arbitration
- Built-from-source runtime: headless QEMU and server-only meshtasticd compiled from pinned sources into the runtime root
- Headless by default: GUI stacks and their packages are opt-in (`--with-gui`)
- auto-install: per-stack selection, abort and recovery — from-zero proven on Pi Zero 2W and Pi 5
- Self-update hardened: sandboxed CPU-throttled helper unit, nginx-restart escape hatch for bind changes, handles force-pushed upstreams
- bootstrap-deps.sh: dry-run gate, LAN-aware Wi-Fi power-save handling, auto swapfile, persistent journal
- MeshCom HMAC auth + running-task indicators
- Start confirm: per-band stack parameters + callsign enforcement
- Web GUI: dark mode, dependency overview + checks, per-stack daemon params/frequency, unified webserver controls
- Audit + stabilization pass; known-good pins refreshed to the run-proven set (Zero 2W + Pi 5 acceptance runs)

## 0.1.4
- Make web-GUI, meshcom and meshtastic GUI remote exposable With TLS and certificate-auth
- CLI consistency — `lhpc config` (per-stack settings, callsign, daemon params, operator identity), `stack restart`, `webserver proxy`, `cert export`; every next-step hint points at a real command
- per-component update availability indicator
- GUI polishing
- Docs: auto-install flow, expose-with-mTLS + browser client-cert runbook, backup/restore, per-file tables of contents
- Cleanup: slimmed, behaviour-focused test suite; removed dead code (no functional change)

## 0.1.3
- self-hosting
- auto-install
- stack lifecycle
- GUI changes

## 0.1.2

- Full containment: managed clones replace linked dev trees (meshcom/meshcore — in-tree venvs built by `lhpc build`); secret and PTY paths move in-root (`config/secrets/xr_pw`, `state/loraham_kiss`); the local adoption fallback is off by default and must be in-root when set; `strategy="link"` is refused at manifest load.
- Hardening & bugfixes: independent per-band daemons (never launches `--radio both`; safe legacy-both teardown), band-isolated topology-truth conflict gating, SIGTERM-only ownership/PID-safe lifecycle under config-stability locking, and identity-bound post-start runners.
- Daemon & stack parameters: per-stack/per-band daemon radio settings (Save/Apply-live/Reset, browser-only FSK warning) and fully component-scoped run/file config so duplicate parameter names never collide.
- Daemon monitoring: live dashboard plus per-band **View Socket** / **RX·TX** viewers (read-only CONF-socket status, RSSI/CAD/stats).
- GUI structure: per-stack collapsible **Settings** replaces the standalone Config page; reworked header/Apps navigation.
- Self-update: coloured footer version/head freshness, a Self-Update page and Apps entry, and a guarded git fast-forward with durable git-anchored config migration to the new defaults.

## 0.1.1 — hardening

Hardening (see `docs/hardening-0.1.md`):

- Descriptor-anchored source transactions, fail-closed session tokens, thin launcher runtime, owned journals; dead-code/docs cleanup; MIT license.

## 0.1.0 — initial version

Terminal CLI and local web console to install, configure and run the LoRaHAM Pi
LoRa stacks (daemon, chat, igate, voice, kiss, meshtastic, meshcom, meshcore).
Adopts and builds each stack's source, starts/stops in dependency order with
per-band radio-conflict gating, writes each app's config, and monitors and
live-tunes the daemon. Bounded read-only status probes; explicit gated mutations;
one-frame TX test on dummy loads. Loopback-only web console (CSRF, CSP).
Validated live on the Raspberry Pi.
