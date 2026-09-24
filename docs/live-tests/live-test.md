# Release matrix, 0.9.0 — box E (`lhpc-e293`), 2026-09-23

Run on `59602ed` (the 0.9.0 release commit: the openHop plugin manager as part of the MeshCore
repeater; pins unchanged since v0.8.3). Box **E** = Pi Zero 2 W, Lite image, Uputronics dual
(433 + 868), **Wi-Fi only**, real u-blox on gpsd. Console left running throughout (`/healthz`
reported `0.9.0` before the first row).

Evidence rule: the controller's own typed outcome plus the stack's own state. Log greps are not
evidence. The runner (`~/matrix-0.9.0/run.log`, `rows.md` on the box) drove every row with the
matrix's six-step loop and recorded the wrapper's times. The box's stack configs (node names,
callsign) were restored from a backup after every purge, so the rows ran under the operator's
identities; the MeshCore identity itself is preserved by `clean --purge` by design.

**Fast lane, by the maintainer's standing waiver ("no heavy compile jobs on the box", 2026-09-19,
in force for this run):** rows 9, 10 and 12 — the from-source meshtastic, daemon and MeshCom
builds — are *not re-run*. Rows 9 and 10 stay inside the fast lane: meshtastic `54e0d8d`, daemon
`e8e748e` + RadioLib `187ef24` are unchanged since the artifacts were built at v0.7.0 and were
last compiled in the 0.6.0 run on box B (this file's git history). **Row 12 is outside the fast
lane's letter**: the MeshCom firmware pin moved at 0.8.3 (`6edc749` → `80b85a5a2`, by the release
bot, whose lane built and proved the artifact — build 35778131763, release-verify green) and the
compile was not repeated here because of the waiver; row 11 proves that artifact on the box, and
the box's MeshCom binary was actually behind the pin before this run (`built_from 6edc749`,
`pin 80b85a5a2` — the 0.8.3 binary had never been installed on E) and is at the pin afterwards.

**Deviations, all recorded here rather than hidden:**

1. **From-zero reinstall: not run** (ToDo R3 stays open). Root was available this time, but the
   box is Wi-Fi-only and `uninstall.sh --purge` drops the preferred-network record and the box's
   PKI and identities; an unattended run with nobody at the bench could leave the box on its
   fallback AP with the operator's certificates regenerated. It waits for a bench session with
   the operator present.
2. **Host tests for daemon, chat and voice: not run** — their lane compiles the daemon's test
   binaries, which the waiver excludes. kiss, graywolf, reticulum and meshcore ran; meshtastic
   and meshcom are refused on the binary channel as designed.
3. **Bot watch-only before the minor** (the rule in `maintenance.md`): one pin had moved
   upstream, MeshCom-Firmware `80b85a5a2` → `dc1a012c` (KISS mode v2). The QEMU headless overlay
   patch of `meshcom-qemu-raspi` does not apply at that tip (`git apply --check` fails on
   `src/configuration_global.h` and `src/udp_functions.cpp`), so the pin is held and the release
   says so in its changelog; the overlay needs maintenance before the bot can move it (ToDo R8).
4. **Web console checks** were run by hand after the runner (its own loop sent the requests with
   the host name `lhpc`, which the console rightly refuses with 400 — it answers only to the names
   it serves); the hand run used the box's served address over the same nginx socket.

## Rows

