# Code report: efficiency-p2 (quick wins QW4–QW7 of the efficiency plan)

Branch `cons/efficiency-p2-r1`, stacked on `cons/efficiency-p1-r1` (QW1–QW3 + their report, `5b35b6c6`) on
`origin/dev` = `d77e34f4`. Plan: `PLAN-efficiency` v4, gate 1 GREEN (round 4). Four commits, one per quick win.

**v2 for the record:** gate 1 round 6 judged v1 (`ccef1d6c..907fb2fb`) GREEN. Packet 1's v2 (`5b35b6c6`) changed the runner's deadline handling, added two tests and reworded the QW3 docs sentence; this packet is rebased on it. `git range-diff ccef1d6c..907fb2fb 5b35b6c6..a0448a3b`, verbatim: three commits identical, the fourth (QW6) differs only in the `docs/architecture.md` sentence it extends, which now builds on packet 1's v2 wording:

```text
1:  e28fcebe = 1:  b61aeb3c efficiency: the MeshCore running mode once per request
2:  efef7bbf = 2:  c5871b23 efficiency: one firewall status per page, the listener list without a snapshot
3:  42793b83 ! 3:  a2a4f37c efficiency: the network view asks NetworkManager for the active connections once
    @@ docs/architecture.md: leaves on disk and which test proves it: the [invariant ta
        snapshot, per-(stack, band) config, consumed-source SHAs, firewall status, listeners, git state
        per distinct checkout) through a thread-local request memo dropped at every request start and
        around every mutation; rechecks under operation locks use `build_snapshot(fresh=True)`. A
    --  snapshot reads the process table once for all components, so it shows one moment, not a mix. Every
    -+  snapshot reads the process table once for all components, and the network view asks
    -+  NetworkManager for the active connections once, so its Wi-Fi and wired rows come from one answer. Every
    +-  snapshot reads the process table once, so every component is judged against the same table. Every
    ++  snapshot reads the process table once, so every component is judged against the same table, and
    ++  the network view asks NetworkManager for the active connections once, so its Wi-Fi and wired rows
    ++  come from one answer. Every
        public `ControllerService` entry that writes anything is `@invalidates_snapshot`; the rest are
        listed with a reason in `snapshot_memo.SNAPSHOT_NEUTRAL`. The
        neutral writers whose writes no snapshot input observes (lock files, the native RF-log roll,
4:  907fb2fb = 4:  a0448a3b efficiency: the polled reads run no git
```

Red-before and green below are on the v2 hashes.

## Commits

