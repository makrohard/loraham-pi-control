# Install-all matrix, 0.5.0 — both boxes, 2026-09-14

Run on `342a069` (main). Box **E** = Pi Zero 2 W (Lite, the matrix's reference box, fast lane by the
maintainer's waiver of 2026-09-13). Box **B** = Pi 5 (Desktop, full lane, added as a second column).

Evidence rule: the controller's own typed outcome plus the stack's own state. Log greps are not evidence.

## Rows

| # | stack | channel | box E | box B |
|---|---|---|---|---|
| 1 | daemon | binary | ✓ install from index (`loraham-daemon@2a0db8872`), build refused as designed, `RADIO=READY` on the CONF socket | ✓ same |
| 2 | chat | pinned | ✓ typed `manual start required for loraham-chat` — the interactive contract | ✓ same |
| 3 | voice | pinned | ✓ `pinned-verified` adopt, terminal variant started | ✓ GTK main started |
| 4 | kiss | pinned | ✓ `127.0.0.1:8001` answers | ✓ same |
| 5 | graywolf | fetched release | ✓ `:8080` answers, post-start provisioning completed | ✓ same |
| 6 | reticulum | pinned | ✓ rns/nomadnet/lxmd/meshchat built rc 0, rns at its ready marker | ✓ same |
| 7 | meshcore | pinned | ✓ node verified on `:5000` | ✓ node on `:5000`, webui on `:8788` |
| 8 | meshtastic | binary | ✓ verified on `:4403`, `--info` returns LHPCBENCH | ✓ returns LHPCPI5 |
| 9 | meshtastic | pinned (source) | *not re-run* — fast-lane waiver 2026-09-13 | ✓ built rc 0, verified, `--versions` = `match` |
| 10 | daemon | pinned (source) | *not re-run* — fast-lane waiver | ✓ daemon + RadioLib in **55 s**, runs `src match` |
| 11 | meshcom | binary | ✓ bridge `:7000`, node `:12323`, UI `:18083` = 200 | ✓ bridge and node verified |
| 12 | meshcom | pinned (source) | *not re-run* — fast-lane waiver | ✗ **blocked** — see below |

**Row 12 on B, blocked by the bench's network, not by the code.** B has no internet of its own. Uppercase
`HTTPS_PROXY`/`HTTP_PROXY` carried the PlatformIO *library* stage (this is what unblocked row 9) and
`git config --global http.proxy` carried the *git-clone* stage, but the ESP32 platform and Arduino
framework resolution still ends in `HTTPClientError`. Hand pre-resolution is not available either: the
`qemu-headless` environment only exists after LHPC applies its overlay, so `pio pkg install -e
qemu-headless` in the plain checkout fails with `UnknownEnvNamesError`. The bridge component of the same
row builds rc 0 and runs verified on `:7000`, and the binary channel for the same stack (row 11) passes
on both boxes.

**Proxy note worth carrying into the matrix:** on a proxied box the lowercase `https_proxy` that the
dotfiles set does not reach PlatformIO. Only the uppercase names work, and git needs its own
`http.proxy`. Symptom without them: `OSError: [Errno 113] No route to host`.

## Cross-cutting checks

| check | E | B |
|---|---|---|
| pins vs binaries (`status --versions`) | ✓ all three binary stacks `built_from == pin` | ✓ `match` on all, after its source builds |
| known-working recorded per source-built stack | ✓ kiss, meshcore, reticulum | ✓ kiss, meshcore, reticulum, meshtastic |
| console through the firewall (mTLS, client cert) | ✓ `:8443` = 200, refused without a certificate | ✓ same |
| per-stack proxies | ✓ 200 with the stack up | ✓ graywolf `:8446`, meshcore webui `:8447` = 200; 502 exactly when the upstream is down |
| boot restore with the default running set | ✓ reboot, journal `2 restored, 0 failed`, identical set back | ✓ (earlier run: `3 restored, 0 failed`) |
| host tests, last | ✓ kiss rc 0 | ✓ |
| from-zero reinstall (§13) | deferred — fast lane | ✓ steps 1–8 plus the secrets and exposure restore |

## From-zero reinstall on B (§13)

Wipe and rebuild from nothing, following the README's happy path verbatim. Both teardown scripts
returned rc 0 and the box was verified clean down to the user units and `/etc/lhpc`. The installer
brought the controller up with identity **ok** on `342a069` and started the console itself.

Step 4's contract proved twice over: a licensed stack first refused with "no radio hardware
configured", and after `lhpc hardware loraham` refused again with the typed callsign refusal and its
`lhpc config operator --callsign` hint. Step 6 passes byte-for-byte — the graywolf admin password shown
on the stack page equals `state/graywolf/graywolf-admin.txt`. Step 8 started and stopped every stack;
chat's `manual start required` and meshcore/meshtastic's `'node_name' is required` after a purge are the
designed refusals, cleared by setting the identity (meshtastic per band).

The restore was verified from outside the box: the old server certificate is served again, the
operator's client certificate is active, the console answers 200 with a client certificate and 403
without, the proxies 403 without and 502 while their stacks are down, and the firewall reports
Config ✓ Boot ✓ Live ✓.

**Second proxy finding, and a trap.** `auto-install` from the web console reached 1 of 9 stacks on this
box: the `lhpc-web` service environment carries no proxy, so every binary fetch failed with
`[Errno 113]`. The same run from the CLI with the proxy exported completed 9/9, 0 blocked, 0 failed.
A systemd drop-in carrying the proxy is **not** a workaround — the integrity check refuses managed
units that have drop-ins. Full evidence: `part2-box-b-from-zero.md` in the release-automation tree.

## Result

E's fast lane is green on every row it runs. B is green on 11 of 12, with row 12 blocked by the box's
lack of internet rather than by anything in the release. No defect was found in Part 2.
