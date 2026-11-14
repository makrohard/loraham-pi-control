"""A21 (PLAN-FW-v4 §1, §2, §5): the gates decide on the APPLIED firewall. Once a hardware's apply ran, a start, a
Webserver Apply and the boot gate pass for every scope the applied candidate has a row for, whatever else was saved
since; source CIDRs and the direct-access tick follow the applied firewall until the next apply (his word). The applied
evidence is the helper's own snapshot + receipt: here written by the REAL helper (`op_apply` over a fake nft) at the
LHPC_FW_PATH_PREFIX layout, read by the controller through its real readers — firewall_status is never mocked."""

from __future__ import annotations

import json
import os
import time

import pytest

import radios_fixture
from lhpc.core import config as cfgmod
from lhpc.core import firewall as fw
from lhpc.core import firewall_helper as fh
from lhpc.core import instances, radios
from lhpc.core import service_firewall as sf
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.service_base import ActionResult
from lhpc.core.services import ControllerService
from firewall_fixture import _candidate, _ep, _expected_live_json, _FakeSys, _seed_meta

R1, R2 = radios_fixture.RADIOS
R3 = radios.Radio("r3", "ch341", "ch341-rfm98pw-433", "433", usb_serial="CHE1W433")
_REAL_OWNER = sf._owner_is_host_root
_HOST = {n: getattr(fw, n) for n in ("HELPER_DEST", "CANDIDATE_DEST", "META_DEST", "SNAPSHOT_DEST", "TRANSITION_DEST",
                                     "JOURNAL_DEST", "LOCK_DEST")} | {"RECEIPT_PATH": fw._PFX + "/run/lhpc-firewall/check.json"}
_DESTS = ("HELPER_DEST", "CANDIDATE_DEST", "META_DEST", "SNAPSHOT_DEST", "TRANSITION_DEST", "JOURNAL_DEST",
          "LOCK_DEST", "RECEIPT_PATH")


class _Sys(_FakeSys):
    """The helper's system seam with THIS boot's id and clock, so its receipt is fresh for the controller."""

    def boot_id(self):
        with open("/proc/sys/kernel/random/boot_id") as f:
            return f.read().strip()

    def boottime(self):
        return time.clock_gettime(time.CLOCK_BOOTTIME)


class _HostFs(FakeSystem):
    """Existence of the firewall artifacts from the real files under the prefix; the systemd units are present."""

    def __init__(self, host):
        super().__init__(paths=set(f"/etc/systemd/system/{u}" for u in (fw.LOADER_UNIT, fw.CHECKER_UNIT,
                                                                           fw.CHECKER_TIMER)))
        self._host = str(host)

    def exists(self, path):
        return os.path.exists(path) if path.startswith(self._host) else super().exists(path)


class Lab:
    """One box: its runtime root, the firewall files under the prefix, and the helper that applies them."""

    def __init__(self, tmp_path, monkeypatch, rows):
        self.host = tmp_path / "host"
        for name in _DESTS:                         # the LHPC_FW_PATH_PREFIX layout, as the testlab sets it
            monkeypatch.setattr(fw, name, str(self.host) + _HOST[name])
        # The real integration readers (conftest's isolation answers "absent" for every service).
        monkeypatch.setattr(ControllerService, "_fw_integration_state", sf.FirewallOpsMixin._fw_integration_state)
        monkeypatch.setattr(ControllerService, "_fw_units_enabled", sf.FirewallOpsMixin._fw_units_enabled)
        self.etc, self.run = os.path.dirname(fw.SNAPSHOT_DEST), os.path.dirname(fw.RECEIPT_PATH)
        # The helper writes as root on a box; here the test user owns the files: read them as host root.
        monkeypatch.setattr(sf, "_read_uid_map", lambda: [(0, 0, 4294967295)])
        monkeypatch.setattr(sf, "_owner_is_host_root", lambda uid, *, canonical_path: _REAL_OWNER(
            0 if uid == os.getuid() else uid, canonical_path=canonical_path))
        self.root = tmp_path / "root"
        radios_fixture.write(self.root, rows)
        self.applied = None

    def svc(self):
        return ControllerService(system=_HostFs(self.host).system, paths=Paths(runtime_root=self.root))

    def radios(self, rows):
        radios_fixture.write(self.root, rows)

    def apply(self, sysx=None, cand=None):
        """The operator's `sudo bash firewall-apply.sh`: the helper applies `cand` (default: the saved candidate).
        Returns its rc."""
        cand = self.svc().firewall_candidate() if cand is None else cand
        if not os.path.exists(fw.META_DEST):
            _seed_meta(self.etc)
        for dest, text in ((fw.HELPER_DEST, fw.helper_source()), (fw.CANDIDATE_DEST, json.dumps(cand))):
            with open(dest, "w") as f:
                f.write(text)
        old = self.applied
        t_allow, t_drop = fh._transition_sets(old, cand)
        sysx = sysx or _Sys()
        sysx.preexisting = old is not None
        sysx.listing = _expected_live_json(cand, transition_allow=t_allow, transition_drop=t_drop)
        rc = fh.op_apply(sysx, fw.CANDIDATE_DEST, etc_dir=self.etc, run_dir=self.run)
        if rc == fh.EXIT_OK:
            self.applied = cand
        return rc

    def snapshot(self):
        with open(fw.SNAPSHOT_DEST) as f:
            return json.load(f)

    def write(self, path, obj, mode=0o644):
        with open(path, "w") as f:
            f.write(obj if isinstance(obj, str) else json.dumps(obj, sort_keys=True))
        os.chmod(path, mode)


def _remote_console(lab, bind="0.0.0.0"):
    cfgmod.save_webserver_config(Paths(runtime_root=lab.root), bind=bind, port=8443, remote_exposed=True,
                                 allowed_cidrs=["192.168.178.0/24"])


def _box(tmp_path, monkeypatch, rows=(R1, R2), console=True):
    """r1 + r2 configured (and the console remote), the firewall applied: the box after its hardware's apply."""
    lab = Lab(tmp_path, monkeypatch, rows)
    if console:
        _remote_console(lab)
    assert lab.apply() == fh.EXIT_OK
    assert lab.svc()._fw_integration_state() == "present"                 # anchor: every gate below really decides
    return lab


