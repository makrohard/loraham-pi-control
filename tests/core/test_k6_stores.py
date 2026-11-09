"""K6 (K6-SCOPE-v2, G1-K4B 1): an instance's settings are its own file, config/stacks/<sid>@<rid>.toml, whatever
other radios the hardware list holds: adding, disabling or removing another radio never moves an existing
instance to another store."""
import dataclasses

import pytest
import radios_fixture

from lhpc.core import radios
from lhpc.core.commands import Instance
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService

R1, R3 = radios.Radio("r1", "a", "uputronics", "433", ce="ce0"), radios.Radio("r3", "b", "uputronics", "433", ce="ce1")


def _svc(tmp_path, rows):
    radios_fixture.write(tmp_path, rows)
    return ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))


def _port(svc, rid):
    return svc.stack_config("loraham-kiss-tnc", "433", Instance(rid, band="433")).get("kiss_port")


@pytest.mark.parametrize("change", ["remove-r1", "disable-r1", "add-r1"])
def test_another_radios_change_never_moves_an_instances_store(tmp_path, change):
    before = (R3,) if change == "add-r1" else (R1, R3)
    svc = _svc(tmp_path, before)
    assert svc.save_config_bundle("kiss", values={"kiss_port": "8061"}, inst=Instance("r3", band="433")).ok
    assert (tmp_path / "config" / "stacks" / "kiss@r3.toml").is_file()
    assert _port(svc, "r3") == "8061"
    after = {"remove-r1": (R3,), "disable-r1": (dataclasses.replace(R1, enabled=False), R3), "add-r1": (R1, R3)}[change]
    svc = _svc(tmp_path, after)
    assert _port(svc, "r3") == "8061", change


def _graywolf_tnc(svc, inst):
    from lhpc.core import commands
    gw = svc.stack("graywolf").component("graywolf")
    step = next(st["argv"] for st in gw.post_steps if st.get("kind") == "exec")       # the provisioning: its TNC
    return commands.expand_argv([t for t in step if t in ("{param:tnc_host}", "{param:tnc_port}")], gw,
                                svc.stack_config("graywolf", inst.band, inst), svc.config().operator, "/rt", "/src",
                                inst.band, inst=inst)


def _kiss_port(svc, inst):
    from lhpc.core import commands
    kiss = svc.stack("kiss").component("loraham-kiss-tnc")
    argv = commands.expand_argv(kiss.run_argv, kiss, svc.stack_config("loraham-kiss-tnc", inst.band, inst),
                                svc.config().operator, "/rt", "/src", inst.band, inst=inst)
    return argv[argv.index("--kiss-port") + 1]


@pytest.mark.parametrize("saved,want", [(None, "8134"), ("9000", "9133")])
def test_k6s2_graywolfs_default_tnc_port_is_kiss_at_its_radios_effective_port(tmp_path, saved, want):
    # K6S-2 / G1-K7G (576c2f80): {port_of:kiss:8001} is the port kiss@r2 LISTENS on: its saved base + 133 (r2), else
    # the manifest's 8001 + 133.
    svc = _svc(tmp_path, radios_fixture.RADIOS)
    r2 = Instance("r2", band="868")
    if saved:
        assert svc.save_config_bundle("kiss", values={"kiss_port": saved}, inst=r2).ok
    assert _kiss_port(svc, r2) == want
    assert _graywolf_tnc(svc, r2) == ["--tnc-host", "127.0.0.1", "--tnc-port", want]


def test_k6s2_an_explicit_graywolf_tnc_port_wins(tmp_path):
    svc = _svc(tmp_path, radios_fixture.RADIOS)
    r2 = Instance("r2", band="868")
    assert svc.save_config_bundle("kiss", values={"kiss_port": "9000"}, inst=r2).ok
    assert svc.save_config_bundle("graywolf", values={"tnc_port": "9555"}, inst=r2).ok
    assert _graywolf_tnc(svc, r2)[-1] == "9555"


