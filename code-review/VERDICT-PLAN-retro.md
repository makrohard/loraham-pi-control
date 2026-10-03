2m 32s nachgedacht
ChatGPT hat gesagt:

I reviewed this strictly as a plan audit, with no repository assumptions beyond the file. I treated each item’s stated guarantee as the boundary for its complete/incomplete verdict, consistent with the plan’s method and its explicit verification limitations. 
gate1-PLAN-retro

id	your verdict (agree / disagree: why)
CR7-2	agree — incomplete. Adding unsafe closes the immediate false-safe path, but the plan itself shows uninstall prep behaves differently from the other HMAC gates: no auto-clear and no recovery guidance. 
gate1-PLAN-retro

CR7-11	agree — complete. The rule now directly represents the stated invariant: running sidecars cannot make a non-running required main look healthy.
CR7-8	agree — complete. Unknown installed state is no longer represented as current; refusal for both dry-run and apply matches the guarantee.
CR7-16	agree — complete for uninstall. The missing global mutator sweep is legitimately separate; it does not make the uninstall fix itself incomplete. 
gate1-PLAN-retro

CR7-7	agree — complete. The added patterns plus explicit cross-stack prefix check adequately support the stated clean invariant.
CR1-8	agree — complete. Validation and canonical transmission both address the defect. Rejecting formerly accepted hand-edited noncanonical overrides is a deliberate tightening, not a remaining violation.
CR1-5	agree — complete. The specific escaping ResourceBusy path becomes typed, and the plan reports a sweep of the other admission sites.
U-2	agree — complete. The fallback is constrained exactly where the stated invariant permits it.
CR2-1	agree — complete. The remaining scoped spelling is not produced by LHPC/HMAC according to the plan, so it does not contradict the operational guarantee.
CR1-4	agree — incomplete. Configured bands are the wrong predicate; the marker/candidate must depend on what actually remains running after the stop. 
gate1-PLAN-retro

CR1-6	agree — complete for the reported launcher-write defect. The known unexpected-exception hole should nevertheless be handled as a separate follow-up; see below.
U-1	agree — complete for admission release. with _sec fixes the stated resource leak. However, the plan explicitly identifies a separate reserved-attempt leak on an unexpected secondary exception. 
gate1-PLAN-retro

CR2-5	agree — complete. The failed-switch path now uses the same unwind mechanism as the other failed switches and reports failed restoration.
CR7-9	agree — complete. The identified malformed terminal marker no longer escapes read_results.
CR4-4	agree — complete. The undecodable-content defect is closed. How repair treats UNREADABLE is a separate behavior question, not evidence this fix is incomplete.
CR8-4	agree — complete. The only normal CLI caller is prevalidated, satisfying the stated no-traceback CLI guarantee.
CR8-2	agree — complete. The guarantee is typed handling, not ASCII-canonical port syntax. Accepting underscores/Unicode digits therefore does not contradict this item.
CR8-3	agree — complete. Same reasoning as CR8-2.
CR7-15	agree — complete. The inode-checked cleanup plus replacement guard matches both halves of the guarantee.
CR4-8	agree — complete. Cleanup now covers the stated failed-probe path.
CR6-9	agree — complete. Full-write looping plus temp cleanup meets the atomic-write guarantee stated here.
CR6-7	agree — complete for receipt truthfulness, but it exposes a separate, important recovery gap: after restore failure the journal is removed while live firewall state is unknown. That needs a follow-up. 
gate1-PLAN-retro

CR6-1	agree — complete. The reconstruction now follows the production AP scoping rule. Duplication is maintainability debt, not a correctness failure in this item.
CR7-14	agree — complete. Validation occurs before restoration begins, which is the critical transactional property stated by the guarantee.
CR4-6	agree — incomplete. Enumerating two additional exception classes still does not satisfy “every failure between begin and commit unwinds.” 
gate1-PLAN-retro

CR8-1	agree — complete for the web defect. The web path is repaired; the discovered CLI/component inconsistency justifies the shared-helper follow-up. 
gate1-PLAN-retro

CR4-3	agree — complete. Failure-time handling and next-run reconciliation are described as consistent for from, to, unreadable, and foreign HEAD cases.
CR5-3	agree — complete. Existing-running nginx is restarted and success is gated on listeners matching; both parts of the guarantee are addressed.
CR9-1	agree — complete. Given the stated allowlisted-name precondition, whitespace splitting does not create an unresolved supported-case defect.
CR9-4	agree — complete. Recording before rendering closes the partial-render rollback hole, and pre-existing unit refusal protects ownership.
CR9-8	agree — complete, with verification caveat. The shell reasoning is sound for the current one-line block, but this remains source-reviewed rather than executed in the stated environment. The plan already discloses that limitation. 
gate1-PLAN-retro

