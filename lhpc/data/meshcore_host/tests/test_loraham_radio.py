"""LoRaHAMRadio adapter tests against the scriptable fake daemon.

Covers the full Phase-1 contract: start/readiness, config negotiation, RX injection
with RSSI/SNR, TX capture and every TX_RESULT shape (ok / failed / delayed / malformed
/ missing), socket loss and daemon restart, shutdown during blocked reads,
cancellation, and bounded buffering.
"""

import asyncio

import pytest

from fake_loraham_daemon import (
    TX_RESULT_FLAG_CAD_TIMEOUT,
    TX_RESULT_STATUS_CHANNEL_BUSY,
    TX_RESULT_STATUS_INVALID_PACKET,
    TX_RESULT_STATUS_RADIO_ERROR,
    FakeLoRaHAMDaemon,
)
from meshcore_host.loraham_radio import RX_QUEUE_MAX, LoRaHAMRadio


def make_radio(daemon, **overrides):
    kwargs = dict(
        data_socket=str(daemon.data_socket),
        config_socket=str(daemon.config_socket),
        frequency=869618000,
        bandwidth=62500,
        spreading_factor=8,
        coding_rate=8,
        txpower=14,
        preamble=16,
        enable_tx=True,
        connect_timeout=1.0,
        reconnect_delay=0.2,
        tx_result_margin=0.5,
        noise_poll_interval=0.05,
        resolve_sockets=False,
    )
    kwargs.update(overrides)
    return LoRaHAMRadio(**kwargs)


async def wait_for(predicate, timeout=2.0, interval=0.01):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(interval)
    return False


@pytest.fixture
async def daemon(tmp_path):
    d = FakeLoRaHAMDaemon(tmp_path, tx=True)
    await d.start()
    yield d
    await d.close()


@pytest.fixture
async def radio(daemon):
    r = make_radio(daemon)
    r.begin()
    assert await wait_for(lambda: r.connected and r.tx_ready)
    yield r
    await r.aclose()


# ---------------------------------------------------------------------------
# Startup / configuration
# ---------------------------------------------------------------------------

async def test_connects_and_becomes_tx_ready(daemon, radio):
    assert radio.connected
    assert radio.tx_ready
    joined = "\n".join(daemon.config_commands)
    assert "SET MODE=LORA" in joined
    assert "FREQ=869.618" in joined
    assert "SF=8" in joined
    assert "BW=62.5" in joined
    assert "SYNC=0x12" in joined
    assert "SET TXMODE=MANAGED" in daemon.config_commands
    assert "SET TXRESULT=1" in daemon.config_commands
    assert "GET STATUS" in daemon.config_commands
    # CADWAIT learned from the handshake.
    assert radio._cadwait_s == pytest.approx(1.5)


async def test_rx_only_mode_skips_tx_setup(daemon):
    r = make_radio(daemon, enable_tx=False)
    r.begin()
    assert await wait_for(lambda: r.connected)
    assert not r.tx_ready
    assert "SET TXMODE=MANAGED" not in daemon.config_commands
    assert await r.send(b"data") is None
    assert daemon.tx_packets == []
    await r.aclose()


async def test_tx_inhibited_without_status_reply(tmp_path):
    d = FakeLoRaHAMDaemon(tmp_path, tx=True, respond_to_status=False)
    await d.start()
    r = make_radio(d, connect_timeout=0.3)
    r.begin()
    assert await wait_for(lambda: r.connected)
    assert not r.tx_ready
    assert await r.send(b"data") is None
    assert d.tx_packets == []
    await r.aclose()
    await d.close()


async def test_tx_inhibited_when_daemon_not_managed(tmp_path):
    d = FakeLoRaHAMDaemon(tmp_path, tx=True, txmode="DIRECT")
    await d.start()
    r = make_radio(d)
    r.begin()
    assert await wait_for(lambda: r.connected)
    await asyncio.sleep(0.05)
    assert not r.tx_ready
    assert await r.send(b"data") is None
    await r.aclose()
    await d.close()


