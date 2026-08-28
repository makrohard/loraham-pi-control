"""MeshCore integration contracts: node identity, position handling, and how an
absent OPTIONAL component is reported.

The theme is the same one that made these bugs expensive on real hardware: every failure
here is SILENT. A rotated identity looks like a working node that nobody recognises; a
stale coordinate pair looks like a position; an absent optional helper looked like an
uninstalled stack.
"""

from __future__ import annotations

import threading

import pytest

from lhpc.core import meshcore_identity as mi
from lhpc.core.config import update_toml
from lhpc.core.model import ComponentStatus, FileParam, RunState
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService
from lhpc.core.status import Snapshot, StackStatus, rollup_states

# Greenwich — publicly known, obviously synthetic, and never anyone's home.
LAT, LON = "51.4779", "-0.0015"
# A 32-byte seed generated for this test file alone. NEVER copy a key off a real
# node into a test: it is that node's on-air identity, and a test file is public.
KEY = "3459f3299360660522b94692c0ab9c23ee24eca9b849175327265d67fe04b93f"

TEMPLATE = """interfaces = ["loraham868"]

[interface.loraham868]
preset = "eu_uk_narrow"
txpower = 14
airtime = 10
enable_tx = true

[device.companion]
name = "NOCALL"
wifi.allow = "127.0.0.1"
# privatekey = "xxxx"
"""


def _svc(tmp_path, template: str = TEMPLATE):
    (tmp_path / "config" / "stacks").mkdir(parents=True, exist_ok=True)
    base = tmp_path / "src" / "meshcore-pi" / "examples"
    base.mkdir(parents=True, exist_ok=True)
    (base / "config-loraham868.toml").write_text(template)
    return ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))


def _generated(tmp_path) -> str:
    return (tmp_path / "config" / "files" / "meshcore-pi.toml").read_text()


def _key_in(text: str) -> str:
    for line in text.splitlines():
        if line.strip().startswith("privatekey"):
            return line.split("=", 1)[1].strip().strip('"')
    return ""


def _coords(text: str) -> list:
    return [ln for ln in text.splitlines() if ln.split("=")[0].strip() in ("lat", "lon")]


# --------------------------------------------------------------------------- identity


@pytest.mark.contract
def test_identity_is_stable_across_regeneration(tmp_path):
    # The bug: meshcore-pi mints a fresh key when its config has none, so every
    # regeneration silently changed the node's public key.
    svc = _svc(tmp_path)
    svc.write_config_files("meshcore")
    first = _key_in(_generated(tmp_path))
    assert len(first) == 64
    for _ in range(3):
        svc.write_config_files("meshcore")
        assert _key_in(_generated(tmp_path)) == first


@pytest.mark.contract
def test_identity_survives_losing_the_generated_config_and_the_source(tmp_path):
    # Rebuild/update/reinstall all replace one or both of those; the key must outlive them.
    svc = _svc(tmp_path)
    svc.write_config_files("meshcore")
    first = _key_in(_generated(tmp_path))
    (tmp_path / "config" / "files" / "meshcore-pi.toml").unlink()
    (tmp_path / "src" / "meshcore-pi" / "examples" / "config-loraham868.toml").write_text(TEMPLATE)
    svc.write_config_files("meshcore")
    assert _key_in(_generated(tmp_path)) == first


@pytest.mark.contract
def test_existing_key_is_adopted_not_rotated(tmp_path):
    # Pinning the identity used to mean hand-editing the upstream template. Upgrading into
    # LHPC-owned identity must keep that node on the air, not re-mint over it.
    svc = _svc(tmp_path, TEMPLATE.replace('# privatekey = "xxxx"', f'privatekey = "{KEY}"'))
    svc.write_config_files("meshcore")
    assert _key_in(_generated(tmp_path)) == KEY
    assert mi.secret_path(svc._paths).read_text().strip() == KEY


@pytest.mark.contract
def test_adopt_prefers_the_generated_config_over_the_template(tmp_path):
    other = "11" * 32
    svc = _svc(tmp_path, TEMPLATE.replace('# privatekey = "xxxx"', f'privatekey = "{other}"'))
    gen = tmp_path / "config" / "files" / "meshcore-pi.toml"
    gen.parent.mkdir(parents=True, exist_ok=True)
    gen.write_text(f'[device.companion]\nprivatekey = "{KEY}"\n')
    assert mi.adopt_identity(svc._paths, svc.meshcore_identity_candidates()) == KEY


