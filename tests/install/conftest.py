"""Fixtures shared by the install suites: one real-git rig, one repository builder, one
`Installer` builder, and the operator's-box service the self-update suites drive.

    def test_x(git, make_repo, installer):
        head = make_repo(tmp_path / "rt" / "local" / "app")   # a committed repo, its HEAD
        inst = installer()                                      # Installer rooted at tmp_path/"rt"
        git(dest, "rev-parse", "HEAD")                          # real git, hermetic environment

Every git call runs under `gitrepo.ENV` (author/committer named, global and system config
nulled, HOME pointed nowhere, prompts off) — never the developer's `~/.gitconfig`.
"""
from __future__ import annotations

from pathlib import Path

import pytest

import gitrepo
from lhpc.core import selfupdate
from lhpc.core.config import Config
from lhpc.core.install import Installer
from lhpc.core.model import Component, ComponentKind, SourceSpec, Stack
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import RealSystem


@pytest.fixture
def git():
    """`git(cwd, *args) -> stdout.strip()`: real git in a temp directory under `gitrepo.ENV`.
    A non-zero exit fails the test naming the command, so a call never needs a returncode check."""
    return gitrepo.git


@pytest.fixture
def make_repo(git):
    """`make_repo(path, files=None) -> head`: a committed repository at `path` (created, parents
    too) holding `files` (default `{"file.txt": "hello\\n"}`), its HEAD sha returned. The ONE
    repository builder the install suites use; tagged or advanced shapes build on it with `git`."""
    def _make(path, files=None):
        path = Path(path)
        path.mkdir(parents=True, exist_ok=True)
        git(path, "init", "-q")
        for name, text in (files or {"file.txt": "hello\n"}).items():
            (path / name).parent.mkdir(parents=True, exist_ok=True)
            (path / name).write_text(text)
        git(path, "add", "-A")
        git(path, "commit", "-qm", "init")
        return git(path, "rev-parse", "HEAD")
    return _make


def _app_component():
    """The plain managed-source component the install suites adopt: `app`, sourced from
    `src/app`, with a local fallback directory `app` under the adopt search root."""
    return Component(id="app", name="app", kind=ComponentKind.SERVICE,
                     source=SourceSpec(path="src/app", local_dir="app"))


@pytest.fixture
def installer(tmp_path):
    """`installer(comp=None, *, extra=(), root=None, search_root=None, system=None, values=None)`
    → an `Installer` over the real System, rooted at `root` (default `tmp_path/"rt"`), whose one
    stack `s` holds `comp` (default: `app` sourced from `src/app`, local fallback `app`) plus
    `extra`. `search_root` is the adopt search root (default `<root>/local`); `values` are
    further config values merged beside it."""
    def _build(comp=None, *, extra=(), root=None, search_root=None, system=None, values=None):
        root = Path(root) if root is not None else tmp_path / "rt"
        comp = comp or _app_component()
        cfg = Config(values={"install": {"adopt_search_root": str(search_root or root / "local")},
                             **(values or {})})
        stacks = (Stack(id="s", name="s", main=comp.id, components=(comp, *extra)),)
        return Installer(Paths(runtime_root=root), stacks, cfg, system or RealSystem())
    return _build


# --- self-update: the operator's box --------------------------------------------------------------

@pytest.fixture()
def env(tmp_path, monkeypatch):
    """A working clone with an origin to advance, and `selfupdate.repo_root()` pointed at it.

    The code under test drives git through `RealSystem()` — a real sub-process against these
    temp repositories — so the actual git integration is exercised, not a stub of it.
    """
    _origin, work, up = gitrepo.repos(tmp_path)
    monkeypatch.setattr(selfupdate, "repo_root", lambda: work)
    return {"sys": RealSystem(), "paths": gitrepo.runtime_paths(tmp_path), "work": work, "up": up}


@pytest.fixture()
def op_svc(tmp_path, monkeypatch):
    """A controller on a FakeSystem, standing in for the operator's box during an update.

    `op_svc(cmds=None, *, root=None, units=False, invocation=None)` returns `(service, fake)` so
    a test can assert on `fake.calls`. `cmds` is a `{argv-tuple: CommandResult}` table; `root`
    overrides the runtime root (default `tmp_path`). The checkout the venv sync would reinstall
    is a temp directory whose `pip install -e` already succeeds, so a test about a LATER step is
    not tripped by that one. `units=True` installs the canonical unit set for this root under
    `$HOME/.config/systemd/user` (the per-test HOME the root conftest supplies — the same place
    `_user_unit_dir()` and the out-of-process verifier look). `invocation=True/False` sets or
    clears INVOCATION_ID (a managed console versus a foreground shell).
    """
    def build(cmds=None, *, root=None, units=False, invocation=None):
        import os
        import sys

        from lhpc.core import selfupdate, updater_units
        from lhpc.core.paths import Paths
        from lhpc.core.probes.backends import CommandResult, FakeSystem
        from lhpc.core.services import ControllerService

        rt = Path(root) if root is not None else tmp_path
        checkout = tmp_path / "checkout"
        checkout.mkdir(exist_ok=True)
        monkeypatch.setattr(selfupdate, "repo_root", staticmethod(lambda: checkout))
        table = {(sys.executable, "-m", "pip", "install", "-e", str(checkout)):
                 CommandResult(0, "", "")}
        table.update(cmds or {})
        fake = FakeSystem(commands=table)
        svc = ControllerService(system=fake.system, paths=Paths(runtime_root=rt))
        if units:
            (rt / "state").mkdir(parents=True, exist_ok=True)
            ud = Path(os.environ["HOME"]) / ".config" / "systemd" / "user"
            ud.mkdir(parents=True, exist_ok=True)
            _, co, venv = updater_units.deployment_paths(str(rt))
            for kind in updater_units.ALL_UNITS:
                (ud / kind).write_text(updater_units.render(kind, str(rt), co, venv))
        if invocation is True:
            monkeypatch.setenv("INVOCATION_ID", "x")
        elif invocation is False:
            monkeypatch.delenv("INVOCATION_ID", raising=False)
        return svc, fake
    return build


