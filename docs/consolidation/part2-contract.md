# PART 2 — Tier 0 contract suite

`-m contract` is the readable core: one lane that states what LHPC PROMISES, tagged on EXISTING
cases (no new tests). Each case goes through the widest public seam available (CLI verb / Flask route /
typed `ActionResult`) and expresses a happy path or the refusal that defines a boundary. `-m safety`
is the invariant subset. Markers registered in `tests/conftest.py`.

- **104** cases tagged `@pytest.mark.contract` — lane runs green in ~22s (< 30s).
- **36** of those also `@pytest.mark.safety("<id>")`; every safety case is also a contract case.
- Collected-case count unchanged by the tagging (markers add no cases).

## Promise → tagged cases (seam · happy/refusal)
1. **reports reality** — `test_cli::test_list_exits_zero`, `::test_status_exits_zero_even_when_services_stopped`,
   `::test_status_unknown_stack_exits_one` (refusal), `::test_explain_shows_direct_default` (CLI);
   `test_controller::test_doctor_surfaces_git_from_the_same_source` (refusal), `::test_doctor_ok_when_only_optional_dep_missing`
   (ActionResult); `test_web::test_dashboard_ok_and_headers` (route).
2. **install from source or refuse** — `test_cli::test_bootstrap_and_install_check`, `::test_install_requires_bootstrap_first`
   (refusal), `::test_install_gate_reports_on_check_but_refuses_on_apply`; `test_install_dep_gate` cli+route
   mandatory/optional gate (3); `test_source::test_source_check_post_does_probe_and_lands_on_install`,
   `::test_source_check_requires_csrf_and_a_known_target` (route refusal).
3. **auto-install runs/aborts/recovers** — `test_web_auto_install` form + refusals + ack-recovery (5, routes);
   `test_cli` status/recover/orphan-confirm (3).
4. **start/stop; conflicting band** — `test_web::test_action_plan_then_confirm`, `::test_start_uninstalled_stack_redirects_to_app_page`;
   `test_cli::test_start_plan_is_dry_run_without_yes`, `::test_stack_restart_is_a_command`;
   `test_hardware::test_daemon_serve_bands_never_both`; `test_resource_coord::test_cross_stack_shared_radio_blocks_start`
   (band conflict refusal); `test_stack_params::test_public_restart_valid_reaches_lock_seam`.
5. **TX opt-in + tests + callsign** [safety `RF-TX-opt-in`] — `test_auto_install` tx requires-tests / no-callsign /
   fail-before-mutate / happy (4, ActionResult); `test_web_auto_install` 2-stage confirm + without-tests-refused +
   confirmed-tx-refused-without-callsign (3, route); `test_daemon_readiness::test_tx_test_refuses_when_radio_not_ready`.
6. **binary channel switch** — `test_web` confirm-page default-channel + refused-binary-offers-source (2, route);
   `test_binary_install::test_install_binary_channel_dispatches`, `::test_install_binary_channel_refuses_all_stacks`
   (ActionResult, FakeSystem); `test_cli::test_install_plan_is_dry_run`.
7. **config + daemon params live** — `test_daemon_params_web` apply-live saves/disabled/rejected/total-failure/fsk-warn/csrf
   (6, route+ActionResult); `test_config::test_save_operator_writes_callsign_locally`.
8. **radio rig** — `test_hardware` probe-inline / block-refuses-when-unset / rejects-invalid / clears-once-configured /
   probe-absent-diagnostic / probe-busy (6).
9. **firewall fail-closed** [safety `firewall-fail-closed`] — `test_web` settings-render + configure-GET-redirect (route);
   `test_firewall` gate allows-verified / refuses-unverified / partial-fails-closed / apply-happy / apply-refuses-not-owned /
   webserver-apply-blocked-by-pending (6).
10. **exposure opt-in; loopback fail-safe** [safety `exposure-fail-closed`] — `test_webserver` configure-post /
    without-cidr-refused / requires-confirmation / requires-csrf / loopback-no-confirm / remote-no-auth-elevated /
    p12-loopback-only (7, route+ActionResult).
11. **HMAC transactional** — `test_hmac` apply-page / apply-post-csrf / disable-requires-phrase / disable-correct-phrase /
    rejects-bad-action / cli-status-gate / cli-apply-no-secret-leak / unsafe-recovery (8, route+CLI+ActionResult).
