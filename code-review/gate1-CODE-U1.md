# Gate 1 — CODE review request: U1

Please judge batch U1 of the architecture consolidation of loraham-pi-control: the plan (below) and its code commits. (1) The refusal-remedy contract: each literal-False ActionResult on the update, self-update, build and binary-channel paths — the shared admission refusal excepted, as the plan says — now carries a next_commands entry or a 'nothing to run here — …' details line; judge each remedy for truth (does the command fix the cause named?), the guard test and its two behavioural twins, and that no ok/summary/data/control flow changed. (2) The channel table in docs/operations.md: is each row true against the code the plan cites? (3) The 14 D1-table line corrections. Also judge the deviations the report names (the FILES interpretation for service_maintenance.py; the static guard instead of driving each refusal).

This file is your whole input: you have no repository access; use no connector, tool or web lookup.

## Answer form

| commit | verdict (OK / FINDING) | what |
|---|---|---|
| `d052c3b` U1: plan — refusals on the update paths name a remedy; one channel table | | |
| `22aa6e0` U1: refusals on the update, self-update, build and binary paths name a remedy | | |
| `2d14c32` U1: the update plan line says which version the update fetches | | |
| `5e68d4c` U1: docs — one table of what to run to keep each stack current | | |
| `4c152ee` U1: the invariant table follows the owner lines the remedy texts moved | | |
| `ad6d6c7` U1: CHANGELOG — refusals on the update paths say what to do | | |

Final line: GREEN / GREEN WITH NOTES / RED

## The plan (plans/PLAN-U1.md)

````markdown
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
````

## Commit list

- `d052c3b` U1: plan — refusals on the update paths name a remedy; one channel table
- `22aa6e0` U1: refusals on the update, self-update, build and binary paths name a remedy
- `2d14c32` U1: the update plan line says which version the update fetches
- `5e68d4c` U1: docs — one table of what to run to keep each stack current
- `4c152ee` U1: the invariant table follows the owner lines the remedy texts moved
- `ad6d6c7` U1: CHANGELOG — refusals on the update paths say what to do
- (this file and the code report are added by the report commit that follows)

## The code report (code-review/code-report-U1.md)

`````markdown
# Code report — U1 (refusal-remedy contract and channel table)

Base: `integration/0.12.0` at `e38803b`. On this branch U1 follows D1 (`8afa12f`).

## Commits

| sha | subject | files | test, red-before |
|---|---|---|---|
| `d052c3b` | U1: plan — refusals on the update paths name a remedy; one channel table | `plans/PLAN-U1.md` | — |
| `22aa6e0` | U1: refusals on the update, self-update, build and binary paths name a remedy | `lhpc/core/install.py`, `service_binary_ops.py`, `service_lifecycle_ops.py`, `service_maintenance.py`, `service_selfupdate.py`, `tests/repo/test_refusal_remedy.py` | 3 new tests; red-before **yes** on `d052c3b` (the parent) |
| `2d14c32` | U1: the update plan line says which version the update fetches | `lhpc/core/service_maintenance.py` | no test: a human sentence (tests/README.md rule 2); deviation named below |
| `5e68d4c` | U1: docs — one table of what to run to keep each stack current | `docs/operations.md`, `docs/cli.md` | — |
| `4c152ee` | U1: the invariant table follows the owner lines the remedy texts moved | `docs/architecture.md` (14 cited line numbers) | the D1 checker: `bad=0` |
| `ad6d6c7` | U1: CHANGELOG — refusals on the update paths say what to do | `CHANGELOG.md` | — |

**Red-before.**

- Command: `git worktree add <tmp> d052c3b`; copy `tests/repo/test_refusal_remedy.py` in; run
  `python -m pytest -q -p no:cacheprovider tests/repo/test_refusal_remedy.py`.
- Result: `3 failed in 1.12s`.
  - The guard lists 91 bare refusals.
  - Twin (i) gets empty `next_commands`.
  - Twin (iv) gets `'activation failed — active source untouched'`, which has no `.prev` path.

**Green after,** on `ad6d6c7`: the same command → `3 passed in 0.33s`.

## The contract and how the guard reads it

A refusal (`ActionResult` constructed with a literal `False`) needs one of two remedies:

- a `next_commands` argument other than the empty list literal; or
- a `details` argument containing a string literal with "nothing to run here — ".

Scopes:

- `service_binary_ops.py` (whole module);
- `service_selfupdate.py` (whole module);
- `service_lifecycle_ops.py` `build`;
- `service_maintenance.py` `update`, `graywolf_upstream_update` and
  `_graywolf_upstream_update_locked`.

What the guard does not see, by construction:

- A refusal whose `ok` is computed. For example `Update INCOMPLETE`, which carries
  `lhpc status --versions`.
- `_operator_outcome`, which empties `next_commands` on a partial success it rewrites. Its
  summary then says what to do.
- `install.py`'s per-component `action.detail` texts (inventoried below).

## Inventory (base line numbers, `e38803b`)

Produced by this script (an AST walk over the scopes, plus `services.py` `install` for
reference). The guard test uses the same rule.

```python
import ast, sys
SCOPES = {
 "lhpc/core/service_binary_ops.py": None,
 "lhpc/core/service_selfupdate.py": None,
 "lhpc/core/service_lifecycle_ops.py": {"build"},
 "lhpc/core/service_maintenance.py": {"update", "graywolf_upstream_update", "_graywolf_upstream_update_locked"},
 "lhpc/core/services.py": {"install"},
}
def lit(n):
    if isinstance(n, ast.Constant) and isinstance(n.value, str): return n.value
    if isinstance(n, ast.JoinedStr):
        return "".join(v.value if isinstance(v, ast.Constant) else "{…}" for v in n.values)
    if isinstance(n, ast.BinOp): return lit(n.left)+lit(n.right)
    return "<expr>"
def strs(n):
    return " ".join(lit(x) for x in ast.walk(n) if isinstance(x,(ast.Constant,ast.JoinedStr)) and isinstance(getattr(x,'value',None),(str,list)) or isinstance(x,ast.JoinedStr))
rows=[]
for f, fns in SCOPES.items():
    tree = ast.parse(open(f).read())
    for fn in ast.walk(tree):
        if not isinstance(fn,(ast.FunctionDef,)): continue
        if fns is not None and fn.name not in fns: continue
        for c in ast.walk(fn):
            if isinstance(c, ast.Call) and getattr(c.func,'id',getattr(c.func,'attr',None))=="ActionResult":
                okn = c.args[0] if c.args else next((k.value for k in c.keywords if k.arg=="ok"),None)
                if not (isinstance(okn, ast.Constant) and okn.value is False): continue
                summ = c.args[1] if len(c.args)>1 else next((k.value for k in c.keywords if k.arg=="summary"),None)
                det = c.args[2] if len(c.args)>2 else next((k.value for k in c.keywords if k.arg=="details"),None)
                nc = c.args[3] if len(c.args)>3 else next((k.value for k in c.keywords if k.arg=="next_commands"),None)
                has_nc = nc is not None and not (isinstance(nc, ast.List) and not nc.elts)
                nothing = det is not None and "nothing to run here" in ast.unparse(det)
                cls = "remedy" if has_nc or nothing else "bare"
                rows.append((f, c.lineno, fn.name, cls, lit(summ)[:110] if summ else "<none>"))
seen=set()
for r in rows:
    if (r[0],r[1]) in seen: continue
    seen.add((r[0],r[1]))
    print(f"{r[0].split('/')[-1]}:{r[1]}|{r[2]}|{r[3]}|{r[4]}")
