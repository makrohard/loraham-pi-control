"""Home's Webserver and System cards are both 984 px at 2560 (equal widths), the
Webserver card's left edge is r1's and its right edge r2's (two radio cards + their gap); both in one wrapping row
below the radio grid, side by side (System on r3's column) at 2560, stacked at 1920; 100% of the page at 360."""
from lhpc.core import radios

_GEO = """() => { const r = s => document.querySelector(s).getBoundingClientRect(), main = r('main.wrap');
  const pad = parseFloat(getComputedStyle(document.querySelector('main.wrap')).paddingLeft);
  return {ws: [Math.round(r('.card.wsbox').left), Math.round(r('.card.wsbox').right)],
          sys: [Math.round(r('.card.sysbox').left), Math.round(r('.card.sysbox').width)],
          tops: [Math.round(r('.card.wsbox').top), Math.round(r('.card.sysbox').top)], r3: Math.round(r('#radio-r3').left),
          gridBottom: Math.round(r('.radiogrid').bottom), duo: !!document.querySelector('.homeduo > .card.wsbox + .card.sysbox'),
          r1: Math.round(r('#radio-r1').left), r2: Math.round(r('#radio-r2').right), inner: Math.round(main.width - 2 * pad),
          scroll: document.documentElement.scrollWidth - innerWidth}; }"""


def test_webserver_and_system_are_the_two_radio_cards_width(flask_page, tmp_path):
    page, _posts = flask_page(path="/")
    (tmp_path / "config" / "radios.toml").write_text(radios.render((
        radios.Radio("r1", "low", "loraham", "433"), radios.Radio("r2", "high", "loraham", "868"),
        radios.Radio("r3", "mstick", "meshstick-1262", "868", usb_serial="MSTICK01"))))
    page.set_viewport_size({"width": 2560, "height": 1200})
    page.reload(wait_until="load")
    g = page.evaluate(_GEO)
    assert g["ws"] == [g["r1"], g["r2"]] and g["ws"][1] - g["ws"][0] == 984 and g["sys"][1] == 984, g
    assert g["duo"] and g["tops"][0] == g["tops"][1] >= g["gridBottom"] and g["sys"][0] == g["r3"], g    # side by side
    page.set_viewport_size({"width": 1920, "height": 1200})
    g = page.evaluate(_GEO)
    assert g["tops"][1] > g["tops"][0] >= g["gridBottom"] and g["sys"][0] == g["ws"][0], g                # stacked
    page.set_viewport_size({"width": 360, "height": 900})
    g = page.evaluate(_GEO)
    assert g["ws"][1] - g["ws"][0] == g["inner"] == g["sys"][1] and g["scroll"] <= 0, g
    page.context.close()
