# CLOUD BRIEF · PLAN for fix batch B1 = groups G9 + G13 + G4 of loraham-pi-control (one Claude Code cloud session, read-only on the code)

You write the PLAN for a group of verified defects; you change no code. Output = ONE commit on this routine's branch
adding `plans/PLAN-B1.md`. Never touch `main` or `dev`, no pull request. (Attribution lines are stripped
downstream; the file is copied out.) Base: origin/main = e5187f70ae4e81a1081a835be06f84746222c3b6 (v0.11.10). Read `docs/architecture.md` (the safety
invariants), `docs/README.md`, `tests/README.md` (the house rules of the tests) and `docs/maintenance.md` (how a patch
is cut) first.

## The maintainer's rules for every change
Simple, robust, maintainable: the SIMPLEST change that removes the defect; no new mechanism unless the defect cannot be
removed without one (then say so and size it); no behaviour change beyond the defect; a regression test per change that
is RED before and GREEN after, placed where the maintainer would look for it (the existing test module of that file);
docs updated where a sentence becomes untrue (one place per fact); the CHANGELOG line in the operator's words.


# Group G9
## The findings of this group (verified; file:line on e5187f70ae4e81a1081a835be06f84746222c3b6)
**Group context / decisions:** DECIDED CR7-1: the config-journal recovery runs ONCE AT STARTUP (one place — find where the console/CLI starts and which lock it must hold), not in every non-transactional writer; the writers stay as they are.

