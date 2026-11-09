"""The update to L5 (plan A §2.2 "Old config", W12; B4-TABLES-v2 rows M1, M7, D14, D15; B4-D1, B4-D4).

Nothing is converted: an old `config/local.toml [radio]` without `config/radios.toml` shows the banner;
band params are carried at start resolution only (CARRY_BAND_PARAMS, B4-D1), never written; the boot-restore
plan drops pre-0.14.0 band items and refuses items of radios that are gone or disabled. New feature, no old
path: the module is imported per test.
"""
from __future__ import annotations

import pytest

from lhpc.core import boot_restore, radio_models
from lhpc.core.paths import resolve_paths

MODELS = radio_models.load()


def _radios():
    from lhpc.core import radios
    return radios


def _rd():
    from lhpc.core import radio_daemon  # B4-D5: the daemon-per-radio functions
    return radio_daemon


def _paths(tmp_path):
    return resolve_paths({"LHPC_RUNTIME_ROOT": str(tmp_path)})


def _r(rid="r1", band="433", **kw):
    return _radios().Radio(id=rid, name=f"Radio {rid}", model="loraham", band=band, **kw)


def _cfg(tmp_path, name, text):
    p = tmp_path / "config" / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


# ---- M1: the W12 banner -------------------------------------------------------------------------

def test_m1_old_radio_config_without_a_hardware_list_shows_the_banner(tmp_path):
    _cfg(tmp_path, "local.toml", '[radio]\nmode = "dual"\n')
    assert _rd().old_config_state(_paths(tmp_path)) is _rd().Tri.YES


def test_m1_no_banner_once_a_hardware_list_exists(tmp_path):
    _cfg(tmp_path, "local.toml", '[radio]\nmode = "dual"\n')
    _cfg(tmp_path, "radios.toml", "")
    assert _rd().old_config_state(_paths(tmp_path)) is _rd().Tri.NO


def test_m1_a_present_but_broken_hardware_list_still_means_no_banner(tmp_path):
    """The broken list is load()'s INVALID, not the old-config banner."""
    _cfg(tmp_path, "local.toml", '[radio]\nmode = "dual"\n')
    _cfg(tmp_path, "radios.toml", "[[radio]\n")
    assert _rd().old_config_state(_paths(tmp_path)) is _rd().Tri.NO


@pytest.mark.parametrize("text", ["", "[other]\nx = 1\n", "radio = 1\n"])
def test_m1_no_banner_without_an_old_radio_table(tmp_path, text):
    _cfg(tmp_path, "local.toml", text)
    assert _rd().old_config_state(_paths(tmp_path)) is _rd().Tri.NO


def test_m1_no_banner_on_a_fresh_box(tmp_path):
    assert _rd().old_config_state(_paths(tmp_path)) is _rd().Tri.NO


def test_m1_an_unreadable_old_config_is_unknown_not_no(tmp_path):
    _cfg(tmp_path, "local.toml", "[radio\n")
    assert _rd().old_config_state(_paths(tmp_path)) is _rd().Tri.UNKNOWN
    (tmp_path / "config" / "local.toml").unlink()
    (tmp_path / "config" / "local.toml").mkdir()
    assert _rd().old_config_state(_paths(tmp_path)) is _rd().Tri.UNKNOWN


def test_m1_nothing_is_written(tmp_path):
    p = _cfg(tmp_path, "local.toml", '[radio]\nmode = "dual"\n')
    before = p.read_bytes()
    _rd().old_config_state(_paths(tmp_path))
    assert p.read_bytes() == before
    assert not (tmp_path / "config" / "radios.toml").exists()


# ---- M7: the carry switch (B4-D1) ---------------------------------------------------------------

def test_resolving_a_radios_settings_writes_nothing(tmp_path):
    rad = _radios()
    defaults = {"txmode": "managed", "cadmon": "off", "cadrssi": "-90", "hipower": "off", "ocp": "auto"}
    assert rad.save(_paths(tmp_path), [_r("r1")], MODELS) == []
    p = tmp_path / "config" / "radios.toml"
    before = p.read_bytes()
    s = rad.radio_settings(_r("r1", txmode="direct"), MODELS["loraham"], defaults)
    assert s.txmode == "direct"
    assert p.read_bytes() == before


# ---- D14, D15: boot restore across the update ---------------------------------------------------

def _item(item_id, kind, band="", rid=None):
    it = boot_restore.new_item(item_id, kind, band=band)
    if rid is not None:
        it["rid"] = rid
    return it


def test_d14_a_pre_014_band_item_is_dropped_silently():
    keep, refused = _rd().restore_plan([_item("a", "daemon", band="433")], [_r("r1")])
    assert keep == [] and refused == []


