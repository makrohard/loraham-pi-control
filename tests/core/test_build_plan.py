"""The one build plan (`lhpc/core/build_plan.py`): the CLI build and the console's detached build
select, lock and record the same thing. A build holds every source it consumes — its own and each
`build_requires` dependency's — for its whole run, and its receipt records the revisions read
while it holds them.

The detached path runs the real `spawn_web_job` up to the launcher it renders; the launcher's
spec is then run in process (`build_launcher_runtime.run`) with the web gate left out (its
tracking handshake is proven in tests/web/test_webjob.py) and each step replaced by a fake of
`_run_step` with its real signature, so both paths reach their steps holding their real locks."""

import ast
import fcntl
import os
import subprocess

import pytest

from lhpc.core import build_launcher_runtime as blr
from lhpc.core import progress, reslock
from lhpc.core.lifecycle import Lifecycle
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import CommandResult, FakeSystem
from lhpc.core.services import ControllerService

MESHCORE_SOURCES = ("src/openhop-core", "src/openhop-repeater", "src/meshcore-webui",
                    "src/meshcore-cli")


def _git(src, *args):
    return subprocess.run(["git", "-C", str(src), *args], capture_output=True, text=True,
                          check=True).stdout.strip()


def _commit(src):
    """A new commit in `src` (a git repository is made on first use); returns its revision."""
    if not (src / ".git").exists():
        src.mkdir(parents=True, exist_ok=True)
        _git(src, "init", "-q")
    _git(src, "-c", "user.name=lab", "-c", "user.email=lab", "commit", "-q", "--allow-empty",
         "-m", "rev")
    return _git(src, "rev-parse", "HEAD")


class _Commands(dict):
    """The box's command table: `git` runs for real (the receipt reads real revisions), a
    `systemctl` probe answers inactive, every build step succeeds after `on_step` ran."""

    on_step = staticmethod(lambda: None)

    def get(self, argv, default=None):
        if argv[0] == "git":
            r = subprocess.run(list(argv), capture_output=True, text=True, check=False)
            return CommandResult(r.returncode, r.stdout, r.stderr)
        if argv[0] == "systemctl":
            return CommandResult(3, "inactive\n", "")
        self.on_step()
        return CommandResult(0, "ok\n", "")


def _box(tmp_path, on_step=lambda: None):
    root = tmp_path / "rt"
    cmds = _Commands()
    svc = ControllerService(system=FakeSystem(commands=cmds).system,
                            paths=Paths(runtime_root=root))
    assert svc.bootstrap(apply=True).ok
    cmds.on_step = on_step
    return svc, root


def _locked(svc):
    """The meshcore sources whose lock another operation cannot take right now."""
    held = []
    for sp in MESHCORE_SOURCES:
        try:
            with reslock.operation_lock(svc._paths, reslock.source_lock_key(sp), "update", "probe"):
                pass
        except reslock.ResourceBusy:
            held.append(sp)
    return held


def _cli(svc, target, monkeypatch, before_run=None):
    if before_run:
        before_run()
    return svc.build(target, apply=True)


def _background(svc, target, monkeypatch, before_run=None):
    """`spawn_web_job` up to its launcher; the launcher's spec then runs in process."""
    on_step = svc._system.runner.commands.on_step
    def run_step(argv, cwd, env, timeout, stall_s=None, sample_s=progress.SAMPLE_S):
        on_step()
        return 0, "", False

    def spawn_job(self, name, argv, cwd, env=None):
        text = open(argv[-1]).read()
        spec = ast.literal_eval(text[text.index("run(") + 4:text.rindex(")")])
        spec["result_name"] = ""              # the web gate: tests/web/test_webjob.py
        if before_run:
            before_run()
        blr.run(spec)
        return f"{name}.log", 0               # "could not start": nothing else spawns
    monkeypatch.setattr(blr, "_run_step", run_step)
    monkeypatch.setattr(Lifecycle, "spawn_job", spawn_job)
    return svc.spawn_web_job("build", target)


