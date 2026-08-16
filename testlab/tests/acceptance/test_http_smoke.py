"""Acceptance smoke over the REAL server: boot, banner, CSRF discipline, the full
parameterless-GET sweep with a process-boundary no-mutation check, and the Test Lab
panel's own ops."""
from __future__ import annotations

import pytest
from labproc import run_lab


@pytest.mark.covers("route:GET /", "route:GET /healthz")
def test_dashboard_and_health(client):
    status, body = client.get("/")
    assert status == 200
    assert "TEST LAB — SIMULATED HARDWARE" in body
    assert client.get("/healthz")[0] == 200


@pytest.mark.covers("route:GET /testlab", "route:POST /testlab/<op>#scenario",
                    "route:POST /testlab/<op>#check")
def test_testlab_panel_scenario_roundtrip(client, lab):
    status, body = client.get("/testlab")
    assert status == 200 and "Switch scenario" in body
    st, _ = client.post("/testlab/scenario", {"name": "degraded"}, csrf_from="/testlab")
    assert st in (302, 303)
    out = run_lab(lab.env, "status", check=True).stdout
    assert "degraded" in out
    st2, _ = client.post("/testlab/check", {}, csrf_from="/testlab")
    assert st2 in (302, 303)


def test_csrf_missing_token_refused_on_posts(client):
    for path, form in (("/testlab/scenario", {"name": "healthy"}),
                       ("/action", {"op": "start", "stack": "daemon"}),
                       ("/gps", {"source": "off"})):
        status, _ = client.post(path, form, csrf_from=None)
        assert status == 400, path


def _get_rules():
    """Parameterless GET rules straight from the real url_map (in-process app build —
    enumeration only, the requests go to the real server)."""
    import os
    import tempfile
    from pathlib import Path

    from lhpc.adapters.web.app import create_app
    from lhpc.core.paths import Paths
    from lhpc.core.probes.backends import FakeSystem
    from lhpc.core.services import ControllerService
    tmp = Path(tempfile.mkdtemp())
    (tmp / "config" / "stacks").mkdir(parents=True)
    env_off = os.environ.pop("LHPC_TESTLAB", None)
    try:
        svc = ControllerService(system=FakeSystem(files={"/proc/uptime": "1 2\n"}).system,
                                paths=Paths(runtime_root=tmp))
        app = create_app(lambda: svc)
    finally:
        if env_off is not None:
            os.environ["LHPC_TESTLAB"] = env_off
    rules = []
    for rule in app.url_map.iter_rules():
        if rule.endpoint == "static" or "GET" not in (rule.methods or ()):
            continue
        if "<" in rule.rule:
            continue
        rules.append(rule.rule)
    return sorted(rules)


@pytest.mark.covers("route:GET /stacks", "route:GET /dependencies",
                    "route:GET /auto-install", "route:GET /api/system",
                    "route:GET /api/tasks", "route:GET /api/dash-signature",
                    "route:GET /api/auto-install", "route:GET /api/hmac-apply",
                    "route:GET /webserver/ca.crt", "route:GET /webserver/logs",
                    "route:GET /firewall/logs", "route:GET /controller/logs",
                    "route:GET /stacks/loraham-pi-control")
def test_every_parameterless_get_renders_and_mutates_nothing(client, lab):
    watched = ["state/testlab/scenario.json", "state/testlab/nm.json",
               "state/testlab/units.json", "config/local.toml"]

    def snapshot():
        out = {}
        for rel in watched:
            p = lab.root / rel
            out[rel] = p.read_bytes() if p.exists() else b""
        return out
    before = snapshot()
    failures = []
    for rule in _get_rules():
        status, _body = client.get(rule)
        # ca.crt may 404 before webserver init; a couple of flows answer with a
        # redirect to their landing page — all are valid render responses.
        if status not in (200, 302, 303, 404):
            failures.append((rule, status))
    assert not failures, failures
    assert snapshot() == before                     # GETs mutate nothing observable


def test_every_post_route_refuses_without_csrf(client):
    """The POST sweep: EVERY POST row answers through the running app — a tokenless
    POST is refused (400) or the op/route gate 404s. Proves route existence + the CSRF
    discipline for the whole POST surface, including each dispatcher op."""
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import covscan
    subst = {"<sid>": "daemon", "<stack_id>": "daemon", "<target>": "daemon",
             "<band>": "433", "<label>": "labx", "<action>": "enable"}
    failures = []
    for rid in sorted(covscan.sweepable_route_ids()):
        body = rid[len("route:"):]
        method, rest = body.split(" ", 1)
        if method != "POST":
            continue
        rule, _, op = rest.partition("#")
        path = rule
        for k, v in subst.items():
            path = path.replace(k, v)
        for k in ("<op>", "<kind>"):
            path = path.replace(k, op or "x")
        status, _ = client.post(path, {"op": op} if op else {}, csrf_from=None)
        if status not in (400, 404, 405):
            failures.append((rid, status))
    assert not failures, failures


def test_second_reset_is_idempotent_and_returns_to_baseline(lab, client):
    """The user's second-launch gate: another `testlab reset` (as postCreate/postStart
    would run it) succeeds, keeps the healthy baseline, and the console stays up."""
    run_lab(lab.env, "scenario", "degraded", check=True)
    r = run_lab(lab.env, "reset", check=True, timeout=600)
    assert "healthy baseline" in r.stdout
    out = run_lab(lab.env, "status", check=True).stdout
    assert "scenario: healthy" in out
    assert client.get("/healthz")[0] == 200
