# Gate 1: sec-lows (group B: the security analysis's mechanical lows L0, L6, L7, L8) — v3

**Request.** Round 4 was RED on one point (L6: a browser that cached the year-long HSTS policy kept refusing a plain-http listener; the CHANGELOG overstated it). Judge v3 against that finding and the decision quoted in the report: in a mixed http/https configuration every https listener sends `max-age=0`. Judge: does `_hsts()` send the policy only when every listener on the host is TLS and `max-age=0` otherwise, on every TLS listener; does `max-age=0` clear a cached policy as claimed (RFC 6797 6.1.1); do the five render cases and the disabled-stack case pin it, and can each red-before fail for its defect; is the CHANGELOG line now exact; is anything else changed (L7, L8 and L0 are range-diff `=` apart from the shared bullet and the rebase onto dev 75e7972f); is any claim stronger than the evidence.

This file is your whole input: you have no repository access; use no connector, tool or web lookup.

**Answer form.**

| commit | verdict (OK / FINDING) | what |
|---|---|---|

Then numbered findings (file:line, why it matters, smallest fix), then one final line: GREEN / GREEN WITH NOTES / RED.

Redaction: none needed; the diffs are `git diff` output verbatim. Addresses in tests are documentation ranges.

## v2 → v3

```text
1:  3b53a0ac ! 1:  34d16d08 sec-lows L6: HSTS only when every listener on the host is TLS, stack UIs included
2:  2aaebfc1 ! 2:  9f30559b sec-lows L7: a revoked certificate's leftover .p12 bundle is not served
3:  fb67f3ee = 3:  c2c6b0f1 sec-lows L8: without the nginx header the socket's peer decides loopback
4:  7cf1957e = 4:  a39f38c3 web: refuse a POST the browser marks as from another site or port
```

`git diff 7cf1957e HEAD -- lhpc tests/web CHANGELOG.md` (7cf1957e = the v2 + DELTA-1 stack tip; the rebase onto `75e7972f` adds only its own tests/install commit, left out here):

```diff
diff --git a/CHANGELOG.md b/CHANGELOG.md
index 33ed2ebd..ff43137f 100644
--- a/CHANGELOG.md
+++ b/CHANGELOG.md
@@ -33,8 +33,8 @@ Fixed
   index in the stack's source checkout.
 - The dashboard's and the log pages' background refresh no longer runs git for every installed source; what they
   show is unchanged.
-- Web console: while a stack's web UI is served over plain http, the box no longer sends HSTS, which made browsers
-  refuse that UI once they had seen the console (with every listener on https, the stack UIs send it as well); a
+- Web console: with a stack web UI on plain http, the https listeners send HSTS `max-age=0`, which prevents new HSTS
+  lockouts and clears an already-cached policy on the next HTTPS visit (all-https boxes keep HSTS, stack UIs too); a
   revoked certificate's leftover `.p12` is no longer handed out, by the download or `lhpc webserver cert export`.
 
 ## 0.12.9
diff --git a/lhpc/core/webserver.py b/lhpc/core/webserver.py
index fa4e0656..964678c7 100644
--- a/lhpc/core/webserver.py
+++ b/lhpc/core/webserver.py
@@ -641,16 +641,17 @@ def _stack_deny_locations(s) -> str:
     return "\n".join(out)
 
 
-# One HSTS line for every TLS listener: the console and each stack UI proxy.
+# The HSTS line every TLS listener sends: the console and each stack UI proxy.
 _HSTS = '        add_header Strict-Transport-Security "max-age=31536000" always;\n'
+_HSTS_CLEAR = '        add_header Strict-Transport-Security "max-age=0" always;\n'
 
 
-def _hsts_ok(cfg: WebserverConfig, stack_webs) -> bool:
-    """HSTS binds the whole host, every port: send it only when the console and every enabled
-    stack UI listen on TLS, or a browser that met one listener would refuse a plain-http one on the
-    same host for a year."""
-    return cfg.scheme == "https" and not any(s.swc.enabled and s.swc.scheme == "http"
-                                             for s in stack_webs)
+def _hsts(cfg: WebserverConfig, stack_webs) -> str:
+    """HSTS binds the whole host, every port. With the console and every enabled stack UI on TLS,
+    the policy; with any plain-http listener on the host, `max-age=0`, which a browser obeys on its
+    next https visit by dropping a cached policy (RFC 6797 6.1.1), so none stays refused."""
+    mixed = cfg.scheme == "http" or any(s.swc.enabled and s.swc.scheme == "http" for s in stack_webs)
+    return _HSTS_CLEAR if mixed else _HSTS
 
 
 def _stack_blocks(paths: Paths, cfg: WebserverConfig, stack_webs) -> tuple:
@@ -691,7 +692,7 @@ def _stack_blocks(paths: Paths, cfg: WebserverConfig, stack_webs) -> tuple:
                 # The SAME client CA as the console: one client certificate authenticates everywhere.
                 tls += (f"        ssl_client_certificate {client_ca};\n"
                         f"        ssl_crl {crl};\n")
-            tls += f"        {_verify_client(swc)}\n" + (_HSTS if _hsts_ok(cfg, stack_webs) else "")
+            tls += f"        {_verify_client(swc)}\n" + _hsts(cfg, stack_webs)
         # A self-signed loopback upstream (meshtasticd) cannot be verified and needs no verification.
         proxy_ssl = "            proxy_ssl_verify off;\n" if s.upstream_scheme == "https" else ""
         blocks.append(f"""
@@ -765,7 +766,7 @@ def render_nginx_config(paths: Paths, cfg: WebserverConfig, stack_webs=()) -> st
             f"    ssl_client_certificate {client_ca};\n"
             f"    ssl_crl {crl};\n")
         verify = f"        {_verify_client(cfg)}\n"
-        hsts = _HSTS if _hsts_ok(cfg, stack_webs) else ""
+        hsts = _hsts(cfg, stack_webs)
     ui_prelude, ui_blocks = _stack_blocks(paths, cfg, stack_webs)
     return f"""# Generated by LoRaHAM Pi Control — DO NOT EDIT (regenerated on Apply).
 # Rootless nginx: TLS boundary on {cfg.bind}:{cfg.port}; backend = Waitress unix socket.
diff --git a/tests/web/test_stackweb.py b/tests/web/test_stackweb.py
index e9c9738f..fd476383 100644
--- a/tests/web/test_stackweb.py
+++ b/tests/web/test_stackweb.py
@@ -218,21 +218,23 @@ def test_http_listener_has_no_tls_and_no_mtls():
     assert "ssl_verify_client" not in block
 
 
-# HSTS covers every port of the host: it is sent only when every listener on it is TLS, or a browser
-# that met one of them would refuse a plain-http console or stack UI on the same host for a year.
-@pytest.mark.parametrize("console, schemes, sent", [
-    ("https", ("https",), True),
-    ("https", ("http",), False),            # the console's own HSTS would lock the http stack UI out
-    ("https", ("https", "http"), False),
-    ("http", ("https",), False),            # a TLS stack UI would lock the http console out
+# HSTS covers every port of the host. With every listener on TLS each one sends the policy; with a
+# plain-http listener beside, the TLS ones send max-age=0, which a browser obeys on its next https
+# visit by dropping a cached policy (RFC 6797 6.1.1), so no listener on the host stays refused.
+@pytest.mark.parametrize("console, schemes, sent, cleared", [
+    ("https", ("https",), 2, 0),
+    ("https", ("http",), 0, 1),             # the console clears a policy that would refuse the stack UI
+    ("https", ("https", "http"), 0, 2),
+    ("http", ("https",), 0, 1),             # the TLS stack UI clears a policy that would refuse the console
+    ("http", ("http",), 0, 0),              # no TLS listener: no HSTS header at all
 ])
-def test_hsts_is_sent_only_when_every_listener_on_the_host_is_tls(console, schemes, sent):
+def test_hsts_is_sent_only_when_every_listener_on_the_host_is_tls(console, schemes, sent, cleared):
     conf = webserver.render_nginx_config(
         _paths(), WebserverConfig(scheme=console),
         [_proxy(f"s{i}", mode="lan", port=8444 + i, scheme=s, allowed_cidrs=("10.0.0.0/8",))
          for i, s in enumerate(schemes)])
-    hsts = 'add_header Strict-Transport-Security "max-age=31536000" always;'
-    assert conf.count(hsts) == ((1 + schemes.count("https")) if sent else 0)
+    assert conf.count('add_header Strict-Transport-Security "max-age=31536000" always;') == sent
+    assert conf.count('add_header Strict-Transport-Security "max-age=0" always;') == cleared
 
 
 def test_a_disabled_http_stack_ui_does_not_withhold_hsts():
```

## The analysis rows (ANALYSIS-2-SECURITY-v3.md, sha256 bf5063b6…, verbatim)

