# Gate 1 — code review request, F43, Correction 6

**Request.** Judge ONLY one commit, d53c2b4 "F43: calibrate.sh — the fixed calibration
workload". It is the earlier commit 7474eb8 with one fix amended in; the rest of that commit is
the version already judged, and the diff below is exactly the amendment (both commits have the
same parent). Judge four things:

1. **The work dir is on disk.** `calibrate.sh` no longer uses `mktemp` in `TMPDIR`. Its default
   work dir is `<runtime root>/state/lhpc-calib` when the script is the checkout inside an LHPC
   install, else `$HOME/.cache/lhpc-calib`; `--work-dir DIR` overrides it. Check that `TMPDIR` is
   never consulted, that the script creates and removes the dir, and that it can never remove a
   directory it did not create.
2. **The refusals.** Before any timed part, the script refuses a work dir on tmpfs or ramfs and a
   work dir with less free space than the io size + 64 MB, each with one line on stderr and
   exit 1. Check the order (refusal before writing), the numbers in the message, and the
   cleanup on refusal.
3. **`--io-mb`.** The io size is now a knob (default 256, unchanged) and is part of the
   `workload` hash, so a calibration taken at another size never matches the real workload.
   Check both.
4. **The tests and the docs.** Check that `testlab/tests/unit/test_calibrate.py` proves 1–3 and
   would have caught the defect, and that the two doc lines name the work dir correctly.

The eleven other commits of the branch keep their patch-ids and are not under review.

Answer in the form `| commit | verdict (OK / FINDING) | what |`, then give one final line: GREEN /
GREEN WITH NOTES / RED. A finding names the line in the diff and what goes wrong.

This file is your whole input: you have no repository access; use no connector, tool or web lookup.

## Why the commit was amended

The first real row A on a Pi Zero 2 W ran `bash testlab/slowbuild/calibrate.sh`. Line 16 of the
old script created its work dir with `mktemp -d "${TMPDIR:-/tmp}/lhpc-calib.XXXXXX"`. On the Zero
`/tmp` is a 208 MB tmpfs, and the io part writes 256 MB, so it died after 105 s with
`OSError: [Errno 28] No space left on device`. Even where it fits, io on tmpfs times RAM, not the
SD card the calibration is meant to measure.

## Context (unchanged code the amendment relies on)

