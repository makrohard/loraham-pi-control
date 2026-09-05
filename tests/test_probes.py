from __future__ import annotations
import pytest
from lhpc.core.model import ProcessSpec, SystemdScope, SourceSpec, SourceState
from lhpc.core.probes.backends import FakeSystem, parse_proc_net_tcp, CommandResult
from lhpc.core.probes.process import matches, probe_process
from lhpc.core.probes.unixsock import probe_daemon_status, probe_socket
from lhpc.core.probes.systemd import UnitState, probe_unit
from lhpc.core.probes.source import probe_source


# ===== merged from test_probes.py =====
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


# ===== merged from test_probes_process_net.py =====
def test_process_match_python_script():
    spec = ProcessSpec(exec_name="python3", all_args=("gps-relay.py",))
    argv = ["python3", "scripts/gps-relay.py", "--mode", "fixture"]
    assert matches(spec, argv)


def test_process_match_requires_exec_basename():
    spec = ProcessSpec(exec_name="loraham_daemon", any_args=("433", "both"))
    assert matches(spec, ["/usr/local/bin/loraham_daemon", "--radio", "433"])
    assert not matches(spec, ["loraham_daemon_helper", "--radio", "433"])


def test_process_any_args_band():
    spec = ProcessSpec(exec_name="loraham_daemon", any_args=("868", "both"))
    assert matches(spec, ["loraham_daemon", "--radio", "868"])
    assert matches(spec, ["loraham_daemon", "--radio", "both"])
    assert not matches(spec, ["loraham_daemon", "--radio", "433"])


def test_process_match_is_token_scoped_not_whole_line():
    # A pattern must live inside a single token; it cannot span two arguments.
    spec = ProcessSpec(exec_name="python3", all_args=("python3 scripts",))
    assert not matches(spec, ["python3", "scripts/meshcore.py"])


def test_probe_process_collects_pids():
    fake = FakeSystem(cmdlines_data={
        10: ["loraham_daemon", "--radio", "433"],
        11: ["python3", "meshcore.py"],
    })
    spec = ProcessSpec(exec_name="loraham_daemon", any_args=("433",))
    pm = probe_process(fake.system, spec)
    assert pm.matched and pm.pids == [10]


def test_empty_argv_never_matches():
    assert not matches(ProcessSpec(exec_name="x"), [])


_TCP4 = (
    "  sl  local_address rem_address   st ...\n"
    "   0: 0100007F:1F40 00000000:0000 0A 0 0 0 0 0 12345 1 0\n"   # 127.0.0.1:8000 LISTEN
    "   1: 0100007F:1F41 0100007F:1234 01 0 0 0 0 0 99 1 0\n"      # ESTABLISHED (ignored)
)


_TCP6 = (
    "  sl  local_address rem_address   st ...\n"
    "   0: 00000000000000000000000001000000:22B8 00000000000000000000000000000000:0000 0A 0 0 0 0 0 6789 1 0\n"
)


def test_parse_ipv4_listen_only():
    lst = parse_proc_net_tcp(_TCP4, "ipv4")
    assert [(x.port, x.inode) for x in lst] == [(8000, 12345)]


def test_parse_ipv6():
    lst = parse_proc_net_tcp(_TCP6, "ipv6")
    assert lst and lst[0].port == 0x22B8 and lst[0].family == "ipv6"


# ===== merged from test_probes_unixsock.py =====
_VALID = (
    b"STATUS RADIO=READY TX=0 CAD=0 GETRSSI=0 TXRESULT=0 TXMODE=DIRECT TXQUEUE=1 "
    b"CADWAIT=1500 CADIDLE=250 CADPOLL=50 CADTXAFTERTIMEOUT=0 CADMONITOR=0 CADRSSI=-90\n"
)


def test_socket_existence_and_type():
    fake = FakeSystem(sockets={"/tmp/loraconf433.sock"}, paths={"/tmp/plain"})
    assert probe_socket(fake.system, "/tmp/loraconf433.sock").is_socket
    # exists but is not a socket
    p = probe_socket(fake.system, "/tmp/plain")
    assert p.exists and not p.is_socket
    # absent
    assert not probe_socket(fake.system, "/tmp/none").exists


def test_daemon_status_valid_ready():
    fake = FakeSystem(unix_replies={"/tmp/loraconf433.sock": _VALID})
    ds = probe_daemon_status(fake.system, "/tmp/loraconf433.sock")
    assert ds.reachable and ds.ready
    assert ds.radio == "READY" and ds.tx_mode == "DIRECT"
    assert ds.fields["CADWAIT"] == "1500"


