"""A web page's default proxy port: 8444 + its kind's index + 133 x (n - 1) on radio rN, the next free port when that
one is taken, a named refusal when none is (the plan of record: UNATTENDED/B1b-page-ports/plan/PLAN-v3.md 3a48dcfb).
The two-radio default list and an added radio: tests/web/test_stackweb.py. Through the public paths: the bulk
`stack_webs_configure_apply`, the single `stack_web_view` + `stack_web_configure`, and `plan_stack_exposure`."""
import pytest
import radios_fixture

from lhpc.core import config as cfgmod
from lhpc.core import listeners, radios, webserver
from lhpc.core.config import StackWebConfig
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService

# The kind indices, pinned here on purpose (a test that read them from the code would follow a reordering).
K = {"graywolf": 0, "meshcom": 1, "meshcore": 2, "meshtastic": 3, "reticulum": 4, "meshcore-meshcore-node": 5}
BOX_H = radios_fixture.RADIOS + (radios.Radio("r3", "rfm98 433", "ch341-rfm98pw-433", "433", usb_serial="CHE1W433"),
                                 radios.Radio("r4", "pine64 868", "pinedio-usb-1262", "868", usb_port="1-1"))
POLICY = dict(mode="lan", scheme="https", access_mode="local-open-remote-auth", cidrs=["192.168.178.0/24"],
              confirm=True)


def _derived(page_id):
    kind, _, rid = page_id.partition("@")
    return 8444 + K[kind] + 133 * (int(rid[1:]) - 1 if rid else 0)


def _svc(tmp_path, rows=radios_fixture.RADIOS):
    if rows is not None:
        radios_fixture.write(tmp_path, rows)
    return ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))


def _bulk(svc):
    """The bulk Apply's SAVE (its nginx reload has no nginx in the fake system): saved, or the refusal."""
    r = svc.stack_webs_configure_apply(**POLICY)
    return r.summary.split(". ", 1)[0] == f"common policy saved for {len(svc.stack_web_eligible())} stack web UI(s)", r


def _saved(svc):
    svc._invalidate_config()
    return {sid: c.port for sid, c in svc.config().stackweb.items()}


def test_box_h_bulk_apply_gives_every_page_its_radio_derived_port(tmp_path):
    # Red on 085034cf: "meshcore-meshcore-node@r4: port 8458 is already meshcore@r27 (template)/meshcore-node's listener".
    svc = _svc(tmp_path, BOX_H)
    ok, r = _bulk(svc)
    assert ok, (r.summary, r.details)
    got = _saved(svc)
    assert got == {p: _derived(p) for p in got} and len(got) == 15
    assert (got["graywolf@r1"], got["meshcom@r3"], got["meshcore-meshcore-node@r2"]) == (8444, 8711, 8582)
    assert got["meshcore-meshcore-node@r4"] == 8848                       # was 8458, meshcore@r27's node


@pytest.mark.parametrize("rows", [radios_fixture.RADIOS, radios_fixture.RADIOS[:1], radios_fixture.RADIOS[1:], None,
                                  BOX_H], ids=["dual", "433-only", "868-only", "no-list", "four-radios"])
def test_a_page_s_suggestion_is_its_kind_and_radio_on_every_box(tmp_path, rows):
    svc = _svc(tmp_path, rows)
    pages = [p.page_id for p in svc.web_pages()]
    assert pages and {p: svc.stack_web_view(p)["suggested_port"] for p in pages} == {p: _derived(p) for p in pages}


def test_a_console_on_a_derived_port_pushes_that_page_forward_only(tmp_path):
    svc = _svc(tmp_path)
    cfgmod.save_webserver_config(svc._paths, port=8444)                  # the console on graywolf@r1's port
    svc._invalidate_config()
    ok, r = _bulk(svc)
    assert ok, (r.summary, r.details)
    got = _saved(svc)
    occupied = listeners.stack_listeners(svc)
    assert got["graywolf@r1"] != 8444 and len(set(got.values())) == len(got)
    assert not set(got.values()) & ({8444} | set(occupied))
    svc2 = _svc(tmp_path / "b")
    cfgmod.save_webserver_config(svc2._paths, port=9443 + 1000)          # a console elsewhere moves nobody
    svc2._invalidate_config()
    assert _bulk(svc2)[0]
    assert _saved(svc2) == {p: _derived(p) for p in _saved(svc2)}


def test_two_hand_saved_pages_on_derived_ports_push_both_unsaved_ones_to_distinct_ports(tmp_path):
    svc = _svc(tmp_path)
    assert svc.stack_web_configure("graywolf@r2", port=8445).ok          # meshcom@r1's derived port
    assert svc.stack_web_configure("meshtastic@r2", port=8447).ok        # meshtastic@r1's derived port
    assert _bulk(svc)[0]
    got = _saved(svc)
    assert (got["graywolf@r2"], got["meshtastic@r2"]) == (8445, 8447)    # a saved port is never moved
    assert got["meshcom@r1"] == 8446 and got["meshtastic@r1"] == 8448 and got["reticulum@r1"] == 8449
    assert len(set(got.values())) == len(got)


