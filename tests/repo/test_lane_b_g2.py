"""The lane-B gate-2 fixes (LANEB-G2-TABLES-v7, LBS-D1, LBS-D2): the writers' state records and stop_writer's
decision on them (table A), the runner sampler publishing its own group from inside it (G2-1, G2-2), a failed
qemu launch uploading only its launch-failure capture (A21), and every local observation the lane turns into an
answer checked before it is used (table B, G2-3). Each case names its table row."""
import os
import re
import shutil
import subprocess
import sys
import time

import pytest

import repo_paths

LANE = repo_paths.REPO / ".github" / "lane-b"
TOOL = repo_paths.REPO / ".github" / "scripts" / "lane_b.py"
WORKFLOW = repo_paths.REPO / ".github" / "workflows" / "lane-b.yml"


def _jobs():
    parts = re.split(r"\n  ([\w-]+):\n", "\n" + WORKFLOW.read_text().split("\njobs:\n", 1)[1])
    return {name: body.split("\n      - ")[1:] for name, body in zip(parts[1::2], parts[2::2])}


def _run_block(step: str) -> str:
    lines = step.split("run: |\n", 1)[1].splitlines()
    body = [ln for ln in lines if ln.startswith("          ") or not ln.strip()]
    body = body[:next((i for i, ln in enumerate(lines) if ln.strip() and not ln.startswith("          ")), len(lines))]
    return "\n".join(ln[10:] for ln in body)


def _step(job, name_start):
    return next(s for s in _jobs()[job] if s.startswith(f"name: {name_start}"))


def _stub(tmp_path, **bodies):
    d = tmp_path / "stub"
    d.mkdir(exist_ok=True)
    for name, body in bodies.items():
        (d / name).write_text(f"#!/bin/bash\n{body}\n")
        (d / name).chmod(0o755)
    return d


def _sw(tmp_path, name, state, content, stub=None):
    """stop_writer on $WORK/<name> with the state record `state` (None: absent) and the pidfile `content` (None:
    absent). Returns (rc, stderr)."""
    work = tmp_path / "work"
    work.mkdir(exist_ok=True)
    f = work / name
    if state is not None:
        (work / f"{name}.state").write_text(state)
    if content is not None:
        f.write_text(content)
    path = (f"{stub}:" if stub else "") + "/usr/bin:/bin"
    p = subprocess.run(["bash", "-c", f'set -uo pipefail; . {LANE / "guest.sh"}; stop_writer {f}'],
                       capture_output=True, text=True, env={"WORK": str(work), "PATH": path}, timeout=120)
    return p.returncode, p.stderr


# ---- table A: the state records and the content rule ------------------------------------------------------------

@pytest.mark.parametrize("name, state, content, rc, why", [
    ("qemu.pid", None, None, 1, "no state record"),                        # A8: absence alone never means "never started"
    ("runner-sampler.pid", None, None, 1, "no state record"),
    ("qemu.pid", "none\n", None, 0, ""),                                    # A17: never launched, a positive record
    ("runner-sampler.pid", "none\n", None, 0, ""),
    ("qemu.pid", "none\n", "123\n", 1, "state none, but a pidfile"),         # A20
    ("runner-sampler.pid", "none\n", "grp:123\n", 1, "state none, but a pidfile"),
    ("qemu.pid", "launching\n", None, 3, "the launch failed"),              # A15 / A21
    ("runner-sampler.pid", "launching\n", None, 1, "nothing published"),    # A4 / A8
    ("runner-sampler.pid", "launching\n", "unpublished\n", 1, "not a group published"),   # A1e, A2, A3, A4
    ("runner-sampler.pid", "launching\n", "-12345\n", 1, "not a group published"),        # A3: the parent's id
    ("runner-sampler.pid", "launching\n", "grp:\n", 1, "not a group published"),          # A7: a partial value
    ("runner-sampler.pid", "launching\n", "grp:1x\n", 1, "not a group published"),
    ("runner-sampler.pid", "launching\n", "", 1, "not a group published"),                # A6: an empty file
    ("qemu.pid", "launching\n", "", 1, "not a pid"),                         # A18
    ("qemu.pid", "launching\n", "12a\n", 1, "not a pid"),
    ("qemu.pid", "launching\n", "0123\n", 1, "not a pid"),
    ("qemu.pid", "launching\n", "1\n", 1, "not a pid"),
    ("qemu.pid", "launching\n", "999999", 1, "not one pid and a final newline"),          # A18: no final newline
    ("qemu.pid", "launching\n", "999999\n\n", 1, "not one pid and a final newline"),
    ("qemu.pid", "running\n", "999999\n", 1, "unknown state"),
    ("qemu.pid", "", "999999\n", 1, "unknown state"),
])
def test_stop_writer_decides_on_the_state_record_and_the_published_content(tmp_path, name, state, content, rc, why):
    got, err = _sw(tmp_path, name, state, content)
    assert got == rc, (got, err)
    assert why in err, err


