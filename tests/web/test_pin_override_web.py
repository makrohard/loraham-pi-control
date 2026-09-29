"""A3 in the console: the override is offered on a gate-1 pin refusal of an overridable stack
and not on the other refusals (E), bound to the refusal's consent token (§2b); MeshCom gets the hint and
the self-update first and no checkbox (B1 = X); the stack page shows a job's typed refusal and
an override still in force."""
import dataclasses
import re

import pytest
from htmlq import parse

from lhpc.core import binary_install as bi
from lhpc.core import binary_receipt as brx
from lhpc.core import jobresult
from lhpc.core.service_base import ActionResult
from lhpc.core.services import ControllerService

A, B = "a" * 40, "b" * 40


@pytest.fixture
def published(monkeypatch):
    """`published(stack, cid, commit)`: the index serves `stack` with `cid` built from `commit`.
    A download fails the test, so these console plans are shown to stop before one."""
    monkeypatch.setattr(ControllerService, "binary_target", lambda self: "aarch64-trixie")

    def _set(stack, cid, commit):
        def _entry(idx, sid):
            pins = ControllerService()._binary_pins(sid)
            return bi.IndexEntry(stack=sid, filename=f"{sid}-{'a' * 64}.tar.zst",
                                 url="https://example.invalid/x", sha256="a" * 64, size=10,
                                 components={**pins, cid: commit}, runtime_deps=(),
                                 target="aarch64-trixie",
                                 provenance={"smoke": {"mode": "mandatory", "result": "passed"}})
        monkeypatch.setattr(bi, "fetch_index", lambda url: {"schema": 2, "stacks": {}})
        monkeypatch.setattr(bi, "index_entry", _entry)
        monkeypatch.setattr(bi, "require_zstd", lambda: None)
        monkeypatch.setattr(bi, "download_artifact",
                            lambda *a, **k: pytest.fail("must not download"))
    return _set


@pytest.fixture
def update_available(monkeypatch):
    def _set(flag):
        monkeypatch.setattr(ControllerService, "self_update_status",
                            lambda self: {"version": "0.11.3", "head_short": "", "available": flag,
                                          "ver_color": "grey", "commit_color": "grey",
                                          "update_available": flag})
    return _set


def _daemon_cid():
    return next(iter(ControllerService()._binary_pins("daemon")))


def _post(c, csrf, **form):
    return parse(c.post("/action", data={"_csrf": csrf(c), **form}).get_data(as_text=True))


def _refusal_token(doc):
    form = doc.by_id("pin-override")
    return doc.within(form).field_default("consent") if form is not None else None


# ---- the confirm page: the three ways, E, the token --------------------------------------------

def test_pin_refusal_renders_the_override_form_with_the_lagging_list_and_checkbox(
        web, csrf, published, update_available):
    published("daemon", _daemon_cid(), A)
    update_available(True)
    c = web()
    doc = _post(c, csrf, op="install", target="daemon", source="binary")
    form = doc.by_id("pin-override")
    assert form is not None
    scope = doc.within(form)
    assert scope.find("input", type="checkbox", name="accept_pin_mismatch")
    assert re.fullmatch(r"[0-9a-f]{64}", scope.field_default("consent"))
    assert _daemon_cid() in scope.text and A[:9] in scope.text
    # way (a) self-update FIRST, then (b) the source form, then (c) the override
    sel = doc.by_id("pin-selfupdate")
    src = next(f for f in doc.find("form") if "Build from source" in doc.within(f).text)
    assert doc.index(sel) < doc.index(src) < doc.index(form)


@pytest.mark.parametrize("error", ["built for another target", "did not pass a mandatory smoke test",
                                   "no binary is published for 'daemon'"])
def test_other_refusals_render_no_override_form(web, csrf, monkeypatch, published, error):
    published("daemon", _daemon_cid(), A)
    monkeypatch.setattr(bi, "check_target", lambda e, t: (_ for _ in ()).throw(
        bi.BinaryInstallError(error)))
    doc = _post(web(), csrf, op="install", target="daemon", source="binary")
    assert doc.by_id("pin-override") is None and doc.by_id("pin-selfupdate") is None
    assert not doc.find("input", name="accept_pin_mismatch")


