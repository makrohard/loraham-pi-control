"""Self-update: lhpc's own version / git / upstream state.

lhpc is normally installed as an EDITABLE git checkout, so it updates ITSELF by fast-forwarding that
checkout (`git fetch` + `git merge --ff-only`) and asking the operator to restart the web console —
a flow kept deliberately separate from the managed-component update machinery (installer / clone).

Design rules:
  * LOCAL git (`rev-parse`, `status`, `show`, `merge`, `reset`) is not a network call; the UPSTREAM
    comparison (`fetch`) is, so it runs ONLY from explicit actions / a startup thread and is CACHED to
    a state marker. GET pages read the marker via `status_view()` — never git, never network.
  * Every git subprocess is bounded and fail-soft: any failure degrades to "unknown" (grey footer),
    never a crash or a 500.
  * A DIRTY tree is refused by default (would overwrite local edits); `apply_update(force=True)`
    discards local changes and hard-aligns to upstream only when the operator opts in.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import time
from contextlib import contextmanager
from pathlib import Path

from . import runtime_fs
from .paths import Paths, PathContainmentError
from .probes.backends import System
from ..version import __version__

_REMOTE = "origin"
_LOCAL_TIMEOUT = 5.0
_NET_TIMEOUT = 25.0
_MARKER = ("state", "selfupdate.json")
_MIGRATE_MARKER = ("state", "selfupdate-migrate.json")
_LOCK = ("state", "locks", "selfupdate.lock")
_CTRL_RUNTIME_LOCK = ("state", "locks", "controller-runtime")
_VERSION_RE = re.compile(r"""__version__\s*=\s*["']([^"']+)["']""")

JOURNAL_VERSION = 3                                # v3: records reference a durable git transaction ANCHOR
_SAFE = re.compile(r"^[A-Za-z0-9_.-]+$")          # stack/comp/name/key tokens
_SAFE_OPT = re.compile(r"^[A-Za-z0-9_.-]*$")      # band (may be empty)
_SHA = re.compile(r"^[0-9a-fA-F]{7,64}$")         # abbreviated..full git commit id
_BRANCH = re.compile(r"^[A-Za-z0-9._][A-Za-z0-9._/-]*$")   # git branch token
_TXID_RE = re.compile(r"^[0-9a-f]{16,64}$")       # safe, ref-name-safe transaction id
_ANCHOR_NS = "refs/lhpc/selfupdate"               # dedicated LHPC-owned git ref namespace for anchors
_RESERVED_PREFIXES = ("dp_", "autostart_")        # daemon-profile + autostart storage keys
_ANCHOR_FIELDS = ("from_head", "to_head", "branch", "pending")


class SelfUpdateBusy(Exception):
    """Another self-update holds the interprocess lock."""


class UpdateLockError(Exception):
    """The self-update lock could not be opened safely (unsafe runtime state)."""


class JournalPersistError(Exception):
    """The migration journal could not be durably persisted before source mutation."""


@contextmanager
def update_lock(paths: Paths):
    """The ONE dedicated, runtime-owned, no-follow interprocess self-update lock. Held (NON-BLOCKING)
    across the WHOLE operation — candidate capture, journal persistence, fetch/ref resolution,
    merge/reset/clean, cache writes, config migration and journal finalization — so a concurrent apply
    fails promptly (`SelfUpdateBusy`) with zero git/config/marker mutation, and explicit/startup checks
    can defer. Raises `UpdateLockError` if the lock leaf cannot be opened safely."""
    try:
        fh = runtime_fs.open_lock(paths, paths.under(*_LOCK))
    except (OSError, PathContainmentError) as e:
        raise UpdateLockError(str(e)) from e
    try:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as e:
            raise SelfUpdateBusy() from e
        yield
    finally:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        fh.close()


class ControllerRuntimeBusy(Exception):
    """The controller-runtime lock is held incompatibly — either the web server holds it
    SHARED (so an apply cannot take it EXCLUSIVE) or an apply holds it EXCLUSIVE (so the
    web server cannot start). Fail-closed, never block."""


class ControllerRuntimeLockError(Exception):
    """The controller-runtime lock leaf could not be opened safely (unsafe runtime state)."""


@contextmanager
def controller_runtime_lock(paths: Paths, *, exclusive: bool):
    """The runtime-owned, no-follow controller-runtime flock at `state/locks/
    controller-runtime`. Prevents the running web server from having its own source mutated
    underneath it: `lhpc web` holds it SHARED (`exclusive=False`) for its whole lifetime;
    `self-update --apply` takes it EXCLUSIVE (`exclusive=True`) BEFORE the self-update
    `update_lock` (fixed lock order — no inversion). Both acquisitions are NON-BLOCKING:
    an incompatible holder raises `ControllerRuntimeBusy` immediately (never a hang). There
    is NO shared→exclusive upgrade — each caller opens its own descriptor. The kernel drops
    the flock automatically if the holder dies."""
    try:
        fh = runtime_fs.open_lock(paths, paths.under(*_CTRL_RUNTIME_LOCK))
    except (OSError, PathContainmentError) as e:
        raise ControllerRuntimeLockError(str(e)) from e
    mode = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
    try:
        try:
            fcntl.flock(fh.fileno(), mode | fcntl.LOCK_NB)
        except OSError as e:
            raise ControllerRuntimeBusy() from e
        yield
    finally:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        fh.close()


def repo_root() -> Path | None:
    """The git checkout that contains this lhpc package, or None when lhpc is not a git checkout
    (a plain wheel install) — in which case self-update is simply unavailable. Walks up from the
    package directory to the first ancestor that has a `.git` AND still contains `lhpc/version.py`."""
    import lhpc

    pkg = Path(lhpc.__file__).resolve().parent            # …/lhpc
    for d in (pkg.parent, *pkg.parent.parents):
        if (d / ".git").exists() and (d / "lhpc" / "version.py").is_file():
            return d
    return None


def _git(system: System, root: Path, args: list[str], timeout: float):
    return system.runner.run(["git", "-C", str(root), *args], timeout=timeout)


# Strip ANSI/OSC escapes, C0/C1 control chars, and Unicode box-drawing/block glyphs — the noise that
# makes a raw git/pip tail render as garbage in an HTML flash. Kept module-level so the sanitizer is
# tested directly.
_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")
_NOISE_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f─-▟]")


def _summarize_output(text: str, limit: int = 200) -> str:
    """Reduce raw command output (git/pip stderr, possibly multi-line with ANSI colour, indented
    `hint:` blocks, or Unicode box-drawing) to ONE clean, bounded, single-line human string safe to
    drop into a status summary. Empty input → ''. This is presentation hygiene only; the underlying
    command result is unchanged."""
    if not text:
        return ""
    cleaned = _NOISE_RE.sub(" ", _ANSI_RE.sub("", text))
    # Collapse every whitespace run (incl. the now-stripped newlines) to single spaces, so nothing
    # depends on HTML/terminal whitespace handling downstream.
    collapsed = " ".join(cleaned.split())
    if len(collapsed) > limit:
        collapsed = collapsed[:limit].rstrip() + "…"
    return collapsed


def local_state(system: System) -> dict:
    """NETWORK-FREE local snapshot: version + HEAD + branch + dirtiness. `is_git` is False when lhpc
    is not a git checkout (self-update unavailable)."""
    root = repo_root()
    st = {"is_git": root is not None, "version": __version__, "root": str(root) if root else "",
          "head": "", "head_short": "", "branch": "", "dirty": False}
    if root is None:
        return st
    r = _git(system, root, ["rev-parse", "HEAD"], _LOCAL_TIMEOUT)
    if r.returncode == 0:
        st["head"] = r.stdout.strip()
        st["head_short"] = st["head"][:9]
    b = _git(system, root, ["rev-parse", "--abbrev-ref", "HEAD"], _LOCAL_TIMEOUT)
    if b.returncode == 0 and b.stdout.strip() != "HEAD":
        st["branch"] = b.stdout.strip()
    # A tree is dirty if it has tracked modifications OR non-ignored untracked files/dirs — an
    # update would clobber both. `git status --porcelain` lists untracked paths (as `?? …`) while
    # honouring .gitignore, so ignored runtime artifacts (e.g. a `.venv/`) never count as dirty.
    s = _git(system, root, ["status", "--porcelain"], _LOCAL_TIMEOUT)
    st["dirty"] = bool(s.returncode == 0 and s.stdout.strip())
    return st


def local_changes(system: System, limit: int = 20) -> tuple[str, ...]:
    """NETWORK-FREE: the actual `git status --porcelain` lines behind `local_state()["dirty"]`.

    `dirty` is a bool, which told the operator *that* an overwrite was needed but never *why*. The
    overwrite path runs `git reset --hard` + `git clean -ffd`, so these are precisely the paths that
    would be discarded — name them before asking anyone to tick the box. Bounded, and truncation is
    DISCLOSED ("… and N more") rather than silent. Fail-soft: any git error -> ()."""
    root = repo_root()
    if root is None:
        return ()
    s = _git(system, root, ["status", "--porcelain"], _LOCAL_TIMEOUT)
    if s.returncode != 0:
        return ()
    lines = [ln for ln in s.stdout.splitlines() if ln.strip()]
    if len(lines) <= limit:
        return tuple(lines)
    return (*lines[:limit], f"… and {len(lines) - limit} more")


def divergence(system: System, branch: str = "") -> tuple[int, int]:
    """NETWORK-FREE `(ahead, behind)` of HEAD vs the already-fetched `origin/<branch>` ref.

    Uses the remote-tracking ref the last 'check for updates' fetch populated, so no network call.
    Fail-soft to `(0, 0)` on any git error / missing ref — exactly like `ff_blocked`, so a transient
    git problem never invents a divergence."""
    root = repo_root()
    if root is None:
        return (0, 0)
    br = branch or local_state(system).get("branch") or "main"
    r = _git(system, root, ["rev-list", "--left-right", "--count", f"HEAD...{_REMOTE}/{br}"],
             _LOCAL_TIMEOUT)
    if r.returncode != 0:
        return (0, 0)
    parts = r.stdout.split()
    if len(parts) != 2:
        return (0, 0)
    try:
        return (int(parts[0]), int(parts[1]))
    except ValueError:
        return (0, 0)


def ff_blocked(system: System, branch: str = "") -> bool:
    """NETWORK-FREE: would a normal (fast-forward-only) update be REFUSED because the local history
    has diverged? True when HEAD is NOT an ancestor of the already-fetched upstream ref AND differs
    from it — i.e. `git merge --ff-only` would fail and only a force (reset --hard) can update. Uses
    the remote-tracking ref populated by the last 'check for updates' fetch, so it makes no network
    call. Fail-soft: any git error / missing ref / not-a-checkout → False (nothing to warn about)."""
    root = repo_root()
    if root is None:
        return False
    local = local_state(system)
    br = branch or local.get("branch") or "main"
    ref = f"{_REMOTE}/{br}"
    up = _git(system, root, ["rev-parse", ref], _LOCAL_TIMEOUT)
    if up.returncode != 0:
        return False                                  # upstream ref not present locally → can't tell
    up_head = up.stdout.strip()
    if not up_head or up_head == local.get("head"):
        return False                                  # up to date → not blocked
    anc = _git(system, root, ["merge-base", "--is-ancestor", "HEAD", ref], _LOCAL_TIMEOUT)
    # `merge-base --is-ancestor` uses EXACTLY: 0 = ancestor (ff-able), 1 = NOT an ancestor
    # (diverged). Any other code (128 bad/ missing ref, runner failure, …) is a real error — fail
    # SOFT to False so a transient git problem never masquerades as divergence and offers a force.
    return anc.returncode == 1


def _version_at(system: System, root: Path, ref: str) -> str:
    """__version__ from `git show <ref>:lhpc/version.py` ('' on failure)."""
    r = _git(system, root, ["show", f"{ref}:lhpc/version.py"], _LOCAL_TIMEOUT)
    if r.returncode != 0:
        return ""
    m = _VERSION_RE.search(r.stdout)
    return m.group(1) if m else ""


def _deps_changed(system: System, root: Path, ref: str) -> bool:
    """True if pyproject.toml differs between HEAD and the fetched upstream ref (hint that the
    operator may need `pip install -e .` after updating)."""
    r = _git(system, root, ["diff", "--name-only", f"HEAD..{ref}", "--", "pyproject.toml"],
             _LOCAL_TIMEOUT)
    return bool(r.returncode == 0 and r.stdout.strip())


def check_upstream(system: System, branch: str = "") -> dict:
    """NETWORK (explicit): fetch the remote branch, then read the upstream HEAD + version LOCALLY.
    Never touches the working tree. Fail-soft: returns {ok: False, error} on any problem."""
    root = repo_root()
    if root is None:
        return {"ok": False, "error": "not a git checkout"}
    br = branch or local_state(system).get("branch") or "main"
    # `--` ends option parsing so a branch name can never be read as a git flag (S5:
    # defense-in-depth — `br` is derived locally today, but guard it if it ever becomes
    # operator-settable).
    f = _git(system, root, ["fetch", "--quiet", _REMOTE, "--", br], _NET_TIMEOUT)
    if getattr(f, "not_found", False):
        return {"ok": False, "error": "git not found"}
    if f.returncode != 0:
        return {"ok": False, "error": _summarize_output(f.stderr) or "fetch failed"}
    ref = f"{_REMOTE}/{br}"
    h = _git(system, root, ["rev-parse", ref], _LOCAL_TIMEOUT)
    if h.returncode != 0:
        return {"ok": False, "error": _summarize_output(h.stderr) or "no upstream ref"}
    head = h.stdout.strip()
    return {"ok": True, "branch": br, "upstream_head": head, "upstream_head_short": head[:9],
            "upstream_version": _version_at(system, root, ref),
            "deps_changed": _deps_changed(system, root, ref)}


# ---- cached marker (the ONLY thing GET pages read) --------------------------------------------

CACHE_MAX_BYTES = 64 * 1024          # a tiny status marker; anything larger is untrusted
CACHE_SCHEMA_VERSION = 1


def read_cache(paths: Paths) -> dict:
    """FILE-SAFE AND SCHEMA-SAFE cached-status read (the ONLY thing GET pages read). It
    must never block, raise, or hand malformed data to presentation code:
      * regular-file only, no-follow, size-gated (`stat_leaf_nofollow` BEFORE the bounded
        `read_text_regular`, which alone caps the read but does not prove the file was not
        larger) — reject an oversized cache (incl. a valid-JSON prefix + padding), never
        truncate;
      * the JSON ROOT and the nested `local`/`upstream`/`identity` values (when present)
        must be dicts of bounded primitives — a scalar/list/wrong-shape payload → `{}`.
    Any unsafe / missing / malformed / wrong-shape cache returns `{}` (rendered as GRAY
    "unknown"), never an exception or a block."""
    from . import runtime_fs as _rfs
    import stat as _stat
    try:
        # `paths.under()` realpath-checks the leaf and raises PathContainmentError (a
        # ValueError) for an ESCAPING symlink — it MUST be inside the try so that case
        # returns {} rather than 500-ing the page (the very case no-follow defends against).
        path = paths.under(*_MARKER)
        stt = _rfs.stat_leaf_nofollow(paths, path)
        if stt is None or not _stat.S_ISREG(stt.st_mode) or stt.st_size > CACHE_MAX_BYTES:
            return {}                                     # absent/symlink/non-regular/oversized
        raw = _rfs.read_text_regular(paths, path, max_bytes=CACHE_MAX_BYTES)
        data = json.loads(raw)
    except (OSError, PathContainmentError, ValueError):
        return {}
    return data if _valid_cache(data) else {}


# Type predicates for cached fields. `bool` is deliberately NOT an int here — a JSON `true`
# must never masquerade as a timestamp/version — and strings are length-bounded so nothing
# unbounded reaches presentation (the whole file is already <= CACHE_MAX_BYTES, but be
# explicit). Any consumed field of the wrong type invalidates the WHOLE envelope -> {}.
_STR_MAX = 512


def _is_str(v) -> bool:
    return isinstance(v, str) and len(v) <= _STR_MAX


def _is_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _is_bool(v) -> bool:
    return isinstance(v, bool)


# {section: {field: predicate}} for EVERY cache field presentation code consumes. A present
# field must satisfy its predicate; absent is always allowed (rendered as unknown/default).
_LOCAL_FIELDS = {"head": _is_str, "head_short": _is_str, "branch": _is_str,
                 "version": _is_str, "root": _is_str, "dirty": _is_bool, "is_git": _is_bool}
_UPSTREAM_FIELDS = {"ok": _is_bool, "deps_changed": _is_bool, "error": _is_str,
                    "branch": _is_str, "upstream_head": _is_str,
                    "upstream_head_short": _is_str, "upstream_version": _is_str}
_IDENTITY_FIELDS = {"ok": _is_bool, "status": _is_str, "reason": _is_str, "checked_at": _is_int}
# Outcome of the LAST service-mediated apply (the one-click web update) — shown once the
# console is back so the operator sees what happened while it was down.
_LAST_APPLY_FIELDS = {"ok": _is_bool, "summary": _is_str, "finished_at": _is_int}


def _valid_section(sec, fields) -> bool:
    if sec is None:
        return True                                       # absent section is fine
    if not isinstance(sec, dict):
        return False
    return all(pred(sec[k]) for k, pred in fields.items() if k in sec)


def _valid_cache(data) -> bool:
    """Schema-validate a decoded cache envelope: the root must be a dict; a present
    `schema_version` must be exactly the current one (an unknown/future version is rejected;
    a LEGACY envelope with none is accepted and renders as unchecked); a present `checked_at`
    must be an int (never a bool); and every consumed `local`/`upstream`/`identity` field
    must match its type. Unknown extra fields are ignored, not rendered."""
    if not isinstance(data, dict):
        return False                                      # scalar / list / wrong root shape
    if "schema_version" in data:                          # absent = legacy envelope (allowed)
        sv = data["schema_version"]
        if not _is_int(sv) or sv != CACHE_SCHEMA_VERSION:
            return False                                  # unknown/future version, or "1"/true
    if "checked_at" in data and not _is_int(data["checked_at"]):
        return False
    return (_valid_section(data.get("local"), _LOCAL_FIELDS)
            and _valid_section(data.get("upstream"), _UPSTREAM_FIELDS)
            and _valid_section(data.get("identity"), _IDENTITY_FIELDS)
            and _valid_section(data.get("last_apply"), _LAST_APPLY_FIELDS))


def write_cache(paths: Paths, data: dict) -> None:
    try:
        runtime_fs.write_marker(paths, paths.under(*_MARKER), json.dumps(data), 0o600)
    except (OSError, PathContainmentError, ValueError, TypeError):
        pass                                              # cache is best-effort, never fatal


# A candidate carries `from_head`/`expected` only as advisory record fields — NEITHER is trusted to
# authorize a deletion. The pre-update manifest is selected by the containing TRANSITION record's
# from_head (validated against the actual checkout), and the default is re-derived from that manifest
# (see ControllerService._prove_candidate).
_CAND_FIELDS = ("stack", "band", "key", "kind", "comp", "name", "expected", "from_head")


def _valid_candidate(c) -> bool:
    """A migration candidate is STRUCTURALLY safe only if it is a well-formed record whose key is
    EXACTLY the permitted scoped/flat form for its (kind, comp, name), with safe tokens, a commit-id
    `from_head`, and NOT a reserved daemon-profile/autostart storage key. Structural validity is
    necessary but NOT sufficient to delete — the pre-update default is proven from source separately."""
    if not isinstance(c, dict):
        return False
    if any(not isinstance(c.get(f), str) for f in _CAND_FIELDS):
        return False
    kind, comp, name, key, band = c["kind"], c["comp"], c["name"], c["key"], c["band"]
    if kind not in ("r", "f"):
        return False
    if not (_SAFE.match(c["stack"]) and _SAFE.match(comp) and _SAFE.match(name) and _SAFE.match(key)):
        return False
    if not _SAFE_OPT.match(band) or not _SHA.match(c["from_head"]):
        return False
    if name.startswith(_RESERVED_PREFIXES) or key.startswith(_RESERVED_PREFIXES):
        return False
    allowed = {name, f"__r__{comp}__{name}"} if kind == "r" else {f"file_{name}", f"__f__{comp}__{name}"}
    return key in allowed                                 # exact permitted key form only


def _valid_record(r, *, require_txid: bool = True) -> bool:
    """A transition record is valid ONLY with distinct, well-formed transition identity (valid branch
    token + two DISTINCT commit-id-form heads), a `txid` referencing its durable anchor, and an
    all-valid candidate list. (`require_txid=False` when validating an anchor payload, which is
    identified by its ref name, not an embedded txid.)"""
    if not isinstance(r, dict):
        return False
    fh, th, br = r.get("from_head"), r.get("to_head"), r.get("branch")
    if not (isinstance(fh, str) and isinstance(th, str) and isinstance(br, str)):
        return False
    if not (_SHA.match(fh) and _SHA.match(th) and _BRANCH.match(br)):
        return False
    if fh == th:                                         # a transition must be between DISTINCT commits
        return False
    if require_txid and not (isinstance(r.get("txid"), str) and _TXID_RE.match(r["txid"])):
        return False
    pending = r.get("pending")
    return isinstance(pending, list) and all(_valid_candidate(c) for c in pending)


# ---- durable git transaction anchor -----------------------------------------------------------
# A journal record is untrusted runtime state; its authoritative twin is a blob bound under
# refs/lhpc/selfupdate/<txid> in the checkout's OWN git object store. Recovery obtains the
# transition/candidate data from the ANCHOR and verifies the runtime journal matches it exactly.

def new_txid() -> str:
    import secrets
    return secrets.token_hex(16)


def _anchor_ref(txid: str) -> str:
    return f"{_ANCHOR_NS}/{txid}"


def create_anchor(system: System, txid: str, payload: dict) -> bool:
    """Persist the canonical transition `payload` ({from_head,to_head,branch,pending}) as a git blob
    bound at `refs/lhpc/selfupdate/<txid>`, BEFORE the runtime journal and BEFORE any source mutation.
    Returns False on ANY failure so the caller refuses to mutate source (fail-closed)."""
    import os as _os
    import tempfile
    root = repo_root()
    if root is None or not _TXID_RE.match(txid or ""):
        return False
    blob = json.dumps({k: payload.get(k) for k in _ANCHOR_FIELDS}, sort_keys=True)
    tmp = None
    try:
        with tempfile.NamedTemporaryFile("w", delete=False, suffix=".json") as tf:
            tf.write(blob)
            tmp = tf.name
        h = _git(system, root, ["hash-object", "-w", tmp], _LOCAL_TIMEOUT)
        if h.returncode != 0 or not _SHA.match(h.stdout.strip()):
            return False
        u = _git(system, root, ["update-ref", _anchor_ref(txid), h.stdout.strip()], _LOCAL_TIMEOUT)
        return u.returncode == 0
    except OSError:
        return False
    finally:
        if tmp:
            try:
                _os.unlink(tmp)
            except OSError:
                pass


def read_anchor(system: System, txid) -> dict | None:
    """The AUTHORITATIVE, structurally-valid transition payload bound at the anchor, or None (missing,
    unsafe txid, or unreadable/invalid blob)."""
    root = repo_root()
    if root is None or not isinstance(txid, str) or not _TXID_RE.match(txid):
        return None
    r = _git(system, root, ["cat-file", "-p", _anchor_ref(txid)], _LOCAL_TIMEOUT)
    if r.returncode != 0:
        return None
    try:
        d = json.loads(r.stdout)
    except (ValueError, TypeError):
        return None
    if not isinstance(d, dict) or not _valid_record(d, require_txid=False):
        return None
    return d


def delete_anchor(system: System, txid) -> None:
    root = repo_root()
    if root is None or not isinstance(txid, str) or not _TXID_RE.match(txid):
        return
    _git(system, root, ["update-ref", "-d", _anchor_ref(txid)], _LOCAL_TIMEOUT)


def anchored_record(system: System, record) -> dict | None:
    """Return the AUTHORITATIVE anchor payload for `record` — but ONLY if the anchor exists and its
    (from_head, to_head, branch, pending) EXACTLY match the runtime record's. Else None (→ block
    recovery-required). The journal's own fields never authorize migration on their own."""
    if not isinstance(record, dict):
        return None
    anchor = read_anchor(system, record.get("txid"))
    if anchor is None:
        return None
    if any(anchor.get(k) != record.get(k) for k in _ANCHOR_FIELDS):
        return None
    return anchor


