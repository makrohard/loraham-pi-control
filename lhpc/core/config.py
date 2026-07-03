"""Layered configuration.

Five concerns, kept strictly separate (see docs/operations.md):

  1. tracked defaults        lhpc/data/defaults.toml        (shipped package data)
  2. known-good profiles     lhpc/data/profiles.example.toml (catalogue; runtime: profiles/)
  3. local operator overrides <runtime>/config/local.toml   (git-ignored, operator settings + callsign)
  4. local secrets           <runtime>/config/secrets.toml  (git-ignored, mode 0600)
  5. generated runtime state  <runtime>/state/              (never sole source of truth)

This module loads and merges layers 1+3 into an effective `Config`, and reads
secrets (layer 4) separately and lazily. It never writes secrets and never emits
them in status output. Callsign and other operator identity live ONLY in the
runtime-local layer, never in the tracked repo.
"""

from __future__ import annotations

import fcntl
import json
import math
import os
import re
import tomllib
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from .assets import asset_path
from .paths import Paths, PathContainmentError

# Tracked defaults shipped with the controller (package data, wheel-safe).
_DEFAULTS_PATH = asset_path("defaults.toml")


class ConfigError(Exception):
    """A config file could not be parsed — surfaced as a diagnostic, never a crash."""


def _atomic_write(paths: Paths, path: Path, text: str, mode: int = 0o644) -> None:
    """Atomically write a RUNTIME-OWNED config leaf THROUGH the safe runtime FS
    (`runtime_fs.atomic_write`): containment, no-follow leaf, parent fsync. Runtime-state
    config writes never bypass `runtime_fs`; source-tree config generation uses a separate
    contained writer in the service layer."""
    from . import runtime_fs
    runtime_fs.atomic_write(paths, path, text, mode)


@contextmanager
def config_lock(paths: Paths):
    """Serialize config mutations within a runtime root (a single exclusive flock).
    The lock file is opened with O_NOFOLLOW so a symlinked `.lock` leaf is refused,
    and its path is containment-checked; if the lock cannot be acquired safely the
    mutation is blocked (the exception propagates), never silently bypassed."""
    from . import runtime_fs
    # Single safe API: contained path + O_NOFOLLOW open (a symlinked .lock leaf or an
    # escaping parent raises here, blocking mutation rather than being bypassed).
    fh = runtime_fs.open_lock(paths, paths.under("config", ".lock"))
    try:
        fcntl.flock(fh, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fh, fcntl.LOCK_UN)
        fh.close()


@dataclass(frozen=True)
class OperatorConfig:
    """Operator identity/settings — sourced ONLY from the runtime-local layer."""

    callsign: str = ""
    locator: str = ""

    @property
    def configured(self) -> bool:
        return bool(self.callsign)


@dataclass
class Config:
    """Effective configuration after merging defaults + local overrides."""

    values: dict = field(default_factory=dict)
    operator: OperatorConfig = field(default_factory=OperatorConfig)
    sources: dict = field(default_factory=dict)   # per-component runtime overrides
    remotes: dict = field(default_factory=dict)   # per-component GitHub remote overrides
    local_path: Path | None = None
    secrets_path: Path | None = None
    diagnostics: list = field(default_factory=list)   # config-parse problems (non-fatal)

    def get(self, section: str, key: str, default=None):
        # A hand-edited wrong-type section (e.g. `install = "x"`) must never crash a
        # caller with AttributeError — treat a non-table section as absent (safe default).
        sec = self.values.get(section, {})
        if not isinstance(sec, dict):
            return default
        return sec.get(key, default)


def _load_toml(path: Path) -> dict:
    """Parse an EXTERNAL toml (shipped package-data defaults). Runtime-owned toml uses
    `_load_runtime_toml` (descriptor-anchored, no-follow)."""
    if not path.is_file():
        return {}
    try:
        with path.open("rb") as fh:
            return tomllib.load(fh)
    except (tomllib.TOMLDecodeError, OSError) as exc:
        raise ConfigError(f"{path}: {exc}") from exc


