# Gate 1 — code review request, F43, Correction 7

**Request.** Judge ONLY the five commits amended in Correction 7, and within each only the
amendment shown below (each diff is the commit's new version against its old version, restricted
to the files the amendment touched; the rest of each commit is the version already judged).
The other eight F43 commits and the data commit keep their patch-ids and are not under review.
Judge, per commit:

1. **fc89dac** "the slow-target baseline file" — the header documents the `lhpc` label forms.
2. **05ccfd1** "the slow-target budget test and its shared rule" — GAP 1: `entry_errors`
   accepts a release tag `vX.Y.Z (sha)` and a candidate ref `release/<name> (sha)` (and a
   self-update's `<from tag> -> <to> (sha)`), the SHA mandatory; `minor()` of each form. And the
   coverage rule: `PIP_SYNC_SINCE` / `waiver()` make L4 (`selfupdate-pip`) GREEN, by name, up to
   and including its introducing release; `_check_coverage` lists only genuinely unmeasured pairs.
   Check that nothing else can be waived and that an unmeasured pair is never green.
3. **5e4a4c6** "the slow-build lane" — GAP 2: the self-update case runs on a runtime laid out as a
   box with the canonical units installed the way install.sh installs them; the helper's unit
   verification is not stubbed. The lane's L4 waiver past bootstrap. The disk-throttle proof
   `_io_problems` in `test_slow_build_env`. Check that no path records evidence from an
   unthrottled box and that the waiver cannot hide a lost timing line.
4. **3e5d3cd** "the slow-build job" — GAP 3: the throttle constants and their rationale, the step
   that resolves the disks to throttle, the `docker run` flags. Check that a run cannot proceed
   unthrottled on any axis without failing, and that the numbers follow from the stated
   measurements.
5. **4a9c743** "the release-policy sentence …" — CHANGELOG and docs: is every sentence true as
   written, given that the lane's calibration case turns the release check red whenever the
   container is faster than the Zero on any axis?

Answer in the form `| commit | verdict (OK / FINDING) | what |`, then give one final line: GREEN /
GREEN WITH NOTES / RED. A finding names the line in the diff and what goes wrong.

This file is your whole input: you have no repository access; use no connector, tool or web lookup.

## Correction 7 — evidence added before this review

**Red-before of every new test.** Method: the tree at the corr7 code head `9d9e82b`, with the three
production files Correction 7 changed (`lhpc/core/slow_target.py`, `.github/workflows/testlab.yml`,
`testlab/tests/slowbuild/test_slow_build.py`) put back to their Correction 6 versions (`7de65df`), then
only the new tests run. With the Correction 7 files they pass.

| new test | against the Correction 6 code |
|---|---|
| `tests/install/test_slow_target_budget.py`: `test_the_lhpc_label_takes_a_tag_or_a_candidate_ref` (GAP 1 label), `test_coverage_waives_l4_up_to_the_introducing_release_and_names_it`, `test_coverage_lists_only_the_genuinely_unmeasured`, `test_the_introducing_release_is_a_changelog_release` | `4 failed` |
| `tests/repo/test_slow_build_gate.py`: `test_the_throttle_is_named_and_reaches_the_container_and_the_lane`, `test_the_cpu_quota_is_below_the_zero_breakeven` | `2 failed` |
| `testlab/tests/unit/test_slow_build_lane.py`: `test_past_bootstrap_the_introducing_release_waives_l4_only`, `test_the_l4_waiver_needs_the_version_rule_too`, `test_the_helper_runtime_has_the_units_its_verification_requires` (the unit-install test), `test_the_lane_never_writes_the_real_home_units`, `test_the_lane_proves_the_disk_throttle` (5 cases), `test_the_disk_throttle_must_be_named` | `10 failed` (all cases) |

**One code fixup, in `05ccfd1` (the budget-test commit): `PIP_SYNC_SINCE` "0.11.12" → "0.12.0".** The
0.11.12 patch was folded into the 0.12.0 release, so the release that introduces the pip-sync line is
0.12.0. Its red-before is the guard test that caught it. On `integration/0.12.0` (version 0.12.0, no
`## 0.11.12` section) the old value failed `test_the_introducing_release_is_a_changelog_release` and,
with the L4 waiver keyed to 0.11.12, `test_coverage` (`selfupdate-pip` unmeasured). With "0.12.0"
both pass. On this branch (version 0.11.11) the waiver still applies (0.11.11 ≤ 0.12.0).

**Which `[[calibration]]` the lane uses when two exist.** `calibration_failures` keeps the `zero2w`
entries whose `workload` equals the script's hash and takes the newest by `date`, never by position.
The Zero's calibration of this script is the third data commit on `f43/baseline-rowA`, `4638cdd8` (not
on this branch; it joins in the integration delta):
workload `sha256:17b2f715…`, cpu 98.7, io 492.1, mem 103.2. That workload hash, recomputed from this
branch's `testlab/slowbuild` (the script, `calib-src/`, `io_mb=256`), is identical. The older entry
(`ae7c6013…`, the round-5 script) no longer applies.

**House rule.** The baseline header's host example and its first line no longer name the reference
box (fixed up into `fc89dac`). The report names no tool's branches.

### Numbers, measured

