"""The slow-build lane's verdict on its own steps (plans/PLAN-F43.md §7), without the throttled
box: every path the lane runs a step on fails on a rejection marker, on stdout or stderr, and on
a non-zero exit status; and the budget case's waivers hold only in the bootstrap state.

The lane module itself is opt-in (`LHPC_SLOW_BUILD=1`); it is loaded here by path so its step
helpers run in the ordinary unit lane."""
from __future__ import annotations

import importlib.util
import subprocess
import types
from pathlib import Path

import pytest

from lhpc.core.jobs import run_job
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import CommandResult

_LANE = Path(__file__).resolve().parents[1] / "slowbuild" / "test_slow_build.py"
_spec = importlib.util.spec_from_file_location("slow_build_lane", _LANE)
lane = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lane)

MARKER = "[timeout] step ran past its limit"
GRAYWOLF = next(c for c, o in lane.LANE_OPS if o == "deb-fetch")


def _done(rc=0, stdout="ok\n", stderr=""):
    return subprocess.CompletedProcess(["x"], rc, stdout, stderr)


@pytest.fixture
def step(monkeypatch):
    """Every subprocess and `lhpc` call the lane makes returns `result[0]`."""
    result = [_done()]
    monkeypatch.setattr(lane, "run_lhpc", lambda *a, **k: result[0])
    monkeypatch.setattr(lane, "subprocess", types.SimpleNamespace(
        run=lambda *a, **k: result[0], CompletedProcess=subprocess.CompletedProcess))
    return result


class _Svc:
    def graywolf_upstream_check(self, stack):
        return types.SimpleNamespace(ok=True, summary="")

    def graywolf_upstream_state(self, stack):
        return {"latest": "1.2.3"}


def _paths(tmp_path):
    env = {"LHPC_RUNTIME_ROOT": str(tmp_path)}
    return {
        "install": lambda: lane._install(env, "kiss"),
        "build": lambda: lane._build(env, "meshcore-cli"),
        "deb-fetch": lambda: lane._deb_fetch(_Svc(), GRAYWOLF, tmp_path),
        "cli-venv": lambda: lane._cli_venv(lane.run_lhpc()),
        "self-update": lambda: lane._helper(tmp_path / "lhpc", env),
        "calibrate": lane._calibrate,
    }


PATHS = ("install", "build", "deb-fetch", "cli-venv", "self-update", "calibrate")


@pytest.mark.parametrize("path", PATHS)
def test_a_timeout_line_on_stderr_fails_every_path(step, path, tmp_path):
    step[0] = _done(stdout="[venv] 3.0 s\n", stderr=f"building\n{MARKER}\n")
    with pytest.raises(pytest.fail.Exception) as red:
        _paths(tmp_path)[path]()
    assert f"rejection marker — not evidence: {MARKER!r}" in str(red.value)


@pytest.mark.parametrize("path", PATHS)
def test_a_marker_on_stdout_fails_every_path(step, path, tmp_path):
    step[0] = _done(stdout="[stalled] no activity for 10 min\n")
    with pytest.raises(pytest.fail.Exception, match=r"\[stalled\] no activity for 10 min"):
        _paths(tmp_path)[path]()


@pytest.mark.parametrize("path", PATHS)
def test_a_non_zero_status_fails_every_path(step, path, tmp_path):
    step[0] = _done(rc=124, stdout="", stderr="killed\n")
    with pytest.raises(pytest.fail.Exception, match=r"failed \(rc 124\)"):
        _paths(tmp_path)[path]()


@pytest.mark.parametrize("line", ["[fail] x", "[FAILED] x", "  [timeout]",
                                  "[TIMED OUT after 900s — job was KILLED; result is INCOMPLETE]"])
def test_every_marker_form_is_rejected(line):
    with pytest.raises(pytest.fail.Exception):
        lane._judged("step", _done(stderr=f"a\n{line}\nb\n"))


def test_a_clean_step_passes_and_returns_both_streams():
    out = lane._judged("step", _done(stdout="[progress] longest quiet 12.0 s\n",
                                     stderr="0 failed, 3 passed [failures: none]\n"))
    assert "[progress] longest quiet 12.0 s" in out and "0 failed" in out