def _load_runtime_toml(paths: Paths, path: Path) -> dict:
    """Parse a RUNTIME-OWNED toml leaf via a descriptor-anchored, NO-FOLLOW read
    (`runtime_fs.read_bytes`): an absent file -> {} (benign default); an unreadable,
    symlinked, escaping, or malformed file raises `ConfigError` so its content can NEVER
    contribute data from outside the runtime root and the caller surfaces a diagnostic."""
    from . import runtime_fs
    try:
        raw = runtime_fs.read_bytes(paths, path)
    except FileNotFoundError:
        return {}
    except (OSError, PathContainmentError) as exc:
        raise ConfigError(f"{path}: {exc}") from exc
    try:
        return tomllib.loads(raw.decode("utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
        raise ConfigError(f"{path}: {exc}") from exc


def _deep_merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def load_config(paths: Paths, defaults_path: Path | None = None) -> Config:
    """Merge tracked defaults with the runtime-local override layer (read-only)."""
    defaults = _load_toml(defaults_path or _DEFAULTS_PATH)
    local_path = paths.runtime_root / "config" / "local.toml"
    # Malformed operator config is a DIAGNOSTIC, not a crash: fall back to defaults
    # and surface the parse error so the operator can fix local.toml.
    diagnostics: list = []
    try:
        local = _load_runtime_toml(paths, local_path)
    except ConfigError as exc:
        local, diagnostics = {}, [f"ignored malformed local config — {exc}"]
    merged = _deep_merge(defaults, local)

    # STRUCTURE validation (not just syntax): a wrong-typed section — e.g. a hand-edited
    # `operator = "x"` or `remotes = "x"` — must become a diagnostic + safe default, never
    # a crash (a str has no `.get`) and never leak a bad value into command/config/Git.
    op = merged.get("operator", {})
    if not isinstance(op, dict):
        diagnostics.append(f"ignored non-table [operator] (got {type(op).__name__}); using defaults")
        op = {}

    def _str_field(name: str) -> str:
        v = op.get(name, "")
        if not isinstance(v, str):
            diagnostics.append(f"ignored non-string operator.{name} ({type(v).__name__}); treating as unset")
            return ""
        return v

    operator = OperatorConfig(callsign=_str_field("callsign"), locator=_str_field("locator"))

    remotes_raw = local.get("remotes", {})   # runtime-local only, never tracked
    if not isinstance(remotes_raw, dict):
        diagnostics.append(f"ignored non-table [remotes] (got {type(remotes_raw).__name__}); using none")
        remotes = {}
    else:
        # Drop any non-string remote value here so a malformed hand-edit can never reach
        # Git (URL syntax is validated separately at save/use time).
        remotes = {}
        for k, v in remotes_raw.items():
            if isinstance(v, str):
                remotes[k] = v
            else:
                diagnostics.append(f"ignored non-string remote '{k}' ({type(v).__name__})")

    sources = merged.get("sources", {})
    if not isinstance(sources, dict):
        diagnostics.append(f"ignored non-table [sources] ({type(sources).__name__}); using defaults")
        sources = {}

    return Config(
        values=merged,
        operator=operator,
        sources=sources,
        remotes=remotes,
        local_path=local_path,
        secrets_path=paths.runtime_root / "config" / "secrets.toml",
        diagnostics=diagnostics,
    )


def load_secrets(paths: Paths) -> dict:
    """Read the local secrets layer (never tracked). Returns {} if absent."""
    return _load_runtime_toml(paths, paths.runtime_root / "config" / "secrets.toml")


def _toml_value(kind: str, value: str) -> str:
    """Format a value as TOML scalar for a flat key update."""
    v = str(value)
    if kind in ("int", "float"):
        return v if v.strip() != "" else "0"
    if kind == "flag":
        return "true" if v not in ("", "0", "false", "off") else "false"
    return '"' + v.replace("\\", "\\\\").replace('"', '\\"') + '"'


def render_keyval(params, values, subst, sep: str = " = ", comment: bool = True) -> str:
    """Render a flat `key<sep>value` config file from FileParams. `sep="="` (no
    spaces) suits parsers that split on the first '=' (e.g. lorachat.conf)."""
    lines = ["# Generated by lhpc — edit via the web Config page."] if comment else []
    for p in params:
        v = values.get(p.name, p.default)
        if p.kind == "flag":
            v = "1" if str(v) not in ("", "0", "false", "off") else "0"
        lines.append(f"{subst(p.key)}{sep}{subst(str(v))}")   # key may hold {band}
    return "\n".join(lines) + "\n"


def update_toml(text: str, params, values, subst) -> str:
    """Update declared keys (by section) in an existing TOML file, preserving the
    rest. A blank value leaves the base file as-is (e.g. keep a preset default);
    a set value updates the key — uncommenting a `# key = …` line if needed."""
    want = {}
    for p in params:
        raw = subst(str(values.get(p.name, p.default)))
        if p.kind != "flag" and raw.strip() == "":
            continue                       # blank -> don't touch the base
        want[(p.section, p.key)] = _toml_value(p.kind, raw)
    lines = text.splitlines()
    section = ""
    done = set()                           # update the FIRST occurrence of each key only
    for i, line in enumerate(lines):
        st = line.strip()
        if st.startswith("[") and st.endswith("]"):
            section = st[1:-1]
            continue
        candidate = st[1:].strip() if st.startswith("#") else st
        if "=" in candidate:
            key = candidate.split("=", 1)[0].strip()
            if (section, key) in want and (section, key) not in done:
                indent = line[: len(line) - len(line.lstrip())]
                lines[i] = f"{indent}{key} = {want[(section, key)]}"
                done.add((section, key))
    return "\n".join(lines) + ("\n" if text.endswith("\n") else "")


def _yaml_value(kind: str, value: str) -> str:
    v = str(value)
    if kind == "flag":
        return "true" if v not in ("", "0", "false", "off") else "false"
    return v   # YAML bare scalar (ints/strings unquoted, as meshtasticd uses)


def update_yaml(text: str, params, values, subst) -> str:
    """Update declared `section.key` entries in a 2-space-indented YAML file,
    preserving everything else. Updates the FIRST occurrence of each key in its
    section (uncommenting a `#  key: …` line if that is the first occurrence), so
    the active value is set while commented alternative blocks are left untouched.
    Blank non-flag values leave the base as-is."""
    want = {}
    for p in params:
        raw = subst(str(values.get(p.name, p.default)))
        if p.kind != "flag" and raw.strip() == "":
            continue
        want[(p.section, p.key)] = _yaml_value(p.kind, raw)
    lines = text.splitlines()
    section = ""
    done = set()
    for i, line in enumerate(lines):
        bare = line.strip()
        if not bare or bare.startswith("---"):
            continue
        # Section header: an UNcommented top-level `Key:` with no inline value.
        if (not bare.startswith("#") and bare.endswith(":")
                and (len(line) - len(line.lstrip())) == 0 and ":" not in bare[:-1]):
            section = bare[:-1].strip()
            continue
        # Analyse a possibly-commented key line, preserving the key's own indent.
        analysed = line
        if bare.startswith("#"):
            h = line.index("#")
            analysed = line[:h] + line[h + 1:]      # drop one '#', keep indentation
        a = analysed.strip()
        if not a or a.startswith("#") or ":" not in a:
            continue
        indent = len(analysed) - len(analysed.lstrip())
        key = a.split(":", 1)[0].strip()
        sec = "" if indent == 0 else section
        if (sec, key) in want and (sec, key) not in done:
            lines[i] = f"{' ' * indent}{key}: {want[(sec, key)]}"
            done.add((sec, key))
    return "\n".join(lines) + ("\n" if text.endswith("\n") else "")


def _patch_local_table(data: dict, table: str, updates: dict) -> None:
    """Patch ONLY the named keys of a managed local table, IN PLACE on `data`. Contract:
        missing table                -> create a new flat table from `updates`;
        existing flat table           -> patch only the named keys (all other keys preserved);
        existing non-table value      -> `ConfigError` (before any write — fail closed on a valid-
                                          but-wrong TOML shape, e.g. ``operator = "text"``).
    A value of ``None`` in `updates` REMOVES that key (used to clear a remote override)."""
    cur = data.get(table)
    if cur is None:
        base: dict = {}
    elif isinstance(cur, dict):
        base = dict(cur)                          # keep every existing key/type
    else:
        raise ConfigError(f"local.toml [{table}] is a {type(cur).__name__}, not a table; "
                          f"refused (file unchanged)")
    for key, value in updates.items():
        if value is None:
            base.pop(key, None)
        else:
            base[key] = value
    data[table] = base


def _write_local_tables(paths: Paths, path: Path, updates: dict) -> Path:
    """PATCH managed tables into <runtime>/config/local.toml. `updates` is
    ``{table: {key: value_or_None}}`` — each table is patched by owned keys only (see
    `_patch_local_table`): other keys in that table, all other tables, and every root scalar are
    preserved with their exact types. A value of ``None`` clears that key.

    Fail closed: a malformed existing file, an incompatible managed-table shape (a non-table
    ``operator``/``remotes`` value), or an unsupported value/key raises `ConfigError` WITHOUT
    writing — the prior file is preserved byte-for-byte."""
    existing = _load_runtime_toml(paths, path)   # no-follow read; ConfigError on corrupt
    data = dict(existing)                         # keep root scalars + every other table
    for table, kv in updates.items():
        _patch_local_table(data, table, kv)      # patch owned keys; ConfigError on non-table shape
    # Type-safe + fail-closed render (raises before any write on an unsupported value/key).
    _atomic_write(paths, path, render_local_tables(data), mode=0o600)   # local layer: 0600
    return path


def save_operator_config(paths: Paths, callsign: str, locator: str) -> Path:
    """Persist operator identity into the runtime-local layer (git-ignored)."""
    path = paths.runtime_root / "config" / "local.toml"
    with config_lock(paths):
        return _write_local_tables(paths, path, {"operator": {"callsign": callsign, "locator": locator}})


def save_component_remote(paths: Paths, component_id: str, url: str) -> Path:
    """Override a component's GitHub remote in the runtime-local layer. An empty
    url clears the override. The URL is validated to a safe remote policy BEFORE
    any file change (raises ValidationError on an unsafe/option-like value)."""
    from . import validators
    cid = validators.path_component(component_id, field="component id")
    clean = validators.remote_url(url, field="remote")
    path = paths.runtime_root / "config" / "local.toml"
    with config_lock(paths):
        # Patch ONLY this component's key (None clears it), preserving every other remote. A
        # non-table `remotes` value is rejected inside the patch (ConfigError), never a raw
        # `dict("string")` ValueError.
        return _write_local_tables(paths, path, {"remotes": {cid: clean or None}})


def render_local_tables(data: dict) -> str:
    """Render a complete local.toml from a parsed structure ``{key: scalar | {key: scalar}}`` —
    TYPE-SAFE and FAIL-CLOSED. Root scalar keys are preserved (never dropped), then each flat
    ``[section]`` table. Keys/values go through `_toml_key`/`_toml_scalar`, so bool/int/finite-float
    keep their type and quotes/backslashes/control chars/Unicode round-trip; a nested table, array,
    datetime, non-finite float, unsupported object, or control-character key raises `ConfigError`
    BEFORE any write. Finally the document is parsed with `tomllib` and its structure is verified
    to equal `data` — a mismatch (an unsafe key/value) is refused."""
    root = {k: v for k, v in data.items() if not isinstance(v, dict)}
    tables = {k: v for k, v in data.items() if isinstance(v, dict)}
    lines = ["# Local operator overrides (managed by lhpc — git-ignored)."]
    for key, value in root.items():
        lines.append(f"{_toml_key(key)} = {_toml_scalar(value)}")
    for section, table in tables.items():
        lines.append(f"\n[{_toml_key(section)}]")
        for key, value in table.items():
            if isinstance(value, dict):
                raise ConfigError(f"nested table [{section}.{key}] is not supported in local.toml")
            lines.append(f"{_toml_key(key)} = {_toml_scalar(value)}")
    rendered = "\n".join(lines) + "\n"
    try:
        parsed = tomllib.loads(rendered)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"generated local.toml is not valid TOML (refused): {exc}") from exc
    if parsed != data:
        raise ConfigError("generated local.toml did not round-trip (unsafe key/value); refused")
    return rendered


def render_stack_config(stack_id: str, values: dict) -> str:
    """Render a per-stack config file — type-aware, fully escaped, parse-validated before write
    (see `_render_stack_config`)."""
    return _render_stack_config(stack_id, values)


def _txn_journal(paths: Paths) -> Path:
    return paths.under("state", "config-txn.json")


_JOURNAL_VERSION = 1
# Only these logical config targets may ever appear in a transaction journal. Recovery
# maps a logical kind + a validated runtime-relative path through the safe path API —
# it never trusts or touches an arbitrary absolute path from journal content.
_ALLOWED_KINDS = {"local", "stack"}


def _resolve_journal_target(paths: Paths, rec) -> Path:
    """Map ONE journal target record through the allowlist to a safe runtime path, or
    raise ConfigError. Rejects unknown kinds, absolute/traversal/escaping paths, the
    wrong shape per kind, and a symlink-leaf target."""
    if not isinstance(rec, dict):
        raise ConfigError("malformed journal target record")
    kind, rel = rec.get("kind"), rec.get("rel")
    if kind not in _ALLOWED_KINDS:
        raise ConfigError(f"unknown journal target kind {kind!r}")
    if (not isinstance(rel, str) or not rel or os.path.isabs(rel)
            or rel != os.path.normpath(rel) or ".." in rel.split("/")):
        raise ConfigError(f"unsafe journal target path {rel!r}")
    parts = rel.split("/")
    if kind == "local" and parts != ["config", "local.toml"]:
        raise ConfigError("local journal target must be config/local.toml")
    if kind == "stack" and (len(parts) != 3 or parts[:2] != ["config", "stacks"]
                            or not parts[2].endswith(".toml")):
        raise ConfigError("stack journal target must be config/stacks/<name>.toml")
    try:
        p = paths.under(*parts)        # lexical + symlink-parent containment
    except PathContainmentError as exc:
        raise ConfigError(f"journal target escapes runtime root: {exc}") from exc
    if p.is_symlink():
        raise ConfigError(f"refusing a symlink-leaf journal target: {p}")
    return p


def recover_config_transaction(paths: Paths) -> str | None:
    """Recover a pending config journal. Returns a message if it restored cleanly,
    None if there was NO journal, or "" if recovery is required but could not complete
    (journal retained — caller must block). A journal that EXISTS but is malformed,
    unreadable, wrong-schema, duplicate, or names a non-allowlisted target is NEVER
    treated as absent — it blocks (fail-closed)."""
    from . import runtime_fs
    try:
        jp = _txn_journal(paths)
    except PathContainmentError:
        # The journal's OWN location escapes the runtime root (e.g. a journal symlink
        # whose target leaves the root): a pending journal that cannot be safely located
        # is recovery-required, never absent and never an uncaught containment exception.
        return ""
    # Presence is decided WITHOUT following the leaf: ANY directory entry at the journal
    # path -- a regular file, OR a symlink (including a dangling or escaping one) -- is a
    # pending journal that must be recovered/blocked. `Path.exists()` follows the link and
    # would report a dangling-symlink journal as absent; `os.path.lexists` does not.
    if not os.path.lexists(jp):
        return None
    try:
        journal = json.loads(runtime_fs.read_text(paths, jp))   # no-follow read
    except (OSError, ValueError, PathContainmentError):
        return ""                       # exists but unreadable/symlinked/malformed -> BLOCK
    if (not isinstance(journal, dict) or journal.get("version") != _JOURNAL_VERSION
            or not isinstance(journal.get("targets"), list) or not journal["targets"]):
        return ""                       # wrong schema -> BLOCK
    resolved, seen = [], set()
    try:
        for rec in journal["targets"]:
            p = _resolve_journal_target(paths, rec)
            if str(p) in seen:
                return ""               # duplicate target -> BLOCK
            seen.add(str(p))
            resolved.append((p, rec))
    except ConfigError:
        return ""                       # unknown/escaping/symlink target -> BLOCK
    for p, rec in resolved:
        try:
            if rec.get("existed"):
                _atomic_write(paths, p, rec.get("pre") or "", int(rec.get("mode", 0o644)))
            else:
                runtime_fs.unlink(paths, p)           # descriptor-anchored, no-follow
        except (OSError, PathContainmentError):
            return ""                   # recovery FAILED -> keep journal, BLOCK
    try:
        runtime_fs.unlink(paths, jp)
    except (OSError, PathContainmentError):
        return ""                       # journal could not be removed -> recovery-required
    return f"recovered a pending config transaction ({len(resolved)} file(s))"


def apply_config_transaction(paths: Paths, targets: list[tuple[str, Path, str, int]]) -> None:
    """Write several config files all-or-recoverable under one lock. Each target is
    (logical-kind, path, content, mode). Steps: recover/​block any pending journal;
    journal each pre-image with a logical kind + runtime-relative path; atomically
    replace each; roll back all on failure; remove the journal only on success.
    Raises ConfigError("recovery-required: …") if a restore fails (journal kept)."""
    with config_lock(paths):
        if recover_config_transaction(paths) == "":
            raise ConfigError("recovery-required: a pending config journal could not be "
                              "recovered; resolve it before saving config again")
        jp = _txn_journal(paths)
        journal = {"version": _JOURNAL_VERSION, "targets": []}
        for kind, p, _content, mode in targets:
            if p.is_symlink():
                raise ConfigError(f"refusing a symlink-leaf config target: {p}")
            rel = os.path.relpath(str(p), str(paths.runtime_root))
            from . import runtime_fs
            try:
                pre, existed = runtime_fs.read_text(paths, p), True   # no-follow read
            except FileNotFoundError:
                pre, existed = None, False
            except (OSError, PathContainmentError) as exc:   # unreadable/unsafe -> NOT "nonexistent"
                raise ConfigError(f"config target exists but is unreadable: {p} ({exc})")
            journal["targets"].append({"kind": kind, "rel": rel, "pre": pre,
                                       "existed": existed, "mode": mode})
        for rec in journal["targets"]:        # prove every target resolves safely first
            _resolve_journal_target(paths, rec)
        _atomic_write(paths, jp, json.dumps(journal), 0o600)   # anchored write creates parents
        try:
            for kind, p, content, mode in targets:
                # `content` may be a callable rendered INSIDE this lock (merge-in-transaction),
                # so it reads the LATEST file and preserves keys owned by another writer. A raise
                # here (e.g. an unsupported manual value) triggers the rollback below.
                _atomic_write(paths, p, content(paths) if callable(content) else content, mode)
        except Exception as failure:
            for rec in journal["targets"]:        # roll back everything
                p = _resolve_journal_target(paths, rec)
                try:
                    if rec["existed"]:
                        _atomic_write(paths, p, rec["pre"], int(rec["mode"]))
                    else:
                        runtime_fs.unlink(paths, p)   # descriptor-anchored, no-follow
                except (OSError, PathContainmentError) as exc:
                    raise ConfigError(f"recovery-required: rollback failed ({exc}); "
                                      "journal retained") from exc
            try:
                runtime_fs.unlink(paths, jp)          # rolled back cleanly
            except (OSError, PathContainmentError) as exc:
                raise ConfigError(f"recovery-required: journal cleanup failed ({exc}); "
                                  "journal retained") from exc
            raise ConfigError("config transaction failed and was rolled back: "
                              f"{failure}") from failure
        try:
            runtime_fs.unlink(paths, jp)              # success — remove the journal
        except (OSError, PathContainmentError) as exc:
            raise ConfigError(f"recovery-required: journal cleanup failed ({exc}); "
                              "journal retained") from exc




# --- per-stack user configuration (set via the web Config page) -----------

def _stack_config_path(paths: Paths, stack_id: str, band: str = "") -> Path:
    # Band-switchable stacks keep a separate config per band: "<id>@<band>.toml".
    # Defence in depth: the id is a single path component and the band must be a
    # real radio band, so neither can introduce a separator or "..". The result is
    # then proven to stay inside config/stacks/ (rejects any symlink/escape).
    from . import validators
    sid = validators.path_component(stack_id, field="stack id")
    if band:
        band = validators.band(band, allow_both=True)
    name = f"{sid}@{band}.toml" if band else f"{sid}.toml"
    base = (paths.runtime_root / "config" / "stacks").resolve()
    path = (base / name).resolve()
    if base not in path.parents:
        raise validators.ValidationError(f"config path escapes stacks dir: {name!r}")
    return path


def load_stack_config(paths: Paths, stack_id: str, band: str = "") -> dict:
    """User-defined configuration for a stack/band (runtime-local, git-ignored)."""
    try:
        return _load_runtime_toml(paths, _stack_config_path(paths, stack_id, band))
    except ConfigError:
        return {}            # a corrupt stored config falls back to defaults


# TOML basic-string control-character escapes (TOML v1.0 §String). Other C0 controls + DEL are
# emitted as \uXXXX; a raw control character is NEVER placed in a one-line basic string.
_TOML_STR_ESC = {"\\": "\\\\", '"': '\\"', "\b": "\\b", "\t": "\\t",
                 "\n": "\\n", "\f": "\\f", "\r": "\\r"}
_BARE_KEY = re.compile(r"[A-Za-z0-9_-]+")


def _toml_basic_string(s: str) -> str:
    """`s` as a TOML basic string (double-quoted), fully escaped so it round-trips exactly:
    backslash/quote/backspace/tab/newline/formfeed/CR use short escapes, every other C0 control
    and DEL become \\uXXXX, and Unicode text is preserved verbatim. Invalid (non-UTF-8-encodable)
    text — e.g. a lone surrogate — is rejected BEFORE any write."""
    try:
        s.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ConfigError(f"string is not valid Unicode (lone surrogate?): {exc}") from exc
    out = []
    for ch in s:
        if ch in _TOML_STR_ESC:
            out.append(_TOML_STR_ESC[ch])
        elif ord(ch) < 0x20 or ord(ch) == 0x7F:
            out.append(f"\\u{ord(ch):04X}")
        else:
            out.append(ch)
    return '"' + "".join(out) + '"'


def _toml_scalar(value) -> str:
    """TOML representation of a SUPPORTED stack-config scalar, preserving its type: str (basic
    string, fully escaped), bool (`true`/`false`), int (decimal), finite float (round-trippable
    decimal). Anything else — list, table/mapping, datetime, NaN, ±inf, other objects — raises
    `ConfigError` BEFORE any write. NB: bool is checked before int (bool is an int subclass)."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ConfigError(f"non-finite float not allowed in stack config: {value!r}")
        return repr(value)                       # shortest round-trippable decimal
    if isinstance(value, str):
        return _toml_basic_string(value)
    raise ConfigError(f"unsupported stack-config value type "
                      f"{type(value).__name__}: {value!r}")


def _toml_key(key: str) -> str:
    """A TOML key that stays a single FLAT literal key: bare `[A-Za-z0-9_-]+`, else a quoted basic
    string (so `custom.key`, `spaced key`, `a#b` never become a dotted/nested path). A control
    character in a key is rejected before any write."""
    if not isinstance(key, str) or key == "":
        raise ConfigError(f"invalid config key: {key!r}")
    if any(ord(c) < 0x20 or ord(c) == 0x7F for c in key):
        raise ConfigError(f"control character in config key: {key!r}")
    return key if _BARE_KEY.fullmatch(key) else _toml_basic_string(key)


def _render_stack_config(stack_id: str, values: dict) -> str:
    """Render a stack config, then PARSE-BEFORE-WRITE: keys and scalars are type-aware and fully
    escaped, and the result is validated with `tomllib` so a malformed line can never reach disk
    (raises `ConfigError` before any write; the caller keeps the prior file)."""
    lines = [f"# {stack_id} configuration (managed by lhpc — git-ignored)."]
    for key, value in values.items():
        lines.append(f"{_toml_key(key)} = {_toml_scalar(value)}")
    rendered = "\n".join(lines) + "\n"
    try:
        parsed = tomllib.loads(rendered)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"generated stack config is not valid TOML (refused): {exc}") from exc
    if set(parsed) != {k for k in values}:       # a key became nested/dotted -> refuse
        raise ConfigError("generated stack config changed the key set (unsafe key); refused")
    return rendered


def save_stack_config(paths: Paths, stack_id: str, values: dict, band: str = "") -> Path:
    """Persist a stack/band's configuration (flat key/value, stored as strings).
    Atomic + locked so concurrent web saves cannot corrupt or interleave."""
    path = _stack_config_path(paths, stack_id, band)
    with config_lock(paths):
        _atomic_write(paths, path, _render_stack_config(stack_id, values), mode=0o644)
    return path


def merge_stack_values(paths: Paths, stack_id: str, band: str, updates: dict,
                       clear_empty: bool = True) -> dict:
    """Read the LATEST stack config (MUST be called inside `config_lock`) and merge `updates`,
    keeping every OTHER key with its parsed type — so one owner's write never drops another's
    keys (a daemon-profile save keeps normal params; a normal save keeps `dp_*`; a manual scalar
    survives both). When `clear_empty`, a value of ""/None removes that key; otherwise it is
    stored (normal-config semantics keep an explicit empty value). Returns the merged dict."""
    current = dict(_load_runtime_toml(paths, _stack_config_path(paths, stack_id, band)))
    for key, value in updates.items():
        if clear_empty and value in (None, ""):
            current.pop(key, None)
        else:
            current[key] = value if not isinstance(value, str) else value
    return current


def conditional_clear_stack_config(paths: Paths, stack_id: str, band: str, expected: dict,
                                   matches) -> int:
    """Race-safe removal of legacy default-equal keys under ONE config lock. Re-reads the LATEST
    config, and for each key in `expected` removes it ONLY if `matches(key, str(current[key]),
    expected[key])` is True — i.e. the stored value is STILL semantically the pre-update default
    captured for that key. A value a concurrent save changed to a genuine override (or an intentional
    empty override) therefore fails the predicate and survives untouched; there is no stale
    snapshot-to-delete window. Returns the number removed; the write is atomic, so a write failure
    raises (ConfigError/OSError) and removes nothing (the caller keeps the candidates pending)."""
    path = _stack_config_path(paths, stack_id, band)
    with config_lock(paths):
        current = dict(_load_runtime_toml(paths, path))
        to_del = [k for k, exp in expected.items()
                  if k in current and matches(k, str(current[k]), exp)]
        if to_del:
            for k in to_del:
                del current[k]
            _atomic_write(paths, path, _render_stack_config(stack_id, current), mode=0o644)
        return len(to_del)


def update_stack_config(paths: Paths, stack_id: str, updates: dict, band: str = "",
                        clear_empty: bool = True) -> Path:
    """Locked read-merge-write of a stack's config under ONE lock: read the LATEST config, merge
    `updates` (see `merge_stack_values`), and atomic-write. Preserves every other key (run params,
    file values, autostart, `dp_*`, manual scalars …) and any concurrent change committed before
    the lock was taken. The render is type-aware and validates before the write, so a corrupt
    manual entry blocks the save and leaves the file unchanged. Never nests `config_lock`."""
    path = _stack_config_path(paths, stack_id, band)
    with config_lock(paths):
        merged = merge_stack_values(paths, stack_id, band, updates, clear_empty)
        _atomic_write(paths, path, _render_stack_config(stack_id, merged), mode=0o644)
    return path
