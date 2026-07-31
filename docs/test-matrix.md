# Release test matrix — 0.1.7 (reticulum stack)

Three boxes, live hardware, both bands. `rnstatus` interface counters are the RX/TX
evidence; log greps are not (see field-notes).

| box | hardware | role |
|---|---|---|
| `loraham` (Pi 5) | SX1278 433 + RFM95 868 | build box, web console, RF peer |
| `lhpc-zero` (Zero 2 W) | Uputronics CE0 433 + CE1 868 | RF peer, field box (AP + mTLS) |
| `lhpc-zero-wave` (Zero 2 W) | Waveshare SX1262 868 | zero-to-hero install, SX1262 proof |

## Zero-to-hero (fresh Zero 2 W + Waveshare) — 16 min

| phase | time |
|---|---|
| `bootstrap-deps.sh --spi-mode soft-cs` (272 pkgs) | 375 s |
| `install.sh` (clone, venv, console) | 341 s |
| reboot (SPI overlay + groups) | 35 s |
| `lhpc install reticulum` | 69 s |
| `lhpc build reticulum` (nomadnet, lxmd, rns; sideband GUI-skipped) | 158 s |

## Wipe and rebuild (Pi 5)

`lhpc uninstall --yes` removed 16 of 17 sources and REFUSED the one with local changes
(naming `clean` as the escape). After `clean --purge`: install 17 s, build 125 s
(Sideband included — the Pi 5 has the GUI deps), first start verified, RF proven again
from a tree that no longer existed 4 minutes earlier. Config, secrets and PKI survived.

## RF — every direction proven

868: all six directions between SX1276, SX127x and SX1262.

The SX1262 pair was re-proven after the sync-word correction — both directions,
`0x14 0x24` read back from the chip (earlier runs carried `0x11 0x24`, which
interoperated because only the high nibbles carry the value, but was out of spec).
Exact commit set for that run: driver `3fef542`, RNS `b48b96e6`, lhpc = the commit
that pins them (`lhpc/data/manifest.example.toml`, `pin_commit`).
433: both directions between SX1278 and Uputronics 433.

## Coexistence (one SPI bus, `spi0.lock`)

* daemon 433 + Reticulum 868 — both `READY`, interleaved
* daemon 868 (MeshCore) + Reticulum 433 direct-SPI
* MeshCom 433 + Meshtastic 868 (field box, survives reboot)

## Refused as designed

* meshtastic 868 while Reticulum owns 868 — radio + `spi.bus.0.unlocked` conflicts
* igate while kiss holds 433; kiss while Reticulum holds 433
* Reticulum second band while running
* client start when the shared instance is absent (`loraham-rns-client`, exit 3)
* non-loopback bind for 4242 (param and node both refuse)
* source update while a consumer runs; drifted checkout not overwritten
* start with a build receipt that no longer matches its sources

## Granularity, on purpose

`install` adopts a whole STACK; `update` refreshes ONE source. A component id given
to `install` now names its owning stack and both commands, instead of "Unknown stack".

## Boot restore

Field box power-cycled twice: `2 restored, 0 failed`, AP up, console mTLS-gated.
