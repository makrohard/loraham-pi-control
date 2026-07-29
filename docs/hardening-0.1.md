# Hardening — safety model & evidence

What the controller now guarantees, and how it is enforced. Not a history; these
are the current rules.

## Contents

- [Structured command execution (no shell)](#structured-command-execution-no-shell)
- [Verified owned-process stopping](#verified-owned-process-stopping)
- [Path containment](#path-containment)
- [Truthful outcomes & readiness](#truthful-outcomes--readiness)
- [Staged update / activation](#staged-update--activation)
- [Uninstall protection](#uninstall-protection)
- [GET no-network guarantee](#get-no-network-guarantee)
- [Web path safety & confirmation](#web-path-safety--confirmation)
- [Atomic, locked config writes](#atomic-locked-config-writes)
- [Package-install correctness](#package-install-correctness)
- [Test commands](#test-commands)
- [Further guarantees, by area](#further-guarantees-by-area)
- [Still open](#still-open)

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
- Manual `start/` wrappers are retired: lhpc starts services itself and the dashboard shows
  the copy-paste command for interactive components, generated from the same spec
  (`commands.display_command`).

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
  containment-checked helper as save/load. Tests: `tests/test_runtime_fs.py`.

## Truthful outcomes & readiness
- `start` returns failure unless every required component reached a verified healthy
  state. A daemon start verifies each requested band's CONF socket; a launch
  that never exposes its socket is a failure, not a warning. A dependent is not
  started when daemon readiness failed. `update` aggregates and reports nonzero on
  partial failure. CLI exit status, web flash and summaries agree. Tests: `tests/test_post_start.py`.

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
  configured commit; dirty or linked working trees are never overwritten. Tests: `tests/test_staged_update.py`, `tests/test_source.py`.

## Uninstall protection
- Uninstall refuses while a target component is running, never removes a source
  still referenced by another component (shared checkout — chat/iGate share
  `LoRaHAM_Daemon`; kiss/serial-kiss share `loraham-kiss-tnc`), and never deletes
  config, secrets or profiles. Tests: `tests/test_uninstall_safety.py`.

## GET no-network guarantee
- No GET route runs a network or git-remote command. Update freshness is an explicit
  action (`lhpc source-check`). A recording-runner spy hits every GET route and
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
  refuses and reports it. Tests: `tests/test_config.py`, `tests/test_runtime_fs.py`.

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

## Further guarantees, by area

Each line names a rule that is implemented and covered by the suite. The reasoning lives in the
module docstrings, the proof in the tests — this file names the guarantee.

**Execution.** Structured argv everywhere (above). `@file:` secrets fail closed — a missing,
unreadable or empty secret blocks the launch or build rather than passing a blank value. A
`pkg-config` failure aborts a build instead of compiling with missing flags.

**Filesystem containment** (`core/runtime_fs.py`). One descriptor-anchored traversal opens the
runtime root and walks each parent with `O_DIRECTORY|O_NOFOLLOW`, so a symlink swapped in
mid-operation cannot redirect a write. Atomic writes fsync the parent directory after replace;
config, owned-record, journal and log leaves are opened `O_NOFOLLOW`. Containment failures are
typed (`PathContainmentError`), caught at each boundary — never an exception reaching the CLI or
web layer.

**Source transactions** (`core/install.py`, `core/source_fs.py`). An update clones a candidate
beside the destination, archives the prior to a transaction-owned `.prev`, activates by atomic
no-clobber rename, writes the ownership record, then removes the `.prev` — journalled at every
step. Removals are identity-bound (dev+ino+ctime), so a substituted leaf is retained as evidence
instead of deleted. Any unresolved or malformed journal blocks all source mutation until an
operator resolves it. Recovery works from the journal alone, through an owned handle.

**Locking** (`core/reslock.py`). Start, stop, restart, build, update, uninstall and clean take
named non-blocking locks; a contended operation refuses immediately, naming the holder, instead
of deadlocking. Nested internal calls reuse the held lock re-entrantly.

**Config.** A Settings save is one validate-first, all-or-recoverable bundle transaction: the
whole submission is validated before any write, both files are journalled and atomically
replaced, a mid-write failure rolls back, and a pending journal is recovered or blocks before the
next save. A malformed `local.toml` is preserved, never overwritten. Config lives at `0600`
(`local.toml`, secrets) or `0644` (per-stack files).

**Daemon control.** One bounded CONF parser for every read: oversized (≥4 KiB), over-long-line or
over-tokenized responses are rejected fail-closed. TX-mode changes are applied and read back — an
unconfirmed change blocks dependents and post-start.

**Start / stop / post-start.** Every component yields one typed `Outcome`; `ActionResult.ok`
derives entirely from those, with no prose side channel. A stop counts as verified only when the
owned process ceased **and** every ready endpoint disappeared; markers and ownership records
clear only then. Required post-start steps run synchronously and gate the verified result;
optional ones are detached and report a truthful scheduling outcome. Unobserved-launch cleanup
re-checks start time and session leadership before signalling (PID reuse).

**Jobs & logs.** Job markers are PID-reuse-resistant; job logs are truncated `O_NOFOLLOW` before
any write. `prune_logs()` keeps a bounded count/byte budget at operation boundaries and never
deletes a log belonging to an active job.

**Web.** GET routes run no network and no git; mutations are POST + CSRF + explicit confirm.
Per-stack config paths validate the id and band and are proven to stay inside `config/stacks/`.
Generated wrappers revalidate every pre-step destination at execution time through the same
shared engine the in-process start uses — there is no second implementation.

**Newer subsystems** carry their own model documents rather than a summary here:
[binary channel](provenance.md#the-binary-channel) (HTTPS + sha256 + pin equality + smoke gate,
one journaled transaction), [managed firewall](firewall.md) (default-deny, root-owned helper,
ownership-proven table), [webserver](webserver.md) (nginx + mTLS, two-CA PKI), and boot restore
(replays only saved configuration through the normal gated start path — see
[operations.md](operations.md#not-a-supervisor)).

## Still open

- **Independent review.** None of this has been reviewed by anyone outside the project.
- **`--live` is deliberately absent** from the CLI and service params: that interface is not
  frozen, so it is not offered.
- Two test promises still lack a widest-seam case (a real firewall-apply route test, a
  sandbox-safe boot-restore case) — see `tests/README.md`.
