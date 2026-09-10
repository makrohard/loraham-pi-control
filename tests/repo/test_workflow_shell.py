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
from pathlib import Path

import pytest

WORKFLOWS = sorted((Path(__file__).resolve().parents[2] / ".github" / "workflows").glob("*.yml"))


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
