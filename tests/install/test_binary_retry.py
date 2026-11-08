"""P1 (DEC-W340): the binary channel's two downloads (the index, an artifact) retry a TRANSFER failure.

The first retry runs at once, then waits of 5, 15, 45 and 120 s; a 4xx, a TLS error, a local write error, a size or
sha256 mismatch fail at once as before. Every failed attempt removes its staging file (a removal that fails is
reported, never swallowed), every attempt starts a fresh hash and byte count, every URL passes the one opener's
HTTPS-only check, and the 10 min deadline holds across a blocking connect or read and across the waits.

The network door `_open_stream(url, timeout)` is scripted; `time.sleep` and `time.monotonic` are recorded or driven,
so no test sleeps."""
import errno
import hashlib
import http.client
import io
import json
import socket
import ssl
import urllib.error
from types import SimpleNamespace

import pytest

from lhpc.core import binary_install as bi

_REAL_HTTP_GET = bi._http_get        # captured at import, before the conftest's network stub
DATA = bytes(range(256)) * 12        # 3072 bytes
URL = "https://github.com/makrohard/lhpc-binaries/releases/download/binaries/demo.tar.zst"
IDX = "https://github.com/makrohard/lhpc-binaries/releases/download/binaries/index-v3.json"


def _entry(data=DATA, url=URL):
    return bi.IndexEntry(stack="demo", filename="demo.tar.zst", url=url, sha256=hashlib.sha256(data).hexdigest(),
                         size=len(data), components={}, runtime_deps=(), target="t", provenance={})


def _http(code):
    return urllib.error.HTTPError(URL, code, "status", {}, None)


class _Body(io.BytesIO):
    """A response body read in 1000-byte pieces; `cut` = the stream breaks (IncompleteRead) at that offset."""

    def __init__(self, data, cut=None, on_read=None):
        super().__init__(data)
        self.cut, self.on_read = cut, on_read

    def read(self, n=-1):
        if self.on_read:
            self.on_read()
        if self.cut is not None and self.tell() >= self.cut:
            raise http.client.IncompleteRead(b"")
        return super().read(min(n, 1000))


@pytest.fixture
def net(monkeypatch, tmp_path):
    """`script`: one outcome per opened URL (an exception to raise, bytes or a body to serve)."""
    s = SimpleNamespace(script=[], calls=[], waits=[], now=[0.0], dest=tmp_path / "dl.tar.zst", seen=[], per_open=0.0)

    def stream(url, timeout=None):
        s.calls.append((url, timeout))
        s.now[0] += s.per_open                            # the time a connect takes
        s.seen.append(s.dest.exists())                    # the staging file as the next attempt finds it
        out = s.script.pop(0)
        if isinstance(out, BaseException):
            raise out
        return out if isinstance(out, io.IOBase) else _Body(out)

    def sleep(t):
        s.waits.append(t)
        s.now[0] += t
    monkeypatch.setattr(bi, "_open_stream", stream)
    monkeypatch.setattr(bi, "_http_get", _REAL_HTTP_GET)
    monkeypatch.setattr(bi.time, "sleep", sleep)
    monkeypatch.setattr(bi.time, "monotonic", lambda: s.now[0])
    return s


def test_a_504_then_success_retries_once_at_once(net):
    net.script += [_http(504), DATA]
    bi.download_artifact(_entry(), net.dest)
    assert (net.dest.read_bytes(), net.waits, len(net.calls)) == (DATA, [0], 2)


def test_504_throughout_waits_0_5_15_45_120_then_refuses_and_leaves_no_file(net, capsys):
    net.script += [_http(504)] * 6
    with pytest.raises(bi.BinaryInstallError, match="download failed: HTTP Error 504"):
        bi.download_artifact(_entry(), net.dest)
    assert (net.waits, len(net.calls), net.dest.exists()) == ([0, 5, 15, 45, 120], 6, False)
    assert "retrying in 15 s" in capsys.readouterr().err                 # each wait is on the operator's output


@pytest.mark.parametrize("exc", [
    urllib.error.URLError(ConnectionResetError(104, "reset")), urllib.error.URLError(socket.timeout("timed out")),
    urllib.error.URLError(ConnectionRefusedError(111, "refused")), urllib.error.URLError(socket.gaierror(-3, "dns")),
    ConnectionResetError(104, "reset"), TimeoutError("timed out"), http.client.RemoteDisconnected("closed")],
    ids=["urlerror-reset", "urlerror-timeout", "urlerror-refused", "urlerror-dns", "reset", "timeout", "disconnected"])
def test_a_transfer_failure_is_retried_also_inside_a_urlerror(net, exc):
    net.script += [exc, DATA]
    bi.download_artifact(_entry(), net.dest)
    assert (net.dest.read_bytes(), net.waits) == (DATA, [0])


@pytest.mark.parametrize("exc", [
    _http(404), ssl.SSLError(1, "bad cert"), urllib.error.URLError(ssl.SSLError(1, "bad cert")),
    urllib.error.URLError("refusing a non-HTTPS redirect to http://x/")],
    ids=["404", "ssl", "urlerror-ssl", "redirect-refusal"])