PATHS = pytest.mark.parametrize("build", [_cli, _background], ids=["cli", "background"])


def _meshcore_sources(root):
    for sp in MESHCORE_SOURCES:
        (root / sp).mkdir(parents=True, exist_ok=True)
    return _commit(root / "src/openhop-core"), _commit(root / "src/openhop-repeater")


@PATHS
def test_both_paths_lock_and_record_the_same(tmp_path, monkeypatch, build):
    """meshcore-node consumes openhop-repeater-src (a checkout without build steps): on either
    path its step runs holding both sources, and the receipt records both revisions — the one
    `is_built` then accepts."""
    seen = []
    svc, root = _box(tmp_path, lambda: seen.append(_locked(svc)))
    own, dep = _meshcore_sources(root)
    build(svc, "meshcore-node", monkeypatch)
    assert seen and all(s == ["src/openhop-core", "src/openhop-repeater"] for s in seen)
    marker = root / "src/openhop-core/.venv/.lhpc-build-complete"
    assert marker.read_text() == (f"lhpc build complete\nconsumed meshcore-node {own}\n"
                                  f"consumed openhop-repeater-src {dep}\n")
    assert svc.is_built(next(c for c in svc.stack("meshcore").components
                             if c.id == "meshcore-node"))


def test_a_cli_build_with_an_unreadable_dependency_revision_says_unverified(tmp_path,
                                                                           monkeypatch):
    """The repeater checkout is no repository: the build runs, its receipt records `unknown`,
    the result names the unverified dependency, and the component does not read built."""
    svc, root = _box(tmp_path)
    for sp in MESHCORE_SOURCES:
        (root / sp).mkdir(parents=True, exist_ok=True)
    _commit(root / "src/openhop-core")
    res = _cli(svc, "meshcore-node", monkeypatch)
    assert [d.split()[:2] for d in res.details if d.split()[:1] == ["[unverified]"]] == [
        ["[unverified]", "openhop-repeater-src:"]]
    assert not svc.is_built(next(c for c in svc.stack("meshcore").components
                                 if c.id == "meshcore-node"))


@PATHS
def test_a_provider_source_cannot_move_during_a_dependent_build(tmp_path, monkeypatch, build):
    """An update of the dependency's source (it takes that source's lock) is refused busy while
    the dependent build runs."""
    refused = []

    def update_dependency():
        try:
            with reslock.operation_lock(svc._paths, reslock.source_lock_key("src/openhop-repeater"),
                                        "update", "meshcore"):
                refused.append(False)
        except reslock.ResourceBusy:
            refused.append(True)
    svc, root = _box(tmp_path, update_dependency)
    _meshcore_sources(root)
    build(svc, "meshcore-node", monkeypatch)
    assert refused and all(refused)



@PATHS
def test_a_dependency_of_a_dependency_is_locked(tmp_path, monkeypatch, build):
    """The manifest's chain nomadnet -> rns -> rns-lora-interface: a build of nomadnet holds all
    three sources for its whole run, the dependency's dependency included, so an operation that
    needs rns-lora-interface's source is refused busy while nomadnet builds."""
    chain = ("src/loraham-rns-interface", "src/nomadnet", "src/reticulum")
    held, refused = [], []

    def probe():
        held.append([sp for sp in chain if _busy(svc, sp)])
        refused.append(_busy(svc, "src/loraham-rns-interface"))
    svc, root = _box(tmp_path, probe)
    for sp in chain:
        (root / sp).mkdir(parents=True, exist_ok=True)
    build(svc, "nomadnet", monkeypatch)
    assert held and all(h == list(chain) for h in held), held
    assert refused and all(refused)


def _busy(svc, sp):
    try:
        with reslock.operation_lock(svc._paths, reslock.source_lock_key(sp), "update", "probe"):
            return False
    except reslock.ResourceBusy:
        return True

