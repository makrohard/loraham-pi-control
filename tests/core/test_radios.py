"""The hardware list `config/radios.toml` (plan A §2.2, stage L5; B4-TABLES-v2 rows D13, D13b, H1, H2, H6, R1).

Tests the radios module through its public functions only (INTERFACE-B4-v8 §1). A test whose name starts
with `test_q<N>_` carries a request of INTERFACE-B4-v8 §1b to the module: it states a row of the design
table or a decision that the first implementation did not yet hold.

A new feature with no old path: the module does not exist before L5, so each test imports it itself.
"""
from __future__ import annotations

import dataclasses
import os
import tomllib

import pytest

from lhpc.core import radio_models
from lhpc.core.paths import resolve_paths

MODELS = radio_models.load()


def _radios():
    from lhpc.core import radios
    return radios


def _paths(tmp_path):
    return resolve_paths({"LHPC_RUNTIME_ROOT": str(tmp_path)})


def _r(rid="r1", name=None, model="loraham", band="433", **kw):
    return _radios().Radio(id=rid, name=f"Radio {rid}" if name is None else name, model=model, band=band, **kw)


def _write(tmp_path, text):
    p = tmp_path / "config" / "radios.toml"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


def _reasons(radios_list):
    return _radios().check(list(radios_list), MODELS)


# ---- load: the file's state ------------------------------------------------------------------------

@pytest.mark.no_default_hardware                  # the list is really absent (conftest's baseline list off)
def test_a_missing_file_is_absent_and_not_startable(tmp_path):
    rad = _radios()
    st = rad.load(_paths(tmp_path), MODELS)
    assert st.kind == rad.ABSENT and st.radios == () and not st.startable


def test_a_valid_file_is_valid_and_startable(tmp_path):
    rad = _radios()
    _write(tmp_path, '[[radio]]\nid = "r1"\nname = "LoRaHAM 433"\nmodel = "loraham"\nband = "433"\n')
    st = rad.load(_paths(tmp_path), MODELS)
    assert st.kind == rad.VALID and st.startable
    assert st.radios == (_r("r1", "LoRaHAM 433"),)


@pytest.mark.parametrize("text", [
    "[[radio]\n",                                                     # not TOML
    'other = 1\n',                                                    # a key outside [[radio]]
    'radio = "x"\n',                                                  # radio is not an array of tables
    '[[radio]]\nid = "r1"\nname = "A"\nmodel = "loraham"\n',          # band missing
    '[[radio]]\nid = "r1"\nname = "A"\nmodel = "loraham"\nband = "433"\ncolour = "red"\n',   # unknown key
    '[[radio]]\nid = "r1"\nname = "A"\nmodel = "loraham"\nband = "999"\n',                   # band of no model
    '[[radio]]\nid = "r1"\nname = "A"\nmodel = "nope"\nband = "433"\n',                      # unknown model
], ids=["toml", "extra-key", "radio-not-array", "missing-band", "unknown-field", "bad-band", "bad-model"])
def test_a_bad_file_is_invalid_with_a_reason_and_not_startable(tmp_path, text):
    """R1 (B4-D1): starts are refused until the list is valid; the list alone is the state."""
    rad = _radios()
    _write(tmp_path, text)
    st = rad.load(_paths(tmp_path), MODELS)
    assert st.kind == rad.INVALID and st.reasons and not st.startable and st.radios == ()


def test_an_unreadable_file_is_invalid_not_absent(tmp_path):
    """Unreadable is never absent (hunt class 6): a directory where the file belongs."""
    rad = _radios()
    (tmp_path / "config" / "radios.toml").mkdir(parents=True)
    st = rad.load(_paths(tmp_path), MODELS)
    assert st.kind == rad.INVALID and st.reasons


