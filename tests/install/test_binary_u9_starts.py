"""U9 on U8's start readiness, through the public paths: a binary U9 installs from an entry built from other commits
than today's pins starts (the readiness never compares pins), `lhpc update` installs a newer published entry, and a
receipt an older lhpc wrote over the pin check (U10a: its acceptance is ignored) still starts. The offer edges: a
re-publish at the same stamp is no offer; a newer entry that misses a covered component offers nothing and the
install refuses it typed."""
import json
import os

import pytest
import radios_fixture

from lhpc.adapters.cli import main as cli
from lhpc.core import binary_install as bi, binary_receipt as brx, radios
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService

TARGET = "aarch64-trixie"
OTHER = "f" * 40


def _svc(tmp_path, monkeypatch):
    monkeypatch.setattr(ControllerService, "binary_target", lambda self: TARGET)
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    monkeypatch.setattr(cli, "ControllerService", lambda *a, **k: svc)     # the CLI drives this box
    return svc


def _entry(sid, sha, components, published):
    filename = f"{sid}-{sha}.tar.zst"
    return {"filename": filename, "url": f"https://example.invalid/{filename}", "sha256": sha, "size": 10,
            "built_from": "b" * 40, "components": dict(components), "lhpc_commit": "c" * 40,
            "builder_commit": "d" * 40, "target": TARGET, "os": "trixie",
            "container_digest": "debian@sha256:" + "e" * 64, "runtime_deps": [],
            "smoke": {"mode": "mandatory", "result": "passed"}, "extract_to": "runtime-root", "inputs": {},
            "published_at": published}


def _serve(monkeypatch, sid, *entries):
    index = {"schema": 3, "stacks": {sid: list(entries)}}
    monkeypatch.setattr(bi, "_http_get", lambda url, max_bytes: json.dumps(index).encode())
    monkeypatch.setattr(bi, "require_zstd", lambda: None)


def _other(svc, sid):
    return {cid: OTHER for cid in svc._binary_pins(sid)}


def _staged(monkeypatch, svc, sid):
    """The download stages the artifact's proof and probe files; the probes answer; nothing to provision."""
    spec = svc.binary_spec(sid)
    files = sorted({*spec.proof_paths, *(next(iter(a)) for a in spec.probes)})

    def stage(tar, stage_dir, roots):
        for rel in files:
            os.makedirs(os.path.dirname(os.path.join(stage_dir, rel)), exist_ok=True)
            with open(os.path.join(stage_dir, rel), "w") as fh:
                fh.write("staged")
        return files
    monkeypatch.setattr(bi, "download_artifact", lambda *a, **k: None)
    monkeypatch.setattr(bi, "validate_and_extract", stage)
    monkeypatch.setattr(bi, "run_probe", lambda paths, argv: "ok")
    monkeypatch.setattr(ControllerService, "_binary_provision", lambda self, *a: [])


def _reaches_the_spawn(svc, monkeypatch, tmp_path):
    from lhpc.core import config as cfgmod
    cfgmod.save_hardware_setup(svc._paths, "uputronics")
    svc._invalidate_config()
    radios_fixture.write(tmp_path, (radios.Radio("r1", "low", "uputronics", "433", ce="ce0"),
                                    radios.Radio("r2", "high", "uputronics", "868", ce="ce1")))
    daemon = svc.stack("daemon").component("loraham-daemon")
    (tmp_path / daemon.source.path).mkdir(parents=True, exist_ok=True)
    spawned = []
    monkeypatch.setattr(type(svc._lifecycle()), "start",
                        lambda self, *a, **k: spawned.append(a) or (_ for _ in ()).throw(AssertionError("spawned")))
    try:
        svc.start("daemon", apply=True)
    except AssertionError:
        pass
    return svc.install_blocker(daemon), bool(spawned)


