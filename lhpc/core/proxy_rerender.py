"""The stack proxies re-rendered after a self-update (CO-14).

A release can change what the nginx config renders for the SAME saved settings: a stack's deny
list, a notice. An updated box kept serving the previous release's render until someone pressed
Apply, while a fresh install rendered the new one (field find, 10-19).

  * BEFORE the checkout moves, the old process writes `state/webserver-render.pending` (`mark`).
    A plain marker carries no version dependency; a marker the update cannot write stops it.
  * AFTER the advance, the NEW checkout's interpreter runs `lhpc _webserver-rerender`
    (`reconcile`): it renders fresh with the new code and compares with the deployed config.
    Equal: the marker is cleared. Different: the Webserver Apply runs (render, `nginx -t`, reload)
    and a successful Apply clears it. The old process cannot do this itself: it imported the
    previous release's renderer.
  * The Apply runs only when the saved webserver settings ARE the applied ones (Save is not Apply:
    a saved-but-unapplied policy is never pushed live in the background), so the listener policy
    stays what it was. Not applied, refused (the firewall gate, `nginx -t`) or failed: the marker
    stays with its reason, and every console page shows "Apply needed: <reason>" until a Webserver
    Apply succeeds (`webserver_apply` clears it through `_record_applied`).
"""
from __future__ import annotations

import json
import subprocess
import sys
from os.path import exists, join

from . import runtime_fs
from .paths import PathContainmentError, Paths

MARKER = ("state", "webserver-render.pending")
_TIMEOUT_S = 120.0
_NOT_APPLIED = ("the saved webserver settings are not applied yet — Apply them, and this "
                "release's proxy configuration loads with them")


def mark(paths: Paths) -> str:
    """Record, before the checkout moves, that the proxies need a re-render: "" when recorded,
    else the cause."""
    try:
        runtime_fs.write_marker(paths, paths.under(*MARKER), "{}")
    except Exception as exc:                            # containment / fs error
        return f"{'/'.join(MARKER)}: {type(exc).__name__}: {' '.join(str(exc).split())}"
    return ""


def pending(paths: Paths) -> str | None:
    """None when nothing is pending; else the reason the re-render is still owed ("" before the
    new interpreter has run)."""
    try:
        raw = runtime_fs.read_text_regular(paths, paths.under(*MARKER), max_bytes=1 << 16)
    except FileNotFoundError:
        return None
    except (OSError, PathContainmentError):
        return ""
    try:
        rec = json.loads(raw or "{}")
    except ValueError:
        return ""
    return str(rec.get("detail", "")) if isinstance(rec, dict) else ""


def clear(paths: Paths) -> None:
    try:
        runtime_fs.unlink(paths, paths.under(*MARKER))
    except (OSError, PathContainmentError):
        pass


def _keep(paths: Paths, why: str) -> tuple[bool, str]:
    try:
        runtime_fs.write_marker(paths, paths.under(*MARKER), json.dumps({"detail": why}))
    except (OSError, PathContainmentError):
        pass
    return False, why


# A running nginx can still serve the old config after its file is gone: owed unless nginx's pid file and the applied
# record are both proven absent (B3-D22, B3-D22a, B3-D22b, B3-D24, B3-D25).
_MISSING = "deployed nginx config is missing — Apply the webserver configuration"


def _pid_state(paths: Paths) -> str:
    """nginx's pid file, strictly: "present" (a regular readable file holding a decimal pid), "absent" (ENOENT on the
    leaf or its directory) or "unknown" (anything else: unreadable, not regular, a symlink, an unsafe parent, no pid)."""
    from .webserver import NGINX_PID
    try:
        raw = runtime_fs.read_text_regular(paths, paths.under(*NGINX_PID), max_bytes=4096)
    except FileNotFoundError:
        return "absent"
    except Exception:
        return "unknown"
    return "present" if raw.strip().isdigit() else "unknown"


def _evidence_state(paths: Paths) -> str:
    """The webserver evidence's applied record, strictly: "present" (schema 1 with a well-formed applied_snapshot),
    "absent" (no file, or schema 1 without an applied_snapshot: verified, never applied — webserver.py writes that) or
    "unknown" (unreadable, not JSON, a decode that raises, another schema, a malformed snapshot, over the cap).
    `read_applied` folds every unknown into {}, which reads as "never applied"; this probe does not."""
    from .webserver import EVIDENCE, EVIDENCE_SCHEMA
    try:
        raw = runtime_fs.read_text_regular(paths, paths.under(*EVIDENCE))
    except FileNotFoundError:
        return "absent"
    except Exception:
        return "unknown"
    try:
        data = json.loads(raw)
    except Exception:                                   # ValueError, or RecursionError from deep nesting
        return "unknown"
    schema = data.get("schema") if isinstance(data, dict) else None
    if type(schema) is not int or schema != EVIDENCE_SCHEMA:     # exact type: True == 1 and 1.0 == 1 (B3-D27)
        return "unknown"
    snap = data.get("applied_snapshot")
    if snap is None:
        return "absent"
    if isinstance(snap, dict) and isinstance(snap.get("console"), dict) and isinstance(snap.get("proxies"), list):
        return "present"
    return "unknown"


