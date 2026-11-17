"""U9 (plan v6, D9 + D10): the binary lane's one target is `newest_entry`, the newest index-v3 entry for this
box's target by `published_at`, proven or not. No commit or build-input equality: the entry must name every
component this LHPC's binary spec covers (an extra one is accepted), and it is offered only when it is newer
than the installed receipt's entry. Driven through the public install / update / source-check paths with only
the HTTP seam stubbed, so the real index parser and selection run."""
from __future__ import annotations

import json
import time

from lhpc.core import binary_install as bi, stackupdates
from lhpc.core.install import Installer
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import CommandResult, FakeSystem
from lhpc.core.services import ActionResult, ControllerService

TARGET = "aarch64-trixie"


def _svc(tmp_path, monkeypatch):
    monkeypatch.setattr(ControllerService, "binary_target", lambda self: TARGET)
    return ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))


def _entry(sid, sha, components, published, target=TARGET):
    filename = f"{sid}-{sha}.tar.zst"
    return {"filename": filename, "url": f"https://example.invalid/{filename}", "sha256": sha,
            "size": 10, "built_from": "b" * 40, "components": dict(components),
            "lhpc_commit": "c" * 40, "builder_commit": "d" * 40, "target": target,
            "os": "trixie", "container_digest": "debian@sha256:" + "e" * 64, "runtime_deps": [],
            "smoke": {"mode": "mandatory", "result": "passed"}, "extract_to": "runtime-root",
            "inputs": {}, "published_at": published}


def _serve(monkeypatch, sid, *entries):
    index = {"schema": 3, "stacks": {sid: list(entries)}}
    monkeypatch.setattr(bi, "_http_get", lambda url, max_bytes: json.dumps(index).encode())
    monkeypatch.setattr(bi, "require_zstd", lambda: None)


def _other(svc, sid, commit="f" * 40):
    """Every covered component at a commit this LHPC does not pin."""
    return {cid: commit for cid in svc._binary_pins(sid)}


def _checked(svc, cid="loraham-daemon"):
    """The public `lhpc source-check` verdict for one component (its detail line)."""
    lines = [d for d in svc.source_check("daemon").details if d.split(": ", 1)[0].strip() == cid]
    assert len(lines) == 1, lines
    return lines[0].split(": ", 1)[1]


def _is(status):
    return stackupdates.label(status)


def _names(plan, sha):
    return any(f"-{sha}.tar.zst" in d for d in plan.details)


def test_an_entry_built_from_other_commits_installs(tmp_path, monkeypatch):
    # Today: refused, "built from different commits than this lhpc pins". D9: the newest binary, unproven too.
    svc = _svc(tmp_path, monkeypatch)
    _serve(monkeypatch, "daemon", _entry("daemon", "1" * 64, _other(svc, "daemon"), "2026-11-10T03:51:59Z"))
    plan = svc.install("daemon", apply=False, source="binary")
    assert plan.ok, plan.summary
    assert _names(plan, "1" * 64)


def test_the_newest_entry_wins_over_an_older_one_at_the_pins(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch)
    _serve(monkeypatch, "daemon",
           _entry("daemon", "2" * 64, svc._binary_pins("daemon"), "2026-11-09T17:54:53Z"),
           _entry("daemon", "1" * 64, _other(svc, "daemon"), "2026-11-10T03:51:59Z"))
    plan = svc.install("daemon", apply=False, source="binary")
    assert plan.ok and _names(plan, "1" * 64) and not _names(plan, "2" * 64)


def test_an_entry_missing_a_covered_component_is_the_typed_refusal(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch)
    comps = _other(svc, "daemon")
    del comps["radiolib"]
    _serve(monkeypatch, "daemon", _entry("daemon", "1" * 64, comps, "2026-11-10T03:51:59Z"))
    res = svc.install("daemon", apply=False, source="binary")
    assert not res.ok
    assert "no published binary covers radiolib yet: install from source" in res.summary
    assert res.data["binary_refusal"] == "none_compatible"
    assert "lhpc install daemon --source pinned --yes" in res.next_commands


