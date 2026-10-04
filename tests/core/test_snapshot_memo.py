"""The controller's snapshot memo and the per-request memo behind it: one status assessment per
operation or web request, thread-local so concurrent Waitress workers never share or clobber
one another, dropped by every mutating entry and by `fresh=True` — and the once-per-request
read contracts a render relies on (firewall status, listeners, stack configs, source lines)."""

from __future__ import annotations

import inspect
import os
import threading
import types

import pytest

from lhpc.adapters.web.app import create_app
from lhpc.core import reslock, status as statusmod
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import CommandResult as CR, FakeSystem
from lhpc.core.services import ControllerService


def _svc(tmp_path):
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    return ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))


def _count_assessments(monkeypatch):
    n = []
    orig = statusmod.StatusProber.assess_stacks
    monkeypatch.setattr(statusmod.StatusProber, "assess_stacks",
                        lambda self, stacks: (n.append(1), orig(self, stacks))[1])
    return n


def _count_calls(monkeypatch, owner, name):
    n = []
    orig = getattr(owner, name)
    monkeypatch.setattr(owner, name, lambda self, *a, **k: (n.append(1), orig(self, *a, **k))[1])
    return n


def test_render_assesses_the_snapshot_once_per_request(tmp_path, monkeypatch):
    # The Apps page calls build_snapshot ~15× (one per stack helper). The memo must collapse that
    # to a SINGLE assessment — this is the whole performance fix.
    n = _count_assessments(monkeypatch)
    c = create_app(lambda: _svc(tmp_path)).test_client()
    n.clear(); c.get("/stacks")
    assert len(n) == 1, f"one render must assess once, got {len(n)}"


def test_each_request_reassesses_fresh(tmp_path, monkeypatch):
    # before_request drops the cache, so a second request never serves the first request's snapshot.
    n = _count_assessments(monkeypatch)
    c = create_app(lambda: _svc(tmp_path)).test_client()
    n.clear(); c.get("/stacks"); c.get("/stacks"); c.get("/")
    assert len(n) == 3, f"each request reassesses exactly once, got {len(n)}"


def test_memo_returns_same_object_until_invalidated(tmp_path):
    svc = _svc(tmp_path)
    a = svc.build_snapshot()
    assert svc.build_snapshot() is a                 # memoized within the operation
    svc.invalidate_snapshot()
    assert svc.build_snapshot() is not a             # invalidated -> recompute


def test_fresh_bypasses_cache_and_refreshes_it(tmp_path):
    # The authoritative under-lock rechecks pass fresh=True and must NEVER get a cached snapshot.
    svc = _svc(tmp_path)
    a = svc.build_snapshot()
    b = svc.build_snapshot(fresh=True)
    assert b is not a                                # fresh forced a recompute
    assert svc.build_snapshot() is b                 # and refreshed the cache for later readers


def test_mutating_ops_drop_the_memo(tmp_path):
    # A public mutating entry must never let a later read serve a pre-mutation snapshot, even in
    # the same process (CLI sequences, an outer op reading after an inner public stop). Entry+exit
    # invalidation also covers refusal paths, so this holds regardless of the op's outcome.
    svc = _svc(tmp_path)
    a = svc.build_snapshot()
    assert svc.stop("kiss", apply=False).ok                    # traverses the decorated public entry
    assert svc.build_snapshot() is not a


def test_uninstall_drops_the_memo(tmp_path, monkeypatch):
    # uninstall's locked recheck caches the PRE-removal state; a later read in the same thread must
    # reassess, never serve that snapshot with the removed sources still installed.
    svc = _svc(tmp_path)
    n = _count_assessments(monkeypatch)
    assert svc.uninstall("kiss", apply=True).ok
    n.clear()
    svc.build_snapshot()
    assert len(n) == 1


def test_nested_public_stop_refreshes_the_outer_readers(tmp_path):
    # The owner-stop window inside start(): after an inner public stop returns, the outer op's next
    # build_snapshot() must recompute (the inner exit-invalidation is what restores the guarantee).
    svc = _svc(tmp_path)
    a = svc.build_snapshot()
    res = svc.stop("kiss", apply=True)               # nothing runs here: a typed result, never a raise
    assert res.ok is True
    assert svc.build_snapshot() is not a


def test_snapshot_memo_is_thread_local(tmp_path):
    # The shared ControllerService is hit by concurrent Waitress worker threads. The memo must be
    # thread-local: one thread's invalidation must NOT clobber another thread's cached snapshot, and
    # each thread computes its own. Sequenced with events so the interleaving is deterministic.
    svc = _svc(tmp_path)
    r = {}
    a_built, b_done = threading.Event(), threading.Event()

    def thread_a():
        r["a1"] = svc.build_snapshot()          # A memoizes in A's thread-local
        a_built.set()
        b_done.wait(5)                          # ... while B builds + invalidates on its own thread
        r["a2"] = svc.build_snapshot()          # must return A's SAME object (B could not clobber it)

    def thread_b():
        a_built.wait(5)
        r["b1"] = svc.build_snapshot()          # B memoizes in B's own thread-local (distinct object)
        svc.invalidate_snapshot()               # clears ONLY B's memo
        b_done.set()

    ta, tb = threading.Thread(target=thread_a), threading.Thread(target=thread_b)
    ta.start(); tb.start(); ta.join(5); tb.join(5)

    assert r["a1"] is r["a2"]                    # A's memo survived B's invalidate -> thread-local
    assert r["b1"] is not r["a1"]               # each thread assessed its own snapshot


