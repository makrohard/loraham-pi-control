"""K8d: an instance runs on its radio's band. kiss@r2 on an 868 radio planned and ran its start and restart on 433
(the operation band's primary fallback): the daemon ensure, the TNC's band and its config store were r1's band.
G1-K8D1: the band is the radio ROW's, resolved by the rid, whatever band the caller or the instance carries."""
import pytest
import radios_fixture

from lhpc.core import instances
from lhpc.core.commands import Instance
from lhpc.core.lifecycle import Lifecycle
from lhpc.core.outcomes import CompResult, Outcome
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService


@pytest.fixture
def svc(tmp_path):
    radios_fixture.write(tmp_path, (radios_fixture.R1, radios_fixture.R2_868))          # r1 on 433, r2 on 868
    return ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))


@pytest.mark.parametrize("op", ["start", "restart"])
@pytest.mark.parametrize("inst,caller,band", [
    (Instance("r1", band="433"), "", "433"), (Instance("r2", band="868"), "", "868"),
    (Instance("r2"), "433", "868"),                         # no band on the instance, an ambient 433: r2's row
    (Instance("r2", band="433"), "", "868")])               # a band the row contradicts: the row's
def test_k8_an_instance_plans_on_its_radios_band(svc, op, inst, caller, band):
    res = getattr(svc, op)("kiss", band=caller, inst=inst)
    assert res.ok, res.summary
    assert res.details == [f"  [daemon] start/ensure --radio {band}, TXMODE=MANAGED",
                           f"  [start] loraham-kiss-tnc (band {band})"]


@pytest.mark.parametrize("inst", [Instance("r2"), Instance("r2", band="868")])
def test_k8_a_stop_of_kiss_at_r2_addresses_r2_alone_beside_an_ambient_433(svc, monkeypatch, inst):
    # G1-K8D1: the stop needs no band rule: it addresses the instance by its rid, token and endpoints (r1 on 433 runs)
    stops = []

    def stop(self, comp, **kw):
        stops.append((comp.id, kw.get("rid"), tuple(kw.get("token") or ()), tuple(e.address for e in kw.get("endpoints") or ())))
        return CompResult(component=comp.id, stack="", action="stop", outcome=Outcome.STOPPED, summary="stopped")
    monkeypatch.setattr(Lifecycle, "stop", stop)
    monkeypatch.setattr(ControllerService, "stop_dependents", lambda self, t, bands=None: [])
    monkeypatch.setattr(instances, "instance_runs", lambda s, c, i, band: i.rid in ("r1", "r2"))
    assert svc.stop("kiss", apply=True, band="433", inst=inst).ok
    assert stops == [("loraham-kiss-serial", "r2", (), ("state/r2/loraham_kiss",)),
                     ("loraham-kiss-tnc", "r2", ("--data-socket", "/tmp/loraf-r2.sock"), ("127.0.0.1:8134",))], stops


@pytest.mark.parametrize("op", ["start", "restart"])
def test_k8_the_daemons_own_instance_keeps_its_band_rule(svc, op):
    # the daemon's instance carries no band (radio_start.daemon_inst): its start and restart keep the caller's band;
    # the plan names the one daemon it launches, r2's (its argv: --radio 868 --id r2), never "all bands"
    from lhpc.core import radio_start
    res = getattr(svc, op)("daemon", inst=radio_start.daemon_inst("r2"))
    assert res.ok and res.details == ["  [daemon] start/ensure --radio 868 --id r2"], res.details


@pytest.mark.parametrize("op,word", [("start", "start"), ("restart", "restart")])
@pytest.mark.parametrize("inst", [Instance("r2"), Instance("r2", band="868")])
def test_k8_an_instance_whose_radio_left_the_list_is_refused_by_name(tmp_path, op, word, inst):
    # DEC-W384aj: r2 is not in the hardware list: refused by name, never run on the caller's or a stored band
    radios_fixture.write(tmp_path, (radios_fixture.R1,))
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    res = getattr(svc, op)("kiss", band="433", inst=inst)
    assert (res.ok, res.summary) == (False, f"Refusing to {word}: kiss@r2: r2 is not in the hardware list")


def test_k8_the_started_tnc_of_kiss_at_r2_runs_on_r2s_band_and_sockets(tmp_path, monkeypatch):
    # DEC-W384aj: the launch itself, not only the plan: kiss@r2 (868) beside an ambient 433 spawns its TNC with 868's
    # frequency, r2's sockets, and r2's 868 start log
    radios_fixture.write(tmp_path, (radios_fixture.R1, radios_fixture.R2_868))
    (tmp_path / "src" / "loraham-kiss-tnc").mkdir(parents=True)
    (tmp_path / "src" / "loraham-kiss-tnc" / "loraham-kiss-tnc").write_text("#bin")
    ready = {f"/tmp/loraconf-{r}.sock": f"STATUS RADIO=READY ID={r} BOUND=- TXMODE=MANAGED\n".encode() for r in ("r1", "r2")}
    svc = ControllerService(system=FakeSystem(unix_replies=ready).system, paths=Paths(runtime_root=tmp_path))
    assert svc.bootstrap(apply=True).ok and svc.set_operator_identity(callsign="XX0XXA").ok
    spawned = []

    def life(s):
        lc = Lifecycle(s._paths, s.stacks(), s.config(), s._system,
                       spawn=lambda argv, log, cwd=None, env=None: spawned.append((log.name, list(argv))) or 4321)
        lc.OBSERVE_TIMEOUT_S = 0.0
        monkeypatch.setattr(lc, "record_launch", lambda *a, **k: True)
        return lc
    monkeypatch.setattr(ControllerService, "_lifecycle", life)
    svc.start("kiss", apply=True, band="433", inst=Instance("r2"))      # it does not verify here: no listener
    (log, argv), = [s for s in spawned if s[1][0].endswith("loraham-kiss-tnc")]
    flags = {f: argv[argv.index(f) + 1] for f in ("--rx-freq", "--data-socket", "--conf-socket")}
    assert (log, flags) == ("start-loraham-kiss-tnc-868-r2.log", {"--rx-freq": "869.525",
                            "--data-socket": "/tmp/loraf-r2.sock", "--conf-socket": "/tmp/loraconf-r2.sock"})


def test_k8d_graywolf_on_r2_is_not_refused_by_kiss_running_on_r1(tmp_path, monkeypatch, set_call):
    # K8d item 3: a chain member takes the dependent's radio (13.5), so kiss@r1 live on 433 is no member of
    # graywolf@r2; red on 8289f771: "Cannot start 'graywolf' on 868 MHz: it needs 'kiss', which is already running on
    # 433 MHz". Through the public start (plan).
    import launch_band_fixture
    svc = ControllerService(system=FakeSystem(cmdlines_data={4242: ["loraham-kiss-tnc", "--config", "X"]}).system,
                            paths=Paths(runtime_root=tmp_path))
    set_call(svc)
    launch_band_fixture.set_launch_band(svc, "kiss", "433")
    monkeypatch.setattr(svc, "_band_owner_is_up", lambda sid, snap=None: sid == "kiss")
    sid, inst, band, _why = instances.resolve(svc, "graywolf@r2")
    res = svc.start(sid, apply=False, band=band, inst=inst)
    text = "\n".join((res.summary, *res.details))
    assert "already running on 433 MHz" not in text, text
