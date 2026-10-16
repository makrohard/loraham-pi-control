# Code report: efficiency-a6 (closed Apps rows build no settings body)

Branch `cons/efficiency-a6-r1`, stacked on `cons/efficiency-a3-r1` (`a601b774`) on `origin/dev` = `d77e34f4`. Plan:
`PLAN-efficiency` v4 §5 (gate 1 GREEN, round 4). One commit.

**For the record (after the second read):** its note, adopted by the handler: the CHANGELOG bullet sits under "Changed" (with A3's), not "Fixed". The stack was rebased on A3's amended commits: code commit `15b29bfd` → `ef10bcad`: the CHANGELOG placement, and one `docs/architecture.md` row whose four `app.py` line pointers the two new functions had shifted by 15 (`run_server` 2497 → 2519, `_trusted_host` 311 → 326, `_csrf_ok` 244 → 259, `_set_headers` 295 → 310; found by checking every `file:line` pointer in `docs/` against its named function). The gate-1 file below this report is kept as handed to round 9.

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
