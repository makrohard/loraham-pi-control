"""Radio mode (both | 433 | 868) — config, availability gate, and the mandatory invariants:
M-1 (a daemon is NEVER started with --radio both), M-2 (a shared-PID stray is disclosed), and
M-3 (stop/uninstall of an excluded band are never radio-mode-blocked)."""

from __future__ import annotations

import os

import pytest

from lhpc.core import config
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService

_RDY = b"STATUS RADIO=READY TXMODE=MANAGED CADWAIT=1500 CADIDLE=250\n"


def _svc(tmp_path):
    return ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))


def _mode(svc, mode):
    config.save_radio_mode(svc._paths, mode)
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


# ---- config -------------------------------------------------------------------------------------

def test_config_default_and_active_bands(tmp_path):
    c = config.load_config(Paths(runtime_root=tmp_path))
    assert c.radio.mode == "both" and c.radio.active_bands == ("433", "868")


def test_config_save_and_reload(tmp_path):
    p = Paths(runtime_root=tmp_path)
    for mode, bands in (("433", ("433",)), ("868", ("868",)), ("both", ("433", "868"))):
        config.save_radio_mode(p, mode)
        c = config.load_config(p)
        assert c.radio.mode == mode and c.radio.active_bands == bands and c.diagnostics == []


def test_config_bad_value_is_diagnostic_and_falls_open_to_both(tmp_path):
    p = Paths(runtime_root=tmp_path)
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    (tmp_path / "config" / "local.toml").write_text('[radio]\nmode = "999"\n')
    c = config.load_config(p)
    assert c.radio.mode == "both"                                   # fail-open: never blocks
    assert any("radio.mode" in d for d in c.diagnostics)


def test_save_rejects_invalid_mode(tmp_path):
    with pytest.raises(config.ConfigError):
        config.save_radio_mode(Paths(runtime_root=tmp_path), "nope")


def test_service_helpers(tmp_path):
    svc = _svc(tmp_path)
    assert svc.active_bands() == ("433", "868") and svc.band_active("868")
    _mode(svc, "433")
    assert svc.radio_mode() == "433" and svc.active_bands() == ("433",)
    assert svc.band_active("433") and not svc.band_active("868")


# ---- availability gate --------------------------------------------------------------------------

def test_gate_blocks_fixed_absent_band_stack(tmp_path):
    svc = _svc(tmp_path)
    assert svc.radio_mode_block("meshcore") == ""                  # both mode: available
    _mode(svc, "433")
    reason = svc.radio_mode_block("meshcore")                      # meshcore is fixed 868
    assert "868" in reason and "433-only" in reason
    _mode(svc, "868")
    assert svc.radio_mode_block("meshcore") == ""                  # 868 mode: available again


def test_gate_allows_switchable_stack_in_either_single_mode(tmp_path):
    svc = _svc(tmp_path)                                            # voice: bands 433+868
    for m in ("433", "868"):
        _mode(svc, m)
        assert svc.radio_mode_block("voice") == ""                 # switchable -> any active band ok
        assert svc.radio_mode_block("meshtastic") == ""            # meshtastic is switchable 433/868


def test_gate_daemon_itself_never_blocked(tmp_path):
    svc = _svc(tmp_path)
    for m in ("both", "433", "868"):
        _mode(svc, m)
        assert svc.radio_mode_block("daemon") == ""                # no fixed band -> always available


def test_start_refuses_absent_band_stack_dry_and_apply(tmp_path):
    svc = _svc(tmp_path)
    _mode(svc, "433")
    for apply in (False, True):
        r = svc.start("meshcore", apply=apply)
        assert not r.ok and "radio mode is 433-only" in r.summary


# ---- M-1: --radio both is GONE — a daemon is only ever started per single band ------------------

def test_both_is_not_a_daemon_radio_choice(tmp_path):
    # the offered param choices no longer include 'both'
    daemon = _svc(tmp_path).stack("daemon").component("loraham-daemon")
    radio = next(p for p in daemon.run_params if p.name == "radio")
    assert "both" not in radio.choices and set(radio.choices) == {"433", "868"}


