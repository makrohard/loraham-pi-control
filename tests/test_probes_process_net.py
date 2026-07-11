"""Tests for process-identity and TCP listening probes."""

from __future__ import annotations

from lhpc.core.model import ProcessSpec
from lhpc.core.probes.backends import FakeSystem, parse_proc_net_tcp
from lhpc.core.probes.process import matches, probe_process


# --- process identity (argv from NUL-separated /proc/<pid>/cmdline) --------

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


# --- TCP listeners (IPv4 + IPv6) -------------------------------------------

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
