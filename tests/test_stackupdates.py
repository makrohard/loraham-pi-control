"""Per-component source-freshness cache (`state/stackupdates.json`).

The invariant under test: a corrupt, absent, or STALE cache degrades to `unchecked` — never a
false `up_to_date`, and never a `behind` that keeps nagging after the operator already updated.
"""

from __future__ import annotations

import json

import pytest

from lhpc.core import stackupdates as su
from lhpc.core.paths import Paths

A = "a" * 40
B = "b" * 40


def _paths(tmp_path):
    (tmp_path / "state").mkdir(parents=True, exist_ok=True)
    return Paths(runtime_root=tmp_path)


def _entry(status, at=A, up=B):
    return {"remote": "https://example.invalid/x.git", "source_path": "src/x",
            "local_head_at_check": at, "upstream_head": up, "status": status}


def _write_raw(tmp_path, text):
    (tmp_path / "state").mkdir(parents=True, exist_ok=True)
    (tmp_path / "state" / "stackupdates.json").write_text(text)


# --- envelope safety ----------------------------------------------------------------------------

def test_absent_cache_is_empty_view(tmp_path):
    v = su.view(_paths(tmp_path))
    assert v == {"checked_at": 0, "components": {}}


def test_record_roundtrip(tmp_path):
    p = _paths(tmp_path)
    su.record(p, {"loraham-daemon": _entry(su.BEHIND)}, now=1000)
    v = su.view(p)
    assert v["checked_at"] == 1000
    e = v["components"]["loraham-daemon"]
    assert e["status"] == su.BEHIND and e["local_head_at_check"] == A and e["checked_at"] == 1000


@pytest.mark.parametrize("raw", [
    '{"schema_version": 2, "components": {}}',            # future version -> whole envelope rejected
    '{"schema_version": "1", "components": {}}',           # string version
    '{"schema_version": true, "components": {}}',          # bool is not an int
    '{"checked_at": true}',                                # bool must never pass as a timestamp
    '{"checked_at": "1000"}',
    '{"components": []}',                                  # wrong shape
    '[1, 2, 3]',                                           # wrong root
    'not json at all',
])
def test_malformed_envelope_degrades_to_unchecked(tmp_path, raw):
    _write_raw(tmp_path, raw)
    assert su.view(_paths(tmp_path)) == {"checked_at": 0, "components": {}}


def test_oversized_cache_is_rejected_not_truncated(tmp_path):
    # a valid-JSON prefix followed by padding must not be read as valid
    _write_raw(tmp_path, '{"schema_version": 1, "components": {}}' + " " * (su.CACHE_MAX_BYTES + 1))
    assert su.view(_paths(tmp_path))["components"] == {}


def test_symlinked_marker_is_refused(tmp_path):
    p = _paths(tmp_path)
    real = tmp_path / "state" / "real.json"
    real.write_text('{"schema_version": 1, "checked_at": 5, "components": {}}')
    (tmp_path / "state" / "stackupdates.json").symlink_to("real.json")
    assert su.view(p) == {"checked_at": 0, "components": {}}     # no-follow


# --- per-entry validation: one bad record must not blind the others -----------------------------

def test_bad_entry_is_dropped_but_siblings_survive(tmp_path):
    _write_raw(tmp_path, json.dumps({
        "schema_version": 1, "checked_at": 10,
        "components": {
            "good": _entry(su.BEHIND),
            "bad-status": _entry("totally-made-up"),
            "bad-head": _entry(su.UP_TO_DATE, at="zz-not-hex"),
            "bad-checked-at": {**_entry(su.BEHIND), "checked_at": True},
        }}))
    comps = su.view(_paths(tmp_path))["components"]
    assert set(comps) == {"good"}


def test_record_merges_and_does_not_clobber_other_components(tmp_path):
    p = _paths(tmp_path)
    su.record(p, {"daemon": _entry(su.BEHIND), "radiolib": _entry(su.UP_TO_DATE)}, now=1)
    su.record(p, {"daemon": _entry(su.UP_TO_DATE)}, now=2)       # a single-stack re-check
    comps = su.view(p)["components"]
    assert comps["daemon"]["status"] == su.UP_TO_DATE
    assert comps["radiolib"]["status"] == su.UP_TO_DATE          # untouched, not erased


def test_record_rejects_an_invalid_entry_without_writing_it(tmp_path):
    p = _paths(tmp_path)
    su.record(p, {"x": _entry("nonsense")}, now=1)
    assert su.view(p)["components"] == {}


# --- the staleness guard (the whole point) ------------------------------------------------------

def test_verdict_holds_only_for_the_head_it_was_computed_against(tmp_path):
    assert su.effective_status(_entry(su.UP_TO_DATE, at=A), A) == su.UP_TO_DATE
    assert su.effective_status(_entry(su.BEHIND, at=A), A) == su.BEHIND


def test_stale_up_to_date_never_renders_green(tmp_path):
    # Checked against A; the source is now at B (operator updated / cleaned + re-cloned).
    assert su.effective_status(_entry(su.UP_TO_DATE, at=A), B) == su.UNCHECKED


def test_stale_behind_stops_nagging_after_an_update(tmp_path):
    # The reverse staleness: a "behind" verdict for a commit that is no longer installed.
    assert su.effective_status(_entry(su.BEHIND, at=A), B) == su.UNCHECKED


def test_missing_or_empty_heads_are_unchecked(tmp_path):
    assert su.effective_status(_entry(su.UP_TO_DATE, at=""), A) == su.UNCHECKED
    assert su.effective_status(_entry(su.UP_TO_DATE, at=A), "") == su.UNCHECKED
    assert su.effective_status(None, A) == su.UNCHECKED
    assert su.effective_status({}, A) == su.UNCHECKED


def test_unknown_needs_no_head_to_stay_valid(tmp_path):
    # "nothing to compare" (no remote / not installed) is not a verdict ABOUT a commit.
    assert su.effective_status(_entry(su.UNKNOWN, at=""), A) == su.UNKNOWN


def test_error_is_head_bound_and_never_green(tmp_path):
    assert su.effective_status(_entry(su.ERROR, at=A), A) == su.ERROR
    assert su.effective_status(_entry(su.ERROR, at=A), B) == su.UNCHECKED


def test_unchecked_is_never_a_stored_status():
    # It is derived, so it can never be mistaken for a check that actually ran.
    assert su.UNCHECKED not in su._STORED