def test_the_lane_records_the_quiet_line_a_build_step_log_ends_with(step, tmp_path, monkeypatch):
    # The log as `run_job` writes it for a build step whose Watch saw a 41.5 s quiet period.
    class _Streaming:
        def run_streaming(self, argv, timeout, log_fh, **kw):
            log_fh.write("compiling\n")
            return CommandResult(returncode=0, stdout="", stderr="", longest_quiet_s=41.5)

    def build(*a, **k):                         # the build this invocation runs writes it
        run_job(_Streaming(), name="build-meshcore-cli", argv=["make"], cwd=None,
                logs_dir=tmp_path / "logs", paths=Paths(runtime_root=tmp_path), stall_s=600.0)
        return _done()
    monkeypatch.setattr(lane, "run_lhpc", build)
    seen = []
    monkeypatch.setattr(lane, "_record", lambda *a, **k: seen.append((a, k)))
    lane._build({"LHPC_RUNTIME_ROOT": str(tmp_path)}, "meshcore-cli")
    assert seen and seen[0][1] == {"quiet_s": 41.5}


def test_a_build_log_this_invocation_did_not_write_is_not_evidence(step, tmp_path, monkeypatch):
    """A zero-exit build that wrote no fresh log (nothing to build, or a broken log path) must not
    record an older build's quiet time from a log left in logs/."""
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "build-meshcore-cli.log").write_text("compiling\n[progress] longest quiet 7.0 s\n")
    seen = []
    monkeypatch.setattr(lane, "_record", lambda *a, **k: seen.append((a, k)))
    with pytest.raises(AssertionError, match="no build log written by this invocation"):
        lane._build({"LHPC_RUNTIME_ROOT": str(tmp_path)}, "meshcore-cli")
    assert seen == []


# ---- the budget case's waivers (bootstrap; L4 on the introducing release) -------------------

L4 = "no row C evidence for lhpc-selfupdate selfupdate-pip: the slow-build run did not measure it"
NO_Z = "no Zero baseline for lhpc-selfupdate selfupdate-pip: run row A"
OTHER = "no row C evidence for kiss build: the slow-build run did not measure it"


def test_bootstrap_on_the_introducing_release_names_l4_and_waives_only_it():
    fails, boot = lane._waived([L4, NO_Z, OTHER], [], intro=True)
    assert fails == [OTHER]                          # the decision: only the L4 pair is waived
    assert lane.stt.BOOTSTRAP in boot and lane.L4_INTRODUCING in boot   # named, not its prose


def test_missing_l4_evidence_fails_when_not_the_introducing_release():
    fails, boot = lane._waived([L4, NO_Z], [], intro=False)
    assert fails == [L4] and lane.L4_INTRODUCING not in boot


NO_Z_L4 = NO_Z     # the L4 pair's missing Zero baseline


def test_past_bootstrap_the_introducing_release_waives_l4_only():
    """Row A on the introducing release ran the previous tag's helper too: no L4 on the Zero
    either. Past bootstrap the L4 pair is waived (named in the summary as NO EVIDENCE), every
    other failure stands."""
    other_z = "no Zero baseline for kiss build: run row A"
    fails, boot = lane._waived([L4, NO_Z_L4, OTHER, other_z], [{"op": "build"}], intro=True)
    assert boot == "" and fails == [OTHER, other_z]


def test_the_l4_waiver_needs_the_version_rule_too(monkeypatch, tmp_path):
    """A previous tag without the line is not enough: past PIP_SYNC_SINCE the L4 failures stand
    (a lost line is a defect, not the introducing release)."""
    monkeypatch.setattr(lane, "OUT", tmp_path)
    monkeypatch.setattr(lane, "_prev_tag", lambda: "v9.9.9")
    monkeypatch.setattr(lane, "_introducing", lambda prev: prev == "v9.9.9")
    monkeypatch.setattr(lane, "EVIDENCE", {})
    monkeypatch.setattr(lane, "LANE_OPS", [(lane.stt.SELFUPDATE_COMPONENT, "selfupdate-pip")])
    monkeypatch.setattr(lane, "BASELINE", {"measured": [{"op": "build"}]})
    monkeypatch.setattr(lane, "__version__", "99.0.0")
    with pytest.raises(AssertionError, match="no row C evidence for lhpc-selfupdate"):
        lane.test_slow_build_budget()
    monkeypatch.setattr(lane, "__version__", lane.stt.PIP_SYNC_SINCE)
    lane.test_slow_build_budget()
    assert "**NO EVIDENCE**" in (tmp_path / "slow-build-summary.md").read_text()



