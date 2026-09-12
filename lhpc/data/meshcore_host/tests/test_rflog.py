"""RF log: the line contract and retention, the two [radio] keys, and the hooks in
LoRaHAMRadio — RX ahead of the callback/queue, TX with the daemon's TX_RESULT as
the outcome and `unconfirmed` wherever a written frame's result was never learned."""

import asyncio
import os
import textwrap

import pytest

from fake_loraham_daemon import TX_RESULT_STATUS_CHANNEL_BUSY, FakeLoRaHAMDaemon
from meshcore_host.config import ConfigError, load_config
from meshcore_host.loraham_radio import LoRaHAMRadio
from meshcore_host.rflog import RfLog, format_rx, format_tx, parse_switch
from test_loraham_radio import make_radio, wait_for

# ---- lines -------------------------------------------------------------------

def test_rx_line_carries_signal_and_both_payload_views():
    assert format_rx("2026-09-12T16:03:47.412Z", -104.5, 7.25, b'A"\\\x7f\x00') == (
        '2026-09-12T16:03:47.412Z RX rssi=-104.50 snr=7.25 len=5'
        ' hex=41225c7f00 ascii="A...."\n')


def test_tx_line_carries_an_outcome_and_no_signal():
    assert format_tx("2026-09-12T16:03:51.006Z", "unconfirmed", b"hi") == (
        '2026-09-12T16:03:51.006Z TX rssi=- snr=- len=2 outcome=unconfirmed'
        ' hex=6869 ascii="hi"\n')


@pytest.mark.parametrize("value,want", [(True, True), (False, False), ("on", True), ("Off", False),
                                        ("yes", True), ("no", False), ("1", True), ("0", False)])
def test_switch_accepts_a_toml_bool_and_the_lhpc_enum(value, want):
    assert parse_switch(value) is want


@pytest.mark.parametrize("value", ["", "maybe", None, "2"])
def test_switch_refuses_anything_else(value):
    with pytest.raises(ValueError):
        parse_switch(value)


# ---- file --------------------------------------------------------------------

def _lines(path):
    with open(path, "rb") as f:
        return f.read().count(b"\n")


def test_relative_and_empty_paths_are_refused():
    log = RfLog()
    for bad in ("logs/rf.log", "", None):
        with pytest.raises(ValueError):
            log.open(bad)
    assert not log.active
    log.rx(0, 0, b"x")                          # silent no-op while closed


def test_rollover_copies_to_previous_and_keeps_the_inode(tmp_path):
    path = tmp_path / "rf-meshcore.log"
    log = RfLog(max_bytes=2000)
    log.open(str(path))
    inode = os.stat(path).st_ino
    for _ in range(30):                         # ~100 B per line: crosses 2000 once
        log.rx(-90.0, 5.0, bytes(range(16)))
    prev = tmp_path / "rf-meshcore.log.1"
    assert prev.stat().st_size > 0
    assert os.stat(path).st_ino == inode        # truncated in place, not renamed
    assert path.stat().st_size < 2000
    assert _lines(prev) + _lines(path) == 30    # nothing lost

    with open(path, "r+b") as f:                # the controller's Clear
        f.truncate(0)
    log.tx("ok", b"\x01\x02")
    assert _lines(path) == 1
    assert os.stat(path).st_ino == inode
    log.close()
    assert not log.active


# ---- [radio] keys ---------------------------------------------------------------

def _config(tmp_path, radio_extra):
    p = tmp_path / "meshcore.toml"
    p.write_text(textwrap.dedent(f"""
        [companion]
        name = "pyMC"
        [identity]
        key = "00"
        [radio]
        preset = "eu_uk_narrow"
        {radio_extra}
    """))
    return p


def test_the_switch_and_path_are_read_from_the_radio_table(tmp_path):
    cfg = load_config(_config(tmp_path, 'rf_log = "on"\nrf_log_path = "/var/log/rf.log"'))
    assert cfg.rf_log is True and cfg.rf_log_path == "/var/log/rf.log"
    cfg = load_config(_config(tmp_path, 'rf_log = true\nrf_log_path = "/var/log/rf.log"'))
    assert cfg.rf_log is True
    cfg = load_config(_config(tmp_path, ""))
    assert cfg.rf_log is False and cfg.rf_log_path == ""


@pytest.mark.parametrize("extra,match", [
    ('rf_log = "on"', "needs rf_log_path"),
    ('rf_log = "on"\nrf_log_path = "rf.log"', "absolute"),
    ('rf_log = "maybe"', "on or off"),
    ('rf_log = 1', "wrong type"),
])
def test_a_bad_switch_is_a_config_error_not_a_silent_off(tmp_path, extra, match):
    with pytest.raises(ConfigError, match=match):
        load_config(_config(tmp_path, extra))


# ---- the hooks in LoRaHAMRadio --------------------------------------------------

@pytest.fixture
async def daemon(tmp_path):
    d = FakeLoRaHAMDaemon(tmp_path, tx=True)
    await d.start()
    yield d
    await d.close()