def _start(svc, sid, rid, apply=False):
    inst, why = instances.copy_of(svc, sid, rid)
    assert inst is not None, why
    return svc.start(sid, inst=inst, apply=apply)


def _gate(svc, sid, rid, render=False):
    inst, why = instances.copy_of(svc, sid, rid)
    assert inst is not None, why
    return svc.firewall_gate_stack_start(sid, inst=inst, render=render)


def _nginx_stubs(monkeypatch, svc):
    applied = []
    monkeypatch.setattr("lhpc.core.webserver.nginx_installed", lambda system: True)
    monkeypatch.setattr(svc, "_webserver_apply_after_gate",
                        lambda cfg: (applied.append(1), ActionResult(True, "applied"))[1])
    return applied


def _boot(monkeypatch, svc):
    """The nginx boot gate with no wait: its summary, and the bind it promoted (None = it kept the remote config)."""
    promoted = {}
    monkeypatch.setattr(fw, "BOOT_GATE_WAIT_S", 0)
    monkeypatch.setattr("lhpc.core.webserver.stage_and_validate",
                        lambda system, paths, cfg, proxies: (promoted.__setitem__("bind", cfg.bind) or (True, "", "s")))
    monkeypatch.setattr("lhpc.core.webserver.promote_config", lambda paths: None)
    r = svc.firewall_boot_gate()
    assert r.ok, r.summary
    return r.summary, promoted.get("bind")


# --- passes: a radio added after the apply needs no apply for what the applied firewall covers (red on 085034cf) ---

def test_a_configured_radios_stack_starts_after_another_radio_is_added(tmp_path, monkeypatch):
    # red on 085034cf (evidence-v1/m4): adding r3 changed the intent, so meshtastic@r1 was refused "changes pending"
    lab = _box(tmp_path, monkeypatch, console=False)
    lab.radios((R1, R2, R3))
    svc = lab.svc()
    assert not svc.firewall_status()["config_ok"]                       # the saved intent differs: r3's rows
    for rid in ("r1", "r2"):
        assert _gate(svc, "meshtastic", rid) == (True, "", [])            # the gate itself allows it
        res = _start(svc, "meshtastic", rid)                              # and the public plan is not refused by it
        assert (res.data or {}).get("firewall_gate") != "pending", res.summary
    ok, msg, cmds = _gate(svc, "meshtastic", "r3")                        # r3 waits for its own apply
    assert not ok and msg.startswith("The saved listener is not covered by the applied firewall"), msg
    assert cmds[0] == "lhpc firewall --script > /dev/null"               # the dry run wrote no script
    assert lab.apply() == fh.EXIT_OK                                      # r3's apply: one per hardware
    assert _gate(lab.svc(), "meshtastic", "r3")[0]


def test_webserver_apply_passes_after_a_radio_is_added(tmp_path, monkeypatch):
    # red on 085034cf: `webserver apply` with the console remote was refused after an unrelated radio was added
    lab = _box(tmp_path, monkeypatch)
    lab.radios((R1, R2, R3))
    svc = lab.svc()
    applied = _nginx_stubs(monkeypatch, svc)
    r = svc.webserver_apply()
    assert r.ok and applied == [1], r.summary
    assert not svc.webserver_apply_pending()


def test_the_boot_gate_keeps_the_console_remote_after_a_radio_is_added(tmp_path, monkeypatch):
    # red on 085034cf: the next boot started the console LOOPBACK-ONLY after a radio was added without an apply
    lab = _box(tmp_path, monkeypatch)
    lab.radios((R1, R2, R3))
    summary, bind = _boot(monkeypatch, lab.svc())
    assert "remote listener allowed" in summary and bind is None, summary


def test_the_deferred_apply_and_the_watchdog_complete_on_the_applied_firewall(tmp_path, monkeypatch):
    # A Webserver Apply refused before the console's apply completes once that apply ran, though r3 was added since.
    lab = Lab(tmp_path, monkeypatch, (R1, R2))
    assert lab.apply() == fh.EXIT_OK                                      # applied with the console local
    _remote_console(lab)
    svc = lab.svc()
    applied = _nginx_stubs(monkeypatch, svc)
    assert not svc.webserver_apply().ok and svc.webserver_apply_pending()
    assert svc.webserver_apply_complete_pending() is None                 # not applied yet: nothing to complete
    assert lab.apply() == fh.EXIT_OK
    lab.radios((R1, R2, R3))
    svc._invalidate_config()
    assert svc.webserver_apply_complete_pending().ok and applied == [1]


# --- passes by his contract: CIDRs and the tick follow the applied firewall --------------------------------------

def _kiss(svc, values, rid="r1"):
    inst, _why = instances.copy_of(svc, "kiss", rid)
    cfgmod.update_stack_config(svc._paths, "kiss", values, inst=inst)
    svc._invalidate_config()


@pytest.mark.parametrize("change", [
    {"kiss_bind": "192.168.178.128/25"},                                 # narrowed
    {"kiss_bind": "192.168.0.0/16"},                                     # widened
    {"kiss_bind": ""},                                                   # any source
])
def test_a_source_cidr_change_on_an_applied_row_takes_effect_with_the_apply(tmp_path, monkeypatch, change):
    lab = Lab(tmp_path, monkeypatch, (R1, R2))
    _kiss(lab.svc(), {"kiss_host": "0.0.0.0", "kiss_bind": "192.168.178.0/24"})
    assert lab.apply() == fh.EXIT_OK
    svc = lab.svc()
    _kiss(svc, change)
    assert not svc.firewall_status()["config_ok"]
    assert _gate(svc, "kiss", "r1")[0]


@pytest.mark.parametrize("tick", [True, False])
def test_a_direct_access_tick_or_untick_takes_effect_with_the_apply(tmp_path, monkeypatch, tick):
    lab = Lab(tmp_path, monkeypatch, (R1, R2))
    svc = lab.svc()
    _kiss(svc, {"kiss_host": "0.0.0.0"})
    rid = next(e["id"] for e in svc.firewall_candidate()["endpoints"] if e["id"].startswith("loraham-kiss-tnc-r1"))
    assert svc.firewall_configure(allow_endpoints=[] if tick else [rid]).ok
    assert lab.apply() == fh.EXIT_OK
    svc = lab.svc()
    assert svc.firewall_configure(allow_endpoints=[rid] if tick else []).ok
    assert not svc.firewall_status()["config_ok"] and _gate(svc, "kiss", "r1")[0]


