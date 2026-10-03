# CLOUD BRIEF · PLAN for fix group GL3d of loraham-pi-control (one Claude Code cloud session, read-only on the code)

You write the PLAN for a group of verified defects; you change no code. Output = ONE commit on this routine's branch
adding `plans/PLAN-GL3d.md`. Never touch `main` or `dev`, no pull request. (Attribution lines are stripped
downstream; the file is copied out.) Base: origin/main = e5187f70ae4e81a1081a835be06f84746222c3b6 (v0.11.10). Read `docs/architecture.md` (the safety
invariants), `docs/README.md`, `tests/README.md` (the house rules of the tests) and `docs/maintenance.md` (how a patch
is cut) first.

## The maintainer's rules for every change
Simple, robust, maintainable: the SIMPLEST change that removes the defect; no new mechanism unless the defect cannot be
removed without one (then say so and size it); no behaviour change beyond the defect; a regression test per change that
is RED before and GREEN after, placed where the maintainer would look for it (the existing test module of that file);
docs updated where a sentence becomes untrue (one place per fact); the CHANGELOG line in the operator's words.

## The findings of this group (verified; file:line on e5187f70ae4e81a1081a835be06f84746222c3b6)
**Group context / decisions:** Test-lab and demo items. CR10-7 was fixed in 0.11.10 (check). CR10-1: the lab's power() should simulate a crash (SIGKILL keeping the evidence) and run the real boot_restore_run — a lab design change, size it; CR10-4/5: one /proc cmdline liveness helper for both pid files; CR10-2/3: the demo must show the identity gate and the takeover confirm like production.

