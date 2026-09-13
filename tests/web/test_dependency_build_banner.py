"""The stack body's build-dependency surface: a library component (RadioLib) is presented as a
BUILD dependency, never as a skippable optional one, and when that dependency is not built the
body warns about it first, linking to the dependency's own card rather than to the generic
build-needed note."""
from __future__ import annotations

import htmlq
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService


def _daemon_svc(tmp_path):
    return ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))


def test_build_dependency_banner_warns_radiolib_first(tmp_path, web):
    # After an update (fresh RadioLib checkout, no .a) the body shows the build-the-dependency
    # warning — the severe banner — whose link opens RadioLib's own dependency card.
    svc = _daemon_svc(tmp_path)
    (tmp_path / "src" / "RadioLib").mkdir(parents=True)
    (tmp_path / "src" / "loraham-daemon").mkdir(parents=True)
    assert svc.unbuilt_build_deps("daemon") == ["radiolib"]              # the typed state
    doc = htmlq.parse(web(service_factory=lambda: svc).get("/stacks?open=daemon").get_data(as_text=True))
    banners = [b for b in doc.find("div", class_="taskitem-bad") if "radiolib" in doc.within(b).text]
    assert len(banners) == 1, "exactly one severe banner names the unbuilt dependency"
    link = doc.within(banners[0]).find("a", class_="ti-view")[0]["href"]
    assert "comp=radiolib" in link and link.endswith("#comp-radiolib")  # opens RadioLib's own card


def test_library_shows_build_dependency_pill_not_optional(tmp_path, web):
    # A kind=library (RadioLib) is a BUILD dependency, not a skippable "optional" component.
    doc = htmlq.parse(web(service_factory=lambda: _daemon_svc(tmp_path))
                      .get("/stacks?open=daemon").get_data(as_text=True))
    row = doc.within(doc.by_id("comp-radiolib"))
    pills = [p.text for p in row.find("span", class_="pill")]
    assert "build dependency" in pills
    assert "optional" not in pills
