"""Reticulum's per-radio `backend` (plan reti v21 §1, B1): `daemon` (the default, any radio) or `spi` (the direct
driver, SPI radios only). The backend decides the interface's claims, requirements, its daemon dependency and the
rendered `type`; one variant (instances.for_backend) reaches the start (the run order), the snapshot (what runs) and
the configured readers (deps, install, doctor). Box H: r1 loraham 433, r2 loraham 868 (SPI), r3 CH341-RFM98 433 and
r4 PineDio 868 (USB)."""
from __future__ import annotations

import contextlib
import subprocess
import sys

import pytest
import radios_fixture

from lhpc.adapters.cli import main as cli
from lhpc.core import commands, instances, manifest
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ActionResult, ControllerService

BOX_H = radios_fixture.BOX_H
DAEMON = ("daemon", "loraham-daemon")
RNS = ("reticulum", "rns")
ARGV = {"loraham-daemon": lambda rid, band: ["loraham_daemon", "--radio", band, "--id", rid],
        "rns": lambda rid, band: ["loraham-rns-node"]}
SPI_CLAIMS = [("loraham.radio.{radio:868}", "exclusive"), ("loraham.radio.{radio:433}", "exclusive"),
              ("spi.bus.0", "cooperative"), ("spi.bus.0.unlocked", "exclusive")]   # rns's radio claims at d8e330cc
SPI_REQUIRES = ["/dev/spidev0.0", "spi,gpio", "/var/lib/dpkg/info/python3-libgpiod.list",
                "/var/lib/dpkg/info/python3-spidev.list"]


def _backend(tmp_path, rid, value):
    d = tmp_path / "config" / "stacks"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"reticulum@{rid}.toml").write_text(f'backend = "{value}"\n')


def _svc(tmp_path, table=None):
    radios_fixture.write(tmp_path, BOX_H)
    return ControllerService(system=FakeSystem(cmdlines_data=table or {}).system, paths=Paths(runtime_root=tmp_path))


@contextlib.contextmanager
def _box(tmp_path, *holders):
    """Box H with `holders` running as real sleep processes under their spec's argv, each with its launch record:
    (stack, component, rid, band)."""
    radios_fixture.write(tmp_path, BOX_H)
    procs = [subprocess.Popen([sys.executable, "-c", "import signal; signal.pause()"], start_new_session=True)
             for _ in holders]
    try:
        svc = _svc(tmp_path, {p.pid: ARGV[h[1]](h[2], h[3]) for p, h in zip(procs, holders)})
        for p, (sid, cid, rid, band) in zip(procs, holders):
            stk = svc.stack(sid)
            assert svc._lifecycle().record_launch(stk, stk.component(cid), p.pid, band=band, extra={"rid": rid})
        yield svc
    finally:
        for p in procs:
            p.kill()
            p.wait(timeout=5)


def _req(q):
    return q.check_file or ",".join(q.groups)


def _rns(order):
    return next(c for _s, c in order if c.id == "rns")


def _radio_side(comp):
    """`comp`'s claims on the radio, the bus and the daemon (its fixed TCP ports are no backend's)."""
    return [(r.key, r.mode.value) for r in comp.resources if r.kind.value in ("radio-band", "spi-bus", "daemon-socket")]


def _said(res):
    return " ".join([res.summary, *res.details])


# ---- the default and the refusal (M5) ------------------------------------------------------------------------------

