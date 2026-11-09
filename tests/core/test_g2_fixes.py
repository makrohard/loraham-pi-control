"""The four fixes of the Work round G2-0.14.0-1 (TEST-DESIGN-G2-FIXES-v1): the rows the code role's own tests leave
open. Item 1, a radio's row dp_* (through the public Apply, apply_daemon_params); item 2, every pid judged
from argv, exe and comm (through radio_start.start_plan); item 3, a boot-restore band item kept only when its band
resolves to its radio (radio_daemon.restore_plan). radios.py is new: each test imports it itself.
"""
from __future__ import annotations

import dataclasses
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


# ---- item 1: a radio's row dp_*, through the public Apply ----------------------------------------------------
# The Apply button's service call (apply_daemon_params) sends the radio's params to its daemon as CONF SETs; the fake
# daemon answers on the sockets it serves only and records each SET. DEC-W384az: the radio is named (rid), never a
# band, and an old per-band value (`dp_<band>_*` in daemon.toml: 700, 900, 300) is read nowhere — the row's value or
# the default (CADWAIT 1500, CADIDLE 250) is sent, to that radio's socket only.

@dataclasses.dataclass
class _ConfDaemon(FakeSystem):
    """A daemon per CONF socket in `serve` (path -> STATUS fields): a SET changes a field and is recorded in `sent`,
    GET STATUS and GET CHANNEL report the fields; every other path is refused."""
    serve: dict = dataclasses.field(default_factory=dict)

    def request(self, path, payload, timeout, max_bytes):
        if path not in self.serve:
            raise ConnectionRefusedError(f"no daemon on {path}")
        st = self.serve[path]
        line = payload.decode("ascii").strip()
        if line.startswith("SET "):
            self.sent.append((path, payload))
            k, _, v = line[4:].partition("=")
            st[k] = v
            return b"OK\n"
        if line == "GET STATUS":
            return ("STATUS " + " ".join(f"{k}={v}" for k, v in st.items()) + "\n").encode()
        if line == "GET STATS":
            return b"STATS TXOK=0 RXOK=0\n"
        if line.startswith("GET CHANNEL"):
            return f"CHANNEL MODE={st.get('MODE', 'LORA')} RSSI=-120 CADSTATE=NOTSCANNED BUSY=0\n".encode()
        return b"ERR UNKNOWN\n"


def _status(rid, band):
    return {"RADIO": "READY", "ID": rid, "BOUND": "-", "BAND": band, "TXMODE": "DIRECT", "POWER": "10"}


RID_SOCKETS = {"/tmp/loraconf-r1.sock": ("r1", "433"), "/tmp/loraconf-r2.sock": ("r2", "868")}
BAND_SOCKETS = {"/tmp/loraconf433.sock": ("-", "433"), "/tmp/loraconf868.sock": ("-", "868")}   # 0.13.x: never used


def _apply(tmp_path, rows, legacy, rid, *, sockets=RID_SOCKETS, raw=None, target="daemon"):
    """Apply on radio `rid` with the list `rows` (or the raw radios.toml `raw`; neither = no list) and the old per-band
    `legacy` values in daemon.toml; returns (result, [(socket, "SET CADWAIT=…"/"SET CADIDLE=…"), …] in order)."""
    from lhpc.core import config as cfgmod
    from lhpc.core.services import ControllerService
    p = Paths(runtime_root=tmp_path)
    cfgmod.save_hardware_setup(p, "uputronics")
    if raw is not None:
        (tmp_path / "config" / "radios.toml").write_text(raw)
    elif rows is not None:
        _write(tmp_path, rows)
    fake = _ConfDaemon(serve={path: _status(r, b) for path, (r, b) in sockets.items()})
    svc = ControllerService(system=fake.system, paths=p)
    cfgmod.update_stack_config(p, "daemon", legacy)
    res = svc.apply_daemon_params(target, rid=rid)
    sets = [(path, v.decode().strip()) for path, v in fake.sent
            if v.decode().startswith(("SET CADWAIT=", "SET CADIDLE="))]
    return res, sets


def _pair(dp433=(("CADWAIT", "100"),), dp868=(("CADWAIT", "300"),), band="868"):
    R = _radios().Radio
    return [R("r1", "a", "uputronics", "433", ce="ce0", dp=dp433), R("r2", "b", "uputronics", band, ce="ce1", dp=dp868)]


@pytest.mark.parametrize("rid,sock,row", [("r1", "/tmp/loraconf-r1.sock", "100"),
                                          ("r2", "/tmp/loraconf-r2.sock", "300")])
