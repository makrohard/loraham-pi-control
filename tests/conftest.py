"""Shared test fixtures."""

from __future__ import annotations

import os
import socket
import subprocess
import threading
import time

import pytest


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "needs_session: requires a real POSIX session (sid>0) — the product's "
                   "procident.identity_complete refuses sid==0, so these tests fail in a sandbox "
                   "whose processes have session id 0 (run them under `setsid`).")
    config.addinivalue_line(
        "markers", "needs_nonroot: requires a non-root euid — a chmod-based permission fixture "
                   "does not bind for root.")
    config.addinivalue_line(
        "markers", "no_default_hardware: opt OUT of the test-baseline hardware setup so the test sees "
                   "the true fresh-install default (no radio hardware configured).")
    config.addinivalue_line(
        "markers", "no_default_display: opt OUT of the test-baseline graphical session, so the test "
                   "sees the real compositor-socket detection (headless).")
    config.addinivalue_line(
        "markers", "slow: a genuinely slow test (real bash sub-process building a real venv, or a "
                   "timed retry loop). Excluded by the fast lane `-m 'not slow'`; always run by the "
                   "complete coverage gate.")
    config.addinivalue_line(
        "markers", "requires_zstd: crosses the production extraction boundary (`zstd -dc`), so it "
                   "needs the host `zstd` binary. Skipped (with reason) where it is absent; the target "
                   "and CI install it, so it normally runs.")
    config.addinivalue_line(
        "markers", "contract: Tier 0 — the readable core, one lane that states what LHPC PROMISES. "
                   "Each tagged case goes through the widest public seam (CLI verb / Flask route / "
                   "typed ActionResult) and expresses a happy path or the refusal that defines a "
                   "boundary. Run it with `-m contract`; it must be green and quick.")
    config.addinivalue_line(
        "markers", "safety(id): a contract case that guards a named SAFETY invariant. The id is the "
                   "docs/hardening-0.1.md P0.x/P1.x where one exists (P0.5 uninstall, P0.6 GET-no-"
                   "network), else a descriptive slug (RF-TX-opt-in / firewall-fail-closed / "
                   "exposure-fail-closed). Run the invariant set with `-m safety`.")


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

    def _refuse(url, max_bytes):
        raise AssertionError(
            f"test attempted a real binary-channel download: {url} — stub "
            "lhpc.core.binary_install._http_get or pick a source channel")
    monkeypatch.setattr(_bi, "_http_get", _refuse)


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


# --- fake gpsd -----------------------------------------------------------------------------
# Shared because two modules need it (gps + doctor). It lives HERE, not in a test module, so
# nothing has to import one test module from another: `from tests.test_gps import ...` only
# resolved because the local lane runs `python -m pytest`, which puts the working directory on
# sys.path. CI runs the `pytest` console script, which does not — so 0.1.8 collected fine on
# both boxes and died on collection in CI.

class _FakeGpsd:
    """A minimal gpsd: answers ?DEVICES with a device list and streams NMEA after ?WATCH."""

    def __init__(self, devices=(), sentences=(), close_after=None):
        self.devices = list(devices)
        self.sentences = list(sentences)
        self.close_after = close_after
        self._srv = socket.socket()
        self._srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._srv.bind(("127.0.0.1", 0))
        self._srv.listen(4)
        self.port = self._srv.getsockname()[1]
        self._stop = threading.Event()
        self._t = threading.Thread(target=self._serve, daemon=True)
        self._t.start()

    def _serve(self):
        import json
        while not self._stop.is_set():
            try:
                self._srv.settimeout(0.3)
                conn, _ = self._srv.accept()
            except (TimeoutError, OSError):
                continue
            with conn:
                conn.settimeout(1.0)
                try:
                    req = conn.recv(4096)
                except (TimeoutError, OSError):
                    req = b""
                if b"?DEVICES" in req:
                    conn.sendall(json.dumps(
                        {"class": "DEVICES",
                         "devices": [{"path": p} for p in self.devices]}).encode() + b"\n")
                    continue
                sent = 0
                while not self._stop.is_set():
                    for s in self.sentences:
                        try:
                            conn.sendall(s.encode() + b"\r\n")
                        except OSError:
                            return
                        sent += 1
                        if self.close_after and sent >= self.close_after:
                            return
                    time.sleep(0.05)

    def close(self):
        self._stop.set()
        try:
            self._srv.close()
        except OSError:
            pass


@pytest.fixture
def fake_gpsd():
    """Factory for a minimal in-process gpsd. Every server it hands out is closed at teardown,
    so a failing assertion cannot leak the accept thread into the rest of the session."""
    made: list = []

    def _make(**kw):
        srv = _FakeGpsd(**kw)
        made.append(srv)
        return srv

    yield _make
    for srv in made:
        srv.close()


def set_call(svc, callsign="DJ0CHE"):
    """Configure a valid operator callsign so a LICENSED stack (chat/igate/voice/meshcom) passes
    CALL-enforcement — the realistic precondition for starting one. Returns the service."""
    from lhpc.core.config import save_operator_config
    save_operator_config(svc._paths, callsign)
    svc._invalidate_config()
    return svc

# Real-but-harmless spawn shim: ownership recording now requires a COMPLETE /proc
# identity, so tests that "start" something must spawn a real process (a detached
# `sleep`) rather than a fake pid. All spawned sleepers are reaped at session end.
_SPAWNED: list = []


def real_spawn(argv, log, cwd=None, env=None):
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
