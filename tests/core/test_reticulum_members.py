"""Radios joined to the running Reticulum node, restart-free (plan reti v21 §1c-§1d, B5 design note v2): a start of a
further radio offers Connect; Connect = its intent + the ordinary start, whose rns step attaches (rnstatus --attach,
verified in -j, a member record on the owner's process) and launches nothing; a member's Stop detaches it and never
signals; the owner's Stop ends the memberships; every owner start reconciles the intended members; a member's saved
settings are checked first and reloaded live. The node's RPC is a fake (`Node`); its processes are real sleeps."""
from __future__ import annotations

import json
import re
import os
import subprocess

import pytest
import radios_fixture
from seams import seed_framing_driver

from lhpc.adapters.cli import main as cli
from lhpc.core import instances, kill
from lhpc.core.lifecycle import Lifecycle
from lhpc.core.outcomes import Outcome
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import CommandResult, FakeSystem
from lhpc.core.services import ControllerService

PINE = radios_fixture.BOX_H[3]                                    # r4, PineDio 868 (USB): the daemon backend


class Node:
    """The running node's `rnstatus` and the interface's section check, as the box answers them."""

    def __init__(self, base):
        self.base, self.attached, self.calls = base, {"LoRa r2"}, []
        self.refuse_attach, self.unlisted, self.refuse_reload, self.check_line = set(), set(), set(), ""
        self.j_down, self.j_down_after = False, set()   # `-j` unanswered (a hung or busy RPC); from the op named on

    def run(self, argv, timeout, cwd=None, env=None):
        tool = os.path.basename(argv[0])
        if tool == "loraham-rns-check-section":
            block = open(argv[1]).read()
            self.calls.append(("check", block))
            line = self.check_line or ("passphrase: IFAC needs networkname and passphrase"   # the driver's half-IFAC rule
                                       if "networkname" in block and "passphrase" not in block else "")
            return CommandResult(1, line + "\n", "") if line else CommandResult(0, "", "")
        if tool != "rnstatus":
            return self.base.run(argv, timeout, cwd=cwd, env=env)
        args = argv[3:]
        self.calls.append(tuple(args))
        if args == ["-j"]:
            if self.j_down:
                return CommandResult(1, "", "No shared RNS instance available to get status from")
            return CommandResult(0, json.dumps({"interfaces": [{"name": f"LoRaSPIInterface[{n}]"}
                                                               for n in sorted(self.attached)]}), "")
        op, name = args
        self.j_down = self.j_down or op in self.j_down_after
        if op == "--attach" and name not in self.refuse_attach:
            self.attached |= {name} - self.unlisted
            return CommandResult(0, f"Interface {name} was attached", "")
        if op == "--detach":
            self.attached.discard(name)
            return CommandResult(0, "", "")
        if op == "--reload" and name not in self.refuse_reload:
            return CommandResult(0, "", "")
        return CommandResult(1, f"Could not {op[2:]} interface {name}", "")


