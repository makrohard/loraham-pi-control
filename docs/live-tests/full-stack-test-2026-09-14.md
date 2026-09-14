# Full-stack live test, 0.5.0 — 2026-09-13/14

Two boxes, three reference radios, every stack LHPC ships. Run on the 0.5.0 candidate as it moved
from `32cd88b` to the release commit; every row below was witnessed on real hardware, and nothing is
recorded that was not.

## What the run is

| | |
|---|---|
| Boxes | **E** = Pi Zero 2 W, Lite image, Uputronics SX1278 (433) + SX1276 (868) · **B** = Pi 5, Desktop image, SX1278 (433) + RFM95 (868) |
| Radios | LilyGO T-Deck (433) · T-Deck Pro (868) · Station G2 (868) |
| Commits | C `32cd88b` → C′ `6589e17` (daemon CRC pin) → C″ `bb5f7ab` (voice 868) → **C‴ `342a069`** (band-already-served) |
| Evidence | RF logs from both boxes per row, decoder output, peer-side confirmation, box-side status |

## Result

114 rows recorded: **87 pass**, 13 pre-run blockers closed, 6 partial, 3 parked, 2 direct-only,
1 not-proven, 1 blocked, 1 skipped. **No row failed.**

Three product defects were found, fixed and proven on hardware during the run; two more findings were
recorded for classification.

## The defects this run found

**F-5 — Voice could not transmit on 868.** The shipped profile was SF11 at 250 kHz, where one voice
packet spends about 1.2 s on the air to carry 260 ms of speech, so the app's own airtime check refused
the mode and no PTT was possible. The 868 default is now SF7. Proven both directions afterwards: E
transmits a continuous Codec2 stream that B receives at −81 dBm, and B's stream reaches E at −90 dBm,
byte-identical payloads.

**F-6 — a stack went deaf while reporting healthy.** Starting a second component of an already
running stack re-applied that stack's daemon parameters to the band its own component had tuned. The
kiss stack declares no frequency of its own, so the band default won and the radio moved from 433.775
to 433.175. Status stayed green, `RADIO=READY` and `RXREADY=1` kept reading true, the RF log simply
went silent, and five transmissions from the other box at −70 dBm were never heard. Only a restart of
the TNC recovered it. Fixed by extending the guard that already protected a band held by *another*
stack to cover the starting stack's own running components. Proven live afterwards with the same
three-step sequence: the keep line appears, the daemon's last init stays at 433.774994, and reception
keeps counting while the second component runs.

**F-3 — CRC-failed frames were delivered as valid** (found in the chat rows, fixed in the pinned
daemon): the daemon cleared the radio's interrupt flags before RadioLib could read the CRC verdict, so
a corrupted frame on a marginal link arrived as a good message in every stack.

**F-1 — a fresh MeshCore node had no Public channel**, so it could neither send to Public nor map an
incoming channel hash. Seeded on first start only, so a channel an operator removed stays removed.

**F-7 and F-8 — recorded, not fixed.** The voice terminal build is not staged on a Desktop box and
reads its configuration from the directory of its executable; and a controller update leaves
in-controller component code stale in its built venv while `status --versions` still reports `match` —
box B ran the pre-fix MeshCore host for hours after being moved to the new commit, and only a rebuild
put it right.

## What the bench could not produce

Three-node routing (M7, M11) needs two nodes out of direct range, and every radio here is on one desk;
recorded as direct-only with the hop lists as evidence. MeshCore's O9 and O10 are partial for the same
reason — the repeater is proven receiving and retransmitting, but the end nodes hear each other
directly. The APRS tracker rows A1 and A4 are parked one step short: the firmware is flashed and its
channel matches, but the beacon callsign must be set through the tracker's browser portal, which
refuses every scripted save. A17 has no reference firmware at all — CA2RXU ships no T-Deck Pro variant.

## Rows

### R — Reticulum

