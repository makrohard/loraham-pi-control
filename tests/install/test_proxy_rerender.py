"""T-CO8: a deny-list change reaches an updated box through `self-update --apply` (CO-14).

An updated box kept serving the previous release's proxy render — a stack's deny list among it —
while a fresh install rendered the new one (field find, 10-19). The deployed nginx config here is
the previous release's render of the same saved settings: this release's meshcore deny list
with one location missing. After the update the new checkout's interpreter renders fresh: the
difference is applied (render, `nginx -t`, reload) and the marker cleared; an Apply that fails
keeps the marker and every page says "Apply needed: <why>".

Collaborators stubbed: git (`selfupdate.apply_update` advances by calling the update's
`before_mutation` hook, as the real one does right before the merge), systemd (the unit refresh)
and the new interpreter (the `lhpc _webserver-rerender` subprocess runs `reconcile` on a fresh
service, which is what that process sees).
"""
from __future__ import annotations

import os
import subprocess
import sys

import pytest

from selfupdate_fakes import WEB_INACTIVE
from lhpc.core import proxy_rerender, runtime_fs, selfupdate, webserver
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import CommandResult as CR
from lhpc.core.probes.backends import FakeSystem, Listener
from lhpc.core.services import ControllerService

_LISTENERS = [Listener("ipv4", "127.0.0.1", 8443, 1),      # loopback console
              Listener("ipv4", "0.0.0.0", 8446, 3),        # the proxy itself
              Listener("ipv4", "127.0.0.1", 8788, 2)]      # upstream, loopback


def _box(tmp_path, monkeypatch, *, nginx_t):
    """An applied meshcore proxy whose deployed config is the previous release's render, and a
    checkout whose update advances. Returns (service factory, paths, previous render, fake)."""
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    paths = Paths(runtime_root=tmp_path)
    boot = ControllerService(system=FakeSystem().system, paths=paths)
    boot.webserver_init(dns_sans=["pi.local"])
    assert boot.stack_web_configure("meshcore", mode="lan", port=8446,
                                    cidrs=["192.168.1.0/24"], confirm=True).ok
    runtime_fs.mkdir(paths, "state", "run")
    runtime_fs.write_marker(paths, paths.under(*webserver.NGINX_PID), str(os.getpid()))
    staged = str(paths.under(*webserver.NGINX_CONF_STAGED))
    live = str(paths.under(*webserver.NGINX_CONF))
    checkout = tmp_path / "checkout"
    (checkout / "lhpc").mkdir(parents=True)
    (checkout / "lhpc" / "version.py").write_text(f'__version__ = "{selfupdate.__version__}"\n')
    monkeypatch.setattr(selfupdate, "repo_root", staticmethod(lambda: checkout))
    rows = {tuple(a): CR(0, "", "") for a in selfupdate.venv_sync_argvs(sys.executable, checkout)}
    rows.update(WEB_INACTIVE)
    rows.update({("nginx", "-v"): CR(0, "", "nginx/1.24"),
                 ("nginx", "-t", "-c", staged): CR(0, "", "ok"),
                 ("nginx", "-s", "reload", "-c", live): CR(0, "", "")})
    fake = FakeSystem(commands=rows, listeners=list(_LISTENERS))

    def svc():                      # a fresh service: what the next process sees
        return ControllerService(system=fake.system, paths=paths)
    assert svc().webserver_apply().ok                       # applied by this release ...
    now = webserver.read_applied(paths)
    deny = [ln for ln in runtime_fs.read_text_regular(paths, paths.under(*webserver.NGINX_CONF))
            .splitlines(keepends=True) if ln.lstrip().startswith("location ~ ")]
    assert deny, "the meshcore proxy renders no deny location"
    previous = runtime_fs.read_text_regular(paths, paths.under(*webserver.NGINX_CONF)).replace(
        deny[0], "", 1)
    runtime_fs.atomic_write(paths, paths.under(*webserver.NGINX_CONF), previous, mode=0o644)
    assert webserver.read_applied(paths) == now             # ... deployed as the previous one's
    fake.commands[("nginx", "-t", "-c", staged)] = nginx_t

    def advance(system, paths_, *, force=False, branch="", before_mutation=None):
        if before_mutation is not None:
            before_mutation("a" * 40, "b" * 40, "main", False)
        return {"ok": True, "deps_changed": False, "new_head_short": "bbbbbbb",
                "new_version": selfupdate.__version__, "message": selfupdate.APPLIED_RESTART_MESSAGE}
    monkeypatch.setattr(selfupdate, "apply_update", advance)
    monkeypatch.setattr(ControllerService, "_refresh_units_post_update",
                        lambda self: (True, "units canonical"))
    real_run = subprocess.run

    def new_interpreter(argv, *a, **k):
        av = [str(x) for x in argv]
        if av[1:4] != ["-m", "lhpc", "_webserver-rerender"]:
            return real_run(argv, *a, **k)
        assert av[4:] == ["--root", str(paths.runtime_root)], av
        ok, detail = proxy_rerender.reconcile(svc())
        return subprocess.CompletedProcess(av, 0 if ok else 1, detail + "\n", "")
    monkeypatch.setattr(proxy_rerender.subprocess, "run", new_interpreter)
    return svc, paths, previous, fake