def read_migration_journal(paths: Paths):
    """Read + STRICTLY validate the DURABLE migration journal (untrusted persisted input). Returns
    `(envelope, blocked)`:
      * SAFELY ABSENT   -> (None, False): proceed as no journal;
      * present but UNREADABLE (symlink, fifo/device, directory, escaped/unsafe parent, inaccessible)
        or MALFORMED (bad JSON / schema / transition identity / candidate) -> (None, True): the caller
        MUST block the self-update and report recovery-needed — NEVER act on it, NEVER mutate;
      * safely readable + schema-valid -> ({"completed", "prepared"}, False).
    Inspection is descriptor-safe/no-follow (no check-then-open, no path-following existence probe); it
    never raises and never deletes config."""
    try:
        raw = runtime_fs.read_text_regular(paths, paths.under(*_MIGRATE_MARKER))
    except FileNotFoundError:
        return None, False                               # ONLY a truly-absent leaf proceeds as no journal
    except (OSError, PathContainmentError):
        return None, True                                # present but unreadable/unsafe -> fail closed
    try:
        d = json.loads(raw)
    except (ValueError, TypeError):
        return None, True
    if not isinstance(d, dict) or d.get("version") != JOURNAL_VERSION:
        return None, True
    for slot in ("completed", "prepared"):
        v = d.get(slot)
        if v is not None and not _valid_record(v):
            return None, True
    return {"completed": d.get("completed"), "prepared": d.get("prepared")}, False


