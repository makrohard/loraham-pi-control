"""Managed Firewall — pure model layer.

TWO-HASH TRUTH MODEL. Three states exist and one hash cannot represent them:
the operator's firewall-relevant INTENT (derived from lhpc config, unprivileged), the
root-resolved ACCEPTED MODEL of the `inet lhpc` table (adds the persisted installation
ownership ID, authoritative SSH scopes and transition-preservation scopes — root side), and
the LIVE table in the kernel. This module owns the UNPRIVILEGED half: the strict, versioned
CANDIDATE the controller renders and the root helper consumes as *data only*, plus its
canonical `intent_hash`. The candidate deliberately CANNOT express nftables structure —
ownership ID, table/chain names, hooks, priorities, policies or rule ordering are unknown
fields here and are injected exclusively by the root-owned helper. A matching `intent_hash`
never proves live rules match; a matching live table never proves it corresponds to the
current saved intent — the receipt carries both hashes so each side checks its own.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re

# Version of the CANDIDATE data schema (what the controller writes and the helper parses).
CANDIDATE_SCHEMA = 1
# Version of the controller<->helper protocol (candidate schema + receipt + snapshot shapes).
# A controller/helper mismatch is surfaced as "setup/update required" and fails closed.
# Bumped 1->2: the persisted receipt/snapshot contract changed (integration_rev added, band
# scope-key dimension) — an old-protocol snapshot/receipt is rejected -> forces a fresh apply.
PROTOCOL_VERSION = 2

# Live receipt freshness window (seconds, CLOCK_BOOTTIME). ~3 checker periods (60s): a couple
# of missed ticks is tolerated, a stale/absent checker is not (the receipt ages out -> Live ✗).
FRESH_WINDOW_S = 200

# Bounded wait (seconds) the nginx boot gate gives the firewall loader to produce a valid
# current-boot receipt before falling back to a loopback-only listener.
BOOT_GATE_WAIT_S = 20

# Bounded collections: the helper treats the candidate as untrusted data, so every list the
# unprivileged side controls has a hard cap (schema-rejected above it, never truncated).
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
# `band` is an OPTIONAL scope-key dimension (default "" = band-agnostic / single-band). It lets two
# genuinely-diverging per-band scopes of the SAME stable endpoint id coexist without merging.
_EP_KEYS = ("id", "proto", "family", "addr", "port", "allow_cidrs", "selected", "deny_default",
            "auth", "band")
_INGRESS_KEYS = ("proto", "family", "addr", "port", "allow_cidrs")
_EXTRA_KEYS = ("proto", "family", "addr", "port", "cidr")
_AP_KEYS = ("enabled", "interface", "cidr")


# --- strict primitive checks (bool is NOT int, int is NOT bool) ------------------------------

def _is_bool(v) -> bool:
    return type(v) is bool


def _is_int(v) -> bool:
    return type(v) is int and type(v) is not bool


def _is_str(v) -> bool:
    return type(v) is str


def _port_ok(v) -> bool:
    return _is_int(v) and 1 <= v <= 65535


def _family_of_ip(text: str) -> str | None:
    try:
        ip = ipaddress.ip_address(text)
    except ValueError:
        return None
    return "ipv4" if ip.version == 4 else "ipv6"


def _family_of_cidr(text: str) -> str | None:
    try:
        net = ipaddress.ip_network(text)
    except ValueError:
        return None
    return "ipv4" if net.version == 4 else "ipv6"


def _keys_errors(obj: dict, allowed: tuple, path: str, errors: list) -> None:
    """Exact key-set discipline: every missing key and every unknown field is an error.
    Unknown-field rejection is the security property that keeps nftables structure
    (ownership id, table/chain names, hooks, priorities, policies, rule order) out of the
    unprivileged candidate — the helper injects those, never accepts them."""
    for k in allowed:
        if k not in obj:
            errors.append(f"{path}: missing '{k}'")
    for k in obj:
        if k not in allowed:
            errors.append(f"{path}: unknown field '{k}'")


def _scope_errors(entry: dict, path: str, errors: list) -> None:
    """Shared proto/family/addr/port validation for endpoint-like scopes."""
    if entry.get("proto") not in _PROTOS:
        errors.append(f"{path}: proto must be one of {_PROTOS}")
    fam = entry.get("family")
    if fam not in _FAMILIES:
        errors.append(f"{path}: family must be one of {_FAMILIES}")
    addr = entry.get("addr")
    if not _is_str(addr):
        errors.append(f"{path}: addr must be a string")
    elif addr == "*":
        pass                                    # wildcard within the declared family
    elif fam == "dual":
        errors.append(f"{path}: family 'dual' requires addr '*'")
    elif fam in ("ipv4", "ipv6") and _family_of_ip(addr) != fam:
        errors.append(f"{path}: addr '{addr}' does not match family '{fam}'")
    if not _port_ok(entry.get("port")):
        errors.append(f"{path}: port must be an integer 1..65535")


def _cidr_list_errors(cidrs, fam: str, path: str, errors: list) -> None:
    if type(cidrs) is not list:
        errors.append(f"{path}: must be a list")
        return
    if len(cidrs) > MAX_CIDRS:
        errors.append(f"{path}: too many entries (max {MAX_CIDRS})")
        return
    for i, c in enumerate(cidrs):
        if not _is_str(c) or (cf := _family_of_cidr(c)) is None:
            errors.append(f"{path}[{i}]: invalid CIDR")
        elif fam in ("ipv4", "ipv6") and cf != fam:
            errors.append(f"{path}[{i}]: CIDR family does not match '{fam}'")


def _scopes_overlap(a: dict, b: dict) -> bool:
    """Full-scope overlap (proto + family + address + port) — NEVER the bare port number.
    Wildcard addresses overlap anything in an overlapping family; 'dual' overlaps both.

    SYMMETRIC, and deliberately permissive: it answers "could these two touch?", which is the
    right question when REFUSING an extra_allow that grazes a deny scope. It is the WRONG
    question for proving protection — see `scope_covers` below."""
    if a.get("proto") != b.get("proto") or a.get("port") != b.get("port"):
        return False
    fa, fb = a.get("family"), b.get("family")
    if fa != fb and "dual" not in (fa, fb):
        return False
    aa, ab = a.get("addr"), b.get("addr")
    return aa == "*" or ab == "*" or aa == ab


def scope_covers(protector: dict, listener: dict) -> bool:
    """Does `protector` cover the ENTIRE `listener` scope? DIRECTIONAL — coverage, not overlap.

    `_scopes_overlap` must never be used for this: it treats ipv4-vs-dual and
    concrete-vs-wildcard as a match, so an IPv4-only rule would "prove" a dual listener safe and
    a rule for one address would "prove" a wildcard listener safe. Both are false negatives for
    reachability, i.e. a green badge on a port that is still reachable.

    Coverage requires: same proto, same port, the protector's family to CONTAIN the listener's,
    and the protector's address to contain the listener's (a wildcard protector covers a concrete
    listener; a concrete protector never covers a wildcard one).

    IPv6 wildcard caveat: `tcp_listeners()` reads /proc/net/tcp and /proc/net/tcp6 and tags each
    record ipv4 or ipv6 — never dual. A socket bound `::` with the default bindv6only=0 also
    accepts IPv4 while appearing only as one ipv6 record, so an ipv6 protector would wrongly
    cover it. A wildcard IPv6 listener therefore demands a DUAL protector."""
    if protector.get("proto") != listener.get("proto"):
        return False
    if protector.get("port") != listener.get("port"):
        return False
    pf, lf = protector.get("family"), listener.get("family")
    la = listener.get("addr") or "*"
    if lf == "ipv6" and la == "*":
        # `::` accepts IPv4 too; only dual protection covers both halves.
        if pf != "dual":
            return False
    elif pf != "dual" and pf != lf:
        return False
    # Same normalisation the siblings use (_scopes_overlap / firewall_helper): a missing addr
    # means the wildcard, not "matches nothing".
    pa = protector.get("addr") or "*"
    return pa == "*" or pa == la


# --- validation ------------------------------------------------------------------------------

def validate_candidate(cand) -> list[str]:
    """Validate a candidate intent strictly; returns a list of errors ([] = valid).
    Fail-closed philosophy: anything unknown, out of range, mistyped or over-cap is an
    error — the root helper independently re-validates the same schema and refuses too."""
    errors: list[str] = []
    if type(cand) is not dict:
        return ["candidate: must be an object"]
    _keys_errors(cand, _TOP_KEYS, "candidate", errors)

    if cand.get("schema") != CANDIDATE_SCHEMA:
        errors.append(f"candidate: schema must be {CANDIDATE_SCHEMA}")
    if cand.get("mode") not in _MODES:
        errors.append(f"candidate: mode must be one of {_MODES}")

    deny_scopes: list[dict] = []
    eps = cand.get("endpoints")
    if type(eps) is not list:
        errors.append("endpoints: must be a list")
    elif len(eps) > MAX_ENDPOINTS:
        errors.append(f"endpoints: too many entries (max {MAX_ENDPOINTS})")
    else:
        seen_keys: set = set()
        for i, ep in enumerate(eps):
            path = f"endpoints[{i}]"
            if type(ep) is not dict:
                errors.append(f"{path}: must be an object")
                continue
            _keys_errors(ep, _EP_KEYS, path, errors)
            eid = ep.get("id")
            band = ep.get("band", "")
            if not _is_str(band):
                errors.append(f"{path}: band must be a string")
                band = ""
            if not _is_str(eid) or not _ID_RE.match(eid or ""):
                errors.append(f"{path}: invalid id")
            elif (eid, band) in seen_keys:
                errors.append(f"{path}: duplicate id '{eid}' (band {band!r})")
            else:
                seen_keys.add((eid, band))
            _scope_errors(ep, path, errors)
            _cidr_list_errors(ep.get("allow_cidrs"), ep.get("family"),
                              f"{path}.allow_cidrs", errors)
            for flag in ("selected", "deny_default"):
                if not _is_bool(ep.get(flag)):
                    errors.append(f"{path}: {flag} must be a boolean")
            if ep.get("auth") not in ("none", "password", "mtls", "token"):
                errors.append(f"{path}: auth must be one of none|password|mtls|token")
            if ep.get("deny_default") is True:
                deny_scopes.append(ep)

    ing = cand.get("proxy_ingress")
    if type(ing) is not list:
        errors.append("proxy_ingress: must be a list")
    elif len(ing) > MAX_PROXY_INGRESS:
        errors.append(f"proxy_ingress: too many entries (max {MAX_PROXY_INGRESS})")
    else:
        for i, e in enumerate(ing):
            path = f"proxy_ingress[{i}]"
            if type(e) is not dict:
                errors.append(f"{path}: must be an object")
                continue
            _keys_errors(e, _INGRESS_KEYS, path, errors)
            _scope_errors(e, path, errors)
            _cidr_list_errors(e.get("allow_cidrs"), e.get("family"),
                              f"{path}.allow_cidrs", errors)

    ssh = cand.get("ssh_ports")
    if type(ssh) is not list:
        errors.append("ssh_ports: must be a list")
    elif len(ssh) > MAX_SSH_PORTS:
        errors.append(f"ssh_ports: too many entries (max {MAX_SSH_PORTS})")
    else:
        for i, p in enumerate(ssh):
            if not _port_ok(p):
                errors.append(f"ssh_ports[{i}]: must be an integer 1..65535")

    ap = cand.get("ap")
    if type(ap) is not dict:
        errors.append("ap: must be an object")
    else:
        _keys_errors(ap, _AP_KEYS, "ap", errors)
        if not _is_bool(ap.get("enabled")):
            errors.append("ap: enabled must be a boolean")
        if not _is_str(ap.get("interface")) or not _is_str(ap.get("cidr")):
            errors.append("ap: interface and cidr must be strings")
        elif ap.get("enabled") is True:
            # AP is strictly opt-in AND strictly explicit: no interface/CIDR defaults.
            if not _IFACE_RE.match(ap["interface"] or ""):
                errors.append("ap: enabled requires a valid interface name")
            if _family_of_cidr(ap["cidr"] or "") != "ipv4":
                errors.append("ap: enabled requires a valid IPv4 cidr")

    extra = cand.get("extra_allow")
    if type(extra) is not list:
        errors.append("extra_allow: must be a list")
    elif len(extra) > MAX_EXTRA_ALLOW:
        errors.append(f"extra_allow: too many entries (max {MAX_EXTRA_ALLOW})")
    else:
        for i, e in enumerate(extra):
            path = f"extra_allow[{i}]"
            if type(e) is not dict:
                errors.append(f"{path}: must be an object")
                continue
            _keys_errors(e, _EXTRA_KEYS, path, errors)
            _scope_errors(e, path, errors)
            c = e.get("cidr")
            if not _is_str(c) or (cf := _family_of_cidr(c)) is None:
                errors.append(f"{path}: invalid cidr")
            elif e.get("family") in ("ipv4", "ipv6") and cf != e["family"]:
                errors.append(f"{path}: cidr family does not match '{e['family']}'")
            # A deny-default endpoint's scope can ONLY be opened via its own endpoint
            # checkbox (which carries the unauthenticated-exposure warning) — an extra_allow
            # entry covering that scope would bypass the warning, so it is refused here.
            for dep in deny_scopes:
                if _scopes_overlap(e, dep):
                    errors.append(
                        f"{path}: covers deny-default endpoint '{dep.get('id')}' — use that "
                        f"endpoint's Allow-direct-access checkbox instead")
    return errors


# --- canonical form + intent hash ------------------------------------------------------------

def canonical_intent(cand: dict) -> str:
    """Deterministic canonical JSON of a VALID candidate: entry order carries no meaning in
    the intent (rule ORDER is fixed structure owned by the helper), so lists are sorted by
    their identifying scope before serialization."""
    c = json.loads(json.dumps(cand))                       # deep copy, JSON-clean
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
    return json.dumps(c, sort_keys=True, separators=(",", ":"))


def intent_hash(cand: dict) -> str:
    """sha256 of the canonical intent. Raises ValueError on an invalid candidate — a hash of
    malformed intent must never exist (it could be mistaken for a comparable identity)."""
    errors = validate_candidate(cand)
    if errors:
        raise ValueError("invalid candidate: " + "; ".join(errors[:5]))
    return hashlib.sha256(canonical_intent(cand).encode("utf-8")).hexdigest()


# --- root artifact renderers (FW-3) ----------------------------------------------------------
# The controller RENDERS everything root will run — helper source, three systemd units, the
# first-install/apply script and the reset script — and never executes any of it. The helper
# source is the packaged lhpc/core/firewall_helper.py byte-for-byte (its tests run in-repo;
# root runs the installed root-owned copy). Unit constraints from the approved plan: host
# network namespace (NEVER PrivateNetwork/NetworkNamespacePath — nft must see the real
# firewall), CAP_NET_ADMIN-only, RuntimeDirectoryPreserve=yes (a completed oneshot must not
# take the receipt directory with it), loader ordered after nftables.service and before
# network-pre.target, and no foreign unit is ever enabled/modified/owned.

# Advanced relocation knob for sandboxes/simulators: $LHPC_FW_PATH_PREFIX prepends a
# directory to the host-global firewall paths so the REAL freshness/receipt logic runs
# against relocated files. Captured at import. Unset (production, always) leaves every
# value byte-identical to the literals below.
_PFX = os.environ.get("LHPC_FW_PATH_PREFIX", "")

HELPER_DEST = _PFX + "/etc/lhpc/firewall-helper"
CANDIDATE_DEST = _PFX + "/etc/lhpc/firewall.candidate.json"
META_DEST = _PFX + "/etc/lhpc/firewall.meta.json"
SNAPSHOT_DEST = _PFX + "/etc/lhpc/firewall.snapshot.json"
TRANSITION_DEST = _PFX + "/etc/lhpc/firewall.transition.json"
JOURNAL_DEST = _PFX + "/etc/lhpc/firewall.journal.json"
LOCK_DEST = _PFX + "/etc/lhpc/.firewall.lock"
LOADER_UNIT = "lhpc-firewall.service"
CHECKER_UNIT = "lhpc-firewall-check.service"
CHECKER_TIMER = "lhpc-firewall-check.timer"
RECEIPT_PATH = _PFX + "/run/lhpc-firewall/check.json"

_SANDBOX = """\
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=yes
PrivateTmp=yes
ReadWritePaths=/etc/lhpc /run/lhpc-firewall
CapabilityBoundingSet=CAP_NET_ADMIN
RestrictAddressFamilies=AF_NETLINK AF_UNIX AF_INET AF_INET6
RestrictNamespaces=true
LockPersonality=true
SystemCallArchitectures=native
RuntimeDirectory=lhpc-firewall
RuntimeDirectoryMode=0755
RuntimeDirectoryPreserve=yes
"""


def helper_source() -> str:
    """The exact bytes to install as /etc/lhpc/firewall-helper (root:root 0755)."""
    from pathlib import Path
    return (Path(__file__).parent / "firewall_helper.py").read_text()


def integration_rev() -> str:
    """Full SHA-256 of the packaged helper source — the EXPECTED installed-helper revision. The
    installed helper stamps its OWN source hash into every receipt/snapshot; the controller requires
    an exact match before Live can be green, so a stale helper left by an lhpc update reads
    'setup/update required' until re-applied. TRAILING newlines are stripped before hashing: the
    apply-script heredoc that installs the helper appends a newline, so the on-disk file differs
    from `helper_source()` by one trailing byte — normalising makes the two sides match while still
    detecting any semantic change to the helper."""
    return hashlib.sha256(helper_source().rstrip("\n").encode("utf-8")).hexdigest()


def render_loader_unit() -> str:
    return f"""\
