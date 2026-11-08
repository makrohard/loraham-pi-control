"""Item 7d part 2 (DEC-W52): a listed MeshCore instance in a repeater mode has its own openHop file under its
{instance_dir} (state/<rid>/openhop/config.yaml) with its own repeater identity and dashboard password; slot 0's
file and its legacy copies are not touched. Through `lhpc stack start meshcore@r3`, with openHop's file helper
replaced by an in-memory one that writes the path it is given (the venv's helper is the host suite's)."""
import json
from pathlib import Path

import pytest

from radios_fixture import R1, R3

from lhpc.core.commands import Instance
from lhpc.core import meshcore_identity as mi
from lhpc.core import openhop_store as ops
from lhpc.core.paths import Paths

LISTED = 'meshcore = ["r3"]\n'


def _helper(calls):
    def helper(svc, verb, path, *keys, stdin=""):
        p = Path(path)
        calls.append((verb, str(p)))
        conf = json.loads(p.read_text()) if p.exists() else {}
        if verb == "get":
            return json.dumps({k: conf.get(k) for k in keys})
        if verb in ("set", "render"):
            conf.update(json.loads(stdin))
            if verb == "render":
                return json.dumps(conf)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(conf))
        return ""
    return helper


def _repeater(tmp_path, name="Relay R3", rid=""):
    # rid: the listed instance's own settings, config/stacks/meshcore@<rid>.toml (7f, DEC-W60: never slot 0's)
    from lhpc.adapters.cli import main as cli
    from lhpc.core import config as cfgmod
    if rid:
        cfgmod.update_stack_config(Paths(runtime_root=tmp_path), "meshcore",
                                   {"file_mode": "repeater", "file_repeater_name": name}, inst=Instance(1, rid))
        return
    svc = cli.ControllerService()
    r = svc.save_config("meshcore", {"file_mode": "repeater", "file_repeater_name": name})
    assert r.ok, (r.summary, r.details)


def test_a_listed_instance_gets_its_own_openhop_file_and_slot_0_is_untouched(box, tmp_path, monkeypatch):
    run = box((R1, R3), LISTED, ports=(5010, 8010, 8798))
    calls = []
    monkeypatch.setattr(ops, "_helper", _helper(calls))
    _repeater(tmp_path, "Relay 0")                                # slot 0 a repeater too: nothing of it is lent
    _repeater(tmp_path, rid="r3")
    slot0_key = mi.secret_path(Paths(runtime_root=tmp_path), mi.REPEATER_IDENTITY_FILENAME)
    slot0_key.parent.mkdir(parents=True, exist_ok=True)
    slot0_key.write_text("ab" * 32 + "\n")                        # slot 0's legacy identity, not yet migrated
    run("start", "meshcore@r3")
    mine = tmp_path / "state" / "r3" / "openhop" / "config.yaml"
    assert mine.is_file(), calls
    conf = json.loads(mine.read_text())
    assert conf.get("repeater.node_name") == "Relay R3"
    assert conf.get("repeater.identity_key") and conf.get("repeater.security.admin_password"), conf
    assert not (tmp_path / "state" / "openhop" / "config.yaml").exists()              # slot 0's file: never written
    assert all(str(tmp_path / "state" / "openhop") not in p for _v, p in calls), calls
    assert conf["repeater.identity_key"] != "ab" * 32                 # its own identity, never slot 0's
    assert slot0_key.read_text() == "ab" * 32 + "\n"                  # slot 0's legacy copy: neither taken nor removed


@pytest.mark.parametrize("held,written", [(("r1",), True), (("r1", "r3"), False)])
def test_an_instances_file_waits_only_for_its_own_records(box, tmp_path, monkeypatch, held, written):
    # blocked(): a same-boot, unverified node record of r3 holds meshcore@r3's file back; slot 0's record of r1
    # does not (before 7d, every node record counted for every start).
    from lhpc.core import lifecycle as lcmod
    run = box((R1, R3), LISTED, ports=(5010, 8010, 8798))
    monkeypatch.setattr(ops, "_helper", _helper([]))
    _repeater(tmp_path, rid="r3")
    monkeypatch.setattr(lcmod, "current_boot_id", lambda: "boot-now")
    real = lcmod.Lifecycle.owned_records

    def records(self, cid, band=None, role="", rid=None):
        if cid != "meshcore-node":
            return real(self, cid, band, role, rid)
        return [{"component": cid, "rid": r, "boot_id": "boot-now"} for r in held if rid in (None, r)]
    monkeypatch.setattr(lcmod.Lifecycle, "owned_records", records)
    rc, out, _launches = run("start", "meshcore@r3")
    assert (tmp_path / "state" / "r3" / "openhop" / "config.yaml").is_file() is written, out
    if not written:
        assert rc == 1 and ops.UNVERIFIED.split(";")[0] in out, out


def test_slot_0s_file_stays_where_it_was(box, tmp_path, monkeypatch):
    run = box((R1, R3), None, ports=(5000, 8000, 8788))
    calls = []
    monkeypatch.setattr(ops, "_helper", _helper(calls))
    _repeater(tmp_path, "Relay 0")
    run("start", "meshcore")
    assert (tmp_path / "state" / "openhop" / "config.yaml").is_file(), calls
    assert not (tmp_path / "state" / "r3").exists()
