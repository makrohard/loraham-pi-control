"""Managed Firewall — candidate intent model (FW-1).

The candidate is the UNPRIVILEGED side of the two-hash truth model: pure data describing
firewall-relevant operator intent. It must be strict (unknown fields rejected — that is what
keeps nftables STRUCTURE out of unprivileged hands), bounded, and canonically hashable so the
controller can compare saved intent against a receipt's `intent_hash` without ambiguity.
"""

from __future__ import annotations

import copy

import pytest

from lhpc.core import firewall as fw
from lhpc.core.service_base import ActionResult


def _ep(**over):
    e = {"id": "meshtastic.api.tcp-4403", "proto": "tcp", "family": "ipv4",
         "addr": "0.0.0.0", "port": 4403, "allow_cidrs": [],
         "selected": False, "deny_default": True, "auth": "none", "band": ""}
    e.update(over)
    return e


def _candidate(**over):
    c = {
        "schema": fw.CANDIDATE_SCHEMA,
        "mode": "secure-default",
        "endpoints": [
            _ep(),
            _ep(id="kiss.tnc.tcp-8001", port=8001, deny_default=False, selected=True,
                allow_cidrs=["192.168.178.0/24"]),
        ],
        "proxy_ingress": [
            {"proto": "tcp", "family": "ipv4", "addr": "0.0.0.0", "port": 8443,
             "allow_cidrs": ["192.168.178.0/24"]},
        ],
        "ssh_ports": [],
        "ap": {"enabled": False, "interface": "", "cidr": ""},
        "extra_allow": [],
    }
    c.update(over)
    return c


# --- validation: strictness -----------------------------------------------------------------

def test_valid_candidate_has_no_errors():
    assert fw.validate_candidate(_candidate()) == []


def test_unknown_top_level_field_rejected():
    c = _candidate()
    c["surprise"] = 1
    assert any("unknown field" in e and "surprise" in e for e in fw.validate_candidate(c))


@pytest.mark.parametrize("field", ["ownership_id", "table", "chains", "hook", "priority",
                                   "policy", "rules", "comment"])
def test_nftables_structure_fields_are_unknown_fields(field):
    # The candidate must NOT contain or control fixed nftables structure or the ownership
    # identity — the root helper injects those. They are rejected as unknown, top-level AND
    # per-endpoint.
    c = _candidate()
    c[field] = "x"
    assert any("unknown field" in e for e in fw.validate_candidate(c))
    c2 = _candidate()
    c2["endpoints"][0][field] = "x"
    assert any("unknown field" in e for e in fw.validate_candidate(c2))


def test_missing_field_rejected():
    c = _candidate()
    del c["mode"]
    assert any("missing" in e and "mode" in e for e in fw.validate_candidate(c))


def test_wrong_schema_version_rejected():
    assert any("schema" in e for e in fw.validate_candidate(_candidate(schema=99)))


def test_bool_typing_is_strict_and_ints_are_not_bools():
    c = _candidate()
    c["endpoints"][0]["selected"] = 1                     # int is NOT bool
    assert fw.validate_candidate(c)
    c2 = _candidate()
    c2["endpoints"][0]["port"] = True                     # bool is NOT int
    assert fw.validate_candidate(c2)


def test_bounds_ports_cidrs_and_collection_caps():
    assert fw.validate_candidate(_candidate(ssh_ports=[0]))
    assert fw.validate_candidate(_candidate(ssh_ports=[65536]))
    c = _candidate()
    c["endpoints"][0]["allow_cidrs"] = ["not-a-cidr"]
    assert fw.validate_candidate(c)
    big = _candidate(endpoints=[_ep(id=f"e{i}", port=1000 + i) for i in range(fw.MAX_ENDPOINTS + 1)])
    assert any("too many" in e for e in fw.validate_candidate(big))


def test_family_address_consistency():
    c = _candidate()
    c["endpoints"][0].update(family="ipv6", addr="0.0.0.0")     # v4 addr under ipv6
    assert fw.validate_candidate(c)
    c2 = _candidate()
    c2["endpoints"][0].update(family="ipv4", addr="::")          # v6 addr under ipv4
    assert fw.validate_candidate(c2)
    c3 = _candidate()
    c3["endpoints"][0].update(family="dual", addr="*")           # dual requires wildcard
    assert fw.validate_candidate(c3) == []


def test_ap_enabled_requires_interface_and_cidr():
    c = _candidate(ap={"enabled": True, "interface": "", "cidr": ""})
    errs = fw.validate_candidate(c)
    assert any("ap" in e for e in errs)
    ok = _candidate(ap={"enabled": True, "interface": "wlan0", "cidr": "10.42.0.0/24"})
    assert fw.validate_candidate(ok) == []


def test_extra_allow_cannot_cover_a_deny_default_scope():
    # 4403/9443-class exceptions go through their endpoint checkbox (with its warning) —
    # extra_allow entries matching a deny-default endpoint's FULL scope are refused. The
    # comparison is protocol+family+addr+port, never the bare port number.
    c = _candidate(extra_allow=[{"proto": "tcp", "family": "ipv4", "addr": "0.0.0.0",
                                 "port": 4403, "cidr": "0.0.0.0/0"}])
    assert any("deny" in e and "checkbox" in e for e in fw.validate_candidate(c))
    # Same numeric port under a DIFFERENT protocol is a different scope — allowed.
    udp = _candidate(extra_allow=[{"proto": "udp", "family": "ipv4", "addr": "0.0.0.0",
                                   "port": 4403, "cidr": "0.0.0.0/0"}])
    assert fw.validate_candidate(udp) == []


# --- canonical hash --------------------------------------------------------------------------

def test_intent_hash_is_order_independent():
    a = _candidate()
    b = copy.deepcopy(a)
    b["endpoints"].reverse()
    b["proxy_ingress"] = list(reversed(b["proxy_ingress"]))
    assert fw.intent_hash(a) == fw.intent_hash(b)


def test_intent_hash_changes_on_any_semantic_change():
    base = fw.intent_hash(_candidate())
    assert fw.intent_hash(_candidate(mode="compatibility")) != base
    c = _candidate()
    c["endpoints"][1]["selected"] = False
    assert fw.intent_hash(c) != base
    c2 = _candidate()
    c2["endpoints"][1]["allow_cidrs"] = ["10.0.0.0/8"]
    assert fw.intent_hash(c2) != base


def test_intent_hash_refuses_invalid_candidates():
    c = _candidate()
    c["surprise"] = 1
    with pytest.raises(ValueError):
        fw.intent_hash(c)


# --- FW-2: root helper — import hygiene, validator drift, model resolution -------------------

def test_helper_imports_stdlib_only():
    """TRUST BOUNDARY: the installed helper must never import lhpc checkout code. Assert the
    module source imports only an allowlisted stdlib set."""
    import ast
    import pathlib
    src = pathlib.Path("lhpc/core/firewall_helper.py").read_text()
    allowed = {"hashlib", "ipaddress", "json", "re", "os", "sys", "fcntl", "subprocess",
               "time", "tempfile", "ctypes", "errno", "stat", "__future__"}
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Import):
            for a in node.names:
                assert a.name.split(".")[0] in allowed, a.name
        elif isinstance(node, ast.ImportFrom):
            assert (node.module or "").split(".")[0] in allowed, node.module


def test_helper_validator_agrees_with_controller_validator():
    """The schema duplication across the trust boundary is deliberate — this drift test keeps
    both validators in behavioral lockstep over a shared corpus."""
    from lhpc.core import firewall_helper as fh
    corpus = [_candidate(), _candidate(mode="compatibility"),
              _candidate(ap={"enabled": True, "interface": "wlan0", "cidr": "10.42.0.0/24"})]
    bad = [_candidate(schema=99), _candidate(mode="open")]
    b1 = _candidate()
    b1["surprise"] = 1
    b2 = _candidate()
    b2["endpoints"][0]["policy"] = "accept"
    b3 = _candidate(extra_allow=[{"proto": "tcp", "family": "ipv4", "addr": "0.0.0.0",
                                  "port": 4403, "cidr": "0.0.0.0/0"}])
    bad += [b1, b2, b3]
    for c in corpus:
        assert fw.validate_candidate(c) == [] and fh.validate_candidate(c) == []
    for c in bad:
        assert fw.validate_candidate(c) and fh.validate_candidate(c)


def _resolve(cand=None, **kw):
    from lhpc.core import firewall_helper as fh
    kw.setdefault("ownership_id", "abc123")
    kw.setdefault("ssh_scopes", [{"proto": "tcp", "family": "dual", "addr": "*", "port": 22}])
    return fh.resolve_model(cand or _candidate(), **kw)


def test_model_rule_order_lo_then_deny_drops_before_conntrack():
    m = _resolve()
    kinds = [(r.get("match"), r.get("action"), r.get("endpoint")) for r in m["rules"]]
    assert kinds[0] == ("iif-lo", "accept", None)
    # the deny-default endpoint drop comes BEFORE ct rules so pre-established sessions die
    drop_i = next(i for i, r in enumerate(m["rules"]) if r.get("endpoint"))
    ct_i = next(i for i, r in enumerate(m["rules"]) if r.get("match") == "ct-established-related")
    assert drop_i < ct_i


def test_secure_default_baseline_has_dhcp_client_rules_with_ap_disabled():
    m = _resolve()
    matches = {r.get("match") for r in m["rules"]}
    assert "dhcpv4-client" in matches and "dhcpv6-client" in matches
    assert not any(r.get("match", "").startswith("ap-") for r in m["rules"])
    assert m["chain"]["policy"] == "drop"


def test_ap_rules_only_when_enabled_and_dns_on_both_protocols():
    c = _candidate(ap={"enabled": True, "interface": "wlan0", "cidr": "10.42.0.0/24"})
    m = _resolve(c)
    ap_rules = [r for r in m["rules"] if r.get("match", "").startswith("ap-")]
    assert any(r["match"] == "ap-dhcp-server" and "saddr" not in r for r in ap_rules)
    assert {r["proto"] for r in ap_rules if r["match"] == "ap-dns"} == {"udp", "tcp"}


def test_compatibility_drops_every_unselected_listener_and_no_default_drop():
    c = _candidate(mode="compatibility")
    m = _resolve(c)
    assert m["chain"]["policy"] == "accept"
    dropped = {r.get("endpoint") for r in m["rules"] if r["action"] == "drop" and r.get("endpoint")}
    assert dropped == {"meshtastic.api.tcp-4403"}       # unselected; kiss is selected
    c2 = _candidate(mode="compatibility")
    c2["endpoints"][1]["selected"] = False
    dropped2 = {r.get("endpoint") for r in _resolve(c2)["rules"]
                if r["action"] == "drop" and r.get("endpoint")}
    assert dropped2 == {"meshtastic.api.tcp-4403", "kiss.tnc.tcp-8001"}


def test_selected_endpoint_allow_is_family_and_cidr_scoped():
    m = _resolve()
    allows = [r for r in m["rules"] if r["action"] == "accept" and r.get("port") == 8001]
    assert allows and all(r["family"] == "ipv4" for r in allows)
    assert {r.get("saddr") for r in allows} == {"192.168.178.0/24"}


def test_ownership_comment_and_model_hash_sensitivity():
    from lhpc.core import firewall_helper as fh
    m1, m2 = _resolve(), _resolve(ownership_id="other")
    assert m1["comment"] == "lhpc-owned:abc123"
    assert fh.model_hash(m1) != fh.model_hash(m2)          # ownership is part of the model
    m3 = _resolve(_candidate(mode="compatibility"))
    assert fh.model_hash(m3) != fh.model_hash(m1)


def test_transition_scopes_extend_drops_and_allows():
    old = [{"proto": "tcp", "family": "ipv4", "addr": "0.0.0.0", "port": 9001}]
    m = _resolve(transition_drop=old)
    assert any(r.get("transition") and r["action"] == "drop" and r["port"] == 9001
               for r in m["rules"])
    m2 = _resolve(transition_allow=[{"proto": "tcp", "family": "ipv4", "addr": "0.0.0.0",
                                     "port": 8443, "allow_cidrs": ["192.168.178.0/24"]}])
    assert any(r["action"] == "accept" and r.get("port") == 8443 for r in m2["rules"])


# --- FW-2: nft builders vs LIVE round-trip fixture -------------------------------------------
# tests/data/nft_live_roundtrip.json is a REAL `nft -j list table inet lhpc` capture from
# nftables 1.1.3 on the target OS, produced by loading render_nft_text() of exactly the model
# below (load -> capture -> destroy). CI compares structurally without root; the live
# verification phase re-proves it on hardware.

def _roundtrip_model():
    from lhpc.core import firewall_helper as fh
    cand = _candidate(ap={"enabled": True, "interface": "wlan0", "cidr": "10.42.0.0/24"})
    return fh.resolve_model(
        cand, ownership_id="tid",
        ssh_scopes=[{"proto": "tcp", "family": "dual", "addr": "*", "port": 22}],
        transition_drop=[{"proto": "tcp", "family": "ipv4", "addr": "0.0.0.0", "port": 9001}],
        transition_allow=[{"proto": "tcp", "family": "ipv4", "addr": "*", "port": 8446,
                           "allow_cidrs": ["192.168.178.0/24"]}])


def test_expected_listing_matches_live_roundtrip_fixture():
    import pathlib
    from lhpc.core import firewall_helper as fh
    live = (pathlib.Path(__file__).resolve().parent / "data" / "nft_live_roundtrip.json").read_text()
    verdict, detail = fh.compare_live(live, _roundtrip_model())
    assert (verdict, detail) == ("verified", "")


def test_compare_live_verdict_matrix():
    import json as _json
    import pathlib
    from lhpc.core import firewall_helper as fh
    m = _roundtrip_model()
    live = _json.loads((pathlib.Path(__file__).resolve().parent / "data" / "nft_live_roundtrip.json").read_text())

    extra = _json.loads(_json.dumps(live))               # a foreign rule appended -> mismatch
    extra["nftables"].append({"rule": {"family": "inet", "table": "lhpc", "chain": "input",
                                       "handle": 999, "expr": [{"accept": None}]}})
    assert fh.compare_live(_json.dumps(extra), m)[0] == "mismatch"

    missing = _json.loads(_json.dumps(live))             # a baseline rule removed -> mismatch
    del missing["nftables"][5]
    assert fh.compare_live(_json.dumps(missing), m)[0] == "mismatch"

    foreign = _json.loads(_json.dumps(live))             # ownership comment changed -> not-owned
    for e in foreign["nftables"]:
        if "table" in e:
            e["table"]["comment"] = "someone-else"
    assert fh.compare_live(_json.dumps(foreign), m)[0] == "not-owned"

    assert fh.compare_live(_json.dumps({"nftables": []}), m)[0] == "missing-table"
    assert fh.compare_live("not json", m)[0] == "error"

    counters = _json.loads(_json.dumps(live))            # counter atom is volatile -> verified
    for e in counters["nftables"]:
        if "rule" in e:
            e["rule"]["expr"].insert(len(e["rule"]["expr"]) - 1, {"counter": {"packets": 5, "bytes": 1}})
            break
    assert fh.compare_live(_json.dumps(counters), m)[0] == "verified"


def test_wildcard_binds_scope_by_family_never_daddr():
    from lhpc.core import firewall_helper as fh
    text = fh.render_nft_text(_roundtrip_model())
    assert "daddr 0.0.0.0" not in text and "daddr ::" not in text
    assert "meta nfproto ipv4 tcp dport 4403 drop" in text
    # nft canonicalizes nfproto away when a same-family saddr match exists — the builder
    # must not emit it there or live comparison would forever mismatch.
    assert "meta nfproto ipv4 ip saddr" not in text


# --- FW-2: helper runtime — operations, receipt, lock, recovery, SSH resolution --------------

class _FakeSys:
    """Programmable Sys seam. `nft -j list table` serves a listing the TEST precomputes for
    the exact model the operation will resolve (ownership id is pre-seeded, SSH responses are
    fixed), so live verification runs the REAL comparison code end to end."""

    def __init__(self):
        self.calls = []
        self.loaded_text = None
        self.listing = None                      # JSON text served AFTER a load (or if preexisting)
        # Model reality: a first apply sees NO table until it loads one. `preexisting=True` means
        # the table is present BEFORE any load (a foreign/re-apply/reset scenario). `nft -f` sets
        # `_loaded`. This lets op_apply's entry checks (mint-after-absence-proof; refuse an owned
        # table with a missing snapshot) see the correct pre-load state.
        self.preexisting = False
        self._loaded = False
        self.tables_listing = {"nftables": [{"table": {"family": "inet", "name": "lhpc"}}]}
        self.sshd_t = "port 22\n"
        self.ss_out = ""

    def run(self, argv, timeout=30.0, stdin_text=None):
        self.calls.append(argv)
        if argv[:3] == ["nft", "-c", "-f"]:
            return 0, "", ""
        if argv[:2] == ["nft", "-f"]:
            self.loaded_text = stdin_text
            self._loaded = True
            return 0, "", ""
        if argv[:4] == ["nft", "-j", "list", "table"]:
            if self.listing is None or not (self._loaded or self.preexisting):
                return 1, "", "Error: No such file or directory"
            return 0, self.listing, ""
        if argv[:4] == ["nft", "-j", "list", "tables"]:
            import json as _json
            return 0, _json.dumps(self.tables_listing), ""
        if argv[:2] == ["nft", "destroy"]:
            self.listing = None
            self._loaded = False
            return 0, "", ""
        if argv[0] == "sshd":
            return (0, self.sshd_t, "") if self.sshd_t else (1, "", "no sshd")
        if argv[0] == "systemctl":
            return 0, "", ""
        if argv[0] == "ss":
            return 0, self.ss_out, ""
        return 0, "", ""

    def boot_id(self):
        return "boot-1"

    def boottime(self):
        return 1234.5

    def walltime(self):
        return 1_784_900_000.0


def _seed_meta(etc, ownership="fixedid01"):
    import json as _json
    import os as _os
    _os.makedirs(etc, exist_ok=True)
    from lhpc.core import firewall_helper as fh
    fh.atomic_write(f"{etc}/firewall.meta.json",
                    _json.dumps({"protocol": fh.PROTOCOL_VERSION,
                                 "ownership_id": ownership}), 0o644)