def _nginx_may_run(paths: Paths) -> bool:
    """Whether an nginx may still serve an old config. Only when nginx's pid file AND the applied record are both
    proven absent can nothing serve one (a box that never set up nginx); PRESENT or UNKNOWN keeps the Apply owed
    (B3-D25). An installed nginx unit file is no sign: every update writes the whole managed unit set (B3-D22b)."""
    return not (_pid_state(paths) == "absent" and _evidence_state(paths) == "absent")


def _missing(paths: Paths) -> tuple[bool, str]:
    if _nginx_may_run(paths):
        return _keep(paths, _MISSING)
    clear(paths)                                        # no nginx was ever set up: the first Apply renders fresh
    return True, "no deployed nginx config"


def _unreadable(exc: Exception) -> str:
    return f"the deployed nginx config could not be read ({type(exc).__name__}) — Apply the webserver configuration"


def _deployed(paths: Paths) -> str | None:
    """The deployed nginx config, or None when there is none. Any other read error is the caller's: it keeps the
    marker with the cause (`_unreadable`), so the update's own result stands."""
    from .webserver import NGINX_CONF
    try:
        return runtime_fs.read_text_regular(paths, paths.under(*NGINX_CONF))
    except FileNotFoundError:
        return None


def reconcile(svc) -> tuple[bool, str]:
    """Run in the NEW checkout's interpreter: (marker cleared, what happened)."""
    from . import webserver as _ws
    paths = svc._paths
    if pending(paths) is None:
        return True, "nothing pending"
    try:
        deployed = _deployed(paths)
    except (OSError, PathContainmentError) as exc:
        return _keep(paths, _unreadable(exc))
    if deployed is None:
        return _missing(paths)
    fresh = _ws.render_nginx_config(paths, svc.config().webserver, svc._stack_web_proxies())
    if fresh == deployed:
        clear(paths)
        return True, "the deployed nginx config is this release's render"
    applied = {k: v for k, v in _ws.read_applied(paths).items() if k != "at"}
    if applied != svc._ws_policy_now():
        return _keep(paths, _NOT_APPLIED)
    res = svc.webserver_apply()
    if res.ok:
        clear(paths)
        return True, "this release's proxy configuration applied"
    return _keep(paths, f"this release's proxy configuration is not loaded — {res.summary}")


def _python(paths: Paths) -> str:
    from .updater_units import deployment_paths
    py = join(deployment_paths(str(paths.runtime_root))[2], "bin", "python")
    return py if exists(py) else sys.executable


def after_advance(svc) -> tuple[bool, str]:
    """The steps after a real advance that need the new checkout, on both update paths (the operator
    `_apply_and_sync` and the one-click helper): the service's unit refresh, then the proxies'
    re-render (`after_update`). Returns the unit refresh's (ok, detail); the re-render's outcome is
    the marker's, shown by the banner, and does not change the update's."""
    units = svc._refresh_units_post_update()
    after_update(svc._paths)
    return units


def after_update(paths: Paths) -> tuple[bool, str]:
    """Run in the OLD process once the checkout advanced: hand the re-render to the new
    checkout's interpreter. (marker cleared, detail)."""
    if pending(paths) is None:
        return True, "nothing pending"
    try:
        deployed = _deployed(paths)
    except (OSError, PathContainmentError) as exc:
        return _keep(paths, _unreadable(exc))
    if deployed is None:
        return _missing(paths)
    argv = [_python(paths), "-m", "lhpc", "_webserver-rerender", "--root", str(paths.runtime_root)]
    try:
        run = subprocess.run(argv, capture_output=True, text=True, timeout=_TIMEOUT_S, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        return _keep(paths, f"the re-render could not run ({type(exc).__name__}) — Apply the "
                     "webserver configuration")
    out = (run.stdout or "").strip().splitlines()
    if run.returncode == 0:
        return True, out[-1] if out else "re-rendered"
    why = pending(paths)
    if why:
        return False, why
    return _keep(paths, "the re-render failed ("
                 + ((run.stderr or "").strip().splitlines() or ["no output"])[-1][:160]
                 + ") — Apply the webserver configuration")
