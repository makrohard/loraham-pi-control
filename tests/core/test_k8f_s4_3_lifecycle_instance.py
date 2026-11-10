"""K8f S4-3 (K8F-S4-DESIGN-v4 S4b/S4c rows in service_lifecycle_ops): a start, a post-start re-run and the post-start
outcome lines are about the instance the operator named or the instance that runs, never the fallback radio of the
band. Two radios on 433 (r1, r2)."""
import json
import subprocess
import sys

import pytest
import radios_fixture

from lhpc.core import instances
from lhpc.core import runtime_fs
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService


def _svc(tmp_path, r2=radios_fixture.R2):
    radios_fixture.write(tmp_path, (radios_fixture.R1, r2))          # r1 on 433, r2 on 433 unless named
    return ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))


def test_a_poststart_judges_the_identity_of_its_own_instance(tmp_path):
    # meshcom@r1 has its own callsign, meshcom@r2 none (no global call): a post-start re-run of meshcom@r2 is refused on
    # r2's identity with r2's remedy; meshcom@r1 passes the identity gate. red on f6c9fe2c: both judged by the band's
    # fallback instance (r1), so meshcom@r2 passed the gate
    svc = _svc(tmp_path)
    r1 = instances.parse_target(svc, "meshcom@r1", "start")[1]
    r2 = instances.parse_target(svc, "meshcom@r2", "start")[1]
    assert svc.save_config_bundle("meshcom", values={"mc_callsign": "DL9XYZ-1"}, inst=r1).ok
    res2 = svc.poststart("meshcom", apply=True, inst=r2)
    assert not res2.ok and "a callsign is required" in res2.summary, res2.summary
    assert (res2.data or {}).get("enforce_fields") == ["c_mc_callsign"]
    assert res2.next_commands and res2.next_commands[0].split()[:3] == ["lhpc", "config", "meshcom@r2"], res2.next_commands
    res1 = svc.poststart("meshcom", apply=True, inst=r1)
    assert "a callsign is required" not in res1.summary, res1.summary
    assert any("start it first (lhpc stack start meshcom@r1)" in d for d in res1.details), res1.details   # its own


def test_a_terminal_client_start_names_the_stack_instance_of_its_radio(tmp_path):
    # a direct start of voice's terminal client on r2 is refused (only a stack start generates its config); the
    # remedy is that radio's stack start. red on f6c9fe2c: "voice@<rid>" (two radios able, the instance not passed)
    svc = _svc(tmp_path)
    target, inst = instances.parse_target(svc, "loraham-voice-cli@r2", "start")[:2]
    for apply in (False, True):
        res = svc.start(target, apply=apply, inst=inst)
        assert not res.ok and res.next_commands == ["lhpc stack start voice@r2"], (apply, res.next_commands)


def _process():
    """A REAL process (start_new_session) past its exec when this returns (it said so), running until its stdin
    closes: no clock, no polling."""
    p = subprocess.Popen([sys.executable, "-c", "import sys; print('up', flush=True); sys.stdin.read()"],
                         stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, start_new_session=True)
    assert p.stdout.readline() == "up\n"
    return p


def _end(p):
    p.stdin.close(); p.wait(); p.stdout.close()


def _live_main(life, stack, comp, rid, band):
    """A REAL running main of `comp` on radio `rid` (`band`), recorded as a start records it."""
    p = _process()
    assert life.record_launch(stack, comp, p.pid, band, ident=life._capture_identity(p.pid), role="",
                              extra={"rid": rid})
    return p


def _ceased_post_record(life, stack, comp, band, binding, result):
    """A detached post-runner record bound to `binding` whose runner has exited (its outcome is final)."""
    p = _process()
    assert life.record_launch(stack, comp, p.pid, band, ident=life._capture_identity(p.pid), role="post",
                              binding=binding,
                              extra={"post_uid": f"u{p.pid}", "result_path": str(result), "log_path": str(result)})
    _end(p)


def _steps(path, label, outcome):
    runtime_fs.ensure_dir(Paths(runtime_root=path.parents[2]), path.parent)
    path.write_text(json.dumps({"steps": [{"kind": "exec", "label": label, "outcome": outcome, "rc": 0,
                                           "attempts": 3, "elapsed_s": 9.0}]}))


@pytest.mark.needs_session
@pytest.mark.parametrize("r2", [radios_fixture.R2, radios_fixture.R2_868], ids=["one-band", "two-bands"])
def test_post_start_outcomes_of_two_instances_are_each_their_own(tmp_path, set_call, r2):
    # meshtastic runs on r1 (433) and on r2 (433, or 868), each with its own verified main, a required (synchronous)
    # outcome and a detached outcome (r1's required step applied, r2's failed): both instances' outcomes are shown,
    # each re-apply naming its own instance; on two bands each block is read on its instance's band. red on
    # f6c9fe2c: one binding of any radio (rid None) judged both, so r2's required block and r2's detached outcome
    # (bound to r2's main) were dropped, and the re-apply line read "meshtastic@<rid>"
    svc = _svc(tmp_path, r2)
    set_call(svc)
    life = svc._lifecycle()
    stack = svc.stack("meshtastic")
    comp = stack.component("meshtastic")
    bands = {"r1": "433", "r2": r2.band}
    procs = [_live_main(life, stack, comp, rid, bands[rid]) for rid in ("r1", "r2")]
    try:
        for rid in ("r1", "r2"):
            binding = life._binding_for(comp.id, bands[rid], instances.copy_of(svc, "meshtastic", rid)[0])
            assert binding is not None, rid
            req = life.paths.under("state", "post", life.required_result_leaf(binding))
            runtime_fs.ensure_dir(life.paths, req.parent)
            req.write_text(json.dumps({"v": 1, "meta": {"comp": comp.id, "band": bands[rid], "role": "required"},
                                       "binding": binding, "done": True,
                                       "steps": [{"kind": "exec", "label": f"region-{rid}",
                                                  "outcome": "ok" if rid == "r1" else "failed",
                                                  "rc": 0, "attempts": 1, "elapsed_s": 1.0}]}))
            det = life.paths.under("state", "post", f"detached-{rid}.json")
            _steps(det, f"owner-{rid}", "exhausted")
            _ceased_post_record(life, stack, comp, bands[rid], binding, det)
        lines = svc._post_start_outcomes(comp.id)
        region = [ln for ln in lines if "region-" in ln]
        assert len(region) == 2 and region[0] == "post-start: region-r1 applied after 1.0s", lines
        assert region[1] == ("post-start: region-r2 FAILED (exit 0) after 1.0s — see the post-start log; re-apply with: "
                             "lhpc stack poststart meshtastic@r2"), lines
        for rid in ("r1", "r2"):
            assert any(f"owner-{rid} NOT applied" in ln and ln.endswith(f"lhpc stack poststart meshtastic@{rid}")
                       for ln in lines), (rid, lines)
    finally:
        for p in procs:
            _end(p)