# LoRaHAM Pi Control managed-firewall LOADER — root-owned lhpc artifact (generated).
# Runs AFTER a foreign nftables.service (its `flush ruleset`, if any, finishes first —
# ordering only: lhpc never enables, modifies or takes ownership of that unit) and BEFORE
# network-pre.target, systemd's intended firewall barrier. The lingering user manager is
# NOT ordered against this — the controller's listener gate covers that case (the verified
# invariant is "no lhpc-managed non-loopback listener before a valid current-boot receipt").
[Unit]
Description=LoRaHAM Pi Control firewall loader (lhpc-owned table only)
DefaultDependencies=no
After=local-fs.target nftables.service
Wants=network-pre.target
Before=network-pre.target shutdown.target
Conflicts=shutdown.target
RequiresMountsFor=/etc/lhpc

[Service]
Type=oneshot
ExecStart={HELPER_DEST} load
# HOST network namespace is MANDATORY: nft must program the Pi's real firewall. Never add
# PrivateNetwork= or NetworkNamespacePath= here.
{_SANDBOX}
[Install]
WantedBy=multi-user.target
"""


def render_checker_unit() -> str:
    return f"""\
# LoRaHAM Pi Control managed-firewall CHECKER — root-owned lhpc artifact (generated).
# Verifies the LIVE inet lhpc table against the accepted snapshot (normalized semantic
# comparison) and writes the receipt to /run/lhpc-firewall/check.json. It NEVER re-applies
# rules on drift — it reports; reapplication stays an explicit operator sudo action.
# RuntimeDirectoryPreserve keeps the receipt directory across completed oneshot runs; only
# reboot clears it, which is exactly the freshness lifetime the receipt claims.
[Unit]
Description=LoRaHAM Pi Control firewall live check
After=local-fs.target

