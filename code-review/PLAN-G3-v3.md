# PLAN G3 v3 — restart/start refusals decided before anything is stopped or promised

Base: `origin/main` = `e5187f70` (v0.11.10); line numbers are this SHA's. Findings: **CR1-2** (S2), **CR1-7** (S3).
v3 of `code-review/PLAN-G3-v2.md`, answering `code-review/VERDICT-PLAN-G3-v2.md` (RED: the six v1 findings OK; new
findings **N1** parity claim / GPS + radio-mode before the stop, **N2** band vs `_rband`, **N3** CHANGELOG typo).
Everything not marked **v3** is v2 unchanged. `slo` = `lhpc/core/service_lifecycle_ops.py`,
`sfw` = `lhpc/core/service_firewall.py`, `sp` = `lhpc/core/service_params.py`.

## 1. Analysis (v2 §1 stands; v3 additions in bold)

- CR1-2: public `restart()` (slo:2638) → identity 2694, saved-launch 2701, MeshCore position 2711 →
  `_restart_impl_inner` (2886): identity 2947, saved-launch 2949, dep-band 2957-2962 → **stop 2969** → nested
  `self.start(apply=True)` 2979 → firewall 1029, ambiguity 1038, blockers 1045-1055 — refused after the stop.
- CR1-7: `start(apply=False)` returns at 618-619 into the inner plan branch 936-994; firewall, ambiguity and the
  outer identity+position block (650-673) run on apply only.
- Apply order today: outer identity 650-658 → position 667-673 (guarded by `position is None and _order and not
  _order_already_healthy(_order, _radio)`) → hook → inner gui 844, mode 846, runtime root 853, hardware 858, gps 866,
  radio mode 872, own-band 895, dep-band 910, identity 923, saved-launch 934 → healthy 1000 → firewall → ambiguity →
  blockers.
- Position is read-only (`meshcore_position`, sp:1736-1776; `plan_from_config`, gps.py:251) — unchanged from v2.
- Identity domains / `exclude_holders` — unchanged from v2 (slo:393-399, 2280, 2426, 2457).
- **v3 (N1) — what restart APPLY does NOT check before its stop today.** The restart PLAN gets every inner start
  refusal via `_start_impl(apply=False)` (2919). The APPLY reaches only gui/mode (2656-2658, 2897-2900), band_refusal
  2660, identity, saved-launch, position, dep-band before 2969. **Hardware 858, GPS 866 and radio mode 872 are
  checked only by the nested start after the stop** (also runtime root 853, which a running stack cannot fail).
  All three ignore whether the target is running: `hardware_block` (527-541) reads `hardware_configured()` and the
  run order; `radio_mode_block` (567-582) reads the order and `active_bands()`; `gps_block` (448-525) reads `[gps]`
  config and asks the *system* gpsd on 127.0.0.1 (`gpsd_owns_device`, gps.py:355-389). No stack manifest has a gpsd
  component, so the stop leg cannot change the answer. Preflighting them before the stop is therefore exact.
- **v3 (N1) — the one start refusal the stop legitimately releases**: own-band 895-904 ("already running on X —
  refusing to start it on Y") tests `_band_owner_is_up(_sid)`, which the stop leg makes false. The restart plan
  refuses it (via 2919) and the apply does not (stop, then start on Y). That gap already exists on e5187f70, sits
  outside CR1-2/CR1-7 and is **not** changed here (see §2 parity statement).
- **v3 (N2) — the band in `_restart_impl_inner`.** The apply enters with `band=_rband` (2689 → 2723 → 2874); 2901-2908
  re-resolves only an empty band. The plan enters with the *raw* band (2664 → 2870), so an explicit band stays
  raw there. `operation_band` (sp:2709-2730) is idempotent: a non-empty result `cb` is a member of
  `stack_bands(target)` (`_config_band`, slo:4485-4499), so the second call takes it as the explicit hint (slo:804)
  and returns it unchanged; an empty `cb` means `stack_bands` is empty, so the second call returns the daemon's hint
  unchanged or `""` again. Hence on apply `self.operation_band(target, band) == band == _rband`, and on plan it is
  exactly the value the apply's 2689 would compute.

## 2. The change

**a) `firewall_gate_stack_start(..., *, render=True, op="start")`** (sfw:999) — unchanged from v2 (texts table:
partial / pending / not modeled, each `… — restart was not performed; the running stack was left up. …`).

**b) `_start_preflight_refusal(self, target, order, band, radio, op, *, check_blockers, render,
exclude_holders=frozenset())`** — unchanged from v2 (gate → ambiguity → filtered blockers only if `check_blockers`).

**c) `_start_outer_refusal` / `_position_refusal` / `_order_radio`** — unchanged from v2.

