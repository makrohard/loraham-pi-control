# Gate 1 — code review request, F43, Correction 8

**Request.** Judge ONLY the four commits amended in Correction 8, and within each only the
amendment shown below (each diff is the commit's new version against its old version, restricted
to the files the amendment touched; the rest of each commit is the version already judged). The
other six F43 code commits, the Correction 4–6 report commits and the data commit keep their patch-ids (the
Correction 7 report commit changes by one reworded line of its report, a version example)
and are not under review. Correction 7 was judged RED with three findings; judge, per commit:

1. **8cfd974** (was 5e4a4c6) "the slow-build lane" — finding 1: `_io_problems` must no longer pass
   on ANY tight `io.max` line. `_required_disks()` names the disks that MUST be throttled — the
   container's writable layer (`SLOW_IO_ROOT_DISK`, resolved by the job) and the calibration work
   dir (`CALIB_WORK`, now also passed to `calibrate.sh` as `--work-dir`) — and the check fails
   unless EACH has its own tight line, naming the disk that has none. An unresolvable required
   disk is a problem, so `_record()` records nothing. Check that no path records evidence while a
   required disk is unthrottled or unknown.
2. **24eb667** (was 3e5d3cd) "the slow-build job" — finding 2: `disk_of()` exits non-zero, naming
   the path, when it cannot resolve; each REQUIRED path (the writable layer's storage and
   `DockerRootDir`) must resolve to a block device or the step STOPS before any measurement; swap
   may be skipped with a notice; `SLOW_IO_ROOT_DISK` reaches the lane. Check that the job cannot
   reach the measuring step with an unthrottled required disk.
3. **c4ebbcb** (was 4a9c743) "the release-policy sentence …" — finding 3: `PIP_SYNC_SINCE` is
   0.12.0 in docs/maintenance.md; docs/testlab.md describes the required disks. Is every sentence
   true as written?
4. **c8e886c** (was 4ab52a2) "calibrate.sh" — docs only: the calibration work dir is documented as
   exactly what `calibrate.sh` does (`<runtime root>/state/lhpc-calib` from the installed checkout
   `<runtime root>/src/loraham-pi-control` while `<runtime root>/state` exists, else
   `$HOME/.cache/lhpc-calib`; `--work-dir` overrides). The script is unchanged.

Answer in the form `| commit | verdict (OK / FINDING) | what |`, then give one final line: GREEN /
GREEN WITH NOTES / RED. A finding names the line in the diff and what goes wrong.

This file is your whole input: you have no repository access; use no connector, tool or web lookup.

## Correction 8 — evidence added before this review

**Red-before of every new and changed test.** Method: the final test modules
`testlab/tests/unit/test_slow_build_lane.py` and `tests/repo/test_slow_build_gate.py` run in a
worktree at the Correction 7 head (16c09c1); with the amended code they pass.

| tests | against the Correction 7 code | amended |
|---|---|---|
| lane: `test_each_required_disk_must_be_throttled` (3: only an unrelated disk tight; one of two; one `max`), `test_disk_of_resolves_the_whole_disk_like_the_job` (2), `test_the_box_with_both_required_disks_throttled_is_the_throttled_box`, `test_a_work_dir_on_the_writable_layer_is_the_layer_disk`, `test_an_unresolvable_required_disk_records_nothing` (3: root disk unnamed, malformed, work dir unresolvable — `_record` raises, `EVIDENCE` stays empty, `_write` never runs), `test_the_calibration_work_dir_is_the_one_the_throttle_check_names` | 11 failed | pass |
| lane: the 6 earlier io cases, now with the required disks | 6 failed | pass |
| gate: `test_an_unresolvable_required_disk_stops_the_job_before_any_measurement` (2: a missing path, `/proc`), `test_disk_of_fails_loudly_on_an_unresolvable_path`, `test_a_resolvable_storage_disk_is_throttled_and_named_to_the_lane`, `test_the_throttle_is_named_and_reaches_the_container_and_the_lane` (extended) | 5 failed | pass |

Semantic red-before, the old code itself: the Correction 7 `_io_problems(cg)` on an `io.max` of
only `7:0 riops=1200 wiops=120` returns `[]`; the Correction 7 `disk_of /proc` and
`disk_of /no/such` exit 0 with empty output.

**On a real host (x86 VM, cgroup v1).** The step extracted from `testlab.yml`, run with `bash -e`
and a stub `docker`: storage `/` → `SLOW_IO_FLAGS= --device-write-iops /dev/vda:120
--device-read-iops /dev/vda:1200`, `SLOW_IO_ROOT_DISK=254:0`, rc 0; storage `/proc` → `disk_of:
backing device of /proc could not be resolved (MAJ:MIN 0:22)`, `::error::STOP: backing device of
/proc could not be resolved — the throttle cannot be applied`, rc 1, nothing written to
`$GITHUB_ENV`. The lane's `_disk_of`: `/` → `254:0`, `/proc` → `None`. NOT run: the container step
on a real cgroup v2 — this host has no running Docker daemon and no `io.max` (cgroup v1); the new
check on its `/sys/fs/cgroup` reports `io.max is unreadable`. The maintainer's lane run is the proof
that `SLOW_IO_ROOT_DISK` matches the `io.max` line Docker writes.

**Patch-ids** (`git show <c> | git patch-id --stable`): the four amended commits change (05882c04af50
→ dc1b48fb781d, 80739b76a63b → 52a03cc52c76, b8bae5ba7713 → 7bcf0556845f, 0f8760b50b61 →
82c9ad03beb3), and the Correction 7 report commit c12c357d52e2 → c171e967de57 (one report line: a
version example now reads "the next release or a new major version"); the other
ten commits, the data commit included, keep theirs. `calibrate.sh` is
unchanged, so the data commit's calibration workload hash still matches. The throttle constants
in `.github/workflows/testlab.yml` are unchanged since Correction 7: `SLOW_CPUS` "0.25", `SLOW_MEM`
"416m", `SLOW_SWAP` "1184m", `SLOW_WRITE_IOPS` "120", `SLOW_READ_IOPS` "1200" (numbers table).