# --- refused: what the applied firewall has no row for, or a policy it does not carry ----------------------------

@pytest.mark.parametrize("values", [
    {"kiss_port": "9002"},                                                # changed port
    {"kiss_host": "192.168.178.5"},                                       # changed bind address
    {"kiss_host": "::"},                                                  # changed family
])
def test_a_changed_listener_key_is_refused_until_its_apply(tmp_path, monkeypatch, values):
    lab = Lab(tmp_path, monkeypatch, (R1, R2))
    _kiss(lab.svc(), {"kiss_host": "0.0.0.0", "kiss_port": "9001"})
    assert lab.apply() == fh.EXIT_OK
    svc = lab.svc()
    assert _gate(svc, "kiss", "r1")[0]                                    # anchor: the applied listener passes
    _kiss(svc, values)
    ok, msg, _cmds = _gate(svc, "kiss", "r1")
    assert not ok and msg.startswith("The saved listener is not covered"), msg


def test_a_loopback_listener_bound_to_the_network_is_refused_until_its_apply(tmp_path, monkeypatch):
    lab = _box(tmp_path, monkeypatch, console=False)                      # kiss loopback when applied: no row
    svc = lab.svc()
    _kiss(svc, {"kiss_host": "0.0.0.0"})
    ok, msg, _cmds = _gate(svc, "kiss", "r1")
    assert not ok and msg.startswith("The saved listener is not covered"), msg
    assert _gate(svc, "meshtastic", "r1")[0]                              # anchor: an applied listener passes


def _firewall_extra(lab):
    cfgmod.save_firewall_config(Paths(runtime_root=lab.root), extra_allow=[
        {"proto": "tcp", "family": "ipv4", "addr": "*", "port": 5555, "cidr": "10.0.0.0/8"}])


@pytest.mark.parametrize("change", ["mode", "extra_allow", "invalid"])
def test_a_policy_the_applied_firewall_does_not_carry_waits_for_its_apply(tmp_path, monkeypatch, change):
    # mode: compatibility is never credited as secure-default; extra_allow takes effect with its apply; an invalid
    # saved candidate refuses as before (here 17 SSH ports, over MAX_SSH_PORTS: no key of any row changes)
    lab = _box(tmp_path, monkeypatch)
    svc = lab.svc()
    applied = _nginx_stubs(monkeypatch, svc)
    assert _gate(svc, "meshtastic", "r1")[0] and svc.webserver_apply().ok   # anchor
    if change == "mode":
        assert svc.firewall_configure(mode="compatibility").ok
    elif change == "extra_allow":
        _firewall_extra(lab)
    else:
        cfgmod.save_firewall_config(svc._paths, ssh_ports=list(range(2201, 2218)))
    svc._invalidate_config()
    ok, msg, _cmds = _gate(svc, "meshtastic", "r1")
    assert not ok and msg.startswith("Firewall changes pending"), msg
    r = svc.webserver_apply()
    assert not r.ok and "Firewall changes pending" in r.summary and applied == [1]


def test_an_unmapped_listener_never_has_a_row():
    sc = {"id": "x.tcp-1", "proto": "tcp", "family": "dual", "addr": "*", "port": 1, "band": "", "_unmapped": True}
    row = {k: sc[k] for k in ("id", "proto", "family", "addr", "port", "band")}
    assert not sf._fw_has_row(sc, {"endpoints": [row]})
    assert sf._fw_has_row({**sc, "_unmapped": False}, {"endpoints": [row]})     # anchor: the same key matches


def test_a_band_agnostic_row_never_covers_a_band_whose_scope_diverged(tmp_path, monkeypatch):
    # G1-FW-A21 B1 (dropped on this proof): an applied row with band="" exists only while the scope is the same on
    # every band. Here kiss is applied with one scope on both radios' bands (433: r1, 868: r2), every row band="";
    # then the 868 band's saved scope diverges (its port): that band's listener has no applied row and is REFUSED
    # until the next apply, while the unchanged 433 band still starts.
    lab = Lab(tmp_path, monkeypatch, (R1, R2))
    for rid in ("r1", "r2"):
        _kiss(lab.svc(), {"kiss_host": "0.0.0.0", "kiss_port": "9001"}, rid=rid)
    assert lab.apply() == fh.EXIT_OK
    kiss = [e for e in lab.snapshot()["candidate"]["endpoints"] if e["id"].startswith("loraham-kiss-tnc")]
    assert len(kiss) == 2 and {e["band"] for e in kiss} == {""}         # the band-agnostic rows are in play
    svc = lab.svc()
    assert _gate(svc, "kiss", "r2")[0]                                    # anchor: before the divergence it passes
    _kiss(svc, {"kiss_port": "9002"}, rid="r2")
    ok, msg, _cmds = _gate(svc, "kiss", "r2")
    assert not ok and msg.startswith("The saved listener is not covered"), msg
    assert _gate(svc, "kiss", "r1")[0]
    assert lab.apply() == fh.EXIT_OK and _gate(lab.svc(), "kiss", "r2")[0]   # its apply covers it


def test_the_templates_band_agnostic_row_covers_no_diverged_band():
    # The same proof for the template (no radio): the row builder writes band="" only for one scope on every band;
    # a band whose port diverged does not match that row, a CIDR-only divergence does (his word: CIDRs follow the apply).
    import types
    stk = types.SimpleNamespace(id="kiss", components=())

    def scope(port, cidrs=()):
        return {"id": "kiss.kc.tcp-8001", "proto": "tcp", "family": "ipv4", "addr": "*", "port": port,
                "allow_cidrs": list(cidrs), "deny": False, "auth": "none", "loopback": False}
    applied = {"endpoints": fw.endpoint_rows(stk, ("433", "868"), lambda band, inst: scope(9001), set())}
    assert [e["band"] for e in applied["endpoints"]] == [""]
    launch = {k: v for k, v in scope(9002).items() if k in ("id", "proto", "family", "addr", "port")}
    assert not sf._fw_has_row({**launch, "band": "868"}, applied)
    assert sf._fw_has_row({**launch, "port": 9001, "band": "868", "allow_cidrs": ["10.0.0.0/8"]}, applied)


