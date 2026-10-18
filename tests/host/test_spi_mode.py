"""The SPI chip-select mode: the boot file and the booted device tree must agree, only the two
supported configurations are admitted, and meshtasticd's YAML follows the live mode."""

import pytest

from lhpc.core import spi_mode
from lhpc.core.config import Config, OperatorConfig
from lhpc.core.lifecycle import Lifecycle
from lhpc.core.model import (
    Component,
    ComponentKind,
    ResourceClaim,
    ResourceKind,
    ResourceMode,
    SourceSpec,
    Stack,
)
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem

GPIO_PHANDLE = 0x5A


def _be(*vals):
    return b"".join(v.to_bytes(4, "big") for v in vals)


def _tree(tmp_path, cs=b"", alias=True, symbols=True, phandle=GPIO_PHANDLE, status=None):
    """A fake /sys/firmware/devicetree/base with spi0 and the `gpio` label."""
    dt = tmp_path / "dt"
    (dt / "soc" / "spi@7e204000").mkdir(parents=True)
    (dt / "soc" / "gpio@7e200000").mkdir(parents=True)
    (dt / "aliases").mkdir()
    (dt / "__symbols__").mkdir()
    if alias:
        (dt / "aliases" / "spi0").write_bytes(b"/soc/spi@7e204000\0")
    if cs is not None:
        (dt / "soc" / "spi@7e204000" / "cs-gpios").write_bytes(cs)
    if symbols:
        (dt / "__symbols__" / "gpio").write_bytes(b"/soc/gpio@7e200000\0")
    (dt / "soc" / "gpio@7e200000" / "phandle").write_bytes(_be(phandle))
    if status is not None:
        (dt / "soc" / "spi@7e204000" / "status").write_bytes(status)
    return dt


KERNEL_CS = _be(GPIO_PHANDLE, 8, 1, GPIO_PHANDLE, 7, 1)


@pytest.mark.parametrize("text, mode", [
    (None, "off"),
    ("arm_64bit=1\n", "off"),
    ("dtparam=spi=on\n", "kernel"),
    ("dtparam=spi=on\ndtoverlay=spi0-0cs\n", "soft"),
    ("dtparam=spi=on\n# dtoverlay=spi0-0cs\n", "kernel"),
    ("dtparam=spi=on\ndtoverlay=spi0-1cs\n", "unsupported"),
    ("dtparam=spi=on\ndtoverlay=spi0-2cs,cs0_pin=24\n", "unsupported"),
])
def test_the_boot_file_names_one_of_the_two_supported_modes(text, mode):
    assert spi_mode.boot_mode(text) == mode


@pytest.mark.parametrize("kw, mode", [
    ({"cs": b""}, "soft"),                                            # spi0-0cs: cs-gpios;
    ({"cs": KERNEL_CS}, "kernel"),                                    # <&gpio 8 1>, <&gpio 7 1>
    ({"cs": _be(GPIO_PHANDLE, 8, 1, GPIO_PHANDLE, 24, 1)}, "unsupported"),   # remapped CE1
    ({"cs": _be(GPIO_PHANDLE, 8, 1)}, "unsupported"),                 # spi0-1cs
    ({"cs": _be(GPIO_PHANDLE, 7, 1, GPIO_PHANDLE, 8, 1)}, "unsupported"),    # swapped
    ({"cs": _be(0x77, 8, 1, 0x77, 7, 1)}, "unsupported"),             # another GPIO controller
    ({"cs": _be(GPIO_PHANDLE, 8, 0, GPIO_PHANDLE, 7, 0)}, "unsupported"),    # other flags
    ({"cs": KERNEL_CS, "symbols": False}, "unsupported"),             # the phandle unprovable
    ({"cs": None}, "unsupported"),                                    # no cs-gpios at all
    ({"alias": False}, "off"),                                        # no SPI in the tree
    ({"cs": KERNEL_CS, "status": b"disabled\0"}, "off"),             # SPI disabled, alias kept
    ({"cs": KERNEL_CS, "status": b"okay\0"}, "kernel"),
])
def test_the_booted_tree_is_read_from_spi0s_full_cs_gpios_specifiers(tmp_path, kw, mode):
    assert spi_mode.live_mode(_tree(tmp_path, **kw)) == mode


