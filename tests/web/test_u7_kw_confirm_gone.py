"""U7 (plan v6, D3): the "Confirm this stack as working" button and its route are gone; the stack page
still renders. Red at 912c37d5: the route answered the POST (a redirect), the body offered the button."""

import time

from lhpc.core import source_registry
from lhpc.core.paths import Paths


def _running_chat_with_an_old_marker(tmp_path):
    """What made the offer in 0.13.x: a last-start marker + a matching registry record, chat running."""
    import json
    commit = "a" * 40
    (tmp_path / "src" / "LoRaHAM_Daemon").mkdir(parents=True, exist_ok=True)
    entries = {"loraham-chat": {"commit": commit, "selector": "dev", "remote": "",
                                "source_rel": "src/LoRaHAM_Daemon"}}
    m = tmp_path / "state" / "last-start" / "chat.json"
    m.parent.mkdir(parents=True, exist_ok=True)
    m.write_text(json.dumps({"version": 1, "stack": "chat", "band": "433", "started_at": time.time(),
                             "entries": entries, "hash": "f" * 64}))
    assert source_registry.write_record(Paths(runtime_root=tmp_path), source_registry.RegistryRecord(
        "src/LoRaHAM_Daemon", "", "dev", commit, time.time(), "", ("loraham-chat",)))


def test_the_confirm_route_is_gone_while_the_stack_page_renders(tmp_path, web, csrf):
    _running_chat_with_an_old_marker(tmp_path)
    c = web(cmdlines={555: ["loraham_chat"]})
    body = c.get("/stacks/chat/body")
    assert body.status_code == 200                              # anchor: the stack body renders
    text = body.get_data(as_text=True)
    assert 'id="stack-install-chat"' in text
    assert "Confirm this stack as working" not in text and "known-working/confirm" not in text
    r = c.post("/stacks/chat/known-working/confirm", data={"_csrf": csrf(c)})
    assert r.status_code == 404, r.status_code
