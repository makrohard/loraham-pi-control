# PLAN G2 — `reset_config` as one config transaction (CR2-2, CR2-3, CR2-4)

Base: `e5187f70` (v0.11.10). Read-only analysis; CR2-2 and CR2-4 reproduced on the base in a scratch test
under `tests/core/` (conftest hardware): a reset of running `chat` left `restart_required("chat") is None`;
`reset_config("kiss", "433")` with a malformed `kiss.toml` said "refused, not modified" and `kiss@433.toml`
had lost `rx_only`.

**Recommendation: ONE transactional `reset_config`, not three patches.** It needs no new mechanism. It uses
`apply_config_transaction` (config.py:1774), which `save_config_bundle` and `set_operator_identity` already
use, with renderers called inside the lock, `None` = target withdrawn, and the `"state"` marker target. The
only refactor is a pure move: the `_render_marker` closure (service_params.py:1422-1518) becomes a method
so both callers share it. Three separate fixes do not work: a marker written after `update_stack_config` is
not atomic with the change, which breaks the invariant CR2-2 is about. An in-lock GPS recheck cannot be added
to `update_stack_config`, which takes the lock itself and never nests (config.py:2023-2036). Pre-validating
the band-less file still leaves two separate writes. Done as three, the fixes rebuild this same transaction
in pieces.

## 1. Analysis

| id | path today (file:line) | defect | callers | what a test sees today |
|---|---|---|---|---|
| CR2-2 | `reset_config` service_params.py:3329-3372 clears keys with `update_stack_config` (l.3362), one locked write per file | no restart-marker decision; `_render_marker` exists only inside `save_config_bundle` (l.1422-1518) | CLI `lhpc config <stack> --reset` (cli/main.py:325), web `POST /stacks/<id>/config/reset` (web/app.py:2036) | running chat, saved `file_tx_freq=434.500`, reset → ok, `restart_required("chat") is None`; `lhpc status` shows no RESTART REQUIRED line |
| CR2-3 | `stored` read (l.3345) and the `use_gps` liveness gate (l.3352-3360, already a forced-fresh snapshot) run BEFORE `update_stack_config` takes `config_lock` (config.py:2033) | a start that holds the config lock SHARED (`_config_stable`) and finishes between gate and write runs on the old `use_gps` while the saved value flips | same | the gate runs with the config lock free (a stub of `gps_liveness_blockers` can take `config/.lock` LOCK_EX\|LOCK_NB) |
| CR2-4 | `files = [cfg_band, ""]` (l.3344), each file a separate `update_stack_config` | banded file cleared, then the band-less file raises (`ConfigError`/`OSError`/`PathContainmentError`) or the use_gps gate returns (l.3356). The banded clear stays, and the result says "refused, not modified" (l.3366) | same, band-switchable stacks only | malformed `kiss.toml` + `kiss@433.toml` `rx_only="on"` → `not ok`, `rx_only` gone |

Scoped `__r__<comp>__use_gps` (part of the CR2-3 claim) has no effect: no GPS reader uses it (verifier, l.1264-1267).
That part is not planned.

## 2. The change

All three changes are in `lhpc/core/service_params.py`. CR2-3 and CR2-2 build on the transaction that CR2-4
adds.

