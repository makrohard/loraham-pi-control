"""The console's TX test transmits only after the operator's explicit opt-in, and only under a
valid station callsign.

The one gate across the three conditions, at the widest seam (`POST /action op=test-tx`): the first
POST only renders the TX confirmation (the opt-in) and transmits nothing; the confirmed POST is
refused, with nothing transmitted, while the operator callsign is missing or a placeholder; with a
valid callsign it sends exactly one frame per READY band, identified with that callsign. The
transmit itself is the seam (`Lifecycle.run_daemon_tx_test`); nothing reaches a radio.
"""
from __future__ import annotations

import pytest

from lhpc.core import validators
from lhpc.core.config import save_operator_config
from lhpc.core.lifecycle import Lifecycle, TxTestResult
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService
from seams import seed_built

pytestmark = [pytest.mark.contract, pytest.mark.safety("RF-TX-opt-in")]

READY = b"STATUS RADIO=READY TXMODE=MANAGED\n"


@pytest.fixture
def box(tmp_path, web, monkeypatch):
    """The console over a box whose daemon serves both bands, both READY; returns (client, svc,
    sent) — `sent` lists every (band, payload) the TX test hands to the radio."""
    seed_built(tmp_path, "loraham-daemon/loraham_daemon/loraham_daemon")
    sys_ = FakeSystem(unix_replies={"/tmp/loraconf433.sock": READY,
                                    "/tmp/loraconf868.sock": READY}).system
    svc = ControllerService(system=sys_, paths=Paths(runtime_root=tmp_path))
    sent = []

    def transmit(self, band, payload):
        sent.append((band, payload))
        return TxTestResult(ok=True, band=band, txok_before=0, txok_after=1, detail="stub")
    monkeypatch.setattr(Lifecycle, "run_daemon_tx_test", transmit)
    return web(service_factory=lambda: svc), svc, sent


def _post(client, csrf, *, confirmed):
    form = {"_csrf": csrf(client), "op": "test-tx", "target": "daemon"}
    if confirmed:
        form["confirmed"] = "yes"
    return client.post("/action", data=form)


def test_without_the_confirmation_nothing_is_transmitted(box, csrf, set_call):
    client, svc, sent = box
    set_call(svc)
    r = _post(client, csrf, confirmed=False)
    assert r.status_code == 200                   # the confirmation page — the opt-in
    assert sent == []


@pytest.mark.parametrize("callsign", ["", *validators._PLACEHOLDER_BASES])
def test_a_confirmed_test_without_a_valid_callsign_transmits_nothing(box, csrf, callsign):
    client, svc, sent = box
    if callsign:
        save_operator_config(svc._paths, callsign)
        svc._invalidate_config()
    r = _post(client, csrf, confirmed=True)
    assert r.status_code in (302, 303)
    assert sent == []
    with client.session_transaction() as sess:
        assert [cat for cat, _msg in sess.get("_flashes", [])] == ["warn"]


def test_a_confirmed_test_with_a_callsign_sends_one_identified_frame_per_band(box, csrf, set_call):
    client, svc, sent = box
    set_call(svc)
    call = svc.config().operator.callsign
    r = _post(client, csrf, confirmed=True)
    assert r.status_code in (302, 303)
    assert sent == [("433", f"LHPC TX TEST DE {call}"), ("868", f"LHPC TX TEST DE {call}")]
