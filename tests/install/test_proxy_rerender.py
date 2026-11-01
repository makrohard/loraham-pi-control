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