@pytest.fixture()
def systemctl_ok_rows():
    """The eight `systemctl --user` rows a successful integration repair drives, each answering
    rc 0: daemon-reload, the two watcher `enable --now`s, the web and boot-restore enables, the
    two watcher `is-active` probes and the web restart. A test overrides the one row it wants
    to fail."""
    from lhpc.core import updater_units as U
    from lhpc.core.probes.backends import CommandResult
    ok = CommandResult(0, "", "")
    return {("systemctl", "--user", "daemon-reload"): ok,
            ("systemctl", "--user", "enable", "--now", U.PATH_UNIT): ok,
            ("systemctl", "--user", "enable", "--now", U.RESTART_PATH_UNIT): ok,
            ("systemctl", "--user", "enable", U.WEB_UNIT): ok,
            ("systemctl", "--user", "enable", U.BOOT_RESTORE_UNIT): ok,
            ("systemctl", "--user", "is-active", "--quiet", U.PATH_UNIT): ok,
            ("systemctl", "--user", "is-active", "--quiet", U.RESTART_PATH_UNIT): ok,
            ("systemctl", "--user", "restart", U.WEB_UNIT): ok}


# --- binary channel: receipts and the pipeline stub -----------------------------------------------


@pytest.fixture
def stub_pipeline(monkeypatch):
    """`stub_pipeline(svc, *, download=None) -> entry`: everything of `binary_install` up to the
    download is answered locally — the index is fetched from nowhere, its entry for the stack
    carries the manifest pins and this box's target (so the real target and pin checks pass),
    and `zstd` is taken as present. `download` replaces `download_artifact`; by default it
    raises `BinaryInstallError("DOWNLOAD-REACHED")`, the marker that the gates were passed."""
    from lhpc.core import binary_install as bi

    def _stub(svc, *, download=None):
        def entry(sid):
            return bi.IndexEntry(
                stack=sid, filename=f"{sid}-{'a' * 64}.tar.zst",
                url=f"https://example.invalid/{sid}-{'a' * 64}.tar.zst", sha256="a" * 64,
                size=10, components=dict(svc._binary_pins(sid)), runtime_deps=(),
                target=svc.binary_target(),
                provenance={"smoke": {"mode": "mandatory", "result": "passed"}})

        def _reached(*a, **k):
            raise bi.BinaryInstallError("DOWNLOAD-REACHED")
        monkeypatch.setattr(bi, "fetch_index", lambda url: {"schema": 2, "stacks": {}})
        monkeypatch.setattr(bi, "index_entry", lambda idx, sid: entry(sid))
        monkeypatch.setattr(bi, "require_zstd", lambda: None)
        monkeypatch.setattr(bi, "download_artifact", download or _reached)
        return entry
    return _stub


@pytest.fixture
def stub_adopt(monkeypatch):
    """`stub_adopt(svc, *, fail_paths=(), record=True) -> seen`: the source adoption a channel
    switch performs, stubbed as a collaborator (`_adopt_dev_fallback` is the one seam the switch
    calls per source group; a real clone would need a remote). A successful adoption creates
    the source directory AND writes its ownership record — the switch is complete only once
    every adopted path is recorded, so `record=False` is a failed switch, not a shortcut. Paths
    in `fail_paths` fail. `seen` collects `(path, selector, force)` per call."""
    from lhpc.core import source_registry
    from lhpc.core.services import ControllerService

    class _Adopted:
        status, detail, provenance = "done", "", ""

    class _Failed:
        status, detail, provenance = "failed", "clone failed", ""

    def _stub(svc, *, fail_paths=(), record=True):
        seen = []

        def _adopt(self, inst, st, comp, selector, resolved, force=False, locked=False):
            path = comp.source.path
            seen.append((path, selector, force))
            if path in fail_paths:
                return _Failed()
            svc._paths.resolve_source(path).mkdir(parents=True, exist_ok=True)
            if record:
                source_registry.write_record(svc._paths, source_registry.RegistryRecord(
                    source_rel=path, remote=comp.source.remote or "https://x.invalid/r.git",
                    selector=selector, resolved_commit="e" * 40, adopted_at=2.0,
                    txn_id="txn-new-" + comp.id, components=(comp.id,)))
            return _Adopted()
        monkeypatch.setattr(ControllerService, "_adopt_dev_fallback", _adopt)
        return seen
    return _stub