def test_a_band_labelled_row_serves_its_own_band_only():
    sc = {"id": "a.tcp-1", "proto": "tcp", "family": "ipv4", "addr": "*", "port": 1, "band": "433"}
    row = {k: sc[k] for k in ("id", "proto", "family", "addr", "port")}
    assert not sf._fw_has_row(sc, {"endpoints": [{**row, "band": "868"}]})
    assert sf._fw_has_row(sc, {"endpoints": [{**row, "band": "433"}]})
    assert sf._fw_has_row(sc, {"endpoints": [{**row, "band": ""}]})              # band-agnostic: every band


@pytest.mark.parametrize("bind", ["192.168.178.5", "::"])
def test_the_console_on_another_address_or_family_waits_for_its_apply(tmp_path, monkeypatch, bind):
    lab = _box(tmp_path, monkeypatch)
    _remote_console(lab, bind=bind)                                       # same port 8443, another scope
    svc = lab.svc()
    _nginx_stubs(monkeypatch, svc)
    assert not svc.webserver_apply().ok
    summary, promoted = _boot(monkeypatch, svc)
    assert "LOOPBACK-ONLY" in summary and promoted == "127.0.0.1"


# --- refused, evidence: the applied evidence counts only when the helper's files prove it ------------------------

def _tamper_snapshot(lab, edit):
    snap = lab.snapshot()
    edit(snap)
    lab.write(fw.SNAPSHOT_DEST, snap)


def _tamper_receipt(lab, **over):
    with open(fw.RECEIPT_PATH) as f:
        r = json.load(f)
    lab.write(fw.RECEIPT_PATH, {**r, **over})


TAMPER = {
    "intent-hash": lambda lab: _tamper_snapshot(lab, lambda s: s["candidate"]["endpoints"].pop()),
    "model-hash": lambda lab: _tamper_snapshot(lab, lambda s: s["model"]["rules"].pop()),
    "snapshot-key-missing": lambda lab: _tamper_snapshot(lab, lambda s: s.pop("integration_rev")),
    "snapshot-old-helper": lambda lab: _tamper_snapshot(lab, lambda s: s.update(integration_rev="OLD")),
    "snapshot-invalid-json": lambda lab: lab.write(fw.SNAPSHOT_DEST, "{not json"),
    "snapshot-group-writable": lambda lab: os.chmod(fw.SNAPSHOT_DEST, 0o664),
    "receipt-other-boot": lambda lab: _tamper_receipt(lab, boot_id="another-boot"),
    "receipt-not-verified": lambda lab: _tamper_receipt(lab, verdict="mismatch"),
    "receipt-error": lambda lab: _tamper_receipt(lab, verdict="error"),
    "receipt-stale-helper": lambda lab: _tamper_receipt(lab, integration_rev="STALE-REV"),
    "journal-present": lambda lab: lab.write(fw.JOURNAL_DEST, {"op": "apply", "phase": "begin"}, 0o600),
}


@pytest.mark.parametrize("how", sorted(TAMPER))
def test_the_applied_evidence_counts_only_when_the_helpers_files_prove_it(tmp_path, monkeypatch, how):
    lab = _box(tmp_path, monkeypatch, console=False)
    lab.radios((R1, R2, R3))
    assert _gate(lab.svc(), "meshtastic", "r1")[0]                        # anchor: the evidence counts
    TAMPER[how](lab)
    ok, msg, _cmds = _gate(lab.svc(), "meshtastic", "r1")
    assert not ok, how


def test_a_snapshot_symlink_or_one_over_the_bound_is_not_evidence(tmp_path, monkeypatch):
    lab = _box(tmp_path, monkeypatch, console=False)
    lab.radios((R1, R2, R3))
    svc = lab.svc()
    assert svc._fw_applied_candidate() is not None                        # anchor
    monkeypatch.setattr(fw, "MAX_SNAPSHOT_BYTES", os.path.getsize(fw.SNAPSHOT_DEST) - 1)
    assert svc._fw_applied_candidate() is None
    monkeypatch.setattr(fw, "MAX_SNAPSHOT_BYTES", 8 * 1024 * 1024)
    real = fw.SNAPSHOT_DEST + ".real"
    os.rename(fw.SNAPSHOT_DEST, real)
    os.symlink(real, fw.SNAPSHOT_DEST)
    assert svc._fw_applied_candidate() is None


def test_a_snapshot_not_owned_by_root_is_not_evidence(tmp_path, monkeypatch):
    lab = _box(tmp_path, monkeypatch, console=False)
    svc = lab.svc()
    assert svc._fw_applied_candidate() is not None                        # anchor
    monkeypatch.setattr(sf, "_owner_is_host_root", _REAL_OWNER)          # the test user's file, read as itself
    assert svc._fw_applied_candidate() is None


def test_a_receipt_that_changed_between_the_reads_is_not_evidence(tmp_path, monkeypatch):
    lab = _box(tmp_path, monkeypatch, console=False)
    svc = lab.svc()
    assert svc._fw_applied_candidate() is not None                        # anchor
    real = sf._read_root_json

    def read(path, max_bytes):                                           # a new apply lands after the snapshot read
        out = real(path, max_bytes)
        if path == fw.SNAPSHOT_DEST:
            _tamper_receipt(lab, walltime=1.0)
        return out
    monkeypatch.setattr(sf, "_read_root_json", read)
    assert svc._fw_applied_candidate() is None


