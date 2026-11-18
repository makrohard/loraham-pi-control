"""LOGS-IN-TREE (plan v4, gate 1 r1 B1-B4 N1-N4, r2 N5-N7): the application logs and the RF logs are normal entries
of the menu tree. Every Apps row ends with Logs (one entry per program that writes a log, then its RF log); a log
entry is a closed panel on the Apps page (its last 30 lines on open and on Reload), its page is today's log page,
and the log pages get their breadcrumbs from the tree. Red on the base 70944217 unless a test says otherwise."""

from __future__ import annotations

import html
import re
from html.parser import HTMLParser
from urllib.parse import parse_qs, urlsplit

import pytest
from htmlq import parse
from lhpc.core import config as cfgmod
from lhpc.core import radio_start
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService

# N4: an INDEPENDENT inventory of the shipped manifest, by hand: each stack -> its programs that write a log (Home's
# log links), and its RF log: "own" (the job its Logs › RF log opens), "note" (another stack writes it) or None.
_INVENTORY = {
    "daemon": (["loraham-daemon"], ("own", "loraham-daemon")),
    "chat": ([], None),
    "voice": (["loraham-voice"], None),
    "kiss": (["loraham-kiss-tnc", "loraham-kiss-serial"], ("own", "loraham-kiss-tnc", "rf-kiss.log")),
    "graywolf": (["graywolf"], ("note", "kiss")),
    "meshtastic": (["meshtastic-gps", "meshtastic"], ("own", "meshtastic", "rf-meshtastic.log")),
    "meshcom": (["meshcom-bridge", "meshcom-gps", "meshcom-gps-relay", "meshcom-qemu"],
                ("own", "meshcom-bridge", "rf-meshcom.log")),
    "meshcore": (["meshcore-gps", "meshcore-node", "meshcore-webui", "meshcore-cli"],
                 ("own", "meshcore-node", "rf-meshcore.log")),
    "reticulum": (["rns", "lxmd", "sideband", "meshchat"], ("own", "rns", "rf-reticulum.log")),
}
_CTL = "LoRaHAM Pi Control"


def _client(web, tmp_path, radios=False):
    """A fake box; `radios`: the two-radio hardware list (r1, r2)."""
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    p = Paths(runtime_root=tmp_path)
    if radios:
        assert radio_start.setup_radios(p, "uputronics")[0]
        cfgmod.save_hardware_setup(p, "uputronics")
    svc = ControllerService(system=FakeSystem().system, paths=p)
    return web(service_factory=lambda: svc), svc


def _html(c, path):
    r = c.get(path)
    assert r.status_code == 200, (path, r.status_code)
    return r.get_data(as_text=True)


class _Tree(HTMLParser):
    """The tree page's entries: (its path below Apps, its link). A level with levels below is a <details>; its own
    link reads "<label> page ›"."""

    def __init__(self):
        super().__init__()
        self.uls, self.path, self.entries, self._summary, self._a = [], [], [], None, None

    def handle_starttag(self, tag, attrs):
        if tag == "ul":
            self.uls.append("navtree" in (dict(attrs).get("class") or ""))
        elif any(self.uls) and tag == "summary":
            self._summary = ""
        elif any(self.uls) and tag == "a":
            self._a = [dict(attrs).get("href", ""), ""]

    def handle_data(self, data):
        if self._summary is not None:
            self._summary += data
        elif self._a is not None:
            self._a[1] += data

    def handle_endtag(self, tag):
        if tag == "ul" and self.uls:
            self.uls.pop()
        elif tag == "summary" and self._summary is not None:
            self.path.append(" ".join(self._summary.split()))
            self._summary = None
        elif tag == "details" and any(self.uls):
            self.path.pop()
        elif tag == "a" and self._a is not None:
            (href, text), self._a = self._a, None
            text = " ".join(text.split())
            self.entries.append((tuple(self.path) + (() if text.endswith(" page ›") else (text,)), href))


def _tree(c, radio=""):
    tree = _Tree()
    tree.feed(_html(c, "/stacks/menus" + (f"?radio={radio}" if radio else "")))
    return dict(tree.entries)


def _q(href):
    u = urlsplit(href)
    return u.path, {k: v[0] for k, v in parse_qs(u.query).items()}


# ---- the tree (plan §1, test 1, N4) ---------------------------------------------------------------------------

