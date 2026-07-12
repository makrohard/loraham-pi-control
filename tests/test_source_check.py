"""`source_check` — the ONLY per-component network freshness probe, and its GUI surface.

Guards the two rules that make the indicator trustworthy:
  * no GET route may probe (P0.6); only the dedicated POST and the background thread do;
  * a cached verdict is shown only for the exact local head it was computed against.
"""

from __future__ import annotations

import subprocess

from lhpc.core import stackupdates as su
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import CommandResult as CR, FakeSystem
from lhpc.core.services import ControllerService

DAEMON_REMOTE = "https://github.com/makrohard/LoRaHAM_Daemon.git"
DAEMON_BRANCH = "hardening/daemon-tests"
RADIOLIB_REMOTE = "https://github.com/jgromes/RadioLib"

A = "a" * 40
B = "b" * 40


def _install(tmp_path, rel):
    d = tmp_path / rel
    d.mkdir(parents=True, exist_ok=True)
    return d


def _svc(tmp_path, commands=None):
    return ControllerService(system=FakeSystem(commands=commands or {}).system,
                             paths=Paths(runtime_root=tmp_path))


def _ls_remote(remote, ref, sha):
    return {("git", "ls-remote", remote, ref): CR(0, f"{sha}\trefs/heads/{ref}\n", "")}


def _git_src(src, sha):
    """A clean git checkout at `sha`. `probe_source` needs all three: a failing `status
    --porcelain` makes it return UNKNOWN *without* a head, and the row then renders no @head."""
    a = str(src)
    return {("git", "-C", a, "rev-parse", "HEAD"): CR(0, sha + "\n", ""),
            ("git", "-C", a, "describe", "--tags", "--always", "--dirty"): CR(0, "v111a\n", ""),
            ("git", "-C", a, "status", "--porcelain", "--untracked-files=no"): CR(0, "", "")}


# --- the probe ----------------------------------------------------------------------------------

def test_uninstalled_source_is_unknown_and_makes_no_network_call(tmp_path):
    svc = _svc(tmp_path)                                   # no src/ dirs exist
    res = svc.source_check("daemon")
    assert not any("ls-remote" in " ".join(c) for c in svc._system.runner.calls)
    assert su.view(Paths(runtime_root=tmp_path))["components"]["loraham-daemon"]["status"] == su.UNKNOWN
    # "nothing to compare" is NOT a passing check: never ok (green), never worded "up to date".
    assert not res.ok
    assert "up to date" not in res.summary
    assert "No installed/comparable sources could be checked" in res.summary


def test_all_unknown_never_reports_up_to_date(tmp_path):
    res = _svc(tmp_path).source_check()                     # whole box, nothing installed
    assert not res.ok and "up to date" not in res.summary
    assert res.data["counts"][su.UP_TO_DATE] == 0
    assert res.data["counts"][su.UNKNOWN] == res.data["checked"]


def test_mixed_up_to_date_and_unknown_is_qualified_and_not_green(tmp_path):
    # daemon installed and current; radiolib never installed -> unknown.
    ds = _install(tmp_path, "src/loraham-daemon")
    cmds = {**_ls_remote(DAEMON_REMOTE, DAEMON_BRANCH, A), **_git_src(ds, A)}
    res = _svc(tmp_path, cmds).source_check("daemon")
    assert res.summary == "1 up to date, 1 unknown/not comparable for 'daemon'."
    assert not res.ok                                       # a partial comparison is not a green
    assert "All checked sources are up to date" not in res.summary


def test_unqualified_up_to_date_requires_every_source_comparable(tmp_path):
    ds = _install(tmp_path, "src/loraham-daemon")
    rl = _install(tmp_path, "src/RadioLib")
    cmds = {**_ls_remote(DAEMON_REMOTE, DAEMON_BRANCH, A), **_git_src(ds, A),
            **_ls_remote(RADIOLIB_REMOTE, "master", A), **_git_src(rl, A)}
    res = _svc(tmp_path, cmds).source_check("daemon")
    assert res.ok and res.summary == "All checked sources are up to date for 'daemon'."


def test_behind_with_an_unknown_sibling_is_qualified_and_not_green(tmp_path):
    ds = _install(tmp_path, "src/loraham-daemon")           # radiolib absent -> unknown
    cmds = {**_ls_remote(DAEMON_REMOTE, DAEMON_BRANCH, B), **_git_src(ds, A)}
    res = _svc(tmp_path, cmds).source_check("daemon")
    assert "1 of 2 source(s) behind" in res.summary and "1 not comparable" in res.summary
    assert not res.ok


