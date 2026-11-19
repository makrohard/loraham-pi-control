"""UI-REORG c8 (plan v7 s7, L78 = PLAN-CERTS-TREE-v1, 11-19): Webserver › Certificates holds three nodes, PKI,
Server certificate and Client certificate, each a closed panel with its own page; the contents are split, nothing is
dropped: every form and field id of today sits on exactly one child. Also s7's controller order."""
from __future__ import annotations

import re

import pytest
from htmlq import parse

_KIDS = [("pki", "PKI", {"/webserver/init"}, {"ws-init-confirm"}),
         ("server", "Server certificate", {"/webserver/tls-renew"}, {"ws-tls-clock"}),
         ("client", "Client certificate", {"/webserver/cert"}, {"ws-issue-label", "ws-issue-clock"})]
_CERTS = "/stacks/controller/menu/webserver-row--certificates"


def _doc(c, path):
    r = c.get(path)
    assert r.status_code == 200, (path, r.status_code)
    return parse(r.get_data(as_text=True))


def test_the_tree_lists_the_three_nodes_under_certificates_in_order(web):
    tree = web().get("/stacks/menus").get_data(as_text=True)
    ids = re.findall(r'/stacks/controller/menu/(webserver-row--certificates--[a-z]+)"', tree)
    assert ids == [f"webserver-row--certificates--{k}" for k, *_ in _KIDS]


@pytest.mark.parametrize("kid, label, actions, fields", _KIDS)
def test_each_childs_page_holds_exactly_its_forms(web, kid, label, actions, fields):
    c = web()
    mid = f"webserver-row--certificates--{kid}"
    doc = _doc(c, f"/stacks/controller/menu/{mid}")
    crumbs = " ".join(doc.find("nav", class_="crumbs")[0].text.split())
    assert crumbs.endswith(f"› Certificates › {label}"), crumbs
    panel = doc.by_id(mid)
    assert panel.has_attr("open")
    assert {f["action"] for f in doc.within(panel).find("form")} == actions
    assert {i["id"] for i in doc.within(panel).find("input") if i["id"]} == fields
    others = set().union(*(f for k, _l, _a, f in _KIDS if k != kid))
    assert not [i for i in doc.find("input") if i["id"] in others]               # nothing of a sibling


def test_the_certificates_page_shows_all_three_and_every_id_once(web):
    doc = _doc(web(), _CERTS)
    for kid, *_ in _KIDS:
        assert doc.by_id(f"webserver-row--certificates--{kid}") is not None, kid
    ids = [i["id"] for i in doc.find("input") if i["id"]]
    assert sorted(ids) == sorted(set().union(*(f for *_x, f in _KIDS))) and len(ids) == len(set(ids))


def test_a_form_posted_from_a_childs_page_returns_to_it(web):
    c = web()
    page = "/stacks/controller/menu/webserver-row--certificates--server"
    doc = _doc(c, page)
    [form] = [f for f in doc.find("form") if f["action"] == "/webserver/tls-renew"]
    data = {i["name"]: i["value"] for i in doc.within(form).find("input") if i["type"] == "hidden"}
    r = c.post("/webserver/tls-renew", data=data)
    assert r.status_code == 302 and r.headers["Location"].split("#")[0].endswith(page), r.headers["Location"]


def test_the_controller_order(web):
    # UI-REORG s7: Update · System dependencies · Global operator callsign · Network · Webserver · Firewall ·
    # Position (GPS) · Backup secrets · Logs (Backup secrets just before Logs; was third)
    doc = _doc(web(), "/stacks/controller/menu/controller-row")
    row = doc.within(doc.by_id("controller-row"))
    top = [d["id"] for d in row.find("details") if d["id"] in (
        "controller-update", "controller-system-deps", "operator-callsign", "controller-network", "webserver-row",
        "firewall-row", "gps-row", "controller-backup-secrets", "controller-logs")]
    assert top.index("operator-callsign") < top.index("controller-backup-secrets") == len(top) - 2, top
    assert top[-1] == "controller-logs" and top[:2] == ["controller-update", "controller-system-deps"], top
    tree = web().get("/stacks/menus").get_data(as_text=True)
    order = [m for m in re.findall(r'/stacks/controller/menu/([a-z-]+)"', tree) if m in top]
    assert order[-1] == "controller-backup-secrets", order          # (Logs links its own page, /controller/logs)
