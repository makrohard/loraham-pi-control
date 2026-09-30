"""A5: the console's "Backup secrets" block on the LHPC row shows exactly two copy commands and
does nothing itself: no form, no link to a file, no input; no route serves a backup."""
from htmlq import parse

from lhpc.core import secrets_backup, service_secrets


def test_the_block_shows_exactly_the_two_commands_and_nothing_to_click(web, monkeypatch):
    import subprocess
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("rendering the block runs no command")))
    for mod, name in ((secrets_backup, "inventory"), (secrets_backup, "read_archive"),
                      (service_secrets.SecretsOpsMixin, "secrets_backup"),
                      (service_secrets.SecretsOpsMixin, "secrets_restore")):
        monkeypatch.setattr(mod, name, lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("rendering the block reads no secret")))
    doc = parse(web().get("/stacks").get_data(as_text=True))
    block = doc.by_id("controller-backup-secrets")
    assert block is not None
    seg = doc.within(block)
    boxes = [p.text.strip() for p in seg.find("pre")]
    assert boxes == ["lhpc secrets backup",
                     "lhpc secrets restore $HOME/lhpc-secrets-HOSTNAME-YYYYMMDDTHHMMSSZ.tar"]
    assert "not executed by LHPC" in seg.text
    assert not seg.find("form") and not seg.find("input") and not seg.find("a")


def test_no_route_serves_a_backup_file(web):
    app = web().application
    assert not [r.rule for r in app.url_map.iter_rules() if "secret" in r.rule or ".tar" in r.rule]
