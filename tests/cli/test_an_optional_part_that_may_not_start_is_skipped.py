"""Q11b (his word 11-22: "if component is optional, warn only but start the rest"): a STACK start skips an optional
part whose install blocker fires (not built, not installed, a binary to repair) with a warning and starts the rest;
a required part, a build provider (a library another part is built from) and a direct start of the optional part
itself still refuse, as before. Through `lhpc stack start`, the detached web start (`_stack-start`), the web Start
POST and the stack page."""
import os

from radios_fixture import R1, R2_868, R3

from lhpc.adapters.cli.main import main
from lhpc.core import config as cfgmod
from lhpc.core import jobresult, jobs, procident, radios
from lhpc.core.commands import Instance
from lhpc.core.lifecycle import Lifecycle
from lhpc.core.manifest import load_manifest
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService

NOT_BUILT = "not built — run: lhpc build {} --yes"


def _meshcore(box, tmp_path, monkeypatch, blocked=("meshcore-webui",), absent=()):
    """meshcore on r3 with its Web UI ticked on r2 and r3 (an explicit tick: the parent takes the same path); the parts
    in `blocked` report "not built" (their source present), the parts in `absent` have no checkout ("not installed")."""
    run = box((R1, R2_868, R3), ports=(5266, 9054))
    for rid in ("r2", "r3"):
        cfgmod.update_stack_config(Paths(runtime_root=tmp_path), "meshcore",
                                   {"autostart_meshcore-webui": "on", "file_node_name": "Node R3"}, inst=Instance(rid))
    _blockers(monkeypatch, tmp_path, "meshcore", blocked, absent)
    return run


def _blockers(monkeypatch, tmp_path, sid, blocked=(), absent=()):
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    for c in svc.stack(sid).components:                    # the checkouts present (the stack is installed)
        if c.source is not None and c.id not in absent:
            svc._lifecycle().source_dir(c).mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(ControllerService, "install_blocker", lambda self, c: (
        f"not installed — run: lhpc install {sid}" if c.id in absent else
        NOT_BUILT.format(sid) if c.id in blocked else ""))
    monkeypatch.setattr(ControllerService, "_source_present", lambda self, c: c.id not in absent)
    monkeypatch.setattr(ControllerService, "spawn_start_job",            # the web Start's job: recorded, never run
                        lambda self, op, target, *a, rid="", **k: (f"web-{op}-{target}-{rid}.log", "admitted", ""))


def _launched(launches, comp):
    return [rid for c, _band, rid in launches if c == comp]


def _start_post(c, csrf, target):
    """The console's Start POST: (where it lands, that page's text)."""
    resp = c.post("/action", data={"_csrf": csrf(c), "op": "start", "target": target})
    assert resp.status_code == 302, resp.status_code
    return resp.location, c.get(resp.location.split("#")[0]).get_data(as_text=True)


def _child(tmp_path, target, rid):
    """The detached web start as the console spawns it (`_stack-start <target> --radio <rid> --web-result …`), over the
    box's service: (exit code, the attempt's job result)."""
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    log, aid = f"web-start-{target}-{rid}.log", "c" * 32
    assert jobresult.reserve(svc._paths, log, aid, "start", target, svc.stack_of(target) or target, [])
    assert jobs.write_job_marker(svc._paths, log, os.getpid(), target, "start",
                                 ident=procident.proc_identity(os.getpid()), attempt_id=aid)
    rc = main(["_stack-start", target, "--web-result", log, "--attempt-id", aid, "--radio", rid])
    return rc, jobresult.read_one(svc._paths, log)


def test_a_stack_start_skips_an_unbuilt_optional_part_with_a_warning_and_starts_the_rest(box, tmp_path, monkeypatch):
    # The general gate. Red before: rc 1, "[blocked] meshcore-webui: not built …", nothing launched.
    run = _meshcore(box, tmp_path, monkeypatch)
    rc, out, launches = run("start", "meshcore@r3")
    assert rc == 0 and _launched(launches, "meshcore-node") == ["r3"], out
    assert "  [skipped] meshcore-webui: not built — run: lhpc build meshcore --yes — not started; the rest of " \
           "meshcore starts" in out, out
    assert _launched(launches, "meshcore-webui") == [], launches


def test_a_stack_start_skips_an_optional_part_whose_checkout_is_gone(box, tmp_path, monkeypatch):
    # The source-absent site. Red before: rc 1, "[blocked] meshcore-webui: not installed …".
    run = _meshcore(box, tmp_path, monkeypatch, blocked=(), absent=("meshcore-webui",))
    rc, out, launches = run("start", "meshcore@r3")
    assert rc == 0 and _launched(launches, "meshcore-node") == ["r3"], out
    assert "  [skipped] meshcore-webui: not installed — run: lhpc install meshcore — not started" in out, out


def test_an_interactive_optional_service_that_is_not_built_is_skipped(box, tmp_path, monkeypatch):
    # The interactive site: NomadNet (a tickable interactive service) ticked, its source present, not built.
    # Red before: rc 1, "[blocked] nomadnet: interactive but not built …".
    run = box((R1, R2_868), paths=[str(tmp_path / "state" / "reticulum" / "ready")])
    cfgmod.update_stack_config(Paths(runtime_root=tmp_path), "reticulum", {"autostart_nomadnet": "on"},
                               inst=Instance("r2"))
    _blockers(monkeypatch, tmp_path, "reticulum", blocked=("nomadnet",))
    monkeypatch.setattr(Lifecycle, "missing_requirements", lambda self, comp: [])   # the PC has no SPI device
    rc, out, launches = run("start", "reticulum@r2")
    assert "  [skipped] nomadnet: interactive but not built — run: lhpc build reticulum --yes — not started" in out, out
    assert rc == 0 and _launched(launches, "rns") == ["r2"], out