| commit | subject | files | tests (layer; asserted field) | red-before |
|---|---|---|---|---|
| b61aeb3c | efficiency: the MeshCore running mode once per request | services.py, tests/stacks/test_meshcore_mode.py | stacks: `test_the_running_mode_is_read_once_per_request_and_again_after_a_render` (5 Lifecycles in one request → `meshcore_running_mode` read once; their expected MeshCore ports `[5000, 8000]`; after `write_config_files` renders `repeater`, the next Lifecycle's ports are `[8000]`) | yes, on 5b35b6c6: 1 failed, `5 == 1` reads |
| c5871b23 | efficiency: one firewall status per page, the listener list without a snapshot | service_firewall.py, adapters/web/app.py, tests/host/test_firewall.py | host: `test_a_stacks_render_builds_the_firewall_candidate_once` (`firewall_candidate` calls per `GET /stacks` == 1); `test_the_firewall_settings_view_is_the_same_with_the_status_handed_in` (equal dicts); `test_the_firewall_listener_list_needs_no_status_snapshot` (same (stack, component, address) list; `assess_stacks` not called) | yes, on b61aeb3c: 3 failed — `2 == 1` candidates; the equal-output test structurally (`TypeError`: the parameter is new); `[1] == []` assessments |
| a2a4f37c | efficiency: the network view asks NetworkManager for the active connections once | service_network.py, tests/host/test_network_controls.py, docs/architecture.md | host: `test_the_network_view_lists_the_active_connections_once` (`--active` nmcli calls == 1; `mode, active.name, active.address == ("ap", "lhpc-ap", "10.42.0.1/24")`; `ethernet == {name, device, address}`) | yes, on c5871b23: 1 failed, `2 == 1` calls |
| a0448a3b | efficiency: the polled reads run no git | services.py, status.py, probes/source.py, service_lifecycle_ops.py, tests/core/test_snapshot_memo.py, CHANGELOG.md | core: `test_a_snapshot_without_git_has_the_same_run_states[7 source states × running/stopped]` (the `run_state` of every component equal with and without git; git called only with sources and only where a checkout exists; the webui's own state as expected); `test_the_dashboard_signature_and_a_log_running_flag_run_no_git` (signature and flag equal the full snapshot's; 0 git calls; precondition: the full read runs git) | yes, on a2a4f37c: 15 failed — the 14 cases `TypeError` (`sources` is new; structural), the service test `4` git calls |

**Red-before command:** as in packet 1 (`git archive <commit>^`, the commit's test files over it, `pytest -k`).

**Green** (on a0448a3b, a detached worktree): `python -m pytest -q -p no:cacheprovider -n 12 --dist loadfile
tests/core tests/install tests/repo tests/web tests/stacks tests/host tests/cli tests/golden` → `7711 passed, 1 skipped in 123.78s`.
`ruff check lhpc testlab` → `All checks passed!`; `ruff check tests --select F,E9` → `All checks passed!`.
Lanes: none per packet (the handler runs them on dev per batch).

## HOUSE RULES KEPT (checker output, verbatim)

```text
HOUSE RULES KEPT  5b35b6c6..a0448a3b
changelog    +1 bullets (limit 2, each ≤ 3 lines; longest 2), top section 24 lines   OK
docs         1 files, +3 lines; fact "come from one answer" -> docs/architecture.md   OK
tests        7 new tests; layers tests/core=core tests/host=host tests/stacks=stacks; red-before 4/4   OK
patterns     getsource 0 assert-True 0 source-read 0 sleep 0 startswith 0 network 0   OK
markers      0 without slow   OK
attribution  authors makrohard; trailers 0; words 0   OK
modules      service_firewall.py 1533->1533 lines (budget 1533), 19->19 public (budget 19); service_lifecycle_ops.py 6315->6315 lines (budget 6315), 73->73 public (budget 73); service_network.py 1183->1183 lines (budget 1183), 9->9 public (budget 9); services.py 2959->2959 lines (budget 2959), 28->28 public (budget 28); family net +0 lines, +0 public   OK
VERDICT      PASS  (hard 0, warn 0)
```

## Simplicity guardrails

- No new module, class, framework or state. QW4 reuses the existing per-request memo; QW5 adds one optional argument;
  QW6 one class constant and one optional argument per helper; QW7 one keyword on `build_snapshot`, one on
  `StatusProber`, one on `probe_source`.
- Service family: every file keeps its line and public-method counts (services.py 2959, service_firewall.py 1533,
  service_lifecycle_ops.py 6315, service_network.py 1183: the checker's modules line); docstrings were tightened where
  a new sentence was needed, never a statement merged to save a line, except `act = self._nm_active(listed :=
  …)` in `network_view` (one assignment expression).
- Lines outside the family (`wc -l`, 5b35b6c6 → a0448a3b): status.py 488 → 490; probes/source.py 142 → 145;
  adapters/web/app.py unchanged in length.

## Deviations from the plan

- QW4: the plan (v1) proposed an extra memo drop in `write_config_files`; it is already `@invalidates_snapshot`
  (`service_params.py:2874`), which drops the memo on entry and exit, so no line was added (the test proves the
  render is seen).
- QW6: v1's config-path memo was dropped in plan v2 (it skipped the per-call revalidation); only the NetworkManager
  half is here.

## The 6-point block

1. **Contracts.**
   - `_lifecycle()` (`services.py:1909`): the running MeshCore mode comes from `_request_memo(("meshcore-running-mode",),
     self.meshcore_running_mode)`. Its writers: `write_config_files` (`service_params.py:2875`, decorated at :2874)
     and `clean` (`service_maintenance.py:2560`, decorated at :2559); the meshcore host only reads the file. A write
     by another process during a request is seen from the next request (the approved coherence change).
   - `firewall_settings_view(st=None)` (`service_firewall.py:1095`): `st` is the caller's `firewall_status()`
     (`:1101`); `_firewall_view_safe(svc, fw=None)` (`app.py:1009`) passes the page's `fw_status` (`app.py:1099`);
     `None` (the wide read failed) → the view builds its own, as before. `_fw_listener_endpoints`
     (`service_firewall.py:34`) iterates `self.stacks()`; the snapshot overlays stamp component statuses only, never
     stack objects, so the (stack, component, endpoint) list and its order are the manifest's, as before.
   - `network_view` (`service_network.py:295`) runs `_NM_ACTIVE` (`:71`) once and hands the `(rc, out)` to
     `_nm_active(listed)` (`:126`) and `_nm_ethernet(listed)` (`:143`); the other three `_nm_active()` callers
     (`:359`, `:633`, `:1103`) pass nothing and read as before. A failing nmcli now fails both rows at one moment.
   - `build_snapshot(*, fresh=False, sources=True)` (`services.py:597`): `sources=False` returns the request's cached
     snapshot when one exists, else assesses with `StatusProber(..., sources=False)` (`status.py:67`), whose
     `probe_source(..., git=False)` (`probes/source.py:93`) returns UNKNOWN after the symlink / missing / not-a-repo
     checks, and does not cache the result (`services.py:642`). Callers: `dash_signature`
     (`service_lifecycle_ops.py:5003`) and `log_running` (`:5767`); every other caller is unchanged.
2. **Invariants and their tests.**
   - One MeshCore mode read per request, and a render inside the request is seen: the QW4 test.
   - One firewall candidate per `/stacks` render; the settings view equal either way; no snapshot for the listener
     list: the three QW5 tests; the existing firewall file (204 collected tests) green.
   - One `--active` listing per view, both rows from it (new invariant, the approved coherence change): the QW6 test.
   - The polled reads' outputs equal the full snapshot's and run no git: the QW7 tests over all seven source states
     (match, dirty, differs, patched, missing, not-a-repo, binary) × running/stopped. A run state reads the source
     only as missing / not a repository (`status.py`, `_run_state_for_service`), and binary is decided before the
     probe, so equality holds by construction; the test checks it.
3. **Failure classes.**
   - Fakes with the real signature: `FakeSystem` throughout; the monkeypatched counters wrap the real methods.
   - EIO / EACCES: a failing nmcli (rc ≠ 0) still yields empty rows for both, as each did alone; a source the fs
     checks cannot see reads MISSING in both modes (the `missing` case).
   - Ctrl-C: no new loop or wait; nothing to clean up.
   - CLI / web / job / boot agree: `lhpc status` and every gate use `build_snapshot()` with `sources=True` (unchanged);
     only the two polled reads use `sources=False`; `fresh=True` rechecks are untouched.
   - Stacked conflicts: the light snapshot is never cached, so a later full read in the same request computes its own.
4. **Test rules.** Behaviour at the public seam (a web GET, `firewall_settings_view`, `network_view`,
   `dash_signature`, `log_running`, `assess_stacks`); typed fields and call counts, no sentences.
5. **Directories and ruff.** core, install, repo, web, stacks, host, cli, golden: `7711 passed, 1 skipped`; ruff as above.
6. **Adversarial self-review.** Found and fixed: (a) the planned memo drop for QW4 was redundant (the decorator) and
   was removed after a red-without-drop check showed the test still passed; (b) the first QW7 service test passed
   for the wrong reason (no checkout in its fake, so git never ran) — given a checkout and a precondition that the
   full read runs git; (c) a fake argv `/venv/bin/...` tripped the checker's slow-work heuristic — renamed `/opt/bin`;
   (d) checked that nothing else `dash_signature` calls (`restart_required_stacks`, `_component_booting`,
   `daemon_view`, `radio_mode`) builds a snapshot: marker and socket reads only.

## Numbers, measured

| figure | command | output |
|---|---|---|
| 5 MeshCore mode reads for 5 Lifecycles before | the QW4 test on 5b35b6c6 | `5 == 1` |
| 2 firewall candidates per `/stacks` before | the QW5 test on b61aeb3c | `2 == 1` |
| 2 `--active` nmcli calls per view before | the QW6 test on c5871b23 | `2 == 1` |
| 4 git calls per poll before (this fake, one checkout) | the QW7 service test on a2a4f37c | 4 extra items |
| family line counts | `tools/check_houserules.py` | the modules line |
| the Zero figures (144 mode reads, 1.83 s for two firewall statuses, 5 nmcli, 0.85 s git per poll) | reported by `fieldtest-e293/50-profile-endpoints.txt` (box window 1), not re-measured here | — |
