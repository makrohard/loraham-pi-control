"""Persistence: real stop → reconstruct → start cycles, and fail-closed behaviour.

The gate: after a restart the node has the SAME public identity, contacts (including
learned out_path routes), channels, and relevant prefs — and a Companion client can
keep using that state. A broken database must abort startup, never masquerade as
empty and destroy valid state on the next flush.
"""

import asyncio
import contextlib
import os

import pytest
from meshcore import MeshCore

from fake_loraham_daemon import FakeLoRaHAMDaemon
from meshcore_host.app import HostApp
from meshcore_host.persistence import CompanionStore, StoreError
from test_companion_integration import (
    host_config,
    inject_advert,
    make_peer,
    wait_for,
    write_identity,
)


@pytest.fixture
async def daemon(tmp_path):
    d = FakeLoRaHAMDaemon(tmp_path, tx=True)
    await d.start()
    yield d
    await d.close()


def db_path(tmp_path):
    return str(tmp_path / "state" / "companion.db")


async def start_app(tmp_path, daemon, key_file, **overrides):
    cfg = host_config(tmp_path, daemon, key_file, db=db_path(tmp_path), **overrides)
    app = HostApp(cfg)
    await app.start()
    assert await wait_for(lambda: app.radio.tx_ready)
    app.tcp_port = app.server._server.sockets[0].getsockname()[1]
    return app


async def test_full_restart_cycle_preserves_state(tmp_path, daemon):
    key_file, _ = write_identity(tmp_path)
    app = await start_app(tmp_path, daemon, key_file)
    pub_before = app.public_key_hex

    # Learn a contact via RF advert, with a client connected (so hooks fire too).
    mc = await MeshCore.create_tcp("127.0.0.1", app.tcp_port, default_timeout=5)
    peer = make_peer()
    await inject_advert(daemon, peer, "SURVIVOR")
    assert await wait_for(
        lambda: app.companion.contacts.get_by_key(peer.get_public_key()) is not None
    )
    # Learn a route: simulate a PATH update on the contact, then set a channel.
    contact = app.companion.contacts.get_by_key(peer.get_public_key())
    contact.out_path = b"\x11\x22"
    contact.out_path_len = 2
    res = await mc.commands.set_channel(2, "persisted", b"\x42" * 16)
    assert res.type.name == "OK"
    # The frame server stores the PSK padded to the firmware's 32-byte field;
    # the persistence invariant is bit-exact round-tripping of that stored form.
    secret_before = app.companion.channels.get(2).secret
    await mc.disconnect()

    await app.stop()  # flushes

    # Reconstruct from disk only.
    app2 = await start_app(tmp_path, daemon, key_file)
    try:
        assert app2.public_key_hex == pub_before
        restored = app2.companion.contacts.get_by_key(peer.get_public_key())
        assert restored is not None
        assert restored.name == "SURVIVOR"
        assert restored.out_path == b"\x11\x22"
        assert restored.out_path_len == 2
        ch = app2.companion.channels.get(2)
        assert ch is not None and ch.name == "persisted"
        assert ch.secret == secret_before
        assert ch.secret[:16] == b"\x42" * 16

        # A client can continue using the state.
        mc2 = await MeshCore.create_tcp("127.0.0.1", app2.tcp_port, default_timeout=5)
        res = await mc2.commands.get_contacts()
        assert peer.get_public_key().hex() in res.payload
        back = await mc2.commands.get_channel(2)
        assert back.payload["channel_name"] == "persisted"
        await mc2.disconnect()
    finally:
        await app2.stop()


async def test_unsynced_message_survives_restart(tmp_path, daemon):
    key_file, _ = write_identity(tmp_path)
    app = await start_app(tmp_path, daemon, key_file)
    mc = await MeshCore.create_tcp("127.0.0.1", app.tcp_port, default_timeout=5)

    peer = make_peer()
    await inject_advert(daemon, peer, "SENDER")
    assert await wait_for(
        lambda: app.companion.contacts.get_by_key(peer.get_public_key()) is not None
    )
    from openhop_core.companion.models import Contact
    from openhop_core.protocol.packet_builder import PacketBuilder
    us = Contact(public_key=app.public_key_hex, name="X")
    pkt, _ = PacketBuilder.create_text_message(us, peer, "survive me",
                                               message_type="flood")
    await daemon.send_rx(pkt.write_to())
    # Wait until the message hit the database (client connected => hook fired),
    # WITHOUT syncing it.
    assert await wait_for(lambda: app.store.queued_message_count() == 1)
    await mc.disconnect()
    await app.stop()

    app2 = await start_app(tmp_path, daemon, key_file)
    try:
        mc2 = await MeshCore.create_tcp("127.0.0.1", app2.tcp_port, default_timeout=5)
        got = None
        for _ in range(20):
            res = await mc2.commands.get_msg()
            if res.type.name == "CONTACT_MSG_RECV":
                got = res.payload
                break
            await asyncio.sleep(0.1)
        assert got is not None and got["text"] == "survive me"
        await mc2.disconnect()
    finally:
        await app2.stop()


