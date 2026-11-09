"""K8d item 1 (W401, DEC-W384az): a client stack's daemon radio parameters are per instance, `dp_<PARAM>` in
config/stacks/<stack>@<rid>.toml, applied over its radio's row (daemon@<rid>'s, config/radios.toml) when the instance
starts there; no per-band value (`dp_<band>_<PARAM>`) is read. Driven through the unchanged public paths: the CLI,
the web's panel route and the start's apply after READY (`_apply_stack_daemon_params(sid, band, rid)`)."""
import tomllib

import pytest
import radios_fixture

from lhpc.adapters.cli import main as cli
from lhpc.core import config as cfgmod
from lhpc.core import radios
from lhpc.core.commands import Instance
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService

ROWS = (radios.Radio("r1", "a", "uputronics", "433", ce="ce0"), radios.Radio("r3", "b", "uputronics", "433", ce="ce1"))
READY = {f"/tmp/loraconf-{r}.sock": f"STATUS RADIO=READY ID={r} BOUND=- TXMODE=MANAGED\n".encode() for r in ("r1", "r3")}


def _stored(tmp_path, name):
    p = tmp_path / "config" / "stacks" / name
    return tomllib.loads(p.read_text()) if p.exists() else {}


def _row_dp(tmp_path):
    return {r.id: dict(r.dp) for r in radios.load(Paths(runtime_root=tmp_path)).radios}


@pytest.fixture
def box(tmp_path, monkeypatch, capsys):
    radios_fixture.write(tmp_path, ROWS)
    fake = FakeSystem(unix_replies=READY)
    svc = ControllerService(system=fake.system, paths=Paths(runtime_root=tmp_path))
    monkeypatch.setattr(cli, "ControllerService", lambda *a, **k: ControllerService(system=fake.system,
                                                                                    paths=Paths(runtime_root=tmp_path)))

    def run(*argv):
        rc = cli.main(["config", *argv])
        return rc, capsys.readouterr().out
    return svc, fake, run


def _sets(fake, name):
    return [(p, v.decode().strip()) for p, v in fake.sent if v.decode().startswith(f"SET {name}=")]


def test_cli_an_app_instances_daemon_param_is_its_own_set_never_its_radios_row(box, tmp_path):
    # red on c08157ed: "saved the daemon settings of r3 (via chat@r3)" and r3's row {"SF": "9"}
    _svc, _fake, run = box
    rc, out = run("chat@r3", "--daemon-param", "SF=9")
    assert (rc, out.splitlines()[0]) == (0, "OK    saved the daemon settings of chat@r3"), out
    assert _stored(tmp_path, "chat@r3.toml").get("dp_SF") == "9"
    assert _row_dp(tmp_path) == {"r1": {}, "r3": {}}


def test_cli_an_instance_holds_its_default_against_its_radios_row(box, tmp_path):
    # C2: r1's row says CADIDLE 111; chat@r1 saved at its default 250 keeps 250 (it differs from what it inherits);
    # saved at the row's 111 it clears. red on c08157ed: both forms wrote r1's row
    _svc, _fake, run = box
    assert run("daemon@r1", "--daemon-param", "CADIDLE=111")[0] == 0
    assert run("chat@r1", "--daemon-param", "CADIDLE=250")[0] == 0
    assert _stored(tmp_path, "chat@r1.toml").get("dp_CADIDLE") == "250" and _row_dp(tmp_path)["r1"] == {"CADIDLE": "111"}
    assert run("chat@r1", "--daemon-param", "CADIDLE=111")[0] == 0
    assert "dp_CADIDLE" not in _stored(tmp_path, "chat@r1.toml")


def test_start_sends_the_instances_own_set_over_its_radios_row(box, tmp_path):
    # C1: kiss@r3's CADWAIT 300 goes to r3 over r3's row 200; the row's CADIDLE 400 under it; nothing to r1.
    # red on c08157ed: SET CADWAIT=200 (the row won; the instance file was never read)
    svc, fake, _run = box
    radios_fixture.write(tmp_path, (ROWS[0], radios.Radio("r3", "b", "uputronics", "433", ce="ce1",
                                                          dp=(("CADIDLE", "400"), ("CADWAIT", "200")))))
    cfgmod.update_stack_config(svc._paths, "kiss", {"dp_CADWAIT": "300"}, inst=Instance("r3"))
    lines, _tx_ok, gate = svc._apply_stack_daemon_params("kiss", "433", "r3")
    assert gate == "", lines
    assert _sets(fake, "CADWAIT") == [("/tmp/loraconf-r3.sock", "SET CADWAIT=300")], lines
    assert _sets(fake, "CADIDLE") == [("/tmp/loraconf-r3.sock", "SET CADIDLE=400")], lines


@pytest.mark.parametrize("holder", ["kiss", "daemon"])
def test_start_sends_no_old_per_band_value(box, holder):
    # an old dp_433_CADWAIT in the starting stack's own file (kiss.toml, daemon.toml) is read nowhere: the default 1500
    # goes. red on c08157ed: SET CADWAIT=900 (folded into the radio's settings, or read as the stack's own)
    svc, fake, _run = box
    cfgmod.update_stack_config(svc._paths, holder, {"dp_433_CADWAIT": "900"})
    lines, _tx_ok, _gate = svc._apply_stack_daemon_params(holder, "433", "r1")
    assert _sets(fake, "CADWAIT") == [("/tmp/loraconf-r1.sock", "SET CADWAIT=1500")], lines


def test_web_a_client_panel_save_writes_its_instances_set(web, csrf, tmp_path):
    # the panel's route as it posts today (band, no radio): kiss's one radio on 433 is r1 (the baseline list), so
    # kiss@r1's set. red on c08157ed: dp_433_CADIDLE in kiss.toml
    c = web()
    r = c.post("/stacks/kiss/daemon-params", data={"_csrf": csrf(c), "band": "433", "dp_CADIDLE": "40"})
    assert r.status_code in (302, 303)
    assert _stored(tmp_path, "kiss@r1.toml").get("dp_CADIDLE") == "40"
    assert _stored(tmp_path, "kiss.toml") == {}
