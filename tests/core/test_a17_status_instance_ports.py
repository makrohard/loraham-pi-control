"""A17 + A24 detail (0.14.0 manual test): `lhpc status` probes each component at the ports of the instance it runs as,
never the template's (r1's); an unclaimed process (radio unknown) adds the template's, so neither hides the other
(G1-B1-PLAN rounds). Through svc.status, the path `lhpc status <stack>` renders."""
import radios_fixture

from lhpc.core import commands, instances, radios
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem, Listener
from lhpc.core.services import ControllerService

ROWS = radios_fixture.RADIOS + (radios.Radio("r3", "rfm98 433", "ch341-rfm98pw-433", "433", usb_serial="CHE1W433"),
                                radios.Radio("r4", "pine64 868", "pinedio-usb-1262", "868", usb_port="1-1"))
LEGACY = {"meshcom-qemu": ["qemu-system-xtensa", "-nographic"],                # 0.13.x argv: no instance token
          "meshcore-node": ["python", "-m", "meshcore_host", "/opt/old/meshcore.toml"]}


def _svc(tmp_path, procs=(), ports=(), saved=None, legacy=()):
    """`procs`: (stack, component, rid) each running with its instance's own argv token; `legacy`: components running
    with an older argv no instance claims; `ports`: listening TCP ports; `saved`: {(stack, rid): values} saved first."""
    radios_fixture.write(tmp_path, ROWS)
    paths, band = Paths(runtime_root=tmp_path), {r.id: r.band for r in ROWS}
    plain = ControllerService(system=FakeSystem().system, paths=paths)
    for (sid, rid), values in (saved or {}).items():
        assert plain.save_config_bundle(sid, values=values, inst=instances.copy_of(plain, sid, rid)[0]).ok
    table = {}
    for n, (sid, cid, rid) in enumerate(procs):
        comp = plain.stack(sid).component(cid)
        opt, val = instances._token(plain, comp, commands.Instance(rid, band=band[rid]), band[rid])
        table[1000 + n] = [comp.process.exec_name, *comp.process.all_args, opt, val]
    for n, cid in enumerate(legacy):
        table[2000 + n] = LEGACY[cid]
    system = FakeSystem(cmdlines_data=table, listeners=[Listener("ipv4", "127.0.0.1", p, p) for p in ports])
    return ControllerService(system=system.system, paths=paths)


def _rows(svc, sid, cid):
    """The component's status line and its endpoint addresses, as `lhpc status <sid>` prints them."""
    lines = svc.status(sid).details
    at = next(i for i, ln in enumerate(lines) if ln.split()[:1] == [cid])
    eps = []
    for ln in lines[at + 1:]:
        if not ln.startswith("        "):
            break
        if ln.split()[0] == "endpoint":
            eps.append(ln.split()[1])
    return lines[at].split()[1], eps


def test_a_node_running_as_r3_is_judged_at_r3s_own_ports(tmp_path):
    # red-before (b3fc6e60): "meshcom-qemu degraded", endpoints 127.0.0.1:18083 + 127.0.0.1:12323 (r1's) absent
    svc = _svc(tmp_path, procs=[("meshcom", "meshcom-qemu", "r3"), ("meshcom", "meshcom-bridge", "r1")],
               ports=(18349, 12589, 7000))
    assert _rows(svc, "meshcom", "meshcom-qemu") == ("running", ["127.0.0.1:18349", "127.0.0.1:12589"])
    assert _rows(svc, "meshcom", "meshcom-bridge")[1] == ["127.0.0.1:7000"]


def test_a_stopped_client_of_r4_shows_r4s_port(tmp_path):
    # red-before (b3fc6e60): meshcore-webui (stopped) listed at 127.0.0.1:8788, r1's template port (A24 detail)
    state, eps = _rows(_svc(tmp_path, procs=[("meshcore", "meshcore-node", "r4")], ports=(5399,)),
                       "meshcore", "meshcore-webui")
    assert state not in ("running", "degraded") and eps == ["127.0.0.1:9187"]


def test_a_stopped_client_beside_two_running_instances_lists_both(tmp_path):
    svc = _svc(tmp_path, procs=[("meshcore", "meshcore-node", "r2"), ("meshcore", "meshcore-node", "r4")],
               ports=(5133, 5399))
    state, eps = _rows(svc, "meshcore", "meshcore-webui")
    assert state not in ("running", "degraded") and eps == ["127.0.0.1:8921", "127.0.0.1:9187"]


def test_an_instance_is_judged_at_its_saved_port(tmp_path):
    # meshcom@r3's bridge saved at 7500 listens on 7766 (+ 2 x 133, G2 COMB 4)
    svc = _svc(tmp_path, procs=[("meshcom", "meshcom-bridge", "r3")], ports=(7766,),
               saved={("meshcom", "r3"): {"port": "7500"}})
    assert _rows(svc, "meshcom", "meshcom-bridge")[1] == ["127.0.0.1:7766"]