@pytest.mark.parametrize("available", [True, False])
def test_self_update_offer_only_when_an_update_is_available(web, csrf, published, update_available,
                                                            available):
    published("daemon", _daemon_cid(), A)
    update_available(available)
    doc = _post(web(), csrf, op="install", target="daemon", source="binary")
    assert (doc.by_id("pin-selfupdate") is not None) == available
    assert doc.by_id("pin-override") is not None


def test_post_with_the_checkbox_and_token_runs_the_plan_with_the_override(web, csrf, published):
    published("daemon", _daemon_cid(), A)
    c = web()
    token = _refusal_token(_post(c, csrf, op="install", target="daemon", source="binary"))
    doc = _post(c, csrf, op="install", target="daemon", source="binary",
                accept_pin_mismatch="yes", consent=token)
    assert "Binary install plan for 'daemon'" in doc.text and "OVER THE PIN CHECK" in doc.text
    # the NORMAL confirm follows, carrying the same consent on to the apply
    assert doc.field_default("confirmed") == "yes"
    assert doc.field_default("consent") == token and doc.field_default("accept_pin_mismatch") == "yes"
    assert doc.by_id("pin-override") is None


def test_post_without_the_checkbox_is_still_refused(web, csrf, published):
    published("daemon", _daemon_cid(), A)
    c = web()
    token = _refusal_token(_post(c, csrf, op="install", target="daemon", source="binary"))
    doc = _post(c, csrf, op="install", target="daemon", source="binary", consent=token)
    assert doc.by_id("pin-override") is not None and "cannot proceed" in doc.text


def test_csrf_is_enforced(web, published):
    published("daemon", _daemon_cid(), A)
    r = web().post("/action", data={"op": "install", "target": "daemon", "source": "binary",
                                    "accept_pin_mismatch": "yes", "consent": "f" * 64})
    assert r.status_code == 400


@pytest.mark.parametrize("consent", ["", "not-a-token", "f" * 64])
def test_a_post_without_or_with_a_wrong_token_is_not_honoured(web, csrf, published, consent):
    """E: no token (or a malformed one) is a plain plan — the refusal with the checkbox again; a
    token that is not this artifact's is `consent_stale` — the refusal with the way to review."""
    published("daemon", _daemon_cid(), A)
    doc = _post(web(), csrf, op="install", target="daemon", source="binary",
                accept_pin_mismatch="yes", consent=consent)
    assert "cannot proceed" in doc.text
    if consent == "f" * 64:
        assert "review the install again" in doc.text and doc.by_id("pin-override") is None
        assert doc.find("button", type="submit")
    else:
        assert doc.by_id("pin-override") is not None


def test_the_token_reaches_the_install_jobs_argv(web, csrf, monkeypatch, published):
    published("daemon", _daemon_cid(), A)
    c = web()
    token = _refusal_token(_post(c, csrf, op="install", target="daemon", source="binary"))
    seen = {}

    def _spawn(self, op, target, source="pinned", accept_pin_mismatch=""):
        seen.update(op=op, source=source, accept=accept_pin_mismatch)
        return None, "blocked", "stubbed"
    monkeypatch.setattr(ControllerService, "spawn_web_job", _spawn)
    c.post("/action", data={"_csrf": csrf(c), "op": "install", "target": "daemon",
                            "source": "binary", "confirmed": "yes", "accept_pin_mismatch": "yes",
                            "consent": token})
    assert seen == {"op": "install", "source": "binary", "accept": token}


def test_the_token_reaches_the_inline_update(web, csrf, monkeypatch, published):
    published("daemon", _daemon_cid(), A)
    seen = {}

    def _update(self, target="", apply=False, source="pinned", accept_pin_mismatch="", **kw):
        seen[apply] = accept_pin_mismatch
        return ActionResult(True, "updated", data={"changes": 1})
    monkeypatch.setattr(ControllerService, "update", _update)
    c = web()
    c.post("/action", data={"_csrf": csrf(c), "op": "update", "target": "daemon",
                            "source": "binary", "confirmed": "yes", "accept_pin_mismatch": "yes",
                            "consent": "e" * 64})
    assert seen[True] == "e" * 64


# ---- MeshCom (B1 = X): the hint and the self-update first, no checkbox, ever --------------------

