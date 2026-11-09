"""W5 (K8's web part, CLEANUP-DESIGN-v13 13.3, W321): a stack component's "Radio config" link opens the daemon's
Settings on the stack's shown radio (`?radio=`), never on a band."""
from urllib.parse import parse_qs, urlsplit

from htmlq import parse


def test_the_radio_config_link_names_the_shown_radio_not_a_band(web):
    doc = parse(web().get("/stacks?open=kiss").get_data(as_text=True))
    links = [a["href"] for a in doc.find("a", class_="act") if "cfg=daemon" in (a["href"] or "")]
    assert links, "no Radio config link"
    for href in links:
        q = parse_qs(urlsplit(href).query)
        assert "band" not in q and q.get("radio") == ["r1"], href                  # kiss@r1: the 433 radio
