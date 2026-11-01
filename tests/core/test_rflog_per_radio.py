"""The daemon's RF log per radio (B4-WIRING-TABLES-v1 W7-8a; B4-D23, B4-D24): `logs/<rid>/rf-daemon.log`.

The registry authorizes exactly `r<1-99>/rf-daemon.log` one level under logs/ (`by_job` → the daemon entry and the
rid), the 0.13.x band files stay listed read-only while they exist, and a prune never deletes either form. The
existing registry functions (`by_job`, the Entry's jobs) give a behavioural red-before on the release before L5;
`radio_job`, `radio_dir_ok`, `list_jobs` and `prune_targets` are new there.
"""
from __future__ import annotations

import pytest

from lhpc.core import rflog
from lhpc.core.paths import Paths, resolve_paths


def _daemon():
    return rflog.entry("daemon")


def _r(rid, band="868", enabled=True):
    from lhpc.core import radios
    return radios.Radio(id=rid, name=f"Radio {rid}", model="loraham", band=band, enabled=enabled)


# ---- the rid form is a registered job ---------------------------------------------------------------

@pytest.mark.parametrize("rid", ["r1", "r12", "r99"])
def test_by_job_accepts_a_radios_own_log_and_names_the_rid(rid):
    got = rflog.by_job(f"{rid}/rf-daemon.log")
    assert got is not None and got[0].surface == "daemon" and got[1] == rid


@pytest.mark.parametrize("band", ["433", "868"])
def test_by_job_still_knows_the_013x_band_files(band):
    got = rflog.by_job(f"rf-daemon-{band}.log")
    assert got is not None and got[0].surface == "daemon" and got[1] == band


@pytest.mark.parametrize("job", [
    "r0/rf-daemon.log", "r100/rf-daemon.log", "R1/rf-daemon.log", "r01/rf-daemon.log", "r1//rf-daemon.log",
    "r1/../x", "../rf-daemon.log", "r1/../rf-daemon.log", "r1/rf-daemon.log\n", " r1/rf-daemon.log",
    "logs/r1/rf-daemon.log", "r1/r2/rf-daemon.log", "r1/rf-daemon.log.bak", "r1/rf-kiss.log", "r1\\rf-daemon.log",
    "/r1/rf-daemon.log", "r1/", "rf-daemon.log", "rf-daemon-915.log", "",
])
def test_by_job_refuses_every_other_name(job):
    assert rflog.by_job(job) is None


@pytest.mark.parametrize("job", [None, 1, b"r1/rf-daemon.log", ["r1/rf-daemon.log"], True])
def test_by_job_refuses_a_non_string_and_never_raises(job):
    assert rflog.by_job(job) is None


# ---- the daemon entry's job for a radio ------------------------------------------------------------

def test_the_daemon_entry_names_a_radios_job():
    assert _daemon().radio_job("r7") == "r7/rf-daemon.log"


@pytest.mark.parametrize("rid", ["r0", "r100", "R1", "", None, 1, "r1/x", "r1\n"])
def test_no_job_for_a_value_that_is_no_radio_id(rid):
    assert _daemon().radio_job(rid) is None


@pytest.mark.parametrize("surface", ["graywolf", "meshcom", "meshtastic", "meshcore", "reticulum"])
def test_only_the_daemon_logs_per_radio(surface):
    assert rflog.entry(surface).radio_job("r1") is None


# ---- the radio's directory is real (no symlink above the no-follow leaf read) --------------------------

def _paths(tmp_path):
    return resolve_paths({"LHPC_RUNTIME_ROOT": str(tmp_path)})


