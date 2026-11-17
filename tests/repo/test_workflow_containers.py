"""Every container a workflow step starts is removed by its name (.github/scripts/containers.sh), within one bound.

The docker client's end never ended its container: a step killed by a job timeout or a cancel left the container
running, and `--rm` removes a container only once it exits (G1-TC-T3-RE B1, the class). A step starts each container
under a name of this run (`crun`, `lhpc_wait_for`, `lhpc_start`) and owns that name until `lhpc_clean` has removed
it: while the client's process group lives (it may still be creating the container), then once more, all within
LHPC_CLEAN_S; a failure fails the step. A create that a client dying mid-create left behind is the job sweep's, which
fails the job; after the job the hosted runner is discarded (G2-TESTSD fix plan v1)."""
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
WORKFLOWS = sorted((ROOT / ".github" / "workflows").glob("*.yml"))
PROBE = re.compile(r'lhpc_start "\$name" docker run -d --name "\$name" .*')   # the disk probe's start, and no other
RUN = {"GITHUB_RUN_ID": "7", "GITHUB_RUN_ATTEMPT": "1"}
PREFIX = "lhpc-ci-7-1-"


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


def _container_jobs():
    """(workflow, job name, job) of every job that starts a container."""
    for wf in WORKFLOWS:
        for name, job in (yaml.safe_load(wf.read_text()).get("jobs") or {}).items():
            if any(SOURCE in st.get("run", "") or "docker run" in st.get("run", "") for st in job.get("steps", [])):
                yield wf, name, job


def test_every_container_a_workflow_starts_is_owned_by_its_step():
    """A container starts only through `crun`, `lhpc_wait_for` or `lhpc_start` (each takes the name first), never
    inside $(...) (bash holds its traps until a substitution ends), in a step that sources the helper before it. The
    one direct `docker run` is the disk probe's, as the argument of `lhpc_start` under the same name (G2-TESTSD B5: its
    `cid=$(docker run -d ...)` left the named container to nobody when a signal came during the substitution)."""
    seen, probes = 0, []
    for wf in WORKFLOWS:
        for block in _run_blocks(wf.read_text()):
            code = [ln.split(" #", 1)[0] for ln in block if not ln.lstrip().startswith("#")]
            starts = [i for i, ln in enumerate(code) if re.search(r"\b(crun|lhpc_wait_for|lhpc_start)\b", ln)]
            for ln in code:
                assert not re.search(r"\$\(\s*(crun|lhpc_wait_for|lhpc_start)\b", ln), f"{wf.name}: inside $(...): {ln}"
                said = re.sub(r'''\becho +(?:"[^"]*"|'[^']*')''', "echo", ln)   # a message's text starts nothing
                if re.search(r"\bdocker +(?:container +)?(?:run|create)\b", said):   # any start, quoted or not
                    assert PROBE.fullmatch(ln.strip().split(" >", 1)[0]), f"{wf.name}: a container no step owns: {ln}"
                    probes.append(ln.strip())
            if starts:
                seen += len(starts)
                src = next((i for i, ln in enumerate(code) if ln.strip() == SOURCE), None)
                assert src is not None and src < starts[0], f"{wf.name}: a start without {SOURCE} first: {code[starts[0]]}"
    assert seen >= 15, seen                                  # the testlab workflow's container starts, all of them
    assert len(probes) == 1, probes


def _sweeps():
    for wf, name, job in _container_jobs():
        yield wf, name, job, job["steps"][-1]


