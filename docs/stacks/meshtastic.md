# Stack: Meshtastic

Rootless `meshtasticd` driving the RF95 radio directly (band-switchable; default 868).
`lhpc` starts and stops it — no sudo, no systemd. Because it owns the radio, it claims
its band exclusively and **cannot run while the daemon serves that band** (`lhpc` blocks
the conflict).

| | |
|---|---|
| Component | `meshtastic` |
| Run | `meshtasticd -c <runtime>/config/files/meshtasticd.yaml -d <runtime>/state/meshtastic` |
| Config | `meshtasticd.yaml` (per-band LoRa pins, region, web port) |
| Web UI | `:9443` (rootless can't bind 443) |
| API | `:4403` |

The runtime data dir (`-d …`) is writable, so the web TLS cert is generated there.
Region and node name are applied once after start via the device API.

**Install:** the binary channel is the default here (a prebuilt, sha256-verified artifact —
minutes instead of hours); `--source pinned|dev|stable` builds from source instead. See [Binary
channel](../../README.md#binary-channel-prebuilt).

## Server-only build (no display stack)

`meshtasticd` is **built from a pinned upstream checkout**, not installed from the Meshtastic OBS apt
package. The reason is concrete: the OBS package is built from upstream's `native-tft` PlatformIO
environment, which adds `-lX11 -linput -lxkbcommon` for the on-device MUI, and its `Depends` list
`libsdl2-2.0-0` — which is overdeclared (the binary never links SDL) but pulls `libpulse0`,
`libwayland-*`, `mesa-libgallium`, `libllvm19` and `x11-common` with it. On a headless Trixie *lite*
image that was a 99-package / 308 MB desktop cascade for software that can never render.

LHPC builds upstream's **`native`** environment instead — the same source, minus the TFT/MUI path.
Everything the stack actually uses is unaffected: the ulfius web server (9443), the TCP API (4403)
and direct RF95 SPI/GPIO.

- **Source**: a normal managed Git source, so the **pinned / stable / dev** selectors and the
  Check / Update / Build flows all apply when you build here. Pinned is the exact commit behind the
  OBS build it replaced — and the published binary is built from exactly that commit.
- **Artifacts** live under the runtime root: `build/tools/meshtasticd/meshtasticd`, its web UI at
  `build/tools/meshtasticd/web`, and the managed Meshtastic CLI.
- **Web UI follows the source.** The required release is read from the checkout's `bin/web.version`.
  A pinned build additionally verifies the declared SHA-256; dev/stable builds validate and *record*
  the hash instead of asserting one. Assets from a different revision are never reused.
- **Link gate.** After the build, `readelf -d` and `ldd` must show no SDL, X11, Wayland, Mesa, LLVM,
  PulseAudio, libinput or xkbcommon. A binary that links any of them is refused, not published.
- **Rebuild after update.** The strict completion marker lives in the source checkout and is written
  only after every step succeeds, so replacing the checkout (update, or a different selector) makes
  the stack read *Build required* until the new revision is rebuilt.

A native C++ build is slow on a Pi Zero 2W — the platform/library resolve alone can take many
minutes, and the disk swapfile the bootstrap provisions exists for exactly this.
