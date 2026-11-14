"""A8b (0.14.0 manual test): the MeshCom start note named r1's web port (:18083) on every radio; the note names no
port (his decision Q-B1-2: the card's link and the [run] line show each radio's own). Through start_notes, the
dashboard's note for a started component."""
import re

from lhpc.core.outcomes import CompResult, Outcome
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ActionResult, ControllerService


def test_the_meshcom_start_note_names_no_port(tmp_path):
    # red-before (b3fc6e60): "... Its web UI at :18083 is sluggish until then ..."
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    started = ActionResult(True, "ok", results=(
        CompResult(component="meshcom-qemu", action="start", outcome=Outcome.VERIFIED),))
    (note,) = svc.start_notes(started)
    assert "boots in ~1 min on a Pi 5 and ~6–14 min on a Pi Zero 2W" in note      # the guidance stays (anchor)
    assert not re.search(r":\d{2,5}\b", note), note