def test_every_container_job_ends_with_one_inline_sweep_on_a_hosted_runner():
    """G2-TESTSD B2/B7/N2: the job's last step runs whatever came before (if: always()), is the same inline text in
    every such job (no checkout, no helper: B7), and lists this run's names only, anchored. A create landing after it
    is the hosted runner's: every such job runs on a GitHub-hosted label, whose machine is discarded after the job."""
    texts = set()
    for wf, name, job, last in _sweeps():
        assert last.get("if") == "always()", f"{wf.name}: job {name} does not end with the sweep: {last.get('name')}"
        assert ".github/" not in last["run"] and "containers.sh" not in last["run"], last["run"]
        assert 'prefix="lhpc-ci-${GITHUB_RUN_ID}-${GITHUB_RUN_ATTEMPT}-"' in last["run"], last["run"]
        assert '--filter "name=^/?$prefix"' in last["run"], last["run"]
        assert re.fullmatch(r"ubuntu-[0-9.]+(-arm)?", job["runs-on"]), (name, job["runs-on"])   # GitHub-hosted
        texts.add(last["run"])
    assert len(texts) == 1, texts
    assert len(list(_sweeps())) == 3                         # testlab, release-verify, slow-build


def test_the_quota_steps_container_is_the_one_its_step_removes():
    """The sizing program starts its timed build itself: the step hands it the name it owns."""
    text = (ROOT / ".github" / "workflows" / "testlab.yml").read_text()
    m = re.search(r"lhpc_wait_for (\S+) \\\n\s+python3 testlab/slowbuild/cpu_quota.py --image \"\$IMG\" --name (\S+) ",
                  text)
    assert m and m[1] == m[2] == '"${LHPC_CI_PREFIX}quota-$$"', m


def _docker(tmp_path: Path, mode: str) -> Path:
    """A stand-in `docker` in tmp/bin. `run` is the client: it records its pid (tmp/client.pid), and like a real
    attached client ignores TERM and INT and ends when its container is removed (137), or, for `run -d`, once the
    container exists (0). Its container is a process of its own (tmp/<name>.pid; every one in tmp/boxes):
    `running` exists at once; `pending` is a create the daemon holds: it lands just after a `rm -f` that found no
    such container; `exits`: the client ends at once with 0, no container; `deaf`: the container exists, the client
    never ends by itself. `rm -f NAME...` ends each container that exists, says "No such container" for the others
    (rc 1); tmp/rm-fails: it fails with that file's words; tmp/hang-rm: it hangs. `ps -aq --filter name=RE` lists
    the containers whose "/name" matches RE (tmp/ps-fails, tmp/hang-ps as for rm)."""
    t, b = tmp_path, tmp_path / "bin"
    b.mkdir()
    (t / "mode").write_text(mode)
    (b / "docker").write_text(
        "#!/bin/bash\n"
        f'echo "$*" >> {t}/calls\n'
        f'box() {{ setsid tail -f /dev/null </dev/null >/dev/null 2>&1 & echo $! > {t}/$1.pid; echo $! >> {t}/boxes; }}\n'
        'case "$1" in\n'
        f'  run) name=$4; echo $$ > {t}/client.pid; trap "" TERM INT; mode=$(cat {t}/mode)\n'
        '       [ "$mode" = exits ] && exit 0\n'
        f'       if [ "$mode" = pending ]; then touch {t}/$name.pending; else box "$name"; fi\n'
        f'       echo started; [ -p {t}/ready ] && echo r > {t}/ready\n'
        '       [ "$mode" = deaf ] && exec tail -f /dev/null >/dev/null\n'
        f'       until [ -s {t}/$name.pid ]; do sleep 0.05; done\n'                          # wallclock: the stand-in's poll
        '       [ "$2" = -d ] && exit 0\n'
        f'       while kill -0 "$(cat {t}/$name.pid 2>/dev/null)" 2>/dev/null; do sleep 0.05; done\n'  # wallclock: poll
        '       exit 137 ;;\n'
        f'  rm)  shift; [ "$1" = -f ] && shift; [ -f {t}/hang-rm ] && exec tail -f /dev/null\n'
        f'       [ -f {t}/rm-fails ] && {{ cat {t}/rm-fails >&2; exit 1; }}\n'
        '       st=0\n'
        '       for n in "$@"; do\n'
        f'         if [ -f {t}/$n.pid ]; then kill -KILL "$(cat {t}/$n.pid)" 2>/dev/null; rm -f {t}/$n.pid\n'
        '         else echo "Error response from daemon: No such container: $n" >&2; st=1\n'
        f'              if [ -f {t}/$n.pending ]; then rm -f {t}/$n.pending; box "$n"; fi\n'   # the held create lands
        '         fi\n'
        '       done; exit $st ;;\n'
        f'  ps)  [ -f {t}/hang-ps ] && exec tail -f /dev/null; [ -f {t}/ps-fails ] && {{ cat {t}/ps-fails >&2; exit 1; }}\n'
        '       re=${4#name=}\n'
        f'       for f in {t}/*.pid; do n=$(basename "$f" .pid); [ "$n" = client ] && continue\n'
        '         [[ "/$n" =~ $re ]] && kill -0 "$(cat "$f")" 2>/dev/null && echo "$n"\n'
        '       done; true ;;\n'
        "esac\n")
    (b / "docker").chmod(0o755)
    (b / "python3").symlink_to(sys.executable)
    return b