def test_daemon_status_radio_failed_is_not_ready():
    reply = b"STATUS RADIO=FAILED TX=0 TXMODE=MANAGED\n"
    fake = FakeSystem(unix_replies={"/tmp/loraconf868.sock": reply})
    ds = probe_daemon_status(fake.system, "/tmp/loraconf868.sock")
    assert ds.reachable and not ds.ready and ds.radio == "FAILED"


def test_daemon_status_connection_failure():
    fake = FakeSystem(unix_errors={"/tmp/loraconf433.sock": "timed out"})
    ds = probe_daemon_status(fake.system, "/tmp/loraconf433.sock")
    assert not ds.reachable and not ds.ready
    assert "timed out" in ds.evidence["error"]


def test_daemon_status_malformed_response():
    fake = FakeSystem(unix_replies={"/tmp/loraconf433.sock": b"garbage not a status\n"})
    ds = probe_daemon_status(fake.system, "/tmp/loraconf433.sock")
    assert not ds.reachable and not ds.ready
    assert "malformed" in ds.evidence["error"]


def test_daemon_status_empty_response():
    fake = FakeSystem(unix_replies={"/tmp/loraconf433.sock": b""})
    ds = probe_daemon_status(fake.system, "/tmp/loraconf433.sock")
    assert not ds.reachable and "empty" in ds.evidence["error"]


def test_daemon_status_oversize_is_bounded_and_parsed_or_safe():
    # An oversize first line (no newline within the cap) must not hang or crash;
    # it is read up to the cap and parsed defensively.
    huge = b"STATUS RADIO=READY " + b"PAD=x " * 5000 + b"\n"
    fake = FakeSystem(unix_replies={"/tmp/loraconf433.sock": huge})
    ds = probe_daemon_status(fake.system, "/tmp/loraconf433.sock")
    # We only require: no exception, reachable parse, and RADIO captured.
    assert ds.reachable and ds.radio == "READY"


# ===== merged from test_probes_systemd.py =====
_PROPS = "ActiveState,SubState,LoadState,UnitFileState"


def _argv(unit: str, user: bool = False) -> tuple[str, ...]:
    base = ["systemctl"]
    if user:
        base.append("--user")
    base += ["show", unit, "--property", _PROPS]
    return tuple(base)


def _show(active: str, load: str = "loaded", enabled: str = "enabled") -> str:
    return f"ActiveState={active}\nSubState=x\nLoadState={load}\nUnitFileState={enabled}\n"


def test_active_system_unit():
    fake = FakeSystem(commands={_argv("d@433.service"): CommandResult(0, _show("active"), "")})
    p = probe_unit(fake.system, "d@433.service", SystemdScope.SYSTEM)
    assert p.state is UnitState.ACTIVE
    assert p.enabled == "enabled"


@pytest.mark.parametrize("unit,user,result,expected", [
    pytest.param("d.service", False, CommandResult(0, _show("failed"), ""), UnitState.FAILED,
                 id="test_failed_unit"),
    pytest.param("d.service", False, CommandResult(0, _show("inactive"), ""), UnitState.INACTIVE,
                 id="test_inactive_unit"),
    pytest.param("x.service", False, CommandResult(0, _show("inactive", load="not-found"), ""),
                 UnitState.NOT_FOUND, id="test_not_found_unit"),
    pytest.param("hub.service", True, CommandResult(1, "", "Failed to connect to bus: no medium"),
                 UnitState.UNAVAILABLE, id="test_user_scope_no_bus_is_unavailable"),
    pytest.param("d.service", False, CommandResult(124, "", "", timed_out=True), UnitState.TIMEOUT,
                 id="test_timeout_is_timeout"),
    pytest.param("d.service", False, CommandResult(127, "", "", not_found=True), UnitState.UNAVAILABLE,
                 id="test_systemctl_missing_is_unavailable"),
])
def test_probe_unit_status(unit, user, result, expected):
    scope = SystemdScope.USER if user else SystemdScope.SYSTEM
    fake = FakeSystem(commands={_argv(unit, user=user): result})
    assert probe_unit(fake.system, unit, scope).state is expected


# ===== merged from test_probes_source.py =====
_PIN = "a" * 40


_OTHER = "b" * 40


_ABS = "/runtime/src/comp"


