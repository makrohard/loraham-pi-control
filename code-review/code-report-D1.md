# Code report — D1 (the invariant table)

Base: `integration/0.12.0` at `e38803b`. Docs only. No file under `lhpc/`, `tests/` or
`testlab/` changed.

## Commits

| sha | subject | files | tests |
|---|---|---|---|
| `b2a82ba` | D1: plan — the invariant table | `plans/PLAN-D1.md` | — |
| `b6655e2` | D1: the invariant table — owner, locks, durable state and proving tests per safety invariant | `docs/architecture.md` | none added (docs). Red-before: not applicable. The table checker below is the gate for this commit. |

## What changed

- New `### Invariant table` at the end of § Safety model. It has 35 rows in 9 areas, sorted in
  the order the brief gives: admission/locks, config transactions, install/recovery, binary
  channel, PKI/web, firewall, self-update, radio/band ownership, lab.
- Each row gives:
  - the invariant (a short label plus a link to where it is stated);
  - the owner (`file:line` of the `def`);
  - the locks in order;
  - the durable state and its recovery rule;
  - the proving tests (`module::name`).
- The 16 Safety-model bullets keep their guarantees. Their trailing module and test references
  were removed, because the table is now the one place for them. Two changes are not removals:
  - The § Safety model lead sentence now points to the table.
  - The *Locking* bullet gains the lock order, which no doc stated before. Its source is
    `lhpc/core/services.py:2505`. The update and uninstall `with` blocks
    (`lhpc/core/service_maintenance.py:1841` and `:2359` on the base) show the same order.
- Reworded lines in the prose: "Path containment" (`core/runtime_fs.py` → "Every runtime write"),
  "Typed validation", "Daemon sockets", "Web" and "Packaging". They lost their code paths and
  kept their meaning. Visible with `git diff --color-moved=dimmed-zebra e38803b b6655e2 -- docs/architecture.md`.

## How every row was verified

1. Owner, mechanically. A script reads the table. For every ```path:line` `name``` it asserts
   that line `line` of `path` contains `def name(`. For every ```tests/…::name``` it asserts
   `^def name(` exists in that file. It also checks every link anchor. Output on `b6655e2`:
   `rows=35 owners=77 tests=105 bad=0`. Source:

```python
"""Verify every owner `path:line` `name` and every `tests/...::name` in the invariant table."""
import re, sys, pathlib
doc = pathlib.Path("docs/architecture.md").read_text().split("### Invariant table", 1)[1].split("\n## ", 1)[0]
rows = [l for l in doc.splitlines() if l.startswith("| ") and not l.startswith("| area") ]
bad = owners = tests = 0
for row in rows:
    cells = [c.strip() for c in row.strip("|").split("|")]
    if len(cells) != 6:
        print("BADROW", row[:80]); bad += 1; continue
    for cell in (cells[2], cells[3], cells[4]):
        path = None
        for m in re.finditer(r"`([\w/.]+\.py)?:(\d+)` `(\w+)`", cell):
            path = m.group(1) or path
            n, name = int(m.group(2)), m.group(3)
            line = pathlib.Path(path).read_text().splitlines()[n - 1]
            owners += 1
            if not re.search(rf"def {name}\(", line):
                print("OWNER MISMATCH", path, n, name, "->", line.strip()); bad += 1
    path = None
    for m in re.finditer(r"`((?:testlab/)?tests/[\w/]+\.py)?::(\w+)`", cells[5]):
        path = m.group(1) or path
        tests += 1
        if not re.search(rf"^def {m.group(2)}\(", pathlib.Path(path).read_text(), re.M):
            print("TEST MISSING", path, m.group(2)); bad += 1
    for m in re.finditer(r"\]\(([\w.-]+\.md)?(#[\w-]+)?\)", cells[1]):
        f = pathlib.Path("docs") / (m.group(1) or "architecture.md")
        if not f.exists():
            print("LINK FILE", f); bad += 1; continue
        if m.group(2):
            slugs = {re.sub(r"[^\w\- ]", "", h.lower()).strip().replace(" ", "-")
                     for h in re.findall(r"^#+ (.+)$", f.read_text(), re.M)}
            if m.group(2)[1:] not in slugs:
                print("ANCHOR", f, m.group(2)); bad += 1
print(f"rows={len(rows)} owners={owners} tests={tests} bad={bad}")
sys.exit(1 if bad else 0)
```

