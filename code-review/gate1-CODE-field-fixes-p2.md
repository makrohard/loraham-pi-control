# Gate 1: field-fixes-p2 (plan item P2, the repeater dashboard: presets and login-signing secret), v2

**Request.** Judge the two code commits below against the plan's item P2 (quoted). That means: correctness; whether
each red-before test can fail for the defect named; whether behaviour changes only where the plan says; the docs and
CHANGELOG lines; and the report's claims against the diff. The plan is in its own gate-1 rounds; only its P2 section
is implemented here.

This file is your whole input: you have no repository access; use no connector, tool or web lookup.

**Answer form.**

| commit | verdict (OK / FINDING) | what |
|---|---|---|

Then numbered findings (file:line, why it matters, smallest fix), then one final line: GREEN / GREEN WITH NOTES / RED.

Redaction: none applied. The diff contains no home path, LAN address, host name or call sign; it is `git diff`
output verbatim.

## The plan's item P2 (plan v2, verbatim)

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

## Commits (full messages, `git log --reverse --format=%B`)
### 642224c4 field-fixes-p2: the repeater dashboard's radio presets are installed

```text
field-fixes-p2: the repeater dashboard's radio presets are installed

Upstream's dashboard reads <storage>/radio-presets.json (openhop_repeater
9c958e31 api_endpoints.py:1413-1418); its installer copies the file there,
LHPC installs with pip and never did, so the preset picker reported "Presets
file not found". A hidden param `presets_file` points at the pinned checkout's
file; the repeater host copies it into the state dir at each start when it
differs (temp file + os.replace), so it follows the pin. A missing source is
logged and the start goes on. Saving a picked preset stays refused at the
proxy (LHPC owns radio settings); the stack doc says so.

Red before: test_the_pinned_radio_presets_are_placed_where_upstream_reads_them
and test_a_missing_presets_source_neither_fails_the_start_nor_writes fail on
the parent (AttributeError: no install_presets);
test_generation_renders_the_repeater_table_from_one_file fails with
KeyError: 'presets_file'.
```

### c699bd16 field-fixes-p2: the repeater dashboard keeps its logins across a restart

```text
field-fixes-p2: the repeater dashboard keeps its logins across a restart

Upstream signs dashboard logins with a JWT secret it mints at every start
unless the configuration carries one, and it cannot save one because LHPC
gives it no config path (openhop_repeater 9c958e31 http_server.py:594-626),
so every restart signed every dashboard login out (field: 3 of 3 starts).
LHPC now mints the secret once, like the dashboard password: 64 letters and
digits in config/secrets/openhop_repeater_jwt.txt (0600), passed as
[repeater] jwt_secret on every generation and required by the host in the
repeater roles. The file is declared in the stack's secret_files, so
`lhpc clean meshcore --purge` removes it, and it is part of
`lhpc secrets backup`, which takes all of config/secrets/. The chat mode mints
none. service_params.py shrinks by one line (the tuple moves to
meshcore_identity).

Red before, on the parent: test_the_dashboard_login_secret_survives_every_regeneration
fails with KeyError: 'jwt_secret' (the generated config has none);
test_chat_mode_mints_no_repeater_secrets and the declared-secret case with
AttributeError (no REPEATER_JWT_FILENAME); the purge case because the file is
not declared; the host's security dict, loader and repeater-role cases
structurally (TypeError: HostConfig has no jwt_secret field).
```

## The code report (verbatim)

### Code report: field-fixes-p2 (plan item P2 = field decisions D3 presets + D4 login-signing secret)

Branch `cons/field-fixes-p2-r1` on `origin/dev` = `78c9fac7`. Plan: `plans/PLAN-field-fixes-0.12.11.md` v2 (gate 1
GREEN WITH NOTES, round 2), item P2: one packet, two commits, because both items change the same five files.

**v2 for the record:** the second reader found that the D4 commit message named the wrong red for
`test_the_dashboard_login_secret_survives_every_regeneration` (it fails with `KeyError: 'jwt_secret'`, as this report
says). The message is corrected; the code and tests are unchanged (`a81ad09d` → `c699bd16`, same tree).

## Commits

| commit | subject | files | tests (layer; rule-10 assertion) | red-before |
|---|---|---|---|---|
| 642224c4 | field-fixes-p2: the repeater dashboard's radio presets are installed | meshcore_host/repeater.py, meshcore_host/config.py, manifest.example.toml, meshcore_host/tests/test_repeater.py, tests/stacks/test_meshcore_mode.py, docs/stacks/meshcore.md, CHANGELOG.md | in-package `meshcore_host/tests`: `test_the_pinned_radio_presets_are_placed_where_upstream_reads_them` (target bytes == source bytes, exact, also after the source changes); `test_a_missing_presets_source_neither_fails_the_start_nor_writes` (no exception, no target file). Ordinary `tests/stacks`: `test_generation_renders_the_repeater_table_from_one_file` gains `rep["presets_file"] == <runtime>/src/openhop-repeater/radio-presets.json` (rendered config, exact) | yes, on 78c9fac7: the two host tests `AttributeError: … no attribute 'install_presets'`; the generation test `KeyError: 'presets_file'` |
| c699bd16 | field-fixes-p2: the repeater dashboard keeps its logins across a restart | meshcore_identity.py, service_params.py, manifest.example.toml, meshcore_host/config.py, meshcore_host/repeater.py, meshcore_host/tests/test_repeater.py, meshcore_host/tests/test_plugin_manager.py (fixture rows), tests/stacks/test_meshcore_mode.py, tests/core/test_clean_purge_wipe.py, docs/stacks/meshcore.md, CHANGELOG.md | ordinary `tests/stacks`: `test_the_dashboard_login_secret_survives_every_regeneration` (two generations → the same `[repeater] jwt_secret`, 64 alphanumerics, the file holds it at mode 0600); `test_chat_mode_mints_no_repeater_secrets` (+ no JWT file, no `jwt_secret` key). `tests/core`: the purge case and the declared-secret case gain the file. Host: `security == {"admin_password": …, "jwt_secret": …}`; the loader refuses a repeater role without `jwt_secret` and reads it with it | yes, on 642224c4: the generation test `KeyError: 'jwt_secret'` (the behavioural red); the chat and declared cases `AttributeError: no REPEATER_JWT_FILENAME`; the purge case (the file not declared). The host tests are red structurally (`TypeError: HostConfig … 'jwt_secret'`, the field is new): 8 failed, 38 passed |