def test_a_wrong_answer_is_not_retried(net, exc):
    net.script += [exc, DATA]
    with pytest.raises(bi.BinaryInstallError, match="download failed"):
        bi.download_artifact(_entry(), net.dest)
    assert (len(net.calls), net.waits, net.dest.exists()) == (1, [], False)


def test_a_local_write_error_is_not_retried(net, monkeypatch, tmp_path):
    class Full(io.BytesIO):
        def write(self, b):
            raise OSError(errno.ENOSPC, "No space left on device")
    monkeypatch.setattr(bi, "open", lambda *a, **k: Full(), raising=False)
    net.script += [DATA, DATA]
    with pytest.raises(bi.BinaryInstallError, match="No space left"):
        bi.download_artifact(_entry(), net.dest)
    assert (len(net.calls), net.waits) == (1, [])


def test_a_sha256_mismatch_is_not_retried(net):
    net.script += [DATA, DATA]
    bad = _entry()
    bad = bi.IndexEntry(**{**bad.__dict__, "sha256": "0" * 64})
    with pytest.raises(bi.BinaryInstallError, match="sha256 mismatch"):
        bi.download_artifact(bad, net.dest)
    assert (len(net.calls), net.dest.exists()) == (1, False)


def test_a_stream_cut_short_is_removed_and_the_next_attempt_starts_fresh(net):
    # the second attempt finds no staging file, and its own hash and count verify (a carried-over count of the first
    # attempt's 2000 bytes would read "larger than its published size")
    net.script += [_Body(DATA, cut=2000), DATA]
    bi.download_artifact(_entry(), net.dest)
    assert (net.seen, net.dest.read_bytes(), net.waits) == ([False, False], DATA, [0])


def test_a_staging_file_that_cannot_be_removed_is_reported_and_no_attempt_follows(net, monkeypatch):
    def unlink(p, *a, **k):
        raise PermissionError(13, "Permission denied", str(p))
    monkeypatch.setattr(bi.os, "unlink", unlink)
    net.script += [_Body(DATA, cut=2000), DATA]
    with pytest.raises(bi.BinaryInstallError, match=f"could not remove the partial download {net.dest}"):
        bi.download_artifact(_entry(), net.dest)
    assert len(net.calls) == 1


@pytest.mark.parametrize("url", ["http://github.com/makrohard/lhpc-binaries/releases/download/binaries/x",
                                 "file:///etc/passwd"])
def test_the_one_opener_refuses_a_non_https_url_for_the_index_and_an_artifact(net, url):
    with pytest.raises(bi.BinaryInstallError, match="non-HTTPS"):
        bi.fetch_index(url)
    with pytest.raises(bi.BinaryInstallError, match="non-HTTPS"):
        bi.download_artifact(_entry(url=url), net.dest)
    assert net.calls == []


@pytest.mark.parametrize(("per_open", "timeouts", "waits"), [
    (290.0, [60.0, 60.0, 15.0], [0, 5]),     # the third connect gets only the 15 s left
    (298.0, [60.0, 60.0], [0, 4.0])],        # the second wait is cut to the 4 s left; no third attempt
    ids=["connect-capped", "wait-capped"])
def test_the_deadline_caps_each_connect_and_each_wait(net, per_open, timeouts, waits):
    net.per_open = per_open
    net.script += [_http(504)] * 6
    with pytest.raises(bi.BinaryInstallError, match="did not finish within 600 s"):
        bi.download_artifact(_entry(), net.dest)
    assert ([t for _, t in net.calls], net.waits) == (timeouts, waits)


def test_the_deadline_holds_across_a_blocking_read(net):
    # each read lowers the socket's timeout to the time left: 60, 60, then 40; the fourth read is past the deadline
    sock = SimpleNamespace(timeouts=[])
    sock.settimeout = sock.timeouts.append

    def tick():
        net.now[0] += 280.0
    body = _Body(DATA, on_read=tick)
    body.fp = SimpleNamespace(raw=SimpleNamespace(_sock=sock))       # where urllib's response keeps its socket
    net.script.append(body)
    with pytest.raises(bi.BinaryInstallError, match="did not finish within 600 s"):
        bi.download_artifact(_entry(), net.dest)
    assert (sock.timeouts, len(net.calls), net.dest.exists()) == ([60.0, 60.0, 40.0], 1, False)


def test_the_index_retries_too_and_its_bound_is_not_retried(net):
    net.script += [_http(503), json.dumps({"schema": 3, "stacks": {}}).encode()]
    assert bi.fetch_index(IDX) == {"schema": 3, "stacks": {}} and net.waits == [0]
    net.script += [b" " * (bi._INDEX_MAX_BYTES + 1), b"{}"]
    with pytest.raises(bi.BinaryInstallError, match="byte bound"):
        bi.fetch_index(IDX)
    assert len(net.calls) == 3
