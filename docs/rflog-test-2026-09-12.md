# RF logs — live matrix, 2026-09-12

The 25-row proof the RF-log feature was planned with, run on the reference box with real radio
traffic. Measured values only; a row that could not be proven on the available hardware says so
and says why, rather than being dropped.

**Bench.** Box `lhpc-e293`, Pi Zero 2 W, `lhpc hardware` = `uputronics` (433 + 868). Controller
`lhpc 0.4.5` on `feature/rf-logger`; the console restarted after the checkout switch. Peers on the
developer machine: a BQ Station G2 (Meshtastic, 868, LongFast), a LilyGo T-Deck Pro (MeshCore,
869.618/62.5/SF8/CR8, driven over BLE) and a LilyGo T-Deck (MeshCom 4.35p, 433.175, driven over
BLE — its USB console is silent by build). There is **no LoRa-APRS peer and no second Reticulum
LoRa node**, so the kiss and Reticulum RX rows are reported as not proven; every TX row is a real
transmission. Log timestamps are UTC; the box clock is CEST.

**How the writers reached the box.** The daemon and the meshcom stack are on the binary channel,
whose artifacts predate the writers, and the moved pins cannot be carried by an artifact that does
not exist yet — the release publishes it first ([maintenance](maintenance.md#rf-logs)). So:
`lhpc install daemon --source pinned` + `lhpc build daemon` (RadioLib and the daemon from source at
`1623ae9`); `lhpc update kiss` + `lhpc build kiss` (`c2d359c`); `rns-lora-interface` via
`--source dev` — `--source pinned` honoured this box's known-working composition (`3fef542`) over
the manifest pin, and the branch head *is* the pin `b630aa1` — then `lhpc build rns` (the venv
reports 0.2.0); the MeshCom bridge cloned and cmake-built at `7c86c96` beside the runtime and its
binary copied over the artifact's (QEMU node and firmware artifact unchanged; the old binary kept
as `meshcom-loraham-bridge.artifact-35a9348`). meshtastic is config-only. The MeshCore node needed a
`node_name` (the box's had been reset) and ran in `chat` mode.

Evidence is the file itself (`lhpc rflog`, the log page and its API over the console socket), the
controller's typed outcome and the stack's own state. Frame bytes are quoted as the log wrote
them; the operator's callsign is written `<call>` here.

## Contents

- [A. The writers](#a-the-writers)
- [B. The switch](#b-the-switch)
- [C. The console](#c-the-console)
- [D. Retention, restart, power](#d-retention-restart-power)
- [Not proven here](#not-proven-here)

## A. The writers

| # | function | result |
|---|---|---|
| 1 | daemon writer, RX (both bands) | **PASS** — with `--radio 433` and `--radio 868` both running (each spawned with `--rflog on --rflog-path …/rf-daemon-<band>.log`), the T-Deck's MeshCom frames land in `rf-daemon-433.log` only (`18:33:54Z RX rssi=-53.00 snr=13.50 len=46 band=433`) and the T-Deck Pro's advert in `rf-daemon-868.log` only (`18:36:49Z RX rssi=-67.00 snr=11.25 len=112 band=868`); at that second the 433 file has no line (21 lines, all 433), the 868 file two |
| 2 | daemon writer, TX | **PASS** — a frame sent by the QEMU node appears as `18:34:28Z TX … len=43 outcome=ok band=433` **59 s after** the net-console write, i.e. after the daemon's `transmit()` returned, not when it was queued; `lhpc test kiss --tx` (TXOK 0→1) gives `18:47:03Z TX … len=22 outcome=ok` with `LHPC TX TEST DE <call>` readable in the ASCII column. A CAD-refused send was not observed in this run |
| 3 | kiss/graywolf writer | **TX PASS** — graywolf's own beacon through the TNC: `rf-kiss.log` `18:47:31.112Z TX rssi=- snr=- len=71 outcome=ok tnc2="<call>>APGRWO,WIDE1-1,WIDE2-1:!/…"`, the same frame 3 ms earlier in the daemon's file (`18:47:31.109Z`); a raw frame injected on the KISS port was not transmitted by the TNC and produced no line (not investigated). **RX not proven**: no LoRa-APRS peer |
| 4 | meshcom writer | **PASS** — RX: the T-Deck's text `RF-LOG row4b` arrives as `18:33:54Z RX rssi=-53.00 snr=13.50 len=46` with `XX0XXX-00>*:RF-LOG row4b` readable, in `rf-meshcom.log` 74 ms after the daemon's line; its mesh repeats follow as further RX lines (`DJ0CHE,XX0XXX-00>`). TX: the node's `::RF-LOG box4b` appears as `18:34:28Z TX … len=43 outcome=ok` on the daemon's `TX_RESULT`, 59 s after `submit_tx`; nothing is decoded — hex + dotted ASCII only |
| 5 | meshtastic native trace | **PASS** — the generated `meshtasticd.yaml` line 217 reads `TraceFile: /home/lhpc/loraham-pi-control/logs/rf-meshtastic.log` under `LogLevel: info`; the node writes one JSON object per packet from start. The G2's packets appear as `{"bytes":"73C6…","channel":8,"from":2733223640,…,"rssi":-91,"size":13,"snr":12,…}`. `lhpc meshtastic --info` keeps working (`:4403` untouched). Bench note: at the G2's 27 dBm one metre from the box the node reported `num_packets_rx=0` twice; at `lora.tx_power 3` every packet arrived — receiver overload, not the feature |
| 6 | meshcore writer | **PASS (chat mode)** — RX: the T-Deck Pro's flood advert `18:36:49.968Z RX rssi=-67.00 snr=11.25 len=112 hex=1100 37bd5d25f79f…` (its public key readable), the node's log `RX ADVERT (4)`. TX: `meshcore-cli advert` → `18:37:41.236Z TX … len=120 outcome=ok`, node log `TX 120 bytes (type=ADVERT)`, the daemon's line 55 ms earlier. No peer identity is logged — the boundary carries none. **`repeater` / `chat+repeater` not run** (needs the repeater identity set up); the writer is the one `LoRaHAMRadio` every mode uses |
| 7 | reticulum writer | **TX PASS** — MeshChat `GET /api/v1/announce` → `18:05:02Z TX rssi=- snr=- len=235 outcome=ok hex=7100…` and `18:05:17Z … len=197 outcome=ok` (the LXMF and the delivery announce), ciphertext. **RX not proven**: no second Reticulum LoRa node; transport-on relay therefore not observed either |
| 23 | MeshChat via reticulum | **PASS** — `POST /api/v1/lxmf-messages/send` to an unknown destination ("Could not find path") → `18:12:16Z TX … len=51 outcome=ok`, the destination hash readable in the hex: the path request went out as a raw Reticulum packet; there is no MeshChat log |

## B. The switch

| # | function | result |
|---|---|---|
| 8 | switch OFF | **PASS** — reticulum: after `lhpc stack restart reticulum` the config reads `rf_log = off`, an announce adds no line (4 → 4), the node runs unchanged. kiss: with the switch off the TNC starts with `--rflog off` and creates no file |
| 9 | switch ON again | **PASS** — reticulum: `rf_log on` + restart: the next announce appends (4 → 6) to the **same inode 412360**, the 18:05:02Z line still first. kiss: `lhpc config kiss rf_log on` + `lhpc stack restart graywolf` → TNC argv `--rflog on`, `rf-kiss.log` created; nothing but the writer's component was restarted |
| 10 | web switch = CLI switch | **PASS** — `POST /stacks/graywolf/rflog value=off` wrote `config/stacks/kiss.toml` (the band-less file) with `rf_log = "off"` and created no `graywolf.toml`; `lhpc config kiss rf_log` reads `off`; `lhpc config kiss rf_log on` removes the key (the default is never stored); the other kiss params were untouched throughout (the file held only that key). Read back on both bands: the same value |
| 21 | restart-pending | **PASS** — `lhpc config reticulum rf_log off` → submenu "Configured: **off** · restart required"; an announce still appends (2 → 4) until the restart, then no more (row 8). CLI: `lhpc status kiss` prints `! RESTART REQUIRED: 'kiss' — saved settings differ from the running stack` after the kiss switch changed |

## C. The console

| # | function | result |
|---|---|---|
| 11 | RF-Logs submenu | **PASS** — `stack-rflog-<id>` rendered on daemon, graywolf, meshcom, meshtastic, meshcore, reticulum and on none of chat, kiss, voice; the daemon card links `rf-daemon-433.log` and `rf-daemon-868.log`; graywolf's link is `/logs/loraham-kiss-tnc?job=rf-kiss.log` |
| 12 | page | **PASS** — `/api/logs/rns?job=rf-reticulum.log` returns the path, the lines (2, then 6) and `running: true` from the writer's state; the page polls it every 2 s (unchanged `logs.js`) |
| 13 | switcher | **PASS** — the page carries `<nav class="rfswitch">` with the seven jobs in registry order, the shown one `aria-current="page"`; `rf-daemon-433.log` before its first frame rendered "(no log file yet)" |
| 14 | Clear, scoped | **PASS** — also run by the operator from a browser at 21:08:04 on `rf-daemon-868.log` (POST `/logs/loraham-daemon/clear` → 302: 0 bytes on the old inode, `.1` absent, every other file untouched). By script: `POST /logs/meshcore-node/clear` on `rf-meshcore.log` (+ `.1`): 302, the file is 0 bytes on the **same inode 412352**, `.1` removed, `rf-reticulum.log` still 7 lines |
| 15 | Clear refusals | **PASS** — `rf-made-up.log` → 404 (its content untouched), `../rf-meshcore.log` → 404, `rf-meshcore.txt` → 404, `rf-reticulum.log` through the wrong writer → 404, missing CSRF → 400, `rf-meshcom.log` as a symlink to `/etc/hostname` → refused with "path escapes runtime root via symlink", the target intact and never displayed |

## D. Retention, restart, power

| # | function | result |
|---|---|---|
| 16 | per-job retention | **Writers: unit-proven, not forced live** — the copy-truncate cap is a compile-time 5 MB in every writer and no run produced 5 MB of frames; each repo's own test rolls at a test threshold and continues on the live inode. **meshtastic's opportunistic roll: proven live** (row 24) |
| 17 | restart survives | **PASS** — `lhpc stack restart graywolf` and `… meshtastic` with all seven RF files present: every inode and every line count kept (433: 25 → 26 with the restart beacon, kiss 1 → 2, meshtastic 13 → 26 with the node's boot packets, the five others unchanged); the run log `start-loraham-kiss-tnc-433.log` was rewritten (872 → 708 bytes, new mtime). Reticulum earlier: 2 → 4 → 6 lines on one inode across two restarts |
| 18 | power cycle | **PASS** — `POST /power/reboot` from the console at 21:15:47, box back at 21:16:45; all seven files identical before and after (inode, line count, byte count: 433 `421702`/27/5704, kiss `412357`/3/1086, meshcom `421707`/23/4464, meshcore `421730`/2/842, reticulum `412360`/7/4558, meshtastic `421736`/34/7863, the 868 daemon file as the operator had just cleared it); boot restore brought daemon, kiss, graywolf and meshtastic back ("2 restored, 2 skipped") and the trace resumed on the same inode (34 → 46 lines within a minute) |
| 19 | write rate | **measured, quiet channel** — the Meshtastic trace over 30 min (20:45:30 → 21:15:40) with the G2 at 3 dBm and the node's own telemetry: 8 → 34 lines, 2,002 → 7,863 bytes, i.e. ~0.9 lines/min and ~195 B/min (~280 kB/day); one restart added 13 lines of boot packets. Against the 5 MB cap that is weeks of headroom on this channel; a genuinely busy 868 channel was not available |
| 24 | meshtastic retention | **PASS** — the trace padded to 6,219,851 bytes, one page read → the live file 0 bytes on the **same inode 421736**, `.1` = 5,242,814 bytes starting on a line boundary; meshtasticd kept appending to that inode. A pad of 4,670,961 bytes (under the cap) did not roll |
| 25 | viewer after rollover | **PASS** — right after that roll the API still returned 300 lines: the tail of `.1` followed by the live file's new packets |

## Not proven here

- **Kiss/graywolf RX and Reticulum RX** — no LoRa-APRS peer and no second RNS LoRa node on the bench; the relayed-packet part of row 7 with it.
- **Row 22, a Reticulum `unconfirmed` transmit** — cannot be provoked on demand; every transmit in this run confirmed (`ok`). The duty-dropped case is unit-proven in the driver.
- **MeshCore `repeater` / `chat+repeater`** — the box ran `chat`; the writer sits in the radio adapter shared by every mode.
- **A CAD-refused daemon send** (row 2's negative half) — not observed; unit-proven in the daemon.
- **The writers' 5 MB rollover live** (row 16) — see above.
