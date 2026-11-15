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