# Exact types per field (QUALITY-RULES-v1 rules 2 and 3): every other TOML type of each field is refused.
_TOML_VALUES = {"str": '"x"', "int": "1", "float": "1.5", "bool": "true", "list": "[1]",
                "table": "{ a = 1 }", "datetime": "1979-05-27T07:32:00Z", "empty-list": "[]"}
_FIELD_TYPE = {"id": "str", "name": "str", "model": "str", "band": "str", "enabled": "bool", "ce": "str",
               "usb_serial": "str", "usb_port": "str", "high_power": "bool"}
_BASE = {"id": '"r1"', "name": '"A"', "model": '"loraham"', "band": '"433"'}


@pytest.mark.parametrize("field,kind", [(f, k) for f, t in _FIELD_TYPE.items() for k in _TOML_VALUES if k != t])
def test_every_field_refuses_every_other_toml_type(tmp_path, field, kind):
    rad = _radios()
    row = dict(_BASE)
    row[field] = _TOML_VALUES[kind]
    _write(tmp_path, "[[radio]]\n" + "".join(f"{k} = {v}\n" for k, v in row.items()))
    st = rad.load(_paths(tmp_path), MODELS)
    assert st.kind == rad.INVALID, (field, kind)


def test_a_duplicate_key_in_a_row_is_invalid(tmp_path):
    rad = _radios()
    _write(tmp_path, '[[radio]]\nid = "r1"\nid = "r2"\nname = "A"\nmodel = "loraham"\nband = "433"\n')
    assert rad.load(_paths(tmp_path), MODELS).kind == rad.INVALID


# ---- check: the save checks over the whole list (A §2.2) ------------------------------------------

@pytest.mark.parametrize("rid", ["r0", "r100", "R1", "r01", "r1 ", "", "1", "r-1", "r１"])
def test_an_id_outside_r1_to_r99_is_refused(rid):
    assert _reasons([_r(rid)])


@pytest.mark.parametrize("rid", ["r1", "r9", "r10", "r99"])
def test_an_id_from_r1_to_r99_is_accepted(rid):
    assert _reasons([_r(rid)]) == []


def test_a_duplicate_id_is_refused():
    assert _reasons([_r("r1", "A", band="433"), _r("r1", "B", band="868")])


@pytest.mark.parametrize("name", ["", "A" * 13, "a/b", "naïve", "tab\tname"])
def test_a_name_outside_the_rule_is_refused(name):
    assert _reasons([_r("r1", name)])


def test_a_name_of_twelve_characters_is_accepted():
    assert _reasons([_r("r1", "A" * 12)]) == []


def test_names_are_unique_case_insensitively():
    reasons = _reasons([_r("r1", "Stick", band="433"), _r("r2", "STICK", band="868")])
    assert reasons and any("r2" in x for x in reasons)


def test_a_band_the_model_does_not_have_is_refused():
    """The row is valid apart from its band (a serial stick, so no port rule applies)."""
    assert _reasons([_r("r1", model="meshstick-1262", band="868", usb_serial="ABCDEFGH")]) == []
    assert _reasons([_r("r1", model="meshstick-1262", band="433", usb_serial="ABCDEFGH")])


def test_a_model_with_several_variants_needs_its_ce():
    """uputronics has ce0/ce1 for either band: no ce selects nothing (variant() is None)."""
    rad = _radios()
    r = _r("r1", model="uputronics", band="433")
    assert rad.variant(r, MODELS["uputronics"]) is None
    assert _reasons([r])
    assert _reasons([dataclasses.replace(r, ce="ce2")])
    assert rad.variant(dataclasses.replace(r, ce="ce1"), MODELS["uputronics"]).key == "ce1"


def test_a_model_with_one_variant_per_band_needs_no_ce():
    rad = _radios()
    assert rad.variant(_r("r1", band="868"), MODELS["loraham"]).key == "868"
    assert _reasons([_r("r1", band="868")]) == []


def test_the_two_loraham_modules_are_one_box():
    assert _reasons([_r("r1", "L433", band="433"), _r("r2", "L868", band="868")]) == []