**Skips on a host whose tmp dir is tmpfs.** `test_disk_of_resolves_the_whole_disk_like_the_job`
(2 cases) skips when `tmp_path` is on a device-less filesystem (major 0, e.g. tmpfs): it needs a real
MAJ:MIN to resolve. `testlab/tests/unit/test_calibrate.py` skips its 4 cases that need an on-disk
tmp dir there. On a host with tmp on disk, all run.

### Numbers, measured

| figure | command | output |
|---|---|---|
| red-before | `pytest -q testlab/tests/unit/test_slow_build_lane.py tests/repo/test_slow_build_gate.py` in a worktree at 16c09c1 with the two final test modules | 22 failed, 40 passed |
| green after | the same on this branch | 62 passed |
| slow-target modules | `pytest -q testlab/tests/unit/test_slow_build_lane.py testlab/tests/unit/test_calibrate.py testlab/tests/slowbuild tests/repo tests/install/test_slow_target_budget.py` | 504 passed, 16 skipped, 2 failed (`test_coverage`, `test_changelog_leads_with_the_current_version` — both identical on 16c09c1) |
| workflow lint | `pytest -q tests/repo/test_workflow_shell.py` | 10 passed |
| ruff | `ruff check lhpc testlab`; `ruff check tests --select F,E9` | all checks passed (both) |
| amended commit alone | worktree at 8cfd974: lane unit + workflow lint; at 24eb667: lane unit + gate + workflow lint | 59 passed; 71 passed |
| old `_io_problems` | the Correction 7 lane module, `_io_problems(cg)` with `io.max` = `7:0 riops=1200 wiops=120` | `[]` |
| old `disk_of` | the Correction 7 function, `disk_of /proc` | rc 0, no output |
| new step, real disk | the step via `bash -e`, stub `docker` → `/` | rc 0, `SLOW_IO_ROOT_DISK=254:0` |
| new step, no device | the same, stub → `/proc` | rc 1, STOP line, `$GITHUB_ENV` empty |
| delta size | `git diff --shortstat 16c09c1 <head minus the report and data commits> -- . ':!code-review' ':!tests/data'` | 7 files, +320 −41 |
| throttle constants | `grep -E '^\s+SLOW_(CPUS\|MEM\|SWAP\|WRITE_IOPS\|READ_IOPS):' .github/workflows/testlab.yml` at 16c09c1 and at this head | identical: 0.25, 416m, 1184m, 120, 1200 |
| skips, tmp on tmpfs | `pytest -rs testlab/tests/unit/test_slow_build_lane.py testlab/tests/unit/test_calibrate.py` (this host, `/tmp` tmpfs) | 50 passed, 6 skipped (lane 2: device-less tmp; calibrate 4: tmp on tmpfs) |

## Context (unchanged code the amendments rely on)

- `_env_problems()` is checked by `test_slow_build_env` and by every `_record()` call
  (`assert not problems, "not evidence — this is not the throttled box: …"`) before an entry is
  added to `EVIDENCE` and written by `_write()`. It also checks `SLOW_CPUS` against `cpu.max`,
  `memory.max` ≤ 416 MiB and no `LHPC_BUILD_*` override.
- The job: `Resolve the disks to throttle` writes `SLOW_IO_FLAGS` (and now `SLOW_IO_ROOT_DISK`) to
  `$GITHUB_ENV`; the next step `Slow-target build` runs `docker run … $SLOW_IO_FLAGS` with the lab
  image, `-v lhpc_slow_tmp:/tmp`, and the lane as the user `lhpclab` via `su lhpclab -c "…"`. A step
  that exits non-zero stops the job (the default `bash -e`). Docker's `--device-write-iops
  /dev/X:N` sets `wiops=N` on the line of X's whole-disk MAJ:MIN in the container's `io.max`.
- `calibrate.sh`'s default work dir: `<runtime root>/state/lhpc-calib` when the script's checkout
  is `<root>/src/loraham-pi-control` and `<root>/state` exists, else `$HOME/.cache/lhpc-calib`;
  `--work-dir DIR` replaces it; the dir must not exist yet. In the lane the checkout is a copy
  under `/tmp/repo`, so the default is `$HOME/.cache/lhpc-calib` on the image's root filesystem.
  The workload hash covers the script, `calib-src/` and `io_mb`, not the arguments.

## The amendments

### 1. 8cfd974 — the slow-build lane (finding 1)

```diff
diff --git a/testlab/tests/slowbuild/test_slow_build.py b/testlab/tests/slowbuild/test_slow_build.py
index 91c1ebb..8dc53ee 100644
--- a/testlab/tests/slowbuild/test_slow_build.py
+++ b/testlab/tests/slowbuild/test_slow_build.py
@@ -62,6 +62,10 @@ EVIDENCE: dict[tuple[str, str], dict] = {}
 CALIBRATION: list[dict] = []
 FETCHED: dict[str, str] = {}
 INTRODUCING: list[str] = []      # the previous tag, when its helper has no pip sync line
+# calibrate.sh's work dir in the lane (its own default outside an install): named here so the
+# disk throttle is proved on the disk that backs it (`_required_disks`).
+CALIB_WORK = Path.home() / ".cache" / "lhpc-calib"
+SYS_BLOCK = Path("/sys/dev/block")
 
 _SECS = r"(\d+(?:\.\d+)?) s"
 # The timing lines print one decimal (`.1f`): a step faster than 50 ms reads `0.0 s`. That is
@@ -238,7 +242,8 @@ def _helper(lhpc: Path, env: dict) -> tuple[float, str]:
 
 
 def _calibrate() -> str:
-    r = subprocess.run(["bash", str(REPO / "testlab" / "slowbuild" / "calibrate.sh")],
+    r = subprocess.run(["bash", str(REPO / "testlab" / "slowbuild" / "calibrate.sh"),
+                        "--work-dir", str(CALIB_WORK)],
                        capture_output=True, text=True, timeout=HARNESS_S, check=False)
     return _judged("calibrate.sh", r)
 
@@ -265,16 +270,69 @@ def _env_problems() -> list[str]:
         out.append(f"cpu.max {quota} {period}: the CPU throttle to {cpus} CPUs is not in force")
     if mem == "max" or int(mem) > 416 * 2**20:
         out.append(f"memory.max {mem}: the 416 MiB memory cap is not in force")
-    out += _io_problems(cg)
+    required, unresolved = _required_disks()
+    out += unresolved + _io_problems(cg, required)
     if not os.cpu_count():
         out.append("nproc unreadable")
     return out
 
 
-def _io_problems(cg: Path) -> list[str]:
+def _disk_of(path: Path) -> str | None:
+    """The whole disk behind `path` (or its nearest existing parent) as MAJ:MIN, resolved as the
+    job's `disk_of()` does: the filesystem's device, a partition's parent disk. None when it
+    cannot be resolved — an overlay or other device-less filesystem, no sysfs entry."""
+    while not path.exists() and path != path.parent:
+        path = path.parent
+    try:
+        dev = path.stat().st_dev
+        node = (SYS_BLOCK / f"{os.major(dev)}:{os.minor(dev)}").resolve(strict=True)
+        if (node / "partition").exists():
+            node = node.parent
+        mm = (node / "dev").read_text().strip()
+    except OSError:
+        return None
+    return mm if os.major(dev) and re.fullmatch(r"\d+:\d+", mm) else None
+
+
+def _on_writable_layer(path: Path) -> bool:
+    """`path` is on the container's root filesystem, its writable layer."""
+    return path.stat().st_dev == Path("/").stat().st_dev
+
+
+def _required_disks() -> tuple[dict[str, str], list[str]]:
+    """The disks that MUST be throttled, {what: MAJ:MIN}, and why any of them is unknown.
+
+    The container's writable layer has no block device inside the container (overlay): the job
+    resolves the disk behind Docker's storage and names it in SLOW_IO_ROOT_DISK. The calibration
+    work dir resolves here; on the writable layer it is that same disk. Unknown is a problem,
+    never a skip."""
+    found, problems = {}, []
+    root = os.environ.get("SLOW_IO_ROOT_DISK", "")
+    layer = bool(re.fullmatch(r"\d+:\d+", root))
+    if layer:
+        found["the container's writable layer"] = root
+    else:
+        problems.append(f"SLOW_IO_ROOT_DISK={root or None!r}: the disk behind the container's "
+                        "writable layer is not named — the throttle cannot be proved")
+    work = f"the calibration work dir ({CALIB_WORK})"
+    near = next(p for p in (CALIB_WORK, *CALIB_WORK.parents) if p.exists())
+    if layer and _on_writable_layer(near):
+        found[work] = root
+    elif mm := _disk_of(CALIB_WORK):
+        found[work] = mm
+    else:
+        problems.append(f"STOP: backing device of {near} (the calibration work dir {CALIB_WORK}) "
+                        "could not be resolved — the throttle cannot be applied")
+    return found, problems
+
+
+def _io_problems(cg: Path, required: dict[str, str]) -> list[str]:
     """The disk throttle (SLOW_WRITE_IOPS / SLOW_READ_IOPS, the job's `--device-*-iops`) must be
-    in force on at least one disk of this cgroup's io.max: without it the io part of the
-    calibration — and every IO-bound step — runs at the runner's SSD speed."""
+    in force in this cgroup's io.max on EACH required disk (`_required_disks`): one unthrottled
+    disk runs the io part of the calibration — or any IO-bound step — at the runner's SSD
+    speed. A tight line for some other disk proves nothing."""
+    if not required:
+        return ["no disk is required to be throttled — the disk throttle cannot be proved"]
     want = {}
     for key, var in (("wiops", "SLOW_WRITE_IOPS"), ("riops", "SLOW_READ_IOPS")):
         try:
@@ -285,12 +343,15 @@ def _io_problems(cg: Path) -> list[str]:
         lines = (cg / "io.max").read_text().splitlines()
     except OSError as exc:
         return [f"io.max is unreadable — the disk throttle cannot be proved: {exc}"]
+    tight = set()
     for line in lines:
-        kv = dict(f.split("=", 1) for f in line.split()[1:] if "=" in f)
+        disk, *fields = line.split() or [""]
+        kv = dict(f.split("=", 1) for f in fields if "=" in f)
         if all(kv.get(k, "max") != "max" and int(kv[k]) <= v for k, v in want.items()):
-            return []
-    return [f"io.max {lines!r}: no disk is throttled to wiops<={want['wiops']} "
-            f"riops<={want['riops']}"]
+            tight.add(disk)
+    return [f"io.max {lines!r}: {what}, disk {mm}, is not throttled to "
+            f"wiops<={want['wiops']} riops<={want['riops']}"
+            for what, mm in required.items() if mm not in tight]
 
 
 def test_slow_build_env():
diff --git a/testlab/tests/unit/test_slow_build_lane.py b/testlab/tests/unit/test_slow_build_lane.py
index e9ba5a8..8914ea9 100644
--- a/testlab/tests/unit/test_slow_build_lane.py
+++ b/testlab/tests/unit/test_slow_build_lane.py
@@ -251,24 +251,140 @@ def test_the_lane_never_writes_the_real_home_units(tmp_path):
 
 # ---- the disk throttle is part of the throttled box (Correction 7, GAP 3) -------------------
 
+ROOT, WORK = "8:0", "259:0"
+REQUIRED = {"the container's writable layer": ROOT, "the calibration work dir": WORK}
+
+
 @pytest.mark.parametrize("io_max, ok", [
-    ("8:0 rbps=max wbps=max riops=300 wiops=120\n", True),
-    ("8:0 rbps=max wbps=max riops=200 wiops=100\n", True),        # tighter is fine
-    ("8:0 rbps=max wbps=max riops=max wiops=120\n", False),       # reads unthrottled
-    ("8:0 rbps=max wbps=max riops=300 wiops=500\n", False),       # looser than the job's
+    ("8:0 rbps=max wbps=max riops=300 wiops=120\n259:0 riops=300 wiops=120\n", True),
+    ("8:0 rbps=max wbps=max riops=200 wiops=100\n259:0 riops=1 wiops=1\n", True),  # tighter
+    ("8:0 rbps=max wbps=max riops=max wiops=120\n259:0 riops=300 wiops=120\n", False),  # reads
+    ("8:0 rbps=max wbps=max riops=300 wiops=500\n259:0 riops=300 wiops=120\n", False),  # loose
     ("", False),                                                  # no throttle at all
 ])
 def test_the_lane_proves_the_disk_throttle(tmp_path, monkeypatch, io_max, ok):
     monkeypatch.setenv("SLOW_WRITE_IOPS", "120")
     monkeypatch.setenv("SLOW_READ_IOPS", "300")
     (tmp_path / "io.max").write_text(io_max)
-    assert (lane._io_problems(tmp_path) == []) is ok
+    assert (lane._io_problems(tmp_path, REQUIRED) == []) is ok
+
+
+@pytest.mark.parametrize("io_max, bad", [
+    ("7:0 riops=300 wiops=120\n", [ROOT, WORK]),             # only an unrelated disk is tight
+    ("8:0 riops=300 wiops=120\n7:0 riops=1 wiops=1\n", [WORK]),    # one of the two required
+    ("8:0 riops=300 wiops=120\n259:0 riops=max wiops=max\n", [WORK]),
+])
+def test_each_required_disk_must_be_throttled(tmp_path, monkeypatch, io_max, bad):
+    """Correction 8, finding 1: one tight line anywhere is not the throttle — every required
+    disk needs its own, and the problem names the disk that has none."""
+    monkeypatch.setenv("SLOW_WRITE_IOPS", "120")
+    monkeypatch.setenv("SLOW_READ_IOPS", "300")
+    (tmp_path / "io.max").write_text(io_max)
+    problems = lane._io_problems(tmp_path, REQUIRED)
+    assert [mm for mm in (ROOT, WORK) if any(f"disk {mm}," in p for p in problems)] == bad
+    assert len(problems) == len(bad), problems
+    assert lane._io_problems(tmp_path, {})[0].startswith("no disk is required")
+
+
+def _fake_sys(tmp_path, monkeypatch, dev: int, disk: str | None, partition: bool):
+    """A /sys/dev/block holding `dev` (a partition of `disk` when `partition`)."""
+    sysb = tmp_path / "sys"
+    sysb.mkdir()
+    if disk is not None:
+        node = tmp_path / "devices" / "vda"
+        node.mkdir(parents=True)
+        (node / "dev").write_text(f"{disk}\n")
+        if partition:
+            node = node / "vda1"
+            node.mkdir()
+            (node / "partition").write_text("1\n")
+            (node / "dev").write_text(f"{lane.os.major(dev)}:{lane.os.minor(dev)}\n")
+        (sysb / f"{lane.os.major(dev)}:{lane.os.minor(dev)}").symlink_to(node)
+    monkeypatch.setattr(lane, "SYS_BLOCK", sysb)
+
+
+@pytest.mark.parametrize("partition", [False, True])
+def test_disk_of_resolves_the_whole_disk_like_the_job(tmp_path, monkeypatch, partition):
+    dev = tmp_path.stat().st_dev
+    if not lane.os.major(dev):
+        pytest.skip("tmp_path is on a device-less filesystem here")
+    _fake_sys(tmp_path, monkeypatch, dev, "254:0", partition)
+    assert lane._disk_of(tmp_path / "not" / "yet") == "254:0"   # the nearest existing parent
+
+
+def _box(tmp_path, monkeypatch, root_disk, work_disk, io_max, layer=False):
+    monkeypatch.setenv("SLOW_CPUS", "0.25")
+    monkeypatch.setenv("SLOW_WRITE_IOPS", "120")
+    monkeypatch.setenv("SLOW_READ_IOPS", "300")
+    for k in [k for k in lane.os.environ if k.startswith("LHPC_BUILD_")]:
+        monkeypatch.delenv(k)
+    if root_disk is None:
+        monkeypatch.delenv("SLOW_IO_ROOT_DISK", raising=False)
+    else:
+        monkeypatch.setenv("SLOW_IO_ROOT_DISK", root_disk)
+    work = tmp_path / "home" / ".cache" / "lhpc-calib"
+    (tmp_path / "home").mkdir()
+    monkeypatch.setattr(lane, "CALIB_WORK", work)
+    monkeypatch.setattr(lane, "_disk_of", lambda p: work_disk if p == work else None)
+    monkeypatch.setattr(lane, "_on_writable_layer", lambda p: layer)
+    cg = tmp_path / "cg"
+    cg.mkdir()
+    (cg / "cpu.max").write_text("25000 100000\n")
+    (cg / "memory.max").write_text(f"{416 * 2**20}\n")
+    (cg / "io.max").write_text(io_max)
+    real = lane.Path
+    monkeypatch.setattr(lane, "Path", lambda *a: cg if a == ("/sys/fs/cgroup",) else real(*a))
+
+
+TIGHT = "8:0 riops=300 wiops=120\n259:0 riops=300 wiops=120\n"
+
+
+def test_the_box_with_both_required_disks_throttled_is_the_throttled_box(tmp_path, monkeypatch):
+    _box(tmp_path, monkeypatch, ROOT, WORK, TIGHT)
+    assert lane._env_problems() == []
+
+
+def test_a_work_dir_on_the_writable_layer_is_the_layer_disk(tmp_path, monkeypatch):
+    """The CI case: $HOME is on the container's overlay root, which has no block device inside
+    the container — the work dir then needs the disk the job named behind Docker's storage."""
+    _box(tmp_path, monkeypatch, ROOT, None, "8:0 riops=300 wiops=120\n", layer=True)
+    assert lane._env_problems() == []
+    (tmp_path / "cg" / "io.max").write_text("259:0 riops=300 wiops=120\n")
+    problems = lane._env_problems()
+    assert len(problems) == 2 and all("disk 8:0," in p for p in problems), problems
+
+
+@pytest.mark.parametrize("root_disk, work_disk, want", [
+    (None, WORK, "SLOW_IO_ROOT_DISK=None"),
+    ("sda", WORK, "SLOW_IO_ROOT_DISK='sda'"),
+    (ROOT, None, "STOP: backing device of"),
+])
+def test_an_unresolvable_required_disk_records_nothing(tmp_path, monkeypatch, root_disk,
+                                                       work_disk, want):
+    """Correction 8, finding 2: a required disk that cannot be resolved is a problem — the lane
+    measures nothing as evidence — never a disk silently left out of the check."""
+    _box(tmp_path, monkeypatch, root_disk, work_disk, TIGHT)
+    problems = lane._env_problems()
+    assert any(p.startswith(want) for p in problems), problems
+    monkeypatch.setattr(lane, "EVIDENCE", {})
+    monkeypatch.setattr(lane, "_write", lambda: pytest.fail("evidence written"))
+    with pytest.raises(AssertionError, match="not evidence"):
+        lane._record("meshcore-cli", "build", 12.0)
+    assert lane.EVIDENCE == {}
+
+
+def test_the_calibration_work_dir_is_the_one_the_throttle_check_names(step):
+    seen = []
+    step[0] = _done(stdout="cpu=1.0 io=1.0 mem=1.0 workload=sha256:" + "0" * 64 + "\n")
+    lane.subprocess.run = lambda cmd, **k: seen.append(cmd) or step[0]
+    lane._calibrate()
+    assert seen[0][-2:] == ["--work-dir", str(lane.CALIB_WORK)]
 
 
 def test_the_disk_throttle_must_be_named(tmp_path, monkeypatch):
     monkeypatch.delenv("SLOW_WRITE_IOPS", raising=False)
     (tmp_path / "io.max").write_text("8:0 riops=1 wiops=1\n")
-    assert lane._io_problems(tmp_path)[0].startswith("SLOW_WRITE_IOPS=None")
+    assert lane._io_problems(tmp_path, REQUIRED)[0].startswith("SLOW_WRITE_IOPS=None")
     monkeypatch.setenv("SLOW_WRITE_IOPS", "1")
     monkeypatch.setenv("SLOW_READ_IOPS", "1")
-    assert lane._io_problems(tmp_path / "nowhere")[0].startswith("io.max is unreadable")
+    assert lane._io_problems(tmp_path / "nowhere", REQUIRED)[0].startswith("io.max is unreadable")
```

### 2. 24eb667 — the slow-build job (finding 2)

```diff
diff --git a/.github/workflows/testlab.yml b/.github/workflows/testlab.yml
index d75abdf..e498ee4 100644
--- a/.github/workflows/testlab.yml
+++ b/.github/workflows/testlab.yml
@@ -252,33 +252,55 @@ jobs:
           docker build -t "$IMG" -f .devcontainer/Dockerfile .devcontainer
           echo "IMG=$IMG" >> "$GITHUB_ENV"
 
-      # io.max takes whole disks: every disk behind Docker's storage (the container's root and
-      # its /tmp volume) and behind each active swap area gets the IOPS limits. No disk found is
-      # a failure, never an unthrottled run.
+      # io.max takes whole disks. REQUIRED: the disk behind the container's writable layer
+      # (Docker's storage, or containerd's with its image store), where the calibration work dir
+      # under the lab user's home lives, and the disk behind Docker's storage, which holds the
+      # /tmp volume. Each resolves, or the job STOPS here, before any measurement. An active swap
+      # area's disk is throttled too, or skipped with a notice. The lane re-checks io.max for the
+      # writable layer's disk (SLOW_IO_ROOT_DISK) and the work dir's, and records nothing unless
+      # each is throttled.
       - name: Resolve the disks to throttle
         run: |
+          # The whole disk behind a path or block device, as /dev/NAME; non-zero, naming the
+          # path, when it cannot be resolved.
           disk_of() {
             local mm d
-            if [ -b "$1" ]; then mm=$(lsblk -dno MAJ:MIN "$1" | tr -d ' ')
-            else mm=$(findmnt -n -o MAJ:MIN --target "$1" | head -1 | tr -d ' '); fi
-            [ -n "$mm" ] && [ -e "/sys/dev/block/$mm" ] || return 0
+            if [ -b "$1" ]; then mm=$(lsblk -dno MAJ:MIN "$1" 2>/dev/null | tr -d ' ')
+            else mm=$(findmnt -n -o MAJ:MIN --target "$1" 2>/dev/null | head -1 | tr -d ' '); fi
+            if [ -z "$mm" ] || [ ! -e "/sys/dev/block/$mm" ]; then
+              echo "disk_of: backing device of $1 could not be resolved (MAJ:MIN ${mm:-none})" >&2
+              return 1
+            fi
             d=$(readlink -f "/sys/dev/block/$mm")
             [ -e "$d/partition" ] && d=$(dirname "$d")
             echo "/dev/$(basename "$d")"
           }
           cat /proc/swaps
           lsblk
-          disks=$( { disk_of "$(docker info -f '{{.DockerRootDir}}')"
-                     tail -n +2 /proc/swaps | while read -r swap _; do disk_of "$swap"; done
-                   } | sort -u)
-          [ -n "$disks" ] || { echo "::error::no disk behind Docker's storage resolved"; exit 1; }
+          storage=$(docker info -f '{{.DockerRootDir}}')
+          layer=$storage
+          if [ "$(docker info -f '{{.Driver}}')" = overlayfs ]; then layer=/var/lib/containerd; fi
+          disks=
+          for path in "$layer" "$storage"; do
+            d=$(disk_of "$path") && test -b "$d" || {
+              echo "::error::STOP: backing device of $path could not be resolved — the throttle cannot be applied"
+              exit 1
+            }
+            [ "$path" = "$layer" ] && root=$d
+            disks="$disks $d"
+          done
+          while read -r swap _; do
+            if d=$(disk_of "$swap") && test -b "$d"; then disks="$disks $d"
+            else echo "::notice::swap $swap: its disk could not be resolved — not throttled"; fi
+          done < <(tail -n +2 /proc/swaps)
+          disks=$(printf '%s\n' $disks | sort -u | xargs)
           flags=
           for d in $disks; do
-            test -b "$d" || { echo "::error::$d is not a block device"; exit 1; }
             flags="$flags --device-write-iops $d:$SLOW_WRITE_IOPS --device-read-iops $d:$SLOW_READ_IOPS"
           done
-          echo "throttled disks: $disks"
+          echo "throttled disks: $disks (writable layer: $root)"
           echo "SLOW_IO_FLAGS=$flags" >> "$GITHUB_ENV"
+          echo "SLOW_IO_ROOT_DISK=$(lsblk -dno MAJ:MIN "$root" | tr -d ' ')" >> "$GITHUB_ENV"
 
       # A heredoc lane script, as in release-verify. The container gets the throttle; the lane
       # itself checks that cpu.max, memory.max and io.max are in force and fails if they are not.
@@ -302,14 +324,14 @@ jobs:
           # shellcheck disable=SC2086  # SLOW_IO_FLAGS is a word list by design
           docker run --rm --user root \
             --cpus="$SLOW_CPUS" --memory="$SLOW_MEM" --memory-swap="$SLOW_SWAP" $SLOW_IO_FLAGS \
-            -e SLOW_CPUS -e SLOW_MEM -e SLOW_WRITE_IOPS -e SLOW_READ_IOPS \
+            -e SLOW_CPUS -e SLOW_MEM -e SLOW_WRITE_IOPS -e SLOW_READ_IOPS -e SLOW_IO_ROOT_DISK \
             -e LHPC_SLOW_BUILD_RUN_URL="$GITHUB_SERVER_URL/$GITHUB_REPOSITORY/actions/runs/$GITHUB_RUN_ID" \
             -v "$PWD":/repo:ro -v lhpc_slow_tmp:/tmp -v "$PWD/slowevidence":/out \
             "$IMG" bash -lc '
               set -e
               cp -r /repo /tmp/repo && chown -R lhpclab /tmp/repo && chmod a+w /out
               install -m 0755 /repo/lane.sh /tmp/lane.sh
-              su lhpclab -c "SLOW_CPUS=$SLOW_CPUS SLOW_MEM=$SLOW_MEM SLOW_WRITE_IOPS=$SLOW_WRITE_IOPS SLOW_READ_IOPS=$SLOW_READ_IOPS LHPC_SLOW_BUILD_RUN_URL=$LHPC_SLOW_BUILD_RUN_URL /tmp/lane.sh"
+              su lhpclab -c "SLOW_CPUS=$SLOW_CPUS SLOW_MEM=$SLOW_MEM SLOW_WRITE_IOPS=$SLOW_WRITE_IOPS SLOW_READ_IOPS=$SLOW_READ_IOPS SLOW_IO_ROOT_DISK=$SLOW_IO_ROOT_DISK LHPC_SLOW_BUILD_RUN_URL=$LHPC_SLOW_BUILD_RUN_URL /tmp/lane.sh"
             '
           test -s slowevidence/junit-slow-build.xml \
             || { echo "::error::the lane produced no JUnit — it did not run"; exit 1; }
diff --git a/tests/repo/test_slow_build_gate.py b/tests/repo/test_slow_build_gate.py
index c3b278a..02c6c25 100644
--- a/tests/repo/test_slow_build_gate.py
+++ b/tests/repo/test_slow_build_gate.py
@@ -12,6 +12,8 @@ import sys
 import textwrap
 from pathlib import Path
 
+import pytest
+
 import repo_paths
 
 WORKFLOW = repo_paths.REPO / ".github" / "workflows" / "testlab.yml"
@@ -113,10 +115,13 @@ def test_the_throttle_is_named_and_reaches_the_container_and_the_lane():
     run = text[text.index("docker run --rm --user root"):text.index("test -s slowevidence")]
     for flag in ('--cpus="$SLOW_CPUS"', '--memory="$SLOW_MEM"', '--memory-swap="$SLOW_SWAP"',
                  "$SLOW_IO_FLAGS", "-e SLOW_CPUS -e SLOW_MEM -e SLOW_WRITE_IOPS -e SLOW_READ_IOPS",
-                 "SLOW_WRITE_IOPS=$SLOW_WRITE_IOPS SLOW_READ_IOPS=$SLOW_READ_IOPS"):
+                 "-e SLOW_IO_ROOT_DISK",
+                 "SLOW_WRITE_IOPS=$SLOW_WRITE_IOPS SLOW_READ_IOPS=$SLOW_READ_IOPS",
+                 "SLOW_IO_ROOT_DISK=$SLOW_IO_ROOT_DISK"):
         assert flag in run, flag
     assert '--device-write-iops $d:$SLOW_WRITE_IOPS --device-read-iops $d:$SLOW_READ_IOPS' in text
     assert 'echo "SLOW_IO_FLAGS=$flags" >> "$GITHUB_ENV"' in text