def _expected_live_json(cand, ownership="fixedid01", **kw):
    import json as _json
    from lhpc.core import firewall_helper as fh
    m = fh.resolve_model(cand, ownership_id=ownership,
                         ssh_scopes=[{"proto": "tcp", "family": "dual", "addr": "*",
                                      "port": 22}], **kw)
    listing = fh.expected_listing(m)
    for i, e in enumerate(listing):                       # simulate kernel handles
        for body in e.values():
            body["handle"] = i + 1
    return _json.dumps({"nftables": [{"metainfo": {"version": "1.1.3"}}, *listing]})


@pytest.mark.contract
@pytest.mark.safety("firewall-fail-closed")
def test_apply_happy_path_writes_snapshot_and_verified_receipt(tmp_path):
    import json as _json
    from lhpc.core import firewall_helper as fh
    etc, run = str(tmp_path / "etc"), str(tmp_path / "run")
    _seed_meta(etc)
    cand = _candidate()
    sysx = _FakeSys()
    sysx.listing = _expected_live_json(cand)
    cpath = tmp_path / "cand.json"
    cpath.write_text(_json.dumps(cand))
    rc = fh.op_apply(sysx, str(cpath), etc_dir=etc, run_dir=run)
    assert rc == fh.EXIT_OK
    snap = _json.loads((tmp_path / "etc" / "firewall.snapshot.json").read_text())
    assert snap["intent_hash"] == fw.intent_hash(cand)     # helper hash == controller hash
    assert snap["model_hash"] and snap["nft_text"].startswith("destroy table inet lhpc")
    receipt = _json.loads((tmp_path / "run" / "check.json").read_text())
    assert receipt["verdict"] == "verified" and receipt["boot_id"] == "boot-1"
    assert receipt["intent_hash"] != receipt["model_hash"]  # two DISTINCT hashes
    assert not (tmp_path / "etc" / "firewall.journal.json").exists()


def test_apply_transition_then_cleanup(tmp_path):
    import json as _json
    from lhpc.core import firewall_helper as fh
    etc, run = str(tmp_path / "etc"), str(tmp_path / "run")
    _seed_meta(etc)
    sysx = _FakeSys()
    c1 = _candidate()
    p1 = tmp_path / "c1.json"
    p1.write_text(_json.dumps(c1))
    sysx.listing = _expected_live_json(c1)
    assert fh.op_apply(sysx, str(p1), etc_dir=etc, run_dir=run) == fh.EXIT_OK

    c2 = _candidate()                                     # proxy moves 8443 -> 9444
    c2["proxy_ingress"][0]["port"] = 9444
    old_ing = [{"proto": "tcp", "family": "ipv4", "addr": "0.0.0.0", "port": 8443,
                "allow_cidrs": ["192.168.178.0/24"]}]
    sysx.listing = _expected_live_json(c2, transition_allow=old_ing)
    p2 = tmp_path / "c2.json"
    p2.write_text(_json.dumps(c2))
    assert fh.op_apply(sysx, str(p2), etc_dir=etc, run_dir=run) == fh.EXIT_OK
    receipt = _json.loads((tmp_path / "run" / "check.json").read_text())
    assert receipt["transitional"] is True                # old ingress preserved -> amber
    assert (tmp_path / "etc" / "firewall.transition.json").exists()
    assert "8443" in ((tmp_path / "etc" / "firewall.snapshot.json").read_text())

    sysx.listing = _expected_live_json(c2)                # cleanup: final model only
    assert fh.op_apply(sysx, str(p2), cleanup=True, etc_dir=etc, run_dir=run) == fh.EXIT_OK
    receipt = _json.loads((tmp_path / "run" / "check.json").read_text())
    assert receipt["transitional"] is False
    assert not (tmp_path / "etc" / "firewall.transition.json").exists()


@pytest.mark.contract
@pytest.mark.safety("firewall-fail-closed")
def test_apply_refuses_invalid_candidate_and_not_owned_table(tmp_path):
    import json as _json
    from lhpc.core import firewall_helper as fh
    etc, run = str(tmp_path / "etc"), str(tmp_path / "run")
    _seed_meta(etc)
    sysx = _FakeSys()
    bad = tmp_path / "bad.json"
    bad.write_text(_json.dumps({"schema": 1}))
    assert fh.op_apply(sysx, str(bad), etc_dir=etc, run_dir=run) == fh.EXIT_CANDIDATE
    assert not (tmp_path / "etc" / "firewall.snapshot.json").exists()

    cand = _candidate()
    good = tmp_path / "good.json"
    good.write_text(_json.dumps(cand))
    foreign = _expected_live_json(cand, ownership="SOMEONE-ELSE")
    sysx.listing = foreign
    sysx.preexisting = True                                # a foreign table is live BEFORE apply
    assert fh.op_apply(sysx, str(good), etc_dir=etc, run_dir=run) == fh.EXIT_NOT_OWNED
    assert not (tmp_path / "etc" / "firewall.snapshot.json").exists()


def test_secure_default_aborts_on_unresolvable_ssh_compat_proceeds(tmp_path):
    import json as _json
    from lhpc.core import firewall_helper as fh
    etc, run = str(tmp_path / "etc"), str(tmp_path / "run")
    _seed_meta(etc)
    sysx = _FakeSys()
    sysx.sshd_t = ""                                       # sshd -T unavailable
    cand = _candidate()
    p = tmp_path / "c.json"
    p.write_text(_json.dumps(cand))
    assert fh.op_apply(sysx, str(p), etc_dir=etc, run_dir=run) == fh.EXIT_CANDIDATE

    compat = _candidate(mode="compatibility")
    from lhpc.core import firewall_helper as fh2
    m = fh2.resolve_model(compat, ownership_id="fixedid01", ssh_scopes=[])
    listing = fh2.expected_listing(m)
    for i, e in enumerate(listing):
        for body in e.values():
            body["handle"] = i + 1
    sysx.listing = _json.dumps({"nftables": [{"metainfo": {}}, *listing]})
    p2 = tmp_path / "c2.json"
    p2.write_text(_json.dumps(compat))
    assert fh.op_apply(sysx, str(p2), etc_dir=etc, run_dir=run) == fh.EXIT_OK


def test_check_verdicts_and_busy_leaves_receipt_untouched(tmp_path):
    import json as _json
    from lhpc.core import firewall_helper as fh
    etc, run = str(tmp_path / "etc"), str(tmp_path / "run")
    _seed_meta(etc)
    cand = _candidate()
    sysx = _FakeSys()
    sysx.listing = _expected_live_json(cand)
    p = tmp_path / "c.json"
    p.write_text(_json.dumps(cand))
    assert fh.op_apply(sysx, str(p), etc_dir=etc, run_dir=run) == fh.EXIT_OK
    assert fh.op_check(sysx, etc_dir=etc, run_dir=run) == fh.EXIT_OK

    mutated = _json.loads(sysx.listing)                   # foreign rule appended -> mismatch
    mutated["nftables"].append({"rule": {"family": "inet", "table": "lhpc",
                                         "chain": "input", "handle": 99,
                                         "expr": [{"accept": None}]}})
    sysx.listing = _json.dumps(mutated)
    assert fh.op_check(sysx, etc_dir=etc, run_dir=run) == fh.EXIT_FAIL
    receipt = _json.loads((tmp_path / "run" / "check.json").read_text())
    assert receipt["verdict"] == "mismatch"

    # BUSY: another operation holds the lock -> EXIT_BUSY and the receipt is NOT rewritten.
    before = (tmp_path / "run" / "check.json").read_text()
    lock = fh.OperationLock(f"{etc}/.firewall.lock")
    assert lock.acquire(wait=True)
    try:
        assert fh.op_check(sysx, etc_dir=etc, run_dir=run) == fh.EXIT_BUSY
    finally:
        lock.release()
    assert (tmp_path / "run" / "check.json").read_text() == before


def test_recovery_finishes_promoted_apply_forward(tmp_path):
    # P2-1 main crash boundary: the snapshot was atomically PROMOTED (canonical == new hash) but
    # the process crashed BEFORE the journal was removed. Recovery must see the expected new
    # canonical hash and finish forward safely (re-load + drop the journal).
    import hashlib
    import json as _json
    from lhpc.core import firewall_helper as fh
    etc, run = str(tmp_path / "etc"), str(tmp_path / "run")
    _seed_meta(etc)
    cand = _candidate()
    sysx = _FakeSys()
    sysx.listing = _expected_live_json(cand)
    p = tmp_path / "c.json"
    p.write_text(_json.dumps(cand))
    assert fh.op_apply(sysx, str(p), etc_dir=etc, run_dir=run) == fh.EXIT_OK
    canon = (tmp_path / "etc" / "firewall.snapshot.json").read_text()
    new_hash = hashlib.sha256(canon.encode("utf-8")).hexdigest()
    fh.atomic_write(f"{etc}/firewall.journal.json", _json.dumps(
        {"op": "apply", "txid": "t", "phase": "snapshot-staged", "old_snapshot": None,
         "old_hash": "", "new_hash": new_hash,
         "staged": f"{etc}/firewall.snapshot.json.staging-t"}), 0o600)
    sysx.loaded_text = None
    assert fh.op_check(sysx, etc_dir=etc, run_dir=run) == fh.EXIT_OK
    assert sysx.loaded_text is not None                    # recovery re-loaded the promoted snapshot
    assert not (tmp_path / "etc" / "firewall.journal.json").exists()


def test_recovery_rolls_back_unpromoted_apply(tmp_path):
    # P2-1: journal at snapshot-staged but the canonical snapshot is NOT the new one (promotion
    # never happened) -> discard the staging file, keep the old canonical, drop the journal.
    import json as _json
    from lhpc.core import firewall_helper as fh
    etc, run = str(tmp_path / "etc"), str(tmp_path / "run")
    _seed_meta(etc)
    cand = _candidate()
    sysx = _FakeSys()
    sysx.listing = _expected_live_json(cand)
    p = tmp_path / "c.json"
    p.write_text(_json.dumps(cand))
    assert fh.op_apply(sysx, str(p), etc_dir=etc, run_dir=run) == fh.EXIT_OK
    old_canon = (tmp_path / "etc" / "firewall.snapshot.json").read_text()
    staged = f"{etc}/firewall.snapshot.json.staging-t2"
    fh.atomic_write(staged, _json.dumps({"stale": True}), 0o644)
    fh.atomic_write(f"{etc}/firewall.journal.json", _json.dumps(
        {"op": "apply", "txid": "t2", "phase": "snapshot-staged",
         "old_snapshot": _json.loads(old_canon), "old_hash": "x",
         "new_hash": "deadbeef", "staged": staged}), 0o600)
    assert fh.op_check(sysx, etc_dir=etc, run_dir=run) == fh.EXIT_OK
    assert not (tmp_path / "etc" / "firewall.journal.json").exists()
    import os as _os
    assert not _os.path.exists(staged)                     # staging discarded
    # canonical snapshot preserved (the old one, never overwritten before promotion)
    assert (tmp_path / "etc" / "firewall.snapshot.json").read_text() == old_canon


def test_recovery_fails_closed_on_unknown_journal_shape(tmp_path):
    # P2-1: a parseable journal with an unknown op/phase is an interrupted op of unknown state ->
    # fail closed (EXIT_INTERNAL), never silently deleted.
    import json as _json
    from lhpc.core import firewall_helper as fh
    etc, run = str(tmp_path / "etc"), str(tmp_path / "run")
    _seed_meta(etc)
    sysx = _FakeSys()
    fh.atomic_write(f"{etc}/firewall.journal.json",
                    _json.dumps({"op": "apply", "phase": "who-knows"}), 0o600)
    assert fh.op_check(sysx, etc_dir=etc, run_dir=run) == fh.EXIT_INTERNAL
    fh.atomic_write(f"{etc}/firewall.journal.json", _json.dumps({"op": "mystery"}), 0o600)
    assert fh.op_check(sysx, etc_dir=etc, run_dir=run) == fh.EXIT_INTERNAL


def test_corrupt_journal_fails_closed(tmp_path):
    # FW-R9: a journal that EXISTS but does not parse is an interrupted op of unknown state —
    # recover() must FAIL CLOSED so op_check/op_load abort rather than proceed on that state.
    from lhpc.core import firewall_helper as fh
    etc, run = str(tmp_path / "etc"), str(tmp_path / "run")
    _seed_meta(etc)
    fh.atomic_write(f"{etc}/firewall.journal.json", "{ this is not json", 0o600)
    sysx = _FakeSys()
    assert fh.op_check(sysx, etc_dir=etc, run_dir=run) == fh.EXIT_INTERNAL
    # An ABSENT journal is the safe case — recover proceeds.
    p = fh._paths(etc, run)
    fh._durable_unlink(p["journal"])
    assert fh.recover(sysx, p) is True


def test_reset_removes_only_when_owned(tmp_path):
    import json as _json
    from lhpc.core import firewall_helper as fh
    etc, run = str(tmp_path / "etc"), str(tmp_path / "run")
    _seed_meta(etc)
    cand = _candidate()
    sysx = _FakeSys()
    sysx.listing = _expected_live_json(cand)
    p = tmp_path / "c.json"
    p.write_text(_json.dumps(cand))
    assert fh.op_apply(sysx, str(p), etc_dir=etc, run_dir=run) == fh.EXIT_OK

    sysx.listing = _expected_live_json(cand, ownership="SOMEONE-ELSE")   # foreign-replaced
    assert fh.op_reset(sysx, etc_dir=etc, run_dir=run) == fh.EXIT_NOT_OWNED
    assert (tmp_path / "etc" / "firewall.snapshot.json").exists()        # nothing removed

    sysx.listing = _expected_live_json(cand)                              # ours again
    assert fh.op_reset(sysx, etc_dir=etc, run_dir=run) == fh.EXIT_OK
    for leaf in ("firewall.snapshot.json", "firewall.meta.json", "firewall.transition.json"):
        assert not (tmp_path / "etc" / leaf).exists()
    assert not (tmp_path / "run" / "check.json").exists()


def test_ssh_parsers():
    from lhpc.core import firewall_helper as fh
    ports, scopes = fh.parse_sshd_t("port 22\nport 2222\nlistenaddress 192.168.178.5:2200\n")
    assert ports == [22, 2222]
    assert scopes == [{"proto": "tcp", "family": "ipv4", "addr": "192.168.178.5",
                       "port": 2200}]
    assert fh.parse_unit_cmdline("/usr/sbin/sshd -D -p 2022 -o Port=2023 -f /etc/ssh/alt") \
        == ([2022, 2023], "/etc/ssh/alt")
    assert fh.parse_socket_listen("[::]:2222 0.0.0.0:22") == [22, 2222]
    scopes, confident = fh.resolve_ssh_scopes(_FakeSys(), [2222])
    ports = {s["port"] for s in scopes}
    # override 2222 is honored AND the effective sshd -T port 22 is still unioned (no lockout)
    assert confident and {22, 2222} <= ports


# --- FW-3: root artifact renderers -----------------------------------------------------------

def test_units_carry_mandated_settings_and_never_private_network():
    from lhpc.core import firewall as fwm
    loader, checker, timer = (fwm.render_loader_unit(), fwm.render_checker_unit(),
                              fwm.render_checker_timer())
    for text in (loader, checker):
        directives = [ln for ln in text.splitlines() if ln and not ln.startswith("#")]
        # Host netns is mandatory: no private-network DIRECTIVE may exist (the comment
        # naming the prohibition is fine — only real unit directives count).
        assert not any(d.startswith(("PrivateNetwork", "NetworkNamespacePath"))
                       for d in directives)
        assert "CapabilityBoundingSet=CAP_NET_ADMIN" in text
        assert "NoNewPrivileges=yes" in text
        assert "RuntimeDirectory=lhpc-firewall" in text
        assert "RuntimeDirectoryPreserve=yes" in text
    assert "Before=network-pre.target" in loader and "After=local-fs.target nftables.service" in loader
    assert "RequiresMountsFor=/etc/lhpc" in loader
    assert f"ExecStart={fwm.HELPER_DEST} load" in loader        # helper, never nft -f directly
    assert f"ExecStart={fwm.HELPER_DEST} check" in checker
    assert "OnBootSec=60" in timer and "OnUnitActiveSec=60" in timer


def test_apply_script_embeds_helper_byte_exact_and_is_valid_bash(tmp_path):
    import subprocess
    from lhpc.core import firewall as fwm
    script = fwm.render_apply_script('{"schema":1}')
    assert fwm.helper_source() in script                        # BYTE-exact embedding
    assert 'if [ "$(id -u)" -ne 0 ]' in script                  # root guard
    assert f"{fwm.HELPER_DEST} apply {fwm.CANDIDATE_DEST}" in script   # installed helper runs
    # The SCRIPT itself never invokes nft (the embedded helper source may reference it) —
    # every firewall mutation goes through the installed root-owned helper.
    in_heredoc = False
    for ln in script.splitlines():
        if ln.startswith("cat > ") and "<<'LHPC_EOF_" in ln:
            in_heredoc = ln.split("<<'")[1].rstrip("'")
        elif in_heredoc and ln == in_heredoc:
            in_heredoc = False
        elif not in_heredoc:
            assert not ln.strip().startswith("nft "), ln
    assert "systemctl enable lhpc-firewall.service lhpc-firewall-check.timer" in script
    for render in (script, fwm.render_reset_script(), fwm.render_cleanup_script()):
        f = tmp_path / "s.sh"
        f.write_text(render)
        assert subprocess.run(["bash", "-n", str(f)]).returncode == 0


def test_reset_script_names_only_lhpc_artifacts():
    from lhpc.core import firewall as fwm
    reset = fwm.render_reset_script()
    assert f"{fwm.HELPER_DEST} reset" in reset
    for token in ("nftables.conf", "nftables.service enable", "/etc/nftables.d"):
        assert token not in reset                               # foreign config never named
    # Units are disabled+removed via a guarded loop that names each lhpc unit.
    for u in (fwm.LOADER_UNIT, fwm.CHECKER_UNIT, fwm.CHECKER_TIMER):
        assert u in reset
    assert 'rm -f "/etc/systemd/system/$u"' in reset


