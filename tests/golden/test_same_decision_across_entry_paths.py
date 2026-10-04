"""Same input, same decision — whichever entry path runs it.

One scenario is driven through every entry path that can start a stack, each on a fresh box:

- `cli`  — `lhpc stack <op> <target> --yes` (`main()`: the plan, then the apply);
- `web`  — `POST /action` on the console: the plan in the route, then the real `spawn_start_job`
           parent, its real tracking and admission handshake, and the `lhpc _stack-start` child
           it spawns as a SEPARATE detached process (its own pid, detached by setsid) through the
           production `Lifecycle._real_spawn` (see `entry`);
- `job`  — the detached job runner alone (`lhpc _stack-start …` over a tracked attempt);
- `boot` — the boot-restore unit (`lhpc autostart --run-service`) replaying a prior boot's record.

Every `lhpc` process a path runs (the CLI, the web's child, the job runner, the boot unit) enters
through `main()` and builds its OWN `ControllerService()` the production way; the box's host (the
FakeSystem, and the manifest a scenario names) reaches it through the production extension point
`LHPC_SYSTEM_PROVIDER` (`entry_host.py`), as a lab host reaches every process. The web parent is
the console's long-lived service (`box.svc`).

The DECISION compared across paths: `started`, the `refusal` class (the typed `data` key where
one exists, `blockers` for a plan that lists owners, `manual_required`/`unverified`/`ok` from the
outcomes, else the deciding result's summary), the `files` the path left under the runtime root
(each path's own bookkeeping — attempt and job markers, the boot journal and its evidence, the
web session key and job logs, lock files — excluded) and the processes the path spawned that
are `live` afterwards (each checked by its pid and start time in /proc, not through the ownership
records). Every path must leave exactly one ownership record of this boot per live process, naming
its pid and start time, and none for a process that is gone (`KissBox.ownership`). The RENDERING (exit code, HTTP status and admission, job state, journal item) is
asserted per path as well. A rendering difference (same decision, different words or format) is
`intended:`; a DECISION difference between paths is always a `known defect <id>:` naming exactly
that difference — never `intended`.

What the web path observes of its child is what a separate process leaves behind: its exit
status (the test is its parent and reaps it), the attempt record and job marker it was tracked
by, the files it wrote, and its outermost start/restart decision (recorded by `entry_host`'s
delegating spy in that process). The job marker the parent wrote must name the child's pid, which
differs from the test process's — a parent that tracked itself instead fails here and at the
child's own gate.
"""

import json
import os
import re
import signal
import subprocess
import sys
import threading
import tomllib
import uuid
from pathlib import Path

import entry_host
import pytest

from lhpc.adapters.cli import main as cli_main
from lhpc.adapters.web.app import create_app
from lhpc.core import config as cfgmod
from lhpc.core import jobresult, jobs, procident
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import CommandResult
from lhpc.core.services import ControllerService

pytestmark = pytest.mark.needs_session

PATHS = ("cli", "web", "job", "boot")
_BOOKKEEPING = re.compile(r"^(state/(jobresults|jobs|owned|locks)/|state/boot-restore\.json$|"
                          r"logs/web-|config/secrets/web_session\.key$|config/\.lock$)")
KISS_STARTED = ["logs/start-loraham-kiss-tnc-433.log", "state/daemon-feed-floor-433",
                "state/running/kiss.band"]


def _files(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*")
            if p.is_file() and not _BOOKKEEPING.match(str(p.relative_to(root)))}


