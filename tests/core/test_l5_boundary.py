"""T-L5 / T-V2 at the L5 boundary (PROOF T-L5; plan A §2.3 process match rule and the L5 stage row; B4-TABLES-v2 row S3).

Every daemon client starts against the `--id` daemon of its radio with its socket arguments: the client's
resolved argv carries its instance's socket paths, each under its own option, as two whole adjacent tokens.

Which components are daemon clients is derived from the manifest, not listed (ledger L22): a component whose run
or config-file parameter defaults to a daemon socket template (`/tmp/lora-{rid}.sock`, `loraf-`, `loraconf-`), or
that holds a `loraham.daemon-socket.*` resource. Every component of the manifest is classified exactly once
(CLASS): a new component fails here until it is, and a derived client must be an argv or a config-file client.

Measured at the release before L5 (INTERFACE-B4-v8 §4): chat's `--data-socket` is the daemon's raw socket and
the KISS TNC's is the framed one; both carry `--conf-socket`. The expected paths are literal strings
(socket directory `/tmp`, manifest LORAHAM_SOCKET_DIR); this file imports no radios module, so on the release
before L5 the assertions fail by behaviour (the band paths `/tmp/lora433.sock` …).

graywolf is not a daemon client (it speaks KISS over TCP to the TNC; B4-D4). The MeshCore node reads its sockets
from its config file (meshcore.toml); its rendered file is test_client_by_radio.py's.
"""
from __future__ import annotations

import re

import pytest

from lhpc.core import commands
from lhpc.core.config import Config
from lhpc.core.manifest import load_manifest

RT, SRC = "/rt", "/rt/src/x"
SOCKET_DIR = "/tmp"
COMPS = {c.id: c for st in load_manifest() for c in st.components}

# component id -> the (option, socket kind) pairs it must carry at L5
PAIRS = {
    "loraham-chat": (("--data-socket", "data"), ("--conf-socket", "conf")),
    "loraham-voice": (("--data-socket", "data"), ("--conf-socket", "conf")),
    "loraham-voice-cli": (("--data-socket", "data"), ("--conf-socket", "conf")),
    "loraham-kiss-tnc": (("--data-socket", "frame"), ("--conf-socket", "conf")),
    "meshcom-bridge": (("--data-socket", "frame"), ("--conf-socket", "conf")),
}
# component id -> the (config-file key, socket kind) pairs its config file carries
FILE_PAIRS = {
    "meshcore-node": (("data_socket", "frame"), ("config_socket", "conf")),
}
OWNER = "loraham-daemon"

# Every component of the manifest, classified once: "argv" (PAIRS), "file" (FILE_PAIRS), "owner" (the daemon
# itself), "none" (no daemon socket).
CLASS = {
    OWNER: "owner",
    **dict.fromkeys(PAIRS, "argv"),
    **dict.fromkeys(FILE_PAIRS, "file"),
    **dict.fromkeys((
        "radiolib", "libch341", "loraham-kiss-serial", "graywolf",
        "meshtastic-gps", "meshtastic", "meshtastic-web", "meshtastic-cli",
        "meshcom-gps", "meshcom-gps-relay", "meshcom-qemu", "meshcom-firmware",
        "meshcore-gps", "meshcore-webui", "meshcore-cli", "openhop-repeater-src",
        "rns-lora-interface", "rns", "nomadnet", "lxmd", "sideband", "meshchat"), "none"),
}
_TEMPLATE = re.compile(r"^/tmp/lora(f|conf)?-\{rid\}\.sock$")
_KIND = {"": "data", "f": "frame", "conf": "conf"}


def _socket_defaults(comp):
    """(where, key, kind) of each run or config-file parameter whose default is a daemon socket template."""
    out = []
    file_params = getattr(comp.config_file, "params", ()) if comp.config_file else ()
    for where, params in (("run", comp.run_params or ()), ("file", file_params or ())):
        for p in params:
            m = _TEMPLATE.match(str(getattr(p, "default", "")))
            if m:
                out.append((where, p.name if where == "run" else p.key, _KIND[m.group(1) or ""]))
    return out


def _is_daemon_socket(key):
    return key.split(".")[:2] == ["loraham", "daemon-socket"]


