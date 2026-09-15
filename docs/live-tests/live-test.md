# Install-all matrix, 0.6.0 — box B, 2026-09-15

Run on `c4ae47c` (the 0.6.0 candidate). Box **B** = Pi 5 (Desktop, 4 GB), full lane, every row.

Evidence rule: the controller's own typed outcome plus the stack's own state. Log greps are not evidence.

**Three deviations from `test-matrix.md`, accepted by the maintainer before the run:**

1. **The bench is box B, not box E.** The documented reference box is the Pi Zero 2 W (512 MB). Box E
   was needed for GPS-time testing and is powered down, so every row ran on the Pi 5. Build times below
   are therefore optimistic against the documented bench, and the memory/OOM observations the procedure
   asks for say nothing about the constrained box — nothing came close to pressure at 4 GB.
2. **Box B reached the internet through a temporary second address.** Its own gateway (192.168.0.10) is
   down. A second address on the working subnet was added through LHPC's own polkit network rule — no
   root — and removed after the run. This is what closed row 12; see below.
3. **Box B does not carry the time-source feature.** `bootstrap-deps.sh` was never run there, so it
   still keeps time with `systemd-timesyncd` against its hard-configured NTP server, and chrony is not
   installed. **This matrix therefore proves that 0.6.0 does not regress the stacks — not that the time
   source works.** That was proven separately on box E and is recorded in the feature report.

## Rows

| # | stack | channel | install | build | start | evidence |
|---|---|---|---|---|---|---|
| 1 | daemon | binary | 20 s | *refused* | 5 s | `RADIO=READY TXMODE=MANAGED` on **both** 433 and 868 |
| 2 | chat | pinned | 4 s | 1 s | *manual* | typed `manual_required: loraham-chat is interactive` — the contract |
| 3 | voice | pinned | 4 s | 2 s | 2 s | both components started |
| 4 | kiss | pinned | 4 s | 3 s | 3 s | verified |
| 5 | graywolf | pinned | 0 s | 8 s | 3 s | verified |
| 6 | reticulum | pinned | 49 s | 91 s | 2 s | rns on 868, ready marker present, three TCP endpoints, `src match`. **Sideband built here** — the Lite bench skips it |
| 7 | meshcore | pinned | 28 s | 122 s | 2 s | node on `:5000`, 868, `src match` |
| 8 | meshtastic | binary | 43 s | *refused* | 15 s | `:4403` and `:9443` present |
| 9 | meshtastic | pinned | 15 s | **636 s** | 16 s | `:4403` + `:9443`, `src match`, post-start completed |
| 10 | daemon | pinned | 23 s | 35 s | 6 s | RadioLib + daemon, runs `src match` |
| 11 | meshcom | binary | 26 s | *refused* | 46 s | bridge + node verified |
| 12 | meshcom | pinned | 24 s | **471 s** | 47 s | QEMU built (link gate clean), firmware built, `:7000` + `:12323` verified, **`:18083` → 200** |

*refused* = the documented typed refusal on a binary channel: `build needs the source channel`.

**Every build passed.** The heavy compiles — meshtasticd, RadioLib + daemon, and QEMU + MeshCom
firmware — all completed.

## Row 12 is closed

0.5.0 could only record row 12 as **blocked** on this box: it had no internet of its own, and
PlatformIO could not resolve the ESP32 platform through a proxy. With real connectivity the row
completes — QEMU builds and passes its own link gate, the firmware builds, and the node answers on
`:18083`. The blocker was the bench, exactly as the 0.5.0 report suspected, and not the code.

## Two bench findings, neither a defect

**The purge removes the node identity.** `lhpc clean --purge` removes a stack's config, which includes
`node_name` / `node_short` / `mc_callsign`, and the controller then correctly refuses to start a stack
that has no identity. The documented procedure runs purge → install → build → start with no step that
restores it, so on a box where identity was not already set, every identity-bearing row fails at start.
The 0.5.0 evidence column (`--info returns LHPCPI5`) implies a configured node, so this has always been
assumed rather than stated. **`test-matrix.md` should say so.**

**A GPS-capable stack blocks on its GPS bridge.** meshtastic, meshcore and meshcom each have a `*-gps`
component, and the stack's main component declares a dependency on it. On a box with no receiver the
bridge cannot verify (`GPS feed never reached its source (gpsd connection closed)`) and the main
component is never started — `[blocked] meshcore-node: depends on meshcore-gps, which did not start`.
`use_gps off` is the correct configuration for a receiver-less bench and every affected row then passes.
This never surfaces on the documented bench, which has a u-blox attached. **Also worth stating in
`test-matrix.md`.**

## Observed, not proven

- **meshcore's web UI (`:8788`) read absent** after `stack start` returned rc 0 and the node verified on
  `:5000`. The 0.5.0 run recorded it present on this box. The node is proven; the web UI is not, and it
  is recorded that way rather than assumed.
- The **cold-boot GPS-only case** (RTC-less, no network) and the **live valid-client / revoked-client
  check after CRL repair** remain release acceptance items, unproven here and unchanged by this run.
