"""Radio HARDWARE setup — the enumerated multi-hardware model that supersedes the old band-only
radio mode. Covers: the setup catalog + config, the v112 daemon argv (one process per radio, `--hw`,
no band-suffixed flags), the refuse-to-start gate when unconfigured, the LED probe, and the retained
invariants (one process per band, stray disclosure, stop/uninstall never gated)."""

from __future__ import annotations

import os

import pytest

from lhpc.core import commands, config
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import CommandResult, FakeSystem
from lhpc.core.probes.hardware import ProbeResult, probe_radio
from lhpc.core.services import ControllerService

# This module drives the hardware model directly, so it opts out of the test-baseline setup and sees
# the true fresh-install default ('unset'); tests that need a configured box call `_setup(...)`.
pytestmark = pytest.mark.no_default_hardware

_RDY = b"STATUS RADIO=READY TXMODE=MANAGED CADWAIT=1500 CADIDLE=250\n"


def _svc(tmp_path):
    return ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))


def _setup(svc, setup_id):
    config.save_hardware_setup(svc._paths, setup_id)
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
    return set(re.findall(r"start daemon --radio (\w+)", "\n".join(details)))


# ---- config: catalog + fresh-install default ----------------------------------------------------

def test_config_default_is_unconfigured(tmp_path):
    c = config.load_config(Paths(runtime_root=tmp_path))
    assert c.radio.hardware == "unset"
    assert c.radio.active_bands == ()            # nothing served until a board is picked
    assert c.radio.configured is False
    assert c.radio.radio_mode == "unset"


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
    assert c.radio.active_bands == bands and c.radio.configured
    for b in ("433", "868"):
        assert c.radio.hw_preset(b) == presets.get(b, "")
    assert c.radio.radio_mode == ("both" if len(bands) == 2 else bands[0])


def test_config_bad_value_is_diagnostic_and_falls_open_to_unset(tmp_path):
    p = Paths(runtime_root=tmp_path)
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    (tmp_path / "config" / "local.toml").write_text('[radio]\nhardware = "nonsense"\n')
    c = config.load_config(p)
    assert c.radio.hardware == "unset"                             # fail-open: refuse, not guess
    assert any("radio.hardware" in d for d in c.diagnostics)


def test_migrates_legacy_hw_preset_to_loraham(tmp_path):
    # The daemon renamed --hw `legacy` -> `loraham` and removed `legacy` (it now fails the daemon
    # usage check). A stored [radio].hardware="legacy" must migrate to the `loraham` setup so an
    # existing install keeps working instead of reading as unconfigured / launching --hw legacy.
    p = Paths(runtime_root=tmp_path)
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    (tmp_path / "config" / "local.toml").write_text('[radio]\nhardware = "legacy"\n')
    c = config.load_config(p)
    assert c.radio.hardware == "loraham"
    assert c.radio.hw_preset("433") == "loraham" and c.radio.hw_preset("868") == "loraham"
    assert any("legacy" in d and "loraham" in d for d in c.diagnostics)


def test_save_rejects_invalid_setup(tmp_path):
    with pytest.raises(config.ConfigError):
        config.save_hardware_setup(Paths(runtime_root=tmp_path), "nope")


def test_service_helpers(tmp_path):
    svc = _svc(tmp_path)
    assert svc.active_bands() == () and not svc.hardware_configured()
    _setup(svc, "waveshare-433")
    assert svc.hardware_setup() == "waveshare-433" and svc.hardware_configured()
    assert svc.active_bands() == ("433",) and svc.radio_mode() == "433"
    assert svc.band_active("433") and not svc.band_active("868")
    assert svc.hw_preset_for_band("433") == "waveshare-sx1262"
    assert dict(svc.hw_setups())["loraham"].startswith("LoRaHAM")


# ---- M0: v112 daemon argv — one process per radio, `--hw`, no band-suffixed flags ---------------

def test_daemon_argv_is_v112_shape_no_suffixed_flags(tmp_path):
    svc = _svc(tmp_path)
    comp = svc.stack("daemon").component("loraham-daemon")
    op = svc.config().operator
    params = {"radio": "433", "hw": "loraham", "txmode": "direct", "cadmon": "on", "cadrssi": "-95"}
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
    assert {"tx_433", "tx_868", "cadmon_433", "cadmon_868"} <= names  # per-band stored values kept
    radio = next(p for p in daemon.run_params if p.name == "radio")
    assert "both" not in radio.choices and set(radio.choices) == {"433", "868"}


# ---- M2: refuse-to-start when no hardware is configured -----------------------------------------

