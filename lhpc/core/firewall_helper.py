#!/usr/bin/python3
"""Managed Firewall — ROOT HELPER source.

This file is installed BYTE-FOR-BYTE to `/etc/lhpc/firewall-helper` (root:root, 0755) by the
operator-run apply script and is the ONLY code the root loader/checker units execute. The
trust boundary is absolute: after installation no root process executes or imports mutable
lhpc checkout content — therefore this module imports NOTHING from lhpc (stdlib only; an
import-hygiene test enforces it) and re-validates the unprivileged candidate independently
(the schema duplication with lhpc.core.firewall is deliberate and drift-tested).

The candidate is DATA. It cannot name tables, chains, hooks, priorities, policies, rule
order or the installation ownership ID — this helper injects all fixed structure, derives
authoritative SSH scopes at apply time, merges transition-preservation scopes, and only then
computes `model_hash` over the complete canonical expected table. Live verification
normalizes `nft -j list table inet lhpc` into the SAME canonical model (handles/counters are
the only tolerated volatility; anything unknown is a mismatch).
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import re

# Bumped 1->2 when the persisted receipt/snapshot contract changed (integration_rev added, band
# scope-key dimension). An old-protocol snapshot/receipt is rejected -> forces a fresh apply.
PROTOCOL_VERSION = 2
CANDIDATE_SCHEMA = 1


def integration_rev():
    """Full SHA-256 of THIS helper's own source — the integration revision stamped into every
    receipt and snapshot. The controller computes the SAME hash over its packaged helper_source()
    (both via read_text().encode('utf-8')); a mismatch means the INSTALLED helper is stale (an
    lhpc update changed the helper but the operator has not re-applied) -> never green until then.
    Uses builtin open() in utf-8 text mode; TRAILING newlines are stripped before hashing so the
    heredoc that installs this file (it appends a trailing newline) still hashes equal to the
    controller's `helper_source().rstrip()` — while any SEMANTIC change is still detected. No
    pathlib import (the helper's stdlib-only trust boundary)."""
    try:
        with open(__file__, encoding="utf-8") as f:
            return hashlib.sha256(f.read().rstrip("\n").encode("utf-8")).hexdigest()
    except OSError:
        return ""


TABLE_FAMILY = "inet"
TABLE_NAME = "lhpc"
CHAIN_NAME = "input"
CHAIN_HOOK = "input"
CHAIN_PRIORITY = 0            # "filter" priority

MAX_ENDPOINTS = 128
MAX_PROXY_INGRESS = 32
MAX_CIDRS = 32
MAX_SSH_PORTS = 16
MAX_EXTRA_ALLOW = 64

_MODES = ("secure-default", "compatibility")
_PROTOS = ("tcp", "udp")
_FAMILIES = ("ipv4", "ipv6", "dual")
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")
_IFACE_RE = re.compile(r"^[A-Za-z0-9_.-]{1,15}$")

_TOP_KEYS = ("schema", "mode", "endpoints", "proxy_ingress", "ssh_ports", "ap", "extra_allow")
# `band` is an OPTIONAL scope-key dimension (default "" = band-agnostic / single-band) — the
# drift-tested twin of the controller's schema; keep both in lock-step.
_EP_KEYS = ("id", "proto", "family", "addr", "port", "allow_cidrs", "selected", "deny_default",
            "auth", "band")
_INGRESS_KEYS = ("proto", "family", "addr", "port", "allow_cidrs")
_EXTRA_KEYS = ("proto", "family", "addr", "port", "cidr")
_AP_KEYS = ("enabled", "interface", "cidr")


# --- independent candidate validation (deliberate duplicate; see module docstring) -----------

def _is_bool(v):
    return type(v) is bool


def _is_int(v):
    return type(v) is int and type(v) is not bool


def _is_str(v):
    return type(v) is str


def _port_ok(v):
    return _is_int(v) and 1 <= v <= 65535


def _ip_family(text):
    try:
        return "ipv4" if ipaddress.ip_address(text).version == 4 else "ipv6"
    except ValueError:
        return None


def _cidr_family(text):
    try:
        return "ipv4" if ipaddress.ip_network(text).version == 4 else "ipv6"
    except ValueError:
        return None


def _keys(obj, allowed, path, errors):
    for k in allowed:
        if k not in obj:
            errors.append(f"{path}: missing '{k}'")
    for k in obj:
        if k not in allowed:
            errors.append(f"{path}: unknown field '{k}'")


def _scope(entry, path, errors):
    if entry.get("proto") not in _PROTOS:
        errors.append(f"{path}: bad proto")
    fam = entry.get("family")
    if fam not in _FAMILIES:
        errors.append(f"{path}: bad family")
    addr = entry.get("addr")
    if not _is_str(addr):
        errors.append(f"{path}: addr must be a string")
    elif addr != "*":
        if fam == "dual":
            errors.append(f"{path}: family 'dual' requires addr '*'")
        elif fam in ("ipv4", "ipv6") and _ip_family(addr) != fam:
            errors.append(f"{path}: addr/family mismatch")
    if not _port_ok(entry.get("port")):
        errors.append(f"{path}: bad port")


def _cidrs(cidrs, fam, path, errors):
    if type(cidrs) is not list:
        errors.append(f"{path}: must be a list")
        return
    if len(cidrs) > MAX_CIDRS:
        errors.append(f"{path}: too many entries")
        return
    for i, c in enumerate(cidrs):
        cf = _cidr_family(c) if _is_str(c) else None
        if cf is None:
            errors.append(f"{path}[{i}]: invalid CIDR")
        elif fam in ("ipv4", "ipv6") and cf != fam:
            errors.append(f"{path}[{i}]: CIDR family mismatch")


def _overlap(a, b):
    if a.get("proto") != b.get("proto") or a.get("port") != b.get("port"):
        return False
    fa, fb = a.get("family"), b.get("family")
    if fa != fb and "dual" not in (fa, fb):
        return False
    return a.get("addr") == "*" or b.get("addr") == "*" or a.get("addr") == b.get("addr")


def validate_candidate(cand):
    """Strict validation; list of errors, [] = valid. Must stay behavior-identical to
    lhpc.core.firewall.validate_candidate (drift-tested from the repo side)."""
    errors = []
    if type(cand) is not dict:
        return ["candidate: must be an object"]
    _keys(cand, _TOP_KEYS, "candidate", errors)
    if cand.get("schema") != CANDIDATE_SCHEMA:
        errors.append(f"candidate: schema must be {CANDIDATE_SCHEMA}")
    if cand.get("mode") not in _MODES:
        errors.append(f"candidate: mode must be one of {_MODES}")

    deny = []
    eps = cand.get("endpoints")
    if type(eps) is not list:
        errors.append("endpoints: must be a list")
    elif len(eps) > MAX_ENDPOINTS:
        errors.append("endpoints: too many entries")
    else:
        seen = set()
        for i, ep in enumerate(eps):
            path = f"endpoints[{i}]"
            if type(ep) is not dict:
                errors.append(f"{path}: must be an object")
                continue
            _keys(ep, _EP_KEYS, path, errors)
            eid = ep.get("id")
            band = ep.get("band", "")
            if not _is_str(band):
                errors.append(f"{path}: band must be a string")
                band = ""
            if not _is_str(eid) or not _ID_RE.match(eid or ""):
                errors.append(f"{path}: invalid id")
            elif (eid, band) in seen:
                errors.append(f"{path}: duplicate id")
            else:
                seen.add((eid, band))
            _scope(ep, path, errors)
            _cidrs(ep.get("allow_cidrs"), ep.get("family"), f"{path}.allow_cidrs", errors)
            for flag in ("selected", "deny_default"):
                if not _is_bool(ep.get(flag)):
                    errors.append(f"{path}: {flag} must be a boolean")
            if ep.get("auth") not in ("none", "password", "mtls", "token"):
                errors.append(f"{path}: bad auth")
            if ep.get("deny_default") is True:
                deny.append(ep)

    ing = cand.get("proxy_ingress")
    if type(ing) is not list:
        errors.append("proxy_ingress: must be a list")
    elif len(ing) > MAX_PROXY_INGRESS:
        errors.append("proxy_ingress: too many entries")
    else:
        for i, e in enumerate(ing):
            path = f"proxy_ingress[{i}]"
            if type(e) is not dict:
                errors.append(f"{path}: must be an object")
                continue
            _keys(e, _INGRESS_KEYS, path, errors)
            _scope(e, path, errors)
            _cidrs(e.get("allow_cidrs"), e.get("family"), f"{path}.allow_cidrs", errors)

    ssh = cand.get("ssh_ports")
    if type(ssh) is not list:
        errors.append("ssh_ports: must be a list")
    elif len(ssh) > MAX_SSH_PORTS:
        errors.append("ssh_ports: too many entries")
    else:
        for i, p in enumerate(ssh):
            if not _port_ok(p):
                errors.append(f"ssh_ports[{i}]: bad port")

    ap = cand.get("ap")
    if type(ap) is not dict:
        errors.append("ap: must be an object")
    else:
        _keys(ap, _AP_KEYS, "ap", errors)
        if not _is_bool(ap.get("enabled")):
            errors.append("ap: enabled must be a boolean")
        if not _is_str(ap.get("interface")) or not _is_str(ap.get("cidr")):
            errors.append("ap: interface and cidr must be strings")
        elif ap.get("enabled") is True:
            if not _IFACE_RE.match(ap["interface"] or ""):
                errors.append("ap: enabled requires a valid interface name")
            if _cidr_family(ap["cidr"] or "") != "ipv4":
                errors.append("ap: enabled requires a valid IPv4 cidr")

    extra = cand.get("extra_allow")
    if type(extra) is not list:
        errors.append("extra_allow: must be a list")
    elif len(extra) > MAX_EXTRA_ALLOW:
        errors.append("extra_allow: too many entries")
    else:
        for i, e in enumerate(extra):
            path = f"extra_allow[{i}]"
            if type(e) is not dict:
                errors.append(f"{path}: must be an object")
                continue
            _keys(e, _EXTRA_KEYS, path, errors)
            _scope(e, path, errors)
            c = e.get("cidr")
            cf = _cidr_family(c) if _is_str(c) else None
            if cf is None:
                errors.append(f"{path}: invalid cidr")
            elif e.get("family") in ("ipv4", "ipv6") and cf != e["family"]:
                errors.append(f"{path}: cidr family mismatch")
            for dep in deny:
                if _overlap(e, dep):
                    errors.append(f"{path}: covers deny-default endpoint '{dep.get('id')}' "
                                  f"— use that endpoint's checkbox")
    return errors


# --- model resolution (pure; ownership + SSH + transitions injected HERE, never accepted) ----

def _allow_rules(scope, cidrs):
    """Expand one allow scope into ordered rule dicts, one per CIDR (or one unrestricted
    rule when no CIDR narrowing applies). Family scoping is explicit: an ipv4-only scope
    never opens IPv6, and vice versa; 'dual' emits an unqualified l4 match."""
    rules = []
    base = {"action": "accept", "proto": scope["proto"], "port": scope["port"],
            "family": scope["family"], "addr": scope.get("addr", "*")}
    if cidrs:
        for c in sorted(cidrs):
            r = dict(base)
            r["saddr"] = c
            rules.append(r)
    else:
        rules.append(base)
    return rules


def resolve_model(candidate, *, ownership_id, ssh_scopes, transition_allow=(),
                  transition_drop=()):
    """Build the complete expected `inet lhpc` table model from a VALIDATED candidate plus
    root-side facts: the persisted installation ownership ID (embedded as the table
    comment), the AUTHORITATIVELY resolved SSH scopes (sshd -T + unit cmdline + active
    listeners — computed by the caller in root context), and transition-preservation scopes.
    The candidate has no influence over structure: table/chain identity, hook, priority,
    policy and rule ORDER are fixed here."""
    mode = candidate["mode"]
    rules = []
    add = rules.append

    # 1. Loopback first — everything local always works.
    add({"action": "accept", "match": "iif-lo"})
    # 2. Deny-default endpoint drops BEFORE conntrack: sessions established before apply
    #    must die too. Non-loopback ingress only (iif-lo already accepted above). In
    #    compatibility mode the same early-drop set covers EVERY unselected direct listener.
    if mode == "secure-default":
        droppable = [e for e in candidate["endpoints"]
                     if e["deny_default"] and not e["selected"]]
    else:
        droppable = [e for e in candidate["endpoints"] if not e["selected"]]
    _seen_drop = set()
    for ep in sorted(droppable, key=lambda e: (e["id"], e.get("band", ""))):
        k = (ep["proto"], ep["family"], ep["addr"], ep["port"])
        if k in _seen_drop:            # identical scope from a divergent-band row -> one rule
            continue
        _seen_drop.add(k)
        add({"action": "drop", "proto": ep["proto"], "port": ep["port"],
             "family": ep["family"], "addr": ep["addr"], "endpoint": ep["id"]})
    for scope in sorted(transition_drop, key=_scope_key):
        add({"action": "drop", "proto": scope["proto"], "port": scope["port"],
             "family": scope["family"], "addr": scope.get("addr", "*"),
             "transition": True})
    # 3. Conntrack.
    add({"action": "drop", "match": "ct-invalid"})
    add({"action": "accept", "match": "ct-established-related"})
    # 4. ICMP: v6 is mandatory (NDP) or IPv6 dies entirely; v4 for field diagnosability.
    add({"action": "accept", "match": "icmpv6"})
    add({"action": "accept", "match": "icmp"})

    if mode == "secure-default":
        # 5. DHCP CLIENT preservation is BASELINE (never AP-gated): v4 replies 67->68
        #    (RFC 2131) and, for IPv6-enabled networks, DHCPv6 replies 547->546 (RFC 8415).
        add({"action": "accept", "match": "dhcpv4-client"})
        add({"action": "accept", "match": "dhcpv6-client"})
        # 6. mDNS scoped to its multicast destinations only.
        add({"action": "accept", "match": "mdns-v4"})
        add({"action": "accept", "match": "mdns-v6"})
        # 7. SSH — authoritative scopes resolved by the caller; never lock the operator out.
        for scope in sorted(ssh_scopes, key=_scope_key):
            rules.extend(_allow_rules(scope, scope.get("allow_cidrs") or ()))
        # 8. AP server rules (strictly opt-in, interface-scoped; DISCOVER arrives from
        #    0.0.0.0 so the DHCP rule carries NO source-CIDR condition).
        ap = candidate["ap"]
        if ap["enabled"]:
            add({"action": "accept", "match": "ap-dhcp-server", "iif": ap["interface"]})
            for proto in ("udp", "tcp"):
                add({"action": "accept", "match": "ap-dns", "proto": proto,
                     "iif": ap["interface"], "saddr": ap["cidr"]})
        # 9. Derived allows: selected endpoints, proxy ingress, transition preservation,
        #    extra_allow — each with full family/addr/CIDR scoping.
        _seen_allow = set()
        for ep in sorted((e for e in candidate["endpoints"] if e["selected"]),
                         key=lambda e: (e["id"], e.get("band", ""))):
            k = (ep["proto"], ep["family"], ep["addr"], ep["port"],
                 tuple(sorted(ep["allow_cidrs"])))
            if k in _seen_allow:
                continue
            _seen_allow.add(k)
            rules.extend(_allow_rules(ep, ep["allow_cidrs"]))
        for ing in sorted(candidate["proxy_ingress"], key=_scope_key):
            rules.extend(_allow_rules(ing, ing["allow_cidrs"]))
        for scope in sorted(transition_allow, key=_scope_key):
            rules.extend(_allow_rules(scope, scope.get("allow_cidrs") or ()))
        for e in sorted(candidate["extra_allow"],
                        key=lambda x: (x["proto"], x["family"], x["addr"], x["port"],
                                       x["cidr"])):
            rules.extend(_allow_rules(e, [e["cidr"]]))
        policy = "drop"
    else:
        # Compatibility: no default drop — the drops above are the entire effect.
        policy = "accept"

    return {
        "protocol": PROTOCOL_VERSION,
        "family": TABLE_FAMILY,
        "table": TABLE_NAME,
        "comment": f"lhpc-owned:{ownership_id}",
        "chain": {"name": CHAIN_NAME, "hook": CHAIN_HOOK, "type": "filter",
                  "priority": CHAIN_PRIORITY, "policy": policy},
        "rules": rules,
    }


def _scope_key(s):
    return (s.get("proto", ""), s.get("family", ""), s.get("addr", ""), s.get("port", 0))


def canonical_model(model) -> str:
    """Canonical JSON of the complete expected table. Rule ORDER is meaningful (nftables
    evaluates in order), so the rules list is serialized as-is — sorting happens at
    resolution time under fixed structural placement, never here."""
    return json.dumps(model, sort_keys=True, separators=(",", ":"))


def model_hash(model) -> str:
    return hashlib.sha256(canonical_model(model).encode("utf-8")).hexdigest()


# --- nft text + expected-JSON builders -------------------------------------------------------
# ONE spec per rule kind produces BOTH the `.nft` transaction text and the expected `nft -j`
# expression list. Live verification is a structural comparison of the stripped live listing
# against `expected_listing(model)` — bijective by construction, so render and verification
# cannot drift, and ANY live content the builder would not produce (extra rules, chains,
# reordered entries, foreign edits) is a mismatch. Shapes verified against a captured
# `nft -j` fixture from the target nftables (1.1.3).

def _m_meta(key, right):
    return {"match": {"op": "==", "left": {"meta": {"key": key}}, "right": right}}


def _m_payload(proto, field, right):
    return {"match": {"op": "==",
                      "left": {"payload": {"protocol": proto, "field": field}},
                      "right": right}}


def _m_ct(right):
    op = "in"
    return {"match": {"op": op, "left": {"ct": {"key": "state"}}, "right": right}}


def _cidr_right(cidr):
    # Defensive: a bare address (no "/") is a host route — /32 for IPv4, /128 for IPv6. The
    # controller normalizes CIDRs before this, but a hand-authored candidate must not crash
    # the renderer (int("") ValueError).
    addr, sep, length = cidr.partition("/")
    if not sep:
        full = 32 if _cidr_family(addr) == "ipv4" else 128
        return {"prefix": {"addr": addr, "len": full}}
    return {"prefix": {"addr": addr, "len": int(length)}}


def _fam_ip(family):
    return "ip" if family == "ipv4" else "ip6"


def _scope_atoms(rule):
    """Family/address/source scoping atoms shared by allow and drop rules, in fixed order:
    nfproto (family-scoped wildcard) OR daddr (address-specific), then saddr CIDR. An
    ipv4-only scope never opens/covers IPv6 and vice versa; 'dual' emits no family atom.
    The wildcard BINDS `0.0.0.0` and `::` mean "any local address of that family" — they
    scope by nfproto, never by a literal daddr (which would match no real traffic)."""
    text = []
    exprs = []
    fam, addr = rule["family"], rule.get("addr", "*")
    saddr = rule.get("saddr")
    # nft canonicalizes away a `meta nfproto` atom made redundant by a same-family payload
    # match (ip/ip6 saddr|daddr already implies the family) — proven by live round-trip.
    # Emit the family atom only when nothing else carries the family, or the live listing
    # would never equal the expected one.
    saddr_fam = _cidr_fam(saddr) if saddr else None
    if addr in ("*", "0.0.0.0", "::"):
        if fam in ("ipv4", "ipv6") and saddr_fam != fam:
            text.append(f"meta nfproto {fam}")
            exprs.append(_m_meta("nfproto", fam))
    else:
        text.append(f"{_fam_ip(fam)} daddr {addr}")
        exprs.append(_m_payload(_fam_ip(fam), "daddr", addr))
    if saddr:
        text.append(f"{_fam_ip(fam if fam != 'dual' else _cidr_fam(saddr))} saddr {saddr}")
        exprs.append(_m_payload(_fam_ip(fam if fam != "dual" else _cidr_fam(saddr)),
                                "saddr", _cidr_right(saddr)))
    return text, exprs


def _cidr_fam(cidr):
    return _cidr_family(cidr) or "ipv4"


def _rule_spec(rule):
    """(nft_text, expected_expr_list, comment|None) for one model rule."""
    m = rule.get("match")
    act = rule["action"]
    verdict = [{act: None}]
    if m == "iif-lo":
        return 'iif "lo" accept', [_m_meta("iif", "lo"), *verdict], None
    if m == "ct-invalid":
        return "ct state invalid drop", [_m_ct("invalid"), *verdict], None
    if m == "ct-established-related":
        return ("ct state established,related accept",
                [_m_ct(["established", "related"]), *verdict], None)
    if m == "icmpv6":
        return ("meta l4proto ipv6-icmp accept",
                [_m_meta("l4proto", "ipv6-icmp"), *verdict], None)
    if m == "icmp":
        return ("ip protocol icmp accept",
                [_m_payload("ip", "protocol", "icmp"), *verdict], None)
    if m == "dhcpv4-client":
        # FAMILY-scoped: a DHCPv4 reply is IPv4 — the nfproto qualifier keeps this rule from
        # also matching IPv6 UDP 67->68 in the shared inet table.
        return ("meta nfproto ipv4 udp sport 67 udp dport 68 accept",
                [_m_meta("nfproto", "ipv4"), _m_payload("udp", "sport", 67),
                 _m_payload("udp", "dport", 68), *verdict], None)
    if m == "dhcpv6-client":
        return ("meta nfproto ipv6 udp sport 547 udp dport 546 accept",
                [_m_meta("nfproto", "ipv6"), _m_payload("udp", "sport", 547),
                 _m_payload("udp", "dport", 546), *verdict], None)
    if m == "mdns-v4":
        return ("ip daddr 224.0.0.251 udp dport 5353 accept",
                [_m_payload("ip", "daddr", "224.0.0.251"),
                 _m_payload("udp", "dport", 5353), *verdict], None)
    if m == "mdns-v6":
        return ("ip6 daddr ff02::fb udp dport 5353 accept",
                [_m_payload("ip6", "daddr", "ff02::fb"),
                 _m_payload("udp", "dport", 5353), *verdict], None)
    if m == "ap-dhcp-server":
        # DISCOVER arrives from 0.0.0.0 — interface-scoped, deliberately NO source CIDR, but an
        # explicit IPv4 family qualifier (DHCPv4 is IPv4).
        iif = rule["iif"]
        return (f'iifname "{iif}" meta nfproto ipv4 udp sport 68 udp dport 67 accept',
                [_m_meta("iifname", iif), _m_meta("nfproto", "ipv4"),
                 _m_payload("udp", "sport", 68), _m_payload("udp", "dport", 67), *verdict],
                None)
    if m == "ap-dns":
        iif, proto, saddr = rule["iif"], rule["proto"], rule["saddr"]
        return (f'iifname "{iif}" ip saddr {saddr} {proto} dport 53 accept',
                [_m_meta("iifname", iif),
                 _m_payload("ip", "saddr", _cidr_right(saddr)),
                 _m_payload(proto, "dport", 53), *verdict], None)
    # Scoped allow/drop on a concrete port.
    scope_text, scope_exprs = _scope_atoms(rule)
    text = " ".join([*scope_text, f"{rule['proto']} dport {rule['port']}", act])
    exprs = [*scope_exprs, _m_payload(rule["proto"], "dport", rule["port"]), *verdict]
    comment = None
    if rule.get("endpoint"):
        comment = f"lhpc-deny:{rule['endpoint']}" if act == "drop" else None
    elif rule.get("transition"):
        comment = "lhpc-transition"
    return text, exprs, comment


def render_nft_text(model) -> str:
    """The COMPLETE atomic transaction: destroy-if-present + full table block in one `nft -f`
    invocation (one netlink batch — the kernel applies all or nothing). Only OUR table is
    named; foreign tables are structurally untouchable from this text."""
    lines = [f"destroy table {model['family']} {model['table']}",
             f"table {model['family']} {model['table']} {{",
             f"\tcomment \"{model['comment']}\"",
             f"\tchain {model['chain']['name']} {{",
             f"\t\ttype {model['chain']['type']} hook {model['chain']['hook']} "
             f"priority {model['chain']['priority']}; policy {model['chain']['policy']};"]
    for rule in model["rules"]:
        text, _, comment = _rule_spec(rule)
        suffix = f' comment "{comment}"' if comment else ""
        lines.append(f"\t\t{text}{suffix}")
    lines += ["\t}", "}", ""]
    return "\n".join(lines)


def expected_listing(model) -> list:
    """The stripped `nft -j list table` output this model must produce: table, chain, rules
    in order — no handles, no metainfo."""
    fam, tab = model["family"], model["table"]
    out = [{"table": {"family": fam, "name": tab, "comment": model["comment"]}},
           {"chain": {"family": fam, "table": tab, "name": model["chain"]["name"],
                      "type": model["chain"]["type"], "hook": model["chain"]["hook"],
                      "prio": model["chain"]["priority"],
                      "policy": model["chain"]["policy"]}}]
    for rule in model["rules"]:
        _, exprs, comment = _rule_spec(rule)
        r = {"family": fam, "table": tab, "chain": model["chain"]["name"], "expr": exprs}
        if comment:
            r["comment"] = comment
        out.append({"rule": r})
    return out


def strip_volatile(listing) -> list:
    """Drop metainfo and every kernel-volatile field (handles, counters) from a parsed
    `nft -j` listing. Everything else is significant — unknown content must SURVIVE this
    strip so the comparison flags it."""
    out = []
    for entry in listing.get("nftables", []):
        if "metainfo" in entry:
            continue
        e = json.loads(json.dumps(entry))
        for body in e.values():
            if isinstance(body, dict):
                body.pop("handle", None)
                if isinstance(body.get("expr"), list):
                    body["expr"] = [x for x in body["expr"] if "counter" not in x]
        out.append(e)
    return out


def compare_live(live_json_text, model):
    """(verdict, detail) — verdict in: verified | mismatch | missing-table | not-owned |
    error. Structural equality against expected_listing(model); the ownership comment is
    part of the comparison (a live table without OUR comment is not-owned, never merely
    mismatched)."""
    try:
        live = json.loads(live_json_text)
    except (ValueError, TypeError):
        return "error", "live listing is not valid JSON"
    stripped = strip_volatile(live)
    tables = [e for e in stripped if "table" in e]
    if not tables:
        return "missing-table", "no inet lhpc table in live listing"
    if len(tables) != 1 or tables[0]["table"].get("name") != model["table"]:
        return "error", "unexpected table set in listing"
    if tables[0]["table"].get("comment") != model["comment"]:
        return "not-owned", "live table ownership comment does not match"
    expected = expected_listing(model)
    if stripped == expected:
        return "verified", ""
    # Name the first divergence for the receipt — bounded, never the whole listing.
    for i, (a, b) in enumerate(zip(expected, stripped, strict=False)):
        if a != b:
            return "mismatch", f"entry {i} diverges"
    return "mismatch", f"entry count differs (expected {len(expected)}, live {len(stripped)})"


# --- runtime layer ---------------------------------------------------------------------------
# Everything below touches the system and is therefore driven through the injectable `Sys`
# seam: tests exercise orchestration with a fake, root exercises it for real. The invariants
# implemented here are the plan's P1-C set: ONE flock held across every complete operation, a
# fsynced journal that the next invocation finishes-or-rolls-back deterministically, and a
# receipt that is written ONLY by this helper, atomically, with boot-id + CLOCK_BOOTTIME
# freshness evidence (wall-clock is carried for display, never for freshness).

import errno  # noqa: E402
import fcntl  # noqa: E402
import os  # noqa: E402
import stat  # noqa: E402
import subprocess  # noqa: E402
import tempfile  # noqa: E402
import time  # noqa: E402

ETC_DIR = "/etc/lhpc"
RUN_DIR = "/run/lhpc-firewall"
META_PATH = ETC_DIR + "/firewall.meta.json"
SNAPSHOT_PATH = ETC_DIR + "/firewall.snapshot.json"
TRANSITION_PATH = ETC_DIR + "/firewall.transition.json"
JOURNAL_PATH = ETC_DIR + "/firewall.journal.json"
LOCK_PATH = ETC_DIR + "/.firewall.lock"
RECEIPT_PATH = RUN_DIR + "/check.json"

MAX_CANDIDATE_BYTES = 256 * 1024
LOCK_WAIT_S = 10.0


class Sys:
    """Thin, injectable system seam. The real one shells out to nft/systemctl/sshd and reads
    /proc; tests replace it wholesale. No behavior lives here beyond bounded execution."""

    def run(self, argv, timeout=30.0, stdin_text=None):
        try:
            p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout,
                               input=stdin_text, check=False)
            return p.returncode, p.stdout, p.stderr
        except FileNotFoundError:
            return 127, "", f"{argv[0]}: not found"
        except subprocess.TimeoutExpired:
            return 124, "", f"{argv[0]}: timeout"

    def boot_id(self):
        try:
            with open("/proc/sys/kernel/random/boot_id") as f:
                return f.read().strip()
        except OSError:
            return ""

    def boottime(self):
        return time.clock_gettime(time.CLOCK_BOOTTIME)

    def walltime(self):
        return time.time()


