"""`lhpc _gps-bridge` — feeds ONE consumer a device-shaped NMEA stream.

Why this exists: meshtasticd accepts only `GPS: SerialPath:` (a serial device — no gpsd,
no TCP), and MeshCom's pinned relay speaks only to a LOCAL gpsd. Neither can consume a
remote gpsd, a direct NMEA device, or a fixed position. This bridge turns whatever the
global `[gps]` setting says into the shape each consumer can actually read.

ONE INSTANCE PER CONSUMER. No shared process, no reference counting: the consumers want
different output shapes (a PTY for meshtasticd, a UNIX socket for MeshCom's QEMU UART1),
and a shared process would have to outlive whichever stack stopped first.

Two behaviours here are not optional, both learned on hardware:

* **The PTY must be drained.** meshtasticd does not merely read NMEA — it PROBES the chip
  (`$PDTINFO`, `$PCAS06`, `$PAIR021`, `$PMTK605`, sweeping baud rates) and expects replies.
  Nothing answers a passive feed, so it eventually gives up and proceeds; but its probe
  writes land in the PTY, and with no reader that buffer fills and blocks the writer. We
  read and discard continuously.
* **Readiness comes from the SOURCE, not the path.** A PTY exists the instant it is
  created, long before any position flows. Readiness that trusted the path would report a
  healthy GPS for a dead gpsd.

Nothing here ever logs a coordinate, a raw NMEA sentence, or gpsd JSON.
"""

from __future__ import annotations

import errno
import os
import socket
import sys
import threading
import time

from .gps import (
    FEED_COMPONENTS,
    OUT_PTY,
    bridge_endpoint_path,
    bridge_state_dir,
)

# Exit codes — distinct so the lifecycle can tell "you configured this wrong" from
# "the source went away", which need different operator actions.
EXIT_OK = 0
EXIT_CONFIG = 2          # unusable configuration; retrying would never help
EXIT_OUTPUT = 4          # could not publish the consumer-facing endpoint
# There is deliberately NO "source unreachable" exit code: an unreachable source is a normal,
# recoverable condition (a gpsd restart, a receiver replugged), so the bridge retries with
# backoff and reports it through readiness instead of dying. Exiting would turn a 10-second
# gpsd blip into a stack component that stays down until someone notices.

_READY_STALE_S = 20.0    # no sentence for this long => no longer ready
_READY_REFRESH_S = 10.0  # rewrite the marker this often so `updated` shows liveness
_RECONNECT_MIN_S = 1.0
_RECONNECT_MAX_S = 30.0
_FIXED_INTERVAL_S = 1.0
_BINARY_GRACE_S = 8.0    # data flowing but no NMEA for this long => wrong protocol
# Longest partial line held while waiting for a newline. A real NMEA sentence is <= 82 bytes;
# anything longer is not a sentence in progress, it is a stream with no newlines at all (UBX
# binary, or a wedged remote). Without a bound the partial buffer grows for as long as that
# lasts — unbounded memory on a Pi, and it kept growing even after the binary diagnosis fired.
_MAX_PARTIAL = 4096


def _log(msg: str) -> None:
    """Bridge log line, scrubbed on the way out.

    Every call site here already passes counts rather than positions, but relying on that is
    relying on convention: one future line that interpolates an exception carrying a sentence,
    or a gpsd error echoing its payload, would silently write the operator's location into a
    log file. Redacting at the single choke point makes the guarantee structural.
    """
    from .gps import redact
    sys.stderr.write(f"[gps-bridge] {redact(msg)}\n")
    sys.stderr.flush()


# ---------------------------------------------------------------- NMEA helpers

def _nmea_checksum(body: str) -> str:
    ck = 0
    for ch in body:
        ck ^= ord(ch)
    return f"{ck:02X}"


def _sentence(body: str) -> bytes:
    return f"${body}*{_nmea_checksum(body)}\r\n".encode("ascii", "ignore")


def _deg_to_dm(value: float, is_lat: bool) -> tuple[str, str]:
    hemi = ("N" if value >= 0 else "S") if is_lat else ("E" if value >= 0 else "W")
    v = abs(value)
    deg = int(v)
    minutes = (v - deg) * 60.0
    width = 2 if is_lat else 3
    return f"{deg:0{width}d}{minutes:07.4f}", hemi


