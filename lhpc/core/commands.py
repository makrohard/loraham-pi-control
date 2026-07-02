"""Structured command model — turns a component's typed launch spec into a real
argv list, environment, and controller-owned pre/post steps, with NO shell.

A run/build/test command is an argv TOKEN TEMPLATE: an ordered list where each
entry is either a literal token or a single whole placeholder. Placeholders:

    {param:NAME}      a run-param -> 0+ validated argv tokens (emit_param)
    {operator:callsign} / {operator:locator}   -> one validated token
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
                          .replace("{locator}", op.locator or "")
                          .replace("{runtime}", runtime).replace("{source}", source))
                val = validators.validate_param(p, raw)
                out.extend(emit_param(p, val))
                continue
            if kind == "operator" and name == "callsign":
                out.append(validators.callsign(op.callsign or "N0CALL",
                                                field="callsign") or "N0CALL")
                continue
            if kind == "operator" and name == "locator":
                out.append(validators.locator(op.locator, field="locator"))
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
    (which blocks the launch/build) — it never silently becomes an empty string."""
    env: dict[str, str] = {}
    for key, value in (env_items or ()):
        if not _ENV_NAME_RE.fullmatch(key):
            raise CommandError(f"invalid environment variable name: {key!r}")
        v = str(value)
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


def build_step_argv(step: dict, runner, runtime: str, source: str) -> list[str]:
    """Resolve a build/test step's argv (literals + a `{pkgconfig:NAME}` token,
    expanded by invoking pkg-config with shell=False — never a backtick subshell)."""
    out: list[str] = []
    for tok in step.get("argv", []):
        if tok.startswith("{pkgconfig:") and tok.endswith("}"):
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


def render_post_launcher(steps, comp, params, op, runtime: str, source: str,
                         band: str = "", binding: dict | None = None, gated: bool = False) -> str:
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
            graw = (graw.replace("{callsign}", op.callsign or "N0CALL")
                        .replace("{locator}", op.locator or "")).strip()
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
            resolved.append({"kind": "exec", "argv": [exe, *argv[1:]],
                             "optional": optional})
        elif kind in ("tcp_wait", "tcp_send"):
            d = {"kind": kind, "host": step.get("host", "127.0.0.1"),
                 "port": int(step["port"]),
                 "optional": bool(step.get("optional")) and not step.get("required")}
            if kind == "tcp_wait":
                d["timeout"] = float(step.get("timeout", 60))
            else:
                d["data"] = _post_data(step.get("data", ""), comp, params, op, runtime, source, band)
                # A slow guest (e.g. QEMU firmware) may open its console port long before it is
                # ready to process a command. `repeat`/`interval` re-send the line until it lands
                # (idempotent settings like --setcall); default = send once. Malformed retry
                # metadata is a typed render failure — fail-closed, never a silent clamp.
                d["repeat"] = _post_repeat(step.get("repeat", 1))
                d["interval"] = _post_interval(step.get("interval", 0))
            resolved.append(d)
        else:
            raise CommandError(f"unknown post-step kind {kind!r}")
    return (_POST_RUNNER.replace("__STEPS__", repr(resolved))
            .replace("__BINDING__", repr(binding))
            .replace("__GATED__", repr(bool(gated))))


def _post_data(template: str, comp, params, op, runtime, source, band) -> str:
    """Expand a tcp_send data line: literal text with whole {param:…}/{operator:…}
    placeholders replaced by their single validated value."""
    import re
    def repl(m):
        return " ".join(expand_argv([m.group(0)], comp, params, op, runtime, source, band))
    return re.sub(r"\{[a-z]+:[a-z_]+\}", repl, template)


_POST_RUNNER = '''\
import os, select, socket, sys, time, subprocess
STEPS = __STEPS__
BINDING = __BINDING__
GATED = __GATED__

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
            if not _main_ok():
                break                   # bound main gone/replaced/zombie -> no further side effects
            rc = subprocess.run(s["argv"], shell=False, timeout=120,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode
            if rc != 0 and not s.get("optional", True):
                sys.exit(rc)            # a required exec that fails fails the launcher
        elif k == "tcp_wait":
            if not _main_ok():
                break                   # bound main already gone -> do not even wait
            end = time.time() + s["timeout"]
            ok = False
            while time.time() < end:
                if not _main_ok():
                    break               # main gone/zombie mid-wait -> no connection attempt
                try:
                    with socket.create_connection((s["host"], s["port"]), 2):
                        ok = True
                        break
                except OSError:
                    time.sleep(2)
            if not ok and not s.get("optional", True):
                sys.exit(1)             # a required endpoint that never appears fails
        elif k == "tcp_send":
            reps = s.get("repeat", 1)
            sent = 0
            for i in range(reps):
                if not _main_ok():
                    sys.stderr.write("tcp_send %s:%s: bound main gone/replaced -> stop (no send)\\n"
                                     % (s["host"], s["port"]))
                    sys.exit(0)          # exit WITHOUT sending — never hit a restarted main
                try:
                    with socket.create_connection((s["host"], s["port"]), 2) as c:
                        c.sendall(s["data"].encode())
                    sent += 1                # one complete connect + sendall succeeded
                except OSError as e:
                    sys.stderr.write("tcp_send %s:%s attempt %d/%d failed: %s\\n"
                                     % (s["host"], s["port"], i + 1, reps, e))
                if i + 1 < reps and s.get("interval", 0):
                    time.sleep(s["interval"])
            # Truthful: a REQUIRED send fails only if EVERY attempt failed (one success is enough,
            # even if later idempotent repeats fail). An OPTIONAL send never gates the start.
            if sent == 0 and not s.get("optional", True):
                sys.stderr.write("tcp_send %s:%s: all %d attempt(s) failed\\n"
                                 % (s["host"], s["port"], reps))
                sys.exit(1)
    except Exception:
        if not s.get("optional", True):
            sys.exit(1)
'''


