# Code report: field-fixes-p2 (plan item P2 = field decisions D3 presets + D4 login-signing secret)

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
