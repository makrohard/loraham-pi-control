# Stack: MeshCom (QEMU + bridge + GPS)

Runs unmodified MeshCom firmware headless under QEMU, bridged to the LoRaHAM daemon
on 433 MHz. The daemon must be in **DIRECT** mode (MeshCom does its own CSMA).

Start order: daemon → bridge → GPS relay → QEMU.

| | |
|---|---|
| Components | `meshcom-bridge`, `meshcom-gps-relay`, `meshcom-qemu` |
| Bridge | `meshcom-loraham-bridge --bind 127.0.0.1 --port 7000 --backend loraham`; consumes `/tmp/lora433f.sock`, requires `loraham.profile.433 = DIRECT` |
| QEMU | `scripts/run.sh --env qemu-headless-extradio-gpsd`; web UI `:18083`, net-console `:12323` |
| Callsign | node CALL set over the net-console (`--setcall`) after boot, re-sent until the firmware accepts it; an empty/`N0CALL` value sends nothing |
| Firmware build | `scripts/build.sh` with `XR_HOST=10.0.2.2 XR_PORT=7000 XR_PASSWORD=$(cat /tmp/xr_pw)` baked in → `flash.bin` |
| GPS relay | `scripts/gps-relay.py` — starts before the node (one-shot GPS init) |

The firmware connects to the bridge over TCP (external-radio); `XR_HOST`/`XR_PORT`
point at the bridge, `XR_PASSWORD` (optional HMAC) comes from `/tmp/xr_pw`. The HMAC
password and any real GPS coordinates are secrets — git-ignored, never committed.