```

Counts: 156 constructors.

| | count |
|---|---|
| already had a remedy | 49 |
| remedy added | 91 |
| shared admission refusal, excluded | 7 |
| `services.py` `install`, outside FILES (U2) | 9 |

| base file:line | function | cause (summary, base) | before | after |
|---|---|---|---|---|
| `service_binary_ops.py:105` | `binary_install` | Cannot install '{…}' from binary: {…} | has remedy | unchanged |
| `service_binary_ops.py:147` | `binary_install` | Binary install of '{…}' needs system packages that are not installed. | has remedy | unchanged |
| `service_binary_ops.py:121` | `binary_install` | Binary install of '{…}' refused: {…} | has remedy | unchanged |
| `service_binary_ops.py:133` | `binary_install` | The published binary or LHPC's pins changed since you confirmed (now: the published binary matches this LHPC's | has remedy | unchanged |
| `service_binary_ops.py:204` | `binary_install` | <expr> | bare | admission refusal: excluded, U2 |
| `service_binary_ops.py:207` | `binary_install` | Binary install of '{…}' blocked: {…} | bare | remedy added |
| `service_binary_ops.py:210` | `binary_install` | Binary install of '{…}' blocked: {…} | bare | remedy added |
| `service_binary_ops.py:218` | `binary_install` | Binary install of '{…}' blocked: {…} | bare | remedy added |
| `service_binary_ops.py:225` | `binary_install` | Binary install of '{…}' blocked: {…} | has remedy | unchanged |
| `service_binary_ops.py:315` | `binary_install` | Binary install of '{…}' blocked: {…} | bare | remedy added |
| `service_binary_ops.py:330` | `binary_install` | Refusing to install '{…}' from binary: component(s) running. | has remedy | unchanged |
| `service_binary_ops.py:186` | `binary_install` | Binary install of '{…}' blocked: moving {…} to its pin failed: {…} | has remedy | unchanged |
| `service_binary_ops.py:322` | `binary_install` | Binary install of '{…}' blocked: cannot create a staging directory ({…}) | bare | remedy added |
| `service_binary_ops.py:352` | `binary_install` | Binary install of '{…}' blocked: {…} | has remedy | unchanged |
| `service_binary_ops.py:438` | `binary_install` | Binary install of '{…}' failed: {…} | has remedy | unchanged |
| `service_binary_ops.py:304` | `binary_install` | Binary install of '{…}' blocked: {…} needs its pinned source checkout (run scripts live there) and it could no | has remedy | unchanged |
| `service_binary_ops.py:255` | `binary_install` | Binary install of '{…}' blocked: the existing {…} checkout is not provably ours ({…}). | has remedy | unchanged |
| `service_binary_ops.py:262` | `binary_install` | Binary install of '{…}' blocked: {…} is at {…}, the pin is {…} — the artifact's run scripts must come from the | has remedy | unchanged |
| `service_binary_ops.py:276` | `binary_install` | Binary install of '{…}' blocked: {…} has local changes — the artifact runs scripts from that checkout. | has remedy | unchanged |
| `service_binary_ops.py:506` | `_binary_pin_refusal` | Binary install of '{…}' refused: {…} | has remedy | unchanged |
| `service_binary_ops.py:477` | `_binary_pin_refusal` | Binary install of '{…}' refused even with --accept-pin-mismatch: its run scripts come from LHPC's own pinned c | has remedy | unchanged |
| `service_binary_ops.py:490` | `_binary_pin_refusal` | The published binary or LHPC's pins changed since you confirmed (now: {…}). Nothing was installed; review the  | has remedy | unchanged |
| `service_binary_ops.py:822` | `binary_retire` | Cannot retire the binary install of '{…}': the open transaction is not this one | bare | remedy added |
| `service_binary_ops.py:833` | `binary_retire` | Cannot retire the binary install of '{…}': {…} | has remedy | unchanged |
| `service_binary_ops.py:861` | `_retire_body` | Cannot retire the binary install of '{…}': {…} | has remedy | unchanged |
| `service_binary_ops.py:866` | `_retire_body` | Retirement of '{…}' is INCOMPLETE — its binary receipt cannot be read, so the installed files cannot be identi | has remedy | unchanged |
| `service_binary_ops.py:892` | `_retire_body` | Refusing to retire the binary install of '{…}': component(s) running. | has remedy | unchanged |
| `service_binary_ops.py:932` | `_retire_body` | Retirement of '{…}' is INCOMPLETE — the receipt was kept so the remaining files stay owned. | has remedy | unchanged |
| `service_binary_ops.py:955` | `_retire_body` | Removed the binary files of '{…}' but could not remove its receipt — resolve state/binary by hand. | bare | remedy added |
| `service_binary_ops.py:886` | `_retire_body` | Refusing to retire the binary install of '{…}': installed files changed since installation ({…}). | bare | remedy added |
| `service_binary_ops.py:910` | `_retire_body` | Cannot retire the binary install of '{…}': its receipt could not be removed | bare | remedy added |
| `service_binary_ops.py:906` | `_retire_body` | Cannot retire the binary install of '{…}': its files could not be moved aside ({…}) | bare | remedy added |
| `service_binary_ops.py:949` | `_retire_body` | Retirement of '{…}' is INCOMPLETE — the receipt was kept so {…} stays owned. | has remedy | unchanged |
| `service_selfupdate.py:302` | `self_update_check` | Self-update is unavailable (lhpc is not a git checkout). | bare | remedy added |
| `service_selfupdate.py:305` | `self_update_check` | Could not reach upstream: {…}. | bare | remedy added |
| `service_selfupdate.py:299` | `self_update_check` | Could not check upstream (unsafe runtime state). | bare | remedy added |
| `service_selfupdate.py:277` | `self_update_check` | Self-update check blocked: the migration journal is unreadable, corrupt or unsafe. No upstream check was made  | bare | remedy added |
| `service_selfupdate.py:283` | `self_update_check` | Self-update check blocked: the checkout is at an unexpected commit for a recorded migration transition. No ups | bare | remedy added |
| `service_selfupdate.py:355` | `self_update_apply` | <expr> | bare | admission refusal: excluded, U2 |
| `service_selfupdate.py:357` | `self_update_apply` | A task is starting right now (admission contended) — try the update again shortly. | bare | remedy added |
| `service_selfupdate.py:360` | `self_update_apply` | lhpc-web.service is running — stop it, update, then start it again. | bare | remedy added |
| `service_selfupdate.py:368` | `self_update_apply` | Could not acquire the controller-runtime lock (unsafe runtime state) — aborting without changes. | bare | remedy added |
| `service_selfupdate.py:371` | `self_update_apply` | A self-update is already in progress — try again shortly. | bare | remedy added |
| `service_selfupdate.py:374` | `self_update_apply` | Could not acquire the self-update lock (unsafe runtime state) — aborting without changes. | bare | remedy added |
| `service_selfupdate.py:339` | `self_update_apply` | Self-update blocked: {…} — resolve it before self-updating. | bare | remedy added |
| `service_selfupdate.py:346` | `self_update_apply` | Self-update blocked: unsafe controller identity ({…}). No changes were made. | bare | remedy added |
| `service_selfupdate.py:523` | `self_update_apply_operator` | refusing to stop/start services from a managed unit — run `lhpc self-update --apply` from an interactive opera | bare | remedy added |
| `service_selfupdate.py:559` | `self_update_apply_operator` | <expr> | bare | admission refusal: excluded, U2 |
| `service_selfupdate.py:561` | `self_update_apply_operator` | A task is starting right now (admission contended) — retry the update. | bare | remedy added |
| `service_selfupdate.py:537` | `self_update_apply_operator` | could not stop lhpc-web.service — stop it manually then retry | bare | remedy added |
| `service_selfupdate.py:552` | `self_update_apply_operator` | <expr>  — AND lhpc-web did NOT restart. Recover with: systemctl --user start lhpc-web.service | bare | remedy added |
| `service_selfupdate.py:597` | `_self_update_locked` | Self-update blocked: the migration journal is missing-but-present-unreadable, corrupt or unsafe. No changes we | bare | remedy added |
| `service_selfupdate.py:602` | `_self_update_locked` | Self-update blocked: the checkout is at an unexpected commit for a recorded migration transition. No changes w | bare | remedy added |
| `service_selfupdate.py:744` | `_self_update_locked` | <expr> | has remedy | unchanged |
| `service_selfupdate.py:752` | `_self_update_locked` | <expr> | bare | remedy added |
| `service_selfupdate.py:627` | `_self_update_locked` | Self-update blocked: the recorded migration transition is not authorised by a matching durable anchor. No chan | bare | remedy added |
| `service_selfupdate.py:677` | `_self_update_locked` | Refusing to self-update: could not durably record the config-migration intent before changing source. No chang | bare | remedy added |
| `service_selfupdate.py:850` | `self_update_trigger` | One-click update needs the managed web service (systemd). This console is running in the foreground. | has remedy | unchanged |
| `service_selfupdate.py:860` | `self_update_trigger` | A previous update needs recovery first — run `lhpc self-update --recover-request`. | bare | remedy added |
| `service_selfupdate.py:863` | `self_update_trigger` | One-click update is unavailable — the web/updater units are not the canonical managed set ({…}). Run `lhpc sel | bare | remedy added |
| `service_selfupdate.py:869` | `self_update_trigger` | Self-update is unavailable — lhpc is not running from a git checkout. | bare | remedy added |
| `service_selfupdate.py:873` | `self_update_trigger` | Self-update blocked: unsafe controller identity ({…}). | bare | remedy added |
| `service_selfupdate.py:895` | `self_update_trigger` | A controller uninstall is in progress — cannot self-update. | bare | remedy added |
| `service_selfupdate.py:898` | `self_update_trigger` | An update request is already pending — the console is about to update. | bare | remedy added |
| `service_selfupdate.py:902` | `self_update_trigger` | Self-update blocked: {…}. | bare | remedy added |
| `service_selfupdate.py:888` | `self_update_trigger` | A task is starting right now (admission contended) — retry the update. | bare | remedy added |
| `service_selfupdate.py:893` | `self_update_trigger` | <expr> | bare | admission refusal: excluded, U2 |
| `service_selfupdate.py:913` | `self_update_trigger` | An update request is already pending — the console is about to update. | bare | remedy added |
| `service_selfupdate.py:916` | `self_update_trigger` | Could not queue the update request: {…} | bare | remedy added |
| `service_selfupdate.py:938` | `self_update_run_service` | A task is starting right now (admission contended) — the update helper will retry on the next request. | bare | remedy added |
| `service_selfupdate.py:992` | `_self_update_run_service_locked` | Self-update service did not run. | bare | remedy added |
| `service_selfupdate.py:977` | `_self_update_run_service_locked` | Malformed update request — recovery required. | bare | remedy added |
| `service_selfupdate.py:988` | `_self_update_run_service_locked` | In-flight update ownership could not be proven — recovery required (`lhpc self-update --recover-request`). | bare | remedy added |
| `service_selfupdate.py:1061` | `_self_update_run_service_locked` | Update outcome could not be recorded durably — recovery required (`lhpc self-update --recover-request`). | bare | remedy added |
| `service_selfupdate.py:961` | `_self_update_run_service_locked` | A previous update is already in flight — recovery required (`lhpc self-update --recover-request`). | bare | remedy added |
| `service_selfupdate.py:965` | `_self_update_run_service_locked` | Could not claim the update request: {…} | bare | remedy added |
| `service_selfupdate.py:1067` | `_self_update_run_service_locked` | <expr> (in-flight marker cleanup FAILED: {…} — recovery required) | bare | remedy added |
| `service_selfupdate.py:1008` | `_self_update_run_service_locked` | Could not acquire the controller-runtime lock (unsafe runtime state) — no changes made. | bare | remedy added |
| `service_selfupdate.py:1028` | `_self_update_run_service_locked` | <expr> | bare | remedy added |
| `service_selfupdate.py:1003` | `_self_update_run_service_locked` | The console did not release the controller-runtime lock — no changes made. | bare | remedy added |
| `service_selfupdate.py:1192` | `self_update_recover_request` | Update-state recovery failed unexpectedly: {…} | bare | remedy added |
| `service_selfupdate.py:1197` | `self_update_recover_request` | Uninstall-guard recovery failed unexpectedly: {…} | bare | remedy added |
| `service_selfupdate.py:1246` | `_recover_uninstall_guard` | another uninstall-guard operation is in progress ({…}) — retry. | bare | remedy added |
| `service_selfupdate.py:1235` | `_recover_uninstall_guard` | An uninstall guard is held by a LIVE process (an uninstall may be running) — not removing it. | bare | remedy added |
| `service_selfupdate.py:1224` | `_recover_uninstall_guard` | An uninstall guard exists but is unreadable/unsafe — NOT removing it ({…}). | bare | remedy added |
| `service_selfupdate.py:1230` | `_recover_uninstall_guard` | An uninstall guard exists but its owner record is malformed — cannot prove the owner ceased; verify no uninsta | bare | remedy added |
| `service_selfupdate.py:1241` | `_recover_uninstall_guard` | Could not remove the stale uninstall guard: {…} | bare | remedy added |
| `service_selfupdate.py:1262` | `_recover_update_state` | The in-flight update record is unreadable/malformed — its helper cannot be proven stopped. Ensure lhpc-selfupd | bare | remedy added |
| `service_selfupdate.py:1269` | `_recover_update_state` | An update is still running (helper process alive) — wait for it to finish before recovering. | bare | remedy added |
| `service_selfupdate.py:1276` | `_recover_update_state` | Could not record the interrupted outcome durably — the in-flight record is kept; try recovery again. | bare | remedy added |
| `service_selfupdate.py:1296` | `self_update_repair_integration` | Not a self-hosted deployment (no checkout at {…}) — cannot manage web/updater units. | bare | remedy added |
| `service_selfupdate.py:1300` | `self_update_repair_integration` | An uninstall is in progress (.lhpc-uninstalling present) — recover it first (`lhpc self-update --recover-reque | bare | remedy added |
| `service_selfupdate.py:1304` | `self_update_repair_integration` | An update request is pending/in-flight — run `lhpc self-update --recover-request` first. | bare | remedy added |
| `service_selfupdate.py:1322` | `self_update_repair_integration` | systemctl --user daemon-reload failed after writing the units — not proceeding (the units are on disk but not  | bare | remedy added |
| `service_selfupdate.py:1351` | `self_update_repair_integration` | Installed the units but could not enable/start the request watcher (lhpc-selfupdate.path) — not proceeding. Ch | bare | remedy added |
| `service_selfupdate.py:1361` | `self_update_repair_integration` | Installed the units but could not enable/start the nginx-restart watcher (lhpc-nginx-restart.path) — not proce | bare | remedy added |
| `service_selfupdate.py:1368` | `self_update_repair_integration` | Could not enable the web service (lhpc-web.service) — not proceeding. | bare | remedy added |
| `service_selfupdate.py:1377` | `self_update_repair_integration` | Could not enable the boot-restore unit (lhpc-boot-restore.service) — not proceeding. | bare | remedy added |
| `service_selfupdate.py:1386` | `self_update_repair_integration` | The update path watcher (lhpc-selfupdate.path) is not active after enable --now — not proceeding. Check `syste | bare | remedy added |
| `service_selfupdate.py:1393` | `self_update_repair_integration` | The nginx-restart watcher (lhpc-nginx-restart.path) is not active after enable --now — not proceeding. Check ` | bare | remedy added |
| `service_selfupdate.py:1318` | `self_update_repair_integration` | <expr> | bare | remedy added |
| `service_selfupdate.py:1337` | `self_update_repair_integration` | After writing units, {…} still loads a different fragment ({…}). A higher-priority unit or mask shadows it — r | bare | remedy added |
| `service_selfupdate.py:1341` | `self_update_repair_integration` | {…} has an active drop-in override ({…}) — it can override the sandbox; remove it, then repair. | bare | remedy added |
| `service_selfupdate.py:1404` | `self_update_repair_integration` | Installed and enabled the units but the web console restart FAILED — the repair is NOT marked complete. Check  | bare | remedy added |
| `service_selfupdate.py:1448` | `self_update_repair_and_trigger` | One-click update needs the managed web service (systemd). This console is running in the foreground. | has remedy | unchanged |
| `service_selfupdate.py:1462` | `self_update_repair_and_trigger` | A previous update needs recovery first — run `lhpc self-update --recover-request`. | bare | remedy added |
| `service_selfupdate.py:1465` | `self_update_repair_and_trigger` | An uninstall is in progress — recover it first. | bare | remedy added |
| `service_selfupdate.py:1468` | `self_update_repair_and_trigger` | An update request is already pending — the console is about to update. | bare | remedy added |
| `service_selfupdate.py:1477` | `self_update_repair_and_trigger` | The web/updater units are not safely this deployment's ({…}) — resolve them manually, then update. | bare | remedy added |
| `service_selfupdate.py:1484` | `self_update_repair_and_trigger` | This console can't install systemd units itself (the user bus is unavailable). From a shell on this machine ru | bare | remedy added |
| `service_selfupdate.py:1492` | `self_update_repair_and_trigger` | Unit repair did not fully converge — run `lhpc self-update --repair-integration` from a shell. | bare | remedy added |
| `service_lifecycle_ops.py:3179` | `build` | Refusing to build '{…}': {…} | has remedy | unchanged |
| `service_lifecycle_ops.py:3185` | `build` | Refusing to build '{…}': invalid log-base prefix | bare | remedy added |
| `service_lifecycle_ops.py:3188` | `build` | <expr> | has remedy | unchanged |
| `service_lifecycle_ops.py:3322` | `build` | Refusing to build '{…}': {…} | bare | remedy added |
| `service_lifecycle_ops.py:3226` | `build` | Refusing to build '{…}': build dependency {…} is not installed. | has remedy | unchanged |
| `service_lifecycle_ops.py:3241` | `build` | Refusing to build '{…}': {…} is not installed. | has remedy | unchanged |
| `service_lifecycle_ops.py:3271` | `build` | Refusing to build '{…}': {…} needs a GUI toolkit that is not installed. | has remedy | unchanged |
| `service_lifecycle_ops.py:3373` | `build` | <expr> | bare | admission refusal: excluded, U2 |
| `service_lifecycle_ops.py:3375` | `build` | Build blocked for '{…}': {…} | has remedy | unchanged |
| `service_lifecycle_ops.py:3378` | `build` | Build blocked for '{…}': {…} | has remedy | unchanged |
| `service_maintenance.py:929` | `graywolf_upstream_update` | '{…}' is not an upstream-tracking package | bare | remedy added |
| `service_maintenance.py:931` | `graywolf_upstream_update` | run the upstream check first | has remedy | unchanged |
| `service_maintenance.py:935` | `graywolf_upstream_update` | '{…}' installed version unknown (no version stamp) — refetch/reinstall to record it | has remedy | unchanged |
| `service_maintenance.py:957` | `graywolf_upstream_update` | <expr> | bare | admission refusal: excluded, U2 |
| `service_maintenance.py:961` | `graywolf_upstream_update` | Cannot update '{…}': {…} | has remedy | unchanged |
| `service_maintenance.py:981` | `_graywolf_upstream_update_locked` | upstream fetch failed for '{…}' ({…}) — the install was left unchanged | has remedy | unchanged |
| `service_maintenance.py:998` | `_graywolf_upstream_update_locked` | fetched {…} but could not re-mark built: {…} | bare | remedy added |
| `service_maintenance.py:1779` | `update` | '{…}' is installed from a binary — switching to the {…} source channel is an install, not an update. | has remedy | unchanged |
| `service_maintenance.py:1799` | `update` | Refusing to update '{…}': {…} | bare | remedy added |
| `service_maintenance.py:1825` | `update` | Refusing to update '{…}': component(s) using the affected source(s) are running. | has remedy | unchanged |
| `service_maintenance.py:1760` | `update` | The binary channel installs ONE stack at a time — name the stack. | has remedy | unchanged |
| `service_maintenance.py:1764` | `update` | Cannot update '{…}' from binary: {…} | has remedy | unchanged |
| `service_maintenance.py:1786` | `update` | No sources. | bare | remedy added |
| `service_maintenance.py:1912` | `update` | <expr> | bare | admission refusal: excluded, U2 |
| `service_maintenance.py:1914` | `update` | Update blocked for '{…}': {…} | has remedy | unchanged |
| `service_maintenance.py:1917` | `update` | Update blocked for '{…}': {…} | has remedy | unchanged |
| `service_maintenance.py:1850` | `update` | Refusing to update '{…}': component(s) using the affected source(s) started while the update was acquiring its | has remedy | unchanged |
| `service_maintenance.py:1869` | `update` | Refusing to update '{…}': shared-source remote configuration is inconsistent. | bare | remedy added |
| `service_maintenance.py:1876` | `update` | Refusing to update '{…}': incompatible source resolutions for a shared checkout. | bare | remedy added |
| `service_maintenance.py:1885` | `update` | Refusing to update '{…}': {…} | bare | remedy added |
| `services.py:1538` | `install` | Runtime root is not bootstrapped yet. | has remedy | unchanged |
| `services.py:1553` | `install` | Refusing to install '{…}': shared-source remote configuration is inconsistent. | bare | U2 (outside FILES) |
| `services.py:1585` | `install` | Refusing to install '{…}': {…} | bare | U2 (outside FILES) |
| `services.py:1589` | `install` | Refusing to install '{…}': incompatible source resolutions for a shared checkout. | bare | U2 (outside FILES) |
| `services.py:1619` | `install` | Refusing to install '{…}': {…} | bare | U2 (outside FILES) |
| `services.py:1521` | `install` | The binary channel installs ONE stack at a time. | has remedy | unchanged |
| `services.py:1608` | `install` | <expr> | bare | U2 (outside FILES) |
| `services.py:1611` | `install` | A task is starting right now (admission contended) — retry the install. | bare | U2 (outside FILES) |
| `services.py:1637` | `install` | Refusing to switch '{…}' to the {…} source channel: the existing checkout(s) cannot be taken over. | has remedy | unchanged |
| `services.py:1668` | `install` | Install superseded before admission — nothing was changed. | bare | U2 (outside FILES) |
| `services.py:1785` | `install` | <expr> — but the HMAC password could NOT be enabled ({…}); fix and re-run before starting the meshcom link. | has remedy | unchanged |
| `services.py:1790` | `install` | <expr> (candidate cleanup INCOMPLETE) | has remedy | unchanged |
| `services.py:1657` | `install` | Refusing to install '{…}': {…} | bare | U2 (outside FILES) |
| `services.py:1778` | `install` | Switch of '{…}' to the {…} source channel FAILED — {…}. | has remedy | unchanged |
| `services.py:1713` | `install` | Refusing to switch '{…}' to the {…} source channel: {…} | bare | U2 (outside FILES) |

### `install.py` failed-detail lines (base)

These are per-component lines inside the update or install result. They are not
`ActionResult`s, so the contract does not reach them. Only (iv) changed.

- **Name what to do:**
  - `:329` "resolve manually"; `:373` and `:549` "resolve it before …";
  - `:442` "Recover by removing the checkout … and re-running install"; `:457` the same, for
    discarding local changes;
  - `:621` "re-run the update after inspecting it";
  - `:638` prior-dirty, "remove the .prev directory and the journal manually";
  - `:652` "move or remove the local file, then retry";
  - `:661` "revert or stash … fork the project";
  - `:689` **(iv), now names `src/.<name>.prev` and `lhpc update <comp> --yes`**.
- **Cause only (U2 candidates):**
  - `:243`, `:397` (`str(exc)`); `:256`;
  - `:352`, `:404`, `:778`, `:791` (source unavailable);
  - `:382` (busy, implicit retry);
  - `:411`, `:418`, `:436`, `:475`, `:568`, `:630`, `:672`, `:677`, `:683`, `:694`,
    `:761`, `:787`.

## The measured refusals

- **(i)** `service_selfupdate.py:346` / `:873`. **Fixed.**
  - `next_commands`: `git -C <root>/src/loraham-pi-control switch main`, then
    `lhpc self-update --apply`.
  - A details line names `git switch main` itself, so the console, which shows no
    `next_commands`, still reads a remedy.
  - Twin test: `test_unsafe_identity_refusal_names_the_command`.
- **(ii)** `lhpc update graywolf` → `service_maintenance.py:1786` → `_unknown_stack`
  (`services.py:2843`). **Not fixed here: control flow.** U2-1 below.
- **(iii)** The plan line now reads "`<c>: upstream <state> — this update fetches the <source>
  version from <remote>`".
  - **Fixed** (`2d14c32`).
  - "pinned: match" is the post-update probe state (`lhpc/core/probes/source.py:128`). It is
    true and stays.
  - The mid-run "Next:" is control flow in `lhpc/adapters/cli/main.py:106-118` (`_apply_flow`
    renders the dry run first). U2-2.
- **(iv)** `install.py:689`. **Fixed.**
  - The detail now reads: "activation failed — active source untouched. If src/.<name>.prev
    exists, it is left over from an earlier interrupted update and blocks every update of this
    source: move it out of src/ (lhpc no longer uses it), then retry (lhpc update <comp> --yes)".
  - It is one string literal sequence with no newline.
  - Twin test: `test_leftover_prev_refusal_names_the_folder_and_the_command`. That test also
    shows the folder is left in place.
- **(v)** `service_lifecycle_ops.py:1194`. Unchanged; it is the model wording.

## Findings for U2 (not changed here)

1. **(ii)** A known stack with no git source answers "Unknown stack". Proposed patch in
   `ServiceMaintenance.update` before `_unknown_stack`:
   `if target and self.stack(target) is not None: return ActionResult(False, f"'{target}' has no
   git source to update.", details=["  its pinned release is re-fetched by a build; the newest
   upstream release by --upstream"], next_commands=[f"lhpc build {target} --yes",
   f"lhpc update {target} --upstream --yes"])`.
2. **(iii)** `_apply_flow` prints the dry run's `Next: … --yes` inside a `--yes` run. Proposal:
   with `yes=True`, render the plan without `next_commands`.
3. **The web console never renders `next_commands`.** `grep -rn next_commands
   lhpc/adapters/web` finds no hit. A command-only remedy reaches the CLI only. Proposal: render
   them under the flash.
4. **The shared admission refusal** (7 sites) has no `next_commands`. Its reason is worded in
   `_task_admission_blocked` and in the power gate in `services._acquire_key`. Proposal: return
   the remedy with the tag, and let the 7 sites pass it on.
   - Stacked-only: `cons/S1`'s golden build test pins `next_commands: []` on this result.
5. **`services.py` `install`, 9 bare.** Proposed remedies:
   - `:1553` and `:1589`: the update's two sentences;
   - `:1585`: the defect sentence;
   - `:1608`: admission (item 4);
   - `:1611` and `:1668`: `lhpc install <s> --yes` (retry);
   - `:1619`: the MeshCore-identity sentence;
   - `:1657` and `:1713`: "nothing to run here — the binary install is left as it was; the box's
     operator fixes what is named above, then retries the switch".
6. **`install.py` cause-only details** (listed above).

## The 6-point block

1. **Contracts.**
   - `ActionResult` (`lhpc/core/service_base.py:111`) is unchanged: fields and types.
   - For each changed constructor, only `details` or `next_commands` contents are new or moved.
     `ok`, `summary` and `data` are byte-identical, apart from (iv)'s `action.detail` and the
     plan line of (iii).
   - No signature, lock order, persisted format or exception type changed. No `import` was added
     or removed (`git diff e38803b HEAD -- lhpc | grep -E '^[-+].*import'` → no output).
   - The new contract is stated in the test module's docstring.
2. **Invariants and tests.**
   - The safety invariants in reach: *Truthful outcomes* (`ok` unchanged at every site, by
     construction of the diff) and *Source transactions* ((iv)'s refusal still removes nothing).
   - Proven by:
     - `tests/repo/test_refusal_remedy.py::test_leftover_prev_refusal_names_the_folder_and_the_command`
       asserts the `.prev` folder is still there;
     - `tests/install/test_staged_update.py` (4 passed);
     - `tests/install/test_binary_channel.py`, `tests/core/test_controller.py` and
       `tests/install/test_selfupdate_service.py` (283 passed, 1 environmental failure, below).
3. **Known failure classes.**
   - **Fakes:** the twins use the real `Installer` and `ControllerService` with `FakeSystem`. They
     stub two collaborators (`controller_identity_live`, `_self_update_blockers`), with the
     signatures `(self)` and `(self)`. A comment says why.
   - **EIO / EACCES / ENOTDIR:** not applicable; no probe changed.
   - **KeyboardInterrupt:** not applicable; no cleanup path changed.
   - **Same decision across CLI, web, detached job and boot restore:** decisions are unchanged.
     The displays differ:
     - the web shows the new `details` lines but not `next_commands` (U2-3);
     - the auto-install narrative log prints a binary or build refusal's `details`
       (`service_auto_install.py:1261`, `:1386`), so it gains the new line;
     - the one-click helper persists `summary` only, so it is unchanged.
   - **Stacked-only conflicts with the other batches' files:** none of the hunks lies within 3
     lines of a `cons/Q2`, `cons/Q3` or `cons/S1` hunk in `install.py` or
     `service_binary_ops.py` (hunk-range comparison, command in "Numbers").
     - `CHANGELOG.md`: Q2, Q3 and this batch all append under `## 0.12.0`. That is an adjacent
       textual conflict to resolve by keeping all lines.
     - `docs/architecture.md`: see the D1 report (Q2's Path-containment hunk).
     - `cons/S1`'s golden build test pins the admission refusal and three other build results.
       None of those is changed here.
4. **Test rules.**
   - Red-before is proven per test (above).
   - Remedies are asserted by exact command token (`next_commands[:2] == [...]`; the
     `lhpc update c --yes` token in the detail), not by `startswith`.
   - The guard's marker is the contract's own token, "nothing to run here — ".
   - No network: the git runner is a fake.
   - No unchecked return: `write_record` is asserted.
5. **Whole test directories.**
   - **Added to:** `tests/repo`. `python -m pytest -q -p no:cacheprovider tests/repo` →
     `415 passed, 5 skipped`.
   - **Also run,** because they drive the changed refusals: `tests/install/test_binary_channel.py
     tests/core/test_controller.py tests/install/test_selfupdate_service.py
     tests/install/test_staged_update.py` → `1 failed, 283 passed`.
     - The failure is `test_doctor_is_quiet_for_a_healthy_binary_install`. It is
       environmental: this container has no `zstd`, so `doctor` prints "zstd: not installed
       (optional — … binary install channel …)".
     - It fails identically on the parent with `lhpc/` stashed.
   - **Lint:** `ruff check lhpc testlab` → `All checks passed!`; `ruff check tests --select F,E9`
     → `All checks passed!`.
   - No signature changed, so there was no further grep. No full-suite run; nothing ran in the
     background.
6. **Adversarial self-review. Found and fixed:**
   - The identity details said "the first command…", which the console, showing no
     `next_commands`, cannot see. It now names `git switch main` itself.
   - The plan counted six admission refusals and 92 remedies. Measured: 7 and 91. Corrected.
   - The CHANGELOG said "always" and "install". Neither is true for `lhpc install`'s own
     refusals or the admission refusal. Reworded.
   - Two commit subjects said "every". Reworded.
   - Retire refusals first pointed at `lhpc clean --purge`, which deletes config. They now point
     at `lhpc uninstall` (a changed file) or say "nothing to run here" (an unwritable state
     folder).
   - The channel table first claimed `--versions` reads `match` after a known-working update.
     That was not proven, so it was removed.
   - The docs and this report describe exactly the diff.

## Simplicity guardrails

- No new abstraction, helper, class or registry. Only keyword arguments on existing
  constructors, plus one test module.
- No dependency was added. No new `import`. The `self.*` used in new text was already used in
  the same method: `self._paths` in `self_update_apply`/`self_update_trigger`, and
  `self._source_rel` in `_stage_and_activate`.
- Net code grows by text only. Line counts before → after (`wc -l`):

| file | before | after | +/- |
|---|---|---|---|
| `install.py` | 2323 | 2328 | +6 -1 |
| `service_binary_ops.py` | 988 | 1013 | +35 -10 |
| `service_lifecycle_ops.py` | 6053 | 6059 | +8 -2 |
| `service_maintenance.py` | 2774 | 2798 | +33 -9 |
| `service_selfupdate.py` | 1523 | 1687 | +241 -77 |

## Precision checklist

1. **Absolute sentences.**
   - "No change in behaviour" is stated only as "no `ok`/`summary`/`data`/control flow
     change". The visible output changes are listed in point 3.
   - "Every refusal" is stated only for the guard's scopes. The CHANGELOG names the paths and does
     not say "always".
2. **One line.** (iv)'s detail is adjacent string literals with no `\n`. Each new `details`
   entry is one string literal sequence with no newline.
3. **Fault injection.**
   - Twin (iv) reaches the failed-clean branch through a real pre-existing `.prev`
     (`_activate_held`'s first check). The assertion on the `.prev` path proves that branch was
     the one reached.
   - Twin (i) asserts `data["identity_unsafe"]`, which only that branch sets.
4. **Moved lines.**
   - The two command lists moved from `details` to `next_commands`
     (`service_selfupdate.py` web-running and stop-failed). Shown by
     `git diff --color-moved=dimmed-zebra e38803b HEAD -- lhpc/core/service_selfupdate.py`.
   - The reworded lines are (iii) and (iv).
5. **Exceptions.** No helper added; nothing swallows anything new.
6. **Runs cited.** Every run cited is on `ad6d6c7`, or on `d052c3b` for red-before. Both are on
   this branch.

## Deviations

- **FILES.** `service_source*.py` matches no file. Its text lines were applied in
  `service_maintenance.py` (`update`, `graywolf_upstream_update`,
  `_graywolf_upstream_update_locked`), the module docs/architecture.md calls "source update".
  This is plan open question 1. `services.py` was not touched (U2-5).
- **`docs/architecture.md`** is touched by U1 only to correct the 14 D1-table line numbers that
  the remedy texts moved (`4c152ee`). Between `22aa6e0` and `5e68d4c` those 14 numbers are
  stale.
- **The guard is static** (a whole-module negative invariant with two behavioural twins), not
  "drive each refusal": 91 setups would be out of proportion.
- **(iii) has no test.** It is a human sentence (tests/README.md rule 2).
- **The base does not carry Q2 or Q3.** `git log origin/integration/0.12.0` shows Q1, Q4 and Q5.
  `plans/PLAN-Q1.md` exists only on `cons/Q1`, and was read there. Q1's typed outcomes sit on no
  refusal in these scopes.
- **Branch.** The brief asks for `cons/<batch>-r<round>`. The push goes to `cons/S3-r1`; if
  refused, to this round's own branch.

- Attribution check: `git grep -niE 'claude|chatgpt|anthropic|agent [0-9]|session'` over the files this branch changes.
  Every hit is a product word on a line that was already in the text: the web session, a process session, the
  session cookie, "supersession". The one hit on an added line is the reflowed Web bullet in `docs/architecture.md`
  ("before any session or CSRF work"), and its wording is unchanged. There is no attribution line in any commit.

## Numbers, measured

| figure | command | output |
|---|---|---|
| constructors / remedied / had remedy / admission / services.py | `python3 inv.py` (above) on `e38803b`; counts by class | 156 / 91 / 49 / 7 / 9 |
| bare on the parent per the guard | the guard's `_refusals`/`_has_remedy` on `d052c3b` | 91 |
| test functions, parametrize | `grep -c "^def test_" tests/repo/test_refusal_remedy.py`; `grep -c parametrize` | 3, 0 |
| per-file lines, +/- | `wc -l`; `git diff --numstat e38803b HEAD -- <f>` | table above |
| all U1 commits up to `ad6d6c7` | `git diff --numstat 8afa12f ad6d6c7 \| awk '{a+=$1;d+=$2} END {print a, d}'` | +638 -108 |
| plan length | `wc -l plans/PLAN-U1.md` | 120 |
| D1 table lines corrected | `git show 4c152ee --numstat` | 7 / 7 (14 references) |
| table checker on `ad6d6c7` | `python3 check_table.py` | `rows=35 owners=77 tests=105 bad=0` |
| hunk overlap with Q2/Q3/S1 | per-file `git diff -U0` hunk ranges vs `git diff -U0 e38803b...origin/cons/<b>`, ±3 lines | none in code files |
| tests/repo | `python -m pytest -q -p no:cacheprovider tests/repo` | 415 passed, 5 skipped |
| new module | `python -m pytest -q -p no:cacheprovider tests/repo/test_refusal_remedy.py` | 3 passed (after); 3 failed (on `d052c3b`) |
| neighbour modules | the four-module run in point 5 | 1 failed (zstd, environmental), 283 passed |
`````

## Full diff of the commits under review

```diff
diff --git a/CHANGELOG.md b/CHANGELOG.md
index 889035e..df2f784 100644
--- a/CHANGELOG.md
+++ b/CHANGELOG.md
@@ -12,6 +12,13 @@
   a start with a terminal-only part (the Voice terminal variant) counts as successful, and whether a half-finished
   configuration save blocks the next one are now decided from a recorded fact, never from the wording of the
   message you see. The messages themselves are unchanged.
+- A refused update, self-update, build or binary install now says what to do: a command under the
+  CLI's *Next:*, or a *nothing to run here* line naming what has to be fixed on the box. A
+  self-update on a checkout left on a detached HEAD or another branch names the `git … switch
+  main` to run; an update blocked by a `src/.<name>.prev` folder left over from an interrupted
+  update names that folder. The update plan says which version the update fetches instead of
+  "fetch newest". What to run to keep each stack current is one table in
+  [operations](docs/operations.md#keeping-stacks-current).
 
 - Every release is now also installed, built and self-updated on a test box slowed down below a Pi Zero 2 W
   before it ships: an update that would stall or run into a time limit on a slow box turns the release check
diff --git a/docs/architecture.md b/docs/architecture.md
index 71849b4..d684470 100644
--- a/docs/architecture.md
+++ b/docs/architecture.md
@@ -278,25 +278,25 @@ unless absolute.
 
 | area | invariant | owner | locks (in order) | durable state · recovery | proving tests |
 |---|---|---|---|---|---|
-| admission / locks | task admission: no new task while an uninstall or a self-update is pending or running ([Locking](#safety-model)) | `lhpc/core/services.py:2509` `_admit`; `lhpc/core/service_selfupdate.py:1110` `_task_admission_blocked` | `controller-task-admission`, always first | none of its own; reads `state/selfupdate.request`, `state/selfupdate.inflight`, `.lhpc-uninstalling` | `tests/core/test_task_admission.py::test_second_service_contends_on_the_admission_lock`, `::test_apply_task_starts_refused_during_uninstall` |
+| admission / locks | task admission: no new task while an uninstall or a self-update is pending or running ([Locking](#safety-model)) | `lhpc/core/services.py:2509` `_admit`; `lhpc/core/service_selfupdate.py:1209` `_task_admission_blocked` | `controller-task-admission`, always first | none of its own; reads `state/selfupdate.request`, `state/selfupdate.inflight`, `.lhpc-uninstalling` | `tests/core/test_task_admission.py::test_second_service_contends_on_the_admission_lock`, `::test_apply_task_starts_refused_during_uninstall` |
 | admission / locks | named non-blocking locks; a contended operation refuses naming the holder ([Locking](#safety-model)) | `lhpc/core/reslock.py:106` `operation_lock`; `lhpc/core/services.py:2572` `_acquire_key` | one flock per key under `state/locks/` | owner record beside the lock; the kernel drops a dead holder's flock | `tests/core/test_reslock.py::test_second_acquire_is_blocked_and_names_holder`, `::test_dead_holder_lock_is_free`, `::test_concurrent_lifecycle_op_is_blocked` |
 | admission / locks | one lock order for every operation ([Locking](#safety-model)) | `lhpc/core/services.py:2559` `_admission_guard`, `:218` `_config_stable`, `:360` `_source_operation_guard`, `:2636` `_lifecycle_guard` | admission → config stability → `source-txn-index` → source paths (sorted) or the `lifecycle.<stack>` bundle → self-update lock | — | `tests/core/test_task_admission.py::test_admission_acquired_before_config_stable`, `::test_second_thread_start_and_config_do_not_invert`; `tests/core/test_op_serialization.py::test_multi_source_update_holds_locks_across_groups` |
 | config transactions | a save validates everything first, is journalled and rolls back; a crashed save is finished before any writer runs ([Config as a transaction](#safety-model)) | `lhpc/core/config.py:1901` `apply_config_transaction`, `:1854` `_finish_pending_journal`, `:1881` `recover_config_journal_at_startup` | config lock `config/.lock` (`lhpc/core/config.py:77` `config_lock`) | `state/config-txn.json`; `lhpc/core/config.py:1800` `recover_config_transaction` rolls back, or blocks and keeps the journal | `tests/core/test_config.py::test_pending_journal_is_recovered_before_next_save`, `::test_rollback_failure_retains_journal_and_blocks_later`, `::test_a_non_transactional_save_finishes_a_pending_journal_before_it_writes`; `tests/cli/test_cli.py::test_a_pending_config_journal_is_cleaned_up_when_lhpc_starts` |
 | config transactions | a malformed `local.toml` is kept; only an absent per-stack file means defaults ([Config as a transaction](#safety-model)) | `lhpc/core/config.py:1320` `_write_local_tables`, `:2019` `load_stack_config` | config lock (writes only) | `config/local.toml`, `config/stacks/<id>[@band].toml` | `tests/core/test_config.py::test_local_unsupported_structures_block_and_preserve`, `::test_malformed_stack_config_raises_and_is_preserved`, `::test_absent_stack_config_is_defaults`, `::test_web_returns_409_on_malformed_config`; gap: a write over a `local.toml` that is not valid TOML |
-| install / recovery | source transactions: candidate recorded before the clone, prior archived to `.prev`, no-clobber activation, ownership record, unresolved journal blocks all source mutation ([Source transactions](#safety-model)) | `lhpc/core/install.py:512` `_stage_and_activate`, `:1277` `_staged_clone_record`, `:1920` `_activate_held`, `:1204` `_pending_journals` | admission → `source-txn-index` → source paths | `state/source-txn/<name>-<sha256>.json`, its `.staging` record, `src/.<name>.prev`; `lhpc/core/install.py:1227` `_recover_scan` finishes or rolls back, else blocks | `tests/install/test_source.py::test_recovery_removes_a_clone_killed_before_its_journal`, `::test_recovery_finishes_an_activation_interrupted_right_after_the_archive_rename`, `::test_retained_journal_blocks_every_source_op`; `tests/install/test_staged_update.py::test_failed_clone_leaves_active_source_intact` |
+| install / recovery | source transactions: candidate recorded before the clone, prior archived to `.prev`, no-clobber activation, ownership record, unresolved journal blocks all source mutation ([Source transactions](#safety-model)) | `lhpc/core/install.py:512` `_stage_and_activate`, `:1282` `_staged_clone_record`, `:1925` `_activate_held`, `:1209` `_pending_journals` | admission → `source-txn-index` → source paths | `state/source-txn/<name>-<sha256>.json`, its `.staging` record, `src/.<name>.prev`; `lhpc/core/install.py:1232` `_recover_scan` finishes or rolls back, else blocks | `tests/install/test_source.py::test_recovery_removes_a_clone_killed_before_its_journal`, `::test_recovery_finishes_an_activation_interrupted_right_after_the_archive_rename`, `::test_retained_journal_blocks_every_source_op`; `tests/install/test_staged_update.py::test_failed_clone_leaves_active_source_intact` |
 | install / recovery | locally added files are carried, never collateral ([Locally added files](#safety-model)) | `lhpc/core/source_fs.py:546` `carry_extras`, `:688` `extras_preserved` | as source transactions | `.prev` and its journal are kept while a carry is unproven | `tests/install/test_source.py::test_an_added_file_colliding_with_the_new_upstream_refuses`, `::test_a_carry_failure_restores_the_prior_and_refuses`, `::test_an_addition_made_after_the_carry_retains_the_archived_prior` |
 | install / recovery | identity-verified stopping ([Identity-verified stopping](#safety-model)) | `lhpc/core/lifecycle.py:1255` `stop`, `:1117` `verify_owned`; `lhpc/core/procident.py:96` `identity_matches` | `lifecycle.<stack>` bundle and its `claim.*` keys (`lhpc/core/services.py:2444` `_lifecycle_lock_keys`); stop takes no admission | `state/owned/<comp>__<band>__<pid>__<nonce>.json`, cleared only after verified cessation | `tests/core/test_process_ownership.py::test_manual_matching_process_without_record_is_not_killed`, `::test_stop_drops_reused_pid_record_without_signalling`, `::test_ceased_process_with_lingering_endpoint_retains_record` |
 | install / recovery | path containment ([Path containment](#safety-model)) | `lhpc/core/runtime_fs.py:71` `_walk_parent`, `:153` `atomic_write` | none | — | `tests/core/test_runtime_fs.py::test_symlinked_parent_refuses_every_runtime_op`, `::test_atomic_write_rejects_symlink_leaf` |
-| install / recovery | uninstall refuses while running, keeps shared sources, keeps config, secrets and profiles ([Uninstall protection](#safety-model)) | `lhpc/core/service_maintenance.py:2280` `uninstall` | admission → config stability (shared) → every affected source path | `state/source-registry/` records | `tests/core/test_uninstall_safety.py::test_uninstall_refuses_while_running`, `::test_uninstall_keeps_source_shared_within_stack`; `tests/core/test_recovery.py::test_uninstall_removes_source_but_keeps_config`; gap: secrets and profiles kept on uninstall |
+| install / recovery | uninstall refuses while running, keeps shared sources, keeps config, secrets and profiles ([Uninstall protection](#safety-model)) | `lhpc/core/service_maintenance.py:2304` `uninstall` | admission → config stability (shared) → every affected source path | `state/source-registry/` records | `tests/core/test_uninstall_safety.py::test_uninstall_refuses_while_running`, `::test_uninstall_keeps_source_shared_within_stack`; `tests/core/test_recovery.py::test_uninstall_removes_source_but_keeps_config`; gap: secrets and profiles kept on uninstall |
 | install / recovery | boot restore replays saved configuration through the gated start, honours stop notes ([operations](operations.md#not-a-supervisor)) | `lhpc/core/service_boot_restore.py:280` `boot_restore_run`, `:121` `_write_stop_intent` | admission, then the normal start locks | `state/boot-restore.json`, `state/stop-intent/<stack>.json`; `lhpc/core/service_boot_restore.py:258` `_boot_journal_recovery` consumes an item left *attempting*, never retries it | `tests/core/test_boot_restore.py::test_crash_after_attempting_never_retries_but_pending_survives`, `::test_boot_restore_run_honors_stop_intent_end_to_end`, `::test_driver_admission_refusal_consumes_nothing` |
 | install / recovery | a wheel installed into a fresh venv runs ([Packaging](#safety-model)) | `lhpc/core/assets.py:20` `asset_path` | none | — | `tests/repo/test_packaging.py::test_data_assets_resolve`, `::test_manifest_loads_from_package_data`; gap: no test installs the wheel into a fresh venv |
-| binary channel | a failed or interrupted binary install restores the previous one ([operations](operations.md#install-channels)) | `lhpc/core/service_binary_ops.py:88` `binary_install`, `:555` `binary_recover` | admission → the covered source paths | `state/binary/install.journal.json`; `binary_recover` runs first under the locks: a committed journal is dropped, any other is rolled back (files, receipt, mesh password) | `tests/install/test_binary_install.py::test_rollback_restores_an_interrupted_publish`; `tests/install/test_binary_channel.py::test_retire_recovers_an_interrupted_transaction_first`, `::test_unexpected_error_after_publish_unwinds_everything` |
+| binary channel | a failed or interrupted binary install restores the previous one ([operations](operations.md#install-channels)) | `lhpc/core/service_binary_ops.py:88` `binary_install`, `:568` `binary_recover` | admission → the covered source paths | `state/binary/install.journal.json`; `binary_recover` runs first under the locks: a committed journal is dropped, any other is rolled back (files, receipt, mesh password) | `tests/install/test_binary_install.py::test_rollback_restores_an_interrupted_publish`; `tests/install/test_binary_channel.py::test_retire_recovers_an_interrupted_transaction_first`, `::test_unexpected_error_after_publish_unwinds_everything` |
 | binary channel | the receipt is absent, valid, superseded or unsafe; unreadable is never absent ([operations](operations.md#install-channels)) | `lhpc/core/binary_receipt.py:272` `receipt_state` | none (read) | `state/binary/<stack>.json` | `tests/install/test_binary_channel.py::test_malformed_receipt_is_unsafe_never_absent`, `::test_superseded_when_txn_id_differs`, `::test_absent_when_no_receipt` |
-| binary channel | retiring never deletes a file changed since installation ([operations](operations.md#install-channels)) | `lhpc/core/service_binary_ops.py:855` `_retire_body` | the caller's (install switch, uninstall, clean) | the receipt is kept while a file is changed | `tests/install/test_binary_install.py::test_retire_refuses_when_files_changed` |
+| binary channel | retiring never deletes a file changed since installation ([operations](operations.md#install-channels)) | `lhpc/core/service_binary_ops.py:871` `_retire_body` | the caller's (install switch, uninstall, clean) | the receipt is kept while a file is changed | `tests/install/test_binary_install.py::test_retire_refuses_when_files_changed` |
 | PKI / web | loopback bind, `Host` check, POST + CSRF + confirm, security headers, config paths inside `config/stacks/` ([Web](#safety-model)) | `lhpc/adapters/web/app.py:2492` `run_server`, `:311` `_trusted_host`, `:244` `_csrf_ok`, `:295` `_set_headers`; `lhpc/core/config.py:1995` `_stack_config_path` | none | — | `tests/web/test_web.py::test_run_server_rejects_non_loopback`, `::test_action_requires_csrf`, `::test_dashboard_ok_and_headers`, `::test_config_path_cannot_escape_via_band_or_id`; `tests/web/test_trusted_host.py::test_interactive_console_rejects_rebinding_host` |
 | PKI / web | no GET route runs a network or git-remote command ([Web](#safety-model)) | no single owner: every GET renders cached state (`lhpc/core/selfupdate.py:690` `status_view` for self-update) | none | — | `tests/web/test_web.py::test_get_routes_make_no_network_calls`, `::test_page_load_is_read_only`; `tests/core/test_controller.py::test_controller_status_makes_no_live_calls` |
 | PKI / web | evidence once per request; every writing entry invalidates the snapshot ([Evidence once per request](#safety-model)) | `lhpc/core/snapshot_memo.py:24` `invalidates_snapshot`; `lhpc/core/services.py:880` `invalidate_snapshot` | none | — | `tests/core/test_snapshot_memo.py::test_every_public_service_entry_is_classified`, `::test_every_traced_write_is_classified`, `::test_no_neutral_writer_path_is_a_snapshot_input` |
-| PKI / web | detached web jobs publish the tracking marker only after admission is released; job markers resist PID reuse ([Detached web jobs](#safety-model)) | `lhpc/core/service_lifecycle_ops.py:3500` `spawn_web_job`, `:4003` `active_jobs`, `:3843` `prune_logs`; `lhpc/core/webjob_gate.py:64` `verify_tracked` | admission across reserve and spawn, released before publishing | `state/jobresults/<log>.json`, `state/jobs/<log>.job`; an untrackable child is terminated or the attempt marked *unsafe* | `tests/web/test_webjob.py::test_spawn_web_job_captures_then_releases_admission_before_publishing`; `tests/core/test_process_ownership.py::test_untracked_job_spawn_is_terminated_not_orphaned`; `tests/core/test_runtime_fs.py::test_job_marker_reused_pid_not_active`, `::test_prune_logs_bounds_count_and_protects_active` |
+| PKI / web | detached web jobs publish the tracking marker only after admission is released; job markers resist PID reuse ([Detached web jobs](#safety-model)) | `lhpc/core/service_lifecycle_ops.py:3506` `spawn_web_job`, `:4009` `active_jobs`, `:3849` `prune_logs`; `lhpc/core/webjob_gate.py:64` `verify_tracked` | admission across reserve and spawn, released before publishing | `state/jobresults/<log>.json`, `state/jobs/<log>.job`; an untrackable child is terminated or the attempt marked *unsafe* | `tests/web/test_webjob.py::test_spawn_web_job_captures_then_releases_admission_before_publishing`; `tests/core/test_process_ownership.py::test_untracked_job_spawn_is_terminated_not_orphaned`; `tests/core/test_runtime_fs.py::test_job_marker_reused_pid_not_active`, `::test_prune_logs_bounds_count_and_protects_active` |
 | PKI / web | no shell; typed validation; `@file:` secrets fail closed ([No shell](#safety-model), [Typed validation](#safety-model)) | `lhpc/core/commands.py:75` `expand_argv`, `:130` `build_env`; `lhpc/core/validators.py:434` `validate_param` | none | — | `tests/core/test_structured_exec.py::test_hostile_value_stays_one_token`, `::test_started_process_argv_is_not_a_shell`, `::test_at_file_secret_missing_blocks`; `tests/core/test_validators.py::test_start_rejects_malicious_runparam` |
 | PKI / web | an unverified clock never dates a certificate ([operations](operations.md#clock)) | `lhpc/core/clock.py:27` `verdict`, `:79` `clock_refusal` | the caller's | the original certificate stays active on a refusal | `tests/core/test_clock_gate.py::test_every_mutating_path_refuses_an_unverified_clock`, `::test_a_refused_reissue_leaves_the_original_certificate_active` |
 | firewall | the helper's apply is journalled and verified; a crash is finished forward or rolled back ([firewall](firewall.md)) | `lhpc/core/firewall_helper.py:1246` `op_apply`, `:1156` `op_check`, `:1048` `recover` | root flock `/etc/lhpc/.firewall.lock` | `/etc/lhpc/firewall.{journal,meta,snapshot,transition}.json`, receipt `/run/lhpc-firewall/check.json`; a corrupt journal fails closed | `tests/host/test_firewall.py::test_recovery_finishes_promoted_apply_forward`, `::test_recovery_rolls_back_unpromoted_apply`, `::test_corrupt_journal_fails_closed`, `::test_apply_happy_path_writes_snapshot_and_verified_receipt` |
@@ -304,7 +304,7 @@ unless absolute.
 | self-update | an unsafe controller identity blocks apply ([Controller identity](#controller-identity--self-update)) | `lhpc/core/services.py:495` `controller_identity_live` | none (read) | the cached verdict in the self-update envelope | `tests/core/test_controller.py::test_identity_ok`, `::test_missing_checkout_is_not_applicable_not_unsafe`, `::test_self_update_apply_blocked_by_unsafe_identity` |
 | self-update | a running console never has its source changed underneath it ([Controller identity](#controller-identity--self-update)) | `lhpc/core/selfupdate.py:100` `controller_runtime_lock`, `:65` `update_lock` | admission → controller-runtime (exclusive; the console holds it shared) → self-update lock | `state/locks/` | `tests/core/test_controller.py::test_apply_refused_while_web_shared_lock_held`, `::test_web_shared_fails_closed_while_apply_exclusive_held`; `tests/core/test_task_admission.py::test_self_update_apply_contends_typed` |
 | self-update | one-click update and boot restore run only on the canonical, unoverridden units ([deployment](deployment.md#self-update)) | `lhpc/core/updater_units.py:555` `verify`, `:578` `integration` | none (read) | the unit files under the user unit folder | `tests/host/test_updater_units.py::test_verify_ok_and_integration_ok`, `::test_verify_overridden_by_dropin`, `::test_verify_unsafe_symlinked_unit` |
-| self-update | the update request is claimed once; an in-flight record is cleared only when its helper is proven gone ([deployment](deployment.md#recovery)) | `lhpc/core/service_selfupdate.py:840` `self_update_trigger`, `:942` `_self_update_run_service_locked`, `:1178` `self_update_recover_request` | admission | `state/selfupdate.request`, `state/selfupdate.inflight`; `lhpc self-update --recover-request` | `tests/install/test_selfupdate_service.py::test_trigger_writes_exclusive_request_no_systemctl`, `::test_recover_inflight_requires_dead_helper`, `::test_run_service_refuses_preexisting_inflight_preserving_both` |
+| self-update | the update request is claimed once; an in-flight record is cleared only when its helper is proven gone ([deployment](deployment.md#recovery)) | `lhpc/core/service_selfupdate.py:897` `self_update_trigger`, `:1027` `_self_update_run_service_locked`, `:1277` `self_update_recover_request` | admission | `state/selfupdate.request`, `state/selfupdate.inflight`; `lhpc self-update --recover-request` | `tests/install/test_selfupdate_service.py::test_trigger_writes_exclusive_request_no_systemctl`, `::test_recover_inflight_requires_dead_helper`, `::test_run_service_refuses_preexisting_inflight_preserving_both` |
 | self-update | config migration is recorded before the checkout moves; a damaged journal blocks with no change ([deployment](deployment.md#self-update)) | `lhpc/core/selfupdate.py:600` `classify_journal` | the self-update lock | `state/selfupdate-migrate.json` and its git anchor | `tests/install/test_selfupdate_migration.py::test_interrupted_migration_recovered_by_fresh_service`, `::test_journal_persist_failure_refuses_before_mutation`, `::test_malformed_journal_blocks_without_mutation_or_deletion` |
 | radio / band ownership | one stack per band; a start is refused with the holder named ([Radios](#radios-bands-and-resource-claims)) | `lhpc/core/resources.py:49` `interpret_conflicts` | `claim.<resource>` keys in the lifecycle bundle | none (observed state) | `tests/core/test_run_order.py::test_same_frequency_blocks_second_stack`; `tests/web/test_daemon_params_web.py::test_app_apply_refused_on_a_band_another_stack_uses`; `tests/stacks/test_hardware.py::test_probe_refused_while_a_direct_radio_stack_owns_the_band` |
 | radio / band ownership | the SPI bus is shared only through the daemon's `spi0.lock` ([Radios](#radios-bands-and-resource-claims)) | the daemon, not LHPC; LHPC models it as the `spi.bus.0` / `spi.bus.0.unlocked` claims | the daemon's flock `state/loraham/spi0.lock` | — | `tests/core/test_resources_conflicts.py::test_cooperative_peers_do_not_conflict`, `::test_meshtastic_conflicts_with_daemon_on_868_radio_not_spi`; gap: the lock itself (daemon side) |
diff --git a/docs/cli.md b/docs/cli.md
index faaf644..c28792c 100644
--- a/docs/cli.md
+++ b/docs/cli.md
@@ -240,9 +240,9 @@ is a host test too; it runs against the pinned upstream in the built environment
 
 ### update
 `lhpc update [<target>] [--source binary|pinned|dev|stable] [--accept-pin-mismatch] [--upstream] [--yes]`
-— update a stack/component.
+— update a stack/component. Which command for which situation:
+[keeping stacks current](operations.md#keeping-stacks-current).
 
-- Without `--source`, a binary-installed target stays binary; any other goes to `pinned`.
 - When the published binary lags this lhpc's pins, the update refuses and names the ways forward
   (self-update, a source build, `--accept-pin-mismatch` where allowed); cancelling keeps the
   working binary.
diff --git a/docs/operations.md b/docs/operations.md
index f9a7923..60942bb 100644
--- a/docs/operations.md
+++ b/docs/operations.md
@@ -75,6 +75,23 @@ On the binary channel:
 Files you add to a source checkout survive an update; editing upstream files blocks it
 ([ownership records](provenance.md#ownership-records)).
 
+### Keeping stacks current
+
+What to run in each situation and what you will see. The channels themselves:
+[provenance](provenance.md#selections).
+
+| situation | run | what you see |
+|---|---|---|
+| first install | `lhpc install <stack> --yes`, then on the source channel `lhpc build <stack> --yes`; or `lhpc auto-install --yes` for every stack (install and build) | the plan, then the install on the stack's default channel: the published binary where there is one for this platform, else `pinned` |
+| keep current at the pins this release was tested with | `lhpc self-update --apply`, then `lhpc update <stack> --yes`, then on the source channel `lhpc build <stack> --yes` | each source moves to `pinned` — your newest known-working composition for the stack, else the release's manifest pin. A binary-installed stack updates to the published binary |
+| follow development | `lhpc update <stack> --source dev --yes`, then `lhpc build <stack> --yes` | the branch tip; when adopting it fails, the update retries once at the known-working (else manifest-pin) commit and says *FELL BACK* |
+| follow stable | `lhpc update <stack> --source stable --yes`, then `lhpc build <stack> --yes` | the newest plain version tag, else the default branch's head |
+| binary where published | once: `lhpc install <stack> --source binary --yes`; afterwards `lhpc update <stack> --yes` stays on the binary | a published binary built from other commits than this lhpc's pins is refused; `Next:` offers `lhpc self-update --apply` and the source install |
+| a fetched release (graywolf) | `lhpc update graywolf --upstream --yes` for the newest release; `lhpc build graywolf --yes` for the pinned one | the release fetched and checked against its checksums, then a restart if the stack was running |
+| back to known-working after a failed update | nothing to undo for a source: a failed update keeps the previous source active (unless its line says *prior-dirty*). `lhpc update <stack> --yes` takes a stack on `dev` or `stable` back to its known-working (else manifest-pin) commit; a binary goes back by `lhpc install <stack> --source pinned --yes` | *Update INCOMPLETE* with the failed source's line |
+| an update was refused | the commands under `Next:`; where there is none, the refusal says *nothing to run here* and what has to be fixed on the box | the cause on the first line. A running stack: `lhpc stack stop <stack> --yes`. A left-over `src/.<name>.prev`: move it out of `src/`, then retry |
+| the first start after a source update | `lhpc build <stack> --yes` | until then `lhpc stack start` refuses a component *not built* or whose *sources changed since the last build*, naming `lhpc build <stack>` |
+
 ## Fast vs explicit
 
 - Fast & bounded (no build, no mutation, no RF): `status`, `explain`, `doctor`, `logs`, `web`
diff --git a/lhpc/core/install.py b/lhpc/core/install.py
index cbac94c..1b3cc81 100644
--- a/lhpc/core/install.py
+++ b/lhpc/core/install.py
@@ -686,7 +686,12 @@ class Installer:
                     return action
                 if outcome != "activated":         # "failed-clean": no journal, safe to drop
                     self._cleanup_owned_staging(txn, handle, staging.name)   # handle-safe
-                    action.status, action.detail = "failed", "activation failed — active source untouched"
+                    action.status, action.detail = "failed", (
+                        "activation failed — active source untouched. If "
+                        f"{self._source_rel(dest.with_name('.' + dest.name + '.prev'))} exists, it "
+                        "is left over from an earlier interrupted update and blocks every update "
+                        "of this source: move it out of src/ (lhpc no longer uses it), then retry "
+                        f"(lhpc update {comp.id} --yes)")
                     return action
                 return self._adopt_done(action, spec, dest, desc, source, signer_diags,
                                         expected=expected, kw_label=kw_label)
diff --git a/lhpc/core/service_binary_ops.py b/lhpc/core/service_binary_ops.py
index eeae6ad..fd07b4d 100644
--- a/lhpc/core/service_binary_ops.py
+++ b/lhpc/core/service_binary_ops.py
@@ -204,10 +204,12 @@ class BinaryOpsMixin:
             return ActionResult(False, _adm.reason, data={"admission_blocked": _adm.tag})
         except reslock.ResourceBusy as _busy:
             _stack.close()
-            return ActionResult(False, f"Binary install of '{stack_id}' blocked: {_busy}")
+            return ActionResult(False, f"Binary install of '{stack_id}' blocked: {_busy}",
+                                next_commands=["lhpc status"])
         except SourceTxnBlocked as _blocked:
             _stack.close()
-            return ActionResult(False, f"Binary install of '{stack_id}' blocked: {_blocked}")
+            return ActionResult(False, f"Binary install of '{stack_id}' blocked: {_blocked}",
+                                next_commands=["lhpc status"])
         with _stack:
             # ---- ONE lock-held boundary. Everything that reads or changes state lives
             # here, in this order: recover an interrupted transaction, read the current
@@ -215,7 +217,11 @@ class BinaryOpsMixin:
             # stopped, THEN open the journal and mutate.
             _rec_ok, _rec_why = self.binary_recover()
             if not _rec_ok:
-                return ActionResult(False, f"Binary install of '{stack_id}' blocked: {_rec_why}")
+                return ActionResult(False, f"Binary install of '{stack_id}' blocked: {_rec_why}",
+                                    details=["  nothing to run here — an earlier binary install left "
+                                             "state under state/binary/ that lhpc could not undo; "
+                                             "the box's operator fixes what is named above, then "
+                                             "the next binary install recovers it"])
             _pstate, _prec, _pwhy = self.binary_receipt_state(stack_id)
             if _pstate == "unsafe" and _prec is None:
                 # UNREADABLE ownership evidence: we cannot know which files the previous
@@ -312,7 +318,10 @@ class BinaryOpsMixin:
 
             baseline, berr = self._binary_registry_baseline(stack_id)
             if berr:
-                return ActionResult(False, f"Binary install of '{stack_id}' blocked: {berr}")
+                return ActionResult(False, f"Binary install of '{stack_id}' blocked: {berr}",
+                                    details=["  nothing to run here — the source ownership record "
+                                             "named above is damaged; the box's operator inspects "
+                                             "and removes it by hand, then retries"])
             txn = secrets.token_hex(8)
             try:
                 runtime_fs.mkdir(self._paths, "state")     # a not-yet-bootstrapped root has none
@@ -320,7 +329,11 @@ class BinaryOpsMixin:
                                           dir=str(self._paths.under("state")))
             except (OSError, PathContainmentError, ValueError) as exc:
                 return ActionResult(False, f"Binary install of '{stack_id}' blocked: cannot create a "
-                                           f"staging directory ({exc})")
+                                           f"staging directory ({exc})",
+                                    details=["  nothing to run here — the runtime root's state/ "
+                                             "folder is not writable (disk full or permissions); "
+                                             "the box's operator frees space or fixes it, then "
+                                             "retries"])
             # AUTHORITATIVE running recheck under the held locks (the source-update pattern):
             # a start that slipped in before the locks must refuse with ZERO mutation — a
             # binary update replaces the executable/firmware the stack is running from.
@@ -820,7 +833,10 @@ class BinaryOpsMixin:
             if _js != "valid" or _j is None or _j.get("stack") != stack_id \
                     or _j.get("txn") != txn:
                 return ActionResult(False, f"Cannot retire the binary install of '{stack_id}': "
-                                           "the open transaction is not this one")
+                                           "the open transaction is not this one",
+                                    details=["  nothing to run here — this is an lhpc defect (a "
+                                             "caller passed another transaction), not a problem "
+                                             "on the box; report it with this message"])
             state, rec, why = brx.receipt_state(self._paths, stack_id)
             return self._retire_body(stack_id, state, rec, why, force=force, locked=locked,
                                      txn=txn)
@@ -887,7 +903,8 @@ class BinaryOpsMixin:
                     False,
                     f"Refusing to retire the binary install of '{stack_id}': installed files "
                     f"changed since installation ({changed}).",
-                    details=["  Remove them by hand, or re-run with the force option."])
+                    details=["  Remove them by hand, or re-run with the force option."],
+                    next_commands=[f"lhpc uninstall {stack_id} --yes"])
         if not locked and (_running := self._binary_running_components(stack_id)):
             return ActionResult(
                 False, f"Refusing to retire the binary install of '{stack_id}': component(s) "
@@ -905,10 +922,15 @@ class BinaryOpsMixin:
             except (bi.BinaryInstallError, OSError, PathContainmentError, ValueError) as exc:
                 return ActionResult(
                     False, f"Cannot retire the binary install of '{stack_id}': its files could "
-                           f"not be moved aside ({exc})")
+                           f"not be moved aside ({exc})",
+                    details=["  nothing to run here — the runtime root is not writable (disk "
+                             "full or permissions); the box's operator fixes it, then retries"])
             if not brx.remove_receipt(self._paths, stack_id):
                 return ActionResult(False, f"Cannot retire the binary install of '{stack_id}': "
-                                           "its receipt could not be removed")
+                                           "its receipt could not be removed",
+                                    details=["  nothing to run here — state/binary/ is not "
+                                             "writable (disk full or permissions); the box's "
+                                             "operator fixes it, then retries"])
             # Prune here too: an emptied publish directory left behind reads as "destination
             # already exists" and the source adoption would SKIP it — a silent no-op install.
             self._prune_empty_dirs(rec.files)
@@ -953,7 +975,10 @@ class BinaryOpsMixin:
                     next_commands=[f"lhpc clean {stack_id} --purge --yes"])
         if not brx.remove_receipt(self._paths, stack_id):
             return ActionResult(False, f"Removed the binary files of '{stack_id}' but could not "
-                                       "remove its receipt — resolve state/binary by hand.")
+                                       "remove its receipt — resolve state/binary by hand.",
+                                details=["  nothing to run here — state/binary/ is not writable "
+                                         "(disk full or permissions); the box's operator fixes "
+                                         "it and removes the receipt by hand"])
         self.invalidate_snapshot()
         return ActionResult(True, f"Retired the binary install of '{stack_id}' "
                                   f"({removed} file(s) removed).")
diff --git a/lhpc/core/service_lifecycle_ops.py b/lhpc/core/service_lifecycle_ops.py
index cbded87..80b9b29 100644
--- a/lhpc/core/service_lifecycle_ops.py
+++ b/lhpc/core/service_lifecycle_ops.py
@@ -3182,7 +3182,10 @@ class LifecycleOpsMixin:
         # A caller-supplied run-specific log-base prefix (HMAC apply) is validated BEFORE any path is
         # constructed — a strict controller pattern bound to the FULL 32-hex run id (never marker-time only).
         if log_base_override and not _HMAC_LOG_BASE_RE.match(log_base_override):
-            return ActionResult(False, f"Refusing to build '{target}': invalid log-base prefix")
+            return ActionResult(False, f"Refusing to build '{target}': invalid log-base prefix",
+                                details=["  nothing to run here — this is an lhpc defect (an "
+                                         "internal caller passed a bad log name), not a problem "
+                                         "on the box; report it with this message"])
         items, err = self._resolve(target)
         if err:
             return ActionResult(False, err, next_commands=["lhpc list"])
@@ -3319,7 +3322,10 @@ class LifecycleOpsMixin:
         src_paths = sorted({c.source.path for _, c in buildable if c.source})
         ctx_err = self._auto_install_ctx_error(auto_install_ctx, src_paths)
         if ctx_err:
-            return ActionResult(False, f"Refusing to build '{target}': {ctx_err}")
+            return ActionResult(False, f"Refusing to build '{target}': {ctx_err}",
+                                details=["  nothing to run here — this is an lhpc defect (the "
+                                         "auto-install run's lock context does not match), not a "
+                                         "problem on the box; report it with this message"])
         try:
             with self._source_operation_guard(src_paths, op="build"):
                 # A MeshCore build recreates src/openhop-core/.venv — the interpreter an orphaned
diff --git a/lhpc/core/service_maintenance.py b/lhpc/core/service_maintenance.py
index 936b3dc..fc07f8d 100644
--- a/lhpc/core/service_maintenance.py
+++ b/lhpc/core/service_maintenance.py
@@ -926,7 +926,8 @@ class MaintenanceOpsMixin:
         restarts the stack if it was running. Refuses when not actually behind upstream."""
         st = self.graywolf_upstream_state(target)
         if not st:
-            return ActionResult(False, f"'{target}' is not an upstream-tracking package")
+            return ActionResult(False, f"'{target}' is not an upstream-tracking package",
+                                next_commands=[f"lhpc update {target} --yes"])
         if not st.get("latest"):
             return ActionResult(False, "run the upstream check first",
                                 next_commands=[f"lhpc status {target}"])
@@ -995,7 +996,11 @@ class MaintenanceOpsMixin:
             runtime_fs.atomic_write(self._paths, marker,
                                     BUILD_MARKER_TEXT + self._consumed_source_lines(main), 0o644)
         except (OSError, PathContainmentError) as exc:
-            return ActionResult(False, f"fetched {version} but could not re-mark built: {exc}")
+            return ActionResult(False, f"fetched {version} but could not re-mark built: {exc}",
+                                details=[f"  the build fetches the pinned release again; run "
+                                         f"`lhpc update {target} --upstream --yes` after it for "
+                                         f"{version}"],
+                                next_commands=[f"lhpc build {target} --yes"])
         notes = [f"  [ok] fetched upstream {version} (verified vs checksums.txt)"]
         restart_ok = True
         # `was_running` was sampled BEFORE a fetch that can run for minutes; an
@@ -1783,7 +1788,10 @@ class MaintenanceOpsMixin:
                 data={"channel": "binary"})
         all_items = self._with_source(target)
         if not all_items:
-            return self._unknown_stack(target) if target else ActionResult(False, "No sources.")
+            return self._unknown_stack(target) if target else ActionResult(
+                False, "No sources.",
+                details=["  nothing to run here — the manifest declares no managed source, so "
+                         "there is nothing to update"])
         # A NAMED component updates exactly itself; a stack (or the empty "all"
         # target) skips its optional libs/firmware — EXCEPT hard build dependencies
         # (`build_requires`, e.g. the daemon's RadioLib), which are updated with their
@@ -1796,7 +1804,10 @@ class MaintenanceOpsMixin:
             items = [(s, c) for s, c in all_items if not c.optional or c.id in required]
         ctx_err = self._auto_install_ctx_error(auto_install_ctx, {c.source.path for _, c in items})
         if ctx_err:
-            return ActionResult(False, f"Refusing to update '{target or 'all'}': {ctx_err}")
+            return ActionResult(False, f"Refusing to update '{target or 'all'}': {ctx_err}",
+                                details=["  nothing to run here — this is an lhpc defect (the "
+                                         "auto-install run's lock context does not match), not a "
+                                         "problem on the box; report it with this message"])
         if not apply:
             # The dry-run is the explicit freshness check (`lhpc update --check`):
             # it is the ONLY place that contacts the remote (git ls-remote). GET web
@@ -1804,8 +1815,8 @@ class MaintenanceOpsMixin:
             details = []
             for _, c in items:
                 fresh = self.update_status(c)
-                details.append(f"  {c.id}: {fresh} — fetch newest from "
-                               f"{c.source.remote or 'local checkout'}")
+                details.append(f"  {c.id}: upstream {fresh} — this update fetches the "
+                               f"{source} version from {c.source.remote or 'local checkout'}")
             return ActionResult(
                 True, f"Update plan for '{target or 'all'}': refresh {len(items)} source(s) "
                 "from the remote.",
@@ -1869,21 +1880,34 @@ class MaintenanceOpsMixin:
                     return ActionResult(False, f"Refusing to update '{target or 'all'}': "
                                         "shared-source remote configuration is "
                                         "inconsistent.",
-                                        details=[f"  {c}" for c in conflicts])
+                                        details=[*(f"  {c}" for c in conflicts),
+                                                 "  nothing to run here — the box's operator "
+                                                 "sets one remote for every component of that "
+                                                 "checkout ([remotes] in config/local.toml), "
+                                                 "then retries"])
                 groups, plan_conflicts = self._plan_source_groups(items, source,
                                                                   exact_pin=exact_pin)
                 if plan_conflicts:
                     return ActionResult(False, f"Refusing to update '{target or 'all'}': "
                                         "incompatible source resolutions for a shared "
                                         "checkout.",
-                                        details=[f"  {c}" for c in plan_conflicts])
+                                        details=[*(f"  {c}" for c in plan_conflicts),
+                                                 "  nothing to run here — the stacks sharing "
+                                                 "that checkout ask for different versions; the "
+                                                 "box's operator updates them with one --source "
+                                                 "or re-confirms known-working on each, then "
+                                                 "retries"])
                 # An update REPLACES the source tree, and the MeshCore identity may still
                 # live only in the template inside it. Copy it out now — after every
                 # refusal check, with all locks held, before the first mutation.
                 _id_err = self.meshcore_identity_guard([c for _s, c in items])
                 if _id_err:
                     return ActionResult(False, f"Refusing to update '{target or 'all'}': "
-                                               f"{_id_err}")
+                                               f"{_id_err}",
+                                        details=["  nothing to run here — the MeshCore identity "
+                                                 "could not be copied out of the source; the "
+                                                 "box's operator checks the file named above, "
+                                                 "then retries"])
                 inst = self._installer()
                 out, ok = [], True
                 mutated_paths = []
diff --git a/lhpc/core/service_selfupdate.py b/lhpc/core/service_selfupdate.py
index e458d7d..d699048 100644
--- a/lhpc/core/service_selfupdate.py
+++ b/lhpc/core/service_selfupdate.py
@@ -278,14 +278,23 @@ class SelfUpdateOpsMixin:
                                         "unreadable, corrupt or unsafe. No upstream check was made — "
                                         "recovery needed (inspect state/selfupdate-migrate.json).",
                                         data={"journal_corrupt": True,
-                                              **selfupdate.status_view(self._paths)})
+                                              **selfupdate.status_view(self._paths)},
+                                        details=["  nothing to run here — "
+                                                 "state/selfupdate-migrate.json is damaged; the "
+                                                 "box's operator inspects it and removes it, then "
+                                                 "retries"])
                 if status == "recovery_required":
                     return ActionResult(False, "Self-update check blocked: the checkout is at an "
                                         "unexpected commit for a recorded migration transition. No "
                                         "upstream check was made — recovery required (inspect "
                                         "state/selfupdate-migrate.json).",
                                         data={"recovery_required": True,
-                                              **selfupdate.status_view(self._paths)})
+                                              **selfupdate.status_view(self._paths)},
+                                        details=["  nothing to run here — the checkout is not at "
+                                                 "the commit state/selfupdate-migrate.json "
+                                                 "records (it was moved by hand); the box's "
+                                                 "operator inspects that file and the checkout "
+                                                 "before anything else"])
                 # Embed the LIVE controller-identity verdict into the SAME atomic envelope
                 # write (a separate field could be dropped by a later refresh). GET/status
                 # then renders the cached verdict only.
@@ -297,13 +306,22 @@ class SelfUpdateOpsMixin:
                                 data={**view, "deferred": True})
         except selfupdate.UpdateLockError:
             return ActionResult(False, "Could not check upstream (unsafe runtime state).",
-                                data=selfupdate.status_view(self._paths))
+                                data=selfupdate.status_view(self._paths),
+                                details=["  nothing to run here — the runtime root's state/ "
+                                         "folder is not usable (disk full, permissions or a "
+                                         "damaged lock file); the box's operator fixes it, then "
+                                         "retries"])
         if not view["is_git"]:
             return ActionResult(False, "Self-update is unavailable (lhpc is not a git checkout).",
-                                data=view)
+                                data=view,
+                                details=["  nothing to run here — lhpc is installed without git "
+                                         "(a copied folder or a package); only an install made by "
+                                         "install.sh can self-update — the box's operator "
+                                         "reinstalls it that way"])
         if not view["have_upstream"]:
             return ActionResult(False, f"Could not reach upstream: {view.get('upstream_error', '')}.",
-                                data=view)
+                                data=view,
+                                next_commands=["lhpc self-update"])
         if view["update_available"]:
             msg = (f"Update available — upstream {view['upstream_head_short']}"
                    f" (v{view['upstream_version'] or '?'}).")
@@ -337,7 +355,10 @@ class SelfUpdateOpsMixin:
                 blk = self._self_update_blockers()
                 if blk:
                     return ActionResult(False, f"Self-update blocked: {blk[0]} — resolve it before "
-                                        "self-updating.", data={f"blocked_by_{blk[1]}": True})
+                                        "self-updating.", data={f"blocked_by_{blk[1]}": True},
+                                        details=["  wait for that work to finish (or recover it "
+                                                 "on its page in the console), then retry"],
+                                        next_commands=["lhpc self-update --apply"])
                 # LIVE identity gate (recomputed here, NEVER trusting the cache): only a genuinely
                 # UNSAFE self-hosted checkout blocks apply before any mutation.
                 if self.controller() is not None:
@@ -345,7 +366,14 @@ class SelfUpdateOpsMixin:
                     if idv.get("status") == "unsafe":
                         return ActionResult(False, f"Self-update blocked: unsafe controller identity "
                                             f"({idv['reason']}). No changes were made.",
-                                            data={"identity_unsafe": True, "identity": idv})
+                                            data={"identity_unsafe": True, "identity": idv},
+                                            next_commands=[f"git -C {self._paths.runtime_root}/src/"
+                                                           "loraham-pi-control switch main",
+                                                           "lhpc self-update --apply"],
+                                            details=["  `git switch main` in the checkout fixes "
+                                                     "a detached HEAD or another branch; for any "
+                                                     "other reason above, the box's operator "
+                                                     "restores the layout install.sh made"])
                 # controller-runtime EXCLUSIVE (so the running web server, holding it SHARED, can never
                 # have its source mutated underneath it), THEN the self-update lock. Both non-blocking.
                 with (selfupdate.controller_runtime_lock(self._paths, exclusive=True),
@@ -355,24 +383,34 @@ class SelfUpdateOpsMixin:
             return ActionResult(False, _adm.reason, data={"admission_blocked": _adm.tag})
         except reslock.ResourceBusy:
             return ActionResult(False, "A task is starting right now (admission contended) — try the "
-                                "update again shortly.", data={"contended": True})
+                                "update again shortly.", data={"contended": True},
+                                next_commands=["lhpc self-update --apply"])
         except selfupdate.ControllerRuntimeBusy:
             return ActionResult(
                 False, "lhpc-web.service is running — stop it, update, then start it again.",
-                details=["systemctl --user stop lhpc-web",
-                         "lhpc self-update --apply",
-                         "systemctl --user start lhpc-web",
-                         "(or just click 'Update now' in the web console — it does all this)"],
+                details=["(or just click 'Update now' in the web console — it does all this)"],
+                next_commands=["systemctl --user stop lhpc-web",
+                               "lhpc self-update --apply",
+                               "systemctl --user start lhpc-web"],
                 data={"web_running": True})
         except selfupdate.ControllerRuntimeLockError:
             return ActionResult(False, "Could not acquire the controller-runtime lock (unsafe runtime "
-                                "state) — aborting without changes.", data={"lock_error": True})
+                                "state) — aborting without changes.", data={"lock_error": True},
+                                details=["  nothing to run here — the runtime root's state/ "
+                                         "folder is not usable (disk full, permissions or a "
+                                         "damaged lock file); the box's operator fixes it, then "
+                                         "retries"])
         except selfupdate.SelfUpdateBusy:
             return ActionResult(False, "A self-update is already in progress — try again shortly.",
-                                data={"busy": True})
+                                data={"busy": True},
+                                next_commands=["lhpc self-update --apply"])
         except selfupdate.UpdateLockError:
             return ActionResult(False, "Could not acquire the self-update lock (unsafe runtime state) "
-                                "— aborting without changes.", data={"lock_error": True})
+                                "— aborting without changes.", data={"lock_error": True},
+                                details=["  nothing to run here — the runtime root's state/ "
+                                         "folder is not usable (disk full, permissions or a "
+                                         "damaged lock file); the box's operator fixes it, then "
+                                         "retries"])
 
     def _refresh_units_post_update(self):
         """VERIFY the managed units against the NEW checkout, out of process. Returns
@@ -522,7 +560,8 @@ class SelfUpdateOpsMixin:
         if _os.environ.get("INVOCATION_ID"):
             return ActionResult(False, "refusing to stop/start services from a managed unit — run "
                                 "`lhpc self-update --apply` from an interactive operator shell",
-                                data={"reason": "managed-unit"})
+                                data={"reason": "managed-unit"},
+                                next_commands=["lhpc self-update --apply"])
         _S = 30.0
         act = self._system.runner.run(
             ["systemctl", "--user", "is-active", "--quiet", updater_units.WEB_UNIT], _S)
@@ -535,9 +574,9 @@ class SelfUpdateOpsMixin:
                 stop = self._system.runner.run(["systemctl", "--user", "stop", updater_units.WEB_UNIT], _S)
                 if getattr(stop, "not_found", False) or stop.returncode != 0:
                     return ActionResult(False, "could not stop lhpc-web.service — stop it manually then retry",
-                                        details=["systemctl --user stop lhpc-web",
-                                                 "lhpc self-update --apply",
-                                                 "systemctl --user start lhpc-web"],
+                                        next_commands=["systemctl --user stop lhpc-web",
+                                                       "lhpc self-update --apply",
+                                                       "systemctl --user start lhpc-web"],
                                         data={"stop_failed": True})
                 try:
                     res = self._apply_and_sync(force)         # admission still held; venv synced here
@@ -553,13 +592,15 @@ class SelfUpdateOpsMixin:
                                         "with: systemctl --user start lhpc-web.service",
                                         details=tuple(res.details),
                                         data={**dict(res.data), "web_restart_failed": True,
-                                              "update_applied": bool(res.data.get("update_applied"))})
+                                              "update_applied": bool(res.data.get("update_applied"))},
+                                        next_commands=["systemctl --user start lhpc-web.service"])
                 return self._operator_outcome(res, restarted=True)
         except AdmissionRefused as _adm:
             return ActionResult(False, _adm.reason, data={"admission_blocked": _adm.tag})
         except reslock.ResourceBusy:
             return ActionResult(False, "A task is starting right now (admission contended) — retry the "
-                                "update.", data={"contended": True})
+                                "update.", data={"contended": True},
+                                next_commands=["lhpc self-update --apply"])
 
     @staticmethod
     def _operator_outcome(res: ActionResult, *, restarted: bool) -> ActionResult:
@@ -597,12 +638,19 @@ class SelfUpdateOpsMixin:
             return ActionResult(False, "Self-update blocked: the migration journal is missing-but-"
                                 "present-unreadable, corrupt or unsafe. No changes were made — recovery "
                                 "needed (inspect / remove state/selfupdate-migrate.json).",
-                                data={"journal_corrupt": True})
+                                data={"journal_corrupt": True},
+                                details=["  nothing to run here — state/selfupdate-migrate.json "
+                                         "is damaged; the box's operator inspects it and removes "
+                                         "it, then retries"])
         if status == "recovery_required":
             return ActionResult(False, "Self-update blocked: the checkout is at an unexpected commit for "
                                 "a recorded migration transition. No changes were made — recovery "
                                 "required (inspect state/selfupdate-migrate.json).",
-                                data={"recovery_required": True})
+                                data={"recovery_required": True},
+                                details=["  nothing to run here — the checkout is not at the "
+                                         "commit state/selfupdate-migrate.json records (it was "
+                                         "moved by hand); the box's operator inspects that file "
+                                         "and the checkout before anything else"])
         completed = env.get("completed") if env else None
         prepared = env.get("prepared") if env else None
 
@@ -626,7 +674,11 @@ class SelfUpdateOpsMixin:
             if anchor is None:                                           # defensive (classifier verified)
                 return ActionResult(False, "Self-update blocked: the recorded migration transition is "
                                     "not authorised by a matching durable anchor. No changes were made "
-                                    "— recovery required.", data={"recovery_required": True})
+                                    "— recovery required.", data={"recovery_required": True},
+                                    details=["  nothing to run here — the migration recorded in "
+                                             "state/selfupdate-migrate.json has no matching "
+                                             "record in the checkout's git; the box's operator "
+                                             "inspects that file before anything else"])
             m, remaining = self._run_migration(anchor["pending"], anchor["from_head"])
             migrated += m
             if remaining:
@@ -676,7 +728,11 @@ class SelfUpdateOpsMixin:
         except selfupdate.JournalPersistError:
             return ActionResult(False, "Refusing to self-update: could not durably record the config-"
                                 "migration intent before changing source. No changes were made.",
-                                data={"journal_write_failed": True})
+                                data={"journal_write_failed": True},
+                                details=["  nothing to run here — the runtime root's state/ "
+                                         "folder is not usable (disk full, permissions or a "
+                                         "damaged lock file); the box's operator fixes it, then "
+                                         "retries"])
 
         # 5. On a REAL advance WITH candidates (hook.written -> a valid anchor + journal exist), promote
         #    the prepared transition to `completed` (keeping the anchor), then migrate. Fully resolved ->
@@ -750,7 +806,8 @@ class SelfUpdateOpsMixin:
             details += [n for n in (migrated_note, pending_note) if n]
             details += list(fw_notes)
             return ActionResult(False, res["message"], data=data,
-                                details=tuple(d for d in details if d))
+                                details=tuple(d for d in details if d),
+                                next_commands=list(instr["commands"]))
         if res.get("already"):                               # nothing to update; may have recovered pending
             details = tuple(n for n in (migrated_note, pending_note) if n)
             return ActionResult(True, res["message"], data=data, details=details)
@@ -858,20 +915,33 @@ class SelfUpdateOpsMixin:
         integ = self.updater_integration()
         if integ["status"] == "recovery_required":
             return ActionResult(False, "A previous update needs recovery first — run "
-                                "`lhpc self-update --recover-request`.", data={"recovery_required": True})
+                                "`lhpc self-update --recover-request`.", data={"recovery_required": True},
+                                next_commands=["lhpc self-update --recover-request"])
         if integ["status"] != "ok":
             return ActionResult(False, "One-click update is unavailable — the web/updater units are "
                                 f"not the canonical managed set ({integ['status']}). Run `lhpc "
                                 "self-update --repair-integration`, or `lhpc self-update --apply`.",
-                                data={"integration": integ["status"]})
+                                data={"integration": integ["status"]},
+                                next_commands=["lhpc self-update --repair-integration",
+                                               "lhpc self-update --apply"])
         st = self.self_update_status()
         if not st.get("available"):
             return ActionResult(False, "Self-update is unavailable — lhpc is not running from a "
-                                "git checkout.", data={"unavailable": True})
+                                "git checkout.", data={"unavailable": True},
+                                details=["  nothing to run here — lhpc is installed without git "
+                                         "(a copied folder or a package); only an install made by "
+                                         "install.sh can self-update — the box's operator "
+                                         "reinstalls it that way"])
         idv = st.get("identity")
         if isinstance(idv, dict) and idv.get("status") == "unsafe":
             return ActionResult(False, "Self-update blocked: unsafe controller identity "
-                                f"({idv.get('reason', '')}).", data={"identity_unsafe": True})
+                                f"({idv.get('reason', '')}).", data={"identity_unsafe": True},
+                                next_commands=[f"git -C {self._paths.runtime_root}/src/"
+                                               "loraham-pi-control switch main",
+                                               "lhpc self-update --apply"],
+                                details=["  `git switch main` in the checkout fixes a detached "
+                                         "HEAD or another branch; for any other reason above, the "
+                                         "box's operator restores the layout install.sh made"])
         mode = "overwrite" if overwrite else "normal"
         import contextlib
 
@@ -886,21 +956,28 @@ class SelfUpdateOpsMixin:
                 self._acquire_key(adm, self.ADMISSION_KEY, "self-update-trigger", "")
             except reslock.ResourceBusy:
                 return ActionResult(False, "A task is starting right now (admission contended) — retry "
-                                    "the update.", data={"contended": True})
+                                    "the update.", data={"contended": True},
+                                    next_commands=["lhpc self-update --apply"])
             except AdmissionRefused as _adm:
                 # The power-pending gate lives INSIDE _acquire_key (one choke point): a
                 # reboot/shutdown in flight must not admit a self-update it would kill.
                 return ActionResult(False, _adm.reason, data={"admission_blocked": _adm.tag})
             if self.uninstall_guard_blocks():
                 return ActionResult(False, "A controller uninstall is in progress — cannot self-update.",
-                                    data={"uninstalling": True})
+                                    data={"uninstalling": True},
+                                    next_commands=["lhpc self-update --recover-request"])
             if self.classify_request() != "absent":
                 return ActionResult(False, "An update request is already pending — the console is about "
-                                    "to update.", data={"already_pending": True})
+                                    "to update.", data={"already_pending": True},
+                                    details=["  nothing to run here — the queued update starts by "
+                                             "itself; wait for the console to come back"])
             blk = self._self_update_blockers()
             if blk:
                 return ActionResult(False, f"Self-update blocked: {blk[0]}.",
-                                    data={f"blocked_by_{blk[1]}": True})
+                                    data={f"blocked_by_{blk[1]}": True},
+                                    details=["  wait for that work to finish (or recover it on "
+                                             "its page in the console), then retry"],
+                                    next_commands=["lhpc self-update --apply"])
             if not queue:
                 return ActionResult(True, "Update can be queued.", data={"preflight": True,
                                                                           "mode": mode})
@@ -911,10 +988,16 @@ class SelfUpdateOpsMixin:
                 m.close()
             except FileExistsError:
                 return ActionResult(False, "An update request is already pending — the console is about "
-                                    "to update.", data={"already_pending": True})
+                                    "to update.", data={"already_pending": True},
+                                    details=["  nothing to run here — the queued update starts by "
+                                             "itself; wait for the console to come back"])
             except Exception as exc:                           # containment / fs error
                 return ActionResult(False, f"Could not queue the update request: {exc}",
-                                    data={"trigger_failed": True})
+                                    data={"trigger_failed": True},
+                                    details=["  nothing to run here — the runtime root's state/ "
+                                             "folder is not usable (disk full, permissions or a "
+                                             "damaged lock file); the box's operator fixes it, "
+                                             "then retries"])
         return ActionResult(True, "Update queued — the console will stop, update itself and come "
                             "back automatically.", data={"triggered": True, "mode": mode})
 
@@ -936,7 +1019,9 @@ class SelfUpdateOpsMixin:
                 self._admit_raw(adm, "self-update-helper")
             except reslock.ResourceBusy:
                 return ActionResult(False, "A task is starting right now (admission contended) — the "
-                                    "update helper will retry on the next request.", data={"contended": True})
+                                    "update helper will retry on the next request.", data={"contended": True},
+                                    details=["  nothing to run here — the update helper takes the "
+                                             "next request; click Update again in a moment"])
             return self._self_update_run_service_locked()
 
     def _self_update_run_service_locked(self) -> ActionResult:
@@ -960,10 +1045,12 @@ class SelfUpdateOpsMixin:
         except FileExistsError:
             return ActionResult(False, "A previous update is already in flight — recovery required "
                                 "(`lhpc self-update --recover-request`).",
-                                data={"recovery_required": True})
+                                data={"recovery_required": True},
+                                next_commands=["lhpc self-update --recover-request"])
         except Exception as exc:
             return ActionResult(False, f"Could not claim the update request: {exc}",
-                                data={"claim_failed": True})
+                                data={"claim_failed": True},
+                                next_commands=["lhpc self-update --recover-request"])
         # Read mode, then overwrite the in-flight record with a process-identity claim.
         try:
             mode = runtime_fs.read_text_regular(self._paths, inflight, max_bytes=4096).strip()
@@ -975,7 +1062,8 @@ class SelfUpdateOpsMixin:
             selfupdate.record_last_apply_strict(self._paths, ok=False,
                                                 summary="Update request was malformed — recovery required.")
             return ActionResult(False, "Malformed update request — recovery required.",
-                                data={"malformed": True})
+                                data={"malformed": True},
+                                next_commands=["lhpc self-update --recover-request"])
         force = (mode == "overwrite")
         runtime_fs.write_marker(self._paths, inflight, json.dumps(self._helper_identity(mode)))
         # PROVE this exact helper owns the in-flight record it just wrote (durable PID + /proc start
@@ -987,9 +1075,11 @@ class SelfUpdateOpsMixin:
                 summary="In-flight update ownership could not be proven — recovery required.")
             return ActionResult(False, "In-flight update ownership could not be proven — recovery "
                                 "required (`lhpc self-update --recover-request`).",
-                                data={"ownership_unproven": True})
+                                data={"ownership_unproven": True},
+                                next_commands=["lhpc self-update --recover-request"])
 
-        res = ActionResult(False, "Self-update service did not run.", data={})
+        res = ActionResult(False, "Self-update service did not run.", data={},
+                           next_commands=["lhpc self-update --recover-request"])
         try:
             # Web is stopped (Conflicts+After) so its SHARED lock is released; take EXCLUSIVE with
             # a short bounded retry to cover the stop-completion window, then apply.
@@ -1001,13 +1091,18 @@ class SelfUpdateOpsMixin:
                 except selfupdate.ControllerRuntimeBusy:
                     if _time.monotonic() >= deadline:
                         res = ActionResult(False, "The console did not release the controller-runtime "
-                                           "lock — no changes made.", data={"web_running": True})
+                                           "lock — no changes made.", data={"web_running": True},
+                                           next_commands=["lhpc self-update --apply"])
                         raise _StopRun() from None
                     _time.sleep(0.5)
                 except selfupdate.ControllerRuntimeLockError:
                     res = ActionResult(False, "Could not acquire the controller-runtime lock "
                                        "(unsafe runtime state) — no changes made.",
-                                       data={"lock_error": True})
+                                       data={"lock_error": True},
+                                       details=["  nothing to run here — the runtime root's "
+                                                "state/ folder is not usable (disk full, "
+                                                "permissions or a damaged lock file); the box's "
+                                                "operator fixes it, then retries"])
                     raise _StopRun() from None
             res = self.self_update_apply(force=force)
             if self._source_advanced(res):                  # incl. the reset+clean-failed partial
@@ -1030,7 +1125,9 @@ class SelfUpdateOpsMixin:
                                                f"{sys.executable} -m pip install -e {root} manually, "
                                                "then restart the console."
                                                + (f" ({detail})" if detail else "")),
-                                           data={**dict(res.data), "venv_sync_failed": True})
+                                           data={**dict(res.data), "venv_sync_failed": True},
+                                           next_commands=[f"{sys.executable} -m pip install -e {root}",
+                                                          "systemctl --user restart lhpc-web.service"])
                     else:
                         # Refresh the managed units with the NEW code. This path applies
                         # inline (it does not go through _apply_and_sync), so without this
@@ -1060,12 +1157,14 @@ class SelfUpdateOpsMixin:
         if not selfupdate.record_last_apply_strict(self._paths, ok=bool(res.ok), summary=res.summary):
             return ActionResult(False, "Update outcome could not be recorded durably — recovery "
                                 "required (`lhpc self-update --recover-request`).",
-                                data={**dict(res.data), "record_failed": True})
+                                data={**dict(res.data), "record_failed": True},
+                                next_commands=["lhpc self-update --recover-request"])
         try:
             runtime_fs.unlink(self._paths, inflight)
         except Exception as exc:
             return ActionResult(False, res.summary + f" (in-flight marker cleanup FAILED: {exc} — "
-                                "recovery required)", data={**dict(res.data), "cleanup_failed": True})
+                                "recovery required)", data={**dict(res.data), "cleanup_failed": True},
+                                next_commands=["lhpc self-update --recover-request"])
         return res
 
     def _helper_owns_inflight(self) -> bool:
@@ -1190,12 +1289,21 @@ class SelfUpdateOpsMixin:
             req_res = self._recover_update_state()
         except Exception as exc:
             req_res = ActionResult(False, f"Update-state recovery failed unexpectedly: {exc}",
-                                   data={"request_recovery_error": True})
+                                   data={"request_recovery_error": True},
+                                   details=["  nothing to run here — recovery stopped on the "
+                                            "unexpected error above; the box's operator checks "
+                                            "state/selfupdate.request and "
+                                            "state/selfupdate.inflight by hand and reports the "
+                                            "error"])
         try:
             guard_res = self._recover_uninstall_guard()
         except Exception as exc:
             guard_res = ActionResult(False, f"Uninstall-guard recovery failed unexpectedly: {exc}",
-                                     data={"guard": "error"})
+                                     data={"guard": "error"},
+                                     details=["  nothing to run here — recovery stopped on the "
+                                              "unexpected error above; the box's operator checks "
+                                              "the .lhpc-uninstalling guard in the runtime root "
+                                              "by hand and reports the error"])
         if guard_res is None:
             return req_res                          # no guard: request-only result and wording
         ok = req_res.ok and guard_res.ok
@@ -1222,7 +1330,10 @@ class SelfUpdateOpsMixin:
                     return None
                 except (OSError, PathContainmentError):
                     return ActionResult(False, f"An uninstall guard exists but is unreadable/unsafe "
-                                        f"— NOT removing it ({path}).", data={"guard": "unsafe"})
+                                        f"— NOT removing it ({path}).", data={"guard": "unsafe"},
+                                        details=["  nothing to run here — the box's operator "
+                                                 "makes sure no uninstall runs, then removes the "
+                                                 "guard file named above by hand"])
                 try:
                     rec = json.loads(raw)
                     pid, start = _guard_owner_ints(rec)
@@ -1230,21 +1341,31 @@ class SelfUpdateOpsMixin:
                     return ActionResult(False, f"An uninstall guard exists but its owner record is "
                                         f"malformed — cannot prove the owner ceased; verify no "
                                         f"uninstall is running, then remove {path} by hand.",
-                                        data={"guard": "malformed"})
+                                        data={"guard": "malformed"},
+                                        details=["  nothing to run here — the box's operator does "
+                                                 "the check and the removal named above"])
                 if not _proc_ceased(pid, start):
                     return ActionResult(False, "An uninstall guard is held by a LIVE process (an "
                                         "uninstall may be running) — not removing it.",
-                                        data={"guard": "live"})
+                                        data={"guard": "live"},
+                                        details=["  nothing to run here — an uninstall is "
+                                                 "running; it removes the guard itself when it "
+                                                 "ends — wait for it"])
                 try:
                     runtime_fs.unlink(self._paths, path)
                 except (OSError, PathContainmentError) as exc:
                     return ActionResult(False, f"Could not remove the stale uninstall guard: {exc}",
-                                        data={"guard": "unlink_failed"})
+                                        data={"guard": "unlink_failed"},
+                                        details=["  nothing to run here — the runtime root's "
+                                                 "state/ folder is not usable (disk full, "
+                                                 "permissions or a damaged lock file); the box's "
+                                                 "operator fixes it, then retries"])
                 return ActionResult(True, f"Cleared a stale uninstall guard (pid {pid} proven "
                                     "ceased).", data={"guard": "cleared"})
         except reslock.ResourceBusy as busy:
             return ActionResult(False, f"another uninstall-guard operation is in progress ({busy}) — "
-                                "retry.", data={"guard": "contended"})
+                                "retry.", data={"guard": "contended"},
+                                next_commands=["lhpc self-update --recover-request"])
 
     def _recover_update_state(self) -> ActionResult:
         """The request/in-flight half of recovery."""
@@ -1262,12 +1383,16 @@ class SelfUpdateOpsMixin:
             return ActionResult(False, "The in-flight update record is unreadable/malformed — its "
                                 "helper cannot be proven stopped. Ensure lhpc-selfupdate.service is "
                                 "not active, then remove state/selfupdate.inflight by hand.",
-                                data={"state": "malformed"})
+                                data={"state": "malformed"},
+                                details=["  nothing to run here — the box's operator does the "
+                                         "check and the removal named above"])
         # in_flight: verify the recorded process is gone.
         rec = json.loads(runtime_fs.read_text_regular(self._paths, inflight, max_bytes=4096))
         if not _proc_ceased(rec.get("pid"), rec.get("start_time")):
             return ActionResult(False, "An update is still running (helper process alive) — wait "
-                                "for it to finish before recovering.", data={"state": "running"})
+                                "for it to finish before recovering.", data={"state": "running"},
+                                details=["  nothing to run here — the update finishes by itself; "
+                                         "wait for it, then retry"])
         # Record the interrupted outcome DURABLY *before* removing the evidence — if the strict
         # write fails, keep the in-flight marker so recovery can be retried (never silently clear).
         from . import selfupdate as _su
@@ -1275,7 +1400,8 @@ class SelfUpdateOpsMixin:
                 summary="A previous update was interrupted and did not complete."):
             return ActionResult(False, "Could not record the interrupted outcome durably — the "
                                 "in-flight record is kept; try recovery again.",
-                                data={"record_failed": True})
+                                data={"record_failed": True},
+                                next_commands=["lhpc self-update --recover-request"])
         runtime_fs.unlink(self._paths, inflight)
         return ActionResult(True, "Cleared an interrupted update (helper had stopped); recorded it "
                             "as incomplete.", data={"cleared": "in_flight"})
@@ -1295,15 +1421,20 @@ class SelfUpdateOpsMixin:
         if not _op.isdir(_op.join(checkout, ".git")):
             return ActionResult(False, "Not a self-hosted deployment (no checkout at "
                                 f"{checkout}) — cannot manage web/updater units.",
-                                data={"not_self_hosted": True})
+                                data={"not_self_hosted": True},
+                                details=["  nothing to run here — lhpc runs from a dev checkout "
+                                         "or a copy, not the self-hosted layout install.sh makes; "
+                                         "the web/updater units are managed only there"])
         if self.uninstall_guard_blocks():
             return ActionResult(False, "An uninstall is in progress (.lhpc-uninstalling present) — "
                                 "recover it first (`lhpc self-update --recover-request`).",
-                                data={"uninstalling": True})
+                                data={"uninstalling": True},
+                                next_commands=["lhpc self-update --recover-request"])
         if self.classify_request() != "absent":
             return ActionResult(False, "An update request is pending/in-flight — run "
                                 "`lhpc self-update --recover-request` first.",
-                                data={"request_present": True})
+                                data={"request_present": True},
+                                next_commands=["lhpc self-update --recover-request"])
         ud = self._user_unit_dir()
         # The units log with StandardOutput=append:{root}/logs/... — systemd creates the FILE but not
         # the directory, and a repaired root may predate/have lost it (bootstrap normally makes it).
@@ -1315,13 +1446,18 @@ class SelfUpdateOpsMixin:
         try:
             actions = updater_units.write_set(ud, root)
         except ValueError as exc:
-            return ActionResult(False, str(exc), data={"write_refused": True})
+            return ActionResult(False, str(exc), data={"write_refused": True},
+                                details=["  nothing to run here — the box's operator moves the "
+                                         "unit file(s) named above out of the user unit folder, "
+                                         "then repairs again"])
         S = 20.0
         reload_res = self._system.runner.run(["systemctl", "--user", "daemon-reload"], timeout=S)
         if reload_res.returncode != 0:
             return ActionResult(False, "systemctl --user daemon-reload failed after writing the units "
                                 "— not proceeding (the units are on disk but not activated). Check "
-                                "`systemctl --user status`.", data={"daemon_reload_failed": True})
+                                "`systemctl --user status`.", data={"daemon_reload_failed": True},
+                                next_commands=["systemctl --user status",
+                                               "lhpc self-update --repair-integration"])
         # Authoritative loader check (operator shell HAS the bus): the ACTIVE fragment must be our
         # file AND carry NO drop-ins — a drop-in can override the sandbox / ExecStart /
         # InaccessiblePaths of the vetted unit, so either condition FAILS the repair before we
@@ -1336,11 +1472,15 @@ class SelfUpdateOpsMixin:
             if props.get("FragmentPath") != want:
                 return ActionResult(False, f"After writing units, {kind} still loads a different "
                                     f"fragment ({out.strip()[:120]}). A higher-priority unit or "
-                                    "mask shadows it — resolve manually.", data={"shadowed": kind})
+                                    "mask shadows it — resolve manually.", data={"shadowed": kind},
+                                    next_commands=[f"systemctl --user cat {kind}",
+                                                   "lhpc self-update --repair-integration"])
             if props.get("DropInPaths", "").strip():
                 return ActionResult(False, f"{kind} has an active drop-in override "
                                     f"({props['DropInPaths'].strip()[:120]}) — it can override the "
-                                    "sandbox; remove it, then repair.", data={"dropin": kind})
+                                    "sandbox; remove it, then repair.", data={"dropin": kind},
+                                    next_commands=[f"systemctl --user cat {kind}",
+                                                   "lhpc self-update --repair-integration"])
         # Enable both, and START the watcher now so a request marker is caught even before the web
         # is (re)started under the new unit. Every step's return code is CHECKED and fails the repair
         # truthfully — a partial integration is never reported as success, and the root marker is
@@ -1351,7 +1491,9 @@ class SelfUpdateOpsMixin:
             return ActionResult(False, "Installed the units but could not enable/start the request "
                                 "watcher (lhpc-selfupdate.path) — not proceeding. Check "
                                 "`systemctl --user status lhpc-selfupdate.path`.",
-                                data={"path_watcher_failed": True})
+                                data={"path_watcher_failed": True},
+                                next_commands=["systemctl --user status lhpc-selfupdate.path",
+                                               "lhpc self-update --repair-integration"])
         # Same for the nginx-restart watcher (the web console's bind-change escape hatch). NOTE the
         # deliberate startup-recovery semantics: `--now` with a stale request present fires ONE
         # restart immediately — marker consumed, fresh nginx (rate-limited; chosen, not accidental).
@@ -1361,12 +1503,16 @@ class SelfUpdateOpsMixin:
             return ActionResult(False, "Installed the units but could not enable/start the "
                                 "nginx-restart watcher (lhpc-nginx-restart.path) — not proceeding. "
                                 "Check `systemctl --user status lhpc-nginx-restart.path`.",
-                                data={"restart_watcher_failed": True})
+                                data={"restart_watcher_failed": True},
+                                next_commands=["systemctl --user status lhpc-nginx-restart.path",
+                                               "lhpc self-update --repair-integration"])
         web_en = self._system.runner.run(["systemctl", "--user", "enable", updater_units.WEB_UNIT],
                                          timeout=S)
         if web_en.returncode != 0:
             return ActionResult(False, "Could not enable the web service (lhpc-web.service) — not "
-                                "proceeding.", data={"web_enable_failed": True})
+                                "proceeding.", data={"web_enable_failed": True},
+                                next_commands=["systemctl --user status lhpc-web.service",
+                                               "lhpc self-update --repair-integration"])
         # Boot restore: plain `enable` (NEVER --now — enabling must not trigger a restore run;
         # restoration is additionally gated on [boot] restore + a canonical enabled web unit).
         # Systemd is demonstrably available at this point, so an enable failure is a REPAIR
@@ -1376,7 +1522,9 @@ class SelfUpdateOpsMixin:
         if br_en.returncode != 0:
             return ActionResult(False, "Could not enable the boot-restore unit "
                                 "(lhpc-boot-restore.service) — not proceeding.",
-                                data={"boot_restore_enable_failed": True})
+                                data={"boot_restore_enable_failed": True},
+                                next_commands=["systemctl --user status lhpc-boot-restore.service",
+                                               "lhpc self-update --repair-integration"])
         # The watcher MUST be active now — in BOTH modes (a migration's still-running OLD web does not
         # pull it up via Wants=, and a CLI repair must not silently leave it down) — otherwise a queued
         # request is never consumed. Fail BEFORE writing the root marker / restarting.
@@ -1386,14 +1534,18 @@ class SelfUpdateOpsMixin:
             return ActionResult(False, "The update path watcher (lhpc-selfupdate.path) is not active "
                                 "after enable --now — not proceeding. Check "
                                 "`systemctl --user status lhpc-selfupdate.path`.",
-                                data={"path_watcher_failed": True})
+                                data={"path_watcher_failed": True},
+                                next_commands=["systemctl --user status lhpc-selfupdate.path",
+                                               "lhpc self-update --repair-integration"])
         ract = self._system.runner.run(["systemctl", "--user", "is-active", "--quiet",
                                         updater_units.RESTART_PATH_UNIT], timeout=S)
         if ract.returncode != 0:
             return ActionResult(False, "The nginx-restart watcher (lhpc-nginx-restart.path) is not "
                                 "active after enable --now — not proceeding. Check "
                                 "`systemctl --user status lhpc-nginx-restart.path`.",
-                                data={"restart_watcher_failed": True})
+                                data={"restart_watcher_failed": True},
+                                next_commands=["systemctl --user status lhpc-nginx-restart.path",
+                                               "lhpc self-update --repair-integration"])
         if restart:
             rst = self._system.runner.run(["systemctl", "--user", "restart", updater_units.WEB_UNIT],
                                           timeout=S)
@@ -1405,7 +1557,9 @@ class SelfUpdateOpsMixin:
                                     "restart FAILED — the repair is NOT marked complete. Check "
                                     f"`systemctl --user status {updater_units.WEB_UNIT}` and "
                                     f"`tail -n 50 {web_log}`.",
-                                    data={"web_restart_failed": True})
+                                    data={"web_restart_failed": True},
+                                    next_commands=[f"systemctl --user status {updater_units.WEB_UNIT}",
+                                                   f"tail -n 50 {web_log}"])
         self._write_root_marker()          # ONLY after every required integration step succeeded
         details = [f"  {k}: {a}" for k, a in actions]
         details.append(self._enable_linger(S))
@@ -1460,13 +1614,17 @@ class SelfUpdateOpsMixin:
         # Refuse recovery / pending-request / uninstall BEFORE any preflight or write.
         if status == "recovery_required":
             return ActionResult(False, "A previous update needs recovery first — run "
-                                "`lhpc self-update --recover-request`.", data={"recovery_required": True})
+                                "`lhpc self-update --recover-request`.", data={"recovery_required": True},
+                                next_commands=["lhpc self-update --recover-request"])
         if self.uninstall_guard_blocks():
             return ActionResult(False, "An uninstall is in progress — recover it first.",
-                                data={"uninstalling": True})
+                                data={"uninstalling": True},
+                                next_commands=["lhpc self-update --recover-request"])
         if self.classify_request() != "absent":
             return ActionResult(False, "An update request is already pending — the console is about "
-                                "to update.", data={"request_present": True})
+                                "to update.", data={"request_present": True},
+                                details=["  nothing to run here — the queued update starts by "
+                                         "itself; wait for the console to come back"])
         # Fixable ONLY when every non-OK unit is missing/modified_ours (an ambiguous/foreign/
         # overridden/unsafe/unreadable unit is NOT auto-repairable).
         fixable_set = (updater_units.OK, updater_units.MISSING, updater_units.MODIFIED_OURS)
@@ -1476,7 +1634,11 @@ class SelfUpdateOpsMixin:
             detail = ", ".join(f"{k}: {v}" for k, v in bad.items())
             return ActionResult(False, "The web/updater units are not safely this deployment's "
                                 f"({detail}) — resolve them manually, then update.",
-                                data={"integration": status, "unfixable": bad})
+                                data={"integration": status, "unfixable": bad},
+                                details=["  nothing to run here — the units named above were "
+                                         "changed or replaced outside lhpc; the box's operator "
+                                         "restores or removes them from a shell, then runs the "
+                                         "update again"])
         # Bus preflight — cheap, read-only. A hardened (bus-blocked) console fails here BEFORE any
         # write and gets shell guidance.
         probe = self._system.runner.run(["systemctl", "--user", "show", "-p", "Version"], timeout=20.0)
@@ -1484,14 +1646,16 @@ class SelfUpdateOpsMixin:
             return ActionResult(False, "This console can't install systemd units itself (the user "
                                 "bus is unavailable). From a shell on this machine run "
                                 "`lhpc self-update --repair-integration`, then click Update.",
-                                data={"bus_unavailable": True})
+                                data={"bus_unavailable": True},
+                                next_commands=["lhpc self-update --repair-integration"])
         rep = self.self_update_repair_integration(restart=False)
         if not rep.ok:
             return rep
         if self.updater_integration()["status"] != "ok":                # repair must have converged
             return ActionResult(False, "Unit repair did not fully converge — run "
                                 "`lhpc self-update --repair-integration` from a shell.",
-                                data={"repair_incomplete": True})
+                                data={"repair_incomplete": True},
+                                next_commands=["lhpc self-update --repair-integration"])
         return self.self_update_trigger(overwrite=overwrite, queue=queue)
 
     def _write_root_marker(self) -> None:
diff --git a/tests/repo/test_refusal_remedy.py b/tests/repo/test_refusal_remedy.py
new file mode 100644
index 0000000..6987eba
--- /dev/null
+++ b/tests/repo/test_refusal_remedy.py
@@ -0,0 +1,162 @@
+"""The refusal-remedy contract on the install / update / self-update / build / binary-channel paths.
+
+A refusal (`ActionResult(ok=False, ...)`) carries one plain-language cause in `summary` and a
+remedy: a `next_commands` entry the operator can run, or a `details` line of the form
+"nothing to run here — <what is wrong on the box and who fixes it>".
+
+`test_no_bare_refusal_on_the_update_paths` is a whole-module negative invariant ("no refusal
+constructor in these scopes is bare") that no driven path can prove for every constructor, so it
+reads the scopes' source (tests/README.md rule 1, exception). Its behavioural twins drive two
+refusals measured on a real box through the ordinary fakes: the self-update identity refusal and
+the update blocked by a leftover `.prev` folder.
+
+Not in the guard, named: the admission refusal (`ActionResult(False, <AdmissionRefused>.reason)`)
+is the shared admission gate's text, worded once in `_task_admission_blocked` for every operation;
+`ControllerService.install` (services.py) is outside this batch's files.
+"""
+
+from __future__ import annotations
+
+import ast
+from pathlib import Path
+
+import pytest
+
+from repo_paths import REPO
+
+pytestmark = pytest.mark.contract
+
+NOTHING_TO_RUN = "nothing to run here — "
+
+# module -> the functions on the install / update / self-update / build / binary paths (None = all)
+SCOPES = {
+    "lhpc/core/service_binary_ops.py": None,
+    "lhpc/core/service_selfupdate.py": None,
+    "lhpc/core/service_lifecycle_ops.py": {"build"},
+    "lhpc/core/service_maintenance.py": {"update", "graywolf_upstream_update",
+                                         "_graywolf_upstream_update_locked"},
+}
+
+
+def _arg(call: ast.Call, pos: int, name: str):
+    if len(call.args) > pos:
+        return call.args[pos]
+    return next((k.value for k in call.keywords if k.arg == name), None)
+
+
+def _refusals(tree: ast.AST, functions):
+    """(function, call) for every `ActionResult(False, ...)` / `ActionResult(ok=False, ...)`."""
+    for fn in ast.walk(tree):
+        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
+            continue
+        if functions is not None and fn.name not in functions:
+            continue
+        for call in ast.walk(fn):
+            if not (isinstance(call, ast.Call)
+                    and getattr(call.func, "id", getattr(call.func, "attr", "")) == "ActionResult"):
+                continue
+            ok = _arg(call, 0, "ok")
+            if isinstance(ok, ast.Constant) and ok.value is False:
+                yield fn.name, call
+
+
+def _has_remedy(call: ast.Call) -> bool:
+    commands = _arg(call, 3, "next_commands")
+    if commands is not None and not (isinstance(commands, ast.List) and not commands.elts):
+        return True
+    details = _arg(call, 2, "details")
+    return details is not None and any(
+        isinstance(n, ast.Constant) and isinstance(n.value, str) and NOTHING_TO_RUN in n.value
+        for n in ast.walk(details))
+
+
+def _is_admission_refusal(call: ast.Call) -> bool:
+    summary = _arg(call, 1, "summary")
+    return isinstance(summary, ast.Attribute) and summary.attr == "reason"
+
+
+def test_no_bare_refusal_on_the_update_paths():
+    bare, seen = [], set()
+    for rel, functions in SCOPES.items():
+        tree = ast.parse((REPO / rel).read_text(encoding="utf-8"))
+        for fn, call in _refusals(tree, functions):
+            seen.add((rel, fn))
+            if not _is_admission_refusal(call) and not _has_remedy(call):
+                bare.append(f"{rel}:{call.lineno} ({fn})")
+    # A renamed function must not empty its scope silently.
+    for rel, functions in SCOPES.items():
+        for fn in functions or ():
+            assert (rel, fn) in seen, f"{rel}: no refusal found in {fn}() — scope out of date"
+    assert not bare, "refusal without a remedy (next_commands, or '" + NOTHING_TO_RUN + "…'):\n" \
+        + "\n".join(bare)
+
+
+# ---- behavioural twins ---------------------------------------------------------------------
+
+
+def test_unsafe_identity_refusal_names_the_command(tmp_path, monkeypatch):
+    """`lhpc self-update --apply` on a checkout a developer left in detached HEAD (measured on a
+    Pi Zero 2 W): the refusal names the command that puts it back on its branch."""
+    from lhpc.core.paths import Paths
+    from lhpc.core.probes.backends import FakeSystem
+    from lhpc.core.services import ControllerService
+
+    svc = ControllerService(system=FakeSystem(cmdlines_data={}).system,
+                            paths=Paths(runtime_root=tmp_path))
+    # Stubbed collaborators: the live identity probe (it needs a real self-hosted checkout) and
+    # the job/auto-install/HMAC scan; the refusal under test is the identity gate behind them.
+    monkeypatch.setattr(ControllerService, "controller_identity_live",
+                        lambda self: {"status": "unsafe", "ok": False,
+                                      "reason": "checkout is in detached HEAD"})
+    monkeypatch.setattr(ControllerService, "_self_update_blockers", lambda self: None)
+    res = svc.self_update_apply()
+    assert not res.ok and res.data.get("identity_unsafe")
+    assert "detached HEAD" in res.summary
+    assert res.next_commands[:2] == [
+        f"git -C {tmp_path}/src/loraham-pi-control switch main", "lhpc self-update --apply"]
+
+
+class _GitRunner:
+    """A clone that succeeds, a clean tree, and the identity the ownership record holds."""
+
+    def run(self, argv, timeout=None, *a, **k):
+        from lhpc.core.probes.backends import CommandResult
+        if argv[:2] == ["git", "clone"]:
+            (Path(argv[-1]) / ".git").mkdir(parents=True, exist_ok=True)
+            return CommandResult(0, "", "")
+        if "status" in argv or "ls-files" in argv:
+            return CommandResult(0, "", "")
+        if "config" in argv:
+            return CommandResult(0, "https://example/repo.git\n", "")
+        return CommandResult(0, "abc123\n", "")
+
+
+def test_leftover_prev_refusal_names_the_folder_and_the_command(tmp_path):
+    """An update blocked by a `.prev` folder an interrupted update left behind (measured on a
+    Pi Zero 2 W): the refusal names that folder and the command to run after moving it."""
+    import time
+
+    from lhpc.core import source_registry
+    from lhpc.core.config import Config
+    from lhpc.core.install import Installer
+    from lhpc.core.model import Component, ComponentKind, SourceSpec, Stack
+    from lhpc.core.paths import Paths
+    from lhpc.core.probes.backends import FakeSystem
+
+    rt = tmp_path / "rt"
+    (rt / "src" / "repo").mkdir(parents=True)
+    (rt / "src" / ".repo.prev").mkdir()                    # left over by an interrupted update
+    comp = Component(id="c", name="c", kind=ComponentKind.SERVICE,
+                     source=SourceSpec(path="src/repo", local_dir="repo",
+                                       remote="https://example/repo.git", branch="main"))
+    paths = Paths(runtime_root=rt)
+    assert source_registry.write_record(paths, source_registry.RegistryRecord(
+        "src/repo", "https://example/repo.git", "dev", "abc123", time.time(), "", ("c",)))
+    system = FakeSystem().system
+    system.runner = _GitRunner()
+    inst = Installer(paths, (Stack(id="s", name="s", main="c", components=(comp,)),),
+                     Config(values={"install": {"adopt_search_root": str(rt / "nolocal")}}), system)
+    action = inst.adopt_source(comp, force=True, source="dev")
+    assert action.status == "failed"
+    assert "src/.repo.prev" in action.detail and "lhpc update c --yes" in action.detail
+    assert (rt / "src" / ".repo.prev").is_dir()            # the refusal itself removes nothing
```
