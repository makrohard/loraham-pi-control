"""Graywolf's upstream-release check and one-click update.

The .deb is fetched, so there is no git head to poll: the version rides in the tree's
`.lhpc-graywolf-version` stamp, upstream is the GitHub releases API, and an operator update
verifies the new .deb against that release's own checksums.txt (graywolf-fetch.sh
--from-upstream). These tests pin the version math, the network handling, and the render.
"""

import json
import pathlib

from lhpc.core.lifecycle import BUILD_MARKER_TEXT
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService


def _svc(tmp_path, installed="0.14.12"):
    (tmp_path / "config" / "stacks").mkdir(parents=True, exist_ok=True)
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    main = svc.stack("graywolf").main_component
    d = tmp_path / "/".join(main.build_marker.split("/")[:-1])
    d.mkdir(parents=True, exist_ok=True)
    (d / ".lhpc-graywolf-version").write_text(f"{installed} arm64\n")
    marker = svc._lifecycle().source_dir(main) / main.build_marker
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(BUILD_MARKER_TEXT + svc._consumed_source_lines(main))
    return svc


def _cache(svc, latest):
    (svc._paths.runtime_root / "state").mkdir(exist_ok=True)
    (svc._paths.runtime_root / "state" / "graywolf-upstream.json").write_text(
        json.dumps({"latest": latest, "checked_at": 1, "error": ""}))


def test_installed_version_comes_from_the_tree_stamp_not_the_pin_name(tmp_path):
    """After an upstream update the manifest marker name stays the pinned baseline, so the
    DISPLAYED version must come from `.lhpc-graywolf-version` (the true version)."""
    svc = _svc(tmp_path, installed="0.16.0")           # ahead of the manifest pin
    fv = svc.fetched_version_state("graywolf")
    assert fv["installed"] == "0.16.0"
    # FORWARD-ONLY: sitting ahead of the pin must NOT prompt a downgrade.
    assert fv["has_update"] is False


def test_upstream_state_is_ahead_only_when_newer(tmp_path):
    svc = _svc(tmp_path, installed="0.14.12")
    assert svc.graywolf_upstream_state("graywolf")["ahead"] is False   # no cache yet
    _cache(svc, "0.15.0")
    assert svc.graywolf_upstream_state("graywolf")["ahead"] is True
    _cache(svc, "0.14.12")
    assert svc.graywolf_upstream_state("graywolf")["ahead"] is False   # equal, not ahead
    _cache(svc, "0.14.0")
    assert svc.graywolf_upstream_state("graywolf")["ahead"] is False   # older upstream


def test_check_parses_the_tag_and_handles_failures(tmp_path, monkeypatch):
    svc = _svc(tmp_path)

    def api(payload, rc=0, err=""):
        class R:
            returncode = rc; stdout = payload; stderr = err
        monkeypatch.setattr(svc._system.runner, "run", lambda a, timeout=None: R())

    api('{"tag_name":"v0.15.0"}')
    r = svc.graywolf_upstream_check("graywolf")
    assert r.ok and "0.15.0" in r.summary
    assert svc.graywolf_upstream_state("graywolf")["latest"] == "0.15.0"

    api('{"message":"API rate limit exceeded"}')          # no tag_name
    assert svc.graywolf_upstream_check("graywolf").ok is False
    api("", rc=7, err="could not resolve host")           # network error
    assert svc.graywolf_upstream_check("graywolf").ok is False


def test_upstream_update_fetches_verifies_and_remarks_built(tmp_path, monkeypatch):
    """One-click: runs the fetch --from-upstream, then re-marks the tree built so the start
    gate stays satisfied. Refuses when not actually behind."""
    svc = _svc(tmp_path, installed="0.14.12")
    _cache(svc, "0.15.0")
    calls = []

    class R:
        returncode = 0; stdout = ""; stderr = ""

    def fake_run(argv, timeout=None):
        calls.append(argv)
        # emulate the fetch script swapping the tree: new stamp, marker removed
        if "graywolf-fetch.sh" in " ".join(argv):
            d = tmp_path / "build" / "tools" / "graywolf"
            (d / ".lhpc-graywolf-version").write_text("0.15.0 arm64\n")
            (svc._lifecycle().source_dir(svc.stack("graywolf").main_component)
             / svc.stack("graywolf").main_component.build_marker).unlink()
        return R()
    monkeypatch.setattr(svc._system.runner, "run", fake_run)
    monkeypatch.setattr(ControllerService, "stack_running", lambda self, t: False)

    res = svc.graywolf_upstream_update("graywolf", apply=True)
    assert res.ok, res.summary
    # the fetch ran with --from-upstream for the cached version
    fetch = next(a for a in calls if "graywolf-fetch.sh" in " ".join(a))
    assert fetch[-1] == "--from-upstream" and "0.15.0" in fetch
    # re-marked built -> the start gate is satisfied again
    assert svc.is_built(svc.stack("graywolf").main_component) is True
    # displayed version moved, no lingering update prompt
    fv = svc.fetched_version_state("graywolf")
    assert fv["installed"] == "0.15.0" and fv["has_update"] is False

    # not behind -> no-op refusal (idempotent), never runs the fetch again
    calls.clear()
    assert "already at the latest" in svc.graywolf_upstream_update("graywolf", apply=True).summary
    assert not calls