| figure | command | output |
|---|---|---|
| red-before counts | `pytest -q -k <new tests>` with the three Correction 6 production files restored | 4 failed / 2 failed / 10 failed |
| the fixup's guard | `pytest tests/install/test_slow_target_budget.py` on `integration/0.12.0` + this delta, before the fix | 2 failed (`…_is_a_changelog_release`, `test_coverage`) |
| calibration workload | `find calib-src … \| sort -z \| xargs -0 sha256sum calibrate.sh; echo io_mb=256` piped to `sha256sum` (the script's own rule) | `17b2f7159232…` = the data commit's |
| script blob | `sha256sum testlab/slowbuild/calibrate.sh` | `b4ff9d6f4eab…` |
| GAP 3 table | recorded for this correction on an x86 VM (cgroup v1); not re-derivable on another host | the job row's io is carried over from the 300-IOPS run; the first lane run measures it |

## Why the commits were amended

The slow-build job's second real run (a release candidate, 0.11.12) was 8 passed / 2 failed /
1 bootstrap skip, and the first real row A (30 measured entries and one calibration from a Pi Zero
2 W, committed as a data commit after the series) exposed three gaps:

- **GAP 1.** Row A measured a candidate, so its entries carry
  `lhpc = "release/0.11.12-bundle (3d0943a1)"`. The old rule accepted only a label starting with
  `vX.Y.` and rejected 29 of 30 entries. The one self-update entry is
  `lhpc = "v0.11.10 -> v0.11.11 (35923b80)"` (it timed that update); the data stays as it is.
- **GAP 2.** The self-update helper applied the update, then exited 1: "the managed systemd units
  could NOT be refreshed — units not canonical: lhpc-boot-restore.service: missing; …". The lane's
  runtime had no units; a box has them.
- **GAP 3.** The throttled container was faster than the Zero on every axis of the fixed
  calibration workload. Zero: cpu 99.7 s, io 489.8 s, mem 110.1 s. Container at `--cpus 0.5
  --memory 416m`, no disk limit: cpu 57.8, io 31.4, mem 12.2.

Also: with row A in the baseline, coverage was red for 17 pairs. 16 are being measured on the Zero
now (row A part 2, a later data commit). The 17th is L4 (`selfupdate-pip`): the release that
introduces the helper's `[selfupdate] pip sync <n> s` line (0.11.12) is updated TO by the previous
release's helper, which cannot print it, on the Zero and in the lane alike.

## Context (unchanged code the amendments rely on)

- **The budget rule** `lhpc/core/slow_target.py`: `required(stacks, excluded)` lists every
  (component, op) the baseline must hold, leaving out `[excluded]` components;
  `bootstrap_reason(measured, unmeasured)` is "" once any measured entry exists;
  `zero_baseline()` carries the latest Zero entry of this or the previous minor (by `minor()`);
  `compare()` yields failures such as `no row C evidence for <c> <op>: …` and
  `no Zero baseline for <c> <op>: run row A`; `calibration_failures()` compares the container's
  cpu_s/io_s/mem_s with the latest `zero2w` `[[calibration]]` of the SAME workload hash and fails
  on any axis that is faster.
- **The lane** `testlab/tests/slowbuild/test_slow_build.py` runs inside the job's container:
  `_env_problems()` (checked by `test_slow_build_env` and by every `_record()` call) refuses to
  record unless `SLOW_CPUS` is in (0, 1], the cgroup's `cpu.max` enforces it, `memory.max` is
  ≤ 416 MiB and no `LHPC_BUILD_*` override is set. `_helper()` runs
  `lhpc self-update --run-service` with `INVOCATION_ID` set (as systemd does), times it whole and
  fails on a non-zero exit or a rejection marker. `INTRODUCING` records the previous tag when its
  `lhpc/core/service_selfupdate.py` lacks the pip sync line.
- **The helper** (`service_selfupdate`): after applying the update and syncing the venv it runs
  `<root>/venv/lhpc/bin/python -m lhpc.core.updater_units verify-set <root>` (the running
  interpreter's python if that venv is absent): every unit of `ALL_UNITS` under
  `$HOME/.config/systemd/user` must be byte-identical to `render(kind, root, checkout, venv)` with
  `deployment_paths(root)` = (`root`, `root/src/loraham-pi-control`, `root/venv/lhpc`). It runs no
  `systemctl`. Before applying, its controller-identity check applies when lhpc runs from
  `<root>/src/loraham-pi-control`: the checkout must be on branch `main` with `origin` equal to the
  manifest's controller remote; otherwise ("not self-hosted") the check is skipped.
- **install.sh** writes each unit with
  `"${VENV}/bin/python" -m lhpc.core.updater_units render <kind> "$TARGET_DIR" "$CHECKOUT" "$VENV"`
  into the user unit dir, then `systemctl --user daemon-reload/enable`.
- **The product runner** gives subprocesses a fixed environment: PATH, LANG, LC_ALL and only
  HOME, XDG_RUNTIME_DIR, XDG_CONFIG_HOME, DBUS_SESSION_BUS_ADDRESS, PLATFORMIO_CORE_DIR,
  IDF_TOOLS_PATH, XDG_CACHE_HOME, PIP_CACHE_DIR, TMPDIR, LHPC_QEMU_MIN_FREE_INODES.
- **The job** (`slow-build`, `ubuntu-24.04-arm`, 330 min): builds the lab image, runs the lane in
  `docker run --user root … -v lhpc_slow_tmp:/tmp …`, the lane as user `lhpclab` via `su`; then a
  JUnit gate requires env, every stack case, selfupdate, calibrated and budget to have PASSED
  (skips accepted only in bootstrap).
- **`calibrate.sh`** — cpu: `make -j$(nproc)` of vendored C sources; io: write and fsync 65536
  files of 4 KiB, then read them back, in a work dir on disk (in the lane:
  `$HOME/.cache/lhpc-calib` on the container's root file system); mem: touch 700 MB of anonymous
  pages twice. The workload hash covers the script, the sources and the io size.
- **CHANGELOG** is at `## 0.11.12` (the release under preparation); `lhpc/version.py` still says
  0.11.11 until the release commit.

## Measurements behind the GAP 3 numbers (measured for this correction; x86 VM, 4 cores, cgroup v1)

`calibrate.sh` in a container, work dir and swap file on a journaled ext4 disk, throttled with
the same docker flags:

| throttle (cpus / memory / write IOPS / read IOPS) | cpu | io | mem |
|---|---|---|---|
| 0.5 / 416m / none / none (the old job) | 83.3 | 25.2 | 8.1 |
| 0.25 / 416m / 120 / 300 | 158.0 | 614.6 | 608.3 |
| 0.25 / 416m / 120 / 2000 (mem part only) | — | — | 97.1 |
| 0.25 / 416m / 120 / 1000 (mem part only) | — | — | 185.7 |
| **0.25 / 416m / 120 / 1200 (the job)** | **163.0** | **614.6** (carried over from the 300-IOPS run; the first lane run measures it) | **156.1** |
| the Zero (row A) | 99.7 | 489.8 | 110.1 |

2048 synced 4 KiB files: 20.5 s at 120 write IOPS on the journaled disk (~1.2 writes charged per
file), 68 s on an unjournaled one (~4). The hosted arm runner (cgroup v2) was not available here;
its first run is the proof on the runner. The Zero's first `[[calibration]]` is for the candidate's older `calibrate.sh` (another workload
hash). The job's JUnit gate requires `test_slow_build_calibrated` PASSED, so every slow-build run
(the release check) is RED until the tree under test carries a `zero2w` calibration of this
script. That calibration now exists on `f43/baseline-rowA` at `4638cdd8` (the third data commit), not
on this branch, whose baseline carries only the `ae7c6013…` calibration. It is stacked with this
branch in the integration delta, so this branch's own lane run would still read "uncalibrated".

## The amendments

### 1. fc89dac — the slow-target baseline file (header)

```diff
diff --git a/tests/data/slow-target-builds.toml b/tests/data/slow-target-builds.toml
index 1f8702c..0a286b1 100644
--- a/tests/data/slow-target-builds.toml
+++ b/tests/data/slow-target-builds.toml
@@ -1,5 +1,5 @@
 # The slow-target BASELINE (plans/PLAN-F43.md §7, §8): what the update path's operations take on
-# the real slow target (Pi Zero 2 W `lhpc-e293`, row A) and in the throttled CI container (row C).
+# the real slow target (a Pi Zero 2 W, row A) and in the throttled CI container (row C).
 # `tests/install/test_slow_target_budget.py` checks every limit on the update path against it, and
 # the `slow-build` lane checks a release's fresh evidence against it (docs/maintenance.md).
 #
@@ -21,8 +21,13 @@
 #   quiet_s = 140                # op = "build" only: longest gap without progress (L1)
 #   key = "pin:<manifest pin>"   # pin:<pin> | deb:<version fetched> | deps:<sha256>; never an LHPC SHA
 #   source = "zero2w"            # or "throttled-ci"
-#   host = "lhpc-e293"           # or "gha ubuntu-24.04-arm cpus=0.5 mem=416m"
-#   lhpc = "v0.12.0 (6ff1937)"   # provenance and the entry's minor; not part of the key
+#   host = "zero2w-ref"          # or "gha ubuntu-24.04-arm cpus=0.25 mem=416m wiops=120 riops=1200"
+#   lhpc = "v0.12.0 (6ff1937)"   # provenance and the entry's minor; not part of the key. Two
+#                                #   forms, the short SHA mandatory in both: a release tag
+#                                #   "vX.Y.Z (sha7)", or a candidate ref "release/<name> (sha7)"
+#                                #   (row A on a candidate before its tag; minor = the first
+#                                #   X.Y.Z in <name>). A self-update entry names the update it
+#                                #   timed: "<from tag> -> <to tag or ref> (sha7)".
 #   date = 2026-10-10
 #   evidence = "docs/live-tests/live-test.md#rows"   # or the Actions run URL
 #
```

### 2. 05ccfd1 — the slow-target budget test and its shared rule

```diff
diff --git a/lhpc/core/slow_target.py b/lhpc/core/slow_target.py
index e196a20..bf3cd6f 100644
--- a/lhpc/core/slow_target.py
+++ b/lhpc/core/slow_target.py
@@ -23,6 +23,18 @@ SELFUPDATE_COMPONENT = "lhpc-selfupdate"
 CLI_VENV_COMPONENT = "meshtastic-cli-venv"
 _KEY_RE = re.compile(r"(pin|deb|deps):\S+")
 _MINOR_RE = re.compile(r"v?(\d+)\.(\d+)\.")
+# `lhpc`: the LHPC tree an entry was measured on, the short SHA mandatory: a release tag
+# `vX.Y.Z (sha7)`, or a release candidate's ref `release/<name> (sha7)` (row A measures the
+# candidate before its tag exists). A self-update entry names the update it timed,
+# `<from tag> -> <to tag or ref> (sha7)`. The minor is the (target) tag's X.Y, or the first X.Y.Z
+# in a candidate ref's name.
+_REL = r"(?:v\d+\.\d+\.\d+\S*|release/\S+)"
+_LHPC_RE = re.compile(rf"(?:v\d+\.\d+\.\d+\S* -> )?{_REL} \([0-9a-f]{{7,40}}\)")
+_REF_MINOR_RE = re.compile(r"(\d+)\.(\d+)\.\d")
+# The first release whose self-update helper prints `[selfupdate] pip sync <n> s` (L4). An update
+# TO it runs the previous release's helper, which cannot print the line, on the Zero and in the
+# lane alike: L4 is measurable from the next release on (docs/maintenance.md).
+PIP_SYNC_SINCE = "0.12.0"
 
 
 class LimitUnavailable(Exception):
@@ -205,8 +217,9 @@ def entry_errors(entry: dict) -> list[str]:
         errs.append("key must be pin:<..>, deb:<..> or deps:<..>")
     if entry.get("source") not in SOURCES:
         errs.append(f"source must be one of {SOURCES}")
-    if minor(entry) is None:
-        errs.append("lhpc must name the release, e.g. 'v0.12.0 (6ff1937)'")
+    if not _LHPC_RE.fullmatch(str(entry.get("lhpc", ""))):
+        errs.append("lhpc must name the release tag or candidate ref and its short SHA, e.g. "
+                    "'v0.12.0 (6ff1937)' or 'release/0.12.0-bundle (6ff1937)'")
     if not isinstance(entry.get("date"), _dt.date):
         errs.append("date must be a TOML date")
     for f in ("host", "evidence"):
@@ -216,7 +229,13 @@ def entry_errors(entry: dict) -> list[str]:
 
 
 def minor(entry: dict):
-    m = _MINOR_RE.match(str(entry.get("lhpc", "")))
+    """The minor of the entry's `lhpc` label (an update's: its target's): the tag's X.Y, or the
+    first X.Y.Z in a candidate ref's name; None when it names none (such an entry is only ever
+    FRESH, never carried)."""
+    label = str(entry.get("lhpc", "")).split(" -> ")[-1]
+    m = (_MINOR_RE.match(label) if label.startswith("v")
+         else _REF_MINOR_RE.search(label.split(" ", 1)[0]) if label.startswith("release/")
+         else None)
     return (int(m.group(1)), int(m.group(2))) if m else None
 
 
@@ -225,6 +244,22 @@ def version_minor(version: str):
     return (int(m.group(1)), int(m.group(2))) if m else None
 
 
+def _release(version: str) -> tuple[int, ...]:
+    return tuple(int(x) for x in re.findall(r"\d+", version)[:3])
+
+
+def waiver(component: str, op: str, version: str) -> str:
+    """Why (component, op) needs no measurement on a tree at `version`, or "". The one waiver:
+    L4 (`selfupdate-pip`) up to and including the release that introduces its timing line
+    (PIP_SYNC_SINCE) — nobody can measure it there. Excluded components never reach here:
+    `required` leaves them out."""
+    if (component, op) == (SELFUPDATE_COMPONENT, "selfupdate-pip") and \
+            _release(version) <= _release(PIP_SYNC_SINCE):
+        return (f"{component} {op} (L4): no evidence up to the introducing release "
+                f"{PIP_SYNC_SINCE} — measured from the next release on")
+    return ""
+
+
 # ---- the rule (§8) -------------------------------------------------------------------------
 
 def zero_baseline(entries, component: str, op: str, key: str, this_minor):
diff --git a/tests/install/test_slow_target_budget.py b/tests/install/test_slow_target_budget.py
index cf47069..6135600 100644
--- a/tests/install/test_slow_target_budget.py
+++ b/tests/install/test_slow_target_budget.py
@@ -58,6 +58,26 @@ def test_every_calibration_entry_is_well_formed():
         assert isinstance(c.get("date"), dt.date), c
 
 
+def test_the_lhpc_label_takes_a_tag_or_a_candidate_ref():
+    """Row A measures a release candidate before its tag exists: `release/<name> (sha7)` is true
+    provenance and well-formed, as is a tag `vX.Y.Z (sha7)` and a self-update's
+    `<from> -> <to> (sha7)`; the short SHA is mandatory in every form."""
+    def label_errors(label):
+        return [e for e in stt.entry_errors(_entry(lhpc=label)) if e.startswith("lhpc")]
+    for good in ("v0.12.0 (6ff1937)", "release/0.11.12-bundle (3d0943a1)",
+                 "v0.11.12 (3d0943a1c2b4)", "v0.11.10 -> v0.11.11 (35923b80)",
+                 "v0.11.11 -> release/0.11.12-bundle (3d0943a1)"):
+        assert label_errors(good) == [], good
+    for bad in ("release/0.11.12-bundle", "v0.12.0", "release/0.11.12-bundle (zzzzzzz)",
+                "release/ (3d0943a)", "main (3d0943a)", "v0.12 (6ff1937)", "",
+                "v0.11.10 -> v0.11.11", "main -> v0.11.11 (35923b80)"):
+        assert label_errors(bad), bad
+    assert stt.minor(_entry(lhpc="release/0.11.12-bundle (3d0943a1)")) == (0, 11)
+    assert stt.minor(_entry(lhpc="v0.12.0 (6ff1937)")) == (0, 12)
+    assert stt.minor(_entry(lhpc="release/bundle (3d0943a1)")) is None
+    assert stt.minor(_entry(lhpc="v0.10.9 -> v0.11.0 (35923b80)")) == (0, 11)
+
+
 def test_every_excluded_component_exists_and_says_why():
     known = {c.id for st in STACKS for c in st.components}
     assert set(EXCLUDED) <= known, f"excluded but not in the manifest: {set(EXCLUDED) - known}"
@@ -72,9 +92,15 @@ def test_coverage_names_every_kind_of_operation():
     assert ops == set(stt.OPS), f"ops never required: {set(stt.OPS) - ops}"
 
 
-def _check_coverage(measured, required) -> None:
+def _check_coverage(measured, required, version=__version__) -> list[str]:
+    """Green when every required pair is measured or waived (stt.waiver); returns the waivers,
+    printed by name. Excluded components are not required at all."""
     have = {(e["component"], e["op"]) for e in measured}
-    missing = [f"{c} {o}" for c, o in required if (c, o) not in have]
+    open_ = [(c, o) for c, o in required if (c, o) not in have]
+    waived = [w for w in (stt.waiver(c, o, version) for c, o in open_) if w]
+    missing = [f"{c} {o}" for c, o in open_ if not stt.waiver(c, o, version)]
+    for w in waived:
+        print(f"waived: {w}")
     boot = stt.bootstrap_reason(measured, missing)
     if boot:
         pytest.skip(boot)
@@ -82,6 +108,7 @@ def _check_coverage(measured, required) -> None:
         f"no entry in {BASELINE_FILE.name} for {len(missing)} operation(s): run row A "
         "(docs/test-matrix.md) and record them, or exclude the component with a reason:\n"
         + "\n".join(missing))
+    return waived
 
 
 def test_coverage():
@@ -104,6 +131,46 @@ def test_coverage_enforces_once_one_entry_is_measured():
     _check_coverage([_entry(component="a", op="build")], [("a", "build")])
 
 
+PIP = (stt.SELFUPDATE_COMPONENT, "selfupdate-pip")
+
+
+def test_coverage_waives_l4_up_to_the_introducing_release_and_names_it():
+    """Row A on the introducing release ran the previous release's helper: no pip sync line, on
+    the Zero or anywhere. That gap is GREEN there and before, by name; never past it."""
+    measured = [_entry(component="a", op="build")]
+    for version in (stt.PIP_SYNC_SINCE, "0.11.11"):
+        waived = _check_coverage(measured, [("a", "build"), PIP], version=version)
+        assert waived and waived[0].startswith("lhpc-selfupdate selfupdate-pip (L4)")
+    nxt = stt.PIP_SYNC_SINCE.rsplit(".", 1)
+    for version in (f"{nxt[0]}.{int(nxt[1]) + 1}", "1.0.0"):
+        with pytest.raises(AssertionError, match="lhpc-selfupdate selfupdate-pip"):
+            _check_coverage(measured, [("a", "build"), PIP], version=version)
+
+
+def test_coverage_lists_only_the_genuinely_unmeasured():
+    """An excluded component is never required; a waived pair is not listed; a measured
+    entry of an excluded component (a shared tree adopted on the Zero) is harmless."""
+    excluded = set(EXCLUDED)
+    assert not [c for c, _ in REQUIRED if c in excluded]
+    measured = [_entry(component="a", op="build"), _entry(component=next(iter(excluded)),
+                                                            op="clone")]
+    with pytest.raises(AssertionError) as red:
+        _check_coverage(measured, [("a", "build"), ("b", "clone"), PIP],
+                        version=stt.PIP_SYNC_SINCE)
+    assert [ln.strip() for ln in str(red.value).splitlines()[1:]
+            if ln.startswith("  ")] == ["b clone"]
+
+
+def test_the_introducing_release_is_a_changelog_release():
+    """PIP_SYNC_SINCE names a CHANGELOG release section once this tree has reached it: a
+    renumbered release cannot leave it pointing at nothing (and so waiving L4 for ever, or
+    never). A tree still below it (the series before its release section) cannot tell yet."""
+    changelog = (repo_paths.REPO / "CHANGELOG.md").read_text()
+    reached = stt._release(__version__) >= stt._release(stt.PIP_SYNC_SINCE)
+    assert f"\n## {stt.PIP_SYNC_SINCE}\n" in changelog or not reached
+    assert reached or stt.waiver(*PIP, __version__)
+
+
 # ---- (b) budget ----------------------------------------------------------------------------
 
 @pytest.mark.parametrize("op", sorted(stt.LIMITS))
```

### 3. 5e4a4c6 — the slow-build lane

```diff
diff --git a/testlab/tests/slowbuild/test_slow_build.py b/testlab/tests/slowbuild/test_slow_build.py
index 117bf46..91c1ebb 100644
--- a/testlab/tests/slowbuild/test_slow_build.py
+++ b/testlab/tests/slowbuild/test_slow_build.py
@@ -23,6 +23,7 @@ Run in this order, without `-x`: the budget case needs every measurement the run
 from __future__ import annotations
 
 import datetime as dt
+import json
 import os
 import re
 import subprocess
@@ -36,7 +37,7 @@ from lhpc_testlab.release import on_binary
 from lhpc_testlab.testing import LabServer, lab_env, run_lhpc
 
 from lhpc.core import slow_target as stt
-from lhpc.core.manifest import default_manifest_path, load_manifest
+from lhpc.core.manifest import default_manifest_path, load_controller, load_manifest
 from lhpc.version import __version__
 
 pytestmark = pytest.mark.slow
@@ -141,7 +142,10 @@ def _record(component: str, op: str, seconds: float, quiet_s: float | None = Non
     if quiet_s is not None:
         entry["quiet_s"] = round(quiet_s, 1)
     entry.update({"key": key, "source": "throttled-ci",
-                  "host": f"gha ubuntu-24.04-arm cpus={os.environ.get('SLOW_CPUS', '?')} mem=416m",
+                  "host": (f"gha ubuntu-24.04-arm cpus={os.environ.get('SLOW_CPUS', '?')} "
+                           f"mem={os.environ.get('SLOW_MEM', '?')} "
+                           f"wiops={os.environ.get('SLOW_WRITE_IOPS', '?')} "
+                           f"riops={os.environ.get('SLOW_READ_IOPS', '?')}"),
                   "lhpc": f"v{__version__} ({_git('-C', str(REPO), 'rev-parse', '--short', 'HEAD')})",
                   "date": dt.datetime.now(dt.UTC).date(),
                   "evidence": os.environ.get("LHPC_SLOW_BUILD_RUN_URL", "local run")})
@@ -261,11 +265,34 @@ def _env_problems() -> list[str]:
         out.append(f"cpu.max {quota} {period}: the CPU throttle to {cpus} CPUs is not in force")
     if mem == "max" or int(mem) > 416 * 2**20:
         out.append(f"memory.max {mem}: the 416 MiB memory cap is not in force")
+    out += _io_problems(cg)
     if not os.cpu_count():
         out.append("nproc unreadable")
     return out
 
 
+def _io_problems(cg: Path) -> list[str]:
+    """The disk throttle (SLOW_WRITE_IOPS / SLOW_READ_IOPS, the job's `--device-*-iops`) must be
+    in force on at least one disk of this cgroup's io.max: without it the io part of the
+    calibration — and every IO-bound step — runs at the runner's SSD speed."""
+    want = {}
+    for key, var in (("wiops", "SLOW_WRITE_IOPS"), ("riops", "SLOW_READ_IOPS")):
+        try:
+            want[key] = int(os.environ.get(var, ""))
+        except ValueError:
+            return [f"{var}={os.environ.get(var)!r}: the lane needs the disk throttle"]
+    try:
+        lines = (cg / "io.max").read_text().splitlines()
+    except OSError as exc:
+        return [f"io.max is unreadable — the disk throttle cannot be proved: {exc}"]
+    for line in lines:
+        kv = dict(f.split("=", 1) for f in line.split()[1:] if "=" in f)
+        if all(kv.get(k, "max") != "max" and int(kv[k]) <= v for k, v in want.items()):
+            return []
+    return [f"io.max {lines!r}: no disk is throttled to wiops<={want['wiops']} "
+            f"riops<={want['riops']}"]
+
+
 def test_slow_build_env():
     """The lane measures the PRODUCTION limits in a throttled box, or it measures nothing:
     every measurement refuses to record while any of this is wrong."""
@@ -289,26 +316,67 @@ def test_slow_build_stack(env, svc, stack, tmp_path):
         _cli_venv(r)
 
 
+def _box_env(root: Path, home: Path) -> dict:
+    """The lab env of `root` with its own $HOME, where the box's user units live: the lane never
+    writes the container user's (or a developer's) $HOME/.config/systemd/user. The pip cache stays
+    the user's, as on a box that installed before."""
+    cache = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
+    home.mkdir(parents=True, exist_ok=True)
+    return {**lab_env(root), "HOME": str(home), "XDG_CACHE_HOME": cache}
+
+
+def _install_units(python: Path, root: Path, env: dict) -> list[str]:
+    """The managed systemd units of a box, installed the way install.sh installs them: each unit
+    of the installed release's `updater_units.ALL_UNITS`, rendered by that release's own
+    `python -m lhpc.core.updater_units render <kind> <root> <checkout> <venv>` into
+    $HOME/.config/systemd/user. No `systemctl --user` (the container has no user manager): the
+    helper never calls it either — it is sandboxed and, after the update, only VERIFIES these
+    files (`updater_units verify-set`, file reads), which is what the lane must exercise."""
+    def py(*args: str) -> str:
+        return subprocess.run([str(python), *args], env=env, capture_output=True, text=True,
+                              check=True, timeout=600).stdout
+    kinds, (r, checkout, venv) = json.loads(py(
+        "-c", "import json, sys; from lhpc.core import updater_units as u; "
+        "print(json.dumps([u.ALL_UNITS, u.deployment_paths(sys.argv[1])]))", str(root)))
+    unit_dir = Path(env["HOME"]) / ".config" / "systemd" / "user"
+    unit_dir.mkdir(parents=True, exist_ok=True)
+    for kind in kinds:
+        (unit_dir / kind).write_text(py("-m", "lhpc.core.updater_units", "render", kind, r,
+                                        checkout, venv))
+    return kinds
+
+
 def test_slow_build_selfupdate(tmp_path):
     """L3 + L4: a one-click self-update from the previous release tag to this commit — the
     helper body (`lhpc self-update --run-service`) timed whole, its pip sync from its own line.
-    The throttle (cpus <= 1) is already tighter than the unit's CPUQuota=150%. On the release
-    that introduces the pip sync line the previous tag's helper cannot print it: L4 then has no
-    evidence, by name (L4_INTRODUCING), and the budget case says so."""
+    The runtime is laid out as a box: the checkout at <root>/src/loraham-pi-control, the venv at
+    <root>/venv/lhpc and the release's canonical units in $HOME (`_install_units`), so the
+    helper's own post-update unit verification runs as on a box instead of failing on units no
+    box lacks. The throttle (cpus <= 1) is already tighter than the unit's CPUQuota=150%. On the
+    release that introduces the pip sync line the previous tag's helper cannot print it: L4 then
+    has no evidence, by name (L4_INTRODUCING), and the budget case says so."""
     cand = _git("-C", str(REPO), "rev-parse", "HEAD")
     prev = _git("-C", str(REPO), "describe", "--tags", "--abbrev=0", "--match", "v*", f"{cand}^")
-    remote, co, venv = tmp_path / "remote.git", tmp_path / "loraham-pi-control", tmp_path / "venv"
+    root = tmp_path / "runtime"
+    LabServer(root).init_and_reset()
+    env = _box_env(root, tmp_path / "home")
+    remote = tmp_path / "remote.git"
+    co, venv = root / "src" / "loraham-pi-control", root / "venv" / "lhpc"
     _git("clone", "--quiet", "--bare", str(REPO), str(remote))
     _git("-C", str(remote), "update-ref", "refs/heads/main", cand)
     _git("clone", "--quiet", "--branch", "main", str(remote), str(co))
     _git("-C", str(co), "reset", "--quiet", "--hard", prev)
+    # A box's origin is the approved canonical remote, which the helper's controller-identity
+    # check demands of an in-root checkout; the lane serves it from the local candidate remote
+    # (git's own `insteadOf`, set in this checkout only), so `origin` reads as on a box.
+    canonical = load_controller(co / "lhpc" / "data" / "manifest.example.toml").remote
+    _git("-C", str(co), "remote", "set-url", "origin", canonical)
+    _git("-C", str(co), "config", f"url.{remote}.insteadOf", canonical)
     subprocess.run([sys.executable, "-m", "venv", str(venv)], check=True, timeout=600)
     pip = [str(venv / "bin" / "python"), "-m", "pip", "install", "-q"]
     for target in (co, co / "testlab"):          # release-1 and its own lab provider
         subprocess.run([*pip, "-e", str(target)], check=True, timeout=HARNESS_S)
-    root = tmp_path / "runtime"
-    LabServer(root).init_and_reset()
-    env = lab_env(root)
+    _install_units(venv / "bin" / "python", root, env)
     (root / "state" / "selfupdate.request").write_text("normal\n")
     seconds, out = _helper(venv / "bin" / "lhpc", env)
     assert _git("-C", str(co), "rev-parse", "HEAD") == cand, f"{prev} was not updated to {cand}"
@@ -340,21 +408,22 @@ def test_slow_build_calibrated():
 
 
 def _waived(fails: list[str], measured, intro: bool) -> tuple[list[str], str]:
-    """(the failures that stand, the bootstrap skip reason or ""). Bootstrap (no [[measured]]
-    entry yet): only the missing Zero baseline is waived, by name, and on the release that
-    introduces the pip sync line the missing L4 evidence; the evidence and the
-    twice-the-measurement rule still judge this run's own numbers. Past bootstrap nothing is."""
-    l4 = f"no row C evidence for {stt.SELFUPDATE_COMPONENT} selfupdate-pip"
+    """(the failures that stand, the bootstrap skip reason or ""). On the release that introduces
+    the pip sync line (`intro`: the previous tag's helper has none AND `stt.waiver` holds for this
+    version) nobody can measure L4, here or on the Zero: its missing evidence and its missing Zero
+    baseline are waived, by name, in and past bootstrap — the same waiver the coverage test
+    applies. Bootstrap (no [[measured]] entry yet): every missing Zero baseline is waived too, by
+    name. The evidence and the twice-the-measurement rule still judge this run's own numbers."""
+    l4 = f"{stt.SELFUPDATE_COMPONENT} selfupdate-pip"
+    if intro:
+        fails = [f for f in fails if not f.startswith((f"no row C evidence for {l4}",
+                                                       f"no Zero baseline for {l4}"))]
     unmeasured = [f.removeprefix("no Zero baseline for ").removesuffix(": run row A")
                   for f in fails if f.startswith("no Zero baseline for ")]
     boot = stt.bootstrap_reason(measured, unmeasured)
     if boot:
-        fails = [f for f in fails if not f.startswith("no Zero baseline for ")
-                 and not (intro and f.startswith(l4))]
+        fails = [f for f in fails if not f.startswith("no Zero baseline for ")]
         boot += f"; {L4_INTRODUCING}" if intro else ""
-    elif intro:
-        fails = [f"{f} — {L4_INTRODUCING}, which only the bootstrap state waives"
-                 if f.startswith(l4) else f for f in fails]
     return fails, boot
 
 
@@ -377,7 +446,8 @@ def test_slow_build_budget():
         lines.append(line)
     # The release introducing the pip sync line has no L4 evidence (docs/maintenance.md).
     l4 = f"no row C evidence for {stt.SELFUPDATE_COMPONENT} selfupdate-pip"
-    intro = bool(INTRODUCING) and any(f.startswith(l4) for f in fails)
+    intro = (bool(INTRODUCING) and any(f.startswith(l4) for f in fails)
+             and bool(stt.waiver(stt.SELFUPDATE_COMPONENT, "selfupdate-pip", __version__)))
     OUT.mkdir(parents=True, exist_ok=True)
     (OUT / "slow-build-summary.md").write_text(
         "### slow-build: E (row C) vs Z (Zero baseline)\n\n"
diff --git a/testlab/tests/unit/test_slow_build_lane.py b/testlab/tests/unit/test_slow_build_lane.py
index c677272..0dc93bc 100644
--- a/testlab/tests/unit/test_slow_build_lane.py
+++ b/testlab/tests/unit/test_slow_build_lane.py
@@ -112,10 +112,32 @@ def test_missing_l4_evidence_fails_when_not_the_introducing_release():
     assert fails == [L4] and lane.L4_INTRODUCING not in boot
 
 
-def test_past_bootstrap_the_introducing_release_still_fails_l4():
-    fails, boot = lane._waived([L4], [{"op": "build"}], intro=True)
-    assert boot == "" and len(fails) == 1
-    assert fails[0].startswith(L4) and lane.L4_INTRODUCING in fails[0]
+NO_Z_L4 = NO_Z     # the L4 pair's missing Zero baseline
+
+
+def test_past_bootstrap_the_introducing_release_waives_l4_only():
+    """Row A on the introducing release ran the previous tag's helper too: no L4 on the Zero
+    either. Past bootstrap the L4 pair is waived (named in the summary as NO EVIDENCE), every
+    other failure stands."""
+    other_z = "no Zero baseline for kiss build: run row A"
+    fails, boot = lane._waived([L4, NO_Z_L4, OTHER, other_z], [{"op": "build"}], intro=True)
+    assert boot == "" and fails == [OTHER, other_z]
+
+
+def test_the_l4_waiver_needs_the_version_rule_too(monkeypatch, tmp_path):
+    """A previous tag without the line is not enough: past PIP_SYNC_SINCE the L4 failures stand
+    (a lost line is a defect, not the introducing release)."""
+    monkeypatch.setattr(lane, "OUT", tmp_path)
+    monkeypatch.setattr(lane, "INTRODUCING", ["v9.9.9"])
+    monkeypatch.setattr(lane, "EVIDENCE", {})
+    monkeypatch.setattr(lane, "LANE_OPS", [(lane.stt.SELFUPDATE_COMPONENT, "selfupdate-pip")])
+    monkeypatch.setattr(lane, "BASELINE", {"measured": [{"op": "build"}]})
+    monkeypatch.setattr(lane, "__version__", "99.0.0")
+    with pytest.raises(AssertionError, match="no row C evidence for lhpc-selfupdate"):
+        lane.test_slow_build_budget()
+    monkeypatch.setattr(lane, "__version__", lane.stt.PIP_SYNC_SINCE)
+    lane.test_slow_build_budget()
+    assert "**NO EVIDENCE**" in (tmp_path / "slow-build-summary.md").read_text()
 
 
 # ---- a step faster than the log's resolution (testlab run 37145791526) ----------------------
@@ -169,3 +191,65 @@ def test_the_helper_runs_past_the_unit_plumbing_guard(tmp_path):
     env = {k: v for k, v in lane.os.environ.items() if k != "INVOCATION_ID"}
     _, out = lane._helper(fake, env)
     assert lane._PIP_SYNC.findall(out) == ["4.2"]
+
+
+# ---- the helper's runtime carries a box's canonical units (testlab run 37148794385) ---------
+
+def _verify_set(root, env):
+    """The helper's own post-update unit check, as it runs it (service_selfupdate)."""
+    import sys
+    return subprocess.run([sys.executable, "-m", "lhpc.core.updater_units", "verify-set",
+                           str(root)], env=env, capture_output=True, text=True, timeout=60,
+                          check=False)
+
+
+def test_the_helper_runtime_has_the_units_its_verification_requires(tmp_path):
+    """Run 37148794385: the helper applied the update, then exited 1 — "units not canonical:
+    lhpc-boot-restore.service: missing; …" — because the lane's runtime had no units at all. The
+    lane now installs them as install.sh does, into the runtime's own $HOME, and the helper's
+    verification passes on them."""
+    import sys
+    root = tmp_path / "runtime"
+    (root / "src").mkdir(parents=True)
+    env = lane._box_env(root, tmp_path / "home")
+    assert env["HOME"] == str(tmp_path / "home") and env["LHPC_RUNTIME_ROOT"] == str(root)
+    before = _verify_set(root, env)
+    assert before.returncode == 1 and "lhpc-boot-restore.service: missing" in before.stdout
+    kinds = lane._install_units(Path(sys.executable), root, env)
+    from lhpc.core import updater_units
+    assert tuple(kinds) == updater_units.ALL_UNITS
+    after = _verify_set(root, env)
+    assert (after.returncode, after.stdout) == (0, "ok\n"), after.stdout + after.stderr
+    unit = (tmp_path / "home" / ".config/systemd/user" / updater_units.HELPER_UNIT).read_text()
+    assert f"{root}/venv/lhpc/bin/lhpc " in unit      # the box layout the lane runs the helper in
+
+
+def test_the_lane_never_writes_the_real_home_units(tmp_path):
+    env = lane._box_env(tmp_path / "runtime", tmp_path / "home")
+    assert env["HOME"] != str(Path.home())
+    assert env["XDG_CACHE_HOME"]                       # the pip cache stays the user's
+
+
+# ---- the disk throttle is part of the throttled box (Correction 7, GAP 3) -------------------
+
+@pytest.mark.parametrize("io_max, ok", [
+    ("8:0 rbps=max wbps=max riops=300 wiops=120\n", True),
+    ("8:0 rbps=max wbps=max riops=200 wiops=100\n", True),        # tighter is fine
+    ("8:0 rbps=max wbps=max riops=max wiops=120\n", False),       # reads unthrottled
+    ("8:0 rbps=max wbps=max riops=300 wiops=500\n", False),       # looser than the job's
+    ("", False),                                                  # no throttle at all
+])
+def test_the_lane_proves_the_disk_throttle(tmp_path, monkeypatch, io_max, ok):
+    monkeypatch.setenv("SLOW_WRITE_IOPS", "120")
+    monkeypatch.setenv("SLOW_READ_IOPS", "300")
+    (tmp_path / "io.max").write_text(io_max)
+    assert (lane._io_problems(tmp_path) == []) is ok
+
+
+def test_the_disk_throttle_must_be_named(tmp_path, monkeypatch):
+    monkeypatch.delenv("SLOW_WRITE_IOPS", raising=False)
+    (tmp_path / "io.max").write_text("8:0 riops=1 wiops=1\n")
+    assert lane._io_problems(tmp_path)[0].startswith("SLOW_WRITE_IOPS=None")
+    monkeypatch.setenv("SLOW_WRITE_IOPS", "1")
+    monkeypatch.setenv("SLOW_READ_IOPS", "1")
+    assert lane._io_problems(tmp_path / "nowhere")[0].startswith("io.max is unreadable")
```

### 4. 3e5d3cd — the slow-build job

```diff
diff --git a/.github/workflows/testlab.yml b/.github/workflows/testlab.yml
index b5d3e87..d75abdf 100644
--- a/.github/workflows/testlab.yml
+++ b/.github/workflows/testlab.yml
@@ -209,15 +209,35 @@ jobs:
   slow-build:
     # Row C of the slow-target build proof (docs/maintenance.md, plans/PLAN-F43.md): every
     # operation on the update path, run and timed under the PRODUCTION limits inside a container
-    # throttled below a Pi Zero 2 W, then judged against tests/data/slow-target-builds.toml.
+    # throttled to a Pi Zero 2 W, then judged against tests/data/slow-target-builds.toml.
     # The same triggers as release-verify, so every release candidate, bot ones included.
-    # SLOW_CPUS starts at 0.5 and only ever goes DOWN: test_slow_build_calibrated turns this job
-    # red while the container is faster than the Zero on the fixed calibration workload.
+    # test_slow_build_calibrated turns this job red while the container is faster than the Zero
+    # on ANY part (cpu, io, mem) of the fixed calibration workload: then tighten that part's
+    # throttle below; a throttle only ever gets tighter.
     if: ${{ (github.event_name == 'push' && github.ref == 'refs/heads/main') || inputs.release_verify }}
     runs-on: ubuntu-24.04-arm
     timeout-minutes: 330
     env:
-      SLOW_CPUS: "0.5"
+      # The throttle. Measured: the Zero's calibration (row A, 2026-10-03) is cpu 99.7 s,
+      # io 489.8 s, mem 110.1 s; this container at --cpus 0.5, --memory 416m and no disk
+      # throttle took cpu 57.8, io 31.4, mem 12.2 (testlab run 37148794385): faster on all three.
+      # CPU — the cpu part is CPU-time bound and scales with the quota: 0.5 x 57.8 / 99.7 =
+      # 0.29 CPUs matches the Zero; 0.25 leaves ~15 %.
+      SLOW_CPUS: "0.25"
+      # Memory — what a Zero 2 W leaves free; the rest of the 700 MB mem part swaps (the Zero
+      # swaps into zram). --memory-swap is memory + 768 MiB swap.
+      SLOW_MEM: "416m"
+      SLOW_SWAP: "1184m"
+      # Disk — on every disk behind Docker's storage and swap. The io part is 65536 fsync'd
+      # 4 KiB files: 489.8 s on the Zero's SD card, ~134 synced files/s. On a journaled ext4
+      # each synced file costs ~1.2 writes charged to the container (measured), so 120 write
+      # IOPS gives ~100 files/s, ~650 s. Reads also pace the mem part, whose swap-ins hit
+      # the disk (the Zero swaps into zram instead): measured 608 s at 300 read IOPS, 186 s at
+      # 1000, 97 s at 2000; 1200 keeps it above the Zero's 110 s. These are local measurements
+      # (x86, cgroup v1, Correction 7 of code-review/code-report-F43.md); the calibration case
+      # of every run is the proof on the runner.
+      SLOW_WRITE_IOPS: "120"
+      SLOW_READ_IOPS: "1200"
     steps:
       - name: Check out
         uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
@@ -232,8 +252,36 @@ jobs:
           docker build -t "$IMG" -f .devcontainer/Dockerfile .devcontainer
           echo "IMG=$IMG" >> "$GITHUB_ENV"
 
+      # io.max takes whole disks: every disk behind Docker's storage (the container's root and
+      # its /tmp volume) and behind each active swap area gets the IOPS limits. No disk found is
+      # a failure, never an unthrottled run.
+      - name: Resolve the disks to throttle
+        run: |
+          disk_of() {
+            local mm d
+            if [ -b "$1" ]; then mm=$(lsblk -dno MAJ:MIN "$1" | tr -d ' ')
+            else mm=$(findmnt -n -o MAJ:MIN --target "$1" | head -1 | tr -d ' '); fi
+            [ -n "$mm" ] && [ -e "/sys/dev/block/$mm" ] || return 0
+            d=$(readlink -f "/sys/dev/block/$mm")
+            [ -e "$d/partition" ] && d=$(dirname "$d")
+            echo "/dev/$(basename "$d")"
+          }
+          cat /proc/swaps
+          lsblk
+          disks=$( { disk_of "$(docker info -f '{{.DockerRootDir}}')"
+                     tail -n +2 /proc/swaps | while read -r swap _; do disk_of "$swap"; done
+                   } | sort -u)
+          [ -n "$disks" ] || { echo "::error::no disk behind Docker's storage resolved"; exit 1; }
+          flags=
+          for d in $disks; do
+            test -b "$d" || { echo "::error::$d is not a block device"; exit 1; }
+            flags="$flags --device-write-iops $d:$SLOW_WRITE_IOPS --device-read-iops $d:$SLOW_READ_IOPS"
+          done
+          echo "throttled disks: $disks"
+          echo "SLOW_IO_FLAGS=$flags" >> "$GITHUB_ENV"
+
       # A heredoc lane script, as in release-verify. The container gets the throttle; the lane
-      # itself checks that cpu.max and memory.max are in force and fails if they are not.
+      # itself checks that cpu.max, memory.max and io.max are in force and fails if they are not.
       - name: Slow-target build
         run: |
           mkdir -p slowevidence
@@ -251,16 +299,17 @@ jobs:
             python -m pytest testlab/tests/slowbuild -q -rs \
               --junitxml=/out/junit-slow-build.xml
           LANE
+          # shellcheck disable=SC2086  # SLOW_IO_FLAGS is a word list by design
           docker run --rm --user root \
-            --cpus="$SLOW_CPUS" --memory=416m --memory-swap=1184m \
-            -e SLOW_CPUS \
+            --cpus="$SLOW_CPUS" --memory="$SLOW_MEM" --memory-swap="$SLOW_SWAP" $SLOW_IO_FLAGS \
+            -e SLOW_CPUS -e SLOW_MEM -e SLOW_WRITE_IOPS -e SLOW_READ_IOPS \
             -e LHPC_SLOW_BUILD_RUN_URL="$GITHUB_SERVER_URL/$GITHUB_REPOSITORY/actions/runs/$GITHUB_RUN_ID" \
             -v "$PWD":/repo:ro -v lhpc_slow_tmp:/tmp -v "$PWD/slowevidence":/out \
             "$IMG" bash -lc '
               set -e
               cp -r /repo /tmp/repo && chown -R lhpclab /tmp/repo && chmod a+w /out
               install -m 0755 /repo/lane.sh /tmp/lane.sh
-              su lhpclab -c "SLOW_CPUS=$SLOW_CPUS LHPC_SLOW_BUILD_RUN_URL=$LHPC_SLOW_BUILD_RUN_URL /tmp/lane.sh"
+              su lhpclab -c "SLOW_CPUS=$SLOW_CPUS SLOW_MEM=$SLOW_MEM SLOW_WRITE_IOPS=$SLOW_WRITE_IOPS SLOW_READ_IOPS=$SLOW_READ_IOPS LHPC_SLOW_BUILD_RUN_URL=$LHPC_SLOW_BUILD_RUN_URL /tmp/lane.sh"
             '
           test -s slowevidence/junit-slow-build.xml \
             || { echo "::error::the lane produced no JUnit — it did not run"; exit 1; }
diff --git a/tests/repo/test_slow_build_gate.py b/tests/repo/test_slow_build_gate.py
index e5a20b8..c3b278a 100644
--- a/tests/repo/test_slow_build_gate.py
+++ b/tests/repo/test_slow_build_gate.py
@@ -91,3 +91,37 @@ def test_a_failed_or_missing_row_c_case_is_refused(tmp_path):
 def test_a_fully_passed_run_is_accepted(tmp_path):
     r = _run(tmp_path, _row_c() + _judging(), MEASURED)
     assert r.returncode == 0 and "::warning::" not in r.stdout, r.stdout + r.stderr
+
+
+# ---- the throttle (Correction 7): at least as slow as the Zero on every axis -----------------
+
+def _job_env() -> dict[str, str]:
+    text = WORKFLOW.read_text()
+    job = text[text.index("\n  slow-build:\n"):]
+    block = re.search(r"^    env:\n(.*?)^    steps:", job, re.MULTILINE | re.DOTALL)
+    assert block, "the slow-build job has no env block"
+    return dict(re.findall(r'^      (SLOW_\w+): "([^"]*)"', block.group(1), re.MULTILINE))
+
+
+def test_the_throttle_is_named_and_reaches_the_container_and_the_lane():
+    """Every axis is a named constant, applied by `docker run` and handed to the lane, whose
+    `test_slow_build_env` proves each one is in force (cpu.max, memory.max, io.max)."""
+    env = _job_env()
+    assert set(env) == {"SLOW_CPUS", "SLOW_MEM", "SLOW_SWAP", "SLOW_WRITE_IOPS",
+                        "SLOW_READ_IOPS"}, env
+    text = WORKFLOW.read_text()
+    run = text[text.index("docker run --rm --user root"):text.index("test -s slowevidence")]
+    for flag in ('--cpus="$SLOW_CPUS"', '--memory="$SLOW_MEM"', '--memory-swap="$SLOW_SWAP"',
+                 "$SLOW_IO_FLAGS", "-e SLOW_CPUS -e SLOW_MEM -e SLOW_WRITE_IOPS -e SLOW_READ_IOPS",
+                 "SLOW_WRITE_IOPS=$SLOW_WRITE_IOPS SLOW_READ_IOPS=$SLOW_READ_IOPS"):
+        assert flag in run, flag
+    assert '--device-write-iops $d:$SLOW_WRITE_IOPS --device-read-iops $d:$SLOW_READ_IOPS' in text
+    assert 'echo "SLOW_IO_FLAGS=$flags" >> "$GITHUB_ENV"' in text
+
+
+def test_the_cpu_quota_is_below_the_zero_breakeven():
+    """Row C at 0.5 CPUs took 57.8 s on the cpu part, the Zero 99.7 s (testlab run 37148794385,
+    row A 2026-10-03); the part is CPU-time bound, so the Zero's quota is 0.5 x 57.8 / 99.7. A
+    quota above it is a container faster than the Zero by construction."""
+    assert 0 < float(_job_env()["SLOW_CPUS"]) <= 0.5 * 57.8 / 99.7
+    assert int(_job_env()["SLOW_WRITE_IOPS"]) <= 134    # the Zero's 65536 synced files / 489.8 s
```

### 5. 4a9c743 — the release-policy sentence, CHANGELOG and docs

```diff
diff --git a/CHANGELOG.md b/CHANGELOG.md
index 94110ab..ebf932e 100644
--- a/CHANGELOG.md
+++ b/CHANGELOG.md
@@ -2,9 +2,10 @@
 
 ## 0.11.12
 
-- Every release is now also installed, built and self-updated on a test box slowed down below a Pi Zero 2 W
-  before it ships: an update that would stall or run into a time limit on a slow box turns the release check
-  red instead of reaching yours.
+- Every release is now also installed, built and self-updated on a test box throttled to a Pi Zero 2 W's CPU,
+  SD card and memory before it ships, and each run proves it is no faster than a real Zero on a fixed workload:
+  an update that would stall or run into a time limit on a slow box turns the release check red instead of
+  reaching yours.
 
 ## 0.11.11
 
diff --git a/docs/maintenance.md b/docs/maintenance.md
index fd6b52a..9861c08 100644
--- a/docs/maintenance.md
+++ b/docs/maintenance.md
@@ -161,8 +161,11 @@ repositories.
   on**: that release's self-update runs the previous tag's helper, which cannot print the line,
   in row C and in row A alike. The lane detects it (the previous tag's
   `lhpc/core/service_selfupdate.py` lacks the line), still times the whole helper (L3), and names
-  the gap in the bootstrap skip reason and the job summary; the gap is waived only in the
-  bootstrap state, so the first row A is taken on the release after the introducing one.
+  the gap in the job summary (and the bootstrap skip reason). The gap is waived, by name, up to
+  and including that release (`lhpc.core.slow_target.PIP_SYNC_SINCE`, 0.11.12): the coverage
+  test reports the L4 pair as waived, not missing, and the lane's budget case waives its missing
+  evidence and Zero baseline when the previous tag lacks the line too. From the next release on
+  both require it.
 - **Every release is followed by an image.** `loraham-images` is tagged with the same version once
   the binaries a moved pin needs are published ([binary channel](provenance.md#the-binary-channel)).
 - **The release bot** runs the pin patch (watch, repin, binaries, proof, release, image) and opens
diff --git a/docs/testlab.md b/docs/testlab.md
index e6532b2..3809f0c 100644
--- a/docs/testlab.md
+++ b/docs/testlab.md
@@ -102,7 +102,8 @@ pytest -q                                      # default lane (lab lanes skip)
 LHPC_ACCEPTANCE=1 pytest testlab/tests/acceptance -q   # real server + real executable
 LHPC_BROWSER=1 pytest testlab/tests/browser -q     # headless Chromium (pip install -e ./testlab[browser])
 LHPC_RELEASE_VERIFY=1 pytest testlab/tests/release -q -x  # release lane: every stack installed, built, started
-LHPC_SLOW_BUILD=1 SLOW_CPUS=0.5 pytest testlab/tests/slowbuild -q  # slow-build lane: only inside the throttled container
+LHPC_SLOW_BUILD=1 SLOW_CPUS=0.25 SLOW_MEM=416m SLOW_WRITE_IOPS=120 SLOW_READ_IOPS=1200 \
+  pytest testlab/tests/slowbuild -q             # slow-build lane: only inside the throttled container
 ```
 
 **From a worktree, prefix these with `PYTHONPATH=$PWD`** (or re-run
@@ -141,16 +142,19 @@ subprocesses import `lhpc` from the editable install, i.e. whichever checkout wa
   - In CI it is the `release-verify` job ([when it runs](maintenance.md#what-ci-enforces)). It
     uploads `junit-release.xml`, the recorded versions and the lab's own logs.
 - **slow-build** is row C of the [slow-target build row](maintenance.md#branches-and-releases):
-  the CI job `slow-build` runs it inside a container throttled below a Pi Zero 2 W
-  (`--cpus=$SLOW_CPUS --memory=416m --memory-swap=1184m`). Under the production limits it times
+  the CI job `slow-build` runs it inside a container throttled to a Pi Zero 2 W's CPU, disk and
+  memory (`--cpus=$SLOW_CPUS --memory=$SLOW_MEM --memory-swap=$SLOW_SWAP` and
+  `--device-write-iops`/`--device-read-iops` on every disk behind Docker's storage and swap;
+  the numbers and their measured rationale are the job's `env`). Under the production limits it times
   each lane stack's install (clone, checkout, the Meshtastic CLI venv), every component's build
   (wall time and longest quiet gap), the graywolf upstream fetch and a self-update from the
   previous release tag, then checks them with `lhpc.core.slow_target` against
   `tests/data/slow-target-builds.toml`.
   - `test_slow_build_env` fails unless the throttle is in force and no `LHPC_BUILD_*` override is
     set; nothing is recorded otherwise. `test_slow_build_calibrated` runs
-    `testlab/slowbuild/calibrate.sh` and fails while the container is faster than the Zero on the
-    same workload (then lower `SLOW_CPUS`; it never goes up). `test_slow_build_budget` names every
+    `testlab/slowbuild/calibrate.sh` and fails while the container is faster than the Zero on any
+    part (cpu, io, mem) of the same workload (then tighten that part's throttle; a throttle never
+    loosens). `test_slow_build_budget` names every
     operation over budget or without evidence.
   - The job fails unless those three cases, every stack case and the self-update case PASSED
     (a skip is not a pass; the one exception is the
```
