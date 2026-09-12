"""RF logs in the manifest and the registry: every config owner declares ONE public band-less
`rf_log` switch, the writers get the registry file on their run line or in their generated
config, meshtastic's `TraceFile` is derived from the switch, and only the six registered
surfaces resolve."""

from __future__ import annotations

import pytest

from lhpc.core import commands, rflog
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService


def _svc(tmp_path):
    return ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))


def _switch_params(stack):
    out = []
    for c in stack.components:
        out += [(c.id, "run", p) for p in c.run_params if p.name == rflog.RF_LOG_PARAM]
        if c.config_file:
            out += [(c.id, "file", p) for p in c.config_file.params if p.name == rflog.RF_LOG_PARAM]
    return out


@pytest.mark.parametrize("entry", rflog.REGISTRY, ids=[e.surface for e in rflog.REGISTRY])
def test_every_owner_declares_one_public_switch_on_its_writer(tmp_path, entry):
    svc = _svc(tmp_path)
    owner = svc.stack(entry.owner)
    assert owner is not None and svc.stack(entry.surface) is not None
    assert owner.component(entry.writer) is not None
    declared = _switch_params(owner)
    assert [(cid, kind) for cid, kind, _p in declared] == [(entry.writer, entry.kind)]
    p = declared[0][2]
    assert p.kind == "enum" and tuple(p.choices) == ("on", "off") and p.default == "on"
    assert p.group == rflog.GROUP and p.apply_mode == "restart"
    assert not getattr(p, "hidden", False)


def test_graywolf_declares_no_switch_of_its_own(tmp_path):
    # It proxies to the kiss TNC, where its frames cross the radio.
    assert _switch_params(_svc(tmp_path).stack("graywolf")) == []
    assert rflog.entry("graywolf").owner == "kiss"


def test_only_the_six_surfaces_resolve():
    assert [e.surface for e in rflog.REGISTRY] == ["daemon", "graywolf", "meshcom", "meshtastic",
                                                   "meshcore", "reticulum"]
    for other in ("kiss", "chat", "voice", "loraham-kiss-tnc", ""):
        assert rflog.entry(other) is None
    assert rflog.by_job("rf-made-up.log") is None
    assert rflog.by_job("rf-kiss.log")[0].surface == "graywolf"
    e = rflog.entry("daemon")
    assert e.job("433") == "rf-daemon-433.log" and e.job("868") == "rf-daemon-868.log"
    assert e.job() is None and e.banded and not rflog.entry("meshcom").banded


@pytest.mark.parametrize("surface,band", [("daemon", "433"), ("daemon", "868"),
                                          ("graywolf", ""), ("meshcom", "")])
def test_argv_writers_get_the_switch_and_the_registry_file(tmp_path, surface, band):
    svc = _svc(tmp_path)
    e = rflog.entry(surface)
    comp = svc.stack(e.owner).component(e.writer)
    base = {"radio": band, "hw": "loraham", "txmode": "managed", "cadmon": "off", "cadrssi": "-90"}
    for value in ("on", "off"):
        argv = commands.expand_argv(comp.run_argv, comp, {**base, "rf_log": value},
                                    svc.config().operator, "/rt", "/src", band)
        assert argv[argv.index("--rflog") + 1] == value
        assert argv[argv.index("--rflog-path") + 1] == f"/rt/logs/{e.job(band)}"


def test_meshtastic_trace_file_is_derived_from_the_switch(tmp_path):
    svc = _svc(tmp_path)
    comp = svc.stack("meshtastic").component("meshtastic")
    p = next(p for p in comp.config_file.params if p.name == rflog.MESHTASTIC_TRACE_PARAM)
    assert p.hidden and p.omit_if_empty and (p.section, p.key) == ("Logging", "TraceFile")
    gen = tmp_path / "config" / "files" / "meshtasticd.yaml"

    def active_trace():
        return [ln.strip() for ln in gen.read_text().splitlines()
                if ln.strip().startswith("TraceFile:")]
    assert [w.status for w in svc.write_config_files("meshtastic")] == ["written"]
    assert active_trace() == [f"TraceFile: {tmp_path}/logs/rf-meshtastic.log"]
    assert svc.save_config_bundle("meshtastic", values={"rf_log": "off"}).ok
    svc.write_config_files("meshtastic")
    assert active_trace() == []                       # omitted, never blank
    # A stored or ephemeral value of the hidden param is ignored: the switch is the only input.
    svc.save_stack_config("meshtastic", {"file_trace_file": "/elsewhere/x.log"})
    svc.write_config_files("meshtastic", overrides={"trace_file": "/elsewhere/y.log"})
    assert active_trace() == []


def test_meshcore_and_reticulum_get_the_switch_and_the_path_in_their_config(tmp_path):
    svc = _svc(tmp_path)
    assert all(w.status == "written" for w in svc.write_config_files("meshcore"))
    toml = (tmp_path / "config" / "files" / "meshcore.toml").read_text()
    assert 'rf_log = "on"' in toml
    assert f'rf_log_path = "{tmp_path}/logs/rf-meshcore.log"' in toml
    assert all(w.status == "written" for w in svc.write_config_files("reticulum"))
    conf = (tmp_path / "state" / "reticulum" / "config").read_text()
    lora = conf[conf.index("[[LoRa]]"):]
    assert "rf_log = on" in lora and f"rf_log_path = {tmp_path}/logs/rf-reticulum.log" in lora
    assert svc.save_config_bundle("meshcore", values={"file_rf_log": "off"}).ok
    assert svc.save_config_bundle("reticulum", values={"file_rf_log": "off"}).ok
    svc.write_config_files("meshcore")
    svc.write_config_files("reticulum")
    assert 'rf_log = "off"' in (tmp_path / "config" / "files" / "meshcore.toml").read_text()
    assert "rf_log = off" in (tmp_path / "state" / "reticulum" / "config").read_text()
