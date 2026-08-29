"""Live position feed consumer: LHPC's normalized position -> Companion advert position.

LHPC owns the GPS source (gpsd / NMEA device / auto policy); its bridge republishes a
NORMALIZED position as line-JSON on a Unix socket. This consumer applies it to the
Companion's advert position — no gpsd protocol, no NMEA parsing, no source policy here.

Feed protocol (one JSON object per line):
    {"fix": true, "lat": <deg>, "lon": <deg>}   position update
    {"fix": false}                              source alive but no usable fix

Required semantics (the reason this module exists):
    live fix          -> position advertised (ADVERT_LOC_SHARE)
    stale / no fix    -> position REMOVED (ADVERT_LOC_NONE), never retained
    source recovers   -> position reappears without restarting MeshCore

Staleness is enforced by a local timer as well as by explicit no-fix lines, so a dead
bridge (socket gone, no lines at all) also clears the position.

Never logs coordinates. Never persists live coordinates (the persistence layer
deliberately excludes position prefs).
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Optional

from openhop_core.companion.constants import ADVERT_LOC_NONE, ADVERT_LOC_SHARE

from .config import is_valid_lat_lon

logger = logging.getLogger("meshcore-host.gps")

_RECONNECT_MIN_S = 1.0
_RECONNECT_MAX_S = 30.0
_MAX_LINE = 1024


class GpsFeed:
    def __init__(self, companion, *, socket_path: str, stale_after_s: float = 60.0):
        self._companion = companion
        self._socket_path = socket_path
        self._stale_after_s = stale_after_s
        self._task: Optional[asyncio.Task] = None
        self._stale_handle: Optional[asyncio.TimerHandle] = None
        self._have_position = False

    @property
    def has_position(self) -> bool:
        return self._have_position

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.get_running_loop().create_task(
                self._run(), name="gps feed"
            )

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:
                # A feed task that died of an unexpected error must not also
                # break shutdown — log it and finish tearing down.
                logger.exception("GPS feed task had failed")
        self._cancel_stale_timer()
        self._clear_position("shutdown")

    # ------------------------------------------------------------------

    def _apply_position(self, lat: float, lon: float) -> None:
        if not is_valid_lat_lon(lat, lon):
            logger.warning("Feed position out of range; ignored")
            return
        prefs = self._companion.prefs
        prefs.latitude = lat
        prefs.longitude = lon
        if prefs.advert_loc_policy != ADVERT_LOC_SHARE:
            prefs.advert_loc_policy = ADVERT_LOC_SHARE
            logger.info("Live position acquired; advert location enabled")
        self._have_position = True
        self._arm_stale_timer()

    def _clear_position(self, why: str) -> None:
        prefs = self._companion.prefs
        if prefs.advert_loc_policy != ADVERT_LOC_NONE or self._have_position:
            prefs.advert_loc_policy = ADVERT_LOC_NONE
            prefs.latitude = 0.0
            prefs.longitude = 0.0
            self._have_position = False
            logger.info("Live position removed (%s)", why)

    def _arm_stale_timer(self) -> None:
        self._cancel_stale_timer()
        loop = asyncio.get_running_loop()
        self._stale_handle = loop.call_later(
            self._stale_after_s, self._clear_position, "stale"
        )

    def _cancel_stale_timer(self) -> None:
        if self._stale_handle is not None:
            self._stale_handle.cancel()
            self._stale_handle = None

    def _handle_line(self, line: bytes) -> None:
        try:
            record = json.loads(line)
        except ValueError:
            logger.warning("Malformed GPS feed line ignored")
            return
        if not isinstance(record, dict):
            logger.warning("Malformed GPS feed record ignored")
            return
        if record.get("fix") is True:
            lat, lon = record.get("lat"), record.get("lon")
            if isinstance(lat, (int, float)) and isinstance(lon, (int, float)) \
                    and not isinstance(lat, bool) and not isinstance(lon, bool):
                self._apply_position(float(lat), float(lon))
            else:
                logger.warning("GPS feed fix without coordinates ignored")
        else:
            self._cancel_stale_timer()
            self._clear_position("no fix")

    async def _run(self) -> None:
        delay = _RECONNECT_MIN_S
        try:
            while True:
                try:
                    reader, writer = await asyncio.open_unix_connection(self._socket_path)
                except (OSError, ConnectionError):
                    self._maybe_stale_disconnect()
                    await asyncio.sleep(delay)
                    delay = min(delay * 2, _RECONNECT_MAX_S)
                    continue
                delay = _RECONNECT_MIN_S
                logger.info("GPS feed connected")
                try:
                    while True:
                        try:
                            line = await reader.readline()
                        except ValueError:
                            # StreamReader limit overrun (a line beyond the 64 KiB
                            # buffer): the stream is unusable — reconnect rather
                            # than let the feed task die for the process lifetime.
                            logger.warning("GPS feed line overran the buffer; reconnecting")
                            break
                        if not line:
                            break
                        if len(line) > _MAX_LINE:
                            logger.warning("Oversized GPS feed line ignored")
                            continue
                        self._handle_line(line)
                except (OSError, ConnectionError, asyncio.IncompleteReadError):
                    pass
                finally:
                    writer.close()
                    try:
                        await writer.wait_closed()
                    except Exception:
                        pass
                logger.info("GPS feed disconnected; retrying")
                self._maybe_stale_disconnect()
                await asyncio.sleep(delay)
        except asyncio.CancelledError:
            raise

    def _maybe_stale_disconnect(self) -> None:
        # The stale timer keeps running while disconnected, so an unreachable
        # bridge clears the position after stale_after_s just like silence does.
        # Nothing extra to do here; the hook exists for symmetry/clarity.
        return
