"""No published binary built for this LHPC (`none_compatible`) in the console: the confirm page
offers the self-update first and the source channel second, and never a way over the pin check
(`--accept-pin-mismatch` is removed, decision D1); the stack page shows a job's typed refusal and,
from a receipt an older LHPC wrote, an acceptance still in force."""

import pytest
from htmlq import parse

from lhpc.core import binary_install as bi
from lhpc.core import binary_receipt as brx
from lhpc.core import jobresult
from lhpc.core.services import ControllerService

A, B = "a" * 40, "b" * 40


@pytest.fixture
def published(monkeypatch):
    """`published(stack, cid, commit)`: the index serves `stack` without `cid` (U9: a covered component
    it does not name, so no published binary covers this lhpc; `commit` is unused).
    A download fails the test, so these console plans are shown to stop before one."""
    monkeypatch.setattr(ControllerService, "binary_target", lambda self: "aarch64-trixie")

    def _set(stack, cid, commit):
        def _entry(idx, sid):
            pins = ControllerService()._binary_pins(sid)
            return bi.IndexEntry(stack=sid, filename=f"{sid}-{'a' * 64}.tar.zst",
                                 url="https://example.invalid/x", sha256="a" * 64, size=10,
                                 components={k: v for k, v in pins.items() if k != cid}, runtime_deps=(),
                                 target="aarch64-trixie",
                                 provenance={"smoke": {"mode": "mandatory", "result": "passed"}})
        monkeypatch.setattr(bi, "fetch_index", lambda url: {"schema": 3, "stacks": {}})
        monkeypatch.setattr(bi, "index_entries", lambda idx, sid: [_entry(idx, sid)])
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


def _source_form(doc):
    """The "build from source instead" way: the form that offers a source channel to pick."""
    forms = [f for f in doc.find("form") if doc.within(f).find("select", name="source")]
    assert len(forms) == 1, "exactly one source form"
    return forms[0]


# ---- the confirm page: self-update first, source second, nothing else ---------------------------

@pytest.mark.parametrize("op", ["install", "update"])
@pytest.mark.parametrize("stack,cid", [("daemon", None), ("meshcom", "meshcom-qemu")])
def test_confirm_none_compatible_offers_self_update_then_source_and_no_override(
        web, csrf, published, update_available, op, stack, cid):
    published(stack, cid or _daemon_cid(), A)
    update_available(True)
    doc = _post(web(), csrf, op=op, target=stack, source="binary")
    assert "cannot proceed" in doc.text
    sel = doc.by_id("pin-selfupdate")
    assert sel is not None and doc.within(sel).find("a", href="/self-update/apply")
    assert doc.index(sel) < doc.index(_source_form(doc))
    assert doc.by_id("pin-override") is None and not doc.find("input", name="accept_pin_mismatch")
    assert not doc.find("input", name="consent")


@pytest.mark.parametrize("error", ["built for another target", "did not pass a mandatory smoke test",
                                   "no binary is published for 'daemon'"])
def test_other_refusals_render_no_self_update_offer(web, csrf, monkeypatch, published, error):
    published("daemon", _daemon_cid(), A)
    monkeypatch.setattr(bi, "check_target", lambda e, t: (_ for _ in ()).throw(
        bi.BinaryInstallError(error)))
    doc = _post(web(), csrf, op="install", target="daemon", source="binary")
    assert doc.by_id("pin-selfupdate") is None


@pytest.mark.parametrize("available", [True, False])
def test_self_update_offer_only_when_an_update_is_available(web, csrf, published, update_available,
                                                            available):
    published("daemon", _daemon_cid(), A)
    update_available(available)
    doc = _post(web(), csrf, op="install", target="daemon", source="binary")
    assert (doc.by_id("pin-selfupdate") is not None) == available


@pytest.mark.parametrize("op", ["install", "update"])
def test_a_posted_acceptance_from_an_old_page_changes_nothing(web, csrf, monkeypatch, published, op):
    """A form an older console rendered still posts `accept_pin_mismatch` + `consent`: the console
    ignores both. The install job is spawned as any binary install (its own refusal follows);
    the inline update refuses as before."""
    published("daemon", _daemon_cid(), A)
    spawned = []
    monkeypatch.setattr(ControllerService, "spawn_web_job",
                        lambda self, op, target, **kw: spawned.append(kw) or (None, "blocked", "x"))
    doc = _post(web(), csrf, op=op, target="daemon", source="binary", confirmed="yes",
                accept_pin_mismatch="yes", consent="f" * 64)
    assert spawned == ([{"source": "binary"}] if op == "install" else [])
    assert doc.by_id("pin-override") is None and not doc.find("input", name="consent")