- **The runtime root** (`docs/architecture.md`): everything lives under one root
  (`$HOME/loraham-pi-control`, override `LHPC_RUNTIME_ROOT`) with `src/`, `config/`, `state/`,
  `logs/` and `venv/lhpc`. In the self-hosted deployment (the Zero's), LHPC's checkout is
  `<root>/src/loraham-pi-control`. In a dev checkout the root is elsewhere.
- **Row C** (CI job `slow-build`, unchanged): the lane runs
  `subprocess.run(["bash", str(REPO / "testlab" / "slowbuild" / "calibrate.sh")], ...)` with no
  arguments, from a copy of the repository at `/tmp/repo` in a container started with
  `--user root` and `-v lhpc_slow_tmp:/tmp` (a Docker volume). That copy is not inside a runtime
  root, so the default work dir there is `$HOME/.cache/lhpc-calib` (root's home), on the
  container's overlay file system. Before the amendment it was under `/tmp`, the Docker volume.
- **Recorded calibrations:** `tests/data/slow-target-builds.toml` holds no `[[calibration]]`
  entry yet (bootstrap), so the changed `workload` hash orphans nothing.
- **The testlab unit job** runs `python -m pytest testlab/tests/unit`; the new test module is
  picked up there.

## The amendment (7474eb8 → d53c2b4)

```diff
diff --git a/docs/maintenance.md b/docs/maintenance.md
index fd6b52a..2209adb 100644
--- a/docs/maintenance.md
+++ b/docs/maintenance.md
@@ -151,7 +151,10 @@ repositories.
   when row C measures the new pin above limit/4 (= 50 % of the budget) or that (component, op) has
   no Zero entry at all. A minor runs rows 6, 7, 8, the self-update and `calibrate.sh` on the Zero 2 W
   before the tag and commits the numbers ([test matrix](test-matrix.md#slow-target-baseline)),
-  which refreshes the baselines and the calibration. Bootstrap: while
+  which refreshes the baselines and the calibration. `calibrate.sh` works in
+  `$HOME/loraham-pi-control/state/lhpc-calib` on the SD card (the runtime root's state dir; it
+  needs 256 MB + 64 MB free there), never in `/tmp`, a ~200 MB tmpfs on the Zero: it refuses a
+  tmpfs work dir and one with too little free space. Bootstrap: while
   `tests/data/slow-target-builds.toml` holds no measured entry at all (before the first row A),
   `test_coverage`, `test_slow_build_calibrated` and `test_slow_build_budget` SKIP with a
   "bootstrap: no row A yet" reason naming every unmeasured operation, and the `slow-build` gate
diff --git a/docs/test-matrix.md b/docs/test-matrix.md
index 19d382f..63acae1 100644
--- a/docs/test-matrix.md
+++ b/docs/test-matrix.md
@@ -108,7 +108,7 @@ that runs it on the Zero.
 | `selfupdate-helper` | a one-click update from the previous release: `systemctl --user show lhpc-selfupdate.service -p ExecMainStartTimestamp -p ExecMainExitTimestamp`, the difference |
 | `selfupdate-pip` | the `[selfupdate] pip sync <n> s` line of that run in `logs/lhpc-selfupdate.log`; which release has it: see [maintenance](maintenance.md#branches-and-releases), the slow-target build row |
 | `deb-fetch` | `t bash lhpc/data/scripts/graywolf-fetch.sh /tmp/gw <latest> --from-upstream` |
-| calibration | `bash testlab/slowbuild/calibrate.sh` prints `cpu= io= mem= workload=`: one `[[calibration]]` entry |
+| calibration | `bash testlab/slowbuild/calibrate.sh` (from the installed checkout, so its work dir is `state/lhpc-calib` on the SD card) prints `cpu= io= mem= workload=`: one `[[calibration]]` entry |
 
 `key` is `pin:<the component's manifest pin_commit>` (the `cli-venv` entry: meshtastic's),
 `deb:<version fetched>`, or for both self-update entries the output of
diff --git a/testlab/slowbuild/calibrate.sh b/testlab/slowbuild/calibrate.sh
index e8908f5..ad8f0a4 100755
--- a/testlab/slowbuild/calibrate.sh
+++ b/testlab/slowbuild/calibrate.sh
@@ -4,20 +4,72 @@
 # the throttled CI container (row C); the container must be at least as slow on every part.
 #
 #   cpu  build the vendored C sources in calib-src/ with make -j$(nproc)
-#   io   write and fsync 256 MB as 4 KiB files, then read them back
+#   io   write and fsync 256 MB as 4 KiB files, then read them back, on disk (below)
 #   mem  touch 700 MB of anonymous pages twice (more than a Zero has: zram/swap must work)
 #
 # Prints one line:  cpu=<s> io=<s> mem=<s> workload=sha256:<hex>
-# `workload` hashes this script and every file under calib-src/, so a changed workload never
-# matches a calibration recorded for another one.
+# `workload` hashes this script, every file under calib-src/ and the io size, so a changed
+# workload never matches a calibration recorded for another one.
+#
+# The work dir is on the disk the lane calibrates, never in TMPDIR: on a Zero 2 W /tmp is a
+# ~200 MB tmpfs, too small for the io part, and io on tmpfs measures RAM. Default: the runtime
+# root's state/lhpc-calib when this checkout is the one inside an LHPC install
+# (<root>/src/loraham-pi-control), else $HOME/.cache/lhpc-calib. The script creates it, refuses
+# it on tmpfs/ramfs or with too little free space, and removes it at exit; an existing one is
+# never reused.
+#
+#   --work-dir DIR   use DIR instead (must not exist yet)
+#   --io-mb N        io size in MB (default 256; any other size is a different workload)
 set -euo pipefail
 
 here=$(cd "$(dirname "$0")" && pwd)
-work=$(mktemp -d "${TMPDIR:-/tmp}/lhpc-calib.XXXXXX")
+io_mb=256
+margin_mb=64
+work=
+while (($#)); do
+  case $1 in
+    --work-dir) work=${2:?--work-dir needs a directory}; shift 2 ;;
+    --io-mb) io_mb=${2:?--io-mb needs a number}; shift 2 ;;
+    *) echo "calibrate.sh: unknown argument: $1" >&2; exit 2 ;;
+  esac
+done
+if [[ ! $io_mb =~ ^[1-9][0-9]*$ ]]; then
+  echo "calibrate.sh: --io-mb must be a positive integer" >&2
+  exit 2
+fi
+
+if [[ -z $work ]]; then
+  root=$(cd "$here/../../../.." && pwd)
+  if [[ $(cd "$here/../.." && pwd) == "$root/src/loraham-pi-control" && -d $root/state ]]; then
+    work=$root/state/lhpc-calib
+  else
+    work=${HOME:?HOME is not set}/.cache/lhpc-calib
+  fi
+fi
+if [[ -e $work ]]; then
+  echo "calibrate.sh: refused: work dir $work already exists (an interrupted run?): remove it" >&2
+  exit 1
+fi
+mkdir -p "$(dirname "$work")"
+mkdir "$work"
 trap 'rm -rf "$work"' EXIT
 