def test_h1_a_pin_claimed_by_another_spi_row_is_refused_naming_it():
    """H1: the loraham 433 module and uputronics CE0 both use BCM 8 (cs) and 25 (irq)."""
    reasons = _reasons([_r("r1", band="433"), _r("r2", model="uputronics", band="868", ce="ce0")])
    assert any("r2" in x and "r1" in x for x in reasons)


def test_h1_a_disabled_row_still_claims_its_pins():
    reasons = _reasons([_r("r1", band="433", enabled=False),
                        _r("r2", model="uputronics", band="868", ce="ce0")])
    assert any("r1" in x for x in reasons)


def test_a_usb_setting_on_an_spi_row_is_refused():
    assert _reasons([_r("r1", usb_serial="ABCDEFGH")])
    assert _reasons([_r("r1", usb_port="1-1.3")])


def test_high_power_on_a_model_without_the_switch_is_refused():
    assert _reasons([_r("r1", model="waveshare-sx1262", band="868", high_power=True)])
    assert _reasons([_r("r1", high_power=True)]) == []


def _stick(rid, serial="", port="", name=None):
    return _r(rid, name or f"S{rid}", model="meshstick-1262", band="868", usb_serial=serial, usb_port=port)


def test_h2_serials_must_differ_in_their_first_eight_characters():
    """H2, D13: pine64 and the daemon compare the first 8 characters."""
    assert _reasons([_stick("r1", "ABCDEFGH-1"), _stick("r2", "ABCDEFGH-2")])
    assert _reasons([_stick("r1", "ABCDEFGH1"), _stick("r2", "ABCDEFGI1")]) == []


def test_h2_at_most_one_stick_without_a_serial():
    """W19: the daemon has no port selector, so a second serial-less stick cannot be told apart."""
    assert _reasons([_stick("r1", port="1-1.2"), _stick("r2", port="1-1.3")])


def test_d13_a_long_serial_is_accepted_and_compared_by_its_prefix():
    assert _reasons([_stick("r1", "ABCDEFGHIJKLMNOP")]) == []


def test_a_bus_port_must_look_like_one():
    for bad in ("1", "1-", "-1", "1-1.", "a-1", "1-1..2", "1-1 "):
        assert _reasons([_stick("r1", port=bad)]), bad


def test_more_than_99_radios_are_refused():
    rad = _radios()
    many = [_r(f"r{n}", f"N{n}", model="meshstick-1262", band="868", usb_serial=f"SER{n:05d}")
            for n in range(1, 100)]
    assert not [x for x in rad.check(many, MODELS) if "99" in x]
    extra = [*many, _r("r1", "N100", model="meshstick-1262", band="868", usb_serial="SER00100")]
    assert any("99" in x for x in rad.check(extra, MODELS))


# ---- next_id, default_name, with_row, without -----------------------------------------------------

def test_next_id_is_the_lowest_free_number():
    rad = _radios()
    assert rad.next_id([]) == "r1"
    assert rad.next_id([_r("r1"), _r("r3")]) == "r2"
    assert rad.next_id([_r(f"r{n}") for n in range(1, 10)]) == "r10"


def test_next_id_refuses_when_all_99_are_taken():
    rad = _radios()
    with pytest.raises(ValueError):
        rad.next_id([_r(f"r{n}") for n in range(1, 100)])


def test_default_name_is_short_and_band_then_numbered():
    rad = _radios()
    m = MODELS["meshstick-1262"]
    first = rad.default_name([], m)
    assert first == f"{m.short} 868"
    second = rad.default_name([_stick("r1", "ABCDEFGH", name=first)], m)
    assert second == f"{m.short} 2"


def test_with_row_replaces_by_id_or_appends_and_without_removes():
    rad = _radios()
    a, b = _r("r1", "A"), _r("r2", "B", band="868")
    assert rad.with_row((a,), b) == (a, b)
    a2 = dataclasses.replace(a, name="A2")
    assert rad.with_row((a, b), a2) == (a2, b)
    assert rad.without((a, b), "r1") == (b,)


