# Stack: Voice

LoRa voice app (`loraham_voice`) in two builds of the same source: a GTK GUI on a
desktop (`lhpc` starts and stops it; needs a display) and an **ncurses terminal
variant** for headless/Lite boxes — no graphical environment involved. Band-switchable
(433/868); daemon-backed.

| | |
|---|---|
| Components | `loraham-voice` (GTK, desktop) · `loraham-voice-cli` (ncurses terminal) |
| Source | `LoRaHAM_Voice` (one pinned checkout, two builds) |
| Build | GTK: `gcc … -o loraham_voice` (codec2, GTK, ALSA) · terminal: `gcc -DNO_GTK … -o loraham_voice_cli` (codec2, ALSA, ncurses — zero GTK/X11 linkage) |
| Config | `loraham_voice.conf` (shared by both): callsign (max 11 characters; inherits the global operator base callsign while empty) + per-band LoRa params keyed `<band>_freq`, `<band>_sf`, `<band>_bw`, `<band>_cr`, `<band>_power`, `<band>_crc`, `<band>_preamble`, `<band>_sync`, `<band>_ldro` |

The app reads its config from the directory of its binary, so `lhpc` symlinks the
binary into the runtime config dir and runs it there. Both variants claim the same
exclusive audio device, so they can never run at once — the terminal variant is refused
while the GTK app holds it.

The terminal variant is a **fallback**, offered only where the GTK app cannot run (no
toolkit, or no display). It reads the shared config the GTK component owns, so starting
it on its own is refused: use `lhpc stack start voice`, which generates that config —
including your callsign — first. With neither a local callsign nor a global one set, the
start is refused rather than run unidentified.

## Headless / Lite systems

The terminal variant makes Voice a first-class headless stack: `libcodec2-dev` and
`libasound2-dev` are lean (no graphical chain) and part of the standard bootstrap, so
`lhpc install voice && lhpc build voice` works on a Lite image out of the box. The GTK
component is *optional* — on a box without the GTK toolchain the build's GUI preflight
simply drops it and never pulls a graphical environment.

Like chat and NomadNet, the terminal UI is **interactive**: it needs a real TTY, so
lhpc never autostarts it — `lhpc stack start voice` brings up the daemon and prints
the exact copy-paste command (the dashboard's Voice card shows the same generated
command), and you run it in a terminal, locally or over SSH. Space is PTT; it talks
to the daemon exactly like the GTK app and shares its config, which you edit on the
Voice stack's Config page and which every start regenerates.

## Desktop systems

Unchanged: with the GUI dependencies installed the GTK app builds and runs as before,
and the terminal variant is not offered — the GTK app owns the audio device there.

```bash
sudo bash bootstrap-deps.sh --spi-mode <mode> --with-gui
```