def test_behind_records_both_heads(tmp_path):
    src = _install(tmp_path, "src/loraham-daemon")
    cmds = {**_ls_remote(DAEMON_REMOTE, DAEMON_BRANCH, B), **_git_src(src, A)}
    svc = _svc(tmp_path, cmds)
    res = svc.source_check("loraham-daemon")
    assert res.ok and "1 of 1 source(s) behind" in res.summary
    e = su.view(Paths(runtime_root=tmp_path))["components"]["loraham-daemon"]
    assert e["status"] == su.BEHIND
    assert e["local_head_at_check"] == A and e["upstream_head"] == B
    assert e["remote"] == DAEMON_REMOTE and e["source_path"] == "src/loraham-daemon"


def test_up_to_date_when_heads_match(tmp_path):
    src = _install(tmp_path, "src/loraham-daemon")
    cmds = {**_ls_remote(DAEMON_REMOTE, DAEMON_BRANCH, A), **_git_src(src, A)}
    res = _svc(tmp_path, cmds).source_check("loraham-daemon")
    assert res.ok and "up to date" in res.summary
    e = su.view(Paths(runtime_root=tmp_path))["components"]["loraham-daemon"]
    assert e["status"] == su.UP_TO_DATE


def test_failed_ls_remote_is_error_not_unknown(tmp_path):
    # An unreachable remote must NOT read like "nothing to compare" — and must not report ok.
    _install(tmp_path, "src/loraham-daemon")
    cmds = {("git", "ls-remote", DAEMON_REMOTE, DAEMON_BRANCH): CR(128, "", "could not resolve host")}
    res = _svc(tmp_path, cmds).source_check("loraham-daemon")
    assert not res.ok                                       # a failed check is not a clean bill
    e = su.view(Paths(runtime_root=tmp_path))["components"]["loraham-daemon"]
    assert e["status"] == su.ERROR


def test_broken_checkout_is_error(tmp_path):
    src = _install(tmp_path, "src/loraham-daemon")
    cmds = {**_ls_remote(DAEMON_REMOTE, DAEMON_BRANCH, B),
            ("git", "-C", str(src), "rev-parse", "HEAD"): CR(128, "", "not a git repository")}
    res = _svc(tmp_path, cmds).source_check("loraham-daemon")
    assert not res.ok
    e = su.view(Paths(runtime_root=tmp_path))["components"]["loraham-daemon"]
    assert e["status"] == su.ERROR


def test_stack_sweep_covers_non_runnable_library_components(tmp_path):
    # radiolib has a remote but no run_argv — `_resolve` would drop it; the sweep must not.
    ds = _install(tmp_path, "src/loraham-daemon")
    rl = _install(tmp_path, "src/RadioLib")
    cmds = {**_ls_remote(DAEMON_REMOTE, DAEMON_BRANCH, A), **_git_src(ds, A),
            **_ls_remote(RADIOLIB_REMOTE, "master", B), **_git_src(rl, A)}
    _svc(tmp_path, cmds).source_check("daemon")
    comps = su.view(Paths(runtime_root=tmp_path))["components"]
    assert comps["loraham-daemon"]["status"] == su.UP_TO_DATE
    assert comps["radiolib"]["status"] == su.BEHIND


def test_unknown_target_errors_without_network(tmp_path):
    svc = _svc(tmp_path)
    res = svc.source_check("no-such-thing")
    assert not res.ok and "Unknown stack or component" in res.summary
    assert svc._system.runner.calls == []


# --- update_status keeps its 3-value contract ---------------------------------------------------

def test_update_status_contract_unchanged(tmp_path):
    src = _install(tmp_path, "src/loraham-daemon")
    comp = _svc(tmp_path).stack("daemon").component("loraham-daemon")

    behind = {**_ls_remote(DAEMON_REMOTE, DAEMON_BRANCH, B), **_git_src(src, A)}
    assert _svc(tmp_path, behind).update_status(comp) == "update-available"

    same = {**_ls_remote(DAEMON_REMOTE, DAEMON_BRANCH, A), **_git_src(src, A)}
    assert _svc(tmp_path, same).update_status(comp) == "up-to-date"

    # a probe ERROR collapses back to "unknown" for the legacy callers (update()'s dry-run)
    fail = {("git", "ls-remote", DAEMON_REMOTE, DAEMON_BRANCH): CR(1, "", "boom")}
    assert _svc(tmp_path, fail).update_status(comp) == "unknown"
    assert _svc(tmp_path).update_status(None) == "unknown"