# ---- render / load_text round trip -----------------------------------------------------------------

_SAMPLES = {   # each row as (id, name, model, band, extra fields); built inside the test (no import at collection)
    "empty": [],
    "one": [("r1", "LoRaHAM 433", "loraham", "433", {})],
    "two-bands": [("r1", "L433", "loraham", "433", {}),
                  ("r2", "L868", "loraham", "868", {"enabled": False, "high_power": True})],
    "ce": [("r1", "Up", "uputronics", "433", {"ce": "ce1"})],
    "sticks": [("r1", "S1", "meshstick-1262", "868", {"usb_serial": "ABCDEFGH"}),
               ("r2", "S2", "meshstick-1262", "868", {"usb_port": "1-1.3"})],
    "name-chars": [("r1", "a.b_c-d e", "loraham", "433", {})],
}


@pytest.mark.parametrize("sample", sorted(_SAMPLES))
def test_render_reads_back_as_the_same_list(sample):
    rad = _radios()
    rows = [_r(rid, name, model, band, **kw) for rid, name, model, band, kw in _SAMPLES[sample]]
    st = rad.load_text(rad.render(rows), MODELS)
    assert st.kind == rad.VALID and list(st.radios) == list(rows)
    tomllib.loads(rad.render(rows))                       # well-formed TOML


# ---- save: checked whole, written whole (H6, B4-D1) -------------------------------------------------

def test_save_writes_a_valid_list_that_loads_back(tmp_path):
    rad = _radios()
    rows = [_r("r1", "L433", band="433"), _r("r2", "L868", band="868")]
    assert rad.save(_paths(tmp_path), rows, MODELS) == []
    assert list(rad.load(_paths(tmp_path), MODELS).radios) == rows


def test_save_refuses_a_list_that_fails_a_check_and_writes_nothing(tmp_path):
    rad = _radios()
    reasons = rad.save(_paths(tmp_path), [_r("r1"), _r("r1", "B", band="868")], MODELS)
    assert reasons
    assert not (tmp_path / "config" / "radios.toml").exists()


def _old_file(tmp_path):
    rad = _radios()
    assert rad.save(_paths(tmp_path), [_r("r1", "Old", band="433")], MODELS) == []
    p = tmp_path / "config" / "radios.toml"
    return p, p.read_bytes()


@pytest.mark.parametrize("fault", ["open", "write", "short-write", "fsync", "replace"])
def test_h6_a_failure_before_the_rename_leaves_the_old_file_byte_unchanged(tmp_path, monkeypatch, fault):
    """H6 (B4-D1): on failure REFUSE and leave the file unchanged; no temp file stays behind."""
    rad = _radios()
    p, before = _old_file(tmp_path)
    real = {"open": os.open, "write": os.write, "fsync": os.fsync, "replace": os.replace}

    def boom(*a, **k):
        raise OSError(28, "No space left on device")

    if fault == "open":
        monkeypatch.setattr(os, "open", lambda f, *a, **k: boom() if str(f).endswith(".tmp") else real["open"](f, *a, **k))
    elif fault == "write":
        monkeypatch.setattr(os, "write", boom)
    elif fault == "short-write":
        monkeypatch.setattr(os, "write", lambda fd, data: 0)
    elif fault == "fsync":
        calls = []
        monkeypatch.setattr(os, "fsync", lambda fd: (calls.append(fd), boom())[1] if not calls else real["fsync"](fd))
    elif fault == "replace":
        monkeypatch.setattr(os, "replace", boom)
    reasons = rad.save(_paths(tmp_path), [_r("r1", "New", band="433")], MODELS)
    monkeypatch.undo()
    assert reasons
    assert p.read_bytes() == before
    assert not list(p.parent.glob("*.tmp"))


