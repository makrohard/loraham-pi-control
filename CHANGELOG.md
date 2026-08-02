# Changelog

## 0.1.8

- **One global position source** (`lhpc gps`) shared by Meshtastic, MeshCom and Sideband: a gpsd on this box or another, a receiver read directly, or a fixed position. Per-stack settings only turn GPS on or off — Sideband's old independent source selector no longer applies, and any values still saved are reported as ignored
- Meshtastic gains GPS for the first time: lhpc presents the source as the serial device meshtasticd requires, applies `position.gps_mode` in **both** directions, and uses the node's native fixed-position support (no synthesized stream, no chip-probe delay)
- MeshCom production GPS comes from the same mechanism; the fixture relay is now explicitly a test facility and never joins a normal start
- Direct-device use is refused when gpsd already owns the receiver — identity resolved through the real character device, so `/dev/ttyACM0` and `/dev/serial/by-id/…` are recognised as one
- Malformed `[gps]` fails closed to `off`, and stacks that would use GPS refuse to start rather than run silently blind
- `gpsd` is an optional convenience, surfaced only when the configured server is local; lhpc never configures gpsd itself (see [docs/gps.md](docs/gps.md))
- Per-stack `use_gps` switch (default **off**) so setting a source does not silently start every stack beaconing; a stack with GPS on and the source off refuses to start and names both settings. Stored band-lessly (like autostart) so it survives a band change, and refused while the stack runs — its feed, claims and generated config came from the current setting
- A saved source that cannot be resolved — an `nmea` device that is missing or is not a character device — refuses the start and is named as **UNUSABLE** by `lhpc gps` and the console, instead of reporting a healthy `source: nmea` while every stack ran position-blind
- Reaching a source is not having one: opening the device, or completing gpsd's TCP handshake, no longer admits a start — an empty gpsd that owns no receiver, or one sending only non-navigation traffic, keeps the feed pre-admission instead of bringing the stack up position-blind
- A feed reads **live only when position is flowing**: a sentence must carry a valid fix, so a receiver that is merely talking — satellites-in-view, a GGA with fix quality `0`, or the lone `$GPTXT` of a u-blox left in UBX binary mode — reports "reachable, waiting for a fix" instead of "position source live" (found on hardware)
- Feed health follows the **upstream source**, not the endpoint: an unreachable source fails the start and is cleaned up, later loss reads **DEGRADED**, and recovery returns to running with no restart
- Readiness left behind by a previous run cannot approve a new start: a marker that is not being refreshed, or that does not name a live feed process, reads **DEGRADED** instead of `ready`
- Component targets behave like their stack: `lhpc config meshcom-qemu use_gps on` sets the one stack switch, a direct consumer start brings the feed its stack's plan calls for, and starting a feed by itself is refused unless that plan uses it. GPS applies to what a start actually brings up rather than to stack membership, so the MeshCom bridge and firmware, the fixture relay, and a Reticulum start without Sideband bring up no feed, claim no receiver, and are not gated on GPS settings they never read
- GPS liveness is one strict, fresh check shared by both configuration transactions: RUNNING, DEGRADED **and UNKNOWN** on a GPS-enabled stack's position readers or its feed all block a source or `use_gps` change, so an unanswerable probe can no longer pull the receiver out from under a possibly-live reader; a stack running with GPS off blocks nothing
- The switch is stored canonically: a value that is not `on` clears the key rather than saving a third state, so an empty value can no longer disable GPS under a running stack without the refusal firing
- The receiver is an exclusive claim keyed on the real device (`st_rdev`), so `/dev/ttyACM0` and its `by-id` alias cannot be taken twice, and a running stack blocks another from the same receiver
- `--with-gps` installs gpsd from bootstrap when the source is a local gpsd
- A receiver left in **UBX binary** mode by gpsd is diagnosed by name instead of silently delivering nothing
- Fixed: `lhpc install --source binary` self-contended on its own source guard, making binary install impossible for any stack with `clone_required` (MeshCom)

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
