"""The lane-B checks (.github/scripts/lane_b.py) that the QB-1 job relies on to fail closed: a pinned image
whose digest differs is refused before any boot, and a guest that is not a guest (the runner's own
kernel, another architecture, no Pi OS userland, not Trixie) fails the job."""
import os
import re
import shutil
import subprocess
import sys

import pytest

import repo_paths

TOOL = repo_paths.REPO / ".github" / "scripts" / "lane_b.py"
GUEST = {"machine": "aarch64", "kernel": "6.12.48+deb13-arm64", "rpi_issue": "yes",
         "os_release": 'PRETTY_NAME="Debian GNU/Linux 13 (trixie)"\nVERSION_CODENAME=trixie\n'}


def _run(*args):
    return subprocess.run([sys.executable, str(TOOL), *map(str, args)], capture_output=True, text=True,
                          check=False)


@pytest.mark.parametrize("algo", ["sha256", "sha512"])
def test_an_image_whose_digest_differs_is_refused(tmp_path, algo):
    image = tmp_path / "image"
    image.write_bytes(b"not the pinned image")
    import hashlib
    good = hashlib.new(algo, image.read_bytes()).hexdigest()
    assert _run("verify", image, algo, good).returncode == 0
    p = _run("verify", image, algo, "0" * len(good))
    assert p.returncode == 1 and f"{algo} mismatch" in p.stderr


def _identity(tmp_path, host_kernel="6.11.0-1013-azure", **change):
    d = tmp_path / "guest"
    d.mkdir(exist_ok=True)
    for name, text in {**GUEST, **change}.items():
        (d / name).write_text(text)
    return _run("identity", host_kernel, d)


def test_a_guest_passes(tmp_path):
    p = _identity(tmp_path)
    assert p.returncode == 0, p.stderr


@pytest.mark.parametrize("change, why", [
    ({"kernel": "6.11.0-1013-azure"}, "the runner's own kernel"),
    ({"machine": "x86_64"}, "not aarch64"),
    ({"rpi_issue": "no"}, "no /etc/rpi-issue"),
    ({"os_release": "VERSION_CODENAME=bookworm\n"}, "not trixie"),
])
def test_a_guest_that_is_not_the_pi_os_guest_fails(tmp_path, change, why):
    p = _identity(tmp_path, **change)
    assert p.returncode == 1 and why in p.stderr


@pytest.mark.parametrize("text, ok", [
    ("active\nactive\n", True), ("inactive\nactive\n", False), ("active\ninactive\n", False),
    ("active\n", False), ("", False), ("active\nactive\nactive\n", False),
])
def test_the_topic_job_passes_only_when_both_units_are_active(tmp_path, text, ok):
    f = tmp_path / "units-active.txt"
    f.write_text(text)
    assert (_run("active", 2, f).returncode == 0) == ok


def test_the_topic_job_asks_the_check_for_both_units():
    topic = (repo_paths.REPO / ".github" / "lane-b" / "topic.sh").read_text()
    assert 'lane_b.py" active 2 "$EV/units-active.txt"' in topic


WORKFLOW = repo_paths.REPO / ".github" / "workflows" / "lane-b.yml"


def _jobs():
    """{job: [step text]} of lane-b.yml, split by indentation (no YAML parser in the dev dependencies)."""
    parts = re.split(r"\n  ([\w-]+):\n", "\n" + WORKFLOW.read_text().split("\njobs:\n", 1)[1])
    return {name: body.split("\n      - ")[1:] for name, body in zip(parts[1::2], parts[2::2])}


