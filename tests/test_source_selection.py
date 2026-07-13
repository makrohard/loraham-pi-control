"""§3 — source selection is production-safe: an invalid selector is rejected (never a
silent 'dev' fallback), and a hand-edited malformed remote is revalidated at the Git
boundary and never reaches `git clone`."""

from lhpc.core.services import ControllerService
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import System, FakeSystem, CommandResult
from lhpc.core.install import Installer
from lhpc.core.config import Config
from lhpc.core.model import SourceSpec


def test_invalid_source_selector_rejected_not_dev(tmp_path):
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    ln, admission, reason = svc.spawn_web_job("install", "daemon", source="evil")
    assert ln is None and admission == "blocked" and "invalid source" in reason


class _RecordingRunner:
    def __init__(self):
        self.calls = []

    def run(self, argv, timeout=None, cwd=None, env=None):
        self.calls.append(list(argv))
        return CommandResult(0, "", "")


def _inst_with(runner, tmp_path):
    fake = FakeSystem()
    sys = System(runner=runner, procfs=fake, fs=fake, unix=fake)
    return Installer(Paths(runtime_root=tmp_path / "rt"), (), Config(), sys)


def test_malformed_remote_never_reaches_git(tmp_path):
    runner = _RecordingRunner()
    inst = _inst_with(runner, tmp_path)
    spec = SourceSpec(path="src/x", remote="--upload-pack=evil")
    ok = inst._clone(spec, tmp_path / "dest", "dev", remote="--upload-pack=evil")
    assert ok is False
    assert not any("clone" in c for c in runner.calls)      # git clone NEVER invoked


def test_valid_remote_reaches_git(tmp_path):
    runner = _RecordingRunner()
    inst = _inst_with(runner, tmp_path)
    spec = SourceSpec(path="src/x", remote="https://github.com/x/y.git", branch="main")
    inst._clone(spec, tmp_path / "dest", "dev", remote="https://github.com/x/y.git")
    assert any("clone" in c for c in runner.calls)          # a valid remote does clone


def test_run_action_rejects_invalid_source(tmp_path):
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    r = svc.run_action("install", "daemon", source="evil")
    assert not r.ok and "Invalid source" in r.summary          # never rewritten to 'dev'


def test_run_action_default_source_is_pinned():
    from inspect import signature
    assert signature(ControllerService.run_action).parameters["source"].default == "pinned"


def test_update_status_malformed_remote_never_reaches_git(tmp_path):
    from lhpc.core.model import Component, ComponentKind, SourceSpec
    runner = _RecordingRunner()
    fake = FakeSystem()
    sys = System(runner=runner, procfs=fake, fs=fake, unix=fake)
    svc = ControllerService(system=sys, paths=Paths(runtime_root=tmp_path))
    (tmp_path / "src" / "x").mkdir(parents=True)                # installed source dir
    (tmp_path / "config").mkdir(parents=True)
    (tmp_path / "config" / "local.toml").write_text('[remotes]\nx = "--upload-pack=evil"\n')
    comp = Component(id="x", name="x", kind=ComponentKind.SERVICE,
                     source=SourceSpec(path="src/x", remote="https://github.com/a/b.git"))
    assert svc.update_status(comp) == "unknown"                 # blocked, no check
    assert not any("ls-remote" in c for c in runner.calls)      # git ls-remote NEVER invoked
