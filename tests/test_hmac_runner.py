"""PART 2 core: the byte-oriented streaming redactor + the controlled (redacting/cancellable) runner.

These prove the security invariant — a secret echoed by the firmware build NEVER reaches the log, at EVERY
chunk boundary — and the typed cancellation/timeout outcomes."""

import threading
import time

from lhpc.core.probes.backends import RealCommandRunner
from lhpc.core.service_hmac import _StreamRedactor


def _stream(secrets, data: bytes, chunk: int) -> bytes:
    r = _StreamRedactor(tuple(s.encode() for s in secrets))
    out = b""
    for i in range(0, len(data), chunk):
        out += r.feed(data[i:i + chunk])
    out += r.flush()
    return out


def test_redactor_masks_at_every_split_and_chunk_size():
    secret = "deadbeefcafebabe0123456789abcdef"       # 32 hex, like a real token
    sb = secret.encode()
    for cs in (1, 2, 3, 5, 7, 16, 31, 32, 33, 64, 1000):
        data = b"prefix " + sb + b" middle " + sb + b" suffix"
        out = _stream([secret], data, cs)
        assert sb not in out, cs
        assert out.count(b"****") == 2, (cs, out)
        assert out.replace(b"****", b"") == b"prefix  middle  suffix", cs


def test_redactor_overlapping_and_shared_prefix_patterns():
    # "abcdef" and its shared prefix "abc" — longest-first masking, fed ONE byte at a time.
    r = _StreamRedactor((b"abc", b"abcdef"))
    out = b""
    for byte in b"x" b"abcdef" b"y" b"abc" b"z":
        out += r.feed(bytes([byte]))
    out += r.flush()
    assert b"abcdef" not in out and b"abc" not in out
    assert out.replace(b"****", b"") == b"xyz"


def test_redactor_empty_patterns_pass_through_unchanged():
    r = _StreamRedactor(())
    assert r.feed(b"hello world") == b"hello world" and r.flush() == b""
    r2 = _StreamRedactor((b"",))                       # empty pattern is dropped
    assert r2.feed(b"hello") == b"hello"


def test_controlled_runner_redacts_real_subprocess_output(tmp_path):
    secret = "s3cr3t-t0ken-value"
    log = tmp_path / "build.log"
    with open(log, "wb") as fh:
        r = RealCommandRunner().run_streaming(
            ["echo", f"compiling with XR_PASSWORD={secret} done"],
            timeout=10, log_fh=fh, redactor=_StreamRedactor((secret.encode(),)))
    data = log.read_bytes()
    assert secret.encode() not in data and b"****" in data and b"compiling with" in data
    assert not r.timed_out and not r.cancelled and r.output_unverified is False


def test_controlled_runner_cooperative_cancellation_terminates(tmp_path):
    log = tmp_path / "c.log"
    flag = {"v": False}

    def flip():
        time.sleep(0.4)
        flag["v"] = True
    threading.Thread(target=flip, daemon=True).start()
    with open(log, "wb") as fh:
        r = RealCommandRunner().run_streaming(
            ["sleep", "30"], timeout=30, log_fh=fh, should_cancel=lambda: flag["v"])
    assert r.cancelled and not r.timed_out
    assert r.termination in ("terminated", "already-ceased")   # proven stopped
    assert r.session_ident and r.session_ident["pid"] > 0


def test_controlled_runner_timeout_is_typed(tmp_path):
    log = tmp_path / "t.log"
    with open(log, "wb") as fh:
        r = RealCommandRunner().run_streaming(
            ["sleep", "30"], timeout=0.4, log_fh=fh, redactor=_StreamRedactor((b"unused",)))
    assert r.timed_out and not r.cancelled
    assert r.termination in ("terminated", "already-ceased")


def test_controlled_runner_writes_to_a_TEXT_mode_log(tmp_path):
    # Regression: production opens the log TEXT-mode (runtime_fs.open_log_truncate), but the redactor
    # yields BYTES — the drain must write to the underlying binary buffer, NOT crash with a TypeError.
    secret = "t0ken-XYZ"
    log = tmp_path / "text.log"
    with open(log, "w", encoding="utf-8") as fh:       # TEXT handle, like production
        r = RealCommandRunner().run_streaming(
            ["echo", f"cc -DXR_PASSWORD={secret} firmware.c"],
            timeout=10, log_fh=fh, redactor=_StreamRedactor((secret.encode(),)))
    data = log.read_bytes()
    assert secret.encode() not in data and b"****" in data and b"firmware.c" in data
    assert not r.timed_out and not r.cancelled and r.output_unverified is False


def test_controlled_runner_large_output_does_not_deadlock(tmp_path):
    # A build emitting far more than a pipe buffer (>256 KB) must fully drain within the timeout — the
    # drain reads continuously, so the child never blocks on a full pipe.
    log = tmp_path / "big.log"
    secret = "deadbeef"
    with open(log, "w", encoding="utf-8") as fh:
        r = RealCommandRunner().run_streaming(
            ["python3", "-c", "print(('x' * 200 + ' deadbeef ') * 4000)"],   # ~840 KB
            timeout=30, log_fh=fh, redactor=_StreamRedactor((secret.encode(),)))
    data = log.read_bytes()
    assert not r.timed_out and r.returncode == 0
    assert len(data) > 256 * 1024 and secret.encode() not in data and b"****" in data


class _FailingSink:
    """A log handle whose underlying binary buffer refuses every write — simulates a full/unwritable disk."""
    def __init__(self):
        self.buffer = self

    def write(self, *a):
        raise OSError("no space left on device")

    def flush(self):
        pass


def test_controlled_runner_flags_log_write_failure_without_crashing(tmp_path):
    # P2: the drain reads the child's output but CANNOT persist it. Cessation/draining are still proven (no
    # unsafe), but the result must carry log_write_failed so the job is not reported as a success.
    r = RealCommandRunner().run_streaming(
        ["echo", "compiling firmware"], timeout=10, log_fh=_FailingSink(),
        redactor=_StreamRedactor((b"unused",)))
    assert r.returncode == 0                                   # the child exited cleanly
    assert r.log_write_failed is True                          # …but its log could not be persisted
    assert r.output_unverified is False and not r.timed_out    # draining/cessation WERE proven -> not unsafe


def test_controlled_runner_fast_path_untouched_when_no_redactor_or_cancel(tmp_path):
    # neither redactor nor should_cancel -> the direct-fd fast path (no CommandResult extras set)
    log = tmp_path / "f.log"
    with open(log, "wb") as fh:
        r = RealCommandRunner().run_streaming(["echo", "hello"], timeout=10, log_fh=fh)
    assert log.read_bytes().strip() == b"hello"
    assert not r.cancelled and not r.output_unverified and r.session_ident is None