@pytest.fixture
def box(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("LHPC_RUNTIME_ROOT", str(tmp_path))
    calls = []
    monkeypatch.setattr(ControllerService, "run_action", lambda self, op, target, **kw: (
        calls.append((op, target, getattr(kw.get("inst"), "rid", None))), ActionResult(True, "ok", data={"changes": 1}))[1])

    def run(*argv):
        radios_fixture.write(tmp_path, BOX_H)
        return cli.main(list(argv)), capsys.readouterr(), calls
    return run


def test_the_default_backend_starts_reticulum_on_a_usb_radio(box):
    # red-before (d8e330cc): rc 1, "reticulum drives SPI radios only, r4 is a USB-CH341 radio"
    rc, out, calls = box("stack", "start", "reticulum@r4", "--yes")
    assert (rc, calls[-1]) == (0, ("start", "reticulum", "r4")), out.err


def test_backend_spi_on_a_usb_radio_is_refused_at_save_and_at_start(tmp_path, box):
    svc = _svc(tmp_path)
    res = svc.save_config("reticulum", {"backend": "spi"}, inst=commands.Instance("r4", band="868"))
    # red-before (d8e330cc): refused as an unknown field, never by the radio
    assert not res.ok and "reticulum with backend spi drives SPI radios only, r4 is a USB-CH341 radio" in _said(res)
    assert not (tmp_path / "config" / "stacks" / "reticulum@r4.toml").exists()
    _backend(tmp_path, "r4", "spi")                                   # a hand-written file: the start refuses
    rc, out, calls = box("stack", "start", "reticulum@r4", "--yes")
    assert rc == 1 and calls == [] and "reticulum with backend spi drives SPI radios only" in out.err


def test_backend_spi_is_saved_on_an_spi_radio(tmp_path):
    svc = _svc(tmp_path)
    assert svc.save_config("reticulum", {"backend": "spi"}, inst=commands.Instance("r2", band="868")).ok
    assert instances.access(svc, svc.stack("reticulum"), "r2") == "spi"
    assert instances.access(svc, svc.stack("reticulum"), "r4") == "daemon"


def test_a_backend_change_while_that_radio_runs_is_refused_and_the_file_kept(tmp_path):
    with _box(tmp_path, (*RNS, "r2", "868")) as svc:
        res = svc.save_config("reticulum", {"backend": "spi"}, inst=commands.Instance("r2", band="868"))
        # red-before (d8e330cc): refused as an unknown field, never by the running instance
        assert not res.ok and "reticulum@r2 runs: its backend changes at its next start" in _said(res)
        assert not (tmp_path / "config" / "stacks" / "reticulum@r2.toml").exists()


# ---- the start's variant (C1) ---------------------------------------------------------------------------------------

def test_the_daemon_backend_runs_with_the_daemon_and_makes_no_spi_claim(tmp_path):
    svc = _svc(tmp_path)
    order = svc._run_order("reticulum", instances.copy_of(svc, "reticulum", "r4")[0])
    ids = [c.id for _s, c in order]
    # red-before (d8e330cc): no loraham-daemon in the order, the four SPI claims and requirements
    assert "loraham-daemon" in ids and ids.index("loraham-daemon") < ids.index("rns")
    assert _radio_side(_rns(order)) == [
        ("loraham.daemon-socket.{radio:433}", "consumer"), ("loraham.daemon-socket.{radio:868}", "consumer")]
    assert [_req(q) for q in _rns(order).requires] == []


def test_the_spi_backend_runs_as_before(tmp_path):
    svc = _svc(tmp_path)
    _backend(tmp_path, "r2", "spi")
    order = svc._run_order("reticulum", instances.copy_of(svc, "reticulum", "r2")[0])
    assert "loraham-daemon" not in [c.id for _s, c in order]
    assert _radio_side(_rns(order)) == SPI_CLAIMS
    assert [_req(q) for q in _rns(order).requires] == SPI_REQUIRES


# ---- what runs (C2, C3 live) -----------------------------------------------------------------------------------------

def _radio_conflicts(svc):
    return sorted((c.resource_key, *sorted(c.holders)) for c in svc.observed_conflicts()
                  if c.resource_key.startswith("loraham.radio."))      # rule-10: selects the radio claims of the list


def test_reticulum_on_the_daemon_beside_its_own_daemon_shows_no_conflict(tmp_path):
    # red-before (d8e330cc): ("loraham.radio.868 (radios r2, r4)", "loraham-daemon", "rns")
    with _box(tmp_path, (*RNS, "r4", "868"), (*DAEMON, "r4", "868")) as svc:
        assert _radio_conflicts(svc) == []


def test_reticulum_on_spi_beside_the_daemon_on_its_radio_still_shows_the_conflict(tmp_path):
    _backend(tmp_path, "r2", "spi")
    with _box(tmp_path, (*RNS, "r2", "868"), (*DAEMON, "r2", "868")) as svc:
        assert _radio_conflicts(svc) == [("loraham.radio.868 (radios r2, r4)", "loraham-daemon", "rns")]


def test_a_daemon_stop_reaches_reticulum_only_on_the_daemon_backend(tmp_path):
    # red-before (d8e330cc): reticulum is never a daemon dependent
    with _box(tmp_path, (*RNS, "r4", "868"), (*DAEMON, "r4", "868")) as svc:
        assert "reticulum" in svc.stop_dependents("daemon")
    _backend(tmp_path, "r2", "spi")
    with _box(tmp_path, (*RNS, "r2", "868"), (*DAEMON, "r4", "868")) as svc:
        assert "reticulum" not in svc.stop_dependents("daemon")


# ---- the rendered interface (M6) --------------------------------------------------------------------------------------

def _rendered_type(tmp_path, svc, rid):
    svc.write_config_files("rns", "868", inst=instances.copy_of(svc, "reticulum", rid)[0])
    lines = [ln.strip() for ln in (tmp_path / "state" / "reticulum" / "config").read_text().splitlines()]
    lora = lines[lines.index("[[LoRa]]") + 1:]
    return next(ln for ln in lora if ln.partition("=")[0].strip() == "type")


def test_the_rendered_interface_type_follows_the_backend(tmp_path):
    svc = _svc(tmp_path)
    # red-before (d8e330cc): "type = LoRaSPIInterface" for every radio
    assert _rendered_type(tmp_path, svc, "r2") == "type = LoRaDaemonInterface"
    _backend(tmp_path, "r2", "spi")
    assert _rendered_type(tmp_path, svc, "r2") == "type = LoRaSPIInterface"


# ---- the configured readers (M7) --------------------------------------------------------------------------------------

def _dep_keys(svc):
    return {f"{d['what']} {d['install']}" for d in svc.system_deps("reticulum")}


def test_the_deps_of_a_daemon_only_box_name_no_spi_requirement(tmp_path):
    svc = _svc(tmp_path)
    # red-before (d8e330cc): spidev, the SPI python bindings and the spi/gpio groups on every box
    assert not [k for k in _dep_keys(svc) if "spidev" in k or "gpiod" in k or "spi,gpio" in k]
    _backend(tmp_path, "r2", "spi")
    assert [k for k in sorted(_dep_keys(svc)) if "spidev" in k] != []


def test_the_bootstrap_set_keeps_the_spi_packages(tmp_path):
    core, _gui, _gps = _svc(tmp_path)._declared_dep_scopes()
    assert any("python3-spidev" in c for c in core) and any("python3-libgpiod" in c for c in core)


def test_explain_labels_the_backend_entries(tmp_path):
    text = "\n".join(_svc(tmp_path).explain("reticulum").details)
    # red-before (d8e330cc): no backend label, no daemon dependency
    assert "depends on (backend daemon): loraham-daemon" in text
    assert "claims spi.bus.0.unlocked (exclusive) (backend spi)" in text
    assert "claims loraham.daemon-socket.{radio:868} (consumer) (backend daemon)" in text


# ---- the manifest's rules ------------------------------------------------------------------------------------------

def _comp(**extra):
    return {"stack": [{"id": "x", "component": [{"id": "c", "kind": "service", **extra}]}]}


@pytest.mark.parametrize("extra,match", [
    ({"resource": [{"key": "k", "kind": "spi-bus", "backend": "spi"}]}, "no `backend` enum param"),
    ({"param": [{"name": "backend", "choices": ["a"]}], "require": [{"cmd": "x", "backend": "b"}]}, "not one of"),
    ({"param": [{"name": "backend", "choices": ["a"]}], "require": [{"cmd": "x", "gui": True, "backend": "a"}]},
     "`gui` or `absent_file`"),
    ({"backend_depends": {"a": ["c2"]}}, "no `backend` enum param"),
])
def test_a_backend_key_is_checked_at_load(extra, match):
    with pytest.raises(manifest.ManifestError, match=match):
        manifest.parse_manifest(_comp(**extra))


# ---- the live readers (C3) and the auto-install readers (M7) ---------------------------------------------------------

def test_a_daemon_stop_on_its_radio_names_reticulum_on_the_daemon_backend(tmp_path):
    from lhpc.core import radio_start
    # red-before (d8e330cc): reticulum@r4 is no dependent of daemon@r4
    with _box(tmp_path, (*RNS, "r4", "868"), (*DAEMON, "r4", "868")) as svc:
        assert "reticulum@r4" in radio_start.radio_dependents(svc, "868", radio_start.daemon_inst("r4"))


def test_the_daemon_cascade_and_the_tx_chain_take_reticulum_on_the_daemon(tmp_path):
    svc = _svc(tmp_path)
    # red-before (d8e330cc): reticulum depends on no daemon
    assert "reticulum" in svc._dependents_of("daemon")
    assert "loraham-daemon" in [c.id for c in svc._tx_chain_components(svc.stack("reticulum"))]
    for rid in ("r1", "r2", "r3", "r4"):
        _backend(tmp_path, rid, "spi")
    assert "reticulum" not in svc._dependents_of("daemon")
    assert "loraham-daemon" not in [c.id for c in svc._tx_chain_components(svc.stack("reticulum"))]


def test_stopping_reticulum_on_the_daemon_releases_the_band_it_alone_used(tmp_path, monkeypatch):
    # red-before (d8e330cc): ("daemon", []): a non-daemon-dependent stack releases nothing
    import types
    monkeypatch.setattr(ControllerService, "daemon_view", lambda self, band, rid="": types.SimpleNamespace(reachable=True))
    with _box(tmp_path, (*DAEMON, "r4", "868")) as svc:
        inst = instances.copy_of(svc, "reticulum", "r4")[0]
        assert svc._daemon_bands_to_release(svc.stack("reticulum"), "reticulum", {"868"}, inst) == ("daemon", ["868"])


def test_auto_install_takes_the_daemon_with_reticulum_and_no_spi_requirement(tmp_path):
    svc = _svc(tmp_path)
    # red-before (d8e330cc): no daemon edge, not mandatory, the SPI requirements listed
    assert "daemon" in svc._auto_install_scope_edges()[1]["reticulum"]
    assert svc._auto_install_dep_mandatory(svc.stack("reticulum"), "daemon")
    order = [st.id for st, _w in svc._auto_install_scope()]
    assert order.index("daemon") < order.index("reticulum")
    assert not [b for b in svc._auto_install_runtime_blockers(svc.stack("reticulum")) if "spi" in b]


def test_the_deps_report_of_a_daemon_only_box_names_no_spi_requirement(tmp_path):
    svc = _svc(tmp_path)
    # red-before (d8e330cc): /dev/spidev0.0 and the SPI bindings in every box's `lhpc deps reticulum`
    assert "spidev" not in repr(svc.deps_report("reticulum"))
    _backend(tmp_path, "r2", "spi")
    assert "spidev" in repr(svc.deps_report("reticulum"))


def test_without_an_enabled_radio_the_configured_backend_is_the_default(tmp_path):
    import dataclasses
    radios_fixture.write(tmp_path, [dataclasses.replace(r, enabled=False) for r in BOX_H])
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    assert instances.radios_of(svc.stack("reticulum"), svc._paths) == ()
    # red-before (d8e330cc): no daemon edge
    assert "daemon" in svc._auto_install_scope_edges()[1]["reticulum"]
