"""LoRaHAM daemon radio backend for openHop Core.

Implements openHop's ``LoRaRadio`` contract (``openhop_core.hardware.base``) on top of the
LoRaHAM daemon's framed data + config Unix sockets, so an openHop Companion node can use
the daemon-owned radio exactly the way meshcore-pi's ``lorahaminterface`` did.

This adapter is deliberately MeshCore-protocol agnostic: it moves opaque RF payload bytes
and per-packet RSSI/SNR between the daemon and openHop. Packet parsing, routing, ACK/PATH/
TRACE, crypto and Companion semantics all live in openHop Core.

Daemon wire protocol (loraham_daemon framed_data.h, v111+):
    frame   = [type u8][payload_len u16 LE][payload]
    types   = RX_PACKET 0x01, TX_PACKET 0x02, ERROR 0x03, TX_RESULT 0x04
    RX_PACKET payload  = [rssi_cdbm i16 LE][snr_cdb i16 LE][RF bytes]
    TX_RESULT payload  = [status u8][flags u8][seq u16 LE]
Config socket is line-based ASCII (SET/GET). Channel access (LBT/CAD) is delegated to the
daemon's MANAGED TX mode; every TX_PACKET is answered by exactly one TX_RESULT.

TX is allowed only after the connect handshake verified RADIO=READY, TXMODE=MANAGED,
TXRESULT=1 and a usable CADWAIT (all global per-band daemon state another client could have
changed) — until then TX fails cleanly while RX keeps working.
"""

from __future__ import annotations

import asyncio
import logging
import math
import os
import re
import stat
import struct
import time
from collections import deque
from typing import Callable, Optional

logger = logging.getLogger("LoRaHAMRadio")

try:  # openHop base class when available; adapter also works standalone (tests).
    from openhop_core.hardware.base import LoRaRadio as _LoRaRadioBase
    from openhop_core.protocol.packet_utils import calculate_lora_airtime_ms
except ImportError:  # pragma: no cover - exercised only without openhop installed
    _LoRaRadioBase = object
    calculate_lora_airtime_ms = None

FRAMED_DATA_HEADER_LEN = 3
FRAMED_DATA_TYPE_RX_PACKET = 0x01
FRAMED_DATA_TYPE_TX_PACKET = 0x02
FRAMED_DATA_TYPE_ERROR = 0x03
FRAMED_DATA_TYPE_TX_RESULT = 0x04
FRAMED_DATA_TYPES = {
    FRAMED_DATA_TYPE_RX_PACKET,
    FRAMED_DATA_TYPE_TX_PACKET,
    FRAMED_DATA_TYPE_ERROR,
    FRAMED_DATA_TYPE_TX_RESULT,
}

# RX_PACKET payload: [rssi_cdbm i16 LE][snr_cdb i16 LE][RF payload].
FRAMED_DATA_RX_META_LEN = 4
# TX_RESULT payload: [status u8][flags u8][seq u16 LE].
FRAMED_DATA_TX_RESULT_PAYLOAD_LEN = 4
# Sentinel for unavailable RSSI/SNR, in centi-units.
FRAMED_DATA_SIGNAL_UNAVAILABLE = -32768

# TX_RESULT wire status codes (loraham_daemon framed_data.h, v111). On-the-wire values,
# NOT the daemon's internal TxResult enum. There is no CAD_TIMEOUT status: a not-sent
# MANAGED TX reports CHANNEL_BUSY (2); a send-after-timeout reports OK (0) with the
# CAD_TIMEOUT flag.
TX_RESULT_STATUS_OK = 0
TX_RESULT_STATUS_BUSY = 1
TX_RESULT_STATUS_CHANNEL_BUSY = 2
TX_RESULT_STATUS_RADIO_NOT_READY = 3
TX_RESULT_STATUS_RADIO_ERROR = 4
TX_RESULT_STATUS_INVALID_PACKET = 5
TX_RESULT_STATUS_INVALID_BAND = 6

# TX_RESULT flag bits (informational; status is authoritative).
TX_RESULT_FLAG_MANAGED = 0x01
TX_RESULT_FLAG_DEFERRED = 0x02
TX_RESULT_FLAG_CAD_TIMEOUT = 0x04

# Daemon CADWAIT default (seconds) if the status reply does not report it.
DEFAULT_CADWAIT_S = 1.5

# How often to ask the daemon (GET CHANNEL) for a fresh live-channel RSSI to keep
# get_noise_floor() current. A light cadence: the daemon answers non-destructively
# (it skips the CAD scan when an RX packet is pending), and openHop only reads the
# cached value.
NOISE_POLL_INTERVAL_S = 5.0

# Daemon live-RSSI sentinel (config_status_live_rssi_dbm) returned when the radio is
# TX-busy or its mutex is contended — not a real measurement, so it is never cached.
LIVE_RSSI_UNAVAILABLE_DBM = -200.0

# Largest RF payload the daemon protocol can carry (LoRa maximum).
MAX_RF_PAYLOAD = 255

