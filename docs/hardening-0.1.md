# Hardening 0.1 — safety model & evidence

What the controller now guarantees, and how it is enforced. Not a history; these
are the current rules.

## Structured command execution (no shell)
- All normal lifecycle execution is **structured argv with `shell=False`**: `start`
  (`subprocess.Popen(argv, shell=False, cwd, env, start_new_session=True)`), `build`
  and host `test` (`run_job`), web jobs and post-start (generated Python launchers).
  No `/bin/sh -c`, `sh -c` or `bash -c` remains on any normal path — enforced by
  `tests/test_structured_exec.py` (source scan + spawn-argv capture).
- The manifest defines an argv TOKEN TEMPLATE (`run_argv`/`build_steps`/`test_argv`),
  typed `pre_steps`/`post_steps` (mkdir/chmod/symlink/delay/exec/tcp), `run_cwd` and
  `run_env` (`@file:`/`@env:`). `commands.expand_argv` turns each token into argv:
  a literal is one token; `{param:NAME}` → `emit_param` (option+value are SEPARATE
  tokens, disabled flag → zero tokens); `{operator:callsign}` → one validated token.
  A user value is always its own validated token — it cannot merge with an option,
  change the executable/cwd/env, or become shell syntax. Controller-derived
  `{runtime}`/`{source}` may be embedded in a literal path; a user value never can.
- Every value is still validated by type (`lhpc/core/validators.py`) before
  persistence and before execution. Dependency probing uses `shutil.which`.
  Remote-override URLs use an https/scp-ssh policy.
- All shipped components are migrated (`test_all_command_bearing_components_are_migrated`).
  Manual wrappers + the displayed manual command are generated from the SAME spec
  (`commands.display_command`); they `exec` a fixed argv + forward `"$@"`, never
  building a command string from configuration.

## Verified owned-process stopping
- Each launch is recorded under a UNIQUE id (`state/owned/<comp>__<band>__<pid>.json`,
  mode 0600) with full identity: pid, `/proc` start time, pgid, sid, executable, and
  a sha256 fingerprint of the NUL-separated argv. A daemon owns independent
  433/868/both records (not one mutable marker).
- `Lifecycle.stop` is **record-driven and identity-verified**: before any signal it
  re-reads `/proc` and requires the pid to be alive and its start time, pgid, sid,
  executable and argv fingerprint to still match the record, the pgid to differ from
  the controller's, and the process to be an LHPC-owned session leader. Any mismatch
  → no signal, reported `unverified`/`manual-required` with an exact-PID hint. After
  SIGTERM it waits for **verified cessation** before clearing the record (no
  auto-SIGKILL). The daemon/iGate run without `-d` so LHPC owns the real session
  leader (not a self-daemonized re-PID). Process scanning detects manual processes
  but never authorizes a kill. Tests: `tests/test_process_ownership.py`.

## Path containment
- `Paths.resolve_source()` / `Paths.under()` confine every resolved path to the
  runtime root (reject absolute and `..`). `reset_config` uses the same
  containment-checked helper as save/load. Tests: `tests/test_path_containment.py`.

## Truthful outcomes & readiness
- `start` returns failure unless every required component reached a verified healthy
  state. A daemon `--radio both` verifies BOTH 433 and 868 CONF sockets; a launch
  that never exposes its socket is a failure, not a warning. A dependent is not
  started when daemon readiness failed. `update` aggregates and reports nonzero on
  partial failure. CLI exit status, web flash and summaries agree. Tests:
  `tests/test_truthful_outcomes.py`.

## Staged update / activation
- Updates clone/adopt into a sibling candidate dir and activate by archiving the prior
  source to a transaction-owned `.<name>.prev`, then renaming the candidate in — the active
  source is never destroyed by a failed acquisition. `.prev` is a **transaction artifact,
  not a permanent backup**: once activation succeeds and the destination is proven a usable
  directory, the `.prev` is removed (confirmed gone) BEFORE the journal is cleared, so a
  normal successful update leaves no `.prev` and the NEXT update is not blocked by an
  orphan. A `.prev` with no valid journal remains an unowned orphan that blocks (never
  blind-deleted); a failed `.prev` removal retains the journal + `.prev` and returns
  recovery-required for normal recovery to retry. `pinned` must resolve to the exact
  configured commit; dirty or linked working trees are never overwritten. Tests:
  `tests/test_staged_update.py`, `tests/test_source_txn.py`.

## Uninstall protection
- Uninstall refuses while a target component is running, never removes a source
  still referenced by another component (shared checkout — chat/iGate share
  `LoRaHAM_Daemon`; kiss/serial-kiss share `loraham-kiss-tnc`), and never deletes
  config, secrets or profiles. Tests: `tests/test_uninstall_safety.py`.

## GET no-network guarantee
- No GET route runs a network or git-remote command. Update freshness is an explicit
  action (`lhpc update --check`). A recording-runner spy hits every GET route and
  asserts no network command runs: `tests/test_web.py::test_get_routes_make_no_network_calls`.

## Web path safety & confirmation
- Live daemon settings use the same two-step plan + confirm as other mutations
  (CSRF mandatory). Per-stack config paths validate the id (single path component)
  and band (a real radio band) and are proven to stay inside `config/stacks/` —
  band/id traversal is rejected. Tests: `tests/test_web.py`.

## Atomic, locked config writes
- All config writes go through `_atomic_write` (temp in the same dir, fsync, mode
  set, `os.replace`) under an exclusive `config_lock` flock; local config is `0600`.
  A malformed existing `local.toml` is **preserved, not overwritten** — the save
  refuses and reports it. Tests: `tests/test_config_safety.py`,
  `tests/test_path_containment.py`.

