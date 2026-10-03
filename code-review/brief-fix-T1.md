# CLOUD BRIEF · FIX the trivial code-review findings, group T1 (one Claude Code cloud session)

You fix a fixed list of verified defects in this repository. Scope = exactly the findings below, nothing else: no
refactor, no renaming, no behaviour change beyond removing the defect, no change to files the findings do not
name (except the test file for each fix and, for a generated script, its renderer). Never open a pull request, never
touch `main`, `dev`, `code-review/brief` or any other branch, never tag. No Co-Authored-By or AI-attribution line.

## Base and output
`git fetch origin && git checkout -B <this routine's branch> origin/main`; assert HEAD == c60028f08a87a98373646f4364f7988e5cf6d58d
(v0.11.9) else stop and report. Output: ONE COMMIT PER FINDING on this routine's branch, subject `<id>: <what the fix
does, ≤ 72 chars>`, body = the defect in two lines + the test name; then one last commit adding
`code-review/fix-report-T1.md`; `git push` once at the end.

## Rules for every fix (the maintainer's: simple, robust, maintainable)
1. The SIMPLEST change that removes the defect, in the style of the surrounding code (match its idiom and comment
   density). If the simplest correct fix needs more than ~30 changed lines or a new mechanism, DO NOT fix it: write
   "SKIPPED: needs <what>" in the report and move on.
2. Every fix gets a regression test that is RED before and GREEN after (the test the finding names, or the closest
   place where the maintainer would look for it: the existing test module for that file; `tests/README.md` has the
   house rules — read it first). Prove red-before by running the new test against a stash of the fix once, and say so.
3. Run `python -m pytest -q -p no:cacheprovider <the test modules you touched>` (if pytest is missing:
   `pip install -e . pytest` first; if Flask is missing for a web test, `pip install flask`), and `ruff check lhpc tests`.
   All green before the push. Do not run anything needing hardware or root.
4. A generated file (`bootstrap-deps.sh` is rendered by `lhpc/core/deps.py`; check `tools/` and `tests/repo` for the
   freeze/drift checks) is fixed in its renderer and regenerated the way the repository's check expects; the frozen
   checks in `tests/repo/` must stay green.
5. Keep secrets, addresses, call signs and home paths out of code, tests and the report.

## The report (`code-review/fix-report-T1.md`)
One table: `| id | commit | files | test (module::name) | red-before proven (yes/no) | note |`; then "Skipped"
with the reason per id; then the pytest and ruff summary lines.

## The findings (verified; the verifier's evidence tells you where and what)
Each finding: the reviewer's row, then the verifier's row (verdict, severity, evidence).

### CR2-1
- where: `lhpc/core/service_params.py:3338` · severity kept S1: the reset turns HMAC auth off and skips the disable-confirm gate meant 
- claim: `service_hmac._is_hmac_managed_param` / `save_config_bundle` (l.1126): the HMAC `password_file` override is never clearable via generic config ("would silently restore open auth, bypassing the disable-confirm gate"); its ONLY writer is `hmac_set_secret`.
- defect: `reset_config` builds `run_names` from `run_params_for(target)`, which includes `password_file`, and clears it with `update_stack_config`. A Settings "Reset to defaults" (web `/stacks/meshcom/config/reset`, CLI `lhpc config meshcom --reset`) drops the bridge to open auth (default `""`) with no confirm phrase, while `config/secrets/xr_pw` stays on disk.
- how to see it: Reproduced with `FakeSystem`: `hmac_set_secret("meshcom","enable")` → `hmac_status` True; `save_config("meshcom",{"password_file":""})` is refused; `reset_config("meshcom")` returns ok and `hmac_status("meshcom")` is False. A test "reset_config keeps the HMAC-managed password_file" would be red.
- verifier: CONFIRMED — service_params.py:3346-3347 puts `password_file` into the cleared set, because `run_names` comes from the unfiltered `run_params_for` (service_lifecycle_ops.py:4155-4163). FakeSystem scratch: after `hmac_set_secret` enable, `save_config({"password_file":""})` was refused, but `reset_config("meshcom")` returned ok and `hmac_status` read False. The secret file stayed on disk.

### CR1-4
- where: `lhpc/core/service_lifecycle_ops.py:2627` · severity kept S2: the restart-needed signal is lost in a routine client stop
- claim: The restart-required marker is "cleared on a verified stop" because "the stale processes are gone" (2610-2613; `restart_required.py` docstring). The marker records a band.
- defect: `_stop_impl` clears the daemon stack's marker (2627) and retires its known-working candidate (2621) after **any** verified daemon stop, including a single-band stop (`band in ("433","868")`). The daemon instance on the other band keeps running its old configuration. Two routine routes do single-band daemon stops: a client stop that releases its daemon band (2592), and a client restart's stop leg.
- how to see it: Daemon serving 433 and 868 as two instances. Save `hipower_868` (restart mode) while it runs, so the daemon marker shows band 868. Then `lhpc stack stop kiss --yes` (kiss on 433) releases daemon 433, and the marker is deleted. The 868 instance still runs without the permission and the console shows no restart needed.
- verifier: CONFIRMED — `_stop_impl` clears the marker whenever `ok and apply and self.stack(target)` (2618-2627), whatever `band` is. Client release calls `stop(daemon, band=b)` (2590-2592). Scratch: marker `{params:[hipower_868], band:"868"}`; `stop("daemon", apply=True, band="433")` gave ok and `read_marker` → None.

### CR1-5
- where: `lhpc/core/service_lifecycle_ops.py:1624` · severity kept S2: CLI traceback; the `lhpc meshtastic` reconverge lambda raises
- claim: Public entries turn lock contention into a typed refusal. `start`, `stop`, `restart` and the TX test all catch `reslock.ResourceBusy`.
- defect: `poststart()` catches `AdmissionRefused`, `SourceTxnBlocked` and `OSError`/`PathContainmentError`, but not `reslock.ResourceBusy`. Task admission is an exclusive flock (`_acquire_key` → `operation_lock`), so any concurrent task makes `poststart` raise: a CLI traceback, the web's generic error page, and an exception out of the `lhpc meshtastic` reconverge lambda (`cli/main.py:1017`).
- how to see it: Reproduced: another process holds `controller-task-admission` (for example a running web build), then `svc.poststart("meshcom", apply=True)` raises `ResourceBusy` from line 1624.
- verifier: CONFIRMED — `poststart` catches `AdmissionRefused`/`SourceTxnBlocked`/`OSError` (1625-1633); `reslock.ResourceBusy` is not an OSError (reslock.py:36). Scratch: with admission held, `start` returned a typed busy refusal, while `poststart("meshcom", apply=True)` raised `ResourceBusy` from :1624.

### CR1-6
- where: `lhpc/core/service_lifecycle_ops.py:3498` · severity kept S3: needs a write failure after reserve
- claim: `spawn_web_job` returns `(log, admission, reason)`. A failed spawn turns its reservation terminal (3462, 3495, 3501).
- defect: `runtime_fs.write_launcher(...)` (3498) runs after `jobresult.reserve` (3471) and outside any `try`. The `ensure_dir` calls at 3430/3432 are also unguarded. An `OSError`/`PathContainmentError` there escapes `spawn_web_job` (web 500), and the attempt marker stays `starting`. `jobresult.reserve` refuses to supersede that state (`jobresult.py:221`), so every later web Build/Test of the component is "already in progress" until it is recovered or dismissed by hand.
- how to see it: `state/jobs/` not writable, or the disk full, between `reserve` and the launcher write. Then web Build is clicked twice: the first click returns 500, the second is blocked.
- verifier: CONFIRMED (one sub-claim wrong) — `write_launcher` (3498) runs after `jobresult.reserve` (3471) and outside the try at 3473-3495. The state stays `starting`, `reserve` refuses over it (jobresult.py:221), and nothing reaps it. Wrong: the `ensure_dir` calls at 3430/3432 run before reserve, so they cause only a 500, not a stuck reservation.

### U-1
- where: `lhpc/core/service_lifecycle_ops.py:3558-3565` · severity kept S3: unusual trigger, but the effect is worse than stated
- claim: `spawn_web_job`: a failed spawn turns its reservation terminal and releases its admission.
- defect: The secondary-job loop admits `_sec` ("web-job", target) and calls `_launch(fn, _sec)` with no try/finally; if `spawn_fn()` raises (e.g. `write_launcher` OSError) the ExitStack is never closed and the per-target web-job admission lock is held until the process ends.
- how to see it: `write_launcher` raising inside the secondary loop; the next web Build of the same target is refused as busy.
- verifier: PARTLY — Holds: `_launch(fn, _sec)` (service_lifecycle_ops.py:3565) has no try/finally. Wrong: the lock is the global `controller-task-admission` key, not per-target (services.py:2476/2494), and the flock is released once the frame is collected. What leaks is the per-thread count `_held_counts` (services.py:2490). FakeSystem scratch: after the failure, the same worker thread admitted the next task without the flock or the strict checks.

### CR1-8
- where: `lhpc/core/daemon_control.py:326` · severity kept S3: reachable only via the direct `daemon_set` CLI/web; saved profile value
- claim: Typed validation "before persistence and before execution". FREQ deliberately uses an ASCII-only regex because "\d would match Unicode digits" (167).
- defect: Integer keys are validated with `int(value)`, which also accepts `" 5"`, `"+5"`, `"1_0"`, a trailing newline and non-ASCII digits (the POWER live check at 443 does the same). `apply_set` (509-512) then sends the raw upper-cased value, not `canonical_value`, so the bytes on the socket are not the validated token. `daemon_set` (`service_params.py:3466`) passes operator input straight through. Together with CR1-3, the daemon's refusal is reported as "SENT".
- how to see it: `lhpc daemon 433 --set SF=1_0 --yes`: it passes validation, `SET SF=1_0` is written to the CONF socket, and the result reads "SENT (unconfirmed)".
- verifier: CONFIRMED — `validate_set` uses `int(value)` (daemon_control.py:326); `"1_0"`, `" 10"`, `"+10"`, `"10\n"` and Arabic-Indic digits pass. `apply_set` sends `value.upper()`, not `canonical_value` (509-512). Scratch: `SET SF=1_0` reached the socket and was reported "SENT but UNCONFIRMED". No injection (enum keys need an exact match; FREQ uses fullmatch).

### U-2
- where: `lhpc/core/service_lifecycle_ops.py:4038-4048` · severity corrected S3→S2: reachable in normal use (one band's daemon down) and reports a 
- claim: TX-test bands "come from the target's components (or, for the daemon itself, the bands it is serving)".
- defect: The `active_bands()` fallback when `wanted` is empty has no daemon-only guard: a client stack whose configured band is not active gets its TX test redirected to another active band instead of being refused.
- how to see it: TX test on a client configured for a band the daemon does not serve while the other band is active.
- verifier: CONFIRMED — The `active_bands()` fallback at service_lifecycle_ops.py:4045-4046 has no daemon-only guard (the comment at 4036-4038 says daemon only). Scratch: meshcom (433) with only 868 active, `test("meshcom", tx=True, apply=False)` returned ok with plan "band: 868 MHz".

### CR2-5
- where: `lhpc/core/services.py:1693` · severity kept S3: it needs the set-aside to fail after an earlier group was adopted
- claim: `_resolve_switch` (l.1405-1407): on a failed binary→source switch the sources this switch CREATED are removed FIRST, then the binary is restored ("removing them afterwards would delete what the restore just put back").
- defect: On the in-loop failure of `_preserve_replaced_source`, the code calls `binary_recover()` and returns, without `_undo_created_sources(_switch_created)`. Checkouts (and ownership records) adopted for earlier groups of the same switch stay in place, and the artifact's files are restored into or beside them. That is the mixed state `_resolve_switch` exists to prevent.
- how to see it: `lhpc install <binary-stack> --source pinned --yes` with two source groups: the first absent (gets adopted), the second marked for replacement whose set-aside fails (e.g. txn dir unwritable). After that, the first group's new checkout + record remain beside the restored receipt.
- verifier: CONFIRMED — services.py:1689-1694 calls `binary_recover()` and returns without `_undo_created_sources(_switch_created)`. This breaks the undo-before-restore order of `_resolve_switch` (l.1403-1409). The normal failure path at l.1752 goes through `_resolve_switch`. Checked by reading, not reproduced.

### CR7-2
- where: `lhpc/core/service_maintenance.py:2092` · severity kept S2 (prep says "safe to remove" while a build/restart might still run)
- claim: `controller_uninstall_prep` fails closed on unresolved HMAC state; refusal text "running or unresolved/unsafe"
- defect: The check is `hst.get("unsafe") or phase in ("running","interrupted")`. `hmac_apply_status` (service_hmac.py:310/318) returns `phase="unsafe"` (driver or build step MIGHT still run) without a top-level `unsafe` key, and `_hmac_mark_unsafe_orphan` persists that phase. Prep then reports "Quiescent… safe to remove controller state".
- how to see it: `hmac_apply_status` → `{"run_id":"x","phase":"unsafe","steps":[],"derived_unsafe":True}`; `controller_uninstall_prep()` returns ok. No HMAC case in `tests/core/test_uninstall_prep.py`.
- verifier: CONFIRMED — service_maintenance.py:2092 tests only `unsafe` and phase running/interrupted, but service_hmac.py:310/318/799 produce `phase="unsafe"` with no top-level `unsafe` key. Scratch test_r2 monkeypatched `hmac_apply_status` to return phase unsafe: `controller_uninstall_prep()` returned ok=True, "Quiescent… safe to remove controller state".

### CR7-7
- where: `lhpc/core/service_maintenance.py:2486-2487 (match at 2684)` · severity kept S3
- claim: clean docstring (2414): removes "its components' logs + job logs"; plan: "every LHPC-owned trace"
- defect: Prefixes are `install-<sid>` and `build
- how to see it: test
- verifier: CONFIRMED — service_maintenance.py:2486/2684: the stem `start-<cid>.prev` matches neither `== p` nor `p+"-"`, and the `web-` prefix is never listed (web job log name at service_lifecycle_ops.py:3602). The matcher evaluated on these names: start-kiss.prev.log False, web-start-kiss.log False, web-restart-kiss.log False, start-kiss-433.prev.log True.

