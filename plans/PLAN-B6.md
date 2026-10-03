# PLAN-B6 — the three design items B4 left open (CR7-13b, CR3-3b, CR10-1)

Base: `main` e5187f70 (v0.11.10). B4 (`claude/busy-heisenberg-vb7z2i`) is stacked before these commits; every
line below is on e5187f70 unless marked "B4:". Order of the code commits: CR7-13b, CR3-3b, CR10-1.

## 1. CR7-13b — `@file:` secrets are read with `Path.read_text`

- **Today.** `lhpc/core/commands.py:130` `build_env`; `@file?:` reads at :147, `@file:` at :158, both
  `Path(path).read_text(encoding="utf-8")`. B4 (CR7-13) changes only the two `except` lines (:151, :159) to
  catch `ValueError` too. Callers: every launch and build env (`lifecycle` via `build_env`, and the web-job build
  launcher `build_launcher_runtime.py:283-292`, which resolves `@file:` at exec time).
- **Defect.** The path may sit outside the runtime root, and `Path.read_text` follows a symlink, reads a file of
  any size whole, and blocks for ever on a FIFO (the open waits for a writer: a start or build hangs).
- **What a test sees today.** A symlinked secret is read through the link; a 1 MiB secret is accepted (first
  line used); a FIFO secret never returns.
- **Change.** One reader, `runtime_fs.read_secret_text(path, max_bytes=64 KiB)`: `os.open(O_RDONLY|O_NOFOLLOW|
  O_NONBLOCK|O_CLOEXEC)`, regular-file check on the held fd, bounded read that rejects oversize, strict UTF-8.
  It reuses `runtime_fs._require_regular_fd` and `_read_fd_bounded` (the same checks `read_bytes` applies to
  runtime leaves), but takes a plain path, because a secret need not be under the runtime root. Every refusal is
  an `OSError` (`FileNotFoundError` when absent; a non-UTF-8 file → `OSError`, as B4 made `read_text` do), so
  both `build_env` branches keep their existing handling: absent optional → `""`, anything else →
  `CommandError`. New behaviour: a secret that is a symlink, not a regular file, over 64 KiB or not UTF-8 is a
  `CommandError` (launch/build refused) instead of being followed, read whole or blocking. ~20 lines.
- **Risk.** A box whose secret is a symlink would now be refused. The only manifest secret is
  `@file?:{runtime}/config/secrets/xr_pw` (`manifest.example.toml:1914`), written by LHPC's HMAC actions as a
  regular file; nothing in LHPC creates a secret symlink. Checked: `grep '@file' lhpc` → that one use. An
  optional secret that is a *dangling* symlink was `""` and is now a `CommandError` — the same defect, named.
- **Tests** (`tests/core/test_structured_exec.py`, after `test_at_file_secret_present_is_read`):
  `test_a_secret_that_is_not_a_bounded_regular_file_is_a_command_error[symlink|oversize × @file:|@file?:]` (red
  before: the value is returned) and `test_a_fifo_secret_is_refused_without_blocking[@file:|@file?:]` (red
  before: the call is still blocked after 5 s; the test then opens the FIFO for writing to release it).
  Preservation: the missing/empty/present tests and B4's `test_non_utf8_secret_file_is_a_command_error`.
- **Docs.** `docs/architecture.md:192`: "a missing, unreadable or empty secret" → add "a symlinked, non-regular
  or oversized one". CHANGELOG (top of the file, §5): "A secret file named with `@file:` (the MeshCom HMAC
  password) is used only when it is a regular file of at most 64 KiB, not a symlink; anything else stops the
  start or build with a named error instead of being followed, read whole, or hanging on a pipe."
- **Stacking.** `:158` is adjacent to B4's `:159`, so stacking onto B4 conflicts in that one hunk. Resolution:
  B6's read line + B4's `except (OSError, ValueError)` line (both kept; `ValueError` is then unreachable but
  harmless). Verified by cherry-picking B4 then B6 in a scratch worktree; recorded in the report.

## 2. CR3-3b — a crash during the clone leaves a journal-less candidate

- **Today.** `install.py:510` `_stage_and_activate`: candidate name `.<dest>.candidate-<pid>-<ns>` (:526);
  journal preflight (:543-549); `_stage_candidate` (:551 → :713) creates the candidate (:724, again :759 for the
  local fallback) and clones into it (:743, up to `_CLONE_TIMEOUT_S` = 900 s, :1962) or copies the local tree.
  The first journal is written only in `_activate_held` (:1743). Recovery (`_recover_scan` :1220) reads only
  `*.json` journals. B4 changes `_recover_one`'s call (:1322-1324) and `_finish_or_rollback` (B4 :1521-1536,
  :1575-1580, incl. CR3-3a's removal of a candidate on its full v5 ident) — none of the lines below.
