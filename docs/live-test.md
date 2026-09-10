# Live tests

The newest completed live run on the reference box, and nothing else. Measured values only. Which
releases run what is defined in [maintenance.md](maintenance.md#branches-and-releases); the
full-matrix procedure is [test-matrix.md](test-matrix.md). CI and the [testlab](testlab.md) prove
the code and the console.

Earlier runs — the 0.3.9 added-files run, the 0.3.0 and 0.2.10 release matrices and the
2026-09-05 on-air silicon test other pages cite for their measured numbers — live in this file's
git history:
`git log --follow -p -- docs/live-test.md`.

## 0.3.12 → 0.3.14 — the release-automation campaign, 2026-09-10

One run of the reference box on one day, in two parts: the binary and pinned rows on the 0.3.12
candidate, then — after the bot released v0.3.14 and the box took it by `self-update` — the two
rows the daemon/Chat repin needed. Each part says which artifact it measured; neither part's rows
are evidence for the other's.

### Part 1 — the binary and pinned rows, on 0.3.12

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
| 9 | meshtastic | pinned (source) | — | — | — | — | **not re-run** — fast-lane waiver, maintainer, 2026-09-10. `meshtastic-firmware` pin `54e0d8d0` is the one measured in the 0.3.0 matrix and unchanged since v0.3.7 |
| 10 | daemon | pinned (source) | — | — | — | — | **not re-run** — same waiver. `loraham-daemon` `10f41070` + `radiolib` `187ef247`, both unchanged since v0.3.7 |
| 12 | meshcom | pinned (source) | — | — | — | — | **not re-run** — same waiver. `meshcom-qemu` `579e463e`, `meshcom-firmware` `674413ce`, `meshcom-bridge` `f0189206`, unchanged since v0.3.7 |

Also checked: `lhpc status --versions` on the three binary stacks — every installed artifact's
components equal the manifest pins, `built_from == pin` throughout.

Memory: minimum `MemAvailable` 69 952 kB in row 1 and **23 920 kB** during Reticulum's build, the
tightest point of the run. No OOM line in `dmesg` after any row. The console was left running for
the light rows and stopped for MeshCom's QEMU boot.

The three waived rows are in the table above rather than omitted from it, with the pins that make
the waiver legitimate — the [fast-lane rule](test-matrix.md#fast-lane) requires exactly that, and
the run that last measured them is the 0.3.0 matrix in this file's git history. The waiver is the
maintainer's instruction of 2026-09-10 to run the binary and pinned rows only.

Two things worth knowing next time: `rnstatus` needs `--config {runtime}/state/reticulum`, and
without it creates a stray `~/.reticulum` and reports no instance; and the operator self-update
path prints the restart and venv-sync steps it has already carried out
([backlog](backlog.md#operator-self-update-prints-steps-it-already-took)).

### Part 2 — the repin, on v0.3.14

The box was returned to `main` and taken to the release the bot had just made: `lhpc self-update
--apply` from 0.3.12 to **0.3.14 in 34.6 s**, the documented operator path. The daemon and Chat
are the two pins the release lane cannot prove without a radio, and the rewrite that prompted the
repin changed no production source, so these two rows are what the repin needs.

| row | purge | install | build | start | evidence |
|---|---|---|---|---|---|
| daemon, binary | 10 s | 10 s | **refused** (binary channel), 3 s | 14 s | both bands `RADIO=READY TXMODE=MANAGED` |
| chat, pinned | 6 s | 6 s | 5 s | typed `manual_required` | the compiled TUI drew its header, call list and `TX 433.775 / RX 433.900` under a PTY |

`lhpc status --versions` afterwards:

```
loraham-daemon  binary  binary@afa7d89a4  built_from=dbd2998b7e69  pin=dbd2998b7e69
radiolib        binary  binary@afa7d89a4  built_from=187ef24791c3  pin=187ef24791c3
loraham-chat    match   pin=dbd2998b7e69  tag=v112-6-gdbd2998
```

Before these rows `loraham-chat` read `differs`, because its checkout still held the pre-rewrite
commit. Installed source, manifest pin and rebuilt artifact now agree on the rewritten commit, on
real hardware — which is the whole claim the repin makes. Controller `4afef7394` = v0.3.14; the
image published for it is `loraham-images` v0.3.14 at `c07a63e`.

Part 2 measured these two rows and nothing else. Part 1's rows keep their own artifact and their
own date.

### Not run, and not claimed

Neither part covered these, and no earlier row implies them:

| owed row | why it is not covered here |
|---|---|
| **from-zero reinstall** | per-stack purges on an existing installation do not test that path |
| **Desktop-variant rows** | Voice GTK and Sideband need a display this Lite box has not got |
| **cross-cutting operator flows** | beyond pins-versus-binaries and the refusals recorded above |
| **booting a published image** | build and publish evidence is not boot evidence |

The [fast-lane rule](test-matrix.md#fast-lane) that scoped this campaign expressly retains
from-zero and cross-cutting checks, so the waiver does not reach them.

**Disposition: all four run in the 0.4.0 campaign.** Maintainer decision of 2026-09-10, given
during the release-automation round of that date: the round was scoped to prove the release bot
rather than LHPC itself, and these are statements about the product on hardware, so they move to
0.4.0 as their own campaign rather than gating the bot. They are recorded here, not closed — a
0.4.0 run starts from this list.
