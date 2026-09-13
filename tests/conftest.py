"""Shared test fixtures."""

from __future__ import annotations

import os
import subprocess

import pytest


def pytest_collection_modifyitems(config, items):
    # Skip (with a reason) in degenerate environments so the product's CORRECT strictness
    # (sid>0 identity, non-root perm fixtures) is not misread as a code failure. Never fires on a
    # normal desktop or the Raspberry Pi target (sid>0, non-root).
    import shutil
    no_session = os.getsid(0) == 0
    is_root = os.geteuid() == 0
    no_zstd = shutil.which("zstd") is None
    for it in items:
        if no_session and it.get_closest_marker("needs_session"):
            it.add_marker(pytest.mark.skip(
                reason="no real POSIX session (sid==0); run under `setsid` (identity_complete needs sid>0)"))
        if is_root and it.get_closest_marker("needs_nonroot"):
            it.add_marker(pytest.mark.skip(
                reason="running as root; the chmod permission fixture does not bind for root"))
        if no_zstd and it.get_closest_marker("requires_zstd"):
            it.add_marker(pytest.mark.skip(
                reason="host `zstd` binary not installed; the extraction boundary cannot be crossed "
                       "(install it with: sudo apt install -y zstd)"))

from lhpc.core.services import ControllerService
from lhpc.core.lifecycle import Lifecycle


@pytest.fixture(autouse=True)
def _isolated_runtime_root(monkeypatch, tmp_path_factory):
    """HERMETIC: never let a test resolve the operator's REAL deployment root. Tests that call the
    CLI `main()` without constructing Paths themselves fall back to the LHPC_RUNTIME_ROOT /
    ~/loraham-pi-control default — on a developer box that is a LIVE deployment, and its state (a
    running auto-install's admission markers, update requests, …) leaked straight into assertions
    (live find: the whole suite went red while an auto-install ran on the same machine). Point the
    env default at a fresh per-test directory; tests that pass explicit Paths are unaffected."""
    monkeypatch.setenv("LHPC_RUNTIME_ROOT", str(tmp_path_factory.mktemp("ambient-root")))
    # HERMETIC too: GitHub's hosted runners execute under systemd, which sets INVOCATION_ID
    # ambiently — flipping every CLI-context test into the managed-unit/web branches (live find:
    # four webserver-apply failures on every CI leg while the same suite was green on the Pi).
    # Tests that exercise the managed context set INVOCATION_ID explicitly and are unaffected.
    monkeypatch.delenv("INVOCATION_ID", raising=False)


@pytest.fixture(autouse=True)
def _no_binary_network(monkeypatch):
    """HERMETIC: the binary channel is the first lhpc feature that can fetch over HTTPS, and the
    CLI/web defaults route the three heavy stacks through it. A test that reaches the real
    release would be slow, flaky and dependent on what is published RIGHT NOW — fail loudly
    instead. Tests that exercise the transaction stub `_http_get` themselves (their monkeypatch
    runs after this fixture and wins)."""
    from lhpc.core import binary_install as _bi

    def _refuse(url, *_a, **_k):
        raise AssertionError(
            f"test attempted a real binary-channel download: {url} — stub "
            "lhpc.core.binary_install._http_get / _open_stream, or pick a source channel")

    # BOTH doors: `_http_get` fetches the index, `_open_stream` streams the asset itself.
    monkeypatch.setattr(_bi, "_http_get", _refuse)
    monkeypatch.setattr(_bi, "_open_stream", _refuse)


@pytest.fixture(autouse=True)
def _no_pip_install(monkeypatch):
    """A test must never install into the developer's venv.

    `self-update` really runs `<python> -m pip install -e <root>` after an advance. A test that
    lets that through installs a throwaway package from a pytest tmp dir into the shared venv and
    breaks the NEXT run, not its own: the editable finder then points `lhpc` at a directory that
    no longer exists, and 4,500 tests stop collecting with a FileNotFoundError that names /tmp and
    never names pip. Same shape as `_no_binary_network` below: turn a silent poisoning into a loud
    failure in the test that causes it.
    """
    from lhpc.core.probes.backends import RealCommandRunner
    real = RealCommandRunner.run

    def guarded(self, argv, timeout=None, *a, **kw):
        av = [str(x) for x in argv]
        if "pip" in " ".join(av[:3]) and "install" in av:
            raise AssertionError(
                f"a test tried to install into the developer's venv: {av!r} — stub the runner")
        return real(self, argv, timeout, *a, **kw)

    monkeypatch.setattr(RealCommandRunner, "run", guarded)


