# Release test matrix

The leading live test of a release: every stack is **purged, installed, built, started and verified
on the box**, one stack at a time, with the install, build and start times recorded and the memory
watched during the heavy compiles. CI proves the code, the [testlab](testlab.md) proves the
console, the [silicon test](silicon-test-2026-09-05.md) proves the stacks on the air; this matrix
proves that a release **installs and comes up** from nothing on the reference box. It is run before
a release is tagged and its result table is committed with the release.

Evidence is the controller's own typed outcome plus the stack's own state (`lhpc status`, the
node's info, an HTTP answer, `rnstatus` counters). Log greps are not evidence.

## Contents

- [Bench](#bench)
- [Procedure per stack](#procedure-per-stack)
- [Matrix](#matrix)
- [Cross-cutting checks](#cross-cutting-checks)
- [Refused as designed](#refused-as-designed)
- [Results](#results)

## Bench

| | |
|---|---|
| Box | `lhpc-e293`, Raspberry Pi Zero 2 W (512 MB, 415 MB usable after zram), Lite image, LAN |
| Radio | LoRaHAM daemon serving 433 and 868; record `lhpc hardware` at the start of the run |
| Channels | `pinned` = the manifest pins (the known-working line, main); `dev` = the branch tip and the default install for every stack without a published binary; `binary` for daemon, meshtastic and meshcom. All three are covered, see [Coverage](#coverage) |
| Console | left running for the light stacks; **stopped for the heavy compiles** (`systemctl --user stop lhpc-web lhpc-nginx`), as [field-notes](field-notes.md#build-durations--memory-pressure-512-mb-zero-2-w) require on 512 MB |
| Radio budget | one stack per band at a time: 433 belongs to the daemon chain (kiss, graywolf, igate, meshcom), 868 to one of meshtastic / MeshCore / Reticulum. Stop the previous owner before starting the next |

## Procedure per stack

Every row runs the same loop, in a tmux on the box, timed with the wrapper below. Nothing is
skipped because "it worked last release".

```bash
t() { local s=$(date +%s); "$@"; echo "[timer] $* -> $(( $(date +%s) - s )) s"; }

t lhpc clean <stack> --purge --yes            # 1. purge: sources, config, logs, history
t lhpc install <stack> --source <chan> --yes  # 2. install on the channel under test
t lhpc build <stack> --yes                    # 3. build (no-op for pure binary / fetched stacks)
t lhpc stack start <stack> --yes              # 4. start on the band the radio budget allows
lhpc status <stack>                           # 5. verify: the row's evidence column
t lhpc stack stop <stack> --yes               # 6. stop; `lhpc status` shows nothing left running
```

- **Times.** `install` is the wrapper's number for step 2, `build` for step 3, `start` for step 4
  (the controller returns when the components are verified). Where a stack is *usable* later than
  it is *verified* (MeshCom's web UI answers 502 until the firmware has booted), record both.
- **Memory / OOM** during every build and every start of a heavy stack: `vmstat -n 10 >
  ~/vm-<stack>.log &` alongside, `free -m` before and after, and `dmesg -T | grep -iE
  'oom|killed process'` afterwards. Any OOM line, any exit code from a killed child, and the
  minimum `MemAvailable` go into the result table.
- **Box left clean.** After the last row `lhpc status` lists only what the release ships as
  running by default (nothing), and the runtime holds no orphan `state/` markers.

## Matrix

Order matters: light stacks first, the heavy compiles last, MeshCom from source as the very last
row. A stack that shares a source with an earlier row (chat, igate and voice share the daemon's
sources) is still purged and reinstalled on its own.

| # | stack | channel | build | start | evidence |
|---|---|---|---|---|---|
| 1 | `daemon` | binary | — | both bands | `lhpc status daemon`: READY on 433 and 868; `lhpc daemon 433` answers |
| 2 | `chat` | pinned | daemon sources | interactive | the printed command runs in a terminal and exits cleanly |
| 3 | `igate` | pinned | daemon sources | 433 | verified; needs the daemon's 433 |
| 4 | `voice` | pinned | `loraham-voice-cli` (GTK variant skipped on Lite) | interactive | the terminal variant's printed command runs; GTK reported skipped, not failed |
| 5 | `kiss` | pinned | `loraham-kiss-tnc` | 433 | verified; TCP `127.0.0.1:8001` answers |
| 6 | `graywolf` | fetched release | — | 433 (needs kiss) | verified; web UI `127.0.0.1:8080` answers; the KISS client is held |
| 7 | `reticulum` | pinned | rns, nomadnet, lxmd (sideband skipped on Lite) | the free band | `rnstatus` lists the LoRa interface; the ready marker present |
| 8 | `meshcore` | pinned | node, webui, openhop repeater source | 868, mode chat+repeater | node and repeater verified; web UI `:8788` and dashboard `:8000` answer; `meshcore-cli` listed on the Dashboard |
| 9 | `meshtastic` | binary | — | 868 (MeshCore stopped) | verified; `lhpc meshtastic --info` returns the node; `meshtastic-cli` listed |
| 10 | `meshtastic` | pinned (from source) | meshtasticd | 868 | as row 9; build time and memory recorded |
| 11 | `daemon` | pinned (from source) | RadioLib + daemon | both bands | as row 1; build time and memory recorded |
| 12 | `meshcom` | binary | bridge | 433 (graywolf/kiss stopped) | verified; web UI `:18083` 502 until boot then 200; callsign switches from the placeholder |
| 13 | `meshcom` | pinned (from source) | QEMU, firmware, bridge | 433 | as row 12 — the longest row; QEMU ~68 min and the firmware ~26 min cold at `-j1`; memory watched throughout |

Rows 10–13 are the heavy compiles: console stopped, `vmstat` running, `dmesg` checked after each.

### Coverage

Every stack on every channel it can be installed on — its default channel, the release's pins,
and the published binary where one exists. The cell names the row that proves it; the
auto-install row is the `dev` proof for every stack because that is the channel the default
all-stacks install (and the image builder) uses.

| stack | binary | pinned (source) | dev (default install) |
|---|---|---|---|
| daemon | row 1 | row 11 | auto-install (binary is its default) |
| chat | — | row 2 | auto-install |
| igate | — | row 3 | auto-install |
| voice | — | row 4 | auto-install |
| kiss | — | row 5 | auto-install |
| graywolf | — (fetched release) | row 6 | auto-install |
| reticulum | — | row 7 | auto-install |
| meshcore | — | row 8 | auto-install |
| meshtastic | row 9 | row 10 | auto-install (binary is its default) |
| meshcom | row 12 | row 13 | auto-install (binary is its default) |

No empty cell: this is the full pre-release check.

## Cross-cutting checks

After the per-stack rows, with the box holding every stack installed and built:

| check | how | evidence |
|---|---|---|
| **auto-install consistency** | purge every stack, then `lhpc auto-install --yes` — the exact command the image builder runs | every stack ends installed on its default channel (binary where published, else `dev` = the branch tip) and built; `lhpc status --versions` is recorded as-is: a `dev` checkout reads `match` only while the branch tip equals the pin and `differs` once upstream moved (the image's `components-*.txt` shows the same lines); nothing reads "not built"; total time recorded |
| **known-working** | `lhpc known-working <stack>` for each started stack | the profile records the run-proven commits (the per-release step in [maintenance](maintenance.md#per-release)) |
| **boot restore** | power-cycle once with the release's default running set | `N restored, 0 failed`, console reachable |
| **web console** | Dashboard, Apps rows, Settings of every stack after the run | no traceback in the console log; every row opens |
| **pins vs binaries** | `lhpc status --versions` on the three binary stacks | the installed binary's components equal the manifest pins |
| **host tests — the very last step before the tag and the images** | after every compile has succeeded: `lhpc test daemon --yes` first, then `lhpc test <stack> --yes` for every other stack, one at a time, timed; where a stack offers a bounded TX test, `lhpc test <stack> --tx --yes` as well (real RF: only with the operator's go for this bench and the antennas or dummy loads in place) | outcome and duration per stack, for the record only — heavy memory pressure and even an OOM kill are expected on the Zero 2 W and are not a release blocker |

## Refused as designed

These contracts are pinned by unit tests and are not re-run row by row. The live run naturally hits
the first three; note them when they occur.

| refusal | pinned by |
|---|---|
| a second owner of a band (meshtastic while MeshCore or Reticulum holds 868; kiss while Reticulum holds 433) | `tests/test_run_order.py`, `tests/test_reticulum_stack.py` |
| meshtastic with Reticulum on the bus (`spi.bus.0.unlocked`) | `tests/test_reticulum_stack.py` |
| a start with a missing identity (Meshtastic / MeshCore node name, MeshCom callsign) — plan and apply, CLI and web | `tests/test_identity.py` |
| a source update while a consumer runs; a drifted checkout is not overwritten | `tests/test_source.py` |
| a start against a build receipt that no longer matches its sources | `tests/test_reticulum_stack.py` |
| a dependent component when its dependency failed to start | `tests/test_deps.py` |
| changing the GPS source, or a stack's `use_gps`, while a consumer runs | `tests/test_gps.py` |

## Results

One table per release. Times in seconds unless noted; `min avail` is the lowest `MemAvailable`
seen during the row; OOM is "none" or the `dmesg` line.

### 0.2.10 — pending

Pins moved in this release: see the CHANGELOG. Run on: —.

| # | stack | channel | install | build | start | usable | min avail | OOM | verdict |
|---|---|---|---|---|---|---|---|---|---|
| 1 | daemon | binary | | | | | | | |
| 2 | chat | pinned | | | | | | | |
| 3 | igate | pinned | | | | | | | |
| 4 | voice | pinned | | | | | | | |
| 5 | kiss | pinned | | | | | | | |
| 6 | graywolf | fetched | | | | | | | |
| 7 | reticulum | pinned | | | | | | | |
| 8 | meshcore | pinned | | | | | | | |
| 9 | meshtastic | binary | | | | | | | |
| 10 | meshtastic | source | | | | | | | |
| 11 | daemon | source | | | | | | | |
| 12 | meshcom | binary | | | | | | | |
| 13 | meshcom | source | | | | | | | |

Cross-cutting: auto-install — · known-working — · boot restore — · console — · pins vs binaries —.

| host tests | outcome | duration | OOM | TX test |
|---|---|---|---|---|
| daemon | | | | |
| other stacks (one row each) | | | | |

### Earlier releases

0.1.7 (Reticulum, three boxes, both bands, every RF direction between SX1276/SX127x/SX1262) and
0.1.8 (GPS: all four sources against all three consumers) were run against matrices of their own;
what they proved is now stated where it applies — the SX1262 notes in
[field-notes](field-notes.md#waveshare-sx1262-hat--no-tcxo-and-two-driver-traps) and
[reticulum](stacks/reticulum.md#hardware), the GPS behaviour in [gps](gps.md) — and their refusals
are pinned by the tests listed above. The 433 path of the SX1262 remains untested on the air.
