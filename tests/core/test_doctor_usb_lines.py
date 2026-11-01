"""The doctor's USB and host lines (B5-WIRING-TABLES-v1 Table D; INTERFACE-B5-W1 `doctor_usb_lines`,
`radio_presence`, `detect_allowed`, the `UsbProbes` record).

The doctor only shows. Line texts are fixed templates the code role quotes from a rendered output, so these tests
assert which lines appear and what they name, never a whole text, except the two the contract fixes:
"unknown transport on <rid>" and "unknown (<error>)". New modules: each test imports them itself.
"""
from __future__ import annotations

import pytest

from lhpc.core import radio_models

MODELS = radio_models.load()
STICK = "meshstick-1262"


def _ua():
    from lhpc.core import usb_access
    return usb_access


def _uf():
    from lhpc.core import usb_flow
    return usb_flow


def _r(rid="r1", model=STICK, band="868", **kw):
    from lhpc.core import radios
    return radios.Radio(id=rid, name=f"Radio {rid}", model=model, band=band, **kw)


def _field(value):
    ua = _ua()
    if value is None:
        return ua.Field(ua.Probe.ABSENT)
    if value is ua.Probe.UNKNOWN:
        return ua.Field(ua.Probe.UNKNOWN)
    return ua.Field(ua.Probe.PRESENT, value)


def _dev(port, *, vid="1a86", pid="5512", serial=None):
    ua = _ua()
    return ua.UsbDevice(port, _field(vid), _field(pid), _field("1"), _field(port.split("-", 1)[1]),
                        _field("5"), _field(None), _field(serial))


def _scan(*devices, listing=None):
    ua = _ua()
    return ua.Scan(listing or ua.Probe.PRESENT, tuple(devices))


def _probes(**over):
    ua, uf = _ua(), _uf()
    base = {
        "scan": lambda: _scan(_dev("1-1.3", serial="ABCDEFGH12")),
        "rule_state": lambda: ua.RuleState.CURRENT,
        "ch341_module": lambda: ua.Probe.ABSENT,
        "ch341_binding": lambda port: ua.Binding(ua.Probe.ABSENT),
        "plugdev": lambda: ua.Membership.MEMBER,
        "libusb": lambda: ua.Library(ua.Probe.PRESENT, "libusb-1.0.so.0"),
        "node_access": lambda device: ua.Probe.PRESENT,
        "hotplug_for": lambda rid: ua.Probe.PRESENT,
    }
    base.update(over)
    return uf.UsbProbes(**base)


def _lines(rows, **over):
    return _uf().doctor_usb_lines(rows, MODELS, _probes(**over))


# ---- D-1: which lines apply ---------------------------------------------------------------------

def test_d1_an_spi_only_list_has_no_usb_host_lines_and_asks_no_usb_probe():
    def never(*a):
        raise AssertionError("no USB probe for an SPI-only list")
    lines = _lines([_r("r1", model="loraham", band="433")], scan=never, rule_state=never, ch341_module=never,
                   plugdev=never, libusb=never, node_access=never, hotplug_for=never)
    usb = _lines([_r("r1", usb_serial="ABCDEFGH")])
    assert len(usb) > len(lines)
    assert not [x for x in lines if "unknown (" in x]


def test_d1_a_disabled_usb_row_still_counts_for_the_host_lines():
    assert len(_lines([_r("r1", usb_serial="ABCDEFGH", enabled=False)])) == len(_lines([_r("r1", usb_serial="ABCDEFGH")]))


def test_d1_a_row_of_an_unknown_transport_names_its_rid():
    lines = _lines([_r("r7", model="no-such-model")])
    assert "unknown transport on r7" in lines


# ---- D-2, D-4, D-6: states shown, the copybox offered ------------------------------------------------

@pytest.mark.parametrize("state", ["MISSING", "STALE"])
def test_d2_a_missing_or_stale_rule_offers_the_copybox(state):
    ua = _ua()
    lines = _lines([_r("r1", usb_serial="ABCDEFGH")], rule_state=lambda: getattr(ua.RuleState, state))
    current = _lines([_r("r1", usb_serial="ABCDEFGH")])
    assert any("copybox" in x for x in lines)
    assert not any("copybox" in x for x in current)


def test_d4_each_plugdev_state_gives_a_different_line():
    ua = _ua()
    seen = set()
    for m in ua.Membership:
        lines = _lines([_r("r1", usb_serial="ABCDEFGH")], plugdev=lambda m=m: m)
        seen.add(tuple(lines))
    assert len(seen) == len(list(ua.Membership))


def test_d3_module_and_binding_states_give_different_lines():
    ua = _ua()
    seen = set()
    for module in ua.Probe:
        lines = _lines([_r("r1", usb_port="1-1.3")], scan=lambda: _scan(_dev("1-1.3")),
                       ch341_module=lambda module=module: module)
        seen.add(tuple(lines))
    assert len(seen) == 3


# ---- D-9: a raising probe ------------------------------------------------------------------------

@pytest.mark.parametrize("name", ["scan", "rule_state", "ch341_module", "plugdev", "libusb", "hotplug_for"])
def test_d9_a_probe_that_raises_reads_unknown_with_the_error_and_the_rest_still_print(name):
    def boom(*a):
        raise RuntimeError("probe broke")
    rows = [_r("r1", usb_serial="ABCDEFGH")]
    good = _lines(rows)
    bad = _lines(rows, **{name: boom})
    assert any("unknown (" in x and "probe broke" in x for x in bad)
    assert len(bad) >= len(good) - 1


# ---- D-8: radio_presence -----------------------------------------------------------------------

def _presence(radio, found):
    return _uf().radio_presence(radio, MODELS[radio.model], found)