def write_migration_journal(paths: Paths, envelope: dict) -> bool:
    """Atomically persist the two-slot envelope `{completed, prepared}` (containment + no-follow +
    atomic rename). DEFENSIVE INVARIANT: any NON-NULL record must satisfy the complete v3 schema
    (valid transition identity + a `txid` referencing its anchor + valid candidates) — an invalid
    record (e.g. an empty/absent txid, or an empty pending list) is REFUSED (returns False) and never
    persisted, so a broken intermediate journal can never be written and left for later cleanup.
    Returns False on failure so the caller can REFUSE to mutate source when intent cannot be recorded."""
    completed, prepared = envelope.get("completed"), envelope.get("prepared")
    for record in (completed, prepared):
        if record is not None and (not _valid_record(record) or not record.get("pending")):
            return False
    rec = {"version": JOURNAL_VERSION, "completed": completed, "prepared": prepared}
    try:
        runtime_fs.write_marker(paths, paths.under(*_MIGRATE_MARKER), json.dumps(rec), 0o600)
        return True
    except (OSError, PathContainmentError):
        return False


def clear_migration_journal(paths: Paths) -> bool:
    """Remove the journal once every candidate is resolved. Returns True when the marker is gone
    (unlinked or already absent); False on failure — the caller then KEEPS the durable anchor so a
    later invocation re-migrates idempotently and retries the clear (self-healing)."""
    try:
        paths.safe_unlink(paths.under(*_MIGRATE_MARKER))
        return True
    except (OSError, PathContainmentError):
        return False


