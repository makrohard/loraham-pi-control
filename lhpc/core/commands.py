"""Structured command model — turns a component's typed launch spec into a real
argv list, environment, and controller-owned pre/post steps, with NO shell.

A run/build/test command is an argv TOKEN TEMPLATE: an ordered list where each
entry is either a literal token or a single whole placeholder. Placeholders:

    {param:NAME}      a run-param -> 0+ validated argv tokens (emit_param)
    {operator:callsign}   -> one validated token
    {band}            -> one token (the selected band)
    {runtime}/{source}  -> may appear INSIDE a literal token to build a path
                          (controller-derived, never user input)

A user value is always its own validated token and can never merge with an option,
change the executable/cwd/env, or become shell syntax. Tokens are executed with
`subprocess.Popen(argv, shell=False, cwd=..., env=...)`.
"""

from __future__ import annotations

import math
import os
import re
import shlex
import shutil
from pathlib import Path

from . import validators
from .model import emit_param


_PKG_RE = re.compile(r"[A-Za-z0-9._+-]+")
_ENV_NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


class CommandError(Exception):
    """A command template / step could not be built safely."""


def _paths_subst(text: str, runtime: str, source: str, band: str) -> str:
    """Substitute ONLY controller-derived paths into a literal token/value."""
    out = (text.replace("{runtime}", runtime)
               .replace("{source}", source)
               .replace("{band}", band or ""))
    if "{" in out and "}" in out:
        raise CommandError(f"unresolved placeholder in token: {text!r}")
    return out


def expand_argv(tokens, comp, params, op, runtime: str, source: str,
                band: str = "") -> list[str]:
    """Expand an argv token template to a validated argv list (no shell)."""
    by_name = {p.name: p for p in comp.run_params}
    out: list[str] = []
    for tok in tokens:
        if tok.startswith("{") and tok.endswith("}") and tok.count("{") == 1:
            inner = tok[1:-1]
            kind, _, name = inner.partition(":")
            if kind == "param":
                p = by_name.get(name)
                if p is None:
                    raise CommandError(f"unknown run-param token {tok}")
                raw = str((params or {}).get(name, p.default))
                # A param default may reference operator identity / paths (e.g. igate
                # `call` defaults to "{callsign}"). Resolve those controller-derived
                # templates BEFORE validating the value.
                raw = (raw.replace("{callsign}", op.callsign or "N0CALL")
                          .replace("{runtime}", runtime).replace("{source}", source))
                val = validators.validate_param(p, raw)
                out.extend(emit_param(p, val))
                continue
            if kind == "operator" and name == "callsign":
                out.append(validators.callsign(op.callsign or "N0CALL",
                                                field="callsign") or "N0CALL")
                continue
            if inner == "band":
                if band:
                    out.append(band)
                continue
            # {runtime}/{source} alone as a whole token -> the path
            out.append(_paths_subst(tok, runtime, source, band))
            continue
        # A literal token may embed only controller paths, never a user param.
        out.append(_paths_subst(tok, runtime, source, band))
    if not out:
        raise CommandError("empty argv")
    return out


def build_env(env_items, runtime: str, source: str, band: str = "") -> dict:
    """Build an environment dict from typed items. A value of `@file:PATH` reads
    that file's first line (used for the MeshCom HMAC password) — no `$(cat)`.

    FAIL-CLOSED: a missing/unreadable/empty `@file:` secret raises CommandError
    (which blocks the launch/build) — it never silently becomes an empty string.
    `@file?:PATH` is the OPTIONAL form: an absent/empty file yields "" (matches the
    legacy `$(cat … 2>/dev/null)` semantics for optional secrets like the MeshCom
    HMAC); an UNREADABLE present file still fails closed."""
    env: dict[str, str] = {}
    for key, value in (env_items or ()):
        if not _ENV_NAME_RE.fullmatch(key):
            raise CommandError(f"invalid environment variable name: {key!r}")
        v = str(value)
        if v.startswith("@file?:"):
            path = _paths_subst(v[len("@file?:"):], runtime, source, band)
            try:
                lines = Path(path).read_text(encoding="utf-8").splitlines()
                env[key] = lines[0].strip() if lines else ""
            except FileNotFoundError:
                env[key] = ""                    # optional secret: absent -> disabled
            except OSError as exc:
                raise CommandError(
                    f"optional secret file for {key} is unreadable: {exc}")
            continue
        if v.startswith("@file:"):
            path = _paths_subst(v[len("@file:"):], runtime, source, band)
            try:
                first = Path(path).read_text(encoding="utf-8").splitlines()
            except OSError as exc:
                raise CommandError(f"secret file for {key} is missing/unreadable: {exc}") from exc
            line = first[0].strip() if first else ""
            if not line:
                raise CommandError(f"secret file for {key} is empty: {path}")
            env[key] = line
        elif v.startswith("@env:"):                 # inherit a host env var (NAME[=DEFAULT])
            spec = v[len("@env:"):]
            name, sep, default = spec.partition("=")
            if not _ENV_NAME_RE.fullmatch(name):
                raise CommandError(f"invalid @env name in {v!r}")
            got = os.environ.get(name)
            env[key] = got if got is not None else (default if sep else "")
        else:
            env[key] = _paths_subst(v, runtime, source, band)
    return env