## Package-install correctness
- Tracked TOML assets live in `lhpc/data/` and load via `importlib.resources`
  (`lhpc/core/assets.py`) — no `Path(__file__).parents[...]` repo-root assumption.
  Verified by building a wheel and installing it into a fresh venv outside the
  checkout. Tests: `tests/test_packaging.py`.

## Test commands
```
python -m compileall lhpc
pytest -q
git diff --check
python -m pip wheel . --no-deps -w /tmp/lhpc-wheel-test
# then: install that wheel in a fresh venv and run `lhpc --help` / `lhpc list`
```
Result: the automated test suite passes; `git diff --check` clean; the wheel installs
and runs (`lhpc --help` / `lhpc list`) from an isolated venv. (Test counts grow each
hardening pass — see the suite itself rather than a hard-coded number here.)

## Verified vs. still-open
Verified now: **structured argv execution (shell=False) on every normal lifecycle
and job path** — all shipped components migrated; **manual wrappers generated as
Python `os.execvpe` launchers** (no bash/`exec cd`), from the same spec; input
validation; identity-verified owned-process stopping; daemon/iGate foreground
ownership (no `-d`); **daemon TX-mode apply + readback gating** (a failed SET or
absent/mismatched readback blocks dependents and post-start — `_apply_tx_mode`);
**runtime mutation containment via `Paths.under()`** (lexical + symlink) applied to
wrappers, owned records, post/job launchers, logs, and typed pre-step destinations,
with linked source trees observe-only (`resolve_source` lexical); corrupt-`local.toml`
preservation; remote-override URL policy; `shutil.which` probing; GET no-network;
atomic+locked config writes; bounded log tail; wheel install.

Also verified: the Config page is **one validate-first, all-or-recoverable
configuration-bundle transaction** (`save_config_bundle` → `apply_config_transaction`):
the whole submission is validated before any write, unknown fields are rejected, a
malformed `local.toml` is preserved, both files (`local.toml` + the per-stack file) are
journalled and atomically replaced, a mid-write failure rolls everything back, and a
pending journal is recovered (or blocks) before the next save. **Linked external source
trees are read-only** — `build`/host-test on a linked (symlinked) source return
`BLOCKED` and never write into it. **Leaf no-follow**: atomic config/owned-record/
journal writes refuse a pre-existing symlink leaf, and start logs open with `O_NOFOLLOW`.

Also verified: **manifest lifecycle validation at load** (`manifest.ManifestError`) —
every runnable component declares a valid readiness policy (`process`/`endpoint`/
`daemon-band`/`manual`/`external-systemd`); `readiness="endpoint"` requires an endpoint
marked `ready = true`; argv token grammar, placeholders, pre/post step kinds, and env
names are validated. **`@file:` secrets fail closed** — a missing/unreadable/empty
secret raises `CommandError` and blocks the launch/build (never a blank value); invalid
`@env`/env names are rejected. **Endpoint readiness is enforced into start**: a
`readiness="endpoint"` component is only treated as up once every `ready=true` endpoint
is present (bounded); if they never appear the launched process is SIGTERM-cleaned and
the start is reported unverified/failed. **`DEGRADED` is not healthy** — it never counts
as already-running and never triggers a duplicate launch (returns blocked with verified-
stop guidance). A **typed lifecycle outcome model** (`core/outcomes.py`: `Outcome` +
`CompResult`, success/verified derived from the typed outcome) is in place and unit-tested.

Also verified: **verified stop is typed and truthful** — `Lifecycle.stop` returns a
`CompResult` and a stop is `STOPPED` only when the owned process ceased AND every
`ready=true` endpoint disappeared (otherwise `STILL_RUNNING` / `ENDPOINT_STILL_PRESENT`
/ `UNVERIFIED` / `MANUAL_REQUIRED` / `ALREADY_STOPPED`, no SIGKILL). `services.stop`
aggregates these (`applied_ok`) so `ActionResult.ok` reflects real cessation; **band/
interactive markers clear only after a verified stop**. **Restart** aborts if the stop
is not verified; **`stop_owners`** blocks the target launch if any conflicting owner is
not verified-stopped; **cascade** treats an interactive dependent as `MANUAL_REQUIRED`
and a failed dependent blocks the parent. A **required (non-optional) manual/interactive/
systemd component makes an applied start non-success** (`MANUAL_REQUIRED`, with the exact
operator command). **Generated config is never written into a linked external source**
(`write_config_files` skips it). **Runtime markers use a centralized safe-path API**
(`Paths.mutable_leaf`/`safe_unlink`/`contains`): marker writes and deletes reject
symlink-leaf and escaping paths; the **web job-log selector** accepts only an approved
`logs/<name>.log` (validated component, `.log` suffix, contained, non-symlink).
**Config-journal trust hardening**: the journal is versioned with **logical target kinds**
(`local`/`stack`) + runtime-relative paths through an allowlist — a malformed, unreadable,
wrong-schema, duplicate, unknown-kind, absolute, or traversing journal **blocks
fail-closed** (recovery-required, journal retained, zero mutation) and never touches an
arbitrary path; an existing-but-unreadable target is not treated as absent.