# --- the render contract "once per request" ---------------------------------------------------------

def test_a_render_reads_firewall_status_and_listeners_once(tmp_path, monkeypatch):
    # A /stacks render once called firewall_status() 10–13× and tcp_listeners() once per
    # TCP endpoint; both are now render-wide reads passed down the existing seams.
    fw = _count_calls(monkeypatch, ControllerService, "firewall_status")
    lis = _count_calls(monkeypatch, FakeSystem, "tcp_listeners")
    c = create_app(lambda: _svc(tmp_path)).test_client()
    for path in ("/stacks", "/", "/stacks/meshcore/body"):
        fw.clear(); lis.clear()
        assert c.get(path).status_code == 200
        assert len(fw) <= 2, (path, len(fw))          # the render-wide read (+ the settings view)
        assert len(lis) <= 2, (path, len(lis))        # the snapshot assessment + ONE shared read


def test_stack_config_is_loaded_once_per_stack_and_band_per_request(tmp_path, monkeypatch):
    # 467 `load_stack_config` reads per render (every parameter row) collapse to one per
    # (stack, band) through the thread-local request memo.
    from lhpc.core import service_params as _sp
    n = []
    orig = _sp.load_stack_config
    monkeypatch.setattr(_sp, "load_stack_config",
                        lambda *a, **k: (n.append((a[1], a[2] if len(a) > 2 else k.get("band", ""))),
                                         orig(*a, **k))[1])
    svc = _svc(tmp_path)
    c = create_app(lambda: svc).test_client()
    n.clear(); assert c.get("/stacks").status_code == 200
    assert len(n) == len(set(n)), "the same (stack, band) file was read more than once in a render"
    assert len(n) <= 3 * len(svc.stacks())


def test_request_memo_is_thread_local_and_cleared_with_the_snapshot(tmp_path):
    svc = _svc(tmp_path)
    a = svc._request_memo(("k",), object)
    assert svc._request_memo(("k",), object) is a            # memoized within the request
    svc.invalidate_snapshot()
    b = svc._request_memo(("k",), object)
    assert b is not a                                          # dropped with the snapshot
    svc._invalidate_config()
    assert svc._request_memo(("k",), object) is not b          # dropped by a config write too
    r = {}
    def other():
        r["t"] = svc._request_memo(("k",), object)
    t = threading.Thread(target=other); t.start(); t.join(5)
    assert r["t"] is not svc._request_memo(("k",), object)    # per thread, never shared
    # a failing compute is never memoized
    calls = []
    def boom():
        calls.append(1)
        raise ValueError("x")
    for _ in range(2):
        with pytest.raises(ValueError):
            svc._request_memo(("boom",), boom)
    assert calls == [1, 1]


def test_consumed_source_lines_run_git_once_per_component_per_request(tmp_path):
    svc = _svc(tmp_path)
    comp = next(c for s in svc.stacks() for c in s.components if c.build_requires and c.build_marker)
    before = len(svc._system.runner.calls)
    first = svc._consumed_source_lines(comp)
    n_git = len(svc._system.runner.calls) - before
    assert n_git >= 1 and svc._consumed_source_lines(comp) == first
    assert len(svc._system.runner.calls) - before == n_git      # the second read hit the memo
    svc.invalidate_snapshot()
    svc._consumed_source_lines(comp)
    assert len(svc._system.runner.calls) - before == 2 * n_git  # a new request recomputes


def _git_src(src, sha):
    a = str(src)
    return {("git", "-C", a, "rev-parse", "HEAD"): CR(0, sha + "\n", ""),
            ("git", "-C", a, "status", "--porcelain=v2", "--branch", "--untracked-files=no"):
                CR(0, f"# branch.oid {sha}\n# branch.head main\n", ""),
            ("git", "-C", a, "describe", "--tags", "--always", "--dirty"): CR(0, "v111a\n", "")}


def test_components_sharing_a_checkout_and_pin_are_probed_once_per_snapshot(tmp_path):
    # kiss-tnc and kiss-serial both build from src/loraham-kiss-tnc at the same pin: one snapshot asks git
    # about that checkout ONCE (two subprocesses), not once per component.
    from lhpc.core.model import SourceSpec
    from lhpc.core.status import StatusProber
    src = tmp_path / "src" / "loraham-kiss-tnc"
    fake = FakeSystem(paths={str(src), str(src / ".git")}, commands=_git_src(src, "a" * 40))
    paths = Paths(runtime_root=tmp_path)
    svc = ControllerService(system=fake.system, paths=paths)
    comps = [c for s in svc.stacks() for c in s.components
             if c.source and c.source.path == "src/loraham-kiss-tnc"]
    assert len(comps) >= 2 and len({c.source.pin_commit for c in comps}) == 1   # precondition
    prober = StatusProber(fake.system, paths)
    snap = prober.assess_stacks(svc.stacks())
    git = [c for c in fake.calls if c[:3] == ["git", "-C", str(src)]]
    assert len(git) == 2, git                                   # status + describe, once
    heads = {snap.stacks[i].components[c.id].source_head for i, s in enumerate(svc.stacks())
             for c in comps if c.id in snap.stacks[i].components}
    assert heads == {"a" * 40}
    # a DIFFERENT pin on the same path is a different question -> its own probe
    other = SourceSpec(path="src/loraham-kiss-tnc", pin_commit="b" * 40)
    prober2 = StatusProber(fake.system, paths)
    fake.calls.clear()
    prober2._assess_source(type("C", (), {"id": "x", "source": other})())
    prober2._assess_source(type("C", (), {"id": "y", "source": comps[0].source})())
    assert len([c for c in fake.calls if c[:1] == ["git"]]) == 4


