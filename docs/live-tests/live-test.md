# Release matrix, 0.10.0 — box D (`lhpc-0ae1`, Pi 5) and box E (`lhpc-e293`), 2026-09-26

Run on `candidate/0.10.0-highpower` at `a7eabcf` (0.10.0 with the high-power opt-in, daemon 1.2.0); the
KISS fix it made necessary (below) was proven with kiss 0.6.3 on `a7eabcf` + the kiss repin (the pinned
kiss commit differs from the one run only in comments, its changelog and a test name). The matrix and the
soak ran MeshCom firmware `71f51be`; the release pins `f2b96dff`, proven separately below
([final firmware delta](#final-firmware-delta)). Box **D** =
Raspberry Pi 5, 4 GB, Desktop image, LoRaHAM dual board (433 + 868), no GPS receiver (`use_gps off`),
operator `DJ0CHE`, MeshCom `DJ0CHE-15`. Box **E** = Pi Zero 2 W, Lite image, Uputronics bare SX1278
(433) + SX1276 (868), upgraded from a candidate controller by moving the checkout (`self-update` refuses
a checkout that is not on `main`).

Evidence rule: the controller's own typed outcome plus the stack's own state, and an outside witness
(a peer device) wherever RF is claimed.

## Box D — rows

| # | stack | channel | build | evidence |
|---|---|---|---|---|
| 1 | `daemon` | binary | — | *refused* by the pins gate until the release's daemon binary is published (published 1.1.1, pin 1.2.0) |
| 2 | `chat` | pinned | 1 s | the printed command ran as printed and exited with rc 0 |
| 3 | `voice` | pinned | 2 s | on the Desktop image the GTK voice starts verified; the terminal variant is skipped with its reason |
| 4 | `kiss` | pinned | 2 s | verified; TCP `127.0.0.1:8001` open |
| 5 | `graywolf` | release | 2 s | verified; web UI `:8080` → 200 |
| 6 | `reticulum` | pinned | 81 s | ready marker; MeshChat `:8790` → 200; `rns=1.5.4` in every client, `lxmf differs` (1.1.0 in lxmd, 1.1.1 elsewhere) |
| 7 | `meshcore` | pinned | 77 s | chat+repeater; web UI `:8788` → 200, dashboard `:8000` → 200; one plugin manager |
| 8 | `meshtastic` | binary | *refused* (binary) | verified; `lhpc meshtastic --info` → `LHPC Pi5`, firmware 2.7.26 |
| 9 | `meshtastic` | pinned (from source) | 636 s | as row 8 |
| 10 | `daemon` | pinned (from source) | 40 s | daemon 1.2.0; 433 + 868 `RADIO=READY` |
| 11 | `meshcom` | binary | — | *refused* by the pins gate until the release's meshcom binary is published |
| 12 | `meshcom` | pinned (from source) | 559 s | firmware `71f51be`, QEMU 9.2.2 with its licence texts and source note; verified; `:18083` → 200 |

Memory: at least 2.4 GB free (free + cache) during every heavy build, no OOM.

## Box D — cross-cutting checks

| check | result |
|---|---|
| **web console** | 21 pages: `/`, `/stacks`, `/gps`, `/logs`, every `/stacks/<stack>` (302) and its body (200); 0 tracebacks |
| **pins vs binaries** | every source component `match`; the one `differs` is the documented LXMF split |
| **`dev` selector spot-check (kiss)** | `install --source dev` resolved the branch tip (`mutable-dev`); built and started; reinstalled on the default channel: `match` |
| **known-working** | `lhpc known-working kiss` recorded the composition after a green start |
| **boot restore** | kiss and MeshCore running, one reboot: `done — 3 restored, 0 failed`; console 200 |
| **auto-install consistency** | every stack purged, then `lhpc auto-install --yes`: 154 s, **2 of 9** — the daemon's default channel is the binary, refused until the release's daemon binary is published, and the seven radio stacks depend on it; nothing reads "not built", every announced log exists |
| **host tests** | all nine rc 0 (daemon 228 s, meshcore 83 s, meshcom 20 s, kiss 4 s ran tests; chat, voice, graywolf, reticulum and meshtastic print their TX-safe plan) |
| **high-power** | refusals only on this box: both bands `HIGHPOWER=0 CHIPFAMILY=SX127x`; daemon `SET POWER=20` → `ERR INVALID`; lhpc refuses before the socket; the switch rows were not run (lab instruction, 2026-09-26) |
| **from-zero reinstall** | not run: `install.sh` installs `main`, which carries this release only after the tag |

## Peers (box D)

| peer | result |
|---|---|
| Meshtastic ↔ Station G2 (868) | a Pi 5 broadcast heard by the G2; a G2 broadcast decrypted in the Pi 5's RF log |
| MeshCore ↔ T-Deck Pro (BLE, 868) | adverts both ways; a message each way, the Pi 5's arriving in its companion inbox after its ACK |
| Reticulum ↔ Heltec RNode (868) | LXMF from the PC to MeshChat (direct, one hop via the RNode) and from lxmd's venv to the PC |
| APRS ↔ CA2RXU T-Beam (433) | **failed on `a7eabcf`**: Graywolf reported every message sent while the daemon transmitted nothing — the KISS TNC dropped daemon 1.2.0's 283-character `STATUS` line (its line buffer was 256 bytes) and never transmitted without it. **Passed with kiss 0.6.3** (512 bytes): the tracker's serial shows the message received and its `ack003` sent; Graywolf marked it acked after one attempt; the daemon counted one TX and one RX |

## New in 0.10.0 — proven on hardware

The first two rows are this run's; the rest were proven the night before on the same day's earlier
candidate (`95c020b`), in code this run did not change.

| function | proof |
|---|---|
| +20 dBm opt-in | box E (bare SX1278): the rows of [the high-power test](high-power-test-2026-09-26.md), twice (once by each of two operators), one frame at 20 dBm each time |
| binary update moves a stale MeshCom checkout | box D, released 0.9.2 → candidate with a compatible older known-working record: the checkout moved to the manifest pin, the install went on, MeshCom booted |
| `stack start --band` | box D: one daemon on 868, the 433 socket absent; `--band 915` is a usage error |
| restart-required clears | box D: an unchanged write raises no flag; a real change flags; changing back clears |
| advisory Companion claim | box D: `meshcore-cli` attaches while the web UI runs; the web UI yields and reconnects after `quit` |
| RF-log decrypt, Reticulum link data | box D with a Heltec RNode: a split LXMF DIRECT is labelled link traffic |
| RF-log decrypt, Meshtastic DM | box E with a Station G2: the DM decodes to its plaintext |
| openhop-repeater `b846c79` | box E with a T-Deck Pro: build without a compiler, the dashboard through the proxy, the plugin cycle, adverts both ways |
| a busy build is named | box D: a start refused during a build names the sources it builds |

## Box E — upgrade, reboots, fast lane, negatives

- **Upgrade:** checkout moved to the candidate, the controller reinstalled, `self-update
  --repair-integration`: all 7 managed units unchanged; `/healthz` ok.
- **The daemon binary after the upgrade:** with the published 1.1.1 binary still installed, the next boot
  restored nothing (`0 restored, 1 failed`) and a start reads `[BLOCKED] daemon: installed binary artifact
  is behind the manifest`. See the changelog's upgrade note; the release publishes the 1.2.0 binary.
- **Daemon 1.2.0 from source on the Zero:** 261 s.
- **Reboots:** three with the daemon at the pin, each `2 restored`; one plugin manager; `fake-hwclock`
  loaded the shutdown instant; chrony offsets below 0.2 ms.
- **Fast lane** (rows 9, 10 and 12 not re-run on the Zero): daemon (source), chat (rc 0), voice (terminal
  variant), kiss, graywolf, reticulum, meshcore and meshtastic binary pass; meshcom binary is refused by
  the pins gate until the release's binary is published.
- **Negative rows:** meshtastic on 868 while MeshCore holds it — a typed conflict naming both; `--band 915`
  — a usage error; stopping the daemon under kiss and MeshCore stops both as dependents; `kill -9` of the
  MeshCore host reads `(degraded)` and a restart recovers; an unchanged setting write leaves the file
  byte-identical and raises no restart flag.
- **Web console:** `/`, `/stacks`, `/healthz` and every stack page → 200, no traceback.

## Soak (box E)

2 h 06 min on `a7eabcf` (MeshCom firmware `71f51be`): MeshCom on 433 from the candidate's meshcom
artifact (built from that candidate, not yet published, installed through the real CLI with only its
download served locally), MeshCore on 868 and the console. Peers every 5 min: a T-Deck (MeshCom, 433) and a
MeshCore node (868).

| check | result |
|---|---|
| restarts, OOM | none (QEMU, bridge and plugin manager kept their PIDs across 123 samples) |
| memory | MemAvailable 109–141 MB with no downward trend; swap a plateau at 293–330 MB |
| console | 377 probes, none unanswered, max 1.79 s, no freeze ≥ 10 s |
| box → air (MeshCom channel message) | 25/25 on air, latency median 1.94 s, max 2.90 s; all 25 heard back relayed by the T-Deck |
| T-Deck → box (MeshCom DM) | 25/25 received and acknowledged, RX → ACK median 1.6 s |
| MeshCore peer → box | 25/25 received and acknowledged |
| growth | logs +120 KB, `/run` +4 KB; `fake-hwclock` saved hourly |

The install printed "moved meshcom-qemu to its pin (run scripts)": the binary update moving a stale MeshCom
checkout, on the Zero.

## Final firmware delta

After the matrix and the soak, the MeshCom firmware pin moved from `71f51be` to `f2b96dff` (the fork's
`lhpc-speed` after upstream `6cc8b552` and the final speed pull requests). That change touches the message
and ACK path (`sendMessage`, `SendAckMessage`), so it was proven on its own on box E:

the candidate's meshcom artifact built for `f2b96dff` (not yet published, installed through the real CLI with
only its download served locally), and a T-Deck on its **stock** MeshCom 4.35p firmware as the peer. The
witness for both directions is the box's RF log (the stock T-Deck prints nothing with debug off).

| check | result |
|---|---|
| boot and callsign | all three components verified; the callsign confirmed after boot |
| box → T-Deck message | on air once as message 017; the T-Deck's `ack017` received 4.0 s after the transmission |
| T-Deck → box message | received once as message 693; the box's `ack693` on air 6.3 s later |
| no duplicate, no wedge | 18 minutes: each message exactly once, no peer retry, 36/36 console probes answered (max 0.27 s) |

The box's own message took 6.5 s from the console to the air and its ACK 6.3 s — slower than in the soak
(≤ 2.9 s). These are single samples; both acknowledgements were accepted and nothing was retried.