+    assert 'echo "SLOW_IO_ROOT_DISK=' in text
 
 
 def test_the_cpu_quota_is_below_the_zero_breakeven():
@@ -125,3 +130,74 @@ def test_the_cpu_quota_is_below_the_zero_breakeven():
     quota above it is a container faster than the Zero by construction."""
     assert 0 < float(_job_env()["SLOW_CPUS"]) <= 0.5 * 57.8 / 99.7
     assert int(_job_env()["SLOW_WRITE_IOPS"]) <= 134    # the Zero's 65536 synced files / 489.8 s
+
+
+# ---- the disks to throttle (Correction 8): a required disk resolves, or nothing is measured ----
+
+def _resolve_step() -> str:
+    text = WORKFLOW.read_text()
+    m = re.search(r"^( *)- name: Resolve the disks to throttle\n\1  run: \|\n(.*?)\n\n",
+                  text, re.M | re.S)
+    assert m, "the slow-build job's disk-resolving step is gone from testlab.yml"
+    return textwrap.dedent(m.group(2))
+
+
+def _resolve(tmp_path: Path, storage: str, driver: str = "overlay2"):
+    """The step, as the runner runs it (`bash -e`), with a stub `docker` naming `storage`."""
+    stub = tmp_path / "bin"
+    stub.mkdir()
+    (stub / "docker").write_text(
+        "#!/bin/sh\ncase $* in *DockerRootDir*) echo " + storage + " ;; "
+        "*Driver*) echo " + driver + " ;; *) exit 9 ;; esac\n")
+    (stub / "docker").chmod(0o755)
+    env_file = tmp_path / "github_env"
+    env_file.write_text("")
+    env = {"PATH": f"{stub}:/usr/bin:/bin:/usr/sbin:/sbin", "GITHUB_ENV": str(env_file),
+           "SLOW_WRITE_IOPS": "120", "SLOW_READ_IOPS": "1200"}
+    r = subprocess.run(["bash", "-e", "-c", _resolve_step()], env=env, capture_output=True,
+                       text=True, check=False)
+    return r, env_file.read_text()
+
+
+def _tools():
+    if not all(Path(p).exists() for p in ("/usr/bin/findmnt", "/usr/bin/lsblk", "/proc/swaps")):
+        pytest.skip("findmnt / lsblk / /proc/swaps are not available here")
+
+
+@pytest.mark.parametrize("storage", ["no/such/storage", "/proc"])   # missing; no block device
+def test_an_unresolvable_required_disk_stops_the_job_before_any_measurement(tmp_path, storage):
+    """Correction 8, finding 2: Docker's storage on no resolvable disk was skipped (`return 0`)
+    and the job ran unthrottled on the other disks. Now it stops, naming the path, and hands the
+    measuring step no throttle to run with."""
+    _tools()
+    storage = str(tmp_path / storage)
+    r, env = _resolve(tmp_path, storage)
+    assert r.returncode == 1, r.stdout + r.stderr
+    assert (f"::error::STOP: backing device of {storage} could not be "
+            "resolved — the throttle cannot be applied") in r.stdout, r.stdout
+    assert "SLOW_IO_FLAGS" not in env and "SLOW_IO_ROOT_DISK" not in env
+
+
+def test_disk_of_fails_loudly_on_an_unresolvable_path(tmp_path):
+    _tools()
+    fn = re.search(r"^disk_of\(\) \{\n.*?^\}\n", _resolve_step(), re.M | re.S)
+    assert fn, "disk_of() is gone from the step"
+    r = subprocess.run(["bash", "-c", fn.group(0) + 'disk_of "$1"', "-", str(tmp_path / "x")],
+                       capture_output=True, text=True, check=False)
+    assert r.returncode == 1 and r.stdout == "", r.stdout
+    assert f"backing device of {tmp_path}/x could not be resolved" in r.stderr, r.stderr
+
+
+def test_a_resolvable_storage_disk_is_throttled_and_named_to_the_lane(tmp_path):
+    _tools()
+    probe = subprocess.run(["findmnt", "-n", "-o", "MAJ:MIN", "--target", str(tmp_path)],
+                           capture_output=True, text=True, check=False).stdout.split()
+    if not probe or not Path(f"/sys/dev/block/{probe[0]}").exists():
+        pytest.skip("the test's own disk does not resolve here")
+    r, env = _resolve(tmp_path, str(tmp_path))
+    assert r.returncode == 0, r.stdout + r.stderr
+    flags = re.search(r"^SLOW_IO_FLAGS=(.*)$", env, re.M).group(1)
+    root = re.search(r"^SLOW_IO_ROOT_DISK=(\d+:\d+)$", env, re.M)
+    assert root, env
+    disk = re.search(r"--device-write-iops (/dev/\S+):120 --device-read-iops \1:1200", flags)
+    assert disk, flags
```

### 3. c4ebbcb — the release-policy sentence, docs (finding 3)

```diff
diff --git a/docs/maintenance.md b/docs/maintenance.md
index 0792539..ed780bf 100644
--- a/docs/maintenance.md
+++ b/docs/maintenance.md
@@ -165,7 +167,7 @@ repositories.
   in row C and in row A alike. The lane detects it (the previous tag's
   `lhpc/core/service_selfupdate.py` lacks the line), still times the whole helper (L3), and names
   the gap in the job summary (and the bootstrap skip reason). The gap is waived, by name, up to
-  and including that release (`lhpc.core.slow_target.PIP_SYNC_SINCE`, 0.11.12): the coverage
+  and including that release (`lhpc.core.slow_target.PIP_SYNC_SINCE`, 0.12.0): the coverage
   test reports the L4 pair as waived, not missing, and the lane's budget case waives its missing
   evidence and Zero baseline when the previous tag lacks the line too. From the next release on
   both require it.
diff --git a/docs/testlab.md b/docs/testlab.md
index 3809f0c..b4ecefd 100644
--- a/docs/testlab.md
+++ b/docs/testlab.md
@@ -144,14 +144,16 @@ subprocesses import `lhpc` from the editable install, i.e. whichever checkout wa
 - **slow-build** is row C of the [slow-target build row](maintenance.md#branches-and-releases):
   the CI job `slow-build` runs it inside a container throttled to a Pi Zero 2 W's CPU, disk and
   memory (`--cpus=$SLOW_CPUS --memory=$SLOW_MEM --memory-swap=$SLOW_SWAP` and
-  `--device-write-iops`/`--device-read-iops` on every disk behind Docker's storage and swap;
-  the numbers and their measured rationale are the job's `env`). Under the production limits it times
+  `--device-write-iops`/`--device-read-iops` on the disk behind the container's writable layer
+  and behind Docker's storage, which must resolve or the job stops before measuring, and on each
+  resolvable swap disk; the numbers and their measured rationale are the job's `env`). Under the production limits it times
   each lane stack's install (clone, checkout, the Meshtastic CLI venv), every component's build
   (wall time and longest quiet gap), the graywolf upstream fetch and a self-update from the
   previous release tag, then checks them with `lhpc.core.slow_target` against
   `tests/data/slow-target-builds.toml`.
-  - `test_slow_build_env` fails unless the throttle is in force and no `LHPC_BUILD_*` override is
-    set; nothing is recorded otherwise. `test_slow_build_calibrated` runs
+  - `test_slow_build_env` fails unless the throttle is in force — `io.max` throttles the disk
+    behind the writable layer and the one behind the calibration work dir, each by name — and no
+    `LHPC_BUILD_*` override is set; nothing is recorded otherwise. `test_slow_build_calibrated` runs
     `testlab/slowbuild/calibrate.sh` and fails while the container is faster than the Zero on any
     part (cpu, io, mem) of the same workload (then tighten that part's throttle; a throttle never
     loosens). `test_slow_build_budget` names every
```

### 4. c8e886c — calibrate.sh, docs (the work dir)

```diff
diff --git a/docs/maintenance.md b/docs/maintenance.md
index 0792539..12fed19 100644
--- a/docs/maintenance.md
+++ b/docs/maintenance.md
@@ -151,10 +151,12 @@ repositories.
   when row C measures the new pin above limit/4 (= 50 % of the budget) or that (component, op) has
   no Zero entry at all. A minor runs rows 6, 7, 8, the self-update and `calibrate.sh` on the Zero 2 W
   before the tag and commits the numbers ([test matrix](test-matrix.md#slow-target-baseline)),
-  which refreshes the baselines and the calibration. `calibrate.sh` works in
-  `$HOME/loraham-pi-control/state/lhpc-calib` on the SD card (the runtime root's state dir; it
-  needs 256 MB + 64 MB free there), never in `/tmp`, a ~200 MB tmpfs on the Zero: it refuses a
-  tmpfs work dir and one with too little free space. Bootstrap: while
+  which refreshes the baselines and the calibration. Run from the installed checkout
+  `<runtime root>/src/loraham-pi-control` while `<runtime root>/state` exists, `calibrate.sh` works
+  in `<runtime root>/state/lhpc-calib` on the SD card (any other checkout, or no `state` dir:
+  `$HOME/.cache/lhpc-calib`; `--work-dir DIR` overrides both). It needs 256 MB + 64 MB free there, never works in `/tmp`, a
+  ~200 MB tmpfs on the Zero, and refuses a tmpfs work dir, one with too little free space and an
+  existing one. Bootstrap: while
   `tests/data/slow-target-builds.toml` holds no measured entry at all (before the first row A),
   `test_coverage`, `test_slow_build_calibrated` and `test_slow_build_budget` SKIP with a
   "bootstrap: no row A yet" reason naming every unmeasured operation, and the `slow-build` gate
diff --git a/docs/test-matrix.md b/docs/test-matrix.md
index 63acae1..de0ac99 100644
--- a/docs/test-matrix.md
+++ b/docs/test-matrix.md
@@ -108,7 +108,7 @@ that runs it on the Zero.
 | `selfupdate-helper` | a one-click update from the previous release: `systemctl --user show lhpc-selfupdate.service -p ExecMainStartTimestamp -p ExecMainExitTimestamp`, the difference |
 | `selfupdate-pip` | the `[selfupdate] pip sync <n> s` line of that run in `logs/lhpc-selfupdate.log`; which release has it: see [maintenance](maintenance.md#branches-and-releases), the slow-target build row |
 | `deb-fetch` | `t bash lhpc/data/scripts/graywolf-fetch.sh /tmp/gw <latest> --from-upstream` |
-| calibration | `bash testlab/slowbuild/calibrate.sh` (from the installed checkout, so its work dir is `state/lhpc-calib` on the SD card) prints `cpu= io= mem= workload=`: one `[[calibration]]` entry |
+| calibration | `bash testlab/slowbuild/calibrate.sh` (from the installed checkout `<runtime root>/src/loraham-pi-control` with `<runtime root>/state` present, so its work dir is `<runtime root>/state/lhpc-calib` on the SD card; otherwise `$HOME/.cache/lhpc-calib`) prints `cpu= io= mem= workload=`: one `[[calibration]]` entry |
 
 `key` is `pin:<the component's manifest pin_commit>` (the `cli-venv` entry: meshtastic's),
 `deb:<version fetched>`, or for both self-update entries the output of
```
