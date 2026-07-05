"""Structured command execution: argv token boundaries, no shell on the migrated
run path, and the shell-to-exec ownership-race fix (controlled real subprocesses)."""

import os
import signal
import subprocess

import pytest

from lhpc.core import commands
from lhpc.core.model import RunParam, Component, ComponentKind, Stack
from lhpc.core.lifecycle import Lifecycle
from lhpc.core.config import Config, OperatorConfig
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService


# --- token boundaries ---------------------------------------------------------

def test_emit_param_returns_separate_tokens():
    from lhpc.core.model import emit_param
    opt = RunParam("radio", kind="enum", choices=("433", "868"), arg="--radio")
    assert emit_param(opt, "433") == ["--radio", "433"]          # two tokens
    flag = RunParam("debug", kind="flag", flag="--debug")
    assert emit_param(flag, "on") == ["--debug"] and emit_param(flag, "") == []
    pos = RunParam("freq", kind="str")
    assert emit_param(pos, "433.775") == ["433.775"]             # one token


def test_hostile_value_stays_one_token():
    comp = Component(id="c", name="c", kind=ComponentKind.SERVICE,
                     run_params=(RunParam("name", kind="str", validator="node"),))
    op = OperatorConfig()
    # A value with a space (allowed by node names) stays ONE argv token.
    argv = commands.expand_argv(["app", "{param:name}"], comp, {"name": "My Node"},
                                op, "/rt", "/src")
    assert argv == ["app", "My Node"]


def test_expand_argv_rejects_embedded_user_placeholder():
    comp = Component(id="c", name="c", kind=ComponentKind.SERVICE,
                     run_params=(RunParam("x", kind="str"),))
    with pytest.raises(commands.CommandError):
        commands.expand_argv(["app", "--opt={param:x}"], comp, {"x": "v"},
                             OperatorConfig(), "/rt", "/src")


def test_migrated_daemon_run_is_structured_no_shell(tmp_path):
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    comp = svc.stack("daemon").component("loraham-daemon")
    assert comp.run_argv and comp.readiness == "daemon-band"
    argv = commands.expand_argv(comp.run_argv, comp, {"radio": "433", "debug": "on"},
                                OperatorConfig(), str(tmp_path), str(tmp_path))
    assert "/bin/sh" not in argv and "sh" != argv[0]
    assert argv[:3] == ["loraham_daemon/loraham_daemon", "--radio", "433"]
    assert "--debug" in argv                                     # flag on -> one token


# --- ownership-race: direct exec records the real executable ------------------

@pytest.fixture
def reaper():
    procs = []
    yield procs
    for p in procs:
        try:
            p.kill(); p.wait(timeout=2)
        except Exception:
            pass


def _life(tmp_path):
    return Lifecycle(Paths(runtime_root=tmp_path), (),
                     Config(operator=OperatorConfig()), FakeSystem().system)


def test_structured_start_records_real_executable_not_shell(tmp_path):
    life = _life(tmp_path)
    life.OBSERVE_TIMEOUT_S = 3.0          # exercise the real observation path
    comp = Component(id="sleeper", name="s", kind=ComponentKind.SERVICE,
                     run_argv=("sleep", "{param:dur}"),
                     run_params=(RunParam("dur", kind="str", default="30"),))
    res = life.start(Stack(id="s", name="s", main="sleeper"), comp)
    assert res.ok, res.detail
    recs = life.owned_records("sleeper")
    assert len(recs) == 1 and recs[0]["exec"] == "sleep"        # real exec, not /bin/sh
    life.stop(comp)                                            # cleanup