def test_a_waived_failure_is_never_shown_as_fail(monkeypatch, tmp_path):
    """The summary's verdict word follows the waiver: on the introducing release the L4 pair
    passes the case, so the summary names it waived, not **FAIL**."""
    monkeypatch.setattr(lane, "OUT", tmp_path)
    monkeypatch.setattr(lane, "_prev_tag", lambda: "v9.9.9")
    monkeypatch.setattr(lane, "_introducing", lambda prev: prev == "v9.9.9")
    monkeypatch.setattr(lane, "EVIDENCE", {})
    monkeypatch.setattr(lane, "LANE_OPS", [(lane.stt.SELFUPDATE_COMPONENT, "selfupdate-pip")])
    monkeypatch.setattr(lane, "BASELINE", {"measured": [{"op": "build"}]})
    monkeypatch.setattr(lane, "__version__", lane.stt.PIP_SYNC_SINCE)
    lane.test_slow_build_budget()
    summary = (tmp_path / "slow-build-summary.md").read_text()
    assert "**FAIL**" not in summary, summary
    assert f"**WAIVED** {L4}" in summary and "**NO EVIDENCE**" in summary, summary


def test_the_l4_waiver_does_not_need_a_helper_run(monkeypatch, tmp_path):
    """Correction 12: the waiver reads the previous tag, not the self-update case's outcome. With
    no helper run at all (the case failed before it), the introducing release still waives the L4
    pair; the helper's own missing evidence (L3) stands, named alone."""
    monkeypatch.setattr(lane, "OUT", tmp_path)
    monkeypatch.setattr(lane, "_prev_tag", lambda: "v9.9.9")
    monkeypatch.setattr(lane, "_introducing", lambda prev: prev == "v9.9.9")
    monkeypatch.setattr(lane, "EVIDENCE", {})
    monkeypatch.setattr(lane, "LANE_OPS", [(lane.stt.SELFUPDATE_COMPONENT, "selfupdate-helper"),
                                           (lane.stt.SELFUPDATE_COMPONENT, "selfupdate-pip")])
    monkeypatch.setattr(lane, "BASELINE", {"measured": [{"op": "build"}]})
    monkeypatch.setattr(lane, "__version__", lane.stt.PIP_SYNC_SINCE)
    with pytest.raises(AssertionError) as exc:
        lane.test_slow_build_budget()
    lines = str(exc.value).split("\n")
    assert "selfupdate-helper" in str(exc.value) and "selfupdate-pip" not in str(exc.value), lines


def test_the_box_is_hardened_like_a_bootstrapped_one(tmp_path):
    """Correction 12 (testlab run 37162748975): a clone under a group-writable umask left the
    checkout 0775, and the helper refused it ("checkout is group/other-writable"). The case now
    hardens the root, src/ and the checkout to 0700, as a box's bootstrap does. The predicate is
    the identity check's own: `st_mode & 0o022` on each of the three."""
    root = tmp_path / "runtime"
    co = root / "src" / "loraham-pi-control"
    co.mkdir(parents=True)
    for d in (root, root / "src", co):
        d.chmod(0o775)
    assert all(d.stat().st_mode & 0o022 for d in (root, root / "src", co))      # refused
    lane._harden_box(root, co)
    assert [d.stat().st_mode & 0o777 for d in (root, root / "src", co)] == [0o700] * 3  # accepted

# ---- a step faster than the log's resolution (testlab run 37145791526) ----------------------

