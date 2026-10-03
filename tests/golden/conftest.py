"""Shared fixtures of the golden (characterization) set — see tests/README.md, `golden/`.

A golden case records what one coordinating operation does TODAY, for one fixed scenario: the
result fields, the files it wrote/removed under the runtime root, and the ORDER of its phases
(admission → locks → recheck → mutation → verification → finalization). Each case is tagged in its
docstring: "intended" (the behaviour is the contract) or "known defect <id>" (recorded as is).

The order is observed through the seams named in `ORDER_SEAMS` below — the ONE place a refactor
that renames a coordinator step updates. That is the golden set's sanctioned exception to rule 1
(no pinning helper names): pinning the step order is its purpose, and it pins it here only.
"""

from __future__ import annotations

import contextlib
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from lhpc.core import boot_restore, reslock, restart_required
from lhpc.core import config as cfgmod
from lhpc.core import lifecycle as lifecycle_mod
from lhpc.core.lifecycle import Lifecycle
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem, Listener
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
    `known defect <finding id>:`."""
    doc = (request.function.__doc__ or "").strip()
    assert re.match(r"(intended|known defect [\w-]+):", doc), (
        f"{request.node.name}: a golden case's docstring must open with 'intended:' or "
        "'known defect <id>:'")


# --- the one box the lifecycle goldens run on ----------------------------------------------------
# kiss over a daemon already serving 433: the daemon is not spawned (only its CONF SETs run), the
# TNC is endpoint-ready on 127.0.0.1:8001 and spawns a real detached `sleep` (ownership needs a
# complete /proc identity), so start → stop → restart run their real coordinators end to end.

_READY = b"STATUS RADIO=READY TX=0 TXMODE=MANAGED CADWAIT=1500 CADRSSI=-90\n"


def _alive(pid: int) -> bool:
    try:
        with open(f"/proc/{pid}/stat") as f:
            return f.read().rsplit(")", 1)[1].split()[0] not in ("Z", "X")
    except (OSError, IndexError):
        return False


class _TncEndpoint:
    """The TNC's ready listener 127.0.0.1:8001, present exactly while an LHPC-owned TNC process is
    alive — the real readiness evidence, so a stop sees it vanish and a start sees it appear.
    `up=False` models a TNC that never opens its port."""

    def __init__(self, root: Path):
        self.root, self.up = Path(root), True

    def __iter__(self):
        d = self.root / "state" / "owned"
        live = self.up and d.is_dir() and any(
            _alive(json.loads(p.read_text())["pid"]) for p in d.glob("loraham-kiss-tnc__*.json"))
        return iter([Listener(family="ipv4", ip="127.0.0.1", port=8001, inode=1)] if live else [])


class KissBox:
    def __init__(self, root: Path, fake: FakeSystem, svc: ControllerService):
        self.root, self.fake, self.svc = root, fake, svc

    def endpoint_never_up(self) -> None:
        self.fake.listeners.up = False

    def owned(self) -> list[str]:
        return owned(self.root)

    def live(self) -> list[str]:
        """The components whose LHPC-owned process is alive (a prior boot's record is not)."""
        d = self.root / "state" / "owned"
        recs = [json.loads(p.read_text()) for p in d.glob("*.json")] if d.is_dir() else []
        return sorted(r["component"] for r in recs if _alive(r["pid"]))


@pytest.fixture
def kiss_box(tmp_path, monkeypatch, real_spawn, set_call):
    """`kiss_box(callsign=True, root=tmp_path)` → a KissBox: kiss installed and built, daemon
    READY on 433, the TNC's endpoint following its process, a callsign saved (unless
    callsign=False)."""
    def _make(*, callsign=True, root=None):
        root = Path(root or tmp_path)
        (root / "src" / "loraham-kiss-tnc").mkdir(parents=True)
        (root / "src" / "loraham-kiss-tnc" / "loraham-kiss-tnc").write_text("#bin")
        fake = FakeSystem(unix_replies={"/tmp/loraconf433.sock": _READY})
        fake.listeners = _TncEndpoint(root)
        svc = ControllerService(system=fake.system, paths=Paths(runtime_root=root))
        svc.bootstrap(apply=True)
        monkeypatch.setattr(ControllerService, "_lifecycle", lambda s: Lifecycle(
            s._paths, s.stacks(), s.config(), s._system, spawn=real_spawn))
        if callsign:
            set_call(svc)
        return KissBox(root, fake, svc)
    return _make


@pytest.fixture
def prior_boot(monkeypatch):
    """`prior_boot(root, stack="kiss", comp="loraham-kiss-tnc", band="433")` → the path of an
    ownership record a PREVIOUS boot left behind (its process gone), the evidence boot-restore
    replays; this boot's id is pinned, restore is enabled and the web integration reads proven
    (the two host gates boot-restore checks before it plans)."""
    from lhpc.core import service_boot_restore as sbr
    monkeypatch.setattr(sbr, "current_boot_id", lambda: "CURBOOT")
    monkeypatch.setattr(ControllerService, "_web_integration_proven", lambda self: (True, ""))
    monkeypatch.setattr(ControllerService, "boot_restore_enabled", lambda self: (True, ""))

    def _plant(root, stack="kiss", comp="loraham-kiss-tnc", band="433", pid=999999):
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
    """`with held_lock(paths, key):` holds that operation lock from ANOTHER process, so the
    contention is real (an in-process holder is waited on, not refused)."""
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
        yield
    finally:
        p.stdin.close()
        p.wait(timeout=10)
