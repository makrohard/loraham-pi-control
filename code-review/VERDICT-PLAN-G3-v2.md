3m 10s nachgedacht
ChatGPT hat gesagt:
Gate 1 re-review — PLAN G3 v2
Item	Result	Review
Shared preflight lacked exclude_holders	OK	Fixed cleanly. _start_preflight_refusal(..., *, check_blockers, render, exclude_holders=frozenset()) now has the required keyword-only parameter, and exclusion is applied only to blocker holders rather than changing firewall/ambiguity behavior. 
gate1-PLAN-G3-v2

Start PLAN evaluated MeshCore position at a different point/order than APPLY	OK	_start_outer_refusal() centralizes identity → position, and both start PLAN and APPLY invoke it after band_refusal and before _start_impl. The healthy-order predicate is shared as well. This closes the specific v1 ordering defect. 
gate1-PLAN-G3-v2

exclude_holders semantics were unspecified	OK	v2 now defines the identity domain and the exact exclusion set: own stack for whole-stack restart, plus the dependent stack IDs returned by the same dry-run stop plan when the restart will cascade. Non-cascaded dependents remain blockers. 
gate1-PLAN-G3-v2
 
gate1-PLAN-G3-v2

Own optional component / cascaded dependent false-positive test missing	OK	The mandatory test is present and is materially sound. It injects both holder classes, independently asserts their stack_of() domains and the stop-plan dependent identity, verifies cascade=True does not falsely refuse, and verifies cascade=False still refuses on graywolf. That catches both a missing exclusion and an over-broad exclusion. 
gate1-PLAN-G3-v2

Restart refusal wording misleading	OK	Firewall refusals now explicitly say restart was not performed and the running stack was left up, instead of reusing a post-stop/start-style message. The regression test also checks both wording and actual running state. 
gate1-PLAN-G3-v2
 
gate1-PLAN-G3-v2

Regression tests were not genuinely red-before	OK	The new firewall, ambiguity, position-order, restart-before-stop and external-blocker tests all contain expectations that the described e5187f70 behavior cannot satisfy. The old healthy-running test is now correctly labelled characterization rather than defect proof. For the mandatory exclusion test, the cascade=True half is naturally a guard against a new false positive and may pass before the change, but the same test's cascade=False half is explicitly red-before; that is legitimate. 
gate1-PLAN-G3-v2
New finding — PLAN/APPLY parity is still not literally exact

FINDING. The plan explicitly specifies different blocker behavior:

start PLAN: check_blockers=False
start APPLY: check_blockers=not stop_owners
restart: check_blockers=apply and not stop_owners

and later summarizes this as “blockers on apply only.” 
gate1-PLAN-G3-v2
 
gate1-PLAN-G3-v2

The start difference was apparently already accepted for the web owner-confirm workflow, so I would not reopen that earlier accepted design merely because PLAN and APPLY are semantically different there. But v2 cannot simultaneously claim that overall PLAN/APPLY parity is “exact.” The correct statement is narrower: the position/firewall/ambiguity ordering targeted by CR1-7 is now aligned, subject to the intentionally different blocker policy.

There is a second parity uncertainty in the text: the restart PLAN retains _start_impl(apply=False) for GPS/radio-mode refusals which it says that path “alone covers.” On the supplied plan alone, that does not establish equivalent pre-stop APPLY coverage. 
gate1-PLAN-G3-v2

New finding — concrete-band specification contradicts itself

FINDING. The restart call-site specification says the new preflight receives band and computes _daemon_needs(..., band). The dependent exclusion's stop plan likewise uses band. 
gate1-PLAN-G3-v2

But the risk analysis later asserts that preflight and the nested start use the same concrete band, specifically _rband. 
gate1-PLAN-G3-v2

With no repository access, I cannot establish that band == _rband at that location. The implementation plan should therefore say _rband consistently at the preflight, _daemon_needs, and exclusion stop-plan calls, or explicitly establish that band has already been normalized to that value. Otherwise the very parity being protected can differ by band.

There is also a small documentation typo: the proposed CHANGELOG says the restart “says the running stack was left up (it left the stack down).” Presumably that should be “previously left the stack down” or the parenthetical should disappear. 
gate1-PLAN-G3-v2

Verdict: RED

The v1 findings themselves are substantively repaired, and the exclude_holders test is now present and well designed. The blocker for GREEN is the new specification gap: v2 claims exact PLAN/APPLY/band parity while its own call-site description does not actually specify that consistently, and literal PLAN/APPLY parity remains explicitly non-identical.

The minimal correction is small: make the restart preflight/exclusion use the same resolved _rband explicitly, clarify the intended parity claim as “same refusal/order except the deliberate blocker-plan policy,” and state where the APPLY gets the GPS/radio-mode equivalent checks before the stop. Then this looks ready for another gate pass.
