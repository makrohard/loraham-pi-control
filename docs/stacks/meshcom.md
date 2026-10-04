# Stack: MeshCom (QEMU)

Unmodified MeshCom firmware running headless under QEMU, bridged to the LoRaHAM daemon on
433 MHz. The firmware has no real radio (`EXTERNAL_RADIO`); channel access is the daemon's, in
**MANAGED** mode (the bridge also sets `TXMODE=MANAGED` on connect). Start order: daemon → bridge
→ GPS feed → QEMU.

| | |
|---|---|
| Components | `meshcom-bridge` · `meshcom-gps` (position feed, admitted by the global GPS plan) · `meshcom-gps-relay` (test fixture) · `meshcom-qemu` (main) · `meshcom-firmware` (the PlatformIO image, cloned by ref during the build) |
| Source / pin | `src/meshcom-qemu-raspi` ← `makrohard/meshcom-qemu-raspi` (run/build/setup scripts + the QEMU overlay) · `src/meshcom-loraham-bridge` ← `makrohard/meshcom-loraham-bridge` · firmware `icssw-org/MeshCom-Firmware` branch `dev`, pinned by commit |
| Bridge | `build/meshcom-loraham-bridge --bind 127.0.0.1 --port 7000 --backend loraham [--password-file …] --ping-interval-ms 30000 --pong-timeout-ms 90000 --rflog on --rflog-path <runtime>/logs/rf-meshcom.log`; consumes `/tmp/lora433f.sock`; built with cmake (needs `libssl-dev`) |
| QEMU node | `scripts/run.sh --env qemu-headless-extradio-gpsd --qemu <binary> --node-image <runtime>/state/meshcom/node-flash.bin`; web UI `127.0.0.1:18083`, net-console `127.0.0.1:12323`; readiness window 600 s |
| Firmware image | `.work/MeshCom-Firmware/.pio/build/qemu-headless-extradio-gpsd/flash.bin`, completion marker `.lhpc-build-complete` beside it |
| Callsign | `mc_callsign` is pushed over the net-console (`--setcall`) after boot, unless a `--info` probe shows the node already has it (it persists in the node's NVS); an empty or cut reply counts as not ready. Re-sent on a stepped schedule (~13 min) until the firmware ACKs; a call never confirmed shows `UNVERIFIED` in `lhpc status` and does not fail the start. `lhpc stack poststart meshcom` re-runs it |
| Secrets | `<runtime>/config/secrets/xr_pw` (0600) — the HMAC password, first line |
| Resources | `tcp.port.7000` / `.18083` / `.12323` exclusive · `loraham.daemon-socket.433` consumer · `loraham.profile.433` requirement `MANAGED` · `meshcom.uart1.feed` exclusive (one feed on the UART: production or fixture) |
| Install channel | **binary** by default ([provenance](../provenance.md#the-binary-channel)): the QEMU binary, firmware image and bridge overlaid on the pinned clone, which keeps the run scripts. Its firmware has an empty password: open auth, HMAC changes refused until installed from source. `--source pinned\|dev\|stable` builds everything on the box (about 2 h on a Pi Zero 2W). After an LHPC update that moves MeshCom's pins it does not start until updated (the node keeps its ID): `lhpc stack stop meshcom --yes`, `lhpc update meshcom --yes`, `lhpc stack start meshcom --yes` |

## Contents

- [Settings](#settings)
- [Build tooling](#build-tooling)
- [Position (GPS)](#position-gps)
- [Notes](#notes)
- [Conflicts](#conflicts)

## Settings

| param | component | default | notes |
|---|---|---|---|
| `mc_callsign` | qemu | inherits the global base callsign while empty | optional numeric suffix `-1`…`-99`, shaped like `N0CALL-99` with your own call |
| `use_gps` | qemu | on | use the global position source |
| `env` | qemu | `qemu-headless-extradio-gpsd` | firmware image to boot; another env needs its own build |
| `qemu` | qemu | the managed in-root binary | advanced: a custom `qemu-system-xtensa` |
| `port` / `bind` | bridge | `7000` / `127.0.0.1` | the port must equal the firmware's baked `XR_PORT` |
| `backend` | bridge | `loraham` | `fake` = no RF |
| `password_file` | bridge | blank = open auth | managed only by the HMAC flow, never by generic config |
| `ping_interval` / `pong_timeout` | bridge | 30000 / 90000 ms | XR keepalive sized for QEMU/TCG stalls (the bridge's own 15 s / 10 s defaults flap under emulation) |
| `rf_log` | bridge | on | RF log `logs/rf-meshcom.log`: raw MeshCom frames received (RSSI/SNR) or sent — TX on the daemon's `TX_RESULT` (`ok`; `unconfirmed` when the bridge faulted with the frame already handed over), never on submit; no decoding. Switch semantics: [maintenance](../maintenance.md#rf-logs) |
| `rate` / `loop` | gps-relay | 5 / on | fixture replay only |

RF parameters come from the firmware over the XR protocol; the daemon-side profile is in
[daemon](daemon.md#radio-parameters).

**HMAC password** — `lhpc hmac status|enable|disable|renew` ([cli](../cli.md#hmac)). The firmware
has `XR_HOST=10.0.2.2` (QEMU's user-net gateway), `XR_PORT=7000` and `XR_PASSWORD` (first line of
`config/secrets/xr_pw`) baked in at build; the bridge reads the same file via `--password-file`.
`enable`/`renew` mint the secret, rebuild the firmware and restart the link (minutes). While HMAC
is enabled the stack page's **Password** section shows it; change it only through those actions,
never by editing the file ([operations](../operations.md#secrets-and-passwords)).

## Build tooling

`lhpc build meshcom` (source channel) provisions everything inside the runtime root:

- **PlatformIO 6.1.19** in a managed venv `build/tools/platformio/.venv`, passed to the build
  scripts by absolute path (`PIO=`), with `PLATFORMIO_CORE_DIR=build/tools/platformio/core`.
- **qemu-system-xtensa from source** (`scripts/build-qemu.sh`) at the pinned Espressif tag
  `esp-develop-9.2.2-20260417` plus `patches/qemu/` of meshcom-qemu-raspi (the flash-cache fix of
  espressif/qemu#183) into `build/tool-cache/qemu-xtensa/…`: shallow clone without `roms/*`,
  headless (no display/audio back-ends), `--enable-gcrypt`, memory-aware `-j`
  ([maintenance](../maintenance.md#running-on-a-pi)), then the meshtasticd link gate and a smoke
  launch before the `.lhpc-qemu-built` marker. Build requires: `lhpc deps`; runtime: `libslirp0`.
- **Firmware**: `scripts/setup.sh --src <the meshcom-firmware remote> --ref {pin:src/MeshCom-Firmware}`
  fetches the firmware at the `meshcom-firmware` pin into `.work/` (always the `{pin:…}` token,
  never a literal commit); `apply-overlay.sh`
  applies the QEMU overlay (fail-closed `git apply --check`), `prepare-openeth.sh` resolves the
  ESP32 platform, `build.sh --env qemu-headless-extradio-gpsd` produces `flash.bin`.

## Position (GPS)

The global position ([GPS](../gps.md)) is served by the `meshcom-gps` feed on the QEMU node's
UART1 socket (lhpc is the client, QEMU the server). The feed starts before the node because
MeshCom's GPS init is one-shot at boot. `meshcom-gps-relay` replays a synthetic NMEA fixture — a
test facility, never started by a normal start (`lhpc stack start meshcom-gps-relay` runs it).

## Notes

- **What normal looks like.** The emulated node boots in about a minute on a Pi 5 and 6 to 14
  minutes on a Pi Zero 2W; until then the web UI at `:18083` answers 502 and the callsign stays
  the placeholder, and the node reads *booting* in the console while the callsign push is still
  running. The QEMU process sits around 50 % CPU at steady state on a Pi.
- Box→peer texts leave on MeshCom's own TX scheduling (tens of seconds). At a noisy site the 433
  channel can read BUSY against the default `CADRSSI` and queued TX end in `CAD_TIMEOUT` — the
  threshold, not the TX mode, is the lever.
- Net-console commands end in CRLF; `::text` sends a message.
- Memory on a 512 MB Zero 2W: [maintenance](../maintenance.md#running-on-a-pi).

## Conflicts

- One app stack per band ([kiss](kiss.md#notes)): not with kiss/graywolf, chat or voice on 433, nor
  with meshtastic or reticulum holding 433.
- `meshcom.uart1.feed`: the production feed and the fixture relay never run together.
