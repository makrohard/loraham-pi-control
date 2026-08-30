"""`lhpc meshtastic` guarded passthrough — classifier, execution, drift and managed-exe tests.

The classifier tests are pure (no node, no CLI). The execution tests monkeypatch os.execv /
subprocess.run to capture the exact forwarded argv without replacing the test process or needing a
running node. A drift test pins the policy to the manifest's Meshtastic version.
"""

from __future__ import annotations

import os
import re
import stat
from pathlib import Path

import pytest

from lhpc.core import meshtastic_tool as mt

ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# Classifier — transport selection is always refused
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("argv", [
    ["--host"], ["--host", "foo"], ["--host=foo"], ["--host=meshtastic.local:4404"],
    ["--tcp", "x"], ["--tcp=x"], ["-t"], ["-t", "x"], ["-tfoo"],
    ["--port", "/dev/ttyUSB0"], ["--serial", "/dev/ttyUSB0"], ["--serial=/dev/ttyUSB0"],
    ["-s"], ["-s", "/dev/ttyUSB0"], ["-sfoo"],
    ["--ble"], ["--ble", "name"], ["-b"], ["-b", "name"], ["--ble-scan"],
    # argparse abbreviations that uniquely resolve to a transport selector
    ["--ho", "foo"], ["--hos=foo"], ["--tc", "x"], ["--por", "/dev/x"], ["--ser", "/dev/x"],
    ["--ble-s"],
    # buried in the middle / after other args
    ["--sendtext", "hi", "--host", "evil"], ["--nodes", "-t", "evil"],
])
def test_transport_selection_is_blocked(argv):
    assert mt.classify(argv).action == "block"


# ---------------------------------------------------------------------------
# Classifier — LHPC-owned local settings are refused; everything else passes
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("argv", [
    ["--set", "lora.region", "EU_868"],
    ["--set", "lora.Region", "EU_868"],          # camelCase field
    ["--set", "position.gps_mode", "1"],
    ["--set", "position.gpsMode", "1"],          # camelCase
    ["--set-owner", "Bob"], ["--set-owner=Bob"],
    ["--set-owner-short", "BO"],
    ["--setlat", "51.5"], ["--setlon", "-0.1"], ["--setalt", "20"],
    ["--remove-position"],
    # abbreviations resolving to an owned flag
    ["--set-owner-sh", "BO"], ["--remove-p"],
    # mixed: a harmless setting BEFORE the protected one is still caught
    ["--set", "power.ls_secs", "300", "--set", "lora.region", "US"],
])
def test_owned_local_settings_are_blocked(argv):
    assert mt.classify(argv).action == "block"


@pytest.mark.parametrize("argv", [
    ["--info"], ["--nodes"], ["--listen"], ["--reply"], ["--test"], ["--tunnel"],
    ["--sendtext", "hello world"], ["--traceroute", "!12345678"],
    ["--dest", "!12345678", "--sendtext", "hello", "--ack"],
    ["--request-telemetry"], ["--request-position"], ["--reboot"], ["--shutdown"],
    ["--ch-add", "foo"], ["--ch-index", "1", "--ch-set", "psk", "random"],
    ["--get", "lora.region"],                    # a READ of an owned field is fine
    ["--set", "lora.hop_limit", "3"],            # a non-owned --set is fine
    ["--set", "power.ls_secs", "300"],
    ["--sendtext", "please set lora.region to EU"],   # value merely CONTAINS an owned name
])
def test_ordinary_commands_pass_through(argv):
    d = mt.classify(argv)
    assert d.action == "pass", d.message
    # ...and they pass through as a plain foreground exec — never a reconvergence detour.
    assert d.bulk_config is False


# ---------------------------------------------------------------------------
# Remote --dest exempts the LOCAL-node ownership guards
# ---------------------------------------------------------------------------

def test_remote_dest_exempts_owned_setting():
    assert mt.classify(["--dest", "!deadbeef", "--set", "lora.region", "EU_868"]).action == "pass"
    assert mt.classify(["--dest", "0x1234", "--set-owner", "Bob"]).action == "pass"


def test_local_and_broadcast_dest_still_guard_owned_setting():
    assert mt.classify(["--set", "lora.region", "EU"]).action == "block"          # no --dest = local
    assert mt.classify(["--dest", "^local", "--set", "lora.region", "EU"]).action == "block"
    assert mt.classify(["--dest", "^all", "--set", "lora.region", "EU"]).action == "block"


