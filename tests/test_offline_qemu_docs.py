"""Item 3 regression: the offline web-build docs must NOT recommend a systemd drop-in for the QEMU
tarball (it violates the canonical-unit integrity contract — integration repair refuses to proceed on
any override drop-in), and MUST document the supported temporary user-manager environment instead.
"""

import pathlib

_REPO = pathlib.Path(__file__).resolve().parents[1]
_DOCS = [_REPO / "docs" / "field-notes.md", _REPO / "README.md", _REPO / "docs" / "cli.md"]


def _all_docs_text() -> str:
    return "\n".join(p.read_text() for p in _DOCS if p.exists())


def test_no_forbidden_qemu_tarball_systemd_dropin_recommendation():
    text = _all_docs_text()
    # The exact drop-in assignment must never appear anywhere in the docs (recommendation or example).
    assert "Environment=LHPC_QEMU_TARBALL" not in text, \
        "docs must not carry a permanent systemd Environment=LHPC_QEMU_TARBALL drop-in"


def test_offline_web_build_documents_the_supported_user_manager_env():
    text = _all_docs_text()
    assert "systemctl --user set-environment" in text and "LHPC_QEMU_TARBALL" in text
    assert "systemctl --user unset-environment LHPC_QEMU_TARBALL" in text   # cleanup documented