@pytest.mark.contract
def test_a_commented_template_key_reads_as_absent(tmp_path):
    # A commented example documents the key; it is not a key.
    svc = _svc(tmp_path)
    base = tmp_path / "src" / "meshcore-pi" / "examples" / "config-loraham868.toml"
    assert mi.candidate_key(svc._paths, base) == ""


@pytest.mark.safety("meshcore-identity")
def test_a_candidate_without_a_key_falls_through_to_the_next(tmp_path):
    svc = _svc(tmp_path)
    gen = tmp_path / "config" / "files" / "meshcore-pi.toml"
    gen.parent.mkdir(parents=True, exist_ok=True)
    gen.write_text('[device.companion]\nname = "x"\n')          # no privatekey at all
    base = tmp_path / "src" / "meshcore-pi" / "examples" / "config-loraham868.toml"
    base.write_text(TEMPLATE.replace('# privatekey = "xxxx"', f'privatekey = "{KEY}"'))
    assert mi.adopt_identity(svc._paths, svc.meshcore_identity_candidates()) == KEY


@pytest.mark.safety("meshcore-identity")
def test_an_invalid_candidate_key_blocks_instead_of_minting(tmp_path):
    # The dangerous confusion: treating "present but malformed" as "absent" would mint a NEW
    # identity over the one the operator was trying to keep.
    svc = _svc(tmp_path, TEMPLATE.replace('# privatekey = "xxxx"', 'privatekey = "deadbeef"'))
    with pytest.raises(mi.MeshCoreIdentityError):
        mi.ensure_identity(svc._paths, svc.meshcore_identity_candidates())
    assert not mi.secret_path(svc._paths).exists()


@pytest.mark.safety("meshcore-identity")
def test_a_malformed_toml_candidate_blocks_instead_of_minting(tmp_path):
    """AUDIT-FOUND: a candidate we cannot PARSE is not a candidate without a key. The
    generated config can hold the only surviving copy of the identity alongside unrelated
    TOML damage; falling through would mint a new key and then regenerate the file over the
    original — losing the identity exactly when we were trying to rescue it."""
    svc = _svc(tmp_path)
    gen = tmp_path / "config" / "files" / "meshcore-pi.toml"
    gen.parent.mkdir(parents=True, exist_ok=True)
    # Valid key present, but the document is broken further down.
    gen.write_text(f'[device.companion]\nprivatekey = "{KEY}"\nbroken = [1,\n')
    with pytest.raises(mi.MeshCoreIdentityError):
        mi.ensure_identity(svc._paths, svc.meshcore_identity_candidates())
    assert not mi.secret_path(svc._paths).exists(), "must not mint over a damaged candidate"


@pytest.mark.safety("meshcore-identity")
def test_an_invalid_stored_secret_blocks_and_is_never_replaced(tmp_path):
    svc = _svc(tmp_path)
    sp = mi.secret_path(svc._paths)
    sp.parent.mkdir(parents=True, exist_ok=True)
    sp.write_text("not-a-key\n")
    sp.chmod(0o600)
    with pytest.raises(mi.MeshCoreIdentityError):
        mi.ensure_identity(svc._paths)
    assert sp.read_text() == "not-a-key\n"


@pytest.mark.safety("meshcore-identity")
def test_a_group_readable_secret_is_refused(tmp_path):
    svc = _svc(tmp_path)
    sp = mi.secret_path(svc._paths)
    sp.parent.mkdir(parents=True, exist_ok=True)
    sp.write_text(KEY)
    sp.chmod(0o644)
    with pytest.raises(mi.MeshCoreIdentityError):
        mi.ensure_identity(svc._paths)