def _deployed(paths):
    return runtime_fs.read_text_regular(paths, paths.under(*webserver.NGINX_CONF))


def test_a_deny_list_change_is_applied_by_the_update(tmp_path, monkeypatch):
    svc, paths, previous, _fake = _box(tmp_path, monkeypatch, nginx_t=CR(0, "", "ok"))
    res = svc().self_update_apply_operator()
    assert res.ok, res.summary
    fresh = webserver.render_nginx_config(paths, svc().config().webserver,
                                          svc()._stack_web_proxies())
    assert _deployed(paths) == fresh != previous            # this release's deny list is live
    assert proxy_rerender.pending(paths) is None            # nothing owed, no banner


def test_a_failing_nginx_t_keeps_the_marker_and_every_page_says_apply_needed(
        tmp_path, monkeypatch, web):
    svc, paths, previous, fake = _box(tmp_path, monkeypatch,
                                      nginx_t=CR(1, "", "nginx: [emerg] unknown directive \"x\""))
    svc().self_update_apply_operator()
    assert _deployed(paths) == previous                     # the previous proven config stays
    why = proxy_rerender.pending(paths)
    assert why and "nginx config validation failed" in why
    page = web(service_factory=svc).get("/").get_data(as_text=True)
    assert "Apply needed: this release&#39;s proxy configuration is not loaded" in page
    # A Webserver Apply that succeeds discharges it.
    fake.commands[("nginx", "-t", "-c", str(paths.under(*webserver.NGINX_CONF_STAGED)))] = (
        CR(0, "", "ok"))
    assert svc().webserver_apply().ok
    assert proxy_rerender.pending(paths) is None
    assert "Apply needed" not in web(service_factory=svc).get("/").get_data(as_text=True)


def test_a_saved_but_unapplied_proxy_edit_is_not_pushed_live_by_the_update(tmp_path, monkeypatch):
    """Save is not Apply: with an edit saved and not applied, the update applies nothing in the
    background. The marker stays and says the saved settings wait for their Apply."""
    svc, paths, previous, _fake = _box(tmp_path, monkeypatch, nginx_t=CR(0, "", "ok"))
    assert svc().stack_web_configure("meshcore", cidrs=["192.168.2.0/24"], confirm=True).ok
    svc().self_update_apply_operator()
    assert _deployed(paths) == previous
    why = proxy_rerender.pending(paths)
    assert why is not None and why.startswith("the saved webserver settings are not applied")


