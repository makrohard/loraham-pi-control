"""A3: `--accept-pin-mismatch` on `lhpc install --source binary` and `lhpc update`."""
from __future__ import annotations

import pytest

from lhpc.adapters.cli import main as cli_main
from lhpc.core import binary_install as bi
from lhpc.core.service_base import ActionResult
from lhpc.core.services import ControllerService

A = "a" * 40


@pytest.fixture
def box(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("LHPC_RUNTIME_ROOT", str(tmp_path / "rt"))
    cli_main.main(["bootstrap", "--yes"])
    capsys.readouterr()
    monkeypatch.setattr(ControllerService, "binary_target", lambda self: "aarch64-trixie")
    return tmp_path / "rt"


def _published(monkeypatch, stack, cid, commit):
    """The index serves `stack` with `cid` built from `commit`; everything else at the pins.
    A download fails the test, so each case here is shown to stop before one."""
    def _entry(idx, sid):
        svc = ControllerService()
        pins = svc._binary_pins(sid)
        return bi.IndexEntry(stack=sid, filename=f"{sid}-{'a' * 64}.tar.zst",
                             url="https://example.invalid/x", sha256="a" * 64, size=10,
                             components={**pins, cid: commit}, runtime_deps=(),
                             target="aarch64-trixie",
                             provenance={"smoke": {"mode": "mandatory", "result": "passed"}})
    monkeypatch.setattr(bi, "fetch_index", lambda url: {"schema": 2, "stacks": {}})
    monkeypatch.setattr(bi, "index_entry", _entry)
    monkeypatch.setattr(bi, "require_zstd", lambda: None)
    monkeypatch.setattr(bi, "download_artifact",
                        lambda *a, **k: pytest.fail("must not download"))


def test_cli_install_with_flag_passes_the_dry_run_token_to_the_apply(box, monkeypatch):
    """The real `_apply_flow`: the dry run (ok, changes >= 1) returns `data["consent"]`, and the
    apply call receives exactly that token."""
    calls = []

    def _install(self, stack=None, apply=False, source="", accept_pin_mismatch="", **kw):
        calls.append((apply, accept_pin_mismatch))
        if not apply:
            return ActionResult(True, "plan", data={"changes": 1, "consent": "c" * 64})
        return ActionResult(True, "done", data={"changes": 1})
    monkeypatch.setattr(ControllerService, "install", _install)
    assert cli_main.main(["install", "daemon", "--source", "binary",
                          "--accept-pin-mismatch", "--yes"]) == 0
    assert calls == [(False, "yes"), (True, "c" * 64)]


def test_cli_update_with_flag_passes_the_dry_run_token_to_the_apply(box, monkeypatch):
    calls = []

    def _update(self, target="", apply=False, source="", accept_pin_mismatch="", **kw):
        calls.append((apply, accept_pin_mismatch))
        return ActionResult(True, "p", data={"changes": 1, "consent": "d" * 64})
    monkeypatch.setattr(ControllerService, "update", _update)
    assert cli_main.main(["update", "daemon", "--source", "binary",
                          "--accept-pin-mismatch", "--yes"]) == 0
    assert calls == [(False, "yes"), (True, "d" * 64)]


def test_cli_without_the_flag_carries_no_token(box, monkeypatch):
    calls = []

    def _install(self, stack=None, apply=False, source="", accept_pin_mismatch="", **kw):
        calls.append(accept_pin_mismatch)
        return ActionResult(True, "p", data={"changes": 1, "consent": "c" * 64})
    monkeypatch.setattr(ControllerService, "install", _install)
    cli_main.main(["install", "daemon", "--source", "binary", "--yes"])
    assert calls == ["", ""]


def test_cli_flag_value_must_be_a_consent_token(box, capsys):
    """A value that is neither empty nor a 64-hex token is an error: a stack name right after
    the flag is rejected, not taken as the token."""
    with pytest.raises(SystemExit):
        cli_main.main(["install", "--accept-pin-mismatch", "daemon", "--yes"])
    assert "consent token" in capsys.readouterr().err


@pytest.mark.parametrize("op", ["install", "update"])
def test_cli_meshcom_pin_refusal_without_flag_offers_no_override(box, monkeypatch, capsys, op):
    _published(monkeypatch, "meshcom", "meshcom-qemu", A)
    argv = [op, "meshcom", "--source", "binary", "--yes"]
    seen = {}
    real = ControllerService.binary_install

    def _spy(self, *a, **k):
        r = real(self, *a, **k)
        seen["r"] = r
        return r
    monkeypatch.setattr(ControllerService, "binary_install", _spy)
    assert cli_main.main(argv) != 0
    out = capsys.readouterr().out
    assert "Update LHPC first" in out and "--accept-pin-mismatch" not in out
    assert seen["r"].next_commands == ["lhpc self-update --apply",
                                       "lhpc install meshcom --source pinned --yes"]


@pytest.mark.parametrize("op", ["install", "update"])
def test_cli_meshcom_pin_refusal_with_flag_is_refused_typed(box, monkeypatch, capsys, op):
    _published(monkeypatch, "meshcom", "meshcom-qemu", A)
    seen = {}
    real = ControllerService.binary_install

    def _spy(self, *a, **k):
        r = real(self, *a, **k)
        seen["r"] = r
        return r
    monkeypatch.setattr(ControllerService, "binary_install", _spy)
    assert cli_main.main([op, "meshcom", "--source", "binary", "--accept-pin-mismatch",
                          "--yes"]) != 0
    out = capsys.readouterr().out
    assert "refused even with --accept-pin-mismatch" in out
    assert out.index("Update LHPC first") > out.index("refused even with")
    assert seen["r"].data["override_refused"] == "clone_required"
    assert seen["r"].next_commands == ["lhpc self-update --apply",
                                       "lhpc install meshcom --source pinned --yes"]


def test_cli_check_keeps_the_flag(box, monkeypatch, capsys):
    """Gate 2's P3: `--check` (the read-only preview) forwards `--accept-pin-mismatch`: the
    flagged plan, rc 0 (it used to drop the flag and show the refusal, rc 1)."""
    cid = next(iter(ControllerService()._binary_pins("daemon")))
    _published(monkeypatch, "daemon", cid, A)
    assert cli_main.main(["install", "daemon", "--source", "binary", "--accept-pin-mismatch",
                          "--check"]) == 0
    out = capsys.readouterr().out
    assert "Binary install plan for 'daemon'" in out and "OVER THE PIN CHECK" in out
