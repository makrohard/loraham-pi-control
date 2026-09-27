# Live test, 0.10.0 — proof rows and the from-source rehearsal, 2026-09-26/27

Boxes: **e293** = Pi Zero 2 W (`lhpc-e293`, Lite image, Uputronics SX1278 on 433 and SX1276 on 868);
**Pi 5** = `lhpc-0ae1` (Desktop image, LoRaHAM board: RFM98PW with amplifier on 433, RFM95 on 868).
Peers: T-Deck (MeshCom), T-Deck Pro (MeshCore), Station G2 (Meshtastic), T-Beam CA2RXU (APRS).
Every transmission stayed within 1 % duty cycle per band and transmitter; the Pi 5's 433 path never ran above
normal power.

The full release matrix ([test-matrix.md](../test-matrix.md)) was **not yet run at the time of the release commit**;
see the release's run report.

## Proof rows

| row | what | box, head | result |
|---|---|---|---|
| P1 | high-power switch in the web console, a real browser over LAN https with a client certificate | e293 `16263c5`, 2026-09-26 | PASS: switch on → restart required; start → `--high-power` on 433 only, `HIGHPOWER=1`; switch off → a live `POWER=20` refused; restart → `HIGHPOWER=0` |
| P2 | radiated power `POWER=17` vs `20` on e293's bare 433, received by the Pi 5 | e293 `16263c5` TX, Pi 5 RX, 2026-09-27 | +1.0 dB at 20 over 17 (RSSI medians −78 / −77, n = 21 / 20); the chip took `POWER=20`; radiated gain still to be measured |
| P3 | daemon TX guard delays | — | documented limit ([backlog](../backlog.md)): not measurable within 1 % duty cycle |
| P4a/P4b | MeshCore group messages both ways, repeater retransmit | e293 `16263c5` ↔ T-Deck Pro, 2026-09-27 | PASS |
| P4c | MeshCore admin login over the air, then telemetry | e293 `9062008` ↔ T-Deck Pro, 2026-09-27 | PASS: 15-character password → `LOGIN_SUCCESS`; telemetry request → `TELEMETRY_RESPONSE` |
| P5 | chat + repeater: decode lines for a channel message and a DM | e293 `16263c5` ↔ T-Deck Pro, 2026-09-27 | PASS |
| P6 | Graywolf scheduled beacon across slot boundaries | Pi 5 `9062008` → T-Beam, 2026-09-27 | PASS: 3/3 beacons, 600.000 s apart, each decoded |
| P7a | RF log RX lines for kiss/Graywolf | Pi 5 `9062008` ↔ T-Beam, 2026-09-27 | PASS 2/2 |
| P7b | RF log RX lines for Reticulum | — | not run: the PC's Heltec RNode port was busy |
| P8 | RF log 5 MB rollover | e293 `9062008`, 2026-09-27 | PASS: one roll at frame 14 of 20, every frame once across the rolled and the live file |
| P9 | SX1262 on 868 | — | documented limit ([backlog](../backlog.md)): no SX1262 on any board here |
| P10 | Station G2 learns the Pi 5's key from node-info; DMs | Pi 5 `9062008` ↔ G2, 2026-09-27 | PASS: 3/3 DMs each way acknowledged after the G2 had the Pi 5's node-info (the node's answer to the G2's broadcast at 608 s of uptime) |
| P11 | Pi 5 868 noise floor | Pi 5 `71c0d39`, 2026-09-27 | closed: −119 dBm median after an antenna and placement change (before −110) |
| P12 | MeshCom on the release artifacts: speed, node settings across updates, T-Deck DMs, reboots | e293, artifacts built from `0683000a` (QEMU `b53b230c`, firmware `ba289816`), 2026-09-27 | PASS: console → TX median 1.55–1.92 s; settings carried across an update, reset by `clean --purge`; 10/10 cold starts; DMs 4/4 acknowledged both ways; reboot with MeshCom + MeshCore running restored both after the [upgrade note](../../CHANGELOG.md)'s commands |
| P13 | client CRL expired while no NTP/GPS | Pi 5, `71c0d39` → `15e9d95`, 2026-09-27 | PASS: 0.9.2 locked out (400); the fix healed the CRL provisionally (200), revocations kept; normalised after NTP |

## From-source rehearsal (Pi 5)

Run to warm the caches before the final candidate; the pins do not move.

| row | stack | head | build | peak memory | result |
|---|---|---|---|---|---|
| 10 | `daemon`, pinned from source | `cc04996a` | 32 s | 942 MiB | PASS: both bands READY |
| 9 | `meshtastic`, pinned from source | `cc04996a` | 638 s | 1276 MiB | PASS: start verified, `lhpc meshtastic --info` answers |
| 12 | `meshcom`, pinned from source | `5bd3090e` | 228 s | 1678 MiB | PASS: bridge, GPS and QEMU verified; web UI `:18083` → 200; callsign confirmed after 18.7 s |
