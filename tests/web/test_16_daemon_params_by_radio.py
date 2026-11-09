"""Item 16 batch 2 (DEC-W53): the daemon's settings panel per radio. `rid` on the panel and on its Save, Apply and
Reset (the panel posts every param as dp_<PARAM>): the values are that radio's own, its row's dp_<PARAM> fields in config/radios.toml, written under the
hardware list's lock (W217) through radios.save, which keeps the list's reserved ids; Apply sets that radio's daemon
only. Driven through the console's routes; the saved file is read back."""
import contextlib
import re

import pytest
import radios_fixture

from lhpc.core import config as cfgmod
from lhpc.core import radios
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem

ROWS = (radios.Radio("r1", "a", "uputronics", "433", ce="ce0"), radios.Radio("r2", "b", "uputronics", "433", ce="ce1"))
STATUS = "STATUS ID={} BOUND=- RADIO=READY TX=0 TXMODE=MANAGED CADWAIT=1500 CADRSSI=-90\n"


@pytest.fixture
def box(web, tmp_path, csrf):
    radios_fixture.write(tmp_path, ROWS)
    text = (tmp_path / "config" / "radios.toml").read_text()
    (tmp_path / "config" / "radios.toml").write_text('retired = ["r9"]\n' + text)      # a reserved id (DEC-W39)
    assert radios.load(Paths(runtime_root=tmp_path)).retired == ("r9",)
    fake = FakeSystem(unix_replies={f"/tmp/loraconf-{r}.sock": STATUS.format(r).encode() for r in ("r1", "r2")})
    c = web(system=fake.system)
    token = csrf(c, "/stacks?dp=daemon")

    def post(path, **form):
        return c.post(path, data={"_csrf": token, **form})
    return c, post, fake, tmp_path


def _row(tmp_path, rid):
    return next(r for r in radios.load(Paths(runtime_root=tmp_path)).radios if r.id == rid)


def test_a_save_for_one_radio_writes_its_row_alone_and_keeps_the_reserved_ids(box):
    c, post, _fake, tmp_path = box
    r = post("/stacks/daemon/daemon-params", radio="r2", dp_FREQ="433.600")
    assert r.status_code == 302
    assert dict(_row(tmp_path, "r2").dp) == {"FREQ": "433.600"} and _row(tmp_path, "r1").dp == ()
    assert radios.load(Paths(runtime_root=tmp_path)).retired == ("r9",)
    assert "radio=r2" in r.headers["Location"]                                # back to that radio's panel (W2)


def test_a_blank_value_clears_the_radios_own_value_and_reset_clears_them_all(box):
    _c, post, _fake, tmp_path = box
    post("/stacks/daemon/daemon-params", radio="r2", dp_FREQ="433.600", dp_SF="9")
    post("/stacks/daemon/daemon-params", radio="r2", dp_FREQ="", dp_SF="9")
    assert dict(_row(tmp_path, "r2").dp) == {"SF": "9"}
    post("/stacks/daemon/daemon-params/reset", radio="r2")
    assert _row(tmp_path, "r2").dp == ()


def test_a_busy_hardware_list_changes_nothing(box, monkeypatch):
    _c, post, _fake, tmp_path = box
    before = (tmp_path / "config" / "radios.toml").read_bytes()

    @contextlib.contextmanager
    def busy(paths, timeout=0):
        raise cfgmod.ConfigLockBusy("held by another save")
        yield
    monkeypatch.setattr(cfgmod, "config_lock", busy)
    post("/stacks/daemon/daemon-params", radio="r2", dp_FREQ="433.600")
    assert (tmp_path / "config" / "radios.toml").read_bytes() == before


def test_an_invalid_value_is_refused_and_writes_nothing(box):
    _c, post, _fake, tmp_path = box
    before = (tmp_path / "config" / "radios.toml").read_bytes()
    post("/stacks/daemon/daemon-params", radio="r2", dp_SF="99")
    assert (tmp_path / "config" / "radios.toml").read_bytes() == before


