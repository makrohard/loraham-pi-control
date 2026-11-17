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
import sys
import time
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
HELPER = ROOT / ".github" / "scripts" / "containers.sh"
SOURCE = ". .github/scripts/containers.sh"
DISK_PROBE = r'cid=\$\(docker run -d --name "lhpc-ci-disk-\$\$" "\$IMG" sleep 300\) \|\| rc=\$\?'  # wallclock: text: the probe's line


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
    helper before it. The one exception is the slow-build disk probe's detached container: exactly one such start in
    all workflows, the probe's own `cid=$(docker run -d --name lhpc-ci-...`, after the EXIT trap that removes that
    "$cid" (G2-TESTSB B4: any detached start passed once some trap removed some "$cid")."""
    seen, direct = 0, []
    for wf in sorted((ROOT / ".github" / "workflows").glob("*.yml")):
        for block in _run_blocks(wf.read_text()):
            code = [ln.split(" #", 1)[0] for ln in block if not ln.lstrip().startswith("#")]
            starts = [i for i, ln in enumerate(code) if re.search(r"\b(crun|lhpc_wait_for)\b", ln)]
            for i, ln in enumerate(code):
                assert not re.search(r"\$\(\s*(crun|lhpc_wait_for)\b", ln), f"{wf.name}: inside $(...): {ln.strip()}"
                if re.search(r"\bdocker +(?:container +)?(?:run|create)\b", ln):   # any direct start, text or not
                    direct.append(ln.strip())
                    assert re.fullmatch(DISK_PROBE, ln.strip()), f"{wf.name}: a container started without crun: {ln.strip()}"
                    trap = [j for j, t in enumerate(code[:i]) if re.fullmatch(
                        r"""trap '\[ -z "\$cid" \] \|\| docker rm -f "\$cid" >/dev/null 2>&1; true' EXIT""", t.strip())]
                    assert trap, f"{wf.name}: the detached container's removal is not in place before it starts"
            if starts:
                seen += len(starts)
                src = next((i for i, ln in enumerate(code) if ln.strip() == SOURCE), None)
                assert src is not None and src < starts[0], f"{wf.name}: crun without {SOURCE} first: {code[starts[0]]}"
    assert seen >= 14, seen                                  # the testlab workflow's container starts, all of them
    assert len(direct) == 1, direct                          # the disk probe, and nothing else


def test_every_job_that_starts_containers_ends_with_the_sweep():
    """G2-TESTSA B1: a create the daemon finishes after its step's removals is removed by the job's last step, which
    runs whatever came before (if: always()) and removes every lhpc-ci-* name."""
    jobs = 0
    for wf in sorted((ROOT / ".github" / "workflows").glob("*.yml")):
        for name, job in (yaml.safe_load(wf.read_text()).get("jobs") or {}).items():
            runs = [st.get("run", "") for st in job.get("steps", [])]
            if not any(SOURCE in r or "docker run" in r for r in runs):
                continue
            jobs += 1
            last = job["steps"][-1]
            assert last.get("if") == "always()" and re.search(r"(?m)^lhpc_sweep$", last.get("run", "")), \
                f"{wf.name}: job {name} does not end with the sweep: {last.get('name')}"
    assert jobs == 3, jobs                                   # testlab, release-verify, slow-build


def test_the_quota_steps_container_is_the_one_its_step_removes():
    """The sizing program starts its timed build itself: the step hands it the name it removes when it ends."""
    text = (ROOT / ".github" / "workflows" / "testlab.yml").read_text()
    m = re.search(r"lhpc_wait_for (\S+) \\\n\s+python3 testlab/slowbuild/cpu_quota.py --image \"\$IMG\" --name (\S+) ",
                  text)
    assert m and m[1] == m[2], m


def _step(tmp_path, body: str, creates: str = "running", cwd: Path | None = None):
    """A step script run as GitHub runs one (`bash -e`, its own process group) with a stand-in `docker`. Its `run` is the client:
    it records its pid, says it started (a line on stdout, and a byte into tmp/ready when that FIFO exists), and stays;
    like a real attached client it ignores TERM and INT (it forwards them), so only KILL ends it. Its container:
    `running` exists at once; `being-created` does not exist yet, and nothing but the client's death stops it;
    `late` is a create the daemon already holds: it lands at the removal's second check (more than two check
    intervals after the kill: the first removal and the first check found nothing), from a detached daemon side that
    says so into tmp/created;
    `exits` ends at once with 0; `apart` is a container that runs on its own, apart from its client (a client killed
    alone, by OOM or KILL, leaves it running). A `run` of a name that is in use is refused (125), as docker refuses it.
    `rm -f` ends a container that exists; `container inspect` says whether it does, and an absent one by docker's
    words (always yes with tmp/stuck: a container the daemon will not remove; tmp/inspect-fails: the inspection itself
    fails with that file's words); `ps -aq --filter name=X` lists the containers whose name holds X."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    t = tmp_path
    create = {"running": f"echo $$ > {t}/$name.pid; ",
              "being-created": "",
              "late": f"echo $name > {t}/pending; ",
              "exits": "",
              "apart": f"setsid tail -f /dev/null </dev/null >/dev/null 2>&1 & echo $! > {t}/$name.pid; "}[creates]
    # stdout away from the pipe: `tail -f` ends by itself once a pipe on its stdout loses its reader; a client does not
    stay = "exit 0" if creates == "exits" else "trap '' TERM INT; exec tail -f /dev/null >/dev/null"
    (bin_dir / "docker").write_text(f"""#!/bin/bash
echo "$*" >> {t}/calls
case "$1" in
  run)       name=$4
             if [ -f {t}/$name.pid ] && kill -0 "$(cat {t}/$name.pid)" 2>/dev/null; then
               echo "Conflict. The container name \\"/$name\\" is already in use" >&2; exit 125; fi
             echo $$ > {t}/client.pid; {create}echo started; [ -p {t}/ready ] && echo r > {t}/ready; {stay} ;;
  rm)        [ -f {t}/rm-fails ] && {{ cat {t}/rm-fails >&2; exit 1; }}
             for n in "${{@:3}}"; do
               [ -f {t}/$n.pid ] && kill -KILL "$(cat {t}/$n.pid)" 2>/dev/null; rm -f {t}/$n.pid
             done; true ;;
  container) n=$3; echo x >> {t}/inspected
             if [ "$(wc -l < {t}/inspected)" -eq 2 ] && [ "$(cat {t}/pending 2>/dev/null)" = "$n" ]; then rm -f {t}/pending
               setsid bash -c "echo \\$\\$ > {t}/daemon.pid; echo \\$\\$ > {t}/$n.pid; [ -p {t}/created ] && echo c > {t}/created; exec tail -f /dev/null >/dev/null" </dev/null >/dev/null 2>&1 &
               echo "Error: No such container: $n" >&2; exit 1   # not there yet: it lands after this second check
             fi
             [ -f {t}/inspect-fails ] && {{ cat {t}/inspect-fails >&2; exit 1; }}
             [ -f {t}/stuck ] || {{ [ -f {t}/$n.pid ] && kill -0 "$(cat {t}/$n.pid)" 2>/dev/null; }} \
               || {{ echo "Error: No such container: $n" >&2; exit 1; }} ;;
  ps)        want=${{4#name=}}
             for f in {t}/*.pid; do n=$(basename "$f" .pid)
               case "$n" in client|daemon) continue ;; esac
               [[ $n == *"$want"* ]] && kill -0 "$(cat "$f")" 2>/dev/null && echo "$n"
             done; true ;;
esac
""")
    (bin_dir / "docker").chmod(0o755)
    (bin_dir / "python3").symlink_to(sys.executable)
    step = tmp_path / "step.sh"
    step.write_text(f". {HELPER}\n{body}\n")
    env = dict(os.environ, PATH=f"{bin_dir}:{os.environ['PATH']}", RUNNER_TEMP=str(t), LHPC_RECHECK_S="0.5",
               GITHUB_ENV=str(t / "github_env"))
    return subprocess.Popen(["bash", "-e", str(step)], stdout=subprocess.PIPE, env=env, cwd=cwd or t,
                            start_new_session=True)


def _run_name(tmp_path) -> str:
    """The name the step's `docker run` used."""
    return next(c.split()[3] for c in (tmp_path / "calls").read_text().splitlines() if c.startswith("run "))  # rule-10: selection


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def _ended(pid: int, timeout: float = 5.0) -> bool:
    """True once `pid` has exited (a zombie not yet reaped by init counts: kill -0 still finds those)."""
    try:
        fd = os.pidfd_open(pid)
    except ProcessLookupError:
        return True
    try:
        return bool(select.select([fd], [], [], timeout)[0])
    finally:
        os.close(fd)


def _rest(proc, timeout: float = 10.0) -> str:
    """What the ended step printed after the lines already read (bounded: a pipe still held elsewhere never hangs)."""
    return os.read(proc.stdout.fileno(), 65536).decode() if select.select([proc.stdout], [], [], timeout)[0] else ""


def _pid(path: Path):
    return int(path.read_text()) if path.exists() else None


def _end(tmp_path, proc):
    """Whatever a failing run left: the step's process group, the stand-in client, its daemon side."""
    if proc.poll() is None:
        os.killpg(proc.pid, signal.SIGKILL)
        proc.wait()
    for f in ("client.pid", "daemon.pid"):
        pid = _pid(tmp_path / f)
        if pid and _alive(pid):
            os.kill(pid, signal.SIGKILL)


def _line(proc, timeout: float = 10.0) -> bytes:
    """The step's next line of stdout, read without blocking past `timeout` (a partial line never hangs the test)."""
    fd, got, end = proc.stdout.fileno(), b"", time.monotonic() + timeout  # wallclock: the read's own bound, no sync
    while not got.endswith(b"\n"):
        left = end - time.monotonic()                # wallclock: the read's own bound, no sync
        assert left > 0 and select.select([fd], [], [], left)[0], f"no whole line within {timeout:g} s: {got!r}"
        b = os.read(fd, 1)                       # one byte: nothing past this line is taken from the pipe
        assert b, f"the step closed its stdout mid-line: {got!r}"
        got += b
    return got


def _started(tmp_path, proc):
    assert _line(proc) == b"started\n", "the client never started"


@pytest.mark.wallclock("a real step shell; the removal looks for a late create for 5 x 0.5 s; waits bounded at 10 s")
@pytest.mark.parametrize("creates", ["running", "being-created", "late"])
@pytest.mark.parametrize("sig, rc, group", [(signal.SIGTERM, 143, False), (signal.SIGINT, 130, False),
                                            (signal.SIGTERM, 143, True)], ids=["TERM", "INT", "TERM-to-the-group"])
def test_a_signalled_step_ends_its_client_and_removes_its_container(tmp_path, sig, rc, group, creates):
    """Signalled while its container runs, while the client is still creating it (G1-TC-T3-RE2 B2), or while the
    daemon still holds a create that lands after the first removal: the step kills the client's process group, removes the
    name until it stays gone, and goes no further. A signal to the step's whole group ends it the same way (r6 second
    read N3: it ended with 1 once the group signal had killed the client first)."""
    os.mkfifo(tmp_path / "created")
    created = os.open(tmp_path / "created", os.O_RDWR)    # held open: the daemon side's announcement never blocks
    proc = _step(tmp_path, f"crun img sleep 600\ntouch {tmp_path}/after", creates=creates)
    try:
        _started(tmp_path, proc)
        client = _pid(tmp_path / "client.pid")
        os.killpg(proc.pid, sig) if group else proc.send_signal(sig)
        assert proc.wait(10) == rc
        assert not _alive(client), "the client outlived its step: it can still create its container"
        if creates == "late":
            assert select.select([created], [], [], 10)[0], "the late create never landed"
        name = _run_name(tmp_path)
        assert subprocess.run([str(tmp_path / "bin" / "docker"), "container", "inspect", name]).returncode == 1, \
            f"{name} is still there"
        assert not (tmp_path / "after").exists()
    finally:
        os.close(created)
        _end(tmp_path, proc)


@pytest.mark.parametrize("body, rc", [("echo done", 0),                        # no container at all
                                      ("crun img true\necho done", 0),          # one that ended by itself
                                      ("crun img true\nfalse", 1)])            # the step's own failure
def test_a_step_that_ends_by_itself_keeps_its_own_status(tmp_path, body, rc):
    """G1-TC-T3 r6 second read B1: the EXIT trap ended every such step with 2 under `bash -e` (a kill with no pid)."""
    proc = _step(tmp_path, body, creates="exits")
    try:
        assert proc.wait(10) == rc
    finally:
        _end(tmp_path, proc)


@pytest.mark.wallclock("the real sizing program and a step shell; waits bounded at 10 s")
def test_the_quota_step_signalled_mid_create_leaves_no_client_and_no_container(tmp_path):
    """The step's own quota lines (sourced helper, lhpc_wait_for, the program's --name): signalled while the program's
    docker client is still creating its container, the step kills the program AND its client (r6 second read B2: the
    client was orphaned and created the container after the removal)."""
    text = (ROOT / ".github" / "workflows" / "testlab.yml").read_text()
    m = re.search(r"\n( +)(lhpc_wait_for lhpc-ci-quota-\$\$ \\\n.*\n)", text)
    lines = m[2].replace("\n" + m[1], "\n")
    os.mkfifo(tmp_path / "ready")
    fd = os.open(tmp_path / "ready", os.O_RDWR)            # a reader from the start: the stand-in's write never blocks
    proc = _step(tmp_path, lines.replace('"$IMG"', "img") + f"touch {tmp_path}/after", creates="being-created", cwd=ROOT)
    try:
        assert select.select([fd], [], [], 10)[0], "the program's client never started"
        client = _pid(tmp_path / "client.pid")
        proc.send_signal(signal.SIGTERM)
        assert proc.wait(10) == 143
        assert not _alive(client), "the program's docker client outlived the step"
        calls = (tmp_path / "calls").read_text().splitlines()
        name = _run_name(tmp_path)
        assert re.fullmatch(r"lhpc-ci-quota-\d+", name) and calls.count(f"rm -f {name}") >= 2, calls   # before + after
        assert not (tmp_path / "after").exists()
    finally:
        os.close(fd)
        _end(tmp_path, proc)


@pytest.mark.wallclock("a real step shell; the removal looks for a late create for 5 x 0.5 s; waits bounded at 10 s")
@pytest.mark.parametrize("why, said", [("Error: No such container: x", False), ("Cannot connect to the Docker daemon", True)])
def test_a_removal_that_fails_is_said_unless_the_container_is_gone(tmp_path, why, said):
    proc = _step(tmp_path, "crun img sleep 600")
    try:
        _started(tmp_path, proc)                           # past the pre-clean: only the step's end meets the failure
        (tmp_path / "rm-fails").write_text(why + "\n")
        if said:
            (tmp_path / "stuck").touch()                   # the daemon cannot remove it: it stays
        proc.send_signal(signal.SIGTERM)
        assert proc.wait(10) == 143
        out = os.read(proc.stdout.fileno(), 65536).decode() if select.select([proc.stdout], [], [], 10)[0] else ""
        assert ("::warning::" in out and "Cannot connect to the Docker daemon" in out) is said, out
        assert ("is still there after the step's removals" in out) is said, out    # it survived: said once more
    finally:
        _end(tmp_path, proc)


def test_a_leftover_under_the_name_never_blocks_the_next_create(tmp_path):
    """A create that landed after a step's checks leaves a container under its name (r7 delta read N4). The next use of
    that name removes it first, so its create is not refused ("name already in use") and the step succeeds."""
    leftover = subprocess.Popen(["tail", "-f", "/dev/null"], stdout=subprocess.DEVNULL, start_new_session=True)
    (tmp_path / "fixed-x.pid").write_text(f"{leftover.pid}\n")
    proc = _step(tmp_path, "lhpc_wait_for fixed-x docker run --rm --name fixed-x img true", creates="exits")
    try:
        assert proc.wait(10) == 0
        assert (tmp_path / "calls").read_text().splitlines()[:2] == ["rm -f fixed-x", "run --rm --name fixed-x img true"]
    finally:
        _end(tmp_path, proc)
        leftover.kill()
        leftover.wait()


@pytest.mark.wallclock("a real step shell and a stand-in container process; waits bounded at 10 s")
def test_a_client_that_ends_alone_leaves_no_container_running(tmp_path):
    """G2-TESTSA B3: the client killed on its own (KILL, OOM) while its container runs: the step goes on, as it would,
    and the container is removed by its name once the client's wait returns (the name was dropped unremoved)."""
    proc = _step(tmp_path, f"crun img sleep 600 || echo \"client rc $?\"\ntouch {tmp_path}/after", creates="apart")
    try:
        _started(tmp_path, proc)
        name = _run_name(tmp_path)
        box = _pid(tmp_path / f"{name}.pid")
        os.kill(_pid(tmp_path / "client.pid"), signal.SIGKILL)
        assert proc.wait(10) == 0
        assert _line(proc) == b"client rc 137\n"            # its status kept
        assert (tmp_path / "after").exists()
        assert _ended(box), f"{name} runs on after its client and its step ended"
    finally:
        _end(tmp_path, proc)
        box = _pid(tmp_path / f"{_run_name(tmp_path)}.pid")
        if box and _alive(box):
            os.kill(box, signal.SIGKILL)


@pytest.mark.wallclock("a real step shell; the removal looks for a late create for 5 x 0.5 s; waits bounded at 10 s")
def test_a_second_signal_does_not_cut_the_removal_short(tmp_path):
    """G2-TESTSA B4: the runner sends INT, then TERM 7.5 s later. A TERM that comes while the step's removal still
    looks for a late create no longer ends that removal: the create that lands after it is removed, the status is INT's."""
    os.mkfifo(tmp_path / "created")
    created = os.open(tmp_path / "created", os.O_RDWR)
    proc = _step(tmp_path, "crun img sleep 600", creates="late")
    try:
        _started(tmp_path, proc)
        proc.send_signal(signal.SIGINT)
        assert select.select([created], [], [], 10)[0], "the late create never landed"
        proc.send_signal(signal.SIGTERM)                     # mid-removal: the create is there, its removal to come
        assert proc.wait(10) == 130
        name = _run_name(tmp_path)
        assert _ended(_pid(tmp_path / "daemon.pid")), f"{name}: the late create outlived its step"
    finally:
        os.close(created)
        _end(tmp_path, proc)


@pytest.mark.wallclock("a real step shell; the removal looks for a late create for 5 x 0.5 s; waits bounded at 10 s")
def test_an_inspection_that_fails_is_said_not_taken_for_absence(tmp_path):
    """G2-TESTSA N1: a failed `docker container inspect` (the daemon unreachable) is not "no such container": it is
    said, and the removal is tried again rather than skipped."""
    proc = _step(tmp_path, "crun img sleep 600")
    try:
        _started(tmp_path, proc)
        (tmp_path / "inspect-fails").write_text("Cannot connect to the Docker daemon\n")
        proc.send_signal(signal.SIGTERM)
        assert proc.wait(10) == 143
        out = _rest(proc)
        assert "::warning::docker container inspect lhpc-ci-" in out and "Cannot connect to the Docker daemon" in out, out
        rms = [c for c in (tmp_path / "calls").read_text().splitlines() if c.startswith("rm -f ")]  # rule-10: selection, counted
        assert len(rms) >= 3, rms                            # before, at the end, and on each failed look
    finally:
        _end(tmp_path, proc)


@pytest.mark.parametrize("left", [[], ["lhpc-ci-1-2", "lhpc-ci-quota-3"]], ids=["none", "two"])
def test_the_sweep_removes_every_container_a_step_left(tmp_path, left):
    """G2-TESTSA B1: the job's last step removes whatever lhpc-ci-* container is there (a create that landed after its
    step's removals), says which, and leaves others alone."""
    boxes = {n: subprocess.Popen(["tail", "-f", "/dev/null"], stdout=subprocess.DEVNULL, start_new_session=True)
             for n in [*left, "someone-else"]}
    for n, p in boxes.items():
        (tmp_path / f"{n}.pid").write_text(f"{p.pid}\n")
    proc = _step(tmp_path, "lhpc_sweep", creates="exits")
    try:
        assert proc.wait(10) == 0
        out = _rest(proc)
        for n in left:
            boxes[n].wait(5)                                 # removed: it ends (TimeoutExpired if it does not)
        assert boxes["someone-else"].poll() is None          # not ours: left alone
        assert (out == "") is (not left), out
        assert all(n in out for n in left), out
    finally:
        _end(tmp_path, proc)
        for p in boxes.values():
            p.kill()
            p.wait()
