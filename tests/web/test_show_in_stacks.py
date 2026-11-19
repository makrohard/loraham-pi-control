"""F12: "‹ show in Stacks" (one shared p.pagebar line, app._show_url) right under the breadcrumb on the own pages a
job link cannot reach: /hardware, the controller / webserver / firewall logs, a program's log and an RF log opened
as their tree level. None on a build-log page (current or older install log), a job log, a program the tree does
not list, or a page outside the tree."""
import re
from urllib.parse import parse_qs, urlsplit

import pytest
from htmlq import parse

from lhpc.core import config as cfgmod
from lhpc.core import radio_start
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService


def _client(web, tmp_path, radios=False):
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    p = Paths(runtime_root=tmp_path)
    if radios:
        assert radio_start.setup_radios(p, "uputronics")[0]
        cfgmod.save_hardware_setup(p, "uputronics")
    svc = ControllerService(system=FakeSystem().system, paths=p)
    return web(service_factory=lambda: svc), svc


def _log(svc, name):
    p = svc._paths.under("logs", name)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(f"{name} line\n")


def _bar(c, url):
    """The page's "‹ show in Stacks" links, and the element right after the crumbline."""
    r = c.get(url)
    assert r.status_code == 200, (url, r.status_code)
    body = r.get_data(as_text=True)
    links = [a["href"] for a in parse(body).find("a") if a.text.strip() == "‹ show in Stacks"]
    rest = body[body.index('<div class="crumbline">'):]
    rest = rest[rest.index("</nav></div>") + len("</nav></div>"):].lstrip()
    first = re.match(r"<p\b[^>]*>.*?</p>", rest)
    return links, first.group(0) if first else ""


_OWN = [("/hardware", "daemon", "stack-hardware-daemon"),
        ("/controller/logs", "", "controller-logs"),
        ("/webserver/logs?src=access", "", "webserver-row--logs"),
        ("/firewall/logs", "", "firewall-row--log"),
        ("/logs/meshcom-bridge", "meshcom", "log-meshcom-bridge"),                       # a program's log
        ("/logs/meshcom-bridge?job=rf-meshcom.log", "meshcom", "rflog-meshcom")]          # an RF log


@pytest.mark.parametrize("url, owner, mid", _OWN, ids=[u for u, _o, _m in _OWN])
def test_each_own_tree_page_has_the_show_in_stacks_line_under_its_breadcrumb(tmp_path, web, url, owner, mid):
    c, _svc = _client(web, tmp_path)
    links, first = _bar(c, url)
    assert len(links) == 1, links
    assert first == f'<p class="pagebar"><a href="{links[0]}">‹ show in Stacks</a></p>', first
    href = urlsplit(links[0].replace("&amp;", "&"))
    assert href.path == "/stacks" and href.fragment == mid, links
    assert parse_qs(href.query) == ({"open": [owner]} if owner else {}), links


def test_the_line_keeps_the_pages_radio(tmp_path, web):
    c, _svc = _client(web, tmp_path, radios=True)
    links, _first = _bar(c, "/logs/loraham-daemon?radio=r2")
    href = urlsplit(links[0].replace("&amp;", "&"))
    assert parse_qs(href.query) == {"open": ["daemon"], "radio": ["r2"]} and href.fragment == "log-loraham-daemon"


def test_no_line_on_build_logs_job_logs_unlisted_programs_and_pages_outside_the_tree(tmp_path, web):
    c, svc = _client(web, tmp_path)
    _log(svc, "adopt-radiolib.log")
    _log(svc, "build-radiolib.log")
    assert svc.install_log("radiolib") == "build-radiolib.log"
    for url in ("/logs/radiolib?job=build-radiolib.log",                  # the Build logs level's page (current)
                "/logs/radiolib?job=adopt-radiolib.log",                  # an older install log
                "/stacks/daemon/menu/build-radiolib",                     # the level's link: the same page
                "/logs/meshcore-node?job=install-meshcore.log",           # a run's job log
                "/logs/radiolib",                                         # a program the tree does not list
                "/hardware/add"):                                         # not a tree level
        r = c.get(url, follow_redirects=True)
        assert r.status_code == 200, (url, r.status_code)
        assert "‹ show in Stacks" not in r.get_data(as_text=True), url


def test_no_line_on_a_radios_page_and_add_hardware(tmp_path, web):
    # their line under the breadcrumb is "‹ The radios" (F5), never "‹ show in Stacks" (his F12 comment)
    c, _svc = _client(web, tmp_path, radios=True)
    for url in ("/hardware/radio/r1", "/hardware/add"):
        links, first = _bar(c, url)
        assert links == [] and first == '<p class="pagebar"><a href="/hardware">‹ The radios</a></p>', (url, first)
