"""Same input, same decision — whichever entry path runs it.

One scenario is driven through every entry path that can start a stack, each on a fresh box:

- `cli`  — `lhpc stack <op> <target> --yes` (`main()`: the plan, then the apply);
- `web`  — `POST /action` on the console: the plan in the route, then the real `spawn_start_job`
           parent and handshake, with the spawned `lhpc _stack-start` child run here in-process;
- `job`  — the detached job runner alone (`lhpc _stack-start …` over a tracked attempt);
- `boot` — the boot-restore unit (`lhpc autostart --run-service`) replaying a prior boot's record.

The DECISION compared across paths: `started`, the `refusal` class (the typed `data` key where
one exists, `blockers` for a plan that lists owners, `manual_required`/`unverified`/`ok` from the
outcomes, else the deciding result's summary), the `files` the path left under the runtime root
(each path's own bookkeeping — attempt and job markers, the boot journal and its evidence, the
web session key and job logs, lock files — excluded) and the LHPC-owned processes `live`
afterwards. Only the rendering may differ — exit code, flash or confirm page, job state, journal
item — and each path's rendering is asserted as well.

Every case's docstring opens with `intended:` or `known defect <id>:` (tests/README.md, `golden/`).
"""

import json
import os
import re
import uuid

import pytest

from lhpc.adapters.cli import main as cli_main
from lhpc.adapters.web.app import create_app
from lhpc.core import config as cfgmod
from lhpc.core import jobresult, jobs, procident
from lhpc.core.lifecycle import Lifecycle
from lhpc.core.outcomes import manual_required_only
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import CommandResult
from lhpc.core.services import ControllerService

pytestmark = pytest.mark.needs_session

PATHS = ("cli", "web", "job", "boot")
_BOOKKEEPING = re.compile(r"^(state/(jobresults|jobs|owned|locks)/|state/boot-restore\.json$|"
                          r"logs/web-|config/secrets/web_session\.key$|config/\.lock$)")
_TYPED = ("admission_blocked", "enforce_fields", "firewall_gate", "reason")
KISS_STARTED = ["logs/start-loraham-kiss-tnc-433.log", "state/daemon-feed-floor-433",
                "state/running/kiss.band"]


def _classify(res):
    """The refusal class of the result that decided."""
    data = res.data or {}
    for key in _TYPED:
        if data.get(key):
            return key
    if data.get("blockers"):
        return "blockers"
    if res.ok:
        return "ok"
    if res.results and manual_required_only(res.results):
        return "manual_required"
    for outcome in ("unverified", "blocked"):
        if any(r.outcome.value == outcome for r in res.results):
            return outcome
    return res.summary


def _files(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*")
            if p.is_file() and not _BOOKKEEPING.match(str(p.relative_to(root)))}


