# PART 1 — ≥3 duplicate-cluster folds (former node → parametrized node)

Base: `f2270f3`. Test-only; no `lhpc/` changes. Each folded case keeps its exact input,
expected value, `match=`/assert, and per-case comment inside its `pytest.param(..., id=<old name>)`,
so only the file→param portion of node ids changes and the collected-case count is preserved.

AST `test_*` defs: **3068 → 3018** (−50). Collected cases unchanged by the folds
(params preserve the count); the suite total is 3414 → 3415 only because the daemon-fix
regression test was added (separate change).

## Folds
- `test_binary_install.py` → `test_extract_rejects_hostile_archive` ← symlink_escape /
  parent_traversal / member_outside_publish_roots (the duplicate-member case kept separate:
  different structure + audit docstring).
- `test_manifest_validation.py` → `test_manifest_component_rejected` ← 18 `*_rejected` component
  tests (all bare `raises(ManifestError)`; the one case with a `match=` kept it). `_ok`/accept and
  two-`raises` tests left standalone.
- `test_manifest_graph.py` → `test_manifest_graph_rejected` ← duplicate_stack_id / duplicate_component_id
  / main_must_be_in_own_stack / dependency_must_resolve / self_dependency / cycle / longer_cycle /
  invalid_band (8). `valid_graph_parses` + `cross_stack_dependency_resolves` left alone.
- `test_daemon_bounds.py` → `test_status_reply_grammar_violation_rejected` ← empty_key / empty_value /
  duplicate_key / control_char_value (4).
- `test_build_timeout.py` → `test_is_built_rejects_bad_marker` ← is_built_rejects oversize / directory /
  fifo / symlink-marker (4). `build_fails_closed_*` (assert-no-step) left separate.
- `test_probes.py` → `test_probe_unit_status` ← failed / inactive / not_found / user_no_bus /
  timeout / systemctl_missing (6). `active_system_unit` (extra `enabled` assert) left alone.
- `test_rig_supervisor_recover.py` → `test_supervise_inconclusive_status_is_never_a_verdict` ←
  nonzero_status_rc / empty_status / unrecognized_status is_inconclusive (3).
- `test_stack_params.py` → `test_ordinary_run_param_rejected_before_lifecycle` ← invalid /
  unknown / non_mapping ordinary run param (3); and `test_public_start_rejected_before_lock` ←
  public_start non_mapping / unknown / invalid before lock (3). Accept/seam cases left alone.
- `test_runtime_fs.py` → `test_read_bytes_refuses_non_regular_leaf` ← read_bytes refuses fifo /
  directory / symlink-leaf (3 — SAME function `runtime_fs.read_bytes`). Other functions
  (read_text_regular, log openers, tail, oversize-with-accept-at-cap) left alone.
- `test_cli.py` → `test_config_cli_rejected` ← the 7 pure config-CLI rejections. warn / mutate /
  accept (`_saves`, `_sets_and_normalizes`, `_n0call_warns`) left alone.

## REFUSALS (principle-3 gate not met — recorded, not forced)
- `test_pki.py:80` — requires_san / rejects_wildcard_ip_san / requires_ca are THREE distinct PKI
  boundaries. [SAFETY: PKI]
- `test_post_start.py:1686` — TX-mode readback vs CAD-idle are different invariants. [SAFETY: RF]
- `test_sysstats.py:93` — parse_loadavg / parse_uptime / parse_os_release are DIFFERENT parser
  functions (already parametrized). Different SUT.
- `test_selfupdate.py:2233` — repair_restart false vs true changes the CONTROL FLOW, not just input.
- `test_cli.py:370` (as a 9-cluster) — mixes reject + warn + accept/mutate; only the clean 7-case
  pure-reject subset was folded (above), the rest left standalone.
- `test_structured_exec.py:161` — only 2 identical-shape `_blocks` cases; below the size-3 floor.
- `test_source.py:1791` — build / uninstall / host_test blocked-by-source-lock call three DIFFERENT
  SUT methods. [SAFETY: resource coordination]
- `test_source.py:3026` — BLOCKED vs MANUAL_REQUIRED are different observable outcomes (+ an extra
  marker invariant). [SAFETY: destructive guards]

## Pre-existing finding (NOT introduced here; out of scope)
The `test_binary_install.py` binary-switch/retire tests (`test_complete_switch_commits_the_retirement`
and ~5 siblings) are ISOLATION-FRAGILE at `f2270f3`: green in the full suite (the coverage gate ran
them with 0 failed) but red when run as a bare subset, because `svc.install(... source=pinned)` sees
`loraham-daemon` as "component(s) running" without full-suite process context. Unrelated to these
folds (they fail identically with the fold reverted). Flagged for a future isolation-hardening pass;
the Tier 0 `contract` lane below deliberately tags isolation-ROBUST cases only.
