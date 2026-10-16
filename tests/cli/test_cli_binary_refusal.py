"""`lhpc install --source binary` / `lhpc update` with no published binary built for this LHPC: the
refusal names self-update first and source second; `--accept-pin-mismatch` is gone (decision D1)."""
from __future__ import annotations

import pytest

from lhpc.adapters.cli import main as cli_main
from lhpc.core import binary_install as bi
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


@pytest.mark.parametrize("verb", ["install", "update"])
def test_the_accept_pin_mismatch_flag_is_gone(box, monkeypatch, capsys, verb):
    monkeypatch.setattr(ControllerService, verb, lambda *a, **k: pytest.fail("must not run"))
    assert cli_main.main([verb, "daemon", "--source", "binary", "--accept-pin-mismatch",
                          "--yes"]) == 2
    assert "unrecognized arguments: --accept-pin-mismatch" in capsys.readouterr().err


@pytest.mark.parametrize("op", ["install", "update"])
@pytest.mark.parametrize("stack", ["daemon", "meshcom"])
def test_cli_none_compatible_names_self_update_then_source(box, monkeypatch, capsys, op, stack):
    cid = ("meshcom-qemu" if stack == "meshcom"
           else next(iter(ControllerService()._binary_pins("daemon"))))
    _published(monkeypatch, stack, cid, A)
    seen = {}
    real = ControllerService.binary_install

    def _spy(self, *a, **k):
        r = real(self, *a, **k)
        seen["r"] = r
        return r
    monkeypatch.setattr(ControllerService, "binary_install", _spy)
    assert cli_main.main([op, stack, "--source", "binary", "--yes"]) != 0
    out = capsys.readouterr().out
    assert "Update LHPC first" in out and "--accept-pin-mismatch" not in out
    assert seen["r"].data["binary_refusal"] == "none_compatible"
    assert seen["r"].next_commands == ["lhpc self-update --apply",
                                       f"lhpc install {stack} --source pinned --yes"]