@pytest.fixture(autouse=True)
def _no_shell_execution():
    """SAFETY: nothing LHPC runs may go through a shell.

    Every launch, build, test and web job is structured argv with `shell=False`, so a validated
    operator value can never merge with an option, change the executable, or become shell syntax.
    This watches the real `subprocess` entry points for the WHOLE suite, so any driven flow that
    started passing a truthy `shell=` fails in the test that reached it — a behavioural check that
    a source scan of three chosen modules could not make, and that no comment or refusal message
    can trip. The other half of the invariant (the argv LHPC actually spawns is the real
    executable, never a shell) is owned by
    `tests/core/test_structured_exec.py::test_started_process_argv_is_not_a_shell`.
    """
    import subprocess
    originals = {n: getattr(subprocess, n) for n in
                 ("Popen", "run", "call", "check_call", "check_output")}

    def guarded(name, fn):
        def wrapper(*args, **kwargs):
            if kwargs.get("shell"):
                raise AssertionError(
                    f"subprocess.{name} was called with shell={kwargs['shell']!r} — LHPC runs "
                    "structured argv with shell=False, never a shell")
            return fn(*args, **kwargs)
        return wrapper

    for name, fn in originals.items():
        setattr(subprocess, name, guarded(name, fn))
    try:
        yield
    finally:
        for name, fn in originals.items():
            setattr(subprocess, name, fn)


@pytest.fixture(autouse=True)
def _fw_host_isolation(monkeypatch, tmp_path_factory):
    """HERMETIC: the managed-firewall READ side consults HOST-GLOBAL paths (/etc/lhpc,
    /etc/systemd/system wants, /run/lhpc-firewall/check.json). On a box where the operator has
    actually applied the firewall those artifacts exist, every test service reads integration
    'present', and the exposure gate goes red across the whole suite (live find: 24 failures the
    hour the firewall was installed on the dev Pi). Default the class-level readers to the
    fresh-machine answer and point the receipt at an empty per-test path; firewall tests that
    exercise these functions stub them per-INSTANCE (or pass explicit paths) and are unaffected."""
    from lhpc.core import firewall as fwm
    from lhpc.core.services import ControllerService
    monkeypatch.setattr(ControllerService, "_fw_integration_state", lambda self: "absent")
    monkeypatch.setattr(ControllerService, "_fw_units_enabled", lambda self: False)
    monkeypatch.setattr(fwm, "RECEIPT_PATH",
                        str(tmp_path_factory.mktemp("fw-iso") / "check.json"))


@pytest.fixture(autouse=True)
def _home_isolation(monkeypatch, tmp_path_factory):
    """HERMETIC: HOME is host-global, and `_user_unit_dir()` resolves `~/.config/systemd/user` from
    it. So the managed-unit verdict depended on whether the developer had lhpc installed: on this
    Pi the real units point at the real root, read FOREIGN against a temp runtime root, and the
    post-update refresh returned "not this deployment's — left untouched" (ok); on a CI runner with
    no units at all it ran the repair and failed. Three self-update tests passed here and failed
    there for that reason alone — and nothing in the suite said so.

    Point HOME at an empty per-test directory: the fresh-machine answer, identical everywhere.
    Tests that need a populated HOME set it themselves (their monkeypatch runs after this one and
    wins), which is why this redirects HOME rather than patching `_user_unit_dir` — patching the
    method would have overridden those tests' own redirect."""
    monkeypatch.setenv("HOME", str(tmp_path_factory.mktemp("home-iso")))