12. **self-update checks/applies/survives failure** — `test_web` check-csrf / one-click-confirm / dirty-consent (route);
    `test_cli` apply-yes / aborts-without-yes / busy (CLI); `test_selfupdate` diverged-refused / cleanup-truthful-partial.
13. **boot restore** — `test_boot_restore` web-toggle / toggle-csrf / cli-autostart / cli-unsafe-journal-nonzero (4).
    NOTE: these carry the module's `needs_session` marker (real process), so they run under a real session and
    SKIP (never fail) in a sid==0 sandbox — the lane stays green either way. See gaps.
14. **uninstall/clean refuse while running; config preserved** [safety `P0.5`] — `test_uninstall_safety` refuses-running /
    removes-unshared / keeps-shared / refuses-identity-drift; `test_clean` refuses-running / requires-purge /
    removes-exact-preserves-rest (7, ActionResult).
15. **GET never mutates / never networks** [safety `P0.6`] — `test_web` get-routes-no-network / page-load-read-only /
    system-api-read-only / get-daemon-config-read-only / socket-stream-read-only-bounded / stranded-GET-redirects (6, route).

## CONTRACT-GAPS (the most valuable output — promises whose widest-seam case is missing or weak)
1. **Firewall APPLY route (P9)** — no test drives a real `POST /firewall/configure` (or `/refresh`) through Flask and
   observes an applied effect. Only `GET /firewall/configure` (redirect) and settings-render are route-level; the real
   apply/fail-closed transaction is proven only at the module/`ActionResult` seam (`test_firewall.py`). Missing: a
   genuine route happy+refusal for firewall apply.
2. **Boot-restore has no isolation-safe case (P13)** — the ENTIRE `test_boot_restore.py` is `pytest.mark.needs_session`
   at module scope, including the plain `/boot-restore` CSRF/toggle route tests that don't themselves spawn a process.
   The lane therefore only has session-dependent cases for promise 13. A future split (move the pure route-toggle
   tests out from under the module marker) would give promise 13 a truly sandbox-safe contract case.
3. **Binary-channel SWITCH at the route seam (P6)** — no Flask-route test completes an actual binary→source (or reverse)
   switch; only confirm-page channel-selection is route-tested. The switch itself is proven only via
   `ControllerService.install()` (and those `RealSystem`-backed switch tests are NOT tagged — they read the global
   `/tmp/loraconf*.sock` and so are not hermetic against a running daemon; see the environmental note in part1-folds.md).
4. **Uninstall/clean at the route seam (P14)** — no `/action` POST exercises `op=uninstall`/`op=clean` and asserts the
   refuse-while-running boundary at the web seam; proven only at the `ControllerService` level.
5. **Hardware SETUP route (P8)** — only `/hardware/probe` is route-tested; the `/hardware` setup POST itself has no
   direct client test (setup correctness is proven through `config.py` unit tests).
6. **"all 54 routes" is not enumerated (P15)** — `test_get_routes_make_no_network_calls` walks 11 representative GET
   paths, not the full route table. If "all 54 routes" is meant literally, no test walks every route.
7. **No single composite TX-gate test (P5)** — "opt-in + passing tests + callsign" is covered by several separate
   refusal tests read together; there is no one canonical named invariant test that asserts the whole gate at once.

## SAFETY invariant IDs
`docs/hardening-0.1.md` has NO enumerated P0.x/P1.x registry — the ids exist only as inline docstring/comment labels.
Two are stated verbatim in test docstrings and are used as-is; the other three safety promises have no matching id in
that document, so a descriptive slug is used and the gap is recorded here.

| promise | `safety(id)` | basis |
|---|---|---|
| 14 uninstall/clean | `P0.5` | verbatim in `test_uninstall_safety.py` docstring + hardening-0.1 "Uninstall protection" |
| 15 GET no-network | `P0.6` | verbatim (`test_web.py:150`, `service_maintenance.py`, hardening-0.1 "GET no-network guarantee") |
| 5 TX opt-in | `RF-TX-opt-in` | no P-id exists (closest is P0.4 = TXMODE apply/readback, a different concern) — slug used |
| 9 firewall | `firewall-fail-closed` | firewall isn't in hardening-0.1 at all (its own `FW-*` numbering) — slug used |
| 10 exposure | `exposure-fail-closed` | no exposure-specific P-id (P0.7 = generic plan+confirm) — slug used |