- **Defect.** Power loss or a kill during the clone leaves a whole tree that no record names. The source rule
  "without identity evidence nothing is deleted" keeps it on the SD card for good; repeated, it fills the card.
- **Callers.** Every install/update path (`adopt_source` direct and under the services' source guard,
  auto-install). What a test sees today: after a SIGKILL during staging, the candidate stays after recovery.
- **Change — a pre-clone record, the smallest mechanism that leaves evidence.** In `_stage_and_activate` the
  transaction's `with` also opens `_staged_clone_record(dest, staging)`: an exclusive (`open_marker_excl`)
  leaf `state/source-txn/<journal-stem>.<candidate-name>.staging` holding `{"state": "staging", source_rel,
  candidate_rel, "ident": null}`, written before the candidate exists; `_stage_candidate` rewrites it with the
  candidate's `[dev, ino]` right after each `create_candidate` (two sites, through the retained marker fd); the
  `with` removes it when `_stage_and_activate` returns, by which time a journal owns the candidate or the
  candidate is gone. Recovery (`_recover_scan`, before the journals): for each `.staging` record, validate it
  like a journal (managed destination, record name, controller candidate name, ident shape) and take the
  source-path lock. Busy → left alone, nothing reported. Held → if a journal exists for that source, the journal
  owns the candidate and the record is cleared; else the candidate is removed with `source_fs.remove_bound` on
  the recorded `[dev, ino]` (no ident yet → only an *empty* directory is removed, `rmdir`), and the record is
  cleared once the candidate is gone. A candidate that cannot be proven stays, and so does its record. ~60 lines.
- **Why nothing smaller.** Without a record there is no evidence (the B4 rule). Writing the record into the
  `.json` journal itself would make every running clone a "pending journal": `_pending_journals` (:1197),
  `build_launcher_runtime.py:233`, `service_auto_install.py:1735` would block all source mutation, builds and the
  first-start banner box-wide for up to 15 minutes, and `_activate_held`'s exclusive journal create (the
  injected-journal guard) would have to become a rewrite. A separate non-`.json` leaf changes neither.
- **Liveness: the source lock, not the pid.** The cloning process holds the source-path lock for the whole
  clone (`adopt_source` :376-382; the services' guard `services.py:383-412` keeps the source locks after
  releasing the index). A flock dies with its process and is per open file, so recovery holding that lock proves
  the writer is gone — the same proof `planned` journals rely on today (:1319-1326). A recorded pid would add a
  failure mode: after a reboot the pid can belong to any process, and the clone would be kept for good. The pid
  is still in the candidate name for the operator. **(Open question 1.)**
- **Evidence strength.** `[dev, ino]`, not v5 `[dev, ino, ctime]`: the clone itself changes the directory's ctime,
  so no ctime recorded before the clone can match. It is bound to a unique `pid-ns` name the record names, in
  the controller-owned txn dir, and is used only after the lock proves the writer dead; B4's CR3-1/2 accept
  `[dev, ino]` for one leaf per state on the same reasoning. A journal-owned candidate keeps the journal's full
  v5 proof: records are processed before journals and a record whose source has a journal is cleared without
  touching anything.
- **Risks ruled out.** (a) Self-update: the controller checkout is not a managed stack source
  (`_managed_source_dests`), and self-update does not use `_stage_and_activate`; a record for it is invalid and
  left. (b) B4's CR3-1/2/3 recovery: journal handling is unchanged and runs after the records. (c) A record
  write failure: the clone proceeds as today (best-effort, like the clone log), so no new refusal. (d) The
  `.staging` leaf never matches `*.json`, so no blocking reader sees it.
- **Test** (`tests/install/test_source.py`, after `test_parent_swap_after_fd_cannot_redirect_clone_outside`):
  `test_recovery_removes_a_clone_killed_before_its_journal` — a forked child adopts `app` and SIGKILLs itself
  inside staging (the copy stub is wrapped to copy, then die: the one seam inside the window); the parent
  asserts the candidate exists and no journal does, then `recover_source_activations()` removes the candidate
  and the record, the box is unblocked and a new adoption succeeds. Red before: the candidate survives.
  Plus `test_a_staged_clone_whose_source_lock_is_held_is_left_alone` (preservation of the live case: record +
  candidate, source lock held → both kept; green before and after).