def test_daemon_serve_bands_never_both(tmp_path):
    svc = _svc(tmp_path)                                            # both mode
    assert svc._daemon_serve_bands("") == ["433", "868"]           # all active, as two bands
    assert svc._daemon_serve_bands("both") == ["433", "868"]       # legacy 'both' -> active bands
    assert svc._daemon_serve_bands("433") == ["433"]
    _mode(svc, "433")
    assert svc._daemon_serve_bands("both") == ["433"]              # clamped, never 'both'
    assert svc._daemon_serve_bands("868") == ["433"]              # excluded single -> active
    assert svc._daemon_serve_bands("433") == ["433"]


@pytest.mark.needs_session
def test_m1_both_mode_spawns_two_single_band_processes(tmp_path):
    svc = _daemon_svc(tmp_path, {})                                 # default both mode
    res = svc.start("daemon", apply=True)
    assert _band_starts(res.details) == {"433", "868"}             # two explicit single-band procs
    assert "--radio both" not in "\n".join(res.details)


@pytest.mark.needs_session
def test_m1_single_mode_starts_only_the_active_band(tmp_path):
    svc = _daemon_svc(tmp_path, {})
    _mode(svc, "433")
    res = svc.start("daemon", apply=True)
    assert _band_starts(res.details) == {"433"}                    # only 433, never both/868
    assert "--radio both" not in "\n".join(res.details)


# ---- M-2: shared-PID stray disclosure -----------------------------------------------------------

def test_m2_shared_pid_stray_is_disclosed(tmp_path):
    # ONE --radio both process serves both bands; under mode 433 the 868 card is a stray whose Stop
    # would also kill the active 433 daemon -> it must disclose shared_with.
    svc = _daemon_svc(tmp_path, {"/tmp/loraconf433.sock": _RDY, "/tmp/loraconf868.sock": _RDY},
                      cmdlines={100: ["loraham_daemon", "--radio", "both"]})
    _mode(svc, "433")
    cards = {r["band"]: r for r in svc.radio_overview()}
    assert not cards["433"]["stray"]                               # active column
    assert cards["868"]["stray"] and cards["868"]["shared_with"] == ["433"]


def test_m2_separate_pid_stray_is_independent(tmp_path):
    # two per-band processes: stopping the 868 stray does NOT touch 433 -> no shared disclosure.
    svc = _daemon_svc(tmp_path, {"/tmp/loraconf433.sock": _RDY, "/tmp/loraconf868.sock": _RDY},
                      cmdlines={100: ["loraham_daemon", "--radio", "433"],
                                101: ["loraham_daemon", "--radio", "868"]})
    _mode(svc, "433")
    cards = {r["band"]: r for r in svc.radio_overview()}
    assert cards["868"]["stray"] and cards["868"]["shared_with"] == []


def test_both_mode_radio_overview_shows_two_columns_no_strays(tmp_path):
    svc = _daemon_svc(tmp_path, {"/tmp/loraconf433.sock": _RDY, "/tmp/loraconf868.sock": _RDY},
                      cmdlines={100: ["loraham_daemon", "--radio", "both"]})
    cards = svc.radio_overview()
    assert {c["band"] for c in cards} == {"433", "868"}
    assert not any(c["stray"] for c in cards)                      # both mode: nothing is a stray


# ---- M-3: stop/uninstall of an excluded band are NEVER radio-mode-blocked -----------------------

def test_m3_stop_and_uninstall_never_radio_blocked(tmp_path):
    svc = _svc(tmp_path)
    _mode(svc, "433")
    assert not svc.start("meshcore", apply=False).ok               # start IS blocked
    # ...but recovery-direction ops are OUTSIDE the gate:
    assert svc.stop("meshcore", apply=False).ok
    assert "radio mode" not in svc.stop("meshcore", apply=False).summary
    assert svc.uninstall("meshcore", apply=False).ok


def test_dashboard_narrows_in_single_radio_mode(tmp_path):
    # Single-radio mode: the Dashboard carries the `dash-narrow` body class so the whole page
    # (header/footer/boxes/banners) shrinks to the single radio box's width; `both` stays full width.
    from lhpc.adapters.web.app import create_app
    svc = _svc(tmp_path)
    app = create_app(service_factory=lambda: svc)
    app.config["SESSION_COOKIE_SECURE"] = False
    c = app.test_client()
    body_tag = lambda html: html.split("<body", 1)[1].split(">", 1)[0]
    assert "dash-narrow" not in body_tag(c.get("/").get_data(as_text=True))   # both -> full width
    _mode(svc, "433")
    assert "dash-narrow" in body_tag(c.get("/").get_data(as_text=True))       # single -> narrowed
