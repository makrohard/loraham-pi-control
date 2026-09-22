r"""The repeater roles: upstream openhop_repeater's RepeaterDaemon on the LoRaHAM radio adapter.

    LoRaHAM Daemon -> LoRaHAMRadio -> RepeaterDaemon (dispatcher, router, policy, dashboard)
                                        \-> CompanionBridge -> CompanionFrameServer -> TCP 5000
                                            (chat+repeater only: the SAME chat node as the chat role)

LHPC's TOML is the ONLY configuration: this module translates it into the in-memory dict upstream
expects and injects the radio. Upstream gets no config path (`config_path = None`): for a save it
then falls back to /etc/openhop_repeater/config.yaml (or $OPENHOP_REPEATER_CONFIG, which this
process clears), a path the rootless unit cannot create — and every dashboard route that would
save is denied at LHPC's proxy anyway. Storage, GPS, MQTT and Glass are pinned by LHPC below; the
dashboard binds loopback.

Exit codes mirror `__main__`: 2 unusable configuration, 3 identity unusable, 1 anything else.
"""

from __future__ import annotations

import os

import asyncio
import logging
from typing import Optional

from .config import DASHBOARD_PORT, HostConfig, ConfigError
from .gps_feed import GpsFeed
from .identity import IdentityError, load_identity_hex
from .loraham_radio import LoRaHAMRadio
from .plugin_manager import KILL_GRACE_S, TERM_GRACE_S, PluginManagerChild

logger = logging.getLogger("meshcore-host.repeater")

EXIT_CONFIG = 2
EXIT_IDENTITY = 3
# Bounds on the cleanup this host owns, applied BEFORE upstream's exit watchdog is armed (see
# `_Host.run`): deferring that watchdog must never turn its bounded shutdown into an open hang.
CLEANUP_STEP_S = 10.0

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
            rf_log_path=cfg.rf_log_path if cfg.rf_log else "",
        )
        self.gps: Optional[GpsFeed] = None
        # The plugin manager is part of the repeater: spawned by this host in the repeater roles
        # (docs/stacks/meshcore.md, "Plugins"). Built here so a missing upstream module surfaces
        # as the same ImportError -> EXIT_CONFIG as a missing repeater (integration failure).
        self.plugins: Optional[PluginManagerChild] = (
            PluginManagerChild(cfg.repeater_state_dir) if cfg.plugins_on else None)
        self.daemon = self._make_daemon()

    def _make_daemon(self):
        from repeater.main import RepeaterDaemon           # upstream, pinned in the stack venv
        host = self

        class LhpcRepeaterDaemon(RepeaterDaemon):
            """Upstream's daemon plus the things only LHPC knows: the companion's position
            policy and its LHPC-owned name, applied AFTER upstream has built the bridge — and
            the moment its exit watchdog may arm."""
            lhpc_watchdog_deferred = False

            async def initialize(self):
                await super().initialize()
                host._after_initialize(self)

            def _arm_exit_watchdog(self):
                # Upstream arms a 5 s `os._exit(0)` timer at the END of its own `_shutdown()`,
                # before `run()` returns to this host — which still has the plugin manager
                # (8 s + 2 s), the GPS feed and the radio to close. Record the intent; the host
                # arms the ORIGINAL watchdog once its bounded cleanup is done (`_Host.run`).
                self.lhpc_watchdog_deferred = True

        daemon = LhpcRepeaterDaemon(self.conf, radio=self.radio)
        # EXPLICIT: with the attribute missing OR None upstream falls back to
        # /etc/openhop_repeater/config.yaml (after $OPENHOP_REPEATER_CONFIG / $PYMC_REPEATER_CONFIG,
        # cleared here so an inherited environment cannot point a save at a writable file). The
        # rootless unit cannot create that directory, so a save fails — and the proxy denies every
        # route that would try. LHPC's TOML stays the only configuration.
        for var in ("OPENHOP_REPEATER_CONFIG", "PYMC_REPEATER_CONFIG"):
            os.environ.pop(var, None)
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

    async def _bounded(self, name: str, coro, timeout: float = CLEANUP_STEP_S) -> None:
        """One cleanup step, bounded and logged: a hang or an error in it must not stop the
        next step, and must never delay the watchdog forever."""
        try:
            await asyncio.wait_for(coro, timeout)
        except asyncio.TimeoutError:
            logger.error("%s did not finish within %.0f s; continuing shutdown", name, timeout)
        except Exception:
            logger.exception("%s failed; continuing shutdown", name)

    async def run(self) -> None:
        logger.info("Starting openHop repeater host: role=%s repeater=%s companion=%s plugins=%s",
                    self.cfg.mode, self.cfg.repeater_name,
                    self.cfg.name if self.cfg.companion_on else "-",
                    "on" if self.plugins is not None else "off")
        # Upstream begins the radios IT builds; an injected one is ours to begin (sync: it
        # schedules the daemon connection manager) and ours to close (async aclose — upstream
        # only calls a sync `cleanup()` when a radio has one).
        self.radio.begin()
        watch: Optional[asyncio.Task] = None
        try:
            try:
                # Manager first, like upstream's container entry; a spawn that fails leaves the
                # repeater running with the dashboard's "plugin manager unavailable" banner.
                if self.plugins is not None and self.plugins.start():
                    watch = asyncio.get_running_loop().create_task(
                        self.plugins.watch(), name="plugin-manager watch")
                await self.daemon.run()                    # installs SIGTERM/SIGINT handlers
            finally:
                # ORDER (locked by a test): plugin manager -> GPS feed -> radio -> the deferred
                # upstream watchdog. Every step bounded; the watchdog armed in the OUTER finally.
                if watch is not None:
                    watch.cancel()
                    try:
                        await watch
                    except (asyncio.CancelledError, Exception):
                        pass
                if self.plugins is not None:
                    # blocking by design (SIGTERM, 8 s, SIGKILL, 2 s): bounded by construction,
                    # so its step bound is that worst case plus a second, never less
                    await self._bounded("plugin manager stop",
                                        asyncio.to_thread(self.plugins.stop),
                                        TERM_GRACE_S + KILL_GRACE_S + 1.0)
                if self.gps is not None:
                    await self._bounded("GPS feed stop", self.gps.stop())
                await self._bounded("radio close", self.radio.aclose())
                logger.info("openHop repeater host stopped")
        finally:
            if getattr(self.daemon, "lhpc_watchdog_deferred", False):
                from repeater.main import RepeaterDaemon   # the ORIGINAL, unchanged 5 s guard
                RepeaterDaemon._arm_exit_watchdog(self.daemon)


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
