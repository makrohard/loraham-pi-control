# Live test, 0.12.0 — RF proof, 2026-10-05

The release commit is `83d6367` (`v0.12.0`). Both boxes ran the released controller, installed by the product path
(`lhpc self-update --apply` on `main`), with every installed stack updated (`lhpc update <stack>`; nothing left
needing a rebuild). Boxes: the Pi 5 test box (Desktop image, LoRaHAM board) for 433 MHz, and the Zero 2 W test box
(Lite image) for 868 MHz and as the second box of the box-to-box rows. The peers were real radios beside the boxes,
at about one metre. Each row sent one marked text (a nonce) each way, and each was looked for on the other side.

Power and duty: the Pi 5 never used `--high-power` or 20 dBm. On 433 it transmitted under the existing power
guard, and the daemon's POWER=10 was confirmed after every start. The Zero's transmit powers were read before its
rows: Meshtastic 17 dBm (the stack's `max_power`), MeshCore 14 dBm, Reticulum 14 dBm, daemon 433 17 dBm, voice
17 dBm. None was at 20 dBm, so none was lowered; the values after the rows equal those before. Box transmit time
stayed far under 1 % per band (at most about 7 s in the hour on any box and band).

Times are UTC.

| row | stack | box | band | peer | direction | nonce seen | result |
|---|---|---|---|---|---|---|---|
| 1 | meshcom | Pi 5 | 433.175 MHz | MeshCom T-Deck (DJ0CHE-07) | peer → box | yes (03:31:26, rssi −84) | PASS |
| 1 | meshcom | Pi 5 | 433.175 MHz | MeshCom T-Deck (DJ0CHE-07) | box → peer | yes: the T-Deck relayed the same text back to the box (03:32) | PASS |
| 2 | kiss + graywolf (APRS) | Pi 5 | 433.775 MHz | CA2RXU T-Beam (DJ0CHE-7) | box → peer | yes: the tracker printed the message (`[LoRa Rx]`) | PASS |
| 2 | kiss + graywolf (APRS) | Pi 5 | 433.775 MHz | CA2RXU T-Beam (DJ0CHE-7) | peer → box | yes: the tracker's ACK received (03:37:43, rssi −71); Graywolf marked the message `acked` after 1 attempt | PASS |
| 3 | meshtastic | Zero 2 W | 868, EU_868 LongFast | Station G2 | peer → box | yes (03:42:51, rssi −69; node log and the RF-log decoder) | PASS |
| 3 | meshtastic | Zero 2 W | 868, EU_868 LongFast | Station G2 | box → peer | yes: implicit ACK, and the G2's rebroadcast of the same packet received back (03:43:39) | PASS |
| 4 | meshcore | Zero 2 W | 869.618 MHz, eu_uk_narrow | T-Deck Pro (Bluetooth companion) | peer → box | yes (03:47:03, rssi −66; public channel) | PASS |
| 4 | meshcore | Zero 2 W | 869.618 MHz, eu_uk_narrow | T-Deck Pro (Bluetooth companion) | box → peer | yes (03:47:52, on the peer's public channel) | PASS |
| 5 | reticulum + meshchat | Zero 2 W | 868.5 MHz, SF8 | Heltec LoRa32 V3 as RNode | box → peer | announce: path installed on the peer; LXMF text delivered (03:51:06) | PASS |
| 5 | reticulum + meshchat | Zero 2 W | 868.5 MHz, SF8 | Heltec LoRa32 V3 as RNode | peer → box | announce heard (03:50:06, rssi −56); LXMF text in MeshChat, delivery proof back (03:50:45) | PASS |
| 6 | chat | Pi 5 ↔ Zero 2 W | 433.775 MHz | the other box | Zero → Pi 5 | yes (03:53:46, rssi −74; on screen and in `lorachat.log`) | PASS |
| 6 | chat | Pi 5 ↔ Zero 2 W | 433.775 MHz | the other box | Pi 5 → Zero | yes (03:54:02, rssi −70; on screen and in `lorachat.log`) | PASS |
| 7 | voice | Zero 2 W → Pi 5 | 434.700 MHz, SF7 | the other box | Zero → Pi 5 | no text in voice: 4 s of push-to-talk with the test tone, 14 frames sent, the same 14 received, start and end frames included (rssi −74) | PASS |
| 7 | voice | Pi 5 → Zero 2 W | 434.700 MHz, SF7 | the other box | Pi 5 → Zero | — | not run (below) |

Notes per row:

- Row 3: the Zero's node had a new key since its reinstall, so the G2 first removed the old entry for that node
  number. Channel broadcasts need no key exchange.
- Row 4: the peer used its stored Bluetooth bond, with no pairing PIN. The first connection attempt failed although
  the peer was advertising; the second worked.
- Row 5: `rnode_framing` was set to `yes` for the row, as a real RNode needs, and back to `no` afterwards.
- Row 6: both boxes carry the same operator callsign; the client does not filter its own call.
- Row 7: the Zero has no microphone, so the terminal client's test tone was the audio source.

## Not run

- **Voice, Pi 5 → Zero.** On the Pi 5, lhpc starts the desktop variant of the voice app (the terminal variant is
  offered only where the desktop app cannot run), and its push-to-talk is a key or button in the desktop window.
  The run had no input on that desktop, so only the Zero → Pi 5 direction was proven.
