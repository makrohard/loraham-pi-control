# RF-log viewer and decrypt — live proof, 2026-09-13

The decoders of the RF-log viewer/decrypt feature, run on the reference box against real radio
traffic from three peers, once per encrypted stack. Measured values only; a row that could not
be proven on the available hardware says so and says why, rather than being dropped.

**Bench.** Box `lhpc-e293`, Pi Zero 2 W, `lhpc hardware` = `uputronics` (433 + 868). Controller
on `feature/rflog-view-decrypt` in the 0.5.0 cycle; the console restarted after each checkout
move. Peers on the developer machine: a BQ Station G2 (Meshtastic, 868, LongFast, `lora.tx_power
3` — at 27 dBm next to the box the receiver saw nothing, a bench artefact known from the RF-log
run), a LilyGo T-Deck Pro (MeshCore, 868, driven over BLE) and a LilyGo T-Deck reflashed to the
official RNode firmware (Reticulum, 433, an `RNodeInterface` on this machine at 434.500 MHz /
125 kHz / SF8 / CR5). Log timestamps are UTC; the box clock is CEST. The operator's position is
written `<lat>, <lon>` here.

**How the feature reached the box.** The controller checkout moved to the branch; nothing else
was installed or updated. Two things the box taught, both in the feature commit: the managed
Meshtastic CLI venv had no AES library, so the manifest now pins `pycryptodomex==3.23.0` into it
as a `build_inputs` entry (the same pip command was run by hand on the box, and the new input
line recorded beside the installed artifact — what a rebuild writes; on the binary channel the
release publishes the artifact that carries it); and the RNS venv has no LXMF, so the reticulum
decoder runs under LXMF's venv when that component is built. Three defects were found live and
fixed before this report: the venv interpreter (a symlink by design) was refused as a root
escape; `--follow` output was block-buffered through a file and lost a burst between polls
under a small `--lines`; the reticulum decoder was handed the config *file* as its configdir and
read an empty config, which said "no IFAC" on a box that had one.

Evidence is `lhpc rflog <stack> --decrypt [--follow]` on the box, the decoded API and the log
page over the console socket, and the stacks' own logs.

## Contents

