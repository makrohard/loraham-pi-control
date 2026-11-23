"""Radios joined to the running Reticulum node, restart-free (plan reti v21 §1c-§1d, B5 design note v2): a start of a
further radio offers Connect; Connect = its intent + the ordinary start, whose rns step attaches (rnstatus --attach,
verified in -j, a member record on the owner's process) and launches nothing; a member's Stop detaches it and never
signals; the owner's Stop ends the memberships; every owner start reconciles the intended members; a member's saved
settings are checked first and reloaded live. The node's RPC is a fake (`Node`); its processes are real sleeps."""
from __future__ import annotations

import dataclasses
import json
import re
import os
import subprocess

import pytest
import radios_fixture
from seams import seed_framing_driver

from lhpc.adapters.cli import main as cli
from lhpc.core import instances, kill, radios
from lhpc.core import reticulum_members as _rm
from lhpc.core.lifecycle import Lifecycle
from lhpc.core.outcomes import Outcome
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import CommandResult, FakeSystem
from lhpc.core.services import ControllerService

PINE = radios_fixture.BOX_H[3]                                    # r4, PineDio 868 (USB): the daemon backend
SPI868 = (radios.Radio("r1", "b", "uputronics", "868", ce="ce1"),   # two SPI radios on 868 (Reticulum runs on 868 only)
          radios.Radio("r2", "a", "uputronics", "868", ce="ce0"))


# The refusals of the pinned interface's `loraham-rns-check-section` (rns-lora-interface f196175, section.py main/check)
# a member test can meet: a block of another type, a missing required key, a half IFAC.
_REQUIRED = ("band", "conf_socket", "data_socket", "frequency")


def _real_refusal(block: str) -> str:
    keys = dict(ln.strip().split(" = ", 1) for ln in block.splitlines() if " = " in ln and ln.strip()[:1] != "#")
    if keys.get("type") != "LoRaDaemonInterface":
        return f"type: {keys.get('type')!r} is not LoRaDaemonInterface"
    if missing := [k for k in _REQUIRED if not keys.get(k)]:
        return f"{missing[0]}: is required"
    if bool(keys.get("networkname")) != bool(keys.get("passphrase")):
        return "passphrase: IFAC needs both networkname and passphrase, or neither"
    return ""


