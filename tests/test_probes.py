

def test_process_match_accepts_pip_console_script_form():
    # LIVE FINDING: 'meshcli' is a pip console script — the kernel executes it as
    # "<venv>/bin/python3.13 <venv>/bin/meshcli …", so exec_name='meshcli' never
    # matched and the dashboard kept 'MeshCore CLI' at 'stopped' while it was running.
    # The probe now accepts the console-script form: python-interpreter argv[0] AND the
    # SCRIPT token's exact basename — never a substring guess.
    from lhpc.core.model import ProcessSpec
    from lhpc.core.probes.process import matches
    spec = ProcessSpec(exec_name="meshcli")
    assert matches(spec, ["/r/src/meshcore-cli/.venv/bin/python3.13",
                          "/r/src/meshcore-cli/.venv/bin/meshcli", "127.0.0.1", "5000"])
    assert matches(spec, ["/r/.venv/bin/meshcli", "127.0.0.1"])  # direct exec still ok
    assert not matches(spec, ["/usr/bin/python3", "/tmp/evil-meshcli-lookalike.py"])
    assert not matches(spec, ["/usr/bin/python3", "/x/meshcli.py"])   # exact basename
    assert not matches(spec, ["/usr/bin/perl", "/x/meshcli"])         # python only
    assert not matches(spec, ["/usr/bin/python3"])                    # no script token
