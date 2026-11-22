"""Dash plan c9: on a Home radio card each component row and the RX-TX Monitor sit in a light box (1px line border,
6px radius, tinted background, no separator lines between rows); below 480 px the Apps-style inset tightening applies
to .runstack, the boxes and the Monitor (.3rem .35rem). The Apps page's own phone insets are unchanged. Computed styles
in a real browser (meshcore@r3 running, r3's daemon up)."""
from lhpc.core import instances, radios
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem, Listener
from lhpc.core.services import ControllerService

_READY = b"STATUS RADIO=READY ID=r3 BOUND=- TXMODE=MANAGED\n"
_STYLE = """() => { const cs = e => getComputedStyle(e), card = document.getElementById('radio-r3');
  const box = e => ({bw: cs(e).borderTopWidth, bl: cs(e).borderLeftWidth, br: cs(e).borderTopLeftRadius,
                     bg: cs(e).backgroundColor, pad: [cs(e).paddingTop, cs(e).paddingLeft]});
  return {rows: [...card.querySelectorAll('ul.complist > li.crow')].map(box), mon: box(card.querySelector('details.monitor')),
          run: box(card.querySelector('.runstack')), root: parseFloat(cs(document.documentElement).fontSize)}; }"""


def _home(flask_page, tmp_path):
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    (tmp_path / "config" / "radios.toml").write_text(radios.render((
        radios.Radio("r1", "low", "loraham", "433"), radios.Radio("r3", "mstick", "meshstick-1262", "868",
                                                                  usb_serial="MSTICK01"))))
    plain = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    inst, table = instances.parse_target(plain, "meshcore@r3", "start")[1], {}
    for cid in ("meshcore-node", "meshcore-gps"):
        comp = plain.stack("meshcore").component(cid)
        opt, val = instances._token(plain, comp, inst, "868")
        table[700 + len(table)] = [comp.process.exec_path or comp.process.exec_name,
                                   *(("-m", comp.process.entry) if comp.process.entry else ()),
                                   *[a for a in comp.process.all_args if a != opt], opt, val]
    return flask_page(path="/", cmdlines=table, unix_replies={"/tmp/loraconf-r3.sock": _READY},
                      listeners=[Listener("ipv4", "127.0.0.1", 5399, 1)])[0]


def _px(rem, root):
    return f"{round(rem * root, 2):g}px"


def test_rows_and_monitor_are_boxes_with_phone_insets(flask_page, tmp_path):
    page = _home(flask_page, tmp_path)
    page.set_viewport_size({"width": 1280, "height": 900})
    s = page.evaluate(_STYLE)
    assert len(s["rows"]) >= 2, s
    for b in s["rows"] + [s["mon"]]:
        assert (b["bw"], b["bl"], b["br"]) == ("1px", "1px", "6px") and b["bg"] not in ("rgba(0, 0, 0, 0)", "transparent"), b
        assert b["pad"] == [_px(.45, s["root"]), _px(.6, s["root"])], b
    page.set_viewport_size({"width": 390, "height": 900})
    s = page.evaluate(_STYLE)
    for b in s["rows"] + [s["mon"], s["run"]]:
        assert b["pad"] == [_px(.3, s["root"]), _px(.35, s["root"])], b
    page.context.close()


def test_the_apps_phone_insets_are_unchanged(flask_page, tmp_path):
    page = _home(flask_page, tmp_path)
    page.set_viewport_size({"width": 390, "height": 900})
    page.goto(page.url.replace("/#", "/").rstrip("/") + "/stacks?open=meshcore", wait_until="load")
    pad = page.evaluate("() => { const e = document.querySelector('.stackrow-body'); const c = getComputedStyle(e);"
                        " return [c.paddingTop, c.paddingLeft, parseFloat(getComputedStyle(document.documentElement).fontSize)]; }")
    assert pad[:2] == [_px(.25, pad[2]), _px(.35, pad[2])], pad
    assert page.evaluate("() => document.querySelectorAll('li.crow, details.monitor, .runstack').length") == 0
    page.context.close()