def render_build_launcher(steps: list, runtime: str, source: str,
                          lock_paths: list | tuple = (), index_lock: str = "",
                          txn_dir: str = "") -> str:
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
        # FAIL-CLOSED env: same `build_env` rules as normal execution — a missing/
        # empty `@file:` secret, bad @env, or invalid env name raises CommandError
        # (blocks the build) rather than silently becoming an empty value.
        env = build_env(tuple((step.get("env") or {}).items()), runtime, source)
        argv = [_paths_subst(t, runtime, source, "") if not t.startswith("{pkgconfig:") else t
                for t in step.get("argv", [])]
        resolved.append({"argv": argv, "env": env})
    # Descriptor-safe spec: carry the runtime root + runtime-relative lock NAMES (all locks
    # live under `state/locks/`), NOT trusted absolute paths. The shared runtime rebuilds
    # `Paths(runtime_root)` and opens each via a full parent no-follow walk.
    from pathlib import Path as _P
    spec = {"steps": resolved, "cwd": source, "runtime_root": str(runtime),
            "lock_names": sorted(_P(p).name for p in lock_paths),
            "index_lock_name": (_P(index_lock).name if index_lock else "")}
    return _BUILD_RUNNER.replace("__SPEC__", repr(spec))


# THIN wrapper: the generated launcher embeds an immutable spec literal and delegates ALL
# security-sensitive behavior (locks, journal preflight, timeout, pkg-config, process-tree
# termination, cleanup) to the tested `lhpc.core.build_launcher_runtime` module.
_BUILD_RUNNER = '''\
from lhpc.core import build_launcher_runtime
build_launcher_runtime.run(__SPEC__)
'''


def render_wrapper(comp, op, runtime: str, source: str) -> str:
    """Generate a manual launcher as PYTHON (no shell) from the structured command
    spec: it runs typed pre-steps, sets cwd/env, and os.execvpe()s a FIXED default
    argv plus the operator's extra sys.argv[1:] as separate tokens. No command string
    is built from configuration; no bash/sh/eval/`exec cd`."""
    argv = expand_argv(comp.run_argv, comp, None, op, runtime, source, "")
    cwd = _paths_subst(comp.run_cwd, runtime, source, "") if comp.run_cwd else source
    env = build_env(comp.run_env, runtime, source, "")
    # Same normalizer the in-process start uses -> a wrapper runs byte-identical pre-steps.
    pre = normalize_pre_steps(comp.pre_steps, runtime, source, "")
    tx = "TX-capable (RX-safe defaults)" if comp.tx_capable else "RX-only"
    # Embed values as Python literals via repr() — robust (no quote-collision) and
    # safe for str/list/dict-of-str. Never executed as a string.
    return (_WRAPPER.replace("__ARGV__", repr(argv)).replace("__ENVLIT__", repr(env))
            .replace("__CWDLIT__", repr(cwd)).replace("__PRE__", repr(pre))
            .replace("__RUNTIME__", repr(runtime))
            .replace("__ID__", comp.id).replace("__EXE__", argv[0] if argv else "?")
            .replace("__CWD__", cwd).replace("__TX__", tx))


_WRAPPER = '''\
#!/usr/bin/env python3
# Generated by lhpc — manual launcher for "__ID__" (from the structured command spec).
# Executable: __EXE__   cwd: __CWD__   Radio: __TX__
# Runs the real command directly (os.execvpe, no shell). Append your own arguments;
# they are forwarded as separate argv tokens. lhpc never auto-enables TX.
#
# Pre-steps are applied through the installed LHPC runtime helper, which RE-VALIDATES
# every mutable destination at execution time (rejecting a symlink leaf/parent
# introduced after this wrapper was generated). It fails closed before exec.
import os, sys
ARGV    = __ARGV__
CWD     = __CWDLIT__
ENV     = __ENVLIT__
PRE     = __PRE__
RUNTIME = __RUNTIME__
if PRE:
    try:
        from lhpc.core.wrapper_runtime import apply_pre_steps
    except Exception as exc:
        sys.stderr.write("lhpc wrapper: runtime helper unavailable: %s\\n" % exc)
        raise SystemExit(3)
    try:
        apply_pre_steps(RUNTIME, PRE)
    except Exception as exc:
        sys.stderr.write("lhpc wrapper: unsafe pre-step, refusing to launch: %s\\n" % exc)
        raise SystemExit(4)
os.chdir(CWD)
os.execvpe(ARGV[0], ARGV + sys.argv[1:], {**os.environ, **ENV})
'''
