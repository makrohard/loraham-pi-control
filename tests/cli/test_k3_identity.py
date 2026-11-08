"""K3 (CLEANUP-DESIGN-v13 13.2, W323/W326): every operator entry names its instance `<stack>@<rid>`.

A bare stack is refused with the radios it can run on, one radio included; a bare stop or kill names the instances
that run; `<stack>@<rid>` on a radio without a listed copy is slot 0 on that radio (until the slots go), and its stop
is the whole stack's, as the bare stop was. The CLI runs over a temporary runtime root with its own hardware list."""
import pytest
import radios_fixture

from lhpc.adapters.cli import main as cli
from lhpc.core import commands, instances, radios
from lhpc.core.services import ActionResult, ControllerService

R1 = radios.Radio("r1", "low", "loraham", "433")
R3 = radios.Radio("r3", "high", "loraham", "868")


@pytest.fixture
def box(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("LHPC_RUNTIME_ROOT", str(tmp_path))
    calls = []
    monkeypatch.setattr(ControllerService, "run_action", lambda self, op, target, **kw: (
        calls.append((op, target, getattr(kw.get("inst"), "rid", None))), ActionResult(True, "ok", data={"changes": 1}))[1])

    def run(rows, *argv):
        radios_fixture.write(tmp_path, rows)
        rc = cli.main(list(argv))
        return rc, capsys.readouterr(), calls
    return run


@pytest.mark.parametrize("rows,why", [
    ((R1, R3), "kiss can run on r1 or r3: use kiss@r1 or kiss@r3"),
    ((R1,), "kiss can run on r1: use kiss@r1"),                       # W326: one possible radio too
    ((R1, radios.Radio("r3", "high", "loraham", "868", enabled=False)), "kiss can run on r1: use kiss@r1"),
])
@pytest.mark.parametrize("verb", ["start", "restart"])
def test_dod_1_a_bare_stack_is_refused_with_the_list_and_nothing_runs(box, rows, why, verb):
    # DoD test 1 (13.12.3). RED before K3: the bare stack started slot 0 (rc 0, run_action called).
    rc, out, calls = box(rows, "stack", verb, "kiss", "--yes")
    assert (rc, calls) == (1, []) and out.err == f"ERR   {why}\n"


@pytest.mark.parametrize("argv", [("stack", "stop", "kiss", "--yes"), ("kill", "kiss", "--yes")])
def test_a_bare_stop_or_kill_names_the_running_instances(box, monkeypatch, argv):
    monkeypatch.setattr(instances, "running_keys", lambda svc, sid: ["kiss@r1", "kiss@r3"], raising=False)
    rc, out, calls = box((R1, R3), *argv)
    assert (rc, calls) == (1, []) and "kiss runs as kiss@r1 and kiss@r3: name one (kiss@r1)" in out.err


def test_a_named_instance_on_a_radio_without_a_listed_copy_is_slot_0_there(box):
    rc, _out, calls = box((R1, R3), "stack", "start", "kiss@r3", "--yes")
    assert rc == 0 and calls[-1] == ("start", "kiss", "r3")


def test_a_named_radio_of_another_band_is_refused(box):
    rc, out, calls = box((R1, R3), "stack", "start", "meshcom@r3", "--yes")     # meshcom runs on 433 only
    assert (rc, calls) == (1, []) and out.err == "ERR   meshcom@r3: meshcom runs on 433 MHz, r3 is on 868 MHz\n"


@pytest.mark.parametrize("inst,whole", [(commands.Instance(0, "r1"), True), (commands.Instance(1, "r3"), False),
                                        (None, True)])
def test_slot_0_named_on_its_radio_is_the_whole_stacks_operator_stop(tmp_path, monkeypatch, inst, whole):
    # K3: `kiss@r1` (slot 0) is what a bare stop was: its cascade passes the operator's word down, so a dependent it
    # stops keeps its stop intent (boot restore does not resurrect it). A listed copy's stop does not. RED before K3:
    # an explicit slot-0 instance was not whole (False).
    radios_fixture.write(tmp_path, (R1, R3))
    from lhpc.core.paths import Paths
    from lhpc.core.probes.backends import FakeSystem
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    seen = []
    monkeypatch.setattr(ControllerService, "_stop_impl", lambda self, target, **kw: (
        seen.append(kw["_operator"]), ActionResult(False, "captured"))[1])
    svc.stop("kiss", apply=True, inst=inst)
    assert seen == [whole]


def test_the_running_instances_are_the_verified_records_and_the_copies_processes(tmp_path, monkeypatch):
    # running_keys: slot 0 by its VERIFIED ownership record's radio (an unverified or other stack's record is not),
    # a listed copy by copy_processes; by rid order, each once.
    radios_fixture.write(tmp_path, (R1, R3))
    from lhpc.core.lifecycle import Lifecycle
    from lhpc.core.paths import Paths
    from lhpc.core.probes.backends import FakeSystem
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    recs = [{"stack": "kiss", "component": "loraham-kiss-tnc", "rid": "r3", "pid": 11},
            {"stack": "kiss", "component": "loraham-kiss-tnc", "rid": "r1", "pid": 12},      # not verified
            {"stack": "chat", "component": "loraham-chat", "rid": "r1", "pid": 13},
            {"stack": "kiss", "component": "loraham-kiss-tnc", "rid": "", "pid": 14}]         # no radio
    monkeypatch.setattr(Lifecycle, "owned_inventory", lambda self: (recs, [], "ok"))
    monkeypatch.setattr(Lifecycle, "verify_owned", lambda self, rec: (rec["pid"] != 12, ""))
    monkeypatch.setattr(instances, "copy_processes", lambda svc, table: [("kiss", "r12", "loraham-kiss-tnc", 21),
                                                                        ("chat", "r2", "loraham-chat", 22),
                                                                        ("kiss", "r3", "loraham-kiss-tnc", 23)])
    assert instances.running_keys(svc, "kiss") == ["kiss@r3", "kiss@r12"]


def test_a_suggested_command_names_the_bands_first_radio(tmp_path):
    # instances.named with a band: its first enabled radio by rid order, never a radio of another band (K3).
    radios_fixture.write(tmp_path, (R1, radios.Radio("r12", "b", "meshstick-1262", "868", usb_port="1-1.3"), R3,
                                    radios.Radio("r2", "c", "meshstick-1262", "868", enabled=False, usb_serial="MS123456")))
    from lhpc.core.paths import Paths
    from lhpc.core.probes.backends import FakeSystem
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    assert instances.named(svc, "daemon", band="868") == "daemon@r3"
    assert instances.named(svc, "daemon", band="433") == "daemon@r1"
    assert instances.named(svc, "kiss@r12") == "kiss@r12"                    # already named


def _two_433(tmp_path):
    from lhpc.core.paths import Paths
    from lhpc.core.probes.backends import FakeSystem
    radios_fixture.write(tmp_path, (radios.Radio("r1", "a", "uputronics", "433", ce="ce0"),
                                    radios.Radio("r2", "b", "uputronics", "433", ce="ce1")))
    return ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))


