"""Shared fixtures of the golden (characterization) set — see tests/README.md, `golden/`.

A golden case records what one coordinating operation does TODAY, for one fixed scenario: the
result fields, the files it wrote/removed under the runtime root, and the ORDER of its phases
(admission → locks → recheck → mutation → verification → finalization). Each case is tagged in its
docstring: "intended" (the behaviour is the contract) or "known defect <id>" (recorded as is).

The order is observed through the seams named in `ORDER_SEAMS` below — the ONE place a refactor
that renames a coordinator step updates. That is the golden set's sanctioned exception to rule 1
(no pinning helper names): pinning the step order is its purpose, and it pins it here only.

What is real and what is substituted. The TNC's readiness and cessation collaborators are not
stubbed: the TNC is a real process, its ready endpoint the listener it really opens (read from
the kernel), its cessation the real one. The substitutions, every one by name:
- `ControllerService._lifecycle` (`kiss_box`): the real `Lifecycle` with the box's spawn
  callable, which launches the TNC process instead of the component binary;
- the `System`: a `FakeSystem` (daemon CONF socket answering READY; `listeners` =
  `_TncEndpoint`, the kernel's table filtered to the TNC's port, reported as 8001), and the
  host data a scenario puts into it: process-table entries (`cmdlines_data`: a band owner, an
  unowned TNC, a running chat) and the build steps' command results (`commands`, build);
- two bounded waits, shortened: `ControllerService.ENDPOINT_VERIFY_TIMEOUT_S` 3 s (production
  6 s), `Lifecycle.STOP_WAIT_S` 1 s in the two outlives-SIGTERM cases (production 5 s);
- `prior_boot`: `LHPC_BOOT_ID_FILE` (the production boot-id override) — the two boot-restore host
  gates are arranged on disk, not stubbed;
- fault injection and probes in single cases: `config._atomic_write` failing its third write
  (save_config_bundle `test_failed_write_rolls_back`), a delegating `runtime_fs.atomic_write`
  that records the build's stamps (build);
- every `ORDER_SEAMS` wrapper (delegating, `phases`);
- the suite's autouse isolation (tests/conftest.py): `_fw_integration_state`,
  `_fw_units_enabled`, `firewall.RECEIPT_PATH`, `updater_units._SYSTEM_ROOTS`, HOME/XDG,
  `config.HW_DEFAULT`, `display_available`, the `RealProcFs` reads, `gps.local_gpsd_listening`,
  `read_kernel_time_state`, the binary-download refusal, `DAEMON_VERIFY_TIMEOUT_S` and
  `Lifecycle.OBSERVE_TIMEOUT_S` (0 s).
"""

from __future__ import annotations

import contextlib
import dataclasses
import json
import os
import re
import signal
import socket
import subprocess
import sys
from pathlib import Path

import pytest

from lhpc.core import boot_restore, reslock, restart_required
from lhpc.core import config as cfgmod
from lhpc.core import lifecycle as lifecycle_mod
from lhpc.core.lifecycle import Lifecycle
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem, parse_proc_net_tcp
from lhpc.core.services import ControllerService


def _rel(paths, path) -> str:
    return str(Path(path).relative_to(paths.runtime_root))


def _journal(paths, journal) -> str:
    items = ",".join(f"{i.get('target')}={i.get('state')}" for i in journal.get("items") or [])
    return f"journal:{journal.get('state')}[{items}]"