[Service]
Type=oneshot
ExecStart={HELPER_DEST} check
# HOST network namespace is MANDATORY (see loader unit).
{_SANDBOX}"""


def render_checker_timer() -> str:
    return f"""\
# LoRaHAM Pi Control managed-firewall check TIMER — root-owned lhpc artifact (generated).
[Unit]
Description=Periodic LoRaHAM Pi Control firewall live check

[Timer]
OnBootSec=60
OnUnitActiveSec=60
AccuracySec=15
Unit={CHECKER_UNIT}

[Install]
WantedBy=timers.target
"""


def _sh_heredoc(name: str, content: str) -> str:
    """Quoted-delimiter heredoc — content is written verbatim, nothing expands."""
    return f"cat > \"$TMP\" <<'LHPC_EOF_{name}'\n{content}\nLHPC_EOF_{name}\n"


def render_apply_script(candidate_json: str) -> str:
    """The ONE operator command (`sudo bash .../firewall-apply.sh`). Idempotent: installs or
    refreshes the root-owned helper + units (mktemp+mv, symlink-refusing), enables loader and
    timer on FIRST install, copies the candidate to a root-owned path, then delegates the
    entire firewall mutation to the INSTALLED helper (a mutable checkout script is never the
    thing executing nft — plan 6c). Runs an immediate check at the end."""
    units = ((LOADER_UNIT, render_loader_unit()), (CHECKER_UNIT, render_checker_unit()),
             (CHECKER_TIMER, render_checker_timer()))
    lines = [
        "#!/usr/bin/env bash",
        "# lhpc managed-firewall APPLY (generated — regenerate via the console or",
        "# `lhpc firewall --script`). Installs/refreshes ROOT-OWNED lhpc artifacts and applies",
        "# the current candidate through the installed helper. It never edits, overwrites,",
        "# renames or deletes any pre-existing firewall configuration of yours.",
        "set -euo pipefail",
        'if [ "$(id -u)" -ne 0 ]; then echo "run with sudo" >&2; exit 10; fi',
        'command -v nft >/dev/null || { echo "nftables is not installed (sudo apt install -y nftables)" >&2; exit 11; }',
        "mkdir -p /etc/lhpc",
        "install_file() {  # $1 dest  $2 mode   (content on stdin via $TMP)",
        '\tif [ -e "$1" ] && [ ! -f "$1" ]; then echo "refusing: $1 is not a regular file" >&2; exit 12; fi',
        '\tchmod "$2" "$TMP"; mv -f "$TMP" "$1"',
        "}",
    ]
    # TRANSACTIONAL ORDER: install the helper + candidate, then VALIDATE-AND-APPLY through the
    # helper FIRST (set -e aborts here on any failure). Only after a proven-good apply do we
    # install and enable the boot units — so a refused/invalid apply never leaves enabled units
    # pointing at a missing/bad snapshot.
    body = ['TMP="$(mktemp /etc/lhpc/.helper.XXXXXX)"',
            _sh_heredoc("HELPER", helper_source()),
            f'install_file {HELPER_DEST} 0755',
            'TMP="$(mktemp /etc/lhpc/.cand.XXXXXX)"',
            _sh_heredoc("CANDIDATE", candidate_json),
            f"install_file {CANDIDATE_DEST} 0644",
            f"{HELPER_DEST} apply {CANDIDATE_DEST}"]     # validates+loads; aborts on failure
    for name, content in units:
        body += [f'TMP="$(mktemp /etc/systemd/system/.{name}.XXXXXX)"',
                 _sh_heredoc(name.replace(".", "_").replace("-", "_"), content),
                 f"install_file /etc/systemd/system/{name} 0644"]
    body += [
        "systemctl daemon-reload",
        f"systemctl enable {LOADER_UNIT} {CHECKER_TIMER} >/dev/null",
        f"systemctl start {CHECKER_TIMER}",
        f"{HELPER_DEST} check || true",
        'echo "apply finished — dashboard shows the verified state after the next refresh"',
    ]
    return "\n".join(lines + body) + "\n"


def render_cleanup_script() -> str:
    """Transition cleanup: re-applies the root-owned candidate WITHOUT transition
    preservation, producing the final model and clearing the transition record."""
    return "\n".join([
        "#!/usr/bin/env bash",
        "set -euo pipefail",
        'if [ "$(id -u)" -ne 0 ]; then echo "run with sudo" >&2; exit 10; fi',
        f"{HELPER_DEST} apply {CANDIDATE_DEST} --cleanup",
        f"{HELPER_DEST} check || true",
    ]) + "\n"


def render_reset_script() -> str:
    """Exact undo: the helper removes the OWNED table + lhpc data (ownership-proven), then
    this script disables and removes only lhpc's own units and artifacts. Foreign files,
    tables, units and enabled-states are untouched — by construction nothing here names
    anything that is not lhpc's.

    Fail-closed on an untrusted helper: ownership + nftables verification lives ONLY in the
    trusted root helper — never duplicated in Bash. If the installed helper is missing, a
    symlink/non-regular file, not root-owned, or not executable, the reset REFUSES and tells the
    operator to regenerate/reinstall the current helper (run the apply script) and then reset —
    so a live owned table is removed by the proven code path, never stranded. The `/etc/lhpc`
    directory is emptied of KNOWN lhpc files only, then removed WITH rmdir (any unexpected
    foreign file left there is preserved, not blindly recursively deleted)."""
    # Every file lhpc itself writes under /etc/lhpc — nothing else lives here by design.
    lhpc_files = [HELPER_DEST, CANDIDATE_DEST, META_DEST, SNAPSHOT_DEST,
                  TRANSITION_DEST, JOURNAL_DEST, LOCK_DEST]
    units = [CHECKER_TIMER, CHECKER_UNIT, LOADER_UNIT]
    lines = [
        "#!/usr/bin/env bash",
        "# lhpc managed-firewall RESET (generated). Removes ONLY lhpc-owned artifacts.",
        "set -euo pipefail",
        'if [ "$(id -u)" -ne 0 ]; then echo "run with sudo" >&2; exit 10; fi',
        # The trusted helper MUST be present as a regular, root-owned, executable file — it is the
        # only code that proves table ownership and removes it. Anything else -> REFUSE (never
        # duplicate ownership/nft logic in Bash, never delete state we cannot prove is ours).
        f'if [ -f {HELPER_DEST} ] && [ ! -L {HELPER_DEST} ] && [ -O {HELPER_DEST} ] '
        f'&& [ -x {HELPER_DEST} ]; then',
        f'\t{HELPER_DEST} reset',
        'else',
        '\techo "refusing: the trusted firewall-helper is missing or untrusted —" >&2',
        '\techo "reinstall the current helper (run firewall-apply.sh), then run this reset" >&2',
        '\texit 13',
        'fi',
        # Disable+remove ONLY units whose files exist; a genuine disable failure is surfaced
        # (set -e), while an absent unit is simply skipped (never a spurious failure).
        f'for u in {" ".join(units)}; do',
        '\tif [ -e "/etc/systemd/system/$u" ]; then',
        '\t\tsystemctl disable --now "$u"',
        '\t\trm -f "/etc/systemd/system/$u"',
        '\tfi',
        'done',
        "systemctl daemon-reload",
        # Remove KNOWN lhpc files, then rmdir the dir — rmdir removes it ONLY if empty, so any
        # unexpected foreign file left under /etc/lhpc is preserved rather than blindly nuked.
        f'rm -f {" ".join(lhpc_files)}',
        'rmdir /etc/lhpc 2>/dev/null || true',
        "rm -rf /run/lhpc-firewall",
        'echo "lhpc firewall integration removed — your own firewall configuration is exactly as before"',
    ]
    return "\n".join(lines) + "\n"