def _fake(head: str, porcelain: str = "", *, repo: bool = True) -> FakeSystem:
    paths = {_ABS}
    if repo:
        paths.add(f"{_ABS}/.git")
    # `porcelain` is a v1-style " M file" line list; rendered as porcelain=v2 entry lines.
    entries = "".join(f"1 .M N... 100644 100644 100644 {head} {head} {ln.split()[-1]}\n"
                      for ln in porcelain.splitlines() if ln.strip())
    return FakeSystem(
        paths=paths,
        commands={
            ("git", "-C", _ABS, "status", "--porcelain=v2", "--branch", "--untracked-files=no"):
                CommandResult(0, f"# branch.oid {head}\n# branch.head main\n" + entries, ""),
        },
    )


def test_source_missing():
    fake = FakeSystem()  # path not present
    p = probe_source(fake.system, SourceSpec(path="src/comp", pin_commit=_PIN), _ABS)
    assert p.state is SourceState.MISSING


def test_source_not_a_repo():
    fake = _fake(_PIN, repo=False)
    p = probe_source(fake.system, SourceSpec(path="src/comp", pin_commit=_PIN), _ABS)
    assert p.state is SourceState.NOT_A_REPO


def test_source_pin_match():
    fake = _fake(_PIN)
    p = probe_source(fake.system, SourceSpec(path="src/comp", pin_commit=_PIN), _ABS)
    assert p.state is SourceState.MATCH and p.head == _PIN


def test_source_pin_differs():
    fake = _fake(_OTHER)
    p = probe_source(fake.system, SourceSpec(path="src/comp", pin_commit=_PIN), _ABS)
    assert p.state is SourceState.DIFFERS


def test_source_dirty_overrides_pin():
    fake = _fake(_PIN, porcelain=" M file.c\n")
    p = probe_source(fake.system, SourceSpec(path="src/comp", pin_commit=_PIN), _ABS)
    assert p.state is SourceState.DIRTY


def test_source_unknown_on_git_error():
    fake = FakeSystem(
        paths={_ABS, f"{_ABS}/.git"},
        commands={("git", "-C", _ABS, "status", "--porcelain=v2", "--branch",
                   "--untracked-files=no"): CommandResult(128, "", "fatal")},
    )
    p = probe_source(fake.system, SourceSpec(path="src/comp", pin_commit=_PIN), _ABS)
    assert p.state is SourceState.UNKNOWN


def test_source_probe_is_two_subprocesses_and_parses_status_v2():
    """0.2.9: ONE `git status --porcelain=v2 --branch` (HEAD + tracked dirtiness) plus
    `git describe` — never a third `rev-parse`. Detached HEAD keeps its oid; an unborn branch
    or a timeout is UNKNOWN, never a guessed clean."""
    from lhpc.core.probes.source import parse_status_v2
    assert parse_status_v2(f"# branch.oid {_PIN}\n# branch.head main\n") == (_PIN, False)
    assert parse_status_v2(f"# branch.oid {_PIN}\n# branch.head (detached)\n") == (_PIN, False)
    assert parse_status_v2("# branch.oid (initial)\n# branch.head main\n") == ("", False)
    assert parse_status_v2(f"# branch.oid {_PIN}\n1 .M N... 100644 100644 100644 a b f.c\n") == (_PIN, True)
    assert parse_status_v2(f"# branch.oid {_PIN}\n2 R. N... 100644 100644 100644 a b R100 n\to\n")[1]
    assert parse_status_v2(f"# branch.oid {_PIN}\nu UU N... 100644 100644 100644 100644 a b c f\n")[1]
    fake = _fake(_PIN)
    p = probe_source(fake.system, SourceSpec(path="src/comp", pin_commit=_PIN), _ABS)
    assert p.state is SourceState.MATCH
    gits = [c for c in fake.system.runner.calls if c[:1] == ["git"]]
    assert len(gits) == 2 and gits[0][3] == "status" and gits[1][3] == "describe"
    assert not any("rev-parse" in c for c in gits)
    # unborn branch -> UNKNOWN, with no head
    fake2 = FakeSystem(paths={_ABS, f"{_ABS}/.git"}, commands={
        ("git", "-C", _ABS, "status", "--porcelain=v2", "--branch", "--untracked-files=no"):
            CommandResult(0, "# branch.oid (initial)\n", "")})
    p2 = probe_source(fake2.system, SourceSpec(path="src/comp", pin_commit=_PIN), _ABS)
    assert p2.state is SourceState.UNKNOWN and p2.head == ""
    # timeout -> UNKNOWN
    fake3 = FakeSystem(paths={_ABS, f"{_ABS}/.git"}, commands={
        ("git", "-C", _ABS, "status", "--porcelain=v2", "--branch", "--untracked-files=no"):
            CommandResult(0, "", "", timed_out=True)})
    assert probe_source(fake3.system, SourceSpec(path="src/comp", pin_commit=_PIN),
                        _ABS).state is SourceState.UNKNOWN