async def test_begin_is_idempotent(daemon, radio):
    task = radio._manager_task
    radio.begin()
    assert radio._manager_task is task


async def test_invalid_config_rejected(daemon):
    with pytest.raises(ValueError):
        make_radio(daemon, bandwidth=123456)
    with pytest.raises(ValueError):
        make_radio(daemon, spreading_factor=13)
    with pytest.raises(ValueError):
        make_radio(daemon, txpower=25)
    with pytest.raises(ValueError):
        make_radio(daemon, txmaxpower=5)  # below txpower
    with pytest.raises(ValueError):
        make_radio(daemon, syncword=0x1FF)


# ---------------------------------------------------------------------------
# RX path
# ---------------------------------------------------------------------------

async def test_rx_callback_with_signal_metadata(daemon, radio):
    got = []
    radio.set_rx_callback(lambda data, rssi, snr: got.append((data, rssi, snr)))
    await daemon.send_rx(b"\x01\x02\x03", rssi_cdbm=-9150, snr_cdb=525)
    assert await wait_for(lambda: got)
    data, rssi, snr = got[0]
    assert data == b"\x01\x02\x03"
    assert rssi == -92  # -91.5 rounded
    assert snr == pytest.approx(5.25)
    assert radio.get_last_rssi() == -92  # same rounding as the callback value
    assert radio.get_last_snr() == pytest.approx(5.25)


async def test_rx_signal_unavailable_sentinel(daemon, radio):
    got = []
    radio.set_rx_callback(lambda data, rssi, snr: got.append((data, rssi, snr)))
    await daemon.send_rx(b"pkt", rssi_cdbm=-32768, snr_cdb=-32768)
    assert await wait_for(lambda: got)
    assert got[0] == (b"pkt", 0, 0.0)


async def test_wait_for_rx_fallback(daemon, radio):
    await daemon.send_rx(b"fallback", rssi_cdbm=-8000, snr_cdb=100)
    data = await asyncio.wait_for(radio.wait_for_rx(), timeout=2.0)
    assert data == b"fallback"
    assert radio.get_last_rssi() == -80


async def test_rx_queue_is_bounded(daemon, radio):
    # No callback registered: frames land in the fallback queue, which must cap
    # at RX_QUEUE_MAX and drop the oldest.
    for i in range(RX_QUEUE_MAX + 10):
        await daemon.send_rx(bytes([i & 0xFF]) + b"x")
    assert await wait_for(lambda: len(radio._rx_queue) == RX_QUEUE_MAX)
    first = await asyncio.wait_for(radio.wait_for_rx(), timeout=1.0)
    assert first[0] == 10  # 0..9 dropped


async def test_short_and_empty_rx_frames_ignored(daemon, radio):
    got = []
    radio.set_rx_callback(lambda data, rssi, snr: got.append(data))
    # Metadata-only frame (no RF payload) and short frame: both ignored.
    await daemon.send_rx(b"")
    await daemon.send_raw(bytes([0x01, 2, 0, 0xAA, 0xBB]))
    await daemon.send_rx(b"good")
    assert await wait_for(lambda: got)
    assert got == [b"good"]


# ---------------------------------------------------------------------------
# TX path
# ---------------------------------------------------------------------------

async def test_send_success_returns_metadata(daemon, radio):
    meta = await radio.send(b"\xaa" * 30)
    assert meta is not None
    assert meta["airtime_ms"] > 0
    assert daemon.tx_packets == [b"\xaa" * 30]


async def test_send_cad_timeout_flag_still_success(daemon, radio):
    daemon.set_tx_result(flags=0x01 | TX_RESULT_FLAG_CAD_TIMEOUT)
    meta = await radio.send(b"pkt")
    assert meta is not None
    assert meta["tx_flags"] & TX_RESULT_FLAG_CAD_TIMEOUT


