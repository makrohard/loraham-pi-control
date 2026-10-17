"""The one-store MeshCore repeater settings: they live ONLY in openHop's own file.

openHop keeps its own configuration in `<runtime>/state/openhop/config.yaml` (the host's
meshcore_host.openhop_file). Five settings the repeater shares with LHPC live there and nowhere
else: the repeater name, its behaviour, the dashboard password, the repeater identity and the duty
cycle (docs/openhop-on-lhpc.md). LHPC owns them while the repeater is stopped, openHop while it
runs; one store, so LHPC Settings and openHop never differ.

LHPC reads and renders the file only through the host's one helper (`python -m
meshcore_host.openhop_file` in the stack venv, which carries PyYAML), and writes it only under the
EXCLUSIVE config lock with no node process and no unverified stop: a Settings save commits the
rows as one target of its own config transaction (`rows_target`), so a refusal anywhere in the
save writes neither store. `ensure` runs after a MeshCore start's admission and before its shared
config guard: it moves a pre-issue-5 value (the stack config, the secret files) into the file,
mints what a repeater needs and the file lacks, and removes each legacy copy only after the file
and its directory are durable.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from . import config as cfgmod
from . import meshcore_identity as _mci
from .meshcore_mode import NODE_ID, STACK_ID

# param name -> (openHop key, scale): the LHPC Settings row and where it lives in the file.
# `airtime` is a percentage in LHPC and milliseconds per minute in openHop's duty_cycle section.
ROWS = {
    "repeater_name": ("repeater.node_name", None),
    "repeater_mode": ("repeater.mode", None),
    "airtime": ("duty_cycle.max_airtime_per_minute", 600),
    "dashboard_password": ("repeater.security.admin_password", None),
    "repeater_key": ("repeater.identity_key", None),
}
_SECRET_ROWS = {"dashboard_password": _mci.REPEATER_ADMIN_FILENAME,
                "repeater_key": _mci.REPEATER_IDENTITY_FILENAME}
RUNNING = "the repeater is running: change it in openHop"
# The host's text for the same refusal at start (meshcore_host/repeater.py CHAT_NAME_TAKEN).
CHAT_NAME_TAKEN = ("An openHop companion already uses this name with a different identity key; "
                   "remove the old entry in openHop or choose another name.")
UNVERIFIED = ("The repeater stop is not verified; try again after the stop is verified. "
              "(lhpc status meshcore)")
TIMEOUT_S = 20


class OneStoreError(ValueError):
    """A one-store read or write could not be done; nothing legacy was removed."""


def file_path(paths) -> Path:
    return Path(paths.runtime_root) / "state" / "openhop" / "config.yaml"


def _python(svc) -> Path:
    st = svc.stack(STACK_ID)
    comp = st.component(NODE_ID) if st is not None else None
    root = svc._lifecycle().source_dir(comp) if comp is not None else None
    py = Path(root) / ".venv" / "bin" / "python" if root else None
    if py is None or not py.exists():
        raise OneStoreError("the MeshCore stack is not built (its venv carries openHop's file helper)")
    return py


def _helper(svc, *args: str, stdin: str = "") -> str:
    proc = subprocess.run([str(_python(svc)), "-m", "meshcore_host.openhop_file", *args],
                          input=stdin, capture_output=True, text=True, timeout=TIMEOUT_S,
                          check=False)
    if proc.returncode != 0:
        raise OneStoreError(proc.stderr.strip() or f"openhop_file {args[0]} failed")
    return proc.stdout


def read(svc, keys) -> dict:
    if not file_path(svc._paths).exists():
        return dict.fromkeys(keys)
    return json.loads(_helper(svc, "get", str(file_path(svc._paths)), *keys))


def to_row(name: str, value):
    """openHop's value as the LHPC row shows it ('' when the file has none)."""
    if value is None:
        return ""
    scale = ROWS[name][1]
    return str(round(value / scale)) if scale else str(value)


def to_file(name: str, text: str):
    scale = ROWS[name][1]
    return round(float(text) * scale) if scale else text


def blocked(svc) -> str:
    """'' when LHPC may write the file (no node process, no unverified stop), else the reason."""
    svc.build_snapshot(fresh=True)
    if svc.stack_running(STACK_ID):
        return RUNNING
    if svc._lifecycle().owned_records(NODE_ID):
        return UNVERIFIED
    return ""


def _set(svc, values: dict) -> None:
    _helper(svc, "set", str(file_path(svc._paths)), stdin=json.dumps(values))


_ROWS_SEEN: dict = {}          # file path -> ((inode, mtime_ns, size), every row key's value)


