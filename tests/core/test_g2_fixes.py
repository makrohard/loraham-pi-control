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
# The Apply button's service call (apply_daemon_params) sends the band's params to its daemon as CONF SETs; the fake
# daemon answers on the sockets it serves only and records each SET. A RED case sends the stack's own conflicting
# value before the fix (4a38458d) and the row's value after it; a CONTROL passes on both sides (tests/README rule 12)
# and pins what the fix must not change. The legacy values (700, 900, 300) differ from the default (CADWAIT 1500,
# CADIDLE 250), so the row, the stack's value and the default are told apart.

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


def _apply(tmp_path, rows, legacy, band, *, sockets=RID_SOCKETS, raw=None):
    """Apply on `band` with the list `rows` (or the raw radios.toml `raw`; neither = no list) and the daemon stack's
    `legacy` config; returns (result, [(socket, "SET CADWAIT=…"/"SET CADIDLE=…"), …] in order)."""
    from lhpc.core import config as cfgmod
    from lhpc.core.services import ControllerService
    p = Paths(runtime_root=tmp_path)
    cfgmod.save_hardware_setup(p, "uputronics")
    if raw is not None:
        (tmp_path / "config" / "radios.toml").write_text(raw)
    elif rows is not None:
        _write(tmp_path, rows)
    fake = _ConfDaemon(serve={path: _status(rid, b) for path, (rid, b) in sockets.items()})
    svc = ControllerService(system=fake.system, paths=p)
    cfgmod.update_stack_config(p, "daemon", legacy)
    res = svc.apply_daemon_params("daemon", band)
    sets = [(path, v.decode().strip()) for path, v in fake.sent
            if v.decode().startswith(("SET CADWAIT=", "SET CADIDLE="))]
    return res, sets


def _pair(dp433=(("CADWAIT", "100"),), dp868=(("CADWAIT", "300"),), band="868"):
    R = _radios().Radio
    return [R("r1", "a", "uputronics", "433", ce="ce0", dp=dp433), R("r2", "b", "uputronics", band, ce="ce1", dp=dp868)]


@pytest.mark.parametrize("band,sock,row", [("433", "/tmp/loraconf-r1.sock", "100"),
                                           ("868", "/tmp/loraconf-r2.sock", "300")])
def test_r4_each_band_gets_its_own_radios_row_value_over_its_conflicting_legacy_value(tmp_path, band, sock, row):
    """RED before the fix: SET CADWAIT=700 / 900 (the stack's own value)."""
    res, sets = _apply(tmp_path, _pair(), {"dp_433_CADWAIT": "700", "dp_868_CADWAIT": "900"}, band)
    assert res.ok, (res.summary, res.details)
    assert sets == [(sock, f"SET CADWAIT={row}"), (sock, "SET CADIDLE=250")]       # that radio's socket only


def test_r8_every_row_param_wins_over_its_legacy_value(tmp_path):
    """RED before the fix: SET CADWAIT=700, SET CADIDLE=300."""
    res, sets = _apply(tmp_path, _pair(dp433=(("CADIDLE", "400"), ("CADWAIT", "200")), dp868=()),
                       {"dp_433_CADWAIT": "700", "dp_433_CADIDLE": "300"}, "433")
    assert res.ok, (res.summary, res.details)
    assert sets == [("/tmp/loraconf-r1.sock", "SET CADWAIT=200"), ("/tmp/loraconf-r1.sock", "SET CADIDLE=400")]


def test_r9_a_row_value_equal_to_the_default_still_wins_over_the_legacy_value(tmp_path):
    """RED before the fix: SET CADWAIT=700."""
    res, sets = _apply(tmp_path, _pair(dp433=(("CADWAIT", "1500"),), dp868=()), {"dp_433_CADWAIT": "700"}, "433")
    assert res.ok, (res.summary, res.details)
    assert sets == [("/tmp/loraconf-r1.sock", "SET CADWAIT=1500"), ("/tmp/loraconf-r1.sock", "SET CADIDLE=250")]


def test_r1_control_two_enabled_radios_on_a_band_apply_the_stacks_value(tmp_path):
    """CONTROL: the band names no one radio, so neither row value is sent; the stack's value goes to the band socket."""
    R = _radios().Radio
    rows = [R("r1", "a", "uputronics", "868", ce="ce0", dp=(("CADWAIT", "100"),)),
            R("r2", "b", "uputronics", "868", ce="ce1", dp=(("CADWAIT", "300"),))]
    res, sets = _apply(tmp_path, rows, {"dp_868_CADWAIT": "900"}, "868",
                       sockets={"/tmp/loraconf868.sock": ("-", "868"), **RID_SOCKETS})
    assert res.ok, (res.summary, res.details)
    assert sets == [("/tmp/loraconf868.sock", "SET CADWAIT=900"), ("/tmp/loraconf868.sock", "SET CADIDLE=250")]


@pytest.mark.parametrize("case", ["no list", "invalid list", "radio disabled"])
def test_r2_r3_r5_control_a_band_without_its_one_radio_applies_the_stacks_value(tmp_path, case):
    """CONTROL: no hardware list, a list that does not load, or the band's only radio disabled: no row value; the
    stack's value goes to the 0.13.x band socket."""
    R = _radios().Radio
    rows = {"no list": None, "invalid list": None,
            "radio disabled": [R("r1", "a", "uputronics", "433", ce="ce0", enabled=False, dp=(("CADWAIT", "100"),)),
                               R("r2", "b", "uputronics", "868", ce="ce1")]}[case]
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    res, sets = _apply(tmp_path, rows, {"dp_433_CADWAIT": "700"}, "433",
                       sockets={"/tmp/loraconf433.sock": ("-", "433"), **RID_SOCKETS},
                       raw="[[radio]]\nid = 7\n" if case == "invalid list" else None)
    assert res.ok, (res.summary, res.details)
    assert sets == [("/tmp/loraconf433.sock", "SET CADWAIT=700"), ("/tmp/loraconf433.sock", "SET CADIDLE=250")]


@pytest.mark.parametrize("key,value", [("cadrssi_433", "zzz"), ("ocp_433", "nope"), ("cadmon_433", "maybe")])
def test_r7_control_a_radio_whose_settings_do_not_resolve_sends_no_row_value(tmp_path, key, value):
    """CONTROL: its start is refused (radio_settings raises), so its row value reaches no SET either; the stack's
    value goes to its radio's socket."""
    res, sets = _apply(tmp_path, _pair(dp868=()), {"dp_433_CADWAIT": "700", key: value}, "433")
    assert res.ok, (res.summary, res.details)
    assert sets == [("/tmp/loraconf-r1.sock", "SET CADWAIT=700"), ("/tmp/loraconf-r1.sock", "SET CADIDLE=250")], \
        (res.summary, res.details)


@pytest.mark.parametrize("band", ["915", "", "r1"])
def test_r6_a_band_outside_433_868_applies_nothing(tmp_path, band):
    """A unit check: Apply resolves every band it is given to a served one first, so the public path never hands
    this guard a band outside 433/868."""
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
