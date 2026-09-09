"""Acceptance smoke over the REAL server: boot, banner, CSRF discipline, the full
parameterless-GET sweep with a process-boundary no-mutation check, and the Test Lab
panel's own ops."""
from __future__ import annotations

from lhpc_testlab.testing import run_lab


def test_dashboard_and_health(client):
    status, body = client.get("/")
    assert status == 200
    assert "TEST LAB — SIMULATED HARDWARE" in body
    assert client.get("/healthz")[0] == 200


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


def _app_rules():
    """The real app's url_map, built in process purely to ENUMERATE routes — every request in
    these tests goes to the running server. One builder, so the GET and POST sweeps can never
    disagree about what the surface is."""
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
    return [r for r in app.url_map.iter_rules() if r.endpoint != "static"]


def _get_rules():
    """Parameterless GET rules — the ones a sweep can call without inventing an argument."""
    return sorted(r.rule for r in _app_rules()
                  if "GET" in (r.methods or ()) and "<" not in r.rule)


def _post_rules():
    """Every POST rule the app declares."""
    return sorted(r.rule for r in _app_rules() if "POST" in (r.methods or ()))


def test_every_parameterless_get_renders_and_preserves_watched_lab_state(client, lab):
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
        # Only ca.crt may legitimately 404 (no server CA before webserver init); a couple of
        # flows answer with a redirect to their landing page. Every other rule must RENDER —
        # accepting 404 for all of them would hide a page that started aborting.
        ok = (200, 302, 303, 404) if rule == "/webserver/ca.crt" else (200, 302, 303)
        if status not in ok:
            failures.append((rule, status))
    assert not failures, failures
    # Narrow, and honest about it: these four files are the lab state a GET could plausibly
    # disturb, not a whole-tree mutation detector.
    assert snapshot() == before, "a GET changed lab state"


def test_every_post_route_refuses_without_csrf(client):
    """Every POST route the running app declares refuses a tokenless POST with exactly 400.

    The route list comes from the app's OWN url_map, like the GET sweep above, so the surface can
    never drift from a hand-kept inventory.

    EXACTLY 400, never a broader set. Accepting 404 too would let this pass after the very
    regression it exists to catch: a CSRF check moved BEHIND target validation still refuses an
    unauthenticated request today, but would answer 404 for an unknown target and the sweep would
    stay green.

    Each placeholder is therefore substituted with a target the route actually SERVES, which is
    not the same stack for all of them: `<sid>` reaches the HMAC routes, and HMAC applies only to
    meshcom; `<stack_id>` reaches `mode`, which only meshcore carries. Substituting one stack
    everywhere makes several routes 404 before they ever reach their CSRF check — which is
    exactly the blindness this assertion exists to remove.
    """
    subst = {"<sid>": "meshcom", "<stack_id>": "meshcore", "<target>": "daemon",
             "<band>": "433", "<label>": "labx", "<action>": "enable",
             "<op>": "start", "<kind>": "reboot", "<name>": "x"}
    failures = []
    skipped = []
    for rule in sorted(_post_rules()):
        path = rule
        for k, v in subst.items():
            path = path.replace(k, v)
        if "<" in path:                      # an unknown converter: record it, never silently skip
            skipped.append(rule)
            continue
        status, _ = client.post(path, {}, csrf_from=None)
        if status != 400:
            failures.append((rule, status))
    assert not failures, f"POST routes that did not refuse a tokenless POST with 400: {failures}"
    assert not skipped, (
        f"no substitution for {skipped} — add one, or this route is not being swept at all")


def test_second_reset_is_idempotent_and_returns_to_baseline(lab, client):
    """The user's second-launch gate: another `testlab reset` (as postCreate/postStart
    would run it) succeeds, keeps the healthy baseline, and the console stays up."""
    run_lab(lab.env, "scenario", "degraded", check=True)
    r = run_lab(lab.env, "reset", check=True, timeout=600)
    assert "healthy baseline" in r.stdout
    out = run_lab(lab.env, "status", check=True).stdout
    assert "scenario: healthy" in out
    assert client.get("/healthz")[0] == 200
