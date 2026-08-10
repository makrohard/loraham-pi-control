# Changelog

## Unreleased

- **GPS works out of the box**: the source defaults to `auto` (gpsd on localhost if one runs, else no position — never a refusal) and every stack's `use_gps` defaults to on. **Upgrade note:** an untouched box that runs a local gpsd starts reporting real position, on the air, after this update
- Audit follow-ups: a running GPS-enabled graywolf now blocks a source change; `fixed` says plainly that graywolf gets no position from it; meshcore-cli's Python-3.12-only syntax now fails at build time, not first run
- Audit closure: `auto` only counts a listener truly reachable at 127.0.0.1:2947; one start uses one frozen auto verdict end to end (bridge included); meshcore-cli repinned to the last Python-3.11-clean commit

- Review follow-up on the fixes below — each had left the same hole in an adjacent path:
  - The GPS **consumer** set was a second hardcoded list (one function below the fixed one) still missing graywolf, so the GPS admission gate was skipped for it: with `use_gps` on and the source off, graywolf started silently position-blind where meshtastic was refused. Both sets are now manifest-derived; components that read a position declare `reads_position` (the `use_gps` param cannot say who reads — reticulum declares it on `rns`, Sideband is the reader). Both sets are also cached — they sit on hot paths and were rescanned per call
  - A stale saved auto-start tick for the **test fixture** was still honored by the run order (the stale-tick fix covered production feeds only), silently replaying a synthetic position on every MeshCom start with no UI left to show or clear it. Feeds and fixtures now share one predicate; the fixture runs only when named directly
  - The cross-band dependency rule keyed on an *explicitly requested* band — but the CLI has no band flag, so a bandless start resolved to the declared primary and sailed past the rule into the exact state it refuses. It now judges the resolved band
  - The same rule now also runs in **restart's preflight**: an applied restart used to stop the stack and only then have its start refused, degrading restart to stop-only and leaving the stack down
  - The fixture's knobs (rate/loop) had resurfaced as editable inputs under "Daemon process options" on the MeshCom confirm page after being filtered from the parameters panel; they no longer render anywhere on a stack confirm (a direct fixture start keeps them)
  - The Package **Uninstall/Clean** buttons were gated on "fully built", hiding them for an interrupted fetch or stale pin — exactly the states where removal is the fix. They now gate on anything removable being on disk
  - The GPS card's consumer list and the band-rule's snapshot use are derived/shared rather than hand-written/per-dependency

- Fix: a stack whose `use_gps` was on could not be started from the console at all. The GPS-capable set was a hardcoded list that did not name `graywolf`, so its saved switch read as off, the start form's echo of the saved value looked like a per-start change, and the start was refused as "use_gps cannot be changed for a single start". The set is now derived from the manifest — declaring the param IS being GPS-capable
- Fix: **MeshCom would not start** once the GPS-feed checkbox on the Confirm:start page had ever been ticked. That saved `autostart_meshcom-gps = on`, which forced the feed into the run order while `use_gps` was off, and a feed the position plan does not want is refused — so one stale tick stopped the stack durably. A position feed is admitted from the resolved plan only, and is no longer offered as an auto-start choice (nor is the synthetic test fixture); the confirm page showed both beside the real switch as a second and third GPS control
- `use_gps` is shown on the Confirm:start page as a saved setting with no input field, instead of an editable control whose only possible effect was a failed start
- Fix: starting a stack on the other band while a stack it DEPENDS on holds one is refused. The "one band at a time" rule covered only the target's own stack, so graywolf — which owns no radio and pulls in the KISS TNC and the daemon — was admitted on 868 while that chain ran on 433, coming up on 868 talking to a 433 TNC
- Fix: a stack whose artifact is fetched rather than cloned (`graywolf`) now has **Uninstall** and **Clean all** buttons. Both hung off having a source repo, so it could be installed from the console but only removed from a shell

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
