"""DEC-W21 (the USB stick entries): Add Hardware checks the stick's chip as the picked model's before anything is
written. Through POST /hardware/add: a found chip adds the row; no answer, or a refused check (the stick held, a line
held), writes nothing and says why. One case runs the real Detect on a fake bus (its seams as in the Detect tests)."""
import radios_fixture
import repo_paths

from lhpc.core import radios, usb_access
from lhpc.core.paths import Paths
from lhpc.core.radio_detect import DetectRefused, ch341, run
from lhpc.core.usb_access import Probe

STICKS = repo_paths.REPO / "tests" / "data" / "usb_sysfs" / "meshstick"     # the Meshstick on 1-1.3, no serial
REAL_SCAN = usb_access.scan
SPI = (radios.Radio("r1", "low", "loraham", "433"), radios.Radio("r2", "high", "loraham", "868"))


def _env(web, tmp_path, monkeypatch):
    radios_fixture.write(tmp_path, SPI)
    monkeypatch.setattr(usb_access, "scan", lambda *a, **k: REAL_SCAN(STICKS))
    monkeypatch.setattr(usb_access, "node_access", lambda *a, **k: Probe.PRESENT)
    return web()


def _file(tmp_path):
    return (tmp_path / "config" / "radios.toml").read_bytes()


def _flash(c):
    return " ".join(c.get("/hardware").get_data(as_text=True).split())


def _add(c, csrf):
    return c.post("/hardware/add", data={"_csrf": csrf(c), "model": "meshstick-1262", "port": "1-1.3"})


def test_a_found_chip_adds_the_row(web, tmp_path, monkeypatch, csrf):
    c = _env(web, tmp_path, monkeypatch)
    seen = []
    monkeypatch.setattr(run, "detect_stick", lambda svc, model, port: seen.append((model, port)) or run.Detected(
        "found", f"{model} on port {port}: SX1262 found (SX1261 V2D 2D02)", "sx1262", "SX1261 V2D 2D02"))
    assert _add(c, csrf).status_code in (302, 303)
    assert seen == [("meshstick-1262", "1-1.3")]
    assert [r.id for r in radios.load(Paths(runtime_root=tmp_path)).radios] == ["r1", "r2", "r3"]


def test_no_answer_writes_nothing_and_says_check_the_model(web, tmp_path, monkeypatch, csrf):
    c = _env(web, tmp_path, monkeypatch)
    before = _file(tmp_path)
    monkeypatch.setattr(run, "detect_stick", lambda svc, model, port: run.Detected(
        "not-found", f"{model} on port {port}: no SX1262 answered", "sx1262"))
    _add(c, csrf)
    assert _file(tmp_path) == before
    assert "Not added: meshstick-1262 on port 1-1.3: no SX1262 answered — check the model; nothing was written" \
        in _flash(c)


def test_a_refused_check_writes_nothing_and_names_why(web, tmp_path, monkeypatch, csrf):
    c = _env(web, tmp_path, monkeypatch)
    before = _file(tmp_path)
    monkeypatch.setattr(run, "detect_stick", lambda svc, model, port: run.Detected(
        "refused", f"Detect refused: {model} on port {port}: the stick is in use by another process (its daemon?)"))
    _add(c, csrf)
    assert _file(tmp_path) == before
    assert "Not added: Detect refused: meshstick-1262 on port 1-1.3: the stick is in use" in _flash(c)


def test_the_real_detect_runs_on_the_sticks_own_bus(web, tmp_path, monkeypatch, csrf):
    # Detect itself, on a fake bus: the stick on 1-1.3 is selected by its port (no serial) and answers an SX1262.
    c = _env(web, tmp_path, monkeypatch)
    picked = []

    class _Usb:
        def close(self):
            pass

        def release(self, h):
            pass

    class _Chip:
        def reset(self, level):
            pass

        def busy(self):
            return False

        def transfer(self, out):
            if out[0] == 0x80:
                return bytes([0, 0x22])
            return bytes(4) + b"SX1261 V2D 2D02".ljust(16, b"\0") if out[0] == 0x1D else bytes(len(out))

        def sleep(self, s):
            pass

        def now(self):
            return 0.0

        def close(self):
            pass

    monkeypatch.setattr(run, "libusb", _Usb)
    monkeypatch.setattr(ch341, "select", lambda usb, vid, pid, *, serial="", port=(): picked.append((serial, port)) or "h")
    monkeypatch.setattr(ch341, "Ch341Bus", lambda usb, h, pins: _Chip())
    assert _add(c, csrf).status_code in (302, 303)
    assert picked == [("", (1, 1, 3))]
    assert len(radios.load(Paths(runtime_root=tmp_path)).radios) == 3


def test_a_check_that_raises_on_the_bus_writes_nothing(web, tmp_path, monkeypatch, csrf):
    c = _env(web, tmp_path, monkeypatch)
    before = _file(tmp_path)

    class _Usb:
        def close(self):
            pass

    def held(usb, vid, pid, **kw):
        raise DetectRefused("the stick is in use by another process (its daemon?)")
    monkeypatch.setattr(run, "libusb", _Usb)
    monkeypatch.setattr(ch341, "select", held)
    _add(c, csrf)
    assert _file(tmp_path) == before
