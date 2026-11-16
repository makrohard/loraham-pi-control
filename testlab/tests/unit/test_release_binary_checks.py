"""The release lane's two binary checks, offline against a served index (the lane itself runs them against the
live index): an index whose entries are all built from other commits than the candidate's pins fails the exact-pin
check and passes the newest-entry check (U9 installs it); an index at the pins and build inputs passes both."""
from __future__ import annotations

import hashlib
from pathlib import Path

from lhpc_testlab.release import binaries_at_the_pins, binaries_installed

from lhpc.core import binary_install as bi
from lhpc.core.manifest import default_manifest_path
from lhpc.core.paths import Paths
from lhpc.core.services import ControllerService

TARGET = "aarch64-trixie"


def _stacks(tmp_path: Path, at_pins: bool):
    """(service, [(stack, spec, entries)]) for every binary stack of the packaged manifest, one entry each."""
    svc = ControllerService(manifest_path=default_manifest_path(), paths=Paths(runtime_root=tmp_path))
    index = {"schema": 3, "stacks": {}}
    specs = []
    for st in svc.stacks():
        spec = svc.binary_spec(st.id)
        if spec is None:
            continue
        pins = svc._binary_pins(st.id)
        inputs = {c.id: hashlib.sha256(svc.build_inputs_text(c).encode("utf-8")).hexdigest()
                  for c in st.components if c.id in spec.covers and (c.build_inputs or c.asset_inputs)}
        name = f"{st.id}-{'4' * 64}.tar.zst"
        index["stacks"][st.id] = [{
            "filename": name, "url": f"https://example.invalid/{name}", "sha256": "4" * 64, "size": 10,
            "built_from": "b" * 40, "components": pins if at_pins else dict.fromkeys(pins, "f" * 40),
            "lhpc_commit": "c" * 40, "builder_commit": "d" * 40, "target": TARGET, "os": "trixie",
            "container_digest": "debian@sha256:" + "e" * 64, "runtime_deps": [],
            "smoke": {"mode": "mandatory", "result": "passed"}, "extract_to": "runtime-root", "inputs": inputs,
            "published_at": "2026-11-12T00:00:00Z"}]
        specs.append((st, spec))
    assert specs, "the packaged manifest names no binary stack"
    return svc, [(st, spec, bi.index_entries(index, st.id)) for st, spec in specs]


def test_an_index_built_only_from_other_commits_fails_the_exact_pin_check_and_installs(tmp_path):
    svc, stacks = _stacks(tmp_path, at_pins=False)
    chosen, refused = binaries_at_the_pins(svc, TARGET, stacks)
    assert chosen == [] and len(refused) == len(stacks), refused
    chosen, refused = binaries_installed(TARGET, stacks)
    assert refused == [] and len(chosen) == len(stacks), refused


def test_an_index_at_the_pins_passes_both_checks(tmp_path):
    svc, stacks = _stacks(tmp_path, at_pins=True)
    assert binaries_at_the_pins(svc, TARGET, stacks)[1] == []
    assert binaries_installed(TARGET, stacks)[1] == []


def test_an_index_whose_entries_miss_a_covered_component_fails_the_newest_entry_check(tmp_path):
    # no entry the candidate's install takes (none_compatible): red, named per stack
    _svc, stacks = _stacks(tmp_path, at_pins=False)
    for _st, _spec, entries in stacks:
        for e in entries:
            e.components.pop(min(e.components))
    chosen, refused = binaries_installed(TARGET, stacks)
    assert chosen == [] and len(refused) == len(stacks), chosen
    assert all("no published binary covers" in r for r in refused), refused


def test_an_index_at_the_pins_from_other_build_inputs_fails_the_exact_pin_check_and_installs(tmp_path):
    # built at the pins, but one covered component's build-input digest differs from the candidate's (a CLI bump
    # after the build): the exact check refuses that stack, the newest-entry check still takes it
    svc, stacks = _stacks(tmp_path, at_pins=True)
    bumped = [st.id for st, _spec, entries in stacks if entries[0].inputs]
    assert bumped, "no binary stack covers a component with build inputs"
    for _st, _spec, entries in stacks:
        for e in entries:
            if e.inputs:
                e.inputs[min(e.inputs)] = "0" * 64
    chosen, refused = binaries_at_the_pins(svc, TARGET, stacks)
    assert sorted(r.split(":")[0] for r in refused) == sorted(bumped), refused
    assert all("at the candidate's pins and build inputs" in r for r in refused), refused
    assert len(chosen) == len(stacks) - len(bumped), chosen
    chosen, refused = binaries_installed(TARGET, stacks)
    assert refused == [] and len(chosen) == len(stacks), refused
