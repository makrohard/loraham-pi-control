# CLOUD BRIEF · PLAN for F42: a build must never fail only because the hardware is slow (one Claude Code cloud session, read-only on the code)

Output = ONE commit on this routine's branch adding `plans/PLAN-F42.md`; never touch main/dev/code-review/brief; no pull request. Base origin/main = e5187f70 (v0.11.10); F39/F40 (the launcher honours the manifest timeouts; meshcore-cli 1800 s + marker; honest "timed out") is landing as 0.11.11 — read `git show origin/code-review/brief:code-review/F39-F40-meshcore-build-timeouts.md` and `…:code-review/PLAN-F39-F40.md` first; read docs/architecture.md, docs/maintenance.md, tests/README.md.

## The maintainer's words (2026-10-03): "Non-updatable stacks are BAD. Just raising the timeout seems to be a bad solution."
A Raspberry Pi that needs 35 minutes for one `pip install .` step (source builds of dbus-fast / PyNaCl / pycryptodome on armv7, a slow SD card, a throttled CPU) must NOT end with a killed, incomplete build — that box can never update that stack. A build that is genuinely stuck (a hung network read, a deadlocked child) must still end.

## The plan must settle
1. **What distinguishes slow from stuck**, measurably on the box: CPU time of the build's process group (/proc/<pid>/stat utime+stime over the tree, or the cgroup if the launcher runs in one), output progress (pip is silent off-TTY — say how to make it talk: `pip -v`/`--progress-bar off` with unbuffered output, PYTHONUNBUFFERED), network activity. Propose the simplest robust STALL rule (e.g. "no CPU time and no output for N minutes → stuck", N ≈ 10) and keep the per-component budget as a generous OUTER ceiling (hours, not 30 min) that only protects against runaway loops.
2. Where it lives: `run_job` (CLI/auto-install path), the web launcher runtime (`build_launcher_runtime.py`), `commands.run_streaming` — one rule for all three; what each path can observe; the detail texts ("stalled for N min: …" vs "exceeded the budget of N h").
3. The cheap wins first: `pip --prefer-binary` and the piwheels index for the build venvs on armv7 (deps.py / the pip config the installer writes), constraints files for MeshCore like webui/repeater already have; whether the lab or the images can verify an armv7 build at all.
4. Tests (red before): a fake step that sleeps with no CPU → killed as stalled; a fake step that burns CPU for longer than the old 1800 s → completes; the budget ceiling; the detail texts. Live proof on the Pi 5: a real MeshCore build from the web completes; `top`-visible compile is not killed.
5. Risk: a stall rule that is too eager kills legitimate waits (a slow git clone, a network stall that recovers) — how the plan avoids that (network activity counts as progress? a per-step grace?). State what cannot be verified without the field box (its arch, pip.conf, durations).
6. Commits, docs (one place: docs/maintenance.md build section), CHANGELOG in the operator's words, open questions with recommendations (≤ 5), self-check. ≤ 220 lines.

## Commit identity (the maintainer's rule)
Before your first commit run `git config user.name makrohard` and `git config user.email <the author e-mail of the makrohard commits: git log -1 --format=%ae --author=makrohard origin/main>`; no Co-Authored-By/Claude-Session/AI-attribution line; check `git log -1 --format='%an %cn%n%B'` before the push.

## Adversarial self-review before the push (mandatory)
When everything is green, re-read your whole diff once more AS A HOSTILE REVIEWER who will be paid per finding: for every hunk ask what input, timing, caller or platform breaks it; what the old code handled that the new code does not; which test only passes because of the fake; which claim in your report you have not actually run. Fix what you find, re-run the gates, and list in the report what this pass found and changed (or 'nothing').
