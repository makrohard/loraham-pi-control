# CLOUD BRIEF · PLAN for fix batch B4 = groups GL3a + GL3b + GL3c + GL3d of loraham-pi-control (one Claude Code cloud session, read-only on the code)

You write the PLAN for a group of verified defects; you change no code. Output = ONE commit on this routine's branch
adding `plans/PLAN-B4.md`. Never touch `main` or `dev`, no pull request. (Attribution lines are stripped
downstream; the file is copied out.) Base: origin/main = e5187f70ae4e81a1081a835be06f84746222c3b6 (v0.11.10). Read `docs/architecture.md` (the safety
invariants), `docs/README.md`, `tests/README.md` (the house rules of the tests) and `docs/maintenance.md` (how a patch
is cut) first.

## The maintainer's rules for every change
Simple, robust, maintainable: the SIMPLEST change that removes the defect; no new mechanism unless the defect cannot be
removed without one (then say so and size it); no behaviour change beyond the defect; a regression test per change that
is RED before and GREEN after, placed where the maintainer would look for it (the existing test module of that file);
docs updated where a sentence becomes untrue (one place per fact); the CHANGELOG line in the operator's words.


# Group GL3a
## The findings of this group (verified; file:line on e5187f70ae4e81a1081a835be06f84746222c3b6)
**Group context / decisions:** Medium-risk items: CR2-6 band-scope the dependents (pass the stopped band, not ''), do not filter by run state; CR6-2 `::` only for bind-param listeners (nginx listen [::] is ipv6only=on); CR6-8 union the active sshd (addr, port) scopes; CR1-7 is part of G3 — list it here only if G3's plan does not cover it; CR4-5 also in G11 — plan once.

### CR2-6
- where: `lhpc/core/services.py:2456` · severity kept S3: the effect is a spurious ResourceBusy
- claim: Docstring: "Stack ids of RUNNING stacks that depend on `target` (for cascade stop)".
- defect: No run-state filter: every stack with a component whose `depends_on` hits the target's run order is returned. A cascade/daemon stop or a `restart --cascade` therefore also locks `lifecycle.<stack>` + claim keys of stopped, unrelated stacks (other band included). A concurrent start of such a stack in another process makes the stop fail `ResourceBusy` even though that stack is not touched.
- how to see it: With the daemon on 868 only, start a 433 client from a second process and, while it holds its locks, `lhpc stack stop daemon --band 868 --yes` → refused as busy on the 433 client's lifecycle key.
- verifier: PARTLY — Holds: `_dependents_of` (services.py:2456-2464) has no run-state filter. Scratch with nothing running: the lock keys for a daemon stop on 868 included `lifecycle.*` of every client plus the 433 claims, because dependents are added with band "" (l.2452-2453). Does not hold in full: locking stopped dependents is deliberate (service_lifecycle_ops.py:2342-2344, so none starts mid-cascade). The real defects are the wrong docstring and the other-band over-locking.

### CR4-5
- where: `lhpc/core/updater_units.py:454` · severity kept S3: needs an operator-made override in a path the check does not scan
- claim: `_has_dropin`: a drop-in "in the user unit dir or any system search dir" makes the unit OVERRIDDEN, so one-click and boot restore act only on a byte-exact, un-overridden unit.
- defect: The search covers only `~/.config/systemd/user`, `/etc/systemd/user` and `/usr/lib/systemd/user` (l.59). It misses `~/.config/systemd/user.control` and `$XDG_RUNTIME_DIR/systemd/{user.control,transient}` (where `systemctl --user set-property` writes), `/run/systemd/user`, `/usr/local/lib/systemd/user`, `~/.local/share/systemd/user`, and the type/prefix drop-ins `service.d/` and `lhpc-.service.d/`. A higher-priority fragment in `user.control` is not seen either, because `verify` reads only `~/.config/systemd/user/<unit>`. A sandbox-relaxing override there still reads `ok` for the bus-free gates: `self_update_trigger` and the boot-restore gate. Only the shell-side `--repair-integration` checks `DropInPaths`/`FragmentPath` authoritatively.
- how to see it: Create `~/.config/systemd/user.control/lhpc-web.service.d/x.conf` (or `~/.config/systemd/user/service.d/x.conf`) next to canonical units. `integration()["status"]` stays `ok`.
- verifier: CONFIRMED — `_has_dropin` searches only `_DROPIN_DIRS` (updater_units.py:59) plus the user dir (:470-484). Scratch with canonical units: a .conf under `user.control/lhpc-web.service.d`, `service.d/` or `lhpc-.service.d/` keeps status `ok`. Only `--repair-integration` asks systemd (service_selfupdate.py:1273-1284).

