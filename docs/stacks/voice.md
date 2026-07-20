# Stack: Voice

LoRa voice app (`loraham_voice`) — a GTK GUI; `lhpc` starts and stops it (needs a
display). Band-switchable (433/868); daemon-backed.

| | |
|---|---|
| Component | `loraham-voice` |
| Source | `LoRaHAM_Voice` |
| Build | `gcc … -o loraham_voice` (needs codec2, GTK, ALSA dev libs) |
| Config | `loraham_voice.conf`: callsign + per-band LoRa params keyed `<band>_freq`, `<band>_sf`, `<band>_bw`, `<band>_cr`, `<band>_power`, `<band>_crc`, `<band>_preamble`, `<band>_sync`, `<band>_ldro` |

The app reads its config from the directory of its binary, so `lhpc` symlinks the
binary into the runtime config dir and runs it there.

## Headless systems

The Voice component is a GTK application: it needs `libgtk-3-dev` to build and a graphical session
to run. `bootstrap-deps.sh` does **not** install GUI dependencies by default, so on a headless image
the stack is deliberately unavailable — `lhpc build voice` refuses up front (before running any
build step) and auto-install records the stack as `skipped`, which is an accepted outcome, not a
failure. To make it available on a machine with a display:

```bash
sudo bash bootstrap-deps.sh --spi-mode <mode> --with-gui
```
