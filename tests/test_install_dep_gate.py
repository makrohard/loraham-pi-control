"""Mandatory system dependencies block Install; optional ones only warn — uniformly across the
service classifier, the CLI, the web confirm page, and auto-install (plan preflight + mid-run gate +
the /auto-install page). Deterministic: FakeSystem drives `check_file` presence via `exists`; the one
`cmd` dep (kiss `socat`) is driven by patching `shutil.which`.

Fixtures of note:
- `chat` main component (`loraham-chat`, NOT optional) requires the ncurses header (`check_file`) →
  a MANDATORY dep, absent under a bare FakeSystem → block.
- `kiss` optional component (`loraham-kiss-serial`, optional=true) requires `socat` (`cmd`) → an
  OPTIONAL dep → warn.
"""

import shutil

import pytest

from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService, ActionResult
from lhpc.adapters.web.app import create_app
from lhpc.adapters.cli import main as cli


def _svc(tmp_path, paths=(), groups=("spi", "gpio")):
    fake = FakeSystem(effective_group_names=frozenset(groups), configured_group_names=frozenset(groups),
                      paths=set(paths))
    return ControllerService(system=fake.system, paths=Paths(runtime_root=tmp_path))


@pytest.fixture
def _socat_absent(monkeypatch):
    real = shutil.which
    monkeypatch.setattr(shutil, "which", lambda n, *a, **k: None if n == "socat" else real(n, *a, **k))


# --- service classifier ---------------------------------------------------------------------------

def test_mandatory_dep_of_nonoptional_component_blocks(tmp_path):
    gate = _svc(tmp_path).install_dep_gate("chat")            # ncurses header absent
    assert gate["block"] and not gate["warn"]
    assert all(d["mandatory"] for d in gate["block"])
    assert any("ncurses" in (d["what"] or "").lower() for d in gate["block"])


def test_optional_dep_of_optional_component_only_warns(tmp_path, _socat_absent):
    gate = _svc(tmp_path).install_dep_gate("kiss")           # socat missing, on the OPTIONAL PTY comp
    assert gate["warn"] and not gate["block"]
    assert all(not d["mandatory"] for d in gate["warn"])
    assert any("socat" in (d["what"] or "").lower() for d in gate["warn"])


def test_runtime_group_capability_is_excluded_from_the_gate(tmp_path):
    # meshtasticd + spidev present so only the spi/gpio GROUP capability is unmet; group caps gate
    # start, not install -> they appear in neither block nor warn.
    svc = _svc(tmp_path, paths=("/usr/bin/meshtasticd", "/dev/spidev0.0"), groups=())
    gate = svc.install_dep_gate("meshtastic")
    assert gate["block"] == [] and gate["warn"] == []
    assert any(d["runtime"] for d in svc.system_deps("meshtastic"))   # the group cap still surfaces


def test_system_deps_carry_mandatory_flag(tmp_path):
    deps = _svc(tmp_path).system_deps("chat")
    assert deps and all("mandatory" in d for d in deps)
    assert all(d["mandatory"] for d in deps)                  # chat's only comp is non-optional


# --- CLI -----------------------------------------------------------------------------------------

def _patch_gate(monkeypatch, mapping):
    monkeypatch.setattr(ControllerService, "install_dep_gate",
                        lambda self, t: mapping.get(t, {"block": [], "warn": []}))