def test_csrf_is_enforced(web, published):
    published("daemon", _daemon_cid(), A)
    r = web().post("/action", data={"op": "install", "target": "daemon", "source": "binary",
                                    "accept_pin_mismatch": "yes", "consent": "f" * 64})
    assert r.status_code == 400


# ---- the stack page: a job's typed refusal, an override in force ---------------------------------

def _failed_install_job(svc_paths, stack, pairs):
    log, aid = f"install-{stack}.log", "d" * 32
    assert jobresult.reserve(svc_paths, log, aid, "install", stack, stack, [])
    assert jobresult.terminalize(svc_paths, log, aid, "failed", detail="install failed",
                                 refusal={"pin_mismatch": pairs})


def _stack_page(c, stack):
    return parse(c.get(f"/stacks?open={stack}").get_data(as_text=True))


@pytest.mark.parametrize("stack,cid", [("daemon", None), ("meshcom", "meshcom-qemu")])
def test_stack_page_shows_the_jobs_refusal_without_fetching(web, monkeypatch, tmp_path,
                                                           update_available, stack, cid):
    """The job result holds the lagging pairs; the GET does not fetch the index (a fetch fails the
    test); the banner leads with Update LHPC first and offers Install again, never an override."""
    from lhpc.core.paths import Paths
    update_available(True)
    monkeypatch.setattr(bi, "fetch_index", lambda url: pytest.fail("a GET must not fetch"))
    _failed_install_job(Paths(runtime_root=tmp_path), stack, {cid or _daemon_cid(): [A, B]})
    doc = _stack_page(web(), stack)
    box = doc.by_id(f"pin-refusal-{stack}")
    assert box is not None
    scope = doc.within(box)
    assert "Update LHPC first" in scope.text and scope.find("a", href="/self-update/apply")
    assert scope.find("input", name="op", value="install")
    assert not scope.find("input", name="consent") and "--accept-pin-mismatch" not in scope.text


def test_stack_page_names_the_uncovered_case_when_no_commit_lags(web, tmp_path, update_available):
    """U9's refusal (no published binary covers this LHPC): the banner still leads with Update LHPC
    first and says what is missing."""
    from lhpc.core.paths import Paths
    update_available(True)
    _failed_install_job(Paths(runtime_root=tmp_path), "meshtastic", {})
    box = _stack_page(web(), "meshtastic").by_id("pin-refusal-meshtastic")
    assert box is not None
    scope = _stack_page(web(), "meshtastic").within(box)
    assert "Update LHPC first" in scope.text and "covers this LHPC's components" in scope.text
    assert "other commits" not in scope.text


def test_a_legacy_override_shows_no_pill_and_no_warning(web, tmp_path, binary_receipt, monkeypatch,
                                                        update_available):
    # U10a (D6): an override an older lhpc recorded in the receipt is ignored by the console.
    import json
    from lhpc.core.paths import Paths
    from lhpc.core.probes.backends import FakeSystem
    update_available(True)
    monkeypatch.setattr(ControllerService, "binary_target", lambda self: "aarch64-trixie")
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    cid = _daemon_cid()
    pin = svc._binary_pins("daemon")[cid]
    binary_receipt(svc, commits={**svc._binary_pins("daemon"), cid: A})
    p = brx.receipt_path(svc._paths, "daemon")
    d = json.loads(p.read_text())
    d["override"] = {"at": 1, "lhpc_version": "0.11.3", "mismatch": {cid: [A, pin]}}
    p.write_text(json.dumps(d))
    doc = _stack_page(web(), "daemon")
    assert doc.by_id("stack-install-daemon") is not None                 # the stack page itself
    assert doc.by_id("pin-override-daemon") is None and "over pin check" not in doc.text
    assert not [a for a in doc.find("a", href="/self-update/apply") if "pin check" in (a["title"] or "")]
