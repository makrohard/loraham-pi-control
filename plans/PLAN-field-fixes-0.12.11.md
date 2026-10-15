# Plan: field fixes after the 2026-10-13 Pi Zero field test (D1–D4, F136, nits)

Base origin/dev 254bbae3 (all line numbers). Decisions D1–D4: the maintainer's, 2026-10-15. Targets: P1, P2 → 0.12.10
(freeze Fri ≈16:00Z); P3 → 0.12.10 if its live row passes in box window 2, else 0.12.11; P4 → 0.12.11.

## P1 — D2: the skyview reads gpsd's cached sky report (target 0.12.10)
- **Today.** `gps.gpsd_snapshot` (`lhpc/core/gps.py:1018-1158`) opens one gpsd connection per poll, sends
  `?WATCH={"enable":true,"json":true}` (`:1037`) and keeps the last SKY that ARRIVES in its 2.5 s (`:1094-1123`).
  The box's gpsd emits SKY every 5.00 s, so 9 of 20 polls saw none and returned `satellites: []`, which the page
  draws as "no satellites reported".
- **Change.** The request becomes WATCH plus `?POLL;` (one write). gpsd answers a POLL at once with its cached
  latest `tpv` and `sky` per active device. A new `cls == "POLL"` branch feeds each `sky` element through the
  existing SKY branch (moved into a local function, so attribution and parsing stay one code path). Live SKY reports
  later in the window still replace it, as today.
- **Age bound.** A cached SKY is used only when it carries `time` and that time is no more than `NMEA_STALE_S`
  (20 s, `gps.py:556`) before the `time` of the same device's cached TPV in the same POLL. Both times come from the
  receiver, so the host clock plays no part (a field box runs on fake-hwclock until GPS time arrives). Without
  either time, or older, the cached SKY is ignored: today's behaviour, never a stale sky.
- **M1 (e293, read-only, before code):** does a SKY inside POLL carry `time`? (`printf '?WATCH={"enable":true};?POLL;\n'
  | timeout 3 nc 127.0.0.1 2947`). If not, the rule is a no-op there; then I recommend accepting an untimed cached SKY
  only beside a same-device cached TPV with mode ≥ 2 (gpsd keeps sky state only for an active device). Handler decides.
- **Tests** (`tests/stacks/test_gps_monitor.py`, ordinary layer, `fake_gpsd`): the fake gains `poll_reply=`, sent
  only when the request contains `?POLL;` (so the test proves the request is sent). Cases: (a) stream VERSION, DEVICES(one), TPV(mode 3), no SKY; POLL holds a SKY 3 s older than its TPV →
  rule-10 assertion `[s["prn"] for s in snap["satellites"]] == [3, 7, 12]` and `snap["sats_seen"] == 3`
  (red today: `[]` and `None`); (b) cached SKY 25 s older → `satellites == []`; (c) cached SKY without `time` →
  `[]`; (d) a live SKY after the POLL wins (its PRNs).
- **Docs.** `docs/gps.md:49` gains: "Each poll also asks gpsd for its latest sky report, so a sky view gpsd sends
  only every few seconds is not missed; a sky report more than 20 s older than the device's position is not used."
- **CHANGELOG.** "The GPS sky view no longer alternates with "no satellites" on a gpsd receiver that reports its
  satellites only every few seconds."