# --- GUI: the rendered signal -------------------------------------------------------------------

def _repo(tmp_path, rel):
    """An installed git source whose probed HEAD the page will render as @<head>.

    Real dirs (the probe's `is_dir()` guard) AND `FakeSystem.paths` (its data-driven `fs.exists`,
    which `probe_source` consults before reading a head).
    """
    d = _install(tmp_path, rel)
    (d / ".git").mkdir(exist_ok=True)
    return d


def _fs_paths(*dirs):
    out = set()
    for d in dirs:
        out |= {str(d), f"{d}/.git"}
    return out


def _client(tmp_path, fake):
    from lhpc.adapters.web.app import create_app
    return create_app(service_factory=lambda: ControllerService(
        system=fake.system, paths=Paths(runtime_root=tmp_path))).test_client()


def _app(tmp_path, commands=None, dirs=()):
    return _client(tmp_path, FakeSystem(commands=commands or {}, paths=_fs_paths(*dirs)))


def _csrf(client, path="/stacks"):
    import re
    m = re.search(r'name="_csrf" value="([^"]+)"', client.get(path).get_data(as_text=True))
    return m.group(1) if m else ""


def _row(body, sid):
    # Summary-only slice (head/status pills live here). The action links (logs / Update) now
    # render in a .row-actions overlay AFTER </details> — use _wrap() for those.
    i = body.index('id="stackrow-' + sid + '"')
    return body[i:body.index("</summary>", i)]


def _wrap(body, sid):
    # A stack's whole wrapper: its <details> AND the .row-actions overlay after it, up to the next row.
    i = body.index('id="stackrow-' + sid + '"')
    nxt = body.find('class="stackrow-wrap"', i + 1)
    return body[i:(nxt if nxt != -1 else len(body))]


def _seed(tmp_path, entries, now=1000):
    su.record(Paths(runtime_root=tmp_path), entries, now=now)


def test_main_behind_paints_head_yellow_and_shows_the_link(tmp_path):
    ds = _repo(tmp_path, "src/loraham-daemon")
    _seed(tmp_path, {"loraham-daemon": _entry_for(su.BEHIND, A)})
    body = _app(tmp_path, _git_src(ds, A), [ds]).get("/stacks").get_data(as_text=True)
    assert "ver-yellow" in _row(body, "daemon") and "@" + A[:9] in _row(body, "daemon")
    assert ">Update</a>" in _wrap(body, "daemon")   # link is in the row-actions overlay


def test_only_a_dependency_behind_shows_the_link_but_leaves_head_grey(tmp_path):
    # The @head pill IS the main's commit — it must not go yellow because radiolib is stale.
    ds = _repo(tmp_path, "src/loraham-daemon")
    rl = _repo(tmp_path, "src/RadioLib")
    _seed(tmp_path, {"loraham-daemon": _entry_for(su.UP_TO_DATE, A),
                     "radiolib": _entry_for(su.BEHIND, B)})
    cmds = {**_git_src(ds, A), **_git_src(rl, B)}
    body = _app(tmp_path, cmds, [ds, rl]).get("/stacks").get_data(as_text=True)
    assert ">Update</a>" in _wrap(body, "daemon")   # any component behind -> link (overlay)
    assert "ver-yellow" not in _row(body, "daemon")           # but the main's head (summary) stays grey


def test_nothing_behind_and_empty_cache_show_neither(tmp_path):
    ds = _repo(tmp_path, "src/loraham-daemon")
    cmds = _git_src(ds, A)
    body = _app(tmp_path, cmds, [ds]).get("/stacks").get_data(as_text=True)
    # never checked -> no Update link in the overlay, no yellow head pill in the summary
    assert "update-link" not in _wrap(body, "daemon") and "ver-yellow" not in _row(body, "daemon")

    _seed(tmp_path, {"loraham-daemon": _entry_for(su.UP_TO_DATE, A)})
    body = _app(tmp_path, cmds, [ds]).get("/stacks").get_data(as_text=True)
    assert "update-link" not in _wrap(body, "daemon") and "ver-yellow" not in _row(body, "daemon")


