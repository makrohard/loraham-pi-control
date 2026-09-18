# High-power (+20 dBm) opt-in live test — 2026-09-18

The feature: LoRaHAM daemon 1.2.0 `--high-power` and lhpc 0.8.0's per-band switch, live predicate
and start gate. Run on the bare-module proof case the plan names; every row below was witnessed on
the hardware, and nothing is recorded that was not.

| | |
|---|---|
| Box | **E** = Pi Zero 2 W, Lite image (0.7.0), Uputronics SX1278 (433, `uputronics-ce0`) + SX1276 (868, `uputronics-ce1`) — bare HopeRF RFM95/98W modules, the datasheet's own +20 dBm case |
| Controller | lhpc `feature/high-power` (0.8.0), run from the source checkout; console reached over its unix socket |
| Daemon | 1.2.0 built **on the box** from the pinned source `b0a211e` (RadioLib `187ef24` built first); 4 minutes on the Zero |
| Evidence | argv from `ps`, `GET STATUS` / `GET STATS` over the CONF socket, the daemon start logs, the RF logs, the rendered console pages |

## Row 1 — without the flag (the 1.0.0 contract, now with a reason)

Both bands start without `--high-power`; `STATUS` on both ends in `HIGHPOWER=0 CHIPFAMILY=SX127x`.

    SET POWER=20            -> ERR INVALID     log: CONFIG rejected: POWER=20 (high-power mode not enabled (start with --high-power))
    SET MODE=FSK POWER=20   -> ERR INVALID     MODE stayed LORA (whole command refused, no mode switch)
    SET POWER=17            -> OK

lhpc refuses before the socket: *"POWER=20 refused: high-power permission is not enabled on the
running daemon (switch it on in the daemon Hardware settings, then restart the daemon)"*, and
`lhpc daemon 433` reads `Chip: SX127x   high-power permission: off`.

## Row 2 — the switch

Saved through the real console form (`POST /hardware/high-power`, CSRF): the page then shows the
saved-on / running-off mismatch line, `lhpc status` raises **RESTART REQUIRED: 'daemon'**, nothing
restarted. **Finding during this row:** `lhpc config daemon <param>` exposes none of the daemon's
start options — `tx_433` included, which `docs/stacks/daemon.md` claims — so the CLI form of the
switch was added as `lhpc hardware --high-power <band> on|off` and used for every later row.

## Row 3 — with the flag on 433 only

    argv 433: ... --cad-rssi -90 --high-power          argv 868: ... --cad-rssi -90
    STATUS 433: HIGHPOWER=1 CHIPFAMILY=SX127x         STATUS 868: HIGHPOWER=0 CHIPFAMILY=SX127x

The five-line `WARNING` block appears **once** in the 433 start log and **not** in the 868 log.

    433 SET POWER=20 -> OK    log: [sx127x] WARNING POWER=20 applied: +20 dBm, OCP 140 mA; duty cycle <= 1 %, operator responsible
    433 SET POWER=18 -> ERR INVALID   (invalid LoRa value)      433 SET POWER=19 -> ERR INVALID
    433 SET POWER=17 -> OK            433 SET MODE=FSK POWER=20 -> OK (MODE=FSK), SET MODE=LORA -> OK
    868 SET POWER=20 -> ERR INVALID   (no permission on that process)

Console: `rd-hp-433` on the dashboard card, `hp-warn-433` on the Hardware section, `dp-hp-daemon`
on the daemon-params panel — and none of the three for 868. `lhpc daemon 433` prints the `WARN`
line. `lhpc daemon 433 --set POWER=20 --yes` is admitted as **SENT (unconfirmed)** — the daemon
never echoes `POWER`.

## Row 4 — transmit at 20, then at 17

One 24-byte frame through the raw DATA socket at each level, MANAGED TX, default 433 channel:

    POWER=20  TXOK 0 -> 1   TXERR 0    rf log: TX len=24 outcome=ok
    POWER=17  TXOK 1 -> 2   TXERR 0    rf log: TX len=24 outcome=ok