@pytest.mark.parametrize("boot, live, why", [
    ("soft", "soft", ""),
    ("kernel", "kernel", ""),
    ("off", "off", ""),
    ("kernel", "soft", "reboot required"),          # edited to kernel, not rebooted
    ("soft", "kernel", "reboot required"),          # edited back to soft, not rebooted
    ("kernel", "unsupported", "unsupported SPI boot configuration"),   # booted another overlay
    ("unsupported", "kernel", "unsupported SPI boot configuration"),
])
def test_the_mode_is_valid_only_when_file_and_kernel_agree(boot, live, why):
    got = spi_mode.refusal(boot, live)
    assert (got == "") if not why else got.startswith(why)


BASE = """Lora:
# 868 MHz
  Module: RF95
  CS: 7
  IRQ: 16      # DIO0

# 433 MHz
#  CS: 8

Logging:
  LogLevel: info
"""


def test_soft_mode_leaves_the_rendered_yaml_as_it_is():
    assert spi_mode.meshtasticd_yaml(BASE, "soft") == BASE


@pytest.mark.parametrize("cs, dev", [("7", "spidev0.1"), ("8", "spidev0.0")])
def test_kernel_mode_names_the_ce_spidev_and_drops_the_gpio_cs(cs, dev):
    got = spi_mode.meshtasticd_yaml(BASE.replace("CS: 7", f"CS: {cs}"), "kernel")
    assert f"  spidev: {dev}\n" in got and "\n  CS:" not in got
    assert "#  CS: 8" in got and "  IRQ: 16" in got                 # the rest is untouched


def test_kernel_mode_replaces_a_spidev_line_already_there():
    got = spi_mode.meshtasticd_yaml(BASE.replace("  IRQ:", "  spidev: spidev0.0\n  IRQ:"),
                                    "kernel")
    assert got.count("spidev:") == 1 and "spidev: spidev0.1" in got


def test_kernel_mode_refuses_a_cs_off_ce0_ce1():
    with pytest.raises(ValueError, match="meshtastic's CS is GPIO 21"):
        spi_mode.meshtasticd_yaml(BASE.replace("CS: 7", "CS: 21"), "kernel")


def test_rollback_to_soft_renders_the_gpio_cs_again(monkeypatch):
    """The YAML is rendered from the base at every start: after a reboot back to soft chip
    selects the next start writes `CS:` again and no `spidev:`."""
    monkeypatch.setattr(spi_mode, "live_mode", lambda dt=None: "kernel")
    assert "spidev: spidev0.1" in spi_mode.render("yaml-update", BASE)
    monkeypatch.setattr(spi_mode, "live_mode", lambda dt=None: "soft")
    again = spi_mode.render("yaml-update", BASE)
    assert "  CS: 7\n" in again and "spidev" not in again
    assert spi_mode.render("toml-update", "x = 1\n") == "x = 1\n"


# --- the start gate -----------------------------------------------------------------------

def _start(tmp_path, monkeypatch, boot, live, spi=True):
    monkeypatch.setattr(spi_mode, "read_config_txt", lambda paths=None: boot)
    monkeypatch.setattr(spi_mode, "live_mode", lambda dt=None: live)
    res = (ResourceClaim(key="spi.bus.0", kind=ResourceKind.SPI_BUS,
                         mode=ResourceMode.COOPERATIVE),) if spi else ()
    comp = Component(id="radio", name="radio", kind=ComponentKind.SERVICE, resources=res,
                     run_argv=("/bin/true",))
    life = Lifecycle(Paths(runtime_root=tmp_path), (), Config(operator=OperatorConfig()),
                     FakeSystem().system)
    return life.start(Stack(id="s", name="s", components=(comp,)), comp)


def test_a_bus_user_is_refused_while_the_boot_file_waits_for_a_reboot(tmp_path, monkeypatch):
    got = _start(tmp_path, monkeypatch, "dtparam=spi=on\n", "soft")
    assert not got.ok and got.detail.startswith("reboot required")


def test_a_bus_user_is_refused_on_a_booted_unsupported_layout(tmp_path, monkeypatch):
    got = _start(tmp_path, monkeypatch, "dtparam=spi=on\n", "unsupported")
    assert not got.ok and got.detail.startswith("unsupported SPI boot configuration")


def test_a_component_off_the_bus_is_never_gated(tmp_path, monkeypatch):
    got = _start(tmp_path, monkeypatch, "dtparam=spi=on\n", "unsupported", spi=False)
    assert "SPI" not in (got.detail or "")