def test_every_app_ends_with_its_logs_its_programs_then_its_rf_log(tmp_path, web):
    c, svc = _client(web, tmp_path)
    tree = _tree(c)
    for sid, (programs, rf) in _INVENTORY.items():
        st = svc.stack(sid)
        below = {p[-1]: href for p, href in tree.items() if p[:-1] == (st.name, "Logs")}
        want = {st.component(cid).name: f"/logs/{cid}" for cid in programs}
        assert {k: _q(v)[0] for k, v in below.items() if k != "RF log"} == want, sid
        if rf is not None:
            assert list(below)[-1] == "RF log", sid    # programs, then the RF log
        if rf is None:
            assert "RF log" not in below, sid
        elif rf[0] == "own":
            path, q = _q(below["RF log"])
            assert path == f"/logs/{rf[1]}" and (q["job"] == rf[2] if len(rf) > 2 else True), (sid, below["RF log"])
        else:                                          # Graywolf: an entry with no log of its own (its page: the note)
            assert _q(below["RF log"])[0] == f"/stacks/{sid}/menu/rflog-{sid}", sid
        assert (st.name, "Logs") in tree, sid          # every row has its Logs (chat's says that none writes one)
        # Logs is the row's last entry
        assert [p for p in tree if len(p) == 2 and p[0] == st.name][-1] == (st.name, "Logs"), sid


def test_the_apps_pages_logs_show_the_programs_then_the_rf_log(tmp_path, web):
    c, _svc = _client(web, tmp_path)
    for sid, (programs, rf) in _INVENTORY.items():
        doc = parse(_html(c, f"/stacks/{sid}/body"))
        ids = [d["id"] for d in doc.within(doc.by_id(f"logs-{sid}")).find("details")][1:]   # in page order
        n = len(programs)
        assert sorted(ids[:n]) == sorted(f"log-{cid}" for cid in programs), (sid, ids)
        assert ids[n:] == ([f"rflog-{sid}"] if rf else []), (sid, ids)                # the RF log last


def test_the_controller_the_webserver_and_the_firewall_end_with_their_logs(tmp_path, web):
    c, _svc = _client(web, tmp_path)
    tree = _tree(c)
    assert tree[(_CTL, "Logs")] == "/controller/logs"
    assert tree[(_CTL, "Webserver and mTLS proxy", "Logs")] == "/webserver/logs"         # after the rename (test 6)
    assert tree[(_CTL, "Firewall", "Log")] == "/firewall/logs"
    assert [p for p in tree if len(p) == 2 and p[0] == _CTL][-1] == (_CTL, "Logs")
    for parent in ("Webserver and mTLS proxy", "Firewall"):
        assert [p for p in tree if len(p) == 3 and p[:2] == (_CTL, parent)][-1][-1] in ("Logs", "Log")


def test_graywolfs_rf_log_entry_names_the_kiss_tnc_and_links_its_rf_log(tmp_path, web):
    c, svc = _client(web, tmp_path)
    doc = parse(_html(c, "/stacks/graywolf/body"))
    note = doc.by_id("rflog-graywolf")
    text = " ".join(note.text.split())
    assert f"{svc.stack('graywolf').name}'s RF traffic is written by the {svc.stack('kiss').name}" in text
    assert [_q(a["href"]) for a in doc.within(note).find("a")] == [("/logs/loraham-kiss-tnc", {"job": "rf-kiss.log"})]


# ---- the Apps page's panels (plan §2, v4 N2/N3) -----------------------------------------------------------------

def test_a_log_entry_is_a_closed_panel_with_reload_and_a_status_line(tmp_path, web):
    c, _svc = _client(web, tmp_path)
    body = _html(c, "/stacks/meshcom/body")
    doc = parse(body)
    for mid, src, kind in (("log-meshcom-bridge", "/api/logs/meshcom-bridge", "lines"),
                           ("rflog-meshcom", "/api/rflog/meshcom-bridge?job=rf-meshcom.log", "records")):
        panel = doc.by_id(mid)
        tag = re.search(r'<details\b[^>]*\bid="' + mid + r'"[^>]*>', body).group(0)
        assert " open" not in tag and panel["data-src"] == src and panel["data-kind"] == kind, mid
        inside = doc.within(panel)
        assert [b["type"] for b in inside.find("button")] == ["button"] and inside.find("button")[0].text == "Reload"
        status = inside.find("span", class_="logpanel-status")[0]
        assert status["role"] == "status" and status["aria-live"] == "polite"
        assert [p.text for p in inside.find("pre")] == [""]                    # empty until opened; no live region
        block = body[body.index(tag):body.index("</details>", body.index(tag))]
        assert re.search(r"<noscript><p[^>]*>The last 30 lines need JavaScript; .page ›. opens the full log\.</p>"
                         r"</noscript>", block), mid                          # without JS: a short note
    page = [a["href"] for a in doc.find("a", class_="pagelink")]
    assert "/logs/meshcom-bridge" in page and "/logs/meshcom-bridge?job=rf-meshcom.log" in page
    assert any(s.endswith("/logpanel.js") for s in (x["src"] for x in parse(_html(c, "/stacks")).find("script")
                                                    if x["src"]))