# (owner, attribute, phase label). The label is a format string whose "{0}"/"{1}" take the call's
# first positional arguments after self (stringified: an object's `.id`, else str), e.g. the lock
# key or the component — or a callable of those arguments.
ORDER_SEAMS = (
    (ControllerService, "_admission_guard", "admission"),
    (ControllerService, "_admit", "admission"),
    (ControllerService, "_config_stable", "config-stable"),
    (ControllerService, "_acquire_key", "lock:{1}"),
    (reslock, "operation_lock", "lock:{1}"),
    (ControllerService, "_start_outer_refusal", "recheck:start"),
    (ControllerService, "_identity_refusal", "recheck:identity"),
    (ControllerService, "_start_preflight_refusal", "recheck:preflight"),
    (ControllerService, "clear_daemon_feed", "mutate:feed-floor:{0}"),
    (ControllerService, "write_config_files", "mutate:config-files:{0}"),
    (Lifecycle, "start", "mutate:spawn:{1}"),
    (Lifecycle, "stop", "mutate:signal:{0}"),
    (Lifecycle, "_invalidate_build_marker", "mutate:invalidate-marker"),
    (lifecycle_mod, "run_job", "mutate:build-step"),
    (ControllerService, "_verify_band_up", "verify:conf:{0}"),
    (ControllerService, "_ready_endpoints_present", "verify:endpoints:{0}"),
    (ControllerService, "_run_post_start", "verify:post-start"),
    (ControllerService, "_set_running_band", "final:running-band:{1}"),
    (ControllerService, "_capture_start_composition", "final:known-working:{0}"),
    (restart_required, "clear_marker", "final:clear-restart-marker:{1}"),
    (ControllerService, "_clear_stop_intent", "final:clear-stop-intent:{0}"),
    (ControllerService, "_write_stop_intent", "final:stop-intent:{0}"),
    # config transaction (save_config_bundle)
    (cfgmod, "config_lock", "lock:config"),
    (cfgmod, "_finish_pending_journal", "recheck:config-journal"),
    (cfgmod, "_atomic_write", lambda paths, path, *a: f"mutate:write:{_rel(paths, path)}"),
    # boot-restore journal (each durable write, with its run and item states)
    (boot_restore, "write_journal", _journal),
)

# Phases whose repeats are not behaviour (re-entrant guards, a recheck run twice): recorded on
# their first occurrence only. Mutations, verifications and finalizations are recorded each time.
_ONCE = ("admission", "lock", "config-stable", "recheck")


def _name(x) -> str:
    return getattr(x, "id", None) or str(x)


@pytest.fixture
def phases(monkeypatch):
    """The ordered phase log of the operation under test: every `ORDER_SEAMS` call, delegating to
    the real implementation. Admission, lock and recheck labels are recorded on their FIRST
    occurrence only (`_ONCE`). Call `phases.clear()` after the scenario's set-up."""
    log: list[str] = []

    def _wrap(owner, attr, label):
        real = getattr(owner, attr)
        is_method = isinstance(owner, type)

        def wrapper(*args, **kwargs):
            pos = args[1:] if is_method else args
            text = (label(*pos) if callable(label)
                    else label.format(*[_name(a) for a in pos], *([""] * 3)))
            if not (text.split(":", 1)[0] in _ONCE and text in log):
                log.append(text)
            return real(*args, **kwargs)
        monkeypatch.setattr(owner, attr, wrapper)

    for owner, attr, label in ORDER_SEAMS:
        _wrap(owner, attr, label)
    return log


def phase_kinds(log):
    """The phase sequence with consecutive repeats folded: ['admission', 'lock', 'recheck', …]."""
    out: list[str] = []
    for e in log:
        k = e.split(":", 1)[0]
        if k == "config-stable":
            k = "lock"
        if not out or out[-1] != k:
            out.append(k)
    return out


_OWNED = re.compile(r"__\d+__[0-9a-f]{32}\.json$")


def tree(root: Path) -> dict[str, str]:
    """Every file under `root` → its text (or '<bytes>'), runtime-relative; an ownership record's
    pid and nonce read `<pid>__<nonce>`. Lock files are left out: which locks an operation takes,
    and when, is the `phases` log's job (a lock file outlives its lock by design)."""
    out = {}
    for p in sorted(Path(root).rglob("*")):
        rel = str(p.relative_to(root))
        if not p.is_file() or p.is_symlink() or rel.startswith("state/locks/") or rel == "config/.lock":
            continue
        try:
            text = p.read_text()
        except (UnicodeDecodeError, OSError):
            text = "<bytes>"
        out[_OWNED.sub("__<pid>__<nonce>.json", rel)] = text
    return out


def tree_diff(before: dict, after: dict) -> dict:
    """{'added': [...], 'removed': [...], 'changed': [...]} — sorted runtime-relative paths."""
    return {
        "added": sorted(set(after) - set(before)),
        "removed": sorted(set(before) - set(after)),
        "changed": sorted(k for k in set(before) & set(after) if before[k] != after[k]),
    }


def owned(root: Path) -> list[str]:
    """The components with an ownership record under state/owned (sorted)."""
    d = Path(root) / "state" / "owned"
    return sorted(json.loads(p.read_text())["component"] for p in d.glob("*.json")) if d.is_dir() else []


