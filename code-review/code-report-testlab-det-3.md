# Code report: testlab-det-3 v4 (packet P3: the lanes clone from mirrors; acquisition has the transfer backoff)

Branch `cons/testlab-det-3-r1` on `origin/dev` = `d77e34f4` (P4b landed). One code commit, 6e7903e0. Plan v4 §1 (class A, transfer backoff) and §2 P3; the maintainer's decisions c1 (transfers retried long, never an assertion) and c3 (release-verify installs from the mirror).

## v4: what changed since v3 (917be744), and why

1. **A real defect: v3's lanes never cloned from the mirrors** (second read of P5; it hits P3). The lane exported `GIT_CONFIG_GLOBAL`, but the product clones through `RealCommandRunner`, whose children get `{**_FIXED_ENV, **env}`. `_FIXED_ENV` is PATH/LANG/LC_ALL plus a short whitelist that includes `HOME` and `XDG_CONFIG_HOME` and not `GIT_CONFIG_GLOBAL` (lhpc/core/probes/backends.py:188–203, used at :262, :374, :430). So run 38823954555's green lanes cloned from GitHub; the mirror was fetched and proved complete, but not used. Fix, with no product change: the lane copies the config to the lab user's `~/.gitconfig` (`su lhpclab` sets `HOME`), and the runner forwards `HOME`. `verify` now uses the same mechanism (a temporary `HOME`).
2. **The proof goes through the real runner.** New `mirror-sources.py resolved <dir>`: for every remote, `git ls-remote --get-url <remote>` run through `RealCommandRunner`. It prints the URL after `insteadOf` without touching the network, and exits 1 on any remote that does not resolve to its mirror. Each lane runs it after installing lhpc and before its tests. The test `test_only_a_gitconfig_in_home_redirects_a_clone_through_the_products_runner` clones a NONEXISTENT `file:///nonexistent/upstream/up.git` through `RealCommandRunner` in a fresh interpreter (so `_FIXED_ENV` is built from that test's `HOME`). With the config in `HOME` the clone succeeds and `resolved` prints `ok`. With `GIT_CONFIG_GLOBAL` (v3's mechanism) the clone fails and `resolved` prints `NOT REDIRECTED … (resolves to <remote>)`, exit 1. No network either way.
3. **Exclusions are keyed by component, not by remote URL** (round 12 point 1). In v3, a future component reusing an excluded URL (say the meshtastic firmware) would have been dropped from the list and cloned live. `NOT_MIRRORED` now names components (`radiolib`, `meshtastic`, `meshcom-firmware`, `meshcom-bridge`). A remote is excluded only through those components, and a stale component id is an error. Twin test: the real manifest plus a new component on the meshtastic firmware remote lists that remote.
4. **Stale text** (round 12 point 2): this report is rewritten for v4; `LANE_COMPONENTS` and the "14-component list" (v2) are gone from code and report.
5. **Nothing landed relied on the dropped env.**
   - P2's `PYTHONFAULTHANDLER` reaches stack programs through `lifecycle`'s spawn (`{**os.environ, **env}`, lhpc/core/lifecycle.py:1485, wrapped by testlab/lhpc_testlab/spawn.py), not through the runner.
   - The interactive case sets it on its own `_pty_launch`.
   - P3a and P1 pass no env to the runner. P1's slow-build Graywolf fetch runs `graywolf-fetch.sh` by direct `subprocess.run`.
   - The lab's fake sources use `GIT_CONFIG_GLOBAL=/dev/null` in their OWN direct git calls (testlab/lhpc_testlab/ops.py:656), unaffected.

Red-before against v3 (the new test file on a `git archive` of 917be744): 4 failed, 4 passed.
- Both runner cases fail on the missing `resolved` (`(2, '')`).
- The twin fails through the behaviour: the reused firmware remote is absent from the list.
- `a remote added … mirrored by default` fails because its fixture now names excluded COMPONENTS (v3 keyed URLs). That is a fixture shape, not a behaviour.

## Commit

| commit | files | tests | red-before |
|---|---|---|---|
| 6e7903e0 | .github/scripts/mirror-sources.py (new), .github/scripts/retry.sh (`backoff`), .github/workflows/testlab.yml (testlab + release-verify jobs), docs/testlab.md, CHANGELOG.md, tests/repo/test_mirror_sources.py (new), tests/repo/test_workflow_shell.py | tests/repo: 9 mirror cases (2 through the runner) + 3 backoff cases; the existing verdict guard also reads `backoff` | new tooling: negative cases; for v4's fixes, red against v3 as above |

## HOUSE RULES KEPT (dev's tools/check_houserules.py on origin/dev..HEAD)

```
HOUSE RULES KEPT  origin/dev..HEAD
changelog    +1 bullets (limit 2, each ≤ 3 lines; longest 2), top section 22 lines   OK
docs         1 files, +17 lines; fact "What a CI lane may fetch" -> docs/testlab.md; fact "mirror-sources.py" -> docs/testlab.md   OK
tests        10 new tests; layers tests/repo=repo; red-before 1/1   OK
patterns     getsource 0 assert-True 0 source-read 0 sleep 0 startswith 2 network 0   WARN
markers      0 without slow   OK
attribution  authors makrohard; trailers 0; words 0   OK
modules      no family file touched; family net +0 lines, +0 public   OK
VERDICT      PASS  (hard 0, warn 1)
```

