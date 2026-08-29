"""SQLite persistence for the openHop Companion node.

openHop's stores (contacts, channels, prefs, offline message queue) are in-memory;
this module makes them survive restarts, using the persistence hooks openHop
deliberately exposes (FrameServer `_persist_*`/`_save_*`/`_sync_next_from_persistence`,
CompanionBase `_save_prefs`) plus a periodic flush.

The periodic flush exists because the FrameServer's fine-grained persist hooks only
fire while a Companion client is connected (push callbacks are registered per client
connection), and PATH-learned route updates mutate contacts without any save hook.
The flush closes both gaps; the hooks keep the loss window small while connected.

FAIL CLOSED: a database that exists but cannot be opened/read aborts startup. A
temporary read error must never be treated as "empty database" — that would later
overwrite valid state with an empty one.

Nothing here logs message text, keys, or coordinates.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sqlite3
import threading
from dataclasses import asdict
from pathlib import Path
from typing import Any, Optional

from openhop_core.companion.companion_radio import CompanionRadio
from openhop_core.companion.frame_server.server import CompanionFrameServer
from openhop_core.companion.models import Channel, QueuedMessage

logger = logging.getLogger("meshcore-host.persistence")

SCHEMA_VERSION = 1
FLUSH_INTERVAL_S = 30.0

# NodePrefs fields that are Companion state worth keeping. Radio parameters are NOT
# persisted: LHPC's generated config owns them, and a stale persisted copy would
# silently override an operator's config change. Position prefs (latitude, longitude,
# advert_loc_policy) are ALSO excluded: LHPC owns GPS policy, and persisting a live
# moving coordinate would resurrect it as a stale static position after restart.
PERSISTED_PREFS = (
    "node_name", "adv_type",
    "multi_acks", "telemetry_mode_base", "telemetry_mode_location",
    "telemetry_mode_environment", "manual_add_contacts", "autoadd_config",
    "autoadd_max_hops", "rx_delay_base", "airtime_factor", "client_repeat",
    "path_hash_mode", "default_scope_name",
)
# bytes-typed pref persisted hex-encoded.
PERSISTED_PREFS_HEX = ("default_scope_key",)


class StoreError(RuntimeError):
    """The database is present but unusable. Startup must abort, not continue empty."""


class CompanionStore:
    """SQLite-backed store. All writes are upserts; restore never mints state."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._conn: Optional[sqlite3.Connection] = None
        # flush() is allowed only after restore() completed: flushing the
        # still-empty in-memory stores over a database whose restore FAILED
        # would be exactly the "read error becomes empty store" data loss this
        # module promises cannot happen.
        self._restored = False
        # openHop runs _sync_next_from_persistence in a worker thread
        # (asyncio.to_thread), so the connection is shared across threads and
        # every operation takes this lock.
        self._lock = threading.RLock()

    # -- lifecycle -----------------------------------------------------------

    def open(self) -> None:
        existed = self.path.exists()
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            if not existed:
                # Pre-create 0600 so no window exists where channel PSKs or
                # message text sit world-readable (WAL/SHM sidecars inherit the
                # main file's mode).
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                os.close(fd)
            conn = sqlite3.connect(
                self.path, isolation_level=None, check_same_thread=False
            )
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            check = conn.execute("PRAGMA integrity_check").fetchone()
            if not check or check[0] != "ok":
                conn.close()
                raise StoreError(f"companion database failed integrity check: {self.path}")
            self._create_schema(conn)
        except (sqlite3.Error, OSError) as exc:
            raise StoreError(
                f"cannot open companion database {self.path}: {exc} — refusing to "
                f"continue with what could wrongly look like an empty store"
            ) from exc
        self._conn = conn
        logger.info("Companion store open: %s (existed=%s)", self.path, existed)

    def _create_schema(self, conn: sqlite3.Connection) -> None:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS contacts ("
            " public_key TEXT PRIMARY KEY, data TEXT NOT NULL)"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS channels ("
            " idx INTEGER PRIMARY KEY, name TEXT NOT NULL, secret BLOB NOT NULL)"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS prefs (key TEXT PRIMARY KEY, value TEXT)"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS messages ("
            " id INTEGER PRIMARY KEY AUTOINCREMENT, data TEXT NOT NULL)"
        )
        row = conn.execute("SELECT value FROM meta WHERE key='schema'").fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO meta (key, value) VALUES ('schema', ?)",
                (str(SCHEMA_VERSION),),
            )
        elif int(row[0]) != SCHEMA_VERSION:
            raise StoreError(
                f"companion database schema {row[0]} != supported {SCHEMA_VERSION}"
            )

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            except sqlite3.Error:
                logger.exception("Closing companion store failed")
            self._conn = None

    def _db(self) -> sqlite3.Connection:
        if self._conn is None:
            raise StoreError("companion store is not open")
        return self._conn

    # -- restore (startup) ---------------------------------------------------

    async def restore(self, companion: CompanionRadio) -> None:
        """Rebuild in-memory stores from the database. Raises on ANY read error."""
        try:
            with self._lock:
                db = self._db()
                contact_rows = db.execute("SELECT data FROM contacts").fetchall()
                channel_rows = db.execute(
                    "SELECT idx, name, secret FROM channels"
                ).fetchall()
                pref_rows = db.execute("SELECT key, value FROM prefs").fetchall()
        except sqlite3.Error as exc:
            raise StoreError(f"cannot read companion database: {exc}") from exc

        records = []
        for (data,) in contact_rows:
            try:
                records.append(json.loads(data))
            except ValueError as exc:
                raise StoreError(f"corrupt contact row in companion database: {exc}") from exc
        if records:
            companion.contacts.load_from_dicts(records)

        for idx, name, secret in channel_rows:
            if not companion.channels.set(int(idx), Channel(name=name, secret=bytes(secret))):
                logger.warning("Could not restore channel idx=%s", idx)

        prefs = companion.prefs
        for key, value in pref_rows:
            try:
                if key in PERSISTED_PREFS_HEX:
                    setattr(prefs, key, bytes.fromhex(value))
                elif key in PERSISTED_PREFS:
                    current = getattr(prefs, key)
                    setattr(prefs, key, type(current)(json.loads(value)))
            except (ValueError, TypeError) as exc:
                raise StoreError(f"corrupt pref row {key!r} in companion database: {exc}") from exc

        self._restored = True
        logger.info(
            "Restored companion state: %d contacts, %d channels, %d prefs, %d queued messages",
            len(records), len(channel_rows), len(pref_rows), self.queued_message_count(),
        )

    # -- save paths ----------------------------------------------------------

    def save_contact(self, contact: Any) -> None:
        rec = self._contact_record(contact)
        if rec is None:
            return
        try:
            with self._lock:
                self._db().execute(
                    "INSERT INTO contacts (public_key, data) VALUES (?, ?) "
                    "ON CONFLICT(public_key) DO UPDATE SET data=excluded.data",
                    (rec["public_key"], json.dumps(rec)),
                )
        except sqlite3.Error:
            logger.exception("Persisting contact failed")

    @staticmethod
    def _contact_record(contact: Any) -> Optional[dict]:
        try:
            pub = contact.public_key
            pub_hex = pub.hex() if isinstance(pub, bytes) else str(pub)
            out_path = contact.out_path
            advert_pkt = getattr(contact, "last_advert_packet", None)
            return {
                "public_key": pub_hex,
                "name": contact.name,
                "adv_type": contact.adv_type,
                "flags": contact.flags,
                "out_path_len": contact.out_path_len,
                "out_path": out_path.hex() if isinstance(out_path, bytes) else out_path,
                "last_advert_timestamp": contact.last_advert_timestamp,
                "lastmod": contact.lastmod,
                "gps_lat": contact.gps_lat,
                "gps_lon": contact.gps_lon,
                "sync_since": contact.sync_since,
                "last_advert_packet": advert_pkt.hex() if isinstance(advert_pkt, bytes) else None,
            }
        except AttributeError:
            logger.exception("Contact not serializable")
            return None

    def save_contacts(self, companion: CompanionRadio) -> None:
        try:
            records = companion.contacts.to_dicts()
        except Exception:
            logger.exception("Exporting contacts failed")
            return
        with self._lock:
            try:
                db = self._db()
                db.execute("BEGIN")
                db.execute("DELETE FROM contacts")
                db.executemany(
                    "INSERT INTO contacts (public_key, data) VALUES (?, ?)",
                    [(r["public_key"], json.dumps(r)) for r in records],
                )
                db.execute("COMMIT")
            except sqlite3.Error:
                # ROLLBACK under the SAME lock hold: releasing it first would let
                # a worker-thread pop_message() join the open transaction and have
                # its delete reverted (duplicate delivery).
                logger.exception("Persisting contacts failed")
                try:
                    db.execute("ROLLBACK")
                except sqlite3.Error:
                    pass

    def save_channels(self, companion: CompanionRadio) -> None:
        with self._lock:
            try:
                db = self._db()
                db.execute("BEGIN")
                db.execute("DELETE FROM channels")
                for idx in range(companion.channels.max_channels):
                    channel = companion.channels.get(idx)
                    if channel is None:
                        continue
                    db.execute(
                        "INSERT INTO channels (idx, name, secret) VALUES (?, ?, ?)",
                        (idx, channel.name, channel.secret),
                    )
                db.execute("COMMIT")
            except sqlite3.Error:
                logger.exception("Persisting channels failed")
                try:
                    db.execute("ROLLBACK")
                except sqlite3.Error:
                    pass

    def save_prefs(self, prefs: Any) -> None:
        try:
            values = asdict(prefs)
        except TypeError:
            values = {k: getattr(prefs, k, None) for k in PERSISTED_PREFS + PERSISTED_PREFS_HEX}
        rows = []
        for key in PERSISTED_PREFS:
            if key in values and values[key] is not None:
                rows.append((key, json.dumps(values[key])))
        for key in PERSISTED_PREFS_HEX:
            value = values.get(key)
            if isinstance(value, bytes):
                rows.append((key, value.hex()))
        try:
            with self._lock:
                self._db().executemany(
                    "INSERT INTO prefs (key, value) VALUES (?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    rows,
                )
        except sqlite3.Error:
            logger.exception("Persisting prefs failed")

    # -- offline message queue ----------------------------------------------

    # QueuedMessage field names; push.py's msg_dict carries extras (packet_hash)
    # that the dataclass does not accept, and bytes fields need hex round-trips.
    _MSG_FIELDS = (
        "sender_key", "txt_type", "timestamp", "text", "is_channel", "channel_idx",
        "path_len", "snr", "rssi", "channel_data_type", "channel_data_payload",
        "sender_prefix",
    )
    _MSG_BYTES_FIELDS = ("sender_key", "channel_data_payload", "sender_prefix")

    def push_message(self, msg_dict: dict) -> bool:
        """Persist one queued message. Returns False when the write failed (the
        caller must then leave the in-memory entry alone)."""
        record = {}
        for key in self._MSG_FIELDS:
            if key not in msg_dict or msg_dict[key] is None:
                continue
            value = msg_dict[key]
            if key in self._MSG_BYTES_FIELDS and isinstance(value, bytes):
                value = value.hex()
            record[key] = value
        try:
            with self._lock:
                self._db().execute(
                    "INSERT INTO messages (data) VALUES (?)", (json.dumps(record),)
                )
            return True
        except (sqlite3.Error, TypeError, ValueError):
            logger.exception("Persisting queued message failed")
            return False

    def pop_message(self) -> Optional[dict]:
        # Validate BEFORE deleting: a corrupt row must not take the rest of the
        # queue down with it — drop only that row and serve the next one.
        while True:
            try:
                with self._lock:
                    db = self._db()
                    row = db.execute(
                        "SELECT id, data FROM messages ORDER BY id LIMIT 1"
                    ).fetchone()
                    if row is None:
                        return None
                    try:
                        record = json.loads(row[1])
                        if not isinstance(record, dict):
                            raise ValueError("queued message is not an object")
                    except ValueError:
                        logger.exception("Corrupt queued message dropped (id=%s)", row[0])
                        db.execute("DELETE FROM messages WHERE id=?", (row[0],))
                        continue
                    db.execute("DELETE FROM messages WHERE id=?", (row[0],))
            except (sqlite3.Error, StoreError):
                logger.exception("Reading queued message failed")
                return None
            for key in self._MSG_BYTES_FIELDS:
                if isinstance(record.get(key), str):
                    try:
                        record[key] = bytes.fromhex(record[key])
                    except ValueError:
                        logger.warning("Corrupt bytes field %r in queued message", key)
                        record[key] = b""
            return record

    def queued_message_count(self) -> int:
        try:
            with self._lock:
                row = self._db().execute("SELECT COUNT(*) FROM messages").fetchone()
            return int(row[0])
        except sqlite3.Error:
            return 0

    # -- full flush ----------------------------------------------------------

    async def flush(self, companion: CompanionRadio) -> None:
        if self._conn is None or not self._restored:
            # Startup failed before open()/restore() completed: writing the
            # (empty) in-memory state now would destroy the very data the
            # failed restore refused to misread.
            return
        self.save_contacts(companion)
        self.save_channels(companion)
        self.save_prefs(companion.prefs)
        self.drain_message_queue(companion)

    def drain_message_queue(self, companion: CompanionRadio) -> None:
        """Move in-memory offline messages into the database.

        The FrameServer hook persists messages only while a client is
        connected (push callbacks are per-connection); this drain covers the
        clientless store-and-forward case so a restart cannot lose them. On a
        failed write the entry goes back into the memory queue."""
        queue = companion.message_queue
        moved = 0
        while True:
            msg = queue.pop()
            if msg is None:
                break
            record = {key: getattr(msg, key, None) for key in self._MSG_FIELDS}
            if self.push_message(record):
                moved += 1
            else:
                queue.push(msg)
                break
        if moved:
            logger.info("Persisted %d queued messages from memory", moved)