@pytest.fixture
def entry(kiss_box, prior_boot, monkeypatch, csrf, tmp_path):
    """`entry(path, op, target, setup=None, *, evidence=("kiss", "loraham-kiss-tnc", "433"),
    callsign=True)` → {started, refusal, files, live, render}. `setup(box)` prepares the scenario
    on the path's fresh box (it may replace `box.svc`); `evidence` is the prior boot's record the
    `boot` path replays."""
    decided, booted, web = [], [], {}
    depth = [0]

    def _spy(real, into):          # the outermost start/restart result; boot-restore's own
        def wrapper(self, *a, **k):
            depth[0] += into is decided
            try:
                res = real(self, *a, **k)
            finally:
                depth[0] -= into is decided
            if into is booted or depth[0] == 0:
                into.append(res)
            return res
        return wrapper
    monkeypatch.setattr(ControllerService, "start", _spy(ControllerService.start, decided))
    monkeypatch.setattr(ControllerService, "restart", _spy(ControllerService.restart, decided))
    monkeypatch.setattr(ControllerService, "boot_restore_run",
                        _spy(ControllerService.boot_restore_run, booted))

    # web: the parent spawns "this process"; its handshake runs the child it would have spawned
    real_handshake, real_spawn_start = (ControllerService._web_admit_handshake,
                                        ControllerService.spawn_start_job)

    def spawn_job(self, name, argv, cwd, env=None):
        web["argv"] = argv
        return f"{name}.log", os.getpid()

    def handshake(self, log, aid):
        web["child_rc"] = cli_main.main(web["argv"][3:])
        return real_handshake(self, log, aid)

    def spawn_start(self, *a, **k):
        web["parent"] = real_spawn_start(self, *a, **k)
        return web["parent"]
    monkeypatch.setattr(Lifecycle, "spawn_job", spawn_job)
    monkeypatch.setattr(ControllerService, "_web_admit_handshake", handshake)
    monkeypatch.setattr(ControllerService, "spawn_start_job", spawn_start)
    monkeypatch.setenv("LHPC_WEBJOB_GATE_TIMEOUT_S", "2")
    monkeypatch.setenv("LHPC_WEB_ADMIT_TIMEOUT_S", "2")

    def _run(path, op, target, setup=None, *, evidence=("kiss", "loraham-kiss-tnc", "433"),
             callsign=True):
        box = kiss_box(root=tmp_path / path, callsign=callsign)
        monkeypatch.setenv("LHPC_RUNTIME_ROOT", str(box.root))
        monkeypatch.setattr(cli_main, "ControllerService", lambda: box.svc)
        if path == "boot":
            prior_boot(box.root, stack=evidence[0], comp=evidence[1], band=evidence[2])
        if setup:
            setup(box)
        box.svc.invalidate_snapshot()
        before = _files(box.root)
        decided.clear()
        booted.clear()
        web.clear()
        render, started = _DRIVE[path](box, op, target, web, csrf, monkeypatch)
        after = _files(box.root)
        parent = web.get("parent")
        if parent and parent[1] == "blocked" and "child_rc" not in web:
            refusal = parent[2]            # the web parent refused: no child ever decided
        elif decided:
            refusal = _classify(decided[-1])
        else:                              # boot-restore planned no start at all
            refusal = _classify(booted[-1])
        told = list(decided[-1].next_commands) if decided else []
        return {"started": started, "refusal": refusal, "live": box.live(), "render": render,
                "files": sorted(k for k in before.keys() | after.keys()
                                if before.get(k) != after.get(k)),
                "next_commands": told, "root": box.root}
    return _run


def _job_state(svc, op, target):
    rec = jobresult.read_one(svc._paths, f"web-{op}-{target}.log")
    return None if rec is None else (rec["state"], rec["admitted"])


def _cli(box, op, target, web, csrf, monkeypatch):
    rc = cli_main.main(["stack", op, target, "--yes"])
    return {"rc": rc}, rc == 0


def _job(box, op, target, web, csrf, monkeypatch):
    """The attempt as the web parent leaves it — reserved, its job marker naming this process
    (the in-process child) — then the runner, with the band the parent freezes."""
    svc = box.svc
    log, aid = f"web-{op}-{target}.log", uuid.uuid4().hex
    assert jobresult.reserve(svc._paths, log, aid, op, target, svc.stack_of(target) or target, [])
    assert jobs.write_job_marker(svc._paths, log, os.getpid(), target, op,
                                 ident=procident.proc_identity(os.getpid()), attempt_id=aid)
    argv = ["_stack-start", target, "--web-result", log, "--attempt-id", aid]
    band = svc.operation_band(target, "")
    argv += (["--band", band] if band else []) + (["--restart"] if op == "restart" else [])
    rc = cli_main.main(argv)
    state = _job_state(svc, op, target)
    return {"rc": rc, "job": state}, state[0] == "done"


def _boot(box, op, target, web, csrf, monkeypatch):
    monkeypatch.setenv("INVOCATION_ID", "golden")
    rc = cli_main.main(["autostart", "--run-service"])
    p = box.root / "state" / "boot-restore.json"
    j = json.loads(p.read_text()) if p.exists() else {"state": None, "items": [], "skipped": []}
    items = [i["state"] for i in j["items"]]
    return ({"rc": rc, "journal": j["state"], "items": items,
             "skipped": [s["reason"] for s in j.get("skipped", [])]}, items == ["succeeded"])


def _web(box, op, target, web, csrf, monkeypatch):
    client = create_app(service_factory=lambda: box.svc).test_client()
    r = client.post("/action", data={"_csrf": csrf(client, "/"), "op": op, "target": target,
                                     "from": "dash"})
    state = _job_state(box.svc, op, target)
    return ({"status": r.status_code, "admission": (web.get("parent") or (0, None))[1],
             "child_rc": web.get("child_rc"), "job": state},
            bool(state and state[0] == "done"))


_DRIVE = {"cli": _cli, "web": _web, "job": _job, "boot": _boot}


def _all(entry, op, target, setup=None, **kw):
    paths = PATHS if op == "start" else PATHS[:3]          # boot-restore only ever starts
    return {p: entry(p, op, target, setup, **kw) for p in paths}


