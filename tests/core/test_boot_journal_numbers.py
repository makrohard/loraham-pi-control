"""L24 F4: the boot-restore journal's writer identity holds numbers its writer can write. The input matrix measured
validate_journal accepting process_start_time NaN, ±inf, negative, 0 and a 401-digit integer, and a 401-digit pid.
The writer stores the kernel's start time (an int; 0 when it could not be read) and os.getpid(): anything else
is a journal lhpc did not write, so it reads UNSAFE (automatic work blocked), never a live or a dead run."""
import json

import pytest

from lhpc.core import boot_restore as br
from lhpc.core.paths import Paths

# JSON text of the field, as Python's json writes and reads it (NaN/Infinity included).
BAD_START = {"nan": "NaN", "inf": "Infinity", "-inf": "-Infinity", "negative": "-5", "float": "7.5",
             "401-digits": "1" + "0" * 400, "2^64": str(1 << 64)}
BAD_PID = {"401-digits": "1" + "0" * 400, "above-pid-max": str((1 << 22) + 1), "float": "42.0"}


def _load(tmp_path, field, text):
    j = br.new_journal(boot_id="B", pid=42, process_start_time=7, items=[])
    raw = json.dumps(j).replace(f'"{field}": {j[field]}', f'"{field}": {text}')
    assert text in raw
    p = br.journal_path(Paths(runtime_root=tmp_path))
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(raw)
    return br.load_journal(Paths(runtime_root=tmp_path))[1]


@pytest.mark.parametrize("text", list(BAD_START.values()), ids=list(BAD_START))
def test_an_impossible_start_time_makes_the_journal_unsafe(tmp_path, text):
    assert _load(tmp_path, "process_start_time", text).partition(":")[0] == "unsafe"


@pytest.mark.parametrize("text", list(BAD_PID.values()), ids=list(BAD_PID))
def test_an_impossible_pid_makes_the_journal_unsafe(tmp_path, text):
    assert _load(tmp_path, "pid", text).partition(":")[0] == "unsafe"


@pytest.mark.parametrize("text", ["0", "7", str((1 << 64) - 1)], ids=["unread-zero", "small", "u64-max"])
def test_the_start_times_the_writer_writes_stay_valid(tmp_path, text):
    assert _load(tmp_path, "process_start_time", text) == "valid"
