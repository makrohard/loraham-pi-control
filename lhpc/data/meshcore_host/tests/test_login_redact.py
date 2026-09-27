"""P1.14: the decrypted MeshCore login never reaches a log.

Fake payloads only. The last test reads the PINNED openhop_core (installed by CI's meshcore-host
job): if a repin renames the two log prefixes, it fails, so the leak cannot come back silently."""
import inspect
import io
import logging

import pytest

from meshcore_host import login_redact

FAKE = "00112233" + "746573742d6f6e6c79" + "00"


@pytest.fixture
def repeater_log():
    """The `RepeaterDaemon` logger with a capturing handler, as the host sets it up."""
    lg = logging.getLogger(login_redact.REPEATER_LOGGER)
    buf = io.StringIO()
    h = logging.StreamHandler(buf)
    lg.addHandler(h); lg.setLevel(logging.INFO)
    login_redact.protect(lg)
    yield lg, buf
    lg.removeHandler(h)


def test_both_secret_lines_are_redacted_at_the_source(repeater_log):
    lg, buf = repeater_log
    lg.info(f"[LoginServer] Plaintext hex: {FAKE}")
    lg.info("[LoginServer] Password hex: %s", "746573742d6f6e6c79")
    lg.info("[LoginServer] Login request from abcdef... password=<provided>")
    out = buf.getvalue()
    assert FAKE not in out and "746573742d6f6e6c79" not in out
    assert out.count(login_redact.REDACTED) == 2
    assert "password=<provided>" in out                     # the useful lines stay


def test_every_handler_sees_only_the_redacted_record(repeater_log):
    # Upstream's dashboard buffer hangs on the ROOT logger; a filter on the originating logger
    # runs before propagation, so the root's handlers get the redacted record too.
    lg, _buf = repeater_log
    root_buf = io.StringIO()
    h = logging.StreamHandler(root_buf)
    logging.getLogger().addHandler(h)
    try:
        lg.info(f"[LoginServer] Plaintext hex: {FAKE}")
    finally:
        logging.getLogger().removeHandler(h)
    assert FAKE not in root_buf.getvalue() and login_redact.REDACTED in root_buf.getvalue()


def test_a_broken_record_is_dropped_not_raised(repeater_log):
    lg, buf = repeater_log
    lg.info("[LoginServer] Plaintext hex: %s %s", FAKE)     # bad args: getMessage raises
    assert FAKE not in buf.getvalue()


def test_the_filter_follows_the_logger_the_repeater_really_passes():
    other = logging.getLogger("SomeRenamedDaemon")

    class _Helper:
        log_fn = other.info

    class _Daemon:
        login_helper = _Helper()

    login_redact.protect_login_helper(_Daemon())
    assert any(isinstance(f, login_redact.LoginSecretFilter) for f in other.filters)


def test_the_pinned_core_still_logs_exactly_these_prefixes():
    # The prefixes are the pinned core's text (login_server.py). A repin that changes them must
    # fail HERE, not leak on a box.
    from openhop_core.node.handlers import login_server
    src = inspect.getsource(login_server)
    for prefix in login_redact.PREFIXES:
        assert f'"{prefix} ' in src or f'f"{prefix} ' in src, prefix