def _orphan(cmd):
    """Start `cmd` as an orphan (its starter exits at once), as the job's writers are when the evidence step runs:
    nothing here holds it as an unreaped child. Returns its pid."""
    out = subprocess.run(["bash", "-c", f"{cmd} > /dev/null 2>&1 & echo $!"], capture_output=True, text=True, check=True)
    return int(out.stdout)


def test_a_well_formed_qemu_pidfile_of_a_running_process_is_stopped(tmp_path):
    # A16 and A19: the process is ended and shown gone.
    pid = _orphan("sleep 300")
    try:
        rc, err = _sw(tmp_path, "qemu.pid", "launching\n", f"{pid}\n")
        assert rc == 0, err
    finally:
        subprocess.run(["kill", "-9", str(pid)], capture_output=True, check=False)


def test_a_sampler_group_published_from_inside_is_stopped(tmp_path):
    # A5, A9: "grp:<id>" names the group; kill -- -id, then pgrep -g shows it empty.
    pid = _orphan("setsid sleep 300")
    for _ in range(200):
        if subprocess.run(["pgrep", "-g", str(pid)], capture_output=True).returncode == 0:
            break
        time.sleep(0.01)
    try:
        rc, err = _sw(tmp_path, "runner-sampler.pid", "launching\n", f"grp:{pid}\n")
        assert rc == 0, err
    finally:
        subprocess.run(["kill", "-9", "--", f"-{pid}"], capture_output=True, check=False)


def test_a_group_gone_is_accepted_only_while_the_pidfile_still_holds_the_value_stopped(tmp_path):
    # A12: after the stop the group must still be empty and the pidfile still hold the published value; a pidfile
    # that changed under the stop (here: rewritten while pgrep runs) is refused.
    work = tmp_path / "work"
    stub = _stub(tmp_path, pgrep=f'echo "grp:424242" > {work}/runner-sampler.pid; exit 1')
    rc, err = _sw(tmp_path, "runner-sampler.pid", "launching\n", "grp:999999\n", stub=stub)
    assert rc == 1 and "not shown gone" in err, err


# ---- table A: the sampler publishes its own group (G2-1, G2-2) --------------------------------------------------

def _start(tmp_path, stub=None, background=False):
    work, ev = tmp_path / "work", tmp_path / "ev"
    work.mkdir(exist_ok=True)
    ev.mkdir(exist_ok=True)
    (work / "qemu.pid.state").write_text("none\n")
    (work / "runner-sampler.pid.state").write_text("none\n")
    path = (f"{stub}:" if stub else "") + "/usr/bin:/bin"
    script = f'set -euo pipefail; . {LANE / "guest.sh"}; g() {{ :; }}; gcp() {{ :; }}; start_samplers'
    env = {"WORK": str(work), "EV": str(ev), "PATH": path, "GITHUB_WORKSPACE": str(repo_paths.REPO)}
    if background:
        return subprocess.Popen(["bash", "-c", script], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                env=env)
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True, env=env, timeout=60)


def _evidence_step(tmp_path, job="topic", stub=None):
    work, ev = tmp_path / "work", tmp_path / "ev"
    (ev / "log.txt").write_text("ok\n")
    path = (f"{stub}:" if stub else "") + "/usr/bin:/bin"
    step = next(s for s in _jobs()[job] if "id: evidence" in s)
    return subprocess.run(["bash", "-e", "-c", _run_block(step)], capture_output=True, text=True, timeout=120,
                          env={"EV": str(ev), "WORK": str(work), "PATH": path, "LF": str(tmp_path / "lf"),
                               "GITHUB_OUTPUT": str(tmp_path / "out"), "GITHUB_WORKSPACE": str(repo_paths.REPO)})


