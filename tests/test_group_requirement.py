"""Run-time unix-group capability requirement (meshtastic needs spi+gpio to run rootless).

The requirement is surfaced in the stack's system-dependencies view and enforced at START (a hard
requirement), but is NOT part of the install gate (meshtasticd installs fine without the group).
Group membership is read through the injectable System seam, so tests drive it with FakeSystem.
"""

from pathlib import Path

from lhpc.core.services import ControllerService
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem


def _svc(tmp_path, groups):
    # meshtasticd binary + SPI device present so ONLY the group requirement varies.
    fake = FakeSystem(user_group_names=frozenset(groups),
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


def test_real_filesystem_user_groups_returns_names(tmp_path):
    from lhpc.core.probes.backends import RealFileSystem
    names = RealFileSystem().user_groups()
    assert isinstance(names, frozenset) and all(isinstance(n, str) for n in names)


def test_real_user_groups_uses_configured_membership_not_stale_process_groups(monkeypatch):
    # Regression: a long-lived / lingering service whose PROCESS supplementary groups are STALE (it
    # started before `usermod -aG`) must still report a genuinely-configured member. user_groups() reads
    # the group database (os.getgrouplist / /etc/group), NOT os.getgroups() — otherwise a granted user
    # is falsely shown "not found" until the service restarts.
    import os as _os, grp as _grp, pwd as _pwd
    from lhpc.core.probes.backends import RealFileSystem
    names = {1000: "makro", 989: "spi", 986: "gpio"}
    monkeypatch.setattr(_os, "getuid", lambda: 1000)
    monkeypatch.setattr(_os, "getgid", lambda: 1000)
    monkeypatch.setattr(_pwd, "getpwuid", lambda uid: type("P", (), {"pw_name": "makro"})())
    monkeypatch.setattr(_os, "getgroups", lambda: [1000])                       # STALE: no spi/gpio
    monkeypatch.setattr(_os, "getgrouplist", lambda name, gid: [1000, 989, 986])  # CONFIGURED
    monkeypatch.setattr(_grp, "getgrgid", lambda gid: type("G", (), {"gr_name": names[gid]})())
    assert {"spi", "gpio"} <= RealFileSystem().user_groups()