def test_a_checkout_below_log_resolution_is_evidence_at_the_floor(tmp_path, monkeypatch):
    """`[git] checkout <ref> 0.0 s` (a sub-50 ms checkout, `.1f`) is a fast step, not a broken
    one: one entry at the floor with its note, and the stack's measurement goes on."""
    monkeypatch.setattr(lane, "_env_problems", list)
    monkeypatch.setattr(lane, "_write", lambda: None)
    monkeypatch.setattr(lane, "EVIDENCE", {})
    component = next(c for c, o in lane.LANE_OPS if o == "clone")
    adopter = lane.stt.tree_components(lane.STACKS, component)[0]
    (tmp_path / "logs").mkdir()
    (tmp_path / "logs" / f"adopt-{adopter}.log").write_text(
        "[git] clone 12.3 s\n[git] checkout v1.2.3 0.0 s\n")
    lane._adoption({"LHPC_RUNTIME_ROOT": str(tmp_path)}, component)
    checkout = lane.EVIDENCE[(component, "checkout")]
    assert checkout["seconds"] == lane.LOG_RESOLUTION_S == 0.1
    assert checkout["note"] == lane.BELOW_RESOLUTION == "below log resolution"
    assert lane.stt.entry_errors(checkout) == []
    clone = lane.EVIDENCE[(component, "clone")]
    assert clone["seconds"] == 12.3 and "note" not in clone


def test_the_validator_still_rejects_a_zero():
    floor = {"component": "c", "op": "checkout", "key": "pin:abc", "source": "throttled-ci",
             "host": "h", "lhpc": "v0.11.12 (abc1234)", "date": lane.dt.date(2026, 10, 3),
             "evidence": "e", "note": lane.BELOW_RESOLUTION}
    assert lane.stt.entry_errors({**floor, "seconds": lane.LOG_RESOLUTION_S}) == []
    for op in ("checkout", "clone", "build"):
        assert "seconds must be a positive number" in lane.stt.entry_errors(
            {**floor, "op": op, "seconds": 0.0})


# ---- the self-update helper runs as its systemd unit does -----------------------------------

def test_the_helper_runs_past_the_unit_plumbing_guard(tmp_path):
    """`lhpc self-update --run-service` refuses (rc 2) without INVOCATION_ID; the lane's helper
    step must run it as systemd does and get the helper's own evidence line."""
    import sys
    fake = tmp_path / "lhpc"
    fake.write_text(
        f"#!{sys.executable}\n"
        "import sys\n"
        "from lhpc.adapters.cli.main import _unit_plumbing_refusal\n"
        "rc = _unit_plumbing_refusal('--run-service', 'lhpc-selfupdate.service', 'x')\n"
        "if rc is None:\n"
        f"    print('{lane._PIP_SYNC_MARK} 4.2 s')\n"
        "sys.exit(rc or 0)\n")
    fake.chmod(0o755)
    env = {k: v for k, v in lane.os.environ.items() if k != "INVOCATION_ID"}
    _, out = lane._helper(fake, env)
    assert lane._PIP_SYNC.findall(out) == ["4.2"]


# ---- the helper's runtime carries a box's canonical units (testlab run 37148794385) ---------

def _verify_set(root, env):
    """The helper's own post-update unit check, as it runs it (service_selfupdate)."""
    import sys
    return subprocess.run([sys.executable, "-m", "lhpc.core.updater_units", "verify-set",
                           str(root)], env=env, capture_output=True, text=True, timeout=60,
                          check=False)


def test_the_helper_runtime_has_the_units_its_verification_requires(tmp_path):
    """Run 37148794385: the helper applied the update, then exited 1 — "units not canonical:
    lhpc-boot-restore.service: missing; …" — because the lane's runtime had no units at all. The
    lane now installs them as install.sh does, into the runtime's own $HOME, and the helper's
    verification passes on them."""
    import sys
    root = tmp_path / "runtime"
    (root / "src").mkdir(parents=True)
    env = lane._box_env(root, tmp_path / "home")
    assert env["HOME"] == str(tmp_path / "home") and env["LHPC_RUNTIME_ROOT"] == str(root)
    before = _verify_set(root, env)
    assert before.returncode == 1 and "lhpc-boot-restore.service: missing" in before.stdout
    kinds = lane._install_units(Path(sys.executable), root, env)
    from lhpc.core import updater_units
    assert tuple(kinds) == updater_units.ALL_UNITS
    after = _verify_set(root, env)
    assert (after.returncode, after.stdout) == (0, "ok\n"), after.stdout + after.stderr
    unit = (tmp_path / "home" / ".config/systemd/user" / updater_units.HELPER_UNIT).read_text()
    assert f"{root}/venv/lhpc/bin/lhpc " in unit      # the box layout the lane runs the helper in


