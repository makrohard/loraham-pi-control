# PLAN-U1 — refusals on the update paths name a remedy; one channel table

Goal: a refusal on an install / update / self-update / build / binary-channel path tells the
operator what to do. Two parts: (1) remedy texts plus a permanent guard test; (2) one table of
update commands in the docs. No behaviour change: no branch, no signature, no persisted format.
Line numbers are on the base, `e38803b`.

## Contract

Every `ActionResult(ok=False, …)` on those paths carries a cause (the existing `summary`) and a
remedy: a `next_commands` entry the operator can run, or a `details` line "nothing to run here —
<what is wrong on the box and who fixes it>". Wording models: the start gate's "rebuild first
(lhpc build <s>)" (`lhpc/core/service_lifecycle_ops.py:1194`) and F36's cause-then-commands
notice (`lhpc/core/service_firewall.py:705`).

## Inventory

Every literal-`False` `ActionResult` constructor in these scopes; script and full list: report.

| scope | bare | has remedy |
|---|---|---|
| `service_binary_ops.py` (whole module) | 11 | 22 |
| `service_selfupdate.py` (whole module) | 75 | 3 |
| `service_lifecycle_ops.py` `build` | 3 | 7 |
| `service_maintenance.py` `update`, `graywolf_upstream_update`, `_graywolf_upstream_update_locked` | 9 | 11 |
| `services.py` `install` (not in FILES; reported only) | 9 | 6 |

Seven of the bare constructors are the shared admission refusal `ActionResult(False, _adm.reason)`
(binary `:204`; self-update `:355`, `:559`, `:893`; build `:3373`; update `:1912`, `:957`). Its text is
worded once, in `_task_admission_blocked` (`service_selfupdate.py:1110`), for every operation,
and already names `lhpc self-update --recover-request`; `cons/S1`'s golden build test pins its
`next_commands: []`. The guard excludes it; it is a U2 item. `install.py` builds no
`ActionResult`: its refusals are `action.detail` lines inside the update result, and only (iv)
changes there.

## The measured refusals

- **(i)** Unsafe identity, `service_selfupdate.py:346` and `:873`, bare today. Gets
  `next_commands = ["git -C <root>/src/loraham-pi-control switch main", "lhpc self-update --apply"]`
  and a details line: the command fixes a detached HEAD or another branch; any other reason
  (listed at `services.py:495-586`) means restoring the install.sh layout.
- **(ii)** `service_maintenance.py:1786`. Graywolf has no git source, so `_with_source` is empty
  and `_unknown_stack` (`services.py:2843`) answers. A truthful text needs a new branch ("known
  stack, no source"): control flow, so U2. Proposed patch: `if self.stack(target):` refuse with
  "'<s>' has no git source — `lhpc build <s> --yes` re-fetches its pinned release;
  `lhpc update <s> --upstream --yes` takes the newest".
- **(iii)** The plan line at `service_maintenance.py:1807` says "fetch newest" but compares the
  branch tip while the apply fetches the `--source` version. It becomes "`{c.id}: upstream
  {fresh} — this update fetches the {source} version from …`". The "pinned: match" after it is the
  probe's true post-update state (`probes/source.py:128`) and stays. The "Next: … --yes" inside a
  `--yes` run comes from the CLI's `_apply_flow` (`adapters/cli/main.py:106-118`), which renders
  the plan before applying: control flow, a U2 finding.
- **(iv)** `install.py:689`. The failed-clean detail names `src/.<name>.prev` (the
  `_source_rel(...)` expression from `:642`), says to move it out of `src/`, and to retry with
  `lhpc update <comp> --yes`. Failed-clean also follows a generic activation error (`:2080`), so
  the text says "If … exists".
- **(v)** `service_lifecycle_ops.py:1194` already has its remedy and stays.

## Change, by kind (text only: new keyword arguments on existing constructors)

- Contention (busy lock, contended admission, "already in progress"): the retry command.
  Recovery states: `lhpc self-update --recover-request`. Unit problems: the
  `systemctl --user status|cat <unit>` the text names, plus `lhpc self-update --repair-integration`.
- A damaged file, a full disk, a foreign unit, or an internal defect: "nothing to run here — …",
  naming what the box's operator fixes.
- A binary retire refused because a file changed: `lhpc uninstall <s> --yes` (uninstall retires
  with force, `service_maintenance.py:2382`).
- Two refusals that listed commands as `details` lines (`service_selfupdate.py:361`, `:537`) move
  them to `next_commands`.
- Q1's typed outcomes (`TrackOutcome`, `ConfigRecovery`) sit on no refusal in these scopes; no
  remedy text goes there.

## Test (red before, kept as the guard)

New: `tests/repo/test_refusal_remedy.py`.

1. Guard: parse the scopes; fail on any bare literal-`False` constructor (admission excepted,
   named in the docstring) and on a scoped function that disappears. A whole-module negative
   invariant (tests/README.md rule 1 exception, with twins). Deviation from "drive each":
   driving 91 refusals through fakes is 91 setups.
2. Twin (i): `self_update_apply` with the identity probe stubbed to "detached HEAD" names the
   `git … switch main` command.
3. Twin (iv): a real `Installer` with a leftover `src/.repo.prev` refuses, naming that path and
   `lhpc update c --yes`; the folder stays.

Red before: all three fail on the parent (with `lhpc/` stashed).

## Channel table (docs)

One table in `docs/operations.md` § Install channels: situation → command → what you see.

| row | rests on |
|---|---|
| first install | `default_channel`, `service_binary_channel.py:68` |
| keep current at the release's pins | self-update, then a bare update; pinned = known-working, else the manifest pin (`_plan_source_groups`, `service_maintenance.py:425`) |
| follow dev / follow stable | the one-time dev fallback, `service_maintenance.py:333` |
| binary where published | `update_default_channel`, `service_binary_channel.py:80`; lag refusal `service_binary_ops.py:470` |
| known-working fallback after a failed update | a failed activation keeps the active source |
| a refused update | its `Next:` lines; graywolf: `graywolf_upstream_update`, `service_maintenance.py:923` |
| first start after an update | `service_lifecycle_ops.py:1194` |

`docs/cli.md` § update keeps its flags and drops the channel rule it repeats. The D1 table cites
owner lines in five files this batch edits; U1 corrects those numbers in one commit, its one
touch of `docs/architecture.md`.

## Risks

- A remedy wrong for some reason: each command was checked against the path it leads to; where
  the right command depends on the reason, the remedy is a sentence instead.
- Tests that assert changed text: `grep -rn` over `tests/` for each changed string; the modules
  that drive these refusals are run.
- The web never renders `next_commands` (no reference in `app.py`): command-only remedies are
  CLI-only. U2 finding; the web is outside FILES.

## Open questions (with recommendations)

1. FILES names `service_source*.py`, which matches no file; the source-update module is
   `service_maintenance.py` (architecture.md: "source update"). Recommendation: its update text
   lines are that entry. Done, flagged as a deviation.
2. `services.py` `install` (9 bare): U2, with the texts proposed in the report.