async def test_periodic_flush_covers_clientless_learning(tmp_path, daemon):
    key_file, _ = write_identity(tmp_path)
    app = await start_app(tmp_path, daemon, key_file)
    # Shrink the flush interval for the test (restart: the first long sleep is
    # already scheduled).
    await app.flusher.stop()
    app.flusher._interval = 0.2
    app.flusher.start()
    peer = make_peer()
    # NO client connected: only the periodic flush persists this contact.
    await inject_advert(daemon, peer, "NOCLIENT")
    assert await wait_for(
        lambda: app.companion.contacts.get_by_key(peer.get_public_key()) is not None
    )
    store = CompanionStore(db_path(tmp_path))

    def persisted():
        with contextlib.suppress(Exception):
            row = app.store._db().execute(
                "SELECT COUNT(*) FROM contacts"
            ).fetchone()
            return row[0] == 1
        return False

    assert await wait_for(persisted, timeout=5.0)
    await app.stop()


async def test_corrupt_database_fails_closed(tmp_path, daemon):
    key_file, _ = write_identity(tmp_path)
    os.makedirs(tmp_path / "state", exist_ok=True)
    with open(db_path(tmp_path), "wb") as fh:
        fh.write(b"this is not a sqlite database" * 100)
    cfg = host_config(tmp_path, daemon, key_file, db=db_path(tmp_path))
    app = HostApp(cfg)
    with pytest.raises(StoreError):
        await app.start()
    # And nothing overwrote the broken file with an "empty" database.
    with open(db_path(tmp_path), "rb") as fh:
        assert fh.read(9) != b"SQLite fo"
    await app.stop()


async def test_wrong_schema_fails_closed(tmp_path, daemon):
    import sqlite3
    key_file, _ = write_identity(tmp_path)
    os.makedirs(tmp_path / "state", exist_ok=True)
    conn = sqlite3.connect(db_path(tmp_path))
    conn.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
    conn.execute("INSERT INTO meta VALUES ('schema', '999')")
    conn.commit()
    conn.close()
    cfg = host_config(tmp_path, daemon, key_file, db=db_path(tmp_path))
    app = HostApp(cfg)
    with pytest.raises(StoreError):
        await app.start()
    await app.stop()


async def test_position_prefs_never_persisted(tmp_path, daemon):
    key_file, _ = write_identity(tmp_path)
    app = await start_app(tmp_path, daemon, key_file)
    # Simulate a live position having been applied.
    app.companion.prefs.latitude = 51.5
    app.companion.prefs.longitude = -0.1
    app.companion.prefs.advert_loc_policy = 1
    await app.store.flush(app.companion)
    rows = dict(app.store._db().execute("SELECT key, value FROM prefs").fetchall())
    assert "latitude" not in rows
    assert "longitude" not in rows
    assert "advert_loc_policy" not in rows
    await app.stop()

    # After restart the position is gone (LHPC config decides it afresh).
    app2 = await start_app(tmp_path, daemon, key_file)
    try:
        assert app2.companion.prefs.latitude == 0.0
        assert app2.companion.prefs.advert_loc_policy == 0
    finally:
        await app2.stop()


async def test_failed_restore_never_flushes_empty_state(tmp_path, daemon):
    """P0 regression: a restore that fails must leave the database untouched —
    the teardown flush must NOT write the empty in-memory stores over it."""
    import json
    import sqlite3
    key_file, _ = write_identity(tmp_path)
    # Build a valid store holding one contact, then corrupt ONE row's JSON.
    app = await start_app(tmp_path, daemon, key_file)
    peer = make_peer()
    await inject_advert(daemon, peer, "PRECIOUS")
    assert await wait_for(
        lambda: app.companion.contacts.get_by_key(peer.get_public_key()) is not None
    )
    await app.stop()
    conn = sqlite3.connect(db_path(tmp_path))
    assert conn.execute("SELECT COUNT(*) FROM contacts").fetchone()[0] == 1
    conn.execute("UPDATE contacts SET data='{broken'")
    conn.commit()
    conn.close()

    cfg = host_config(tmp_path, daemon, key_file, db=db_path(tmp_path))
    app2 = HostApp(cfg)
    with pytest.raises(StoreError):
        await app2.start()
    await app2.stop()  # the run_until_signal teardown path calls this too

    conn = sqlite3.connect(db_path(tmp_path))
    rows = conn.execute("SELECT data FROM contacts").fetchall()
    conn.close()
    assert len(rows) == 1, "failed restore must not delete the stored contacts"
    assert rows[0][0] == "{broken", "failed restore must leave the data untouched"