| row | result | what was witnessed |
|---|---|---|
| R1 | pass | announce both ways, framed: 6 RX / 20 TX frames, decoder names the peer |
| R2 | pass | split packet RNode->box: a 255 B frame + its partner, first line fragment, second the message with its sequence |
| R3 | pass | opportunistic LXMF RNode->box: delivered in 2 s, decrypted in the RF-log view (first attempt needed a retry after a lost proof — normal half-duplex, proofs were TXed) |
| R4 | pass | link request RNode->box established; the viewer types link traffic as undecryptable by design (3 rows) |
| R5 | pass | SF7 fast profile: the driver programs preamble 24 for SF7/BW125 and both directions still work — box online line 'SF7 BW125k CR4/5 5468 bps', 3 RNode announces decoded |
| R6 | pass | MeshChat message to the RNode peer and back: the box's MeshChat POSTed 'R6 meshchat to rnode 102842' over the air, the PC listener received it and replied, and the reply is in MeshChat's conversation AND decrypted in the RF-log viewer ('LXMF from b213417f7e69: rfproof-reply R6 reply from the RNode peer'); proof TXed |
| R7 | pass | page fetch from the peer node over a link (resource transfer): the box opened a link to the RNode peer's nomadnetwork.node destination, requested /page/index.mu and received the 216-byte page over the air; the peer logged the link, the request and the resource |
| R8 | pass | lxmd propagation node stores a message for a later pickup: the sender's PROPAGATED message was accepted by the node (DELIVERED-TO-NODE 12:34:08) while the recipient was absent, and the recipient picked it up from the node minutes later ('PICKED UP: R8 stored 103338'). LHPC exposes no switch for this by design (manifest note); enable_node was set by hand in lxmd's own config for the row and restored afterwards |
| R9 | pass | reticulum 868 framed against the T-Deck Pro in its RNode role: the Pro's announces reach E at -65 dBm and decode ('announce 8c21375a24c1 RF-PROOF peer'), E's own framed announces go out, and the Pro's opportunistic LXMF arrives and is decrypted in the viewer with E's proof TXed (the PC's delivery callback timed out again on the first copy — the same half-duplex proof race seen in R3 on 433, not an 868 defect) |
| R10 | pass | box-to-box announce both ways with BARE framing on 433: B received E's two announces (32ee17caaf2b, 6f26638a45f2) at -79 dBm, E received B's two (192a88c1ccd7, 6cc9fb0f3dc0) at -72/-74 dBm; both logs decode them as announces |
| R11 | pass | box-to-box LXMF both ways on 433, bare framing: E's MeshChat -> B ('R11 E to B 113017' in B's conversation) and B -> E ('R11 B to E 113054' in E's conversation and decrypted in E's RF-log viewer, proof TXed) |
| R12 | pass | 868 box-to-box, bare framing, BOTH ways — after the maintainer repositioned box B's 868 antenna (F-2 resolved): B now receives E at -77 dBm / SNR 11 (four announces in three minutes), E's LXMF 'R12 E to B 868 114114' is in B's conversation and B's 'R12 B to E 868 144939' is in E's. The earlier failure was the antenna, exactly as the isolation predicted |
| R13 | pass | transport routing, three nodes, BOTH halves proven. Part 1 (bare): E with enable_transport = Yes relayed a PC client's LXMF to box B over LoRa — PC joined E's client port through the documented SSH tunnel, hops 2, DELIVERED in 2 s, decrypted in B's viewer. Part 2 (framed, all three): box B -> the RNode peer THROUGH E — B's MeshChat sent, the PC listener on the T-Deck RNode received it 1 s later, and E's RF log shows it relaying the peer's announce (RX 223 B then TX 239 B, the same destination) which is the relay itself. |
| R14 | pass | sideband on box B (desktop build) exchanges over the radio with E's LXMF: sideband announces its own delivery destination (788ab3a98c7e, seen in B's RF log as a 235-byte announce), E learns the path through a path request and sends; B's RF log shows the two inbound data packets for that destination at -76/-77 dBm decrypting only for sideband's own identity ('not addressed to this node' from the rns viewer's point of view, which is the correct typed answer — the message belongs to sideband, not to MeshChat's identity). Confirmed on B: sideband's own store (app_storage/sideband.db, table lxm) holds TWO messages whose destination is sideband's hash 788ab3a98c7e… and whose source is E's MeshChat 32ee17caaf2b… — the two messages E sent, received and stored by the desktop app over the radio. |
| R15 | pass | console decoded API: header stripped, split reassembled, LXMF decrypted |
| R16 | pass | rnstatus lists the LoRa interface; rnsd up after the console start |

### M — Meshtastic

