# Fold + Tier-0-contract task — report

Base `f2270f3` (consolidation + ruff, committed). Everything below is UNCOMMITTED and folds into the
same commit, per operator. Test-only except the daemon dashboard fix (one `lhpc/` file, operator-requested).

## Before → after
| metric | before (f2270f3) | after | Δ |
|---|---|---|---|
| test files | 95 | 95 | 0 |
| AST `test_*` defs | 3068 | 3018 | −50 (PART 1 folds) |
| collected cases | 3414 | 3415 | +1 (daemon regression test) |
| test LOC | ~46.5k | ~46.7k | folds −~350 LOC, offset by 104 `@contract` + 36 `@safety` tags + the daemon test |
| coverage (branch-incl) | 85.3847 % (ruff-tree baseline) | **85.3973 %** (up; comparator PASS) | — |
| `-m contract` | (new) | 104 cases, green, ~22s (< 30s) | — |
| `-m safety` | (new) | 36 cases, green, ~5s | — |

## Included changes (three logical parts + one fix)
- **Daemon dashboard fix** (`lhpc/core/service_lifecycle_ops.py`, operator-reported): `radio_overview`
  now reports the daemon *installed* when it is binary-covered or running, not only source-present —
  a binary-installed, running daemon no longer renders "Daemon not installed". Regression test in
  `tests/test_daemon_readiness.py`. Patch: `daemon-installed-fix.patch`.
- **PART 1 — folds**: 10 ≥3-clusters folded into parametrized tables (−50 defs), 7 refused. Details,
  FOLD-LOG, and REFUSALS in **`part1-folds.md`**. Patch: `part-1.patch` (pure-fold files; the three
  files that ALSO got PART-2 markers — test_cli / test_stack_params / test_binary_install — are in the
  combined patch).
- **PART 2 — Tier 0 contract**: 104 existing cases tagged `@pytest.mark.contract` (36 also
  `@pytest.mark.safety`), across the 15 promises via the widest public seam. Promise→case map and the
  7 CONTRACT-GAPS in **`part2-contract.md`**. Patch: `part-2.patch`.
- **PART 3 — docs**: `tests/README.md` gained a "Tier 0 — the contract" section (and `contract`/`safety`
  in the marker list); one `CHANGELOG.md` line.

## Gates
- G1 (total coverage not below baseline) + G2 (no covered prod line lost; only lhpc change is the
  daemon fix, ratio-checked): the final full coverage gate result is appended to `report.md`-style
  logs; the daemon fix raises coverage of `service_lifecycle_ops.py` (new branch covered).
- G3: PART 1 reduced defs only via recorded folds (cases preserved); PART 2 left the collected count
  exactly unchanged (markers add no cases). The one +1 is the daemon regression test.
- G4: no untouchable safety area merged across — the safety-boundary clusters (pki, source-lock,
  post-start TX/CAD) were REFUSED, and folded safety families keep every per-case assertion.
- ruff `lhpc` clean; ruff `tests --select F,E9` clean; compileall clean; `git diff --check` clean.

## Deliverable patches
`part-1.patch`, `part-2.patch`, `daemon-installed-fix.patch`, and `part-all.patch` (the full combined
uncommitted diff, incl. new-file safety tags and the fold+marker overlap files). Plus `part1-folds.md`
and `part2-contract.md`.

## Carried finding (environmental; not a regression, not fixed here)
The `RealSystem`-backed binary-switch tests in `test_binary_install.py` are NOT hermetic against a live
daemon: they read the global `/tmp/loraconf433.sock`, so with a real daemon running on the box they
report "component(s) running" and fail (`-m "not slow"` red) even though the code is correct. Verified
by stopping the daemon → green. Not caused by any change here. A future isolation-hardening pass should
route the CONF-socket path through a per-test override for those tests; the contract lane deliberately
tags none of them.