### CR10-1
- where: `testlab/lhpc_testlab/ops.py:304` · severity S3 (corrected from S2: affects only the test lab's reboot simulation and the confidence it
- claim: `power()` docstring and `spawn.py:3-6`: a "FAITHFUL simulated reboot" in which "boot-bound records behave exactly as on a real box", after which previously-running stacks are "restored".
- defect: The restore is `svc.start(sid, apply=True)` for every stack that was running. The production boot-restore path (`lhpc-boot-restore.service` → `boot_restore_run`: journal, evidence classifier, band policy) is never invoked. The stop result at `ops.py:297` is also discarded (`.ok` unchecked, exceptions swallowed), so a stack that did not stop still has its boot id advanced and is then started again.
- how to see it: The acceptance case `test_simulated_reboot_restores_running_stacks` stays green when `boot_restore_run` is broken, for example when the planner refuses every item. A case that runs the production restore after `_power --kind reboot` would be red.
- verifier: CONFIRMED — testlab/lhpc_testlab/ops.py:293-308: the stop results are discarded (`try/except: pass`), then `svc.start(sid, apply=True)` runs for each stack. Nothing in testlab/ calls `boot_restore_run` (grep finds only comments), so test_chain_roundtrip.py:168 never runs the production restore. A real fix can't just call `boot_restore_run` afterwards: `svc.stop` tears down the owned evidence the restore classifies, so the "reboot" must kill processes without the lifecycle stop.

### CR10-2
- where: `demo/lhpc_demo/service.py:430` · severity S3 (corrected from S2: demo only; no station is affected, but the demo misrepresents the i
- claim: `service.py:1-6`: everything except the lifecycle is "the unmodified lhpc app". `docs/architecture.md` (Identity): "a start with no resolvable identity is refused, never launched with a placeholder".
- defect: `DemoService.start`/`restart` (also `:533`) override the gated production start and never call `enforce_identity`. The demo seeds only the global callsign (`bridge.py:27`), so Meshtastic and MeshCore, whose node identities never inherit, start "successfully" with no node name. The real plan returns `ok=False` with `enforce_fields` and sends the operator to Settings.
- how to see it: Boot the bridge and call `run_action("start","meshtastic",apply=False)`: the result is `ok=True`. On the same service, `enforce_identity("meshtastic")` returns `(False, [c_node_name, c_node_short], …)`. Reproduced at the base commit.
- verifier: CONFIRMED — demo/lhpc_demo/service.py:430-489 never calls `enforce_identity`. In a scratch DemoService (seeded as bridge._seed does, plus seed_all_installed), `run_action("start","meshtastic")` returns ok for both the plan and the apply, while `enforce_identity("meshtastic")` returns `(False, ['c_node_name','c_node_short'], …)`. The fix also needs demo node identities seeded in bridge._seed.

### CR10-3
- where: `demo/lhpc_demo/service.py:469` · severity S3 (corrected from S2: demo only; the takeover is named in the summary text, but productio
- claim: demo/README: start/stop "with one-stack-per-band handoff", shown through the real console.
- defect: Production `start` refuses a band held by another stack unless `stop_owners` is set (`service_lifecycle_ops.py:1044-1048`), and the web shows a confirm page when the plan carries `data["blockers"]`. The demo plan returns no `data`, so the web skips the confirm, and apply stops the owners whatever `stop_owners` says (`:473-474`). Visitors see a silent takeover of the band that the real box never performs.
- how to see it: With graywolf running on 433, `run_action("start","meshcom",apply=True, stop_owners=False)` returns `ok=True`, "stopped kiss, graywolf". Reproduced at the base commit.
- verifier: CONFIRMED — demo/lhpc_demo/service.py:469-476: the plan's `data` is `{}` (no `blockers`), so lhpc/adapters/web/app.py:1633 skips the confirm. Scratch: with graywolf running on 433, `run_action("start","meshcom",apply=True,stop_owners=False)` returns ok with "stopped kiss, graywolf on band 433". Production refuses at service_lifecycle_ops.py:1044-1052.

### CR10-4
- where: `testlab/lhpc_testlab/supervisor.py:117` · severity S3 (corrected from S2: needs a stale pid file plus pid reuse after a container restart, la
- claim: `nginx_ctl` "escalate[s] until the old master is PROVEN gone" and starts nginx "exactly as the production user unit does".
- defect: Liveness is only `os.kill(pid_from_nginx.pid, 0)`. A stale `state/run/nginx.pid`, left when a Codespace stops with nginx killed, can name a reused pid of the same user. `start` then returns "already running" while no nginx serves (`:164-165`), and `stop`/`restart` send SIGQUIT/SIGTERM/SIGKILL to that unrelated process, which can be the console or a stack (`:142-153`).
- how to see it: Write a live pid of the lab user (e.g. a `sleep`) into `state/run/nginx.pid` and run the simulated `systemctl --user restart lhpc-nginx`: the `sleep` is killed. Run `start` instead: it reports ok and nothing listens on the proxy ports.
- verifier: CONFIRMED — testlab/lhpc_testlab/supervisor.py:117-125 checks liveness with `os.kill(pid,0)` only. Scratch nginx_pid.py, with a `sleep` pid written to state/run/nginx.pid: `nginx_ctl("start")` returns (True,'already running') and does not start nginx; `nginx_ctl("stop")` returns (True,'stopped') and the `sleep` dies of SIGQUIT (rc -3).

### CR10-5
- where: `testlab/lhpc_testlab/ops.py:364` · severity S3 (kept)
- claim: `_gpsd_pid` tells whether the fake gpsd is running, and `_respawn_gpsd` SIGTERMs "the old" one (`:377-382`).
- defect: Same pid-file-only liveness. `start.sh` never respawns the fakes after a Codespace restart, so `gpsd.pid` goes stale. A reused pid makes `check` report gpsd running, and the panel's Reset sends SIGTERM to whatever process now has that pid.
- how to see it: After a container restart, give the stale pid to another lab-user process, then run `lhpc-testlab check` (it reports "fake gpsd: running") and `reset` (that process gets SIGTERM).
- verifier: CONFIRMED — testlab/lhpc_testlab/ops.py:364-382. Scratch gpsd_pid.py, with a `sleep` pid in state/testlab/gpsd.pid: `_gpsd_pid` returns that pid (so check/status would say gpsd is running), and `_respawn_gpsd` sends it SIGTERM (rc -15). .devcontainer/start.sh never respawns the fakes.

### CR10-6
- where: `testlab/lhpc_testlab/ops.py:93` · severity S3 (kept)
- claim: `check` docstring: "ok requires the fakes alive". docs/testlab.md: `check` "reports the fakes".
- defect: Only gpsd is checked. The APRS-IS sink (`aprs_sink.py`), which graywolf's forced iGate needs, is never checked, and neither is the fake daemon process. Neither is respawned by `.devcontainer/start.sh` after a restart, yet `check` passes.
- how to see it: Kill the `aprs_sink.py` process and run `lhpc-testlab check`: it reports "Lab check passed.".
- verifier: PARTLY — Holds for the APRS-IS sink: ops.py:93-104 checks only `_gpsd_pid`, `_prepare_aprs_sink` (412-418) keeps no pid, and start.sh never respawns the sink. Does not hold for the "fake daemon process": that is the `daemon` lifecycle stack (ops.py:240), which reset installs but does not start, so `check` reporting its readiness and not its liveness is consistent with how every other stack is checked. With the sink down the iGate fails closed (the overlay still forces the local sink), so there is no live-network leak.

### CR10-7
- where: `demo/lhpc_demo/service.py:533` · severity S3 (kept)
- claim: `start`/`stop` refuse an optional component, "symmetric", so that the demo is "honest instead of reporting" an action it did not do (`:438-445`, `:495-502`).
- defect: `restart` has no such guard. Restarting an optional component (e.g. `meshcore-webui`) resolves to its stack, marks the whole stack running and reports "Restarted meshcore". The component itself stays shown as stopped (`build_snapshot` keeps optional parts STOPPED).
- how to see it: Call `restart("meshcore-webui", apply=True)` on a stopped meshcore: the result is ok and the stack is running, but the component the operator asked for is not.
- verifier: CONFIRMED — demo/lhpc_demo/service.py:533-565 has no optional-component guard, unlike :438-445 and :495-502. Scratch: `start("meshcore-webui")` is refused, but `restart("meshcore-webui", apply=True)` returns ok with "Restarted meshcore", the stack runs, and the snapshot still shows meshcore-webui as STOPPED.



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

## Commit identity (the maintainer's rule — a direct violation otherwise)
Before your first commit run `git config user.name makrohard` and `git config user.email <the author e-mail of the makrohard commits in this repository: git log -1 --format=%ae --author=makrohard origin/main>`, and commit with that identity; no Co-Authored-By, Claude-Session or any AI-attribution line in any message. After each commit check `git log -1 --format='%an %cn%n%B'` shows makrohard twice and no such line; fix it with `git commit --amend --reset-author --no-edit` before you push.