@pytest.fixture
def box(tmp_path, monkeypatch):
    """Box H's r1 (433) and r2 (868) on the direct driver and the r4 stick: the node starts on r2 (the owner)."""
    spawned = []

    def spawn(argv, log, cwd=None, env=None):
        open(str(log), "a").close()
        spawned.append(subprocess.Popen(["sleep", "120"], start_new_session=True,   # wallclock: a live launch, ended after the test; never paces
                                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        return spawned[-1].pid
    radios_fixture.write(tmp_path, (*radios_fixture.RADIOS, PINE))
    radios_fixture.reticulum_backend(tmp_path, ["r1", "r2"])
    seed_framing_driver(tmp_path)
    for name, value in (("hardware_configured", lambda self: True), ("radio_bands", lambda self: ["433", "868"]),
                        ("is_built", lambda self, c: True), ("_build_complete", lambda self, c: True),
                        ("_source_present", lambda self, c: True)):
        monkeypatch.setattr(ControllerService, name, value)
    monkeypatch.setattr(Lifecycle, "missing_requirements", lambda self, comp: [])
    fake = FakeSystem(paths={str(tmp_path / "state" / "reticulum" / "ready")})
    node = Node(FakeSystem())
    monkeypatch.setattr(fake, "run", node.run)
    svc = ControllerService(system=fake.system, paths=Paths(runtime_root=tmp_path))
    monkeypatch.setattr(ControllerService, "_lifecycle",
                        lambda self: Lifecycle(self._paths, self.stacks(), self.config(), self._system, spawn=spawn))
    monkeypatch.setattr(cli, "ControllerService", lambda *a, **k: svc)
    box = type("Box", (), {"svc": svc, "node": node, "spawned": spawned, "root": tmp_path})
    yield box
    for p in spawned:
        p.terminate()
        p.wait()


def _start(box, rid):
    return box.svc.start("reticulum", apply=True, inst=instances.copy_of(box.svc, "reticulum", rid)[0])


def _member(box, rid):
    return [r for r in box.svc._lifecycle().owned_records("rns", role=None, rid=rid) if r.get("member_of")]


def _intent(box, rid):
    return instances.member_of(box.svc, "reticulum", rid)


def _sections(box):
    return [ln.strip() for ln in (box.root / "state" / "reticulum" / "config").read_text().splitlines()
            if ln.strip()[:7] == "[[LoRa "]


def _owner_up(box):
    res = _start(box, "r2")
    assert res.ok, res.summary + "\n".join(res.details)
    return box.spawned[-1]


def test_a_start_of_another_radio_offers_connect_and_names_the_join(box):
    _owner_up(box)
    plan = box.svc.start("reticulum", inst=instances.copy_of(box.svc, "reticulum", "r1")[0])
    # red-before (064363de): "'reticulum' runs one instance at a time (one node, fixed ports): reticulum@r2 runs"
    assert not plan.ok and plan.summary == "Reticulum runs on r2. Connect r1 to that node? (live, no restart)"
    assert plan.next_commands == ["lhpc stack start reticulum@r1 --join --yes"]


def test_join_attaches_verifies_records_and_launches_nothing(box, capsys):
    owner = _owner_up(box)
    launched = len(box.spawned)
    # red-before (064363de): `--join` is no option of `lhpc stack start` (usage error, rc 2)
    assert cli.main(["stack", "start", "reticulum@r1", "--join", "--yes"]) == 0, capsys.readouterr()
    assert ("--attach", "LoRa r1") in box.node.calls and ("-j",) in box.node.calls
    assert [r["pid"] for r in _member(box, "r1")] == [owner.pid] and _intent(box, "r1") == "r2"
    assert _sections(box) == ["[[LoRa r2]]", "[[LoRa r1]]"] and len(box.spawned) == launched   # S3: nothing launched


def test_a_join_starts_none_of_the_member_radios_ticked_node_clients(box, capsys):
    # a25 B5 F1: nomadnet, lxmd, sideband, meshchat are the reticulum stack's optional parts, ticked per radio
    _owner_up(box)
    (box.root / "config" / "stacks" / "reticulum@r1.toml").write_text(
        'backend = "spi"\nautostart_lxmd = "on"\nautostart_meshchat = "on"\n')   # r1 once ran alone with clients
    launched = len(box.spawned)
    # red-before (22e00f73): the plan starts lxmd and meshchat for r1, a second lxmd is launched
    assert cli.main(["stack", "start", "reticulum@r1", "--join", "--yes"]) == 0, capsys.readouterr()
    out = capsys.readouterr().out
    assert "lxmd" not in out and "meshchat" not in out and len(box.spawned) == launched
    assert _member(box, "r1")


def test_an_attach_the_node_does_not_list_is_refused_and_leaves_nothing(box, capsys):
    _owner_up(box)
    box.node.unlisted = {"LoRa r1"}                                 # rc 0, but RNS dropped the interface (plan §1c (a))
    assert cli.main(["stack", "start", "reticulum@r1", "--join", "--yes"]) == 1
    assert "did not attach LoRa r1" in capsys.readouterr().out
    assert (_sections(box), _member(box, "r1"), _intent(box, "r1")) == (["[[LoRa r2]]"], [], "")


def test_a_section_the_interface_refuses_is_never_written_or_attached(box, capsys):
    _owner_up(box)
    before = (box.root / "state" / "reticulum" / "config").read_text()
    box.node.check_line = "txpower: 2..10 dBm on 433"
    assert cli.main(["stack", "start", "reticulum@r1", "--join", "--yes"]) == 1
    assert "txpower: 2..10 dBm on 433" in capsys.readouterr().out
    assert not [c for c in box.node.calls if c[:1] == ("--attach",)]
    assert (box.root / "state" / "reticulum" / "config").read_text() == before


def test_a_member_stop_detaches_and_never_signals_the_node(box, capsys):
    owner = _owner_up(box)
    assert cli.main(["stack", "start", "reticulum@r1", "--join", "--yes"]) == 0
    res = box.svc.stop("reticulum", apply=True, inst=instances.copy_of(box.svc, "reticulum", "r1")[0])
    # red-before (064363de): the member record's stop SIGTERMs the owner's process group
    assert res.ok and owner.poll() is None and ("--detach", "LoRa r1") in box.node.calls
    assert (_sections(box), _member(box, "r1"), _intent(box, "r1")) == (["[[LoRa r2]]"], [], "")


def test_the_owners_stop_ends_the_memberships(box):
    owner = _owner_up(box)
    assert cli.main(["stack", "start", "reticulum@r1", "--join", "--yes"]) == 0
    res = box.svc.stop("reticulum", apply=True, inst=instances.copy_of(box.svc, "reticulum", "r2")[0])
    assert res.ok and owner.wait(timeout=10) is not None
    assert (_member(box, "r1"), _intent(box, "r1")) == ([], "")


def test_no_generic_stop_or_kill_signals_through_a_member_record(box):
    owner = _owner_up(box)
    assert cli.main(["stack", "start", "reticulum@r1", "--join", "--yes"]) == 0
    life, rns = box.svc._lifecycle(), box.svc.stack("reticulum").component("rns")
    cr = life.stop(rns, rid="r1")                                    # a member's record alone
    # red-before (064363de): STOPPED, the owner's process group signalled
    assert cr.outcome is Outcome.MANUAL_REQUIRED and owner.poll() is None and _member(box, "r1")
    res = kill.kill(box.svc, "reticulum@r1", apply=True)
    assert not res.ok and "member of the node on r2" in res.summary and owner.poll() is None
    cr = life.stop(rns)                                              # the rollback's form: no radio, owner + member
    assert cr.outcome is Outcome.STOPPED and owner.wait(timeout=10) is not None and _member(box, "r1") == []


def test_every_owner_start_reconciles_its_members_and_a_failed_one_never_fails_it(box, tmp_path):
    radios_fixture.reticulum_backend(tmp_path, [])                  # (the dir)
    (tmp_path / "config" / "stacks" / "reticulum@r1.toml").write_text('backend = "spi"\nmember_of = "r2"\n')
    (tmp_path / "config" / "stacks" / "reticulum@r4.toml").write_text(
        'member_of = "r2"\nfile_ifac_netname = "labnet"\n')         # IFAC without its passphrase: the interface refuses r4
    box.node.refuse_attach = {"LoRa r1"}
    res = _start(box, "r2")
    # red-before (064363de): no member is attached at an owner start
    assert res.ok, res.summary
    text = "\n".join(res.details)
    assert "[member] r1: not connected — the node did not attach LoRa r1" in text
    assert "[member] r4: not connected — " in text and "passphrase" in text          # a25 B3 N1 / B5 C4
    assert (_member(box, "r1"), _member(box, "r4"), _intent(box, "r1"), _intent(box, "r4")) == ([], [], "r2", "r2")
    box.node.refuse_attach = set()
    assert cli.main(["stack", "start", "reticulum@r1", "--join", "--yes"]) == 0     # its [Connect] retries it
    assert [r["rid"] for r in _member(box, "r1")] == ["r1"]


def test_a_live_members_settings_are_checked_first_and_reloaded_live(box):
    _owner_up(box)
    assert cli.main(["stack", "start", "reticulum@r1", "--join", "--yes"]) == 0
    r1 = instances.copy_of(box.svc, "reticulum", "r1")[0]
    box.node.check_line = "frequency: 433050000-434790000 Hz"
    res = box.svc.save_config("reticulum", {"file_frequency": "430000000"}, inst=r1)
    # red-before (064363de): saved, nothing checked, nothing reloaded
    assert not res.ok and "the interface would refuse these settings" in " ".join(res.details)
    box.node.check_line = ""
    res = box.svc.save_config("reticulum", {"file_frequency": "434000000"}, inst=r1)
    assert res.ok and ("--reload", "LoRa r1") in box.node.calls and "[member] r1: reloaded live" in "\n".join(res.details)
    box.node.refuse_reload = {"LoRa r1"}
    res = box.svc.save_config("reticulum", {"file_frequency": "434100000"}, inst=r1)
    assert res.ok and "not connected: the node refused the new settings" in "\n".join(res.details)
    # red-before (ea51424b): only the record goes, the refused interface is never detached (plan v25 §9.2)
    assert ("--detach", "LoRa r1") in box.node.calls and "LoRa r1 detached" in "\n".join(res.details)
    assert _member(box, "r1") == [] and _intent(box, "r1") == "r2"


def test_a_member_record_is_no_boot_restore_item_of_its_own(box):
    _owner_up(box)
    assert cli.main(["stack", "start", "reticulum@r1", "--join", "--yes"]) == 0
    evidence = box.svc._classify_boot_evidence("another-boot")[0]
    # red-before (064363de): the member's record is its own item (reticulum@r1), refused at restore
    assert sorted((e.stack, e.rid) for e in evidence if e.stack == "reticulum") == [("reticulum", "r2")]


def test_a_failed_join_releases_the_daemon_band_it_brought_up(tmp_path):
    # a25 C2: the join's rns step failed after its daemon band started; the node's own record (another radio's) keeps
    # no member's daemon up. Called as test_process_ownership's roll-back tests call it (a start cannot arrange this).
    # red-before (064363de): TypeError (no `joined`); before B5 the owner's record held the band: BLOCKED, left running
    import types

    from lhpc.core.outcomes import CompResult
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    order = [(None, types.SimpleNamespace(id=c)) for c in ("loraham-daemon", "rns")]

    class Life:
        stopped = []

        def stop(self, c, band=""):
            self.stopped.append((c.id, band))
            return CompResult(component=c.id, action="stop", outcome=Outcome.STOPPED, summary="stopped")

        def owned_records(self, cid, rid=None):
            return [{"component": cid, "rid": "r2"}] if cid == "rns" and rid in (None, "r2") else []
    results = [CompResult(component="loraham-daemon", action="start", outcome=Outcome.VERIFIED),
               CompResult(component="rns", action="start", outcome=Outcome.BLOCKED, summary="not connected")]
    life = Life()
    svc._roll_back_start(life, order, results, "", {"868"}, "r4")
    assert life.stopped == [("loraham-daemon", "868")]


# ---- the cards (B6 design note v1 C1-C4) ----------------------------------------------------------------------------

def _home(box, web):
    client = web(service_factory=lambda: box.svc)
    return client, client.get("/", headers={"Host": "localhost:8443"}).get_data(as_text=True)


def _forms(html, target):
    """[(op, button)] of the rendered forms posting `target`."""
    return re.findall(rf'name="op" value="([a-z]+)">\s*<input type="hidden" name="target" value="{re.escape(target)}">'
                      r'.*?<button type="submit">([^<]+)</button>', html, re.S)


def test_a_connected_radios_card_shows_its_node_and_disconnect(box, web):
    _owner_up(box)
    assert cli.main(["stack", "start", "reticulum@r1", "--join", "--yes"]) == 0
    _c, html = _home(box, web)
    # red-before (f4ff210c): r1 has no card (the one-node stack gives Home its owner only)
    assert "Connected to the Reticulum node on r2" in html
    assert ("stop", "Disconnect") in _forms(html, "reticulum@r1")
    box.node.attached.discard("LoRa r1")                                     # the record stays, the node lost it
    _c, html = _home(box, web)
    assert "Not connected — not attached — see the Reticulum log" in html   # live = the record AND the node's list


def test_a_not_connected_radios_card_names_why_and_offers_connect_and_remove(box, web):
    _owner_up(box)
    (box.root / "config" / "stacks" / "reticulum@r1.toml").write_text('backend = "spi"\nmember_of = "r2"\n')
    _c, html = _home(box, web)
    # red-before (f4ff210c): no card, no reason
    assert "Not connected — not attached — see the Reticulum log" in html
    assert _forms(html, "reticulum@r1") == [("join", "Connect"), ("stop", "Remove")]
    res = box.svc.stop("reticulum", apply=True, inst=instances.copy_of(box.svc, "reticulum", "r1")[0])
    assert res.ok and "r1 removed from the Reticulum node (it was not connected)" in " ".join(res.details)   # C3
    assert _intent(box, "r1") == "" and not [c for c in box.node.calls if c[:1] == ("--detach",)]


def test_a_member_less_box_never_asks_the_node(box, web):
    _owner_up(box)
    before = box.node.calls.count(("-j",))
    _home(box, web)
    assert box.node.calls.count(("-j",)) == before                           # a25 B6 C1


def test_with_the_node_down_the_card_says_so(box, web):
    (box.root / "config" / "stacks" / "reticulum@r1.toml").write_text('backend = "spi"\nmember_of = "r2"\n')
    _c, html = _home(box, web)
    assert "Not connected — the node on r2 is not running" in html


def test_one_node_answer_serves_every_card_of_a_page(box, web):
    _owner_up(box)
    stores = {"r1": 'backend = "spi"\nmember_of = "r2"\n', "r4": 'member_of = "r2"\n'}
    for rid, text in stores.items():
        (box.root / "config" / "stacks" / f"reticulum@{rid}.toml").write_text(text)
    before = box.node.calls.count(("-j",))
    _c, html = _home(box, web)
    assert box.node.calls.count(("-j",)) - before == 1                       # a25 B5 N1: once per request
    assert "Not connected — daemon@r4 is not ready" in html                  # r4 on the daemon backend, no daemon


def test_connect_from_the_web_joins_and_a_failed_one_leaves_nothing(box, web, csrf):
    _owner_up(box)
    client = web(service_factory=lambda: box.svc)
    box.node.unlisted = {"LoRa r1"}
    client.post("/action", data={"_csrf": csrf(client), "op": "join", "target": "reticulum@r1", "confirmed": "yes"})
    # red-before (f4ff210c): op join is a bad request (400)
    assert (_member(box, "r1"), _intent(box, "r1")) == ([], "")
    box.node.unlisted = set()
    client.post("/action", data={"_csrf": csrf(client), "op": "join", "target": "reticulum@r1", "confirmed": "yes"})
    assert _member(box, "r1") and _intent(box, "r1") == "r2"


def test_a_web_start_of_another_radio_asks_connect_and_the_owners_stop_names_its_members(box, web, csrf):
    _owner_up(box)
    client = web(service_factory=lambda: box.svc)
    page = client.post("/action", data={"_csrf": csrf(client), "op": "start", "target": "reticulum@r1"}).get_data(as_text=True)
    # red-before (f4ff210c): a flash of the refusal, no Connect
    assert 'name="op" value="join"' in page and "Connect r1 to that node?" in page
    assert cli.main(["stack", "start", "reticulum@r1", "--join", "--yes"]) == 0
    page = client.post("/action", data={"_csrf": csrf(client), "op": "stop", "target": "reticulum@r2"}).get_data(as_text=True)
    assert "[also disconnects] reticulum@r1: a radio of this node" in page
    res = box.svc.stop("reticulum", inst=instances.copy_of(box.svc, "reticulum", "r1")[0])
    assert "[removes]" not in " ".join(res.details) and "[disconnects] r1 from the Reticulum node" in " ".join(res.details)


def test_the_dashboard_signature_follows_the_members_without_asking_the_node(box):
    _owner_up(box)
    (box.root / "config" / "stacks" / "reticulum@r1.toml").write_text('backend = "spi"\nmember_of = "r2"\n')
    box.svc.invalidate_snapshot()
    sig, calls = box.svc.dash_signature(), len(box.node.calls)
    assert box.svc.stop("reticulum", apply=True, inst=instances.copy_of(box.svc, "reticulum", "r1")[0]).ok   # Remove
    box.svc.invalidate_snapshot()
    # red-before (f4ff210c): the signature does not change when an intent goes (the page would not reload)
    assert box.svc.dash_signature() != sig and not [c for c in box.node.calls[calls:] if c == ("-j",)]


# ---- restart once per node (B7) ---------------------------------------------------------------------------------------

def test_an_owner_restart_restarts_the_node_once_and_its_members_come_back(box):
    owner = _owner_up(box)
    assert cli.main(["stack", "start", "reticulum@r1", "--join", "--yes"]) == 0
    launched = len(box.spawned)
    res = box.svc.restart("reticulum", apply=True, inst=instances.copy_of(box.svc, "reticulum", "r2")[0])
    # red-before (bbaf96b6): the restart's stop ended the memberships: r1 not back, its intent gone
    assert res.ok, res.summary + "\n".join(res.details)
    assert owner.wait(timeout=10) is not None and len(box.spawned) == launched + 1      # one node launch, no member's
    assert [r["pid"] for r in _member(box, "r1")] == [box.spawned[-1].pid] and _intent(box, "r1") == "r2"


def test_a_member_restart_rejoins_the_node(box):
    owner = _owner_up(box)
    assert cli.main(["stack", "start", "reticulum@r1", "--join", "--yes"]) == 0
    res = box.svc.restart("reticulum", apply=True, inst=instances.copy_of(box.svc, "reticulum", "r1")[0])
    # red-before (bbaf96b6): its stop cleared the intent, so its start was the Connect offer (refused)
    assert res.ok, res.summary + "\n".join(res.details)
    assert owner.poll() is None and _member(box, "r1") and _intent(box, "r1") == "r2"


# ---- the daemon panel of a node radio (plan v22 §8.3 B2b) -------------------------------------------------------------

def test_a_joined_radios_panel_save_is_checked_first_and_reloads_it_live(box):
    from lhpc.core import radio_start, reticulum_members
    _owner_up(box)
    reticulum_members._save_intent(box.svc, "r4", "r2")
    assert reticulum_members.attach(box.svc, "r2", "r4") == ""          # the join's rns step (r4's daemon: not here)
    box.node.check_line = "txpower: above the 868 MHz band cap"
    res = box.svc.save_daemon_params("reticulum", {"POWER": "13"}, rid="r4")
    # red-before (da24e83c): saved unchecked, nothing reloaded (the interface keeps its start-time section)
    assert not res.ok and "the interface would refuse these settings: txpower" in res.summary, res.summary
    assert box.node.calls[-1][0] == "check" and re.search(r"txpower\s*=\s*13\b", box.node.calls[-1][1])
    assert "POWER" not in radio_start.instance_dp(box.svc, "reticulum", "r4")
    box.node.check_line, calls = "", len(box.node.calls)
    res = box.svc.save_daemon_params("reticulum", {"POWER": "12"}, rid="r4")
    assert res.ok and "r4: reloaded live" in res.summary and ("--reload", "LoRa r4") in box.node.calls[calls:]
    assert box.svc.restart_required("reticulum@r4") is None and _member(box, "r4")


# ---- row 218 fold (plan v25 §9): an unanswered `-j` is not "gone" -----------------------------------------------------

def test_a_member_stop_the_node_does_not_answer_about_is_refused_and_keeps_it(box, capsys):
    _owner_up(box)
    assert cli.main(["stack", "start", "reticulum@r1", "--join", "--yes"]) == 0
    box.node.j_down = True
    res = box.svc.stop("reticulum", apply=True, inst=instances.copy_of(box.svc, "reticulum", "r1")[0])
    # red-before (ea51424b): stopped, no --detach asked, block + record + intent gone while the node may still run r1
    assert not res.ok and "did not detach LoRa r1 (the node does not answer)" in "\n".join(res.details), res.details
    assert ("--detach", "LoRa r1") in box.node.calls
    assert (_sections(box), len(_member(box, "r1")), _intent(box, "r1")) == (["[[LoRa r2]]", "[[LoRa r1]]"], 1, "r2")
    box.node.j_down = False
    assert box.svc.stop("reticulum", apply=True, inst=instances.copy_of(box.svc, "reticulum", "r1")[0]).ok
    assert (_sections(box), _member(box, "r1"), _intent(box, "r1")) == (["[[LoRa r2]]"], [], "")


def test_a_member_of_a_node_that_is_gone_is_removed_without_asking_it(box):
    owner = _owner_up(box)
    assert cli.main(["stack", "start", "reticulum@r1", "--join", "--yes"]) == 0
    owner.terminate()
    owner.wait(timeout=10)
    calls = len(box.node.calls)
    res = box.svc.stop("reticulum", apply=True, inst=instances.copy_of(box.svc, "reticulum", "r1")[0])
    # red-before (ea51424b): the dead node is asked (`-j`, `--detach`) for a member it can no longer hold
    assert res.ok and box.node.calls[calls:] == []
    assert (_member(box, "r1"), _intent(box, "r1")) == ([], "")


def test_an_attach_the_node_does_not_confirm_is_detached_and_stays_tracked(box, capsys):
    _owner_up(box)
    box.node.j_down_after = {"--attach"}                            # attached (rc 0), then `-j` no longer answers
    assert cli.main(["stack", "start", "reticulum@r1", "--join", "--yes"]) == 1
    out = capsys.readouterr().out
    # red-before (ea51424b): the block removed without a --detach, no record, the intent dropped: r1 abandoned live
    assert "did not attach LoRa r1" in out and "it stays tracked" in out, out
    assert ("--detach", "LoRa r1") in box.node.calls
    assert (_sections(box), len(_member(box, "r1")), _intent(box, "r1")) == (["[[LoRa r2]]", "[[LoRa r1]]"], 1, "r2")
    box.node.j_down, box.node.j_down_after = False, set()           # the node answers again: its [Connect] ...
    assert cli.main(["stack", "start", "reticulum@r1", "--join", "--yes"]) == 0, capsys.readouterr()
    assert len(_member(box, "r1")) == 1                             # ... keeps the one record (plan v25 §9.2)
    assert box.svc.stop("reticulum", apply=True, inst=instances.copy_of(box.svc, "reticulum", "r1")[0]).ok   # Remove
    assert (_sections(box), _member(box, "r1"), _intent(box, "r1")) == (["[[LoRa r2]]"], [], "")


def test_a_reload_the_node_does_not_confirm_keeps_the_member_record(box):
    _owner_up(box)
    assert cli.main(["stack", "start", "reticulum@r1", "--join", "--yes"]) == 0
    r1 = instances.copy_of(box.svc, "reticulum", "r1")[0]
    box.node.j_down = True                                          # both reloads rc 0, neither confirmed
    res = box.svc.save_config("reticulum", {"file_frequency": "434100000"}, inst=r1)
    text = "\n".join(res.details)
    # red-before (ea51424b): the record removed although the interface may run (both reloads succeeded)
    assert res.ok and "not connected: the node refused the new settings" in text and "it stays tracked" in text, text
    assert len(_member(box, "r1")) == 1 and _intent(box, "r1") == "r2"
