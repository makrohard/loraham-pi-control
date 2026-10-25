"""The lane-B checks (.github/scripts/lane_b.py) that the QB-1 job relies on to fail closed: a pinned image
whose digest differs is refused before any boot, and a guest that is not a guest (the runner's own
kernel, another architecture, no Pi OS userland, not Trixie) fails the job."""
import os
import re
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


def _live(tmp_path, apply_rc, apply_text, repaired):
    d = tmp_path / "live"
    d.mkdir()
    (d / "U0-apply.txt").write_text(f"{apply_text}\nrc={apply_rc}\n")
    if repaired:
        (d / "U0-repair.txt").write_text("OK    Web + one-click updater integration installed/repaired.\nrc=0\n")
    return d


_STALE = ("ERR   Update applied — units-stale: the managed systemd units could NOT be refreshed", 1, True)
_CLEAN = ("OK    Update applied.", 0, False)


@pytest.mark.parametrize("want, case, ok", [
    ("units-stale", _STALE, True), ("rc0", _CLEAN, True),
    ("units-stale", _CLEAN, False), ("rc0", _STALE, False),      # the other branch ran: the job fails
])
def test_each_u0_job_requires_the_branch_it_exists_for(tmp_path, want, case, ok):
    text, rc, repaired = case
    p = _run("branch", want, _live(tmp_path, rc, text, repaired))
    assert (p.returncode == 0) is ok, p.stderr


@pytest.mark.parametrize("text, ok", [
    ("active\nactive\n", True), ("inactive\nactive\n", False), ("active\ninactive\n", False),
    ("active\n", False), ("", False), ("active\nactive\nactive\n", False),
])
def test_the_topic_job_passes_only_when_both_units_are_active(tmp_path, text, ok):
    f = tmp_path / "units-active.txt"
    f.write_text(text)
    assert (_run("active", 2, f).returncode == 0) == ok


def test_the_topic_job_asks_the_check_for_both_units():
    u0b = (repo_paths.REPO / ".github" / "lane-b" / "u0b.sh").read_text()
    assert 'lane_b.py" active 2 "$EV/units-active.txt"' in u0b


WORKFLOW = repo_paths.REPO / ".github" / "workflows" / "lane-b.yml"


def _jobs():
    """{job: [step text]} of lane-b.yml, split by indentation (no YAML parser in the dev dependencies)."""
    parts = re.split(r"\n  ([\w-]+):\n", "\n" + WORKFLOW.read_text().split("\njobs:\n", 1)[1])
    return {name: body.split("\n      - ")[1:] for name, body in zip(parts[1::2], parts[2::2])}


def test_an_artifact_uploads_only_after_its_redaction_guard_passed():
    # A failed redaction check must stop the upload: `if: always()` on the upload ran it anyway.
    uploaded = 0
    for job, steps in _jobs().items():
        for up in (s for s in steps if s.startswith("uses: actions/upload-artifact")):
            uploaded += 1
            guard = re.search(r"if: always\(\) && steps\.(\w+)\.outcome == 'success'", up)
            assert guard, (job, up.splitlines()[:3])
            owner = [s for s in steps if f"id: {guard.group(1)}" in s]
            assert len(owner) == 1 and "PRIVATE KEY-----|SSHPASS=" in owner[0] and "exit 1" in owner[0], job
    assert uploaded == 2                                       # QB-1 and U0-B


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
    for job, steps in _jobs().items():
        step = next(s for s in steps if "id: evidence" in s)
        p = subprocess.run(["bash", "-e", "-c", _run_block(step)], capture_output=True, text=True,
                           env={"EV": str(ev), "WORK": str(work), "PATH": "/usr/bin:/bin",
                                "GITHUB_WORKSPACE": str(repo_paths.REPO)})
        assert p.returncode == rc, (job, case, p.stdout, p.stderr)


LANE = repo_paths.REPO / ".github" / "lane-b"


def _evidence(tmp_path, writer=None, pidfile_text=None, path_extra=None):
    """Each job's evidence step as the runner runs it, with an optional live writer into the evidence (a process
    group that appends; on TERM it writes `writer` once more and exits) named by runner-sampler.pid. The writer is
    an orphan, as the job's writers are when the step runs (their starter has exited), so nothing here holds it as
    an unreaped child."""
    ev, work = tmp_path / "ev", tmp_path / "work"
    ev.mkdir(exist_ok=True)
    work.mkdir(exist_ok=True)
    (ev / "log.txt").write_text("ok\n")
    results = []
    for job, steps in _jobs().items():
        pgid = None
        if writer is not None:
            (ev / "w.txt").unlink(missing_ok=True)
            on_term = f"trap 'echo \"{writer}\" >> {ev}/w.txt; exit 0' TERM"
            loop = f"{on_term}; echo up >> {ev}/w.txt; while :; do echo tick >> {ev}/w.txt; sleep 0.05; done"
            start = subprocess.run(["bash", "-c", 'setsid bash -c "$1" > /dev/null 2>&1 & echo $!', "_", loop],
                                   capture_output=True, text=True, check=True)
            pgid = int(start.stdout)
            while not (ev / "w.txt").exists():
                pass
            (work / "runner-sampler.pid").write_text(f"-{pgid}\n")
        elif pidfile_text is not None:
            (work / "runner-sampler.pid").write_text(pidfile_text)
        step = next(s for s in steps if "id: evidence" in s)
        path = (f"{path_extra}:" if path_extra else "") + "/usr/bin:/bin"
        p = subprocess.run(["bash", "-e", "-c", _run_block(step)], capture_output=True, text=True, check=False,
                           env={"EV": str(ev), "WORK": str(work), "PATH": path,
                                "GITHUB_WORKSPACE": str(repo_paths.REPO)})
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
    ("\n", None, "not a pid"), ("12-3\n", None, "not a pid"),
    ("-999999\n", 0, "still runs"),                           # the writer never goes: the bound, then no upload
    ("-999999\n", 2, "the liveness check failed"),            # the check itself fails: no upload either
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


def test_the_runner_sampler_is_a_process_group_the_stop_ends(tmp_path):
    # start_samplers as the job runs it (the guest half stubbed): the pidfile names the sampler's own group, and
    # stop_writer leaves no process of it behind.
    script = f"""set -euo pipefail
WORK={tmp_path}; EV={tmp_path}; GITHUB_WORKSPACE={repo_paths.REPO}; . {LANE / "guest.sh"}
g() {{ :; }}; gcp() {{ :; }}
start_samplers
t=$(cat {tmp_path}/runner-sampler.pid); [ "${{t#-}}" != "$t" ]
pgrep -g "${{t#-}}" > /dev/null
stop_writer {tmp_path}/runner-sampler.pid
r=0; pgrep -g "${{t#-}}" > /dev/null || r=$?; echo "after=$r"
"""
    p = subprocess.run(["bash", "-c", script], capture_output=True, text=True, check=False, timeout=90)
    assert p.returncode == 0 and "after=1" in p.stdout, p.stdout + p.stderr
