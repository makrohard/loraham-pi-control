# Migrating a deployment to the self-hosted layout (operator runbook)

This is an **operator-run** procedure — LHPC ships no migration tool. It relocates an
existing deployment whose git checkout sits **at** the runtime root (tangled with `config/`,
`logs/`, `state/`, `.venv`) into the [self-hosted layout](deployment.md#self-hosted-deployment-layout-the-deployment-standard):
the runtime root becomes a plain container with LHPC's checkout under `src/loraham-pi-control`
and the venv at `venv/lhpc`. It is **reversible by directory rename** in the default mode.
Do it in a maintenance window; nothing here runs radios.

> This document describes the deployment relocation only. Your **dev** checkout (e.g.
> `~/src/loraham-pi-control`) is untouched — the in-root deployment checkout is a fresh
> clone.

## 0 — Freeze one exact SHA (never a moving branch)

The deployment input is an **immutable commit**, not `origin/main` (which moves). Fetch and
record it:

```bash
git -C <deployment-checkout> fetch origin main
EXPECTED_SHA=$(git -C <deployment-checkout> rev-parse origin/main)
echo "$EXPECTED_SHA"        # write this down; every step below asserts it
```

Capture the starting state: unit + service status, `git status --porcelain`/branch/remotes/
HEAD, running managed stacks, and any active build/test/bulk job, reservation, source
journal, or self-update journal. Do not proceed from a **dirty** checkout unless its diff is
captured and accepted.

## 1 — Quiesce FIRST (before any copy)

Stop the web service **and every managed unit/process that can write under the runtime
root**, and verify none remain (no jobs, no bulk run, no unresolved journal, no relevant
PIDs). Hold this quiescent state through copy → verification → clone → cutover, so the trees
cannot change under the migration. Do **not** attempt a live copy + delta re-sync: the
no-`--delete` copy rule below means deletions would not mirror safely.

## 2 — Stage a copy (same filesystem)

Stage into `~/loraham-pi-control.stage-<ts>` on the **same filesystem**. Migrate the runtime
data — at least `config/ logs/ state/` (always) and `src/`. Two modes:

- **Full-rollback (default):** copy everything including `src/`. Rollback stays a pure
  rename because the old root remains a complete, untouched image. Needs ~2× `src/` free
  space.
- **Constrained-disk:** `mv` (rename) `src/` after a verified **external** backup. Rollback
  here is **not** a pure rename — it restores the backup / re-adopts stacks.

Copy with a **metadata- and symlink-preserving** transfer — never dereference adopt-by-link
sources, never delete on the destination:

```bash
BACKUP=~/loraham-pi-control-migration-backups/$(date +%Y%m%d-%H%M%S)   # OUTSIDE both roots
mkdir -p "$BACKUP"
for sub in config logs state src; do
  rsync -aHAX --links ~/loraham-pi-control/$sub/  ~/loraham-pi-control.stage-<ts>/$sub/
  # verify EACH subtree immediately, BEFORE the new checkout/venv exist:
  rsync -aHAXn --itemize-changes ~/loraham-pi-control/$sub/  ~/loraham-pi-control.stage-<ts>/$sub/
done   # any itemized diff => abort
```

## 3 — Place the frozen checkout (deliberately, at the exact SHA)

Abort if `stage/src/loraham-pi-control` already exists. Then construct the checkout
deliberately — do not "clone and hope":

```bash
STAGE=~/loraham-pi-control.stage-<ts>
git clone --no-checkout <canonical-remote> "$STAGE/src/loraham-pi-control"
git -C "$STAGE/src/loraham-pi-control" fetch --no-tags origin main
test "$(git -C "$STAGE/src/loraham-pi-control" rev-parse origin/main)" = "$EXPECTED_SHA"   # abort if main moved
git -C "$STAGE/src/loraham-pi-control" checkout -B main "$EXPECTED_SHA"
git -C "$STAGE/src/loraham-pi-control" branch --set-upstream-to=origin/main main
```

Verify: clean worktree, `HEAD == EXPECTED_SHA`, branch `main`, tracking `origin/main`,
canonical `origin`, and **no** top-level `.git` at the stage root.

**Preflight** with a *throwaway* venv (never the production one — a venv's entry-point
shebangs bake in the absolute interpreter path, so a stage-built venv breaks on rename):

```bash
python3 -m venv "$STAGE/.preflight-venv"
"$STAGE/.preflight-venv/bin/pip" install -e "$STAGE/src/loraham-pi-control"
LHPC_RUNTIME_ROOT="$STAGE" "$STAGE/.preflight-venv/bin/python" -c \
 'import lhpc,pathlib;from lhpc.core import selfupdate;from lhpc.core.paths import resolve_paths;
  print(pathlib.Path(lhpc.__file__).resolve());print(selfupdate.repo_root());print(resolve_paths().runtime_root)'
# expect: package under $STAGE/src/loraham-pi-control/lhpc; repo_root == that; runtime_root == $STAGE
rm -rf "$STAGE/.preflight-venv"
```

## 4 — Cut over + build the PRODUCTION venv at the final path

Still quiesced, re-confirm nothing is writing under the root, then rename:

```bash
mv ~/loraham-pi-control  ~/loraham-pi-control.rollback-<ts>     # keep as rollback evidence
mv "$STAGE"              ~/loraham-pi-control
python3 -m venv ~/loraham-pi-control/venv/lhpc
~/loraham-pi-control/venv/lhpc/bin/pip install -e ~/loraham-pi-control/src/loraham-pi-control
# (install waitress too, per deployment.md)
```

Assert the security boundary before starting: the runtime root, `src/`, and the checkout are
owned by you and **not** group/other-writable:

```bash
chmod 700 ~/loraham-pi-control ~/loraham-pi-control/src ~/loraham-pi-control/src/loraham-pi-control
```

Re-run the identity smoke test against the **final** path, install the updated
`deploy/lhpc-web.service` (it sets `LHPC_RUNTIME_ROOT` + `WorkingDirectory`), `daemon-reload`,
start, and confirm `lhpc status` shows a healthy `[controller]` row (identity ok). Keep the
`~/loraham-pi-control.rollback-<ts>` tree through a burn-in period.

## 5 — Rollback

- **Full-rollback mode:** stop the new service, rename the failed new root aside, rename
  `~/loraham-pi-control.rollback-<ts>` back to `~/loraham-pi-control`, restore the prior
  unit. Pure rename.
- **Constrained-disk mode:** restore `src/` from the external backup (or re-adopt stacks),
  then restore `config/ logs/ state/` and the unit. **Not** a pure rename.

Never auto-merge `state/`/`logs/` between the failed and restored roots.