2. Locks and durable state, by reading the code where the lock is taken or the path is built.
   Commands per row:
   - admission: `grep -n "def _admit" lhpc/core/services.py`, key at `:2507`;
   - reslock: `grep -n '"locks"' lhpc/core/reslock.py` → `:66`, `:70`;
   - config: `grep -n '".lock"' lhpc/core/config.py` → `:107`; `config-txn.json` → `:1746`;
   - source transactions: `lhpc/core/install.py:1043` (`source-txn` dir), `:1045-1059`
     (journal name), `:1202` (`source-txn-index`), `:1269` (`.staging` record);
   - uninstall order: the `with` at `lhpc/core/service_maintenance.py:2359` (base);
   - binary: `lhpc/core/binary_install.py:49` (`JOURNAL_REL`); order admission → covered paths
     in `binary_install` (base `:192-202`);
   - boot restore: `lhpc/core/boot_restore.py:27`;
   - jobs: `lhpc/core/jobresult.py:27`, `lhpc/core/jobs.py:262`;
   - firewall: `lhpc/core/firewall_helper.py:633-634`, `:975-976`;
   - self-update: `lhpc/core/selfupdate.py:36-38`, `lhpc/core/updater_units.py:48-49`;
   - owned records: `lhpc/core/lifecycle.py:883-884`.
3. Statements: each row's invariant label links to the bullet or page that states it. The checker
   checks the anchors.

## Gaps for the handler (no proving test)

1. A write over a `local.toml` that is not valid TOML keeps the bytes. Only the
   unsupported-structure case is tested (`test_local_unsupported_structures_block_and_preserve`).
2. Uninstall keeps secrets and profiles. Config is proven by
   `tests/core/test_recovery.py::test_uninstall_removes_source_but_keeps_config`.
3. "A wheel installed into a fresh venv runs". The base prose cited
   `tests/repo/test_packaging.py`, but no test there builds or installs a wheel
   (`grep -rn "wheel" tests` → no hit).
4. The daemon's `spi0.lock`. The daemon enforces it, not LHPC; only LHPC's claim model is tested.

Also noted: "No GET runs a network command" has no single enforcing function; the row says so.
Many proving tests carry no `safety` marker (see the plan's open question).

## The 6-point block

1. **Contracts.** None touched; documentation only.
2. **Invariants and tests.** No invariant changes. The table states which test proves each one.
   All 105 cited tests exist (checker).
3. **Known failure classes.**
   - Fakes, probes and interrupts: not applicable, no code.
   - Same decision across entry paths: not applicable.
   - Stacked conflicts: `docs/architecture.md` is also changed by `cons/Q2` (`git diff --stat
     e38803b...origin/cons/Q2 -- docs/architecture.md` → 8 lines). Q2's hunks sit in the
     Safety-model bullets this batch trims, so a textual conflict on stacking is likely. The
     resolution is mechanical: keep Q2's sentence and drop the module and test references.
   - U1 (this branch) shifts owner lines in five files. U1 re-runs the checker and corrects them
     in its own commit.
4. **Test rules.** No test added.
5. **Whole test directories.** No test directory touched.
   - `python -m pytest -q -p no:cacheprovider tests/repo` → `410 passed, 5 skipped` (on
     `b6655e2`).
   - `ruff check lhpc testlab` → `All checks passed!`.
   - `ruff check tests --select F,E9` → `All checks passed!`.
6. **Adversarial self-review.**
   - Found and fixed: the plan said 19 bullets; the measured count is 16 (`grep -c '^- \*\*'` on
     the base section). Fixed before this report.
   - Found: the "Packaging" bullet's test reference pointed to a test that does not prove the
     claim. It is now a listed gap, not a silent removal.
   - The docs and this report describe exactly the diff.

## Simplicity guardrails

- No code; no abstraction.
- The prose shrinks: 36 lines removed, 74 added, of which the table is 47. Net +38 lines
  (`git diff --numstat e38803b b6655e2 -- docs/architecture.md` → `74 36`).
- Dependencies: not applicable.

## Precision checklist

1. Absolute sentences:
   - "every row's claims verified" means the mechanical checks above plus the reads listed. Lock
     claims were read, not machine-checked.
   - "one place per fact" is limited to the facts moved here.
2. One-line texts: not applicable.
3. Fault injectors: not applicable.
4. Moved lines: shown with `--color-moved=dimmed-zebra`. The reworded bullets are named above.
5. Helpers swallowing exceptions: not applicable.
6. Every run cited above is on `b6655e2`, which is on this branch.

## Deviations

- `plans/PLAN-Q1.md` is not on `integration/0.12.0`. It is on `cons/Q1`, and was read there.
- `docs/README.md`: no change. The index lists pages, not sections.

## Numbers, measured

| figure | command | output |
|---|---|---|
| architecture.md lines before / after | `git show e38803b:docs/architecture.md \| wc -l`; `wc -l < docs/architecture.md` | 305 / 343 |
| +/- | `git diff --numstat e38803b b6655e2 -- docs/architecture.md` | 74 / 36 |
| rows, owners, tests | checker | 35, 77, 105, bad=0 |
| Safety-model bullets on the base | `git show e38803b:docs/architecture.md \| sed -n '/^## Safety model/,/^## Controller/p' \| grep -c '^- \*\*'` | 16 |
| gap markers | `grep -c "gap:" docs/architecture.md` | 4 |
| plan length | `wc -l plans/PLAN-D1.md` | 71 |
| tests/repo | `python -m pytest -q -p no:cacheprovider tests/repo` | 410 passed, 5 skipped |