Also verified: **the start loop is now driven by typed `CompResult`s** — each component
yields one `Outcome` (`ALREADY_HEALTHY`/`VERIFIED`/`BLOCKED`/`FAILED`/`UNVERIFIED`/
`MANUAL_REQUIRED`); `ActionResult.results` carries them and `ActionResult.ok` derives
ENTIRELY from those outcomes (no `failed`-list/prose side channel). **Stop ownership
retention is correct**: records/markers are cleared only after BOTH process cessation
AND ready-endpoint disappearance — a lingering endpoint (or a no-record-but-endpoint-
present case) yields `ENDPOINT_STILL_PRESENT` with evidence retained, and a partial
multi-record stop never discards evidence. **Unobserved-launch cleanup is PID-reuse-safe**
(`_terminate_unobserved` re-checks start-time + session leadership before signalling).
**Required vs optional post-start**: a step marked `required` runs synchronously, bounded,
shell-free; its result gates `VERIFIED` and a failure triggers verified cleanup; the
launcher now checks exec/tcp return codes; optional steps remain detached/scheduled and
never imply verified setup. **Optional post-start scheduling is truthful**: `spawn_post_start`
returns a typed `PostStartSchedule(ok, detail)` covering every scheduling-stage failure
(launcher render, launcher write, log/dir setup, spawn, runtime containment) — these are no
longer swallowed, and `_run_post_start` reports `optional post-start scheduled` only on
confirmed success, else a VISIBLE non-gating `optional post-start could NOT be scheduled: …`.
**Descriptor-containment failures are typed, not crashes**: `runtime_fs` raises
`PathContainmentError` (a `ValueError`, not an `OSError`) for a symlinked/non-directory
runtime parent; `jobs.run_job` (typed `FAILED`/rc 126, runner not invoked), `Lifecycle.start`
(log setup inside the typed boundary → `StartLaunch(ok=False)`), and required post-start
(typed `FAILED` `JobResult`) all catch it at the correct boundary so no exception leaks to
the CLI/web layer.

Also verified: **wrapper execution-time revalidation** — generated wrappers no longer
mutate the filesystem inline; they import the installed helper `core/wrapper_runtime.py`
which rebuilds `Paths(runtime_root)` at run time and re-checks every pre-step destination
(rejecting a symlink leaf/parent introduced after generation), exiting non-zero before
exec on any unsafe path. The in-process controller start and the generated wrapper now run
**one shared pre-step engine** (`wrapper_runtime.apply_steps`) fed by **one normalizer**
(`commands.normalize_pre_steps`), so both run byte-identical steps with the SAME policy:
contained destination (escaping parent symlink rejected), `mkdir`/`chmod` refuse a symlink
leaf, `symlink` may replace an existing symlink/file leaf but never a real directory. There
is no second pre-step implementation. **Web-job build launcher fails closed** like normal execution:
a missing/empty `@file:` secret, bad env, or unresolved token blocks the build, and a
**pkg-config failure aborts** (never builds with missing flags); concurrent build/test
launchers get **unique runtime-owned names** written via the safe path API. **Config lock
is no-follow**: `config/.lock` is opened with `O_NOFOLLOW` under a containment-checked
path (a symlinked lock leaf is refused; lock-acquisition failure blocks mutation). Atomic
config writes **fsync the parent directory** after replace. **Daemon STATUS parsing is
bounded**: oversized (≥4 KiB), over-long-first-line (>1 KiB), and over-tokenized (>64)
responses are rejected fail-closed. The meaningless **`--live` flag was removed** (CLI +
service param) since the interface is not yet frozen.

Also verified: an **authoritative, descriptor-anchored runtime filesystem module**
(`core/runtime_fs.py`). A single internal traversal (`_walk_parent`) opens the runtime root
as a directory and walks each parent component with `dir_fd` + `O_DIRECTORY|O_NOFOLLOW`
(creating intermediates one component at a time when asked), so a parent swapped to a
symlink — or a non-directory component — between validation and use is refused AT THE
SYSCALL: there is no check-then-open/mutate race for runtime-owned state. Every operation
(`ensure_dir`/`mkdir`, `atomic_write`/`write_marker`/`write_launcher`,
`open_log_append`/`open_log_truncate`/`open_lock`, `read_bytes`/`read_text`/`tail`,
`unlink`) acts on the leaf relative to the held parent fd (atomic write: temp leaf via the
parent fd → fsync file → chmod → rename via src/dst dir-fds → fsync the parent dir fd). The
runtime ROOT is the trusted anchor (it may itself be a symlink) but every component under it
is `O_NOFOLLOW`. The temp leaf for an atomic write is **collision-safe**: a unique random
nonce + `O_CREAT|O_EXCL|O_NOFOLLOW` with bounded retry, so a write never truncates or
consumes another (even same-process) write's temporary file. The **default real start-log
open** (`Lifecycle._real_spawn`) goes through `runtime_fs.open_log_append`; the **job-log**
setup goes through `runtime_fs.open_log_truncate` (the injectable spawn seam is preserved);
external logs (`jobs.tail_log`, the daemon `/tmp` log) remain direct `O_NOFOLLOW` readers.

