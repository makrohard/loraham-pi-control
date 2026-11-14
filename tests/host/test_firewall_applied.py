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
from firewall_fixture import _expected_live_json, _FakeSys, _seed_meta

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

    def apply(self, sysx=None):
        """The operator's `sudo bash firewall-apply.sh`: the helper applies the saved candidate. Returns its rc."""
        cand = self.svc().firewall_candidate()
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
        res = _start(svc, "meshtastic", rid)
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