def _holds_daemon_socket(comp):
    return any(_is_daemon_socket(r.key) for r in comp.resources)


def _derived(comp):
    """The class the manifest itself implies for `comp` (CLASS must agree)."""
    wheres = {w for w, _k, _kind in _socket_defaults(comp)}
    if comp.id == OWNER:
        return "owner"
    if "run" in wheres:
        return "argv"
    if "file" in wheres:
        return "file"
    return "client without sockets" if _holds_daemon_socket(comp) else "none"


def _socket(kind, rid):
    name = {"conf": f"loraconf-{rid}", "data": f"lora-{rid}", "frame": f"loraf-{rid}"}[kind]
    return f"{SOCKET_DIR}/{name}.sock"


def _argv(comp, slot, rid):
    inst = commands.Instance(slot, rid, {})
    return commands.expand_argv(comp.run_argv, comp, {}, Config().operator, RT, SRC, comp.band, inst=inst)


def _has_pair(argv, opt, value):
    return any(argv[i] == opt and argv[i + 1] == value for i in range(len(argv) - 1))


def test_every_manifest_component_is_classified_once():
    assert set(CLASS) == set(COMPS), (sorted(set(COMPS) - set(CLASS)), sorted(set(CLASS) - set(COMPS)))
    assert not set(PAIRS) & set(FILE_PAIRS)


@pytest.mark.parametrize("comp_id", sorted(COMPS))
def test_the_class_is_what_the_manifest_implies(comp_id):
    assert _derived(COMPS[comp_id]) == CLASS.get(comp_id), (comp_id, _socket_defaults(COMPS[comp_id]))


def test_the_daemon_holds_the_daemon_socket_of_each_band():
    assert {r.key for r in COMPS[OWNER].resources if _is_daemon_socket(r.key)} == {
        "loraham.daemon-socket.{radio:433}", "loraham.daemon-socket.{radio:868}"}


@pytest.mark.parametrize("comp_id", sorted(FILE_PAIRS))
def test_a_config_file_client_names_its_radios_sockets(comp_id):
    got = {(k, kind) for where, k, kind in _socket_defaults(COMPS[comp_id]) if where == "file"}
    assert got == set(FILE_PAIRS[comp_id]), got


@pytest.mark.parametrize("comp_id", sorted(PAIRS))
def test_an_argv_clients_parameters_are_its_pairs(comp_id):
    """The run parameters' socket kinds are the pairs' kinds (the option names are checked on the argv below)."""
    got = sorted(kind for where, _k, kind in _socket_defaults(COMPS[comp_id]) if where == "run")
    assert got == sorted(kind for _opt, kind in PAIRS[comp_id]), got


@pytest.mark.parametrize("slot,rid", [(0, "r1"), (0, "r2"), (1, "r2"), (1, "r12")])
@pytest.mark.parametrize("comp_id", sorted(PAIRS))
def test_t_l5_each_client_carries_its_radios_sockets_under_their_options(comp_id, slot, rid):
    argv = _argv(COMPS[comp_id], slot, rid)
    for opt, kind in PAIRS[comp_id]:
        assert _has_pair(argv, opt, _socket(kind, rid)), (opt, _socket(kind, rid), argv)


@pytest.mark.parametrize("comp_id", sorted(PAIRS))
def test_t_l5_a_socket_under_the_wrong_option_is_not_accepted(comp_id):
    """The data option never carries the conf socket, and two radios never share a socket."""
    a, b = _argv(COMPS[comp_id], 0, "r1"), _argv(COMPS[comp_id], 1, "r2")
    assert not _has_pair(a, "--data-socket", _socket("conf", "r1"))
    assert not [t for t in a if t.endswith("-r2.sock")]
    assert not [t for t in b if t.endswith("-r1.sock")]


def test_graywolf_claims_no_daemon_socket():
    """B4-D4: graywolf is excluded from T-L5; it must stay a non-client (no daemon-socket claim)."""
    gw = [c for c in COMPS.values() if c.id.startswith("graywolf")]
    assert gw
    for c in gw:
        assert not [r for r in c.resources if "daemon-socket" in r.key or "radio-user" in r.key], c.id
