"""calibrate.sh times the SD card, not RAM: its io part refuses a tmpfs work dir and one too small.

The first row A on a Zero 2 W wrote the io part into `mktemp -d /tmp/...`: /tmp there is a 208 MB
tmpfs, and 256 MB died with ENOSPC after 105 s. Even where it fits, io on tmpfs measures memory.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = next(p for p in Path(__file__).resolve().parents if (p / "lhpc" / "version.py").is_file())
SCRIPT = REPO / "testlab" / "slowbuild" / "calibrate.sh"


def _fstype(path: Path) -> str:
    return subprocess.run(["stat", "-f", "-c", "%T", str(path)], capture_output=True, text=True,
                          check=True).stdout.strip()


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", str(SCRIPT), *args], capture_output=True, text=True,
                          timeout=600, check=False)


def test_a_tmpfs_work_dir_is_refused():
    shm = Path("/dev/shm")
    if not shm.is_dir() or _fstype(shm) != "tmpfs":
        pytest.skip("no tmpfs at /dev/shm on this box")
    work = shm / "lhpc-calib-test"
    r = _run("--work-dir", str(work), "--io-mb", "1")
    assert r.returncode == 1, r.stdout + r.stderr
    assert re.search(r"refused: .*lhpc-calib-test is on tmpfs", r.stderr), r.stderr
    assert "cpu=" not in r.stdout
    assert not work.exists(), "the refused work dir must be removed"


def test_too_little_free_space_is_refused_with_the_numbers(tmp_path):
    if _fstype(tmp_path) in ("tmpfs", "ramfs"):
        pytest.skip("pytest's tmp dir is on tmpfs on this box")
    work = tmp_path / "calib"
    r = _run("--work-dir", str(work), "--io-mb", str(2**30))
    assert r.returncode == 1, r.stdout + r.stderr
    assert re.search(r"refused: .* MB free, the io part needs \d+ MB \+ 64 MB", r.stderr), r.stderr
    assert not work.exists()


def test_a_disk_work_dir_runs_and_is_removed_and_the_io_size_is_hashed(tmp_path):
    """Runs on disk. A calibration recorded with a test-sized io part never matches the real
    workload: the io size is part of the workload hash."""
    if _fstype(tmp_path) in ("tmpfs", "ramfs"):
        pytest.skip("pytest's tmp dir is on tmpfs on this box")
    hashes = set()
    for mb in ("1", "2"):
        work = tmp_path / f"calib{mb}"
        r = _run("--work-dir", str(work), "--io-mb", mb)
        assert r.returncode == 0, r.stdout + r.stderr
        m = re.fullmatch(r"cpu=[\d.]+ io=[\d.]+ mem=[\d.]+ workload=sha256:([0-9a-f]{64})\n",
                         r.stdout)
        assert m, r.stdout
        hashes.add(m.group(1))
        assert not work.exists()
    assert len(hashes) == 2


def test_an_existing_work_dir_is_never_reused_or_removed(tmp_path):
    work = tmp_path / "calib"
    work.mkdir()
    (work / "keep").write_text("x")
    r = _run("--work-dir", str(work), "--io-mb", "1")
    assert r.returncode == 1 and "already exists" in r.stderr, r.stderr
    assert (work / "keep").is_file()


@pytest.mark.parametrize("installed", [True, False])
def test_the_default_work_dir_is_on_disk_and_never_tmpdir(tmp_path, installed):
    """Inside an install: <root>/state/lhpc-calib; elsewhere $HOME/.cache/lhpc-calib. TMPDIR is
    not consulted. An oversized io part makes it refuse right after naming the dir."""
    if _fstype(tmp_path) in ("tmpfs", "ramfs"):
        pytest.skip("pytest's tmp dir is on tmpfs on this box")
    root, home = tmp_path / "root", tmp_path / "home"
    checkout = root / "src" / "loraham-pi-control" if installed else tmp_path / "dev-checkout"
    (checkout / "testlab").mkdir(parents=True)
    shutil.copytree(SCRIPT.parent, checkout / "testlab" / "slowbuild")
    (root / "state").mkdir(parents=True)
    home.mkdir()
    r = subprocess.run(["bash", str(checkout / "testlab" / "slowbuild" / "calibrate.sh"),
                        "--io-mb", str(2**30)], capture_output=True, text=True, timeout=60,
                       check=False, env={**os.environ, "HOME": str(home), "TMPDIR": "/dev/shm"})
    want = root / "state" / "lhpc-calib" if installed else home / ".cache" / "lhpc-calib"
    assert r.returncode == 1 and f"work dir {want} has " in r.stderr, r.stderr
    assert not want.exists()


def test_stale_artefacts_in_calib_src_never_shorten_the_measured_build(tmp_path):
    """The cpu part builds exactly the hashed inputs (the .c files and the Makefile): an object or
    a `calib` left in calib-src (a manual `make` there) is not copied, so every source is still
    compiled and linked — never a partial or no-op build the workload hash does not cover."""
    if _fstype(tmp_path) in ("tmpfs", "ramfs"):
        pytest.skip("pytest's tmp dir is on tmpfs on this box")
    lab = tmp_path / "slowbuild"
    shutil.copytree(SCRIPT.parent, lab)
    src = lab / "calib-src"
    sources = sorted(p.name for p in src.glob("*.c"))
    subprocess.run(["make", "-s", "-C", str(src)], check=True, capture_output=True)  # stale build
    assert (src / "calib").is_file() and all((src / s).with_suffix(".o").is_file()
                                              for s in sources)
    log = tmp_path / "cc.log"
    cc = tmp_path / "cc-log"
    cc.write_text(f'#!/bin/sh\necho "$*" >> {log}\nexec cc "$@"\n')
    cc.chmod(0o755)
    r = subprocess.run(["bash", str(lab / "calibrate.sh"), "--work-dir", str(tmp_path / "w"),
                        "--io-mb", "1"], capture_output=True, text=True, timeout=600, check=False,
                       env={**os.environ, "CC": str(cc)})
    assert r.returncode == 0, r.stdout + r.stderr
    calls = log.read_text().splitlines() if log.exists() else []
    compiled = sorted(c.split()[-1] for c in calls if " -c " in f" {c} ")
    assert compiled == sources, calls
    assert sum(1 for c in calls if " -c " not in f" {c} ") == 1, calls     # the link