def test_r4_each_radio_gets_its_own_rows_value_and_no_old_band_value(tmp_path, rid, sock, row):
    """RED before the G2 fix: SET CADWAIT=700 / 900 (the stack's own value); DEC-W384az: that value is read nowhere."""
    res, sets = _apply(tmp_path, _pair(), {"dp_433_CADWAIT": "700", "dp_868_CADWAIT": "900"}, rid)
    assert res.ok, (res.summary, res.details)
    assert sets == [(sock, f"SET CADWAIT={row}"), (sock, "SET CADIDLE=250")]       # that radio's socket only


def test_r8_every_row_param_wins_over_its_legacy_value(tmp_path):
    """RED before the fix: SET CADWAIT=700, SET CADIDLE=300."""
    res, sets = _apply(tmp_path, _pair(dp433=(("CADIDLE", "400"), ("CADWAIT", "200")), dp868=()),
                       {"dp_433_CADWAIT": "700", "dp_433_CADIDLE": "300"}, "r1")
    assert res.ok, (res.summary, res.details)
    assert sets == [("/tmp/loraconf-r1.sock", "SET CADWAIT=200"), ("/tmp/loraconf-r1.sock", "SET CADIDLE=400")]


def test_r9_a_row_value_equal_to_the_default_still_wins_over_the_legacy_value(tmp_path):
    """RED before the fix: SET CADWAIT=700."""
    res, sets = _apply(tmp_path, _pair(dp433=(("CADWAIT", "1500"),), dp868=()), {"dp_433_CADWAIT": "700"}, "r1")
    assert res.ok, (res.summary, res.details)
    assert sets == [("/tmp/loraconf-r1.sock", "SET CADWAIT=1500"), ("/tmp/loraconf-r1.sock", "SET CADIDLE=250")]


def test_w384az_an_old_band_value_alone_is_not_sent_the_default_is(tmp_path):
    """DEC-W384az: a row without dp_* and only an old dp_433_* in daemon.toml: the default goes, never 700."""
    res, sets = _apply(tmp_path, _pair(dp433=(), dp868=()), {"dp_433_CADWAIT": "700"}, "r1")
    assert res.ok, (res.summary, res.details)
    assert sets == [("/tmp/loraconf-r1.sock", "SET CADWAIT=1500"), ("/tmp/loraconf-r1.sock", "SET CADIDLE=250")]


@pytest.mark.parametrize("rid,sock,row", [("r1", "/tmp/loraconf-r1.sock", "100"),
                                          ("r2", "/tmp/loraconf-r2.sock", "300")])
def test_r1_control_two_enabled_radios_on_a_band_each_gets_its_own_row(tmp_path, rid, sock, row):
    """CONTROL: two radios on 868; each Apply names its radio and sends that radio's row to its socket alone, never
    the other's and never the 0.13.x band socket (ledger L27, DEC-W27)."""
    R = _radios().Radio
    rows = [R("r1", "a", "uputronics", "868", ce="ce0", dp=(("CADWAIT", "100"),)),
            R("r2", "b", "uputronics", "868", ce="ce1", dp=(("CADWAIT", "300"),))]
    res, sets = _apply(tmp_path, rows, {"dp_868_CADWAIT": "900"}, rid,
                       sockets={**BAND_SOCKETS, "/tmp/loraconf-r1.sock": ("r1", "868"), "/tmp/loraconf-r2.sock": ("r2", "868")})
    assert res.ok, (res.summary, res.details)
    assert sets == [(sock, f"SET CADWAIT={row}"), (sock, "SET CADIDLE=250")]


@pytest.mark.parametrize("case,why", [pytest.param("no list", "r1 is not a radio of the hardware list",
                                                   marks=pytest.mark.no_default_hardware),   # really absent
                                      ("invalid list", "r1 is not a radio of the hardware list"),
                                      ("radio disabled", "r1 is disabled")])
def test_r2_r3_r5_control_a_radio_the_list_does_not_hold_enabled_is_refused(tmp_path, case, why):
    """CONTROL: no hardware list, a list that does not load, or the radio disabled: refused by name, nothing sent —
    not the old band value to the 0.13.x band socket either (DEC-W384az: no band form)."""
    R = _radios().Radio
    rows = {"no list": None, "invalid list": None,
            "radio disabled": [R("r1", "a", "uputronics", "433", ce="ce0", enabled=False, dp=(("CADWAIT", "100"),)),
                               R("r2", "b", "uputronics", "868", ce="ce1")]}[case]
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    res, sets = _apply(tmp_path, rows, {"dp_433_CADWAIT": "700"}, "r1", sockets={**BAND_SOCKETS, **RID_SOCKETS},
                       raw="[[radio]]\nid = 7\n" if case == "invalid list" else None)
    assert (res.ok, res.summary) == (False, f"Cannot apply daemon's daemon parameters: {why}")
    assert sets == []


