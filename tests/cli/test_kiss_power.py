"""Radio power item 3 (plan v3 b0434470, the maintainer's decision 1b377343 "kiss"/"chat", plan audit B1/N5): LHPC
writes the KISS TNC's config per instance with `power` = the kiss panel's POWER (the one setting) and the TNC reads
that file, not its checkout's example; chat's and kiss's POWER are the operator's (not greyed); kiss's TNC puts its
own POWER back on every frequency switch, so a kiss POWER change takes effect at its next start and Apply live leaves
it out and says so. Through `lhpc stack start`, the panel view, the settings save, write_config_files and Apply."""
import dataclasses

import pytest
import radios_fixture
from radios_fixture import R1

from lhpc.core import commands, radio_start, radios
from lhpc.core import config as cfgmod
from lhpc.core.commands import Instance
from lhpc.core.manifest import load_manifest
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService

R1_433 = Instance("r1", band="433")


def _conf(tmp_path, rid="r1"):
    path = tmp_path / "config" / "files" / rid / "loraham_kiss_tnc.conf"
    return path.read_text() if path.is_file() else None


def _svc(tmp_path, rows=(R1,)):
    radios_fixture.write(tmp_path, rows)
    fake = FakeSystem(unix_replies={"/tmp/loraconf-r1.sock": b"STATUS RADIO=READY ID=r1 BOUND=- TXMODE=MANAGED\n"})
    return ControllerService(system=fake.system, paths=Paths(runtime_root=tmp_path)), fake


def test_a_kiss_start_writes_its_own_file_with_the_panels_power_and_the_tnc_reads_it(box, tmp_path):
    # red-before: no file is written and the TNC's argv names its checkout's loraham_kiss_tnc.conf.example
    rc, out, launches = box((R1,), ports=(8001,))("start", "kiss@r1")
    assert rc == 0 and ("loraham-kiss-tnc", "433", "r1") in launches, out
    assert _conf(tmp_path) == "power=17\n"                                   # loraham (SPI): min(17, 17)
    comp = next(c for s in load_manifest() if s.id == "kiss" for c in s.components if c.id == "loraham-kiss-tnc")
    tok = comp.run_argv[comp.run_argv.index("--config") + 1]
    (path,) = commands.expand_argv([tok], comp, {}, None, str(tmp_path), "/src", "433", inst=R1_433)
    assert path == str(tmp_path / "config" / "files" / "r1" / "loraham_kiss_tnc.conf")
    assert "loraham_kiss_tnc.conf.example" not in comp.run_argv


@pytest.mark.parametrize("row_dp, inst_dp, want", [((), None, "17"), ((("POWER", "12"),), None, "12"),
                                                   ((("POWER", "12"),), "11", "11")])
def test_the_files_power_is_the_instances_over_the_rows_over_the_default(tmp_path, row_dp, inst_dp, want):
    # red-before: no `power` is written at all (the TNC sent its example's 17 whatever the panel said)
    svc, _fake = _svc(tmp_path, (dataclasses.replace(R1, dp=row_dp),))
    if inst_dp:
        cfgmod.save_stack_config(svc._paths, "kiss", {"dp_POWER": inst_dp}, inst=Instance("r1"))
    (cw,) = svc.write_config_files("kiss", "433", inst=R1_433)
    assert cw.status == "written", cw
    assert _conf(tmp_path) == f"power={want}\n"


def test_graywolf_reaches_the_tnc_through_the_same_file(box, tmp_path, monkeypatch):
    # graywolf@r1 starts kiss@r1 (its chain on its own radio): the TNC it brings up reads kiss@r1's file
    from lhpc.adapters.cli import main as cli
    from lhpc.core.jobs import JobResult, JobState
    from lhpc.core.lifecycle import Lifecycle
    run = box((R1,), ports=(8001, 8080))
    assert cli.ControllerService().set_operator_identity(callsign="DL1ABC").ok     # graywolf is licensed
    monkeypatch.setattr(Lifecycle, "run_required_post_start",                      # its provisioning is not ours
                        lambda self, *a, **k: JobResult(name="post", state=JobState.SUCCEEDED, returncode=0, log_path=""))
    rc, out, launches = run("start", "graywolf@r1")
    assert ("loraham-kiss-tnc", "433", "r1") in launches, (rc, out, launches)
    assert _conf(tmp_path) == "power=17\n"


@pytest.mark.parametrize("stack", ["chat", "kiss"])
def test_chats_and_kisss_power_is_the_operators(tmp_path, stack):
    # red-before: POWER greyed (app-owned) on both; the other radio params the app sets stay greyed
    svc, _fake = _svc(tmp_path)
    rows = {r["name"]: r for r in svc.daemon_params_view(stack, rid="r1")["rows"]}
    assert rows["POWER"]["app_owned"] is False
    assert rows["SF"]["app_owned"] is True and rows["BW"]["app_owned"] is True
    assert rows["POWER"]["at_start"] is (stack == "kiss")                    # kiss: at the next start (B1)


def test_the_hidden_power_row_is_never_saved_or_read(tmp_path):
    # the file's `power` is LHPC's (derived): a save refuses it, a hand-stored value is never written
    svc, _fake = _svc(tmp_path)
    res = svc.save_config_bundle("kiss", values={"file_power": "5"}, inst=R1_433)
    assert not res.ok and "power: derived: LHPC computes it" in " ".join(res.details), (res.summary, res.details)
    cfgmod.save_stack_config(svc._paths, "kiss", {"file_power": "5"}, inst=Instance("r1"))
    svc._invalidate_config()
    svc.write_config_files("kiss", "433", inst=R1_433)
    assert _conf(tmp_path) == "power=17\n"


