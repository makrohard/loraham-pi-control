3m 5s nachgedacht
ChatGPT hat gesagt:
Gate 1 review — B1 CORRECTION 1

The package asks for the two amended fixes only to be judged against the original CR7-1 and CR1-1 findings. 
gate1-CODE-B1-corr1

1. CR7-1 — FINDING

The architectural change is the right one: moving the invariant into config_lock is much stronger than relying on startup recovery. A normal pending journal is recovered under the flock before the caller executes, and an unrecoverable malformed journal is tested to block the writer. 
gate1-CODE-B1-corr1

However, the promised typed refusal is not actually complete.

_finish_pending_journal() does this:

catches PathContainmentError from the initial journal-path probe and sets pending = True;
then calls recover_config_transaction(paths) with no exception translation;
only converts the special case where recovery returns a false value into ConfigRecoveryRequired. 
gate1-CODE-B1-corr1

This matters because the startup wrapper still explicitly catches both OSError and PathContainmentError around _finish_pending_journal(). That itself demonstrates that these exceptions remain possible during recovery. 
gate1-CODE-B1-corr1

So the correction does not fully implement its own stated contract:

recover the pending journal or refuse with ConfigRecoveryRequired / ConfigLockBusy.

For example, a containment failure can be detected by the first _txn_journal() evaluation, deliberately treated as recovery-required, and then escape raw when recover_config_transaction() encounters the same unsafe path. Likewise an OSError during recovery can escape raw.

The safety invariant is substantially improved—such an exception occurs before yield, so the writer does not proceed—but callers are no longer guaranteed the typed ConfigLockBusy refusal that this correction explicitly relies upon for uniform handling. That can turn the recovery condition into an unhandled/error-specific path instead of the promised clean refusal.

Required correction: inside _finish_pending_journal, translate recovery failures that mean “journal cannot safely be recovered” into ConfigRecoveryRequired, preserving the original exception as __cause__. The exact exception set should follow recover_config_transaction's documented failure contract; from this supplied diff, at minimum the already-acknowledged OSError / PathContainmentError cases need consideration.

The two new config tests are useful and genuinely exercise the original bug, but the refusal test covers only malformed JSON returning the expected failure state; it does not exercise these exception-producing recovery paths. 
gate1-CODE-B1-corr1

2. CR1-1 — OK

This closes the TOCTOU finding cleanly.

For an app TX test, admission is acquired first, then all applicable claim.loraham.radio.<band> keys are acquired, and _tx_band_conflict() is re-run after the claims are held. The guard then surrounds the transmit section, so a controller start cannot acquire one of those claims between the re-check and RF transmission. 
gate1-CODE-B1-corr1

The apply path likewise performs the running-state and competing-stack checks inside its existing lifecycle.<stack> + band-claim critical section. 
gate1-CODE-B1-corr1

The TX regression test is particularly valuable: it deliberately lets a competing start win immediately after the unlocked planning check, then verifies that the second check under the claim observes the new occupant and that no frame is emitted. 
gate1-CODE-B1-corr1

The apply concurrency test was already green against the previously reviewed production because that path already held the claim; therefore it is a pinning/regression test rather than red-before proof of this amendment. The correction report states that distinction explicitly, so there is no misleading test claim. 
gate1-CODE-B1-corr1

Judgment: OK. It is small, follows the stated admission → resource-lock ordering, preserves the daemon special case, and addresses the actual race rather than merely narrowing its window.

Final verdict: RED

One of the two original findings is correctly closed (CR1-1: OK), but CR7-1 still has a concrete gap in the required typed recovery-refusal boundary. The main stale-journal safety invariant is much better and the supplied test evidence is credible—including the reported full-suite delta of four additional passing tests—but I would correct the exception normalization and add a regression test for an exception-producing recovery failure before calling the batch green. 
gate1-CODE-B1-corr1