def test_a_restart_plan_assesses_the_snapshot_once(tmp_path, monkeypatch, set_call):
    # The combined restart plan (start leg + stop collateral) goes through the INNER planners: the
    # public entries would drop the snapshot and the request memo between the two legs and assess
    # everything twice inside one web Restart click.
    n = _count_assessments(monkeypatch)
    svc = _svc(tmp_path)
    set_call(svc)
    n.clear()
    plan = svc.restart("kiss", apply=False)
    assert plan.ok and "dependents" in plan.data
    assert len(n) <= 2, f"restart plan assessed {len(n)}×"          # one memoized (+ one fresh recheck)


_KNOWN_MUTATORS = (
    # decorated before 0.11.10's follow-ups
    "apply_daemon_params", "auto_install", "auto_install_abort", "auto_install_ack", "build",
    "clean", "hmac_apply_start", "hmac_set_secret", "install", "poststart", "restart",
    "save_config", "save_config_bundle", "start", "stop", "test", "uninstall", "update",
    # the known undecorated mutators (build marker, receipts/files, config/params, markers)
    "graywolf_upstream_update", "binary_install", "binary_retire", "reset_config",
    "save_stack_config", "save_daemon_params", "reset_daemon_params", "daemon_set",
    "save_component_remote", "confirm_known_working", "boot_restore_run", "set_high_power",
    "set_gps", "set_rflog", "set_rflog_all",
)


@pytest.mark.parametrize("name", _KNOWN_MUTATORS)
def test_known_mutators_are_decorated(name):
    """The KNOWN mutating entries carry the `invalidates_snapshot` marker. Not a completeness
    guarantee: a new public mutator that is neither decorated nor listed here still passes."""
    assert getattr(getattr(ControllerService, name), "invalidates_snapshot", False) is True


def test_graywolf_upstream_update_drops_the_memo(tmp_path):
    # It re-marks the build and replaces the installed tree; a later read must reassess.
    svc = _svc(tmp_path)
    a = svc.build_snapshot()
    assert not svc.graywolf_upstream_update("graywolf", apply=False).ok  # no upstream check recorded
    assert svc.build_snapshot() is not a


def test_every_public_service_entry_is_classified():
    """The COMPLETE universe: every public `ControllerService` name except a plain constant is
    either `@invalidates_snapshot` or listed in `snapshot_memo.SNAPSHOT_NEUTRAL` with a reason —
    never both, never neither, and no listed name is gone. A new public entry of any shape fails
    here until someone decides which it is. Introspection only: no service is built."""
    from lhpc.core.snapshot_memo import SNAPSHOT_NEUTRAL
    universe = {n for n in dir(ControllerService) if not n.startswith("_")
                and not isinstance(inspect.getattr_static(ControllerService, n), (int, float, str, tuple))}
    marked = {n for n in universe
              if getattr(inspect.getattr_static(ControllerService, n), "invalidates_snapshot", False) is True}
    neutral = set(SNAPSHOT_NEUTRAL)
    assert not universe - marked - neutral, f"unclassified: {sorted(universe - marked - neutral)}"
    assert not marked & neutral, f"decorated AND listed neutral: {sorted(marked & neutral)}"
    assert not neutral - universe, f"listed neutral but no longer public: {sorted(neutral - universe)}"
    assert all(isinstance(r, str) and r.strip() for r in SNAPSHOT_NEUTRAL.values())


def test_set_hardware_setup_drops_the_memo(tmp_path):
    # The setup decides the served bands the snapshot reports; a later read must reassess.
    svc = _svc(tmp_path)
    a = svc.build_snapshot()
    assert svc.set_hardware_setup("loraham").ok
    assert svc.build_snapshot() is not a


# ---- the neutral entries: what they write, and that no snapshot input observes it ----------
#
# Each neutral entry is DRIVEN on a live runtime root (meshtastic installed and running with
# its RF trace over the roll cap, a direct-NMEA receiver configured on a pty) and every write
# it makes is traced — files, directories, modes, renames, truncations, the receiver's tty mode,
# signals and every command (System runner or a real subprocess). A "read-only" entry that
# writes anything fails, and a neutral writer must write EXACTLY its SNAPSHOT_NEUTRAL_WRITERS
# paths. Then a fresh snapshot is traced on that state — also while the claim and roll locks are
# held — and fails the day it reads one of those paths, the receiver's tty mode, or runs a
# command that names one.

_RECEIVER = "termios:gps-receiver"
# The writers on a read path or inside a decorated op (their SNAPSHOT_NEUTRAL reasons). Pinned
# here so no entry moves into this category, out of the traced ones, without a visible edit.
_READ_PATH_WRITERS = frozenset({
    "active_jobs", "build_snapshot", "clear_daemon_feed", "clear_stale_interactive",
    "daemon_channel_scan", "firewall_gate_activation", "firewall_gate_stack_start",
    "invalidate_snapshot", "mark_interactive", "network_view", "prune_logs", "refresh_gps_auto",
    "rflog_decode", "web_session_secret",
})
_TMP = __import__("re").compile(r"\..+\.tmp-\d+-[0-9a-f]+")     # runtime_fs's atomic temp names
_READ_ONLY_COMMANDS = (("systemctl", "show"), ("systemctl", "is-system-running"),
                       ("systemctl", "--user", "is-system-running"), ("systemctl", "is-active"),
                       ("systemctl", "is-enabled"))