@pytest.mark.parametrize("dest", ["!ffffffff", "0xffffffff", "0xFFFFFFFF", "4294967295"])
def test_broadcast_id_dest_still_guards_owned_setting(dest):
    # The broadcast address by id/number reaches the LOCAL node too, so guards must still apply.
    assert mt.classify(["--dest", dest, "--set", "lora.region", "US"]).action == "block"


def test_last_dest_wins_like_argparse_store():
    # argparse `store` keeps the LAST --dest; a trailing LOCAL dest must re-arm the guard...
    assert mt.classify(
        ["--dest", "!deadbeef", "--dest", "^local", "--set", "lora.region", "US"]
    ).action == "block"
    # ...and a trailing REMOTE dest must exempt despite an earlier local one.
    assert mt.classify(
        ["--dest", "^local", "--dest", "!deadbeef", "--set", "lora.region", "US"]
    ).action == "pass"


def test_set_equals_form_field_is_classified():
    # `--set=FIELD` puts the field on the first token; the owned-field guard must still see it.
    assert mt.classify(["--set=lora.region", "US"]).action == "block"
    assert mt.classify(["--dest", "!deadbeef", "--set=lora.region", "US"]).action == "pass"
    assert mt.classify(["--set=telemetry.environment_measurement_enabled", "true"]).action == "pass"


# ---------------------------------------------------------------------------
# Factory reset — warn + confirm; device reset gets stronger wording
# ---------------------------------------------------------------------------

def test_factory_reset_config_confirms_without_device_wording():
    d = mt.classify(["--factory-reset"])
    assert d.action == "confirm" and not d.device_reset
    assert "BLE" not in d.message

    d2 = mt.classify(["--factory-reset-config"])
    assert d2.action == "confirm" and not d2.device_reset


def test_factory_reset_device_gets_stronger_wording():
    d = mt.classify(["--factory-reset-device"])
    assert d.action == "confirm" and d.device_reset
    assert "BLE" in d.message and "PKI" in d.message


# ---------------------------------------------------------------------------
# Managed executable resolution — LHPC-provisioned only, no PATH fallback
# ---------------------------------------------------------------------------

def _make_fake_cli(root: Path, body: str = "#!/bin/bash\nexit 0\n") -> str:
    exe = root.joinpath(*mt.MANAGED_CLI_REL)
    exe.parent.mkdir(parents=True, exist_ok=True)
    exe.write_text(body)
    exe.chmod(exe.stat().st_mode | stat.S_IXUSR)
    return str(exe)


def test_resolve_managed_cli_finds_in_root_and_ignores_absence(tmp_path):
    assert mt.resolve_managed_cli(tmp_path) is None            # not built
    exe = _make_fake_cli(tmp_path)
    assert mt.resolve_managed_cli(tmp_path) == exe             # exact in-root path


def test_resolve_managed_cli_never_uses_path(tmp_path, monkeypatch):
    # Even with an unmanaged `meshtastic` on PATH, resolution stays None when unbuilt.
    fake_bin = tmp_path / "pathbin"
    fake_bin.mkdir()
    (fake_bin / "meshtastic").write_text("#!/bin/bash\n")
    (fake_bin / "meshtastic").chmod(0o755)
    monkeypatch.setenv("PATH", f"{fake_bin}:{os.environ.get('PATH', '')}")
    assert mt.resolve_managed_cli(tmp_path) is None


# ---------------------------------------------------------------------------
# Wrapper option handling
# ---------------------------------------------------------------------------

def test_split_wrapper_args_extracts_yes_only():
    yes, fwd = mt.split_wrapper_args(["--factory-reset", "--yes"])
    assert yes and fwd == ["--factory-reset"]
    yes2, fwd2 = mt.split_wrapper_args(["--info"])
    assert not yes2 and fwd2 == ["--info"]


def test_yes_after_double_dash_is_data_not_flag():
    # `--` ends option parsing; a `--yes` past it is a literal value and must be forwarded verbatim.
    yes, fwd = mt.split_wrapper_args(["--sendtext", "--", "--yes"])
    assert not yes and fwd == ["--sendtext", "--", "--yes"]
    # A leading `--yes` (before `--`) is still the wrapper flag.
    yes2, fwd2 = mt.split_wrapper_args(["--yes", "--factory-reset"])
    assert yes2 and fwd2 == ["--factory-reset"]