_HEAD = re.compile(r"^\s*\[([^\]]+)\]\s*([^\s:]*)")
_DAEMON_PARAM = re.compile(r"^\s*\[(ok|warn)\] (433|868): ")


def result_fields(res) -> dict:
    """The decision-bearing fields of an ActionResult, for an exact comparison: `heads` is each
    detail line's `[tag] subject` (paths and prose dropped; the daemon's per-parameter push lines
    are owned by tests/stacks/test_daemon_params.py and left out)."""
    heads = []
    for d in res.details:
        m = _HEAD.match(d)
        if m and not _DAEMON_PARAM.match(d):
            heads.append(f"[{m.group(1)}] {m.group(2)}".strip())
    return {"ok": res.ok, "summary": res.summary, "data_keys": sorted(res.data or {}),
            "next_commands": list(res.next_commands), "heads": heads,
            "outcomes": [(r.component, r.outcome.value) for r in res.results]}


class Run:
    """One operation run: `res` (the ActionResult), `fields` (`result_fields`), `files` (the
    `tree_diff` it caused), `phases` (a copy of the phase log it produced) and `kinds` (that
    log folded to its phase sequence)."""

    def __init__(self, res, files, log):
        self.res, self.fields, self.files, self.phases = res, result_fields(res), files, list(log)
        self.kinds = phase_kinds(log)


@pytest.fixture
def run_op(phases):
    """`run_op(root, fn)` → Run: clears the phase log, calls `fn()`, and records what changed."""
    def _run(root, fn):
        phases.clear()
        before = tree(root)
        res = fn()
        return Run(res, tree_diff(before, tree(root)), phases)
    return _run


@pytest.fixture(autouse=True)
def _tagged(request):
    """Every golden case says what it records: its docstring opens with `intended:` or
    `known defect <finding id>[, <finding id>…]:` (one id per difference it records)."""
    doc = (request.function.__doc__ or "").strip()
    assert re.match(r"(intended|known defect [\w-]+(, [\w-]+)*):", doc), (
        f"{request.node.name}: a golden case's docstring must open with 'intended:' or "
        "'known defect <id>[, <id>…]:'")


# --- the one box the lifecycle goldens run on ----------------------------------------------------
# kiss over a daemon already serving 433: the daemon is not spawned (only its CONF SETs run); the
# TNC is a real detached process (ownership needs a complete /proc identity) that opens a real
# loopback listener, so start → stop → restart run their real coordinators end to end and the TNC's
# readiness and cessation they report are observed, not configured (the substitutions that remain
# are listed in the module docstring).

_READY = b"STATUS RADIO=READY TX=0 TXMODE=MANAGED CADWAIT=1500 CADRSSI=-90\n"

# The TNC process the box spawns: argv[1] is the loopback port it listens on (0: it never opens
# one), argv[2] "ignore" makes it ignore SIGTERM (a process that really outlives its stop signal).
_TNC = ("import signal, socket, sys, time\n"
        "port, term = int(sys.argv[1]), sys.argv[2]\n"
        "if term == 'ignore':\n"
        "    signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        "if port:\n"
        "    s = socket.socket()\n"
        "    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)\n"
        "    s.bind(('127.0.0.1', port))\n"
        "    s.listen()\n"
        "time.sleep(300)\n")


def _alive(pid: int) -> bool:
    try:
        with open(f"/proc/{pid}/stat") as f:
            return f.read().rsplit(")", 1)[1].split()[0] not in ("Z", "X")
    except (OSError, IndexError):
        return False