**CR2-4 · `reset_config` becomes one `apply_config_transaction`.** New behaviour: both files (banded and
band-less) are rendered inside one config lock and then written, or none is written.
- Per file `b` in `files`, one target `("stack", _stack_config_path(paths, target, b), _render_reset(b), 0o644)`.
  `_render_reset` reads the LATEST file through `merge_stack_values(paths, target, b, {})` (in the lock). It
  drops the keys that match today's predicate (unchanged: `k in run_names or k.startswith(("file_",
  "autostart_", "__r__", "__f__"))`) and adds them to a `cleared` list. If none match it returns `None`, so
  the file is not written and an absent file is not created. Otherwise it returns
  `render_stack_config(target, merged)`.
- In this commit the use_gps gate stays before the lock, but it runs for BOTH files before the
  transaction: a refusal now leaves both files untouched.
- `if cleared: self._invalidate_config()` after the commit. The result texts stay as they are.
- Diff about +25/-15. Risk: preserving `dp_*` or manual scalars (the renderer only pops matched keys),
  password_file (`run_names` is unchanged), the idempotent "already at defaults" result (`cleared` stays
  empty because both renderers return None). A symlinked leaf is still refused, now by the transaction's
  `is_symlink` check (config.py:1799). Existing tests that cover this: `test_reset_*` in
  tests/core/test_config.py, test_hmac.py:661, test_identity.py:191-193.

**CR2-3 · the use_gps gate moves INTO `_render_reset`.** New behaviour: the same predicate as today
(`"use_gps" in normal and value != use_gps_default(...)`, on the value just read in the lock) and the same
`gps_liveness_blockers([target], snap=self._gps_fresh_snapshot())` run under the exclusive config lock.
Blockers make the renderer raise `ConfigError`, and the transaction rolls back.
- To keep today's refusal text, the renderer stores the blockers in a local dict (`_gps_busy["v"]`, the
  `_live_seen` idiom from l.1214) before it raises. The `except ConfigError` branch checks that dict first.
  There is no new reason token. The pre-lock gate is removed: one check, in the lock.
- Diff about +10/-12. Risk: a start waiting on the lock now completes after the reset, so it reads the
  reset value. That is intended. A snapshot that cannot be built (`None`) is still a blocker (fail-closed,
  unchanged).

**CR2-2 · the reset carries the restart marker.** New behaviour: a reset that changes restart- or build-mode
params of a live consumer writes the marker in the same transaction. If the marker cannot be written,
nothing is reset.
- Move `_render_marker` (l.1422-1518) out of `save_config_bundle` into
  `_restart_marker_target(self, target, sid, band, cfg_band, params, modes, live_seen)`. It returns the
  `("state", _rr.marker_path(...), renderer, 0o600)` tuple. `save_config_bundle` calls it with its
  `modes`/`_live_seen`, and the body is moved byte for byte.
- `reset_config` appends `self._restart_marker_target(target, target, band, cfg_band, params, modes,
  live_seen)` with `params = [(kind, c, p, post)]`, built from EVERY form param of the stack:
  `self._form_run_params(c)` as `"r"`, `c.config_file.params` as `"f"`. `post` is `"" if p.validator in
  self._IDENTITY_ENFORCE else self._param_default_canon(p, cfg_band, band)`, the same canon rule as
  bundle l.1250-1251. The renderer already compares pre- and post-effective values under the lock, so an
  unstored param is "unchanged". It also handles restore-to-launched, the other-band case
  (`_BANDLESS_STACK_PARAMS` only) and the forced-fresh `active_config_consumer`.
- Diff: about 95 lines moved, about +12 net. Risk: bundle marker behaviour. Ruled out because the move is
  verbatim and the test_restart_required.py and test_identity.py marker tests run unchanged.
  `use_gps`/`rf_log` defaults come from `p.default`, which is the source of `use_gps_default`/
  `rflog.switch_default` too, so the reset's post value equals the value readers use.

### Lock order (after CR2-2)

| # | what | lock held |
|---|---|---|
| 1 | `apply_config_transaction` → `config_lock` (`config/.lock`, exclusive flock, bounded 15 s → `ConfigLockBusy`) | — → EX |
| 2 | `recover_config_transaction`, journal pre-images of banded, band-less, marker | EX |
| 3 | render banded → render band-less (each: fresh read, use_gps gate with `build_snapshot(fresh=True)`) → render marker (`active_config_consumer(fresh=True)`) | EX |
| 4 | atomic writes, journal removal; on any raise roll back all three | EX |
| 5 | release; `_invalidate_config()` | — |

This is the one lock `save_config_bundle` takes, and its renderers make the same two snapshot calls under it
(l.1357, l.1497), so the change adds no new lock pair. A start holds the same file SHARED, so it finishes
before step 1 or waits until step 5.

### Refusal texts (summary; `label` = `'kiss' (433)`)

| case | text |
|---|---|
| use_gps in use (in-lock) | `Config reset blocked for 'reticulum': cannot change use_gps while reticulum-node is running (lhpc stack stop reticulum)`: unchanged wording |
| malformed band-less file | `Config reset blocked for 'kiss' (433): unsafe/malformed config (refused, not modified): config transaction failed and was rolled back: …kiss.toml: Expected '=' …`: "not modified" is now true |
| symlinked config or marker leaf | `… (refused, not modified): refusing a symlink-leaf config target: …/state/restart-required/chat.json` |
| lock busy | `… (refused, not modified): config is busy — a long-running operation holds it; try again shortly` (as today) |
| success with marker | `Config reset to defaults for 'chat'.`; `lhpc status` then shows `! RESTART REQUIRED: 'chat' — saved settings differ from the running stack (lhpc stack stop chat && lhpc stack start chat)` |

## 3. Tests (each RED on e5187f70, GREEN after its commit)

| id | module::name | asserts | why red before |
|---|---|---|---|
| CR2-4 | tests/core/test_config.py::test_reset_of_one_band_is_refused_whole_on_a_malformed_band_less_file | `kiss@433` `rx_only="on"`, `kiss.toml` invalid TOML; `reset_config("kiss","433")` not ok, "not modified" in summary, `rx_only` still in `kiss@433` | reproduced: banded file already cleared |
| CR2-3 | tests/core/test_config.py::test_reset_config_rechecks_use_gps_under_the_config_lock | reticulum `use_gps="off"`. The stubbed `gps_liveness_blockers` records whether `config/.lock` is held (its own `open` + `LOCK_EX\|LOCK_NB` fails) and returns `["reticulum-node"]`. Assert: lock seen held, reset refused with today's text, file untouched | the gate runs before `update_stack_config` takes the lock |
| CR2-2 | tests/core/test_restart_required.py::test_reset_of_a_running_stack_writes_the_restart_marker | stopped `_svc` saves `file_tx_freq=434.500`; running `_svc(cmdlines={555:["loraham_chat"]})` resets → ok, `_marker(tmp_path)["params"] == ["tx_freq"]`, `"chat" in restart_required_stacks()` | reproduced: marker None |
| CR2-2 | tests/core/test_restart_required.py::test_reset_marker_write_failure_resets_nothing | symlinked marker leaf (as in `test_marker_write_failure_rolls_back_whole_save`, l.135); running reset → not ok, `chat.toml` keeps `file_tx_freq`, target of the symlink unchanged | the reset ignores the marker and clears the file |

Existing tests that must stay green unchanged: `test_reset_config_gates_use_gps_on_running_consumers`
(test_config.py:1226), the stub matches the in-lock call; all of test_restart_required.py and the
test_identity.py marker tests (the moved renderer); test_hmac.py:661; `pytest -q` and `ruff check lhpc`.

## 4. Docs / CHANGELOG

- docs: no sentence becomes untrue. Recommended, in the CR2-2 commit: docs/architecture.md:219,
  "A Settings save validates" → "A Settings save or reset validates", the one place for the fact. cli.md:125
  stays as it is.
- CHANGELOG, `## Unreleased`:
  `- Settings "Reset to defaults" on a running stack now shows RESTART REQUIRED, like a save; a reset of one band is refused whole when the stack's band-less file is broken or GPS is in use (it cleared the band's settings and still said "not modified").`