The WARN, answered: **`startswith 2`**, both in test_workflow_shell.py, are list FILTERS inside the assert (`[… for ln in … if ln.startswith("slept ")]`); the assertion compares the whole list exactly (`== ["0", "60", "120", "300", "600", "600"]`).

## What the commit does

- **The mirror.**
  - `mirror-sources.py list` derives the remotes from the manifest: all, minus those of the `NOT_MIRRORED` components, each with its reason. Today that is 14 remotes, covering the 14 green run 37426359349 cloned.
  - The acquisition step clones each `--mirror` under `backoff`, and each attempt first removes a partial directory.
  - `config` writes one `insteadOf` per remote plus `safe.directory = *`: the mirrors are mounted read-only and owned by the runner's user.
  - The lane copies it to `~/.gitconfig` and runs `resolved`. `remote.origin.url` stays the https URL, because `insteadOf` rewrites at use.
- **The completeness proof.** `verify` runs in `docker run --network none` and clones each listed remote through the config, exiting 1 on any failure.
- **The backoff.**
  - `backoff` sits beside `retry` in retry.sh: at once, then after 1, 2, 5 and 10 min, then every 10 min until 30 min. Each wait is logged; the last status is returned.
  - It wraps `docker build`, the lab's pip installs and the Chromium download in the two jobs.
  - The guard test refuses `backoff pytest|…` as it refuses `retry N …`.
  - A deterministic failure there turns red only after the waits (about 28 min). That is the accepted cost of c1, stated in docs/testlab.md.
- **Xvfb.** The testlab job waits up to 60 × 0.5 s for `/tmp/.X11-unix/X99`.

## The 6-point block

1. **Contracts.**
   - `list <manifest>`: remotes, one per line; exit 1 naming a stale excluded component.
   - `name <remote>`: `host_owner_repo.git`.
   - `config <remotes-file> <dir>`: the exact git config text (test-pinned).
   - `verify <dir>`: `ok` / `NOT MIRRORED` per remote, exit 1 on any.
   - `resolved <dir>`: `ok` / `NOT REDIRECTED  <remote>  (resolves to <url>)` per remote, exit 1 on any. It imports lhpc; the rest is stdlib.
   - `backoff <argv…>`: 0, or the command's last status.
2. **Invariants.**
   - No assertion or test body is retried.
   - The product's clone path is unchanged (no lhpc/ file changed); the lane proves, through the product's runner, that it resolves to the mirrors.
3. **Failure classes.**
   - A missing mirror fails `verify` with no network.
   - A config that does not reach the runner's git fails `resolved` before the tests.
   - A new component is mirrored by default, also on a reused excluded remote.
   - A stale exclusion is an error.
   - Residual: `resolved` proves the rewrite for every LISTED remote. A lane clone of a remote the manifest does not name (none today) is not covered.
4. **Test rules:** repository layer, no network (an `.invalid` host only `insteadOf` resolves; nonexistent `file://` paths); exact text where the format is the contract; the runner cases in a fresh interpreter.
5. **Directories:**
   - `pytest -q -n 8 tests/repo` → `591 passed, 1 skipped`.
   - `ruff check lhpc testlab` and `ruff check tests --select F,E9` → `All checks passed!`.
6. **Adversarial self-review.**
   - (a) `safe.directory = *` now lives in the lab user's `~/.gitconfig` inside the throwaway lane container (it was in the exported global file before): the same scope, one container.
   - (b) The v3 claim "implied by `insteadOf`" was wrong, and v3's report said "not observed". v4 now observes it on every lane.
   - (c) Lane evidence: v4 has no lane run yet; the batch run on dev, or a branch run on request, shows `resolved`'s `ok` lines in the job log.

## Numbers, measured

| figure | command | output |
|---|---|---|
| base | `git log --oneline -1 origin/dev` | `d77e34f4` |
| diff | `git diff --shortstat origin/dev 6e7903e0` | 7 files changed, 434 insertions(+), 12 deletions(-) |
| remotes | `mirror-sources.py list lhpc/data/manifest.example.toml \| wc -l` | 14 |
| tests | `pytest -q -n 8 tests/repo` | 591 passed, 1 skipped |
| red-before | the new test file on `git archive 917be744` | 4 failed, 4 passed |
| runner whitelist | `sed -n 188,203p lhpc/core/probes/backends.py` | HOME, XDG_RUNTIME_DIR, XDG_CONFIG_HOME, DBUS_SESSION_BUS_ADDRESS, PLATFORMIO_CORE_DIR, IDF_TOOLS_PATH, XDG_CACHE_HOME, PIP_CACHE_DIR, TMPDIR, LHPC_QEMU_MIN_FREE_INODES |
