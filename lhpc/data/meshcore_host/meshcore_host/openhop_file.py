"""openHop's own configuration file in the repeater roles: `<state_dir>/config.yaml`.

openHop keeps every setting LHPC does not own in its own file, saved the upstream way (its
dashboard, CLI, Glass). At every start the host reads that file with upstream's loader, puts
LHPC's values over the keys LHPC owns (`OWNED`), and writes the merged config back with a
marker block on top, so the file always shows what runs (docs/openhop-on-lhpc.md).

The ONE-STORE settings (`ONE_STORE`) live only in this file: LHPC Settings writes them while
the repeater is stopped (through `set_keys`, called by the controller under its config lock via
`python -m meshcore_host.openhop_file`), openHop while it runs. The host never overrides them.

Every write is atomic and durable: a temp file (0600) in the same directory, fsync, rename, then
an fsync of the directory, and of each parent whose entry a new directory added.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Iterable

MARKER_KEY = "lhpc_managed"
FILE_NAME = "config.yaml"

# The keys LHPC owns: replaced at every start, a change refused by the guards. Whole sections are
# replaced, so a field upstream adds to one of them is covered too.
OWNED_SECTIONS = ("radio_type", "radio", "http", "storage")
REMOVED_SECTIONS = ("radios", "kiss", "modem_usb", "modem_tcp")
OWNED_KEYS = ("glass.cert_store_dir", "setup_completed", "repeater.security.jwt_secret")

# Lives only in this file (LHPC writes it while the repeater is stopped).
ONE_STORE = ("repeater.node_name", "repeater.mode", "repeater.security.admin_password",
             "repeater.identity_key", "duty_cycle")
# Kept as bytes in the file (upstream's `LocalIdentity(seed=...)` takes bytes; YAML `!!binary`).
_BYTES_KEYS = ("repeater.identity_key",)

MARKER_NOTE = ("Set by LoRaHAM Pi Control at every start. Edits to the keys under \"keys\" are "
               "replaced; change them in LHPC Settings (MeshCore). Keys under \"lhpc_one_store\": "
               "change them in openHop (dashboard, CLI, Glass) while the repeater runs, or in LHPC "
               "Settings while it is stopped. Everything else is yours. Edit this file by hand only "
               "while the repeater is stopped: a running openHop rewrites the whole file on its "
               "next save.")


def path_in(state_dir: str | Path) -> Path:
    return Path(state_dir) / FILE_NAME


def key_id(key_hex: str) -> str:
    """A short, non-secret handle for an identity key. The controller passes the chat node's
    handle (`chat_node_id`) to the pure repeater, which is given no chat key, so it can still
    remove LHPC's chat-node entry after a mode change (same rule: core/openhop_store.key_id)."""
    return hashlib.sha256(key_hex.strip().lower().encode()).hexdigest()[:16] if key_hex else ""


def marker(use_gps: bool) -> dict:
    keys = list(OWNED_SECTIONS) + list(REMOVED_SECTIONS) + list(OWNED_KEYS)
    keys += ["identities.companions[LHPC chat node, by identity key]"]
    keys += ["gps"] if use_gps else ["gps.time_sync_enabled"]
    return {"note": MARKER_NOTE, "keys": keys, "lhpc_one_store": list(ONE_STORE)}


def get(conf: dict, dotted: str, default=None):
    node = conf
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    return node


def put(conf: dict, dotted: str, value) -> None:
    parts = dotted.split(".")
    node = conf
    for part in parts[:-1]:
        child = node.get(part)
        if not isinstance(child, dict):
            child = node[part] = {}
        node = child
    node[parts[-1]] = value


def with_marker_first(conf: dict, use_gps: bool) -> dict:
    """The dict upstream keeps in memory: the marker as its FIRST key, so upstream's own saves
    (which keep dict order) write it on top too."""
    out = {MARKER_KEY: marker(use_gps)}
    out.update((k, v) for k, v in conf.items() if k != MARKER_KEY)
    return out


def dump(conf: dict) -> str:
    import yaml                                   # the stack venv's PyYAML, upstream's own options
    return yaml.safe_dump(conf, default_flow_style=False, indent=2, width=1000000,
                          sort_keys=False, allow_unicode=True)


def _fsync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def ensure_dir(directory: Path) -> None:
    """Create `directory` (0700) and its missing parents; fsync the parent of every directory
    created, so the new entries survive a crash. An existing directory is tightened to 0700."""
    missing = []
    probe = directory
    while not probe.exists():
        missing.append(probe)
        probe = probe.parent
    for d in reversed(missing):
        os.mkdir(d, 0o700)
        _fsync_dir(d.parent)
    os.chmod(directory, 0o700)


def write_atomic(path: Path, text: str) -> None:
    """The one durable write: temp 0600, fsync, rename, directory fsync. Raises on any failure;
    the caller removes nothing that depends on it until this returns."""
    ensure_dir(path.parent)
    tmp = path.with_name(f".{path.name}.lhpc-tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise
    _fsync_dir(path.parent)


def read(path: Path) -> dict:
    import yaml
    if not path.exists():
        return {}
    doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(doc, dict):
        raise ValueError(f"{path} is not a mapping")
    return doc


def set_keys(path: Path, values: dict, use_gps: bool = True) -> None:
    """Set exactly the named dotted keys; every other key of the file stays as it is."""
    write_atomic(path, updated(path, values, use_gps))


def updated(path: Path, values: dict, use_gps: bool = True) -> str:
    """The file's text with exactly the named dotted keys set (nothing is written)."""
    conf = read(path)
    for key, value in values.items():
        if key in _BYTES_KEYS and isinstance(value, str):
            value = bytes.fromhex(value.strip())
        put(conf, key, value)
    if MARKER_KEY not in conf:
        conf = with_marker_first(conf, use_gps)
    return dump(conf)


def get_keys(path: Path, keys: Iterable[str]) -> dict:
    conf = read(path)
    out = {}
    for key in keys:
        value = get(conf, key)
        out[key] = value.hex() if isinstance(value, bytes) else value
    return out


def sync(path: Path) -> None:
    """Redo the durability steps for a file that already exists (a retry after a crash between
    the rename and the fsyncs must not skip them)."""
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    _fsync_dir(path.parent)
    _fsync_dir(path.parent.parent)


def _json_value(value):
    """Nested values JSON has no type for (a companion's key as bytes, a YAML date)."""
    return value.hex() if isinstance(value, bytes) else str(value)


def main(argv: list[str]) -> int:
    """The controller's entry (stack venv): `get PATH KEY...` prints JSON; `set PATH` reads a JSON
    object of dotted keys from stdin; `render PATH` prints the text `set` would write (the
    controller commits it in its config transaction); `sync PATH`. Exit 2 on a usage error, 1 on
    any failure."""
    if len(argv) < 2 or argv[0] not in ("get", "set", "render", "sync"):
        print("usage: openhop_file get PATH KEY... | set|render PATH < json | sync PATH", file=sys.stderr)
        return 2
    path = Path(argv[1])
    try:
        if argv[0] == "get":
            print(json.dumps(get_keys(path, argv[2:]), default=_json_value))
        elif argv[0] == "set":
            set_keys(path, json.loads(sys.stdin.read()))
        elif argv[0] == "render":
            sys.stdout.write(updated(path, json.loads(sys.stdin.read())))
        else:
            sync(path)
    except (OSError, ValueError) as exc:
        print(f"openhop_file {argv[0]}: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
