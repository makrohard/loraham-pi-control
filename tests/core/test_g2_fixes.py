"""The four fixes of the Work round G2-0.14.0-1 (TEST-DESIGN-G2-FIXES-v1): the rows the code role's own tests leave
open. Item 1, a radio's row dp_* (radio_start.row_dp and the service's _daemon_param_applies); item 2, every pid judged
from argv, exe and comm (through radio_start.start_plan); item 3, a boot-restore band item kept only when its band
resolves to its radio (radio_daemon.restore_plan). radios.py is new: each test imports it itself.
"""
from __future__ import annotations

import random

import pytest

from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem

DAEMON = "/opt/lhpc/src/loraham-daemon/loraham_daemon/loraham_daemon"


def _radios():
    from lhpc.core import radios
    return radios


def _write(tmp_path, rows):
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    (tmp_path / "config" / "radios.toml").write_text(_radios().render(tuple(rows)))


def _svc(tmp_path, **fake):
    from lhpc.core.services import ControllerService
    return ControllerService(system=FakeSystem(**fake).system, paths=Paths(runtime_root=tmp_path))


# ---- item 1: a radio's row dp_* --------------------------------------------------------------------------

def _row_dp(tmp_path, band):
    from lhpc.core import radio_start
    return radio_start.row_dp(_svc(tmp_path), band)


def test_r1_two_enabled_radios_on_a_band_give_no_row_value(tmp_path):
    R = _radios().Radio
    _write(tmp_path, [R("r1", "a", "uputronics", "868", ce="ce0", dp=(("CADWAIT", "100"),)),
                      R("r2", "b", "uputronics", "868", ce="ce1", dp=(("CADWAIT", "300"),))])
    assert _row_dp(tmp_path, "868") == {}


def test_r1_the_stacks_value_is_applied_when_the_band_is_shared(tmp_path):
    from lhpc.core import config as cfgmod
    R = _radios().Radio
    _write(tmp_path, [R("r1", "a", "uputronics", "868", ce="ce0", dp=(("CADWAIT", "100"),)),
                      R("r2", "b", "uputronics", "868", ce="ce1", dp=(("CADWAIT", "300"),))])
    svc = _svc(tmp_path)
    cfgmod.update_stack_config(svc._paths, "daemon", {"dp_868_CADWAIT": "1500"})
    assert svc._daemon_param_applies("daemon", "868").get("CADWAIT") == "1500"


def test_r2_no_hardware_list_gives_no_row_value(tmp_path):
    assert _row_dp(tmp_path, "433") == {}


def test_r3_a_disabled_radio_gives_its_band_no_row_value(tmp_path):
    R = _radios().Radio
    _write(tmp_path, [R("r1", "a", "uputronics", "433", ce="ce0", enabled=False, dp=(("CADWAIT", "100"),)),
                      R("r2", "b", "uputronics", "868", ce="ce1")])
    assert _row_dp(tmp_path, "433") == {}


def test_r4_each_band_gets_its_own_radios_value_only(tmp_path):
    R = _radios().Radio
    _write(tmp_path, [R("r1", "a", "uputronics", "433", ce="ce0", dp=(("CADWAIT", "100"),)),
                      R("r2", "b", "uputronics", "868", ce="ce1", dp=(("CADWAIT", "300"),))])
    assert _row_dp(tmp_path, "433") == {"CADWAIT": "100"}
    assert _row_dp(tmp_path, "868") == {"CADWAIT": "300"}


def test_r5_an_invalid_list_gives_no_row_value(tmp_path):
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "radios.toml").write_text("[[radio]]\nid = 7\n")
    assert _row_dp(tmp_path, "433") == {}


@pytest.mark.parametrize("key,value", [("cadrssi_433", "zzz"), ("ocp_433", "nope"), ("cadmon_433", "maybe")])
def test_r7_a_radio_whose_settings_do_not_resolve_gives_no_row_value(tmp_path, key, value):
    """Its start is refused (radio_settings raises), so no row value reaches its daemon either."""
    from lhpc.core import config as cfgmod
    from lhpc.core import radio_start
    R = _radios().Radio
    _write(tmp_path, [R("r1", "a", "uputronics", "433", ce="ce0", dp=(("CADWAIT", "100"),))])
    svc = _svc(tmp_path)
    cfgmod.update_stack_config(svc._paths, "daemon", {key: value})
    assert radio_start.row_dp(svc, "433") == {}


@pytest.mark.parametrize("band", ["915", "", "r1"])
def test_r6_a_band_outside_433_868_applies_nothing(tmp_path, band):
    R = _radios().Radio
    _write(tmp_path, [R("r1", "a", "uputronics", "433", ce="ce0", dp=(("CADWAIT", "100"),))])
    assert _svc(tmp_path)._daemon_param_applies("daemon", band) == {}


# ---- item 2: every pid judged from argv, exe and comm --------------------------------------------------------