def _env(tmp_path: Path, **extra) -> dict:
    return dict(os.environ, PATH=f"{tmp_path / 'bin'}:{os.environ['PATH']}", GITHUB_ENV=str(tmp_path / "github_env"),
                **{"LHPC_CLEAN_S": "2", "LHPC_POLL_S": "0.1", **RUN, **extra})


def _step(tmp_path, body: str, mode: str = "running", cwd: Path | None = None, **extra):
    """A step script run as GitHub runs one (`bash -e`, its own process group) with the stand-in `docker`."""
    _docker(tmp_path, mode)
    step = tmp_path / "step.sh"
    step.write_text(f". {HELPER}\n{body}\n")
    return subprocess.Popen(["bash", "-e", str(step)], stdout=subprocess.PIPE, env=_env(tmp_path, **extra),
                            cwd=cwd or tmp_path, start_new_session=True)


def _run_name(tmp_path) -> str:
    """The name the step's `docker run` used."""
    return next(c.split()[3] for c in (tmp_path / "calls").read_text().splitlines() if c.startswith("run "))  # rule-10: selection


def _calls(tmp_path) -> list[str]:
    return (tmp_path / "calls").read_text().splitlines()


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


def _pid(path: Path):
    return int(path.read_text()) if path.exists() else None


def _boxes(tmp_path) -> list[int]:
    f = tmp_path / "boxes"
    return [int(p) for p in f.read_text().split()] if f.exists() else []


def _end(tmp_path, proc=None):
    """Whatever a failing run left: the step's process group, the stand-in client, every container."""
    if proc is not None and proc.poll() is None:
        os.killpg(proc.pid, signal.SIGKILL)
        proc.wait()
    for pid in [_pid(tmp_path / "client.pid"), *_boxes(tmp_path)]:
        if pid and _alive(pid):
            os.kill(pid, signal.SIGKILL)


def _rest(proc, timeout: float = 10.0) -> str:
    """What the ended step printed after the lines already read (bounded: a pipe still held elsewhere never hangs)."""
    return os.read(proc.stdout.fileno(), 65536).decode() if select.select([proc.stdout], [], [], timeout)[0] else ""


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


def _started(proc):
    assert _line(proc) == b"started\n", "the client never started"


def _gone(tmp_path, name: str):
    assert not (tmp_path / f"{name}.pid").exists(), f"{name} is still there"
    assert all(_ended(b) for b in _boxes(tmp_path)), "a container runs on"


@pytest.mark.wallclock("a real step shell; its cleanup polls every 0.1 s; waits bounded at 10 s")
@pytest.mark.parametrize("mode", ["running", "pending"])
@pytest.mark.parametrize("sig, rc, group", [(signal.SIGTERM, 143, False), (signal.SIGINT, 130, False),
                                            (signal.SIGTERM, 143, True)], ids=["TERM", "INT", "TERM-to-the-group"])
