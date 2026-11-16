"""STATUS-DEAD-SOCKET (box H 2026-11-15 ~21:05Z): `lhpc status` said the daemon was running while daemon@r3's process
was gone and /tmp/loraconf-r3.sock refused (Errno 111): daemon@r4 still ran, and the component's one state spoke for
every instance. Each instance is now listed with its own state: running only while its process is alive AND its own
endpoints answer (its daemon CONF socket, its TCP listeners), degraded with the endpoint named otherwise, and not
running when its launch record of this boot is left behind by a process that ended without a stop. Every stack's
instances follow the one rule. Through svc.status, the path `lhpc status <stack>` renders."""
import radios_fixture
from radios_fixture import NO_PID, launch_record, live_records

from lhpc.core import instances
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem, Listener
from lhpc.core.services import ControllerService

GONE_PID = NO_PID + 7
REFUSED = "[Errno 111] Connection refused"
BAND = {r.id: r.band for r in radios_fixture.BOX_H}


def _ready(rid, radio=b"READY"):
    return b"STATUS RADIO=" + radio + b" ID=" + rid.encode() + b" BOUND=- TXMODE=MANAGED\n"


def _svc(tmp_path, monkeypatch, procs=(), ports=(), sockets=(), replies=None, errors=None, rows=radios_fixture.BOX_H):
    """`procs`: (stack, component, rid) each running under its verified launch record (how the daemon's instances
    are told); `sockets`: CONF socket paths present; `replies`/`errors`: their STATUS answers or connect errors;
    `ports`: listening TCP ports; `rows`: the hardware list."""
    radios_fixture.write(tmp_path, rows)
    paths = Paths(runtime_root=tmp_path)
    plain = ControllerService(system=FakeSystem().system, paths=paths)
    table = {}
    for n, (sid, cid, rid) in enumerate(procs):
        pid = NO_PID + 100 + n
        comp = plain.stack(sid).component(cid)
        launch_record(tmp_path, sid, cid, rid, BAND[rid], pid)
        table[pid] = [comp.process.exec_name, *comp.process.all_args]
    live_records(monkeypatch, table)
    system = FakeSystem(cmdlines_data=table, sockets=set(sockets), unix_replies=replies or {},
                        unix_errors=errors or {}, listeners=[Listener("ipv4", "127.0.0.1", p, p) for p in ports])
    return ControllerService(system=system.system, paths=paths)


def _instances(svc, sid):
    """{`<stack>@<rid>`: (state, detail)} of the `instance` lines `lhpc status <sid>` prints."""
    out = {}
    for ln in svc.status(sid).details:
        words = ln.split()
        if words[:1] == ["instance"]:
            state, _, detail = ln.partition(": ")[2].partition(" — ")
            out[words[1].rstrip(":")] = (state, detail)
    return out


def _sock(rid):
    return f"/tmp/loraconf-{rid}.sock"


def test_a_dead_instance_beside_a_live_one_is_not_running(tmp_path, monkeypatch):
    # box H 11-15: r3's record left, its process gone, its socket file refusing; r4 runs and answers.
    # red-before (59d00cab): no instance line at all, only "loraham-daemon running"
    launch_record(tmp_path, "daemon", "loraham-daemon", "r3", "433", GONE_PID)
    svc = _svc(tmp_path, monkeypatch, procs=[("daemon", "loraham-daemon", "r4")], sockets=[_sock("r3"), _sock("r4")],
               replies={_sock("r4"): _ready("r4")}, errors={_sock("r3"): REFUSED})
    got = _instances(svc, "daemon")
    assert set(got) == {"daemon@r3", "daemon@r4"}
    assert got["daemon@r4"] == ("running", "")
    state, detail = got["daemon@r3"]
    assert state == "not running" and f"pid {GONE_PID}" in detail and "lhpc stack start daemon@r3" in detail


def test_a_restarted_instance_is_running_beside_its_old_record(tmp_path, monkeypatch):
    # box H 21:27Z: daemon@r3 started again; a start leaves the ended launch's record (a stop drops it), and the
    # instance that runs is not also "not running"
    launch_record(tmp_path, "daemon", "loraham-daemon", "r3", "433", GONE_PID)
    svc = _svc(tmp_path, monkeypatch, procs=[("daemon", "loraham-daemon", "r3")], sockets=[_sock("r3")],
               replies={_sock("r3"): _ready("r3")})
    assert [ln.split()[1] for ln in svc.status("daemon").details if ln.split()[:1] == ["instance"]] == ["daemon@r3:"]
    assert _instances(svc, "daemon") == {"daemon@r3": ("running", "")}


def test_a_live_process_whose_socket_refuses_is_degraded(tmp_path, monkeypatch):
    # red-before (59d00cab): no instance line; the component read "running" (its socket is no state gate)
    svc = _svc(tmp_path, monkeypatch, procs=[("daemon", "loraham-daemon", "r3")], sockets=[_sock("r3")],
               errors={_sock("r3"): REFUSED})
    state, detail = _instances(svc, "daemon")["daemon@r3"]
    assert state == "degraded" and f"socket {_sock('r3')} not answering" in detail and "111" in detail


def test_a_live_process_without_its_socket_is_degraded(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch, procs=[("daemon", "loraham-daemon", "r3")])
    assert _instances(svc, "daemon") == {"daemon@r3": ("degraded", f"socket {_sock('r3')} not answering: absent")}