def test_an_artifact_uploads_only_after_its_redaction_guard_passed():
    # A failed redaction check must stop the upload: `if: always()` on the upload ran it anyway. Each job has two
    # uploads: the evidence, only when the evidence step succeeded; the launch-failure capture (LBS-D2), only when the
    # evidence step set launchfail=1, which it does only after redact() passed on that capture.
    uploaded = 0
    for job, steps in _jobs().items():
        ups = [s for s in steps if s.startswith("uses: actions/upload-artifact")]
        assert len(ups) == 2, job
        for up in ups:
            uploaded += 1
            guard = re.search(r"if: always\(\) && steps\.(\w+)\.(outcome == 'success'|outputs\.launchfail == '1')", up)
            assert guard, (job, up.splitlines()[:3])
            owner = [s for s in steps if f"id: {guard.group(1)}" in s]
            assert len(owner) == 1 and "PRIVATE KEY-----|SSHPASS=" in owner[0] and "exit 1" in owner[0], job
            if "launchfail" in guard.group(2):
                block = _run_block(owner[0])
                assert block.index('redact "$LF" || exit 1') < block.index('echo "launchfail=1" >> "$GITHUB_OUTPUT"'), job
    assert uploaded == 2                                       # the one job, from-topic: two


def _run_block(step: str) -> str:
    """A step's `run: |` script, de-indented, as the runner receives it."""
    lines = step.split("run: |\n", 1)[1].splitlines()
    body = [ln for ln in lines if ln.startswith("          ") or not ln.strip()]
    body = body[:next((i for i, ln in enumerate(lines) if ln.strip() and not ln.startswith("          ")), len(lines))]
    return "\n".join(ln[10:] for ln in body)


@pytest.mark.parametrize("case, rc", [("clean", 0), ("a key", 1), ("an unreadable file", 1)])
def test_the_redaction_check_fails_closed(tmp_path, case, rc):
    # The evidence step runs as the runner runs it (`bash -e`, then its own `set -uo pipefail`): clean
    # evidence passes; a hit stops the upload; so does the check itself failing (grep exits 2 on an
    # unreadable file), which used to read as "no hit".
    if os.geteuid() == 0:
        pytest.skip("root reads every file")
    ev, work = tmp_path / "ev", tmp_path / "work"
    ev.mkdir(); work.mkdir()
    (ev / "log.txt").write_text("ok\n")
    if case == "a key":
        (ev / "key.txt").write_text("-----BEGIN OPENSSH PRIVATE KEY-----\n")
    if case == "an unreadable file":
        (ev / "locked.txt").write_text("x\n")
        (ev / "locked.txt").chmod(0)
    for w in ("qemu.pid", "runner-sampler.pid"):
        (work / f"{w}.state").write_text("none\n")
    for job, steps in _jobs().items():
        step = next(s for s in steps if "id: evidence" in s)
        p = subprocess.run(["bash", "-e", "-c", _run_block(step)], capture_output=True, text=True,
                           env={"EV": str(ev), "WORK": str(work), "PATH": "/usr/bin:/bin", "LF": str(tmp_path / "lf"),
                                "GITHUB_OUTPUT": str(tmp_path / "out"), "GITHUB_WORKSPACE": str(repo_paths.REPO)})
        assert p.returncode == rc, (job, case, p.stdout, p.stderr)


LANE = repo_paths.REPO / ".github" / "lane-b"


