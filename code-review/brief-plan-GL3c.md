# CLOUD BRIEF · PLAN for fix group GL3c of loraham-pi-control (one Claude Code cloud session, read-only on the code)

You write the PLAN for a group of verified defects; you change no code. Output = ONE commit on this routine's branch
adding `plans/PLAN-GL3c.md`. Never touch `main` or `dev`, no pull request. (Attribution lines are stripped
downstream; the file is copied out.) Base: origin/main = e5187f70ae4e81a1081a835be06f84746222c3b6 (v0.11.10). Read `docs/architecture.md` (the safety
invariants), `docs/README.md`, `tests/README.md` (the house rules of the tests) and `docs/maintenance.md` (how a patch
is cut) first.

## The maintainer's rules for every change
Simple, robust, maintainable: the SIMPLEST change that removes the defect; no new mechanism unless the defect cannot be
removed without one (then say so and size it); no behaviour change beyond the defect; a regression test per change that
is RED before and GREEN after, placed where the maintainer would look for it (the existing test module of that file);
docs updated where a sentence becomes untrue (one place per fact); the CHANGELOG line in the operator's words.

## The findings of this group (verified; file:line on e5187f70ae4e81a1081a835be06f84746222c3b6)
**Group context / decisions:** One shared safe-read helper (RecursionError / UnicodeDecodeError / TypeError / ValueError → the typed refusal) for the state readers; CR7-9 and CR7-14 were already fixed point-wise in 0.11.10 — the plan says whether they migrate to the helper or stay; auto_install.py:486 is another RecursionError site.

### CR7-10
- where: `lhpc/core/restart_required.py:283-286 (same class: jobresult.py:164, status.py:281, config.py:737-742)` · severity S3 kept (needs a crafted/corrupt state file or hand-edited local.toml)
- claim: `read_marker`: malformed marker → `{"unsafe": True…}`, GET-safe; config: hand-edits "must never crash config load" (700-701)
- defect: `json.loads` of a deeply nested document raises RecursionError, which none of these `except (ValueError, …)` catch: `read_marker` raises; `jobresult.read_results` raises; `_gps_feed_ready` raises on a 4 KiB marker of `[`; `load_config` crashes on a nested `[firewall] extra_allow` string (the TOML path itself converts RecursionError, 580-583).
- how to see it: Write `'['*5000` to `state/restart-required/x.json` → `read_marker(P,"x")` raises RecursionError (reproduced; same for jobresult).
- verifier: CONFIRMED — Line ref wrong: read_marker's json.loads is restart_required.py:53-55 (file has 142 lines); jobresult.py:164, status.py:281, config.py:737-742 are correct. Scratch g3/r10.py with '['*5000: read_marker, jobresult._read_raw/read_results, auto_install.read_marker (auto_install.py:486-487, catches only JSONDecodeError; not in the finding) and load_config (extra_allow string) all raise RecursionError.

### CR7-13
- where: `lhpc/core/runtime_fs.py:545 (`read_text`); lhpc/core/commands.py:147, 158 (`build_env`)` · severity S3 kept (needs a non-UTF-8 config or secret file; leads to a crash, not data loss)
- claim: `read_text` raises only PathContainmentError/OSError; `build_env`: a bad `@file:` secret raises CommandError
- defect: Strict UTF-8 decode raises UnicodeDecodeError. Callers catch only OSError/ConfigError/CommandError (config.py:1799 pre-image read, lifecycle.py:384-391, build_launcher_runtime.py:288-293) → CLI traceback / web 500 / crashed build. `build_env` also reads via `Path.read_text` (follows symlinks, unbounded, blocks on a FIFO).
- how to see it: `config/files/x.yaml` containing byte `\xff`, then `apply_config_transaction` → UnicodeDecodeError, not ConfigError.
- verifier: CONFIRMED — runtime_fs.py:545 decode raises UnicodeDecodeError (a ValueError, not an OSError). Scratch g3/r13.py: a \xff in config/stacks/chat.toml makes save_config_bundle raise UnicodeDecodeError out of config.py:1799. build_env with an @file: secret containing \xff raises UnicodeDecodeError, not CommandError (commands.py:147,158).

### CR7-9
- where: `lhpc/core/jobresult.py:112` · severity kept S3
- claim: module: "Every function is best-effort and NEVER raises (a GET must not 500)"; reads "STRUCTURALLY validated"
- defect: A terminal marker whose `finished_at` is a falsy non-string (`0`, `false`, `[]`) skips the 106-109 check and hits `_TS_RE.match(0)` → TypeError out of `read_results`. The banner caller (service_maintenance.py:1237) swallows it and drops ALL job entries incl. unsafe ones; `_web_unsafe_source_block` (service_lifecycle_ops.py:3339) does not catch it, so the unsafe-source gate raises.
- how to see it: `state/jobresults/a.log.json` = `{"op":"build","state":"done","log":"a.log","attempt_id":"abcdef12","finished_at":0}`; `read_results(P)` raises TypeError (reproduced).
- verifier: CONFIRMED — jobresult.py:112 calls `_TS_RE.match` on a non-str. The 106-109 guard is skipped because the value is falsy. Scratch run: `read_results` raises TypeError for finished_at 0/False/[]. The banner (service_maintenance.py:1237) swallows it and drops every job entry, unsafe ones included. `_web_unsafe_source_block` (service_lifecycle_ops.py:3339, called at 3422) does not catch it, so the web build POST raises.

### CR7-14
- where: `lhpc/core/config.py:1751` · severity S3 kept (needs a corrupt journal; the journal is kept, so nothing is lost, but every confi
- claim: journal recovery (1714-1716): a malformed journal "is NEVER treated as absent — it blocks"
- defect: `int(rec.get("mode", 0o644))` raises ValueError on a non-numeric mode (a non-str `pre` gives AttributeError in `_atomic_write`) — escapes `set_operator_identity` (catches OSError/ConfigError) after earlier targets may already be restored.
- how to see it: Journal with `"mode": "rw"` → `recover_config_transaction` raises ValueError.
- verifier: CONFIRMED — config.py:1751. Scratch g3/r14.py: journal target 1 is valid and target 2 has mode "rw". recover_config_transaction raises ValueError after target 1 is already restored, and set_operator_identity raises ValueError (it catches only OSError/ConfigError). With "pre": 5, it raises AttributeError.



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

## Commit identity (the maintainer's rule — a direct violation otherwise)
Before your first commit run `git config user.name makrohard` and `git config user.email <the author e-mail of the makrohard commits in this repository: git log -1 --format=%ae --author=makrohard origin/main>`, and commit with that identity; no Co-Authored-By, Claude-Session or any AI-attribution line in any message. After each commit check `git log -1 --format='%an %cn%n%B'` shows makrohard twice and no such line; fix it with `git commit --amend --reset-author --no-edit` before you push.

## Adversarial self-review before the push (mandatory)
When everything is green, re-read your whole diff once more AS A HOSTILE REVIEWER who will be paid per finding: for every hunk ask what input, timing, caller or platform breaks it; what the old code handled that the new code does not; which test only passes because of the fake; which claim in your report you have not actually run. Fix what you find, re-run the gates, and list in the report what this pass found and changed (or 'nothing').

## Review-packet header (mandatory wording)
Every review packet you write MUST start with: a request paragraph that names what to judge and the answer form — `| commit | verdict (OK / FINDING) | what |` and a final line GREEN / GREEN WITH NOTES / RED — and the sentence "This file is your whole input: you have no repository access; use no connector, tool or web lookup."
