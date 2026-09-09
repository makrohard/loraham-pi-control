# Release test matrix

Every stack **purged, installed, built, started and verified on the box**, one stack at a time,
with the install, build and start times recorded and the memory watched during the heavy compiles.
CI proves the code, the [testlab](testlab.md) proves the console; this matrix proves that a release
**installs and comes up** from nothing on the reference box.

**When it applies:** a minor release (`0.X.0`) runs it before the tag. A patch release runs the
live checks its own change calls for instead — the release policy is
[maintenance](maintenance.md#branches-and-releases). Results replace the section in
[live-test.md](live-test.md).

Evidence is the controller's own typed outcome plus the stack's own state (`lhpc status`, the
node's info, an HTTP answer, `rnstatus` counters). Log greps are not evidence.

## Contents

- [Bench](#bench)
- [Procedure per stack](#procedure-per-stack)
- [Matrix](#matrix)
- [Fast lane](#fast-lane)
- [Cross-cutting checks](#cross-cutting-checks)
- [From-zero reinstall](#from-zero-reinstall)
- [Refused as designed](#refused-as-designed)

## Bench

| | |
|---|---|
| Box | `lhpc-e293`, Raspberry Pi Zero 2 W (512 MB, 415 MB usable after zram), Lite image, LAN |
| Radio | LoRaHAM daemon serving 433 and 868; record `lhpc hardware` at the start of the run |
| Channels | `pinned` = the manifest pins; `dev` = the branch tip and the default install for every stack without a published binary ([provenance](provenance.md#selections)); `binary` for daemon, meshtastic and meshcom. All three are covered, see [Coverage](#coverage) |
| Console | left running for the light stacks; **stopped for the heavy compiles** (`systemctl --user stop lhpc-web lhpc-nginx`), as the 512 MB box requires (see [maintenance](maintenance.md)) |
| Radio budget | one stack per band at a time: 433 belongs to the daemon chain (kiss, graywolf, meshcom), 868 to one of meshtastic / MeshCore / Reticulum. Stop the previous owner before starting the next |

## Procedure per stack

Every row runs the same loop, in a tmux on the box, timed with the wrapper below. Nothing is
skipped because "it worked last release" — the one exception is the [fast lane](#fast-lane).

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
row. A stack that shares a source with an earlier row (chat and voice share the daemon's
sources) is still purged and reinstalled on its own.

| # | stack | channel | build | start | evidence |
|---|---|---|---|---|---|
| 1 | `daemon` | binary | — | both bands | `lhpc status daemon`: READY on 433 and 868; `lhpc daemon 433` answers |
| 2 | `chat` | pinned | daemon sources | interactive | the printed command runs in a terminal and exits cleanly |
| 3 | `voice` | pinned | `loraham-voice-cli` (GTK variant skipped on Lite) | interactive | the terminal variant's printed command runs; GTK reported skipped, not failed |
| 4 | `kiss` | pinned | `loraham-kiss-tnc` | 433 | verified; TCP `127.0.0.1:8001` answers |
| 5 | `graywolf` | fetched release | — | 433 (needs kiss) | verified; web UI `127.0.0.1:8080` answers; the KISS client is held |
| 6 | `reticulum` | pinned | rns, nomadnet, lxmd (sideband skipped on Lite) | the free band | `rnstatus` lists the LoRa interface; the ready marker present |
| 7 | `meshcore` | pinned | node, webui, openhop repeater source | 868, mode chat+repeater | node and repeater verified; web UI `:8788` and dashboard `:8000` answer; `meshcore-cli` listed on the Dashboard |
| 8 | `meshtastic` | binary | — | 868 (MeshCore stopped) | verified; `lhpc meshtastic --info` returns the node; `meshtastic-cli` listed |
| 9 | `meshtastic` | pinned (from source) | meshtasticd | 868 | as row 8; build time and memory recorded |
| 10 | `daemon` | pinned (from source) | RadioLib + daemon | both bands | as row 1; build time and memory recorded |
| 11 | `meshcom` | binary | bridge | 433 (graywolf/kiss stopped) | verified; web UI `:18083` 502 until boot then 200; callsign switches from the placeholder |
| 12 | `meshcom` | pinned (from source) | QEMU, firmware, bridge | 433 | as row 11 — the longest row by far (build times: [maintenance](maintenance.md#running-on-a-pi)); memory watched throughout |

Rows 9–12 are the heavy compiles: console stopped, `vmstat` running, `dmesg` checked after each.

### Coverage

Every stack on every channel it can be installed on — its default channel, the release's pins,
and the published binary where one exists. The cell names the row that proves it; the
auto-install row is the `dev` proof for every stack because that is the channel the default
all-stacks install (and the image builder) uses.

| stack | binary | pinned (source) | dev (default install) |
|---|---|---|---|
| daemon | row 1 | row 10 | auto-install (binary is its default) |
| chat | — | row 2 | auto-install |
| voice | — | row 3 | auto-install |
| kiss | — | row 4 | auto-install |
| graywolf | — (fetched release) | row 5 | auto-install |
| reticulum | — | row 6 | auto-install |
| meshcore | — | row 7 | auto-install |
| meshtastic | row 8 | row 9 | auto-install (binary is its default) |
| meshcom | row 11 | row 12 | auto-install (binary is its default) |

No empty cell — the full check, unless a [fast lane](#fast-lane) waiver applies.

## Fast lane

A release whose heavy-stack pins are unchanged since the last measured run may skip the three
from-source rows (9 meshtastic, 10 daemon, 12 meshcom) **on the maintainer's explicit waiver**: the
binary rows (1, 8, 11) still prove the artifacts that ship, and the compiles they would repeat are
the ones already timed under the same pins. Everything else runs unchanged — every light stack's
build, the cross-cutting checks, the from-zero reinstall (with the published binaries) and the host
tests. A skipped row is written into the result table as *not re-run* with a footnote naming the
run that measured it and the waiver's date — that run may live only in this file's git history —
and the pins column must show the pin is the same. A changed
pin, a changed toolchain or a changed builder image takes the row out of the fast lane.

## Cross-cutting checks

After the per-stack rows, with the box holding every stack installed and built:

| check | how | evidence |
|---|---|---|
| **auto-install consistency (CLI path)** | purge every stack, then `lhpc auto-install --yes` — the exact command the image builder runs and README step 9; every log file the run announces (`tail -f …`) must exist afterwards | every stack ends installed on its default channel (binary where published, else `dev` = the branch tip) and built; `lhpc status --versions` recorded as-is (what `match`/`differs` mean: [provenance](provenance.md)); the image's `components-*.txt` shows the same lines; nothing reads "not built"; total time recorded |
| **known-working** | after each stack's green start, the stack page must offer to record the composition; confirm it there for every source-built stack (a binary install and the fetched graywolf release have no source composition and show no offer, by design) (the CLI form is `lhpc known-working <stack>`) | the offer is visible and plainly worded (one click, no commit ids to understand); `profiles/known-working/<stack>.json` and `lhpc status --versions` show the run-proven pins (the per-release step in [maintenance](maintenance.md#moving-a-pin)) |
| **boot restore** | power-cycle once with the release's default running set | `N restored, 0 failed`, console reachable |
| **web console** | Dashboard, Apps rows, Settings of every stack after the run | no traceback in the console log; every row opens |
| **pins vs binaries** | `lhpc status --versions` on the three binary stacks | the installed binary's components equal the manifest pins |
| **host tests — the very last step, after the from-zero reinstall and before the tag** | after every compile has succeeded and the box has been reinstalled: `lhpc test daemon --yes` first, then `lhpc test <stack> --yes` for every other stack, one at a time, timed; where a stack offers a bounded TX test, `lhpc test <stack> --tx --yes` as well (real RF: only with the operator's go for this bench and the antennas or dummy loads in place) | outcome and duration per stack, for the record only — heavy memory pressure and even an OOM kill are expected on the Zero 2 W and are not a release blocker |

## From-zero reinstall

After the rows, the controller itself is reinstalled from nothing on the same box, timed, and driven
the way a new operator would drive it — the happy path only: the defaults install the three heavy
stacks from the published binaries, the light stacks build from source. The from-source rows above
already proved and timed every compile, and a Zero 2 W's Wi-Fi can drop under a long compile
, so no heavy compile is repeated before the host tests:

| step | how | evidence |
|---|---|---|
| 1. uninstall + wipe | `sudo bash config/files/firewall/firewall-reset.sh` first when the managed firewall is installed (every uninstall refuses while it is), then `bash uninstall.sh --purge` from the old checkout | stacks stopped and verified, runtime root gone, no managed unit left |
| 2. install | the documented happy path, line by line (README → `install.sh` → console); a doc line that does not work as written is corrected in the same release | `lhpc --version`, console answers; the commands run are the evidence |
| 3. network | the Apps page's Network panel: the box joins (or re-joins) the operator's Wi-Fi; the AP stays the fallback | console reachable on the joined network |
| 4. first start, global callsign unset | one licensed stack started before any identity is set | the typed refusal (CLI hint `lhpc config operator --callsign`, the Settings row highlighted in the console); nothing started |
| 5. identity, then the rest | `lhpc config operator --callsign <CALL>`, then the remaining stacks' first start with the saved defaults | every stack starts; Meshtastic / MeshCore still need their own node names, as documented |
| 6. passwords | after each stack's first start, its Password section on the stack page | the stored value is shown and equals the file (graywolf admin, MeshCore repeater dashboard, MeshCom HMAC via Renew) |
| 7. auto-install from the web console | Apps → Auto-install with the defaults (binary where published, else `dev`; no tests, no TX) | every mandatory stack installed and built; the run's total time; **no GTK / X11 / Wayland package installed** (`dpkg -l` count before and after) |
| 8. start and stop of every stack | on the fresh install: `lhpc stack start <stack> --yes` → verify → `lhpc stack stop <stack> --yes`, one stack at a time, then the conflicting pairs | every stack starts and stops with the typed outcomes; a band or TX-mode conflict (meshtastic vs MeshCore on 868, MeshCom vs graywolf on 433) is refused with its reason, nothing half-started; interactive components (chat, voice terminal, Meshtastic CLI, MeshCore CLI) are listed with their command and never started by the controller |

Remote exposure with mTLS and the managed firewall needs the operator's one root step (the copy-paste
`sudo` line the console prints — [firewall](firewall.md), [webserver](webserver.md)); it is exercised as the last from-zero step when the operator enters
that line, otherwise its contracts rest on the unit tests.

The result lines replace the section in [live-test.md](live-test.md).

## Refused as designed

These contracts are pinned by unit tests and are not re-run row by row; the model behind them is
[architecture](architecture.md#radios-bands-and-resource-claims). The live run naturally hits
the first three; note them when they occur.

| refusal | pinned by |
|---|---|
| a second owner of a band (meshtastic while MeshCore or Reticulum holds 868; kiss while Reticulum holds 433) | `tests/core/test_run_order.py`, `tests/stacks/test_reticulum_stack.py` |
| meshtastic with Reticulum on the bus (`spi.bus.0.unlocked`) | `tests/stacks/test_reticulum_stack.py` |
| a start with a missing identity (Meshtastic / MeshCore node name, MeshCom callsign) — plan and apply, CLI and web | `tests/core/test_identity.py` |
| a source update while a consumer runs; a drifted checkout is not overwritten | `tests/core/test_uninstall_safety.py`, `tests/install/test_source.py` |
| a start against a build receipt that no longer matches its sources | `tests/stacks/test_reticulum_stack.py` |
| a dependent component when its dependency failed to start | `tests/stacks/test_reticulum_stack.py` |
| changing the GPS source, or a stack's `use_gps`, while a consumer runs | `tests/stacks/test_gps.py` |
