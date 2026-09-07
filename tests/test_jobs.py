

def test_no_ld_preload_wrapping_in_job_execution():
    # LIVE FINDING: stdbuf's LD_PRELOAD propagated into programs UNDER TEST (daemon
    # suite's single-read pipe capture raced line-buffered output -> flaky FAIL under
    # load). Job/step execution must never wrap commands in stdbuf; PYTHONUNBUFFERED
    # (an env var honored only by python itself) is the allowed unbuffering mechanism.
    import pathlib
    for mod in ("lhpc/core/jobs.py", "lhpc/core/build_launcher_runtime.py"):
        src = pathlib.Path(mod).read_text()
        assert "/usr/bin/stdbuf" not in src, mod          # no executable wrapping
        assert "PYTHONUNBUFFERED" in src, mod


def test_write_job_marker_records_only_a_complete_identity_and_blanks_a_bad_attempt(tmp_path):
    import os
    from lhpc.core import jobs, procident
    from lhpc.core.paths import Paths
    paths = Paths(runtime_root=tmp_path); (tmp_path / "state" / "jobs").mkdir(parents=True)
    ident = procident.proc_identity(os.getpid())
    assert jobs.write_job_marker(paths, "build-x", os.getpid(), "daemon", "build", ident=ident,
                                 attempt_id="not-hex!") is True
    body = (tmp_path / "state" / "jobs" / "build-x.job").read_text()
    assert 'attempt_id = ""' in body and f"pid = {os.getpid()}" in body and 'log = "build-x"' in body
    assert jobs.write_job_marker(paths, "build-y", os.getpid(), "daemon", "build",
                                 ident={**ident, "starttime": -1}) is False        # incomplete identity
    assert not (tmp_path / "state" / "jobs" / "build-y.job").exists()


def test_prune_ephemeral_launchers_fails_closed_on_a_symlinked_subdir(tmp_path):
    import os
    from lhpc.core import jobs
    from lhpc.core.paths import Paths
    paths = Paths(runtime_root=tmp_path); (tmp_path / "state").mkdir()
    outside = tmp_path / "outside"; outside.mkdir(); (outside / "s.py").write_text("SENTINEL")
    os.symlink(outside, tmp_path / "state" / "jobs")
    assert jobs.prune_ephemeral_launchers(paths, 0) == 0
    assert (outside / "s.py").read_text() == "SENTINEL"