def classify_journal(paths: Paths, system: System):
    """PURE, NON-MUTATING classification of the durable journal against the ACTUAL checkout HEAD — the
    single gate shared by apply and the freshness check. Returns `(status, env, head)`:
      * 'blocked'           — absent-but-present-unreadable / corrupt / unsafe journal;
      * 'recovery_required' — structurally valid but NOT authorised by a matching durable anchor, or
                              the checkout is at an unexpected commit for a recorded transition (a
                              `prepared` whose HEAD matches neither endpoint, or pending `completed`
                              work whose HEAD != its recorded to_head);
      * 'ok'                — proceed (`env` is None when the journal is safely absent).
    Reads only (journal marker + local `git rev-parse HEAD` + the transaction anchor): no fetch, no
    cache/journal/source/config mutation."""
    env, bad = read_migration_journal(paths)
    if bad:
        return "blocked", None, ""
    if env is None:
        return "ok", None, ""
    completed, prepared = env.get("completed"), env.get("prepared")
    head = local_state(system).get("head", "")
    if prepared and completed and completed.get("pending"):
        return "recovery_required", env, head        # inconsistent: valid code never writes both
    if prepared:
        if anchored_record(system, prepared) is None:   # no matching durable anchor -> untrusted
            return "recovery_required", env, head
        if not head or head not in (prepared["from_head"], prepared["to_head"]):
            return "recovery_required", env, head
    if completed and completed.get("pending"):
        if anchored_record(system, completed) is None:
            return "recovery_required", env, head
        if not head or head != completed["to_head"]:
            return "recovery_required", env, head
    return "ok", env, head


