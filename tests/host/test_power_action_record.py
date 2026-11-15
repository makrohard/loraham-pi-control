"""DEC-W11: a console power action leaves one LHPC record line, written before the trigger spawns.

Driven through the console: POST /power/<kind> confirmed, with the headers nginx sets (X-LHPC-Client-Verify,
X-LHPC-Client-DN). The trigger spawn and logind's answer are faked at their seams, as in test_power_controls.
"""
from __future__ import annotations

import re

import pytest

from lhpc.core import lifecycle as lcmod
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService

LINE = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ power (reboot|poweroff) requested by client (\S+)")


def _armed(web, tmp_path, monkeypatch, verdict='s "yes"\n'):
    """A console whose power actions are supported, logind answering `verdict`, the spawn recorded together with
    whether the record existed at that moment."""
    (tmp_path / "config" / "stacks").mkdir(parents=True, exist_ok=True)
    svc = ControllerService(system=FakeSystem(files={"/proc/uptime": "100.00 380.00\n"}).system,
                            paths=Paths(runtime_root=tmp_path))
    monkeypatch.setattr(ControllerService, "power_supported", lambda self, kind=None: True)
    monkeypatch.setattr(lcmod, "current_boot_id", lambda: "boot-1")
    orig = svc._system.runner.run

    def run(argv, timeout=None):
        if argv and argv[0] == "busctl":
            class R:
                returncode, stdout, stderr = 0, verdict, ""
            return R()
        return orig(argv, timeout)
    monkeypatch.setattr(svc._system.runner, "run", run)
    spawned = []

    def spy(self, argv, log_path, cwd=None, env=None):
        spawned.append((tmp_path / "logs" / "power-actions.log").is_file())
        return 4242
    monkeypatch.setattr(lcmod.Lifecycle, "_real_spawn", spy)
    return web(service_factory=lambda: svc), spawned


def _apply(client, csrf, kind, headers):
    r = client.post(f"/power/{kind}", data={"_csrf": csrf(client, "/"), "confirmed": "yes"}, headers=headers)
    assert r.status_code == 302, r.status_code


def _records(tmp_path):
    p = tmp_path / "logs" / "power-actions.log"
    return p.read_text().splitlines() if p.is_file() else []


def test_r1_a_verified_clients_poweroff_is_recorded_with_its_cn_before_the_trigger(web, csrf, tmp_path, monkeypatch):
    """RED before: no record at all."""
    client, spawned = _armed(web, tmp_path, monkeypatch)
    _apply(client, csrf, "poweroff", {"X-LHPC-Client-Verify": "SUCCESS",
                                      "X-LHPC-Client-DN": "CN=phone-dl1abc,O=LoRaHAM Pi Control"})
    recs = _records(tmp_path)
    assert len(recs) == 1 and LINE.fullmatch(recs[0]).groups() == ("poweroff", "phone-dl1abc")
    assert spawned == [True]                                   # on disk when the trigger spawned


@pytest.mark.parametrize("headers", [{}, {"X-LHPC-Client-Verify": "FAILED", "X-LHPC-Client-DN": "CN=intruder"},
                                     {"X-LHPC-Client-Verify": "NONE", "X-LHPC-Client-DN": "CN=x"},
                                     {"X-LHPC-Client-DN": "CN=no-verify-header"}],
                         ids=["no-headers", "verify-failed", "verify-none", "dn-without-verify"])
def test_r2_without_a_verified_certificate_the_client_reads_dash(web, csrf, tmp_path, monkeypatch, headers):
    """RED before: no record. A DN nginx did not verify is never named."""
    client, spawned = _armed(web, tmp_path, monkeypatch)
    _apply(client, csrf, "reboot", headers)
    recs = _records(tmp_path)
    assert len(recs) == 1 and LINE.fullmatch(recs[0]).groups() == ("reboot", "-")
    assert spawned == [True]


def test_r3_two_actions_append_two_lines(web, csrf, tmp_path, monkeypatch):
    """RED before. The second click is refused while the first is pending, so the marker is cleared between them."""
    client, spawned = _armed(web, tmp_path, monkeypatch)
    _apply(client, csrf, "reboot", {})
    (tmp_path / "state" / "power-pending.json").unlink()     # the first one's pending marker (the trigger is faked)
    _apply(client, csrf, "poweroff", {})
    assert [LINE.fullmatch(r).group(1) for r in _records(tmp_path)] == ["reboot", "poweroff"]


def test_r4_control_a_refused_action_requests_nothing_and_records_nothing(web, csrf, tmp_path, monkeypatch):
    client, spawned = _armed(web, tmp_path, monkeypatch, verdict='s "no"\n')
    _apply(client, csrf, "poweroff", {"X-LHPC-Client-Verify": "SUCCESS", "X-LHPC-Client-DN": "CN=phone"})
    assert _records(tmp_path) == [] and spawned == []


def test_r5_a_record_that_cannot_be_written_never_blocks_the_action(web, csrf, tmp_path, monkeypatch):
    """RED before (no record, so nothing says it is missing). A directory where the record goes: the trigger still
    spawns and the result names the missing record."""
    client, spawned = _armed(web, tmp_path, monkeypatch)
    (tmp_path / "logs" / "power-actions.log").mkdir(parents=True)
    r = client.post("/power/poweroff", data={"_csrf": csrf(client, "/"), "confirmed": "yes"}, follow_redirects=True)
    assert spawned == [False]
    assert "power record not written" in r.get_data(as_text=True)


@pytest.mark.parametrize("dn,name", [("CN=phone-dl1abc,O=LHPC", "phone-dl1abc"), ("/O=LHPC/CN=old-form", "old-form"),
                                     ("O=LHPC, CN=spaced", "spaced"), ("O=LHPC", "-"), ("", "-"), (None, "-"),
                                     (7, "-"), ("CN=a;rm -rf /,O=x", "a?rm -rf"), ("CN=" + "x" * 99, "x" * 64)])
def test_r6_the_client_name_is_the_cn_and_safe_for_one_line(dn, name):
    from lhpc.core import power_record
    assert power_record.client_name(dn) == name
