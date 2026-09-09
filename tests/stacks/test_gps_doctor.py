"""`lhpc doctor`'s GPS checks: what it says about a configured gpsd, and the promises the
summary line makes about it.

Every one of these was a real mis-diagnosis on hardware. A gpsd that answers but owns no
receiver looks identical to a healthy one from the outside; a repair command printed bare
tells a remote operator to fix the wrong machine; and a bounded check that is only bounded
per read is not bounded at all.
"""
from __future__ import annotations

import pytest


def _svc(tmp_path):
    """A service on a FAKE system — doctor must be judged on the test's own configuration,
    never on whatever the developer machine happens to be running."""
    from lhpc.core.paths import Paths
    from lhpc.core.probes.backends import FakeSystem
    from lhpc.core.services import ControllerService
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    return ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))


def _stub_gpsd(monkeypatch, devices=(), err=""):
    """Answer the ?DEVICES query in-process.

    These tests are about doctor's WORDING, not about reaching a daemon — and reaching one is
    exactly the machine dependency this commit removes elsewhere. Left unstubbed they contacted
    a real LAN address and a real loopback port, so their result depended on what the host and
    the network happened to be doing.
    """
    from lhpc.core import services as _services
    monkeypatch.setattr(_services, "gpsd_devices", lambda h, p, timeout=3.0: (list(devices), err),
                        raising=False)
    from lhpc.core import gps as _gps
    monkeypatch.setattr(_gps, "gpsd_devices", lambda h, p, timeout=3.0: (list(devices), err))


def test_doctor_names_a_gpsd_that_owns_no_receiver(tmp_path, fake_gpsd):
    """gpsd ANSWERING is not gpsd HAVING A RECEIVER. Debian defaults to `DEVICES=""` + `USBAUTO`,
    so gpsd depends on a udev hotplug: restart it while the receiver is already plugged in and it
    returns owning nothing, accepts connections happily, and streams nothing. Every GPS source
    then yields no position with nothing naming the cause — exactly what happened on hardware,
    mid-matrix, and it took a manual `?DEVICES` query to see it.
    """
    from lhpc.core.config import save_gps
    svc = _svc(tmp_path)
    srv = fake_gpsd(devices=[])
    try:
        save_gps(svc._paths, source="gpsd", host="127.0.0.1", port=str(srv.port))
        svc._invalidate_config()
        text = " ".join(svc.doctor().details)
    finally:
        srv.close()
    assert "owns NO receiver" in text, text
    assert "gpsdctl add" in text, "and it must name the fix"


def test_doctor_is_quiet_when_gpsd_owns_a_receiver(tmp_path, fake_gpsd):
    """The check must not cry wolf on a healthy box."""
    from lhpc.core.config import save_gps
    svc = _svc(tmp_path)
    srv = fake_gpsd(devices=["/dev/ttyACM0"])
    try:
        save_gps(svc._paths, source="gpsd", host="127.0.0.1", port=str(srv.port))
        svc._invalidate_config()
        text = " ".join(svc.doctor().details)
    finally:
        srv.close()
    assert "owns NO receiver" not in text
    assert "is not answering" not in text


def test_doctor_names_an_unreachable_gpsd(tmp_path, monkeypatch):
    """A configured gpsd that is simply not there is the other half of the same question."""
    from lhpc.core.config import save_gps
    svc = _svc(tmp_path)
    save_gps(svc._paths, source="gpsd", host="127.0.0.1", port="65123")
    svc._invalidate_config()
    _stub_gpsd(monkeypatch, err="ConnectionRefusedError: [Errno 111] Connection refused")
    text = " ".join(svc.doctor().details)
    assert "is not answering" in text, text


@pytest.mark.parametrize("source", ["off", "fixed"])
def test_doctor_does_not_probe_gpsd_for_other_sources(tmp_path, source):
    """No pointless network probe — and no warning about a daemon this box does not use."""
    from lhpc.core.config import save_gps
    svc = _svc(tmp_path)
    if source == "fixed":
        save_gps(svc._paths, source="fixed",
                 fixed_lat="51.4779", fixed_lon="-0.0015", fixed_alt="12")
    else:
        save_gps(svc._paths, source="off")
    svc._invalidate_config()
    text = " ".join(svc.doctor().details)
    assert "gpsd at" not in text


# ---- the doctor probe must keep doctor's promises ---------------------------------------------

def test_the_gpsd_query_honours_one_total_deadline():
    """`timeout` was applied per `recv` across up to 40 reads, so a gpsd that keeps talking
    without ever sending DEVICES could hold the caller for 40x the budget. `doctor` promises a
    BOUNDED check, and the start gate uses the same helper."""
    import socket
    import threading
    import time

    from lhpc.core.gps import gpsd_devices

    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    stop = threading.Event()

    def chatter():
        try:
            conn, _ = srv.accept()
        except OSError:
            return
        with conn:
            # Answers steadily but NEVER with a DEVICES message, each reply arriving just
            # inside the per-read timeout: with a per-read budget this runs 40 x 0.4s.
            while not stop.is_set():
                try:
                    conn.sendall(b'{"class":"WATCH"}\n')
                    time.sleep(0.4)
                except OSError:
                    return

    t = threading.Thread(target=chatter, daemon=True)
    t.start()
    try:
        started = time.monotonic()
        devs, err = gpsd_devices("127.0.0.1", port, timeout=0.5)
        waited = time.monotonic() - started
    finally:
        stop.set()
        srv.close()
        t.join(2)

    assert devs == [] and err, "a gpsd that never answers must report an error"
    assert waited < 3.0, (
        f"one TOTAL deadline, not one per read — waited {waited:.1f}s; the per-read form would "
        f"run to ~40 x the budget here")


def test_doctor_sends_a_remote_operator_to_the_right_machine(tmp_path, monkeypatch):
    """`systemctl`/`gpsdctl` act on the machine they run on. Printing them bare for a gpsd on
    ANOTHER box tells the operator to repair the wrong one."""
    from lhpc.core.config import save_gps
    svc = _svc(tmp_path)
    save_gps(svc._paths, source="gpsd", host="192.0.2.42", port="2947")   # TEST-NET-1, never routed
    svc._invalidate_config()
    _stub_gpsd(monkeypatch, devices=[])
    text = " ".join(svc.doctor().details)
    assert "192.0.2.42" in text
    assert "ON 192.0.2.42, not here" in text, text


@pytest.mark.parametrize("host", ["127.0.0.1", "127.0.0.2", "localhost", "::1"])
def test_doctor_does_not_misdirect_for_a_local_gpsd(tmp_path, monkeypatch, host):
    """...and the local case must stay clean, with no confusing 'on another host' note. Every
    loopback FORM counts: a hand-rolled check read 127.0.0.2 and expanded IPv6 as remote, which
    is why this uses the config's own canonical `local_gpsd`."""
    from lhpc.core.config import save_gps
    svc = _svc(tmp_path)
    save_gps(svc._paths, source="gpsd", host=host, port="2947")
    svc._invalidate_config()
    _stub_gpsd(monkeypatch, devices=[])
    text = " ".join(svc.doctor().details)
    assert "not here" not in text, text


def test_doctor_summary_does_not_promise_something_it_breaks(tmp_path):
    """The summary claimed "no network" while the gpsd probe opened a socket. A contract line
    that is not true is worse than no line — it is what an operator quotes back at you."""
    svc = _svc(tmp_path)
    summary = svc.doctor().summary
    assert "no network" not in summary, summary
    assert "gpsd" in summary, "and it must say what the one exception is"
