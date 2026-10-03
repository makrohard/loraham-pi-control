# PLAN G3 v2 — restart/start refusals decided before anything is stopped or promised

Base: `origin/main` = `e5187f70` (v0.11.10); line numbers are this SHA's. Findings: **CR1-2** (S2), **CR1-7** (S3).
v2 of `code-review/PLAN-G3.md`, answering every finding of `code-review/VERDICT-PLAN-G3.md` (RED). Kept unchanged
(review: OK): `render=False` planning, the start APPLY refactor, the start PLAN firewall/ambiguity with
`check_blockers=False`, the restart APPLY preflight, the CR1-7 tests, docs scope and the 2-commit split.
`slo` = `lhpc/core/service_lifecycle_ops.py`, `sfw` = `lhpc/core/service_firewall.py`.

## 1. Analysis (v1 §1 stands; corrections in bold)

- CR1-2: public `restart()` (slo:2637) → identity 2694, saved-launch 2701, MeshCore position 2711 (unconditional)
  → `_restart_impl_inner` (2886): identity 2947, saved-launch 2949, dep-band 2957-2962 → **stop 2969** → nested
  `self.start(apply=True)` 2979 → firewall 1029, ambiguity 1038, blockers 1045-1055 — refused after the stop.
- CR1-7: `start(apply=False)` returns at 618-619 into the inner plan branch 936-994 (ok at 989); the firewall gate
  (1029), ambiguity (1038) and the outer identity+position block (650-673) run on apply only.
- **Apply order today** (the order v2 gives the plan): outer identity 650-660 → position 669-673 (*guarded by
  `position is None and _order and not _order_already_healthy(_order, _radio)`* — so a healthy MeshCore apply takes
  no position either; the review's "position before the healthy shortcut" is ordering only) → hook → inner gps,
  radio-mode, dep-band, identity, saved-launch → healthy shortcut 1000 → firewall → ambiguity → blockers.
- **Position is read-only**: `meshcore_position` (service_params.py:1736-1776) reads saved config and
  `plan_from_config` (gps.py:251, "reads the filesystem only to resolve a device's identity") — no gpsd I/O, so
  the plan may call it (closes v1's open verification; `test_a_dry_run_start_does_no_gps_io`, test_meshcore.py:511,
  stays green). The comment at 661-668 ("does network I/O") is reworded.
- **Identity domains**: `run_blockers` holder ids are `holder_stack = ss.stack.id` (slo:396, 393); `stop_dependents`
  returns `ss.stack.id` (slo:2280); the stop leg cascades exactly `stop_dependents(_sid or target, bands=…)`
  (slo:2426), exposed by the stop plan as `data["dependents"]` (slo:2457). Same domain: stack ids.
- **Who holds what before a restart's stop**: `run_blockers` skips only the run order (`c.id in order_ids`, 399),
  so a running *optional* component of the target's own stack (not in its run order, cf.
  `_running_optional_components` 3008) can appear with `holder_stack == target`; a cascaded dependent can appear
  with its own stack id. Both are down after the stop leg → must not refuse the restart before it.

## 2. The change

**a) `firewall_gate_stack_start(self, target, band="", *, render=True, op="start")`** (sfw:999).
`render=False` skips the two `self.firewall_render()` calls (sfw:1021, 1029). `op` only picks the sentence tail; the
decision and `next_commands` are unchanged; `op="start"` texts are byte-identical to today. For `op="restart"`:

| case | `op="restart"` message (reason kept, start tail replaced) |
|---|---|
| partial (1011-1015) | `Firewall integration is partially installed — restart was not performed; the running stack was left up. Repair it first, then restart '<t>'.` |
| pending (1019-1025) | `Firewall changes pending — restart was not performed; the running stack was left up. Apply the firewall first, then restart '<t>'.` |
| not modeled (1027-1034) | `The saved listener is not covered by the applied firewall — restart was not performed; the running stack was left up. Apply the firewall first, then restart '<t>'.` |

Implementation: one local `left = "restart was not performed; the running stack was left up"` and per case
`reason + (start_tail if op == "start" else f" — {left}. {remedy}")`. ~10 lines.

