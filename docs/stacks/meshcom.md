# Stack: MeshCom (QEMU + bridge + GPS)

Runs unmodified MeshCom firmware headless under QEMU, bridged to the LoRaHAM daemon
on 433 MHz. The daemon must be in **DIRECT** mode (MeshCom does its own CSMA).

**Boot time:** the emulated node needs **~1 min on a Pi 5 and ~5–6 min on a Pi Zero 2W** after
`stack start` before it is usable — the web UI answers 502 and the callsign stays a placeholder
until the firmware finishes booting. Expected, not a failure.

Start order: daemon → bridge → GPS relay → QEMU.

**Install:** the binary channel is the default here (a prebuilt, sha256-verified artifact —
minutes instead of hours); `--source pinned|dev|stable` builds from source instead. See [Binary
channel](../../README.md#binary-channel-prebuilt).


| | |
|---|---|
| Components | `meshcom-bridge`, `meshcom-gps-relay`, `meshcom-qemu` |
| Bridge | `meshcom-loraham-bridge --bind 127.0.0.1 --port 7000 --backend loraham`; consumes `/tmp/lora433f.sock`, requires `loraham.profile.433 = DIRECT` |
| QEMU | `scripts/run.sh --env qemu-headless-extradio-gpsd`; web UI `:18083`, net-console `:12323` |
| Callsign | node CALL set over the net-console (`--setcall`) after boot, re-sent until the firmware accepts it; an empty/`N0CALL` value sends nothing |
| Firmware build | `scripts/build.sh` with `XR_HOST=10.0.2.2 XR_PORT=7000 XR_PASSWORD=$(cat <runtime>/config/secrets/xr_pw)` baked in → `flash.bin` |
| GPS relay | `scripts/gps-relay.py` — starts before the node (one-shot GPS init) |

The firmware connects to the bridge over TCP (external-radio); `XR_HOST`/`XR_PORT`
point at the bridge, `XR_PASSWORD` (optional HMAC) comes from `<runtime>/config/secrets/xr_pw`. The HMAC
password and any real GPS coordinates are secrets — git-ignored, never committed.

## GPS

Production position comes from the global setting (`lhpc gps`, see [GPS](../gps.md)), fed to
the QEMU node's UART by lhpc. The pinned `gps-relay.py` is **not** used for this: it accepts
only a loopback gpsd, so it cannot serve a remote gpsd, a directly-read receiver, or a fixed
position.

`MeshCom GPS relay (fixture)` replays a checked-in **synthetic** file. It is a test facility,
never a position source, and it is not part of a normal start. Run it explicitly if you want
it: `lhpc stack start meshcom-gps-relay`.

Position reaches the node through lhpc's feed, which connects to QEMU's own UART socket as a
client (QEMU is the server). Verified on hardware with a live gpsd, a gpsd on another machine,
and a fixed position.

**Give the emulator time.** MeshCom's GPS init is one-shot at boot, and the node needs roughly
1 minute on a Pi 5 but up to ~8 on a Zero 2 W before `--pos` reports a fix. A node that shows no
position immediately after start is usually still booting.