@pytest.mark.parametrize("setup", ["uputronics-868", "uputronics"])
def test_k1_a_radio_the_list_does_not_hold_is_refused_never_remapped(tmp_path, setup):
    """K1 (S2): the list has one radio, r1 on 868; Apply on r2 is refused by name and sends nothing to r1. RED before
    K1 with the 868-only setup: the setup's single band remapped a 433 request onto 868 (applied 17/17)."""
    from lhpc.core import config as cfgmod
    from lhpc.core.services import ControllerService
    p = Paths(runtime_root=tmp_path)
    cfgmod.save_hardware_setup(p, setup)
    _write(tmp_path, [_radios().Radio("r1", "b", "uputronics", "868", ce="ce1")])
    fake = _ConfDaemon(serve={"/tmp/loraconf-r1.sock": _status("r1", "868"), "/tmp/loraconf868.sock": _status("-", "868")})
    res = ControllerService(system=fake.system, paths=p).apply_daemon_params("daemon", rid="r2")
    assert (res.ok, res.summary) == (False, "Cannot apply daemon's daemon parameters: r2 is not a radio of the hardware list")
    assert fake.sent == []


@pytest.mark.parametrize("rid", ["r1", "r2"])
def test_c57_a_fixed_band_client_has_no_panel_on_a_disabled_or_other_band_radio(tmp_path, monkeypatch, rid):
    """G1-C57 B1: meshcom (433 only) beside a list whose 433 radio is disabled and whose 868 radio runs: no panel on
    either radio, and its Apply on either is refused by name, nothing reaches r2. RED on b872e622: the panel moved to
    868 and its Apply sent the SETs to r2's socket."""
    from lhpc.core import config as cfgmod
    from lhpc.core import radio_start
    from lhpc.core.services import ControllerService
    p = Paths(runtime_root=tmp_path)
    cfgmod.save_hardware_setup(p, "uputronics")
    R = _radios().Radio
    _write(tmp_path, [R("r1", "a", "uputronics", "433", ce="ce0", enabled=False), R("r2", "b", "uputronics", "868", ce="ce1")])
    fake = _ConfDaemon(serve={"/tmp/loraconf-r2.sock": _status("r2", "868")})
    svc = ControllerService(system=fake.system, paths=p)
    monkeypatch.setattr(radio_start, "radio_clients", lambda s, b, r, sid: ([], sid == "meshcom"))   # "it runs there"
    assert svc.daemon_params_view("meshcom", rid=rid) == {}
    res = svc.apply_daemon_params("meshcom", rid=rid)
    assert not res.ok and res.summary == ("Cannot apply meshcom's daemon parameters: "
                                          + ("r1 is disabled" if rid == "r1" else "meshcom has no daemon parameters on r2"))
    assert fake.sent == []


def test_c57_a_switchable_client_applies_on_the_radio_the_list_serves(tmp_path, monkeypatch):
    """The other half of G1-C57 B1: kiss (433 or 868) on a list with r1 on 868 only: its panel and Apply are r1's,
    on 868, never its primary 433."""
    from lhpc.core import config as cfgmod
    from lhpc.core import radio_start
    from lhpc.core.services import ControllerService
    p = Paths(runtime_root=tmp_path)
    cfgmod.save_hardware_setup(p, "uputronics")
    _write(tmp_path, [_radios().Radio("r1", "b", "uputronics", "868", ce="ce1")])
    fake = _ConfDaemon(serve={"/tmp/loraconf-r1.sock": _status("r1", "868")})
    svc = ControllerService(system=fake.system, paths=p)
    monkeypatch.setattr(radio_start, "radio_clients", lambda s, b, r, sid: ([], sid == "kiss"))
    assert svc.daemon_params_view("kiss", rid="r1")["band"] == "868"
    res = svc.apply_daemon_params("kiss", rid="r1")
    assert res.data["band"] == "868" and fake.sent and {path for path, _v in fake.sent} == {"/tmp/loraconf-r1.sock"}


@pytest.mark.parametrize("rid", ["r9", "", "433"])
def test_r6_a_rid_that_is_no_enabled_radio_applies_nothing(tmp_path, rid):
    """A unit check: Apply refuses a rid the list does not hold first, so the public path never hands this guard
    one."""
    R = _radios().Radio
    _write(tmp_path, [R("r1", "a", "uputronics", "433", ce="ce0", dp=(("CADWAIT", "100"),))])
    assert _svc(tmp_path)._daemon_param_applies("daemon", rid) == {}


# ---- item 2: every pid judged from argv, exe and comm --------------------------------------------------------