### CR6-2
- where: `lhpc/core/service_firewall.py:1385-1386 (used at 74-76)` · severity corrected to S3: needs the non-default advanced bind `::` plus compatibility mode. The das
- claim: `scope_covers` (firewall.py:173-185) states that a socket bound `::` also accepts IPv4 (bindv6only=0). Compatibility mode promises "unwanted stack ports blocked" (service_firewall.py:241; docs/firewall.md Modes).
- defect: `_classify_bind("::")` returns `("ipv6", "*")`. An unselected bind-param listener with bind `::` (the MeshCom bridge's `bind` uses validator `host`, which accepts `::`) gets only `meta nfproto ipv6 tcp dport P drop` (firewall_helper.py:319, 454-457). In compatibility mode (chain policy accept), its IPv4 side stays reachable while the status line reads Active · "unwanted stack ports blocked". The stack-start gate also accepts it, because `_fw_scope_modeled` matches family `ipv6` exactly.
- how to see it: Set meshcom bridge `bind = "::"`, firewall mode compatibility, endpoint not ticked. `firewall_candidate()` has an endpoint with `family:"ipv6"`. `render_nft_text` emits an IPv6-only drop, so an IPv4 client still connects to port 7000. A test asserting the candidate family is `dual` for a `::` bind would fail.
- verifier: CONFIRMED (code level) — service_firewall.py:1385-1386 gives `("ipv6","*")` for `::`; the bridge `bind` validator `host` accepts `::` (checked). firewall_helper.py:454-457 then emits `meta nfproto ipv6` only, and 990-991 matches the family exactly. Caveat: nginx `listen [::]` sets ipv6only=on by default, so the fix must apply only to bind-param listeners, not to the console bind

### CR6-8
- where: `lhpc/core/firewall_helper.py:914-917` · severity S3 kept: new SSH connections via other addresses are dropped until sshd restarts; establis
- claim: `resolve_ssh_scopes` docstring (879-883): the currently ACTIVE sshd listen ports are always unioned in, so a config transition can never cut recovery access.
- defect: Active ports are unioned by port number only. When `sshd -T` reports a precise `ListenAddress` for that port (e.g. `<lan-addr>:22`, edited but sshd not yet restarted), the port is in `covered`, so no wildcard rule is emitted for the still-active `0.0.0.0:22`/`[::]:22` socket. New SSH connections via any other address (AP address, IPv6) are dropped in secure-default until sshd restarts.
- how to see it: Fake `sshd -T` output `port 22` + `listenaddress <lan-addr>:22` and `ss -tlnp` showing `0.0.0.0:22 … sshd`. `resolve_ssh_scopes` returns only the `<lan-addr>` scope.
- verifier: CONFIRMED — Scratch: fake `sshd -T` printing `port 22` + `listenaddress <lan>:22`, and `ss` showing `0.0.0.0:22` and `[::]:22` for sshd. `resolve_ssh_scopes` returns only the `<lan>` ipv4 scope (firewall_helper.py:914-917; `active_sshd_ports` 922-936 drops the address). Fix: union the active (addr, port) scopes

### CR1-7
- where: `lhpc/core/service_lifecycle_ops.py:591` · severity kept S3: the plan says ok, the apply refuses; nothing is mutated before the refusal
- claim: `start()` docstring: "The plan (`apply=False`) and the apply take every decision alike … so a refused start is known before anything is queued or mutated."
- defect: The plan returns at 936-994. The firewall exposure gate (1029), the config-ambiguity refusal (1038) and the MeshCore position refusal (669, `start()` apply path only) run only on apply. The plan answers ok ("Run plan …"); the queued or applied start is then refused.
- how to see it: Same setup as CR1-2: `lhpc stack start <id>` without `--yes` shows a clean plan; `--yes` returns "Firewall changes pending". Red test: "start plan refuses what the apply's firewall gate refuses".
- verifier: CONFIRMED — The plan returns at 936-994, before the firewall gate (1029) and ambiguity (1038). The MeshCore position refusal runs only in the `start()` apply (655-669). Scratch: firewall gate stubbed to refuse; `start("kiss", apply=False)` returned ok "Run plan…". Caveat: `firewall_gate_stack_start` writes via `firewall_render()` (service_firewall.py:1018, 1026), so the plan needs a read-only variant.




# Group GL3b
## The findings of this group (verified; file:line on e5187f70ae4e81a1081a835be06f84746222c3b6)
**Group context / decisions:** Install-journal design: the ctime-bound ident goes stale across a rename made before the journal refresh — the plan decides between refreshing the journal before each rename and relaxing the ident for one state/leaf pair; candidate cleanup on recovery; power-loss ordering on ext4 reasoned, not assumed.

### CR3-1
- where: `lhpc/core/install.py:1772 (journal refreshed only at 1796-1797; recovery refuses at 1583-1586 and 1627-1629)` · severity kept S3: narrow crash window, but the managed source stays missing and all source mutation
- claim: `_recover_scan` docstring (1221): "Finish or roll back each INTERRUPTED source activation so the active source is never left missing". architecture.md, Source transactions: "journalled at every step".
- defect: The `planned` journal records the prior's v5 ident `[dev, ino, ctime]` while the prior is still at `dest`. The rename `dest -> .prev` at 1772 changes the prior's ctime (the comment at 1793 says so). The journal only gets the new ctime at 1797, after `txn.fsync()`. If the process dies or power is lost between 1772 and 1797, recovery finds `dest` absent and `.prev` failing `ident_matches`. It returns "archived prior could not be proven … (everything retained)". The managed source stays missing, so the stack cannot start. The retained journal also blocks every source mutation on the box (`_pending_journals`) until an operator renames `.prev` back by hand.
- how to see it: Write a v5 `planned` journal whose idents are taken while the prior is still at `dest`, then `os.rename(dest, prev)` and call `recover_source_activations()`. Result: `dest` absent, `.prev` and the journal retained. A test that expects the prior restored would fail.
- verifier: CONFIRMED — The `planned` journal holds the prior's ident taken at `dest` (install.py:1741). The rename is at 1772, the refresh only at 1796-1797, and recovery checks the v5 ctime at 1582-1586. Scratch: planned journal, `os.rename(dest, prev)`, then `recover_source_activations()`: "archived prior could not be proven … (everything retained)", with `dest` absent. A refreshed `prior-archived` journal recovers (control).

### CR3-2
- where: `lhpc/core/install.py:1839 (journal refreshed only at 1853-1854; recovery refuses at 1559-1564)` · severity kept S3: the new tree is active, no loss. `.prev` and the journal stay, and source mutatio
- claim: Same claim as CR3-1: recovery completes an interrupted activation, and the journal is cleared once the prior is proven removed.
- defect: Same pattern at promotion. `staging -> dest` (1839) changes the candidate's ctime. The `activated` journal with the refreshed ident is written only at 1854. A crash in between leaves the new tree active and the record writable. But `_prev_cleanup_ok(..., active=(dest, stale candidate ident))` fails at 1427, so recovery returns "archived prior could not be removed or was substituted". The journal and `.prev` stay forever, and all source mutation box-wide is blocked until manual cleanup. No data loss.
- how to see it: Use a `prior-archived` journal with idents taken before promotion, `os.rename(staging, dest)`, then `recover_source_activations()`. Result: journal and `.prev` remain, with recovery-required.
- verifier: CONFIRMED — The candidate ident goes stale with the promotion at install.py:1839; the refresh is only at 1853-1854. `_prev_cleanup_ok` fails at 1426-1427, so recovery returns at 1562-1564. Scratch: `prior-archived` journal, `os.rename(staging, dest)`, then recovery: "archived prior could not be removed or was substituted (journal + prior retained)".

### CR3-3
- where: `lhpc/core/install.py:526 / 724 (candidate created), 1565 (recovery clears the journal)` · severity kept S3: no data loss, but each interrupted update can leave a full orphan clone
- claim: Module docstring: a failed transaction leaves the active source untouched. `_cleanup_owned_staging` is "THE authoritative handle-safe staging cleanup". architecture.md: "clones a candidate beside the destination … journalled at every step".
- defect: A full clone is staged as `.<name>.candidate-<pid>-<ns>` long before the journal exists: the clone can take up to `_CLONE_TIMEOUT_S` = 900 s. A kill, reboot or power loss during the clone leaves the candidate with no journal. A crash after journal creation but before the archive (state `planned`, `dest` intact) is cleared by recovery as "active source intact" (1565), and the candidate is left behind. No code path ever removes such a candidate: nothing outside `install.py` handles `candidate-` leaves, and the names are unique, so later updates never collide with it. Each interrupted update can permanently waste a whole clone (RadioLib is about 114 MB) on the SD card.
- how to see it: Write a v5 `planned` journal next to an intact `dest` plus a staged candidate, then `recover_source_activations()`. The journal is removed, the candidate is still on disk, and it is still there after a later successful `adopt_source(force=True)`.
- verifier: CONFIRMED — The candidate is created at install.py:526/724, before the journal (1742). A `planned` journal with `dest` intact is cleared at 1565. `candidate-` appears nowhere in lhpc/ outside install.py (only a regex at 1087). Scratch: recovery returned "active source intact" and removed the journal; the candidate was still on disk after a later successful `adopt_source(force=True)`.




# Group GL3c
## The findings of this group (verified; file:line on e5187f70ae4e81a1081a835be06f84746222c3b6)
**Group context / decisions:** One shared safe-read helper (RecursionError / UnicodeDecodeError / TypeError / ValueError → the typed refusal) for the state readers; CR7-9 and CR7-14 were already fixed point-wise in 0.11.10 — the plan says whether they migrate to the helper or stay; auto_install.py:486 is another RecursionError site.

### CR7-10
- where: `lhpc/core/restart_required.py:283-286 (same class: jobresult.py:164, status.py:281, config.py:737-742)` · severity S3 kept (needs a crafted/corrupt state file or hand-edited local.toml)
- claim: `read_marker`: malformed marker → `{"unsafe": True…}`, GET-safe; config: hand-edits "must never crash config load" (700-701)
- defect: `json.loads` of a deeply nested document raises RecursionError, which none of these `except (ValueError, …)` catch: `read_marker` raises; `jobresult.read_results` raises; `_gps_feed_ready` raises on a 4 KiB marker of `[`; `load_config` crashes on a nested `[firewall] extra_allow` string (the TOML path itself converts RecursionError, 580-583).
- how to see it: Write `'['*5000` to `state/restart-required/x.json` → `read_marker(P,"x")` raises RecursionError (reproduced; same for jobresult).
- verifier: CONFIRMED — Line ref wrong: read_marker's json.loads is restart_required.py:53-55 (file has 142 lines); jobresult.py:164, status.py:281, config.py:737-742 are correct. Scratch g3/r10.py with '['*5000: read_marker, jobresult._read_raw/read_results, auto_install.read_marker (auto_install.py:486-487, catches only JSONDecodeError; not in the finding) and load_config (extra_allow string) all raise RecursionError.

### CR7-13
- where: `lhpc/core/runtime_fs.py:545 (`read_text`); lhpc/core/commands.py:147, 158 (`build_env`)` · severity S3 kept (needs a non-UTF-8 config or secret file; leads to a crash, not data loss)
- claim: `read_text` raises only PathContainmentError/OSError; `build_env`: a bad `@file:` secret raises CommandError
- defect: Strict UTF-8 decode raises UnicodeDecodeError. Callers catch only OSError/ConfigError/CommandError (config.py:1799 pre-image read, lifecycle.py:384-391, build_launcher_runtime.py:288-293) → CLI traceback / web 500 / crashed build. `build_env` also reads via `Path.read_text` (follows symlinks, unbounded, blocks on a FIFO).
- how to see it: `config/files/x.yaml` containing byte `\xff`, then `apply_config_transaction` → UnicodeDecodeError, not ConfigError.
- verifier: CONFIRMED — runtime_fs.py:545 decode raises UnicodeDecodeError (a ValueError, not an OSError). Scratch g3/r13.py: a \xff in config/stacks/chat.toml makes save_config_bundle raise UnicodeDecodeError out of config.py:1799. build_env with an @file: secret containing \xff raises UnicodeDecodeError, not CommandError (commands.py:147,158).

### CR7-9
- where: `lhpc/core/jobresult.py:112` · severity kept S3
- claim: module: "Every function is best-effort and NEVER raises (a GET must not 500)"; reads "STRUCTURALLY validated"
- defect: A terminal marker whose `finished_at` is a falsy non-string (`0`, `false`, `[]`) skips the 106-109 check and hits `_TS_RE.match(0)` → TypeError out of `read_results`. The banner caller (service_maintenance.py:1237) swallows it and drops ALL job entries incl. unsafe ones; `_web_unsafe_source_block` (service_lifecycle_ops.py:3339) does not catch it, so the unsafe-source gate raises.
- how to see it: `state/jobresults/a.log.json` = `{"op":"build","state":"done","log":"a.log","attempt_id":"abcdef12","finished_at":0}`; `read_results(P)` raises TypeError (reproduced).
- verifier: CONFIRMED — jobresult.py:112 calls `_TS_RE.match` on a non-str. The 106-109 guard is skipped because the value is falsy. Scratch run: `read_results` raises TypeError for finished_at 0/False/[]. The banner (service_maintenance.py:1237) swallows it and drops every job entry, unsafe ones included. `_web_unsafe_source_block` (service_lifecycle_ops.py:3339, called at 3422) does not catch it, so the web build POST raises.

### CR7-14
- where: `lhpc/core/config.py:1751` · severity S3 kept (needs a corrupt journal; the journal is kept, so nothing is lost, but every confi
- claim: journal recovery (1714-1716): a malformed journal "is NEVER treated as absent — it blocks"
- defect: `int(rec.get("mode", 0o644))` raises ValueError on a non-numeric mode (a non-str `pre` gives AttributeError in `_atomic_write`) — escapes `set_operator_identity` (catches OSError/ConfigError) after earlier targets may already be restored.
- how to see it: Journal with `"mode": "rw"` → `recover_config_transaction` raises ValueError.
- verifier: CONFIRMED — config.py:1751. Scratch g3/r14.py: journal target 1 is valid and target 2 has mode "rw". recover_config_transaction raises ValueError after target 1 is already restored, and set_operator_identity raises ValueError (it catches only OSError/ConfigError). With "pre": 5, it raises AttributeError.




# Group GL3d
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
5. **Order and commits**: one commit per finding, subject `<id>: <what>`, grouped by group in the order given; the order if one depends on another.
6. **Live proof**: whether a row on the Pi 5 is needed (an operator-visible path) and what it would show.
7. **Open questions** for the maintainer, each with your recommendation (≤ 5).
8. **Self-check**: re-read every claim against the code once more; list what you could not verify.

## Commit identity (the maintainer's rule — a direct violation otherwise)
Before your first commit run `git config user.name makrohard` and `git config user.email <the author e-mail of the makrohard commits in this repository: git log -1 --format=%ae --author=makrohard origin/main>`, and commit with that identity; no Co-Authored-By, Claude-Session or any AI-attribution line in any message. After each commit check `git log -1 --format='%an %cn%n%B'` shows makrohard twice and no such line; fix it with `git commit --amend --reset-author --no-edit` before you push.

## The plan-review packet (you build it too)
As a second file in the same commit add `plans/gate1-PLAN-B4.md`: a header for an independent reviewer with NO repository access ("This file is your whole input"; one paragraph per group in product terms — what the defects are and what the plan changes; the reviewer judges per change OK / FINDING on: simplest correct change, could it break a working path, are the tests real (red before), anything missing or riskier than necessary; final GREEN / GREEN WITH NOTES / RED), then the whole plan. No home paths (write $HOME), no e-mail, IPs, call signs, no 'threat/bypass/forge' wording.