def test_an_unreadable_deployed_config_keeps_the_marker_and_the_update_still_reports(tmp_path, monkeypatch):
    """The deployed nginx config cannot be read (a permission error, after the checkout moved): the update still
    returns its result, the marker stays with the cause, and nothing else is touched."""
    svc, paths, previous, _fake = _box(tmp_path, monkeypatch, nginx_t=CR(0, "", "ok"))
    real = runtime_fs.read_text_regular
    conf = paths.under(*webserver.NGINX_CONF)

    def denied(paths_, path, *a, **k):
        if path == conf:
            raise PermissionError(13, "Permission denied", str(path))
        return real(paths_, path, *a, **k)
    monkeypatch.setattr(runtime_fs, "read_text_regular", denied)
    res = svc().self_update_apply_operator()
    monkeypatch.setattr(runtime_fs, "read_text_regular", real)
    assert res.data.get("update_applied"), res.summary
    why = proxy_rerender.pending(paths)
    assert why == ("the deployed nginx config could not be read (PermissionError) — Apply the webserver "
                   "configuration")
    assert _deployed(paths) == previous


def test_the_hidden_verb_with_nothing_pending_changes_nothing(tmp_path, capsys):
    from lhpc.adapters.cli.main import main
    assert main(["_webserver-rerender", "--root", str(tmp_path)]) == 0
    assert capsys.readouterr().out.strip().endswith("nothing pending")


def test_the_hidden_verb_keeps_the_marker_when_the_deployed_config_cannot_be_read(tmp_path, monkeypatch, capsys):
    """The new interpreter's half (`reconcile`, reached through the hidden verb): a deployed config it cannot read
    keeps the marker with the cause and exits 1; no Apply runs."""
    from lhpc.adapters.cli.main import main
    paths = Paths(runtime_root=tmp_path)
    (tmp_path / "config" / "nginx").mkdir(parents=True)
    conf = paths.under(*webserver.NGINX_CONF)
    conf.write_text("# deployed\n")
    assert proxy_rerender.mark(paths) == ""
    real = runtime_fs.read_text_regular

    def denied(paths_, path, *a, **k):
        if path == conf:
            raise PermissionError(13, "Permission denied", str(path))
        return real(paths_, path, *a, **k)
    monkeypatch.setattr(runtime_fs, "read_text_regular", denied)
    rc = main(["_webserver-rerender", "--root", str(tmp_path)])
    monkeypatch.setattr(runtime_fs, "read_text_regular", real)
    assert rc == 1
    assert proxy_rerender.pending(paths) == ("the deployed nginx config could not be read (PermissionError) — "
                                             "Apply the webserver configuration")
    assert capsys.readouterr().out.strip().endswith("Apply the webserver configuration")



def test_the_apply_needed_banner_stands_when_the_firewall_notice_raises(tmp_path, monkeypatch, web):
    """G1-3 B1: the two page-wide notices are read on their own; a failure reading the firewall's deferred-Apply
    marker must not hide a re-render that is owed."""
    from lhpc.core.services import ControllerService
    paths = Paths(runtime_root=tmp_path)
    (tmp_path / "state").mkdir(parents=True, exist_ok=True)
    assert proxy_rerender.mark(paths) == ""
    proxy_rerender._keep(paths, "this release's proxy configuration is not loaded — test")

    def broken(self):
        raise RuntimeError("firewall marker unreadable")
    monkeypatch.setattr(ControllerService, "webserver_apply_pending", broken)
    page = web(paths=paths).get("/").get_data(as_text=True)
    assert "Apply needed: this release&#39;s proxy configuration is not loaded — test" in page


def test_a_marker_that_breaks_the_reader_still_shows_the_generic_banner(tmp_path, web):
    """B3-D16a: a marker of 65535 '[' makes json.loads raise RecursionError, which pending() does not catch; the
    page's own except (G1-3 B1) turns it into the banner with its generic reason, never a hidden notice."""
    paths = Paths(runtime_root=tmp_path)
    (tmp_path / "state").mkdir(parents=True, exist_ok=True)
    paths.under(*proxy_rerender.MARKER).write_text("[" * 65535)
    with pytest.raises(RecursionError):        # the reader itself fails: the page's except is the path under test
        proxy_rerender.pending(paths)
    page = web(paths=paths).get("/").get_data(as_text=True)
    assert 'id="ws-render-notice">Apply needed: the update has not re-rendered the stack proxies yet' in " ".join(page.split())