# The tty ioctls that change a device (its mode, line state, window or exclusivity); a read
# (TCGETS, TIOCMGET, ...) is not a write.
_TTY_SETS = frozenset(getattr(__import__("termios"), n) for n in (
    "TCSETS", "TCSETSW", "TCSETSF", "TCSETA", "TCSETAW", "TCSETAF", "TCFLSH", "TCXONC", "TCSBRK",
    "TCSBRKP", "TIOCSWINSZ", "TIOCMSET", "TIOCMBIS", "TIOCMBIC", "TIOCEXCL", "TIOCNXCL",
    "TIOCSCTTY", "TIOCSTI", "TIOCSBRK", "TIOCCBRK", "TIOCSSERIAL", "TIOCSETD")
    if hasattr(__import__("termios"), n))
_READ_ONLY_GIT = {"rev-parse", "status", "rev-list", "describe", "log", "show", "ls-files"}


def _read_only_command(argv):
    argv = [str(a) for a in argv]
    if any(tuple(argv[:len(p)]) == p for p in _READ_ONLY_COMMANDS):
        return True
    if argv[:2] == ["git", "-C"] and len(argv) > 3:
        return argv[3] in _READ_ONLY_GIT or argv[3:5] == ["config", "--get"]
    return False


@pytest.fixture
def receiver():
    master, slave = os.openpty()
    yield os.ttyname(slave)
    os.close(master)
    os.close(slave)


def _live(tmp_path, dev, monkeypatch):
    """A runtime root in the state the neutral writers act on: meshtastic installed (its binary)
    and running (process, both listeners and its `state/meshtasticd/` tree; GPS use off, so it
    does not hold the receiver), its native RF trace over the roll cap, and a direct-NMEA
    receiver configured on a pty, free of gpsd. Returns (svc, root, fake)."""
    from lhpc.core import config as cfgmod, gps as _gps, rflog
    from lhpc.core.probes.backends import Listener
    monkeypatch.setattr(rflog, "MAX_BYTES", 4096)           # the cap, small: same code path
    monkeypatch.setattr(_gps, "gpsd_owns_device", lambda d, h, po, timeout=3.0: (False, "free"))
    root = tmp_path.resolve()
    (root / "config").mkdir(parents=True, exist_ok=True)
    binary = root / "build" / "tools" / "meshtasticd" / "meshtasticd"
    binary.parent.mkdir(parents=True)
    binary.write_text("#!/bin/sh\n")
    binary.chmod(0o755)
    (root / "state" / "meshtasticd" / "ssl").mkdir(parents=True)   # the running node's own state
    paths = Paths(runtime_root=root)
    cfgmod.save_stack_config(paths, "meshtastic", {"use_gps": "off"})
    cfgmod.save_gps(paths, source="nmea", device=dev, nmea_baud=9600)
    (root / "logs").mkdir()
    (root / "logs" / "rf-meshtastic.log").write_bytes(b'{"x":1}\n' * 1024)       # 8 KiB > cap
    fake = FakeSystem(cmdlines_data={4242: [str(binary), "-c", f"{root}/config/files/meshtasticd.yaml",
                                            "-d", f"{root}/state/meshtasticd"]})
    svc = ControllerService(system=fake.system, paths=paths)
    for inode, ep in enumerate(svc.stack("meshtastic").main_component.endpoints, start=11):
        host, port = ep.address.rsplit(":", 1)              # its own listeners, owned by the node
        fake.listeners.append(Listener("ipv4", host, int(port), inode))
        fake.owners[inode] = 4242
    svc._MONITOR_SAMPLE_S = 0.3
    return svc, str(root), fake


def _fd_path(fd):
    try:
        return os.readlink(f"/proc/self/fd/{fd if isinstance(fd, int) else fd.fileno()}")
    except (OSError, AttributeError, TypeError):
        return None