def test_default_fetch_path_is_unchanged_no_upstream_flag(tmp_path):
    """The image/auto-install fetch (no --from-upstream) still requires the reviewed table
    entry — the upstream mode is opt-in and never the default."""
    script = pathlib.Path("lhpc/data/scripts/graywolf-fetch.sh").read_text()
    assert "--from-upstream" in script
    # the table lookup is tried FIRST; upstream checksums only as a fallback under the flag
    assert script.index('EXPECTED="$(sums') < script.index('[ -n "$FROM_UPSTREAM" ]')
    assert 'FROM_UPSTREAM=""' in script          # defaults off


def test_ver_tuple_orders_prerelease_and_short_versions():
    """REVIEW-FOUND: leading-digits-per-segment (not digit-concatenation) and a pre-release
    flag, so '0.15.0-rc1' < '0.15.0', '0.14' < '0.14.12', and 'v'-prefixes normalise."""
    vt = ControllerService._ver_tuple
    assert vt("0.15.0-rc1") < vt("0.15.0")
    assert vt("0.14") < vt("0.14.12")
    assert vt("0.14.11") < vt("0.14.12") < vt("0.15.0")
    assert vt("v0.15.0") == vt("0.15.0")


def test_a_rollback_pin_still_surfaces_via_needs_build(tmp_path):
    """REVIEW-FOUND #3: the forward-only has_update pill does not show a pin ROLLBACK — but a
    rollback changes the version-named build marker path, so is_built goes False and the
    operator gets the 'Build needed' banner + Build button instead. The signal exists."""
    svc = _svc(tmp_path, installed="0.14.12")           # tree at 0.14.12, marker present
    main = svc.stack("graywolf").main_component
    assert svc.is_built(main) is True
    # simulate an LHPC rollback: the manifest marker path now names an OLDER version
    object.__setattr__(main, "build_marker", "build/tools/graywolf/.lhpc-built-0.14.10")
    assert svc.is_built(main) is False                  # marker at the new pin path absent
    assert "graywolf" in svc.unbuilt_components("graywolf")   # -> needs_build banner + Build


def test_transient_check_preserves_the_last_known_version(tmp_path, monkeypatch):
    """REVIEW-FOUND #1: a flaky/rate-limited re-check must NOT wipe a previously found update."""
    svc = _svc(tmp_path, installed="0.14.12")

    def api(payload, rc=0):
        class R:
            returncode = rc; stdout = payload; stderr = ""
        monkeypatch.setattr(svc._system.runner, "run", lambda a, timeout=None: R())

    api('{"tag_name":"v0.15.0"}')
    assert svc.graywolf_upstream_check("graywolf").ok
    assert svc.graywolf_upstream_state("graywolf")["ahead"] is True   # pill shown

    api("", rc=7)                                        # network failure on the re-check
    assert svc.graywolf_upstream_check("graywolf").ok is False
    st = svc.graywolf_upstream_state("graywolf")
    assert st["latest"] == "0.15.0" and st["ahead"] is True, "the update must survive the flake"


def test_update_reports_a_failed_restart_as_not_ok(tmp_path, monkeypatch):
    """REVIEW-FOUND #0: a failed post-update restart leaves the station offline — it must
    NOT be reported as a green success."""
    svc = _svc(tmp_path, installed="0.14.12")
    _cache(svc, "0.15.0")

    class R:
        returncode = 0; stdout = ""; stderr = ""

    def fake_run(argv, timeout=None):
        if "graywolf-fetch.sh" in " ".join(argv):
            (tmp_path / "build" / "tools" / "graywolf" / ".lhpc-graywolf-version").write_text(
                "0.15.0 arm64\n")
            (svc._lifecycle().source_dir(svc.stack("graywolf").main_component)
             / svc.stack("graywolf").main_component.build_marker).unlink()
        return R()
    monkeypatch.setattr(svc._system.runner, "run", fake_run)
    monkeypatch.setattr(ControllerService, "stack_running", lambda self, t: True)
    from lhpc.core.service_base import ActionResult as _AR
    monkeypatch.setattr(ControllerService, "restart",
                        lambda self, t, **kw: _AR(False, "restart failed"))

    res = svc.graywolf_upstream_update("graywolf", apply=True)
    assert res.ok is False, "a failed restart must surface as not-ok"
    assert "restart FAILED" in res.summary