def _evidence(tmp_path, writer=None, pidfile_text=None, path_extra=None, kill_log=None, jobs=("topic",)):
    """The evidence step of each job in `jobs` as the runner runs it, with an optional live writer into the evidence
    (a process group that appends; on TERM it writes `writer` once more and exits) named by runner-sampler.pid as
    the sampler publishes it ("grp:<id>", state record "launching"). The writer is an orphan, as the job's writers
    are when the step runs (their starter has exited), so nothing here holds it as an unreaped child. With
    `pidfile_text`, runner-sampler.pid holds that text under state "launching". qemu.pid's state record is "none".
    With `kill_log`, the step's shell has `kill` replaced by a recorder (BASH_ENV): no signal is sent, and each call
    is a line in that file. Only the topic job runs a sampler, so only its evidence step asks for runner-sampler.pid."""
    ev, work = tmp_path / "ev", tmp_path / "work"
    ev.mkdir(exist_ok=True)
    work.mkdir(exist_ok=True)
    (ev / "log.txt").write_text("ok\n")
    results = []
    for job, steps in ((j, s) for j, s in _jobs().items() if j in jobs):
        pgid = None
        (work / "qemu.pid.state").write_text("none\n")
        (work / "runner-sampler.pid.state").write_text("none\n" if writer is None and pidfile_text is None else "launching\n")
        if writer is not None:
            (ev / "w.txt").unlink(missing_ok=True)
            on_term = f"trap 'echo \"{writer}\" >> {ev}/w.txt; exit 0' TERM"
            loop = f"{on_term}; echo up >> {ev}/w.txt; while :; do echo tick >> {ev}/w.txt; sleep 0.05; done"
            start = subprocess.run(["bash", "-c", 'setsid bash -c "$1" > /dev/null 2>&1 & echo $!', "_", loop],
                                   capture_output=True, text=True, check=True)
            pgid = int(start.stdout)
            while not (ev / "w.txt").exists():
                pass
            (work / "runner-sampler.pid").write_text(f"grp:{pgid}\n")
        elif pidfile_text is not None:
            (work / "runner-sampler.pid").write_text(pidfile_text)
        step = next(s for s in steps if "id: evidence" in s)
        path = (f"{path_extra}:" if path_extra else "") + "/usr/bin:/bin"
        env = {"EV": str(ev), "WORK": str(work), "PATH": path, "GITHUB_WORKSPACE": str(repo_paths.REPO),
               "LF": str(tmp_path / "lf"), "GITHUB_OUTPUT": str(tmp_path / "out")}
        if kill_log is not None:
            (tmp_path / "kill-recorder.sh").write_text(f'kill() {{ echo "kill $*" >> {kill_log}; }}\n')
            env["BASH_ENV"] = str(tmp_path / "kill-recorder.sh")
        p = subprocess.run(["bash", "-e", "-c", _run_block(step)], capture_output=True, text=True, check=False,
                           env=env)
        alive = pgid is not None and subprocess.run(["pgrep", "-g", str(pgid)], capture_output=True,
                                                    check=False).returncode == 0
        if alive:
            os.killpg(pgid, 9)
        # the sums, if written, must still match the files: nothing changed them after the hashing
        sums = (ev / "SHA256SUMS").exists() and subprocess.run(["sha256sum", "-c", "--quiet", "SHA256SUMS"], cwd=ev,
                                                               capture_output=True, check=False).returncode == 0
        results.append((job, p, alive, sums))
        (ev / "SHA256SUMS").unlink(missing_ok=True)
        (work / "runner-sampler.pid").unlink(missing_ok=True)
    return ev, results


def test_the_evidence_is_scanned_and_hashed_only_after_its_writers_stopped(tmp_path):
    ev, results = _evidence(tmp_path, writer="late")
    for job, p, alive, sums in results:
        assert p.returncode == 0 and not alive and sums, (job, p.stdout, p.stderr)
    # the writer's last line came on TERM, before the hashing: it is in the file the sums cover
    assert (ev / "w.txt").read_text().splitlines()[-1] == "late"


def test_bytes_a_writer_adds_on_its_way_out_are_still_redaction_checked(tmp_path):
    _ev, results = _evidence(tmp_path, writer="-----BEGIN OPENSSH PRIVATE KEY-----")
    for job, p, alive, sums in results:
        assert p.returncode == 1 and "redaction: a key" in p.stdout and not alive and not sums, (job, p.stdout, p.stderr)


@pytest.mark.parametrize("pidfile_text, ps_rc, why", [
    ("\n", None, "not a group published"), ("12-3\n", None, "not a group published"),
    ("-999999\n", None, "not a group published"),           # the old parent-side form is never accepted
    ("unpublished\n", None, "not a group published"),       # the sentinel: launched, nothing published
    ("grp:999999\n", 0, "still runs"),                       # the writer never goes: the bound, then no upload
    ("grp:999999\n", 2, "the liveness check failed"),        # the check itself fails: no upload either
])
def test_no_upload_when_a_writer_cannot_be_shown_stopped(tmp_path, pidfile_text, ps_rc, why):
    stub = None
    if ps_rc is not None:
        stub = tmp_path / "stub"
        stub.mkdir()
        for name, body in {"pgrep": f"exit {ps_rc}", "ps": f"exit {ps_rc}", "sleep": "exit 0"}.items():
            (stub / name).write_text(f"#!/bin/bash\n{body}\n")
            (stub / name).chmod(0o755)
    _ev, results = _evidence(tmp_path, pidfile_text=pidfile_text, path_extra=stub)
    for job, p, _alive, sums in results:
        assert p.returncode == 1 and "evidence: a writer" in p.stdout and why in p.stderr and not sums, \
            (job, p.stdout, p.stderr)


