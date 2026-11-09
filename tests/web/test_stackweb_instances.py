"""nginx pages per instance (A §2.6; PROOF T-N1, T-N5). K4b: every page is an instance's, `<page>@<rid>`, proxied to
its radio's port, with the component's deny locations and deny answer; the template's values (no radio) keep `<page>`."""

from lhpc.core import commands
from lhpc.core import webserver as ws
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService
from lhpc.core.webserver import StackWebProxy


def _svc(tmp_path, monkeypatch, slot_k=True):
    (tmp_path / "config" / "stacks").mkdir(parents=True, exist_ok=True)
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    if slot_k:     # raising=False: on a code without instances the pages are the bare stack's only (the red-before)
        monkeypatch.setattr(commands, "stack_instances",
                            lambda stk, paths=None: (commands.TEMPLATE, commands.Instance("r2"))
                            if stk.id == "meshcore" else (commands.TEMPLATE,), raising=False)
    return svc


def test_a_slot_k_instance_has_its_own_pages_proxied_to_its_own_ports(tmp_path, monkeypatch):
    pages = {p.page_id: p for p in _svc(tmp_path, monkeypatch).stack_web_pages("meshcore")}
    assert {pid: p.address for pid, p in pages.items()} == {
        "meshcore": "127.0.0.1:8788", "meshcore-meshcore-node": "127.0.0.1:8000",
        "meshcore@r2": "127.0.0.1:8921", "meshcore-meshcore-node@r2": "127.0.0.1:8133"}     # + 133 on r2 (DEC-W348)
    assert pages["meshcore@r2"].label == pages["meshcore"].label + " @r2"


def test_t_n5_every_instance_page_carries_the_bare_pages_deny_locations_and_answer(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch)
    pages = {p.page_id: p for p in svc.stack_web_pages("meshcore")}
    for base in ("meshcore", "meshcore-meshcore-node"):
        bare, mine = pages[base], pages[f"{base}@r2"]
        assert (mine.deny_paths, mine.deny_notice) == (bare.deny_paths, bare.deny_notice) and bare.deny_paths

    def proxy(page, port):
        class _SWC:
            stack_id = page.page_id; enabled = True; remote = False; allowed_cidrs = []
            scheme = "https"; access_mode = "auth-everywhere"
        _SWC.port = port
        return StackWebProxy(_SWC(), page.address, page.scheme, page.deny_paths, page.deny_notice)
    node0, nodek = pages["meshcore-meshcore-node"], pages["meshcore-meshcore-node@r2"]
    out = ws.render_nginx_config(svc._paths, svc.config().webserver, [proxy(node0, 8448), proxy(nodek, 8458)])
    block0 = out.split("# meshcore-meshcore-node web UI")[1].split("# meshcore-meshcore-node@r2 web UI")[0]
    blockk = out.split("# meshcore-meshcore-node@r2 web UI")[1]

    def deny_lines(block):
        return [ln.strip() for ln in block.splitlines() if ln.strip().startswith("location ~ ")]
    assert deny_lines(block0) == deny_lines(blockk) and deny_lines(block0)
    assert f"return 200 '{ws.DENY_NOTICE_BODY}'" in deny_lines(blockk)[0]
    assert "server 127.0.0.1:8133" in out and "server 127.0.0.1:8000" in out


def test_t_n1_every_page_is_an_instances_page(tmp_path, monkeypatch):
    # K4b (no slots): the default hardware list (r1 433, r2 868) gives meshcore its 868 radio, r2; no bare page id.
    pages = _svc(tmp_path, monkeypatch, slot_k=False).stack_web_pages("meshcore")
    assert [p.page_id for p in pages] == ["meshcore@r2", "meshcore-meshcore-node@r2"]
    assert [p.address for p in pages] == ["127.0.0.1:8921", "127.0.0.1:8133"]          # r2's ports: + 133