@pytest.mark.parametrize("argv,needs", [
    (["--version"], False), (["--ver"], False), (["--hel"], False),
    (["-h"], False), ([], False),
    # --support (prints + exits) and --test (its own USB serial test) create no LHPC-node interface.
    (["--support"], False), (["--sup"], False), (["--test"], False),
    # A terminal no-node action makes the WHOLE command node-free even with modifiers — upstream
    # exits before building an interface, so --debug / a trailing --version never reach a node op.
    (["--test", "--debug"], False), (["--support", "--debug"], False),
    (["--version", "--info"], False), (["--reply", "--version"], False),
    (["--info"], True), (["--reply"], True), (["--tunnel"], True),   # real connected-interface ops
])
def test_needs_node_treats_any_no_node_action_as_terminal(argv, needs):
    assert mt._needs_node(argv) is needs


def test_support_and_test_skip_readiness_and_forced_host(tmp_path, captured_exec):
    # --support/--test must run with the stack STOPPED and WITHOUT a forced --host — with modifiers too.
    _make_fake_cli(tmp_path)
    for a in (["--support"], ["--test"], ["--test", "--debug"], ["--support", "--debug"]):
        captured_exec.clear()
        _run(a, root=tmp_path, running=False, box=captured_exec)
        assert captured_exec["argv"][1:] == a                 # no forced --host, no readiness gate


# ---------------------------------------------------------------------------
# run() — execution path (execv captured, no node needed)
# ---------------------------------------------------------------------------

class _Captured(Exception):
    pass


@pytest.fixture
def captured_exec(monkeypatch):
    """Capture the argv run() would exec, instead of replacing the process."""
    box = {}

    def fake_execv(exe, argv):
        box["exe"], box["argv"] = exe, argv
        raise _Captured

    monkeypatch.setattr(mt.os, "execv", fake_execv)
    return box


def _run(argv, *, root, running=True, confirm=lambda _p: True, box=None, reconverge=None):
    import io
    out, err = io.StringIO(), io.StringIO()
    try:
        rc = mt.run(argv, runtime_root=root, stack_running=lambda _s: running,
                    confirm=confirm, reconverge=reconverge, out=out, err=err)
    except _Captured:
        return None, out.getvalue(), err.getvalue()
    return rc, out.getvalue(), err.getvalue()


def test_passthrough_forces_local_host_exactly_once(tmp_path, captured_exec):
    _make_fake_cli(tmp_path)
    _run(["--nodes"], root=tmp_path, box=captured_exec)
    argv = captured_exec["argv"]
    # --host is PREPENDED (before forwarded args) so a user `--` cannot demote it to a positional.
    assert argv[1:] == ["--host", mt.LOCAL_API, "--nodes"]
    assert argv.count("--host") == 1


def test_forced_host_precedes_user_double_dash(tmp_path, captured_exec):
    _make_fake_cli(tmp_path)
    _run(["--sendtext", "hi", "--", "positional"], root=tmp_path, box=captured_exec)
    argv = captured_exec["argv"]
    # The forced --host must appear BEFORE the `--` marker, else upstream treats it as positional.
    assert argv.index("--host") < argv.index("--")
    assert argv[1:3] == ["--host", mt.LOCAL_API]


def test_forced_host_targets_local_api_port():
    # The forced transport must be the manifest's local endpoint; upstream splits host:port itself.
    assert mt.LOCAL_API == "127.0.0.1:4403"


def test_set_value_beginning_with_dash_is_not_reclassified(tmp_path, captured_exec):
    # A non-owned --set whose VALUE looks like an option must not be re-inspected (off-by-one guard).
    _make_fake_cli(tmp_path)
    for value in ("-s", "--factory-reset", "--host"):
        captured_exec.clear()
        rc, _out, _err = _run(["--set", "device.role", value], root=tmp_path, box=captured_exec)
        assert rc is None and "argv" in captured_exec        # execs, not blocked/confirmed


def test_set_fixed_position_is_owned():
    assert mt.classify(["--set", "position.fixed_position", "true"]).action == "block"
    assert mt.classify(["--set=position.fixed_position", "false"]).action == "block"


def test_set_ham_is_identity_owned():
    # --set-ham sets a licensed callsign as the node OWNER (LHPC-owned name) — block locally,
    # exempt for a remote --dest (same as the other owned flags).
    assert mt.classify(["--set-ham", "N0CALL"]).action == "block"
    assert mt.classify(["--dest", "!deadbeef", "--set-ham", "N0CALL"]).action == "pass"


