# Gate 1 — code review request, F43, Correction 5

**Request.** Judge ONLY one commit, e6fccb2 "F43: the slow-build lane — row C measured under the
production limits". It is the earlier lane commit 8c7f131 with two fixes amended in. The rest of
that commit is the version already judged. The diff below is exactly the amendment. Judge four
things:

1. **The floor for fast steps.** A timing line printed as `0.0 s`, below the log's one-decimal
   resolution, now becomes one evidence entry at the floor `0.1 s` with
   `note = "below log resolution"`. Check that the stack's measurement goes on after it, that
   the validator still rejects a zero for every op, and that the floor cannot turn a slow
   operation into a PASS.
2. **The self-update helper.** The lane's helper step now runs
   `lhpc self-update --run-service` with `INVOCATION_ID` set, the way its systemd unit runs it.
   Check that only that subprocess gets the marker.
3. **The tests.** Check that the three new tests prove 1 and 2 and would have caught both
   defects.
4. **Nothing else changed.** Check that nothing else in the lane changed: the list of measured
   operations (`LANE_OPS`), the L4 waiver and the budget case.

The ten other commits of the branch keep their patch-ids and are not under review.

Answer in the form `| commit | verdict (OK / FINDING) | what |`, then give one final line: GREEN /
GREEN WITH NOTES / RED. A finding names the line in the diff and what goes wrong.

This file is your whole input: you have no repository access; use no connector, tool or web lookup.

## Why the commit was amended

The first real run of the CI job `slow-build` failed for two reasons, both in the lane's own code.

1. **Fast checkouts aborted every source stack.** The product's adoption log writes
   `[git] checkout <ref> 0.0 s` for a checkout under 50 ms. The lane stored `seconds = 0.0`, and
   the entry validator rejected it with "seconds must be a positive number". The stack's case
   stopped at its first checkout, so no build was measured for five stacks. The budget case then
   failed with "no row C evidence for …" for each of them.
2. **The self-update helper was always refused.** It ran without `INVOCATION_ID`, so the
   product's unit-plumbing guard refused it with rc 2 on every tree. The two self-update
   operations never got evidence.

## Context (unchanged code the amendment relies on)

The product's timing line (`lhpc/core/install.py`, unchanged; it is the operator's log):

```python
                log_fh.write(f"\n[git] {what} {time.monotonic() - t0:.1f} s\n")
```

The guard (`lhpc/adapters/cli/main.py`, unchanged):

```python
def _unit_plumbing_refusal(flag: str, unit: str, use: str):
    """Hidden unit plumbing runs from its systemd unit. It is refused when the systemd invocation
    marker (`INVOCATION_ID`) is absent, e.g. a hand-run in a shell. The marker is an indicator, not an
    authentication boundary. Returns the exit code 2 when refused, else None."""
    if os.environ.get("INVOCATION_ID"):
        return None
    print(f"ERR   {flag} is unit plumbing, meant to run from {unit}; refused because the systemd "
          f"invocation marker (INVOCATION_ID) is absent. Use {use} instead.")
    return 2
```

The lab environment every other lane step uses (`testlab/lhpc_testlab/testing.py`, unchanged):

```python
def lab_env(root: Path) -> dict:
    env = dict(os.environ)
    env["LHPC_RUNTIME_ROOT"] = str(root)
    env["LHPC_TESTLAB"] = "1"
    env["LHPC_SYSTEM_PROVIDER"] = "lhpc_testlab.provider:build"
    env["LHPC_BOOT_ID_FILE"] = str(root / "state" / "testlab" / "host" / "boot_id")
    env["LHPC_FW_PATH_PREFIX"] = str(root / "state" / "testlab" / "host")
    env.pop("INVOCATION_ID", None)
    return env
```

The entry validator (`lhpc/core/slow_target.py`, unchanged; the seconds rule):

```python
    sec = entry.get("seconds")
    if isinstance(sec, bool) or not isinstance(sec, (int, float)) or sec <= 0:
        errs.append("seconds must be a positive number")
```

It does not check for unknown keys, so `note` passes. The budget rule compares `seconds` with the
limit and with twice the Zero measurement, and ignores `note`.

The lane's reader of the adoption log (in the commit, unchanged by the amendment):

