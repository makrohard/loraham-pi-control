"""Item 8 — permanently fix the meshtastic stack on Uputronics hardware.

Two field-verified defects, both covered here:

1. The generated `meshtasticd.yaml` claimed GPIOs that don't exist on the Uputronics RF95 board:
   `Reset` (the board has NO reset line — NC) and `Busy` (RF95 has no BUSY pin). BCM 6/13 are the
   board LEDs, which the running LoRaHAM daemon holds via lgpio, so meshtasticd's request for line 6
   failed EBUSY and aborted radio init. The base template (`LoRaHAM_Pi/meshtastic/config.yaml`) now
   declares ONLY `CS` + `IRQ`; this test proves the yaml generator preserves that corrected pin set
   (never re-injecting Reset/Busy while it applies the Webserver params).

2. The apt package's ROOT `meshtasticd.service` grabs the shared SPI radio, so the rootless stack can
   never own it. A new `absent_file` (must-not-run) requirement names its enabled wants-symlink: it is
   a RUN-TIME capability (surfaced + blocks start, never blocks install), and when the service is active
   without being enabled the run-failure path names it as the likely cause.
"""

from pathlib import Path

from lhpc.core.config import update_yaml
from lhpc.core.model import FileParam
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem, CommandResult
from lhpc.core.services import ControllerService

# The enabled-service symlink the must-not-run requirement watches.
SYMLINK = "/etc/systemd/system/multi-user.target.wants/meshtasticd.service"
UNIT = "meshtasticd.service"

# The corrected Uputronics base (matches LoRaHAM_Pi/meshtastic/config.yaml): CS + IRQ only, and a
# Logging section the generator DOES drive (the web PORT is no longer a param — it is fixed at
# 9443 in the packaged base) — so a passing test proves the Lora block survives regeneration
# untouched while a real param is applied.
CORRECTED_BASE = """\
---
owner: LoRaHAM_Pi
Lora:
# 868 MHz
  Module: RF95
  CS: 7
  IRQ: 16      # DIO0

# 433 MHz
#  Module: "RF95"
#  CS: 8
#  IRQ: 25      # DIO0

Logging:
  LogLevel: info
"""


def _svc(tmp_path, paths=(), commands=None):
    fake = FakeSystem(paths=set(paths), commands=dict(commands or {}))
    return ControllerService(system=fake.system, paths=Paths(runtime_root=Path(tmp_path))), fake


def _meshtastic_comp(svc):
    return next(c for s in svc.stacks() for c in s.components if c.id == "meshtastic")


# --- 1. template regeneration produces the corrected pin set --------------------------------------

def test_template_regeneration_produces_corrected_uputronics_pins(tmp_path):
    svc, _ = _svc(tmp_path)
    comp = _meshtastic_comp(svc)
    fc = comp.config_file
    assert fc and fc.fmt == "yaml-update"
    # Regenerate exactly as the writer does: base text + this component's declared params.
    out = update_yaml(CORRECTED_BASE, fc.params, {"loglevel": "debug"}, lambda s: s)

    # The ACTIVE (uncommented) 868 block carries only the real pins ...
    active = out.split("# 433 MHz", 1)[0]
    assert "Module: RF95" in active and "CS: 7" in active and "IRQ: 16" in active
    # ... and never the phantom Reset / Busy that caused the EBUSY radio-init abort.
    assert "Reset:" not in out and "Busy:" not in out
    # The generator DID run (a real param applied), so the untouched Lora block is a real pass-through.
    assert "LogLevel: debug" in out


# The PRE-FIX base still carries the harmful pins (as HEAD 1706725 does) — used to prove the fix lives
# in lhpc: the optional-absent + omit_if_empty params must OMIT them even from a stale base.
STALE_BASE = """\
---
owner: LoRaHAM_Pi
Lora:
# 868 MHz
  Module: RF95
  CS: 7
  IRQ: 16      # DIO0
  Reset: 6
  Busy: 12
Webserver:
  Port: 0
"""


def test_meshtastic_reset_busy_params_are_optional_absent(tmp_path):
    # Item 1: no default, no band_defaults, omit_if_empty -> the common (Uputronics) case leaves them
    # unset and the key is omitted. Kept advanced so an exotic board can still set a real pin.
    fc = _meshtastic_comp(_svc(tmp_path)[0]).config_file
    reset = next(p for p in fc.params if p.name == "reset")
    busy = next(p for p in fc.params if p.name == "busy")
    for p in (reset, busy):
        assert p.omit_if_empty and p.default == "" and p.band_defaults == () and p.advanced


