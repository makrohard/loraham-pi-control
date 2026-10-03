"""The `slow-build` job's JUnit gate, run as the job runs it.

pytest exits 0 when every case skips, so the job's own gate decides whether a run measured
anything. It is a heredoc inside `.github/workflows/testlab.yml`; this extracts that exact text
and runs it over synthetic JUnit files and baselines.
"""
from __future__ import annotations

import re
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

import repo_paths

WORKFLOW = repo_paths.REPO / ".github" / "workflows" / "testlab.yml"
STACKS = ("kiss", "chat")
MEASURED = ('[[measured]]\ncomponent = "c"\nop = "clone"\nseconds = 1\nkey = "pin:x"\n'
            'source = "zero2w"\nhost = "h"\nlhpc = "v0.11.0 (abc1234)"\ndate = 2026-10-01\n'
            'evidence = "e"\n')
BOOT = "bootstrap: no row A yet — 2 operations unmeasured: a build, b clone"


def _gate() -> str:
    text = WORKFLOW.read_text()
    m = re.search(r"^( *)python3 - slowevidence/junit-slow-build\.xml \S+ <<'PY'\n(.*?)^\1PY$",
                  text, re.M | re.S)
    assert m, "the slow-build JUnit gate heredoc is gone from testlab.yml"
    return textwrap.dedent(m.group(2))


def _case(name: str, state: str = "passed", msg: str = "") -> str:
    inner = {"passed": "", "skipped": f'<skipped type="pytest.skip" message="{msg}"/>',
             "failure": '<failure message="boom"/>'}[state]
    return f'<testcase classname="t" name="{name}">{inner}</testcase>'


def _run(tmp_path: Path, cases: list[str], baseline: str) -> subprocess.CompletedProcess:
    junit, base = tmp_path / "junit.xml", tmp_path / "baseline.toml"
    junit.write_text(f'<testsuites><testsuite name="pytest">{"".join(cases)}'
                     "</testsuite></testsuites>")
    base.write_text(baseline)
    return subprocess.run([sys.executable, "-", str(junit), str(base)], input=_gate(),
                          capture_output=True, text=True, check=False)


def _row_c(state: str = "passed", msg: str = "") -> list[str]:
    return ([_case("test_slow_build_env", state, msg)]
            + [_case(f"test_slow_build_stack[{s}]", state, msg) for s in STACKS]
            + [_case("test_slow_build_selfupdate", state, msg)])


def _judging(state: str = "passed", msg: str = "") -> list[str]:
    return [_case("test_slow_build_calibrated", state, msg),
            _case("test_slow_build_budget", state, msg)]


def test_an_all_skipped_run_is_refused_even_while_bootstrapping(tmp_path):
    r = _run(tmp_path, _row_c("skipped", BOOT) + _judging("skipped", BOOT), "")
    assert r.returncode == 1 and "test_slow_build_env (skipped)" in r.stdout, r.stdout


def test_bootstrap_skips_with_passed_row_c_are_accepted(tmp_path):
    r = _run(tmp_path, _row_c() + _judging("skipped", BOOT), "[excluded]\n")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "::warning::bootstrap" in r.stdout


def test_a_bootstrap_skip_is_refused_once_one_entry_is_measured(tmp_path):
    r = _run(tmp_path, _row_c() + _judging("skipped", BOOT), MEASURED)
    assert r.returncode == 1, r.stdout
    assert "test_slow_build_calibrated (skipped)" in r.stdout
    assert "test_slow_build_budget (skipped)" in r.stdout


def test_bootstrap_accepts_only_its_own_skip_reason(tmp_path):
    r = _run(tmp_path, _row_c() + _judging("skipped", "slow-build lane is opt-in"), "")
    assert r.returncode == 1 and "test_slow_build_budget (skipped)" in r.stdout, r.stdout


def test_a_failed_or_missing_row_c_case_is_refused(tmp_path):
    failed = _row_c()
    failed[1] = _case("test_slow_build_stack[kiss]", "failure")
    r = _run(tmp_path, failed + _judging("skipped", BOOT), "")
    assert r.returncode == 1 and "test_slow_build_stack[kiss] (failure)" in r.stdout, r.stdout
    r = _run(tmp_path, _row_c()[:1] + _judging(), MEASURED)
    assert r.returncode == 1 and "test_slow_build_stack[*] (missing)" in r.stdout, r.stdout


def test_a_fully_passed_run_is_accepted(tmp_path):
    r = _run(tmp_path, _row_c() + _judging(), MEASURED)
    assert r.returncode == 0 and "::warning::" not in r.stdout, r.stdout + r.stderr


# ---- the throttle (Correction 7): at least as slow as the Zero on every axis -----------------

def _job_env() -> dict[str, str]:
    text = WORKFLOW.read_text()
    job = text[text.index("\n  slow-build:\n"):]
    block = re.search(r"^    env:\n(.*?)^    steps:", job, re.MULTILINE | re.DOTALL)
    assert block, "the slow-build job has no env block"
    return dict(re.findall(r'^      (SLOW_\w+): "([^"]*)"', block.group(1), re.MULTILINE))