def test_integration_rev_ignores_trailing_newline_install_drift(tmp_path):
    # LIVE-FOUND: the apply-script heredoc that installs the helper appends a trailing newline, so
    # the on-disk file is `helper_source() + "\n"`. integration_rev must hash the two EQUAL (else
    # Live is never green after a real apply), while still detecting a semantic change.
    import hashlib
    from lhpc.core import firewall as fwm
    src = fwm.helper_source()
    controller = fwm.integration_rev()
    # the installed-helper self-hash side, simulated with the trailing-newline drift:
    installed_drifted = hashlib.sha256((src + "\n").rstrip("\n").encode("utf-8")).hexdigest()
    assert controller == installed_drifted
    # a real change to the body still changes the revision.
    changed = hashlib.sha256((src + "x").rstrip("\n").encode("utf-8")).hexdigest()
    assert changed != controller


def test_helper_source_starts_with_shebang():
    from lhpc.core import firewall as fwm
    assert fwm.helper_source().startswith("#!/usr/bin/python3\n")


# --- FW-5: manifest metadata (fail-closed) + derivation --------------------------------------

def _svc(tmp_path):
    from lhpc.core.paths import Paths
    from lhpc.core.probes.backends import FakeSystem
    from lhpc.core.services import ControllerService
    (tmp_path / "config").mkdir(exist_ok=True)
    return ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))


def test_manifest_firewall_metadata_parsed_for_every_tcp_listener():
    import pathlib
    from lhpc.core.manifest import load_manifest
    stacks = load_manifest(pathlib.Path("lhpc/data/manifest.example.toml"))
    listeners = [(c.id, e) for st in stacks for c in st.components
                 for e in c.endpoints if e.kind == "tcp" and e.role == "listener"]
    assert listeners
    for cid, e in listeners:
        assert e.firewall is not None, f"{cid} {e.address} has no firewall metadata"
    denies = [(c.id, e.address) for st in stacks for c in st.components
              for e in c.endpoints if e.firewall and e.firewall.deny]
    assert ("meshtastic", "127.0.0.1:4403") in denies
    assert ("meshtastic", "127.0.0.1:9443") in denies


def test_manifest_firewall_metadata_fails_closed_on_unknown_key():
    from lhpc.core.manifest import _parse_firewall_meta, ManifestError
    import pytest as _pt
    with _pt.raises(ManifestError):
        _parse_firewall_meta({"port_param": "p", "surprise": 1})
    with _pt.raises(ManifestError):
        _parse_firewall_meta({"auth": "magic"})
    with _pt.raises(ManifestError):
        _parse_firewall_meta({"deny": "yes"})
    assert _parse_firewall_meta(None) is None
    m = _parse_firewall_meta({"auth": "none", "deny": True})
    assert m.deny is True and m.auth == "none"


def test_candidate_derivation_deny_endpoints_are_dual_wildcard(tmp_path):
    svc = _svc(tmp_path)
    cand = svc.firewall_candidate()
    denies = [e for e in cand["endpoints"] if e["deny_default"]]
    assert {e["port"] for e in denies} == {4403, 9443}
    for e in denies:
        assert e["family"] == "dual" and e["addr"] == "*" and not e["selected"]
    fw.intent_hash(cand)               # valid candidate


def test_candidate_includes_console_ingress_when_remote(tmp_path):
    from lhpc.core import config as cfgmod
    svc = _svc(tmp_path)
    cfgmod.save_webserver_config(svc._paths, bind="0.0.0.0", port=8443,
                                 remote_exposed=True, allowed_cidrs=["192.168.178.0/24"])
    svc._invalidate_config()
    cand = svc.firewall_candidate()
    ing = [e for e in cand["proxy_ingress"] if e["port"] == 8443]
    assert ing and ing[0]["allow_cidrs"] == ["192.168.178.0/24"]
    # loopback backends never appear as proxy ingress
    assert all(e["port"] != 5000 for e in cand["proxy_ingress"])


def test_ap_managed_console_ingress_is_unscoped(tmp_path):
    """Operator ruling (live-found lockout): on AP-managed (Lite) boxes the console/proxy
    nft ingress is UNSCOPED — the box roams between its AP subnet and joined WLANs, and a
    subnet-scoped rule locked the operator out of the console on every new network. nginx
    CIDR allowlist + mTLS stay the gate; non-AP boxes keep full CIDR scoping."""
    from lhpc.core import config as cfgmod
    svc = _svc(tmp_path)
    cfgmod.save_webserver_config(svc._paths, bind="0.0.0.0", port=8443,
                                 remote_exposed=True, allowed_cidrs=["10.42.0.0/24"])
    cfgmod.save_firewall_config(svc._paths, ap_enabled=True, ap_interface="wlan0",
                                ap_cidr="10.42.0.0/24")
    svc._invalidate_config()
    cand = svc.firewall_candidate()
    ing = [e for e in cand["proxy_ingress"] if e["port"] == 8443]
    assert ing and ing[0]["allow_cidrs"] == []            # one unrestricted accept
    # non-AP boxes keep scoping (the sibling test above pins that case);
    # and the nft intent no longer changes when the nginx allowlist widens:
    h1 = __import__("lhpc.core.firewall", fromlist=["intent_hash"]).intent_hash(cand)
    cfgmod.save_webserver_config(svc._paths,
                                 allowed_cidrs=["10.42.0.0/24", "192.168.178.0/24"])
    svc._invalidate_config()
    h2 = __import__("lhpc.core.firewall", fromlist=["intent_hash"]).intent_hash(
        svc.firewall_candidate())
    assert h1 == h2                                        # joining a WLAN trips no gate


def test_selected_endpoint_marks_candidate(tmp_path):
    import pathlib
    svc = _svc(tmp_path)
    # tick the 4403 deny endpoint via [firewall] allow_endpoints
    local = svc._paths.runtime_root / "config" / "local.toml"
    local.write_text('[firewall]\nallow_endpoints = "meshtastic.tcp-4403"\n')
    svc._invalidate_config()
    cand = svc.firewall_candidate()
    sel = [e for e in cand["endpoints"] if e["id"] == "meshtastic.tcp-4403"]
    assert sel and sel[0]["selected"] is True
    _ = pathlib


# --- FW-4: hardened receipt reader + status dimensions ---------------------------------------