def test_meshtastic_omits_reset_busy_even_from_a_stale_base(tmp_path):
    # Item 1 (authoritative fix in lhpc): a base that STILL carries Reset: 6 / Busy: 12 (pre-fix
    # template, or a regeneration path that bypasses the base hygiene) generates a meshtasticd.yaml with
    # NEITHER — the params remove the harmful active keys rather than writing an empty value.
    fc = _meshtastic_comp(_svc(tmp_path)[0]).config_file
    out = update_yaml(STALE_BASE, fc.params, {}, lambda s: s)
    assert "Module: RF95" in out and "CS: 7" in out and "IRQ: 16" in out
    for line in out.splitlines():
        s = line.strip()
        assert not (s.startswith("Reset:") or s.startswith("Busy:")), f"harmful pin survived: {line!r}"


def test_meshtastic_exotic_board_can_still_set_reset(tmp_path):
    # Advanced escape hatch: a board that genuinely has the pin can set it (base line updated).
    fc = _meshtastic_comp(_svc(tmp_path)[0]).config_file
    out = update_yaml(STALE_BASE, fc.params, {"reset": "18", "busy": "20"}, lambda s: s)
    assert "Reset: 18" in out and "Busy: 20" in out


def test_generated_868_from_shipped_base_has_no_reset_busy(tmp_path):
    # Against the ACTUAL base lhpc ships (package data — no clone, always present, so this
    # never skips) + defaults, the active 868 block is Module/CS/IRQ with no Reset/Busy.
    from lhpc.core.assets import asset_text
    fc = _meshtastic_comp(_svc(tmp_path)[0]).config_file
    assert fc.base == "{asset}/bases/meshtasticd.yaml"
    out = update_yaml(asset_text("bases/meshtasticd.yaml"), fc.params, {}, lambda s: s)
    active = out.split("# 433 MHz", 1)[0]                      # the uncommented 868 block
    assert "Module: RF95" in active and "CS: 7" in active and "IRQ: 16" in active
    for line in active.splitlines():
        s = line.strip()
        assert not (s.startswith("Reset:") or s.startswith("Busy:"))


# --- 2. dep-gate / overview cover the must-not-run check -------------------------------------------

def test_enabled_service_is_runtime_not_install_gated_and_blocks_start(tmp_path):
    # Symlink present => the packaged root service is ENABLED.
    svc, _ = _svc(tmp_path, paths=(SYMLINK, "/usr/bin/meshtasticd", "/dev/spidev0.0"))

    # A run-time capability: it NEVER blocks (or warns) the install gate.
    gate = svc.install_dep_gate("meshtastic")
    assert not any("meshtasticd.service" in (d["what"] or "") for d in gate["block"] + gate["warn"])

    # It DOES surface in the system-dependencies view: unsatisfied, runtime, with the disable command.
    must = [d for d in svc.system_deps("meshtastic") if "meshtasticd.service" in (d["what"] or "")]
    assert must and must[0]["runtime"] and not must[0]["satisfied"]
    assert must[0]["install"] == "sudo systemctl disable --now meshtasticd"

    # ... and the dependency-report/overview carries it too, with a copyable install_cmd.
    report = svc.deps_report("meshtastic")
    row = next(d for d in report["system"] if "meshtasticd.service" in (d.label or ""))
    assert not row.satisfied and row.runtime
    assert row.install_cmd == "sudo systemctl disable --now meshtasticd"

    # Enabled => START is blocked (fail-closed): the requirement is unsatisfied.
    miss = svc._lifecycle().missing_requirements(_meshtastic_comp(svc))
    assert any(r.absent_file == SYMLINK for r in miss)


def test_disabled_service_satisfies_the_requirement(tmp_path):
    # No symlink => not enabled => satisfied, and start is not blocked on it.
    svc, _ = _svc(tmp_path, paths=("/usr/bin/meshtasticd", "/dev/spidev0.0"))
    must = [d for d in svc.system_deps("meshtastic") if "meshtasticd.service" in (d["what"] or "")]
    assert must and must[0]["satisfied"] and must[0]["runtime"]
    miss = svc._lifecycle().missing_requirements(_meshtastic_comp(svc))
    assert not any(r.absent_file for r in miss)


