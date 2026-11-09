"""W4 (DEC-W43): `lhpc hardware detect <rid>` runs Detect on one radio of the hardware list and prints its line; rc 0
when the chip was found, 1 when not found or refused, 2 on a usage error. Detect itself is radio_detect.run's
(tests/core/test_dec_w43_detect_run.py); here the CLI drives it with the daemon check real over a faked /proc
listing and the SPI bus a fake behind run.spi_bus."""
from __future__ import annotations

import pytest
import radios_fixture

from lhpc.adapters.cli.main import main
from lhpc.core import radio_daemon, radio_start, radios
from lhpc.core.radio_detect import run

ROWS = (radios.Radio("r1", "low", "loraham", "433"),)


class _Sx127x:
    """An SX127x on a fake bus answering `version` at register 0x42."""

    def __init__(self, version):
        self.version, self.closed, self.t = version, False, 0.0

    def reset(self, level):
        pass

    def busy(self):
        return False

    def transfer(self, out):
        return bytes([0, self.version])

    def sleep(self, s):
        self.t += s

    def now(self):
        return self.t

    def close(self):
        self.closed = True


@pytest.fixture
def box(tmp_path, monkeypatch, capsys):
    """`box(version=0x12, daemon=False)` → run the CLI: (rc, output, the buses opened)."""
    (tmp_path / "config" / "stacks").mkdir(parents=True)
    (tmp_path / "state").mkdir()
    radios_fixture.write(tmp_path, ROWS)
    monkeypatch.setenv("LHPC_RUNTIME_ROOT", str(tmp_path))

    def make(*, version=0x12, daemon=False):
        opened = []
        procs = [radio_daemon.DaemonProc(4242, "id", rid="r1", band="433")] if daemon else []
        monkeypatch.setattr(radio_start, "_daemon_procs", lambda svc: list(procs))
        # the CONF-socket read too: a lab daemon on this host's /tmp/loraconf-r1.sock must not decide the case
        from types import SimpleNamespace

        from lhpc.core import daemon_control
        monkeypatch.setattr(daemon_control, "read_view", lambda system, rid: SimpleNamespace(reachable=False))
        monkeypatch.setattr(run, "spi_bus", lambda pins, chip, lock: opened.append(chip) or _Sx127x(version))

        def cli(*argv):
            rc = main(["hardware", *argv])
            return rc, capsys.readouterr().out, opened
        return cli
    return make


def test_a_found_chip_prints_its_line_and_exits_0(box):
    rc, out, opened = box(version=0x12)("detect", "r1")
    assert (rc, out, opened) == (0, "OK    r1: SX127x found (version 0x12)\n", ["sx127x"])


def test_no_answer_exits_1(box):
    rc, out, _opened = box(version=0x22)("detect", "r1")
    assert (rc, out) == (1, "ERR   r1: no SX127x answered\n")


def test_a_running_daemon_is_refused_and_nothing_opens(box):
    # The red-before through the CLI: with radio_start.daemon_running forced False this test goes red.
    rc, out, opened = box(daemon=True)("detect", "r1")
    assert (rc, out, opened) == (1, "ERR   Detect refused: r1's daemon is running. Stop it first.\n", [])


def test_a_radio_not_in_the_list_is_refused(box):
    rc, out, opened = box()("detect", "r9")
    assert rc == 1 and out.partition(" ")[0] == "ERR" and "r9" in out and opened == []


@pytest.mark.parametrize("argv", [("detect",), ("detect", "r1", "--confirm-phrase", "remove-r1"),
                                  ("detect", "r1", "--high-power", "433", "on")])
def test_a_usage_error_exits_2_and_detects_nothing(box, argv):
    rc, out, opened = box()(*argv)
    assert (rc, opened) == (2, []) and "lhpc hardware detect <rid>" in out