def test_a_binary_installed_from_other_commits_starts_and_update_installs_a_newer_one(tmp_path, monkeypatch,
                                                                                    capsys):
    svc = _svc(tmp_path, monkeypatch)
    other = _other(svc, "daemon")
    _serve(monkeypatch, "daemon", _entry("daemon", "1" * 64, other, "2026-11-12T00:00:00Z"))
    _staged(monkeypatch, svc, "daemon")
    rc = cli.main(["install", "daemon", "--source", "binary", "--yes"])
    assert rc == 0, capsys.readouterr().out
    st, rec, why = brx.receipt_state(svc._paths, "daemon")
    assert st == "valid" and rec.artifact_sha256 == "1" * 64 and rec.components == other, why
    assert _reaches_the_spawn(svc, monkeypatch, tmp_path) == ("", True)       # built from other commits: starts
    _serve(monkeypatch, "daemon", _entry("daemon", "2" * 64, other, "2026-11-13T00:00:00Z"),
           _entry("daemon", "1" * 64, other, "2026-11-12T00:00:00Z"))
    capsys.readouterr()
    rc = cli.main(["update", "daemon", "--yes"])
    out = capsys.readouterr().out
    assert rc == 0 and brx.receipt_state(svc._paths, "daemon")[1].artifact_sha256 == "2" * 64, out


def test_a_receipt_installed_over_the_pin_check_by_an_older_lhpc_still_starts(tmp_path, monkeypatch, binary_receipt):
    svc = _svc(tmp_path, monkeypatch)
    pins, cid = svc._binary_pins("daemon"), "loraham-daemon"
    binary_receipt(svc, "daemon", commits={**pins, cid: OTHER})
    p = brx.receipt_path(svc._paths, "daemon")
    d = json.loads(p.read_text())
    d["override"] = {"at": 1, "lhpc_version": "0.11.3", "mismatch": {cid: [OTHER, pins[cid]]}}
    p.write_text(json.dumps(d))
    svc.invalidate_snapshot()
    assert _reaches_the_spawn(svc, monkeypatch, tmp_path) == ("", True)


def test_a_re_publish_at_the_same_stamp_is_no_offer(tmp_path, monkeypatch, binary_receipt):
    # Another sha at the installed entry's published_at is not newer: no offer, nothing replaced.
    svc = _svc(tmp_path, monkeypatch)
    stamp = "2026-11-12T00:00:00Z"
    binary_receipt(svc, "daemon", sha="1" * 64, commits=_other(svc, "daemon"), provenance={"published_at": stamp})
    _serve(monkeypatch, "daemon", _entry("daemon", "2" * 64, _other(svc, "daemon"), stamp))
    assert svc.binary_freshness("daemon")["state"] == "current"
    plan = svc.install("daemon", apply=False, source="binary")
    assert plan.ok and plan.data["changes"] == 0 and "not newer than the installed one" in " ".join(plan.details), (
        plan.summary, plan.details)


def test_a_newer_entry_missing_a_covered_component_offers_nothing_and_the_install_refuses_it_typed(
        tmp_path, monkeypatch, binary_receipt):
    svc = _svc(tmp_path, monkeypatch)
    covers = _other(svc, "daemon")
    binary_receipt(svc, "daemon", sha="1" * 64, commits=covers, provenance={"published_at": "2026-11-10T00:00:00Z"})
    dropped = sorted(covers)[0]
    _serve(monkeypatch, "daemon",
           _entry("daemon", "2" * 64, {k: v for k, v in covers.items() if k != dropped}, "2026-11-12T00:00:00Z"),
           _entry("daemon", "1" * 64, covers, "2026-11-10T00:00:00Z"))
    assert svc.binary_freshness("daemon")["state"] == "current"          # not "could not check"
    res = svc.install("daemon", apply=False, source="binary")
    assert not res.ok and f"no published binary covers {dropped} yet: install from source" in res.summary, res.summary
    assert res.data.get("binary_refusal") == "none_compatible"


