"""Every container a workflow step starts is removed when the step ends (.github/scripts/containers.sh).

The docker client's end never ended its container: a step killed by a job timeout or a cancel left the container
running, and `--rm` removes a container only once it exits (G1-TC-T3-RE B1, the class). A step starts its containers
with `crun`, which names each one; the step removes every name when it ends."""
from __future__ import annotations

import os
import re
import select
import signal
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
HELPER = ROOT / ".github" / "scripts" / "containers.sh"
SOURCE = ". .github/scripts/containers.sh"


def _run_blocks(text: str):
    """Each step's `run: |` block, as its lines."""
    lines = text.split("\n")
    for i, line in enumerate(lines):
        m = re.match(r"^(\s*)run: \|\s*$", line)
        if not m:
            continue
        block = []
        for nxt in lines[i + 1:]:
            if nxt.strip() and len(nxt) - len(nxt.lstrip()) <= len(m.group(1)):
                break
            block.append(nxt)
        yield block


def test_every_container_a_workflow_starts_is_removed_when_its_step_ends():
    """A container starts only through `crun` or `lhpc_wait_for` (the quota step's program, which names its container
    as the step does), never inside $(...) (bash holds its traps until a substitution ends), in a step that sources the
    helper before it. The one exception is the slow-build disk probe's detached container, whose step removes it by id
    in its own EXIT trap."""
    seen = 0
    for wf in sorted((ROOT / ".github" / "workflows").glob("*.yml")):
        for block in _run_blocks(wf.read_text()):
            code = [ln.split(" #", 1)[0] for ln in block if not ln.lstrip().startswith("#")]
            starts = [i for i, ln in enumerate(code) if re.search(r"\b(crun|lhpc_wait_for)\b", ln)]
            for i, ln in enumerate(code):
                assert not re.search(r"\$\(\s*(crun|lhpc_wait_for)\b", ln), f"{wf.name}: inside $(...): {ln.strip()}"
                if re.search(r"(?:^\s*|\$\(|[;&|]\s*)docker (?:run|create)\b", ln):   # a command, not text
                    assert "docker run -d" in ln, f"{wf.name}: a container started without crun: {ln.strip()}"
                    trap = next(j for j, t in enumerate(code) if "trap" in t and 'docker rm -f "$cid"' in t)
                    assert trap < i, f"{wf.name}: the detached container's removal is not in place before it starts"
            if starts:
                seen += len(starts)
                src = next((i for i, ln in enumerate(code) if ln.strip() == SOURCE), None)
                assert src is not None and src < starts[0], f"{wf.name}: crun without {SOURCE} first: {code[starts[0]]}"
    assert seen >= 14, seen                                  # the testlab workflow's container starts, all of them


def test_the_quota_steps_container_is_the_one_its_step_removes():
    """The sizing program starts its timed build itself: the step hands it the name it removes when it ends."""
    text = (ROOT / ".github" / "workflows" / "testlab.yml").read_text()
    m = re.search(r"lhpc_wait_for (\S+) \\\n\s+python3 testlab/slowbuild/cpu_quota.py --image \"\$IMG\" --name (\S+) ",
                  text)
    assert m and m[1] == m[2], m


def _step(tmp_path, body: str, creates: bool = True):
    """A step script run as GitHub runs one (`bash -e`), with a stand-in `docker` whose `run` is the client: it records
    its pid, prints `started` and stays (no clock). With `creates`, its container exists (rm -f of its name ends it, as
    the daemon would); without, the client is still creating it, and nothing but the client's death stops that."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    created = f"echo $$ > {tmp_path}/$name.pid; " if creates else ""
    (bin_dir / "docker").write_text(f"""#!/bin/bash
echo "$*" >> {tmp_path}/calls
case "$1" in
  run)  name=$4; echo $$ > {tmp_path}/client.pid; {created}echo started; exec tail -f /dev/null ;;
  rm)   [ -f {tmp_path}/rm-fails ] && {{ cat {tmp_path}/rm-fails >&2; exit 1; }}
        for n in "${{@:3}}"; do [ -f {tmp_path}/$n.pid ] && kill "$(cat {tmp_path}/$n.pid)" 2>/dev/null; done; true ;;
esac
""")
    (bin_dir / "docker").chmod(0o755)
    step = tmp_path / "step.sh"
    step.write_text(f". {HELPER}\n{body}\n")
    env = dict(os.environ, PATH=f"{bin_dir}:{os.environ['PATH']}", RUNNER_TEMP=str(tmp_path))
    return subprocess.Popen(["bash", "-e", str(step)], stdout=subprocess.PIPE, env=env, start_new_session=True)


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


@pytest.mark.wallclock("a real step shell and its stand-in client; the reads and waits are bounded at 10 s (hang guards)")
@pytest.mark.parametrize("creates", [True, False], ids=["running", "being-created"])
@pytest.mark.parametrize("sig, rc", [(signal.SIGTERM, 143), (signal.SIGINT, 130)])
def test_a_signalled_step_ends_its_client_and_removes_its_container(tmp_path, sig, rc, creates):
    """Signalled while its container runs, or while the client is still creating it (G1-TC-T3-RE2 B2: a removal before
    the create found nothing, and the client created the container after the step had ended): the step kills and reaps
    the client, then removes the name; it goes no further."""
    proc = _step(tmp_path, f"crun img sleep 600\ntouch {tmp_path}/after", creates=creates)
    try:
        assert select.select([proc.stdout], [], [], 10)[0], "the client never started"
        assert proc.stdout.readline() == b"started\n"
        client = int((tmp_path / "client.pid").read_text())
        proc.send_signal(sig)
        assert proc.wait(10) == rc
        assert not _alive(client), "the client outlived its step: it can still create its container"
        calls = (tmp_path / "calls").read_text().splitlines()
        name = calls[0].split()[3]
        assert calls[0].split()[:3] == ["run", "--rm", "--name"] and calls[1:] == [f"rm -f {name}"], calls
        assert not (tmp_path / "after").exists()
    finally:
        if proc.poll() is None:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()
        if (tmp_path / "client.pid").exists() and _alive(client := int((tmp_path / "client.pid").read_text())):
            os.kill(client, signal.SIGKILL)


@pytest.mark.wallclock("a real step shell and its stand-in client; the reads and waits are bounded at 10 s (hang guards)")
@pytest.mark.parametrize("why, said", [("Error: No such container: x", False), ("Cannot connect to the Docker daemon", True)])
def test_a_removal_that_fails_is_said_unless_the_container_is_gone(tmp_path, why, said):
    proc = _step(tmp_path, "crun img sleep 600")
    (tmp_path / "rm-fails").write_text(why + "\n")
    try:
        assert select.select([proc.stdout], [], [], 10)[0], "the client never started"
        assert proc.stdout.readline() == b"started\n"
        proc.send_signal(signal.SIGTERM)
        assert proc.wait(10) == 143
        out = os.read(proc.stdout.fileno(), 65536).decode() if select.select([proc.stdout], [], [], 10)[0] else ""
        assert ("may still be running: docker rm -f: Cannot connect to the Docker daemon" in out) is said, out
    finally:
        if proc.poll() is None:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()
