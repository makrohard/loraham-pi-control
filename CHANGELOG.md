# Changelog

## 0.2.9

- **Start means start:** a web Start or Restart runs exactly the saved configuration — the start-confirm page with its Stack-parameters panel, per-band daemon panels, identity fields, optional-component checkboxes and Save / Save-and-start is gone; Settings is the only place configuration changes (the CLI always worked this way). A routine Start/Restart runs at once and returns where you came from; only a consequential choice still asks — another running stack owns the radio, or a restart would stop running dependents (the restart plan now carries the stop's collateral). A start refused over a missing or unusable identity lands on the stack's Settings with the offending row highlighted, and the refusal is known at plan time: the CLI's dry run and the web click share one verdict, re-checked under the locks before anything is mutated. A licensed stack whose local callsign is empty still launches with the global one (materialized for the launch, never saved). Optional components follow one rule (`hidden` / `listed` / `tickable`) on the Settings card and in the run order. A stack row shows a waiting indicator while its body loads. The daemon's `debug` launch flag, settable only on the retired page, is gone

## 0.2.8

- **MeshCore repeater:** the meshcore stack (now named *MeshCore (OpenHop)*) gains a `mode` setting — `chat` (today's companion node, the default), `chat+repeater` (upstream [openHop repeater](https://github.com/openhop-dev/openhop_repeater) hosting the same companion inside it) or `repeater` (repeater only). One openhop process on the radio in every mode, the same `python -m meshcore_host` command, the same chat rows; the repeater gets its own node name, its own identity key and a dashboard on `127.0.0.1:8000` whose admin password LHPC mints. Web UI and CLI keep working in `chat+repeater`; in `repeater` they refuse to start (no companion). Status, start readiness, identity enforcement and minting (the companion's key exists only where the companion runs, the repeater's only in the repeater modes), the optional clients and the box's position (a repeater-only start runs no `meshcore-gps` feed, claims no receiver and is never refused over a GPS setting) all follow that one mode decision; a change is restart-required and cannot be overridden for a single start. The mode is switched at the top of the stack's page (the same saved setting as Settings → Repeater), shown as a pill on the Apps row and by `lhpc status meshcore`; the start-confirm panel shows the Settings card's group headings
- **A proxied web page per component:** a stack may carry several web UIs, each its own page in the Webserver panels, `lhpc webserver proxy <page>`, nginx block and suggested port. A stack's first web component keeps the stack id as its page id, so every saved proxy setting stays valid; a further one is `<stack>-<component>` (`meshcore-meshcore-node` = the repeater dashboard, whose upstream is served in the repeater modes — the page and its proxy exist in every mode, like every stack's page exists while the stack is stopped; `meshcore` = the MeshCore Web UI). Page ids are collision-checked at manifest load, including their nginx spellings
- **Proxy deny lists are spelling-tolerant:** a denied path now refuses its punctuation variants, trailing slash and sub-paths too (CherryPy folds punctuation to `_` and binds sub-path segments to handler arguments). The repeater dashboard's list is source-derived from the pinned upstream: every route that changes its configuration, identities, radio or version is refused at LHPC's perimeter, read pages and operational actions pass
- Leftovers from 0.2.6: the MeshCore CLI (a REPL run on demand) is listed as such on the Settings card and the confirm page instead of getting an auto-start tick a saved tick of which seeded the REPL into the stack's start; `lhpc firewall` names the deferred Webserver apply, README no longer claims MeshCore has no web UI, the manifest's dependency note no longer mentions the retired Node Manager
- Fixes folded into this release after the first tag: the Stacks list no longer shows a fetched-package or binary version twice (the version cell carries it, update/upstream notices moved to the wrapping cell); denied proxy paths answer `404` instead of `403`, because the openHop dashboard treats a `403` on any call as an expired session and logged the operator out on its first denied request
- **Upgrading — one action required:** the MeshCore stack's build now also consumes the pinned repeater checkout, so after the update its node reads *not built* and **will not start — not by hand, not by boot restore — until you run `lhpc install meshcore` (adopts the new source, leaves the existing checkouts untouched) and `lhpc build meshcore` once**, in `chat` mode too. Nothing else changes until `mode` is changed. Unrelated but worth knowing: `lhpc update meshcore` refuses any built MeshCore checkout ("local modifications present") because the LHPC patch counts as a local change; the pins did not move between 0.2.7 and 0.2.8, so no update is needed for this release

## 0.2.7

- AP fallback: the 10-minute retry of the preferred Wi-Fi no longer takes the AP down while a client is connected to it (`iw` station table; a missing `iw` defers the automatic retry, the console's Retry still works), and a disarmed `lhpc-ap` profile is re-armed by the network watchdog. `iw` joins the default bootstrap
- A webserver Apply that the firewall gate refused is remembered and completes automatically once the firewall is verified — no second click after the sudo step. The Firewall panel shows it while it is owed, the dashboard "(pending Apply)" badge links to the Webserver panel. Only the policy that was deferred is activated; a later webserver or proxy edit needs its own Apply

## 0.2.6

- **Coherent identities:** an optional global base operator callsign (its own card on the Stacks page) that licensed stacks (chat, iGate, Voice, Graywolf, MeshCom) inherit while their local callsign is empty; a per-stack value overrides it and carries that stack's SSID or portable form. Per-protocol validation throughout (APRS SSID `-1`…`-15`, MeshCom `-1`…`-99`, Voice ≤11 characters, Meshtastic/MeshCore node names). Meshtastic and MeshCore never inherit a callsign — their node names are their own
- A start, restart or post-start without a resolvable identity is refused before anything changes: the web marks the missing fields, the CLI prints the command for each. An identity entered on the Start/Restart panel is saved as configuration first, so launch and store always agree; changing the global flags running inherited stacks restart-required
- `lhpc config` and Settings can clear an identity (the stack then cannot start until one is set), and a long required post-start — MeshCom waits minutes for its guest console — no longer blocks them: the launch keeps its frozen configuration, the save is accepted, and the running stack is flagged restart-required
- **Upgrading:** stacks that relied on an implicit identity refuse to start until one is set, and a reboot will not bring them back on its own. A global callsign carrying an SSID is no longer inheritable (set the bare base call once; per-stack SSIDs stay local); Meshtastic's `node_name`/`node_short` and MeshCore's `name` no longer default to a generic value; a stored per-stack `call` with an explicit `-0` or a base longer than 6 characters is refused. `lhpc status` names every stack that needs one and prints the command
- **Upgrading, one-time:** the first self-update from 0.2.5 runs its *own* migration code, so a per-stack callsign that had become equal to the global is cleared by it — the stack then inherits the same value, so nothing changes on air, but the pin is gone. Re-set it with `lhpc config <stack> <field> <value>` if you meant it as a pin; later updates keep such values

## 0.2.5

- **Stacks WebGUIs:** one Webserver subpanel applies a common proxy policy (access, scheme, auth, CIDRs) to every eligible stack web UI at once — ports stay per-stack (existing kept, missing get the normal suggested default), all-or-nothing validation, one atomic save, one nginx apply; the console's own settings live under "LHPC WebGUI"
- auto-install no longer blocks a stack over its gui_optional GUI component's missing toolkit (v0.2.4 Lite image build failure)

## 0.2.4

- **Voice on headless/Lite boxes:** the same source built with `-DNO_GTK` as `loraham-voice-cli`, a pure ncurses TUI with zero graphical linkage — `lhpc stack start voice` prints the exact terminal command; codec2/ALSA moved into the standard bootstrap, GTK stays behind `--with-gui`
- The GTK app is `gui_optional`: absent toolkit/display drops it from build/start/auto-install and status instead of failing the stack; on a desktop it runs exactly as before and the terminal variant is not offered
- The terminal variant is a guarded fallback: direct start/restart refused (its config — incl. the callsign — belongs to the GTK component), exclusive audio enforced, offered only where the GUI cannot run; plan/preview and no-op results tell the same truth
- Interactive components run their pre-start steps, so the printed command actually works (live-found ENOENT)
- `bootstrap-deps.sh --dry-run` no longer rejects its own `libasound2-dev` (ALSA is not an audio server; PulseAudio stays denied)

## 0.2.3

- **`lhpc meshtastic <args>` — a guarded passthrough to the managed Meshtastic CLI:** runs the pinned upstream CLI against this box's local node; everything forwards unchanged except what LHPC owns. Transport/address selectors are refused (always the local node), LHPC-owned settings (LoRa region, owner name/short incl. `--set-ham`, GPS mode, fixed position) point at the matching `lhpc` command, and factory-reset asks to confirm (`--yes` skips). A broad config import (`--configure`/`--import-config`/`--seturl`/`--ch-*-url`) runs, then LHPC auto-reasserts region/name/GPS via post-start convergence — verified, so a drift never reports success. A remote `--dest` is unrestricted; `--help`/`--version`/`--support`/`--test` need no running stack. The dashboard hints at the wrapped CLI when the stack starts
- MeshCore RF presets: `txmaxpower` ceiling raised 14 → 20 dBm (the RF95's PA limit, within the 869.4–869.65 MHz 500 mW allowance). The default `txpower` stays 14 — raising it is an operator opt-in via `lhpc config meshcore txpower`
- New hardware profile **`uputronics-x`** — Uputronics dual HAT with crossed modules (CE0 = 868, CE1 = 433). For units whose modules are mounted opposite to the standard `uputronics` profile; the symptom is both bands deaf or marginal with correctly connected antennas, since each radio drives the other band's module
- **MeshCore now runs on openHop Core:** the node is upstream [openHop Core](https://github.com/openhop-dev/openhop_core) on the LoRaHAM daemon (a reviewable patch, never a fork) with an LHPC host adapter, replacing the retired `meshcore-pi` fork
- **Browser GUI replaces the Tk Node Manager:** `meshcore-webui` reached through the LHPC TLS/PKI proxy — no graphical dependency, runs headless; config/log links and a live noise-floor panel now populate
- **The one Companion slot is shared safely:** running `meshcore-cli` and the WebUI together shows an active conflict (Apps banner + card), and the WebUI yields the radio to a running CLI (lock handoff) instead of fighting for it
- LHPC keeps its ownership boundary: the proxy denies factory-reset/radio/tx/tuning/position/name and the WebUI refuses to change GPS advert-location policy; the node name and GPS position are configuration-owned and never resurrected from persisted state, and an unrestorable persisted channel now fails startup closed instead of being silently dropped

## 0.2.2

- **MeshCore identity is LHPC-owned:** the node's private key lives in `config/secrets/meshcore_identity.key` (0600) and is adopted, never re-minted on config regeneration; the generated `meshcore-pi.toml` that carried it is 0600
- **MeshCore position follows the box:** the global GPS source (`use_gps`, default on) feeds the node continuously through a `meshcore-gps` bridge instead of freezing at start; `fixed` still writes static coordinates
- meshcore-pi repinned `640978e`: the companion port no longer drops an idle client every ~90 s, current v1 routing/path encoding, a malformed packet or hostile trace can no longer take the node down; daemon defaults corrected to `POWER=14`/`PREAMBLE=16`
- All external software repinned to current upstream (graywolf 0.14.13, meshcore-cli v1.6.3, RadioLib 7.7.1-57, meshtastic v2.7.26-32, Reticulum 1.5.1, Sideband 2.1.0, meshcom-qemu 54c3ec3, MeshCom firmware v4.35p.08.29) — several upstream tags/history had moved and broke a fresh build
- An absent optional component no longer fails the whole stack, and `lhpc build` on an uninstalled component says so instead of failing with rc 127
- Validated on hardware (identity across restart, live GPS, stable Companion, a real advert on 868); peer-to-peer RF is covered by tests against the current wire format only, not field-validated

## 0.2.1

- **"Back to AP mode" no longer refused:** re-activating the box's own shared AP needs the NetworkManager `wifi.share.open`/`.protected` polkit actions the network rule omitted, so it (and a failed join's AP fallback) failed with "Not authorized to share connections via wifi" — stranding the box when the AP was its only way home. The rule now grants them and the auth preflight checks them; re-run the copybox or `bootstrap-deps.sh` on existing boxes

## 0.2.0

- **Interactive in-browser demo** (GitHub Pages): the real console compiled to WebAssembly with Pyodide, driven against a pure in-browser simulation backend — browse the dashboard/Apps and install → build → start → stop any stack with one-stack-per-band handoff and a live radio panel, no Pi, no server, no sign-in. Badge in the README; see `demo/`
- **Codespaces test lab** (`lhpc-testlab`): the real console + CLI + real stack processes (kiss, graywolf, meshcore, meshcom, …) against deterministic fake hardware/OS backends, one click in a GitHub Codespace — fault scenarios, RX/TX injection, simulated reboot, and a coverage-matrix gate. Ships nothing in the lhpc wheel or the Pi image. Badge in the README; see `docs/testlab.md`
- Generic extension point behind both: `ControllerService` honors `$LHPC_SYSTEM_PROVIDER` (`module:factory`) to supply an alternate System/manifest/spawn for out-of-tree simulation harnesses; unset (production, always) it is byte-identical to before
- `{multiarch}` token in manifest `check_file` paths (libslirp) — resolves to the aarch64 literal on the Pi (unchanged), truthful on x86

## 0.1.17

- New **Network** panel (AP-managed boxes only): join an existing Wi-Fi from the console; the box's own AP stays the automatic fallback, and a **preferred** network is re-joined whenever it reappears. Console follows onto the joined network (cert + nginx allowlist stay the gate); expired-CRL self-heal; second polkit rule via bootstrap (opt-out `--no-network-controls`)
- Power buttons now show on a correctly authorized box: visibility asks logind directly (per-action, cached)
- The Reboot confirm page warns that the AP vanishes for a minute or two mid-reboot

## 0.1.16

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