@pytest.mark.parametrize("rid,where", [("", "kiss.toml"), ("r1", "kiss@r1.toml")])
def test_a_pending_migration_candidate_clears_the_file_it_was_read_from(tmp_path, monkeypatch, rid, where):
    # K6: a candidate recorded with its instance's rid clears that instance's own file; one without a rid (a journal
    # written before K6, crossed by the update) means the global <sid>.toml, as it did. The proof is the
    # transition's (tests/install/test_selfupdate_migration.py); here it is given, to pin the file choice alone.
    from lhpc.core import config as cfgmod
    from lhpc.core.services import ControllerService
    svc = _svc(tmp_path, radios_fixture.RADIOS)
    tnc = svc.stack("kiss").component("loraham-kiss-tnc")
    p = next(q for q in tnc.run_params if q.name == "rx_only")
    other = "kiss@r1.toml" if not rid else "kiss.toml"
    for name in (where, other):
        cfgmod.save_stack_config(svc._paths, "kiss", {"rx_only": "off"}, inst=Instance("r1") if "@" in name else None)
    monkeypatch.setattr(ControllerService, "_prove_candidate", lambda self, cand, head: (p, "off"))
    cand = {"stack": "kiss", "band": "433", "key": "rx_only", "kind": "r", "comp": tnc.id, "name": "rx_only",
            "expected": "off", **({"rid": rid} if rid else {})}
    migrated, remaining = svc._run_migration([cand], "head")
    stacks = tmp_path / "config" / "stacks"
    assert (migrated, remaining) == (1, [])
    assert "rx_only" not in cfgmod._load_runtime_toml(svc._paths, stacks / where)
    assert cfgmod._load_runtime_toml(svc._paths, stacks / other)["rx_only"] == "off"          # the other file: kept


DERIVED = ("data_socket", "config_socket", "companion_port", "db", "repeater_dashboard_port", "repeater_state_dir",
           "gps_file", "cli_lock", "restart_cmd", "rf_log_path")
SECRET = (("meshcore", "privatekey"), ("meshcore", "repeater_key"), ("meshcore", "dashboard_password"),
          ("reticulum", "ifac_netkey"), ("reticulum", "internet_ifac_netkey"))


def test_w384q_the_derived_rows_are_exactly_the_hidden_ones_computed_from_the_instance(tmp_path):
    from lhpc.core.service_params import _derived
    svc = _svc(tmp_path, radios_fixture.RADIOS)
    got = {p.name for s in svc.stacks() for c in s.components
           for p in (*c.run_params, *(c.config_file.params if c.config_file else ())) if _derived(p)}
    assert got == set(DERIVED)
    assert not got & {n for _s, n in SECRET}


@pytest.mark.parametrize("name", DERIVED)
def test_w384q_a_derived_row_is_refused_at_save_and_a_stored_one_is_never_read(tmp_path, name):
    import tomllib
    from lhpc.core import config as cfgmod
    svc = _svc(tmp_path, radios_fixture.RADIOS)
    r2 = Instance("r2", band="868")
    r = svc.save_config_bundle("meshcore", values={f"file_{name}": "/x/9999"}, inst=r2)
    assert not r.ok and f"{name}: derived: LHPC computes it" in " ".join(r.details), (r.summary, r.details)
    want = svc.file_config_values("meshcore", "868", r2)[name]
    cfgmod.save_stack_config(svc._paths, "meshcore", {f"file_{name}": "/x/9999"}, inst=r2)    # a hand edit
    svc._invalidate_config()
    assert svc.file_config_values("meshcore", "868", r2)[name] == want != "/x/9999"
    if name == "repeater_dashboard_port":                     # the port mismatch the review found: 8133 rendered
        assert all(w.status == "written" for w in svc.write_config_files("meshcore", "868", inst=r2))
        doc = tomllib.loads((tmp_path / "config" / "files" / "r2" / "meshcore.toml").read_text())
        assert doc["repeater"]["dashboard_port"] == 8133


@pytest.mark.parametrize("sid,name", SECRET)
def test_w384q_a_secret_hidden_row_keeps_todays_behaviour(tmp_path, sid, name):
    # DEC-W384q (2): no change in 0.14.0, OPEN to the maintainer: a saved value is stored and read as before
    svc = _svc(tmp_path, radios_fixture.RADIOS)
    inst = Instance("r2", band="868") if sid == "meshcore" else Instance("r1", band="433")
    assert svc.save_config_bundle(sid, values={f"file_{name}": "Operator-Set-123"}, inst=inst).ok
    assert svc.file_config_values(sid, inst.band, inst)[name] == "Operator-Set-123"


@pytest.mark.parametrize("inst", [Instance("r2", band="868"), Instance("r2")], ids=["with-band", "bare"])
def test_g1_k6c_n5_an_instances_save_is_compared_with_its_own_bands_defaults(tmp_path, inst):
    # kiss@r2 (868): saving tx_freq = 868's default (869.525) stores nothing; before, it was compared with the
    # stack default band's (433.775) and stored as an override
    svc = _svc(tmp_path, radios_fixture.RADIOS)
    assert svc.save_config_bundle("kiss", values={"tx_freq": "869.525"}, inst=inst).ok
    assert "tx_freq" not in svc._stack_config_cached("kiss", "", Instance("r2"))
    assert svc.stack_config("loraham-kiss-tnc", "868", Instance("r2", band="868"))["tx_freq"] == "869.525"