def test_an_entry_naming_an_extra_component_is_accepted(tmp_path, monkeypatch):
    # As today (control): coverage is a subset check, an extra component is another LHPC's wider spec.
    svc = _svc(tmp_path, monkeypatch)
    comps = {**svc._binary_pins("daemon"), "meshstick-tools": "a" * 40}
    _serve(monkeypatch, "daemon", _entry("daemon", "1" * 64, comps, "2026-11-10T03:51:59Z"))
    plan = svc.install("daemon", apply=False, source="binary")
    assert plan.ok and _names(plan, "1" * 64)


def test_equal_published_at_takes_the_first_listed_entry(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch)
    for first, second in (("3", "4"), ("4", "3")):
        _serve(monkeypatch, "daemon",
               _entry("daemon", first * 64, _other(svc, "daemon"), "2026-11-10T03:51:59Z"),
               _entry("daemon", second * 64, _other(svc, "daemon", "9" * 40), "2026-11-10T03:51:59Z"))
        plan = svc.install("daemon", apply=False, source="binary")
        assert plan.ok and _names(plan, first * 64) and not _names(plan, second * 64)


def test_another_targets_newer_entry_is_not_the_target(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch)
    _serve(monkeypatch, "daemon",
           _entry("daemon", "5" * 64, _other(svc, "daemon"), "2026-11-11T00:00:00Z", target="x86_64-trixie"),
           _entry("daemon", "1" * 64, _other(svc, "daemon"), "2026-11-10T03:51:59Z"))
    plan = svc.install("daemon", apply=False, source="binary")
    assert plan.ok and _names(plan, "1" * 64)


def _installed(svc, binary_receipt, published):
    return binary_receipt(svc, sha="1" * 64, commits=_other(svc, "daemon"),
                          provenance={"published_at": published})


def test_a_withdrawn_installed_binary_stays_and_an_older_one_is_not_offered(tmp_path, monkeypatch,
                                                                             binary_receipt):
    # D10: the bot withdrew the installed entry; only an older one (at the pins) is left.
    svc = _svc(tmp_path, monkeypatch)
    _installed(svc, binary_receipt, "2026-11-10T03:51:59Z")
    _serve(monkeypatch, "daemon", _entry("daemon", "2" * 64, svc._binary_pins("daemon"), "2026-11-09T17:54:53Z"))
    plan = svc.update("daemon", apply=False, source="binary")
    assert plan.ok, plan.summary
    assert plan.data["changes"] == 0 and f"daemon-{'1' * 64}.tar.zst" in plan.summary
    assert not any("download" in d for d in plan.details)
    applied = svc.update("daemon", apply=True, source="binary")
    assert applied.ok and applied.data["changes"] == 0
    assert svc.binary_receipt_state("daemon")[1].artifact_sha256 == "1" * 64
    assert _checked(svc) == _is(stackupdates.UP_TO_DATE)


def test_a_newer_entry_is_offered_and_read_as_an_update(tmp_path, monkeypatch, binary_receipt):
    svc = _svc(tmp_path, monkeypatch)
    _installed(svc, binary_receipt, "2026-11-09T17:54:53Z")
    _serve(monkeypatch, "daemon", _entry("daemon", "2" * 64, _other(svc, "daemon"), "2026-11-10T03:51:59Z"))
    plan = svc.update("daemon", apply=False, source="binary")
    assert plan.ok and plan.data["changes"] == 1 and _names(plan, "2" * 64)
    assert _checked(svc) == _is(stackupdates.BEHIND)


def test_an_unreadable_index_reads_could_not_check(tmp_path, monkeypatch, binary_receipt):
    svc = _svc(tmp_path, monkeypatch)
    _installed(svc, binary_receipt, "2026-11-09T17:54:53Z")

    def down(url, max_bytes):
        raise bi.BinaryInstallError("download failed: HTTP Error 503")
    monkeypatch.setattr(bi, "_http_get", down)
    assert _checked(svc) == _is(stackupdates.ERROR)