def test_stale_cache_renders_unchecked_not_a_stale_verdict(tmp_path):
    # Verdicts were computed against A; the checkout has since moved to B.
    ds = _repo(tmp_path, "src/loraham-daemon")
    cmds = _git_src(ds, B)
    for status in (su.BEHIND, su.UP_TO_DATE):
        _seed(tmp_path, {"loraham-daemon": _entry_for(status, A)})
        body = _app(tmp_path, cmds, [ds]).get("/stacks").get_data(as_text=True)
        assert "update-link" not in _wrap(body, "daemon"), status   # no stale nagging
        assert "ver-yellow" not in _row(body, "daemon"), status     # no stale yellow
        assert "unchecked" in body                                  # Install panel says so


def test_update_link_opens_the_install_section(tmp_path):
    ds = _repo(tmp_path, "src/loraham-daemon")
    _seed(tmp_path, {"loraham-daemon": _entry_for(su.BEHIND, A)})
    row = _wrap(_app(tmp_path, _git_src(ds, A), [ds]).get("/stacks").get_data(as_text=True), "daemon")
    i = row.index(">Update</a>")
    href = row[row.rindex('href="', 0, i) + 6:row.index('"', row.rindex('href="', 0, i) + 6)]
    assert "open=daemon" in href and "inst=daemon" in href
    assert href.endswith("#stack-install-daemon")


def test_every_top_level_row_has_an_actions_overlay(tmp_path):
    # The logs / "Update" links live in a .row-actions overlay OUTSIDE each row's <summary>
    # (a11y). Every top-level row (controller + each stack) has one.
    body = _app(tmp_path).get("/stacks").get_data(as_text=True)
    assert body.count('class="row-actions"') >= 2         # controller row + at least one stack


# --- the network boundary (P0.6, both directions) -----------------------------------------------

def test_get_stacks_never_probes_even_with_a_populated_cache(tmp_path):
    ds = _repo(tmp_path, "src/loraham-daemon")
    _seed(tmp_path, {"loraham-daemon": _entry_for(su.BEHIND, A)})
    fake = FakeSystem(commands=_git_src(ds, A), paths=_fs_paths(ds))
    c = _client(tmp_path, fake)
    c.get("/stacks")
    assert not any("ls-remote" in " ".join(call) for call in fake.calls)


def test_source_check_post_does_probe_and_lands_on_install(tmp_path):
    ds = _repo(tmp_path, "src/loraham-daemon")
    rl = _repo(tmp_path, "src/RadioLib")
    cmds = {**_ls_remote(DAEMON_REMOTE, DAEMON_BRANCH, B), **_git_src(ds, A),
            **_ls_remote(RADIOLIB_REMOTE, "master", A), **_git_src(rl, A)}
    fake = FakeSystem(commands=cmds, paths=_fs_paths(ds, rl))
    c = _client(tmp_path, fake)
    tok = _csrf(c)
    r = c.post("/source-check/daemon", data={"_csrf": tok})
    assert r.status_code == 302
    assert "open=daemon" in r.headers["Location"] and "inst=daemon" in r.headers["Location"]
    assert r.headers["Location"].endswith("#stack-install-daemon")
    assert any("ls-remote" in " ".join(call) for call in fake.calls)     # it DID probe
    assert su.view(Paths(runtime_root=tmp_path))["components"]["loraham-daemon"]["status"] == su.BEHIND


def test_source_check_component_target_returns_to_its_stack(tmp_path):
    _repo(tmp_path, "src/RadioLib")
    c = _app(tmp_path)
    r = c.post("/source-check/radiolib", data={"_csrf": _csrf(c)})
    assert r.status_code == 302 and r.headers["Location"].endswith("#stack-install-daemon")


def test_source_check_requires_csrf_and_a_known_target(tmp_path):
    fake = FakeSystem()
    c = _client(tmp_path, fake)
    assert c.post("/source-check/daemon").status_code == 400          # no CSRF token
    assert c.post("/source-check/nope", data={"_csrf": _csrf(c)}).status_code == 404
    assert not any("ls-remote" in " ".join(call) for call in fake.calls)


def test_source_check_is_not_an_action_op():
    # It mutates nothing but the cache marker; it must not enter the lifecycle dispatch.
    assert "source-check" not in ControllerService.WEB_ACTIONS
    assert "check" not in ControllerService.WEB_ACTIONS


def _entry_for(status, at):
    return {"remote": DAEMON_REMOTE, "source_path": "src/x",
            "local_head_at_check": at, "upstream_head": B, "status": status}
