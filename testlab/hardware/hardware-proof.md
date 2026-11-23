# Hardware proof: the RF test line

`testlab/hardware/rf-proof.sh` puts every radio of the lab's boxes on air with a short test frame, and in its full lane
every stack with its real peer. It starts each release's hardware test from working code instead of a new sheet: fill the
local file, run dry, then run live.

Every live run, either lane, needs the maintainer's transmit and lab permission and the release coordinator's go for that
run, with the hardware connected, reachable and set up. `live` prints that line first.

## The lanes

| lane | proves | rows | needs |
|---|---|---|---|
| **RF fast** | every present radio sends and receives one nonce frame through the daemon | the frame rows 7–11 | the boxes only: no PC peer, no Bluetooth, no PIN |
| **RF full** | RF fast, plus every stack on air with a real peer | rows 1–12 | the PC peers below |
| **rns** | Reticulum's two radios on one node | row 13 | box H's board 868 and Meshstick, the RNode |

`LANE=fast|full|rns` picks the rows (default `fast`); row 13 has a lane of its own, so the RNode's frames of a lane stay
within the 10 per run; `ROWS_ONLY="<id> ..."` runs single rows. RF fast is not the release
matrix's fast lane ([test matrix](../../docs/test-matrix.md)).

The rows, in run order (the `ROWS` table in `rf-proof.sh` is the only place that defines them):

| row | stack | endpoint A | endpoint B | power A / B |
|---|---|---|---|---|
| 7 | daemon text frames | box H, board 433 | box H, the 433 stick | minimum / minimum, restored; plus a per-radio stop check |
| 8 | daemon text frames | box H, Meshstick 868 | the Zero, board 868 | minimum, restored / minimum, restored |
| 9 | daemon text frames | box H, PineDio 868 | the Zero, board 868 | minimum, restored / minimum, restored |
| 10 | daemon text frames | box H, board 433 | the Zero, board 433 | as-is / as-is |
| 11 | daemon text frames | box H, board 868 | the Zero, board 868 | as-is / as-is |
| 1 | MeshCom | box H, board 433 | the MeshCom T-Deck | as-is / read only (its stored power 0..17 dBm, else a STOP: set it by hand first) |
| 2 | KISS + Graywolf | box H, board 433 | the CA2RXU tracker | as-is / as-is, not read |
| 3 | Meshtastic | the Zero, board 868 | the Station G2 | as-is / low, restored |
| 4 | MeshCore | the Zero, board 868 | the T-Deck Pro (Bluetooth) | as-is / read only |
| 5 | Reticulum + MeshChat | the Zero, board 868 (direct SPI) | the Heltec RNode through the PC's shared rnsd | as-is / read only |
| 12 | Reticulum (daemon backend) + MeshChat | box H, Meshstick 868 | the Heltec RNode through the PC's shared rnsd | as-is / read only |
| 6 | chat | box H, board 433 | the Zero, board 433 | as-is / as-is |
| 13 | Reticulum: two radios, one node (lane rns) | box H, board 868 (the owner) + Meshstick 868 (the member) | the Heltec RNode through the PC's shared rnsd | as-is, the owner at minimum for the member's exchange (restored) / read only |

A row-6 direction passes only when its word is in the receiving radio's log and on the other box's chat screen (the
chat client's pane, written as `SEEN <nonce> <time> screen`; a miss writes only the screen's line count, never a screen
line); a failed direction does not skip the other.

