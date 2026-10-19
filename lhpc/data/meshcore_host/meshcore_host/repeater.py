r"""The repeater roles: upstream openhop_repeater's RepeaterDaemon on the LoRaHAM radio adapter.

    LoRaHAM Daemon -> LoRaHAMRadio -> RepeaterDaemon (dispatcher, router, policy, dashboard)
                                        \-> CompanionBridge -> CompanionFrameServer -> TCP 5000
                                            (chat+repeater only: the SAME chat node as the chat role)

openHop keeps its own configuration file, `<state_dir>/config.yaml` (openhop_file): every setting
LHPC does not own is saved there the upstream way and takes effect from it. LHPC's TOML owns the
keys in openhop_file.OWNED (radio, http, storage, the Glass cert store, the chat node's entry, GPS
while use_gps is on, time sync, setup_completed, the login secret): at every start this module
reads the file with upstream's loader, puts LHPC's values over those keys, writes the result back
(marker block on top) and injects the radio. The one-store settings (name, behaviour, password,
identity, duty cycle) are read from the file and never overridden. The guards (guards.py) refuse
a change of an LHPC value at upstream's entry points; the dashboard binds loopback.

Exit codes mirror `__main__`: 2 unusable configuration, 3 identity unusable, 1 anything else.
"""

from __future__ import annotations

import os

import asyncio
import logging
from pathlib import Path
from typing import Optional

from . import guards
from . import openhop_file as ofile
from .config import BEHAVIOURS, DASHBOARD_PORT, HostConfig, ConfigError
from .gps_feed import GpsFeed
from .gps_file import NmeaFile
from .identity import IdentityError, load_identity_hex
from .loraham_radio import LoRaHAMRadio
from .plugin_manager import KILL_GRACE_S, SELF_EXIT_S, TERM_GRACE_S, PluginManagerChild

logger = logging.getLogger("meshcore-host.repeater")

EXIT_CONFIG = 2
EXIT_IDENTITY = 3
# Bounds on the cleanup this host owns, applied BEFORE upstream's exit watchdog is armed (see
# `_Host.run`): deferring that watchdog must never turn its bounded shutdown into an open hang.
CLEANUP_STEP_S = 10.0


def radio_status(connected: bool, tx_ready: bool, enable_tx: bool) -> str:
    """Upstream's dashboard radio state from the adapter's link state: "ok" only when the daemon
    link is up and — with TX enabled — the MANAGED-TX handshake is complete; else "degraded"."""
    return "ok" if connected and (tx_ready or not enable_tx) else "degraded"


def install_presets(cfg: HostConfig) -> None:
    """Place upstream's radio presets where its dashboard reads them, `<storage>/radio-presets.json`
    (its installer copies the file there; LHPC installs with pip, which does not). The source is the
    pinned checkout, so the file follows the pin. Best effort: a missing or unwritable file is
    logged and the repeater starts anyway; the preset picker then reports upstream's own error."""
    if not cfg.presets_file:
        return
    target = Path(cfg.repeater_state_dir) / "radio-presets.json"
    try:
        data = Path(cfg.presets_file).read_bytes()
        if target.is_file() and target.read_bytes() == data:
            return
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(target.name + ".tmp")
        tmp.write_bytes(data)
        os.replace(tmp, target)
    except OSError as exc:
        logger.warning("radio presets not installed (%s); the dashboard's preset picker stays empty", exc)


CHAT_NAME_TAKEN = ("An openHop companion already uses this name with a different identity key; "
                   "remove the old entry in openHop or choose another name.")


def lhpc_values(cfg: HostConfig) -> dict:
    """The sections LHPC owns, in upstream's names and units (Hz, dBm), the dashboard on loopback,
    storage under the runtime root."""
    return {
        # A non-disabled marker: upstream never builds a radio (ours is injected), but its setup
        # wizard reads `radio_type` and would flag a missing one as "setup needed". "external" is
        # the type proposed upstream for a radio passed to RepeaterDaemon(radio=...).
        "radio_type": "external",
        "radio": {
            "frequency": cfg.frequency,
            "bandwidth": cfg.bandwidth,
            "spreading_factor": cfg.spreading_factor,
            "coding_rate": cfg.coding_rate,
            "preamble_length": cfg.preamble,
            "tx_power": cfg.txpower,
            "sync_word": cfg.syncword,
        },
        "http": {"enabled": True, "host": "127.0.0.1", "port": DASHBOARD_PORT},
        "storage": {"storage_dir": cfg.repeater_state_dir},
    }