def normalize_pre_steps(steps, runtime: str, source: str, band: str = "") -> list:
    """Resolve typed pre-steps into execution-ready tuples (paths substituted) — the ONE
    form consumed by the shared safe engine (`wrapper_runtime.apply_steps`). This is used
    both for an in-process controller start and for serializing a generated wrapper, so a
    wrapper and a normal start run byte-identical steps. Raises CommandError on a bad spec."""
    out = []
    for s in (steps or ()):
        k = s.get("kind")
        try:
            if k == "mkdir":
                out.append(("mkdir", _paths_subst(s["path"], runtime, source, band),
                            str(s.get("mode", ""))))
            elif k == "chmod":
                out.append(("chmod", _paths_subst(s["path"], runtime, source, band),
                            str(s["mode"])))
            elif k == "symlink":
                out.append(("symlink", _paths_subst(s["src"], runtime, source, band),
                            _paths_subst(s["dst"], runtime, source, band)))
            else:
                raise CommandError(f"unknown pre-step kind: {k!r}")
        except KeyError as exc:
            raise CommandError(f"pre-step {k} missing field {exc}") from exc
    return out


def run_pre_steps(steps, runtime: str, source: str, band: str = "") -> None:
    """Execute typed controller-owned pre-steps through the SAME execution-time-safe
    engine the generated wrappers use (`wrapper_runtime.apply_steps`) — never a shell.
    Raises CommandError on failure (which blocks the launch)."""
    from . import wrapper_runtime
    from .paths import Paths, PathContainmentError
    try:
        tuples = normalize_pre_steps(steps, runtime, source, band)
        wrapper_runtime.apply_steps(Paths(runtime_root=Path(runtime)), tuples)
    except (OSError, ValueError, PathContainmentError) as exc:
        raise CommandError(f"pre-step failed: {exc}") from exc


_ASSET_SEG = re.compile(r"[A-Za-z0-9_.-]+")


def _asset_token(tok: str) -> str:
    """Resolve `{asset}/<rel>` to a packaged-data path (same read-only package data the
    config bases use, located via importlib.resources — never a repo-relative guess).

    Build helpers that cannot live in an UPSTREAM checkout ship with lhpc instead; this is
    the only way a step can reference them. Read-only by construction: package data is never
    a write destination, and the relative path is validated segment-by-segment so it can
    never traverse out of `lhpc/data/`."""
    rel = tok[len("{asset}"):].lstrip("/")
    parts = [p for p in rel.split("/") if p]
    if not parts or not all(_ASSET_SEG.fullmatch(p) and p not in ("..", ".") for p in parts):
        raise CommandError(f"unsafe packaged-asset path: {tok!r}")
    from .assets import asset_path
    p = asset_path("/".join(parts))
    if not p.is_file():
        raise CommandError(f"packaged asset not found: {tok!r}")
    return str(p)


def build_step_argv(step: dict, runner, runtime: str, source: str) -> list[str]:
    """Resolve a build/test step's argv (literals, a `{pkgconfig:NAME}` token expanded by
    invoking pkg-config with shell=False — never a backtick subshell — and `{asset}/...`
    for a helper shipped as lhpc package data)."""
    out: list[str] = []
    for tok in step.get("argv", []):
        if tok == "{asset}" or tok.startswith("{asset}/"):
            out.append(_asset_token(tok))
        elif tok.startswith("{pkgconfig:") and tok.endswith("}"):
            pkg = tok[len("{pkgconfig:"):-1]
            if not pkg or not _PKG_RE.fullmatch(pkg):
                raise CommandError(f"invalid pkg-config package name {pkg!r}")
            r = runner.run(["pkg-config", "--cflags", "--libs", pkg], 15.0, cwd=source)
            # FAIL-CLOSED: never build with compiler flags silently omitted.
            if r.returncode != 0:
                err = (r.stderr or "").strip()[:200]
                raise CommandError(f"pkg-config failed for {pkg!r}: {err or 'nonzero exit'}")
            out.extend((r.stdout or "").split())
        else:
            out.append(_paths_subst(tok, runtime, source, ""))
    if not out:
        raise CommandError("empty build/test argv")
    return out


