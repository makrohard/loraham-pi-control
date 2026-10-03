# CLOUD BRIEF · PLAN for fix group G13 of loraham-pi-control (one Claude Code cloud session, read-only on the code)

You write the PLAN for a group of verified defects; you change no code. Output = ONE commit on this routine's branch
adding `plans/PLAN-G13.md`. Never touch `main` or `dev`, no pull request. (Attribution lines are stripped
downstream; the file is copied out.) Base: origin/main = e5187f70ae4e81a1081a835be06f84746222c3b6 (v0.11.10). Read `docs/architecture.md` (the safety
invariants), `docs/README.md`, `tests/README.md` (the house rules of the tests) and `docs/maintenance.md` (how a patch
is cut) first.

## The maintainer's rules for every change
Simple, robust, maintainable: the SIMPLEST change that removes the defect; no new mechanism unless the defect cannot be
removed without one (then say so and size it); no behaviour change beyond the defect; a regression test per change that
is RED before and GREEN after, placed where the maintainer would look for it (the existing test module of that file);
docs updated where a sentence becomes untrue (one place per fact); the CHANGELOG line in the operator's words.

## The findings of this group (verified; file:line on e5187f70ae4e81a1081a835be06f84746222c3b6)
**Group context / decisions:** DECIDED CR9-2: a select/poll drain with a stop flag (bounded), not an abandoned thread; CR9-3: Detect takes the daemon instance lock like a start.

### CR9-2
- where: `lhpc/core/probes/backends.py:439 (same at :270-274)` · severity S2 kept: no setsid is needed. On normal exit `terminate_session` is not called, so any bac
- claim: `_run_controlled`: "an escaped descendant holding stdout can never wedge us"; "force it so the drain can't wedge us".
- defect: `proc.stdout` is a `BufferedReader`, and the drain thread is blocked inside `read()` while it holds the buffer lock. `close()` from the main thread waits for that same lock, so it blocks until the escaped descendant closes the pipe or exits. `RealCommandRunner.run` (:270-274) has the same pattern. The bounded join does not bound the call: a build/web-job driver hangs, together with the locks it holds.
- how to see it: Reproduced: `Popen(["sh","-c","setsid sleep 12 & echo hi"], stdout=PIPE)`, drain thread as in `_drain`, `p.wait()`, `join(1)`, then `p.stdout.close()`. The close took 11.0 s, i.e. until the descendant exited. A test that spawns a step leaving a `setsid` child holding stdout and asserts `run_streaming(..., should_cancel=…)` returns within ~6 s with `output_unverified=True` would be red.
- verifier: CONFIRMED — Scratch script with `sh -c "setsid sleep 10 & echo hi"`: `run_streaming(..., should_cancel=lambda: False)` took 10.0 s with `output_unverified=False`, and `RealCommandRunner.run` also took 10.0 s. `stream.close()` at backends.py:439 and :270-274 waits for the BufferedReader lock, so the bounded joins do not bound the call. Closing the fd does not wake a blocked `read` on Linux, so the fix needs a select/non-blocking drain or an abandoned thread.

### CR9-3
- where: `lhpc/core/probes/hardware.py:187-188 (caller service_lifecycle_ops.py:543-565)` · severity S2 kept (the effect on the radio was not verified on hardware)
- claim: "It NEVER clobbers a real daemon … an already-served band exit BUSY"; architecture: "One stack owns a band at a time" (`loraham.radio.<band>` exclusive).
- defect: The only protection is the daemon's own instance lock, which only a LoRaHAM daemon holds. meshtasticd and the Reticulum node own the band through an exclusive `loraham.radio.<band>` claim, but `probe_hardware` (POST `/hardware/probe`) checks no claims, run state or operation lock. The probe daemon therefore runs `begin()` on the chip that a running direct-radio stack is driving.
- how to see it: With meshtastic running on 868, click Detect for 868 on the daemon Hardware page. The probe is spawned with no refusal (no claim check in `probe_hardware`). A test with meshtastic running on a band, asserting `probe_hardware(band, …)` is refused with the holder named, would be red. The radio-side effect was not verified on hardware.
- verifier: CONFIRMED — The line ref is off: hardware.py has 109 lines, and the "NEVER clobbers" docstring is at hardware.py:55-56. `probe_hardware` (service_lifecycle_ops.py:543-565) checks only band, preset and built state. meshtastic (manifest.example.toml:1553-1560) and reticulum (:2935-2942) claim `loraham.radio.<band>` exclusively without holding the daemon instance lock. The Detect form (_stack_settings.html) is not gated either.



## What the plan must contain (≤ 200 lines, tables where possible)
1. **Analysis per finding**: the code path today (file:line), the defect, callers affected, what a test sees today.
2. **The change per finding**: exact function(s), the new behaviour in one sentence, the expected diff size, the
   risk (what working path could break and how the plan rules it out). If two findings share a fix, say so.
3. **Tests**: per finding the test module::name, what it asserts, why it is red before.
4. **Docs/CHANGELOG**: the sentences to change (file:line) and the CHANGELOG line.
5. **Order and commits**: one commit per finding, subject `<id>: <what>`; the order if one depends on another.
6. **Live proof**: whether a row on the Pi 5 is needed (an operator-visible path) and what it would show.
7. **Open questions** for the maintainer, each with your recommendation (≤ 5).
8. **Self-check**: re-read every claim against the code once more; list what you could not verify.
