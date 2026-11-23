"""F34 C1: one band list (lhpc/core/known_bands.py). K1: every band the data names is a known band. K2: one patch
of known_bands.ALL reaches every caller through its public path (no private copy bound at import)."""
import dataclasses

import radios_fixture

from lhpc.core import config, daemon_control, daemon_params, instances, known_bands, radio_daemon, radio_models, radios
from lhpc.core import resources
from lhpc.core import validators
from lhpc.core.manifest import load_manifest
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService

READY = b"STATUS RADIO=READY ID=%s BOUND=- TXMODE=MANAGED CADWAIT=1500 CADRSSI=-90\n"


def _walk(obj, seen=None):
    """Every dataclass instance reachable from `obj` (tuples, lists, dict values, dataclass fields)."""
    seen = set() if seen is None else seen
    if id(obj) in seen:
        return
    seen.add(id(obj))
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        yield obj
        for f in dataclasses.fields(obj):
            yield from _walk(getattr(obj, f.name), seen)
    elif isinstance(obj, (tuple, list)):
        for x in obj:
            yield from _walk(x, seen)
    elif isinstance(obj, dict):
        for x in obj.values():
            yield from _walk(x, seen)


# ---- K1: the data names known bands only ------------------------------------------------------------------------

def test_every_band_of_the_manifest_is_known():
    # band / bands of every component, and the keys of every param's band_defaults (manifest.py checks only
    # band/bands; a band_defaults key of an unknown band would silently fall back to `default`, model.py band_default)
    named = set()
    for node in _walk(load_manifest()):
        named |= {b for b in (getattr(node, "band", ""),) if isinstance(b, str) and b}
        named |= set(getattr(node, "bands", ()) or ())
        named |= {k for k, _v in getattr(node, "band_defaults", ()) or ()}
    assert named and named <= set(known_bands.ALL), sorted(named - set(known_bands.ALL))


def test_every_band_of_the_radio_models_is_known():
    named = set()
    for m in radio_models.load().values():
        named |= set(m.bands) | {v.band for v in m.variants if v.band}
    assert named and named <= set(known_bands.ALL), sorted(named - set(known_bands.ALL))


def test_every_band_key_of_the_hardware_setups_and_daemon_profiles_is_known():
    hw = {b for _label, preset_by_band in config.HW_SETUPS.values() for b in preset_by_band}
    base = set(daemon_params._BASE) - {"_"}
    profiles = {b for per_band in daemon_params.STACK_DEFAULTS.values() for b in per_band} - {"*"}
    for what, named in (("HW_SETUPS", hw), ("daemon _BASE", base), ("STACK_DEFAULTS", profiles)):
        assert named and named <= set(known_bands.ALL), (what, sorted(named - set(known_bands.ALL)))


# ---- K2: one patch reaches every caller -------------------------------------------------------------------------

def test_a_third_band_in_the_one_list_reaches_validators_claims_and_the_daemon_argv(monkeypatch):
    monkeypatch.setattr(known_bands, "ALL", ("433", "868", "915"))
    assert validators.band("915") == "915"
    assert resources.radio_band("loraham.radio.915") == "915"
    assert resources.radio_band("loraham.radio.{radio:915}") == "915"
    assert resources.normal_key("loraham.daemon-socket.915") == "loraham.daemon-socket.{radio:915}"
    p = radio_daemon.parse_daemon_proc(7, ["/x/loraham_daemon", "--radio", "915"])
    assert (p.kind, p.band) == ("no-id", "915")
    assert daemon_control.conf_socket("915").endswith("loraconf915.sock")
    assert instances._socket_bands(["--sock", "/tmp/loraconf915.sock"]) == {"915"}     # a 0.13 per-band socket


def test_without_the_patch_a_third_band_is_refused_everywhere():
    # the control: the same calls refuse "915" while the list holds 433 and 868 only
    assert resources.radio_band("loraham.radio.915") == ""
    assert radio_daemon.parse_daemon_proc(7, ["/x/loraham_daemon", "--radio", "915"]).kind == "unknown"
    assert instances._socket_bands(["--sock", "/tmp/loraconf915.sock"]) == set()
    try:
        validators.band("915")
    except validators.ValidationError as e:
        assert "allowed: 433, 868, both" in str(e)
    else:
        raise AssertionError("915 accepted")


def test_a_third_band_reaches_the_daemon_overview_strays(tmp_path, monkeypatch):
    # the service's band loop (the stray daemons of the dashboard) reads the same list: a daemon answering for 915
    # with no radio there is a stray row, as an 868 one is (test_radio_overview_per_radio.py)
    monkeypatch.setattr(known_bands, "ALL", ("433", "868", "915"))
    radios_fixture.write(tmp_path, (radios.Radio("r1", "a", "uputronics", "433", ce="ce0"),))
    fake = FakeSystem(unix_replies={"/tmp/loraconf-r1.sock": READY % b"r1", "/tmp/loraconf-r9.sock": READY % b"r9"})
    svc = ControllerService(system=fake.system, paths=Paths(runtime_root=tmp_path))
    real = svc.daemon_view
    monkeypatch.setattr(svc, "daemon_view", lambda band, rid="": daemon_control.read_view(svc._system, "r9")
                        if band == "915" and not rid else real(band, rid))
    assert [(x["band"], x["daemon"]["reachable"]) for x in svc.stray_daemons()] == [("915", True)]


def test_a_third_band_reaches_the_hardware_add_band_selector(web, monkeypatch):
    # the HAT form's Band <select> (hardware_add.html) is built from the list the route passes: the same patch
    # shows a third option; unpatched it offers 433 and 868, as the hand-written options did
    def options():
        page = web().get("/hardware/add").get_data(as_text=True)
        form = page[page.index('id="hw-addhat"'):]
        sel = form[form.index('<select name="band">'):]
        return sel[:sel.index("</select>")]
    assert '<option value="433">433 MHz</option><option value="868">868 MHz</option>' in options()
    monkeypatch.setattr(known_bands, "ALL", ("433", "868", "915"))
    assert '<option value="915">915 MHz</option>' in options()
