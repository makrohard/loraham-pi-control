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


@pytest.mark.parametrize("inst,whole", [(commands.Instance("r1"), True), (commands.Instance("r3"), True),
                                        (None, True)])
def test_every_instances_stop_is_the_operators_stop(tmp_path, monkeypatch, inst, whole):
    # K3, K4b (no slots): an instance's stop passes the operator's word down its cascade, so a dependent it stops
    # keeps its stop intent (boot restore does not resurrect it); kiss@r3 is an instance like kiss@r1, no lesser copy.
    radios_fixture.write(tmp_path, (R1, R3))
    from lhpc.core.paths import Paths
    from lhpc.core.probes.backends import FakeSystem
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    seen = []
    monkeypatch.setattr(ControllerService, "_stop_impl", lambda self, target, **kw: (
        seen.append(kw["_operator"]), ActionResult(False, "captured"))[1])
    svc.stop("kiss", apply=True, inst=inst)
    assert seen == [whole]


def test_the_running_instances_are_the_verified_records_and_the_instances_processes(tmp_path, monkeypatch):
    # running_keys: an instance by its VERIFIED ownership record's radio (an unverified, radio-less or other stack's
    # record is not), or by its own process (instance_processes); by rid order, each once.
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
    monkeypatch.setattr(instances, "instance_processes", lambda svc, table: [("kiss", "r12", "loraham-kiss-tnc", 21),
                                                                        ("chat", "r2", "loraham-chat", 22),
                                                                        ("kiss", "r3", "loraham-kiss-tnc", 23)])
    assert instances.running_keys(svc, "kiss") == ["kiss@r3", "kiss@r12"]


def test_a_suggested_command_names_the_one_candidate_or_leaves_the_template(tmp_path):
    # instances.named (handler DEC G1-C60-RE): never a guess. The band's ONE enabled radio, else `<rid>`.
    radios_fixture.write(tmp_path, (R1, radios.Radio("r12", "b", "meshstick-1262", "868", usb_port="1-1.3"), R3,
                                    radios.Radio("r2", "c", "meshstick-1262", "868", enabled=False, usb_serial="MS123456")))
    from lhpc.core.paths import Paths
    from lhpc.core.probes.backends import FakeSystem
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    assert instances.named(svc, "daemon", band="868") == "daemon@<rid>"           # r3 and r12: no guess
    assert instances.named(svc, "daemon", band="433") == "daemon@r1"
    assert instances.named(svc, "meshcom") == "meshcom@r1"                       # 433 only: its one radio
    assert instances.named(svc, "kiss") == "kiss@<rid>"                          # three radios it can run on
    assert instances.named(svc, "kiss@r12") == "kiss@r12"                        # already named
    assert instances.stop_keys(svc, "kiss") == []                                # nothing runs: no command


def _two_433(tmp_path):
    from lhpc.core.paths import Paths
    from lhpc.core.probes.backends import FakeSystem
    radios_fixture.write(tmp_path, (radios.Radio("r1", "a", "uputronics", "433", ce="ce0"),
                                    radios.Radio("r2", "b", "uputronics", "433", ce="ce1")))
    return ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))


def test_c60_b1_each_radios_change_is_its_own_marker_its_own_remedy_and_its_own_clear(tmp_path, monkeypatch):
    # G1-C60(-RE) B1/A: r1 and r2 on 433, both daemons running; both OCPs change. Each radio has its own marker
    # (daemon@r1, daemon@r2), the status names each restart, r1's restart clears r1's only, the whole daemon's both.
    from lhpc.core import commands, radio_start, restart_required
    svc = _two_433(tmp_path)
    monkeypatch.setattr(radio_start, "daemon_running", lambda svc, radio, procs=None: True)
    assert svc.set_ocp("r2", "120").ok and svc.set_ocp("r1", "130").ok
    assert svc.restart_required_stacks() == ["daemon@r1", "daemon@r2"]
    text = "\n".join(svc.status("daemon").details)                       # the public status names each remedy
    assert "(lhpc stack restart daemon@r1)" in text and "(lhpc stack restart daemon@r2)" in text, text
    mp = restart_required.marker_path(svc._paths, "daemon")                      # a whole-daemon change too
    mp.parent.mkdir(parents=True, exist_ok=True)
    mp.write_text(svc.restart_marker_payload("daemon", ["log_level"], "433"))
    for k in instances.marker_keys(svc, "daemon", commands.Instance("r1")):   # r1's restart: r1's only
        restart_required.clear_marker(svc._paths, k)
    assert sorted(svc.restart_required_stacks()) == ["daemon", "daemon@r2"]
    for k in instances.marker_keys(svc, "daemon"):                              # the whole daemon's
        restart_required.clear_marker(svc._paths, k)
    assert svc.restart_required_stacks() == []