def test_the_receipt_records_the_revision_read_under_the_locks(tmp_path, monkeypatch):
    """The dependency moves after the console spawned the build and before the build holds its
    locks: the receipt records the revision the build consumed, not the one seen at spawn."""
    svc, root = _box(tmp_path, lambda: None)
    own, _at_spawn = _meshcore_sources(root)
    moved = []
    _background(svc, "meshcore-node", monkeypatch,
                before_run=lambda: moved.append(_commit(root / "src/openhop-repeater")))
    assert len(moved) == 1
    assert (root / "src/openhop-core/.venv/.lhpc-build-complete").read_text() == (
        f"lhpc build complete\nconsumed meshcore-node {own}\n"
        f"consumed openhop-repeater-src {moved[0]}\n")


def test_the_console_builds_a_component_as_the_cli_does(tmp_path, monkeypatch):
    """A console Build of one component builds what `lhpc build <component>` builds — that
    component — even while a provider of it is not built (the stack's Build builds providers
    first); it still holds the provider's source while it builds."""
    svc, root = _box(tmp_path)
    for sp in ("src/reticulum", "src/loraham-rns-interface", "src/nomadnet"):
        (root / sp).mkdir(parents=True, exist_ok=True)
    steps = [["python3", "-m", "venv", "--system-site-packages", ".venv"],
             [".venv/bin/pip", "install", "--disable-pip-version-check", "{runtime}/src/reticulum"],
             [".venv/bin/pip", "install", "--disable-pip-version-check", "."]]
    plan = svc.build("nomadnet")
    assert plan.ok and plan.details == [
        "  [build] nomadnet: " + " ; ".join(" ".join(argv) for argv in steps)]
    rendered = []
    from lhpc.core import commands
    real = commands.render_build_launcher

    def render(*a, **kw):
        rendered.append((kw["target"], sorted(kw["shared_lock_paths"]),
                         [st["argv"] for st in (a[0] if a else kw["steps"])]))
        return real(*a, **kw)
    monkeypatch.setattr(commands, "render_build_launcher", render)
    child = subprocess.Popen(["sleep", "30"])
    # Stubs the collaborator, the detached spawn: a live process stands in for the launcher.
    monkeypatch.setattr(Lifecycle, "spawn_job",
                        lambda self, name, argv, cwd, env=None: (f"{name}.log", child.pid))
    try:
        assert svc.spawn_web_job("build", "nomadnet")[:2] == ("build-nomadnet.log", "pending")
    finally:
        child.kill()
        child.wait()
    shared = sorted(str(reslock.lock_file_path(svc._paths, reslock.source_lock_key(sp)))
                    for sp in ("src/loraham-rns-interface", "src/reticulum"))
    assert rendered == [("nomadnet", shared, steps)]


def test_parallel_jobs_share_a_dependency(tmp_path, monkeypatch):
    """A detached build takes a dependency's source lock shared: a sibling build holding it
    shared does not stop it; an exclusive holder (an update) does."""
    root = tmp_path / "rt"
    paths = Paths(runtime_root=root)
    locks = paths.under("state", "locks")
    locks.mkdir(parents=True)
    dep = reslock.lock_file_path(paths, reslock.source_lock_key("src/reticulum"))
    dep.touch()
    ran = []

    def spec():
        return {"steps": [{"argv": ["true"], "env_items": []}], "cwd": str(tmp_path),
                "runtime_root": str(root), "lock_names": [], "shared_lock_names": [dep.name],
                "index_lock_name": ""}

    fd = os.open(dep, os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_SH)                    # a sibling build consuming it
        blr.run(spec())
        ran.append("beside a shared holder")
        fcntl.flock(fd, fcntl.LOCK_UN)
        fcntl.flock(fd, fcntl.LOCK_EX)                    # an update holding it
        monkeypatch.setenv("LHPC_BUILD_LOCK_WAIT_S", "0.2")
        with pytest.raises(SystemExit) as e:
            blr.run(spec())
        assert e.value.code == 3
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
    assert ran == ["beside a shared holder"]