def test_a_marker_that_cannot_be_read_shows_the_generic_banner(tmp_path, web):
    """pending()'s OSError branch: a marker that is not a regular file (here a directory) is owed with no reason yet;
    every page shows the banner with its generic reason."""
    paths = Paths(runtime_root=tmp_path)
    paths.under(*proxy_rerender.MARKER).mkdir(parents=True)
    assert proxy_rerender.pending(paths) == ""
    page = web(paths=paths).get("/").get_data(as_text=True)
    assert 'id="ws-render-notice">Apply needed: the update has not re-rendered the stack proxies yet' in " ".join(page.split())


MISSING = "deployed nginx config is missing — Apply the webserver configuration"


def test_a_missing_deployed_config_keeps_the_marker_while_nginx_still_runs(tmp_path, monkeypatch):
    """B3-D22: the deployed nginx config is gone but nginx still runs (its pid file stands): a running nginx can still
    serve the old config, so the update keeps the marker with an owed Apply, runs no Apply, and still reports."""
    svc, paths, _previous, _fake = _box(tmp_path, monkeypatch, nginx_t=CR(0, "", "ok"))
    paths.under(*webserver.NGINX_CONF).unlink()
    assert paths.under(*webserver.NGINX_PID).exists()           # nginx still running
    res = svc().self_update_apply_operator()
    assert res.data.get("update_applied"), res.summary
    assert proxy_rerender.pending(paths) == MISSING
    assert not paths.under(*webserver.NGINX_CONF).exists()      # no Apply ran


def test_the_hidden_verb_keeps_the_marker_when_the_deployed_config_is_missing(tmp_path, monkeypatch, capsys):
    """B3-D22, the new interpreter's half (`reconcile`, through the hidden verb): a missing deployed config keeps
    the marker with an owed Apply and exits 1."""
    from lhpc.adapters.cli.main import main
    _svc, paths, _previous, _fake = _box(tmp_path, monkeypatch, nginx_t=CR(0, "", "ok"))
    paths.under(*webserver.NGINX_CONF).unlink()
    assert proxy_rerender.mark(paths) == ""
    assert main(["_webserver-rerender", "--root", str(tmp_path)]) == 1
    assert proxy_rerender.pending(paths) == MISSING
    assert capsys.readouterr().out.strip().endswith(MISSING)


def _update_without_nginx(tmp_path, monkeypatch):
    """A box that never set up nginx (the loopback console: no nginx config, no nginx pid file, no recorded Apply) and a checkout whose update advances. Returns (service factory, paths)."""
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    paths = Paths(runtime_root=tmp_path)
    checkout = tmp_path / "checkout"
    (checkout / "lhpc").mkdir(parents=True)
    (checkout / "lhpc" / "version.py").write_text(f'__version__ = "{selfupdate.__version__}"\n')
    monkeypatch.setattr(selfupdate, "repo_root", staticmethod(lambda: checkout))
    rows = {tuple(a): CR(0, "", "") for a in selfupdate.venv_sync_argvs(sys.executable, checkout)}
    rows.update(WEB_INACTIVE)
    fake = FakeSystem(commands=rows, listeners=list(_LISTENERS))

    def advance(system, paths_, *, force=False, branch="", before_mutation=None):
        if before_mutation is not None:
            before_mutation("a" * 40, "b" * 40, "main", False)
        return {"ok": True, "deps_changed": False, "new_head_short": "bbbbbbb",
                "new_version": selfupdate.__version__, "message": selfupdate.APPLIED_RESTART_MESSAGE}
    monkeypatch.setattr(selfupdate, "apply_update", advance)
    monkeypatch.setattr(ControllerService, "_refresh_units_post_update", lambda self: (True, "units canonical"))
    return (lambda: ControllerService(system=fake.system, paths=paths)), paths