# ---- G2-UPDCHAIN-A named fixes: a receipt's publication time, one readiness rule ------------------------------


def _installed_at(svc, rec, stamp):
    import calendar
    import dataclasses
    import time
    at = calendar.timegm(time.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ"))
    assert brx.write_receipt(svc._paths, dataclasses.replace(rec, installed_at=float(at)))
    svc.invalidate_snapshot()


def test_a_receipt_without_its_publication_time_takes_it_from_its_listed_entry(tmp_path, monkeypatch, binary_receipt):
    # An older lhpc installed A (published Nov 1) on Nov 16 and recorded no publication time; B (Nov 10) is newer
    # than A, so it is offered: A's entry dates it, never the install time.
    svc = _svc(tmp_path, monkeypatch)
    other = _other(svc, "daemon")
    _installed_at(svc, binary_receipt(svc, "daemon", sha="1" * 64, commits=other, provenance={}),
                  "2026-11-16T00:00:00Z")
    b = _entry("daemon", "2" * 64, other, "2026-11-10T00:00:00Z")
    _serve(monkeypatch, "daemon", b, _entry("daemon", "1" * 64, other, "2026-11-01T00:00:00Z"))
    assert svc.binary_freshness("daemon") == {"state": "behind", "offer": b["filename"]}
    plan = svc.install("daemon", apply=False, source="binary")
    assert plan.ok and plan.data["changes"] == 1 and any(b["filename"] in d for d in plan.details), plan.details


def test_a_receipt_without_its_publication_time_and_no_listed_entry_offers_only_what_is_proven_newer(
        tmp_path, monkeypatch, binary_receipt):
    # A's sha is not listed and A's receipt has no publication time. A was published no later than it was
    # installed (Nov 16): an entry published after that is proven newer and offered; one published before it may be
    # older, so it is never offered and never "current": could not check, and the install keeps A.
    svc = _svc(tmp_path, monkeypatch)
    other = _other(svc, "daemon")
    _installed_at(svc, binary_receipt(svc, "daemon", sha="1" * 64, commits=other, provenance={}),
                  "2026-11-16T00:00:00Z")
    after = _entry("daemon", "2" * 64, other, "2026-11-16T12:00:00Z")
    _serve(monkeypatch, "daemon", after)
    assert svc.binary_freshness("daemon") == {"state": "behind", "offer": after["filename"]}
    before = _entry("daemon", "3" * 64, other, "2026-11-10T00:00:00Z")
    _serve(monkeypatch, "daemon", before)
    fresh = svc.binary_freshness("daemon")
    assert fresh["state"] == "error" and fresh["offer"] == "" and "no publication time" in fresh["why"], fresh
    plan = svc.install("daemon", apply=False, source="binary")
    assert plan.ok and plan.data["changes"] == 0 and "no publication time" in " ".join(plan.details), plan.details


def test_a_re_publication_of_the_installed_sha_never_dates_it_after_its_install(tmp_path, monkeypatch, binary_receipt):
    # G2-UPDCHAIN-A D3: an older lhpc installed A on Nov 5 (no publication time); A's original entry is gone and its
    # sha is listed again Nov 10, a re-publication. B, published Nov 10 after A's install, is newer whatever A's
    # date: offered, never "current" by the re-publication's stamp.
    svc = _svc(tmp_path, monkeypatch)
    other = _other(svc, "daemon")
    _installed_at(svc, binary_receipt(svc, "daemon", sha="1" * 64, commits=other, provenance={}),
                  "2026-11-05T00:00:00Z")
    b = _entry("daemon", "2" * 64, other, "2026-11-10T12:00:00Z")
    _serve(monkeypatch, "daemon", b, _entry("daemon", "1" * 64, other, "2026-11-10T12:00:00Z"))
    assert svc.binary_freshness("daemon") == {"state": "behind", "offer": b["filename"]}
    plan = svc.install("daemon", apply=False, source="binary")
    assert plan.ok and plan.data["changes"] == 1 and any(b["filename"] in d for d in plan.details), plan.details


@pytest.mark.parametrize("re_published", ["2026-11-18T00:00:00Z", "2026-11-12T00:00:00Z"],
                         ids=["after-the-install", "before-the-install"])
def test_a_re_publication_under_another_target_never_dates_the_installed_sha(tmp_path, monkeypatch, binary_receipt,
                                                                            re_published):
    # G2-UPDCHAIN-A D3 (second read) and D2 B1: A (originally Nov 1, no longer listed) installed Nov 16 by an older
    # lhpc; B for this box published Nov 10, before that install; A's sha listed again under ANOTHER target, after
    # the install or before it. Any listing of A's sha is only an upper bound on A's publication (it may be a
    # re-publication): B cannot be ordered against A: could not check, never "current", and the install keeps A.
    svc = _svc(tmp_path, monkeypatch)
    other = _other(svc, "daemon")
    _installed_at(svc, binary_receipt(svc, "daemon", sha="1" * 64, commits=other, provenance={}),
                  "2026-11-16T00:00:00Z")
    b = _entry("daemon", "2" * 64, other, "2026-11-10T00:00:00Z")
    _serve(monkeypatch, "daemon", b, {**_entry("daemon", "1" * 64, other, re_published),
                                      "target": "aarch64-bookworm", "os": "bookworm"})
    fresh = svc.binary_freshness("daemon")
    assert fresh["state"] == "error" and fresh["offer"] == "" and "no publication time" in fresh["why"], fresh
    plan = svc.install("daemon", apply=False, source="binary")
    assert plan.ok and plan.data["changes"] == 0 and "no publication time" in " ".join(plan.details), plan.details


def test_an_entry_published_in_the_second_of_the_install_is_not_proven_newer(tmp_path, monkeypatch, binary_receipt):
    # D2 N1: a legacy receipt installed at 12:00:00; B published at that same second is not proven later (">"):
    # could not check, kept.
    svc = _svc(tmp_path, monkeypatch)
    other = _other(svc, "daemon")
    _installed_at(svc, binary_receipt(svc, "daemon", sha="1" * 64, commits=other, provenance={}),
                  "2026-11-16T12:00:00Z")
    _serve(monkeypatch, "daemon", _entry("daemon", "2" * 64, other, "2026-11-16T12:00:00Z"))
    fresh = svc.binary_freshness("daemon")
    assert fresh["state"] == "error" and "no publication time" in fresh["why"], fresh


def test_an_install_the_start_refuses_is_not_current_and_not_kept(tmp_path, monkeypatch, binary_receipt):
    # One readiness rule: the installed binary records no commit for a covered component, so the start refuses it.
    # Its freshness is then never "current" (the stack's verdict is "update available"), and the install does not
    # keep it: a listed entry that covers it is installed although it is not newer.
    from lhpc.core import stackupdates
    svc = _svc(tmp_path, monkeypatch)
    covers = _other(svc, "daemon")
    dropped = sorted(covers)[0]
    partial = {k: v for k, v in covers.items() if k != dropped}
    binary_receipt(svc, "daemon", sha="1" * 64, commits=partial, provenance={"published_at": "2026-11-12T00:00:00Z"})
    full = _entry("daemon", "2" * 64, covers, "2026-11-10T00:00:00Z")
    _serve(monkeypatch, "daemon", full)
    comp = svc.stack("daemon").component(dropped)
    assert "the installed binary predates" in svc.install_blocker(comp)
    assert svc.binary_freshness("daemon") == {"state": "behind", "offer": full["filename"]}
    assert svc._update_probe(comp)["status"] == stackupdates.BEHIND
    plan = svc.install("daemon", apply=False, source="binary")
    assert plan.ok and plan.data["changes"] == 1 and any(full["filename"] in d for d in plan.details), plan.details


def test_an_install_the_start_refuses_with_nothing_covering_it_is_not_current(tmp_path, monkeypatch, binary_receipt):
    # Nothing published covers the missing component: never "current", and never "update available" (no update
    # exists): could not check, with the start's own reason, in the freshness and in the source check's detail.
    from lhpc.core import stackupdates
    svc = _svc(tmp_path, monkeypatch)
    covers = _other(svc, "daemon")
    dropped = sorted(covers)[0]
    partial = {k: v for k, v in covers.items() if k != dropped}
    binary_receipt(svc, "daemon", sha="1" * 64, commits=partial, provenance={"published_at": "2026-11-10T00:00:00Z"})
    _serve(monkeypatch, "daemon", _entry("daemon", "1" * 64, partial, "2026-11-10T00:00:00Z"))
    comp = svc.stack("daemon").component(dropped)
    reason = svc.install_blocker(comp)
    fresh = svc.binary_freshness("daemon")
    assert fresh["state"] == "error" and fresh["offer"] == "" and reason in fresh["why"], (fresh, reason)
    assert svc._update_probe(comp)["status"] == stackupdates.ERROR
    check = svc.source_check("daemon")
    assert any(d.startswith(f"  {dropped}: could not check — {fresh['why']}") for d in check.details), check.details
    res = svc.install("daemon", apply=False, source="binary")
    assert not res.ok and res.data.get("binary_refusal") == "none_compatible", res.summary


def test_a_receipt_gone_between_the_branch_and_its_freshness_is_could_not_check(tmp_path, monkeypatch, binary_receipt):
    # The source check takes the binary branch on a valid receipt, then its freshness reads the receipt again: a
    # retire or failed install in between leaves "n/a". Never "current": could not check.
    from lhpc.core import stackupdates
    svc = _svc(tmp_path, monkeypatch)
    covers = _other(svc, "daemon")
    binary_receipt(svc, "daemon", sha="1" * 64, commits=covers, provenance={"published_at": "2026-11-10T00:00:00Z"})
    _serve(monkeypatch, "daemon", _entry("daemon", "1" * 64, covers, "2026-11-10T00:00:00Z"))
    comp = svc.stack("daemon").component(sorted(covers)[0])
    real = ControllerService.binary_freshness

    def retired_meanwhile(self, sid):
        assert brx.remove_receipt(self._paths, sid)
        self.invalidate_snapshot()
        return real(self, sid)
    monkeypatch.setattr(ControllerService, "binary_freshness", retired_meanwhile)
    assert svc._update_probe(comp)["status"] == stackupdates.ERROR


def test_a_receipt_gone_between_the_branch_and_its_freshness_names_the_stack_could_not_check(
        tmp_path, monkeypatch, binary_receipt, capsys):
    # The same race through `lhpc source-check --json`, the sweep a self-update reads (U5): the stack is in
    # `stacks_unchecked` and in no other list, so the update says "Could not check every source of: daemon."
    svc = _svc(tmp_path, monkeypatch)
    covers = _other(svc, "daemon")
    binary_receipt(svc, "daemon", sha="1" * 64, commits=covers, provenance={"published_at": "2026-11-10T00:00:00Z"})
    _serve(monkeypatch, "daemon", _entry("daemon", "1" * 64, covers, "2026-11-10T00:00:00Z"))
    real = ControllerService.binary_freshness

    def retired_meanwhile(self, sid):
        brx.remove_receipt(self._paths, sid)
        self.invalidate_snapshot()
        return real(self, sid)
    monkeypatch.setattr(ControllerService, "binary_freshness", retired_meanwhile)
    capsys.readouterr()
    assert cli.main(["source-check", "--json"]) == 1
    data = json.loads(capsys.readouterr().out)["data"]
    assert (data["stacks_unchecked"], data["stacks_behind"], data["stacks_lhpc_first"]) == (["daemon"], [], []), data