def test_two_instances_of_one_component_need_all_their_endpoints(tmp_path):
    procs = [("meshcom", "meshcom-qemu", "r1"), ("meshcom", "meshcom-qemu", "r3")]
    assert _rows(_svc(tmp_path, procs=procs, ports=(18083, 12323, 18349, 12589)), "meshcom", "meshcom-qemu") == (
        "running", ["127.0.0.1:18083", "127.0.0.1:12323", "127.0.0.1:18349", "127.0.0.1:12589"])
    assert _rows(_svc(tmp_path, procs=procs, ports=(18083, 12323, 18349)), "meshcom", "meshcom-qemu")[0] == "degraded"


def test_an_unclaimed_process_alone_keeps_the_template_ports(tmp_path):
    assert _rows(_svc(tmp_path, legacy=["meshcom-qemu"], ports=(18083, 12323)), "meshcom", "meshcom-qemu") == (
        "running", ["127.0.0.1:18083", "127.0.0.1:12323"])


def test_an_unclaimed_process_never_hides_an_instances_lost_listener(tmp_path):
    # G1-B1-PLAN-RE BLOCKING 1: qemu@r3 lost 18349 while a legacy qemu answers on the template's 18083/12323
    svc = _svc(tmp_path, procs=[("meshcom", "meshcom-qemu", "r3")], legacy=["meshcom-qemu"],
               ports=(18083, 12323, 12589))
    state, eps = _rows(svc, "meshcom", "meshcom-qemu")
    assert state == "degraded" and set(eps) == {"127.0.0.1:18349", "127.0.0.1:12589", "127.0.0.1:18083", "127.0.0.1:12323"}


def test_the_node_follows_the_same_rule(tmp_path):
    # G1-B1-PLAN-RE2 BLOCKING 3: node@r2's Companion 5133 down beside a legacy node on the template's 5000; red on
    # b3fc6e60 (any unclaimed process sent the node to the template: running)
    svc = _svc(tmp_path, procs=[("meshcore", "meshcore-node", "r2")], legacy=["meshcore-node"], ports=(5000,))
    state, eps = _rows(svc, "meshcore", "meshcore-node")
    assert state == "degraded" and "127.0.0.1:5133" in eps and "127.0.0.1:5000" in eps


def test_each_address_is_listed_once(tmp_path):
    # r1's own ports and the template's coincide: listed once (G1-B1-PLAN-RE2 N1)
    svc = _svc(tmp_path, procs=[("meshcom", "meshcom-qemu", "r1")], legacy=["meshcom-qemu"], ports=(18083, 12323))
    assert _rows(svc, "meshcom", "meshcom-qemu") == ("running", ["127.0.0.1:18083", "127.0.0.1:12323"])


def test_status_endpoints_use_the_probers_one_process_table(tmp_path, monkeypatch):
    # G1-B1-PLAN-RE N2: status_endpoints and its process collection work on the prober's one table, never on a read of
    # their own (the snapshot's other helpers are not part of this change)
    svc = _svc(tmp_path, procs=[("meshcom", "meshcom-qemu", "r3")], ports=(18349, 12589))
    given, inside, real = [], [], (instances.status_endpoints, instances.instance_processes, instances.unclaimed_pids)

    def status_endpoints(svc, table, owned):
        given.append(table)
        try:
            return real[0](svc, table, owned)
        finally:
            given.append(None)                            # the end of the call

    def track(fn):
        def wrapped(svc, *a, **k):
            table = a[1] if fn is real[2] and len(a) > 1 else (a[0] if fn is real[1] else k.get("table"))
            if given and given[-1] is not None:
                inside.append(table)
            return fn(svc, *a, **k)
        return wrapped
    monkeypatch.setattr(instances, "status_endpoints", status_endpoints)
    monkeypatch.setattr(instances, "instance_processes", track(real[1]))
    monkeypatch.setattr(instances, "unclaimed_pids", track(real[2]))
    svc.build_snapshot(fresh=True, sources=False)
    assert given[0] is not None and len(inside) >= 2 and all(t is given[0] for t in inside)


def test_without_a_running_instance_the_template_ports_stay(tmp_path):
    assert _rows(_svc(tmp_path), "meshcom", "meshcom-qemu")[1] == ["127.0.0.1:18083", "127.0.0.1:12323"]


def test_a_recorded_process_without_a_radio_counts_as_unclaimed(tmp_path, monkeypatch):
    # an older LHPC's qemu, known only by its verified record (its argv no longer matches the spec, status.py:84-88),
    # beside qemu@r3 that lost 18349: the union still judges r3 -> degraded
    from lhpc.core.lifecycle import Lifecycle
    svc = _svc(tmp_path, procs=[("meshcom", "meshcom-qemu", "r3")], ports=(18083, 12323, 12589))
    real = Lifecycle.owned_running
    monkeypatch.setattr(Lifecycle, "owned_running", lambda self: {**real(self), "meshcom-qemu": [2999]})
    state, eps = _rows(svc, "meshcom", "meshcom-qemu")
    assert state == "degraded" and "127.0.0.1:18349" in eps and "127.0.0.1:18083" in eps
