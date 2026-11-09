"""Stage L5, B4 commit 3: claims, locks, conflicts, status and CONF SETs name a radio by its id (W4-4, W4-1 with
B4-D39, W5-2, W6-2/W6-3). The one resolver is radio_start.key_for: a band with exactly one enabled radio is that
radio; no list, none or two radios keep the band (fail-closed: two radios of one band share one key)."""
import pytest
import radios_fixture

from lhpc.core import radio_start, radios
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService
from lhpc.core.status import StatusProber

NO_LIST = None
_ABSENT = pytest.mark.no_default_hardware     # the list is really absent (conftest's baseline list off)
TWO_868 = (radios.Radio("r1", "a", "uputronics", "868", ce="ce0"), radios.Radio("r2", "b", "uputronics", "868", ce="ce1"))
MIXED = (radios.Radio("r1", "low", "loraham", "433"), radios.Radio("r2", "high", "waveshare-sx1262", "868"))
READY = b"STATUS RADIO=READY ID=%s BOUND=- TXMODE=MANAGED\n"


def _svc(tmp_path, rows, **fake):
    if rows is not NO_LIST:
        radios_fixture.write(tmp_path, rows)
    return ControllerService(system=FakeSystem(**fake).system, paths=Paths(runtime_root=tmp_path))


@pytest.mark.parametrize("rows,band,name", [
    (radios_fixture.RADIOS, "433", "r1"), (radios_fixture.RADIOS, "868", "r2"),
    (TWO_868, "868", "868"),                     # two radios of one band share the band key (B4-D39)
    pytest.param(NO_LIST, "433", "433", marks=_ABSENT),
    ((), "433", "433"),                          # S2: an empty list serves no band (868 is K6's per-rid chooser)
    ((radios.Radio("r1", "low", "loraham", "433", enabled=False),), "433", "433"),
])
def test_a_bands_claim_and_lock_keys_name_its_one_radio(tmp_path, rows, band, name):
    svc = _svc(tmp_path, rows)
    keys = svc._operation_resource_keys("kiss", band, "", "start")
    assert f"loraham.radio.{name}" in keys and f"loraham.daemon-socket.{name}" in keys
    assert {k for k in keys if k.rsplit(".", 1)[0] == "loraham.radio"} == {f"loraham.radio.{name}"}   # no other radio
    assert radio_start.claim_key(svc._paths, band) == f"loraham.radio.{name}"


def test_w4_1_two_radios_on_one_band_refuse_the_client_naming_both(tmp_path, monkeypatch):
    # B4-D39: a direct user on r1 and a client starting on 868 — the start is not addressed by radio id yet,
    # so the band's two radios are named and the start is refused, nothing launched.
    from lhpc.core.lifecycle import Lifecycle
    svc = _svc(tmp_path, TWO_868, cmdlines_data={300: ["meshtasticd"]})
    svc._set_running_band("meshtastic", "868")
    (tmp_path / "src" / "loraham-kiss-tnc").mkdir(parents=True)
    monkeypatch.setattr(ControllerService, "hardware_configured", lambda self: True)
    monkeypatch.setattr(ControllerService, "_ensure_daemon", lambda self, *a, **k: ([], True, ""))
    for name in ("is_built", "_source_present"):
        monkeypatch.setattr(ControllerService, name, lambda self, c: True)
    monkeypatch.setattr(ControllerService, "binary_behind", lambda self, c: "")
    launched = []
    monkeypatch.setattr(Lifecycle, "start", lambda self, s, c, *a, **k: launched.append(c.id))
    res = svc.start("kiss", apply=True, band="868")
    assert not res.ok and launched == []
    assert res.summary == "Cannot run 'kiss': meshtastic@<rid> must be stopped first.", res.summary   # shared band: no guess
    plan = svc.start("kiss", band="868")
    assert {b["resource"] for b in plan.data["blockers"]} >= {"loraham.radio.868 (radios r1, r2)"}, plan.data


