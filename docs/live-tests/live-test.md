# Live test, 0.11.0 — the release matrix, 2026-09-28

Boxes: **e293** = Pi Zero 2 W (`lhpc-e293`, Lite image, Uputronics SX1278 on 433 and SX1276 on 868);
**Pi 5** = `lhpc-0ae1` (Desktop image, LoRaHAM board: RFM98PW with amplifier on 433, RFM95 on 868).
Peers: T-Deck (MeshCom), T-Deck Pro (MeshCore, over BLE), Station G2 (Meshtastic), T-Beam (APRS), Heltec RNode
(Reticulum).

The release commit is `6ff1937` (`v0.11.0`). The MeshCom binary was built from it and published before the rows that
need it. Recorded airtime, against the 36 s per hour that a 1 % duty cycle allows: on 433 in the hour of row 11
T-Deck ≈ 15 s, e293 ≈ 9 s, Pi 5 ≈ 4 s; rows 4/5 Pi 5 ≈ 5.3 s; row 8 ≈ 4 s per side; row 6 ≈ 3 s per side; row 7
T-Deck Pro ≈ 2.5 s, e293 ≈ 4.5 s (row 2 was not recorded). The Pi 5 never used `--high-power` or 20 dBm, and its 433
transmissions were short.

## Rows ([test-matrix.md](../test-matrix.md))

| row | e293 | Pi 5 |
|---|---|---|
| 1 daemon, binary | PASS: build refused as designed, both bands READY, `built_from == pin` | — |
| 2 chat | PASS: RF both ways with the Pi 5 | PASS: after the purge chat receives on 433.775 (the new default); RF both ways with e293 |
| 3 voice | PASS: terminal variant runs, GTK skipped as designed | PASS: GTK variant runs, terminal variant skipped |
| 3 voice over RF | not run: no audio capture device on either box | not run |
| 4 kiss, 5 graywolf | — | PASS: beacon and message to the T-Beam, message ACKed; T-Beam → Pi 5 peer-limited (no GPS fix indoors) |
| 6 reticulum | PASS with the Heltec RNode on 868: path, probe, LXMF both ways | — |
| 7 meshcore (chat + repeater) | PASS with the T-Deck Pro over BLE: adverts, DMs and channel both ways, repeater retransmit | — |
| 8 meshtastic, binary | PASS with the Station G2 (see the known issue below) | PASS with the Station G2 (see the known issue below) |
| 9 meshtastic from source | — | PASS: build 825 s, start verified (the registry download limit needed a second uplink, see below) |
| 10 daemon from source | PASS: build 317 s, both bands READY, no OOM | — |
| 11 meshcom, binary | PASS with the T-Deck: DMs and channel both ways; own node ID; the ID is kept across a restart and is new after a purge | PASS as the second node: own node ID, different from e293's; DMs both ways with the T-Deck |
| 12 meshcom from source | — | PASS: build 839 s, all four components at their pins, start verified, a new node ID after the purge |

Cross-cutting on e293: auto-install, the `dev` spot-check (kiss) and pins vs binaries: PASS. Known-working: confirmed
with the CLI, and the offer's form was read in the stack page after a green start. The web console: its pages and API
routes read over the console's local socket, 0 tracebacks (not through the web server with a browser; from the LAN
the web server completed the TLS handshake and refused with 403 by its allow-list, as configured).

Boot restore on e293 with three stacks running (daemon, meshcom, meshcore): `3 restored, 0 failed`; with nothing
running: `0 restored, 0 failed`. High-power switch on e293: refusal without the flag, restart needed after
the switch, the start gate, one short frame at 20 dBm with `TXERR` 0, revocation by restart: PASS. On the Pi 5: the
refusals and the switch state only (`POWER=20` refused on both bands, `--high-power` absent from the daemon's
command line): PASS.

## Upgrade path (e293)

From a real 0.10.0 box: `lhpc self-update --apply` to 0.11.0; before the upgrade note's commands, boot restore refuses
MeshCom ("installed binary artifact is behind the manifest … update it first"), as documented; after the note's
commands MeshCom starts with its own node ID; voice reaches its new pin with `lhpc update voice --yes` and
`lhpc build voice --yes`. After the final reboot, boot restore brought MeshCom back (`1 restored, 0 failed`, one item
skipped and not identified), its node ID kept. PASS.

## From-zero reinstall

Both boxes: firewall reset, `uninstall.sh --purge`, `bootstrap-deps.sh`, `install.sh` from `main`, reboot, hardware,
the start refused without a callsign (with the hint), identities, auto-install from the web console, every stack the
controller starts started and stopped (chat on both boxes and the voice terminal client on e293 are started by hand
and were listed as manual), host tests for the record. PASS on both. Not exercised: the Wi-Fi join through the Network
panel (both boxes are wired; on the Pi 5 there is no access-point profile, so the panel has no controls) and, on
e293, the console's highlight of the callsign row. What the reinstall leaves on both boxes: the managed firewall is
removed by the reset step and not installed again by the install (it is the operator's step), and the console is back
at its default (loopback, remote access off).

| | e293 | Pi 5 |
|---|---|---|
| auto-install from the console | 14 min 12 s, 9/9 | 5 min 01 s, 9/9 |
| host tests | kiss PASS 21 s, meshcore PASS 154 s; chat, voice, graywolf, reticulum print a plan only (no pass or fail line); daemon, meshtastic, meshcom refused on the binary channel (as designed) | kiss PASS 3 s; meshcore ran 82 s (its output named the node test and the web UI backend import; the pass or fail line was not captured); graywolf, chat, voice, reticulum: no host test; daemon, meshtastic, meshcom refused on the binary channel |
| README step 3 (`bootstrap-deps.sh --dry-run`) | exit 5: apt could not resolve the declared packages, and the message named `sudo apt-get update`; step 4 passed | passed |

## Known issue (also in the release notes)

After `lhpc clean meshtastic --purge` and a reinstall, the Meshtastic node has a new key. On both boxes the node
logged its node-info at start but did not transmit it. Peers learned the new key when the node answered a broadcast
from them, sent after the node had been up for about ten minutes; an earlier broadcast was not tried. Direct messages
in both directions worked after that exchange.

## Notes from the run

- The PlatformIO registry refused package downloads from the test site's public address ("Download limit
  exceeded"); rows 9 and 12 ran with the registry reached over a second uplink.
- Boot restore needs a running set to restore: the release ships with nothing running by default.
- Meshtastic node-info on a fresh node (the known issue above), measured after the matrix on 2026-09-28: the Pi 5
  after `lhpc clean meshtastic --purge` and a binary reinstall (firmware 2.7.26), debug logging, a Station G2
  listening. The first-time region setting scheduled a restart ("Reboot in 7 seconds"); the owner push then logged
  "Send owner", "Started Tx" and, one second later, "Completed sending" while the next configuration push
  (`position.gps_mode`) retuned the radio. The G2 received one damaged frame (CRC error) at that time and kept the
  old key. After the restart the node logged "Skip send NodeInfo since we sent it <600s ago" and `num_packets_tx=0`
  at 75 s. A G2 broadcast 10 min 23 s after the recorded send made the node answer with its node-info; the G2
  decoded it and dropped it for the key mismatch ("Public Key mismatch, dropping NodeInfo").

The 0.10.0 record is this file at `v0.10.0`.