### CR7-8
- where: `lhpc/core/service_maintenance.py:925-927 (also 955-957)` · severity kept S3
- claim: `graywolf_upstream_update`: "Refuses when not actually behind upstream"; upstream check never claims currency for an unknown installed version
- defect: With no version stamp / built marker, `installed` is "" and `ahead` False → returns ok=True "already at the latest upstream release ()". `lhpc update graywolf --upstream` exits 0.
- how to see it: Fresh root + `state/graywolf-upstream.json` `{"latest":"9.9.9"}`; dry-run and apply both ok.
- verifier: CONFIRMED — `graywolf_upstream_state` (843-844) gives ahead=False when installed is "", and 925-927/955-957 then return ok. Scratch test_r8 wrote `{"latest":"9.9.9"}` to the cache and called `graywolf_upstream_update`: dry-run and apply both returned True, "already at the latest upstream release ()". The check path (900-905) handles the unknown case, but the update path does not.

### CR7-11
- where: `lhpc/core/status.py:459-461` · severity S3 kept (needs main source missing while a sidecar still runs; badge is wrong)
- claim: rollup: a stack whose MAIN is not running while another component runs is "only partially running: degraded"
- defect: Only `main_st is STOPPED` is downgraded. A main reading NOT_INSTALLED (source missing) with a running sidecar: NOT_INSTALLED (2) < RUNNING (3), so the stack badge reads `running`.
- how to see it: Snapshot: main NOT_INSTALLED, sidecar RUNNING → `rollup_states` = `running` (STOPPED main → `degraded`) (reproduced).
- verifier: CONFIRMED — status.py:459-461 downgrades only `main_st is STOPPED`; _SEVERITY (389-397) ranks NOT_INSTALLED 2 below RUNNING 3, and _run_state_for_service returns NOT_INSTALLED for a non-running main with source MISSING/NOT_A_REPO (status.py:380-382), so worst stays RUNNING. Established by code reading; no snapshot repro.

### CR7-16
- where: `lhpc/core/service_maintenance.py:2245 (`uninstall`; also 1600 `confirm_known_working`, 676 `power_action`)` · severity S3 kept (stale preflight can wrongly refuse or misplan; locked recheck is fresh 
- claim: snapshot_memo.py / services.py:599, 882: every public mutating entry is `@invalidates_snapshot`
- defect: `uninstall` is not decorated (`update`, `clean` are): the locked recheck at 2330 (`fresh=True`) re-caches pre-removal state, so same-thread reads after the uninstall see removed sources as installed; the apply preflight (2280) reuses the dry run's snapshot. Locked recheck is still fresh, so not unsafe.
- how to see it: `a = svc.build_snapshot(); svc.uninstall("kiss"); svc.build_snapshot() is a` → True (False for `clean`).
- verifier: PARTLY — uninstall (service_maintenance.py:2245) lacks @invalidates_snapshot. Scratch g3/test_r16d.py: the cache is still set after uninstall(apply=True), but cleared after clean. The finding's repro line is wrong: `build_snapshot() is a` gives False, because the fresh locked recheck replaces the cache. What stays cached is that pre-removal recheck snapshot. confirm_known_working and power_action were not checked.