def test_a_transitional_snapshot_counts_with_its_candidate_rows_only(tmp_path, monkeypatch):
    # The transitional apply keeps the old console ingress open in the MODEL until the cleanup apply; the gate reads
    # the candidate's rows only, so the console's old scope is not credited.
    lab = _box(tmp_path, monkeypatch)                                     # applied with the console remote
    cfgmod.save_webserver_config(Paths(runtime_root=lab.root), bind="127.0.0.1", remote_exposed=False)
    assert lab.apply() == fh.EXIT_OK
    assert lab.snapshot()["transitional"]
    svc = lab.svc()
    assert svc._fw_applied_candidate() is not None and _gate(svc, "meshtastic", "r1")[0]   # it counts
    _remote_console(lab)
    svc._invalidate_config()
    _nginx_stubs(monkeypatch, svc)
    assert not svc.webserver_apply().ok


# --- helper (§5): a failed apply never leaves a gate on the previous evidence -------------------------------------

class _FailingSys(_Sys):
    """Every load verifies against nothing: the new ruleset fails and the previous one is not restored."""

    def run(self, argv, timeout=30.0, stdin_text=None):
        out = super().run(argv, timeout, stdin_text)
        if argv[:2] == ["nft", "-f"]:
            self.listing = json.dumps({"nftables": []})
        return out


def test_a_failed_apply_refuses_through_the_journal_window_and_after_the_error_receipt(tmp_path, monkeypatch):
    lab = _box(tmp_path, monkeypatch, console=False)
    lab.radios((R1, R2, R3))
    assert _gate(lab.svc(), "meshtastic", "r1")[0]                        # anchor: the applied evidence counts
    seen = []
    real = fh.write_receipt

    def barrier(*a, **k):                                                 # the gate around the error receipt's write
        seen.append(("before", _gate(lab.svc(), "meshtastic", "r1")[0], os.path.exists(fw.JOURNAL_DEST)))
        out = real(*a, **k)
        seen.append(("after", _gate(lab.svc(), "meshtastic", "r1")[0], os.path.exists(fw.JOURNAL_DEST)))
        return out
    monkeypatch.setattr(fh, "write_receipt", barrier)
    sysx = _FailingSys()
    assert lab.apply(sysx) == fh.EXIT_FAIL
    with open(fw.RECEIPT_PATH) as f:
        assert "previous ruleset NOT restored" in json.load(f)["detail"]
    assert seen == [("before", False, True), ("after", False, True)], seen
    assert not os.path.exists(fw.JOURNAL_DEST) and not _gate(lab.svc(), "meshtastic", "r1")[0]


# --- kept: render-aware refusals, the dry run, the notice ---------------------------------------------------------

def test_a_start_refusal_names_the_script_only_when_it_was_rendered(tmp_path, monkeypatch):
    lab = _box(tmp_path, monkeypatch, console=False)
    lab.radios((R1, R2, R3))
    svc = lab.svc()
    monkeypatch.setattr(type(svc), "firewall_render",
                        lambda self: ActionResult(False, "could not render firewall scripts: EACCES"))
    ok, _msg, cmds = _gate(svc, "meshtastic", "r3", render=True)
    assert not ok and cmds == ["lhpc firewall --script"], cmds
    monkeypatch.setattr(type(svc), "firewall_render", lambda self: ActionResult(True, "regenerated"))
    ok, _msg, cmds = _gate(svc, "meshtastic", "r3", render=True)
    assert not ok and cmds[0].startswith("sudo bash ") and cmds[0].endswith("firewall-apply.sh"), cmds


def test_a_pass_on_the_applied_firewall_renders_on_the_apply_and_never_on_the_dry_run(tmp_path, monkeypatch):
    lab = _box(tmp_path, monkeypatch, console=False)
    lab.radios((R1, R2, R3))
    svc = lab.svc()
    calls = []
    monkeypatch.setattr(type(svc), "firewall_render",
                        lambda self: calls.append(1) or ActionResult(True, "regenerated"))
    assert _gate(svc, "meshtastic", "r1", render=False)[0] and calls == []
    assert _gate(svc, "meshtastic", "r1", render=True)[0] and calls == [1]   # the saved intent differs: re-render


def test_the_reapply_notice_shows_with_a_stale_helper_whatever_the_saved_intent(tmp_path, monkeypatch):
    # red on 085034cf: with the helper revision AND the candidate changed the pill reads "changes pending", and the
    # notice keyed on the pill's reason stayed silent — every box after the 0.14.0 update (new instance rows)
    lab = _box(tmp_path, monkeypatch)
    _tamper_receipt(lab, integration_rev="STALE-REV")
    lab.radios((R1, R2, R3))
    svc = lab.svc()
    note = svc.firewall_reapply_notice()
    assert note is not None and "before you reboot" in note["text"] and note["remote"]
    st = svc.firewall_status()
    assert st["reason"] == "changes-pending" and st["helper_stale"]
    _tamper_receipt(lab, integration_rev=fw.integration_rev())            # anchor: a current helper, no notice
    assert svc.firewall_reapply_notice() is None


# --- sandbox: the snapshot is trusted like the receipt, on its canonical path only --------------------------------

@pytest.mark.parametrize("unit, path, trusted", [
    ("lhpc-web.service", "canonical", True),
    ("lhpc-nginx.service", "canonical", True),
    ("x-lhpc-web.service.scope", "canonical", False),
    ("lhpc-web.service", "copy", False),
])
def test_the_snapshot_read_inside_the_managed_unit(tmp_path, monkeypatch, unit, path, trusted):
    lab = _box(tmp_path, monkeypatch, console=False)
    copy = fw.SNAPSHOT_DEST + ".copy"
    lab.write(copy, lab.snapshot())
    monkeypatch.setattr(sf, "_owner_is_host_root", _REAL_OWNER)
    monkeypatch.setattr(sf, "_read_uid_map", lambda: [(os.getuid(), os.getuid(), 1)])    # the same-ID sandbox
    monkeypatch.setattr(sf, "_overflow_uid", lambda: os.getuid())          # host root reads as the overflow uid
    monkeypatch.setattr(sf, "_own_cgroup_text", lambda: f"0::/user.slice/user@1000.service/app.slice/{unit}")
    read = sf._read_root_json(fw.SNAPSHOT_DEST if path == "canonical" else copy, fw.MAX_SNAPSHOT_BYTES)
    assert (read is not None) is trusted


# --- one-click pages (§3): the console's networks pre-open every page's allocated port -----------------------------

NET = "192.168.178.0/24"                                                  # the console's networks (_remote_console)