def test_a_signalled_step_removes_its_container_while_its_client_lives(tmp_path, sig, rc, group, mode):
    """Signalled while its container runs, or while the daemon still holds its create (it lands after the first
    removal: TESTSA B1's input): the step keeps removing the name while the client lives, so the late create is
    removed too and the client ends with it; then the step goes no further. A signal to the step's whole group ends
    it the same way (the client is in a group of its own)."""
    proc = _step(tmp_path, f"crun img sleep 600\ntouch {tmp_path}/after", mode=mode)
    try:
        _started(proc)
        client = _pid(tmp_path / "client.pid")
        os.killpg(proc.pid, sig) if group else proc.send_signal(sig)
        assert proc.wait(10) == rc
        assert _ended(client), "the client outlived its step: it can still create its container"
        _gone(tmp_path, _run_name(tmp_path))
        assert len(_boxes(tmp_path)) == 1, "the held create never landed"
        assert "::error::" not in _rest(proc)
        assert not (tmp_path / "after").exists()
    finally:
        _end(tmp_path, proc)


@pytest.mark.parametrize("body, rc", [("echo done", 0),                        # no container at all
                                      ("crun img true\necho done", 0),          # one that ended by itself
                                      ("crun img true\nfalse", 1)])            # the step's own failure
def test_a_step_that_ends_by_itself_keeps_its_own_status(tmp_path, body, rc):
    """G1-TC-T3 r6 second read B1: the EXIT trap ended every such step with 2 under `bash -e` (a kill with no pid)."""
    proc = _step(tmp_path, body, mode="exits")
    try:
        assert proc.wait(10) == rc
        assert "::error::" not in _rest(proc)
    finally:
        _end(tmp_path, proc)


@pytest.mark.wallclock("a real step shell; waits bounded at 10 s")
@pytest.mark.parametrize("why", ["Cannot connect to the Docker daemon", "Error response from daemon: busy"])
def test_a_removal_that_fails_fails_the_step(tmp_path, why):
    """G2-TESTSD B4: a removal that failed (not "no such container") was only warned of, and the step succeeded."""
    (tmp_path / "rm-fails").write_text(why + "\n")
    proc = _step(tmp_path, "crun img true\necho done", mode="exits")
    try:
        assert proc.wait(10) == 1
        out = _rest(proc)
        assert re.search(rf"::error::container {PREFIX}\S+: its removal failed \(docker rm -f rc 1\): {why}", out), out
        assert "done" not in out
    finally:
        _end(tmp_path, proc)


@pytest.mark.wallclock("a real step shell under a 2 s cleanup bound, a hanging docker; waits bounded at 15 s")
def test_a_hanging_removal_ends_within_the_bound_and_fails_the_step(tmp_path):
    """G2-TESTSD B3: `docker rm -f` had no deadline: a hung daemon hung the step's cleanup until the runner's KILL.
    Every docker call of the cleanup runs under the time left (at least 1 s); a timed-out removal is a failure."""
    proc = _step(tmp_path, "crun img sleep 600")
    try:
        _started(proc)
        (tmp_path / "hang-rm").touch()
        proc.send_signal(signal.SIGTERM)
        assert proc.wait(15) == 143
        assert re.search(r"::error::container \S+: its removal failed \(docker rm -f rc 124\)", _rest(proc))
    finally:
        _end(tmp_path, proc)


@pytest.mark.wallclock("a real step shell under a 2 s cleanup bound; waits bounded at 15 s")
def test_a_client_still_there_at_the_bound_is_killed_and_said(tmp_path):
    """A client that never ends (not even once its container is removed) is killed at the cleanup's deadline, the name
    is removed once more, and the step fails saying a create it held may land later (the boundary)."""
    proc = _step(tmp_path, "crun img sleep 600", mode="deaf")
    try:
        _started(proc)
        client = _pid(tmp_path / "client.pid")
        proc.send_signal(signal.SIGTERM)
        assert proc.wait(15) == 143
        out = _rest(proc)
        assert re.search(r"::error::container \S+: its client was still there after 2 s and was killed; a create it "
                         r"held can land after this step", out), out
        assert _ended(client)
        _gone(tmp_path, _run_name(tmp_path))
    finally:
        _end(tmp_path, proc)


