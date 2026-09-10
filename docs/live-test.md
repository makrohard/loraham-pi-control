# Live tests

The newest completed live run on the reference box, and nothing else. Measured values only. Which
releases run what is defined in [maintenance.md](maintenance.md#branches-and-releases); the
full-matrix procedure is [test-matrix.md](test-matrix.md). CI and the [testlab](testlab.md) prove
the code and the console.

Earlier runs — the 0.3.9 added-files run, the 0.3.0 and 0.2.10 release matrices and the
2026-09-05 on-air silicon test other pages cite for their measured numbers — live in this file's
git history:
`git log --follow -p -- docs/live-test.md`.

## 0.3.12 — the binary and pinned rows, in preparation of 0.4.0, 2026-09-10

Deliberately short. The full twelve-row matrix ran clean for 0.3.0 and nothing since has touched
real silicon or a real radio, so this run re-proves only what a release-automation change can
plausibly break — the binary channel and the pinned default — and leaves 0.4.0 starting from a
measured box instead of an assumed one. The three from-source rows (9 Meshtastic, 10 daemon,
12 MeshCom) are the [fast lane](test-matrix.md#fast-lane) waiver the maintainer gave on
2026-09-10: their pins are unchanged and the compiles they would repeat were timed in the 0.3.0
run, which lives in this file's git history.

Box `lhpc-e293`, Pi Zero 2 W (415 MB usable), Debian 13 Lite, aarch64, `lhpc hardware` =
Uputronics dual (CE0 433 + CE1 868). It was taken from 0.3.1 to 0.3.11 by `lhpc self-update
--apply` (35 s) and then to the release candidate by hand: `self-update` **refuses** a checkout
that is not on `main` (*"unsafe controller identity"*), which is the gate working, so the
candidate was checked out and its venv synced. Console left running for the light rows.

| row | stack | channel | purge | install | build | start | evidence |
|---|---|---|---|---|---|---|---|
| 1 | daemon | binary | 6 s | 8 s (1.0 MB) | **refused**, 2 s | 12 s | both bands `RADIO=READY TXMODE=MANAGED`; `lhpc daemon 433` (CAD BUSY, RSSI −84 dBm) and `868` (CAD FREE, −108 dBm) answer. 868 needed Meshtastic stopped first — the controller had skipped it with *"868 owned by meshtastic"* |
| 2 | chat | pinned | 6 s | 5 s | 5 s | typed `manual_required` | the printed command drew the TUI — header, call list, `TX 433.775 / RX 433.900`; Ctrl-C exit 130, no orphan process, daemon unaffected |
| 3 | voice | pinned | 6 s | 4 s | 4 s, GTK **skipped, not failed** | 8 s | start reports `[skipped] loraham-voice: GUI toolkit not installed` and `[manual_required] loraham-voice-cli`; the terminal variant drew `Verfügbare Audiogeräte` and `Aufnahme [0]:` |
| 4 | kiss | pinned | 6 s | 5 s | 18 s | 9 s | `[verified] loraham-kiss-tnc: started; ready endpoint(s) up (127.0.0.1:8001: present)` |
| 5 | graywolf | fetched release | 6 s | 2 s | 18 s (the release fetch) | 10 s | `[verified] graywolf … (127.0.0.1:8080: present); required post-start completed`; graywolf and kiss-serial both read `depends on loraham-kiss-tnc: running on 433 MHz` — the KISS client is held |
| 6 | reticulum | pinned | 8 s | 250 s | 168 s, Sideband **skipped** | 8 s | first start **refused**: *"Cannot run 'reticulum': daemon must be stopped first"* — the SPI-bus contract. With graywolf, kiss and the daemon stopped it came up on 868: `[verified] rns … ready: present`, and `rnstatus` lists `LoRaSPIInterface[LoRa] Status: Up` |
| 7 | meshcore | pinned | 9 s | 75 s | 443 s | 11 s | two typed refusals first — missing `node_name`, then `chat+repeater` without a repeater name, the save rolled back. Then `mode: chat+repeater`: node on 868, companion `:5000`, repeater dashboard `:8000` → 200, web UI `:8788` → 200 (started by name, as an optional component must be) |
| 8 | meshtastic | binary | 7 s | 131 s (50.7 MB) | **refused** | 35 s | refused first for `node_name`/`node_short`; then verified, `:4403` up, GPS live, all four post-start steps applied. `lhpc meshtastic --info` returns the node — owner `LHPCBENCH (LHPB)`, firmware `2.7.26.54e0d8d` = pin `54e0d8d0`. The artifact is the republished one, `binary@a0ffaa37a` |
| 11 | meshcom | binary | 6 s | 26 s (11.6 MB) | **refused** | 392 s | bridge on `:7000`, GPS relay live, QEMU node verified with the post-start completed; the firmware's UART shows `SX1268 … success` and `OpenETH GOT_IP`, the bridge logged `XR client connected`. `:18083` refused until the node had booted, then **200**, and the callsign reached the net-console — *"acknowledged on attempt 22"*. Inside the 6–14 min the controller's own note predicts for this box |

Also checked: `lhpc status --versions` on the three binary stacks — every installed artifact's
components equal the manifest pins, `built_from == pin` throughout.

Memory: minimum `MemAvailable` 69 952 kB in row 1 and **23 920 kB** during Reticulum's build, the
tightest point of the run. No OOM line in `dmesg` after any row. The console was left running for
the light rows and stopped for MeshCom's QEMU boot.

Not run, and not claimed: the from-zero reinstall, the Desktop-variant rows (Voice GTK and
Sideband need a display this Lite box does not have), and rows 9, 10 and 12 under the waiver
above.

Two things worth knowing next time: `rnstatus` needs `--config {runtime}/state/reticulum`, and
without it creates a stray `~/.reticulum` and reports no instance; and the operator self-update
path prints the restart and venv-sync steps it has already carried out
([backlog](backlog.md#operator-self-update-prints-steps-it-already-took)).
