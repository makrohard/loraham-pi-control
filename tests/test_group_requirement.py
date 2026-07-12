"""Run-time unix-group capability requirement (meshtastic needs spi+gpio to run rootless).

The requirement is surfaced in the stack's system-dependencies view and enforced at START (a hard
requirement), but is NOT part of the install gate (meshtasticd installs fine without the group).
Group membership is read through the injectable System seam, so tests drive it with FakeSystem.
"""

from pathlib import Path

from lhpc.core.services import ControllerService
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.lifecycle import GROUP_MISSING_HINT, GROUP_RESTART_HINT


def _svc(tmp_path, groups=None, *, effective=None, configured=None):
    # meshtasticd binary + SPI device present so ONLY the group requirement varies. By default the
    # process is EFFECTIVELY in `groups` and CONFIGURED into the same set (no restart pending); the two
    # tiers can be set independently to exercise the "granted, restart pending" state.
    eff = frozenset(effective if effective is not None else (groups or ()))
    cfg = frozenset(configured if configured is not None else (groups or ()))
    fake = FakeSystem(effective_group_names=eff, configured_group_names=cfg,
                      paths={"/usr/bin/meshtasticd", "/dev/spidev0.0"})
    return ControllerService(system=fake.system, paths=Paths(runtime_root=tmp_path))


def _meshtastic_comp(svc):
    return next(c for st in svc.stacks() if st.id == "meshtastic"
               for c in st.components if c.id == "meshtastic")


def test_missing_requirements_flags_group_only_when_not_in_all_groups(tmp_path):
    # needs BOTH groups; a subset is still missing, a superset is satisfied.
    for groups, expect_missing in ((set(), True), ({"spi"}, True),
                                   ({"spi", "gpio"}, False), ({"spi", "gpio", "x"}, False)):
        svc = _svc(tmp_path, groups)
        life = svc._lifecycle()
        miss = life.missing_requirements(_meshtastic_comp(svc))
        assert any(r.groups for r in miss) is expect_missing, (groups, expect_missing)


def test_system_deps_shows_group_capability_with_grant_command(tmp_path):
    svc = _svc(tmp_path, set())
    grp = [d for d in svc.system_deps("meshtastic") if d["runtime"]]
    assert len(grp) == 1
    d = grp[0]
    assert d["satisfied"] is False and d["install"] == "sudo usermod -aG spi,gpio $USER"
    assert "spi + gpio group membership" in d["what"]
    # satisfied once in both groups
    svc2 = _svc(tmp_path, {"spi", "gpio"})
    assert [d for d in svc2.system_deps("meshtastic") if d["runtime"]][0]["satisfied"] is True


def test_group_capability_is_excluded_from_the_install_gate(tmp_path):
    # NOT in the groups, but meshtasticd installs fine without them -> the install gate must be empty.
    svc = _svc(tmp_path, set())
    assert svc.missing_system_deps("meshtastic") == []      # install/build not blocked by the group
    # (and it is genuinely missing at the display/start layer)
    assert any(d["runtime"] and not d["satisfied"] for d in svc.system_deps("meshtastic"))


def test_two_tier_group_states_gate_on_effective_hint_on_configured(tmp_path):
    from lhpc.core import deps
    comp_index = {}
    for st in ControllerService(system=FakeSystem().system,
                                paths=Paths(runtime_root=tmp_path)).stacks():
        for c in st.components:
            comp_index[c.id] = c

    def state(effective, configured):
        svc = _svc(tmp_path, effective=effective, configured=configured)
        life = svc._lifecycle()
        blocked = any(r.groups for r in life.missing_requirements(_meshtastic_comp(svc)))
        sd = [d for d in svc.system_deps("meshtastic") if d["runtime"]][0]
        di = [it for it in deps.stack_report(life, svc._paths, svc.stacks(), "meshtastic",
                                             comp_index) if it.runtime][0]
        return blocked, sd, di

    # (i) NEITHER configured nor effective -> genuinely missing: blocked, usermod grant shown, and the
    #     "not a member" hint (NOT the restart hint — they must never co-appear).
    blocked, sd, di = state(set(), set())
    assert blocked and sd["satisfied"] is False
    assert sd["install"] == "sudo usermod -aG spi,gpio $USER" and GROUP_MISSING_HINT in sd["what"]
    assert di.install_cmd == "sudo usermod -aG spi,gpio $USER" and di.detail == GROUP_MISSING_HINT
    assert GROUP_RESTART_HINT not in di.detail and GROUP_RESTART_HINT not in sd["what"]

    # (ii) CONFIGURED but not EFFECTIVE -> restart pending: START STILL BLOCKED, usermod suppressed,
    #      both surfaces carry the restart hint (re-granting is not the fix).
    blocked, sd, di = state(set(), {"spi", "gpio"})
    assert blocked and sd["satisfied"] is False               # fail-closed: start not allowed
    assert sd["install"] == "" and GROUP_RESTART_HINT in sd["what"]
    assert di.install_cmd == "" and di.detail == GROUP_RESTART_HINT

    # (iii) EFFECTIVE -> satisfied: not blocked, no command.
    blocked, sd, di = state({"spi", "gpio"}, {"spi", "gpio"})
    assert not blocked and sd["satisfied"] is True and di.satisfied is True


