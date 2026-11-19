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
from lhpc.adapters.web import submenus
from lhpc.core import config as cfgmod
from lhpc.core import radio_start
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService

# N4: an INDEPENDENT inventory of the shipped manifest, by hand: each stack -> its programs that write a log (Home's
# log links), and its RF log: "own" (the job its Logs › RF log opens), "note" (another stack writes it) or None.
# #75: a "per radio" file (every instance writes its own <rid>/<file>) opens the entry's radio's own file.
_INVENTORY = {
    "daemon": (["loraham-daemon"], ("own", "loraham-daemon")),
    "chat": ([], None),
    "voice": (["loraham-voice"], None),
    "kiss": (["loraham-kiss-tnc", "loraham-kiss-serial"], ("own", "loraham-kiss-tnc", "rf-kiss.log", "per radio")),
    "graywolf": (["graywolf"], ("note", "kiss")),
    "meshtastic": (["meshtastic-gps", "meshtastic"], ("own", "meshtastic", "rf-meshtastic.log", "per radio")),
    "meshcom": (["meshcom-bridge", "meshcom-gps", "meshcom-gps-relay", "meshcom-qemu"],
                ("own", "meshcom-bridge", "rf-meshcom.log", "per radio")),
    "meshcore": (["meshcore-gps", "meshcore-node", "meshcore-webui", "meshcore-cli"],
                 ("own", "meshcore-node", "rf-meshcore.log", "per radio")),
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


_GROUPS = ("Per radio", "Single")


def _q(href):
    u = urlsplit(href)
    return u.path, {k: v[0] for k, v in parse_qs(u.query).items()}


# ---- the tree (plan §1, test 1, N4) ---------------------------------------------------------------------------

def test_every_app_ends_with_its_logs_its_programs_then_its_rf_log(tmp_path, web):
    c, svc = _client(web, tmp_path)
    tree = _tree(c)
    for sid, (programs, rf) in _INVENTORY.items():
        st = svc.stack(sid)
        # UI-REORG c7: below Logs directly, or in its Per radio / Single group (the Per radio group ends with the RF log)
        entries = [(p, href) for p, href in tree.items() if p[:2] == (st.name, "Logs") and len(p) > 2
                   and p[-1] not in _GROUPS]
        below = {p[-1]: href for p, href in entries}
        want = {st.component(cid).name: f"/logs/{cid}" for cid in programs}
        assert {k: _q(v)[0] for k, v in below.items() if k != "RF log"} == want, sid
        if rf is not None:                             # programs, then the RF log (of the flat list / Per radio)
            assert [p[-1] for p, _h in entries if p[2] != "Single"][-1] == "RF log", sid
        if rf is None:
            assert "RF log" not in below, sid
        elif rf[0] == "own":
            path, q = _q(below["RF log"])
            job = f"{q.get('radio', '')}/{rf[2]}" if len(rf) > 3 else rf[2] if len(rf) > 2 else None
            assert path == f"/logs/{rf[1]}" and (q["job"] == job if job else True), (sid, below["RF log"])
            assert len(rf) < 4 or q.get("radio"), (sid, below["RF log"])          # a per-radio file names its radio
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
        single = ids[ids.index(f"logs-{sid}--single"):] if f"logs-{sid}--single" in ids else []   # c7: its group
        ids = [i for i in ids if i not in single and not i.startswith(f"logs-{sid}--")]
        single = [i for i in single if not i.startswith(f"logs-{sid}--")]
        n = len(programs) - len(single)
        assert sorted(ids[:n] + single) == sorted(f"log-{cid}" for cid in programs), (sid, ids)
        assert ids[n:] == ([f"rflog-{sid}"] if rf else []), (sid, ids)                # the RF log last (Per radio)


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
    assert [_q(a["href"]) for a in doc.within(note).find("a")] == [("/logs/loraham-kiss-tnc",       # #75: r1's own file
                                                                    {"job": "r1/rf-kiss.log", "radio": "r1"})]


# ---- the Apps page's panels (plan §2, v4 N2/N3) -----------------------------------------------------------------

def test_a_log_entry_is_a_closed_panel_with_reload_and_a_status_line(tmp_path, web):
    c, _svc = _client(web, tmp_path)
    body = _html(c, "/stacks/meshcom/body")
    doc = parse(body)
    for mid, src, kind in (("log-meshcom-bridge", "/api/logs/meshcom-bridge", "lines"),
                           ("rflog-meshcom", "/api/rflog/meshcom-bridge?job=r1/rf-meshcom.log", "records")):   # #75
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
    assert "/logs/meshcom-bridge" in page and "/logs/meshcom-bridge?job=r1/rf-meshcom.log&radio=r1" in page   # #75
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
    assert _q(tree[(name, "Logs", "Per radio", svc.stack("daemon").component("loraham-daemon").name)]) == \
        ("/logs/loraham-daemon", {"radio": "r2"})                                      # c7: under Per radio
    assert _q(tree[(name, "Logs", "Per radio", "RF log")]) == ("/logs/loraham-daemon",
                                                              {"job": "r2/rf-daemon.log", "radio": "r2"})
    page = [_q(a["href"]) for a in parse(_html(c, "/stacks/daemon/body?radio=r2")).find("a", class_="pagelink")]
    assert ("/logs/loraham-daemon", {"job": "r2/rf-daemon.log", "radio": "r2"}) in page
    assert ("/logs/loraham-daemon", {"radio": "r2"}) in page
    r = c.get("/stacks/daemon/menu/rflog-daemon?radio=r2")
    assert r.status_code == 302 and _q(r.headers["Location"].removeprefix("http://localhost")) == \
        ("/logs/loraham-daemon", {"job": "r2/rf-daemon.log", "radio": "r2"})
    # a radio the box does not have is not carried: the per-radio RF log opens on the stack's first radio
    assert _q(_tree(c, "r9")[(name, "Logs", "Per radio", "RF log")]) == ("/logs/loraham-daemon",
                                                                          {"job": "r1/rf-daemon.log", "radio": "r1"})


# ---- the log pages: breadcrumbs from the tree, one heading (tests 2, 3, 6; v4 B1, B4; G1-R2 N7) -------------------

_PAGES = [
    ("/logs/meshcom-bridge", ["meshcom", "Logs", "Per radio", "@meshcom-bridge"]),          # c7: its group
    ("/logs/meshcom-bridge?job=rf-meshcom.log", ["meshcom", "Logs", "Per radio", "RF log"]),
    ("/logs/loraham-kiss-tnc?job=rf-kiss.log", ["kiss", "Logs", "Per radio", "RF log"]),
    ("/controller/logs", [_CTL, "Logs"]),
    ("/webserver/logs?src=access", [_CTL, "Webserver and mTLS proxy", "Logs"]),
    ("/firewall/logs", [_CTL, "Firewall", "Log"]),
    ("/logs/radiolib", ["daemon", "Install", "Dependencies", "radiolib", "Log"]),        # B1: not a tree entry
    ("/logs/meshcore-node?job=install-meshcore.log", ["meshcore", "Install", "Actions", "Job log"]),  # B4: a run's job log (c3: under Actions)
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
    assert doc.find("nav", class_="crumbs")[0].text.endswith(f"{svc.stack('meshcom').name} › Logs › Per radio › Bridge log")
    assert (svc.stack("meshcom").name, "Logs", "Per radio", "Bridge log") in _tree(c)


def test_the_firewall_log_has_no_back_link(tmp_path, web):
    c, _svc = _client(web, tmp_path)
    assert "← Firewall" not in _html(c, "/firewall/logs")


# ---- the Apps page's "logs" links go; Home keeps its (plan §2b, v4 B1) ----------------------------------------------

def test_the_apps_page_has_no_logs_link_not_even_on_a_dependencies_row(tmp_path, web):
    # UI-REORG c13 (his OPEN-20 "link be removed" + OPEN-UX2-2 = A): old (c7) -> new: the Dependencies rows' "logs"
    # links whose log was under no Logs (radiolib, ...) go too; those logs are under Install › Build logs now
    c, svc = _client(web, tmp_path)
    pages = ["/stacks", "/stacks/controller/menu/controller-row", "/stacks/meshcore/menu/stack-install-meshcore"]
    pages += [f"/stacks/{s.id}/body" for s in svc.stacks()]
    for url in pages:
        doc = parse(_html(c, url))
        assert [a["href"] for a in doc.find("a", class_="logslink")] == [], url
        assert not [a for a in doc.find("a", class_="pill") if a.text.strip() == "RF log"], url
    doc = parse(_html(c, "/stacks/daemon/body"))
    assert doc.by_id("build-radiolib") is not None                     # its install log: Install › Build logs


def test_home_keeps_its_log_links(tmp_path, web):
    # green on the base too: Home is not the Apps page
    c, _svc = _client(web, tmp_path, radios=True)
    # (an instance row's config/logs links are pinned in test_home_instance_card_links)
    assert [a["href"] for a in parse(_html(c, "/")).find("a", class_="logslink")] == ["/webserver/logs"]


# ---- UI-REORG c7 (plan v7 s6): Logs grouped by radio --------------------------------------------------------------

@pytest.mark.parametrize("sid, per, single, switch", [
    ("kiss", ["log-loraham-kiss-tnc", "rflog-kiss"], ["log-loraham-kiss-serial"], True),   # mixed
    ("reticulum", [], None, False),                     # single-only (its RF log is one file, no radio): flat
    ("graywolf", ["log-graywolf", "rflog-graywolf"], None, True),                          # + the RF-log note (KISS's)
])
def test_a_stacks_logs_are_grouped_per_radio_and_single(tmp_path, web, sid, per, single, switch):
    c, svc = _client(web, tmp_path, radios=True)
    doc = parse(_html(c, f"/stacks/{sid}/body?radio=r1"))
    logs = doc.within(doc.by_id(f"logs-{sid}"))
    assert bool(logs.find("nav", class_="rswitch")) == switch, sid
    group = doc.by_id(f"logs-{sid}--per-radio")
    if not per:
        assert group is None and doc.by_id(f"logs-{sid}--single") is None, sid          # no switch, no groups
        assert [d["id"] for d in logs.find("details")][1:] == [lp["id"] for lp in submenus.stack_logs(svc.stack(sid))]
        return
    assert [d["id"] for d in doc.within(group).find("details")][1:] == per, sid
    one = doc.by_id(f"logs-{sid}--single")
    assert ([d["id"] for d in doc.within(one).find("details")][1:] if one is not None else None) == single, sid
    if sid == "graywolf":                                                                # kind=note kept
        assert "RF traffic is written by the" in doc.within(doc.by_id("rflog-graywolf")).text


def test_single_logs_link_without_the_radio_and_per_radio_logs_with_it(tmp_path, web):
    c, _svc = _client(web, tmp_path, radios=True)
    tree = _tree(c, "r2")
    single = [h for p, h in tree.items() if p[:3] == ("LoRaHAM KISS TNC", "Logs", "Single") and len(p) == 4]
    per = [h for p, h in tree.items() if p[:3] == ("LoRaHAM KISS TNC", "Logs", "Per radio") and len(p) == 4]
    assert single and all("radio" not in _q(h)[1] for h in single), single
    assert per and all(_q(h)[1].get("radio") == "r2" for h in per), per
    for gid in ("logs-kiss--per-radio", "logs-kiss--single"):                           # the new levels' pages
        assert c.get(f"/stacks/kiss/menu/{gid}").status_code == 200


# ---- UI-REORG c13 (his OPEN-UX2-2 = A): Install › Build logs ----------------------------------------------------
# an independent inventory, by hand: per stack, its components with an install log entry (a source or build steps;
# the three lhpc-built-in GPS components and meshtastic-cli have none: OPEN-UX2-3 / his flag)
_BUILD_LOGS = {
    "daemon": ["loraham-daemon", "radiolib", "libch341"], "chat": ["loraham-chat"],
    "voice": ["loraham-voice", "loraham-voice-cli"], "kiss": ["loraham-kiss-tnc", "loraham-kiss-serial"],
    "graywolf": ["graywolf"], "meshtastic": ["meshtastic", "meshtastic-web"],
    "meshcom": ["meshcom-bridge", "meshcom-gps-relay", "meshcom-qemu", "meshcom-firmware"],
    "meshcore": ["meshcore-node", "meshcore-webui", "meshcore-cli", "openhop-repeater-src"],
    "reticulum": ["rns-lora-interface", "rns", "nomadnet", "lxmd", "sideband", "meshchat"],
}


def _log(svc, name, mtime):
    import os
    p = svc._paths.under("logs", name)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(f"{name} line\n")
    os.utime(p, (mtime, mtime))


def test_every_stack_has_a_build_log_entry_per_component_with_an_install_log_in_manifest_order(tmp_path, web):
    c, svc = _client(web, tmp_path)
    # Stack › Logs unchanged (a22 N3 pin, a fresh client: no remembered rows): chat still has no program log
    assert f"No program of {svc.stack('chat').name} writes a log." in _html(c, "/stacks/chat/menu/logs-chat")
    assert {s.id: [e["id"][len("build-"):] for e in submenus.stack_build_logs(s)] for s in svc.stacks()} == _BUILD_LOGS
    for sid, cids in _BUILD_LOGS.items():
        doc = parse(_html(c, f"/stacks/{sid}/body"))
        node = doc.by_id(f"stack-install-{sid}--build-logs")
        assert [d["id"] for d in doc.within(node).find("details") if d["id"].startswith("build-")] == [f"build-{cid}" for cid in cids], sid
    # a shared component only in its owner stack: chat/graywolf list no loraham-daemon / loraham-kiss-tnc
    assert "loraham-daemon" not in _BUILD_LOGS["chat"] and "loraham-kiss-tnc" not in _BUILD_LOGS["graywolf"]


def test_an_entry_shows_the_newest_clone_build_or_test_log_and_no_log_yet_before_one(tmp_path, web):
    from lhpc.core import auto_install
    c, svc = _client(web, tmp_path)
    _log(svc, "adopt-radiolib.log", 1000)
    _log(svc, "build-radiolib.log", 2000)
    _log(svc, "start-radiolib.log", 3000)                               # a start log is no install log
    assert svc.install_log("radiolib") == "build-radiolib.log"
    run = "0123456789abcdef0123456789abcdef"
    assert auto_install.RUN_ID_RE.match(run)
    ai = auto_install.component_log_name(run, "test-radiolib")          # the real builder's name
    _log(svc, ai, 4000)
    assert svc.install_log("radiolib") == ai                            # an auto-install run's test log: newer
    step = auto_install.component_log_base(run, "build-radiolib") + "-2.log"   # a multi-step build's step name
    _log(svc, step, 4500)                                               # (service_build.py:216 base + run_job -<i>)
    assert svc.install_log("radiolib") == step
    ai = step
    _log(svc, "build-meshtastic-web.log", 5000)
    assert svc.install_log("meshtastic") == ""                          # another component's log, never this one's
    _log(svc, "build-radiolib-configure.log", 6000)
    assert svc.install_log("radiolib") == ai                            # B3: an alphabetic tail is no install log
    assert svc.install_log("libch341") == ""
    doc = parse(_html(c, "/stacks/daemon/body"))
    assert doc.by_id("build-radiolib")["data-src"] == f"/api/logs/radiolib?job={ai}"
    lib = doc.by_id("build-libch341")
    assert "no log yet" in lib.text and lib["data-src"] is None
    pages = [a["href"] for a in doc.find("a", class_="pagelink")]
    assert f"/logs/radiolib?job={ai}" in pages                          # a logged entry: "page ›" to its log
    assert "/stacks/daemon/menu/build-libch341" in pages                # no log yet: its menu page (plan v4: one
    #                                                                     "page ›" per level, test_u4_menu_pages)
    r = c.get("/stacks/daemon/menu/build-radiolib")                     # its own page: the job log
    assert r.status_code == 302 and r.headers["Location"].endswith(f"/logs/radiolib?job={ai}")
    assert "no log yet" in _html(c, "/stacks/daemon/menu/build-libch341")   # none: its menu page says so
    got = c.get(f"/api/logs/radiolib?job={ai}").get_json()
    assert got["lines"] == [f"{ai} line"]
    crumbs = " ".join(parse(_html(c, f"/logs/radiolib?job={ai}")).find("nav", class_="crumbs")[0].text.split())
    assert crumbs.endswith("Install › Actions › Job log"), crumbs     # N5: today's job-log crumbs


def test_a_components_newest_job_log_is_its_own_and_keeps_its_numbered_and_radio_siblings(tmp_path, web):
    # found while measuring c13 (pre-existing): `<kind>-<cid>-*` by prefix took another component's log
    _c, svc = _client(web, tmp_path)
    life = svc._lifecycle()
    _log(svc, "build-loraham-voice-cli.log", 3000)
    assert life._newest_job_log("loraham-voice") is None                # red at the base: the -cli log
    _log(svc, "start-loraham-voice-r1.log", 1000)
    assert life._newest_job_log("loraham-voice").name == "start-loraham-voice-r1.log"   # a rid-only start log
    _log(svc, "start-loraham-voice-433-r2.log", 2000)
    assert life._newest_job_log("loraham-voice").name == "start-loraham-voice-433-r2.log"
    _log(svc, "build-loraham-voice-2.log", 2500)
    assert life._newest_job_log("loraham-voice").name == "build-loraham-voice-2.log"   # a numbered build step
    _log(svc, "post-loraham-voice-4242-17000000.log", 2700)
    assert life._newest_job_log("loraham-voice").name == "post-loraham-voice-4242-17000000.log"   # a post runner
    # the start-log scan (no band: the GUI's logs link): two ids sharing a prefix, no exact log of the shorter
    comp = svc.stack("meshtastic").component("meshtastic")
    _log(svc, "start-meshtastic-web-r1.log", 4000)
    assert life.start_log(comp) is None                                 # red at the base: meshtastic-web's
    _log(svc, "start-meshtastic-433-r1.log", 3500)
    assert life.start_log(comp).name == "start-meshtastic-433-r1.log"


def test_a_component_with_only_a_host_test_gets_a_build_log_entry():
    # G2-UXJ N1 (plan v4): a host test writes an install log too (test-<cid>.log); no shipped component has one
    # without a source or build steps, so a stand-in stack shows the rule
    from types import SimpleNamespace as NS
    comps = [NS(id="t-only", name="T", source=None, build_steps=(), test_argv=("pytest",)),
             NS(id="nothing", name="N", source=None, build_steps=(), test_argv=())]
    assert [e["id"] for e in submenus.stack_build_logs(NS(id="x", components=comps))] == ["build-t-only"]


# ---- F9 (Q4 = A): the radio switch stands in "Per radio", on every logs page --------------------------------------

_PER_RADIO_STACKS = ("daemon", "kiss", "graywolf", "meshtastic", "meshcom", "meshcore")


@pytest.mark.parametrize("sid", _PER_RADIO_STACKS)
def test_the_radio_switch_is_inside_per_radio_inline_and_on_the_groups_page(tmp_path, web, sid):
    c, _svc = _client(web, tmp_path, radios=True)
    for url in (f"/stacks/{sid}/body?radio=r2",                                   # inline: the Stacks body
                f"/stacks/{sid}/menu/logs-{sid}?radio=r2",                        # inline: the Logs level's page
                f"/stacks/{sid}/menu/logs-{sid}--per-radio?radio=r2"):            # standalone: the group's page
        doc = parse(_html(c, url))
        per = doc.by_id(f"logs-{sid}--per-radio")
        assert per is not None, url
        inside = doc.within(per).find("nav", class_="rswitch")
        logs = doc.by_id(f"logs-{sid}")                                           # absent on the group's page
        assert len(inside) == 1, url
        assert logs is None or len(doc.within(logs).find("nav", class_="rswitch")) == 1, url    # none above the groups
        one = doc.by_id(f"logs-{sid}--single")
        assert one is None or not doc.within(one).find("nav", class_="rswitch"), url
        hrefs = [a["href"] for a in doc.within(inside[0]).find("a")]
        assert hrefs, url
        for h in hrefs:
            path, query = _q(h)
            assert h.endswith(f"#logs-{sid}--per-radio") and query.get("radio") in ("r1", "r2"), (url, h)
            # B1: from a menu page the link opens the group's own page (Per radio opened there, the switch shown)
            assert path == ("/stacks" if "/body" in url else f"/stacks/{sid}/menu/logs-{sid}--per-radio"), (url, h)
        assert {_q(h)[1]["radio"] for h in hrefs} | {"r2"} == {"r1", "r2"}, (url, hrefs)


def test_a_single_only_stack_has_no_logs_switch(tmp_path, web):
    c, _svc = _client(web, tmp_path, radios=True)
    doc = parse(_html(c, "/stacks/reticulum/body?radio=r1"))
    assert doc.by_id("logs-reticulum--per-radio") is None
    assert not doc.within(doc.by_id("logs-reticulum")).find("nav", class_="rswitch")