def _write_receipt(path, **over):
    import json as _json
    import os as _os
    from lhpc.core import firewall_helper as fh
    from lhpc.core import firewall as _fwm
    r = {"protocol": fh.PROTOCOL_VERSION, "integration_rev": _fwm.integration_rev(),
         "verdict": "verified", "detail": "", "intent_hash": "IH", "model_hash": "MH",
         "boot_id": "boot-x", "boottime": 100.0, "walltime": 1.0, "transitional": False,
         "foreign_tables": []}
    r.update(over)
    _os.makedirs(_os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        _json.dump(r, f)


def test_receipt_reader_rejects_nonroot_symlink_and_unsafe(tmp_path, monkeypatch):
    import os as _os
    svc = _svc(tmp_path)
    rp = tmp_path / "receipt.json"
    _write_receipt(str(rp))
    # In the test the file is owned by us (uid != 0), so the root-owner check rejects it.
    assert svc._fw_read_receipt(str(rp)) is None
    # Symlink is refused by O_NOFOLLOW regardless of owner.
    link = tmp_path / "link.json"
    _os.symlink(rp, link)
    assert svc._fw_read_receipt(str(link)) is None
    # Bypass the owner check to prove the mode/shape checks: patch fstat owner to 0 but keep
    # group-write set -> still rejected.
    real_fstat = _os.fstat
    def fake_fstat(fd):
        st = real_fstat(fd)
        return type("S", (), {"st_mode": st.st_mode | 0o020, "st_uid": 0,
                              "st_size": st.st_size})()
    monkeypatch.setattr("lhpc.core.service_firewall.stat.S_ISREG", lambda m: True)
    monkeypatch.setattr(_os, "fstat", fake_fstat)
    assert svc._fw_read_receipt(str(rp)) is None       # group-writable -> reject


def test_owner_is_host_root_userns_translation(monkeypatch):
    """st_uid and uid_map are BOTH relative to the caller's userns and namespaces NEST, so a mapped
    uid 0 is trusted ONLY under the exact INITIAL identity map (0 0 4294967295) — a nested "0 0 1"
    must be rejected. The overflow fallback (web sandbox) is gated on the exact same-ID map + the
    canonical path + a positively-proven managed cgroup."""
    from lhpc.core import service_firewall as m
    monkeypatch.setattr(m, "_overflow_uid", lambda: 65534)
    monkeypatch.setattr(m, "_in_managed_lhpc_unit", lambda: True)        # default: in the managed unit

    def use_map(rows):
        monkeypatch.setattr(m, "_read_uid_map", lambda: rows)

    # initial identity map "0 0 4294967295", st_uid 0 -> ACCEPT; non-root uid -> reject
    use_map([(0, 0, 4294967295)])
    assert m._owner_is_host_root(0, canonical_path=True) is True
    assert m._owner_is_host_root(0, canonical_path=False) is True
    assert m._owner_is_host_root(1000, canonical_path=True) is False

    # DECISIVE nested case: non-initial identity-looking "0 0 1" + st_uid 0 -> REJECT
    use_map([(0, 0, 1)])
    assert m._owner_is_host_root(0, canonical_path=True) is False

    # rootless "0 1000 1" + st_uid 0 -> REJECT
    use_map([(0, 1000, 1)])
    assert m._owner_is_host_root(0, canonical_path=True) is False
    assert m._owner_is_host_root(0, canonical_path=False) is False

    # managed same-ID sandbox "1000 1000 1", overflow owner
    use_map([(1000, 1000, 1)])
    assert m._owner_is_host_root(65534, canonical_path=True) is True    # canonical + managed cgroup
    assert m._owner_is_host_root(65534, canonical_path=False) is False  # non-canonical
    assert m._owner_is_host_root(1000, canonical_path=True) is False    # our own uid, not overflow
    monkeypatch.setattr(m, "_in_managed_lhpc_unit", lambda: False)
    assert m._owner_is_host_root(65534, canonical_path=True) is False   # wrong context (not managed)
    monkeypatch.setattr(m, "_in_managed_lhpc_unit", lambda: True)

    # fail closed: unreadable map, and any multi-row / non-exact map
    use_map(None)
    assert m._owner_is_host_root(0, canonical_path=True) is False
    use_map([(0, 0, 4294967295), (1000, 1000, 1)])
    assert m._owner_is_host_root(0, canonical_path=True) is False


def test_nginx_unit_is_a_trusted_sandbox_for_the_receipt(monkeypatch):
    """The boot gate runs inside lhpc-nginx.service, whose sandbox unmaps host root exactly like the
    console's — so the root receipt shows the overflow uid there too. Excluding that unit made the
    gate permanently unable to see a verified firewall, forcing the console back to loopback on
    every restart (live-found on a Zero). Foreign units stay untrusted."""
    from lhpc.core import service_firewall as m
    monkeypatch.setattr(m, "_overflow_uid", lambda: 65534)
    monkeypatch.setattr(m, "_read_uid_map", lambda: [(1000, 1000, 1)])   # same-ID sandbox map
    base = "0::/user.slice/user-1000.slice/user@1000.service/app.slice/"
    for unit in ("lhpc-nginx.service", "lhpc-web.service", "lhpc-boot-restore.service"):
        monkeypatch.setattr(m, "_own_cgroup_text", lambda u=unit: base + u)
        assert m._in_managed_lhpc_unit() is True
        assert m._owner_is_host_root(65534, canonical_path=True) is True
    for foreign in ("lhpc-firewall.service", "lhpc-selfupdate.service",
                    "x-lhpc-nginx.service.scope", "nginx.service"):
        monkeypatch.setattr(m, "_own_cgroup_text", lambda u=foreign: base + u)
        assert m._in_managed_lhpc_unit() is False
        assert m._owner_is_host_root(65534, canonical_path=True) is False


def test_managed_web_unit_exact_identity(monkeypatch):
    """The managed-unit proof must be the EXACT cgroup-v2 leaf unit, not a substring — a user can
    name a transient scope so the path merely contains 'lhpc-web.service'."""
    from lhpc.core import service_firewall as m
    real = "0::/user.slice/user-1000.slice/user@1000.service/app.slice/lhpc-web.service"
    assert m._managed_unit_leaf(real) == "lhpc-web.service"
    for spoof in ("0::/user.slice/user-1000.slice/user@1000.service/app.slice/attacker-lhpc-web.service.scope",
                  "0::/user.slice/user-1000.slice/user@1000.service/app.slice/lhpc-web.service-evil.scope",
                  "0::/foo/lhpc-web.service.fake"):
        assert m._managed_unit_leaf(spoof) != "lhpc-web.service"
    # ambiguous cgroup (empty, or cgroup-v1 / multi-controller) => None => not the managed unit
    assert m._managed_unit_leaf("") is None
    assert m._managed_unit_leaf("1:name=systemd:/a\n0::/b") is None

    # _in_managed_lhpc_unit drives the EXACT check via the (mockable) cgroup read
    monkeypatch.setattr(m, "_own_cgroup_text", lambda: real)
    assert m._in_managed_lhpc_unit() is True
    monkeypatch.setattr(m, "_own_cgroup_text", lambda: "0::/user.slice/x-lhpc-web.service.scope")
    assert m._in_managed_lhpc_unit() is False
    def boom():
        raise OSError()
    monkeypatch.setattr(m, "_own_cgroup_text", boom)
    assert m._in_managed_lhpc_unit() is False


def test_receipt_reader_userns_translation_end_to_end(tmp_path, monkeypatch):
    """Through _fw_read_receipt on the canonical path: a nested-namespace forgery reading as uid 0
    (map "0 0 1") is REJECTED; the legitimate managed web sandbox (overflow + EXACT lhpc-web.service
    cgroup) is ACCEPTED; and a SPOOFED cgroup whose name merely CONTAINS the unit string is REJECTED."""
    import os as _os

    from lhpc.core import service_firewall as m
    svc = _svc(tmp_path)
    rp = tmp_path / "check.json"
    _write_receipt(str(rp))
    monkeypatch.setattr(fw, "RECEIPT_PATH", str(rp))               # make this file the canonical path
    monkeypatch.setattr(m, "_overflow_uid", lambda: 65534)
    monkeypatch.setattr(m, "_own_cgroup_text",
                        lambda: "0::/user.slice/user-1000.slice/user@1000.service/app.slice/lhpc-web.service")
    monkeypatch.setattr("lhpc.core.service_firewall.stat.S_ISREG", lambda mm: True)
    real = _os.fstat

    def fake_owner(uid):
        monkeypatch.setattr(_os, "fstat", lambda fd: type("S", (), {
            "st_mode": real(fd).st_mode & ~0o022, "st_uid": uid, "st_size": real(fd).st_size})())

    # nested namespace (0 0 1): a fake operator-owned file reads as uid 0 -> MUST be rejected.
    monkeypatch.setattr(m, "_read_uid_map", lambda: [(0, 0, 1)])
    fake_owner(0)
    assert svc._fw_read_receipt() is None
    # legitimate managed web sandbox (same-ID map, overflow owner, EXACT unit) -> accepted.
    monkeypatch.setattr(m, "_read_uid_map", lambda: [(1000, 1000, 1)])
    fake_owner(65534)
    assert svc._fw_read_receipt() is not None
    # SPOOFED cgroup whose name only CONTAINS the unit string -> rejected.
    monkeypatch.setattr(m, "_own_cgroup_text", lambda: "0::/user.slice/x-lhpc-web.service.scope")
    assert svc._fw_read_receipt() is None


def test_status_dimensions_and_freshness(tmp_path, monkeypatch):
    import time as _t
    svc = _svc(tmp_path)
    cand = svc.firewall_candidate()
    ih = fw.intent_hash(cand)
    # Make the reader accept our test file and the integration look installed+enabled.
    rp = tmp_path / "check.json"
    monkeypatch.setattr(svc, "_fw_read_receipt",
                        lambda path=None: _read_plain(rp))
    monkeypatch.setattr(svc, "_fw_integration_present", lambda: True)
    monkeypatch.setattr(svc, "_fw_units_enabled", lambda: True)
    monkeypatch.setattr(svc, "_fw_boot_id", lambda: "boot-x")
    now = _t.clock_gettime(_t.CLOCK_BOOTTIME)

    _write_receipt(str(rp), intent_hash=ih, boottime=now, boot_id="boot-x")
    st = svc.firewall_status()
    assert st["config_ok"] and st["boot_ok"] and st["live_ok"] and st["level"] == "ok"
    assert "Config ✓ · Boot ✓ · Live ✓" in st["line"]

    # LIVE-FOUND: rules verified for the current intent BUT a stale helper revision -> NOT green,
    # and reported as "Update required" (re-apply), never a phantom rule-mismatch.
    _write_receipt(str(rp), intent_hash=ih, boottime=now, boot_id="boot-x",
                   integration_rev="STALE-REV")
    st = svc.firewall_status()
    assert not st["live_ok"] and st["reason"] == "update-required" and st["level"] == "warn"
    assert "Update required" in st["line"]

    # STALE (old boottime) -> not live, not green.
    _write_receipt(str(rp), intent_hash=ih, boottime=now - 100000, boot_id="boot-x")
    st = svc.firewall_status()
    assert not st["live_ok"] and st["level"] == "bad"

    # DIFFERENT boot -> not fresh.
    _write_receipt(str(rp), intent_hash=ih, boottime=now, boot_id="OTHER")
    assert not svc.firewall_status()["live_ok"]

    # Config drift: receipt intent != current intent -> "Changes pending".
    _write_receipt(str(rp), intent_hash="STALE-IH", boottime=now, boot_id="boot-x")
    st = svc.firewall_status()
    assert not st["config_ok"] and st["reason"] == "changes-pending"

    # Copied desired hash is NOT live proof: verdict must be verified too.
    _write_receipt(str(rp), intent_hash=ih, verdict="mismatch", boottime=now, boot_id="boot-x")
    assert not svc.firewall_status()["live_ok"]


def _read_plain(path):
    import json as _json
    from lhpc.core import firewall_helper as fh
    try:
        with open(path) as f:
            r = _json.load(f)
    except (OSError, ValueError):
        return None
    return r if isinstance(r, dict) and r.get("protocol") == fh.PROTOCOL_VERSION else None


# --- FW-6: prospective-set exposure gating ---------------------------------------------------

def _svc_fw_installed(tmp_path, monkeypatch, *, live_ok=True, config_ok=True, state="present"):
    svc = _svc(tmp_path)
    monkeypatch.setattr(svc, "_fw_integration_state", lambda: state)
    st = {"config_ok": config_ok, "live_ok": live_ok}
    monkeypatch.setattr(svc, "firewall_status", lambda: st)
    return svc


def test_gate_noop_when_integration_absent(tmp_path, monkeypatch):
    svc = _svc(tmp_path)
    monkeypatch.setattr(svc, "_fw_integration_state", lambda: "absent")
    allowed, msg, cmds = svc.firewall_gate_activation({8443})
    assert allowed and msg == "" and cmds == []


@pytest.mark.contract
@pytest.mark.safety("firewall-fail-closed")
def test_gate_partial_install_fails_closed(tmp_path, monkeypatch):
    # P1-1: a half-installed firewall must not be trusted — refuse remote activation.
    svc = _svc_fw_installed(tmp_path, monkeypatch, state="partial")
    monkeypatch.setattr(type(svc), "firewall_render",
                        lambda self: ActionResult(True, "regenerated"))
    allowed, msg, _cmds = svc.firewall_gate_activation({8443})
    assert not allowed and "partially installed" in msg

    # ...but a LOOPBACK-ONLY activation activates nothing that needs protecting. Refusing it
    # stranded an operator who was reducing exposure while the integration was broken — and the
    # refusal spoke of "a remote listener" that this change does not create.
    allowed, msg, cmds = svc.firewall_gate_activation(set())
    assert allowed and msg == "" and cmds == []
    monkeypatch.setattr(type(svc), "firewall_render",
                        lambda self: ActionResult(False, "could not render firewall scripts: EACCES"))
    allowed, msg, cmds = svc.firewall_gate_activation(set())
    assert allowed and "could not be regenerated" in msg
    assert not any("firewall-apply.sh" in c for c in cmds)


@pytest.mark.contract
@pytest.mark.safety("firewall-fail-closed")
def test_gate_refuses_even_already_exposed_when_unverified(tmp_path, monkeypatch):
    # P1-1: an already-socket-exposed port is NOT evidence of firewall protection. When the
    # firewall is installed but not verified-current, activating a remote listener is refused
    # (closes the "already exposed -> bypass" hole, incl. CIDR widening on an open port).
    svc = _svc_fw_installed(tmp_path, monkeypatch, live_ok=False, config_ok=True)
    allowed, msg, _cmds = svc.firewall_gate_activation({8443})
    assert not allowed and "Firewall changes pending" in msg


def test_gate_allows_when_no_remote_listener(tmp_path, monkeypatch):
    """Closing the LAST remote listener is always allowed — and must still regenerate the firewall
    scripts, because the intent changed. Returning early on the empty set left firewall-apply.sh
    advertising an ingress that no longer exists (audit)."""
    svc = _svc_fw_installed(tmp_path, monkeypatch, live_ok=False, config_ok=False)
    rendered = []
    monkeypatch.setattr(type(svc), "firewall_render",
                        lambda self: rendered.append(1) or ActionResult(True, "regenerated"))
    allowed, msg, cmds = svc.firewall_gate_activation(set())
    assert allowed and msg == "" and cmds == []
    assert rendered, "an intent change must regenerate the scripts even with nothing left remote"

    # Render failure: still allowed (the exposure only shrank), but say so and offer the re-render.
    monkeypatch.setattr(type(svc), "firewall_render",
                        lambda self: ActionResult(False, "could not render firewall scripts: EACCES"))
    allowed, msg, cmds = svc.firewall_gate_activation(set())
    assert allowed and "could not be regenerated" in msg and cmds == ["lhpc firewall --script"]
    assert not any("firewall-apply.sh" in c for c in cmds)

    # Receipt already matches the current intent -> nothing changed, nothing to regenerate.
    svc2 = _svc_fw_installed(tmp_path, monkeypatch, live_ok=True, config_ok=True)
    calls = []
    monkeypatch.setattr(type(svc2), "firewall_render",
                        lambda self: calls.append(1) or ActionResult(True, "regenerated"))
    assert svc2.firewall_gate_activation(set())[0] and not calls


@pytest.mark.contract
@pytest.mark.safety("firewall-fail-closed")
def test_gate_allows_remote_listener_when_firewall_verified(tmp_path, monkeypatch):
    svc = _svc_fw_installed(tmp_path, monkeypatch, live_ok=True, config_ok=True)
    assert svc.firewall_gate_activation({8443})[0]         # verified + current -> allowed


@pytest.mark.contract
@pytest.mark.safety("firewall-fail-closed")
def test_webserver_apply_blocked_by_pending_firewall(tmp_path, monkeypatch):
    from lhpc.core import config as cfgmod
    svc = _svc(tmp_path)
    cfgmod.save_webserver_config(svc._paths, bind="0.0.0.0", port=8443,
                                 remote_exposed=True, allowed_cidrs=["192.168.178.0/24"])
    svc._invalidate_config()
    monkeypatch.setattr(svc, "_fw_integration_state", lambda: "present")
    monkeypatch.setattr(svc, "firewall_status",
                        lambda: {"config_ok": True, "live_ok": False})
    monkeypatch.setattr("lhpc.core.webserver.nginx_installed", lambda system: True)
    r = svc.webserver_apply()
    assert not r.ok and "Firewall changes pending" in r.summary
    # The exposing config was NOT promoted — the safe file nginx loads is untouched.
    assert (r.data or {}).get("firewall_gate") == "pending"


def _svc_gate_pending(tmp_path, monkeypatch, fw):
    """An exposing console config whose Apply the firewall gate refuses (fw = mutable status)."""
    from lhpc.core import config as cfgmod
    svc = _svc(tmp_path)
    cfgmod.save_webserver_config(svc._paths, bind="0.0.0.0", port=8443,
                                 remote_exposed=True, allowed_cidrs=["192.168.178.0/24"])
    svc._invalidate_config()
    monkeypatch.setattr(svc, "_fw_integration_state", lambda: "present")
    monkeypatch.setattr(svc, "firewall_status", lambda: dict(fw))
    monkeypatch.setattr("lhpc.core.webserver.nginx_installed", lambda system: True)
    return svc


@pytest.mark.contract
def test_gate_refusal_is_recorded_and_completed_once_firewall_verified(tmp_path, monkeypatch):
    # Live-found: the refusal was a one-off flash; after the operator's firewall step nothing
    # completed the apply and the Firewall panel said nothing about it.
    from lhpc.core.service_base import ActionResult
    fw = {"config_ok": True, "live_ok": False}
    svc = _svc_gate_pending(tmp_path, monkeypatch, fw)
    assert not svc.webserver_apply_pending()
    r = svc.webserver_apply()
    assert not r.ok and (r.data or {}).get("firewall_gate") == "pending"
    assert svc.webserver_apply_pending()                              # recorded by the refusal
    assert svc.firewall_settings_view()["webserver_apply_pending"]    # surfaced where they look
    # Firewall still unverified: the watchdog does nothing, the marker stays.
    assert svc.webserver_apply_complete_pending() is None
    assert svc.webserver_apply_pending()
    # Firewall verified: the watchdog runs the apply the operator already confirmed — the real
    # webserver_apply, with only the post-gate activation stubbed.
    fw["live_ok"] = True
    calls = []
    monkeypatch.setattr(svc, "_webserver_apply_after_gate",
                        lambda cfg: (calls.append(1), ActionResult(True, "applied"))[1])
    assert svc.webserver_apply_complete_pending().ok
    assert calls == [1] and not svc.webserver_apply_pending()         # cleared on gate pass
    assert not svc.firewall_settings_view()["webserver_apply_pending"]
    assert svc.webserver_apply_complete_pending() is None             # nothing owed: no apply
    assert calls == [1]


def test_gate_pending_marker_cleared_once_the_gate_lets_an_apply_run(tmp_path, monkeypatch):
    # The marker records the DEFERRAL only. An apply the gate lets through discharges it even
    # when that apply then fails (operator's click or the watchdog alike): the failure is shown
    # in the Webserver panel, and the watchdog must not re-run it unasked.
    from lhpc.core.service_base import ActionResult
    fw = {"config_ok": True, "live_ok": False}
    svc = _svc_gate_pending(tmp_path, monkeypatch, fw)
    assert not svc.webserver_apply().ok and svc.webserver_apply_pending()
    fw["live_ok"] = True
    monkeypatch.setattr(svc, "_webserver_apply_after_gate",
                        lambda cfg: ActionResult(False, "nginx reload failed"))
    assert not svc.webserver_apply().ok                               # operator's own apply
    assert not svc.webserver_apply_pending()
    assert svc.webserver_apply_complete_pending() is None             # no background retry


@pytest.mark.contract
@pytest.mark.safety("firewall-fail-closed")
def test_gate_pending_completion_never_applies_a_changed_policy(tmp_path, monkeypatch):
    # Audit P1: a save-only edit after the refusal (here the console's auth policy, which the
    # firewall intent does not see) must not be activated by the deferred Apply. The marker is
    # discharged and the new config waits for its own explicit Apply.
    from lhpc.core.service_base import ActionResult
    fw = {"config_ok": True, "live_ok": False}
    svc = _svc_gate_pending(tmp_path, monkeypatch, fw)
    assert not svc.webserver_apply().ok and svc.webserver_apply_pending()
    assert svc.webserver_configure(access_mode="no-auth").ok          # save-only path
    svc._invalidate_config()
    fw["live_ok"] = True
    calls = []
    monkeypatch.setattr(svc, "_webserver_apply_after_gate",
                        lambda cfg: (calls.append(1), ActionResult(True, "applied"))[1])
    assert svc.webserver_apply_complete_pending() is None
    assert calls == [] and not svc.webserver_apply_pending()
    assert svc.webserver_apply_complete_pending() is None             # stays discharged


def test_prospective_ports_exclude_loopback_backends(tmp_path):
    from lhpc.core import config as cfgmod
    svc = _svc(tmp_path)
    cfgmod.save_webserver_config(svc._paths, bind="0.0.0.0", port=8443, remote_exposed=True)
    cfgmod.save_stackweb_config(svc._paths, "meshcom", mode="local", port=8444)   # loopback
    svc._invalidate_config()
    ports = svc._prospective_nginx_ports()
    assert 8443 in ports and 8444 not in ports             # local proxy is not exposed


# --- FW-R8: stack-start exposure gate --------------------------------------------------------

def _expose_kiss(svc):
    """Configure the kiss TNC to bind a non-loopback listener (0.0.0.0:9001) via saved config."""
    from lhpc.core import config as _cfg
    band = svc._config_band("kiss", "")
    _cfg.save_stack_config(svc._paths, "kiss",
                           {"kiss_host": "0.0.0.0", "kiss_port": "9001"}, band)
    svc._invalidate_config()


def test_stack_start_gate_noop_when_firewall_absent(tmp_path, monkeypatch):
    svc = _svc(tmp_path)
    _expose_kiss(svc)
    monkeypatch.setattr(svc, "_fw_integration_state", lambda: "absent")
    allowed, msg, cmds = svc.firewall_gate_stack_start("kiss")
    assert allowed and msg == "" and cmds == []


def test_stack_start_gate_refuses_exposed_listener_when_unverified(tmp_path, monkeypatch):
    # The core hole: a non-loopback stack listener must not come up without a live-verified
    # firewall protecting it.
    svc = _svc(tmp_path)
    _expose_kiss(svc)
    monkeypatch.setattr(svc, "_fw_integration_state", lambda: "present")
    monkeypatch.setattr(svc, "firewall_status",
                        lambda: {"config_ok": True, "live_ok": False})
    allowed, msg, _cmds = svc.firewall_gate_stack_start("kiss")
    assert not allowed and "Firewall changes pending" in msg and "start 'kiss' again" in msg


def test_stack_start_gate_allows_exposed_listener_when_verified(tmp_path, monkeypatch):
    svc = _svc(tmp_path)
    _expose_kiss(svc)
    monkeypatch.setattr(svc, "_fw_integration_state", lambda: "present")
    monkeypatch.setattr(svc, "firewall_status",
                        lambda: {"config_ok": True, "live_ok": True})
    assert svc.firewall_gate_stack_start("kiss")[0]


def test_stack_start_gate_ignores_loopback_only_stack(tmp_path, monkeypatch):
    # Default kiss bind is loopback -> nothing exposed -> allowed even with an unverified firewall.
    svc = _svc(tmp_path)
    monkeypatch.setattr(svc, "_fw_integration_state", lambda: "present")
    monkeypatch.setattr(svc, "firewall_status",
                        lambda: {"config_ok": False, "live_ok": False})
    assert svc.firewall_gate_stack_start("kiss")[0]        # loopback backend -> no gate


def test_stack_start_gate_honours_ephemeral_bind_override(tmp_path, monkeypatch):
    # Saved config keeps kiss on loopback, but an ephemeral Start-confirm --kiss-host 0.0.0.0
    # would expose it this launch -> the gate must see it from the launch plan and refuse.
    svc = _svc(tmp_path)
    monkeypatch.setattr(svc, "_fw_integration_state", lambda: "present")
    monkeypatch.setattr(svc, "firewall_status",
                        lambda: {"config_ok": True, "live_ok": False})
    assert svc.firewall_gate_stack_start("kiss")[0]        # saved config = loopback -> allowed
    allowed, msg, _cmds = svc.firewall_gate_stack_start(
        "kiss", params={"kiss_host": "0.0.0.0"})
    assert not allowed and "Firewall changes pending" in msg


def test_stack_start_gate_fails_closed_on_unmapped_nonloopback_listener(tmp_path, monkeypatch):
    # A tcp listener with NO firewall metadata (future addition) cannot have its scope derived;
    # a non-loopback one must be treated as exposed and gated, never silently started.
    import types
    svc = _svc(tmp_path)
    comp = types.SimpleNamespace(id="future-comp")
    stk = types.SimpleNamespace(id="future")
    ep = types.SimpleNamespace(address="0.0.0.0:7788", kind="tcp", role="listener", firewall=None)
    monkeypatch.setattr(svc, "_run_order", lambda t: [(stk, comp)])
    monkeypatch.setattr(svc, "_fw_listener_endpoints", lambda: ([], [(stk, comp, ep)]))
    monkeypatch.setattr(svc, "_fw_integration_state", lambda: "present")
    monkeypatch.setattr(svc, "firewall_status",
                        lambda: {"config_ok": True, "live_ok": False})
    allowed, msg, _cmds = svc.firewall_gate_stack_start("future")
    assert not allowed and "Firewall changes pending" in msg
    # A loopback-only unmapped listener is not exposed -> not gated.
    ep_lo = types.SimpleNamespace(address="127.0.0.1:7788", kind="tcp", role="listener", firewall=None)
    monkeypatch.setattr(svc, "_fw_listener_endpoints", lambda: ([], [(stk, comp, ep_lo)]))
    assert svc.firewall_gate_stack_start("future")[0]


def test_stack_start_gate_partial_install_fails_closed(tmp_path, monkeypatch):
    svc = _svc(tmp_path)
    _expose_kiss(svc)
    monkeypatch.setattr(svc, "_fw_integration_state", lambda: "partial")
    allowed, msg, _cmds = svc.firewall_gate_stack_start("kiss")
    assert not allowed and "partially installed" in msg


def test_stack_start_gate_verified_refuses_ephemeral_port_move(tmp_path, monkeypatch):
    # P1-1: even with a live-verified firewall, an EPHEMERAL scope change (here a port move) that
    # is not represented in the applied model must be refused — the receipt vouches only for the
    # saved candidate, never for an ad-hoc launch scope.
    svc = _svc(tmp_path)
    _expose_kiss(svc)                                      # saved 0.0.0.0:9001, modeled + verified
    monkeypatch.setattr(svc, "_fw_integration_state", lambda: "present")
    monkeypatch.setattr(svc, "firewall_status",
                        lambda: {"config_ok": True, "live_ok": True,
                                 "candidate": svc.firewall_candidate()})
    assert svc.firewall_gate_stack_start("kiss")[0]        # exact saved scope -> allowed
    allowed, msg, _ = svc.firewall_gate_stack_start("kiss", params={"kiss_port": "9002"})
    assert not allowed
    assert msg == "Save the setting permanently, apply the firewall, then start."


def test_fw_scope_modeled_matches_full_scope(tmp_path):
    # P1-1 unit: exact match on proto/family/addr/port/band with CIDRs covered; a port move,
    # CIDR widening, wrong band, or unmapped scope is NOT represented; a modeled DROP covers any.
    svc = _svc(tmp_path)
    allow = {"id": "x", "proto": "tcp", "family": "ipv4", "addr": "0.0.0.0", "port": 9001,
             "allow_cidrs": ["192.168.0.0/24"], "selected": True, "deny_default": False,
             "auth": "none", "band": ""}
    cand = {"endpoints": [allow]}

    def sc(**o):
        base = {"id": "x", "proto": "tcp", "family": "ipv4", "addr": "0.0.0.0", "port": 9001,
                "allow_cidrs": ["192.168.0.5/32"], "band": "433"}
        base.update(o)
        return base

    assert svc._fw_scope_modeled(sc(), cand)                          # covered by the allow
    assert not svc._fw_scope_modeled(sc(allow_cidrs=["10.0.0.0/8"]), cand)   # CIDR widening
    assert not svc._fw_scope_modeled(sc(allow_cidrs=[]), cand)        # any-source widening
    assert not svc._fw_scope_modeled(sc(port=9002), cand)             # port move
    assert not svc._fw_scope_modeled(sc(_unmapped=True), cand)        # unmapped never matches
    # band-specific modeled row only matches its own band.
    cand_b = {"endpoints": [dict(allow, band="868")]}
    assert not svc._fw_scope_modeled(sc(band="433"), cand_b)
    assert svc._fw_scope_modeled(sc(band="868"), cand_b)
    # a modeled DROP (unselected, no allow-list) covers any source scope.
    cand_drop = {"endpoints": [dict(allow, selected=False, deny_default=False, allow_cidrs=[])]}
    assert svc._fw_scope_modeled(sc(allow_cidrs=["10.0.0.0/8"]), cand_drop)


def test_candidate_represents_divergent_bands(tmp_path, monkeypatch):
    # P1-1: when a stack's per-band scopes DIVERGE, the candidate must carry a distinct row per
    # band (so compat mode drops each real port); identical-across-bands collapses to one
    # band-agnostic row (band="") so the common case is unchanged.
    import types
    svc = _svc(tmp_path)
    comp = types.SimpleNamespace(id="kc")
    stk = types.SimpleNamespace(id="kiss")
    ep = object()
    monkeypatch.setattr(svc, "_fw_listener_endpoints", lambda: ([(stk, comp, ep)], []))
    monkeypatch.setattr(svc, "stack_bands", lambda t: ("433", "868"))

    def _scope(_s, _c, _e, overrides=None, band=None):
        port = 9001 if band == "433" else 9002            # diverge per band
        return {"id": "kiss.kc.tcp-8001", "proto": "tcp", "family": "ipv4",
                "addr": "0.0.0.0", "port": port, "allow_cidrs": [], "deny": False,
                "auth": "none", "loopback": False}
    monkeypatch.setattr(svc, "_fw_resolve_scope", _scope)
    eps = svc.firewall_candidate()["endpoints"]
    bands = sorted(e["band"] for e in eps)
    assert bands == ["433", "868"] and {e["port"] for e in eps} == {9001, 9002}
    # identical-across-bands -> single band-agnostic row.
    monkeypatch.setattr(svc, "_fw_resolve_scope",
                        lambda *a, band=None, **k: {"id": "kiss.kc.tcp-8001", "proto": "tcp",
                        "family": "ipv4", "addr": "0.0.0.0", "port": 9001, "allow_cidrs": [],
                        "deny": False, "auth": "none", "loopback": False})
    eps2 = svc.firewall_candidate()["endpoints"]
    assert len(eps2) == 1 and eps2[0]["band"] == ""


# --- FW-R10: fail-closed configure (validate-before-save rollback + render surfacing) --------

def test_configure_rolls_back_when_candidate_invalid(tmp_path, monkeypatch):
    svc = _svc(tmp_path)
    assert svc.firewall_configure(mode="compatibility").ok
    assert svc.config().firewall.mode == "compatibility"
    from lhpc.core import firewall as fwm
    monkeypatch.setattr(fwm, "validate_candidate", lambda cand: ["boom"])
    r = svc.firewall_configure(mode="secure-default")
    assert not r.ok and "not saved" in r.summary
    # ROLLED BACK: the prior valid mode is still persisted, not the rejected one.
    assert svc.config().firewall.mode == "compatibility"


def test_configure_surfaces_render_failure(tmp_path, monkeypatch):
    svc = _svc(tmp_path)
    def _boom(candidate=None):
        raise OSError("disk full")
    monkeypatch.setattr(svc, "firewall_scripts", _boom)
    r = svc.firewall_configure(mode="secure-default")
    assert not r.ok and "could not render" in r.summary
    # SCRIPTS FIRST, CONFIG LAST: a script-write failure leaves nothing persisted (not saved).
    assert "not saved" in r.summary


def test_configure_validate_before_write_leaves_config_and_scripts_intact(tmp_path, monkeypatch):
    # P2-2: an invalid candidate is caught BEFORE any persistent mutation — the previous config
    # and previously-rendered scripts are untouched (nothing half-written).
    svc = _svc(tmp_path)
    assert svc.firewall_configure(mode="compatibility").ok
    apply_path = svc._fw_script_paths()["firewall-apply.sh"]
    before = open(apply_path).read()
    from lhpc.core import firewall as fwm
    monkeypatch.setattr(fwm, "validate_candidate", lambda cand: ["boom"])
    r = svc.firewall_configure(mode="secure-default")
    assert not r.ok and "not saved" in r.summary
    assert svc.config().firewall.mode == "compatibility"      # config unchanged
    assert open(apply_path).read() == before                  # scripts unchanged


def test_fw_update_preflight_gates_nginx_unit(tmp_path, monkeypatch):
    # P1-2C: before self-update advances, abort when the firewall is installed, remote web is
    # exposed, and the boot-gated nginx unit cannot be brought current this run.
    from lhpc.core import updater_units as uu
    svc = _svc(tmp_path)
    monkeypatch.setattr(svc, "_fw_integration_state", lambda: "present")
    monkeypatch.setattr(svc, "_fw_remote_web_exposed", lambda: True)

    def _integ(nginx, managed):
        return {"per_unit": {uu.NGINX_UNIT: nginx}, "managed": managed}

    # foreign unit -> abort (cannot safely replace)
    monkeypatch.setattr(svc, "updater_integration", lambda: _integ(uu.FOREIGN, False))
    r = svc.firewall_update_nginx_preflight()
    assert r is not None and not r.ok and "repair-integration" in r.summary
    # owned-stale but MANAGED (bus-blocked, cannot daemon-reload) -> abort
    monkeypatch.setattr(svc, "updater_integration", lambda: _integ(uu.MODIFIED_OURS, True))
    assert svc.firewall_update_nginx_preflight() is not None
    # unit already OK -> proceed
    monkeypatch.setattr(svc, "updater_integration", lambda: _integ(uu.OK, True))
    assert svc.firewall_update_nginx_preflight() is None
    # owned-stale + INTERACTIVE (has bus) -> proceed, migrate post-advance
    monkeypatch.setattr(svc, "updater_integration", lambda: _integ(uu.MISSING, False))
    assert svc.firewall_update_nginx_preflight() is None
    # absent firewall or loopback-only -> no gating
    monkeypatch.setattr(svc, "_fw_integration_state", lambda: "absent")
    assert svc.firewall_update_nginx_preflight() is None


def test_fw_post_update_reconcile_defers_to_fresh_process(tmp_path, monkeypatch):
    # AR2-P1b: a self-update must NOT regenerate firewall artifacts in the old (pre-update) process
    # — it marks, and the freshly-restarted new-code process reconciles on startup, then clears it.
    import os as _os
    from lhpc.core import updater_units as uu
    svc = _svc(tmp_path)
    _os.makedirs(svc._paths.under("state"), exist_ok=True)
    marker = "state/firewall-postupdate.pending"
    # no marker -> no-op
    assert svc.firewall_post_update_reconcile() == []
    # the OLD process only MARKS (never renders artifacts)
    svc._fw_mark_post_update()
    assert svc._marker_present(marker)
    # the FRESH process reconciles: render scripts + (here) unit already OK -> clears the marker
    ran = {"render": 0}
    monkeypatch.setattr(svc, "_fw_integration_state", lambda: "present")
    monkeypatch.setattr(svc, "firewall_render", lambda: ran.__setitem__("render", 1))
    monkeypatch.setattr(svc, "updater_integration",
                        lambda: {"per_unit": {uu.NGINX_UNIT: uu.OK}, "managed": True})
    svc.firewall_post_update_reconcile()
    assert ran["render"] == 1
    assert not svc._marker_present(marker)                 # cleared after reconcile


def test_fw_update_after_advance_migrates_owned_unit(tmp_path, monkeypatch):
    # P1-2 B/C: a real advance regenerates the firewall scripts and brings the LHPC-owned nginx
    # unit current on disk — interactive path daemon-reloads; managed path reports reload/reboot.
    from lhpc.core import updater_units as uu
    svc = _svc(tmp_path)
    monkeypatch.setattr(svc, "_fw_integration_state", lambda: "present")
    rendered = {"n": 0}
    monkeypatch.setattr(svc, "firewall_render", lambda: rendered.__setitem__("n", rendered["n"] + 1))
    written = {"n": 0}
    monkeypatch.setattr("lhpc.core.updater_units.write_set",
                        lambda ud, root: (written.__setitem__("n", written["n"] + 1), [])[1])
    reloads = {"n": 0}
    monkeypatch.setattr(svc._system.runner, "run",
                        lambda argv, timeout=None: (reloads.__setitem__("n", reloads["n"] + 1),
                                                    type("R", (), {"returncode": 0, "stdout": ""})())[1])
    # interactive owned-stale -> write + daemon-reload + note
    monkeypatch.setattr(svc, "updater_integration",
                        lambda: {"per_unit": {uu.NGINX_UNIT: uu.MODIFIED_OURS}, "managed": False})
    notes = svc.firewall_update_after_advance()
    assert rendered["n"] == 1 and written["n"] == 1 and reloads["n"] == 1
    assert any("reloaded systemd" in n for n in notes)
    # managed owned-stale -> write, NO daemon-reload, reload/reboot note
    written["n"] = reloads["n"] = 0
    monkeypatch.setattr(svc, "updater_integration",
                        lambda: {"per_unit": {uu.NGINX_UNIT: uu.MISSING}, "managed": True})
    notes = svc.firewall_update_after_advance()
    assert written["n"] == 1 and reloads["n"] == 0
    assert any("reboot is required" in n for n in notes)


def test_configure_runs_under_the_single_config_lock(tmp_path, monkeypatch):
    # P2-2/AR2: the whole validate+scripts+save operation must run under ONE config lock, so a
    # concurrent holder makes configure fail-fast "busy" (never write scripts beside another
    # request's config). Proven by making config_lock raise busy.
    import contextlib
    from lhpc.core import config as cfgmod
    svc = _svc(tmp_path)

    @contextlib.contextmanager
    def _busy(paths, timeout=cfgmod.CONFIG_LOCK_TIMEOUT_S):
        raise cfgmod.ConfigLockBusy("config is busy — a long-running operation holds it")
        yield                                             # pragma: no cover — unreachable

    monkeypatch.setattr(cfgmod, "config_lock", _busy)
    r = svc.firewall_configure(mode="secure-default")
    assert not r.ok and "busy" in r.summary.lower()
    # no scripts were written while "busy" (nothing persisted)
    import os as _os
    base = svc._paths.under("config/files/firewall")
    assert not _os.path.exists(base) or "firewall-apply.sh" not in _os.listdir(base)


def test_configure_writes_scripts_atomically(tmp_path):
    # P2-2: scripts are written via a same-directory temp file + atomic rename (never truncated in
    # place), so no `.sh` is left half-written and no stray temp remains after success.
    import os as _os
    svc = _svc(tmp_path)
    assert svc.firewall_configure(mode="secure-default").ok
    base = svc._paths.under("config/files/firewall")
    names = _os.listdir(base)
    assert "firewall-apply.sh" in names
    assert not any(n.startswith(".tmp-") for n in names)      # no leftover temp files
    for n in ("firewall-apply.sh", "firewall-reset.sh", "firewall-cleanup.sh"):
        assert _os.access(_os.path.join(base, n), _os.X_OK)   # executable


def test_recommended_preset_clears_advanced_exceptions(tmp_path):
    # P2-4: "Use recommended settings" must clear the advanced escape hatches (manual SSH ports
    # and extra_allow), never silently retain arbitrary inbound allowances.
    from lhpc.core import config as _cfg
    svc = _svc(tmp_path)
    _cfg.save_firewall_config(svc._paths, mode="compatibility", ssh_ports=[2222],
                              extra_allow=[{"proto": "tcp", "family": "ipv4", "addr": "0.0.0.0",
                                            "port": 1234, "cidr": "10.0.0.0/8"}])
    svc._invalidate_config()
    assert svc.config().firewall.ssh_ports and svc.config().firewall.extra_allow
    assert svc.firewall_configure(recommended=True).ok
    fw = svc.config().firewall
    assert fw.mode == "secure-default" and fw.ssh_ports == () and fw.extra_allow == ()


def test_ap_enabled_strict_boolean_parse(tmp_path):
    # P2-4: a hand-edited scalar like ap_enabled = "false" is truthy under bool() and must NOT
    # enable the AP rules — strict isinstance(bool) with a diagnostic, stays disabled.
    from lhpc.core.config import _parse_firewall
    diags = []
    cfg = _parse_firewall({"firewall": {"ap_enabled": "false", "ap_interface": "wlan0",
                                        "ap_cidr": "10.0.0.0/24"}}, diags)
    assert cfg.ap_enabled is False
    assert any("ap_enabled" in d for d in diags)
    # a real bool True with interface+cidr enables it.
    cfg2 = _parse_firewall({"firewall": {"ap_enabled": True, "ap_interface": "wlan0",
                                         "ap_cidr": "10.0.0.0/24"}}, [])
    assert cfg2.ap_enabled is True


def test_boot_gate_falls_back_to_loopback_when_unverified(tmp_path, monkeypatch):
    from lhpc.core import config as cfgmod
    svc = _svc(tmp_path)
    cfgmod.save_webserver_config(svc._paths, bind="0.0.0.0", port=8443, remote_exposed=True)
    svc._invalidate_config()
    monkeypatch.setattr(svc, "_fw_integration_state", lambda: "present")
    monkeypatch.setattr(svc, "firewall_status",
                        lambda: {"config_ok": False, "live_ok": False})
    from lhpc.core import firewall as fwm
    monkeypatch.setattr(fwm, "BOOT_GATE_WAIT_S", 0)
    promoted = {}
    monkeypatch.setattr("lhpc.core.webserver.stage_and_validate",
                        lambda system, paths, cfg, proxies: (
                            promoted.__setitem__("bind", cfg.bind) or (True, "", "staged")))
    monkeypatch.setattr("lhpc.core.webserver.promote_config",
                        lambda paths: promoted.__setitem__("promoted", True))
    r = svc.firewall_boot_gate()
    assert r.ok and "LOOPBACK-ONLY" in r.summary
    assert promoted["bind"] == "127.0.0.1" and promoted["promoted"] is True


def test_boot_gate_refusal_names_the_failing_dimension(tmp_path, monkeypatch):
    """One message for every cause (no receipt, stale receipt, changed intent, half-installed
    integration) is what made this unreadable live: the operator's own shell said verified while
    the gate said the opposite, with nothing naming the difference."""
    from lhpc.core import config as cfgmod
    from lhpc.core import firewall as fwm
    svc = _svc(tmp_path)
    cfgmod.save_webserver_config(svc._paths, bind="0.0.0.0", port=8443, remote_exposed=True)
    svc._invalidate_config()
    monkeypatch.setattr(fwm, "BOOT_GATE_WAIT_S", 0)
    monkeypatch.setattr("lhpc.core.webserver.stage_and_validate", lambda *a, **k: (True, "", "s"))
    monkeypatch.setattr("lhpc.core.webserver.promote_config", lambda paths: None)

    monkeypatch.setattr(svc, "_fw_integration_state", lambda: "present")
    monkeypatch.setattr(svc, "firewall_status",
                        lambda: {"config_ok": True, "live_ok": False, "reason": "no-receipt"})
    assert "live rules unverified (no-receipt)" in svc.firewall_boot_gate().summary

    monkeypatch.setattr(svc, "firewall_status",
                        lambda: {"config_ok": False, "live_ok": True, "reason": ""})
    assert "saved intent differs" in svc.firewall_boot_gate().summary

    monkeypatch.setattr(svc, "_fw_integration_state", lambda: "partial")
    assert "integration partial" in svc.firewall_boot_gate().summary


def test_boot_gate_allows_when_verified(tmp_path, monkeypatch):
    from lhpc.core import config as cfgmod
    svc = _svc(tmp_path)
    cfgmod.save_webserver_config(svc._paths, bind="0.0.0.0", port=8443, remote_exposed=True)
    svc._invalidate_config()
    monkeypatch.setattr(svc, "_fw_integration_state", lambda: "present")
    monkeypatch.setattr(svc, "firewall_status",
                        lambda: {"config_ok": True, "live_ok": True})
    called = {"staged": False}
    monkeypatch.setattr("lhpc.core.webserver.stage_and_validate",
                        lambda *a, **k: called.__setitem__("staged", True) or (True, "", ""))
    r = svc.firewall_boot_gate()
    assert r.ok and "allowed" in r.summary and called["staged"] is False   # no rewrite


def test_boot_gate_noop_without_integration(tmp_path):
    svc = _svc(tmp_path)
    assert svc.firewall_boot_gate().ok


# --- FW-8: uninstall gate + stance-string drift ----------------------------------------------

def test_uninstall_refuses_while_firewall_integration_installed():
    import pathlib
    src = pathlib.Path("uninstall.sh").read_text()
    # SECURITY REGRESSION: the firewall roots must be HARD-CODED with NO caller-controlled override.
    # A variable-driven root (even one named LHPC_TEST_*) lets a production caller point the
    # preflight at an empty dir and orphan a live, boot-persistent firewall. The shipped script must
    # therefore contain no such override at all; the hermetic test seam lives only in the tests
    # (they run a copy with the canonical roots substituted).
    assert "LHPC_TEST_FW" not in src
    assert 'FW_ETC="/etc/lhpc"' in src
    assert 'FW_SYSD="/etc/systemd/system"' in src
    assert 'FW_HELPER="${FW_ETC}/firewall-helper"' in src
    assert "managed firewall integration is still installed" in src
    assert "firewall-reset.sh" in src
    # ownership-metadata-missing also blocks (never orphan a boot-persistent firewall)
    assert "ownership metadata" in src and "refusing to orphan" in src


def test_stance_strings_updated_to_managed_firewall():
    import pathlib
    # Old absolute "cannot close it" wording is gone from code + templates.
    for path in ("lhpc/core/service_webserver.py",
                 "lhpc/adapters/web/templates/_stackweb.html"):
        assert "LHPC cannot close" not in pathlib.Path(path).read_text()
    fw = pathlib.Path("docs/firewall.md").read_text()
    assert "uses native\nnftables" in fw or "uses native nftables" in fw
    assert "table inet lhpc" in fw and "never edits your firewall" in fw
    # README advertises the managed firewall (case-insensitive: it appears as "the managed firewall"
    # in the Remote-access section).
    assert "managed firewall" in pathlib.Path("README.md").read_text().lower()


def test_apply_script_renders_from_real_candidate(tmp_path):
    # End-to-end: derive candidate -> render apply script -> valid bash embedding the helper.
    import subprocess
    svc = _svc(tmp_path)
    scripts = svc.firewall_scripts()
    for name in ("firewall-apply.sh", "firewall-reset.sh", "firewall-cleanup.sh"):
        p = scripts[name]
        assert subprocess.run(["bash", "-n", p]).returncode == 0


# --- FW rev-8 matrix completeness ------------------------------------------------------------

def test_dhcp_directions_are_distinct_client_vs_ap_server():
    from lhpc.core import firewall_helper as fh
    # AP enabled: baseline CLIENT reply 67->68 AND AP-server DISCOVER 68->67 both present,
    # and they are different rules (never conflated).
    cand = _candidate(ap={"enabled": True, "interface": "wlan0", "cidr": "10.42.0.0/24"})
    m = fh.resolve_model(cand, ownership_id="x",
                         ssh_scopes=[{"proto": "tcp", "family": "dual", "addr": "*",
                                      "port": 22}])
    texts = [fh._rule_spec(r)[0] for r in m["rules"]]
    assert "meta nfproto ipv4 udp sport 67 udp dport 68 accept" in texts   # client reply (v4)
    assert any("sport 68 udp dport 67" in t for t in texts)       # AP DISCOVER (server)
    # AP DISCOVER is interface-scoped, NEVER source-CIDR-scoped.
    disc = next(t for t in texts if "sport 68 udp dport 67" in t)
    assert 'iifname "wlan0"' in disc and "saddr" not in disc


def test_endpoint_family_scoping_v4_v6_dual(tmp_path):
    from lhpc.core import firewall_helper as fh
    eps = [
        _ep(id="a.tcp-1000", port=1000, family="ipv4", addr="0.0.0.0",
            deny_default=False, selected=True, allow_cidrs=[]),
        _ep(id="b.tcp-1001", port=1001, family="ipv6", addr="::",
            deny_default=False, selected=True, allow_cidrs=[]),
        _ep(id="c.tcp-1002", port=1002, family="dual", addr="*",
            deny_default=False, selected=True, allow_cidrs=[]),
    ]
    cand = _candidate(endpoints=eps)
    m = fh.resolve_model(cand, ownership_id="x",
                         ssh_scopes=[{"proto": "tcp", "family": "dual", "addr": "*", "port": 22}])
    texts = [fh._rule_spec(r)[0] for r in m["rules"]]
    assert "meta nfproto ipv4 tcp dport 1000 accept" in texts     # v4-only
    assert "meta nfproto ipv6 tcp dport 1001 accept" in texts     # v6-only, never v4
    assert "tcp dport 1002 accept" in texts                       # dual: no family atom
    assert "meta nfproto ipv4 tcp dport 1001 accept" not in texts


def test_freshness_immune_to_wallclock_jump(tmp_path, monkeypatch):
    import time as _t
    svc = _svc(tmp_path)
    monkeypatch.setattr(svc, "_fw_boot_id", lambda: "b")
    now_bt = _t.clock_gettime(_t.CLOCK_BOOTTIME)
    # A receipt whose WALL clock is wildly wrong but whose BOOTTIME is current is still fresh.
    assert svc._fw_receipt_fresh({"boot_id": "b", "boottime": now_bt, "walltime": 0})
    # An OLD boottime is stale regardless of wall clock.
    assert not svc._fw_receipt_fresh({"boot_id": "b", "boottime": now_bt - 99999,
                                      "walltime": 9e9})


def test_reset_interruption_recovers_forward(tmp_path):
    import json as _json
    from lhpc.core import firewall_helper as fh
    etc, run = str(tmp_path / "etc"), str(tmp_path / "run")
    _seed_meta(etc)
    cand = _candidate()
    sysx = _FakeSys()
    sysx.listing = _expected_live_json(cand)
    p = tmp_path / "c.json"
    p.write_text(_json.dumps(cand))
    assert fh.op_apply(sysx, str(p), etc_dir=etc, run_dir=run) == fh.EXIT_OK
    # Simulate an interrupted reset: journal left at reset/begin, table still ours.
    fh.atomic_write(f"{etc}/firewall.journal.json",
                    _json.dumps({"op": "reset", "phase": "begin",
                                 "meta": {"ownership_id": "fixedid01"}}), 0o600)
    # The next helper invocation (a check) must finish the reset forward, not proceed as normal.
    fh.op_check(sysx, etc_dir=etc, run_dir=run)
    assert not (tmp_path / "etc" / "firewall.snapshot.json").exists()   # reset completed
    assert not (tmp_path / "etc" / "firewall.journal.json").exists()


# --- FW logs + anchor ------------------------------------------------------------------------

def test_helper_appends_to_firewall_log(tmp_path):
    from lhpc.core import firewall_helper as fh
    run = str(tmp_path / "run")
    fh.write_receipt(_FakeSys(), "verified", "", "IH", "MH", ["ip:custom"], run_dir=run)
    log = (tmp_path / "run" / "firewall.log").read_text()
    assert "verdict=verified" in log and "foreign=ip:custom" in log
    # bounded to ~200 lines
    for i in range(250):
        fh.append_log(run, f"line {i}")
    lines = (tmp_path / "run" / "firewall.log").read_text().splitlines()
    assert len(lines) <= 200 and lines[-1] == "line 249"


def test_firewall_log_tail_reader(tmp_path, monkeypatch):
    from lhpc.core import firewall as fwm
    svc = _svc(tmp_path)
    logdir = tmp_path / "run"
    logdir.mkdir()
    (logdir / "firewall.log").write_text("bt=100 verdict=verified\nbt=160 verdict=mismatch\n")
    monkeypatch.setattr(fwm, "RECEIPT_PATH", str(logdir / "check.json"))
    path, lines = svc.firewall_log_tail()
    assert lines == ["bt=100 verdict=verified", "bt=160 verdict=mismatch"]
    assert svc.firewall_has_log() is True


def test_firewall_row_anchor_is_on_the_details_element(tmp_path):
    # The anchor must land on the <details> itself (so the hash-open logic opens the tab),
    # never on a wrapping <div> (closest('details') would then miss it).
    body = _svc_client(tmp_path).get("/stacks?open=kiss").get_data(as_text=True)
    assert 'class="advcfg" id="firewall-row"' in body
    assert 'class="ws-sub-wrap" id="firewall-row"' not in body


def _svc_client(tmp_path):
    from lhpc.adapters.web.app import create_app
    from lhpc.core.paths import Paths
    from lhpc.core.probes.backends import FakeSystem
    from lhpc.core.services import ControllerService

    def factory():
        (tmp_path / "config").mkdir(exist_ok=True)
        return ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    return create_app(service_factory=factory).test_client()


def test_dashboard_logs_link_in_header_and_fw_line(tmp_path, monkeypatch):
    from lhpc.core.services import ControllerService
    monkeypatch.setattr(ControllerService, "firewall_has_log", lambda self: True)
    body = _svc_client(tmp_path).get("/").get_data(as_text=True)
    # header logs link (right-aligned via hdrlogs) present in the wsbox summary
    assert 'class="logslink hdrlogs"' in body
    assert "/firewall/logs" in body                    # firewall line logs link


def test_reset_script_removes_known_files_then_rmdir():
    # FW-R10: no blind `rm -rf /etc/lhpc`. Remove KNOWN lhpc files (incl. the .firewall.lock
    # that once stranded a plain rmdir), then rmdir — which removes the dir ONLY if empty, so an
    # unexpected foreign file left there is preserved rather than recursively nuked.
    from lhpc.core import firewall as fwm
    reset = fwm.render_reset_script()
    assert "rm -rf /etc/lhpc" not in reset
    assert "rmdir /etc/lhpc" in reset
    for f in ("/etc/lhpc/.firewall.lock", "/etc/lhpc/firewall.meta.json",
              "/etc/lhpc/firewall.snapshot.json", fwm.CANDIDATE_DEST, fwm.HELPER_DEST):
        assert f in reset


def test_reset_script_refuses_without_a_trusted_helper():
    # P1-4: ownership + nftables verification lives ONLY in the trusted helper — never duplicated
    # in Bash. The reset requires the helper to be a regular, root-owned, executable, non-symlink
    # file; anything else REFUSES (exit 13) and tells the operator to reinstall it first. No
    # nft-in-Bash probe.
    from lhpc.core import firewall as fwm
    reset = fwm.render_reset_script()
    assert "nft list table inet lhpc" not in reset            # no ownership logic in Bash
    for tok in (f"[ -f {fwm.HELPER_DEST} ]", f"[ ! -L {fwm.HELPER_DEST} ]",
                f"[ -O {fwm.HELPER_DEST} ]", f"[ -x {fwm.HELPER_DEST} ]", "exit 13"):
        assert tok in reset


# --- FW-R1: audit remediation — false-green, over-open, crash ---------------------------------

def test_security_pill_never_green_for_allowed_unauth_port(tmp_path, monkeypatch):
    # P1-7: an operator-ALLOWED unauthenticated exposed port must never read "secure", even
    # with a live-verified firewall (the verified model ALLOWS it — it is genuinely exposed).
    from lhpc.core.services import ControllerService
    svc = _svc(tmp_path)
    cand = {"mode": "secure-default", "endpoints": [
        {"id": "meshtastic.tcp-4403", "proto": "tcp", "family": "dual", "addr": "*",
         "port": 4403, "allow_cidrs": [], "selected": True, "deny_default": True,
         "auth": "none"}]}
    monkeypatch.setattr(ControllerService, "firewall_status",
                        lambda self: {"live_ok": True, "config_ok": True, "candidate": cand})
    rows = [{"kind": "port", "port": "0.0.0.0:4403", "exposure": {"level": "bad"}}]
    pill = svc.security_pill(rows)
    assert pill["level"] == "bad" and pill["label"] == "exposed"     # NOT secure


def test_security_pill_never_green_upgrades_exposed_port_by_number(tmp_path, monkeypatch):
    # P2-3: even with a live-verified firewall that DROPS port 4403, an externally-exposed
    # listener on 4403 must NOT read as green — enforcement is scoped by proto/family/address,
    # so a same-numbered listener on a different address/family is not proven filtered. The pill
    # keeps the row's conservative exposure colour; firewall status is shown separately.
    from lhpc.core.services import ControllerService
    svc = _svc(tmp_path)
    cand = {"mode": "secure-default", "endpoints": [
        {"id": "meshtastic.tcp-4403", "proto": "tcp", "family": "dual", "addr": "*",
         "port": 4403, "allow_cidrs": [], "selected": False, "deny_default": True,
         "auth": "none", "band": ""}]}
    monkeypatch.setattr(ControllerService, "firewall_status",
                        lambda self: {"live_ok": True, "config_ok": True, "candidate": cand})
    # a same-NUMBER exposed listener (different address/family) is NOT upgraded to green.
    assert svc.security_pill([{"kind": "port", "port": "0.0.0.0:4403",
                              "exposure": {"level": "bad"}}])["level"] == "bad"
    assert svc.security_pill([{"kind": "port", "port": "0.0.0.0:8080",
                              "exposure": {"level": "bad"}}])["level"] == "bad"


def test_security_pill_not_green_when_config_stale(tmp_path, monkeypatch):
    from lhpc.core.services import ControllerService
    svc = _svc(tmp_path)
    cand = {"mode": "secure-default", "endpoints": [
        {"id": "meshtastic.tcp-4403", "proto": "tcp", "family": "dual", "addr": "*",
         "port": 4403, "allow_cidrs": [], "selected": False, "deny_default": True,
         "auth": "none"}]}
    # live but config drifted -> the applied model may not match -> NOT filtered
    monkeypatch.setattr(ControllerService, "firewall_status",
                        lambda self: {"live_ok": True, "config_ok": False, "candidate": cand})
    assert svc.security_pill([{"kind": "port", "port": "0.0.0.0:4403",
                              "exposure": {"level": "bad"}}])["level"] == "bad"


def test_endpoint_id_stable_across_port_change(tmp_path):
    # P1-2: the checkbox id must NOT contain the configured port (a port change loses it).
    svc = _svc(tmp_path)
    ids = {e["id"] for e in svc.firewall_candidate()["endpoints"]}
    assert "meshtastic.tcp-4403" in ids                             # static port in id
    # kiss id uses its STATIC 8001, not any configured override. Expose kiss (bind 0.0.0.0)
    # and change its port; the checkbox id must stay stable while the resolved port tracks.
    from lhpc.core import config as _cfg
    band = svc._config_band("kiss", "")
    _cfg.save_stack_config(svc._paths, "kiss",
                           {"kiss_host": "0.0.0.0", "kiss_port": "9001"}, band)
    svc._invalidate_config()
    kiss_ids = [e for e in svc.firewall_candidate()["endpoints"]
                if e["id"].startswith("loraham-kiss-tnc")]
    assert kiss_ids and kiss_ids[0]["id"] == "loraham-kiss-tnc.tcp-8001"  # stable
    assert kiss_ids[0]["port"] == 9001                              # resolved port still tracked


def test_proxy_ingress_family_follows_bind_not_dual(tmp_path):
    from lhpc.core import config as cfgmod
    svc = _svc(tmp_path)
    cfgmod.save_webserver_config(svc._paths, bind="0.0.0.0", port=8443, remote_exposed=True,
                                 allowed_cidrs=["0.0.0.0/0"])
    svc._invalidate_config()
    ing = [e for e in svc.firewall_candidate()["proxy_ingress"] if e["port"] == 8443]
    assert ing and ing[0]["family"] == "ipv4"                       # IPv4 bind -> not dual


# --- FW-R2: SSH union + boot-gate fail-closed ------------------------------------------------

def test_ssh_override_still_unions_active_listeners():
    # P1-4: an override must NOT drop the live recovery connection — active sshd ports are
    # ALWAYS unioned in even when [firewall] ssh_ports is set.
    from lhpc.core import firewall_helper as fh

    class _S(_FakeSys):
        def __init__(self):
            super().__init__()
            self.ss_out = 'LISTEN 0 128 0.0.0.0:2222 users:(("sshd",pid=1,fd=6))'
    scopes, confident = fh.resolve_ssh_scopes(_S(), [22])   # override says 22
    ports = {s["port"] for s in scopes}
    assert 22 in ports and 2222 in ports and confident      # active 2222 preserved


def test_ssh_precise_listenaddress_not_widened():
    # P1-4: a precise ListenAddress scope must not also emit a dual-wildcard for that port.
    from lhpc.core import firewall_helper as fh

    class _S(_FakeSys):
        def __init__(self):
            super().__init__()
            self.sshd_t = "port 2200\nlistenaddress 192.168.1.5:2200\n"
    scopes, _ = fh.resolve_ssh_scopes(_S(), [])
    p2200 = [s for s in scopes if s["port"] == 2200]
    assert any(s["addr"] == "192.168.1.5" for s in p2200)   # precise present
    assert not any(s["addr"] == "*" for s in p2200)         # wildcard NOT added


def test_boot_gate_fails_closed_when_fallback_cannot_stage(tmp_path, monkeypatch):
    # P1-1: if the loopback fallback can't be staged, refuse to start nginx (ok=False → CLI
    # exit 1) rather than bind the promoted remote config.
    from lhpc.core import config as cfgmod, firewall as fwm
    svc = _svc(tmp_path)
    cfgmod.save_webserver_config(svc._paths, bind="0.0.0.0", port=8443, remote_exposed=True)
    svc._invalidate_config()
    monkeypatch.setattr(svc, "_fw_integration_state", lambda: "present")
    monkeypatch.setattr(svc, "firewall_status",
                        lambda: {"config_ok": False, "live_ok": False})
    monkeypatch.setattr(fwm, "BOOT_GATE_WAIT_S", 0)
    monkeypatch.setattr("lhpc.core.webserver.stage_and_validate",
                        lambda *a, **k: (False, "nginx -t failed", ""))
    r = svc.firewall_boot_gate()
    assert not r.ok and "refusing to start nginx" in r.summary


def test_boot_gate_partial_install_goes_loopback_only(tmp_path, monkeypatch):
    # P1-3: a PARTIAL integration (a required artifact missing, or a pending-recovery journal)
    # must NOT be treated like absent — the boot gate goes straight to loopback-only rather than
    # letting nginx bind the remote listener ungated. No bounded wait (it could never go green).
    from lhpc.core import config as cfgmod
    svc = _svc(tmp_path)
    cfgmod.save_webserver_config(svc._paths, bind="0.0.0.0", port=8443, remote_exposed=True)
    svc._invalidate_config()
    monkeypatch.setattr(svc, "_fw_integration_state", lambda: "partial")
    # A live_ok status would wrongly allow — assert partial ignores it and still goes loopback.
    monkeypatch.setattr(svc, "firewall_status", lambda: {"config_ok": True, "live_ok": True})
    promoted = {}
    monkeypatch.setattr("lhpc.core.webserver.stage_and_validate",
                        lambda system, paths, cfg, proxies: (
                            promoted.__setitem__("bind", cfg.bind) or (True, "", "staged")))
    monkeypatch.setattr("lhpc.core.webserver.promote_config",
                        lambda paths: promoted.__setitem__("promoted", True))
    r = svc.firewall_boot_gate()
    assert r.ok and "LOOPBACK-ONLY" in r.summary
    assert promoted["bind"] == "127.0.0.1" and promoted["promoted"] is True


def test_integration_state_classification(tmp_path, monkeypatch):
    # P1-3: 'present' requires ALL required artifacts and NO journal; a missing required artifact
    # or a present journal (pending recovery / unsafe) is 'partial'; nothing at all is 'absent'.
    from lhpc.core import firewall as fwm
    from lhpc.core.service_firewall import FirewallOpsMixin
    from lhpc.core.probes.backends import FakeSystem
    from lhpc.core.paths import Paths
    from lhpc.core.services import ControllerService
    (tmp_path / "config").mkdir(exist_ok=True)
    fake = FakeSystem()                                # presence driven via the filesystem seam
    svc = ControllerService(system=fake.system, paths=Paths(runtime_root=tmp_path))
    # the hermetic conftest fixture defaults this reader to "absent" suite-wide (host /etc
    # isolation); THIS test exercises the REAL classification, so bind the mixin original. Artifact
    # presence is expressed through FakeSystem.paths — no global os.path.exists monkeypatch.
    monkeypatch.setattr(svc, "_fw_integration_state",
                        FirewallOpsMixin._fw_integration_state.__get__(svc))
    assert svc._fw_integration_state() == "absent"
    for p in svc._fw_required_artifacts():
        fake.paths.add(p)
    assert svc._fw_integration_state() == "present"
    fake.paths.add(fwm.JOURNAL_DEST)                    # pending-recovery journal
    assert svc._fw_integration_state() == "partial"
    fake.paths.discard(fwm.JOURNAL_DEST)
    fake.paths.discard(fwm.SNAPSHOT_DEST)              # a required artifact missing
    assert svc._fw_integration_state() == "partial"


# --- FW-R3: recovery/reset ownership-proof + transactional install ---------------------------

def test_load_never_clobbers_foreign_table(tmp_path):
    # P1-5: a foreign table inet lhpc (no ownership comment) must NOT be destroyed by a load.
    import json as _json
    from lhpc.core import firewall_helper as fh
    etc, run = str(tmp_path / "etc"), str(tmp_path / "run")
    _seed_meta(etc)
    cand = _candidate()
    sysx = _FakeSys()
    sysx.listing = _expected_live_json(cand)
    p = tmp_path / "c.json"
    p.write_text(_json.dumps(cand))
    assert fh.op_apply(sysx, str(p), etc_dir=etc, run_dir=run) == fh.EXIT_OK
    # Foreign table replaces ours; simulate a crash mid-apply (journal snapshot-committed).
    sysx.listing = _expected_live_json(cand, ownership="SOMEONE-ELSE")
    sysx.loaded_text = None
    fh.atomic_write(f"{etc}/firewall.journal.json",
                    _json.dumps({"op": "apply", "phase": "snapshot-committed",
                                 "old_snapshot": None}), 0o600)
    fh.op_check(sysx, etc_dir=etc, run_dir=run)
    assert sysx.loaded_text is None                        # recovery did NOT run our destroy+load


def test_reset_refuses_when_metadata_missing_but_table_present(tmp_path):
    # P1-5: no ownership metadata + a live inet lhpc table -> cannot prove ownership -> refuse.
    from lhpc.core import firewall_helper as fh
    etc, run = str(tmp_path / "etc"), str(tmp_path / "run")
    import os as _os
    _os.makedirs(etc, exist_ok=True)
    sysx = _FakeSys()
    sysx.listing = _expected_live_json(_candidate(), ownership="whoever")   # a table exists
    sysx.preexisting = True                                # ...live BEFORE this reset
    # no meta file written
    assert fh.op_reset(sysx, etc_dir=etc, run_dir=run) == fh.EXIT_NOT_OWNED


def test_reset_keeps_metadata_when_table_not_cleared(tmp_path, monkeypatch):
    import json as _json
    from lhpc.core import firewall_helper as fh
    etc, run = str(tmp_path / "etc"), str(tmp_path / "run")
    _seed_meta(etc)
    cand = _candidate()
    sysx = _FakeSys()
    sysx.listing = _expected_live_json(cand)
    p = tmp_path / "c.json"
    p.write_text(_json.dumps(cand))
    assert fh.op_apply(sysx, str(p), etc_dir=etc, run_dir=run) == fh.EXIT_OK
    # destroy is a no-op (table stays) -> reset must NOT delete metadata.
    orig_run = sysx.run
    def run_no_destroy(argv, **kw):
        if argv[:2] == ["nft", "destroy"]:
            return 0, "", ""                              # pretend success but table remains
        return orig_run(argv, **kw)
    monkeypatch.setattr(sysx, "run", run_no_destroy)
    rc = fh.op_reset(sysx, etc_dir=etc, run_dir=run)
    assert rc == fh.EXIT_FAIL
    assert (tmp_path / "etc" / "firewall.meta.json").exists()   # ownership record kept


def test_failed_first_apply_removes_bad_snapshot(tmp_path):
    import json as _json
    from lhpc.core import firewall_helper as fh
    etc, run = str(tmp_path / "etc"), str(tmp_path / "run")
    _seed_meta(etc)
    cand = _candidate()
    sysx = _FakeSys()
    sysx.listing = _json.dumps({"nftables": [{"metainfo": {}},
                               {"table": {"family": "inet", "name": "lhpc",
                                          "comment": "lhpc-owned:fixedid01"}}]})  # mismatch
    p = tmp_path / "c.json"
    p.write_text(_json.dumps(cand))
    rc = fh.op_apply(sysx, str(p), etc_dir=etc, run_dir=run)
    assert rc == fh.EXIT_FAIL
    assert not (tmp_path / "etc" / "firewall.snapshot.json").exists()   # bad snapshot removed


def test_apply_script_validates_before_enabling_units():
    from lhpc.core import firewall as fwm
    s = fwm.render_apply_script('{"schema":1}')
    apply_i = s.index(f"{fwm.HELPER_DEST} apply {fwm.CANDIDATE_DEST}")
    enable_i = s.index("systemctl enable")
    assert apply_i < enable_i                              # apply (validate) BEFORE enabling


def test_uninstall_detects_partial_firewall_integration():
    import pathlib
    src = pathlib.Path("uninstall.sh").read_text()
    assert "lhpc-firewall-check.service" in src and "lhpc-firewall-check.timer" in src
    assert "PARTIAL install" in src or "residual checker/timer" in src


# --- FW-R4: GET discipline, fail-closed validation, DHCP scoping ------------------------------

def test_settings_view_writes_no_scripts_on_get(tmp_path):
    # P2: rendering the Firewall settings section must NOT write the operator scripts (a GET
    # side effect). The files are (re)written only on a mutation (configure/render).
    svc = _svc(tmp_path)
    svc.firewall_status()
    svc.firewall_settings_view()
    import os
    base = svc._paths.under("config/files/firewall")
    assert not os.path.exists(base) or not os.listdir(base)   # nothing written on GET
    # _fw_units_enabled (the only status subprocess before) is now a filesystem check:
    import inspect
    assert "is-enabled" not in inspect.getsource(svc._fw_units_enabled)


def test_units_enabled_reads_symlinks_not_systemctl(tmp_path, monkeypatch):
    from lhpc.core import firewall as fwm
    from lhpc.core.service_firewall import FirewallOpsMixin
    from lhpc.core.probes.backends import FakeSystem
    from lhpc.core.paths import Paths
    from lhpc.core.services import ControllerService
    (tmp_path / "config").mkdir(exist_ok=True)
    fake = FakeSystem()
    svc = ControllerService(system=fake.system, paths=Paths(runtime_root=tmp_path))
    # exercise the REAL reader (conftest defaults it to False suite-wide); a subprocess is a hard
    # failure — enablement is a pure filesystem WantedBy-symlink check, driven via FakeSystem.paths.
    monkeypatch.setattr(svc, "_fw_units_enabled",
                        FirewallOpsMixin._fw_units_enabled.__get__(svc))
    monkeypatch.setattr(svc._system.runner, "run",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("subprocess!")))
    assert svc._fw_units_enabled() is False               # no symlinks -> not enabled (no subprocess)
    wants = ("/etc/systemd/system/multi-user.target.wants/" + fwm.LOADER_UNIT,
             "/etc/systemd/system/timers.target.wants/" + fwm.CHECKER_TIMER)
    for w in wants:
        fake.paths.add(w)
    assert svc._fw_units_enabled() is True                # both WantedBy symlinks present
    fake.paths.discard(wants[0])
    assert svc._fw_units_enabled() is False               # one missing -> not fully enabled