def test_blocked_transport_returns_2_and_does_not_exec(tmp_path, captured_exec):
    _make_fake_cli(tmp_path)
    rc, _out, err = _run(["--host", "evil"], root=tmp_path, box=captured_exec)
    assert rc == 2 and "argv" not in captured_exec and "transport" in err.lower()


def test_owned_setting_returns_2_with_guidance(tmp_path, captured_exec):
    _make_fake_cli(tmp_path)
    rc, _out, err = _run(["--set", "lora.region", "EU_868"], root=tmp_path, box=captured_exec)
    assert rc == 2 and "lhpc config meshtastic region" in err


def test_node_op_refused_when_stack_not_running(tmp_path, captured_exec):
    _make_fake_cli(tmp_path)
    rc, _out, err = _run(["--nodes"], root=tmp_path, running=False, box=captured_exec)
    assert rc == 1 and "not running" in err and "argv" not in captured_exec


def test_help_and_version_skip_readiness(tmp_path, captured_exec):
    _make_fake_cli(tmp_path)
    for a in (["--help"], ["--version"], []):
        captured_exec.clear()
        _run(a, root=tmp_path, running=False, box=captured_exec)   # not running, still execs
        assert captured_exec["argv"][1:] == a                      # no forced --host for help/version


def test_missing_managed_cli_reports_build_hint(tmp_path):
    rc, _out, err = _run(["--info"], root=tmp_path)                 # nothing built
    assert rc == 1 and "lhpc build meshtastic" in err


def test_factory_reset_confirmation_flow(tmp_path, captured_exec):
    _make_fake_cli(tmp_path)
    # interactive NO -> no exec
    captured_exec.clear()
    rc, _out, _err = _run(["--factory-reset"], root=tmp_path, confirm=lambda _p: False,
                          box=captured_exec)
    assert rc == 1 and "argv" not in captured_exec
    # interactive YES -> exec once
    captured_exec.clear()
    _run(["--factory-reset"], root=tmp_path, confirm=lambda _p: True, box=captured_exec)
    assert captured_exec["argv"][1:] == ["--host", mt.LOCAL_API, "--factory-reset"]


def test_factory_reset_yes_flag_executes_and_is_not_forwarded(tmp_path, captured_exec):
    _make_fake_cli(tmp_path)

    def _never(_p):
        raise AssertionError("--yes must skip the prompt")

    _run(["--factory-reset", "--yes"], root=tmp_path, confirm=_never, box=captured_exec)
    assert "--yes" not in captured_exec["argv"]                     # wrapper flag, never upstream
    assert captured_exec["argv"][1:] == ["--host", mt.LOCAL_API, "--factory-reset"]


def test_factory_reset_noninteractive_without_yes_is_refused(tmp_path, captured_exec):
    _make_fake_cli(tmp_path)
    # EOF/closed stdin -> the real _confirm returns False; here confirm returns False.
    rc, _out, _err = _run(["--factory-reset-device"], root=tmp_path, confirm=lambda _p: False,
                          box=captured_exec)
    assert rc == 1 and "argv" not in captured_exec


def test_exit_code_preserved_via_subprocess_fallback(tmp_path, monkeypatch):
    # If execv is unavailable, run() falls back to a stdio-inheriting subprocess whose exit code
    # is returned unchanged. Use a fake CLI that exits 7.
    _make_fake_cli(tmp_path, "#!/bin/bash\nexit 7\n")
    monkeypatch.setattr(mt.os, "execv", lambda *_a: (_ for _ in ()).throw(OSError("no execv")))
    rc, _out, _err = _run(["--nodes"], root=tmp_path)
    assert rc == 7


@pytest.mark.parametrize("argv", [
    ["--ch-add", "Foo"], ["--ch-set", "psk", "random"],
    ["--ch-index", "1", "--ch-set", "name", "foo"],
])
def test_exact_channel_siblings_do_not_reconverge(argv):
    # Exact --ch-add / --ch-set are distinct commands (no full LoRa config) — plain pass-through.
    assert mt.classify(argv).bulk_config is False


@pytest.mark.parametrize("flag", ["--ch-add-url", "--ch-set-url", "--seturl",
                                  "--ch-add-u", "--seturl"])   # -url variants + a unique abbreviation
def test_url_setters_and_unique_abbreviations_reconverge(flag):
    assert mt.classify([flag, "x"]).bulk_config is True


@pytest.mark.parametrize("flag", ["--configure", "--import-config", "--seturl", "--ch-set-url",
                                  "--ch-add-url"])