async def test_send_channel_busy_fails(daemon, radio):
    daemon.set_tx_result(status=TX_RESULT_STATUS_CHANNEL_BUSY)
    assert await radio.send(b"pkt") is None
    # Connection stays usable; a later OK send succeeds.
    daemon.set_tx_result(status=0)
    assert await radio.send(b"pkt2") is not None


async def test_send_radio_error_fails(daemon, radio):
    daemon.set_tx_result(status=TX_RESULT_STATUS_RADIO_ERROR)
    assert await radio.send(b"pkt") is None


async def test_send_invalid_packet_fails(daemon, radio):
    daemon.set_tx_result(status=TX_RESULT_STATUS_INVALID_PACKET)
    assert await radio.send(b"pkt") is None


async def test_send_delayed_result_within_timeout(daemon, radio):
    daemon.tx_result_delay = 0.5
    meta = await radio.send(b"delayed")
    assert meta is not None


async def test_send_result_timeout_reconnects(daemon, radio):
    daemon.set_tx_result(respond=False)
    assert await radio.send(b"lost") is None
    assert not radio.tx_ready
    # The adapter reconnects on its own and becomes TX-ready again.
    daemon.set_tx_result(respond=True)
    assert await wait_for(lambda: radio.tx_ready, timeout=5.0)
    assert await radio.send(b"after") is not None


async def test_malformed_tx_result_ignored_then_timeout(daemon, radio):
    daemon.set_tx_result(payload_len=3)
    assert await radio.send(b"pkt") is None
    daemon.set_tx_result(payload_len=4)
    assert await wait_for(lambda: radio.tx_ready, timeout=5.0)


async def test_error_frame_aborts_pending_tx(daemon, radio):
    daemon.set_tx_result(respond=False)

    async def fire_error():
        await daemon.wait_tx(b"pkt", timeout=2.0)
        await daemon.send_error("TX rejected")

    err_task = asyncio.create_task(fire_error())
    assert await radio.send(b"pkt") is None
    await err_task


async def test_oversized_payload_rejected_locally(daemon, radio):
    assert await radio.send(b"x" * 256) is None
    assert await radio.send(b"") is None
    assert daemon.tx_packets == []