**Red-before command.** A detached worktree of the parent (78c9fac7 for 642224c4, 642224c4 for c699bd16) received
that commit's test files (`git show <commit>:<file>`). Then `python -m pytest -q -p no:cacheprovider
tests/stacks/test_meshcore_mode.py tests/core/test_clean_purge_wipe.py` with the controller venv, and
`tests/test_repeater.py tests/test_plugin_manager.py` in `lhpc/data/meshcore_host` with an interpreter that holds the
pinned openhop_core 6e6dcf65 and openhop_repeater 9c958e31 (as CI's meshcore-host job). Results: 642224c4's →
`1 failed, 62 passed` and `2 failed, 44 passed`; c699bd16's → `4 failed, 60 passed` and `8 failed, 38 passed`.
The worktrees were reset and removed.

**Green** (on c699bd16): `python -m pytest -q -p no:cacheprovider -n 12 --dist loadfile tests/stacks tests/core
tests/repo tests/web tests/cli` → `5272 passed, 1 skipped in 73.11s`. The host suite `python -m pytest -q
-p no:cacheprovider tests/` in `lhpc/data/meshcore_host` → `139 passed, 3 skipped in 25.89s`. The 3 skips need the
MeshCore client library (`meshcore not importable`), unrelated. `ruff check lhpc testlab` → `All checks passed!`;
`ruff check tests --select F,E9` → `All checks passed!`. `tests/repo` after the last amend → `576 passed, 1 skipped`.

Lanes: none per packet, by the maintainer's rule until Sat 10:00 (the handler runs them on dev per batch).

## HOUSE RULES KEPT (checker output, verbatim)

```text
HOUSE RULES KEPT  origin/dev..HEAD
changelog    +2 bullets (limit 2, each ≤ 3 lines; longest 2), top section 20 lines   OK
docs         1 files, +8 lines; fact "openhop_repeater_jwt.txt" -> docs/stacks/meshcore.md; fact "radio presets come from the pinned checkout" -> docs/stacks/meshcore.md   OK
tests        3 new tests; layers lhpc/data/meshcore_host/tests=in-package tests/core=core tests/stacks=stacks; red-before 2/2   OK
patterns     getsource 0 assert-True 0 source-read 0 sleep 0 startswith 0 network 0   OK
markers      0 without slow   OK
attribution  authors makrohard; trailers 0; words 0   OK
modules      service_params.py 3380->3379 lines (budget 3380), 54->54 public (budget 54); family net -1 lines, +0 public   OK
VERDICT      PASS  (hard 0, warn 0)
```

## Simplicity guardrails

- D3: one plain function (`install_presets`, 19 lines) and one call; one hidden param; one config field.
- D4: no new mechanism. `ensure_password` mints per file from a two-entry length table, and the repeater secrets
  tuple moves into `meshcore_identity`, so `service_params.py` shrinks by one line (3380 → 3379, at its budget of
  3380; the checker's modules line).
- Lines (`wc -l`, before → after): meshcore_identity.py 256 → 266; service_params.py 3380 → 3379;
  manifest.example.toml 3629 → 3646; host config.py 273 → 279; host repeater.py 276 → 298; meshcore.md 174 → 177.
- Dependencies: `install_presets` uses `pathlib.Path`, `os.replace` and the module logger (`repeater.py` already
  imported `os` and `logging`). `ensure_password`: unchanged.

## Deviations

- Plan v2 named "a test proves" that the JWT file is in `lhpc secrets backup`. No second backup test was added. The
  backup takes the whole `config/secrets/`, and its canonical test
  (`tests/core/test_secrets_backup.py::test_backup_holds_exactly_the_members_with_modes_manifest_last`) owns that
  behaviour (tests/README.md rule 3, one owner). The new D4 test pins the half that is D4's: the secret is minted at
  `config/secrets/openhop_repeater_jwt.txt`. Together they prove the claim.
- `test_plugin_manager.py` changed only in two TOML fixtures: a repeater table now needs `jwt_secret`.

## The 6-point block

1. **Contracts.**
   - `install_presets(cfg: HostConfig) -> None` (repeater.py:58). It never raises `OSError`: a missing or unreadable
     source, or an unwritable state dir, logs one warning and returns. An empty `presets_file` returns at once.
     It writes `<state_dir>/radio-presets.json` (temp + `os.replace`) only when the bytes differ. It is called once
     at `_Host.run` start (repeater.py:231), so only in the repeater roles, where `state_dir` is required
     (config.py:271).
   - `ensure_password(paths, filename) -> str` (meshcore_identity.py:256): its contract is unchanged for the admin
     file (15 characters). For `REPEATER_JWT_FILENAME` it mints 64 and validates with the same normalizer (15..128
     printable, no blank). Any other filename raises `KeyError` only at minting; its one caller
     (`_controller_secret`, service_params.py:1688) passes these two.
   - `HostConfig.jwt_secret` is required in the repeater roles (`ConfigError`, config.py:269), like
     `admin_password`.
   - Upgrade: an old host package (not yet rebuilt) reading the new TOML ignores the two new keys; the loader has no
     unknown-key check. The host package is a build input, so the component reads *Build required* until rebuilt.
2. **Invariants + tests.**
   - Secrets 0600, minted once, never replaced: `test_the_dashboard_login_secret_survives_every_regeneration`.
   - A mode with no use for a secret mints none: `test_chat_mode_mints_no_repeater_secrets`.
   - Purge removes every stack-owned secret and nothing else: `test_purge_wipes_what_the_stack_owns_and_nothing_else`.
   - Every minted secret is declared: `test_every_controller_minted_secret_file_is_declared_by_its_stack`.
   - The proxy deny list is unchanged: saving a preset stays refused, which the doc states.
3. **Failure classes.**
   - Real signatures: the host tests call the real `install_presets` and `build_upstream_config`; the controller
     tests the real `write_config_files`.
   - EIO/EACCES on the presets copy: caught as `OSError` and logged (`test_a_missing_presets_source…` drives the
     ENOENT branch); others take the same `except`.
   - Ctrl-C: no cleanup path added; a `.tmp` left by an interrupt is overwritten at the next start.
   - CLI/web/job/boot-restore: all render the config through `write_config_files`, the one mint site.
   - Stacked: P2 shares `repeater.py`, the manifest and `meshcore.md` with P3 (not yet written), and CHANGELOG
     with P1 (landed; the rebase kept both bullets).
4. **Test rules.** Red-before as above. Exact bytes and typed values (config keys, file mode, `ConfigError` message
   match for the new refusal, as the loader test already does for its other refusals). No sleep, no network. No
   private patched.
5. **Directories + ruff.** tests/stacks, tests/core, tests/repo, tests/web, tests/cli: `5272 passed, 1 skipped`;
   the host suite: `139 passed, 3 skipped`; both ruff runs clean. `ensure_password`'s signature is unchanged, so
   no caller grep was needed; `grep -rn 'jwt\|JWT' lhpc/adapters lhpc/core --include=*.py` outside
   meshcore_identity → no hit (the secret is shown nowhere).
6. **Adversarial self-review.** Found and fixed:
   - two overlong doc lines;
   - "so a restart keeps a dashboard login" overclaimed (upstream's tokens still expire after their 60 min) →
     "a restart does not sign a dashboard login out";
   - the upgrade order (old host, new TOML), checked as described in 1.
   Weighed and kept:
   - the presets copy runs at every start (one read and compare; a write only on a change);
   - the JWT secret uses the password normalizer, since 64 alphanumerics fit its 15..128 rule.
   Absolute words checked: "never raises" in 1 is limited to `OSError`, which is what the code catches.

## Numbers, measured

| figure | command | output |
|---|---|---|
| base | `git rev-parse --short 642224c4~1` | 78c9fac7 |
| commits | `git log --format=%h origin/dev..HEAD` | 642224c4, c699bd16 |
| diff | `git diff --stat 78c9fac7 c699bd16 \| tail -1` | 11 files changed, 129 insertions(+), 18 deletions(-) |
| D3 / D4 | `git diff --stat` per commit | 7 files, +59 −2 / 11 files, +70 −16 |
| service_params.py | `wc -l`; checker modules line | 3380 → 3379 (budget 3380) |
| red-before | the commands above | D3: 1 failed / 62 passed; 2 failed / 44 passed · D4: 4 failed / 60 passed; 8 failed / 38 passed |
| green | the commands above | 5272 passed, 1 skipped; host 139 passed, 3 skipped |
| field symptoms | FIELDTEST-E293 §B3, §B4 | "Presets file not found" ×1; "No JWT secret found" at 3 of 3 starts |

## The diff (`git diff 78c9fac7 c699bd16`, verbatim)

```diff
diff --git a/CHANGELOG.md b/CHANGELOG.md
index 4b54a493..ba902936 100644
--- a/CHANGELOG.md
+++ b/CHANGELOG.md
@@ -6,6 +6,10 @@ Fixed
 
 - The GPS sky view no longer alternates with "no satellites reported" when gpsd reports the satellites only
   every few seconds: the console keeps each receiver's last satellite list for up to 20 s.