# --- 3. run-failure path renders the system-service hint -------------------------------------------

def _isactive(rc):
    return {("systemctl", "is-active", "--quiet", UNIT): CommandResult(returncode=rc, stdout="", stderr="")}


def test_run_failure_hint_names_active_system_service(tmp_path):
    # Active-but-not-enabled: the symlink is absent (start proceeds), yet the root service is running and
    # holds the radio -> the readiness-failure detail must name it with the disable copybox.
    svc, _ = _svc(tmp_path, commands=_isactive(0))
    hint = svc._conflicting_service_hint(_meshtastic_comp(svc))
    assert "meshtasticd.service" in hint and "ACTIVE" in hint
    assert "sudo systemctl disable --now meshtasticd" in hint


def test_run_failure_hint_absent_when_service_inactive_or_missing(tmp_path):
    # Inactive service -> no hint (only ever ADDS signal).
    svc_i, _ = _svc(tmp_path, commands=_isactive(3))
    assert svc_i._conflicting_service_hint(_meshtastic_comp(svc_i)) == ""
    # systemctl not found (bare FakeSystem) -> still no hint, no spurious blame.
    svc_n, _ = _svc(tmp_path)
    assert svc_n._conflicting_service_hint(_meshtastic_comp(svc_n)) == ""


# --- update_yaml omit_if_empty machinery (unit) ---------------------------------------------------

def test_update_yaml_omit_if_empty_removes_active_key_but_keeps_commented():
    base = "Lora:\n  CS: 7\n  Reset: 6\n  Busy: 12\n#  Reset: 22\n"
    reset = FileParam(name="reset", key="Reset", section="Lora", kind="int", omit_if_empty=True)
    busy = FileParam(name="busy", key="Busy", section="Lora", kind="int", omit_if_empty=True)
    out = update_yaml(base, [reset, busy], {}, lambda s: s)          # unset -> omit
    assert "CS: 7" in out
    assert not any(ln.strip().startswith("Reset:") for ln in out.splitlines())   # active dropped
    assert not any(ln.strip().startswith("Busy:") for ln in out.splitlines())
    assert "#  Reset: 22" in out                                    # commented example preserved


def test_update_yaml_omit_if_empty_set_value_is_written():
    base = "Lora:\n  CS: 7\n  Reset: 6\n"
    reset = FileParam(name="reset", key="Reset", section="Lora", kind="int", omit_if_empty=True)
    out = update_yaml(base, [reset], {"reset": "18"}, lambda s: s)
    assert "Reset: 18" in out


def test_update_yaml_non_omit_blank_leaves_base_as_is():
    # Regression: a NON-omit param left blank keeps the base value (unchanged legacy behavior).
    base = "Lora:\n  CS: 7\n"
    cs = FileParam(name="cs", key="CS", section="Lora", kind="int", default="")
    out = update_yaml(base, [cs], {}, lambda s: s)
    assert "CS: 7" in out


# --- packaged base: no clone needed, read straight from lhpc package data --------------------

def test_meshtastic_config_base_ships_with_lhpc_and_needs_no_hardware_clone(tmp_path):
    # The yaml template ships WITH lhpc, so a fresh install never clones the LoRaHAM_Pi hardware
    # repo (schematics/photos/pySX127x) just to read one file. The component DOES declare a source
    # now — upstream meshtastic/firmware, cloned to BUILD the server-only daemon, which is a
    # different thing entirely from cloning a hardware repo for a config file.
    comp = _meshtastic_comp(_svc(tmp_path)[0])
    assert comp.config_file.base.startswith("{asset}/")
    assert "LoRaHAM_Pi" not in (comp.source.remote if comp.source else "")
    assert comp.source is not None and comp.source.remote.endswith("meshtastic/firmware.git")


def test_packaged_base_is_shipped_and_readable():
    # The base must resolve from package data (wheel-safe path), not a repo checkout.
    from lhpc.core.assets import asset_text
    text = asset_text("bases/meshtasticd.yaml")
    assert "Module: RF95" in text and "CS: 7" in text
    assert "SHIPPED WITH lhpc" in text                  # provenance/edit guidance for operators


