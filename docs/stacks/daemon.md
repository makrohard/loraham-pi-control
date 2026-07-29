# Stack: LoRaHAM daemon

The hardware owner for all daemon-backed stacks. One component, one process per band.

| | |
|---|---|
| Component | `loraham-daemon` (radio chosen at run time) |
| Run param | `--radio 433 \| 868`; lhpc launches one `--radio <band>` instance per band, optional `--debug`, per-band `--tx-mode-<band> managed\|direct`, CAD monitor params |
| Build | `loraham_daemon/build.sh` (needs RadioLib + liblgpio) |
| Sockets (per band) | `/tmp/loraconf<band>.sock` (CONF), `/tmp/lora<band>f.sock` (framed), `/tmp/lora<band>.sock` (raw) |
| Locks | `/run/lock/loraham/` |
| Hardware | SPI `/dev/spidev0.0`, GPIO `/dev/gpiochip0` |

**Install:** the binary channel is the default here (a prebuilt, sha256-verified artifact —
minutes instead of hours); `--source pinned|dev|stable` builds from source instead. See [Binary
channel](../../README.md#binary-channel-prebuilt).

## TX modes

`MANAGED` (bounded CAD/LBT, returns `CHANNEL_BUSY` on a busy channel) or `DIRECT`
(immediate TX, no CAD). Selectable per band at boot or live (`lhpc daemon <band>
--set TXMODE=…`). TX is never auto-enabled by `lhpc`.

## Resources

- `spi.bus.0` — cooperative for the daemon's 433/868 instances; a direct-SPI client
  claims it exclusive and conflicts.
- `loraham.daemon-socket.<band>` — provider; consumers are kiss, the MeshCom bridge
  and meshcore.
- `loraham.radio.<band>` — exclusive per band.

## Readiness

Read-only `GET STATUS` on the CONF socket → `STATUS RADIO=READY|FAILED|… TXMODE=…`.
`RADIO=READY` means the radio is up. No side effects, no TX.