### CR7-1
- where: `lhpc/core/config.py:1251 (`_write_local_tables`; same at 1972-2029 `save_stack_config`/`update_stack_config`)` · severity corrected S1->S3 (silent revert of a local.toml save, but only once a journal was left beh
- claim: architecture.md "config as a transaction … a mid-write failure rolls back"; `set_operator_identity` says patching before recovery "would resurrect rolled-back data or drop restored keys"
- defect: The non-transactional writers (hardware, gps, boot-restore, webserver, firewall, stackweb, remotes, operator, install, daemon params) take `config_lock` but never call `recover_config_transaction` (only config.py:1788 and service_params.py:1592 do). A save made while a journal is pending is later reverted by the journal's pre-image on the next transaction — the operator's save silently vanishes.
- how to see it: Leave `state/config-txn.json` with a pre-image of `local.toml`; `save_hardware_setup(P, "loraham")` succeeds and `load_config` shows it; run any `apply_config_transaction` (a Settings save) → hardware is back to `unset`.
- verifier: CONFIRMED — Only config.py:1788 and service_params.py:1592 call `recover_config_transaction`. `_write_local_tables` (1251) and save/update_stack_config (1972-2029) skip it. Scratch r1.py left a journal with a pre-image of local.toml, ran `save_hardware_setup(P,"loraham")` (load_config showed loraham), then ran one `apply_config_transaction` on a stack file. local.toml was back to the pre-image and the hardware key was gone.

### CR7-3
- where: `lhpc/core/commands.py:734-737, 771-774, 835-839 (exit 0 at 926)` · severity kept S2
- claim: architecture.md "truthful outcomes"; `poststart(require_all=True)`: `.ok` proves every LHPC-owned value was reasserted
- defect: When the bound main process dies/is replaced during a REQUIRED post-start, the runner skips the remaining steps (`break` / `sys.exit(0)`) and exits 0; service_lifecycle_ops.py:1560 treats `jr.ok` as "required post-start completed" and the component is recorded verified.
- how to see it: Render a launcher with `required=True` bound to a dead pid, once with an exec, tcp_wait, tcp_send step → rc 0 each. `test_bound_runner_skips_send_when_main_gone` checks only "nothing sent", not the rc.
- verifier: CONFIRMED — commands.py:737/774 `break`, then `_flush_results(True)` and a normal exit; 839 `sys.exit(0)`. Scratch r3.py rendered `render_post_launcher(..., binding=<dead pid>, require_all=True)` with one required exec, tcp_wait or tcp_send step: rc 0 in all three cases. service_lifecycle_ops.py:1438-1452/1713 then records VERIFIED. The "main-gone" outcome is only shown in status (4965). Fix: a required step that hits main-gone exits nonzero; the detached optional path must keep exit 0.

### CR7-4
- where: `lhpc/core/config.py:1171-1225 (`update_yaml`)` · severity kept S2 (an advertised escape hatch silently does nothing; affects exotic boards only)
- claim: manifest meshtastic `reset`/`busy`: "an exotic board that GENUINELY has the pin can still set it"; `update_toml`/`update_ini` append an absent declared key so it "must never silently vanish"
- defect: `update_yaml` has no append step: a wanted `(section,key)` with no matching line is dropped. The shipped `bases/meshtasticd.yaml` `Lora:` section has no `Reset`/`Busy` line (the commented examples sit under `Interfaces:`), so a saved Reset/Busy never reaches the generated config while the save reports success.
- how to see it: `update_yaml(<shipped meshtasticd.yaml>, [reset,busy,cs], {...})` → CS updated, no Reset/Busy. `test_meshtastic_exotic_board_can_still_set_reset` uses a stale base that already has `Reset:`.
- verifier: CONFIRMED — config.py:1171-1225 has no step that appends a missing key. The shipped bases/meshtasticd.yaml `Lora:` block has no Reset/Busy line; its commented examples are parsed under section `Interfaces`. Scratch test_r4 ran update_yaml(shipped base, reset=18, busy=20, cs=9) and got only `CS: 9`. The existing test uses STALE_BASE (test_meshtastic_uputronics.py:122).

### CR7-5
- where: `lhpc/core/config.py:1026 (`_ini_scalar`)` · severity kept S2 (IFAC passphrase mismatch, but only with `"` or `\` plus a quote trigger)
- claim: "Render a value the way ConfigObj will read it back"
- defect: When quoting triggers, `\` is doubled and `"` becomes `\"`; ConfigObj does no escape processing, so the value reads back changed (a `"`+`,` value can even become a list). The Reticulum IFAC passphrase arrives unvalidated from secrets via `secret_ref` and can contain these characters → node uses a different passphrase than configured.
- how to see it: Render `pa"ss` → `"pa\"ss"`; ConfigObj reads `pa\"ss`.
- verifier: CONFIRMED — config.py:1024 escapes; ConfigObj 5.x does no unescaping. Scratch run with configobj: `pa"ss` was written as `"pa\"ss"` and read back as `pa\"ss`, and `x", y` read back as a list `['x\\','y"']`. The value comes unvalidated from secrets via secret_ref (service_params.py:3082-3100, manifest ifac_netkey).

### CR7-6
- where: `lhpc/core/commands.py:268 (`display_command`)` · severity kept S2
- claim: interactive components get their copy-paste command "from the same spec" as a start
- defect: `expand_argv(comp.run_argv, comp, None, …)` passes params `None` (manifest defaults only) and no band from the caller (`manual_start_command`, service_lifecycle_ops.py:4816); saved Settings (host, port, JSON, debug) never appear in the printed command.
- how to see it: Save meshcore-cli `port=5005`, `debug=true`; `manual_start_command` prints `-p 5000` and no `-D`.
- verifier: CONFIRMED — commands.py:268 passes params=None, and service_lifecycle_ops.py:4816 passes neither stored params nor a band. Scratch test_r6 saved meshcore `port=5005, debug=true` (`stack_config` shows them). `manual_start_command` printed `-p 5000` with no `-D`.




# Group G13
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




# Group G4
## The findings of this group (verified; file:line on e5187f70ae4e81a1081a835be06f84746222c3b6)
**Group context / decisions:** DECIDED: REFUSE a daemon-param apply (and the TX test) on a band that serves another running stack, with a typed message naming the stack; never clamp silently. U-2 (TX-test fallback) was already fixed in 0.11.10 — check and reuse its band logic.

### CR1-1
- where: `lhpc/core/service_lifecycle_ops.py:2153` · severity corrected S1→S2: values are validated per band and POWER is live-gated (service_params.py:
- claim: Band arbitration: only one app stack per band. `_ensure_daemon` (1975-1984) says a band that already serves ANOTHER running stack "must NOT be reconfigured". `daemon_params_view` (2078) clamps the band to the bands the stack can use.
- defect: `apply_daemon_params` takes `b = _effective_daemon_band(target, band)`, which returns any *active* band the caller asks for. It does not check the target's own bands or the band it runs on. The only other check is `stack_running(sid)`. So the target's whole profile (TXMODE, MODE, FREQ/SF/BW/CR, CAD*, POWER) is pushed live onto a daemon band that may belong to a different stack: TXMODE=DIRECT from voice can land under a MANAGED client, and another stack's frequency and spreading factor can retune a band someone else is using.
- how to see it: Reproduced in a scratch test: `apply_daemon_params("meshcom", "868")` with meshcom (fixed 433) stubbed as running. `daemon_params_view` clamps the band to 433, yet 17 SETs went to `/tmp/loraconf868.sock` and `data.band == "868"`. Reachable by `lhpc config meshcom --band 868 --apply-daemon` and by web POST `/stacks/<id>/daemon-params/apply` with `band=868`. The same applies to a band-switchable stack (kiss, voice) asking for the band it is not on.
- verifier: CONFIRMED — `apply_daemon_params` (service_lifecycle_ops.py:2153) takes `_effective_daemon_band` (service_params.py:217-227), which returns any active band, and has no own-band check unlike `_ensure_daemon` 1975-1984. Scratch: meshcom running, 433+868 active; `daemon_params_view` clamps to 433, yet `apply_daemon_params("meshcom","868")` sent 15 SETs to the 868 socket with `data.band=="868"`.




## What the plan must contain (≤ 200 lines, tables where possible)
1. **Analysis per finding**: the code path today (file:line), the defect, callers affected, what a test sees today.
2. **The change per finding**: exact function(s), the new behaviour in one sentence, the expected diff size, the
   risk (what working path could break and how the plan rules it out). If two findings share a fix, say so.
3. **Tests**: per finding the test module::name, what it asserts, why it is red before.
4. **Docs/CHANGELOG**: the sentences to change (file:line) and the CHANGELOG line.
5. **Order and commits**: one commit per finding, subject `<id>: <what>`, grouped by group in the order given; the order if one depends on another.
6. **Live proof**: whether a row on the Pi 5 is needed (an operator-visible path) and what it would show.
7. **Open questions** for the maintainer, each with your recommendation (≤ 5).
8. **Self-check**: re-read every claim against the code once more; list what you could not verify.

## Commit identity (the maintainer's rule — a direct violation otherwise)
Before your first commit run `git config user.name makrohard` and `git config user.email <the author e-mail of the makrohard commits in this repository: git log -1 --format=%ae --author=makrohard origin/main>`, and commit with that identity; no Co-Authored-By, Claude-Session or any AI-attribution line in any message. After each commit check `git log -1 --format='%an %cn%n%B'` shows makrohard twice and no such line; fix it with `git commit --amend --reset-author --no-edit` before you push.

## The plan-review packet (you build it too)
As a second file in the same commit add `plans/gate1-PLAN-B1.md`: a header for an independent reviewer with NO repository access ("This file is your whole input"; one paragraph per group in product terms — what the defects are and what the plan changes; the reviewer judges per change OK / FINDING on: simplest correct change, could it break a working path, are the tests real (red before), anything missing or riskier than necessary; final GREEN / GREEN WITH NOTES / RED), then the whole plan. No home paths (write $HOME), no e-mail, IPs, call signs, no 'threat/bypass/forge' wording.

## Adversarial self-review before the push (mandatory)
When everything is green, re-read your whole diff once more AS A HOSTILE REVIEWER who will be paid per finding: for every hunk ask what input, timing, caller or platform breaks it; what the old code handled that the new code does not; which test only passes because of the fake; which claim in your report you have not actually run. Fix what you find, re-run the gates, and list in the report what this pass found and changed (or 'nothing').