def test_broad_local_mutator_triggers_reconvergence(tmp_path, monkeypatch, flag):
    # Every broad LOCAL mutator (incl. the channel-URL setters) must auto-reassert LHPC settings.
    _make_fake_cli(tmp_path)
    calls = {}

    class _R:
        returncode = 0

    def fake_run(argv, check=False):
        calls["argv"] = argv
        return _R()

    monkeypatch.setattr(mt.subprocess, "run", fake_run)
    reconv = {"n": 0}

    def _reconv():
        reconv["n"] += 1
        return True

    rc, out, _err = _run([flag, "x"], root=tmp_path, reconverge=_reconv)
    assert rc == 0
    assert reconv["n"] == 1                                       # reconvergence actually ran
    assert calls["argv"][1:3] == ["--host", mt.LOCAL_API]         # prepended forced transport
    assert "Reasserted LHPC-managed settings" in out


@pytest.mark.parametrize("code", [0, 3])
def test_reconvergence_runs_on_any_exit_code(tmp_path, monkeypatch, code):
    _make_fake_cli(tmp_path)

    class _R:
        returncode = code

    monkeypatch.setattr(mt.subprocess, "run", lambda argv, check=False: _R())
    reconv = {"n": 0}
    rc, _out, _err = _run(["--configure", "c"], root=tmp_path,
                          reconverge=lambda: (reconv.__setitem__("n", reconv["n"] + 1), True)[1])
    assert rc == code and reconv["n"] == 1                        # original rc preserved, reconverged


def test_reconvergence_runs_on_keyboardinterrupt(tmp_path, monkeypatch):
    # Ctrl-C mid-import may leave a partial mutation — reconvergence must still run, and the
    # interruption surfaces as a clean rc 130 (no traceback), not a re-raised exception.
    _make_fake_cli(tmp_path)

    def boom(argv, check=False):
        raise KeyboardInterrupt

    monkeypatch.setattr(mt.subprocess, "run", boom)
    reconv = {"n": 0}
    rc, _out, _err = _run(["--import-config", "c"], root=tmp_path,
                          reconverge=lambda: (reconv.__setitem__("n", reconv["n"] + 1), True)[1])
    assert rc == 130 and reconv["n"] == 1


def test_remote_dest_bulk_does_not_reconverge(tmp_path, monkeypatch, captured_exec):
    # A broad mutator aimed at a REMOTE node must NOT reconverge the local node (execs, no bulk path).
    _make_fake_cli(tmp_path)
    reconv = {"n": 0}
    _run(["--dest", "!deadbeef", "--seturl", "x"], root=tmp_path, box=captured_exec,
         reconverge=lambda: (reconv.__setitem__("n", reconv["n"] + 1), True)[1])
    assert reconv["n"] == 0 and "argv" in captured_exec          # foreground exec, no reconvergence


def _fake_run_rc(monkeypatch, code):
    class _R:
        returncode = code
    monkeypatch.setattr(mt.subprocess, "run", lambda argv, check=False: _R())


def test_missing_reconverge_callable_is_not_a_false_green(tmp_path, monkeypatch):
    # upstream 0 but no reconvergence hook -> LHPC cannot prove reassert -> NONZERO (not 0).
    _make_fake_cli(tmp_path)
    _fake_run_rc(monkeypatch, 0)
    rc, _out, err = _run(["--configure", "c"], root=tmp_path, reconverge=None)
    assert rc == 1 and "lhpc stack poststart meshtastic" in err


def test_upstream_0_reconverge_false_returns_nonzero(tmp_path, monkeypatch):
    _make_fake_cli(tmp_path)
    _fake_run_rc(monkeypatch, 0)
    rc, _out, err = _run(["--configure", "c"], root=tmp_path, reconverge=lambda: False)
    assert rc == 1 and "could NOT verify" in err


def test_upstream_0_reconverge_exception_returns_nonzero(tmp_path, monkeypatch):
    _make_fake_cli(tmp_path)
    _fake_run_rc(monkeypatch, 0)

    def _boom():
        raise RuntimeError("poststart blew up")

    rc, _out, _err = _run(["--configure", "c"], root=tmp_path, reconverge=_boom)
    assert rc == 1                                        # exception in reconvergence != success


def test_upstream_nonzero_preserved_even_when_reconverge_fails(tmp_path, monkeypatch):
    # A failed upstream import keeps ITS rc; reconvergence is still attempted but does not override it.
    _make_fake_cli(tmp_path)
    _fake_run_rc(monkeypatch, 3)
    calls = {"n": 0}

    def _reconv():
        calls["n"] += 1
        return False

    rc, _out, _err = _run(["--configure", "c"], root=tmp_path, reconverge=_reconv)
    assert rc == 3 and calls["n"] == 1                    # rc 3 preserved, reconvergence attempted


