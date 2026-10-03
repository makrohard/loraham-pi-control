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
                 "SLOW_WRITE_IOPS=$SLOW_WRITE_IOPS SLOW_READ_IOPS=$SLOW_READ_IOPS"):
        assert flag in run, flag
    assert '--device-write-iops $d:$SLOW_WRITE_IOPS --device-read-iops $d:$SLOW_READ_IOPS' in text
    assert 'echo "SLOW_IO_FLAGS=$flags" >> "$GITHUB_ENV"' in text


def test_the_cpu_quota_is_below_the_zero_breakeven():
    """Row C at 0.5 CPUs took 57.8 s on the cpu part, the Zero 99.7 s (testlab run 37148794385,
    row A 2026-10-03); the part is CPU-time bound, so the Zero's quota is 0.5 x 57.8 / 99.7. A
    quota above it is a container faster than the Zero by construction."""
    assert 0 < float(_job_env()["SLOW_CPUS"]) <= 0.5 * 57.8 / 99.7
    assert int(_job_env()["SLOW_WRITE_IOPS"]) <= 134    # the Zero's 65536 synced files / 489.8 s
