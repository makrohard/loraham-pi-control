# Full-stack live test, 0.7.0 — 2026-09-17/18

Two boxes, three reference radios, every stack LHPC ships. Run on the 0.7.0 candidate as it moved from
`8f7a7ae` to the release commit; every row below was witnessed on real hardware, and nothing is
recorded that was not. The run found three shipped defects — two of them already in released versions —
and each fix is proven here on the air, not in CI.

## What the run is

| | |
|---|---|
| Boxes | **E** = Pi Zero 2 W, Lite image, Uputronics SX1278 (433) + SX1276 (868) · **B** = Pi 5, Desktop image, LoRaHAM dual-module (SX1278 433 + RFM95 868) |
| Radios | LilyGO T-Deck, MeshCom 4.35p (433) · T-Deck Pro, MeshCore (868) · Station G2, Meshtastic (868) |
| Commits | `8f7a7ae` → `cfec05e` (dev final) → **`cd7e1f3` = v0.7.0** (squash, tree byte-identical to `cfec05e`) |
| Pins moved | daemon `58051e9` → **`e8e748e` (v1.1.1)** · meshcom overlay `579e463` → **`b322a88`** |
| Evidence | RF logs from both boxes per row, daemon `GET STATS` counters, peer-side confirmation, `strace` command census, TLS handshake reads |

Box B's card was removed by the operator partway through the run; every row needing it was completed
first. Box E was reflashed from zero with the release-candidate Lite image for the firstboot rows, and
its PKI restored afterwards.

## The three defects this run found

### MeshCom could not transmit at all — shipped broken in v0.6.2

Daemon 1.0.0 narrowed `POWER` on SX127x to 2–17 dBm. The QEMU firmware had `TX_OUTPUT_POWER 20`
compiled in and reports its compiled power over the external-radio path, so every configure was
refused and the stack never reached the air:

    [CONF433] CONFIG rejected: POWER=20 (invalid LoRa value)
    [bridge] XR client disconnected: config-failed

A runtime `--txpower` cannot help — power is snapshotted once at XR connect. The overlay now builds at
17 dBm, `TX_POWER_MIN` moved to 2 to match the accepted range.

### Chat went deaf after transmitting — long-standing, in the shipped default

With TX and RX on different frequencies (default 433.775 / 433.900) the client retuned to TX, queued
the frame, slept 100 ms and retuned back. A short frame at SF12/BW125 is ~2.6 s of airtime plus an
unbounded listen-before-talk wait, so the retune landed mid-transmit and was lost, leaving the radio
parked on the TX frequency.

### A fix inside a packaged asset went stale silently

A box updated to 0.7.0 kept running a three-day-old MeshCore poller still issuing the destructive
`GET CHANNEL`, while `lhpc status` reported everything current — because the component's own source pin
had not moved.

## The matrix

### Phase A — the channel-scan repair

| # | row | result |
|---|---|---|
| A1–A2 | binary and pinned-source channels install and verify | PASS |
| A3–A5 | `/api/daemon/<band>` passive (`CADSCAN=0 NOTSCANNED`), `/feed` 200, `SET MODE` read-back | PASS |
| A6 | reception while the console endpoint polls at 3 s | **16 of 16 frames** |
| A7–A8 | "Scan now" POST+CSRF; GET refused; command census | **PARTIAL** — server side proven: the route is POST-only at framework level, `POST` without a token → 400, shipped JS can only scan on an explicit click. Browser-driven census not run |
| A9 | MeshCore poller uses NOSCAN on a live box | **FAIL as found, PASS after rebuild** — see below |

**A9, the finding.** The installed poller (`site-packages/meshcore_host/loraham_radio.py`, installed
2026-09-14) still sent plain `GET CHANNEL` while the lhpc source carried the NOSCAN fix. After the
component was rebuilt, a 35-second `strace -e trace=read` census of the 868 daemon counted:

    8 × "GET CHANNEL NOSCAN"     0 × plain "GET CHANNEL"

### Phase B — the clock gate

