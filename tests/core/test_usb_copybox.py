"""The copybox (B5-WIRING-TABLES-v1 Table C; INTERFACE-B5-W1 `copybox`, `usb_setup_text`).

The copybox runs a script file the generator writes; the one line checks the file's sha256 before it runs it,
and a copybox is never shown for a file that is missing or stale. New modules: each test imports them itself.
"""
from __future__ import annotations

import hashlib
import shlex
import stat

from lhpc.core import radio_models
from lhpc.core.paths import resolve_paths

MODELS = radio_models.load()


def _uf():
    from lhpc.core import usb_flow
    return usb_flow


def _paths(tmp_path):
    return resolve_paths({"LHPC_RUNTIME_ROOT": str(tmp_path)})


def _file(tmp_path):
    return tmp_path / "state" / "usb-setup" / "usb-setup.sh"


def test_c2_a_ready_copybox_writes_the_standalone_block_and_names_its_sha256(tmp_path):
    uf = _uf()
    box = uf.copybox(_paths(tmp_path), "tester", MODELS)
    assert box.state == "ready" and box.reason == ""
    f = _file(tmp_path)
    assert box.path == str(f)
    data = f.read_bytes()
    assert data.decode("utf-8") == uf.usb_setup_text("tester", MODELS, standalone=True)
    assert box.sha256 == hashlib.sha256(data).hexdigest()
    assert stat.S_IMODE(f.stat().st_mode) == 0o644


def test_c2_the_line_checks_the_sha256_before_it_runs_and_has_no_exit_outside_its_quoted_body(tmp_path):
    box = _uf().copybox(_paths(tmp_path), "tester", MODELS)
    words = shlex.split(box.line)
    assert words[:3] == ["sudo", "sh", "-c"] and len(words) == 4      # one quoted body, nothing after it
    body = words[3]
    assert box.path in body and box.sha256 in body
    assert body.index("sha256sum -c") < body.index("bash ")
    assert "exit" not in box.line.replace(body, "")


def test_c1_the_copybox_file_and_the_scaffold_block_come_from_one_generator(tmp_path):
    uf = _uf()
    box = uf.copybox(_paths(tmp_path), "tester", MODELS)
    scaffold = uf.usb_setup_text("$OP", MODELS).replace('"$OP"', '"tester"').replace("$OP", "tester")
    body = _file(tmp_path).read_text(encoding="utf-8").rstrip("\n").rpartition("\n")[0]
    assert box.state == "ready" and scaffold.rstrip("\n") == body.rstrip("\n")


def test_c4_a_stale_file_is_rewritten_before_the_copybox_is_shown(tmp_path):
    f = _file(tmp_path)
    f.parent.mkdir(parents=True)
    f.write_text("#!/bin/sh\necho an older rule set\n", encoding="utf-8")
    box = _uf().copybox(_paths(tmp_path), "tester", MODELS)
    assert box.state == "ready"
    assert hashlib.sha256(f.read_bytes()).hexdigest() == box.sha256
    assert "older rule set" not in f.read_text(encoding="utf-8")


def test_c3_a_file_that_cannot_be_written_shows_no_copybox_and_never_raises(tmp_path):
    (tmp_path / "state").mkdir()
    (tmp_path / "state" / "usb-setup").write_text("a file where the directory belongs", encoding="utf-8")
    box = _uf().copybox(_paths(tmp_path), "tester", MODELS)
    assert box.state == "refused" and box.line == "" and box.reason


def test_c3_a_runtime_path_out_of_the_root_shows_no_copybox(tmp_path):
    root, outside = tmp_path / "root", tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (root / "state").symlink_to(outside, target_is_directory=True)
    box = _uf().copybox(resolve_paths({"LHPC_RUNTIME_ROOT": str(root)}), "tester", MODELS)
    assert box.state == "refused" and box.line == ""
    assert not list(outside.rglob("usb-setup.sh"))