def test_c60_b2_a_daemon_holding_r2_is_stopped_as_daemon_at_r2(tmp_path):
    svc = _two_433(tmp_path)
    blockers = [{"resource": "loraham.radio.r2", "holder": "d", "holder_stack": "daemon", "band": "433"}]
    assert svc.owner_stops(blockers) == [("daemon@r2", "lhpc stack stop daemon@r2")]


def test_c60_a_holder_is_stopped_exactly_and_a_stack_wide_remedy_names_each_instance(tmp_path, monkeypatch):
    svc = _two_433(tmp_path)
    monkeypatch.setattr(instances, "running_keys", lambda svc, sid: ["kiss@r1", "kiss@r3"] if sid == "kiss" else [],
                        raising=False)
    blockers = [{"resource": "loraham.radio.r1", "holder": "k", "holder_stack": "kiss", "band": "433"}]
    assert svc.owner_stops(blockers) == [("kiss@r1", "lhpc stack stop kiss@r1")]       # exactly the holder (G1-C60-RE B)
    assert instances.stop_keys(svc, "kiss") == ["kiss@r1", "kiss@r3"]                 # a stack-wide remedy: each one
    band = [{"resource": "loraham.radio.433", "holder": "k", "holder_stack": "kiss", "band": "433"}]
    assert svc.owner_stops(band) == [("kiss@r1", "lhpc stack stop kiss@r1")]          # the running ones on 433 (r1, r2)


def test_transitional_a_radios_daemon_set_and_its_live_view_and_running_list(tmp_path, monkeypatch):
    # The transitional rid form for the web lane's W1 (handler DEC; by K6 the rid is required): with r1 and r2 both on
    # 433, a SET names r2's own socket, the Settings panel's live block reads r2, and the FSK warning lists the stacks
    # running on r2 only.
    from lhpc.core import daemon_control as dc
    from lhpc.core.paths import Paths
    from lhpc.core.probes.backends import FakeSystem
    radios_fixture.write(tmp_path, (radios.Radio("r1", "a", "uputronics", "433", ce="ce0"),
                                    radios.Radio("r2", "b", "uputronics", "433", ce="ce1")))
    ready = b"STATUS RADIO=READY TXMODE=MANAGED CADWAIT=1500\n"
    fs = FakeSystem(unix_replies={dc.conf_socket("r1"): ready, dc.conf_socket("r2"): ready})
    svc = ControllerService(system=fs.system, paths=Paths(runtime_root=tmp_path))
    assert "SET TXMODE=DIRECT" in svc.daemon_set("r2", "TXMODE", "DIRECT", apply=True).summary
    assert [path for path, _p in fs.sent if b"SET TXMODE" in _p] == [dc.conf_socket("r2")]
    assert not svc.daemon_set("r9", "TXMODE", "DIRECT", apply=True).ok
    view = svc.config_view("daemon", rid="r2")
    assert [(r["rid"], r["band"], r["reachable"]) for r in view["radios"]] == [("r2", "433", True)]
    assert [r["rid"] for r in svc.config_view("daemon")["radios"]] == ["r1"]         # the band's first radio
    monkeypatch.setattr(instances, "running_keys", lambda svc, sid: ["kiss@r2"] if sid == "kiss" else ["chat@r1"])
    assert svc.lora_clients_on("r2") == ["kiss"]