def test_identity_mismatch_leaves_no_record_and_terminates(tmp_path, reaper):
    import time
    life = _life(tmp_path)
    life.OBSERVE_TIMEOUT_S = 1.0
    # A real session leader is running, but its observed argv will NOT match the
    # intended argv (this is exactly the shell-to-exec race: the process LHPC ends
    # up with is not the one it intended). Ownership must NOT be recorded, and the
    # just-created session must be terminated.
    p = subprocess.Popen(["sleep", "30"], start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    reaper.append(p)
    comp = Component(id="racer", name="r", kind=ComponentKind.SERVICE)
    ok = life._observe_and_record(Stack(id="r", name="r", main="racer"),
                                  comp, p.pid, "", ["sleep", "999"])   # mismatch
    assert ok != "ok"                                          # not owned
    assert ok in ("ceased", "unverified")                      # typed cleanup status
    assert life.owned_records("racer") == []
    time.sleep(0.3)
    assert not life._proc_alive(p.pid)                          # session terminated


def test_all_command_bearing_components_are_migrated():
    from lhpc.core.manifest import load_manifest
    unmigrated = []
    for s in load_manifest():
        for c in s.components:
            if c.run_cmd and not c.run_argv:
                unmigrated.append((c.id, "run"))
            if c.build_cmd and not c.build_steps:
                unmigrated.append((c.id, "build"))
            if c.test_cmd and not c.test_argv:
                unmigrated.append((c.id, "test"))
            if c.post_start and not c.post_steps and not c.interactive:
                unmigrated.append((c.id, "post"))
    assert unmigrated == []


def test_no_shell_in_lifecycle_and_job_sources():
    # Belt-and-suspenders: the execution modules contain no shell invocation.
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[1] / "lhpc" / "core"
    for name in ("lifecycle.py", "jobs.py", "commands.py"):
        text = (root / name).read_text()
        assert "/bin/sh" not in text and "shell=True" not in text
        assert "sh -c" not in text and "bash -c" not in text


def test_started_process_argv_is_not_a_shell(tmp_path):
    # Capture the argv LHPC actually spawns for the (migrated) daemon: it must be
    # the real executable, never /bin/sh -c.
    from lhpc.core.manifest import load_manifest
    from conftest import real_spawn
    captured = {}
    def spy(argv, log, cwd=None, env=None):
        captured["argv"], captured["cwd"] = argv, cwd
        return real_spawn(argv, log, cwd, env)   # real process -> ownership records
    daemon = [c for s in load_manifest() for c in s.components if c.id == "loraham-daemon"][0]
    life = Lifecycle(Paths(runtime_root=tmp_path), (), Config(operator=OperatorConfig()),
                     FakeSystem().system, spawn=spy)
    life.OBSERVE_TIMEOUT_S = 0.0
    res = life.start(Stack(id="daemon", name="d", main="loraham-daemon"), daemon,
                     {"radio": "433"})
    assert res.ok
    assert captured["argv"][0] == "loraham_daemon/loraham_daemon"
    assert "/bin/sh" not in captured["argv"] and "-c" not in captured["argv"][:1]


# --- §4.4 @file / @env fail-closed -------------------------------------------

def test_at_file_secret_missing_blocks(tmp_path):
    from lhpc.core import commands
    with pytest.raises(commands.CommandError):
        commands.build_env(((("XR_PW", f"@file:{tmp_path}/nope.pw"),)), str(tmp_path), str(tmp_path))


def test_at_file_secret_empty_blocks(tmp_path):
    from lhpc.core import commands
    (tmp_path / "blank.pw").write_text("\n")
    with pytest.raises(commands.CommandError):
        commands.build_env(((("XR_PW", f"@file:{tmp_path}/blank.pw"),)), str(tmp_path), str(tmp_path))


def test_at_file_secret_present_is_read(tmp_path):
    from lhpc.core import commands
    (tmp_path / "ok.pw").write_text("s3cret\nignored\n")
    env = commands.build_env(((("XR_PW", f"@file:{tmp_path}/ok.pw"),)), str(tmp_path), str(tmp_path))
    assert env["XR_PW"] == "s3cret"


def test_invalid_env_name_rejected(tmp_path):
    from lhpc.core import commands
    with pytest.raises(commands.CommandError):
        commands.build_env(((("BAD NAME", "x"),)), str(tmp_path), str(tmp_path))


def test_at_env_default_only_when_declared(tmp_path, monkeypatch):
    from lhpc.core import commands
    monkeypatch.delenv("LHPC_TEST_VAR", raising=False)
    # no "=" -> no default declared -> empty (not the literal spec)
    env = commands.build_env(((("V", "@env:LHPC_TEST_VAR"),)), str(tmp_path), str(tmp_path))
    assert env["V"] == ""
    env2 = commands.build_env(((("V", "@env:LHPC_TEST_VAR=fallback"),)), str(tmp_path), str(tmp_path))
    assert env2["V"] == "fallback"


# --- Workstream D: web-job build launcher fails closed -----------------------

def test_build_launcher_at_file_missing_blocks(tmp_path):
    # A required @file: secret is now resolved fail-closed at EXEC time (not render), so
    # the secret value is never baked into the launcher; a missing secret fails the BUILD.
    import subprocess, sys
    from lhpc.core import commands
    steps = [{"argv": ["true"], "env": {"XR_PW": f"@file:{tmp_path}/nope.pw"}}]
    script = commands.render_build_launcher(steps, str(tmp_path), str(tmp_path))
    assert "@file:" in script                             # token carried, resolved at exec
    f = tmp_path / "b.py"; f.write_text(script)
    rc = subprocess.run([sys.executable, str(f)], capture_output=True, text=True, timeout=20)
    assert rc.returncode != 0 and "build env error" in rc.stderr


def test_build_launcher_pkgconfig_failure_exits_nonzero(tmp_path):
    import subprocess, sys
    from lhpc.core import commands
    steps = [{"argv": ["true", "{pkgconfig:lhpc-nonexistent-xyz}"]}]
    script = commands.render_build_launcher(steps, str(tmp_path), str(tmp_path))
    f = tmp_path / "b.py"
    f.write_text(script)
    rc = subprocess.run([sys.executable, str(f)], capture_output=True, text=True, timeout=20)
    assert rc.returncode != 0 and "pkg-config failed" in rc.stderr


# --- readiness-gated, ack-aware tcp_send (MeshCom setcall live finding) -----------------------

def _mk_comp_with_post(post):
    from lhpc.core.model import Component, ComponentKind, RunParam
    return Component(id="c", name="c", kind=ComponentKind.SERVICE,
                     run_params=(RunParam(name="mc_callsign", kind="str",
                                          validator="callsign", default="OE1ABC"),),
                     post_steps=tuple(post))


def _render(post, params=None):
    from lhpc.core import commands

    class _Op:
        callsign = "OE1ABC"
        locator = "JN88"
    comp = _mk_comp_with_post(post)
    return commands.render_post_launcher(list(post), comp, params or {}, _Op(),
                                         "/rt", "/rt/src", "")


def test_tcp_send_probe_fields_are_rendered_and_param_expanded():
    code = _render([{"kind": "tcp_send", "port": 1, "data": "--setcall {param:mc_callsign}\n",
                     "probe": "--info\n", "probe_stop_on": "Call:{param:mc_callsign}",
                     "stop_on": "Call:{param:mc_callsign}"}])
    assert "'probe': '--info\\n'" in code
    assert "Call:OE1ABC" in code                                 # {param} expanded
    compile(code, "<launcher>", "exec")                          # valid python


def _serve(behavior):
    """Tiny TCP server thread: behavior(list_of_received_payload_lines) -> reply per
    connection based on a mutable state; returns (port, received, stop)."""
    import socket, threading
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(8)
    port = srv.getsockname()[1]
    received = []
    stopped = {"v": False}
    def loop():
        srv.settimeout(0.3)
        while not stopped["v"]:
            try:
                conn, _ = srv.accept()
            except socket.timeout:
                continue
            with conn:
                conn.settimeout(0.5)
                try:
                    data = conn.recv(4096).decode()
                except OSError:
                    data = ""
                reply = behavior(received, data)
                received.append(data)
                if reply:
                    try:
                        conn.sendall(reply.encode())
                    except OSError:
                        pass
    t = threading.Thread(target=loop, daemon=True)
    t.start()
    def stop():
        stopped["v"] = True
        srv.close()
    return port, received, stop


def _run_launcher(code):
    import subprocess, sys
    return subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                          timeout=60)