@pytest.fixture
def entry(kiss_box, prior_boot, monkeypatch, csrf, tmp_path):
    """`entry(path, op, target, setup=None, *, evidence=("kiss", "loraham-kiss-tnc", "433"),
    callsign=True)` → {started, refusal, files, live, render, next_commands, root}. `setup(box)`
    prepares the scenario on the path's fresh box (it may set `box.manifest` and replace
    `box.svc`); `evidence` is the prior boot's record the `boot` path replays.

    Substituted for every path (all listed here):
    - the host: `LHPC_SYSTEM_PROVIDER=entry_host:build` serves the current box's FakeSystem and
      manifest, so each process's own `ControllerService()` runs on the box (production reads the
      same variable; unset, it builds the real system) — in a separate process, the box rebuilt
      from its description (`entry_host`);
    - `kiss_box`'s own substitutions (its real TNC process spawn and endpoint reader, the 3 s
      endpoint wait) and `prior_boot`'s (this boot's id through `LHPC_BOOT_ID_FILE`);
    - the spies on `ControllerService.start`/`restart`/`boot_restore_run`/`spawn_start_job`,
      which delegate;
    - the firewall readers `ControllerService._fw_integration_state` and `firewall_status`, set by
      the suite's isolation or by a scenario (`firewall_pending_host`) and, in the child,
      re-applied by `entry_host` from the description.
    For the web path only: the box's Lifecycle spawn (already `kiss_box`'s TNC spawn) hands an
    `lhpc _stack-start …` argv to the production `Lifecycle._real_spawn` — the real
    `subprocess.Popen` of the argv the real `spawn_start_job` built, detached (setsid), its
    output appended to the real job log — keeping the Popen object so the test, its parent,
    waits for it and reaps it (also at teardown). Teardown kills every TNC the child spawned
    (SIGKILL to its process group, only while the pid still has the start time recorded at its
    spawn: a reused pid is left alone); it cannot reap them, as they are not its children: once their
    parent has exited, init (or the nearest subreaper) collects them.
    The parent's admission handshake keeps its production bound, 3 s (`LHPC_WEB_ADMIT_TIMEOUT_S`
    is removed from the environment): a child admitted later renders `pending`, as it would in
    production, and fails the rendering assertion. In the child, `entry_host` re-applies the
    suite's autouse isolation (listed in its docstring). Not covered: the child's stdout is the
    job log, which no assertion reads; the suite's pip and shell guards do not run in the child."""
    decided, booted, web = [], [], {}
    depth = threading.local()

    def _spy(real, into):          # the outermost start/restart result; boot-restore's own
        def wrapper(self, *a, **k):
            n = getattr(depth, "n", 0)
            depth.n = n + (into is decided)
            try:
                res = real(self, *a, **k)
            finally:
                depth.n = n
            if into is booted or n == 0:
                into.append(res)
            return res
        return wrapper
    monkeypatch.setattr(ControllerService, "start", _spy(ControllerService.start, decided))
    monkeypatch.setattr(ControllerService, "restart", _spy(ControllerService.restart, decided))
    monkeypatch.setattr(ControllerService, "boot_restore_run",
                        _spy(ControllerService.boot_restore_run, booted))

    import lhpc
    monkeypatch.setenv("LHPC_SYSTEM_PROVIDER", "entry_host:build")
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join(
        [str(Path(entry_host.__file__).parent), str(Path(lhpc.__file__).parents[1]),
         *filter(None, [os.environ.get("PYTHONPATH")])]))
    monkeypatch.delenv("LHPC_WEB_ADMIT_TIMEOUT_S", raising=False)      # the production 3 s

    children, descriptions = [], []

    def routed(tnc_lifecycle):          # kiss_box's Lifecycle, `lhpc _stack-start` routed out
        def lifecycle(self):
            lc = tnc_lifecycle(self)
            tnc = lc._spawn

            def spawn(argv, log, cwd=None, env=None):
                if argv[1:4] != ["-m", "lhpc", "_stack-start"]:
                    return tnc(argv, log, cwd=cwd, env=env)
                real_popen = subprocess.Popen

                def keep(*a, **k):                         # keep the Popen: the test reaps it
                    web["child"] = real_popen(*a, **k)
                    children.append(web["child"])
                    return web["child"]
                with monkeypatch.context() as m:
                    m.setattr(subprocess, "Popen", keep)
                    return lc._real_spawn(argv, log, cwd=cwd, env=env)
            lc._spawn = spawn
            return lc
        return lifecycle

    real_spawn_start = ControllerService.spawn_start_job

    def spawn_start(self, *a, **k):            # a spy: records the parent's (log, admission, why)
        web["parent"] = real_spawn_start(self, *a, **k)
        return web["parent"]
    monkeypatch.setattr(ControllerService, "spawn_start_job", spawn_start)

    def _run(path, op, target, setup=None, *, evidence=("kiss", "loraham-kiss-tnc", "433"),
             callsign=True):
        box = kiss_box(root=tmp_path / path, callsign=callsign)
        box.manifest = None
        monkeypatch.setattr(ControllerService, "_lifecycle", routed(ControllerService._lifecycle))
        monkeypatch.setattr(entry_host, "LIVE", box)
        monkeypatch.setenv("LHPC_RUNTIME_ROOT", str(box.root))
        if path == "boot":
            prior_boot(box.root, stack=evidence[0], comp=evidence[1], band=evidence[2])
        if setup:
            setup(box)
        box.svc.invalidate_snapshot()
        desc = entry_host.describe(box, tmp_path / f"{path}-host")
        descriptions.append(desc)
        monkeypatch.setenv(entry_host.HOST_ENV, str(desc))
        before = _files(box.root)
        decided.clear()
        booted.clear()
        web.clear()
        web["desc"] = desc
        render, started = _DRIVE[path](box, op, target, web, csrf, monkeypatch)
        after = _files(box.root)
        parent, child = web.get("parent"), web.get("decision")
        if "child" in web:                 # the separate process decided (or refused at its gate)
            assert child is not None, f"the web child decided nothing (rc {render['child_rc']})"
            refusal, told = child["refusal"], child["next_commands"]
        elif parent and parent[1] == "blocked":
            refusal, told = parent[2], []  # the web parent refused: no child was spawned
        elif decided:
            refusal, told = entry_host.classify(decided[-1]), list(decided[-1].next_commands)
        else:                              # boot-restore planned no start at all
            refusal, told = entry_host.classify(booted[-1]), []
        live, owned = box.ownership(also=entry_host.spawned(desc))
        assert owned == live, f"{path}: ownership records {owned} != live processes {live}"
        return {"started": started, "refusal": refusal, "render": render,
                "live": box.live(also=entry_host.spawned(desc)),
                "files": sorted(k for k in before.keys() | after.keys()
                                if before.get(k) != after.get(k)),
                "next_commands": told, "root": box.root}
    yield _run
    for c in children:                     # reap the web children, then kill what they spawned
        if c.poll() is None:
            c.kill()
        c.wait(timeout=10)
    for d in descriptions:                 # each only while it is the process recorded
        for pid, start, _comp in entry_host.spawned(d):
            entry_host.kill_group_if_same(pid, start)