def _rows(svc) -> dict:
    """Every row key's value in ONE helper call, reused while the file is unchanged (a Settings
    render reads five rows: one subprocess, not five). Any write replaces the file (a new inode)
    or changes its mtime, so a changed file is read again."""
    p = file_path(svc._paths)
    st = p.stat()
    stamp = (st.st_ino, st.st_mtime_ns, st.st_size)
    seen = _ROWS_SEEN.get(str(p))
    if seen is None or seen[0] != stamp:
        seen = _ROWS_SEEN[str(p)] = (stamp, read(svc, [k for k, _s in ROWS.values()]))
    return seen[1]


def row_value(svc, name: str, legacy: str) -> str:
    """The stopped row's value: openHop's file if it holds the key, else the legacy source
    (the pre-first-start migration state)."""
    try:
        value = _rows(svc).get(ROWS[name][0])
    except (OSError, OneStoreError):
        value = None
    return to_row(name, value) if value is not None else legacy


def chat_name_taken(svc, name: str) -> bool:
    """Plan §13: a companion in openHop's file other than LHPC's chat node (found by the chat
    node's identity key) already carries `name`. False without a file or a name."""
    if not name or not file_path(svc._paths).exists():
        return False
    companions = read(svc, ["identities.companions"]).get("identities.companions") or []
    mine = _mci.read_secret(svc._paths, _mci.IDENTITY_FILENAME)
    return any(isinstance(c, dict) and c.get("name") == name
               and str(c.get("identity_key") or "").strip().lower() != mine
               for c in companions)


def rows_target(svc, values: dict) -> tuple:
    """The config-transaction target that writes the named rows (each exactly its own key) into
    openHop's file. Rendered INSIDE the transaction's exclusive lock: refused while the repeater
    runs or its stop is unverified, and committed or rolled back together with every other target
    of the same save (a refusal anywhere in the save writes nothing)."""
    keys = {ROWS[n][0]: to_file(n, v) for n, v in values.items()}

    def render(_paths):
        why = blocked(svc)
        if why:
            raise cfgmod.ConfigError(why)
        try:
            return _helper(svc, "render", str(file_path(svc._paths)), stdin=json.dumps(keys))
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            raise cfgmod.ConfigError(f"openHop's config file: {exc}") from exc
    return ("openhop", file_path(svc._paths), render, 0o600)


def save_rows(svc, values: dict) -> str:
    """The rows alone, as their own transaction. '' on success, else the refusal (nothing written)."""
    try:
        cfgmod.apply_config_transaction(svc._paths, [rows_target(svc, values)])
    except cfgmod.ConfigError as exc:
        return str(exc)
    return ""


def ensure(svc, legacy: dict, airtime_default: str) -> None:
    """Before a repeater-mode start: every one-store key present in openHop's file. `legacy` maps
    row name -> the pre-issue-5 stored value (or ''). Legacy copies are removed only after the
    write AND the fsyncs succeeded; any failure raises with everything legacy intact."""
    with cfgmod.config_lock(svc._paths):
        why = blocked(svc)
        if why:
            raise OneStoreError(why)
        have = read(svc, [k for k, _s in ROWS.values()])
        missing = {}
        for name, (key, _scale) in ROWS.items():
            if have.get(key) is not None:
                continue
            value = legacy.get(name, "")
            if not value and name in _SECRET_ROWS:
                value = _mci.read_secret(svc._paths, _SECRET_ROWS[name])
            if not value:
                value = _mint(name, airtime_default)
            if value:
                missing[key] = to_file(name, value)
        if have.get("repeater.node_name") is None and "repeater.node_name" not in missing:
            raise OneStoreError("set the repeater name in LHPC Settings (Repeater) first")
        if missing:
            _set(svc, missing)
        _helper(svc, "sync", str(file_path(svc._paths)))
        # durable: now the legacy copies go, for every key the file holds
        for filename in _SECRET_ROWS.values():
            _mci.remove_secret(svc._paths, filename)
        cfgmod.remove_stack_keys(svc._paths, STACK_ID, NODE_ID, [n for n in ROWS if n not in _SECRET_ROWS])


def _mint(name: str, airtime_default: str):
    if name == "repeater_key":
        return _mci.mint_identity()
    if name == "dashboard_password":
        return _mci.mint_password(_mci.REPEATER_ADMIN_FILENAME)
    if name == "airtime":
        return airtime_default
    if name == "repeater_mode":
        return "forward"
    return ""


def for_start(svc, target: str) -> bool:
    """Does a start of `target` run the repeater (the one-store steps apply)?"""
    from .meshcore_mode import repeater_on
    return target in (STACK_ID, NODE_ID) and repeater_on(svc.meshcore_mode())


def start_ensure(svc, target: str) -> None:
    """The start's step, after admission and before its shared config guard: `ensure` with the
    legacy stack-config values. Any failure refuses the start as a OneStoreError."""
    if not for_start(svc, target):
        return
    legacy = {n: svc._stored_param_value(STACK_ID, "file", NODE_ID, n)
              for n in ("repeater_name", "repeater_mode", "airtime")}
    _c, p, _e = svc._param_ref(STACK_ID, "file", f"{NODE_ID}.airtime")
    try:
        ensure(svc, legacy, p.default if p is not None else "10")
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        raise OneStoreError(f"openHop's config file: {exc}") from exc


