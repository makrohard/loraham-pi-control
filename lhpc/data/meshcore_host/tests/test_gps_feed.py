"""GPS semantics: off / fixed / live feed with stale-clearing and recovery.

    use_gps off  -> no position
    fixed        -> static configured position
    live fix     -> current live position advertised
    stale/no-fix -> old moving position REMOVED, not retained
    recovery     -> position reappears without restarting MeshCore
"""

import asyncio
import contextlib
import json

import pytest
from openhop_core.companion.constants import ADVERT_LOC_NONE, ADVERT_LOC_SHARE

from fake_loraham_daemon import FakeLoRaHAMDaemon
from meshcore_host.app import HostApp
from test_companion_integration import host_config, wait_for, write_identity


class FakeGpsBridge:
    """Serves the meshcore position feed protocol on a Unix socket."""

    def __init__(self, path):
        self.path = str(path)
        self.server = None
        self.writers = []

    async def start(self):
        import os
        with contextlib.suppress(FileNotFoundError):
            os.unlink(self.path)
        self.server = await asyncio.start_unix_server(self._on_client, path=self.path)

    async def close(self):
        for w in self.writers:
            w.close()
            with contextlib.suppress(Exception):
                await w.wait_closed()
        if self.server:
            self.server.close()
            await self.server.wait_closed()
            self.server = None
        self.writers = []

    async def _on_client(self, reader, writer):
        self.writers.append(writer)

    async def send(self, record):
        data = (json.dumps(record) + "\n").encode()
        for w in list(self.writers):
            try:
                w.write(data)
                await w.drain()
            except Exception:
                self.writers.remove(w)


@pytest.fixture
async def daemon(tmp_path):
    d = FakeLoRaHAMDaemon(tmp_path, tx=True)
    await d.start()
    yield d
    await d.close()


@pytest.fixture
async def bridge(tmp_path):
    b = FakeGpsBridge(tmp_path / "gps.sock")
    await b.start()
    yield b
    await b.close()


async def make_app(tmp_path, daemon, **gps):
    key_file, _ = write_identity(tmp_path)
    cfg = host_config(tmp_path, daemon, key_file, **gps)
    app = HostApp(cfg)
    await app.start()
    return app


async def test_gps_off_no_position(tmp_path, daemon):
    app = await make_app(tmp_path, daemon, gps_mode="off")
    try:
        assert app.companion.prefs.advert_loc_policy == ADVERT_LOC_NONE
        assert app.companion.prefs.latitude == 0.0
        assert app.gps is None
    finally:
        await app.stop()


async def test_gps_fixed_static_position(tmp_path, daemon):
    app = await make_app(tmp_path, daemon, gps_mode="fixed",
                         gps_lat=48.137, gps_lon=11.575)
    try:
        assert app.companion.prefs.advert_loc_policy == ADVERT_LOC_SHARE
        assert app.companion.prefs.latitude == pytest.approx(48.137)
        assert app.companion.prefs.longitude == pytest.approx(11.575)
    finally:
        await app.stop()


async def test_live_fix_stale_and_recovery_cycle(tmp_path, daemon, bridge):
    app = await make_app(
        tmp_path, daemon, gps_mode="feed",
        gps_socket=bridge.path, gps_stale_s=0.5,
    )
    try:
        prefs = app.companion.prefs
        # Starts with no position.
        assert prefs.advert_loc_policy == ADVERT_LOC_NONE
        assert await wait_for(lambda: bridge.writers)

        # LIVE: fix arrives -> advertised.
        await bridge.send({"fix": True, "lat": 52.52, "lon": 13.405})
        assert await wait_for(lambda: prefs.advert_loc_policy == ADVERT_LOC_SHARE)
        assert prefs.latitude == pytest.approx(52.52)

        # Position follows updates.
        await bridge.send({"fix": True, "lat": 52.53, "lon": 13.41})
        assert await wait_for(lambda: prefs.latitude == pytest.approx(52.53))

        # STALE: silence beyond stale_after_s -> removed, not retained.
        assert await wait_for(
            lambda: prefs.advert_loc_policy == ADVERT_LOC_NONE, timeout=3.0
        )
        assert prefs.latitude == 0.0
        assert prefs.longitude == 0.0

        # RECOVERY: without any restart, a new fix reappears.
        await bridge.send({"fix": True, "lat": 52.54, "lon": 13.42})
        assert await wait_for(lambda: prefs.advert_loc_policy == ADVERT_LOC_SHARE)
        assert prefs.latitude == pytest.approx(52.54)

        # EXPLICIT no-fix clears immediately.
        await bridge.send({"fix": False})
        assert await wait_for(lambda: prefs.advert_loc_policy == ADVERT_LOC_NONE)
    finally:
        await app.stop()


async def test_malformed_feed_lines_ignored(tmp_path, daemon, bridge):
    app = await make_app(
        tmp_path, daemon, gps_mode="feed",
        gps_socket=bridge.path, gps_stale_s=30.0,
    )
    try:
        prefs = app.companion.prefs
        assert await wait_for(lambda: bridge.writers)
        await bridge.send({"fix": True, "lat": 50.0, "lon": 8.0})
        assert await wait_for(lambda: prefs.advert_loc_policy == ADVERT_LOC_SHARE)

        # Garbage must not clear or alter the position.
        for w in bridge.writers:
            w.write(b"not json at all\n")
            w.write(b'{"fix": true, "lat": "north"}\n')
            w.write(b'{"fix": true, "lat": 999, "lon": 0}\n')
            await w.drain()
        await asyncio.sleep(0.3)
        assert prefs.advert_loc_policy == ADVERT_LOC_SHARE
        assert prefs.latitude == pytest.approx(50.0)
    finally:
        await app.stop()


async def test_missing_feed_socket_keeps_node_up_and_position_clear(tmp_path, daemon):
    app = await make_app(
        tmp_path, daemon, gps_mode="feed",
        gps_socket=str(tmp_path / "absent.sock"), gps_stale_s=0.3,
    )
    try:
        assert await wait_for(lambda: app.radio.connected)
        await asyncio.sleep(0.6)
        assert app.companion.prefs.advert_loc_policy == ADVERT_LOC_NONE
    finally:
        await app.stop()


async def test_bridge_restart_recovers_feed(tmp_path, daemon, bridge):
    app = await make_app(
        tmp_path, daemon, gps_mode="feed",
        gps_socket=bridge.path, gps_stale_s=0.5,
    )
    try:
        prefs = app.companion.prefs
        assert await wait_for(lambda: bridge.writers)
        await bridge.send({"fix": True, "lat": 40.0, "lon": -3.7})
        assert await wait_for(lambda: prefs.advert_loc_policy == ADVERT_LOC_SHARE)

        await bridge.close()
        # Stale timer clears the old position while the bridge is down.
        assert await wait_for(
            lambda: prefs.advert_loc_policy == ADVERT_LOC_NONE, timeout=3.0
        )

        await bridge.start()
        assert await wait_for(lambda: bridge.writers, timeout=5.0)
        await bridge.send({"fix": True, "lat": 40.1, "lon": -3.6})
        assert await wait_for(
            lambda: prefs.advert_loc_policy == ADVERT_LOC_SHARE, timeout=5.0
        )
        assert prefs.latitude == pytest.approx(40.1)
    finally:
        await app.stop()
