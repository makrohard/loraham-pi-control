"""UI-REORG c3 (plan v7 s2): every stack's Install holds Actions (today's body without its two dependency includes),
Repositories (the source remotes, their own form: remotes only), Dependencies and System dependencies, each ONCE; the
old daemon Repositories URL forwards to the new level."""
from __future__ import annotations

import pytest
import radios_fixture
from htmlq import parse

from lhpc.core import config as cfgmod
from lhpc.core import radios
from lhpc.core.paths import Paths

ROWS = (radios.Radio("r1", "a", "uputronics", "433", ce="ce0"),)


@pytest.fixture
def c(web, tmp_path):
    radios_fixture.write(tmp_path, ROWS)
    return web()


def test_install_holds_actions_repositories_and_both_dependency_levels_once(c):
    for url in ("/stacks?open=daemon&inst=daemon", "/stacks/daemon/menu/stack-install-daemon"):
        html = c.get(url).get_data(as_text=True)
        doc = parse(html)
        inst = doc.within(doc.by_id("stack-install-daemon"))
        kids = [x.text for x in inst.find("summary")]
        assert kids[0] == "Install" and kids.index("Actions") < kids.index("Repositories") \
            < kids.index("Dependencies") < kids.index("System dependencies"), (url, kids)
        for lvl in ("dependencies", "system-dependencies", "actions", "repositories"):
            assert html.count(f'id="stack-install-daemon--{lvl}"') == 1, (url, lvl)
        acts = doc.within(doc.by_id("stack-install-daemon--actions"))
        assert acts.find("form") and not acts.find("details", id="stack-install-daemon--dependencies")
    for lvl in ("actions", "repositories"):                                  # each one its own level page
        assert c.get(f"/stacks/daemon/menu/stack-install-daemon--{lvl}").status_code == 200


def test_a_remotes_save_writes_the_remotes_only(c, tmp_path, csrf):
    # red before c3: no /stacks/<sid>/remotes (404); the config save would clear absent flags and auto-start ticks
    p = Paths(runtime_root=tmp_path)
    cfgmod.update_stack_config(p, "kiss", {"autostart_loraham-kiss-serial": "on"})
    own = tmp_path / "config" / "stacks" / "kiss.toml"
    before = own.read_bytes()
    doc = parse(c.get("/stacks/kiss/menu/stack-install-kiss--repositories").get_data(as_text=True))
    form = next(f for f in doc.find("form") if f["action"] == "/stacks/kiss/remotes")
    names = [i["name"] for i in doc.within(form).find("input") if i["name"].startswith("remote_")]
    assert names, "no remote field"
    url = "https://github.com/example/fork.git"
    r = c.post("/stacks/kiss/remotes", data={"_csrf": csrf(c), **dict.fromkeys(names, url)})   # one shared checkout
    assert r.status_code == 302 and r.headers["Location"].endswith("#stack-install-kiss--repositories")
    assert own.read_bytes() == before                                          # values + auto-start byte-equal
    local = (tmp_path / "config" / "local.toml").read_text()
    assert url in local
    c.post("/stacks/kiss/remotes", data={"_csrf": csrf(c), **dict.fromkeys(names, "")})       # blank reverts to the default
    assert url not in (tmp_path / "config" / "local.toml").read_text() and own.read_bytes() == before


@pytest.mark.parametrize("query, radio", [("?radio=r1", "?radio=r1"), ("?radio=r9", ""), ("", "")])
def test_the_old_repositories_url_forwards_to_install_repositories(c, query, radio):
    r = c.get(f"/stacks/daemon/menu/stack-settings-daemon--repository{query}")
    assert r.status_code == 302
    assert r.headers["Location"] == (f"/stacks/daemon/menu/stack-install-daemon--repositories{radio}"
                                     "#stack-install-daemon--repositories")
    assert c.get(r.headers["Location"].split("#")[0]).status_code == 200
