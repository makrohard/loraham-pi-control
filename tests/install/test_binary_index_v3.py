"""The binary channel reads index-v3 (docs/provenance.md, the binary channel): a list of published
entries per stack, newest first; the index is read strictly (a malformed entry refuses it, never a skip).
Which entry is installed (U9, `newest_entry`) is tests/install/test_binary_newest_entry.py. Driven through
`binary_install` with only the HTTP seam stubbed, so the real index parser and selection run."""
from __future__ import annotations

import hashlib
import json

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


def test_the_newest_entry_built_for_this_lhpc_is_taken_without_a_note(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch)
    pins, inputs = svc._binary_pins("daemon"), _inputs(svc, "daemon")
    _serve(monkeypatch, {"schema": 3, "stacks": {"daemon": [
        _entry("daemon", "3" * 64, pins, inputs, "2026-10-17T08:00:00Z"),
        _entry("daemon", "4" * 64, pins, inputs)]}})
    plan = svc.binary_install("daemon", apply=False)
    assert plan.ok and any(f"daemon-{'3' * 64}.tar.zst" in d for d in plan.details)
    assert not plan.data.get("newer_published")


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