def test_the_throttle_is_named_and_reaches_the_container_and_the_lane():
    """Every axis is a named constant, applied by `docker run` and handed to the lane, whose
    `test_slow_build_env` proves each one is in force (cpu.max, memory.max, io.max)."""
    env = _job_env()
    assert set(env) == {"SLOW_CPUS", "SLOW_MEM", "SLOW_SWAP", "SLOW_WRITE_IOPS",
                        "SLOW_READ_IOPS"}, env
    text = WORKFLOW.read_text()
    run = text[text.index("docker run --rm --user root"):text.index("test -s slowevidence")]
    for flag in ('--cpus="$SLOW_CPUS"', '--memory="$SLOW_MEM"', '--memory-swap="$SLOW_SWAP"',
                 "$SLOW_IO_FLAGS", "-e SLOW_CPUS -e SLOW_MEM -e SLOW_WRITE_IOPS -e SLOW_READ_IOPS",
                 "-e SLOW_IO_ROOT_DISK",
                 "SLOW_WRITE_IOPS=$SLOW_WRITE_IOPS SLOW_READ_IOPS=$SLOW_READ_IOPS",
                 "SLOW_IO_ROOT_DISK=$SLOW_IO_ROOT_DISK"):
        assert flag in run, flag
    assert '--device-write-iops $d:$SLOW_WRITE_IOPS --device-read-iops $d:$SLOW_READ_IOPS' in text
    assert 'echo "SLOW_IO_FLAGS=$flags" >> "$GITHUB_ENV"' in text
    assert 'echo "SLOW_IO_ROOT_DISK=' in text


def test_the_cpu_quota_is_below_the_zero_breakeven():
    """Row C at 0.5 CPUs took 57.8 s on the cpu part, the Zero 99.7 s (testlab run 37148794385,
    row A 2026-10-03); the part is CPU-time bound, so the Zero's quota is 0.5 x 57.8 / 99.7. A
    quota above it is a container faster than the Zero by construction."""
    assert 0 < float(_job_env()["SLOW_CPUS"]) <= 0.5 * 57.8 / 99.7
    assert int(_job_env()["SLOW_WRITE_IOPS"]) <= 134    # the Zero's 65536 synced files / 489.8 s


# ---- the disks to throttle (Correction 8): a required disk resolves, or nothing is measured ----

def _resolve_step() -> str:
    text = WORKFLOW.read_text()
    m = re.search(r"^( *)- name: Resolve the disks to throttle\n\1  run: \|\n(.*?)\n\n",
                  text, re.M | re.S)
    assert m, "the slow-build job's disk-resolving step is gone from testlab.yml"
    return textwrap.dedent(m.group(2))


def _resolve(tmp_path: Path, storage: str, driver: str = "overlay2"):
    """The step, as the runner runs it (`bash -e`), with a stub `docker` naming `storage`."""
    stub = tmp_path / "bin"
    stub.mkdir()
    (stub / "docker").write_text(
        "#!/bin/sh\ncase $* in *DockerRootDir*) echo " + storage + " ;; "
        "*Driver*) echo " + driver + " ;; *) exit 9 ;; esac\n")
    (stub / "docker").chmod(0o755)
    env_file = tmp_path / "github_env"
    env_file.write_text("")
    env = {"PATH": f"{stub}:/usr/bin:/bin:/usr/sbin:/sbin", "GITHUB_ENV": str(env_file),
           "SLOW_WRITE_IOPS": "120", "SLOW_READ_IOPS": "1200"}
    r = subprocess.run(["bash", "-e", "-c", _resolve_step()], env=env, capture_output=True,
                       text=True, check=False)
    return r, env_file.read_text()


def _tools():
    if not all(Path(p).exists() for p in ("/usr/bin/findmnt", "/usr/bin/lsblk", "/proc/swaps")):
        pytest.skip("findmnt / lsblk / /proc/swaps are not available here")


@pytest.mark.parametrize("storage", ["no/such/storage", "/proc"])   # missing; no block device
def test_an_unresolvable_required_disk_stops_the_job_before_any_measurement(tmp_path, storage):
    """Correction 8, finding 2: Docker's storage on no resolvable disk was skipped (`return 0`)
    and the job ran unthrottled on the other disks. Now it stops, naming the path, and hands the
    measuring step no throttle to run with."""
    _tools()
    storage = str(tmp_path / storage)
    r, env = _resolve(tmp_path, storage)
    assert r.returncode == 1, r.stdout + r.stderr
    assert (f"::error::STOP: backing device of {storage} could not be "
            "resolved — the throttle cannot be applied") in r.stdout, r.stdout
    assert "SLOW_IO_FLAGS" not in env and "SLOW_IO_ROOT_DISK" not in env


def test_disk_of_fails_loudly_on_an_unresolvable_path(tmp_path):
    _tools()
    fn = re.search(r"^disk_of\(\) \{\n.*?^\}\n", _resolve_step(), re.M | re.S)
    assert fn, "disk_of() is gone from the step"
    r = subprocess.run(["bash", "-c", fn.group(0) + 'disk_of "$1"', "-", str(tmp_path / "x")],
                       capture_output=True, text=True, check=False)
    assert r.returncode == 1 and r.stdout == "", r.stdout
    assert f"backing device of {tmp_path}/x could not be resolved" in r.stderr, r.stderr


def test_a_resolvable_storage_disk_is_throttled_and_named_to_the_lane(tmp_path):
    _tools()
    probe = subprocess.run(["findmnt", "-n", "-o", "MAJ:MIN", "--target", str(tmp_path)],
                           capture_output=True, text=True, check=False).stdout.split()
    if not probe or not Path(f"/sys/dev/block/{probe[0]}").exists():
        pytest.skip("the test's own disk does not resolve here")
    r, env = _resolve(tmp_path, str(tmp_path))
    assert r.returncode == 0, r.stdout + r.stderr
    flags = re.search(r"^SLOW_IO_FLAGS=(.*)$", env, re.M).group(1)
    root = re.search(r"^SLOW_IO_ROOT_DISK=(\d+:\d+)$", env, re.M)
    assert root, env
    disk = re.search(r"--device-write-iops (/dev/\S+):120 --device-read-iops \1:1200", flags)
    assert disk, flags
