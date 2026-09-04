r"""The repeater roles: upstream openhop_repeater's RepeaterDaemon on the LoRaHAM radio adapter.

    LoRaHAM Daemon -> LoRaHAMRadio -> RepeaterDaemon (dispatcher, router, policy, dashboard)
                                        \-> CompanionBridge -> CompanionFrameServer -> TCP 5000
                                            (chat+repeater only: the SAME chat node as the chat role)

LHPC's TOML is the ONLY configuration: this module translates it into the in-memory dict upstream
expects and injects the radio. Upstream never gets a config path (`config_path = None`), so nothing
it does — dashboard saves, JWT-secret persistence, node-name sync — can write a file: every save
fails closed. Storage, GPS, MQTT and Glass are pinned by LHPC below; the dashboard binds loopback.

Exit codes mirror `__main__`: 2 unusable configuration, 3 identity unusable, 1 anything else.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

from .config import DASHBOARD_PORT, HostConfig, ConfigError
from .gps_feed import GpsFeed
from .identity import IdentityError, load_identity_hex
from .loraham_radio import LoRaHAMRadio

logger = logging.getLogger("meshcore-host.repeater")

EXIT_CONFIG = 2
EXIT_IDENTITY = 3

# Upstream's default duty budget is 3600 ms per minute (6 %); LHPC's `airtime` is a percentage.
_MS_PER_MINUTE_PER_PERCENT = 600


def seed_bytes(text: str) -> bytes:
    """The identity seed upstream's `LocalIdentity(seed=...)` needs: BYTES (32 or 64), decoded
    from LHPC's hex with the same fail-closed rules as the chat node's key (upstream passes the
    repeater key through undecoded, unlike its companion entries)."""
    load_identity_hex(text)                    # validates length/hex/import; raises IdentityError
    return bytes.fromhex(text.strip().lower())


def radio_status(connected: bool, tx_ready: bool, enable_tx: bool) -> str:
    """Upstream's dashboard radio state from the adapter's link state: "ok" only when the daemon
    link is up and — with TX enabled — the MANAGED-TX handshake is complete; else "degraded"."""
    return "ok" if connected and (tx_ready or not enable_tx) else "degraded"


def build_upstream_config(cfg: HostConfig) -> dict:
    """The in-memory configuration upstream's RepeaterDaemon reads. One source of truth (LHPC's
    TOML), translated: radio parameters in upstream's names and units (Hz, dBm), the duty budget
    from LHPC's percentage, storage under the runtime root, the dashboard on loopback, every
    outbound integration off, and — in chat+repeater — the chat node as the ONE hosted companion
    with today's name, key, bind and port."""
    conf: dict = {
        "repeater": {
            "node_name": cfg.repeater_name,
            "identity_key": seed_bytes(cfg.repeater_key),
            "mode": cfg.repeater_behaviour,
            "security": {"admin_password": cfg.dashboard_password},
        },
        # A non-disabled marker: upstream never builds a radio (ours is injected), but its setup
        # wizard reads `radio_type` and would flag a missing one as "setup needed".
        "radio_type": "loraham",
        "radio": {
            "frequency": cfg.frequency,
            "bandwidth": cfg.bandwidth,
            "spreading_factor": cfg.spreading_factor,
            "coding_rate": cfg.coding_rate,
            "preamble_length": cfg.preamble,
            "tx_power": cfg.txpower,
            "sync_word": cfg.syncword,
        },
        "duty_cycle": {
            "enforcement_enabled": True,
            "max_airtime_per_minute": int(round(cfg.airtime * _MS_PER_MINUTE_PER_PERCENT)),
        },
        "http": {"enabled": True, "host": "127.0.0.1", "port": DASHBOARD_PORT},
        "storage": {"storage_dir": cfg.repeater_state_dir},
        "gps": {"enabled": False, "time_sync_enabled": False},
        "mqtt_brokers": {},
        "glass": {"enabled": False},
        "identities": {"companions": [], "room_servers": []},
    }
    if cfg.companion_on:
        # The hosted companion IS today's chat node: same fail-closed identity rules as HostApp
        # (upstream would merely log and skip a bad companion key, leaving no companion at all).
        if cfg.key_file:
            raise ConfigError("[identity] key_file is not supported in the repeater roles — "
                              "LHPC injects the key inline")
        load_identity_hex(cfg.key)                   # raises IdentityError, never limps
        conf["identities"]["companions"] = [{
            "name": cfg.name,
            "identity_key": cfg.key,                 # hex: upstream decodes companion keys itself
            "settings": {
                "node_name": cfg.name,
                "bind_address": cfg.bind,
                "tcp_port": cfg.port,
                "tcp_timeout": 0,                    # firmware behaviour: no idle disconnect
            },
        }]
    return conf


