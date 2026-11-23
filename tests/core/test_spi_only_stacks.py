"""SPI-REFUSAL (PLAN-SPI-REFUSAL v4): a stack with `radio_access = "spi"` (reticulum, meshtastic: they drive the radio
themselves) is OFFERED and STARTED on SPI radios only (instances.eligible / radio_refusal, copy_of's refusal); what runs
and what a Stop reaches stay the inventory (radios_of, stack_instances, the running keys). Box H's list: r1 loraham 433,
r2 loraham 868 (SPI), r3 CH341-RFM98 433 and r4 PineDio 868 (USB).
Reticulum's refusal is per interface since its `backend` setting (plan reti v21 §1, B1): these tests save
`backend = spi` on every radio (`_spi_backend`), so they hold for that backend; the default `daemon` is in
test_reticulum_backend.py."""
from __future__ import annotations

import contextlib
import subprocess
import sys

import pytest
import radios_fixture
from htmlq import parse

from lhpc.adapters.cli import main as cli
from lhpc.core import commands, instances, manifest, radio_models, radios
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ActionResult, ControllerService

BOX_H = radios_fixture.BOX_H
USB_ONLY = BOX_H[2:]                                            # r3, r4: no SPI radio at all
TWO_SPI_868 = (radios.Radio("r2", "a", "uputronics", "868", ce="ce0"), radios.Radio("r5", "b", "uputronics", "868", ce="ce1"))


def why(target, rid, kind="USB-CH341", alts=None):
    sid = target.partition("@")[0]
    owner = "meshtastic" if sid.startswith("meshtastic") else "reticulum"
    if alts is None:                                            # F34: Reticulum runs on 868 only, r1 is 433
        alts = ("r1", "r2") if owner == "meshtastic" else ("r2",)
    who = owner if owner == "meshtastic" else "reticulum with backend spi"
    tail = ("use " + " or ".join(f"{owner}@{r}" for r in alts)) if alts else "add an SPI radio on the Hardware page"
    return f"{owner}@{rid}: {who} drives SPI radios only, {rid} is a {kind} radio: {tail}"


def _spi_backend(tmp_path, rows):
    """Reticulum on the direct driver (`backend = spi`) on every radio of `rows`."""
    radios_fixture.reticulum_backend(tmp_path, [r.id for r in rows])


def _svc(tmp_path, rows=BOX_H):
    radios_fixture.write(tmp_path, rows)
    _spi_backend(tmp_path, rows)
    return ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))


@contextlib.contextmanager
def _running(svc, rid, band, alive=True, sid="reticulum", cid="rns"):
    """`sid`@`rid` runs by its verified launch record on a live process; alive=False: the record stays, the process is
    gone (a stale record)."""
    p = subprocess.Popen([sys.executable, "-c", "import signal; signal.pause()"], start_new_session=True)
    try:
        stk = svc.stack(sid)
        assert svc._lifecycle().record_launch(stk, stk.component(cid), p.pid, band=band, extra={
                "rid": rid, **({"node_owner": True} if instances.fixed_ports_only(stk) else {})})   # as a start writes it
        if not alive:
            p.kill()
            p.wait(timeout=5)
        yield p
    finally:
        if p.poll() is None:
            p.kill()
            p.wait(timeout=5)


def _selects(doc, prefix):
    """{aria-label: [option values]} of the rendered selects whose label starts with `prefix`."""
    return {s["aria-label"]: [o["value"] for o in doc.within(s).find("option") if o["value"]]
            for s in doc.find("select") if (s["aria-label"] or "").startswith(prefix)}


def _forms(doc, op):
    """The hidden `target` of every rendered fixed-radio form posting `op`."""
    out = []
    for f in doc.find("form"):
        fields = {i["name"]: i["value"] for i in doc.within(f).find("input") if i["name"]}
        if fields.get("op") == op and "target" in fields:
            out.append(fields["target"])
    return out


# ---- the property (T1) and the helper (T13) -------------------------------------------------------------------

def test_t1_an_unknown_radio_access_is_a_manifest_error():
    # red-before (aa4a88c8): any value was accepted silently
    with pytest.raises(manifest.ManifestError, match="radio_access"):
        manifest.parse_manifest({"stack": [{"id": "x", "radio_access": "usb", "component": []}]})