def chat_companion(cfg: HostConfig) -> dict:
    """The hosted companion IS today's chat node: same fail-closed identity rules as HostApp
    (upstream would merely log and skip a bad companion key, leaving no companion at all)."""
    if cfg.key_file:
        raise ConfigError("[identity] key_file is not supported in the repeater roles — "
                          "LHPC injects the key inline")
    load_identity_hex(cfg.key)                       # raises IdentityError, never limps
    return {
        "name": cfg.name,
        "identity_key": cfg.key,                     # hex: upstream decodes companion keys itself
        "settings": {
            "node_name": cfg.name,
            "bind_address": cfg.bind,
            "tcp_port": cfg.port,
            "tcp_timeout": 0,                        # firmware behaviour: no idle disconnect
        },
    }


def apply_overlay(conf: dict, cfg: HostConfig) -> dict:
    """Put LHPC's values over the keys LHPC owns, in place; the user's keys stay as they are.
    Returns `conf` with the marker block as its first key."""
    conf.update(lhpc_values(cfg))
    for section in ofile.REMOVED_SECTIONS:
        conf.pop(section, None)
    ofile.put(conf, "glass.cert_store_dir", str(Path(cfg.repeater_state_dir) / "glass"))
    conf["setup_completed"] = True
    ofile.put(conf, "repeater.security.jwt_secret", cfg.jwt_secret)
    if cfg.use_gps:
        conf["gps"] = {"enabled": True, "source": "file", "source_path": cfg.gps_file,
                       "poll_interval_seconds": 2.0, "stale_after_seconds": 10.0,
                       "time_sync_enabled": False, "persist_gps_fix_to_config": False}
    else:
        ofile.put(conf, "gps.time_sync_enabled", False)
    _place_chat_node(conf, cfg)
    return ofile.with_marker_first(conf, cfg.use_gps)


def _place_chat_node(conf: dict, cfg: HostConfig) -> None:
    """LHPC's chat node among the companions, found by its identity key: renamed in place, added
    in chat+repeater, removed in the pure repeater (which is given no chat key: the controller
    passes the key's non-secret handle `chat_node_id`, never read from the file). User companions
    are never touched; a user companion carrying the chat node's name refuses the start."""
    identities = conf.get("identities")
    if not isinstance(identities, dict):
        identities = conf["identities"] = {}
    companions = [c for c in (identities.get("companions") or []) if isinstance(c, dict)]
    handle = ofile.key_id(cfg.key) if cfg.companion_on else cfg.chat_node_id
    mine = [c for c in companions if handle and ofile.key_id(str(c.get("identity_key", ""))) == handle]
    others = [c for c in companions if c not in mine]
    if cfg.companion_on:
        if any(c.get("name") == cfg.name for c in others):
            raise ConfigError(CHAT_NAME_TAKEN)
        companions = others + [chat_companion(cfg)]
    else:
        companions = others
    identities["companions"] = companions
    identities.setdefault("room_servers", [])


def check_one_store(conf: dict) -> None:
    """Refuse only what upstream cannot run with (openhop_file.unusable, the rule the controller's
    migration checks too); LHPC's own rules are reported by the controller."""
    _raise_unusable(ofile.unusable(conf))


def _raise_unusable(bad) -> None:
    if bad:
        error = IdentityError if bad[0] == "repeater.identity_key" else ConfigError
        raise error(f"{bad[0]} in openHop's file {bad[1]}")


def chat_pubkey(cfg: HostConfig) -> str:
    if not cfg.companion_on:
        return ""
    from openhop_core import LocalIdentity                 # the same core the companion uses
    return bytes(LocalIdentity(seed=bytes.fromhex(cfg.key.strip())).get_public_key()).hex()


def load_openhop_config(cfg: HostConfig) -> dict:
    """openHop's file through upstream's own loader (defaults, normalisation, policy.yaml)."""
    path = ofile.path_in(cfg.repeater_state_dir)
    if not path.exists():
        raise ConfigError(f"openHop's config file {path} is missing; LHPC creates it before the "
                          "start (restart the stack from LHPC)")
    conf, bad = ofile.load_checked(path)        # upstream's loader + the one rule (shared with `check`)
    _raise_unusable(bad)
    return conf