def _trace_writes(fn, root, dev, fake, monkeypatch):
    """Run `fn` and return (every write it made, its exception or None). A write is a path
    relative to `root` (or absolute outside it), `_RECEIVER` for the receiver's tty mode, or
    `exec:`/`kill:` for a command that is not a known read or a signal other than 0 (kill,
    killpg)."""
    import builtins
    import fcntl
    import io
    import subprocess
    import termios
    out = set()
    flags_w = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND

    def note(p, dir_fd=None):
        try:
            p = os.fsdecode(os.fspath(p))
        except TypeError:
            return
        if dir_fd is not None and not os.path.isabs(p):
            p = os.path.join(_fd_path(dir_fd) or "", p)
        p = os.path.normpath(p)
        if p == root or p.startswith(root + os.sep):
            p = os.path.relpath(p, root)
        if not _TMP.fullmatch(os.path.basename(p)):         # the rename's target is recorded
            out.add(p)

    def w_open(f):
        def g(p, flags, *a, **k):
            if flags & flags_w:
                note(p, k.get("dir_fd"))
            return f(p, flags, *a, **k)
        return g

    def w_io(f):
        def g(p, mode="r", *a, **k):
            if not isinstance(p, int) and any(c in mode for c in "wax+"):
                note(p)
            return f(p, mode, *a, **k)
        return g

    def w_paths(*keys):             # the dir_fd keyword of each path argument; None: not written
        def w(f):
            def g(*a, **k):
                for i, key in enumerate(keys):
                    if key and i < len(a) and not isinstance(a[i], int):
                        note(a[i], k.get(key))
                return f(*a, **k)
            return g
        return w

    def w_mkdir(f):                 # only a directory that is really created
        def g(p, *a, **k):
            r = f(p, *a, **k)
            note(p, k.get("dir_fd"))
            return r
        return g

    def w_mode(f, st):              # only a mode that really changes
        def g(p, mode, *a, **k):
            try:
                before = st(p).st_mode
            except (OSError, TypeError):
                before = None
            r = f(p, mode, *a, **k)
            if before is None or st(p).st_mode != before:
                note(_fd_path(p) if isinstance(p, int) else p, k.get("dir_fd"))
            return r
        return g

    def w_fd(f):
        def g(fd, *a, **k):
            note(_fd_path(fd))
            return f(fd, *a, **k)
        return g

    def w_kill(f):
        def g(pid, sig):
            if sig != 0:
                out.add(f"kill:{f.__name__}:{pid}:{sig}")
            return f(pid, sig)
        return g

    def w_popen(f):
        def g(self, args, *a, **k):
            out.add("exec:" + (args if isinstance(args, str) else " ".join(map(str, args))))
            return f(self, args, *a, **k)
        return g

    def w_ioctl(f):                 # only a tty request that changes the device
        def g(fd, request, *a, **k):
            if request in _TTY_SETS:
                p = _fd_path(fd)
                out.add(_RECEIVER if p == dev else f"termios:{p}")
            return f(fd, request, *a, **k)
        return g

    def w_tcset(f):
        def g(fd, when, attrs):
            p = _fd_path(fd)
            out.add(_RECEIVER if p == dev else f"termios:{p}")
            return f(fd, when, attrs)
        return g

    n0 = len(fake.calls)
    err = None
    with monkeypatch.context() as m:
        m.setattr(os, "open", w_open(os.open))
        m.setattr(io, "open", w_io(io.open))
        m.setattr(builtins, "open", io.open)
        m.setattr(os, "mkdir", w_mkdir(os.mkdir))
        for name in ("rmdir", "unlink", "remove", "truncate", "utime", "chown", "mkfifo", "mknod"):
            m.setattr(os, name, w_paths("dir_fd")(getattr(os, name)))
        for name in ("rename", "replace"):
            m.setattr(os, name, w_paths("src_dir_fd", "dst_dir_fd")(getattr(os, name)))
        for name in ("symlink", "link"):
            m.setattr(os, name, w_paths(None, "dst_dir_fd")(getattr(os, name)))
        m.setattr(os, "chmod", w_mode(os.chmod, os.stat))
        m.setattr(os, "fchmod", w_mode(os.fchmod, os.fstat))
        m.setattr(os, "ftruncate", w_fd(os.ftruncate))
        m.setattr(os, "kill", w_kill(os.kill))
        m.setattr(os, "killpg", w_kill(os.killpg))
        m.setattr(subprocess.Popen, "__init__", w_popen(subprocess.Popen.__init__))
        m.setattr(termios, "tcsetattr", w_tcset(termios.tcsetattr))
        m.setattr(fcntl, "ioctl", w_ioctl(fcntl.ioctl))
        try:
            fn()
        except Exception as exc:                            # writes before it still count
            err = exc
    out |= {"exec:" + " ".join(c) for c in fake.calls[n0:] if not _read_only_command(c)}
    return out, err


def _matches(token, glob):
    import fnmatch
    return token == glob.rstrip("/") if glob.endswith("/") else fnmatch.fnmatchcase(token, glob)


def _drive_args(svc):
    """A value per required parameter name of a neutral entry (the live fixture's meshtastic)."""
    from lhpc.core.services import ActionResult
    stack = svc.stack("meshtastic")
    comp = stack.main_component
    return {
        "target": "meshtastic", "stack_id": "meshtastic", "sid": "meshtastic",
        "comp_id": "meshtastic", "component_id": "meshtastic", "surface": "meshtastic",
        "band": "868", "op": "start", "job": "rf-meshtastic.log", "name": "region",
        "comp": comp, "c": comp, "stack": stack, "req": comp.requires[0],
        "run_id": "x", "index": 0, "offset": 0, "channel": "0", "label": "x", "page_id": "x",
        "page": (svc.web_pages() or [None])[0], "summary": {}, "selection": {}, "scopes": [],
        "prospective_ports": [], "stack_ids": ["meshtastic"], "values": {}, "rec": {},
        "listeners": [], "port": 4403, "records": [], "scope": {"addr": comp.endpoints[0].address.rsplit(":", 1)[0]},
        "groups": [], "params": {}, "result": ActionResult(True, "x"),
        "sids": ["meshtastic"], "why": "did not build",
    }


# The neutral writers' own calls: the arguments that reach the write (the native RF job).
_DRIVE_KW = {"log_tail": {"job": "rf-meshtastic.log"}, "rflog_records": {"lines": 50},
             "rflog_tail": {"lines": 50}}


def _drive(svc, name):
    """A call of one neutral entry with a value for each required parameter. Nothing of the entry
    runs here: a method is looked up without its descriptor running, and a property is READ by the
    returned call, so a getter that writes runs inside the trace. Any other kind of entry (a
    callable object, a partial, a builtin, a cached_property) fails here, naming it: the trace
    could not prove what it does."""
    entry = inspect.getattr_static(svc, name)
    if isinstance(entry, property):
        return lambda: getattr(svc, name)
    assert isinstance(entry, (types.FunctionType, staticmethod, classmethod)), (
        f"{name}: a {type(entry).__name__} entry — make it a method or a property so the trace "
        "can drive it")
    fn = getattr(svc, name)
    args = _drive_args(svc)
    kw = {}
    for p in inspect.signature(fn).parameters.values():
        if p.kind in (p.VAR_POSITIONAL, p.VAR_KEYWORD) or p.default is not p.empty:
            continue
        assert p.name in args, f"{name}: no drive value for parameter {p.name!r} (add one)"
        kw[p.name] = args[p.name]
    kw.update(_DRIVE_KW.get(name, {}))
    return lambda: fn(**kw)


