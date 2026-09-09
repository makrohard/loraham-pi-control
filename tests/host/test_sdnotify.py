"""`sd_notify` client: payload sanitation + best-effort delivery.

The wire format is newline-separated `KEY=VALUE`, so the payload is a protocol, not free text. And
the caller is a service unit's startup path: nothing here may raise, block, or fail a start.
"""

from __future__ import annotations

import socket

import pytest

from lhpc.core import sdnotify


@pytest.fixture()
def sock(tmp_path, monkeypatch):
    """A real AF_UNIX SOCK_DGRAM listener at $NOTIFY_SOCKET."""
    path = str(tmp_path / "notify")
    s = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    s.bind(path)
    s.settimeout(2.0)
    monkeypatch.setenv("NOTIFY_SOCKET", path)
    yield s
    s.close()


# --- delivery -----------------------------------------------------------------------------------

def test_no_notify_socket_is_a_silent_noop(monkeypatch):
    monkeypatch.delenv("NOTIFY_SOCKET", raising=False)
    assert sdnotify.notify("STATUS=x") is False          # no socket, no exception


def test_datagram_arrives_verbatim(sock):
    assert sdnotify.notify("STATUS=hello") is True
    assert sock.recv(4096) == b"STATUS=hello"


def test_payload_is_encoded_utf8_not_the_locale(sock):
    # The unit runs with no locale guarantee; the URL separator we send is a non-ASCII '·'.
    assert sdnotify.notify_status("https://a/ · https://b/") is True
    raw = sock.recv(4096)
    assert raw == "STATUS=https://a/ · https://b/".encode("utf-8")
    assert raw.decode("utf-8").startswith("STATUS=https://a/ ·")


def test_notify_status_prefixes_the_key(sock):
    sdnotify.notify_status("up")
    assert sock.recv(4096) == b"STATUS=up"


def test_abstract_socket_name_translates_at_to_nul(monkeypatch):
    # Linux abstract namespace: systemd passes '@name'; the wire form is '\0name'.
    seen = {}

    class _FakeSock:
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def connect(self, addr):
            seen["addr"] = addr
        def sendall(self, payload):
            seen["payload"] = payload

    monkeypatch.setenv("NOTIFY_SOCKET", "@lhpc-test")
    monkeypatch.setattr(socket, "socket", lambda *a, **k: _FakeSock())
    assert sdnotify.notify("STATUS=x") is True
    assert seen["addr"] == "\0lhpc-test"


def test_socket_errors_are_swallowed(monkeypatch, tmp_path):
    monkeypatch.setenv("NOTIFY_SOCKET", str(tmp_path / "nobody-listening"))
    assert sdnotify.notify("STATUS=x") is False          # ECONNREFUSED/ENOENT -> False, not a raise


# --- sanitation: the payload is a protocol ------------------------------------------------------

def test_embedded_nul_is_rejected_never_truncated(sock):
    # A NUL truncates the datagram at the C boundary — half a status would read as a whole one.
    assert sdnotify.notify("STATUS=good\x00EVIL") is False
    with pytest.raises(socket.timeout):
        sock.recv(4096)                                  # nothing was sent at all


def test_newline_cannot_inject_a_second_field(sock):
    # `STATUS=x\nREADY=1` would be TWO protocol fields in one datagram. systemd splits on newlines
    # FIRST, then on the first '=' — so the invariant is "exactly one line", not "one '='". The
    # smuggled READY survives only as inert text inside the STATUS value, never as its own field.
    assert sdnotify.notify("STATUS=x\nREADY=1") is True
    fields = sock.recv(4096).decode("utf-8").split("\n")
    assert fields == ["STATUS=xREADY=1"]                 # one field, key STATUS, nothing else


def test_carriage_return_and_control_chars_are_stripped(sock):
    assert sdnotify.notify("STATUS=a\rb\x07c") is True
    assert sock.recv(4096) == b"STATUS=abc"


def test_tab_survives_but_c0_does_not():
    assert sdnotify.sanitize("a\tb") == "a\tb"
    assert sdnotify.sanitize("a\x01b") == "ab"


def test_oversized_payload_is_truncated_not_sent_whole(sock):
    assert sdnotify.notify("STATUS=" + "x" * (sdnotify.MAX_PAYLOAD * 2)) is True
    assert len(sock.recv(65536)) == sdnotify.MAX_PAYLOAD


def test_sanitize_returns_none_only_for_nul():
    assert sdnotify.sanitize("plain") == "plain"
    assert sdnotify.sanitize("\x00") is None