def test_hardware_block_refuses_daemon_and_radio_stacks_when_unset(tmp_path):
    svc = _svc(tmp_path)                                            # default: unset
    for target in ("daemon", "meshcore", "chat"):
        assert "no radio hardware configured" in svc.hardware_block(target)
        for apply in (False, True):
            r = svc.start(target, apply=apply)
            assert not r.ok and "no radio hardware configured" in r.summary
            assert "lhpc hardware" in " ".join(r.next_commands)


def test_hardware_block_clears_once_configured(tmp_path):
    svc = _svc(tmp_path)
    _setup(svc, "loraham")
    assert svc.hardware_block("daemon") == "" and svc.hardware_block("meshcore") == ""


def test_start_refuses_absent_band_stack_after_configured(tmp_path):
    # Once hardware IS configured, the (separate) radio-mode gate still refuses a stack whose only
    # band is not served — with the radio-mode message, not the hardware one.
    svc = _svc(tmp_path)
    _setup(svc, "waveshare-433")                                   # serves 433 only
    r = svc.start("meshcore", apply=False)                         # meshcore is fixed 868
    assert not r.ok and "radio mode is 433-only" in r.summary


# ---- retained invariants: one process per band, serve-bands never 'both' ------------------------

def test_daemon_serve_bands_never_both(tmp_path):
    svc = _svc(tmp_path)
    _setup(svc, "loraham")
    assert svc._daemon_serve_bands("") == ["433", "868"]
    assert svc._daemon_serve_bands("both") == ["433", "868"]       # legacy 'both' -> active bands
    assert svc._daemon_serve_bands("433") == ["433"]
    _setup(svc, "uputronics-433")
    assert svc._daemon_serve_bands("both") == ["433"]              # clamped, never 'both'
    assert svc._daemon_serve_bands("868") == ["433"]              # excluded single -> active


@pytest.mark.needs_session
def test_dual_setup_spawns_two_single_band_processes(tmp_path):
    svc = _daemon_svc(tmp_path, {})
    _setup(svc, "loraham")
    res = svc.start("daemon", apply=True)
    assert _band_starts(res.details) == {"433", "868"}
    assert "--radio both" not in "\n".join(res.details)


@pytest.mark.needs_session
def test_single_setup_starts_only_the_active_band(tmp_path):
    svc = _daemon_svc(tmp_path, {})
    _setup(svc, "uputronics-433")
    res = svc.start("daemon", apply=True)
    assert _band_starts(res.details) == {"433"}
    assert "--radio both" not in "\n".join(res.details)


# ---- stray disclosure (unchanged behavior, now fed by the setup) --------------------------------

def test_shared_pid_stray_is_disclosed(tmp_path):
    svc = _daemon_svc(tmp_path, {"/tmp/loraconf433.sock": _RDY, "/tmp/loraconf868.sock": _RDY},
                      cmdlines={100: ["loraham_daemon", "--radio", "both"]})
    _setup(svc, "waveshare-433")                                   # serves 433 only
    cards = {r["band"]: r for r in svc.radio_overview()}
    assert not cards["433"]["stray"]
    assert cards["868"]["stray"] and cards["868"]["shared_with"] == ["433"]


