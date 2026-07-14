# Changelog

## unreleased
- Live-test run 2026-07-14: known-good pins refreshed to today's proven set (daemon, radiolib, meshcom-qemu/gps, meshcore-cli); docs: secrets layout, expose-runbook rebind note; test evidence in docs/live-test-2026-07-14.md

## 0.1.5
- Dependency overview + Checks
- daemon params per stack and frequency
- consistent webserver controls
- GUI: Add dark mode, polish styles
- hardening and refactor services.py
- Start gate predicts the spawned ch
- MeshCom HMAC Auth + Task indicators
- Remove unused Locator-foo
- Audit + Test

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