@pytest.mark.safety("meshcore-identity")
def test_both_pinned_key_lengths_are_accepted(tmp_path):
    # ed25519_wrapper takes a 32-byte seed OR the 64-byte Meshcore (a,RH) key. Nothing else.
    assert mi.normalize_key(KEY) == KEY
    assert mi.normalize_key("ab" * 64) == "ab" * 64
    assert mi.normalize_key("ab" * 20) == ""
    assert mi.normalize_key("zz" * 32) == ""
    assert mi.normalize_key(None) == ""


@pytest.mark.safety("meshcore-identity")
def test_secret_and_generated_config_are_0600(tmp_path):
    svc = _svc(tmp_path)
    svc.write_config_files("meshcore")
    assert mi.secret_path(svc._paths).stat().st_mode & 0o777 == 0o600
    assert mi.secret_path(svc._paths).parent.stat().st_mode & 0o777 == 0o700
    gen = tmp_path / "config" / "files" / "meshcore-pi.toml"
    assert gen.stat().st_mode & 0o777 == 0o600, "the generated file carries the private key"


@pytest.mark.safety("meshcore-identity")
def test_lhpc_never_emits_the_key(tmp_path):
    # Not "no key in any log" — historical upstream logs already contain keys and LHPC
    # cannot rewrite them. What LHPC controls is its OWN output.
    svc = _svc(tmp_path)
    writes = svc.write_config_files("meshcore")
    key = _key_in(_generated(tmp_path))
    blobs = [w.detail or "" for w in writes] + [w.summary if hasattr(w, "summary") else ""
                                                for w in writes]
    blobs.append(svc.status("meshcore").summary)
    blobs += list(svc.status("meshcore").details or ())
    blobs.append(str(svc.config_view("meshcore")))
    for blob in blobs:
        assert key not in blob