SOFT_TXT, KERNEL_TXT = "dtparam=spi=on\ndtoverlay=spi0-0cs\n", "dtparam=spi=on\n"


def _real_spi_users():
    from lhpc.core.manifest import load_manifest
    by_id = {c.id: (s, c) for s in load_manifest() for c in s.components}
    return [by_id["loraham-daemon"], by_id["meshtastic"]]


@pytest.mark.parametrize("boot, live", [(SOFT_TXT, "kernel"), (KERNEL_TXT, "soft")])
@pytest.mark.parametrize("who", [0, 1])                     # the daemon, meshtastic
def test_both_stacks_are_refused_in_both_directions_until_a_reboot(tmp_path, monkeypatch, boot, live, who):
    asks = "soft" if boot == SOFT_TXT else "kernel"
    stack, comp = _real_spi_users()[who]
    monkeypatch.setattr(spi_mode, "read_config_txt", lambda paths=None: boot)
    monkeypatch.setattr(spi_mode, "live_mode", lambda dt=None: live)
    life = Lifecycle(Paths(runtime_root=tmp_path), (), Config(operator=OperatorConfig()), FakeSystem().system)
    got = life.start(stack, comp)
    assert not got.ok and got.detail == (f"reboot required: config.txt asks for {asks} SPI chip selects, but the "
                                         f"running kernel booted with {live}")


@pytest.mark.parametrize("boot, live, want", [(SOFT_TXT, "kernel", "asks for soft SPI chip selects, but the running kernel "
                                                                    "booted with kernel"),
                                             (KERNEL_TXT, "soft", "asks for kernel SPI chip selects, but the running kernel "
                                                                  "booted with soft"),
                                             (KERNEL_TXT, "kernel", None)])
def test_a_running_spi_user_is_warned_when_config_txt_now_disagrees(monkeypatch, boot, live, want):
    """`stack start` on a running stack answers "already running" without reaching the start gate
    (live row L4.5): each running bus user then gets a warning that its next start is refused."""
    monkeypatch.setattr(spi_mode, "read_config_txt", lambda paths=None: boot)
    monkeypatch.setattr(spi_mode, "live_mode", lambda dt=None: live)
    order = _real_spi_users()
    got = Lifecycle.spi_running_warnings(order)
    if want is None:
        assert got == []
    else:
        assert [line.split(":", 1)[0] for line in got] == ["  [warning] loraham-daemon", "  [warning] meshtastic"]
        assert all("running, but its next start is refused: reboot required: config.txt " + want in line
                   for line in got)


# --- provenance: with kernel chip selects, what runs must contain kernel_cs_since --------------

def _clone(tmp_path):
    """A clone with three commits: old -> since -> newer."""
    import subprocess
    repo = tmp_path / "src"
    repo.mkdir()

    def git(*a):
        return subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c",
                               "user.email=t@example.invalid", *a], check=True,
                              capture_output=True, text=True).stdout.strip()
    git("init", "-q")
    shas = []
    for n in ("old", "since", "newer"):
        (repo / "f").write_text(n)
        git("add", "f")
        git("commit", "-qm", n)
        shas.append(git("rev-parse", "HEAD"))
    return repo, dict(zip(("old", "since", "newer"), shas, strict=True)), git


def _no_receipt(monkeypatch):
    from lhpc.core import binary_receipt
    monkeypatch.setattr(binary_receipt, "receipt_state", lambda paths, sid: ("absent", None, ""))


@pytest.mark.parametrize("head, refused", [("old", True), ("since", False), ("newer", False)])
def test_a_daemon_source_checkout_must_contain_the_kernel_cs_commit(tmp_path, monkeypatch, head,
                                                                     refused):
    _no_receipt(monkeypatch)
    repo, sha, git = _clone(tmp_path)
    git("checkout", "-q", sha[head])
    comp = Component(id="loraham-daemon", name="d", kind=ComponentKind.SERVICE,
                     kernel_cs_since=sha["since"])
    why = spi_mode.provenance_refusal(None, Stack(id="daemon", name="d", components=(comp,)), comp,
                                      lambda c: repo)
    assert bool(why) is refused
    if refused:
        assert why.startswith("loraham-daemon was built from") and "lhpc update daemon" in why


@pytest.mark.parametrize("verified, built, refused", [
    (True, "since", False), (True, "old", True), (False, "since", True)])
