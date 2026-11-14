"""A one-band stack (no component declares `bands`; meshcom 433, meshcore 868, chat 433) writes launch records with no
band, because it has no band to choose. Such a record means the stack's one band: a running instance of it is not "launched on an
unknown band" (the CLI config and the web component start), and boot restore brings it back on that band. A stack that
declares `bands`, even one, still reads a band-less record as no band evidence."""
from __future__ import annotations

import dataclasses
import json
import subprocess
import sys

import pytest
import radios_fixture

from lhpc.adapters.cli import main as cli
from lhpc.core import boot_restore, instances
from lhpc.core import service_boot_restore as sbr
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.service_base import ActionResult
from lhpc.core.services import ControllerService

pytestmark = pytest.mark.needs_session

UNKNOWN = "launched on an unknown band"


def _svc(tmp_path):
    return ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))


def _spawn():
    return subprocess.Popen([sys.executable, "-c", "import signal; signal.pause()"], start_new_session=True)


def _run_as_the_start_path_records(tmp_path, sid, comp_id, rid):
    """A live process with the launch record a start writes for `sid@rid`: the band is the start path's own value."""
    radios_fixture.write(tmp_path)
    svc = _svc(tmp_path)
    stack = svc.stack(sid)
    inst = instances.copy_of(svc, sid, rid)[0]
    band = svc._config_band(sid, "", inst)
    p = _spawn()
    assert svc._lifecycle().record_launch(stack, stack.component(comp_id), p.pid, band=band, extra={"rid": rid})
    return p, band


def _stop(p):
    p.kill()
    p.wait(timeout=5)


def test_a_running_one_band_instance_lists_its_settings_from_the_cli(tmp_path, monkeypatch, capsys):
    p, band = _run_as_the_start_path_records(tmp_path, "meshcom", "meshcom-qemu", "r1")
    try:
        assert band == ""                                                     # what the start path writes today
        monkeypatch.setattr(cli, "ControllerService", lambda *a, **k: _svc(tmp_path))
        rc = cli.main(["config", "meshcom@r1", "list"])
        out = capsys.readouterr().out
        assert rc == 0 and UNKNOWN not in out, out
        assert instances.recorded_bands(_svc(tmp_path), "meshcom") == {"r1": "433"}
    finally:
        _stop(p)


def test_a_component_of_a_running_one_band_instance_starts_from_the_web(tmp_path, monkeypatch, web, csrf):
    p, _band = _run_as_the_start_path_records(tmp_path, "meshcore", "meshcore-node", "r2")
    spawns = []

    def spy(self, op, target, band="", stop_owners=False, cascade=False, rid="", plan_hash=None):
        spawns.append((op, target, rid))
        return f"web-{op}-{target}-{rid}.log", "admitted", ""
    monkeypatch.setattr(ControllerService, "spawn_start_job", spy)
    monkeypatch.setattr(ControllerService, "run_action", lambda self, op, target, apply=False, **k: ActionResult(True, "plan"))
    try:
        c = web()
        r = c.post("/action", data={"_csrf": csrf(c), "op": "start", "target": "meshcore-webui@r2"}, follow_redirects=True)
        assert UNKNOWN not in r.get_data(as_text=True)
        assert spawns == [("start", "meshcore-webui", "r2")], spawns
    finally:
        _stop(p)


def _prev_boot_record(tmp_path, stack, comp, rid, band="", pid=999999):
    """A record from the previous boot, as such a stack's start wrote it (band "")."""
    rec = {"launch_id": f"{comp}__{band or 'x'}__{pid}__{'ab' * 16}", "stack": stack, "component": comp,
           "band": band, "pid": pid, "role": "", "launched_at": 1000, "version": 1, "requested_target": stack,
           "start_scope": "stack", "boot_id": "OLDBOOT", "starttime": "123", "pgid": pid, "sid": pid, "rid": rid}
    d = tmp_path / "state" / "owned"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{rec['launch_id']}.json").write_text(json.dumps(rec))