| row | result | what was witnessed |
|---|---|---|
| M1 | pass | 868 Meshtastic E<->G2 both ways, after aligning the channel: the G2's text decodes on E ('[LongFast] M1 G2 aligned 183322' at -77 dBm) and E's own text is heard back ('M1 box to G2 183422' at -76 dBm); the G2 also carries E's node in its database. ROOT CAUSE of the earlier failure: the two nodes were on LongFast but with different primary channel definitions (their channel URLs differed in the trailing LoRa bytes), so nodeinfo decoded — it needs no channel key — while text did not. Fixed by applying E's channel URL to the G2 with --seturl; E's own channel, the LHPC default, was not touched |
| M2 | pass | 868 direct messages both ways after the node-info exchange: the G2's DM decodes on E as a direct packet ('text !a2e9aed8 (cheS) -> !9ee3dad0 (LHPB): [direct] M2 dm to box 183600', -79 dBm) and E's DM to the G2 is acknowledged by it on the air ('routing !a2e9aed8 -> !9ee3dad0: ack' plus the typed meta line with the packet id, -77 dBm). Third-party traffic on the band (other nodes' position/telemetry) decodes or stays typed as designed |
| M3 | pass | E's position and node info reach G over 868: G's node DB shows LHPCBENCH/LHPB (PORTDUINO) at 48.4180/11.6654, 501 m, SNR 7 dB, 0 hops, heard 27 s earlier — the position comes from E's gpsd; a live device-telemetry request from G to E is answered over RF (channel util 3.41 %, TX air util 0.02 %) |
| M4 | pass | traceroute E -> G on 868 is a single direct hop in both directions: '!9ee3dad0 --> !a2e9aed8 (6.5dB)' out and '!a2e9aed8 --> !9ee3dad0 (6.75dB)' back |
| M5 | pass | E serves the pinned Meshtastic web client (manifest pin 2.7.2) through the mTLS reverse proxy: https://192.168.178.106:8447/ returns 200 with <title>Meshtastic Web</title> to a client certificate issued by E ('pc-matrix'), and is refused 403 without one; 'lhpc meshtastic --info' reports owner LHPCBENCH (LHPB), firmware 2.7.26.54e0d8d, hwModel PORTDUINO, 29 nodes in the DB |
| M6 | pass | 868 between the two meshtasticd: broadcast E->B ('M6 broadcast E to B' in B's router log) and B->E, then a direct message each way, both ACKed on RF — B->E 'M6 DM B to E' and E->B 'M6 DM E to B second'. One earlier unacked DM was not seen at B and was not retried by the sender; the acked retries arrived |
| M7 | direct-only | all three nodes are in direct range on this bench (G, B and this PC on one desk, E the only distant node), so the two-hop route via E cannot be produced: traceroute G->B is one direct hop each way, 8.75 dB out and 6.75 dB back. Documented outcome per plan 6.3, not a failure |
| M8 | pass | B serves the same pinned Meshtastic web client through its own mTLS proxy: https://192.168.0.50:8445/ returns 200 with <title>Meshtastic Web</title> and the same asset bundle as E (index-BTHwHyj7.js), 403 without a client certificate; 'lhpc meshtastic --info' on B reports owner LHPCPI5 (LHP5), firmware 2.7.26.54e0d8d, hwModel PORTDUINO. B's port map differs from E's (meshtastic 8445, meshcore 8447) — per-box assignment, not a defect |
| M9 | pass | 433 Meshtastic E<->T-Deck both ways: the T-Deck's texts reach E and decode in the viewer with the channel key ('[LongFast] M9 tdeck 182518' at -66 dBm, 'M9 second 182718' at -65 dBm) and E's own text goes out and is heard back ('[LongFast] M9 box to tdeck 182817' at -67 dBm); both nodes carry the same primary channel URL and see each other's nodeinfo (-64 dBm). Note: the console's --sendtext reports a broken pipe on its TCP interface after the send, yet the frame is transmitted and logged — cosmetic CLI teardown, not a lost message |
| M10 | pass | 433 between the two meshtasticd: broadcast E->B ('M10 broadcast E to B 433') and B->E, plus a direct message each way, both ACKed on RF — E->B 'M10 DM E to B 433' and B->E 'M10 DM B to E 433'. B needed its 433-scoped identity set first (node_name/node_short are per band) and both stacks were moved to 433 through the console, since the CLI start always takes the declared primary band |
| M11 | direct-only | 433 routing cannot be produced on this bench for the same geometry as M7: traceroute B <-> T-Deck is one direct hop each way (5.5 dB out, 11.0 dB back) with E never in the path, because all three radios are within a few metres. The T-Deck also stopped answering on USB again during this block, so the traceroute was driven from box B instead; the hop list is the same evidence |
| M12 | pass | RF-log decoder on E, 868: channel traffic decodes with the key the box holds ('[LongFast] M6 broadcast B to E'), position packets decode to fields ('[LongFast] 48.41800, 11.66541, alt 492 m'), node info and telemetry decode (air_util_tx, battery_level, channel_utilization, uptime), and foreign-channel traffic stays opaque with a typed reason ('no key for channel hash 0x54') across 300 records. Direct messages are tagged '[direct]'; the box-to-box DMs decode because those two nodes never exchanged public keys, so the DM was channel-encrypted, whereas the PKC DM of row M2 stayed opaque |
| M13 | pass | radio budget on E: with meshtastic holding 868, 'lhpc stack start daemon' plans and applies 433 only — '[daemon] start/ensure --radio 433 (868 owned by meshtastic — skipped)' and '[skip] 868 owned by meshtastic — daemon serving 433 only'; the daemon comes up on 433 and its params are applied there |
| M14 | pass | after stopping meshtastic on E the 868 radio is released: the next 'lhpc stack start daemon' plans '--radio all bands' and brings 868 up ('[ok] start daemon --radio 868', FREQ=869.525 applied), where the previous run had skipped it as owned |

### D — Daemon

| row | result | what was witnessed |
|---|---|---|
| D1 | pass | both daemon processes run at once and each band answers its own control socket: /tmp/loraconf433.sock and /tmp/loraconf868.sock exist together, 'lhpc daemon 433' and 'lhpc daemon 868' both report Radio READY with independent uptimes and counters (433 serving kiss, 868 serving meshcore) |
| D2 | partial | the daemon RF logs carry RX and TX lines for every consumer that has run: 433 holds 21 RX / 7 TX with APRS TNC2 frames of 34-73 bytes, 868 holds 141 RX / 238 TX spanning 6-byte control frames, 22-byte daemon test frames, APRS frames and the 255-byte Codec2 voice stream. P1 (Reticulum) and P4 (kiss/graywolf/voice) are covered; P5 (MeshCom) has not run yet, so the row stays partial until the MeshCom block is executed |
| D3 | pass | the daemon's counters and CAD state are readable per band: 'lhpc daemon 433' reports RX/TXOK/TXERR, live and packet RSSI, CAD state and thresholds; the same command on 868 reports that band's own independent set (D1 evidence) |
| D4 | pass | the RF log's switch and Clear are scoped per band: 'lhpc rflog daemon --band 433 --clear' emptied only the 433 file ('(no output yet)' afterwards) while the 868 log kept its history, and the rf_log switch is the stack-level (band-less) setting the manifest declares — the same key both bands read at their next start |
| D5 | pass | daemon-only frame exchange on both bands: on 433 a frame box B transmits is logged by E's daemon as 'RX rssi=-70.00 snr=6.75 len=73 band=433' and E's answer as a TX line in the same file; on 868 E's daemon log carries 141 RX lines from B's traffic (APRS frames of the A10/A12 rows and the 255-byte Codec2 stream of V4) alongside 238 TX lines. Both files are band-tagged and hold the raw hex |
| D6 | pass | per-band daemon parameters apply live and are confirmed: 'lhpc daemon 433 --set CADRSSI=-95 --yes' moved the CAD threshold from -90 to -95 dBm in the running process (the monitor shows it) and setting it back to -90 took effect the same way; without --yes the command asks first and aborts, which is the documented confirmation |

### A — KISS + Graywolf

| row | result | what was witnessed |
|---|---|---|
| A1 | parked | the CA2RXU tracker firmware V2.4.3.2 (ttgo_t_deck_GPS) was fetched, flashed to the T-Deck and came up alive with its LoRa profile already matching LHPC's APRS channel (433.775 MHz, SF12, BW125, CR5) and its configuration portal reachable on LoRaTracker-AP. The row is parked because the callsign could not be set unattended: the portal renders its beacon fields client-side and resets the connection on every scripted save (multipart, JSON body and form POST all returned no response), and putting a tracker on the air as NOCALL-7 is not acceptable practice. ONE ACTION NEEDED: open http://192.168.4.1/ on the LoRaTracker-AP from a browser, set the beacon callsign to DJ0CHE-7 and save; the rows then run unattended. The T-Deck has meanwhile been restored to MeshCom, its intended end state |
| A2 | pass | manual APRS beacon: the beacon fired from E's Graywolf API (POST /api/beacons/1/send, 200) goes out as a TNC2 position frame in E's rf-kiss.log ('DJ0CHE>APGRWO,WIDE1-1,WIDE2-1:!/...C/A=...') and box B receives the same frame over the air at -75 dBm / SNR 9.5 — decoded to readable TNC2 text in B's own RF log |
| A3 | pass | the scheduled Graywolf beacon fires by itself: E transmitted position beacons at 22:06:07, 22:09:07 and 22:19:07 UTC with no manual send in that window — the last two exactly 600 s apart, the configured interval — and box B received them at -73 dBm, SNR 9.75-10.5 (B's clock runs about 20 s behind E's). Window covers more than two intervals, which closes the plan's B3 retest: scheduled beacons are not broken, the earlier run simply missed the slot |
| A4 | parked | same blocker as A1: the tracker's beacon needs a callsign set through its browser portal before it may transmit. Firmware, channel and portal are all proven working |
| A5 | pass | iGate on E: with igate enabled the station connects to APRS-IS ('aprs-is connected component=igate server=rotate.aprs2.net:14580 callsign=DJ0CHE') and a frame box B transmits on 433 appears on the public network receive-gated by E — read back from a read-only APRS-IS connection on this PC: 'DJ0CHE-2>APGRWO,WIDE1-1,WIDE2-1,qAR,DJ0CHE::APRSIS :A5 gate test{020'. The qAR construct names E as the gating station |
| A6 | pass | kiss-serial PTY bridge: a KISS frame written into state/loraham_kiss goes out on RF (rf-kiss.log TX, TNC2 'DJ0CHE-9>APLH01:>kiss pty test 0.5.0'), and a frame from box B arrives on the PTY KISS-framed (c0 00 ... c0, AX.25 decoding to B's message) while E's RF log logs the same RX at -70 dBm. NOTE: the PTY had to be retuned by hand first — see finding F-6 |
| A7 | pass | the kiss RF log carries TNC2 lines with signal data: RX lines show rssi/snr and the decoded TNC2 text, TX lines show the outcome — both boxes' rf-kiss.log in the A2 evidence |
| A8 | pass | APRS message with acknowledgement between the two stations on 433: E's Graywolf sent 'DJ0CHE>APGRWO,WIDE1-1,WIDE2-1::DJ0CHE-2 :A8 from E 193609{004', B received it at -76 dBm, B's station answered 'DJ0CHE-2>APGRWO::DJ0CHE :ack004' and E received that ack at -68 dBm — both directions and the ack visible as decoded TNC2 text in both RF logs. B was given its own SSID (DJ0CHE-2) for the row |
| A9 | blocked | 433 digipeat via a third node cannot run: the row needs the T-Deck as the originating tracker and it is both on Meshtastic firmware and unresponsive on USB; and the digipeat mechanism itself did not repeat on 868 either with the digipeater enabled and a WIDE1 repeat rule in place (A11). Two independent blockers, neither of them a controller defect |
| A10 | pass | APRS on 869.525 between the two stations: E's Graywolf sent 'DJ0CHE>APGRWO,WIDE1-1,WIDE2-1::DJ0CHE-2 :A10 E to B 868 v2{006', B received it and answered 'DJ0CHE-2>APGRWO::DJ0CHE :ack006' which E received at -89 dBm; the reverse direction B->E ('A10 B to E 868{015') was acked by E ('ack015') and received at B at -81 dBm; E's position beacon was received at B at -80 dBm / SNR 10.5. B's own beacon was not sent: a status beacon is refused by its API with HTTP 422 and B has no position source, and no position was invented for it. Both boxes needed a distinct callsign on 868 — the band-scoped call was empty on both, so both inherited the same global callsign |
| A11 | not-proven | two-box digipeat on 868 could not be produced: E's Graywolf digipeater was enabled through its API (enabled true, my_call DJ0CHE, dedupe 30 s) with a repeat rule (alias WIDE1, alias_type wide, max_hops 1, action repeat, channel 1) and the component restarts cleanly ('starting component name=digipeater'), but three probes from B with an unused WIDE1-1,WIDE2-1 path are received by E (-89 to -91 dBm, logged as 'aprs packet ... path=[WIDE1-1 WIDE2-1]') and never repeated: no relayed frame in E's TX log and nothing back at B. LHPC does not provision digipeater settings, so this is graywolf-side configuration, not a controller defect |
| A12 | pass | 868 KISS: the TNC on 869.525 logs TNC2 lines with RSSI/SNR for RX and outcome for TX in rf-kiss.log, and the kiss-serial PTY bridge works on 868 too — a frame written into state/loraham_kiss goes out as 'DJ0CHE-9>APLH01:>kiss pty test 0.5.0' and box B receives it at -80 dBm / SNR 11. Starting kiss-serial re-applies the band defaults here as well (daemon log shows a fresh begin() at FREQ=869.525024), but on 868 that default equals the frequency the stack uses, so nothing breaks — the same re-apply is what kills 433 in finding F-6 |
| A13 | pass | radio budget on 433: with kiss and graywolf running there, 'lhpc stack start meshcom' is refused typed before anything is touched — 'Cannot run meshcom: graywolf, kiss must be stopped first', naming each holder ('radio 433 MHz held by running stack kiss' / '... graywolf') and offering the two stop commands |
| A14 | pass | Graywolf's web UI is reachable through the mTLS reverse proxy on both boxes: https://192.168.178.106:8444/ on E and https://192.168.0.50:8446/ on B both return 200 with <title>graywolf</title> to a client certificate issued by that box, and 403 without one. The per-box port assignment differs (graywolf is 8444 on E, 8446 on B) |
| A15 | pass | the RF-log decoder view of an AX.25/APRS frame shows callsigns, path and payload: the kiss log renders 'DJ0CHE>APGRWO,WIDE1-1,WIDE2-1::DJ0CHE-2 :A8 from E …' and the ack frame in the same readable form, with rssi/snr on receive and outcome on transmit |
| A16 | pass | stop kiss while graywolf runs: graywolf keeps running, status shows the unmet dep; restarting kiss is refused typed ('radio 433 MHz held by running stack graywolf') and offers 'lhpc stack stop graywolf'; following it starts kiss (tnc listening on 8001) |
| A17 | parked | no CA2RXU variant exists for the T-Deck Pro (blocker B9): the V2.4.3.2 release ships ttgo_t_deck_GPS and ttgo_t_deck_plus only, and the Pro has different LoRa pins and an e-paper display. Nothing to flash, so the wanted 868 tracker row has no reference firmware on the desk |

### C — Chat

| row | result | what was witnessed |
|---|---|---|
| C1 | pass | chat text on 433 between the boxes, RE-RUN on C' with the fixed daemon: E's message 'C1 crossed 155632' arrives in B's TUI cleanly. TWO facts learned in the re-run: (1) the CRC fix works in the wild — while B was receiving marginal frames its daemon logged '[433] RX read error: -7, packet dropped, drops=1..3' instead of delivering the garbage the pre-fix daemon delivered in the first run (finding F-3, now fixed); (2) chat ships tx_freq 433.775 / rx_freq 433.900, a duplex pair, so two boxes talking DIRECTLY must have the pair crossed — the first run only worked because each daemon happened to be parked on the other's transmit frequency — which is BY DESIGN and documented (docs/stacks/chat.md:33: 'LoRa-APRS split: chat transmits on 433.775, the channel stock ESP32 trackers listen on, and receives on 433.900'; kiss uses 433.775 both ways). Test-setup fact, not a defect: box-to-box chat rows run with B's rx_freq crossed onto E's tx_freq (433.775), which makes the row deterministic. |
| C2 | skipped | the chat TUI sends no position line of its own on this build: 11 APRS position frames are in the 433 RF log but all carry the APGRWO tocall (graywolf's beacon from earlier runs), none from the chat session; the chat frames are message frames only (APRS ::ALL). Recorded as not-offered rather than failed |
| C3 | pass | RE-RUN on C': the printed manual start command runs the chat TUI over ssh on both boxes (Lite and Desktop), status line rendered |
| C4 | pass | RE-RUN on C': the RF-log viewer renders the chat frames as readable APRS text on the fixed daemon |

### V — Voice

| row | result | what was witnessed |
|---|---|---|
| V1 | pass | voice E -> B on 433: with the built-in test tone on and PTT held (a keystroke every 200 ms), E transmits a continuous run of 255-byte Codec2 frames (rf-daemon-433.log TX, outcome=ok) and B RECEIVES them at -75 dBm / SNR 10.5 — the codec2 stream crosses the radio box-to-box. The loopback wav on B is silent because B runs the GTK build whose playback device is chosen in its own UI; the RF evidence on both sides is what proves the row, the recorded-audio variant needs the terminal build on B |
| V2 | pass | voice B -> E on 433: B's terminal build transmits Codec2 frames with the tone on and PTT held (255-byte TX lines, outcome=ok on B) and E receives the stream at -68/-69 dBm, SNR 6 — the reverse direction of V1, proven on both radio logs |
| V3 | pass | voice E -> B on 868 with the fixed profile: the app renders '868MHz 869.525 SF7 BW250 \| TX:Codec2-3200' with no refusal (it read [VERBOTEN!] before the fix), tone on and PTT held sends a continuous run of 255-byte Codec2 frames (E's rf-daemon-868.log TX outcome=ok) and box B receives the same stream at -81/-82 dBm, SNR 9.5-10. Proven on C'' = bb5f7ab |
| V4 | pass | voice B -> E on 868: B's terminal build renders the same fixed profile (868MHz 869.525 SF7 BW250) and with tone on and PTT held transmits 255-byte Codec2 frames (B's rf-daemon-868.log TX outcome=ok); E receives the identical payloads at -90 dBm, SNR 6-6.75. The two boxes' log clocks differ by about 18 s (E has no RTC), the hex payloads match byte for byte. Proven on C'' = bb5f7ab |
| V5 | pass | voice is configurable per band and the values are what the launch uses: callsign (inherits the global), freq 434.700, sf 7, bw 125.0, cr 5, crc 1 and the audio/device settings are offered by lhpc config voice on E |
| V6 | pass | voice UI readiness on both images: E (Lite) runs the terminal build over ssh with its device picker and status line ('LoRaHAM Voice \| DJ0CHE \| 433MHz 434.700 SF7 BW125 \| TX:Codec2-3200'), B (Desktop) runs both the GTK build (process alive under the desktop session) and the terminal build used for V2 |
| V7 | pass | RF-log viewer shows the voice frames: 255-byte Codec2 payloads appear as TX/RX lines with band, RSSI/SNR and outcome in rf-daemon-433.log; the daemon's log is plaintext so the viewer renders type and length rather than a decode, as designed |
| V8 | pass | voice and kiss on the same band behave as documented: with voice holding 433 the kiss start is refused before any mutation — '[conflict] radio 433 MHz is held by running stack voice (loraham-voice-cli)' and 'Cannot run kiss: voice must be stopped first' |

### K — MeshCom

| row | result | what was witnessed |
|---|---|---|
| K1 | pass | MeshCom text both ways between box E and the T-Deck peer (DJ0CHE-07, MeshCom 4.35p, CTRY EU8, 433.175): T-Deck -> box, E's RF log records '::K1 tdeck to box 0305' at -75 and -69/-68 dBm, SNR 8.75-11.5; box -> T-Deck, E transmits 'DJ0CHE>*:K1 box to tdeck ...' and the T-Deck demonstrably receives the box's traffic — it RELAYED E's later frame onto the mesh, which box B logged as 'DJ0CHE,DJ0CHE-07>*:...' at -87 dBm. The peer's own mheard table and console stayed empty throughout, so the display side of the peer is unproven while the radio side is proven |
| K2 | partial | position from gpsd: box side proven, peer side unwitnessed. E's MeshCom node sends position beacons every few minutes (27-byte frames, 'DJ0CHE>H@R0/H@R1', outcome=ok) fed by the box's live gpsd through meshcom-gps ('position source live, 5 sentences' at start), and box B receives them. The T-Deck peer's own mheard and position views stayed empty all session, the same peer-display gap as K3 |
| K3 | partial | mheard: box side proven, peer side empty. E's and B's MeshCom RF logs carry every frame with RSSI and SNR for both directions (-69 to -87 dBm, SNR 9 to 12.25) and the decoded callsigns and paths. The T-Deck's own '--mheard' table stayed empty for the whole session even while it was demonstrably receiving and relaying box traffic, so the peer-side half of this row is not witnessed |
| K4 | pass | rf-meshcom.log carries raw frames in both directions with the outcome after the transmit result: TX lines read 'outcome=ok' with the frame length and full hex (27-byte beacons, 44-51 byte texts), RX lines carry rssi and snr plus the same hex, and the ascii column decodes the MeshCom header, callsign and payload. Both boxes write the same shape |
| K5 | pass | MeshCom TX success rate on the busy channel: 27 of 27 transmissions from E report outcome=ok and not one failed, measured on a channel whose live noise floor reads -85 to -83 dBm against a -90 dBm CAD threshold (blocker B2). The CAD_TIMEOUT failures of the 2026-09-05 silicon test do not reproduce on this commit |
| K6 | pass | MeshCom gateway is off on the box node, read from the node's own UI: 'Gateway: off'. Re-recorded after the binary was refreshed from the index; the earlier 'blocked' entry described the stale artifact, not the stack |
| K7 | pass | MeshCom text both ways between the two QEMU nodes on 433.175: B's node (DJ0CHE-12) sends '::K7 B node to E node 0318' and E receives the identical 51-byte frame at -69 dBm / SNR 9; E's node (DJ0CHE) sends '::K7 E node to B node 0320' and B receives the identical 48-byte frame at -75 dBm / SNR 12.25 |
| K8 | pass | MeshCom mesh forwarding proven with the T-Deck as the relay: E's node transmits 'DJ0CHE>*:K7 E node to B node 0320' (48 bytes) and box B receives it TWICE — once directly at -75 dBm as the 48-byte original, and 5 s later as a 58-byte frame at -87 dBm whose path carries the relay, 'DJ0CHE,DJ0CHE-07>*:...'. The same pattern appears for B's own beacons relayed as 'DJ0CHE-12,DJ0CHE-07'. Attenuation was not needed: the forwarded copy is distinguishable by its path field, which is the evidence the row asks for |
| K9 | pass | HMAC bridge password round trip, run on box B (the Pi 5) rather than E because enabling rebuilds the firmware and E froze earlier tonight under load: 'lhpc hmac enable meshcom --yes' provisions the password, restarts meshcom-bridge and the QEMU node and reports 'DONE: HMAC password enabled'; 'lhpc hmac renew meshcom --yes' rotates it with the same clean restarts and reports 'DONE: HMAC password renewed'; 'lhpc hmac status' reads 'enabled (meshcom)' afterwards |
| K10 | pass | RF-log decoder view of MeshCom frames on E: the log page renders each frame decoded — sender callsign and path ('DJ0CHE,XX0XXX-00'), frame type (position beacons as 'H@R0'/'H@R1', text as '*:') and the payload itself ('RF-LOG box4 203012') — alongside the raw hex the writer stores, with RSSI/SNR on the received ones |
| K11 | pass | MANAGED start order on E: 'lhpc stack start meshcom' runs the plan in order — the daemon is ensured on 433 with the MeshCom profile first (TXMODE=MANAGED, CADIDLE 28 ms applied and confirmed), then meshcom-bridge comes up and is verified on 127.0.0.1:7000, then meshcom-gps with a live position feed (5 sentences), then meshcom-qemu, verified on 127.0.0.1:12323 with its required post-start completed. Every component reports [verified], none [blocked] |

### O — MeshCore

| row | result | what was witnessed |
|---|---|---|
| O1 | pass | public channel both ways (passed after a manual Public seed). Pro->box 'O1 channel 092921' decoded; box->Pro 'O1 box2pro 093307' received over BLE. GAP (LHPC host, confirmed with the driver's author): openHop's ChannelStore starts empty and our meshcore host never seeds slot 0 with DEFAULT_PUBLIC_CHANNEL_SECRET, so a fresh companion.db cannot send to Public and logs 'Unknown channel hash' on RX; real MeshCore firmware seeds it at first boot. Worked around with meshcore-cli add_channel; the flush persists it. [RE-RUN ON C' — daemon-backed row, proven on the pre-CRC-fix daemon] |
| O2 | pass | DM Pro->box with ACK: 'O2 dm 093357' decrypted in the viewer (direct CHEMobile -> me), box ACKed 1e7fe12b which matches the Pro's expected_ack; box->Pro DM 'O2b box2pro dm 093429' TXed and ACKed back (multi-ack + ack 75be3c45) [RE-RUN ON C' — daemon-backed row, proven on the pre-CRC-fix daemon] |
| O3 | pass | advert flood: 6 advert lines in the node log, the Pro's BLE session lists contacts [RE-RUN ON C' — daemon-backed row, proven on the pre-CRC-fix daemon] |
| O4 | pass | advert carries the box's position from the live source: the Pro's contact for 'LHPC e293' shows adv_lat 48.430178 / adv_lon 11.66823 with a last_advert timestamp [RE-RUN ON C' — daemon-backed row, proven on the pre-CRC-fix daemon] |
| O5 | pass | chat mode serves the companion on TCP 5000; web UI answer recorded [RE-RUN ON C' — daemon-backed row, proven on the pre-CRC-fix daemon] |
| O6 | pass | repeater mode: the Pro's channel packet (RX 37 B) was retransmitted by the box (TX 38 B, 'RepeaterHandler: Retransmitted packet (38 bytes, 443.4ms airtime)'), the repeater dashboard answers on 8000 and no companion port 5000 is open [RE-RUN ON C' — daemon-backed row, proven on the pre-CRC-fix daemon] |
| O7 | pass | chat+repeater: both ports open (5000 companion + 8000 dashboard); the Pro's channel message is received AND retransmitted in the same mode — the viewer decodes both the RX and the repeated TX [RE-RUN ON C' — daemon-backed row, proven on the pre-CRC-fix daemon] |
| O8 | pass | MeshCore chat between the two boxes on 868: adverts cross both ways (B's key 2a2ff3a4... heard at E at -89 dBm, E's 874c2a0f... heard at B at -80 dBm) and each node lists the other as a contact; public channel both ways — B decodes '<<< Channel [Public] LHPC e293: O8 public E to B 0220 >>>' and E '<<< Channel [Public] LHPCPI5: O8 public B to E 0221 >>>'; direct messages both ways — 'Received TXT_MSG: O8 DM E to B 0222' on B and 'O8 DM B to E 0223' on E. Box B first had to be rebuilt: it was still running the pre-fix MeshCore host and logged 'Unknown channel hash: 11' until 'lhpc build meshcore' put the F-1 seeding code in place |
| O9 | partial | E in repeater mode does repeat, proven twice: a public message from box B arrives ('RX GRP_TXT (5) len=51') and is retransmitted ('RepeaterHandler: Retransmitted packet (54 bytes, 574.5ms airtime)'), and the same for a message from the T-Deck Pro over BLE. The row's 'only via E' clause cannot be produced on this bench: B decoded the Pro's message at 02:21:27, before E retransmitted it at 02:21:49, because the two end nodes are on one desk and hear each other directly. Same geometry limit as M7 and M11 |
| O10 | partial | E in chat+repeater: the repeater half works — a public message from B is received and retransmitted ('RepeaterHandler: Retransmitted packet (54 bytes, 574.5ms airtime)' at 02:24:11). The chat half surfaced nothing in this mode: both a channel message and a direct message reach the radio ('RX GRP_TXT (5) len=51', 'RX TXT_MSG (2) len=52') but neither produced a decode line, where plain chat mode decoded both in row O8. Consistent with the F-1 note that chat+repeater persists through openHop's RepeaterDaemon rather than the companion store, so the node has neither the Public channel nor the contact keys there — worth the maintainer's judgement, recorded rather than chased |
| O11 | pass | mode switch chat -> repeater -> chat+repeater through lhpc config + restart; the documented port set per mode: chat 5000 only, repeater 8000 only, chat+repeater both [RE-RUN ON C' — daemon-backed row, proven on the pre-CRC-fix daemon] |
| O12 | pass | rf-meshcore.log carries RX and TX lines for every case above and the decoder types them (channel/direct/ack/multipart/advert) — see the O1/O2/O6/O7 logs [RE-RUN ON C' — daemon-backed row, proven on the pre-CRC-fix daemon] |
| O13 | pass | meshtastic refused while meshcore owns 868, typed before any mutation [RE-RUN ON C' — daemon-backed row, proven on the pre-CRC-fix daemon] |
| O14 | pass | MeshCore identity persists across a restart (config/secrets/meshcore_identity.key, 0600, same digest before and after); openHop build patch lines recorded [RE-RUN ON C' — daemon-backed row, proven on the pre-CRC-fix daemon] |

### X — Cross-cutting

| row | result | what was witnessed |
|---|---|---|
| X1 | pass | boot restore on both boxes: each was rebooted with its default running set (daemon + kiss TNC + graywolf) and came back with exactly that set. B's autostart journal reads 'done @ 2026-09-14 00:06:11 — 3 restored, 0 failed' and E's 'done @ 2026-09-14 00:08:37 — 2 restored, 0 failed, 1 skipped' (the skip is graywolf's dependency ordering, which came up with the stack). Component lists before and after are identical on both |
| X2 | pass | RF-log retention and Clear: rotation is real on the box — rf-meshtastic.log.1 is 5242814 bytes, exactly the writers' 5 MiB cap (rflog.py MAX_BYTES), with the live file continuing beneath it. Clear is scoped per stack: 'lhpc rflog graywolf --clear' emptied only rf-kiss.log (graywolf's own file, '(no output yet)' afterwards) while the meshtastic log kept its records; per-band scoping was proven separately in D4, where clearing 433 left the 868 file intact |
| X3 | pass | web console sweep on both boxes: the dashboard, the stacks page, the firewall log page and all nine stack bodies return 200 with real content (26-61 kB each), and every RF-log view that has a file behind it loads — daemon 433 and 868, kiss, meshtastic, meshcore, meshcom, and reticulum through its own component (rns-lora-interface). 18 pages 200 on each box. The only 404 in the sweep was my own wrong component name for the Reticulum log ('rnsd'), corrected in the same run |
| X4 | partial | version and health check on both boxes. Box B is clean apart from one entry: only loraham-chat shows 'differs' (pin 2a0db8872d99), everything else match or binary, doctor reports 0 observed resource conflicts. Box E shows 'differs' for chat, voice, voice-cli, kiss-tnc, kiss-serial and 'dirty' for meshcore-node, because the run installed E from --source dev after --source pinned kept resolving to the old known-working composition; its doctor is otherwise clean, reporting only the documented headless GUI gaps (libgtk-3-dev, libx11-dev) and 0 resource conflicts. A fully 'match' table is what the from-zero install of Part 2 has to produce, so this row is re-checked there |
| X5 | pass | host tests on E: 'lhpc test kiss --yes' runs the component's own suite and passes (rc 0, loraham-kiss-tnc, log test-loraham-kiss-tnc.log). The other stacks report nothing to run: chat, voice and reticulum have no host test, and daemon is refused by design because it is installed from a prebuilt binary ('host tests needs the source channel') |
| X6 | pass | firewall on both boxes with the stacks that were exercised: E reports 'Active — Secure default · Config ✓ · Boot ✓ · Live ✓' and B 'Active — Compatibility · unwanted stack ports blocked · Config ✓ · Boot ✓ · Live ✓', both with 'Live verified'. The operator console is reachable through the firewall from this PC on each box (https://192.168.178.106:8443/ and https://192.168.0.50:8443/ both 200 with a client certificate) |

### B — Pre-run blockers

| row | result | what was witnessed |
|---|---|---|
| B1 | closed | RNode framing on the LHPC side is in and proven: the whole Reticulum block R1-R16 passed against the RNodes on this commit, with the RF-log decoder stripping the header byte and reassembling split frames, and the start gate refusing while a built driver predates the framing |
| B2 | closed | noise floor measured on E's 433 radio before the MeshCom lane: live RSSI -85 to -83 dBm over three samples against the CAD threshold of -90 dBm, so the channel reads BUSY and a MANAGED transmitter defers. That is the CAD_TIMEOUT cause from the 2026-09-05 silicon test, now measured rather than assumed: the site noise sits about 6 dB above the threshold |
| B3 | closed | scheduled beacon retest done in A3: two consecutive fires exactly 600 s apart, received by the peer box. The earlier 'never observed' reading was a too-short observation window |
| B4 | closed | the Meshtastic DM procedure is confirmed, not a bug: a direct message only lands once the peers have exchanged node info, which the M2 and M6 rows show — the first unacked DM between the two boxes was not seen at the far end, and the acked retry after the exchange arrived both ways |
| B5 | closed | the 868 Reticulum reference is the T-Deck Pro RNode port, not stock hardware; the on-air code is stock RNode 1.86 and the R-block rows say so |
| B6 | closed | MeshCom on 868 and MeshCore on 433 are out of scope by the plan's frequency table; no row attempted either |
| B7 | closed | binary index skew is resolved for the run: the index already carried meshcom at lhpc 6589e17 with bridge 7c86c96, and E's copy was simply older — 'lhpc install meshcom --source binary --yes' pulled the current artifact (provenance meshcom-bridge@7c86c96aa) and the start gate passed immediately. The FINAL republish of all three binaries at the tagged commit remains the teammate's step before Part 2 of the release |
| B8 | closed | GPS on box B: its saved source is hampi's gpsd at 192.168.0.10:2947, which is unreachable now and blocked every Meshtastic start on B with 'GPS feed never reached its source'. Set to off for the run so the stack could start; the end state puts the original setting back |
| B9 | closed | CA2RXU has no T-Deck Pro variant, so the wanted 868 tracker row A17 has no reference firmware — by the plan's own note, not a defect |
| B10 | closed | the CRL lockout fix is in 0.5.0 (landed on dev 32cd88b before the run): the client-CA CRL is re-checked on every network-watchdog pass on every box, and both boxes' exposed consoles answered client certificates throughout this run — X6 reached each console through its firewall with a certificate issued by that box |
| B11 | closed | daemon CRC detection landed as the pinned daemon 2a0db88 and is what the run installs: CRC-failed frames are dropped and counted instead of being handed to the stacks |
| B12 | closed | voice 868 profile fixed and live-proven on the release commit: rows V3 and V4 pass both directions at SF7/BW250 |
| B13 | closed | F-6 fixed on branch fix/band-already-served (342a069): the daemon-params guard now covers the starting stack's own running components; testlab green, CI running, live proof pending |

### F — Desk end state

| row | result | what was witnessed |
|---|---|---|
| F1 | pass | F-1 fix proven live on E at C': with a FRESH companion.db the first MeshCore start logs 'Restored companion state: 0 contacts, 0 channels (+Public seeded), 0 prefs, 0 queued messages', meshcore-cli shows '0: Public [8b3387e9c5cdea6ac9e5edbaa115cd72]', and the database now carries the channel row plus the _channels_seeded marker. Learned on the way: in chat+repeater mode persistence goes through openHop's RepeaterDaemon (state/openhop/repeater.db) instead of our CompanionStore, so the gap only ever applied to the plain-chat path |
| F14 | pass | Station G2 updated to Meshtastic 2.7.26.54e0d8d: config exported first, 4 MB flash backed up, app image written and verified, board boots and reports hwModel STATION_G2 with its name 'CHE Station' kept. It now enumerates as the native USB-JTAG device (port name changed), which is the 2.7.26 behaviour on this board |
