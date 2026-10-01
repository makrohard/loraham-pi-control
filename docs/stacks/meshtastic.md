# Stack: Meshtastic

Rootless `meshtasticd` driving the RF95 radio **directly over SPI**, on 868 (default) or 433 MHz.
`lhpc` starts and stops it as a user process — no sudo, no systemd.

| | |
|---|---|
| Components | `meshtastic` (main) · `meshtastic-gps` (feed, admitted by the global GPS plan) · `meshtastic-cli` (on-demand, `lhpc meshtastic …`) |
| Source / pin | `src/meshtastic-firmware` ← `meshtastic/firmware`, a managed git source |
| Run | `build/tools/meshtasticd/meshtasticd -c <runtime>/config/files/meshtasticd.yaml -d <runtime>/state/meshtasticd` |
| Endpoints | TCP API `:4403` · web UI `:9443` (HTTPS; rootless cannot bind 443) — both bind all interfaces with no auth; containment: [firewall](../firewall.md#what-actually-listens) |
| Config | `<runtime>/config/files/meshtasticd.yaml`, regenerated per band from `lhpc/data/bases/meshtasticd.yaml` at every start (per-band LoRa pins, web root, TLS paths, log level) |
| Artifacts | `build/tools/meshtasticd/meshtasticd`, its web UI at `build/tools/meshtasticd/web`, the managed CLI venv `build/tools/meshtastic-cli/.venv` (`meshtastic==2.7.11`, `pycryptodomex==3.23.0` for the RF-log decoder) |
| Resources | `loraham.radio.868` + `.433` exclusive · `spi.bus.0.unlocked` exclusive · `tcp.port.4403` + `.9443` exclusive |
| System | `/dev/spidev0.0` (`dtoverlay=spi0-0cs`), `spi` + `gpio` group membership, the packaged root `meshtasticd.service` must be disabled (`sudo systemctl disable --now meshtasticd`) |
| Install channel | **binary** by default (binary + web assets); `--source pinned\|dev\|stable` builds natively. Policy: [provenance](../provenance.md#the-binary-channel) |

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
| `node_name` / `node_short` | *(empty)* | the node's own names (39 / 4 UTF-8 bytes), never the operator callsign; the start is refused until both are set ([architecture](../architecture.md#identity-and-callsigns)) |
| `max_power` | `17` | TX power cap in dBm (1–20), written to the YAML (`Lora.RF95_MAX_POWER`), so it holds from the first frame; the node's own `lora.tx_power` never exceeds it. 20 dBm: `lhpc config meshtastic max_power 20`, then restart |
| `use_gps` | `on` | use the global position source |
| `rf_log` | `on` | `logs/rf-meshtastic.log` — meshtasticd's own per-packet JSON trace (`Logging.TraceFile`). Decrypt ([maintenance](../maintenance.md#rf-logs)) reads the peers' keys from `prefs/nodes.proto`, which meshtasticd saves with a delay (a key learned within a minute of the last save waits for the next), so a direct message can show `no-key` while `lhpc meshtastic --nodes` already lists the key |
| `loglevel`, `max_nodes`, `ble`, `mqtt`, `cs`, `irq`, `reset`, `busy`, `ssl_key`, `ssl_cert`, `web_root` | advanced | YAML keys. `cs`/`irq` default 7/16 (868) and 8/25 (433); `reset`/`busy` are omitted when empty — the Uputronics RF95 boards have neither line, and BCM 6/13 are the daemon's LEDs |

Region, node identity, GPS mode and fixed position are device settings applied through the
managed CLI after start (post-start steps, re-runnable with `lhpc stack poststart meshtastic`).
The web port is fixed at 9443.

A hand edit of the generated YAML is lost at the next start; change a setting with
`lhpc config meshtastic <param> <value>` or the stack's Settings (effective at the next start).

## Native build

`meshtasticd` is built with upstream's **`native`** PlatformIO environment — not `native-tft`
(the OBS package's), which links X11/libinput/xkbcommon for an on-device UI. Steps: a managed PlatformIO 6.1.19 venv → `pio run -e
native` → the **link gate** (`meshtastic-link-gate.sh`: `readelf -d` + `ldd` must show no SDL, X11,
Wayland, Mesa/GL, LLVM, PulseAudio, ALSA, libinput, xkbcommon or GTK; fail-closed) → the **web
client** (`meshtastic-web-assets.sh`: the meshtastic/web release LHPC pins by version and sha256,
verified on every install — v2.7.2, independent of the firmware's `bin/web.version`, which is
upstream's ESP32 last-known-good) → the CLI venv. The completion marker lives in the checkout and
is written after the last step, so an updated checkout reads *Build required* until rebuilt. A
native build takes about 2¾ h on a Pi Zero 2W.

The web-client and CLI versions are recorded beside the marker (`build_inputs`), so moving either
also reads *Build required*; a binary-channel box is pointed at
`lhpc install meshtastic --source binary --yes`. Moving these pins:
[maintenance](../maintenance.md#moving-a-pin).

## Position (GPS)

Position comes from the global setting ([GPS](../gps.md)); `use_gps` only opts this node in or out.

- **gpsd** — the `meshtastic-gps` feed presents the stream as a serial device, because
  meshtasticd reads only `GPS: SerialPath:`. Expect ~37 s of `No GNSS Module` warnings while it
  probes for a chip.
- **nmea** — meshtasticd reads the receiver directly (no probe delay); gpsd must not also own it.
- **fixed** — the node's own fixed-position support (`--setlat/--setlon/--setalt`); no feed.
- **off** — `position.gps_mode = NOT_PRESENT` and `--remove-position`, so a stored fixed position
  never keeps beaconing.

## Command line (`lhpc meshtastic`)

`lhpc meshtastic <args>` runs the managed Meshtastic CLI against this box's node — a guarded
passthrough (`lhpc meshtastic --help` shows the upstream reference). The Dashboard lists it as the
on-demand component *Meshtastic CLI*; it is never auto-started.

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

- A freshly reset node cannot be direct-messaged until node info has been exchanged (the
  firmware rejects a channel-encrypted DM with `NO_CHANNEL`; broadcasts are unaffected). Every
  start's post-start step re-applies the owner, and the firmware may log a node-info then; its boot
  node-info check follows about 30 s after start. Both pass the firmware's node-info throttle
  (10 minutes by default), so "Skip send NodeInfo since we sent it <600s ago" means one was
  generated, not that it went over the air — a logged node-info is not proof of a transmission.
  (That line and "Started Tx"/"Completed sending" are DEBUG: `lhpc config meshtastic loglevel debug`.)
  On a fresh node the node-info logged at start did not reach the peer in our tests: a following
  start step cut its transmission, the throttle then applied, and the node's later answer was
  dropped by a peer still holding the old key (next note). Record:
  [live test](../live-tests/live-test.md#notes-from-the-run).
- A new image, `lhpc clean meshtastic --purge` or a deleted `state/meshtasticd` gives the node a
  new key but the same node number (from the Pi's Bluetooth MAC; the YAML sets no `MACAddress`).
  A peer that stored the old key drops node-info carrying the new one, so direct messages fail both
  ways (`PKI_UNKNOWN_PUBKEY`, `NO_CHANNEL`) while broadcasts on a shared channel work. On the peer,
  remove the node (`meshtastic --remove-node '!<node id>'`); it learns the new key from the next
  node-info.
- The `gpiochip` is not hard-coded in the YAML base: the Pi Zero 2W header is `gpiochip0`; a Pi 5
  puts it on another chip — add a per-pin `gpiochip:` only if your kernel needs it.
- The web TLS certificate is generated into the writable data dir (`state/meshtasticd/ssl`).

## Conflicts

- Claims `loraham.radio.<band>` exclusively: not with the daemon on that band (so not with
  kiss/graywolf/chat/voice/meshcom on 433, meshcore on 868), and not with reticulum on that band.
- `spi.bus.0.unlocked`: `meshtastic + reticulum` is refused on any band pair
  ([architecture](../architecture.md#radios-bands-and-resource-claims)).
