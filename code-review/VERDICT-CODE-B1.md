4m 58s nachgedacht
ChatGPT hat gesagt:
Gate 1 review — B1

Final verdict: RED

The supplied evidence supports genuine RED-before/GREEN-after tests and shows the final suite has the same 97 environment failures as the base, with 20 additional passing cases. 
gate1-CODE-B1
 However, I find two correctness gaps in the implemented fixes.

CR	Verdict	Review
CR7-1	FINDING	Startup-only journal recovery does not establish the required invariant.
CR7-3	OK	Minimal correction; required main-gone becomes failure while optional behavior stays unchanged.
CR7-4	OK	Appropriately scoped YAML insertion; the added list-tail regression addresses the main structural risk.
CR7-5	OK	Small, coherent quoting change with real red/green coverage. Refusing unrepresentable values is safer than corrupting them.
CR7-6	OK	Uses saved launch parameters without introducing a parallel execution path; sensible validation handling.
CR9-2	OK	The raw-fd poll + cooperative stop directly removes the buffered-reader close deadlock and correctly marks incomplete streaming output unverified.
CR9-3	OK	Owner detection is performed while holding the daemon/band start locks, making the check/probe sequence appropriately serialized.
CR1-1	FINDING	Apply is serialized correctly, but the TX protection is only a state check and is not shown held under the band claim through transmission.
CR7-1 — FINDING: stale journals can still precede a non-transactional write

The new helper explicitly returns and lets execution continue when the config lock is busy or recovery encounters OSError/PathContainmentError. 
gate1-CODE-B1
 The plan itself says “lock busy → skipped”. 
gate1-CODE-B1

That is unsafe because the defect being fixed is specifically that a non-transactional write may happen while the stale journal still exists. A command can time out after two seconds in startup recovery, continue, later acquire the config lock in its actual writer, write new hardware/GPS/operator state, and leave the old journal available for a subsequent transaction to replay.

There is also an acknowledged, more direct residual case: if a CLI transaction crashes while the web console is already running, that console does not execute the new startup hook again. The plan explicitly acknowledges that the console will not see the journal until its next transaction. 
gate1-CODE-B1
 A non-transactional web save in between therefore remains vulnerable.

So the product-level guarantee is not yet achieved. The happy-path test proves recovery on a newly started uncontended CLI, but does not test either residual ordering. The report nevertheless states the broader guarantee that a later non-transactional save cannot be undone. 
gate1-CODE-B1

Required correction: establish the invariant at the write boundary, under config_lock: before any non-transactional config writer changes files, recover a pending journal or refuse the write. Ideally centralize this so every writer cannot forget it. Startup recovery can remain as eager cleanup, but should not be the correctness mechanism.

CR1-1 — FINDING: TX conflict check is TOCTOU

apply_daemon_params performs its new _band_used_by_others() check while holding claim.loraham.radio.<band>, which is correct. 
gate1-CODE-B1
 The plan itself specifically describes the existing-band-lock protection as applying to Apply. 
gate1-CODE-B1

The TX path, by contrast, computes the READY bands and then calls _band_used_by_others() as an ordinary check. The supplied change shows no band claim held across that check and the subsequent TX operation. 
gate1-CODE-B1
 Thus another stack can start on that band after the check but before transmission.

The new test proves only the static case where the conflicting stack is already running; it does not exercise that race. 
gate1-CODE-B1
 For an RF-safety/exclusivity rule, check-and-act needs the same serialization used by start/apply.

Required correction: hold the appropriate claim.loraham.radio.<band> lock(s) across conflict re-evaluation and the actual TX test, using the established lock ordering. Add a regression that forces a competing start at the check/TX boundary.

Everything else in B1 is acceptable from the supplied evidence. In particular, the red-before/green-after table is substantive rather than cosmetic, including the three required/three optional CR7-3 cases, the real escaped-descendant processes for CR9-2, and the safety tests for the two radio changes. 
gate1-CODE-B1

Gate result: RED — fix CR7-1 and CR1-1, then re-review.