def _expose(svc, page, port=None, cidrs=(NET,), mode="lan"):
    port = svc._page_ports()[page] if port is None else port
    return svc.stack_web_configure_apply(page, mode=mode, port=port, cidrs=list(cidrs), confirm=True)


def _ingress(cand):
    return [(e["port"], tuple(e["allow_cidrs"])) for e in cand["proxy_ingress"]]


def test_a_page_exposed_to_the_consoles_networks_after_its_radios_apply_is_one_click(tmp_path, monkeypatch):
    # red on 085034cf (and on the decision alone): the page's own row was missing, so its Apply was refused
    lab = _box(tmp_path, monkeypatch)
    svc = lab.svc()
    applied = _nginx_stubs(monkeypatch, svc)
    r = _expose(svc, "graywolf@r1", port=8444)                            # its allocated port on the default list
    assert r.ok and applied == [1], r.summary
    assert not svc.webserver_apply_pending()


def test_the_bulk_expose_of_every_page_is_one_click_after_the_apply(tmp_path, monkeypatch):
    lab = _box(tmp_path, monkeypatch)
    svc = lab.svc()
    applied = _nginx_stubs(monkeypatch, svc)
    r = svc.stack_webs_configure_apply(mode="lan", scheme="https", access_mode="local-open-remote-auth",
                                       cidrs=[NET], confirm=True)
    assert r.ok and applied == [1], r.summary


def test_an_enabled_local_only_page_switched_to_remote_after_the_apply_is_one_click(tmp_path, monkeypatch):
    # red on 085034cf: a local page saved before the apply had no pre-opened row
    lab = Lab(tmp_path, monkeypatch, (R1, R2))
    _remote_console(lab)
    svc = lab.svc()
    assert svc.stack_web_configure("meshcom@r1", mode="local", port=8445).ok    # its allocated port
    assert lab.apply() == fh.EXIT_OK
    svc = lab.svc()
    applied = _nginx_stubs(monkeypatch, svc)
    r = _expose(svc, "meshcom@r1", port=8445)
    assert r.ok and applied == [1], r.summary


def test_a_page_exposed_to_other_networks_passes_and_its_apply_sets_them(tmp_path, monkeypatch):
    lab = _box(tmp_path, monkeypatch)
    svc = lab.svc()
    _nginx_stubs(monkeypatch, svc)
    assert _expose(svc, "graywolf@r1", cidrs=("10.0.0.0/8",)).ok
    port = svc._page_ports()["graywolf@r1"]
    assert (port, ("10.0.0.0/8",)) in _ingress(svc.firewall_candidate())   # the apply sets the page's networks
    assert not svc.firewall_status()["config_ok"]                         # "Changes pending" says so


def _server_block(text, port):
    return next(b for b in text.split("server {") if f":{port}" in b.split("\n", 3)[1])


def test_a_page_reusing_another_pages_port_passes_and_nginx_enforces_its_own_networks(tmp_path, monkeypatch):
    # his contract (§1.3, §9): the firewall keeps page A's networks at P until the apply; nginx enforces B's
    from lhpc.core import webserver as _ws
    lab = _box(tmp_path, monkeypatch)
    svc = lab.svc()
    _nginx_stubs(monkeypatch, svc)
    port = svc._page_ports()["graywolf@r1"]
    assert _expose(svc, "graywolf@r1", cidrs=("10.1.0.0/16",)).ok
    assert lab.apply() == fh.EXIT_OK
    svc = lab.svc()
    _nginx_stubs(monkeypatch, svc)
    assert svc.stack_web_configure("graywolf@r1", mode="local", port=port + 100).ok
    assert _expose(svc, "meshcom@r1", port=port, cidrs=("10.2.0.0/16",)).ok      # passes on A's row at P
    assert (port, ("10.1.0.0/16",)) in _ingress(lab.snapshot()["candidate"])     # the firewall: A's networks
    text = _ws.render_nginx_config(svc._paths, svc.config().webserver, svc._stack_web_proxies())
    block = _server_block(text, port)
    assert "allow 10.2.0.0/16;" in block and "10.1.0.0/16" not in block and "deny all;" in block


def _script_candidate(svc):
    import re
    text = open(svc._paths.under("config/files/firewall/firewall-apply.sh")).read()
    return json.loads(re.search(r"<<'LHPC_EOF_CANDIDATE'\n(.*?)\nLHPC_EOF_CANDIDATE", text, re.S).group(1))


def test_one_apply_after_the_update_is_enough_when_pages_are_re_entered(tmp_path, monkeypatch):
    # B2 #5 (A5, NOTE-STEP6-FW-v1 §4): red on 409869a2 (the decision alone) and on 085034cf — the update's reconcile
    # rendered the script, the operator re-entered a page (the upgrade step), ran the ONE apply (TRANSITIONAL: a 0.13.x
    # page the update does not carry), and `webserver apply` refused "Firewall changes pending" (H1: the saved intent
    # changed after the render). Now the re-entered page at its allocated port (meshcom@r1: 8445 on the default list)
    # with the console's networks is its pre-opened row: the intent the script carries is the saved one.
    lab = Lab(tmp_path, monkeypatch, (R1, R2))
    _remote_console(lab)
    paths = Paths(runtime_root=lab.root)
    cfgmod.save_stackweb_config(paths, "meshtastic@r1", mode="lan", port=9100, allowed_cidrs=(NET,))
    with monkeypatch.context() as m:
        m.setattr(fh, "integration_rev", lambda: "OLD-HELPER")             # 0.13.x applied it
        assert lab.apply() == fh.EXIT_OK
    cfgmod.save_stackweb_config(paths, "meshtastic@r1", port=0)             # the update does not carry that page
    svc = lab.svc()
    os.makedirs(svc._paths.under("state"), exist_ok=True)
    svc._fw_mark_post_update()
    svc.firewall_post_update_reconcile()                                  # the restarted console renders
    cfgmod.save_stackweb_config(paths, "meshcom@r1", mode="lan", port=8445, allowed_cidrs=(NET,))   # re-entered
    assert lab.apply(cand=_script_candidate(lab.svc())) == fh.EXIT_OK     # the ONE apply of the rendered script
    assert lab.snapshot()["transitional"]
    svc = lab.svc()
    applied = _nginx_stubs(monkeypatch, svc)
    st = svc.firewall_status()
    r = svc.webserver_apply()
    assert r.ok and applied == [1], (r.summary, {k: st[k] for k in ("config_ok", "live_ok", "transitional")})
    # anchor: a real intent change after the apply still refuses (the gate is not weakened)
    cfgmod.save_stackweb_config(paths, "graywolf@r1", mode="lan", port=9200, allowed_cidrs=(NET,))
    svc._invalidate_config()
    r = svc.webserver_apply()
    assert not r.ok and "Firewall changes pending" in r.summary and applied == [1]