def test_w4_1_the_shared_band_key_blocks_a_direct_user_beside_the_bands_daemon(tmp_path):
    # Meshtastic (a direct SPI user) on a band of two radios: its claim is the band's shared key, so the running
    # daemon of that band is its blocker — fail-closed, never admitted beside it (B4-D39).
    svc = _svc(tmp_path, TWO_868, cmdlines_data={100: ["loraham_daemon", "--radio", "868", "--id", "r1"]},
               unix_replies={"/tmp/loraconf-r1.sock": READY % b"r1"})
    blockers = svc.run_blockers("meshtastic", "868")
    assert any(b["resource"] == "loraham.radio.868 (radios r1, r2)" for b in blockers), blockers


def test_a_conflict_names_the_radio(tmp_path):
    # W4-2: the observed conflict of the daemon on 868 and meshtastic on 868 names r2, the band's one radio.
    svc = _svc(tmp_path, radios_fixture.RADIOS,
               cmdlines_data={100: ["loraham_daemon", "--radio", "868", "--id", "r2"], 300: ["meshtasticd"]},
               unix_replies={"/tmp/loraconf-r2.sock": READY % b"r2"})
    svc._set_running_band("meshtastic", "868")
    msgs = [c.message for c in svc._observed_conflicts()]
    assert any(m.endswith("on loraham.radio.r2 — both running") for m in msgs), msgs
    assert not any("{radio:" in m or "loraham.radio.868" in m for m in msgs)


def test_status_lists_every_radios_conf_socket(tmp_path):
    # W6-2: the daemon's `{rid}` endpoint is one observation per enabled radio, two radios of a band included.
    radios_fixture.write(tmp_path, TWO_868)
    sys = FakeSystem(unix_replies={"/tmp/loraconf-r1.sock": READY % b"r1"}).system
    prober = StatusProber(sys, Paths(runtime_root=tmp_path))
    comp = next(c for st in ControllerService(system=sys, paths=Paths(runtime_root=tmp_path)).stacks()
                for c in st.components if c.id == "loraham-daemon")
    obs, *_ = prober._assess_endpoints(comp)
    assert [o.spec.address for o in obs] == ["/tmp/loraconf-r1.sock", "/tmp/loraconf-r2.sock"]


@_ABSENT
def test_status_without_a_list_has_no_daemon_socket(tmp_path):
    sys = FakeSystem().system
    comp = next(c for st in ControllerService(system=sys, paths=Paths(runtime_root=tmp_path)).stacks()
                for c in st.components if c.id == "loraham-daemon")
    obs, *_ = StatusProber(sys, Paths(runtime_root=tmp_path))._assess_endpoints(comp)
    assert obs == []


def test_the_overview_lists_each_radio_of_a_band_with_its_own_daemon(tmp_path):
    # W6-3: the 868 entry names both radios, each read on its own socket; r2's daemon is not up.
    svc = _svc(tmp_path, TWO_868, unix_replies={"/tmp/loraconf-r1.sock": READY % b"r1"})
    monkey_bands = {"433", "868"}
    svc.radio_bands = lambda: sorted(monkey_bands)
    entry = next(e for e in svc.radio_overview() if e["band"] == "868")
    assert [(r["rid"], r["reachable"], r["ready"]) for r in entry["radios"]] == [("r1", True, True),
                                                                                    ("r2", False, False)]
    assert next(e for e in svc.radio_overview() if e["band"] == "433")["radios"] == []


def test_the_overview_leaves_out_a_disabled_radio(tmp_path):
    # W6-4: a disabled radio has no entry (its daemon, if one runs, is a stray, not a card).
    rows = (radios.Radio("r1", "a", "uputronics", "868", ce="ce0"),
            radios.Radio("r2", "b", "uputronics", "868", ce="ce1", enabled=False))
    svc = _svc(tmp_path, rows, unix_replies={"/tmp/loraconf-r2.sock": READY % b"r2"})
    svc.radio_bands = lambda: ["868"]
    assert [r["rid"] for r in next(e for e in svc.radio_overview() if e["band"] == "868")["radios"]] == ["r1"]


@pytest.mark.parametrize("rows,band,family", [(MIXED, "433", "sx127x"), (MIXED, "868", "sx1262"),
                                              (TWO_868, "868", None),
                                              pytest.param(NO_LIST, "868", None, marks=_ABSENT)])
