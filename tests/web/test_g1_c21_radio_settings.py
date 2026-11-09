"""Gate-1 C-21 on item 16 batch 2, a radio's daemon settings save through the console's route (the panel posts every
param as dp_<PARAM>, as it shows it): B1 the save is judged on the hardware list as it reads under the list's lock
(W217), never on one read before it; B2 a radio keeps what the operator set, its source default included, and a
value posted as shown stays inherited from the band."""
import contextlib

import pytest
import radios_fixture

from lhpc.core import config as cfgmod
from lhpc.core import radios
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem

STATUS = "STATUS ID={} BOUND=- RADIO=READY TX=0 TXMODE=MANAGED CADWAIT=1500 CADRSSI=-90\n"


def _rows(r2_high_power):
    return (radios.Radio("r1", "a", "uputronics", "433", ce="ce0"),
            radios.Radio("r2", "b", "uputronics", "433", ce="ce1", high_power=r2_high_power))


@pytest.fixture
def box(web, tmp_path, csrf):
    radios_fixture.write(tmp_path, _rows(True))
    fake = FakeSystem(unix_replies={f"/tmp/loraconf-{r}.sock": STATUS.format(r).encode() for r in ("r1", "r2")})
    c = web(system=fake.system)
    token = csrf(c, "/stacks?dp=daemon")

    def post(**form):
        band = {} if "radio" in form else {"band": "433"}               # W3: a radio's save names the radio alone
        return c.post("/stacks/daemon/daemon-params", data={"_csrf": token, **band, **form})
    return post, tmp_path


def _dp(tmp_path, rid):
    return dict(next(r for r in radios.load(Paths(runtime_root=tmp_path)).radios if r.id == rid).dp)


def test_a_save_is_judged_on_the_list_its_lock_reads(box, monkeypatch):
    # B1: r2's high power is revoked by another writer that held the list's lock first; POWER=20 needs it
    post, tmp_path = box
    real = cfgmod.config_lock

    @contextlib.contextmanager
    def after_another_writer(paths, *a, **k):
        with real(paths, *a, **k):
            radios_fixture.write(tmp_path, _rows(False))
            yield
    monkeypatch.setattr(cfgmod, "config_lock", after_another_writer)
    post(radio="r2", dp_POWER="20")
    assert _dp(tmp_path, "r2") == {}


def test_a_radio_keeps_its_source_default_over_the_bands_value(box):
    # B2: the band saves SF 9; r2 set to the source default (12) keeps 12, never the band's 9
    post, tmp_path = box
    post(dp_SF="9")
    post(radio="r2", dp_SF="12")
    assert _dp(tmp_path, "r2") == {"SF": "12"}


def test_a_value_posted_as_shown_stays_inherited(box):
    # note 2: r2 shows the band's FREQ and the operator changes only SF; FREQ stays the band's, so a later band
    # change reaches r2
    post, tmp_path = box
    post(dp_FREQ="433.600")
    post(radio="r2", dp_FREQ="433.600", dp_SF="10")
    assert _dp(tmp_path, "r2") == {"SF": "10"}