# Bounded RX buffer for the wait_for_rx() fallback path. The primary delivery path is the
# RX callback (openHop's Dispatcher registers one); this queue only matters for callers
# without a callback, and must never grow without bound if nobody drains it.
RX_QUEUE_MAX = 64

VALID_BANDWIDTHS_HZ = {
    7800, 10400, 15600, 20800, 31250, 41700, 62500, 125000, 250000, 500000,
}


def resolve_socket_path(configured: str) -> str:
    """systemd deployments serve the daemon sockets under /run/loraham; direct/user starts
    under /tmp (LORAHAM_SOCKET_DIR). Prefer the /run/loraham path when the socket already
    exists there, else the configured path — mirrors the daemon's other clients.

    LOCKSTEP: this policy also lives in lhpc/core/daemon_control.py
    (_prefer_run_socket) and lhpc/core/lifecycle.py. It must stay runtime-resolved
    here (the daemon can restart under either namespace while this app runs), so a
    daemon socket-dir change must be applied to all three sites."""
    if not configured:
        return configured
    run = os.path.join("/run/loraham", os.path.basename(configured))
    try:
        if stat.S_ISSOCK(os.stat(run).st_mode):
            return run
    except OSError:
        pass
    return configured


class LoRaHAMRadio(_LoRaRadioBase):
    """openHop radio backend speaking the LoRaHAM daemon socket protocol."""

    def __init__(
        self,
        *,
        data_socket: str,
        config_socket: str,
        frequency: int,
        bandwidth: int,
        spreading_factor: int,
        coding_rate: int,
        txpower: int,
        txmaxpower: Optional[int] = None,
        crc: bool = True,
        preamble: int = 16,
        syncword: int = 0x12,
        ldro: bool = False,
        enable_tx: bool = False,
        apply_config: bool = True,
        connect_timeout: float = 5.0,
        reconnect_delay: float = 5.0,
        tx_result_margin: float = 1.0,
        max_packet_size: int = 255,
        airtime_dutycycle: float = 10.0,
        noise_poll_interval: float = NOISE_POLL_INTERVAL_S,
        resolve_sockets: bool = True,
    ) -> None:
        # Configured paths are kept as given; resolution against /run/loraham
        # happens on EVERY connect attempt (the daemon can restart under either
        # namespace while this app runs).
        self._configured_data_socket = data_socket
        self._configured_config_socket = config_socket
        self._resolve_sockets = resolve_sockets
        self.data_socket = data_socket
        self.config_socket = config_socket
        # Canonical names: openHop's Dispatcher reads spreading_factor / bandwidth /
        # coding_rate / preamble_length from the radio via getattr for airtime maths
        # (flood RX delay, TX budget, retransmit jitter) — they MUST exist under
        # exactly these names or silent wrong fallbacks (SF10/250k/CR5/8) apply.
        self.frequency = frequency
        self.bandwidth = bandwidth
        self.spreading_factor = spreading_factor
        self.coding_rate = coding_rate
        self.crc = crc
        self.preamble_length = preamble
        self.syncword = syncword
        self.ldro = ldro
        self.txpower = txpower
        self.txmaxpower = txpower if txmaxpower is None else txmaxpower
        self.enable_tx = enable_tx
        self.apply_config = apply_config
        self.connect_timeout = connect_timeout
        self.reconnect_delay = reconnect_delay
        self.tx_result_margin = tx_result_margin
        self.max_packet_size = max_packet_size
        self.airtime_dutycycle = airtime_dutycycle
        self._noise_poll_interval = noise_poll_interval

        self._validate_config()

        self._rx_callback: Optional[Callable] = None
        self._rx_queue: deque = deque(maxlen=RX_QUEUE_MAX)
        self._rx_available = asyncio.Event()
        self._last_rssi = 0
        self._last_snr = 0.0
        # Cached idle-channel RSSI (dBm) from the daemon's live-RSSI reads; None
        # until the first CHANNEL reply and reset on disconnect, so openHop's
        # stats path omits noise_floor rather than reporting a stale/bogus 0.
        self._noise_floor: Optional[float] = None

        self._data_reader: Optional[asyncio.StreamReader] = None
        self._data_writer: Optional[asyncio.StreamWriter] = None
        self._config_reader: Optional[asyncio.StreamReader] = None
        self._config_writer: Optional[asyncio.StreamWriter] = None

        self._running = False
        self._closed = False
        self._manager_task: Optional[asyncio.Task] = None
        # Serialises the whole TX transaction (arm pending -> write -> await -> consume)
        # so at most one _pending_tx_result exists at any time even if sends overlap.
        self._tx_lock = asyncio.Lock()
        self._cadwait_s = DEFAULT_CADWAIT_S
        self._tx_ready = False
        self._connected = False
        self._pending_tx_result: Optional[asyncio.Future] = None

        # Rolling duty-cycle accounting (same shape as meshcore-pi): the last 5 TX
        # timestamps and airtimes bound the on-air share to `airtime_dutycycle` percent.
        # DELIBERATELY separate from openHop's dispatcher TX budget: that leaky bucket
        # is active only while client-repeat is enabled (prefs.client_repeat), while
        # this limiter is the LHPC-configured regulatory backstop that must hold for
        # a plain companion too. With client-repeat off it is the only governor.
        self._airtime_txtimestamp: deque = deque([0.0] * 5, maxlen=5)
        self._airtime_txtime: deque = deque([0.0] * 5, maxlen=5)

        # Optional observer: called with (connected: bool, tx_ready: bool) on every
        # connection state change, for host-level readiness reporting.
        self.on_link_state: Optional[Callable[[bool, bool], None]] = None

    # ------------------------------------------------------------------
    # Config validation
    # ------------------------------------------------------------------

    def _validate_config(self) -> None:
        for name in ("data_socket", "config_socket"):
            value = getattr(self, name)
            if not isinstance(value, str) or value == "":
                raise ValueError(f"{name} must be a non-empty string")
        for name in ("frequency", "spreading_factor", "bandwidth", "coding_rate",
                     "preamble_length", "syncword",
                     "txpower", "txmaxpower", "max_packet_size"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"{name} must be an integer")
        if self.frequency <= 0:
            raise ValueError("frequency must be positive")
        if self.bandwidth not in VALID_BANDWIDTHS_HZ:
            raise ValueError("bw must be a valid SX127x LoRa bandwidth in Hz")
        if not 6 <= self.preamble_length <= 65535:
            raise ValueError("preamble must be between 6 and 65535")
        if not 7 <= self.spreading_factor <= 12:
            raise ValueError("sf must be between 7 and 12")
        if not 5 <= self.coding_rate <= 8:
            raise ValueError("cr must be between 5 and 8")
        if not 0 <= self.syncword <= 0xFF:
            raise ValueError("syncword must fit in one byte")
        if not 0 <= self.txpower <= 20:
            raise ValueError("txpower must be between 0 and 20 dBm")
        if not self.txpower <= self.txmaxpower <= 20:
            raise ValueError("txmaxpower must be between txpower and 20 dBm")
        if not 1 <= self.max_packet_size <= 255:
            raise ValueError("max_packet_size must be between 1 and 255")
        for name in ("crc", "ldro", "enable_tx", "apply_config"):
            if not isinstance(getattr(self, name), bool):
                raise ValueError(f"{name} must be boolean")
        for name in ("connect_timeout", "reconnect_delay"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
                raise ValueError(f"{name} must be a positive number")
        if (isinstance(self.tx_result_margin, bool)
                or not isinstance(self.tx_result_margin, (int, float))
                or self.tx_result_margin < 0):
            raise ValueError("tx_result_margin must be zero or positive")
        if (isinstance(self.airtime_dutycycle, bool)
                or not isinstance(self.airtime_dutycycle, (int, float))
                or not 0 < self.airtime_dutycycle <= 100):
            raise ValueError("airtime_dutycycle must be between 0 and 100 percent")

    # ------------------------------------------------------------------
    # openHop LoRaRadio contract
    # ------------------------------------------------------------------

    def begin(self) -> None:
        """Start the persistent daemon connection manager. Requires a running loop."""
        if self._running:
            return
        loop = asyncio.get_running_loop()
        self._running = True
        self._manager_task = loop.create_task(
            self._connection_loop(), name="LoRaHAM daemon socket manager"
        )

    async def send(self, data: bytes) -> Optional[dict]:
        """Transmit one RF payload through the daemon and await its TX_RESULT.

        Returns a metadata dict on a confirmed successful transmission, None on any
        failure — exactly the contract openHop's Dispatcher expects.
        """
        data = bytes(data)
        if not self.enable_tx:
            logger.debug("TX disabled (RX-only mode); packet discarded")
            return None
        if not data or len(data) > self.max_packet_size:
            logger.error("TX payload size invalid: %d bytes", len(data))
            return None

        async with self._tx_lock:
            # Reject a missing or closing transport so a result of a timed-out or
            # cancelled TX cannot be inherited by a new future before the reconnect
            # handshake re-establishes a fresh stream.
            if self._data_writer is None or self._data_writer.is_closing():
                logger.warning("Daemon data socket not connected; packet not sent")
                return None
            if not self._tx_ready:
                # Do not latch a failed handshake forever: force a reconnect,
                # which re-runs the GET STATUS handshake (paced by
                # reconnect_delay), so a transient RADIO=INITIALIZING or a
                # briefly-flipped TXMODE recovers without a socket error.
                logger.warning(
                    "Daemon TX not ready (CADWAIT/TXMODE/TXRESULT); packet not "
                    "sent, re-handshaking"
                )
                self._request_reconnect()
                return None

            # Rolling duty-cycle guard: pace rather than refuse, so the mesh sees a
            # delayed packet instead of a lost one.
            wait_s = self.transmit_wait()
            if wait_s > 0:
                logger.info("Duty-cycle limit: delaying TX %.2f s", wait_s)
                await asyncio.sleep(wait_s)
                if self._data_writer is None or self._data_writer.is_closing() \
                        or not self._tx_ready:
                    logger.warning("Daemon link changed during duty-cycle wait; packet not sent")
                    return None

            airtime_ms = self._calculate_airtime_ms(len(data))
            timeout = self._cadwait_s + (airtime_ms / 1000.0) + self.tx_result_margin

            loop = asyncio.get_running_loop()
            future: asyncio.Future = loop.create_future()
            # Arm the pending slot before writing so a fast TX_RESULT is not missed.
            self._pending_tx_result = future

            try:
                await self._write_frame(FRAMED_DATA_TYPE_TX_PACKET, data)
                result = await asyncio.wait_for(future, timeout=timeout)
            except asyncio.CancelledError:
                # Clear our slot before unwinding so a late TX_RESULT cannot be
                # mis-associated with the next send(); force a fresh handshake.
                if self._pending_tx_result is future:
                    self._pending_tx_result = None
                self._set_tx_ready(False)
                self._request_reconnect()
                raise
            except (TimeoutError, asyncio.TimeoutError):
                # asyncio.TimeoutError is only an alias of TimeoutError from 3.11 on;
                # catching both keeps the reconnect guard working on Python 3.10.
                if self._pending_tx_result is future:
                    self._pending_tx_result = None
                self._set_tx_ready(False)
                logger.warning(
                    "No TX_RESULT after %.2f s; packet result lost, reconnecting", timeout
                )
                self._request_reconnect()
                return None
            except (ConnectionError, OSError) as exc:
                # Stream error: the frame may already have reached the daemon, so a late
                # TX_RESULT could be inherited by the next send(). Invalidate and reconnect.
                if self._pending_tx_result is future:
                    self._pending_tx_result = None
                self._set_tx_ready(False)
                logger.error("Daemon TX stream error: %s", exc)
                self._request_reconnect()
                return None
            except Exception as exc:
                # Local error before anything went out; connection stays usable.
                if self._pending_tx_result is future:
                    self._pending_tx_result = None
                logger.error("Daemon TX failed: %s", exc)
                return None

            if result is None:
                # Resolved by an ERROR frame or a connection drop.
                logger.warning("TX aborted before a result was received")
                return None

            status, flags, seq = result
            if status == TX_RESULT_STATUS_OK:
                # OK also covers send-after-CAD-timeout (flag 0x04): transmitted.
                self._airtime_txtimestamp.append(time.monotonic())
                self._airtime_txtime.append(airtime_ms)
                logger.debug(
                    "TX ok: %d bytes, airtime %.1f ms, flags=0x%02X seq=%d",
                    len(data), airtime_ms, flags, seq,
                )
                return {"airtime_ms": airtime_ms, "tx_flags": flags, "tx_seq": seq}
            if status in (TX_RESULT_STATUS_BUSY, TX_RESULT_STATUS_CHANNEL_BUSY):
                logger.info("Channel busy (status=%d); packet not sent", status)
            elif status in (TX_RESULT_STATUS_RADIO_NOT_READY, TX_RESULT_STATUS_RADIO_ERROR):
                logger.error("Daemon radio error (status=%d seq=%d)", status, seq)
            elif status in (TX_RESULT_STATUS_INVALID_PACKET, TX_RESULT_STATUS_INVALID_BAND):
                logger.error("Daemon rejected packet (status=%d seq=%d)", status, seq)
            else:
                logger.error("Unknown TX_RESULT status=%d seq=%d", status, seq)
            return None

    async def wait_for_rx(self) -> bytes:
        """Fallback RX path for callers without an RX callback.

        Raises ConnectionError once the radio has been closed, so shutdown wakes
        blocked waiters instead of hanging them forever.
        """
        while True:
            if self._rx_queue:
                rf, rssi, snr = self._rx_queue.popleft()
                if not self._rx_queue:
                    self._rx_available.clear()
                self._last_rssi = rssi
                self._last_snr = snr
                return rf
            if self._closed:
                raise ConnectionError("LoRaHAMRadio is closed")
            self._rx_available.clear()
            await self._rx_available.wait()

    def set_rx_callback(self, callback: Optional[Callable]) -> None:
        """Register the per-packet callback: callback(data, rssi, snr)."""
        self._rx_callback = callback

    def sleep(self) -> None:
        """No-op: the daemon owns radio power management."""

    def get_last_rssi(self) -> int:
        # Same rounding as the per-packet callback values, so companion stats and
        # dispatcher-stamped packet RSSI agree for the same reception.
        return int(round(self._last_rssi))

    def get_last_snr(self) -> float:
        return float(self._last_snr)

    def get_noise_floor(self) -> Optional[float]:
        """Idle-channel RSSI (dBm) cached from the daemon's live-RSSI reads.

        openHop's CompanionRadio._get_radio_stats surfaces this into
        STATS_TYPE_RADIO (the same hasattr contract as the SX1262/TCP/KISS
        radios). None until the first CHANNEL reply is seen (or while
        disconnected), so the stats path omits noise_floor rather than
        reporting a bogus 0.
        """
        return self._noise_floor

    async def refresh_noise_floor(self) -> Optional[float]:
        """Ask the daemon (GET CHANNEL) for a fresh channel read.

        Best-effort: the reply is parsed and cached by the config reader loop
        (LIVERSSI). Mirrors the TCP radio's refresh/get pair; the noise poller
        drives this on a timer, but openHop may also call it directly.
        """
        writer = self._config_writer
        if writer is not None and not writer.is_closing():
            try:
                await self._write_config_command("GET CHANNEL\n")
            except Exception as exc:
                logger.debug("Noise-floor refresh failed: %s", exc)
        return self._noise_floor

    def _maybe_update_noise_floor(self, line: str) -> None:
        """Cache the daemon's live channel RSSI from a `CHANNEL …` status line.

        LIVERSSI is a chip-native idle-channel RSSI read (readLiveRssi) — the
        closest value the daemon exposes to a noise floor. The -200 dBm sentinel
        (TX-busy / mutex contended) is discarded so a real reading is kept.
        """
        if "CHANNEL" not in line.upper():
            return
        fields = self._parse_key_value_fields(line)
        raw = fields.get("LIVERSSI") or fields.get("RSSI")
        if raw is None:
            return
        try:
            value = float(raw)
        except ValueError:
            return
        if value <= LIVE_RSSI_UNAVAILABLE_DBM:
            return
        self._noise_floor = value

    def check_radio_health(self) -> bool:
        """Periodic health probe (called by openHop's maintenance loop in a thread).

        Purely observational: reconnection is the connection manager's job.
        """
        healthy = self._connected and (self._tx_ready or not self.enable_tx)
        if not healthy:
            logger.warning(
                "Daemon link unhealthy: connected=%s tx_ready=%s (tx enabled=%s)",
                self._connected, self._tx_ready, self.enable_tx,
            )
        return healthy

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def tx_ready(self) -> bool:
        return self._tx_ready

    async def aclose(self) -> None:
        """Orderly shutdown: stop the manager, close sockets, release waiters."""
        self._running = False
        self._closed = True
        task = self._manager_task
        self._manager_task = None
        if task is not None:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        await self._close_sockets()
        # Wake any wait_for_rx() waiter so it observes the closed state.
        self._rx_available.set()

    # ------------------------------------------------------------------
    # Daemon connection management
    # ------------------------------------------------------------------

    def _set_link_state(self, connected: bool, tx_ready: bool) -> None:
        changed = (connected != self._connected) or (tx_ready != self._tx_ready)
        self._connected = connected
        self._tx_ready = tx_ready
        if changed and self.on_link_state is not None:
            try:
                self.on_link_state(connected, tx_ready)
            except Exception:
                logger.exception("on_link_state observer failed")

    def _set_tx_ready(self, ready: bool) -> None:
        self._set_link_state(self._connected, ready)

    async def _open_unix_connection(self, path: str, label: str):
        logger.info("Connecting LoRaHAM %s socket: %s", label, path)
        try:
            return await asyncio.wait_for(
                asyncio.open_unix_connection(path), timeout=self.connect_timeout
            )
        except FileNotFoundError as exc:
            raise ConnectionError(f"LoRaHAM {label} socket not found: {path}") from exc
        except (TimeoutError, asyncio.TimeoutError) as exc:
            raise ConnectionError(
                f"Timed out connecting LoRaHAM {label} socket: {path}"
            ) from exc
        except OSError as exc:
            raise ConnectionError(
                f"Unable to connect LoRaHAM {label} socket {path}: {exc}"
            ) from exc

    async def _connect_sockets(self) -> None:
        self._fail_pending_tx()
        self._cadwait_s = DEFAULT_CADWAIT_S
        self._set_link_state(False, False)

        if self._resolve_sockets:
            self.data_socket = resolve_socket_path(self._configured_data_socket)
            self.config_socket = resolve_socket_path(self._configured_config_socket)

        self._data_reader, self._data_writer = await self._open_unix_connection(
            self.data_socket, "data"
        )
        self._config_reader, self._config_writer = await self._open_unix_connection(
            self.config_socket, "config"
        )

        if self.apply_config:
            await self._send_config()
        if self.enable_tx:
            # Delegate LBT to the daemon and ask for a per-TX result frame.
            await self._write_config_command("SET TXMODE=MANAGED\n")
            await self._write_config_command("SET TXRESULT=1\n")

        tx_ready = await self._handshake_status()
        self._set_link_state(True, tx_ready if self.enable_tx else False)
        logger.info("LoRaHAM daemon sockets connected (tx_ready=%s)", self._tx_ready)

    @staticmethod
    def _format_decimal(value: float) -> str:
        return f"{value:.6f}".rstrip("0").rstrip(".")

    def _format_config_command(self) -> str:
        return (
            "SET MODE=LORA "
            f"FREQ={self._format_decimal(self.frequency / 1_000_000)} "
            f"SF={self.spreading_factor} "
            f"BW={self._format_decimal(self.bandwidth / 1000)} "
            f"CR={self.coding_rate} "
            f"CRC={1 if self.crc else 0} "
            f"PREAMBLE={self.preamble_length} "
            f"SYNC=0x{self.syncword:02X} "
            f"LDRO={1 if self.ldro else 0} "
            f"POWER={self.txpower}\n"
        )

    async def _send_config(self) -> None:
        command = self._format_config_command()
        logger.info("Applying LoRaHAM radio config: %s", command.strip())
        await self._write_config_command(command)

    async def _write_config_command(self, command: str) -> None:
        writer = self._config_writer
        if writer is None:
            raise ConnectionError("LoRaHAM config socket is not connected")
        writer.write(command.encode("ascii"))
        await writer.drain()

    @staticmethod
    def _parse_key_value_fields(line: str) -> dict:
        return {
            key.upper(): value.upper()
            for key, value in re.findall(r"\b([A-Z]+)=([^\s]+)", line.upper())
        }

    async def _handshake_status(self) -> bool:
        """One-shot connect handshake: `GET STATUS`, cache CADWAIT, verify TX gates.

        Returns whether TX is ready. RX-only clients never gate on this.
        """
        try:
            await self._write_config_command("GET STATUS\n")
        except Exception as exc:
            logger.warning("Unable to request LoRaHAM status: %s", exc)
            return False

        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.connect_timeout
        buffer = bytearray()

        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                logger.warning(
                    "No CADWAIT in daemon status; TX %s",
                    "inhibited" if self.enable_tx else "n/a",
                )
                return False
            try:
                data = await asyncio.wait_for(
                    self._config_reader.read(256), timeout=remaining
                )
            except (TimeoutError, asyncio.TimeoutError):
                logger.warning("No daemon status reply; TX inhibited")
                return False
            if not data:
                raise ConnectionError("LoRaHAM config socket closed during status handshake")

            buffer.extend(data)
            while b"\n" in buffer:
                line, _, rest = buffer.partition(b"\n")
                buffer = bytearray(rest)
                decoded = line.decode(errors="replace").rstrip("\r")
                logger.debug("LoRaHAM config socket: %s", decoded)

                fields = self._parse_key_value_fields(decoded)
                if "CADWAIT" not in fields:
                    continue
                cadwait_ok = False
                try:
                    self._cadwait_s = max(int(fields["CADWAIT"]) / 1000.0, 0.0)
                    cadwait_ok = True
                    logger.info("LoRaHAM CADWAIT=%s ms", fields["CADWAIT"])
                except ValueError:
                    logger.warning("Bad LoRaHAM CADWAIT value: %s", fields["CADWAIT"])

                if not self.enable_tx:
                    return False
                radio = fields.get("RADIO")
                txmode = fields.get("TXMODE")
                txresult = fields.get("TXRESULT")
                if (cadwait_ok and radio == "READY"
                        and txmode == "MANAGED" and txresult == "1"):
                    return True
                logger.warning(
                    "LoRaHAM TX not ready: RADIO=%s CADWAIT_ok=%s TXMODE=%s "
                    "TXRESULT=%s; TX inhibited",
                    radio, cadwait_ok, txmode, txresult,
                )
                return False

    async def _config_reader_loop(self) -> None:
        buffer = bytearray()
        while True:
            data = await self._config_reader.read(256)
            if not data:
                raise ConnectionError("LoRaHAM config socket closed")
            buffer.extend(data)
            while b"\n" in buffer:
                line, _, rest = buffer.partition(b"\n")
                buffer = bytearray(rest)
                decoded = line.decode(errors="replace").rstrip("\r")
                logger.debug("LoRaHAM config socket: %s", decoded)
                self._maybe_update_noise_floor(decoded)
            if len(buffer) > 4096:
                logger.warning("Discarding oversized LoRaHAM config socket buffer")
                buffer.clear()

    async def _noise_poll_loop(self) -> None:
        """Refresh the cached noise floor on a timer while connected.

        Spawned per-connection alongside the reader loops and cancelled with
        them on disconnect, so it only runs while the config socket is up.
        """
        while True:
            await self.refresh_noise_floor()
            await asyncio.sleep(self._noise_poll_interval)

    async def _read_exact(self, reader: asyncio.StreamReader, size: int, label: str) -> bytes:
        try:
            return await reader.readexactly(size)
        except asyncio.IncompleteReadError as exc:
            raise ConnectionError(
                f"LoRaHAM {label} socket closed while reading {size} bytes"
            ) from exc

    def _decode_frame_header(self, header: bytes):
        frame_type, payload_len = struct.unpack("<BH", header)
        if frame_type not in FRAMED_DATA_TYPES:
            # Unknown type means framing may be lost entirely; treated as a connection
            # error by the reader loop, which forces a clean reconnect.
            raise ValueError(f"unknown LoRaHAM frame type 0x{frame_type:02X}")
        return frame_type, payload_len

    async def _read_frame(self, reader: asyncio.StreamReader, label: str):
        header = await self._read_exact(reader, FRAMED_DATA_HEADER_LEN, label)
        frame_type, payload_len = self._decode_frame_header(header)
        if payload_len == 0:
            return frame_type, b""
        # Oversize guard: RX_PACKET carries 4 metadata bytes before the RF payload.
        # RX is judged against the PROTOCOL maximum (255), not max_packet_size — that
        # is a local TX MTU and must not discard valid packets other nodes sent.
        if frame_type == FRAMED_DATA_TYPE_RX_PACKET:
            if payload_len > FRAMED_DATA_RX_META_LEN + MAX_RF_PAYLOAD:
                await self._read_exact(reader, payload_len, label)
                logger.warning("Dropping oversized LoRaHAM RX frame: %d bytes", payload_len)
                return None, b""
        elif frame_type == FRAMED_DATA_TYPE_TX_PACKET:
            if payload_len > self.max_packet_size:
                await self._read_exact(reader, payload_len, label)
                logger.warning("Dropping oversized LoRaHAM frame: %d bytes", payload_len)
                return None, b""
        payload = await self._read_exact(reader, payload_len, label)
        return frame_type, payload

    def _handle_rx_packet(self, payload: bytes):
        if len(payload) < FRAMED_DATA_RX_META_LEN:
            logger.warning("Ignoring short LoRaHAM RX frame: %d bytes", len(payload))
            return None
        rssi_cdbm = int.from_bytes(payload[0:2], "little", signed=True)
        snr_cdb = int.from_bytes(payload[2:4], "little", signed=True)
        rf = payload[FRAMED_DATA_RX_META_LEN:]
        if not rf:
            logger.warning("Ignoring empty LoRaHAM RX packet frame")
            return None
        rssi = 0.0 if rssi_cdbm == FRAMED_DATA_SIGNAL_UNAVAILABLE else rssi_cdbm / 100.0
        snr = 0.0 if snr_cdb == FRAMED_DATA_SIGNAL_UNAVAILABLE else snr_cdb / 100.0
        return bytes(rf), rssi, snr

    async def _data_reader_loop(self) -> None:
        while True:
            frame_type, payload = await self._read_frame(self._data_reader, "data")
            if frame_type is None:
                continue
            if frame_type == FRAMED_DATA_TYPE_RX_PACKET:
                decoded = self._handle_rx_packet(payload)
                if decoded is None:
                    continue
                rf, rssi, snr = decoded
                self._last_rssi = rssi
                self._last_snr = snr
                cb = self._rx_callback
                if cb is not None:
                    try:
                        cb(rf, int(round(rssi)), snr)
                    except Exception:
                        logger.exception("RX callback failed")
                else:
                    # Bounded fallback queue (drop-oldest under overload).
                    self._rx_queue.append((rf, rssi, snr))
                    self._rx_available.set()
                continue
            if frame_type == FRAMED_DATA_TYPE_TX_RESULT:
                self._deliver_tx_result(payload)
                continue
            if frame_type == FRAMED_DATA_TYPE_ERROR:
                logger.error(
                    "LoRaHAM framed data error: %s",
                    payload.decode("utf-8", errors="replace"),
                )
                if self._pending_tx_result is not None:
                    # A daemon ERROR can arrive instead of — or in addition to — a
                    # TX_RESULT. The pairing of pending sends to results is now
                    # ambiguous (a late TX_RESULT could resolve the NEXT send), so
                    # fail the in-flight transmit AND resync with a reconnect,
                    # exactly like the timeout path.
                    self._fail_pending_tx()
                    self._set_tx_ready(False)
                    self._request_reconnect()
                continue
            logger.warning("Ignoring unexpected LoRaHAM data frame type 0x%02X", frame_type)

    def _deliver_tx_result(self, payload: bytes) -> None:
        # TX_RESULT payload is exactly 4 bytes; any other length is malformed. Ignore it
        # (do not resolve the pending future) and let the timeout act.
        if len(payload) != FRAMED_DATA_TX_RESULT_PAYLOAD_LEN:
            logger.warning("Ignoring malformed TX_RESULT frame: %d bytes", len(payload))
            return
        status, flags, seq = struct.unpack("<BBH", payload)
        future = self._pending_tx_result
        if future is None or future.done():
            logger.warning(
                "Discarding unexpected TX_RESULT (status=%d flags=0x%02X seq=%d)",
                status, flags, seq,
            )
            return
        self._pending_tx_result = None
        future.set_result((status, flags, seq))

    def _fail_pending_tx(self) -> None:
        future = self._pending_tx_result
        self._pending_tx_result = None
        if future is not None and not future.done():
            future.set_result(None)

    def _request_reconnect(self) -> None:
        # Closing the data writer drops the transport, which makes the reader loops error
        # out so the connection loop reconnects.
        writer = self._data_writer
        if writer is not None:
            try:
                writer.close()
            except Exception as exc:
                logger.debug("Error while forcing reconnect: %s", exc)

    async def _connection_loop(self) -> None:
        while self._running:
            tasks = []
            try:
                await self._connect_sockets()
                tasks = [
                    asyncio.create_task(
                        self._data_reader_loop(), name="LoRaHAM data socket reader"
                    ),
                    asyncio.create_task(
                        self._config_reader_loop(), name="LoRaHAM config socket reader"
                    ),
                    asyncio.create_task(
                        self._noise_poll_loop(), name="LoRaHAM noise-floor poller"
                    ),
                ]
                done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    task.result()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error("LoRaHAM socket connection error: %s", exc)
            finally:
                for task in tasks:
                    task.cancel()
                if tasks:
                    await asyncio.gather(*tasks, return_exceptions=True)
                await self._close_sockets()
            if self._running:
                logger.info(
                    "Reconnecting LoRaHAM daemon sockets in %.1f s", self.reconnect_delay
                )
                await asyncio.sleep(self.reconnect_delay)

    async def _close_sockets(self) -> None:
        # Resolve any in-flight transmit so it does not hang across reconnect, and
        # require a fresh handshake before the next TX.
        self._fail_pending_tx()
        self._set_link_state(False, False)
        # Drop the cached noise floor: a value read before the drop is stale, and
        # openHop should report "no reading" (0) rather than a stale one until the
        # poller refreshes it after reconnect.
        self._noise_floor = None

        writers = (self._data_writer, self._config_writer)
        self._data_reader = None
        self._data_writer = None
        self._config_reader = None
        self._config_writer = None
        for writer in writers:
            if writer is None:
                continue
            writer.close()
            try:
                await writer.wait_closed()
            except Exception as exc:
                logger.debug("Error while closing LoRaHAM socket: %s", exc)

    # ------------------------------------------------------------------
    # Framing / airtime helpers
    # ------------------------------------------------------------------

    def _encode_frame(self, frame_type: int, payload: bytes) -> bytes:
        payload = bytes(payload)
        return struct.pack("<BH", frame_type, len(payload)) + payload

    async def _write_frame(self, frame_type: int, payload: bytes) -> None:
        writer = self._data_writer
        if writer is None:
            raise ConnectionError("LoRaHAM data socket is not connected")
        # No extra lock: send() is the only writer and already serialises the whole
        # TX transaction under _tx_lock.
        frame = self._encode_frame(frame_type, payload)
        writer.write(frame)
        await writer.drain()

    def _calculate_airtime_ms(self, payload_len: int) -> float:
        """LoRa packet airtime in milliseconds.

        Uses openHop's RadioLib-matched helper — the SAME formula the Dispatcher
        applies for its airtime maths — so the TX_RESULT timeout and the duty
        accounting agree with the rest of the process. The local Semtech-formula
        fallback exists only for openhop-less standalone use.
        """
        if calculate_lora_airtime_ms is not None:
            return calculate_lora_airtime_ms(
                payload_len,
                self.spreading_factor,
                self.bandwidth,
                self.coding_rate,
                preamble_symbols=self.preamble_length,
                crc_enabled=self.crc,
                low_dr_opt=self.ldro or None,
            )
        symbol_time = (2 ** self.spreading_factor) / self.bandwidth  # pragma: no cover
        low_data_rate = 1 if self.ldro else 0
        crc_enabled = 1 if self.crc else 0
        payload_symbols = 8 + max(
            math.ceil(
                ((8 * payload_len) - (4 * self.spreading_factor) + 28 + (16 * crc_enabled))
                / (4 * (self.spreading_factor - (2 * low_data_rate)))
            ) * self.coding_rate,
            0,
        )
        return (self.preamble_length + 4.25 + payload_symbols) * symbol_time * 1000

    def transmit_wait(self) -> float:
        """Suggested duty-cycle wait in seconds before the next TX (0 = go now)."""
        tx_earliest = self._airtime_txtimestamp[0]
        tx_period = time.monotonic() - tx_earliest
        tx_total = sum(self._airtime_txtime)
        if tx_earliest <= 0 or tx_period <= 0 or tx_total <= 0:
            return 0.0
        duty_cycle = 100 * (tx_total / 1000) / tx_period
        for c in range(3):
            fraction = 1 / (1 << c)
            limit = self.airtime_dutycycle * fraction
            if duty_cycle > limit:
                tx_min = (
                    tx_earliest + (tx_total / 1000) / (limit / 100) - time.monotonic()
                ) * fraction
                if tx_min > 0:
                    return tx_min
        return 0.0

    def get_radioconfig(self) -> tuple:
        """(freq_khz, bw_hz, sf, cr, txpower_dbm, txmaxpower_dbm)."""
        return (self.frequency // 1000, self.bandwidth, self.spreading_factor, self.coding_rate, self.txpower, self.txmaxpower)
