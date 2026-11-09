"""Item 7c2a (DEC-W15/W16/W31, K4ac): `lhpc stack start <stack>@<rid>` starts a copy on its radio's band, and slot 0
runs on the first enabled radio of its band; the band's further radios are the copies (the hardware list's).

An instance is told apart by its process spec's instance token (kiss-tnc: `--data-socket` = its radio's socket) and
by its ownership records' rid, the matcher kill.py uses. The CLI runs on a FakeSystem service over a temporary
runtime root: the daemon's CONF socket answers READY, the launch is recorded (Lifecycle.start), and a component's
readiness port is a declared listener."""
from radios_fixture import R1, R2_868, R2, R3, kiss_on as _kiss_on

from lhpc.adapters.cli import main as cli
from lhpc.core import commands, radio_start

def test_a_listed_instance_starts_on_its_radio_as_its_slot(box):
    rc, out, launches = box((R1, R2_868, R3), ports=(8267,))("start", "kiss@r3")
    assert ("loraham-kiss-tnc", "868", "r3") in launches, (launches, out)
    assert rc == 0, out


def test_an_instance_that_runs_is_already_healthy_and_nothing_launches(box):
    run = box((R1, R2_868, R3), procs={4100: _kiss_on("r3")}, ports=(8267,))
    rc, out, launches = run("start", "kiss@r3")
    assert rc == 0 and "already healthy" in out and launches == [], (out, launches)


def test_slot_0_is_not_healthy_because_another_instance_runs(box):
    # Only kiss@r3 runs; kiss@r1 is slot 0 on r1 (433) and launches, never reads r3's process as its own.
    run = box((R1, R2_868, R3), procs={4100: _kiss_on("r3")}, ports=(8001,))
    rc, out, launches = run("start", "kiss@r1")                     # K3: slot 0 named on its radio
    assert ("loraham-kiss-tnc", "433", "r1") in launches, (launches, out)


def test_slot_0_that_runs_beside_a_listed_copy_is_already_healthy(box):
    # Slot 0 runs on r1 (its DEC-W16 radio) while kiss@r3 is listed: its start is the no-op, judged on slot 0's own
    # copy from copy_of (CLEANUP-DESIGN-v6: slot 0 is a real copy), never a re-derived radio.
    run = box((R1, R2_868, R3), procs={4100: _kiss_on("r1")}, ports=(8001,))
    rc, out, launches = run("start", "kiss@r1")                     # K3: slot 0 named on its radio
    assert rc == 0 and "already healthy" in out and launches == [], (out, launches)


def test_a_band_caller_takes_the_bands_first_radio(box):
    # K4b (no slots): a band caller (until K6) means the band's first enabled radio, r1; r2 is kiss@r2, an instance
    # like any other, so there is no choice to name.
    box((R1, R2, R3))
    svc = cli.ControllerService()
    comp = next(c for c in svc.stack("kiss").components if c.id == "loraham-kiss-tnc")
    out = []
    inst, why = radio_start.client_instance(svc, comp, "433", None, out)
    assert (inst.rid, why, out) == ("r1", "", [])


def test_the_cli_refuses_an_instance_on_no_radio_of_the_list(box, capsys):
    box((R1, R2_868, R3))
    rc = cli.main(["stack", "start", "kiss@r9", "--yes"])
    assert rc == 1 and "kiss@r9: r9 is not a radio of the hardware list" in capsys.readouterr().err


def test_poststart_takes_an_instance_like_every_stack_verb(box, capsys):
    # K4b: every instance is a stack's instance, so `poststart kiss@r3` reaches the stack's own answer (kiss declares
    # no post-start steps), no longer a "not for a copy" refusal.
    box((R1, R2_868, R3))
    rc = cli.main(["stack", "poststart", "kiss@r3", "--yes"])
    assert rc == 1 and "'kiss' declares no post-start steps." in capsys.readouterr().out


def test_the_plan_names_the_instance(box):
    rc, out, _launches = box((R1, R2_868, R3), ports=(8267,))("start", "kiss@r3")
    assert "Run plan for 'kiss@r3': 2 component(s) in order." in out, out


def test_an_instances_claims_are_its_own_and_slot_0_keeps_its_keys(box):
    # Item 7a: the start's conflict check keys the target's claims by its instance (r3: BASE + 266, the kiss-serial
    # key with its radio's suffix); slot 0's keys are the manifest's, unchanged.
    from lhpc.core import instances
    box((R1, R2_868, R3))
    stack = cli.ControllerService().stack("kiss")
    claims = [r for c in stack.components for r in c.resources if not r.key.startswith("loraham.")]
    assert [instances.claim_key(r, commands.Instance("r3")) for r in claims] == ["tcp.port.8267",
                                                                                    "serial.loraham-kiss-r3"]
    assert [instances.claim_key(r, commands.TEMPLATE) for r in claims] == ["tcp.port.8001", "serial.loraham-kiss"]