class Node:
    """The running node's `rnstatus` and the interface's section check, as the box answers them."""

    def __init__(self, base):
        self.base, self.attached, self.calls = base, {"LoRa r2"}, []
        self.refuse_attach, self.unlisted, self.refuse_reload, self.check_line = set(), set(), set(), ""
        self.j_down, self.j_down_after = False, set()   # `-j` unanswered (a hung or busy RPC); from the op named on
        self.j_miss, self.j_miss_after = 0, {}           # `-j` unanswered N times after the op named

    def run(self, argv, timeout, cwd=None, env=None):
        tool = os.path.basename(argv[0])
        if tool == "loraham-rns-check-section":
            block = open(argv[1]).read()
            self.calls.append(("check", block))
            line = self.check_line or _real_refusal(block)
            return CommandResult(1, line + "\n", "") if line else CommandResult(0, "", "")
        if tool != "rnstatus":
            return self.base.run(argv, timeout, cwd=cwd, env=env)
        args = argv[3:]
        self.calls.append(tuple(args))
        if args == ["-j"]:
            if self.j_miss:
                self.j_miss -= 1
                return CommandResult(1, "", "No shared RNS instance available to get status from")
            if self.j_down:
                return CommandResult(1, "", "No shared RNS instance available to get status from")
            return CommandResult(0, json.dumps({"interfaces": [{"name": f"LoRaSPIInterface[{n}]"}
                                                               for n in sorted(self.attached)]}), "")
        op, name = args
        self.j_down = self.j_down or op in self.j_down_after
        self.j_miss = self.j_miss_after.get(op, self.j_miss)
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
    """Two SPI radios on 868 (r1, r2) on the direct driver and the r4 stick: the node starts on r2 (the owner)."""
    spawned = []

    def spawn(argv, log, cwd=None, env=None):
        open(str(log), "a").close()
        spawned.append(subprocess.Popen(["sleep", "120"], start_new_session=True,   # wallclock: a live launch, ended after the test; never paces
                                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        return spawned[-1].pid
    radios_fixture.write(tmp_path, (*SPI868, PINE))
    radios_fixture.reticulum_backend(tmp_path, ["r1", "r2"])
    with (tmp_path / "config" / "stacks" / "reticulum@r2.toml").open("a") as f:   # MeshChat (selected by default) is no
        f.write('autostart_meshchat = "off"\n')                                  # process here: the owner node alone
    seed_framing_driver(tmp_path)
    for name, value in (("hardware_configured", lambda self: True), ("radio_bands", lambda self: ["868"]),
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
    assert not plan.ok and plan.summary == ("Reticulum runs on r2. Connect r1 to that node? (live, no restart) "
                                            "Restart MeshChat afterwards to see r1 in its interfaces.")
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


def test_a_join_starts_no_meshchat_by_default_either(box, capsys):
    # MeshChat starts with the stack by default (his word 11-23); a joining radio still starts none (one on 8790).
    _owner_up(box)
    launched = len(box.spawned)
    assert cli.main(["stack", "start", "reticulum@r1", "--join", "--yes"]) == 0, capsys.readouterr()
    assert "meshchat" not in capsys.readouterr().out and len(box.spawned) == launched
    assert _member(box, "r1")


def test_an_attach_the_node_does_not_list_is_refused_and_leaves_nothing(box, capsys):
    _owner_up(box)
    box.node.unlisted = {"LoRa r1"}                                 # rc 0, but RNS dropped the interface (plan §1c (a))
    assert cli.main(["stack", "start", "reticulum@r1", "--join", "--yes"]) == 1
    assert "the node attached LoRa r1 but does not list it" in capsys.readouterr().out
    assert (_sections(box), _member(box, "r1"), _intent(box, "r1")) == (["[[LoRa r2]]"], [], "")


def test_a_section_the_interface_refuses_is_never_written_or_attached(box):
    from lhpc.core import reticulum_members
    _owner_up(box)
    before = (box.root / "state" / "reticulum" / "config").read_text()
    reticulum_members._save_intent(box.svc, "r4", "r2")                  # r4: the stick, the daemon backend
    box.node.check_line = "txpower: above the 868 MHz band cap"
    assert "txpower: above the 868 MHz band cap" in reticulum_members.attach(box.svc, "r2", "r4")
    assert not [c for c in box.node.calls if c[:1] == ("--attach",)]
    assert (box.root / "state" / "reticulum" / "config").read_text() == before


def test_an_spi_members_section_never_meets_the_daemon_interfaces_check(box, capsys):
    # RETI-SOCKET (C2): the check is the daemon interface's and refuses every other type. Red-before (666d3661, with
    # the fake refusing as the real tool does): "type: 'LoRaSPIInterface' is not LoRaDaemonInterface", the join fails
    _owner_up(box)
    assert cli.main(["stack", "start", "reticulum@r1", "--join", "--yes"]) == 0, capsys.readouterr().out
    assert not [c for c in box.node.calls if c[:1] == ("check",)] and _member(box, "r1")


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


def test_a_meshchat_stop_on_the_owner_keeps_the_nodes_members(box):
    # red-before (921ca5b0): the owner's MeshChat stop counted as the node's stop: the plan named r1, the apply
    # dropped r1's member record and intent with no detach while rns kept r1 attached
    owner = _owner_up(box)
    assert cli.main(["stack", "start", "reticulum@r1", "--join", "--yes"]) == 0
    plan = _stop(box, "r2", apply=False, target="meshchat")
    assert plan.ok and not [d for d in plan.details if "[also disconnects]" in d], plan.details
    calls = len(box.node.calls)
    res = _stop(box, "r2", target="meshchat")
    assert res.ok, res.summary + "\n".join(res.details)
    assert [r.component for r in res.results] == ["meshchat"] and owner.poll() is None
    assert _member(box, "r1") and _intent(box, "r1") == "r2" and "[[LoRa r1]]" in _sections(box)
    assert box.node.calls[calls:] == [] and "LoRa r1" in box.node.attached        # no rnstatus: the node keeps r1


def test_a_start_and_a_meshchat_restart_on_the_owner_pass_with_a_live_member(box):
    # plan v3 F3, red-before (921ca5b0): the one-instance gate counted the member's key and refused the owner's own
    # start: "Reticulum runs on r2. Connect r2 to that node?"
    owner = _owner_up(box)
    assert cli.main(["stack", "start", "reticulum@r1", "--join", "--yes"]) == 0
    r2 = instances.copy_of(box.svc, "reticulum", "r2")[0]
    records = [r["pid"] for r in _member(box, "r1")]
    calls = len(box.node.calls)
    res = box.svc.start("meshchat", apply=True, inst=r2)
    assert "Connect r2" not in res.summary, res.summary
    res = box.svc.restart("meshchat", apply=True, inst=r2)
    assert "Connect r2" not in res.summary, res.summary + "\n".join(res.details)
    assert owner.poll() is None and [r["pid"] for r in _member(box, "r1")] == records and _intent(box, "r1") == "r2"
    assert "[[LoRa r1]]" in _sections(box) and "LoRa r1" in box.node.attached
    assert not [c for c in box.node.calls[calls:] if c[0] in ("--detach", "--attach")]


def test_a_meshchat_stop_on_a_member_claims_no_disconnect(box):
    # plan v3 C2, red-before (921ca5b0) on the plan leg: `stop meshchat@r1` stops nothing of r1's node link, yet its
    # plan said "[disconnects] r1 from the Reticulum node"
    _owner_up(box)
    assert cli.main(["stack", "start", "reticulum@r1", "--join", "--yes"]) == 0
    plan = _stop(box, "r1", apply=False, target="meshchat")
    assert not [d for d in plan.details if "from the Reticulum node" in d], plan.details
    calls = len(box.node.calls)
    _stop(box, "r1", target="meshchat")
    assert _member(box, "r1") and _intent(box, "r1") == "r2" and "[[LoRa r1]]" in _sections(box)
    assert "LoRa r1" in box.node.attached and not [c for c in box.node.calls[calls:] if c[0] == "--detach"]
    whole = _stop(box, "r1", apply=False)                                   # guard: its whole-stack Stop still says it
    assert "  [disconnects] r1 from the Reticulum node (it keeps running)" in whole.details, whole.details


def test_a_third_radios_start_still_asks_to_connect(box):
    # guard (green before and after F3): only the owner's own members are the same node
    owner = _owner_up(box)
    assert cli.main(["stack", "start", "reticulum@r1", "--join", "--yes"]) == 0
    res = _start(box, "r4")
    assert not res.ok and res.summary.startswith("Reticulum runs on r2. Connect r4 to that node?"), res.summary
    owner.kill()                                 # G1 254 N1: a dead node's member key is exempted for no radio
    owner.wait(timeout=10)
    assert _rm.owner_members(box.svc, "r2") == [] and _member(box, "r1")


@pytest.mark.parametrize("target", ["reticulum", "rns"])
def test_a_stop_of_the_node_on_the_owner_names_and_ends_the_memberships(box, target):
    # regression guard: the whole-stack Stop and `stop rns` take the node, so they still end its memberships
    owner = _owner_up(box)
    assert cli.main(["stack", "start", "reticulum@r1", "--join", "--yes"]) == 0
    plan = _stop(box, "r2", apply=False, target=target)
    assert "  [also disconnects] reticulum@r1: a radio of this node" in plan.details, plan.details
    res = _stop(box, "r2", target=target)
    assert res.ok and owner.wait(timeout=10) is not None and (_member(box, "r1"), _intent(box, "r1")) == ([], "")


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


def test_a_live_daemon_members_settings_the_interface_refuses_are_never_saved_or_reloaded(box):
    # save_refusal on the daemon member r4 (the check is the daemon interface's; the spi member's reload is below)
    from lhpc.core import reticulum_members
    _owner_up(box)
    reticulum_members._save_intent(box.svc, "r4", "r2")
    assert reticulum_members.attach(box.svc, "r2", "r4") == ""
    r4 = instances.copy_of(box.svc, "reticulum", "r4")[0]
    store = box.root / "config" / "stacks" / "reticulum@r4.toml"
    before, calls = store.read_text(), len(box.node.calls)
    box.node.check_line = "airtime_limit_short: 0-25 % on 868"
    res = box.svc.save_config("reticulum", {"file_airtime_limit_short": "30.0"}, inst=r4)
    # red-before (064363de): saved, nothing checked, nothing reloaded
    assert not res.ok and "the interface would refuse these settings: airtime_limit_short" in " ".join(res.details)
    assert box.node.calls[calls:][-1][0] == "check" and re.search(r"airtime_limit_short\s*=\s*30", box.node.calls[-1][1])
    assert store.read_text() == before and not [c for c in box.node.calls[calls:] if c[:1] == ("--reload",)]


def test_a_live_members_settings_are_checked_first_and_reloaded_live(box):
    _owner_up(box)
    assert cli.main(["stack", "start", "reticulum@r1", "--join", "--yes"]) == 0
    r1 = instances.copy_of(box.svc, "reticulum", "r1")[0]
    # (the check-first refusal is the daemon interface's: test_a_section_the_interface_refuses_..., the panel test)
    res = box.svc.save_config("reticulum", {"file_frequency": "434000000"}, inst=r1)
    # red-before (064363de): saved, nothing reloaded
    assert res.ok and ("--reload", "LoRa r1") in box.node.calls and "[member] r1: reloaded live" in "\n".join(res.details)
    box.node.refuse_reload = {"LoRa r1"}
    res = box.svc.save_config("reticulum", {"file_frequency": "434100000"}, inst=r1)
    text = "\n".join(res.details)
    assert res.ok and "not connected: neither the new settings (refused: Could not reload interface LoRa r1)" in text, text
    # red-before (ea51424b): only the record goes, the refused interface is never detached (plan v25 §9.2)
    assert ("--detach", "LoRa r1") in box.node.calls and "LoRa r1 is gone from the node" in text
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
    assert 'name="op" value="join"' in page and "Connect r1 to that node?" in page \
        and "Restart MeshChat afterwards to see r1 in its interfaces." in page
    assert cli.main(["stack", "start", "reticulum@r1", "--join", "--yes"]) == 0
    page = client.post("/action", data={"_csrf": csrf(client), "op": "stop", "target": "reticulum@r2"}).get_data(as_text=True)
    assert "[also disconnects] reticulum@r1: a radio of this node" in page
    res = box.svc.stop("reticulum", inst=instances.copy_of(box.svc, "reticulum", "r1")[0])
    assert "[removes]" not in " ".join(res.details) and "[disconnects] r1 from the Reticulum node" in " ".join(res.details)


def test_the_web_stop_of_the_owners_meshchat_names_no_member(box, web, csrf):
    # red-before (921ca5b0): the confirm of a MeshChat Stop on the owner listed r1 as disconnected
    _owner_up(box)
    assert cli.main(["stack", "start", "reticulum@r1", "--join", "--yes"]) == 0
    client = web(service_factory=lambda: box.svc)
    page = client.post("/action", data={"_csrf": csrf(client), "op": "stop", "target": "meshchat@r2"}).get_data(as_text=True)
    assert "[stop] meshchat" in page and "[also disconnects]" not in page


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


def test_a_member_restart_starts_no_meshchat_by_default_either(box):
    # MeshChat is selected by default (his word 11-23); a joining radio's restart still starts none (the owner's on 8790).
    _owner_up(box)
    assert cli.main(["stack", "start", "reticulum@r1", "--join", "--yes"]) == 0
    launched = len(box.spawned)
    res = box.svc.restart("reticulum", apply=True, inst=instances.copy_of(box.svc, "reticulum", "r1")[0])
    assert res.ok and not any("meshchat" in d for d in res.details), res.summary + "\n".join(res.details)
    assert len(box.spawned) == launched


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
    # row 220 B1: the attach succeeded, only its confirmation is missing
    assert "not verified: the node attached LoRa r1 but does not answer whether it runs" in out, out
    assert "it may still be live and stays tracked" in out
    assert "did not attach" not in out
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
    # row 220 B2: both reloads succeeded (rc 0), neither is confirmed: nothing was refused, the state is not known
    assert res.ok and "not verified: neither the new settings (reloaded, the node does not answer whether it runs)" in text
    assert "it may still be live and stays tracked" in text and "refused" not in text and "not connected" not in text, text
    assert len(_member(box, "r1")) == 1 and _intent(box, "r1") == "r2"


def test_the_owners_stop_clears_the_intent_of_a_radio_that_may_not_start_now(box):
    _owner_up(box)
    assert cli.main(["stack", "start", "reticulum@r1", "--join", "--yes"]) == 0
    radios_fixture.write(box.root, tuple(dataclasses.replace(r, enabled=False) if r.id == "r1" else r
                                         for r in (*radios_fixture.RADIOS, PINE)))   # r1 disabled after its Connect
    res = box.svc.stop("reticulum", apply=True, inst=instances.copy_of(box.svc, "reticulum", "r2")[0])
    # red-before (ea51424b): r1 keeps member_of = "r2" (not eligible, not cleared): its own start refused for good
    assert res.ok and _intent(box, "r1") == ""


def test_a_node_started_again_after_a_crash_keeps_one_member_record_on_its_new_process(box):
    old = _owner_up(box)
    assert cli.main(["stack", "start", "reticulum@r1", "--join", "--yes"]) == 0
    old.kill()                                                       # the node crashed: no Stop, its records stay
    old.wait(timeout=10)
    new = _owner_up(box)                                             # its start reconciles r1 (plan v25 §9.2)
    assert [r["pid"] for r in _member(box, "r1")] == [new.pid]


def test_a_remove_takes_every_member_record_of_the_radio(box):
    owner = _owner_up(box)
    assert cli.main(["stack", "start", "reticulum@r1", "--join", "--yes"]) == 0
    life, stk = box.svc._lifecycle(), box.svc.stack("reticulum")
    assert life.record_launch(stk, stk.component("rns"), owner.pid, band="868",
                              extra={"rid": "r1", "member_of": "r2"})            # a second one, however it came
    assert len(_member(box, "r1")) == 2
    assert box.svc.stop("reticulum", apply=True, inst=instances.copy_of(box.svc, "reticulum", "r1")[0]).ok
    # red-before (b602baa8): the first record goes, the second stays (a25 F218 N1)
    assert _member(box, "r1") == []


def test_an_attach_the_node_confirms_only_after_its_teardown_says_it_is_gone(box, capsys):
    _owner_up(box)
    box.node.j_miss_after = {"--attach": 1}                        # attached (rc 0), the verifying `-j` unanswered once
    assert cli.main(["stack", "start", "reticulum@r1", "--join", "--yes"]) == 1
    out = capsys.readouterr().out
    # red-before (a8c5bd67): "did not attach" although the attach succeeded (row 220 B1)
    # a25 F220 N1: "is gone from the node" (the teardown's own `-j` may answer without it: no --detach needed)
    assert "not connected: the node attached LoRa r1 but does not answer whether it runs; LoRa r1 is gone from the node" in out
    assert ("--detach", "LoRa r1") in box.node.calls
    assert (_sections(box), _member(box, "r1"), _intent(box, "r1")) == (["[[LoRa r2]]"], [], "")


def test_an_owner_starts_reconcile_says_not_verified_while_its_member_stays_tracked(box, tmp_path):
    radios_fixture.reticulum_backend(tmp_path, [])                  # (the dir)
    (tmp_path / "config" / "stacks" / "reticulum@r1.toml").write_text('backend = "spi"\nmember_of = "r2"\n')
    box.node.j_down_after = {"--attach"}                            # the reconcile's attach: rc 0, then `-j` unanswered
    res = _start(box, "r2")
    text = "\n".join(res.details)
    # a25 F220 N2: the reconcile line follows the facts (record kept -> "not verified"), never a fixed "not connected"
    assert res.ok and "[member] r1: not verified — the node attached LoRa r1 but does not answer whether it runs" in text, text
    assert len(_member(box, "r1")) == 1 and _intent(box, "r1") == "r2"


def test_a_member_radio_below_reticulums_power_floor_is_left_out_and_the_owner_starts(box):
    # plan RETI-POWER v2 §3b / a25 N3: refused, never raised to 2 (that would send above the radio's POWER)
    radios_fixture.write(box.root, (*radios_fixture.RADIOS, dataclasses.replace(PINE, dp=(("POWER", "1"),))))
    (box.root / "config" / "stacks" / "reticulum@r4.toml").write_text('member_of = "r2"\n')
    res = _start(box, "r2")
    assert res.ok, res.summary
    # red-before (93419bfe): r4 attached at its profile's 14, the radio's POWER 1 unseen
    assert "[member] r4: not connected — r4's POWER 1 dBm is below Reticulum's minimum 2 dBm" in "\n".join(res.details)
    assert _member(box, "r4") == [] and "[[LoRa r4]]" not in _sections(box)


# ---- a member's leave (PLAN-RETI-MEMBER-LEAVE v2): only what its start ran, the owner's clients untouched --------------

def _owners_meshchat_up(box):
    """The owner's MeshChat on its one fixed address (the manifest's 127.0.0.1:8790), up as the node runs it."""
    from lhpc.core.probes.backends import Listener
    box.svc._system.procfs.listeners.append(Listener(family="ipv4", ip="127.0.0.1", port=8790, inode=8790))


def _stop(box, rid, apply=True, target="reticulum"):
    return box.svc.stop(target, apply=apply, inst=instances.copy_of(box.svc, "reticulum", rid)[0])


def test_a_member_stop_with_the_owners_meshchat_up_detaches_and_checks_no_client(box):
    # red-before (e14cf5be, R-P6 run 4 13-leave.txt): the member's stop took meshchat too, met the owner's MeshChat on
    # 8790: ENDPOINT_STILL_PRESENT, rc 1 "NOT fully verified"
    owner = _owner_up(box)
    assert cli.main(["stack", "start", "reticulum@r1", "--join", "--yes"]) == 0
    _owners_meshchat_up(box)
    res = _stop(box, "r1")
    assert res.ok, res.summary + "\n".join(res.details)
    assert [r.component for r in res.results] == ["rns"] and owner.poll() is None and ("--detach", "LoRa r1") in box.node.calls
    assert not [d for d in res.details if "[kept]" in d] and _member(box, "r1") == []


def _release_spy(monkeypatch, other=None):
    """The daemon's own stop recorded, not run: [(band, rid)]; the daemon reachable, `other` a stack on r4's daemon."""
    import types

    from lhpc.core.service_base import ActionResult
    released, real_stop, real_insts = [], ControllerService.stop, instances.running_insts

    def stop(self, target, apply=False, *a, **kw):
        if kw.get("band"):
            released.append((kw["band"], getattr(kw.get("inst"), "rid", "")))
            return ActionResult(True, "stopped")
        return real_stop(self, target, apply, *a, **kw)
    monkeypatch.setattr(ControllerService, "stop", stop)
    monkeypatch.setattr(ControllerService, "daemon_view", lambda self, b, rid="": types.SimpleNamespace(reachable=True))
    monkeypatch.setattr(ControllerService, "stop_dependents", lambda self, sid, bands=None: [other] if other else [])
    monkeypatch.setattr(instances, "running_insts", lambda svc, d: [types.SimpleNamespace(rid="r4")] if d == other
                        else real_insts(svc, d))
    return released


@pytest.mark.parametrize("other, want", [(None, [("868", "r4")]), ("kiss", [])])
def test_a_daemon_member_of_an_spi_node_releases_its_daemon_band_unless_another_stack_needs_it(box, monkeypatch, other,
                                                                                                want):
    # red-before (e14cf5be): own_ok False (the owner's MeshChat on 8790), the release never asked (R-P6, by ps);
    # (bcc00c75, GAP-A): the release read the node's running backend, the spi owner's: no daemon dependency, nothing.
    # G1 row 242: a stack still on r4's daemon keeps it.
    from lhpc.core import reticulum_members
    _owner_up(box)
    reticulum_members._save_intent(box.svc, "r4", "r2")
    assert reticulum_members.attach(box.svc, "r2", "r4") == ""
    _owners_meshchat_up(box)
    released = _release_spy(monkeypatch, other)
    res = _stop(box, "r4")
    assert res.ok, res.summary + "\n".join(res.details)
    assert released == want


def test_an_spi_members_stop_releases_no_daemon_band(box, monkeypatch):
    # Part 3: an instance releases by its own radio's backend; r1 runs spi
    _owner_up(box)
    assert cli.main(["stack", "start", "reticulum@r1", "--join", "--yes"]) == 0
    released = _release_spy(monkeypatch)
    res = _stop(box, "r1")
    assert res.ok and released == [], (res.details, released)


def _node_bytes(box):
    return (box.root / "state" / "reticulum" / "config").read_bytes()


def test_a_never_joined_radios_stop_while_the_node_runs_elsewhere_leaves_as_a_member(box):
    # red-before (bcc00c75, GAP-B): the generic path met the owner's MeshChat on 8790: not verified, every time
    owner = _owner_up(box)
    _owners_meshchat_up(box)
    node, calls = _node_bytes(box), len(box.node.calls)
    res = _stop(box, "r1")
    assert res.ok, res.summary + "\n".join(res.details)
    assert [(r.component, r.summary) for r in res.results] == [
        ("rns", "r1 removed from the Reticulum node (it was not connected)")]
    assert owner.poll() is None and _node_bytes(box) == node and ("--detach", "LoRa r1") not in box.node.calls[calls:]


def test_a_left_radios_stop_again_leaves_as_a_member(box):
    # red-before (bcc00c75, GAP-B): after its leave r1 is no member: its second Stop took the generic path, not verified
    owner = _owner_up(box)
    assert cli.main(["stack", "start", "reticulum@r1", "--join", "--yes"]) == 0
    _owners_meshchat_up(box)
    assert _stop(box, "r1").ok
    res = _stop(box, "r1")
    assert res.ok and [r.component for r in res.results] == ["rns"] and owner.poll() is None, res.details


def test_with_no_node_running_a_radios_stop_is_the_generic_one(box):
    res = _stop(box, "r1")
    assert {r.component for r in res.results} == {"rns", "sideband", "meshchat", "lxmd", "nomadnet"}, res.details
    assert not [r for r in res.results if "Reticulum node" in r.summary]


def test_a_radio_with_its_own_stand_alone_node_record_keeps_the_generic_stop(box, monkeypatch):
    # reti N1 (a25 v4 N1): a non-member rns record under r1 (an earlier node there) is never a silent "removed"
    _owner_up(box)
    real = Lifecycle.owned_records

    def records(self, comp_id, band=None, role="", rid=None):
        if comp_id == "rns" and rid == "r1":
            return [{"component": "rns", "rid": "r1", "pid": 1}]
        return real(self, comp_id, band=band, role=role, rid=rid)
    monkeypatch.setattr(Lifecycle, "owned_records", records)
    res = _stop(box, "r1", apply=False)
    assert [d for d in res.details if d.startswith("  [stop] ")] == [
        "  [stop] rns", "  [stop] sideband", "  [stop] meshchat", "  [stop] lxmd", "  [stop] nomadnet"], res.details
    assert not [d for d in res.details if "from the Reticulum node" in d], res.details


def test_a_failed_leave_retried_never_touches_the_owners_node(box, monkeypatch):
    # G1 row 242: the leave's teardown succeeds, its other own component (a record under r1) does not verify; the retry
    # (r1 no member any more, the node on r2) leaves as a member: verified, rns alone, none of the owner's processes
    # signalled, its records kept
    from lhpc.core.outcomes import CompResult
    owner = _owner_up(box)
    assert cli.main(["stack", "start", "reticulum@r1", "--join", "--yes"]) == 0
    _owners_meshchat_up(box)
    real_recs, real_stop = Lifecycle.owned_records, Lifecycle.stop
    stale = {"on": True}

    def records(self, comp_id, band=None, role="", rid=None):
        if comp_id == "meshchat" and rid == "r1" and stale["on"]:
            return [{"component": "meshchat", "rid": "r1", "pid": 1}]
        return real_recs(self, comp_id, band=band, role=role, rid=rid)

    def lstop(self, comp, *a, **kw):
        if comp.id == "meshchat" and stale["on"]:
            return CompResult(component="meshchat", action="stop", outcome=Outcome.ENDPOINT_STILL_PRESENT,
                              summary="no owned process, but readiness endpoint(s) still present: 127.0.0.1:8790")
        return real_stop(self, comp, *a, **kw)
    monkeypatch.setattr(Lifecycle, "owned_records", records)
    monkeypatch.setattr(Lifecycle, "stop", lstop)
    owners = real_recs(box.svc._lifecycle(), "rns", role=None, rid="r2")
    first = _stop(box, "r1")
    assert not first.ok and [r.component for r in first.results] == ["rns", "meshchat"], first.details
    again = _stop(box, "r1")                       # the record still there: never abandoned, its stop reports again
    assert not again.ok and [r.component for r in again.results] == ["rns", "meshchat"], again.details
    stale["on"] = False
    retry = _stop(box, "r1")                       # Part 4: a radio outside the running node leaves as a member
    assert retry.ok and [r.component for r in retry.results] == ["rns"], retry.details
    assert owner.poll() is None and real_recs(box.svc._lifecycle(), "rns", role=None, rid="r2") == owners


def test_a_members_stop_plan_lists_its_rns_step_alone(box):
    _owner_up(box)
    assert cli.main(["stack", "start", "reticulum@r1", "--join", "--yes"]) == 0
    res = _stop(box, "r1", apply=False)
    assert res.ok and [d for d in res.details if d.startswith("  [stop] ")] == ["  [stop] rns"], res.details
    assert "1 component(s)" in res.summary


def test_a_component_with_a_record_under_the_member_is_still_in_its_stop(box, monkeypatch):
    # N1: a record under the member's own radio pulls its component in (never a silent leftover)
    _owner_up(box)
    assert cli.main(["stack", "start", "reticulum@r1", "--join", "--yes"]) == 0
    real = Lifecycle.owned_records

    def records(self, comp_id, band=None, role="", rid=None):
        if comp_id == "meshchat" and rid == "r1":
            return [{"component": "meshchat", "rid": "r1", "pid": 1}]
        return real(self, comp_id, band=band, role=role, rid=rid)
    monkeypatch.setattr(Lifecycle, "owned_records", records)
    res = _stop(box, "r1", apply=False)
    assert [d for d in res.details if d.startswith("  [stop] ")] == ["  [stop] rns", "  [stop] meshchat"], res.details


def test_a_component_target_on_a_member_keeps_its_own_stop(box):
    # a25 C1: only a whole-stack target is filtered; `stop meshchat` on a member's radio is the operator's word about it
    _owner_up(box)
    assert cli.main(["stack", "start", "reticulum@r1", "--join", "--yes"]) == 0
    res = _stop(box, "r1", apply=False, target="meshchat")
    assert [d for d in res.details if d.startswith("  [stop] ")] == ["  [stop] meshchat"], (res.summary, res.details)


def test_a_client_stop_that_does_not_verify_says_it_released_nothing(box):
    # plan v2 Part 2: the owner's own stop meets a MeshChat on 8790 it has no record of: not verified, nothing released
    _owner_up(box)
    _owners_meshchat_up(box)
    res = _stop(box, "r2")
    assert not res.ok
    assert "  [kept] daemon band(s) and dependency stacks: not released (this stop did not verify)" in res.details
    assert not [d for d in res.details if d.startswith(("  [stopped daemon]", "  [stopped dependency]"))], res.details