def test_the_meshcom_checkout_follows_the_entrys_commit_not_the_pin(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch)
    comps = {**_other(svc, "meshcom"), "meshcom-gps-relay": "f" * 40}
    _serve(monkeypatch, "meshcom", _entry("meshcom", "1" * 64, comps, "2026-11-10T03:51:59Z"))
    seen = []

    class _Failed:
        status, detail, provenance = "failed", "STOP-HERE", ""

    def adopt(self, comp, force=False, source="pinned", pinned_expected=None, **kw):
        seen.append((comp.id, pinned_expected[0] if pinned_expected else None))
        return _Failed()
    monkeypatch.setattr(Installer, "adopt_source", adopt)
    monkeypatch.setattr(bi, "download_artifact", lambda *a, **k: None)   # staged first, then the checkout
    monkeypatch.setattr(bi, "validate_and_extract", lambda *a, **k: [])
    res = svc.install("meshcom", apply=True, source="binary")
    assert not res.ok and "STOP-HERE" in res.summary
    assert seen == [("meshcom-qemu", "f" * 40)]


def test_a_receipt_without_published_at_compares_by_its_install_time(tmp_path, monkeypatch, binary_receipt):
    # A receipt an older lhpc wrote records no `published_at`: an entry published before the install
    # is not newer (never an older replacement), one published after it is.
    svc = _svc(tmp_path, monkeypatch)
    rec = binary_receipt(svc, sha="1" * 64, provenance={})
    _serve(monkeypatch, "daemon", _entry("daemon", "2" * 64, _other(svc, "daemon"), "2026-01-01T00:00:00Z"))
    assert svc.update("daemon", apply=False, source="binary").data["changes"] == 0
    later = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(rec.installed_at + 60))
    _serve(monkeypatch, "daemon", _entry("daemon", "2" * 64, _other(svc, "daemon"), later))
    assert svc.update("daemon", apply=False, source="binary").data["changes"] == 1


def test_a_clean_checkout_at_the_pin_is_moved_to_the_entrys_commit_under_the_locks(tmp_path, monkeypatch):
    # The clean checkout of ours at another commit is moved by the locked adoption to the binary's commit
    # (not the pin); no source update runs inside the install.
    from lhpc.core import source_registry
    from lhpc.core.install import DirtyReport
    svc = _svc(tmp_path, monkeypatch)
    comp = svc.stack("meshcom").component("meshcom-qemu")
    svc._paths.resolve_source(comp.source.path).mkdir(parents=True)
    _serve(monkeypatch, "meshcom", _entry("meshcom", "1" * 64, _other(svc, "meshcom"), "2026-11-10T03:51:59Z"))
    seen = []

    class _Rec:
        resolved_commit = comp.source.pin_commit

    class _Failed:
        status, detail, provenance = "failed", "STOP-HERE", ""

    def adopt(self, c, force=False, source="pinned", pinned_expected=None, locked=False):
        seen.append((c.id, force, locked, pinned_expected[0]))
        return _Failed()
    monkeypatch.setattr(source_registry, "verify_identity", lambda *a, **k: (_Rec(), ""))
    monkeypatch.setattr(Installer, "dirty_report", lambda self, d, path: DirtyReport())
    monkeypatch.setattr(Installer, "adopt_source", adopt)
    monkeypatch.setattr(ControllerService, "update", lambda self, *a, **k: (
        seen.append("update"), ActionResult(False, "UPDATE-CALLED"))[1])
    monkeypatch.setattr(bi, "download_artifact", lambda *a, **k: None)   # staged first, then the checkout
    monkeypatch.setattr(bi, "validate_and_extract", lambda *a, **k: [])
    res = svc.install("meshcom", apply=True, source="binary")
    assert not res.ok and "STOP-HERE" in res.summary, res.summary
    assert seen == [("meshcom-qemu", True, True, "f" * 40)]


