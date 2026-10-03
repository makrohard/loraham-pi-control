3m 21s nachgedacht
ChatGPT hat gesagt:

The overall direction is good—session-scoped progress detection is much better than simply raising 900/1800-second limits—but the current plan still has several correctness gaps that can reproduce exactly the failure F42 is meant to eliminate. The proposed rule is at 
gate1-PLAN-F42
, with the three-runner wiring at 
gate1-PLAN-F42
.

change	verdict (OK / FINDING)	what
Measure the whole child session, not only the top-level pip process	OK	This is the right unit. Compiler children, Python build helpers, etc. are exactly what must count as activity. /proc + session membership is appropriately simple and rootless.
CPU/output/I/O as independent progress signals	OK	The combination is sound as an operational liveness signal. CPU catches silent compilation, output catches tools that report progress, and I/O catches downloads/build activity that is otherwise quiet. It is intentionally not proof that useful work is being done.
Per-sample thresholds: 0.25 s CPU / 15 s and 64 KiB I/O / 15 s	FINDING	This can kill a job that is continuously making real progress. A severely contended/niced build doing 0.20 s CPU every 15 s makes ~8 s of CPU during the 10-minute window, yet every sample fails the threshold, so it is classified stalled. Likewise a download writing 50 KiB every 15 s is continually advancing but never reaches 64 KiB/sample. That directly violates the F42 invariant. Count any monotonic increase, or accumulate sub-threshold deltas instead of discarding them every sample.
Hard 4-hour ceiling while progress continues	FINDING	The plan still contains a fixed wall-clock failure cliff: a legitimate build that is compiling at hour 4 is killed solely because the box is slow. That conflicts with the literal requirement “a build must never fail only because the hardware is slow.” The plan itself acknowledges that a CPU-burning hang cannot be distinguished from productive compilation using these signals. 
gate1-PLAN-F42
 Either make the requirement explicitly “stall detection plus a safety budget which may terminate an active build,” or remove the absolute claim. A hard ceiling and an absolute “never because slow” guarantee cannot both be proven from these signals.
Recovering network waits	FINDING	“Ten minutes with zero CPU, zero output and zero I/O is not a wait that recovers on its own” is too strong. A userspace process blocked in a socket read during a long network outage can recover when connectivity returns. 
gate1-PLAN-F42
 There is no observational way here to distinguish “dead forever” from “blocked for 11 minutes then recovers.” A 600 s threshold can still be a sensible policy, but document it as a policy boundary rather than a proof of irrecoverable hang.
Central progress.py with Watch shared by runners	OK	Good design. One small mechanism with injected clock/sampler is simpler and more maintainable than three independent timeout implementations.
CLI Lifecycle.build → run_job → run_streaming	OK	The proposed wiring provides a single CLI-side policy and preserves existing timed_out/unsafe semantics, which minimizes collateral changes.
Web launcher effective ceiling	FINDING	Section 2 says the effective ceiling is max(comp.build_timeout, 14400), but the launcher section says it reads the F40 spec["step_timeout"] “as the ceiling”; that existing field contains the manifest timeout, including 1800 s for MeshCore. The only new _spawn_build field explicitly mentioned is stall_s. 
gate1-PLAN-F42
 Unless _spawn_build serializes the effective 4 h+ value or the launcher itself applies max(...), the web path retains the old 30-minute cliff. This needs to be explicit and tested.
Auto-install uses exactly the same rule	FINDING	The review request explicitly asks about CLI build, auto-install and web launcher. The plan establishes Lifecycle.build and the detached web launcher, but never identifies the auto-install call chain or gives an auto-install test. From this file alone I cannot establish that auto-install necessarily traverses Lifecycle.build. Add the concrete entrypoint/call chain and one regression test proving it receives the same stall and effective ceiling values.
Existing LHPC_BUILD_STEP_TIMEOUT_S remains usable	FINDING	The plan says both that the effective ceiling is max(comp.build_timeout, BUILD_CEILING_S) and that existing tests which set LHPC_BUILD_STEP_TIMEOUT_S to force a short timeout “keep working.” 
gate1-PLAN-F42
 Those statements need explicit precedence semantics. If the test sets the environment to 1 s but the manifest/default participates in max(), it cannot force 1 s. Define default-vs-explicit override behavior and test it on both CLI and launcher paths.
test_sleeping_step_is_killed_as_stalled, stall_s=1, sample 0.2 s, completes <5 s	FINDING	The production plan fixes sampling at 15 s, yet this test assumes a 0.2 s sampling interval. 
gate1-PLAN-F42
 No injectable/patchable sample interval is specified. With a real 15 s sampler, a 1 s stall cannot be detected in under 5 s. Make the sample interval injectable/monkeypatchable, or the proposed integration test is not implementable as written.
