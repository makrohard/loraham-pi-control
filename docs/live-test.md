# Live tests

Dated validation evidence, newest first: what was run on the reference box and against real peers, with
measured values only. The procedure lives in [test-matrix.md](test-matrix.md); CI and the
[testlab](testlab.md) prove the code and the console.

## Contents

- [0.2.10 — release test, 2026-09-05/06](#0210--release-test-2026-09-0506)
- [Silicon test, 2026-09-05](#silicon-test-2026-09-05)

## 0.2.10 — release test, 2026-09-05/06

All checks pass. Box: Pi Zero 2 W (e293), Lite image, LoRaHAM Pi HAT dual-module. Row = one stack purged,
installed, built, started and verified; checks 14–42 = the cross-cutting and from-zero items of the procedure.


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
| 20 | from-zero | documented install happy path line by line (README → `install.sh` → console); docs corrected where a line fails | README step 2 `bootstrap-deps.sh --dry-run` (no root): rc 0, 0:00:37; step 3 not repeated (root; deps present from the image); step 4 `curl … install.sh | bash`: rc 0 in 0:01:45, `lhpc --version` = 0.2.10, console 200 on loopback right after install, units lhpc-web + lhpc-nginx; step 5 reboot through the console: accepted (302), box came back on its fallback AP `lhpc-e293` | pass |
| 21 | from-zero | Wi-Fi join via the Network panel from the box's fallback AP (the genuine flow), console back on the joined network | the fresh install came up on its fallback AP `lhpc-e293`; this PC joined the AP and drove the console's Network panel: stage 1 (confirm page) 200, stage 2 confirmed with the password 302 → the AP vanished after 5 s and the box answered on the home network after 12 s, the operator's Wi-Fi active on wlan0; the console's own check on the joined network follows in part 2 | pass |
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


## Silicon test, 2026-09-05

On-air acceptance of the stacks against real ESP32 peers running each project's own original firmware —
not emulated peers, not LHPC talking to itself. Only what was actually transmitted and heard is recorded.
Times are CEST unless marked UTC. Scoreboard: 40 rows across four stacks — 34 pass, 1 fail (Graywolf's
scheduled beacon), 1 inconclusive, 4 not covered (no indoor GPS fix on the tracker; content verification
on the MeshCom peer).

### Test bench

| | |
|---|---|
| Box | `lhpc-e293` (Raspberry Pi Zero 2W, Lite image), LAN `192.168.178.106`, LHPC main @ `b7cfd5c` (0.2.8 + fixes) |
| Radio | LoRaHAM daemon on both bands: 433 via `/tmp/loraconf433.sock`, 868 via `/tmp/loraconf868.sock`. Meshtastic drives its 868 radio directly instead |
| Peer A (433, MeshCom) | LilyGo T-Deck, MeshCom 4.35p, call `DJ0CHE-07`, workstation USB, serial console |
| Peer B (868, Meshtastic) | BQ Station G2, `CHE Station` / `cheS`, workstation USB, Meshtastic CLI |
| Peer C (433, LoRa-APRS) | the same T-Deck reflashed to CA2RXU 2026-04-22, call `DJ0CHE-7`, serial console |
| Peer D (869.618, MeshCore) | LilyGo T-Deck Pro, node `CHEMobile`, driven over **BLE** (its USB is log-only) |
| Distance | Same premises, a few metres. RSSI −19…−60 dBm depending on the pair — near field throughout, so nothing here says anything about range |
| Operator | All RF inside the licensed profiles; nothing transmitted beyond the tests listed |

Peers A, B and C occupy the workstation's single `/dev/ttyACM0`, so only one is plugged in at a
time; the sections below are in the order they were run.

Evidence sources used:

- Daemon counters on both bands: `GET STATS`, `GET STATUS`, `GET CHANNEL` on the CONF socket.
- Stack logs on the box, and each stack's own API or web endpoint.
- On the peers: the firmware's own serial console, its Meshtastic/MeshCore client over USB or BLE,
  and its heard/contact list.

---

### 1. MeshCom (QEMU + bridge + daemon) — 433.175 MHz

**Result: PASS for both directions at packet level; message content not independently verified; one repeat inconclusive.**

#### Configuration as found

| Node | Call | Profile | Power | Notes |
|---|---|---|---|---|
| QEMU node (box) | `DJ0CHE-12` | 433.1750 MHz, EU8, EBYTE_E22 emulation | 20 dBm | MESH off, gateway off, web :18083, net-console :12323 |
| T-Deck (peer) | `DJ0CHE-07` | 433.1750 MHz, SF11, BW250, CR4/6 | 22 dBm | MESH on, gateway off, WiFi off |
| Daemon 433 | — | configured by the bridge (control-plane) | capped 20 dBm | `TXMODE=MANAGED`, `CADRSSI=−90`, `CADWAIT=1500` |

Stack start sequence observed: bridge listening 08:25:58, QEMU launched 08:26, XR client connected
08:31:42 (≈5.5 min boot on the Zero 2W — matches the documented expectation), radio configured
08:31:45.

#### Matrix

| # | Test | Evidence | Result |
|---|---|---|---|
| 1 | Boot hellos cross both ways (no operator action) | QEMU MHeard: `DJ0CHE-07 HEY` rssi −54 snr 12; T-Deck MHeard: `DJ0CHE-12 HEY` rssi −48 snr 7; daemon RX=1 TXOK=1 | PASS |
| 2 | T-Deck → box text (`::LHPC test 1 from T-Deck DJ0CHE-07`, 08:41:53) | daemon RX 7→9 within seconds; QEMU MHeard row → typ `TXT` 07:41:55 UTC | PASS |
| 3 | Box → T-Deck text via net-console (`::LHPC test 2 from QEMU DJ0CHE-12`, 08:43:01) | daemon TXQDONE 15→16, TXOK 5→6, TXQLAST=OK; T-Deck MHeard row → typ `TXT` 07:44:01 UTC (≈60 s after send) | PASS |
| 4 | Box → T-Deck repeat (`::LHPC test 3 …`, 08:45:43) | daemon TXQDONE 16→20, TXOK 6→7, CADTIMEOUT 10→12; T-Deck MHeard shows only a later `HEY` (07:47:33 UTC) | INCONCLUSIVE |
| 5 | Received text readable on the far side | not achieved — MHeard proves a `TXT` packet arrived, not its content | NOT COVERED |
| 6 | Periodic beacons during the run | both MHeard tables kept refreshing | PASS |

#### Findings

1. **Channel reads BUSY almost permanently at the box.** `GET CHANNEL` sampled live RSSI −78…−84 dBm
   against `CADRSSI=−90`; `CADSTATE=BUSY` in every sample. Over the session 12 of ~20 queued TX
   jobs ended `CAD_TIMEOUT`. This is why row 4 is inconclusive and why box→peer traffic will be
   lossy in this environment. MeshCom runs MANAGED by design, so the remedy is the threshold, not
   the mode: raise `CADRSSI` towards the measured floor. Not applied — operator decision.
2. **MANAGED is the correct TX mode, and the stack page was stale.** The bridge submits managed-TX
   settings and the live box ran MANAGED throughout, transmitting successfully — so MANAGED is what
   MeshCom uses. `docs/stacks/meshcom.md` claimed the daemon "must be in DIRECT mode" in two places;
   both are corrected as part of this commit.
3. **Latency:** box→peer texts appear ~60 s after the send (MeshCom's own TX scheduling), peer→box
   within ~2 s.

#### Follow-ups (not done)

- Decide the CADRSSI policy for MeshCom: the threshold, not the TX mode, is what stalls it here.
- Add a content-verified message test (web UI or debug capture) and repeat row 4 three times.
- Consider surfacing `CAD_TIMEOUT` drops in the stack page (idea already noted in the RF lessons).

---

### 2. Meshtastic (meshtasticd, native) — 868 MHz

**Result: PASS in all four tested paths (broadcast and direct message, both directions), after a
full reset of both nodes to LHPC first-install settings.**

#### Reset performed first (operator instruction: LHPC must run its standard settings, not the Spanish ones)

The box node carried a hand-made configuration that LHPC does not manage and never sets:
five channels (`SFNarrow` primary, plus `Iberia`, `Madrid`, `Bots`, `Test`), `usePreset: false`,
`overrideFrequency: 869.618`, SF7 / BW62.5. The peer was on stock preset settings, so the two could
not meet.

| Step | Command | Effect |
|---|---|---|
| Backup both nodes | `lhpc meshtastic --export-config` / `meshtastic --port … --export-config` | saved to the scratchpad before any change |
| Reset box node | `lhpc meshtastic --factory-reset --yes` | config reset; meshtasticd logged "Factory config reset finished, rebooting soon" and exited |
| Restart stack | `lhpc stack start meshtastic --yes` | LHPC re-applied what it owns: region, node identity, GPS mode, fixed position — "required post-start completed" |
| Reset peer | `meshtastic --port /dev/ttyACM0 --factory-reset` | region fell to `UNSET` (TX disabled) as expected |
| Peer region + name | `--set lora.region EU_868 --set-owner "CHE Station" --set-owner-short cheS` | the peer is not LHPC-managed, so these two settings are set by hand |

LHPC's own stack parameters needed **no** change — they were already at manifest defaults
(`region = EU_868`, `use_gps = on`); only the required identity (`node_name`, `node_short`) is
operator-set. The Spanish configuration lived entirely inside Meshtastic, not in LHPC.

#### State after the reset — both nodes identical where it matters

| | Box (LHPC-managed) | Peer (Station G2) |
|---|---|---|
| Node | `Joe on LHPC` / `JLHP`, `!9ee3dad0`, PORTDUINO | `CHE Station` / `cheS`, `!a2e9aed8`, STATION_G2 |
| Region | EU_868 | EU_868 |
| Preset | `usePreset: true`, BW250 / SF11 / CR5 (LongFast) | same |
| Override frequency | none | none |
| Primary channel | default (identical channel bytes on both) | default |
| TX | enabled, 27 dBm | enabled, 27 dBm |
| GPS | enabled, live fix 48.4180 / 11.6654 | not present |

#### Matrix

| # | Test | Evidence | Result |
|---|---|---|---|
| 1 | Peer → box, broadcast text | box log: `Received text msg from=0xa2e9aed8 … msg=LHPC MT test 1 from G2` | PASS |
| 2 | Box → peer, broadcast text | peer listener: `TEXT from !9ee3dad0: LHPC MT test B from box rssi=-19 snr=6.0` | PASS |
| 3 | Peer → box, direct message with ACK | first attempt NAK `NO_CHANNEL`; after node-info exchange: `Received an ACK`, box log has the text | PASS (after 4) |
| 4 | Node-info / key exchange | `lhpc stack poststart meshtastic --yes` → peer received `NODEINFO_APP from !9ee3dad0` and stored the box with its public key | PASS |
| 5 | Box → peer, direct message with ACK | first attempt NAK `MAX_RETRANSMIT`; retry: `Received an ACK` and peer received the text (rssi −22, snr 6.0) | PASS on retry |
| 6 | Box position broadcast | peer received `POSITION_APP from !9ee3dad0` | PASS |
| 7 | LHPC reconvergence after a factory reset | region, owner and GPS re-applied automatically by the stack's post-start | PASS |

#### Findings

1. **A factory-reset node cannot be direct-messaged until node info has been exchanged.** Modern
   firmware refuses a channel-encrypted direct message — box log: `Rejecting legacy DM`, answered
   with routing error `NO_CHANNEL`. The sender only uses the modern encrypted form once it holds the
   recipient's public key, which arrives with node info. Default `nodeInfoBroadcastSecs` is 10800 s,
   so an operator can wait up to **three hours** after a reset before direct messages work.
   `lhpc stack poststart meshtastic` re-applies the node identity and triggers an immediate node-info
   broadcast — the practical unblock. Worth documenting in the Meshtastic stack page; broadcasts are
   unaffected throughout.
2. **First direct-message attempt failed in each direction, succeeded on retry** (`NO_CHANNEL` one
   way for the reason above, `MAX_RETRANSMIT` the other). Treat a single failed direct message as
   inconclusive, not as a broken link.
3. **LHPC's post-start convergence works as designed** — after a factory reset the node came back
   with the LHPC-owned settings re-applied without any operator action beyond the stack start.
4. **The link is near-field** (RSSI −19…−22 dBm). This test proves configuration and protocol, not
   range.
5. **Historic Spanish traffic is still in the box log** (`Hola Getafe!`, `Test DJ0CHE Getafe`), from
   before the reset. Log only; the node configuration is clean.
6. **GPS on the box works** (live fix broadcast to the peer). The recurring `RTC not found` warning
   is the known missing real-time clock on this box, not a fault.
7. **Side effect to resolve:** the LoRaHAM daemon and, with it, the MeshCom stack were **running**
   before this work and are **stopped** now; the daemon's log stops at 09:07, around the Meshtastic
   restart. meshtasticd is documented as conflicting with the daemon over direct SPI, so a
   conflict-driven stop is plausible — but the two had been running side by side beforehand
   (433 on CE0, 868 on CE1), so this needs a look before the next stack test.

#### Follow-ups (not done)

- Establish why the daemon/MeshCom stopped, and restart them.
- Document the node-info / direct-message rule and the `poststart` unblock on the Meshtastic stack page.
- Repeat direct messages three times per direction to quantify the first-attempt failures.

---

### 3. Graywolf APRS (+ KISS TNC + daemon) — 433.775 MHz LoRa-APRS

**Result: PASS. A message sent from the box was received and acknowledged by the peer, and the
acknowledgment came back over RF and was gated to APRS-IS — a complete round trip with verified
content.**

#### Peer

LilyGo T-Deck, reflashed from MeshCom to **CA2RXU LoRa APRS Tracker/Station, version 2026-04-22**
(`RichonGuzman`), on the workstation's USB at `/dev/ttyACM0`. It arrived unconfigured: the firmware
force-starts its web-configuration portal (own access point `LoRaTracker-AP`) until a callsign is
set, so it never reached operating mode.

Per operator instruction the tracker keeps **standard configuration, callsign only**. It was set to
`DJ0CHE-7` in all three beacon profiles through the portal, everything else untouched. Its portal
posts `multipart/form-data`, so a URL-encoded post is silently ignored — that is why an earlier
attempt appeared to save but did not. After the write the device left configuration mode and booted
normally (`Initializing SX126X … LoRa init done! … Setup Done!`).

Its stock radio profile matches LHPC's APRS profile exactly, so nothing had to be aligned:

| | Tracker (CA2RXU, profile 1) | Box (LHPC `_LORAHAM`) |
|---|---|---|
| Frequency | 433.775 MHz | 433.775 MHz RX and TX (single channel) |
| Spreading factor | 12 | 12 |
| Bandwidth | 125 kHz | 125 kHz |
| Coding rate | 4/5 | 4/5 |
| Power | 20 dBm | 17 dBm |
| TX mode | — | MANAGED (required by Graywolf) |

#### Chain brought up on the box

`lhpc stack start graywolf --yes` started all three in order and verified each: daemon on 433 in
MANAGED mode, KISS TNC listening on `127.0.0.1:8001` with the client attached, Graywolf on
`127.0.0.1:8080` with its post-start provisioning completed. Its KISS channel reports
`health: live`, `tx capable`. Meshtastic stayed running on 868 throughout, so the two coexist.

#### Matrix

| # | Test | Evidence | Result |
|---|---|---|---|
| 1 | Tracker configured and in operating mode | portal write accepted; boot log shows radio init and `Setup Done!`; config diff is the callsign only | PASS |
| 2 | Chain starts with dependencies in order | daemon MANAGED → TNC 8001 → Graywolf 8080, each verified by LHPC | PASS |
| 3 | KISS client attached to the TNC | TNC log `Client connected: 127.0.0.1:41692`; channel `health: live` | PASS |
| 4 | APRS-IS session established | `aprs-is connected server=rotate.aprs2.net:14580 callsign=DJ0CHE` | PASS |
| 5 | Box → tracker, message over RF | tracker console: `[LoRa Rx] DJ0CHE>APGRWO,WIDE1-1,WIDE2-1::DJ0CHE-7 :LHPC graywolf test 1{005` | PASS |
| 6 | Tracker → box, acknowledgment over RF | tracker `[LoRa Tx] … ack005`; box received it, Graywolf marks message 5 `acked` after 1 attempt | PASS |
| 7 | Callsign confirmed on air | the received frame's source reads `DJ0CHE-7` | PASS |
| 8 | Daemon transmitted without a channel-busy stall | `TXOK=1 TXERR=0 TXBUSY=0 CADTIMEOUT=0` | PASS |
| 9 | RF → APRS-IS gating | the acknowledgment appears as `DJ0CHE-7>APLRT1,WIDE1-1,qAR,DJ0CHE::DJ0CHE :ack005` | PASS |
| 10 | Tracker's own position beacon → box | not exercised: no GPS fix indoors, so the tracker never beacons | NOT COVERED |
| 11 | Box → tracker, position beacon on manual trigger | `POST /api/beacons/1/send` → `{"status":"sent"}`; tracker console: `[LoRa Rx] DJ0CHE>APGRWO,WIDE1-1,WIDE2-1:!4825.81N\\01140.09EO/A=001649Hello from Joe!`; daemon `TXOK` 1→3, no CAD timeouts | PASS |
| 12 | Box → tracker, position beacon on its own schedule | beacon is `enabled`, GPS-sourced, `interval: 600`, gpsd holds a 3D fix, scheduler heap built — yet nothing transmitted in the 19 minutes after start and a 6-minute tracker capture was silent | **FAIL** |
| 13 | Beacon carries a real position | `!4825.81N\\01140.09EO/A=001649` — the box's gpsd fix, altitude in feet | PASS |
| 14 | Tracker → box, position beacon | the operator cannot trigger a beacon by hand on this build and there is no indoor GPS fix, so the tracker never beacons. Its transmit path is already proven by row 6 (the acknowledgment), so this row adds nothing and was skipped by agreement | NOT COVERED (skipped) |
| 15 | Digipeating of a peer packet | not exercised | NOT COVERED |

#### Findings

1. **The APRS side transmits cleanly where MeshCom stalls.** The same 433 band read `BUSY` with a
   live RSSI near −83 dBm, yet the APRS message went out first time with zero CAD timeouts. The
   APRS profile waits up to 1500 ms and needs 250 ms of confirmed idle, and that found a gap;
   MeshCom's much shorter idle window did not. This strengthens the case that the default
   channel-busy threshold, not the radio, is what blocks MeshCom transmissions here.
2. **Test traffic reached the public APRS network.** With the iGate enabled, the peer's
   acknowledgment was receive-gated to APRS-IS as `qAR,DJ0CHE`, and the outgoing message was
   **also** delivered over APRS-IS in parallel with RF. Everything used the operator's own
   callsign, so this is legitimate, but it means bench traffic is publicly visible. For a purely
   local test, set `igate = 0` — which is the LHPC default. The station currently runs
   `igate = 1`, `gate_rf_to_is = 1` and `gate_is_to_rf = 1`, the last two both non-default;
   `gate_is_to_rf` had no effect here because the iGate has no IS→RF rules configured.
3. **The tracker cannot be tested unattended without a GPS fix.** CA2RXU only beacons on a valid
   fix, so peer-initiated traffic needs either an outdoor fix or a keypress. Message
   acknowledgments are the reliable way to prove the peer's transmit path indoors.
4. **The tracker's portal needs multipart form data.** Worth knowing before anyone scripts against
   it: a URL-encoded post returns no error and changes nothing.
5. **A callsign is what releases the tracker from configuration mode.** Freshly flashed it is
   unreachable on RF, which looks like a hardware fault and is not one.
6. **The beacon transmits on demand but never on its own schedule.** `POST /api/beacons/1/send`
   puts a correct position beacon on the air within a second, twice in a row, received intact by the
   tracker — so the radio path, the position source and the beacon definition are all sound. What
   does not happen is the scheduled transmission: the beacon is `enabled` with `interval: 600`,
   `delay_seconds: 30`, `slot_seconds: 146`, the scheduler logged
   `beacon scheduler heap built count=1`, and nothing fired in 19 minutes. That isolates the defect
   to Graywolf's beacon scheduling, not to LHPC's radio chain. Worth reproducing and, if it holds,
   reporting upstream.

#### Follow-ups (not done)

- **Investigate the beacon that never fired** (finding 6) — the main open item from this section.
- Decide the iGate policy for bench testing and record it.
- Test digipeating of a peer packet.
- Take the tracker outdoors for a GPS fix if peer-initiated beacons ever need covering; the peer's
  transmit path is already proven by its acknowledgment, so this is optional.

---

### 4. MeshCore (OpenHop) — 869.618 MHz, mode chat+repeater

**Result: PASS in every functional path — messaging both ways with acknowledgments, advert
propagation, repeater retransmission and both web endpoints. One usability defect found in the
command-line client.**

#### Peer

LilyGo **T-Deck Pro** running MeshCore, node name **CHEMobile**, public key `37bd5d25f79f…`. Its USB
port is log-only — it is not a serial companion, so the client interface is **BLE**
(`MeshCore-CHEMobile`, `50:78:7D:2C:B6:A1`). Driving it from the workstation over BLE with the
`meshcore` Python package let both ends of every test be scripted.

Its radio matches the box's `eu_uk_narrow` preset exactly, so nothing had to be changed on either
side:

| | Peer (CHEMobile) | Box (`eu_uk_narrow`) |
|---|---|---|
| Frequency | 869.618 MHz | 869.618 MHz |
| Bandwidth | 62.5 kHz | 62.5 kHz |
| Spreading factor | 8 | 8 |
| Coding rate | 8 | 8 |
| TX power | 14 dBm (max 22) | 14 dBm (max 20) |

#### Box side

Node **DJ0CHE-12** (`e9dfe7e00b47…`), mode `chat+repeater` with repeater **Relay e293** in `forward`
behaviour, position from the global GPS source. MeshCore reaches the radio **through the LoRaHAM
daemon** on 868, not by direct SPI, so the start applied the daemon's queue and channel-busy
settings first.

#### Matrix

| # | Test | Evidence | Result |
|---|---|---|---|
| 1 | Radio contention is refused, not fudged | starting MeshCore while Meshtastic held 868 was refused with a typed conflict naming the holder and the fix (`meshtastic must be stopped first`) | PASS |
| 2 | Stack starts after freeing the radio | daemon 868 settings confirmed one by one, GPS live (5 sentences), node endpoints 5000 and 8000 up, web UI 8788 up | PASS |
| 3 | Radio profile matches the peer | both ends report 869.618 / 62.5 / SF8 / CR8 | PASS |
| 4 | Nodes know each other | box lists `CHEMobile` at **0 hop**; peer lists `DJ0CHE-12` and `Relay e293` | PASS |
| 5 | Box → peer direct message | peer received `{"type":"PRIV","pubkey_prefix":"e9dfe7e00b47","text":"LHPC meshcore test 1"}` | PASS |
| 6 | Peer acknowledged it | node log `RX ACK (3) len=4` | PASS |
| 7 | Peer → box direct message | node log `RX TXT_MSG (2) len=52` | PASS |
| 8 | Box acknowledged it | node log `TX 8 bytes (type=ACK, route=DIRECT)` | PASS |
| 9 | Advert from the box reaches the peer | peer event `ADVERTISEMENT {"public_key":"e9dfe7e00b47…"}`; its contact's `last_advert` jumped from 1788554754 to 1788598531 | PASS |
| 10 | Repeater retransmits | `RepeaterHandler: Retransmitted packet` for both the ACK (8 bytes) and the advert (120 bytes, 1131 ms airtime) | PASS |
| 11 | Web UI and repeater dashboard serve | 8788 → 200, 8000 → 200 | PASS |
| 12 | RF counters agree with the app layer | daemon 868: `RX=3 TXOK=2 TXERR=0 CADTIMEOUT=0` | PASS |

#### Findings

1. **`meshcore-cli` reports failures for commands that actually succeed.** Both `msg` and `advert`
   returned `Error … {'reason': 'no_event_received'}` while the node log shows the work was done and
   the peer received it. `sync_msgs` and `recv` return nothing at all even though a message had just
   arrived and been acknowledged. The cause is visible in the node log: every CLI invocation opens a
   new companion session and **evicts the previous one** (`Companion already has a client; evicting
   previous connection`), then disconnects on `empty_read`. So the confirmation event and any queued
   message are lost with the evicted session. The protocol layer is sound; the one-shot CLI usage
   pattern is not. Anyone reading `meshcore-cli` output as ground truth will draw the wrong
   conclusion — the node log is the reliable source.
2. **The peer's USB port is not a companion interface.** The T-Deck Pro's serial output is boot and
   hardware logging only; a `meshcore` client on it fails with "are you sure your node is a serial
   companion?". BLE is the control path, and it worked first time with no OS pairing.
3. **Radio arbitration is correct and legible.** LHPC refused to start MeshCore while Meshtastic held
   868, named both the radio and the resource key, identified the holding stack and printed the
   command that resolves it. This is the behaviour that was missing from the earlier session, where
   the daemon simply vanished.
4. **868 transmits cleanly.** No CAD timeouts at all on this band, in contrast to MeshCom's
   experience on 433 — further evidence that MeshCom's problem is its channel-busy settings rather
   than the radio or the site.
5. **The peer carries stale contacts** (`CHEPortable`, `DJ0CHE-06`, `MC_Node` with adverts from
   earlier sessions). Harmless, but worth knowing when reading its contact list as test evidence:
   only a refreshed `last_advert` proves current reachability.

#### Follow-ups (not done)

- Decide whether `meshcore-cli` should hold one persistent companion session for multiple commands,
  or whether LHPC should document that its one-shot output cannot be trusted (finding 1).
- Exercise the repeater's forwarding between two remote nodes; this run only proved it retransmits
  its own traffic.
- Channel (group) messaging and telemetry were not tested.

---
