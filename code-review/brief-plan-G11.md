# CLOUD BRIEF · PLAN for fix group G11 of loraham-pi-control (one Claude Code cloud session, read-only on the code)

You write the PLAN for a group of verified defects; you change no code. Output = ONE commit on this routine's branch
adding `plans/PLAN-G11.md`. Never touch `main` or `dev`, no pull request. (Attribution lines are stripped
downstream; the file is copied out.) Base: origin/main = e5187f70ae4e81a1081a835be06f84746222c3b6 (v0.11.10). Read `docs/architecture.md` (the safety
invariants), `docs/README.md`, `tests/README.md` (the house rules of the tests) and `docs/maintenance.md` (how a patch
is cut) first.

## The maintainer's rules for every change
Simple, robust, maintainable: the SIMPLEST change that removes the defect; no new mechanism unless the defect cannot be
removed without one (then say so and size it); no behaviour change beyond the defect; a regression test per change that
is RED before and GREEN after, placed where the maintainer would look for it (the existing test module of that file);
docs updated where a sentence becomes untrue (one place per fact); the CHANGELOG line in the operator's words.

## The findings of this group (verified; file:line on e5187f70ae4e81a1081a835be06f84746222c3b6)
### CR4-1
- where: `lhpc/core/service_selfupdate.py:404` · severity kept S2: masked today by frozen units, but the one check meant for a unit-changing release
- claim: The one-click helper "refresh[es] the managed units with the NEW code" (l.984-988), and the out-of-process `verify-set` is what "turns a silent boot-restore outage into a visible partial update" (l.389-394).
- defect: In the helper, `state/selfupdate.inflight` exists for the whole run (unlinked only at l.1009). `updater_integration()` therefore reports `status="recovery_required"` (l.748-750), so `fixable` is False. `_refresh_units_post_update` returns `(True, "units not this deployment's — left untouched")` before the verifier runs. Every one-click update reports `units_refreshed=True` without checking anything. If a release changes unit bytes, boot restore stops working and nothing reports it. Today this is masked only by the frozen-units invariant.
- how to see it: Reproduced with a scratch test. Use `op_svc(units=True, invocation=True)`, a queued request, a stubbed `self_update_apply` that returns ok, and `subprocess.run` patched to fail `verify-set`. `self_update_run_service()` returns ok, `units_refresh_detail == "units not this deployment's — left untouched"`, and `subprocess.run` is never called. A test asserting that the helper path invokes `verify-set` would be red. The existing helper tests all monkeypatch `_refresh_units_post_update` or `updater_integration`.
- verifier: CONFIRMED — The helper's inflight record (service_selfupdate.py:934) makes the request "in_flight" (:1110-1111). `updater_integration` then forces recovery_required with fixable=False (:748-756), and :404-405 returns early. Scratch as in the finding: ok=True, units_refreshed=True, detail "units not this deployment's — left untouched", `subprocess.run` never called.

### CR4-2
- where: `lhpc/core/provenance.py:198` · severity kept S2: the label can claim a signature that does not cover the pin, and describe-string 
- claim: `signature-verified` means "pin verified **and** signed by a configured trusted signer" (docs/provenance.md; module docstring l.8). model.py says `pin_tag` is a "human tag (NOT used as immutable verification)".
- defect: With signers configured and a `pin_tag` set, the code runs `git verify-tag <pin_tag>`. That checks only the tag object's signature, and nothing checks that the tag points to `pin`. A clone whose `refs/tags/<pin_tag>` names any trusted-signed tag (for example one from a remote override or fork) yields `SIGNATURE_VERIFIED` for a pin the signature does not cover. The reverse also happens: many shipped `pin_tag` values are `git describe` strings (`7.7.1-57-g187ef247`, `9c00022`), not tags, so `verify-tag` fails and a correctly signed pin commit always reads `signature-unavailable`.
- how to see it: Configure one trusted signer. Make a temporary repo with HEAD = pin (unsigned) and a signed tag named `pin_tag` on another commit. `evaluate(..., source="pinned")` returns `signature-verified`. A test "signature status is bound to the pin commit" would be red.
- verifier: CONFIRMED — provenance.py:198-201 runs `verify-tag <pin_tag>` with no check that the tag points at the pin; HEAD==pin (:191) is the only binding. A stub runner (trusted VALIDSIG) gives `signature-verified`. With real git, `verify-tag` on an abbreviated commit returns rc 1; manifest.example.toml:303/464/1732/1846 hold such values. The content is still pin-bound, so the overclaim is about origin.

### CR4-5
- where: `lhpc/core/updater_units.py:454` · severity kept S3: needs an operator-made override in a path the check does not scan
- claim: `_has_dropin`: a drop-in "in the user unit dir or any system search dir" makes the unit OVERRIDDEN, so one-click and boot restore act only on a byte-exact, un-overridden unit.
- defect: The search covers only `~/.config/systemd/user`, `/etc/systemd/user` and `/usr/lib/systemd/user` (l.59). It misses `~/.config/systemd/user.control` and `$XDG_RUNTIME_DIR/systemd/{user.control,transient}` (where `systemctl --user set-property` writes), `/run/systemd/user`, `/usr/local/lib/systemd/user`, `~/.local/share/systemd/user`, and the type/prefix drop-ins `service.d/` and `lhpc-.service.d/`. A higher-priority fragment in `user.control` is not seen either, because `verify` reads only `~/.config/systemd/user/<unit>`. A sandbox-relaxing override there still reads `ok` for the bus-free gates: `self_update_trigger` and the boot-restore gate. Only the shell-side `--repair-integration` checks `DropInPaths`/`FragmentPath` authoritatively.
- how to see it: Create `~/.config/systemd/user.control/lhpc-web.service.d/x.conf` (or `~/.config/systemd/user/service.d/x.conf`) next to canonical units. `integration()["status"]` stays `ok`.
- verifier: CONFIRMED — `_has_dropin` searches only `_DROPIN_DIRS` (updater_units.py:59) plus the user dir (:470-484). Scratch with canonical units: a .conf under `user.control/lhpc-web.service.d`, `service.d/` or `lhpc-.service.d/` keeps status `ok`. Only `--repair-integration` asks systemd (service_selfupdate.py:1273-1284).

### CR4-7
- where: `lhpc/core/service_binary_ops.py:826` · severity kept S3: needs an operator-replaced file the hash cannot read
- claim: Retire: "Only a MODIFIED file stops us: that is operator content we must not delete."
- defect: `binary_receipt.sha256_file` returns `""` for a file that is unreadable, over 512 MiB, or not regular (binary_receipt.py:316-322). `_retire_body` treats `actual == ""` as "simply GONE" and then unlinks the path (l.862-866). An operator-replaced file the hash cannot read (mode 000, or larger than the bound) is deleted without the "changed" refusal.
- how to see it: Replace a receipt-listed file with a different file at mode 000 (as a non-root user), then run `binary_retire(stack)` without force. The file is removed and retire reports success. A test "unreadable modified file blocks retire" would be red.
- verifier: CONFIRMED (via the size bound; mode 000 not reproducible as root) — `sha256_file` returns "" on failure (binary_receipt.py:316-324), and service_binary_ops.py:826 treats "" as gone. Scratch: a sparse file over 512 MiB in place of a receipt file was deleted and retire returned ok. A small modified file is refused (control). A symlink replacement is also unlinked.



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