+- MeshCore repeater dashboard: a restart no longer signs everyone out; the login-signing secret is kept in
+  `config/secrets/` and is part of `lhpc secrets backup`.
+- MeshCore repeater dashboard: the radio presets list loads (it reported "Presets file not found"); radio settings
+  are still changed in LHPC's Settings.
 - The slow-target release check times the download of the Graywolf release the manifest pins and no longer asks
   GitHub's API for the newest one, so the API's rate limit can no longer stop a release candidate (the download itself
   still comes from GitHub).
diff --git a/docs/stacks/meshcore.md b/docs/stacks/meshcore.md
index 6742a272..0d58c61d 100644
--- a/docs/stacks/meshcore.md
+++ b/docs/stacks/meshcore.md
@@ -12,7 +12,7 @@ persistence, readiness and lifecycle. The node never drives SPI or GPIO itself.
 | Build | `lhpc build meshcore`: `.venv` (`--system-site-packages`) → openHop Core → `meshcore_host` → the repeater's closure (`openhop-repeater-constraints.txt`) and checkout. Web UI: a backend venv from `meshcore-webui-constraints.txt`; the frontend is prebuilt package data (no npm). All headless |
 | Run | `.venv/bin/python -m meshcore_host <runtime>/config/files/meshcore.toml` — the same command in every mode |
 | Config | `<runtime>/config/files/meshcore.toml` (0600 — it carries the private key), rendered from `lhpc/data/bases/meshcore.toml` on every start |
