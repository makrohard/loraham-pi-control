"""K8f S4-2 re-round (audits/VERDICT-G1-K8F-S4-2.md df2925f5): the RF-log viewer path decodes a radio's log in that
radio's mode. Two radios on 868 (r3, r2)."""
import sys

import radios_fixture

from lhpc.core import instances, rflog
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService

LINE = '2026-09-12T18:36:49.968Z RX rssi=-67.00 snr=11.25 len=4 hex=01020304 ascii="...."'


def _box(tmp_path):
    radios_fixture.write(tmp_path, (radios_fixture.R3, radios_fixture.R2_868))
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    return svc, {r: instances.parse_target(svc, f"meshcore@{r}", "start")[1] for r in ("r2", "r3")}


def test_the_viewer_decodes_a_radios_log_in_its_mode_and_resets_once_when_it_moves(tmp_path, monkeypatch):
    # B2 note: r2 runs as a repeater (generated config) with its saved mode back at chat; its job's cache follows r2's
    # mode and resets once when r2 stops (repeater -> saved chat). red on 99da4eb1's base f6c9fe2c: the stack-wide mode
    svc, _ins = _box(tmp_path)
    gen = tmp_path / "config" / "files" / "r2" / "meshcore.toml"
    gen.parent.mkdir(parents=True, exist_ok=True)
    gen.write_text('[repeater]\nrole = "repeater"\n')
    live = {"r2": True}
    monkeypatch.setattr(instances, "instance_runs", lambda s, comp, inst, band: live.get(getattr(inst, "rid", ""), False))
    runs = tmp_path / "runs"
    script = tmp_path / "fake_decoder.py"
    script.write_text("import json, sys\nreqs = [json.loads(l) for l in sys.stdin if l.strip()]\n"
                      f"open({str(runs)!r}, 'a').write(str(len(reqs)) + '\\n')\n"
                      "for r in reqs:\n    print(json.dumps({'key': r['key'], 'status': 'ok', 'kind': 'text', "
                      "'peer': 'p', 'decoded': 'hi'}))\n")
    monkeypatch.setattr(ControllerService, "_rflog_decoder_argv", lambda self, e, rid="": ((sys.executable, str(script)), ""))
    recs = [rflog.parse_line(f"{LINE[:-1]}{i}\"") for i in range(2)]
    svc.rflog_decode("meshcore-node", "r2/rf-meshcore.log", recs)
    svc.rflog_decode("meshcore-node", "r2/rf-meshcore.log", recs)
    assert runs.read_text().splitlines() == ["2"]                           # cached under r2's running repeater
    live["r2"] = False                                                      # r2 stopped: its saved chat applies
    svc.rflog_decode("meshcore-node", "r2/rf-meshcore.log", recs)
    assert runs.read_text().splitlines() == ["2", "2"]                      # the mode moved: reset once

