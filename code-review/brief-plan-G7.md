# CLOUD BRIEF · PLAN for fix group G7 of loraham-pi-control (one Claude Code cloud session, read-only on the code)

You write the PLAN for a group of verified defects; you change no code. Output = ONE commit on this routine's branch
adding `plans/PLAN-G7.md`. Never touch `main` or `dev`, no pull request. (Attribution lines are stripped
downstream; the file is copied out.) Base: origin/main = e5187f70ae4e81a1081a835be06f84746222c3b6 (v0.11.10). Read `docs/architecture.md` (the safety
invariants), `docs/README.md`, `tests/README.md` (the house rules of the tests) and `docs/maintenance.md` (how a patch
is cut) first.

## The maintainer's rules for every change
Simple, robust, maintainable: the SIMPLEST change that removes the defect; no new mechanism unless the defect cannot be
removed without one (then say so and size it); no behaviour change beyond the defect; a regression test per change that
is RED before and GREEN after, placed where the maintainer would look for it (the existing test module of that file);
docs updated where a sentence becomes untrue (one place per fact); the CHANGELOG line in the operator's words.

## The findings of this group (verified; file:line on e5187f70ae4e81a1081a835be06f84746222c3b6)
### CR5-1
- where: `lhpc/core/pki.py:224 (`_load_index`), :659-662 (`build_crl`), :476/:492 (`issue_client_cert`)` · severity kept S2: active certificates silently stop being revocable (needs a damaged index)
- claim: `_load_index` is a "fail-safe read … never a crash"; every issued client certificate stays revocable by label (`docs/webserver.md` Revocation).
- defect: When the index is malformed or unreadable, `_load_index` returns an EMPTY index. The writers then save it back: `build_crl` (the watchdog's periodic CRL refresh) and `issue_client_cert` overwrite `client-index.json`. Every recorded certificate is gone. The CRL keeps the old revocations, but a certificate that is still ACTIVE can no longer be revoked: `revoke` answers "no active certificate labelled …", and the certificate stays valid until it expires (default 825 d). The only other way out is a CA re-init.
- how to see it: Reproduced: init both CAs → `issue_client_cert(phone)` → truncate `client-index.json` → `build_crl()`. The index is now `{"certs": []}` and `revoke_client_cert(phone)` raises "no active certificate". A test that would be red: "build_crl refuses (does not rewrite) a present-but-malformed index".
- verifier: CONFIRMED — pki.py:222-237 turns a malformed index into an empty one; :659-664 and :476-492 save it back. Scratch: issue `phone`, truncate the index, `build_crl()`: the index becomes `{"certs": []}` (crl_number also drops to 1) and `revoke_client_cert` raises "no active certificate".

### CR5-2
- where: `lhpc/core/service_webserver.py:1639-1641, 1668 (`webserver_reset_defaults`)` · severity kept S2: "reset" leaves the console and proxies exposed, with no way out offered
- claim: Docstring: "Reset to safe defaults AND prove remote exposure has ceased". docs/webserver.md: after `reset-defaults`, "`verify` then proves the remote listener is gone".
- defect: Reset only does `nginx -s reload`, never a restart. The module's own comment (:2046-2048, `_webserver_apply_after_gate`) says a reload cannot move a held listener from 0.0.0.0:P to 127.0.0.1:P: the master keeps the OLD config. That old config includes every stack proxy reset just disabled. So in the main case (console exposed on 0.0.0.0:8443, reset to 127.0.0.1:8443), reset always ends with "STILL bound remotely — cessation UNPROVEN". The console and the proxies stay exposed. The result names no way out (no `next_commands`). The way out is `lhpc webserver apply`, which does restart, because the applied snapshot still shows the flip.
- how to see it: Expose the console (bind 0.0.0.0, port 8443), apply, then `lhpc webserver reset-defaults` on real nginx. The listener stays on 0.0.0.0:8443 and the result is not ok. `test_reset_refuses_cessation_while_the_console_listener_stays_exposed` covers only the honesty of the result, not the cessation.
- verifier: CONFIRMED (code only; no real nginx) — service_webserver.py:1643 only reloads. The module's own comments (:2046-2050, webserver.py:962-964, :1305-1307) say a reload cannot rebind a held 0.0.0.0:P to 127.0.0.1:P. The not-ok branch :1668-1669 has no `next_commands`. tests/web/test_stackweb.py:1290 asserts only honesty.

### CR5-4
- where: `lhpc/core/pki.py:169, :182, :226, :256 (`_read_cert`/`_read_key`/`_load_index`/`_load_pending`), reached from :794 `server_cert_names`, :746 `pki_status`, :813 `server_key_state` · severity kept S3: needs a symlinked or non-directory `config/tls/server`
- claim: `server_cert_names`: ("unreadable", reason) "when `_read_cert` raises (malformed or unsafe)". `pki_status` is a READ-ONLY, never-failing status. The service turns "unreadable" into a typed refusal ("the way out: tls-renew").
- defect: `runtime_fs.read_text_regular` raises `PathContainmentError` (a `ValueError`, not an `OSError`) for a symlinked or non-directory parent. `_read_cert`/`_read_key` catch only `OSError`, so it escapes as a non-`PKIError`. As a result `server_cert_names`, `server_key_state` and `pki_status` raise. `webserver_monitor`/`monitor_view`, `expose`, Settings Apply and `verify` then fail with an unhandled exception instead of the typed "unreadable" refusal.
- how to see it: Reproduced: replace `config/tls/server` with a symlink to a directory holding the same files. `server_cert_names`, `pki_status` and `server_key_state` each raise `PathContainmentError`.
- verifier: CONFIRMED — runtime_fs.py:106 raises `PathContainmentError` (a ValueError); pki.py:166-171 and :179-184 catch only OSError. Scratch with a symlinked server dir: `server_cert_names`, `server_key_state`, `pki_status` and `server_cert_chain_ok` (documented "Never raises") all raise. `doctor` survives (services.py:1176 catches ValueError).

### CR5-5
- where: `lhpc/core/pki.py:452-462 (`issue_server_cert`, `keep_key=False`)` · severity kept S3: needs a write failure between two atomic writes
- claim: Module docstring: all writes are atomic. `tls-renew` / init / expose "fail closed".
- defect: The new key and the new certificate are two separate atomic writes, and the KEY is written first. If the certificate write fails (ENOSPC, power cut, I/O error), `server.key` is new and `server.crt` is old: a mismatched pair. The running nginx keeps working from memory. The next `apply` fails `nginx -t` (key values mismatch), and the next restart or boot of `lhpc-nginx` fails, so the HTTPS console is unreachable. An `OSError` here is also not a `PKIError`, so `webserver_tls_renew` (:1718) does not turn it into a typed failure.
- how to see it: Inject a failure into `_write_cert` after `_write_key` succeeded (monkeypatch `runtime_fs.atomic_write` to raise for `server.crt`). Afterwards `server.key` no longer matches `server.crt`'s public key.
- verifier: CONFIRMED — pki.py:459-461 writes the key before the cert. Scratch with ENOSPC injected on `server.crt`: a plain OSError escapes (missed by `except PKIError` at service_webserver.py:1718), and `server.key` no longer matches `server.crt`.



## What the plan must contain (≤ 200 lines, tables where possible)
1. **Analysis per finding**: the code path today (file:line), the defect, callers affected, what a test sees today.
2. **The change per finding**: exact function(s), the new behaviour in one sentence, the expected diff size, the
   risk (what working path could break and how the plan rules it out). If two findings share a fix, say so.
3. **Tests**: per finding the test module::name, what it asserts, why it is red before.
4. **Docs/CHANGELOG**: the sentences to change (file:line) and the CHANGELOG line.
5. **Order and commits**: one commit per finding, subject `<id>: <what>`; the order if one depends on another.
6. **Live proof**: whether a row on the Pi 5 is needed (an operator-visible path) and what it would show.
7. **Open questions** for the maintainer, each with your recommendation (≤ 5).
8. **Self-check**: re-read every claim against the code once more; list what you could not verify.
