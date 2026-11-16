# Hardware proof: the RF test line

`testlab/hardware/rf-proof.sh` puts every radio of the lab's boxes on air with a short test frame, and in its full lane
every stack with its real peer. It starts each release's hardware test from working code instead of a new sheet: fill the
local file, run dry, then run live.

Every live run, either lane, needs the maintainer's transmit and lab permission and the release coordinator's go for that
run, with the hardware connected, reachable and set up. `live` prints that line first.

## The two lanes

| lane | proves | rows | needs |
|---|---|---|---|
| **RF fast** | every present radio sends and receives one nonce frame through the daemon | the frame rows 7–11 | the boxes only: no PC peer, no Bluetooth, no PIN |
| **RF full** | RF fast, plus every stack on air with a real peer | rows 1–11 | the PC peers below |

`LANE=fast|full` picks the rows (default `fast`); `ROWS_ONLY="<id> ..."` runs single rows. RF fast is not the release
matrix's fast lane ([test matrix](../test-matrix.md)).

The rows, in run order (the `ROWS` table in `rf-proof.sh` is the only place that defines them):

| row | stack | endpoint A | endpoint B | power A / B |
|---|---|---|---|---|
| 7 | daemon text frames | box H, board 433 | box H, the 433 stick | minimum / minimum, restored; plus a per-radio stop check |
| 8 | daemon text frames | box H, Meshstick 868 | the Zero, board 868 | minimum, restored / as-is |
| 9 | daemon text frames | box H, PineDio 868 | the Zero, board 868 | minimum, restored / as-is |
| 10 | daemon text frames | box H, board 433 | the Zero, board 433 | as-is / as-is |
| 11 | daemon text frames | box H, board 868 | the Zero, board 868 | as-is / as-is |
| 1 | MeshCom | box H, board 433 | the MeshCom T-Deck | as-is / receive only |
| 2 | KISS + Graywolf | box H, board 433 | the CA2RXU tracker | as-is / as-is, not read |
| 3 | Meshtastic | the Zero, board 868 | the Station G2 | as-is / low, restored |
| 4 | MeshCore | the Zero, board 868 | the T-Deck Pro (Bluetooth) | as-is / read only |
| 5 | Reticulum + MeshChat | the Zero, board 868 | the Heltec RNode through the PC's shared rnsd | as-is / read only |
| 6 | chat | box H, board 433 | the Zero, board 433 | as-is / as-is |

A frame row first reads FREQ and SF of both ends from the daemon's STATUS line; a difference is a FAIL (the radio is
present but incompatible). BW, coding rate and sync word are not in STATUS: a mismatch there shows as a frame not heard.
Row 5 has no on-air PASS yet.

## Hardware that is not there

A missing box, radio or peer makes its rows NOT RUN, naming what is missing (`absent: box Z`, `absent: radio HMS`,
`absent: peer tdpro`); the other rows still run. A radio key that matches two radios is NOT RUN too
(`ambiguous: radio H433 (2 match)`), never a first match. A box that answered at the start and is lost later is a STOP.
The report ends with a COVERAGE table: per supported model, stack and check, and per direction, `proven`, `FAIL`,
`NOT RUN` with the reason, or `not present`. An absence is never a PASS; whether a release ships with a NOT RUN row is the
release's decision.

## The local file

`testlab/hardware/lab.local.env` (git ignores `*.local.env`) holds the lab's values; `lab.example.env` beside it lists
every key with a placeholder and is what dry reads:

- per box `B` (H, Z): `BOX_<B>_HOST` (required), `BOX_<B>_USER` (default `lhpc`), `BOX_<B>_RUNTIME` (default
  `~/loraham-pi-control`);
- the radio keys, `RADIO_<KEY>="<box> <model> <band> [serial=<s>|port=<p>]"`; the three USB sticks share one USB id, so
  a stick is found by its serial, or by its port when the serial is not unique;
- the PC peers, `PEER_<name>="<kind> <by-id name | BLE address> <hub>"` for tdeck, tracker, g2, tdpro and rnode; the
  RNode's also names the PC's shared rnsd, `instance=<name> port=<n> control=<n>` (no defaults, the two ports differ);
- `OPERATOR_CALL`, the peer venvs, the shared rnsd config, the peer lock and `EVD_ROOT` (under `/home`, never `/tmp`).

It holds no secret, and the runner refuses one that sets a variable named like `*PASS*`, `*PIN*` or `*SECRET*`. The box
passwords come on stdin, one line each (box H, then the Zero; an empty line declares that box absent); the MeshCore PIN
is the next line, read only when row 4 runs. Stdin is then closed.

## Safety

