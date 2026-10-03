# Gate 1 — code review request, Q1 follow-up fix (round 1)

Please judge the one commit below. It fixes a defect left by batch Q1 (typed config-recovery result) on `integration/0.12.0` ac1eadd. Check it for: correctness; whether every caller of `recover_config_transaction` now reads the typed result; whether a journal that cannot be finished can still be overwritten by any config writer; whether behaviour changes beyond the defect; whether the test is red before the change and green after it.

Answer form, one row per commit, then a final line:

| commit | verdict (OK / FINDING) | what |
|---|---|---|
| c430b85 | | |

Final line: GREEN / GREEN WITH NOTES / RED

This file is your whole input: you have no repository access; use no connector, tool or web lookup.

## The defect

Q1 changed `recover_config_transaction(paths)` from `str | None` (`None` = no journal, `""` =
recovery required but not possible, a note = recovered) to `tuple[ConfigRecovery, str]`
(`UNNECESSARY`, `BLOCKED`, `RECOVERED`). One caller was not updated. `_apply_config_transaction_locked`,
the transaction body that runs under a config lock its caller already holds, still read:

```python
    if recover_config_transaction(paths) == "":
        raise ConfigError("recovery-required: a pending config journal could not be "
                          "recovered; resolve it before saving config again")
```

A tuple never equals `""`, so a journal that cannot be finished (`BLOCKED`) no longer stops the body.
The body then writes its own journal over the pending one, replaces the targets and removes the journal
on success: the pending journal and the pre-images it held are gone. Before Q1 the comparison was right
(the function returned `""` for this case), so no released version has the defect.

Observed on ac1eadd (a malformed journal `{ not json`, then the locked body called with one stack
target): the call returns normally, the stack file holds the new content, and no journal is left.

## Who reaches the locked body

- `apply_config_transaction` takes `config_lock` first. `config_lock` finishes a pending journal before
  the holder runs and raises `ConfigRecoveryRequired` unless the outcome is `RECOVERED` or there is no
  journal. This path was not affected: the public call refuses on ac1eadd too.
- `ControllerService.set_operator_identity` (`service_params.py`) holds `config_lock` and checks
  `recover_config_transaction(...)[0] is ConfigRecovery.BLOCKED` itself before it calls the body. Not
  affected.
- `save_config_bundle` (`service_params.py:1433`) calls the body directly when the thread already holds
  the config-stability guard EXCLUSIVELY (the auto-install boundary): it reuses the lock the boundary
  already holds instead of taking `config_lock` again, so no journal is finished at that point. A journal left during that hold (a save whose rollback failed retains its
  journal and blocks later saves) is then overwritten by the next save in the same boundary. This path is
  analysed from the code; it was not reproduced end to end.

No test covered the body on its own: the existing journal tests go through `config_lock`, which
finishes or refuses the journal before the body runs.

## The fix

The check tests the typed outcome: `if recover_config_transaction(paths)[0] is ConfigRecovery.BLOCKED:`.
`UNNECESSARY` and `RECOVERED` let the body proceed, as before Q1 (`None` and a note did). No CHANGELOG
entry: the defect exists only on the unreleased integration branch.

## The test

`tests/core/test_config.py::test_the_locked_transaction_body_stops_on_a_journal_it_cannot_finish`,
three cases, calling the locked body directly:
- `blocked` (journal `{ this is not json`): `ConfigError` matching `recovery-required`; the journal is
  kept byte for byte and the target is untouched.
