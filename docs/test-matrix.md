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
| **auto-install consistency (CLI path)** | purge every stack, then `lhpc auto-install --yes` — the exact command the image builder runs and README step 8; every log file the run announces (`tail -f …`) must exist afterwards | every stack ends installed on its default channel (binary where published, else `dev` = the branch tip) and built; `lhpc status --versions` is recorded as-is: a `dev` checkout reads `match` only while the branch tip equals the pin and `differs` once upstream moved (the image's `components-*.txt` shows the same lines); nothing reads "not built"; total time recorded |
| **known-working** | after each stack's green start, the stack page must offer to record the composition; confirm it there for every stack (the CLI form is `lhpc known-working <stack>`) | the offer is visible and plainly worded (one click, no commit ids to understand); `profiles/known-working/<stack>.json` and `lhpc status --versions` show the run-proven pins (the per-release step in [maintenance](maintenance.md#per-release)) |
| **boot restore** | power-cycle once with the release's default running set | `N restored, 0 failed`, console reachable |
| **web console** | Dashboard, Apps rows, Settings of every stack after the run | no traceback in the console log; every row opens |
| **pins vs binaries** | `lhpc status --versions` on the three binary stacks | the installed binary's components equal the manifest pins |
| **host tests — the very last step, after the from-zero reinstall and before the tag** | after every compile has succeeded and the box has been reinstalled: `lhpc test daemon --yes` first, then `lhpc test <stack> --yes` for every other stack, one at a time, timed; where a stack offers a bounded TX test, `lhpc test <stack> --tx --yes` as well (real RF: only with the operator's go for this bench and the antennas or dummy loads in place) | outcome and duration per stack, for the record only — heavy memory pressure and even an OOM kill are expected on the Zero 2 W and are not a release blocker |

## From-zero reinstall

After the rows, the controller itself is reinstalled from nothing on the same box, timed, and driven
the way a new operator would drive it — the happy path only: the defaults install the three heavy
stacks from the published binaries, the light stacks build from source. The from-source rows above
already proved and timed every compile, and a Zero 2 W's Wi-Fi can drop under a long compile
(it did once in this release's run), so no heavy compile is repeated before the host tests:

| step | how | evidence |
|---|---|---|
| 1. uninstall + wipe | `bash uninstall.sh --purge` from the old checkout | stacks stopped and verified, runtime root gone, no managed unit left |
| 2. install | the documented happy path, line by line (README → field-notes fresh-install checklist → `install.sh` → console); a doc line that does not work as written is corrected in the same release | `lhpc --version`, console answers; the commands run are the evidence |
| 3. network | the Apps page's Network panel: the box joins (or re-joins) the operator's Wi-Fi; the AP stays the fallback | console reachable on the joined network |
| 4. first start, global callsign unset | one licensed stack started before any identity is set | the typed refusal (CLI hint `lhpc config operator --callsign`, the Settings row highlighted in the console); nothing started |
| 5. identity, then the rest | `lhpc config operator --callsign <CALL>`, then the remaining stacks' first start with the saved defaults | every stack starts; Meshtastic / MeshCore still need their own node names, as documented |
| 6. passwords | after each stack's first start, its Password section on the stack page | the stored value is shown and equals the file (graywolf admin, MeshCore repeater dashboard, MeshCom HMAC via Renew) |
| 7. auto-install from the web console | Apps → Auto-install with the defaults (binary where published, else `dev`; no tests, no TX) | every mandatory stack installed and built; the run's total time; **no GTK / X11 / Wayland package installed** (`dpkg -l` count before and after) |
| 8. start and stop of every stack | on the fresh install: `lhpc stack start <stack> --yes` → verify → `lhpc stack stop <stack> --yes`, one stack at a time, then the conflicting pairs | every stack starts and stops with the typed outcomes; a band or TX-mode conflict (meshtastic vs MeshCore on 868, MeshCom vs graywolf on 433) is refused with its reason, nothing half-started; interactive components (chat, voice terminal, Meshtastic CLI, MeshCore CLI) are listed with their command and never started by the controller |

Remote exposure with mTLS and the managed firewall needs the operator's one root step (the copy-paste
`sudo` line the console prints); it is exercised as the last from-zero step when the operator enters
that line, otherwise its contracts rest on the unit tests.

The result lines go into the release's results section below.

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

One table per release. Durations as h:mm:ss; `min avail` is the lowest `MemAvailable`
seen during the row; OOM is "none" or the `dmesg` line.

### 0.2.10 — 2026-09-05/06, all checks pass

Pins moved in this release: Reticulum 1.5.2, MeshCom firmware dev tip 674413c (QEMU overlay 579e463), Meshtastic stable v2.7.26. Run on `lhpc-e293` (Raspberry Pi Zero 2 W, Lite image), 2026-09-05 12:46 to 2026-09-06 01:10 local, on the tagged tree. Two defects found and fixed during the run (the MeshCom GPS drain under QEMU, the known-working composition on a headless box), one operator root step (the managed firewall), two power cycles (one Wi-Fi drop under the QEMU compile, one AP fallback after the fresh install).

Pins are per row (the manifest of the tested head). Two pins moved during the run: rows 12 and 13 ran
meshcom-qemu-raspi at 4bf1183 (before the bounded GPS drain), the meshcom verification in check 15 and
the fresh install ran 579e463; the `dev`-channel installs of checks 14 and 22 took openhop-core at its
branch tip (dae75b4) while the pinned row 8 ran 8cdb04e.
| # | stack | channel | pins under test | clean | install | build | start | usable | min avail | OOM | verdict |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | daemon | binary | daemon 10f4107<br>radiolib 187ef24<br>(binary) | 0:00:06 | 0:00:10 | n/a | 0:00:12 | — | 170 MB | none | **pass** ⁽1⁾ |
| 2 | chat | pinned | chat 10f4107 | 0:00:05 | 0:00:03 | 0:00:14 | rc 1 (0:00:05) | — | 170 MB | none | **pass** ⁽2⁾ |
| 3 | igate | pinned | igate 10f4107 | 0:00:06 | 0:00:06 | 0:00:12 | 0:00:06 | — | 183 MB | none | **pass** |
| 4 | voice | pinned | voice 143b83f | 0:00:06 | 0:00:05 | 0:00:16 | 0:00:08 | — | 180 MB | none | **pass** ⁽4⁾ |
| 5 | kiss | pinned | kiss 3c4461e (v0.5.1) | 0:00:06 | 0:00:04 | 0:00:24 | 0:00:08 | 0:00:00 | 187 MB | none | **pass** |
| 6 | graywolf | fetched | graywolf 0.14.13 (fetched) | 0:00:06 | 0:00:02 | 0:00:36 | 0:00:11 | 0:00:00 | 171 MB | none | **pass** |
| 7 | reticulum | pinned | rns ea98db4 (1.5.2)<br>rns-lora-interface 3fef542<br>nomadnet ad10301<br>lxmd 795fdaa<br>sideband 1402bb6 skipped | 0:00:18 | 0:13:25 | 0:03:05 | 0:00:09 | — | 138 MB | none | **pass** |
| 8 | meshcore | pinned | openhop-core 8cdb04e<br>meshcore-webui 94dcc3d<br>meshcore-cli 568d158 (v1.6.3)<br>openhop-repeater efc5616 | 0:00:11 | 0:02:24 | 0:08:26 | 0:00:39 | 0:00:02 | 47 MB | none | **pass** ⁽8⁾ |
| 9 | meshtastic | binary | meshtastic 54e0d8d (v2.7.26)<br>CLI pip 2.7.11<br>(binary) | 0:00:08 | 0:03:21 | n/a | 0:00:40 | 0:00:16 | 133 MB | none | **pass** |
| 10 | meshtastic | source | meshtastic 54e0d8d (v2.7.26)<br>web 2.6.7<br>(source) | 0:00:07 | 0:02:22 | 2:42:58 | 0:00:34 | 0:00:06 | 24 MB | none | **pass** |
| 11 | daemon | source | daemon 10f4107<br>radiolib 187ef24<br>(source) | 0:00:06 | 0:02:43 | 0:05:00 | 0:00:13 | — | 106 MB | none | **pass** |
| 12 | meshcom | binary | firmware 674413c<br>meshcom-qemu-raspi 4bf1183<br>bridge f018920<br>(binary) | 0:00:15 | 0:00:34 | n/a | 0:07:31 | 0:02:06 | 111 MB | none | **pass** ⁽12⁾ |
| 13 | meshcom | source | firmware 674413c<br>meshcom-qemu-raspi 4bf1183<br>bridge f018920<br>(source) | 0:00:08 | 0:01:56 | 0:45:45 | 0:08:03 | 0:01:06 | 26 MB | none | **pass** ⁽13⁾ |

<!-- rowfoot:begin -->
- ⁽1⁾ row 1: build refused for a binary install, as designed
- ⁽2⁾ row 2: interactive: start ensures the daemon and prints the command
- ⁽4⁾ row 4: first attempt: clean refused by a stale ownership record left by an out-of-band deploy (identity drift, see operations.md); re-run after clearing the record
- ⁽8⁾ row 8: first attempt: same stale-record refusal on openhop-core; re-run after clearing the record
- ⁽12⁾ row 12: first attempt refused by the pins gate as designed: the box still ran the pre-bump checkout (binary 4bf1183 vs pin ef043a9); re-run on main 625f1f3
- ⁽13⁾ row 13: 0:45:45 is the re-run with the QEMU build skipped on its marker; the first attempt built QEMU in 1:13:12 and then lost the network at the firmware clone, so a cold build is the sum of both; the later overlay fix (meshcom-qemu-raspi 579e463, bounded QEMU GPS drain) was compiled from source by the binary builder with its smoke test and runs on the box as the published binary, so this row was not repeated
<!-- rowfoot:end -->

Checks after the rows — every planned check of this release, filled as it is measured:

<!-- checks:begin -->
| # | phase | check | measured | verdict |
|---|---|---|---|---|
| 14 | cross-cutting | auto-install consistency — the CLI path (README step 8): purge all, `lhpc auto-install --yes`, defaults; log creation checked | purge of all 10 stacks 0:01:05; `lhpc auto-install --yes` 0:19:13, rc 0: 10/10 stacks successful, 0 blocked, 0 failed, 0 skipped (GUI deps absent); nothing reads not-built; min avail 50 MB; no OOM; every stack on its default channel (binary for daemon, meshtastic, meshcom; dev for the rest); log creation: all 26 announced job logs (`auto-install-<run>-build-<component>-<step>.log`) exist under logs/, 16 with output, 10 empty for silent steps (venv, install -D) | pass |
| 15 | cross-cutting | known-working confirmation: after each stack's GREEN start the console must OFFER to record the composition (the stack page's known-working offer) — judged for user-friendliness (visible, worded plainly, one click, no tokens/SHAs the operator must understand) — then confirmed via the GUI for EVERY stack; `lhpc status --versions` / profiles/known-working/<stack>.json show the recorded pins | offer shown and confirmed with one click, profile written: kiss (loraham-kiss-tnc, -serial), meshcore (node, webui, cli, openhop repeater), igate; no offer by design for the binary installs (daemon, meshtastic) and the fetched graywolf release (no source composition); chat and voice (interactive, not started by the controller) show no offer; meshcom (binary, no offer by design): its first start failed on a dev-tip regression — the firmware's new per-loop GPS UART drain is unbounded and starved the loop under QEMU's unpaced socket UART (loop gaps 41 s, 100 s, 245 s; net-console deaf); fixed in the QEMU overlay (meshcom-qemu-raspi 579e463, drain bounded per pass), binary republished, verified with three starts on the box: verified in 0:10:08, 0:05:58, 0:06:02, maximum drain time per pass 0.5 s, loop gaps 867 ms and 989 ms (one 59 s gap outside the section during the post-start step); reticulum: first no offer — a real gap (Sideband skipped on Lite blocked the whole composition), fixed in 81551f2 and re-checked live: offer shown (lxmd, nomadnet, rns, rns-lora-interface), confirmed with one click, profile written | pass |
| 16 | cross-cutting | boot restore (power-cycle, N restored / 0 failed) | reboot requested through the console (POST /power/reboot, confirmed) with daemon, kiss, graywolf and meshcore running: ping back 0:03:01 and console up 0:03:05 after the request; boot-restore state done, restored kiss, graywolf, meshcore and the daemon, 0 failed, 0 skipped, 0 issues; all four running afterwards | pass |
| 17 | cross-cutting | web console sweep (Dashboard, Apps rows, Settings, no traceback) | 18 console pages fetched over the socket (Dashboard, Apps, GPS, hardware, auto-install, boot-restore, dependencies, controller logs, every stack body): 0 errors, 0 tracebacks in the console log, sweep 0:00:30 | pass |
| 18 | cross-cutting | pins vs binaries (`lhpc status --versions`, three binary stacks) | after the auto-install: loraham-daemon binary, radiolib binary, meshtastic binary, meshcom-bridge/qemu/firmware binary (meshcom-gps-relay source, match) — the pins gate accepted all three published binaries against the manifest | pass |
| 19 | from-zero | `uninstall.sh --purge` (stacks stopped and verified, runtime root gone) | after the root-owned firewall reset (the one step this run could not do itself): `uninstall.sh --purge --yes` rc 0 in 0:00:35 — stacks stopped and verified, runtime root gone, 0 managed units left, CLI link gone. First attempt had been refused (fail-closed) while the managed firewall integration was installed | pass |
| 20 | from-zero | documented install happy path line by line (README → field-notes checklist → `install.sh` → console); docs corrected where a line fails | README step 2 `bootstrap-deps.sh --dry-run` (no root): rc 0, 0:00:37; step 3 not repeated (root; deps present from the image); step 4 `curl … install.sh | bash`: rc 0 in 0:01:45, `lhpc --version` = 0.2.10, console 200 on loopback right after install, units lhpc-web + lhpc-nginx; step 5 reboot through the console: accepted (302), box came back on its fallback AP `lhpc-e293` | pass |
| 21 | from-zero | Wi-Fi join via the Network panel from the box's fallback AP (the genuine flow), console back on the joined network | the fresh install came up on its fallback AP `lhpc-e293`; this PC joined the AP and drove the console's Network panel: stage 1 (confirm page) 200, stage 2 confirmed with the password 302 → the AP vanished after 5 s and the box answered on the home network after 12 s, `Suche...` active on wlan0; the console's own check on the joined network follows in part 2 | pass |
| 22 | from-zero | web-console auto-install with defaults; GTK/X11/Wayland package count unchanged | Apps → Auto-install with the defaults (all ten stacks, binary where published else dev, no tests, no TX) posted through the console: run completed in 0:18:44 (21:47:00Z → 22:05:44Z): 10/10 stacks successful, 0 blocked, 0 failed, 0 skipped (GUI deps absent); graphical packages (gtk/x11/wayland/xorg/mesa) 11 before and 11 after, no new package installed; 20 components read binary or match afterwards | pass |
| 23 | from-zero | first start on the FRESH install with the global callsign never set (one licensed stack, nothing started before): typed identity refusal, CLI hint, Settings row highlighted | on the fresh install, nothing started before, global callsign unset, meshcom's own callsign empty: `lhpc stack start meshcom --yes` refused typed — "Cannot start 'meshcom': a callsign is required to start 'meshcom' — set 'mc_callsign' (or the global operator callsign)" with the hint `lhpc config meshcom mc_callsign YOURCALL-99`; web Start from the Apps page: 302 to `/stacks?cfg=meshcom&bad=c_mc_callsign#stack-settings-meshcom` — the stack's Settings opened with the callsign row highlighted, nothing started | pass |
| 24 | from-zero | `lhpc config operator --callsign DJ0CHE`, then first start of the remaining stacks (fresh box, saved defaults) | `lhpc config operator --callsign DJ0CHE` saved; node names set; first starts: daemon 0:00:15, kiss 0:00:11, graywolf 0:00:17, meshcore (chat+repeater) 0:00:17, meshtastic 0:00:37, meshcom 0:06:07 (QEMU boot) — all running; chat: interactive, the start ensures the daemon and prints the command (rc 1 by design); voice: started while kiss and graywolf held 433 → refused typed "graywolf, kiss must be stopped first" (a band conflict, the interactive command is otherwise printed); igate 0:00:08, reticulum 0:00:12 — every stack's first start on the fresh install succeeded | pass |
| 25 | from-zero | password check per stack after its first start: the Password section of the web GUI shows the stored value (graywolf, MeshCore repeater dashboard, MeshCom HMAC) and it matches the file | graywolf: the Password section shows the stored value in its copy box and it equals state/graywolf/graywolf-admin.txt; MeshCore repeater dashboard: shown and equals config/secrets/openhop_repeater_admin.txt; MeshCom HMAC: on the happy path (prebuilt binary) the console's HMAC page says plainly "not available — prebuilt binary whose firmware has NO mesh password (open auth) — install it from source to manage the password", and the Password section shows the HMAC row as disabled; n/a by design on a binary install | pass (HMAC n/a on the binary install, by design) |
| 26 | from-zero | start/stop behaviour of EVERY stack on the fresh install: start → verify → stop per stack; a band/TX-mode conflict is refused with the typed reason (meshtastic vs MeshCore on 868, MeshCom vs graywolf on 433); interactive components (chat, voice-cli, meshtastic-cli, meshcore-cli) are listed with their command, never started by the controller | start → verify → stop per stack on the fresh install, all clean: daemon, igate 0:00:21, kiss 0:00:23, graywolf 0:00:29, meshcore 0:00:30, meshtastic 0:00:47, reticulum 0:00:21; interactive components listed on the Dashboard with their command ("Interactive — run local", e.g. the MeshCore CLI), `lhpc stack start chat` answers "interactive — the daemon is ensured, then run it yourself in a terminal"; conflict pairs, each refused typed and nothing half-started: meshtastic while MeshCore holds 868 → "radio 868 MHz is held by running stack 'meshcore'", "Cannot run 'meshtastic': daemon, meshcore must be stopped first"; meshcom while kiss and graywolf hold 433 → "radio 433 MHz is held by running stack 'kiss' / 'graywolf'", "Cannot run 'meshcom': graywolf, kiss must be stopped first" (one stack per daemon band); meshtastic while reticulum owns its radio → "spi.bus.0.unlocked is held by running stack 'reticulum'", "Cannot run 'meshtastic': reticulum must be stopped first"; the daemon starts on the other band beside reticulum (the documented coexistence) | pass |
| 27 | from-zero | docs/ssh-tunnel.md: every tunnel command live-verified from this PC against the running stacks (console 8443, graywolf 8080, MeshCore 8788/8000, MeshCom 18083/12323, meshtastic 4403, kiss 8001, reticulum 4242) | from this PC with the doc's `ssh -N -L` commands verbatim (host swapped): console 8443 → 200, graywolf 8080 → 200, MeshCore repeater dashboard 8000 → 200, MeshCore companion 5000 → open, KISS 8001 → open, MeshCom web 18083 → 200 (after its boot), MeshCom net-console 12323 → open, Reticulum 4242 → open, MeshCore web UI 8788 → 200 once its optional component is started (the doc now says so); meshtastic 4403/9443 were not reached in this run because my test started the node while the daemon still held 868 (refused, correctly) — the node's API itself was verified in rows 9 and 10 (`lhpc meshtastic --info`); the doc notes the port delay after start; in check 29 the node's ports 4403 and 9443 were present after a clean start, confirming the documented port table | pass (9 of 11 verified live; 2 not reached by a test-setup error) |
| 28 | from-zero | remote exposure with mTLS + managed firewall (the operator's one root step): console allowlisted for the joined subnet by the Network panel, `lhpc firewall --script` rendered, `sudo bash firewall-apply.sh` + `sudo systemctl start lhpc-firewall-check.service` entered by the operator, `lhpc firewall` verified, from another machine: https://<box>:8443 refused without a client certificate and 200 with the issued one | the Network-panel join had already allowlisted 192.168.178.0/24 in mode local-open-remote-auth (remote listener on 0.0.0.0:8443); `lhpc firewall --script` rendered the apply script, the operator entered `sudo bash firewall-apply.sh` + `sudo systemctl start lhpc-firewall-check.service` ("applied and live-verified"); `lhpc firewall`: Active, Config ✓ Boot ✓ Live ✓; `lhpc webserver verify`: verified; from this PC: https://192.168.178.106:8443/ without a client certificate 403, with the issued certificate (`cert issue matrix-pc2`, one-time passphrase, `cert export` .p12) 200 = the Dashboard; a stack port (4403) is unreachable from the LAN | pass |
| 29 | from-zero | stack WebGUIs exposed through the Webserver panel's common policy (one policy for all stack WebGUIs, mTLS), then each proxied UI verified from another machine with the issued client certificate | Stacks WebGUIs common policy saved through the console (lan, https, local-open-remote-auth, 192.168.178.0/24, confirm phrase enable-remote): 5 pages, ports assigned 8444 graywolf, 8445 meshcom, 8446 MeshCore web UI, 8447 meshtastic, 8448 MeshCore repeater dashboard; the webserver apply was gated ("Firewall changes pending — apply the firewall first"), the operator entered the re-rendered apply script ("applied and live-verified"), then `lhpc webserver apply` + `verify` passed and nginx listens on 8443–8448; from this PC, each page 403 without the client certificate and 200 with it: graywolf 8444, MeshCom 8445 (after its boot), MeshCore web UI 8446, meshtastic 8447 (node ports 4403/9443 present), MeshCore repeater dashboard 8448; the native MeshCore port 8788 is unreachable from the LAN (firewalled); Chrome on this PC opens the console and the stack UIs with the installed certificate | pass |
| 30 | host tests | daemon: `lhpc test daemon --yes` (+ `--tx` if offered) | host test refused as designed: installed from the published binary, host tests need the source channel (rc 1, 0:00:02); TX test (one frame per band): first attempt 433 did not confirm (TXOK 0→0, 868 ok), retry PASSED on both bands in 0:00:08 (TXOK 0→1 each) — the 433 miss matches the CAD-busy condition seen at this site in the silicon test | pass (host test n/a on the binary install; TX pass) |
| 31 | host tests | chat | no host test declared (`[host-test] loraham-chat: (no host test)`, 0:00:03); TX test (one frame per band, daemon up) PASSED in 0:00:04, no OOM | n/a |
| 32 | host tests | igate | no host test declared (0:00:02); TX test (one frame per band, daemon up) PASSED in 0:00:05, no OOM | n/a |
| 33 | host tests | voice | no host test declared (0:00:03); TX test (one frame per band, daemon up) PASSED in 0:00:07, no OOM | n/a |
| 34 | host tests | kiss | host test PASSED in 0:00:20, lowest 197 MB, no OOM; TX test (one frame per band, daemon up) PASSED in 0:00:07, no OOM | pass |
| 35 | host tests | graywolf | no host test declared (0:00:03); TX test (one frame per band, daemon up) PASSED in 0:00:06, no OOM | n/a |
| 36 | host tests | reticulum | no host test declared (0:00:02); TX test: not daemon-TX-testable by design (the stack drives its own radio; verify TX from its own app/logs) — the typed refusal says exactly that | n/a by design |
| 37 | host tests | meshcore | host test PASSED in 0:02:37, lowest 132 MB, no OOM; TX test (one frame per band, daemon up) PASSED in 0:00:05, no OOM | pass |
| 38 | host tests | meshtastic | host test refused as designed: binary install (rc 1, 0:00:03); TX test: not daemon-TX-testable by design (the stack drives its own radio; verify TX from its own app/logs) — the typed refusal says exactly that | n/a by design |
| 39 | host tests | meshcom | host test refused as designed: binary install (rc 1, 0:00:03); TX test with the daemon up: first attempt 433 did not confirm (TXOK 1→1), retry PASSED in 0:00:06 (TXOK 1→2) | pass (host test n/a on the binary install; TX pass) |
| 40 | release | final commit "0.2.10" (results table, docs) + tag v0.2.10 on main | main rewritten to four commits over v0.2.9 (docs matrix 8921ef5 → pins 805f41c → known-working fix 82c63db → 0.2.10 f42dc45), tree identical to the tested state; tag v0.2.10 on f42dc45 pushed 2026-09-06 01:20 | pass |
| 41 | release | CI, testlab, demo-pages green on the tag | on the tagged head f42dc45: CI success (run 33997957395), testlab success (33997957492); demo-pages did not trigger for the docs-only final commit and was green on the last code commit | pass |
| 42 | release | images v0.2.10: milestone tag, both variants built, assets published | loraham-images milestone adcee81 tagged v0.2.10 after the binaries were live: lint, precheck, build (lite), build (desktop), publish-tag all success; assets loraham-lhpc-desktop.img.xz 1913 MiB (135 MiB under the 2 GiB limit), loraham-lhpc-lite.img.xz 892 MiB, components/packages/provenance per variant, SHA256SUMS, signature | pass |
<!-- checks:end -->

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
