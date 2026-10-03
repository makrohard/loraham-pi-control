# PLAN-D1 — the invariant table in docs/architecture.md

Goal: one place where a reviewer finds, for every safety claim, its owner (`file:line` of the
enforcing function), its locks in order, its durable state with the recovery rule, and the tests
that prove it. Docs only: `docs/architecture.md`. No code, no tests.

## Today

- `docs/architecture.md` § Safety model (lines 184-279 on the base) states 16 guarantees as
  bullets. Some bullets end with a module and a test file (`core/install.py`;
  `tests/install/test_source.py`). Others name neither. None says which lock or which journal.
  So the "where" facts are scattered and partly missing.
- The firewall, self-update, boot-restore, binary-channel and radio guarantees are stated on other
  pages: firewall.md, deployment.md, operations.md and § Radios. Nothing maps them to code.

## Change

1. Add `### Invariant table` at the end of § Safety model, sorted by area: admission/locks,
   config transactions, install/recovery, binary channel, PKI/web, firewall, self-update,
   radio/band ownership, lab. Columns: area · invariant · owner · locks (in order) ·
   durable state · recovery · proving tests.
2. One place per fact.
   - The invariant column is one short phrase that links to the bullet or page where the
     guarantee is stated.
   - The bullets lose their trailing module and test references; those now live only in the
     table.
   - The opening sentence of § Safety model points to the table.
   - § Locking gains the one lock-order sentence. It was stated nowhere in the docs before; the
     code states it at `lhpc/core/services.py:2505`.
3. Where no test proves a claim, the tests cell says `gap: <what>` and the report lists it for the
   handler.

## Sources and verification (per row)

- **Owner:** `grep -n "def <name>(" <file>` on the base tree. The row cites the `def` line.
- **Locks:** read where the lock is taken. Examples: the `with (...)` of `update`/`uninstall`;
  `_admit`; `config_lock`; `firewall_helper.LOCK_PATH`; `selfupdate._LOCK`/`_CTRL_RUNTIME_LOCK`.
- **Durable state:** grep the path constant. Examples: `JOURNAL_REL`, `_journal_path`,
  `_txn_journal`, `REQUEST_REL`, `_MIGRATE_MARKER`.
- **Proving tests:** `grep -n "^def <test>(" <file>`. Each test must exist.
- **Mechanical check:** a script parses the table and checks two things. Every
  `` `path:line` `name` `` must land on `def name(` at that line. Every `` `tests/…::name` ``
  must exist. It also checks every link anchor. Its source and output go into the report.

## Gaps expected (from code and tests only)

- A write over a `local.toml` that is not valid TOML. Only the unsupported-structure case is
  tested.
- Uninstall keeping secrets and profiles. Config is tested by `test_recovery.py`.
- A wheel installed into a fresh venv. The prose cites `tests/repo/test_packaging.py`, but no
  test there installs a wheel.
- The daemon's `spi0.lock`. The daemon owns it, not LHPC; LHPC only tests the claim model.

## Risk and how it is ruled out

- **A stale line number.** The checker runs on the D1 commit. U1 (same branch, after D1) edits
  text in five owner files and shifts their lines. U1 therefore re-runs the checker and corrects
  the shifted numbers in its own commit. This is the one place U1 touches `docs/architecture.md`;
  it is named in U1's report.
- **Two statements of one fact.** Every trimmed bullet keeps its guarantee. The table adds no
  guarantee wording of its own beyond the short label and the lock order. The lock order is
  stated once, in the bullet.
- **A row claiming a test that proves something else.** The tests were chosen by name and read
  where the name was ambiguous. Partial proofs are marked `gap:`.

## Open questions

- Several rows' tests do not carry `@pytest.mark.safety`: admission, reslock, snapshot memo, boot
  restore, binary channel, self-update, path containment and web jobs. So `-m safety` is narrower
  than the table. Recommendation: a later batch marks them. That changes `tests/`, which is out of
  this batch.