- `recovered` (a valid journal holding the target's pre-image, the target half-written): the body
  restores, writes the new content and removes the journal.
- `no-journal`: the body writes and leaves no journal.

Red-before: with ac1eadd's `lhpc/core/config.py`, `[blocked]` fails (`DID NOT RAISE ConfigError`); the
other two pass. With the fix: `3 passed`.

The `UNNECESSARY`/`RECOVERED` paths through the public writers keep working; these existing tests pass
with the fix: `test_pending_journal_is_recovered_before_next_save`,
`test_a_non_transactional_save_finishes_a_pending_journal_before_it_writes`,
`test_a_recovered_journal_admits_the_writer_whatever_its_note_says[,reworded]`,
`test_malicious_or_malformed_journal_blocks` (all cases), `test_rollback_failure_retains_journal_and_blocks_later`.

## Numbers, measured

| figure | command | output |
|---|---|---|
| size | `git diff --numstat ac1eadd c430b85` | `lhpc/core/config.py` +1 −1; `tests/core/test_config.py` +26 −0 |
| line counts | `git show <rev>:<file> \| wc -l`, ac1eadd → c430b85 | config.py 2161 → 2161; test_config.py 1868 → 1894 |
| red-before | `pytest tests/core/test_config.py -k locked_transaction_body` with ac1eadd's `config.py` | `1 failed, 2 passed` (`[blocked]`) |
| green | same, with the fix | `3 passed` |
| named existing tests + the new one | `pytest tests/core/test_config.py -k '<the names above>'` | `14 passed` |
| whole file | `pytest tests/core/test_config.py` | `156 passed` |
| wider run | `pytest -n 12 --dist loadfile tests/core tests/install tests/web` (Python 3.14 venv) | `3 failed, 4307 passed, 1 skipped`; the 3 are `test_build_launcher_runtime.py::test_rendered_launcher_nonfinite_spec_timeout_fails_safe[inf,-inf,nan]`, which fail the same way on ac1eadd (a local-environment cause, unrelated to this commit) |
| lint | `ruff check lhpc testlab`; `ruff check tests --select F,E9` | All checks passed (both) |

## Commit message

```text
c430b85 Q1: a blocked config journal stops the locked transaction body

`_apply_config_transaction_locked` still compared the recovery result with "",
the return value before Q1 typed it as (ConfigRecovery, note). The tuple never
equals "", so a journal that could not be finished no longer stopped the body:
it went on, wrote its own journal over the pending one and lost its pre-images.
The public `apply_config_transaction` was not affected (config_lock finishes the
journal first); the auto-install boundary calls the locked body under a lock it
already holds. The check now tests for BLOCKED.
```

## Full diff (ac1eadd..c430b85)

```diff
diff --git a/lhpc/core/config.py b/lhpc/core/config.py
index c878d7c..3da7efd 100644
--- a/lhpc/core/config.py
+++ b/lhpc/core/config.py
@@ -1917,7 +1917,7 @@ def _apply_config_transaction_locked(paths: Paths, targets: list[tuple[str, Path
     section, so snapshot and write cannot be split by a concurrent start). Everyone else MUST use
     `apply_config_transaction()`, which acquires the lock. Steps: recover/block any pending journal; journal each pre-image; atomically replace; roll back
     all on failure; remove the journal on success."""
-    if recover_config_transaction(paths) == "":
+    if recover_config_transaction(paths)[0] is ConfigRecovery.BLOCKED:
         raise ConfigError("recovery-required: a pending config journal could not be "
                           "recovered; resolve it before saving config again")
     jp = _txn_journal(paths)
diff --git a/tests/core/test_config.py b/tests/core/test_config.py
index 68f4ffb..7d01e8a 100644
--- a/tests/core/test_config.py
+++ b/tests/core/test_config.py
@@ -1866,3 +1866,29 @@ def test_a_journal_path_that_cannot_be_examined_refuses_the_writer(tmp_path, mon
         cfgmod.save_hardware_setup(paths, "loraham")
     assert exc.value.reason == "recovery-required"
     assert local.read_text() == "# untouched\n"
+
+
+@pytest.mark.safety("config-transaction")
+@pytest.mark.parametrize("journal", [None, "recoverable", "{ this is not json"],
+                         ids=["no-journal", "recovered", "blocked"])
+def test_the_locked_transaction_body_stops_on_a_journal_it_cannot_finish(tmp_path, journal):
+    """The locked body runs under a lock its caller already holds (the auto-install boundary), so
+    it finishes a pending journal itself. A journal it cannot finish stops it with
+    recovery-required and is kept; no journal, or a recovered one, lets the write proceed."""
+    paths = _paths(tmp_path)
+    stack = tmp_path / "config" / "stacks" / "daemon.toml"
+    stack.parent.mkdir(parents=True, exist_ok=True)
+    stack.write_text("# old\n")
+    if journal == "recoverable":
+        stack.write_text("# CORRUPT partial write\n")
+        journal = {"version": 1, "targets": [{"kind": "stack", "rel": "config/stacks/daemon.toml",
+                                              "pre": "# old\n", "existed": True, "mode": 0o644}]}
+    jp = _write_journal(tmp_path, journal) if journal else cfgmod._txn_journal(paths)
+    targets = [("stack", stack, 'radio = "868"\n', 0o644)]
+    if isinstance(journal, str):
+        with pytest.raises(ConfigError, match="recovery-required"):
+            cfgmod._apply_config_transaction_locked(paths, targets)
+        assert jp.read_text() == journal and stack.read_text() == "# old\n"
+    else:
+        cfgmod._apply_config_transaction_locked(paths, targets)
+        assert not jp.exists() and stack.read_text() == 'radio = "868"\n'
```