def test_t1_the_shipped_spi_stacks_are_reticulum_and_meshtastic_every_other_is_daemon(tmp_path):
    svc = _svc(tmp_path)
    access = {s.id: getattr(s, "radio_access", "daemon") for s in svc.stacks()}     # red-before: no stack is "spi"
    assert {k for k, v in access.items() if v == "spi"} == {"meshtastic"}           # reticulum: per interface (B1)
    assert set(access.values()) == {"spi", "daemon"}
    assert {s.id for s in svc.stacks() if instances.access(svc, s, "r2") == "spi"} == {"reticulum", "meshtastic"}


@pytest.mark.parametrize("model,kind", [("no-such-model", "UNKNOWN"), ("", "UNKNOWN"),
                                        ("pinedio-usb-1262", "USB-CH341")])
def test_t13_an_unknown_empty_or_usb_model_is_refused_for_an_spi_stack_only(tmp_path, model, kind):
    svc, models = _svc(tmp_path), radio_models.load()
    radio = radios.Radio("r4", "x", model, "868")
    assert instances.radio_refusal(svc.stack("reticulum"), radio, models, "spi") == \
        f"reticulum with backend spi drives SPI radios only, r4 is a {kind} radio"
    assert instances.radio_refusal(svc.stack("meshtastic"), radio, models) == \
        f"meshtastic drives SPI radios only, r4 is a {kind} radio"
    assert instances.radio_refusal(svc.stack("kiss"), radio, models) == ""
    assert instances.radio_refusal(svc.stack("reticulum"), radio, models, "daemon") == ""
    assert instances.radio_refusal(svc.stack("reticulum"), radios.Radio("r2", "h", "loraham", "868"), models, "spi") == ""


# ---- inventory vs eligibility (T2) ------------------------------------------------------------------------------

def test_t2_eligibility_is_the_spi_radios_the_inventory_is_unchanged(tmp_path):
    svc = _svc(tmp_path)
    assert instances.eligible(svc.stack("meshtastic"), svc) == ("r1", "r2")
    assert instances.radios_of(svc.stack("meshtastic"), svc._paths) == ("r1", "r2", "r3", "r4")
    assert instances.eligible(svc.stack("reticulum"), svc) == ("r2",)               # F34: 868 only, r2 the SPI one
    assert instances.radios_of(svc.stack("reticulum"), svc._paths) == ("r2", "r4")
    assert instances.eligible(svc.stack("kiss"), svc) == ("r1", "r2", "r3", "r4")
    assert [i.rid for i in commands.stack_instances(svc.stack("meshtastic"), svc._paths)] == ["r1", "r2", "r3", "r4"]
    assert [i.rid for i in commands.stack_instances(svc.stack("reticulum"), svc._paths)] == ["r2"]   # singleton rule, 868
    with _running(svc, "r4", "868"):
        assert [i.rid for i in commands.stack_instances(svc.stack("reticulum"), svc._paths)] == ["r4"]
        assert instances.eligible(svc.stack("reticulum"), svc) == ("r2",)


def test_t2_two_spi_radios_on_one_band_stay_addressable_and_a_band_start_names_them(tmp_path):
    svc = _svc(tmp_path, TWO_SPI_868)
    assert instances.eligible(svc.stack("reticulum"), svc) == ("r2", "r5")
    assert [instances.copy_of(svc, "reticulum", r)[1] for r in ("r2", "r5")] == ["", ""]
    rid, why_ = instances.one_radio(svc, "reticulum", "868")
    assert rid == "" and "name the radio" in why_
    assert [i.rid for i in commands.stack_instances(svc.stack("reticulum"), svc._paths)] == ["r2"]


# ---- the start refusal: copy_of (T14), the band start (T15) -----------------------------------------------------

def _addressed(svc, sid, rid):
    inst, why_ = instances.copy_of(svc, sid, rid)
    return (inst.rid, inst.band) if inst is not None else None, why_


def test_t14_a_refused_radio_that_does_not_run_is_addressed_with_the_one_text(tmp_path):
    # red-before (aa4a88c8): an instance with no why. Addressed like a disabled radio's (its view and Stop), never
    # started (every start entry refuses on the why). Guards the why-chain: a refused radio never reads launched[rid]
    svc = _svc(tmp_path)
    assert _addressed(svc, "reticulum", "r4") == (("r4", "868"), why("reticulum", "r4"))
    assert _addressed(svc, "meshtastic", "r3") == (("r3", "433"), why("meshtastic", "r3"))


