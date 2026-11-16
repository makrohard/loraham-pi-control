"""A component run by an interpreter is identified by the program the interpreter runs (`process.entry`), compared
whole, and, where its interpreter is an absolute path of this install, by that interpreter (`process.exec_path`,
argv[0] compared whole). A pytest, linter or editor run that only NAMES the module or script path, or another app or
venv running the same program, is not the component. Before the fix a substring of any argv token sufficed: a lab
reset aborted on a local pytest run whose argv named lhpc/data/meshcore_host ("a matching process is running but not
owned by LHPC — stop it yourself: kill <pytest>"), status read that pytest as a running meshcore-node, and a bare
`lhpc kill meshcore` named it. Boundary: one LHPC install per box; components whose argv[0] is relative or bare
(meshcore-node, meshchat, meshcom-gps-relay) are identified by `entry` alone."""
import pytest

from lhpc.core import kill
from lhpc.core.manifest import ManifestError, parse_manifest
from lhpc.core.model import RunState
from lhpc.core.outcomes import Outcome
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService

PY = "/home/u/.venv/bin/python"
PY3 = "/home/u/.venv/bin/python3"
OWN = "{exec_path}"        # this install's resolved interpreter (the spec's exec_path)

# (id, component, its stack, an argv that is NOT the component, the component's real argv)
CASES = [
    ("meshcore-node", "meshcore-node", "meshcore",
     [PY, "-m", "pytest", "-q", "--ignore=lhpc/data/meshcore_host", "tests"],
     [".venv/bin/python", "-m", "meshcore_host", "/r/config/files/meshcore.toml"]),
    ("meshtastic-gps", "meshtastic-gps", "meshtastic",
     [PY3, "-m", "pytest", "-q", "tests/stacks", "-k", "_gps-bridge and meshtastic"],
     [OWN, "-m", "lhpc.adapters.cli.main", "_gps-bridge", "meshtastic", "--state", "/r/state/gps/meshtastic"]),
    ("meshtastic-gps-other-venv", "meshtastic-gps", "meshtastic",
     ["/opt/other/venv/bin/python3", "-m", "lhpc.adapters.cli.main", "_gps-bridge", "meshtastic", "--state", "/s"],
     [OWN, "-m", "lhpc.adapters.cli.main", "_gps-bridge", "meshtastic", "--state", "/r/state/gps/meshtastic"]),
    ("meshcom-gps", "meshcom-gps", "meshcom",
     [PY3, "-m", "pytest", "-q", "tests/stacks", "-k", "_gps-bridge and meshcom"],
     [OWN, "-m", "lhpc.adapters.cli.main", "_gps-bridge", "meshcom", "--state", "/r/state/gps/meshcom"]),
    ("meshcore-gps", "meshcore-gps", "meshcore",
     [PY3, "-m", "pytest", "-q", "tests/stacks", "-k", "_gps-bridge and meshcore"],
     [OWN, "-m", "lhpc.adapters.cli.main", "_gps-bridge", "meshcore", "--state", "/r/state/gps/meshcore"]),
    ("meshcom-gps-relay", "meshcom-gps-relay", "meshcom",
     ["python3", "-m", "pyflakes", "scripts/gps-relay.py"],
     ["python3", "scripts/gps-relay.py", "--mode", "fixture", "--uart", ".run/gps-uart1.sock"]),
    ("meshcore-webui", "meshcore-webui", "meshcore",
     [PY, "-m", "pytest", "-q", "-k", "uvicorn and app.main:app"],
     [OWN, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8788"]),
    ("meshcore-webui-other-app", "meshcore-webui", "meshcore",
     ["/home/u/other-app/.venv/bin/python", "-m", "uvicorn", "app.main:app", "--port", "8000"],
     [OWN, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8788"]),
    ("meshcore-webui-of-another-install", "meshcore-webui", "meshcore",   # G1 round 1's scenario
     ["/opt/other/src/meshcore-webui/backend/.venv/bin/python", "-m", "uvicorn", "app.main:app", "--port", "9000"],
     [OWN, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8788"]),
    ("meshcore-webui-path-in-an-argument", "meshcore-webui", "meshcore",
     ["/home/u/other-app/.venv/bin/python", "-m", "uvicorn", "app.main:app",
      "--root-path", "/r/src/meshcore-webui/backend/.venv/bin/python"],
     [OWN, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8788"]),
    ("meshchat", "meshchat", "reticulum",
     [PY, "-m", "pytest", "-q", "tests/test_meshchat.py"],
     [".venv/bin/python", "meshchat.py", "--headless", "--host", "127.0.0.1", "--port", "8790"]),
]
IDS = [c[0] for c in CASES]


def _svc(tmp_path, argv):
    return ControllerService(system=FakeSystem(cmdlines_data={4242: argv}).system, paths=Paths(runtime_root=tmp_path))


def _own(tmp_path, comp, argv):
    """`argv` with OWN replaced by this install's resolved interpreter for `comp`."""
    spec = next(c for s in _svc(tmp_path, []).stacks() for c in s.components if c.id == comp).process
    return [spec.exec_path if a == OWN else a for a in argv]


def _seen(svc, stack, comp):
    """(the stop's outcome, the snapshot's run state, whether a bare kill names pid 4242) for `comp`."""
    cr = next(r for r in svc.stop(stack, apply=True).results if r.component == comp)
    state = next(s for s in svc.build_snapshot().stacks if s.stack.id == stack).components[comp].run_state
    plan = kill.kill(svc, stack, comp)                        # the dry run: names the processes, signals none
    # a candidate is a target or, its /proc identity unreadable here (a fake pid), an unresolved one
    named = 4242 in plan.data["pids"] or any(u.startswith("pid 4242 ") for u in plan.data.get("unresolved", []))
    return cr, state, named


@pytest.mark.contract
@pytest.mark.parametrize("cid,comp,stack,other,real", CASES, ids=IDS)
def test_a_process_that_is_not_the_component_is_not_matched(tmp_path, cid, comp, stack, other, real):
    cr, state, named = _seen(_svc(tmp_path, other), stack, comp)
    assert cr.outcome is Outcome.ALREADY_STOPPED, cr.summary
    assert state not in (RunState.RUNNING, RunState.DEGRADED)
    assert not named


@pytest.mark.parametrize("cid,comp,stack,other,real", CASES, ids=IDS)
def test_control_the_components_own_argv_still_matches(tmp_path, cid, comp, stack, other, real):
    # control (passes before and after the fix): the real argv, unrecorded, is still the component
    cr, state, named = _seen(_svc(tmp_path, _own(tmp_path, comp, real)), stack, comp)
    assert cr.outcome is Outcome.MANUAL_REQUIRED and cr.summary.endswith("kill 4242"), cr.summary
    assert state in (RunState.RUNNING, RunState.DEGRADED)
    assert named


def test_the_interpreter_is_this_installs(tmp_path):
    svc = _svc(tmp_path, [])
    webui = svc.stack("meshcore").component("meshcore-webui").process
    assert webui.exec_path == f"{tmp_path}/src/meshcore-webui/backend/.venv/bin/python"


@pytest.mark.contract
@pytest.mark.parametrize("process,msg", [
    ({"exec_name": "python3", "entry": ["meshcore_host"]}, "process.entry must be a string"),
    ({"exec_name": "python3", "exec_path": "/usr/bin/python3"}, "process.exec_path must be"),
    ({"exec_name": "python3", "exec_path": "{runtime}/x/python3"}, "process.exec_path must be"),
], ids=["entry-not-a-string", "exec_path-absolute-literal", "exec_path-other-template"])
def test_a_malformed_process_identity_is_refused(process, msg):
    comp = {"id": "a", "name": "a", "process": process}
    with pytest.raises(ManifestError, match=msg):
        parse_manifest({"stack": [{"id": "s", "component": [comp], "main": "a"}]})
