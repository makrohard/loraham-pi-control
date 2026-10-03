# CLOUD BRIEF · PLAN for fix group GL2 of loraham-pi-control (one Claude Code cloud session, read-only on the code)

You write the PLAN for a group of verified defects; you change no code. Output = ONE commit on this routine's branch
adding `plans/PLAN-GL2.md`. Never touch `main` or `dev`, no pull request. (Attribution lines are stripped
downstream; the file is copied out.) Base: origin/main = e5187f70ae4e81a1081a835be06f84746222c3b6 (v0.11.10). Read `docs/architecture.md` (the safety
invariants), `docs/README.md`, `tests/README.md` (the house rules of the tests) and `docs/maintenance.md` (how a patch
is cut) first.

## The maintainer's rules for every change
Simple, robust, maintainable: the SIMPLEST change that removes the defect; no new mechanism unless the defect cannot be
removed without one (then say so and size it); no behaviour change beyond the defect; a regression test per change that
is RED before and GREEN after, placed where the maintainer would look for it (the existing test module of that file);
docs updated where a sentence becomes untrue (one place per fact); the CHANGELOG line in the operator's words.

## The findings of this group (verified; file:line on e5187f70ae4e81a1081a835be06f84746222c3b6)
**Group context / decisions:** The small S3 items of list 2 (one commit each; keep each change ≤ ~30 lines). DECIDED CR9-5: the DOCUMENTED parser rule wins — fix the test that asserts the opposite. CR9-6/CR9-7 are in the bootstrap renderer (deps.py) + regenerate the script. Note CR4-7, CR5-4, CR5-5 are ALSO listed in G7/G11 — plan them ONCE here and say so.

