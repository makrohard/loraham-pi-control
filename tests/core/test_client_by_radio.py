"""Stage L5, W3 (B4 commit 2): a daemon client runs as its band's one radio's instance and renders that radio's
sockets; with no such radio, or two, its start is refused by name, and an unresolved `{rid}` never renders."""
import pytest
import radios_fixture

from lhpc.core import commands, radio_start, radios
from lhpc.core.config import Config
from lhpc.core.lifecycle import Lifecycle
from lhpc.core.outcomes import Outcome
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService

NO_LIST = None
TWO_433 = (radios.Radio("r1", "a", "uputronics", "433", ce="ce0"), radios.Radio("r2", "b", "uputronics", "433", ce="ce1"))
OFF = (radios.Radio("r1", "low", "loraham", "433", enabled=False),)
REFUSED = "no radio for its daemon sockets: "


def _svc(tmp_path, rows):
    if rows is not NO_LIST:
        radios_fixture.write(tmp_path, rows)
    return ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))


# The design table (rule 1): hardware list x band x component -> the instance's radio, or the refusal's reason.
CASES = [
    (NO_LIST, "loraham-kiss-tnc", "433", None, "no hardware list"),
    ((), "loraham-kiss-tnc", "433", None, "no enabled radio on 433 MHz"),
    (OFF, "loraham-kiss-tnc", "433", None, "no enabled radio on 433 MHz"),
    (radios_fixture.RADIOS, "loraham-kiss-tnc", "433", "r1", ""),
    (radios_fixture.RADIOS, "loraham-kiss-tnc", "868", "r2", ""),
    (radios_fixture.RADIOS, "meshcom-bridge", "", None, "2 radios can run it for this stack: r1 (low), r2 (high); name one"),
    (TWO_433, "meshcom-bridge", "433", "r1", ""),       # DEC-W16: slot 0 runs on the band's first unlisted radio
    (TWO_433, "loraham-kiss-tnc", "868", None, "no enabled radio on 868 MHz"),
    (NO_LIST, "graywolf", "433", "", ""),              # not a daemon client: slot 0 without a radio, any list
    (TWO_433, "graywolf", "", "", ""),
]


@pytest.mark.parametrize("rows,cid,band,rid,why", CASES)
def test_a_client_runs_as_its_bands_one_radio_or_is_refused_by_name(tmp_path, rows, cid, band, rid, why):
    svc = _svc(tmp_path, rows)
    comp = next(c for st in svc.stacks() for c in st.components if c.id == cid)
    inst, refusal = radio_start.client_instance(svc, comp, band)
    if rid is None:
        assert (inst, refusal) == (None, REFUSED + why)
    else:
        assert refusal == "" and (inst.slot, inst.rid) == (0, rid)


def test_an_unresolved_radio_template_never_renders(tmp_path):
    # W3-1: slot 0 without a radio has no daemon socket to name; every render of a `{rid}` param refuses it.
    svc = _svc(tmp_path, NO_LIST)
    comp = svc.stack("kiss").component("loraham-kiss-tnc")
    src = str(svc._paths.resolve_source(comp.source.path))
    want = "needs this instance's radio (no radio id given)"
    with pytest.raises(commands.CommandError, match=r"^data_socket: needs this instance's radio \(no radio id"):
        commands.expand_argv(comp.run_argv, comp, {}, Config().operator, str(tmp_path), src, "433")
    opt, tpl = comp.process.instance_arg
    with pytest.raises(commands.CommandError) as e:
        commands.instance_value(tpl, comp, {}, str(tmp_path), src, "433")
    assert e.value.args[0].endswith(want)
    with pytest.raises(commands.CommandError):            # a saved value carrying the template is refused too
        commands.expand_argv(comp.run_argv, comp, {"data_socket": "/tmp/loraf-{rid}.sock"}, Config().operator,
                             str(tmp_path), src, "433")
    argv = commands.expand_argv(comp.run_argv, comp, {}, Config().operator, str(tmp_path), src, "433",
                                inst=commands.Instance(0, "r7"))
    assert "/tmp/loraf-r7.sock" in argv and not any("{rid}" in a for a in argv)


@pytest.mark.parametrize("rows,band,listed,why", [
    (NO_LIST, "433", None, "no hardware list"),
    ((), "433", None, "no enabled radio on 433 MHz"),
    # DEC-W16: two radios no longer refuse slot 0 (it runs on r1); with both listed it has no radio left.
    (TWO_433, "433", 'kiss = ["r1", "r2"]\n', "every enabled radio of 433 MHz holds a listed instance of kiss (r1, r2): "
                                              "start one of them as kiss@<rid>")])