def _kill_group_of(pidfile):
    t = pidfile.read_text().strip() if pidfile.exists() else ""
    if t.startswith("grp:"):
        subprocess.run(["kill", "-9", "--", f"-{t[4:]}"], capture_output=True, check=False)


def test_the_evidence_step_refuses_while_the_sampler_is_held_before_setsid(tmp_path):
    # G2-1, A3 (event-controlled, replaces the old sleep-1 ordering proof): the sampler's launch is held before
    # setsid ran (a shim waits on a FIFO the test opens later), and the real evidence step runs meanwhile. The
    # state record says "launching" and nothing is published from inside a group: the step refuses, writes no
    # sums. Released, the sampler publishes "grp:<id>" itself and start_samplers succeeds.
    fifo, held = tmp_path / "release", tmp_path / "held"
    os.mkfifo(fifo)
    stub = _stub(tmp_path, setsid=f'echo $$ > {held}; read -r _ < {fifo}; exec {shutil.which("setsid")} "$@"')
    starter = _start(tmp_path, stub=stub, background=True)
    try:
        for _ in range(500):
            if held.exists():
                break
            time.sleep(0.01)
        assert held.exists(), "the launch never reached the shim"
        p = _evidence_step(tmp_path)
        assert p.returncode == 1 and "evidence: a writer" in p.stdout, (p.stdout, p.stderr)
        assert "not a group published" in p.stderr, p.stderr
        assert not (tmp_path / "ev" / "SHA256SUMS").exists()
        with open(fifo, "w") as fh:                                       # release the launch
            fh.write("go\n")
        out, err = starter.communicate(timeout=60)
        assert starter.returncode == 0, err
        assert re.fullmatch(r"grp:[1-9][0-9]*\n", (tmp_path / "work" / "runner-sampler.pid").read_text())
    finally:
        if starter.poll() is None:
            try:
                os.close(os.open(fifo, os.O_WRONLY | os.O_NONBLOCK))
            except OSError:
                pass
            starter.kill()
        _kill_group_of(tmp_path / "work" / "runner-sampler.pid")


def test_a_published_sampler_is_stopped_and_the_evidence_proceeds(tmp_path):
    # A5 through the real start and the real evidence step: published from inside, stopped, sums written.
    p = _start(tmp_path)
    try:
        assert p.returncode == 0, p.stderr
        e = _evidence_step(tmp_path)
        assert e.returncode == 0, (e.stdout, e.stderr)
        assert subprocess.run(["sha256sum", "-c", "--quiet", "SHA256SUMS"], cwd=tmp_path / "ev",
                              capture_output=True).returncode == 0
        # the writers as the stop found them, in the evidence and its sums (LBS-D3): records and pidfile bytes
        w = tmp_path / "ev" / "writers"
        assert (w / "runner-sampler.pid.state").read_text() == "launching\n"
        assert re.match(r"0000000\s+g\s+r\s+p\s+:", (w / "runner-sampler.pid.od").read_text())
        assert (w / "qemu.pid.state").read_text() == "none\n" and (w / "qemu.pid.od").read_text() == "absent\n"
        assert "./writers/runner-sampler.pid.od" in (tmp_path / "ev" / "SHA256SUMS").read_text()
    finally:
        _kill_group_of(tmp_path / "work" / "runner-sampler.pid")