def _refusal(tmp_path, procs):
    """r1's start-plan refusal with /proc holding `procs`: pid -> (argv or None, exe or None, comm or None, exists)."""
    from lhpc.core import radio_start
    R = _radios().Radio
    _write(tmp_path, [R("r1", "low", "loraham", "433")])
    fake = {"cmdlines_data": {}, "links": {}, "files": {}, "paths": set(), "dirs": {"/proc": []}}
    for pid, (argv, exe, comm, exists) in procs.items():
        fake["dirs"]["/proc"].append(str(pid))
        if argv is not None:
            fake["cmdlines_data"][pid] = argv
        if exe is not None:
            fake["links"][f"/proc/{pid}/exe"] = exe
        if comm is not None:
            fake["files"][f"/proc/{pid}/comm"] = comm
        if exists:
            fake["paths"].add(f"/proc/{pid}")
    _params, refusal, _notes = radio_start.start_plan(_svc(tmp_path, **fake), "r1")
    return refusal


def test_p1_a_pid_gone_by_the_reads_is_not_counted(tmp_path):
    assert _refusal(tmp_path, {4242: (None, None, None, False)}) == ""


def test_p2_a_daemon_named_only_by_its_exe_refuses(tmp_path):
    assert "4242" in _refusal(tmp_path, {4242: (None, DAEMON, None, True)})


def test_p3_a_daemon_comm_beside_another_programs_argv_refuses(tmp_path):
    assert "4242" in _refusal(tmp_path, {4242: (["/usr/bin/python3", "x.py"], None, "loraham_daemon", True)})


def test_p4_a_pid_only_in_the_listing_that_cannot_be_read_refuses(tmp_path):
    assert "4242" in _refusal(tmp_path, {4242: (None, None, None, True)})


def _oracle(argv, exe, comm, exists):
    """The rule measured at 15df37b5 (daemon_entry): a daemon when any readable source names the binary; another
    program or a kernel thread is not; unknown otherwise, counted only while the pid exists."""
    names = [n for n in (exe and exe.rsplit("/", 1)[-1], argv[0].rsplit("/", 1)[-1] if argv else None,
                         comm and comm.strip()) if n]
    if "loraham_daemon" in names:
        return True
    if names or (exe is None and argv == []):
        return False
    return exists


ARGVS = [None, [], ["/usr/bin/python3", "x.py"], [DAEMON, "--radio", "433"]]
EXES = [None, "/usr/bin/bash", DAEMON]
COMMS = [None, "bash", "kworker/0:1", "loraham_daemon"]


@pytest.mark.parametrize("seed", range(30))
def test_p5_a_start_refuses_exactly_when_a_pid_is_or_cannot_be_told_from_the_daemon(tmp_path, seed):
    rng = random.Random(seed)  # noqa: S311 - a seeded, repeatable test input, no secret
    procs = {4000 + i: (rng.choice(ARGVS), rng.choice(EXES), rng.choice(COMMS), rng.random() < 0.7)
             for i in range(rng.randint(1, 3))}
    want = any(_oracle(*p) for p in procs.values())
    refusal = _refusal(tmp_path, procs)
    assert bool(refusal) is want, (procs, refusal)


# ---- item 3: a boot-restore band item kept only when its band resolves to its radio ----------------------------

def _plan(rows, items):
    from lhpc.core import radio_daemon
    return radio_daemon.restore_plan(items, rows)


def test_b1_a_disabled_radio_does_not_share_the_band(tmp_path):
    R = _radios().Radio
    kept, refused = _plan([R("r1", "a", "loraham", "433"), R("r2", "b", "waveshare-sx1262", "433", enabled=False)],
                          [{"target": "kiss", "band": "433", "rid": "r1"}])
    assert len(kept) == 1 and refused == []


@pytest.mark.parametrize("band", ["433", "868"])
def test_b2_a_band_less_item_with_a_radio_is_kept(band):
    R = _radios().Radio
    kept, refused = _plan([R("r1", "a", "loraham", band)], [{"target": "reticulum", "band": "", "rid": "r1"}])
    assert len(kept) == 1 and refused == []


def test_b3_a_radio_now_on_another_band_is_refused_naming_both_bands():
    R = _radios().Radio
    kept, refused = _plan([R("r1", "a", "loraham", "433"), R("r2", "b", "loraham", "868")],
                          [{"target": "kiss", "band": "868", "rid": "r1"}])
    assert kept == [] and "433" in refused[0][1] and "868" in refused[0][1]


@pytest.mark.parametrize("seed", range(30))
def test_b4_a_band_item_is_kept_exactly_when_its_bands_enabled_radios_are_its_radio(seed):
    R = _radios().Radio
    rng = random.Random(seed)  # noqa: S311 - a seeded, repeatable test input, no secret
    rows = [R(f"r{i}", f"n{i}", "loraham", rng.choice(["433", "868"]), enabled=rng.random() < 0.7)
            for i in range(1, rng.randint(2, 5))]
    enabled = [r for r in rows if r.enabled]
    if not enabled:
        return
    r = rng.choice(enabled)
    band = rng.choice(["433", "868"])
    kept, refused = _plan(rows, [{"target": "kiss", "band": band, "rid": r.id}])
    want = [x.id for x in rows if x.enabled and x.band == band] == [r.id]
    assert (len(kept) == 1) is want and (len(refused) == 1) is (not want)
