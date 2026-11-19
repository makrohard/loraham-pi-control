"""F3 (his pick b, scope libraries only): a library's Info card has no TX row ("TX disabled" on radiolib, libch341,
... said nothing); every other component keeps it, the transmitting firmware meshcom-firmware included."""
from htmlq import parse

LIBRARIES = {"daemon": ("radiolib", "libch341"), "meshtastic": ("meshtastic-web",),
             "meshcore": ("openhop-repeater-src",), "reticulum": ("rns-lora-interface",)}


def _rows(client, sid, card):
    doc = parse(client.get(f"/stacks/{sid}/body").get_data(as_text=True))
    el = doc.by_id(card)
    assert el is not None, (sid, card)
    rows = {}
    for tr in doc.within(el).find("tr"):
        th, td = doc.within(tr).find("th"), doc.within(tr).find("td")
        if th and td:
            rows[th[0].text] = td[0].text
    return rows


def test_a_library_card_has_no_tx_row(web):
    c = web()
    for sid, libs in LIBRARIES.items():
        for lib in libs:
            rows = _rows(c, sid, f"comp-{lib}--info")
            assert "Run state" in rows and "TX" not in rows, (lib, rows)


def test_the_other_cards_keep_their_tx_row(web):
    c = web()
    assert "(TX-capable)" in _rows(c, "daemon", "stack-info-daemon")["TX"]
    assert "(TX-capable)" in _rows(c, "meshcom", "comp-meshcom-firmware--info")["TX"]
    # scope = libraries only (his A): a service that is not TX-capable keeps its row, without the TX-capable note
    webui = _rows(c, "meshcore", "comp-meshcore-webui--info")
    assert "TX" in webui and "(TX-capable)" not in webui["TX"], webui