CR9-9	agree — complete. Connection failure and normal session termination now retain distinct states as required.
CR9-10	agree — complete. Socket-creation failure advances to the next address instead of prematurely ending resolution.
CR9-11	agree — incomplete. The plan demonstrates two remaining classes: integer overflow and non-finite floating values reaching JSON. 
gate1-PLAN-retro

CR9-13	agree — complete. Delegating to the common coordinate validator closes the duplicated validation divergence.
CR10-7	agree — complete. Restart now has the same optional-component refusal as start/stop. The third copy is maintainability debt only.

I therefore would not downgrade any of the 32 items currently marked complete, if “complete” means complete against that item’s explicitly stated defect/guarantee. Two of those complete items nevertheless reveal additional correctness work that the follow-up section currently omits.

Follow-ups

CR9-11 — necessary, but strengthen the proposal. The proposed _int OverflowError handling and finite check are correct directions. 
gate1-PLAN-retro
 The plan should additionally require that _num itself cannot raise during numeric conversion—for example, a scalar too large to convert to a finite float must also become None. Test all JSON-exposed numeric paths (timestamp, rssi, snr, size), and include a strict serialization assertion such as json.dumps(record, allow_nan=False). Merely testing rssi: NaN does not fully prove the stated “every returned record serializes as valid JSON” guarantee.

CR4-6 — necessary, but “catch Exception and wrap it as BinaryInstallError” is too loose as written. 
gate1-PLAN-retro
 The important invariant is unwind on every Exception. Do not let the broad catch silently erase programming-error diagnostics. The simplest robust shape is: catch Exception, perform/guarantee the transaction unwind, preserve/log the original exception, and then produce the required typed failure. Do not catch BaseException. The regression should assert the important state restoration—not merely that a ValueError becomes typed: journal closed/removed as appropriate, password restored, and partial transaction state undone.

CR1-4 — necessary; proposal is right but needs fail-closed semantics. 
gate1-PLAN-retro
 Decide _whole from an authoritative post-stop observation of remaining daemon instances. Clear marker/candidate only when LHPC positively knows none remain. If that observation itself is unavailable/indeterminate, retain them. Add three cases: only stopped band had been running → clear; another band still runs → retain; post-stop running-state probe indeterminate → retain.

CR7-2 — necessary; proposal is basically correct. 
gate1-PLAN-retro
 One missing detail: after _hmac_try_auto_clear, use the refreshed/resulting phase, not the phase value read before the attempt. An auto-clear failure or still-unsafe result must remain blocking. next_commands belongs on that remaining refusal.

CR8-1 — necessary and a good simplification. 
gate1-PLAN-retro
 Put the rule in core/service code so neither adapter owns channel semantics. The helper must normalize via stack_of(target) itself. Add a regression specifically using a component target through both CLI and web, because that is the divergence the plan discovered.

CR7-16 — necessary, but do not mechanically decorate every mutator. 
gate1-PLAN-retro
 Inventory public operations that can alter snapshot-visible state. Decorate those; explicitly allowlist/document operations that cannot. Ideally add a small invariant/meta-test so a newly added public mutator cannot silently bypass the policy. Blanket decoration is not dangerous in most cases, but it encourages cargo-cult invalidation and can hide what the cache contract actually is.

Two missing follow-ups should be added:

CR6-7 failed-restore recovery semantics. The plan explicitly says that after restore failure the journal is still unlinked, the live firewall ruleset is unknown, and there is then no recovery record. 
gate1-PLAN-retro
 That is a correctness/recoverability issue, not merely cleanup. Do not blindly decide “keep the existing journal”; first define the recovery contract. The simplest acceptable guarantee is something like: a failed restore leaves durable evidence that firewall state is indeterminate, and subsequent check/mutation cannot silently treat it as healthy. Then implement either an idempotently retryable journal or a dedicated recovery-required marker and test the next-operation behavior.

U-1 / CR1-6 reserved secondary attempt on unexpected exception. The plan already notes that an exception after secondary reserve can permanently leave the attempt as "starting". 
gate1-PLAN-retro
 “Only a programming error can cause it” is not enough for persistent job state. Add exception-safe settlement: if an exception occurs after reserve but before successful spawn, terminalize the reserved attempt and then re-raise the programming error. That preserves diagnostics and prevents a wedged job record.

The CR9-8 multiline-shell observation is worth documenting, but I would not make it a mandatory follow-up unless multiline packaged-service blocks are actually supported by the manifest contract. Likewise, the duplicated firewall rule, duplicate atomic writers, and third demo guard are maintainability opportunities rather than necessary corrections to this gate.

RED — the 36 per-item verdicts are essentially sound, but the follow-up plan should be changed before coding: CR6-7 and the secondary reserved-job exception path are missing correctness follow-ups, and CR9-11/CR4-6 need slightly stronger acceptance criteria.
