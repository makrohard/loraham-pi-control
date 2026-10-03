# PLAN T5 — CI guards and the named missing contract tests (tests/CI only)

Base `integration/0.12.0` + the T2 series on the same branch. Files: `tests/` (incl. `tests/repo`,
`tests/data`), `.github/workflows/ci.yml` (one new job), `docs/maintenance.md` (one sentence per
guard). `lhpc/` and `testlab/` untouched. No mutation testing (the maintainer's rule; the words
"and the mutation job" in the completion criterion are stale — handler's clarification).

## 1. Fakes keep the real signature

Today: 1,725 `monkeypatch.setattr` calls (`ControllerService` 511, `type(svc)` 210, `svc` 158,
module aliases, 32 dotted strings, 10 with a computed name). 760 replace with a lambda, many of
them `lambda *a, **k`. No `autospec` anywhere. A static AST pass resolves about 1,420; the
`svc`/`type(svc)` targets need type guessing, and same-named local defs mis-resolve. It already
finds a stale fake: `tests/core/test_boot_restore.py:581` fakes `start(…, params, daemon_overrides,
file_overrides, …)`, parameters the real `start` (service_lifecycle_ops.py) no longer has. The
risk the brief names (B6) is a permissive fake swallowing a call the real function would refuse.

**Simpler form (guardrail: no framework):** check the CALL, not the fake's text. A ~40-line
wrapper in `tests/conftest.py` replaces `pytest.MonkeyPatch.setattr` for the session. When a lambda
or def is put over an `lhpc.*` function or method (module function; method on its class, bound
with `self`; method on one instance, without), it installs a thin wrapper. The wrapper binds each
call to the REAL `inspect.signature` first, then calls the fake. That is what `create_autospec`
checks, applied to every site at once with no rewrite, whatever the target expression. Values,
classes, callable objects and non-LHPC targets are installed unchanged.

Test: `tests/repo/test_fake_signatures.py`, one case per target form plus the pass-through.
Red-before: without the guard, 4 of 6 fail.

Risk: a test that depends on the extra frame (one does: `test_secrets_backup.py` reads
`inspect.stack()[1]`), or on `getattr(...) is fake`. Ruled out by running every test directory
with the guard. The one frame-reading test is adjusted to look past the wrapper.

## 2. Unit templates frozen

Today: `tests/host/test_updater_units.py::test_unit_bytes_are_the_frozen_render` pins the seven
user units (inline `_UNIT_BYTES_0_1_6`, rendered with a fixed home root). The three firewall units
(`firewall.py:411/439/458`, installed by the sudo apply script) are not pinned at all. No CI step
mentions frozen units.

Change (extend, not duplicate): `tests/repo/test_unit_templates_frozen.py` checks all ten renders
against the checked-in `tests/data/unit-templates.sha256`, with the exact failure message from the
brief. The user units are hashed as the shipped `%h` render, so the file equals
`sha256sum deploy/<unit>`. The inline dict and the old test are removed from tests/host. The
backlog's "Holding the line" sentence names the new test.

Red-before: one comment byte changed in `_WEB`, and one directive in the firewall timer → red.

## 3. The five contract tests (backlog "Contract gaps" 1, 3, 4, 5, 7)

One module each, at the widest seam:

| contract | module | seam → observed effect | red-before mutation |
|---|---|---|---|
| firewall route effects | tests/web/test_firewall_configure_route.py | `POST /firewall/configure` → candidate in `firewall-apply.sh` → helper ruleset (accept/drop per route; recommended preset; a refused submission changes nothing) | route ignores `allow_*` |
| binary-channel switch | tests/install/test_channel_switch_route.py | `POST /action op=install source=…` → the job argv → that CLI command in-process → receipt, MeshCom auth (open on binary, password on source), rollback of a failed switch | failed switch skips `binary_recover` |
| uninstall/clean refusal | tests/web/test_uninstall_clean_running_route.py | confirmed `POST /action` for a running, owned kiss → refused, source + record kept; stopped → removed | the four running checks off |
| hardware setup | tests/web/test_hardware_setup_route.py | `POST /hardware` → the daemon argv the next start spawns (`--radio`/`--hw` per band); unknown setup or no CSRF → no write | the save skipped |
| TX opt-in + test + callsign | tests/web/test_tx_test_route.py | `POST /action op=test-tx`: unconfirmed → nothing sent; confirmed without a valid callsign → nothing sent; valid → one identified frame per READY band | callsign check off; opt-in skipped |

The backlog's gap list loses those five entries.

## 4. CI

`ci.yml`: a new `guards` job (Python 3.13, the same pinned actions and install step as `test`)
runs the two guard modules as their own named check. Both also run inside `test`.
`docs/maintenance.md`: one sentence per guard. Checks run: `tests/repo/test_workflow_shell.py`
and `actionlint` 1.7.7 on ci.yml.

## Open questions (with recommendation)

- Should `guards` become a required check in the GitHub ruleset? That is a repository setting,
  not in the tree. Recommendation: yes, beside `test (3.x)`.
- The static scan's stale-but-harmless fakes (e.g. the `start` fake above) never receive a call
  the real function refuses, so the guard does not flag them. Recommendation: leave them; the
  guard fails the first time one would hide a refused call.
- `lhpc/core/updater_units.py`'s banner names `tests/test_updater_units.py`. That was already
  stale, and it is lhpc/, out of this batch's files. Recommendation: fix it with the next lhpc
  change to that file.
