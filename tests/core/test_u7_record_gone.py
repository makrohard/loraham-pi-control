"""U7 (plan v6, D3 + D5): the known-working record and the last-start marker are gone.

The "Known working" lane (`--source pinned`) is this LHPC's manifest pin itself: a store left
under `profiles/known-working/` by an older LHPC is not read by an install, an update or a
freshness check, a stack start writes no `state/last-start/` marker, and boot restore takes a
band only from the ownership record (or the manifest's fixed band), never from a marker. The
0.13.x on-disk files are written raw here, the way an older LHPC left them.
"""

import json
import os
import subprocess

from lhpc.core import source_registry
from lhpc.core.model import ProfileState
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService

_ENV = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}


def _git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True,
                          text=True, env=_ENV).stdout.strip()


def _old_store(root, stack_id, entries):
    """A 0.13.x known-working store (`profiles/known-working/<stack>.json`), written raw."""
    p = root / "profiles" / "known-working" / f"{stack_id}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"version": 1, "compositions": [
        {"hash": "f" * 64, "entries": entries, "validated": {"confirmed_at": 1.0}}]}))


def _old_marker(root, stack_id, band, started_at=5.0):
    """A 0.13.x last-start marker (`state/last-start/<stack>.json`), written raw."""
    p = root / "state" / "last-start" / f"{stack_id}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"version": 1, "stack": stack_id, "band": band,
                             "started_at": started_at, "entries": {}, "hash": "f" * 64}))


def test_a_pinned_install_takes_the_manifest_pin_over_an_old_store(tmp_path):
    """The store names the FIRST commit, the manifest pin the second (the local tree's HEAD): the pinned
    adoption activates at the pin. Red at 912c37d5: the store's commit was expected, the tree failed closed."""
    from lhpc.core.config import Config
    from lhpc.core.install import Installer
    from lhpc.core.model import Component, ComponentKind, SourceSpec, Stack
    from lhpc.core.probes import RealSystem
    local = tmp_path / "rt" / "local" / "app"
    local.mkdir(parents=True)
    _git(local, "init", "-q")
    for n in ("1", "2"):
        (local / "f").write_text(n)
        _git(local, "add", "-A")
        _git(local, "commit", "-qm", n)
    first, pin = _git(local, "rev-parse", "HEAD~1"), _git(local, "rev-parse", "HEAD")
    comp = Component(id="app", name="app", kind=ComponentKind.SERVICE,
                     source=SourceSpec(path="src/app", local_dir="app", pin_commit=pin))
    stacks = (Stack(id="s", name="s", main="app", components=(comp,)),)
    paths = Paths(runtime_root=tmp_path / "rt")
    _old_store(tmp_path / "rt", "s", {"app": {"commit": first, "selector": "pinned", "remote": "",
                                              "source_rel": "src/app", "strategy": ""}})
    inst = Installer(paths, stacks,
                     Config(values={"install": {"adopt_search_root": str(tmp_path / "rt" / "local")}}),
                     RealSystem())
    action = inst.adopt_source(comp, source="pinned")
    assert action.status == "done", action.detail
    assert source_registry.read_record(paths, "src/app").resolved_commit == pin
    assert "known working (operator-confirmed" not in action.detail


def test_a_pinned_update_plans_the_manifest_pin_over_an_old_store(tmp_path, monkeypatch):
    """`lhpc update kiss --source pinned`: the store holds a compatible composition at another commit;
    the adoption is not handed that commit. Red at 912c37d5: it was the planned identity."""
    from lhpc.core.install import Installer, PlanAction
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    stack = svc.stack("kiss")
    old = "b" * 40
    _old_store(tmp_path, "kiss", {c.id: {"commit": old, "selector": "pinned",
                                          "remote": svc._effective_remote(c), "source_rel": c.source.path,
                                          "strategy": ""}
                                  for c in stack.components if c.source is not None})
    got = []

    def adopt(self, comp, force=False, source="pinned", pinned_expected=None, locked=False):
        got.append((comp.id, source, pinned_expected))
        return PlanAction("adopt", "", f"adopt {comp.id}", status="done", detail="(fake)")
    monkeypatch.setattr(Installer, "adopt_source", adopt)
    svc.update("kiss", apply=True, source="pinned")
    assert got, "no source was adopted"
    assert all(src == "pinned" and old not in (exp or ("",))[0] for _cid, src, exp in got), got


def test_boot_restore_reads_no_last_start_marker(tmp_path, monkeypatch):
    """A radio-less, band-less main record of a band-switchable stack beside a valid 0.13.x marker naming
    868: the marker supplies no band, so the skip says there is no band source. Red at 912c37d5: the
    marker's band made an item, which the radio filter then dropped as a band item from before 0.14.0."""
    import lhpc.core.service_boot_restore as sbr
    from tests.core.test_boot_restore import _rec, _write_record
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    monkeypatch.setattr(sbr, "current_boot_id", lambda: "CURBOOT")
    monkeypatch.setattr(ControllerService, "_web_integration_proven", lambda self: (True, ""))
    monkeypatch.setattr(ControllerService, "boot_restore_enabled", lambda self: (True, ""))
    _write_record(tmp_path, _rec(boot_id="OLDBOOT"))           # kiss, band "", no radio
    _old_marker(tmp_path, "kiss", "868")
    res = svc.boot_restore_run()
    assert res.ok, res.summary
    journal = json.loads((tmp_path / "state" / "boot-restore.json").read_text())
    reasons = [s["reason"] for s in journal["skipped"] if s["stack"] == "kiss"]
    assert reasons and all("no valid band source" in r for r in reasons), reasons


def test_the_confirmed_working_profile_state_is_gone():
    values = {p.value for p in ProfileState}
    assert "installed-unvalidated" in values                    # anchor: the enum is read
    assert "confirmed-working" not in values