- **Not in P1:** the direct-NMEA sample (H2, not the box's path) and `gps.js`.
- **Live row.** `lhpc gps --monitor --sats` 20× at 3 s → 0 empty runs while gpsd shows satellites (field: 9/20).

## P2 — D3 + D4: the repeater dashboard's presets and a persistent login-signing secret (target 0.12.10)
One packet, two commits: both items change the same five files (host `config.py`, `repeater.py`, manifest, meshcore.md, CHANGELOG).

**D3 — presets.**
- **Today.** Upstream reads `<storage>/radio-presets.json`, else a path relative to the installed package
  (`openhop_repeater` 9c958e31 `repeater/web/api_endpoints.py:1413-1418`). Its installer copies the file into the
  storage dir (`manage.sh:684, 1241`); LHPC pip-installs into a venv and never places it → "Presets file not found".
- **Change.** A hidden param `presets_file` (`[repeater] presets_file`, default
  `{runtime}/src/openhop-repeater/radio-presets.json`, the pinned checkout) and one plain function
  `install_presets(cfg)` in `repeater.py`, called by the repeater host before upstream starts. It copies the bytes to
  `<state_dir>/radio-presets.json` when they differ (write a temp file in the same dir, then `os.replace`). A
  missing or unreadable source logs one warning and the start goes on: the picker then reports upstream's own error,
  as today. The file follows the pin, because the source checkout follows it.
- **Q1 for the maintainer (found while reading).** The dashboard uses the presets only in Configuration → radio
  edit and the setup wizard: a picked preset fills the form, and Save posts `/api/update_radio_config`, which
  LHPC's proxy denies (404, `manifest.example.toml:2537`; LHPC owns radio settings). After D3 the picker lists and
  fills, but saving is still refused, as before. My recommendation: keep D3 as decided (the error goes away, and the
  presets are visible for reference). The doc says radio changes go through LHPC Settings.
- **Tests.** `lhpc/data/meshcore_host/tests/test_repeater.py`: (a) after `install_presets`,
  `<state_dir>/radio-presets.json` holds the source bytes (rule 2, exact bytes; red today: the function does not
  exist and the file is absent); (b) a missing source raises nothing and leaves no target file.
  `tests/stacks/test_meshcore_mode.py`: a repeater-mode generation writes `[repeater] presets_file` =
  `<runtime>/src/openhop-repeater/radio-presets.json` (config rendering, exact).
- **Docs.** `docs/stacks/meshcore.md`, *Repeater dashboard* paragraph: "The radio presets come from the pinned
  checkout and are copied into `state/openhop/` at each start; a picked preset cannot be saved (radio settings are
  LHPC's: Settings)."
- **CHANGELOG.** "MeshCore repeater dashboard: the radio presets list loads (it reported "Presets file not
  found"); radio settings are still changed in LHPC's Settings."
- **Live row.** Configuration → radio edit lists the pinned presets; no "Presets file not found" in the node log.

**D4 — JWT secret.**
- **Today.** `build_upstream_config` (`meshcore_host/repeater.py:66`) passes `security = {admin_password}` only.
  Upstream mints a random JWT secret at every start and cannot save it (`config_path = None`, `repeater.py:166`;
  upstream `http_server.py:594-626`), so every restart signs every dashboard login out (field: 3 starts, 3 log
  lines). The same secret keys upstream's API-token HMAC (`api_tokens.py:14`); token minting is denied at the proxy.
- **Change.** `meshcore_identity.py`: `REPEATER_JWT_FILENAME = "openhop_repeater_jwt.txt"`; `ensure_password`
  takes the minted length per file (admin 15 as today, JWT 64 letters and digits, about 381 bits); a
  `REPEATER_SECRETS` tuple moves here. `service_params.py`: `_REPEATER_SECRETS` = that tuple, and the admin branch of
  `_controller_secret` (`:1688`) covers both files. Net lines in `service_params.py` ≤ 0: it sits at its budget
  (3380 of 3380, `tests/repo/test_module_budgets.py` on cons/houserules-2-r1). Manifest: a hidden
  `dashboard_jwt_secret` param (`[repeater] jwt_secret`, `secret_file`), and `secret_files` += the file, so
  `clean --purge` removes it. Host: `config.py` reads `jwt_secret` and requires it in the repeater roles like
  `admin_password` (`config.py:262`); `repeater.py` puts it in `security`.
- **Backup set.** `lhpc secrets backup` takes all of `config/secrets/` (`secrets_backup.py:6`, `service_secrets.py:107`):
  the file joins backup and restore with no code change; a test proves it.
- **Tests.** `tests/stacks/test_meshcore_mode.py`: (a) two repeater-mode generations write the same
  `[repeater] jwt_secret`, and the file is 0600 (red today: key absent); (b) chat mode mints no JWT file (extends
  `test_chat_mode_mints_no_repeater_secrets`). `lhpc/data/meshcore_host/tests/test_repeater.py`:
  `conf["repeater"]["security"]["jwt_secret"] == cfg.jwt_secret`, and a repeater-role config without it is a
  `ConfigError`. `tests/core/test_clean_purge_wipe.py`: the file joins `OWNED["meshcore"]`.
  `tests/host/` or `tests/core/` (where the secrets-backup tests live): a backup lists
  `config/secrets/openhop_repeater_jwt.txt` (new invariant for this file; negative = the file absent → not listed).
- **Docs.** `docs/stacks/meshcore.md:15` (identity row) and `:68` ("its key, the dashboard password and the
  dashboard's login-signing secret are minted on the first repeater-mode start and reused").
- **CHANGELOG.** "MeshCore repeater dashboard: a restart no longer signs everyone out; the login-signing secret is kept
  in `config/secrets/` and is part of `lhpc secrets backup`."
- **Live row.** Log in → `lhpc stack restart meshcore` → the open dashboard keeps working without a new login; the
  node log has no "No JWT secret found"; `lhpc secrets backup` names the file.

## P3 — D1: remove LHPC's MeshCore duty guard (target 0.12.10 if box window 2 proves it, else 0.12.11)
- **Today.** `LoRaHAMRadio` (`meshcore_host/loraham_radio.py`) holds the configured % over the last 5 TX:
  parameter/attribute `:163, :196`, deques + comment `:234-241`, range check `:291-294`, wait in `send()`
  `:354-363`, history append `:432-433`, `transmit_wait()` `:1001-1018`. Built in both roles: `app.py:56`,
  `repeater.py:126`. Field: 2,072 delays, max 27.2 s.
- **Change.** Delete all of it, the then-unused `import time` (`:33`) and the two `airtime_dutycycle=` keywords.
  About 46 lines out (estimate from the ranges), nothing added. `repeater.py:82-84` keeps feeding upstream's 60 s budget
  from `airtime`. In the chat role `airtime` then limits nothing: upstream's design (DUTY-GUARD-DIG §2). The
  manifest label becomes "Duty-cycle limit % (repeater modes; enforced by openHop over 60 s)".
- **Tests.** `tests/test_loraham_radio.py`: `test_duty_cycle_pacing_state` (`:364-370`, reads the guard) is
  replaced by: six 50-byte frames (≈ 0.54 s airtime each at the harness radio) sent back-to-back inside
  `asyncio.wait_for(..., 5.0)` → rule-10 assertion `[m is not None for m in metas] == [True] * 6`. Red today: the
  sixth send waits 27.1 s (5 × 0.5417 s at 10 %) → `TimeoutError`. `test_repeater.py:43` (6,000 ms/min) stays: it
  proves upstream's budget is still fed.
- **Budget.** `loraham_radio.py` is not in the module-budget table (service family only); no table value to lower.
- **Docs.** `docs/stacks/meshcore.md:42` → `| airtime | 10 % | duty-cycle limit of the repeater modes, enforced by
  openHop over a 60 s window; the chat mode has none (upstream's design) |`.
- **CHANGELOG.** "MeshCore: the repeater no longer delays its transmissions behind a second, much stricter limit of
  LHPC's own; openHop's budget at the configured percentage is the only one."
- **Live row (box window 2, repeater mode, T-Deck Pro peer connected).** Six back-to-back frames (adverts or
  traces) → no "delaying TX" line; upstream's budget still acts: with `airtime` lowered for the row, "Packet dropped
  at TX time: duty-cycle exceeded" appears, then `airtime` back to 10. 868 only; within ≤ 36 s/h.

## P4 — nits (target 0.12.11; upstream read at openhop_repeater 9c958e31)
- **O4 Ctrl-C.** `lhpc.adapters.cli.main:main` (`:1022-1035`) catches only `ConfigError`, so SIGINT during a
  start prints a raw traceback (rc 130; the state converges). Change: `except KeyboardInterrupt:` → one stderr line
  "interrupted — `lhpc status` shows where it stopped", return 130. Every `finally` has already run by then. Test
  (`tests/cli/`): a service verb stubbed to raise `KeyboardInterrupt` → rc 130 and the command token
  `lhpc status` on stderr, no "Traceback" (red today).
- **146's `-W error` edge.** `testlab/lhpc_testlab/release.py:473-476`: the retry note's `warnings.warn` and
  `record` run after `_pty_launch` but before the `try` whose `finally` calls `_pty_stop`. Change: move those lines
  inside the `try`. Test (`testlab/tests/unit/test_release_attribution.py`): `record` raising on attempt 2 still
  stops the second process (`_pty_stop` spied; red today).
- **MeshChat own-pins wording.** No finding record names its text or screen. **Q2:** send the observation, or it stays listed.
- **F136: already done.** 95782542 is in v0.12.2, with its CHANGELOG line; the NEXT-RUN carry-over is stale. Only a live
  check is left: on e293, `lhpc firewall` names no foreign `nm-shared-wlan0` (re-apply first if the helper is stale).

## Packets, order, files
| packet | files | target |
|---|---|---|
| P1 D2 | gps.py, tests/stacks/conftest.py, tests/stacks/test_gps_monitor.py, docs/gps.md, CHANGELOG | 0.12.10 |
| P2 D3+D4 | meshcore_host/{config,repeater}.py, meshcore_identity.py, service_params.py, manifest, tests (meshcore_host/test_repeater, stacks/test_meshcore_mode, core/test_clean_purge_wipe, the backup test), docs/stacks/meshcore.md, CHANGELOG | 0.12.10 |
| P3 D1 | meshcore_host/{loraham_radio,app,repeater}.py, meshcore_host/tests/test_loraham_radio.py, manifest label, docs/stacks/meshcore.md:42, CHANGELOG | 0.12.10 / 0.12.11 |
| P4 nits | cli/main.py, tests/cli/, testlab release.py + its unit test, CHANGELOG | 0.12.11 |
P2/P3 share `repeater.py`, the manifest and `meshcore.md`, and every packet touches CHANGELOG: P3 rebases onto P2 after
P2 lands. Each packet is far below 90 KB.

**Guardrails.** No new module, class or framework: D2 adds one branch and reuses the SKY parser; D3 adds one function;
D4 reuses `ensure_password`; D1 only deletes. **Questions:** Q1 (D3), Q2 (MeshChat), M1 (a 2-min read-only e293 slot).

## Numbers, measured
| figure | command | output |
|---|---|---|
| base | `git log -1 --format=%H origin/dev` | 254bbae3a6d24d829932f1dcb1a66e7c6b56dc6d |
| service_params.py lines / budget | `wc -l lhpc/core/service_params.py`; `git show origin/cons/houserules-2-r1:tests/repo/test_module_budgets.py` | 3380 / 3380 |
| airtime 50 B at the harness radio (preamble 16); the guard's 6th-frame wait | `_calculate_airtime_ms(50)` via the venv; 5 × 0.5417 s / 0.10 | 541.7 ms; 27.1 s |
| guard references outside the module | `grep -rn 'transmit_wait\|airtime_dutycycle\|_airtime_txt' --include=*.py` | app.py:56, repeater.py:126, tests :365, :370 |
| F136 release | `git tag --contains 95782542 \| head -1` | v0.12.2 |