def test_cli_install_refuses_on_mandatory_and_never_installs(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("LHPC_RUNTIME_ROOT", str(tmp_path / "rt"))
    cli.main(["bootstrap", "--yes"]); capsys.readouterr()
    _patch_gate(monkeypatch, {"chat": {"block": [{"what": "ncurses.h", "mandatory": True,
                                                  "install": "sudo apt install -y libncurses-dev"}],
                                       "warn": []}})
    ran = []
    monkeypatch.setattr(ControllerService, "install",
                        lambda self, *a, **k: ran.append(1) or ActionResult(True, "x"))
    rc = cli.main(["install", "chat"])
    out = capsys.readouterr().out
    assert rc == 1 and not ran                                # refused BEFORE install ran
    assert "Refusing to install 'chat'" in out and "libncurses-dev" in out


def test_cli_install_warns_on_optional_but_proceeds(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("LHPC_RUNTIME_ROOT", str(tmp_path / "rt"))
    cli.main(["bootstrap", "--yes"]); capsys.readouterr()
    _patch_gate(monkeypatch, {"kiss": {"block": [],
                                       "warn": [{"what": "socat", "mandatory": False,
                                                 "install": "sudo apt install -y socat"}]}})
    rc = cli.main(["install", "kiss", "--check"])             # --check -> dry-run, no real work
    out = capsys.readouterr().out
    assert rc == 0
    assert "WARN" in out and "socat" in out


# --- web per-stack confirm page ------------------------------------------------------------------

def _real_app(tmp_path):
    svc_holder = {}
    def factory():
        svc = _svc(tmp_path)
        svc_holder["svc"] = svc
        return svc
    app = create_app(service_factory=factory)
    app.config["SESSION_COOKIE_SECURE"] = False
    return app.test_client()


def _csrf(c, path="/stacks"):
    return c.get(path).get_data(as_text=True).split('name="_csrf" value="')[1].split('"')[0]


def test_confirm_page_blocks_apply_on_mandatory(tmp_path):
    c = _real_app(tmp_path)
    cf = c.post("/action", data={"_csrf": _csrf(c), "op": "install",
                                 "target": "chat"}).get_data(as_text=True)
    assert "Missing system dependencies" in cf                # depnote-bad mandatory block
    assert 'name="confirmed" value="yes"' not in cf          # Apply form suppressed


def test_confirm_page_warns_but_allows_apply_on_optional(tmp_path, _socat_absent):
    c = _real_app(tmp_path)
    cf = c.post("/action", data={"_csrf": _csrf(c), "op": "install",
                                 "target": "kiss"}).get_data(as_text=True)
    assert "Optional dependencies not installed" in cf and "socat" in cf
    assert "Missing system dependencies" not in cf           # NOT a hard block
    assert 'name="confirmed" value="yes"' in cf              # Apply form still present


# --- auto-install -----------------------------------------------------------------------------------

def test_auto_install_plan_preflight_lists_blocked_and_advises_abort(tmp_path):
    r = _svc(tmp_path).auto_install(apply=False)              # bare FakeSystem -> header deps missing
    assert r.ok
    assert any(d.startswith("  [blocked] chat:") for d in r.details)
    assert any("will be SKIPPED" in d for d in r.details)


def test_auto_install_page_shows_skip_warning_without_disabling_start(tmp_path, monkeypatch):
    # Isolate the dep card from the auto-install-state gate so the Start button's only possible disabler
    # would be a dep-based one — which we deliberately do NOT add.
    monkeypatch.setattr(ControllerService, "_auto_install_gate", lambda self: "")
    c = _real_app(tmp_path)
    body = c.get("/auto-install").get_data(as_text=True)
    assert "will be SKIPPED" in body and "chat" in body      # mandatory-missing stack listed
    assert '<button type="submit" disabled>' not in body     # dep block does NOT disable Start


def test_auto_install_preflight_helper_classifies_block_vs_warn(tmp_path, _socat_absent):
    pf = _svc(tmp_path).auto_install_dep_preflight()
    assert any(s["stack"] == "chat" for s in pf["block"])     # ncurses -> block
    assert any(s["stack"] == "kiss" for s in pf["warn"])      # socat -> warn only


@pytest.mark.needs_session
def test_auto_install_gate_blocks_before_any_source_work(tmp_path, monkeypatch):
    # Stub freeze/adopt/build/test so the run reaches the per-stack loop fast — but leave the REAL
    # dep gate in place. meshtastic is independent with mandatory deps missing -> it must be BLOCKED
    # by the early gate, before its source phase is ever entered.
    from lhpc.core.install import Installer, PlanAction
    monkeypatch.setattr(ControllerService, "_frozen_ref",
                        lambda self, comp, source: (("f" * 40, "frozen: stub"), ""))
    monkeypatch.setattr(Installer, "adopt_source",
                        lambda self, comp, force=False, source="pinned", pinned_expected=None,
                        locked=False: PlanAction("adopt", "", f"adopt {comp.id}",
                                                 status="done", detail="ok"))
    monkeypatch.setattr(ControllerService, "build",
                        lambda self, t, apply=False, auto_install_ctx=None, **k: ActionResult(True, "built"))
    monkeypatch.setattr(ControllerService, "test",
                        lambda self, t, tx=False, apply=False, auto_install_ctx=None, **k:
                        ActionResult(True, "tested"))
    lines = []
    _svc(tmp_path).auto_install(apply=True, tests=True, emit=lines.append)
    joined = "\n".join(lines)
    assert "==== meshtastic: BLOCKED (missing mandatory system deps" in joined
    assert "==== meshtastic: sources ====" not in joined      # never reached the source phase


def test_radiolib_build_deps_warn_on_a_fresh_image(tmp_path):
    # Field-verified (Trixie lite): cmake, the lgpio dev header, and the C++ toolchain are absent and the
    # RadioLib/daemon build fails on them. They are build-only deps of the OPTIONAL radiolib component, so
    # the daemon's install gate WARNS (never blocks source install) with the exact operator commands —
    # closing the "pre-check stayed green" gap.
    gate = _svc(tmp_path).install_dep_gate("daemon")           # bare FakeSystem -> the build deps absent
    warn = {d["install"] for d in gate["warn"]}
    assert "sudo apt install -y cmake" in warn
    assert "sudo apt install -y liblgpio-dev" in warn
    assert "sudo apt install -y build-essential" in warn
    assert all(not d["mandatory"] for d in gate["warn"])       # build deps of an optional comp -> warn


def test_meshcom_declares_the_qemu_libslirp_dependency(tmp_path):
    # meshcom's Espressif qemu-system-xtensa aborts with "libslirp.so.0 missing" without libslirp0
    # (run.sh names the exact fix). Declared so the dep pre-check surfaces it instead of a fresh image
    # failing at runtime; on the non-optional QEMU node it blocks install (like meshtasticd/spidev).
    gate = _svc(tmp_path).install_dep_gate("meshcom")
    assert any(d["install"] == "sudo apt install -y libslirp0" for d in gate["block"]), gate["block"]
