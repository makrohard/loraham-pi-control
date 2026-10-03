2m 49s nachgedacht
ChatGPT hat gesagt:

I reviewed only the rewritten FOLLOW-UP section F1–F8, as requested, and treated the file as the complete input with no repository assumptions. 
gate1-PLAN-retro-v2

Follow-up	Verdict	Review
F1 — CR6-7 firewall recovery	OK	This now has a coherent recovery contract: the accepted snapshot remains authoritative, failed apply does not promote staged state, and health requires a later verification. The actual change is appropriately tiny and fixes the misleading first-install receipt. The RED-before unreadable-state test proves the code change, while the second test protects the wider recovery contract. 
gate1-PLAN-retro-v2

F2 — U-1 / CR1-6 reserved attempt settlement	FINDING	The main design is right, but it does not satisfy its own “re-raised unchanged” guarantee. settle_on_raise calls terminalize() inside the exception handler. The plan explicitly acknowledges that if terminalize() raises, that new exception replaces the original and the original merely survives as __context__. That is not “re-raised unchanged.” 
gate1-PLAN-retro-v2

F3 — CR9-11 rflog parsing	FINDING	The numeric fixes are correct for the demonstrated failures, but the stated contract is stronger than the change/test: “For any input line, parse_line returns a record.” On Python 3.11, JSON decoding itself can raise ValueError for an integer string exceeding the interpreter digit limit; the proposed test uses only a 400-digit JSON integer and tests the 5000-digit case only through the non-JSON len= parser. The plan therefore does not establish its universal no-raise guarantee. 
gate1-PLAN-retro-v2

F4 — CR4-6 binary transaction unwind	OK	Moving the auth-switch path into the common transaction try, broadening to Exception, preserving diagnostics for unexpected exceptions, and exercising a post-publish failure is the right simple architecture. The proposed state-restoration assertions are materially stronger than merely checking the returned error. The coding step should ensure the existing unwind result cannot silently mask the diagnostic, but I do not see a plan-level blocker here. 
gate1-PLAN-retro-v2

F5 — CR1-4 marker clearing	FINDING	The proposed predicate does not prove the stated guarantee. _whole = bool(cl) and not self._daemon_radio_modes(cl) establishes only “the mode parser found no recognized daemon radio mode.” Yet the plan explicitly promises that a daemon with an unknown --radio must keep the marker. Such a daemon can plausibly produce no recognized radio mode, making _whole true and clearing incorrectly. The three planned tests also omit this explicitly claimed unknown-mode case. 
gate1-PLAN-retro-v2

F6 — CR7-2 refreshed HMAC phase	OK	This directly addresses the earlier issue: attempt auto-clear under the same lock, then make the decision from a fresh persisted phase rather than either the old phase or the helper's boolean return. The second test is particularly useful because it prevents exactly that regression. The recovery command also makes the refusal actionable. 
gate1-PLAN-retro-v2

F7 — CR8-1 update channel rule	OK	This is the appropriate centralization: resolve component→stack once in core and have both adapters consume the same answer. The CLI RED-before regression plus web guard covers the required cross-surface consistency. Keeping explicit selectors untouched is also correctly scoped. 
gate1-PLAN-retro-v2

F8 — CR7-16 mutator inventory/meta-test	FINDING	This still does not provide the requested complete inventory guarantee. There are 325 public methods, but the proposed meta-test examines only methods returning ActionResult or taking apply—93 methods. The plan itself concedes that an unannotated public mutator without apply is not covered. Therefore a newly added snapshot-visible mutator can still bypass both the decorator and the allowlist without making the meta-test fail. 
gate1-PLAN-retro-v2
Required corrections
F2: Make settlement failure unable to replace the triggering exception. The handler should best-effort terminalize, separately record/log a terminalization failure, and then re-raise the original exception. Either weaken the absolute “leaves terminal” guarantee for storage failure, or provide a genuine fallback settlement mechanism. Add a test where terminalize() itself raises and verify that the originally injected exception is what escapes.
F3: Either narrow the contract to the numeric conversions actually being hardened, or make parse_line genuinely defensive at its external parsing boundary. At minimum add a JSON integer exceeding Python's digit limit and verify that it returns a record and still passes json.dumps(..., allow_nan=False). If the literal “any input line” guarantee remains, the test set needs to exercise decoder-level failure classes too.
F5: Decide from daemon presence, not from the set of recognized radio modes. From the single post-stop cmdlines snapshot derive a tri-state such as no daemon / some daemon / indeterminate, and clear only for positively observed no daemon. Add the missing daemon-with-unknown-or-malformed---radio regression.
F8: The meta-test must have a mechanically complete universe. Every public service entry in the chosen API boundary must be either snapshot-invalidating or explicitly snapshot-neutral; a heuristic based on return annotation/apply cannot establish that. The current admission that some public mutators are invisible to the test is exactly the hole the meta-test was meant to close.
Final verdict: RED

F1, F4, F6, and F7 are ready as planned. F2 has an exception-preservation contradiction, F3 overclaims its no-raise coverage, F5 conflates “no recognized mode” with “no daemon,” and F8 still permits unclassified public mutators.

The two strongest blockers are F5 and F8: both directly violate their stated guarantees rather than merely needing stronger tests. F2 is also a concrete exception-path correctness issue. Once those are corrected—and F3's contract/test scope is made consistent—I would expect this plan to be close to GREEN.
