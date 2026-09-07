# Stack: MeshCom (QEMU)

Unmodified MeshCom firmware running headless under QEMU, bridged to the LoRaHAM daemon on
433 MHz. The daemon runs **MANAGED**: the emulated firmware has no real radio (`EXTERNAL_RADIO`), so
channel access is delegated to the daemon, and the bridge itself sets `TXMODE=MANAGED` on connect.
Start order: daemon → bridge → GPS feed → QEMU.

| | |
|---|---|
| Components | `meshcom-bridge` · `meshcom-gps` (position feed, admitted by the global GPS plan) · `meshcom-gps-relay` (test fixture) · `meshcom-qemu` (main) · `meshcom-firmware` (the PlatformIO image, cloned by ref during the build) |
| Source / pin | `src/meshcom-qemu-raspi` ← `makrohard/meshcom-qemu-raspi` `579e463` (run/build/setup scripts + the QEMU overlay) · `src/meshcom-loraham-bridge` ← `makrohard/meshcom-loraham-bridge` `f018920` · firmware `icssw-org/MeshCom-Firmware` `dev` @ `674413c` (`v4.35p.08.29-34`) |
| Bridge | `build/meshcom-loraham-bridge --bind 127.0.0.1 --port 7000 --backend loraham [--password-file …] --ping-interval-ms 30000 --pong-timeout-ms 90000`; consumes `/tmp/lora433f.sock`; built with cmake (needs `libssl-dev`) |
| QEMU node | `scripts/run.sh --env qemu-headless-extradio-gpsd --qemu <binary>`; web UI `127.0.0.1:18083`, net-console `127.0.0.1:12323`; readiness window 600 s |
| Firmware image | `.work/MeshCom-Firmware/.pio/build/qemu-headless-extradio-gpsd/flash.bin`, completion marker `.lhpc-build-complete` beside it |
| Callsign | `mc_callsign` pushed over the net-console (`--setcall`) after boot: probe `--info` first, skip when already set (it persists in the node's NVS), else re-send on a stepped schedule (~13 min window) until the firmware ACKs. `lhpc stack poststart meshcom` re-runs it |
| Secrets | `<runtime>/config/secrets/xr_pw` (0600) — the HMAC password, first line |
| Resources | `tcp.port.7000` / `.18083` / `.12323` exclusive · `loraham.daemon-socket.433` consumer · `loraham.profile.433` requirement `MANAGED` · `meshcom.uart1.feed` exclusive (one feed on the UART: production or fixture) |
| Install channel | **binary** by default: a sha256-verified artifact overlaying the QEMU binary, the firmware image and the bridge over the pinned clone (the run scripts stay in the checkout). Its firmware is built with an empty password, so the stack runs open auth and HMAC changes are refused until installed from source. `--source pinned\|dev\|stable` builds everything on the box. Policy: [provenance](../provenance.md) |

## Contents

- [Settings](#settings)
- [Build tooling](#build-tooling)
- [Position (GPS)](#position-gps)
- [Notes](#notes)
- [Conflicts](#conflicts)

## Settings

| param | component | default | notes |
|---|---|---|---|
| `mc_callsign` | qemu | inherits the global base callsign while empty | optional numeric suffix `-1`…`-99`, shaped like `N0CALL-99` with your own call; the start is refused without an effective identity |
| `use_gps` | qemu | on | use the global position source |
| `env` | qemu | `qemu-headless-extradio-gpsd` | firmware image to boot; another env needs its own build |
| `qemu` | qemu | the managed in-root binary | advanced: override with a custom `qemu-system-xtensa` |
| `port` / `bind` | bridge | `7000` / `127.0.0.1` | the port must equal the firmware's baked `XR_PORT` |
| `backend` | bridge | `loraham` | `fake` = no RF |
| `password_file` | bridge | blank = open auth | managed only by the HMAC flow, never by generic config |
| `ping_interval` / `pong_timeout` | bridge | 30000 / 90000 ms | XR keepalive sized for QEMU/TCG stalls (the bridge binary's own 15 s / 10 s defaults flap under emulation) |
| `rate` / `loop` | gps-relay | 5 / on | fixture replay only |

RF parameters are not bridge settings — they arrive from the firmware over the XR protocol; the
daemon-side profile (433.175 MHz, SF10, BW125, CR6, CADIDLE 28 ms) lives in [daemon](daemon.md).

**HMAC password** — `lhpc hmac status|enable|disable|renew` (default stack meshcom). The firmware
reaches the bridge with `XR_HOST=10.0.2.2` (QEMU's user-net gateway) and `XR_PORT=7000` baked in
at build, plus `XR_PASSWORD` = the first line of `config/secrets/xr_pw`; the bridge reads the same
file through `--password-file`. `enable`/`renew` mint the secret, rebuild the firmware and restart
the link (minutes); `disable` needs a typed confirmation and downgrades the link to
unauthenticated. The stack page's **Password** section masks the stored password behind a *Show* toggle and a copy button
while HMAC is enabled; it is changed only through those actions, never by editing the file, and it
never appears in a log, marker or result. Policy: [operations](../operations.md).

## Build tooling

`lhpc build meshcom` (source channel) provisions everything inside the runtime root:

- **PlatformIO 6.1.19** in a managed venv `build/tools/platformio/.venv`, passed to the build
  scripts by absolute path (`PIO=`), with `PLATFORMIO_CORE_DIR=build/tools/platformio/core`.
- **qemu-system-xtensa built from source** by `scripts/build-qemu.sh` at the pinned Espressif
  commit `esp-develop-9.0.0-20240606` into `build/tool-cache/qemu-xtensa/…`: a shallow clone (no
  `roms/*` submodules), every display/audio back-end disabled (`--disable-sdl/gtk/vnc/opengl/…`),
  `--enable-gcrypt` (the esp32 machine's RSA device aborts without it; a `libgcrypt-config`
  pkg-config shim covers Trixie), `--disable-werror`, a memory-aware `-j = min(nproc,
  floor(MemTotal_GB))`, then the same link gate as meshtasticd and a smoke launch before the
  `.lhpc-qemu-built` marker. Native to the box. The toolchain and the headless library headers
  are declared requires (`lhpc deps`); the runtime needs `libslirp0`.
- **Firmware**: `scripts/setup.sh --ref 674413c` clones the firmware into `.work/`,
  `apply-overlay.sh` applies the QEMU overlay (fail-closed `git apply --check`),
  `prepare-openeth.sh` resolves the ESP32 platform, `build.sh --env qemu-headless-extradio-gpsd`
  produces `flash.bin`. Per-step build timeout 28800 s.

**Offline / prebuilt QEMU (manual, standalone — not via lhpc).** `scripts/fetch-qemu.sh` fetches
the prebuilt Espressif tarball (it links libSDL2, so it loads only on a box with a display stack).
`LHPC_QEMU_TARBALL` is that script's own variable, read only when you run it yourself; it is not
forwarded through lhpc, so `lhpc build meshcom` (which builds from source) neither reads nor
honors it. The two equivalent standalone forms:

```bash
scripts/fetch-qemu.sh <dest-dir> --from-file /absolute/path/qemu-...tar.xz
LHPC_QEMU_TARBALL=/absolute/path/qemu-...tar.xz scripts/fetch-qemu.sh <dest-dir>
```

The file must exist; it is subject to the same pinned sha256 check.

## Position (GPS)

Position comes from the global setting ([GPS](../gps.md)), served by the `meshcom-gps` feed on the
QEMU node's UART1 socket (lhpc connects as the client; QEMU is the server) — for a local or remote
gpsd, a directly-read receiver, or a fixed position. The feed starts before the node because
MeshCom's GPS init is one-shot at boot. `meshcom-gps-relay` replays a checked-in synthetic NMEA
file: a test facility, never a position source, not part of a normal start
(`lhpc stack start meshcom-gps-relay` runs it deliberately). The bridge and the firmware read no
position.

## Notes

- **What normal looks like.** The emulated node boots in about a minute on a Pi 5 and 6 to 14
  minutes on a Pi Zero 2W ([live tests](../live-test.md)); until then the web UI at `:18083` answers 502 and the callsign stays the placeholder —
  expected, not a failure (`lhpc status` shows "post-start: … NOT applied" while the callsign push
  is outstanding). The QEMU process sits around 50 % CPU at steady state on a Pi.
- Box→peer texts leave on MeshCom's own TX scheduling (tens of seconds); with the default
  `CADRSSI` the 433 channel can read BUSY at a noisy site and queued TX end in `CAD_TIMEOUT` —
  the threshold, not the TX mode, is the lever. Evidence: [live tests](../live-test.md).
- Net-console commands end in CRLF; `::text` sends a message.
- Memory: on a 512 MB Zero 2W run MeshCom **or** Meshtastic, not both, and stop the console
  while the node boots — see [maintenance](../maintenance.md).

## Conflicts

- One app stack per band ([kiss](kiss.md)): not with kiss/graywolf, chat or voice on 433, nor with
  meshtastic or reticulum holding 433.
- `meshcom.uart1.feed`: the production feed and the fixture relay never run together.