@pytest.mark.wallclock("a real step shell under a 2 s cleanup bound; waits bounded at 15 s")
def test_a_second_signal_during_the_exit_cleanup_does_not_cut_it_short(tmp_path):
    """G2-TESTSA B4 / G2-TESTSD B6: the runner sends INT, then TERM 7.5 s later. A TERM during the step's cleanup
    (here it runs to its deadline: the client never ends) does not end it: the client is killed, the name removed
    once more, and the status is INT's."""
    proc = _step(tmp_path, "crun img sleep 600", mode="deaf")
    try:
        _started(proc)
        client = _pid(tmp_path / "client.pid")
        proc.send_signal(signal.SIGINT)
        n = _wait_calls(tmp_path, "rm -f ", 1)
        proc.send_signal(signal.SIGTERM)                     # mid-cleanup
        assert proc.wait(15) == 130
        assert "was killed" in _rest(proc)
        assert _ended(client)
        assert len([c for c in _calls(tmp_path) if c.startswith("rm -f ")]) > n   # rule-10: selection, counted
    finally:
        _end(tmp_path, proc)


def _wait_calls(tmp_path, prefix: str, n: int, timeout: float = 10.0) -> int:
    """Wait (bounded) until the stand-in has had at least `n` calls starting with `prefix`; their count."""
    end = time.monotonic() + timeout                               # wallclock: the wait's own bound
    while True:
        calls = (tmp_path / "calls").read_text().splitlines() if (tmp_path / "calls").exists() else []
        got = len([c for c in calls if c.startswith(prefix)])     # rule-10: selection, counted
        if got >= n:
            return got
        assert time.monotonic() < end, f"fewer than {n} calls {prefix!r} within {timeout:g} s"   # wallclock: bound
        time.sleep(0.05)                                           # wallclock: the stand-in's calls file has no event


@pytest.mark.wallclock("a real step shell under a 2 s cleanup bound and a group member that never ends; bounded 15 s")
def test_a_signal_during_a_cleanup_after_the_client_ended_lets_it_finish(tmp_path):
    """G2-TESTSD B6 / plan look C2: a TERM that comes while a client's normal-end cleanup runs (here: a member of its
    group lives on, so the cleanup runs to its deadline) is held until that cleanup has finished (the member killed,
    the name removed), then ends the step with its status. A handler that `return`ed from the trap string would have
    returned from the cleanup itself, cutting it short."""
    member = tmp_path / "member.pid"
    proc = _step(tmp_path, f"lhpc_wait_for {PREFIX}x bash -c '(trap \"\" TERM INT; exec tail -f /dev/null) "
                           f"& echo $! > {member}; echo started'\ntouch {tmp_path}/after", mode="exits")
    try:
        _started(proc)
        n = _wait_calls(tmp_path, "rm -f ", 1)
        proc.send_signal(signal.SIGTERM)                     # mid-cleanup, outside the EXIT trap
        assert proc.wait(15) == 143
        assert "was killed" in _rest(proc)
        assert _ended(_pid(member)), "the group's member outlived the cleanup"
        assert len([c for c in _calls(tmp_path) if c.startswith("rm -f ")]) > n   # rule-10: selection, counted
        assert not (tmp_path / "after").exists()
    finally:
        _end(tmp_path, proc)
        if member.exists() and _alive(_pid(member)):
            os.kill(_pid(member), signal.SIGKILL)


