"""requirements.lock (the runtime dependencies of LHPC's own venv, installed with --require-hashes) covers every
dependency pyproject.toml declares, at a version its specifier admits, each with at least one file hash. The
transitive completeness is CI's: a lock-only install in a clean venv, then `pip check`.

A repository invariant, not behaviour: it reads the two files."""
from __future__ import annotations

import re
import tomllib

import repo_paths
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name


def _lock() -> dict:
    text = (repo_paths.REPO / "requirements.lock").read_text()
    pins = {}
    for block in re.split(r"\n(?=[A-Za-z0-9])", text):
        m = re.match(r"([A-Za-z0-9_.-]+)==([^\s\\]+)", block)
        if m:
            pins[canonicalize_name(m.group(1))] = (m.group(2), re.findall(r"--hash=sha256:[0-9a-f]{64}", block))
    return pins


def test_every_declared_dependency_is_locked_at_an_admitted_version_with_a_hash():
    # The runtime dependencies and the build backend: LHPC installs with --no-build-isolation.
    pyproject = tomllib.loads((repo_paths.REPO / "pyproject.toml").read_text())
    declared = pyproject["project"]["dependencies"] + pyproject["build-system"]["requires"]
    pins = _lock()
    assert declared and pins
    for spec in map(Requirement, declared):
        version, hashes = pins.get(canonicalize_name(spec.name), (None, []))
        assert version is not None, f"{spec.name} is not in requirements.lock"
        assert spec.specifier.contains(version, prereleases=True), f"{spec.name}=={version} vs {spec.specifier}"
        assert hashes, f"{spec.name} has no hash"


def test_every_lock_entry_carries_a_hash():
    assert all(hashes for _version, hashes in _lock().values())


def test_deployment_docs_name_the_lock_sync_for_an_update_from_0_12_10():
    """An update from 0.12.10 or older is run by that release's code, which synced the venv without the
    lock; docs/deployment.md gives the one sync to run afterwards. It must be the command self-update
    runs (`venv_sync_command`) at the deployment's paths, relative to the runtime root."""
    from lhpc.core.selfupdate import venv_sync_command
    docs = (repo_paths.REPO / "docs" / "deployment.md").read_text()
    assert venv_sync_command("venv/lhpc/bin/python", "src/loraham-pi-control") in docs