-workload=$(cd "$here" && find calib-src -type f \( -name '*.c' -o -name Makefile \) -print0 \
-             | LC_ALL=C sort -z | xargs -0 sha256sum calibrate.sh | sha256sum | cut -d' ' -f1)
+fstype=$(stat -f -c %T "$work")
+if [[ $fstype == tmpfs || $fstype == ramfs ]]; then
+  echo "calibrate.sh: refused: work dir $work is on $fstype;" \
+       "the io part must time the disk (--work-dir)" >&2
+  exit 1
+fi
+free_mb=$(( $(df -Pk "$work" | awk 'NR == 2 { print $4 }') / 1024 ))
+if (( free_mb < io_mb + margin_mb )); then
+  echo "calibrate.sh: refused: work dir $work has $free_mb MB free," \
+       "the io part needs $io_mb MB + $margin_mb MB margin" >&2
+  exit 1
+fi
+
+workload=$( { cd "$here" && find calib-src -type f \( -name '*.c' -o -name Makefile \) -print0 \
+                | LC_ALL=C sort -z | xargs -0 sha256sum calibrate.sh; echo "io_mb=$io_mb"; } \
+            | sha256sum | cut -d' ' -f1)
 
 elapsed() { awk -v a="$1" -v b="$2" 'BEGIN { printf "%.1f", b - a }'; }
 
@@ -28,11 +80,11 @@ cpu=$(elapsed "$t0" "$EPOCHREALTIME")
 
 mkdir "$work/io"
 t0=$EPOCHREALTIME
-python3 - "$work/io" <<'PY'
+python3 - "$work/io" "$io_mb" <<'PY'
 import os
 import sys
 d, block = sys.argv[1], os.urandom(4096)