def test_the_binary_channel_reads_its_verified_receipt(tmp_path, monkeypatch, verified, built,
                                                       refused):
    from types import SimpleNamespace

    from lhpc.core import binary_receipt
    repo, sha, _git = _clone(tmp_path)
    rec = SimpleNamespace(components={"loraham-daemon": sha[built]})
    monkeypatch.setattr(binary_receipt, "receipt_state", lambda paths, sid: ("valid", rec, ""))
    monkeypatch.setattr(binary_receipt, "verify_files", lambda paths, r: (verified, []))
    comp = Component(id="loraham-daemon", name="d", kind=ComponentKind.SERVICE,
                     kernel_cs_since=sha["since"])
    why = spi_mode.provenance_refusal(None, Stack(id="daemon", name="d", components=(comp,)), comp,
                                      lambda c: repo)
    assert bool(why) is refused


def _binary_at(monkeypatch, commit):
    from types import SimpleNamespace

    from lhpc.core import binary_receipt
    rec = SimpleNamespace(components={"loraham-daemon": commit})
    monkeypatch.setattr(binary_receipt, "receipt_state", lambda paths, sid: ("valid", rec, ""))
    monkeypatch.setattr(binary_receipt, "verify_files", lambda paths, r: (True, []))


@pytest.mark.parametrize("built, admitted", [("pin", True), ("other", False)])
def test_a_binary_box_without_history_admits_the_pinned_build_only(tmp_path, monkeypatch, built,
                                                                   admitted):
    """The shipped layout: a verified binary receipt, a source dir that is no git repository. A
    build at the manifest's pin is admitted (CI proves the pin contains kernel_cs_since); any other
    commit cannot be checked there and is refused as such, never as "predates"."""
    _repo, sha, _git = _clone(tmp_path)
    nogit = tmp_path / "binary-tree"
    nogit.mkdir()
    _binary_at(monkeypatch, sha["newer"] if built == "pin" else "e" * 40)
    comp = Component(id="loraham-daemon", name="d", kind=ComponentKind.SERVICE,
                     kernel_cs_since=sha["since"],
                     source=SourceSpec(path="src/loraham-daemon", pin_commit=sha["newer"]))
    why = spi_mode.provenance_refusal(None, Stack(id="daemon", name="d", components=(comp,)), comp,
                                      lambda c: nogit)
    assert (why == "") is admitted
    if not admitted:
        assert "cannot be checked here" in why and "predates" not in why
        assert "lhpc update daemon --source pinned --yes" in why


def test_a_shallow_dev_checkout_is_refused_as_unverifiable(tmp_path, monkeypatch):
    """A `dev` install is a --depth 1 clone: its history does not hold kernel_cs_since."""
    import subprocess
    _no_receipt(monkeypatch)
    repo, sha, _git = _clone(tmp_path)
    shallow = tmp_path / "shallow"
    subprocess.run(["git", "clone", "-q", "--depth", "1", f"file://{repo}", str(shallow)], check=True)
    comp = Component(id="loraham-daemon", name="d", kind=ComponentKind.SERVICE,
                     kernel_cs_since=sha["since"],
                     source=SourceSpec(path="src/loraham-daemon", pin_commit=sha["old"]))
    why = spi_mode.provenance_refusal(None, Stack(id="daemon", name="d", components=(comp,)), comp,
                                      lambda c: shallow)
    assert why == (f"loraham-daemon was built from {sha['newer'][:9]}, which cannot be checked here against "
                   "kernel chip selects (its checkout holds no history to compare) "
                   f"({sha['since'][:9]}): update it (`lhpc update daemon --source pinned --yes`) "
                   "before starting it with kernel chip selects")


@pytest.mark.parametrize("state, refused", [("unsafe", True), ("superseded", True)])
def test_a_receipt_that_is_present_but_not_valid_refuses(tmp_path, monkeypatch, state, refused):
    """A binary daemon with a broken or superseded receipt is never judged by a clone that may
    already sit at the new pin: both are faults lhpc doctor names, not a source install."""
    from lhpc.core import binary_receipt
    repo, sha, git = _clone(tmp_path)
    git("checkout", "-q", sha["since"])
    monkeypatch.setattr(binary_receipt, "receipt_state", lambda paths, sid: (state, None, "x"))
    comp = Component(id="loraham-daemon", name="d", kind=ComponentKind.SERVICE,
                     kernel_cs_since=sha["since"])
    why = spi_mode.provenance_refusal(None, Stack(id="daemon", name="d", components=(comp,)), comp,
                                      lambda c: repo)
    assert bool(why) is refused
    if refused:
        assert "has no recorded commit to check against kernel chip selects" in why