**What this row proves, and what it does not.** The witness is the box alone: `TXOK` counted and
`TXERR` stayed 0, so the mode engaged and the 20 dBm / 140 mA pairing held — the chip completed the
transmit without the over-current protection tripping. By this project's own evidence rule a `TXOK`
count is not proof of a radiated transmission; no outside receiver heard these frames (row 5). So:
**the mode engages and the pairing holds — proven; a radiated +3 dB — unwitnessed, open.** Nothing
here measures the output power, the OCP margin or thermal behaviour — no meter, no analyser. Whether
a receiver witness (a second box listening on 433, or an SDR) comes before or after the tag is the
maintainer's call.

## Row 5 — peer RSSI comparison: NOT ACHIEVED

Twelve 13-byte frames alternating 17 / 20 dBm, 20 s apart, at the T-Deck's own LoRa parameters
(433.175 MHz, SF11, BW250, CR4/6, sync 0x2B, `TXMODE=DIRECT`), all `outcome=ok` (`TXOK=14
TXERR=0` for the day). The MeshCom 4.35p firmware on the T-Deck printed **no RX line at all** with
`--debug on`: it reports MeshCom-framed packets, not raw LoRa frames. So there is no relative RF
evidence in this run; the row stays open for a receiver that reports raw frames. Airtime during the
attempt stayed far below the 1 % duty contract (≈ 0.15 s per frame every 20 s).

## Row 6 — the start gate

With the switch saved **on** and the daemon **running without** the permission, a stack whose
profile asks for 20 does not start:

    [fail] 433: kiss requests POWER=20 but POWER=20 refused: high-power permission is not enabled on the
           running daemon (switch it on in the daemon Hardware settings, then restart the daemon)
           (saved switch on, running HIGHPOWER=0)
    [blocked] loraham-kiss-tnc: daemon not ready — not started

`kiss` stayed stopped; the daemon was untouched (`HIGHPOWER=0`, no RF setter sent). Two neighbouring
behaviours seen on the way, both by design: with the switch saved **off**, a stored `POWER=20` is
filtered out of the profile and the stack starts at its default; and a stack start that has to
launch the daemon itself under a saved-on switch brings it up **with** the flag, so 20 applies.

## Row 7 — pending-off, and revocation by restart

    restart with the switch on  -> STATUS HIGHPOWER=1
    lhpc hardware --high-power 433 off
      lhpc daemon 433          -> high-power permission: ON        (the running truth)
      console                  -> hp-warn-433 AND hp-mismatch-433: "saved off, but the running daemon still holds the +20 dBm permission"
      lhpc daemon 433 --set POWER=20 -> refused: "the band's high-power switch is saved off; the running daemon still holds the permission until it is restarted"
    restart                     -> STATUS HIGHPOWER=0, argv without --high-power

## Final state of the box

Controller 0.8.0 (`27e5a7a`), daemon 1.2.0 from source, both bands running **without** the flag,
switch off on both bands, the kiss profile reset, no restart-required marker. Nothing else changed.

## Not proven here, stated plainly

* RF output at +20 dBm, the OCP margin, and thermal behaviour — no instruments.
* Anything on an SX1262 (none on this box) or on the LoRaHAM 433 RFM98PW (excluded by the plan;
  unvalidated).
* The binary-channel install of daemon 1.2.0 — the release rebuild is where that is proven.

## Pre-existing observations, outside this feature

* `docs/stacks/daemon.md` says `lhpc config daemon tx_433 …` sets the per-band TX mode; the CLI
  answers *"'daemon' has no configurable parameters"*. Documentation defect.
* `lhpc stack stop kiss` also stopped the daemon that had been started separately beforehand
  (`[Daemon] stop requested` 17:53:12Z right after the kiss stop). Lifecycle scope worth a look.
* After a preflight refusal the dependent's own line reads `daemon not ready — not started`; the
  `[fail]` line above it carries the real reason.