@pytest.mark.wallclock("a real step shell under a 4 s cleanup bound, signalled 3 s into it; waits bounded at 15 s")
def test_a_signal_late_in_a_cleanup_keeps_its_one_deadline(tmp_path):
    """G2-TESTSD B3/B6: a TERM that ended the step from inside a client's normal-end cleanup left the name to the EXIT
    cleanup, which started its deadline over: up to twice LHPC_CLEAN_S, past the runner's KILL. The signal is held
    and the cleanup ends by its own deadline (its loop, one last removal and a poll)."""
    member = tmp_path / "member.pid"
    proc = _step(tmp_path, f"lhpc_wait_for {PREFIX}x bash -c '(trap \"\" TERM INT; exec tail -f /dev/null) "
                           f"& echo $! > {member}; echo started'", mode="exits", LHPC_CLEAN_S="4")
    try:
        _started(proc)
        _wait_calls(tmp_path, "rm -f ", 1)
        first = time.monotonic()                             # wallclock: the cleanup's first removal
        time.sleep(3)                                        # wallclock: 3 s into its 4 s bound
        proc.send_signal(signal.SIGTERM)
        assert proc.wait(15) == 143
        took = time.monotonic() - first                      # wallclock: against the bound; a restart takes 7 s
        assert took < 5.5, f"the cleanup ended {took:.1f} s after its first removal: its deadline started over"
        assert "was killed" in _rest(proc)
        assert _ended(_pid(member)), "the group's member outlived the cleanup"
    finally:
        _end(tmp_path, proc)
        if member.exists() and _alive(_pid(member)):
            os.kill(_pid(member), signal.SIGKILL)


@pytest.mark.wallclock("a real step shell; its EXIT cleanup's removal hangs under a 2 s bound; waits bounded at 15 s")
def test_a_signal_during_the_exit_cleanup_of_a_step_that_failed_by_itself_is_held(tmp_path):
    """G2-TESTSD B6: the EXIT cleanup of a step that ended without a signal (here: an error after the probe started) has
    no `trap ''` of its own; a TERM during it is held by the one guard, so the cleanup ends by its bound, says why,
    and the step keeps its own status."""
    proc = _step(tmp_path, f'name="${{LHPC_CI_PREFIX}}disk-$$"\nlhpc_start "$name" docker run -d --name "$name" img '
                           f'sleep 300 >/dev/null\ntouch {tmp_path}/hang-rm\nfalse')
    try:
        _wait_calls(tmp_path, "rm -f ", 1)
        proc.send_signal(signal.SIGTERM)                     # mid-cleanup, inside the EXIT trap
        assert proc.wait(15) == 1
        assert re.search(r"::error::container \S+: its removal failed \(docker rm -f rc 124\)", _rest(proc))
    finally:
        _end(tmp_path, proc)


@pytest.mark.wallclock("the real sizing program and a step shell; waits bounded at 10 s")
def test_the_quota_step_signalled_mid_create_removes_the_create_and_ends_the_program(tmp_path):
    """The step's own quota lines (sourced helper, lhpc_wait_for, the program's --name): signalled while the daemon
    holds the program's create, the step removes the name until the program and its client have ended, so the create
    that lands is removed too (r6 second read B2: the client was orphaned and created the container after the removal)."""
    text = (ROOT / ".github" / "workflows" / "testlab.yml").read_text()
    m = re.search(r'\n( +)(lhpc_wait_for "\$\{LHPC_CI_PREFIX\}quota-\$\$" \\\n.*\n)', text)
    lines = m[2].replace("\n" + m[1], "\n")
    os.mkfifo(tmp_path / "ready")
    fd = os.open(tmp_path / "ready", os.O_RDWR)            # a reader from the start: the stand-in's write never blocks
    proc = _step(tmp_path, lines.replace('"$IMG"', "img") + f"touch {tmp_path}/after", mode="pending", cwd=ROOT)
    try:
        assert select.select([fd], [], [], 10)[0], "the program's client never started"
        client = _pid(tmp_path / "client.pid")
        proc.send_signal(signal.SIGTERM)
        assert proc.wait(10) == 143
        assert _ended(client), "the program's docker client outlived the step"
        name = _run_name(tmp_path)
        assert re.fullmatch(rf"{PREFIX}quota-\d+", name), name
        _gone(tmp_path, name)
        assert len(_boxes(tmp_path)) == 1, "the held create never landed"
        assert not (tmp_path / "after").exists()
    finally:
        os.close(fd)
        _end(tmp_path, proc)


