"""The browser bridge. Holds ONE persistent app + test client (so the Flask session cookie
and CSRF token survive across navigations), exposes handle() for the JS layer to call per
navigation/form submit, and (de)serializes the simulated state for localStorage."""
from __future__ import annotations

import json

from .app import build_app

# Bump when the saved-state meaning changes so returning visitors with an older save get
# the current default (e.g. the all-installed box) instead of a stale layout.
_STATE_SCHEMA = 2

_svc = None
_client = None


def _seed(svc) -> None:
    """Present a CONFIGURED box: a radio board + callsign, so the dashboard shows a set-up
    station instead of first-run 'configure your hardware' prompts. Non-fatal."""
    try:
        svc.set_hardware_setup("loraham")
    except Exception:
        pass
    try:
        from lhpc.core.config import save_operator_config
        save_operator_config(svc._paths, "DL0DEM")
    except Exception:
        pass


def boot(state_json: str = "") -> str:
    global _svc, _client  # noqa: PLW0603  (module-level singletons for the browser session)
    import os
    root = "/tmp/lhpcroot"
    os.makedirs(root, exist_ok=True)
    # A stable simulated boot id clears the "boot identity unavailable" warning (lhpc reads
    # LHPC_BOOT_ID_FILE when set — the documented sandbox override).
    bid = os.path.join(root, "boot_id")
    if not os.path.exists(bid):
        with open(bid, "w") as fh:
            fh.write("00000000-0000-4000-8000-0000000000d1\n")
    os.environ["LHPC_BOOT_ID_FILE"] = bid
    os.environ.setdefault("LHPC_SYSTEM_PROVIDER", "lhpc_demo.provider:build")
    os.environ.setdefault("LHPC_RUNTIME_ROOT", root)
    app, _svc = build_app()
    _client = app.test_client()
    _seed(_svc)
    # Restore ONLY a current-schema save. A missing/old _schema (e.g. a returning visitor
    # whose localStorage predates the all-installed default) is discarded, so they get the
    # current default instead of a stale empty box.
    restored = None
    if state_json:
        try:
            parsed = json.loads(state_json)
            if (isinstance(parsed, dict) and parsed.get("_schema") == _STATE_SCHEMA
                    and isinstance(parsed.get("stacks"), dict)):
                restored = parsed["stacks"]
        except ValueError:
            restored = None
    if restored is not None:
        _svc._demo = restored           # returning visitor's saved (current-schema) state
    else:
        _svc.seed_all_installed()       # fresh or stale-schema: a fully installed, not-running box
    return "ok"


def handle(method: str, path: str, form_json: str = "") -> str:
    """Route one request through the real app's test client and return {status,ctype,body}."""
    data = None
    if form_json:
        try:
            data = json.loads(form_json)
        except ValueError:
            data = None
    m = str(method).upper()
    resp = (_client.post(path, data=data, follow_redirects=True) if m == "POST"
            else _client.get(path, follow_redirects=True))
    ctype = resp.headers.get("Content-Type", "")
    return json.dumps({"status": resp.status_code, "ctype": ctype,
                       "body": resp.get_data(as_text=True)})


def dump_state() -> str:
    return json.dumps({"_schema": _STATE_SCHEMA, "stacks": getattr(_svc, "_demo", {})})


def reset_state() -> str:
    if _svc is not None:
        _svc._demo = {}
        # Params the session saved land in the (Pyodide) FS via the real config APIs, so a
        # reset must clear config/stacks too — not just the in-memory model — or a prior
        # run's kiss.toml etc. survives the reset.
        try:
            for f in _svc._paths.under("config", "stacks").glob("*.toml"):
                try:
                    f.unlink()
                except OSError:
                    pass
        except OSError:
            pass
        _svc._invalidate_config()
        _svc.seed_all_installed()       # Reset -> the clean fully-installed baseline
    return "ok"