- Every box radio, sticks included, runs at most min(17, its model's maximum) dBm (the PineDio 14), never with
  `--high-power` or a high-power grant. A peer transmits only after its power read back low, or as one of the as-is
  peers named above.
- Every transmission goes through one dispatcher, `tx <radio> <frames> <nonce|control> -- <executor> <args>`, which checks
  the command, the radio's power read in this row, the message (the row's nonce only, at most 16 characters), the
  budget, both boxes' boot ids and the USB lists, and only then runs the send. An automatic send of a protocol endpoint
  (the tracker's ACK, the LXMF delivery proof, the RNS path request) is reserved with `tx ... control --reserve` right
  before the send that triggers it. Any failed check is a STOP.
- A radio sends at most 10 frames per run (`TXMAX`); the start prints each radio's worst case (`BUDGET` lines) and a sum
  above 10 is a STOP before any transmission.
- Every change is undone after its row, the undo recorded before the change. An unplanned reboot stops the run.
- Every captured output is redacted before it is written: the passwords, the PIN and the operator's call with any SSID.
  A received radio line is never written; the receive judges write `SEEN <nonce> <time> rssi=<n>`. The end scan checks
  the evidence again.
- Reading MeshCom's received frames switches `--setinfo` on for the read and off after: the read leaves setinfo off.

## Running it

```sh
bash testlab/hardware/rf-proof.sh dry               # the example bench; no box, peer or network
LANE=fast bash testlab/hardware/rf-proof.sh live    # on the go: passwords (and the PIN) on stdin
```

Dry prints every command it would run (`[dry] <H|Z|PC> > <file>: <command>`), the `REPORT`, `COVERAGE`, `BUDGET` and
`TX` lines and one `FRAME` line per frame a real run would send; it ends with its self-checks
(`dry self-check: all <n> OK`). Exit status: 0 = every row PASS or NOT RUN, 1 = a FAIL, 2 = a STOP (dry: 0 or 2). CI runs
both lanes dry on every push (`testlab/tests/hardware`): the dry inventory, the NOT RUN cases, the frame coverage of every
`tx`, the lhpc calls parsed at the head (`cliforms.py`) and the redaction.

`testlab/hardware/fw-boxproof.sh dry|live` is the firewall box proof on box H, built the same way (the same local file,
the password on stdin).

## The dated report

A live run writes its evidence under `$EVD_ROOT` (the command outputs, `REPORT.md`, `COVERAGE.md`, `SHA256SUMS`). The
dated report in `docs/live-tests/` takes its rows from `REPORT.md` and `COVERAGE.md`, names each peer by its kind
("the CA2RXU tracker"), never by its call, and lists every NOT RUN row with its reason.

## Bench setup and reflash (manual, outside every lane)

Box H (a Pi 5) carries the LoRaHAM board (433 and 868) and three USB sticks: the CH341 + RFM98PW 433 build, a PineDio
(on port 1-1, its serial not unique) and a Meshstick; a stick added later needs one firewall apply before the console
stops showing "Changes pending". The Zero (a Pi Zero 2 W) carries the LoRaHAM board. The PC peers hang on two unpowered
hubs (the T-Deck and the Heltec on one, the tracker and the Station G2 on the other).

Finding them on the PC:

- `ls -l /dev/serial/by-id/`: the serial peers by name; only by-id names go into the local file, the ttyUSB/ttyACM numbers
  change order.
- `bluetoothctl devices`: the T-Deck Pro. Its address is the one its BLE advert carries now, not a stale cached advert of
  another name.
- The Heltec RNode is reached only through the PC's shared rnsd instance (the RNS config path in the local file), never
  by opening its port.
- On a box: `lhpc hardware` shows its radio setup ([CLI](../cli.md#hardware)).

Known traps: the PC's gpsd claims every CP2102 port on each replug, the RNode's included, so stop it (or narrow its udev
rule) before a row that needs the RNode; a fix that lasts needs root on the PC. Reflashing or factory-resetting a peer is
allowed when a row needs it (identities on the lab peers need not be kept); record the image and its sha.

Reflash scripts exist only for flashes on record, under `testlab/hardware/manual/`:

| peer | script | record |
|---|---|---|
| the Heltec LoRa32 V3 RNode | `manual/rnode-heltec-v3.sh` | `rnodeconf --autoinstall` (rns 1.5.4), firmware 1.86; rnodeconf checks the signature, no image sha recorded |
| the MeshCom T-Deck | none | no recorded flash |
| the CA2RXU tracker | none | no recorded flash |
| the Station G2 | none | no recorded flash |
| the T-Deck Pro | none | no recorded flash |
