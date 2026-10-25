"""Golden: T-R1's L3 part (A §2.3, PROOF §1) — at L3 each band is its own instance, so what a
stack's start, a config save while it runs, and its stop on 433 write never names its 868
instance: the ownership record, the running, stop-intent, known-working (last-start candidate)
and restart-required markers and the start log carry 433 or no band. The box is the golden
`kiss_box` (kiss over a daemon serving 433). The box's sources have no registry records, so the
start's composition is given (a stub of `_stack_composition_entries` only); the candidate is
written by the start's own code."""

import json
import re
import types

import pytest

from lhpc.core.services import ControllerService

pytestmark = pytest.mark.needs_session

# what would name the other instance: its band in a path, or one of its daemon sockets
OTHER = re.compile(r"868|lora(f|conf)?868\.sock")


def _touched(run):
    return run.files["added"] + run.files["changed"]


def _text(root, rel):
    """A touched file's text; an ownership record is listed with `<pid>__<nonce>` placeholders."""
    paths = (sorted(root.glob(rel.replace("<pid>__<nonce>", "*"))) if "<pid>" in rel
             else [root / rel])
    return "".join(p.read_text(errors="replace") for p in paths if p.is_file())


def _clean(box, run):
    for rel in _touched(run):
        assert not OTHER.search(rel), rel
        assert not re.search(r"lora(f|conf)?868f?\.sock", _text(box.root, rel)), rel     # raw, framed, conf


@pytest.mark.parametrize("sock", ["/tmp/lora868.sock", "/tmp/lora868f.sock", "/tmp/loraconf868.sock"])
def test_the_clean_check_names_each_868_socket_spelling_in_a_touched_file(tmp_path, sock):
    """intended: the raw, the framed and the conf socket of 868, written into a touched file, each fail the check
    (G2-B2 N3); 433's own socket passes."""
    (tmp_path / "state").mkdir()
    (tmp_path / "state" / "x.json").write_text(f'{{"socket": "{sock}"}}')
    box, run = types.SimpleNamespace(root=tmp_path), types.SimpleNamespace(files={"added": ["state/x.json"],
                                                                                    "changed": []})
    with pytest.raises(AssertionError):
        _clean(box, run)
    (tmp_path / "state" / "x.json").write_text('{"socket": "/tmp/lora433f.sock"}')
    _clean(box, run)                                       # 433's own socket passes


def _band(box, rel):
    return json.loads((box.root / rel).read_text()).get("band", "")


def test_a_stack_on_433_never_names_its_868_instance(kiss_box, run_op, monkeypatch):
    """intended: the start, a save while running, and the stop of kiss on 433 touch only 433's
    or band-less state."""
    entry = {"commit": "a" * 40, "selector": "pinned", "remote": "r", "source_rel": "src/x"}
    monkeypatch.setattr(ControllerService, "_stack_composition_entries",
                        lambda self, sid: {"loraham-kiss-tnc": entry})
    box = kiss_box()
    start = run_op(box.root, lambda: box.svc.start("kiss", apply=True))
    assert start.res.ok, start.res.summary
    assert _touched(start), "the start wrote nothing"
    _clean(box, start)
    recs = sorted((box.root / "state" / "owned").glob("*.json"))
    assert recs and all(json.loads(r.read_text())["band"] == "433" for r in recs)
    assert (box.root / "state" / "running" / "kiss.band").read_text().strip() == "433"
    assert "state/last-start/kiss.json" in _touched(start)                  # known-working
    assert _band(box, "state/last-start/kiss.json") == "433"
    save = run_op(box.root, lambda: box.svc.save_config_bundle("kiss", values={"tx_freq": "433.800"},
                                                               band="433"))
    assert save.res.ok, save.res.summary
    assert "state/restart-required/kiss.json" in _touched(save)            # restart-required
    _clean(box, save)
    marker = json.loads((box.root / "state/restart-required/kiss.json").read_text())
    assert marker["band"] == "433" and set(marker["launched"]) == {"433|loraham-kiss-tnc|tx_freq"}
    stop = run_op(box.root, lambda: box.svc.stop("kiss", apply=True))
    assert stop.res.ok, stop.res.summary
    _clean(box, stop)