def split_saves(svc, target: str, params: list) -> tuple:
    """A settings save: once openHop's file exists, the one-store rows go there, as one more
    target of the save's own transaction (never cleared). Returns (refusal ActionResult or None,
    the params left for LHPC's own store, the targets to commit with them)."""
    rows = [ch for ch in params if ch.kind == "f" and ch.comp.id == NODE_ID and ch.param.name in ROWS]
    if not rows or not file_path(svc._paths).exists():
        return None, params, []
    if any(str(ch.value).strip() == "" for ch in rows):
        from .service_base import ActionResult
        return ActionResult(False, f"Config not saved for '{target}'.",
                            details=["a repeater setting kept in openHop's file cannot be cleared"]), [], []
    left = [ch for ch in params if ch not in rows]
    return None, left, [rows_target(svc, {ch.param.name: str(ch.value) for ch in rows})]


def rendered(svc, name: str, target: str):
    """The host-config value generation renders for a meshcore-node param of this feature, or
    None for any other param: a one-store row is never rendered (the host reads openHop's file);
    `openhop_use_gps` is derived from the stack's `use_gps` switch."""
    if name in ROWS:
        return ""
    if name == "openhop_use_gps":
        return "on" if svc.gps_enabled_for(target) else "off"
    if name == "chat_node_id":                          # the pure repeater's handle, never secret
        try:
            return key_id(_mci.read_secret(svc._paths, _mci.IDENTITY_FILENAME))
        except (OSError, ValueError):
            return ""                                    # no usable chat key: nothing is "mine"
    return None


def key_id(key_hex: str) -> str:
    """The chat key's non-secret handle; the host's openhop_file.key_id is the same rule."""
    import hashlib
    return hashlib.sha256(key_hex.strip().lower().encode()).hexdigest()[:16] if key_hex else ""


def chat_name_refusal(svc, current: dict, merged: dict) -> str:
    """Plan §13 at save: in chat+repeater the chat node is an entry of openHop's companion list,
    so a save that sets its name or turns that mode on is refused when another companion there
    carries the name (the host refuses the same at start). "" = fine; an unreadable file refuses."""
    from .meshcore_mode import normalize
    new, old = ({n: svc._resolve_stored(d, "f", NODE_ID, n, 1)[0] for n in ("mode", "node_name")}
                for d in (merged, current))
    if new == old or normalize(new["mode"]) != "chat+repeater":
        return ""
    try:
        return CHAT_NAME_TAKEN if chat_name_taken(svc, str(new["node_name"] or "")) else ""
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        return f"openHop's config file could not be read, so the chat node's name cannot be checked ({exc})"


def password_view(svc, comp) -> dict | None:
    """The Password section once openHop's file exists (it holds the dashboard password), else
    None (before the first repeater-mode start the legacy secret file is shown, and for any other
    component's password)."""
    if not comp.ui_password_file.endswith(_mci.REPEATER_ADMIN_FILENAME) or not file_path(svc._paths).exists():
        return None
    try:
        value = read(svc, ["repeater.security.admin_password"]).get(
            "repeater.security.admin_password") or None
        reason = "" if value else "not set in openHop's config file"
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        value, reason = None, f"openHop's config file is unreadable ({exc})"
    return {"user": comp.ui_user or "admin", "path": str(file_path(svc._paths)),
            "exists": value is not None, "value": value, "reason": reason, "edit_command": "",
            "note": comp.ui_password_note}


def start_warnings(svc, target: str) -> list:
    """The `[warning]` lines of a repeater-mode start (plan DELTA-2 a): a one-store value written
    the upstream way that LHPC's own rules would not accept is neither refused nor overwritten,
    only shown. [] when there is no file yet or it cannot be read (the ensure step reports that)."""
    if not for_start(svc, target) or not file_path(svc._paths).exists():
        return []
    try:
        got = read(svc, ["repeater.node_name", "repeater.security.admin_password"])
    except (OSError, ValueError, subprocess.SubprocessError):
        return []
    out = []
    name = str(got.get("repeater.node_name") or "").strip()
    call = str(svc.config().operator.callsign or "").strip()
    if name and call and name.casefold() == call.casefold():
        out.append("Repeater name equals the operator callsign (set in openHop): give the repeater "
                   "its own name")
    password = got.get("repeater.security.admin_password")
    if password and not _mci._normalize_password(password):
        out.append("Dashboard password (set in openHop) is outside LHPC's rule: 15-128 printable "
                   "ASCII characters, no spaces")
    return out