**d) v3 (N1): `_start_static_refusal(self, target, op) -> ActionResult | None`** in slo beside `hardware_block`:
lines 853-875 moved verbatim — runtime root (`Runtime root not bootstrapped.`, `lhpc bootstrap`), then
`f"Cannot {op} '{target}': {hw_block}"` / `["lhpc hardware"]`, then gps (`gps_next or ["lhpc gps"]`), then radio
mode (`["lhpc hardware"]`). `_start_impl_inner` calls it with `op="start"` at the same place → start texts
byte-identical. With `op="restart"` the text equals what the restart plan returns today via
`res.summary.replace("start", "restart", 1)` (2921), so restart plan texts do not change either.

| site | change | diff |
|---|---|---|
| `start()` apply 650-673 | `_start_outer_refusal(_op_band-based args)` (v2) | −22/+4 |
| `start()` plan 618-619 | `_ob = self.operation_band(target, band)`, `_order_radio`, `_start_outer_refusal` (v2) | +4 |
| `_start_impl_inner` 853-875 | **v3**: `if (_r := self._start_static_refusal(target, "start")) is not None: return _r` | −20/+2 |
| `_start_impl_inner` plan, before 989 | `_start_preflight_refusal(…, "start", check_blockers=False, render=False)` behind `not _order_already_healthy` (v2) | +4 |
| `_start_impl_inner` apply 1029-1055 | `_start_preflight_refusal(…, check_blockers=not stop_owners, render=True)` (v2) | −22/+5 |
| public `restart()` plan 2663-2665 | **v3 (N2) name**: `_rband = self.operation_band(target, band)` (the 2689 expression), then identity, saved-launch, `_position_refusal(t, "restart")` on `_rband` (v2's `_pb`, renamed) | +6 |
| public `restart()` apply 2711-2715 | `_position_refusal(target, "restart")` (v2) | −4/+3 |
| `_restart_impl_inner` 2909-2962 | **v3**: directly above `if not apply:` (2909): `_rband = self.operation_band(target, band)` (== `band` on apply, §1 N2); then **`_start_static_refusal(target, "restart")`**; then the moved identity/saved-launch/dep-band block 2945-2962, its `band` replaced by `_rband` (dep-band: `_rband or self._config_band(target, _rband)`); then `radio = (self._daemon_needs(_pre_order, _rband)[0] or "") if _pre_order else ""` and `_start_preflight_refusal(target, _pre_order, _rband, radio, "restart", check_blockers=apply and not stop_owners, render=apply, exclude_holders=_ex)` | +12/−0 (moved) |
| restart cascade predicate | `_restart_stops_dependents(target, cascade)` from 2932-2934 (v2) | ±3 |
| comment 661-668 | "does network I/O" → "can legitimately refuse the start (reads saved config only)" (v2) | ±1 |

The stop leg (2969), nested start (2979) and plan legs (2919, 2924) keep `band`; on apply that is `_rband` (§1 N2),
so the preflight, `_daemon_needs`, the exclusion stop plan and the stop leg all judge one band. The plan legs keep
their raw-band behaviour (out of scope); only the new preflight reads `_rband` on the plan.

**`exclude_holders` (`_ex`)** — v2 unchanged except the band: `({target} if self.stack(target) else set())` plus,
when `_restart_stops_dependents(target, cascade)`, `set(self._stop_impl(target, apply=False, cascade=cascade,
band=_rband).data.get("dependents") or [])` (signature slo:2384) — the same band the stop leg receives.

**v3 (N1) parity statement** (replaces v2's "exact"): PLAN and APPLY of each operation take **the same refusals in
the same order, except the deliberate blocker policy** — start PLAN `check_blockers=False`, start APPLY
`not stop_owners`, restart `apply and not stop_owners` (owner-confirm flow) — **and** the pre-existing own-band
refusal 895 (restart plan only; released by the stop; unchanged, out of scope). Order on both restart modes:
public gui → mode → band_refusal → identity → saved-launch → position; inner gui → mode → **runtime root →
hardware → GPS → radio mode** → identity → saved-launch → dep-band → firewall → ambiguity (→ blockers). On the
restart apply all of this runs **before** the stop at 2969 — GPS and radio mode via `_start_static_refusal`, no
longer only in the nested start. The plan's later `_start_impl(apply=False)` (2919) stays as the backstop and
the source of the plan details; after v3 the only refusal it can add is the own-band one above.

New texts: the restart firewall messages; ambiguity and position as `Cannot restart '<t>': …`; blockers keep
`Cannot run '<t>'`. Start texts byte-identical.

Risks (v2 list, plus): GPS query twice on a direct-NMEA restart (preflight + nested start, ≤3 s timeout each,
gps.py:356) — same as the plan already does today; accepted. Healthy no-op start, `render=False`, owner-confirm,
false-refusal exclusion, double `run_blockers` probe — as v2. Band drift → closed by `_rband` above.

## 3. Tests

**Red on e5187f70, green after** (v2 rows unchanged; **v3** row added):

| finding | module::test | asserts |
|---|---|---|
| CR1-7 | `tests/host/test_firewall.py::test_start_plan_refuses_what_the_apply_firewall_gate_refuses` | as v2 |
| CR1-7 | `tests/stacks/test_stack_params.py::test_ambiguous_flat_legacy_refuses_the_plan_too` (beside 538) | as v2 |
| CR1-7 | `tests/stacks/test_meshcore.py::test_start_plan_takes_the_position_decision_like_the_apply` (~356) | as v2 |
| CR1-2 | `tests/host/test_firewall.py::test_restart_with_a_pending_firewall_gate_leaves_the_running_stack_up` | as v2 |
| CR1-2 | same module::`test_restart_plan_refuses_a_pending_firewall_gate` | as v2 |
| CR1-2 | `tests/core/test_run_order.py::test_restart_refuses_run_blockers_before_its_stop` (beside 1017) | as v2 |
| CR1-2 | `tests/core/test_run_order.py::test_restart_preflight_ignores_holders_its_own_stop_releases` (**mandatory**; fixtures 1042/1060) | as v2; additionally `_stop_impl` is spied to assert the exclusion stop plan received `band == ` the stop leg's band |
| CR1-2 **v3** | `tests/core/test_run_order.py::test_restart_refuses_gps_and_radio_mode_before_its_stop` (parametrized: `gps_block` → `("the global position source is invalid (x) …", ["lhpc gps"])`; `radio_mode_block` → `"requires the 433 MHz radio — …"`) | kiss running (fixture 1042), `_spy_life_stops` (1060): `restart("kiss", apply=True)` → `ok is False`, summary starts `Cannot restart 'kiss':` (not `Restarted`), spy list empty, `not res.results`; `restart("kiss", apply=False)` → same summary (plan/apply parity). Red today: the apply stops first and returns `Restarted 'kiss'. Cannot start …` (2996) |
| N2 **v3** | same module::`test_restart_plan_and_apply_preflight_one_band` | spy `_start_preflight_refusal`; explicit `band` on a band-switchable stack and `band=""`: the plan's and the apply's recorded band are equal and equal `svc.operation_band(target, band)`. New helper → green-after only; not counted as defect proof |

**Characterization (green before and after)**: as v2 (`test_start_plan_of_a_running_stack_is_not_refused_by_the_gate`,
`test_a_dry_run_start_does_no_gps_io` test_meshcore.py:511, sfw gate tests, restart cascade tests 1070-1101,
`tests/web` owner-confirm tests).

## 4. Docs / CHANGELOG

- Docstrings 591-593, 2865-2868, docs/operations.md:201-203 — become true; no edit.
- docs/firewall.md (~205): as v2.
- CHANGELOG (next patch) — **v3 (N3) wording**: "- A restart that the firewall gate, an ambiguous saved value, a
  running conflicting stack, the GPS check or the radio mode would refuse is now refused before anything is stopped
  and says the running stack was left up; it previously left the stack down. The start and restart dry runs and the
  web Start/Restart plans show these refusals, and an unusable MeshCore position, instead of an ok plan."

## 5. Commits

1. `CR1-7: start plan takes the apply's refusals` — gate kwargs, `_start_preflight_refusal`, `_position_refusal`,
   `_start_outer_refusal`, `_order_radio`, **`_start_static_refusal`** (start call site only), start call sites,
   comment, CR1-7 tests + characterization.
2. `CR1-2: restart preflights the start refusals before its stop` — restart call sites with `_rband`, block move,
   static refusal before the stop, `_restart_stops_dependents`, `exclude_holders`, CR1-2 tests (incl. mandatory and
   the v3 rows), docs + CHANGELOG.

## 6. Live proof (Pi 5)

As v2 (firewall pending → "Cannot restart 'kiss': Firewall changes pending — restart was not performed; …", kiss
RUNNING; 2b dry run same text, firewall mtimes unchanged). **v3**: (2c) on a 433+868 box set radio mode to
868-only while kiss (433) runs → `lhpc stack restart kiss --yes` → "Cannot restart 'kiss': requires the 433 MHz
radio — …", `lhpc status kiss` still RUNNING; restore the mode.

## 7. Open questions — resolved

Q1-Q4 as v2. Q5 (v3): the own-band plan/apply gap (895) is pre-existing and reverse-direction (plan stricter than
apply); left for a separate finding rather than widening G3.

## 8. Self-check

Re-read at e5187f70 for v3: slo 448-582 (gps/hardware/radio-mode blocks), 600-680, 771-811, 837-1000, 2384,
2636-2735, 2862-3005, 4485-4499; sp 2709-2730; gps.py 355-389; test_run_order.py 1017-1101 (fixture names/lines);
CHANGELOG.md head. `grep` found no gpsd stack component (only gps-source config/handling in sp, services.py,
gps_bridge.py). Not executed (read-only round): the tests; the live proof.
