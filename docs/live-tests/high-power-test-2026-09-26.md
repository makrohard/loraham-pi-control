# High-power (+20 dBm) opt-in live test — 2026-09-26

The feature: LoRaHAM daemon 1.2.0 `--high-power` and lhpc 0.10.0's per-band switch, live predicate
and start gate, re-run on the integrated 0.10.0 candidate. Every row below was witnessed on the
hardware, and nothing is recorded that was not.

| | |
|---|---|
| Box | **E** = Pi Zero 2 W (`lhpc-e293`), Lite image, Uputronics SX1278 (433, `uputronics-ce0`) + SX1276 (868, `uputronics-ce1`) — bare HopeRF RFM95/98W modules, the datasheet's own +20 dBm case |
| Controller | lhpc 0.10.0 candidate (the high-power commit on `a2c8f55`), run from the source checkout |
| Daemon | 1.2.0 (`1f1baf2`) built **on the box** from the pinned source, RadioLib `187ef24` first; 328 s on the Zero, swap used but no stall (peak swap-out 8.9 MB/s, iowait ≤ 15 %) |
| Evidence | argv from `ps`, `GET STATUS` / `GET STATS` over the CONF socket, the daemon's start logs and RF log, lhpc's own typed output |

## Row 1 — without the flag

Both bands start without `--high-power`; `STATUS` on both reads `HIGHPOWER=0 CHIPFAMILY=SX127x`.

    SET POWER=20            -> ERR INVALID     log: CONFIG rejected: POWER=20 (high-power mode not enabled (start with --high-power))
    SET MODE=FSK POWER=20   -> ERR INVALID     (whole command refused)
    SET POWER=17            -> OK

lhpc refuses before the socket: *"POWER=20 refused: high-power permission is not enabled on the
running daemon (switch it on in the daemon Hardware settings, then restart the daemon)"*, and
`lhpc daemon 433` reads `Chip: SX127x   high-power permission: off`.

## Row 2 — the switch

`lhpc hardware --high-power 433 on`: `lhpc status` raises **RESTART REQUIRED: 'daemon'**, the 433
daemon keeps its PID and `HIGHPOWER=0`, and `lhpc daemon 433` still reads `off` (the running truth).
The console form (`POST /hardware/high-power`) was not driven in this run.

## Row 3 — with the flag on 433 only

    argv 433: ... --cad-rssi -90 --high-power --rflog on     argv 868: ... --cad-rssi -90 --rflog on
    STATUS 433: HIGHPOWER=1 CHIPFAMILY=SX127x               STATUS 868: HIGHPOWER=0 CHIPFAMILY=SX127x

The five-line `WARNING` block appears once in the 433 start log and not in the 868 log.

    433 SET POWER=20 -> OK    log: [sx127x] WARNING POWER=20 applied: +20 dBm, OCP 140 mA; duty cycle <= 1 %, operator responsible
    433 SET POWER=18 -> ERR INVALID      433 SET POWER=19 -> ERR INVALID
    433 SET POWER=17 -> OK               433 SET MODE=FSK POWER=20 -> OK, SET MODE=LORA -> OK
    868 SET POWER=20 -> ERR INVALID      (no permission on that process)

`lhpc daemon 433` reads `high-power permission: ON` and prints the `WARN` line; `lhpc daemon 433
--set POWER=20 --yes` is admitted as sent, not confirmed (the daemon never echoes `POWER`).

## Row 4 — transmit at 20, then at 17

One 24-byte frame through the raw DATA socket at each level, MANAGED TX, default 433 channel:

    POWER=20  TXOK 0 -> 1   TXERR 0    rf log: TX len=24 outcome=ok
    POWER=17  TXOK 1 -> 2   TXERR 0    rf log: TX len=24 outcome=ok

The witness is the box alone: the mode engages and the 20 dBm / 140 mA pairing holds (the chip
completed the transmit without the over-current protection tripping). No outside receiver heard these
frames, so a radiated +3 dB is unwitnessed. Nothing here measures output power, the OCP margin or
thermal behaviour.

## Row 5 — peer RSSI comparison: not run

No receiver that reports raw LoRa frames on 433 was part of this run.

## Row 6 — the start gate

Switch saved **on**, daemon **running without** the permission, kiss's saved daemon params asking for
`POWER=20`:

    [fail] 433: kiss requests POWER=20 but POWER=20 refused: high-power permission is not enabled on the
           running daemon (switch it on in the daemon Hardware settings, then restart the daemon)
           (saved switch on, running HIGHPOWER=0)
    [blocked] loraham-kiss-tnc: daemon not ready — not started

kiss stayed stopped and the daemon kept `HIGHPOWER=0`. The run summary line reads *"Run FAILED for
'kiss': loraham-daemon, loraham-kiss-tnc did not start/verify"* although the daemon was already running
and untouched; the `[fail]` line carries the real reason.

## Row 7 — pending-off, and revocation by restart

    restart with the switch on       -> STATUS HIGHPOWER=1
    lhpc hardware --high-power 433 off
      lhpc daemon 433                -> high-power permission: ON   (the running truth)
      lhpc daemon 433 --set POWER=20 -> refused: "the band's high-power switch is saved off; the running daemon still holds the permission until it is restarted"
    restart                          -> STATUS HIGHPOWER=0, argv without --high-power

## Final state of the box

Switch off on both bands, kiss's saved daemon params reset, no restart-required marker. The box was
then returned to the state it had before the run (another candidate controller, the published daemon
1.1.1 binary, MeshCore running).

## Not proven here

* RF output at +20 dBm, the OCP margin and thermal behaviour — no instruments.
* Anything on an SX1262 or on the LoRaHAM 433 RFM98PW.
* The console form of the switch (row 2 used the CLI form).
* The binary-channel install of daemon 1.2.0 — the release rebuild is where that is proven.
