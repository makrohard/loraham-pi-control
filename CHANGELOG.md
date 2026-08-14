# Changelog

## Unreleased

- Stopping a stack now also tears down the dependency stacks it alone was using (stop graywolf → kiss stops → daemon released); a dependency another running stack still needs stays up
- The Start-confirm page also shows the dependency stacks the start pulls up (kiss under graywolf) — fully editable like the target's own: per-start overrides reach the dependency's launch, and Save persists into the dependency's own config
- Audit hardening: stopping a not-running stack never tears down its dependencies; the stop plan discloses the collateral; an override for an already-running dependency warns instead of vanishing; partial saves report exactly what persisted; graywolf's upstream update preserves an operator stop mid-fetch and refuses admission contention cleanly
- The dashboard's system card gains **Reboot / Shut down** buttons (confirm page, graceful via logind): authorized by a polkit rule that bootstrap-deps installs (opt-out `--no-power-controls`) or the dependency panel's copybox adds on existing boxes; buttons stay hidden until then, and a pending power action blocks new builds/updates until it fires

## 0.1.15

- graywolf's 433 TX default is now **433.775** (single-channel, same as stock ESP32 trackers — they never listened on the old 433.900 split, live-found); the RX/TX split stays available by config

## 0.1.14

- Certificate fetch helpers (the `scp` copyboxes) render in **every serving mode** again — they are operator conveniences addressed at the box's live IP, not secret material, so a plain no-auth box can bootstrap cert auth from them; still offered only for active certificates

## 0.1.13

- graywolf gains an **upstream check**: a network probe of its GitHub releases and a one-click **Update** to the latest — the new `.deb` verified against that release's own `checksums.txt`. The default image/auto-install fetch stays on the reviewed, pinned checksum

## 0.1.12

- **Boot-restore honors an explicit operator stop**: a stack stopped before a reboot stays stopped, even when the stop could not verify the process gone (live-found with Voice restarting on every boot); the next `stack start` makes it restorable again. Scoped precisely to a direct whole-stack stop — internal cascades, band switches and component stops never tombstone
- **Certificates panel** gains fetch helpers under where each is created: paste-ready `scp` commands (Linux PC) addressed at the box's own current IP, plus a plain **Download ca.crt** link for browsers and phones (public certificate, no key). Shown only to a trusted session; offered only for active certificates
- Fetched-package stacks (`graywolf`) show their installed version in the row and offer **Uninstall / Clean all**, plus **Update** — naming the new version — when the manifest pin moves

## 0.1.11

