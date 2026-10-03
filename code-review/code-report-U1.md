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
