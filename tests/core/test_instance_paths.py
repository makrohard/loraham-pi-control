"""B3-D17 (A §2.3 :251): every path an instance owns follows its instance. For every component of a stack that runs per
radio, every string of the shipped manifest that names a path under the runtime's state/, logs/ or config/files/ —
run lines, argv, pre-steps, environment, config files and their params, param defaults, endpoint addresses, the UI
password file — carries {instance_dir} right after that directory: slot 0 renders today's path, every slot k a path of
its own. And no value the loader hands to a consumer keeps the token unrendered.

Out of scope, named: the daemon (one per radio from L5, named by the radio id), the Reticulum stack (one node, A §2.4),
a stack's `state_root` (the stack's purge root), and meshcom-gps-relay's relative socket (L8-MC)."""

import re

import pytest

import repo_paths
from lhpc.core import commands
from lhpc.core.manifest import load_manifest

try:
    import tomllib
except ModuleNotFoundError:          # Python 3.10
    import tomli as tomllib

MANIFEST = repo_paths.REPO / "lhpc" / "data" / "manifest.example.toml"
PER_RADIO = {"chat", "voice", "kiss", "graywolf", "meshtastic", "meshcom", "meshcore"}
SKIP_KEYS = {"state_root", "note", "purpose", "description", "label", "ui_password_note"}
SKIP_COMPONENTS = {"meshcom-gps-relay"}
# An instance-owned location: the runtime's state/, logs/ or config/files/ (absolute through {runtime}, or relative to
# the runtime root as endpoint addresses and the UI password file are), followed by a name — or config/files itself as a
# directory (a working directory, a mkdir).
WHERE = r"(?:\{runtime\}/|(?<![\w/{]))(?:(?:state|logs|config/files)/(?=[\w.{-])|config/files(?=[\"' ]|$))"
OWNED = re.compile(WHERE + r"(?!\{instance_dir\})")
INSTANCES = [commands.SLOT0] + [commands.Instance(k, rid) for k, rid in zip(range(1, 8),
                                                                           ("r1", "r12", "r2", "r21", "r9", "r99", "r3"))]


def _strings(node, key=""):
    if isinstance(node, str):
        yield key, node
    elif isinstance(node, dict):
        for k, v in node.items():
            if k not in SKIP_KEYS:
                yield from _strings(v, k)
    elif isinstance(node, list):
        for v in node:
            yield from _strings(v, key)


def _components():
    data = tomllib.loads(MANIFEST.read_text())
    for st in data["stack"]:
        if st["id"] in PER_RADIO:
            for comp in st.get("component", []):
                if comp["id"] not in SKIP_COMPONENTS:
                    yield comp


def _owned_paths():
    for comp in _components():
        for key, text in _strings(comp):
            if re.search(WHERE, text):
                yield comp["id"], key, text


def test_every_instance_owned_path_follows_its_instance():
    bare = [(cid, key, text) for cid, key, text in _owned_paths() if OWNED.search(text)]
    assert not bare, "paths without {instance_dir}:\n" + "\n".join(f"  {c} {k}: {t}" for c, k, t in bare)


@pytest.mark.parametrize("cid, key, text", list(_owned_paths()), ids=lambda v: str(v)[:40])
def test_slot_0_is_todays_path_and_every_instance_has_its_own(cid, key, text):
    rendered = [inst.subst(text) for inst in INSTANCES]
    assert rendered[0] == text.replace("{instance_dir}", "")
    assert len(set(rendered)) == len(INSTANCES), (cid, key, text)


def test_no_loaded_value_keeps_the_token():
    # The loader hands consumers slot 0's values (the templates are kept beside them); what runs per instance (run,
    # argv, pre-steps, environment) is rendered by commands at its start.
    for st in load_manifest():
        if st.id not in PER_RADIO:
            continue
        for c in st.components:
            loaded = [c.ui_password_file] + [p.default for p in c.run_params] + [e.address for e in c.endpoints]
            if c.config_file is not None:
                loaded += [c.config_file.path] + [p.default for p in c.config_file.params]
            assert not [v for v in loaded if "{instance_dir}" in str(v)], c.id