def refresh_cache(system: System, paths: Paths, branch: str = "",
                  identity: dict | None = None, now: int | None = None) -> dict:
    """Do a live upstream check and persist the COMPLETE versioned envelope
    `{schema_version, local, upstream, identity, checked_at}` in ONE atomic write. The
    `identity` verdict (computed by the controller-identity live check in the service
    layer) is embedded HERE so it can never be silently dropped by a later refresh; it is
    a bounded `{checked_at, ok, reason}` dict or None. Returns the computed status_view.

    Called with `identity=None` (e.g. by `apply_update`, which cannot recompute it), the
    PRIOR envelope's identity verdict is CARRIED FORWARD rather than nulled — so an unrelated
    refresh never drops the verdict (single-envelope invariant). The `last_apply` outcome is
    likewise carried forward (only `record_last_apply_strict` writes it)."""
    prior_env = read_cache(paths)
    if identity is None:
        prior = prior_env.get("identity")
        identity = prior if isinstance(prior, dict) else None
    last_apply = prior_env.get("last_apply")
    data = {"schema_version": CACHE_SCHEMA_VERSION,
            "local": local_state(system), "upstream": check_upstream(system, branch),
            "identity": identity if isinstance(identity, dict) else None,
            "last_apply": last_apply if isinstance(last_apply, dict) else None,
            "checked_at": int(time.time()) if now is None else int(now)}
    write_cache(paths, data)
    return status_view(paths)


