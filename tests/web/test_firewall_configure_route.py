"""The firewall the console configures is the ruleset the operator's apply loads.

`POST /firewall/configure` never runs a privileged command: its effect is the candidate it embeds
in `config/files/firewall/firewall-apply.sh`, which the operator runs with sudo and which hands
that candidate to the root helper. So the applied effect is the helper's ruleset for the candidate
in the script: a route the form allows is accepted, every other declared route is dropped, the
recommended preset drops them all, and a refused submission leaves the script as it was.
"""
from __future__ import annotations

import json
import re

import pytest

from lhpc.core import firewall_helper as fh

pytestmark = [pytest.mark.contract, pytest.mark.safety("firewall-fail-closed")]

SCRIPT = "config/files/firewall/firewall-apply.sh"
SSH = [{"proto": "tcp", "family": "dual", "addr": "*", "port": 22}]
ACCEPT = {port: f"tcp dport {port} accept" for port in (4403, 9443)}
DROP = {port: f'tcp dport {port} drop comment "lhpc-deny:meshtastic.tcp-{port}"'
        for port in (4403, 9443)}


def _configure(client, csrf, **form):
    r = client.post("/firewall/configure", data={"_csrf": csrf(client), **form})
    assert r.status_code in (302, 303)


def _applied(tmp_path) -> list[str]:
    """The rules the root helper loads for the candidate in the apply script, for the two
    meshtastic routes (TCP 4403 and 9443) the default manifest declares."""
    text = (tmp_path / SCRIPT).read_text()
    candidate = json.loads(re.search(r"<<'LHPC_EOF_CANDIDATE'\n(.*?)\nLHPC_EOF_CANDIDATE\n",
                                     text, re.S).group(1))
    assert fh.validate_candidate(candidate) == []
    nft = fh.render_nft_text(fh.resolve_model(candidate, ownership_id="x", ssh_scopes=SSH))
    return sorted((line.strip() for line in nft.splitlines()
                   if re.search(r"dport (4403|9443) ", line)), key=lambda r: r.split()[2])


@pytest.mark.parametrize("allowed,rules", [
    ([], [DROP[4403], DROP[9443]]),
    (["meshtastic.tcp-4403"], [ACCEPT[4403], DROP[9443]]),
    (["meshtastic.tcp-4403", "meshtastic.tcp-9443"], [ACCEPT[4403], ACCEPT[9443]]),
])
def test_an_allowed_route_is_accepted_and_every_other_is_dropped(tmp_path, web, csrf,
                                                                allowed, rules):
    client = web()
    _configure(client, csrf, mode="secure-default", ssh_ports="",
               **{f"allow_{route}": "on" for route in allowed})
    assert _applied(tmp_path) == rules


def test_the_recommended_preset_drops_every_route(tmp_path, web, csrf):
    client = web()
    _configure(client, csrf, mode="secure-default", ssh_ports="",
               **{"allow_meshtastic.tcp-4403": "on"})
    _configure(client, csrf, recommended="yes")
    assert _applied(tmp_path) == [DROP[4403], DROP[9443]]


def test_a_refused_submission_leaves_the_applied_ruleset_as_it_was(tmp_path, web, csrf):
    client = web()
    _configure(client, csrf, mode="secure-default", ssh_ports="",
               **{"allow_meshtastic.tcp-4403": "on"})
    before = (tmp_path / SCRIPT).read_bytes()
    _configure(client, csrf, mode="secure-default", ssh_ports="", ap_enabled="on",
               **{"allow_meshtastic.tcp-9443": "on"})        # an AP without interface/CIDR
    assert (tmp_path / SCRIPT).read_bytes() == before
    assert _applied(tmp_path) == [ACCEPT[4403], DROP[9443]]