- [A. Meshtastic](#a-meshtastic)
- [B. MeshCore](#b-meshcore)
- [C. Reticulum](#c-reticulum)
- [D. Surfaces and boundaries](#d-surfaces-and-boundaries)
- [Not proven here](#not-proven-here)

## A. Meshtastic

`rf-meshtastic.log` is meshtasticd's own trace: an encrypted `bytes` record per packet on the
air (`channel` = the channel hash) and, for packets the node decoded itself, a `payload` record
(`channel` = the channel index). The decoder opens the first kind with the PSKs in
`prefs/channels.proto` and shows the second as it is.

| # | frame | result |
|---|---|---|
| 1 | G2 channel text, RX | **PASS** — `22:26:22Z RX rssi=-98.00 snr=9.50 len=27 text !a2e9aed8 → ^all: [LongFast] RF-DECRYPT g2c 222432` decoded from the 27-byte ciphertext with the (public) LongFast key; a second one at `22:27:36Z` (`g2f`). The G2 transmits a text 30–60 s after the CLI's "Sending" line; its nodeinfo goes out first |
| 2 | nodeinfo, telemetry, position, ack, RX | **PASS** — `len=80 nodeinfo … [LongFast] CHE Station (cheS) !a2e9aed8`, `len=32 telemetry … [LongFast] 26 B` beside the node's own `telemetry … air_util_tx=… battery_level=101 …`, `len=43 position !9ee3dad0 → ^all: [LongFast] <lat>, <lon>, alt 505 m` (the box's own position, rebroadcast by the G2, `hops_away 1`), `len=13 routing !a2e9aed8 → !9ee3dad0: [LongFast] ack` |
| 3 | G2 direct message (public-key), RX | **PASS** — the 42-byte packet on channel hash `0x00` addressed to the box, opened with the node's own key (`prefs/config.proto`) and the G2's public key from the node database: `22:26:56Z RX rssi=-100.00 snr=9.50 len=42 text !a2e9aed8 (cheS) → !9ee3dad0 (LHPB): [direct] RF-DECRYPT g2e dm 222614`; the RF-log run's `RF-LOG row5b 204221` (37 B) opens the same way. The nonce layout (packet id, extra nonce, sender — the firmware's `initNonce`) was pinned on the box before it went into the decoder; the decoded API reports both as `ok`. In the first pass of this run the same frames read `[no key for channel hash 0x00]` — the row was corrected when the decoding was added |
| 4 | foreign channel, RX | **PASS (typed)** — `20:00:37Z RX … len=45 !da73eafc → ^all: [no key for channel hash 0xa2]`, `no-key` |
| 5 | the box's own text, TX | **by design** — `lhpc meshtastic --sendtext` gives `[ServerAPI] Received text msg from=0x0` in the node log and a `meta` record in the trace (packet id, hops); meshtasticd writes no payload for its own transmissions and the G2 did not rebroadcast it. Shown as `meta`, never as a gap |
| 6 | CLI `--follow` | **PASS** — a `tmux` session running `lhpc rflog meshtastic --decrypt --follow --lines 1 > file`: the file grows per batch (flush fixed on the box); see B.5 for the burst |
| 7 | the node's packets to itself | **typed** — 5–6-byte `bytes` records with `from` = `to` = the node (the CLI's local API traffic the trace records, `23:24:20Z TX len=5 …`) read `local packet, 5 B (the node to itself)`, never a decryption attempt |
| 8 | node names | ids carry the node database's short name: `!a2e9aed8 (cheS)`, `!9ee3dad0 (LHPB)` |

## B. MeshCore

`rf-meshcore.log` is the host's radio adapter's line per frame. Identity and stores by LHPC's
`mode`: chat = `config/secrets/meshcore_identity.key` + `state/meshcore/companion.db`
(contacts: `CHEMobile`; no channels stored — the Public channel's well-known key is built in);
repeater = `openhop_repeater_identity.key` + `state/openhop/repeater.db`.

| # | frame | result |
|---|---|---|
| 1 | T-Deck advert, RX, chat | **PASS** — `22:33:11.102Z RX rssi=-70.00 snr=10.50 len=112 advert 37bd5d25: CHEMobile key 37bd5d25f79fffe0…` (Ed25519 signature verified over pubkey + timestamp + appdata) |
| 2 | T-Deck Public-channel text, RX, chat | **PASS** — `22:33:22.261Z RX … len=53 channel Public: CHEMobile: RF-DECRYPT chan 223320` (MAC-then-decrypt with the built-in Public key) |
| 3 | T-Deck → box direct, RX, chat | **PASS** — `22:33:34.160Z RX … len=38 direct CHEMobile → me: RF-DECRYPT dm2box 223332` (shared secret from the box's seed and the stored contact key) |
| 4 | box → T-Deck direct, TX, chat | **PASS** — `meshcore-cli -t 127.0.0.1 msg CHEMobile …` → `22:35:26.047Z TX len=38 outcome=ok direct me → CHEMobile: RF-DECRYPT box2tdeck 223523`, then `len=6 ack: ack e74dc429` and a `multipart`/path frame typed as such |
| 5 | CLI `--follow`, burst | **PASS after a fix** — following with `--lines 1`, three frames arrived within one 2 s poll (`34.160Z`, `34.978Z`, `35.827Z`) and the first was lost: `--follow` now reads the full 300-line tail between polls and dedups by key; regression in `tests/cli/test_cli.py` |
| 6 | repeater mode | **PASS** — `repeater_name LHPC-REP`, `mode repeater`, restart: the T-Deck's advert `22:36:55Z RX … advert 37bd5d25: CHEMobile …` and its repeat by the box `22:37:00Z TX len=113 outcome=ok advert 37bd5d25: CHEMobile …`; the channel text `22:37:07Z RX … channel Public: CHEMobile: RF-DECRYPT rep-chan 223705` and its repeat `22:37:09Z TX …`; a direct message addressed to the *companion* key `22:37:19Z RX … len=38 direct 37 → 87: [between other nodes]` — the repeater identity is not that pair, typed `undecryptable`. Mode restored to `chat` afterwards; `repeater_name` left set |
| 7 | the mode resets the cache | unit-proven (`test_the_cache_is_bounded_and_the_meshcore_mode_resets_it`); on the box the repeater rows decoded under the new identity immediately after the restart |
| 8 | path returns, RX and TX | **PASS** — the pairwise frames around the direct message (same layout as text: dest hash, src hash, MAC + cipher under the pair's secret): `22:33:34.978Z TX len=22 outcome=ok path me → CHEMobile: path 0 hop(s) - extra type 0x03 6 B d860a49300af` and `22:33:35.827Z RX rssi=-70.00 snr=11.00 len=22 path CHEMobile → me: path 0 hop(s) - extra type 0xff 4 B aa926a8d` |
| 9 | requests, responses, anonymous requests, channel data, trace | **unit-proven** with openHop's own crypto and generated identities (`test_every_pairwise_frame_this_node_is_part_of_opens`, `test_an_anonymous_request_to_us_opens_and_a_login_never_shows_its_password`, `test_channel_data_and_trace_frames_are_readable`); the bench produced none of these frames (a repeater login would) |

## C. Reticulum

`rf-reticulum.log` is the LoRa driver's line per frame, written before RNS decides what it is.
For this slot the stack ran on the **433** module (graywolf, kiss and the daemon stopped, the
console's band-aware start form) at 434.500 MHz, `txpower 5`, with MeshChat as the LXMF peer;
afterwards everything was put back (868, IFAC cleared, the bench key removed from
`config/secrets.toml`, the 433 stacks restarted).

| # | frame | result |
|---|---|---|
| 1 | MeshChat announces, TX | **PASS** — `GET /api/v1/announce` on the box → `22:45:18.401Z TX len=235 outcome=ok announce 32ee17caaf2b: announce 32ee17caaf2b Anonymous Peer` (the LXMF delivery destination; signature validated with RNS's own `validate_announce`, the display name from the app data) and `len=197 … announce 6f26638a45f2 … Anonymous Peer` |
| 2 | PC RNode announce, RX | **not achieved — radio interop, see 4** — one frame in ten minutes of announcing from one metre: `22:48:31.054Z RX rssi=-69.00 snr=12.00 len=181 hex=c001009c…`. Its first byte is no RNS header (an announce from the PC starts `71`), so it is a garbled reception with the IFAC bit set by error; the decoder typed it honestly for what the bytes say (`[IFAC frame, no network name/passphrase configured]`, later `[IFAC does not match this network]`, both `no-key`) |
| 3 | IFAC unmasking | **PASS** — a bench IFAC (`ifac_netname labnet` on the 433 band + `ifac_netkey` in `config/secrets.toml`; a half-configured IFAC is refused by the driver, which the first attempt showed), stack restarted: the box's own masked announces `23:01:16.713Z TX len=243 outcome=ok` and `23:01:17.382Z TX len=205` decode through `Transport.handle_ifac` to `announce 32ee17caaf2b Anonymous Peer` / `announce 6f26638a45f2 Anonymous Peer` (8 bytes longer than in row 1: the IFAC). This is the row that exposed the configdir defect |
| 4 | single packet to the box, link request | **not proven live — the RNode and the driver never exchanged a frame** — 25 minutes of PC announces (7 and 10 dBm, with and without the bench IFAC) gave the box nothing but the garbled frame of row 2; a PC-side sniffer printing every frame the RNode hears (`Transport.inbound` hooked) heard nothing while the box announced twice (235 + 197 B at 23:35:48). Both radios report 434.500 MHz / SF8 / BW125 / CR5; the driver (`loraham-rns-interface`) uses sync word `0x12` and an 8-symbol preamble, the RNode firmware reports an 18-symbol preamble and fixes its own sync word — neither is a reticulum setting lhpc exposes. Follow-up: expose `syncword`/`preamble` (the driver already reads them) and re-run these rows, or run them between two LoRaHAM boxes. A single packet to the box's own destination cannot be witnessed from the box itself: RNS delivers a local destination locally, nothing goes on the air. The decode of a ratcheted and an unratcheted single packet, and the `link` typing, are proven with generated identities in `test_reticulum_decoder.py` (RNS's own `Identity.encrypt`/`decrypt`, the ratchet file as LXMF writes it) |
| 6 | path requests, TX | **PASS** — RNS's own plain-destination packets read: `18:12:16.942Z TX len=51 outcome=ok path-request 6b9f66014d98: path request for a2e9aed8b0c1` (MeshChat's send to an unknown destination in the RF-log run) and `23:32:30.060Z … path request for 32ee17caaf2b` (`rnpath` on the box) |
| 5 | decoded API | **PASS** — `GET /api/rflog/rns/decoded?job=rf-reticulum.log` → 200, `Cache-Control: no-store`, `error ""`, 12 records with the statuses above |

## D. Surfaces and boundaries

| # | function | result |
|---|---|---|
| 1 | page | **PASS** — `/logs/meshtastic?job=rf-meshtastic.log` carries `data-decoder="1"`, the switcher, then `class="rfdecrypt"` with `id="rf-decrypt" aria-pressed="false"` on its own row, then the filter; the `decoded` column exists only there. `/logs/loraham-kiss-tnc?job=rf-kiss.log` and `…/loraham-daemon?job=rf-daemon-433.log`: `data-decoder="0"`, no toggle |
| 2 | decoded API | **PASS** — meshtastic 200 / `no-store` / 300 records, 92 `ok`, 49 `no-key`, `error ""`; meshcore 200; the plaintext logs `404` |
| 3 | records API | **PASS** — 300 records with `ascii band dir hex key len outcome raw rssi snr summary ts` |
| 4 | first decode cost | measured — `lhpc rflog meshtastic --decrypt --lines 8` cold: 5.2 s wall on the Zero 2 W (decoder interpreter start + protobuf); a warm poll serves cached records without a subprocess |
| 5 | nothing written | **PASS with one nuance** — stacks stopped, inventory of `logs/ state/ config/` (path, size, mtime; `state/run` excluded) before and after 600 CLI-decoded lines and two decoded-API calls: the only changed entries were `state/meshcore/companion.db-wal` / `-shm` (SQLite's read-lock files for a WAL database opened `mode=ro` — the mechanism, no content) and `logs/nginx-access.log` (the URL, documented). No decoded text anywhere |
| 6 | host header | the console refuses an unknown `Host` (`400 … does not answer to the host "x"`); the socket rows use `localhost` |

## Not proven here

- Reticulum: a single packet to the box and a link request on the air (C.4): the official RNode
  firmware and the LoRaHAM driver did not interoperate on this bench (sync word / preamble are
  the suspects; neither is exposed by lhpc). The unit suite covers the decode with RNS's own
  primitives.
- MeshCore requests, responses, anonymous requests, channel data and trace frames: none on the
  bench; unit-proven with openHop's own crypto (B.9).
- The box's own Meshtastic transmissions: meshtasticd traces them without payload (A.5).

**Box left as:** controller on `feature/rflog-view-decrypt`; graywolf (433) + meshtastic (868)
running as before the run, meshcore stopped in `chat` mode with `repeater_name LHPC-REP` still
set, reticulum stopped on its 868 defaults (`meshtastic + reticulum` is refused on any band pair:
both drive the SPI bus without the daemon's lock, `docs/stacks/reticulum.md`); the G2 at 3 dBm. Deviations: `pycryptodomex` installed
into the managed CLI venv by hand (the manifest's command) and the input line recorded beside the
artifact — a release republishes the artifact that carries both.
