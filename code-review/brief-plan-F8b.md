# CLOUD BRIEF · PLAN for F8b: the complete inventory of snapshot-invalidating service entries + a meta-test

Read-only on the code; output = ONE commit on this routine's branch adding `plans/PLAN-F8b.md`; never touch main/dev/code-review/brief; no pull request; no attribution lines. Base origin/main e5187f70. Read `git show origin/code-review/brief:code-review/PLAN-retro-0.11.10-v3.md` section F8 (F8a decorates the known mutators now; F8b is this: a mechanically complete universe), docs/architecture.md, tests/README.md.

## The plan must settle
1. The universe: every public attribute of `ControllerService` (services.py:161 and its mixins) that is callable — how to enumerate it mechanically (dir/inspect over the composed class, excluding private and inherited object methods).
2. The classification: each entry is either snapshot-INVALIDATING (decorated `@invalidates_snapshot`) or explicitly listed as snapshot-NEUTRAL in one allowlist with a one-line reason; both/neither is a test failure; a stale allowlist entry (name no longer exists) fails too.
3. The meta-test: `tests/core/test_snapshot_memo.py::test_every_public_service_entry_is_classified` — the exact assertion, how it stays cheap, and how it reports the offending names.
4. The inventory itself as a table (name → class → reason) for the ~325 entries, grouped; say how many are mutators and which decorations are missing today (F8a covers 18+15).
5. Risk: a wrongly-neutral entry = stale snapshot (what the operator sees); a wrongly-invalidating entry = a slower page; the plan errs toward invalidating.
6. Commits, tests, docs (one place), open questions, self-check. ≤ 220 lines.
