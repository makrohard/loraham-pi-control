# Release matrix, 0.8.0 — box E (`lhpc-e293`), 2026-09-19

Run on `1921ae1` (the 0.8.0 release commit: GPS Monitor; pins unchanged since v0.7.0). Box **E** =
Pi Zero 2 W, Lite image, Uputronics dual (433 + 868), **Wi-Fi only** (the USB Ethernet adapter was
removed earlier that day — it was jamming the GPS receiver). Console left running throughout.

Evidence rule: the controller's own typed outcome plus the stack's own state. Log greps are not
evidence. The runner (`~/matrix-0.8.0/run.log`, `rows.md` on the box) drove every row with the
matrix's six-step loop and recorded the wrapper's times.

**Fast lane, by the maintainer's explicit waiver of 2026-09-19 ("no heavy compile jobs on the
box"):** rows 9, 10 and 12 — the from-source meshtastic, daemon and MeshCom builds — are *not
re-run*. Their pins are unchanged since the three binary artifacts were built at v0.7.0
(`cd7e1f3`): meshtastic `54e0d8d`, daemon `e8e748e` + RadioLib `187ef24`, MeshCom `7c86c96` /
`6edc749` / `b322a88`; the binary rows 1, 8 and 11 prove those artifacts. The compiles were last
measured in the 0.6.0 run on box B (this file's git history).

**Deviations, all recorded here rather than hidden:**

1. **From-zero reinstall: not run.** It starts with `firewall-reset.sh` under `sudo` (the managed
   firewall is installed on this box), and this run had no root. The from-zero section is therefore
   *blocked*, not passed.
2. **Host tests for daemon, chat and voice: not run** — their lane compiles the daemon's test
   binaries, which the waiver excludes. kiss, graywolf, reticulum and meshcore ran; meshtastic and
   meshcom are refused on the binary channel as designed.
3. **Identities.** The box carried no Meshtastic, MeshCore or MeshCom identity. The runner set
   `node_name`/`node_short e293` (Meshtastic), `node_name e293` (MeshCore) and the MeshCom callsign
   from the box's operator callsign with SSID `-15`; they remain in the box's config after the run.
4. **Restored settings.** The box's daemon config carried `hipower_433 = on` and `POWER 20` from the
   unreleased high-power branch; the released daemon refuses `POWER 20` on an SX127x, so the restore
   after each purge dropped both (default power). The shared `LoRaHAM_Daemon` checkout was found at
   an unpinned commit (`4943b8e`, with an untracked build directory), which made chat read `differs`
   after the auto-install; it was moved to the pin `e8e748e` and chat/voice read `match`.

## Rows

| # | stack | channel | install | build | start | evidence |
|---|---|---|---|---|---|---|
| 1 | `daemon` | binary | 7s | *refused* (binary) | 13s | `lhpc status daemon`: running, both bands READY; `lhpc daemon 433` / `868` answer; build *refused* (binary, as designed) |
| 2 | `chat` | pinned | 5s | 5s | 5s (refused) | typed `manual_required` — the interactive contract; source at the pin after the run |
| 3 | `voice` | pinned | 4s | 5s | 8s | `loraham-voice-cli` built and listed; GTK variant `not-applicable` on Lite |
| 4 | `kiss` | pinned | 4s | 16s | 9s | verified; TCP `127.0.0.1:8001` answers |
| 5 | `graywolf` | pinned | 2s | 16s | 11s | verified; web UI `127.0.0.1:8080` → 200; KISS client held |
| 6 | `reticulum` | pinned | 212s | 248s | 12s | rns on 868 with ready marker, nomadnet, lxmd, meshchat built; MeshChat UI `:8790` → 200; Sideband skipped on Lite |
| 7 | `meshcore` | pinned | 71s | 435s | 13s | node + webui verified on 868 (`:8788` → 200), openhop repeater source `match`; `known-working` recorded |
| 8 | `meshtastic` | binary | 133s | *refused* (binary) | 3s (refused) | **first attempt refused as designed**: `node_short` missing (the box had no Meshtastic identity; the runner set only `node_name`). Row 8r below is the re-run |
| 9 | `meshtastic` | pinned (from source) | — | *not re-run* | — | maintainer's waiver 2026-09-19; pin `54e0d8d` unchanged since the artifact was built at v0.7.0 — row 8r proves that artifact |
| 10 | `daemon` | pinned (from source) | — | *not re-run* | — | same waiver; pin `e8e748e` (1.1.1) + RadioLib `187ef24` unchanged since v0.7.0 — row 1 proves that artifact |
| 11 | `meshcom` | binary | 25s | *refused* (binary) | 360s | bridge + QEMU node + gps feed running on 433; web UI `:18083` → 200 once the node had booted; build *refused* (binary) |
| 12 | `meshcom` | pinned (from source) | — | *not re-run* | — | same waiver; pins `7c86c96` / `6edc749` / `b322a88` unchanged since v0.7.0 — row 11 proves that artifact |
| 8r | `meshtastic` | binary (re-run, `node_short` set) | 134s | *refused* (binary) | 33s | verified on 868: `meshtastic-gps` *position source live (5 sentences)* through gpsd with a real fix, `meshtastic` ready on `:4403`, post-start completed, `lhpc meshtastic --info` → `Owner: e293` |

Memory stayed between 219 and 246 MB available (`free -m`) across the rows; no OOM line.

## Cross-cutting checks

| check | result |
|---|---|
| **auto-install consistency** | every stack purged, then `lhpc auto-install --yes`: **9/9 successful, 0 blocked, 0 failed, 0 skipped**, 19 min 24 s (17:35:03 → 17:54:27); daemon, meshtastic and meshcom from the published binaries, the light stacks built from source; `lhpc status --versions` afterwards: every source component `match` (chat after the checkout correction above) |
| **`dev` selector spot-check (kiss)** | `install --source dev` resolved the branch tip and built; kiss started under the daemon; reinstalled on the default channel: `match` (the tip is the pin) |
| **known-working** | recorded for kiss, reticulum and meshcore after their green starts (`lhpc known-working <stack>`) |
| **boot restore** | one reboot through the console's own Reboot action with the release's default running set (nothing): see below |
| **web console** | `/`, `/stacks`, `/auto-install`, `/dependencies`, `/controller/logs`, `/healthz` → 200; every `/stacks/<stack>` → 302 to its anchor and `/stacks/<stack>/body` → 200; `/healthz` reports `0.8.0`, 9 stacks; **0 tracebacks** in the console journal across the run |
| **pins vs binaries** | daemon `built_from e8e748e = pin`, RadioLib `187ef24 = pin`, meshtastic `54e0d8d = pin`, MeshCom bridge/qemu/firmware `7c86c96` / `b322a88` / `6edc749` = pins |
| **host tests** | kiss 22 s rc 0 · graywolf 2 s rc 0 (nothing to do) · reticulum 3 s rc 0 (nothing to do) · meshcore 155 s rc 0 · meshtastic / meshcom *refused on the binary channel* (as designed) · daemon / chat / voice *not run* (waiver) |
| **from-zero reinstall** | **blocked — needs root** (firewall reset); not run |

## Boot restore

One reboot through the console's own Reboot action (logind, no root) with nothing running, at
18:09. The box came back on its home Wi-Fi, `lhpc-web` active, `/healthz` → `{"stacks":9,"status":"ok","version":"0.8.0"}`,
boot-restore recorded `state: no-plan` with no issues — nothing had been running, so nothing to restore (`lhpc status`: nothing
running — the release's default set), and no stack came up that had not been running.
