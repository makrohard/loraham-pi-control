"""G2 B1 (RADIO-POWER item 4): the MeshCom bridge's TX power ceiling (`--max-power {daemon:power}`, meshcom-loraham-bridge
0.3.0) is the POWER the instance applies to its daemon, the value the node's `--txpower` takes too.

- P1: `{daemon:power}` renders only with that value; without it the launch is refused (no bridge, no radio), and the
  stack start hands every component its instance's value.
- P2: the ceiling is fixed at the bridge's start. While a stack with one runs on that radio, a higher POWER marks the
  instance restart-required, and a lower one is refused (the old, higher ceiling would stay on air until a restart);
  the radio row's POWER the same when the instance inherits it.
- P4: `lhpc status` and the Apps row name "NO RADIO: power above panel" from the bridge's own log (its last configured
  or refused XR connection since its start), never by inference."""
import dataclasses
import re

import pytest
from radios_fixture import R1

from lhpc.adapters.cli import main as cli
from lhpc.core import commands, radio_start, runtime_fs
from lhpc.core.lifecycle import Lifecycle


class _Op:
    callsign = "DL1ABC"


class _C:
    run_params = []


@pytest.mark.contract
@pytest.mark.parametrize("daemon", [None, {}, {"power": ""}, {"power": "1 2"}, {"power": "abc"}, {"power": "123"}],
                         ids=["none", "empty", "blank", "two-tokens", "not-a-number", "three-digits"])
def test_a_run_line_with_the_ceiling_renders_only_with_the_instances_power(daemon):
    argv = ["build/meshcom-loraham-bridge", "--max-power", "{daemon:power}"]
    with pytest.raises(commands.CommandError, match=r"no \{daemon:power\} value for this instance"):
        commands.expand_argv(argv, _C(), {}, _Op(), "/rt", "/src", daemon=daemon)
    assert commands.expand_argv(argv, _C(), {}, _Op(), "/rt", "/src", daemon={"power": "14"})[-2:] == \
        ["--max-power", "14"]
    # the dashboard's copy-paste command is no command without it, never one without the ceiling
    comp = dataclasses.replace(next(c for s in cli.ControllerService().stacks() for c in s.components
                                    if c.id == "meshcom-bridge"), run_argv=tuple(argv))
    assert commands.display_command(comp, _Op(), "/rt", "/src", daemon=daemon) == ""


def _meshcom_box(box, monkeypatch, row_dp=()):
    from lhpc.core.services import ControllerService
    run = box((dataclasses.replace(R1, dp=tuple(row_dp)),), ports=(18083, 12323, 7000))
    monkeypatch.setattr(ControllerService, "start_blocking_requirements", lambda self, comp: [])   # a built box
    svc = cli.ControllerService()
    assert svc.set_operator_identity(callsign="DL1ABC").ok
    seen = []
    fake = Lifecycle.start

    def start(self, stack, comp, params=None, band="", **kw):
        seen.append((comp.id, (kw.get("inst") or commands.TEMPLATE).rid, kw.get("daemon")))
        return fake(self, stack, comp, params, band, **kw)

    def post(self, stack, comp, comp_cfg, band="", inst=None, **kw):
        from lhpc.core.jobs import JobResult, JobState
        return JobResult(name=f"post-{comp.id}", state=JobState.SUCCEEDED, returncode=0, log_path="")
    monkeypatch.setattr(Lifecycle, "start", start)
    monkeypatch.setattr(Lifecycle, "run_required_post_start", post)
    return run, seen


@pytest.mark.parametrize("row_dp, own, want", [((), None, None), ((("POWER", "12"),), None, "12"),
                                               ((("POWER", "12"),), "11", "11")],
                         ids=["default", "row", "own-over-row"])
def test_the_stack_start_hands_each_component_its_instances_power(box, monkeypatch, row_dp, own, want):
    run, seen = _meshcom_box(box, monkeypatch, row_dp)
    svc = cli.ControllerService()
    if own is not None:
        assert svc.save_daemon_params("meshcom", {"POWER": own}, rid="r1").ok
    want = want or svc._daemon_param_applies("meshcom", "r1")["POWER"]
    rc, out, _launches = run("start", "meshcom@r1")
    clients = [s for s in seen if s[0] != "loraham-daemon"]
    assert clients and all(s[1:] == ("r1", {"power": want}) for s in clients), (seen, out)
    assert {"meshcom-bridge", "meshcom-qemu"} <= {s[0] for s in clients}, seen