@pytest.mark.parametrize("op", ["install", "update"])
def test_confirm_meshcom_pin_refusal_hint_and_selfupdate_no_checkbox(web, csrf, published,
                                                                    update_available, op):
    published("meshcom", "meshcom-qemu", A)
    update_available(True)
    c = web()
    doc = _post(c, csrf, op=op, target="meshcom", source="binary")
    assert "Update LHPC first" in doc.text
    sel = doc.by_id("pin-selfupdate")
    assert sel is not None and doc.within(sel).find("a", href="/self-update/apply")
    assert all(doc.index(sel) < doc.index(f) for f in doc.find("form")
               if "Build from source" in doc.within(f).text)
    assert doc.by_id("pin-override") is None and not doc.find("input", name="accept_pin_mismatch")
    # a posted acceptance is still refused, typed, and still offers no checkbox
    doc = _post(c, csrf, op=op, target="meshcom", source="binary", accept_pin_mismatch="yes",
                consent="a" * 64)
    assert "refused even with --accept-pin-mismatch" in doc.text
    assert doc.by_id("pin-override") is None and doc.by_id("pin-selfupdate") is not None


# ---- the stack page: a job's typed refusal, an override in force ---------------------------------

def _failed_install_job(svc_paths, stack, refused, pairs):
    log, aid = f"install-{stack}.log", "d" * 32
    assert jobresult.reserve(svc_paths, log, aid, "install", stack, stack, [])
    assert jobresult.terminalize(svc_paths, log, aid, "failed", detail="install failed",
                                 refusal={"pin_mismatch": pairs, "override_refused": refused})


def _stack_page(c, stack):
    return parse(c.get(f"/stacks?open={stack}").get_data(as_text=True))


def test_consent_stale_page_says_review_again_without_a_token(web, monkeypatch, tmp_path,
                                                              update_available):
    """The job result holds the NEW map but no token; the GET does not fetch the index (a fetch
    fails the test); the page offers an Install-again form and no token."""
    from lhpc.core.paths import Paths
    update_available(True)
    monkeypatch.setattr(bi, "fetch_index", lambda url: pytest.fail("a GET must not fetch"))
    _failed_install_job(Paths(runtime_root=tmp_path), "daemon", "consent_stale",
                        {_daemon_cid(): [B, A]})
    doc = _stack_page(web(), "daemon")
    box = doc.by_id("pin-refusal-daemon")
    assert box is not None
    scope = doc.within(box)
    assert "review the install again" in scope.text and B[:9] in scope.text
    assert scope.find("input", name="op", value="install")
    assert not scope.find("input", name="consent") and not scope.find("input", name="accept_pin_mismatch")


def test_stack_page_meshcom_refusal_hint_and_selfupdate_link(web, tmp_path, update_available):
    from lhpc.core.paths import Paths
    update_available(True)
    _failed_install_job(Paths(runtime_root=tmp_path), "meshcom", "clone_required",
                        {"meshcom-qemu": [A, B]})
    doc = _stack_page(web(), "meshcom")
    box = doc.by_id("pin-refusal-meshcom")
    assert box is not None
    scope = doc.within(box)
    assert "Update LHPC first" in scope.text and scope.find("a", href="/self-update/apply")
    assert not scope.find("form") and "--accept-pin-mismatch" not in scope.text


def test_the_pill_and_the_stack_page_warning_while_the_override_is_active(web, tmp_path,
                                                                          binary_receipt,
                                                                          monkeypatch,
                                                                          update_available):
    from lhpc.core.paths import Paths
    from lhpc.core.probes.backends import FakeSystem
    update_available(True)
    monkeypatch.setattr(ControllerService, "binary_target", lambda self: "aarch64-trixie")
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    cid = _daemon_cid()
    pin = svc._binary_pins("daemon")[cid]
    rec = binary_receipt(svc, commits={**svc._binary_pins("daemon"), cid: A})
    ov = {"at": 1, "lhpc_version": "0.11.3", "mismatch": {cid: [A, pin]}}
    assert brx.write_receipt(svc._paths, dataclasses.replace(rec, override=ov))
    c = web()
    doc = _stack_page(c, "daemon")
    warn = doc.by_id("pin-override-daemon")
    assert warn is not None and doc.within(warn).find("a", href="/self-update/apply")
    assert doc.find("span", class_="ver-yellow") and "over pin check" in doc.text
    # stale (a normal install wrote the receipt without the override): both are gone
    assert brx.write_receipt(svc._paths, dataclasses.replace(rec, override=None))
    doc = _stack_page(web(), "daemon")
    assert doc.by_id("pin-override-daemon") is None and "over pin check" not in doc.text