def test_page_ports_are_unique_and_stable_and_avoid_the_console_and_every_listener(tmp_path, monkeypatch):
    from lhpc.core import listeners
    lab = Lab(tmp_path, monkeypatch, (R1, R2))
    svc = lab.svc()
    before = svc._page_ports()
    assert (before["graywolf@r1"], before["meshcom@r1"]) == (8444, 8445)  # the literals the one-click tests use
    _kiss(svc, {"kiss_port": str(before["meshcom@r1"])})                  # a listener takes a page's default
    ports = svc._page_ports()
    owned = set(listeners.stack_listeners(svc))
    assert before["meshcom@r1"] in owned and ports["meshcom@r1"] != before["meshcom@r1"]
    assert len(set(ports.values())) == len(ports)                         # no two pages share a port
    assert not set(ports.values()) & ({svc.config().webserver.port} | owned)
    for page, port in ports.items():                                      # saving at the allocation moves nobody
        assert svc.stack_web_configure(page, mode="local", port=port).ok
        assert svc._page_ports() == ports, page


@pytest.mark.parametrize("console", [65534, 65535])
def test_page_ports_never_take_a_taken_port_at_the_top_of_the_range(tmp_path, monkeypatch, console):
    # G1-FW-A21 B2: red on 8614a8c8, where an exhausted walk clamped every page to 65535 (the console's,
    # or one shared by all pages); the allocation never returns a taken port
    lab = Lab(tmp_path, monkeypatch, (R1, R2))
    cfgmod.save_webserver_config(Paths(runtime_root=lab.root), bind="0.0.0.0", port=console, remote_exposed=True,
                                 allowed_cidrs=[NET])
    svc = lab.svc()
    ports = svc._page_ports()
    assert len(ports) == len(svc._page_positions()) and len(set(ports.values())) == len(ports)
    assert console not in ports.values() and all(1024 <= p <= 65535 for p in ports.values())


def test_a_hand_edited_page_port_on_a_listener_port_is_never_preopened(tmp_path, monkeypatch):
    # G1-FW-A21 B2: the save refuses a page port another owner listens on; a hand edit does not, and then the
    # firewall must not open that listener's port to the console's networks
    from lhpc.core import listeners
    lab = Lab(tmp_path, monkeypatch, (R1, R2))
    _remote_console(lab)
    svc = lab.svc()
    owned = sorted(listeners.stack_listeners(svc))[0]
    cfgmod.save_stackweb_config(svc._paths, "meshcom@r1", mode="local", port=owned)    # no validation: a hand edit
    svc._invalidate_config()
    rows = _ingress(svc.firewall_candidate())
    assert owned not in [p for p, _c in rows]
    assert (svc._page_ports()["graywolf@r1"], (NET,)) in rows             # anchor: the other pages stay pre-opened


@pytest.mark.parametrize("bind, cidrs", [("0.0.0.0", []), ("0.0.0.0", ["0.0.0.0/0"])],
                         ids=["no-allowlist", "whole-internet"])   # an IPv6-only list: the console's save refuses it
def test_a_console_without_ipv4_networks_preopens_nothing(tmp_path, monkeypatch, bind, cidrs):
    # G1-FW-A21 B3: red on 8614a8c8, where the IPv4 filter of such a list left [] and the rows opened every
    # page to ANY source; an empty list never means "any" off an AP box
    lab = Lab(tmp_path, monkeypatch, (R1, R2))
    cfgmod.save_webserver_config(Paths(runtime_root=lab.root), bind=bind, port=8443, remote_exposed=True,
                                 allowed_cidrs=cidrs)
    svc = lab.svc()
    ports = set(svc._page_ports().values())
    rows = svc.firewall_candidate()["proxy_ingress"]
    assert [r for r in rows if r["port"] in ports] == []
    assert [r["port"] for r in rows] == [8443]                            # anchor: the console's own row stays


def test_preopened_rows_only_while_the_console_is_remote_and_one_row_per_page(tmp_path, monkeypatch):
    lab = Lab(tmp_path, monkeypatch, (R1, R2))
    svc = lab.svc()
    assert _ingress(svc.firewall_candidate()) == []                       # console local: nothing pre-opened
    _remote_console(lab)
    svc._invalidate_config()
    ports = svc._page_ports()
    rows = _ingress(svc.firewall_candidate())
    assert sorted(rows) == sorted([(8443, (NET,))] + [(p, (NET,)) for p in ports.values()])
    assert svc.stack_web_configure("graywolf@r1", mode="lan", port=ports["graywolf@r1"], cidrs=["10.0.0.0/8"],
                                   confirm=True).ok
    rows = _ingress(svc.firewall_candidate())
    assert [r for r in rows if r[0] == ports["graywolf@r1"]] == [(ports["graywolf@r1"], ("10.0.0.0/8",))]


def test_preopened_rows_are_unscoped_on_an_ap_box(tmp_path, monkeypatch):
    lab = Lab(tmp_path, monkeypatch, (R1, R2))
    _remote_console(lab)
    svc = lab.svc()
    assert svc.firewall_configure(ap_enabled=True, ap_interface="wlan0", ap_cidr="10.42.0.0/24").ok
    rows = _ingress(svc.firewall_candidate())
    assert len(rows) == 1 + len(svc._page_ports()) and all(c == () for _p, c in rows)


