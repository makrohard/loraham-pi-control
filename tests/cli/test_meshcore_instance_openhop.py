"""Item 7d part 2 (DEC-W52): a listed MeshCore instance in a repeater mode has its own openHop file under its
{instance_dir} (state/<rid>/openhop/config.yaml) with its own repeater identity and dashboard password; the bare stack's
file and its legacy copies are not touched. Through `lhpc stack start meshcore@r3`, with openHop's file helper
replaced by an in-memory one that writes the path it is given (the venv's helper is the host suite's)."""
import json
from pathlib import Path

import pytest

from radios_fixture import R1, R2_868, R3

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
    # rid: the listed instance's own settings, config/stacks/meshcore@<rid>.toml (7f, DEC-W60: never the bare stack's)
    from lhpc.adapters.cli import main as cli
    from lhpc.core import config as cfgmod
    if rid:
        cfgmod.update_stack_config(Paths(runtime_root=tmp_path), "meshcore",
                                   {"file_mode": "repeater", "file_repeater_name": name}, inst=Instance(rid))
        return
    svc = cli.ControllerService()
    r = svc.save_config("meshcore", {"file_mode": "repeater", "file_repeater_name": name})
    assert r.ok, (r.summary, r.details)


def test_an_instance_gets_its_own_openhop_file_and_the_bare_one_is_untouched(box, tmp_path, monkeypatch):
    run = box((R1, R2_868, R3), ports=(5266, 8266, 9054))
    calls = []
    monkeypatch.setattr(ops, "_helper", _helper(calls))
    _repeater(tmp_path, "Relay 0")                                # the bare copy a repeater too: nothing of it is lent
    _repeater(tmp_path, rid="r3")
    bare_key = mi.secret_path(Paths(runtime_root=tmp_path), mi.REPEATER_IDENTITY_FILENAME)
    bare_key.parent.mkdir(parents=True, exist_ok=True)
    bare_key.write_text("ab" * 32 + "\n")                         # the bare copy's legacy identity, not yet migrated
    run("start", "meshcore@r3")
    mine = tmp_path / "state" / "r3" / "openhop" / "config.yaml"
    assert mine.is_file(), calls
    conf = json.loads(mine.read_text())
    assert conf.get("repeater.node_name") == "Relay R3"
    assert conf.get("repeater.identity_key") and conf.get("repeater.security.admin_password"), conf
    assert not (tmp_path / "state" / "openhop" / "config.yaml").exists()              # the bare file: never written
    assert all(str(tmp_path / "state" / "openhop") not in p for _v, p in calls), calls
    assert conf["repeater.identity_key"] != "ab" * 32                 # its own identity, never the bare one's
    assert bare_key.read_text() == "ab" * 32 + "\n"                   # the bare legacy copy: neither taken nor removed


@pytest.mark.parametrize("held,written", [(("r1",), True), (("r1", "r3"), False)])
def test_an_instances_file_waits_only_for_its_own_records(box, tmp_path, monkeypatch, held, written):
    # blocked(): a same-boot, unverified node record of r3 holds meshcore@r3's file back; the bare stack's record of r1
    # does not (before 7d, every node record counted for every start).
    from lhpc.core import lifecycle as lcmod
    run = box((R1, R2_868, R3), ports=(5266, 8266, 9054))
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


def test_the_bands_only_radios_file_sits_under_its_rid_too(box, tmp_path, monkeypatch):
    # K4b: paths sit under <rid>/ for every instance; meshcore@r3 (868's only radio here) reads the band's store
    # (a band-store save marks its reader <sid>@<band's first radio>) and writes state/r3/openhop, never the 0.13.x file.
    run = box((R1, R3), ports=(5266, 8266, 9054))
    calls = []
    monkeypatch.setattr(ops, "_helper", _helper(calls))
    _repeater(tmp_path, "Relay 0")
    run("start", "meshcore@r3")
    mine = tmp_path / "state" / "r3" / "openhop" / "config.yaml"
    assert mine.is_file(), calls
    assert json.loads(mine.read_text()).get("repeater.node_name") == "Relay 0"
    assert not (tmp_path / "state" / "openhop" / "config.yaml").exists(), calls


def test_a_repeater_mode_save_reads_its_own_radios_name_never_another_radios(box, tmp_path, monkeypatch, capsys):
    # K8 (no inst=None): `lhpc config meshcore@r3 mode repeater` needs r3's OWN repeater name. r2's openHop file holds
    # one and r3 has none: the save is refused by name and writes nothing (before, the check read the band's first
    # radio's file and let r3's save through without a name).
    from lhpc.adapters.cli import main as cli
    box((R1, R2_868, R3))
    monkeypatch.setattr(ops, "_helper", _helper([]))
    r2_file = tmp_path / "state" / "r2" / "openhop" / "config.yaml"
    r2_file.parent.mkdir(parents=True)
    r2_file.write_text(json.dumps({"repeater.node_name": "Relay R2"}))
    r3_store = tmp_path / "config" / "stacks" / "meshcore@r3.toml"
    before = r3_store.read_bytes() if r3_store.exists() else None
    capsys.readouterr()
    rc = cli.main(["config", "meshcore@r3", "mode", "repeater"])
    out = capsys.readouterr()
    assert rc != 0, out
    assert "needs the repeater's own node name" in out.out + out.err, out
    assert (r3_store.read_bytes() if r3_store.exists() else None) == before