@pytest.mark.safety("meshcore-identity")
def test_concurrent_first_use_yields_one_key(tmp_path):
    # Target-exclusive creation, not an atomic replace: a rename-over would let both racers
    # "succeed" and silently discard one identity.
    svc = _svc(tmp_path)
    seen, barrier = [], threading.Barrier(4)

    def go():
        barrier.wait()
        seen.append(mi.ensure_identity(svc._paths))

    threads = [threading.Thread(target=go) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(set(seen)) == 1
    assert seen[0] == mi.secret_path(svc._paths).read_text().strip()


@pytest.mark.safety("meshcore-identity")
def test_adopt_never_mints(tmp_path):
    # Updating or uninstalling a MeshCore that was never used must not create a key.
    svc = _svc(tmp_path)
    assert mi.adopt_identity(svc._paths, svc.meshcore_identity_candidates()) == ""
    assert not mi.secret_path(svc._paths).exists()


@pytest.mark.safety("meshcore-identity")
def test_read_only_operations_create_no_key(tmp_path):
    svc = _svc(tmp_path)
    svc.status("meshcore")
    svc.start("meshcore", apply=False)
    svc.source_check("meshcore")
    svc.update("meshcore", apply=False)
    svc.uninstall("meshcore", apply=False)
    svc.clean("meshcore", apply=False)
    assert not mi.secret_path(svc._paths).exists()


@pytest.mark.safety("meshcore-identity")
def test_clean_purge_adopts_the_key_before_removing_the_source(tmp_path):
    # `clean` removes the source AND the generated config, so an identity living only in
    # those is gone unless it is copied out first. It deliberately spares config/secrets.
    svc = _svc(tmp_path, TEMPLATE.replace('# privatekey = "xxxx"', f'privatekey = "{KEY}"'))
    res = svc.clean("meshcore", apply=True, purge=True)
    assert res.ok or "running" not in (res.summary or "")
    assert mi.secret_path(svc._paths).read_text().strip() == KEY


@pytest.mark.safety("meshcore-identity")
def test_uninstall_adopts_the_key_before_removing_the_source(tmp_path):
    svc = _svc(tmp_path, TEMPLATE.replace('# privatekey = "xxxx"', f'privatekey = "{KEY}"'))
    svc.uninstall("meshcore", apply=True)
    assert mi.secret_path(svc._paths).read_text().strip() == KEY


@pytest.mark.safety("meshcore-identity")
def test_a_destructive_op_refuses_on_an_invalid_key_rather_than_destroying_it(tmp_path):
    svc = _svc(tmp_path, TEMPLATE.replace('# privatekey = "xxxx"', 'privatekey = "nope"'))
    res = svc.clean("meshcore", apply=True, purge=True)
    assert not res.ok
    assert (tmp_path / "src" / "meshcore-pi").is_dir(), "the source must still be there"


# --------------------------------------------------------------------------- update_toml


@pytest.mark.contract
def test_a_set_key_absent_from_the_template_is_inserted():
    lat = FileParam(name="lat", key="lat", section="device.companion", kind="float",
                    hidden=True, omit_if_empty=True)
    out = update_toml(TEMPLATE, [lat], {"lat": LAT}, lambda s: s)
    assert f"lat = {LAT}" in out


@pytest.mark.contract
def test_an_empty_omit_if_empty_param_removes_a_stale_key_but_keeps_comments():
    # The live box had lat/lon hand-added to the template. Leaving them would keep
    # publishing a position after GPS was switched off.
    stale = TEMPLATE.replace('name = "NOCALL"', f'name = "NOCALL"\nlat = {LAT}\nlon = {LON}')
    lat = FileParam(name="lat", key="lat", section="device.companion", kind="float",
                    hidden=True, omit_if_empty=True)
    lon = FileParam(name="lon", key="lon", section="device.companion", kind="float",
                    hidden=True, omit_if_empty=True)
    out = update_toml(stale, [lat, lon], {"lat": "", "lon": ""}, lambda s: s)
    assert _coords(out) == []
    assert "# privatekey" in out, "a commented example is documentation, not a stale value"


@pytest.mark.contract
def test_a_missing_target_section_is_an_error():
    p = FileParam(name="lat", key="lat", section="device.companion", kind="float")
    with pytest.raises(ValueError, match="no section"):
        update_toml("[other]\na = 1\n", [p], {"lat": LAT}, lambda s: s)


@pytest.mark.contract
def test_the_result_is_validated_before_it_is_returned():
    p = FileParam(name="name", key="name", section="device.companion", kind="str")
    with pytest.raises(ValueError):
        update_toml('[device.companion]\nname = "x"\nbroken = [1,\n',
                    [p], {"name": "y"}, lambda s: s)


@pytest.mark.contract
def test_a_blank_ordinary_param_still_leaves_the_base_alone():
    # `frequency` blank means "let the preset own it" — not "delete the line".
    p = FileParam(name="txpower", key="txpower", section="interface.loraham868", kind="int")
    out = update_toml(TEMPLATE, [p], {"txpower": ""}, lambda s: s)
    assert "txpower = 14" in out


# --------------------------------------------------------------------------- position


def _tpv(lat=51.4779, lon=-0.0015, mode=3) -> bytes:
    """One gpsd TPV line. `mode` 0/1 means "no fix" — the case that must NOT read as a
    position, because gpsd emits it happily with lat/lon still present."""
    return (b'{"class":"TPV","mode":%d,"lat":%s,"lon":%s}\n'
            % (mode, str(lat).encode(), str(lon).encode()))


@pytest.mark.contract
def test_fixed_position_reaches_the_generated_config(tmp_path):
    svc = _svc(tmp_path)
    assert svc.set_gps(source="fixed", fixed_lat=LAT, fixed_lon=LON).ok
    pos, note = svc.meshcore_position("meshcore")
    # Normalised to a TOML-safe decimal, not the saved string (see the leading-zero case).
    assert note == ""
    assert (float(pos["lat"]), float(pos["lon"])) == (float(LAT), float(LON))
    svc.write_config_files("meshcore", position=pos)
    import tomllib
    doc = tomllib.loads(_generated(tmp_path))
    assert doc["device"]["companion"]["lat"] == pytest.approx(float(LAT))
    assert doc["device"]["companion"]["lon"] == pytest.approx(float(LON))


@pytest.mark.contract
def test_use_gps_off_omits_coordinates_and_clears_a_stale_pair(tmp_path):
    stale = TEMPLATE.replace('name = "NOCALL"', 'name = "NOCALL"\nlat = 40.3\nlon = -3.7')
    svc = _svc(tmp_path, stale)
    assert svc.set_gps(source="fixed", fixed_lat=LAT, fixed_lon=LON).ok
    svc.save_stack_config("meshcore", {"use_gps": "off"})
    pos, note = svc.meshcore_position("meshcore")
    assert (pos, note) == ({}, "")
    svc.write_config_files("meshcore", position=pos)
    assert _coords(_generated(tmp_path)) == []


@pytest.mark.safety("meshcore-position")
def test_saved_and_override_coordinates_are_ignored(tmp_path):
    # Controller-owned: only the resolved global plan may put a position on the air.
    svc = _svc(tmp_path)
    assert svc.set_gps(source="off").ok
    svc.save_stack_config("meshcore", {"file_lat": "99.9", "file_lon": "88.8"})
    pos, _ = svc.meshcore_position("meshcore")
    svc.write_config_files("meshcore", position=pos,
                           overrides={"lat": "12.3", "lon": "45.6"})
    assert _coords(_generated(tmp_path)) == []


@pytest.mark.contract
def test_a_live_gpsd_source_is_served_by_the_bridge_not_a_snapshot(tmp_path, fake_gpsd):
    """A live source feeds the node continuously, so its position follows the box. Writing
    a snapshot as well would leave a stale pair for it to revert to the moment the feed
    aged out — defeating the staleness clearing on both sides."""
    srv = fake_gpsd(json_lines=[_tpv()])
    svc = _svc(tmp_path)
    assert svc.set_gps(source="gpsd", host="127.0.0.1", port=srv.port).ok
    assert svc.meshcore_position("meshcore") == ({}, "")


@pytest.mark.contract
def test_a_live_source_runs_the_meshcore_gps_bridge(tmp_path, fake_gpsd):
    srv = fake_gpsd(json_lines=[_tpv()])
    svc = _svc(tmp_path)
    assert svc.set_gps(source="gpsd", host="127.0.0.1", port=srv.port).ok
    order = [c.id for _s, c in (svc._run_order("meshcore") or ())]
    assert "meshcore-gps" in order


@pytest.mark.contract
def test_a_fixed_source_runs_no_bridge(tmp_path):
    """Static coordinates need no feed to keep alive."""
    svc = _svc(tmp_path)
    assert svc.set_gps(source="fixed", fixed_lat=LAT, fixed_lon=LON).ok
    order = [c.id for _s, c in (svc._run_order("meshcore") or ())]
    assert "meshcore-gps" not in order


@pytest.mark.contract
def test_use_gps_off_runs_no_bridge_and_writes_no_device(tmp_path, fake_gpsd):
    srv = fake_gpsd(json_lines=[_tpv()])
    svc = _svc(tmp_path)
    assert svc.set_gps(source="gpsd", host="127.0.0.1", port=srv.port).ok
    svc.save_stack_config("meshcore", {"use_gps": "off"})
    order = [c.id for _s, c in (svc._run_order("meshcore") or ())]
    assert "meshcore-gps" not in order
    svc.write_config_files("meshcore", position={})
    assert "gps.device" not in _generated(tmp_path)


@pytest.mark.contract
def test_a_live_source_writes_the_bridge_pty_as_the_device(tmp_path, fake_gpsd):
    """The node reads NMEA from a device path; it must be the bridge's PTY, never the real
    receiver — two readers on one receiver is what the bridge exists to prevent."""
    srv = fake_gpsd(json_lines=[_tpv()])
    svc = _svc(tmp_path)
    assert svc.set_gps(source="gpsd", host="127.0.0.1", port=srv.port).ok
    svc.write_config_files("meshcore", position={})
    text = _generated(tmp_path)
    assert "gps.device" in text
    assert "state/gps/meshcore" in text


@pytest.mark.parametrize("lat,lon", [("007.6", "51.4779"), ("51.4779", "007.6"),
                                     ("5.", "0.5"), (".5", "-.25"), ("051.4779", "0")])
def test_a_valid_fixed_position_never_produces_invalid_toml(tmp_path, lat, lon):
    # REVIEW-FOUND: `[gps]` validates coordinates with float(), which accepts forms TOML
    # rejects — a European `fixed_lon = "007.6"` is a perfectly good position and an invalid
    # TOML number. Written through verbatim it failed config generation with an opaque
    # "generated TOML is invalid" and blocked every MeshCore start.
    svc = _svc(tmp_path)
    if not svc.set_gps(source="fixed", fixed_lat=lat, fixed_lon=lon).ok:
        pytest.skip("not accepted as a fixed position")
    pos, note = svc.meshcore_position("meshcore")
    assert pos, note
    writes = svc.write_config_files("meshcore", position=pos)
    assert [w.status for w in writes] == ["written"], [w.detail for w in writes]
    import tomllib
    doc = tomllib.loads(_generated(tmp_path))
    assert doc["device"]["companion"]["lat"] == pytest.approx(float(lat))
    assert doc["device"]["companion"]["lon"] == pytest.approx(float(lon))


@pytest.mark.safety("meshcore-identity")
def test_an_unreadable_candidate_blocks_rather_than_minting_over_it(tmp_path):
    # REVIEW-FOUND: treating an unreadable candidate (symlinked leaf, swapped parent) as
    # "no key" falls through to minting — the exact silent rotation this module prevents.
    svc = _svc(tmp_path)
    gen = tmp_path / "config" / "files" / "meshcore-pi.toml"
    gen.parent.mkdir(parents=True, exist_ok=True)
    real = tmp_path / "elsewhere.toml"
    real.write_text(f'[device.companion]\nprivatekey = "{KEY}"\n')
    gen.symlink_to(real)                                  # read_bytes is O_NOFOLLOW
    with pytest.raises(mi.MeshCoreIdentityError):
        mi.ensure_identity(svc._paths, svc.meshcore_identity_candidates())
    assert not mi.secret_path(svc._paths).exists()


@pytest.mark.safety("meshcore-identity")
def test_the_published_key_file_is_never_visible_half_written(tmp_path):
    # REVIEW-FOUND: creating the target first and writing into it afterwards leaves a window
    # where the racing loser reads an EMPTY file and reports a corrupt identity.
    from lhpc.core import runtime_fs
    p = tmp_path / "config" / "secrets" / "probe.key"
    runtime_fs.create_exclusive_bytes(svc_paths := Paths(runtime_root=tmp_path), p, b"x" * 64)
    assert p.read_bytes() == b"x" * 64
    with pytest.raises(FileExistsError):
        runtime_fs.create_exclusive_bytes(svc_paths, p, b"y" * 64)
    assert p.read_bytes() == b"x" * 64, "the loser must never overwrite the winner"
    assert not [q for q in p.parent.iterdir() if q.name.startswith(".")], "no temp left behind"


@pytest.mark.safety("meshcore-position")
def test_other_stacks_are_untouched(tmp_path, fake_gpsd):
    srv = fake_gpsd(silent=True)
    svc = _svc(tmp_path)
    assert svc.set_gps(source="gpsd", host="127.0.0.1", port=srv.port).ok
    assert svc.meshcore_position("meshtastic") == ({}, "")


@pytest.mark.safety("meshcore-position")
def test_a_dry_run_start_does_no_gps_io(tmp_path, fake_gpsd):
    srv = fake_gpsd(json_lines=[_tpv()])
    svc = _svc(tmp_path)
    assert svc.set_gps(source="gpsd", host="127.0.0.1", port=srv.port).ok
    svc.start("meshcore", apply=False)
    assert srv.connections == 0


# --------------------------------------------------------------------------- optional


def _snapshot(svc, states: dict):
    mc = next(s for s in svc.stacks() if s.id == "meshcore")
    snap = Snapshot(runtime_root_exists=True)
    ss = StackStatus(stack=mc)
    for comp in mc.components:
        ss.components[comp.id] = ComponentStatus(
            comp.id, run_state=states.get(comp.id, RunState.NOT_APPLICABLE))
    snap.stacks.append(ss)
    return snap, ss


@pytest.mark.contract
def test_an_absent_optional_component_does_not_sink_the_stack_badge(tmp_path):
    # Live find: the whole stack rolled up "not-installed" although meshcore-pi was
    # installed and merely stopped — only the never-cloned optional GUI was missing.
    svc = _svc(tmp_path)
    snap, ss = _snapshot(svc, {"meshcore-pi": RunState.STOPPED,
                               "meshcore-nodegui": RunState.NOT_INSTALLED,
                               "meshcore-cli": RunState.STOPPED})
    assert rollup_states(snap)["meshcore"] == "stopped"
    assert ss.components["meshcore-nodegui"].run_state is RunState.NOT_INSTALLED, \
        "the component itself must stay truthful"


@pytest.mark.contract
def test_a_missing_mandatory_component_still_sinks_the_badge(tmp_path):
    svc = _svc(tmp_path)
    snap, _ = _snapshot(svc, {"meshcore-pi": RunState.NOT_INSTALLED,
                              "meshcore-nodegui": RunState.NOT_INSTALLED,
                              "meshcore-cli": RunState.STOPPED})
    assert rollup_states(snap)["meshcore"] == "not-installed"


@pytest.mark.safety("optional-visibility")
@pytest.mark.parametrize("state", [RunState.FAILED, RunState.DEGRADED])
def test_an_installed_optional_component_is_never_hidden(tmp_path, state):
    # Only NEVER-INSTALLED optionals are excused. A broken one that IS installed is news.
    svc = _svc(tmp_path)
    snap, _ = _snapshot(svc, {"meshcore-pi": RunState.RUNNING,
                              "meshcore-nodegui": state,
                              "meshcore-cli": RunState.STOPPED})
    assert rollup_states(snap)["meshcore"] == state.value


@pytest.mark.contract
def test_building_a_stack_skips_an_absent_optional_component(tmp_path):
    # Before: Popen got a cwd that does not exist -> rc 127, "Build FAILED".
    svc = _svc(tmp_path)
    res = svc.build("meshcore", apply=True)
    assert "meshcore-nodegui" not in (res.summary or "") or res.ok


@pytest.mark.contract
def test_building_an_absent_component_by_name_is_refused_as_not_installed(tmp_path):
    svc = _svc(tmp_path)
    res = svc.build("meshcore-nodegui", apply=True)
    assert not res.ok
    assert "not installed" in res.summary


@pytest.mark.contract
def test_source_check_does_not_fail_a_stack_for_an_absent_optional(tmp_path):
    svc = _svc(tmp_path)
    res = svc.source_check("meshcore")
    assert "meshcore-nodegui" in " ".join(res.details or ()), \
        "the skipped component stays visible in the result"
    assert "meshcore-nodegui" in (res.data or {}).get("excused", [])


# --------------------------------------------------------------------------- daemon


@pytest.mark.contract
def test_meshcore_daemon_defaults_match_what_the_pin_applies():
    # The pinned presets all apply POWER=14 / PREAMBLE=16; declaring 20/8 made the daemon
    # start on values the app immediately overwrote.
    from lhpc.core import daemon_params
    assert daemon_params.default_value("meshcore", "868", "POWER") == "14"
    assert daemon_params.default_value("meshcore", "868", "PREAMBLE") == "16"


@pytest.mark.contract
def test_a_linked_source_does_not_block_maintenance_on_the_base_template(tmp_path, monkeypatch):
    """AUDIT-FOUND: adopting meshcore-pi BY LINK made install/update/uninstall/clean refuse.

    `_resolve_config_dest` only applies its linked-readonly guard when `not for_base`, so the
    BASE template still resolved to a path inside the symlinked checkout. Every runtime read
    is descriptor-anchored (O_NOFOLLOW per component), so reading it raised
    PathContainmentError -> MeshCoreIdentityError -> the identity guard refused the operation.
    Uninstall and clean were refused too, so the operator could not even back out.

    The generated config stays scannable; only the linked base is skipped.
    """
    from lhpc.core.lifecycle import Lifecycle
    svc = _svc(tmp_path)
    monkeypatch.setattr(Lifecycle, "is_linked_source", lambda self, c: True)

    cands = svc.meshcore_identity_candidates()
    # No candidate may point into the linked source tree...
    assert not [p for p in cands if "src" in p.parts], cands
    # ...but the generated {runtime} config stays scannable, so a normal upgrade still adopts.
    assert [p.name for p in cands] == ["meshcore-pi.toml"]
    # With nothing generated yet, adoption is a clean "no key here", not a refusal.
    assert mi.adopt_identity(svc._paths, cands) == ""