| # | row | result |
|---|---|---|
| B1 | commissioning under an unverified clock, then normalisation | PASS |
| B1a | `--accept-unverified-clock` rejected by `init` | PASS — argparse "unrecognized arguments" |
| B2 | PKI lock contention returns the typed busy | PASS |
| B3 | **off-grid firstboot on hardware, from zero** | PASS |
| B5 | Chrome GUI mTLS | PASS |

**B1 raw values** (Box B). Before → after normalisation:

    leaf serial   02807530B588B351CE63689C383550C40C738B8E → 5330C0B63F5B953681971888B9F9E1D3A9C4D57D   CHANGED
    leaf pubkey   2f430eb26a6beac2e84b0cab05a09335ce5f34aa22d773270c0396441dad80f3                     SAME
    server-CA     AA:EC:41:2C:35:D5:55:37:…:34:99:9E:17                                                SAME
    client-CA     6F:7C:9E:02:62:EB:8D:98:…:EB:44:01:1C                                                SAME
    CA dates      2025-01-01 → 2049-12-31, unchanged      leaf dates  Sep 17 2026 → Dec 21 2028
    CRL           2049 → Oct 18 2026 (+30 d)              nginx MainPID 83506 unchanged — reloaded, not restarted

**B3, the row v0.6.2's Lite image failed.** A release-candidate Lite image, flashed to a wiped card and
booted with **no uplink**: firstboot completed, `/healthz` answered **200** over the AP while the box
believed it was three days in the past, and the provisional PKI read — taken off the TLS handshake
before logging in, i.e. a client's view rather than the box's own claim:

    serial     265457A3A74AB8C732AA6E3DD7F24BC3FC21CE15
    notBefore  Jan  1 00:00:00 2025 GMT
    notAfter   Dec 31 23:59:59 2049 GMT
    pubkey     e97033d10fcb0a2c310e7445a34216d9e1882991b5243c4024dfbb8f5062dbc4

Ethernet was then plugged in; chrony reached stratum 3 and the marker cleared **~55 s** later:

    serial → 76E5637D2DA083976ADF75832C281769BD999683   CHANGED
    pubkey   e97033d10fcb0a2c310e7445a34216d9e1882991b5243c4024dfbb8f5062dbc4   SAME
    both CA fingerprints SAME · CA dates still 2025→2049 · CRL → Oct 18 2026
    nginx MainPID 1638 unchanged — reloaded, not restarted

Normalisation happens on the **first watchdog pass after `clock_verified()` turns true**; the watchdog
runs at 60 s while a normalisation is owed. `clock_verified()` requires the *kernel* synced flag and
can lag the time daemon's own report, which is why B3 shows ~55 s and B1 ~97 s. Neither deviates.

### Phase C — 433 stacks against real peers

| # | row | result |
|---|---|---|
| C1 | `kiss` TNC up, box TX decoded by T-Beam | PASS |
| C2–C4 | T-Beam beacon → GrayWolf, digipeat, iGate | **peer-limited** — the T-Beam receives but will not beacon (no GPS fix) |
| C5 | GrayWolf beacons a fixed position | PASS |
| C6–C7 | `meshcom` ↔ T-Deck | **PASS — byte-identical** |
| C8–C9 | `chat` box-to-box | PASS |

**C6/C7 — what proves the MeshCom fix.** The bridge log carries both states in one file:

    2026-09-17T23:49:46  configuration failed … rejected command   ← old firmware, POWER=20
    2026-09-18T02:19:16  [loraham] configured; radio ready         ← new artifact, 17 dBm

and the frame the box transmitted is the frame the peer received:

    box   TX len=39 outcome=ok
          hex 3a0700000004444a304348453e2a3a4336206f6e61697220303232333134002788072223a7737e
    Deck  RX len=39 rssi=-41 snr=6
          hex 3A0700000004444A304348453E2A3A4336206F6E61697220303232333134002788072223A7737E

Identical; decodes to `DJ0CHE>*:C6 onair 022314`. Reverse direction: box RX rssi −49 snr 14 from
`DJ0CHE-07`.