def test_the_neutral_categories_are_pinned():
    # Every NEUTRAL reason is "read-only", a "neutral writer: …" (with its paths), or one of the
    # pinned read-path writers. Relabelling a writer to any other prose fails here.
    from lhpc.core.snapshot_memo import SNAPSHOT_NEUTRAL, SNAPSHOT_NEUTRAL_WRITERS
    other = {n for n, r in SNAPSHOT_NEUTRAL.items()
             if r != "read-only" and not r.startswith("neutral writer")}
    assert other == _READ_PATH_WRITERS
    assert not _READ_PATH_WRITERS & set(SNAPSHOT_NEUTRAL_WRITERS)


@pytest.mark.skipif(not os.path.isdir("/proc/self/fd"), reason="needs /proc/self/fd")
def test_every_traced_write_is_classified(tmp_path, receiver, monkeypatch):
    """Ties the classification to what the entries DO: each neutral entry (except the pinned
    read-path writers) runs on its own live runtime root under the write trace. A "read-only"
    entry that writes anything fails; a neutral writer must write exactly its listed paths —
    every write matches one of them and each of them is written. Fails on an entry relabelled
    read-only and dropped from SNAPSHOT_NEUTRAL_WRITERS (the original defect's shape), on a
    writer whose writes grew or shrank, on a new public read whose call takes a parameter
    nobody can drive yet, and on an entry that raises: one that fails before it writes would
    pass the write check vacuously."""
    from lhpc.core.snapshot_memo import SNAPSHOT_NEUTRAL, SNAPSHOT_NEUTRAL_WRITERS
    wrong, raised, driven = [], [], set()
    for name in sorted(set(SNAPSHOT_NEUTRAL) - _READ_PATH_WRITERS):
        svc, root, fake = _live(tmp_path / name, receiver, monkeypatch)
        call = _drive(svc, name)
        writes, err = _trace_writes(call, root, receiver, fake, monkeypatch)
        if err is not None:
            raised.append((name, repr(err)))
        driven.add(name)
        globs = SNAPSHOT_NEUTRAL_WRITERS.get(name, ())
        unlisted = sorted(w for w in writes if not any(_matches(w, g) for g in globs))
        unwritten = [g for g in globs if not any(_matches(w, g) for w in writes)]
        if unlisted or unwritten:
            wrong.append((name, SNAPSHOT_NEUTRAL[name], unlisted, unwritten))
    assert set(SNAPSHOT_NEUTRAL_WRITERS) <= driven
    assert not raised, f"(entry, exception) — a raising entry proves nothing: {raised}"
    assert not wrong, f"(entry, label, writes not listed, listed paths not written): {wrong}"


def test_every_neutral_writer_names_its_written_paths():
    # A neutral entry that writes is listed in SNAPSHOT_NEUTRAL_WRITERS with what it writes
    # (the traces check it), and its SNAPSHOT_NEUTRAL reason says so — both directions.
    from lhpc.core.snapshot_memo import SNAPSHOT_NEUTRAL, SNAPSHOT_NEUTRAL_WRITERS
    assert set(SNAPSHOT_NEUTRAL_WRITERS) <= set(SNAPSHOT_NEUTRAL)
    said = {n for n, r in SNAPSHOT_NEUTRAL.items() if r.startswith("neutral writer")}
    assert said == set(SNAPSHOT_NEUTRAL_WRITERS)
    for name, globs in SNAPSHOT_NEUTRAL_WRITERS.items():
        assert globs, f"{name} names no written path"
        for g in globs:
            assert g == _RECEIVER or (g.strip() and not g.startswith("/")
                                      and ".." not in g.split("/")), (name, g)