def fixed_sentences(lat: float, lon: float, alt: float | None, when: time.struct_time) -> bytes:
    """A minimal, VALID GPGGA+GPRMC pair for a station that does not move.

    Carries the CURRENT UTC: a consumer that sees a stale timestamp treats the fix as
    expired and discards it, so a fixed position must still tick.
    """
    lat_dm, ns = _deg_to_dm(lat, True)
    lon_dm, ew = _deg_to_dm(lon, False)
    hhmmss = time.strftime("%H%M%S", when) + ".00"
    ddmmyy = time.strftime("%d%m%y", when)
    altv = f"{alt:.1f}" if alt is not None else "0.0"
    gga = (f"GPGGA,{hhmmss},{lat_dm},{ns},{lon_dm},{ew},1,08,1.0,{altv},M,0.0,M,,")
    rmc = (f"GPRMC,{hhmmss},A,{lat_dm},{ns},{lon_dm},{ew},0.00,0.00,{ddmmyy},,,A")
    return _sentence(gga) + _sentence(rmc)


# ---------------------------------------------------------------- outputs

class _Output:
    """Consumer-facing endpoint. Publishes a stable path under the runtime root and
    removes it on the way out, so a stopped bridge never leaves a path that looks live."""

    def __init__(self, link: str, paths=None):
        self.link = link
        self.paths = paths
        self._bytes = 0

    def publish(self) -> None: ...
    def write(self, data: bytes) -> None: ...
    def close(self) -> None: ...

    @property
    def written(self) -> int:
        return self._bytes


class PtyOutput(_Output):
    """A pseudo-terminal, for meshtasticd's `GPS: SerialPath:`."""

    def __init__(self, link: str, paths=None):
        super().__init__(link, paths)
        self._master = -1
        self._slave = -1
        self._drain: threading.Thread | None = None
        self._stop = threading.Event()
        self._drained = 0

    def publish(self) -> None:
        self._master, self._slave = os.openpty()
        # RAW: no echo, no CR/LF translation, no flow control. Without this the line
        # discipline would echo the consumer's probe bytes back as if they were NMEA and
        # mangle sentence terminators.
        try:
            import termios
            import tty
            tty.setraw(self._master)
            tty.setraw(self._slave)
            attrs = termios.tcgetattr(self._slave)
            attrs[3] &= ~termios.ECHO
            termios.tcsetattr(self._slave, termios.TCSANOW, attrs)
        except (ImportError, OSError) as exc:
            _log(f"warning: could not set raw mode ({type(exc).__name__}: {exc})")
        os.set_blocking(self._master, False)
        _publish_link(self.paths, self.link, os.ttyname(self._slave))
        self._stop.clear()
        self._drain = threading.Thread(target=self._drain_loop, name="gps-drain", daemon=True)
        self._drain.start()

    def _drain_loop(self) -> None:
        """Discard whatever the consumer writes (chip-probe commands). Non-blocking, so a
        silent consumer never stalls the feed and a chatty one never fills the buffer."""
        while not self._stop.is_set():
            try:
                data = os.read(self._master, 4096)
                if data:
                    self._drained += len(data)
                    continue
            except BlockingIOError:
                pass
            except OSError as exc:
                if exc.errno not in (errno.EIO, errno.EBADF):
                    _log(f"drain stopped: {type(exc).__name__}")
                return
            self._stop.wait(0.05)

    def write(self, data: bytes) -> None:
        try:
            self._bytes += os.write(self._master, data)
        except BlockingIOError:
            # Consumer is not reading. Dropping is correct for a position feed: the next
            # sentence supersedes this one, and unbounded buffering would only deliver
            # stale positions later.
            pass

    def close(self) -> None:
        self._stop.set()
        if self._drain is not None:
            self._drain.join(timeout=2.0)
        _remove_link(self.paths, self.link)
        for fd in (self._master, self._slave):
            try:
                if fd >= 0:
                    os.close(fd)
            except OSError:
                pass

    @property
    def drained(self) -> int:
        return self._drained