@pytest.fixture(autouse=True)
def _default_display(request, monkeypatch):
    """A graphical session is part of the working-box BASELINE, like the radios above.

    `display_available()` globs for a live compositor socket (`/run/user/<uid>/wayland-*`,
    `/tmp/.X11-unix/X*`) — real host evidence, outside the injected System. So a GUI-capable
    component (`loraham-voice`, `sideband`) STARTED on the developer's desktop Pi and came back
    SKIPPED("needs a graphical session") on a headless CI runner: three tests asserted a start
    outcome here and got a skip there. Default it to present; tests about the detection itself
    take @pytest.mark.no_default_display."""
    if request.node.get_closest_marker("no_default_display"):
        return
    from lhpc.core.services import ControllerService
    monkeypatch.setattr(ControllerService, "display_available", staticmethod(lambda: True))


@pytest.fixture(autouse=True)
def _default_hardware(request, monkeypatch):
    """A fresh install has NO radio hardware configured (the daemon refuses to start), but nearly every
    test exercises a working box. So the test BASELINE defaults to the LoRaHAM dual-radio setup — i.e.
    a `[radio]`-less runtime resolves to 'loraham' — mirroring a deployed unit. Tests about the
    unconfigured state opt out with @pytest.mark.no_default_hardware (then the true 'unset' default
    applies). A test that writes its own `[radio].hardware` overrides this either way."""
    if request.node.get_closest_marker("no_default_hardware"):
        return
    from lhpc.core import config as _config
    monkeypatch.setattr(_config, "HW_DEFAULT", "loraham", raising=False)


@pytest.fixture
def set_call():
    """Configure a licensed callsign — as a FIXTURE, so no test module imports another."""
    return _set_call


def _set_call(svc, callsign="XX0XXA"):
    """Configure a valid operator callsign so a LICENSED stack (chat/graywolf/voice/meshcom) passes
    CALL-enforcement — the realistic precondition for starting one. Returns the service."""
    from lhpc.core.config import save_operator_config
    save_operator_config(svc._paths, callsign)
    svc._invalidate_config()
    return svc

# Real-but-harmless spawn shim: ownership recording now requires a COMPLETE /proc
# identity, so tests that "start" something must spawn a real process (a detached
# `sleep`) rather than a fake pid. All spawned sleepers are reaped at session end.
_SPAWNED: list = []


@pytest.fixture
def real_spawn():
    """The real-spawn shim as a FIXTURE, so a test in any directory gets it without importing
    another test module (see tests/README.md: a test module never imports a test module)."""
    return _real_spawn


def _real_spawn(argv, log, cwd=None, env=None):
    """A `spawn` callable for Lifecycle that launches a real detached `sleep` (its own
    session, so it is an LHPC-ownable session leader) and returns its pid. The log path
    is created so callers that read it work."""
    try:
        open(str(log), "a").close()
    except OSError:
        pass
    p = subprocess.Popen(["sleep", "300"], start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    _SPAWNED.append(p)
    return p.pid


@pytest.fixture(autouse=True)
def _reap_real_spawns():
    yield
    while _SPAWNED:
        p = _SPAWNED.pop()
        try:
            p.kill(); p.wait(timeout=2)
        except Exception:
            pass


@pytest.fixture(autouse=True)
def _no_daemon_verify_wait(monkeypatch):
    """The daemon-start CONF-socket verification waits seconds in production; the
    FakeSystem never simulates the daemon coming up, so disable the wait in tests
    (the start path is still exercised; it just reports success immediately).

    Also disable the post-launch identity-observation wait by default: most tests
    inject a fake spawn whose pid is not a real /proc process. Tests that exercise
    the real ownership/identity path set OBSERVE_TIMEOUT_S explicitly."""
    monkeypatch.setattr(ControllerService, "DAEMON_VERIFY_TIMEOUT_S", 0.0)
    monkeypatch.setattr(ControllerService, "ENDPOINT_VERIFY_TIMEOUT_S", 0.0)
    monkeypatch.setattr(Lifecycle, "OBSERVE_TIMEOUT_S", 0.0)


# --- session-wide propagation of unhandled THREAD exceptions -------------------------------------
# An exception in a thread's run() is otherwise only a PytestUnhandledThreadExceptionWarning that never
# fails CI. Record every such exception and FAIL the session at the end (with thread + traceback), so a
# leaked/racy test thread is a hard failure, not a warning. It does NOT suppress or filter the warning.
import threading as _threading  # noqa: E402
import traceback as _traceback  # noqa: E402

_THREAD_EXCEPTIONS: list = []
_prev_thread_hook = _threading.excepthook


def _record_thread_exception(args):  # noqa: ANN001
    _THREAD_EXCEPTIONS.append(
        "".join(_traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback))
        + f"(thread: {getattr(args.thread, 'name', '?')})")
    if _prev_thread_hook is not None:
        _prev_thread_hook(args)


