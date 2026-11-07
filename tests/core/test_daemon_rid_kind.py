"""Package item 24: the daemon start's shared-band rewrite keys on the refusal's kind, not on its candidate count.

`radio_start.daemon_rid` turns "two or more radios can run it … name one" into the hardware-list remedy
(radio_daemon.SHARED_BAND_TEXT). It checked `len(candidates) > 1`, so any other refusal that carries the band's
radios as candidates was reworded too: "rN: cannot tell whether the stack can run on it" names the whole band. One
case per kind the daemon's band resolution gives."""
import types

import radios_fixture

from lhpc.core import radio_daemon, radio_models, radio_start, radios
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService

TWO_868 = (radios.Radio("r1", "a", "uputronics", "868", ce="ce0"), radios.Radio("r2", "b", "uputronics", "868", ce="ce1"))


def _svc(tmp_path, rows):
    radios_fixture.write(tmp_path, rows)
    return ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))


def test_several_radios_on_the_band_get_the_hardware_list_remedy(tmp_path):
    t = radio_start.daemon_rid(_svc(tmp_path, TWO_868), "868")
    assert (t.outcome, t.candidates, t.next_command) == ("refuse", ("r1", "r2"), "config/radios.toml")
    assert t.reason == f"2 radios can run it on 868 MHz: r1 (a), r2 (b); {radio_daemon.SHARED_BAND_TEXT}"


def test_a_radio_that_cannot_be_judged_keeps_its_own_reason(tmp_path, monkeypatch):
    # Its model is missing from the table the band resolution reads (radio_start's reference only): the refusal
    # names r1 and carries the band's two radios, and it is not a shared-band case.
    svc = _svc(tmp_path, TWO_868)
    real = radio_models.load()
    monkeypatch.setattr(radio_start, "radio_models",
                        types.SimpleNamespace(load=lambda: {k: v for k, v in real.items() if k != "uputronics"}))
    t = radio_start.daemon_rid(svc, "868")
    assert (t.outcome, t.candidates) == ("refuse", ("r1", "r2"))
    assert (t.reason, t.next_command) == ("r1: cannot tell whether the stack can run on it", "")


def test_no_radio_on_the_band_keeps_its_reason(tmp_path):
    t = radio_start.daemon_rid(_svc(tmp_path, TWO_868), "433")
    assert (t.outcome, t.reason, t.next_command) == ("refuse", "no enabled radio on 433 MHz", "lhpc hardware")


def test_one_radio_on_the_band_proceeds(tmp_path):
    t = radio_start.daemon_rid(_svc(tmp_path, radios_fixture.RADIOS), "868")
    assert t.outcome == "proceed" and t.rid


def test_only_the_several_radios_refusal_carries_its_kind(tmp_path):
    svc = _svc(tmp_path, TWO_868)
    assert radio_start.band_rid(svc, "868").kind == radio_daemon.SEVERAL
    assert radio_start.band_rid(svc, "433").kind == ""


def test_the_band_less_path_keeps_the_several_kind(tmp_path):
    # Ledger L20: resolve_target without a band (a stack's start with no saved band) adds its notes to the refusal
    # and rebuilt the Target without `kind`, so a several-radios refusal there lost it.
    svc = _svc(tmp_path, TWO_868)
    t = radio_daemon.resolve_target(radios.load(svc._paths), radio_models.load(), lambda _r, _m: True)
    assert (t.outcome, t.kind, t.candidates) == ("refuse", radio_daemon.SEVERAL, ("r1", "r2")), t