def test_the_controllers_panels_pick_their_source(tmp_path, web):
    c, _svc = _client(web, tmp_path)
    doc = parse(_html(c, "/stacks"))
    for mid, srcs in (("controller-logs", ["controller-web", "controller-selfupdate", "controller-boot-restore"]),
                      ("webserver-row--logs", ["webserver-error", "webserver-access"])):
        panel = doc.by_id(mid)
        opts = [o["value"] for o in doc.within(panel).find("option")]
        assert opts == [f"/api/logpreview/{s}" for s in srcs] and panel["data-src"] == opts[0], mid
    assert doc.by_id("firewall-row--log")["data-src"] == "/api/logpreview/firewall"


# ---- the preview route (v4 B2, G1-R2 N5/N6) ----------------------------------------------------------------------

_SOURCES = {"controller-web": ("controller", "web"), "controller-selfupdate": ("controller", "selfupdate"),
            "controller-boot-restore": ("controller", "boot-restore"), "webserver-error": ("webserver", "error"),
            "webserver-access": ("webserver", "access"), "firewall": ("firewall", "")}


def test_each_preview_source_answers_the_last_30_lines_of_its_own_reader(tmp_path, web, monkeypatch):
    def reader(kind):
        def read(self, *a, **kw):
            src = (a or ("",))[0] if kind != "firewall" else ""
            return f"/abs/{kind}/{src}.log", [f"{kind}:{src}:{i}" for i in range(40)]
        return read
    for kind in ("controller", "webserver", "firewall"):
        monkeypatch.setattr(ControllerService, f"{kind}_log_tail", reader(kind))
    c, _svc = _client(web, tmp_path)
    for source, (kind, src) in _SOURCES.items():
        r = c.get(f"/api/logpreview/{source}")
        assert r.status_code == 200 and r.headers["Cache-Control"] == "no-store", source
        assert r.get_json() == {"source": source, "lines": [f"{kind}:{src}:{i}" for i in range(10, 40)]}, source


@pytest.mark.parametrize("source", ["nope", "Firewall", "WEBSERVER-ERROR", "controller-Web", "firewall%00",
                                    "..%2Ffirewall", "firewall%2F", "%66irewall-x", "logs", ""])
def test_any_other_preview_source_is_404(tmp_path, web, source):
    c, _svc = _client(web, tmp_path)
    assert c.get(f"/api/logpreview/{source}").status_code == 404


# ---- one URL rule keeps the radio (v4 B3) -------------------------------------------------------------------------

def test_with_r2_selected_the_tree_and_page_links_open_r2s_logs(tmp_path, web):
    c, svc = _client(web, tmp_path, radios=True)
    name = svc.stack("daemon").name
    tree = _tree(c, "r2")
    assert _q(tree[(name, "Logs", svc.stack("daemon").component("loraham-daemon").name)]) == \
        ("/logs/loraham-daemon", {"radio": "r2"})
    assert _q(tree[(name, "Logs", "RF log")]) == ("/logs/loraham-daemon", {"job": "r2/rf-daemon.log", "radio": "r2"})
    page = [_q(a["href"]) for a in parse(_html(c, "/stacks/daemon/body?radio=r2")).find("a", class_="pagelink")]
    assert ("/logs/loraham-daemon", {"job": "r2/rf-daemon.log", "radio": "r2"}) in page
    assert ("/logs/loraham-daemon", {"radio": "r2"}) in page
    r = c.get("/stacks/daemon/menu/rflog-daemon?radio=r2")
    assert r.status_code == 302 and _q(r.headers["Location"].removeprefix("http://localhost")) == \
        ("/logs/loraham-daemon", {"job": "r2/rf-daemon.log", "radio": "r2"})
    # a radio the box does not have is not carried: the per-radio RF log opens on the stack's first radio
    assert _q(_tree(c, "r9")[(name, "Logs", "RF log")]) == ("/logs/loraham-daemon", {"job": "r1/rf-daemon.log",
                                                                                     "radio": "r1"})


# ---- the log pages: breadcrumbs from the tree, one heading (tests 2, 3, 6; v4 B1, B4; G1-R2 N7) -------------------