def test_d15_an_item_of_a_radio_that_is_gone_is_refused_with_a_reason():
    keep, refused = _rd().restore_plan([_item("a", "daemon", rid="r9"), _item("b", "stack", rid="r1")],
                                     [_r("r1")])
    assert [i["id"] for i in keep] == ["b"]
    assert [i["id"] for i, _ in refused] == ["a"] and refused[0][1]


@pytest.mark.parametrize("kind", ["daemon", "stack"])
def test_d15_an_item_of_a_disabled_radio_is_refused(kind):
    keep, refused = _rd().restore_plan([_item("a", kind, rid="r1")], [_r("r1", enabled=False)])
    assert keep == [] and [i["id"] for i, _ in refused] == ["a"]


def test_a_bandless_item_without_a_radio_is_kept():
    """B4-D4: band-less restore items (Reticulum's one node) are kept."""
    keep, refused = _rd().restore_plan([_item("rns", "stack")], [_r("r1")])
    assert [i["id"] for i in keep] == ["rns"] and refused == []


def test_restore_plan_keeps_the_order():
    items = [_item("c", "stack", rid="r2"), _item("a", "daemon", rid="r1"), _item("b", "stack", rid="r1")]
    keep, _ = _rd().restore_plan(items, [_r("r1"), _r("r2", band="868")])
    assert [i["id"] for i in keep] == ["c", "a", "b"]


def test_m1_a_hardware_list_that_cannot_be_stat_ed_means_no_banner(tmp_path):
    """Any lstat result of config/radios.toml but "absent" means no banner (INTERFACE-B4-v8 §3): here config/
    escapes the runtime root, so the path is refused (PathContainmentError), and an old [radio] table behind it
    must not raise the banner."""
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "local.toml").write_text('[radio]\nmode = "dual"\n', encoding="utf-8")
    (root / "config").symlink_to(outside, target_is_directory=True)
    rd = _rd()
    assert rd.old_config_state(resolve_paths({"LHPC_RUNTIME_ROOT": str(root)})) is rd.Tri.NO


@pytest.mark.parametrize("data", [b"radio = " + b"[" * 1000 + b"]" * 1000 + b"\n", b'# \xff\xfe\n[radio]\nmode = "dual"\n'],
                         ids=["deep", "non-utf8"])
def test_m1_an_old_config_that_cannot_be_parsed_is_unknown_not_an_exception(tmp_path, data):
    """The Q9 class for the second reader (local.toml): deep nesting and non-UTF-8 bytes are UNKNOWN."""
    rd = _rd()
    p = tmp_path / "config" / "local.toml"
    p.parent.mkdir(parents=True)
    p.write_bytes(data)
    assert rd.old_config_state(_paths(tmp_path)) is rd.Tri.UNKNOWN


def test_m1_a_dangling_hardware_list_symlink_means_no_banner(tmp_path):
    """Present (lstat succeeds) though unreadable: no banner; the list itself is INVALID (Q13)."""
    rd = _rd()
    _cfg(tmp_path, "local.toml", '[radio]\nmode = "dual"\n')
    (tmp_path / "config" / "radios.toml").symlink_to(tmp_path / "nowhere.toml")
    assert rd.old_config_state(_paths(tmp_path)) is rd.Tri.NO


def test_q14_a_dangling_old_config_symlink_is_unknown_not_absent(tmp_path):
    """Q14 (found by the inventory): a local.toml that is a symlink to nothing is present but unreadable:
    UNKNOWN, never NO (NO is "a fresh box": a W12 banner could then be missed)."""
    rd = _rd()
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "local.toml").symlink_to(tmp_path / "nowhere.toml")
    assert rd.old_config_state(_paths(tmp_path)) is rd.Tri.UNKNOWN


def test_q2_class_an_old_config_symlink_out_of_the_root_is_unknown_not_an_exception(tmp_path):
    rd = _rd()
    root, outside = tmp_path / "root", tmp_path / "outside"
    (root / "config").mkdir(parents=True)
    outside.mkdir()
    (outside / "l.toml").write_text('[radio]\nmode = "dual"\n', encoding="utf-8")
    (root / "config" / "local.toml").symlink_to(outside / "l.toml")
    assert rd.old_config_state(resolve_paths({"LHPC_RUNTIME_ROOT": str(root)})) is rd.Tri.UNKNOWN


def test_m1_a_directory_where_the_hardware_list_belongs_means_no_banner(tmp_path):
    """Present (lstat succeeds) but not a file: no banner, whatever local.toml holds; the list is INVALID."""
    rd = _rd()
    _cfg(tmp_path, "local.toml", '[radio]\nmode = "dual"\n')
    (tmp_path / "config" / "radios.toml").mkdir()
    assert rd.old_config_state(_paths(tmp_path)) is rd.Tri.NO