- **GPS works out of the box**: the global source defaults to `auto` (a gpsd on this box if one runs, else no position — never a refusal) and every stack's `use_gps` defaults to on. `auto` counts only a listener truly reachable at 127.0.0.1:2947, one start uses one frozen verdict end to end (GPS bridge included), and a gpsd that vanished or streams nothing does not fail the start. Explicit sources keep their fail-closed refusals. **Upgrade note:** an untouched box running a local gpsd begins reporting real position, on the air
- Console start fixes: a stack with GPS on starts from the web again (the GPS-capable set is manifest-derived now, ending the hardcoded-list drift); a stale GPS-feed auto-start tick no longer blocks MeshCom durably; `use_gps` shows on the confirm page as the saved setting it is (change it under Apps → *stack* → Settings)
- One radio, one band — enforced across the chain: starting (or restarting, preflighted before the stop) on the other band is refused while the stack itself or a dependency it pulls in runs on one
- The Stacks page no longer jumps when using the accordion: the clicked header stays put, action returns keep the scroll position, lazy bodies arrive without moving the row
- Fetched-binary stacks (graywolf) get **Uninstall**/**Clean all** buttons whenever their artifact is on disk; meshtastic's 4403/9443 now carry `tcp.port` claims like every other listener
- Changing the global position source is blocked while any GPS-enabled consumer runs (graywolf included); `fixed` says plainly that graywolf gets no position from it
- `meshcore-cli` repinned to `v1.5.0-63-g56b246b`, the last Python-3.11-clean commit, with a build-time byte-compile guard against a regressing future pin
- The runtime root links `docs/` and `README.md` into the self-hosted checkout instead of shipping an empty docs directory

## 0.1.10

- `graywolf` follows the global position source: a `use_gps` switch like the other consumers', with the resolved plan pushed to graywolf's own GPS settings (gpsd host/port, serial NMEA device, or actively off). It needs no bridge component — it speaks gpsd and serial natively
- Fix: the runner's per-stream capture cap (128 KiB, tail-only) had been outgrown by `manifest.example.toml`, so `git show <ref>:manifest` came back beheaded, TOML parsing failed at line 1, and every self-update config-default migration silently stopped completing (self-update then deferred). Raised to 1 MiB, with a test that fails when the manifest next approaches the cap
- New `graywolf` stack: [graywolf](https://github.com/chrissnell/graywolf) as a full replacement for `igate` — same RF<->APRS-IS job through the KISS TNC, plus a web UI and a searchable packet log. The pinned upstream `.deb` is fetched and unpacked into `build/tools/graywolf` (sha256-verified, rootless, no system package and no new bootstrap dependency), and every start re-applies the stack params through graywolf's REST API. `igate` is now marked DEPRECATED — known bugs, unmaintained upstream — but keeps working unchanged
- `loraham-kiss-tnc` repinned to `v0.5.1`: AX.25 bit 7 is position-dependent (the command/response bit on dst/src, has-been-repeated only on path), so a frame from a conforming APRS sender no longer goes on air with a bogus `*` on the destination; CR/LF in a payload become spaces instead of splitting the frame
- `meshcore-cli` repinned to `v1.5.0-68-g3921259` (self-reported v1.6.0) — rxlog/msgs handlers and the aliases mechanism; the `meshcli` entrypoint and every flag this manifest renders (`-t -p -j -D`) are unchanged
- MeshCom firmware repinned to icssw-org release `v4.35p.08.06` (adds the PL country preset; `lora_setchip` and `mheard` fixes) — the QEMU overlay patch is untouched by that range
- LoRaHAM daemon, chat and iGate repinned to `v112-1-g10f4107` — relicensed to plain GPLv3 with the original author's permission, so the extra non-commercial and reporting conditions are gone and the repo now ships the licence text. Licence headers, READMEs and the iGate startup banner only; no functional change
- Smoke-test fixes: MeshCore Node Manager starts under the read-only-home sandbox (its state moves to `state/meshcore-nm`; an old `~/.meshcore_nm` is left behind), the Chat command block renders again, licensed running stacks report TX enabled, Save on the start-confirm page no longer starts the stack, and exposure pills judge the live listener — its real port, and the policy nginx actually applied — against the verified firewall (a remotely exposed console reads red until one `webserver apply` records that policy)
- Dashboard endpoint pills show a working address for the current viewer, never a dead link
- Sideband is no longer installed on headless systems. It is a graphical Kivy app, but because Kivy
  vendors its own SDL2 it declared no graphical package to gate on, so only `python3-dev` kept it out
  of a headless install — and any box installing that for other reasons got Sideband anyway. It now
  also gates on `libx11-dev`, the marker that `bootstrap-deps.sh --with-gui` was run. The `--with-gui`
  package closure is unchanged (`libgtk-3-dev` already pulled `libx11-dev`).
  **Upgrading a box that already has Sideband:** it stays on disk and still reports `stopped`, but can
  no longer be started or rebuilt. Remove `<runtime>/src/sideband` and `<runtime>/state/sideband` to
  reclaim the space, or run `bootstrap-deps.sh --with-gui` if that machine really does have a display.

## 0.1.9

- MeshCom firmware now tracks canonical upstream (icssw-org) at release `v4.35p.08.03` — the external-radio backend merged upstream (PR #1072), retiring the fork pin; QEMU overlay + build surface unchanged

## 0.1.8

- **One global position source** (`lhpc gps`): gpsd local or remote, a receiver read directly, or a fixed position — shared by Meshtastic, MeshCom and Sideband, with a per-stack on/off switch, an exclusive claim on the receiver, and readiness that follows the source rather than the endpoint
- Fixes: an unrelated `socat` is no longer claimed as the KISS serial bridge; `lhpc doctor` reports a gpsd that answers but owns no receiver; the binary-switch tests no longer read the host's process list
- **Time** row in the System panel: local time, UTC, timezone and a sync-state pin — report-only, LHPC never sets or disciplines the clock

## 0.1.7

- Reticulum (RNS) stack: a node that drives the LoRa radio **directly over SPI** — no rnoded, no RNode firmware, no KISS. Owns its band exclusively, shares the SPI bus with the daemon through `spi0.lock`, and refuses to run without a verified radio
- Driver in its own pinned repo ([loraham-rns-interface](https://github.com/makrohard/loraham-rns-interface)): SX127x proven on air on two boards, SX1262 proven on air on 868 (untested on 433); pins/chip/TCXO/PA come from the selected hardware setup, not free-form config
- Restart-safe duty-cycle accounting (reserved before TX, persisted), per-band legal defaults (868: 25 mW/1 %, 433: 10 mW/10 % on a clear 434.500 MHz)
- Generated configs gain a declared file mode, and a secret may be sourced only from `config/secrets.toml` — never from `local.toml`, a default or a band default
- Nested-INI config generation (`ini-update`) with ConfigObj-safe quoting

## 0.1.6

- Binary install channel: prebuilt, smoke-gated artifacts for daemon/meshtastic/meshcom — minutes instead of hours, and the default where published (`--source binary`); pins must match the manifest, switching back to source is non-destructive
- Managed firewall: nftables default-deny you apply with one sudo command, with per-listener choices, an access-point mode (DHCP/DNS on the AP interface) and three honest status dimensions — policy is now settable from the CLI too (`lhpc firewall --mode/--ap/--ssh-ports/--allow-endpoints/--recommended`), so a headless box needs no console
- System monitor
- Boot auto-restore: stacks that were running come back after a reboot, through the normal start path
- Field-validated from zero on a Pi Zero 2W: binary install → mTLS console → stack proxies → own access point with a phone client certificate
- Test hygiene

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