def test_a_launch_whose_child_cannot_run_setsid_fails_and_the_evidence_refuses(tmp_path):
    # A1e: setsid missing (the child exits 127): nothing is published, the bounded poll ends the launch with rc != 0,
    # and the evidence step refuses (state "launching", the sentinel still there).
    bindir = tmp_path / "bin"
    bindir.mkdir()
    for tool in ("bash", "cat", "mv", "sleep", "date", "cut", "awk", "ps", "tr", "printf", "pgrep", "kill"):
        w = shutil.which(tool)
        if w:
            os.symlink(w, bindir / tool)
    work, ev = tmp_path / "work", tmp_path / "ev"
    work.mkdir()
    ev.mkdir()
    (work / "qemu.pid.state").write_text("none\n")
    (work / "runner-sampler.pid.state").write_text("none\n")
    script = f'set -euo pipefail; . {LANE / "guest.sh"}; g() {{ :; }}; gcp() {{ :; }}; start_samplers'
    p = subprocess.run([str(bindir / "bash"), "-c", script], capture_output=True, text=True, timeout=60,
                       env={"WORK": str(work), "EV": str(ev), "PATH": str(bindir), "GITHUB_WORKSPACE": str(repo_paths.REPO)})
    assert p.returncode != 0 and "published no group" in p.stderr, p.stderr
    e = _evidence_step(tmp_path)
    assert e.returncode == 1 and "not a group published" in e.stderr, (e.stdout, e.stderr)


def test_a_sampler_whose_rename_fails_never_publishes_and_the_evidence_refuses(tmp_path):
    # A6, A7: the publication is a temp file and a rename; a failed rename (only the sampler's own) leaves the
    # sentinel, the child exits, the launch fails at the bound, and the step refuses.
    stub = _stub(tmp_path, mv=f'case "$*" in *runner-sampler.pid.tmp*) exit 1;; esac; exec {shutil.which("mv")} "$@"')
    p = _start(tmp_path, stub=stub)
    assert p.returncode != 0, p.stderr
    e = _evidence_step(tmp_path, stub=None)
    assert e.returncode == 1 and "not a group published" in e.stderr, (e.stdout, e.stderr)


def test_a_partial_publication_left_in_the_temp_file_is_never_taken(tmp_path):
    # A7 (partial output, then a failure): a partial "grp:12" already sits in the temp file and the rename fails. The
    # pidfile keeps the sentinel, the launch fails at its bound, and the evidence step refuses: the pidfile changes only
    # by the rename, so no partial value reaches it. (The sampler's printf is bash's builtin, so a short write itself is
    # not injected here; the rename is the only path into the pidfile.)
    work = tmp_path / "work"
    work.mkdir()
    (work / "runner-sampler.pid.tmp").write_text("grp:12")
    stub = _stub(tmp_path, mv=f'case "$*" in *runner-sampler.pid.tmp*) exit 1;; esac; exec {shutil.which("mv")} "$@"')
    p = _start(tmp_path, stub=stub)
    assert p.returncode != 0, p.stderr
    assert (work / "runner-sampler.pid").read_text() == "unpublished\n"
    e = _evidence_step(tmp_path)
    assert e.returncode == 1 and "not a group published" in e.stderr, (e.stdout, e.stderr)


# ---- A21 (LBS-D2): a failed qemu launch uploads only its capture --------------------------------------------------

@pytest.mark.parametrize("job", ["topic"])
def test_a_failed_qemu_launch_uploads_only_the_launch_failure_capture(tmp_path, job):
    work, ev = tmp_path / "work", tmp_path / "ev"
    work.mkdir()
    ev.mkdir()
    (work / "qemu.pid.state").write_text("launching\n")
    (work / "runner-sampler.pid.state").write_text("none\n")
    (work / "qemu-launch.log").write_text("qemu-system-aarch64: could not load kernel\n")
    (ev / "console.log").write_text("half a boot\n")
    p = _evidence_step(tmp_path, job=job)
    assert p.returncode == 1 and "only the launch-failure capture" in p.stdout, (p.stdout, p.stderr)
    assert (tmp_path / "out").read_text() == "launchfail=1\n"
    lf = tmp_path / "lf"
    want = {"qemu-launch.log", "qemu.pid.state", "qemu.pid.txt", "runner-sampler.pid.state"}
    assert {f.name for f in lf.iterdir()} == want, sorted(f.name for f in lf.iterdir())
    assert (lf / "qemu.pid.txt").read_text() == "qemu.pid: absent\n"
    assert not (ev / "SHA256SUMS").exists()                               # never scan/sums of the rest