async def test_clientless_messages_survive_restart_via_flush(tmp_path, daemon):
    """Messages received while NO client is connected must still survive a
    restart (the flush drains the in-memory queue into the database)."""
    from openhop_core.companion.models import Contact
    from openhop_core.protocol.packet_builder import PacketBuilder
    key_file, _ = write_identity(tmp_path)
    app = await start_app(tmp_path, daemon, key_file)
    peer = make_peer()
    await inject_advert(daemon, peer, "GHOSTSENDER")
    assert await wait_for(
        lambda: app.companion.contacts.get_by_key(peer.get_public_key()) is not None
    )
    us = Contact(public_key=app.public_key_hex, name="X")
    pkt, _ = PacketBuilder.create_text_message(us, peer, "stored and forwarded",
                                               message_type="flood")
    await daemon.send_rx(pkt.write_to())
    assert await wait_for(lambda: app.companion.message_queue.count == 1)
    await app.stop()  # flush drains the queue into the DB

    app2 = await start_app(tmp_path, daemon, key_file)
    try:
        mc = await MeshCore.create_tcp("127.0.0.1", app2.tcp_port, default_timeout=5)
        got = None
        for _ in range(20):
            res = await mc.commands.get_msg()
            if res.type.name == "CONTACT_MSG_RECV":
                got = res.payload
                break
            await asyncio.sleep(0.1)
        assert got is not None and got["text"] == "stored and forwarded"
        await mc.disconnect()
    finally:
        await app2.stop()


async def test_corrupt_queued_row_does_not_block_the_queue(tmp_path, daemon):
    """A corrupt messages row is dropped alone; valid ones behind it deliver."""
    key_file, _ = write_identity(tmp_path)
    app = await start_app(tmp_path, daemon, key_file)
    store = app.store
    store._db().execute("INSERT INTO messages (data) VALUES ('{broken')")
    assert store.push_message({"sender_key": b"\x01" * 32, "text": "good",
                              "timestamp": 1, "txt_type": 0})
    record = store.pop_message()
    assert record is not None and record["text"] == "good"
    assert store.pop_message() is None
    await app.stop()


async def test_persisted_node_name_never_overrides_configuration(tmp_path, daemon):
    """LHPC owns the node name (meshcore.toml). A legacy `node_name` row written by an older
    build must NOT resurrect and override the configured name on restart, and must be purged
    from the store so it can never override again. Mirrors the radio/GPS ownership exclusions."""
    import json
    import sqlite3

    from meshcore_host.persistence import CompanionStore

    key_file, _ = write_identity(tmp_path)
    # Simulate an OLD database that persisted node_name (a build before the ownership fix).
    legacy = CompanionStore(db_path(tmp_path))
    legacy.open()
    legacy._db().execute(
        "INSERT INTO prefs (key, value) VALUES (?, ?)", ("node_name", json.dumps("OLD-NAME"))
    )
    legacy.close()

    # Boot with a DIFFERENT configured name; restore must keep the configured one.
    app = await start_app(tmp_path, daemon, key_file, name="NEW-NAME")
    try:
        assert app.companion.prefs.node_name == "NEW-NAME", \
            "a persisted node_name must never override the LHPC-configured name"
    finally:
        await app.stop()

    # The legacy row is gone, so it can never override on a later restart either.
    conn = sqlite3.connect(db_path(tmp_path))
    try:
        row = conn.execute("SELECT value FROM prefs WHERE key = 'node_name'").fetchone()
    finally:
        conn.close()
    assert row is None, "legacy node_name row must be purged on restore"


async def test_unrestorable_channel_fails_closed_and_is_preserved(tmp_path, daemon):
    """A persisted channel that cannot be represented in memory (out-of-range index) must abort
    startup fail-closed, leaving the SQLite row untouched — never silently destroyed by the next
    flush's DELETE-then-rewrite. Mirrors the corrupt-database fail-closed contract."""
    import sqlite3

    from meshcore_host.persistence import CompanionStore

    key_file, _ = write_identity(tmp_path)
    # Seed a VALID SQLite channel row the in-memory store cannot hold (idx >> max_channels=40).
    store = CompanionStore(db_path(tmp_path))
    store.open()
    store._db().execute(
        "INSERT INTO channels (idx, name, secret) VALUES (?, ?, ?)",
        (9999, "GHOST", b"\x11" * 16),
    )
    store.close()

    cfg = host_config(tmp_path, daemon, key_file, db=db_path(tmp_path))
    app = HostApp(cfg)
    with pytest.raises(StoreError):
        await app.start()
    await app.stop()

    # The row survives bit-for-bit — startup aborted before any flush could delete it.
    conn = sqlite3.connect(db_path(tmp_path))
    try:
        row = conn.execute(
            "SELECT idx, name, secret FROM channels WHERE idx = 9999"
        ).fetchone()
    finally:
        conn.close()
    assert row == (9999, "GHOST", b"\x11" * 16)
