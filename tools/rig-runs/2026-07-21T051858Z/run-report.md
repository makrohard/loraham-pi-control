# Acceptance-rig install run report

- Rig: `192.168.178.105` (`lhpc-zero`, Pi Zero 2W, aarch64, 4 cores, 415 MiB RAM), user `makro`, KEY-ONLY SSH.
- lhpc on rig: **v0.1.4 @3e2f881f4** (OLD CLI — no `auto-install --status/--recover`; recovery via file move-aside).
- Install command (README §7): `lhpc auto-install --yes` in a detached tmux session on the pi. Host tests off, no TX. Source: dev (default).
- Goal: every stack installed+built; GUI-only `loraham-voice` + `meshcore-nodegui` skipped-by-design (headless).
- Password: throwaway, operator-supplied for one-time `ssh-copy-id` only; NEVER written to any script/log/report/commit. **Operator: rotate it.**

## Starting state (05:18Z)
- Installed+built already: daemon, chat, igate, kiss, meshtastic.
- Outstanding: meshcom (bridge, gps-relay, qemu), meshcore (meshcore-pi, meshcore-cli).
- GUI-skip (not-installed, expected): voice, meshcore-nodegui.

## Timeline (UTC)
| time | event |
| --- | --- |
| 05:18:58 | supervisor start; recon complete |
| 05:19:58 | first start attempt REFUSED: leftover reservation from a dead run (00:56–01:04) |
| 05:21:10 | RECOVERY: file move-aside of auto-install-{start,lease,marker}.json (old CLI, liveness proven dead) |
| 05:21:11 | run STARTED in tmux `lhpc`; cloning RadioLib |
| 05:22:xx | healthy: MEM 206/415MB, SWAP 1182MB present, DISK 24G free |
| 05:25–05:27 | built loraham-daemon (RadioLib→daemon) |
| 05:29 | built loraham-kiss-tnc |
| 05:31→ | building meshtastic (native, builders=5); MEM tight 165–367/415MB, swap ≤364/1182 — absorbed, no OOM |
| 05:54 | routine tick: still building meshtastic; healthy |
| 06:57 | meshtastic native build ~430/~900 objects (~5 obj/min); progressing (motion/mqtt); no link yet; healthy |

## ★ HEADLINE MEASUREMENTS (Pi Zero 2W, 415 MiB RAM + 768 MB swapfile prio10 + 415 MB zram)
- **Meshtastic native `meshtasticd` cold build**: PlatformIO `native SUCCESS — Took 6173.95 s = 01:42:53`.
  - Full stack step (venv→pio pkg→pio run→link-gate→web→CLI) wall-clock: **06:29:47 → 08:15:10 ≈ 1h45m**.
  - → `build_timeout = 21600` (6 h) is ~3.5× headroom over the real ~1h43m. Even a pessimistic board clears it.
- **Link gate verdict (verbatim, on the real binary)**: `[meshtastic] link gate OK — no SDL/X11/Wayland/Mesa/LLVM/PulseAudio/libinput/xkbcommon in …/build/tools/meshtasticd/meshtasticd`. **The server-only claim is PROVEN on real hardware.**
- **Web UI**: `web UI v2.6.7 installed` at build/tools/meshtasticd/web.
- (native env compiles LovyanGFX/Panel_sdl.cpp objects but the LINKED binary carries no SDL/X11 — the gate confirms.)

## Run 1 outcome (05:21→07:21, rc=1, completed-with-failures)
- 6/8 stacks SUCCESS: daemon, chat, igate, kiss, **meshtastic**, meshcore.
- voice: **SKIPPED** (GUI-only, headless-safe) — as designed; meshcore-nodegui never entered the plan.
- **meshcom: FAILED** — `build meshcom-qemu` rc 1: `fetch-qemu.sh: extracted qemu is not the pinned build esp_develop_9.0.0_20240606 — refusing` (NOT OOM; sha256 matched, version-string check failed; transactional rollback left only a 0-byte lock).

## Corrections
- SWAP: earlier "no swap" was a recon bug (swapon not on non-login PATH; free awk omitted Swap). Actual swap is HEALTHY: /var/swap.lhpc 768M prio10 + zram, one fstab line, active. No repair needed.
- Recovery path CONFIRMED by operator: file move-aside is correct on 0.1.4 (predates --recover).

## FINDING F1 — meshcom-qemu: undeclared libSDL2 runtime dependency (the only blocker)
- ROOT CAUSE (reproduced): the pinned Espressif `qemu-system-xtensa` is dynamically linked to
  `libSDL2-2.0.so.0`, which is ABSENT on the headless rig → the binary can't load → `--version`
  emits nothing → `fetch-qemu.sh:152` refuses ("not the pinned build"). sha256 of the tarball MATCHES
  (43552f32…) — the artifact is correct; it simply can't run without SDL2.
- The project declares libSDL2 NOWHERE as a meshcom/QEMU dependency (dev manifest + docs only mention
  it as meshtastic's *overdeclared* dep). So meshcom-qemu has an UNDECLARED, unmet runtime dep.
- COST of the fix: `libsdl2-2.0-0` pulls a **35-package** cascade — libwayland-*, mesa-libgallium,
  libllvm19, libgbm1, libpulse0, x11-common, libxkbcommon0 … i.e. the exact desktop/GPU/audio stack the
  meshtastic server-only design fights to avoid. Installing it on the headless acceptance rig is a
  POLICY decision → left to the operator. NOT installed by the supervisor.
- Recommendation options: (a) accept SDL2 for meshcom (declare `libsdl2-2.0-0` as a meshcom dep so
  bootstrap installs it, gated as GUI/opt-in); (b) source/build a headless (no-SDL) Espressif QEMU;
  (c) relax fetch-qemu's version check to run qemu with a dummy/`-display none` load path (won't help —
  the .so must resolve at load time regardless). (a) is the pragmatic fix; it needs the operator's call
  on bringing display client libs onto the rig.

## GOAL STATE — reached for everything except the SDL2-blocked meshcom-qemu
- 14/15 components effectively done: daemon, chat, igate, kiss (all built); meshtastic (built + link
  gate CLEAN + web UI); meshcore-pi + meshcore-cli (built); meshcom-bridge + meshcom-gps-relay (built).
- GUI-skip by design (headless): loraham-voice, meshcore-nodegui — correctly NOT built.
- BLOCKED: meshcom-qemu (F1). This is the sole gap; not retried (deterministic, not transient).

## SUPERVISOR CAVEATS found in the field (fixed / noted)
- `lhpc status` shows a component whose BUILD FAILED as "stopped" (source present) — so the status
  token alone is NOT authoritative for "installed+built". The auto-install SUMMARY ([fail]/[success]
  per stack, or the marker file) is authoritative. On the OLD CLI (no `auto-install --status`) the
  supervisor must read the run-log summary / marker, not status. Patched: outcome is now read from the
  teed run log.
- swapon is not on the rig's non-login SSH PATH → use /sbin/swapon. Patched in evidence capture.
