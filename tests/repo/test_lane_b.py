"""The lane-B checks (.github/scripts/lane_b.py) that the QB-1 job relies on to fail closed: a pinned image
whose digest differs is refused before any boot, and a guest that is not a guest (the runner's own
kernel, another architecture, no Pi OS userland, not Trixie) fails the job."""
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
