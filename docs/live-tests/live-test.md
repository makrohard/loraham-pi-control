# Live tests

The newest completed live run on the reference box, and nothing else. Measured values only. Which
releases run what is defined in [maintenance.md](../maintenance.md#branches-and-releases); the
full-matrix procedure is [test-matrix.md](../test-matrix.md). CI and the [testlab](../testlab.md) prove
the code and the console.

Earlier runs — the 0.3.12→0.3.14 release-automation campaign, the 0.3.9 added-files run, the
0.3.0 and 0.2.10 release matrices and the 2026-09-05 on-air silicon test other pages cite for
their measured numbers — live in this file's git history:
`git log --follow -p -- docs/live-tests/live-test.md`.

## 0.5.0 — the scoped Reticulum run, 2026-09-12

A **scoped** matrix on the maintainer's waiver: this cycle's change is confined to the reticulum
stack plus two console surfaces, so row 6 runs from zero and the rest are recorded *not re-run*,
footnoted to the 0.4.0 full matrix of 2026-09-11 — which lives in this file's git history, the
convention the [fast lane](../test-matrix.md#fast-lane) already uses for a waived row.

Controller `8d983ac` on `feature/reticulum-client-and-interfaces`. Box `lhpc-e293`, Pi Zero 2 W
(415 MB), Debian 13 Lite, aarch64, `lhpc hardware` = Uputronics dual. Console left running.

### Row 6 — reticulum, from zero

| purge | install (pinned) | build | start |
|---|---|---|---|
| 11 s | 285 s | 252 s | 15 s |

`[verified] rns … ready: present`; `127.0.0.1:37428`, `:37429`, `:4242` all present; every
component reads `src match`; Sideband `[skip] … GUI dependencies not installed`, which is the
headless-safe default and not a failure. Lowest free memory during the whole run **27 MB**, no OOM.

Evidence beyond the row's own column:

| check | result |
|---|---|
| the radio's mode, as the NODE reports it | `LoRaSPIInterface[LoRa] · Mode: Internal`, `TCPServerInterface[Client access] · Mode: Gateway` |
| the generated config | `enable_transport = No`; LoRa `internal`, Client access `gateway`, Internet `boundary` + `recursive_prs = yes`, `enabled = no`; file mode **0400** |
| the operator may still choose the fuller bridge | `lora_announce_relay = gateway` → the node reports `Mode: Gateway`; set back → `Mode: Internal` |
| MeshChat | starts by name (a stack start leaves it stopped), `[verified]`, UI **200** |
| the frozen dependency closure on aarch64 | the from-zero build resolved `pip install -c meshchat-constraints.txt` and left exactly **17** packages in the venv — the outstanding re-proof from the correction review |
| known-working | recorded for reticulum |
| web console | `/`, `/stacks` and three stack bodies all **200**, no traceback, and zero errors in the console log for the run window |

### The correction the audit demanded, proven against a running node

Three saved configurations that config generation rejects, each attempted as a real
`lhpc stack restart rns` while the node was up:

| saved state | result | node |
|---|---|---|
| IFAC network name, no passphrase | typed refusal naming the passphrase | **same pid, never stopped** |
| `internet_ifac_netkey = 123456` (a bare number) | same typed refusal | **same pid** |
| passphrase present, `secrets.toml` at `0644` | the loader's refusal, naming the file and the `chmod` remedy | **same pid** |

Before the fix each of these stopped the node first and refused afterwards, leaving it down. The
settings were restored to their defaults afterwards and the node came back `[verified]`.

### Boot restore — two power cycles, and what the first one taught

Both reboots were triggered from the console's own Reboot control (the run account has no
passwordless `reboot`, which is itself the correct posture).

| | last start before the reboot | result |
|---|---|---|
| 1 | component-scoped (`stack start meshchat`, `stack restart rns`) | `no-plan — 0 restored, 0 failed, 1 skipped`, reason **"no full-stack-scoped evidence (component-scoped starts are never widened)"** |
| 2 | stack-scoped (`stack start reticulum`) | **`done — 1 restored, 0 failed, 0 cancelled, 0 pending, 0 skipped`**; `rns` back with `:37428` and `:4242`; console **200** |

The first is a correct **negative**, not a failure, and it is recorded because an operator can
easily read it as one: restoring a whole stack from a component-scoped start would start
components nobody asked for, so the driver declines to widen the evidence it has. Down-to-up was
46 s and 45 s.

### Not re-run

Rows 1–5 and 7–12, and the cross-cutting checks other than boot restore, the console and
known-working, are *not re-run*; they were measured in the 0.4.0 full matrix (2026-09-11, this
file's git history) and this cycle changes nothing they cover.

## 0.4.0 — the full release matrix, 2026-09-11

Nine of the twelve rows run on the reference box, plus the cross-cutting checks and the four rows
0.3.14 left owed. Three rows are the
[fast lane](../test-matrix.md#fast-lane) waiver.

The controller under test is **v0.3.16 `f0509102d`**. 0.4.0 adds to it only the version scalars and
this document, so every row below measures the code 0.4.0 ships. The box was taken there by the
documented operator path — `lhpc self-update --apply --yes` **32 s**, then the venv sync it prints,
**22 s** — from 0.3.14.

Box `lhpc-e293`, Pi Zero 2 W (415 MB usable, 1182 MB swap), Debian 13 Lite, aarch64,
`lhpc hardware` = Uputronics dual (CE0 433 + CE1 868). Console left running for the light rows and
stopped for MeshCom, MeshCore and the auto-install run.

### The rows

| row | stack | channel | purge | install | build | start | evidence |
|---|---|---|---|---|---|---|---|
| 1 | daemon | binary | 6 s | 9 s (1.0 MB) | **refused**, 3 s | 14 s | both bands `RADIO=READY TXMODE=MANAGED`; `lhpc daemon 433` live RSSI −86 dBm CAD FREE, `868` −106 dBm CAD FREE. Artifact `binary@d7a72e11c`, `built_from=82c82c3f1d99` = pin |
| 2 | chat | pinned | 7 s | 5 s | 5 s | typed `manual_required`, 7 s | built by `gcc clients/chat/lorachat_ncurses_113.c` — the path the daemon restructure moved it to. The TUI drew its header, a call list carrying live off-air stations, and `TX 433.775 / RX 433.900`; no orphan process after exit |
| 3 | voice | pinned | 7 s | 5 s | 5 s, GTK **skipped, not failed** | 9 s | `[skipped] loraham-voice: GUI toolkit not installed` and `[manual_required] loraham-voice-cli`; the terminal variant listed the ALSA devices and drew `433MHz 434.700 SF7 BW125 | TX:Codec2-3200 … Verbunden` |
| 4 | kiss | pinned | 7 s | 5 s | 15 s | 9 s | adopted `pinned-verified` at the moved pin `v0.5.1-2-ge7646c1`; `[verified] … 127.0.0.1:8001: present` |
| 5 | graywolf | fetched release | 7 s | 2 s | 31 s (fetch 0.14.13) | 11 s | `[verified] … (127.0.0.1:8080: present); required post-start completed`; the UI answers **200**, `<title>graywolf`; kiss reads `running on 433 MHz` — the KISS client is held |
| 6 | reticulum | pinned | 8 s | 276 s | 160 s, Sideband **skipped** | 11 s | `[verified] rns … ready: present`; `rnstatus --config {runtime}/state/reticulum` lists `LoRaSPIInterface[LoRa] Status: Up` |
| 7 | meshcore | pinned | 11 s | 92 s | 441 s | 13 s | two typed refusals first — missing `node_name`, then `chat+repeater` without a repeater name, **the save rolled back**. `openhop-repeater-src` adopted `pinned-verified` at the moved pin `1.1.4-5-gc02b3cb`, and `openhop-apply-patch.sh` applied the noise-floor patch without conflict. Node on 868 (`:5000`, `:8000`), repeater dashboard `:8000` → **200** `<title>Repeater Dashboard`, web UI `:8788` → **200** after being started by name, as an optional component must be |
| 8 | meshtastic | binary | 7 s | 130 s (50.7 MB) | **refused**, 3 s | 35 s | refused first for `node_name`/`node_short`; then `[verified] … :4403 present`, GPS live, post-start completed. `lhpc meshtastic --info`: owner `LHPCBENCH (LHPB)`, firmware `2.7.26.54e0d8d` = pin `54e0d8d0`, 19 nodes in the db |
| 11 | meshcom | binary | 7 s | 28 s (11.6 MB) | **refused**, 2 s | 361 s | all four components verified: bridge `:7000`, GPS live (5 sentences), QEMU node `:12323` with post-start completed. The firmware UART shows `SX1268 … success`, `OpenETH GOT_IP ip=10.0.2.15` and `[BOOT];ready`; `:18083` answers **200** (29 kB) with the operator's callsign in the title, and the net-console `--info` returns the full node state. The daemon's own 433 log recorded a real `[TX433] 27 Byte` transmission from it |
| 9 | meshtastic | pinned (source) | — | — | — | — | **not re-run** — fast-lane waiver, maintainer, 2026-09-11. `meshtastic-firmware` `54e0d8d0` unchanged since v0.3.7 |
| 10 | daemon | pinned (source) | — | — | — | — | **not re-run** — same waiver. The moved daemon pin `82c82c3f1` was nevertheless compiled from source by the binary builder, which drives LHPC's own recipe in the container, and the artifact row 1 installed is that compile |
| 12 | meshcom | pinned (source) | — | — | — | — | **not re-run** — same waiver. `meshcom-qemu` `579e463e`, `meshcom-firmware` `674413ce` unchanged; `meshcom-bridge` moved to `35a9348a0` and ships in the artifact row 11 installed |

Rows 9, 10 and 12 are the only genuinely long compiles on this box. Voice and MeshCore were at
first waived with them and then run, because the measured numbers say they do not belong in that
class — 5 s and 441 s against the hours those three take.

### Cross-cutting checks

| check | result |
|---|---|
| **pins vs binaries** | all three binary stacks read `built_from == pin`: daemon `binary@d7a72e11c` `82c82c3f1d99`, meshtastic `binary@d5078bc86` `54e0d8d0ab2f`, meshcom `binary@d79715da3` covering `meshcom-bridge@35a9348a0`, `meshcom-firmware@674413ce3`, `meshcom-gps-relay@579e463e2`, `meshcom-qemu@579e463e2` |
| **`dev` selector spot-check** (kiss) | installed `GitHub dev: match (version e7646c1) [provenance: mutable-dev]` — the selector resolved to the branch tip, which is now also the pin, so `--versions` reads `match`; the stack started `[verified]`, and reinstalling on the default channel returned it to `pinned-verified` |
| **known-working** | recorded for `kiss`, `meshcore` and `reticulum` — `profiles/known-working/*.json`. `graywolf` correctly refuses: *"no source composition to record (binary install or fetched release)"*. `chat` and `voice` also refuse, because their components are interactive and the controller never sees them running — see the note below |
| **web console** | `:8443` → **200**, `<title>dashboard — LoRaHAM Pi Control`, all nine stacks listed; zero tracebacks in the console journal for the whole run |
| **band and bus refusals** | Meshtastic while Reticulum held 868 → `[conflict] loraham.radio.868 is held by running stack 'reticulum'`, then `Cannot run 'meshtastic': reticulum must be stopped first`. Starting the **daemon** in the same state is *not* refused: it comes up on 433 alone with the 868 socket `absent`, which is the per-band ownership model over a shared SPI bus working as designed |
| **memory** | minimum free+buffers+cache 257–270 MB across the light rows. **No OOM line in `dmesg` after any row** |

### auto-install, boot restore and host tests

**auto-install consistency.** Every stack purged, then `lhpc auto-install --yes` — the command the
image builder runs and README step 9:

```
auto-install run completed: 9/9 stack(s) successful, 0 blocked, 0 failed, 0 skipped   1105 s
```

Afterwards `lhpc status --versions` shows **zero `differs`**: every source component equals its
manifest pin, every binary stack reads `built_from == pin`, and nothing reads *not built*.
`sideband` reads `missing`, which is the headless skip, not a gap. The three heavy stacks came from
the published binaries, the light ones built from source.

**Boot restore.** One power cycle with the 433 chain running: **3 restored, 0 failed** — daemon,
kiss and graywolf all back, and the console answered `200` again.

**Host tests.** `kiss` passed in 21 s and `meshcore` in 162 s. `chat`, `voice`, `graywolf` and
`reticulum` declare `(no host test)` and correctly report *Nothing to do*. `daemon`, `meshtastic`
and `meshcom` **refuse**: a binary install has no source tree to test, the same contract that
refuses their build. No TX test was run.

Memory across the whole run, as minimum free+buffers+cache: 257–270 MB for the light rows, 234 MB
(Meshtastic), 204 MB (MeshCom), 120 MB (MeshCore) and **109 MB during auto-install**, the tightest
point. **No OOM line in `dmesg` for the entire campaign.**

### The four rows 0.3.14 owed this campaign

| owed row | disposition |
|---|---|
| **cross-cutting operator flows** | **closed** — the table above |
| **from-zero reinstall** | **still owed, and now with a known reason.** `uninstall.sh --purge` refuses while the managed firewall is installed, and removing it is the operator's own `sudo bash config/files/firewall/firewall-reset.sh`. This box has no passwordless sudo, so the path cannot be driven unattended. The refusal is the gate working. `auto-install` on a fully purged runtime — step 7 of that path — is covered above and passed 9/9 |
| **Desktop-variant rows** | **not coverable on this box** — Voice GTK and Sideband need a DISPLAY a Lite image has not got. What is proven is that both *skip* rather than fail: `[skipped] loraham-voice: GUI toolkit not installed` and `[skip] sideband: GUI dependencies not installed` |
| **booting a published image** | **still owed** — it needs a card written and a physical boot, which no remote run can do |

The two that remain owed are owed for reasons that will not change by re-running this matrix. They
need an operator at the box.