def test_the_console_removal_narrowing_holds_with_preopened_rows(tmp_path, monkeypatch):
    # kept: closing the console passes on the receipt alone (no applied evidence) while a page stays exposed
    lab = _box(tmp_path, monkeypatch)
    svc = lab.svc()
    _nginx_stubs(monkeypatch, svc)
    assert _expose(svc, "graywolf@r1", cidrs=("10.0.0.0/8",)).ok
    assert lab.apply() == fh.EXIT_OK
    cfgmod.save_webserver_config(Paths(runtime_root=lab.root), bind="127.0.0.1", remote_exposed=False)
    svc = lab.svc()
    monkeypatch.setattr(svc, "_fw_applied_candidate", lambda: None)       # the receipt's branch, on its own
    monkeypatch.setattr("lhpc.core.webserver.listener_addresses", lambda system, port: ["0.0.0.0"])
    assert svc._narrowing_is_console_removal_only()
    assert svc.firewall_gate_activation(svc._prospective_nginx_ports())[0]


# --- caps and bounds (§4): a full 64-radio list fits, a snapshot the reader could not read is never written --------

def _full_list(lab, long_cidrs):
    """64 radios, every knob listener exposed on each, every page remote (evidence-caps-v1/m7, v3/m9)."""
    from lhpc.core.commands import Instance
    rows = [R1, R2] + [radios.Radio(f"r{i}", f"n{i}", "ch341-rfm98pw-433" if i % 2 else "pinedio-usb-1262",
                                    "433" if i % 2 else "868", usb_serial=f"S{i:04d}XYZW") for i in range(3, 65)]
    lab.radios(rows)
    paths = Paths(runtime_root=lab.root)
    nets = (lambda n: [f"172.{16 + j % 16}.{n % 256}.{(n * 7 + j) % 256}/32" for j in range(32)]) if long_cidrs \
        else (lambda n: [NET])
    for r in rows:
        n = int(r.id[1:])
        cfgmod.update_stack_config(paths, "kiss", {"kiss_host": "0.0.0.0", "kiss_bind": ",".join(nets(n))},
                                   inst=Instance(r.id))
        cfgmod.update_stack_config(paths, "meshcom", {"bind": "0.0.0.0"}, inst=Instance(r.id))
        cfgmod.update_stack_config(paths, "meshcore", {"meshcore_allow": ",".join(nets(n + 100))}, inst=Instance(r.id))
    cfgmod.save_webserver_config(paths, bind="0.0.0.0", remote_exposed=True,
                                 allowed_cidrs=[f"10.{i}.0.0/16" for i in range(32)] if long_cidrs else [NET])
    for n, page in enumerate(lab.svc().web_pages()):
        cfgmod.save_stackweb_config(paths, page.page_id, mode="lan", port=30000 + n, allowed_cidrs=tuple(nets(n + 200)))
    return lab.svc()


@pytest.mark.parametrize("long_cidrs", [False, True], ids=["m7-one-network", "m9-32-networks"])
def test_a_full_64_radio_list_applies_within_every_bound(tmp_path, monkeypatch, long_cidrs):
    # red on 085034cf (evidence-caps-v1/m7): 256 endpoint rows / 226 ingress were refused over the 128 / 32 caps
    lab = Lab(tmp_path, monkeypatch, (R1, R2))
    svc = _full_list(lab, long_cidrs)
    cand = svc.firewall_candidate()
    assert (len(cand["endpoints"]), len(cand["proxy_ingress"])) == (256, 226)
    assert fw.validate_candidate(cand) == [] and fh.validate_candidate(cand) == []
    assert len(json.dumps(cand).encode()) <= fh.MAX_CANDIDATE_BYTES
    assert lab.apply() == fh.EXIT_OK
    assert os.path.getsize(fw.SNAPSHOT_DEST) <= fw.MAX_SNAPSHOT_BYTES
    assert lab.svc()._fw_applied_candidate() is not None                 # its reader reads it back
    assert _gate(lab.svc(), "kiss", "r64")[0]


@pytest.mark.parametrize("key, cap", [("endpoints", "MAX_ENDPOINTS"), ("proxy_ingress", "MAX_PROXY_INGRESS")])
def test_one_row_over_a_cap_is_refused_by_name_on_both_sides(key, cap):
    row = (lambda i: _ep(id=f"x{i}.tcp-1", port=1 + i)) if key == "endpoints" else \
        (lambda i: {"proto": "tcp", "family": "ipv4", "addr": "*", "port": 1 + i, "allow_cidrs": []})
    at = _candidate(**{key: [row(i) for i in range(getattr(fw, cap))]})
    over = _candidate(**{key: [row(i) for i in range(getattr(fw, cap) + 1)]})
    assert fw.validate_candidate(at) == [] and fh.validate_candidate(at) == []
    assert fw.validate_candidate(over) == [f"{key}: too many entries (max {getattr(fw, cap)})"]
    assert fh.validate_candidate(over) == [f"{key}: too many entries"]


def test_the_bounds_are_one_value_on_both_sides():
    assert (fw.MAX_ENDPOINTS, fw.MAX_PROXY_INGRESS, fw.MAX_SNAPSHOT_BYTES) == \
        (fh.MAX_ENDPOINTS, fh.MAX_PROXY_INGRESS, fh.MAX_SNAPSHOT_BYTES) == (512, 512, 8 * 1024 * 1024)
    assert fh.MAX_CANDIDATE_BYTES == 1024 * 1024


def test_a_snapshot_over_the_bound_is_refused_before_any_live_change(tmp_path, monkeypatch, capsys):
    lab = _box(tmp_path, monkeypatch)
    before = {p: open(p).read() for p in (fw.SNAPSHOT_DEST, fw.RECEIPT_PATH)}
    lab.radios((R1, R2, R3))
    monkeypatch.setattr(fh, "MAX_SNAPSHOT_BYTES", len(json.dumps(lab.snapshot(), sort_keys=True)) + 1)
    sysx = _Sys()
    assert lab.apply(sysx) == fh.EXIT_CANDIDATE
    assert "over MAX_SNAPSHOT_BYTES" in capsys.readouterr().out
    assert not any(c[:2] == ["nft", "-f"] for c in sysx.calls)            # nothing loaded
    assert not os.path.exists(fw.JOURNAL_DEST)
    assert {p: open(p).read() for p in before} == before                  # the applied evidence is untouched