class UnixClientOutput(_Output):
    """A CLIENT of QEMU's UART1 UNIX server socket (MeshCom).

    Orientation matters and is easy to get backwards: **QEMU is the server**. It creates
    `.run/gps-uart1.sock` with `server=on,wait=off` and the feed connects to it — exactly what
    the pinned `gps-relay.py` does ("QEMU exposes UART1 as a Unix-domain *server* socket …
    This relay is a *client*"). A feed that listened instead would publish a socket nothing
    ever connects to: it would look healthy and deliver nothing.

    QEMU may not be up yet, and the guest may restart, so this reconnects with backoff and
    drops data while disconnected — a position feed has nothing useful to replay.
    """

    def __init__(self, link: str, paths=None):
        super().__init__(link, paths)
        self._sock: socket.socket | None = None
        self._next_try = 0.0
        self._delay = _RECONNECT_MIN_S
        self.connected = False

    def publish(self) -> None:
        # Nothing to create: the endpoint belongs to QEMU. Connecting is attempted lazily on
        # the first write so the feed starts even when the guest boots later.
        self._try_connect()

    def _try_connect(self) -> bool:
        if self._sock is not None:
            return True
        now = time.monotonic()
        if now < self._next_try:
            return False
        try:
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.settimeout(5.0)
            s.connect(self.link)
            s.setblocking(False)
            self._sock = s
            self._delay = _RECONNECT_MIN_S
            self.connected = True
            _log(f"connected to UART socket {self.link}")
            return True
        except OSError as exc:
            self.connected = False
            self._next_try = now + self._delay
            self._delay = min(self._delay * 2, _RECONNECT_MAX_S)
            _log(f"waiting for UART socket ({type(exc).__name__}); "
                 f"retry in {self._delay:.0f}s")
            return False

    def _drop(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
        self._sock = None
        self.connected = False
        self._next_try = time.monotonic() + _RECONNECT_MIN_S

    def write(self, data: bytes) -> None:
        if not self._try_connect():
            return
        try:
            # Discard anything the guest sends: we never interpret guest->host traffic, but an
            # unread receive buffer would eventually stall the guest.
            try:
                self._sock.recv(4096)
            except (BlockingIOError, OSError):
                pass
            self._sock.sendall(data)
            self._bytes += len(data)
        except BlockingIOError:
            # NOT a broken link. The socket is non-blocking, so this only means the guest has
            # not drained its buffer yet — QEMU's emulated UART is slow and back-pressures
            # constantly. Dropping the sentence is right for a position feed (the next one
            # supersedes it); reconnecting on it churned the connection on every busy moment
            # and was visible on a real node as connect/fail/reconnect loops.
            pass
        except OSError as exc:
            _log(f"UART write failed ({type(exc).__name__}); reconnecting")
            self._drop()

    def close(self) -> None:
        self._drop()


def _publish_link(paths, link: str, target: str) -> None:
    """Publish the endpoint link CONTAINED and atomically.

    Plain `os.symlink` after a check-then-unlink follows symlinked parents — a `state/gps`
    replaced by a link elsewhere would place the endpoint outside the runtime root — and
    leaves a window where the path does not exist while a consumer is already reading it.
    """
    from pathlib import Path

    from . import runtime_fs
    runtime_fs.publish_symlink(paths, Path(link), target)


def _remove_link(paths, link: str) -> None:
    from pathlib import Path

    from . import runtime_fs
    try:
        runtime_fs.unlink_link(paths, Path(link))
    except (OSError, runtime_fs.PathContainmentError):
        pass


# ---------------------------------------------------------------- readiness

def _bounded(partial: bytes) -> bytes:
    """The trailing partial line, discarded once it can no longer become a sentence."""
    return partial if len(partial) <= _MAX_PARTIAL else b""


_NAV_SENTENCES = (b"GGA", b"RMC", b"GLL", b"GNS")


def nmea_checksum_ok(line: bytes) -> bool:
    """Does `line` carry a well-formed `*HH` checksum that matches its payload?

    Readiness is a claim about the SOURCE, so it must not be built on corrupt input: a garbled
    line can present any flag value at all. Sentences without a checksum are rejected too —
    every navigation sentence a receiver emits has one.
    """
    if not line.startswith(b"$") or b"*" not in line:
        return False
    body, _, tail = line[1:].partition(b"*")
    if len(tail) < 2:
        return False
    try:
        want = int(tail[:2], 16)
    except ValueError:
        return False
    got = 0
    for b in body:
        got ^= b
    return got == want


def _coords_present(f: list, lat_i: int) -> bool:
    """Are the lat/lon fields populated with plausible hemispheres? (Never parsed as numbers —
    the bridge must not handle coordinates, only notice that they exist.)"""
    try:
        lat, ns, lon, ew = f[lat_i], f[lat_i + 1], f[lat_i + 2], f[lat_i + 3]
    except IndexError:
        return False
    return bool(lat.strip() and lon.strip()
                and ns.strip().upper() in (b"N", b"S")
                and ew.strip().upper() in (b"E", b"W"))


def classify_sentence(line: bytes) -> tuple[bool, bool]:
    """(is_navigation, has_fix) for one NMEA line.

    `is_navigation` — a checksum-valid GGA/RMC/GLL/GNS with a legal status field. This is what
    "the receiver is talking to us" means; a `$GPTXT` banner is NOT navigation traffic, and a
    receiver stuck in UBX binary mode emits exactly that and nothing else.

    `has_fix` — the same, and its status says the fix is usable AND the coordinate fields are
    populated. An RMC can be flagged `A` with empty coordinates; that is not a position.
    """
    if not nmea_checksum_ok(line) or len(line) < 7:
        return False, False
    kind = line[3:6]
    if kind not in _NAV_SENTENCES:
        return False, False
    f = line.split(b",")
    try:
        if kind == b"GGA":
            q = f[6].strip()
            if not q.isdigit() or int(q) > 8:       # legal quality indicators are 0..8
                return False, False
            return True, q != b"0" and _coords_present(f, 2)
        if kind == b"RMC":
            st = f[2].strip().upper()
            if st not in (b"A", b"V"):
                return False, False
            return True, st == b"A" and _coords_present(f, 3)
        if kind == b"GLL":
            st = f[6].strip().upper()
            if st not in (b"A", b"V"):
                return False, False
            return True, st == b"A" and _coords_present(f, 1)
        # GNS: one mode character per constellation; N = no fix from that one.
        mode = f[6].strip().upper()
        if not mode or not all(c in b"NAEDFMPRS" for c in mode):
            return False, False
        return True, any(c not in b"N" for c in mode) and _coords_present(f, 2)
    except IndexError:
        return False, False                          # truncated -> not usable evidence


def carries_position(line: bytes) -> bool:
    """True when the sentence carries a VALID position. See `classify_sentence`."""
    return classify_sentence(line)[1]


class Readiness:
    """A marker driven by the UPSTREAM SOURCE, never by the output path existing.

    Written next to the endpoint so `lhpc status` and the start gate can read it without
    talking to the bridge. Holds counters and timestamps only — never a position.
    """

    def __init__(self, path: str, paths=None):
        self.path = path
        self.paths = paths
        self.state = "starting"
        self.detail = ""
        self.sentences = 0
        self.nav = 0
        self.fixes = 0
        self.last_ok = 0.0
        self.last_any = 0.0
        self._written_at = 0.0

    def note_data(self, count: int, fixes: int = 0, nav: int = 0) -> None:
        """Record `count` forwarded sentences, `fixes` of which carry a valid position.

        READY means position is flowing — not merely that bytes parse as NMEA. A receiver left
        in UBX binary mode by gpsd still emits a startup `$GPTXT`, and one with no fix still
        emits GSV/GSA and a GGA with fix-quality 0. Counting those as "live" reported
        `position source live (1 sentences)` for a feed that delivered no position at all
        (found on hardware). They mean the source is REACHABLE, which is a different state.
        """
        now = time.time()
        self.sentences += count
        self.nav += nav
        self.last_any = now
        if fixes:
            self.fixes += fixes
            self.last_ok = now
            if self.state != "ready":
                self.state, self.detail = "ready", ""
                self.write()
            elif (now - self._written_at) >= _READY_REFRESH_S:
                # Refresh periodically even while nothing CHANGES: a reader cannot tell "still
                # flowing" from "stopped an hour ago" if `updated` only moves on transitions.
                self.write()
        elif nav and self.state not in ("ready", "connected"):
            # Reachable, but no fix yet — the documented cold-receiver case, a warning rather
            # than a failure. Admission is granted only for VALIDATED NAVIGATION traffic: a
            # `$GPTXT` banner from a receiver stuck in UBX binary mode is not a receiver
            # talking to us, and must fall through to the binary diagnosis instead.
            self.degrade("connected", "source reachable, waiting for a fix")

    def degrade(self, state: str, detail: str) -> None:
        if (self.state, self.detail) != (state, detail):
            self.state, self.detail = state, detail
            self.write()

    def tick(self) -> None:
        if self.state == "ready" and (time.time() - self.last_ok) > _READY_STALE_S:
            self.degrade("stale", f"no position for {int(time.time() - self.last_ok)}s")
        # HEARTBEAT, regardless of state. `updated` is what proves this feed is still the one
        # running; readers treat an unrefreshed marker as belonging to a previous run. Refreshing
        # only when sentences ARRIVE meant a source that is connected but not yet delivering — a
        # cold receiver with no fix, which is normal for minutes — stopped touching the marker and
        # was reported DEGRADED purely for being patient.
        elif (time.time() - self._written_at) >= _READY_REFRESH_S:
            self.write()

    def write(self) -> None:
        """Contained atomic marker write.

        A plain open/replace follows symlinked parents: with `state/gps/<consumer>` swapped
        for a link, the marker lands outside the runtime root. Routed through the
        descriptor-anchored writer so the parent is walked with O_NOFOLLOW and a symlinked
        leaf is refused.
        """
        import json
        from pathlib import Path

        from . import runtime_fs
        payload = {"state": self.state, "detail": self.detail,
                   "sentences": self.sentences,
                   # How many of those actually carried a position. `sentences` alone cannot
                   # distinguish a live receiver from one emitting only GSV/GSA or a lone
                   # $GPTXT, which is exactly the confusion this separates.
                   "fixes": self.fixes,
                   # Validated navigation sentences — what separates "the receiver is talking"
                   # from "some bytes happened to start with $".
                   "nav": self.nav,
                   "updated": int(time.time()),
                   # WHOSE readiness this is. A marker left by a previous run must never
                   # approve a new start, and a pid makes that checkable.
                   "pid": os.getpid()}
        try:
            runtime_fs.atomic_write(self.paths, Path(self.path),
                                    json.dumps(payload), 0o644)
            self._written_at = time.time()
        except (OSError, runtime_fs.PathContainmentError):
            pass

    def clear(self) -> None:
        from pathlib import Path

        from . import runtime_fs
        try:
            runtime_fs.unlink(self.paths, Path(self.path))
        except (OSError, runtime_fs.PathContainmentError):
            pass


# ---------------------------------------------------------------- sources

def _pump_gpsd(host: str, port: int, out: _Output, ready: Readiness, stop) -> None:
    """gpsd, local or remote. Asks for the RAW NMEA stream so no sentence has to be
    synthesised from gpsd's JSON — verified on hardware."""
    delay = _RECONNECT_MIN_S
    while not stop.is_set():
        try:
            with socket.create_connection((host, port), timeout=10) as s:
                s.sendall(b'?WATCH={"enable":true,"nmea":true}\n')
                s.settimeout(5.0)
                delay = _RECONNECT_MIN_S
                # A COMPLETED TCP HANDSHAKE IS NOT A POSITION SOURCE. gpsd accepts connections
                # whether or not it owns a receiver — after a restart it commonly reports
                # `devices: []` and sends nothing at all. Announcing `connected` here admitted
                # that: the start gate accepts `connected`, and the heartbeat then kept it alive
                # indefinitely, so the stack came up position-blind against an empty gpsd. Only
                # checksum-valid navigation traffic may promote this (see `note_data`).
                ready.degrade("starting", "waiting for sentences")
                buf = b""
                while not stop.is_set():
                    try:
                        chunk = s.recv(4096)
                    except TimeoutError:
                        ready.tick()
                        continue
                    if not chunk:
                        break
                    buf += chunk
                    lines = buf.split(b"\n")
                    buf = _bounded(lines.pop())
                    n = fixes = nav = 0
                    for line in lines:
                        if line.startswith(b"$"):
                            out.write(line.rstrip(b"\r") + b"\r\n")
                            n += 1
                            is_nav, has_fix = classify_sentence(line)
                            nav += is_nav
                            fixes += has_fix
                    if n:
                        ready.note_data(n, fixes, nav)
                    ready.tick()
        except (TimeoutError, OSError) as exc:
            ready.degrade("source-lost", f"gpsd unreachable ({type(exc).__name__})")
        if stop.is_set():
            return
        ready.degrade("source-lost", "gpsd connection closed")
        stop.wait(delay)
        delay = min(delay * 2, _RECONNECT_MAX_S)


def _pump_nmea(device: str, baud: int, out: _Output, ready: Readiness, stop,
               expect_key: str = "") -> None:
    """A character device we open ourselves. Configures the port, because an unconfigured
    one silently yields garbage at the wrong speed."""
    delay = _RECONNECT_MIN_S
    while not stop.is_set():
        fd = -1
        try:
            fd = os.open(device, os.O_RDONLY | os.O_NOCTTY | os.O_NONBLOCK)
            # Confirm the thing we OPENED is the receiver whose identity was claimed. The
            # lock key is derived from a stat() of the path; opening it again is a separate
            # resolution, and `/dev/serial/by-id/...` is a symlink that can be repointed at a
            # different device between the two. Without this the feed could read receiver B
            # while the lifecycle believed it held receiver A.
            # Only enforced when an identity was CLAIMED (production always claims one; the
            # plan refuses a non-character device before we get here). Without a claim the
            # source may legitimately be a FIFO or file, which has no device identity at all.
            if expect_key:
                import stat as _stat
                st = os.fstat(fd)
                if not _stat.S_ISCHR(st.st_mode):
                    raise OSError(f"{device} is not a character device")
                if f"serial.dev.{os.major(st.st_rdev)}:{os.minor(st.st_rdev)}" != expect_key:
                    raise OSError(
                        f"{device} now resolves to a different receiver than the one claimed")
            _configure_port(fd, baud)
            delay = _RECONNECT_MIN_S
            ready.degrade("starting", "waiting for sentences")
            buf = b""
            raw_seen = 0          # bytes read from the device
            sentences = 0         # NMEA sentences actually recognised
            nav_seen = 0          # of those, VALIDATED navigation sentences
            since = time.monotonic()
            while not stop.is_set():
                try:
                    chunk = os.read(fd, 4096)
                except BlockingIOError:
                    ready.tick()
                    stop.wait(0.2)
                    continue
                if not chunk:
                    break
                raw_seen += len(chunk)
                buf += chunk
                lines = buf.split(b"\n")
                buf = _bounded(lines.pop())
                n = fixes = nav = 0
                for line in lines:
                    if line.startswith(b"$"):
                        out.write(line.rstrip(b"\r") + b"\r\n")
                        n += 1
                        is_nav, has_fix = classify_sentence(line)
                        nav += is_nav
                        fixes += has_fix
                if n:
                    sentences += n
                    nav_seen += nav
                    ready.note_data(n, fixes, nav)
                # DATA BUT NO SENTENCES is its own failure, and a common one: gpsd switches
                # u-blox receivers into UBX BINARY mode, and they stay there after gpsd stops.
                # The feed would otherwise sit at "waiting for sentences" forever while bytes
                # streamed past — reporting healthy-ish and delivering nothing.
                # Keyed on NAVIGATION sentences, not on any `$` line: a u-blox in UBX mode
                # still emits a `$GPTXT` banner, and counting that as "NMEA is arriving"
                # disabled this detector permanently — the feed then sat in `connected`
                # forever while binary streamed past.
                if (not nav_seen and raw_seen > 512
                        and (time.monotonic() - since) > _BINARY_GRACE_S):
                    ready.degrade("source-lost",
                                  "device is sending binary, not NMEA (a u-blox left in UBX "
                                  "mode by gpsd does this) — use the gpsd source, or switch "
                                  "the receiver back to NMEA")
                ready.tick()
        except OSError as exc:
            ready.degrade("source-lost", f"device unreadable ({type(exc).__name__})")
        finally:
            if fd >= 0:
                try:
                    os.close(fd)
                except OSError:
                    pass
        if stop.is_set():
            return
        stop.wait(delay)
        delay = min(delay * 2, _RECONNECT_MAX_S)


def _configure_port(fd: int, baud: int) -> None:
    import termios
    speed = getattr(termios, f"B{int(baud)}", None)
    if speed is None:
        raise ValueError(f"unsupported baud {baud}")
    try:
        attrs = termios.tcgetattr(fd)
    except termios.error:
        return                      # not a termios device (FIFO/file in tests) — fine
    iflag, oflag, cflag, lflag, _ispeed, _ospeed, cc = attrs
    iflag &= ~(termios.IXON | termios.IXOFF | termios.IXANY | termios.INLCR | termios.ICRNL)
    oflag &= ~termios.OPOST
    lflag &= ~(termios.ECHO | termios.ECHONL | termios.ICANON | termios.ISIG | termios.IEXTEN)
    cflag |= (termios.CLOCAL | termios.CREAD)
    cflag &= ~termios.CSIZE
    cflag |= termios.CS8
    cflag &= ~termios.PARENB
    cflag &= ~termios.CSTOPB
    termios.tcsetattr(fd, termios.TCSANOW,
                      [iflag, oflag, cflag, lflag, speed, speed, cc])


def _pump_fixed(lat: float, lon: float, alt: float | None, out: _Output,
                ready: Readiness, stop) -> None:
    """A station that does not move. Still emits continuously with current UTC."""
    while not stop.is_set():
        out.write(fixed_sentences(lat, lon, alt, time.gmtime()))
        ready.note_data(2, 2)
        stop.wait(_FIXED_INTERVAL_S)


# ---------------------------------------------------------------- entry point

def _install_stop_signals(stop) -> None:
    """Turn SIGTERM/SIGINT into a normal shutdown. Best-effort: signal handlers can only be
    installed on the main thread, and a caller driving `run()` from a worker (tests) already
    owns the stop event."""
    import signal
    try:
        for sig in (signal.SIGTERM, signal.SIGINT):
            signal.signal(sig, lambda _s, _f: stop.set())
    except (ValueError, OSError):
        pass


def run(consumer: str, paths, stop=None) -> int:
    """Serve one consumer until stopped. Reads `[gps]` DIRECTLY — no rendered string, so
    no saved or ephemeral user value can become the source."""
    from .config import load_config
    from .gps import plan_from_config

    # DERIVED from the one feed mapping — a private list here is exactly how a declared,
    # selected, running feed was still refused as an unknown consumer.
    if consumer not in FEED_COMPONENTS:
        _log(f"unknown consumer {consumer!r}")
        return EXIT_CONFIG

    # Under `auto`, honor the CONTROLLER's frozen verdict (it admitted this feed from it) —
    # re-probing here raced a gpsd stopping mid-start into an EXIT_CONFIG that failed the
    # whole stack, a refusal `auto` promises never to produce. Only the bare verdict crosses
    # the boundary; endpoints still come from code and `[gps]`.
    from .gps import AUTO_ENV
    hint = {"gpsd": True, "off": False}.get(os.environ.get(AUTO_ENV, ""))
    plan = plan_from_config(load_config(paths), auto_hint=hint)
    if not plan.enabled:
        _log(f"GPS source is off ({plan.reason or 'not configured'}) — nothing to serve")
        return EXIT_CONFIG

    link = bridge_endpoint_path(paths.runtime_root, consumer)
    # Readiness always lives in OUR state dir, never beside the endpoint: for MeshCom the
    # endpoint belongs to QEMU's source tree, which is not ours to write into.
    ready = Readiness(os.path.join(bridge_state_dir(paths.runtime_root, consumer),
                                   "readiness.json"), paths)
    kind = plan.output_kind(consumer)
    out: _Output = (PtyOutput(link, paths) if kind == OUT_PTY
                    else UnixClientOutput(link, paths))

    try:
        out.publish()
    except OSError as exc:
        _log(f"cannot publish {kind} endpoint: {type(exc).__name__}: {exc}")
        return EXIT_OUTPUT

    stop = stop or threading.Event()
    # SIGTERM is how the lifecycle stops us. Python's default handler exits the process
    # OUTRIGHT, so `finally` never runs and we would leave behind a live-looking endpoint
    # and a readiness marker still saying "ready" — a stopped GPS that reads healthy.
    # Translating the signal into the stop event lets the normal teardown happen.
    _install_stop_signals(stop)

    ready.degrade("starting", f"source={plan.source}")
    _log(f"serving {consumer} via {kind} from source={plan.source}")
    try:
        if plan.source == "gpsd":
            _pump_gpsd(plan.host, plan.port, out, ready, stop)
        elif plan.source == "nmea":
            _pump_nmea(plan.device, plan.nmea_baud, out, ready, stop,
                       expect_key=plan.device_key)
        elif plan.source == "fixed":
            alt = float(plan.fixed_alt) if plan.fixed_alt else None
            _pump_fixed(float(plan.fixed_lat), float(plan.fixed_lon), alt, out, ready, stop)
        else:
            _log(f"unsupported source {plan.source!r}")
            return EXIT_CONFIG
    except KeyboardInterrupt:
        pass
    finally:
        # Readiness and the endpoint disappear together: a stopped bridge must never leave
        # a path or a marker that reads as a live GPS.
        ready.clear()
        out.close()
        drained = getattr(out, "drained", None)
        _log(f"stopped ({out.written} bytes out"
             + (f", {drained} drained" if drained is not None else "") + ")")
    return EXIT_OK
