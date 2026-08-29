"""Host application assembly and lifecycle.

    LoRaHAM Daemon -> LoRaHAMRadio -> CompanionRadio -> CompanionFrameServer -> TCP

Startup failures (config, identity, TCP bind) PROPAGATE — the process exits nonzero
rather than reporting a false healthy state. An unavailable LoRaHAM daemon is the one
deliberate exception: it is a recoverable condition (LHPC orders the daemon first;
restarts happen), so the adapter keeps reconnecting while the Companion stays up.
"""

from __future__ import annotations

import asyncio
import logging
import signal
from typing import Optional

from openhop_core.companion.companion_radio import CompanionRadio
from openhop_core.companion.frame_server.server import CompanionFrameServer

from .config import HostConfig
from .gps_feed import GpsFeed
from .identity import load_identity, load_identity_hex
from .loraham_radio import LoRaHAMRadio
from .persistence import (
    CompanionStore,
    PeriodicFlusher,
    PersistentCompanionRadio,
    PersistentFrameServer,
    install_persistence,
)

logger = logging.getLogger("meshcore-host")


class HostApp:
    def __init__(self, cfg: HostConfig):
        self.cfg = cfg
        # Fail closed before anything else; never mint.
        self.identity = (load_identity_hex(cfg.key)
                         if cfg.key else load_identity(cfg.key_file))
        self.radio = LoRaHAMRadio(
            data_socket=cfg.data_socket,
            config_socket=cfg.config_socket,
            frequency=cfg.frequency,
            bandwidth=cfg.bandwidth,
            spreading_factor=cfg.spreading_factor,
            coding_rate=cfg.coding_rate,
            txpower=cfg.txpower,
            txmaxpower=cfg.txmaxpower,
            crc=cfg.crc,
            preamble=cfg.preamble,
            syncword=cfg.syncword,
            ldro=cfg.ldro,
            enable_tx=cfg.enable_tx,
            airtime_dutycycle=cfg.airtime,
        )
        self.store: Optional[CompanionStore] = (
            CompanionStore(cfg.db) if cfg.db else None
        )
        radio_config = {
            "frequency": cfg.frequency,
            "bandwidth": cfg.bandwidth,
            "spreading_factor": cfg.spreading_factor,
            "coding_rate": cfg.coding_rate,
            "tx_power": cfg.txpower,
        }
        if self.store:
            self.companion = PersistentCompanionRadio(
                radio=self.radio,
                identity=self.identity,
                node_name=cfg.name,
                radio_config=radio_config,
                store=self.store,
            )
        else:
            self.companion = CompanionRadio(
                radio=self.radio,
                identity=self.identity,
                node_name=cfg.name,
                radio_config=radio_config,
            )
        self.flusher: Optional[PeriodicFlusher] = (
            PeriodicFlusher(self.store, self.companion) if self.store else None
        )
        server_cls = PersistentFrameServer if self.store else CompanionFrameServer
        self.server = server_cls(
            self.companion,
            self.identity.get_public_key()[:1].hex(),
            port=cfg.port,
            bind_address=cfg.bind,
            device_model="LHPC-MeshCore",
            # Firmware behaviour: no disconnect on idle. LHPC owns exposure policy.
            client_idle_timeout_sec=None,
        )
        if self.store:
            install_persistence(self.server, self.companion, self.store)
        self.gps: Optional[GpsFeed] = None
        if cfg.gps_mode == "fixed":
            self.companion.prefs.latitude = cfg.gps_lat
            self.companion.prefs.longitude = cfg.gps_lon
            self.companion.prefs.advert_loc_policy = 1  # ADVERT_LOC_SHARE
        elif cfg.gps_mode == "feed":
            self.gps = GpsFeed(
                self.companion,
                socket_path=cfg.gps_socket,
                stale_after_s=cfg.gps_stale_s,
            )
        self._stopped = asyncio.Event()

    @property
    def public_key_hex(self) -> str:
        return self.identity.get_public_key().hex()

    async def start(self) -> None:
        logger.info(
            "Starting MeshCore host: node=%s pubkey=%s port=%d",
            self.cfg.name, self.public_key_hex, self.cfg.port,
        )
        if self.store:
            self.store.open()  # fail closed on a broken database
            await self.store.restore(self.companion)
            self.flusher.start()
        self.radio.begin()
        await self.companion.start()
        if self.gps:
            self.gps.start()
        # TCP bind LAST: LHPC readiness is this endpoint, so it must only exist
        # once the node underneath it actually runs.
        await self.server.start()
        logger.info("MeshCore host ready (companion TCP %s:%d)", self.cfg.bind, self.cfg.port)

    async def stop(self) -> None:
        logger.info("Stopping MeshCore host")
        try:
            await self.server.stop()
        except Exception:
            logger.exception("Frame server stop failed")
        if self.gps:
            try:
                await self.gps.stop()
            except Exception:
                logger.exception("GPS feed stop failed")
        try:
            await self.companion.stop()
        except Exception:
            logger.exception("Companion stop failed")
        await self.radio.aclose()
        if self.store:
            if self.flusher:
                await self.flusher.stop()
            try:
                await self.store.flush(self.companion)
            finally:
                self.store.close()
        self._stopped.set()
        logger.info("MeshCore host stopped")

    async def run_until_signal(self) -> None:
        loop = asyncio.get_running_loop()
        stop_requested = asyncio.Event()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, stop_requested.set)
        try:
            await self.start()
        except Exception:
            # Best-effort teardown of whatever partially started, then propagate.
            try:
                await self.stop()
            except Exception:
                logger.debug("Teardown after failed start also failed", exc_info=True)
            raise
        try:
            await stop_requested.wait()
        finally:
            for sig in (signal.SIGTERM, signal.SIGINT):
                loop.remove_signal_handler(sig)
            await self.stop()
