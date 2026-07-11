"""Injectable system-access backends.

Production code depends on the small `System` facade (a command runner, a /proc
reader, a filesystem checker and a Unix-socket client). The real implementation
talks to the OS read-only; the fake implementation is driven entirely by data so
tests need no hardware, no services and no subprocesses.

Design rules honoured here:
  * subprocesses run with `shell=False`, a fixed minimal environment and a hard
    timeout — never a shell string;
  * /proc traversal is isolated here, not mixed with rendering;
  * every failure is captured (timeout, missing binary, permission) and returned
    as data, never raised to callers.
"""

from __future__ import annotations

import os
import socket
import stat
import subprocess
import time
from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False
    not_found: bool = False     # the executable itself was not found
    # On a timeout, the typed proctree.Termination outcome value ("terminated" /
    # "already-ceased" / "unverified" / "incomplete"); "" when the run did not time out.
    termination: str = ""

    @property
    def may_still_be_running(self) -> bool:
        """True when the timed-out run's ORIGINAL verified session could not be proven empty
        (`unverified`/`incomplete`) — a process may remain alive; surface it rather than assume
        it's gone. NOTE: even a `False` here (the session was emptied) does NOT certify that a
        descendant which escaped via `setsid()` died — that is outside the proven session and
        is not claimed either way."""
        return self.termination in ("unverified", "incomplete")


@dataclass(frozen=True)
class Listener:
    """A local TCP socket in the LISTEN state."""

    family: str                 # "ipv4" | "ipv6"
    ip: str
    port: int
    inode: int


# --- backend protocols ----------------------------------------------------


class CommandRunner(Protocol):
    def run(self, argv: list[str], timeout: float,
            cwd: str | None = None, env: dict | None = None) -> CommandResult: ...


class ProcFs(Protocol):
    def cmdlines(self) -> dict[int, list[str]]: ...
    def tcp_listeners(self) -> list[Listener]: ...
    def owner_pid(self, inode: int, budget_s: float) -> tuple[int | None, bool]: ...


class FileSystem(Protocol):
    def exists(self, path: str) -> bool: ...
    def is_socket(self, path: str) -> bool: ...
    def is_char_device(self, path: str) -> bool: ...
    def user_groups(self) -> frozenset[str]: ...   # unix-group names of the current process


class UnixClient(Protocol):
    def request(
        self, path: str, payload: bytes, timeout: float, max_bytes: int
    ) -> bytes: ...

    def send(self, path: str, payload: bytes, timeout: float) -> None: ...


# --- parsing helpers (pure; unit-tested directly) -------------------------

_TCP_LISTEN = "0A"  # st field value for LISTEN


def parse_proc_net_tcp(text: str, family: str) -> list[Listener]:
    """Parse a /proc/net/tcp or tcp6 table, returning LISTEN sockets only."""
    listeners: list[Listener] = []
    for line in text.splitlines()[1:]:  # skip header
        parts = line.split()
        if len(parts) < 10:
            continue
        local, st, inode = parts[1], parts[3], parts[9]
        if st != _TCP_LISTEN:
            continue
        ip_hex, _, port_hex = local.partition(":")
        try:
            port = int(port_hex, 16)
            inode_i = int(inode)
        except ValueError:
            continue
        listeners.append(
            Listener(family=family, ip=_decode_hex_ip(ip_hex), port=port, inode=inode_i)
        )
    return listeners


def _decode_hex_ip(ip_hex: str) -> str:
    """Best-effort decode of the kernel's little-endian hex IP (display only)."""
    try:
        if len(ip_hex) == 8:  # IPv4
            b = bytes.fromhex(ip_hex)
            return ".".join(str(x) for x in reversed(b))
        if len(ip_hex) == 32:  # IPv6
            return ip_hex.lower()
    except ValueError:
        pass
    return ip_hex


# --- real implementation --------------------------------------------------

