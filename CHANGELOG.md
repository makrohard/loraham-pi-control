# Changelog

## 0.9.3

- `lhpc stack start chat` prints the command to run chat in a terminal, as voice's terminal
  variant does; before it only pointed at the dashboard card, which an SSH operator does not have
  (F-C1). The printed command now stands on a line of its own for both, with the note below it:
  voice's note was appended to the command, so pasting the line as printed was a shell syntax
  error (F-C2).

## 0.9.2

- **MeshCom firmware: the QEMU build now fetches the pin (R8).** The meshcom-qemu setup step
  reads `{pin:src/MeshCom-Firmware}`, a new build-step token the manifest parser resolves to the
  `meshcom-firmware` component's `pin_commit`; from 0.2.10 to 0.9.1 the step carried a hardcoded
  674413c while the pin moved to 80b85a5, so every published meshcom artifact was labelled with a
  commit it did not contain. A repo test now refuses a literal commit in any build step, an
  unknown `{pin:…}` path is a manifest error, and the binary packer (`lhpc-binaries`) refuses to
  pack a firmware checkout that is not the pin. The QEMU overlay was rebased onto the pin
  (meshcom-qemu-raspi: b322a88 -> 74a3a081f), so the meshcom binary is rebuilt for this release.
  **The MeshCom firmware itself changes with it**: nodes move from upstream 674413c (v4.35p.08.29
  era) to the pinned 80b85a5 (v4.35t.09.20), several upstream releases at once — the first real
  firmware change since 0.2.10. Proof: `docs/live-tests/live-test.md`, section "0.9.2 patch proof" (binary
  install through the pinned-clone gate, node boot on the new firmware, T-Deck exchange both ways).
- `lhpc stack restart <stack>` brings back the optional components that were running (MeshChat
  beside rns, an optional client started by name): before, a restart raised only the run order
  and left them stopped (F-R3, `docs/live-tests/reticulum-rnode-test-2026-09-24.md`). The plan
  lists them as `[optional] … restarted with the stack`.
- meshcom-qemu-raspi: b322a8895 -> 74a3a081f, used by meshcom-qemu (overlay rebased onto the
  firmware pin; no other pin moved).

## 0.9.1

- Reticulum: the LoRa interface's short-term airtime guard defaults to **33 %** of its 15 s window
  on both bands (was 5 % on 868, 10 % on 433). At 5 % one 196-byte frame at SF8 filled the window,
  so any multi-frame reply the box had to send — an `rncp` file, a large LXMF, an attachment from
  MeshChat — was paced to one frame per 15 s and the sender timed out, while receiving worked
  (live test against a real RNode: `docs/live-tests/reticulum-rnode-test-2026-09-24.md`). 33 is
  Reticulum's own example for its RNode interface. The long-term limit (the legal duty cycle,
  1 % on 868 / 10 % on 433) is unchanged, and so is the operator's ability to set either value.
- Pins unchanged since 0.8.3; no binary is rebuilt. Image v0.9.1 is built so the two version
  lines stay equal.
- Correction to the 0.9.0 note on MeshCom-Firmware: the QEMU headless build does not check the
  firmware pin out at all — `scripts/setup.sh --ref 674413c…` in the meshcom-qemu build step is a
  hardcoded commit (since 0.2.10), and the published meshcom artifact is labelled with the pin
  while containing that commit plus the overlay. So the 0.8.3 pin move changed a label, not the
  bytes, and every meshcom artifact since 0.2.10 carries the same firmware. The release bot holds
  `src/MeshCom-Firmware` until the build follows the pin and the overlay is rebased (open item R8).

## 0.9.0

- **MeshCore repeater: the openHop plugin manager runs beside the repeater.** The dashboard's
  *Plugins* page needs upstream's separate manager process, which natively is a root systemd unit;
  the node's host now spawns it in the repeater roles (new Repeater setting `plugins`, default
  `on` — every repeater box starts it on its next stack start) and stops it with the stack. Plugins
  are third-party wheels from upstream's catalogue, installed at the operator's click, outside
  lhpc's pinned closure and unsandboxed: the boundary is written in `docs/stacks/meshcore.md`.
  The manager is not restarted after a crash; an unclean manager or node death leaves a
  same-boot marker that refuses a replacement manager and `build`/`update`/`uninstall`/`clean`
  of the stack (and the controller uninstall) until a reboot — a duplicate plugin tree is never created.
- Components can declare a `stop_timeout` (0–600 s) for the lifecycle's cessation wait; the
  MeshCore node uses 40 s (its host stops the manager, the GPS feed and the radio, each bounded)
  and every other component keeps the 5 s default. Upstream's 5 s exit watchdog is armed only
  after that bounded cleanup.
