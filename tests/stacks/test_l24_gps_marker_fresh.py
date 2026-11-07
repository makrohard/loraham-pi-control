"""L24 F1: a GPS readiness marker is fresh only for a real, finite, recent `updated`. The input matrix measured
NaN, "nan", +inf and a timestamp an hour ahead as FRESH, and a 401-digit integer raising OverflowError out of
the status read. The bridge writes `int(time.time())`; a marker up to MARKER_MAX_AGE_S ahead stays fresh (a
clock stepped back), anything further ahead does not."""
import pytest

from lhpc.core import gps

NOW = 1_000_000.0
MAX = gps.MARKER_MAX_AGE_S

# (the `updated` value, fresh?) — the probe's classes, then the window's edges.
CASES = [
    (float("nan"), False), ("nan", False), (float("inf"), False), (float("-inf"), False),
    (10 ** 400, False), (True, False), ("5", False), (None, False), ([1], False), ({"a": 1}, False),
    (-5, False), (0, False),
    (NOW + 3600, False),                 # an hour ahead: not this run's refresh
    (NOW + MAX + 1, False),              # just past the forward window
    (NOW + MAX, True),                   # a clock stepped back by the whole window
    (NOW + 5, True),
    (int(NOW) - 10, True),               # what the bridge writes
    (NOW - MAX, True),
    (NOW - MAX - 0.5, False),
]


@pytest.mark.parametrize("updated,fresh", CASES, ids=[repr(c[0])[:20] for c in CASES])
def test_marker_is_fresh_only_for_a_real_recent_number(updated, fresh):
    assert gps.marker_is_fresh({"updated": updated}, NOW) is fresh


def test_a_missing_updated_is_never_fresh():
    assert gps.marker_is_fresh({}, NOW) is False