**b) `_start_preflight_refusal(self, target, order, band, radio, op, *, check_blockers, render,
exclude_holders=frozenset())`** in slo beside `_identity_refusal` (2852), documented "the one firewall/ambiguity/
blocker wording the start plan, the start apply and the restart (plan and apply) share". Returns ActionResult|None:
1. gate: `ok, msg, cmds = self.firewall_gate_stack_start(target, band=band, render=render, op=op)`; refusal
   `ActionResult(False, f"Cannot {op} '{target}': {msg}", next_commands=cmds, data={"firewall_gate": "pending"})`
   (moved from 1029-1032);
2. `_config_ambiguity(target, order, band)` → `f"Cannot {op} '{target}': {amb}"`, `[f"lhpc config {target}"]`
   (moved from 1038-1041);
3. only if `check_blockers`: **the filter lives here and only here** —
   `blockers = [bl for bl in self.run_blockers(target, band, radio) if bl["holder_stack"] not in exclude_holders]`;
   non-empty → the existing `Cannot run '{target}': {owners} must be stopped first.` + details + `lhpc stack stop <o>`
   (moved verbatim from 1046-1055). Default `frozenset()` = today's start behaviour (no exclusion).

**c) One outer decision for both start modes** (review finding 2). New
`_start_outer_refusal(self, target, order, band, radio, position, note) -> (ActionResult|None, position, note)` =
the apply block 650-673 moved verbatim: `if order:` identity via `_identity_refusal(target, band, "start")` (same
text/data/next_commands as 653-660), then `if position is None and not self._order_already_healthy(order, radio):`
`_position_refusal(target, "start")`. New `_position_refusal(self, target, op) -> (refusal|None, position, note)`
wraps `meshcore_position` with the one text `f"Cannot {op} '{target}': {note}"`, `[f"lhpc status {target}"]`
(today duplicated at 670-673 and 2713-2715). Order/radio come from one helper `_order_radio(target, op_band)`
(the 4 lines 634/639-641).

| site | change | diff |
|---|---|---|
| `start()` apply 650-673 | replace by `_r, position, _pos_note = self._start_outer_refusal(_op_band-based args)`; return `_r` if set | −22/+4 |
| `start()` plan 618-619 | before `_start_impl(apply=False…)`: `_ob = self.operation_band(target, band)`, `_order, _radio = self._order_radio(target, _ob)`, same `_start_outer_refusal` call → **same point (after band_refusal, before `_start_impl`), same order** as apply | +4 |
| `_start_impl_inner` plan, before 989 | `if not self._order_already_healthy(order, radio):` `_start_preflight_refusal(…, "start", check_blockers=False, render=False)` (position no longer here) | +4 |
| `_start_impl_inner` apply 1029-1055 | `_start_preflight_refusal(…, "start", check_blockers=not stop_owners, render=True)`; `blockers = self.run_blockers(…) if stop_owners else []`; owner-stop branch unchanged | −22/+5 |
| public `restart()` plan 2663-2665 | before `_restart_impl(apply=False…)`, mirror apply 2694/2701/2711 in order: `_identity_refusal(t, _pb, "restart")`, `_saved_launch_refusal(t, _pb, "restart")`, `_position_refusal(t, "restart")` with `_pb = self.operation_band(target, band)` | +6 |
| public `restart()` apply 2711-2715 | `_position_refusal(target, "restart")` (same text) | −4/+3 |
| `_restart_impl_inner` 2909-2962 | **move** the identity/saved-launch/dep-band block 2945-2962 above `if not apply:` (2909) so plan and apply run it at the same point, then `_start_preflight_refusal(target, _pre_order, band, radio, "restart", check_blockers=apply and not stop_owners, render=apply, exclude_holders=_ex)` with `radio = (self._daemon_needs(_pre_order, band)[0] or "") if _pre_order else ""` | +8/−0 (moved) |
| restart cascade predicate | extract `_restart_stops_dependents(target, cascade)` from 2932-2934 (`cascade or daemon stack`), used by the plan (2934) and the exclusion | ±3 |
| comment 661-668 | "does network I/O" → "can legitimately refuse the start (reads saved config only)" | ±1 |