def test_the_refusal_names_the_lowest_free_port_never_one_the_walk_assigned():
    owners = {9000: ["x@r1/c"], 9002: ["the console"]}
    p = webserver.plan_stack_exposure(StackWebConfig("meshcom@r1", port=9000), 8443, (9001,), owners)
    assert p["problems"] == ["port 9000 is already x@r1/c's listener — free: 9003"]
    full = {q: ["x@r1/c"] for q in range(9000, 65536)}
    p = webserver.plan_stack_exposure(StackWebConfig("meshcom@r1", port=9000), 8443, (), full)
    assert p["problems"] == ["port 9000 is already x@r1/c's listener — no free port at or above 9000"]


def test_a_page_on_a_listener_port_is_refused_naming_it_and_its_own_saved_port_is_not(tmp_path):
    svc = _svc(tmp_path, BOX_H)
    assert _bulk(svc)[0]
    r = svc.stack_web_configure("meshcom@r3", port=8458)                 # meshcore@r27's template node listener
    assert not r.ok and any("port 8458 is already meshcore@r27 (template)/meshcore-node's listener — free: 8459" in d
                            for d in r.details), r.details
    assert _bulk(svc)[0]                                                  # meshcom@r3 alone on its own saved port
    assert _saved(svc)["meshcom@r3"] == 8711
    cfgmod.save_stackweb_config(svc._paths, "meshcom@r3", port=8458)      # a page already saved ON that listener's port
    svc._invalidate_config()
    ok, r = _bulk(svc)                                                    # its own name is dropped, the listener's not
    assert not ok and ("  - meshcom@r3: port 8458 is already meshcore@r27 (template)/meshcore-node's listener — "
                       "free: 8459") in r.details, r.details


def test_the_single_panel_suggests_the_derived_port_and_saves_it(tmp_path):
    svc = _svc(tmp_path)
    v = svc.stack_web_view("meshcore-meshcore-node@r2")
    assert (v["suggested_port"], v["suggested_note"]) == (8582, "")
    assert svc.stack_web_configure("meshcore-meshcore-node@r2", port=v["suggested_port"]).ok
    assert _saved(svc)["meshcore-meshcore-node@r2"] == 8582


def test_no_free_port_gives_no_number_and_saves_nothing(tmp_path, monkeypatch):
    svc = _svc(tmp_path)
    monkeypatch.setattr(listeners, "stack_listeners",
                        lambda svc, overrides=None: {p: ["x@r1/c"] for p in range(8444, 65536)})
    v = svc.stack_web_view("graywolf@r1")
    assert (v["suggested_port"], v["suggested_note"]) == (None, "graywolf@r1: no free port at or above 8444")
    r = svc.stack_webs_configure_apply(**POLICY)
    assert not r.ok and "  - graywolf@r1: no free port at or above 8444" in r.details
    assert _saved(svc) == {}


def test_a_kind_without_an_index_is_refused_by_name_and_moves_no_other_page(tmp_path, monkeypatch):
    from lhpc.core import service_webserver
    monkeypatch.setattr(service_webserver, "PAGE_KINDS", ("graywolf",) + service_webserver.PAGE_KINDS[2:])
    svc = _svc(tmp_path)
    v = svc.stack_web_view("meshcom@r1")
    assert (v["suggested_port"], v["suggested_note"]) == (None, "meshcom@r1: page kind meshcom has no port index "
                                                                "(PAGE_KINDS)")
    assert svc.stack_web_view("graywolf@r1")["suggested_port"] == 8444
    r = svc.stack_webs_configure_apply(**POLICY)
    assert not r.ok and "  - meshcom@r1: page kind meshcom has no port index (PAGE_KINDS)" in r.details
    assert _saved(svc) == {}


def test_a_radio_above_r64_is_refused_by_name(tmp_path):
    # Not reachable through a page: a hardware list naming r65 loads no radios (measured), so its pages never exist.
    # The derived default refuses it anyway, before any fallback (plan v3 §2, NOTE C).
    port, why = _svc(tmp_path)._default_stack_web_port("graywolf@r65")
    assert (port, why) == (None, "graywolf@r65: radio r65 has no ports: an instance with listeners runs on r1..r64 "
                                 "(base + 133 x (n - 1))")


def test_a_port_param_save_is_judged_with_its_own_value_against_the_shared_owner_map(tmp_path):
    svc = _svc(tmp_path)
    assert svc.stack_web_configure("graywolf@r1", port=8444).ok
    svc._invalidate_config()
    r = svc.save_config("kiss", {"kiss_port": "8444"})                    # the save's own value meets a saved page
    assert not r.ok and any("port 8444 is already the graywolf@r1 web page's listener" in d for d in r.details), \
        r.details
    assert svc.save_config("kiss", {"kiss_port": "8070"}).ok              # its own move is judged by its new value