A frame row first reads FREQ and SF of both ends from the daemon's STATUS line; a difference is a FAIL (the radio is
present but incompatible). BW, coding rate and sync word are not in STATUS: a mismatch there shows as a frame not heard.
Rows 5, 12 and 13 have no on-air PASS yet.
The Reticulum rows (5, 12, 13) tell the box's announce by its LXMF address, read only from MeshChat's identity before each
exchange (its display name is only logged; the address is masked in the evidence like the passwords, and the end scan checks
it). They prove the shipped defaults: each reads `rnode_framing` (it must be `yes`) and the radio's `backend` (row 5 `spi`,
rows 12 and 13 `daemon`), else STOP; the kit sets neither. The PC listens before the MeshChat start, whose own announce it
must hear (the kit asks for none; the start is counted as that send: 2 frames, MeshChat's LXMF and audio-call announces,
on every radio of the node).
Row 5's prerequisite (an operator step before the run): save `backend = spi` on the Zero's board radio
(`lhpc config reticulum@<rid> backend spi`); the product's default is `daemon`, and row 5 stops on it.
The Reticulum rows' prerequisite on each of their boxes: MeshChat started there once (its first start writes
`state/meshchat/identity`, which the rows read before their own start); R0 reads it and a row whose box has none is NOT
RUN with that reason.
Row 13 opens the node on the owner as row 12 does (one exchange; MeshChat starts once and runs to the row's end), then
joins the member (`lhpc stack start reticulum@<rid> --join`): the node keeps its pid and start time, the member's
interface is up and MeshChat answers. One exchange over the member follows with the owner at its minimum (restored after),
heard through one announce call to MeshChat (`rt_announce`); the member's own RF log must gain an RX line and at least 3
TX lines: the announce's 2 and the exchange, which the node relays over no other radio. Then the member's stop (the node
keeps its pid, the member's interface is gone) and one exchange over the owner without an announce: the PC takes the
box's path from its shared rnsd, answered locally (absent: the exchange fails, no path request on air). Then the
member's join again, and the owner's stop (no node process, no Reticulum instance left). Owner budget: 2 + 2 + 3 x 2
(proof and send) = 10 frames, exactly TXMAX: a frame outside this count (an LXMF retry after a lost proof, MeshChat's
auto-announce falling due, a local propagation node's third announce) ends the row FAIL at RS, after the TX. The member is the row's third
endpoint (`member=H:HMS` in the `ROWS` flags): absent, the row is NOT RUN.
Rows 3 and 4, peer -> box: the word must also show up inside the app on the box, not only in its radio log: a Meshtastic
listener on the box, connected before the G2 sends (Meshtastic keeps no history to read later), and MeshCore's own message
queue, read with meshcore-cli. That read takes the messages out of the queue, so a MeshCore client already running there
(the web UI, an interactive meshcore-cli) makes row 4 NOT RUN. A direction passes only with both checks. Their evidence holds
markers only: `SEEN <nonce> <time>` (the app gives no RSSI), and for row 3 `LISTENER-ENDED <rc>` when its listener ended early.

## The pre-step M

After R1 (nothing of the kit's runs then) and before the rows, `mcset.py` copies each stack row's instance settings from the
stack's source file into the instance of that row's radio: row 1 MeshCom's callsign (the value from `MCSET_J1`, a
callsign[-SSID] or `global`; empty = that job NOT RUN), row 2 Graywolf's call, row 3 Meshtastic's node names, row 4 MeshCore's
node name (its mode is never copied: the job reads it and stops unless it is `chat`). The changes are kept (the lab's settings); a job undoes its own change only when its own set or read-back
fails. One line per job in `MCSET.md`; a job that is not a PASS or NOOP is named in `NOT-RUN.txt` and never stops the run; an
undo that does not restore the instance (UNRESOLVED) fails the run at RS. `MCSET=on|off` (default: on in RF full, off in RF fast).
Row 4 runs only after a J4 PASS, which read MeshCore's mode as `chat` (a repeater relays frames no `tx` counts): with
`MCSET=off`, or any other J4 verdict, row 4 is NOT RUN by name.

## Hardware that is not there

A missing box, radio or peer makes its rows NOT RUN, naming what is missing (`absent: box Z`, `absent: radio HMS`,
`absent: peer tdpro`); the other rows still run. A radio key that matches two radios is NOT RUN too
(`ambiguous: radio H433 (2 match)`), never a first match. A box that answered at the start and is lost later is a STOP.
The report ends with a COVERAGE table: per supported model, stack and check, and per direction, `proven`, `FAIL`,
`NOT RUN` with the reason, or `not present`. An absence is never a PASS; whether a release ships with a NOT RUN row is the
release's decision.
Last comes the result table for people, printed and kept as `RESULT.md`: the verdict on top (PASS, FAIL, NOT COMPLETE,
a dry run's rehearsal, or STOPPED), then one line per row with each direction's PASS, FAIL or NOT RUN and its reason.
Rows 1–5, 12 and 13 stop at a FAIL: their later directions are NOT RUN, naming that FAIL, and send nothing.
Row 2 needs the tracker's call-SSID (`TRACKER_CALL`) and either its power from its portal (`TRACKER_DBM`) or
`TRACKER_ASIS=1`: the tracker runs as it is, with no power check (the maintainer's word of 2026-11-18). Meshtastic (row 3):
a packet the node addresses to itself (a local API exchange) is never aired and not counted; the node's own API-sent text is not
in its RF log, so RS adds the kit's own Meshtastic sends from its ledger.

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

- Every box radio, sticks included, runs at most min(17, its model's start power) dBm (USB radios 14; the RFM98PW stick
  at setting 14 also when its model says more: the lab's cap), never with `--high-power` or a high-power grant. A peer
  transmits only after its power read back low, or as one of the as-is peers named above.
- Every power value read, set or restored lies in 0..17 dBm (Meshtastic 1..17: its 0 is the firmware's maximum). A radio
  or peer found outside it, or a box radio above its cap, stops the run before anything changes: set it into that range
  by hand first.
- Every transmission goes through one dispatcher, `tx <radio> <frames> <nonce|control> -- <executor> <args>`, which checks
  the command, the radio's power read in this row, the message (the row's nonce only, at most 16 characters), the
  budget, both boxes' boot ids and the USB lists, and only then runs the send. An automatic send of a protocol endpoint
  (the tracker's ACK, the LXMF delivery proof, the RNS path request) is reserved with `tx ... control --reserve` right
  before the send that triggers it. Any failed check is a STOP. The peer driver (`peer.py`) refuses on its own a power
  value outside that domain, a power set outside the runner's setter and a send outside `tx`. That context guards
  against accidents only: never run `peer.py`'s power setters or senders by hand; they belong to the runner.
- A radio sends at most 10 frames per run (`TXMAX`); the start prints each radio's worst case (`BUDGET` lines) and a sum
  above 10 is a STOP before any transmission. After the run, each radio's frames sent since the start (its RF logs,
  each frame counted once, the stack's own included, plus a send the daemon reported as failed; a radio's Reticulum log
  counts as the daemon's frames again where the row read its backend as `daemon`) must also be at most 10.
  A count that cannot be proven complete (a log cut or cleared in the run) is a FAIL as well.
- Every change is undone after its row, the undo recorded before the change. An unplanned reboot stops the run.
- Every captured output is redacted before it is written: the passwords, the PIN and the operator's call with any SSID.
  A received radio line is never written; the receive judges write `SEEN <nonce> <time> rssi=<n>`. The end scan checks
  the evidence again.
- Reading MeshCom's received frames switches `--setinfo` on for the read and off after, an interrupt included; an off
  that cannot be sent fails the read. A read counts only a frame the peer received (MeshCom's `RX-LoRa2`, the tracker's
  `[LoRa Rx]`) with the nonce in its text, never a console echo. Row 1 reads box H's MeshCom node the same way: its
  `RX-LoRa2` line in the node's UART log, with `--setinfo` on for the row and its off journalled before the on. Each
  direction also needs its frame in the box's RF log (TX `outcome=ok` out, RX in).

## Running it

```sh
bash testlab/hardware/rf-proof.sh dry               # the example bench; no box, peer or network
LANE=fast bash testlab/hardware/rf-proof.sh live    # on the go: passwords (and the PIN) on stdin
```

Dry prints every command it would run (`[dry] <H|Z|PC> > <file>: <command>`), the `REPORT`, `COVERAGE`, `BUDGET` and
`TX` lines and one `FRAME` line per frame a real run would send; it ends with its self-checks
(`dry self-check: all <n> OK`). Exit status: 0 = every row PASS or NOT RUN, 1 = a FAIL, 2 = a STOP (dry: 0 or 2). CI runs
every lane dry on every push (`testlab/tests/hardware`): the dry inventory, the NOT RUN cases, the frame coverage of every
`tx`, the lhpc calls parsed at the head (`cliforms.py`) and the redaction.

`testlab/hardware/fw-boxproof.sh dry|live` is the firewall box proof on box H, built the same way (the same local file,
the password on stdin). It checks a Meshtastic stack's power cap before each start (and the units on its radios, r2 and r4, at its first read
and before every restart; units on other radios stay untouched; `max_power` 0 is refused), needs the operator's call and never takes it as the lab identity, and on a STOP or a signal replays its recorded undos (exit 4 when one fails). Its LAN checks send the client cert named by `LAN_CERT` in the local file (a PEM file with the cert and its unencrypted key, mode 600;
the console's remote-auth answers 403 without one); the kit never reads a passphrase or a .p12.

## The evidence

A live run writes its evidence under `$EVD_ROOT` (the command outputs, `REPORT.md`, `COVERAGE.md`, `SHA256SUMS`); that
evidence is the run's record.

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
  by opening its port. That rnsd runs as your user with `rnsd --config <that path>` (also for the default directory):
  R0 accepts its hold on the RNode's port only then.
- On a box: `lhpc hardware` shows its radio setup ([CLI](../../docs/cli.md#hardware)).

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
