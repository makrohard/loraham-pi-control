# Stack: Meshtastic

Rootless `meshtasticd` driving the RF95 radio **directly over SPI**, on 868 (default) or 433 MHz.
`lhpc` starts and stops it as a user process — no sudo, no systemd. It owns its band exclusively
and cannot run while the daemon serves that band.

| | |
|---|---|
| Components | `meshtastic` (main) · `meshtastic-gps` (feed, admitted by the global GPS plan, not a manual choice) · `meshtastic-cli` (on-demand, `lhpc meshtastic …`) |
| Source / pin | `src/meshtastic-firmware` ← `meshtastic/firmware` `v2.7.26.54e0d8d` (`54e0d8d0…`), a normal managed git source (pinned / stable / dev selectors, Check / Update / Build flows) |
| Run | `build/tools/meshtasticd/meshtasticd -c <runtime>/config/files/meshtasticd.yaml -d <runtime>/state/meshtasticd` |
| Endpoints | TCP API `:4403` · web UI `:9443` (HTTPS; rootless cannot bind 443) — both bind all interfaces with no auth, so the managed firewall denies them by default; the sanctioned remote path is the stack web proxy or an [SSH tunnel](../ssh-tunnel.md) |
| Config | `<runtime>/config/files/meshtasticd.yaml`, regenerated per band from `lhpc/data/bases/meshtasticd.yaml` at every start (per-band LoRa pins, web root, TLS paths, log level) |
| Artifacts | `build/tools/meshtasticd/meshtasticd`, its web UI at `build/tools/meshtasticd/web`, the managed CLI venv `build/tools/meshtastic-cli/.venv` (`meshtastic==2.7.11`) |
| Resources | `loraham.radio.868` + `.433` exclusive · `spi.bus.0.unlocked` exclusive · `tcp.port.4403` + `.9443` exclusive |
| System | `/dev/spidev0.0` (`dtoverlay=spi0-0cs`), `spi` + `gpio` group membership, the packaged root `meshtasticd.service` must be disabled (`sudo systemctl disable --now meshtasticd`) |
| Install channel | **binary** by default (a sha256-verified prebuilt of the binary + web assets, built from the pinned commit); `--source pinned\|dev\|stable` builds natively instead. Policy: [provenance](../provenance.md) |

## Contents

