"""Radio HARDWARE setup — the enumerated multi-hardware model that supersedes the old band-only
radio mode. Covers: the setup catalog + config, the v112 daemon argv (one process per radio, `--hw`,
no band-suffixed flags), the refuse-to-start gate when unconfigured, the LED probe, and the retained
invariants (one process per band, stray disclosure, stop/uninstall never gated)."""

from __future__ import annotations

import os

import pytest
import radios_fixture

from lhpc.core import commands, config, radios
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService

# This module drives the hardware model directly, so it opts out of the test-baseline setup and sees
# the true fresh-install default ('unset'); tests that need a configured box call `_setup(...)`.
pytestmark = pytest.mark.no_default_hardware

_RDY = b"STATUS RADIO=READY TXMODE=MANAGED CADWAIT=1500 CADIDLE=250\n"


def _svc(tmp_path):
    return ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))


def _setup(svc, setup_id):
    # S2: a box set up as `setup_id` has the hardware list `lhpc hardware <setup>` writes; the list decides the bands.
    from lhpc.core import radio_start
    (svc._paths.runtime_root / "config" / "radios.toml").unlink(missing_ok=True)
    config.save_hardware_setup(svc._paths, setup_id)
    assert radio_start.setup_radios(svc._paths, setup_id)[0]
    svc._invalidate_config()


def _daemon_svc(tmp_path, unix_replies, cmdlines=None):
    binp = tmp_path / "src" / "loraham-daemon" / "loraham_daemon" / "loraham_daemon"
    binp.parent.mkdir(parents=True)
    binp.write_text("#!/bin/sh\nsleep 0.1\n")
    os.chmod(binp, 0o755)
    kw = {"unix_replies": unix_replies}
    if cmdlines is not None:
        kw["cmdlines_data"] = cmdlines
    return ControllerService(system=FakeSystem(**kw).system, paths=Paths(runtime_root=tmp_path))


def _band_starts(details):
    import re
    return set(re.findall(r"start daemon r[0-9]+ --radio (\w+)", "\n".join(details)))


# ---- config: catalog + fresh-install default ----------------------------------------------------

def test_config_default_is_unconfigured(tmp_path):
    c = config.load_config(Paths(runtime_root=tmp_path))
    assert c.radio.hardware == "unset"


@pytest.mark.parametrize("setup,bands,presets", [
    ("loraham", ("433", "868"), {"433": "loraham", "868": "loraham"}),
    ("uputronics", ("433", "868"), {"433": "uputronics-ce0", "868": "uputronics-ce1"}),
    ("uputronics-433", ("433",), {"433": "uputronics-ce0"}),
    ("uputronics-868", ("868",), {"868": "uputronics-ce1"}),
    ("waveshare-433", ("433",), {"433": "waveshare-sx1262"}),
    ("waveshare-868", ("868",), {"868": "waveshare-sx1262"}),
])
def test_config_setup_bands_and_presets(tmp_path, setup, bands, presets):
    p = Paths(runtime_root=tmp_path)
    config.save_hardware_setup(p, setup)
    c = config.load_config(p)
    assert c.radio.hardware == setup and c.diagnostics == []
    for b in ("433", "868"):
        assert c.radio.hw_preset(b) == presets.get(b, "")


def test_config_bad_value_is_diagnostic_and_falls_open_to_unset(tmp_path):
    p = Paths(runtime_root=tmp_path)
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    (tmp_path / "config" / "local.toml").write_text('[radio]\nhardware = "nonsense"\n')
    c = config.load_config(p)
    assert c.radio.hardware == "unset"                             # fail-open: refuse, not guess
    assert any("radio.hardware" in d for d in c.diagnostics)


@pytest.mark.contract
def test_save_rejects_invalid_setup(tmp_path):
    with pytest.raises(config.ConfigError):
        config.save_hardware_setup(Paths(runtime_root=tmp_path), "nope")


def test_service_helpers(tmp_path):
    svc = _svc(tmp_path)
    assert svc.radio_bands() == () and not svc.hardware_configured()
    _setup(svc, "waveshare-433")
    assert svc.hardware_setup() == "waveshare-433" and svc.hardware_configured()
    assert svc.radio_bands() == ("433",)                           # the list's one radio decides (S2)
    assert svc.band_active("433") and not svc.band_active("868")
    assert dict(svc.hw_setups())["loraham"].startswith("LoRaHAM")


# ---- M0: v112 daemon argv — one process per radio, `--hw`, no band-suffixed flags ---------------