def test_h6_a_failed_first_save_leaves_no_file(tmp_path, monkeypatch):
    rad = _radios()
    monkeypatch.setattr(os, "replace", lambda *a, **k: (_ for _ in ()).throw(OSError(5, "I/O error")))
    assert rad.save(_paths(tmp_path), [_r("r1")], MODELS)
    monkeypatch.undo()
    assert rad.load(_paths(tmp_path), MODELS).kind == rad.ABSENT


# ---- requests (INTERFACE-B4-v8 §1b) ---------------------------------------------------------------

def test_q1_a_failure_after_the_rename_does_not_claim_the_old_file_is_unchanged(tmp_path, monkeypatch):
    """Q1: once os.replace ran, the new list is the file. A failed directory fsync must not be
    reported as "the old file is unchanged": the operator would believe the old list still holds."""
    rad = _radios()
    p, before = _old_file(tmp_path)
    real_open, real_fsync = os.open, os.fsync
    dir_fds = set()

    def track_open(path, flags, *a, **k):
        fd = real_open(path, flags, *a, **k)
        if flags & getattr(os, "O_DIRECTORY", 0):
            dir_fds.add(fd)
        return fd

    def fsync(fd):
        if fd in dir_fds:
            raise OSError(5, "I/O error")
        return real_fsync(fd)

    monkeypatch.setattr(os, "open", track_open)
    monkeypatch.setattr(os, "fsync", fsync)
    reasons = rad.save(_paths(tmp_path), [_r("r1", "New", band="433")], MODELS)
    monkeypatch.undo()
    assert p.read_bytes() != before                                   # the new list is in place
    assert reasons, "a durability failure is still reported"
    assert not any("unchanged" in x for x in reasons), reasons


def test_q2_a_config_dir_that_escapes_the_runtime_root_is_invalid_not_an_exception(tmp_path):
    """Q2: paths.under raises PathContainmentError (a ValueError) for a symlink out of the root; load
    must answer INVALID, and save must refuse, never raise."""
    rad = _radios()
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "config").symlink_to(outside, target_is_directory=True)
    paths = resolve_paths({"LHPC_RUNTIME_ROOT": str(root)})
    st = rad.load(paths, MODELS)
    assert st.kind == rad.INVALID and st.reasons
    assert rad.save(paths, [_r("r1")], MODELS)
    assert not (outside / "radios.toml").exists()


def test_q3_a_serial_shorter_than_eight_characters_is_refused():
    """Q3 = D13b: the daemon passes and compares the first 8 characters."""
    assert _reasons([_stick("r1", "ABC1234")])


def test_q4_the_serialless_stick_needs_its_port_and_a_serial_stick_has_none():
    """Q4: the BOUND check after READY compares `port:<stored port>` (plan A §2.2, F1: the three-way check after READY)."""
    assert _reasons([_stick("r1")])
    assert _reasons([_stick("r1", "ABCDEFGH", port="1-1.3")])
    assert _reasons([_stick("r1", port="1-1.3")]) == []


@pytest.mark.parametrize("serial", ["ABCD EFGH", "ABCDEFGé", "{source}X", "ABC}DEFGH", "ABCDEFG\x7f"])
def test_q5_a_serial_outside_0x21_to_0x7e_or_with_braces_is_refused(serial):
    assert _reasons([_stick("r1", serial)])


def test_q6_load_text_refuses_what_load_refuses():
    rad = _radios()
    for text in ('other = 1\n', 'radio = "x"\n'):
        st = rad.load_text(text, MODELS)
        assert st.kind == rad.INVALID, text


def test_q7_default_name_never_returns_a_name_outside_the_rule():
    rad = _radios()
    odd = dataclasses.replace(MODELS["meshstick-1262"], short="st!ck")
    with pytest.raises(ValueError):
        rad.default_name([], odd)