def _boot(tmp_path, monkeypatch):
    if not (tmp_path / "config" / "radios.toml").exists():
        radios_fixture.write(tmp_path)
    monkeypatch.setattr(sbr, "current_boot_id", lambda: "CURBOOT")
    monkeypatch.setattr(ControllerService, "_web_integration_proven", lambda self: (True, ""))
    monkeypatch.setattr(ControllerService, "boot_restore_enabled", lambda self: (True, ""))
    calls = []

    def start(self, target, apply=False, stop_owners=False, band="", auto_install_ctx=None, *,
              _before_start_locked=None, _operator=True, position=None, position_note="", inst=None):
        calls.append((target, band or getattr(inst, "band", ""), getattr(inst, "rid", "")))
        return ActionResult(True, "started")
    monkeypatch.setattr(ControllerService, "start", start)
    return calls


def test_boot_restore_brings_a_one_band_stack_back_on_its_band(tmp_path, monkeypatch):
    calls = _boot(tmp_path, monkeypatch)
    _prev_boot_record(tmp_path, "meshcom", "meshcom-qemu", "r1")
    res = _svc(tmp_path).boot_restore_run()
    assert calls == [("meshcom", "433", "r1")], (calls, res.summary, res.details)


def test_boot_restore_still_skips_a_two_band_stack_without_a_band(tmp_path, monkeypatch):
    calls = _boot(tmp_path, monkeypatch)
    _prev_boot_record(tmp_path, "meshtastic", "meshtastic", "r2")
    _svc(tmp_path).boot_restore_run()
    skipped = json.loads((tmp_path / "state" / "boot-restore.json").read_text())["skipped"]
    assert calls == [] and "no valid band source" in json.dumps(skipped), (calls, skipped)


def test_a_one_band_stacks_metas_and_a_selectable_one_band_stack(tmp_path, monkeypatch):
    svc = _svc(tmp_path)
    meshcom = svc._boot_stack_metas()["meshcom"]
    assert (meshcom.fixed_band, meshcom.declared_bands) == ("433", ())
    # a stack that DECLARES one band is selectable: a band-less record there is still no band evidence
    st = svc.stack("meshcom")
    one = dataclasses.replace(st, components=tuple(
        dataclasses.replace(c, bands=("433",)) if c.id == st.main else c for c in st.components))
    assert instances.fixed_band(one) == ""
    monkeypatch.setattr(ControllerService, "stacks", lambda self: [one])
    meta = svc._boot_stack_metas()["meshcom"]
    ev = boot_restore.Evidence(launch_id="x", stack="meshcom", component=st.main, band="", launched_at=1.0, rid="r1",
                               start_scope="stack", requested_target="meshcom")
    assert boot_restore._band_candidates(ev, meta, boot_restore.MarkerView()) == \
        ("", "no valid band source for a band-switchable stack")


@pytest.mark.parametrize("ev_band, marker_band", [
    ("", "868"),                                          # a band-less record + a marker naming another band
    ("868", ""),                                          # a record of another band
])
def test_a_one_band_stacks_contradicting_band_evidence_is_refused(tmp_path, ev_band, marker_band):
    meta = _svc(tmp_path)._boot_stack_metas()["meshcom"]
    ev = boot_restore.Evidence(launch_id="x", stack="meshcom", component="meshcom-qemu", band=ev_band,
                               launched_at=1.0, rid="", start_scope="stack", requested_target="meshcom")
    mk = boot_restore.MarkerView(last_start_state="valid", last_start_band=marker_band, last_start_at=1.0) \
        if marker_band else boot_restore.MarkerView()
    band, reason = boot_restore._band_candidates(ev, meta, mk)
    assert band == "" and "868" in reason, (band, reason)


def test_two_records_on_two_bands_still_tell_no_band(tmp_path):
    radios_fixture.write(tmp_path)
    svc = _svc(tmp_path)
    st = svc.stack("meshcom")
    a, b = _spawn(), _spawn()
    try:
        for p, band in ((a, "433"), (b, "868")):
            assert svc._lifecycle().record_launch(st, st.component("meshcom-qemu"), p.pid, band=band,
                                                  extra={"rid": "r1"})
        assert instances.recorded_bands(svc, "meshcom") == {"r1": ""}
    finally:
        _stop(a)
        _stop(b)