def display_command(comp, op, runtime: str, source: str, band: str = "") -> str:
    """Human-readable, shell-quoted rendering of the structured run command — for
    manual wrappers and the dashboard ONLY. Never executed."""
    try:
        argv = expand_argv(comp.run_argv, comp, None, op, runtime, source, band)
    except CommandError:
        return ""
    return " ".join(shlex.quote(a) for a in argv)


# Steps a detached post-start launcher understands (all shell-free).
def _post_repeat(v) -> int:
    """A tcp_send `repeat`: a GENUINE integer >= 1. Rejects booleans and non-integral floats such
    as 1.5 (fail-closed); an all-digit string is accepted."""
    if isinstance(v, bool):
        raise CommandError(f"tcp_send: repeat must be an integer, not a boolean ({v!r})")
    if isinstance(v, int):
        n = v
    elif isinstance(v, str) and re.fullmatch(r"\+?[0-9]+", v.strip()):
        n = int(v)
    else:
        raise CommandError(f"tcp_send: repeat must be an integer >= 1 (got {v!r})")
    if n < 1:
        raise CommandError(f"tcp_send: repeat must be >= 1 (got {n})")
    return n


def _post_interval(v) -> float:
    """A tcp_send `interval`: a finite, non-negative number. Rejects booleans."""
    if isinstance(v, bool):
        raise CommandError(f"tcp_send: interval must be a number, not a boolean ({v!r})")
    if isinstance(v, (int, float)):
        f = float(v)
    elif isinstance(v, str):
        try:
            f = float(v)
        except ValueError:
            raise CommandError(f"tcp_send: interval must be a number (got {v!r})")
    else:
        raise CommandError(f"tcp_send: interval must be a number (got {v!r})")
    if not (math.isfinite(f) and f >= 0):
        raise CommandError(f"tcp_send: interval must be finite and >= 0 (got {v!r})")
    return f


def _post_schedule(v) -> list:
    """A tcp_send `schedule`: a non-empty list of [count, interval] tiers expressing a stepped
    retry cadence (e.g. [[8, 8], [6, 15], [22, 30]] = 8 attempts 8 s apart, then 6 at 15 s, then
    22 at a 30 s cap). Count follows the `repeat` rules, interval the `interval` rules —
    fail-closed on anything else."""
    if not isinstance(v, (list, tuple)) or not v:
        raise CommandError(
            f"tcp_send: schedule must be a non-empty list of [count, interval] pairs (got {v!r})")
    tiers = []
    for pair in v:
        if not isinstance(pair, (list, tuple)) or len(pair) != 2:
            raise CommandError(
                f"tcp_send: each schedule entry must be a [count, interval] pair (got {pair!r})")
        tiers.append((_post_repeat(pair[0]), _post_interval(pair[1])))
    return tiers


def _post_label(v) -> str:
    """A tcp_send `label`: the short human name used in status outcome lines."""
    if not isinstance(v, str) or not v.strip():
        raise CommandError(f"tcp_send: label must be a non-empty string (got {v!r})")
    return v.strip()