- Live proof on the reference box (both repeater roles, the crash/orphan/reboot semantics of the
  marker, the maintenance refusals, and the dashboard's *Plugins* page through the proxy):
  `docs/live-tests/meshcore-plugins-test-2026-09-22.md`. Release matrix (fast lane by the
  maintainer's standing waiver — no heavy compile on the box): `docs/live-tests/live-test.md`.
- Pins unchanged since 0.8.3, so the binaries published there satisfy this manifest. Upstream
  MeshCom-Firmware has moved past the pin (`dc1a012c`, KISS mode v2), but the QEMU headless
  overlay patch no longer applies there; the pin is held until the overlay is maintained.

## 0.8.3

- MeshCom-Firmware: 6edc74997 -> 80b85a5a2 (v4.35t.09.20), used by meshcom-firmware
- openhop-core: c95a68445 -> cedb26b4b (v1.0.10-423-gcedb26b), used by meshcore-node
- meshcore-cli: 568d158bc -> d4eac61bd (v1.6.4), used by meshcore-cli
- openhop-repeater: 9e375da77 -> 277f11c3f (1.1.4-43-g277f11c), used by openhop-repeater-src

## 0.8.2

- The release bot can release again. Its policy had no rule for `src/meshchat`, so every scheduled
  run since 2026-09-14 refused at its first stage — correctly, and reported only in its own
  repository. The rule is in the bot; here, `pin-validation` now runs the bot's own check against
  the checked-out manifest on every push, so a stack that pins a new source cannot merge without
  its policy rule.
- A test asserted meshcore-cli's exact pinned commit and turned the bot's first candidate red with
  every moved stack otherwise proven. It now asserts a lower bound (never below v1.6.3, the first
  3.11-clean release), which is the contract it was written for. Nothing on a box changes; no image.

## 0.8.1

- Chat is an ordinary pinned source. Its `artifact` flag made every install take the LoRaHAM daemon
  repository's branch tip instead of the manifest pin; the day after 0.8.0 that tip was one
  documentation commit past the pin, the image build installed it, and the image's composition
  check refused the result. Chat now installs and verifies the pinned commit like every other
  source (the same change Voice had in 0.3.10). No shipped component carries the flag any more.

## 0.8.0

- **GPS Monitor.** Under *Position (GPS)* the console gains **Monitor** (first) and **Settings** (the
  form as it was). The Monitor shows the receiver's fix state, coordinates, altitude with its datum,
  the receiver's reported time, satellites used of seen, a Skyview and an NMEA-stream pane, polling
  only while open. `lhpc gps --monitor [--sats]` prints the same snapshot, read-only.
- gpsd sources are read on demand by a disposable, bounded gpsd JSON client: reports correlated per
  device, never merged across devices, a listed device not called a receiver until it reports a
  position, altitude as `altMSL` / `altHAE` / legacy `alt` with the datum named.
- A **direct receiver** (`source = nmea`) is monitored without ever becoming a second serial reader:
  through the owning MeshCom/MeshCore feed's new best-effort `monitor.sock`, shown as *held* while
  Meshtastic, graywolf or Sideband reads it natively, and by one bounded sample under the lifecycle's
  own device claim when nobody holds it. A stack start that meets the Monitor's short claim waits
  for it instead of failing.
- **Privacy contract changed on purpose:** coordinates are never logged; they are displayed only on
  the Monitor surfaces, under the console's configured access policy.
- `gpsd_owns_device()` now answers *indeterminate* for a reported local device path it cannot
  identify (it used to answer "free"); the direct-NMEA start gate inherits that.
- The NMEA classifier moved from the bridge into `gps.py`, shared with the new parser; the bridge's
  behaviour is unchanged, and gpsd and fixed feeds carry no monitor at all.
- Live proof on the reference box (gpsd, real u-blox, including the no-fix → 3D-fix progression, and the browser poll stop/resume row): `docs/live-tests/gps-monitor-test-2026-09-19.md`.
- The 0.7.0 full-stack live test is documented: `docs/live-tests/full-stack-test-2026-09-18.md`.
- CI: the test job's timeout is 30 min (was 20). The suite with branch coverage runs 15–19 min per
  Python lane on the hosted runners, and this release's ~90 new tests pushed the 3.12 lane over the
  old limit twice.

## 0.7.0

- **A status page no longer costs reception.** Reading `GET CHANNEL` on the daemon's CONF socket
  runs a CAD scan: it takes the radio mutex, puts the chip into CAD and re-arms RX, so it destroys
  a frame that is arriving. LHPC issued it constantly — the dashboard's per-band column and the
  RX/TX activity window each polled it every 3 s (per tab, so the cost grew with open tabs), and
  `read_view()`, the generic status read, bundled it into every admission check, blocker test and
  SET read-back. Measured on a Pi Zero 2 W at SF12/BW125: **54 % of frames delivered with the
  console open, 100 % with it closed**; a T-Beam on the same bench heard 12 of 12 throughout. The
  loss scales with airtime, so it was worst on the longest-range settings — including LoRa-APRS.
- `read_view()` is now STATUS + STATS and is passive by construction. Channel data is an explicit
  call: a passive read (`GET CHANNEL NOSCAN`, daemon 1.1.0) for anything periodic, and a scanning
  read reserved for a deliberate, operator-invoked measurement. Not a flag on the old function —
  a flag is how a destructive operation gets back into a generic status read.
- The RX/TX activity window polls a feed-only endpoint; it used to fetch the whole radio view and
  discard everything except the log text. MeshCore's noise-floor poller, which asked every 5 s for
  an RSSI it could get passively, now does. `SET MODE=…` no longer scans to confirm itself.
- The dashboard shows CAD state as "—" until something scans, with a **Scan now** button
  (POST + CSRF — it changes radio state, so it is not a GET). `CADSCAN=0` means *no verdict was
  taken*, not "the channel is free".
- **Listen-before-talk is unchanged.** The daemon still runs CAD before every transmission in
  MANAGED mode. What changed is that reading a status page no longer does.
- `lhpc daemon <band>` still takes a real measurement: asking once, by hand, is the case CAD is
  for.
- Pins the LoRaHAM daemon to **1.1.0** (`58051e9`), which adds the `GET CHANNEL NOSCAN` the passive
  reads above depend on. Measured on a Pi Zero 2 W at SF12/BW125, polling the CONF socket at the
  console's own cadence: **3 of 12 frames delivered with the scanning command, 24 of 24 with the
  new one** — indistinguishable from not polling at all.
- **Commissioning no longer depends on a clock.** `webserver init` refused to create the PKI on an
  unverified clock (0.6.0's gate), and firstboot runs it before the console exists — so a Lite box
  in AP mode with no RTC, no NTP and no GPS fix never finished commissioning: no console, no
  firewall, no way for anyone to fix the clock. The 0.6.2 Lite image failed its firstboot gate
  exactly there. `init` is now ungated. Under an unverified clock it mints the PKI with a **fixed
  provisional validity** (2025-01-01 to 2049-12-31 — the last date expressible as UTCTime, tested
  against OpenSSL, NSS and GnuTLS) instead of dates from the bad clock, writes a marker
  *before* the first certificate, and the console watchdog normalises the server certificate
  (same key) and the CRL once the clock is verified — reloading nginx, never running Apply, so a
  saved-but-unapplied setting is not pushed live in the background. The CAs are never rotated
  automatically. `tls-renew`, `cert issue/reissue/revoke` and a certificate-changing exposure stay
  gated; `--accept-unverified-clock` is removed from `init`, where it now had nothing to override.
- Enabling remote exposure is gated on whether it will actually **reissue the certificate**, not
  on exposure itself: an address already in the SANs changes nothing and is never refused; a
  missing one is reissued provisionally while the PKI is provisional, and gated as before once
  commissioned. `local_ip()` follows the default route, so a Lite box with the AP up and an
  ethernet lead plugged in hit the missing-SAN case at firstboot.
- One lock for every PKI writer. There was none: the watchdog rebuilds the CRL from a background
  thread on every box, so it could load the inventory, an operator could revoke, and the rebuild
  then overwrite that revocation. `init`, renew, issue, reissue, revoke, discard-export, the CRL
  heal and normalisation all take it; the watchdog skips a pass it cannot get, an operator gets
  "PKI operation busy". The CRL heal also now reloads nginx instead of running Apply.
- **An lhpc update can no longer leave a built stack running old shipped code.** Some build steps
  bake lhpc-shipped assets into what they build — the MeshCore host package, the meshcore-webui
  patch, the MeshChat frontend, fetch and gate scripts. Their sources are pinned and did not
  move, so nothing marked them stale when the asset changed: the passive-read fix above reached
  the source tree of a box that updated, but its MeshCore venv kept polling with the scanning
  command until someone rebuilt. Every asset a build step consumes is now recorded beside the
  completion marker with its content digest, and `is_built` recomputes it — a changed asset reads
  **Build required** (binary channel: reinstall) until the component is rebuilt. Consequence of
  the first release with the records: after updating to 0.7.0 six components read *Build
  required* once. Two are binary-covered and take the index reinstall the console offers
  (meshtastic, meshcom — nobody rebuilds QEMU); four rebuild locally in minutes (graywolf,
  meshcore-node, meshcore-webui, meshchat). Fresh installs and images are unaffected. The same
  rule reaches the binary channel: an artifact built by an older controller carries no asset
  records and reads *behind* until the index holds one built at 0.7.0 — so this release
  republishes meshtastic and meshcom, not only the daemon.


## 0.6.2

- Pins the LoRaHAM daemon to **1.0.0** (`4f84b6d`). The reliability release: CAD read from the
  chip's registers instead of a pin it may not route, so Uputronics boards get real
  listen-before-talk; a failed GPIO call is no longer mistaken for a logic level; `POWER` and the
  OCP limited to what the board can reach; FSK frames limited to the 63 bytes the FIFO holds; LDRO
  actually written at long symbol times; English, UTC-stamped log output. Both daemon sites move
  together — the chat stack builds from the same repository.
- Pins the KISS TNC to 0.6.2: no TX retune when RX and TX are equal.
- `POWER` is validated against the chip actually fitted. LoRaHAM daemon 1.0.0 accepts 2–17 dBm on
  SX127x boards (below 2 the driver transmits on RFO instead of the antenna's PA_BOOST pin; 18 and
  19 RadioLib refuses; 20 carries a duty-cycle contract the daemon does not enforce) and keeps
  0–20 on SX1262. lhpc admitted 0–20 for every board, so an operator with a LoRaHAM or Uputronics
  box could enter `POWER=0`, have it accepted, and get `ERR INVALID` from the daemon. The board's
  `--hw` preset now decides the range at the gate that admits the SET *and* in the number input
  beside it. A box with no hardware configured, or an unknown preset, still validates the union of
  both families and leaves the refusal to the daemon — narrowing by guess would make a Waveshare
  box refuse power levels its SX1262 accepts. No stack default changes: chat and kiss ask for 17,
  meshcore 14, meshtastic 17/10, reticulum 10.

## 0.6.1

- The AP is no longer torn down seconds after a phone drops off it. A screen lock, or a phone
  leaving an SSID with no internet — which an AP-mode box by definition has — ends the association
  while the operator is still in front of the box, and the preferred-network retry fired within
  seconds. It now waits 180 s after the AP was last seen not provably idle. "Retry now" ignores it.
- A stack's web UI serves the branded "not responding" page instead of nginx's raw 502 when its
  upstream is stopped or restarting. Only the console block had it.
- The nginx access log keeps non-2xx/3xx only. Routine success was 6,836 of 6,919 lines in one day
  and nothing rotates this file. That reduces growth; it does not bound it — errors still log
  without limit.
- The task banner polls every 2 s only while a job is running, and every 15 s otherwise.

## 0.6.0

- GPS as a time source. `bootstrap-deps.sh` installs chrony and gpsd by default; chrony sets the
  clock from NTP, or from the receiver over SHM when no NTP source is selectable. NTP always wins
  when reachable. `--no-time-source` opts out; a box with `[gps] source = nmea` is skipped, because
  gpsd would take the receiver. Installing chrony REMOVES systemd-timesyncd.
- Unverified time may not mutate the PKI. Issuance, reissue, revocation and remote exposure refuse
  before their first write unless the clock is synchronised, within a second, and not before 2025 —
  an RTC-less box booting in 1970 would otherwise mint certificates that lock out the console.
  `--accept-unverified-clock`, or a WebGUI checkbox, accepts the risk for one operation.
- The client CRL now also repairs itself when its `lastUpdate` is in the future, not only when expired.

## 0.5.1

- Image-only release (`loraham-images` v0.5.1: Desktop slimming). No controller changes.

## 0.5.0

- Starting a second component of a running stack no longer retunes its radio. The stack's daemon
  parameters are applied once before its components start, and the app then owns the radio it
  tuned; with a component already running on that band nothing re-sends the app's frequency, so
  the re-apply put the band back on its default (433.175 instead of the KISS TNC's 433.775) and
  the stack went silent while status, radio state and RX readiness all still read healthy. A band
  already served for the starting stack is now left alone, as one held by another stack already was.
- Voice on 868 can transmit: the shipped profile was SF11 at 250 kHz, where one voice packet spends
  about 1.2 s on the air to carry 260 ms of speech, so the app refused the mode and no PTT was possible.
  The 868 default is now SF7, like 433.
- Daemon pin 2a0db88: LoRa frames that fail the CRC are dropped and counted (`rx_drops`) instead of
  being handed to the stacks — the daemon cleared the radio's IRQ flags before RadioLib could read
  the CRC verdict, so a corrupted frame on a marginal link arrived as a valid message (chat, kiss/
  graywolf, voice, MeshCom, MeshCore all read the daemon's frames). Same pin for the shared chat source.
- MeshCore: a node with a fresh companion database gets the Public channel, as a MeshCore device
  has out of the box. Without it the node could not send to Public at all and logged every received
  channel message as an unknown channel hash. Seeded on a first start only, so a channel an operator
  removed stays removed.
- Web console: the client-CA CRL is re-checked on every network-watchdog pass on every box (was: only
  on AP boxes and at a WLAN join), so a box that outlives the CRL's 30-day nextUpdate no longer locks
  every client certificate out of the exposed console.
- Reticulum talks to RNode devices: `rnode_framing` (off by default) makes the LoRa driver use the
  RNode firmware's air format and preamble; the RF-log decoder strips the header byte and
  reassembles split packets; a start with the switch on refuses while the built driver predates
  it. Driver pinned at its audited framing release.
- RF-log console: one log page with a band row and a stack row, rows in time order with dir,
  ascii and decoded as the default columns (no filter), the switches at the bottom — the shown
  stack's and every stack's at once (`lhpc rflog --all on|off`, reads *mixed* when they differ) —
  and Clear all (`lhpc rflog --clear-all`); the per-stack RF-Logs submenu is gone (the switch is
  in Settings), each dashboard radio card links the band's RF logs under its daemon control, and a
  hidden table stays hidden on a phone.
- RF logs: every stack's radio boundary writes what it heard and sent, one line per frame, kept
  across restarts — the daemon per band, the KISS TNC (graywolf), the MeshCom bridge, the MeshCore
  host, the Reticulum LoRa driver, and meshtasticd's own packet trace. One registry drives the
  RF-Logs submenu on each stack card, the log page's switcher and confirmed Clear, and the new
  `lhpc rflog` command. `rf_log` is a band-less stack setting; the restart marker now judges
  applicability per changed parameter. Live proof: `docs/live-tests/rflog-test-2026-09-12.md`.
- RF-log viewer and decrypt: the log page shows an RF log as a sortable, filterable table with
  columns on and off (phone-friendly, raw view one click away), and for meshtastic, meshcore and
  reticulum a Decrypt toggle below the switcher — plus `lhpc rflog <stack> --decrypt [--follow]` —
  decodes the frames in memory with the keys already on the box, under each stack's own
  interpreter and crypto — everything this node's own secrets can open, including Meshtastic
  public-key direct messages and MeshCore requests, responses and path returns. No key moves,
  nothing decoded is written. The managed Meshtastic CLI
  venv gains `pycryptodomex` (a `build_inputs` entry), so an already-built meshtastic reads
  *Build required* once — an incremental rebuild from source, or on the binary channel the
  artifact this release publishes. Live proof:
  `docs/live-tests/rflog-decrypt-test-2026-09-13.md`.
- Pins: LoRaHAM_Daemon 1623ae9, loraham-kiss-tnc b9b7104, meshcom-loraham-bridge 7c86c96,
  loraham-rns-interface 76a7a37.
- Docs: one place per fact — duplicates across the README, the operator docs and the stack docs
  folded into their canonical file with pointers; stack docs no longer carry pin values (the
  manifest is the one source); live-test reports moved to `docs/live-tests/` and linked from the
  docs index only.

## 0.4.5

- The access point is documented where it is configured. The path to remote access set up neither
  the allowed source range nor the certificate address for it, so a box followed the controller's
  own instruction to open `https://10.42.0.1:8443` and answered `403`. Boxes without an AP —
  Desktop and hand-built — are no longer told to configure one.
- Written down because they cost time: `apply` never re-issues the server certificate, so a SAN
  added without `tls-renew` is saved and not served; `cert revoke` refuses without
  `--confirm-label`.
- openhop_core is built pristine — the noise-floor patch is upstream as PR #133, and until it
  merges the noise floor is not reported. An already-patched checkout now reads `dirty` and
  refuses the overwrite; `docs/stacks/meshcore.md` carries the one-time migration.
- A refused source update says to preserve or reconcile the modifications first, and makes
  discarding them an explicit choice rather than the implied one.

## 0.4.4

- openhop-core: 8a3921da1 -> c95a68445 (v1.0.10-413-gc95a684), used by meshcore-node
- openhop-repeater: c02b3cb73 -> 9e375da77 (1.1.4-7-g9e375da), used by openhop-repeater-src

## 0.4.3

- LoRaHAM_Voice: c0b22ddca -> 8e1af01bf (8e1af01), used by loraham-voice, loraham-voice-cli

  The Voice repository rewrote its history and the pinned commit stopped being reachable from its
  `main`, so `pin-validation` failed and the release-verification lane could not clone the source
  at all. The new pin carries the GPLv3 relicence of the Voice sources, so this is a real content
  change and not only a reachability fix. No binary is affected: Voice is covered by no published
  artifact and builds from source on the box.

- A third-party outage no longer decides a release. The headless Pyodide gate fetched `micropip`
  from a CDN while it ran, because the npm pyodide package ships no wheels — one such fetch failed
  and reddened a release whose diff touched no file under `demo/`. Those two wheels are now
  committed under `demo/vendor/` and served through `packageCacheDir`, which Pyodide checks before
  the network. That removes the fetch that failed and nothing more: the gate still resolves the
  lhpc wheel's own dependencies from PyPI, which is recorded in the backlog with the measurement
  that proves it.

- Acquisition steps across CI, testlab and Pages retry three times. Only acquisition: package
  installs, `git fetch` of the pinned remotes, the browser download. `pip-audit` is deliberately
  excluded, because it exits non-zero on a real vulnerability and a retry cannot tell that from a
  network fault; so are the test commands themselves, because a gate that fails once and passes
  twice is telling you something. A test enforces that rule by reading which program each retry
  invokes.

- The Pages deployment job is restricted to `main`. It had no branch guard, so the manual dispatch
  used to verify a change on its own branch would have published that branch to GitHub Pages.

- Documentation that told the reader to do something the project no longer does: the test suite is
  run with `python -m pytest` (CI switched when coverage moved to the checkout), the documented
  local gate no longer names a parallel plugin that is not a dependency, a maintainer patch lands
  on `dev` rather than branching from `main`, and the deploy-script tests no longer claim to need
  no network while running a real `pip install`.

## 0.4.2

- LoRaHAM_Daemon: 82c82c3f1 -> ff3c26a42 (v0.9.0), used by loraham-daemon, loraham-chat

  The daemon repository merged its upstream and published v0.9.0. The merge resolved two
  restructure conflicts and changed no file, so the pinned tree is byte-identical to the one
  0.4.0's test matrix measured; the artifact is republished regardless, because provenance names
  a commit and that commit has to be the one the manifest pins.

- The daemon `pin_tag` names a tag that exists. It read `v112-7-g82c82c3`, but `v112` is not in
  that commit's ancestry: the daemon's history rewrite re-ided 64 commits and orphaned every
  release tag it had, so `git describe` — the convention every other pin follows — yielded
  `110-137-g82c82c3` instead. Nothing enforced this, because CI validates `pin_commit` and never
  the tag, so a provenance label had been naming an unreachable object since the pin moved. The
  daemon repository now carries a reachable `v0.9.0`, which is what both blocks and both stack
  pages record.

## 0.4.1

- MeshCom-Firmware: 674413ce3 -> 6edc74997 (v4.35t), used by meshcom-firmware

## 0.4.0

- The release test matrix ran on the reference box: nine of the twelve rows, the cross-cutting
  checks, a full purge-and-`auto-install` pass (9/9 stacks, 0 blocked, 0 failed), a boot restore
  (3 restored, 0 failed) and the host tests. Every source component ends at its manifest pin and
  every binary stack at `built_from == pin`, with no OOM anywhere in the run. Measured numbers and
  the rows that remain owed are in [live-test.md](docs/live-tests/live-test.md).

## 0.3.16

- LoRaHAM_Daemon: dbd2998b7 -> 82c82c3f1 (v112-7-g82c82c3), used by loraham-daemon, loraham-chat
- LoRaHAM_Voice: 143b83f25 -> c0b22ddca (c0b22dd), used by loraham-voice, loraham-voice-cli
- openhop-repeater: 4705c99c3 -> c02b3cb73 (1.1.4-5-gc02b3cb), used by openhop-repeater-src

  The Voice move carries no code: 143b83f2 and c0b22dd have the identical tree 780298165b6c.
  It is recorded because Voice is tip-tracked, so the release bot would otherwise read it as a
  real move and spend a version number and an image build on nothing.

- Chat builds from `clients/chat/lorachat_ncurses_113.c`. Chat is an artifact source, so it follows
  the daemon repository's default branch rather than its manifest pin, and that repository moved its
  client programs into `clients/`. The compile was still naming the old root path.

- Adopting a local source no longer fails when git repacks the checkout while it is being
  copied. `copytree` lists a directory then reads it, and git packing loose objects prunes
  their fan-out directories in between, so an entry vanishes mid-copy — the same repository
  in a different physical representation. The `.git` copy is now retried once, and only when
  every collected failure is a missing path; a mixed failure, or a vanishing working-tree
  file, still fails the adoption as before.

## 0.3.15

- loraham-kiss-tnc: 3c4461e4f -> e7646c12f (v0.5.1-2-ge7646c1), used by loraham-kiss-tnc, loraham-kiss-serial
- meshcom-loraham-bridge: f0189206a -> 35a9348a0 (35a9348), used by meshcom-bridge

## 0.3.14

- openhop-repeater: 47e49e64a -> 4705c99c3 (1.1.4-3-g4705c99), used by openhop-repeater-src

## 0.3.13

- A build whose every command succeeded but whose completion marker could not be written no longer looks like that component's own build failure. It used to return the last SUCCESSFUL step's log path, and the release lane decides attribution from exactly that identity — so a full disk during Reticulum's build produced evidence against five upstream pins that had built perfectly. Such a result now carries no step identity at all; the log stays named in the failure text, where diagnostics belong.
- The artifact-portability guard runs in CI instead of skipping there. It compares what this controller adds to a published artifact against the publish roots RELEASED controllers declare, read from their tags — and the CI checkout had no tags, so a required job was proving nothing. CI fetches them now, the guard fails rather than skips when they are missing, and it records which tags it compared.
- The daemon and Chat pins move to `dbd2998` (`v112-6-gdbd2998`). Upstream rewrote its history to remove three attribution trailers, which gave 64 commits new ids and left the old pin off `main`'s ancestry — `pin-validation` catches exactly that. Five test-only commits landed ahead of the rewrite (a strict-build flag fix, three test-hygiene fixes, and six daemon-spawning tests that now skip where there is no `/dev/spidev0.*` instead of dying), so the daemon's own behaviour is unchanged; the binary is republished from the new commit regardless, because provenance names a commit that has to exist.
- The attribution rule moves to `lhpc.core.build_regression`, with `tools/build_regression.py` as its command-line form. The binary builder has to answer the same question about the same `lhpc build` output and cannot import the test lab; two copies of this rule would drift, and the direction they drift in is freezing an upstream pin over a broken package index.

## 0.3.12

- The Meshtastic web-client and CLI pins are recorded (`build_inputs`) in a file beside the built artifact. Moving one used to change nothing an installed box could see: the firmware checkout stayed put, so the completion marker stayed valid, the stack still read *built*, and it kept serving the old client and running the old CLI while the manifest claimed the new ones. Beside the artifact and not inside the marker, because an artifact has to be installable by controllers older than itself: the marker's content is compared byte for byte by every controller that ever shipped, and a file outside the publish roots a released manifest declares is refused outright.
- `lhpc status` no longer calls such an artifact current. Binary freshness compares the artifact's own marker as well as its component commits, still without touching the network.
- A binary-installed stack that reads *not built* is now pointed at `lhpc install <stack> --source binary --yes`. The console offered `lhpc build`, which the binary channel refuses — a dead end.
- The manifest refuses to load unless a recorded input is exactly what the build step consumes, so the two copies of a version cannot drift apart. Each entry names the step that consumes it and the argv token it fills (`command = "pip"`, `token = "meshtastic=={value}"`), and that step must carry the rendered token exactly once: a recorded `2.7.11` is not satisfied by a step installing `2.7.110`, nor by another command that carries `meshtastic==2.7.11` verbatim.
- The release-verification lane proves the installed web client is the pinned one, and that the artifact records the manifest's build inputs — a moved pin whose binary was not republished now stops the release.
- That lane names the cases it must report, so a renamed or dropped case fails it instead of passing a count. Each interactive component must now draw something only a working one draws, and a missing Sideband GUI capability fails rather than printing a note.
- A release-lane failure at a stack's own build, start or readiness carries a `STACK-REGRESSION stack=… phase=…` line in its JUnit failure text, so an automated release can freeze exactly the stack that regressed. A build is attributed only where LHPC typed a component's build as failed AT A STEP THE RECIPE DECLARES ITS OWN — a manifest build step now says so with `attributable = true`, which only a compile, a patch or a check over what earlier steps already fetched may carry. A fetching step never is: pip failing on an unreachable package index is typed exactly like a broken recipe, and freezing four MeshCore pins over it is not recoverable in a week. An install never is either, because a clone that could not resolve a host and an upstream that dropped the pinned commit read the same. Everything else — a stop, the lab's fake daemon, a display-dependent predicate, an artifact that was not republished, the identity check over all stacks — stays deliberately unattributed and reports as an ordinary failure.
- The release lane runs with `-x` and stops at its first failure: its cases chain over one radio pair, so a run that continues either buries a genuine freeze under an unmarked prerequisite failure or hands an innocent stack its own marker. A failing case still releases the stacks it started, and a stop that could not stop something is now a visible teardown error rather than a swallowed one — a run whose lab is broken attributes nothing. A case whose prerequisite was never up still says so instead of failing at its own attributed assertion.
- Both Voice variants START in the lane: the GTK app with the display the lane owns, the terminal variant with that display taken down — the box LHPC actually offers it on. Neither is accepted on build evidence any more.
- The MeshCore repeater refusal now names a route the CLI can take. It said *"set repeater_name in the same save"*, but `lhpc config` saves one parameter per call and rejects two with *"too many arguments"* — only the console can save both at once. From a shell the order is `repeater_name` first, then `mode`, and the message says so.
- **Upgrading:** Meshtastic reads *Build required* once after this release, because no existing artifact records these values yet. On the binary channel that is a reinstall of a republished artifact; from source it is a rebuild, largely incremental. The recorded values live in a file beside the completion marker rather than inside it, so a republished artifact still reads *built* to a controller that predates them — without that, neither publishing the artifact first nor releasing the controller first was safe.

## 0.3.11

- CI lints the test lab. `CONTRIBUTING.md` has always named `ruff check lhpc testlab` as the gate, but the workflow ran `ruff check lhpc` only, so findings in `testlab/` could sit on `main` unnoticed — five did, until 0.3.10 fixed them. The workflow and `maintenance.md` now match what contributors are told to run.

## 0.3.10

- Voice is an ordinary pinned source. Its `artifact` flag meant an ordinary pinned install or update took the branch tip and skipped the identity check, so the manifest pin was decorative. It now installs and verifies the pinned commit like every other source.
- Internal: five lint findings in the test lab's release lane are fixed (regex flag aliases spelled out, one import block sorted). No behaviour change; `ruff check testlab` is green again.

## 0.3.9

- **Files you add to a managed source checkout now survive an update.** A stack's own logs and generated settings, or a file you put there yourself, are carried into the new source instead of blocking the update; the old checkout is discarded only once each of them is proven to be there, and a path the new upstream version also ships is a refusal naming the file rather than a merge. LHPC's own regenerable output (`build/`, `.pio/`, `.venv/`, `.work/`, `.run/`, `__pycache__/`, `node_modules/` and a declared built binary) is the exception: it neither blocks an update nor survives one. Editing, deleting or staging an upstream-tracked file still makes the checkout dirty and blocks the update — to run a modified stack, fork it and point the component's remote and pin at your fork. Uninstall and clean are unchanged and keep their existing dirty-tree protection; unlike source updates, they do not carry additions forward.

## 0.3.8

- The release-verification lane no longer reports a pass it did not earn. A stack counts as running only when LHPC's own status says `running` for it, a failed stop is a failure rather than housekeeping, an interactive component must draw what it is supposed to draw and must exit cleanly rather than be killed, and the optional components a stack start deliberately leaves alone (Sideband, LXMD, the MeshCore Web UI) are started by name. Both Voice variants are proved, because they share one checkout and it proves neither.
- Its identity check fails closed: a mandatory component that is not installed is a failure, not a printed note, and a binary stack is compared against every component its artifact covers. Artifact integrity is checked before the stacks start, because an emulated node writes to its own flash as soon as it boots.
- `release-verify` runs on `main` pushes and explicit dispatch only. It used to run on every push, including `dev`, which spent half an hour proving a commit nobody was about to release.
- Documentation: how `main` advances by both release paths; that a binary row's build is refused rather than skipped; that a `dev` checkout reads `match` when the branch tip IS the pin; and the four different questions provenance answers, of which file integrity is the one that is time-sensitive.

## 0.3.7

- openhop-core: 8cdb04e73 -> 8a3921da1 (v1.0.10-410-g8a3921d), used by meshcore-node
- openhop-repeater: efc5616ec -> 47e49e64a (1.1.4-1-g47e49e6), used by openhop-repeater-src

## 0.3.6

- A default install lands on the composition this release proved: without `--source`, install, update and auto-install take the published binary where there is one, else `pinned` (was `dev`). The image builder runs that same bare auto-install, so a fresh image carries the release's pins instead of the branch tips of the day. `dev` and `stable` stay available as explicit choices.
- New test-lab lane `release` (CI job `release-verify`): every stack an automated pin release may move is installed on its default channel, built, started and verified by its own state, then re-proved to BE the candidate manifest's commits. Interactive components run on a real terminal; GUI ones where LHPC's own predicate says they can. The daemon and RadioLib are the lab's fixtures and are never proved there — they need the radio.
- Release policy: a patch release (pins or a fix) branches from `main` and has two producers, the maintainer and the [release bot](https://github.com/makrohard/lhpc-release-bot); a minor release comes from `dev` with the full box matrix. An image follows every release. A patch returns to `dev` as a fast-forward or a pull request, never a rewrite.
- The lab lanes upload their own build, start and state logs as run artifacts, so a red lane says which component failed.

## 0.3.5

- Tests fail only for LHPC behaviour now. The JavaScript source-arithmetic and hand-built-DOM harnesses are replaced by real headless Chromium, the deployment tests run the shipped scripts, the testlab coverage matrix gives way to enumerating the app's own routes, and the remaining source scans are replaced by the behavioural seams they stood in for.
- The suite is organised by the behaviour it protects (`core`, `stacks`, `web`, `cli`, `install`, `host`, `repo`), the test lab has three named lanes (`unit`, `acceptance`, `browser`), and the suite runs from any working directory, on a machine with no browser, with nothing skipped.
- No product change: the only non-test edit in this release removes a test-only hook from `system.js` that the deleted Node harness needed.

## 0.3.4

- LHPC's own tests for the MeshCore host application ran in no gate at all; they now run in CI against the manifest-pinned openHop core. No external project's own test suite runs in LHPC CI.
- `--source stable` resolves through one rule instead of two: the newest version-shaped tag, else the default-branch HEAD. The local and the remote (auto-install) paths used different regexes and different fallbacks, so the same selector could install different commits of one component.

## 0.3.3

- Documentation: one canonical owner per subject, short context and a link everywhere else; `live-test.md` keeps the newest live run only (git history holds the rest). No behaviour change.
- The release rule now says which releases run the full [test matrix](docs/test-matrix.md): a minor release (`0.X.0`) does, a patch release runs the live checks its own change calls for.

## 0.3.2

- Internal: core service coupling reduced — logic moved into plain core modules (`restart_required`, `power`, `jobs`, `resources`, `gps`, `procident`); no behaviour change. Public surface, CLI and routes identical; live re-proof of every moved flow in `docs/live-tests/live-test.md`.
- CI measures and publishes branch coverage (summary and `coverage.xml` per Python version); no threshold.

## 0.3.1

- Meshtastic serves the newest web client: LHPC pins the meshtastic/web release itself (v2.7.2, sha256-verified on every install) instead of the firmware's `bin/web.version`, which had stayed at 2.6.7 across the 2.7.x/2.8.0 firmware lines. The binary artifact ships the client, so a republish follows the pin.
- Branch model: `main` is the latest release, `dev` is where changes land; CI and testlab run on `dev` too. `CONTRIBUTING.md` says what should be green.

## 0.3.0

Breaking pre-1.0 cleanup; a fresh image or a clean final-0.2.10 install is the supported path. Pins
unchanged since 0.2.10. Release test: `docs/live-tests/live-test.md` (from-zero install, all stacks, boot restore
on a Zero 2 W).

- **iGate removed;** Graywolf is the APRS station (RF↔APRS-IS through the KISS TNC, with a web UI).
- **Source `strategy` and the `link`/`linked` states removed;** every managed source is a clone under
  the runtime root, and a symlink is never a managed source. Stale read tolerances and the old MeshCore
  identity rescue are gone with them.
- **Uninstall keeps operator data** (`config/`, `backups/`, `profiles/`, the stacks' app data under
  `state/`); `--purge` removes everything. A checkout without an ownership record is refused, never
  adopted silently.
- **Binary channel:** updates go through the index; a binary auto-install row that cannot start is
  reported blocked, never successful.
- **Console truth:** a saved proxy Disable stays visible until Apply removes the listener; a
  gate-deferred Webserver Apply is announced on every page until the firewall is applied; the firewall
  scripts exist from bootstrap on and every apply sequence ends with `lhpc webserver apply`.
- **Manifest:** shell-era build/run/test strings removed; shell shorthand refused. `bootstrap-deps.sh`
  never invokes sudo. Stored passwords are masked on the stack page with a Show button.
- **Docs:** consolidated to one home per fact, READMEs rewritten and ground-truthed in both languages,
  the release procedure in `docs/test-matrix.md`, dead code and history wording removed throughout.

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
