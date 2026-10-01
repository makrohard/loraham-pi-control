# Release test matrix

Every stack **purged, installed, built, started and verified on the box**, one at a time, with
install, build and start times recorded and memory watched during the heavy compiles: proof that a
release **installs and comes up** from nothing on the reference box (the two heavy from-source
compiles run on the [second box](#bench)).

**When:** before the tag of a minor release; which release runs what:
[maintenance](maintenance.md#branches-and-releases). The daemon, RadioLib and the shared chat
source are moved by hand and proved here. Results go into the run report and, in the first docs
commit after the tag, replace the section in `docs/live-tests/live-test.md` (the release commit
is not amended: binaries are built from it).

Evidence is the controller's typed outcome plus the stack's own state (`lhpc status`, the node's
info, an HTTP answer, `rnstatus` counters), never a log grep.

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
| Second box | a Raspberry Pi 5 on the same OS release (aarch64): only the heavy from-source rows 9 and 12. The Zero 2 W never compiles a heavy stack; it runs the binary rows 8 and 11 and builds row 10 (daemon) itself |
| Radio | LoRaHAM daemon serving 433 and 868; record `lhpc hardware` at the start of the run |
| Channels | `binary` (daemon, meshtastic, meshcom), `pinned` and `dev` ([selections](provenance.md#selections)); see [Coverage](#coverage) |
| Console | running for the light stacks; **stopped for the heavy compiles** (`systemctl --user stop lhpc-web lhpc-nginx`; [why](maintenance.md#running-on-a-pi)) |
| Radio budget | one stack per band at a time: 433 belongs to the daemon chain (kiss, graywolf, meshcom), 868 to one of meshtastic / MeshCore / Reticulum. Stop the previous owner before starting the next |

## Procedure per stack

Every row runs this loop in a tmux on the box, timed with the wrapper. Nothing is skipped
outside the [fast lane](#fast-lane).

```bash
t() { local s=$(date +%s); "$@"; echo "[timer] $* -> $(( $(date +%s) - s )) s"; }

t lhpc clean <stack> --purge --yes            # 1. purge: sources, config, state, identities, logs
t lhpc install <stack> --source <chan> --yes  # 2. install on the channel under test
t lhpc build <stack> --yes                    # 3. build (no-op for pure binary / fetched stacks)
t lhpc stack start <stack> --yes              # 4. start on the band the radio budget allows
lhpc status <stack>                           # 5. verify: the row's evidence column
t lhpc stack stop <stack> --yes               # 6. stop; `lhpc status` shows nothing left running
```

- **Set the node identity after step 2.** `clean --purge` removes `node_name` / `node_short`
  (meshtastic, meshcore) and `mc_callsign` (meshcom), and a start without identity is refused.
- **No GPS receiver:** set `use_gps off` on meshtastic, meshcore and meshcom; their main
  component depends on the `*-gps` feed, which cannot verify without a receiver
  (`GPS feed never reached its source`, then `[blocked] … depends on …-gps`). The bench box has
  a u-blox.
- **A binary row's build is REFUSED** ([operations](operations.md#install-channels)); record the
  typed refusal as its build result.
- **Times:** `install` = step 2, `build` = step 3, `start` = step 4 (returns once components are
  verified). Where a stack is *usable* later than *verified* (MeshCom:
  [meshcom](stacks/meshcom.md#notes)), record both.
- **Memory / OOM** during every build and every heavy start: `vmstat -n 10 > ~/vm-<stack>.log &`
  alongside, `free -m` before and after, `dmesg -T | grep -iE 'oom|killed process'` afterwards.
  Record any OOM line, any killed child's exit code and the minimum `MemAvailable`.
- **Box left clean:** after the last row `lhpc status` shows nothing running and the runtime holds
  no orphan `state/` markers.

## Matrix

Order: light stacks first, heavy compiles last, MeshCom from source very last. A stack sharing
an earlier row's source (chat and voice share the daemon's) is still purged and reinstalled on
its own.

| # | stack | channel | build | start | evidence |
|---|---|---|---|---|---|
| 1 | `daemon` | binary | refused (no source tree) | both bands | `lhpc status daemon`: READY on 433 and 868; `lhpc daemon 433` answers |
| 2 | `chat` | pinned | daemon sources | interactive | the printed command runs in a terminal and exits cleanly |
| 3 | `voice` | pinned | `loraham-voice-cli` (GTK variant skipped on Lite) | interactive | the terminal variant's printed command runs; GTK reported skipped, not failed. On a Desktop image it is the reverse: the GTK voice starts verified and the terminal variant is skipped |
| 4 | `kiss` | pinned | `loraham-kiss-tnc` | 433 | verified; TCP `127.0.0.1:8001` answers |
| 5 | `graywolf` | fetched release | — | 433 (needs kiss) | verified; web UI `127.0.0.1:8080` answers; the KISS client is held |
| 6 | `reticulum` | pinned | rns, nomadnet, lxmd, meshchat (sideband skipped on Lite) | the free band | `rnstatus` lists the LoRa interface with `Mode: Internal`; the ready marker present; MeshChat's UI answers 200 on `127.0.0.1:8790`, and the generated config is `0400`. The stack's own full matrix is the dated report `docs/live-tests/reticulum-test-2026-09-12.md` |
| 7 | `meshcore` | pinned | node, webui, openhop repeater source | 868, mode chat+repeater (set `repeater_name` first); the optional web UI started with `lhpc stack start meshcore-webui` | node and repeater verified; web UI `:8788` and dashboard `:8000` answer; `meshcore-cli` listed on the Dashboard |
| 8 | `meshtastic` | binary | refused (no source tree) | 868 (MeshCore stopped) | verified; `lhpc meshtastic --info` returns the node; `meshtastic-cli` listed |
| 9 | `meshtastic` | pinned (from source), **on the second box** | meshtasticd | 868 | as row 8; build time and memory recorded |
| 10 | `daemon` | pinned (from source) | RadioLib + daemon | both bands | as row 1; build time and memory recorded |
| 11 | `meshcom` | binary | refused (no source tree) | 433 (graywolf/kiss stopped) | verified; web UI `:18083` answers 200 once the node has booted; callsign switches from the placeholder |
| 12 | `meshcom` | pinned (from source), **on the second box** | QEMU, firmware, bridge | 433 | as row 11 — the longest row by far (build times: [maintenance](maintenance.md#running-on-a-pi)); memory watched throughout |

Rows 9, 10 and 12: console stopped, `vmstat` running, `dmesg` checked after.

Row 7's client commands are typed in the client whose command `lhpc stack start meshcore-cli`
prints ([one connection at a time](stacks/meshcore.md#command-line-client)); the web UI keeps
running. "no_event_received" means no reply within the client's timeout (15 s in meshcore
2.3.14), not that the frame was unsent: read `logs/rf-meshcore.log`.

### Coverage

Every stack on every channel it can be installed on; the cell names the proving row. `dev` is
covered once, by the cross-cutting spot-check.

| stack | binary | pinned (source, default) | dev (explicit) |
|---|---|---|---|
| daemon | row 1 | row 10 | dev spot-check |
| chat | — | row 2 (default) | dev spot-check |
| voice | — | row 3 (default) | dev spot-check |
| kiss | — | row 4 (default) | dev spot-check |
| graywolf | — (fetched release) | row 5 (default) | — (one pinned release) |
| reticulum | — | row 6 (default) | dev spot-check |
| meshcore | — | row 7 (default) | dev spot-check |
| meshtastic | row 8 (default) | row 9 | dev spot-check |
| meshcom | row 11 (default) | row 12 | dev spot-check |

No empty cell, unless a [fast lane](#fast-lane) waiver applies.

## Fast lane

A release whose heavy-stack pins are unchanged since the last measured run may skip the
from-source rows 9, 10 and 12 **on the maintainer's explicit waiver**; the binary rows (1, 8, 11)
still prove the shipped artifacts. Everything else runs. A skipped row is recorded as *not
re-run*, with a footnote naming the measuring run (it may live only in this file's git history)
and the waiver's date, and the pins column showing the same pin. A changed pin, toolchain or
builder image takes the row out of the fast lane.

## Cross-cutting checks

After the per-stack rows, with every stack installed and built:

| check | how | evidence |
|---|---|---|
| **auto-install consistency (CLI path)** | purge every stack, then `lhpc auto-install --yes` (what the image builder and README step 9 run); every log file the run announces (`tail -f …`) must exist afterwards | every stack installed on its default channel and built; `lhpc status --versions` reads `match` for every source component and is recorded as-is; the image's `components-*.txt` shows the same lines; nothing reads "not built"; total time recorded |
| **`dev` selector spot-check** | on one light stack with no binary (kiss): `lhpc clean kiss --purge --yes`, `lhpc install kiss --source dev --yes`, build, start, then reinstall it on the default channel | the install reports the resolved BRANCH TIP and the checkout is at it (`--versions` reads `differs` or, when the tip is the pin, `match`); the stack starts; after the reinstall it reads `match` |
| **known-working** | after each green start, confirm the stack page's offer to record the composition (CLI: `lhpc known-working <stack>`) for every source-built stack with non-interactive components; binary installs and graywolf show no offer, chat and voice refuse it | the offer is visible and plainly worded (one click, no commit ids); `profiles/known-working/<stack>.json` and `lhpc status --versions` show the run-proven pins ([maintenance](maintenance.md#moving-a-pin)) |
| **boot restore** | start a set of stacks (the release runs nothing by default), then power-cycle once | `N restored, 0 failed` for that set, console reachable |
| **web console** | Dashboard, Apps rows, Settings of every stack after the run | no traceback in the console log; every row opens |
| **high-power switch** | on a box whose 433 radio is a bare SX127x module (Uputronics): the rows of the current high-power live test ([live-tests](live-tests/)); on the LoRaHAM board the refusals and the switch state only — the approved plan does not cover +20 dBm TX on its RFM98PW | refusals without the flag, the flag on the switched band only, one frame at 20 with `TXERR` 0, the start gate, revocation by restart |
| **pins vs binaries** | `lhpc status --versions` on the three binary stacks | the installed binary's components equal the manifest pins |
| **host tests: the last step, after the from-zero reinstall, before the tag** | `lhpc test daemon --yes` first, then `lhpc test <stack> --yes` for every other stack, one at a time, timed (binary installs refuse host tests: record them as skipped); where offered, `lhpc test <stack> --tx --yes` too (real RF: only with the operator's go and antennas or dummy loads in place) | outcome and duration per stack, for the record only; memory pressure or an OOM kill on the Zero 2 W is not a release blocker |

## From-zero reinstall

After the rows, the controller is reinstalled from nothing on the same box, timed, and driven as
a new operator would, happy path only: the heavy stacks from the published binaries, the light
stacks from source. No heavy compile is repeated (the rows above timed them, and a Zero 2 W's
Wi-Fi can drop under a long compile):

| step | how | evidence |
|---|---|---|
| 1. uninstall + wipe | `sudo bash config/files/firewall/firewall-reset.sh` first when the managed firewall is installed (every uninstall refuses while it is), then `bash uninstall.sh --purge` from the old checkout | stacks stopped and verified, runtime root gone, no managed unit left |
| 2. install | the documented happy path, line by line (README → `install.sh` → console); a doc line that does not work as written is corrected in the same release | `lhpc --version`, console answers; the commands run are the evidence |
| 3. network | the Apps page's Network panel: the box joins (or re-joins) the operator's Wi-Fi; the AP stays the fallback | console reachable on the joined network |
| 4. first start, global callsign unset | one licensed stack started before any identity is set | the typed refusal (CLI hint `lhpc config operator --callsign`; in the console the start lands on that stack's Settings with its own callsign row highlighted — the global Base callsign row is not marked); nothing started |
| 5. identity, then the rest | `lhpc config operator --callsign <CALL>`, then the remaining stacks' first start with the saved defaults | every stack starts; Meshtastic / MeshCore still need their own node names, as documented |
| 6. passwords | after each stack's first start, its Password section on the stack page | the stored value is shown and equals the file (graywolf admin, MeshCore repeater dashboard, MeshCom HMAC via Renew) |
| 7. auto-install from the web console | Apps → Auto-install with the defaults (binary where published, else `pinned`; no tests, no TX) | every mandatory stack installed and built; the run's total time; **no GTK / X11 / Wayland package installed** (`dpkg -l` count before and after) |
| 8. start and stop of every stack | on the fresh install: `lhpc stack start <stack> --yes` → verify → `lhpc stack stop <stack> --yes`, one stack at a time, then the conflicting pairs | every stack starts and stops with the typed outcomes; a band or TX-mode conflict (meshtastic vs MeshCore on 868, MeshCom vs graywolf on 433) is refused with its reason, nothing half-started; interactive components (chat, the voice terminal variant where the GTK app cannot run, Meshtastic CLI, MeshCore CLI) are listed with their command and never started by the controller |

Remote exposure with mTLS and the managed firewall needs the operator's printed `sudo` line
([firewall](firewall.md), [webserver](webserver.md)); it is the last from-zero step when the
operator enters it, otherwise its contracts rest on the unit tests.

## Refused as designed

Pinned by unit tests, not re-run row by row ([model](architecture.md#radios-bands-and-resource-claims)).
Note the first three when the live run hits them.

| refusal | pinned by |
|---|---|
| a second owner of a band (meshtastic while MeshCore or Reticulum holds 868; kiss while Reticulum holds 433) | `tests/core/test_run_order.py`, `tests/stacks/test_reticulum_stack.py` |
| meshtastic with Reticulum on the bus (`spi.bus.0.unlocked`) | `tests/stacks/test_reticulum_stack.py` |
| a start with a missing identity (Meshtastic / MeshCore node name, MeshCom callsign) — plan and apply, CLI and web | `tests/core/test_identity.py` |
| a source update while a consumer runs; a checkout with upstream changes is not overwritten | `tests/core/test_uninstall_safety.py`, `tests/install/test_source.py` |
| a start against a build receipt that no longer matches its sources | `tests/stacks/test_reticulum_stack.py` |
| a dependent component when its dependency failed to start | `tests/stacks/test_reticulum_stack.py` |
| changing the GPS source, or a stack's `use_gps`, while a consumer runs | `tests/stacks/test_gps.py` |