def test_c60_b1_an_ocp_change_on_r2_names_r2s_restart_and_r1s_restart_keeps_it(tmp_path, monkeypatch):
    # G1-C60 B1: r1 and r2 both on 433, both daemons running; r2's OCP changes. The restart-required remedy names
    # daemon@r2 (not the first running, r1), and r1's daemon restart does not clear r2's marker.
    from lhpc.core import radio_start, restart_required
    svc = _two_433(tmp_path)
    monkeypatch.setattr(radio_start, "daemon_running", lambda svc, radio, procs=None: True)
    monkeypatch.setattr(instances, "running_keys", lambda svc, sid: ["daemon@r1", "daemon@r2"], raising=False)
    assert svc.set_ocp("r2", "120").ok
    text = "\n".join(svc.status("daemon").details)                       # the public status names the remedy
    assert "(lhpc stack restart daemon@r2)" in text and "daemon@r1" not in text, text
    restart_required.clear_marker(svc._paths, "daemon", "r1")              # r1's restart
    assert svc.restart_required("daemon") is not None
    restart_required.clear_marker(svc._paths, "daemon", "r2")              # r2's own restart clears it
    assert svc.restart_required("daemon") is None


def test_c60_b2_a_daemon_holding_r2_is_stopped_as_daemon_at_r2(tmp_path):
    svc = _two_433(tmp_path)
    blockers = [{"resource": "loraham.radio.r2", "holder": "d", "holder_stack": "daemon", "band": "433"}]
    assert svc.owner_stops(blockers) == [("daemon@r2", "lhpc stack stop daemon@r2")]


def test_c60_note_a_stack_wide_remedy_names_every_running_instance(tmp_path, monkeypatch):
    svc = _two_433(tmp_path)
    monkeypatch.setattr(instances, "running_keys", lambda svc, sid: ["kiss@r1", "kiss@r3"] if sid == "kiss" else [],
                        raising=False)
    blockers = [{"resource": "loraham.radio.r1", "holder": "k", "holder_stack": "kiss", "band": "433"}]
    assert svc.owner_stops(blockers) == [("kiss@r1", "lhpc stack stop kiss@r1"), ("kiss@r3", "lhpc stack stop kiss@r3")]