def _running(monkeypatch, sids):
    monkeypatch.setattr(radio_start, "radio_dependents", lambda svc, band, inst=None: [f"{s}@r1" for s in sids])


def _with_ceiling(monkeypatch, svc):
    """The bridge's run line as the pin commit ships it (`--max-power {daemon:power}`): the manifest's own now."""
    if any("{daemon:power}" in c.run_argv for s in svc.stacks() for c in s.components if c.id == "meshcom-bridge"):
        return
    stacks = []
    for st in svc.stacks():
        comps = tuple(dataclasses.replace(c, run_argv=(*c.run_argv, "--max-power", "{daemon:power}"))
                      if c.id == "meshcom-bridge" else c for c in st.components)
        stacks.append(dataclasses.replace(st, components=comps))
    monkeypatch.setattr(type(svc), "stacks", lambda self: tuple(stacks))
    monkeypatch.setattr(type(svc), "stack", lambda self, sid: next((s for s in stacks if s.id == sid), None))


def test_a_power_saved_while_the_ceiling_runs_is_raised_with_a_restart_or_refused_when_lower(box, monkeypatch):
    box((R1,))
    svc = cli.ControllerService()
    _with_ceiling(monkeypatch, svc)
    assert radio_start.running_ceiling_stacks(svc, "r1") == []
    assert svc.save_daemon_params("meshcom", {"POWER": "11"}, rid="r1").ok
    assert svc.restart_required("meshcom@r1") is None                   # not running: the next start takes it
    _running(monkeypatch, ["meshcom", "kiss"])
    assert radio_start.running_ceiling_stacks(svc, "r1") == ["meshcom"]  # kiss's launch takes no ceiling
    assert svc.save_daemon_params("meshcom", {"POWER": "11"}, rid="r1").ok
    assert svc.restart_required("meshcom@r1") is None                   # unchanged: nothing to restart for
    low = svc.save_daemon_params("meshcom", {"POWER": "9"}, rid="r1")
    assert not low.ok and ("meshcom@r1 runs with its TX power ceiling at 11 dBm, and a lower POWER takes effect only "
                           "with a restart: stop it (lhpc stack stop meshcom@r1), save, then start it") in low.summary
    assert svc._daemon_param_applies("meshcom", "r1")["POWER"] == "11"   # nothing saved
    assert svc.save_daemon_params("meshcom", {"POWER": "13"}, rid="r1").ok
    marker = svc.restart_required("meshcom@r1")
    assert marker and "dp_POWER" in str(marker), marker
    assert svc.restart_required("kiss@r1") is None


@pytest.mark.parametrize("own, power, ok, marked", [(None, "8", False, False), (None, "15", True, True),
                                                     ("11", "8", True, False)],
                         ids=["inherits-lower", "inherits-higher", "has-its-own"])
def test_the_rows_power_under_a_running_ceiling_that_inherits_it(box, monkeypatch, own, power, ok, marked):
    box((dataclasses.replace(R1, dp=(("POWER", "12"),)),))
    svc = cli.ControllerService()
    _with_ceiling(monkeypatch, svc)
    if own is not None:
        assert svc.save_daemon_params("meshcom", {"POWER": own}, rid="r1").ok
    _running(monkeypatch, ["meshcom"])
    res = radio_start.set_row_dp(svc, "r1", {"POWER": power})
    assert res.ok is ok, res.summary
    assert (svc.restart_required("meshcom@r1") is not None) is marked
    assert radio_start.row_dp(svc, "r1")["POWER"] == (power if ok else "12")


_LOG = """2026-11-17T20:00:00.000 meshcom-loraham-bridge: listening on 127.0.0.1:7000, auth=password, backend=loraham, \
max-power=10, rflog=off
2026-11-17T20:00:05.000 [bridge] XR client connected
{body}"""
_REFUSED = """2026-11-17T20:00:05.100 [bridge] XR CONFIGURE refused: power 14 dBm is above --max-power 10 dBm
2026-11-17T20:00:05.101 [bridge] XR client disconnected: config-failed
"""
_CONFIGURED = "2026-11-17T20:01:00.000 [bridge] XR configured: power 10 dBm (--max-power 10 dBm)\n"