@pytest.mark.wallclock("a real step shell and a stand-in container process; waits bounded at 10 s")
def test_a_client_that_ends_alone_leaves_no_container_running(tmp_path):
    """G2-TESTSA B3: the client killed on its own (KILL, OOM) while its container runs: the step goes on, as it would,
    and the container is removed by its name once the client's wait returns."""
    proc = _step(tmp_path, f"crun img sleep 600 || echo \"client rc $?\"\ntouch {tmp_path}/after")
    try:
        _started(proc)
        os.kill(_pid(tmp_path / "client.pid"), signal.SIGKILL)
        assert proc.wait(10) == 0
        assert _line(proc) == b"client rc 137\n"            # its status kept
        assert (tmp_path / "after").exists()
        _gone(tmp_path, _run_name(tmp_path))
    finally:
        _end(tmp_path, proc)


@pytest.mark.wallclock("a real step shell; the probe's client is still creating; waits bounded at 10 s")
@pytest.mark.parametrize("sig, rc", [(signal.SIGTERM, 143), (signal.SIGINT, 130)])
def test_a_probe_signalled_while_it_starts_is_removed_by_its_name(tmp_path, sig, rc):
    """G2-TESTSD B5: the disk probe's `cid=$(docker run -d ...)` deferred the signal to the substitution's end and its
    trap removed only "$cid": a signal while the create was held left the named container to nobody. `lhpc_start`
    owns the name before the start: the create that lands is removed by name, and `lhpc_done` is never reached."""
    proc = _step(tmp_path, f'name="${{LHPC_CI_PREFIX}}disk-$$"\nlhpc_start "$name" docker run -d --name "$name" img '
                           f'sleep 300 >/dev/null\nlhpc_done\ntouch {tmp_path}/after', mode="pending")
    try:
        _wait_calls(tmp_path, "run ", 1)
        proc.send_signal(sig)
        assert proc.wait(10) == rc
        name = _run_name(tmp_path)
        assert re.fullmatch(rf"{PREFIX}disk-\d+", name), name
        _gone(tmp_path, name)
        assert len(_boxes(tmp_path)) == 1, "the held create never landed"
        assert not (tmp_path / "after").exists()
    finally:
        _end(tmp_path, proc)


@pytest.mark.wallclock("text: the probe's own command line; nothing sleeps")
def test_a_probe_that_ends_normally_is_removed_by_lhpc_done(tmp_path):
    proc = _step(tmp_path, f'name="${{LHPC_CI_PREFIX}}disk-$$"\nlhpc_start "$name" docker run -d --name "$name" img '
                           f'sleep 300 >/dev/null\n[ -f {tmp_path}/"$name".pid ]\nlhpc_done\necho done')
    try:
        assert proc.wait(10) == 0
        assert _rest(proc) == "done\n"
        _gone(tmp_path, _run_name(tmp_path))
    finally:
        _end(tmp_path, proc)


def _sweep(tmp_path, cwd: Path, **extra) -> subprocess.CompletedProcess:
    """The job's last step, as the runner runs it (bash -eo pipefail), from the workflow's own text."""
    _, _, _, last = next(_sweeps())
    (tmp_path / "sweep.sh").write_text(last["run"])
    return subprocess.run(["bash", "--noprofile", "--norc", "-eo", "pipefail", str(tmp_path / "sweep.sh")],
                          env=_env(tmp_path, **extra), cwd=cwd, capture_output=True, text=True, timeout=30, check=False)


