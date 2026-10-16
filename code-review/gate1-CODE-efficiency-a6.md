# Gate 1: efficiency-a6 (closed Apps rows build no settings body), v1

**Request.** Judge the code commit below against the plan's A6 (quoted). That means: correctness; whether each
red-before test can fail for the defect named; whether the `/stacks` response is unchanged, as claimed, in every way a
row renders; whether the moved forced-open rule is the template's old rule; the CHANGELOG line; and the report's
claims against the diff. The plan is GREEN (gate 1, round 4). The diff is against A3's tip (`a601b774`).

This file is your whole input: you have no repository access; use no connector, tool or web lookup.

**Answer form.**

| commit | verdict (OK / FINDING) | what |
|---|---|---|

Then numbered findings (file:line, why it matters, smallest fix), then one final line: GREEN / GREEN WITH NOTES / RED.

Redaction: none applied. The diff contains no home path, LAN address, host name or call sign; it is `git diff`
output verbatim.

## The plan's A6 (plan v4 §5, verbatim)

## 5. A6 — closed `/stacks` rows build no body context
**Today.** A closed row's body is already fetched on first expand (`/stacks/<sid>/body`, `stacklazy.js`, with its
loading state "Loading …" and its failure state "Could not load the settings. Open the full page" → `?open=`), but
`_stack_groups` (`app.py:853`) still computes every row's full body context (config views, parameter groups,
dependency reports, known-working offer, daemon parameters, web links, credentials, HMAC state…): ≈ 3.3 s of the 6.6 s
`/stacks` on the Zero. The summary and row actions read only `stack, state, running, main, jobs, fetched_ver,
meshcore_mode, meshcore_running_mode, pin_override, upstream, update_main, update_available, update_component,
update_component_is_main` (the two macros in `_macros.html`).
**Change.** `_stack_groups` builds the summary fields for every row and the body fields only for rows whose body is
rendered inline (the template's forced-open rule: jobs, the remembered-open cookie, `?open=`/`?cfg=`/`?dp=`/`?inst=`);
the forced-open rule moves from the template into one Python helper the template reads, so the two cannot disagree.
The lazy route keeps building one full row. The `/stacks` response is unchanged byte for byte.
**Tests** (`tests/web/test_lazy_stacks.py`, layer web): (a) a closed row's body fields are not computed (call count
of `config_view`/`deps_report` = forced rows only; red: every row); (b) the WHOLE `/stacks` response is equal byte for
byte between the new build and today's build-every-row path, per scenario: all closed; each forced-open trigger
(jobs, the remembered-open cookie, `?open`, `?cfg`, `?dp`, `?inst`). Two decisions stay apart in the code: which rows
render their body INLINE (the forced-open rule, unchanged in effect) and which rows get their body FIELDS computed
(the new narrowing). "Today's path" patches only the second to select every row, which is exactly what `_stack_groups`
computes today; the inline rule is untouched; both
responses come from one test client and session (same CSRF token), so nothing is normalised. If a byte differs, the
test prints the first differing line. The fragment checks (summary/row actions; inline body against
`/stacks/<sid>/body` for the same row) stay for diagnosis; (c) the existing loading and failure states keep their browser test (testlab lane; a failing body fetch shows
the fallback link) — added if none exists. **Gain** ≈ 3 s per `/stacks` on the Zero (estimate; measured in box window 2).

## The commit message (`git log a601b774..15b29bfd`, full)

### 15b29bfd efficiency: closed Apps rows build no settings body

efficiency: closed Apps rows build no settings body

A closed stack row's settings body was already fetched only when the row
is opened (stacklazy.js), but the page still built every row's body
fields: config views, parameter groups, dependency reports, known-working
offers, daemon parameters, web links, credentials (about half of a
6.6 s Stacks render on a Zero). Those fields are now built only for the
rows the page renders inline, and for the one row the lazy route asks
for. The forced-open rule (a job, the remembered-open cookie, open / cfg
/ dp / inst) moved from the template into one function, _row_inline, so
the row that is shown open and the row that gets a body cannot disagree.
The page is unchanged byte for byte.