def test_a_box_that_never_set_up_nginx_is_not_left_with_a_permanent_banner(tmp_path, monkeypatch, web):
    """B3-D22a: no deployed config, no nginx pid file, no recorded Apply (a supported base
    install): nothing can serve an old config, so the update clears the marker and no page says Apply needed."""
    svc, paths = _update_without_nginx(tmp_path, monkeypatch)
    res = svc().self_update_apply_operator()
    assert res.data.get("update_applied"), res.summary
    assert proxy_rerender.pending(paths) is None
    page = web(paths=paths).get("/").get_data(as_text=True)
    assert 'id="ws-render-notice"' not in page


def test_the_hidden_verb_clears_the_marker_on_a_box_that_never_set_up_nginx(tmp_path, capsys):
    from lhpc.adapters.cli.main import main
    paths = Paths(runtime_root=tmp_path)
    (tmp_path / "state").mkdir(parents=True)
    assert proxy_rerender.mark(paths) == ""
    assert main(["_webserver-rerender", "--root", str(tmp_path)]) == 0
    assert proxy_rerender.pending(paths) is None
    assert capsys.readouterr().out.strip().endswith("no deployed nginx config")


@pytest.mark.parametrize("signal", ["pid-file", "applied-record"])
def test_each_sign_that_nginx_may_run_keeps_a_missing_config_owed(tmp_path, monkeypatch, capsys, signal):
    """B3-D22a, B3-D22b: either sign that an nginx may still serve an old config (its pid file, a recorded Apply) keeps
    a missing deployed config owed; the hidden verb exits 1."""
    from lhpc.adapters.cli.main import main
    if signal == "applied-record":                  # an applied box whose nginx pid file and config are gone
        _svc, paths, _previous, _fake = _box(tmp_path, monkeypatch, nginx_t=CR(0, "", "ok"))
        paths.under(*webserver.NGINX_PID).unlink()
        assert webserver.read_applied(paths)
    else:
        paths = Paths(runtime_root=tmp_path)
        (tmp_path / "state" / "run").mkdir(parents=True)
        paths.under(*webserver.NGINX_PID).write_text("4242\n")
    paths.under(*webserver.NGINX_CONF).unlink(missing_ok=True)
    assert proxy_rerender.mark(paths) == ""
    assert main(["_webserver-rerender", "--root", str(tmp_path)]) == 1
    assert proxy_rerender.pending(paths) == MISSING


def test_a_managed_box_that_never_set_up_nginx_clears_the_marker(tmp_path, monkeypatch, capsys):
    """B3-D22b: the managed unit set (written by every update's unit refresh) includes lhpc-nginx.service, so a unit
    FILE is no sign that nginx runs. A managed box with all units written, no nginx pid file and no recorded Apply:
    the hidden verb clears the marker and exits 0."""
    from pathlib import Path as _P

    from lhpc.adapters.cli.main import main
    from lhpc.core import updater_units
    paths = Paths(runtime_root=tmp_path)
    (tmp_path / "state").mkdir(parents=True)
    user_dir = _P(os.path.expanduser("~")) / ".config" / "systemd" / "user"
    user_dir.mkdir(parents=True)
    updater_units.write_set(user_dir, str(tmp_path))
    assert (user_dir / updater_units.NGINX_UNIT).exists()          # the managed set carries the nginx unit
    assert proxy_rerender.mark(paths) == ""
    assert main(["_webserver-rerender", "--root", str(tmp_path)]) == 0
    assert proxy_rerender.pending(paths) is None


def test_a_webserver_evidence_file_that_breaks_its_reader_keeps_the_marker_owed(tmp_path, capsys):
    """B3-D24: a deeply nested webserver evidence file makes json.loads raise RecursionError through read_applied. With
    no deployed config, the may-run test must not let that out of the update (after_update runs in-process, after the
    advance) or the hidden verb: it counts as may-run, so the marker stays with the owed Apply."""
    from lhpc.adapters.cli.main import main
    paths = Paths(runtime_root=tmp_path)
    paths.under(*webserver.EVIDENCE).parent.mkdir(parents=True, exist_ok=True)
    paths.under(*webserver.EVIDENCE).write_text("[" * 200000)
    (tmp_path / "state").mkdir(parents=True, exist_ok=True)
    assert proxy_rerender.mark(paths) == ""
    assert proxy_rerender.after_update(paths) == (False, MISSING)        # the update's in-process half
    assert main(["_webserver-rerender", "--root", str(tmp_path)]) == 1   # the new interpreter's half
    assert proxy_rerender.pending(paths) == MISSING