def test_dhcp_rules_are_family_scoped():
    from lhpc.core import firewall_helper as fh
    m = fh.resolve_model(_candidate(), ownership_id="x",
                         ssh_scopes=[{"proto": "tcp", "family": "dual", "addr": "*",
                                      "port": 22}])
    texts = [fh._rule_spec(r)[0] for r in m["rules"]]
    assert "meta nfproto ipv4 udp sport 67 udp dport 68 accept" in texts
    assert "meta nfproto ipv6 udp sport 547 udp dport 546 accept" in texts
    assert "udp sport 67 udp dport 68 accept" not in texts   # unscoped form gone


def test_receipt_reader_rejects_missing_mandatory_fields(tmp_path, monkeypatch):
    svc = _svc(tmp_path)
    rp = tmp_path / "r.json"
    import json as _json
    import os as _os
    from lhpc.core import firewall_helper as fh
    rp.write_text(_json.dumps({"protocol": fh.PROTOCOL_VERSION,   # current protocol, missing fields
                               "verdict": "verified"}))
    # bypass the root-owner check to reach the shape check
    real = _os.fstat
    monkeypatch.setattr("lhpc.core.service_firewall.stat.S_ISREG", lambda m: True)
    monkeypatch.setattr(_os, "fstat", lambda fd: type("S", (), {
        "st_mode": real(fd).st_mode & ~0o022, "st_uid": 0, "st_size": real(fd).st_size})())
    assert svc._fw_read_receipt(str(rp)) is None