```python
def _adoption(env: dict, component: str) -> None:
    for cid in stt.tree_components(STACKS, component):
        log = _logs(env) / f"adopt-{cid}.log"
        text = log.read_text(errors="replace") if log.is_file() else ""
        clone = _CLONE.findall(text)
        if not clone:
            continue
        _reject(log.name, text)
        checkout = _CHECKOUT.findall(text)
        assert checkout, f"{log.name} has no `[git] checkout <ref> <n> s` line — not evidence"
        _record(component, "clone", max(map(float, clone)))
        _record(component, "checkout", max(map(float, checkout)))
        return
```

`_record` is also called for build (a `time.monotonic()` duration plus `quiet_s`), deb-fetch,
cli-venv (from `[venv] <n> s` lines), selfupdate-helper (a `time.monotonic()` duration) and
selfupdate-pip (from `[selfupdate] pip sync <n> s`). After the env check and the entry key, it
asserts `not stt.entry_errors(entry)`.

## The amendment (8c7f131 → e6fccb2)

```diff
diff --git a/testlab/tests/slowbuild/test_slow_build.py b/testlab/tests/slowbuild/test_slow_build.py
index 7f83626..117bf46 100644
--- a/testlab/tests/slowbuild/test_slow_build.py
+++ b/testlab/tests/slowbuild/test_slow_build.py
@@ -63,6 +63,10 @@ FETCHED: dict[str, str] = {}
 INTRODUCING: list[str] = []      # the previous tag, when its helper has no pip sync line
 
 _SECS = r"(\d+(?:\.\d+)?) s"
+# The timing lines print one decimal (`.1f`): a step faster than 50 ms reads `0.0 s`. That is
+# valid evidence of a fast step, recorded at this floor with a note, never as zero.
+LOG_RESOLUTION_S = 0.1
+BELOW_RESOLUTION = "below log resolution"
 # Rejection markers, wherever a step prints them: `[stalled]`, `[timeout]`, `[fail]` and their
 # longer forms (`[failed]`, the job log's `[TIMED OUT after …]`).
 _BAD = re.compile(r"^[ \t]*\[(?:stalled|timeout|timed out|fail|failed)\b[^\]\n]*\]",
@@ -132,6 +136,8 @@ def _record(component: str, op: str, seconds: float, quiet_s: float | None = Non
     key = stt.current_key(component, op, STACKS, PYPROJECT, fetched=FETCHED.get(component, ""))
     assert key, f"no entry key for {component} {op} on this tree"
     entry = {"component": component, "op": op, "seconds": round(seconds, 1)}
+    if entry["seconds"] < LOG_RESOLUTION_S:
+        entry.update(seconds=LOG_RESOLUTION_S, note=BELOW_RESOLUTION)
     if quiet_s is not None:
         entry["quiet_s"] = round(quiet_s, 1)
     entry.update({"key": key, "source": "throttled-ci",
@@ -216,9 +222,12 @@ def _cli_venv(r) -> None:
 
 
 def _helper(lhpc: Path, env: dict) -> tuple[float, str]:
-    """The self-update helper body, timed whole: (seconds, stdout + stderr)."""
+    """The self-update helper body, timed whole: (seconds, stdout + stderr). It runs the way
+    its systemd unit runs it: `--run-service` is unit plumbing, refused (rc 2) without the
+    systemd invocation marker `INVOCATION_ID`."""
     t0 = time.monotonic()
-    r = subprocess.run([str(lhpc), "self-update", "--run-service"], env=env,
+    r = subprocess.run([str(lhpc), "self-update", "--run-service"],
+                       env={**env, "INVOCATION_ID": "slow-build-lane"},
                        capture_output=True, text=True, timeout=HARNESS_S, check=False)
     seconds = time.monotonic() - t0
     return seconds, _judged("the self-update helper", r)
diff --git a/testlab/tests/unit/test_slow_build_lane.py b/testlab/tests/unit/test_slow_build_lane.py
index 30df735..afc6d75 100644
--- a/testlab/tests/unit/test_slow_build_lane.py
+++ b/testlab/tests/unit/test_slow_build_lane.py
@@ -135,3 +135,56 @@ def test_past_bootstrap_the_introducing_release_still_fails_l4():
     fails, boot = lane._waived([L4], [{"op": "build"}], intro=True)
     assert boot == "" and len(fails) == 1
     assert fails[0].startswith(L4) and lane.L4_INTRODUCING in fails[0]
+
+
+# ---- a step faster than the log's resolution (testlab run 37145791526) ----------------------
+
+def test_a_checkout_below_log_resolution_is_evidence_at_the_floor(tmp_path, monkeypatch):
+    """`[git] checkout <ref> 0.0 s` (a sub-50 ms checkout, `.1f`) is a fast step, not a broken
+    one: one entry at the floor with its note, and the stack's measurement goes on."""
+    monkeypatch.setattr(lane, "_env_problems", list)
+    monkeypatch.setattr(lane, "_write", lambda: None)
+    monkeypatch.setattr(lane, "EVIDENCE", {})
+    component = next(c for c, o in lane.LANE_OPS if o == "clone")
+    adopter = lane.stt.tree_components(lane.STACKS, component)[0]
+    (tmp_path / "logs").mkdir()
+    (tmp_path / "logs" / f"adopt-{adopter}.log").write_text(
+        "[git] clone 12.3 s\n[git] checkout v1.2.3 0.0 s\n")
+    lane._adoption({"LHPC_RUNTIME_ROOT": str(tmp_path)}, component)
+    checkout = lane.EVIDENCE[(component, "checkout")]
+    assert checkout["seconds"] == lane.LOG_RESOLUTION_S == 0.1
+    assert checkout["note"] == lane.BELOW_RESOLUTION == "below log resolution"
+    assert lane.stt.entry_errors(checkout) == []
+    clone = lane.EVIDENCE[(component, "clone")]
+    assert clone["seconds"] == 12.3 and "note" not in clone
+
+
+def test_the_validator_still_rejects_a_zero():
+    floor = {"component": "c", "op": "checkout", "key": "pin:abc", "source": "throttled-ci",
+             "host": "h", "lhpc": "v0.11.12 (abc1234)", "date": lane.dt.date(2026, 10, 3),
+             "evidence": "e", "note": lane.BELOW_RESOLUTION}
+    assert lane.stt.entry_errors({**floor, "seconds": lane.LOG_RESOLUTION_S}) == []
+    for op in ("checkout", "clone", "build"):
+        assert "seconds must be a positive number" in lane.stt.entry_errors(
+            {**floor, "op": op, "seconds": 0.0})
+
+
+# ---- the self-update helper runs as its systemd unit does -----------------------------------
+
+def test_the_helper_runs_past_the_unit_plumbing_guard(tmp_path):
+    """`lhpc self-update --run-service` refuses (rc 2) without INVOCATION_ID; the lane's helper
+    step must run it as systemd does and get the helper's own evidence line."""
+    import sys
+    fake = tmp_path / "lhpc"
+    fake.write_text(
+        f"#!{sys.executable}\n"
+        "import sys\n"
+        "from lhpc.adapters.cli.main import _unit_plumbing_refusal\n"
+        "rc = _unit_plumbing_refusal('--run-service', 'lhpc-selfupdate.service', 'x')\n"
+        "if rc is None:\n"
+        f"    print('{lane._PIP_SYNC_MARK} 4.2 s')\n"
+        "sys.exit(rc or 0)\n")
+    fake.chmod(0o755)
+    env = {k: v for k, v in lane.os.environ.items() if k != "INVOCATION_ID"}
+    _, out = lane._helper(fake, env)
+    assert lane._PIP_SYNC.findall(out) == ["4.2"]
```

## What the author ran

- **Red before.** The three new tests against the lane module without the amendment: 3 failed.
  - floor test: `['seconds must be a positive number']`.
  - helper test: `failed (rc 2) … refused because the systemd invocation marker (INVOCATION_ID)
    is absent`.
  - validator test: the constant did not exist yet.

  After the amendment: 30 passed.
- **The lane end to end.** The real lane module, outside the throttled container, which was not
  available. Only the env check was lifted, so none of it is evidence. Every source stack's real
  install and build ran. Ten real `[git] checkout <ref> 0.0 s` lines were each recorded at 0.1
  with the note, and those stacks went on to build. The real self-update helper ran past the
  guard and updated the previous tag's clone to the candidate.
- **Result:** 7 passed and 1 bootstrap skip. Three cases failed, only for the environment: no
  GitHub API through the proxy (graywolf deb-fetch), an x86 box with no aarch64 binary channel
  (the Meshtastic CLI venv), and the budget case naming exactly those two operations.
- **Not claimed:** a green CI lane run.
