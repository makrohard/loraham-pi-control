"""L4: a manifest value with instance tokens loads as its slot-0 value, and keeps its template for the other
slots (A §2.3, §2.4). Every consumer (firewall, nginx, status, admission) reads the slot-0 value, which is
today's (the slot-0 goldens hold it); the template renders any slot through `commands.Instance`."""

import re

from lhpc.core import commands
from lhpc.core.manifest import load_manifest

# Every listener of the shipped manifest that follows its instance's slot: (component, field, slot-0 value).
# Reticulum's 37428/37429/4242 and meshchat's 8790 stay fixed (one node, A §2.4).
TEMPLATED = {
    ("loraham-kiss-tnc", "param", "8001"), ("loraham-kiss-tnc", "address", "127.0.0.1:8001"),
    ("loraham-kiss-tnc", "key", "tcp.port.8001"),
    ("graywolf", "param", "8001"), ("graywolf", "address", "127.0.0.1:8080"), ("graywolf", "key", "tcp.port.8080"),
    ("graywolf", "key", "tcp.port.8001"),
    ("meshtastic", "address", "127.0.0.1:4403"), ("meshtastic", "address", "127.0.0.1:9443"),
    ("meshtastic", "key", "tcp.port.4403"), ("meshtastic", "key", "tcp.port.9443"),
    ("meshcom-bridge", "param", "7000"), ("meshcom-bridge", "address", "127.0.0.1:7000"),
    ("meshcom-bridge", "key", "tcp.port.7000"),
    ("meshcom-qemu", "address", "127.0.0.1:18083"), ("meshcom-qemu", "address", "127.0.0.1:12323"),
    ("meshcom-qemu", "key", "tcp.port.18083"), ("meshcom-qemu", "key", "tcp.port.12323"),
    ("meshcore-node", "address", "127.0.0.1:5000"), ("meshcore-node", "address", "127.0.0.1:8000"),
    ("meshcore-node", "key", "tcp.port.5000"), ("meshcore-node", "key", "tcp.port.8000"),
    ("meshcore-webui", "address", "127.0.0.1:8788"), ("meshcore-webui", "key", "tcp.port.8788"),
    ("meshcore-cli", "param", "5000"),
}


def _templated():
    # The port templates ({port:}/{port_of:}); per-instance paths ({instance_dir}) are tests/core/test_instance_paths.py's.
    out = {}
    for c in (c for st in load_manifest() for c in st.components):
        for p in c.run_params:
            if "{port" in p.default_template:
                out[(c.id, "param", p.default)] = p.default_template
        for e in c.endpoints:
            if "{port" in e.address_template:
                out[(c.id, "address", e.address)] = e.address_template
        for r in c.resources:
            if "{port" in r.key_template:
                out[(c.id, "key", r.key)] = r.key_template
    return out


def test_every_listener_that_follows_its_slot_loads_as_todays_value():
    found = _templated()
    assert set(found) == TEMPLATED
    for (_cid, _field, slot0), template in found.items():
        assert commands.TEMPLATE.subst(template) == slot0 and "{" not in slot0


def test_the_templates_render_every_radio():
    # K4ac (DEC-W348): a copy on r3 serves every templated port at base + 133 x 2, graywolf's kiss port the same
    inst = commands.Instance("r3")
    rendered = {k: inst.subst(t) for k, t in _templated().items()}
    assert rendered[("loraham-kiss-tnc", "address", "127.0.0.1:8001")] == "127.0.0.1:8267"
    assert rendered[("graywolf", "param", "8001")] == "8267"
    assert rendered[("graywolf", "key", "tcp.port.8001")] == "tcp.port.8267"
    for (cid, _field, slot0), value in rendered.items():
        base = int(re.search(r"(\d+)$", slot0).group(1))
        assert value.endswith(str(base + 266)), (cid, slot0, value)


def test_the_run_argv_carries_the_listener_tokens_whole():
    webui = next(c for st in load_manifest() for c in st.components if c.id == "meshcore-webui")
    assert webui.process.instance_arg == ("--port", "{port:8788}")
    assert commands.instance_value("{port:8788}", webui, {}, "/rt", "/src", inst=commands.Instance("r2")) == "8921"


def test_a_manifest_without_tokens_keeps_no_template(tmp_path):
    # C2a's claim, kept at head: the shipped manifest with every token written as its slot-0 value
    # (today's manifest) loads with no template on any param, endpoint or claim.
    import repo_paths
    text = (repo_paths.REPO / "lhpc" / "data" / "manifest.example.toml").read_text()
    text = re.sub(r"\{port(?:_of:[a-z][a-z0-9_-]*)?:(\d+)\}", r"\1", text).replace("{instance_dir}", "").replace(
        "{instance_suffix}", "")
    path = tmp_path / "manifest.toml"
    path.write_text(text)
    for c in (c for st in load_manifest(path) for c in st.components):
        assert not any(p.default_template for p in c.run_params), c.id
        assert not any(e.address_template for e in c.endpoints), c.id
        assert not any(r.key_template for r in c.resources), c.id