def _job_state(svc, op, target):
    rec = jobresult.read_one(svc._paths, f"web-{op}-{target}.log")
    return None if rec is None else (rec["state"], rec["admitted"])


def _cli(box, op, target, web, csrf, monkeypatch):
    rc = cli_main.main(["stack", op, target, "--yes"])
    return {"rc": rc}, rc == 0


def _job(box, op, target, web, csrf, monkeypatch):
    """The attempt as the web parent leaves it — reserved, its job marker naming the process that
    runs the runner (this one) — then the runner, with the band the parent freezes."""
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
    if "child" in web:                     # the separate process the parent spawned, to its end
        child = web["child"]
        child.wait(timeout=120)
        log = f"web-{op}-{target}.log"
        marker = tomllib.loads((box.root / "state" / "jobs" / f"{log}.job").read_text())
        assert child.pid != os.getpid() and marker["pid"] == child.pid, (
            f"the parent tracked pid {marker['pid']}, the child is {child.pid}")
        web["decision"] = entry_host.read_decision(web["desc"])
    state = _job_state(box.svc, op, target)
    return ({"status": r.status_code, "admission": (web.get("parent") or (0, None))[1],
             "child_rc": web["child"].returncode if "child" in web else None, "job": state},
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
    """kiss saved to listen beyond loopback (the host's firewall state: `firewall_pending_host`)."""
    cfgmod.save_stack_config(box.svc._paths, "kiss", {"kiss_host": "0.0.0.0", "kiss_port": "9001"},
                             box.svc._config_band("kiss", ""))
    box.svc._invalidate_config()


def firewall_pending_host(monkeypatch):
    """The firewall installed but not verified current. The integration state and its check are
    host readers: set for every service of the test (the parent's and each process's own), as a
    host's state is the same for every process on it."""
    monkeypatch.setattr(ControllerService, "_fw_integration_state", lambda self: "present")
    monkeypatch.setattr(ControllerService, "firewall_status",
                        lambda self: {"config_ok": True, "live_ok": False})


PENDING_SAVED = {"kiss_port": "8001", "verbose": "on"}


def _pending_config_journal(box):
    """A Settings save crashed mid-write: kiss.toml torn, its journal (with the pre-image) left.
    The pre-image holds a non-default setting, so only its restore can bring it back."""
    f = box.root / "config" / "stacks" / "kiss.toml"
    cfgmod.save_stack_config(box.svc._paths, "kiss", PENDING_SAVED, "")
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
    box.manifest = DATA / "scope2_manifest.toml"
    box.svc = ControllerService(manifest_path=box.manifest, system=box.fake.system,
                                paths=Paths(runtime_root=box.root))
    cfgmod.update_stack_config(box.svc._paths, "ostack2", {"rp": "LEGACY"})



# --- the decisions -----------------------------------------------------------------------------

REFUSED_NOTHING = {"started": False, "files": [], "live": []}
SUCCEEDED = {"cli": {"rc": 0},
             "web": {"status": 302, "admission": "admitted", "child_rc": 0, "job": ("done", True)},
             "job": {"rc": 0, "job": ("done", True)},
             "boot": {"rc": 0, "journal": "done", "items": ["succeeded"], "skipped": []}}
FAILED_AFTER_ADMISSION = {
    "cli": {"rc": 1},
    "web": {"status": 302, "admission": "admitted", "child_rc": 1, "job": ("failed", True)},
    "job": {"rc": 1, "job": ("failed", True)},
    "boot": {"rc": 0, "journal": "failed", "items": ["failed"], "skipped": []}}


def test_success(entry):
    """intended: a clean start succeeds on every path with the same files and the TNC live."""
    res = _all(entry, "start", "kiss")
    _same(res, started=True, refusal="ok", files=KISS_STARTED, live=["loraham-kiss-tnc"])
    assert _renders(res) == SUCCEEDED


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
    """known defect T3-F4: a running band owner stops every path, with nothing written, but not
    with the same decision. T3-F4 (refusal class): the web stops at its plan's `blockers` listing
    (the confirm page) while the CLI, the job runner and boot-restore refuse in the apply's
    preflight with "Cannot run 'kiss': meshtastic must be stopped first."."""
    res = _all(entry, "start", "kiss", _band_owner)
    refusal = "Cannot run 'kiss': meshtastic must be stopped first."
    assert _decisions(res) == {
        "cli": {"refusal": refusal, **REFUSED_NOTHING},
        "web": {"refusal": "blockers", **REFUSED_NOTHING},
        "job": {"refusal": refusal, **REFUSED_NOTHING},
        "boot": {"refusal": refusal, **REFUSED_NOTHING}}
    assert _renders(res) == {
        "cli": {"rc": 1},
        "web": {"status": 200, "admission": None, "child_rc": None, "job": None},
        "job": {"rc": 1, "job": ("failed", True)},
        "boot": {"rc": 0, "journal": "failed", "items": ["failed"], "skipped": []}}


def test_firewall_gate(entry, monkeypatch):
    """intended: the firewall gate refuses on every path with the same class. The CLI and the
    web refuse at their read-only plan (render=False): the script on disk is stale, so their
    commands re-render it before the apply lines; the job runner and boot-restore refuse at the
    apply, which re-renders it itself (config/files/firewall/firewall-apply.sh)."""
    firewall_pending_host(monkeypatch)
    res = _all(entry, "start", "kiss", _firewall_pending)
    applied = {"started": False, "live": [], "refusal": "firewall_gate",
               "files": ["config/files/firewall/firewall-apply.sh"]}
    assert _decisions(res) == {"cli": {"refusal": "firewall_gate", **REFUSED_NOTHING},
                               "web": {"refusal": "firewall_gate", **REFUSED_NOTHING},
                               "job": applied, "boot": applied}
    assert _renders(res) == {
        "cli": {"rc": 1},
        "web": {"status": 302, "admission": None, "child_rc": None, "job": None},
        "job": {"rc": 1, "job": ("failed", True)},
        "boot": {"rc": 0, "journal": "failed", "items": ["failed"], "skipped": []}}
    exposed = '"id": "loraham-kiss-tnc.tcp-8001", "port": 9001'
    for path in PATHS:
        script = res[path]["root"] / "config" / "files" / "firewall" / "firewall-apply.sh"
        assert f"sudo bash {script}" in res[path]["next_commands"]
        rerender = res[path]["next_commands"][0] == "lhpc firewall --script > /dev/null"
        assert rerender is (path in ("cli", "web")), path
        assert (exposed in script.read_text()) is (path in ("job", "boot")), path


def test_config_ambiguity(entry):
    """intended: an ambiguous flat value refuses the start on every path with nothing written —
    at the plan (CLI, web) or the apply's preflight (job, boot-restore, after the hook: the boot
    item is consumed as failed)."""
    res = _all(entry, "start", "ostack2", _ambiguous, evidence=("ostack2", "tgt", "433"))
    _same(res, refusal="Cannot start 'ostack2': run parameter 'rp' is ambiguous — declared by "
                       "more than one component and stored only as a flat value; set a "
                       "component-scoped value for 'dep'", **REFUSED_NOTHING)
    assert _renders(res) == {**FAILED_AFTER_ADMISSION, "web": {
        "status": 302, "admission": None, "child_rc": None, "job": None}}


def test_tx_without_high_power_permission(entry):
    """intended: a stack saved to transmit at POWER=20 without the running daemon's high-power
    permission is BLOCKED on every path (no path's plan checks it; each apply does), nothing
    spawned."""
    res = _all(entry, "start", "kiss", _tx_without_permission)
    _same(res, started=False, refusal="blocked", files=["state/daemon-feed-floor-433"], live=[])
    assert _renders(res) == FAILED_AFTER_ADMISSION


def test_pending_config_journal(entry):
    """intended: a config journal a crashed save left behind is finished by every path's process
    at startup (the CLI, the web's child, the job runner, the boot unit) — the torn file restored
    to its pre-image (its non-default setting read back), the journal gone — and the start then
    succeeds alike."""
    res = _all(entry, "start", "kiss", _pending_config_journal)
    _same(res, started=True, refusal="ok", live=["loraham-kiss-tnc"],
          files=sorted(["config/stacks/kiss.toml", "state/config-txn.json", *KISS_STARTED]))
    assert _renders(res) == SUCCEEDED
    for r in res.values():
        assert not (r["root"] / "state" / "config-txn.json").exists()
        assert cfgmod.load_stack_config(Paths(runtime_root=r["root"]), "kiss") == PENDING_SAVED


def test_damaged_client_index(entry):
    """intended: a damaged client-certificate index gates certificate operations only — no start
    path decides on it, so every path starts alike."""
    res = _all(entry, "start", "kiss", _damaged_client_index)
    _same(res, started=True, refusal="ok", files=KISS_STARTED, live=["loraham-kiss-tnc"])
    assert _renders(res) == SUCCEEDED


def test_interactive_launch(entry):
    """intended: an interactive main is prepared on every path — config generated, dashboard
    marker set, never spawned — and its only shortfall, MANUAL_REQUIRED, counts as success (exit
    0, job done, boot item succeeded). Boot-restore plans it from the record its last start left:
    the daemon that start ensured, recorded under the stack (the main has none of its own)."""
    res = _all(entry, "start", "chat", _chat_installed,
               evidence=("chat", "loraham-daemon", "433"))
    _same(res, started=True, refusal="manual_required", live=[],
          files=["config/files/lorachat.conf", "state/daemon-feed-floor-433",
                 "state/interactive/chat.show"])
    assert _renders(res) == SUCCEEDED


def test_unverified_termination(entry):
    """intended: a TNC that is alive but never opens its endpoint is cleaned up and reported
    UNVERIFIED on every path; each renders it as a failure — the CLI and the web's child exit 1,
    the web and job attempts end `failed` (admitted), the boot item `failed` (the boot unit itself
    exits 0: its driver completed)."""
    res = _all(entry, "start", "kiss", lambda b: b.endpoint_never_up())
    _same(res, started=False, refusal="unverified", live=[],
          files=["logs/start-loraham-kiss-tnc-433.log", "state/daemon-feed-floor-433"])
    assert _renders(res) == FAILED_AFTER_ADMISSION


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


def test_teardown_signals_only_the_process_it_started():
    """intended: the harness's own guard — the teardown's group kill signals a recorded pid only
    while it carries the recorded start time — a pid now naming another process is left alive."""
    p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"],
                         start_new_session=True)
    try:
        start = str(procident.proc_identity(p.pid)["starttime"])
        assert entry_host.kill_group_if_same(p.pid, str(int(start) + 1)) is False
        with pytest.raises(subprocess.TimeoutExpired):
            p.wait(timeout=0.5)
        assert entry_host.kill_group_if_same(p.pid, start) is True
        assert p.wait(timeout=10) == -signal.SIGKILL
    finally:
        if p.poll() is None:
            p.kill()
            p.wait(timeout=10)
