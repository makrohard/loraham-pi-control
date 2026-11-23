"""Per-stack daemon-parameter catalogue: source-derived defaults, app-owned (greyed) vs
operator-editable split, override application. Pure logic — no I/O."""

from lhpc.core import daemon_params as dp
from lhpc.core import daemon_control


def test_client_stacks_and_direct_spi_excluded():
    for s in ("chat", "kiss", "voice", "meshcom", "meshcore"):
        assert dp.is_client(s)
    assert not dp.is_client("meshtastic")            # direct-SPI: no daemon panel
    assert not dp.is_client("daemon")


def test_loraham_defaults_match_app_source():
    # chat/kiss set SF=12 BW=125 CR=5 CRC=1 PREAMBLE=8 SYNC=0x12 POWER=17 (from source).
    for s in ("chat", "kiss"):
        assert dp.default_value(s, "433", "SF") == "12"
        assert dp.default_value(s, "433", "BW") == "125.0"
        assert dp.default_value(s, "433", "SYNC") == "0x12"
        assert dp.default_value(s, "433", "POWER") == "17"
        assert dp.default_value(s, "433", "TXMODE") == "MANAGED"


def test_voice_433_defaults():
    assert dp.default_value("voice", "433", "SF") == "7"
    assert dp.default_value("voice", "433", "SYNC") == "0x12"
    assert dp.default_value("voice", "433", "TXMODE") == "DIRECT"


def test_t4_voice_and_meshcom_have_no_868_profile_meshcore_keeps_its_868():
    # Voice runs on 433 only and MeshCom's 868 profile was unused: neither has an "868" entry. MeshCore keeps it.
    assert set(dp.STACK_DEFAULTS["voice"]) == {"433"}
    assert set(dp.STACK_DEFAULTS["meshcom"]) == {"*", "433"}
    assert dp.STACK_DEFAULTS["meshcore"]["868"]["SF"] == "8"


def test_t8_a_stack_has_band_defaults_only_for_the_bands_it_runs_on():
    # A per-band profile for a band the manifest no longer offers is a stale default (shown in the docs table and on
    # a panel no radio can reach). Reticulum runs on 868 only: no 433 FREQ/POWER is left for it.
    from lhpc.core.instances import stack_bands
    from lhpc.core.manifest import load_manifest
    for st in load_manifest():
        bands = set(stack_bands(st))
        for band in set(dp.STACK_DEFAULTS.get(st.id, {})) - {"*"}:
            assert band in bands, f"{st.id}: a {band} profile, but it runs on {sorted(bands)}"
    assert set(dp.STACK_DEFAULTS["reticulum"]) == {"*", "868"}
    assert dp.default_value("reticulum", "433", "POWER") == ""
    assert dp.default_value("reticulum", "868", "POWER") == "14"


def test_meshcom_433_and_meshcore_868_defaults():
    assert dp.default_value("meshcom", "433", "SF") == "10"        # DACH 433
    assert dp.default_value("meshcom", "433", "BW") == "125.0"
    assert dp.default_value("meshcom", "433", "CADIDLE") == "28"   # shared, the CADIDLE=28 fix
    assert dp.default_value("meshcore", "868", "SF") == "8"
    assert dp.default_value("meshcore", "868", "BW") == "62.5"
    assert dp.default_value("meshcore", "868", "CR") == "8"


def test_all_default_values_are_daemon_valid():
    # Every displayed default must pass the daemon's own validator (no mismatch/typo).
    defaults = [(s, band, name, dp.default_value(s, band, name)) for s in dp.CLIENT_STACKS
                for band in ("433", "868") for name in dp.RADIO_PARAMS + dp.LBT_PARAMS]
    defaults = [d for d in defaults if d[3] != ""]
    assert defaults                                                   # something is checked
    for s, band, name, v in defaults:
        assert daemon_control.validate_set(name, v) is None, f"{s}/{band}/{name}={v}"


def test_radio_params_greyed_lbt_not():
    # All params are editable now; app_owned is only the greyed (will-be-overwritten) hint.
    rows = {r["name"]: r for r in dp.stack_view("meshcom", "433")}
    assert rows["SF"]["app_owned"]                       # greyed: app overwrites it
    assert rows["TXMODE"]["app_owned"]                   # app sets the mode
    assert not rows["CADIDLE"]["app_owned"]              # operator LBT, not overwritten
    assert not rows["CADWAIT"]["app_owned"]


def test_daemon_stack_has_no_greyed_rows_and_shows_base_freq():
    # The daemon has no app overwriting it -> nothing greyed; base FREQ shown.
    rows = {r["name"]: r for r in dp.stack_view("daemon", "433")}
    assert not any(r["app_owned"] for r in rows.values())
    assert rows["FREQ"]["value"] == "433.175"
    assert all("desc" in r and r["desc"] for r in rows.values())   # every field has help


def test_overrides_show_on_all_params():
    # Every param is editable and persists; app_owned only greys it visually.
    ov = {"CADIDLE": "40", "SF": "9"}
    view = {r["name"]: r for r in dp.stack_view("meshcom", "433", ov)}
    assert view["CADIDLE"]["value"] == "40"          # LBT override shows
    assert view["SF"]["value"] == "9"                # radio override also shows (still greyed)
    assert view["SF"]["app_owned"]


def test_the_voice_panel_defaults_are_the_apps_manifest_defaults():
    """For every parameter the voice app sets on its one band (433), the panel's displayed default is
    the value the app is seeded with: the manifest's `433_*` param default (the app's LDRO 2 = auto is
    the daemon's AUTO)."""
    from lhpc.core.manifest import load_manifest
    voice = next(s for s in load_manifest() if s.id == "voice")
    seeded = [("433", p.name.upper(), p.default) for c in voice.components if c.config_file
              for p in c.config_file.params if p.key.startswith("433_")]
    shown = [(band, name, {"2": "AUTO"}.get(value, value) if name == "LDRO" else value,
              dp.default_value("voice", band, name))
             for band, name, value in seeded if name in dp.STACK_DEFAULTS["voice"][band]]
    assert ("433", "SF") in {(band, name) for band, name, _v, _d in shown}
    assert [row for row in shown if row[2] != row[3]] == []

