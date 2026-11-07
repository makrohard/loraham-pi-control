"""L24 F6: a restart-required marker's created_at is a real, finite, non-negative time (the writer stores
time.time()), or the marker is UNSAFE (it still shows as restart required, with the resolve hint). The input
matrix measured true, NaN, ±inf, negative and a 401-digit integer read as a VALID marker."""
import json

import pytest

from lhpc.core import restart_required
from lhpc.core.paths import Paths

BAD = {"bool": "true", "nan": "NaN", "inf": "Infinity", "-inf": "-Infinity", "negative": "-5",
       "401-digits": "1" + "0" * 400}
GOOD = {"time": "1762520000.25", "int": "1762520000", "zero": "0"}


def _read(tmp_path, text):
    paths = Paths(runtime_root=tmp_path)
    p = restart_required.marker_path(paths, "chat")
    p.parent.mkdir(parents=True, exist_ok=True)
    body = {"version": 1, "stack": "chat", "mode": "restart", "params": [], "created_at": 0}
    p.write_text(json.dumps(body).replace('"created_at": 0', f'"created_at": {text}'))
    return restart_required.read_marker(paths, "chat")


@pytest.mark.parametrize("text", list(BAD.values()), ids=list(BAD))
def test_an_impossible_time_makes_the_marker_unsafe(tmp_path, text):
    got = _read(tmp_path, text)
    assert got is not None and got.get("unsafe") is True, got


@pytest.mark.parametrize("text", list(GOOD.values()), ids=list(GOOD))
def test_a_real_time_keeps_the_marker_valid(tmp_path, text):
    got = _read(tmp_path, text)
    assert got is not None and not got.get("unsafe"), got
