"""The builder and the lane must answer the attribution question identically.

The binary builder cannot import the test lab, so it runs `tools/build_regression.py` against the
controller it was told to build. If that tool and the lane ever disagreed, a pin could be frozen
by one path and not the other — so both go through `lhpc.core.build_regression`, and this drives
the tool as the builder actually drives it: a subprocess, over a captured output file.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

import repo_paths

TOOL = repo_paths.REPO / "tools" / "build_regression.py"


def _run(stack: str, captured: str, tmp_path: Path):
    f = tmp_path / "out.txt"
    f.write_text(captured)
    r = subprocess.run([sys.executable, str(TOOL), stack, str(f)],
                       capture_output=True, text=True, check=False)
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


def _owned_log(stack_id: str) -> str:
    from lhpc.core.build_regression import own_step_logs
    from lhpc.core.manifest import load_manifest
    stack = next(s for s in load_manifest() if s.id == stack_id)      # the shipped manifest
    return sorted(own_step_logs(stack))[0]


def test_a_step_the_recipe_declares_its_own_attributes(tmp_path):
    log = _owned_log("meshcore")
    out = _run("meshcore", f"  [failed] build x (rc 1, log /l/{log})", tmp_path)
    assert out == "STACK-REGRESSION stack=meshcore phase=build"


@pytest.mark.parametrize("captured,why", [
    ("  [failed] build meshcore-node (rc 1, log /l/build-meshcore-node-1.log)",
     "a networked pip step is not the recipe's own"),
    ("  [timeout] build meshcore-node (rc 124, log /l/build-meshcore-node-0.log)",
     "a runner that ran out of time says nothing about upstream"),
    ("ERR   Refusing to build 'meshcore': not installed.",
     "a refusal executed no step at all"),
    ("  [failed] build meshcore-node (rc 1, log )",
     "every command succeeded and a local write failed"),
    ("", "no output at all"),
])
def test_everything_else_attributes_nothing(captured, why, tmp_path):
    assert _run("meshcore", captured, tmp_path) == "", why


def test_the_log_names_the_rule_expects_are_the_ones_the_builder_writes(tmp_path, monkeypatch):
    """The naming convention lives in TWO places and must not drift.

    `Lifecycle.build` names step `i` of a multi-step component `build-<component>-<i>`, and
    `build_regression.own_step_logs` reconstructs that name to decide what may attribute. Nothing
    tied them together: renaming the log would leave attribution matching nothing, no automatic
    freeze would ever fire again, and every run would look exactly as green as before. So this
    asks the real `build()` what it names the attributable step, and the rule what it expects.
    """
    from pathlib import PurePosixPath

    from lhpc.core import build_regression as br
    from lhpc.core import lifecycle as lifecycle_mod
    from lhpc.core.jobs import JobResult, JobState
    from lhpc.core.paths import Paths
    from lhpc.core.probes.backends import FakeSystem
    from lhpc.core.services import ControllerService

    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=Path(tmp_path)))
    stack = svc.stack("meshcore")
    comp = next(c for c in stack.components
                if c.build_steps and any(s.get("attributable") for s in c.build_steps))
    owned_index = next(i for i, s in enumerate(comp.build_steps) if s.get("attributable"))
    (svc._lifecycle().source_dir(comp) / ".venv" / "bin").mkdir(parents=True, exist_ok=True)

    seen = []

    def record(runner, **kw):
        seen.append(kw["name"])
        state = JobState.FAILED if len(seen) - 1 == owned_index else JobState.SUCCEEDED
        return JobResult(name=kw["name"], state=state,
                         returncode=1 if state is JobState.FAILED else 0,
                         log_path=f"/x/logs/{kw['name']}.log", tail=[])

    monkeypatch.setattr(lifecycle_mod, "run_job", record)
    res = svc._lifecycle().build(comp, marker_extra=svc._consumed_source_lines(comp))

    assert not res.ok and res.log_path, "the attributable step must fail with a log identity"
    assert PurePosixPath(res.log_path).name in br.own_step_logs(stack), (
        f"build() named the failed step {PurePosixPath(res.log_path).name!r}, but the "
        f"attribution rule looks for one of {sorted(br.own_step_logs(stack))} — a rename here "
        f"silently disables every automatic freeze")