- [Settings](#settings)
- [Native build](#native-build)
- [Position (GPS)](#position-gps)
- [Command line (`lhpc meshtastic`)](#command-line-lhpc-meshtastic)
- [Notes](#notes)
- [Conflicts](#conflicts)

## Settings

| param | default | notes |
|---|---|---|
| `region` | `EU_868` (433: `EU_433`) | LoRa region — required for TX; applied after start (a failed push fails the start) |
| `node_name` / `node_short` | *(empty)* | the node's own names (39 / 4 UTF-8 bytes), never the operator callsign; the start is refused until both are set — node names never inherit ([architecture](../architecture.md#identity-and-callsigns)) |
| `use_gps` | `on` | use the global position source |
| `loglevel`, `max_nodes`, `ble`, `mqtt`, `cs`, `irq`, `reset`, `busy`, `ssl_key`, `ssl_cert`, `web_root` | advanced | YAML keys. `cs`/`irq` default 7/16 (868) and 8/25 (433); `reset`/`busy` are omitted when empty — the Uputronics RF95 boards have neither line, and BCM 6/13 are the daemon's LEDs |

Region, node identity, GPS mode and fixed position are device settings applied through the
managed CLI after start (post-start steps, re-runnable with `lhpc stack poststart meshtastic`).
The web port is fixed at 9443 (the endpoint, proxy upstream and exposure audit derive from it).

## Native build

`meshtasticd` is built from the pinned checkout with upstream's **`native`** PlatformIO
environment — not `native-tft` (the OBS package's), which links X11/libinput/xkbcommon for an
on-device UI a headless box cannot render. Steps: a managed PlatformIO 6.1.19 venv → `pio run -e
native` → the **link gate** (`meshtastic-link-gate.sh`: `readelf -d` + `ldd` must show no SDL, X11,
Wayland, Mesa/GL, LLVM, PulseAudio, ALSA, libinput, xkbcommon or GTK; fail-closed) → the **web
client** (`meshtastic-web-assets.sh`: the meshtastic/web release LHPC pins in the manifest by version
and sha256, verified on every install — currently v2.7.2, the newest release, independent of the
firmware's `bin/web.version`, which is upstream's last-known-good for the ESP32 embedded server and
has stayed at 2.6.7 across the 2.7.x/2.8.0 firmware lines; the pairing is tested on the reference
box and moved with the pin recipe) → the CLI venv. The
completion marker lives in the checkout and is written after the last step, so an updated checkout
reads *Build required* until rebuilt. A native C++ build takes hours on a Pi Zero 2W.

## Position (GPS)

Position comes from the global setting ([GPS](../gps.md)); `use_gps` only opts this node in or out.

- **gpsd** — the `meshtastic-gps` feed presents the stream as a serial device, because
  meshtasticd reads only `GPS: SerialPath:`. Expect ~37 s of `No GNSS Module` warnings while it
  probes for a chip.
- **nmea** — meshtasticd reads the receiver directly and detects the chip (no probe delay); gpsd
  must not also own the device.
- **fixed** — the node's own fixed-position support (`--setlat/--setlon/--setalt`); no feed.
- **off** — `position.gps_mode = NOT_PRESENT` and `--remove-position`, so a stored fixed position
  never keeps beaconing.

## Command line (`lhpc meshtastic`)

`lhpc meshtastic <args>` runs the managed Meshtastic CLI against this box's node — a guarded
passthrough, not a reimplementation (`lhpc meshtastic --help` shows the upstream reference). The
Dashboard lists it as the on-demand component *Meshtastic CLI*; it is never auto-started.

```text
lhpc meshtastic --info · --nodes · --sendtext "hello" · --dest '!12345678' --sendtext "hi" --ack · --listen
```

- Transport selectors (`--host`, `--tcp`/`-t`, `--port`/`--serial`/`-s`, `--ble`/`-b`,
  `--ble-scan`) are refused: the connection is fixed to the local node.
- LHPC-owned local settings are refused with a pointer: region → `lhpc config meshtastic region`;
  owner name/short (incl. `--set-ham`) → `node_name` / `node_short`; GPS mode and fixed position →
  `lhpc gps`. A remote `--dest` is unrestricted.
- `--configure`/`--import-config` and the channel-URL setters (`--seturl`/`--ch-set-url`/
  `--ch-add-url`) run, then LHPC re-asserts and verifies region/name/GPS; if it cannot, the
  command exits non-zero and names `lhpc stack poststart meshtastic`.
- The three `--factory-reset*` flags warn and ask (`--yes` skips); afterwards
  `lhpc stack poststart meshtastic` re-applies the managed settings.
- Node operations need the running stack; `--help`, `--version`, `--support`, `--test` do not.

## Notes

- A freshly reset node cannot be direct-messaged until node info has been exchanged (modern
  firmware rejects a channel-encrypted DM with `NO_CHANNEL`; the default node-info interval is 3 h) —
  `lhpc stack poststart meshtastic` re-applies the identity and triggers an immediate node-info
  broadcast. Broadcasts are unaffected. Evidence: [live tests](../live-test.md).
- The `gpiochip` is not hard-coded in the YAML base: the Pi Zero 2W header is `gpiochip0`; a Pi 5
  puts it on another chip — add a per-pin `gpiochip:` only if your kernel needs it.
- The web TLS certificate is generated into the writable data dir (`state/meshtasticd/ssl`).

## Conflicts

- Claims `loraham.radio.<band>` exclusively: not with the daemon on that band (so not with
  kiss/graywolf/chat/voice/meshcom on 433, meshcore on 868), and not with reticulum on that band.
- `spi.bus.0.unlocked`: `meshtastic + reticulum` is refused outright; `daemon + meshtastic` on
  opposite bands is allowed — the model and the accepted hazard are in
  [architecture](../architecture.md#radios-bands-and-resource-claims).