# A bounded, stable environment for subprocesses. HOME and the XDG vars are passed through so
# git honours the user's config + global gitignore (otherwise globally-ignored files like
# .claude/ show as untracked -> false "dirty") and so `systemctl --user` can find its bus. The
# tool-cache vars (PLATFORMIO_CORE_DIR / IDF_TOOLS_PATH / XDG_CACHE_HOME / PIP_CACHE_DIR) are
# forwarded so that, under the hardened web-service sandbox (ProtectHome=read-only), build/test
# tools write their caches into the runtime-owned location the unit points them at — never into
# ~/.platformio, ~/.espressif or ~/.cache. See deploy/lhpc-web.service.
_FIXED_ENV = {
    "PATH": os.environ.get("PATH", "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"),
    "LANG": "C",
    "LC_ALL": "C",
}
for _k in ("HOME", "XDG_RUNTIME_DIR", "XDG_CONFIG_HOME", "DBUS_SESSION_BUS_ADDRESS",
           "PLATFORMIO_CORE_DIR", "IDF_TOOLS_PATH", "XDG_CACHE_HOME", "PIP_CACHE_DIR", "TMPDIR"):
    if os.environ.get(_k):
        _FIXED_ENV[_k] = os.environ[_k]

# Per-stream in-memory capture cap: keep only the last N bytes (the useful TAIL) of a
# command's stdout/stderr, so a runaway build/test can never exhaust controller memory.
_MAX_CAPTURE_BYTES = 128 * 1024


def _bounded_drain(stream, sink: bytearray) -> None:
    """Read a subprocess pipe to EOF, retaining only the last _MAX_CAPTURE_BYTES in `sink`."""
    try:
        for chunk in iter(lambda: stream.read(8192), b""):
            sink.extend(chunk)
            if len(sink) > _MAX_CAPTURE_BYTES:
                del sink[:len(sink) - _MAX_CAPTURE_BYTES]
    except (OSError, ValueError):
        pass


class RealCommandRunner:
    def run(self, argv: list[str], timeout: float,
            cwd: str | None = None, env: dict | None = None) -> CommandResult:
        import os
        import threading
        try:
            proc = subprocess.Popen(
                argv,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL,
                cwd=cwd, env={**_FIXED_ENV, **(env or {})}, shell=False,
                # Own session/group so a timeout can terminate the whole child TREE, not
                # just the direct child (which would orphan a build's sub-processes).
                start_new_session=True,
            )
        except FileNotFoundError:
            return CommandResult(returncode=127, stdout="", stderr="", not_found=True)
        except OSError as exc:  # permission, etc.
            return CommandResult(returncode=126, stdout="", stderr=str(exc))
        from .. import proctree
        # Capture the FULL session-ownership token IMMEDIATELY after spawn (before the pid can
        # be reused), so a later timeout only signals a session we can still prove is ours.
        _leader_token = proctree.capture_session_token(proc.pid)
        # BOUNDED capture: two drain threads keep only the last _MAX_CAPTURE_BYTES per stream
        # (the useful TAIL), so a runaway build/test can never exhaust memory. Draining
        # continuously also prevents a full-pipe write deadlock.
        out, err = bytearray(), bytearray()
        threads = [threading.Thread(target=_bounded_drain, args=(s, buf), daemon=True)
                   for s, buf in ((proc.stdout, out), (proc.stderr, err))]
        for t in threads:
            t.start()
        timed_out = False
        termination = ""
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            # Terminate the WHOLE owned session (a TERM-ignoring child can outlive its
            # parent) via the ONE shared process-tree helper — drain threads keep running.
            # The typed outcome is surfaced on the result so a caller can see that a process
            # may still be alive (UNVERIFIED/INCOMPLETE) rather than assume it was killed.
            termination = proctree.terminate_session(_leader_token, os.getpid()).value
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                pass
        for t in threads:
            t.join(timeout=2)
        for stream in (proc.stdout, proc.stderr):
            try:
                stream.close()
            except OSError:
                pass
        rc = 124 if timed_out else (proc.returncode if proc.returncode is not None else -1)
        return CommandResult(returncode=rc, stdout=out.decode("utf-8", "replace"),
                             stderr=err.decode("utf-8", "replace"), timed_out=timed_out,
                             termination=termination)

    def run_streaming(self, argv: list[str], timeout: float, log_fh,
                      cwd: str | None = None, env: dict | None = None) -> CommandResult:
        """Like run(), but the child's stdout+stderr stream DIRECTLY into `log_fh`
        (kernel-level fd redirect, interleaved) — the log grows LIVE while the command
        runs instead of being written once at completion. stdout/stderr on the result
        are empty; callers read the persisted log for tails."""
        import os
        try:
            proc = subprocess.Popen(
                argv,
                stdout=log_fh, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                cwd=cwd, env={**_FIXED_ENV, **(env or {})}, shell=False,
                start_new_session=True,
            )
        except FileNotFoundError:
            return CommandResult(returncode=127, stdout="", stderr="", not_found=True)
        except OSError as exc:
            return CommandResult(returncode=126, stdout="", stderr=str(exc))
        from .. import proctree
        _leader_token = proctree.capture_session_token(proc.pid)
        timed_out = False
        termination = ""
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            termination = proctree.terminate_session(_leader_token, os.getpid()).value
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                pass
        rc = 124 if timed_out else (proc.returncode if proc.returncode is not None else -1)
        return CommandResult(returncode=rc, stdout="", stderr="", timed_out=timed_out,
                             termination=termination)