def render_post_launcher(steps, comp, params, op, runtime: str, source: str,
                         band: str = "", binding: dict | None = None, gated: bool = False,
                         result_path: str = "", meta: dict | None = None) -> str:
    """Serialize typed post-start steps into a self-contained Python launcher that runs them
    detached with no shell: delay / exec(argv) / tcp_wait / tcp_send. `binding` (main pid + start
    time + session/group) ties the runner to one exact main launch — it re-checks that main before
    every side-effectful step and stops if it ceased/was replaced. `gated=True` (DETACHED optional
    runners) makes it block on an arm byte from stdin before ANY step — the controller arms it only
    after its ownership record is durable; the synchronous REQUIRED path leaves it False."""
    if binding is not None:
        lid = binding.get("main_launch_id") if isinstance(binding, dict) else None
        ints_ok = isinstance(binding, dict) and all(
            not isinstance(binding.get(k), bool) and isinstance(binding.get(k), int)
            and binding.get(k) > 0
            for k in ("main_pid", "main_starttime", "main_pgid", "main_sid"))
        if not (isinstance(lid, str) and lid and ints_ok):
            raise CommandError("post-start binding must carry a non-empty main_launch_id and "
                               "POSITIVE integer main pid/starttime/pgid/sid")
    resolved = []
    for step in (steps or ()):
        # Declarative placeholder guard (no shell): skip this step entirely when the named param
        # resolves to a placeholder value — e.g. MeshCom sends NO `--setcall` for an empty / N0CALL
        # callsign. The raw resolved value (before validation) is compared, so an empty value that
        # a validator would reject still cleanly skips rather than failing the render.
        guard = step.get("skip_if_param")
        if guard is not None:
            gp = {p.name: p for p in comp.run_params}.get(guard)
            if gp is None:
                raise CommandError(f"post-step skip_if_param references unknown run param {guard!r}")
            # An ABSENT key defaults to [] (never skips); a SUPPLIED value must be a list/tuple of
            # strings only — a falsey non-list (None/False/0/""/{}) is rejected just as strictly as
            # [True]/[1], never silently coerced to [].
            if "skip_values" in step:
                sv = step["skip_values"]
                if not isinstance(sv, (list, tuple)) or not all(isinstance(v, str) for v in sv):
                    raise CommandError("post-step skip_values must be a list/tuple of strings only")
            else:
                sv = []
            graw = str((params or {}).get(guard, gp.default))
            graw = graw.replace("{callsign}", op.callsign or "N0CALL").strip()
            if graw in [str(v) for v in sv]:
                continue
        kind = step.get("kind")
        if kind == "delay":
            resolved.append({"kind": "delay", "seconds": float(step.get("seconds", 0))})
        elif kind == "exec":
            argv = expand_argv(step["argv"], comp, params, op, runtime, source, band)
            exe = shutil.which(argv[0]) or argv[0]
            for cand in step.get("paths", []):
                c = _paths_subst(cand, runtime, source, band)
                if Path(c).exists():
                    exe = c
                    break
            optional = bool(step.get("optional")) and not step.get("required")
            # The status label defaults to the ORIGINAL argv[0]'s basename, never the resolved
            # path — an operator reads "region"/"meshtastic", not a managed venv location.
            resolved.append({"kind": "exec", "argv": [exe, *argv[1:]],
                             "optional": optional,
                             "label": (_post_label(step["label"]) if step.get("label")
                                       else os.path.basename(argv[0]) or "exec")})
        elif kind in ("tcp_wait", "tcp_send"):
            d = {"kind": kind, "host": step.get("host", "127.0.0.1"),
                 "port": int(step["port"]),
                 "optional": bool(step.get("optional")) and not step.get("required")}
            if kind == "tcp_wait":
                d["timeout"] = float(step.get("timeout", 60))
                if step.get("label"):
                    d["label"] = _post_label(step["label"])
            else:
                d["data"] = _post_data(step.get("data", ""), comp, params, op, runtime, source, band)
                # A slow guest (e.g. QEMU firmware) may open its console port long before it is
                # ready to process a command. `repeat`/`interval` re-send the line until it lands
                # (idempotent settings like --setcall); default = send once. `schedule` expresses a
                # STEPPED cadence instead (tight early, backed-off cap later) for guests whose boot
                # time spans seconds-to-minutes; it is mutually exclusive with repeat/interval —
                # two sources of truth for the same window is exactly the malformed-metadata class
                # this render rejects. Malformed retry metadata is a typed render failure —
                # fail-closed, never a silent clamp. The per-attempt sleep list is precomputed here
                # so the runner has ONE sleep code path for both forms.
                if "schedule" in step:
                    if "repeat" in step or "interval" in step:
                        raise CommandError(
                            "tcp_send: schedule is mutually exclusive with repeat/interval")
                    per = [iv for cnt, iv in _post_schedule(step["schedule"])
                           for _ in range(cnt)]
                    d["repeat"] = len(per)
                    d["intervals"] = per[:-1]
                else:
                    d["repeat"] = _post_repeat(step.get("repeat", 1))
                    d["interval"] = _post_interval(step.get("interval", 0))
                    d["intervals"] = [d["interval"]] * (d["repeat"] - 1)
                if step.get("label") is not None:
                    d["label"] = _post_label(step["label"])
                # ACKNOWLEDGEMENT-AWARE sending (live finding: 17 blind --setcall
                # connects starved the MeshCom node's heap and killed its web UI):
                #  * stop_on: read the reply after each send; a match STOPS the repeats
                #    (one acknowledged send instead of the full blind window);
                #  * probe/probe_stop_on: query first and SKIP every send when the
                #    device already has the desired state (idempotent across restarts —
                #    NVS-persisted settings never get re-pushed).
                for fld in ("stop_on", "probe", "probe_stop_on"):
                    if step.get(fld):
                        d[fld] = _post_data(str(step[fld]), comp, params, op, runtime,
                                            source, band)
            resolved.append(d)
        else:
            raise CommandError(f"unknown post-step kind {kind!r}")
    # DESCRIPTOR-SAFE sidecar location, validated HERE so the standalone runner never has to trust
    # (or re-derive) an absolute path: it receives the runtime ROOT plus the runtime-RELATIVE
    # components and walks them with dir_fd + O_DIRECTORY|O_NOFOLLOW. O_NOFOLLOW on the leaf alone
    # never protected a swapped `state/post` PARENT.
    root_s, rel_parts = "", ()
    if result_path:
        if not runtime:
            raise CommandError("a post-start result path requires the runtime root")
        rel = os.path.relpath(str(result_path), str(runtime))
        parts = tuple(p for p in rel.split(os.sep) if p and p != ".")
        if (not parts or ".." in parts or os.path.isabs(rel)
                or any("/" in p or "\x00" in p for p in parts)):
            raise CommandError(
                f"post-start result path escapes the runtime root: {result_path!r}")
        root_s, rel_parts = str(runtime), parts
    return (_POST_RUNNER.replace("__STEPS__", repr(resolved))
            .replace("__BINDING__", repr(binding))
            .replace("__GATED__", repr(bool(gated)))
            .replace("__META__", repr(dict(meta or {})))
            .replace("__ROOT__", repr(root_s))
            .replace("__RESULT_REL__", repr(rel_parts)))


