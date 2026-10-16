# Code report: testrun-p8 v3 (the golden box's TNC endpoint is its own socket)

## Changes v1 → v3 (round 8 verdict, RED, 1 finding; accepted)

| finding | change |
|---|---|
| ownership per box root: a stale, still-listening TNC of the same box could make a later spawn read ready | the contract is stated (ownership per box, as production's readiness is an address check) and enforced: `KissBox.spawn` refuses a spawn while an earlier TNC of the box still lives (pid + start time, the harness's `_alive`/`_starttime`); regression test `test_a_later_spawn_is_refused_while_an_earlier_tnc_of_the_box_lives`. No golden spawns while an earlier TNC lives (all 63 pass with the refusal in place) |

(v2 changed the report only: the second observed instance below.)

Branch `cons/testrun-p8-r1` on `origin/dev` = `d77e34f4`. Found under load by another run (a pre-existing flake);
the handler put it in the X3 test run as its own packet (no planned packet owns `tests/golden/`). Tests only.

## Commit

| commit | files | tests | red-before |
|---|---|---|---|
| 7ecd1fbe | tests/golden/conftest.py, tests/golden/entry_host.py, tests/golden/test_harness.py | golden layer: `test_a_foreign_loopback_listener_is_never_the_tncs_endpoint` asserts `list(box.fake.listeners) == []` (the endpoint the box's `FakeSystem` reports) while a foreign process listens on 127.0.0.1; `test_a_later_spawn_is_refused_while_an_earlier_tnc_of_the_box_lives` asserts the second `spawn` raises and only one process was started | yes: the new test against the parent's `conftest.py` + `entry_host.py` (d77e34f4): `1 failed` — `Left contains one more item: Listener(family='ipv4', ip='127.0.0.1', port=8001, …)`; after: passed. The second test is a new invariant of the harness; its negative case: against the v1 harness (f42abc17's conftest.py) it fails, `DID NOT RAISE AssertionError` |

## HOUSE RULES KEPT (tools/check_houserules.py origin/dev..HEAD, before the report commit)

```
HOUSE RULES KEPT  origin/dev..HEAD
changelog    +0 bullets (limit 2, each ≤ 3 lines; longest 0), top section 20 lines   OK
docs         0 files, +0 lines; no fact given   OK
tests        2 new tests; layers tests/golden=golden; red-before 1/1   OK
patterns     getsource 0 assert-True 0 source-read 0 sleep 0 startswith 0 network 0   OK
markers      0 without slow   OK
attribution  authors makrohard; trailers 0; words 0   OK
modules      no family file touched; family net +0 lines, +0 public   OK
VERDICT      PASS  (hard 0, warn 0)
```

## The defect and the change

- **Before.** `kiss_box` called `_free_port()` (bind 127.0.0.1:0, read the port, close), and the TNC process bound
  that port later, at its spawn. `_TncEndpoint(port)` kept every LISTEN socket on 127.0.0.1:`port`. In the window
  between the close and the TNC's bind, or while the TNC never listens (`endpoint_never_up`), another process may
  listen on that port; the box then read it as the TNC's endpoint: a start could verify ready on a foreign socket,
  a stop could see an endpoint that is not the TNC's.
- **After.**
  - The TNC binds `('127.0.0.1', 0)` itself (argv[1] `listen`), so no port is chosen in advance.
  - `_TncEndpoint(token)` keeps a 127.0.0.1 LISTEN socket only when its inode is in `_tnc_socket_inodes(token)`:
    the socket fds of the live processes whose argv is `[python, -c, _TNC, <listen>, <term>, <token>]`, the token
    being the box's runtime root.
  - It is still reported as port 8001 (the manifest's), the only translation, as before.
  - `_free_port`, `KissBox.port` and `host.json`'s `port` are removed; `entry_host.py` rebuilds the endpoint from
    `d["root"]`. Across processes this finds the same TNCs, because they are matched by argv, not by a parent's
    pid list.
- **A second observed instance** (after the first, in a loaded local `-n 12` run): CI run 38827223542 on the
  dev tip, job `test (3.13)`: `tests/golden/test_same_decision_across_entry_paths.py:282` AssertionError, the
  start reporting `loraham-kiss-tnc: ready endpoint(s) never came up (127.0.0.1:8001: absent (family=ipv4))`
  (1 failed, 7672 passed; 3.11 and 3.12 green). An absent endpoint fits the race's other half: the port taken
  between `_free_port()`'s release and the TNC's bind, so the TNC's bind fails and it never listens; this change
  removes that half too (no port is chosen in advance). Not excluded from the log alone: a TNC that bound but
  took longer than the box's 3 s endpoint wait to start under load (its stderr goes to /dev/null); this change
  does not alter that wait.
- **One TNC per box.** Ownership is per box (the token), not per spawn: as in production, where readiness is
  an address check, whatever TNC of the box listens counts. So that this listener is always the one live TNC's,
  `KissBox.spawn` asserts that no earlier TNC of the box (pid with its recorded start time) is alive before it
  starts another. No golden reaches that state today (the survives-SIGTERM cases end without a new spawn); a
  future one fails loudly at the spawn instead of reading the old listener as the new TNC's.
- Readiness and cessation stay observed: the listener exists only while the TNC process holds it; a TNC that
  never listens or has exited is absent.

## The 6-point block

1. **Contracts (test harness):**
   - `_TncEndpoint(token)` iterates `Listener`s (port 8001) held by the box's TNC processes.
   - `_tnc_socket_inodes(token) -> set[int]`.
   - `KissBox(root, fake, svc)` lost its `port` argument; both callers are updated (`grep -rn 'KissBox(' tests` →
     conftest.py, entry_host.py).
2. **Invariants:** a foreign loopback listener is never the box's endpoint; one box never runs two TNCs at once
   (the two new tests). Readiness through the box's own TNC is still proven by every start golden that verifies
   ready (tests/golden: 63 passed).
3. **Failure classes:**
   - A process that exits between the `/proc` listing and the fd read raises `OSError`; it is skipped.
   - A process of another user cannot be read: `OSError`, skipped (a TNC is always ours).
   - Two boxes in parallel workers never share a token: each has its own tmp root.
4. **Test rules:** rule 4 (no ambient host dependency: a host listener can no longer change a golden's verdict);
   rule 12 (red before, shown); the case is tagged `intended:` as the golden docstring rule requires.
5. **Directories:** `setsid -w python -m pytest -q -p no:cacheprovider -n 12 --dist loadfile tests/golden tests/repo`
   → `641 passed, 1 skipped in 33.24s`; `ruff check lhpc testlab` and `ruff check tests --select F,E9` → `All checks
   passed!`.
6. **Adversarial self-review:**
   - (a) The new test binds `getattr(box, "port", 0)`: on the parent it takes the box's chosen port (the race made
     certain), after the fix any free port. Either way a foreign listener exists while the box's TNC is not
     running; the assertion is the same.
   - (b) The `import socket` the harness no longer uses was removed (ruff F401).
   - (c) Not shown: the original flake itself under load (it needs the race to hit). The red-before reproduces its
     mechanism deterministically instead; the CI instance above is consistent with it but not proven to be it.

## Numbers, measured

| figure | command | output |
|---|---|---|
| base | `git log -1 --format=%h origin/dev` | d77e34f4 |
| diff | `git diff --stat origin/dev 7ecd1fbe` | 3 files changed, 87 insertions(+), 31 deletions(-) |
| red-before | `git checkout d77e34f4 -- tests/golden/conftest.py tests/golden/entry_host.py; pytest tests/golden/test_harness.py -k foreign` | 1 failed |
| after | the same with the branch's files | 1 passed |
| directories | the command in 5 | 641 passed, 1 skipped |
| new-invariant negative | v1 harness + `pytest tests/golden/test_harness.py -k later_spawn` | 1 failed (DID NOT RAISE) |
