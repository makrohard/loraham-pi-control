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


def test_g1_w5_n2_the_link_follows_the_shown_instances_radio(web, tmp_path):
    # Pin (G1-W5 N2, DEC-W384bn): two radios on kiss's band; the page shows kiss@r2 -> its Radio config opens r2
    import radios_fixture
    from lhpc.core import radios
    radios_fixture.write(tmp_path, (radios.Radio("r1", "a", "uputronics", "433", ce="ce0"),
                                    radios.Radio("r2", "b", "uputronics", "433", ce="ce1")))
    doc = parse(web().get("/stacks?radio=r2&open=kiss").get_data(as_text=True))
    links = [a["href"] for a in doc.find("a", class_="act") if "cfg=daemon" in (a["href"] or "")]
    assert links and all(parse_qs(urlsplit(h).query).get("radio") == ["r2"] for h in links), links
