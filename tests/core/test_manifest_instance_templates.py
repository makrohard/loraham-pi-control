"""L4: a manifest value with instance tokens loads as its slot-0 value, and keeps its template for the other
slots (A §2.3, §2.4). Every consumer (firewall, nginx, status, admission) reads the slot-0 value, which is
today's; the template renders any slot through `commands.Instance`."""

import repo_paths

from lhpc.core import commands
from lhpc.core.manifest import load_manifest

MANIFEST = repo_paths.REPO / "lhpc" / "data" / "manifest.example.toml"


def _kiss(tmp_path, templated: bool):
    text = MANIFEST.read_text()
    if templated:
        for a, b in (('default = "8001"', 'default = "{port:8001}"'),
                     ('address = "127.0.0.1:8001"', 'address = "127.0.0.1:{port:8001}"'),
                     ('key = "tcp.port.8001"', 'key = "tcp.port.{port:8001}"')):
            assert text.count(a) >= 1, a
            text = text.replace(a, b, 1)        # the first of each: the kiss component's own
    path = tmp_path / "manifest.toml"
    path.write_text(text)
    return next(c for st in load_manifest(path) for c in st.components if c.id == "loraham-kiss-tnc")


def test_a_templated_manifest_loads_as_todays_values(tmp_path):
    today, templated = _kiss(tmp_path, False), _kiss(tmp_path, True)
    assert templated.run_params == tuple(p if p.name != "kiss_port" else
                                         type(p)(**{**p.__dict__, "default_template": "{port:8001}"})
                                         for p in today.run_params)
    ep = next(e for e in templated.endpoints if e.address_template)
    claim = next(r for r in templated.resources if r.key_template)
    assert (ep.address, claim.key) == ("127.0.0.1:8001", "tcp.port.8001")


def test_the_template_renders_every_slot(tmp_path):
    kiss = _kiss(tmp_path, True)
    port = next(p for p in kiss.run_params if p.name == "kiss_port")
    ep = next(e for e in kiss.endpoints if e.address_template)
    claim = next(r for r in kiss.resources if r.key_template)
    inst = commands.Instance(3, "r2")
    assert [inst.subst(t) for t in (port.default_template, ep.address_template, claim.key_template)] == [
        "8031", "127.0.0.1:8031", "tcp.port.8031"]


def test_a_manifest_without_tokens_keeps_no_template():
    for c in (c for st in load_manifest() for c in st.components):
        assert not any(p.default_template for p in c.run_params), c.id
        assert not any(e.address_template for e in c.endpoints), c.id
        assert not any(r.key_template for r in c.resources), c.id