def _post_data(template: str, comp, params, op, runtime, source, band) -> str:
    """Expand a tcp_send data line: literal text with whole {param:…}/{operator:…}
    placeholders replaced by their single validated value."""
    import re
    def repl(m):
        return " ".join(expand_argv([m.group(0)], comp, params, op, runtime, source, band))
    return re.sub(r"\{[a-z]+:[a-z_]+\}", repl, template)


_POST_RUNNER = '''\
import os, select, socket, stat, sys, time, subprocess
STEPS = __STEPS__
BINDING = __BINDING__
GATED = __GATED__
META = __META__
ROOT = __ROOT__
RESULT_REL = __RESULT_REL__
RESULTS = []
_MAX_RECORDS = 32
_MAX_DETAIL = 200

def _close_all(fds):
    for fd in reversed(fds):
        try:
            os.close(fd)
        except OSError:
            pass

def _walk_parent():
    # DESCRIPTOR-ANCHORED descent to the sidecar's PARENT under ROOT, mirroring
    # lhpc.core.runtime_fs._walk_parent (this runner is standalone stdlib-only and cannot import
    # lhpc — keep the two in step). The ROOT is the trusted anchor and may LEGITIMATELY be a
    # symlink in the operator's setup, so it is opened WITHOUT O_NOFOLLOW; every component UNDER
    # it is opened O_DIRECTORY|O_NOFOLLOW relative to its parent's fd, so a parent swapped to a
    # symlink or a non-directory between validation and use is refused AT THE SYSCALL. O_NOFOLLOW
    # on the leaf alone never covered that. Returns the fd list (deepest last) or None.
    fds = []
    try:
        fds.append(os.open(ROOT, os.O_RDONLY | os.O_DIRECTORY))
        for comp in RESULT_REL[:-1]:
            fds.append(os.open(comp, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                               dir_fd=fds[-1]))
        return fds
    except OSError:
        _close_all(fds)
        return None

def _flush_results(done=False):
    # Terminal-outcome sidecar for the controller's status view. Best-effort: a write failure
    # must never change the runner's exit semantics, and an unsafe parent or leaf must never
    # cause a write OUTSIDE the runtime root — it fails the sidecar only.
    if not RESULT_REL:
        return
    try:
        import json
        blob = json.dumps({"v": 1, "meta": META, "binding": BINDING,
                           "steps": RESULTS[:_MAX_RECORDS], "done": done}).encode("utf-8")
    except Exception:
        return
    fds = _walk_parent()
    if fds is None:
        return
    pfd, name = fds[-1], RESULT_REL[-1]
    tmp = None
    try:
        try:
            lst = os.stat(name, dir_fd=pfd, follow_symlinks=False)
            if not stat.S_ISREG(lst.st_mode):
                return        # symlink / dir / FIFO / device leaf -> never publish through it
        except FileNotFoundError:
            pass              # first write: no leaf yet
        # UNIQUE temp leaf created RELATIVE to the held parent fd. NOTHING is pre-unlinked: the
        # old code deleted a path it had only predicted. O_EXCL means a collision is retried,
        # never consumed.
        fd = None
        for _ in range(64):
            cand = ".%s.tmp-%d-%s" % (name, os.getpid(), os.urandom(8).hex())
            try:
                fd = os.open(cand, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                             0o600, dir_fd=pfd)
            except FileExistsError:
                continue
            tmp = cand
            break
        if tmp is None:
            return
        with os.fdopen(fd, "wb") as f:
            f.write(blob)
            f.flush()
            os.fchmod(f.fileno(), 0o600)   # mode on the HELD fd (umask-proof, no re-resolve)
            fst = os.fstat(f.fileno())     # what we hold IS a fresh, single-link regular file
            if not stat.S_ISREG(fst.st_mode) or fst.st_nlink != 1:
                raise OSError("unsafe temp leaf")
            os.fsync(f.fileno())           # the rename must publish DURABLE content
        os.rename(tmp, name, src_dir_fd=pfd, dst_dir_fd=pfd)
        tmp = None
        os.fsync(pfd)                      # durable rename (parent dir entry)
    except Exception:
        if tmp is not None:
            try:
                os.unlink(tmp, dir_fd=pfd)
            except OSError:
                pass
    finally:
        _close_all(fds)

def _clean(text):
    # BOUNDED, SANITISED excerpt of a child's stderr. Tracebacks and file lines are dropped
    # whole (they leak the venv layout and, via a repr'd argument, potentially a secret); what
    # remains is collapsed to ONE printable line and hard-capped. The status view NEVER renders
    # this field — the full text goes to the post-start log, which the operator opens on purpose.
    out = []
    for line in (text or "").splitlines():
        s = line.strip()
        if not s or s.startswith("Traceback") or s.startswith("File "):
            continue
        out.append(s)
    joined = " ".join(out[-4:])
    joined = "".join(c for c in joined
                     if 0x20 <= ord(c) < 0x7F and c not in ('"', "'", "`"))
    return joined[:_MAX_DETAIL]

def _record(entry):
    # Flushed on EVERY append, so a failed step can never leave a successful empty sidecar
    # behind when the launcher exits non-zero.
    if len(RESULTS) < _MAX_RECORDS:
        RESULTS.append(entry)
        _flush_results()

def _armed():
    # ARM GATE: the controller writes ONE arm byte on stdin ONLY after this runner's ownership
    # record is durable. Until then the runner performs NO post-step side effect. EOF (the gate
    # closed without arming) or a bounded timeout -> exit having done nothing.
    try:
        r, _w, _x = select.select([0], [], [], 30)
        return bool(r) and os.read(0, 1) == b"1"
    except OSError:
        return False

def _main_ok():
    # BOUND to one exact main launch: before any side-effectful step re-verify that main pid is
    # ALIVE (not zombie/dead) and still has the SAME start time + session/group. A ceased, replaced
    # (pid reused with a new start time), or zombie main -> stop: never touch a restarted main.
    if not BINDING:
        return True
    try:
        with open("/proc/%d/stat" % BINDING["main_pid"], "rb") as f:
            rest = f.read().rsplit(b") ", 1)[1].split()
        state = rest[0].decode("ascii", "replace")
        pgrp, session, starttime = int(rest[2]), int(rest[3]), int(rest[19])
    except (OSError, ValueError, IndexError):
        return False
    if state in ("Z", "X", "x"):          # zombie / dead -> treat as ceased
        return False
    return (starttime == BINDING["main_starttime"] and session == BINDING["main_sid"]
            and pgrp == BINDING["main_pgid"])

if GATED and not _armed():
    sys.exit(0)                            # detached runner never armed -> no side effects at all

for s in STEPS:
    k = s["kind"]
    try:
        if k == "delay":
            time.sleep(s["seconds"])
        elif k == "exec":
            t0 = time.time()
            if not _main_ok():
                _record({"kind": "exec", "label": s.get("label", "exec"), "outcome": "main-gone",
                         "rc": None, "attempts": 0, "elapsed_s": 0.0})
                break                   # bound main gone/replaced/zombie -> no further side effects
            try:
                # stdout stays DEVNULL (the volume source); ONLY stderr is captured, re-emitted
                # verbatim to OUR stderr (the post-start log keeps full fidelity) and stored as a
                # sanitised, bounded excerpt that the status view never renders.
                cp = subprocess.run(s["argv"], shell=False, timeout=120,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
                rc = cp.returncode
                err = (cp.stderr or b"").decode("utf-8", "replace")
                if err:
                    sys.stderr.write(err if err.endswith("\\n") else err + "\\n")
            except subprocess.TimeoutExpired:
                _record({"kind": "exec", "label": s.get("label", "exec"), "outcome": "timeout",
                         "rc": None, "attempts": 1, "elapsed_s": round(time.time() - t0, 1),
                         "detail": ""})
                if not s.get("optional", True):
                    sys.exit(1)
                continue
            except OSError as e:        # ENOENT: the binary is not there at all
                _record({"kind": "exec", "label": s.get("label", "exec"), "outcome": "failed",
                         "rc": None, "attempts": 1, "elapsed_s": round(time.time() - t0, 1),
                         "detail": _clean(str(e))})
                if not s.get("optional", True):
                    sys.exit(1)
                continue
            _record({"kind": "exec", "label": s.get("label", "exec"),
                     "outcome": ("ok" if rc == 0 else "failed"), "rc": rc, "attempts": 1,
                     "elapsed_s": round(time.time() - t0, 1),
                     "detail": ("" if rc == 0 else _clean(err))})
            if rc != 0 and not s.get("optional", True):
                sys.exit(rc)            # a required exec that fails fails the launcher
        elif k == "tcp_wait":
            t0 = time.time()
            _lbl = s.get("label", "%s:%s" % (s["host"], s["port"]))
            if not _main_ok():
                _record({"kind": "tcp_wait", "label": _lbl, "host": s["host"], "port": s["port"],
                         "outcome": "main-gone", "attempts": 0, "elapsed_s": 0.0})
                break                   # bound main already gone -> do not even wait
            end = time.time() + s["timeout"]
            ok = False
            tries = 0
            while time.time() < end:
                if not _main_ok():
                    break               # main gone/zombie mid-wait -> no connection attempt
                tries += 1
                try:
                    with socket.create_connection((s["host"], s["port"]), 2):
                        ok = True
                        break
                except OSError:
                    time.sleep(2)
            _record({"kind": "tcp_wait", "label": _lbl, "host": s["host"], "port": s["port"],
                     "outcome": ("ready" if ok else "timeout"), "attempts": tries,
                     "elapsed_s": round(time.time() - t0, 1)})
            if not ok and not s.get("optional", True):
                sys.exit(1)             # a required endpoint that never appears fails
        elif k == "tcp_send":
            reps = s.get("repeat", 1)
            sent = 0
            t0 = time.time()
            def _done(oc, n):
                _record({"kind": "tcp_send",
                         "label": s.get("label", "%s:%s" % (s["host"], s["port"])),
                         "host": s["host"], "port": s["port"], "outcome": oc,
                         "attempts": n, "elapsed_s": round(time.time() - t0, 1)})
            def _reply(conn, budget=3.0):
                conn.settimeout(0.6)
                buf = b""
                end2 = time.time() + budget
                while time.time() < end2 and len(buf) < 4096:
                    try:
                        chunk = conn.recv(1024)
                    except socket.timeout:
                        break
                    except OSError:
                        break
                    if not chunk:
                        break
                    buf += chunk
                return buf.decode("utf-8", "replace")
            probing = bool(s.get("probe") and s.get("probe_stop_on"))
            skipped = False
            for i in range(reps):
                if not _main_ok():
                    sys.stderr.write("tcp_send %s:%s: bound main gone/replaced -> stop (no send)\\n"
                                     % (s["host"], s["port"]))
                    _done("main-gone", i)
                    sys.exit(0)          # exit WITHOUT sending — never hit a restarted main
                acked = False
                try:
                    if probing:
                        # READINESS-GATED (live finding: 18 buffered callsign-push replays
                        # per start): the guest accepts connects long before its console
                        # is alive, and it serves ONE exchange per connection. Probe on
                        # its OWN connection; NO REPLY = still booting -> retry WITHOUT
                        # sending; a matching reply = already set -> ZERO sends, ever.
                        with socket.create_connection((s["host"], s["port"]), 2) as pc:
                            pc.sendall(s["probe"].encode())
                            r = _reply(pc)
                        if not r.strip():
                            sys.stderr.write("tcp_send %s:%s: console not ready "
                                             "(attempt %d/%d) -> no send\\n"
                                             % (s["host"], s["port"], i + 1, reps))
                            raise OSError("console deaf")
                        if s["probe_stop_on"] in r:
                            sys.stderr.write("tcp_send %s:%s: probe matched -> already "
                                             "set, skipping\\n" % (s["host"], s["port"]))
                            skipped = True
                            break
                    with socket.create_connection((s["host"], s["port"]), 2) as c:
                        c.sendall(s["data"].encode())
                        if s.get("stop_on"):
                            acked = s["stop_on"] in _reply(c)
                    sent += 1                # one complete connect + sendall succeeded
                except OSError as e:
                    sys.stderr.write("tcp_send %s:%s attempt %d/%d failed: %s\\n"
                                     % (s["host"], s["port"], i + 1, reps, e))
                if acked:
                    sys.stderr.write("tcp_send %s:%s: acknowledged on attempt %d\\n"
                                     % (s["host"], s["port"], i + 1))
                    break                # ACK received: no further blind repeats
                if i + 1 < reps:
                    ivs = s.get("intervals")
                    iv = ivs[i] if ivs else s.get("interval", 0)
                    if iv:
                        time.sleep(iv)
            if skipped:
                _done("probe-matched", i + 1)
                continue                 # desired state already present
            # Truthful: a REQUIRED send fails only if EVERY attempt failed (one success is enough,
            # even if later idempotent repeats fail). An OPTIONAL send never gates the start.
            if acked:
                _done("acked", i + 1)
            elif sent == 0:
                _done("exhausted", reps)
                if not s.get("optional", True):
                    sys.stderr.write("tcp_send %s:%s: all %d attempt(s) failed\\n"
                                     % (s["host"], s["port"], reps))
                    sys.exit(1)
            else:
                _done("sent-unacked", reps)
    except Exception:
        _flush_results()
        if not s.get("optional", True):
            sys.exit(1)
_flush_results(True)
'''