def test_q8_an_unset_high_power_differs_from_an_explicit_false(tmp_path):
    """Q8: "set on the row" must differ from "not set" (M7 carry of hipower_<band>, a +20 dBm opt-in):
    an operator's saved false must load as False, a row without the field as None."""
    rad = _radios()
    assert _r("r1").high_power is None
    assert rad.save(_paths(tmp_path), [_r("r1", "Off", band="433", high_power=False),
                                       _r("r2", "Unset", band="868")], MODELS) == []
    loaded = {r.id: r for r in rad.load(_paths(tmp_path), MODELS).radios}
    assert loaded["r1"].high_power is False
    assert loaded["r2"].high_power is None


def test_q9_a_deeply_nested_file_is_invalid_not_an_exception(tmp_path):
    """Q9: tomllib recurses on nested arrays; the precondition is measured here first."""
    rad = _radios()
    text = "radio = " + "[" * 1000 + "]" * 1000 + "\n"
    with pytest.raises(RecursionError):
        tomllib.loads(text)
    _write(tmp_path, text)
    assert rad.load(_paths(tmp_path), MODELS).kind == rad.INVALID
    assert rad.load_text(text, MODELS).kind == rad.INVALID


@pytest.mark.parametrize("field,value", [("name", "Stick\n"), ("id", "r1\n")])
def test_q10_a_trailing_newline_is_no_part_of_a_valid_id_or_name(field, value):
    """Q10 (found by this file): `^...$` matches before a trailing newline, so `_NAME` and `_ID` used
    with `match` accept "Stick\\n" and "r1\\n". A rule is applied with fullmatch (or \\Z)."""
    assert _reasons([dataclasses.replace(_r("r1", "Stick"), **{field: value})])


def test_q10_a_trailing_newline_is_no_part_of_a_bus_port():
    assert _reasons([_stick("r1", port="1-1.3\n")])


# ---- every reader of a file, the same classes (QUALITY rule 6: the inventory of Q2/Q9) ---------------
# The readers: radios.load (config/radios.toml) and radio_daemon.old_config_state (config/radios.toml by lstat,
# config/local.toml by read and parse). Each class below is run against each reader that reads that file.

def test_q9_class_a_non_utf8_hardware_list_is_invalid_not_an_exception(tmp_path):
    rad = _radios()
    p = tmp_path / "config" / "radios.toml"
    p.parent.mkdir(parents=True)
    # the bytes sit in a comment: a lenient decode (errors="replace") would load this list as VALID
    p.write_bytes(b'# \xff\xfe\n[[radio]]\nid = "r1"\nname = "A"\nmodel = "loraham"\nband = "433"\n')
    assert rad.load(_paths(tmp_path), MODELS).kind == rad.INVALID


def test_q13_a_dangling_hardware_list_symlink_is_invalid_not_absent(tmp_path):
    """Q13 (found by this inventory): a radios.toml that is a symlink to nothing is present but unreadable;
    reading it as ABSENT ("no hardware list") is the unreadable-vs-absent collision."""
    rad = _radios()
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "radios.toml").symlink_to(tmp_path / "nowhere.toml")
    assert rad.load(_paths(tmp_path), MODELS).kind == rad.INVALID


def test_q2_class_a_hardware_list_symlink_out_of_the_root_is_invalid_and_never_written(tmp_path):
    rad = _radios()
    root, outside = tmp_path / "root", tmp_path / "outside"
    (root / "config").mkdir(parents=True)
    outside.mkdir()
    target = outside / "r.toml"
    target.write_text('[[radio]]\nid = "r1"\nname = "A"\nmodel = "loraham"\nband = "433"\n', encoding="utf-8")
    before = target.read_bytes()
    (root / "config" / "radios.toml").symlink_to(target)
    paths = resolve_paths({"LHPC_RUNTIME_ROOT": str(root)})
    assert rad.load(paths, MODELS).kind == rad.INVALID
    assert rad.save(paths, [_r("r1", "B")], MODELS)
    assert target.read_bytes() == before
