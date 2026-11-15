"""Golden, U7 (plan v6, D5): a healthy stack start writes no last-start marker, and a stop has none to
retire. The kiss box's source carries an ownership-registry record, so the start can name the commit it
ran. Red at 912c37d5: the start wrote state/last-start/kiss.json."""

import time

import pytest

from lhpc.core import source_registry

pytestmark = pytest.mark.needs_session


def test_a_kiss_start_writes_no_last_start_marker(kiss_box, run_op):
    """intended: the start of kiss writes its ownership record and no last-start marker (U7, D5)."""
    box = kiss_box()
    stack = box.svc.stack("kiss")
    for c in stack.components:
        if c.source is not None:
            assert source_registry.write_record(box.svc._paths, source_registry.RegistryRecord(
                c.source.path, "", "pinned", "a" * 40, time.time(), "", (c.id,)))
    start = run_op(box.root, lambda: box.svc.start("kiss", apply=True))
    assert start.res.ok, start.res.summary
    touched = start.files["added"] + start.files["changed"]
    assert any(t.startswith("state/owned/") for t in touched), touched    # anchor: the start recorded itself
    assert not any(t.startswith("state/last-start/") for t in touched), touched
    assert not (box.root / "state" / "last-start").exists()