def test_a_client_start_without_its_radio_is_blocked_before_any_launch(tmp_path, monkeypatch, rows, band, listed, why):
    # Through the public start: the TNC's refusal is BLOCKED with the reason; nothing is launched.
    svc = _svc(tmp_path, rows)
    if listed:
        (tmp_path / "config" / "instances.toml").write_text(listed)
    (tmp_path / "src" / "loraham-kiss-tnc").mkdir(parents=True)
    monkeypatch.setattr(ControllerService, "hardware_configured", lambda self: True)
    monkeypatch.setattr(ControllerService, "_ensure_daemon", lambda self, *a, **k: ([], True, ""))
    for name in ("is_built", "_source_present"):
        monkeypatch.setattr(ControllerService, name, lambda self, c: True)
    monkeypatch.setattr(ControllerService, "binary_behind", lambda self, c: "")
    launched = []
    monkeypatch.setattr(Lifecycle, "start", lambda self, s, c, *a, **k: launched.append(c.id))
    res = svc.start("kiss", apply=True, band=band)
    tnc = next(r for r in res.results if r.component == "loraham-kiss-tnc")
    assert (tnc.outcome, tnc.summary) == (Outcome.BLOCKED, REFUSED + why)
    assert "loraham-kiss-tnc" not in launched and not res.ok


def test_the_manual_command_names_the_radio_or_why_there_is_none(tmp_path):
    # The operator's terminal command for chat renders its radio's sockets (433 → r1), or says why it has none.
    svc = _svc(tmp_path, radios_fixture.RADIOS)
    chat = svc.stack("chat").component("loraham-chat")
    cmd = svc.manual_start_command(chat)
    assert "--data-socket /tmp/lora-r1.sock --conf-socket /tmp/loraconf-r1.sock" in cmd and "{rid}" not in cmd
    bare = _svc(tmp_path / "bare", NO_LIST)
    assert bare.manual_start_command(bare.stack("chat").component("loraham-chat")) \
        == f"(no run command: {REFUSED}no hardware list)"


def test_a_config_file_client_names_its_bands_radio(tmp_path):
    # W3-13: the MeshCore node reads its daemon sockets (frame + CONF) from its config file; on 868 they are r2's, and
    # the other radio's never appear.
    svc = _svc(tmp_path, radios_fixture.RADIOS)
    assert all(w.status == "written" for w in svc.write_config_files("meshcore"))
    toml = (tmp_path / "config" / "files" / "meshcore.toml").read_text()
    assert 'data_socket = "/tmp/loraf-r2.sock"' in toml and 'config_socket = "/tmp/loraconf-r2.sock"' in toml, toml
    assert "-r1.sock" not in toml and "{rid}" not in toml


def test_a_fixed_band_client_starts_on_its_bands_radio_beside_the_other_band(tmp_path, monkeypatch):
    # REL14-D28: MeshCom is not band-switchable (no stack band), its bridge a 433 client. On a list with a radio
    # per band the bridge runs as r1's instance; it is never refused as "2 radios … name one".
    svc = _svc(tmp_path, radios_fixture.RADIOS)
    assert svc.set_operator_identity(callsign="XX0XXA").ok          # MeshCom is licensed
    monkeypatch.setattr(ControllerService, "hardware_configured", lambda self: True)
    monkeypatch.setattr(ControllerService, "_ensure_daemon", lambda self, *a, **k: ([], True, ""))
    for name in ("is_built", "_source_present"):
        monkeypatch.setattr(ControllerService, name, lambda self, c: True)
    monkeypatch.setattr(ControllerService, "binary_behind", lambda self, c: "")
    monkeypatch.setattr(ControllerService, "start_blocking_requirements", lambda self, comp: [])
    launched = []
    from lhpc.core.lifecycle import StartLaunch
    monkeypatch.setattr(Lifecycle, "start", lambda self, s, c, *a, **k: launched.append((c.id, k.get("inst")))
                        or StartLaunch(True, ""))
    res = svc.start("meshcom", apply=True)
    bridge = [inst for cid, inst in launched if cid == "meshcom-bridge"]
    assert bridge and (bridge[0].slot, bridge[0].rid) == (0, "r1"), (res.summary, [r.summary for r in res.results])
    assert not any("name one" in r.summary for r in res.results)