def test_a_launch_failure_capture_with_a_key_is_not_released(tmp_path):
    work, ev = tmp_path / "work", tmp_path / "ev"
    work.mkdir()
    ev.mkdir()
    (work / "qemu.pid.state").write_text("launching\n")
    (work / "runner-sampler.pid.state").write_text("none\n")
    (work / "qemu-launch.log").write_text("-----BEGIN OPENSSH PRIVATE KEY-----\n")
    p = _evidence_step(tmp_path)
    assert p.returncode == 1 and "redaction" in p.stdout, (p.stdout, p.stderr)
    assert not (tmp_path / "out").exists()


# ---- B13: the sums list every file -----------------------------------------------------------------------------

def test_short_sums_are_refused(tmp_path):
    work, ev = tmp_path / "work", tmp_path / "ev"
    work.mkdir()
    ev.mkdir()
    (ev / "a.txt").write_text("a\n")
    (ev / "b.txt").write_text("b\n")
    for w in ("qemu.pid", "runner-sampler.pid"):
        (work / f"{w}.state").write_text("none\n")
    real = shutil.which("sha256sum")
    stub = _stub(tmp_path, sha256sum=f'{real} "$1"')                    # hashes only the first file it is given
    p = _evidence_step(tmp_path, stub=stub)
    assert p.returncode == 1 and "SHA256SUMS lists" in p.stdout, (p.stdout, p.stderr)


# ---- table B: local observations ---------------------------------------------------------------------------------

def _tool(*args):
    return subprocess.run([sys.executable, str(TOOL), *map(str, args)], capture_output=True, text=True)


def _guest_dir(tmp_path, **change):
    d = tmp_path / "guest"
    d.mkdir(exist_ok=True)
    files = {"machine": "aarch64\n", "kernel": "6.12.48+deb13-arm64\n", "rpi_issue": "yes\n",
             "os_release": "VERSION_CODENAME=trixie\n", **change}
    for n, t in files.items():
        if t is None:
            (d / n).unlink(missing_ok=True)
        else:
            (d / n).write_text(t)
    return d


@pytest.mark.parametrize("host, change, rc, why", [
    ("", {}, 1, "the runner kernel is empty"),                             # B1
    ("6.11.0-azure", {"kernel": ""}, 1, "the guest kernel is not one non-empty line"),   # B4
    ("6.11.0-azure", {"kernel": "6.12\n6.13\n"}, 1, "the guest kernel is not one non-empty line"),
    ("6.11.0-azure", {"kernel": None}, 2, "an input cannot be read"),     # B5: plumbing, not the FAIL 1
    ("6.11.0-azure", {}, 0, "ok"),
])
def test_identity_tells_pass_fail_and_plumbing_apart(tmp_path, host, change, rc, why):
    p = _tool("identity", host, _guest_dir(tmp_path, **change))
    assert p.returncode == rc and why in (p.stdout + p.stderr), (p.returncode, p.stdout, p.stderr)


def test_verify_and_active_on_a_missing_file_are_plumbing(tmp_path):
    for args in (("verify", tmp_path / "none", "sha256", "00"), ("active", 2, tmp_path / "none")):
        p = _tool(*args)
        assert p.returncode == 2 and "an input cannot be read" in p.stderr, (args, p.stderr)


@pytest.mark.parametrize("text, ok", [("4f8a6b1c-02", True), ("4f8a6b1c-1234-5678-9abc-def012345678", True),
                                      ("", False), ("4f8a6b1c", False), ("4F8A6B1C-02", False), ("x\n4f8a6b1c-02", False)])
def test_the_partuuid_has_its_shape_before_it_reaches_the_kernel_line(tmp_path, text, ok):
    # B8
    f = tmp_path / "partuuid"
    f.write_text(text + "\n" if text else "")
    p = subprocess.run(["bash", "-c", f'. {LANE / "guest.sh"}; partuuid_ok {f}'], capture_output=True, text=True,
                       env={"WORK": str(tmp_path), "PATH": "/usr/bin:/bin"})
    assert (p.returncode == 0) == ok, p.stderr


