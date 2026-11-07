"""L24 F7: a cache's checked_at is a time stamp: a non-negative int below 2**63 (the writers store int(time.time())).
The input matrix measured a negative and a 401-digit checked_at accepted; the page then said "just now" for the
401 digits (max(0, now - checked_at)) and days ago for a negative one. Now such a cache, or such an entry, reads as
not checked — the same as any malformed cache."""
import pytest

from lhpc.core import selfupdate, stackupdates
from lhpc.core.paths import Paths

BAD = {"negative": -5, "401-digits": 10 ** 400, "2^63": 1 << 63}
GOOD = {"zero": 0, "now": 1762520000, "int64-max": (1 << 63) - 1}


@pytest.mark.parametrize("v", list(BAD.values()), ids=list(BAD))
def test_a_stack_check_cache_with_an_impossible_stamp_reads_unchecked(tmp_path, v):
    paths = Paths(runtime_root=tmp_path)
    entry = {"status": stackupdates.UP_TO_DATE, "checked_at": 1762520000}
    stackupdates.write_cache(paths, {"schema_version": stackupdates.CACHE_SCHEMA_VERSION, "checked_at": v,
                                     "components": {"a": entry}})
    assert stackupdates.view(paths) == {"checked_at": 0, "components": {}}     # the whole cache: unchecked


@pytest.mark.parametrize("v", list(BAD.values()), ids=list(BAD))
def test_a_component_entry_with_an_impossible_stamp_is_dropped(tmp_path, v):
    paths = Paths(runtime_root=tmp_path)
    good = {"status": stackupdates.UP_TO_DATE, "checked_at": 1762520000}
    stackupdates.write_cache(paths, {"schema_version": stackupdates.CACHE_SCHEMA_VERSION, "checked_at": 1762520000,
                                     "components": {"a": good, "b": dict(good, checked_at=v)}})
    assert set(stackupdates.view(paths)["components"]) == {"a"}


@pytest.mark.parametrize("v", list(BAD.values()), ids=list(BAD))
def test_a_self_update_cache_with_an_impossible_stamp_reads_unchecked(tmp_path, v):
    paths = Paths(runtime_root=tmp_path)
    selfupdate.write_cache(paths, {"schema_version": selfupdate.CACHE_SCHEMA_VERSION, "checked_at": v})
    assert selfupdate.read_cache(paths) == {}
    selfupdate.write_cache(paths, {"schema_version": selfupdate.CACHE_SCHEMA_VERSION,
                                   "identity": {"ok": True, "status": "ok", "reason": "", "checked_at": v}})
    assert selfupdate.read_cache(paths) == {}


@pytest.mark.parametrize("v", list(GOOD.values()), ids=list(GOOD))
def test_real_stamps_stay_valid(tmp_path, v):
    paths = Paths(runtime_root=tmp_path)
    stackupdates.write_cache(paths, {"schema_version": stackupdates.CACHE_SCHEMA_VERSION, "checked_at": v,
                                     "components": {}})
    assert stackupdates.view(paths)["checked_at"] == v
    selfupdate.write_cache(paths, {"schema_version": selfupdate.CACHE_SCHEMA_VERSION, "checked_at": v})
    assert selfupdate.read_cache(paths).get("checked_at") == v
