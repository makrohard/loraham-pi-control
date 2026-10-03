# CLOUD BRIEF · PLAN for fix group GL3a of loraham-pi-control (one Claude Code cloud session, read-only on the code)

You write the PLAN for a group of verified defects; you change no code. Output = ONE commit on this routine's branch
adding `plans/PLAN-GL3a.md`. Never touch `main` or `dev`, no pull request. (Attribution lines are stripped
downstream; the file is copied out.) Base: origin/main = e5187f70ae4e81a1081a835be06f84746222c3b6 (v0.11.10). Read `docs/architecture.md` (the safety
invariants), `docs/README.md`, `tests/README.md` (the house rules of the tests) and `docs/maintenance.md` (how a patch
is cut) first.

## The maintainer's rules for every change
Simple, robust, maintainable: the SIMPLEST change that removes the defect; no new mechanism unless the defect cannot be
removed without one (then say so and size it); no behaviour change beyond the defect; a regression test per change that
is RED before and GREEN after, placed where the maintainer would look for it (the existing test module of that file);
docs updated where a sentence becomes untrue (one place per fact); the CHANGELOG line in the operator's words.

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

## Adversarial self-review before the push (mandatory)
When everything is green, re-read your whole diff once more AS A HOSTILE REVIEWER who will be paid per finding: for every hunk ask what input, timing, caller or platform breaks it; what the old code handled that the new code does not; which test only passes because of the fake; which claim in your report you have not actually run. Fix what you find, re-run the gates, and list in the report what this pass found and changed (or 'nothing').
