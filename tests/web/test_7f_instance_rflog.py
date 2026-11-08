"""Item 7f: a listed instance's own RF log `logs/<rid>/rf-<x>.log` (its writer's {instance_dir}) opens through the
web routes like a radio's daemon log: the page and the records API serve it, the log page's stack row names it,
its badge is the instance's run state, and its MeshCore records are decoded with the instance's own mode, stores
and secrets, never slot 0's. Any other path or writer stays refused."""
import pytest
import radios_fixture

from lhpc.core.commands import Instance
from lhpc.core import config as cfgmod
from lhpc.core import instances, radios
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService

LINE = "2026-09-12T16:01:00.000Z TX rssi=- snr=- len=3 outcome=ok hex=112233"
ROWS = (radios.Radio("r2", "a", "uputronics", "868", ce="ce0"), radios.Radio("r4", "b", "uputronics", "868", ce="ce1"))
JOB = "r4/rf-meshcore.log"


def _seed(tmp_path):
    radios_fixture.write(tmp_path, ROWS)
    (tmp_path / "logs" / "r4").mkdir(parents=True)
    (tmp_path / "logs" / "r4" / "rf-meshcore.log").write_text(LINE + "\n")


def test_an_instances_log_records_come_from_its_file(web, tmp_path):
    _seed(tmp_path)
    r = web().get(f"/api/rflog/meshcore-node?job={JOB}")
    assert r.status_code == 200
    data = r.get_json()
    assert (data["job"], data["path"]) == (JOB, str(tmp_path / "logs" / "r4" / "rf-meshcore.log"))
    assert len(data["records"]) == 1


def test_the_log_page_shows_the_instances_file_and_names_it_in_its_stack_row(web, tmp_path):
    _seed(tmp_path)
    page = web().get(f"/logs/meshcore-node?job={JOB}&band=868").get_data(as_text=True)
    assert f'data-job="{JOB}"' in page and str(tmp_path / "logs" / "r4" / "rf-meshcore.log") in page
    row = page[page.index('class="rfswitch"'):]
    row = row[:row.index("</nav>")]                                       # the log page's stack row alone
    assert 'href="/logs/meshcore-node?job=r4/rf-meshcore.log&amp;band=868" aria-current="page">meshcore r4</a>' in row
    assert 'href="/logs/meshcore-node?job=rf-meshcore.log&amp;band=868">meshcore</a>' in row   # slot 0's row stays


@pytest.mark.parametrize("target,job", [("loraham-kiss-tnc", JOB), ("meshcom-bridge", "r4/rf-meshcom.log"),
                                        ("meshcore-node", "r4/rf-made-up.log"), ("meshcore-node", "r4/../rf-meshcore.log")])
def test_another_writer_or_file_is_refused(web, tmp_path, target, job):
    _seed(tmp_path)
    assert web().get(f"/api/rflog/{target}?job={job}").status_code == 404


def test_an_instances_records_are_decoded_with_its_own_mode_stores_and_secrets(tmp_path, monkeypatch):
    _seed(tmp_path)
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    cfgmod.update_stack_config(svc._paths, "meshcore", {"file_mode": "repeater", "file_repeater_name": "R4"}, inst=Instance(1, "r4"))
    svc._invalidate_config()
    py = tmp_path / "src" / "openhop-core" / ".venv" / "bin" / "python"   # the stack's interpreter (built)
    py.parent.mkdir(parents=True)
    py.write_text("")
    py.chmod(0o755)
    seen = []
    monkeypatch.setattr(ControllerService, "_rflog_run", lambda self, argv, misses: (seen.append(list(argv)), ({}, ""))[1])
    _path, records = svc.rflog_records("meshcore-node", JOB)
    svc.rflog_decode("meshcore-node", JOB, records)
    [argv] = seen
    opt = dict(zip(argv, argv[1:]))
    assert opt["--mode"] == "repeater"
    assert opt["--secrets"] == str(tmp_path / "config" / "secrets" / "r4")
    assert opt["--store"] == str(tmp_path / "state" / "r4" / "meshcore" / "companion.db")
    assert opt["--repeater-store"] == str(tmp_path / "state" / "r4" / "openhop")


def test_an_instances_badge_is_the_instances_run_state(tmp_path, monkeypatch):
    _seed(tmp_path)
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    monkeypatch.setattr(instances, "instance_runs", lambda svc, comp, inst, band: inst.rid == "r4" and band == "868")
    assert svc.rflog_running(JOB) is True
    assert svc.rflog_running("rf-meshcore.log") is False                  # slot 0 does not run


@pytest.mark.parametrize("job,surface", [("r4/rf-meshcore.log", "meshcore"), ("r1/rf-kiss.log", "graywolf"),
                                         ("r2/rf-daemon.log", "daemon")])
def test_a_radios_own_log_names_its_writers_entry_and_radio(job, surface):
    from lhpc.core import rflog
    e, rid = rflog.by_job(job)
    assert (e.surface, rid) == (surface, job.partition("/")[0])