def test_w5_2_the_chip_family_is_the_radio_rows_model(tmp_path, rows, band, family):
    svc = _svc(tmp_path, rows)
    assert radio_start.chip_family(svc, band) == family
    if family:
        assert svc.chip_family_for_band(band) == family


def test_w5_2_a_set_is_checked_against_this_radios_chip(tmp_path):
    # POWER 20 is in range on the SX1262 (868, r2) and out of range on the SX127x (433, r1): the row decides.
    svc = _svc(tmp_path, MIXED, unix_replies={"/tmp/loraconf-r1.sock": READY % b"r1",
                                              "/tmp/loraconf-r2.sock": READY % b"r2"})
    from lhpc.core import daemon_control
    assert daemon_control.validate_set("POWER", "20", svc.chip_family_for_band("433"))
    assert not daemon_control.validate_set("POWER", "20", svc.chip_family_for_band("868"))


@pytest.mark.parametrize("argv", [["loraham_daemon", "--radio", "433"],                 # a 0.13.x daemon, no --id
                                  ["loraham_daemon", "--radio", "433", "--id", "r1"]])  # r1's, started outside lhpc
def test_w4_7_a_per_band_stop_never_signals_a_daemon_it_does_not_own(tmp_path, argv):
    # A daemon with no ownership record on r1's band: the stop is not verified, names the pid, and signals nothing
    # (record-driven stop, A §2.3).
    from lhpc.core.outcomes import Outcome
    svc = _svc(tmp_path, radios_fixture.RADIOS, cmdlines_data={4242: argv})
    res = svc.stop("daemon", apply=True, band="433")
    assert not res.ok
    assert [(r.component, r.outcome) for r in res.results] == [("loraham-daemon", Outcome.MANUAL_REQUIRED)]
    assert res.results[0].summary.endswith("stop it yourself: kill 4242")


def test_w1_11_a_per_band_daemon_stop_of_a_shared_band_is_no_longer_refused(tmp_path):
    # Item 16 (DEC-W53 (2)) replaces B4-D39's refusal: a per-band stop of a band two radios share stops each radio's
    # daemon (test_16_radio_stop.py); one radio's daemon alone is `--radio rN`.
    svc = _svc(tmp_path, TWO_868, cmdlines_data={100: ["loraham_daemon", "--radio", "868", "--id", "r1"]})
    for apply in (False, True):
        assert "share it" not in svc.stop("daemon", apply=apply, band="868").summary
    assert "share it" not in svc.restart("daemon", apply=True, band="868").summary


@pytest.mark.parametrize("rows,band", [(radios_fixture.RADIOS, "868"), (NO_LIST, "433"),
                                       ((radios.Radio("r1", "a", "uputronics", "868", ce="ce0"),
                                         radios.Radio("r2", "b", "uputronics", "868", ce="ce1", enabled=False)), "868")])
def test_w1_11_one_radio_or_none_keeps_the_per_band_stop(tmp_path, rows, band):
    svc = _svc(tmp_path, rows)
    assert svc.band_refusal("daemon", band, "stop") is None
    assert svc.band_refusal("kiss", "868", "stop") is None           # a client's stop is never this rule's


@pytest.mark.parametrize("rid,held", [("r1", False), ("r2", True)])
def test_k4b_a_running_multi_band_stack_holds_only_its_instances_band(tmp_path, rid, held):
    # K4b writes no per-stack band marker: kiss's band is its running instance's radio's (r1 433, r2 868), so a
    # MeshCore start on 868 is blocked by kiss@r2 and not by kiss@r1 (no "band unknown, every band").
    from lhpc.core import instances
    from lhpc.core.commands import Instance
    svc = _svc(tmp_path, MIXED)
    opt, val = instances._token(svc, svc.stack("kiss").component("loraham-kiss-tnc"), Instance(rid), "")
    svc = _svc(tmp_path, MIXED, cmdlines_data={62: ["/x/loraham-kiss-tnc", opt, val]})
    assert instances.running_keys(svc, "kiss") == [f"kiss@{rid}"]
    assert any(b["holder_stack"] == "kiss" for b in svc.run_blockers("meshcore", "868")) is held
