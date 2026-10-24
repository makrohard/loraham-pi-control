"""The radio model table (lhpc/data/radio_models.toml) and its loader, held to the daemon's presets: the
fixture tests/data/daemon-hardware_profile.cpp is the daemon's hardware_profile.cpp at the manifest pin,
its commit in the header line, refreshed with every daemon pin move."""
from __future__ import annotations

import re
import tomllib

import pytest
import repo_paths

from lhpc.core import radio_models

FIXTURE = repo_paths.REPO / "tests" / "data" / "daemon-hardware_profile.cpp"

_FAKE = """
[[model]]
id = "fake-stick"
label = "A stick no code knows"
short = "fake"
transport = "usb-ch341"
chip = "sx1262"
bands = ["868"]
power_max_dbm = 20
high_power_switch = false
usb = { vid = "1a86", pid = "5512", product = "Fake" }
  [[model.variant]]
  key = "default"
  preset = "fake-1262"
  pins = {}
"""


def test_the_shipped_table_loads():
    models = radio_models.load()
    assert set(models) == {"loraham", "uputronics", "waveshare-sx1262"}
    assert [v.band for v in models["loraham"].variants] == ["433", "868"]
    assert models["uputronics"].variant("ce1").preset == "uputronics-ce1"


def test_a_new_model_is_one_row_and_no_code(tmp_path):
    # T-M2 (I12): a model within the supported chips and transports needs no code change.
    path = tmp_path / "models.toml"
    path.write_text(_FAKE)
    stick = radio_models.load(path)["fake-stick"]
    assert (stick.transport, stick.chip, stick.bands) == ("usb-ch341", "sx1262", ("868",))
    assert stick.usb == {"vid": "1a86", "pid": "5512", "product": "Fake"}
    assert stick.variant("default").pins == {}


@pytest.mark.parametrize("edit, reason", [
    (('short = "fake"', 'short = "toolong8"'), "short"),
    (('transport = "usb-ch341"', 'transport = "i2c"'), "transport"),
    (('chip = "sx1262"', 'chip = "sx1280"'), "chip"),
    (('bands = ["868"]', 'bands = ["915"]'), "band"),
    (('usb = { vid = "1a86", pid = "5512", product = "Fake" }\n', ""), "usb"),
    (("pins = {}", "pins = { cs = 41 }"), "pin"),
    (("pins = {}", "pins = { mosi = 10 }"), "pin"),
    (('high_power_switch = false', 'high_power_switch = false\ncolour = "red"'), "colour"),
])
def test_a_row_outside_the_schema_is_refused(tmp_path, edit, reason):
    path = tmp_path / "models.toml"
    path.write_text(_FAKE.replace(*edit))
    with pytest.raises(radio_models.ModelError, match=reason):
        radio_models.load(path)


def test_a_duplicate_id_is_refused(tmp_path):
    path = tmp_path / "models.toml"
    path.write_text(_FAKE + _FAKE)
    with pytest.raises(radio_models.ModelError, match="duplicate"):
        radio_models.load(path)


def _daemon_profiles() -> dict:
    """{preset: {band: {field: pin or None, "chip": family}}}: `profile_fill`'s lines per preset, walking its
    `if (band == 433)` and `if (ce0)` branches for each band (`DAEMON_HW_PIN_NC` = no line)."""
    body = re.search(r"static bool profile_fill\(.*?\n\{(.*?)\n\}", FIXTURE.read_text(), re.DOTALL).group(1)
    out = {}
    for block in re.split(r"\n    if \(strcmp\(preset, ", body)[1:]:
        head, rest = block.split("{", 1)
        for preset in re.findall(r'"([\w-]+)"\) == 0', head):
            out[preset] = {}
            for band in radio_models.BANDS:
                conds, taken, got = {"band == 433": band == "433", "ce0": preset.endswith("0")}, [], {}
                for s in (line.strip() for line in rest.splitlines()):
                    if m := re.fullmatch(r"if \((.+)\) \{", s):
                        taken.append(conds[m.group(1)])          # an unknown branch fails here, loudly
                    elif s == "} else {":
                        taken[-1] = not taken[-1]
                    elif s == "}" and taken:
                        taken.pop()
                    elif all(taken):
                        got.update((f, None if v == "DAEMON_HW_PIN_NC" else int(v))
                                   for f, v in re.findall(r"p->(\w+) = (\d+|DAEMON_HW_PIN_NC);", s))
                        if fam := re.search(r"DAEMON_CHIP_FAMILY_(\w+);", s):
                            got["chip"] = fam.group(1).lower()
                out[preset][band] = got
    return out


def test_the_fixture_is_the_daemon_at_the_manifest_pin():
    # "refreshed at each pin bump": a pin move without a new fixture fails here.
    pin = re.search(r" at ([0-9a-f]{40}) ", FIXTURE.read_text().splitlines()[0]).group(1)
    manifest = tomllib.loads((repo_paths.REPO / "lhpc" / "data" / "manifest.example.toml").read_text())
    daemon = next(c for st in manifest["stack"] for c in st.get("component", []) if c["id"] == "loraham-daemon")
    assert daemon["source"]["pin_commit"] == pin


def test_every_variant_claims_the_daemons_pins_and_chip():
    # T-M1: each variant's pins, in each band it serves, and its model's chip equal the daemon's preset.
    daemon = _daemon_profiles()
    for model in radio_models.load().values():
        for v in model.variants:
            for band in ([v.band] if v.band else model.bands):
                prof = daemon[v.preset][band]
                assert {f: v.pins.get(f) for f in radio_models.PIN_FIELDS} == \
                    {f: prof.get(f) for f in radio_models.PIN_FIELDS}, (model.id, v.key, band)
                assert prof["chip"] == model.chip, (model.id, v.key)


def test_the_rows_name_exactly_the_daemons_presets():
    # T-M3: every preset the daemon knows at the pin has a row, and no row names one it does not know.
    known = re.search(r'daemon_hardware_profile_known\(void\)\s*\{\s*return "([^"]*)";', FIXTURE.read_text())
    named = {v.preset for m in radio_models.load().values() for v in m.variants}
    assert named == set(known.group(1).split(", ")) == set(_daemon_profiles())