-n = 256 * 2**20 // 4096
+n = int(sys.argv[2]) * 2**20 // 4096
 for i in range(n):
     fd = os.open(os.path.join(d, f"{i:05d}"), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
     os.write(fd, block)
diff --git a/testlab/tests/unit/test_calibrate.py b/testlab/tests/unit/test_calibrate.py
new file mode 100644
index 0000000..1036d10
--- /dev/null
+++ b/testlab/tests/unit/test_calibrate.py
@@ -0,0 +1,96 @@
+"""calibrate.sh times the SD card, not RAM: its io part refuses a tmpfs work dir and one too small.
+
+The first row A on a Zero 2 W wrote the io part into `mktemp -d /tmp/...`: /tmp there is a 208 MB
+tmpfs, and 256 MB died with ENOSPC after 105 s. Even where it fits, io on tmpfs measures memory.
+"""
+from __future__ import annotations
+
+import os
+import re
+import shutil
+import subprocess
+from pathlib import Path
+
+import pytest
+
+REPO = next(p for p in Path(__file__).resolve().parents if (p / "lhpc" / "version.py").is_file())
+SCRIPT = REPO / "testlab" / "slowbuild" / "calibrate.sh"
+
+
+def _fstype(path: Path) -> str:
+    return subprocess.run(["stat", "-f", "-c", "%T", str(path)], capture_output=True, text=True,
+                          check=True).stdout.strip()
+
+
+def _run(*args: str) -> subprocess.CompletedProcess:
+    return subprocess.run(["bash", str(SCRIPT), *args], capture_output=True, text=True,
+                          timeout=600, check=False)
+
+
+def test_a_tmpfs_work_dir_is_refused():
+    shm = Path("/dev/shm")
+    if not shm.is_dir() or _fstype(shm) != "tmpfs":
+        pytest.skip("no tmpfs at /dev/shm on this box")
+    work = shm / "lhpc-calib-test"
+    r = _run("--work-dir", str(work), "--io-mb", "1")
+    assert r.returncode == 1, r.stdout + r.stderr
+    assert re.search(r"refused: .*lhpc-calib-test is on tmpfs", r.stderr), r.stderr
+    assert "cpu=" not in r.stdout
+    assert not work.exists(), "the refused work dir must be removed"
+
+
+def test_too_little_free_space_is_refused_with_the_numbers(tmp_path):
+    if _fstype(tmp_path) in ("tmpfs", "ramfs"):
+        pytest.skip("pytest's tmp dir is on tmpfs on this box")
+    work = tmp_path / "calib"
+    r = _run("--work-dir", str(work), "--io-mb", str(2**30))
+    assert r.returncode == 1, r.stdout + r.stderr
+    assert re.search(r"refused: .* MB free, the io part needs \d+ MB \+ 64 MB", r.stderr), r.stderr
+    assert not work.exists()
+
+
+def test_a_disk_work_dir_runs_and_is_removed_and_the_io_size_is_hashed(tmp_path):
+    """Runs on disk. A calibration recorded with a test-sized io part never matches the real
+    workload: the io size is part of the workload hash."""
+    if _fstype(tmp_path) in ("tmpfs", "ramfs"):
+        pytest.skip("pytest's tmp dir is on tmpfs on this box")
+    hashes = set()
+    for mb in ("1", "2"):
+        work = tmp_path / f"calib{mb}"
+        r = _run("--work-dir", str(work), "--io-mb", mb)
+        assert r.returncode == 0, r.stdout + r.stderr
+        m = re.fullmatch(r"cpu=[\d.]+ io=[\d.]+ mem=[\d.]+ workload=sha256:([0-9a-f]{64})\n",
+                         r.stdout)
+        assert m, r.stdout
+        hashes.add(m.group(1))
+        assert not work.exists()
+    assert len(hashes) == 2
+
+
+def test_an_existing_work_dir_is_never_reused_or_removed(tmp_path):
+    work = tmp_path / "calib"
+    work.mkdir()
+    (work / "keep").write_text("x")
+    r = _run("--work-dir", str(work), "--io-mb", "1")
+    assert r.returncode == 1 and "already exists" in r.stderr, r.stderr
+    assert (work / "keep").is_file()
+
+
+@pytest.mark.parametrize("installed", [True, False])
+def test_the_default_work_dir_is_on_disk_and_never_tmpdir(tmp_path, installed):
+    """Inside an install: <root>/state/lhpc-calib; elsewhere $HOME/.cache/lhpc-calib. TMPDIR is
+    not consulted. An oversized io part makes it refuse right after naming the dir."""
+    if _fstype(tmp_path) in ("tmpfs", "ramfs"):
+        pytest.skip("pytest's tmp dir is on tmpfs on this box")
+    root, home = tmp_path / "root", tmp_path / "home"
+    checkout = root / "src" / "loraham-pi-control" if installed else tmp_path / "dev-checkout"
+    (checkout / "testlab").mkdir(parents=True)
+    shutil.copytree(SCRIPT.parent, checkout / "testlab" / "slowbuild")
+    (root / "state").mkdir(parents=True)
+    home.mkdir()
+    r = subprocess.run(["bash", str(checkout / "testlab" / "slowbuild" / "calibrate.sh"),
+                        "--io-mb", str(2**30)], capture_output=True, text=True, timeout=60,
+                       check=False, env={**os.environ, "HOME": str(home), "TMPDIR": "/dev/shm"})
+    want = root / "state" / "lhpc-calib" if installed else home / ".cache" / "lhpc-calib"
+    assert r.returncode == 1 and f"work dir {want} has " in r.stderr, r.stderr
+    assert not want.exists()
```

## What the author ran

- **Red before.** `test_a_tmpfs_work_dir_is_refused` and
  `test_an_existing_work_dir_is_never_reused_or_removed` against the old script: 2 failed. The
  old script ignored its arguments, wrote 256 MB under `/tmp` and printed
  `cpu=7.4 io=28.5 mem=4.0 workload=…` with rc 0 where a refusal was expected.
- **After the amendment:** `testlab/tests/unit/test_calibrate.py` 6 passed (≈22 s), on the head
  and on d53c2b4 itself (worktree).
- `tests/repo` + `testlab/tests/unit/test_slow_build_lane.py` + the new module: 443 passed,
  5 skipped, 1 failed. The failure is
  `test_version_consistent.py::test_changelog_leads_with_the_current_version` (`## 0.11.12`
  heading against version 0.11.11), known since Correction 4; it fails the same way without this
  amendment.
- `ruff check lhpc testlab`: all checks passed. `bash -n` on the script: ok. The repository does
  not use shellcheck, and it is not installed here.
- **Not claimed:** a row A on the Zero with the amended script, and a row C run with it.