CPU-burning regression test	OK WITH NOTE	The intended behavior is useful, but “red today because the kwarg does not exist” is weaker than a behavioral RED. Stronger is a scaled test showing a continuously active process survives beyond the old wall-clock cutoff. The launcher needs the equivalent test too, because that is where the effective-ceiling wiring is currently ambiguous.
kill -STOP <pip pid> live stuck proof	FINDING	Stopping only the pip PID does not prove session-level stall handling. pip may already have gcc, cc1, a backend, or another child running; those descendants can continue accumulating CPU/I/O while the parent is stopped, correctly preventing a stall. The live proof should STOP all current members of the build session, then verify the sampler sees zero activity and the termination logic leaves no session member alive. The proposed proof is at 
gate1-PLAN-F42
.
/proc races and unreadable /proc/<pid>/io	FINDING	A process can disappear between reading /proc/<pid>/stat and /proc/<pid>/io; /proc/<pid>/io can also be unavailable even where stat is readable. Those are normal sampler conditions and must degrade that signal, not fail the build/watch. test_exited_member_is_not_negative does not necessarily exercise a mid-sample ENOENT/permission failure. Add one.
PID identity in per-PID delta accounting	FINDING	“Seen in both samples” must mean the same process, not merely the same numeric PID. On a busy long build a PID can disappear and be reused. Key samples by (pid, starttime) from /proc/<pid>/stat, otherwise a recycled PID can create a bogus positive or negative delta. This is especially cheap because stat is already being parsed.
utime/stime plus cutime/cstime	OK WITH NOTE	It can double-count child CPU when a child was previously sampled directly and is later reaped into the parent’s cutime. For a Boolean liveness decision this is mostly harmless—it biases toward “alive”—but it should be intentional and covered by the sampler tests.
Malformed stall override fails safe	FINDING	Add +inf to the malformed-value test. float("inf") is positive and is not NaN, but effectively disables stall termination forever. The parser should require math.isfinite(value) and value > 0. The intended fail-safe behavior is described at 
gate1-PLAN-F42
.
Constraints for MeshCore CLI	OK	Pinning the dependency closure is independently valuable and reduces surprise source builds. Keeping the common dbus-fast version aligned also improves cache reuse.
--prefer-binary on unpinned MeshCore steps	FINDING	pip --prefer-binary is not merely a performance hint: it can prefer an older wheel over a newer sdist. On an unpinned install that can change which upstream version gets installed. That is a behavioral/version-selection change hiding inside a timeout fix. Either constrain the relevant versions so selection is deterministic, or do not add --prefer-binary to genuinely unpinned “latest” paths. The proposal is at 
gate1-PLAN-F42
.
No pip -v	OK	Agreed. Compiler CPU is a better liveness signal; log amplification is unnecessary.
No piwheels injection	OK	Agreed. Adding another package index is unnecessary for the supported 64-bit path and materially enlarges the supply/maintenance surface.
Host tests retain bounded wall-clock timeout	OK	Agreed. Build and test semantics are different, and a bounded test timeout is already the simpler behavior.
Tail/detail distinguishes stalled from budget	OK	Good operator behavior and preserves existing state handling while making the actual termination reason visible.
Open questions

Q1 — delete the now-inert 900/1800/3600 manifest build_timeouts? Yes, provided the effective-ceiling computation is first made unambiguous and shared by CLI/auto-install/web. Keep only component values that deliberately exceed the common default. Otherwise the smaller values are misleading. 
gate1-PLAN-F42

Q2 — apply stall detection to host tests? No. I agree with the plan. Tests already have a deliberately bounded timeout; introducing build-liveness heuristics there adds complexity without solving the observed problem.

Q3 — is 600 s right? It is a reasonable first operational default, but not a proof that a wait is irrecoverable. I would keep 600 s after fixing the per-sample threshold problem. The important semantic statement should be “LHPC declares a build stalled after 10 minutes with no observable process activity,” not “such a wait cannot recover.” 
gate1-PLAN-F42

Q4 — pip -v? No. Agree with the plan.

Q5 — piwheels? No. Agree with the plan. Field-box architecture and configuration are useful diagnostics, but they should not drive adding another index to the supported installation path. 
gate1-PLAN-F42

The highest-priority corrections are: eliminate the per-sample progress-loss bug; make the web launcher receive the computed effective ceiling; explicitly cover auto-install; make sampling cadence testable; fix the live STOP proof; and resolve the contradiction between an absolute “never because slow” invariant and a 4-hour active-build cutoff.

RED — the architecture is close, but as written a genuinely progressing slow build can still be classified stalled, and the web path is not yet shown to escape its old manifest timeout.