@pytest.mark.parametrize("consumed, refused", [("since", False), ("old", True), (None, True)])
def test_a_build_dependency_is_read_from_the_users_completion_marker(tmp_path, monkeypatch,
                                                                     consumed, refused):
    """Reticulum: the SPI driver is a library built into `rns`; the marker names its commit."""
    _no_receipt(monkeypatch)
    lib_repo, sha, _git = _clone(tmp_path)
    rns_src = tmp_path / "rns"
    (rns_src / ".venv").mkdir(parents=True)
    if consumed:
        (rns_src / ".venv" / "done").write_text(
            f"lhpc build complete\nconsumed rns-lora-interface {sha[consumed]}\n")
    lib = Component(id="rns-lora-interface", name="l", kind=ComponentKind.LIBRARY,
                    kernel_cs_since=sha["since"])
    rns = Component(id="rns", name="r", kind=ComponentKind.SERVICE, build_marker=".venv/done",
                    build_requires=("rns-lora-interface",))
    why = spi_mode.provenance_refusal(None, Stack(id="reticulum", name="r", components=(rns, lib)),
                                      rns, lambda c: lib_repo if c is lib else rns_src)
    assert bool(why) is refused


def test_the_start_gate_refuses_an_old_build_only_with_kernel_chip_selects(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(spi_mode, "provenance_refusal",
                        lambda *a: calls.append(a) or "rns-lora-interface was built from …")
    got = _start(tmp_path, monkeypatch, "dtparam=spi=on\n", "kernel")
    assert not got.ok and got.detail.startswith("rns-lora-interface was built from")
    calls.clear()
    got = _start(tmp_path, monkeypatch, "dtparam=spi=on\ndtoverlay=spi0-0cs\n", "soft")
    assert calls == []                              # soft mode: no provenance check at all


def test_a_real_source_adoption_turns_an_admitted_binary_into_a_refused_one(tmp_path):
    """Through the real receipt and registry: a verified binary install is judged by its receipt
    (admitted); once the source is adopted over it, binary_receipt.receipt_state reads
    "superseded" (a fault `lhpc doctor` names) and the gate refuses."""
    import hashlib

    from lhpc.core import binary_receipt, source_registry
    repo, sha, _git = _clone(tmp_path)
    rt = tmp_path / "rt"
    paths = Paths(runtime_root=rt)
    rel = "src/loraham-daemon/loraham_daemon/loraham_daemon"
    (rt / rel).parent.mkdir(parents=True)
    (rt / rel).write_bytes(b"daemon")
    assert binary_receipt.write_receipt(paths, binary_receipt.BinaryReceipt(
        stack="daemon", artifact_sha256="a" * 64, artifact_size=6,
        filename="daemon-" + "a" * 64 + ".tar.zst", url="https://example.invalid/d.tar.zst",
        components={"loraham-daemon": sha["since"]}, provenance={}, files=(rel,),
        file_hashes={rel: hashlib.sha256(b"daemon").hexdigest()}, proof_paths=(rel,),
        registry_baseline={"src/loraham-daemon": ""}, probe="d"))
    comp = Component(id="loraham-daemon", name="d", kind=ComponentKind.SERVICE,
                     kernel_cs_since=sha["since"])
    stack = Stack(id="daemon", name="d", components=(comp,))
    assert binary_receipt.receipt_state(paths, "daemon")[0] == "valid"
    assert spi_mode.provenance_refusal(paths, stack, comp, lambda c: repo) == ""
    assert source_registry.write_record(paths, source_registry.RegistryRecord(
        source_rel="src/loraham-daemon", remote="https://example.invalid/d.git",
        selector="pinned", resolved_commit=sha["since"], adopted_at=1.0, txn_id="txn-adopt",
        components=("loraham-daemon",)))
    assert binary_receipt.receipt_state(paths, "daemon")[0] == "superseded"
    why = spi_mode.provenance_refusal(paths, stack, comp, lambda c: repo)
    assert "has no recorded commit to check against kernel chip selects" in why