def _boxes_named(tmp_path, names) -> dict:
    boxes = {n: subprocess.Popen(["tail", "-f", "/dev/null"], stdout=subprocess.DEVNULL, start_new_session=True)
             for n in names}
    for n, p in boxes.items():
        (tmp_path / f"{n}.pid").write_text(f"{p.pid}\n")
    return boxes


@pytest.mark.parametrize("ours", [[], [f"{PREFIX}1-2", f"{PREFIX}quota-3"]], ids=["none", "two"])
def test_the_sweep_removes_this_runs_leftovers_and_fails_the_job(tmp_path, ours):
    """G2-TESTSD B2/B4/B7/N2: the job's last step, run with no checkout (an empty directory), removes the containers
    this run left, names them and fails the job; others (another run's, an unanchored lookalike, a stranger's) stay."""
    _docker(tmp_path, "exits")
    others = ["someone-else", f"x-{PREFIX}9", "lhpc-ci-8-1-9"]
    boxes = _boxes_named(tmp_path, [*ours, *others])
    empty = tmp_path / "no-checkout"
    empty.mkdir()
    try:
        r = _sweep(tmp_path, empty)
        assert r.returncode == (1 if ours else 0), r.stdout + r.stderr
        for n in ours:
            boxes[n].wait(5)                                 # removed: it ends (TimeoutExpired if it does not)
        assert all(boxes[n].poll() is None for n in others), "the sweep removed a container that is not this run's"
        if ours:
            assert f"::error::a step left containers, removed now: {' '.join(sorted(ours))}" in r.stdout, r.stdout
        else:
            assert r.stdout == "", r.stdout
    finally:
        for p in boxes.values():
            p.kill()
            p.wait()


@pytest.mark.parametrize("trouble, said", [("ps-fails", "the container sweep could not list containers (docker ps rc 1)"),
                                           ("hang-ps", "the container sweep could not list containers (docker ps rc 124)"),
                                           ("rm-fails", "the sweep's docker rm -f failed (rc 1)")])
def test_a_sweep_that_cannot_do_its_work_fails_the_job(tmp_path, trouble, said):
    """G2-TESTSD B4/B3: a failing or hanging `docker ps`, or a failing `docker rm -f`, was a warning and success."""
    _docker(tmp_path, "exits")
    boxes = _boxes_named(tmp_path, [f"{PREFIX}1-2"])
    (tmp_path / trouble).write_text("Cannot connect to the Docker daemon\n")
    try:
        r = _sweep(tmp_path, tmp_path, LHPC_SWEEP_S="1")
        assert r.returncode == 1 and f"::error::{said}" in r.stdout, r.stdout + r.stderr
    finally:
        for p in boxes.values():
            p.kill()
            p.wait()


@pytest.mark.wallclock("a real step shell; waits bounded at 10 s")
def test_a_create_that_lands_after_its_client_died_alone_is_the_sweeps(tmp_path):
    """The boundary (plan look C1): a client that dies mid-create on its own leaves a create the daemon holds, which
    lands after the step's last removal. The step cannot see it; the job's sweep removes it and fails the job."""
    proc = _step(tmp_path, "crun img sleep 600 || echo \"client rc $?\"", mode="pending")
    try:
        _started(proc)
        os.killpg(_pid(tmp_path / "client.pid"), signal.SIGKILL)   # the client and its group (the stand-in's polls)
        assert proc.wait(10) == 0
        name = _run_name(tmp_path)
        assert (tmp_path / f"{name}.pid").exists(), "the held create did not land after the step"
        r = _sweep(tmp_path, tmp_path)
        assert r.returncode == 1 and f"::error::a step left containers, removed now: {name}" in r.stdout, r.stdout
        _gone(tmp_path, name)
    finally:
        _end(tmp_path, proc)
