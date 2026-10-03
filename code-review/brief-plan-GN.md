# CLOUD BRIEF · PLAN for fix group GN of loraham-pi-control (one Claude Code cloud session, read-only on the code)

You write the PLAN for a group of verified defects; you change no code. Output = ONE commit on this routine's branch
adding `plans/PLAN-GN.md`. Never touch `main` or `dev`, no pull request. (Attribution lines are stripped
downstream; the file is copied out.) Base: origin/main = e5187f70ae4e81a1081a835be06f84746222c3b6 (v0.11.10). Read `docs/architecture.md` (the safety
invariants), `docs/README.md`, `tests/README.md` (the house rules of the tests) and `docs/maintenance.md` (how a patch
is cut) first.

## The maintainer's rules for every change
Simple, robust, maintainable: the SIMPLEST change that removes the defect; no new mechanism unless the defect cannot be
removed without one (then say so and size it); no behaviour change beyond the defect; a regression test per change that
is RED before and GREEN after, placed where the maintainer would look for it (the existing test module of that file);
docs updated where a sentence becomes untrue (one place per fact); the CHANGELOG line in the operator's words.

## The findings of this group (verified; file:line on e5187f70ae4e81a1081a835be06f84746222c3b6)
**Group context / decisions:** Nits and residuals: U-3 duplicated socket helpers in lifecycle.py; U-5 four band constants → one ALLOWED_BANDS (about 30 literal sites; do it mechanically, one commit, no behaviour change); CR4-8 was fixed in 0.11.10 (check); CR7-15 RESIDUAL: the failure cleanup unlinks by name — the full fix writes under a private temp leaf and publishes atomically (open_marker_excl + create_exclusive_bytes), size it.

### U-3
- where: `lhpc/core/lifecycle.py:1509-1522` · severity kept nit
- claim: —
- defect: `_prefer_run_socket`/`raw_socket`/`conf_socket` duplicate `daemon_control`'s identical helpers.
- how to see it: —
- verifier: PARTLY — `_prefer_run_socket` (lifecycle.py:1510-1516) duplicates daemon_control.py:140-150. `Lifecycle.conf_socket` (1521) differs: it does no band validation (cf. daemon_control.py:156-157) and has no caller. `raw_socket` (1518, used at 1556) has no counterpart in daemon_control.

### U-5
- where: `lhpc/core/service_lifecycle_ops.py:1899 (+ ~11 sites)` · severity kept nit: no behaviour difference today
- claim: `daemon_control.ALLOWED_BANDS` is the single source of truth.
- defect: The literal `("433", "868")` is repeated at ~12 sites.
- how to see it: —
- verifier: CONFIRMED — 12 literal sites in service_lifecycle_ops.py (e.g. :78, :1756, :1899, :2291, :4316), about 30 across lhpc/. There are four separate constants (daemon_control.py:129 `ALLOWED_BANDS`, services.py:2734 `RADIO_BANDS`, validators.py:265, boot_restore.py:34). "Single source of truth" is the reviewer's wording, not the code's.

### CR4-8
- where: `lhpc/core/source_fs.py:172` · severity corrected S3→nit: only empty hidden dirs leak, and the refusal stays typed
- claim: The capability probe is "a scratch `.lhpc-atomic-probe-*` rename under the held parent fd, cleaned up afterwards".
- defect: Cleanup runs only on success or on `AtomicRenameUnavailable`. If the rename fails with any other OSError, the scratch dir `nonce` is left behind (l.178-179). If `rmdir(nonce-b)` fails, `nonce-b` is left behind. Every later probe on that filesystem (the positive result is not cached) can leak another dir into `src/`.
- how to see it: Patch `_rename_noreplace_at` to raise `OSError(EPERM)`, then call `require_atomic_rename(paths, src_parent)`. A `.lhpc-atomic-probe-*` directory remains under `src/`.
- verifier: CONFIRMED — source_fs.py:172-179: an OSError other than ENOSYS/EINVAL skips the `nonce` cleanup, and a failed rmdir leaves `nonce-b`. Scratch with EPERM injected: two calls left two `.lhpc-atomic-probe-*` dirs (failures are not cached).

### CR7-15
- where: `lhpc/core/runtime_fs.py:356-373 (`open_marker_excl`)` · severity S3 kept (needs a transient ENOSPC/EIO; afterwards the operator must clean up by hand)
- claim: exclusive creation of an owned marker (journal/reservation)
- defect: On a write/fsync failure after the `O_EXCL` create (e.g. ENOSPC), fds are closed but the new leaf is not removed (`create_exclusive_bytes` 239-243 does remove it). The empty/partial leaf then reads as malformed: source-txn journal blocks all source mutation; auto-install reservation reports "already exists"/unsafe — manual cleanup after a transient disk-full.
- how to see it: Inject ENOSPC on `_write_all`; leaf remains, next `open_marker_excl` → FileExistsError.
- verifier: CONFIRMED — runtime_fs.py:356-373: the except path closes the fds but never unlinks the leaf (create_exclusive_bytes 239-243 does). Scratch g3/r15.py: after an ENOSPC injected in _write_all, a 0-byte leaf remains and the next open_marker_excl raises FileExistsError. install.py:1259 then reports an unreadable journal as "recovery-required … (retained)".



## What the plan must contain (≤ 200 lines, tables where possible)
1. **Analysis per finding**: the code path today (file:line), the defect, callers affected, what a test sees today.
2. **The change per finding**: exact function(s), the new behaviour in one sentence, the expected diff size, the
   risk (what working path could break and how the plan rules it out). If two findings share a fix, say so.
3. **Tests**: per finding the test module::name, what it asserts, why it is red before.
4. **Docs/CHANGELOG**: the sentences to change (file:line) and the CHANGELOG line.
5. **Order and commits**: one commit per finding, subject `<id>: <what>`; the order if one depends on another.
6. **Live proof**: whether a row on the Pi 5 is needed (an operator-visible path) and what it would show.
7. **Open questions** for the maintainer, each with your recommendation (≤ 5).
8. **Self-check**: re-read every claim against the code once more; list what you could not verify.
