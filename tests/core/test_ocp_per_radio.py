"""OCP per radio after L5 (T-CO9, T-CO10; plan A §2.7, the OCP bullet; B4-TABLES-v2 rows M8, M9; B4-D3a).

The value lives on the radio's row (`ocp_ma`, B4-D3a). LHPC's value is 100, or 140 with high power (W68);
an explicit legal value wins; the value is checked against the radio's chip family; no band value `ocp_<band>` is
carried (K2, CLEANUP-DESIGN-v13 13.8). New feature, no old path: the module is imported per test.
"""
from __future__ import annotations

import dataclasses

import pytest

from lhpc.core import radio_models

MODELS = radio_models.load()
DEFAULTS = {"txmode": "managed", "cadmon": "off", "cadrssi": "-90", "hipower": "off", "ocp": "auto"}


def _radios():
    from lhpc.core import radios
    return radios


def _r(rid="r1", model="loraham", band="433", **kw):
    return _radios().Radio(id=rid, name=f"Radio {rid}", model=model, band=band, **kw)


def _settings(**kw):
    base = {"txmode": "managed", "cadmon": "off", "cadrssi": -90, "hipower": False, "ocp": "auto", "dp": {}}
    base.update(kw)
    cls = _radios().RadioSettings
    names = {f.name for f in dataclasses.fields(cls)}
    if "notes" in names:                                  # B4-D5: RadioSettings also carries notes
        base.setdefault("notes", ())
    return cls(**base)


def _resolve(radio, model):
    return _radios().radio_settings(radio, MODELS[model], {}, DEFAULTS)


@pytest.mark.parametrize("hipower,ocp,want", [(False, "auto", 100), (True, "auto", 140), (True, "100", 100),
                                              (True, "120", 120), (False, "45", 45), (False, "0", 0)])
def test_t_co9_effective_ocp(hipower, ocp, want):
    assert _radios().effective_ocp(_settings(hipower=hipower, ocp=ocp)) == want


@pytest.mark.parametrize("ocp", [" 130", "+130", "1_30", "0130", "²", "1" * 5000, "", "13.0", "-5", "auto "])
def test_effective_ocp_refuses_a_non_canonical_value(ocp):
    with pytest.raises(ValueError):
        _radios().effective_ocp(_settings(ocp=ocp))


@pytest.mark.parametrize("model,band,ocp,ok", [
    ("loraham", "433", "0", True), ("loraham", "433", "45", True), ("loraham", "433", "120", True),
    ("loraham", "433", "130", True), ("loraham", "433", "240", True),
    ("loraham", "433", "125", False), ("loraham", "433", "40", False), ("loraham", "433", "250", False),
    ("waveshare-sx1262", "868", "5", True), ("waveshare-sx1262", "868", "140", True),
    ("waveshare-sx1262", "868", "0", False), ("waveshare-sx1262", "868", "150", False),
    ("waveshare-sx1262", "868", "7", False), ("meshstick-1262", "868", "100", True),
])
def test_t_co10_ocp_on_the_row_is_checked_against_the_chip(model, band, ocp, ok):
    rad = _radios()
    kw = {"usb_port": "1-1.3"} if model == "meshstick-1262" else {}
    reasons = rad.check([_r("r1", model=model, band=band, ocp_ma=ocp, **kw)], MODELS)
    assert (reasons == []) == ok, (model, ocp, reasons)


@pytest.mark.parametrize("ocp", ["1000", "0100", " 90", "90 ", "9e1", "²"])
def test_m9_more_than_three_digits_or_a_non_canonical_value_is_refused_at_save(ocp):
    """W95: more than three digits is refused before any int()."""
    assert _radios().check([_r("r1", ocp_ma=ocp)], MODELS)


def test_ocp_ma_as_a_toml_integer_is_accepted_and_resolved():
    s = _resolve(_r("r1", ocp_ma=140, high_power=True), "loraham")
    assert s.ocp == "140" and _radios().effective_ocp(s) == 140


def test_ocp_ma_as_a_bool_is_refused():
    assert _radios().check([_r("r1", ocp_ma=True)], MODELS)


def test_k2_an_unset_ocp_is_auto_whatever_a_band_value_said():
    """K2: no `ocp_<band>` is carried (13.8): an unset row is auto, 140 with the grant on its band, else 100."""
    rad = _radios()
    assert rad.effective_ocp(_resolve(_r("r1", band="433"), "loraham")) == 100
    assert rad.effective_ocp(_resolve(_r("r1", band="433", ocp_ma="90"), "loraham")) == 90
    assert rad.effective_ocp(_resolve(_r("r1", band="433", high_power=True, high_power_band="433"), "loraham")) == 140