# ---- B3-D25: the exhaustive state table (b3-r19/D25-STATE-TABLE): every PID input class x every evidence input class,
# through both public paths; the marker is cleared only when both are ABSENT ---------------------------------------

_SNAP = {"console": {}, "proxies": []}


def _pid(paths, cls):
    pid = paths.under(*webserver.NGINX_PID)
    if cls in ("P7b", "P7c", "P7d"):                # state/run itself is not a plain directory
        run, root = pid.parent, paths.runtime_root
        if cls == "P7b":                            # a symlink to a real directory that holds a pid
            (root / "realrun").mkdir()
            (root / "realrun" / "nginx.pid").write_text("4242\n")
            run.symlink_to(root / "realrun")
        elif cls == "P7c":                          # a dangling symlink
            run.symlink_to(root / "nowhere")
        else:                                       # a regular file
            run.write_text("x")
        return
    if cls != "P2":
        pid.parent.mkdir(parents=True, exist_ok=True)
    if cls == "P3":
        pid.write_text("4242\n")
    elif cls == "P4":
        pid.write_text("not-a-pid\n")
    elif cls == "P5":
        pid.write_text("4242\n")
        pid.chmod(0)
    elif cls == "P6a":
        pid.mkdir()
    elif cls == "P6b":                              # a FIFO: the probe must not block on it
        os.mkfifo(pid)
    elif cls == "P7a":                              # a symlink leaf
        real = pid.parent / "elsewhere.pid"
        real.write_text("4242\n")
        pid.symlink_to(real)
    elif cls == "P8":                               # the pid file's directory cannot be searched: no stat at all
        pid.write_text("4242\n")
        pid.parent.chmod(0)


def _evidence(paths, cls):
    import json as _json
    ev = paths.under(*webserver.EVIDENCE)
    ev.parent.mkdir(parents=True, exist_ok=True)
    body = {"E2": _json.dumps({"schema": 1, "applied_snapshot": _SNAP}),
            "E3": _json.dumps({"schema": 1}),
            "E4a": _json.dumps({"schema": 1, "applied_snapshot": _SNAP}),
            "E5": "not json {",
            "E6": "[" * 200000,
            "E7": "[1, 2]",
            "E8": _json.dumps({"schema": 0, "applied_snapshot": _SNAP}),
            "E9": _json.dumps({"schema": 1, "applied_snapshot": {"console": "x", "proxies": []}}),
            "E10": _json.dumps({"schema": 1, "pad": "x" * (1 << 20), "applied_snapshot": _SNAP})}.get(cls)
    if cls == "E4b":                                # a symlink to a valid evidence file
        good = paths.runtime_root / "good.json"
        good.write_text(_json.dumps({"schema": 1, "applied_snapshot": _SNAP}))
        ev.symlink_to(good)
    elif cls == "E4c":                              # a dangling symlink
        ev.symlink_to(paths.runtime_root / "nowhere.json")
    elif cls == "E4d":                              # a directory
        ev.mkdir()
    elif cls == "E4e":                              # a FIFO: the probe must not block on it
        os.mkfifo(ev)
    if body is not None:
        ev.write_text(body)
    if cls == "E4a":
        ev.chmod(0)


PID_CLASSES = ["P1", "P2", "P3", "P4", "P5", "P6a", "P6b", "P7a", "P7b", "P7c", "P7d", "P8"]
EV_CLASSES = ["E1", "E2", "E3", "E4a", "E4b", "E4c", "E4d", "E4e", "E5", "E6", "E7", "E8", "E9", "E10"]
CLEARS = {("P1", "E1"), ("P1", "E3"), ("P2", "E1"), ("P2", "E3")}