| # | severity | area | risk | evidence | impact | fix |
|---|---|---|---|---|---|---|
| L0 | low (defense-in-depth) | session cookie + CSRF across the stack UIs | premise stated: a compromised stack-UI *server* is a child of lhpc-web.service (user unit, started by Popen with no uid change, lifecycle.py:222), so it already has the console's uid and can open `state/run/lhpc-web.sock` (0600), where a missing `X-LHPC-Peer` counts as loopback (app.py:78): the cookie path gives it nothing new. What remains: a reader of the forwarded header without that uid (a stack process under another uid, or a UI that reflects request headers to its page) | The stack UIs are server blocks on the same host (`listen …:{port}`, `server_name _`, webserver.py:668-689). Their proxy strips X-Forwarded-* but not `Cookie` (webserver.py:703-716), so the console's `session` cookie (host-scoped, any port) reaches every stack upstream. That cookie is signed, not encrypted: the `_csrf` token in it is readable. `_csrf_ok` checks only the form token against the session (app.py:259-261), and `SameSite=Lax` (app.py:251) does not separate ports of one site | under that remaining premise, a same-site form from a stack UI's port carries the token and the browser's cert | in `_csrf_ok`, refuse when `Sec-Fetch-Site` is present and not `same-origin`/`none` (an absent header, the CLI or curl, passes as today). Done on `cons/csrf-origin-r1` 3a698f3c for the 0.12.11 hygiene packet |
| L5 | low | graywolf fetch | redirect to http | `curl -fsSL` without `--proto '=https' --proto-redir '=https'` (graywolf-fetch.sh:66/110). The default path pins the sha in the script | downgrade possible in upstream mode only | Add the two curl flags |
| L6 | low | headers | clickjacking/HSTS on stack UIs | The console app sets CSP/nosniff/no-referrer/no-store (app.py:204-215), and nginx adds HSTS on the console (webserver.py:755). Stack blocks get no HSTS. The nginx-generated 403 has HSTS only (probe) | minor | `add_header Strict-Transport-Security … always` in the https stack blocks |
| L7 | low | p12 export route | a revoked label's leftover bundle is fetched | `webserver_cert_download` is loopback-only (app.py:2421, `peer_is_loopback` :66-78) but does not check the label is active (the UI hides it) | encrypted, useless for auth | Refuse unless active in `webserver_cert_export_bytes` |
| L8 | low/design | interactive TCP mode | any local user gets the console | A missing `X-LHPC-Peer` counts as loopback (app.py:78). Production serves a 0600 Unix socket (app.py:2675) | local users only | Document it, or check SO_PEERCRED in TCP mode |

## Commits (full messages, `git log --reverse --format=%B`)

### 34d16d08 sec-lows L6: HSTS only when every listener on the host is TLS, else max-age=0

```text
sec-lows L6: HSTS only when every listener on the host is TLS, else max-age=0

Security analysis 2, L6: nginx sent Strict-Transport-Security on the
console's TLS listener (render_nginx_config) but not on the per-stack UI
proxy blocks (_stack_blocks). And HSTS binds the whole host, every port:
the console's header already made a browser refuse a stack UI served over
plain http (a no-auth stack listener may be http) on the same host, for
a year (max-age=31536000).

One rule, _hsts(): every TLS listener (the console, each stack block, at
server level, which its locations inherit: none sets its own add_header)
sends the policy when the console and every enabled stack UI are https,
and `max-age=0` when any listener on the host is plain http. A browser
obeys max-age=0 on its next https visit by dropping a policy it cached
(RFC 6797 6.1.1), so a box that already locked a UI out heals, and no
new lock-out starts. A default deployment renders byte-identically: it
has no stack blocks. render_nginx_config reads stack_webs twice, so it
takes a tuple of it first.

Operator-visible, one CHANGELOG line.

Red before: test_hsts_is_sent_only_when_every_listener_on_the_host_is_tls
fails on the parent for [https-https] (the stack block has no HSTS) and
for the three mixed cases (the console sends the year-long policy, or
nothing clears it). test_a_disabled_http_stack_ui_does_not_withhold_hsts
(port 0, no listener) passes on the parent and fails without the
`enabled` filter.

```

### 9f30559b sec-lows L7: a revoked certificate's leftover .p12 bundle is not served

```text
sec-lows L7: a revoked certificate's leftover .p12 bundle is not served

Security analysis 2, L7: the loopback-only download route
(/webserver/cert/<label>/download) served any label's export bundle that
was still on disk, a revoked one included; only the page hid it. The
check now lives in pki.read_export, the one reader behind the route and
`lhpc webserver cert export` (via webserver_cert_export_bytes): it
returns None unless the label is an active certificate in
list_client_certs, the same state the copyboxes filter on (revoked and
revocation-pending are not active). The route answers 404, the CLI its
existing "issue or reissue it first". The service docstring's "the CLI
locates the file directly" was not true and is corrected (same lines:
service_webserver.py stays at its 2388-line budget).

Red before: test_a_revoked_labels_leftover_p12_is_not_served fails on
the parent: the leftover bundle is on disk and the download answers 200.

```

### c2c6b0f1 sec-lows L8: without the nginx header the socket's peer decides loopback

```text
sec-lows L8: without the nginx header the socket's peer decides loopback

Security analysis 2, L8: peer_is_loopback() read a missing X-LHPC-Peer
header as loopback "by construction" (the bare interactive TCP mode).
It now asks the socket instead: with no header, only a request whose
remote_addr is 127.0.0.1 or ::1 is loopback. Behind nginx nothing
changes (nginx strips a client copy and always sets the header); the
bare TCP mode keeps its loopback-only actions (the .p12 download) for a
loopback peer, and a peer whose address is not loopback, or unknown, is
refused.

Refusing every request without the header, as first proposed, would
also have refused the bare TCP mode's own loopback browser; checking the
socket closes the gap without that. No CHANGELOG line: production always
serves through nginx, which sets the header.

Red before: test_without_the_nginx_header_only_a_loopback_socket_peer_
is_loopback fails on the parent (a 203.0.113.5 peer without the header
gets the .p12, 200). test_peer_is_loopback_trusts_only_nginx_header
pinned the old rule (no header, no peer address = loopback) and now
states the new one.

```

### a39f38c3 web: refuse a POST the browser marks as from another site or port

```text
web: refuse a POST the browser marks as from another site or port

```

## The report

