"""Per-stack daemon-parameter catalogue: source-derived defaults, app-owned (greyed) vs
operator-editable split, override application. Pure logic — no I/O."""

from lhpc.core import daemon_params as dp
from lhpc.core import daemon_control


def test_client_stacks_and_direct_spi_excluded():
    for s in ("chat", "igate", "kiss", "voice", "meshcom", "meshcore"):
        assert dp.is_client(s)
    assert not dp.is_client("meshtastic")            # direct-SPI: no daemon panel
    assert not dp.is_client("daemon")


def test_loraham_defaults_match_app_source():
    # chat/igate/kiss set SF=12 BW=125 CR=5 CRC=1 PREAMBLE=8 SYNC=0x12 POWER=17 (from source).
    for s in ("chat", "igate", "kiss"):
        assert dp.default_value(s, "433", "SF") == "12"
        assert dp.default_value(s, "433", "BW") == "125.0"
        assert dp.default_value(s, "433", "SYNC") == "0x12"
        assert dp.default_value(s, "433", "POWER") == "17"
        assert dp.default_value(s, "433", "TXMODE") == "MANAGED"


def test_voice_per_band_defaults():
    assert dp.default_value("voice", "433", "SF") == "7"
    assert dp.default_value("voice", "868", "SF") == "11"
    assert dp.default_value("voice", "868", "SYNC") == "0x2B"
    assert dp.default_value("voice", "433", "TXMODE") == "DIRECT"


def test_meshcom_and_meshcore_868_defaults():
    assert dp.default_value("meshcom", "433", "SF") == "10"        # DACH 433
    assert dp.default_value("meshcom", "868", "SF") == "11"        # 868
    assert dp.default_value("meshcom", "433", "BW") == "125.0"
    assert dp.default_value("meshcom", "433", "CADIDLE") == "28"   # shared, the CADIDLE=28 fix
    assert dp.default_value("meshcom", "868", "CADIDLE") == "28"
    assert dp.default_value("meshcore", "868", "SF") == "8"
    assert dp.default_value("meshcore", "868", "BW") == "62.5"
    assert dp.default_value("meshcore", "868", "CR") == "8"


def test_all_default_values_are_daemon_valid():
    # Every displayed default must pass the daemon's own validator (no mismatch/typo).
    for s in dp.CLIENT_STACKS:
        for band in ("433", "868"):
            for name in dp.RADIO_PARAMS + dp.LBT_PARAMS:
                v = dp.default_value(s, band, name)
                if v == "":
                    continue
                assert daemon_control.validate_set(name, v) is None, f"{s}/{band}/{name}={v}"


def test_radio_params_greyed_lbt_not():
    # All params are editable now; app_owned is only the greyed (will-be-overwritten) hint.
    rows = {r["name"]: r for r in dp.stack_view("meshcom", "868")}
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
    view = {r["name"]: r for r in dp.stack_view("meshcom", "868", ov)}
    assert view["CADIDLE"]["value"] == "40"          # LBT override shows
    assert view["SF"]["value"] == "9"                # radio override also shows (still greyed)
    assert view["SF"]["app_owned"]