def test_the_detached_web_start_ends_done_and_its_note_carries_the_warning(box, tmp_path, monkeypatch):
    # Red before: the job ended "failed" ("meshcore-webui: not built").
    _meshcore(box, tmp_path, monkeypatch)
    rc, res = _child(tmp_path, "meshcore", "r3")
    assert (rc, res["state"]) == (0, "done"), res
    assert "meshcore-webui: not built — run: lhpc build meshcore --yes — not started" in res["detail"], res


def test_the_web_start_goes_and_the_page_warns_instead_of_requiring_a_build(box, web, csrf, tmp_path, monkeypatch):
    # Red before: the Start flashed "cannot start yet — meshcore-webui: not built …" and the page said
    # "Build required to start" for it.
    _meshcore(box, tmp_path, monkeypatch)
    c = web()
    where, page = _start_post(c, csrf, "meshcore@r3")
    assert "#stack-install-" not in where and "cannot start yet" not in page, (where, page)
    body = c.get("/stacks/meshcore/body").get_data(as_text=True)
    assert "meshcore-webui: not built — run: lhpc build meshcore --yes — meshcore starts without it." in body, body
    assert "Build required to start" not in body and "Install required to start" not in body, body


def test_a_required_part_and_an_optional_one_unbuilt_refuse_on_the_required_one(box, web, csrf, tmp_path, monkeypatch):
    # The mixed case (and the regression guard for a required part): the refusal and the "required" banner name
    # meshcore-node only; the Web UI keeps its own warning.
    run = _meshcore(box, tmp_path, monkeypatch, blocked=("meshcore-node", "meshcore-webui"))
    rc, out, launches = run("start", "meshcore@r3")
    assert rc == 1 and "  [blocked] meshcore-node: not built" in out and _launched(launches, "meshcore-node") == [], out
    c = web()
    _where, page = _start_post(c, csrf, "meshcore@r3")
    assert "cannot start yet — meshcore-node: not built — run: lhpc build meshcore --yes." in page, page
    body = c.get("/stacks/meshcore/body").get_data(as_text=True)
    assert "Build required to start" in body and "meshcore-node: not built" in body, body
    assert "meshcore-webui: not built — run: lhpc build meshcore --yes — meshcore starts without it." in body, body
    assert "meshcore-webui: not built — run: lhpc build meshcore --yes;" not in body \
           and "meshcore-webui: not built — run: lhpc build meshcore --yes." not in body, body   # not in the required one


def test_a_build_provider_still_refuses_the_start(box, web, csrf, tmp_path, monkeypatch):
    # openhop-repeater-src is optional but meshcore-node is built from it: its blocker refuses as before.
    _meshcore(box, tmp_path, monkeypatch, blocked=("openhop-repeater-src",))
    c = web()
    where, page = _start_post(c, csrf, "meshcore@r3")
    assert "cannot start yet — openhop-repeater-src: not built" in page, (where, page)
    body = c.get("/stacks/meshcore/body").get_data(as_text=True)
    assert "openhop-repeater-src" in body and "starts without it" not in body, body


def test_a_direct_start_of_the_optional_part_still_refuses(box, tmp_path, monkeypatch):
    # A direct start has no "rest": the Web UI's own Start ends failed with its blocker, never done with nothing run.
    run = _meshcore(box, tmp_path, monkeypatch)
    rc, out, launches = run("start", "meshcore-webui@r3")
    assert rc != 0 and "  [blocked] meshcore-webui: not built" in out and "[skipped]" not in out, out
    rc, res = _child(tmp_path, "meshcore-webui", "r3")
    assert rc != 0 and res["state"] == "failed" and "meshcore-webui" in res["detail"], res
    assert _launched(launches, "meshcore-webui") == [], launches


def test_only_an_optional_part_no_other_is_built_from_is_skippable():
    stacks = {s.id: s for s in load_manifest()}
    yes = {("meshcore", "meshcore-webui"), ("reticulum", "nomadnet"), ("meshcom", "meshcom-gps-relay"),
           ("kiss", "loraham-kiss-serial")}
    no = {("meshcore", "openhop-repeater-src"), ("daemon", "radiolib"), ("reticulum", "rns-lora-interface"),
          ("meshcore", "meshcore-node"), ("daemon", "loraham-daemon")}
    assert all(stacks[s].skippable(c) for s, c in yes) and not any(stacks[s].skippable(c) for s, c in no)
    assert not any(s.skippable(c.id) for s in stacks.values() for c in s.components if not c.optional)


def test_the_web_start_childs_refusal_is_in_its_job_log_file(tmp_path, monkeypatch):
    # G1 N1 (A2's log-file proof): the real detached child of a Start on a disabled radio writes its refusal into the
    # job log the console's card opens. Red before (fbba5e1a): the log file is empty.
    import radios_fixture
    radios_fixture.write(tmp_path, (radios.Radio("r1", "a", "uputronics", "433", ce="ce0"),
                                    radios.Radio("r3", "b", "uputronics", "433", ce="ce1", enabled=False)))
    monkeypatch.setenv("LHPC_RUNTIME_ROOT", str(tmp_path))
    monkeypatch.setenv("LHPC_WEB_ADMIT_TIMEOUT_S", "60")      # a loaded runner: the handshake returns at the child's end
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    log, admission, reason = svc.spawn_start_job("start", "kiss", rid="r3")    # the real child, its refusal handed back
    assert (log, admission, reason) == (None, "blocked", "kiss@r3: r3 is disabled")
    assert jobresult.read_one(svc._paths, "web-start-kiss-r3.log")["state"] == "failed"
    assert "web start: kiss@r3: r3 is disabled" in (tmp_path / "logs" / "web-start-kiss-r3.log").read_text()
