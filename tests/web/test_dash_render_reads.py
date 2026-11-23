"""What one dashboard render reads: the radio list and the model table are parsed once per file text, never once
per read (one render read them ~470 and ~527 times), and a changed file is seen on the very next read."""
from __future__ import annotations

import os
import tomllib

import pytest

from lhpc.adapters.web.app import create_app
from lhpc.core import radio_models, radios
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService

_LIST = (radios.Radio("r1", "low", "loraham", "433"),
         radios.Radio("r3", "mstick", "meshstick-1262", "868", usb_serial="MSTICK01"))


@pytest.fixture
def dash(tmp_path):
    """A console over a runtime with two enabled radios (r1 433, r3 868); returns (client, radios.toml path)."""
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    listed = tmp_path / "config" / "radios.toml"
    listed.write_text(radios.render(_LIST))
    system = FakeSystem().system
    client = create_app(service_factory=lambda: ControllerService(system=system,
                                                                  paths=Paths(runtime_root=tmp_path))).test_client()
    return client, listed


def _parses(monkeypatch) -> list:
    """Every text tomllib parses from now on."""
    seen: list = []
    real = tomllib.loads

    def counting(text, *a, **k):
        seen.append(text)
        return real(text, *a, **k)
    monkeypatch.setattr(tomllib, "loads", counting)
    return seen


def test_two_dashboard_renders_parse_the_radio_list_and_the_model_table_at_most_once(dash, monkeypatch):
    client, listed = dash
    seen = _parses(monkeypatch)
    for _ in range(2):
        assert client.get("/").status_code == 200
    table = radio_models.TABLE.read_text(encoding="utf-8")
    assert seen.count(table) <= 1, f"the model table parsed {seen.count(table)} times"
    assert seen.count(listed.read_text()) <= 1, f"the radio list parsed {seen.count(listed.read_text())} times"


def test_a_same_size_rewrite_under_the_old_timestamp_is_seen_on_the_next_render(dash):
    """The list is rewritten in place with r3 disabled, at the same size and with the old mtime put back: a cache
    keyed on the file's inode, size and mtime would serve the stale list. The next render shows the change."""
    client, listed = dash
    assert "(r3, 868 MHz)" in client.get("/").get_data(as_text=True)
    before = os.stat(listed)
    text = listed.read_text()
    changed = text.replace('name = "mstick"', 'name = "mstik"').replace(
        'band = "868"\nenabled = true', 'band = "868"\nenabled = false')
    assert len(changed.encode()) == len(text.encode()) and changed != text
    with open(listed, "r+", encoding="utf-8") as f:          # in place: the same inode
        f.write(changed)
    os.utime(listed, ns=(before.st_atime_ns, before.st_mtime_ns))
    after = os.stat(listed)
    assert (after.st_ino, after.st_size, after.st_mtime_ns) == (before.st_ino, before.st_size, before.st_mtime_ns)
    assert "(r3, 868 MHz)" not in client.get("/").get_data(as_text=True)


def test_each_model_table_read_is_its_own_and_a_change_to_one_never_reaches_the_next():
    first = radio_models.load()
    usb_model = next(m for m in first.values() if m.usb)
    direct_model = next(m for m in first.values() if m.direct)
    pinned = next(m for m in first.values() if any(v.pins for v in m.variants))
    keep = (usb_model.usb["vid"], direct_model.direct["module"], dict(pinned.variants[0].pins))
    usb_model.usb["vid"] = "ffff"                          # a caller changes what it was given
    direct_model.direct["module"] = "other"
    pinned.variants[0].pins.clear()
    first.pop(usb_model.id)
    second = radio_models.load()
    assert (second[usb_model.id].usb["vid"], second[direct_model.id].direct["module"],
            dict(second[pinned.id].variants[0].pins)) == keep


def test_the_save_checks_run_with_the_callers_models_on_every_read():
    text = radios.render(_LIST)
    assert radios.load_text(text).kind == radios.VALID           # the shipped table: the list holds
    assert radios.load_text(text, {}).kind == radios.INVALID      # the same text, no models: it does not
    assert radios.load_text(text).kind == radios.VALID
