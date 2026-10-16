# Code report: sec-lows (group B: the security analysis's mechanical lows L0, L6, L7, L8) — v3

Branch `cons/sec-lows-r1` on `origin/dev` = `75e7972f` (local; v1/v2 sat on `0a7fb61d`, the rebase is range-diff `=`; it lands after the 0.12.10 release commit, 0.12.11
content). Source: ANALYSIS-2-SECURITY-v3.md (sha256 bf5063b6…), rows L0 and L5-L8. The maintainer's decision: group B
is ONE packet of the mechanical lows plus L0's CSRF defence; the design lows (L2 flash passphrase, L3 signers, L4 pip
pins) stay parked. L1 (CRL early renewal) is its own branch.

**v3 against v2 (packet sha256 715de38b…, DELTA-1 c99ab9ba…):** gate 1 round 4 (auditor 6b) was RED on one point: v2's
`_hsts_ok()` stopped a NEW mixed http/https lock-out, but a browser that had already cached the console's
`max-age=31536000` kept refusing the http listener until that policy expired, and the CHANGELOG overstated it. The
decision: in a mixed configuration every https listener sends `Strict-Transport-Security: max-age=0`, which a browser
obeys on its next https visit by dropping the cached policy (RFC 6797 6.1.1). L6 is amended (`_hsts()` returns the line
every TLS listener sends; the test has five cases), the shared CHANGELOG bullet says "prevents new HSTS lockouts and
clears an already-cached policy on the next HTTPS visit", and the stack is rebased onto dev `75e7972f`. L7, L8 and L0
are unchanged in content; every sha moves. DELTA-1's disabled-stack case is kept (its expectation is unchanged).

**v2 against v1 (packet sha256 3e15a953…, stack ending cda87245):** the second read found that L6 widened a lock-out:
a stack listener may be plain http (a no-auth stack, config.py:825), and HSTS binds the whole host. On dev only the
console sent HSTS; with v1's L6 an https stack UI sent it too, so a browser that met only that UI was also locked out
of an http stack UI on the host. The decision: send HSTS (console AND stack blocks) only when every listener on the
host is TLS. L6 is amended (`_hsts_ok()`, one CHANGELOG line, the test now four cases); this also fixes the HSTS
mirror finding v1 left for the maintainer. To keep group B at two CHANGELOG bullets, L7 extends L6's "Web console"
bullet instead of adding its own. L8 and L0 are unchanged (range-diff `=`); new shas throughout.

## Commits