def record_last_apply_strict(paths: Paths, *, ok: bool, summary: str,
                             now: int | None = None) -> bool:
    """Merge the outcome of a service-mediated apply into the existing envelope (single atomic
    rewrite; every other field kept as-is) and REQUIRE the write to be durable. Returns True
    only when the envelope was persisted (atomic write + fsync via write_marker), False on any
    write/containment error. The self-update helper uses this so it never deletes the in-flight
    marker on an unrecorded outcome (a silently-lost `write_cache` would hide an incomplete
    update)."""
    env = read_cache(paths)
    if not env:
        env = {"schema_version": CACHE_SCHEMA_VERSION}
    env["schema_version"] = CACHE_SCHEMA_VERSION
    env["last_apply"] = {"ok": bool(ok), "summary": str(summary)[:_STR_MAX],
                         "finished_at": int(time.time()) if now is None else int(now)}
    try:
        runtime_fs.write_marker(paths, paths.under(*_MARKER), json.dumps(env), 0o600)
        return True
    except (OSError, PathContainmentError, ValueError, TypeError):
        return False


def _cstr(v) -> str:
    """Coerce a cached value to a bounded display string (defensive: a future reader
    regression that let a non-string through must not blow up `head[:9]` on a GET)."""
    return v[:_STR_MAX] if isinstance(v, str) else ""