class RealProcFs:
    def cmdlines(self) -> dict[int, list[str]]:
        result: dict[int, list[str]] = {}
        try:
            entries = os.listdir("/proc")
        except OSError:
            return result
        for entry in entries:
            if not entry.isdigit():
                continue
            pid = int(entry)
            try:
                with open(f"/proc/{pid}/cmdline", "rb") as fh:
                    raw = fh.read(64 * 1024)   # bounded: symmetry with capped drains
            except OSError:
                continue
            if not raw:
                continue
            argv = [a for a in raw.split(b"\x00") if a]
            result[pid] = [a.decode("utf-8", "replace") for a in argv]
        return result

    def tcp_listeners(self) -> list[Listener]:
        out: list[Listener] = []
        for path, fam in (("/proc/net/tcp", "ipv4"), ("/proc/net/tcp6", "ipv6")):
            try:
                with open(path, encoding="ascii", errors="replace") as fh:
                    out.extend(parse_proc_net_tcp(fh.read(), fam))
            except OSError:
                continue
        return out

    def owner_pid(self, inode: int, budget_s: float) -> tuple[int | None, bool]:
        """Resolve the PID owning a socket inode, within a strict time budget.

        Returns (pid_or_None, incomplete). `incomplete` is True if the budget was
        exhausted before a definitive answer — callers must not treat that as
        "no owner".
        """
        target = f"socket:[{inode}]"
        deadline = time.monotonic() + budget_s
        try:
            pids = [e for e in os.listdir("/proc") if e.isdigit()]
        except OSError:
            return None, True
        for entry in pids:
            if time.monotonic() > deadline:
                return None, True
            fd_dir = f"/proc/{entry}/fd"
            try:
                fds = os.listdir(fd_dir)
            except OSError:
                continue
            for fd in fds:
                try:
                    if os.readlink(f"{fd_dir}/{fd}") == target:
                        return int(entry), False
                except OSError:
                    continue
        return None, False


class RealFileSystem:
    def exists(self, path: str) -> bool:
        return os.path.exists(path)

    def is_socket(self, path: str) -> bool:
        try:
            return stat.S_ISSOCK(os.stat(path).st_mode)
        except OSError:
            return False

    def is_char_device(self, path: str) -> bool:
        try:
            return stat.S_ISCHR(os.stat(path).st_mode)
        except OSError:
            return False

    def user_groups(self) -> frozenset[str]:
        """Unix-group NAMES the invoking OPERATOR is CONFIGURED into — from the group database
        (`os.getgrouplist`: /etc/group memberships + the primary group), i.e. exactly what
        `usermod -aG` grants and what `id -nG` shows. NOT `os.getgroups()` (the running process's
        cached supplementary groups): a long-lived or lingering service that started BEFORE the groups
        were granted keeps stale supplementary groups, which would report a genuinely-granted member as
        missing until the service/session is restarted (the manifest's 'log out/reboot to apply' step).
        Read-only, no subprocess (safe on GET)."""
        import grp
        import pwd
        try:
            gids = os.getgrouplist(pwd.getpwuid(os.getuid()).pw_name, os.getgid())
        except (KeyError, OSError):
            gids = list(set(os.getgroups()) | {os.getgid()})   # fallback if the passwd entry is absent
        names = set()
        for gid in gids:
            try:
                names.add(grp.getgrgid(gid).gr_name)
            except (KeyError, OSError):
                pass
        return frozenset(names)