def render_build_launcher(steps: list, runtime: str, source: str,
                          lock_paths: list | tuple = (), index_lock: str = "",
                          txn_dir: str = "", result_name: str = "", attempt_id: str = "",
                          op: str = "", target: str = "", stack: str = "") -> str:
    """A self-contained Python launcher that runs build/test steps sequentially with
    NO shell: it resolves `{pkgconfig:NAME}` via pkg-config and runs each argv with
    its env and cwd, streaming output. Returns nonzero on the first failing step.

    Index-to-source handoff (no race): the launcher holds the source-transaction INDEX
    lock (`index_lock`), verifies NO unresolved journal in `txn_dir`, acquires the
    `lock_paths` source flock(s) for its WHOLE lifetime, and only THEN releases the index
    lock. While the index lock is held no new journal can appear and the source lock is
    already taken, so a concurrent update/uninstall cannot race the running job, and a
    retained journal makes the job fail visibly in its log."""
    resolved = []
    for step in steps:
        # SECRETS-AT-REST: env is carried UNRESOLVED (the `@file:`/`@env:` tokens, not
        # their values) and resolved on-host at EXEC time inside the launcher runtime —
        # a secret value is NEVER written into the on-disk launcher `.py`. Env NAMES are
        # still validated here so a malformed spec fails at render, and resolution stays
        # fail-closed at exec (same `build_env` rules). Argv `{pkgconfig:}` stays deferred.
        raw_env = [[str(k), str(v)] for k, v in (step.get("env") or {}).items()]
        for k, _v in raw_env:
            if not _ENV_NAME_RE.fullmatch(k):
                raise CommandError(f"invalid environment variable name: {k!r}")
        # `{asset}/...` resolves to a read-only packaged-data path (same as the CLI path's
        # build_step_argv); `{pkgconfig:}` stays deferred to the launcher runtime; everything else is a
        # controller-derived path substitution. Without the {asset} case a build step that link-gates via
        # the shipped helper (meshcom-qemu, meshtastic) would fail the WEB/detached build with an
        # "unresolved placeholder" while the CLI build succeeded.
        def _launcher_tok(t: str) -> str:
            t = str(t)
            if t == "{asset}" or t.startswith("{asset}/"):
                return _asset_token(t)
            if t.startswith("{pkgconfig:"):
                return t
            return _paths_subst(t, runtime, source, "")
        argv = [_launcher_tok(t) for t in step.get("argv", [])]
        entry = {"argv": argv, "env_items": raw_env}
        # Quiet-step preamble: substituted at render time (static controller text, no
        # secrets) and printed by the launcher runtime BEFORE the step's `+ argv` echo.
        if step.get("announce"):
            entry["announce"] = _paths_subst(str(step["announce"]), runtime, source, "")
        resolved.append(entry)
    # Descriptor-safe spec: carry the runtime root + runtime-relative lock NAMES (all locks
    # live under `state/locks/`), NOT trusted absolute paths. The shared runtime rebuilds
    # `Paths(runtime_root)` and opens each via a full parent no-follow walk.
    from pathlib import Path as _P
    spec = {"steps": resolved, "cwd": source, "runtime_root": str(runtime),
            "lock_names": sorted(_P(p).name for p in lock_paths),
            "index_lock_name": (_P(index_lock).name if index_lock else ""),
            # Web-job attempt identity (all plain identity strings — no secrets) so the child can
            # gate on its job marker and record a terminal green/red result. "" for non-web builds.
            "result_name": result_name, "attempt_id": attempt_id,
            "op": op, "target": target, "stack": stack}
    return _BUILD_RUNNER.replace("__SPEC__", repr(spec))


# THIN wrapper: the generated launcher embeds an immutable spec literal and delegates ALL
# security-sensitive behavior (locks, journal preflight, timeout, pkg-config, process-tree
# termination, cleanup) to the tested `lhpc.core.build_launcher_runtime` module.
_BUILD_RUNNER = '''\
from lhpc.core import build_launcher_runtime
build_launcher_runtime.run(__SPEC__)
'''