@pytest.mark.parametrize("ev", EV_CLASSES)
@pytest.mark.parametrize("pid", PID_CLASSES)
def test_the_marker_is_cleared_only_when_pid_and_evidence_are_both_absent(tmp_path, capsys, request, pid, ev):
    """B3-D25: with no deployed nginx config, the update's half (after_update, in-process) and the new interpreter's
    half (the hidden verb) clear the marker only when the nginx pid file and the webserver evidence are both ABSENT;
    every PRESENT or UNKNOWN keeps it (the Apply stays owed)."""
    from lhpc.adapters.cli.main import main
    paths = Paths(runtime_root=tmp_path)
    (tmp_path / "state").mkdir(parents=True, exist_ok=True)
    request.addfinalizer(lambda: [p.chmod(0o700) for p in (tmp_path / "state" / "run", tmp_path / "state")
                                  if p.is_dir() and not p.is_symlink()])
    _pid(paths, pid)
    _evidence(paths, ev)
    clears = (pid, ev) in CLEARS
    assert proxy_rerender.mark(paths) == ""
    assert proxy_rerender.after_update(paths) == ((True, "no deployed nginx config") if clears else (False, MISSING))
    assert proxy_rerender.pending(paths) == (None if clears else MISSING)
    assert proxy_rerender.mark(paths) == ""
    assert main(["_webserver-rerender", "--root", str(tmp_path)]) == (0 if clears else 1)
    assert proxy_rerender.pending(paths) == (None if clears else MISSING)


# ---- B3-D27: the type sweep: every JSON field the evidence probe reads x every JSON type class (bool, int, float, str,
# null, list, object) plus the exact value, with the pid file absent and the config missing; only an exact schema 1 with
# no snapshot (or a null one, B3-D25a J3) clears the marker ----------------------------------------------------------

_T = {"bool": True, "int": 2, "float": 1.0, "str": "1", "null": None, "list": [1], "object": {"v": 1}}
SWEEP = ([("schema", t, v, False) for t, v in _T.items()] + [("schema", "exact", 1, True)]
         + [("applied_snapshot", t, v, t == "null") for t, v in
            {"bool": True, "int": 1, "float": 1.0, "str": "x", "null": None, "list": [], "object": {"v": 1}}.items()]
         + [("applied_snapshot", "exact", _SNAP, False)]
         + [("console", t, v, False) for t, v in {**_T, "list": [], "object": {}}.items() if t != "object"]
         + [("console", "exact", {}, False)]
         + [("proxies", t, v, False) for t, v in {**_T, "list": []}.items() if t != "list"]
         + [("proxies", "exact", [], False)])


def _swept(field, value):
    if field == "schema":
        return {"schema": value}
    if field == "applied_snapshot":
        return {"schema": 1, "applied_snapshot": value}
    snap = {"console": {}, "proxies": []}
    snap[field] = value
    return {"schema": 1, "applied_snapshot": snap}


@pytest.mark.parametrize("field, tclass, value, clears", SWEEP, ids=[f"{f}-{t}" for f, t, _v, _c in SWEEP])
def test_every_evidence_field_is_read_by_its_exact_type(tmp_path, capsys, field, tclass, value, clears):
    """B3-D27: True == 1 and 1.0 == 1 in Python, so an equality test let {"schema": true} or {"schema": 1.0} pass as
    schema 1. Each field the probe reads is now checked by its exact JSON type; any other type is UNKNOWN and keeps the
    marker. Through after_update and the hidden verb."""
    import json as _json
    from lhpc.adapters.cli.main import main
    paths = Paths(runtime_root=tmp_path)
    paths.under(*webserver.NGINX_PID).parent.mkdir(parents=True)          # state/run present, no pid file
    paths.under(*webserver.EVIDENCE).write_text(_json.dumps(_swept(field, value)))
    assert proxy_rerender.mark(paths) == ""
    assert proxy_rerender.after_update(paths) == ((True, "no deployed nginx config") if clears else (False, MISSING))
    assert proxy_rerender.mark(paths) == ""
    assert main(["_webserver-rerender", "--root", str(tmp_path)]) == (0 if clears else 1)
    assert proxy_rerender.pending(paths) == (None if clears else MISSING)