def test_req_remediation_restart_pending_vs_missing():
    # The shared formatter: a configured-but-not-effective (restart-pending) group grant advises a RESTART,
    # never re-shows the already-run usermod; a genuinely-missing grant shows the command; a non-group req
    # is unaffected.
    from lhpc.core.lifecycle import req_remediation
    from lhpc.core.model import Requirement
    g = Requirement(groups=("spi", "gpio"), install="sudo usermod -aG spi,gpio $USER",
                    note="spi + gpio group membership — needed to run meshtasticd WITHOUT root")
    m_pending = req_remediation(g, pending=True)
    assert GROUP_RESTART_HINT in m_pending and "usermod" not in m_pending
    m_missing = req_remediation(g, pending=False)
    assert "sudo usermod -aG spi,gpio $USER" in m_missing and GROUP_RESTART_HINT not in m_missing
    c = Requirement(cmd="socat", install="sudo apt install socat")
    assert "socat" in req_remediation(c, pending=False) and "apt install socat" in req_remediation(c, pending=False)


def test_start_gate_blocked_reason_advises_restart_when_pending(tmp_path):
    # The START gate itself (not only the display sites) must advise a RESTART for a configured-but-not-
    # effective grant, instead of re-showing the already-run usermod.
    svc = _svc(tmp_path, effective=set(), configured={"spi", "gpio"})
    svc.bootstrap(apply=True)
    mc = _meshtastic_comp(svc)
    (svc._paths.runtime_root / mc.source.path).mkdir(parents=True, exist_ok=True)   # "installed"
    r = svc.start("meshtastic", apply=True)
    blob = "\n".join(r.details) + " " + r.summary
    assert GROUP_RESTART_HINT in blob and "usermod" not in blob


def test_stacks_page_shows_copyable_usermod_only_when_missing(tmp_path):
    from lhpc.adapters.web.app import create_app
    def body(groups):
        svc = _svc(tmp_path, groups)
        app = create_app(lambda: svc); app.config["SESSION_COOKIE_SECURE"] = False
        return app.test_client().get("/stacks").get_data(as_text=True)
    missing = body(set())
    assert "sudo usermod -aG spi,gpio $USER" in missing and "spi + gpio group membership" in missing
    satisfied = body({"spi", "gpio"})
    assert "sudo usermod -aG spi,gpio $USER" not in satisfied     # command hidden once satisfied


def test_real_filesystem_group_methods_return_names(tmp_path):
    from lhpc.core.probes.backends import RealFileSystem
    for names in (RealFileSystem().effective_groups(), RealFileSystem().configured_groups()):
        assert isinstance(names, frozenset) and all(isinstance(n, str) for n in names)


def test_effective_vs_configured_split_on_stale_process_groups(monkeypatch):
    # The two tiers diverge exactly in the case the gate must handle: a lingering process whose PROCESS
    # supplementary groups are STALE (it started before `usermod -aG`). configured_groups() reads the
    # group database (os.getgrouplist) and SEES the grant; effective_groups() reads os.getgroups() and
    # does NOT — so the START gate (which uses effective) stays blocked while the hint (configured) knows
    # the grant is already made ("restart pending").
    import os as _os, grp as _grp, pwd as _pwd
    from lhpc.core.probes.backends import RealFileSystem
    names = {1000: "makro", 989: "spi", 986: "gpio"}
    monkeypatch.setattr(_os, "getuid", lambda: 1000)
    monkeypatch.setattr(_os, "getgid", lambda: 1000)
    monkeypatch.setattr(_pwd, "getpwuid", lambda uid: type("P", (), {"pw_name": "makro"})())
    monkeypatch.setattr(_os, "getgroups", lambda: [1000])                       # STALE: no spi/gpio
    monkeypatch.setattr(_os, "getgrouplist", lambda name, gid: [1000, 989, 986])  # CONFIGURED
    monkeypatch.setattr(_grp, "getgrgid", lambda gid: type("G", (), {"gr_name": names[gid]})())
    fs = RealFileSystem()
    assert {"spi", "gpio"} <= fs.configured_groups()          # the grant is on record
    assert not {"spi", "gpio"} & fs.effective_groups()        # but not yet effective in this process
