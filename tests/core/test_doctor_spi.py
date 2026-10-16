"""`lhpc doctor` reports the SPI chip-select mode: what config.txt asks for, what the running kernel
booted with, and the start gate's refusal while they disagree (docs/cli.md#deps)."""
import pytest

from lhpc.core import spi_mode
from lhpc.core.model import (
    Component,
    ComponentKind,
    ResourceClaim,
    ResourceKind,
    ResourceMode,
    Stack,
)
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService

SOFT_TXT = "dtparam=spi=on\ndtoverlay=spi0-0cs\n"
KERNEL_TXT = "dtparam=spi=on\n"
BUS = (ResourceClaim(key="spi.bus.0", kind=ResourceKind.SPI_BUS, mode=ResourceMode.COOPERATIVE),)


def _spi_lines(tmp_path, monkeypatch, config_txt, live):
    monkeypatch.setattr(spi_mode, "read_config_txt", lambda: config_txt)
    monkeypatch.setattr(spi_mode, "live_mode", lambda: live)
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    return [d for d in svc.doctor().details if d.strip().startswith("SPI chip selects:")]


@pytest.mark.parametrize("config_txt, live, boot", [
    (SOFT_TXT, "soft", "soft"), (KERNEL_TXT, "kernel", "kernel"), (None, "off", "off")],
    ids=["soft", "kernel", "no-config-txt"])
def test_doctor_names_the_mode_when_file_and_kernel_agree(tmp_path, monkeypatch, config_txt, live, boot):
    assert _spi_lines(tmp_path, monkeypatch, config_txt, live) == [
        f"  SPI chip selects: config.txt {boot}, running kernel {live} — they agree"]


@pytest.mark.parametrize("config_txt, live, said", [
    (KERNEL_TXT, "soft", "reboot required: config.txt asks for kernel SPI chip selects, but the "
                         "running kernel booted with soft"),
    (SOFT_TXT, "kernel", "reboot required: config.txt asks for soft SPI chip selects, but the "
                         "running kernel booted with kernel"),
    (KERNEL_TXT + "dtoverlay=spi0-1cs\n", "kernel", "unsupported SPI boot configuration"),
    (KERNEL_TXT, "unsupported", "unsupported SPI boot configuration"),
], ids=["edited-to-kernel", "edited-to-soft", "extra-overlay", "booted-unsupported"])
def test_doctor_names_the_start_gates_refusal(tmp_path, monkeypatch, config_txt, live, said):
    line = _spi_lines(tmp_path, monkeypatch, config_txt, live)
    assert len(line) == 1 and "SPI stacks are refused: " + said in line[0]
    # the same text the start gate returns (lifecycle.py, spi_mode.refusal)
    assert line[0].endswith(spi_mode.refusal(spi_mode.boot_mode(config_txt), live))


# ---- one row per installed SPI bus user built from a kernel_cs_since component --------------

def _repo(tmp_path):
    """A clone with three commits: old -> since -> newer; returns (repo, shas, git)."""
    import subprocess
    repo = tmp_path / "src"
    repo.mkdir()

    def git(*a):
        return subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c",
                               "user.email=t@example.invalid", *a], check=True,
                              capture_output=True, text=True).stdout.strip()
    git("init", "-q")
    shas = {}
    for n in ("old", "since", "newer"):
        (repo / "f").write_text(n)
        git("add", "f")
        git("commit", "-qm", n)
        shas[n] = git("rev-parse", "HEAD")
    return repo, shas, git


def _rows(lines):
    return [x for x in lines if x.startswith("SPI user ")]


def _daemon_stack(since, extra=()):
    comp = Component(id="loraham-daemon", name="d", kind=ComponentKind.SERVICE, kernel_cs_since=since,
                     resources=BUS)
    return Stack(id="daemon", name="d", components=(comp, *extra))


@pytest.fixture
def no_receipt(monkeypatch):
    from lhpc.core import binary_receipt
    monkeypatch.setattr(binary_receipt, "receipt_state", lambda paths, sid: ("absent", None, ""))


@pytest.mark.parametrize("head, row", [
    ("old", "SPI user daemon/loraham-daemon: loraham-daemon was built from {old9}, which predates "
            "kernel chip selects ({since9}): update it (`lhpc update daemon --yes`) before starting "
            "it with kernel chip selects"),
    ("since", "SPI user daemon/loraham-daemon: built with kernel chip selects"),
    ("newer", "SPI user daemon/loraham-daemon: built with kernel chip selects"),
], ids=["old", "since", "newer"])
def test_doctor_names_each_spi_users_build_against_kernel_chip_selects(tmp_path, monkeypatch, no_receipt,
                                                                       head, row):
    """The same provenance rule as the start gate (the real provenance_refusal, a real clone)."""
    repo, sha, git = _repo(tmp_path)
    git("checkout", "-q", sha[head])
    monkeypatch.setattr(spi_mode, "read_config_txt", lambda: KERNEL_TXT)
    monkeypatch.setattr(spi_mode, "live_mode", lambda: "kernel")
    lines = spi_mode.doctor_lines(None, [_daemon_stack(sha["since"])], lambda c: repo)
    assert _rows(lines) == [row.format(old9=sha["old"][:9], since9=sha["since"][:9])]


