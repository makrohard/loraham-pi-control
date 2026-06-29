"""Workstream G — daemon CONF/status protocol bounds: oversized, over-long, and
over-tokenized responses are rejected fail-closed (never parsed)."""

import pytest

from lhpc.core import daemon_control as dc
from lhpc.core.probes.backends import FakeSystem


def _view(reply: bytes):
    sys = FakeSystem(unix_replies={"/tmp/loraconf433.sock": reply}).system
    return dc.read_view(sys, "433")


def test_normal_status_parses():
    v = _view(b"STATUS RADIO=READY TXMODE=MANAGED\n")
    assert v.reachable and v.status.get("TXMODE") == "MANAGED"


def test_oversized_response_rejected():
    big = b"STATUS " + b"X=1 " * 2000 + b"\n"        # exceeds _MAX
    v = _view(big)
    assert not v.reachable                            # fail-closed, not parsed


def test_overlong_first_line_rejected():
    line = b"STATUS " + b"A" * (dc._MAX_LINE + 10) + b"\n"
    v = _view(line)
    assert not v.reachable


def test_too_many_tokens_rejected():
    toks = b" ".join(b"K%d=1" % i for i in range(dc._MAX_TOKENS + 5))
    v = _view(b"STATUS " + toks + b"\n")
    assert not v.reachable


def test_malformed_prefix_rejected():
    v = _view(b"GARBAGE not a status line\n")
    assert not v.reachable


# --- §13: --live fully removed (not deprecated) ------------------------------

def test_live_flag_removed_from_cli():
    import inspect
    from lhpc.adapters.cli import main
    assert "--live" not in inspect.getsource(main)    # flag gone from the CLI


def test_service_test_has_no_live_param():
    import inspect
    from lhpc.core.services import ControllerService
    assert "live" not in inspect.signature(ControllerService.test).parameters


def test_live_flag_rejected_as_unknown_option():
    from lhpc.adapters.cli import main
    parser = main.build_parser() if hasattr(main, "build_parser") else None
    if parser is None:
        return
    with pytest.raises(SystemExit):                   # argparse rejects unknown --live
        parser.parse_args(["test", "daemon", "--live"])


# --- §5.1 normal build pkg-config fail-closed --------------------------------

def test_build_step_argv_pkgconfig_failure_fails_closed(tmp_path):
    from lhpc.core import commands
    # FakeSystem.runner.run returns rc 127 (not_found) for any argv -> pkg-config "fails".
    runner = FakeSystem().system.runner
    with pytest.raises(commands.CommandError):
        commands.build_step_argv({"argv": ["gcc", "{pkgconfig:gtk+-3.0}"]},
                                 runner, str(tmp_path), str(tmp_path))


def test_build_step_argv_invalid_pkg_name_rejected(tmp_path):
    from lhpc.core import commands
    runner = FakeSystem().system.runner
    with pytest.raises(commands.CommandError):
        commands.build_step_argv({"argv": ["gcc", "{pkgconfig:bad name;rm}"]},
                                 runner, str(tmp_path), str(tmp_path))


# --- §5.2 apply_set readback uses the bounded parser -------------------------

class _StoreThenOversize:
    """SET stores the value; the GET read-back returns an OVERSIZED line so the
    bounded parser rejects it -> apply_set must NOT confirm."""
    def request(self, path, payload, timeout, max_bytes):
        return b"STATUS " + b"X=1 " * 5000 + b"\n"
    def send(self, path, payload, timeout):
        pass


def test_apply_set_oversized_readback_not_confirmed(tmp_path):
    sys = FakeSystem().system
    sys.unix = _StoreThenOversize()
    ok, confirmed, detail = dc.apply_set(sys, "433", "TXMODE", "DIRECT")
    assert not ok and not confirmed and "did not report" in detail.lower()


# --- P0.4 TX-test STATS uses the ONE bounded parser --------------------------

def _life_stats(tmp_path, reply: bytes):
    from lhpc.core.lifecycle import Lifecycle
    from lhpc.core.config import Config, OperatorConfig
    from lhpc.core.paths import Paths
    sys = FakeSystem(unix_replies={"/tmp/loraconf433.sock": reply}).system
    return Lifecycle(Paths(runtime_root=tmp_path), (), Config(operator=OperatorConfig()), sys)


def test_stats_txok_parses_valid(tmp_path):
    life = _life_stats(tmp_path, b"STATS TXOK=42 RXOK=7\n")
    assert life._stats_txok("433") == 42


def test_stats_txok_oversized_fails_closed(tmp_path):
    life = _life_stats(tmp_path, b"STATS " + b"X=1 " * 5000 + b"\n")   # > _MAX
    assert life._stats_txok("433") is None


def test_stats_txok_malformed_fails_closed(tmp_path):
    life = _life_stats(tmp_path, b"GARBAGE not stats\n")
    assert life._stats_txok("433") is None


# --- strict CONF parser grammar ----------------------------------------------

def test_prefix_must_match_exactly():
    assert not _view(b"STATUSX RADIO=READY TXMODE=MANAGED\n").reachable   # STATUSX != STATUS


def test_bare_token_among_valid_rejects_whole_response():
    assert not _view(b"STATUS RADIO=READY GARBAGE TXMODE=MANAGED\n").reachable


def test_empty_key_rejected():
    assert not _view(b"STATUS =READY TXMODE=MANAGED\n").reachable


def test_empty_value_rejected():
    assert not _view(b"STATUS RADIO= TXMODE=MANAGED\n").reachable


def test_duplicate_key_rejected():
    assert not _view(b"STATUS RADIO=READY RADIO=BUSY\n").reachable


def test_control_char_value_rejected():
    assert not _view(b"STATUS RADIO=RE\x01DY TXMODE=MANAGED\n").reachable