def test_t14_without_an_spi_radio_the_text_names_the_hardware_page(tmp_path):
    svc = _svc(tmp_path, USB_ONLY)
    assert _addressed(svc, "reticulum", "r4") == (("r4", "868"), why("reticulum", "r4", alts=()))


def test_t15_a_band_start_takes_the_one_radio_it_may_start_on(tmp_path):
    # red-before (aa4a88c8): "'reticulum' runs on r2, r4 (868 MHz): name the radio"
    svc = _svc(tmp_path)
    assert instances.one_radio(svc, "reticulum", "868") == ("r2", "")
    assert instances.one_radio(svc, "kiss", "868")[0] == ""                          # a daemon stack: both, named


# ---- the CLI (T6, T7, T8, T9) -----------------------------------------------------------------------------------

@pytest.fixture
def box(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("LHPC_RUNTIME_ROOT", str(tmp_path))
    calls = []
    monkeypatch.setattr(ControllerService, "run_action", lambda self, op, target, **kw: (
        calls.append((op, target, getattr(kw.get("inst"), "rid", None))), ActionResult(True, "ok", data={"changes": 1}))[1])

    def run(*argv, rows=BOX_H, write=True):
        if write:
            radios_fixture.write(tmp_path, rows)
            _spi_backend(tmp_path, rows)
        rc = cli.main(list(argv))
        return rc, capsys.readouterr(), calls
    return run


@pytest.mark.parametrize("verb", ["start", "restart"])
@pytest.mark.parametrize("target", ["reticulum@r4", "meshtastic@r3", "nomadnet@r4"])
def test_t6_the_cli_refuses_a_usb_radio_with_the_one_text_and_runs_nothing(box, verb, target):
    # red-before (aa4a88c8): rc 0, run_action called
    rc, out, calls = box("stack", verb, target, "--yes")
    rid = target.partition("@")[2]
    assert (rc, calls) == (1, []) and out.err == f"ERR   {why(target, rid)}\n"


def test_t7_a_bare_start_names_the_radios_it_may_start_on(box):
    # red-before (aa4a88c8): "reticulum can run on r1 or r2 or r3 or r4: …"
    rc, out, calls = box("stack", "start", "reticulum", "--yes")
    assert (rc, calls) == (1, []) and \
        out.err == "ERR   reticulum can run on r2: use reticulum@r2\n"          # F34: 868 only


@pytest.mark.parametrize("target", ["reticulum@r2", "meshtastic@r1", "kiss@r4"])
def test_t8_an_spi_row_still_starts(box, target):
    rc, _out, calls = box("stack", "start", target, "--yes")
    assert rc == 0 and calls[-1] == ("start", target.partition("@")[0], target.partition("@")[2])


def test_t9_a_running_instance_on_a_usb_radio_is_stopped_and_its_start_refused(tmp_path, box):
    radios_fixture.write(tmp_path, BOX_H)
    _spi_backend(tmp_path, BOX_H)
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    with _running(svc, "r4", "868"):
        rc, _out, calls = box("stack", "stop", "reticulum@r4", "--yes", write=False)
        assert rc == 0 and calls[-1] == ("stop", "reticulum", "r4")
        rc, out, _calls = box("stack", "start", "reticulum@r4", "--yes", write=False)
        assert rc == 1 and out.err == f"ERR   {why('reticulum', 'r4')}\n"      # never suggests r4 itself


def test_t9_a_running_instance_whose_radio_became_usb_is_stopped_and_its_start_refused(tmp_path, box):
    radios_fixture.write(tmp_path, BOX_H)
    _spi_backend(tmp_path, BOX_H)
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    with _running(svc, "r2", "868"):                                 # launched while r2 was the loraham board
        radios_fixture.write(tmp_path, (BOX_H[0], radios.Radio("r2", "high", "pinedio-usb-1262", "868", usb_serial="PINE0002"),
                                        *BOX_H[2:]))
        rc, _out, calls = box("stack", "stop", "reticulum@r2", "--yes", write=False)
        assert rc == 0 and calls[-1] == ("stop", "reticulum", "r2")
        rc, out, _calls = box("stack", "start", "reticulum@r2", "--yes", write=False)
        assert rc == 1 and out.err == f"ERR   {why('reticulum', 'r2', alts=())}\n"   # r1 is 433


# ---- the web (T3, T4, T5, T12, the Settings Run) ----------------------------------------------------------------

def _web_box(monkeypatch):
    monkeypatch.setattr(ControllerService, "is_installed", lambda self, t: True)
    monkeypatch.setattr(ControllerService, "unbuilt_components", lambda self, t: [])
    monkeypatch.setattr(ControllerService, "start_readiness", lambda self, t, inst=None: ([], []))
    seen = []
    monkeypatch.setattr(ControllerService, "spawn_start_job",
                        lambda self, op, target, band="", stop_owners=False, cascade=False, rid="", plan_hash=None:
                        (seen.append((op, target, rid)), (f"web-{op}-{target}-{rid}.log", "admitted", ""))[1])
    return seen


def _page(web, svc, url):
    return parse(web(service_factory=lambda: svc).get(url, headers={"Host": "localhost:8443"}).get_data(as_text=True))


def test_t3_home_offers_the_spi_stacks_on_the_spi_rows_only(tmp_path, web):
    # red-before (aa4a88c8, measured): r3 and r4 offered meshtastic@ and reticulum@
    svc = _svc(tmp_path)
    sel = _selects(_page(web, svc, "/"), "Stack to start on")
    for rid in ("r1", "r2"):
        assert f"meshtastic@{rid}" in sel[f"Stack to start on {rid}"]
    assert "reticulum@r2" in sel["Stack to start on r2"]
    assert "reticulum@r1" not in sel["Stack to start on r1"]                        # F34: Reticulum runs on 868 only
    for rid in ("r3", "r4"):
        offered = sel[f"Stack to start on {rid}"]
        assert not {v.partition("@")[0] for v in offered} & {"reticulum", "meshtastic"}, offered
        assert f"kiss@{rid}" in offered


@pytest.mark.parametrize("state", ["none", "live", "stale", "gone-row"])
def test_t4_the_stacks_run_line_starts_on_spi_radios_and_stops_what_runs(tmp_path, web, state):
    # red-before (aa4a88c8, measured): Start nomadnet on radio offered @r1..@r4
    svc = _svc(tmp_path)
    run = contextlib.nullcontext() if state == "none" else _running(svc, "r4", "868", alive=state != "stale")
    with run:
        if state == "gone-row":                                      # r4 left the list while reticulum@r4 runs (G1 N1)
            radios_fixture.write(tmp_path, BOX_H[:3])
        doc = _page(web, svc, "/stacks?open=reticulum")
        for label in ("Start nomadnet on radio", "Run reticulum on radio", "Restart reticulum on radio"):
            assert _selects(doc, label)[label] == [f"{label.split()[1]}@r2"], label   # F34: 868 only, r2 the SPI one
        assert "reticulum@r4" in _selects(doc, "Stop reticulum on radio")["Stop reticulum on radio"]   # Stop: as today


def test_t12_home_shows_a_running_usb_instance_with_its_stop(tmp_path, web):
    svc = _svc(tmp_path)
    with _running(svc, "r4", "868"):
        doc = _page(web, svc, "/")
        assert "reticulum@r4" in _forms(doc, "stop")
        assert "reticulum@r4" not in _selects(doc, "Stack to start on r4").get("Stack to start on r4", [])


def test_the_settings_run_is_never_a_start_on_a_usb_radio_its_stop_stays(tmp_path, web):
    # red-before (aa4a88c8): ?radio=r4 rendered a Run of meshtastic-cli@r4 (G1-SPI4 B1). The page shows r4's
    # own view (its instance is addressed, copy_of), with its Stop and without a Run
    svc = _svc(tmp_path)
    r4, r2 = (_page(web, svc, f"/stacks?open=meshtastic&radio={r}") for r in ("r4", "r2"))
    assert "meshtastic-cli@r4" not in _forms(r4, "start") and "meshtastic-cli@r4" in _forms(r4, "stop")
    assert "meshtastic-cli@r2" in _forms(r2, "start")


@pytest.mark.parametrize("sid,cid,shown", [("meshtastic", "meshtastic", False), ("kiss", "loraham-kiss-tnc", True)])
def test_needs_attention_names_a_down_daemon_only_for_a_stack_that_needs_it(tmp_path, web, sid, cid, shown):
    # box H 11-21: "r2: daemon offline while meshtastic@r2 needs it" on Home, while meshtastic drives r2 itself
    # (radio_access "spi"); a daemon client on r2 (kiss) still names it. red-before (9f4da541): meshtastic named
    import re
    svc = _svc(tmp_path)
    with _running(svc, "r2", "868", sid=sid, cid=cid):
        page = web(service_factory=lambda: svc).get("/", headers={"Host": "localhost:8443"}).get_data(as_text=True)
    text = " ".join(re.sub(r"<[^>]+>", " ", page).split())
    assert (f"Needs attention r2: daemon offline while {sid}@r2 needs it" in text) is shown, text[:2000]


def test_the_settings_run_without_a_page_radio_selects_over_the_start_radios_only(tmp_path, web):
    # G2-SPI NOTE2 (THEORETICAL): with no page radio the optional Run is a selector; it lists start_on (instances.
    # eligible), never a running USB radio of run_on, whose Stop selector keeps it. Rendered directly: no measured page
    # reaches an empty page radio (/stacks?open=<stack> without ?radio= shows the first radio). red-before (b38f349f):
    # the Run selector listed meshtastic-cli@r4
    import types

    import flask
    app = web(service_factory=lambda: _svc(tmp_path)).application
    opt = {"id": "meshtastic-cli", "name": "Meshtastic CLI", "running": False, "startable": False, "autostart": False}
    with app.test_request_context("/stacks"):
        html = flask.render_template("_settings_config.html", stack=types.SimpleNamespace(id="meshtastic"),
                                     view={"radio": "", "optional": [opt]}, config_groups=[],
                                     g={"run_on": ["r1", "r2", "r4"], "start_on": ["r1", "r2"]})
    sel = _selects(parse(html), "")
    assert sel["Run meshtastic-cli on radio"] == ["meshtastic-cli@r1", "meshtastic-cli@r2"], sel
    assert sel["Stop meshtastic-cli on radio"] == ["meshtastic-cli@r1", "meshtastic-cli@r2", "meshtastic-cli@r4"], sel


@pytest.mark.parametrize("target", ["reticulum@r4", "meshtastic@r3", "nomadnet@r4"])
def test_t5_a_web_start_on_a_usb_radio_flashes_the_one_text_and_spawns_nothing(tmp_path, monkeypatch, web, csrf, target):
    # red-before (aa4a88c8): the start went on to the confirm / spawn
    svc, seen = _svc(tmp_path), _web_box(monkeypatch)
    c = web(service_factory=lambda: svc)
    page = c.post("/action", data={"_csrf": csrf(c), "op": "start", "target": target},
                  follow_redirects=True).get_data(as_text=True)
    assert why(target, target.partition("@")[2]) in page.replace("&#39;", "'") and seen == []


def test_t5_a_live_usb_instance_is_never_the_suggested_radio(tmp_path, monkeypatch, web, csrf):
    svc, seen = _svc(tmp_path), _web_box(monkeypatch)
    with _running(svc, "r4", "868"):
        c = web(service_factory=lambda: svc)
        page = c.post("/action", data={"_csrf": csrf(c), "op": "start", "target": "reticulum@r4"},
                      follow_redirects=True).get_data(as_text=True)
    assert why("reticulum", "r4") in page and seen == []


# ---- the web job (T10) ------------------------------------------------------------------------------------------

def test_t10_the_detached_start_of_a_usb_radio_fails_with_the_text_and_launches_nothing(tmp_path, monkeypatch):
    # red-before (aa4a88c8): the job went on to the start
    import os

    from lhpc.core import jobresult, jobs, procident
    monkeypatch.setenv("LHPC_RUNTIME_ROOT", str(tmp_path))
    monkeypatch.setenv("LHPC_WEBJOB_GATE_TIMEOUT_S", "0.3")
    svc = _svc(tmp_path)
    log, aid = "web-start-reticulum-r4.log", "c" * 32
    assert jobresult.reserve(svc._paths, log, aid, "start", "reticulum", "reticulum", [])
    assert jobs.write_job_marker(svc._paths, log, os.getpid(), "reticulum", "start",
                                 ident=procident.proc_identity(os.getpid()), attempt_id=aid)
    started = []
    monkeypatch.setattr(ControllerService, "start", lambda self, *a, **k: (started.append(a), ActionResult(True, ""))[1])
    rc = cli.main(["_stack-start", "reticulum", "--web-result", log, "--attempt-id", aid, "--radio", "r4"])
    assert rc == 1 and started == []
    assert (jobresult.read_one(svc._paths, log) or {}).get("detail") == why("reticulum", "r4")
