"""RF log — what the radio heard and sent, one persistent line per packet.

The controller names the file (``rf_log`` + ``rf_log_path`` in the host toml's
``[radio]`` table); this process derives nothing. Line contract, shared with the
other LoRaHAM writers::

    <utc> RX rssi=<dBm> snr=<dB> len=<n> hex=<..> ascii="<..>"
    <utc> TX rssi=- snr=- len=<n> outcome=<ok|unconfirmed> hex=<..> ascii="<..>"

RSSI/SNR are receive metadata; a TX line carries payload and outcome. The
payload is the raw RF frame at the daemon boundary — a MeshCore frame is
binary, so there is no text summary. Every mode (chat, repeater, chat+repeater)
goes through the one LoRaHAMRadio, so every mode logs here.

Retention: copy-truncate at MAX_BYTES into ``<path>.1``; the inode never
changes, so an external truncate (the controller's Clear) is tolerated
(O_APPEND). One writer per process.
"""

import errno
import os
import threading
import time

MAX_BYTES = 5 * 1024 * 1024

_TRUE = ("yes", "on", "true", "1")
_FALSE = ("no", "off", "false", "0")


def parse_switch(value):
    """A TOML bool, or ``on``/``yes``/``true``/``1`` → True and ``off``/``no``/
    ``false``/``0`` → False. Anything else raises: a switch that is neither on
    nor off must not default silently."""
    if isinstance(value, bool):
        return value
    text = str(value if value is not None else "").strip().lower()
    if text in _TRUE:
        return True
    if text in _FALSE:
        return False
    raise ValueError(f"rf_log must be on or off, not {value!r}")


def _utc_now():
    now = time.time()
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(now)) + f".{int(now * 1000) % 1000:03d}Z"


def _ascii(data):
    # Printable ASCII only; the quote and the backslash would break the line's
    # own quoting, so they are dots like every other non-printable.
    return "".join(chr(b) if 0x20 <= b < 0x7F and b not in (0x22, 0x5C) else "." for b in data)


def format_rx(utc, rssi, snr, data):
    return (f"{utc} RX rssi={float(rssi):.2f} snr={float(snr):.2f} len={len(data)}"
            f" hex={bytes(data).hex()} ascii=\"{_ascii(data)}\"\n")


def format_tx(utc, outcome, data):
    return (f"{utc} TX rssi=- snr=- len={len(data)} outcome={outcome}"
            f" hex={bytes(data).hex()} ascii=\"{_ascii(data)}\"\n")


class RfLog:
    """Open with :meth:`open`; :meth:`rx`/:meth:`tx` are silent no-ops while closed."""

    def __init__(self, max_bytes=MAX_BYTES):
        self._fd = None
        self._path = None
        self.max_bytes = max_bytes
        # One lock around rollover + write, so no two callers can ever interleave a
        # copy-truncate (the host is single-threaded today; the lock keeps that a fact of the
        # writer, not of its caller).
        self._lock = threading.Lock()

    @property
    def active(self):
        return self._fd is not None

    @property
    def path(self):
        return self._path

    def open(self, path):
        # Absolute only: the controller names the file; this process never
        # resolves a relative path against a directory it did not choose.
        path = str(path or "")
        if not path.startswith("/"):
            raise ValueError("rf_log_path must be an absolute path")
        # O_RDWR, not O_WRONLY: the rollover reads this same descriptor to copy
        # the tail out. O_APPEND still lands every write at the current end.
        fd = os.open(path, os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_CLOEXEC, 0o644)
        self.close()
        self._fd, self._path = fd, path

    def close(self):
        if self._fd is not None:
            try:
                os.close(self._fd)
            except OSError:
                pass
        self._fd = self._path = None

    def rx(self, rssi, snr, data):
        with self._lock:
            if self._fd is not None:
                self._write(format_rx(_utc_now(), rssi, snr, data))

    def tx(self, outcome, data):
        with self._lock:
            if self._fd is not None:
                self._write(format_tx(_utc_now(), outcome, data))

    # -- retention: copy-truncate, same inode -------------------------------

    def _copy_to_previous(self):
        try:
            with open(self._path + ".1", "wb") as prev:
                off = 0
                while True:
                    chunk = os.pread(self._fd, 65536, off)
                    if not chunk:
                        return True
                    prev.write(chunk)
                    off += len(chunk)
        except OSError:
            return False

    def _write(self, line):
        raw = line.encode("utf-8", "replace")
        try:
            if os.fstat(self._fd).st_size + len(raw) > self.max_bytes:
                # The previous segment is replaced only when the copy succeeded; a
                # failed copy keeps the live file intact and lets it grow past the
                # cap. Copied but not truncated: the next roll overwrites .1 again.
                if self._copy_to_previous():
                    try:
                        os.ftruncate(self._fd, 0)
                    except OSError:
                        pass
            while raw:
                try:
                    n = os.write(self._fd, raw)
                except InterruptedError:
                    continue
                raw = raw[n:]
        except OSError as exc:
            if exc.errno != errno.EINTR:
                return          # a lost line is not worth blocking the reader loop
