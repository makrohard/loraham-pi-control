"""A firewall apply interrupted at every durable write is recovered and can be retried.

The root helper's apply (`firewall_helper.op_apply`) journals `firewall.journal.json` (begin),
writes the new snapshot to a staging name, journals `snapshot-staged`, loads and verifies the
live table, promotes the staging file over the canonical snapshot, records or drops the
transition file, removes the journal and writes the receipt `check.json` and its log line. The helper writes with
its own `atomic_write`, `_durable_unlink` and `os.replace` (it imports nothing from lhpc), which
the harness gates. Each case fails ONE write (disk full, I/O error, Ctrl-C) while a changed
candidate replaces an applied one, and proves: the next check (`op_check`, the timer's and the
boot loader's entry, which run `recover()` first) leaves no journal or staging file and a live
table that is the canonical snapshot — old or new, never a mix — with a `verified` receipt, and
the same apply then succeeds.
"""
from __future__ import annotations

import json

import pytest

from interrupts import FAILURES, durable_writes, run_interrupted, tree
from lhpc.core import firewall as fw
from lhpc.core import firewall_helper as fh

pytestmark = pytest.mark.safety("firewall-fail-closed")

RAW = [(fh, "atomic_write"), (fh, "_durable_unlink", lambda e: None), (fh, "os.replace")]
# The re-apply's durable writes, in order (firewall_helper.py `op_apply`).
POINTS = [
    ("firewall_helper.atomic_write", "etc/firewall.journal.json"),               # begin
    ("firewall_helper.atomic_write", "etc/firewall.snapshot.json.staging-*"),
    ("firewall_helper.atomic_write", "etc/firewall.journal.json"),               # snapshot-staged
    ("os.replace", "etc/firewall.snapshot.json.staging-*"),                      # promote
    ("firewall_helper.atomic_write", "etc/firewall.transition.json"),
    ("firewall_helper._durable_unlink", "etc/firewall.journal.json"),
    ("firewall_helper.atomic_write", "run/check.json"),                          # the receipt
    ("firewall_helper.atomic_write", "run/firewall.log"),                        # its log line
]


def _candidate(allow, ingress=()):
    ep = {"id": "kiss.tnc.tcp-8001", "proto": "tcp", "family": "ipv4", "addr": "0.0.0.0",
          "port": 8001, "allow_cidrs": [allow], "selected": True, "deny_default": False,
          "auth": "none", "band": ""}
    return {"schema": fw.CANDIDATE_SCHEMA, "mode": "secure-default", "endpoints": [ep],
            "proxy_ingress": [{"proto": "tcp", "family": "ipv4", "addr": "0.0.0.0", "port": port,
                               "allow_cidrs": [allow]} for port in ingress],
            "ssh_ports": [], "ap": {"enabled": False, "interface": "",
                                                          "cidr": ""}, "extra_allow": []}


# v2 narrows the endpoint and drops v1's proxy ingress, so the apply is transitional (the ingress
# stays allowed until the cleanup apply) and writes the transition record too.
V1 = _candidate("192.168.178.0/24", ingress=(8443,))
V2 = _candidate("10.0.0.0/8")


class _Nft:
    """The helper's `Sys` seam over a simulated kernel table: `nft -f` loads a text, `nft -j list
    table` lists exactly the model that text was rendered from (handles as the kernel adds them),
    so the helper's real live verification decides."""

    def __init__(self, models):
        self.models = models            # rendered nft text -> the model it came from
        self.loaded = None

    def run(self, argv, timeout=30.0, stdin_text=None):
        if argv[:3] == ["nft", "-c", "-f"]:
            return 0, "", ""
        if argv[:2] == ["nft", "-f"]:
            self.loaded = stdin_text
            return 0, "", ""
        if argv[:4] == ["nft", "-j", "list", "tables"]:
            tables = [{"table": {"family": "inet", "name": "lhpc"}}] if self.loaded else []
            return 0, json.dumps({"nftables": tables}), ""
        if argv[:4] == ["nft", "-j", "list", "table"]:
            if self.loaded is None:
                return 1, "", "Error: No such file or directory"
            listing = fh.expected_listing(self.models[self.loaded])
            for i, entry in enumerate(listing):
                for body in entry.values():
                    body["handle"] = i + 1
            return 0, json.dumps({"nftables": [{"metainfo": {"version": "1.1.3"}}, *listing]}), ""
        if argv[:2] == ["nft", "destroy"]:
            self.loaded = None
            return 0, "", ""
        if argv[0] == "sshd":
            return 0, "port 22\n", ""
        return 0, "", ""

    def boot_id(self):
        return "boot-1"

    def boottime(self):
        return 1234.5

    def walltime(self):
        return 1_784_900_000.0


@pytest.fixture
def box(tmp_path, monkeypatch):
    """V1 applied and verified; returns (nft, apply-V2, etc, run)."""
    models = {}
    real_render = fh.render_nft_text

    def render(model):                  # spy: remember which model each loaded text came from
        text = real_render(model)
        models[text] = model
        return text
    monkeypatch.setattr(fh, "render_nft_text", render)
    etc, run = tmp_path / "etc", tmp_path / "run"
    nft = _Nft(models)
    for name, cand in (("v1.json", V1), ("v2.json", V2)):
        (tmp_path / name).write_text(json.dumps(cand))
    assert fh.op_apply(nft, str(tmp_path / "v1.json"), etc_dir=str(etc), run_dir=str(run)) == fh.EXIT_OK
    return nft, lambda: fh.op_apply(nft, str(tmp_path / "v2.json"), etc_dir=str(etc),
                                    run_dir=str(run)), etc, run


def _settled(nft, etc, run):
    """No journal, staging or temp file; the live table is the canonical snapshot; the receipt
    says verified. Returns the snapshot's intent hash."""
    names = set(tree(etc)) | {f"run/{n}" for n in tree(run)}
    assert "firewall.journal.json" not in names
    assert not [n for n in names if ".staging-" in n or ".tmp" in n]
    snap = json.loads((etc / "firewall.snapshot.json").read_text())
    assert nft.loaded == snap["nft_text"]
    assert json.loads((run / "check.json").read_text())["verdict"] == "verified"
    return snap["intent_hash"]


def test_the_reapply_writes_exactly_the_pinned_points(tmp_path, box):
    nft, apply, _etc, _run = box
    with durable_writes(tmp_path, raw=RAW) as log:
        assert apply() == fh.EXIT_OK
    assert log == POINTS


@pytest.mark.parametrize("failure", sorted(FAILURES))
@pytest.mark.parametrize("point", range(len(POINTS)), ids=[f"{w}:{p}" for w, p in POINTS])
def test_an_interrupted_apply_recovers_and_retries(tmp_path, box, point, failure):
    nft, apply, etc, run = box
    v1, v2 = fw.intent_hash(V1), fw.intent_hash(V2)
    run_interrupted(tmp_path, apply, fail_at=point, exc=FAILURES[failure], raw=RAW)

    # (a)+(b) the next check recovers: old or new ruleset whole, live == canonical, verified
    assert fh.op_check(nft, etc_dir=str(etc), run_dir=str(run)) == fh.EXIT_OK
    assert _settled(nft, etc, run) in (v1, v2)
    # (c) the retry applies v2
    assert apply() == fh.EXIT_OK
    assert _settled(nft, etc, run) == v2
