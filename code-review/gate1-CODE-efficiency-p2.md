# Gate 1: efficiency-p2 (quick wins QW4–QW7: per-request reuse, one firewall status, one NetworkManager answer, polls without git), v2

**Request.** Judge the code commits below against the plan's QW4–QW7 (quoted). That means: correctness; whether each
red-before test can fail for the defect named; whether behaviour changes only where the plan says (QW4's external-write case and QW6 are approved coherence changes; everything else must be output-identical); the
docs and CHANGELOG lines; and the report's claims against the diff. The plan is GREEN (gate 1, round 4); only its QW4–QW7 are implemented here. The diff is against packet 1's tip (`5b35b6c6`, QW1–QW3, its own gate-1 round).

This file is your whole input: you have no repository access; use no connector, tool or web lookup.

**Answer form.**

| commit | verdict (OK / FINDING) | what |
|---|---|---|

Then numbered findings (file:line, why it matters, smallest fix), then one final line: GREEN / GREEN WITH NOTES / RED.

Redaction: none applied. The diff contains no home path, LAN address, host name or call sign; it is `git diff`
output verbatim (the `10.42.0.1/24` in a test is NetworkManager's shared-mode default, already used by that test file's fakes).

## The plan's QW4–QW7 (plan v2 §3 P2 and P3, verbatim; v4 keeps them)

**P2 — per-request reuse in the service family (net 0 lines per file).**
- **QW4 (F4), commit 889a8c1f.** `_lifecycle()` reads the mode through `_request_memo(("meshcore-running-mode",), …)`
  (`services.py:1909`, same line count). WRITERS of the file it reads (`{runtime}/config/files/meshcore.toml`, the
  `meshcore-node` config_file), by `grep -rn "config/files\|meshcore.toml" lhpc` and the write calls in
  `service_params.py`: (1) `write_config_files` (`atomic_write` :3066 / `_write_source_config` :3070) —
  `@invalidates_snapshot`; (2) `clean` with apply, which removes the stack's generated config (`service_maintenance.py:2790-2796`) —
  `@invalidates_snapshot`; (3) the meshcore host only reads it (no write call in `meshcore_host/*.py`). The decorator
  drops the memo on entry AND exit, so the next `_lifecycle()` in the same operation reads the rendered role (test:
  red without the memo = 5 reads; a render inside the request → the next Lifecycle sees `repeater`). A write by
  ANOTHER process during a request is seen at the next request, the same window as the request's snapshot.
- **QW5 (F5, F6)** — auditor OK; not yet coded. `firewall_settings_view(st=None)`; `_fw_listener_endpoints` over
  `self.stacks()` (the snapshot overlays stamp component statuses only, never stack objects).
- **QW6 (F8 only).** `network_view` lists the active connections once; `_nm_active`/`_nm_ethernet` parse that one
  output. Coherence change like QW3 (Wi-Fi and wired rows from one instant), inside a view that is already cached
  across requests for `_NET_VIEW_TTL_S` (10 s, `service_network.py:70`). The config-path memo of v1 is dropped.
**P3 — QW7** — auditor OK with note; to be coded with the exhaustive equal-output test of v1 (7 source states ×
signature and `log_running`), zero git calls per poll.

## Plan v4 §2 (verbatim)

## 2. The coherence changes (one consistent moment instead of a mix; the maintainer's YES, 2026-10-16)
| item | before | after |
|---|---|---|
| QW3 | 22 reads of `/proc` per snapshot, each component at its own instant | one table per snapshot |
| QW4 | each `Lifecycle` in a request re-read the generated MeshCore config | read once per request; LHPC's own writers (`write_config_files`, `clean`, both `@invalidates_snapshot`) drop it, so the next read sees their write; a write by another process during a request is seen from the next request on |
| QW6 | the Wi-Fi row and the wired row each asked NetworkManager | one answer for both rows |
Each commit message states its change in these words; `docs/architecture.md` ("Evidence once per request") states
QW3 and QW6. QW4 carries the same approved within-request trade-off: a change another process makes to the generated
MeshCore config during a page build is reflected from the next request on.

## The commit messages (`git log --reverse 5b35b6c6..a0448a3b`, full)

### b61aeb3c efficiency: the MeshCore running mode once per request

efficiency: the MeshCore running mode once per request

Every Lifecycle re-read and re-parsed the generated MeshCore config for
the running mode: 141 Lifecycle objects and 144 reads per Stacks page on
a Zero (0.95 s). _lifecycle() now takes it through the per-request memo.
Every LHPC writer of that file (write_config_files, clean) is
@invalidates_snapshot, so a render in the same operation drops the memo
and the next Lifecycle reads the new role. A coherence change under the
maintainer's decision (2026-10-16): a write by another process during a
request is seen from the next request on, as for the request's status
snapshot. services.py: same line count.

Red before: test_the_running_mode_is_read_once_per_request_and_again_
after_a_render (5 reads for 5 Lifecycles).

### c5871b23 efficiency: one firewall status per page, the listener list without a snapshot

efficiency: one firewall status per page, the listener list without a snapshot

The Stacks page built the firewall status for its wide reads and again
for the firewall settings section (1.8 s for the two on a Zero); the
section now takes the status the page already has. The firewall's
listener list iterated a whole status snapshot only to reach each
stack's components; it now reads the manifest's stacks, so a
firewall-only read (the firewall page, the CLI) assesses no stack.
service_firewall.py: same line count.

Red before: test_a_stacks_render_builds_the_firewall_candidate_once (2),
test_the_firewall_listener_list_needs_no_status_snapshot (1 assessment);
test_the_firewall_settings_view_is_the_same_with_the_status_handed_in is
the equal-output guard.

### a2a4f37c efficiency: the network view asks NetworkManager for the active connections once

efficiency: the network view asks NetworkManager for the active connections once

The dashboard's Wi-Fi row and wired row each ran `nmcli -t -f
UUID,NAME,TYPE,DEVICE connection show --active` for one view refresh;
the view now asks once and both rows parse that answer, so they show
one moment instead of two (the maintainer's decision, 2026-10-16). The
other callers of _nm_active keep their own read. service_network.py:
same line count.

Red before: test_the_network_view_lists_the_active_connections_once (2
calls); the asserted rows are the equal-output part.

### a0448a3b efficiency: the polled reads run no git

efficiency: the polled reads run no git

The dashboard polls its signature every 4 s (2 s while pending) and a
log page polls its running flag every 2 s; each built a full status
snapshot with git status + describe for every source (32-35 git runs, about
0.85 s of a 1.63 s poll on a Zero). Both read run states only, and a run
state reads the source only as missing / not a repository / binary, which
the filesystem checks decide before git. build_snapshot(sources=False)
returns the request's snapshot when one is cached, else one that runs no
git and is never cached; dash_signature and log_running use it.
services.py, service_lifecycle_ops.py: same line counts.

Red before: test_a_snapshot_without_git_has_the_same_run_states (7 source
states x running/stopped: same run states, no git) and
test_the_dashboard_signature_and_a_log_running_flag_run_no_git (4 git
calls; signature and flag equal the full snapshot's).


## The code report (verbatim; committed as `code-review/code-report-efficiency-p2.md`)

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

## The diff (`git diff 5b35b6c6 a0448a3b`, verbatim)

```diff
diff --git a/CHANGELOG.md b/CHANGELOG.md
index fc20e551..0f34cadc 100644
--- a/CHANGELOG.md
+++ b/CHANGELOG.md
@@ -22,6 +22,8 @@ Fixed
   (unmarked), and NomadNet's records the stacks of every Reticulum process and the shared instance's sockets.
 - Reading a stack's status (console pages, `lhpc status`) no longer takes git's index lock or rewrites the git
   index in the stack's source checkout.
+- The dashboard's and the log pages' background refresh no longer runs git for every installed source; what they
+  show is unchanged.
 
 ## 0.12.9
 
diff --git a/docs/architecture.md b/docs/architecture.md
index 2a917ef8..7259ac81 100644
--- a/docs/architecture.md
+++ b/docs/architecture.md
@@ -288,7 +288,9 @@ leaves on disk and which test proves it: the [invariant table](#invariant-table)
   snapshot, per-(stack, band) config, consumed-source SHAs, firewall status, listeners, git state
   per distinct checkout) through a thread-local request memo dropped at every request start and
   around every mutation; rechecks under operation locks use `build_snapshot(fresh=True)`. A
-  snapshot reads the process table once, so every component is judged against the same table. Every
+  snapshot reads the process table once, so every component is judged against the same table, and
+  the network view asks NetworkManager for the active connections once, so its Wi-Fi and wired rows
+  come from one answer. Every
   public `ControllerService` entry that writes anything is `@invalidates_snapshot`; the rest are
   listed with a reason in `snapshot_memo.SNAPSHOT_NEUTRAL`. The
   neutral writers whose writes no snapshot input observes (lock files, the native RF-log roll,
diff --git a/lhpc/adapters/web/app.py b/lhpc/adapters/web/app.py
index 35b2aad0..1637202c 100644
--- a/lhpc/adapters/web/app.py
+++ b/lhpc/adapters/web/app.py
@@ -1006,9 +1006,9 @@ def create_app(service_factory: ServiceFactory | None = None) -> Flask:
         groups.sort(key=lambda g: (not g["running"], len(g["dep_stacks"]), g["stack"].id))
         return groups, snapshot
 
-    def _firewall_view_safe(svc):
+    def _firewall_view_safe(svc, fw=None):
         try:
-            return svc.firewall_settings_view()
+            return svc.firewall_settings_view(fw)
         except Exception:
             return None                    # fail-safe: never break the Apps page over firewall
 
@@ -1096,7 +1096,7 @@ def create_app(service_factory: ServiceFactory | None = None) -> Flask:
             "controller_system_deps": service.controller_system_deps(),
             "ws_mon": _ws, "ws_certs": (_ws or {}).get("pki", {}).get("clients", []),
             "ws_modes": list(_WS_MODES), "ws_loopback": peer_is_loopback(),
-            "firewall_view": _firewall_view_safe(service),
+            "firewall_view": _firewall_view_safe(service, fw_status),
             "st": service.self_update_status(), "jobs": service.active_jobs(),
             "updater": service.updater_integration(),
             "dep_summary": (_dep := service.dependency_overview()),
diff --git a/lhpc/core/probes/source.py b/lhpc/core/probes/source.py
index 861b1372..1cb4096f 100644
--- a/lhpc/core/probes/source.py
+++ b/lhpc/core/probes/source.py
@@ -77,7 +77,7 @@ def lhpc_patched_only(system: System, abs_path: str, patches) -> bool:
     return _numstat(want.stdout) == _numstat(have.stdout)
 
 
-def probe_source(system: System, spec: SourceSpec, abs_path: str) -> SourceProbe:
+def probe_source(system: System, spec: SourceSpec, abs_path: str, git: bool = True) -> SourceProbe:
     ev = {"path": abs_path}
     if system.fs.readlink(abs_path):
         # A symlink at the managed path is not a managed source (the ONE presence policy:
@@ -90,6 +90,9 @@ def probe_source(system: System, spec: SourceSpec, abs_path: str) -> SourceProbe
     if not system.fs.exists(f"{abs_path}/.git"):
         ev["state"] = "not-a-repo"
         return SourceProbe(SourceState.NOT_A_REPO, evidence=ev)
+    if not git:                     # presence only: a caller that reads run states, not the tree
+        ev["state"] = "not probed"
+        return SourceProbe(SourceState.UNKNOWN, evidence=ev)
 
     # ONE `git status --porcelain=v2 --branch` answers both questions in one call: `# branch.oid` is the
     # full HEAD commit, and every non-header line is a TRACKED-file change (-uno excludes
diff --git a/lhpc/core/service_firewall.py b/lhpc/core/service_firewall.py
index 0a47d527..82dc3b0e 100644
--- a/lhpc/core/service_firewall.py
+++ b/lhpc/core/service_firewall.py
@@ -31,12 +31,12 @@ class FirewallOpsMixin:
         stack+component. A TCP listener WITHOUT metadata is surfaced as `unmapped` (fail
         closed — no allow derived) so a newly added listener can never be silently exposed."""
         mapped, unmapped = [], []
-        for st in self.build_snapshot().stacks if hasattr(self, "build_snapshot") else []:
-            for comp in st.stack.components:
+        for stk in self.stacks() if hasattr(self, "stacks") else []:     # the manifest's, no status
+            for comp in stk.components:
                 for ep in comp.endpoints:
                     if ep.kind != "tcp" or ep.role != "listener":
                         continue
-                    (mapped if ep.firewall else unmapped).append((st.stack, comp, ep))
+                    (mapped if ep.firewall else unmapped).append((stk, comp, ep))
         return mapped, unmapped
 
     def _fw_resolve_scope(self, stk, comp, ep, band=None):
@@ -1092,13 +1092,13 @@ class FirewallOpsMixin:
 
     # ---- settings view + configure (FW-7) ---------------------------------------------------
 
-    def firewall_settings_view(self) -> dict:
+    def firewall_settings_view(self, st=None) -> dict:
         """Everything the Firewall settings section renders: mode, per-listener rows (stable
         id, resolved proto/port, deny flag, selection, whether it exposes non-loopback), the
         locked SSH + proxy-ingress rows, AP controls, foreign-firewall recommendation, and the
-        three script paths for the cmdboxes."""
+        three script paths for the cmdboxes. `st`: the page's own `firewall_status()`, if it has one."""
         fwcfg = getattr(self.config(), "firewall", None)
-        st = self.firewall_status()
+        st = self.firewall_status() if st is None else st
         cand = st.get("candidate") or self.firewall_candidate()   # the status already built it
         rows, seen_ids = [], set()
         for e in cand["endpoints"]:
diff --git a/lhpc/core/service_lifecycle_ops.py b/lhpc/core/service_lifecycle_ops.py
index 8bfc8e61..95f920c8 100644
--- a/lhpc/core/service_lifecycle_ops.py
+++ b/lhpc/core/service_lifecycle_ops.py
@@ -5000,7 +5000,7 @@ class LifecycleOpsMixin:
         interactive apps are marked. The web polls this cheaply and only does a
         full reload when it changes, so live-monitor fields (RSSI/feed/…) update in
         place without the whole page reflowing. Excludes fast-changing telemetry."""
-        snap = self.build_snapshot()
+        snap = self.build_snapshot(sources=False)            # run states only: no git per poll
         up = (RunState.RUNNING, RunState.DEGRADED)
         running = sorted(cid for ss in snap.stacks for cid, st in ss.components.items()
                          if st.run_state in up)
@@ -5764,7 +5764,7 @@ class LifecycleOpsMixin:
                 return False
         s = self.stack(target)
         cid = s.main_component.id if (s and s.main_component) else target
-        for ss in self.build_snapshot().stacks:
+        for ss in self.build_snapshot(sources=False).stacks:     # a run state: no git per poll
             st = ss.components.get(cid)
             if st is not None:
                 return st.run_state in (RunState.RUNNING, RunState.DEGRADED)
diff --git a/lhpc/core/service_network.py b/lhpc/core/service_network.py
index 6916a5dd..1bc95edd 100644
--- a/lhpc/core/service_network.py
+++ b/lhpc/core/service_network.py
@@ -68,6 +68,7 @@ class NetworkOpsMixin:
     # Capability/authorization verdicts re-probe at most this often — both ways.
     _NET_PROBE_TTL_S: ClassVar[float] = 60.0
     _NET_VIEW_TTL_S: ClassVar[float] = 10.0
+    _NM_ACTIVE: ClassVar[tuple] = ("-t", "-f", "UUID,NAME,TYPE,DEVICE", "connection", "show", "--active")
     # NetworkManager actions the Network panel needs (scan/list are already unprivileged):
     # join/save a client profile, plus re-activate the box's own shared AP (wifi.share.*) —
     # without the latter the "Back to AP mode" way home is denied and the box strands.
@@ -122,10 +123,10 @@ class NetworkOpsMixin:
                          "priority": parts[4]})
         return rows
 
-    def _nm_active(self) -> dict:
-        """The active wifi connection as {uuid, name} plus the device address, or {}."""
-        rc, out, _err = self._nmcli(["-t", "-f", "UUID,NAME,TYPE,DEVICE",
-                                     "connection", "show", "--active"])
+    def _nm_active(self, listed=None) -> dict:
+        """The active wifi connection as {uuid, name} plus the device address, or {}; `listed`: the
+        caller's (rc, out) of `_NM_ACTIVE`, so the Wi-Fi and the wired row come from one answer."""
+        rc, out = listed or self._nmcli(self._NM_ACTIVE)[:2]
         act: dict = {}
         if rc == 0:
             for line in out.splitlines():
@@ -139,11 +140,10 @@ class NetworkOpsMixin:
             act["address"] = (out.splitlines() or [""])[0].strip() if rc == 0 else ""
         return act
 
-    def _nm_ethernet(self) -> dict:
+    def _nm_ethernet(self, listed=None) -> dict:
         """The active wired connection as {name, device, address}, or {} (the dashboard's
-        Network row: a wired uplink is shown in preference to Wi-Fi)."""
-        rc, out, _err = self._nmcli(["-t", "-f", "UUID,NAME,TYPE,DEVICE",
-                                     "connection", "show", "--active"])
+        Network row: a wired uplink is shown in preference to Wi-Fi). `listed`: as `_nm_active`."""
+        rc, out = listed or self._nmcli(self._NM_ACTIVE)[:2]
         eth: dict = {}
         if rc == 0:
             for line in out.splitlines():
@@ -292,11 +292,11 @@ class NetworkOpsMixin:
 
             from . import deps as deps_mod
             view["install"] = deps_mod.network_rule_install_cmd(getpass.getuser())
-        act = self._nm_active()
+        act = self._nm_active(listed := self._nmcli(self._NM_ACTIVE)[:2])   # one answer, both rows
         view["mode"] = ("ap" if act.get("name") == self.AP_PROFILE
                         else ("client" if act else "off"))
         view["active"] = act
-        view["ethernet"] = self._nm_ethernet()
+        view["ethernet"] = self._nm_ethernet(listed)
         if view["mode"] == "ap":
             rc, out, _err = self._nmcli(["-g", "802-11-wireless.ssid", "connection", "show",
                                          self.AP_PROFILE])
diff --git a/lhpc/core/services.py b/lhpc/core/services.py
index b0a1c3b9..0b16805b 100644
--- a/lhpc/core/services.py
+++ b/lhpc/core/services.py
@@ -594,7 +594,7 @@ class ControllerService(WebserverOpsMixin, AutoInstallOpsMixin, SelfUpdateOpsMix
 
     # ---- the single probing path (used by CLI and web) -------------------
 
-    def build_snapshot(self, *, fresh: bool = False) -> Snapshot:
+    def build_snapshot(self, *, fresh: bool = False, sources: bool = True) -> Snapshot:
         """Bounded, read-only assessment of every stack. Memoized WITHIN a single request/operation
         only: a page render calls this ~15× (one per stack helper) — recomputing all of it each time
         re-scans /proc and re-runs git for every source (seconds). The cache is dropped at the START of
@@ -603,14 +603,13 @@ class ControllerService(WebserverOpsMixin, AutoInstallOpsMixin, SelfUpdateOpsMix
         paths), so it is NEVER reused across a mutation or an HTTP request: a nested public `stop()`
         inside `start()` re-invalidates on exit, so the outer op's post-mutation read recomputes.
 
-        `fresh=True` FORCES a recompute (and refreshes the cache) — used by the authoritative
-        running-rechecks that run UNDER the operation locks, to bypass a snapshot this same op cached
-        before it acquired its lock (a concurrent op could have changed state in between). Never
-        memoize those.
+        `fresh=True` FORCES a recompute (and refreshes the cache) for the authoritative rechecks UNDER
+        the operation locks (a concurrent op could have changed state since this op's cached read).
+        `sources=False` (the polls, which read run states only) runs no git: this request's snapshot if
+        cached, else one that is never cached, its sources only missing/not-a-repo/binary/unknown.
 
-        The confirmed-working map comes from the OPERATOR-CONFIRMED known-working compositions (file
-        reads only): a component is confirmed-working when its clean source HEAD appears in a stored
-        composition of its stack."""
+        Confirmed-working = a component's clean source HEAD appears in an OPERATOR-CONFIRMED stored
+        known-working composition of its stack (file reads only)."""
         cached = getattr(self._snapshot_state, "cache", None)
         if cached is not None and not fresh:
             return cached
@@ -635,12 +634,13 @@ class ControllerService(WebserverOpsMixin, AutoInstallOpsMixin, SelfUpdateOpsMix
                 for cid in spec.covers:
                     binary_cover[cid] = rec
         snap = StatusProber(self._system, self._paths, confirmed,
-                            binary_cover=binary_cover,
+                            binary_cover=binary_cover, sources=sources,
                             meshcore_mode=self.meshcore_running_mode()).assess_stacks(self.stacks())
         self._overlay_runtime_bands(snap)
         self._overlay_gui_unavailable(snap)
         self._overlay_licensed_tx_enabled(snap)
-        self._snapshot_state.cache = snap
+        if sources:
+            self._snapshot_state.cache = snap
         return snap
 
     def _overlay_licensed_tx_enabled(self, snap) -> None:
@@ -1906,7 +1906,7 @@ class ControllerService(WebserverOpsMixin, AutoInstallOpsMixin, SelfUpdateOpsMix
     def _lifecycle(self) -> Lifecycle:
         lc = Lifecycle(self._paths, self.stacks(), self.config(), self._system)
         # Stop verification probes the endpoints the RUNNING launch opened (MeshCore: by mode).
-        running = self.meshcore_running_mode()           # once per operation, not per poll
+        running = self._request_memo(("meshcore-running-mode",), self.meshcore_running_mode)
         lc.expected_endpoints = lambda comp: _meshcore_mode.expected_endpoints(comp, running)
         wrap = getattr(self._ext, "wrap_spawn", None) if self._ext else None
         if wrap is not None:
diff --git a/lhpc/core/status.py b/lhpc/core/status.py
index 4c3ed675..130ff7bd 100644
--- a/lhpc/core/status.py
+++ b/lhpc/core/status.py
@@ -64,8 +64,9 @@ class StatusProber:
 
     def __init__(self, system: System, paths: Paths, profiles: dict | None = None,
                  binary_cover: dict | None = None,
-                 meshcore_mode: str = _meshcore_mode.DEFAULT_MODE) -> None:
+                 meshcore_mode: str = _meshcore_mode.DEFAULT_MODE, sources: bool = True) -> None:
         self._system = system
+        self._sources = sources                  # False: no git per source (`build_snapshot`)
         self._paths = paths
         self._profiles = profiles or {}
         # The MeshCore stack's effective mode (services.meshcore_mode()): decides which of the
@@ -247,7 +248,8 @@ class StatusProber:
         key = (abs_path, comp.source.pin_commit or "")
         probe = self._source_probes.get(key)
         if probe is None:
-            probe = self._source_probes[key] = probe_source(self._system, comp.source, abs_path)
+            probe = self._source_probes[key] = probe_source(self._system, comp.source, abs_path,
+                                                            git=self._sources)
         return probe
 
     def _profile_state(self, comp: Component, src) -> ProfileState:
diff --git a/tests/core/test_snapshot_memo.py b/tests/core/test_snapshot_memo.py
index edf14391..e9094deb 100644
--- a/tests/core/test_snapshot_memo.py
+++ b/tests/core/test_snapshot_memo.py
@@ -910,3 +910,85 @@ def test_a_snapshot_reads_the_process_table_once(tmp_path):
 
 def _same_spec(svc, cid, spec):
     return next(c for s in svc.stacks() for c in s.components if c.id == cid).process == spec
+
+
+_PIN = "c" * 40
+
+
+def _webui_world(tmp_path, state):
+    """The meshcore-webui source (a patched checkout) in one of seven source states, as FakeSystem
+    sees it: (system extras, binary_cover)."""
+    from lhpc.core.assets import asset_path
+    src = str(tmp_path / "src" / "meshcore-webui")
+    paths = {src, src + "/.git"} if state != "missing" else set()
+    if state == "not-a-repo":
+        paths = {src}
+    head = "d" * 40 if state == "differs" else _PIN
+    dirty = state in ("dirty", "patched")
+    status = f"# branch.oid {head}\n# branch.head main\n" + ("1 .M N... 100644 100644 100644 a b f\n"
+                                                             if dirty else "")
+    cmds = {("git", "-C", src, "status", "--porcelain=v2", "--branch", "--untracked-files=no"):
+            CR(0, status, ""), ("git", "-C", src, "describe", "--tags", "--always"): CR(0, "v1\n", "")}
+    if state == "patched":
+        return paths, cmds, None, src, asset_path
+    cover = None
+    if state == "binary":
+        cover = {"meshcore-webui": types.SimpleNamespace(
+            components={"meshcore-webui": _PIN}, artifact_sha256="e" * 64, filename="a.tar.zst")}
+    return paths, cmds, cover, src, None
+
+
+@pytest.mark.parametrize("running", [True, False])
+@pytest.mark.parametrize("state", ["match", "dirty", "differs", "patched", "missing", "not-a-repo",
+                                   "binary"])
+def test_a_snapshot_without_git_has_the_same_run_states(tmp_path, state, running):
+    # The polled reads (the dashboard signature, a log page's running flag) use run states only, and a
+    # run state reads the source only as present / not a repository / binary — which the filesystem
+    # checks decide before any git runs. Same run states, no git.
+    import dataclasses
+
+    from lhpc.core.status import StatusProber
+    probe_paths, cmds, cover, src, asset_path = _webui_world(tmp_path, state)
+    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
+    stacks = svc.stacks()
+    webui = next(c for s in stacks for c in s.components if c.id == "meshcore-webui")
+    if asset_path is not None:                                    # patched: the patch is LHPC's own
+        files = [str(asset_path(p[len("{asset}/"):])) if p.startswith("{asset}/") else p
+                 for p in webui.source.patches]
+        cmds[("git", "-C", src, "apply", "--reverse", "--check", *files)] = CR(0, "", "")
+        cmds[("git", "-C", src, "apply", "--numstat", *files)] = CR(0, "1\t0\tf\n", "")
+        cmds[("git", "-C", src, "diff", "HEAD", "--numstat")] = CR(0, "1\t0\tf\n", "")
+    stacks = tuple(dataclasses.replace(s, components=tuple(
+        dataclasses.replace(c, source=dataclasses.replace(c.source, pin_commit=_PIN))
+        if c.id == "meshcore-webui" else c for c in s.components)) for s in stacks)
+    argv = [f"/opt/bin/{webui.process.exec_name}", *webui.process.all_args]
+    states = {}
+    for sources in (True, False):
+        fake = FakeSystem(paths=probe_paths, commands=cmds,
+                          cmdlines_data={77: argv} if running else {})
+        snap = StatusProber(fake.system, Paths(runtime_root=tmp_path), binary_cover=cover,
+                            sources=sources).assess_stacks(stacks)
+        states[sources] = {cid: st.run_state for ss in snap.stacks
+                           for cid, st in ss.components.items()}
+        git = [c for c in fake.calls if c[:1] == ["git"]]
+        assert bool(git) == (sources and state not in ("missing", "not-a-repo", "binary"))
+    assert states[False] == states[True]
+    assert states[True]["meshcore-webui"].value in (
+        ("running", "degraded") if running else
+        ("not-installed",) if state in ("missing", "not-a-repo") else ("stopped",))
+
+
+def test_the_dashboard_signature_and_a_log_running_flag_run_no_git(tmp_path):
+    probe_paths, cmds, _cover, _src, _ = _webui_world(tmp_path, "match")
+    fake = FakeSystem(paths=probe_paths, commands=cmds)
+    svc = ControllerService(system=fake.system, paths=Paths(runtime_root=tmp_path))
+    full_sig = (svc.build_snapshot(), svc.dash_signature())[1]    # the request's full snapshot
+    full_run = svc.log_running("meshcore")
+    calls = fake.calls
+    assert any(c[:1] == ["git"] for c in calls)                   # precondition: the full read runs git
+    svc.invalidate_snapshot()
+    before = len(calls)
+    assert svc.dash_signature() == full_sig
+    svc.invalidate_snapshot()
+    assert svc.log_running("meshcore") == full_run
+    assert [c for c in calls[before:] if c[:1] == ["git"]] == []
diff --git a/tests/host/test_firewall.py b/tests/host/test_firewall.py
index b2eae726..4cae3089 100644
--- a/tests/host/test_firewall.py
+++ b/tests/host/test_firewall.py
@@ -3395,3 +3395,42 @@ def test_foreign_tables_keep_a_nat_postrouting_chain_that_queues():
 
 def test_foreign_tables_unreadable_ruleset_is_unknown():
     assert _foreign(_nm_shared_ruleset(), rc=1) is None
+
+
+def test_a_stacks_render_builds_the_firewall_candidate_once(web, monkeypatch):
+    # The Stacks page built the firewall status for its wide reads and again for the settings
+    # section (1.8 s for the two on a Zero); the section now takes the status the page has.
+    from lhpc.core import service_firewall as sf
+    n = []
+    orig = sf.FirewallOpsMixin.firewall_candidate
+    monkeypatch.setattr(sf.FirewallOpsMixin, "firewall_candidate",
+                        lambda self, *a, **k: (n.append(1), orig(self, *a, **k))[1])
+    assert web().get("/stacks").status_code == 200
+    assert len(n) == 1
+
+
+def test_the_firewall_settings_view_is_the_same_with_the_status_handed_in(tmp_path):
+    from lhpc.core.paths import Paths
+    from lhpc.core.probes.backends import FakeSystem
+    from lhpc.core.services import ControllerService
+    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
+    assert svc.firewall_settings_view(svc.firewall_status()) == svc.firewall_settings_view()
+
+
+def test_the_firewall_listener_list_needs_no_status_snapshot(tmp_path, monkeypatch):
+    # The listener list is the manifest's; building a whole status snapshot for it cost a firewall-
+    # only read (the firewall page, the CLI) a full assessment of every stack.
+    from lhpc.core import status as statusmod
+    from lhpc.core.paths import Paths
+    from lhpc.core.probes.backends import FakeSystem
+    from lhpc.core.services import ControllerService
+    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
+    mapped, unmapped = svc._fw_listener_endpoints()
+    ids = [(s.id, c.id, e.address) for s, c, e in mapped + unmapped]
+    assessed, orig = [], statusmod.StatusProber.assess_stacks
+    monkeypatch.setattr(statusmod.StatusProber, "assess_stacks",
+                        lambda self, stacks: (assessed.append(1), orig(self, stacks))[1])
+    svc.invalidate_snapshot()
+    mapped2, unmapped2 = svc._fw_listener_endpoints()
+    assert [(s.id, c.id, e.address) for s, c, e in mapped2 + unmapped2] == ids and ids
+    assert assessed == []
diff --git a/tests/host/test_network_controls.py b/tests/host/test_network_controls.py
index c07ab8a5..598884c5 100644
--- a/tests/host/test_network_controls.py
+++ b/tests/host/test_network_controls.py
@@ -1282,3 +1282,18 @@ def test_network_view_without_a_wired_link(tmp_path):
     _fake_nmcli(svc, _std_replies(active="CL-UUID-2:HomeNet:802-11-wireless:wlan0\n"))
     nv = svc.network_view()
     assert nv["mode"] == "client" and nv["ethernet"] == {} and "ap_ssid" not in nv
+
+
+def test_the_network_view_lists_the_active_connections_once(tmp_path):
+    # The Wi-Fi row and the wired row each ran `nmcli connection show --active`: the view now asks
+    # once and both rows come from that one answer (one moment instead of two).
+    svc = _svc(tmp_path)
+    active = ("AP-UUID-1:lhpc-ap:802-11-wireless:wlan0\n"
+              "ETH-UUID-3:Wired connection 1:802-3-ethernet:eth0\n")
+    calls = _fake_nmcli(svc, _std_replies(active=active))
+    view = svc.network_view()
+    assert sum("--active" in c for c in calls) == 1
+    assert (view["mode"], view["active"]["name"], view["active"]["address"]) == \
+        ("ap", "lhpc-ap", "10.42.0.1/24")
+    assert view["ethernet"] == {"name": "Wired connection 1", "device": "eth0",
+                                "address": "10.42.0.1/24"}
diff --git a/tests/stacks/test_meshcore_mode.py b/tests/stacks/test_meshcore_mode.py
index 2aec4ac6..fddc3430 100644
--- a/tests/stacks/test_meshcore_mode.py
+++ b/tests/stacks/test_meshcore_mode.py
@@ -457,3 +457,23 @@ def test_an_interactive_service_keeps_its_settings_tick(tmp_path):
     assert svc.save_config("reticulum", {"autostart_nomadnet": "on"}).ok
     svc._invalidate_config()
     assert "nomadnet" in [c.id for _s, c in svc._run_order("reticulum")]   # planned (MANUAL_REQUIRED)
+
+
+def test_the_running_mode_is_read_once_per_request_and_again_after_a_render(tmp_path, monkeypatch):
+    # Every Lifecycle re-read and re-parsed the generated config for the running mode (144 times per
+    # Stacks page on a Zero). It is read once per request; a render in the same request (a start)
+    # is seen by the next Lifecycle, so a stop is never verified against the mode before the render.
+    svc = _svc(tmp_path)
+    _set_mode(svc, "chat+repeater", name="Relay 1")
+    svc.write_config_files(mm.STACK_ID)
+    _set_mode(svc, "repeater", name="Relay 1")                    # saved, not rendered yet
+    reads = []
+    orig = ControllerService.meshcore_running_mode
+    monkeypatch.setattr(ControllerService, "meshcore_running_mode",
+                        lambda self: (reads.append(1), orig(self))[1])
+    node = _node(svc)
+    svc.invalidate_snapshot()                                     # a request starts
+    ports = [[mm._port(e) for e in svc._lifecycle().expected_endpoints(node)] for _ in range(5)]
+    assert ports == [[5000, 8000]] * 5 and len(reads) == 1
+    svc.write_config_files(mm.STACK_ID)                           # a start renders the new role
+    assert [mm._port(e) for e in svc._lifecycle().expected_endpoints(node)] == [8000]
```
