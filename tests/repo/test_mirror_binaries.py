"""`.github/scripts/mirror-binaries.py` (L1, DEC-W339): the binaries the testlab lanes install, mirrored before the
lanes. `list` names every candidate entry of the lab's stacks for its target (the product picks among them inside
the lane), and `verify`, run with no network, fails on a file that is missing or differs from the index. New tooling:
negative cases instead of a parent-red."""
import hashlib
import json
import subprocess
import sys

import repo_paths

SCRIPT = repo_paths.REPO / ".github" / "scripts" / "mirror-binaries.py"


def _run(*args):
    return subprocess.run([sys.executable, str(SCRIPT), *map(str, args)], capture_output=True, text=True, check=False)


def _e(stack, target, data):
    sha = hashlib.sha256(data).hexdigest()
    return {"filename": f"{stack}-{sha}.tar.zst", "sha256": sha, "size": len(data), "target": target}


def _mirror(tmp_path):
    es = {"meshcom": [_e("meshcom", "aarch64-trixie", b"a"), _e("meshcom", "aarch64-trixie", b"b"),
                      _e("meshcom", "aarch64-bookworm", b"c")],
          "meshtastic": [_e("meshtastic", "aarch64-trixie", b"d")], "chat": [_e("chat", "aarch64-trixie", b"e")]}
    (tmp_path / "index-v3.json").write_text(json.dumps({"schema": 3, "stacks": es}))
    for data, e in zip((b"a", b"b", b"c", b"d", b"e"), [*es["meshcom"], *es["meshtastic"], *es["chat"]]):
        (tmp_path / e["filename"]).write_bytes(data)
    return es


def test_list_names_every_candidate_of_the_stacks_for_the_target_only(tmp_path):
    es = _mirror(tmp_path)
    r = _run("list", tmp_path / "index-v3.json", "aarch64-trixie", "meshcom", "meshtastic")
    assert r.returncode == 0 and r.stdout.split() == [es["meshcom"][0]["filename"], es["meshcom"][1]["filename"],
                                                      es["meshtastic"][0]["filename"]]


def test_list_with_nothing_to_mirror_fails(tmp_path):
    _mirror(tmp_path)
    r = _run("list", tmp_path / "index-v3.json", "x86_64-trixie", "meshcom")
    assert r.returncode == 1 and "nothing to mirror" in r.stderr


def test_verify_passes_a_complete_mirror_and_fails_a_missing_or_tampered_file(tmp_path):
    es = _mirror(tmp_path)
    args = ("verify", tmp_path, "aarch64-trixie", "meshcom", "meshtastic")
    assert _run(*args).returncode == 0
    (tmp_path / es["meshcom"][2]["filename"]).unlink()          # another target's file: not needed
    assert _run(*args).returncode == 0
    (tmp_path / es["meshtastic"][0]["filename"]).write_bytes(b"D")
    r = _run(*args)
    assert r.returncode == 1 and f"{es['meshtastic'][0]['filename']}: size or sha256 differs" in r.stdout
    (tmp_path / es["meshcom"][1]["filename"]).unlink()
    r = _run(*args)
    assert r.returncode == 1 and f"{es['meshcom'][1]['filename']}: missing" in r.stdout


def test_a_requested_stack_without_an_entry_for_the_target_fails(tmp_path):
    # G1-C56: one stack's entries must not cover for another's absence
    _mirror(tmp_path)
    r = _run("list", tmp_path / "index-v3.json", "aarch64-bookworm", "meshcom", "meshtastic")
    assert r.returncode == 1 and "no entry of meshtastic for aarch64-bookworm" in r.stderr
    assert _run("verify", tmp_path, "aarch64-bookworm", "meshcom", "meshtastic").returncode == 1
