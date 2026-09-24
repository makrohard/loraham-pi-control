"""`{pin:<source path>}` build-step tokens resolve to that source's pin at parse time.

A build that fetches a repository by ref must fetch THE PIN. Until 0.9.1 the MeshCom QEMU
build carried a hardcoded commit next to the pin (R8): the pin moved, the build did not, and the
artifact was labelled with a commit it did not contain. The token is the fix; these tests are the
contract: it resolves to whatever the pin IS (never a literal), and an unknown path is an error.
"""
import copy

import pytest

from lhpc.core import manifest as mf


def _data(pin="a" * 40, token="{pin:src/fw}"):
    return {"stack": [{
        "id": "s", "name": "s", "main": "app",
        "component": [
            {"id": "fw", "name": "fw", "kind": "library", "optional": True,
             "source": {"path": "src/fw", "pin_commit": pin, "remote": "https://x/fw.git",
                        "branch": "dev"}},
            {"id": "app", "name": "app", "kind": "service",
             "run": "bin/app", "readiness": "manual", "interactive": True,
             "build_steps": [{"argv": ["scripts/setup.sh", "--ref", token]}]},
        ]}]}


def test_pin_token_resolves_to_the_pinned_commit():
    stacks = mf.parse_manifest(_data(pin="b" * 40))
    app = stacks[0].component("app")
    assert app.build_steps[0]["argv"] == ["scripts/setup.sh", "--ref", "b" * 40]


def test_pin_token_follows_the_pin_not_a_literal():
    a = mf.parse_manifest(_data(pin="c" * 40))[0].component("app").build_steps[0]["argv"][-1]
    b = mf.parse_manifest(_data(pin="d" * 40))[0].component("app").build_steps[0]["argv"][-1]
    assert (a, b) == ("c" * 40, "d" * 40)


def test_unknown_pin_path_is_a_manifest_error():
    with pytest.raises(mf.ManifestError, match=r"\{pin:src/nope\}"):
        mf.parse_manifest(_data(token="{pin:src/nope}"))


def test_resolution_is_idempotent_on_a_reused_mapping():
    data = _data(pin="e" * 40)
    mf.parse_manifest(data)
    again = mf.parse_manifest(copy.deepcopy(data))
    assert again[0].component("app").build_steps[0]["argv"][-1] == "e" * 40


def test_the_meshcom_qemu_setup_step_fetches_the_firmware_pin():
    """The real manifest: the setup step's --ref IS the meshcom-firmware pin (a contract between two
    manifest values — no pin literal in this test, see tests/repo/test_no_pin_literals_in_tests)."""
    stacks = {s.id: s for s in mf.load_manifest()}
    mc = stacks["meshcom"]
    fw = mc.component("meshcom-firmware")
    qemu = mc.component("meshcom-qemu")
    setup = [st for st in qemu.build_steps if st.get("argv", [""])[0] == "scripts/setup.sh"]
    assert len(setup) == 1
    argv = setup[0]["argv"]
    assert argv[argv.index("--ref") + 1] == fw.source.pin_commit