- **Docs.** `docs/architecture.md:206-210` Source transactions: "An update clones a candidate beside the
  destination, …" → "… records the clone before it starts (so recovery removes a clone a crash interrupted), …".
  CHANGELOG: "An install or update interrupted by a power cut or crash while it was still downloading no longer
  leaves the partial download beside the source for good; the next source operation removes it."

## 3. CR10-1 — the lab's "faithful reboot" never runs boot restore

- **Today.** `testlab/lhpc_testlab/ops.py:279-311` `power()`: stops running stacks with `svc.stop(...,
  _operator=False)` (:293-297, result discarded), advances the boot id, then `svc.start` for each (:302-308).
  Callers: the hidden `lhpc-testlab _power` helper (`cli.py:58`), spawned by the console's Reboot/Power-off
  (`spawn.py:40`); tests `testlab/tests/unit/test_testlab.py::test_power_reboot_does_not_tombstone`,
  `testlab/tests/acceptance/test_power_network.py`, `test_chain_roundtrip.py:168`. B4 does not touch `power()`.
- **Defect.** Production boot restore (`service_boot_restore.py:267`) — evidence classification, the
  journal, the claim hook, the start gates — never runs in the lab; a stop clears the evidence a real reboot
  leaves. The acceptance test only proves kiss listens again.
- **Change.** For `kind == "reboot"`: kill every owned process group whose record `Lifecycle.verify_owned`
  still proves (SIGKILL to the pgid; records kept, as a power cut leaves them), wait for proven cessation
  (`_wait_ceased`), advance the boot id, respawn the fake gpsd (as today), then `svc.boot_restore_run()`; the
  event log line keeps "simulated reboot … (host untouched)" and adds the driver's summary. `poweroff` is
  unchanged (not this defect). ~35 lines.
- **The one gate the lab answers.** `_web_integration_proven` (:318) reads `$HOME/.config/systemd/user` on the real
  host; the lab's console is not a user unit (`.devcontainer/start.sh`, `LabServer`), so the driver would retire
  every record as "disabled". `power()` sets that one check on its own service instance to "proven" (the lab's
  console is up by construction); admission, boot id, `[boot] restore`, classification, journal and every start
  gate are production. **(Open question 2.)**
- **Tests.** Unit (`testlab/tests/unit/test_testlab.py`, after `test_power_reboot_does_not_tombstone`):
  `test_simulated_reboot_kills_owned_groups_and_runs_boot_restore` — a real `sleep` in its own session with a
  v1 ownership record for stack `kiss` (as a launch writes it), `ops.power(svc, "reboot")` → the `sleep` died of
  SIGKILL and `state/boot-restore.json` carries the new boot id and a consumed `kiss` item. Red before: the
  `sleep` lives (the status probe does not call it running) and no journal exists. Acceptance
  `test_simulated_reboot_restores_running_stacks`: additionally asserts the journal's boot id is the new one and
  its `kiss` item `succeeded` (slow lab lane; not run here, see report).
- **Docs.** `docs/testlab.md:94-95`: "owned stacks stop, the boot identity advances …" → "owned process groups
  are killed (their records kept, as after a power cut), the boot identity advances and the production boot
  restore runs (`state/boot-restore.json`) …; the one gate that reads the host's user units counts as passed".
  CHANGELOG: "Test lab: the simulated Reboot now kills the running stacks and runs the real boot restore."

## 4. Tests run per commit
`python -m pytest -q -p no:cacheprovider` on the touched module(s) + `tests/repo`; `ruff check lhpc testlab`;
`ruff check tests --select F,E9`; CR10-1 also `testlab/tests/unit`. Red-before once against the parent commit
in a git worktree. No full suite.

## 5. CHANGELOG placement
`tests/repo/test_version_consistent.py:24-30` requires the first `## X.Y.Z` heading to be the current version,
so a `## 0.11.11` heading without a bump is red, and `## Unreleased` is not used. The three lines go directly
under `# Changelog`, above `## 0.11.10` — where the next 0.11.x section starts; the release commit puts its
heading above them. **(Open question 3.)**

## 6. Open questions (with the recommendation)
1. Liveness by the source lock instead of a recorded pid (§2). Recommend: the lock (no pid-reuse case, same
   proof as `planned` journals).
2. The lab answering the web-integration gate in-process (§3) rather than writing a canonical unit set under a
   lab HOME. Recommend: the in-process answer; a lab HOME would be a new lab feature.
3. CHANGELOG lines above `## 0.11.10` without a heading (§5). Recommend: as planned; the alternative is
   report-only lines as in B4.
4. A `.staging` record whose candidate cannot be proven stays (re-tried at every recovery, blocks nothing).
   Recommend: keep it — it is the only trace of that leaf.