@pytest.mark.parametrize("pidfile_text", ["grp:0\n", "grp:-0\n", "grp:1\n", "grp:-1\n", "grp:00\n", "grp:01\n",
                                          "0\n", "-1\n"])
def test_no_writer_id_that_names_no_single_writer_is_ever_signalled(tmp_path, pidfile_text):
    # G1-LBS-2 B2: `kill -- 0` is the caller's own group and `kill -- -1` every process it may signal; neither (nor
    # pid 1, nor a zero-padded form) names the writer. Refused before any signal, and no upload.
    stub = tmp_path / "stub"
    stub.mkdir()
    (stub / "sleep").write_text("#!/bin/bash\nexit 0\n")
    (stub / "sleep").chmod(0o755)
    log = tmp_path / "kills"
    _ev, results = _evidence(tmp_path, pidfile_text=pidfile_text, path_extra=stub, kill_log=log)
    for job, p, _alive, sums in results:
        assert p.returncode == 1 and "evidence: a writer" in p.stdout and "not a group published" in p.stderr \
            and not sums, (job, p.stdout, p.stderr)
    assert not log.exists(), log.read_text()


@pytest.mark.parametrize("slow_start", [False, True])
def test_the_runner_sampler_is_a_process_group_the_stop_ends(tmp_path, slow_start):
    # start_samplers as the job runs it (the guest half stubbed): the pidfile names the sampler's own group, and
    # stop_writer leaves no process of it behind. The group exists only once setsid has run, after start_samplers
    # returned; the sampler's first line (written after that) is the event the check waits for (G1-LBS-2 B3), the
    # 30 s only the bound of a hang. slow_start: a setsid that starts a second late, the order a busy runner can
    # give; an immediate group check fails there.
    path = os.environ["PATH"]
    if slow_start:
        stub = tmp_path / "stub"
        stub.mkdir()
        (stub / "setsid").write_text(f'#!/bin/bash\nsleep 1\nexec {shutil.which("setsid")} "$@"\n')
        (stub / "setsid").chmod(0o755)
        path = f"{stub}:{path}"
    script = f"""set -euo pipefail
WORK={tmp_path}; EV={tmp_path}; GITHUB_WORKSPACE={repo_paths.REPO}; . {LANE / "guest.sh"}
echo none > {tmp_path}/runner-sampler.pid.state
g() {{ :; }}; gcp() {{ :; }}
start_samplers
t=$(cat {tmp_path}/runner-sampler.pid); [ "${{t#grp:}}" != "$t" ]; t=-${{t#grp:}}
timeout 30 bash -c 'until [ -s "$1" ]; do sleep 0.1; done' _ {tmp_path}/runner-load.txt
pgrep -g "${{t#-}}" > /dev/null
stop_writer {tmp_path}/runner-sampler.pid
r=0; pgrep -g "${{t#-}}" > /dev/null || r=$?; echo "after=$r"
"""
    p = subprocess.run(["bash", "-c", script], capture_output=True, text=True, check=False, timeout=90,
                       env={**os.environ, "PATH": path})
    assert p.returncode == 0 and "after=1" in p.stdout, p.stdout + p.stderr