def atomic_write(path, data, mode=0o644):
    """Root-owned temp in the SAME directory, fsync, rename, fsync the directory — the
    journal/receipt/snapshot durability contract (files AND directory entries)."""
    d = os.path.dirname(path)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".tmp-")
    try:
        os.write(fd, data.encode("utf-8"))
        os.fsync(fd)
        os.fchmod(fd, mode)
    finally:
        os.close(fd)
    os.replace(tmp, path)
    dfd = os.open(d, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(dfd)
    finally:
        os.close(dfd)


def read_bounded(path, max_bytes):
    """No-follow bounded read; (text, error). The candidate is untrusted DATA — a symlink,
    an oversized file or unreadable bytes are refusals, never surprises."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError as exc:
        return None, f"open: {exc.strerror}"
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            return None, "not a regular file"
        if st.st_size > max_bytes:
            return None, "file too large"
        return os.read(fd, max_bytes + 1).decode("utf-8", "strict"), None
    except (OSError, UnicodeDecodeError) as exc:
        return None, str(exc)
    finally:
        os.close(fd)


class OperationLock:
    """The single root operation lock (plan P1-C). O_NOFOLLOW; EX flock held for the entire
    operation. `wait=False` (checker) polls boundedly and signals busy instead of blocking a
    timer tick behind a long apply."""

    def __init__(self, path=LOCK_PATH):
        self._path = path
        self._fd = None

    def acquire(self, wait=True, timeout=LOCK_WAIT_S):
        os.makedirs(os.path.dirname(self._path), exist_ok=True)
        self._fd = os.open(self._path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        deadline = time.monotonic() + (timeout if not wait else 3600.0)
        while True:
            try:
                fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return True
            except OSError as exc:
                if exc.errno not in (errno.EAGAIN, errno.EACCES):
                    raise
                if time.monotonic() >= deadline:
                    return False
                time.sleep(0.2)

    def release(self):
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None


def append_log(run_dir, line):
    """Append one timestamped diagnostic line to run_dir/firewall.log — the console's GET-safe
    firewall log (the root units otherwise log only to root-owned journald). tmpfs, so it
    resets each boot; bounded to the last ~200 lines so a per-minute checker can't grow it."""
    path = run_dir + "/firewall.log"
    try:
        os.makedirs(run_dir, exist_ok=True)
        prev = ""
        try:
            with open(path) as f:
                prev = f.read()
        except OSError:
            pass
        lines = ([*prev.splitlines(), line])[-200:]
        atomic_write(path, "\n".join(lines) + "\n", 0o644)
    except OSError:
        pass                                           # logging is best-effort, never fatal


def write_receipt(sysx, verdict, detail, intent_h, model_h, foreign, transitional=False,
                  run_dir=RUN_DIR):
    """The ONLY receipt writer. Freshness evidence = boot_id + CLOCK_BOOTTIME; wall-clock is
    display-only. Atomic; 0644 so the unprivileged reader can read (and must then verify
    root ownership, mode and shape before trusting it)."""
    receipt = {
        "protocol": PROTOCOL_VERSION,
        "integration_rev": integration_rev(),      # the INSTALLED helper's own revision
        "verdict": verdict,
        "detail": detail,
        "intent_hash": intent_h,
        "model_hash": model_h,
        "boot_id": sysx.boot_id(),
        "boottime": sysx.boottime(),
        "walltime": sysx.walltime(),
        "transitional": bool(transitional),
        "foreign_tables": foreign,
    }
    os.makedirs(run_dir, exist_ok=True)
    atomic_write(run_dir + "/check.json", json.dumps(receipt, sort_keys=True), 0o644)
    fset = f" foreign={','.join(foreign)}" if foreign else ""
    tmark = " [transitional]" if transitional else ""
    append_log(run_dir, f"bt={receipt['boottime']:.0f} verdict={verdict}{tmark}"
                        f"{(' — ' + detail) if detail else ''}{fset}")
    return receipt


def list_foreign_tables(sysx):
    """Names of non-lhpc tables (evidence only — NEVER touched). An lhpc allow can never
    guarantee reachability while these exist: a later foreign base chain may still drop."""
    rc, out, _err = sysx.run(["nft", "-j", "list", "tables"])
    if rc != 0:
        return None
    try:
        entries = json.loads(out).get("nftables", [])
    except ValueError:
        return None
    names = []
    for e in entries:
        t = e.get("table")
        if t and not (t.get("family") == TABLE_FAMILY and t.get("name") == TABLE_NAME):
            names.append(f"{t.get('family', '?')}:{t.get('name', '?')}")
    return sorted(names)


def live_table_state(sysx, model_comment):
    """(state, listing_text): 'absent' | 'ours' | 'not-owned' | 'error'. Read BEFORE any
    replace/rollback/delete — root metadata alone never proves the live table is still ours;
    only the live ownership comment does."""
    rc, out, err = sysx.run(["nft", "-j", "list", "table", TABLE_FAMILY, TABLE_NAME])
    if rc != 0:
        if "No such file or directory" in err or "does not exist" in err:
            return "absent", ""
        return "error", err.strip()[:200]
    try:
        for e in json.loads(out).get("nftables", []):
            if "table" in e:
                if e["table"].get("comment") == model_comment:
                    return "ours", out
                return "not-owned", out
    except ValueError:
        return "error", "unparseable listing"
    return "error", "no table object in listing"


# --- authoritative SSH resolution (parsers pure; orchestration via Sys) ----------------------

def parse_sshd_t(output):
    """Effective ports from `sshd -T`: every `port N` line (Port is repeatable) plus
    port-qualified `listenaddress host:port` entries with full scope."""
    ports = set()
    scopes = []
    for line in output.splitlines():
        parts = line.strip().split()
        if len(parts) >= 2 and parts[0] == "port":
            try:
                ports.add(int(parts[1]))
            except ValueError:
                pass
        elif len(parts) >= 2 and parts[0] == "listenaddress":
            host, _, port = parts[1].rpartition(":")
            if host and port.isdigit():
                host = host.strip("[]")
                fam = _ip_family(host)
                if fam:
                    scopes.append({"proto": "tcp", "family": fam, "addr": host,
                                   "port": int(port)})
    return sorted(ports), scopes


def parse_unit_cmdline(execstart):
    """`-p PORT`, `-o Port=..` and `-f CONFIG` from the sshd unit command line."""
    ports = set()
    conf = None
    toks = (execstart or "").split()
    for i, t in enumerate(toks):
        if t == "-p" and i + 1 < len(toks) and toks[i + 1].isdigit():
            ports.add(int(toks[i + 1]))
        elif t.startswith("-p") and t[2:].isdigit():
            ports.add(int(t[2:]))
        elif t == "-o" and i + 1 < len(toks):
            k, _, v = toks[i + 1].partition("=")
            if k.lower() == "port" and v.isdigit():
                ports.add(int(v))
        elif t == "-f" and i + 1 < len(toks):
            conf = toks[i + 1]
    return sorted(ports), conf


def parse_socket_listen(listen_value):
    """Ports from ssh.socket ListenStream declarations ('[::]:2222', '0.0.0.0:22', '22')."""
    ports = set()
    for item in (listen_value or "").split():
        val = item.rsplit(":", 1)[-1] if ":" in item else item
        if val.isdigit():
            ports.add(int(val))
    return sorted(ports)


def resolve_ssh_scopes(sysx, override_ports):
    """(scopes, confident). Union of: explicit override (wildcard intent, authoritative),
    `sshd -T` effective config (honoring the unit's -f), unit -p/-o ports, socket-activation
    listeners, and root-observed ACTIVE sshd listen ports — a config transition can never cut
    recovery access. Port 22 is used ONLY when stock default behavior is positively
    established. `confident=False` → secure-default must abort (caller enforces)."""
    # A configured override is AUTHORITATIVE for intent, but the currently ACTIVE sshd listen
    # ports are ALWAYS unioned in — a config transition (e.g. override 22 while sshd still
    # listens on 2222) must never drop the live recovery connection.
    ports = set(override_ports or [])
    scopes = []
    confident = bool(override_ports)
    covered = set()                                        # ports with a PRECISE ListenAddress
    conf_file = None
    for unit in ("ssh.service", "sshd.service"):
        rc, out, _ = sysx.run(["systemctl", "show", unit, "-p", "ExecStart", "--value"])
        if rc == 0 and out.strip():
            uports, conf = parse_unit_cmdline(out)
            ports.update(uports)
            conf_file = conf_file or conf
    argv = ["sshd", "-T"] if not conf_file else ["sshd", "-T", "-f", conf_file]
    rc, out, _ = sysx.run(argv)
    if rc == 0 and out:
        tports, tscopes = parse_sshd_t(out)
        ports.update(tports)
        scopes.extend(tscopes)                             # precise ListenAddress scopes
        covered.update(s["port"] for s in tscopes)
        confident = True
    for unit in ("ssh.socket", "sshd.socket"):
        rc, out, _ = sysx.run(["systemctl", "show", unit, "-p", "Listen", "--value"])
        if rc == 0:
            sports = parse_socket_listen(out)
            if sports:
                ports.update(sports)
                confident = True
    active = active_sshd_ports(sysx)
    ports.update(active)
    if active:
        confident = True
    # Emit a dual-wildcard rule ONLY for ports WITHOUT a precise ListenAddress scope — a
    # precise scope must not be widened to every address by also emitting the wildcard.
    scopes = [{"proto": "tcp", "family": "dual", "addr": "*", "port": p}
              for p in sorted(ports) if p not in covered] + scopes
    return scopes, confident


def active_sshd_ports(sysx):
    """Root-observed listen ports of live sshd processes (ss is present on the target OS;
    output parsed defensively — an absent/odd ss yields the empty set, never a crash)."""
    rc, out, _ = sysx.run(["ss", "-tlnp"])
    if rc != 0:
        return set()
    ports = set()
    for line in out.splitlines():
        if '"sshd"' in line or "sshd" in line.split('"')[-1:] or "sshd" in line:
            for tok in line.split():
                if ":" in tok:
                    p = tok.rsplit(":", 1)[-1]
                    if p.isdigit():
                        ports.add(int(p))
                        break
    return ports


# --- journal + operations --------------------------------------------------------------------
# Exit codes (stable API for units, scripts and the controller's status interpretation):
EXIT_OK = 0
EXIT_FAIL = 1          # verification failed / live state wrong
EXIT_PROTOCOL = 65     # controller/helper protocol mismatch -> "setup/update required"
EXIT_CANDIDATE = 66    # candidate rejected (schema/bounds/refusals)
EXIT_NOT_OWNED = 67    # live inet lhpc table is not provably ours -> touch nothing
EXIT_BUSY = 75         # lock held by another operation (checker: receipt left untouched)
EXIT_INTERNAL = 70


def _paths(etc_dir, run_dir):
    return {
        "meta": etc_dir + "/firewall.meta.json",
        "snapshot": etc_dir + "/firewall.snapshot.json",
        "transition": etc_dir + "/firewall.transition.json",
        "journal": etc_dir + "/firewall.journal.json",
        "lock": etc_dir + "/.firewall.lock",
        "receipt": run_dir + "/check.json",
    }


def _read_json(path):
    text, err = read_bounded(path, MAX_CANDIDATE_BYTES)
    if err:
        return None
    try:
        return json.loads(text)
    except ValueError:
        return None


def _read_json_state(path):
    """TRI-STATE privileged read: ('absent', None) | ('valid', value) | ('present-invalid', None).
    'present-invalid' = the file EXISTS but is unreadable / a symlink / oversized / not JSON — an
    interrupted or tampered privileged state that must NEVER be treated as 'absent'. Mutating
    callers (apply/reset/recovery) fail closed with zero writes on 'present-invalid'; read-only
    callers report unverifiable."""
    text, err = read_bounded(path, MAX_CANDIDATE_BYTES)
    if err:
        return ("absent", None) if not os.path.lexists(path) else ("present-invalid", None)
    try:
        return ("valid", json.loads(text))
    except ValueError:
        return ("present-invalid", None)


def _durable_unlink(path):
    """Unlink a file, then fsync its parent directory so the removal survives power loss —
    the delete side of atomic_write's durability contract. Absent file: nothing to do."""
    try:
        os.unlink(path)
    except FileNotFoundError:
        return
    except OSError:
        return
    try:
        dfd = os.open(os.path.dirname(path), os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)
    except OSError:
        pass


def _sha256_text(s):
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def _sha256_file(path):
    try:
        with open(path, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()
    except OSError:
        return ""


def _fsync_dir(path):
    try:
        dfd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)
    except OSError:
        pass


def recover(sysx, p):
    """Deterministic finish-or-rollback of an interrupted apply/reset BEFORE any load/check
    (plan P1-C, P2-1). The journal binds the transaction to txid + the OLD-canonical and
    NEW-staged snapshot hashes; the new snapshot is written to a STAGING name and promoted to
    canonical by an atomic rename only after a verified live load. Recovery reads the ACTUAL
    on-disk hashes to decide, so the begin+new-snapshot window is unambiguous.

    FAIL CLOSED on any unknown / incomplete / inconsistent journal shape — a truncated or
    unrecognised journal must NEVER read as 'all clear'."""
    jstate, j = _read_json_state(p["journal"])
    if jstate == "absent":
        return True                        # nothing to recover
    if jstate == "present-invalid" or not isinstance(j, dict):
        return False                       # interrupted op, undecodable -> fail closed
    op = j.get("op")
    if op == "apply":
        return _recover_apply(sysx, p, j)
    if op == "reset":
        _finish_reset(sysx, p, j)
        return True
    return False                           # unknown op -> fail closed


def _recover_apply(sysx, p, j):
    phase = j.get("phase")
    staged = j.get("staged")
    new_hash = j.get("new_hash")
    if phase == "begin":
        # No promotion happened; discard any partial staging and keep the OLD canonical snapshot.
        if staged:
            _durable_unlink(staged)
        _durable_unlink(p["journal"])
        return True
    if phase == "snapshot-staged":
        canon_hash = _sha256_file(p["snapshot"])
        if new_hash and canon_hash == new_hash:
            # ALREADY PROMOTED (crash after the atomic rename, before journal removal) -> finish
            # forward: ensure the live table matches the (now canonical) new snapshot, drop journal.
            cstate, canon = _read_json_state(p["snapshot"])
            if cstate == "valid":
                _load_snapshot_live(sysx, canon)
            if staged:
                _durable_unlink(staged)
            _durable_unlink(p["journal"])
            return True
        # NOT promoted -> the canonical snapshot is still the OLD one. Roll back: discard staging,
        # reload the old ruleset (or tear down a first-install table that has no old snapshot).
        if staged:
            _durable_unlink(staged)
        old = j.get("old_snapshot")
        if old:
            _load_snapshot_live(sysx, old)
        else:
            mstate, meta = _read_json_state(p["meta"])
            if mstate == "valid" and (meta or {}).get("ownership_id"):
                st, _ = live_table_state(sysx, f"lhpc-owned:{meta['ownership_id']}")
                if st == "ours":
                    sysx.run(["nft", "destroy", TABLE_FAMILY, TABLE_NAME])
        _durable_unlink(p["journal"])
        return True
    return False                           # unknown phase -> fail closed


def _load_snapshot_live(sysx, snap):
    # OWNERSHIP GUARD: our nft_text begins `destroy table inet lhpc`, which would destroy ANY
    # inet lhpc table — including a FOREIGN one that replaced ours during a crash window. Never
    # load (never destroy) unless the live table is absent or provably ours. This makes both
    # the boot loader and crash recovery safe against a foreign replacement.
    state, _ = live_table_state(sysx, snap["model"]["comment"])
    if state == "not-owned":
        return "not-owned"
    if state == "error":
        return "error"
    rc, _out, _err = sysx.run(["nft", "-f", "-"], stdin_text=snap["nft_text"])
    if rc != 0:
        return "load-failed"
    verdict, _detail = _verify_snapshot_live(sysx, snap)
    return verdict


def _verify_snapshot_live(sysx, snap):
    rc, out, err = sysx.run(["nft", "-j", "list", "table", TABLE_FAMILY, TABLE_NAME])
    if rc != 0:
        return "missing-table", err.strip()[:200]
    return compare_live(out, snap["model"])


def _finish_reset(sysx, p, j):
    """Ownership-PROVEN teardown. The metadata (the ownership record) is deleted ONLY after the
    owned table is proven gone — never while a table that might be ours (or an unknown/foreign
    one) still exists, so a boot-persistent firewall is never orphaned and a foreign table is
    never silently abandoned as 'reset'. Returns True iff the table was cleared."""
    meta = j.get("meta") or _read_json(p["meta"]) or {}
    comment = f"lhpc-owned:{meta.get('ownership_id', '')}"
    state, _ = live_table_state(sysx, comment)
    cleared = state == "absent"
    if state == "ours":
        sysx.run(["nft", "destroy", "table", TABLE_FAMILY, TABLE_NAME])
        after, _ = live_table_state(sysx, comment)         # PROVE it is gone
        cleared = after == "absent"
    # state in ('not-owned', 'error'): do NOT destroy and do NOT delete the ownership record.
    if cleared:
        for key in ("snapshot", "transition", "meta", "receipt"):
            _durable_unlink(p[key])
    _durable_unlink(p["journal"])                          # the op is resolved either way
    return cleared


def op_check(sysx, *, etc_dir=ETC_DIR, run_dir=RUN_DIR):
    """Periodic/immediate verification. On a busy lock the existing receipt is deliberately
    LEFT UNTOUCHED (never publish evidence derived from half of a mutation; the freshness
    window ages the old receipt honestly) and EXIT_BUSY is returned."""
    p = _paths(etc_dir, run_dir)
    lock = OperationLock(p["lock"])
    if not lock.acquire(wait=False):
        return EXIT_BUSY
    try:
        if not recover(sysx, p):
            return EXIT_INTERNAL
        sstate, snap = _read_json_state(p["snapshot"])
        if sstate != "valid":
            # Read-only status: report an EXPLICIT unverifiable result (never green, never blank).
            detail = ("no accepted snapshot" if sstate == "absent"
                      else "accepted snapshot present but unreadable")
            write_receipt(sysx, "error", detail, "", "",
                          list_foreign_tables(sysx) or [], run_dir=run_dir)
            return EXIT_FAIL
        if snap.get("protocol") != PROTOCOL_VERSION:
            write_receipt(sysx, "error", "helper/model protocol mismatch — setup/update "
                          "required", "", "", [], run_dir=run_dir)
            return EXIT_PROTOCOL
        verdict, detail = _verify_snapshot_live(sysx, snap)
        write_receipt(sysx, verdict, detail, snap["intent_hash"], snap["model_hash"],
                      list_foreign_tables(sysx) or [],
                      transitional=snap.get("transitional", False), run_dir=run_dir)
        return EXIT_OK if verdict == "verified" else EXIT_FAIL
    finally:
        lock.release()


def op_load(sysx, *, etc_dir=ETC_DIR, run_dir=RUN_DIR):
    """Boot loader body. Succeeds ONLY after live readback verified and the receipt is
    written — a partial load is a unit failure, and the exposure gate then keeps lhpc's
    remote listeners closed."""
    p = _paths(etc_dir, run_dir)
    lock = OperationLock(p["lock"])
    if not lock.acquire(wait=True):
        return EXIT_BUSY
    try:
        if not recover(sysx, p):
            return EXIT_INTERNAL
        sstate, snap = _read_json_state(p["snapshot"])
        if sstate != "valid":
            return EXIT_FAIL            # absent or corrupt -> fail closed (boot gate keeps remote shut)
        if snap.get("protocol") != PROTOCOL_VERSION:
            return EXIT_PROTOCOL
        comment = snap["model"]["comment"]
        state, _ = live_table_state(sysx, comment)
        if state == "not-owned":
            write_receipt(sysx, "not-owned", "live table is not lhpc's — refusing",
                          snap["intent_hash"], snap["model_hash"],
                          list_foreign_tables(sysx) or [], run_dir=run_dir)
            return EXIT_NOT_OWNED
        verdict = _load_snapshot_live(sysx, snap)
        write_receipt(sysx, verdict if verdict in ("verified", "mismatch", "missing-table",
                                                   "not-owned") else "error",
                      "" if verdict == "verified" else f"boot load: {verdict}",
                      snap["intent_hash"], snap["model_hash"],
                      list_foreign_tables(sysx) or [],
                      transitional=snap.get("transitional", False), run_dir=run_dir)
        return EXIT_OK if verdict == "verified" else EXIT_FAIL
    finally:
        lock.release()


def _endpoint_scope(ep):
    return {"proto": ep["proto"], "family": ep["family"], "addr": ep["addr"],
            "port": ep["port"], "allow_cidrs": list(ep.get("allow_cidrs", []))}


def _transition_sets(old_cand, new_cand):
    """Preservation across desired-vs-effective transitions (plan 6b/P1-A): previously
    applied UNSELECTED scopes stay dropped until cleanup; previously applied proxy ingress
    stays allowed so applying the firewall never cuts current web access mid-migration."""
    if not old_cand:
        return [], []
    new_unsel = {(e["proto"], e["family"], e["addr"], e["port"])
                 for e in new_cand["endpoints"] if not e["selected"]}
    t_drop = [_endpoint_scope(e) for e in old_cand["endpoints"]
              if not e["selected"]
              and (e["proto"], e["family"], e["addr"], e["port"]) not in new_unsel]
    new_ing = {(e["proto"], e["family"], e["addr"], e["port"])
               for e in new_cand["proxy_ingress"]}
    t_allow = [dict(e) for e in old_cand["proxy_ingress"]
               if (e["proto"], e["family"], e["addr"], e["port"]) not in new_ing]
    return t_allow, t_drop


def op_apply(sysx, candidate_path, *, cleanup=False, etc_dir=ETC_DIR, run_dir=RUN_DIR):
    """Full apply: validate candidate (data only) -> ownership/collision checks -> resolve
    SSH -> resolve model (with transition preservation unless --cleanup) -> journaled
    snapshot commit -> atomic live transaction -> readback -> receipt. Every durable step is
    fsynced; an interruption at any point is finished or rolled back by `recover()`."""
    p = _paths(etc_dir, run_dir)
    lock = OperationLock(p["lock"])
    if not lock.acquire(wait=True):
        return EXIT_BUSY
    try:
        if not recover(sysx, p):
            return EXIT_INTERNAL
        text, err = read_bounded(candidate_path, MAX_CANDIDATE_BYTES)
        if err:
            print(f"candidate: {err}")
            return EXIT_CANDIDATE
        try:
            cand = json.loads(text)
        except ValueError:
            print("candidate: not valid JSON")
            return EXIT_CANDIDATE
        errors = validate_candidate(cand)
        if errors:
            print("candidate rejected:")
            for e in errors[:10]:
                print(f"  {e}")
            return EXIT_CANDIDATE

        mstate, meta = _read_json_state(p["meta"])
        if mstate == "present-invalid":
            # Corrupt ownership metadata: NEVER mint a fresh id over it (that would strand the
            # existing owned table). Fail closed with zero writes.
            print("ownership metadata is present but unreadable — refusing (fail closed)")
            return EXIT_INTERNAL
        if mstate == "absent":
            # Mint a new ownership id ONLY after proving the live table is ALSO absent — otherwise
            # a pre-existing owned/foreign inet lhpc table would be orphaned or clobbered.
            st0, _ = live_table_state(sysx, "lhpc-owned:")     # empty id matches no real table
            if st0 == "error":
                print("could not inspect the live table — refusing (fail closed)")
                return EXIT_INTERNAL
            if st0 != "absent":
                print("a table inet lhpc exists but there is no ownership metadata — refusing")
                return EXIT_NOT_OWNED
            meta = {"protocol": PROTOCOL_VERSION, "ownership_id": os.urandom(16).hex()}
            os.makedirs(etc_dir, exist_ok=True)
            atomic_write(p["meta"], json.dumps(meta, sort_keys=True), 0o644)
        if meta.get("protocol") != PROTOCOL_VERSION:
            print("helper/model protocol mismatch — setup/update required")
            return EXIT_PROTOCOL
        comment = f"lhpc-owned:{meta['ownership_id']}"
        state, _listing = live_table_state(sysx, comment)
        if state == "not-owned":
            print("a table inet lhpc exists but is NOT provably lhpc's — refusing; "
                  "remove or rename it yourself if it is yours")
            return EXIT_NOT_OWNED

        override = cand.get("ssh_ports") or []
        ssh_scopes, confident = resolve_ssh_scopes(sysx, override)
        if cand["mode"] == "secure-default" and not confident:
            print("effective SSH access could not be resolved confidently — aborting "
                  "before changing the live table. Set [firewall] ssh_ports explicitly.")
            return EXIT_CANDIDATE

        sstate, old_snap = _read_json_state(p["snapshot"])
        if sstate == "present-invalid":
            # A CORRUPT accepted snapshot reads as "no previous snapshot" (old_snap None), so a
            # failed apply would take the first-install branch and DESTROY the existing owned table
            # instead of restoring the ruleset it was supposed to hold. Refuse at entry (zero
            # writes) — never trade restorable state for a teardown.
            print("accepted snapshot is present but unreadable — refusing apply (fail closed)")
            return EXIT_INTERNAL
        if state == "ours" and sstate == "absent":
            # An LHPC-OWNED live table with NO accepted snapshot is an inconsistent state (a prior
            # install whose snapshot was lost). Proceeding would first-install-TEARDOWN the live
            # owned table on any verify failure. Refuse before ANY mutation — reset first.
            print("an owned inet lhpc table is live but its accepted snapshot is missing — "
                  "refusing apply (reset first)")
            return EXIT_INTERNAL
        t_allow, t_drop = ([], []) if cleanup else _transition_sets(
            (old_snap or {}).get("candidate"), cand)
        transitional = bool(t_allow or t_drop)
        model = resolve_model(cand, ownership_id=meta["ownership_id"],
                              ssh_scopes=ssh_scopes,
                              transition_allow=t_allow, transition_drop=t_drop)
        nft_text = render_nft_text(model)
        rc, _out, err = sysx.run(["nft", "-c", "-f", "-"], stdin_text=nft_text)
        if rc != 0:
            print(f"nft refused the candidate transaction: {err.strip()[:300]}")
            return EXIT_FAIL

        snap = {"protocol": PROTOCOL_VERSION, "candidate": cand,
                "intent_hash": _intent_hash_of(cand), "model": model,
                "model_hash": model_hash(model), "nft_text": nft_text,
                "transitional": transitional, "integration_rev": integration_rev()}
        # P2-1 staged-snapshot transaction (bounded, no general framework): begin -> write the new
        # snapshot to a STAGING name -> load+verify live -> atomic-rename promote -> journal removed.
        # The canonical snapshot is NEVER overwritten until a verified load, so a crash at any point
        # is deterministically finished/rolled back by recover() using the recorded hashes.
        snap_json = json.dumps(snap, sort_keys=True)
        txid = os.urandom(8).hex()
        staged = p["snapshot"] + ".staging-" + txid
        new_hash = _sha256_text(snap_json)
        old_hash = _sha256_file(p["snapshot"]) if sstate == "valid" else ""
        jrec = {"op": "apply", "txid": txid, "old_snapshot": old_snap,
                "old_hash": old_hash, "new_hash": new_hash, "staged": staged}
        atomic_write(p["journal"], json.dumps({**jrec, "phase": "begin"}, sort_keys=True), 0o600)
        atomic_write(staged, snap_json, 0o644)
        atomic_write(p["journal"], json.dumps({**jrec, "phase": "snapshot-staged"},
                                              sort_keys=True), 0o600)

        verdict = _load_snapshot_live(sysx, snap)
        if verdict != "verified":
            # NOT promoted — the canonical snapshot is still the OLD one. Discard staging, restore
            # the previous ruleset, OR tear down a first-install table (no old snapshot to restore).
            # Foreign state is untouched (the load guard refuses a foreign table).
            _durable_unlink(staged)
            if old_snap:
                _load_snapshot_live(sysx, old_snap)
            else:
                st, _ = live_table_state(sysx, snap["model"]["comment"])
                if st == "ours":
                    sysx.run(["nft", "destroy", "table", TABLE_FAMILY, TABLE_NAME])
            _durable_unlink(p["journal"])
            write_receipt(sysx, "error", f"apply failed at {verdict}; previous ruleset "
                          "restored", snap["intent_hash"], snap["model_hash"],
                          list_foreign_tables(sysx) or [], run_dir=run_dir)
            return EXIT_FAIL
        # PROMOTE: atomic rename staged -> canonical, then fsync the directory.
        os.replace(staged, p["snapshot"])
        _fsync_dir(etc_dir)
        if transitional:
            atomic_write(p["transition"], json.dumps({
                "txid": txid,
                "source_model_hash": (old_snap or {}).get("model_hash", ""),
                "intent_hash": snap["intent_hash"],
                "transitional_model_hash": snap["model_hash"],
                "old_allow": t_allow, "old_drop": t_drop,
                "cleanup": "apply --cleanup"}, sort_keys=True), 0o644)
        elif os.path.exists(p["transition"]):
            _durable_unlink(p["transition"])
        _durable_unlink(p["journal"])
        write_receipt(sysx, "verified", "", snap["intent_hash"], snap["model_hash"],
                      list_foreign_tables(sysx) or [], transitional=transitional,
                      run_dir=run_dir)
        print("applied and live-verified" + (" (TRANSITIONAL — run the cleanup apply "
              "after the listener move completes)" if transitional else ""))
        return EXIT_OK
    finally:
        lock.release()


def _intent_hash_of(cand):
    """Local canonical intent hash — same algorithm as the controller's (drift-tested)."""
    c = json.loads(json.dumps(cand))
    for ep in c["endpoints"]:
        ep["band"] = ep.get("band", "")                    # normalize the optional band dimension
        ep["allow_cidrs"] = sorted(ep["allow_cidrs"])
    c["endpoints"] = sorted(c["endpoints"], key=lambda e: (e["id"], e["band"]))
    c["proxy_ingress"] = sorted(
        c["proxy_ingress"], key=lambda e: (e["proto"], e["family"], e["addr"], e["port"]))
    for e in c["proxy_ingress"]:
        e["allow_cidrs"] = sorted(e["allow_cidrs"])
    c["ssh_ports"] = sorted(set(c["ssh_ports"]))
    c["extra_allow"] = sorted(
        c["extra_allow"],
        key=lambda e: (e["proto"], e["family"], e["addr"], e["port"], e["cidr"]))
    return hashlib.sha256(
        json.dumps(c, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def op_reset(sysx, *, etc_dir=ETC_DIR, run_dir=RUN_DIR):
    """Ownership-proven removal of lhpc's firewall data and table. Unit files and the helper
    binary are removed by the reset SCRIPT afterwards; this operation never touches foreign
    configuration and refuses an unowned table."""
    p = _paths(etc_dir, run_dir)
    lock = OperationLock(p["lock"])
    if not lock.acquire(wait=True):
        return EXIT_BUSY
    try:
        if not recover(sysx, p):
            return EXIT_INTERNAL
        mstate, meta = _read_json_state(p["meta"])
        if mstate == "present-invalid":
            print("ownership metadata is present but unreadable — refusing reset (fail closed)")
            return EXIT_INTERNAL
        # Ownership check covers the missing-metadata case too: without a metadata ownership id
        # the comment can't match any real table, so a live inet lhpc table reads 'not-owned'
        # and reset REFUSES rather than deleting state that might front a foreign table.
        comment = f"lhpc-owned:{(meta or {}).get('ownership_id', '')}"
        state, _ = live_table_state(sysx, comment)
        if state == "not-owned":
            print("live table inet lhpc is NOT provably lhpc's — refusing to delete it")
            return EXIT_NOT_OWNED
        if state == "error":
            print("could not inspect the live table — refusing reset (fail closed)")
            return EXIT_INTERNAL
        atomic_write(p["journal"], json.dumps(
            {"op": "reset", "phase": "begin", "meta": meta}, sort_keys=True), 0o600)
        cleared = _finish_reset(sysx, p, {"meta": meta})
        if not cleared:
            print("could not prove the owned table was removed — ownership metadata kept")
            return EXIT_FAIL
        print("lhpc firewall data and table removed")
        return EXIT_OK
    finally:
        lock.release()


def main(argv):
    if os.geteuid() != 0:
        print("must run as root (use the printed sudo command)")
        return EXIT_INTERNAL
    sysx = Sys()
    if len(argv) >= 1 and argv[0] == "check":
        return op_check(sysx)
    if len(argv) >= 1 and argv[0] == "load":
        return op_load(sysx)
    if len(argv) >= 2 and argv[0] == "apply":
        return op_apply(sysx, argv[1], cleanup="--cleanup" in argv[2:])
    if len(argv) >= 1 and argv[0] == "reset":
        return op_reset(sysx)
    print("usage: firewall-helper load|check|apply <candidate.json> [--cleanup]|reset")
    return EXIT_INTERNAL


if __name__ == "__main__":
    import sys
    raise SystemExit(main(sys.argv[1:]))