def _decisions(results):
    return {p: {k: r[k] for k in ("started", "refusal", "files", "live")}
            for p, r in results.items()}


def _same(results, **decision):
    """Every path decided exactly `decision`."""
    assert _decisions(results) == {p: decision for p in results}


def _renders(results):
    return {p: r["render"] for p, r in results.items()}


# --- scenario set-ups --------------------------------------------------------------------------

class _EveryStepSucceeds(dict):
    def get(self, argv, default=None):
        return CommandResult(0, "", "")


def _graywolf_built(box):
    """graywolf — a licensed stack, its callsign enforced — built by the real build with every
    step answered rc 0, so the console's installed/built pre-check passes like the others."""
    commands, box.fake.commands = box.fake.commands, _EveryStepSucceeds()
    assert box.svc.build("graywolf", apply=True).ok
    box.fake.commands = commands


def _chat_installed(box):
    (box.root / "src" / "LoRaHAM_Daemon").mkdir(parents=True)
    (box.root / "src" / "LoRaHAM_Daemon" / "loraham_chat").write_text("#bin")


def _band_owner(box):
    box.fake.cmdlines_data[300] = ["meshtasticd"]


def _firewall_pending(box):
    """kiss saved to listen beyond loopback; the firewall installed but not verified current
    (the host's integration state and its check are host readers — set on this box only)."""
    cfgmod.save_stack_config(box.svc._paths, "kiss", {"kiss_host": "0.0.0.0", "kiss_port": "9001"},
                             box.svc._config_band("kiss", ""))
    box.svc._invalidate_config()
    box.svc._fw_integration_state = lambda: "present"
    box.svc.firewall_status = lambda: {"config_ok": True, "live_ok": False}


def _pending_config_journal(box):
    """A Settings save crashed mid-write: kiss.toml torn, its journal (with the pre-image) left."""
    f = box.root / "config" / "stacks" / "kiss.toml"
    cfgmod.save_stack_config(box.svc._paths, "kiss", {"kiss_port": "8001"}, "")
    pre = f.read_text()
    f.write_text("# torn\n")
    (box.root / "state" / "config-txn.json").write_text(json.dumps({"version": 1, "targets": [
        {"kind": "stack", "rel": "config/stacks/kiss.toml", "pre": pre, "existed": True,
         "mode": 0o644}]}))