_PAGES = [
    ("/logs/meshcom-bridge", ["meshcom", "Logs", "@meshcom-bridge"]),
    ("/logs/meshcom-bridge?job=rf-meshcom.log", ["meshcom", "Logs", "RF log"]),
    ("/logs/loraham-kiss-tnc?job=rf-kiss.log", ["kiss", "Logs", "RF log"]),
    ("/controller/logs", [_CTL, "Logs"]),
    ("/webserver/logs?src=access", [_CTL, "Webserver and mTLS proxy", "Logs"]),
    ("/firewall/logs", [_CTL, "Firewall", "Log"]),
    ("/logs/radiolib", ["daemon", "Install", "Dependencies", "radiolib", "Log"]),        # B1: not a tree entry
    ("/logs/meshcore-node?job=install-meshcore.log", ["meshcore", "Install", "Job log"]),  # B4: a run's job log
]


@pytest.mark.parametrize("url, want", _PAGES, ids=[u for u, _w in _PAGES])
def test_a_log_page_has_the_trees_crumbs_and_one_heading(tmp_path, web, url, want):
    c, svc = _client(web, tmp_path)
    names = [svc.stack(w).name if svc.stack(w) is not None else w for w in want[:1]] + [
        svc.stack(want[0]).component(w[1:]).name if w.startswith("@") else w for w in want[1:]]
    body = _html(c, url)
    doc = parse(body)
    assert doc.find("nav", class_="crumbs")[0].text == " › ".join(["Home", "Apps", *names])
    h2 = re.findall(r"<h2[^>]*>(.*?)</h2>", body, re.S)
    assert [html.unescape(h) for h in h2] == [names[-1]]
    assert re.search(r'<h2 class="here">' + re.escape(h2[0]) + r"</h2></nav>", body)
    assert re.search(r'<div class="crumbline"><a class="crumbs-all" href="/stacks/menus[^"]*">All menus</a>', body)
    crumb_links = [a["href"] for a in doc.within(doc.find("nav", class_="crumbs")[0]).find("a")][2:]
    assert all(h.startswith(("/stacks/", "/logs/", "/controller/", "/webserver/", "/firewall/")) for h in crumb_links)


def test_a_log_pages_crumbs_follow_the_entrys_label(tmp_path, web, monkeypatch):
    # N4: the crumbs are the tree's chain, no names written by hand: a renamed entry renames its crumb and heading
    from lhpc.adapters.web import submenus
    real = submenus.stack_logs
    monkeypatch.setattr(submenus, "stack_logs", lambda stack: [dict(lp, label="Bridge log") if lp["id"] ==
                                                               "log-meshcom-bridge" else lp for lp in real(stack)])
    c, svc = _client(web, tmp_path)
    doc = parse(_html(c, "/logs/meshcom-bridge"))
    assert doc.find("nav", class_="crumbs")[0].text.endswith(f"{svc.stack('meshcom').name} › Logs › Bridge log")
    assert (svc.stack("meshcom").name, "Logs", "Bridge log") in _tree(c)


def test_the_firewall_log_has_no_back_link(tmp_path, web):
    c, _svc = _client(web, tmp_path)
    assert "← Firewall" not in _html(c, "/firewall/logs")


# ---- the Apps page's "logs" links go; Home keeps its (plan §2b, v4 B1) ----------------------------------------------

def test_the_apps_page_has_no_logs_link_but_the_dependencies_rows_and_no_rf_pill(tmp_path, web):
    c, svc = _client(web, tmp_path)
    pages = ["/stacks", "/stacks/controller/menu/controller-row", "/stacks/meshcore/menu/stack-install-meshcore"]
    pages += [f"/stacks/{s.id}/body" for s in svc.stacks()]
    for url in pages:
        body = _html(c, url)
        doc = parse(body)
        deps = [d for d in doc.find("details") if d["id"] and d["id"].endswith("--dependencies")]
        kept = {id(a) for d in deps for a in doc.within(d).find("a", class_="logslink")}
        assert [a["href"] for a in doc.find("a", class_="logslink") if id(a) not in kept] == [], url
        assert not [a for a in doc.find("a", class_="pill") if a.text.strip() == "RF log"], url
    doc = parse(_html(c, "/stacks/daemon/body"))
    dep = doc.by_id("stack-install-daemon--dependencies")
    assert "/logs/radiolib" in [a["href"] for a in doc.within(dep).find("a", class_="logslink")]   # B1: kept


def test_home_keeps_its_log_links(tmp_path, web):
    # green on the base too: Home is not the Apps page
    c, _svc = _client(web, tmp_path, radios=True)
    # (an instance row's config/logs links are pinned in test_home_instance_card_links)
    assert [a["href"] for a in parse(_html(c, "/")).find("a", class_="logslink")] == ["/webserver/logs"]
