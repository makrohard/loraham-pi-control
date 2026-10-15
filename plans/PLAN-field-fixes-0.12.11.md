# Plan: field fixes after the 2026-10-13 Pi Zero field test (D1–D4, nits) — v2

Base origin/dev 254bbae3 (all line numbers). Decisions D1–D4: the maintainer's, 2026-10-15. Targets: P1, P2 → 0.12.10
(freeze Fri ≈16:00Z); P3 → 0.12.10 if its live row passes in box window 2, else 0.12.11; P4 → 0.12.11.

## P1 — D2: the sky view keeps each gpsd device's last satellite list (target 0.12.10)
- **Today.** `gps.gpsd_snapshot` (`gps.py:1018-1158`) keeps the last SKY arriving in its 2.5 s read (`:1094-1123`); the
  box's gpsd sends one every 5.00 s, so 9 of 20 polls returned `satellites: []` ("no satellites reported").
- **Why not `?POLL` (measured on e293, gpsd 3.25, NMEA0183, read-only):** POLL's cached `sky` is DOP-only (no
  `satellites`, no `time`), 15 of 15 POLLs over 7 s; satellite SKYs come in one burst, also without `time`.
- **The one rule.** The process remembers the last satellite SKY of each gpsd device (key: host, port, device path)
  with a monotonic timestamp and its `sats_used`/`sats_seen`. A window with a satellite SKY for the resolved device
  (an empty list included) replaces the memo; a window without one reports the memo when it is at most
  `NMEA_STALE_S` (20 s, `gps.py:556`) old, else `[]` as today. No host or receiver clock is used; the request, the
  read window and `gps.js` are unchanged. Process memory: `/api/gps` uses it, a one-shot `lhpc gps --monitor` run
  does not. Only caller: the Monitor (`service_params.py:1933`). Code in progress on cons/field-fixes-p1-r1 (not yet reviewed).
- **Tests** (`tests/stacks/test_gps_monitor.py`, ordinary layer; the fake gpsd now serves every connection in turn):
  (a) satellites, then none → `[s["prn"] for s in r["satellites"]] == [3, 7, 12] and r["sats_seen"] == 3` (red: `[]`);
  (b) a DOP-only SKY keeps the memo (red); (c) older than `NMEA_STALE_S` → `[]`; (d) a new or empty list wins;
  (e) another gpsd port gets none. An autouse fixture in `tests/stacks/conftest.py` clears the memo.
- **Docs** (`gps.md`, gpsd sources): the console keeps each device's last satellite list for up to 20 s; a single
  `lhpc gps --monitor` run shows only its own read. **CHANGELOG.** "The GPS sky view no longer alternates with "no
  satellites reported" when gpsd reports the satellites only every few seconds: the console keeps each receiver's
  last satellite list for up to 20 s."
- **Live row.** `/api/gps` 20× at 3 s → 0 without satellites while gpsd reports them (field 9/20). Direct NMEA: unchanged.

## P2 — D3 + D4: the repeater dashboard's presets and a persistent login-signing secret (target 0.12.10)
One packet, two commits: both change host `config.py`, `repeater.py`, manifest, meshcore.md, CHANGELOG.
**D3 — presets.**
- **Today.** Upstream reads `<storage>/radio-presets.json`, else a path beside the installed package (openhop_repeater
  9c958e31 `api_endpoints.py:1413-1418`); its installer copies the file there (`manage.sh:684, 1241`); LHPC never does.
- **Change.** Hidden param `presets_file` (`[repeater] presets_file`, default
  `{runtime}/src/openhop-repeater/radio-presets.json`) and one function `install_presets(cfg)` in `repeater.py`, run
  before upstream starts: copies the bytes to `<state_dir>/radio-presets.json` when they differ (temp file +
  `os.replace`); a missing source logs one warning and the start goes on. The file follows the pin.
- **Reference only (ruled).** Save of a picked preset posts `/api/update_radio_config`, denied by LHPC's proxy (404,
  `manifest.example.toml:2537`; LHPC owns radio settings); one doc sentence says so.