-| Identity | `<runtime>/config/secrets/meshcore_identity.key` (0600) → the config's `[identity] key`; repeater: `openhop_repeater_identity.key` + `openhop_repeater_admin.txt`. Updates and `uninstall` keep them; `lhpc clean meshcore --purge` removes them, so a reinstall is a new node |
+| Identity | `<runtime>/config/secrets/meshcore_identity.key` (0600) → the config's `[identity] key`; repeater: `openhop_repeater_identity.key` + `openhop_repeater_admin.txt` + `openhop_repeater_jwt.txt` (the dashboard's login-signing secret). Updates and `uninstall` keep them; `lhpc clean meshcore --purge` removes them, so a reinstall is a new node |
 | Endpoints | Companion TCP `:5000` (chat modes; binds loopback while `meshcore_allow` is `127.0.0.1`, else `0.0.0.0`) · repeater dashboard `127.0.0.1:8000` (repeater modes) · Web UI backend `127.0.0.1:8788` — reached through the LHPC proxy or an [SSH tunnel](../ssh-tunnel.md) |
 | Persistence | chat: `<runtime>/state/meshcore/companion.db` (contacts, channels, routes, prefs, queued messages) · repeater modes: `<runtime>/state/openhop/` (the repeater's own SQLite/RRD; the hosted Companion persists there) · Web UI: `<runtime>/state/meshcore-webui/` (a display cache, never the identity) |
 | Resources | `tcp.port.5000` / `.8000` / `.8788` exclusive · `loraham.daemon-socket.868` consumer · `loraham.profile.868` requirement `MANAGED` · `meshcore.companion-client` exclusive, advisory (webui and cli) |
@@ -48,7 +48,7 @@ persistence, readiness and lifecycle. The node never drives SPI or GPIO itself.
 | `plugins` | on | the repeater's plugin manager — see *Plugins* under [Mode](#mode) |
 
 Controller-owned, not settings rows: the private keys, the dashboard password (Password section), `db`, the GPS
-socket/coordinates, `state_dir`. The daemon-side radio parameters live in [daemon](daemon.md).
+socket/coordinates, `state_dir`, `presets_file`. The daemon-side radio parameters live in [daemon](daemon.md).
 
 ## Mode
 
@@ -65,8 +65,9 @@ meshcore` prints the saved mode plus "restart to apply" while another one runs.
 
 The chat node is the same node (name, key) in `chat` and `chat+repeater`; only the contact store
 differs (`companion.db` vs the repeater's database under `state/openhop/`), and nothing is copied
-between them. The repeater is a distinct MeshCore node: its key and the dashboard password are
-minted on the first repeater-mode start and reused. LHPC owns its radio settings, version and
+between them. The repeater is a distinct MeshCore node: its key, the dashboard password and the
+dashboard's login-signing secret are minted on the first repeater-mode start and reused, so a
+restart does not sign a dashboard login out. LHPC owns its radio settings, version and
 configuration: it gets no config path (an upstream save would aim at
 `/etc/openhop_repeater/config.yaml`, which it cannot create); MQTT, Glass and time-sync are off. Its dashboard shows the daemon link *ok* only while the daemon connection
 is up and, with TX enabled, the MANAGED-TX handshake is complete.
@@ -78,7 +79,9 @@ section. The proxy refuses (404, reads included) every route that changes upstre
 identities, radio settings or LHPC-owned state (`proxy_deny_paths` on the 8000 endpoint). Statistics, packets, neighbours, logs, login and a logged-in
 admin's operational actions pass, and, deliberately, two writing routes: the plugin manager
 (`/api/plugins/…`, including an uploaded wheel) and `/api/sensors_config_update`, which fails
-without change because the repeater has no config file.
+without change because the repeater has no config file. The radio presets come from the pinned checkout and are
+copied into `state/openhop/` at each start; a picked preset cannot be saved there (radio settings are LHPC's:
+Settings).
 
 The pinned openhop_core logs each decrypted over-the-air login, password included, at INFO
 ([openhop-dev/openhop_core#156](https://github.com/openhop-dev/openhop_core/issues/156)).
diff --git a/lhpc/core/meshcore_identity.py b/lhpc/core/meshcore_identity.py
index 7f565fc2..1fd0e727 100644
--- a/lhpc/core/meshcore_identity.py
+++ b/lhpc/core/meshcore_identity.py
@@ -34,6 +34,10 @@ IDENTITY_FILENAME = "meshcore_identity.key"
 # a default and ask for a setup wizard). Same store, same 0600 rules, same minted-once contract.
 REPEATER_IDENTITY_FILENAME = "openhop_repeater_identity.key"
 REPEATER_ADMIN_FILENAME = "openhop_repeater_admin.txt"
+# The dashboard's login-signing (JWT) secret: upstream mints a new one at every start unless it is
+# given one, which signs every dashboard login out; minted once here like the password.
+REPEATER_JWT_FILENAME = "openhop_repeater_jwt.txt"
+REPEATER_SECRETS = (REPEATER_IDENTITY_FILENAME, REPEATER_ADMIN_FILENAME, REPEATER_JWT_FILENAME)
 
 # The pinned `ed25519_wrapper.ED25519_Wrapper` accepts exactly these two, hex-encoded: a
 # 32-byte ed25519 seed, or the 64-byte Meshcore-style (a,RH) key. Any other length raises.
@@ -244,13 +248,19 @@ def _normalize_password(raw: object) -> str:
     return s
 
 
+# Minted length per controller-minted login secret: the admin password fits an over-the-air login;
+# the JWT secret is only ever machine-read (64 letters and digits, about 381 bits).
+_MINTED_LENGTH = {REPEATER_ADMIN_FILENAME: OTA_LOGIN_MAX, REPEATER_JWT_FILENAME: 64}
+
+
 def ensure_password(paths: Paths, filename: str) -> str:
-    """A controller-minted login secret (the openHop dashboard admin password): read the stored
-    one, else mint 15 random letters and digits ONCE and persist it 0600 like the identities. Never
-    replaces a stored value; a lax or garbled file raises, exactly like a bad key."""
-    what = "dashboard password"
+    """A controller-minted login secret (the openHop dashboard admin password or its JWT secret):
+    read the stored one, else mint random letters and digits ONCE (`_MINTED_LENGTH`) and persist it
+    0600 like the identities. Never replaces a stored value; a lax or garbled file raises, exactly
+    like a bad key."""
+    what = "dashboard password" if filename == REPEATER_ADMIN_FILENAME else "dashboard JWT secret"
     stored = _read_secret(paths, filename, normalize=_normalize_password, what=what)
     if stored:
         return stored
-    minted = "".join(_secrets.choice(_ALNUM) for _ in range(OTA_LOGIN_MAX))
+    minted = "".join(_secrets.choice(_ALNUM) for _ in range(_MINTED_LENGTH[filename]))
     return _store(paths, minted, filename, normalize=_normalize_password, what=what)
diff --git a/lhpc/core/service_params.py b/lhpc/core/service_params.py
index f1cdbc55..41edece1 100644
--- a/lhpc/core/service_params.py
+++ b/lhpc/core/service_params.py
@@ -79,8 +79,7 @@ _UNREAD = object()             # restart_marker_payload: read the committed mark
 MESHCORE_COMPONENT = "meshcore-node"
 # The repeater's controller-minted secrets (see meshcore_identity) and the rows that select the
 # node's mode — saved settings only, never per-launch overrides (see _normalize_file_overrides).
-_REPEATER_SECRETS = (_meshcore_identity.REPEATER_IDENTITY_FILENAME,
-                     _meshcore_identity.REPEATER_ADMIN_FILENAME)
+_REPEATER_SECRETS = _meshcore_identity.REPEATER_SECRETS
 _IDENTITY_SECRET = _meshcore_identity.IDENTITY_FILENAME      # the Companion's key
 _POSITION_PARAMS = ("lat", "lon")
 
@@ -1685,7 +1684,7 @@ class ParamsConfigMixin:
             # The repeater is a DISTINCT MeshCore node: its own key, minted once, never adopted
             # from anywhere (nothing before this feature could hold one).
             return meshcore_identity.ensure_identity(self._paths, (), filename=name)
-        if name == meshcore_identity.REPEATER_ADMIN_FILENAME:
+        if name in (meshcore_identity.REPEATER_ADMIN_FILENAME, meshcore_identity.REPEATER_JWT_FILENAME):
             return meshcore_identity.ensure_password(self._paths, name)
         raise ValueError(f"unknown controller secret {name!r}")
 
diff --git a/lhpc/data/manifest.example.toml b/lhpc/data/manifest.example.toml
index 80223158..b04463d9 100644
--- a/lhpc/data/manifest.example.toml
+++ b/lhpc/data/manifest.example.toml
@@ -2170,7 +2170,8 @@ main = "meshcore-node"
   [[stack.component]]
   id = "meshcore-node"
   state_root = "state/meshcore"       # companion contacts + messages: clean --purge removes it
-  secret_files = ["meshcore_identity.key", "openhop_repeater_identity.key", "openhop_repeater_admin.txt"]
+  secret_files = ["meshcore_identity.key", "openhop_repeater_identity.key", "openhop_repeater_admin.txt",
+                  "openhop_repeater_jwt.txt"]
   readiness = "endpoint"
   # The node imports its crypto stack (pycryptodomex/PyNaCl) and restores persisted state
   # before opening TCP 5000; give it a generous window on 512 MB / A53-class hardware so
@@ -2403,12 +2404,28 @@ main = "meshcore-node"
       hidden = true
       secret_file = "openhop_repeater_admin.txt"
       [[stack.component.config_file.param]]
+      name = "dashboard_jwt_secret"
+      key = "jwt_secret"
+      section = "repeater"
+      kind = "str"
+      hidden = true
+      secret_file = "openhop_repeater_jwt.txt"
+      [[stack.component.config_file.param]]
       name = "repeater_state_dir"
       key = "state_dir"
       section = "repeater"
       kind = "str"
       hidden = true
       default = "{runtime}/state/openhop"
+      # Upstream's radio presets (the dashboard's preset picker), copied by the host into the state
+      # dir at each start, where upstream's own installer would put them.
+      [[stack.component.config_file.param]]
+      name = "presets_file"
+      key = "presets_file"
+      section = "repeater"
+      kind = "str"
+      hidden = true
+      default = "{runtime}/src/openhop-repeater/radio-presets.json"
       # The openHop plugin manager (dashboard -> Plugins): upstream's separate process, spawned
       # by the host beside the repeater in the repeater roles. `off` = the dashboard shows its
       # "plugin manager unavailable" banner. Plugins are third-party code; see the stack doc.
diff --git a/lhpc/data/meshcore_host/meshcore_host/config.py b/lhpc/data/meshcore_host/meshcore_host/config.py
index 9e865b4f..7a7557c7 100644
--- a/lhpc/data/meshcore_host/meshcore_host/config.py
+++ b/lhpc/data/meshcore_host/meshcore_host/config.py
@@ -98,7 +98,9 @@ class HostConfig:
     repeater_key: str = ""
     repeater_behaviour: str = "forward"
     dashboard_password: str = ""
+    jwt_secret: str = ""             # the dashboard's login-signing secret, minted by LHPC
     repeater_state_dir: str = ""
+    presets_file: str = ""           # upstream's radio-presets.json in the pinned checkout
     plugins_on: bool = True          # the plugin manager runs beside the repeater
 
     extra: dict = field(default_factory=dict)
@@ -243,7 +245,9 @@ def load_config(path: str | Path) -> HostConfig:
     cfg.repeater_key = _opt(rep, "key", "", str, "repeater")
     cfg.repeater_behaviour = _opt(rep, "behaviour", cfg.repeater_behaviour, str, "repeater")
     cfg.dashboard_password = _opt(rep, "admin_password", "", str, "repeater")
+    cfg.jwt_secret = _opt(rep, "jwt_secret", "", str, "repeater")
     cfg.repeater_state_dir = _opt(rep, "state_dir", "", str, "repeater")
+    cfg.presets_file = _opt(rep, "presets_file", "", str, "repeater")
     if cfg.companion_on:
         if bool(cfg.key_file) == bool(cfg.key):
             raise ConfigError("[identity] exactly one of key_file or key must be set")
@@ -261,6 +265,8 @@ def load_config(path: str | Path) -> HostConfig:
             raise ConfigError("[repeater] behaviour must be one of: " + ", ".join(BEHAVIOURS))
         if not cfg.dashboard_password:
             raise ConfigError("[repeater] admin_password is required in the repeater roles")
+        if not cfg.jwt_secret:
+            raise ConfigError("[repeater] jwt_secret is required in the repeater roles")
         if not cfg.repeater_state_dir:
             raise ConfigError("[repeater] state_dir is required in the repeater roles")
         # Strict literal, like every LHPC switch: only "on" and "off" mean anything — "ON",
diff --git a/lhpc/data/meshcore_host/meshcore_host/repeater.py b/lhpc/data/meshcore_host/meshcore_host/repeater.py
index b20ea1e0..85f972d4 100644
--- a/lhpc/data/meshcore_host/meshcore_host/repeater.py
+++ b/lhpc/data/meshcore_host/meshcore_host/repeater.py
@@ -20,6 +20,7 @@ import os
 
 import asyncio
 import logging
+from pathlib import Path
 from typing import Optional
 
 from .config import DASHBOARD_PORT, HostConfig, ConfigError
@@ -54,6 +55,26 @@ def radio_status(connected: bool, tx_ready: bool, enable_tx: bool) -> str:
     return "ok" if connected and (tx_ready or not enable_tx) else "degraded"
 
 
+def install_presets(cfg: HostConfig) -> None:
+    """Place upstream's radio presets where its dashboard reads them, `<storage>/radio-presets.json`
+    (its installer copies the file there; LHPC installs with pip, which does not). The source is the
+    pinned checkout, so the file follows the pin. Best effort: a missing or unwritable file is
+    logged and the repeater starts anyway; the preset picker then reports upstream's own error."""
+    if not cfg.presets_file:
+        return
+    target = Path(cfg.repeater_state_dir) / "radio-presets.json"
+    try:
+        data = Path(cfg.presets_file).read_bytes()
+        if target.is_file() and target.read_bytes() == data:
+            return
+        target.parent.mkdir(parents=True, exist_ok=True)
+        tmp = target.with_name(target.name + ".tmp")
+        tmp.write_bytes(data)
+        os.replace(tmp, target)
+    except OSError as exc:
+        logger.warning("radio presets not installed (%s); the dashboard's preset picker stays empty", exc)
+
+
 def build_upstream_config(cfg: HostConfig) -> dict:
     """The in-memory configuration upstream's RepeaterDaemon reads. One source of truth (LHPC's
     TOML), translated: radio parameters in upstream's names and units (Hz, dBm), the duty budget
@@ -65,7 +86,7 @@ def build_upstream_config(cfg: HostConfig) -> dict:
             "node_name": cfg.repeater_name,
             "identity_key": seed_bytes(cfg.repeater_key),
             "mode": cfg.repeater_behaviour,
-            "security": {"admin_password": cfg.dashboard_password},
+            "security": {"admin_password": cfg.dashboard_password, "jwt_secret": cfg.jwt_secret},
         },
         # A non-disabled marker: upstream never builds a radio (ours is injected), but its setup
         # wizard reads `radio_type` and would flag a missing one as "setup needed".
@@ -207,6 +228,7 @@ class _Host:
                     self.cfg.mode, self.cfg.repeater_name,
                     self.cfg.name if self.cfg.companion_on else "-",
                     "on" if self.plugins is not None else "off")
+        install_presets(self.cfg)
         # Upstream begins the radios IT builds; an injected one is ours to begin (sync: it
         # schedules the daemon connection manager) and ours to close (async aclose — upstream
         # only calls a sync `cleanup()` when a radio has one).
diff --git a/lhpc/data/meshcore_host/tests/test_plugin_manager.py b/lhpc/data/meshcore_host/tests/test_plugin_manager.py
index 63e6ecc4..48a13b85 100644
--- a/lhpc/data/meshcore_host/tests/test_plugin_manager.py
+++ b/lhpc/data/meshcore_host/tests/test_plugin_manager.py
@@ -410,6 +410,7 @@ name = "Relay 1"
 key = "{KEY64}"
 behaviour = "forward"
 admin_password = "{'x' * 24}"
+jwt_secret = "{'j' * 64}"
 state_dir = "/rt/state/openhop"
 {line}''')
     assert load_config(p).plugins_on is expect
@@ -432,6 +433,7 @@ name = "Relay 1"
 key = "{KEY64}"
 behaviour = "forward"
 admin_password = "{'x' * 24}"
+jwt_secret = "{'j' * 64}"
 state_dir = "/rt/state/openhop"
 plugins = {value}
 ''')
diff --git a/lhpc/data/meshcore_host/tests/test_repeater.py b/lhpc/data/meshcore_host/tests/test_repeater.py
index b7202e88..604d13a4 100644
--- a/lhpc/data/meshcore_host/tests/test_repeater.py
+++ b/lhpc/data/meshcore_host/tests/test_repeater.py
@@ -17,7 +17,8 @@ def _cfg(**over) -> HostConfig:
                      frequency=869618000, bandwidth=62500, spreading_factor=8, coding_rate=8,
                      txpower=14, preamble=16, airtime=10.0, mode="chat+repeater",
                      repeater_name="Relay 1", repeater_key=KEY64, repeater_behaviour="forward",
-                     dashboard_password="x" * 24, repeater_state_dir="/rt/state/openhop")
+                     dashboard_password="x" * 24, jwt_secret="j" * 64,
+                     repeater_state_dir="/rt/state/openhop")
     for k, v in over.items():
         setattr(cfg, k, v)
     return cfg
@@ -36,7 +37,7 @@ def test_upstream_config_is_translated_from_lhpcs_one_file():
     conf = rep.build_upstream_config(_cfg())
     assert conf["repeater"]["node_name"] == "Relay 1"
     assert conf["repeater"]["mode"] == "forward"
-    assert conf["repeater"]["security"]["admin_password"] == "x" * 24
+    assert conf["repeater"]["security"] == {"admin_password": "x" * 24, "jwt_secret": "j" * 64}
     assert conf["radio_type"] == "loraham"                        # non-disabled: no setup wizard
     assert conf["radio"]["preamble_length"] == 16                 # upstream's name for `preamble`
     assert conf["radio"]["frequency"] == 869618000 and conf["radio"]["tx_power"] == 14
@@ -57,6 +58,25 @@ def test_the_chat_node_is_the_one_hosted_companion_only_in_chat_plus_repeater():
     assert rep.build_upstream_config(_cfg(mode="repeater"))["identities"]["companions"] == []
 
 
+def test_the_pinned_radio_presets_are_placed_where_upstream_reads_them(tmp_path):
+    src = tmp_path / "src" / "radio-presets.json"
+    src.parent.mkdir()
+    src.write_bytes(b'{"config": {"suggested_radio_settings": {"entries": []}}}\n')
+    state = tmp_path / "state" / "openhop"                         # absent before the first start
+    cfg = _cfg(presets_file=str(src), repeater_state_dir=str(state))
+    rep.install_presets(cfg)
+    assert (state / "radio-presets.json").read_bytes() == src.read_bytes()
+    src.write_bytes(b'{"moved": "with the pin"}\n')                 # the next start follows the pin
+    rep.install_presets(cfg)
+    assert (state / "radio-presets.json").read_bytes() == b'{"moved": "with the pin"}\n'
+
+
+def test_a_missing_presets_source_neither_fails_the_start_nor_writes(tmp_path):
+    state = tmp_path / "state" / "openhop"
+    rep.install_presets(_cfg(presets_file=str(tmp_path / "absent.json"), repeater_state_dir=str(state)))
+    assert not (state / "radio-presets.json").exists()
+
+
 def test_radio_status_follows_the_link_state():
     assert rep.radio_status(True, True, True) == "ok"
     assert rep.radio_status(True, False, True) == "degraded"       # TX enabled, handshake pending
@@ -78,6 +98,7 @@ name = "%s"
 key = "{KEY64}"
 behaviour = "forward"
 admin_password = "{'x' * 24}"
+jwt_secret = "{'j' * 64}"
 state_dir = "/rt/state/openhop"
 '''
     chat_key = f'key = "{SEED}"'
@@ -90,6 +111,10 @@ state_dir = "/rt/state/openhop"
     p.write_text(base % ("", "repeater", "Relay 1"))
     cfg = load_config(p)
     assert cfg.repeater_on and not cfg.companion_on and cfg.repeater_name == "Relay 1"
+    assert cfg.jwt_secret == "j" * 64
+    p.write_text((base % ("", "repeater", "Relay 1")).replace(f'jwt_secret = "{"j" * 64}"\n', ""))
+    with pytest.raises(Exception, match="jwt_secret is required"):
+        load_config(p)
     p.write_text(base % ("", "bogus", "Relay 1"))
     with pytest.raises(Exception, match="role must be one of"):
         load_config(p)
diff --git a/tests/core/test_clean_purge_wipe.py b/tests/core/test_clean_purge_wipe.py
index 0ef4f86d..083589e0 100644
--- a/tests/core/test_clean_purge_wipe.py
+++ b/tests/core/test_clean_purge_wipe.py
@@ -26,6 +26,7 @@ OWNED = {
                  "state/openhop/repeater.db", "config/secrets/meshcore_identity.key",
                  "config/secrets/openhop_repeater_identity.key",
                  "config/secrets/openhop_repeater_admin.txt",
+                 "config/secrets/openhop_repeater_jwt.txt",
                  "config/secrets/meshcore_webui_vapid.pem", "config/files/meshcore.toml"],
     "reticulum": ["state/reticulum/storage/transport_identity", "state/reticulum/config",
                   "state/meshchat/identity", "state/nomadnet/storage/identity",
@@ -136,7 +137,7 @@ def test_every_controller_minted_secret_file_is_declared_by_its_stack(tmp_path):
                 assert c.ui_password_file.split("/")[-1] in decl[st.id][0], c.id
     from lhpc.core import meshcore_identity as mi, service_hmac
     assert {mi.IDENTITY_FILENAME, mi.REPEATER_IDENTITY_FILENAME,
-            mi.REPEATER_ADMIN_FILENAME} <= decl["meshcore"][0]
+            mi.REPEATER_ADMIN_FILENAME, mi.REPEATER_JWT_FILENAME} <= decl["meshcore"][0]
     assert service_hmac._XR_PW[-1] in decl["meshcom"][0]
 
 
diff --git a/tests/stacks/test_meshcore_mode.py b/tests/stacks/test_meshcore_mode.py
index 29c13ec7..2aec4ac6 100644
--- a/tests/stacks/test_meshcore_mode.py
+++ b/tests/stacks/test_meshcore_mode.py
@@ -5,6 +5,8 @@ from __future__ import annotations
 
 import os
 
+import stat
+
 import pytest
 
 from lhpc.core import meshcore_identity as mi
@@ -160,9 +162,11 @@ def test_chat_mode_mints_no_repeater_secrets(tmp_path):
     assert (secrets / mi.IDENTITY_FILENAME).exists()
     assert not (secrets / mi.REPEATER_IDENTITY_FILENAME).exists()
     assert not (secrets / mi.REPEATER_ADMIN_FILENAME).exists()
+    assert not (secrets / mi.REPEATER_JWT_FILENAME).exists()
     import tomllib
     rep_tbl = tomllib.loads((tmp_path / "config" / "files" / "meshcore.toml").read_text())["repeater"]
     assert "key" not in rep_tbl and "admin_password" not in rep_tbl and rep_tbl["role"] == "chat"
+    assert "jwt_secret" not in rep_tbl
 
 
 def test_a_build_needs_the_repeater_checkout_and_names_the_remedy(tmp_path):
@@ -234,9 +238,27 @@ def test_generation_renders_the_repeater_table_from_one_file(tmp_path):
     assert len(rep["key"]) == 64 and rep["key"] != doc["identity"]["key"]
     assert len(rep["admin_password"]) == 15
     assert rep["state_dir"] == str(tmp_path / "state" / "openhop")
+    assert rep["presets_file"] == str(tmp_path / "src" / "openhop-repeater" / "radio-presets.json")
     assert doc["companion"]["name"] == "Chat 1"
 
 
+def test_the_dashboard_login_secret_survives_every_regeneration(tmp_path):
+    # Upstream mints a new JWT signing secret at every start unless it is given one, which signs every
+    # dashboard login out; LHPC mints it once under config/secrets/ (so purge and the secrets backup
+    # take it, like the password) and passes the same value on every generation.
+    import tomllib
+    svc = _svc(tmp_path)
+    _set_mode(svc, "repeater", name="Relay 1")
+    toml = tmp_path / "config" / "files" / "meshcore.toml"
+    svc.write_config_files(mm.STACK_ID)
+    first = tomllib.loads(toml.read_text())["repeater"]["jwt_secret"]
+    svc.write_config_files(mm.STACK_ID)
+    assert tomllib.loads(toml.read_text())["repeater"]["jwt_secret"] == first
+    assert len(first) == 64 and first.isalnum()
+    stored = tmp_path / "config" / "secrets" / mi.REPEATER_JWT_FILENAME
+    assert stored.read_text().strip() == first and stat.S_IMODE(stored.stat().st_mode) == 0o600
+
+
 # --- the box's position ------------------------------------------------------------------------------
 
 def test_repeater_only_consumes_no_position(tmp_path, monkeypatch):
```
