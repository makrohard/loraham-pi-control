# CLOUD BRIEF · FIX the trivial code-review findings, group T2 (one Claude Code cloud session)

You fix a fixed list of verified defects in this repository. Scope = exactly the findings below, nothing else: no
refactor, no renaming, no behaviour change beyond removing the defect, no change to files the findings do not
name (except the test file for each fix and, for a generated script, its renderer). Never open a pull request, never
touch `main`, `dev`, `code-review/brief` or any other branch, never tag. No Co-Authored-By or AI-attribution line.

## Base and output
`git fetch origin && git checkout -B <this routine's branch> origin/main`; assert HEAD == c60028f08a87a98373646f4364f7988e5cf6d58d
(v0.11.9) else stop and report. Output: ONE COMMIT PER FINDING on this routine's branch, subject `<id>: <what the fix
does, ≤ 72 chars>`, body = the defect in two lines + the test name; then one last commit adding
`code-review/fix-report-T2.md`; `git push` once at the end.

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

## The report (`code-review/fix-report-T2.md`)
One table: `| id | commit | files | test (module::name) | red-before proven (yes/no) | note |`; then "Skipped"
with the reason per id; then the pytest and ruff summary lines.

## The findings (verified; the verifier's evidence tells you where and what)
Each finding: the reviewer's row, then the verifier's row (verdict, severity, evidence).

### CR5-3
- where: `lhpc/core/service_webserver.py:2279-2296 (`_start_service_after_gate`)` · severity kept S2: success is reported for a console that is not listening on the new URL
- claim: Docstring: prerequisites are "checked and reported truthfully". Summary on success: "nginx enabled + started — console at <url>".
- defect: The result is ok=True whatever the verify `ev` shows. When `lhpc-nginx` is ALREADY active, `systemctl --user enable --now` neither restarts nor reloads it. The config just promoted is therefore NOT loaded, yet start-service reports the console as started at the new URL. It also reports success when no listener exists at all (`remote_listener_matches`/`stack_listener_matches` failed). Only the applied-snapshot record is gated on the listeners (:2289).
- how to see it: `tests/web/test_stackweb.py::test_start_service_starts_an_all_http_config_without_pki` passes with an empty listener set (scope "absent", which fails the match) and still asserts `r.ok`. Real box: change the port, run `start-service` while nginx runs: ok, but the new port is dead.
- verifier: CONFIRMED — service_webserver.py:2285-2296 returns ok=True whatever the verify shows; only `_record_applied` is gated (:2289). FakeSystem scratch (http config), once with no listener and once with nginx already up on an old port: ok=True with `remote_listener_matches` "failed" and scope "absent".

### CR6-1
- where: `lhpc/core/service_firewall.py:747-749` · severity S2 kept: on every AP-managed box, turning the console back to local-only is refu
- claim: `firewall_gate_activation` / `_narrowing_is_console_removal_only` (666-669, 703-717): removing the console's remote ingress is never blocked just because another stack proxy keeps the prospective port set non-empty.
- defect: The "previous" candidate is rebuilt with `allow_cidrs=_norm_cidrs(ws.allowed_cidrs, family)`. On an AP-managed box (`[firewall] ap_enabled`), `_fw_proxy_ingress` (178-179) emitted that ingress with `allow_cidrs=[]`, so the rebuilt hash never equals the receipt's `intent_hash`. Turning the console back to local-only is then refused with "Firewall changes pending" until the operator re-applies the firewall with sudo.
- how to see it: Reproduced: copy `test_narrowing_allowed_only_when_console_removal_is_the_whole_change` (tests/host/test_firewall.py:2378) and add `save_firewall_config(ap_enabled=True, ap_interface="wlan0", ap_cidr="<AP CIDR>")` before the first candidate. `firewall_gate_activation({8445})` returns `(False, "Firewall changes pending …")`.
- verifier: CONFIRMED — service_firewall.py:747-749 rebuilds the console ingress with `_norm_cidrs(...)`, while 178-179 emits `[]` when `ap_enabled`. Scratch copy of the narrowing test plus `save_firewall_config(ap_enabled=True,…)`: the no-AP case passes, the AP case returns (False, "Firewall changes pending …"). Fix: use the same `[] if ap_enabled` rule, ideally through one shared helper

### CR8-1
- where: `lhpc/adapters/web/app.py:1590` · severity S2 kept: a plain Update click on a source-built stack plans a channel switch to 
- claim: `docs/cli.md` (update): "Without `--source`, a binary-installed target stays binary; any other goes to `pinned`. Switching channels is an install". The CLI does this at `cli/main.py:1562`, and `service_maintenance.update` routes a binary selector on a source-installed stack to `binary_install`, noting that this "is an install".
- defect: For every op, including `update`, `/action` falls back to `service.default_channel(sid)`, which is `binary` wherever an artifact is published, whatever the stack is installed from. The **Update** button (`_macros.action_btn`, which sends no `source`) on a stack built from source plans `update(source="binary")`. The confirm page then shows "Binary (prebuilt)" preselected, and Apply replaces the source install with the published binary. The operator asked for an update, not a channel switch.
- how to see it: Spy on `ControllerService.run_action` with `default_channel` → `binary` and `on_binary_channel` → `False`. POST `/action` with `op=update target=daemon` and no `source`: the plan call receives `source="binary"`. The expected value is `"pinned"`, the CLI rule. Reproduced.
- verifier: CONFIRMED — app.py:1588-1590 uses `default_channel(_sid)` for every op. `action_btn` (_macros.html:46-52) sends no `source`, so update → service_maintenance.py:1739-1749 → `binary_install`. The CLI rule is at cli/main.py:1562. The per-component Update (_stack_body.html:167) resolves to the whole stack's channel too. Flask is not installed here, so the web test was not run; the finding is confirmed by reading the code.

### CR4-3
- where: `lhpc/core/service_selfupdate.py:678` · severity kept S3: needs a git timeout or kill after the ref moved
- claim: The prepared journal and its anchor are dropped only when "git never happened" (step 2 checks `head_now == from_head` before dropping, l.596-601).
- defect: On the in-process failure path, the journal and anchor are dropped whenever `res.ok` is False, without re-reading HEAD. The merge/reset/clean run with the 5 s `_LOCAL_TIMEOUT` (selfupdate.py:825-832). A timeout SIGTERMs git: either mid-checkout (partly written worktree, stale `index.lock`, reported as "git refused the fast-forward") or after the ref moved (for example during `gc --auto`). In the second case HEAD == to_head while the config-migration intent and its anchor are deleted for good. The venv sync and unit refresh are also skipped, because `_source_advanced` is False. The helper unit (Nice=10, CPUQuota=150%, idle I/O class) makes slow git on a Zero 2W more likely.
- how to see it: Stub the runner so `merge --ff-only` returns rc 124 after moving HEAD (or patch `_LOCAL_TIMEOUT` very low against a real repo). `_self_update_locked` then leaves no journal while HEAD == to_head and pending defaults are never migrated. A test "failed apply with HEAD at to_head keeps the prepared record" would be red.
- verifier: CONFIRMED — service_selfupdate.py:678-680 drops the journal and anchor on any failed res without re-reading HEAD (step 2 at :596-601 does re-read it). Scratch built on the setup of `test_real_apply_created_journal_migrates_genuine_old_default`, with merge returning rc 124 after it ran: HEAD==to_head and the journal is None. The next apply reports "Already up to date" with migrated=0.

### CR4-4
- where: `lhpc/core/updater_units.py:442` · severity kept S3: needs a non-UTF-8 unit file
- claim: `verify()` "Returns exactly one verdict constant", and `integration()` is "FILE READS ONLY (GET-safe…)".
- defect: `_read_unit` decodes with strict UTF-8. A unit file holding a non-UTF-8 byte raises `UnicodeDecodeError` (a ValueError, not OSError), and `verify` does not catch it (l.531-538). `updater_integration()` raises on the dashboard render (app.py:1090), on the trigger, and on the boot-restore gate (`service_boot_restore._web_integration_proven` → `verify`). Boot restore then ends in a traceback instead of "not canonical".
- how to see it: Reproduced: a unit dir with `lhpc-web.service` = `b"x\xff\n"` makes `integration()` raise `UnicodeDecodeError`. A test "verify returns UNREADABLE for undecodable bytes" would be red.
- verifier: CONFIRMED (boot-restore part overstated) — updater_units.py:452 decodes strictly, and `verify` catches only FileNotFoundError/OSError (:530-536). Scratch: b"x\xff\n" makes `integration()` raise UnicodeDecodeError. The dashboard (app.py:1090) and trigger (service_selfupdate.py:814) do not catch it. Boot restore catches it as a typed "driver failure" (service_boot_restore.py:284-292), not a bare traceback.

### CR4-6
- where: `lhpc/core/service_binary_ops.py:394` · severity kept S3: needs a corrupt stream after the sha check; MeshCom auth stays open unt
- claim: "any failure below restores the old working install"; the open transaction is unwound on failure (l.362-365, 403-405).
- defect: Only `BinaryInstallError` and `OSError` are caught. `tarfile.TarError` (for example "unexpected end of data" when `zstd -dc` dies mid-stream, validate_and_extract l.312/354) and `PathContainmentError` (a ValueError from `paths.under` in `publish`/`displace`) escape. The `finally` removes the staging dir, but the journal stays open and the MeshCom `password_file` stays blanked (l.347). Nothing restores it until a later binary operation calls `binary_recover()`. The operator gets a traceback or 500 instead of the typed result.
- how to see it: Make `bi.validate_and_extract` raise `tarfile.ReadError` in a `binary_install(apply=True)` test with an HMAC stack. The journal is still present afterwards and `password_file` is "". A test "any extraction error unwinds the transaction" would be red.
- verifier: CONFIRMED (TarError half; PathContainmentError half by reading only) — Only (BinaryInstallError, OSError) are caught (service_binary_ops.py:394), and tarfile errors escape `validate_and_extract` (binary_install.py:310-356). Scratch on meshcom with HMAC enabled and `tarfile.ReadError` injected: the exception propagates, the journal stays valid and `password_file == ""`.

### CR4-8
- where: `lhpc/core/source_fs.py:172` · severity corrected S3→nit: only empty hidden dirs leak, and the refusal stays typed
- claim: The capability probe is "a scratch `.lhpc-atomic-probe-*` rename under the held parent fd, cleaned up afterwards".
- defect: Cleanup runs only on success or on `AtomicRenameUnavailable`. If the rename fails with any other OSError, the scratch dir `nonce` is left behind (l.178-179). If `rmdir(nonce-b)` fails, `nonce-b` is left behind. Every later probe on that filesystem (the positive result is not cached) can leak another dir into `src/`.
- how to see it: Patch `_rename_noreplace_at` to raise `OSError(EPERM)`, then call `require_atomic_rename(paths, src_parent)`. A `.lhpc-atomic-probe-*` directory remains under `src/`.
- verifier: CONFIRMED — source_fs.py:172-179: an OSError other than ENOSYS/EINVAL skips the `nonce` cleanup, and a failed rmdir leaves `nonce-b`. Scratch with EPERM injected: two calls left two `.lhpc-atomic-probe-*` dirs (failures are not cached).

### CR6-7
- where: `lhpc/core/firewall_helper.py:1343-1352` · severity S3 kept: the receipt verdict is still "error" (not green); only the detail text 
- claim: The receipt detail says "apply failed at …; previous ruleset restored".
- defect: The return value of `_load_snapshot_live(sysx, old_snap)` (and the rc of `nft destroy` on first install) is ignored. If the restore load fails after the new table was loaded but did not verify, the live table is the new, unverified ruleset while the receipt and log claim the old one was restored. The next checker tick does report a mismatch.
- how to see it: Make `Sys.run` return rc≠0 for the second `nft -f -`, with a verify mismatch on the first. The receipt detail still reads "previous ruleset restored".
- verifier: CONFIRMED — firewall_helper.py:1343-1352: the result of `_load_snapshot_live(sysx, old_snap)` and the rc of `nft destroy` are ignored, yet the detail is always "previous ruleset restored". Fix: check the restore verdict and word the detail to match

### CR6-9
- where: `lhpc/core/firewall_helper.py:672-679; lhpc/core/service_firewall.py:1339-1346` · severity S3 kept: ENOSPC/EIO only. A short write could also rename a truncated file into 
- claim: These are atomic-write helpers (durable temp + rename).
- defect: If `os.write`/`fsync`/`fchmod` raises (ENOSPC, EIO), the `mkstemp` temp file (`.tmp-*`) is never unlinked. It accumulates in `/etc/lhpc` (root) or `config/files/firewall/`. A short `os.write` is not looped either. The reset script's `rmdir /etc/lhpc` then silently keeps the directory.
- how to see it: Make `os.write` raise inside `atomic_write`: the `.tmp-*` file remains in the directory.
- verifier: CONFIRMED — Scratch: with `os.write` raising ENOSPC, both `firewall_helper.atomic_write` (672-679) and `service_firewall._atomic_write_script` (1339-1346) leave `.tmp-*` files behind. Fix: unlink tmp on exception and loop on short writes (both files, same pattern)

### CR7-9
- where: `lhpc/core/jobresult.py:112` · severity kept S3
- claim: module: "Every function is best-effort and NEVER raises (a GET must not 500)"; reads "STRUCTURALLY validated"
- defect: A terminal marker whose `finished_at` is a falsy non-string (`0`, `false`, `[]`) skips the 106-109 check and hits `_TS_RE.match(0)` → TypeError out of `read_results`. The banner caller (service_maintenance.py:1237) swallows it and drops ALL job entries incl. unsafe ones; `_web_unsafe_source_block` (service_lifecycle_ops.py:3339) does not catch it, so the unsafe-source gate raises.
- how to see it: `state/jobresults/a.log.json` = `{"op":"build","state":"done","log":"a.log","attempt_id":"abcdef12","finished_at":0}`; `read_results(P)` raises TypeError (reproduced).
- verifier: CONFIRMED — jobresult.py:112 calls `_TS_RE.match` on a non-str. The 106-109 guard is skipped because the value is falsy. Scratch run: `read_results` raises TypeError for finished_at 0/False/[]. The banner (service_maintenance.py:1237) swallows it and drops every job entry, unsafe ones included. `_web_unsafe_source_block` (service_lifecycle_ops.py:3339, called at 3422) does not catch it, so the web build POST raises.

### CR7-14
- where: `lhpc/core/config.py:1751` · severity S3 kept (needs a corrupt journal; the journal is kept, so nothing is lost, but e
- claim: journal recovery (1714-1716): a malformed journal "is NEVER treated as absent — it blocks"
- defect: `int(rec.get("mode", 0o644))` raises ValueError on a non-numeric mode (a non-str `pre` gives AttributeError in `_atomic_write`) — escapes `set_operator_identity` (catches OSError/ConfigError) after earlier targets may already be restored.
- how to see it: Journal with `"mode": "rw"` → `recover_config_transaction` raises ValueError.
- verifier: CONFIRMED — config.py:1751. Scratch g3/r14.py: journal target 1 is valid and target 2 has mode "rw". recover_config_transaction raises ValueError after target 1 is already restored, and set_operator_identity raises ValueError (it catches only OSError/ConfigError). With "pre": 5, it raises AttributeError.

### CR7-15
- where: `lhpc/core/runtime_fs.py:356-373 (`open_marker_excl`)` · severity S3 kept (needs a transient ENOSPC/EIO; afterwards the operator must clean up by 
- claim: exclusive creation of an owned marker (journal/reservation)
- defect: On a write/fsync failure after the `O_EXCL` create (e.g. ENOSPC), fds are closed but the new leaf is not removed (`create_exclusive_bytes` 239-243 does remove it). The empty/partial leaf then reads as malformed: source-txn journal blocks all source mutation; auto-install reservation reports "already exists"/unsafe — manual cleanup after a transient disk-full.
- how to see it: Inject ENOSPC on `_write_all`; leaf remains, next `open_marker_excl` → FileExistsError.
- verifier: CONFIRMED — runtime_fs.py:356-373: the except path closes the fds but never unlinks the leaf (create_exclusive_bytes 239-243 does). Scratch g3/r15.py: after an ENOSPC injected in _write_all, a 0-byte leaf remains and the next open_marker_excl raises FileExistsError. install.py:1259 then reports an unreadable journal as "recovery-required … (retained)".

### CR8-2
- where: `lhpc/adapters/web/app.py:2125` · severity S3 kept: the form uses `type=number` (_webserver.html:38), so only a non-browser
- claim: `app.py:2061-2064`: expected failures are typed ActionResults, and the last-resort handler only catches stray exceptions. The CLI types `--port` as `int` (`cli/main.py:926`).
- defect: `/webserver/configure` passes the raw form string `port` to `webserver_configure_apply`, and `int(port)` there (`service_webserver.py:497`) raises `ValueError`. The response is the generic "500 Internal error" page, not a typed refusal.
- how to see it: POST `/webserver/configure` with a valid CSRF token and `port=84x3` → status 500 (`ValueError: invalid literal for int()`). Reproduced.
- verifier: CONFIRMED — Scratch script against FakeSystem: `webserver_configure_apply(port="84x3")` raises `ValueError` at service_webserver.py:497. The route at app.py:2120-2133 does not catch it, so the last-resort handler (app.py:2055-2067) returns 500. A fix in the service also covers the CLI.

### CR8-3
- where: `lhpc/adapters/web/app.py:2177` · severity S3 kept (same reason, _stackweb.html:75 is `type=number`)
- claim: Same as CR8-2. The CLI types `webserver proxy --port` as `int` (`cli/main.py:951`).
- defect: `/stacks/<id>/webserver` passes the raw `port` to `stack_web_configure_apply`, and `int(port)` there (`service_webserver.py:1252`) raises → generic 500.
- how to see it: POST `/stacks/meshtastic/webserver` with a valid CSRF token and `port=84x3` → status 500. Reproduced.
- verifier: CONFIRMED — Same scratch script: `stack_web_configure_apply("meshtastic", port="84x3")` → `stack_web_configure` raises `ValueError` at service_webserver.py:1252.

### CR8-4
- where: `lhpc/adapters/cli/main.py:1751` (also `:1748`)` · severity S3 kept
- claim: `main()` docstring (`cli/main.py:989-991`) promises a clean typed failure, never a traceback. `docs/cli.md`: exit codes `0` / `1` / `2`.
- defect: `webserver cert export <label> <path>` and `webserver cert discard-export <label>` call `pki.read_export` / `pki.discard_export` with no guard. Those functions raise `validators.ValidationError` for a label that is not a single path component. `main()` catches only `ConfigError`, so a Python traceback reaches the terminal, including the install's source paths. `cert issue/revoke` wrap the same validation into a typed `ERR` line.
- how to see it: `lhpc webserver cert export 'a/b' out.p12` → traceback ending in `ValidationError: cert label: path separator not allowed`. The same happens for `lhpc webserver cert discard-export 'a/b'`. Reproduced.
- verifier: CONFIRMED — Ran `main(["webserver","cert","export","a/b",<out>])` and `discard-export a/b`: both print a traceback ending in `ValidationError: cert label: path separator not allowed`. `main()` catches only ConfigError (cli/main.py:995), and `pki.read_export`/`discard_export` validate at pki.py:543/554.

## Adversarial self-review before the push (mandatory)
When everything is green, re-read your whole diff once more AS A HOSTILE REVIEWER who will be paid per finding: for every hunk ask what input, timing, caller or platform breaks it; what the old code handled that the new code does not; which test only passes because of the fake; which claim in your report you have not actually run. Fix what you find, re-run the gates, and list in the report what this pass found and changed (or 'nothing').