def _damaged_client_index(box):
    p = box.root / "config" / "tls" / "client-ca" / "client-index.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"{not json")


def _tx_without_permission(box):
    """kiss saved to transmit at POWER=20 (the switch saved on) while the running daemon does
    not report the high-power permission — the explicit TX authorisation the start gates on."""
    assert box.svc.save_config_bundle("daemon", values={"hipower_433": "on"}).ok
    cfgmod.update_stack_config(box.svc._paths, "kiss", {"dp_433_POWER": "20"})
    box.svc._invalidate_config()


def _ambiguous(box):
    """The two-component test manifest, with a flat legacy value both components declare."""
    from repo_paths import DATA
    box.svc = ControllerService(manifest_path=DATA / "scope2_manifest.toml",
                                system=box.fake.system, paths=Paths(runtime_root=box.root))
    cfgmod.update_stack_config(box.svc._paths, "ostack2", {"rp": "LEGACY"})



# --- the decisions -----------------------------------------------------------------------------

REFUSED_NOTHING = {"started": False, "files": [], "live": []}


def test_success(entry):
    """intended: a clean start succeeds on every path with the same files and the TNC live."""
    res = _all(entry, "start", "kiss")
    _same(res, started=True, refusal="ok", files=KISS_STARTED, live=["loraham-kiss-tnc"])
    assert _renders(res) == {
        "cli": {"rc": 0},
        "web": {"status": 302, "admission": "admitted", "child_rc": 0, "job": ("done", True)},
        "job": {"rc": 0, "job": ("done", True)},
        "boot": {"rc": 0, "journal": "done", "items": ["succeeded"], "skipped": []}}


def test_missing_identity(entry):
    """intended: a licensed stack with no callsign is refused by every path before anything is
    written — the CLI and the web at their plan, the job and boot-restore at the apply's recheck,
    before the hook (the attempt is not admitted, the boot item stays pending)."""
    res = _all(entry, "start", "graywolf", _graywolf_built, callsign=False,
               evidence=("graywolf", "graywolf", "433"))
    _same(res, refusal="enforce_fields", **REFUSED_NOTHING)
    assert _renders(res) == {
        "cli": {"rc": 1},
        "web": {"status": 302, "admission": None, "child_rc": None, "job": None},
        "job": {"rc": 1, "job": ("failed", False)},
        "boot": {"rc": 0, "journal": "failed", "items": ["pending"], "skipped": []}}


def test_admission_refusal(entry, uninstall_guard):
    """known defect T3-F1: every path refuses with nothing written, but the web parent's refusal
    (`spawn_start_job` → `(None, "blocked", reason)`) carries the reason text only — the typed
    class `admission_blocked` the CLI, the job runner and boot-restore return is lost there."""
    res = _all(entry, "start", "kiss", lambda b: uninstall_guard(b.root))
    reason = ("A controller uninstall is in progress (.lhpc-uninstalling) — refusing to start "
              "new work. Let it finish, or recover it.")
    assert _decisions(res) == {
        "cli": {"refusal": "admission_blocked", **REFUSED_NOTHING},
        "web": {"refusal": reason, **REFUSED_NOTHING},
        "job": {"refusal": "admission_blocked", **REFUSED_NOTHING},
        "boot": {"refusal": "admission_blocked", **REFUSED_NOTHING}}
    assert _renders(res) == {
        "cli": {"rc": 1},
        "web": {"status": 302, "admission": "blocked", "child_rc": None, "job": None},
        "job": {"rc": 1, "job": ("failed", False)},
        "boot": {"rc": 1, "journal": None, "items": [], "skipped": []}}


def test_interrupted_install(entry, interrupted_install):
    """intended: an unresolved source-transaction journal refuses the start on every path, under
    the index lock, before the hook — nothing written (the boot item stays pending)."""
    res = _all(entry, "start", "kiss", lambda b: interrupted_install(b.root))
    _same(res, refusal="Cannot start 'kiss': an unresolved source-transaction journal is present "
                       "— resolve it before starting", **REFUSED_NOTHING)
    assert _renders(res) == {
        "cli": {"rc": 1},
        "web": {"status": 302, "admission": "blocked", "child_rc": 1, "job": ("failed", False)},
        "job": {"rc": 1, "job": ("failed", False)},
        "boot": {"rc": 0, "journal": "failed", "items": ["pending"], "skipped": []}}


def test_band_owner(entry):
    """known defect T1-F1: a running band owner stops every path; the web asks first (the confirm
    page, nothing written) while the CLI, the job runner and boot-restore refuse in the apply's
    preflight AFTER resetting the band's feed floor — the same input leaves different files."""
    res = _all(entry, "start", "kiss", _band_owner)
    refusal = "Cannot run 'kiss': meshtastic must be stopped first."
    floor = {"started": False, "files": ["state/daemon-feed-floor-433"], "live": []}
    assert _decisions(res) == {
        "cli": {"refusal": refusal, **floor},
        "web": {"refusal": "blockers", **REFUSED_NOTHING},
        "job": {"refusal": refusal, **floor},
        "boot": {"refusal": refusal, **floor}}
    assert _renders(res)["web"] == {"status": 200, "admission": None, "child_rc": None,
                                    "job": None}


def test_firewall_gate(entry):
    """known defect T3-F2: the firewall gate refuses on every path, but the CLI and the web
    refuse at their plan (render=False) and name the apply script as the remedy WITHOUT
    re-rendering it — the script they point at is stale and does not carry the newly exposed
    listener — while the job runner and boot-restore refuse at the apply, which re-renders it
    (and, T1-F1, has reset the feed floor): the same input leaves different files."""
    res = _all(entry, "start", "kiss", _firewall_pending)
    applied = {"started": False, "live": [], "refusal": "firewall_gate",
               "files": ["config/files/firewall/firewall-apply.sh", "state/daemon-feed-floor-433"]}
    assert _decisions(res) == {"cli": {"refusal": "firewall_gate", **REFUSED_NOTHING},
                               "web": {"refusal": "firewall_gate", **REFUSED_NOTHING},
                               "job": applied, "boot": applied}
    exposed = '"id": "loraham-kiss-tnc.tcp-8001", "port": 9001'
    for path in PATHS:
        script = res[path]["root"] / "config" / "files" / "firewall" / "firewall-apply.sh"
        assert f"sudo bash {script}" in res[path]["next_commands"]
        assert (exposed in script.read_text()) is (path in ("job", "boot")), path


def test_config_ambiguity(entry):
    """intended: an ambiguous flat value refuses the start on every path with nothing written —
    at the plan (CLI, web) or the apply's preflight (job, boot-restore, after the hook: the boot
    item is consumed as failed)."""
    res = _all(entry, "start", "ostack2", _ambiguous, evidence=("ostack2", "tgt", "433"))
    _same(res, refusal="Cannot start 'ostack2': run parameter 'rp' is ambiguous — declared by "
                       "more than one component and stored only as a flat value; set a "
                       "component-scoped value for 'dep'", **REFUSED_NOTHING)
    assert _renders(res)["boot"] == {"rc": 0, "journal": "failed", "items": ["failed"],
                                     "skipped": []}


def test_tx_without_high_power_permission(entry):
    """intended: a stack saved to transmit at POWER=20 without the running daemon's high-power
    permission is BLOCKED on every path (no path's plan checks it; each apply does), nothing
    spawned."""
    res = _all(entry, "start", "kiss", _tx_without_permission)
    _same(res, started=False, refusal="blocked", files=["state/daemon-feed-floor-433"], live=[])


def test_pending_config_journal(entry):
    """intended: a config journal a crashed save left behind is finished by every path's process
    at startup (the CLI, the web's child, the job runner, the boot unit) — the torn file restored,
    the journal gone — and the start then succeeds alike."""
    res = _all(entry, "start", "kiss", _pending_config_journal)
    _same(res, started=True, refusal="ok", live=["loraham-kiss-tnc"],
          files=sorted(["config/stacks/kiss.toml", "state/config-txn.json", *KISS_STARTED]))
    for r in res.values():
        assert not (r["root"] / "state" / "config-txn.json").exists()
        assert "# torn" not in (r["root"] / "config" / "stacks" / "kiss.toml").read_text()


def test_damaged_client_index(entry):
    """intended: a damaged client-certificate index gates certificate operations only — no start
    path decides on it, so every path starts alike."""
    res = _all(entry, "start", "kiss", _damaged_client_index)
    _same(res, started=True, refusal="ok", files=KISS_STARTED, live=["loraham-kiss-tnc"])


def test_interactive_launch(entry):
    """intended: an interactive main is never spawned; the CLI, the web and the job runner count
    a start whose only shortfall is MANUAL_REQUIRED as success (exit 0, job done), with the same
    files. Boot-restore never replays an interactive main: its plan skips it, by design."""
    res = _all(entry, "start", "chat", _chat_installed,
               evidence=("chat", "loraham-chat", "433"))
    manual = {"started": True, "refusal": "manual_required", "live": [],
              "files": ["config/files/lorachat.conf", "state/daemon-feed-floor-433",
                        "state/interactive/chat.show"]}
    assert _decisions(res) == {"cli": manual, "web": manual, "job": manual,
                               "boot": {"started": False, "refusal": "ok", "files": [],
                                        "live": []}}
    assert _renders(res) == {
        "cli": {"rc": 0},
        "web": {"status": 302, "admission": "admitted", "child_rc": 0, "job": ("done", True)},
        "job": {"rc": 0, "job": ("done", True)},
        "boot": {"rc": 0, "journal": "no-plan", "items": [],
                 "skipped": ["interactive main — manual start"]}}


def test_unverified_termination(entry):
    """intended: a TNC whose endpoint never comes up is cleaned up and reported UNVERIFIED on
    every path — a failure everywhere (exit 1, job failed, boot item failed)."""
    res = _all(entry, "start", "kiss", lambda b: b.endpoint_never_up())
    _same(res, started=False, refusal="unverified", live=[],
          files=["logs/start-loraham-kiss-tnc-433.log", "state/daemon-feed-floor-433"])
    assert {p: r["render"].get("rc") for p, r in res.items()} == {
        "cli": 1, "web": None, "job": 1, "boot": 0}
    assert res["web"]["render"]["job"] == ("failed", True)
    assert res["boot"]["render"]["items"] == ["failed"]


def test_restart_of_an_interactive_stack(entry):
    """intended: for a restart the CLI, the web and the job runner agree that a MANUAL_REQUIRED
    result is a failure (exit 1, job failed) — unlike a start (above), where all three count it
    as success. Boot-restore never restarts."""
    res = _all(entry, "restart", "chat", _chat_installed)
    _same(res, started=False, refusal="manual_required", live=[],
          files=["config/files/lorachat.conf", "state/daemon-feed-floor-433",
                 "state/interactive/chat.show"])
    assert _renders(res) == {
        "cli": {"rc": 1},
        "web": {"status": 302, "admission": "admitted", "child_rc": 1, "job": ("failed", True)},
        "job": {"rc": 1, "job": ("failed", True)}}