def _prepare(tmp_path, modules, pub):
    """prepare() with the privileged and image tools stubbed: `modules` directories under droot/lib/modules,
    key.pub = `pub`. Returns the run."""
    work, ev = tmp_path / "work", tmp_path / "ev"
    for d in (work / "droot" / "boot", ev):
        d.mkdir(parents=True, exist_ok=True)
    for m in modules:
        (work / "droot" / "lib" / "modules" / m).mkdir(parents=True)
    (work / "key.pub").write_text(pub)
    stub = _stub(tmp_path, **{"qemu-img": "exit 0",
                              # sfdisk reads its stdin like the real one: a stub that exits unread lets the
                              # `echo | sudo sfdisk` pipeline die of SIGPIPE (141) under pipefail when it exits first
                              "sudo": 'shift; case "$1" in losetup) echo /dev/loop9;; blkid) echo 4f8a6b1c-02;; '
                                      'tee|sfdisk) cat > /dev/null;; *) exit 0;; esac'})
    return subprocess.run(["bash", "-c", f'set -euo pipefail; . {LANE / "guest.sh"}; prepare'], capture_output=True,
                          text=True, env={"WORK": str(work), "EV": str(ev), "PATH": f"{stub}:/usr/bin:/bin"})


@pytest.mark.parametrize("modules, ok", [([], False), (["6.12.48+deb13-arm64"], True), (["6.12.48", "6.12.49"], False)],
                         ids=["none", "one", "two"])
def test_the_guest_kernel_version_comes_from_exactly_one_modules_directory(tmp_path, modules, ok):
    # B6: zero or several directories stop prepare; one gives the version.
    p = _prepare(tmp_path, modules, "ssh-ed25519 AAAA job\n")
    assert (p.returncode == 0) == ok, p.stderr
    if ok:
        assert "guest_kernel=6.12.48+deb13-arm64" in (tmp_path / "ev" / "image.txt").read_text()
    else:
        assert "not exactly one modules directory" in p.stderr


@pytest.mark.parametrize("pub, ok", [("", False), ("not-a-key\n", False), ("ssh-ed25519 A\nssh-ed25519 B\n", False),
                                     ("ssh-ed25519 AAAA job\n", True)], ids=["empty", "shape", "two", "ok"])
def test_the_job_key_reaches_the_user_data_only_as_one_ssh_line(tmp_path, pub, ok):
    # B7
    p = _prepare(tmp_path, ["6.12.48+deb13-arm64"], pub)
    assert (p.returncode == 0) == ok, p.stderr


@pytest.mark.parametrize("fn", ["wait_ssh 60", "wait_reboot 60 old", "wait_up 60"])
@pytest.mark.parametrize("clock", ["exit 1", "echo", "echo garbage"], ids=["fails", "empty", "garbage"])
def test_a_wait_whose_clock_read_fails_stops_at_once(tmp_path, fn, clock):
    # B10: a failed, empty or non-numeric clock read stops the wait at once: before its first poll (no sleep), not
    # at the poll bound and never at the job limit.
    polls = tmp_path / "polls"
    stub = _stub(tmp_path, date=clock, sleep=f"echo x >> {polls}")
    p = subprocess.run(["bash", "-c", f'. {LANE / "guest.sh"}; g() {{ return 1; }}; {fn}'], capture_output=True,
                       text=True, timeout=30, env={"WORK": str(tmp_path), "PATH": f"{stub}:/usr/bin:/bin"})
    assert p.returncode == 1, (p.stdout, p.stderr)
    assert not polls.exists(), polls.read_text()


@pytest.mark.parametrize("fn", ["wait_ssh 60", "wait_reboot 60 old", "wait_up 60"])
def test_a_wait_on_a_frozen_clock_stops_at_its_poll_bound(tmp_path, fn):
    # B10: a clock that returns a constant never reaches the time bound; the poll count does (60/5 + 1 polls).
    count = tmp_path / "polls"
    stub = _stub(tmp_path, date="echo 1000", sleep=f"echo x >> {count}")
    p = subprocess.run(["bash", "-c", f'. {LANE / "guest.sh"}; g() {{ return 1; }}; {fn}'], capture_output=True,
                       text=True, timeout=30, env={"WORK": str(tmp_path), "PATH": f"{stub}:/usr/bin:/bin"})
    assert p.returncode == 1, (p.stdout, p.stderr)
    assert len(count.read_text().splitlines()) <= 60 // 5 + 1


