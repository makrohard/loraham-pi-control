"""§8 — a linked external source tree is read-only to LHPC: build and host-test are
blocked and the external tree is never modified."""

import os

from lhpc.core.lifecycle import Lifecycle
from lhpc.core.config import Config, OperatorConfig
from lhpc.core.model import Component, ComponentKind, SourceSpec, Stack
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from conftest import set_call


def _life(tmp_path):
    return Lifecycle(Paths(runtime_root=tmp_path), (), Config(operator=OperatorConfig()),
                     FakeSystem().system)


def _linked_comp(tmp_path):
    external = tmp_path / "external-checkout"
    external.mkdir()
    (external / "marker").write_text("untouched")
    (tmp_path / "src").mkdir()
    os.symlink(external, tmp_path / "src" / "app")     # adopt-by-link
    comp = Component(id="app", name="app", kind=ComponentKind.SERVICE,
                     build_steps=({"argv": ["true"]},), test_argv=("true",),
                     source=SourceSpec(path="src/app"))
    return comp, external


def test_build_blocked_on_linked_source_without_modifying_it(tmp_path):
    life = _life(tmp_path)
    comp, external = _linked_comp(tmp_path)
    assert life.is_linked_source(comp) is True
    res = life.build(comp)
    assert not res.ok and any("BLOCKED" in t for t in res.tail)
    assert (external / "marker").read_text() == "untouched"
    assert sorted(p.name for p in external.iterdir()) == ["marker"]   # no LHPC files


def test_host_test_blocked_on_linked_source(tmp_path):
    life = _life(tmp_path)
    comp, external = _linked_comp(tmp_path)
    res = life.host_test(comp)
    assert res is not None and not res.ok and any("BLOCKED" in t for t in res.tail)
    assert sorted(p.name for p in external.iterdir()) == ["marker"]


def test_generated_config_not_written_into_linked_source(tmp_path):
    # A file-config component whose source is a linked external tree must not receive
    # a generated config file; write_config_files skips it with a manual note.
    import os
    from lhpc.core.services import ControllerService
    from lhpc.core.paths import Paths
    from lhpc.core.probes.backends import FakeSystem
    # Find a real file-config component and link its source outside the runtime root.
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    target = None
    for s in svc.stacks():
        for c in s.components:
            if c.config_file and c.source:
                target, comp = s.id, c
                break
        if target:
            break
    if target is None:
        return                       # no file-config component to exercise
    external = tmp_path / "ext"
    external.mkdir()
    link = tmp_path / comp.source.path
    link.parent.mkdir(parents=True, exist_ok=True)
    os.symlink(external, link)
    written = svc.write_config_files(target)
    # nothing generated inside the external tree
    assert not any(p for p in external.rglob("*") if p.is_file())


# --- structured generated-config results + auto-start blocking ---------------

def test_write_config_files_returns_structured_results(tmp_path):
    from lhpc.core.services import ControllerService, ConfigWrite
    from lhpc.core.paths import Paths
    from lhpc.core.probes.backends import FakeSystem
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    res = svc.write_config_files("voice")        # env fmt -> runtime config dir
    assert res and all(isinstance(w, ConfigWrite) for w in res)
    assert any(w.component == "loraham-voice" and w.status == "written" for w in res)


def test_write_config_failure_is_structured_not_swallowed(tmp_path, monkeypatch):
    from lhpc.core.services import ControllerService
    from lhpc.core.paths import Paths
    from lhpc.core.probes.backends import FakeSystem
    from lhpc.core import runtime_fs
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    # voice writes to {runtime}/config/files/... -> runtime policy via runtime_fs.
    def boom(paths, path, text, mode=0o644):
        raise OSError("disk full")
    monkeypatch.setattr(runtime_fs, "atomic_write", boom)
    res = svc.write_config_files("voice")
    assert any(w.component == "loraham-voice" and w.status == "failed"
               and "disk full" in w.detail for w in res)


def test_start_blocks_when_generated_config_write_fails(tmp_path, monkeypatch):
    from conftest import real_spawn
    from lhpc.core.services import ControllerService, ConfigWrite
    from lhpc.core.lifecycle import Lifecycle
    from lhpc.core.paths import Paths
    from lhpc.core.probes.backends import FakeSystem
    from lhpc.core.outcomes import Outcome
    # Daemon serving both bands so voice's dependency gate passes and it reaches the
    # config-generation step; then its config write fails -> the launch is BLOCKED.
    # voice requires DIRECT, so the fixture daemon already reports DIRECT (gate clears).
    STATUS = b"STATUS RADIO=READY TXMODE=DIRECT\n"
    sys = FakeSystem(unix_replies={"/tmp/loraconf433.sock": STATUS,
                                   "/tmp/loraconf868.sock": STATUS}).system
    (tmp_path / "src" / "LoRaHAM_Voice").mkdir(parents=True)   # source present (installed)
    svc = ControllerService(system=sys, paths=Paths(runtime_root=tmp_path))
    monkeypatch.setattr(type(svc), "is_installed", lambda self, t: True)
    monkeypatch.setattr(type(svc), "is_built", lambda self, c: True)
    monkeypatch.setattr(type(svc), "_running_conflicts", lambda self, c, b: False)
    monkeypatch.setattr(Lifecycle, "missing_requirements", lambda self, c: [])
    monkeypatch.setattr(type(svc), "_lifecycle", lambda self: Lifecycle(
        self._paths, self.stacks(), self.config(), self._system, spawn=real_spawn))
    monkeypatch.setattr(type(svc), "write_config_files", lambda self, t, b="", overrides=None: [
        ConfigWrite("loraham-voice", "/x/voice.conf", "failed", "disk full")])
    set_call(svc)
    res = svc.start("voice", apply=True)
    assert any(r.component == "loraham-voice" and r.outcome == Outcome.BLOCKED
               and "config generation failed" in (r.summary or "") for r in res.results)