def test_daemon_argv_is_v112_shape_no_suffixed_flags(tmp_path):
    svc = _svc(tmp_path)
    comp = svc.stack("daemon").component("loraham-daemon")
    op = svc.config().operator
    params = {"radio": "433", "hw": "loraham", "txmode": "direct", "cadmon": "on", "cadrssi": "-95",
              "id": "r1", "rflog_path": "{runtime}/logs/r1/rf-daemon.log"}      # stage L5: the radio's own
    argv = commands.expand_argv(comp.run_argv, comp, params, op, "/rt", "/src", "433")
    assert argv[argv.index("--radio") + 1] == "433"
    assert argv[argv.index("--hw") + 1] == "loraham"
    assert argv[argv.index("--tx-mode") + 1] == "direct"
    assert argv[argv.index("--cad-monitor") + 1] == "on"
    assert argv[argv.index("--cad-rssi") + 1] == "-95"
    # No removed band-suffixed flags survive anywhere in the expanded argv.
    assert not any(tok.endswith(("-433", "-868")) for tok in argv)
    assert "--radio" in argv and "both" not in argv


def test_daemon_hidden_injected_params_exist(tmp_path):
    daemon = _svc(tmp_path).stack("daemon").component("loraham-daemon")
    names = {p.name for p in daemon.run_params}
    assert {"hw", "txmode", "cadmon", "cadrssi"} <= names          # injected at spawn
    assert not {"tx_433", "tx_868", "cadmon_433", "cadmon_868"} & names   # K2: no per-band value (13.8)
    radio = next(p for p in daemon.run_params if p.name == "radio")
    assert "both" not in radio.choices and set(radio.choices) == {"433", "868"}


# ---- M2: refuse-to-start when no hardware is configured -----------------------------------------

@pytest.mark.contract
def test_hardware_block_refuses_daemon_and_radio_stacks_when_unset(tmp_path):
    svc = _svc(tmp_path)                                            # default: unset
    # identity now gates first at the public entry (callsign-identities): satisfy it so
    # this test keeps exercising the hardware refusal it is about
    assert svc.set_operator_identity(callsign="XX0XXA").ok
    assert svc.save_config_bundle("meshcore", values={"file_node_name": "TestNode"}).ok
    for target in ("daemon", "meshcore", "chat"):
        assert "no enabled radio in the hardware list" in svc.hardware_block(target)
        for apply in (False, True):
            r = svc.start(target, apply=apply)
            assert not r.ok and "no enabled radio in the hardware list" in r.summary
            assert "lhpc hardware" in " ".join(r.next_commands)


@pytest.mark.contract
def test_hardware_block_clears_once_configured(tmp_path):
    svc = _svc(tmp_path)
    _setup(svc, "loraham")
    assert svc.hardware_block("daemon") == "" and svc.hardware_block("meshcore") == ""


def test_start_refuses_absent_band_stack_after_configured(tmp_path):
    # Once hardware IS configured, the (separate) radio-mode gate still refuses a stack whose only
    # band is not served — with the radio-mode message, not the hardware one.
    svc = _svc(tmp_path)
    _setup(svc, "waveshare-433")                                   # serves 433 only
    # a usable identity, which plan and apply both judge first
    assert svc.save_config_bundle("meshcore", values={"file_node_name": "TestNode"}).ok
    r = svc.start("meshcore", apply=False)                         # meshcore is fixed 868
    assert not r.ok and "the hardware list has radios on 433 only" in r.summary


# ---- retained invariants: one process per band, serve-bands never 'both' ------------------------

@pytest.mark.contract
@pytest.mark.parametrize("rows,raw,bands", [
    ((radios.Radio("r1", "a", "loraham", "433"), radios.Radio("r2", "b", "loraham", "868", enabled=False)), None, ("433",)),
    ((radios.Radio("r1", "a", "loraham", "868"),), None, ("868",)),
    (None, "[[radio]]\nid = 7\n", ()),                          # an invalid list serves nothing
])
def test_the_served_bands_are_the_enabled_radios_of_a_valid_list(tmp_path, rows, raw, bands):
    # S2: the list decides, whatever the board setup says (it names both bands here).
    config.save_hardware_setup(Paths(runtime_root=tmp_path), "uputronics")
    if rows is not None:
        radios_fixture.write(tmp_path, rows)
    else:
        (tmp_path / "config" / "radios.toml").write_text(raw)
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    assert svc.radio_bands() == bands
    assert [b for b in ("433", "868") if svc.band_active(b)] == list(bands)


def test_daemon_bands_follow_the_hardware_list_never_both(tmp_path):
    svc = _svc(tmp_path)
    _setup(svc, "loraham")
    assert svc._daemon_bands("") == ["433", "868"]
    assert svc._daemon_bands("both") == ["433", "868"]       # legacy 'both' -> the list's bands
    assert svc._daemon_bands("433") == ["433"]
    _setup(svc, "uputronics-433")
    assert svc._daemon_bands("both") == ["433"]              # never 'both'
    assert svc._daemon_bands("868") == ["433"]              # a band no radio of the list has -> the list's


@pytest.mark.needs_session
def test_dual_setup_spawns_two_single_band_processes(tmp_path):
    svc = _daemon_svc(tmp_path, {})
    _setup(svc, "loraham")
    radios_fixture.write(tmp_path)                                  # stage L5: r1 on 433, r2 on 868
    res = svc.start("daemon", apply=True)
    assert _band_starts(res.details) == {"433", "868"}
    assert "--radio both" not in "\n".join(res.details)


