"""B3-D17, B3-D17a, B3-D21a (A-r65 §2.3, §2.4): every path an instance owns follows its instance. For every component
of a stack that runs per radio, every string of the shipped manifest that names a path under the runtime's state/,
logs/, config/files/ or config/secrets/ — run lines, argv, pre-steps, environment, config files and their params, param
defaults, endpoint addresses, the UI password file — carries {instance_dir} right after that directory; a file under
/dev/shm (MeshCore's gps_file) carries {instance_suffix} in its name and no per-radio directory (another user could
pre-create one there). The template (no radio) renders the base path, every radio's instance a path of its own (r1's
too, K4b). And no value the loader hands to a consumer keeps a token unrendered.

Out of scope, named: the daemon (one per radio from L5, named by the radio id; its /tmp sockets with it), the Reticulum
stack (one node, A-r65 §2.4), a stack's `state_root` (the stack's purge root), build-time fields (`build`,
`build_steps`: one build per stack, e.g. meshcom-qemu's xr_pw), and meshcom-gps-relay's relative socket (L8-MC)."""

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
SKIP_KEYS = {"state_root", "note", "purpose", "description", "label", "ui_password_note", "build", "build_steps"}
SKIP_COMPONENTS = {"meshcom-gps-relay"}
# An instance-owned location: the runtime's state/, logs/, config/files/ or config/secrets/ (absolute through
# {runtime}, or relative to the runtime root as endpoint addresses and the UI password file are), followed by a name —
# or config/files itself as a directory (a working directory, a mkdir) — or a file under /dev/shm (a whole value:
# MeshCore's gps_file).
RUNTIME = r"(?:\{runtime\}/|(?<![\w/{]))(?:(?:state|logs|config/files|config/secrets)/(?=[\w.{-])|config/files(?=[\"' ]|$))"
WHERE = RUNTIME + r"|^/dev/shm/"
RUNTIME_BARE = re.compile(RUNTIME + r"(?!\{instance_dir\})")
TOKENS = ("{instance_dir}", "{instance_suffix}")


def _bare(text: str) -> bool:
    if text.startswith("/dev/shm/"):
        return "{instance_suffix}" not in text or "{instance_dir}" in text
    return bool(RUNTIME_BARE.search(text))


INSTANCES = [commands.TEMPLATE] + [commands.Instance(rid) for rid in ("r1", "r12", "r2", "r21", "r9", "r99", "r3")]


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


def test_the_walk_finds_every_stack_and_component_it_names():
    # A renamed stack or component would leave the walk silently: each name above must exist.
    data = tomllib.loads(MANIFEST.read_text())
    assert PER_RADIO <= {st["id"] for st in data["stack"]}
    assert SKIP_COMPONENTS <= {c["id"] for st in data["stack"] for c in st.get("component", [])}


def test_every_instance_owned_path_follows_its_instance():
    bare = [(cid, key, text) for cid, key, text in _owned_paths() if _bare(text)]
    assert not bare, "paths without their instance token:\n" + "\n".join(f"  {c} {k}: {t}" for c, k, t in bare)


@pytest.mark.parametrize("cid, key, text", list(_owned_paths()), ids=lambda v: str(v)[:40])
def test_slot_0_is_todays_path_and_every_instance_has_its_own(cid, key, text):
    # K7: a string with port tokens (meshcom-qemu's run line: its guest's --ports and --xr-redirect) renders only on
    # r1..r64, the radios with ports; the template's ports are the bases and its helper instance 0
    ports = re.search(r"\{port:\d+\}", text) is not None
    insts = [i for i in INSTANCES if not (ports and i.rid == "r99")]
    rendered = [inst.subst(text) for inst in insts]
    template = re.sub(r"\{port:(\d+)\}", r"\1", text.replace("{instance_index_dir}", "").replace("{instance_index}", "0"))
    assert rendered[0] == template.replace("{instance_dir}", "").replace("{instance_suffix}", "")
    assert len(set(rendered)) == len(insts), (cid, key, text)


def test_no_loaded_value_keeps_the_token():
    # The loader hands consumers the template values (the templates are kept beside them); what runs per instance (run,
    # argv, pre-steps, environment) is rendered by commands at its start.
    for st in load_manifest():
        if st.id not in PER_RADIO:
            continue
        for c in st.components:
            loaded = [c.ui_password_file] + [p.default for p in c.run_params] + [e.address for e in c.endpoints]
            if c.config_file is not None:
                loaded += [c.config_file.path] + [p.default for p in c.config_file.params]
            assert not [v for v in loaded if any(t in str(v) for t in TOKENS)], c.id