def test_radio_dir_ok_for_a_real_directory_only(tmp_path):
    (tmp_path / "logs" / "r1").mkdir(parents=True)
    other = tmp_path / "elsewhere"
    other.mkdir()
    (tmp_path / "logs" / "r2").symlink_to(other, target_is_directory=True)
    (tmp_path / "logs" / "r3").write_text("not a directory", encoding="utf-8")
    p = _paths(tmp_path)
    assert rflog.radio_dir_ok(p, "r1/rf-daemon.log") is True
    assert rflog.radio_dir_ok(p, "r2/rf-daemon.log") is False
    assert rflog.radio_dir_ok(p, "r3/rf-daemon.log") is False
    assert rflog.radio_dir_ok(p, "r4/rf-daemon.log") is False


def test_radio_dir_ok_refuses_a_logs_dir_out_of_the_root(tmp_path):
    root, outside = tmp_path / "root", tmp_path / "outside"
    root.mkdir()
    (outside / "r1").mkdir(parents=True)
    (root / "logs").symlink_to(outside, target_is_directory=True)
    assert rflog.radio_dir_ok(resolve_paths({"LHPC_RUNTIME_ROOT": str(root)}), "r1/rf-daemon.log") is False


def test_radio_dir_ok_does_not_judge_other_jobs(tmp_path):
    assert rflog.radio_dir_ok(_paths(tmp_path), "rf-kiss.log") is True


# ---- the viewer's list and the prune ---------------------------------------------------------------

def test_list_jobs_one_per_enabled_radio_in_id_order_then_the_legacy_files_read_only():
    radios = [_r("r10", "868"), _r("r2", "433"), _r("r3", "868", enabled=False)]
    jobs = rflog.list_jobs(radios, ["rf-daemon-433.log", "start-x.log", "r2"])
    assert [(j.key, j.job, j.readonly) for j in jobs] == [
        ("r2", "r2/rf-daemon.log", False), ("r10", "r10/rf-daemon.log", False),
        ("433", "rf-daemon-433.log", True)]
    assert "r2" in jobs[0].label and "433" in jobs[0].label


def test_list_jobs_lists_no_legacy_file_that_is_not_there():
    assert [j.job for j in rflog.list_jobs([_r("r1")], [])] == ["r1/rf-daemon.log"]


def test_prune_never_deletes_an_rf_log_in_either_form_nor_what_the_rule_keeps():
    """`prune_targets` judges names relative to logs/, files only: a band file at the top and a radio's log in the
    nested form `<rid>/rf-daemon.log`. A top-level directory entry such as `r1` is not a log name; the caller
    (service_jobs' prune) passes only non-symlink top-level names ending `.log`, so `r1` never reaches it."""
    entries = ["r1/rf-daemon.log", "rf-daemon-433.log", "rf-kiss.log", "start-x.log", "junk.log", "r0/rf-daemon.log"]
    assert rflog.prune_targets(entries, lambda n: n == "start-x.log") == ["junk.log", "r0/rf-daemon.log"]


def test_the_prune_through_the_service_keeps_every_radios_log_and_the_band_files(tmp_path):
    """The public path: the service's prune under count pressure deletes old job logs but never a 0.13.x band file.
    Its caller filters non-symlink top-level names ending `.log` through prune_targets, then keeps regular files
    only. `logs/<rid>/` and its `rf-daemon.log` survive because the listing is one level (not recursive); this test
    shows they survive, not which rule keeps them."""
    import os

    from lhpc.core.probes.backends import FakeSystem
    from lhpc.core.services import ControllerService
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    logs = tmp_path / "logs"
    (logs / "r1").mkdir(parents=True)
    keep = [logs / "r1" / "rf-daemon.log", logs / "rf-daemon-433.log"]
    old = 1_000_000_000
    for p in keep:
        p.write_bytes(b"rf\n")
        os.utime(p, (old, old))
    os.utime(logs / "r1", (old, old))
    for i in range(svc.LOG_RETENTION + 5):
        p = logs / f"build-{i:04d}.log"
        p.write_bytes(b"x\n")
        os.utime(p, (old + 1000 + i, old + 1000 + i))
    assert svc.prune_logs() > 0
    assert (logs / "r1").is_dir() and all(p.exists() for p in keep)
