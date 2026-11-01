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


# A running nginx can still serve the old config after its file is gone: owed, never cleared (B3-D22).
_MISSING = "deployed nginx config is missing — Apply the webserver configuration"


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
        return _keep(paths, _MISSING)
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
        return _keep(paths, _MISSING)
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