_threading.excepthook = _record_thread_exception


def pytest_sessionfinish(session, exitstatus):  # noqa: ANN001
    if _THREAD_EXCEPTIONS:
        tr = session.config.pluginmanager.get_plugin("terminalreporter")
        if tr is not None:
            tr.write_line(f"\nFAIL: {len(_THREAD_EXCEPTIONS)} unhandled thread exception(s):\n"
                          + "\n---\n".join(_THREAD_EXCEPTIONS))
        session.exitstatus = 1


@pytest.fixture(autouse=True)
def _no_host_gpsd(monkeypatch):
    """HERMETIC: the `auto` GPS source probes the HOST's /proc/net/tcp for a localhost gpsd.
    On a dev box that happens to run one, every default-config test would resolve auto->gpsd
    and admit feeds/claims the test never asked for. Pin the probe to the fresh-machine
    answer; tests exercising auto resolution monkeypatch `lhpc.core.gps.local_gpsd_listening`
    themselves (the memo lives inside the real function, so patching here bypasses it too)."""
    from lhpc.core import gps as gps_mod
    monkeypatch.setattr(gps_mod, "local_gpsd_listening", lambda: False)


@pytest.fixture
def manifest_with_moved_input():
    """The shipped manifest's text with one `build_inputs` entry moved — the way a bump does it:
    the recorded `value` and the token the consuming step renders move together
    (`drift=False`), or only the recipe's token moves and the recorded value stays behind
    (`drift=True`, which the loader must refuse). The current values are read from the manifest,
    never written into a test, so a routine pin bump changes nothing here. Returns
    `(text, old_value)`; with `decoy=True` a step carrying the OLD token is added right after
    the drifted step, in another command, to prove it cannot stand in for the real one."""
    import re as _re
    import tomllib as _tomllib

    from lhpc.core.manifest import default_manifest_path

    def _apply(cid, name, new_value, *, drift=False, decoy=False):
        text = default_manifest_path().read_text()
        entry = None
        for stack in _tomllib.loads(text)["stack"]:
            for comp in stack.get("component", []):
                if comp.get("id") != cid:
                    continue
                for item in comp.get("build_inputs", []) or []:
                    if item.get("name") == name:
                        entry = item
        assert entry is not None, f"component {cid!r} has no build input named {name!r}"
        old = str(entry["value"])
        if new_value is None:                    # a value that merely STARTS with the recorded one
            new_value = old + "0"
        rendered, moved = entry["token"].replace("{value}", old), entry["token"].replace("{value}", new_value)
        out, hits = [], 0
        for line in text.splitlines(keepends=True):
            if "argv" in line and entry["command"] in line and f'"{rendered}"' in line:
                line = line.replace(f'"{rendered}"', f'"{moved}"')
                hits += 1
                if decoy:
                    indent = line[:len(line) - len(line.lstrip())]
                    out.append(line)
                    line = f'{indent}{{ argv = ["printf", "{rendered}"] }},\n'
            elif not drift and f'name = "{name}"' in line:
                line, n = _re.subn(r'value = "%s"' % _re.escape(old), f'value = "{new_value}"', line, count=1)
                assert n == 1, line
            out.append(line)
        assert hits == 1, f"the step consuming {name!r} must render its token exactly once ({hits})"
        return "".join(out), old
    return _apply