class _RunResult:
    returncode = 1
    stdout = ""
    stderr = ""


# --- FW-R5/6/7: second-audit remediation -----------------------------------------------------

def test_concrete_console_bind_keeps_its_address(tmp_path):
    from lhpc.core import config as cfgmod
    svc = _svc(tmp_path)
    cfgmod.save_webserver_config(svc._paths, bind="192.168.178.5", port=8443,
                                 remote_exposed=True)
    svc._invalidate_config()
    ing = svc.firewall_candidate()["proxy_ingress"]
    assert ing and ing[0]["family"] == "ipv4" and ing[0]["addr"] == "192.168.178.5"


def test_meshcore_file_param_allow_derives_endpoint(tmp_path):
    from lhpc.core import config as cfgmod
    svc = _svc(tmp_path)
    band = svc._config_band("meshcore", "")
    cfgmod.save_stack_config(svc._paths, "meshcore",
                             {"meshcore_allow": "192.168.178.0/24"}, band)
    svc._invalidate_config()
    mc = [e for e in svc.firewall_candidate()["endpoints"] if "meshcore" in e["id"]]
    assert mc and mc[0]["port"] == 5000 and "192.168.178.0/24" in mc[0]["allow_cidrs"]


def test_endpoints_carry_auth_metadata(tmp_path):
    svc = _svc(tmp_path)
    auths = {e["id"]: e["auth"] for e in svc.firewall_candidate()["endpoints"]}
    assert auths.get("meshtastic.tcp-4403") == "none"