# ---- rule 2: every command substitution the lane runs is checked ------------------------------------------------

# The substitutions that are data, not decisions: the runner sampler's printed line (a measurement it records) and
# commands sent as text to the guest (they run there, quoted).
_DATA = ('printf "%s load=%s mem_avail_mb=%s qemu_cpu=%s', '"$(awk "/MemAvailable/', "g 's=$(date +%s%N)")


def _scripts():
    yield "guest.sh", (LANE / "guest.sh").read_text()
    yield "topic.sh", (LANE / "topic.sh").read_text()
    for job, steps in _jobs().items():
        for s in steps:
            if "run: |" in s:
                yield f"lane-b.yml {job}: {s.splitlines()[0]}", _run_block(s)


_SEP = re.compile(r"(?:^|;|&&|\|\||!|\b(?:then|do|else|if|elif|while|until)\b|\{|\()\s*$")


def _unchecked(code: str) -> list:
    """The command substitutions of one line of shell that are not a plain assignment at a statement start: each
    `$(` must open the whole value of `NAME=$(…)`, where NAME= starts a statement (line start, or after ;, &&, ||,
    then, do, else, if, elif, while, until, `!`, `{` or `(`) and is not the argument of local, export, declare or
    readonly (whose status masks the substitution's); and no `$(` may sit inside a kept substitution. Arithmetic
    `$((…))` is not a substitution, unless one sits inside it."""
    code = re.sub(r"\$\(\((?:(?!\$\()[^()]|\((?:(?!\$\()[^()])*\))*\)\)", "0", code)
    bad, i = [], 0
    while (i := code.find("$(", i)) != -1:
        depth, j = 0, i + 1
        while j < len(code):
            depth += {"(": 1, ")": -1}.get(code[j], 0)
            if depth == 0:
                break
            j += 1
        inner = code[i + 2:j]
        m = re.search(r"([A-Za-z_][A-Za-z0-9_]*)=$", code[:i])
        ok = (m is not None and _SEP.search(code[:m.start()]) is not None
              and not re.search(r"\b(local|export|declare|readonly)\s+(?:[^\s;&|]+\s+)*$", code[:m.start()])
              and "$(" not in inner)
        if not ok:
            bad.append(code[i:j + 1])
        i = j + 1 if ok else i + 2
    return bad


@pytest.mark.parametrize("line", [
    '-append "root=PARTUUID=$(cat partuuid) rw rootwait console=ttyAMA0"',      # the old boot argument
    'echo "kvm=$([ -e /dev/kvm ] && echo present || echo absent)"',            # an argument
    'ver=$(basename "$(ls -d droot/lib/modules/* | head -1)")',                # nested
    'local x=$(cat f)',                                                         # local masks the status
    'python3 lane_b.py identity "$(uname -r)" "$EV/guest"',
    'echo "boot_s=$(( $(date +%s) - t0 ))"',                                    # inside arithmetic
])
def test_the_rule_two_scan_reports_the_forms_the_verdict_named(line):
    assert _unchecked(line), line


@pytest.mark.parametrize("line", ['t0=$(now) || return 1', 'pl=$(sudo -n losetup -fP --show x); dl=$(sudo -n y)',
                                  'last=$(od -An -c -j "$(( sz - 1 ))" -N 1 "$f") || return 1',
                                  'if x; then k=$(uname -r); fi', 'b0=$(g cat id) && [ -n "$b0" ] || fail x'])
def test_the_rule_two_scan_keeps_checked_assignments(line):
    assert not _unchecked(line), line


def test_every_command_substitution_the_lane_runs_is_a_checked_assignment():
    # LBS-D1 / rule 2: a `$(…)` is used only as `NAME=$(…)` at a statement start (whose failure ends the script or is
    # handled on that line), never as an argument, never nested, never under local/export.
    bad = []
    for where, text in _scripts():
        for ln in text.splitlines():
            code = ln.split(" #", 1)[0] if not ln.lstrip().startswith("#") else ""
            if "$(" not in code or any(d in code for d in _DATA):
                continue
            if _unchecked(code):
                bad.append(f"{where}: {ln.strip()}")
    assert not bad, "\n".join(bad)