### CR3-4
- where: `lhpc/core/install.py:944-950 (`extra_files`), consumed at 603` · severity kept S3: fails safe; only operator-made nested repos outside the ignored names (install.py
- claim: `extra_files` docstring: `ls-files --others` "lists every file INDIVIDUALLY". architecture.md: "Locally added files are never collateral … carries the operator's … added files into the new source".
- defect: `git ls-files -z --others` does not descend into an untracked nested git repository. It lists it as a single `dir/` entry (checked with git here: `lib/foo/`). That entry is not filtered. `source_fs.carry_extras` splits it into an empty leaf, `os.stat("")` fails, and the update is refused with "local file could not be read (path is gone or not a directory)". Every update of that checkout fails with a misleading reason. It fails safe (the prior is restored), but the stated "carry, never block" contract does not hold, and the operator is not told the real cause. Shipped stacks keep their nested clones under ignored `.work/`, so only operator-made nested repos trigger it.
- how to see it: In an adopted checkout run `git init lib/foo`, add a file in it, then `adopt_source(comp, force=True)`. The status is `failed` with the carry-failed detail.
- verifier: CONFIRMED (one detail wrong: the error is "local file could not be read ([Errno 2] … '')" from source_fs.py:562, not the message at 557) — `extra_files` (install.py:944-950) returned `('lib/foo/',)` for a nested `git init`. `carry_extras` splits that into the leaf `""` (source_fs.py:554), and `os.stat("")` fails. Scratch: `adopt_source(force=True)` gave status `failed` and the prior stayed. A plain non-git `lib/foo/x` is carried (control).

### CR4-7
- where: `lhpc/core/service_binary_ops.py:826` · severity kept S3: needs an operator-replaced file the hash cannot read
- claim: Retire: "Only a MODIFIED file stops us: that is operator content we must not delete."
- defect: `binary_receipt.sha256_file` returns `""` for a file that is unreadable, over 512 MiB, or not regular (binary_receipt.py:316-322). `_retire_body` treats `actual == ""` as "simply GONE" and then unlinks the path (l.862-866). An operator-replaced file the hash cannot read (mode 000, or larger than the bound) is deleted without the "changed" refusal.
- how to see it: Replace a receipt-listed file with a different file at mode 000 (as a non-root user), then run `binary_retire(stack)` without force. The file is removed and retire reports success. A test "unreadable modified file blocks retire" would be red.
- verifier: CONFIRMED (via the size bound; mode 000 not reproducible as root) — `sha256_file` returns "" on failure (binary_receipt.py:316-324), and service_binary_ops.py:826 treats "" as gone. Scratch: a sparse file over 512 MiB in place of a receipt file was deleted and retire returned ok. A small modified file is refused (control). A symlink replacement is also unlinked.

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

### CR6-3
- where: `lhpc/core/service_boot_restore.py:147-152` · severity S3 kept: needs EACCES or EIO on a directory lhpc owns; start gates still apply
- claim: `_stop_intent_stacks` docstring: an unreadable intent counts as INTENT PRESENT; "failing toward restore would resurrect a stack the operator stopped".
- defect: An `OSError` from `iterdir()` (EACCES/EIO on `state/stop-intent/`) returns `set()`. `Path.is_dir()` also returns False on an error. Every intent is then ignored, and boot restore starts the stacks the operator had stopped. The start gates still apply.
- how to see it: Make `state/stop-intent/` unreadable (chmod 000), write an intent for `kiss`, then run `_classify_boot_evidence`: the kiss evidence is returned as restorable. No test covers an unreadable directory; only a malformed file is tested (tests/core/test_boot_restore.py:1400).
- verifier: CONFIRMED — service_boot_restore.py:147-152: any OSError from `iterdir`, or `is_dir()` returning False, gives `set()`, so no intent is honoured. This contradicts the docstring at 143-145. Fix: on an error other than ENOENT, return a sentinel that makes the caller skip all restores

### CR6-4
- where: `lhpc/core/service_network.py:579-583 (also 545-571)` · severity S3 kept: plaintext passphrase (0600) left on disk until manual cleanup, after a narrow loc
- claim: Module docstring 14-22 and 529-530: the PSK file is unlinked after nmcli returns, and the helper removes the pending record in `finally` on every completion path.
- defect: `network_finalize` has two early-return groups before its `try/finally`: the `ResourceBusy` return at 581-583 and the record checks at 545-571. These leave `state/network-psk-<op_id>` (plaintext WLAN passphrase, 0600) on disk with nothing that ever removes it. The pending record stays until TTL, no outcome is written, and the join silently does not happen. The busy case is a real race: `_network_watch_tick`, `network_prefer`, `network_forget` and `network_ap_now` all take the same reslock briefly via `_net_op_lock`, even when they are then refused by the pending record.
- how to see it: Hold `controller-network-op` (e.g. a watchdog tick) at the moment the detached helper reaches line 579. The helper exits 1, and `state/network-psk-*` persists after the call. A test would assert the pwfile is absent after `network_finalize` returns.
- verifier: CONFIRMED — service_network.py:545-571 and 579-583 return 1 before the `try/finally` at 585. Grep shows `network-psk-*` is unlinked nowhere else (no sweep). `_net_op_lock` (254-271) takes the reslock before it checks the pending record, so the brief contention described in the finding exists. Fix: unlink the canonical pwfile on these returns, or retry the lock briefly

### CR6-5
- where: `lhpc/core/service_network.py:448-459 vs 474-476, 481-484, 492-496, 510-516` · severity S3 kept: stray autoconnect=no profile and a wrong "nothing was changed" message
- claim: The spawn-failure message says "nothing was changed" (516). The same holds for `network_ap_now` at 852-853.
- defect: For a new SSID, `nmcli connection add` has already created a persistent NM profile. Every later failure (secret file, `/proc/uptime`, marker, spawn) returns without deleting it, so a stray "stored network" appears in the panel. In `network_ap_now`, the preferred flag is already cleared (826-827) and NM autoconnect is modified when the spawn fails, yet the reply says "nothing was changed".
- how to see it: Make `_lifecycle()._spawn` return None. After `network_connect(ssid=…, apply=True)`, `nmcli connection show` lists the new profile despite the "nothing was changed" reply.
- verifier: CONFIRMED — service_network.py:448-459 creates the profile; the failure returns at 474-476, 481-484, 492-496 and 510-516 never run `nmcli connection delete`. network_ap_now clears the preference at 826-827 before the spawn failure message at 852-853

### CR6-6
- where: `lhpc/core/service_firewall.py:1137-1149` · severity S3 kept: only if the config write fails after validation; the operator must then still run
- claim: `firewall_configure` (1098-1101): scripts and config are committed together under one lock, and "nothing is persisted until validation passes".
- defect: The operator scripts are replaced with the PROSPECTIVE candidate (1138) before `save_firewall_config` (1144). If the save then fails (`ConfigError` is returned as "firewall config rejected"; an `OSError` is not caught at all), `config/files/firewall/firewall-apply.sh` embeds an intent that was never saved. An operator who runs the shown sudo command applies the rejected selection, e.g. a ticked unauthenticated port. The gate later reports Config ✗, but the ruleset is live.
- how to see it: Monkeypatch `config.save_firewall_config` to raise `ConfigError`, then call `firewall_configure(allow_endpoints=[<deny-default id>])`. The result is `ok=False`, yet the rendered apply script's candidate has `selected: true`.
- verifier: CONFIRMED — Scratch test: `save_firewall_config` patched to raise ConfigError. `firewall_configure(allow_endpoints=[id])` returns ok=False "firewall config rejected", but firewall-apply.sh contains `"selected": true` and the saved allow_endpoints is `()`. An OSError from `_write_local_tables` is uncaught (1144-1149). Fix: on save failure, re-render the scripts from the saved config

### CR7-12
- where: `lhpc/core/commands.py:539-545` · severity S3 kept (needs an odd name; the failure is obscure but nothing unsafe follows)
- claim: a user value is always its own validated token and can never become syntax
- defect: The post-start template is filled by chained `str.replace`; `__STEPS__` (holding operator values) is inserted before `__BINDING__/__GATED__/__META__/__ROOT__/__RESULT_REL__` are replaced, so a value equal to a later marker is substituted. `node_long` accepts any printable text.
- how to see it: Meshtastic `node_name="__ROOT__"` → rendered launcher fails `compile()` (required post-start fails, start refused with an obscure error); `"__GATED__"` → owner name set to `False`.
- verifier: CONFIRMED — commands.py:539-545 replaces markers in a chain, with __STEPS__ before the later markers. Scratch g3/r12.py, real meshtastic post_steps: node_name "__ROOT__" passes validators.node_long, and the rendered launcher fails compile() (SyntaxError). With "__GATED__", the rendered owner argv is `--set-owner False`.

### CR7-17
- where: `lhpc/core/config.py:1465 (and 1562-1575)` · severity S3 kept (narrow concurrent-writer race; the result is an http console silently downgraded 
- claim: 1516-1517: the http/client-cert check uses "the EFFECTIVE result … so neither half can sneak in alone"
- defect: `_reject_http_with_cert_auth` runs on an unlocked `load_config` before `config_lock` is taken; a concurrent web Apply (https + cert mode) and CLI `--scheme http` can both pass and leave http + cert mode on disk (downgraded to no-auth at parse with only a diagnostic). `save_stackweb_configs` validates inside the lock.
- how to see it: Two concurrent writers interleaved between check and lock.
- verifier: CONFIRMED — config.py:1465 runs _reject_http_with_cert_auth on an unlocked load_config, and the config_lock is taken only at 1469-1470. save_stackweb_config (1562-1580) has the same pattern. Callers such as service_webserver.py:472 (webserver_configure) and 546/1471 use the default hold_lock=True with no outer lock. The parse at config.py:687-690 then downgrades to no-auth. Established by code reading; the race was not run.

### CR9-5
- where: `lhpc/core/probes/unixsock.py:287-296` · severity S3 kept (false "ready" needs a garbled/hostile daemon reply)
- claim: Module docstring: "converts any malformed/absent/oversize response into degraded/unknown evidence — never … a false healthy". architecture.md: "One bounded CONF parser for every read (a reply reaching the 4 KiB read cap … is rejected)".
- defect: `probe_daemon_status` (used by `status.py:325` for `daemon-status` endpoints) has its own parser. It accepts a reply that hit the 4096-byte cap with no newline, and an over-tokenized reply, and reports `RADIO=READY`. `daemon_control` rejects both.
- how to see it: `tests/core/test_probes.py::test_daemon_status_oversize_is_bounded_and_parsed_or_safe` is green and asserts `reachable and radio == "READY"` for a 30 KB line, which is the opposite of the documented rule.
- verifier: CONFIRMED — Cited lines are wrong: the parser is unixsock.py:58-98 (file has 98 lines). Scratch g5/cr9_5.py: 30 KB line and 100-token line both give reachable=True ready=True from probe_daemon_status, while daemon_control._query (daemon_control.py:176-193) returns {} for both; test_daemon_status_oversize_is_bounded_and_parsed_or_safe passes (run with --noconftest; flask missing).

### CR9-6
- where: `bootstrap-deps.sh:668` · severity S3 kept (needs a config.txt without a trailing newline)
- claim: "append $1 iff absent (idempotent)" to the boot config.
- defect: `printf "%s\n" "$1" \
- how to see it: tee -a "$CONFIG_TXT"` does not ensure the file ends in a newline. If config.txt's last line has no trailing newline, `dtparam=spi=on` is glued onto that line. SPI is then not enabled, and the operator's last directive is corrupted (e.g. a USB-gadget/network overlay, which can leave a headless box unreachable after the reboot).
- verifier: CONFIRMED — Snippet of bootstrap-deps.sh:667-669 in g5/cr9_6.sh on a file `dtoverlay=dwc2` (no newline) yields `dtoverlay=dwc2dtparam=spi=on` + `dtoverlay=spi0-0cs`.

### CR9-7
- where: `bootstrap-deps.sh:130-131` · severity S3 kept (non-default --target only)
- claim: Pre-flight: "gpsd must never claim a receiver that an LHPC `nmea` source reads DIRECTLY".
- defect: The `[gps] source = nmea` check only reads `$LHPC_RUNTIME_ROOT`, which `sudo` env_reset strips, or `~<op>/loraham-pi-control`. On a box installed with `install.sh --target <elsewhere>`, the check finds no config ("fresh box = proceed"). It installs gpsd, adds `-n` and restarts it (:597), so gpsd (USBAUTO) takes the receiver and leaves a u-blox in UBX binary mode, which is the case this check exists to prevent.
- how to see it: Install to a non-default target, set `lhpc gps --source nmea`, re-run `sudo bash bootstrap-deps.sh --spi-mode skip` (the documented repair): no "time source SKIPPED" line, and gpsd is installed and restarted.
- verifier: CONFIRMED — bootstrap-deps.sh:130-131 only reads LHPC_RUNTIME_ROOT or the operator's default checkout; install.sh:70 supports --target. Scratch g5/cr9_7.sh (lines 113-165): with the env var unset (as after sudo env_reset) and `source = "nmea"` in the alt root, NO_TIME_SOURCE stays empty; with the env var it skips.



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