def _starttime(pid: int) -> str | None:
    """The process's start time (/proc/<pid>/stat field 22): with the pid, its identity."""
    try:
        with open(f"/proc/{pid}/stat") as f:
            return f.read().rsplit(")", 1)[1].split()[19]
    except (OSError, IndexError):
        return None


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _TncEndpoint:
    """The TNC's ready listener as the kernel reports it: the host's real `/proc/net/tcp` table
    (ipv4 — the endpoint is 127.0.0.1), parsed by the production parser `parse_proc_net_tcp` (the
    suite's `_no_host_processes` blanks `RealProcFs.tcp_listeners` itself), keeping only the LISTEN
    socket on the loopback port this box's TNC process binds — no other host listener is seen.
    The manifest pins the endpoint to 127.0.0.1:8001 and a test cannot own a fixed host port, so
    that one port number is reported as 8001 — the only translation; whether the listener exists,
    its host and its LISTEN state are observed. A TNC that never opens its port, or one whose
    socket closed with its process, is simply absent."""

    def __init__(self, port: int):
        self.port = port

    def __iter__(self):
        with open("/proc/net/tcp", encoding="ascii", errors="replace") as fh:
            table = parse_proc_net_tcp(fh.read(), "ipv4")
        return iter([dataclasses.replace(lst, port=8001) for lst in table
                     if (lst.family, lst.ip, lst.port) == ("ipv4", "127.0.0.1", self.port)])