@pytest.mark.parametrize("stack, sent", [("kiss", False), ("chat", True)])
def test_apply_live_leaves_kisss_power_to_its_next_start(tmp_path, monkeypatch, stack, sent):
    # B1: the TNC sends its own POWER on every TX/RX switch; red-before: Apply live sent kiss's POWER and said
    # "applied" for a value the TNC puts back at its next frequency switch. Chat (no POWER in its SET) applies live.
    svc, fake = _svc(tmp_path)
    monkeypatch.setattr(radio_start, "radio_clients", lambda svc, rid, sid: ([], True))      # the instance runs
    cfgmod.save_stack_config(svc._paths, stack, {"dp_POWER": "11"}, inst=Instance("r1"))
    res = svc.apply_daemon_params(stack, rid="r1")
    power_sets = [line for _p, line in fake.sent if line.startswith(b"SET POWER")]
    assert power_sets == ([b"SET POWER=11\n"] if sent else []), (power_sets, res.summary)    # G2: exactly one, or none
    assert ("POWER" in res.data["applied"]) is sent
    if not sent:
        assert res.data["at_start"] == ["POWER"]
        assert "POWER: takes effect at the next start of kiss@r1" in res.details, res.details


def test_a_kiss_render_with_no_power_refuses_by_name(tmp_path):
    # N5: a rid that is no enabled radio has no POWER: the render refuses, no file holds a bare `power=` (the TNC
    # would exit "Config load failed"), so the start is blocked
    svc, _fake = _svc(tmp_path, (R1, radios.Radio("r3", "off", "loraham", "868", enabled=False)))
    (cw,) = svc.write_config_files("kiss", "868", inst=Instance("r3", band="868"))
    assert cw.status == "failed" and "kiss@r3: no POWER for its radio" in cw.detail, cw
    assert _conf(tmp_path, "r3") is None


@pytest.mark.parametrize("stack, hint", [("kiss", True), ("chat", False)])
def test_the_panel_shows_power_editable_and_kiss_at_the_next_start(web, stack, hint):
    # 3d/3f on the page: POWER's row is not greyed (SF's still is) and only kiss's says "at the next start"
    from htmlq import parse
    doc = parse(web().get(f"/stacks?open={stack}&dp={stack}").get_data(as_text=True))
    panel = doc.within(doc.by_id(f"stack-daemon-params-{stack}"))
    rows = {doc.within(tr).find("th")[0].text: tr for tr in panel.find("tr")}
    assert not rows["POWER"].has_class("greyed") and rows["SF"].has_class("greyed")
    assert ("Takes effect at the next start." in doc.within(rows["POWER"]).text) is hint


@pytest.mark.parametrize("extra", [{}, {"hidden": True, "kind": "str"}])
def test_the_loader_admits_panel_power_only_on_a_hidden_int(extra):
    from lhpc.core.manifest import ManifestError, _parse_file_config
    param = {"name": "power", "key": "power", "kind": "int", "panel_power": True, **extra}
    with pytest.raises(ManifestError, match="'power' has panel_power and must be a hidden int"):
        _parse_file_config({"path": "{runtime}/x.conf", "fmt": "env", "param": [param]})


@pytest.mark.parametrize("power", ["21", "22"])
def test_kiss_on_the_meshstick_takes_21_and_22_into_its_file(box, tmp_path, power):
    # G1-RADIO-POWER-CODE B1: LHPC admits the Meshstick's 21..22 for kiss (his P7) and writes it into the TNC's file;
    # the pinned TNC (v0.6.4) parses power 0..22, so the start is not stopped by its own config load
    from lhpc.adapters.cli import main as cli
    from radios_fixture import R2_868, R3
    run = box((R1, R2_868, R3), ports=(8267,))
    assert cli.ControllerService().save_daemon_params("kiss", {"POWER": power}, rid="r3").ok
    rc, out, launches = run("start", "kiss@r3")
    assert rc == 0 and ("loraham-kiss-tnc", "868", "r3") in launches, out
    assert _conf(tmp_path, "r3") == f"power={power}\n"


@pytest.mark.parametrize("client, sent", [("kiss@r1", False), ("chat@r1", True)])
def test_the_daemons_apply_leaves_power_to_a_running_kiss_s_next_start(tmp_path, monkeypatch, client, sent):
    # 3f for the daemon's own Apply (a DIY-guide deep check): with kiss@r1 running, the daemon's POWER sent live is put
    # back by the TNC on its next frequency switch; red-before: "SET POWER=11" sent and reported applied. Chat (no
    # POWER in its SET) keeps it: sent live.
    svc, fake = _svc(tmp_path)
    monkeypatch.setattr(radio_start, "radio_clients", lambda svc, rid, sid: ([client], False))   # another stack runs
    assert svc.save_daemon_params("daemon", {"POWER": "11"}, rid="r1").ok
    res = svc.apply_daemon_params("daemon", rid="r1")
    power_sets = [line for _p, line in fake.sent if line.startswith(b"SET POWER")]
    assert power_sets == ([b"SET POWER=11\n"] if sent else []), (power_sets, res.summary)    # G2: exactly one, or none
    assert ("POWER" in res.data["applied"]) is sent and (res.data["at_start"] == ([] if sent else ["POWER"]))
    if not sent:
        assert "POWER: takes effect at the next start of kiss@r1" in res.details, res.details