def test_dual_setup_radio_overview_two_columns_no_strays(tmp_path):
    svc = _daemon_svc(tmp_path, {"/tmp/loraconf433.sock": _RDY, "/tmp/loraconf868.sock": _RDY},
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


# ---- M4: LED probe --------------------------------------------------------------------------------

class _Runner:
    def __init__(self, result):
        self._r, self.calls = result, []

    def run(self, argv, timeout, cwd=None, env=None):
        self.calls.append((argv, cwd, env))
        return self._r


class _Sys:
    def __init__(self, runner):
        self.runner = runner


def test_probe_present_when_daemon_stays_up(tmp_path):
    # SUCCESS: the daemon never exits, so the bounded runner times out (and terminated it) -> present.
    runner = _Runner(CommandResult(returncode=124, stdout="[Daemon] active radios: 433\n",
                                   stderr="", timed_out=True))
    pr = probe_radio(_Sys(runner), "/bin/daemon", "/src", "433", "loraham", runtime_dir="/rt")
    assert pr.present and not pr.busy
    argv, _cwd, env = runner.calls[0]
    assert argv == ["/bin/daemon", "--radio", "433", "--hw", "loraham", "--debug"]
    assert env["LORAHAM_RUNTIME_DIR"] == "/rt"
    # v112 defaults sockets to /run/loraham (not created on a direct spawn); without a socket dir the
    # probe daemon dies at socket-open before begin() and every probe reads 'not detected'.
    assert env["LORAHAM_SOCKET_DIR"] == "/tmp"


def test_probe_absent_captures_chip_diagnostic(tmp_path):
    runner = _Runner(CommandResult(
        returncode=1, stdout="",
        stderr="SX1262 antwortet nicht (BUSY) - HAT fehlt, falsches Profil oder Verdrahtung\n"
               "[Daemon] Kein ausgewaehltes Radio bereit, beende.\n", timed_out=False))
    pr = probe_radio(_Sys(runner), "/bin/daemon", "/src", "868", "waveshare-sx1262", runtime_dir="/rt")
    assert not pr.present and not pr.busy
    assert "antwortet nicht" in pr.diagnostic


def test_probe_busy_when_band_already_served(tmp_path):
    runner = _Runner(CommandResult(returncode=3, stdout="", stderr="instance lock busy\n",
                                   timed_out=False))
    pr = probe_radio(_Sys(runner), "/bin/daemon", "/src", "433", "loraham", runtime_dir="/rt")
    assert pr.busy and not pr.present


def test_probe_binary_missing(tmp_path):
    runner = _Runner(CommandResult(returncode=127, stdout="", stderr="", not_found=True))
    pr = probe_radio(_Sys(runner), "/bin/daemon", "/src", "433", "loraham", runtime_dir="/rt")
    assert not pr.present and "not found" in pr.message


def test_service_probe_guards(tmp_path):
    svc = _svc(tmp_path)
    assert isinstance(svc.probe_hardware("999", "loraham"), ProbeResult)
    assert "invalid band" in svc.probe_hardware("999", "loraham").message
    assert "unknown hardware preset" in svc.probe_hardware("433", "bogus").message
    # A known request with the daemon not built stops before any spawn.
    assert "not built" in svc.probe_hardware("433", "loraham").message


# ---- M5: dashboard states -----------------------------------------------------------------------

def _body_class(html):
    return html.split("<body", 1)[1].split(">", 1)[0]


def test_dashboard_unconfigured_shows_configure_banner(tmp_path):
    from lhpc.adapters.web.app import create_app
    svc = _svc(tmp_path)
    app = create_app(service_factory=lambda: svc)
    app.config["SESSION_COOKIE_SECURE"] = False
    body = app.test_client().get("/").get_data(as_text=True)
    assert "No radio hardware is configured" in body


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
    assert "Hardware setup" in body                              # the selector is present
    assert "Radio hardware" in body                              # per-stack "configure" note shown


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


def test_probe_message_uses_friendly_label_not_wire_name(tmp_path):
    runner = _Runner(CommandResult(returncode=124, stdout="active radios\n", stderr="", timed_out=True))
    pr = probe_radio(_Sys(runner), "/bin/daemon", "/src", "433", "loraham",
                     runtime_dir="/rt", label="LoRaHAM")
    assert pr.present and "LoRaHAM" in pr.message and "legacy" not in pr.message


def test_probe_result_renders_inline_and_is_consumed_once(tmp_path):
    # The Detect result is stashed in the session and rendered INLINE (under the Detect button on the
    # daemon Hardware settings), not as a top-level flash; a reload does not re-show it.
    from lhpc.adapters.web.app import create_app
    svc = _svc(tmp_path)
    _setup(svc, "loraham")
    app = create_app(service_factory=lambda: svc)
    app.config["SESSION_COOKIE_SECURE"] = False
    c = app.test_client()
    c.get("/stacks")
    with c.session_transaction() as s:
        tok = s["_csrf"]
    r = c.post("/hardware/probe", data={"_csrf": tok, "band": "433", "hw": "loraham"})
    assert r.status_code == 302                                  # PRG: redirect, result in session
    body = c.get("/stacks?open=daemon&cfg=daemon").get_data(as_text=True)
    assert "not built" in body                                  # inline result shown (daemon unbuilt)
    body2 = c.get("/stacks?open=daemon&cfg=daemon").get_data(as_text=True)
    assert "not built" not in body2                             # consumed once (popped from session)


def test_dashboard_narrows_in_single_radio_setup(tmp_path):
    from lhpc.adapters.web.app import create_app
    svc = _svc(tmp_path)
    _setup(svc, "loraham")
    app = create_app(service_factory=lambda: svc)
    app.config["SESSION_COOKIE_SECURE"] = False
    c = app.test_client()
    assert "dash-narrow" not in _body_class(c.get("/").get_data(as_text=True))   # dual -> full width
    _setup(svc, "waveshare-433")
    assert "dash-narrow" in _body_class(c.get("/").get_data(as_text=True))       # single -> narrowed