def _cint(v) -> int:
    return v if (isinstance(v, int) and not isinstance(v, bool)) else 0


def status_view(paths: Paths) -> dict:
    """Read-only, network-free, subprocess-free view for the footer / pages, computed from the cached
    marker only. Always returns a version + head to display, plus colors + `update_available`.
    Every cached field is coerced (`_cstr`/`_cint`/`bool`) BEFORE use, so even if `read_cache`
    ever regressed and let malformed data through, rendering can never raise on a GET."""
    cache = read_cache(paths)
    local = cache.get("local")
    local = local if isinstance(local, dict) else {}
    up = cache.get("upstream")
    up = up if isinstance(up, dict) else {}
    # CACHED-ONLY: `is_git`/`available` come from the cached `local.is_git` written by the last
    # refresh (local_state) — NEVER a live `repo_root()`/`.git` probe here, so every GET stays
    # read-only. Absent (no cache yet, or a legacy cache without the field) -> unavailable/unknown
    # until the startup or explicit "check for updates" refresh populates it. `version` is the
    # in-process running version (not a source-tree probe), fine for display.
    is_git = bool(local.get("is_git") is True)
    version = __version__
    head = _cstr(local.get("head"))
    head_short = _cstr(local.get("head_short")) or (head[:9] if head else "")
    have_up = bool(up.get("ok") is True)
    up_ver = _cstr(up.get("upstream_version"))
    up_head = _cstr(up.get("upstream_head"))

    if not have_up:
        ver_color = commit_color = "grey"
        update_available = False
    else:
        version_changed = bool(up_ver) and up_ver != version
        commit_changed = bool(up_head) and up_head != head
        ver_color = "red" if version_changed else "green"
        commit_color = "green" if not commit_changed else ("red" if version_changed else "yellow")
        update_available = commit_changed
    return {
        "is_git": is_git,
        "available": is_git,
        "version": version, "head": head, "head_short": head_short,
        "branch": _cstr(local.get("branch")), "dirty": bool(local.get("dirty") is True),
        "have_upstream": have_up, "upstream_error": _cstr(up.get("error")),
        "upstream_version": up_ver, "upstream_head_short": _cstr(up.get("upstream_head_short")),
        "deps_changed": bool(up.get("deps_changed") is True),
        "checked_at": _cint(cache.get("checked_at")),
        "ver_color": ver_color, "commit_color": commit_color,
        "update_available": update_available,
        # Controller identity verdict — CACHED only (never a live check on this read).
        # Absent / unchecked -> None, rendered as "unchecked/unknown".
        "identity": _identity_view(cache.get("identity")),
        # Outcome of the last service-mediated apply (one-click web update) — CACHED only;
        # None until one has run.
        "last_apply": _last_apply_view(cache.get("last_apply")),
    }


def _identity_view(raw):
    """Bounded, shape-safe controller-identity verdict from the cached envelope: a dict
    `{checked_at:int, ok:bool, status:str, reason:str}` or None ('unchecked/unknown').
    `status` is one of `ok` / `unsafe` / `not_applicable` (defaulted from `ok` for a legacy
    two-state cache), so presentation can render the NEUTRAL not-self-hosted case distinctly
    from a genuine security failure."""
    if not isinstance(raw, dict):
        return None
    ok = raw.get("ok")
    reason = raw.get("reason")
    if not isinstance(ok, bool):
        return None
    status = raw.get("status")
    if status not in ("ok", "unsafe", "not_applicable"):
        status = "ok" if ok else "unsafe"                 # legacy cache without a status field
    return {"ok": ok, "status": status,
            "reason": str(reason)[:200] if isinstance(reason, str) else "",
            "checked_at": int(raw["checked_at"]) if isinstance(raw.get("checked_at"), int) else 0}


def _last_apply_view(raw):
    """Bounded, shape-safe last-apply outcome from the cached envelope:
    `{ok:bool, summary:str, finished_at:int}` or None (no service-mediated apply yet)."""
    if not isinstance(raw, dict) or not isinstance(raw.get("ok"), bool):
        return None
    summary = raw.get("summary")
    return {"ok": raw["ok"],
            "summary": str(summary)[:_STR_MAX] if isinstance(summary, str) else "",
            "finished_at": _cint(raw.get("finished_at"))}


# ---- apply + restart guidance -----------------------------------------------------------------