- **Tests.** `meshcore_host/tests/test_repeater.py`: after `install_presets` the target holds the source bytes (exact;
  red: absent); a missing source raises nothing, writes nothing. `tests/stacks/test_meshcore_mode.py`: repeater-mode
  generation writes `[repeater] presets_file` = `<runtime>/src/openhop-repeater/radio-presets.json`.
- **Docs** (`meshcore.md`, *Repeater dashboard*): "The radio presets come from the pinned checkout, copied into
  `state/openhop/` at each start; a picked preset cannot be saved (radio settings are LHPC's: Settings)."
- **CHANGELOG.** "MeshCore repeater dashboard: the radio presets list loads (it reported "Presets file not found");
  radio settings are still changed in LHPC's Settings." **Live row.** Configuration → radio edit lists the presets;
  no "Presets file not found" in the node log.
**D4 — JWT secret.**
- **Today.** `build_upstream_config` (`repeater.py:66`) passes only `admin_password`; with `config_path = None`
  (`:166`) upstream mints a JWT secret per start and cannot save it (`http_server.py:594-626`): every restart signs
  every dashboard login out (field: 3 of 3). It also keys the API-token HMAC (`api_tokens.py:14`; minting denied).
- **Change.** `meshcore_identity.py`: `REPEATER_JWT_FILENAME = "openhop_repeater_jwt.txt"`; `ensure_password`
  takes the minted length per file (admin 15 as today, JWT 64 letters and digits, about 381 bits); a
  `REPEATER_SECRETS` tuple moves here. `service_params.py`: `_REPEATER_SECRETS` = that tuple, and the admin branch of
  `_controller_secret` (`:1688`) covers both files; `service_params.py` net ≤ 0 (at its budget). Manifest: a hidden
  `dashboard_jwt_secret` param (`[repeater] jwt_secret`, `secret_file`), and `secret_files` += the file, so
  `clean --purge` removes it. Host: `config.py` reads `jwt_secret` and requires it in the repeater roles like
  `admin_password` (`config.py:262`); `repeater.py` puts it in `security`.
- **Tests.** `tests/stacks/test_meshcore_mode.py`: two repeater-mode generations write the same `[repeater] jwt_secret`,
  file 0600 (red: key absent); chat mode mints no JWT file. `meshcore_host/tests/test_repeater.py`: `security`
  carries it; a repeater-role config without it is a `ConfigError`. `tests/core/test_clean_purge_wipe.py`: in
  `OWNED["meshcore"]`. Backup: `lhpc secrets backup` takes all of `config/secrets/` (`service_secrets.py:107`), so no
  code; a test proves a backup lists `config/secrets/openhop_repeater_jwt.txt`.
- **Docs** `meshcore.md:15, :68` (the secret, minted once). **CHANGELOG.** "MeshCore repeater dashboard: a restart no longer signs everyone out; the login-signing secret is kept
  in `config/secrets/` and is part of `lhpc secrets backup`." **Live row.** Log in → `lhpc stack restart meshcore` →
  the dashboard works without a new login; no "No JWT secret found"; `lhpc secrets backup` names the file.

## P3 — D1: remove LHPC's MeshCore duty guard (target 0.12.10 if box window 2 proves it, else 0.12.11)
- **Today.** `LoRaHAMRadio` (`loraham_radio.py`) holds the configured % over the last 5 TX: `:163, :196`, `:234-241`,
  `:291-294`, the wait in `send()` `:354-363`, `:432-433`, `transmit_wait()` `:1001-1018`; built in `app.py:56` and
  `repeater.py:126`. Field: 2,072 delays, max 27.2 s.
- **Change.** Delete all of it, the then-unused `import time` (`:33`) and the two `airtime_dutycycle=` keywords.
  About 46 lines out (estimate from the ranges), nothing added. `repeater.py:82-84` keeps feeding upstream's budget:
  `max_airtime_per_minute = airtime × 600 ms` (10 % → 6,000 ms). Manifest label: "Duty-cycle limit % (repeater modes;
  enforced by openHop over 60 s)". `loraham_radio.py` is not in the budget table: nothing to lower.
