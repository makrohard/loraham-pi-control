"""U9 on U8's start readiness, through the public paths: a binary U9 installs from an entry built from other commits
than today's pins starts (the readiness never compares pins), `lhpc update` installs a newer published entry, and a
receipt an older lhpc wrote over the pin check (U10a: its acceptance is ignored) still starts. The offer edges: a
re-publish at the same stamp is no offer; a newer entry that misses a covered component offers nothing and the
install refuses it typed."""
import json
import os

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
