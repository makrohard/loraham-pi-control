"""Every shell block in a workflow must actually parse.

A comment inside the release lane's `docker run … bash -lc '…'` once contained an apostrophe.
It ended the single-quoted string, and the job died in sixty seconds with "unexpected EOF while
looking for matching quote" — after building the lab image, before running a single test. Nothing
caught it, because a workflow's shell is never parsed until the runner runs it.
"""
from __future__ import annotations

import re
import subprocess
import textwrap

import pytest

import repo_paths

WORKFLOWS = sorted((repo_paths.REPO / ".github" / "workflows").glob("*.yml"))


def _run_blocks(text: str):
    """(line number, script) for every `run: |` block, by indentation. No YAML parser: the
    controller's dev dependencies have none, and this must run in CI."""
    lines, out = text.splitlines(), []
    for i, line in enumerate(lines):
        m = re.match(r"^(\s*)-?\s*run:\s*[|>]-?\s*$", line)
        if not m:
            continue
        indent, body = len(m.group(1)), []
        for nxt in lines[i + 1:]:
            if nxt.strip() and len(nxt) - len(nxt.lstrip()) <= indent:
                break
            body.append(nxt)
        # DEDENTED, as the YAML block scalar is before the runner sees it. Without this a
        # here-document terminator keeps its indentation and never matches, which reads as a
        # syntax error in a file that is perfectly correct.
        out.append((i + 1, textwrap.dedent("\n".join(body))))
    return out


def test_the_scan_finds_the_blocks_it_is_meant_to_check():
    """A scan that silently matched nothing would pass for ever."""
    found = sum(len(_run_blocks(w.read_text())) for w in WORKFLOWS)
    assert WORKFLOWS, "no workflows found at all"
    assert found >= 5, f"only {found} run blocks found — the scan has stopped seeing them"


@pytest.mark.parametrize("workflow", WORKFLOWS, ids=lambda w: w.name)
def test_every_workflow_shell_block_parses(workflow):
    for line_no, script in _run_blocks(workflow.read_text()):
        r = subprocess.run(["bash", "-n"], input=script, text=True, capture_output=True,
                           check=False)
        assert r.returncode == 0, (
            f"{workflow.name} line {line_no}: the shell does not parse — "
            f"{r.stderr.strip().splitlines()[-1] if r.stderr.strip() else 'no message'}")


def test_a_comment_in_a_workflow_shell_block_carries_no_quote():
    """`bash -n` is not enough, and this is why.

    A double quote in a comment inside a nested `su … -c "…"` string ended that string early.
    What was left still PARSED — it installed a virtualenv and exited 0 — so the lane reported
    success in seventy seconds having never run a test. A syntax check cannot see that; the only
    cheap defence is to keep quotes out of comments in these blocks entirely.
    """
    offenders = []
    for workflow in WORKFLOWS:
        for line_no, script in _run_blocks(workflow.read_text()):
            for offset, line in enumerate(script.splitlines()):
                stripped = line.strip()
                if stripped.startswith("#") and ('"' in stripped or "'" in stripped):
                    offenders.append(f"{workflow.name}:{line_no + offset}: {stripped[:60]}")
    assert not offenders, ("a quote inside a shell comment can end the string it sits in:\n"
                           + "\n".join(offenders))


# --- .github/scripts/retry.sh ------------------------------------------------------------------
#
# The helper exists so an outage at jsDelivr, PyPI or GitHub cannot redden a gate. It is
# deliberately unable to classify failures: it cannot tell a transport fault from a real defect,
# and a retry that guesses is how a red verdict becomes green. Its safety comes from WHERE it is
# used, which the last test here pins.

RETRY = repo_paths.REPO / ".github" / "scripts" / "retry.sh"


def _retry(script: str):
    """Run a snippet with the helper sourced and no delay, so these cost no wall-clock time."""
    return subprocess.run(
        ["bash", "-c", f'. "{RETRY}"\n{script}'],
        capture_output=True, text=True, env={"PATH": "/usr/bin:/bin", "RETRY_DELAY": "0"},
    )


def test_the_retry_helper_parses():
    assert subprocess.run(["bash", "-n", str(RETRY)], capture_output=True).returncode == 0


def test_a_command_that_succeeds_runs_exactly_once():
    r = _retry('n=0; c() { n=$((n+1)); }; retry 3 c; echo "attempts=$n status=$?"')
    assert "attempts=1 status=0" in r.stdout


def test_a_command_that_succeeds_on_the_third_attempt_is_a_success():
    r = _retry('n=0; c() { n=$((n+1)); [ "$n" -ge 3 ]; }; retry 3 c; echo "attempts=$n status=$?"')
    assert "attempts=3 status=0" in r.stdout


def test_exhausting_the_attempts_returns_the_commands_own_status():
    """Not merely non-zero: the caller's `set -e` and any `||` handling read this number, and a
    retry that laundered 7 into 1 would hide which failure actually happened."""
    r = _retry('c() { return 7; }; retry 2 c; echo "status=$?"')
    assert "status=7" in r.stdout


def test_the_helper_does_not_wrap_a_verdict():
    """The one rule that keeps this helper honest. `pip-audit` exits 1 on a REAL vulnerability and
    `pytest` exits 1 on a REAL failure, so retrying either converts a true red into green. Only
    idempotent acquisition may be wrapped.

    The check reads the PROGRAM being retried, not the whole line: `retry 3 pip install .[dev]
    pip-audit` installs the auditor and is fine, while `retry 3 pip-audit .` would run it and is
    not. A substring match cannot tell those apart.
    """
    for wf in WORKFLOWS:
        for lineno, line in enumerate(wf.read_text().splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            m = re.search(r"\bretry\s+\d+\s+(.*)", stripped)
            if not m:
                continue
            argv = m.group(1).split()
            # skip leading VAR=value assignments, then take the program
            while argv and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", argv[0]):
                argv.pop(0)
            assert argv, f"{wf.name}:{lineno} retries nothing: {stripped}"
            program, rest = argv[0], " ".join(argv[1:])
            verdict = (
                program in {"pip-audit", "pytest", "bandit", "ruff"}
                or (program in {"python", "python3"} and "-m pytest" in rest)
                or (program == "node" and "tests/" in rest)
            )
            assert not verdict, f"{wf.name}:{lineno} retries a verdict: {stripped}"