def _refusal(tmp_path, procs):
    """r1's start-plan refusal with /proc holding `procs`: pid -> (argv or None, exe or None, comm or None, exists[,
    state[, task ids]]): the process state written to /proc/<pid>/stat (e.g. "Z") and its /proc/<pid>/task listing."""
    from lhpc.core import radio_start
    R = _radios().Radio
    _write(tmp_path, [R("r1", "low", "loraham", "433")])
    fake = {"cmdlines_data": {}, "links": {}, "files": {}, "paths": set(), "dirs": {"/proc": []}}
    for pid, (argv, exe, comm, exists, *more) in procs.items():
        fake["dirs"]["/proc"].append(str(pid))
        if more:                                    # (state[, task ids]): /proc/<pid>/stat and /proc/<pid>/task
            fake["files"][f"/proc/{pid}/stat"] = f"{pid} ({comm or 'x'}) {more[0]} 1 {pid} {pid} 0 -1 4194560"
            if len(more) > 1:
                fake["dirs"][f"/proc/{pid}/task"] = [str(t) for t in more[1]]
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


def test_p6_a_daemon_zombie_holds_no_radio(tmp_path):
    # W3-LAB-FLAKES-DIAG (a8c40e57): a `[loraham_daemon] <defunct>` left unreaped by any process on the host (argv [],
    # no exe, comm loraham_daemon, state Z) was read as an unknown daemon holding every radio, so r1's start was
    # refused. A zombie has released everything (Lifecycle._proc_ceased: Z/X = ceased): it holds no radio.
    assert _refusal(tmp_path, {4242: ([], None, "loraham_daemon", True, "Z", [4242])}) == ""
    assert _refusal(tmp_path, {4242: ([], None, "loraham_daemon", True, "X", [4242])}) == ""


def test_g1_zb1_a_zombie_leader_with_a_live_thread_still_holds_the_radio(tmp_path):
    # DEC-W384bk: the leader shows Z after pthread_exit while a worker thread runs on (and may hold the radio): only a
    # task list of the leader alone is dead; more threads, or no readable task list, keep the daemon counted
    assert "4242" in _refusal(tmp_path, {4242: ([], None, "loraham_daemon", True, "Z", [4242, 4243])})
    assert "4242" in _refusal(tmp_path, {4242: ([], None, "loraham_daemon", True, "Z")})


def test_p6_the_same_entry_alive_still_refuses(tmp_path):
    # the state decides, not the empty argv: the same entry in state S (or with no readable stat) still refuses
    assert "4242" in _refusal(tmp_path, {4242: ([], None, "loraham_daemon", True, "S")})
    assert "4242" in _refusal(tmp_path, {4242: ([], None, "loraham_daemon", True)})


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
    item = {"target": "kiss", "band": "433", "rid": "r1"}
    kept, refused = _plan([R("r1", "a", "loraham", "433"), R("r2", "b", "waveshare-sx1262", "433", enabled=False)],
                          [item])
    assert kept == [item] and refused == []


@pytest.mark.parametrize("band", ["433", "868"])
def test_b2_a_band_less_item_with_a_radio_is_kept(band):
    R = _radios().Radio
    item = {"target": "reticulum", "band": "", "rid": "r1"}
    kept, refused = _plan([R("r1", "a", "loraham", band)], [item])
    assert kept == [item] and refused == []


def test_b3_a_radio_now_on_another_band_is_refused_naming_both_bands():
    R = _radios().Radio
    item = {"target": "kiss", "band": "868", "rid": "r1"}
    kept, refused = _plan([R("r1", "a", "loraham", "433"), R("r2", "b", "loraham", "868")], [item])
    assert kept == [] and [it for it, _why in refused] == [item]
    assert "433" in refused[0][1] and "868" in refused[0][1]


@pytest.mark.parametrize("seed", range(30))
def test_b4_a_band_item_is_kept_exactly_when_its_radio_is_enabled_on_its_band(seed):
    """Seeded lists of 1-4 radios, some disabled (an all-disabled list included); the item names any of them. K4b: an
    item starts on its own radio (`<stack>@<rid>`), so it is kept exactly when that radio is enabled on its band (the
    slot-0 rule, the band's first radio, is gone)."""
    R = _radios().Radio
    rng = random.Random(seed)  # noqa: S311 - a seeded, repeatable test input, no secret
    rows = [R(f"r{i}", f"n{i}", "loraham", rng.choice(["433", "868"]), enabled=rng.random() < 0.7)
            for i in range(1, rng.randint(2, 5))]
    r = rng.choice(rows)
    item = {"target": f"kiss@{r.id}", "band": rng.choice(["433", "868"]), "rid": r.id}
    kept, refused = _plan(rows, [item])
    want = r.enabled and r.band == item["band"]
    assert (kept, [it for it, _why in refused]) == (([item], []) if want else ([], [item]))


def test_b5_in_an_all_disabled_list_the_item_is_refused_as_disabled():
    R = _radios().Radio
    item = {"target": "kiss", "band": "433", "rid": "r1"}
    kept, refused = _plan([R("r1", "a", "loraham", "433", enabled=False),
                           R("r2", "b", "loraham", "868", enabled=False)], [item])
    assert kept == [] and refused == [(item, "radio r1 is disabled")]
