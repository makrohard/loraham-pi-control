# Changelog

## 0.3.0

- **The `igate` stack is removed;** Graywolf replaces it (same RF↔APRS-IS job through the KISS TNC, plus a web UI). Reflash an old development image, or `lhpc clean igate --purge --yes` before an in-place update.
- **The source `strategy` field is removed** — `link` and the `linked` source state with it. Every managed source is a clone under the runtime root; a symlink where one is expected is refused (generic containment), and a manifest that declares `source.strategy` is now refused at load instead of accepting a key that means nothing. Records and journals written by 0.2.10 still carry the field and are read as an ignored extra.
- **Stale read tolerances removed:** ownership records need schema v1; source-registry v1 records, the `legacy` selector, journals without `had_prior`, the self-update cache without `schema_version`/`status`, `radio.hardware = "legacy"`, the band-less daemon log fallback and the MeshCore host `node_name` purge are gone. Old development state reads as invalid or is ignored; reflash or clean before an in-place update.
- **Old MeshCore identity rescue removed:** only `config/secrets/meshcore_identity.key` and the generated config's `[identity] key` are consulted.
- Unused functions and version-numbered history wording removed; the frozen unit test is named without a version.
- **Docs consolidated** (29 → 26 files): one home per fact, no history; `docs/live-test.md` holds the dated evidence (the 0.2.10 release test, the silicon test); every doc carries a test-enforced Contents block; the hardware statement is corrected (Waveshare SX1262 433M tested on the air, 868M not tested on silicon).

## 0.2.10

- Reticulum 1.5.2; MeshCom firmware at the `dev` tip 674413c (QEMU overlay rebased, GPS UART drain bounded under QEMU); Meshtastic follows the stable tag v2.7.26 (binary republished).
- Known-working works on headless boxes: an optional GUI sidecar never adopted on a Lite install no longer blocks the composition.
- The release test matrix (`docs/test-matrix.md`) is the leading pre-release live test.

## 0.2.9

- Start means start: a web Start or Restart runs the saved configuration; Settings is the only place configuration changes; only a resource conflict or a dependent to stop gets a confirmation.
- Detached web Start/Restart as a tracked job with the task banner; identity refusals redirect to the Settings row.
- The stack page's Password section shows the stored password with a copy button; MeshCom's HMAC password included.
- The managed Meshtastic CLI is listed on the Dashboard as an on-demand component.
- Faster pages: every piece of evidence is read once per request.

## 0.2.8

- MeshCore repeater: the meshcore stack (*MeshCore (OpenHop)*) gains a `mode` setting — `chat`, `chat+repeater`, `repeater` — hosting the upstream openHop repeater beside the companion node.
- One proxied web page per component; proxy deny lists tolerate spelling variants; denied paths answer 404.
- The MeshCore build consumes the pinned repeater checkout: after the update run `lhpc install meshcore` once.

## 0.2.7

- AP fallback: the 10-minute retry of the preferred Wi-Fi no longer takes the AP down while a client is connected to it (`iw` station table; a missing `iw` defers the automatic retry, the console's Retry still works), and a disarmed `lhpc-ap` profile is re-armed by the network watchdog. `iw` joins the default bootstrap
- A webserver Apply that the firewall gate refused is remembered and completes automatically once the firewall is verified — no second click after the sudo step. The Firewall panel shows it while it is owed, the dashboard "(pending Apply)" badge links to the Webserver panel. Only the policy that was deferred is activated; a later webserver or proxy edit needs its own Apply

## 0.2.6

- Coherent identities: an optional global base operator callsign that licensed stacks inherit while their local callsign is empty; Meshtastic and MeshCore node identities never inherit; a start without a resolvable identity is refused before anything changes.
- A global callsign carrying an SSID is not inheritable; per-stack SSIDs stay local.

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

- `lhpc meshtastic <args>`: a guarded passthrough to the managed Meshtastic CLI against the local node.
- MeshCore runs on openHop Core (a reviewable patch on the LoRaHAM daemon, never a fork) with a browser GUI through the LHPC TLS/PKI proxy — replacing the retired fork and its Tk Node Manager; the one Companion slot is shared safely between the CLI and the WebUI.
- New hardware profile `uputronics-x` (crossed modules); MeshCore `txmaxpower` ceiling 20 dBm, default `txpower` 14.

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

- GPS works out of the box: the global source defaults to `auto`, every stack's `use_gps` defaults to on; a source change is blocked while a GPS consumer runs.
- One radio, one band, enforced across the chain; console start fixes; fetched-binary stacks get Uninstall/Clean; `meshcore-cli` repinned.

## 0.1.10

- New `graywolf` stack replaces `igate`: the same RF↔APRS-IS job through the KISS TNC plus a web UI and a searchable packet log; it follows the global position source.
- LoRaHAM daemon, chat and iGate repinned to `v112-1-g10f4107` (relicensed to plain GPLv3); `loraham-kiss-tnc` v0.5.1 (AX.25 command/response bit fix); `meshcore-cli` and MeshCom firmware repinned.
- Sideband is no longer installed on headless systems (it gates on the `--with-gui` marker).

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

Hardening:

- Descriptor-anchored source transactions, fail-closed session tokens, thin launcher runtime, owned journals; dead-code/docs cleanup; MIT license.

## 0.1.0 — initial version

Terminal CLI and local web console to install, configure and run the LoRaHAM Pi
LoRa stacks (daemon, chat, igate, voice, kiss, meshtastic, meshcom, meshcore).
Adopts and builds each stack's source, starts/stops in dependency order with
per-band radio-conflict gating, writes each app's config, and monitors and
live-tunes the daemon. Bounded read-only status probes; explicit gated mutations;
one-frame TX test on dummy loads. Loopback-only web console (CSRF, CSP).
Validated live on the Raspberry Pi.