def test_keyboardinterrupt_during_reconvergence_is_clean_130(tmp_path, monkeypatch):
    # Ctrl-C DURING reconvergence (not just the mutator) -> clean rc 130, no traceback escapes.
    _make_fake_cli(tmp_path)
    _fake_run_rc(monkeypatch, 0)

    def _ki():
        raise KeyboardInterrupt

    rc, _out, _err = _run(["--configure", "c"], root=tmp_path, reconverge=_ki)
    assert rc == 130


# ---------------------------------------------------------------------------
# Version-drift protection
# ---------------------------------------------------------------------------

def test_policy_version_matches_manifest_pin():
    manifest = (ROOT / "lhpc/data/manifest.example.toml").read_text()
    pins = set(re.findall(r"meshtastic==([0-9][^\"'\s]+)", manifest))
    assert mt.MESHTASTIC_CLI_VERSION in pins, (
        f"meshtastic_tool policy is pinned to {mt.MESHTASTIC_CLI_VERSION} but the manifest installs "
        f"{pins}; on an upstream bump re-review the transport / owned-setting / factory-reset "
        f"aliases, then update MESHTASTIC_CLI_VERSION."
    )


# ---------------------------------------------------------------------------
# Integration with the REAL pinned CLI (skipped if not importable locally)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("flag", ["--version", "--help"])
def test_real_managed_cli_help_version(tmp_path, flag):
    import shutil
    import subprocess

    real = shutil.which("meshtastic")
    if real is None:
        pytest.skip("meshtastic CLI not installed in this environment")
    # Point the managed path at the real CLI and run help/version through the wrapper's exec path.
    exe = tmp_path.joinpath(*mt.MANAGED_CLI_REL)
    exe.parent.mkdir(parents=True, exist_ok=True)
    exe.symlink_to(real)
    proc = subprocess.run(
        [os.sys.executable, "-c",
         "import sys; from lhpc.core import meshtastic_tool as m; "
         "sys.exit(m.run(sys.argv[1:], runtime_root=sys.argv.pop(1), "
         "stack_running=lambda s: True, confirm=lambda p: True))",
         str(tmp_path), flag],
        capture_output=True, text=True, check=False, env={**os.environ, "PYTHONPATH": str(ROOT)},
    )
    assert proc.returncode == 0
    assert "meshtastic" in (proc.stdout + proc.stderr).lower()


# ---------------------------------------------------------------------------
# CLI intercept — `lhpc meshtastic ...` is routed BEFORE the main argparse parser
# ---------------------------------------------------------------------------

def test_main_run_intercepts_meshtastic_before_parser(monkeypatch):
    """`_run(["meshtastic", ...])` must hand the tail (raw[1:]) to meshtastic_tool.run verbatim,
    never letting argparse claim upstream flags like --help / --version / unknown options.

    `_cmd_meshtastic` imports the module locally (`from lhpc.core import meshtastic_tool`), so the
    patch target is the module object's `run` attribute (same object as `mt`), not a cli-main attr.
    """
    from lhpc.adapters.cli import main as cli_main

    seen = {}

    def fake_run(passthrough, **kwargs):
        seen["passthrough"] = passthrough
        seen["kwargs"] = kwargs
        return 0

    monkeypatch.setattr(mt, "run", fake_run)
    # --help would normally be claimed by the top-level parser; it must reach the passthrough.
    rc = cli_main._run(["meshtastic", "--help", "--nodes", "--unknown-upstream-flag"])
    assert rc == 0
    assert seen["passthrough"] == ["--help", "--nodes", "--unknown-upstream-flag"]
    # The reconverge callback MUST be wired (P1 depends on it) and be callable.
    assert set(seen["kwargs"]) >= {"runtime_root", "stack_running", "confirm", "reconverge"}
    assert callable(seen["kwargs"]["reconverge"])


def test_main_run_bare_meshtastic_passes_empty_tail(monkeypatch):
    from lhpc.adapters.cli import main as cli_main

    seen = {}
    monkeypatch.setattr(mt, "run",
                        lambda passthrough, **kw: seen.setdefault("p", passthrough) or 0)
    assert cli_main._run(["meshtastic"]) == 0
    assert seen["p"] == []