def test_op_functions_report_busy_when_lock_held(tmp_path):
    from lhpc.core import firewall_helper as fh
    etc, run = str(tmp_path / "etc"), str(tmp_path / "run")
    import os as _os
    _os.makedirs(etc, exist_ok=True)
    held = fh.OperationLock(f"{etc}/.firewall.lock")
    assert held.acquire(wait=True)
    try:
        import unittest.mock as _m
        with _m.patch.object(fh.OperationLock, "acquire", lambda self, wait=True, timeout=0: False):
            assert fh.op_check(_FakeSys(), etc_dir=etc, run_dir=run) == fh.EXIT_BUSY
            assert fh.op_load(_FakeSys(), etc_dir=etc, run_dir=run) == fh.EXIT_BUSY
    finally:
        held.release()


def test_failed_first_apply_tears_down_loaded_table(tmp_path):
    import json as _json
    from lhpc.core import firewall_helper as fh
    etc, run = str(tmp_path / "etc"), str(tmp_path / "run")
    _seed_meta(etc)
    cand = _candidate()
    sysx = _FakeSys()
    # load succeeds but verify mismatches -> first apply must DESTROY the loaded table.
    sysx.listing = _json.dumps({"nftables": [{"metainfo": {}},
                               {"table": {"family": "inet", "name": "lhpc",
                                          "comment": "lhpc-owned:fixedid01"}}]})
    destroyed = []
    orig = sysx.run
    def spy(argv, **kw):
        if argv[:2] == ["nft", "destroy"]:
            destroyed.append(argv)
        return orig(argv, **kw)
    sysx.run = spy
    p = tmp_path / "c.json"
    p.write_text(_json.dumps(cand))
    assert fh.op_apply(sysx, str(p), etc_dir=etc, run_dir=run) == fh.EXIT_FAIL
    assert destroyed                                      # loaded table torn down


def test_apply_refuses_owned_table_with_missing_snapshot(tmp_path):
    # AR2-P1: valid metadata + an LHPC-OWNED live table + a MISSING accepted snapshot must REFUSE
    # before any mutation — never enter the first-install teardown path and destroy the live table.
    import json as _json
    from lhpc.core import firewall_helper as fh
    etc, run = str(tmp_path / "etc"), str(tmp_path / "run")
    _seed_meta(etc)                                       # valid ownership metadata (fixedid01)
    cand = _candidate()
    sysx = _FakeSys()
    sysx.listing = _expected_live_json(cand)              # our OWNED table is live at entry...
    sysx.preexisting = True                               # ...but there is NO snapshot on disk
    destroyed = []
    orig = sysx.run
    sysx.run = lambda argv, **kw: (destroyed.append(argv) if argv[:2] == ["nft", "destroy"]
                                   else None, orig(argv, **kw))[1]
    p = tmp_path / "c.json"
    p.write_text(_json.dumps(cand))
    assert fh.op_apply(sysx, str(p), etc_dir=etc, run_dir=run) == fh.EXIT_INTERNAL
    assert not destroyed                                  # the owned table was NOT torn down
    assert not (tmp_path / "etc" / "firewall.snapshot.json").exists()   # nothing written


def test_receipt_reader_requires_complete_shape(tmp_path, monkeypatch):
    svc = _svc(tmp_path)
    rp = tmp_path / "r.json"
    import json as _json
    import os as _os
    from lhpc.core import firewall_helper as fh
    full = {"protocol": fh.PROTOCOL_VERSION, "integration_rev": "r", "verdict": "verified",
            "detail": "", "intent_hash": "a", "model_hash": "b", "boot_id": "c",
            "boottime": 1.0, "walltime": 2.0, "transitional": False, "foreign_tables": []}
    real = _os.fstat
    monkeypatch.setattr("lhpc.core.service_firewall.stat.S_ISREG", lambda m: True)
    monkeypatch.setattr(_os, "fstat", lambda fd: type("S", (), {
        "st_mode": real(fd).st_mode & ~0o022, "st_uid": 0, "st_size": real(fd).st_size})())
    rp.write_text(_json.dumps(full))
    assert svc._fw_read_receipt(str(rp)) is not None      # complete -> accepted
    for drop in ("walltime", "transitional", "foreign_tables", "detail", "integration_rev"):
        partial = dict(full)
        del partial[drop]
        rp.write_text(_json.dumps(partial))
        assert svc._fw_read_receipt(str(rp)) is None       # missing field -> rejected
    extra = dict(full)
    extra["surprise"] = 1
    rp.write_text(_json.dumps(extra))
    assert svc._fw_read_receipt(str(rp)) is None            # unknown field -> rejected


def test_ap_enabled_without_interface_or_cidr_is_rejected(tmp_path):
    svc = _svc(tmp_path)
    r = svc.firewall_configure(ap_enabled=True, ap_interface="", ap_cidr="")
    assert not r.ok and "Access-Point" in r.summary


def test_ap_dhcp_server_rule_is_family_scoped():
    from lhpc.core import firewall_helper as fh
    cand = _candidate(ap={"enabled": True, "interface": "wlan0", "cidr": "10.42.0.0/24"})
    m = fh.resolve_model(cand, ownership_id="x",
                         ssh_scopes=[{"proto": "tcp", "family": "dual", "addr": "*",
                                      "port": 22}])
    disc = next(fh._rule_spec(r)[0] for r in m["rules"] if r.get("match") == "ap-dhcp-server")
    assert "meta nfproto ipv4" in disc and 'iifname "wlan0"' in disc


def _narrowing_env(tmp_path, monkeypatch, *, console_bind="0.0.0.0", cidrs="192.168.0.0/24"):
    """A box whose firewall receipt was applied WITH the console exposed, and whose saved config has
    since removed that exposure. Returns (svc, applied_intent_hash)."""
    from lhpc.core import config as cfgmod
    from lhpc.core import firewall as fwm
    svc = _svc(tmp_path)
    cfgmod.save_stackweb_config(svc._paths, "meshtastic", mode="lan", port=8445,
                                allowed_cidrs=[cidrs])
    cfgmod.save_webserver_config(svc._paths, bind=console_bind, port=8443, remote_exposed=True,
                                 allowed_cidrs=[cidrs])
    svc._invalidate_config()
    applied = fwm.intent_hash(svc.firewall_candidate())          # what the firewall was applied for
    cfgmod.save_webserver_config(svc._paths, bind="127.0.0.1", remote_exposed=False)
    svc._invalidate_config()
    monkeypatch.setattr(type(svc), "_fw_integration_state", lambda self: "present")
    monkeypatch.setattr(type(svc), "firewall_status",
                        lambda self: {"config_ok": False, "live_ok": False})   # intent changed
    monkeypatch.setattr(type(svc), "firewall_render",
                        lambda self: ActionResult(True, "firewall scripts regenerated"))
    monkeypatch.setattr(type(svc), "_fw_receipt_fresh", lambda self, r: True)
    monkeypatch.setattr(fwm, "integration_rev", lambda: "REV")
    monkeypatch.setattr(type(svc), "_fw_read_receipt",
                        lambda self, path=None: {"verdict": "verified", "integration_rev": "REV",
                                                 "intent_hash": applied})
    return svc, applied


def _listeners(monkeypatch, addrs):
    from lhpc.core import webserver as wsm
    monkeypatch.setattr(wsm, "listener_addresses", lambda system, port: list(addrs))


def test_narrowing_allowed_only_when_console_removal_is_the_whole_change(tmp_path, monkeypatch):
    """The gate must stop a port BINDING ahead of a verified firewall, never one being CLOSED — but
    it may only conclude that from the receipt's own intent hash. Proving the DELTA (current
    candidate + the removed console ingress == what the firewall was applied for) is what makes
    every stale-evidence bypass impossible (audit P1)."""
    svc, _applied = _narrowing_env(tmp_path, monkeypatch)
    _listeners(monkeypatch, ["0.0.0.0"])
    ok, msg, _cmds = svc.firewall_gate_activation({8445})
    assert ok, msg                                              # the real bug: closing the console


