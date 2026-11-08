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
    blocks = list(_run_blocks(workflow.read_text()))
    assert blocks                                                     # something is checked
    for line_no, script in blocks:
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


# --- .github/scripts/retry.sh: the transfer backoff, by the one policy (lhpc/core/transfer.py) ----
#
# The helper exists so an outage at jsDelivr, PyPI or GitHub cannot redden a gate. It repeats a
# failure only when every error line of the command's stdout and stderr is a failed transfer, and
# it is used only around acquisitions, never around a verdict, which the guard below pins.

RETRY = repo_paths.REPO / ".github" / "scripts" / "retry.sh"
NET = "curl: (22) The requested URL returned error: 503"


def _backoff(script: str, **env):
    """The helper with `sleep` replaced by a recorder, so the schedule is read, not waited."""
    return subprocess.run(
        ["bash", "-c", f'. "{RETRY}"\nsleep() {{ echo "slept $1"; }}\n{script}'],
        capture_output=True, text=True, env={"PATH": "/usr/bin:/bin", **env},
        timeout=60,                    # a schedule that never ends fails here, not by hanging
    )


def _slept(r):
    return [ln.split()[1] for ln in r.stdout.splitlines() if ln.startswith("slept ")]


def test_the_retry_helper_parses():
    assert subprocess.run(["bash", "-n", str(RETRY)], capture_output=True).returncode == 0


def test_a_command_that_succeeds_runs_exactly_once_and_passes_its_output_through():
    r = _backoff('n=0; c() { n=$((n+1)); echo "to-out"; echo "to-err" >&2; }; backoff c; '
                 'echo "attempts=$n status=$?"')
    assert "attempts=1 status=0" in r.stdout
    assert "to-out" in r.stdout and "to-err" in r.stderr and _slept(r) == []


def test_a_transfer_that_recovers_is_a_success_after_the_waits():
    r = _backoff(f'n=0; c() {{ n=$((n+1)); [ "$n" -ge 3 ] || {{ echo "{NET}" >&2; return 22; }}; }}; '
                 'backoff c; echo "attempts=$n status=$?"')
    assert "attempts=3 status=0" in r.stdout
    assert _slept(r) == ["0", "60"]
    assert r.stderr.count(NET) == 2                        # stderr passes through unchanged


def test_the_backoff_schedule_is_the_documented_one():
    """At once, then 1, 2, 5 and 10 min, then every 10 min while no attempt would start past the
    30 min retry-start deadline (docs/testlab.md); the command's own status comes back."""
    r = _backoff(f'c() {{ echo "{NET}" >&2; return 7; }}; backoff c; echo "status=$?"')
    assert _slept(r) == ["0", "60", "120", "300", "600", "600"]
    assert "status=7" in r.stdout
    assert r.stderr.count("failed (status 7); retrying in") == 6
    assert "giving up: the retry-start deadline (1800 s) has passed" in r.stderr


@pytest.mark.parametrize("out,err", [
    ("", "curl: (22) The requested URL returned error: 404"),            # a 4xx
    ("ERROR: 404 Not Found", NET),                                        # 503 on stderr, 404 on stdout
    ("sha256 MISMATCH for x.deb", NET),                                   # 503, then a hash mismatch
    (NET, "ERROR: THESE PACKAGES DO NOT MATCH THE HASHES FROM THE REQUIREMENTS FILE."),
    ("", "fatal: repository 'https://github.com/x/y/' not found"),        # another error
    ("", ""),                                                             # no error line at all
], ids=["4xx", "503-then-404-across-streams", "503-then-mismatch", "mismatch-on-stderr",
        "other-error", "no-error-line"])
def test_a_failure_that_is_not_a_transfer_is_never_repeated(out, err):
    r = _backoff(f'n=0; c() {{ n=$((n+1)); printf "%s\\n" "{out}"; printf "%s\\n" "{err}" >&2; return 5; }}; '
                 'backoff c; echo "attempts=$n status=$?"')
    assert "attempts=1 status=5" in r.stdout and _slept(r) == []
    assert "giving up: not a transfer failure" in r.stderr


def test_the_attempts_count_against_the_retry_start_deadline():
    """An instant attempt leaves room for the one retry a zero total allows; an attempt that
    took a second does not: no attempt starts once the attempts and the waits passed the total."""
    fast = _backoff(f'n=0; c() {{ n=$((n+1)); echo "{NET}" >&2; return 4; }}; backoff c; '
                    'echo "attempts=$n"', LHPC_TRANSFER_WAITS="0", LHPC_TRANSFER_TOTAL="0")
    slow = _backoff(f'n=0; c() {{ n=$((n+1)); command sleep 1.2; echo "{NET}" >&2; return 4; }}; '
                    'backoff c; echo "attempts=$n"', LHPC_TRANSFER_WAITS="0", LHPC_TRANSFER_TOTAL="0")
    assert "attempts=2" in fast.stdout and "attempts=1" in slow.stdout


def test_a_test_schedule_without_a_last_wait_stops_after_the_list():
    r = _backoff(f'n=0; c() {{ n=$((n+1)); echo "{NET}" >&2; return 3; }}; backoff c; '
                 'echo "attempts=$n status=$?"', LHPC_TRANSFER_WAITS="0 0")
    assert "attempts=3 status=3" in r.stdout


def test_the_helper_does_not_wrap_a_verdict():
    """The one rule that keeps this helper honest. `pip-audit` exits 1 on a REAL vulnerability and
    `pytest` exits 1 on a REAL failure, so retrying either converts a true red into green. Only
    idempotent acquisition may be wrapped.

    The check reads the PROGRAM being retried, not the whole line: a backoff around pip whose
    arguments name `pip-audit` installs the auditor and is fine, while `backoff pip-audit .` would
    run it and is not. A substring match cannot tell those apart.
    """
    assert WORKFLOWS                                                  # something is checked
    for wf in WORKFLOWS:
        for lineno, line in enumerate(wf.read_text().splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            m = re.search(r"\bbackoff\s+(.*)", stripped)
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


def test_there_is_one_retry_helper():
    """The one policy: `backoff` only. The old fixed-count `retry N` (no classification, 5 s
    doubling) is gone from the helper and from every workflow."""
    assert not re.search(r"^retry\(\)", RETRY.read_text(), re.MULTILINE)
    left = [f"{wf.name}:{i}" for wf in WORKFLOWS
            for i, ln in enumerate(wf.read_text().splitlines(), 1)
            if re.search(r"\bretry\s+\d+\s", ln) and not ln.strip().startswith("#")]
    assert not left, f"a `retry N` caller is left: {left}"
