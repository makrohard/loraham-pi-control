"""The console's view of a stack installed from a prebuilt binary artifact: its row says so
(the `src:` and version pills carry the artifact's provenance), it is never offered as "not
installed", and the MeshCom password page explains why the mesh password cannot be managed there.

A binary install is faked at the receipt — the same seam the service reads — so no artifact is
ever downloaded. The receipt-laying helper here mirrors `tests/install/conftest.py::binary_receipt`;
hoist it to the root conftest if a third directory needs it.
"""
from __future__ import annotations

import htmlq
import pytest

from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService

SHA = "ab" * 32


@pytest.fixture
def binary_box(tmp_path, monkeypatch, binary_receipt):
    """`binary_box(stack=None) -> svc`: a service on a supported binary target; with `stack`
    given, that stack is installed from its artifact (proof-path files + receipt)."""
    monkeypatch.setattr(ControllerService, "binary_target", lambda self: "aarch64-trixie")

    def _make(stack=None):
        svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
        if stack is not None:
            binary_receipt(svc, stack, sha=SHA)
        return svc
    return _make


def _row(client, stack):
    """The stack's row on the overview, with its body rendered server-side (`?open=`), so the
    banners inside it are really there to be found."""
    doc = htmlq.parse(client.get(f"/stacks?open={stack}").get_data(as_text=True))
    return doc.within(doc.by_id(f"stackrow-{stack}"))


def _pills(row, prefix):
    return [p for p in row.find("span", class_="pill") if p.text.startswith(prefix)]


def _install_banners(row, stack):
    """The "not installed" banner: the warning whose link opens the stack's Install section."""
    return [b for b in row.find("div", class_="taskitem-warn")
            if any((a["href"] or "").endswith(f"#stack-install-{stack}")
                   for a in row.within(b).find("a", class_="ti-view"))]


def test_the_row_pills_carry_the_artifact_provenance(web, binary_box):
    row = _row(web(service_factory=lambda: binary_box("daemon")), "daemon")
    assert [p.text for p in _pills(row, "src:")] == ["src: binary"]
    version = _pills(row, "binary@")
    assert len(version) == 1                                   # ONE version pill in the row
    assert version[0].text == "binary@" + SHA[:9]
    assert "verified prebuilt artifact" in (version[0]["title"] or "")   # the tooltip rides on it


def test_a_binary_stack_is_not_offered_as_not_installed(web, binary_box):
    """A binary-installed stack has no clone by design: its row must not carry the
    "Not installed yet — run Install" banner that gates a source stack."""
    assert _install_banners(_row(web(service_factory=lambda: binary_box()), "daemon"), "daemon"), \
        "without any install the banner is expected — or this test could not find it at all"
    row = _row(web(service_factory=lambda: binary_box("daemon")), "daemon")
    assert [p.text for p in _pills(row, "src:")] == ["src: binary"]
    assert _install_banners(row, "daemon") == []


def test_the_password_page_names_the_binary_reason_and_the_source_command(web, binary_box):
    svc = binary_box("meshcom")
    reason = svc.hmac_binary_block("meshcom")
    assert reason                                              # the service refuses first
    doc = htmlq.parse(web(service_factory=lambda: svc).get("/stacks/meshcom/hmac/enable")
                      .get_data(as_text=True))
    notes = [n.text for n in doc.find("p", class_="depnote")]
    assert any(reason in n for n in notes), notes              # the typed reason, verbatim
    assert [c.text for c in doc.find("pre", class_="cmd")] == \
        ["lhpc install meshcom --source pinned --yes"]        # the way out
    assert doc.find("form", action="/stacks/meshcom/hmac/enable/apply") == []   # no Apply