def test_the_lane_never_writes_the_real_home_units(tmp_path):
    env = lane._box_env(tmp_path / "runtime", tmp_path / "home")
    assert env["HOME"] != str(Path.home())
    assert env["XDG_CACHE_HOME"]                       # the pip cache stays the user's


# ---- the disk throttle is part of the throttled box (Correction 7, GAP 3) -------------------

ROOT, WORK = "8:0", "259:0"
REQUIRED = {"the container's writable layer": ROOT, "the calibration work dir": WORK}


@pytest.mark.parametrize("io_max, ok", [
    ("8:0 rbps=max wbps=max riops=300 wiops=120\n259:0 riops=300 wiops=120\n", True),
    ("8:0 rbps=max wbps=max riops=200 wiops=100\n259:0 riops=1 wiops=1\n", True),  # tighter
    ("8:0 rbps=max wbps=max riops=max wiops=120\n259:0 riops=300 wiops=120\n", False),  # reads
    ("8:0 rbps=max wbps=max riops=300 wiops=500\n259:0 riops=300 wiops=120\n", False),  # loose
    ("", False),                                                  # no throttle at all
])
def test_the_lane_proves_the_disk_throttle(tmp_path, monkeypatch, io_max, ok):
    monkeypatch.setenv("SLOW_WRITE_IOPS", "120")
    monkeypatch.setenv("SLOW_READ_IOPS", "300")
    (tmp_path / "io.max").write_text(io_max)
    assert (lane._io_problems(tmp_path, REQUIRED) == []) is ok


@pytest.mark.parametrize("io_max, bad", [
    ("7:0 riops=300 wiops=120\n", [ROOT, WORK]),             # only an unrelated disk is tight
    ("8:0 riops=300 wiops=120\n7:0 riops=1 wiops=1\n", [WORK]),    # one of the two required
    ("8:0 riops=300 wiops=120\n259:0 riops=max wiops=max\n", [WORK]),
])
def test_each_required_disk_must_be_throttled(tmp_path, monkeypatch, io_max, bad):
    """Correction 8, finding 1: one tight line anywhere is not the throttle — every required
    disk needs its own, and the problem names the disk that has none."""
    monkeypatch.setenv("SLOW_WRITE_IOPS", "120")
    monkeypatch.setenv("SLOW_READ_IOPS", "300")
    (tmp_path / "io.max").write_text(io_max)
    problems = lane._io_problems(tmp_path, REQUIRED)
    assert [mm for mm in (ROOT, WORK) if any(f"disk {mm}," in p for p in problems)] == bad
    assert len(problems) == len(bad), problems
    assert lane._io_problems(tmp_path, {})[0].startswith("no disk is required")


def _fake_sys(tmp_path, monkeypatch, dev: int, disk: str | None, partition: bool):
    """A /sys/dev/block holding `dev` (a partition of `disk` when `partition`)."""
    sysb = tmp_path / "sys"
    sysb.mkdir()
    if disk is not None:
        node = tmp_path / "devices" / "vda"
        node.mkdir(parents=True)
        (node / "dev").write_text(f"{disk}\n")
        if partition:
            node = node / "vda1"
            node.mkdir()
            (node / "partition").write_text("1\n")
            (node / "dev").write_text(f"{lane.os.major(dev)}:{lane.os.minor(dev)}\n")
        (sysb / f"{lane.os.major(dev)}:{lane.os.minor(dev)}").symlink_to(node)
    monkeypatch.setattr(lane, "SYS_BLOCK", sysb)


@pytest.mark.parametrize("partition", [False, True])
def test_disk_of_resolves_the_whole_disk_like_the_job(tmp_path, monkeypatch, partition):
    dev = tmp_path.stat().st_dev
    if not lane.os.major(dev):
        pytest.skip("tmp_path is on a device-less filesystem here")
    _fake_sys(tmp_path, monkeypatch, dev, "254:0", partition)
    assert lane._disk_of(tmp_path / "not" / "yet") == "254:0"   # the nearest existing parent


