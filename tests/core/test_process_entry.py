"""A component run by an interpreter is identified by the program the interpreter runs (`process.entry`), compared
whole: a pytest, linter or editor run that only NAMES that module or script path is not the component. Before the
fix a substring of any argv token sufficed: a lab reset aborted on a local pytest run whose argv named
lhpc/data/meshcore_host ("a matching process is running but not owned by LHPC — stop it yourself: kill <pytest>"),
and status read that pytest as a running meshcore-node."""
import pytest

from lhpc.core.manifest import ManifestError, parse_manifest
from lhpc.core.model import RunState
from lhpc.core.outcomes import Outcome
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService

PY = "/home/u/.venv/bin/python"
PY3 = "/home/u/.venv/bin/python3"
WEBUI_PY = "/r/src/meshcore-webui/backend/.venv/bin/python"

# (component, its stack, an argv that only names the component's program, the component's real argv)
CASES = [
    ("meshcore-node", "meshcore",
     [PY, "-m", "pytest", "-q", "--ignore=lhpc/data/meshcore_host", "tests"],
     [".venv/bin/python", "-m", "meshcore_host", "/r/config/files/meshcore.toml"]),
    ("meshtastic-gps", "meshtastic",
     [PY3, "-m", "pytest", "-q", "tests/stacks", "-k", "_gps-bridge and meshtastic"],
     [PY3, "-m", "lhpc.adapters.cli.main", "_gps-bridge", "meshtastic", "--state", "/r/state/gps/meshtastic"]),
    ("meshcom-gps", "meshcom",
     [PY3, "-m", "pytest", "-q", "tests/stacks", "-k", "_gps-bridge and meshcom"],
     [PY3, "-m", "lhpc.adapters.cli.main", "_gps-bridge", "meshcom", "--state", "/r/state/gps/meshcom"]),
    ("meshcore-gps", "meshcore",
     [PY3, "-m", "pytest", "-q", "tests/stacks", "-k", "_gps-bridge and meshcore"],
     [PY3, "-m", "lhpc.adapters.cli.main", "_gps-bridge", "meshcore", "--state", "/r/state/gps/meshcore"]),
    ("meshcom-gps-relay", "meshcom",
     ["python3", "-m", "pyflakes", "scripts/gps-relay.py"],
     ["python3", "scripts/gps-relay.py", "--mode", "fixture", "--uart", ".run/gps-uart1.sock"]),
    ("meshcore-webui", "meshcore",
     [PY, "-m", "pytest", "-q", "-k", "uvicorn and app.main:app"],
     [WEBUI_PY, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8788"]),
    ("meshcore-webui", "meshcore",       # another FastAPI app: only its interpreter differs
     ["/home/u/other-app/.venv/bin/python", "-m", "uvicorn", "app.main:app", "--port", "8000"],
     [WEBUI_PY, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8788"]),
    ("meshchat", "reticulum",
     [PY, "-m", "pytest", "-q", "tests/test_meshchat.py"],
     [".venv/bin/python", "meshchat.py", "--headless", "--host", "127.0.0.1", "--port", "8790"]),
]
IDS = [c[0] for c in CASES[:6]] + ["meshcore-webui-other-app", "meshchat"]


def _svc(tmp_path, argv):
    return ControllerService(system=FakeSystem(cmdlines_data={4242: argv}).system, paths=Paths(runtime_root=tmp_path))


def _stop(svc, stack, comp):
    res = svc.stop(stack, apply=True)
    return next(r for r in res.results if r.component == comp)


def _state(svc, stack, comp):
    ss = next(s for s in svc.build_snapshot().stacks if s.stack.id == stack)
    return ss.components[comp].run_state


@pytest.mark.contract
@pytest.mark.parametrize("comp,stack,named,real", CASES, ids=IDS)
def test_a_process_that_only_names_the_program_is_not_the_component(tmp_path, comp, stack, named, real):
    svc = _svc(tmp_path, named)
    cr = _stop(svc, stack, comp)
    assert cr.outcome is Outcome.ALREADY_STOPPED, cr.summary
    assert _state(svc, stack, comp) not in (RunState.RUNNING, RunState.DEGRADED)


@pytest.mark.parametrize("comp,stack,named,real", CASES, ids=IDS)
def test_control_the_components_own_argv_still_matches(tmp_path, comp, stack, named, real):
    # control (passes before and after the fix): the real argv, unrecorded, is still the component
    svc = _svc(tmp_path, real)
    cr = _stop(svc, stack, comp)
    assert cr.outcome is Outcome.MANUAL_REQUIRED and cr.summary.endswith("kill 4242"), cr.summary
    assert _state(svc, stack, comp) in (RunState.RUNNING, RunState.DEGRADED)


@pytest.mark.contract
def test_a_process_entry_that_is_not_a_string_is_refused():
    comp = {"id": "a", "name": "a", "process": {"exec_name": "python3", "entry": ["meshcore_host"]}}
    with pytest.raises(ManifestError, match="process.entry must be a string"):
        parse_manifest({"stack": [{"id": "s", "component": [comp], "main": "a"}]})