Runtime-owned **READS** are now descriptor-anchored no-follow too: `config/local.toml`,
`config/secrets.toml`, `config/stacks/*.toml` (via `_load_runtime_toml` →
`runtime_fs.read_bytes`), `state/config-txn.json` + pre-images, and `profiles/*.toml` (via a
descriptor-anchored `runtime_fs.listdir` + per-file `read_bytes`). A symlinked/escaping/
malformed runtime config or profile contributes NO data from outside the root (it raises a
typed `ConfigError` diagnostic or is skipped); package-data defaults remain external package
reads. The **config-transaction deletion** path (recovery, rollback, and successful journal
cleanup) uses descriptor-anchored `runtime_fs.unlink`; a unlink that cannot complete safely
retains the journal and yields the typed `recovery-required` outcome. Both the runtime-state **write** AND **read** paths now go through it: `config._atomic_write`
(local.toml, per-stack config, and the config-transaction journal + pre-image read), the
interactive/running-band markers (read and write), the web-job build launcher, the web-job
state directories (`state/jobs`, `state/locks` via `ensure_dir`), the **install wrapper**
(`_write_wrapper` → atomic, executable), ownership records (read + delete), source-
transaction journals (read), the **`reset_config` deletion** (no-follow unlink), saved
profiles, and the **job log setup/write** (`jobs.run_job` → `runtime_fs.ensure_dir` +
`open_log_truncate`). Runtime-owned **reads use no-follow opens** (`read_bytes`/`read_text`/
`tail` open with `O_NOFOLLOW` — no check-then-open); job markers (`active_jobs`,
`log_running`), journals, and runtime-log tails go through them. The remaining direct
`os.open`/`mkdir` calls are deliberate and leaf-safe, NOT bypasses: `_real_spawn` opens the
start log append-only with `O_NOFOLLOW` under the contained `logs/` dir (the spawn function
is replaceable in tests, so it stays Paths-free), `jobs.tail_log` opens EXTERNAL logs (e.g.
the daemon's `/tmp` log) with `O_NOFOLLOW`, and the source-config writer uses descriptor-
anchored `os.open(..., dir_fd=, O_DIRECTORY|O_NOFOLLOW)` by design (see below). `/proc`
identity, package data, and operator `@file:` secrets are external by design.

**Descriptor-anchored managed-source transaction** (`core/source_fs.py`): the managed-source
mutations no longer use pathname `shutil.rmtree`/`os.rename`/`Path.exists()`/`is_symlink()` as
authority. All operate relative to a source parent walked from the runtime-root fd with
`O_DIRECTORY|O_NOFOLLOW`:
- **Recursive removal** (`rmtree_at`, used by `Installer._discard` for candidate/`.prev`/failed
  clones): removes the leaf relative to the held parent fd — a directory is recursed
  **no-follow** (`dir_fd`-relative `lstat`/`open`/`unlink`/`rmdir`), a symlink or regular leaf
  is unlinked without following, a special/unknown leaf **fails closed** (evidence retained).
  A linked external source is a **symlink leaf**, so removal drops only that runtime leaf and
  never touches the external target.
- **One held FD from staging through finalization** (`ManagedSourceTransaction`):
  `_stage_and_activate` opens the source parent ONCE (no-follow) and holds that fd across
  ALL phases — journal preflight, exclusive candidate creation, Git/copy/link staging,
  candidate provenance, `dest→.prev` and `candidate→dest` renames, active-source provenance,
  rollback, `.prev`/candidate cleanup, and final journal removal. `_activate`/`_finish_or_rollback`
  (recovery) likewise run under one held fd. Every leaf inspection (`txn.leaf_kind`), usability
  check (`txn.usable`, via the held fd — never `Path.is_dir()`), sibling rename (`txn.rename`,
  `src_dir_fd == dst_dir_fd == held fd`), recursive removal (`txn.rmtree`), candidate creation
  (`txn.create_candidate`), and `.prev` cleanup (`txn._prev_cleanup_ok`) operates relative to
  THAT fd, with `fsync` after durable transitions. A parent-path swap after the first rename
  cannot redirect any later rename/rollback/cleanup/provenance lookup into a different inode —
  they all keep hitting the original held inode (proven by a parent-swap-mid-transaction test);
  a swapped/unsafe parent fails closed (`recovery-required`). The flow no longer calls
  `self._discard(staging)`, `_discard_ok(prev)`, or `_active_source_usable(dest)` (ordinary
  paths). (The standalone `rename_child`/`leaf_kind`/`rmtree_at` helpers remain for single-shot
  callers.)
- **Retained-FD, inode-bound owned journal** (`runtime_fs.OwnedMarker`): the initial `planned`
  journal is created with `O_CREAT|O_EXCL|O_NOFOLLOW` under a descriptor-walked `state/source-txn`
  parent, RETAINING both the journal file fd AND a dup of the journal parent fd, and fsync'd
  (leaf + parent). A journal INJECTED after the absent-preflight (regular/symlink/special/stale)
  makes the create fail — the transaction blocks (`recovery-required`) and the injected leaf is
  **preserved**, BEFORE any candidate/dest/`.prev` mutation. Every state update (`prior-archived`,
  `activated`) writes through the RETAINED file fd, but only after verifying — through the held
  parent fd, no-follow — that the VISIBLE leaf is still this transaction's `st_dev`/`st_ino`,
  BEFORE and AFTER the write (a replacement swapped in mid-write is caught). Final removal
  re-verifies identity and unlinks via the held parent fd, then fsyncs the parent AFTER the
  unlink; a replacement inode is refused and left untouched. If journal ownership is lost at any
  point the journal is RETAINED (never removed/cleared), the prior source is restored via the
  held fd where a slot was freed, and the transaction returns `recovery-required` (a distinct
  internal `_JournalLost` path — never `failed-clean`). A tiny window between the final identity
  observation and the `unlink` syscall is an unavoidable same-account namespace race, documented
  as such (not claimed stronger than the kernel provides).
- **Retained candidate / link identity handles** (`CandidateHandle` / `LinkHandle`):
  * clone/copy — `txn.create_candidate` creates the candidate dir through the held fd (any
    pre-existing leaf of any kind fails closed) and RETAINS a no-follow FD + `st_dev`/`st_ino`;
    Git and the local copy use its **FD-pinned path** `/proc/<controller-pid>/fd/<candidate-fd>`
    (follows the inode through the activation rename), never the mutable leaf name;
  * link — `txn.create_link` records the leaf's no-follow `st_dev`/`st_ino`, the exact `readlink`
    string, and the validated local target; provenance evaluates ONLY that verified target
    (`local_target`), never a staging/dest symlink whose identity has not just been proven.
  Before archiving, tightly before the promotion rename, and again on the ACTIVE leaf after the
  rename, `txn.verify_candidate`/`verify_link` re-confirm the leaf is still exactly the captured
  handle (candidate: same-inode real directory; link: still a symlink with the same inode +
  `readlink` + directory target). A substitution (symlink-outside, regular file, replacement
  directory, or link retarget) → the substituted leaf and journal are **retained as evidence**
  (never recursively deleted), the prior source is restored where a slot was freed, and the
  outcome is `recovery-required` (never `failed-clean`). The local fallback fills the candidate
  **per-entry** (no `dirs_exist_ok`); failed clone/copy cleanup uses `txn.rmtree`; every handle
  fd closes on transaction exit even after its inode becomes unreachable via substitution.
- **Durability ordering**: write+fsync `planned` journal → `dest→.prev` → fsync held parent →
  journal `prior-archived` → `candidate→dest` → fsync held parent → journal `activated` →
  verify final provenance → remove `.prev` + fsync parent → remove journal only after cleanup.
  Recovery resolves each durable state. (`OwnedMarker` writes loop until the FULL payload is
  written — no partial-`os.write` truncation.)
- **Handle-safe staging cleanup** (`_cleanup_owned_staging`): every staging-cleanup path (failed
  pre-activation provenance, clone-before-fallback reset, fallback version mismatch, no-local
  failure, copy failure, `failed-clean`) verifies the leaf still matches its `CandidateHandle`/
  `LinkHandle` and removes it ONLY when proven — a substituted replacement is retained as
  evidence, never recursively deleted merely because it kept the expected name; an intact
  controller-owned candidate IS cleaned so no empty candidate dirs are left behind. If a clone
  reset finds a substituted candidate it fails closed (recovery-required) rather than recreating.
- **Reverify active handle around final provenance** (provenance evaluation can take time, so a
  swap can occur during it): after `verify_active` SUCCEEDS the active `dest` is re-verified
  against the captured handle before ANY `.prev`/journal removal — a mismatch retains the journal,
  `.prev`, and substituted active leaf and returns recovery-required. On provenance FAILURE,
  `_rollback_bad_active(handle)` removes `dest` ONLY after re-proving it is still the captured
  candidate/link (never a pathname-only `rmtree` of an unverified replacement); an unverified
  destination is retained with the journal.
- **Recovery uses an owned journal handle** (`runtime_fs.open_existing_marker`): recovery opens
  the existing regular journal no-follow (retaining file + parent fds), reads and validates the
  payload THROUGH that fd, and removes it only via `OwnedMarker.remove()` (identity re-verified,
  then parent fsync). A journal replaced after recovery validated it but before removal is NOT
  removed — the replacement and source evidence are retained (recovery-required). The old
  pathname-only `_unlink_journal` is gone.
- **Controller-pinned staging paths**: clone, `git -C`/checkout, the
  local copy, and BOTH the pre- and post-activation provenance checks receive ONLY the
  candidate FD-pinned `/proc/<lhpc-controller-pid>/fd/<held-candidate-fd>` — bound to the LHPC
  controller pid (NOT `/proc/self/...`, whose `self` for a child Git process is Git), backed by
  the still-held fd, so a parent swap after the check cannot redirect them. A real-`git`-clone
  test proves the
  candidate lands in the held parent and a post-FD parent swap cannot redirect the clone outside.
  (Same-account caveat: this closes the parent-swap TOCTOU for source mutation; it is not a
  defence against an attacker who already shares the account.)
- **Provenance inside the durable transaction**: the order is stage → **candidate provenance
  gate** (a not-ok candidate never activates; active source untouched) → durable journal →
  held-FD activation → **active-source provenance verified INSIDE the transaction, before any
  `.prev`/journal cleanup**. A post-activation mismatch rolls back to the prior via the same
  held FD and RETAINS the journal (evidence) — so a failed final check can never leave a
  completed-but-unverified source with the rollback evidence already deleted. `.prev` removal
  and journal clear happen only after the active source is proven usable AND provenance-verified
  (proven by tests: forced post-mismatch restores the prior + retains the journal; a clean
  success clears both).

**Collision-resistant journal identity**: a source-transaction journal filename is bound to the
FULL managed runtime-relative source path — `<basename>-<full-sha256(source_rel)>.json` (a
complete 64-hex digest, domain-separated, not truncated) — so `src/a/app` and `src/b/app` never
share a journal. The payload carries the exact `source_rel` AND a **transaction id** (a
domain-separated SHA-256 of the per-transaction candidate name, which itself carries a unique
pid+monotonic nonce). Recovery independently re-derives the filename identity, refuses any
journal whose filename does not match its declared source, whose destination is not an exact
manifest-managed source, whose candidate/`.prev` names are non-controller, OR whose recorded
`txn_id` is absent (legacy) or altered. A legacy basename-only or txn_id-less journal is
therefore **retained + blocking** (never silently migrated or deleted).

**Shared, fail-closed process-tree termination** (`core/proctree.py`): the bounded command
runner AND the detached build/test launcher use ONE tested `terminate_session(token, …)`. The
token is a **typed immutable `SessionToken`** (frozen dataclass: leader pid, start time, sid,
pgid — all positive) captured with `capture_session_token()` immediately after spawn; the result
is a **typed `Termination`** enum (`TERMINATED` / `ALREADY_CEASED` / `UNVERIFIED` / `INCOMPLETE`,
with `.ok` true only for the first two). Termination **fails closed**: a `None`, non-`SessionToken`,
or incomplete token returns `UNVERIFIED` and signals nothing — a bare pid/sid/pgid is never
accepted as authority. With a complete token the owned pgid is signalled ONLY while ownership is
provable — the leader pid is alive AND the live identity EQUALS the token (frozen-dataclass
equality), OR the leader is gone and a live session member remains. A leader pid that is alive
but does not equal the token is a recycled pid, so its session id is not trusted and nothing is
signalled (this closed a real bug where an ambient-session fallback could `killpg` the
controller's own group). Signalling covers **every process group in the private session**, not
just the leader's: `session_member_details()` enumerates live `(pid, pgid)` members of the
owned session, so a descendant that calls `setpgrp()` (new group, same session) is still reached
— while the controller's own group is always excluded. TERM→bounded-KILL still kills a
TERM-ignoring child after its parent exits; zombies count as ceased. **Session-escape (`setsid()`) is documented, not guessed**: the
termination model covers every verified process group that remains in the ORIGINAL private
session — including a `setpgrp()` descendant (new group, same session) — but a descendant that
calls `setsid()` leaves that session and is OUTSIDE the proven ownership set. It is neither seen
by `session_members` nor claimed as killed (a regression asserts the escapee is not a session
member); the model never falsely reports such a process terminated, and `terminate_session`
returns `INCOMPLETE` whenever a verified in-session member survives escalation. The typed `Termination` is now surfaced on `CommandResult.termination` (and
`.may_still_be_running`) by `RealCommandRunner`, so a timeout that could not be proven fully
terminated is visible to callers rather than assumed dead. The launcher calls the shared helper,
not a duplicated string.

Post-activation provenance is enforced **inside** the durable transaction (`_activate`'s
`verify_active`, which rolls back via the held FD and retains the journal on mismatch);
`_adopt_done`'s provenance evaluation is therefore **display-only** and never raises a NEW
failure after `.prev`/journal have been cleared.

**Detached launcher runtime** (`core/build_launcher_runtime.py`): the generated build/test
launcher is now a THIN wrapper — it embeds an immutable spec literal (`runtime_root` + runtime-
relative lock NAMES, never trusted absolute paths) and calls `build_launcher_runtime.run(spec)`.
ALL security-sensitive behavior lives in that tested module: it rebuilds `Paths(runtime_root)`
and opens every lock via `runtime_fs.open_lock` (a FULL parent NO-FOLLOW walk) and scans the
journal via `runtime_fs.scandir_nofollow`, so a symlinked/replaced parent ANYWHERE in the
lock/journal path — `state/`, `state/locks/`, `state/source-txn/`, or a lock/journal leaf —
fails closed BEFORE any source access (tested for symlinked lock-, index-, and journal-
**parents**, not just leaves). It performs the index-lock → journal-preflight → source-lock
handoff, strict positive per-step timeout parsing (malformed → fail safe, never unlimited),
bounded `pkg-config`, `shell=False` step spawning with output streamed to the inherited job
log, and process-tree termination via the shared `proctree` session token — and releases
EVERY acquired fd in `finally`, including partial-acquisition failure paths. The generated
string contains no lock/journal/timeout/spawn logic.

Generated component configuration uses **three explicit destination policies**: a
`{runtime}/...` path resolves through `Paths.under` and is written via `runtime_fs`; a
RELATIVE path is written under the managed source root by a **descriptor-anchored walk**
(`_open_source_parent`/`_write_source_config`) that creates and opens each path component
RELATIVE TO ITS PARENT fd with `O_DIRECTORY|O_NOFOLLOW` — immune to a symlink-swap race, so a
`source/conf -> outside` link can never get `outside/newdir` created and the leaf is
`O_NOFOLLOW` (no symlink clobber); an arbitrary absolute path / unknown `{placeholder}` /
`..` traversal is REJECTED. **Base-file READS use the SAME descriptor-anchored, `O_NOFOLLOW`
traversal** (`_read_source_base`) — no check-then-open, so a base file or parent swapped to a
symlink after validation cannot be followed. `config_file.base` is validated at manifest
parse time and enforced (descriptor-anchored) at read time; a linked external source is never
written (`linked-readonly`). Stale interactive-marker cleanup (`_safe_unlink`) is
**non-throwing** — a `PermissionError`/`OSError` (not only a containment error) from the safe
unlink is swallowed and returned typed, so a post-launch cleanup failure can never convert a
completed start into an unhandled exception. **`pkg-config` failure fails closed on the NORMAL build
path** too (`build_step_argv` raises with the package name + bounded stderr; invalid
package names rejected). **All daemon CONF reads share one bounded parser** — `apply_set`
read-back now uses it (per-token length cap + duplicate-key policy + ANSI strip), so an
oversized/malformed read-back is a failed confirmation, not a lucky-token match.
**Job markers are PID-reuse-resistant**: a marker records full process identity
(start-time/pgid/sid/exec/argv-fingerprint via the shared `core/procident.py`), and
`active_jobs()` verifies identity (a recycled PID is not an active job; a symlinked marker
is never followed). **Concurrent post/job launchers get unique runtime-owned names** (no
overwrite). **Endpoint declarations are validated** at manifest load: malformed TCP
addresses rejected, a `ready=true` TCP host must be loopback (readiness never probes a
remote), IPv6 `[::1]:port` supported, and an `external` endpoint cannot be a readiness
gate. Manifest validation and the runtime readiness/cessation probes now share **one
host/family-aware endpoint parser** (`probes/endpoints.py`): a `ready=true` TCP endpoint is
matched by port **and** declared family — an IPv6-only listener never satisfies an IPv4
loopback endpoint (and vice-versa), while an IPv4 `0.0.0.0` / IPv6 `::` wildcard does;
`localhost` is satisfied by either family. IPv4 and IPv6 evidence stays distinct.
**Controller resource-operation locks** (`core/reslock.py`): an exclusive `flock` per
canonical resource key under `state/locks/` serializes mutating operations, names the
holder on conflict, and is auto-released by the kernel if the holder dies (intrinsic
stale-lock recovery). Lifecycle coordination lives in the **PUBLIC** `start`/`stop`/
`restart` methods (the authoritative locked entry points), so a DIRECT service call is
guarded identically to a CLI/web call. Each acquires, in ONE stable sorted order, the
per-stack `lifecycle.<id>` lock, a `claim.<resource>` lock per EXCLUSIVE/PROVIDER resource
(radio claims scoped per band, mirroring `run_blockers`), and — for `start`/`restart` — the
source-path lock(s). The guard is **re-entrant within a service instance** (keys already
nest without self-contending; the owner/dependent keys are pre-acquired in the outer bundle
so a cross-target owner-stop/cascade cannot bypass another target's coordination or
deadlock. Re-entrancy is tracked **per THREAD** (`threading.local` recursion counts), not a
process-wide set — the web app shares one `ControllerService` across threads, so only nested
calls in the SAME thread skip re-acquisition while an INDEPENDENT thread contends through
`reslock` and receives `ResourceBusy`. Locks are non-blocking — a real conflict fails fast,
naming the holder. **`--live` is fully removed** (rejected as an unknown option).

Synchronous **build / host-test / uninstall / source-dependent start** run under ONE atomic
source-operation guard (`_source_operation_guard`): acquire the transaction-INDEX lock →
recover + validate journals → block (fail closed) if ANY unresolved journal remains →
acquire all affected source-path locks (stable sorted) WHILE still holding the index lock →
release the index and run the operation under the source locks. There is no preflight/acquire
gap: a journal that a failed transaction retains is caught under the index lock before the
source locks are taken. Source recovery (`_finish_or_rollback`) routes every journal removal
through ONE safe helper (never raises — a failed unlink yields recovery-required), treats a
**dangling active symlink as NOT a usable source**, and retains the journal + candidate/prior
evidence whenever an archived-prior removal or restoration is uncertain.

Generated source-config writes prove containment **before any mutation**: the relative path
is split into components, each intermediate directory is created one component at a time and
rejected if it crosses a symlink (so `source/conf -> outside` never gets `outside/newdir`
created), and the leaf is `O_NOFOLLOW`. Runtime-owned reads use no-follow `runtime_fs`
primitives (`read_bytes`/`read_text` open with `O_NOFOLLOW` — no check-then-open); the job
markers (`active_jobs`, `log_running`) and journals read through them.

This pass also: **`jobs.run_job` sets up the runtime log SAFELY (O_NOFOLLOW create/truncate)
BEFORE executing** — a symlinked/inaccessible log leaf (or a failure to persist output) is a
TYPED `FAILED` result and the command is NOT run, never a silently-successful job with a
missing log; required post-start gates `VERIFIED` on this. **Version selection fails closed**:
`pinned` requires a configured exact pin (and HEAD must equal it), `stable` requires a
configured-or-independently-selected tag (verified `--exact-match`); a fallback/link cannot
report a version-selected success it cannot prove. The source-transaction guard fixes a
**self-contention bug** (recovery runs under the index lock only, then the target source
lock is taken — so a valid retained journal for the target is recovered through adopt/update
instead of becoming permanently busy). `_activate()` returns **structured state**
(`activated`/`failed-clean`/`recovery-required`): on recovery-required the candidate and
prior trees are **preserved** (never blind-discarded) and a failed journal unlink is itself
a typed recovery-required, never an untyped exception. A malformed/unresolved journal now
also blocks synchronous **build, host-test, and uninstall** (the global block previously
wired only into adopt/update). A `ready=true` **Unix/path endpoint must be runtime-contained
unless explicitly external** — an outside-root endpoint can never gate readiness or
cessation, and external endpoints are observe-only.

Also verified: **source activation is a durable, strictly-trusted transaction**. The
journal (`state/source-txn/<name>.json`, schema v2) stores ONLY logical runtime-relative
names — never trusted absolute paths. Recovery derives real paths from the runtime root,
validates every field/state, and requires the candidate/prior names to match the
controller's `.<name>.candidate-<n>-<n>` / `.<name>.prev` patterns; an absolute, escaping,
malformed, or non-controller-named journal is **retained and its source blocked** (never
followed or deleted). Recovery runs under the per-source lock and finishes or rolls back so
the active tree is never left missing; the journal is removed only once the active source
is safely in place. The lock is keyed by the **canonical managed source path**, so
consumers of one shared checkout (chat + igate both use `src/LoRaHAM_Daemon`) serialize on
ONE lock. The whole adopt acquires the **source-transaction INDEX lock first**, then the
canonical source-path lock (stable global order: index → sorted source paths), and holds
both across recovery→candidate→verify→activate→cleanup. Recovery runs inside that boundary,
and **any unresolved/malformed journal blocks ALL source mutation** — not just its own
source: a journal whose source cannot be safely derived (or whose filename does not match
its declared source) is retained and blocks every source operation until an operator
resolves it. A **local fallback AND a `link`-strategy checkout must satisfy the requested
version** before activation (`pinned`→exact HEAD, `stable`→exact tag, `dev`→branch, same
exact policy as copy/clone); a mismatched fallback or link never becomes active and never
reports a version-selected adoption it cannot prove. The source
lock is now applied to **build, host-test, uninstall, update/install-force, and recovery**
(build/host-test lock all distinct source paths in sorted order — no deadlock), so they
contend with an update of the same shared checkout. Dirty and linked sources block
destructive updates. `_activate` now **retains the journal and returns recovery-required**
when a prior-source restoration is uncertain (it never deletes the journal while the active
tree may be missing), and recovery **validates the journal filename against its declared
source identity** so a journal cannot point recovery at a different source. **Detached web
build/test job launchers perform a race-free index-to-source handoff**: the launcher holds
the source-transaction INDEX lock, verifies NO unresolved journal in `state/source-txn/`,
acquires the canonical source-path flock(s) for its whole lifetime, and only THEN releases
the index lock (the kernel releases the source locks on exit/death). While the index lock is
held no new journal can appear and the source lock is already taken, so a concurrent
update/uninstall cannot race the job and a retained journal makes the job fail visibly in
its log. A malformed/unresolved journal also blocks synchronous build/host-test/uninstall.

Generated component configuration is now a **structured result** (`write_config_files()`
returns `ConfigWrite` records: written / linked-readonly / no-base / failed). A write
failure is no longer swallowed: for an auto-started component, a `failed`/`no-base` config
generation **blocks the launch** (`BLOCKED`, naming the path) so it never starts with stale
or absent configuration. Source-tree config writes go through a dedicated contained writer
(atomic, `O_NOFOLLOW` leaf, fsync) kept separate from the runtime-state `runtime_fs` policy;
a linked external source is never written into (reported `linked-readonly`).

The **strict daemon CONF parser** now requires the first token to EQUAL the prefix exactly
(`STATUSX` is rejected), and every remaining token to be a well-formed non-empty
`KEY=VALUE` (a bare/malformed token, empty key/value, illegal key, control character in a
value, or a duplicate key fails the WHOLE response closed).

Process-identity & stop truth (this pass): a launch identity is **complete only with a
non-empty observed argv** — an SHA-256 of an empty cmdline (`argv_len == 0`) is not a valid
argv identity. The complete identity is **captured once at command observation and passed
forward** to the ownership write (no silent re-read/substitution). Cessation is decided by
`_proc_ceased`/`_original_ceased`: a process is proven gone only on `/proc` ENOENT, a
zombie, or a confirmed start-time mismatch (PID reuse); a **transient `/proc` read error is
never cessation** — the ordinary stop path keeps the ownership record and reports
`UNVERIFIED`. **Ownership-record removal failure** now yields `UNVERIFIED` (record retained)
rather than a silently-swallowed clean `STOPPED`. Source activation **refuses to discard an
orphan `.prev`** (a `.prev` with no active journal blocks the operation instead of being
blind-removed).

Also verified (this pass): the **TX-test STATS read uses the ONE bounded daemon parser**
(`daemon_control._query`) — an oversized/malformed STATS fails closed to `None`, never
parsed raw. **Failed-launch cleanup is truthful**: `_terminate_unobserved` returns `True`
only when cessation is actually verified (no SIGKILL), and a launch that can't be owned and
can't be proven ceased becomes a typed **`UNVERIFIED`** (residual process), not `FAILED`.
**Ownership requires a COMPLETE identity** — an observed but incompletely-read `/proc`
identity is refused (`record_launch`→False).

Also verified (this pass): **required post-start is truly typed** — `_run_post_start`
returns an explicit `bool|None` (no `Outcome.ok` AttributeError); a required failure yields
`UNVERIFIED` and verified cleanup, exercised through `ControllerService.start()`.
**Ownership-record writes are mandatory** — `record_launch` returns success/failure via
`runtime_fs` (symlink-leaf refused, fsync'd); a start is never reported owned/verified
unless the record persisted, else the just-spawned session is SIGTERM-cleaned.
**`spawn_job` truncates logs with `O_NOFOLLOW`** before any write (a symlinked log leaf
makes the job not start). **Stop and restart carry typed `CompResult`s in
`ActionResult.results`** (restart preserves stop results + start results, including the
aborted-restart evidence).

Also verified: **bounded log retention** — `prune_logs()` runs at operation boundaries
(build/web-job), keeping at most a named count/byte budget of `logs/*.log`, never deleting
a log that belongs to an **active job** (live evidence is preserved) and never following a
symlink. **`reslock` is wired into `build`, source-`update`, and `uninstall`** (an
uninstall only unlinks a linked source, never its external target).

The **`repair` and `rollback` verbs were removed** (CLI, service, web buttons, docs):
they were redundant with `install`/`update --force`, which re-adopt a fresh source. The
internal source-activation journal/recovery is retained (it just prevents an interrupted
update from leaving the source missing — not a user-facing rollback feature).

The `runtime_fs` migration has extended to **job-log writes** (`jobs.run_job` truncates
the log with `O_NOFOLLOW`, refusing a planted symlinked log leaf) and the **bootstrap
config/secret/prune writes** (atomic, no-follow, parent-fsync via `runtime_fs`).

**`reslock` is now wired into start/stop/restart**, re-entrancy-safely: the lifecycle
lock (`lifecycle.<stack>`) is taken at the EXTERNAL dispatch (`run_action`, used by both
CLI and web), while `restart`/`stop_owners`/`cascade` call `start()`/`stop()` directly and
so never re-acquire it; the lock is non-blocking, so a cross-target nesting fails fast
rather than deadlocking. A concurrent external start/stop/restart of the same stack is
refused with a named-holder diagnostic.

All runtime-layout and source-parent directory creation goes through descriptor-anchored
`runtime_fs.ensure_dir` (no raw `Path.mkdir` remains in the bootstrap/adopt paths).
`StartLaunch`→`CompResult` is a deliberate two-layer split — `StartLaunch` is the internal
raw-launch result; the authoritative outcome is the service layer's `CompResult` in
`ActionResult.results` — not an open item. None of this is hardware/RF-validated.

## Not production-ready
Independent review remains required.
