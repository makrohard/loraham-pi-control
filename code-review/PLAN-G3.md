# PLAN G3 — restart/start refusals decided before anything is stopped or promised

Base: `origin/main` = `e5187f70` (v0.11.10). Line numbers are this SHA's. Findings: **CR1-2** (S2), **CR1-7** (S3).
They share one fix: **one read-only start preflight** used by the start plan, the start apply and the restart
before its stop leg.

## 1. Analysis

| | CR1-2 restart stops, then the nested start refuses | CR1-7 start plan says ok, the apply refuses |
|---|---|---|
| promise | `_restart_impl` docstring 2866-2868: "a valid target is never stopped only to be rejected by the later start" | `start()` docstring 591-593: "The plan … and the apply take every decision alike … a refused start is known before anything is queued or mutated" |
| path today | public `restart()` 2638 → identity 2694, saved-launch 2701, MeshCore position 2711 (all pre-stop) → `_restart_impl_inner` 2886: identity 2947, saved-launch 2949, dep-band 2957-2962 → **stop 2969** → `self.start(apply=True)` 2979 → `_start_impl_inner`: firewall gate **1029**, ambiguity **1038**, blockers without `stop_owners` **1045-1055** | `start(apply=False)` 618 → `_start_impl_inner` plan branch **936-994** returns `ok=True "Run plan …"`. Not checked there: firewall gate 1029, ambiguity 1038 (both after the plan return), MeshCore position 669 (in `start()` apply only) |
| defect | the three 1029/1038/1045 refusals run only after the stop; the stack is left down and the summary reads "Restarted 'x'. Cannot start …" (2996) | plan ok → the queued (web job) or `--yes` start refuses |
| callers | CLI `lhpc stack restart --yes` (main.py:1330), web Restart job (`_stack-start --restart`), boot paths calling `restart(apply=True)` | CLI dry run `lhpc stack start <id>`, web Start (app.py:1526/1638 build the confirm page from the plan), restart plan (2919 calls `_start_impl(apply=False)`) |
| test sees today | firewall gate stubbed to refuse: `restart("kiss", apply=True)` → `ok=False`, `results` contain the stop rows, kiss down | same stub: `start("kiss", apply=False).ok is True` |

Not a refusal to cover for restart: MeshCore position — public `restart()` already takes it pre-stop (2711) and hands
it down. Run blockers in the start **plan** are deliberately *not* a refusal: the plan reports them as `data.blockers`
and the web turns them into the *Stop owner(s) & start* confirmation (app.py:1638). That stays.

Write hazard: `firewall_gate_stack_start` calls `firewall_render()` (service_firewall.py:1021, 1029), which writes the
three operator scripts. A plan (also reached from web GET-ish planning) must not write → a read-only mode is needed.

## 2. The change (one shared fix, no new mechanism beyond one helper + one kwarg)

**a) `firewall_gate_stack_start(target, band="", *, render=True)`** (service_firewall.py:999). `render=False` skips
the two `self.firewall_render()` calls; decision and texts unchanged. Default keeps every existing caller/test as is.
~4 lines.

**b) New `_start_preflight_refusal(self, target, order, band, radio, op, *, check_blockers, render)`** in
service_lifecycle_ops.py, beside `_identity_refusal` (2852) and documented the same way ("the one wording the start
plan, the start apply and the restart preflight share"). Returns an `ActionResult` or None, in today's order:
1. firewall gate → `ActionResult(False, f"Cannot {op} '{target}': {msg}", next_commands=cmds, data={"firewall_gate": "pending"})` (moved verbatim from 1029-1032);
2. `_config_ambiguity` → `f"Cannot {op} '{target}': {amb}"`, `next_commands=[f"lhpc config {target}"]` (moved verbatim from 1038-1041);
3. only if `check_blockers`: `run_blockers(target, band, radio)` non-empty → the existing "Cannot run '{target}': {owners} must be stopped first." result with its details/next_commands (moved verbatim from 1048-1055). Optional `exclude_holders` set (see c).
~30 lines.

**c) Call sites** (behaviour per site in one sentence each):