def test_an_unsafe_receipt_reads_could_not_check(tmp_path, monkeypatch, binary_receipt):
    """U6: a binary-install transaction in an unreadable state makes the receipt unsafe; the lane cannot be
    judged, so the check says so. Its clone would answer "current" (the remote and the checkout at one commit):
    the verdict must not come from the source checkout."""
    fake = FakeSystem()
    monkeypatch.setattr(ControllerService, "binary_target", lambda self: TARGET)
    svc = ControllerService(system=fake.system, paths=Paths(runtime_root=tmp_path))
    _installed(svc, binary_receipt, "2026-11-09T17:54:53Z")
    _serve(monkeypatch, "daemon", _entry("daemon", "2" * 64, _other(svc, "daemon"), "2026-11-10T03:51:59Z"))
    comp = svc.stack("daemon").component("loraham-daemon")
    src = svc._paths.resolve_source(comp.source.path)
    src.mkdir(parents=True, exist_ok=True)
    fake.commands[("git", "ls-remote", comp.source.remote, comp.source.branch or "HEAD")] = \
        CommandResult(0, comp.source.pin_commit + "\tHEAD\n", "")
    fake.commands[("git", "-C", str(src), "rev-parse", "HEAD")] = CommandResult(0, comp.source.pin_commit + "\n", "")
    jp = bi.journal_path(svc._paths)
    jp.parent.mkdir(parents=True, exist_ok=True)
    jp.write_text("{ not json")
    assert svc.binary_receipt_state("daemon")[0] == "unsafe"
    assert _checked(svc) == "could not check"                     # the clone alone says "current"
    # G1 B1: the stored verdict is tied to the checkout head the status probe reports for an unsafe receipt (the
    # clone's), so the page and `status --versions`, which read it through `effective_status`, show it too
    entry = stackupdates.view(svc._paths)["components"]["loraham-daemon"]
    assert stackupdates.effective_status(entry, comp.source.pin_commit) == stackupdates.ERROR, entry  # RED: unchecked


# ---- G2-B1: the binary lane's verdict on every surface, keyed by the installed artifact ----

def _status_src(svc, cid="loraham-daemon"):
    """The `src` words of the component's line in `lhpc status daemon`."""
    lines = [d for d in svc.status("daemon").details if d.split()[:1] == [cid]]
    assert len(lines) == 1, lines
    return lines[0].split(" src ", 1)[1].strip()


def _versions(svc, cid="loraham-daemon"):
    lines = [d for d in svc.status_versions().details if d.split()[:1] == [cid]]
    assert len(lines) == 1, lines
    return lines[0]


def _page(svc, web):
    import htmlq
    client = web(service_factory=lambda: ControllerService(system=svc._system, paths=svc._paths))
    return htmlq.parse(client.get("/stacks/daemon/body").get_data(as_text=True))


def _install_pills(svc, web):
    """The covered components' lane pills in the daemon stack's Install section."""
    doc = _page(svc, web)
    return {p.text.strip() for p in doc.within(doc.by_id("stack-install-daemon")).find("span", class_="ver")}


def _info_card(svc, web):
    doc = _page(svc, web)
    card = doc.within(doc.by_id("stack-info-daemon"))
    rows = [card.within(tr).find("td")[0].text for tr in card.find("tr")
            if [th.text for th in card.within(tr).find("th")] == ["Source / profile"]]
    assert len(rows) == 1, rows
    return rows[0]


def _behind(tmp_path, monkeypatch, binary_receipt):
    svc = _svc(tmp_path, monkeypatch)
    _installed(svc, binary_receipt, "2026-11-09T17:54:53Z")
    _serve(monkeypatch, "daemon", _entry("daemon", "2" * 64, _other(svc, "daemon"), "2026-11-10T03:51:59Z"))
    assert _checked(svc) == _is(stackupdates.BEHIND)
    return svc


def test_a_binary_row_behind_its_newest_entry_reads_the_lane_verdict_on_every_surface(tmp_path, monkeypatch,
                                                                                       binary_receipt, web):
    svc = _behind(tmp_path, monkeypatch, binary_receipt)
    assert _status_src(svc) == "binary, update available"                          # RED before: "binary"
    assert "binary, update available" in _versions(svc)
    assert _info_card(svc, web) == "binary, update available / candidate-available"
    assert _install_pills(svc, web) == {"update available"}


def test_a_binary_verdict_reads_unchecked_once_another_artifact_with_the_same_commits_is_installed(
        tmp_path, monkeypatch, binary_receipt, web):
    svc = _behind(tmp_path, monkeypatch, binary_receipt)
    binary_receipt(svc, sha="3" * 64, commits=_other(svc, "daemon"),             # the same component commits
                   provenance={"published_at": "2026-11-09T17:54:53Z"})
    assert _install_pills(svc, web) == set()                                     # RED before: {"update available"}
    assert _status_src(svc) == "binary, unchecked"