def _snapshot_inputs(svc, root, dev, fake, monkeypatch):
    """Everything one fresh `build_snapshot()` reads that a neutral writer could write, as
    (kind, relative path or `_RECEIVER`): the os-level opens/stats/listings (dir_fd-relative opens
    resolved through /proc), every path handed to the System's fs and unix backends (the
    prober's own reads), every path named in a command it runs (runner or subprocess), and any
    tty-mode read (tcgetattr/ioctl) or command naming the receiver."""
    import builtins
    import fcntl
    import io
    import subprocess
    import termios

    from lhpc.core.probes.backends import System
    seen = set()

    def note(kind, p, dir_fd=None):
        try:
            p = os.fsdecode(os.fspath(p))
        except TypeError:
            return
        if dir_fd is not None and not os.path.isabs(p):
            p = os.path.join(_fd_path(dir_fd) or "", p)
        p = os.path.normpath(p)
        if p == root or p.startswith(root + os.sep):
            seen.add((kind, os.path.relpath(p, root)))

    def note_argv(argv):
        for arg in map(str, [argv] if isinstance(argv, str) else argv):
            if arg == dev:
                seen.add(("run", _RECEIVER))
            elif os.path.isabs(arg):
                note("run", arg)

    def traced(kind, fn):
        def _f(p=".", *a, **k):
            note(kind, p, k.get("dir_fd"))
            return fn(p, *a, **k)
        return _f

    def tty(fn):
        def _f(fd, *a, **k):
            if _fd_path(fd) == dev:
                seen.add(("termios", _RECEIVER))
            return fn(fd, *a, **k)
        return _f

    class _Backend:
        def __init__(self, inner, kind):
            self._inner, self._kind = inner, kind

        def __getattr__(self, name):
            fn = getattr(self._inner, name)
            if not callable(fn):
                return fn
            kind = "list" if name == "listdir" else self._kind
            return lambda *a, **k: (a and note(kind, a[0]), fn(*a, **k))[1]

    class _Runner:
        def run(self, argv, *a, **k):
            note_argv(argv)
            return fake.run(argv, *a, **k)

    def popen(f):
        def g(self, args, *a, **k):
            note_argv(args)
            return f(self, args, *a, **k)
        return g

    svc._system = System(runner=_Runner(), procfs=fake, fs=_Backend(fake, "fs"),
                         unix=_Backend(fake, "unix"))
    with monkeypatch.context() as m:
        for name in ("open", "stat", "lstat", "access"):
            m.setattr(os, name, traced(name, getattr(os, name)))
        for name in ("listdir", "scandir"):
            m.setattr(os, name, traced("list", getattr(os, name)))
        m.setattr(io, "open", traced("open", io.open))
        m.setattr(builtins, "open", io.open)
        m.setattr(subprocess.Popen, "__init__", popen(subprocess.Popen.__init__))
        m.setattr(termios, "tcgetattr", tty(termios.tcgetattr))
        m.setattr(fcntl, "ioctl", tty(fcntl.ioctl))
        snap = svc.build_snapshot(fresh=True)
    svc._system = fake.system
    return seen, snap


def _observed(seen):
    """(neutral writer, what it writes, input) for each snapshot input that observes a neutral
    write: the path itself, a listing of the directory it is written into, or the receiver."""
    import fnmatch

    from lhpc.core.snapshot_memo import SNAPSHOT_NEUTRAL_WRITERS
    hits = []
    for name, globs in SNAPSHOT_NEUTRAL_WRITERS.items():
        for g in globs:
            pat = g.rstrip("/")
            for kind, p in seen:
                if fnmatch.fnmatchcase(p, pat) or (kind == "list" and p == os.path.dirname(pat)):
                    hits.append((name, g, kind, p))
    return hits


# The neutral writers, each called so that it reaches its write on the live fixture.
_WRITER_CALLS = {
    "gps_monitor": lambda svc: svc.gps_monitor(),
    "log_tail": lambda svc: svc.log_tail("meshtastic", 50, job="rf-meshtastic.log"),
    "rflog_records": lambda svc: svc.rflog_records("meshtastic", "rf-meshtastic.log", 50),
    "rflog_tail": lambda svc: svc.rflog_tail("meshtastic", "", 50),
}


def _stack_state(snap, sid):
    return {c: st.run_state.value for ss in snap.stacks if ss.stack.id == sid
            for c, st in ss.components.items()}


