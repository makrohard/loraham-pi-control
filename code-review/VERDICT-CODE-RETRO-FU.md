3m 32s nachgedacht
ChatGPT hat gesagt:
commit	verdict (OK / FINDING)	what
201086b1	OK	Implements the firewall follow-up narrowly: after failed first install, “restored” survives only if the table is already absent or an LHPC-owned table is successfully destroyed; unreadable/not-owned/failed-destroy states become NOT restored. The regression is genuinely red-before and the existing recovery/readback path is preserved. I see no materially simpler safer implementation. 
gate1-CODE-RETRO-FU

7723e4e8	OK	The new context manager covers the reserved-but-not-yet-successfully-spawned window and is deliberately hardened so failed settlement, hostile exception representation, or broken stderr cannot replace the original exception. The start/install/build paths are covered, and PathContainmentError is deliberately normalized as an ordinary spawn failure. The red-before tests exercise both settlement and exception identity. Centralizing this is simpler than three independent exception handlers. 
gate1-CODE-RETRO-FU

9d3b3871	OK	The changes address the demonstrated escape paths: non-finite floats become None, oversized/invalid integer conversions are caught, and recursive JSON is rejected to the raw-record path. The parametrized test contains both genuinely red cases and explicit preservation guards and then verifies json.dumps(..., allow_nan=False). This is a small, appropriate hardening rather than a parser redesign. 
gate1-CODE-RETRO-FU

5b2948e7	FINDING	The main transaction coverage is broadened correctly, including the formerly early-returning password-switch failure, and the supplied regressions are real. However, the claimed “every Exception … unwinds” guarantee is not actually achieved: for an unexpected exception the handler calls traceback.print_exc(), then repr(exc), then formats exc before reaching the recovery/unwind. A broken stderr, or an exception with hostile __repr__/__str__, can therefore raise from the error-reporting path and bypass binary_recover(), exactly the class of secondary-error problem F2 was hardened against. Recovery needs to happen before fallible diagnostics, or those diagnostics need their own non-throwing guards. Add a regression with broken stderr and/or an exception whose __repr__ raises and require the journal/files/password to be restored. 
gate1-CODE-RETRO-FU
 
gate1-CODE-RETRO-FU

e09f47c5	OK	The old inferred “whole stop” rule is replaced by the stated post-stop observation: one process-table snapshot yields none / some / indeterminate, and only none permits marker/candidate clearing for a per-band stop. Empty/unreadable process state fails closed; malformed and wrapped daemon invocations are conservatively retained. The disclosed four red-before cases are genuine additional regressions, not a defect in the new design. 
gate1-CODE-RETRO-FU
 
gate1-CODE-RETRO-FU

e3119ac6	OK	Uninstall prep now does exactly the intended sequence: inspect the persisted HMAC state, attempt auto-clear under the HMAC operation lock when eligible, re-read, and make the allow/block decision from that refreshed state. Exceptions remain fail-closed, and a known SID produces the recovery command. The auto-clear regression is red-before and the “auto-clear says success but state stays unsafe” test correctly remains a guard. 
gate1-CODE-RETRO-FU
 
gate1-CODE-RETRO-FU

8259c224	OK	The bare-update choice is now one core rule, importantly resolving a component to its owning stack before testing the installed channel. That fixes the CLI component case while preserving the already-correct web behavior. CLI is genuinely red-before, web is a useful preservation guard, and extracting the rule into one core helper is the simplest maintainable change. 
gate1-CODE-RETRO-FU
 
gate1-CODE-RETRO-FU

65f7038d	OK	The diff adds @invalidates_snapshot to the stated 15 mutators and adds an explicit marker to the decorator so the contract can be tested directly. Although all 33 parametrized cases were red on the parent because the marker itself was new, omitted new decorators would still remain red after the marker change, so the regression test is meaningful. Double/idempotent invalidation does not expose a correctness regression in the supplied paths. 
gate1-CODE-RETRO-FU
 
gate1-CODE-RETRO-FU

RED

The full-suite failure set being identical to the clean base is good evidence against unrelated regressions, but it does not cover the F4 secondary-failure hole above. 
gate1-CODE-RETRO-FU
