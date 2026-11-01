"""T-L5 / T-V2 at the L5 boundary (PROOF T-L5; plan A §2.3 process match rule and the L5 stage row; B4-TABLES-v2 row S3).

Every daemon client starts against the `--id` daemon of its radio with its socket arguments: the client's
resolved argv carries its instance's socket paths, each under its own option, as two whole adjacent tokens.

Measured at the release before L5 (INTERFACE-B4-v8 §4): chat's `--data-socket` is the daemon's raw socket and
the KISS TNC's is the framed one; both carry `--conf-socket`. The expected paths are literal strings
(socket directory `/tmp`, manifest LORAHAM_SOCKET_DIR); this file imports no radios module, so on the release
before L5 the assertions fail by behaviour (the band paths `/tmp/lora433.sock` …).

Not here: graywolf is not a daemon client (it speaks KISS over TCP to the TNC; B4-D4). voice, voice-cli,
kiss-serial, the MeshCom bridge and the MeshCore node get their pairs when their pins land (INTERFACE §4).
"""
from __future__ import annotations

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
    "loraham-kiss-tnc": (("--data-socket", "frame"), ("--conf-socket", "conf")),
}


def _socket(kind, rid):
    name = {"conf": f"loraconf-{rid}", "data": f"lora-{rid}", "frame": f"loraf-{rid}"}[kind]
    return f"{SOCKET_DIR}/{name}.sock"


def _argv(comp, slot, rid):
    inst = commands.Instance(slot, rid, {})
    return commands.expand_argv(comp.run_argv, comp, {}, Config().operator, RT, SRC, comp.band, inst=inst)


def _has_pair(argv, opt, value):
    return any(argv[i] == opt and argv[i + 1] == value for i in range(len(argv) - 1))


def test_the_listed_clients_exist_in_the_manifest():
    assert set(PAIRS) <= set(COMPS), set(PAIRS) - set(COMPS)


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