@pytest.mark.needs_session
def test_single_setup_starts_only_the_active_band(tmp_path):
    svc = _daemon_svc(tmp_path, {})
    _setup(svc, "uputronics-433")
    radios_fixture.write(tmp_path, (radios.Radio("r1", "low", "uputronics", "433", ce="ce0"),))
    res = svc.start("daemon", apply=True)
    assert _band_starts(res.details) == {"433"}
    assert "--radio both" not in "\n".join(res.details)


# ---- stray disclosure (unchanged behavior, now fed by the setup) --------------------------------

def test_dual_setup_radio_overview_two_columns_no_strays(tmp_path):
    svc = _daemon_svc(tmp_path, {"/tmp/loraconf-r1.sock": _RDY, "/tmp/loraconf-r2.sock": _RDY},
                      cmdlines={100: ["loraham_daemon", "--radio", "both"]})
    _setup(svc, "loraham")
    cards = svc.radio_overview()
    assert {c["band"] for c in cards} == {"433", "868"}
    assert not any(c["stray"] for c in cards)


# ---- M-3: stop/uninstall are never radio-gated --------------------------------------------------

def test_stop_and_uninstall_never_hardware_or_radio_blocked(tmp_path):
    svc = _svc(tmp_path)
    _setup(svc, "waveshare-433")
    assert not svc.start("meshcore", apply=False).ok               # start IS blocked (868 not served)
    assert svc.stop("meshcore", apply=False).ok                    # recovery-direction ops are outside
    assert svc.uninstall("meshcore", apply=False).ok


# ---- M5: dashboard states -----------------------------------------------------------------------

def _body_has_class(html, name):
    import htmlq
    return htmlq.parse(html).find("body")[0].has_class(name)


def test_dashboard_unconfigured_shows_configure_banner(tmp_path):
    from lhpc.adapters.web.app import create_app
    svc = _svc(tmp_path)
    app = create_app(service_factory=lambda: svc)
    app.config["SESSION_COOKIE_SECURE"] = False
    body = app.test_client().get("/").get_data(as_text=True)
    assert "No radio hardware is set up yet." in body                     # Home's setup banner (W250)
    assert 'href="/hardware/add"' in body                                 # it opens Add Hardware


def test_stacks_page_renders_when_unconfigured(tmp_path):
    # The dashboard's "Configure" banner links to /stacks (daemon Hardware settings), so that page
    # MUST render cleanly with no hardware configured — it is the primary setup entry point.
    from lhpc.adapters.web.app import create_app
    svc = _svc(tmp_path)
    app = create_app(service_factory=lambda: svc)
    app.config["SESSION_COOKIE_SECURE"] = False
    r = app.test_client().get("/stacks?open=daemon&cfg=daemon")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "Radio hardware" in body                              # per-stack "configure" note shown
    # DEC-W57: the board selector is Add Hardware's HAT row now; it renders with no hardware configured.
    add = app.test_client().get("/hardware/add")
    assert add.status_code == 200 and 'id="hw-addhat"' in add.get_data(as_text=True)


def test_footer_shows_current_hardware_friendly(tmp_path):
    from lhpc.adapters.web.app import create_app
    svc = _svc(tmp_path)
    _setup(svc, "loraham")
    app = create_app(service_factory=lambda: svc)
    app.config["SESSION_COOKIE_SECURE"] = False
    body = app.test_client().get("/").get_data(as_text=True)
    assert "Radio hardware:" in body                             # 3rd footer line
    assert "LoRaHAM dual-module" in body and "legacy" not in body


def test_footer_unconfigured_says_not_configured(tmp_path):
    from lhpc.adapters.web.app import create_app
    svc = _svc(tmp_path)                                         # module opts out of the baseline -> unset
    app = create_app(service_factory=lambda: svc)
    app.config["SESSION_COOKIE_SECURE"] = False
    body = app.test_client().get("/").get_data(as_text=True)
    assert "Radio hardware:" in body and "Not configured" in body


def test_hw_preset_label_is_friendly_never_legacy():
    assert config.hw_preset_label("loraham") == "LoRaHAM"
    assert config.hw_preset_label("waveshare-sx1262") == "Waveshare SX1262"
    assert config.hw_preset_label("uputronics-ce0") == "Uputronics CE0"


def test_dashboard_narrows_in_single_radio_setup(tmp_path):
    from lhpc.adapters.web.app import create_app
    svc = _svc(tmp_path)
    _setup(svc, "loraham")
    app = create_app(service_factory=lambda: svc)
    app.config["SESSION_COOKIE_SECURE"] = False
    c = app.test_client()
    assert not _body_has_class(c.get("/").get_data(as_text=True), "dash-narrow")   # dual -> full width
    _setup(svc, "waveshare-433")
    assert _body_has_class(c.get("/").get_data(as_text=True), "dash-narrow")       # single -> narrowed
