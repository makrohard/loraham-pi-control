# Reticulum against a real RNode — live test 2026-09-24

Run by the maintainer's agent, unattended, on the maintainer's instruction; the fix it led to is 0.9.1's airtime default.

**Purpose (maintainer, verbatim):** "prove lhpc working against real hardware for reticulum. prove as many functions as you can."
**Setup:** PC host = Reticulum 1.5.4 (`~/claude/.venv-rns`, config `~/claude/rns-host`) on a Heltec LoRa32 V3 flashed as
RNode 1.86 (868, `/dev/ttyUSB1`, txpower 7 dBm). Box = `lhpc-e293` (Pi Zero 2 W, Lite image, controller **main@1dfc5e8 = v0.9.0**,
Uputronics 868 module), reticulum stack at its defaults (868.5 MHz, BW 125 k, SF8, CR 4/5, 14 dBm, no IFAC) plus
`rnode_framing = yes`. MeshCore chat+repeater (the maintainer's normal 868 holder) stopped cleanly first; restored at the end.
Distance ~1 m (RSSI −41…−68 dBm). Box clock is UTC, PC clock CEST (UTC+2). Runtime changes only; no code, no builds on the Zero.

**Result: 19 rows, 17 PASS, 2 FAIL-by-design (rows 12/13, both the airtime governor at defaults → F-R2).** Run 16:31–16:55 CEST,
unattended, coordinated with the teammate (box acked, released 14:54:56Z). Box left as found (MeshCore chat+repeater back on 868, settings reset,
the `secrets.toml` created by row 16 removed — it did not exist before).

| # | LHPC function under test | Result | Evidence |
|---|---|---|---|
| 1 | exclusive-radio handover: `lhpc stack stop meshcore --yes` frees 868 ("stopped daemon 868: no other stack needs it"), plugin marker cleared by the clean stop | PASS | phase1.log 14:31:54 |
| 2 | `lhpc config reticulum rnode_framing yes` persists; render puts `hardware = uputronics`, defaults, `mode = internal` into `state/reticulum/config` | PASS | rendered [[LoRa]] section |
| 3 | `lhpc stack start reticulum --yes` → `[verified] rns: started; ready endpoint(s) up`; shared instance :37428/:37429, client access :4242 present | PASS | phase1.log 14:32:23, `lhpc status` |
| 4 | `lhpc stack start meshchat --yes` (optional component, joins the shared instance) → :8790 up, `/api/v1/config` answers | PASS | lxmf address 28529dd4… |
| 5 | RX from a real RNode with `rnode_framing`: PC announce heard (RSSI −58, SNR 12.5, 236 B), path installed 1 hop via LoRaSPIInterface | PASS | rf-reticulum.log 14:32:55, box `rnpath -t` |
| 6 | RF-log decoder strips the RNode header and decodes the announce incl. display name | PASS | `lhpc rflog reticulum --decrypt`: "announce 8de29ca21bda PC-RNode-Bench" |
| 7 | TX to a real RNode: MeshChat announces (`GET /api/v1/announce`) leave the box `outcome=ok`; PC installs paths via RNodeInterface | PASS | rf log 14:34:29/14:34:44 TX; PC `rnpath -t` |
| 8 | `rnprobe lxmf.delivery <box>` PC→box over the air: valid reply, RTT 928 ms, 1 hop, RSSI −41, SNR 13.5, 0 % loss | PASS | rnprobe output |
| 9 | LXMF opportunistic PC→box (44 B): proof back in 1 s; MeshChat lists it "in"; decoder reassembles the split packet (255+6, seq 9) and DECRYPTS the LXMF with the box's keys; proof TX logged | PASS | peer.log 16:35:18; conversation API; rflog 14:35:18 |
| 10 | LXMF DIRECT (link) PC→box, 288 B: link request/proofs/link data over LoRa, delivered in 3 s, MeshChat "direct in"; decoder labels link traffic "never decryptable" | PASS (note F-R1) | peer.log 16:36:02; rflog 14:35:59–14:36:05 |
| 11 | LXMF box→PC through MeshChat `POST /api/v1/lxmf-messages/send` (opportunistic): MeshChat "delivered", PC peer DELIVERED 1 s later, proof RX logged | PASS | peer.log 16:36:26; rflog 14:36:26 |
| 12 | `rncp` file transfer box→PC (1454 B, compressed) over a link at the box's DEFAULT airtime limits (5 % / 15 s short window) | FAIL at defaults — see F-R2 | rf log 14:37:36–14:39:24: link up in 1 s, then the box sends ONE 196-B resource frame per ~15 s while the PC re-requests every ~5 s; the sender exited before completion, the interface queue kept draining afterwards |
| 13 | `rncp` box→PC with a 256 B file, air quiet 85 s before, DEFAULT limits, sender timeout 300 s | FAIL — sender: "File was not accepted" after 31 s (14:40:51→14:41:22); PC listener: "Starting resource transfer" 16:41:23 → "Resource failed" 16:42:39 | rncp2.log; rncp.log; rf log: box TX every 15 s (14:41:08, :23, :38, :53, 14:42:08, :24), PC requests every ~4–8 s |
| 14 | LXMF DIRECT PC→box, 929 B (carried as a resource over a link) at the box's DEFAULT limits | PASS — delivered in 28 s (16:43:44→16:44:12), MeshChat lists it "direct in" 929 B | peer.log; conversation API; rf log 14:43:56–14:44:13: PC sends 196/255/150/212 B parts, box answers with 84–119 B proofs/requests that fit its window |
| 15 | governor setting: `lhpc config reticulum airtime_limit_short 25` + `lhpc stack restart reticulum --yes` → rendered `airtime_limit_short = 25`; the SAME 256 B `rncp` box→PC | PASS — "Transfer complete … 2.00 Kbps", 7 s (14:48:28→14:48:35), PC saved `small.txt`, sha256 f32f8526… identical | rncp3.log; rncp.log 16:48:34; state/reticulum/config |
| 16 | IFAC set up through lhpc: `lhpc config reticulum ifac_netname lhpcbench` + `[reticulum] ifac_netkey` in a fresh 0600 `config/secrets.toml` + restart → rendered `networkname`/`passphrase` on [[LoRa]], rns + meshchat up | PASS | ifac.log 14:50:39–14:51:14 |
| 17 | IFAC negative: PC announces WITHOUT IFAC → box hears the frame (RX −59 dBm, 236 B) but installs NO path for 8de29ca2 (`rnpath -t` empty for it); the offline RF-log decoder still reads the raw announce, as designed ("what the radio heard") | PASS (rejected as expected) | rf log 14:51:40; rnpath |
| 18 | IFAC positive: PC RNode interface with `networkname = lhpcbench` / `passphrase = bench-ifac-2026` → box installs fresh 1-hop paths for both PC destinations (expires …16:52:38), IFAC-wrapped announces (244 B vs 236 B plain) decoded by the RF-log decoder | PASS | rf log 14:52:38; rnpath; rflog --decrypt |
| 19 | LXMF opportunistic PC→box under IFAC (36 B) | PASS — proof back in 3 s (16:53:20→16:53:23) | peer.log |

## Findings

- **F-R1 (decoder, cosmetic):** the second frame of a split LINK packet is labelled `data <linkid>: [not addressed to this node]` although the link is this node's (rflog 14:36:02.076Z, 230 B, link 5378da0a). The first-half line and the link lines around it are right; only the reassembled-data label is misleading. Cosmetic, no data loss.
- **F-R2 (airtime governor vs. resource transfers):** with `airtime_limit_short = 5.0` (%, 15 s window) one 196-B frame at SF8/BW125 (~0.7 s) already fills a window, so a Reticulum Resource (rncp, big LXMF attachments) is paced to one frame per 15 s and the sender times out. Single-packet traffic (announces, probes, LXMF ≤ ~500 B, links) is unaffected — rows 5–11. This is the governor doing what it is configured to do for 868 SRD, not a bug; but the default makes rncp/attachments impractical, and nothing tells the user why the transfer stalls. Measured pacing: TX at 14:37:52, 14:38:07, 14:38:23, 14:38:38, 14:38:53, 14:39:08 (15.1 s ± 0.4). Row 13 shows it is not a size problem: a 256 B file fails the same way — the Resource handshake needs several box replies inside RNS's own timeouts, and the governor allows one per window. Direction matters (row 14): a resource INTO the box works at defaults, because the box only sends short proofs/requests; a resource OUT of the box (rncp, a large LXMF or attachment sent from MeshChat on the box) starves. So on a default box: receiving big messages works, sending them does not.
- **F-R3 (observation):** `lhpc stack restart reticulum` restarted `rns` but left the optional `meshchat` component STOPPED (it was running before); `lhpc stack start meshchat` was needed again. Whether restart should re-raise optional components that were up is a design question for the maintainer, not a defect claim.

## What this run does NOT prove

- Only the **868** band and the **Uputronics** module on e293; 433 and the LoRaHAM RFM98PW were not on the air.
- `lxmd`, `nomadnet`, Sideband (not on Lite), propagation nodes, `enable_transport`, the `[[Internet]]` interface and `lora_announce_relay`
  were not exercised — the stack ran at its defaults with MeshChat as the only client.
- One RNode at ~1 m (RSSI −41…−68). No range, no multi-hop, no collision behaviour.
- The RNode side used Reticulum 1.5.4 and RNode firmware 1.86; the box pins Reticulum 1.5.2 (MeshChat's closure carries 1.5.4).

## Recommendation on F-R2 (for the maintainer, not implemented)

Either raise `airtime_limit_short` for 868 (the 1 %/h long limit is the regulatory one; the 15 s short window at 5 % is LHPC's own smoothing
and starves any multi-frame reply the box must send), or make the console say why a transfer stalls (the driver knows it is holding a frame
for the next window). Receiving is unaffected, so a box that only reads messages never sees this.