Red before: test_a_closed_row_builds_no_settings_body (config_view ran
for every stack). Equal output: test_the_stacks_page_is_the_same_byte_
for_byte over seven scenarios (all closed; a job; the cookie; open, cfg,
dp, inst) against the build-every-row path; test_which_rows_render_their_
body_inline (the rule's truth table).


## The code report (verbatim; committed as `code-review/code-report-efficiency-a6.md`)

# Code report: efficiency-a6 (closed Apps rows build no settings body)

Branch `cons/efficiency-a6-r1`, stacked on `cons/efficiency-a3-r1` (`a601b774`) on `origin/dev` = `d77e34f4`. Plan:
`PLAN-efficiency` v4 §5 (gate 1 GREEN, round 4). One commit.

## Commits

| commit | subject | files | tests (layer; asserted field) | red-before |
|---|---|---|---|---|
| 15b29bfd | efficiency: closed Apps rows build no settings body | adapters/web/app.py, templates/stacks.html, tests/web/test_lazy_stacks.py, CHANGELOG.md | web: `test_a_closed_row_builds_no_settings_body` (with `?open=kiss`, the stacks `config_view` ran for == `{"kiss"}`); `test_the_stacks_page_is_the_same_byte_for_byte[closed, jobs, cookie, open, cfg, dp, inst]` (the whole `/stacks` response text equals the one with `_row_needs_body` patched to select every row, i.e. today's build-every-row path; one test client; nothing normalised); `test_which_rows_render_their_body_inline` (11 cases of `_row_inline`: job, cookie, cookie with another row named, cookie and named, each of the four arguments, another row named, nothing) | yes, on a601b774: 19 failed — the count test behaviourally (`config_view` ran for every stack: `{'chat', 'daemon', …} == {'kiss'}`); the 7 byte cases and the 11 truth-table cases structurally (`_row_needs_body` / `_row_inline` do not exist on the parent) |

**Against the old tree, measured** (not a test: the old template cannot run inside the new code). The script
`efficiency-a6-pages.py` (run dir) renders `/stacks` from one tree in a fixed FakeSystem world and runtime root, for 9
scenarios: the 7 above plus a cookie naming `kiss` while `?open=chat` names another row, and a three-row cookie
(`kiss,controller-row,chat`). Run once with the old tree (`git archive ef4cb067 lhpc`, before this commit) and once
with the new; then compared with the one per-client CSRF token (a 32-hex string, one distinct per page) normalised:
9 of 9 identical, byte counts 68 103 – 103 846 (`efficiency-a6-pages-out.txt`, run dir).

**Green** (on dceff071, the same commit before its parent gained the A3 report commit; `git diff dceff071 15b29bfd --
lhpc tests` is empty): `python -m pytest -q -p no:cacheprovider -n 12 --dist loadfile tests/core tests/install tests/repo
tests/web tests/stacks tests/host tests/cli tests/golden` → `7730 passed, 1 skipped in 107.16s`. `ruff check lhpc testlab` → `All checks passed!`; `ruff check tests --select F,E9` →
`All checks passed!`. `tests/web/test_lazy_stacks.py`: `25 passed`.

## HOUSE RULES KEPT (checker output, verbatim)

```text
HOUSE RULES KEPT  a601b774..15b29bfd
changelog    +1 bullets (limit 2, each ≤ 3 lines; longest 2), top section 28 lines   OK
docs         0 files, +0 lines; no fact given   OK
tests        3 new tests; layers tests/web=web; red-before 1/1   OK
patterns     getsource 0 assert-True 0 source-read 0 sleep 0 startswith 0 network 0   OK
markers      0 without slow   OK
attribution  authors makrohard; trailers 0; words 0   OK
modules      no family file touched; family net +0 lines, +0 public   OK
VERDICT      PASS  (hard 0, warn 0)
```

## Simplicity guardrails

- Two plain module functions in the adapter (`_row_inline`, 8 lines; `_row_needs_body`, 4 lines), one `if` around the
  body fields, one new keyword (`restored_open`) on the closure `_stack_groups`. No new state, no new route, no JS.
- What moved: the forced-open expression from `stacks.html:185` into `_row_inline` (the template now reads
  `g.inline`); the jobs index from after the row loop to before it (each row needs its jobs to decide); the cookie
  parse in `_stacks_context` from after `_stack_groups` to before it. `service.active_jobs()` is still called once
  by `_stack_groups` (and once more by the page context, as before).
- Body-only fields (built only when `_row_needs_body`): `hardware_block, radio_block, stack_webs, ui_creds_list,
  pin_refusal, removable, command, view, config_groups, system_deps, deps_report, needs_build, needs_build_deps,
  daemon_params, kw_offer, hmac_enabled, restart_required`. Always built: everything the row summary, its row
  actions, the sort key and the page outside the rows read (`stack, state, running, main, jobs, dep_stacks,
  fetched_ver, upstream, meshcore_mode, meshcore_running_mode, pin_override, update_*` and the cheap rest).
- Lines (`wc -l`, ef4cb067 → 15b29bfd): app.py 2670 → 2692 (the adapter is not in the service family);
  stacks.html 231 → 231; test_lazy_stacks.py 80 → 137.

## Deviations from the plan

- None in substance. The plan's fragment checks (summary / inline body against `/stacks/<sid>/body`) are not added:
  the whole-response byte test and the old-tree comparison cover more, and the existing
  `test_partial_body_route_renders_every_stack` keeps the lazy route rendering every stack.

## The 6-point block

1. **Contracts.**
   - `_row_inline(sid, jobs, restored_open, args) -> bool` (`app.py:220`): the template's former expression, verbatim
     in meaning: `jobs` non-empty, or `sid` in the cookie set unless one of `open`/`cfg`/`dp`/`inst` names a
     different row, or any of those four equal to `sid`.
   - `_row_needs_body(inline, only_sid) -> bool` (`app.py:229`): `inline or only_sid is not None`; the test seam.
   - `_stack_groups(..., restored_open=frozenset())` (`app.py:868`): each row gets `jobs` and `inline` (`:956`) and
     the body fields only `if _row_needs_body(inline, only_sid)` (`:998`). `_stacks_context` passes the cookie set
     (`:1104`). The lazy route passes `only_sid`, so its one row is always complete.
   - `stacks.html:185`: `{% set _forced = g.inline %}`; every use of `_forced` below is unchanged.
2. **Invariants and their tests.** The page is unchanged: the 7-scenario byte test (lasting) and the 9-scenario
   old-tree comparison (measured once). A closed row builds no body: the count test. The rule is the old one: the
   11-case truth table. The lazy route still renders every stack: `test_partial_body_route_renders_every_stack`
   (existing); a remembered-open and a forced-open row still render inline: the two existing tests.
3. **Failure classes.** A body field read by a closed row's markup would raise in Jinja on a missing key: the byte
   test renders every scenario, and the summary/row-action macros read only always-built fields (checked against
   `_macros.html`). A failing service call in a body field now fails only when that row is inline or fetched, as
   it already did for the lazy route. EIO/EACCES, Ctrl-C, stacked conflicts: no I/O or lock moved. CLI/web/job/boot:
   web only; nothing in the core changed.
4. **Test rules.** Through the Flask route and a typed helper; the byte test is exact because the page's HTML is
   the contract this change claims to keep; no source reading.
5. **Directories and ruff.** As under Green.
6. **Adversarial self-review.** Found: (a) the byte test alone compared the new template with itself — so the old
   tree was measured too (9 scenarios); (b) a test docstring used a word the checker counts; reworded. Checked:
   `active_jobs` is a neutral read that only drops dead job records (`snapshot_memo.SNAPSHOT_NEUTRAL`), so calling
   it before the row loop instead of after changes nothing it returns within the request.

## Numbers, measured

| figure | command | output |
|---|---|---|
| stacks whose body was built with one row open, before | the count test on a601b774 | every stack (`{'chat', 'daemon', …}`) |
| old vs new page, 9 scenarios | `efficiency-a6-pages.py` twice + normalised compare | 9 SAME |
| `/stacks` all closed, this PC, empty fake world | `efficiency-a6-time.py <tree> <root> 7`, twice | old 73 / 67 ms, new 22 / 16 ms (median of 7) |
| the Zero figure (≈ 3.3 s of 6.6 s in `_stack_groups`) | reported by `fieldtest-e293/50-profile-endpoints.txt` (box window 1) | — |

## The old-tree comparison: script and output (verbatim, from the run directory)

```python
"""Write the /stacks response for each A6 scenario, from the lhpc tree in argv[1], into argv[2]/<scenario>.html.
Same FakeSystem world for both trees; the runtime root argv[3] is shared, so both trees sign with the same console secret."""
import sys
sys.path.insert(0, sys.argv[1])
from pathlib import Path
from lhpc.adapters.web.app import create_app
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService
out, root = Path(sys.argv[2]), Path(sys.argv[3])
(root / "config").mkdir(parents=True, exist_ok=True)
JOB = {"stack": "kiss", "log": "build-loraham-kiss-tnc.log", "op": "build", "target": "kiss"}
URLS = {"closed": "/stacks", "jobs": "/stacks", "cookie": "/stacks", "open": "/stacks?open=kiss",
        "cfg": "/stacks?cfg=kiss", "dp": "/stacks?dp=kiss", "inst": "/stacks?inst=kiss",
        "cookie-other-named": "/stacks?open=chat", "two-cookie": "/stacks"}
for how, url in URLS.items():
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=root))
    if how == "jobs":
        svc.active_jobs = lambda: [dict(JOB)]
    c = create_app(service_factory=lambda: svc).test_client()
    if how in ("cookie", "cookie-other-named"):
        c.set_cookie("lhpc_open", "kiss")
    if how == "two-cookie":
        c.set_cookie("lhpc_open", "kiss,controller-row,chat")
    (out / f"{how}.html").write_bytes(c.get(url).data)
```

```text
# A6: /stacks responses, old tree (cons/efficiency-a3-r1 = ef4cb067's lhpc/) vs new (A6), same FakeSystem world and runtime root
# script: efficiency-a6-pages.py ; comparison: 32-hex tokens normalised (the per-client CSRF token; one distinct per page)
cfg.html SAME bytes old=90192 new=90192 distinct-tokens=1
closed.html SAME bytes old=68103 new=68103 distinct-tokens=1
cookie.html SAME bytes old=90167 new=90167 distinct-tokens=1
cookie-other-named.html SAME bytes old=81777 new=81777 distinct-tokens=1
dp.html SAME bytes old=90167 new=90167 distinct-tokens=1
inst.html SAME bytes old=90214 new=90214 distinct-tokens=1
jobs.html SAME bytes old=90331 new=90331 distinct-tokens=1
open.html SAME bytes old=90167 new=90167 distinct-tokens=1
two-cookie.html SAME bytes old=103846 new=103846 distinct-tokens=1
# timing (efficiency-a6-time.py, median of 7, twice)
a6base: /stacks all closed, median 73 ms over 7
a6head: /stacks all closed, median 22 ms over 7
a6base: /stacks all closed, median 67 ms over 7
a6head: /stacks all closed, median 16 ms over 7
```

## The diff (`git diff a601b774 15b29bfd`, verbatim)

```diff
diff --git a/CHANGELOG.md b/CHANGELOG.md
index c3696c39..0b843a1d 100644
--- a/CHANGELOG.md
+++ b/CHANGELOG.md
@@ -26,6 +26,8 @@ Fixed
   show is unchanged.
 - The dashboard checks for changes made elsewhere every 10 s (was 4 s; still 2 s while a start is pending), and log
   pages fetch new lines every 5 s (was 2 s), which lowers the console's load on a small box.
+- The Apps page loads faster: a closed stack's settings are prepared only when the stack is opened; the page itself
+  is unchanged.
 
 ## 0.12.9
 
diff --git a/lhpc/adapters/web/app.py b/lhpc/adapters/web/app.py
index 1637202c..1e10ad4b 100644
--- a/lhpc/adapters/web/app.py
+++ b/lhpc/adapters/web/app.py
@@ -217,6 +217,21 @@ _SECURITY_HEADERS = {
 ServiceFactory = Callable[[], ControllerService]
 
 
+def _row_inline(sid: str, jobs, restored_open, args) -> bool:
+    """Whether the Stacks page renders `sid`'s settings body inline (open at first paint): a running
+    job, the remembered-open cookie (unless another row is named), or `open`/`cfg`/`dp`/`inst`
+    naming it. Every other row's body is fetched when it is opened (stacklazy.js)."""
+    named = args.get("open") or args.get("cfg") or args.get("dp") or args.get("inst")
+    return bool(jobs or (sid in restored_open and (not named or named == sid))
+                or sid in (args.get("open"), args.get("cfg"), args.get("dp"), args.get("inst")))
+
+
+def _row_needs_body(inline: bool, only_sid) -> bool:
+    """Whether a Stacks row gets its settings-body fields built: only a row the page renders inline,
+    or the one row a lazy body request asks for."""
+    return inline or only_sid is not None
+
+
 def create_app(service_factory: ServiceFactory | None = None) -> Flask:
     """Build the Flask app. `service_factory` is injectable for tests."""
     app = Flask(__name__)
@@ -850,14 +865,17 @@ def create_app(service_factory: ServiceFactory | None = None) -> Flask:
             lis = None
         return fw, lis
 
-    def _stack_groups(band="", only_sid=None, cfg_sid="", *, fw_status=None, listeners=None):
+    def _stack_groups(band="", only_sid=None, cfg_sid="", *, fw_status=None, listeners=None,
+                      restored_open=frozenset()):
         """Per-stack overview rows for the Stacks page. Each row now carries the FULL per-stack
         detail: component statuses/evidence, system+build+runtime
         dependency diagnosis, needs-build, daemon parameters, known-working offer, restart-required
         and stack-scoped conflicts — all read-only, GET-safe. `band` is threaded (as the detail page
         did) into the band-aware views. `only_sid` limits the heavy per-stack build to ONE row (the
         lazy-body partial) — the holistic snapshot/conflicts are still computed, but the ~14 per-stack
-        service calls run for just that stack instead of all of them."""
+        service calls run for just that stack instead of all of them. On the whole page those calls
+        run only for the rows rendered inline (`_row_inline`); a closed row carries what its summary
+        and row actions show, and its body comes from the lazy route when it is opened."""
         snapshot = service.build_snapshot()  # fresh, read-only evidence each load
         rollup = rollup_states(snapshot)
         stack_deps = stack_dependencies([ss.stack for ss in snapshot.stacks])
@@ -871,6 +889,9 @@ def create_app(service_factory: ServiceFactory | None = None) -> Flask:
         # reads `unchecked`, not a stale green/yellow.
         _fresh = service.source_check_view()
         _fresh_comps = _fresh.get("components", {})
+        jobs_by_stack = {}
+        for job in service.active_jobs():
+            jobs_by_stack.setdefault(job.get("stack"), []).append(job)
 
         groups = []
         for ss in snapshot.stacks:
@@ -906,14 +927,12 @@ def create_app(service_factory: ServiceFactory | None = None) -> Flask:
             buildable = bool(main and not main.source and main.build_steps)
             installed = bool(has_source and main_status and main_status.source_state.value
                              in ("match", "dirty", "differs", "unknown", "not-a-repo", "binary"))
-            _unbuilt = service.unbuilt_components(stack.id)
             # A fetched-binary stack has no clone, so the source-gated Uninstall/Clean line never
             # rendered for it — yet `lhpc uninstall graywolf` works and DOES remove the fetched
             # artifact. The console has to offer the removal the CLI already accepts, otherwise a
             # stack can be installed from the console but only removed from a shell. Gated on
             # anything removable being ON DISK — not on "fully built", which hid the buttons for
             # an interrupted fetch or a stale pin, exactly the states where removal is the fix.
-            removable = bool(buildable and service.fetched_artifacts_present(stack.id))
             interactive = bool(main and main.interactive)
             member_ids = {c.id for c in stack.components}
             # Freshness, resolved per component against the head the verdict was computed from.
@@ -933,10 +952,10 @@ def create_app(service_factory: ServiceFactory | None = None) -> Flask:
                 _upd_comp, _upd_is_main = _behind[0], False
             else:
                 _upd_comp, _upd_is_main = None, False
-            groups.append({
+            _jobs = jobs_by_stack.get(stack.id, [])
+            inline = _row_inline(stack.id, _jobs, restored_open, request.args)
+            g = {
                 "stack": stack,
-                "hardware_block": service.hardware_block(stack.id),  # "" unless no hardware configured
-                "radio_block": service.radio_mode_block(stack.id),   # "" unless the mode excludes it
                 "main": index.get(main.id) if main else None,
                 "main_status": main_status,
                 "statuses": ss.components,            # dict[comp_id -> ComponentStatus] (evidence etc.)
@@ -946,13 +965,6 @@ def create_app(service_factory: ServiceFactory | None = None) -> Flask:
                 "update_component": _upd_comp,            # the behind component to deep-link to
                 "update_component_is_main": _upd_is_main,  # main -> Install section; dep -> #comp-<id>
                 "update_checked_ago": _checked_ago(_fresh.get("checked_at", 0)),
-                # [] for a stack with no web UI -> no Webserver sub-section; else one entry per
-                # proxied PAGE (component with a client http/https endpoint).
-                "stack_webs": service.stack_web_views(stack.id, listeners=listeners,
-                                                      fw_status=fw_status),
-                # One Password sub-section per component that declares where its self-generated
-                # UI password lives — independent of web pages.
-                "ui_creds_list": service.ui_credentials_list(stack.id),
                 # The MeshCore stack's Mode switch on the stack body and its pill on the Apps row
                 # ("" elsewhere): the saved mode, the choices, and the mode the running launch was
                 # generated with (shown only while the stack runs and differs from the saved one).
@@ -964,11 +976,9 @@ def create_app(service_factory: ServiceFactory | None = None) -> Flask:
                 # A3: an install over the pin check still in force (the pill and the warning), and
                 # the typed pin refusal of the last failed console install job.
                 "pin_override": service.binary_active_override(stack.id),
-                "pin_refusal": service.binary_install_refusal(stack.id),
                 "installed": installed,
                 "has_source": has_source,
                 "buildable": buildable,
-                "removable": removable,
                 "fetched_ver": (_fv := (service.fetched_version_state(stack.id)
                                         if buildable else {})),
                 # Reuse _fv's installed instead of a second fetched_version_state read.
@@ -980,29 +990,41 @@ def create_app(service_factory: ServiceFactory | None = None) -> Flask:
                 "dep_stacks": [(d, rollup.get(d, "unknown")) for d in stack_deps[stack.id]],
                 "bands": sorted({c.band for c in stack.components if c.band}),
                 "interactive": interactive,
-                "command": service.manual_start_command(main) if interactive else "",
-                # Per-stack Settings section rendered inline on the Apps list (same data the
-                # stack-detail Settings uses): operator, component-scoped params, sources, autostart.
-                "view": service.config_view(stack.id, sband),
-                "config_groups": service.config_param_groups(stack.id, sband),
-                # Folded-in detail sections (all read-only / GET-safe):
-                "system_deps": service.system_deps(stack.id),
-                "deps_report": service.deps_report(stack.id),
-                "needs_build": _unbuilt,
-                "needs_build_deps": service.unbuilt_build_deps(stack.id),   # must build BEFORE the main
-                "daemon_params": service.daemon_params_view(stack.id, band),
-                "kw_offer": service.known_working_offer(stack.id, snapshot),
-                # None when HMAC does not apply here (no flag/row); else whether it is ENABLED.
-                "hmac_enabled": service.hmac_status(stack.id),
-                "restart_required": service.restart_required(stack.id),
                 "conflicts": [c for c in all_conflicts
                               if any(h in member_ids for h in c.holders)],
-            })
-        jobs_by_stack = {}
-        for job in service.active_jobs():
-            jobs_by_stack.setdefault(job.get("stack"), []).append(job)
-        for g in groups:
-            g["jobs"] = jobs_by_stack.get(g["stack"].id, [])
+                "jobs": _jobs,
+                "inline": inline,
+            }
+            if _row_needs_body(inline, only_sid):
+                g.update({
+                    "hardware_block": service.hardware_block(stack.id),  # "" unless no hardware configured
+                    "radio_block": service.radio_mode_block(stack.id),   # "" unless the mode excludes it
+                    # [] for a stack with no web UI -> no Webserver sub-section; else one entry per
+                    # proxied PAGE (component with a client http/https endpoint).
+                    "stack_webs": service.stack_web_views(stack.id, listeners=listeners,
+                                                          fw_status=fw_status),
+                    # One Password sub-section per component that declares where its self-generated
+                    # UI password lives — independent of web pages.
+                    "ui_creds_list": service.ui_credentials_list(stack.id),
+                    "pin_refusal": service.binary_install_refusal(stack.id),
+                    "removable": bool(buildable and service.fetched_artifacts_present(stack.id)),
+                    "command": service.manual_start_command(main) if interactive else "",
+                    # Per-stack Settings section rendered inline on the Apps list (same data the
+                    # stack-detail Settings uses): operator, component-scoped params, sources, autostart.
+                    "view": service.config_view(stack.id, sband),
+                    "config_groups": service.config_param_groups(stack.id, sband),
+                    # Folded-in detail sections (all read-only / GET-safe):
+                    "system_deps": service.system_deps(stack.id),
+                    "deps_report": service.deps_report(stack.id),
+                    "needs_build": service.unbuilt_components(stack.id),
+                    "needs_build_deps": service.unbuilt_build_deps(stack.id),   # must build BEFORE the main
+                    "daemon_params": service.daemon_params_view(stack.id, band),
+                    "kw_offer": service.known_working_offer(stack.id, snapshot),
+                    # None when HMAC does not apply here (no flag/row); else whether it is ENABLED.
+                    "hmac_enabled": service.hmac_status(stack.id),
+                    "restart_required": service.restart_required(stack.id),
+                })
+            groups.append(g)
         groups.sort(key=lambda g: (not g["running"], len(g["dep_stacks"]), g["stack"].id))
         return groups, snapshot
 
@@ -1072,14 +1094,14 @@ def create_app(service_factory: ServiceFactory | None = None) -> Flask:
         it inline; the lazy partial passes None — the transient belongs to the whole-page render).
         `only_sid` (lazy partial) builds just that stack's group — the page-wide ctx below is O(1)."""
         fw_status, listeners = _render_wide_reads()
-        groups, snapshot = _stack_groups(band, only_sid, cfg_sid, fw_status=fw_status,
-                                         listeners=listeners)
         # Rows the operator had open on their last visit (mirrored to the `lhpc_open` cookie by
         # stacks_state.js). Rendering them already-open with their body inline — like a ?open= row —
         # means a restored-open row is present at FIRST PAINT, so re-opening it never shifts the page
         # (CLS). One general mechanism: any top-level row id (a stack sid, or "controller-row").
         from urllib.parse import unquote as _unquote
         restored_open = {s for s in _unquote(request.cookies.get("lhpc_open", "")).split(",") if s}
+        groups, snapshot = _stack_groups(band, only_sid, cfg_sid, fw_status=fw_status,
+                                         listeners=listeners, restored_open=restored_open)
         # Controller-owned Webserver component, rendered INLINE in the controller row. Cached
         # evidence only (monitor_view: no probing/mutation) — fail-safe so it never breaks /stacks.
         from lhpc.core.config import WEBSERVER_ACCESS_MODES as _WS_MODES
diff --git a/lhpc/adapters/web/templates/stacks.html b/lhpc/adapters/web/templates/stacks.html
index 27d3f271..3d63a7c3 100644
--- a/lhpc/adapters/web/templates/stacks.html
+++ b/lhpc/adapters/web/templates/stacks.html
@@ -182,7 +182,7 @@
       {% for g in groups %}
         {# `restored_open` (from the lhpc_open cookie) makes a remembered-open row render inline+open at
            first paint, so re-opening it on reload causes no layout shift — same effect as a ?open= row. #}
-        {% set _forced = g.jobs or (g.stack.id in restored_open and (not _explicit_row or _explicit_row == g.stack.id)) or request.args.get('open') == g.stack.id or request.args.get('cfg') == g.stack.id or request.args.get('dp') == g.stack.id or request.args.get('inst') == g.stack.id %}
+        {% set _forced = g.inline %}{# the forced-open rule: app.py `_row_inline` #}
         <div class="stackrow-wrap">
           <details class="stackrow" id="stackrow-{{ g.stack.id }}"{% if _forced %} open data-force-open="1"{% endif %}>
             <summary>{{ stack_summary(g) }}</summary>
diff --git a/tests/web/test_lazy_stacks.py b/tests/web/test_lazy_stacks.py
index 43f0fd63..a5d29d15 100644
--- a/tests/web/test_lazy_stacks.py
+++ b/tests/web/test_lazy_stacks.py
@@ -4,6 +4,7 @@ the body server-side, so the settings are always reachable."""
 
 from __future__ import annotations
 
+import pytest
 from htmlq import parse
 from lhpc.core.paths import Paths
 from lhpc.core.probes.backends import FakeSystem
@@ -78,3 +79,59 @@ def test_closed_placeholder_has_noscript_fallback_link(tmp_path, web):
     body = c.get("/stacks").get_data(as_text=True)
     # A no-JS / fetch-failure fallback: a link that opens the stack server-side.
     assert "<noscript>" in body and "?open=" in body
+
+
+_JOB = {"stack": "kiss", "log": "build-loraham-kiss-tnc.log", "op": "build", "target": "kiss"}
+_SCENARIOS = {"closed": "/stacks", "jobs": "/stacks", "cookie": "/stacks", "open": "/stacks?open=kiss",
+              "cfg": "/stacks?cfg=kiss", "dp": "/stacks?dp=kiss", "inst": "/stacks?inst=kiss"}
+
+
+def _scenario(c, svc, how):
+    """The URL of one way a row renders: all closed, or `kiss` forced open by a running job, the
+    remembered-open cookie, or one of the four query arguments."""
+    if how == "jobs":
+        svc.active_jobs = lambda: [dict(_JOB)]
+    if how == "cookie":
+        c.set_cookie("lhpc_open", "kiss")
+    return _SCENARIOS[how]
+
+
+def test_a_closed_row_builds_no_settings_body(tmp_path, web):
+    # The page built every row's settings body (config views, dependency reports, …: about half of a
+    # Stacks render on a Zero) although a closed row's body is fetched only when it is opened.
+    c, svc = _client(web, tmp_path)
+    calls = []
+    orig = svc.config_view
+    svc.config_view = lambda sid, *a, **k: (calls.append(sid), orig(sid, *a, **k))[1]
+    c.get("/stacks?open=kiss")
+    assert set(calls) == {"kiss"}
+
+
+@pytest.mark.parametrize("how", list(_SCENARIOS))
+def test_the_stacks_page_is_the_same_byte_for_byte(tmp_path, web, monkeypatch, how):
+    """The whole response equals the one the page gives when every row builds its body (what it did
+    before), for every way a row renders; one test client, nothing normalised."""
+    from lhpc.adapters.web import app as app_mod
+    c, svc = _client(web, tmp_path)
+    url = _scenario(c, svc, how)
+    lazy = c.get(url).get_data(as_text=True)
+    monkeypatch.setattr(app_mod, "_row_needs_body", lambda inline, only_sid: True)
+    full = c.get(url).get_data(as_text=True)
+    first = next((i for i, (a, b) in enumerate(zip(lazy.splitlines(), full.splitlines())) if a != b), None)
+    assert lazy == full, (first, lazy.splitlines()[first] if first is not None else len(lazy) - len(full))
+
+
+@pytest.mark.parametrize("jobs, cookie, args, inline", [
+    ([], set(), {}, False),                                     # nothing names it: lazy
+    ([_JOB], set(), {}, True),                                  # a running job
+    ([], {"kiss"}, {}, True),                                   # remembered open
+    ([], {"kiss"}, {"open": "chat"}, False),                    # remembered, but another row is named
+    ([], {"kiss"}, {"cfg": "kiss"}, True),                      # remembered and named
+    ([], set(), {"open": "kiss"}, True), ([], set(), {"cfg": "kiss"}, True),
+    ([], set(), {"dp": "kiss"}, True), ([], set(), {"inst": "kiss"}, True),
+    ([], set(), {"open": "chat", "dp": "kiss"}, True),          # any of the four naming it
+    ([], set(), {"open": "chat"}, False),                       # another row named
+])
+def test_which_rows_render_their_body_inline(jobs, cookie, args, inline):
+    from lhpc.adapters.web.app import _row_inline
+    assert _row_inline("kiss", jobs, cookie, args) is inline
```