- **What enforces instead (pinned openhop_repeater 9c958e31, read).**
  - `repeater/airtime.py:97-120` `AirtimeManager.can_transmit(airtime_ms)`: keeps the TX of the last
    `window_size = 60` s (`:39`) and allows a send only if `window sum + airtime_ms <= max_airtime_per_minute`; with an
    empty window and a frame above the budget it refuses too (`return False, 1.0`). `record_tx` (`:122`) charges it.
  - The authoritative gate is per egress in `engine.schedule_retransmit` under the TX lock (`engine.py:1811`, gate `:1886-1898`; the fan-out path `:1960` calls it per radio):
    `manager.can_transmit(gate_ms)`; on refusal it logs "Packet dropped at TX time: duty-cycle exceeded" and does not
    send; after a send `_charge_egress` (`:1936`, `:2195-2234`) records it. The advisory check in `__call__`
    (`:505-517`) is skipped only for local transmissions, which still meet the TX-time gate.
  - Every repeater TX site reaches that gate: forwards via `__call__`; adverts (`main.py:1914-1917`), room adverts
    (`main.py:1722-1725`, `api_endpoints.py:7327-7331`), trace and text replies (`handler_helpers/trace.py:377`, `text.py:737-745`)
    and the hosted companion (`main.py:975-979, 1228-1229`) go through `router.inject_packet` (`packet_router.py:394`),
    which calls `repeater_handler(..., local_transmission=True)`. The direct `dispatcher.send_packet` calls at those
    sites are fallbacks only when no router exists, which a running daemon always has.
- **The chat role: a deliberate behaviour change.** Our guard is today the only limit in `chat`; afterwards only
  openhop_core's leaky bucket remains, consulted only while client-repeat is on (6e6dcf65 `node/dispatcher.py:202-213`,
  off by default), as in the MeshCore firmware. A chat node has no software duty limit: upstream's design, ruled (D1).
- **Tests.** `tests/test_loraham_radio.py`: `test_duty_cycle_pacing_state` (`:364-370`) becomes six 50-byte frames
  back-to-back inside `asyncio.wait_for(..., 5.0)` → `[m is not None for m in metas] == [True] * 6`; red today: the
  sixth waits 27.1 s (5 × 0.5417 s at 10 %). `test_repeater.py:43` (6,000 ms/min) stays: LHPC still feeds the budget;
  upstream's enforcement is its own code, proven live (part B).
- **Docs.** `docs/stacks/meshcore.md:42` → `| airtime | 10 % | duty-cycle limit of the repeater modes, enforced by
  openHop over a 60 s window; the chat mode has none (upstream's design) |`.
- **CHANGELOG.** "MeshCore: LHPC's own transmit limit is gone, which delayed repeats by up to 27 s; in the repeater
  modes openHop's budget at the configured percentage is the limit, and the chat mode has none, as in the firmware."
- **Live row (box window 2, e293, mode `chat+repeater`, 868, T-Deck Pro on Public as the witness).** Stimulus: one
  invocation of the managed meshcore-cli against the hosted companion, `meshcli -t 127.0.0.1 -p 5000 public "duty 1"
  … public "duty 6"` (six commands back-to-back). Each is injected → TX-time gate → radio.
  - **A** (`airtime` 10 = 6,000 ms/min): six "Retransmitted packet (N bytes, A ms airtime)" lines within ~10 s, six
    `ok` TX rows in `logs/rf-meshcore.log`, zero "Duty-cycle limit: delaying TX" lines, six messages on the T-Deck
    Pro. Expected A ≈ 0.5–0.7 s per frame (field mean 669 ms); Σ ≈ 4 s ≤ 6 s. (v0.12.9: the guard delays the sixth.)
  - **B** (upstream enforces): `airtime` = ceil(A / 600 ms) % (one frame fits the budget, two do not); restart, wait
    60 s (the start advert leaves the window), three messages → expected: message 1 "Retransmitted packet …", 2 and 3
    "Packet dropped at TX time: duty-cycle exceeded" + "Injected packet failed local transmission"; T-Deck Pro shows
    only message 1. Pass: ≥ 1 drop, and the "Retransmitted" airtime in any 60 s ≤ the budget (a forward in the window
    may shift which message drops). Then `airtime` back to 10, restart.
  - Airtime of the row ≤ 6 s on 868, within the ≤ 36 s/band/h rule.

