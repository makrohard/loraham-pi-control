"""Graywolf's `--source` vocabulary (plan F4): its release is fetched, never compiled from a checkout.

`binary` (the default) = the manifest's pinned `.deb`, which its build fetches; `stable` = the newest
upstream release, verified against its own checksums.txt (`--upstream` stays as the old name);
`pinned`/`dev` name a compiled lane graywolf does not have and are a typed refusal.
"""

import json

import pytest

from lhpc.core.lifecycle import BUILD_MARKER_TEXT
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.service_base import ActionResult
from lhpc.core.services import ControllerService


def _svc(tmp_path, installed="0.14.12", latest="0.15.0"):
    (tmp_path / "config" / "stacks").mkdir(parents=True, exist_ok=True)
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    main = svc.stack("graywolf").main_component
    d = tmp_path / "/".join(main.build_marker.split("/")[:-1])
    d.mkdir(parents=True, exist_ok=True)
    (d / ".lhpc-graywolf-version").write_text(f"{installed} arm64\n")
    marker = svc._lifecycle().source_dir(main) / main.build_marker
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(BUILD_MARKER_TEXT + svc._consumed_source_lines(main))
    (tmp_path / "state").mkdir(exist_ok=True)
    (tmp_path / "state" / "graywolf-upstream.json").write_text(
        json.dumps({"latest": latest, "checked_at": 1, "error": ""}))
    return svc


@pytest.mark.parametrize("apply", [False, True])
def test_stable_is_the_upstream_update(tmp_path, monkeypatch, apply):
    svc = _svc(tmp_path)
    seen = []
    monkeypatch.setattr(ControllerService, "graywolf_upstream_update",
                        lambda self, target, apply=False: seen.append((target, apply))
                        or ActionResult(True, "UPSTREAM"))
    res = svc.update("graywolf", apply=apply, source="stable")
    assert res.summary == "UPSTREAM" and seen == [("graywolf", apply)]


def test_the_stable_plan_names_the_stable_command(tmp_path):
    plan = _svc(tmp_path).update("graywolf", apply=False, source="stable")
    assert plan.ok and "0.15.0" in plan.summary
    assert plan.next_commands == ["lhpc update graywolf --source stable --yes"]


@pytest.mark.parametrize("source", ["pinned", "dev"])
@pytest.mark.parametrize("apply", [False, True])
def test_a_compiled_lane_is_a_typed_refusal(tmp_path, monkeypatch, source, apply):
    svc = _svc(tmp_path)
    monkeypatch.setattr(ControllerService, "graywolf_upstream_update",
                        lambda *a, **k: pytest.fail("must not fetch"))
    monkeypatch.setattr(ControllerService, "build", lambda *a, **k: pytest.fail("must not build"))
    res = svc.update("graywolf", apply=apply, source=source)
    assert not res.ok and res.data["refusal"] == "no_compiled_lane"
    assert res.next_commands == ["lhpc update graywolf --source binary --yes",
                                 "lhpc update graywolf --source stable --yes"]
    assert not (tmp_path / "state" / "source-txn").exists()


@pytest.mark.parametrize("build", [True, False])
@pytest.mark.parametrize("apply", [False, True])
def test_binary_is_the_pinned_release_its_build_fetches(tmp_path, monkeypatch, apply, build):
    """The CLI builds inline; the console (`build=False`) runs the build as its job, as for any
    update whose stack needs a build."""
    svc = _svc(tmp_path)
    seen = []
    monkeypatch.setattr(ControllerService, "build",
                        lambda self, target, apply=False, **k: seen.append((target, apply))
                        or ActionResult(True, "BUILD"))
    res = svc.update("graywolf", apply=apply, source="binary", build=build)
    if apply and not build:
        assert res.ok and res.data["needs_build"] == ["graywolf"] and seen == []
    else:
        assert res.summary == "BUILD" and seen == [("graywolf", apply)]


def test_a_bare_update_is_binary(tmp_path):
    """§F4: `binary` is the default at, behind or ahead of the pin (an earlier `--source stable`
    is not a lane a bare update infers; `stable` is always named)."""
    assert _svc(tmp_path / "a", installed="0.14.14").update_default_channel("graywolf") == "binary"
    assert _svc(tmp_path / "b", installed="0.14.12").update_default_channel("graywolf") == "binary"
    svc = _svc(tmp_path / "c", installed="0.16.0")
    assert svc.update_default_channel("graywolf") == "binary"
    assert svc.update_default_channel("kiss") == "pinned"