def test_the_panel_shows_each_radios_own_values_and_every_radio_to_choose(box):
    _c, post, _fake, tmp_path = box
    post("/stacks/daemon/daemon-params", radio="r2", dp_FREQ="433.600")
    from lhpc.core.services import ControllerService
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    v2, v1 = svc.daemon_params_view("daemon", rid="r2"), svc.daemon_params_view("daemon", rid="r1")
    freq = lambda v: next(row["value"] for row in v["rows"] if row["name"] == "FREQ")  # noqa: E731
    assert (v2["rid"], freq(v2)) == ("r2", "433.600") and freq(v1) != "433.600"
    assert v1["radios"] == [("r1", "a", "433"), ("r2", "b", "433")]
    k2 = svc.daemon_params_view("kiss", rid="r2")                             # DEC-W384az: kiss@r2's own set over r2's row
    assert (k2["rid"], k2["is_daemon"], freq(k2)) == ("r2", False, "433.600")


def test_apply_for_one_radio_sets_that_radios_daemon_only(box):
    _c, post, fake, _tmp_path = box
    post("/stacks/daemon/daemon-params/apply", radio="r2", dp_FREQ="433.600")
    assert {path for path, _payload in fake.sent} == {"/tmp/loraconf-r2.sock"}
    assert ("/tmp/loraconf-r2.sock", b"SET FREQ=433.600\n") in fake.sent


@pytest.mark.parametrize("path", ["/stacks/daemon/daemon-params", "/stacks/daemon/daemon-params/apply",
                                  "/stacks/daemon/daemon-params/reset"])
def test_a_rid_that_is_no_enabled_radio_of_the_band_is_a_404_and_writes_nothing(box, path):
    _c, post, _fake, tmp_path = box
    before = (tmp_path / "config" / "radios.toml").read_bytes()
    r = post(path, radio="r9", dp_FREQ="433.600")
    assert (r.status_code, r.get_json()) == (404, {"error": "r9 is not a radio of the hardware list"})
    assert (tmp_path / "config" / "radios.toml").read_bytes() == before


def test_the_overview_never_fails_on_a_rid_it_cannot_use(box):
    c, _post, _fake, _tmp_path = box
    for q in ("dp=daemon&radio=r9", "dp=daemon&radio=r2&band=868", "dp=kiss&radio=r2"):          # W2: ?radio=
        assert c.get(f"/stacks?{q}").status_code == 200, q
    assert re.search(r"daemon", c.get("/stacks?dp=daemon&radio=r2").get_data(as_text=True))


def test_a_direct_save_of_some_params_keeps_the_radios_other_values(box):
    # A direct caller patches a subset: a param not in `values` is left unchanged, also on a radio's row.
    from lhpc.core.services import ControllerService
    _c, _post, _fake, tmp_path = box
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    assert svc.save_daemon_params("daemon", {"SF": "9"}, rid="r2").ok
    assert svc.save_daemon_params("daemon", {"FREQ": "433.600"}, rid="r2").ok
    assert dict(_row(tmp_path, "r2").dp) == {"FREQ": "433.600", "SF": "9"}


def test_the_overview_shows_the_bands_panel_for_a_rid_it_cannot_use(box):
    c, _post, _fake, _tmp_path = box
    plain = c.get("/stacks?dp=daemon").get_data(as_text=True)
    bad = c.get("/stacks?dp=daemon&radio=r9").get_data(as_text=True)
    marker = 'name="dp_FREQ"'
    assert marker in plain and marker in bad


def test_apply_for_a_radio_without_a_band_uses_the_radios_band(tmp_path):
    # rid alone: the radio's own band decides the values (868's defaults for an 868 radio), never the first band's.
    from lhpc.core.services import ControllerService
    radios_fixture.write(tmp_path, (*ROWS, radios.Radio("r3", "c", "meshstick-1262", "868", usb_serial="MS654321")))
    fake = FakeSystem(unix_replies={"/tmp/loraconf-r3.sock": STATUS.format("r3").encode()})
    svc = ControllerService(system=fake.system, paths=Paths(runtime_root=tmp_path))
    svc.apply_daemon_params("daemon", rid="r3")
    freq = [p.partition(b"=")[2] for _path, p in fake.sent if p.partition(b"=")[0] == b"SET FREQ"]
    assert {path for path, _p in fake.sent} == {"/tmp/loraconf-r3.sock"} and freq and b"433" not in freq[0]
