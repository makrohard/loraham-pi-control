"""Self-hosted controller identity — the dedicated `[controller]` entity, its central
refusal from generic verbs, the STRICT live identity proof, the file-safe + schema-safe
cached status envelope, and the controller-runtime lock (web SHARED vs apply EXCLUSIVE).

Identity tests use REAL git in throwaway temp repos under a temp runtime root (never the
real checkout, never network). `selfupdate.repo_root()` and `lhpc.__file__` are monkey-
patched to the temp checkout so the path/realpath/repo equality checks are exercised.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

import lhpc
from lhpc.core import manifest as mf
from lhpc.core import selfupdate
from lhpc.core.manifest import ManifestError, parse_controller
from lhpc.core.model import ControllerSpec
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem, RealSystem
from lhpc.core.services import ControllerService, _canon_git_url

_CANON_REMOTE = "https://github.com/makrohard/loraham-pi-control.git"
_GENERIC_VERBS = ("install", "update", "uninstall", "clean", "build", "test", "start", "stop")

_GIT_ENV = {
    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@e", "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@e", "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null",
    "GIT_TERMINAL_PROMPT": "0", "HOME": "/nonexistent", "PATH": "/usr/bin:/bin",
}


def _git(cwd, *args):
    r = subprocess.run(["git", *args], cwd=str(cwd), env=_GIT_ENV, capture_output=True, text=True)
    assert r.returncode == 0, f"git {args} failed: {r.stderr}"
    return r.stdout.strip()


# --------------------------------------------------------------------------- manifest / model

def _ctrl(**over) -> dict:
    base = {"id": "loraham-pi-control", "display_name": "LoRaHAM Pi Control",
            "source_path": "src/loraham-pi-control", "branch": "main", "remote": _CANON_REMOTE}
    base.update(over)
    return {"controller": base}


def test_controller_parses_ok():
    c = parse_controller(_ctrl())
    assert isinstance(c, ControllerSpec) and c.id == "loraham-pi-control"


def test_absent_controller_is_none():
    assert parse_controller({"stack": []}) is None


def test_unknown_key_rejected():
    with pytest.raises(ManifestError):
        parse_controller({"controller": {**_ctrl()["controller"], "extra": "x"}})


def test_nested_controller_table_rejected():
    data = _ctrl()
    data["controller"]["nested"] = {"k": "v"}      # a `[controller.nested]` sub-table
    with pytest.raises(ManifestError):
        parse_controller(data)


def test_array_of_tables_rejected():
    with pytest.raises(ManifestError):
        parse_controller({"controller": [_ctrl()["controller"]]})   # `[[controller]]`


def test_fixed_source_path_enforced():
    with pytest.raises(ManifestError):
        parse_controller(_ctrl(source_path="src/elsewhere"))


def test_fixed_branch_enforced():
    with pytest.raises(ManifestError):
        parse_controller(_ctrl(branch="dev"))


def test_empty_field_rejected():
    with pytest.raises(ManifestError):
        parse_controller(_ctrl(remote=""))


def test_id_collision_rejected():
    with pytest.raises(ManifestError):
        parse_controller(_ctrl(id="daemon"), known_ids={"daemon"})


def test_packaged_manifest_has_controller_and_stacks():
    assert mf.load_controller() is not None
    assert len(mf.load_manifest()) >= 1              # load_manifest stack contract unchanged


# --------------------------------------------------------------------------- central refusal (B2)

def _svc(tmp_path) -> ControllerService:
    return ControllerService(system=FakeSystem(cmdlines_data={}).system,
                             paths=Paths(runtime_root=tmp_path))


def test_every_generic_verb_refuses_controller(tmp_path):
    svc = _svc(tmp_path)
    cid = svc.controller().id
    for verb in _GENERIC_VERBS:
        res = getattr(svc, verb)(cid)
        assert not res.ok, f"{verb} should refuse the controller"
        assert "controller-managed" in res.summary
        assert "lhpc self-update" in " ".join(res.next_commands)


def test_refusal_precedes_resolution(tmp_path, monkeypatch):
    """The guard returns BEFORE generic target resolution/mutation runs."""
    svc = _svc(tmp_path)

    def _boom(*a, **k):
        raise AssertionError("target resolution ran despite the controller guard")

    monkeypatch.setattr(svc, "_resolve", _boom)
    monkeypatch.setattr(svc, "_with_source", _boom)
    res = svc.build(svc.controller().id)
    assert not res.ok and "controller-managed" in res.summary


def test_update_controller_is_not_selfupdate_alias(tmp_path):
    svc = _svc(tmp_path)
    res = svc.update(svc.controller().id)
    assert not res.ok and "controller-managed" in res.summary


def test_controller_absent_from_stack_machinery(tmp_path):
    svc = _svc(tmp_path)
    cid = svc.controller().id
    assert cid not in {s.id for s in svc.stacks()}
    assert svc.stack(cid) is None
    assert cid not in {c.id for s in svc.stacks() for c in s.components}


def test_normal_stack_not_refused(tmp_path):
    svc = _svc(tmp_path)
    res = svc.build("daemon")
    assert "controller-managed" not in res.summary


# --------------------------------------------------------------------------- canonical origin

@pytest.mark.parametrize("a,b", [
    (_CANON_REMOTE, "git@github.com:makrohard/loraham-pi-control.git"),
    (_CANON_REMOTE, "https://github.com/makrohard/loraham-pi-control"),
    (_CANON_REMOTE, "ssh://git@github.com/makrohard/loraham-pi-control.git/"),
])
def test_canon_git_url_equivalents(a, b):
    assert _canon_git_url(a) == _canon_git_url(b)


def test_canon_git_url_distinguishes_repos():
    assert _canon_git_url(_CANON_REMOTE) != _canon_git_url("https://github.com/x/other.git")


# --------------------------------------------------------------------------- identity proof (B3)

def _make_checkout(rt: Path, *, branch="main", origin=_CANON_REMOTE) -> Path:
    """A real git checkout at rt/src/loraham-pi-control on `branch` with `origin`, and an
    `lhpc/` package dir so `Path(lhpc.__file__).parents[1]` resolves to the checkout."""
    os.chmod(rt, 0o700)
    src = rt / "src"
    src.mkdir()
    os.chmod(src, 0o700)
    co = src / "loraham-pi-control"
    co.mkdir()
    os.chmod(co, 0o700)
    (co / "lhpc").mkdir()
    (co / "lhpc" / "__init__.py").write_text("")
    _git(co, "init", "-q")
    _git(co, "checkout", "-q", "-b", branch)
    _git(co, "remote", "add", "origin", origin)
    (co / "README").write_text("x")
    _git(co, "add", "-A")
    _git(co, "commit", "-q", "-m", "seed")
    return co


@pytest.fixture
def identity_svc(tmp_path, monkeypatch):
    """A service whose controller layout is a real, correct checkout — repo_root() and
    lhpc.__file__ point at it. Individual tests then break ONE property."""
    co = _make_checkout(tmp_path)
    monkeypatch.setattr(selfupdate, "repo_root", lambda: co)
    monkeypatch.setattr(lhpc, "__file__", str(co / "lhpc" / "__init__.py"))
    svc = ControllerService(system=RealSystem(), paths=Paths(runtime_root=tmp_path))
    return svc, co, tmp_path


def test_identity_ok(identity_svc):
    svc, _co, _rt = identity_svc
    v = svc.controller_identity_live()
    assert v["ok"], v["reason"]


def test_missing_checkout_is_not_applicable_not_unsafe(tmp_path, monkeypatch):
    """A deployment with no in-root checkout (bootstrap-only / not self-hosted) is NEUTRAL
    (not_applicable), NOT a security failure — it must not read as UNSAFE."""
    monkeypatch.setattr(selfupdate, "repo_root", lambda: None)
    svc = ControllerService(system=RealSystem(), paths=Paths(runtime_root=tmp_path))
    v = svc.controller_identity_live()
    assert v["status"] == "not_applicable" and not v["ok"] and "not self-hosted" in v["reason"]


def test_identity_rejects_symlinked_src_parent(identity_svc, tmp_path):
    svc, co, rt = identity_svc
    # Replace src/ with a symlink to an identically-populated real dir elsewhere: a
    # leaf-only check would pass, but the PARENT is now a symlink.
    real = tmp_path.parent / (tmp_path.name + "_real_src")
    (rt / "src").rename(real)
    (rt / "src").symlink_to(real)
    v = svc.controller_identity_live()
    assert not v["ok"] and "symlink" in v["reason"]


def test_identity_rejects_symlinked_leaf(identity_svc, tmp_path):
    svc, co, rt = identity_svc
    real = tmp_path.parent / (tmp_path.name + "_real_co")
    co.rename(real)
    co.symlink_to(real)
    v = svc.controller_identity_live()
    assert not v["ok"] and "symlink" in v["reason"]


def test_identity_rejects_group_writable(identity_svc):
    svc, co, rt = identity_svc
    os.chmod(co, 0o770)                       # group-writable
    v = svc.controller_identity_live()
    assert not v["ok"] and "group/other-writable" in v["reason"]


def test_lhpc_running_from_other_checkout_is_not_applicable(identity_svc, tmp_path, monkeypatch):
    """If an in-root checkout exists but the RUNNING lhpc is a different tree (a dev
    instance run against this root, a tangled deployment), it is not self-hosted -> NEUTRAL,
    not UNSAFE."""
    svc, co, rt = identity_svc
    other = tmp_path.parent / (tmp_path.name + "_other")
    other.mkdir(exist_ok=True)
    monkeypatch.setattr(selfupdate, "repo_root", lambda: other)
    v = svc.controller_identity_live()
    assert v["status"] == "not_applicable" and not v["ok"] and "not self-hosted" in v["reason"]


def test_identity_rejects_imported_pkg_mismatch(identity_svc, tmp_path, monkeypatch):
    svc, co, rt = identity_svc
    monkeypatch.setattr(lhpc, "__file__", "/nonexistent/lhpc/__init__.py")
    v = svc.controller_identity_live()
    assert not v["ok"] and "imported package repo" in v["reason"]


def test_identity_rejects_wrong_branch(tmp_path, monkeypatch):
    co = _make_checkout(tmp_path, branch="feature")
    monkeypatch.setattr(selfupdate, "repo_root", lambda: co)
    monkeypatch.setattr(lhpc, "__file__", str(co / "lhpc" / "__init__.py"))
    svc = ControllerService(system=RealSystem(), paths=Paths(runtime_root=tmp_path))
    v = svc.controller_identity_live()
    assert not v["ok"] and "branch" in v["reason"]


def test_identity_rejects_detached_head(identity_svc):
    svc, co, rt = identity_svc
    _git(co, "checkout", "-q", "--detach", "HEAD")
    v = svc.controller_identity_live()
    assert not v["ok"] and "detached" in v["reason"]


def test_identity_rejects_wrong_origin(tmp_path, monkeypatch):
    co = _make_checkout(tmp_path, origin="https://github.com/evil/other.git")
    monkeypatch.setattr(selfupdate, "repo_root", lambda: co)
    monkeypatch.setattr(lhpc, "__file__", str(co / "lhpc" / "__init__.py"))
    svc = ControllerService(system=RealSystem(), paths=Paths(runtime_root=tmp_path))
    v = svc.controller_identity_live()
    assert not v["ok"] and "origin" in v["reason"]


# --------------------------------------------------------------------------- cache hardening (B4)

def _cache_path(rt: Path) -> Path:
    (rt / "state").mkdir(exist_ok=True)
    return rt / "state" / "selfupdate.json"


@pytest.mark.parametrize("payload", ["[]", '"x"', "123", "null", '{"local": ["bad"]}',
                                     '{"upstream": 5}', "not json at all"])
def test_cache_wrong_shape_returns_empty(tmp_path, payload):
    paths = Paths(runtime_root=tmp_path)
    _cache_path(tmp_path).write_text(payload)
    assert selfupdate.read_cache(paths) == {}
    # status_view still renders (grey unknown), never raises
    assert selfupdate.status_view(paths)["version"]


def test_cache_valid_envelope_parses(tmp_path):
    paths = Paths(runtime_root=tmp_path)
    _cache_path(tmp_path).write_text(json.dumps(
        {"schema_version": 1, "local": {"head": "abc"}, "upstream": {"ok": True},
         "identity": {"ok": True, "reason": "", "checked_at": 1}, "checked_at": 2}))
    assert selfupdate.read_cache(paths)["local"] == {"head": "abc"}


def test_cache_oversized_rejected(tmp_path):
    paths = Paths(runtime_root=tmp_path)
    _cache_path(tmp_path).write_text('{"local":{}}' + " " * (65 * 1024))   # valid prefix + padding
    assert selfupdate.read_cache(paths) == {}


def test_cache_fifo_does_not_block(tmp_path):
    import signal
    paths = Paths(runtime_root=tmp_path)
    p = _cache_path(tmp_path)
    os.mkfifo(p)

    def _timeout(*a):
        raise AssertionError("read_cache blocked on a FIFO")

    old = signal.signal(signal.SIGALRM, _timeout)
    signal.alarm(5)
    try:
        assert selfupdate.read_cache(paths) == {}
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old)


def test_cache_directory_rejected(tmp_path):
    paths = Paths(runtime_root=tmp_path)
    _cache_path(tmp_path).mkdir()
    assert selfupdate.read_cache(paths) == {}


def test_cache_symlink_rejected(tmp_path):
    paths = Paths(runtime_root=tmp_path)
    target = tmp_path / "elsewhere.json"
    target.write_text('{"local":{"head":"zzz"}}')
    _cache_path(tmp_path).symlink_to(target)
    assert selfupdate.read_cache(paths) == {}


# --------------------------------------------------------------------------- single envelope (B4)

def test_refresh_writes_full_envelope_atomically(tmp_path, monkeypatch):
    paths = Paths(runtime_root=tmp_path)
    monkeypatch.setattr(selfupdate, "local_state", lambda s: {"head": "h", "head_short": "h"})
    monkeypatch.setattr(selfupdate, "check_upstream", lambda s, b="": {"ok": True})
    ident = {"ok": True, "reason": "identity ok", "checked_at": 5}
    selfupdate.refresh_cache(FakeSystem(cmdlines_data={}).system, paths, identity=ident, now=9)
    raw = json.loads(_cache_path(tmp_path).read_text())
    assert raw["schema_version"] == 1 and raw["identity"] == ident and raw["checked_at"] == 9
    # a subsequent refresh with a NEW identity keeps it complete (never a dropped field)
    ident2 = {"ok": False, "reason": "x", "checked_at": 6}
    selfupdate.refresh_cache(FakeSystem(cmdlines_data={}).system, paths, identity=ident2, now=10)
    assert json.loads(_cache_path(tmp_path).read_text())["identity"] == ident2


def test_status_view_identity_is_cached_shape(tmp_path):
    paths = Paths(runtime_root=tmp_path)
    _cache_path(tmp_path).write_text(json.dumps(
        {"schema_version": 1, "local": {}, "upstream": {},
         "identity": {"ok": False, "status": "unsafe", "reason": "bad", "checked_at": 3},
         "checked_at": 4}))
    v = selfupdate.status_view(paths)["identity"]
    assert v == {"ok": False, "status": "unsafe", "reason": "bad", "checked_at": 3}


def test_status_view_identity_not_applicable_shape(tmp_path):
    paths = Paths(runtime_root=tmp_path)
    _cache_path(tmp_path).write_text(json.dumps(
        {"schema_version": 1, "local": {}, "upstream": {},
         "identity": {"ok": False, "status": "not_applicable", "reason": "not self-hosted: x",
                      "checked_at": 3}, "checked_at": 4}))
    v = selfupdate.status_view(paths)["identity"]
    assert v["status"] == "not_applicable" and v["ok"] is False


def test_status_view_legacy_identity_without_status(tmp_path):
    """A legacy cache (identity has no `status`) is mapped from `ok` (True->ok, False->unsafe)."""
    paths = Paths(runtime_root=tmp_path)
    _cache_path(tmp_path).write_text(json.dumps(
        {"local": {}, "upstream": {}, "identity": {"ok": True, "reason": "", "checked_at": 3}}))
    assert selfupdate.status_view(paths)["identity"]["status"] == "ok"


def test_legacy_envelope_reads_unchecked(tmp_path):
    paths = Paths(runtime_root=tmp_path)
    _cache_path(tmp_path).write_text(json.dumps({"local": {}, "upstream": {}, "checked_at": 1}))
    assert selfupdate.status_view(paths)["identity"] is None      # no identity -> unchecked


# --------------------------------------------------------------------------- cached-only GET (B4/inv.5)

def test_controller_status_makes_no_live_calls(tmp_path, monkeypatch):
    svc = ControllerService(system=FakeSystem(cmdlines_data={}).system,
                            paths=Paths(runtime_root=tmp_path))

    def _boom(*a, **k):
        raise AssertionError("a live git/network/identity call ran during a cached GET")

    monkeypatch.setattr(selfupdate, "local_state", _boom)
    monkeypatch.setattr(selfupdate, "check_upstream", _boom)
    monkeypatch.setattr(svc, "controller_identity_live", _boom)
    # both the self-update status and the controller row are cached-only
    svc.self_update_status()
    cs = svc.controller_status()
    assert cs is not None and cs["id"] == svc.controller().id


# --------------------------------------------------------------------------- controller-runtime lock (B6)

def test_apply_refused_while_web_shared_lock_held(tmp_path):
    paths = Paths(runtime_root=tmp_path)
    with selfupdate.controller_runtime_lock(paths, exclusive=False):     # web serving
        with pytest.raises(selfupdate.ControllerRuntimeBusy):
            with selfupdate.controller_runtime_lock(paths, exclusive=True):
                pass


def test_web_shared_fails_closed_while_apply_exclusive_held(tmp_path):
    paths = Paths(runtime_root=tmp_path)
    with selfupdate.controller_runtime_lock(paths, exclusive=True):      # apply in progress
        with pytest.raises(selfupdate.ControllerRuntimeBusy):
            with selfupdate.controller_runtime_lock(paths, exclusive=False):
                pass


def test_shared_lock_released_after_exit(tmp_path):
    """The shared lock is released on exit (incl. the finally path) so a later apply works."""
    paths = Paths(runtime_root=tmp_path)
    try:
        with selfupdate.controller_runtime_lock(paths, exclusive=False):
            raise RuntimeError("startup failure")
    except RuntimeError:
        pass
    with selfupdate.controller_runtime_lock(paths, exclusive=True):      # now free
        pass


def test_self_update_apply_refuses_when_web_running(identity_svc):
    """With a VALID controller identity (so the earlier gate passes), an apply while the web
    server holds the SHARED controller-runtime lock refuses with the stop-web message."""
    svc, _co, _rt = identity_svc
    assert svc.controller_identity_live()["ok"]
    with selfupdate.controller_runtime_lock(svc._paths, exclusive=False):
        res = svc.self_update_apply()
    assert not res.ok and res.data.get("web_running")
    assert "lhpc-web.service is running" in res.summary


def test_self_update_apply_blocked_by_unsafe_identity(identity_svc):
    """A genuinely UNSAFE self-hosted checkout (here: group/other-writable) blocks apply
    before any lock/mutation."""
    import os
    svc, co, _rt = identity_svc
    os.chmod(co, 0o770)                                   # tamper: group-writable checkout
    assert svc.controller_identity_live()["status"] == "unsafe"
    res = svc.self_update_apply()
    assert not res.ok and res.data.get("identity_unsafe")


def test_self_update_apply_not_blocked_when_not_self_hosted(tmp_path, monkeypatch):
    """A NOT-self-hosted deployment (no in-root checkout) must NOT be blocked by the identity
    gate — self-update proceeds via the normal repo_root() mechanism."""
    svc = ControllerService(system=RealSystem(), paths=Paths(runtime_root=tmp_path))
    assert svc.controller_identity_live()["status"] == "not_applicable"
    # No git repo here, so apply fails LATER for a benign reason — but NOT identity_unsafe.
    res = svc.self_update_apply()
    assert not res.data.get("identity_unsafe")


# --------------------------------------------------------------------------- audit regressions

def test_whitespace_only_controller_field_rejected():
    """Audit B: a non-empty-but-whitespace field is not a valid value."""
    with pytest.raises(ManifestError):
        parse_controller(_ctrl(remote="   "))


def test_canon_git_url_empty_for_degenerate():
    """Audit A: degenerate remotes canonicalize to '' (the caller must reject empties)."""
    assert _canon_git_url(".git") == ""
    assert _canon_git_url("") == ""
    assert _canon_git_url("/") == ""
    assert _canon_git_url("https://") == ""


def test_canon_git_url_preserves_path_case():
    """Audit C: path case matters on case-sensitive hosts (host is still folded)."""
    assert _canon_git_url("https://h/Foo/Bar") != _canon_git_url("https://h/foo/bar")
    assert _canon_git_url("https://GitHub.com/x/y") == _canon_git_url("https://github.com/x/y")


def test_identity_rejects_degenerate_remote(tmp_path, monkeypatch):
    """Audit A: a controller whose remote canonicalizes to '' must NOT match a checkout with
    no origin (both would otherwise be '')."""
    co = _make_checkout(tmp_path)
    _git(co, "remote", "remove", "origin")               # checkout has NO origin
    monkeypatch.setattr(selfupdate, "repo_root", lambda: co)
    monkeypatch.setattr(lhpc, "__file__", str(co / "lhpc" / "__init__.py"))
    svc = ControllerService(system=RealSystem(), paths=Paths(runtime_root=tmp_path))
    monkeypatch.setattr(svc, "controller",
                        lambda: ControllerSpec("loraham-pi-control", "X",
                                               "src/loraham-pi-control", "main", ".git"))
    v = svc.controller_identity_live()
    assert not v["ok"] and "origin" in v["reason"]


def test_read_cache_escaping_symlink_returns_empty(tmp_path):
    """Audit 1: a cache marker symlinked OUTSIDE the runtime root must return {} (paths.under
    raises PathContainmentError) — never propagate and 500 the page."""
    paths = Paths(runtime_root=tmp_path)
    (tmp_path / "state").mkdir()
    outside = tmp_path.parent / (tmp_path.name + "_outside.json")
    outside.write_text('{"local":{"head":"leak"}}')
    (tmp_path / "state" / "selfupdate.json").symlink_to(outside)
    assert selfupdate.read_cache(paths) == {}             # no raise
    assert selfupdate.status_view(paths)["version"]       # page still renders


def test_write_cache_swallows_containment_error(tmp_path, monkeypatch):
    """Audit 3: write_cache is best-effort — a containment/serialization error never escapes."""
    paths = Paths(runtime_root=tmp_path)
    from lhpc.core.paths import PathContainmentError

    def _boom(*a, **k):
        raise PathContainmentError("escapes")

    monkeypatch.setattr(selfupdate.runtime_fs, "write_marker", _boom)
    selfupdate.write_cache(paths, {"x": 1})               # must not raise


def test_apply_refresh_preserves_identity(tmp_path, monkeypatch):
    """Audit 2: refresh_cache(identity=None) carries the prior identity forward instead of
    dropping it (the case apply_update hits)."""
    paths = Paths(runtime_root=tmp_path)
    (tmp_path / "state").mkdir()
    monkeypatch.setattr(selfupdate, "local_state", lambda s: {"head": "h"})
    monkeypatch.setattr(selfupdate, "check_upstream", lambda s, b="": {"ok": True})
    ident = {"ok": True, "reason": "identity ok", "checked_at": 5}
    selfupdate.refresh_cache(FakeSystem(cmdlines_data={}).system, paths, identity=ident, now=9)
    # a later refresh WITHOUT an identity (apply_update's call) must keep it
    selfupdate.refresh_cache(FakeSystem(cmdlines_data={}).system, paths, identity=None, now=10)
    assert json.loads(_cache_path(tmp_path).read_text())["identity"] == ident


# =========================================================================== P2 hardening

# --------- P2-1: cache is field-level schema-safe; status_view never raises on a GET -------

@pytest.mark.parametrize("payload", [
    {"local": {"head": 1}, "upstream": {}},
    {"local": {"head_short": []}},
    {"local": {"branch": {}}},
    {"local": {"version": 3}},
    {"upstream": {"upstream_head": 1}},
    {"upstream": {"ok": "true"}},
    {"upstream": {"deps_changed": 1}},
    {"checked_at": True},                       # bool must NOT count as an int timestamp
    {"schema_version": 2, "local": {}},         # unknown/future version
    {"schema_version": "1"},                    # wrong-typed version
    {"identity": {"ok": 1}},                    # ok must be a real bool
    {"identity": {"ok": True, "reason": []}},   # reason must be a string
    {"identity": {"ok": True, "checked_at": True}},
])
def test_malformed_cache_fields_rejected_and_render_safe(tmp_path, payload):
    paths = Paths(runtime_root=tmp_path)
    _cache_path(tmp_path).write_text(json.dumps(payload))
    assert selfupdate.read_cache(paths) == {}          # invalid nested data -> {}
    v = selfupdate.status_view(paths)                  # MUST NOT raise on a GET
    assert v["version"] and v["ver_color"] == "grey" and v["head"] == ""


def test_valid_current_envelope_still_parses(tmp_path):
    paths = Paths(runtime_root=tmp_path)
    _cache_path(tmp_path).write_text(json.dumps(
        {"schema_version": 1, "local": {"head": "abcdef012", "head_short": "abcdef012",
         "branch": "main", "dirty": False}, "upstream": {"ok": True, "upstream_head": "abcdef012",
         "upstream_version": "0.1.2", "deps_changed": False},
         "identity": {"ok": True, "reason": "identity ok", "checked_at": 3}, "checked_at": 9}))
    assert selfupdate.read_cache(paths)["local"]["head"] == "abcdef012"
    assert selfupdate.status_view(paths)["branch"] == "main"


def test_legacy_envelope_without_schema_version_accepted(tmp_path):
    paths = Paths(runtime_root=tmp_path)
    _cache_path(tmp_path).write_text(json.dumps({"local": {"head": "x"}, "upstream": {}, "checked_at": 1}))
    assert selfupdate.read_cache(paths) != {}           # legacy is readable
    assert selfupdate.status_view(paths)["identity"] is None   # rendered as unchecked


def test_status_view_defends_even_if_reader_regressed(tmp_path, monkeypatch):
    """Belt-and-suspenders: even if read_cache let malformed data through, status_view
    coerces every field so a GET can never raise."""
    monkeypatch.setattr(selfupdate, "read_cache",
                        lambda p: {"local": {"head": 1, "head_short": [], "branch": {}},
                                   "upstream": {"ok": "yes", "upstream_head": 5},
                                   "checked_at": "nope"})
    v = selfupdate.status_view(Paths(runtime_root=tmp_path))
    assert v["head"] == "" and v["head_short"] == "" and v["branch"] == "" and v["checked_at"] == 0


# --------- P2-2: load_manifest validates a present [controller] table --------------------

_GOOD_STACK = ('[[stack]]\nid="s"\nmain="c"\n'
               '[[stack.component]]\nid="c"\nname="c"\nkind="service"\n')


def _manifest_file(tmp_path, controller_toml: str) -> Path:
    p = tmp_path / "m.toml"
    p.write_text(_GOOD_STACK + controller_toml)
    return p


@pytest.mark.parametrize("ctl", [
    '[controller]\nid="x"\ndisplay_name="X"\nsource_path="src/loraham-pi-control"\nbranch="main"\nremote="r"\nEXTRA="y"\n',
    '[controller]\nid="x"\ndisplay_name="X"\nsource_path="src/loraham-pi-control"\nbranch="main"\nremote="r"\n[controller.nested]\nk="v"\n',
    '[controller]\nid="x"\ndisplay_name="X"\nsource_path="src/other"\nbranch="main"\nremote="r"\n',
    '[controller]\nid="x"\ndisplay_name="X"\nsource_path="src/loraham-pi-control"\nbranch="dev"\nremote="r"\n',
    '[controller]\nid="s"\ndisplay_name="X"\nsource_path="src/loraham-pi-control"\nbranch="main"\nremote="r"\n',  # id collision
])
def test_load_manifest_rejects_invalid_controller(tmp_path, ctl):
    with pytest.raises(ManifestError):
        mf.load_manifest(_manifest_file(tmp_path, ctl))


def test_load_manifest_absent_controller_unchanged(tmp_path):
    p = _manifest_file(tmp_path, "")
    stacks = mf.load_manifest(p)                         # no controller -> normal behavior
    assert len(stacks) == 1 and mf.load_controller(p) is None


# --------- P2-3: cached-only controller row is the FIRST /stacks entry (not the dashboard) --

_NO_CONTROLLER_MANIFEST = _GOOD_STACK           # a stack, no [controller] table


def _app(tmp_path, manifest=None):
    from lhpc.adapters.web.app import create_app

    def factory():
        return ControllerService(manifest_path=manifest,
                                 system=FakeSystem(cmdlines_data={}).system,
                                 paths=Paths(runtime_root=tmp_path))

    return create_app(service_factory=factory).test_client()


def _seed_available_cache(tmp_path):
    """Write a cached self-update envelope that renders the controller as an available git
    checkout — mirrors what the startup refresh writes, so status_view needs no live probe."""
    from lhpc.core.paths import Paths
    (tmp_path / "state").mkdir(parents=True, exist_ok=True)
    selfupdate.write_cache(Paths(runtime_root=tmp_path),
                           {"local": {"is_git": True, "head": "a" * 40, "head_short": "aaaaaaaaa",
                                      "branch": "main"}, "upstream": {}, "checked_at": 1})


def test_stacks_renders_controller_row_first_with_embedded_update(tmp_path):
    _seed_available_cache(tmp_path)
    body = _app(tmp_path).get("/stacks").get_data(as_text=True)   # packaged manifest HAS a controller
    assert 'id="controller-row"' in body and "LoRaHAM Pi Control" in body
    assert "/self-update/check" in body and "Self-Update" not in body   # embedded Update UI, renamed
    # it is the FIRST entry — before the first managed stack's log link
    assert body.index('id="controller-row"') < body.index('logslink')


def test_stacks_omits_controller_row_when_absent(tmp_path):
    p = tmp_path / "m.toml"
    p.write_text(_NO_CONTROLLER_MANIFEST)
    resp = _app(tmp_path, manifest=p).get("/stacks")
    assert resp.status_code == 200 and 'id="controller-row"' not in resp.get_data(as_text=True)


def test_stacks_controller_row_is_cached_only(tmp_path, monkeypatch):
    """The /stacks GET renders the controller row from cached data even when EVERY live
    controller/self-update function would fail — no git/network/live-identity/source-tree probe
    on GET. `repo_root` is included: status_view must NOT probe the live checkout / .git."""
    def _boom(*a, **k):
        raise AssertionError("a live git/network/identity/source-tree call ran during a cached GET")

    monkeypatch.setattr(selfupdate, "repo_root", _boom)          # <- the Issue-A regression guard
    monkeypatch.setattr(selfupdate, "local_state", _boom)
    monkeypatch.setattr(selfupdate, "check_upstream", _boom)
    monkeypatch.setattr(ControllerService, "controller_identity_live", _boom)
    resp = _app(tmp_path).get("/stacks")
    assert resp.status_code == 200 and 'id="controller-row"' in resp.get_data(as_text=True)


def test_all_get_pages_and_footer_are_cached_only(tmp_path, monkeypatch):
    """Every GET route AND the footer context processor must render with no live controller
    probe: repo_root / local_state / check_upstream / controller_identity_live all raise."""
    def _boom(*a, **k):
        raise AssertionError("live controller probe on a GET")

    monkeypatch.setattr(selfupdate, "repo_root", _boom)
    monkeypatch.setattr(selfupdate, "local_state", _boom)
    monkeypatch.setattr(selfupdate, "check_upstream", _boom)
    monkeypatch.setattr(ControllerService, "controller_identity_live", _boom)
    client = _app(tmp_path)
    # The footer (context processor -> self_update_status -> status_view) renders on all of these.
    for path in ("/", "/stacks", "/stacks/daemon", "/logs/loraham-daemon", "/healthz"):
        resp = client.get(path)
        assert resp.status_code in (200, 404), (path, resp.status_code)
        if resp.status_code == 200 and path != "/healthz":
            assert "LoRaHAM Pi Control" in resp.get_data(as_text=True)   # footer rendered


def test_dashboard_has_no_controller_card(tmp_path):
    """The controller lives on /stacks (with its embedded Update UI) — NOT on the dashboard."""
    assert "controller-row" not in _app(tmp_path).get("/").get_data(as_text=True)


def test_stacks_controller_row_not_falsely_running(tmp_path):
    """The controller row must not claim a stack run-state (no running badge)."""
    body = _app(tmp_path).get("/stacks").get_data(as_text=True)
    i = body.index('id="controller-row"')
    assert "badge-running" not in body[i:body.index("</details>", i)]


# --------- P2-4: deps-sync uses the deployment interpreter + checkout, shell-quoted -------

def test_controller_deps_sync_cmd_is_deployment_specific(tmp_path):
    svc = ControllerService(system=FakeSystem(cmdlines_data={}).system,
                            paths=Paths(runtime_root=Path("/home/x/loraham-pi-control")))
    cmd = svc._controller_deps_sync_cmd()
    assert cmd == ("/home/x/loraham-pi-control/venv/lhpc/bin/python -m pip install -e "
                   "/home/x/loraham-pi-control/src/loraham-pi-control")
    assert "pip install -e ." not in cmd


def test_controller_deps_sync_cmd_shell_quoted(tmp_path):
    svc = ControllerService(system=FakeSystem(cmdlines_data={}).system,
                            paths=Paths(runtime_root=Path("/home/a b/root")))
    cmd = svc._controller_deps_sync_cmd()
    assert "'/home/a b/root/venv/lhpc/bin/python'" in cmd
    assert "'/home/a b/root/src/loraham-pi-control'" in cmd


def test_deps_sync_cmd_empty_without_controller(tmp_path, monkeypatch):
    svc = ControllerService(system=FakeSystem(cmdlines_data={}).system,
                            paths=Paths(runtime_root=tmp_path))
    monkeypatch.setattr(svc, "controller", lambda: None)
    assert svc._controller_deps_sync_cmd() == ""


def test_restart_instructions_use_deployment_cmd():
    dep = "/r/venv/lhpc/bin/python -m pip install -e /r/src/loraham-pi-control"
    instr = selfupdate.restart_instructions(deps_changed=True, deps_sync_cmd=dep)
    assert any(dep in c for c in instr["commands"])
    # dev fallback when no controller command is supplied
    fb = selfupdate.restart_instructions(deps_changed=True, deps_sync_cmd="")
    assert any(c.startswith("pip install -e .") for c in fb["commands"])