| site | change | diff |
|---|---|---|
| `_start_impl_inner` apply, 1029-1055 | replace the gate + ambiguity blocks and the `if not stop_owners:` refusal by `_start_preflight_refusal(…, "start", check_blockers=not stop_owners, render=True)`; then `blockers = self.run_blockers(…) if stop_owners else []` and the existing owner-stop branch unchanged. Same checks, same order, same texts. | −22 / +6 |
| `_start_impl_inner` plan, before the return at 989 | when `not self._order_already_healthy(order, radio)` (the apply's own shortcut at 1000, so a healthy no-op is not refused by the plan either): `_start_preflight_refusal(…, "start", check_blockers=False, render=False)`, then, if `position is None`, `meshcore_position(target)` → `None` → `f"Cannot start '{target}': {note}"`, `next_commands=[f"lhpc status {target}"]` (the 671-673 text). | +12 |
| `_restart_impl_inner` apply, after the dep-band check 2962, before `_optional_up`/stop | `_start_preflight_refusal(…, "restart", check_blockers=not stop_owners, render=True, exclude_holders=…)` with `radio = self._daemon_needs(_pre_order, band)[0] or ""` — the gate may render here: an applied restart is a mutation path, same as the start apply. | +8 |
| `_restart_impl_inner` plan, after `res.ok` at 2920 | `_start_preflight_refusal(…, "restart", check_blockers=False, render=False)` — needed because the start plan skips the preflight for a running (healthy) target, and without it fix CR1-2 would open a new restart plan/apply split. Refusal goes through the same `.replace("start","restart",1)`-free path (op already "restart"). | +4 |
| service_lifecycle_ops.py:660-662 comment | "does network I/O" is no longer true (`meshcore_position` reads saved config only, 1736-1776); reword to "can legitimately refuse the start". | ±1 |

`exclude_holders` (restart only): `{self.stack_of(target) or target}` plus, when the stop leg will cascade
(`cascade` or the target is the daemon stack, as computed at 2932-2934), `set(self.stop_dependents(target))`. Reason:
before the stop the target's own optional components and cascaded dependents are still up; a blocker they hold is
released by the stop leg itself, so refusing on it would be a new false refusal (Q2).

**Byte-identical for the start apply** (the texts and `data`/`next_commands` move unchanged; op = "start"):

| refusal | text | identical? |
|---|---|---|
| firewall pending | `Cannot start 'x': Firewall changes pending — the listener was NOT started. Apply the firewall first, then start 'x' again.` + `_fw_apply_lines()` + `data.firewall_gate="pending"` | yes, incl. render on apply |
| firewall unmodeled / partial | `Cannot start 'x': The saved listener is not covered …` / `… partially installed …` | yes |
| ambiguity | `Cannot start 'x': run/file parameter '<p>' is ambiguous …`, `lhpc config x` | yes |
| blockers, no `stop_owners` | `Cannot run 'x': <owners> must be stopped first.` + details + `lhpc stack stop <o>` | yes |
| MeshCore position (apply, 669-673) | untouched | yes (plan reuses the same wording) |
| restart (new, pre-stop) | `Cannot restart 'x': <same tail>` — the gate's own sentence still says "start 'x' again" (it is the gate's text, shared with docs/firewall.md:203); blockers keep "Cannot run 'x'" | new texts, by design |

Risk and how it is ruled out:
- *Plan now refuses an already-running no-op start* → guarded by the apply's own `_order_already_healthy` predicate.
- *Plan writes scripts* → `render=False`; test asserts the apply script file is not created by the plan.
- *Web Start confirm flow for owners breaks* → plan keeps `check_blockers=False`; existing `tests/web` owner-confirm tests stay green.
- *Restart refuses where the stop leg would have freed the resource* → `exclude_holders` (b/c).
- *Double `run_blockers` probe on apply* → avoided: apply only calls it again when `stop_owners`.
- *Firewall gate band drift between preflight and nested start* → both get the same concrete `band` (`_rband`); the gate and scopes read saved config only, never run state.

## 3. Tests (all RED on e5187f70, GREEN after)

| finding | test module::name | asserts | red today because |
|---|---|---|---|
| CR1-7 | `tests/host/test_firewall.py::test_start_plan_refuses_what_the_apply_firewall_gate_refuses` | `_expose_kiss`, state "present", status `config_ok=True, live_ok=False`: `start("kiss", apply=False)` → `ok is False`, `data["firewall_gate"] == "pending"`; and `config/files/firewall/firewall-apply.sh` does **not** exist afterwards (read-only plan) | plan returns ok at 989 |
| CR1-7 | `tests/stacks/test_stack_params.py::test_ambiguous_flat_legacy_refuses_the_plan_too` (beside `test_ambiguous_flat_legacy_fails_typed_before_any_seam`, 538) | same seed, `apply=False` → `ok is False`, "ambiguous" in summary | ambiguity is apply-only |
| CR1-7 | `tests/stacks/test_gps.py::test_meshcore_start_plan_refuses_an_unusable_position` (where the meshcore_position refusals live) | invalid `[gps]` fixed pair + MeshCore with use_gps: `start(<meshcore>, apply=False).ok is False`, `lhpc status` in next_commands | refusal at 669 is apply-only |
| CR1-7 | `tests/host/test_firewall.py::test_start_plan_of_a_running_stack_is_not_refused_by_the_gate` | kiss already healthy (FakeSystem kiss process) + pending gate → plan ok, apply returns "already healthy" (pins the guard) | — (guard test; green before and after, documents the risk) |
| CR1-2 | `tests/host/test_firewall.py::test_restart_with_a_pending_firewall_gate_leaves_the_running_stack_up` | kiss running, pending gate: `restart("kiss", apply=True)` → `ok is False`, `data["firewall_gate"] == "pending"`, `not res.results` (no stop rows, as test_run_order.py:1017 does), kiss still RUNNING in a fresh snapshot | stop at 2969 runs first |
| CR1-2 | same module::`test_restart_plan_refuses_a_pending_firewall_gate` | `restart("kiss", apply=False).ok is False` | restart plan reuses start plan, which skips a running target |
| CR1-2 | `tests/core/test_run_order.py::test_restart_refuses_run_blockers_before_its_stop` (beside 1017) | another stack holding a resource kiss needs, no `stop_owners` → `ok is False`, "must be stopped first", `not res.results`; with `stop_owners=True` the restart proceeds | blockers refusal only in nested start |