| # | stack | channel | install | build | start | evidence |
|---|---|---|---|---|---|---|
| 1 | `daemon` | binary | 6s | *refused* (binary) | 14s | `lhpc status daemon`: running; `lhpc daemon 433` / `868` both `Radio: READY, TX mode: MANAGED`; build *refused* (binary, as designed) |
| 2 | `chat` | pinned | 3s | 5s | 6s (refused) | typed `[manual] loraham-chat is interactive — the daemon is ensured, then run it yourself in a terminal` — the interactive contract; source `match` after the run |
| 3 | `voice` | pinned | 5s | 4s | 8s | `loraham-voice-cli` built and started; GTK variant `not-applicable` on Lite; both `match` |
| 4 | `kiss` | pinned | 5s | 15s | 8s | verified; TCP `127.0.0.1:8001` open; `known-working` recorded |
| 5 | `graywolf` | pinned | 2s | 8s | 12s | verified; web UI `127.0.0.1:8080` → 200; KISS client held |
| 6 | `reticulum` | pinned | 141s | 244s | 13s | rns on 868 with the ready marker; nomadnet, lxmd, meshchat built (Sideband skipped on Lite); `known-working` recorded. MeshChat is an *optional* component and is not part of the stack's start plan: started by name afterwards (`lhpc stack start meshchat --yes`, 14 s, verified on its endpoint) its UI `127.0.0.1:8790` → 200 within 8 s |
| 7 | `meshcore` | pinned | 47s | 430s | 33s | node + webui verified on 868 (webui `:8788` → 200, dashboard `:8000` → 200), openhop repeater source `match`; **exactly one plugin manager running beside the repeater and the same-boot marker present; after the stop no manager and the marker cleared** (the 0.9.0 feature, on the release commit); `known-working` recorded |
| 8 | `meshtastic` | binary | 107s | *refused* (binary) | 38s | verified on 868 with the box's identity restored from the backup: TCP `:4403` open, `lhpc meshtastic --info` → `Owner: e293` |
| 9 | `meshtastic` | pinned (from source) | — | *not re-run* | — | maintainer's waiver; pin `54e0d8d` unchanged since the artifact was built at v0.7.0 — row 8 proves that artifact |
| 10 | `daemon` | pinned (from source) | — | *not re-run* | — | same waiver; pin `e8e748e` (1.1.1) + RadioLib `187ef24` unchanged since v0.7.0 — row 1 proves that artifact |
| 11 | `meshcom` | binary | 20s | *refused* (binary) | 829s | bridge + gps feed verified at once, the QEMU node's ready endpoint `:12323` and its required post-start after the emulated firmware's boot (the plan's own note: 6–14 min on a Zero 2 W; 0.8.0 measured 360 s); web UI `:18083` → 200 right after; the artifact installed is the 0.8.3 one (labelled `meshcom-firmware built_from 80b85a5a2 = pin`; the firmware inside is upstream 674413c — the QEMU build fetches that hardcoded ref, not the pin; corrected in 0.9.1, open item R8) — before this row the box still ran the v0.7.0 artifact |
| 12 | `meshcom` | pinned (from source) | — | *not re-run* | — | see the fast-lane note above: the firmware pin moved at 0.8.3 (bot lane proved the artifact); the compile is excluded by the waiver; row 11 proves that artifact |

Memory stayed between 160 and 263 MB available (`free -m`) across the rows; no OOM line in `dmesg`
after the run. The box was left as found: MeshCore chat+repeater running with its one plugin manager.

## Cross-cutting checks

| check | result |
|---|---|
| **auto-install consistency** | every stack purged, then `lhpc auto-install --yes`: 16 min 12 s (972 s); `lhpc status --versions` afterwards: 16 source components `match`, 0 `differs`, 6 `binary`, 1 `missing` (Sideband: skipped on Lite by design), nothing "not built"; daemon, meshtastic and meshcom from the published binaries, the light stacks built from source |
| **`dev` selector spot-check (kiss)** | `install --source dev` resolved the branch tip and built; kiss started under the daemon; reinstalled on the default channel: `match` (the tip is the pin, `v0.5.1-5-g33c1d22`) |
| **known-working** | recorded for kiss, reticulum and meshcore after their green starts (`lhpc known-working <stack>`) |
| **boot restore** | one reboot through the console's own Reboot action with MeshCore (chat+repeater) running: see below |
| **web console** | by hand with the served address over the console's socket: `/`, `/stacks`, `/auto-install`, `/dependencies`, `/controller/logs`, `/healthz` → 200; every `/stacks/<stack>` → 302 to its anchor and `/stacks/<stack>/body` → 200; `/healthz` reports `0.9.0`, 9 stacks; **0 tracebacks** in the console journal across the run |
| **pins vs binaries** | daemon `built_from e8e748e = pin`, RadioLib `187ef24 = pin`, meshtastic `54e0d8d = pin`, MeshCom bridge/qemu/firmware `7c86c96` / `b322a88` / `80b85a5a2` = pins |
| **host tests** | kiss 21 s rc 0 · graywolf 3 s rc 0 (nothing to do) · reticulum 2 s rc 0 (nothing to do) · meshcore 156 s rc 0 · meshtastic / meshcom *refused on the binary channel* (as designed) · daemon / chat / voice *not run* (waiver) |
| **from-zero reinstall** | **not run** — deviation 1 above (ToDo R3) |

## Boot restore

One reboot through the console's own Reboot action (POST `/power/reboot`, CSRF-checked, over the
console's socket; logind, no root) at 23:51 UTC with MeshCore chat+repeater running on 868. The box
came back on its home Wi-Fi at 23:51:43, `lhpc-web` active, `/healthz` → `{"stacks":9,"status":"ok","version":"0.9.0"}`;
`state/boot-restore.json`: `done`, items `meshcore` **succeeded** and `daemon-reconcile` (868)
**succeeded**, nothing skipped, no issues. MeshCore came back with its GPS feed, node and web UI
verified, and the plugin manager's own log shows the marker protocol crossing the boot as designed:
`Plugin-manager marker from an earlier boot found; replacing it` — then one manager started (pid
1526, a child of the host, same process group) and the marker now carries the new boot id.