def test_deaf_console_gets_no_payload_then_one_acked_send():
    # The console accepts connects but stays SILENT for the first 2 probes (booting):
    # NO payload may be sent. Once it replies (without the desired state), exactly ONE
    # payload lands and the ACK stops the repeats.
    state = {"n": 0}
    def behavior(received, data):
        if data.startswith("--info"):
            state["n"] += 1
            if state["n"] <= 2:
                return ""                                        # booting: deaf
            return "Call:N0CALL Short:N0C set\n"                 # alive, wrong call
        if data.startswith("--setcall"):
            return "Call:OE1ABC Short:OE1 set\n"                 # ACK
        return ""
    port, received, stop = _serve(behavior)
    try:
        code = _render([{"kind": "tcp_send", "port": port,
                         "data": "--setcall {param:mc_callsign}\n",
                         "probe": "--info\n",
                         "probe_stop_on": "Call:{param:mc_callsign}",
                         "stop_on": "Call:{param:mc_callsign}",
                         "repeat": 10, "interval": 0.1}])
        r = _run_launcher(code)
        payloads = [d for d in received if d.startswith("--setcall")]
        assert len(payloads) == 1, received                      # exactly ONE send, ever
        assert "console not ready" in r.stderr
        assert "acknowledged on attempt" in r.stderr
    finally:
        stop()


def test_probe_match_skips_all_sends():
    # NVS-persisted setting already present: the probe matches -> ZERO payload sends.
    def behavior(received, data):
        if data.startswith("--info"):
            return "Call:OE1ABC Short:OE1 set\n"                 # already ours
        return "Call:OE1ABC Short:OE1 set\n"
    port, received, stop = _serve(behavior)
    try:
        code = _render([{"kind": "tcp_send", "port": port,
                         "data": "--setcall {param:mc_callsign}\n",
                         "probe": "--info\n",
                         "probe_stop_on": "Call:{param:mc_callsign}",
                         "stop_on": "Call:{param:mc_callsign}",
                         "repeat": 5, "interval": 0.1}])
        r = _run_launcher(code)
        assert not [d for d in received if d.startswith("--setcall")]
        assert "probe matched" in r.stderr
    finally:
        stop()