def apply_update(system: System, paths: Paths, *, force: bool = False, branch: str = "",
                 before_mutation=None) -> dict:
    """Fast-forward the checkout to the upstream branch (fetching fresh). Returns a result dict:
    {ok, message, dirty, already, deps_changed, new_head_short, new_version}.

    `before_mutation(from_head, to_head, branch, deps_changed)` — if given — is invoked AFTER all
    read-only pre-checks (fetch, already-up-to-date, dirty) and IMMEDIATELY BEFORE the first command
    that advances/resets the checkout. The caller uses it to durably record migration intent; if it
    RAISES, the exception propagates and NO source mutation happens (fail-closed).
      * a non-git install / unreachable upstream → ok=False with a clear message;
      * a DIRTY tree (tracked edits OR non-ignored untracked files/dirs) → ok=False, dirty=True
        (default: DON'T overwrite) unless force=True, which discards tracked changes AND EVERY
        non-ignored untracked path — including nested untracked git repos (`git clean -ffd`) — while
        leaving ignored artifacts like `.venv/`, then hard-aligns to upstream;
      * a clean but DIVERGED history → ff-only fails with a message (force to hard-reset).
    On force, if the reset succeeds but the untracked-cleanup COMMAND fails, the result is
    `{ok:True, cleanup_failed:True, cleanup_error}` — a truthful partial, never a plain success."""
    root = repo_root()
    if root is None:
        return {"ok": False, "message": "Not a git checkout — self-update is unavailable."}
    local = local_state(system)
    br = branch or local.get("branch") or "main"
    up = check_upstream(system, br)
    if not up.get("ok"):
        return {"ok": False, "message": f"Could not reach upstream: {up.get('error', '')}"}
    ref = f"{_REMOTE}/{br}"
    if up["upstream_head"] and up["upstream_head"] == local.get("head"):
        return {"ok": True, "already": True, "message": "Already up to date.", "deps_changed": False}
    if local.get("dirty") and not force:
        # Name the paths in `changes` (the caller renders them as details). `message` stays SINGLE
        # LINE — it is flashed verbatim in the GUI, and the sanitizer contract forbids newlines.
        # "Local changes are present" alone left the operator unable to tell an accidental artifact
        # from real work before consenting to `reset --hard` + `clean -ffd`.
        return {"ok": False, "dirty": True, "changes": list(local_changes(system)),
                "message": "Local changes are present (modified or untracked files) — updating would "
                           "overwrite them. Re-run with 'overwrite local changes' to discard them "
                           "and update."}
    # Last point before the checkout is advanced/reset: let the caller durably record migration intent
    # (a raise here aborts with ZERO source mutation).
    if before_mutation is not None:
        before_mutation(local.get("head", ""), up["upstream_head"], br, bool(up.get("deps_changed")))
    cleanup_failed = False
    cleanup_error = ""
    if force:
        # Discard tracked changes + align to upstream, THEN remove every non-ignored untracked path.
        # `git clean -ffd`: -d = directories, and the DOUBLE -f also removes nested untracked git
        # repositories (a single -f skips them); ignored paths such as `.venv/` are preserved.
        m = _git(system, root, ["reset", "--hard", ref], _LOCAL_TIMEOUT)
        if m.returncode == 0:
            cl = _git(system, root, ["clean", "-ffd"], _LOCAL_TIMEOUT)
            if cl.returncode != 0:
                cleanup_failed = True
                cleanup_error = _summarize_output(cl.stderr or cl.stdout) or "git clean failed"
    else:
        m = _git(system, root, ["merge", "--ff-only", ref], _LOCAL_TIMEOUT)
    if m.returncode != 0:
        detail = _summarize_output(m.stderr)
        return {"ok": False, "message": "Update could not be applied — the local branch has diverged "
                "from upstream." + (f" {detail}" if detail else "")}
    refresh_cache(system, paths, br)
    out = {"ok": True, "deps_changed": bool(up.get("deps_changed", False)),
           "new_head_short": up.get("upstream_head_short", ""),
           "new_version": up.get("upstream_version", "")}
    if cleanup_failed:
        out.update(cleanup_failed=True, cleanup_error=cleanup_error,
                   message="Update aligned to upstream, but some untracked files could NOT be removed "
                           "— delete them manually, then restart the console.")
    else:
        out["message"] = "Update applied — restart the web console to load it."
    return out


def restart_instructions(deps_changed: bool = False, deps_sync_cmd: str = "") -> dict:
    """How the operator restarts the web console after an update (lhpc never restarts itself).
    Detects a systemd user-service context via `INVOCATION_ID`. When dependencies changed,
    `deps_sync_cmd` (if given by the caller) is the EXACT editable-install command for the
    deployment — the self-hosted controller passes its deployment interpreter + checkout,
    already shell-quoted. With no controller declared, the dev fallback `pip install -e .`
    is used."""
    under_systemd = bool(os.environ.get("INVOCATION_ID"))
    cmds: list[str] = []
    note = ""
    if deps_changed:
        sync = deps_sync_cmd or "pip install -e ."
        cmds.append(f"{sync}    # sync the venv FIRST — dependencies changed (LHPC never installs "
                    "packages for you)")
        note = ("Dependencies changed in this update — run the venv sync command before (or with) the "
                "restart; otherwise the controller is updated but missing Python deps. LHPC never "
                "installs system or Python packages for you.")
    cmds.append("systemctl --user restart lhpc-web" if under_systemd
                else "stop the console (Ctrl-C) and re-run:  lhpc web")
    return {"under_systemd": under_systemd, "deps_changed": deps_changed, "commands": cmds, "note": note}
