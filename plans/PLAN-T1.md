# PLAN-T1 — golden / characterization tests of the six coordinating operations

Batch T1 of the architecture consolidation (next 0.x.y release). Test-only: the diff touches
`tests/` (plus this plan and the code-review report files); `lhpc/` and `testlab/` stay unchanged.
Base: `origin/integration/1.0`.

## Goal

One module per operation under `tests/golden/` records, for 6–8 fixed scenarios, what the
operation does TODAY: the result fields (ok, summary, detail heads, data keys, next_commands,
per-component outcomes), the files written/removed under the runtime root, the markers and
journal states, and the ORDER of its phases (admission → locks → recheck → mutation →
verification → finalization). Each case is tagged in its docstring `intended:` or
`known defect <id>:`. Later refactors (W2a build plan, W2c unit migration, W3 config-save split,
W4 start/restart) run this set first.

## Shared harness — `tests/golden/conftest.py` (new)

- `phases`: wraps the seams in ONE table, `ORDER_SEAMS` (delegating to the real code), and logs
  `phase:subject` labels; admission/lock/recheck on first occurrence, mutations each time.
- `run_op(root, fn)` → `Run`: `fields` (decision-bearing ActionResult fields), `files` (tree diff,
  lock files excluded, owned-record pid/nonce normalized), `phases`, `kinds`.
- `kiss_box`: kiss installed + built over a FakeSystem daemon READY on 433; the TNC spawns a real
  `sleep` (the existing `real_spawn` fixture), and its ready endpoint follows that process, so a
  start sees it come up and a stop sees it go (no endpoint toggling by hand).
- `prior_boot`, `uninstall_guard`, `interrupted_install`, `held_lock` (a lock held by another
  process, so contention is real).
- `_tagged` (autouse): every golden case's docstring opens with its tag.

## Per operation (code path today → scenarios)

| op | owner (file:line) | scenarios |
|---|---|---|
| start | `service_lifecycle_ops.py:641` (`start`), `:783/:883` (`_start_impl[_inner]`), `services.py:2558/2635` (admission, lifecycle guard), `lifecycle.py:437` | plan+apply happy; missing callsign (chat); admission; interrupted install; band owner; unverified termination; entry hook; interactive launch |
| stop | `service_lifecycle_ops.py:2373/2426`, `lifecycle.py:1255` | plan; running; nothing runs; unowned process; no cessation; contended lock |
| restart | `service_lifecycle_ops.py:2680/3001` | plan; running; unverified stop aborts; hook refusal; admission; band owner |
| save_config_bundle | `service_params.py:1054`, `config.py:76/1844/1891/1901` | stopped; running (restart marker); validation; journal recovered; journal unrecoverable; write fails mid-way; malformed per-stack file |
| build | `service_lifecycle_ops.py:3166`, `services.py:359` (source guard), `lifecycle.py:350` | plan+apply; failed step; marker written last (meshcore); not installed; admission; interrupted install; contended source lock |
| boot-restore | `service_boot_restore.py:280/307/470/516/560` | restore; nothing to restore; disabled; stop intent; refused before hook (pending); failed after hook (consumed); admission |

Six modules: `test_golden_{start,stop,restart,save_config_bundle,build,boot_restore}.py`.

## Tests, red-before

Golden tests characterize existing behaviour, so "red before" means: each module fails on a
deliberately broken copy of the behaviour. One mutation per operation (applied, run, reverted;
never committed): start — skip the authoritative recheck under the locks; stop — no stop intent;
restart — start after an unverified stop; save — no journal before the writes; build — marker
stamped before the inputs sidecar; boot-restore — the `attempting` claim not made durable.

## Risks and how they are ruled out

- **Pinning private names (rule 1)** — the golden set's purpose is the step order. The names live
  only in `ORDER_SEAMS` (one table); the README says so. Every wrapper delegates, so behaviour
  is unchanged.
- **Flakiness from real processes** — the spawned `sleep` is reaped by the existing autouse
  fixture; the endpoint follows `/proc/<pid>/stat` (a zombie counts as gone); the only patched
  timing is "the process outlives SIGTERM" (`_wait_ceased`), a collaborator stub with a comment.
- **Host leakage** — the existing autouse isolation (runtime root, HOME, firewall, process table)
  applies; the boot id and the two boot-restore host gates are pinned by `prior_boot`.
- **Tmp paths in assertions** — the tree diff is runtime-relative; detail lines are compared as
  `[tag] subject` heads, or by `re.fullmatch` where a path is part of the line.
- **File-disjointness** — only new files under `tests/golden/` plus `tests/README.md`.

## Findings expected (recorded, not fixed)

- T1-F1: a start the preflight refuses (band owner; same path for the firewall gate and config
  ambiguity) has already reset the band's daemon feed floor (`state/daemon-feed-floor-<band>`).

## Seams that do not exist (finding, not added)

- A start/restart has no single typed "refusal class" field: admission, enforce_fields,
  firewall_gate and reason are typed; band owner, interrupted install and lock contention are
  identified by their summary only.

## Open questions (with recommendation)

1. Compare summaries byte for byte? Recommended yes for the golden set: it records what the
   operator sees today; a deliberate wording change updates one expected string.
2. Golden location: `tests/golden/` (a new layer directory) rather than spread over `core/` —
   recommended, so "run the golden set first" is one path.