def test_start_linked_readonly_config_is_manual_required(tmp_path, monkeypatch):
    from conftest import real_spawn
    from lhpc.core.services import ControllerService, ConfigWrite
    from lhpc.core.lifecycle import Lifecycle
    from lhpc.core.paths import Paths
    from lhpc.core.probes.backends import FakeSystem
    from lhpc.core.outcomes import Outcome
    STATUS = b"STATUS RADIO=READY TXMODE=DIRECT\n"   # voice requires DIRECT -> gate clears
    sys = FakeSystem(unix_replies={"/tmp/loraconf433.sock": STATUS,
                                   "/tmp/loraconf868.sock": STATUS}).system
    (tmp_path / "src" / "LoRaHAM_Voice").mkdir(parents=True)
    svc = ControllerService(system=sys, paths=Paths(runtime_root=tmp_path))
    monkeypatch.setattr(type(svc), "is_installed", lambda self, t: True)
    monkeypatch.setattr(type(svc), "is_built", lambda self, c: True)
    monkeypatch.setattr(type(svc), "_running_conflicts", lambda self, c, b: False)
    monkeypatch.setattr(Lifecycle, "missing_requirements", lambda self, c: [])
    monkeypatch.setattr(type(svc), "_lifecycle", lambda self: Lifecycle(
        self._paths, self.stacks(), self.config(), self._system, spawn=real_spawn))
    monkeypatch.setattr(type(svc), "write_config_files", lambda self, t, b="", overrides=None: [
        ConfigWrite("loraham-voice", "/ext/voice.conf", "linked-readonly", "read-only")])
    set_call(svc)
    res = svc.start("voice", apply=True)
    assert any(r.component == "loraham-voice" and r.outcome == Outcome.MANUAL_REQUIRED
               and "linked source is read-only" in (r.summary or "") for r in res.results)


def test_interactive_start_blocks_when_config_generation_fails(tmp_path, monkeypatch):
    # §5.3: an interactive component whose required config CANNOT be generated must be
    # BLOCKED — no interactive marker written, no manual command presented as ready.
    from conftest import real_spawn
    from lhpc.core.services import ControllerService, ConfigWrite
    from lhpc.core.lifecycle import Lifecycle
    from lhpc.core.paths import Paths
    from lhpc.core.probes.backends import FakeSystem
    from lhpc.core.outcomes import Outcome
    STATUS = b"STATUS RADIO=READY TXMODE=MANAGED\n"
    sys = FakeSystem(unix_replies={"/tmp/loraconf433.sock": STATUS,
                                   "/tmp/loraconf868.sock": STATUS}).system
    (tmp_path / "src" / "LoRaHAM_Daemon").mkdir(parents=True)
    svc = ControllerService(system=sys, paths=Paths(runtime_root=tmp_path))
    monkeypatch.setattr(type(svc), "is_installed", lambda self, t: True)
    monkeypatch.setattr(type(svc), "is_built", lambda self, c: True)
    monkeypatch.setattr(type(svc), "_running_conflicts", lambda self, c, b: False)
    monkeypatch.setattr(Lifecycle, "missing_requirements", lambda self, c: [])
    monkeypatch.setattr(type(svc), "_lifecycle", lambda self: Lifecycle(
        self._paths, self.stacks(), self.config(), self._system, spawn=real_spawn))
    monkeypatch.setattr(type(svc), "write_config_files", lambda self, t, b="", overrides=None: [
        ConfigWrite("loraham-chat", "/x/lorachat.conf", "failed", "disk full")])
    marks = {"n": 0}
    monkeypatch.setattr(type(svc), "mark_interactive", lambda self, s, b="": marks.__setitem__("n", marks["n"] + 1))
    set_call(svc)
    res = svc.start("chat", apply=True)
    assert not res.ok
    assert any(r.component == "loraham-chat" and r.outcome == Outcome.BLOCKED
               and "config could not be generated" in (r.summary or "") for r in res.results)
    assert marks["n"] == 0                       # interactive marker NOT written