**C8/C9 — what proves the chat fix.** Before: with the node parked on its TX frequency, the peer's
transmission produced only `RX read error: -7` (CRC mismatch) and nothing reached the UI. After:

    23:55:11.589Z  FREQ=433.774994      ← retuned OUT to TX
    23:55:21.895Z  FREQ=433.899994      ← retuned BACK to RX, 10.3 s later

The second line did not exist before v1.1.1. Equal-frequency configurations are unaffected: one retune
instead of two, and the send returns in 356 ms against 11 718 ms for the split case.

### Phase D — 868 stacks

| # | row | result |
|---|---|---|
| D1 | `meshcore` advert box-to-box, signature parsed | PASS |
| D2 | `meshcore` advert → T-Deck Pro | PASS — peer's `last_advert` moved 1789636497 → 1789682410, other contacts unchanged |
| D3 | `meshcore` message T-Deck Pro → box, decrypted | PASS — `RX TXT_MSG` then `TX type=ACK`; companion store holds the **plaintext**, snr 11.5 rssi −77 |
| D4 | `meshtastic` ↔ Station G2, both directions | PASS |

D3 first looked like a failure (`No contact found for src hash: 37`) and was not: the box held 211
contacts but not the peer's key. MeshCore learns keys from adverts, so an advert from the peer fixed it.
A genuine decrypt failure looks different: `Decryption failed: Invalid HMAC for all 2 contact(s)`.

### Phase E — direct-SPI and admission

| # | row | result |
|---|---|---|
| E1–E2 | `meshtastic` refused while the daemon holds the band; band reclaimed after stop | PASS |
| E3 | `reticulum` box-to-box, both direct-SPI | PASS — **51 B out / 51 B in**, byte-exact, `LoRaSPIInterface` both ends |
| E4 | `reticulum` refused while the daemon holds the band | PASS — both holders named, `rns` stayed stopped |

E3 was run on 868 rather than 433: both boxes were already configured there, the row's value is the
direct-SPI path rather than the band, and it keeps Box B's 1 W 433 stage out of a row that does not
need it.

### Phase F — regressions and guards

| # | row | result |
|---|---|---|
| F1 | same-frequency rule refuses a second app stack | PASS — refusal names the holder, `rc=1`, nothing started |
| F2 | `[keep] <band> in use` for a second component | PASS |
| F3 | KISS rx==tx sends no retune | PASS on both boxes — **zero** `FREQ=` lines for a transmission that demonstrably happened (`TXOK` 5 → 6) |
| F4 | CRC gate: corrupted frame increments `RXDROPS`, not delivered | PASS — **by real RF, not injection** |

F4 came free from the C9 work: with the node off-frequency, the adjacent channel leaked in and the
daemon logged `RX read error: -7` twice — `RADIOLIB_ERR_CRC_MISMATCH` — without delivering anything.

### Phase G — bench-impossible, stated explicitly

`voice` on both bands: neither box has a capture device (`arecord -l` empty). The stack ensures the
daemon with its own parameters — `TXMODE=DIRECT`, `FREQ=434.700012` — and stops at the interactive
boundary. **Nothing was transmitted and no audio path exists**; PTT, codec and round trip remain
unproven and must not be read into this row.

## Deployment, verified on hardware

The fixes were then proven through the channel operators actually use, not a local build:

* `lhpc update daemon` installed the published binary and the running binary reported **1.1.1**
* `lhpc update chat` moved the source to `e8e748e`, `lhpc build chat` produced a binary containing the
  fix's own strings, and the retune-back was observed
* meshcom installed `--source binary` from the republished artifact, and C6/C7 were run on it

## What is not proven here

* **No soak.** This run is row-based. There is no sustained-load result for 0.7.0; the most recent soak
  belongs to the daemon 1.0.0 gate, not to this release.
* **C2–C4** peer-limited, as above.
* **A7/A8** browser-driven command census not run.
* Two Wi-Fi SDIO wedges and one USB-Ethernet chip wedge were observed on Box E during the run
  (`mmc1: error -22`, `r8152 … Unknown version 0x0000`). The board stayed alive through the Wi-Fi
  faults. These are hardware observations, not LHPC behaviour, and are unexplained.
