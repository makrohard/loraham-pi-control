# Silicon test — LHPC stacks against real ESP32 peers, 2026-09-05

On-air acceptance of the LHPC stacks against **real ESP32 hardware running each project's own
original firmware** — not emulated peers, not simulated radios, not LHPC talking to itself. Every
result below was produced by a physical node on the air: MeshCom, Meshtastic, LoRa-APRS and MeshCore
firmware on LilyGo and BQ boards. Software green is not on-air green, so this file records only what
was actually transmitted and what was actually heard, plus what remains open.

Each section covers one stack and states its own evidence. Times are CEST unless marked UTC
(MeshCom's heard tables print UTC).

## Contents

- [Test bench](#test-bench) — box, radios, the four peers, and how evidence was gathered
- [1. MeshCom (QEMU + bridge + daemon) — 433.175 MHz](#1-meshcom-qemu--bridge--daemon--433175-mhz) — both directions at packet level; content unverified, one repeat inconclusive
- [2. Meshtastic (meshtasticd, native) — 868 MHz](#2-meshtastic-meshtasticd-native--868-mhz) — all four paths pass after resetting both nodes to LHPC standard settings
- [3. Graywolf APRS (+ KISS TNC + daemon) — 433.775 MHz LoRa-APRS](#3-graywolf-aprs--kiss-tnc--daemon--433775-mhz-lora-aprs) — full round trip with content; **one defect** — the scheduled beacon never fires
- [4. MeshCore (OpenHop) — 869.618 MHz, mode chat+repeater](#4-meshcore-openhop--869618-mhz-mode-chatrepeater) — every functional path passes; a misreporting command-line client
- [Session close — state on the box (2026-09-05 11:00 CEST)](#session-close--state-on-the-box-2026-09-05-1100-cest) — what the box is running, what changed, and every open item

**Scoreboard:** 40 rows across four stacks — **34 pass**, 1 fail (Graywolf's scheduled beacon),
1 inconclusive, 4 not covered (no indoor GPS fix on the tracker, and content verification on the
MeshCom peer).

---

## Test bench

| | |
|---|---|
| Box | `lhpc-e293` (Raspberry Pi Zero 2W, Lite image), LAN `192.168.178.106`, LHPC main @ `b7cfd5c` (0.2.8 + fixes) |
| Radio | LoRaHAM daemon on both bands: 433 via `/tmp/loraconf433.sock`, 868 via `/tmp/loraconf868.sock`. Meshtastic drives its 868 radio directly instead |
| Peer A (433, MeshCom) | LilyGo T-Deck, MeshCom 4.35p, call `DJ0CHE-07`, workstation USB, serial console |
| Peer B (868, Meshtastic) | BQ Station G2, `CHE Station` / `cheS`, workstation USB, Meshtastic CLI |
| Peer C (433, LoRa-APRS) | the same T-Deck reflashed to CA2RXU 2026-04-22, call `DJ0CHE-7`, serial console |
| Peer D (869.618, MeshCore) | LilyGo T-Deck Pro, node `CHEMobile`, driven over **BLE** (its USB is log-only) |
| Distance | Same premises, a few metres. RSSI −19…−60 dBm depending on the pair — near field throughout, so nothing here says anything about range |
| Operator | All RF inside the licensed profiles; nothing transmitted beyond the tests listed |

Peers A, B and C occupy the workstation's single `/dev/ttyACM0`, so only one is plugged in at a
time; the sections below are in the order they were run.

Evidence sources used:

- Daemon counters on both bands: `GET STATS`, `GET STATUS`, `GET CHANNEL` on the CONF socket.
- Stack logs on the box, and each stack's own API or web endpoint.
- On the peers: the firmware's own serial console, its Meshtastic/MeshCore client over USB or BLE,
  and its heard/contact list.

---

## 1. MeshCom (QEMU + bridge + daemon) — 433.175 MHz

**Result: PASS for both directions at packet level; message content not independently verified; one repeat inconclusive.**

### Configuration as found

| Node | Call | Profile | Power | Notes |
|---|---|---|---|---|
| QEMU node (box) | `DJ0CHE-12` | 433.1750 MHz, EU8, EBYTE_E22 emulation | 20 dBm | MESH off, gateway off, web :18083, net-console :12323 |
| T-Deck (peer) | `DJ0CHE-07` | 433.1750 MHz, SF11, BW250, CR4/6 | 22 dBm | MESH on, gateway off, WiFi off |
| Daemon 433 | — | configured by the bridge (control-plane) | capped 20 dBm | `TXMODE=MANAGED`, `CADRSSI=−90`, `CADWAIT=1500` |

Stack start sequence observed: bridge listening 08:25:58, QEMU launched 08:26, XR client connected
08:31:42 (≈5.5 min boot on the Zero 2W — matches the documented expectation), radio configured
08:31:45.

### Matrix

| # | Test | Evidence | Result |
|---|---|---|---|
| 1 | Boot hellos cross both ways (no operator action) | QEMU MHeard: `DJ0CHE-07 HEY` rssi −54 snr 12; T-Deck MHeard: `DJ0CHE-12 HEY` rssi −48 snr 7; daemon RX=1 TXOK=1 | PASS |
| 2 | T-Deck → box text (`::LHPC test 1 from T-Deck DJ0CHE-07`, 08:41:53) | daemon RX 7→9 within seconds; QEMU MHeard row → typ `TXT` 07:41:55 UTC | PASS |
| 3 | Box → T-Deck text via net-console (`::LHPC test 2 from QEMU DJ0CHE-12`, 08:43:01) | daemon TXQDONE 15→16, TXOK 5→6, TXQLAST=OK; T-Deck MHeard row → typ `TXT` 07:44:01 UTC (≈60 s after send) | PASS |
| 4 | Box → T-Deck repeat (`::LHPC test 3 …`, 08:45:43) | daemon TXQDONE 16→20, TXOK 6→7, CADTIMEOUT 10→12; T-Deck MHeard shows only a later `HEY` (07:47:33 UTC) | INCONCLUSIVE |
| 5 | Received text readable on the far side | not achieved — MHeard proves a `TXT` packet arrived, not its content | NOT COVERED |
| 6 | Periodic beacons during the run | both MHeard tables kept refreshing | PASS |

### Findings

1. **Channel reads BUSY almost permanently at the box.** `GET CHANNEL` sampled live RSSI −78…−84 dBm
   against `CADRSSI=−90`; `CADSTATE=BUSY` in every sample. Over the session 12 of ~20 queued TX
   jobs ended `CAD_TIMEOUT`. This is why row 4 is inconclusive and why box→peer traffic will be
   lossy in this environment. MeshCom runs MANAGED by design, so the remedy is the threshold, not
   the mode: raise `CADRSSI` towards the measured floor. Not applied — operator decision.
2. **MANAGED is the correct TX mode, and the stack page was stale.** The bridge submits managed-TX
   settings and the live box ran MANAGED throughout, transmitting successfully — so MANAGED is what
   MeshCom uses. `docs/stacks/meshcom.md` claimed the daemon "must be in DIRECT mode" in two places;
   both are corrected as part of this commit.
3. **Latency:** box→peer texts appear ~60 s after the send (MeshCom's own TX scheduling), peer→box
   within ~2 s.

### Follow-ups (not done)

- Decide the CADRSSI policy for MeshCom: the threshold, not the TX mode, is what stalls it here.
- Add a content-verified message test (web UI or debug capture) and repeat row 4 three times.
- Consider surfacing `CAD_TIMEOUT` drops in the stack page (idea already noted in the RF lessons).

---

## 2. Meshtastic (meshtasticd, native) — 868 MHz

**Result: PASS in all four tested paths (broadcast and direct message, both directions), after a
full reset of both nodes to LHPC first-install settings.**

### Reset performed first (operator instruction: LHPC must run its standard settings, not the Spanish ones)

The box node carried a hand-made configuration that LHPC does not manage and never sets:
five channels (`SFNarrow` primary, plus `Iberia`, `Madrid`, `Bots`, `Test`), `usePreset: false`,
`overrideFrequency: 869.618`, SF7 / BW62.5. The peer was on stock preset settings, so the two could
not meet.

| Step | Command | Effect |
|---|---|---|
| Backup both nodes | `lhpc meshtastic --export-config` / `meshtastic --port … --export-config` | saved to the scratchpad before any change |
| Reset box node | `lhpc meshtastic --factory-reset --yes` | config reset; meshtasticd logged "Factory config reset finished, rebooting soon" and exited |
| Restart stack | `lhpc stack start meshtastic --yes` | LHPC re-applied what it owns: region, node identity, GPS mode, fixed position — "required post-start completed" |
| Reset peer | `meshtastic --port /dev/ttyACM0 --factory-reset` | region fell to `UNSET` (TX disabled) as expected |
| Peer region + name | `--set lora.region EU_868 --set-owner "CHE Station" --set-owner-short cheS` | the peer is not LHPC-managed, so these two settings are set by hand |

LHPC's own stack parameters needed **no** change — they were already at manifest defaults
(`region = EU_868`, `use_gps = on`); only the required identity (`node_name`, `node_short`) is
operator-set. The Spanish configuration lived entirely inside Meshtastic, not in LHPC.

### State after the reset — both nodes identical where it matters

| | Box (LHPC-managed) | Peer (Station G2) |
|---|---|---|
| Node | `Joe on LHPC` / `JLHP`, `!9ee3dad0`, PORTDUINO | `CHE Station` / `cheS`, `!a2e9aed8`, STATION_G2 |
| Region | EU_868 | EU_868 |
| Preset | `usePreset: true`, BW250 / SF11 / CR5 (LongFast) | same |
| Override frequency | none | none |
| Primary channel | default (identical channel bytes on both) | default |
| TX | enabled, 27 dBm | enabled, 27 dBm |
| GPS | enabled, live fix 48.4180 / 11.6654 | not present |

### Matrix

| # | Test | Evidence | Result |
|---|---|---|---|
| 1 | Peer → box, broadcast text | box log: `Received text msg from=0xa2e9aed8 … msg=LHPC MT test 1 from G2` | PASS |
| 2 | Box → peer, broadcast text | peer listener: `TEXT from !9ee3dad0: LHPC MT test B from box rssi=-19 snr=6.0` | PASS |
| 3 | Peer → box, direct message with ACK | first attempt NAK `NO_CHANNEL`; after node-info exchange: `Received an ACK`, box log has the text | PASS (after 4) |
| 4 | Node-info / key exchange | `lhpc stack poststart meshtastic --yes` → peer received `NODEINFO_APP from !9ee3dad0` and stored the box with its public key | PASS |
| 5 | Box → peer, direct message with ACK | first attempt NAK `MAX_RETRANSMIT`; retry: `Received an ACK` and peer received the text (rssi −22, snr 6.0) | PASS on retry |
| 6 | Box position broadcast | peer received `POSITION_APP from !9ee3dad0` | PASS |
| 7 | LHPC reconvergence after a factory reset | region, owner and GPS re-applied automatically by the stack's post-start | PASS |

### Findings

1. **A factory-reset node cannot be direct-messaged until node info has been exchanged.** Modern
   firmware refuses a channel-encrypted direct message — box log: `Rejecting legacy DM`, answered
   with routing error `NO_CHANNEL`. The sender only uses the modern encrypted form once it holds the
   recipient's public key, which arrives with node info. Default `nodeInfoBroadcastSecs` is 10800 s,
   so an operator can wait up to **three hours** after a reset before direct messages work.
   `lhpc stack poststart meshtastic` re-applies the node identity and triggers an immediate node-info
   broadcast — the practical unblock. Worth documenting in the Meshtastic stack page; broadcasts are
   unaffected throughout.
2. **First direct-message attempt failed in each direction, succeeded on retry** (`NO_CHANNEL` one
   way for the reason above, `MAX_RETRANSMIT` the other). Treat a single failed direct message as
   inconclusive, not as a broken link.
3. **LHPC's post-start convergence works as designed** — after a factory reset the node came back
   with the LHPC-owned settings re-applied without any operator action beyond the stack start.
4. **The link is near-field** (RSSI −19…−22 dBm). This test proves configuration and protocol, not
   range.
5. **Historic Spanish traffic is still in the box log** (`Hola Getafe!`, `Test DJ0CHE Getafe`), from
   before the reset. Log only; the node configuration is clean.
6. **GPS on the box works** (live fix broadcast to the peer). The recurring `RTC not found` warning
   is the known missing real-time clock on this box, not a fault.
7. **Side effect to resolve:** the LoRaHAM daemon and, with it, the MeshCom stack were **running**
   before this work and are **stopped** now; the daemon's log stops at 09:07, around the Meshtastic
   restart. meshtasticd is documented as conflicting with the daemon over direct SPI, so a
   conflict-driven stop is plausible — but the two had been running side by side beforehand
   (433 on CE0, 868 on CE1), so this needs a look before the next stack test.

### Follow-ups (not done)

- Establish why the daemon/MeshCom stopped, and restart them.
- Document the node-info / direct-message rule and the `poststart` unblock on the Meshtastic stack page.
- Repeat direct messages three times per direction to quantify the first-attempt failures.

---

## 3. Graywolf APRS (+ KISS TNC + daemon) — 433.775 MHz LoRa-APRS

**Result: PASS. A message sent from the box was received and acknowledged by the peer, and the
acknowledgment came back over RF and was gated to APRS-IS — a complete round trip with verified
content.**

### Peer

LilyGo T-Deck, reflashed from MeshCom to **CA2RXU LoRa APRS Tracker/Station, version 2026-04-22**
(`RichonGuzman`), on the workstation's USB at `/dev/ttyACM0`. It arrived unconfigured: the firmware
force-starts its web-configuration portal (own access point `LoRaTracker-AP`) until a callsign is
set, so it never reached operating mode.

Per operator instruction the tracker keeps **standard configuration, callsign only**. It was set to
`DJ0CHE-7` in all three beacon profiles through the portal, everything else untouched. Its portal
posts `multipart/form-data`, so a URL-encoded post is silently ignored — that is why an earlier
attempt appeared to save but did not. After the write the device left configuration mode and booted
normally (`Initializing SX126X … LoRa init done! … Setup Done!`).

Its stock radio profile matches LHPC's APRS profile exactly, so nothing had to be aligned:

| | Tracker (CA2RXU, profile 1) | Box (LHPC `_LORAHAM`) |
|---|---|---|
| Frequency | 433.775 MHz | 433.775 MHz RX and TX (single channel) |
| Spreading factor | 12 | 12 |
| Bandwidth | 125 kHz | 125 kHz |
| Coding rate | 4/5 | 4/5 |
| Power | 20 dBm | 17 dBm |
| TX mode | — | MANAGED (required by Graywolf) |

### Chain brought up on the box

`lhpc stack start graywolf --yes` started all three in order and verified each: daemon on 433 in
MANAGED mode, KISS TNC listening on `127.0.0.1:8001` with the client attached, Graywolf on
`127.0.0.1:8080` with its post-start provisioning completed. Its KISS channel reports
`health: live`, `tx capable`. Meshtastic stayed running on 868 throughout, so the two coexist.

### Matrix

| # | Test | Evidence | Result |
|---|---|---|---|
| 1 | Tracker configured and in operating mode | portal write accepted; boot log shows radio init and `Setup Done!`; config diff is the callsign only | PASS |
| 2 | Chain starts with dependencies in order | daemon MANAGED → TNC 8001 → Graywolf 8080, each verified by LHPC | PASS |
| 3 | KISS client attached to the TNC | TNC log `Client connected: 127.0.0.1:41692`; channel `health: live` | PASS |
| 4 | APRS-IS session established | `aprs-is connected server=rotate.aprs2.net:14580 callsign=DJ0CHE` | PASS |
| 5 | Box → tracker, message over RF | tracker console: `[LoRa Rx] DJ0CHE>APGRWO,WIDE1-1,WIDE2-1::DJ0CHE-7 :LHPC graywolf test 1{005` | PASS |
| 6 | Tracker → box, acknowledgment over RF | tracker `[LoRa Tx] … ack005`; box received it, Graywolf marks message 5 `acked` after 1 attempt | PASS |
| 7 | Callsign confirmed on air | the received frame's source reads `DJ0CHE-7` | PASS |
| 8 | Daemon transmitted without a channel-busy stall | `TXOK=1 TXERR=0 TXBUSY=0 CADTIMEOUT=0` | PASS |
| 9 | RF → APRS-IS gating | the acknowledgment appears as `DJ0CHE-7>APLRT1,WIDE1-1,qAR,DJ0CHE::DJ0CHE :ack005` | PASS |
| 10 | Tracker's own position beacon → box | not exercised: no GPS fix indoors, so the tracker never beacons | NOT COVERED |
| 11 | Box → tracker, position beacon on manual trigger | `POST /api/beacons/1/send` → `{"status":"sent"}`; tracker console: `[LoRa Rx] DJ0CHE>APGRWO,WIDE1-1,WIDE2-1:!4825.81N\\01140.09EO/A=001649Hello from Joe!`; daemon `TXOK` 1→3, no CAD timeouts | PASS |
| 12 | Box → tracker, position beacon on its own schedule | beacon is `enabled`, GPS-sourced, `interval: 600`, gpsd holds a 3D fix, scheduler heap built — yet nothing transmitted in the 19 minutes after start and a 6-minute tracker capture was silent | **FAIL** |
| 13 | Beacon carries a real position | `!4825.81N\\01140.09EO/A=001649` — the box's gpsd fix, altitude in feet | PASS |
| 14 | Tracker → box, position beacon | the operator cannot trigger a beacon by hand on this build and there is no indoor GPS fix, so the tracker never beacons. Its transmit path is already proven by row 6 (the acknowledgment), so this row adds nothing and was skipped by agreement | NOT COVERED (skipped) |
| 15 | Digipeating of a peer packet | not exercised | NOT COVERED |

### Findings

1. **The APRS side transmits cleanly where MeshCom stalls.** The same 433 band read `BUSY` with a
   live RSSI near −83 dBm, yet the APRS message went out first time with zero CAD timeouts. The
   APRS profile waits up to 1500 ms and needs 250 ms of confirmed idle, and that found a gap;
   MeshCom's much shorter idle window did not. This strengthens the case that the default
   channel-busy threshold, not the radio, is what blocks MeshCom transmissions here.
2. **Test traffic reached the public APRS network.** With the iGate enabled, the peer's
   acknowledgment was receive-gated to APRS-IS as `qAR,DJ0CHE`, and the outgoing message was
   **also** delivered over APRS-IS in parallel with RF. Everything used the operator's own
   callsign, so this is legitimate, but it means bench traffic is publicly visible. For a purely
   local test, set `igate = 0` — which is the LHPC default. The station currently runs
   `igate = 1`, `gate_rf_to_is = 1` and `gate_is_to_rf = 1`, the last two both non-default;
   `gate_is_to_rf` had no effect here because the iGate has no IS→RF rules configured.
3. **The tracker cannot be tested unattended without a GPS fix.** CA2RXU only beacons on a valid
   fix, so peer-initiated traffic needs either an outdoor fix or a keypress. Message
   acknowledgments are the reliable way to prove the peer's transmit path indoors.
4. **The tracker's portal needs multipart form data.** Worth knowing before anyone scripts against
   it: a URL-encoded post returns no error and changes nothing.
5. **A callsign is what releases the tracker from configuration mode.** Freshly flashed it is
   unreachable on RF, which looks like a hardware fault and is not one.
6. **The beacon transmits on demand but never on its own schedule.** `POST /api/beacons/1/send`
   puts a correct position beacon on the air within a second, twice in a row, received intact by the
   tracker — so the radio path, the position source and the beacon definition are all sound. What
   does not happen is the scheduled transmission: the beacon is `enabled` with `interval: 600`,
   `delay_seconds: 30`, `slot_seconds: 146`, the scheduler logged
   `beacon scheduler heap built count=1`, and nothing fired in 19 minutes. That isolates the defect
   to Graywolf's beacon scheduling, not to LHPC's radio chain. Worth reproducing and, if it holds,
   reporting upstream.

### Follow-ups (not done)

- **Investigate the beacon that never fired** (finding 6) — the main open item from this section.
- Decide the iGate policy for bench testing and record it.
- Test digipeating of a peer packet.
- Take the tracker outdoors for a GPS fix if peer-initiated beacons ever need covering; the peer's
  transmit path is already proven by its acknowledgment, so this is optional.

---

## 4. MeshCore (OpenHop) — 869.618 MHz, mode chat+repeater

**Result: PASS in every functional path — messaging both ways with acknowledgments, advert
propagation, repeater retransmission and both web endpoints. One usability defect found in the
command-line client.**

### Peer

LilyGo **T-Deck Pro** running MeshCore, node name **CHEMobile**, public key `37bd5d25f79f…`. Its USB
port is log-only — it is not a serial companion, so the client interface is **BLE**
(`MeshCore-CHEMobile`, `50:78:7D:2C:B6:A1`). Driving it from the workstation over BLE with the
`meshcore` Python package let both ends of every test be scripted.

Its radio matches the box's `eu_uk_narrow` preset exactly, so nothing had to be changed on either
side:

| | Peer (CHEMobile) | Box (`eu_uk_narrow`) |
|---|---|---|
| Frequency | 869.618 MHz | 869.618 MHz |
| Bandwidth | 62.5 kHz | 62.5 kHz |
| Spreading factor | 8 | 8 |
| Coding rate | 8 | 8 |
| TX power | 14 dBm (max 22) | 14 dBm (max 20) |

### Box side

Node **DJ0CHE-12** (`e9dfe7e00b47…`), mode `chat+repeater` with repeater **Relay e293** in `forward`
behaviour, position from the global GPS source. MeshCore reaches the radio **through the LoRaHAM
daemon** on 868, not by direct SPI, so the start applied the daemon's queue and channel-busy
settings first.

### Matrix

| # | Test | Evidence | Result |
|---|---|---|---|
| 1 | Radio contention is refused, not fudged | starting MeshCore while Meshtastic held 868 was refused with a typed conflict naming the holder and the fix (`meshtastic must be stopped first`) | PASS |
| 2 | Stack starts after freeing the radio | daemon 868 settings confirmed one by one, GPS live (5 sentences), node endpoints 5000 and 8000 up, web UI 8788 up | PASS |
| 3 | Radio profile matches the peer | both ends report 869.618 / 62.5 / SF8 / CR8 | PASS |
| 4 | Nodes know each other | box lists `CHEMobile` at **0 hop**; peer lists `DJ0CHE-12` and `Relay e293` | PASS |
| 5 | Box → peer direct message | peer received `{"type":"PRIV","pubkey_prefix":"e9dfe7e00b47","text":"LHPC meshcore test 1"}` | PASS |
| 6 | Peer acknowledged it | node log `RX ACK (3) len=4` | PASS |
| 7 | Peer → box direct message | node log `RX TXT_MSG (2) len=52` | PASS |
| 8 | Box acknowledged it | node log `TX 8 bytes (type=ACK, route=DIRECT)` | PASS |
| 9 | Advert from the box reaches the peer | peer event `ADVERTISEMENT {"public_key":"e9dfe7e00b47…"}`; its contact's `last_advert` jumped from 1788554754 to 1788598531 | PASS |
| 10 | Repeater retransmits | `RepeaterHandler: Retransmitted packet` for both the ACK (8 bytes) and the advert (120 bytes, 1131 ms airtime) | PASS |
| 11 | Web UI and repeater dashboard serve | 8788 → 200, 8000 → 200 | PASS |
| 12 | RF counters agree with the app layer | daemon 868: `RX=3 TXOK=2 TXERR=0 CADTIMEOUT=0` | PASS |

### Findings

1. **`meshcore-cli` reports failures for commands that actually succeed.** Both `msg` and `advert`
   returned `Error … {'reason': 'no_event_received'}` while the node log shows the work was done and
   the peer received it. `sync_msgs` and `recv` return nothing at all even though a message had just
   arrived and been acknowledged. The cause is visible in the node log: every CLI invocation opens a
   new companion session and **evicts the previous one** (`Companion already has a client; evicting
   previous connection`), then disconnects on `empty_read`. So the confirmation event and any queued
   message are lost with the evicted session. The protocol layer is sound; the one-shot CLI usage
   pattern is not. Anyone reading `meshcore-cli` output as ground truth will draw the wrong
   conclusion — the node log is the reliable source.
2. **The peer's USB port is not a companion interface.** The T-Deck Pro's serial output is boot and
   hardware logging only; a `meshcore` client on it fails with "are you sure your node is a serial
   companion?". BLE is the control path, and it worked first time with no OS pairing.
3. **Radio arbitration is correct and legible.** LHPC refused to start MeshCore while Meshtastic held
   868, named both the radio and the resource key, identified the holding stack and printed the
   command that resolves it. This is the behaviour that was missing from the earlier session, where
   the daemon simply vanished.
4. **868 transmits cleanly.** No CAD timeouts at all on this band, in contrast to MeshCom's
   experience on 433 — further evidence that MeshCom's problem is its channel-busy settings rather
   than the radio or the site.
5. **The peer carries stale contacts** (`CHEPortable`, `DJ0CHE-06`, `MC_Node` with adverts from
   earlier sessions). Harmless, but worth knowing when reading its contact list as test evidence:
   only a refreshed `last_advert` proves current reachability.

### Follow-ups (not done)

- Decide whether `meshcore-cli` should hold one persistent companion session for multiple commands,
  or whether LHPC should document that its one-shot output cannot be trusted (finding 1).
- Exercise the repeater's forwarding between two remote nodes; this run only proved it retransmits
  its own traffic.
- Channel (group) messaging and telemetry were not tested.

---

## Session close — state on the box (2026-09-05 11:00 CEST)

### Running now

| Stack | State | Note |
|---|---|---|
| `daemon` | running | both bands: 433 MANAGED for APRS, 868 MANAGED for MeshCore |
| `kiss` (KISS TNC) | running | `127.0.0.1:8001`, single client held by Graywolf |
| `graywolf` | running | web UI `127.0.0.1:8080`, provisioned, APRS-IS session up as DJ0CHE |
| `meshcore` | running | node `DJ0CHE-12` + repeater `Relay e293`, web UI 8788, dashboard 8000 |
| `meshtastic` | **stopped** | stopped deliberately to free 868 for MeshCore |
| `meshcom` | stopped | left stopped; it wants the daemon in DIRECT, which conflicts with Graywolf's MANAGED |
| everything else | stopped | as found |

**Radio budget.** 433 belongs to the APRS chain and 868 to MeshCore. Meshtastic and MeshCore cannot
both run — both want 868 — and MeshCom cannot share 433 with Graywolf because the two need opposite
daemon TX modes. Earlier in the session the daemon, the KISS TNC, Graywolf and Meshtastic did all run
together, which settles the earlier worry about a direct-SPI clash: the two *bands* coexist fine, it
is two stacks on the *same* band that cannot.

### Changes made on the box across the whole session (runtime only)

1. Meshtastic node factory reset, stack restarted, one poststart, four test messages.
2. Graywolf chain started (daemon, KISS TNC, Graywolf) — no configuration changed; the station's
   own non-default iGate settings were left exactly as the operator had them.
3. One APRS message and two triggered position beacons sent from Graywolf.
4. Meshtastic stopped to free 868, then the MeshCore stack started (daemon 868, GPS feed, node,
   web UI); one message and one advert sent from its node. No MeshCore configuration changed.
5. On the peers: the Meshtastic Station G2 was factory reset and given back its region and name;
   the CA2RXU tracker had its callsign set and nothing else. Both are documented in their sections.

No repository change and no code change at any point — the 0.2.9 branch belongs to another agent.

### Peers

Every peer is attached to the workstation, not the box. Four were used: the T-Deck on MeshCom, the
Station G2 on Meshtastic, the same T-Deck reflashed to CA2RXU for APRS, and the T-Deck Pro on
MeshCore. The first three share the single `/dev/ttyACM0`, so only one of them is connected at a
time; the Pro is reached over BLE and can stay connected alongside.

### Artefacts outside the repo

`live-test-2026-09-05/` holds the pre-reset configuration of both Meshtastic nodes, including the
**only** copy of the Spanish setup with a restore recipe, the tracker's pre-change CA2RXU
configuration, the helper scripts and the receive captures.

### Open items

1. **Graywolf's position beacon never fires** (section 3, finding 6) — the one real defect found.
2. **MeshCom's channel-busy threshold** — about half its queued transmissions ended in CAD timeout,
   while the APRS chain on the same band and the MeshCore chain on 868 transmitted cleanly. That
   points at `CADRSSI` against this site's noise floor, not at the radio or the TX mode.
3. **Meshtastic node-info rule** — document that a freshly reset node cannot be direct-messaged
   until node info has been exchanged, and that `lhpc stack poststart meshtastic` forces it.
4. **iGate policy for bench tests** — decide whether test traffic should reach the public network.
5. **`meshcore-cli` misreports command results** (section 4, finding 1) — decide between a persistent
   companion session and documenting that its one-shot output cannot be trusted.
6. **Why MeshCom and the daemon stopped** during the Meshtastic work. The daemon is back up; MeshCom
   is still stopped, and Meshtastic is now stopped deliberately so MeshCore can hold 868.
7. **Not reached in this run:** digipeating on the APRS chain, and MeshCore channel messaging and
   telemetry. Each stack's own follow-ups list the rest.
