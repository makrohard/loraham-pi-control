"""Offline / prebuilt QEMU docs regression.

`LHPC_QEMU_TARBALL` is fetch-qemu.sh's OWN standalone interface; it is NOT forwarded through lhpc, so the
managed `lhpc build meshcom` (which builds from source via build-qemu.sh) does not read it. The docs must
therefore describe ONLY the direct standalone fetch-qemu.sh forms — never a via-lhpc build or a
user-manager / systemd-drop-in web flow for this variable (all of which implied a forwarding that no
longer exists).
"""

import pathlib

_REPO = pathlib.Path(__file__).resolve().parents[1]
_DOCS = [_REPO / "docs" / "field-notes.md", _REPO / "README.md", _REPO / "docs" / "cli.md"]
_BACKENDS = _REPO / "lhpc" / "core" / "probes" / "backends.py"


def _all_docs_text() -> str:
    return "\n".join(p.read_text() for p in _DOCS if p.exists())


def test_backends_does_not_forward_the_qemu_tarball_var():
    # The forwarding is gone: the var must not appear as a QUOTED allowlist element (the explanatory NOTE
    # may still name it unquoted to say WHY it is not forwarded), so it cannot masquerade as an
    # lhpc-honored override for the managed (build-qemu.sh) build.
    assert '"LHPC_QEMU_TARBALL"' not in _BACKENDS.read_text(), \
        "backends.py must not forward LHPC_QEMU_TARBALL (managed build uses build-qemu.sh, not fetch-qemu.sh)"


def test_offline_docs_document_the_direct_standalone_fetch_forms():
    text = _all_docs_text()
    # Both equivalent standalone forms are documented (flag + fetch-qemu's own env var), run directly.
    assert "scripts/fetch-qemu.sh <dest-dir> --from-file" in text
    assert "LHPC_QEMU_TARBALL=/absolute/path/qemu-...tar.xz scripts/fetch-qemu.sh <dest-dir>" in text


def test_offline_docs_do_not_tie_the_tarball_to_lhpc_or_a_service_env():
    text = _all_docs_text()
    # No via-lhpc build, no user-manager env, no systemd drop-in — every one implied a forwarding that
    # the managed build never had.
    assert "LHPC_QEMU_TARBALL=/absolute/path/qemu-...tar.xz lhpc build meshcom" not in text
    assert "systemctl --user set-environment" not in text
    assert "Environment=LHPC_QEMU_TARBALL" not in text