class RealUnixClient:
    def request(
        self, path: str, payload: bytes, timeout: float, max_bytes: int
    ) -> bytes:
        """Connect to a Unix stream socket, send `payload`, read one bounded reply.

        Stops at the first newline or `max_bytes`, whichever comes first. Raises
        OSError on any transport problem; callers convert that to evidence.
        """
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(timeout)
            sock.connect(path)
            sock.sendall(payload)
            chunks: list[bytes] = []
            total = 0
            while total < max_bytes:
                chunk = sock.recv(min(512, max_bytes - total))
                if not chunk:
                    break
                chunks.append(chunk)
                total += len(chunk)
                if b"\n" in chunk:
                    break
            return b"".join(chunks)

    def send(self, path: str, payload: bytes, timeout: float) -> None:
        """Fire-and-forget: connect, send, close. No reply is read (used for the
        daemon's raw DATA socket, which transmits but does not answer)."""
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(timeout)
            sock.connect(path)
            sock.sendall(payload)


@dataclass
class System:
    """Facade bundling the four backends used by the probes."""

    runner: CommandRunner
    procfs: ProcFs
    fs: FileSystem
    unix: UnixClient


def RealSystem() -> System:  # noqa: N802 (factory reads as a constructor)
    return System(
        runner=RealCommandRunner(),
        procfs=RealProcFs(),
        fs=RealFileSystem(),
        unix=RealUnixClient(),
    )


# --- fake implementation (for tests) --------------------------------------


@dataclass
class FakeSystem:
    """A fully data-driven System for tests. Build it, then read `.system`."""

    commands: dict[tuple[str, ...], CommandResult] = field(default_factory=dict)
    cmdlines_data: dict[int, list[str]] = field(default_factory=dict)
    listeners: list[Listener] = field(default_factory=list)
    owners: dict[int, int] = field(default_factory=dict)
    owner_incomplete: set[int] = field(default_factory=set)
    paths: set[str] = field(default_factory=set)
    sockets: set[str] = field(default_factory=set)
    char_devices: set[str] = field(default_factory=set)
    unix_replies: dict[str, bytes] = field(default_factory=dict)
    unix_errors: dict[str, str] = field(default_factory=dict)
    calls: list[list[str]] = field(default_factory=list)
    # Distinct name from the user_groups() method (a dataclass field cannot share a method's name).
    # Permissive default so existing tests that start hardware stacks stay green; the missing-capability
    # case is opted into with user_group_names=frozenset().
    user_group_names: frozenset[str] = frozenset({"spi", "gpio"})

    # CommandRunner
    def run(self, argv: list[str], timeout: float,
            cwd: str | None = None, env: dict | None = None) -> CommandResult:
        self.calls.append(list(argv))
        return self.commands.get(
            tuple(argv),
            CommandResult(returncode=127, stdout="", stderr="no fake", not_found=True),
        )

    # ProcFs
    def cmdlines(self) -> dict[int, list[str]]:
        return dict(self.cmdlines_data)

    def tcp_listeners(self) -> list[Listener]:
        return list(self.listeners)

    def owner_pid(self, inode: int, budget_s: float) -> tuple[int | None, bool]:
        if inode in self.owner_incomplete:
            return None, True
        return self.owners.get(inode), False

    # FileSystem
    def exists(self, path: str) -> bool:
        return path in self.paths or path in self.sockets or path in self.char_devices

    def is_socket(self, path: str) -> bool:
        return path in self.sockets

    def is_char_device(self, path: str) -> bool:
        return path in self.char_devices

    def user_groups(self) -> frozenset[str]:
        return self.user_group_names

    # UnixClient
    sent: list[tuple[str, bytes]] = field(default_factory=list)

    def request(
        self, path: str, payload: bytes, timeout: float, max_bytes: int
    ) -> bytes:
        if path in self.unix_errors:
            raise OSError(self.unix_errors[path])
        return self.unix_replies.get(path, b"")[:max_bytes]

    def send(self, path: str, payload: bytes, timeout: float) -> None:
        if path in self.unix_errors:
            raise OSError(self.unix_errors[path])
        self.sent.append((path, payload))

    @property
    def system(self) -> System:
        return System(runner=self, procfs=self, fs=self, unix=self)
