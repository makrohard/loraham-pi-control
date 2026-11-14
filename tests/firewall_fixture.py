"""Shared firewall test helpers (a plain helper module, not a test module): the candidate builders, the helper's
programmable system seam, the ownership metadata seed and the expected live listing of a model."""

from __future__ import annotations

from lhpc.core import firewall as fw


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
        if argv[:4] in (["nft", "-j", "list", "ruleset"], ["nft", "-j", "list", "tables"]):
            # `tables_listing` is the whole ruleset (tables AND their chains); `list tables`
            # answers with the table entries of it only, as nft does.
            import json as _json
            entries = self.tables_listing.get("nftables", [])
            if argv[3] == "tables":
                entries = [e for e in entries if "table" in e]
            return 0, _json.dumps({"nftables": entries}), ""
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