@pytest.mark.parametrize("body, shown", [
    (_REFUSED, True),                                   # refused last: no radio
    (_REFUSED + _CONFIGURED, False),                    # the node lowered its power: its radio is back
    (_CONFIGURED, False),                               # configured
    ("", False),                                        # no XR connection yet
], ids=["refused", "refused-then-configured", "configured", "no-connection"])
def test_status_names_no_radio_from_the_bridges_own_log(box, monkeypatch, body, shown):
    box((R1,))
    svc = cli.ControllerService()
    _with_ceiling(monkeypatch, svc)
    life = svc._lifecycle()
    log = life.logs_dir() / radio_start.start_log_name("meshcom-bridge", "433", "r1")
    runtime_fs.ensure_dir(svc._paths, life.logs_dir())
    log.write_text(_LOG.format(body=body))
    monkeypatch.setattr(Lifecycle, "owned_records", lambda self, comp_id, band=None, role="", rid=None:
                        [{"rid": "r1", "band": "433"}] if comp_id == "meshcom-bridge" and role == "" else [])
    monkeypatch.setattr(Lifecycle, "_original_ceased", lambda self, rec: False)
    lines = radio_start.ceiling_outcomes(svc, "meshcom-bridge")
    assert svc._post_start_outcomes("meshcom-bridge") == lines      # G2-D N3: the status path `lhpc status` reads
    want = ("radio: NO RADIO: power above panel — the node asks 14 dBm, the panel's ceiling is 10 dBm (the bridge "
            "refused it at 2026-11-17T20:00:05); set the node's power with: lhpc stack poststart meshcom@r1")
    assert lines == ([want] if shown else []), lines
    assert radio_start.ceiling_outcomes(svc, "meshcom-qemu") == []           # only a component launched with the ceiling
    # a restart starts over: an old run's refusal before the new start's line is history
    log.write_text(_LOG.format(body=_REFUSED) + _LOG.format(body=""))
    assert radio_start.ceiling_outcomes(svc, "meshcom-bridge") == []


def test_the_dashboards_bridge_command_carries_the_instances_ceiling(box, monkeypatch):
    # the copy-paste command a page shows for meshcom-bridge@r1 is the one a start runs: with --max-power and the
    # instance's POWER, never one without the ceiling
    box((dataclasses.replace(R1, dp=(("POWER", "12"),)),))
    svc = cli.ControllerService()
    _with_ceiling(monkeypatch, svc)
    comp = next(c for c in svc.stack("meshcom").components if c.id == "meshcom-bridge")
    cmd = svc.manual_start_command(comp, commands.Instance("r1", band="433"))
    assert "--max-power 12" in cmd, cmd


def test_the_bridge_is_launched_with_the_instances_power_as_its_ceiling(box):
    # the pin commit: the bridge at 0.3.0 (the release whose --max-power is required) and its run line, together, so
    # the two never run apart; its argv for meshcom@r1 carries the POWER the node's --txpower takes
    box((dataclasses.replace(R1, dp=(("POWER", "12"),)),))
    svc = cli.ControllerService()
    comp = next(c for c in svc.stack("meshcom").components if c.id == "meshcom-bridge")
    # the contract, not the pin: a full commit and a release at or above 0.3.0, whose --max-power is required
    assert re.fullmatch(r"[0-9a-f]{40}", comp.source.pin_commit), comp.source.pin_commit
    assert tuple(int(x) for x in re.fullmatch(r"v(\d+)\.(\d+)\.(\d+)", comp.source.pin_tag).groups()) >= (0, 3, 0)
    r1 = commands.Instance("r1", band="433")
    daemon = radio_start.daemon_values(svc, "meshcom", r1)
    argv = commands.expand_argv(comp.run_argv, comp, svc.stack_config(comp.id, "433", r1), _Op(), "/rt", "/src",
                                "433", inst=r1, daemon=daemon)
    assert daemon == {"power": "12"} and argv[argv.index("--max-power") + 1] == "12", argv
    assert comp.run_argv.count("{daemon:power}") == 1