def test_a_start_writes_no_per_stack_band_marker(box, tmp_path):
    # K4b (design 13.12.5): an instance's band is its radio's, so neither kiss@r1 (433's first radio, as a band
    # caller's start resolves) nor kiss@r3 writes state/running/kiss.band.
    run = box((R1, R2_868, R3), ports=(8001, 8267))
    assert run("start", "kiss@r1")[0] == 0 and run("start", "kiss@r3")[0] == 0
    assert not (tmp_path / "state" / "running" / "kiss.band").exists()


def test_an_instance_on_another_band_starts_while_kiss_at_r1_runs(box):
    # K4b: kiss@r1 running on 433 holds back no other instance: a band caller on 868 plans 868's first radio's
    # instance (kiss@r2), and kiss@r3 starts on its own radio.
    run = box((R1, R2_868, R3), procs={4100: _kiss_on("r1")}, ports=(8001, 8267))
    svc = cli.ControllerService()
    plan = svc.start("kiss", apply=False, band="868")
    assert plan.ok and plan.summary == "Run plan for 'kiss@r2': 2 component(s) in order.", plan.summary
    rc, out, launches = run("start", "kiss@r3")
    assert ("loraham-kiss-tnc", "868", "r3") in launches, (launches, out)


def test_a_running_copy_the_start_pass_reaches_is_judged_at_its_own_port(box, monkeypatch):
    # K4ac: when the start pass runs (here: the no-op check says no, as when another component of the order still
    # needs starting), a running kiss@r3 is judged by its own listener (8267), never slot 0's 8001: it is "already
    # running", not "foreign or half-started", and nothing launches it twice.
    from lhpc.core import instances
    monkeypatch.setattr(instances, "already_healthy", lambda *a, **k: False)
    run = box((R1, R2_868, R3), procs={4100: _kiss_on("r3")}, ports=(8267,))
    rc, out, launches = run("start", "kiss@r3")
    assert "readiness evidence is absent" not in out and "already running" in out, out
    assert rc == 0 and not [x for x in launches if x[0] == "loraham-kiss-tnc"], (launches, out)


# ---- 13.5 (CLEANUP-DESIGN-v13): graywolf is the only stack without its own radio ---------------------------------

def _graywolf_box(box, monkeypatch, procs=None):
    """The default list's shape (r1 433, r2 868) with a callsign set; every launch's component, instance and params."""
    from lhpc.core.lifecycle import Lifecycle
    run = box((R1, R2_868), procs=procs, ports=(8134, 8213))
    assert cli.ControllerService().set_operator_identity(callsign="DL1ABC").ok
    seen, fake = [], Lifecycle.start

    def start(self, stack, comp, params=None, band="", **kw):
        seen.append((comp, params, kw.get("inst")))
        return fake(self, stack, comp, params, band, **kw)
    monkeypatch.setattr(Lifecycle, "start", start)
    return run, seen


def test_graywolf_at_r2_starts_kiss_at_r2_and_r2s_daemon(box, monkeypatch):
    # The cross-stack dependency takes the dependent's radio: graywolf@r2 starts r2's daemon and kiss@r2, then itself.
    run, _seen = _graywolf_box(box, monkeypatch)
    _rc, out, launches = run("start", "graywolf@r2")
    assert launches == [("loraham-daemon", "868", "r2"), ("loraham-kiss-tnc", "868", "r2"),
                        ("graywolf", "868", "r2")], (launches, out)


def test_graywolfs_kiss_port_is_kiss_at_its_own_radios(box, monkeypatch):
    # {port_of:kiss:8001} is kiss@r2's port (8001 + 133), and graywolf@r2's web UI is r2's (8080 + 133): no picking.
    run, seen = _graywolf_box(box, monkeypatch)
    run("start", "graywolf@r2")
    comp, params, inst = next(s for s in seen if s[0].id == "graywolf")
    op = cli.ControllerService().config().operator
    step = next(st["argv"] for st in comp.post_steps if st.get("kind") == "exec")       # the provisioning: graywolf's TNC
    tnc = commands.expand_argv([t for t in step if t in ("{param:tnc_host}", "{param:tnc_port}")], comp, params, op,
                               "/rt", "/src", "868", inst=inst)
    assert (inst.rid, tnc) == ("r2", ["--tnc-host", "127.0.0.1", "--tnc-port", "8134"]), tnc
    assert "127.0.0.1:8213" in commands.expand_argv(comp.run_argv, comp, params, op, "/rt", "/src", "868", inst=inst)


def test_graywolf_at_r2_leaves_a_running_kiss_at_r1_alone(box, monkeypatch):
    run, _seen = _graywolf_box(box, monkeypatch, procs={4100: _kiss_on("r1")})
    _rc, out, launches = run("start", "graywolf@r2")
    assert [x for x in launches if x[0] == "loraham-kiss-tnc"] == [("loraham-kiss-tnc", "868", "r2")], (launches, out)