Ambiguity for restart is covered by the shared helper; one parametrize over (gate, ambiguity) in the restart test if the
fixture allows it cheaply, else the gate case alone (tests/README rule 3).

## 4. Docs / CHANGELOG

- service_lifecycle_ops.py:591-593 `start()` docstring — becomes true; no edit.
- service_lifecycle_ops.py:2866-2868 `_restart_impl` docstring — becomes true; no edit.
- docs/operations.md:201-203 ("plans the run (… firewall exposure, resource conflicts …)") — becomes true; no edit.
- docs/firewall.md:198 — append one sentence after the two quoted refusals: "A refused restart leaves the running
  stack up; the dry run (`lhpc stack start <id>` without `--yes`) shows the same refusal." (the only place the gate's
  start/restart behaviour is described).
- CHANGELOG (next patch section): "- A restart that the firewall gate, an ambiguous saved value or a running
  conflicting stack would refuse is now refused before anything is stopped (it left the stack down); the start dry
  run and the web Start plan show these refusals, and an unusable MeshCore position, instead of an ok plan."

## 5. Order and commits

1. `CR1-7: start plan refuses what the apply refuses` — (a), (b), start apply + plan call sites, comment fix, its tests.
2. `CR1-2: restart preflights the start refusals before its stop` — restart apply + plan call sites, `exclude_holders`,
   its tests, docs/firewall.md + CHANGELOG line.
CR1-2 depends on CR1-7 (the helper). Both ship in the same patch.

## 6. Live proof (Pi 5)

Needed (operator-visible, firewall path). Row: firewall installed and verified, kiss running with `kiss_host=0.0.0.0`;
change `kiss_port` in Settings (do not apply the firewall). (1) `lhpc stack start kiss` dry run after `lhpc stack stop
kiss` → "Cannot start 'kiss': Firewall changes pending …", exit ≠ 0, no change in `ls -l config/files/firewall/`
mtime. (2) Start kiss again with the old port, change it, `lhpc stack restart kiss --yes` → "Cannot restart 'kiss':
Firewall changes pending …", `lhpc status kiss` still RUNNING. (3) Apply the firewall, restart → ok.

## 7. Open questions (with recommendation)

1. Restart refusal wording keeps the gate's "then start 'x' again". **Recommend: keep** — it is the gate's sentence,
   quoted in docs/firewall.md; rewording it means a second variant to document.
2. Restart blockers check with `exclude_holders` vs. leaving blockers out of the restart preflight. **Recommend: keep
   the check with the exclusion** (it is in the finding; the exclusion is 3 lines and prevents a new false refusal).
3. Should the restart plan also take the MeshCore position decision (the apply already does, 2711)? Pre-existing,
   outside both findings. **Recommend: yes, in commit 2** — one call to the same helper line the start plan uses;
   otherwise defer to the next review.
4. Summary "Restarted 'x'. Cannot start …" (2996) for refusals the preflight does not cover (e.g. a launch failure).
   **Recommend: out of scope** — a separate S4 wording item.

## 8. Self-check

Re-read against e5187f70: 591-593, 618-619, 660-673, 936-994, 1000-1020, 1029-1055, 2638-2735, 2852-2860,
2862-3005 (service_lifecycle_ops.py); 999-1035, 1160-1167 (service_firewall.py); 746-777, 1736-1776
(service_params.py); 2196-2206 (services.py); docs/firewall.md:198-207; docs/operations.md:200-213. The brief's
"stop at 2963" is 2969 and "checks 2941-2956" are 2947-2962; the gate's `firewall_render()` calls are 1021/1029 (brief: 1018/1026) — same code.
Could not verify (not executed, read-only session): that `plan_from_config(resolve_device=True)` performs only
filesystem reads (no gpsd I/O) — read the signature, not the body; that `stop_dependents` returns stack ids comparable
to `holder_stack` (its docstring says "running stacks"); that the FakeSystem fixtures in test_firewall.py can run kiss
as RUNNING without the `_band_svc` helper of test_run_order.py (else the restart test moves there).