def test_d8_a_serialless_row_follows_the_stick_on_its_port():
    P = _ua().Probe
    assert _presence(_r("r1", usb_port="1-1.3"), _scan(_dev("1-1.3"))).state is P.PRESENT
    assert _presence(_r("r1", usb_port="1-1.3"), _scan(_dev("1-1.2"))).state is P.ABSENT


def test_d8_a_serial_row_is_present_on_the_one_port_whose_serial_has_its_prefix():
    P = _ua().Probe
    p = _presence(_r("r1", usb_serial="ABCDEFGH"), _scan(_dev("1-1.2", serial="XYZ12345"),
                                                         _dev("1-1.4", serial="ABCDEFGH99")))
    assert p.state is P.PRESENT and p.port == "1-1.4"


def test_d8_two_matching_serials_are_ambiguous_unknown():
    P = _ua().Probe
    p = _presence(_r("r1", usb_serial="ABCDEFGH"), _scan(_dev("1-1.2", serial="ABCDEFGH1"),
                                                         _dev("1-1.4", serial="ABCDEFGH2")))
    assert p.state is P.UNKNOWN and set(p.ports) == {"1-1.2", "1-1.4"} and p.note


def test_d8_no_prefix_match_while_a_device_is_unreadable_is_unknown_not_absent():
    ua = _ua()
    p = _presence(_r("r1", usb_serial="ABCDEFGH"), _scan(_dev("1-1.2", serial="XYZ12345"),
                                                         _dev("1-1.4", vid=ua.Probe.UNKNOWN)))
    assert p.state is ua.Probe.UNKNOWN


def test_d8_an_unknown_listing_is_unknown_and_absent_needs_every_device_judged():
    ua = _ua()
    assert _presence(_r("r1", usb_serial="ABCDEFGH"), _scan(listing=ua.Probe.UNKNOWN)).state is ua.Probe.UNKNOWN
    assert _presence(_r("r1", usb_serial="ABCDEFGH"), _scan(_dev("1-1.2", serial="XYZ12345"))).state is ua.Probe.ABSENT


def test_d8_a_found_device_with_an_unreadable_neighbour_notes_it():
    ua = _ua()
    p = _presence(_r("r1", usb_serial="ABCDEFGH"), _scan(_dev("1-1.4", serial="ABCDEFGH99"),
                                                         _dev("1-1.5", vid=ua.Probe.UNKNOWN)))
    assert p.state is ua.Probe.PRESENT and p.note


# ---- D-6: detect_allowed ------------------------------------------------------------------------

def test_d6_detect_is_allowed_only_with_libusb_present():
    ua, uf = _ua(), _uf()
    assert uf.detect_allowed(ua.Library(ua.Probe.PRESENT, "libusb-1.0.so.0")).outcome == "proceed"
    for state in (ua.Probe.ABSENT, ua.Probe.UNKNOWN):
        v = uf.detect_allowed(ua.Library(state, "cannot load libusb-1.0.so.0"))
        assert v.outcome == "refuse" and "cannot load libusb-1.0.so.0" in v.reason


# ---- the line templates (INTERFACE-B5-W1-NOTE-1 §3, §4) ------------------------------------------------

def _template_regexes():
    import re
    import string
    out = []
    for template in _uf().DOCTOR_TEXTS.values():
        parts = []
        for literal, field, _spec, _conv in string.Formatter().parse(template):
            parts.append(re.escape(literal))
            if field is not None:
                parts.append(".+?")
        out.append(re.compile("".join(parts) + r"\Z", re.DOTALL))
    return out


def _all_states_lines():
    ua = _ua()
    rows = [_r("r1", usb_serial="ABCDEFGH"), _r("r2", usb_port="1-1.3"), _r("r3", model="loraham", band="433"),
            _r("r4", model="no-such-model")]
    out = []
    for rule in ua.RuleState:
        for m in ua.Membership:
            out += _lines(rows, rule_state=lambda rule=rule: rule, plugdev=lambda m=m: m,
                          scan=lambda: _scan(_dev("1-1.3"), _dev("1-1.4", serial="ABCDEFGH1")))
    for probe in ua.Probe:
        out += _lines(rows, ch341_module=lambda probe=probe: probe, hotplug_for=lambda rid, probe=probe: probe,
                      node_access=lambda device, probe=probe: probe,
                      libusb=lambda probe=probe: ua.Library(probe, "detail"))
    return out


def test_every_doctor_line_is_one_of_the_doctor_texts():
    """Every line the function prints renders a template of DOCTOR_TEXTS (no text outside the constants)."""
    regexes = _template_regexes()
    assert regexes
    for line in _all_states_lines():
        assert any(rx.match(line) for rx in regexes), line


def test_the_doctor_texts_are_format_strings_keyed_by_name():
    texts = _uf().DOCTOR_TEXTS
    assert isinstance(texts, dict) and texts
    assert all(type(k) is str and type(v) is str for k, v in texts.items())


def test_every_doctor_text_carries_words_of_its_own():
    """A template that is a bare field would match any line: the conformance check above needs text in each."""
    import string
    for name, template in _uf().DOCTOR_TEXTS.items():
        literal = "".join(lit for lit, _f, _s, _c in string.Formatter().parse(template))
        assert sum(ch.isalpha() for ch in literal) >= 3, name


def test_d1_a_transport_outside_the_librarys_list_is_an_unknown_transport():
    import dataclasses
    models = dict(MODELS)
    models["odd-bus"] = dataclasses.replace(MODELS["loraham"], id="odd-bus", transport="i2c")
    lines = _uf().doctor_usb_lines([_r("r5", model="odd-bus", band="433")], models, _probes())
    assert "unknown transport on r5" in lines
