"""Ledger L27 (DEC-W27): with a hardware list that does not resolve a band to one enabled radio (two of them, none,
or an invalid list), no read, no SET and no raw frame for that band reaches any socket: the 0.13.x band socket
(`loraconf<band>.sock`, `lora<band>.sock`) is never the fall-back there. A SET is refused naming why. Without a
hardware list (the 0.13.x upgrade case) the band socket stays. Every unix request and send is recorded."""
import dataclasses

import pytest
import radios_fixture

from lhpc.core import radio_daemon, radios
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService

TWO_868 = (radios.Radio("r1", "a", "uputronics", "868", ce="ce0"), radios.Radio("r2", "b", "uputronics", "868", ce="ce1"))
ONLY_433 = (radios.Radio("r1", "low", "loraham", "433"),)
SHARED = (f"Cannot SET TXMODE on 868 MHz: the hardware list names no one radio for 868 MHz: 2 radios can run it on "
          f"868 MHz: r1 (a), r2 (b); {radio_daemon.SHARED_BAND_TEXT}")
NONE = "Cannot SET TXMODE on 868 MHz: the hardware list names no one radio for 868 MHz: no enabled radio on 868 MHz"


@dataclasses.dataclass
class _Recording(FakeSystem):
    seen: list = dataclasses.field(default_factory=list)

    def request(self, path, payload, timeout, max_bytes):
        self.seen.append((path, payload))
        return super().request(path, payload, timeout, max_bytes)

    def send(self, path, payload, timeout):
        self.seen.append((path, payload))
        return super().send(path, payload, timeout)


def _svc(tmp_path, rows):
    (tmp_path / "config").mkdir(exist_ok=True)
    if rows is not None:
        radios_fixture.write(tmp_path, rows)
    fs = _Recording(unix_replies={"/tmp/loraconf-r1.sock": b"OK\n", "/tmp/loraconf-r2.sock": b"OK\n"})
    return ControllerService(system=fs.system, paths=Paths(runtime_root=tmp_path)), fs


@pytest.mark.parametrize("rows,why", [(TWO_868, SHARED), (ONLY_433, NONE)], ids=["two-radios", "no-radio"])
@pytest.mark.parametrize("apply", [True, False])
def test_a_set_on_a_band_the_list_does_not_resolve_is_refused_and_sends_nothing(tmp_path, rows, why, apply):
    svc, fs = _svc(tmp_path, rows)
    res = svc.daemon_set("868", "TXMODE", "DIRECT", apply=apply)
    assert (res.ok, res.summary) == (False, why)
    assert fs.seen == []


def test_an_invalid_list_sends_nothing_either(tmp_path):
    svc, fs = _svc(tmp_path, None)
    (tmp_path / "config" / "radios.toml").write_text("not toml [")
    res = svc.daemon_set("868", "TXMODE", "DIRECT", apply=True)
    assert not res.ok and res.summary.split(":")[0] == "Cannot SET TXMODE on 868 MHz"
    assert fs.seen == []


def test_the_reads_and_the_raw_frame_reach_no_socket_either(tmp_path):
    svc, fs = _svc(tmp_path, TWO_868)
    view = svc.daemon_view("868")
    assert (view.reachable, view.error) == (False, "no daemon socket: the hardware list names no one radio for this band")
    res = svc._lifecycle().run_daemon_tx_test("868", "x")
    assert not res.ok and res.detail.split(":")[0] == "refused"
    assert svc._lifecycle().raw_socket("868") == ""                              # no data socket for the band
    assert fs.seen == []


def test_without_a_list_the_band_socket_stays_for_a_013_daemon(tmp_path):
    svc, fs = _svc(tmp_path, None)
    svc.daemon_set("868", "TXMODE", "DIRECT", apply=True)
    assert [p for p, _ in fs.seen][:1] == ["/tmp/loraconf868.sock"]             # the upgrade case, unchanged


def test_one_radio_on_the_band_reaches_its_own_socket(tmp_path):
    svc, fs = _svc(tmp_path, radios_fixture.RADIOS)                                # r2 is the one 868 radio
    svc.daemon_set("868", "TXMODE", "DIRECT", apply=True)
    assert {p for p, _ in fs.seen} == {"/tmp/loraconf-r2.sock"}