## 5. Order and commits

1. `CR2-4: reset a stack's band and band-less settings in one config transaction`: test + transaction.
2. `CR2-3: recheck use_gps for a reset under the config lock`: needs commit 1's renderer.
3. `CR2-2: a reset of a running stack writes the restart-required marker`: move of `_render_marker` +
   target + 2 tests + architecture.md word + CHANGELOG line.

## 6. Live proof (Pi 5, needed: CR2-2 is operator-visible)

```
lhpc config kiss rx_only on                   # stack stopped; non-default restart-mode param
lhpc stack start kiss && lhpc status kiss     # no RESTART REQUIRED line
lhpc config kiss --reset --yes                # → Config reset to defaults for 'kiss' (…).
lhpc status kiss                              # → ! RESTART REQUIRED: 'kiss' — saved settings differ from the running stack (lhpc stack stop kiss && lhpc stack start kiss)
lhpc stack stop kiss && lhpc stack start kiss && lhpc status kiss   # line gone
```
The dashboard shows the same flag (restart_required_stacks). CR2-3 (a race) and CR2-4 (fault injection) are
proven by the tests only.

## 7. Open questions

1. Should the reset's success result carry `self._apply_hints(target, modes, live=live_seen.get("v"))` as
   details, the way a save does? **Recommend yes for the CLI** (one line; the hints are already computed).
   The web flash shows only the summary, so it changes nothing there.
2. A scoped `__r__<comp>__password_file` (hand-edited only; `hmac_set_secret` writes the flat key) still
   matches the `__r__` prefix and would be cleared. This is a CR2-1 remnant, outside G2. **Recommend a
   separate finding**, not this group.
3. A rollback that itself fails (`recovery-required: … journal retained`) is still reported as "refused,
   not modified" (as in today's wording). **Recommend leaving it**; `lhpc doctor` reports the journal. The
   bundle has the same shape.
4. Keep a cheap pre-lock use_gps gate as the bundle does? **Recommend no**: one in-lock check is simpler,
   and a reset is rare and not latency-sensitive.

## 8. Self-check

Re-read against e5187f70: reset_config l.3329-3372; `files` l.3344; gate l.3352-3360; refusal l.3366;
`_gps_recheck` l.1333-1362; `_render_marker` l.1422-1518; transaction call l.1519-1529;
`apply_config_transaction` and None/REMOVE semantics config.py:1774-1838 (render all, then write; renderer
errors are wrapped "config transaction failed and was rolled back"); `update_stack_config` config.py:2023;
`ConfigLockBusy(ConfigError)` config.py:46; the status line services.py:994; the web flash shows
`result.summary` only (web/app.py:2037). CR2-2 and CR2-4 were reproduced on the base; CR2-3 was confirmed
by reading the code only.

Not verified:
- that `build_snapshot(fresh=True)` takes no lock that could invert order with `config_lock`. The bundle
  already calls it under the same lock, so this is no new risk.
- that `_stack_config_cached`, read by the marker's pre-effective side, is fresh in the lock. The behaviour
  is shared with the bundle and unchanged.
- whether the auto-install boundary (`_holds_config_exclusive`) can ever call `reset_config`. No caller
  does today (only CLI and web), so the plan uses `apply_config_transaction` without the locked branch.
- the live row on hardware.