## P4 — nits (target 0.12.11; upstream read at openhop_repeater 9c958e31)
- **O4 Ctrl-C.** `cli/main.py:main` (`:1022-1035`) catches only `ConfigError`: SIGINT in a start prints a raw
  traceback (rc 130; state converges). Change: `except KeyboardInterrupt` → one stderr line naming `lhpc status`, rc
  130 (every `finally` has run). Test (`tests/cli/`): a verb stubbed to raise it → rc 130, `lhpc status` on stderr,
  no "Traceback" (red today).
- **146's `-W error` edge.** `testlab/lhpc_testlab/release.py:473-476`: the retry note's `warnings.warn`/`record`
  run before the `try` whose `finally` calls `_pty_stop`; move them inside. Test (`testlab/tests/unit/
  test_release_attribution.py`): `record` raising on attempt 2 still stops the process (red today).
- **CHANGELOG.** O4: "An `lhpc` command stopped with Ctrl-C prints one line naming `lhpc status` instead of a Python
  traceback (exit status 130, as before)." The testlab edge: none (test tooling, per the brief).
- Out: MeshChat own-pins wording (no observation; maintainer's list); F136 (in v0.12.2, 95782542; live check window 2).

## Packets, order, files
| packet | files | target |
|---|---|---|
| P1 D2 | gps.py, tests/stacks/conftest.py, tests/stacks/test_gps_monitor.py, docs/gps.md, CHANGELOG | 0.12.10 |
| P2 D3+D4 | host config/repeater.py, meshcore_identity.py, service_params.py, manifest, 4 test files, meshcore.md, CHANGELOG | 0.12.10 |
| P3 D1 | meshcore_host/{loraham_radio,app,repeater}.py, meshcore_host/tests/test_loraham_radio.py, manifest label, docs/stacks/meshcore.md:42, CHANGELOG | 0.12.10 / 0.12.11 |
| P4 nits | cli/main.py, tests/cli/, testlab release.py + its unit test, CHANGELOG | 0.12.11 |
P3 rebases onto P2 (shared `repeater.py`, manifest, `meshcore.md`); all touch CHANGELOG; each far below 90 KB.
**Guardrails.** No new module, class or framework: D2 one memo dict (+17 lines in gps.py); D3 one function; D4 reuses
`ensure_password`; D1 only deletes. No open questions.
## Numbers, measured
| figure | command | output |
|---|---|---|
| base | `git log -1 --format=%H origin/dev` | 254bbae3a6d24d829932f1dcb1a66e7c6b56dc6d |
| service_params.py lines / budget | `wc -l lhpc/core/service_params.py`; `git show origin/cons/houserules-2-r1:tests/repo/test_module_budgets.py` | 3380 / 3380 |
| airtime 50 B at the harness radio (preamble 16); the guard's 6th-frame wait | `_calculate_airtime_ms(50)` via the venv; 5 × 0.5417 s / 0.10 | 541.7 ms; 27.1 s |
| M1: POLLs with satellites | python gpsd client on e293, `?POLL;` every 0.5 s for 7 s | 0 of 15 |
| F136 release | `git tag --contains 95782542 \| head -1` | v0.12.2 |
| P1 gps.py change | `git diff --stat origin/dev..1d3a34a1 -- lhpc/core/gps.py` | 18 +++++++++++++++++- (17 net) |

## Changes since v1 (round 1: RED, F1–F4)
| finding | change |
|---|---|
| F1 P1 two policies | one rule: the server-side memo ≤ 20 s (handler's ruling B); M1 measured, POLL out |
| F2 P3 replacement not shown | pinned upstream gate quoted with file:line, every TX site traced; chat-role change stated |
| F3 P3 live row | exact stimulus (six `meshcli public` commands), expected airtime, log sequence, part B value |
| F4 P4 CHANGELOG | O4 line written out; testlab edge: none |
| rulings | F136, MeshChat wording out of the packets; D3 reference-only stated |