class _Host:
    """One repeater run: the injected radio, the daemon, the optional GPS feed on the hosted
    companion, and a clean shutdown of what upstream does not own."""

    def __init__(self, cfg: HostConfig):
        self.cfg = cfg
        self.conf = build_upstream_config(cfg)
        self.radio = LoRaHAMRadio(
            data_socket=cfg.data_socket, config_socket=cfg.config_socket,
            frequency=cfg.frequency, bandwidth=cfg.bandwidth,
            spreading_factor=cfg.spreading_factor, coding_rate=cfg.coding_rate,
            txpower=cfg.txpower, txmaxpower=cfg.txmaxpower, crc=cfg.crc,
            preamble=cfg.preamble, syncword=cfg.syncword, ldro=cfg.ldro,
            enable_tx=cfg.enable_tx, airtime_dutycycle=cfg.airtime,
        )
        self.gps: Optional[GpsFeed] = None
        self.daemon = self._make_daemon()

    def _make_daemon(self):
        from repeater.main import RepeaterDaemon           # upstream, pinned in the stack venv
        host = self

        class LhpcRepeaterDaemon(RepeaterDaemon):
            """Upstream's daemon plus the two things only LHPC knows: the companion's position
            policy and its LHPC-owned name, applied AFTER upstream has built the bridge."""

            async def initialize(self):
                await super().initialize()
                host._after_initialize(self)

        daemon = LhpcRepeaterDaemon(self.conf, radio=self.radio)
        # EXPLICIT: upstream falls back to /etc/openhop_repeater/config.yaml when the attribute is
        # missing. None makes every save (dashboard, JWT secret, node-name sync) fail closed.
        daemon.config_path = None
        daemon.radio_status = "degraded"                   # until the link reports otherwise
        self.radio.on_link_state = lambda connected, tx_ready: setattr(
            daemon, "radio_status", radio_status(connected, tx_ready, self.cfg.enable_tx))
        return daemon

    def _after_initialize(self, daemon) -> None:
        bridges = list(getattr(daemon, "companion_bridges", {}).values())
        if not self.cfg.companion_on:
            if bridges:
                logger.warning("repeater role hosts no companion, yet %d were created", len(bridges))
            return
        if len(bridges) != 1:
            raise ConfigError(f"expected exactly one hosted companion, upstream created {len(bridges)}")
        bridge = bridges[0]
        bridge.prefs.node_name = self.cfg.name              # LHPC-owned, reasserted
        if self.cfg.gps_mode == "fixed":
            bridge.prefs.latitude = self.cfg.gps_lat
            bridge.prefs.longitude = self.cfg.gps_lon
            bridge.prefs.advert_loc_policy = 1              # ADVERT_LOC_SHARE
        elif self.cfg.gps_mode == "feed":
            self.gps = GpsFeed(bridge, socket_path=self.cfg.gps_socket,
                               stale_after_s=self.cfg.gps_stale_s)
            self.gps.start()
        else:
            bridge.prefs.latitude = 0.0
            bridge.prefs.longitude = 0.0
            bridge.prefs.advert_loc_policy = 0              # ADVERT_LOC_NONE

    async def run(self) -> None:
        logger.info("Starting openHop repeater host: role=%s repeater=%s companion=%s",
                    self.cfg.mode, self.cfg.repeater_name,
                    self.cfg.name if self.cfg.companion_on else "-")
        # Upstream begins the radios IT builds; an injected one is ours to begin (sync: it
        # schedules the daemon connection manager) and ours to close (async aclose — upstream
        # only calls a sync `cleanup()` when a radio has one).
        self.radio.begin()
        try:
            await self.daemon.run()                        # installs SIGTERM/SIGINT handlers
        finally:
            if self.gps is not None:
                try:
                    await self.gps.stop()
                except Exception:
                    logger.exception("GPS feed stop failed")
            await self.radio.aclose()
            logger.info("openHop repeater host stopped")


def run_repeater(cfg: HostConfig) -> int:
    try:
        host = _Host(cfg)
    except IdentityError as exc:
        logger.error("Identity error: %s", exc)
        return EXIT_IDENTITY
    except (ConfigError, ValueError) as exc:
        logger.error("Configuration error: %s", exc)
        return EXIT_CONFIG
    except ImportError as exc:
        logger.error("openHop repeater is not installed in this venv: %s", exc)
        return EXIT_CONFIG
    try:
        asyncio.run(host.run())
    except KeyboardInterrupt:
        return 0
    except IdentityError as exc:                    # raised inside initialize(): same contract
        logger.error("Identity error: %s", exc)
        return EXIT_IDENTITY
    except (ConfigError, ValueError) as exc:
        logger.error("Configuration error: %s", exc)
        return EXIT_CONFIG
    except Exception as exc:
        logger.error("Fatal: %s", exc, exc_info=True)
        return 1
    return 0


__all__ = ["build_upstream_config", "radio_status", "run_repeater", "seed_bytes"]