class PersistentCompanionRadio(CompanionRadio):
    """CompanionRadio whose prefs survive restarts (openHop `_save_prefs` hook)."""

    def __init__(self, *args, store: CompanionStore, **kwargs):
        self._store = store
        super().__init__(*args, **kwargs)

    def _save_prefs(self) -> None:
        self._store.save_prefs(self.prefs)


class PersistentFrameServer(CompanionFrameServer):
    """FrameServer whose contact/channel/message hooks write through to SQLite."""

    def attach_store(self, store: CompanionStore, companion: CompanionRadio) -> None:
        self._store = store
        self._companion = companion

    async def _persist_contact(self, contact) -> None:
        self._store.save_contact(contact)

    async def _save_contacts(self) -> None:
        self._store.save_contacts(self._companion)

    async def _save_channels(self) -> None:
        self._store.save_channels(self._companion)

    async def _persist_companion_message(self, msg_dict: dict, queue_entry=None) -> None:
        # The database becomes the sole offline queue: persist, then drop the
        # in-memory entry so _cmd_sync_next_message serves it from persistence
        # (and it survives a restart). On a failed write the entry stays in
        # memory — degraded to pre-persistence behaviour, never lost twice.
        if self._store.push_message(msg_dict) and queue_entry is not None:
            try:
                self.bridge.message_queue.remove(queue_entry)
            except Exception:
                # Entry not present / queue variant without remove: leave it; a
                # duplicate delivery risk is preferable to message loss.
                logger.debug("Could not remove persisted entry from memory queue",
                             exc_info=True)

    def _sync_next_from_persistence(self) -> Optional[QueuedMessage]:
        msg = self._store.pop_message()
        if msg is None:
            return None
        try:
            return QueuedMessage(**msg)
        except TypeError:
            logger.exception("Corrupt queued message dropped")
            return None


def install_persistence(
    server: CompanionFrameServer,
    companion: CompanionRadio,
    store: CompanionStore,
) -> None:
    if isinstance(server, PersistentFrameServer):
        server.attach_store(store, companion)


class PeriodicFlusher:
    """Background flush loop: closes the persistence gaps the hooks cannot cover
    (no client connected; PATH-learned route updates)."""

    def __init__(self, store: CompanionStore, companion: CompanionRadio,
                 interval_s: float = FLUSH_INTERVAL_S):
        self._store = store
        self._companion = companion
        self._interval = interval_s
        self._task: Optional[asyncio.Task] = None

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.get_running_loop().create_task(
                self._run(), name="companion store flush"
            )

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def _run(self) -> None:
        while True:
            await asyncio.sleep(self._interval)
            try:
                await self._store.flush(self._companion)
            except Exception:
                logger.exception("Periodic companion store flush failed")
