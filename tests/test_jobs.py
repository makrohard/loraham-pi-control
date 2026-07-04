

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