class _Host:
    """One repeater run: the injected radio, the daemon, the optional GPS feed on the hosted
    companion, and a clean shutdown of what upstream does not own."""

    def __init__(self, cfg: HostConfig):
        self.cfg = cfg
        guards.self_check()                                # before the first upstream load
        self.path = ofile.path_in(cfg.repeater_state_dir)
        self.conf = apply_overlay(load_openhop_config(cfg), cfg)
        ofile.write_atomic(self.path, ofile.dump(self.conf))   # the file shows what runs
        self.chat_pub = chat_pubkey(cfg)
        self.nmea: Optional[NmeaFile] = NmeaFile(cfg.gps_file) if cfg.use_gps else None
        self.radio = LoRaHAMRadio(
            data_socket=cfg.data_socket, config_socket=cfg.config_socket,
            frequency=cfg.frequency, bandwidth=cfg.bandwidth,
            spreading_factor=cfg.spreading_factor, coding_rate=cfg.coding_rate,
            txpower=cfg.txpower, txmaxpower=cfg.txmaxpower, crc=cfg.crc,
            preamble=cfg.preamble, syncword=cfg.syncword, ldro=cfg.ldro,
            enable_tx=cfg.enable_tx,
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

        cfg = self.cfg
        guards.install(lambda: daemon.config, use_gps=cfg.use_gps,
                       chat_key=cfg.key.strip().lower() if cfg.companion_on else "",
                       chat_name=cfg.name if cfg.companion_on else "",
                       chat_pubkey=self.chat_pub, cli_lock=cfg.cli_lock,
                       restart_argv=cfg.restart_argv)
        daemon = LhpcRepeaterDaemon(self.conf, radio=self.radio)
        # openHop's own file, set EXPLICITLY: an inherited $OPENHOP_REPEATER_CONFIG /
        # $PYMC_REPEATER_CONFIG is cleared so nothing can point a save somewhere else.
        for var in ("OPENHOP_REPEATER_CONFIG", "PYMC_REPEATER_CONFIG"):
            os.environ.pop(var, None)
        daemon.config_path = str(self.path)
        daemon.radio_status = "degraded"                   # until the link reports otherwise
        self.radio.on_link_state = lambda connected, tx_ready: setattr(
            daemon, "radio_status", radio_status(connected, tx_ready, self.cfg.enable_tx))
        return daemon

    def _chat_bridge(self, daemon):
        """LHPC's chat node among the hosted companions (user companions may run beside it),
        found by its public key."""
        for bridge in getattr(daemon, "companion_bridges", {}).values():
            identity = getattr(bridge, "_identity", None)
            if identity is not None and bytes(identity.get_public_key()).hex() == self.chat_pub:
                return bridge
        raise ConfigError("upstream did not create LHPC's chat node companion")

    def _after_initialize(self, daemon) -> None:
        bridge = self._chat_bridge(daemon) if self.cfg.companion_on else None
        listener = self.nmea.set_position if self.nmea is not None else None
        if bridge is not None:
            bridge.prefs.node_name = self.cfg.name          # LHPC-owned, reasserted
        if self.cfg.gps_mode == "fixed":
            if bridge is not None:
                bridge.prefs.latitude = self.cfg.gps_lat
                bridge.prefs.longitude = self.cfg.gps_lon
                bridge.prefs.advert_loc_policy = 1          # ADVERT_LOC_SHARE
            if listener is not None:
                listener(self.cfg.gps_lat, self.cfg.gps_lon)
        elif self.cfg.gps_mode == "feed" and (bridge is not None or listener is not None):
            self.gps = GpsFeed(bridge, socket_path=self.cfg.gps_socket,
                               stale_after_s=self.cfg.gps_stale_s, listener=listener)
            self.gps.start()
        elif bridge is not None:
            bridge.prefs.latitude = 0.0
            bridge.prefs.longitude = 0.0
            bridge.prefs.advert_loc_policy = 0              # ADVERT_LOC_NONE
        if self.nmea is not None:
            self.nmea.start()

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
                    self.cfg.mode, ofile.get(self.conf, "repeater.node_name"),
                    self.cfg.name if self.cfg.companion_on else "-",
                    "on" if self.plugins is not None else "off")
        install_presets(self.cfg)
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
                    # blocking by design (its own exit 2 s, SIGTERM, 8 s, SIGKILL, 2 s): bounded by
                    # construction, so its step bound is that worst case plus a second, never less
                    # Shutdown budget: this 13 s + GPS feed 10 s + radio close 10 s + upstream's watchdog 5 s
                    # = 38 s of the node's 40 s stop_timeout; a new step here must fit the 2 s left or raise
                    # stop_timeout.
                    await self._bounded("plugin manager stop",
                                        asyncio.to_thread(self.plugins.stop),
                                        SELF_EXIT_S + TERM_GRACE_S + KILL_GRACE_S + 1.0)
                if self.gps is not None:
                    await self._bounded("GPS feed stop", self.gps.stop())
                if self.nmea is not None:
                    await self._bounded("GPS file stop", self.nmea.stop())
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


__all__ = ["apply_overlay", "check_one_store", "lhpc_values", "radio_status", "run_repeater"]