def test_narrowing_refused_when_anything_else_changed_too(tmp_path, monkeypatch):
    from lhpc.core import config as cfgmod
    svc, _applied = _narrowing_env(tmp_path, monkeypatch)
    _listeners(monkeypatch, ["0.0.0.0"])
    cfgmod.save_stackweb_config(svc._paths, "meshtastic", allowed_cidrs=["10.0.0.0/8"])  # widened
    svc._invalidate_config()
    ok, msg, _cmds = svc.firewall_gate_activation({8445})
    assert not ok and "changes pending" in msg


def test_narrowing_refused_on_stale_or_foreign_receipt(tmp_path, monkeypatch):
    """A previous boot, a replaced helper, an unverified readback or a missing receipt must all fail
    closed — none of them prove the rules currently loaded."""
    svc, applied = _narrowing_env(tmp_path, monkeypatch)
    _listeners(monkeypatch, ["0.0.0.0"])

    monkeypatch.setattr(type(svc), "_fw_receipt_fresh", lambda self, r: False)   # previous boot
    assert not svc.firewall_gate_activation({8445})[0]
    monkeypatch.setattr(type(svc), "_fw_receipt_fresh", lambda self, r: True)

    for bad in ({"verdict": "mismatch", "integration_rev": "REV", "intent_hash": applied},
                {"verdict": "verified", "integration_rev": "OLD", "intent_hash": applied},
                {"verdict": "verified", "integration_rev": "REV", "intent_hash": "deadbeef"},
                None):
        monkeypatch.setattr(type(svc), "_fw_read_receipt", lambda self, path=None, b=bad: b)
        assert not svc.firewall_gate_activation({8445})[0]


def test_narrowing_refused_when_the_previous_bind_is_unknown_or_ambiguous(tmp_path, monkeypatch):
    """The previous console bind comes from the LIVE listener, because the saved bind is already
    loopback. No listener, or more than one, means it cannot be reconstructed — refuse."""
    svc, _applied = _narrowing_env(tmp_path, monkeypatch)
    for addrs in ([], ["0.0.0.0", "192.168.0.5"]):
        _listeners(monkeypatch, addrs)
        assert not svc.firewall_gate_activation({8445})[0]


def test_narrowing_handles_a_concrete_console_bind(tmp_path, monkeypatch):
    """A concrete remote bind is legal and the candidate scopes the rule to that exact address, so
    the reconstruction uses the live address — canonicalized, since /proc reports a wildcard as
    0.0.0.0 while the candidate stores '*'."""
    svc, _applied = _narrowing_env(tmp_path, monkeypatch, console_bind="192.168.0.5")
    _listeners(monkeypatch, ["192.168.0.5"])
    assert svc.firewall_gate_activation({8445})[0]
    _listeners(monkeypatch, ["0.0.0.0"])                        # wrong address -> different hash
    assert not svc.firewall_gate_activation({8445})[0]


def test_console_still_remote_is_never_a_narrowing(tmp_path, monkeypatch):
    """Desired remote exposure that was verified but never activated must NOT open the gate — the
    exact stale-evidence bypass the audit described."""
    from lhpc.core import config as cfgmod
    svc, _applied = _narrowing_env(tmp_path, monkeypatch)
    _listeners(monkeypatch, ["0.0.0.0"])
    cfgmod.save_webserver_config(svc._paths, bind="0.0.0.0", remote_exposed=True)
    svc._invalidate_config()
    assert not svc.firewall_gate_activation({8443, 8445})[0]


def test_gate_never_names_a_script_it_could_not_render(tmp_path, monkeypatch):
    """No result may point at firewall-apply.sh unless THIS call rewrote it — a stale or missing
    script applies the wrong intent (audit P2a). Holds for the narrowing, the refusal and the
    partially-installed branch."""
    from lhpc.core import config as cfgmod
    svc, _applied = _narrowing_env(tmp_path, monkeypatch)
    _listeners(monkeypatch, ["0.0.0.0"])
    monkeypatch.setattr(type(svc), "firewall_render",
                        lambda self: ActionResult(False, "could not render firewall scripts: EACCES"))

    ok, msg, cmds = svc.firewall_gate_activation({8445})          # narrowing + render failure
    assert ok and "could not be regenerated" in msg and cmds == ["lhpc firewall --script"]

    cfgmod.save_stackweb_config(svc._paths, "meshtastic", allowed_cidrs=["10.0.0.0/8"])
    svc._invalidate_config()
    ok, msg, cmds = svc.firewall_gate_activation({8445})          # refusal + render failure
    assert not ok and "could NOT be regenerated" in msg
    assert not any("firewall-apply.sh" in c for c in cmds)

    monkeypatch.setattr(type(svc), "_fw_integration_state", lambda self: "partial")
    ok, msg, cmds = svc.firewall_gate_activation({8445})          # partial + render failure
    assert not ok and not any("firewall-apply.sh" in c for c in cmds)


# ---- directional coverage + containment verdict ------------------------------------------

def _sc(proto="tcp", family="dual", addr="*", port=4403, **kw):
    d = {"proto": proto, "family": family, "addr": addr, "port": port}
    d.update(kw)
    return d


def test_scope_covers_is_directional_not_overlap():
    # _scopes_overlap answers "could these touch?" and is deliberately permissive — right for
    # REFUSING an extra_allow, inverted for PROVING protection. Each row below is a case where
    # overlap says yes and coverage must say no; greening on overlap would put a green badge on a
    # port that is still reachable.
    from lhpc.core.firewall import _scopes_overlap, scope_covers
    ipv4_only, dual_listener = _sc(family="ipv4"), _sc(family="dual")
    concrete, wildcard = _sc(family="ipv4", addr="192.168.1.5"), _sc(family="ipv4", addr="*")
    for protector, listener in ((ipv4_only, dual_listener), (concrete, wildcard)):
        assert _scopes_overlap(protector, listener) is True      # the permissive answer
        assert scope_covers(protector, listener) is False        # the correct one
    assert scope_covers(_sc(), _sc(family="ipv4", addr="10.0.0.5")) is True   # dual covers ipv4
    assert scope_covers(_sc(), _sc(port=8080)) is False                        # port must match
    assert scope_covers(_sc(proto="udp"), _sc()) is False                      # proto must match


def test_ipv6_wildcard_listener_needs_dual_coverage():
    # tcp_listeners() tags each /proc record ipv4 or ipv6, never dual. A socket bound `::` with
    # the default bindv6only=0 ALSO accepts IPv4 while appearing only as one ipv6 record, so an
    # ipv6-only rule must not vouch for it.
    from lhpc.core.firewall import scope_covers
    v6_wildcard_listener = _sc(family="ipv6", addr="*")
    assert scope_covers(_sc(family="ipv6", addr="*"), v6_wildcard_listener) is False
    assert scope_covers(_sc(family="dual"), v6_wildcard_listener) is True


def _fwstat(mode="secure-default", eps=(), ing=(), extra=(), **kw):
    st = {"installed": True, "config_ok": True, "live_ok": True, "transitional": False,
          "candidate": {"mode": mode, "endpoints": list(eps),
                        "proxy_ingress": list(ing), "extra_allow": list(extra)}}
    st.update(kw)
    return st


def test_firewall_containment_verdicts(tmp_path):
    svc = _svc(tmp_path)
    from types import SimpleNamespace
    live = svc.listener_scopes(4403, [SimpleNamespace(family="ipv4", ip="0.0.0.0",
                                                      port=4403, inode=1)])
    allow = _sc(selected=True, allow_cidrs=["10.42.0.0/24"])
    deny = _sc(selected=False, allow_cidrs=[])
    open_ep = _sc(selected=True, allow_cidrs=[])
    cases = [
        # selected=False alone is NOT an early DROP: only a deny_default endpoint is dropped
        # ahead of the accepts. An ORDINARY unselected endpoint falls to the chain policy, where
        # a legal extra_allow may accept it first — so it must never read as denied/green.
        ("secure-default: deny_default unselected IS denied",
         _fwstat(eps=[dict(deny, deny_default=True)]), "denied"),
        ("secure-default: ORDINARY unselected is NOT denied",
         _fwstat(eps=[dict(deny, deny_default=False)]), "unknown"),
        ("ordinary unselected + overlapping extra_allow is never denied",
         _fwstat(eps=[dict(deny, deny_default=False)], extra=[_sc()]), "unknown"),
        ("secure-default LAN allow is a restriction", _fwstat(eps=[allow]), "restricted"),
        ("an unrestricted allow is open", _fwstat(eps=[open_ep]), "open"),
        # compatibility has chain policy ACCEPT: its CIDR allows are NOT installed as restrictions.
        ("compatibility CIDRs prove nothing", _fwstat("compatibility", eps=[allow]), "open"),
        ("compatibility still drops unselected", _fwstat("compatibility", eps=[deny]), "denied"),
        # A verified TRANSITIONAL ruleset may retain older transition_allow scopes that widen
        # reachability, so it may never improve a colour.
        ("transitional proves nothing", _fwstat(eps=[dict(deny, deny_default=True)], transitional=True), "unknown"),
        ("stale config proves nothing", _fwstat(eps=[dict(deny, deny_default=True)], config_ok=False), "unknown"),
        ("unverified live proves nothing", _fwstat(eps=[dict(deny, deny_default=True)], live_ok=False), "unknown"),
        ("absent firewall proves nothing", _fwstat(eps=[dict(deny, deny_default=True)], installed=False), "unknown"),
        ("an unrelated port never matches", _fwstat(eps=[_sc(selected=False, port=8080)]), "unknown"),
        # extra_allow may overlap an ordinary endpoint and widen it; do not compute the union.
        ("a wider overlapping extra_allow kills a restriction claim",
         _fwstat(eps=[allow], extra=[_sc()]), "unknown"),
    ]
    for name, st, want in cases:
        assert svc.firewall_containment(live, st) == want, name


def test_containment_needs_proxy_ingress_or_endpoint_coverage(tmp_path):
    svc = _svc(tmp_path)
    from types import SimpleNamespace
    live = svc.listener_scopes(8443, [SimpleNamespace(family="ipv4", ip="0.0.0.0",
                                                      port=8443, inode=1)])
    ing = _sc(port=8443, allow_cidrs=["10.42.0.0/24"])
    assert svc.firewall_containment(live, _fwstat(ing=[ing])) == "restricted"
    # ...but not in compatibility mode, where proxy_ingress CIDRs are not restrictions.
    assert svc.firewall_containment(live, _fwstat("compatibility", ing=[ing])) == "open"
    assert svc.firewall_containment([], _fwstat(ing=[ing])) == "unknown"   # nothing listening


def _posture_row(**kw):
    from lhpc.core.webserver import posture
    return {"kind": "console", "posture": posture(**kw)}


def test_security_pill_only_claims_restricted_source_when_that_is_the_reason(tmp_path):
    # Every yellow used to read "lan-exposed / a no-auth port is reachable but restricted to
    # allowed sources". That is false for the other legitimate yellows — local cleartext http, or
    # an authenticated listener open to all source addresses. The reason is resolved where the row
    # is built and carried; security_pill stays a pure aggregator and never re-reads firewall state.
    svc = _svc(tmp_path)
    noauth_restricted = _posture_row(local=False, public=False, access_mode="no-auth",
                                     has_cidrs=True, scheme="https", firewall_contained=True)
    assert noauth_restricted["posture"]["sec_level"] == "warn"
    pill = svc.security_pill([noauth_restricted])
    assert pill["level"] == "warn" and pill["label"] == "lan-exposed"
    assert "restricted to allowed sources" in pill["title"]

    # authenticated but open to ALL source addresses -> yellow for a different reason
    authed_public = _posture_row(local=False, public=True, access_mode="auth-everywhere",
                                 has_cidrs=True, scheme="https")
    assert authed_public["posture"]["sec_level"] == "warn"
    p2 = svc.security_pill([authed_public])
    assert p2["level"] == "warn" and p2["label"] == "review"
    assert "restricted to allowed sources" not in p2["title"]

    # local cleartext http -> yellow, also not a restricted-source claim
    local_http = _posture_row(local=True, public=False, access_mode="no-auth",
                              has_cidrs=False, scheme="http")
    assert local_http["posture"]["sec_level"] == "warn"
    assert svc.security_pill([local_http])["label"] == "review"

    # MIXED yellow causes -> generic review, never the specific claim
    mixed = svc.security_pill([noauth_restricted, authed_public])
    assert mixed["level"] == "warn" and mixed["label"] == "review"


def test_a_yellow_port_row_only_claims_restriction_when_one_was_proven(tmp_path):
    # port_exposure("192.168.1.0/24") returns ("warn","LAN") with NO firewall input at all, and
    # two other paths in _dashboard_port_row also yield yellow without a verdict. Marking every
    # yellow port row `restricted_noauth` re-introduced the exact over-claim the reason plumbing
    # removes — and it bites a Zero where containment came back `unknown`.
    svc = _svc(tmp_path)
    proven = {"kind": "port", "exposure": {"level": "warn", "label": "LAN"},
              "warn_reason": "restricted_noauth", "live_scope": "exposed"}
    unproven = {"kind": "port", "exposure": {"level": "warn", "label": "LAN"},
                "warn_reason": "review", "live_scope": "exposed"}
    assert svc.security_pill([proven])["label"] == "lan-exposed"
    p = svc.security_pill([unproven])
    assert p["level"] == "warn" and p["label"] == "review"
    assert "restricted to allowed sources" not in p["title"]
    assert svc.security_pill([proven, unproven])["label"] == "review"     # mixed => generic


def test_an_absent_listener_never_claims_reachability(tmp_path):
    # A row may exist for status/UI purposes while nothing is listening. It is not an exposure,
    # so it must not contribute "a no-auth port is reachable" wording.
    svc = _svc(tmp_path)
    absent = {"kind": "port", "exposure": {"level": "warn", "label": "LAN"},
              "warn_reason": "", "live_scope": "absent"}
    p = svc.security_pill([absent])
    assert p["label"] != "lan-exposed", "an absent listener must not read as restricted exposure"
    assert "restricted to allowed sources" not in p["title"]


# ---- production /proc/net/tcp6 evidence reaches the coverage rule in the right shape -------

def test_a_real_proc_net_tcp6_wildcard_row_is_wildcard_by_the_time_coverage_runs(tmp_path):
    # The `::` rule in scope_covers was right and production evidence never reached it: the kernel
    # writes the wildcard as 32 hex zeroes and the parser passed them through, so `listener_scopes`
    # (which recognises only "::") produced a CONCRETE address — and an ipv6-only rule then
    # "covered" a socket that still accepts IPv4. Drive the whole real chain, not scope_covers with
    # a hand-written addr="*", which is the test shape that missed this.
    from lhpc.core.probes.backends import parse_proc_net_tcp
    from lhpc.core.firewall import scope_covers
    table = ("  sl  local_address                         remote_address                        "
             "st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode\n"
             "   0: 00000000000000000000000000000000:1F63 00000000000000000000000000000000:0000 "
             "0A 00000000:00000000 00:00000000 00000000     0        0 26612 1 0000 100 0\n")
    listeners = parse_proc_net_tcp(table, "ipv6")
    assert [(l.family, l.port) for l in listeners] == [("ipv6", 0x1F63)]
    scopes = _svc(tmp_path).listener_scopes(0x1F63, listeners)
    assert scopes == [{"proto": "tcp", "family": "ipv6", "addr": "*", "port": 0x1F63}]
    v6_only = {"proto": "tcp", "family": "ipv6", "addr": "*", "port": 0x1F63}
    dual = {"proto": "tcp", "family": "dual", "addr": "*", "port": 0x1F63}
    assert scope_covers(v6_only, scopes[0]) is False    # `::` accepts IPv4 too
    assert scope_covers(dual, scopes[0]) is True


def test_other_ipv6_forms_stay_conservative():
    # Only the all-zero wildcard is normalised. ::1 and a concrete address keep their raw hex, so
    # they can never be mistaken for a wildcard (nor claimed as loopback on unproven grounds).
    from lhpc.core.probes.backends import _decode_hex_ip
    assert _decode_hex_ip("0" * 32) == "::"
    assert _decode_hex_ip("00000000000000000000000001000000") != "*"
    assert _decode_hex_ip("00000000000000000000FFFF0100007F") != "*"


# ---- a genuinely ABSENT raw listener is not an exposure -----------------------------------

def _row(port, level, live_scope, warn_reason):
    return {"kind": "port", "port": port, "live_scope": live_scope,
            "exposure": {"level": level, "label": "LAN"}, "warn_reason": warn_reason}


def test_an_absent_raw_listener_contributes_no_exposure_at_all(tmp_path):
    # Clearing only `warn_reason` was not enough: security_pill bumped `worst` from the row's
    # LEVEL first, so a stopped helper with a LAN-shaped saved allow-list still made the whole box
    # read "Exposure present — verify the firewall / access mode."
    svc = _svc(tmp_path)
    pill = svc.security_pill([_row("5000", "warn", "absent", "")])
    assert pill["level"] == "ok"
    assert "Exposure" not in pill["title"] and "reachable" in pill["title"]
    # A red absent row is no different — the listener is DOWN, not reachable-and-open.
    assert svc.security_pill([_row("5000", "bad", "absent", "review")])["level"] == "ok"


def test_an_unverified_running_endpoint_stays_a_review_and_never_disappears(tmp_path):
    # UNVERIFIED is deliberately NOT absent: a listener exists, only its identity is in doubt.
    svc = _svc(tmp_path)
    pill = svc.security_pill([_row("8001", "warn", "unverified", "review")])
    assert pill["level"] == "warn" and pill["label"] == "review"


def test_a_restricted_live_endpoint_still_reads_lan_exposed(tmp_path):
    pill = _svc(tmp_path).security_pill([_row("8001", "warn", "exposed", "restricted_noauth")])
    assert pill["level"] == "warn" and pill["label"] == "lan-exposed"