async def test_send_cancellation_clears_slot_and_reconnects(daemon, radio):
    daemon.set_tx_result(respond=False)
    task = asyncio.create_task(radio.send(b"cancelme"))
    await daemon.wait_tx(b"cancelme", timeout=2.0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert radio._pending_tx_result is None
    daemon.set_tx_result(respond=True)
    assert await wait_for(lambda: radio.tx_ready, timeout=5.0)
    assert await radio.send(b"next") is not None


async def test_concurrent_sends_serialised(daemon, radio):
    daemon.tx_result_delay = 0.1
    results = await asyncio.gather(radio.send(b"one"), radio.send(b"two"))
    assert all(m is not None for m in results)
    assert daemon.tx_packets == [b"one", b"two"]


# ---------------------------------------------------------------------------
# Connection resilience
# ---------------------------------------------------------------------------

async def test_socket_loss_reconnects_and_rx_resumes(daemon, radio):
    got = []
    radio.set_rx_callback(lambda data, rssi, snr: got.append(data))
    await daemon.drop_data_connection()
    assert await wait_for(lambda: not radio.connected, timeout=2.0)
    assert await wait_for(lambda: radio.connected and radio.tx_ready, timeout=5.0)
    await daemon.send_rx(b"back")
    assert await wait_for(lambda: got == [b"back"])


async def test_daemon_restart_reconnects(daemon, radio, tmp_path):
    await daemon.close()
    assert await wait_for(lambda: not radio.connected, timeout=2.0)
    # Same socket paths, fresh daemon process.
    await daemon.start()
    assert await wait_for(lambda: radio.connected and radio.tx_ready, timeout=5.0)
    assert await radio.send(b"revived") is not None


async def test_unknown_frame_type_forces_clean_reconnect(daemon, radio):
    got = []
    radio.set_rx_callback(lambda data, rssi, snr: got.append(data))
    await daemon.send_raw(bytes([0x7F, 1, 0, 0x00]))
    assert await wait_for(lambda: not radio.connected, timeout=2.0)
    assert await wait_for(lambda: radio.connected and radio.tx_ready, timeout=5.0)
    await daemon.send_rx(b"clean")
    assert await wait_for(lambda: got == [b"clean"])


async def test_send_while_disconnected_fails_cleanly(daemon):
    r = make_radio(daemon)
    # Not begun: no connection at all.
    assert await r.send(b"pkt") is None
    await r.aclose()


async def test_missing_socket_keeps_retrying_then_connects(tmp_path):
    d = FakeLoRaHAMDaemon(tmp_path, tx=True)
    r = make_radio(d, connect_timeout=0.3)
    r.begin()  # daemon not started yet
    await asyncio.sleep(0.5)
    assert not r.connected
    await d.start()
    assert await wait_for(lambda: r.connected and r.tx_ready, timeout=5.0)
    await r.aclose()
    await d.close()


# ---------------------------------------------------------------------------
# Shutdown
# ---------------------------------------------------------------------------

async def test_aclose_during_blocked_read(daemon, radio):
    # Reader loops are blocked awaiting daemon data; aclose must return promptly.
    await asyncio.wait_for(radio.aclose(), timeout=2.0)
    assert not radio.connected
    assert radio._manager_task is None


async def test_aclose_is_idempotent(daemon, radio):
    await radio.aclose()
    await radio.aclose()


async def test_aclose_releases_pending_send(daemon, radio):
    daemon.set_tx_result(respond=False)
    task = asyncio.create_task(radio.send(b"stuck"))
    await daemon.wait_tx(b"stuck", timeout=2.0)
    await radio.aclose()
    result = await asyncio.wait_for(task, timeout=2.0)
    assert result is None


# ---------------------------------------------------------------------------
# Health / duty cycle / misc
# ---------------------------------------------------------------------------

async def test_check_radio_health(daemon, radio):
    assert radio.check_radio_health() is True
    await radio.aclose()
    assert radio.check_radio_health() is False


async def test_link_state_observer(daemon):
    states = []
    r = make_radio(daemon)
    r.on_link_state = lambda c, t: states.append((c, t))
    r.begin()
    assert await wait_for(lambda: (True, True) in states)
    await r.aclose()
    assert states[-1] == (False, False)


async def test_duty_cycle_pacing_state(daemon, radio):
    assert radio.transmit_wait() == 0.0
    meta = await radio.send(b"x" * 50)
    assert meta is not None
    # One packet cannot exceed the duty budget over its own period alone;
    # the accounting must at least have recorded it.
    assert sum(radio._airtime_txtime) > 0


async def test_get_radioconfig(daemon, radio):
    assert radio.get_radioconfig() == (869618, 62500, 8, 8, 14, 14)


async def test_socket_paths_re_resolved_each_connect(tmp_path):
    """The daemon can restart under a different socket namespace while the
    adapter runs; every connect attempt must re-resolve the configured path."""
    import meshcore_host.loraham_radio as mod
    d = FakeLoRaHAMDaemon(tmp_path, tx=True)
    await d.start()
    seen = []
    original = mod.resolve_socket_path

    def tracking(path):
        seen.append(path)
        return original(path)

    mod.resolve_socket_path = tracking
    try:
        r = make_radio(d, resolve_sockets=True, connect_timeout=0.5)
        r.begin()
        assert await wait_for(lambda: r.connected)
        first = len(seen)
        assert first >= 2
        await d.drop_data_connection()
        assert await wait_for(lambda: len(seen) > first, timeout=5.0), \
            "reconnect must re-resolve the socket paths"
        await r.aclose()
    finally:
        mod.resolve_socket_path = original
    await d.close()


async def test_tx_not_ready_recovers_via_rehandshake(tmp_path):
    """A daemon that was not TX-ready at connect time must not leave TX dead
    forever: a failed send forces a re-handshake that observes the recovery."""
    d = FakeLoRaHAMDaemon(tmp_path, tx=True, txmode="DIRECT")
    await d.start()
    r = make_radio(d)
    r.begin()
    assert await wait_for(lambda: r.connected)
    assert await r.send(b"nope") is None       # not ready; triggers re-handshake
    d.txmode = "MANAGED"                       # daemon recovers
    assert await wait_for(lambda: r.tx_ready, timeout=5.0), \
        "tx_ready must recover without a socket error"
    assert await r.send(b"now") is not None
    await r.aclose()
    await d.close()


# ---------------------------------------------------------------------------
# Noise floor (daemon live-channel RSSI -> openHop get_noise_floor)
# ---------------------------------------------------------------------------


def _radio_for_parsing():
    # A bare adapter (never begun) is enough to exercise the pure parse helper.
    return LoRaHAMRadio(
        data_socket="/nonexistent/data.sock",
        config_socket="/nonexistent/config.sock",
        frequency=869618000, bandwidth=62500, spreading_factor=8,
        coding_rate=8, txpower=14, resolve_sockets=False,
    )


def test_noise_floor_none_before_any_reading():
    assert _radio_for_parsing().get_noise_floor() is None


def test_noise_floor_parses_liverssi_from_channel_line():
    r = _radio_for_parsing()
    r._maybe_update_noise_floor(
        "CHANNEL RADIO=IDLE/RX BUSY=0 CAD=0 CADSCAN=1 CADSTATE=FREE "
        "RSSI=-95.00 PACKETRSSI=-95.00 LIVERSSI=-112.50 MODE=LORA TXMODE=MANAGED"
    )
    assert r.get_noise_floor() == -112.50


def test_noise_floor_falls_back_to_rssi_without_liverssi():
    r = _radio_for_parsing()
    r._maybe_update_noise_floor("CHANNEL RADIO=IDLE/RX BUSY=0 RSSI=-101.00 MODE=LORA")
    assert r.get_noise_floor() == -101.00


def test_noise_floor_ignores_unavailable_sentinel():
    r = _radio_for_parsing()
    r._maybe_update_noise_floor("CHANNEL LIVERSSI=-100.00 RSSI=-100.00")
    assert r.get_noise_floor() == -100.00
    # -200 is the daemon's TX-busy / contended sentinel: keep the last real value.
    r._maybe_update_noise_floor("CHANNEL LIVERSSI=-200.00 RSSI=-200.00")
    assert r.get_noise_floor() == -100.00


def test_noise_floor_ignores_non_channel_and_malformed_lines():
    r = _radio_for_parsing()
    r._maybe_update_noise_floor("STATUS RADIO=READY CADWAIT=1500 TXRESULT=1")
    assert r.get_noise_floor() is None
    r._maybe_update_noise_floor("CHANNEL LIVERSSI=notanumber")
    assert r.get_noise_floor() is None


async def test_noise_floor_populated_from_daemon_poll(daemon, radio):
    # The poller issues GET CHANNEL on connect; the config reader parses the
    # daemon's LIVERSSI into get_noise_floor().
    daemon.live_rssi = -107.0
    assert await wait_for(lambda: radio.get_noise_floor() == -107.0)
    assert "GET CHANNEL" in daemon.config_commands


async def test_refresh_noise_floor_tracks_new_readings(daemon, radio):
    # The poller keeps refreshing: a changed daemon reading is picked up.
    assert await wait_for(lambda: radio.get_noise_floor() is not None)
    daemon.live_rssi = -121.0
    assert await wait_for(lambda: radio.get_noise_floor() == -121.0)


async def test_noise_floor_repopulates_after_reconnect(daemon, radio):
    assert await wait_for(lambda: radio.get_noise_floor() is not None)
    await daemon.drop_data_connection()  # forces a reconnect -> _close_sockets
    # The poller is re-spawned on reconnect and repopulates the reading, proving
    # it is tied to the live connection rather than carried stale across a drop.
    daemon.live_rssi = -118.0
    assert await wait_for(lambda: radio.get_noise_floor() == -118.0, timeout=5.0)
