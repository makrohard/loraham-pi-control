# Release matrix, 0.10.0 — box D (`lhpc-0ae1`, Pi 5) and box E (`lhpc-e293`), 2026-09-26

Run on `candidate/0.10.0` (`363e15c` → `95c020b`; the later commits change no stack the rows had
already proved, and the rows they touch were re-run). Box **D** = Raspberry Pi 5, 4 GB, Desktop
image, LoRaHAM dual board (433 + 868), no GPS receiver attached (`use_gps off` on the GPS-capable
stacks), operator callsign `DJ0CHE`, MeshCom `DJ0CHE-15`. Box **E** = Pi Zero 2 W, Lite image,
upgraded from the released 0.9.2 the way a user reaches a new version (section E below).

Evidence rule: the controller's own typed outcome plus the stack's own state. Log greps are not
evidence.

## Box D — rows

| # | stack | channel | build | start | evidence |
|---|---|---|---|---|---|
| 1 | `daemon` | binary | *refused* (binary) | both bands | 433 + 868 `RADIO=READY`; `lhpc daemon 433` answers |
| 2 | `chat` | pinned | 1 s | interactive | the printed command ran as printed (the chat UI drew, TX 433.775 / RX 433.900) and exited with rc 0 |
| 3 | `voice` | pinned | 2 s | — | on the Desktop image the GTK voice starts verified and the terminal variant is skipped with its reason |
| 4 | `kiss` | pinned | 3 s | 1 s | verified; TCP `127.0.0.1:8001` open |
| 5 | `graywolf` | release | 1 s | 2 s | verified; web UI `:8080` → 200; depends on kiss running on 433 |
| 6 | `reticulum` | pinned | — | — | LoRa interface `Up`, `Mode: Internal`; ready marker; MeshChat `:8790` → 200; config `0400`; `rns=1.5.4` in rns, nomadnet, lxmd, Sideband and MeshChat, and `reticulum: lxmf differs — 1.1.0 (lxmd), 1.1.1 (nomadnet, sideband, meshchat)` |
| 7 | `meshcore` | pinned | 86 s | 2 s | chat+repeater (`repeater_name` set first); node verified on 5000 and 8000; web UI `:8788` → 200, dashboard `:8000` → 200; one plugin manager |
| 8 | `meshtastic` | binary | *refused* (binary) | 25 s | verified; `lhpc meshtastic --info` → `LHPC Pi5`, firmware 2.7.26 |
| 9 | `meshtastic` | pinned (from source) | 732 s | 15 s | as row 8 |
| 10 | `daemon` | pinned (from source) | 39 s | 4 s | as row 1 |
| 11 | `meshcom` | binary | — | — | *refused* by the pins gate until the release's meshcom binary is published: "the published binary was built from different commits than this lhpc pins"; the unpublished release artifact, installed through the same code with only its download served from a local file, booted (`:18083` → 200 after 15 s, callsign confirmed on the first attempt) |
| 12 | `meshcom` | pinned (from source) | 696 s | 22 s | QEMU 9.2.2 with the flash-cache patch (recorded in the build marker) and its licence texts; firmware from `makrohard/MeshCom-Firmware` `lhpc-speed`; `:18083` → 200 after 15 s; callsign confirmed on the first attempt |

Memory: at least 2.6 GB free (free + cache) during every heavy build.

## Box D — cross-cutting checks

| check | result |
|---|---|
| **web console** | `/` and `/stacks` → 200; every `/stacks/<stack>` → 302 to its row and its body → 200; 0 tracebacks |
| **pins vs binaries** | every binary component's `built_from` equals its pin |
| **`dev` selector spot-check (kiss)** | `install --source dev` resolved the branch tip (`mutable-dev`); built and started; reinstalled on the default channel: `pinned-verified`, `match` |
| **known-working** | the console row offers to record the composition after a green start; `lhpc known-working kiss` recorded it |
| **boot restore** | three reboots with MeshCore (chat+repeater) and kiss running: each `done — 2 restored, 0 failed`; one plugin manager per boot |
| **auto-install consistency** | every stack purged, then `lhpc auto-install --yes`: 261 s, 8 of 9 stacks installed and built; meshcom blocked until the release's meshcom binary is published (its pins moved); nothing reads "not built" |
| **host tests** | kiss, meshcore (82 s), chat, voice, graywolf, reticulum rc 0; daemon and meshtastic refused on the binary channel as designed |
| **from-zero reinstall** | not run: `install.sh` installs `main`, which carries this release only after the tag |

## New in 0.10.0 — proven on hardware

| function | proof |
|---|---|
| `stack start --band` | box D: one daemon on 868, the 433 socket absent; the plan's copyable next command keeps `--band`; `--band 915` is a usage error |
| restart-required clears | box D: an unchanged write raises no flag; `rflog --all off` flags daemon and kiss; `--all on` clears both |
| advisory Companion claim | box D: `meshcore-cli` attaches while the web UI runs; the web UI yields and reconnects after `quit` |
| RF-log decrypt, Reticulum link data | box D with a Heltec RNode: a split LXMF DIRECT is labelled link traffic |
| RF-log decrypt, Meshtastic DM | box E with a Station G2: the DM decodes to its plaintext |
| Reticulum clients on the node's RNS | box D with the RNode: LXMF both ways, from lxmd's venv to the PC and from the PC to MeshChat |
| openhop-repeater `b846c79` | box E with a T-Deck Pro (MeshCore): build without a compiler, the dashboard through the proxy (the plugin manager's routes work, `set_mode` is refused by the proxy), the plugin disable/enable/restart cycle, radio settings unchanged, adverts heard both ways |
| a busy build is named | box D: a start refused during a build names the sources it builds |

## Box E — upgrade from 0.9.2, reboots, fast lane

- **Upgrade:** `lhpc self-update --apply` refuses a checkout that is not on `main` (the identity gate), so
  the self-update transaction itself is proven only once 0.10.0 is on `main`. The run instead moved the
  checkout, reinstalled the controller and ran `lhpc self-update --repair-integration`: all 7 managed units
  reported unchanged (the unit bytes did not move from 0.9.2), `/healthz` reports `0.10.0`, and
  `status --versions` shows the moved pins as `differs` until each stack is rebuilt.
- **MeshCom after the upgrade:** see the changelog's upgrade note. Its clone stays at the old pin, and
  `lhpc update meshcom --source binary` is refused until `lhpc update meshcom-qemu --source pinned` has run.
- **Reboots:** four `systemctl reboot`, each `2 restored, 0 failed`; `fake-hwclock` saves at shutdown and
  loads at boot (`FORCE=true`); chrony synchronised afterwards.
- **Fast lane** (no compile on the Zero; rows 9, 10 and 12 not re-run): daemon binary, chat, voice
  (terminal variant), kiss, graywolf, reticulum, meshcore and meshtastic binary pass; meshcom binary is
  refused by the pins gate until the release's meshcom binary is published.
- **Web console:** `/`, `/stacks` and all nine stack pages → 200, no traceback.
- **APRS** with a T-Beam (CA2RXU firmware): graywolf's message was acknowledged by the tracker.