@pytest.mark.skipif(not os.path.isdir("/proc/self/fd"), reason="needs /proc/self/fd")
def test_no_neutral_writer_path_is_a_snapshot_input(tmp_path, receiver, monkeypatch):
    """The neutral writers stay undecorated ONLY because no snapshot input observes what they
    write. On the live fixture the writers run for real (the lock files, the owner record, the
    roll, the receiver's tty mode), then a fresh snapshot is traced: after they ran, and again
    INSIDE each of them while the receiver claim / the roll lock is held. The snapshot assesses
    meshtastic as running (so its per-stack probes run), and the trace covers the os level, the
    System backends, every command it runs and any tty-mode read. Fails the day one of their
    paths, a listing of their directories or the receiver's tty mode becomes a snapshot input,
    or a command the snapshot runs names one of them."""
    from lhpc.core import rflog
    svc, root, fake = _live(tmp_path, receiver, monkeypatch)
    for call in _WRITER_CALLS.values():
        call(svc)
    locks = sorted(os.listdir(os.path.join(root, "state", "locks")))
    assert any(n.startswith("claim.gps.") and n.endswith(".lock") for n in locks), locks
    assert "rflog-rf-meshtastic.log.lock" in locks
    assert os.path.getsize(os.path.join(root, "logs", "rf-meshtastic.log.1")) > 0
    seen, snap = _snapshot_inputs(svc, root, receiver, fake, monkeypatch)
    assert _stack_state(snap, "meshtastic")["meshtastic"] == "running"
    assert seen, "the trace saw nothing: it is not tracing"
    assert not _observed(seen), f"a neutral writer's path is a snapshot input: {_observed(seen)}"

    held = []                                    # the same trace, taken while each lock is held

    def inside(owner, name):
        orig = getattr(owner, name)

        def _f(self, *a, **k):
            held.append((name, sorted(os.listdir(os.path.join(root, "state", "locks"))),
                         _snapshot_inputs(svc, root, receiver, fake, monkeypatch)[0]))
            return orig(self, *a, **k)
        monkeypatch.setattr(owner, name, _f)
    inside(ControllerService, "_gps_read_device")
    inside(ControllerService, "_rflog_roll_native_locked")
    (tmp_path / "logs" / "rf-meshtastic.log").write_bytes(b'{"x":1}\n' * (2 * rflog.MAX_BYTES // 8))
    _WRITER_CALLS["gps_monitor"](svc)
    _WRITER_CALLS["log_tail"](svc)
    assert [h[0] for h in held] == ["_gps_read_device", "_rflog_roll_native_locked"]
    assert any(n.endswith(".owner") for n in held[0][1]), held[0][1]   # the claim really held
    for name, _locks, seen in held:
        assert seen and not _observed(seen), (name, _observed(seen))


def _open_receiver(svc):
    fd = os.open(svc.gps_settings()["device"], os.O_RDONLY | os.O_NOCTTY | os.O_NONBLOCK)
    try:
        import termios
        termios.tcgetattr(fd)
    finally:
        os.close(fd)


@pytest.mark.skipif(not os.path.isdir("/proc/self/fd"), reason="needs /proc/self/fd")
@pytest.mark.parametrize("read, writer", [
    (lambda svc: reslock.read_owner(svc._paths, "claim.gps.serial.dev.188:0"), "gps_monitor"),
    (lambda svc: svc._system.fs.exists(str(svc._paths.under("logs", "rf-meshtastic.log.1"))),
     "log_tail"),
    (lambda svc: svc._system.fs.listdir(str(svc._paths.under("state", "locks")), 50), "gps_monitor"),
    (_open_receiver, "gps_monitor"),
    (lambda svc: svc._system.runner.run(["stty", "-F", svc.gps_settings()["device"]], 1.0),
     "gps_monitor"),
    (lambda svc: svc._system.runner.run(["tail", str(svc._paths.under("logs", "rf-meshtastic.log"))],
                                        1.0), "log_tail"),
])
def test_the_snapshot_input_trace_catches_a_neutral_writer_path(tmp_path, receiver, monkeypatch,
                                                               read, writer):
    # The guard above is not vacuous: a snapshot that starts reading a neutral writer's path —
    # through runtime_fs, the prober's fs backend, a listing of its directory, the receiver's tty
    # mode, or a command naming one of them — is caught and attributed to that writer.
    svc, root, fake = _live(tmp_path, receiver, monkeypatch)
    orig = ControllerService._overlay_gui_unavailable
    monkeypatch.setattr(ControllerService, "_overlay_gui_unavailable",
                        lambda self, snap: (read(self), orig(self, snap))[1])
    seen, _snap = _snapshot_inputs(svc, root, receiver, fake, monkeypatch)
    assert writer in {h[0] for h in _observed(seen)}


@pytest.mark.skipif(not os.path.isdir("/proc/self/fd"), reason="needs /proc/self/fd")
@pytest.mark.parametrize("label, writes", [
    ("read-only", None),                                          # relabelled, map entry dropped
    ("neutral writer: x", ("state/locks/", "state/locks/claim.gps.*.lock")),   # paths shrank
])
def test_the_write_trace_catches_a_mislabelled_writer(tmp_path, receiver, monkeypatch, label, writes):
    # The classification check is not vacuous: gps_monitor relabelled read-only (and dropped
    # from the writers map), or listed with fewer paths than it writes, is reported.
    from lhpc.core import snapshot_memo
    monkeypatch.setitem(snapshot_memo.SNAPSHOT_NEUTRAL, "gps_monitor", label)
    if writes is None:
        monkeypatch.delitem(snapshot_memo.SNAPSHOT_NEUTRAL_WRITERS, "gps_monitor", raising=False)
    else:
        monkeypatch.setitem(snapshot_memo.SNAPSHOT_NEUTRAL_WRITERS, "gps_monitor", writes)
    svc, root, fake = _live(tmp_path, receiver, monkeypatch)
    w, _err = _trace_writes(_drive(svc, "gps_monitor"), root, receiver, fake, monkeypatch)
    globs = snapshot_memo.SNAPSHOT_NEUTRAL_WRITERS.get("gps_monitor", ())
    assert [x for x in w if not any(_matches(x, g) for g in globs)]


@pytest.mark.skipif(not os.path.isdir("/proc/self/fd"), reason="needs /proc/self/fd")
def test_the_write_trace_catches_a_writing_property(tmp_path, receiver, monkeypatch):
    # A neutral entry that is a property is read INSIDE the trace: a getter that writes is
    # reported, never read before the trace starts and then skipped as "nothing to call".
    svc, root, fake = _live(tmp_path, receiver, monkeypatch)

    def getter(self):
        with open(os.path.join(root, "state", "written-by-a-getter"), "w") as fh:
            fh.write("x")
        return "value"
    monkeypatch.setattr(ControllerService, "writing_getter", property(getter), raising=False)
    writes, err = _trace_writes(_drive(svc, "writing_getter"), root, receiver, fake, monkeypatch)
    assert err is None and writes == {"state/written-by-a-getter"}


@pytest.mark.skipif(not os.path.isdir("/proc/self/fd"), reason="needs /proc/self/fd")
@pytest.mark.parametrize("request_name, expected", [("TCSETS", {_RECEIVER}), ("TCGETS", set())])
def test_the_write_trace_catches_a_mutating_tty_ioctl(tmp_path, receiver, monkeypatch,
                                                     request_name, expected):
    # The receiver's tty mode set through fcntl.ioctl (TCSETS), not termios.tcsetattr, is a write;
    # the read (TCGETS) alone is not.
    import fcntl
    import termios
    svc, root, fake = _live(tmp_path, receiver, monkeypatch)

    def set_mode():
        fd = os.open(receiver, os.O_RDONLY | os.O_NOCTTY | os.O_NONBLOCK)
        try:
            mode = fcntl.ioctl(fd, termios.TCGETS, bytes(64))
            if request_name != "TCGETS":
                fcntl.ioctl(fd, getattr(termios, request_name), mode)
        finally:
            os.close(fd)
    writes, err = _trace_writes(set_mode, root, receiver, fake, monkeypatch)
    assert err is None and writes == expected