def test_doctor_says_the_build_is_checked_only_with_kernel_chip_selects(tmp_path, monkeypatch, no_receipt):
    repo, sha, git = _repo(tmp_path)
    git("checkout", "-q", sha["old"])
    monkeypatch.setattr(spi_mode, "read_config_txt", lambda: SOFT_TXT)
    monkeypatch.setattr(spi_mode, "live_mode", lambda: "soft")
    (row,) = _rows(spi_mode.doctor_lines(None, [_daemon_stack(sha["since"])], lambda c: repo))
    assert "predates kernel chip selects" in row
    assert row.endswith("(checked at start only with kernel chip selects)")


def test_a_build_dependency_names_its_own_commit(tmp_path, monkeypatch, no_receipt):
    """Reticulum: the SPI driver is a library built into `rns`; rns's marker names its commit."""
    lib_repo, sha, _git = _repo(tmp_path)
    rns_src = tmp_path / "rns"
    rns_src.mkdir()
    (rns_src / ".lhpc-built").write_text(f"lhpc build complete\nconsumed rns-lora-interface {sha['old']}\n")
    lib = Component(id="rns-lora-interface", name="l", kind=ComponentKind.LIBRARY, kernel_cs_since=sha["since"])
    rns = Component(id="rns", name="r", kind=ComponentKind.SERVICE, build_marker=".lhpc-built",
                    build_requires=("rns-lora-interface",), resources=BUS)
    monkeypatch.setattr(spi_mode, "read_config_txt", lambda: KERNEL_TXT)
    monkeypatch.setattr(spi_mode, "live_mode", lambda: "kernel")
    (row,) = _rows(spi_mode.doctor_lines(None, [Stack(id="reticulum", name="r", components=(rns, lib))],
                                         lambda c: lib_repo if c is lib else rns_src))
    assert row == (f"SPI user reticulum/rns: rns-lora-interface was built from {sha['old'][:9]}, which "
                   f"predates kernel chip selects ({sha['since'][:9]}): update it (`lhpc update reticulum "
                   "--yes`) before starting it with kernel chip selects")


def test_no_row_for_an_uninstalled_user_or_one_without_kernel_cs_since(tmp_path, monkeypatch, no_receipt):
    monkeypatch.setattr(spi_mode, "read_config_txt", lambda: KERNEL_TXT)
    monkeypatch.setattr(spi_mode, "live_mode", lambda: "kernel")
    plain = Component(id="meshtasticd", name="m", kind=ComponentKind.SERVICE, resources=BUS)
    stack = _daemon_stack("a" * 40, extra=(plain,))
    assert _rows(spi_mode.doctor_lines(None, [stack], lambda c: tmp_path / "absent")) == []
    (tmp_path / "here").mkdir()
    assert [r.split(":")[0] for r in _rows(spi_mode.doctor_lines(None, [stack], lambda c: tmp_path / "here"))] \
        == ["SPI user daemon/loraham-daemon"]                   # meshtasticd: no kernel_cs_since


def test_doctor_lists_the_manifests_installed_spi_users(tmp_path, monkeypatch):
    """Through ControllerService.doctor() and the packaged manifest: the daemon and Reticulum's rns
    get a row once installed; meshtastic (no kernel_cs_since) does not."""
    monkeypatch.setattr(spi_mode, "read_config_txt", lambda: KERNEL_TXT)
    monkeypatch.setattr(spi_mode, "live_mode", lambda: "kernel")
    monkeypatch.setattr(spi_mode, "provenance_refusal", lambda paths, stack, comp, sd: "")
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    life = svc._lifecycle()
    for sid, cid in (("daemon", "loraham-daemon"), ("reticulum", "rns"), ("meshtastic", "meshtastic")):
        life.source_dir(svc.stack(sid).component(cid)).mkdir(parents=True, exist_ok=True)
    rows = [d.strip() for d in svc.doctor().details if d.strip().startswith("SPI user ")]
    assert rows == ["SPI user daemon/loraham-daemon: built with kernel chip selects",
                    "SPI user reticulum/rns: built with kernel chip selects"]
