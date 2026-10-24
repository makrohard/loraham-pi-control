"""The binary channel reads index-v3 (docs/provenance.md, the binary channel): a list of published
entries per stack, newest first, and installs the FIRST entry built for this lhpc — its target, its
component commits and its build-input digests all equal to what this lhpc expects. Driven through
`binary_install` with only the HTTP seam stubbed, so the real index parser and selection run."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from lhpc.core import binary_install as bi
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService


def _svc(tmp_path, monkeypatch):
    monkeypatch.setattr(ControllerService, "binary_target", lambda self: "aarch64-trixie")
    return ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))


def _inputs(svc, sid):
    """{component: sha256 of its build-input sidecar}, as this lhpc would write it."""
    st = svc.stack(sid)
    covers = set(svc.binary_spec(sid).covers)
    return {c.id: hashlib.sha256(svc.build_inputs_text(c).encode()).hexdigest()
            for c in st.components if c.id in covers and (c.build_inputs or c.asset_inputs)}


def _entry(sid, sha, components, inputs, published="2026-10-16T08:00:00Z", lhpc="c" * 40, target="aarch64-trixie"):
    filename = f"{sid}-{sha}.tar.zst"
    return {"filename": filename, "url": f"https://example.invalid/{filename}", "sha256": sha,
            "size": 10, "built_from": "b" * 40, "components": dict(components),
            "lhpc_commit": lhpc, "builder_commit": "d" * 40, "target": target,
            "os": "trixie", "container_digest": "debian@sha256:" + "e" * 64, "runtime_deps": [],
            "smoke": {"mode": "mandatory", "result": "passed"}, "extract_to": "runtime-root",
            "inputs": dict(inputs), "published_at": published}


def _serve(monkeypatch, index):
    monkeypatch.setattr(bi, "_http_get", lambda url, max_bytes: json.dumps(index).encode())
    monkeypatch.setattr(bi, "require_zstd", lambda: None)


def _lagging(pins):
    return {cid: "f" * 40 for cid in pins}


def test_an_older_entry_built_for_this_lhpc_is_installed_and_the_newer_one_named(tmp_path, monkeypatch):
    # The index moved on (a bot release for a newer lhpc); this lhpc's own binary is still listed.
    svc = _svc(tmp_path, monkeypatch)
    pins, inputs = svc._binary_pins("daemon"), _inputs(svc, "daemon")
    _serve(monkeypatch, {"schema": 3, "stacks": {"daemon": [
        _entry("daemon", "1" * 64, _lagging(pins), inputs, "2026-10-17T08:00:00Z", lhpc="a" * 40),
        _entry("daemon", "2" * 64, pins, inputs)]}})
    plan = svc.binary_install("daemon", apply=False)
    assert plan.ok, plan.summary
    assert any(f"daemon-{'2' * 64}.tar.zst" in d for d in plan.details)
    assert plan.data.get("newer_published") is True
    assert any("lhpc self-update --apply" in d for d in plan.details)



def test_an_installed_older_binary_still_names_the_newer_one(tmp_path, monkeypatch):
    # The box already runs this lhpc's binary: nothing to download, nothing to apply, and the line.
    svc = _svc(tmp_path, monkeypatch)
    pins, inputs = svc._binary_pins("daemon"), _inputs(svc, "daemon")
    _serve(monkeypatch, {"schema": 3, "stacks": {"daemon": [
        _entry("daemon", "1" * 64, _lagging(pins), inputs, "2026-10-17T08:00:00Z", lhpc="a" * 40),
        _entry("daemon", "2" * 64, pins, inputs)]}})
    monkeypatch.setattr(ControllerService, "_binary_installed_is",
                        lambda self, sid, e: e.sha256 == "2" * 64)
    plan = svc.binary_install("daemon", apply=False)
    assert plan.ok and "already has the published binary" in plan.summary
    assert plan.data["changes"] == 0 and plan.data.get("newer_published") is True
    assert any("lhpc self-update --apply" in d for d in plan.details)

def test_the_older_entry_is_what_an_apply_installs_and_records(tmp_path, monkeypatch):
    # Two distinct artifacts behind the seam: the box gets the OLDER entry's bytes, and the
    # receipt records that entry's digest — not the newer one listed first.
    svc = _svc(tmp_path, monkeypatch)
    pins, inputs = svc._binary_pins("daemon"), _inputs(svc, "daemon")
    arts = {name: name.encode().ljust(10, b"-") for name in ("newer", "older")}
    sha = {name: hashlib.sha256(data).hexdigest() for name, data in arts.items()}
    by_sha = {sha[name]: data for name, data in arts.items()}
    _serve(monkeypatch, {"schema": 3, "stacks": {"daemon": [
        _entry("daemon", sha["newer"], _lagging(pins), inputs, "2026-10-17T08:00:00Z", lhpc="a" * 40),
        _entry("daemon", sha["older"], pins, inputs)]}})
    binary = "src/loraham-daemon/loraham_daemon/loraham_daemon"
    files = list(svc.binary_spec("daemon").proof_paths)     # the binary and the Meshstick library
    assert files[0] == binary
    monkeypatch.setattr(bi, "download_artifact", lambda e, path: Path(path).write_bytes(by_sha[e.sha256]))

    def stage(tar, stage_dir, roots):
        for rel in files:
            out = Path(stage_dir, rel)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(Path(tar).read_bytes())
        return files
    monkeypatch.setattr(bi, "validate_and_extract", stage)
    monkeypatch.setattr(bi, "run_probe", lambda paths, argv: "ok")
    res = svc.binary_install("daemon", apply=True)
    assert res.ok, res.summary
    assert (tmp_path / binary).read_bytes() == arts["older"]
    receipt = json.loads((tmp_path / "state/binary/daemon.json").read_text())
    assert receipt["artifact_sha256"] == sha["older"]
    assert receipt["file_hashes"][binary] == sha["older"]


def test_the_newest_entry_built_for_this_lhpc_is_taken_without_a_note(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch)
    pins, inputs = svc._binary_pins("daemon"), _inputs(svc, "daemon")
    _serve(monkeypatch, {"schema": 3, "stacks": {"daemon": [
        _entry("daemon", "3" * 64, pins, inputs, "2026-10-17T08:00:00Z"),
        _entry("daemon", "4" * 64, pins, inputs)]}})
    plan = svc.binary_install("daemon", apply=False)
    assert plan.ok and any(f"daemon-{'3' * 64}.tar.zst" in d for d in plan.details)
    assert not plan.data.get("newer_published")


@pytest.mark.parametrize("ahead", [
    {"target": "x86_64-trixie"},                    # another platform's binary
    {"published": "2026-10-15T08:00:00Z"},          # listed first, published before the chosen one
    {"lhpc": "c" * 40},                             # a later build for this same lhpc
])
def test_an_entry_listed_first_is_named_only_when_another_lhpc_published_it_later(tmp_path, monkeypatch, ahead):
    # The note says "built for another LHPC ... published later": list position proves neither.
    svc = _svc(tmp_path, monkeypatch)
    pins, inputs = svc._binary_pins("daemon"), _inputs(svc, "daemon")
    first = {"published": "2026-10-17T08:00:00Z", "lhpc": "a" * 40, **ahead}
    _serve(monkeypatch, {"schema": 3, "stacks": {"daemon": [
        _entry("daemon", "1" * 64, _lagging(pins), inputs, **first),
        _entry("daemon", "2" * 64, pins, inputs)]}})
    plan = svc.binary_install("daemon", apply=False)
    assert plan.ok and any(f"daemon-{'2' * 64}.tar.zst" in d for d in plan.details)
    assert not plan.data.get("newer_published")
    assert not any("lhpc self-update --apply" in d for d in plan.details)


def test_an_entry_with_other_build_inputs_is_never_taken(tmp_path, monkeypatch):
    # Same commits, other build inputs (another web client, CLI or packaged asset): not this lhpc's.
    svc = _svc(tmp_path, monkeypatch)
    pins, inputs = svc._binary_pins("meshtastic"), _inputs(svc, "meshtastic")
    assert inputs, "precondition: meshtastic records build inputs"
    other = {cid: "9" * 64 for cid in inputs}
    _serve(monkeypatch, {"schema": 3, "stacks": {"meshtastic": [
        _entry("meshtastic", "5" * 64, pins, other, "2026-10-17T08:00:00Z"),
        _entry("meshtastic", "6" * 64, pins, inputs)]}})
    plan = svc.binary_install("meshtastic", apply=False)
    assert plan.ok and any(f"meshtastic-{'6' * 64}.tar.zst" in d for d in plan.details)
    _serve(monkeypatch, {"schema": 3, "stacks": {"meshtastic": [
        _entry("meshtastic", "5" * 64, pins, other)]}})
    monkeypatch.setattr(bi, "download_artifact", lambda *a: pytest.fail("must refuse first"))
    res = svc.binary_install("meshtastic", apply=True)
    assert not res.ok and res.data.get("binary_failed")


def test_no_entry_built_for_this_lhpc_is_refused_before_any_download(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch)
    pins, inputs = svc._binary_pins("daemon"), _inputs(svc, "daemon")
    _serve(monkeypatch, {"schema": 3, "stacks": {"daemon": [
        _entry("daemon", "7" * 64, _lagging(pins), inputs)]}})
    monkeypatch.setattr(bi, "download_artifact", lambda *a: pytest.fail("must refuse first"))
    res = svc.binary_install("daemon", apply=True)
    assert not res.ok and res.data.get("binary_failed")
    assert "lhpc self-update --apply" in res.next_commands


@pytest.mark.parametrize("listed", ["missing", "empty", "other-target"])
def test_nothing_published_for_this_box_is_the_typed_refusal(tmp_path, monkeypatch, listed):
    # §F2.2: every zero-candidate outcome is `none_compatible`, before any download.
    svc = _svc(tmp_path, monkeypatch)
    pins, inputs = svc._binary_pins("daemon"), _inputs(svc, "daemon")
    other = dict(_entry("daemon", "8" * 64, pins, inputs), target="armhf-trixie")
    stacks = {"missing": {}, "empty": {"daemon": []}, "other-target": {"daemon": [other]}}[listed]
    _serve(monkeypatch, {"schema": 3, "stacks": stacks})
    monkeypatch.setattr(bi, "download_artifact", lambda *a: pytest.fail("must refuse first"))
    res = svc.binary_install("daemon", apply=True)
    assert not res.ok and res.data["binary_refusal"] == "none_compatible"
    assert res.data["pin_mismatch"] == {}
    assert res.next_commands == ["lhpc self-update --apply", "lhpc install daemon --source pinned --yes"]


@pytest.mark.parametrize("stacks", [
    {"daemon": {}},                                             # a v2 entry under schema 3
    {"daemon": ["not an entry"]},
], ids=["not-a-list", "not-an-object"])
def test_a_malformed_v3_stack_list_is_a_refusal(tmp_path, monkeypatch, stacks):
    svc = _svc(tmp_path, monkeypatch)
    _serve(monkeypatch, {"schema": 3, "stacks": stacks})
    res = svc.binary_install("daemon", apply=False)
    assert not res.ok and res.data.get("binary_failed") and "binary_refusal" not in res.data


def test_inputs_naming_a_component_outside_the_entry_are_a_refusal_not_a_skip(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch)
    pins = svc._binary_pins("daemon")
    _serve(monkeypatch, {"schema": 3, "stacks": {"daemon": [
        _entry("daemon", "8" * 64, pins, {"not-a-component": "0" * 64})]}})
    res = svc.binary_install("daemon", apply=False)
    assert not res.ok and res.data.get("binary_failed")


@pytest.mark.parametrize("sid,field,value", [("daemon", "inputs", []), ("meshtastic", "inputs", "pairs"),
                                             ("daemon", "components", "pairs")])
def test_an_array_where_the_schema_says_object_is_a_plain_refusal(tmp_path, monkeypatch, sid, field, value):
    # dict() would read `[]` as {} (the daemon records no build inputs) and an array of
    # [key, value] pairs as a valid map: both must fail as malformed, before any download.
    svc = _svc(tmp_path, monkeypatch)
    pins, inputs = svc._binary_pins(sid), _inputs(svc, sid)
    assert bool(inputs) == (sid == "meshtastic"), "precondition: which stack records build inputs"
    e = _entry(sid, "2" * 64, pins, inputs)
    e[field] = [list(kv) for kv in e[field].items()] if value == "pairs" else value
    _serve(monkeypatch, {"schema": 3, "stacks": {sid: [e]}})
    monkeypatch.setattr(bi, "download_artifact", lambda *a: pytest.fail("must refuse first"))
    res = svc.binary_install(sid, apply=True)
    assert not res.ok and res.data.get("binary_failed") and "binary_refusal" not in res.data
    assert "not an object" in res.summary


def test_an_unknown_index_schema_is_refused(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch)
    _serve(monkeypatch, {"schema": 4, "stacks": {}})
    res = svc.binary_install("daemon", apply=False)
    assert not res.ok and "schema 4" in res.summary


def test_a_v3_index_with_another_top_level_key_is_refused(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch)
    pins, inputs = svc._binary_pins("daemon"), _inputs(svc, "daemon")
    _serve(monkeypatch, {"schema": 3, "stacks": {"daemon": [_entry("daemon", "2" * 64, pins, inputs)]},
                         "rollback": {}})
    res = svc.binary_install("daemon", apply=False)
    assert not res.ok and res.data.get("binary_failed")


@pytest.mark.parametrize("stamp", [None, "", "2026-10-16 08:00:00", "2026-10-16T08:00:00+00:00",
                                   "2026-10-16T08:00:00.5Z", "2026-13-01T08:00:00Z", "2026-02-30T08:00:00Z",
                                   "2026-10-16T24:00:00Z", 1760601600],
                         ids=["missing", "empty", "no-T-no-Z", "offset", "fraction", "month-13", "feb-30",
                              "hour-24", "epoch-number"])
def test_a_v3_entry_without_a_utc_published_at_is_a_refusal(tmp_path, monkeypatch, stamp):
    # SCHEMA-index-v3: `published_at` is UTC YYYY-MM-DDTHH:MM:SSZ; the valid form is what B1's writer emits
    # (`time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())`; its test fixture "2026-10-16T08:00:00Z" = `_entry`).
    svc = _svc(tmp_path, monkeypatch)
    pins, inputs = svc._binary_pins("daemon"), _inputs(svc, "daemon")
    e = _entry("daemon", "3" * 64, pins, inputs)
    if stamp is None:
        del e["published_at"]
    else:
        e["published_at"] = stamp
    _serve(monkeypatch, {"schema": 3, "stacks": {"daemon": [e]}})
    monkeypatch.setattr(bi, "download_artifact", lambda *a: pytest.fail("must refuse first"))
    res = svc.binary_install("daemon", apply=True)
    assert not res.ok and res.data.get("binary_failed") and "binary_refusal" not in res.data
    assert "published_at" in res.summary


def test_a_malformed_newer_entry_refuses_the_index_not_skipped_for_an_older_one(tmp_path, monkeypatch):
    # SCHEMA-index-v3: an entry failing validation is a refusal, never skipped; a malformed covered
    # commit in the newest entry must not let an older compatible entry install.
    svc = _svc(tmp_path, monkeypatch)
    pins, inputs = svc._binary_pins("daemon"), _inputs(svc, "daemon")
    _serve(monkeypatch, {"schema": 3, "stacks": {"daemon": [
        _entry("daemon", "4" * 64, {cid: "g" * 40 for cid in pins}, inputs, "2026-10-17T08:00:00Z"),
        _entry("daemon", "5" * 64, pins, inputs)]}})
    monkeypatch.setattr(bi, "download_artifact", lambda *a: pytest.fail("must refuse first"))
    res = svc.binary_install("daemon", apply=True)
    assert not res.ok and res.data.get("binary_failed") and "binary_refusal" not in res.data
    assert "malformed commit" in res.summary


def test_an_older_composition_lacking_a_covered_component_is_skipped_not_a_refusal(tmp_path, monkeypatch):
    # A later lhpc covers more: older history entries lack the new component. Such an entry is
    # another composition (skipped, like a lagging commit), never a malformed index.
    svc = _svc(tmp_path, monkeypatch)
    pins, inputs = svc._binary_pins("daemon"), _inputs(svc, "daemon")
    _serve(monkeypatch, {"schema": 3, "stacks": {"daemon": [
        _entry("daemon", "6" * 64, {}, inputs, "2026-10-17T08:00:00Z"),
        _entry("daemon", "7" * 64, pins, inputs)]}})
    plan = svc.binary_install("daemon", apply=False)
    assert plan.ok and any(f"daemon-{'7' * 64}.tar.zst" in d for d in plan.details)
    _serve(monkeypatch, {"schema": 3, "stacks": {"daemon": [_entry("daemon", "6" * 64, {}, inputs)]}})
    monkeypatch.setattr(bi, "download_artifact", lambda *a: pytest.fail("must refuse first"))
    res = svc.binary_install("daemon", apply=True)
    assert not res.ok and res.data["binary_refusal"] == "none_compatible"
    assert res.next_commands[0] == "lhpc self-update --apply"


@pytest.mark.parametrize("where", ["with-a-missing-one", "behind-a-compatible-one"])
def test_a_malformed_covered_commit_anywhere_refuses_the_index(tmp_path, monkeypatch, where):
    # Every present covered commit is checked across the WHOLE list before one entry is chosen
    # or skipped: neither a missing component in the same entry nor a compatible entry in front
    # of it may hide a malformed one.
    svc = _svc(tmp_path, monkeypatch)
    pins, inputs = svc._binary_pins("daemon"), _inputs(svc, "daemon")
    first, *rest = sorted(pins)
    bad = _entry("daemon", "8" * 64, {first: "g" * 40}, inputs, "2026-10-17T08:00:00Z")
    good = _entry("daemon", "9" * 64, pins, inputs)
    assert rest, "precondition: the daemon covers more than one component"
    _serve(monkeypatch, {"schema": 3, "stacks": {"daemon": [bad, good] if where.startswith("with")
                                                  else [good, bad]}})
    monkeypatch.setattr(bi, "download_artifact", lambda *a: pytest.fail("must refuse first"))
    res = svc.binary_install("daemon", apply=True)
    assert not res.ok and res.data.get("binary_failed") and "binary_refusal" not in res.data
    assert "malformed commit" in res.summary