def test_a_socket_that_answers_without_a_ready_radio_is_degraded(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch, procs=[("daemon", "loraham-daemon", "r3")], sockets=[_sock("r3")],
               replies={_sock("r3"): _ready("r3", b"UNPLUGGED")})
    state, detail = _instances(svc, "daemon")["daemon@r3"]
    assert state == "degraded" and f"socket {_sock('r3')} answers" in detail and "RADIO=UNPLUGGED" in detail


def test_a_live_process_whose_socket_answers_ready_is_running(tmp_path, monkeypatch):
    # red-before (59d00cab): no instance line
    svc = _svc(tmp_path, monkeypatch, procs=[("daemon", "loraham-daemon", "r3")], sockets=[_sock("r3")],
               replies={_sock("r3"): _ready("r3")})
    assert _instances(svc, "daemon") == {"daemon@r3": ("running", "")}


def test_an_instance_on_a_disabled_radio_is_judged_by_its_own_socket(tmp_path, monkeypatch):
    # daemon@r3 still runs after r3 was disabled: status probes no disabled radio's socket, so its own is read here
    import dataclasses
    rows = tuple(dataclasses.replace(r, enabled=False) if r.id == "r3" else r for r in radios_fixture.BOX_H)
    up = _svc(tmp_path, monkeypatch, procs=[("daemon", "loraham-daemon", "r3")], rows=rows, sockets=[_sock("r3")],
              replies={_sock("r3"): _ready("r3")})
    assert _instances(up, "daemon") == {"daemon@r3": ("running", "")}
    gone = _svc(tmp_path, monkeypatch, procs=[("daemon", "loraham-daemon", "r3")], rows=rows)
    assert _instances(gone, "daemon") == {"daemon@r3": ("degraded", f"socket {_sock('r3')} not answering: absent")}
    deaf = _svc(tmp_path, monkeypatch, procs=[("daemon", "loraham-daemon", "r3")], rows=rows, sockets=[_sock("r3")],
                replies={_sock("r3"): _ready("r3", b"UNPLUGGED")})
    assert _instances(deaf, "daemon") == {"daemon@r3": ("degraded", f"socket {_sock('r3')} answers, RADIO=UNPLUGGED")}


def test_every_stack_follows_the_rule(tmp_path, monkeypatch):
    # meshcom@r3's qemu lost its web listener 18349 while meshcom@r1's answers; meshcom@r4's bridge ended without a
    # stop. red-before (59d00cab): one "meshcom-qemu degraded" row, no instance named, the ended bridge not shown
    launch_record(tmp_path, "meshcom", "meshcom-bridge", "r4", "868", GONE_PID)
    svc = _svc(tmp_path, monkeypatch, procs=[("meshcom", "meshcom-qemu", "r1"), ("meshcom", "meshcom-qemu", "r3")],
               ports=(18083, 12323, 12589))
    got = _instances(svc, "meshcom")
    assert got["meshcom@r1"] == ("running", "")
    assert got["meshcom@r3"] == ("degraded", "listener absent: 127.0.0.1:18349")
    assert got["meshcom@r4"][0] == "not running"


def test_a_record_of_another_boot_is_not_an_ended_instance(tmp_path, monkeypatch):
    # control (no line on either side): boot restore owns a previous boot's records; status names no instance by them
    launch_record(tmp_path, "daemon", "loraham-daemon", "r3", "433", GONE_PID,
                  boot="00000000-0000-0000-0000-000000000000")
    assert _instances(_svc(tmp_path, monkeypatch), "daemon") == {}


def test_a_stop_clears_an_ended_instance(tmp_path, monkeypatch):
    # the remedy the line names: `lhpc stack stop daemon@r3` drops the left record (Lifecycle.stop's stale-record
    # rule), and the line goes
    launch_record(tmp_path, "daemon", "loraham-daemon", "r3", "433", GONE_PID)
    svc = _svc(tmp_path, monkeypatch)
    assert _instances(svc, "daemon")["daemon@r3"][0] == "not running"
    sid, inst, _band, why = instances.parse_target(svc, "daemon@r3", "stop")
    res = svc.run_action("stop", sid, apply=True, inst=inst)
    assert res.ok, (why, res.summary, res.details)
    assert _instances(svc, "daemon") == {}


def test_a_record_whose_end_is_not_proven_is_not_an_ended_instance(tmp_path, monkeypatch):
    # control (no line on either side): a transient /proc read error is no proof that the process ended (Lifecycle._original_ceased): the instance is
    # not called "not running" on it. Lifecycle._proc_ceased is stubbed as the collaborator that reads /proc.
    from lhpc.core.lifecycle import Lifecycle
    launch_record(tmp_path, "daemon", "loraham-daemon", "r3", "433", GONE_PID)
    svc = _svc(tmp_path, monkeypatch)
    monkeypatch.setattr(Lifecycle, "_proc_ceased", staticmethod(lambda pid: False))
    assert _instances(svc, "daemon") == {}


def test_a_record_with_a_malformed_rid_is_not_an_ended_instance(tmp_path, monkeypatch):
    # G1-STATUS-DEAD-SOCKET NOTE: a record of this boot whose rid is no instance id ("rX") is no instance, and status
    # still renders. red-before (9c16c524): ValueError from the rid sort, `lhpc status` and the console crash
    launch_record(tmp_path, "daemon", "loraham-daemon", "rX", "433", GONE_PID)
    launch_record(tmp_path, "daemon", "loraham-daemon", "r3", "433", GONE_PID + 1)
    svc = _svc(tmp_path, monkeypatch)
    assert svc.status("daemon").ok
    assert list(_instances(svc, "daemon")) == ["daemon@r3"]