def test_packaged_base_is_declared_package_data():
    # A `data/*.toml` glob alone would ship the manifest but silently DROP the base, so an
    # installed wheel would fail config generation with a missing asset.
    import tomllib
    from pathlib import Path as _P
    pj = tomllib.loads((_P(__file__).resolve().parents[1] / "pyproject.toml").read_text())
    globs = pj["tool"]["setuptools"]["package-data"]["lhpc"]
    assert any(g.startswith("data/bases/") for g in globs), globs


def test_asset_base_generates_without_any_source(tmp_path):
    # End-to-end: config generation succeeds for a component with NO source dir on disk.
    svc, _ = _svc(tmp_path)
    written = svc.write_config_files("meshtastic", "868")
    row = next(w for w in written if w.component == "meshtastic")
    assert row.status == "written", (row.status, row.detail)
    text = (tmp_path / "config" / "files" / "meshtasticd.yaml").read_text()
    active = text.split("# 433 MHz", 1)[0]
    assert "Module: RF95" in active
    assert not any(ln.strip().startswith(("Reset:", "Busy:")) for ln in active.splitlines())


def test_asset_policy_rejected_for_generated_destination(tmp_path):
    # {asset} is READ-ONLY package data: valid as a base, never as a write destination.
    svc, _ = _svc(tmp_path)
    comp = _meshtastic_comp(svc)
    dest = svc._resolve_config_dest(comp, "{asset}/bases/meshtasticd.yaml", for_base=False)
    assert dest.status == "failed" and "read-only" in dest.detail
    ok = svc._resolve_config_dest(comp, "{asset}/bases/meshtasticd.yaml", for_base=True)
    assert ok.status == "ok" and ok.policy == "asset"


def test_asset_base_traversal_rejected(tmp_path):
    svc, _ = _svc(tmp_path)
    comp = _meshtastic_comp(svc)
    bad = svc._resolve_config_dest(comp, "{asset}/../secrets.example.toml", for_base=True)
    assert bad.status == "failed"


# --- F2/F7: the packaged base is the AUTHORITATIVE hardware config ----------------------------

def test_generated_yaml_never_references_an_overlay_config_directory(tmp_path):
    # meshtasticd loads YAML fragments from a ConfigDirectory AFTER the -c file, so a leftover
    # packaged example there would silently override the CS/IRQ/module/web settings lhpc
    # generates. The base must not point at one, and neither must the generated file.
    from lhpc.core.assets import asset_text
    base = asset_text("bases/meshtasticd.yaml")
    svc, _ = _svc(tmp_path)
    svc.write_config_files("meshtastic", "868")
    generated = (tmp_path / "config" / "files" / "meshtasticd.yaml").read_text()
    for text, what in ((base, "packaged base"), (generated, "generated yaml")):
        active = [ln for ln in text.splitlines()
                  if "ConfigDirectory" in ln and not ln.lstrip().startswith("#")]
        assert active == [], f"{what} declares an overlay directory: {active}"
        assert "/etc/meshtasticd/config.d" not in text, what


def test_packaged_base_is_the_authoritative_hardware_config(tmp_path):
    # Every hardware key the radio depends on comes from the base we ship — not from an
    # operator-editable overlay and not from the apt package's examples.
    svc, _ = _svc(tmp_path)
    svc.write_config_files("meshtastic", "868")
    generated = (tmp_path / "config" / "files" / "meshtasticd.yaml").read_text()
    active = generated.split("# 433 MHz", 1)[0]
    assert "Module: RF95" in active and "CS: 7" in active and "IRQ: 16" in active
    assert not any(ln.strip().startswith(("Reset:", "Busy:")) for ln in active.splitlines())


def test_web_port_is_fixed_at_9443_and_not_a_parameter(tmp_path):
    # F7: the port is declared ONCE (the packaged base) and matches the component endpoint the
    # dashboard link, the stackweb nginx upstream and the exposure audit all derive from. A
    # settable param moved only the yaml and silently broke every one of those.
    svc, _ = _svc(tmp_path)
    comp = _meshtastic_comp(svc)
    assert not any(p.name == "web_port" for p in comp.config_file.params)
    svc.write_config_files("meshtastic", "868")
    generated = (tmp_path / "config" / "files" / "meshtasticd.yaml").read_text()
    assert "Port: 9443" in generated
    assert "Port: 443 " not in generated and "Port: 443\n" not in generated
    ep = [e.address for e in comp.endpoints if e.scheme == "https"]
    assert ep == ["127.0.0.1:9443"], ep          # config and declaration agree
