"""U7 (plan v6, D3): `lhpc known-working <stack>` is gone. Red at 912c37d5: `lhpc --help` listed it."""

import pytest

from lhpc.adapters.cli.main import main


def _commands(capsys) -> set:
    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0
    lines = capsys.readouterr().out.splitlines()
    # the sub-command table: four spaces, the verb, then its help
    return {ln.split()[0] for ln in lines if ln.startswith("    ") and not ln.startswith("     ") and ln.split()}


def test_help_lists_no_known_working_verb(capsys):
    verbs = _commands(capsys)
    assert "source-check" in verbs                              # anchor: a sibling verb is listed
    assert "known-working" not in verbs


def test_the_known_working_verb_is_refused(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["known-working", "chat"])
    assert exc.value.code == 2
    assert "invalid choice: 'known-working'" in capsys.readouterr().err


def test_cli_update_pinned_plans_the_manifest_pin_over_an_old_store(tmp_path, monkeypatch, capsys):
    """`lhpc update kiss --source pinned --yes` through cli.main beside a 0.13.x known-working store naming
    another commit: the adoption is not handed the store's commit (second read M3: the public path; red at
    912c37d5, where the store's commit was planned)."""
    import json

    from lhpc.adapters.cli import main as cli
    from lhpc.core.install import Installer, PlanAction
    from lhpc.core.paths import Paths
    from lhpc.core.probes.backends import FakeSystem
    from lhpc.core.services import ActionResult, ControllerService
    system = FakeSystem().system
    real = ControllerService
    monkeypatch.setattr(cli, "ControllerService",
                        lambda *a, **k: real(system=system, paths=Paths(runtime_root=tmp_path)))
    svc = real(system=system, paths=Paths(runtime_root=tmp_path))
    old = "b" * 40
    store = tmp_path / "profiles" / "known-working" / "kiss.json"
    store.parent.mkdir(parents=True)
    store.write_text(json.dumps({"version": 1, "compositions": [{"hash": "f" * 64, "validated": {}, "entries": {
        c.id: {"commit": old, "selector": "pinned", "remote": svc._effective_remote(c),
               "source_rel": c.source.path, "strategy": ""}
        for c in svc.stack("kiss").components if c.source is not None}}]}))
    got = []

    def adopt(self, comp, force=False, source="pinned", pinned_expected=None, locked=False):
        got.append((comp.id, source, pinned_expected))
        return PlanAction("adopt", "", f"adopt {comp.id}", status="done", detail="(fake)")
    monkeypatch.setattr(Installer, "adopt_source", adopt)
    monkeypatch.setattr(ControllerService, "build", lambda self, *a, **k: ActionResult(True, "built"))
    main(["update", "kiss", "--source", "pinned", "--yes"])
    out = capsys.readouterr().out
    assert got, out                                            # anchor: the update adopted the kiss source
    assert all(src == "pinned" and old not in (exp or ("",))[0] for _c, src, exp in got), got
    assert "known working (operator-confirmed" not in out