def _box(tmp_path, monkeypatch, root_disk, work_disk, io_max, layer=False):
    monkeypatch.setenv("SLOW_CPUS", "0.25")
    monkeypatch.setenv("SLOW_WRITE_IOPS", "120")
    monkeypatch.setenv("SLOW_READ_IOPS", "300")
    for k in [k for k in lane.os.environ if k.startswith("LHPC_BUILD_")]:
        monkeypatch.delenv(k)
    if root_disk is None:
        monkeypatch.delenv("SLOW_IO_ROOT_DISK", raising=False)
    else:
        monkeypatch.setenv("SLOW_IO_ROOT_DISK", root_disk)
    work = tmp_path / "home" / ".cache" / "lhpc-calib"
    (tmp_path / "home").mkdir()
    monkeypatch.setattr(lane, "CALIB_WORK", work)
    monkeypatch.setattr(lane, "_disk_of", lambda p: work_disk if p == work else None)
    monkeypatch.setattr(lane, "_on_writable_layer", lambda p: layer)
    cg = tmp_path / "cg"
    cg.mkdir()
    (cg / "cpu.max").write_text("25000 100000\n")
    (cg / "memory.max").write_text(f"{416 * 2**20}\n")
    (cg / "io.max").write_text(io_max)
    real = lane.Path
    monkeypatch.setattr(lane, "Path", lambda *a: cg if a == ("/sys/fs/cgroup",) else real(*a))


TIGHT = "8:0 riops=300 wiops=120\n259:0 riops=300 wiops=120\n"


def test_the_box_with_both_required_disks_throttled_is_the_throttled_box(tmp_path, monkeypatch):
    _box(tmp_path, monkeypatch, ROOT, WORK, TIGHT)
    assert lane._env_problems() == []


def test_a_work_dir_on_the_writable_layer_is_the_layer_disk(tmp_path, monkeypatch):
    """The CI case: $HOME is on the container's overlay root, which has no block device inside
    the container — the work dir then needs the disk the job named behind Docker's storage."""
    _box(tmp_path, monkeypatch, ROOT, None, "8:0 riops=300 wiops=120\n", layer=True)
    assert lane._env_problems() == []
    (tmp_path / "cg" / "io.max").write_text("259:0 riops=300 wiops=120\n")
    problems = lane._env_problems()
    assert len(problems) == 2 and all("disk 8:0," in p for p in problems), problems


@pytest.mark.parametrize("root_disk, work_disk, want", [
    (None, WORK, "SLOW_IO_ROOT_DISK=None"),
    ("sda", WORK, "SLOW_IO_ROOT_DISK='sda'"),
    (ROOT, None, "STOP: backing device of"),
])
def test_an_unresolvable_required_disk_records_nothing(tmp_path, monkeypatch, root_disk,
                                                       work_disk, want):
    """Correction 8, finding 2: a required disk that cannot be resolved is a problem — the lane
    measures nothing as evidence — never a disk silently left out of the check."""
    _box(tmp_path, monkeypatch, root_disk, work_disk, TIGHT)
    problems = lane._env_problems()
    assert any(p.startswith(want) for p in problems), problems
    monkeypatch.setattr(lane, "EVIDENCE", {})
    monkeypatch.setattr(lane, "_write", lambda: pytest.fail("evidence written"))
    with pytest.raises(AssertionError, match="not evidence"):
        lane._record("meshcore-cli", "build", 12.0)
    assert lane.EVIDENCE == {}


def test_the_calibration_work_dir_is_the_one_the_throttle_check_names(step):
    seen = []
    step[0] = _done(stdout="cpu=1.0 io=1.0 mem=1.0 workload=sha256:" + "0" * 64 + "\n")
    lane.subprocess.run = lambda cmd, **k: seen.append(cmd) or step[0]
    lane._calibrate()
    assert seen[0][-2:] == ["--work-dir", str(lane.CALIB_WORK)]


def test_the_disk_throttle_must_be_named(tmp_path, monkeypatch):
    monkeypatch.delenv("SLOW_WRITE_IOPS", raising=False)
    (tmp_path / "io.max").write_text("8:0 riops=1 wiops=1\n")
    assert lane._io_problems(tmp_path, REQUIRED)[0].startswith("SLOW_WRITE_IOPS=None")
    monkeypatch.setenv("SLOW_WRITE_IOPS", "1")
    monkeypatch.setenv("SLOW_READ_IOPS", "1")
    assert lane._io_problems(tmp_path / "nowhere", REQUIRED)[0].startswith("io.max is unreadable")