| commit | low | files | test | red-before (the test on the commit's parent) |
|---|---|---|---|---|
| 34d16d08 | L6 HSTS | lhpc/core/webserver.py, tests/web/test_stackweb.py, CHANGELOG.md | `test_hsts_is_sent_only_when_every_listener_on_the_host_is_tls` [console + stacks → policy lines, max-age=0 lines: https+(https) 2, 0; https+(http) 0, 1; https+(https, http) 0, 2; http+(https) 0, 1; http+(http) 0, 0]; `test_a_disabled_http_stack_ui_does_not_withhold_hsts` | 5 FAILED: the four cases with a TLS listener (the parent sends the year-long policy from the console only, and never max-age=0) and the disabled-stack case (the parent's stack block has no HSTS: 1 ≠ 2). http+(http) passes: no TLS listener sends anything on either side. The disabled-stack case also fails without the `enabled` filter (DELTA-1) |
| 9f30559b | L7 p12 | lhpc/core/pki.py, lhpc/core/service_webserver.py (docstring, same lines), tests/web/test_webserver.py, CHANGELOG.md | `test_a_revoked_labels_leftover_p12_is_not_served` | FAILED: the leftover bundle is on disk and the download answers 200 |
| c2c6b0f1 | L8 peer | lhpc/adapters/web/app.py, tests/web/test_webserver.py | `test_without_the_nginx_header_only_a_loopback_socket_peer_is_loopback`; `test_peer_is_loopback_trusts_only_nginx_header` (updated) | FAILED `assert 200 == 403` (a 203.0.113.5 peer without the header gets the .p12); the updated test FAILED `assert True is False` (no header, no peer address read as loopback) |
| a39f38c3 | L0 CSRF | lhpc/adapters/web/app.py, tests/web/test_csrf_origin.py, CHANGELOG.md, docs/architecture.md | `test_a_post_from_another_origin_is_refused_even_with_the_token` [same-site, cross-site] and 4 guards | FAILED [same-site], [cross-site]; 4 passed (same-origin, none, absent header, a GET). A cherry-pick of `cons/csrf-origin-r1` 3a698f3c, applied clean; author makrohard |

Each red-before was run on a clean detached checkout of the commit's parent with only that commit's test file laid
over it; the checkout was clean again afterwards (`git status --short` empty).

## What each commit does

- **L6:** `_hsts(cfg, stack_webs)` returns the line every TLS listener sends (the console, and each stack block at
  server level, which its locations inherit: none sets its own `add_header`): the policy (`max-age=31536000`) when
  the console and every ENABLED stack UI are https, else `max-age=0`. HSTS binds the whole host, every port: a
  plain-http listener beside one that sends the policy is refused by a browser for a year; `max-age=0` makes the
  browser drop a cached policy on its next https visit (RFC 6797 6.1.1), so a box that already locked a UI out heals
  and no new lock-out starts. A plain-http listener sends no header (a browser ignores HSTS over http). A disabled
  stack UI (port 0) has no listener and does not count. `render_nginx_config` reads `stack_webs` twice and takes a
  tuple of it first. A default deployment renders byte-identically: it has no stack blocks.
- **L7:** `pki.read_export`, the only reader of an export bundle (its one caller, `webserver_cert_export_bytes`,
  serves the route and `lhpc webserver cert export`), returns None unless the label is `active` in
  `list_client_certs`, the state the copyboxes filter on (revoked and revocation-pending are not active). In
  `service_webserver.py` the check would have broken the file's 2388-line budget; there only the docstring changed
  (same lines), correcting "the CLI locates the file directly", which was not true.
- **L8:** `peer_is_loopback()`: behind nginx unchanged (the nginx-set `X-LHPC-Peer` decides). Without the header (the
  bare interactive TCP mode, tests) the socket's `request.remote_addr` decides: only 127.0.0.1 and ::1 are loopback.
  The analysis offered "document it, or check SO_PEERCRED"; refusing every header-less request would also refuse the
  bare TCP mode's own loopback browser (its .p12 download). The remote_addr form closes the gap without that. It
  also removes the premise L0 builds on ("a missing `X-LHPC-Peer` counts as loopback").
- **L0 (the cons/csrf-origin-r1 commit):** `_csrf_ok` refuses a POST whose `Sec-Fetch-Site` is present and not `same-origin`/`none`
  (another site, or another port of the box: a stack UI); an absent header (the CLI, curl) passes as before.

## Operator-visible, and the CHANGELOG

| commit | effect | CHANGELOG (Unreleased, Fixed) |
|---|---|---|
| L6 | a box with a stack UI on plain http: its https listeners send `max-age=0` instead of the year-long policy (a browser drops a cached policy on the next https visit, so the http UI is reachable again); all-https boxes: unchanged for the console, and the stack UIs send it too | one "Web console" bullet, shared with L7 (below) |
| L7 | `lhpc webserver cert export` of a revoked label answers "no export bundle … issue or reissue it first"; the console download 404s | the L6 bullet's second half: "Web console: with a stack web UI on plain http, the https listeners send HSTS `max-age=0`, which prevents new HSTS lockouts and clears an already-cached policy on the next HTTPS visit (all-https boxes keep HSTS, stack UIs too); a revoked certificate's leftover `.p12` is no longer handed out, by the download or `lhpc webserver cert export`." |
| L8 | none: production always serves through nginx, which sets the header | — |
| L0 | a form sent from a stack UI's page to the console is refused | "Web console: a form or request the browser marks as sent from another site or from another port of the box (a stack's web UI) is refused even with a valid token." |

## L5 dropped

L5 proposed `--proto '=https' --proto-redir '=https'` on graywolf-fetch.sh. The default download is already
integrity-checked: `EXPECTED` comes from the script's pinned table (`sums`, graywolf-fetch.sh:59 on dev), an empty one
stops the script (:77), and `sha256sum -c` stops on a mismatch before anything is unpacked (:113-114). Only the
opt-in `--from-upstream` mode trusts the release's own checksums.txt. The script is a build input of graywolf
(tests/stacks/test_asset_build_inputs.py), so any edit makes every graywolf box read "needs build" once after the
update (web start and auto-install gate on it). The handler dropped L5 as not worth that. (A drafted form needed
`=https,file`: the test lab's `GRAYWOLF_BASE_URL` is file://.)

## The HSTS mirror: fixed here

v1 listed it for the maintainer: an https console sends HSTS for the host, so a stack UI with a plain-http listener
on the same host was refused by any browser that had seen the console. L6's rule closes it, a policy already cached
included (above).

## HOUSE RULES KEPT (tools/check_houserules.py origin/dev..HEAD --fact "is refused even with the token")

```
HOUSE RULES KEPT  origin/dev..HEAD
changelog    +2 bullets (limit 2, each ≤ 3 lines; longest 3), top section 36 lines   OK
docs         1 files, +3 lines; fact "is refused even with the token" -> docs/architecture.md   OK
tests        7 new tests; layers tests/web=web; red-before 3/4   WARN
patterns     getsource 0 assert-True 0 source-read 0 sleep 0 startswith 0 network 0   OK
markers      0 without slow   OK
attribution  authors makrohard; trailers 0; words 0   OK
modules      service_webserver.py 2388->2388 lines (budget 2388), 42->42 public (budget 42); family net +0 lines, +0 public   OK
VERDICT      PASS  (hard 0, warn 1)
```

The WARN: the L0 commit's message names no red-before (cherry-picked unchanged from cons/csrf-origin-r1); it is measured
above.

## The 6-point block

1. **Contracts:** the console's HSTS line, `peer_is_loopback()`, `pki.read_export`, `_csrf_ok`; no signature changes.
2. **Invariants:** "a remote peer can never appear loopback" (now also without nginx); only active certificates'
   bundles leave the box; no TLS listener sends a lasting HSTS policy while the host has a plain-http listener.
3. **Failure classes:** a header-less request from a non-loopback or unknown socket peer is not loopback. A same-uid
   client talking to the 0600 Unix socket directly, without nginx, is one: waitress reports a Unix-socket peer as
   `localhost` (waitress/server.py:415-416), not an IP. It loses the loopback-only actions and can read the files
   itself. A cross-port form post is refused.
4. **Test rules:** each test drives the route or the renderer; no source reads.
5. **Directories:** `pytest -n 12 tests/web tests/cli tests/repo tests/install` → 3508 passed, 1 skipped; `ruff check lhpc testlab`
   and `ruff check tests --select F,E9` (the CI gates) → clean.
6. **Adversarial self-review:**
   - L6's first form sent HSTS from an https stack block beside an http console (the lock-out); fixed before hand-in.
   - v1's L6 still widened the mirror lock-out (an https stack UI beside an http one); found by the second read, v2.
   - Group B's CHANGELOG first came to three bullets (the checker's limit is two, HARD); L6 and L7 share one.
   - v2's rule did not heal a box that had already cached the year-long policy (gate 1 round 4); v3 sends max-age=0.
   - Correction to DELTA-1: it said the disabled-stack case "passes on the parent (dev)". That was not run, and it is
     false: on dev the stack block sends no HSTS, so the count is 1, not 2 (measured now: FAILED). The rest of
     DELTA-1 was measured and holds.
   - L7's first form put the check into service_webserver.py: the checker's modules row was HARD (+5 lines over the
     budget). Moved to pki.py (one caller).
   - L8 differs from the first proposal ("refuse a missing header"); the reason is above.
   - L0's doc hunk updates numbered pointers in docs/architecture.md (app.py +5). When this stack is rebased onto the
     doc-pointers landing (pointers without line numbers), that part of the hunk is empty; its sentence stays.
   - On this stack the pointer resolver shows only the 3 stale pointers the docs sweep fixes; group B adds none.

## Numbers, measured

| figure | command | output |
|---|---|---|
| base | `git log -1 --format=%h origin/dev` | 75e7972f |
| diff | `git diff origin/dev --stat` | 9 files, +149 / −16 |
| tests | `pytest -n 12 tests/web tests/cli tests/repo tests/install` | 3508 passed, 1 skipped |
| red-before | the four parent runs above | L6 5 failed; L7 1 failed; L8 2 failed; L0 2 failed |