**`exclude_holders` for restart apply (`_ex`)**, computed only when `check_blockers` is true:
`({target} if self.stack(target) else set())` — a whole-stack restart stops its own optional components; a
component restart stops only that component, so its siblings are *not* excluded — **plus**, when
`_restart_stops_dependents(target, cascade)`, `set(self._stop_impl(target, apply=False, cascade=cascade,
band=band).data.get("dependents") or [])` — the exact list the stop leg cascades (2426), with its band scoping. If
that stop plan fails (per-band uncertainty, 2430-2440) it has no `dependents` → no exclusion; the stop leg refuses the
same way, so nothing new is lost. Not excluded: non-cascaded dependents and anything else — they stay up and the
nested start would refuse them after the stop, which is CR1-2 itself.

Restart-plan parity result: plan and apply now run public identity → saved-launch → position, then inner
gui/mode → identity → saved-launch → dep-band → firewall → ambiguity (→ blockers on apply only, as for start).
The restart plan's later `_start_impl(apply=False)` + `.replace("start","restart",1)` (2919-2923) stays for the
refusals it alone covers (gps, radio mode). Byte-identical for **start** (plan and apply share the apply's texts; only
the plan now refuses where the apply already did). New texts: the restart firewall messages above; ambiguity and
position as `Cannot restart '<t>': …`; blockers keep `Cannot run '<t>'`.

Risks: healthy no-op start refused by the plan → both firewall/ambiguity (inner) and position (outer) sit behind
the apply's own `_order_already_healthy` predicate; plan writes scripts → `render=False` (asserted); web
owner-confirm flow → plan `check_blockers=False`; restart false refusal → `exclude_holders` + mandatory test; double
`run_blockers` probe → apply calls it a second time only with `stop_owners`; band drift → preflight and nested start
get the same concrete `band` (`_rband`).

## 3. Tests

**Red on e5187f70, green after** (regression proof):

| finding | module::test | asserts |
|---|---|---|
| CR1-7 | `tests/host/test_firewall.py::test_start_plan_refuses_what_the_apply_firewall_gate_refuses` | `_expose_kiss` (1340), state present, status `config_ok=True, live_ok=False`: plan `ok is False`, `data["firewall_gate"]=="pending"`; `config/files/firewall/firewall-apply.sh` not created by the plan |
| CR1-7 | `tests/stacks/test_stack_params.py::test_ambiguous_flat_legacy_refuses_the_plan_too` (beside 538) | same seed, `apply=False` → `ok is False`, "ambiguous" in summary |
| CR1-7 | `tests/stacks/test_meshcore.py::test_start_plan_takes_the_position_decision_like_the_apply` (position section, ~356; v1 named test_gps.py wrongly) | `meshcore_position` patched to `(None, "the global position source is not usable: x")` **and** the firewall gate patched to refuse: `start("meshcore", apply=False)` and `apply=True` both `ok is False` with the **same summary** (the position refusal — same point, same order), `lhpc status meshcore` in next_commands |
| CR1-2 | `tests/host/test_firewall.py::test_restart_with_a_pending_firewall_gate_leaves_the_running_stack_up` | kiss running, gate pending: `restart("kiss", apply=True)` → `ok is False`, `data["firewall_gate"]=="pending"`, summary contains `restart was not performed; the running stack was left up` and `Firewall changes pending`, not `NOT started`; `not res.results`; kiss RUNNING in a fresh snapshot |
| CR1-2 | same module::`test_restart_plan_refuses_a_pending_firewall_gate` | `restart("kiss", apply=False)` → `ok is False`, same summary as the apply's; plan creates no script |
| CR1-2 | `tests/core/test_run_order.py::test_restart_refuses_run_blockers_before_its_stop` (beside 1017) | external holder (stack `meshtastic`, `svc.stack_of(holder)=="meshtastic"` asserted), no `stop_owners` → `ok is False`, "must be stopped first", `not res.results`; `stop_owners=True` → proceeds |
| CR1-2 | `tests/core/test_run_order.py::test_restart_preflight_ignores_holders_its_own_stop_releases` (**mandatory**, review finding 3/6) | fixture `_kiss_with_graywolf_running` (1042) + `_spy_life_stops` (1060); `run_blockers` patched to return, while the spy list is empty, `[{holder: <a kiss component id>, holder_stack: "kiss"}, {holder: "graywolf", holder_stack: "graywolf"}]`, `[]` after any stop. **Identities asserted**: `svc.stack_of(<comp>)=="kiss"`, `svc.stack_of("graywolf")=="graywolf"`, `svc._stop_impl("kiss", apply=False, cascade=True).data["dependents"]==["graywolf"]`. (i) `cascade=True` → no "must be stopped first", stop leg ran (`"graywolf" in stopped`); (ii) `cascade=False` → refused before the stop naming `graywolf` only (own-stack holder excluded), `not res.results`. Red today via (ii); (i) is the false-positive guard that fails if the exclusion is missing or uses the wrong identity |