def _samplers_then_evidence(tmp_path, prelude):
    """start_samplers as the topic job runs it (the guest half stubbed; `prelude` breaks something),
    then the topic job's evidence step on the same WORK and EV. A setsid shim records the sampler's
    pid. Returns (start rc, evidence step, sums written, the sampler alive after the step)."""
    work, ev, stub = tmp_path / "work", tmp_path / "ev", tmp_path / "stub"
    for d in (work, ev, stub):
        d.mkdir()
    (ev / "log.txt").write_text("ok\n")
    launched = tmp_path / "launched.pid"
    (stub / "setsid").write_text(f'#!/bin/bash\necho $$ > {launched}\nexec {shutil.which("setsid")} "$@"\n')
    (stub / "setsid").chmod(0o755)
    env = {"EV": str(ev), "WORK": str(work), "PATH": f"{stub}:/usr/bin:/bin", "GITHUB_WORKSPACE": str(repo_paths.REPO),
           "LF": str(tmp_path / "lf"), "GITHUB_OUTPUT": str(tmp_path / "out")}
    for w in ("qemu.pid", "runner-sampler.pid"):
        (work / f"{w}.state").write_text("none\n")
    # On exit the script waits for the shim's pid only when it launched a job: a launch is then known
    # for sure, and no launch is known for sure when there is no job.
    script = f"""set -euo pipefail
trap 'if [ -n "$(jobs -p)" ]; then until [ -s {launched} ]; do sleep 0.01; done; fi' EXIT
. {LANE / "guest.sh"}
g() {{ :; }}; gcp() {{ :; }}
{prelude}
start_samplers
"""
    start = subprocess.run(["bash", "-c", script], capture_output=True, text=True, check=False, env=env)
    pgid = int(launched.read_text()) if launched.exists() else None
    try:
        published = (work / "runner-sampler.pid").exists() and (work / "runner-sampler.pid").read_text().startswith("grp:")
        if pgid is not None and published:          # the sampler's first line: it runs, in its group
            subprocess.run(["timeout", "30", "bash", "-c", 'until [ -s "$1" ]; do sleep 0.1; done', "_",
                            str(ev / "runner-load.txt")], check=True)
        step = next(s for s in _jobs()["topic"] if "id: evidence" in s)
        p = subprocess.run(["bash", "-e", "-c", _run_block(step)], capture_output=True, text=True, check=False,
                           env=env)
        alive = pgid is not None and subprocess.run(["pgrep", "-g", str(pgid)], capture_output=True,
                                                    check=False).returncode == 0
    finally:
        if pgid is not None:
            subprocess.run(["kill", "-9", "--", f"-{pgid}"], capture_output=True, check=False)
    return start.returncode, p, (ev / "SHA256SUMS").exists(), alive


@pytest.mark.parametrize("case, prelude", [
    # the pidfile cannot be written at all (a dangling link): no file is ever created
    ("pidfile-unwritable", 'ln -s "$WORK/no/such/dir/x" "$WORK/runner-sampler.pid"'),
    # the sampler's own publication fails (its rename into the pidfile fails): the sentinel stays
    ("publication-fails", 'mkdir -p "$WORK/stubbin"; printf "#!/bin/bash\\ncase \\"\\$*\\" in *runner-sampler.pid.tmp*) '
                          'exit 1;; esac\\nexec /usr/bin/mv \\"\\$@\\"\\n" > "$WORK/stubbin/mv"; '
                          'chmod +x "$WORK/stubbin/mv"; PATH="$WORK/stubbin:$PATH"'),
], ids=["pidfile-unwritable", "publication-fails"])
def test_a_sampler_whose_pidfile_is_not_published_never_runs_beside_an_upload(tmp_path, case, prelude):
    # G1-LBS-3 B6, LANEB-G2-TABLES-v7 A6: a sampler must never run without a pidfile that names it or blocks the
    # upload: the state record says "launching" and the sentinel stays until the sampler itself publishes its group.
    start_rc, p, sums, alive = _samplers_then_evidence(tmp_path, prelude)
    assert start_rc != 0, case
    assert not (sums and alive), (case, p.stdout, p.stderr)       # never a live writer under the sums
    if alive:
        assert p.returncode == 1 and "evidence: a writer" in p.stdout and not sums, (case, p.stdout, p.stderr)