class KissBox:
    def __init__(self, root: Path, fake: FakeSystem, svc: ControllerService, port: int):
        self.root, self.fake, self.svc, self.port = root, fake, svc, port
        self.listens, self.term = True, "default"
        self.spawned: list[tuple[int, str | None, str]] = []   # (pid, start time, component)

    def endpoint_never_up(self) -> None:
        """The next TNC this box spawns stays alive but never opens its port."""
        self.listens = False

    def survives_sigterm(self) -> None:
        """The next TNC this box spawns ignores SIGTERM: it really outlives a stop's signal."""
        self.term = "ignore"

    def spawn(self, procs: list):
        """The `spawn` callable the box's Lifecycle uses: a real detached TNC process (its own
        session, so it is an LHPC-ownable session leader); the log path is created so callers
        that read it work."""
        def _spawn(argv, log, cwd=None, env=None):
            open(str(log), "a").close()
            p = subprocess.Popen(
                [sys.executable, "-c", _TNC, str(self.port if self.listens else 0), self.term],
                start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            procs.append(p)
            self.spawned.append((p.pid, _starttime(p.pid), Path(argv[0]).name))
            return p.pid
        return _spawn

    def owned(self) -> list[str]:
        return owned(self.root)

    def live(self, also=()) -> list[str]:
        """The component of every process this box spawned — and of `also`, the (pid, start
        time, component) of those spawned for it in another process — that is still alive,
        checked by pid and start time in /proc (a zombie is not alive). The ownership records
        are not consulted: a process left running after its record was removed still counts."""
        return sorted(c for pid, st, c in [*self.spawned, *also]
                      if _alive(pid) and _starttime(pid) == st)

    def tnc_alive(self) -> bool:
        """The TNC's owned process is alive (observed in /proc: a zombie is not)."""
        d = self.root / "state" / "owned"
        return any(_alive(json.loads(p.read_text())["pid"])
                   for p in d.glob("loraham-kiss-tnc__*.json"))


@pytest.fixture
def kiss_box(tmp_path, monkeypatch, set_call):
    """`kiss_box(callsign=True, root=tmp_path)` → a KissBox: kiss installed and built, daemon
    READY on 433, a callsign saved (unless callsign=False); the TNC is a real process listening on
    a real loopback port (`_TncEndpoint`). The start's endpoint wait is bounded at 3 s (production: 6 s) — what it
    sees is observed. Every TNC process the box spawned is killed (its session) at teardown."""
    procs: list = []
    monkeypatch.setattr(ControllerService, "ENDPOINT_VERIFY_TIMEOUT_S", 3.0)

    def _make(*, callsign=True, root=None):
        root = Path(root or tmp_path)
        (root / "src" / "loraham-kiss-tnc").mkdir(parents=True)
        (root / "src" / "loraham-kiss-tnc" / "loraham-kiss-tnc").write_text("#bin")
        fake = FakeSystem(unix_replies={"/tmp/loraconf433.sock": _READY})
        port = _free_port()
        fake.listeners = _TncEndpoint(port)
        svc = ControllerService(system=fake.system, paths=Paths(runtime_root=root))
        seeded = svc.bootstrap(apply=True)
        assert seeded.ok, f"seeding: bootstrap failed: {seeded.summary}"
        box = KissBox(root, fake, svc, port)
        spawn = box.spawn(procs)
        monkeypatch.setattr(ControllerService, "_lifecycle", lambda s: Lifecycle(
            s._paths, s.stacks(), s.config(), s._system, spawn=spawn))
        if callsign:
            set_call(svc)
        return box
    yield _make
    for p in procs:
        with contextlib.suppress(OSError):
            os.killpg(p.pid, signal.SIGKILL)
        with contextlib.suppress(subprocess.TimeoutExpired):
            p.wait(timeout=5)


@pytest.fixture
def prior_boot(monkeypatch, tmp_path_factory):
    """`prior_boot(root, stack="kiss", comp="loraham-kiss-tnc", band="433", evidence=True)` → the
    path of an ownership record a PREVIOUS boot left behind (its process gone), the evidence
    boot-restore replays (`evidence=False`: none, only the host below). Nothing boot-restore asks
    is stubbed; each answer is arranged the production way: this boot's id through
    `LHPC_BOOT_ID_FILE` (the production override every reader honours), restore enabled by the
    default `[boot] restore = true` (no `[boot]` table is written), and the web integration proven
    by the canonical `lhpc-web.service` (`updater_units.render`) with its `default.target.wants`
    link in the test's isolated HOME, rendered for `root`."""
    from lhpc.core import updater_units
    boot = tmp_path_factory.mktemp("boot") / "boot_id"
    boot.write_text("CURBOOT\n")
    monkeypatch.setenv("LHPC_BOOT_ID_FILE", str(boot))

    def _plant(root, stack="kiss", comp="loraham-kiss-tnc", band="433", pid=999999,
               evidence=True):
        units = Path(os.environ["HOME"]) / ".config" / "systemd" / "user"
        (units / "default.target.wants").mkdir(parents=True, exist_ok=True)
        _r, checkout, venv = updater_units.deployment_paths(str(root))
        (units / updater_units.WEB_UNIT).write_text(
            updater_units.render(updater_units.WEB_UNIT, str(root), checkout, venv))
        link = units / "default.target.wants" / updater_units.WEB_UNIT
        if not link.is_symlink():
            link.symlink_to(units / updater_units.WEB_UNIT)
        if not evidence:
            return None
        rec = {"launch_id": f"{comp}__{band or 'x'}__{pid}__{'ab' * 16}", "stack": stack,
               "component": comp, "band": band, "pid": pid, "role": "", "launched_at": 1000,
               "version": 1, "requested_target": stack, "start_scope": "stack",
               "boot_id": "OLDBOOT", "starttime": "123", "pgid": pid, "sid": pid}
        d = Path(root) / "state" / "owned"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{rec['launch_id']}.json").write_text(json.dumps(rec))
        return d / f"{rec['launch_id']}.json"
    return _plant


@pytest.fixture
def uninstall_guard():
    """`uninstall_guard(root)` plants the uninstall guard file, which closes task admission."""
    from lhpc.core import updater_units

    def _plant(root):
        (Path(root) / updater_units.UNINSTALL_GUARD).write_text('{"pid": 1, "nonce": "x"}')
    return _plant


@pytest.fixture
def interrupted_install():
    """`interrupted_install(root)` leaves an unresolved source-transaction journal behind, as a
    crashed install/update does; it blocks every source-touching operation until resolved."""
    def _plant(root):
        d = Path(root) / "state" / "source-txn"
        d.mkdir(parents=True, exist_ok=True)
        (d / "garbage.json").write_text("{ not valid")
    return _plant


@pytest.fixture
def held_lock():
    """`with held_lock(paths, key) as holder:` holds that operation lock from ANOTHER process
    (`holder`: its pid, which the refusal names), so the contention is real (an in-process
    holder is waited on, not refused)."""
    return _held_lock


@contextlib.contextmanager
def _held_lock(paths, key):
    root = str(paths.runtime_root)
    code = ("import sys\nfrom pathlib import Path\nfrom lhpc.core import reslock\n"
            "from lhpc.core.paths import Paths\n"
            f"with reslock.operation_lock(Paths(runtime_root=Path({root!r})), {key!r}, 'golden', 'x'):\n"
            "    print('held', flush=True); sys.stdin.read()\n")
    p = subprocess.Popen([sys.executable, "-c", code], stdin=subprocess.PIPE,
                         stdout=subprocess.PIPE, text=True)
    try:
        assert p.stdout.readline().strip() == "held"
        yield p.pid
    finally:
        p.stdin.close()
        p.wait(timeout=10)