> # Code report: sec-lows (group B: the security analysis's mechanical lows L0, L6, L7, L8) — v3
>
> Branch `cons/sec-lows-r1` on `origin/dev` = `75e7972f` (local; v1/v2 sat on `0a7fb61d`, the rebase is range-diff `=`; it lands after the 0.12.10 release commit, 0.12.11
> content). Source: ANALYSIS-2-SECURITY-v3.md (sha256 bf5063b6…), rows L0 and L5-L8. The maintainer's decision: group B
> is ONE packet of the mechanical lows plus L0's CSRF defence; the design lows (L2 flash passphrase, L3 signers, L4 pip
> pins) stay parked. L1 (CRL early renewal) is its own branch.
>
> **v3 against v2 (packet sha256 715de38b…, DELTA-1 c99ab9ba…):** gate 1 round 4 (auditor 6b) was RED on one point: v2's
> `_hsts_ok()` stopped a NEW mixed http/https lock-out, but a browser that had already cached the console's
> `max-age=31536000` kept refusing the http listener until that policy expired, and the CHANGELOG overstated it. The
> decision: in a mixed configuration every https listener sends `Strict-Transport-Security: max-age=0`, which a browser
> obeys on its next https visit by dropping the cached policy (RFC 6797 6.1.1). L6 is amended (`_hsts()` returns the line
> every TLS listener sends; the test has five cases), the shared CHANGELOG bullet says "prevents new HSTS lockouts and
> clears an already-cached policy on the next HTTPS visit", and the stack is rebased onto dev `75e7972f`. L7, L8 and L0
> are unchanged in content; every sha moves. DELTA-1's disabled-stack case is kept (its expectation is unchanged).
>
> **v2 against v1 (packet sha256 3e15a953…, stack ending cda87245):** the second read found that L6 widened a lock-out:
> a stack listener may be plain http (a no-auth stack, config.py:825), and HSTS binds the whole host. On dev only the
> console sent HSTS; with v1's L6 an https stack UI sent it too, so a browser that met only that UI was also locked out
> of an http stack UI on the host. The decision: send HSTS (console AND stack blocks) only when every listener on the
> host is TLS. L6 is amended (`_hsts_ok()`, one CHANGELOG line, the test now four cases); this also fixes the HSTS
> mirror finding v1 left for the maintainer. To keep group B at two CHANGELOG bullets, L7 extends L6's "Web console"
> bullet instead of adding its own. L8 and L0 are unchanged (range-diff `=`); new shas throughout.
>
> ## Commits
>
> | commit | low | files | test | red-before (the test on the commit's parent) |
> |---|---|---|---|---|
> | 34d16d08 | L6 HSTS | lhpc/core/webserver.py, tests/web/test_stackweb.py, CHANGELOG.md | `test_hsts_is_sent_only_when_every_listener_on_the_host_is_tls` [console + stacks → policy lines, max-age=0 lines: https+(https) 2, 0; https+(http) 0, 1; https+(https, http) 0, 2; http+(https) 0, 1; http+(http) 0, 0]; `test_a_disabled_http_stack_ui_does_not_withhold_hsts` | 5 FAILED: the four cases with a TLS listener (the parent sends the year-long policy from the console only, and never max-age=0) and the disabled-stack case (the parent's stack block has no HSTS: 1 ≠ 2). http+(http) passes: no TLS listener sends anything on either side. The disabled-stack case also fails without the `enabled` filter (DELTA-1) |
> | 9f30559b | L7 p12 | lhpc/core/pki.py, lhpc/core/service_webserver.py (docstring, same lines), tests/web/test_webserver.py, CHANGELOG.md | `test_a_revoked_labels_leftover_p12_is_not_served` | FAILED: the leftover bundle is on disk and the download answers 200 |
> | c2c6b0f1 | L8 peer | lhpc/adapters/web/app.py, tests/web/test_webserver.py | `test_without_the_nginx_header_only_a_loopback_socket_peer_is_loopback`; `test_peer_is_loopback_trusts_only_nginx_header` (updated) | FAILED `assert 200 == 403` (a 203.0.113.5 peer without the header gets the .p12); the updated test FAILED `assert True is False` (no header, no peer address read as loopback) |
> | a39f38c3 | L0 CSRF | lhpc/adapters/web/app.py, tests/web/test_csrf_origin.py, CHANGELOG.md, docs/architecture.md | `test_a_post_from_another_origin_is_refused_even_with_the_token` [same-site, cross-site] and 4 guards | FAILED [same-site], [cross-site]; 4 passed (same-origin, none, absent header, a GET). A cherry-pick of `cons/csrf-origin-r1` 3a698f3c, applied clean; author makrohard |
>
> Each red-before was run on a clean detached checkout of the commit's parent with only that commit's test file laid
> over it; the checkout was clean again afterwards (`git status --short` empty).
>
> ## What each commit does
>
> - **L6:** `_hsts(cfg, stack_webs)` returns the line every TLS listener sends (the console, and each stack block at
>   server level, which its locations inherit: none sets its own `add_header`): the policy (`max-age=31536000`) when
>   the console and every ENABLED stack UI are https, else `max-age=0`. HSTS binds the whole host, every port: a
>   plain-http listener beside one that sends the policy is refused by a browser for a year; `max-age=0` makes the
>   browser drop a cached policy on its next https visit (RFC 6797 6.1.1), so a box that already locked a UI out heals
>   and no new lock-out starts. A plain-http listener sends no header (a browser ignores HSTS over http). A disabled
>   stack UI (port 0) has no listener and does not count. `render_nginx_config` reads `stack_webs` twice and takes a
>   tuple of it first. A default deployment renders byte-identically: it has no stack blocks.
> - **L7:** `pki.read_export`, the only reader of an export bundle (its one caller, `webserver_cert_export_bytes`,
>   serves the route and `lhpc webserver cert export`), returns None unless the label is `active` in
>   `list_client_certs`, the state the copyboxes filter on (revoked and revocation-pending are not active). In
>   `service_webserver.py` the check would have broken the file's 2388-line budget; there only the docstring changed
>   (same lines), correcting "the CLI locates the file directly", which was not true.
> - **L8:** `peer_is_loopback()`: behind nginx unchanged (the nginx-set `X-LHPC-Peer` decides). Without the header (the
>   bare interactive TCP mode, tests) the socket's `request.remote_addr` decides: only 127.0.0.1 and ::1 are loopback.
>   The analysis offered "document it, or check SO_PEERCRED"; refusing every header-less request would also refuse the
>   bare TCP mode's own loopback browser (its .p12 download). The remote_addr form closes the gap without that. It
>   also removes the premise L0 builds on ("a missing `X-LHPC-Peer` counts as loopback").
> - **L0 (the cons/csrf-origin-r1 commit):** `_csrf_ok` refuses a POST whose `Sec-Fetch-Site` is present and not `same-origin`/`none`
>   (another site, or another port of the box: a stack UI); an absent header (the CLI, curl) passes as before.
>
> ## Operator-visible, and the CHANGELOG
>
> | commit | effect | CHANGELOG (Unreleased, Fixed) |
> |---|---|---|
> | L6 | a box with a stack UI on plain http: its https listeners send `max-age=0` instead of the year-long policy (a browser drops a cached policy on the next https visit, so the http UI is reachable again); all-https boxes: unchanged for the console, and the stack UIs send it too | one "Web console" bullet, shared with L7 (below) |
> | L7 | `lhpc webserver cert export` of a revoked label answers "no export bundle … issue or reissue it first"; the console download 404s | the L6 bullet's second half: "Web console: with a stack web UI on plain http, the https listeners send HSTS `max-age=0`, which prevents new HSTS lockouts and clears an already-cached policy on the next HTTPS visit (all-https boxes keep HSTS, stack UIs too); a revoked certificate's leftover `.p12` is no longer handed out, by the download or `lhpc webserver cert export`." |
> | L8 | none: production always serves through nginx, which sets the header | — |
> | L0 | a form sent from a stack UI's page to the console is refused | "Web console: a form or request the browser marks as sent from another site or from another port of the box (a stack's web UI) is refused even with a valid token." |
>
> ## L5 dropped
>
> L5 proposed `--proto '=https' --proto-redir '=https'` on graywolf-fetch.sh. The default download is already
> integrity-checked: `EXPECTED` comes from the script's pinned table (`sums`, graywolf-fetch.sh:59 on dev), an empty one
> stops the script (:77), and `sha256sum -c` stops on a mismatch before anything is unpacked (:113-114). Only the
> opt-in `--from-upstream` mode trusts the release's own checksums.txt. The script is a build input of graywolf
> (tests/stacks/test_asset_build_inputs.py), so any edit makes every graywolf box read "needs build" once after the
> update (web start and auto-install gate on it). The handler dropped L5 as not worth that. (A drafted form needed
> `=https,file`: the test lab's `GRAYWOLF_BASE_URL` is file://.)
>
> ## The HSTS mirror: fixed here
>
> v1 listed it for the maintainer: an https console sends HSTS for the host, so a stack UI with a plain-http listener
> on the same host was refused by any browser that had seen the console. L6's rule closes it, a policy already cached
> included (above).
>
> ## HOUSE RULES KEPT (tools/check_houserules.py origin/dev..HEAD --fact "is refused even with the token")
>
> ```
> HOUSE RULES KEPT  origin/dev..HEAD
> changelog    +2 bullets (limit 2, each ≤ 3 lines; longest 3), top section 36 lines   OK
> docs         1 files, +3 lines; fact "is refused even with the token" -> docs/architecture.md   OK
> tests        7 new tests; layers tests/web=web; red-before 3/4   WARN
> patterns     getsource 0 assert-True 0 source-read 0 sleep 0 startswith 0 network 0   OK
> markers      0 without slow   OK
> attribution  authors makrohard; trailers 0; words 0   OK
> modules      service_webserver.py 2388->2388 lines (budget 2388), 42->42 public (budget 42); family net +0 lines, +0 public   OK
> VERDICT      PASS  (hard 0, warn 1)
> ```
>
> The WARN: the L0 commit's message names no red-before (cherry-picked unchanged from cons/csrf-origin-r1); it is measured
> above.
>
> ## The 6-point block
>
> 1. **Contracts:** the console's HSTS line, `peer_is_loopback()`, `pki.read_export`, `_csrf_ok`; no signature changes.
> 2. **Invariants:** "a remote peer can never appear loopback" (now also without nginx); only active certificates'
>    bundles leave the box; no TLS listener sends a lasting HSTS policy while the host has a plain-http listener.
> 3. **Failure classes:** a header-less request from a non-loopback or unknown socket peer is not loopback. A same-uid
>    client talking to the 0600 Unix socket directly, without nginx, is one: waitress reports a Unix-socket peer as
>    `localhost` (waitress/server.py:415-416), not an IP. It loses the loopback-only actions and can read the files
>    itself. A cross-port form post is refused.
> 4. **Test rules:** each test drives the route or the renderer; no source reads.
> 5. **Directories:** `pytest -n 12 tests/web tests/cli tests/repo tests/install` → 3508 passed, 1 skipped; `ruff check lhpc testlab`
>    and `ruff check tests --select F,E9` (the CI gates) → clean.
> 6. **Adversarial self-review:**
>    - L6's first form sent HSTS from an https stack block beside an http console (the lock-out); fixed before hand-in.
>    - v1's L6 still widened the mirror lock-out (an https stack UI beside an http one); found by the second read, v2.
>    - Group B's CHANGELOG first came to three bullets (the checker's limit is two, HARD); L6 and L7 share one.
>    - v2's rule did not heal a box that had already cached the year-long policy (gate 1 round 4); v3 sends max-age=0.
>    - Correction to DELTA-1: it said the disabled-stack case "passes on the parent (dev)". That was not run, and it is
>      false: on dev the stack block sends no HSTS, so the count is 1, not 2 (measured now: FAILED). The rest of
>      DELTA-1 was measured and holds.
>    - L7's first form put the check into service_webserver.py: the checker's modules row was HARD (+5 lines over the
>      budget). Moved to pki.py (one caller).
>    - L8 differs from the first proposal ("refuse a missing header"); the reason is above.
>    - L0's doc hunk updates numbered pointers in docs/architecture.md (app.py +5). When this stack is rebased onto the
>      doc-pointers landing (pointers without line numbers), that part of the hunk is empty; its sentence stays.
>    - On this stack the pointer resolver shows only the 3 stale pointers the docs sweep fixes; group B adds none.
>
> ## Numbers, measured
>
> | figure | command | output |
> |---|---|---|
> | base | `git log -1 --format=%h origin/dev` | 75e7972f |
> | diff | `git diff origin/dev --stat` | 9 files, +149 / −16 |
> | tests | `pytest -n 12 tests/web tests/cli tests/repo tests/install` | 3508 passed, 1 skipped |
> | red-before | the four parent runs above | L6 5 failed; L7 1 failed; L8 2 failed; L0 2 failed |

## The full diff (`git diff 01ea7b6ff2ab4c5006702387161c7ee0f40afeec a39f38c32d7e81ede375f3bdc40c8ad497adbb4b`)

```diff
diff --git a/CHANGELOG.md b/CHANGELOG.md
index c5e90b64..ff43137f 100644
--- a/CHANGELOG.md
+++ b/CHANGELOG.md
@@ -13,6 +13,8 @@ Changed
 
 Fixed
 
+- Web console: a form or request the browser marks as sent from another site or from another port of the
+  box (a stack's web UI) is refused even with a valid token.
 - Auto-install of MeshCom from its published binary moves a clean MeshCom run-script checkout to its pin itself
   when an earlier install left it at another one, instead of asking for a source build.
 - MeshCore: LHPC's own transmit limit is gone, which delayed repeats by up to 27 s; in the repeater modes openHop's
@@ -20,7 +22,7 @@ Fixed
 - An `lhpc` command stopped with Ctrl-C prints one line naming `lhpc status` instead of a Python traceback (exit
   status 130, as before).
 - The GPS sky view no longer alternates with "no satellites reported" when gpsd reports the satellites only
-  every few seconds: a read waits up to 6 s for the next list, and the console keeps the last one for up to 30 s.
+  every few seconds: the console keeps each receiver's last satellite list for up to 20 s.
 - MeshCore repeater dashboard: a restart no longer signs everyone out; the login-signing secret is kept in
   `config/secrets/` and is part of `lhpc secrets backup`.
 - MeshCore repeater dashboard: the radio presets list loads (it reported "Presets file not found"); radio settings
@@ -31,6 +33,9 @@ Fixed
   index in the stack's source checkout.
 - The dashboard's and the log pages' background refresh no longer runs git for every installed source; what they
   show is unchanged.
+- Web console: with a stack web UI on plain http, the https listeners send HSTS `max-age=0`, which prevents new HSTS
+  lockouts and clears an already-cached policy on the next HTTPS visit (all-https boxes keep HSTS, stack UIs too); a
+  revoked certificate's leftover `.p12` is no longer handed out, by the download or `lhpc webserver cert export`.
 
 ## 0.12.9
 
diff --git a/docs/architecture.md b/docs/architecture.md
index 4f0ea5cd..24798e41 100644
--- a/docs/architecture.md
+++ b/docs/architecture.md
@@ -289,7 +289,8 @@ leaves on disk and which test proves it: the [invariant table](#invariant-table)
   response carries `Cache-Control: no-store`, `X-Content-Type-Options: nosniff`,
   `Referrer-Policy: no-referrer` and a `Content-Security-Policy` locked to `'self'` (with
   `base-uri`/`frame-ancestors` `'none'`); per-stack config paths stay inside `config/stacks/`.
-  No GET route runs a network or git-remote command. Network exposure is the nginx front end ([serving model](deployment.md#serving-model)).
+  A POST the browser marks as sent from another site or another port of the box (`Sec-Fetch-Site`)
+  is refused even with the token. No GET route runs a network or git-remote command. Network exposure is the nginx front end ([serving model](deployment.md#serving-model)).
 - **Evidence once per request.** A page render reads each piece of evidence once (status
   snapshot, per-(stack, band) config, consumed-source SHAs, firewall status, listeners, git state
   per distinct checkout) through a thread-local request memo dropped at every request start and
@@ -351,7 +352,7 @@ the runtime root unless absolute.
 | binary channel | a failed install, or one interrupted before its commit, is rolled back to the previous one; one interrupted after its commit stays installed and recovery only completes its cleanup (backups, journal) ([operations](operations.md#install-channels)) | `lhpc/core/service_binary_ops.py:97` `binary_install`, `:601` `binary_recover`; `lhpc/core/binary_install.py:556` `rollback_files` | admission, the covered source paths | `state/binary/install.journal.json`; `binary_recover` runs first under the locks: a committed journal is dropped, any other is rolled back (files, receipt, mesh password) | `tests/install/test_binary_install.py::test_rollback_restores_an_interrupted_publish`; `::test_an_open_binary_transaction_is_recovered_before_already_installed_is_said`; `tests/install/test_binary_channel.py::test_retire_recovers_an_interrupted_transaction_first`, `::test_unexpected_error_after_publish_unwinds_everything`, `::test_committed_journal_leaves_the_receipt_authoritative` |
 | binary channel | the receipt is absent, valid, superseded or unsafe; unreadable is never absent ([operations](operations.md#install-channels)) | `lhpc/core/binary_receipt.py:272` `receipt_state` | none (read) | `state/binary/<stack>.json` | `tests/install/test_binary_channel.py::test_malformed_receipt_is_unsafe_never_absent`, `::test_superseded_when_txn_id_differs`, `::test_absent_when_no_receipt` |
 | binary channel | retiring never deletes a file changed since installation ([operations](operations.md#install-channels)) | `lhpc/core/service_binary_ops.py:916` `_retire_body` | the caller's (install switch, uninstall, clean) | the receipt is kept while a file is changed | `tests/install/test_binary_install.py::test_retire_refuses_when_files_changed` |
-| PKI / web | loopback bind, `Host` check, POST + CSRF + confirm, security headers, config paths inside `config/stacks/` ([Web](#safety-model)) | `lhpc/adapters/web/app.py:2519` `run_server`, `:326` `_trusted_host`, `:259` `_csrf_ok`, `:310` `_set_headers`; `lhpc/core/config.py:1999` `_stack_config_path` | none | — | `tests/web/test_web.py::test_run_server_rejects_non_loopback`, `::test_action_requires_csrf`, `::test_dashboard_ok_and_headers`, `::test_config_path_cannot_escape_via_band_or_id`; `tests/web/test_trusted_host.py::test_interactive_console_rejects_rebinding_host` |
+| PKI / web | loopback bind, `Host` check, POST + CSRF + confirm, security headers, config paths inside `config/stacks/` ([Web](#safety-model)) | `lhpc/adapters/web/app.py:2524` `run_server`, `:331` `_trusted_host`, `:259` `_csrf_ok`, `:315` `_set_headers`; `lhpc/core/config.py:1999` `_stack_config_path` | none | — | `tests/web/test_web.py::test_run_server_rejects_non_loopback`, `::test_action_requires_csrf`, `::test_dashboard_ok_and_headers`, `::test_config_path_cannot_escape_via_band_or_id`; `tests/web/test_trusted_host.py::test_interactive_console_rejects_rebinding_host` |
 | PKI / web | no GET route runs a network or git-remote command ([Web](#safety-model)) | no single owner: every GET renders cached state (`lhpc/core/selfupdate.py:741` `status_view` for self-update) | none | — | `tests/web/test_web.py::test_get_routes_make_no_network_calls`, `::test_page_load_is_read_only`; `tests/core/test_controller.py::test_controller_status_makes_no_live_calls` |
 | PKI / web | evidence once per request; every writing entry invalidates the snapshot ([Evidence once per request](#safety-model)) | `lhpc/core/snapshot_memo.py:26` `invalidates_snapshot`; `lhpc/core/services.py:883` `invalidate_snapshot`, `:597` `build_snapshot`; the tables `lhpc/core/snapshot_memo.py:48` `SNAPSHOT_NEUTRAL`, `:291` `SNAPSHOT_NEUTRAL_WRITERS` | none | — | `tests/core/test_snapshot_memo.py::test_every_public_service_entry_is_classified`, `::test_every_traced_write_is_classified`, `::test_no_neutral_writer_path_is_a_snapshot_input` |
 | PKI / web | detached web jobs publish the tracking marker only after admission is released; job markers resist PID reuse ([Detached web jobs](#safety-model)) | `lhpc/core/service_lifecycle_ops.py:3730` `spawn_web_job`, `:4238` `active_jobs`, `:4078` `prune_logs`; `lhpc/core/webjob_gate.py:64` `verify_tracked` | admission across reserve and spawn, released before publishing | `state/jobresults/<log>.json`, `state/jobs/<log>.job`; an untrackable child is terminated or the attempt marked *unsafe* | `tests/web/test_webjob.py::test_spawn_web_job_captures_then_releases_admission_before_publishing`; `tests/core/test_process_ownership.py::test_untracked_job_spawn_is_terminated_not_orphaned`; `tests/core/test_runtime_fs.py::test_job_marker_reused_pid_not_active`, `::test_prune_logs_bounds_count_and_protects_active` |
diff --git a/docs/gps.md b/docs/gps.md
index 3889344c..c3c5cf0d 100644
--- a/docs/gps.md
+++ b/docs/gps.md
@@ -50,9 +50,8 @@ RMC date it reads `12:34:56 (date unavailable)`), satellites used of seen, a **S
 reads; reports are newline-framed in a bounded buffer and a truncated or malformed report is
 dropped, never guessed. Reports are correlated **per device**; an untagged report is attributed
 only when gpsd lists exactly one device. gpsd sends a satellite report only every few seconds, so
-a read that has a position but no satellite report waits up to 6 s for the next one, and the
-console keeps each device's last satellite list for up to 30 s and shows it when a read sees none;
-a single `lhpc gps --monitor` run shows only what its own read (with that wait) saw. The NMEA pane shows
+the console keeps each device's last satellite list for up to 20 s and shows it when a read sees
+none; a single `lhpc gps --monitor` run shows only what its own read saw. The NMEA pane shows
 **gpsd's NMEA output** (pseudo-NMEA for a receiver gpsd runs in binary mode), combined across
 devices. Watching may activate a receiver on a gpsd running without `-n`; LHPC's own time-source
 gpsd runs with `-n`.
diff --git a/lhpc/adapters/web/app.py b/lhpc/adapters/web/app.py
index 1e10ad4b..0685759c 100644
--- a/lhpc/adapters/web/app.py
+++ b/lhpc/adapters/web/app.py
@@ -68,14 +68,14 @@ _LOOPBACK_HOSTS = {"127.0.0.1", "::1"}
 def peer_is_loopback() -> bool:
     """Loopback-vs-remote decision for loopback-only actions (e.g. a new `.p12` download).
 
-    Trusts ONLY the nginx-set `X-LHPC-Peer` header — nginx strips any client-supplied copy
-    and sets it from the real `$remote_addr`. When the header is absent the app is not behind
-    nginx (bare interactive loopback-TCP mode / tests), which is loopback by construction, so
-    treat it as loopback. A REMOTE client always traverses nginx, which always sets the header
-    to `remote`, so a remote peer can never appear loopback here."""
+    Behind nginx, trusts ONLY the nginx-set `X-LHPC-Peer` header — nginx strips any
+    client-supplied copy and sets it from the real `$remote_addr`. When the header is absent the
+    app is not behind nginx (bare interactive TCP mode / tests): the socket's own peer address
+    decides, so the header's absence alone never reads as loopback. A REMOTE client always
+    traverses nginx, which always sets the header to `remote`."""
     from flask import request
     hdr = request.headers.get("X-LHPC-Peer", "")
-    return hdr == "loopback" if hdr else True
+    return hdr == "loopback" if hdr else request.remote_addr in _LOOPBACK_HOSTS
 
 
 def served_via_nginx() -> bool:
@@ -257,6 +257,11 @@ def create_app(service_factory: ServiceFactory | None = None) -> Flask:
         return session["_csrf"]
 
     def _csrf_ok() -> bool:
+        # The stack web UIs are other ports of this host: the browser sends them this console's
+        # (signed, readable) cookie and calls a form from them `same-site`, so the token alone does
+        # not prove this console's page sent it. Without the header (the CLI, curl): token only.
+        if request.headers.get("Sec-Fetch-Site", "same-origin") not in ("same-origin", "none"):
+            return False
         sent = request.form.get("_csrf", "")
         return bool(sent) and _secrets.compare_digest(sent, session.get("_csrf", ""))
 
diff --git a/lhpc/core/gps.py b/lhpc/core/gps.py
index 873ccaa3..05cd653a 100644
--- a/lhpc/core/gps.py
+++ b/lhpc/core/gps.py
@@ -1017,14 +1017,10 @@ def _altitude(tpv: dict) -> tuple[float | None, str | None]:
 
 # The last satellite list of each gpsd device: (host, port, device path) -> (monotonic start of the
 # read that saw it, satellites, sats_used, sats_seen). Stamped at the read's start, so the list is
-# never older than its stamp says. gpsd sends a SKY with satellites only every few seconds (5.0 s
-# measured on the field box, DOP-only SKYs between), so one 2.5 s read can miss it. A read without
-# a list and without a fresh one here reads on for up to `SKY_WAIT_S` (one list period plus margin)
-# for the next list; a list at most `SKY_MEMO_S` old fills the rest (a 2.5 s read every 3 s drifts
-# against the 5 s period and missed it for up to ~27 s in a row). Its own monotonic clock, no host
-# or receiver time; process memory, so a one-shot CLI run starts without it.
-SKY_WAIT_S = 6.0
-SKY_MEMO_S = 30.0
+# never older than its stamp says. gpsd sends a SKY with satellites only every few seconds (5 s
+# measured with an NMEA receiver), so one 2.5 s read can miss it; a list at most `NMEA_STALE_S` old
+# fills that gap. Its own monotonic clock, no host or receiver time; process memory, so a one-shot
+# CLI run starts without it.
 _SKY_MEMO: dict = {}
 
 
@@ -1037,12 +1033,10 @@ def gpsd_snapshot(host: str, port: int, timeout: float = 2.5) -> dict:
     keeps the ambiguity visible (`nmea_ok` false). Early exit only on `DEVICES=[]` or a
     connection failure. Missing optional fields neither refresh nor clear a value, except that
     losing a usable fix clears lat, lon and altitude. A window without a satellite SKY for the
-    resolved device reports that device's last list if it is at most `SKY_MEMO_S` old; without
-    one, the read goes on for up to `SKY_WAIT_S` until a satellite SKY arrives.
+    resolved device reports that device's last list if it is at most `NMEA_STALE_S` old.
     """
     started = time.monotonic()                      # a SKY in this read arrived after this
     deadline = started + max(0.05, timeout)
-    wait_until = started + max(0.05, timeout, SKY_WAIT_S)
     out = {"ok": False, "error": "", "state": "unavailable", "devices": [], "device": None,
            "mode": None, "lat": None, "lon": None, "alt": None, "alt_kind": None,
            "time": None, "time_has_date": False, "sats_used": None, "sats_seen": None,
@@ -1065,32 +1059,7 @@ def gpsd_snapshot(host: str, port: int, timeout: float = 2.5) -> dict:
                                         "sats_used": None, "sats_seen": None,
                                         "satellites": None})
 
-    def lines():
-        """The budget's read, then, when its ONE positioning device (the snapshot's resolved
-        device) has no satellite SKY and none remembered, one more read until `wait_until` that
-        ends with the first message after that device's satellite SKY (gpsd sends the list as a
-        burst of growing SKYs: ending at the first would show part of it), or quietly at an
-        error: the first read's state stands. Another device's list neither skips nor ends it."""
-        yield from _gpsd_lines(host, port, req, deadline)
-        with_pos = [d for d, r in records.items() if r["tpv"]]
-        if len(with_pos) != 1:
-            return                                  # no position, or ambiguous: no list is shown
-        dev = with_pos[0]
-        memo = _SKY_MEMO.get((host, port, dev))
-        if records[dev]["satellites"] is not None \
-                or (memo is not None and time.monotonic() - memo[0] <= SKY_MEMO_S):
-            return
-        for kind, payload in _gpsd_lines(host, port, req, wait_until):
-            if kind == "error":
-                return
-            if records[dev]["satellites"] is not None and not (
-                    kind == "json" and payload.get("class") == "SKY"
-                    and isinstance(payload.get("satellites"), list)
-                    and payload.get("device") in (dev, None)):
-                return                              # the device's burst is over
-            yield kind, payload
-
-    for kind, payload in lines():
+    for kind, payload in _gpsd_lines(host, port, req, deadline):
         if kind == "error":
             out["error"] = payload
             return out
@@ -1199,7 +1168,7 @@ def gpsd_snapshot(host: str, port: int, timeout: float = 2.5) -> dict:
     if r["satellites"] is not None:                 # a satellite SKY in this window, even an empty one
         _SKY_MEMO[key] = (started, [dict(s_) for s_ in r["satellites"]],
                           r["sats_used"], r["sats_seen"])
-    elif key in _SKY_MEMO and time.monotonic() - _SKY_MEMO[key][0] <= SKY_MEMO_S:
+    elif key in _SKY_MEMO and time.monotonic() - _SKY_MEMO[key][0] <= NMEA_STALE_S:
         _at, sats, r["sats_used"], r["sats_seen"] = _SKY_MEMO[key]
         r["satellites"] = [dict(s_) for s_ in sats]
     out["sats_used"], out["sats_seen"] = r["sats_used"], r["sats_seen"]
diff --git a/lhpc/core/pki.py b/lhpc/core/pki.py
index ee78840f..09bd8bc3 100644
--- a/lhpc/core/pki.py
+++ b/lhpc/core/pki.py
@@ -633,9 +633,14 @@ def list_client_certs(paths: Paths) -> list:
 
 
 def read_export(paths: Paths, label: str) -> bytes | None:
-    """Return the raw `.p12` export bytes for a label, or None if absent/unsafe. The caller
-    (web route) is responsible for the loopback-only gate before serving these bytes."""
+    """Return the raw `.p12` export bytes for an ACTIVE label, or None if absent/unsafe. A revoked
+    or revocation-pending certificate's leftover bundle is not handed out: the copyboxes list
+    only active labels, and this is the one reader behind the download and the CLI export. The
+    caller (web route) is responsible for the loopback-only gate before serving these bytes."""
     label = validators.path_component(label, field="cert label")
+    if not any(c.get("label") == label and c.get("state") == "active"
+               for c in list_client_certs(paths)):
+        return None
     from . import runtime_fs
     try:
         return runtime_fs.read_bytes(paths, _p(paths, _EXPORTS, f"{label}.p12"))
diff --git a/lhpc/core/service_webserver.py b/lhpc/core/service_webserver.py
index 853aca3c..c5e0c892 100644
--- a/lhpc/core/service_webserver.py
+++ b/lhpc/core/service_webserver.py
@@ -1895,8 +1895,8 @@ class WebserverOpsMixin:
             return None
 
     def webserver_cert_export_bytes(self, label) -> bytes | None:
-        """Raw `.p12` bytes for a label (or None). The WEB route must gate this on a
-        loopback-origin session; the CLI locates the file directly."""
+        """Raw `.p12` bytes for an ACTIVE label (or None). The WEB route must gate this on a
+        loopback peer; `lhpc webserver cert export` writes the same bytes to a file."""
         from . import pki as _pki
         return _pki.read_export(self._paths, label)
 
diff --git a/lhpc/core/webserver.py b/lhpc/core/webserver.py
index 89e10e3f..964678c7 100644
--- a/lhpc/core/webserver.py
+++ b/lhpc/core/webserver.py
@@ -641,6 +641,19 @@ def _stack_deny_locations(s) -> str:
     return "\n".join(out)
 
 
+# The HSTS line every TLS listener sends: the console and each stack UI proxy.
+_HSTS = '        add_header Strict-Transport-Security "max-age=31536000" always;\n'
+_HSTS_CLEAR = '        add_header Strict-Transport-Security "max-age=0" always;\n'
+
+
+def _hsts(cfg: WebserverConfig, stack_webs) -> str:
+    """HSTS binds the whole host, every port. With the console and every enabled stack UI on TLS,
+    the policy; with any plain-http listener on the host, `max-age=0`, which a browser obeys on its
+    next https visit by dropping a cached policy (RFC 6797 6.1.1), so none stays refused."""
+    mixed = cfg.scheme == "http" or any(s.swc.enabled and s.swc.scheme == "http" for s in stack_webs)
+    return _HSTS_CLEAR if mixed else _HSTS
+
+
 def _stack_blocks(paths: Paths, cfg: WebserverConfig, stack_webs) -> tuple:
     """(http_prelude, server_blocks) for the per-stack web-UI proxies. ('', '') when none are
     enabled — which is what keeps a default deployment's config byte-identical."""
@@ -679,7 +692,7 @@ def _stack_blocks(paths: Paths, cfg: WebserverConfig, stack_webs) -> tuple:
                 # The SAME client CA as the console: one client certificate authenticates everywhere.
                 tls += (f"        ssl_client_certificate {client_ca};\n"
                         f"        ssl_crl {crl};\n")
-            tls += f"        {_verify_client(swc)}\n"
+            tls += f"        {_verify_client(swc)}\n" + _hsts(cfg, stack_webs)
         # A self-signed loopback upstream (meshtasticd) cannot be verified and needs no verification.
         proxy_ssl = "            proxy_ssl_verify off;\n" if s.upstream_scheme == "https" else ""
         blocks.append(f"""
@@ -730,6 +743,7 @@ def render_nginx_config(paths: Paths, cfg: WebserverConfig, stack_webs=()) -> st
     `stack_webs` is a sequence of `StackWebProxy` (per-stack web-UI reverse proxies). It defaults to
     empty, and an empty set renders BYTE-IDENTICALLY to the console-only config — the websocket `map`,
     the upstreams and the extra server blocks all appear only when a stack is actually proxied."""
+    stack_webs = tuple(stack_webs)                  # read twice: the HSTS rule and the blocks
     sock = _abs(paths, WAITRESS_SOCK)
     server_crt = _abs(paths, ("config", "tls", "server", "server.crt"))
     server_key = _abs(paths, ("config", "tls", "server", "server.key"))
@@ -752,7 +766,7 @@ def render_nginx_config(paths: Paths, cfg: WebserverConfig, stack_webs=()) -> st
             f"    ssl_client_certificate {client_ca};\n"
             f"    ssl_crl {crl};\n")
         verify = f"        {_verify_client(cfg)}\n"
-        hsts = '        add_header Strict-Transport-Security "max-age=31536000" always;\n'
+        hsts = _hsts(cfg, stack_webs)
     ui_prelude, ui_blocks = _stack_blocks(paths, cfg, stack_webs)
     return f"""# Generated by LoRaHAM Pi Control — DO NOT EDIT (regenerated on Apply).
 # Rootless nginx: TLS boundary on {cfg.bind}:{cfg.port}; backend = Waitress unix socket.
diff --git a/tests/stacks/conftest.py b/tests/stacks/conftest.py
index 4d80c41b..26822061 100644
--- a/tests/stacks/conftest.py
+++ b/tests/stacks/conftest.py
@@ -67,9 +67,6 @@ class _FakeGpsd:
                     return
                 if self.json_lines is not None:
                     for chunk in self.json_lines:
-                        if isinstance(chunk, float):     # a pause in the stream, in seconds
-                            time.sleep(chunk)
-                            continue
                         try:
                             conn.sendall(chunk)
                         except OSError:
@@ -109,12 +106,10 @@ class _FakeGpsd:
 
 
 @pytest.fixture(autouse=True)
-def _no_satellite_memo(monkeypatch):
+def _no_satellite_memo():
     """Isolation: `gps.gpsd_snapshot` remembers each gpsd device's last satellite list in the
-    process; every test starts and ends without one. Its wait for a satellite SKY is off here, so
-    a read ends at its budget; the tests of that wait set `SKY_WAIT_S` themselves."""
+    process; every test starts and ends without one."""
     from lhpc.core import gps
-    monkeypatch.setattr(gps, "SKY_WAIT_S", 0.0)
     gps._SKY_MEMO.clear()
     yield
     gps._SKY_MEMO.clear()
diff --git a/tests/stacks/test_gps_monitor.py b/tests/stacks/test_gps_monitor.py
index 008e76ee..ebc0fa4f 100644
--- a/tests/stacks/test_gps_monitor.py
+++ b/tests/stacks/test_gps_monitor.py
@@ -130,7 +130,7 @@ def test_a_dop_only_report_does_not_replace_the_last_list(fake_gpsd):
 
 @pytest.mark.slow                   # timed wait (M0: most of its time is a real wait)
 def test_a_list_older_than_the_stale_bound_is_not_used(fake_gpsd, monkeypatch):
-    monkeypatch.setattr(_gps, "SKY_MEMO_S", 0.0)
+    monkeypatch.setattr(_gps, "NMEA_STALE_S", 0.0)
     r = _two_polls(fake_gpsd, [_sky([_sat(3)])], [])
     assert r["satellites"] == [] and r["sats_seen"] is None
 
@@ -140,7 +140,7 @@ def test_the_age_bound_counts_from_the_start_of_the_read_that_saw_the_list(fake_
     # Each poll reads for its whole 1.2 s budget. The list may have arrived at the first read's
     # start, so at the second read's end it can be ~2.4 s old: a 1.5 s bound must refuse it (a stamp
     # taken at the first read's END would make it look 1.2 s old and serve it).
-    monkeypatch.setattr(_gps, "SKY_MEMO_S", 1.5)
+    monkeypatch.setattr(_gps, "NMEA_STALE_S", 1.5)
     r = _two_polls(fake_gpsd, [_sky([_sat(3)])], [])
     assert r["satellites"] == [] and r["sats_seen"] is None
 
diff --git a/tests/stacks/test_gps_sky_wait.py b/tests/stacks/test_gps_sky_wait.py
deleted file mode 100644
index b8cf4734..00000000
--- a/tests/stacks/test_gps_sky_wait.py
+++ /dev/null
@@ -1,256 +0,0 @@
-"""The GPS Monitor's satellite list against gpsd's real cadence (field row D2).
-
-gpsd sends a SKY every second but carries the satellite list in only every fifth one (5.0 s,
-measured on the field box; the others are DOP-only). The console reads 2.5 s, then polls again 3 s
-after the answer; that cycle drifts against the 5 s period and missed the list for runs of up to
-~27 s, past the 20 s the memo then covered (5 of 20 polls empty on the box). A read without a list
-now waits for the next one, bounded, and a list is remembered for 30 s.
-"""
-
-import json
-import socket
-import threading
-import time
-
-import pytest
-
-from lhpc.core import gps as _gps
-
-VERSION = b'{"class":"VERSION","release":"3.25","proto_major":3,"proto_minor":15}\n'
-DEV_A = b'{"class":"DEVICES","devices":[{"path":"/dev/ttyACM0","driver":"u-blox"}]}\n'
-
-
-def _line(obj) -> bytes:
-    return (json.dumps(obj) + "\n").encode()
-
-
-A, B = "/dev/ttyACM0", "/dev/ttyACM1"
-DEV_AB = _line({"class": "DEVICES", "devices": [{"path": A}, {"path": B}]})
-
-
-def _tpv(device=A) -> bytes:
-    return _line({"class": "TPV", "device": device, "mode": 3, "lat": 51.5, "lon": -0.12})
-
-
-def _dop_sky(device=A) -> bytes:
-    return _line({"class": "SKY", "device": device, "hdop": 0.9, "uSat": 27})
-
-
-def _sky(*prns, device=A) -> bytes:
-    return _line({"class": "SKY", "device": device, "nSat": len(prns), "uSat": len(prns),
-                  "satellites": [{"PRN": p, "el": 30, "az": 90, "ss": 30, "used": True}
-                                 for p in prns]})
-
-
-# --- the bounded wait ----------------------------------------------------------------------------
-# The wait is a second connection to gpsd, so these fakes serve every connection at once, each its
-# own script (the shared fake serves one connection after the other).
-
-class _ScriptGpsd:
-    """Connection i gets scripts[i] (the last one for any later connection): bytes are sent, a
-    float is a pause in seconds; then the connection stays open until the client closes it."""
-
-    def __init__(self, *scripts):
-        self.scripts = scripts
-        self.n = 0
-        self._srv = socket.socket()
-        self._srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
-        self._srv.bind(("127.0.0.1", 0))
-        self._srv.listen(8)
-        self.port = self._srv.getsockname()[1]
-        self._stop = threading.Event()
-        threading.Thread(target=self._accept, daemon=True).start()
-
-    def _accept(self):
-        while not self._stop.is_set():
-            try:
-                self._srv.settimeout(0.2)
-                conn, _ = self._srv.accept()
-            except OSError:
-                continue
-            script = self.scripts[min(self.n, len(self.scripts) - 1)]
-            self.n += 1
-            threading.Thread(target=self._serve, args=(conn, script), daemon=True).start()
-
-    def _serve(self, conn, script):
-        with conn:
-            try:
-                conn.settimeout(1.0)
-                conn.recv(4096)
-                for chunk in script:
-                    if isinstance(chunk, float):
-                        time.sleep(chunk)
-                    else:
-                        conn.sendall(chunk)
-                conn.settimeout(0.2)
-                while not self._stop.is_set():
-                    try:
-                        if not conn.recv(4096):
-                            return
-                    except TimeoutError:
-                        continue
-            except OSError:
-                return
-
-    def close(self):
-        self._stop.set()
-        self._srv.close()
-
-
-@pytest.fixture
-def script_gpsd():
-    made = []
-
-    def make(*scripts):
-        made.append(_ScriptGpsd(*scripts))
-        return made[-1]
-    yield make
-    for srv in made:
-        srv.close()
-
-
-@pytest.mark.slow                   # timed wait (M0: most of its time is a real wait)
-def test_a_list_arriving_after_the_budget_is_waited_for(script_gpsd, monkeypatch):
-    monkeypatch.setattr(_gps, "SKY_WAIT_S", 1.5)
-    srv = script_gpsd([VERSION, DEV_A, _tpv(), _dop_sky()],
-                      [VERSION, DEV_A, _tpv(), _dop_sky(), 0.4, _sky(3, 7), _tpv()])
-    r = _gps.gpsd_snapshot("127.0.0.1", srv.port, timeout=0.3)
-    assert r["state"] == "3d" and [s["prn"] for s in r["satellites"]] == [3, 7]
-    assert r["sats_seen"] == 2
-
-
-@pytest.mark.slow                   # timed wait (M0: most of its time is a real wait)
-def test_the_wait_takes_the_whole_burst_of_a_list(script_gpsd, monkeypatch):
-    # gpsd sends one list as several SKYs in a row, each with more satellites (16, 26, 29, 46, 50
-    # on the field box); the wait ends with the message after the burst, not at its first SKY.
-    monkeypatch.setattr(_gps, "SKY_WAIT_S", 1.5)
-    srv = script_gpsd([VERSION, DEV_A, _tpv(), _dop_sky()],
-                      [VERSION, DEV_A, _tpv(), _dop_sky(), 0.4,
-                       _sky(3), _sky(3, 7), _sky(3, 7, 9), _tpv()])
-    r = _gps.gpsd_snapshot("127.0.0.1", srv.port, timeout=0.3)
-    assert [s["prn"] for s in r["satellites"]] == [3, 7, 9] and r["sats_seen"] == 3
-
-
-@pytest.mark.slow                   # timed wait (M0: most of its time is a real wait)
-def test_without_a_list_in_the_wait_the_read_ends_at_the_bound(fake_gpsd, monkeypatch):
-    monkeypatch.setattr(_gps, "SKY_WAIT_S", 0.8)
-    srv = fake_gpsd(json_lines=[VERSION, DEV_A, _tpv(), _dop_sky()])
-    t0 = time.monotonic()
-    r = _gps.gpsd_snapshot("127.0.0.1", srv.port, timeout=0.3)
-    took = time.monotonic() - t0
-    assert r["state"] == "3d" and r["satellites"] == [] and r["sats_seen"] is None
-    assert 0.7 <= took < 1.5
-
-
-@pytest.mark.slow                   # timed wait (M0: most of its time is a real wait)
-def test_a_remembered_list_ends_the_read_at_its_budget(fake_gpsd, monkeypatch):
-    monkeypatch.setattr(_gps, "SKY_WAIT_S", 3.0)
-    srv = fake_gpsd(json_lines=[VERSION, DEV_A, _tpv(), _sky(5)])
-    _gps.gpsd_snapshot("127.0.0.1", srv.port, timeout=0.3)
-    srv.json_lines = [VERSION, DEV_A, _tpv(), _dop_sky()]
-    t0 = time.monotonic()
-    r = _gps.gpsd_snapshot("127.0.0.1", srv.port, timeout=0.3)
-    assert time.monotonic() - t0 < 1.0 and [s["prn"] for s in r["satellites"]] == [5]
-
-
-# Two receivers on one gpsd: only the device whose position the snapshot shows decides the wait.
-
-@pytest.mark.slow                   # timed wait (M0: most of its time is a real wait)
-def test_another_devices_fresh_list_does_not_skip_the_wait(script_gpsd, monkeypatch):
-    monkeypatch.setattr(_gps, "SKY_WAIT_S", 1.5)
-    srv = script_gpsd([VERSION, DEV_AB, _tpv(A), _sky(3, 7, device=A), _tpv(A)],   # A remembered
-                      [VERSION, DEV_AB, _tpv(B), _dop_sky(B)],
-                      [VERSION, DEV_AB, _tpv(B), _dop_sky(B), 0.4, _sky(5, device=B), _tpv(B)])
-    first = _gps.gpsd_snapshot("127.0.0.1", srv.port, timeout=0.3)
-    assert first["device"] == A and [s["prn"] for s in first["satellites"]] == [3, 7]
-    r = _gps.gpsd_snapshot("127.0.0.1", srv.port, timeout=0.3)
-    assert r["device"] == B and [s["prn"] for s in r["satellites"]] == [5]
-
-
-@pytest.mark.slow                   # timed wait (M0: most of its time is a real wait)
-def test_another_devices_list_does_not_end_the_wait(script_gpsd, monkeypatch):
-    monkeypatch.setattr(_gps, "SKY_WAIT_S", 1.5)
-    srv = script_gpsd([VERSION, DEV_AB, _tpv(B), _dop_sky(B)],
-                      [VERSION, DEV_AB, _tpv(B), 0.3, _sky(3, device=A), _tpv(B), 0.3,
-                       _sky(5, device=B), _tpv(B)])
-    r = _gps.gpsd_snapshot("127.0.0.1", srv.port, timeout=0.3)
-    assert r["device"] == B and [s["prn"] for s in r["satellites"]] == [5]
-
-
-def test_no_wait_without_a_position(fake_gpsd, monkeypatch):
-    monkeypatch.setattr(_gps, "SKY_WAIT_S", 3.0)
-    srv = fake_gpsd(json_lines=[VERSION, DEV_A, _dop_sky()])
-    t0 = time.monotonic()
-    r = _gps.gpsd_snapshot("127.0.0.1", srv.port, timeout=0.3)
-    assert r["state"] == "gpsd-no-data" and time.monotonic() - t0 < 1.0
-
-
-def test_the_memo_covers_the_measured_worst_miss(monkeypatch):
-    # ~27 s measured (a 5.5 s read cycle against the 5.0 s list period); the wait covers a period.
-    monkeypatch.undo()              # the shipped values, not the isolation fixture's
-    assert _gps.SKY_MEMO_S >= 30.0 and _gps.SKY_WAIT_S >= 6.0
-
-
-# --- the field cadence, scaled -------------------------------------------------------------------
-
-class _CadenceGpsd:
-    """gpsd at the measured cadence on one shared clock: every `period` a TPV and a DOP-only SKY,
-    every fifth period the satellite SKY. The clock runs independently of client connections, as
-    gpsd's receiver does."""
-
-    def __init__(self, period):
-        self.period = period
-        self.t0 = time.monotonic()
-        self._srv = socket.socket()
-        self._srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
-        self._srv.bind(("127.0.0.1", 0))
-        self._srv.listen(8)
-        self.port = self._srv.getsockname()[1]
-        self._stop = threading.Event()
-        threading.Thread(target=self._accept, daemon=True).start()
-
-    def _accept(self):
-        while not self._stop.is_set():
-            try:
-                self._srv.settimeout(0.2)
-                conn, _ = self._srv.accept()
-            except OSError:
-                continue
-            threading.Thread(target=self._serve, args=(conn,), daemon=True).start()
-
-    def _serve(self, conn):
-        with conn:
-            try:
-                conn.settimeout(1.0)
-                conn.recv(4096)
-                conn.sendall(VERSION + DEV_A)
-                k = int((time.monotonic() - self.t0) / self.period) + 1
-                while not self._stop.is_set():
-                    delay = self.t0 + k * self.period - time.monotonic()
-                    if delay > 0:
-                        time.sleep(delay)
-                    conn.sendall(_tpv() + _dop_sky() + (_sky(1, 2, 3) if k % 5 == 0 else b""))
-                    k += 1
-            except OSError:
-                return
-
-    def close(self):
-        self._stop.set()
-        self._srv.close()
-
-
-@pytest.mark.slow                   # ~25 s of real time: the field cadence at a fifth of its speed
-def test_the_console_poll_never_shows_an_empty_sky_at_the_field_cadence(monkeypatch):
-    scale = 0.2                     # every duration of the field row, times this
-    monkeypatch.setattr(_gps, "SKY_WAIT_S", 6.0 * scale)
-    monkeypatch.setattr(_gps, "SKY_MEMO_S", 30.0 * scale)
-    srv = _CadenceGpsd(period=1.0 * scale)
-    try:
-        counts = []
-        for _ in range(20):        # the row: 20 polls, each 3 s after the previous answer
-            r = _gps.gpsd_snapshot("127.0.0.1", srv.port, timeout=2.5 * scale)
-            counts.append(len(r["satellites"]))
-            time.sleep(3.0 * scale)
-    finally:
-        srv.close()
-    assert counts.count(0) == 0, counts
diff --git a/tests/web/test_csrf_origin.py b/tests/web/test_csrf_origin.py
new file mode 100644
index 00000000..9e156039
--- /dev/null
+++ b/tests/web/test_csrf_origin.py
@@ -0,0 +1,45 @@
+"""A state-changing POST that a browser marks as coming from another origin is refused, token or not.
+
+The stack web UIs are served on other ports of the same host: the browser treats them as the same
+SITE, sends the console's cookie along (SameSite=Lax does not separate ports), and the signed cookie
+is readable, so the token alone does not prove the console's own page sent the form. The browser's
+`Sec-Fetch-Site` does. A request without it (the CLI, curl with a cert) is judged by the token alone,
+as before.
+"""
+from __future__ import annotations
+
+import pytest
+
+pytestmark = pytest.mark.contract
+
+
+def _post(c, token, headers):
+    return c.post("/action", data={"_csrf": token, "op": "start", "target": "kiss"},
+                  headers=headers)
+
+
+@pytest.mark.parametrize("site", ["same-site", "cross-site"])
+def test_a_post_from_another_origin_is_refused_even_with_the_token(web, csrf, site):
+    # same-site: a stack UI on another port of this host; cross-site: any other page.
+    c = web()
+    token = csrf(c)
+    assert token
+    assert _post(c, token, {"Sec-Fetch-Site": site}).status_code == 400
+
+
+@pytest.mark.parametrize("headers", [
+    {"Sec-Fetch-Site": "same-origin"},   # the console's own form or fetch
+    {"Sec-Fetch-Site": "none"},          # user-initiated
+    {},                                  # the CLI / curl with a cert: no browser metadata
+], ids=["same-origin", "user-initiated", "no-browser-metadata"])
+def test_a_post_from_the_console_itself_still_works(web, csrf, headers):
+    c = web()
+    token = csrf(c)
+    assert _post(c, token, headers).status_code == 302
+
+
+def test_the_origin_check_never_replaces_the_token(web):
+    c = web()
+    r = c.post("/action", data={"op": "start", "target": "kiss"},
+               headers={"Sec-Fetch-Site": "same-origin"})
+    assert r.status_code == 400
diff --git a/tests/web/test_stackweb.py b/tests/web/test_stackweb.py
index 3c9bdec9..fd476383 100644
--- a/tests/web/test_stackweb.py
+++ b/tests/web/test_stackweb.py
@@ -218,6 +218,33 @@ def test_http_listener_has_no_tls_and_no_mtls():
     assert "ssl_verify_client" not in block
 
 
+# HSTS covers every port of the host. With every listener on TLS each one sends the policy; with a
+# plain-http listener beside, the TLS ones send max-age=0, which a browser obeys on its next https
+# visit by dropping a cached policy (RFC 6797 6.1.1), so no listener on the host stays refused.
+@pytest.mark.parametrize("console, schemes, sent, cleared", [
+    ("https", ("https",), 2, 0),
+    ("https", ("http",), 0, 1),             # the console clears a policy that would refuse the stack UI
+    ("https", ("https", "http"), 0, 2),
+    ("http", ("https",), 0, 1),             # the TLS stack UI clears a policy that would refuse the console
+    ("http", ("http",), 0, 0),              # no TLS listener: no HSTS header at all
+])
+def test_hsts_is_sent_only_when_every_listener_on_the_host_is_tls(console, schemes, sent, cleared):
+    conf = webserver.render_nginx_config(
+        _paths(), WebserverConfig(scheme=console),
+        [_proxy(f"s{i}", mode="lan", port=8444 + i, scheme=s, allowed_cidrs=("10.0.0.0/8",))
+         for i, s in enumerate(schemes)])
+    assert conf.count('add_header Strict-Transport-Security "max-age=31536000" always;') == sent
+    assert conf.count('add_header Strict-Transport-Security "max-age=0" always;') == cleared
+
+
+def test_a_disabled_http_stack_ui_does_not_withhold_hsts():
+    # A disabled stack UI (port 0) has no listener, so it cannot be locked out.
+    conf = webserver.render_nginx_config(_paths(), WebserverConfig(scheme="https"), [
+        _proxy("on", mode="lan", port=8444, scheme="https", allowed_cidrs=("10.0.0.0/8",)),
+        _proxy("off", mode="lan", port=0, scheme="http", allowed_cidrs=("10.0.0.0/8",))])
+    assert conf.count('add_header Strict-Transport-Security "max-age=31536000" always;') == 2
+
+
 def test_nginx_token_sanitizes_ids_unusable_as_nginx_variables():
     assert webserver.nginx_token("mesh-com") == "mesh_com"
     assert webserver.nginx_token("a.b@c") == "a_b_c"
diff --git a/tests/web/test_webserver.py b/tests/web/test_webserver.py
index 6fa9ce1d..e9d8ba88 100644
--- a/tests/web/test_webserver.py
+++ b/tests/web/test_webserver.py
@@ -1642,6 +1642,35 @@ def test_p12_download_is_loopback_only(tmp_path, web, csrf):
     assert r.headers["Content-Disposition"].endswith('laptop.p12"')
 
 
+@pytest.mark.contract
+@pytest.mark.safety("exposure-fail-closed")
+def test_without_the_nginx_header_only_a_loopback_socket_peer_is_loopback(tmp_path, web, csrf):
+    # Bare TCP mode (no nginx, no X-LHPC-Peer): the socket's own peer address decides, never
+    # the header's absence alone.
+    c, svc = _app_svc(web, tmp_path)
+    tok = csrf(c)
+    assert svc.webserver_init().ok
+    c.post("/webserver/cert", data={"_csrf": tok, "op": "issue", "label": "laptop"})
+    far = c.get("/webserver/cert/laptop/download", environ_base={"REMOTE_ADDR": "203.0.113.5"})
+    assert far.status_code == 403
+    for near in ("127.0.0.1", "::1"):
+        assert c.get("/webserver/cert/laptop/download",
+                     environ_base={"REMOTE_ADDR": near}).status_code == 200
+
+
+@pytest.mark.contract
+@pytest.mark.safety("exposure-fail-closed")
+def test_a_revoked_labels_leftover_p12_is_not_served(tmp_path, web, csrf):
+    c, svc = _app_svc(web, tmp_path)
+    tok = csrf(c)
+    assert svc.webserver_init().ok
+    c.post("/webserver/cert", data={"_csrf": tok, "op": "issue", "label": "laptop"})
+    svc.webserver_cert_revoke("laptop", accept_unverified=True)
+    assert (svc._paths.runtime_root / "config/tls/exports/laptop.p12").exists()   # the leftover
+    assert c.get("/webserver/cert/laptop/download").status_code == 404
+    assert svc.webserver_cert_export_bytes("laptop") is None
+
+
 def test_webserver_reachable_from_stacks_and_not_a_managed_stack(tmp_path, web):
     # Reachable inline under the controller row on /stacks; the controller id is NOT a managed
     # stack (a bogus stack-detail 404s; it never enters build_snapshot).
@@ -1681,9 +1710,11 @@ def test_peer_is_loopback_trusts_only_nginx_header(tmp_path):
         assert webapp.peer_is_loopback() is False
     with app.test_request_context(headers={"X-LHPC-Peer": "loopback"}):
         assert webapp.peer_is_loopback() is True
-    # A client-supplied spoof cannot help: only the nginx-set value is read; absent => not remote.
-    with app.test_request_context():
+    # No header (bare TCP mode): the socket's peer decides; an unknown peer is not loopback.
+    with app.test_request_context(environ_base={"REMOTE_ADDR": "127.0.0.1"}):
         assert webapp.peer_is_loopback() is True
+    with app.test_request_context():
+        assert webapp.peer_is_loopback() is False
 
 
 def _render(tmp_path, **kw):
```
