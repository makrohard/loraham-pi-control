# CLOUD BRIEF · FIX the trivial code-review findings, group T3 (one Claude Code cloud session)

You fix a fixed list of verified defects in this repository. Scope = exactly the findings below, nothing else: no
refactor, no renaming, no behaviour change beyond removing the defect, no change to files the findings do not
name (except the test file for each fix and, for a generated script, its renderer). Never open a pull request, never
touch `main`, `dev`, `code-review/brief` or any other branch, never tag. No Co-Authored-By or AI-attribution line.

## Base and output
`git fetch origin && git checkout -B <this routine's branch> origin/main`; assert HEAD == c60028f08a87a98373646f4364f7988e5cf6d58d
(v0.11.9) else stop and report. Output: ONE COMMIT PER FINDING on this routine's branch, subject `<id>: <what the fix
does, ≤ 72 chars>`, body = the defect in two lines + the test name; then one last commit adding
`code-review/fix-report-T3.md`; `git push` once at the end.

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

## The report (`code-review/fix-report-T3.md`)
One table: `| id | commit | files | test (module::name) | red-before proven (yes/no) | note |`; then "Skipped"
with the reason per id; then the pytest and ruff summary lines.

## The findings (verified; the verifier's evidence tells you where and what)
Each finding: the reviewer's row, then the verifier's row (verdict, severity, evidence).

### CR9-1
- where: `install.sh:175 (snapshot at :161)` · severity S1 kept: deletes config/secrets and stack app data. Rollback also triggers on or
- claim: Header and the rollback's own message: on failure "remove exactly what we created… leaving a config-only remainder intact", "any pre-existing config left intact".
- defect: `PRE_ENTRIES` comes from `ls -A`, so it is **newline**-separated. It is matched with `case " $PRE_ENTRIES " in *" $_b "*`, which needs a **space** on both sides. With two or more pre-existing entries (the normal uninstall remainder: `config`, `backups`, `.lhpc-root`, `profiles`, `state`), no entry matches, so the rollback runs `rm -rf` on each one. That deletes `config/` (local.toml, secrets.toml, `config/secrets/*` incl. the MeshCore node key) and the stacks' app data in `state/`.
- how to see it: Reproduced in a scratch dir: with `config/ state/ backups/` present before the snapshot, the rollback loop prints RM for all of them. On a box: uninstall (default), then reinstall, and make any step after :183 fail (e.g. `lhpc bootstrap` refusing a malformed remainder `local.toml`, or the identity check at :264). Afterwards `config/` is gone. No test covers rollback over a remainder (`tests/host/test_deploy_scripts.py`).
- verifier: CONFIRMED — Ran a copy of install.sh:161 and :170-176 in a scratch script. With config/ state/ backups/ .lhpc-root present, all four print RM. With only config/ present, it prints KEEP. The cause is that the newline-separated `ls -A` output is matched against space delimiters. Fix: `tr '\n' ' '` the snapshot, or match with `grep -qxF`.

### CR9-4
- where: `install.sh:279-286` · severity S2→S3: needs the renderer or a write to fail part-way after the same venv's Pyth
- claim: "on ANY failure after this point we remove exactly what we created (… + units + link)".
- defect: `CREATED_UNITS` is assigned only after all seven `render_unit … > "$UNIT"` lines have run. If a render fails part-way, `set -e` triggers the rollback with an empty `CREATED_UNITS`. The units already written, and the truncated file that the failing redirect created, stay behind. The next `install.sh` then refuses ("…already exists — a fresh install never overwrites systemd units"), and the stale unit points at a deleted venv.
- how to see it: Make `python -m lhpc.core.updater_units render lhpc-nginx.service` fail (e.g. a broken checkout), run install.sh: `lhpc-web.service`, `lhpc-selfupdate.service`, `lhpc-selfupdate.path` and an empty `lhpc-nginx.service` remain in `~/.config/systemd/user`.
- verifier: CONFIRMED — Ran a copy of install.sh:278-286 plus the rollback in a scratch script, with the 3rd render failing. After rollback, web and helper units remain, plus an empty nginx unit, because `CREATED_UNITS` is set only at :286. Fix: append each unit to `CREATED_UNITS` before its redirect.

### CR9-8
- where: `bootstrap-deps.sh:987` · severity S3 kept
- claim: Header: "Steps that can legitimately fail … are GUARDED — the script runs under `set -e`, so an unguarded one would abort before this summary."
- defect: `systemctl disable --now meshtasticd` is unguarded. If it fails (masked unit, a stop that times out), the script exits with systemctl's code before the deferred verdicts at :993-1006. A failed group grant (7), required swap (4) or failed time source (11) is then never reported, and the caller sees a generic 1.
- how to see it: Packaged meshtasticd unit present and masked (`disable --now` fails), plus `usermod` failing: exit is 1 with no "hardware group membership" message, not 7.
- verifier: CONFIRMED — bootstrap-deps.sh:987 unguarded under `set -euo pipefail` (:54). Scratch g5/cr9_8.sh with a failing `systemctl disable` and _GROUPS_FAILED=1 exits 1, no group message, no exit 7. The "masked unit" trigger itself is unverified (systemd may accept disable of a masked unit); a stop timeout/D-Bus error is the plausible trigger.

### CR9-9
- where: `lhpc/core/gps_bridge.py:772` · severity S3 kept (diagnostic text only)
- claim: Readiness detail tells the operator why the feed is degraded, e.g. "gpsd unreachable (ConnectionRefusedError)".
- defect: After the `except` at :768-769 writes "gpsd unreachable (…)", the code falls through to :772 and overwrites it with "gpsd connection closed". Every connect failure, timeout or refusal reads as "connection closed" in the marker and in status ("source not delivering (…)").
- how to see it: Point `[gps] source = gpsd` at a closed port and run the bridge: `readiness.json` detail is "gpsd connection closed", never "gpsd unreachable". A test asserting the detail after a refused connect would be red.
- verifier: CONFIRMED — gps_bridge.py:768-772. Scratch g5/cr9_9.py against a closed port: degrade calls are ("gpsd unreachable (ConnectionRefusedError)") then ("gpsd connection closed"); degrade (:668-671) overwrites and rewrites the marker immediately.

### CR9-10
- where: `lhpc/core/gps.py:932` · severity S3 kept
- claim: `_gpsd_connect`: "every address attempt" under one deadline.
- defect: `socket.socket(family, …)` is outside the `try`. When an address family is unsupported (IPv6 disabled, e.g. `ipv6.disable=1`, while `localhost` resolves to `::1` first), the `OSError` escapes. The remaining IPv4 addresses are never tried, and the Monitor shows "gpsd unavailable (monitor failed)" (caught at service_params.py:2072).
- how to see it: Use `--source gpsd --host localhost` on a box with IPv6 disabled and `::1 localhost` in /etc/hosts. Or monkeypatch `getaddrinfo` to return `[AF_INET6, AF_INET]` and make `socket(AF_INET6)` raise EAFNOSUPPORT: `gpsd_snapshot` raises instead of connecting via IPv4.
- verifier: CONFIRMED — gps.py:932 `socket.socket(...)` is outside the try at :934. Scratch g5/cr9_10.py (getaddrinfo -> [AF_INET6, AF_INET], AF_INET6 socket raises EAFNOSUPPORT): _gpsd_connect raises OSError(97) instead of connecting via IPv4. Second caller gps.py:1163 has the same exposure.

### CR9-11
- where: `lhpc/core/rflog.py:212 (also :174-180)` · severity S3 kept
- claim: `parse_line`: "anything else is `{key, raw}` only — shown as a raw line, never dropped and never an exception".
- defect: `int(ts)` and `_num()` catch only `ValueError`/`OverflowError`. A JSON line with a non-scalar `timestamp` or `rssi` raises `TypeError`. `parse_lines` (service_lifecycle_ops.py:5592) has no per-line guard, so one such line breaks the whole RF-log view.
- how to see it: `parse_line('{"timestamp": [1]}')` and `parse_line('{"rssi": {}}')` both raise `TypeError` (verified).
- verifier: CONFIRMED — rflog.py:174-180, :210-213. parse_line('{"timestamp": [1]}'), ('{"rssi": {}}'), ('{"snr": [2]}') all raise TypeError; parse_lines (:230) has no guard and app.py:1772/1785 call it unguarded, so the RF view API returns 500.

### CR9-13
- where: `lhpc/core/gps_bridge.py:403-415` · severity S3 kept (needs a checksum-valid but malformed sentence)
- claim: gps.py `_nmea_coord`: a malformed field "must never parse as a different, plausible coordinate" (validates width, minutes < 60, \
- defect: lat\
- how to see it: ≤ 90).
- verifier: CONFIRMED — gps_bridge.py:403-415 vs gps.py:581-605. Checksum-valid GGA: lat `4875.000` -> 49.25 (Monitor: None), `-1207.0` -> 2.45, `487` -> 48.117, `48nan` -> nan, which _record_for (:371) formats as `"lat": nan`, i.e. invalid JSON.

### CR10-7
- where: `demo/lhpc_demo/service.py:533` · severity S3 (kept)
- claim: `start`/`stop` refuse an optional component, "symmetric", so that the demo is "honest instead of reporting" an action it did not do (`:438-445`, `:495-502`).
- defect: `restart` has no such guard. Restarting an optional component (e.g. `meshcore-webui`) resolves to its stack, marks the whole stack running and reports "Restarted meshcore". The component itself stays shown as stopped (`build_snapshot` keeps optional parts STOPPED).
- how to see it: Call `restart("meshcore-webui", apply=True)` on a stopped meshcore: the result is ok and the stack is running, but the component the operator asked for is not.
- verifier: CONFIRMED — demo/lhpc_demo/service.py:533-565 has no optional-component guard, unlike :438-445 and :495-502. Scratch: `start("meshcore-webui")` is refused, but `restart("meshcore-webui", apply=True)` returns ok with "Restarted meshcore", the stack runs, and the snapshot still shows meshcore-webui as STOPPED.

## Adversarial self-review before the push (mandatory)
When everything is green, re-read your whole diff once more AS A HOSTILE REVIEWER who will be paid per finding: for every hunk ask what input, timing, caller or platform breaks it; what the old code handled that the new code does not; which test only passes because of the fake; which claim in your report you have not actually run. Fix what you find, re-run the gates, and list in the report what this pass found and changed (or 'nothing').

## Review-packet header (mandatory wording)
Every review packet you write MUST start with: a request paragraph that names what to judge and the answer form — `| commit | verdict (OK / FINDING) | what |` and a final line GREEN / GREEN WITH NOTES / RED — and the sentence "This file is your whole input: you have no repository access; use no connector, tool or web lookup."