**Characterization (green before and after; not counted as defect proof)**:
`tests/host/test_firewall.py::test_start_plan_of_a_running_stack_is_not_refused_by_the_gate` — kiss healthy + gate
pending → plan ok, apply "already healthy" (pins the healthy guard); existing `test_a_dry_run_start_does_no_gps_io`
(test_meshcore.py:511), the sfw gate tests 1353-1412/2774-2794 (default `op`/`render` unchanged), the restart
cascade tests 1070-1101 and `tests/web` owner-confirm tests stay green unchanged.

## 4. Docs / CHANGELOG

- `start()` docstring 591-593, `_restart_impl` docstring 2866-2868, docs/operations.md:201-203 — become true; no edit.
- docs/firewall.md after the two quoted refusals (~205): "A restart refused by the gate says *restart was not
  performed; the running stack was left up* with the same reason; the dry run (`lhpc stack start <id>` / `lhpc stack
  restart <id>` without `--yes`) shows the same refusal."
- CHANGELOG (next patch): "- A restart that the firewall gate, an ambiguous saved value or a running conflicting stack
  would refuse is now refused before anything is stopped, and says the running stack was left up (it left the stack
  down); the start and restart dry runs and the web Start/Restart plans show these refusals, and an unusable MeshCore
  position, instead of an ok plan."

## 5. Commits

1. `CR1-7: start plan takes the apply's refusals` — gate `render`/`op` kwargs, `_start_preflight_refusal`,
   `_position_refusal`, `_start_outer_refusal`, `_order_radio`, start plan/apply call sites, comment, CR1-7 tests +
   characterization test.
2. `CR1-2: restart preflights the start refusals before its stop` — restart plan/apply call sites, block move,
   `_restart_stops_dependents`, `exclude_holders`, CR1-2 tests (incl. the mandatory one), docs + CHANGELOG.

## 6. Live proof (Pi 5)

As v1 §6, with (2) expecting "Cannot restart 'kiss': Firewall changes pending — restart was not performed; the
running stack was left up. …" and `lhpc status kiss` still RUNNING; add (2b) `lhpc stack restart kiss` (dry run)
→ the same text, `config/files/firewall/` mtimes unchanged.

## 7. Open questions — resolved

Q1 wording: restart-specific tail (a). Q2: blockers kept with the specified exclusion + mandatory test. Q3: restart
plan takes position at the apply's point (public restart, after saved-launch). Q4: "Restarted 'x'. Cannot start …"
(2996) out of scope (S4).

## 8. Self-check

Re-read at e5187f70: slo 343-440, 586-680, 744-760, 936-1056, 2253-2280, 2384-2457, 2636-2735, 2744, 2852-2860,
2862-3005, 3008-3026; sfw 999-1035; service_params.py 1736-1776; gps.py 251-263; tests test_run_order.py 952-1101,
test_meshcore.py 345-516, test_firewall.py 1340-1412, docs/firewall.md 195-207. Not executed (read-only round): the
tests; whether kiss has an optional component with an exclusive resource (the mandatory test patches
`run_blockers`, so it does not depend on one; the real-domain proof is the asserted `stack_of`/stop-plan identities).