@pytest.fixture
async def logged(daemon, tmp_path):
    path = tmp_path / "rf-meshcore.log"
    r = make_radio(daemon, rf_log_path=str(path))
    r.begin()
    assert await wait_for(lambda: r.connected and r.tx_ready)
    yield r, path
    await r.aclose()


def _read(path):
    return path.read_text().splitlines() if path.exists() else []


async def test_rx_is_logged_ahead_of_the_callback(logged, daemon):
    radio, path = logged
    got = []
    radio.set_rx_callback(lambda data, rssi, snr: got.append(data))
    await daemon.send_rx(b"\xaa\x42\x0a", rssi_cdbm=-9150, snr_cdb=275)
    assert await wait_for(lambda: got)
    lines = _read(path)
    assert len(lines) == 1
    assert lines[0].endswith(' RX rssi=-91.50 snr=2.75 len=3 hex=aa420a ascii=".B."')


async def test_rx_is_logged_with_no_callback_too(logged, daemon):
    radio, path = logged
    await daemon.send_rx(b"q")
    assert await wait_for(lambda: _read(path))
    assert await radio.wait_for_rx() == b"q"


async def test_tx_ok_comes_from_the_result_not_the_write(logged, daemon):
    radio, path = logged
    assert await radio.send(b"hi") is not None
    lines = _read(path)
    assert len(lines) == 1
    assert lines[0].endswith(' TX rssi=- snr=- len=2 outcome=ok hex=6869 ascii="hi"')

    daemon.set_tx_result(status=TX_RESULT_STATUS_CHANNEL_BUSY)
    assert await radio.send(b"no") is None      # the radio did not send it
    assert len(_read(path)) == 1


async def test_a_lost_result_is_unconfirmed(daemon, tmp_path):
    path = tmp_path / "rf-meshcore.log"
    radio = make_radio(daemon, rf_log_path=str(path), tx_result_margin=0.2)
    radio.begin()
    assert await wait_for(lambda: radio.connected and radio.tx_ready)
    daemon.set_tx_result(respond=False)
    assert await radio.send(b"hi") is None
    lines = _read(path)
    assert len(lines) == 1
    assert " outcome=unconfirmed hex=6869 " in lines[0]
    await radio.aclose()


async def test_a_link_drop_with_the_frame_written_is_unconfirmed(logged, daemon):
    radio, path = logged
    daemon.set_tx_result(respond=False)
    task = asyncio.create_task(radio.send(b"hi"))
    await daemon.wait_tx(b"hi")                 # the daemon owns it now
    await daemon.drop_data_connection()
    assert await task is None
    assert await wait_for(lambda: _read(path))
    assert " outcome=unconfirmed hex=6869 " in _read(path)[0]


async def test_a_daemon_error_frame_is_not_a_transmission(logged, daemon):
    radio, path = logged
    daemon.set_tx_result(respond=False)
    task = asyncio.create_task(radio.send(b"hi"))
    await daemon.wait_tx(b"hi")
    await daemon.send_error("TX rejected")
    assert await task is None
    await asyncio.sleep(0.05)
    assert _read(path) == []


async def test_a_bad_path_fails_the_radio_before_the_daemon_link(daemon):
    with pytest.raises(ValueError, match="absolute"):
        make_radio(daemon, rf_log_path="rf.log")
    with pytest.raises(OSError):
        make_radio(daemon, rf_log_path="/nonexistent-dir-x/rf.log")


async def test_aclose_releases_the_log(daemon, tmp_path):
    radio = make_radio(daemon, rf_log_path=str(tmp_path / "rf.log"))
    assert radio._rflog.active
    await radio.aclose()
    assert not radio._rflog.active


async def test_a_drain_failure_after_the_write_is_unconfirmed(logged, daemon):
    """The bytes are handed to the transport by write(); a drain() that fails afterwards
    cannot prove the daemon never got the frame, so the log must say `unconfirmed`, not
    stay silent."""
    radio, path = logged
    real = radio._data_writer

    class _FailingDrain:
        def write(self, data):
            real.write(data)
        async def drain(self):
            await real.drain()                  # the bytes really go out
            raise ConnectionError("drain failed after the write")
        def is_closing(self):
            return real.is_closing()
        def close(self):
            real.close()
        def __getattr__(self, name):
            return getattr(real, name)
    radio._data_writer = _FailingDrain()
    assert await radio.send(b"hi") is None
    await daemon.wait_tx(b"hi")                 # the frame did reach the daemon
    lines = _read(path)
    assert len(lines) == 1 and " outcome=unconfirmed hex=6869 " in lines[0]


async def test_a_write_that_never_hands_over_bytes_logs_nothing(daemon, tmp_path):
    path = tmp_path / "rf-meshcore.log"
    radio = make_radio(daemon, rf_log_path=str(path))
    radio.begin()
    assert await wait_for(lambda: radio.connected and radio.tx_ready)
    radio._data_writer = None                   # nothing can be written
    assert await radio.send(b"hi") is None
    assert _read(path) == []
    await radio.aclose()
