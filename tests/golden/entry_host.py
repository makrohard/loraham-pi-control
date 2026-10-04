"""The host provider of the entry-path harness (`test_same_decision_across_entry_paths.py`).

`LHPC_SYSTEM_PROVIDER=entry_host:build` names it for every `lhpc` process a path runs; it hands
each one the current box's host the way a lab host reaches every process:

- in the test process (the CLI, the job runner and the boot unit run there, through `main()`):
  the live box itself (`LIVE`) — its FakeSystem and the manifest the scenario names;
- in a SEPARATE process (the web path's detached `lhpc _stack-start` child, spawned by the
  production `Lifecycle._real_spawn`): the same box rebuilt from the description the test wrote
  (`describe`, named by `$GOLDEN_ENTRY_HOST`) — a FakeSystem with the box's daemon reply and band
  owners, the box's TNC endpoint reader, and `wrap_spawn` launching the box's TNC process (the
  substitution `kiss_box` makes in the test process). A fixture's patch does not cross a
  process boundary, so `_install` sets in that process what the test process has from the
  suite's autouse fixtures (tests/conftest.py) and `kiss_box`, with the test process's values:
  the firewall readers (`_fw_integration_state`, `_fw_units_enabled`, `firewall.RECEIPT_PATH`,
  and a scenario's `firewall_status`), `updater_units._SYSTEM_ROOTS`, `config.HW_DEFAULT`,
  `display_available`, the three `RealProcFs` host reads, `gps.local_gpsd_listening`,
  `read_kernel_time_state`, the binary-channel download refusal, and the bounded waits
  (`ENDPOINT_VERIFY_TIMEOUT_S`, `DAEMON_VERIFY_TIMEOUT_S`, `Lifecycle.OBSERVE_TIMEOUT_S`) — plus
  a recorder of its outermost start/restart decision (a delegating spy, as in the test
  process). Not replicated: the pip and shell guards (checks that fail a test, not host state).
  Every TNC it spawns is appended — pid, start time, component — to the description's `pids`
  file, so the test checks those processes' liveness directly and kills them at teardown — each
  only while it is still the recorded process (`kill_group_if_same`).
"""

import json
import os
import sys
import types
from pathlib import Path

HOST_ENV = "GOLDEN_ENTRY_HOST"
LIVE = None                      # the test process's current box (set by the harness)
_TYPED = ("admission_blocked", "enforce_fields", "firewall_gate", "reason")


def classify(res):
    """The refusal class of the result that decided."""
    from lhpc.core.outcomes import manual_required_only
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


def describe(box, out_dir: Path) -> Path:
    """Write the box's host description for a separate process; returns its path."""
    from lhpc.core import config as cfgmod
    from lhpc.core import firewall as fwm
    from lhpc.core import updater_units
    from lhpc.core.lifecycle import Lifecycle
    from lhpc.core.services import ControllerService
    out_dir.mkdir(parents=True, exist_ok=True)
    fw = ControllerService._fw_integration_state(box.svc)
    desc = {"root": str(box.root), "port": box.port, "listens": box.listens, "term": box.term,
            "cmdlines": {str(k): v for k, v in box.fake.cmdlines_data.items()},
            "manifest": str(box.manifest) if box.manifest else None,
            "waits": {"endpoint": ControllerService.ENDPOINT_VERIFY_TIMEOUT_S,
                      "daemon": ControllerService.DAEMON_VERIFY_TIMEOUT_S,
                      "observe": Lifecycle.OBSERVE_TIMEOUT_S},
            "hw_default": cfgmod.HW_DEFAULT,
            "fw_integration": fw, "fw_receipt": fwm.RECEIPT_PATH,
            "fw_status": ControllerService.firewall_status(box.svc) if fw == "present" else None,
            "systemd_roots": [str(r) for r in updater_units._SYSTEM_ROOTS],
            "record": str(out_dir / "decision.json"), "pids": str(out_dir / "pids")}
    path = out_dir / "host.json"
    path.write_text(json.dumps(desc))
    return path


def read_decision(desc_path: Path):
    """The separate process's recorded decision, or None when it decided nothing."""
    rec = Path(json.loads(Path(desc_path).read_text())["record"])
    return json.loads(rec.read_text()) if rec.exists() else None


def spawned(desc_path: Path) -> list[tuple]:
    """(pid, start time, component) of every process the separate process spawned."""
    p = Path(json.loads(Path(desc_path).read_text())["pids"])
    return [tuple(json.loads(x)) for x in p.read_text().splitlines()] if p.exists() else []


def kill_group_if_same(pid: int, start) -> bool:
    """SIGKILL the process group `pid` leads, but only while `pid` is still the process a test
    started: alive, with the /proc start time `start` it was recorded with, and the leader of
    its own group. A pid (or group id) since reused by another process is never signalled.
    True when it signalled."""
    import signal
    golden = _golden()
    if start is None or not golden._alive(pid) or golden._starttime(pid) != start:
        return False
    try:
        if os.getpgid(pid) != pid:
            return False
        os.killpg(pid, signal.SIGKILL)
    except OSError:
        return False
    return True


def build(paths):
    if LIVE is not None:
        return types.SimpleNamespace(system=LIVE.fake.system, manifest_path=LIVE.manifest,
                                     wrap_spawn=None)
    return _rebuilt(json.loads(Path(os.environ[HOST_ENV]).read_text()))


def _golden():
    """The golden set's own box definitions (tests/golden/conftest.py), loaded by path."""
    import importlib.util
    mod = sys.modules.get("golden_conftest")
    if mod is None:
        spec = importlib.util.spec_from_file_location("golden_conftest",
                                                      Path(__file__).with_name("conftest.py"))
        mod = importlib.util.module_from_spec(spec)
        sys.modules["golden_conftest"] = mod
        spec.loader.exec_module(mod)
    return mod


_installed: list = []


def _install(d):
    """The separate process's patches — once per process."""
    if _installed:
        return
    _installed.append(True)
    from lhpc.core import binary_install, gps, service_system, updater_units
    from lhpc.core import config as cfgmod
    from lhpc.core import firewall as fwm
    from lhpc.core.lifecycle import Lifecycle
    from lhpc.core.probes import backends
    from lhpc.core.services import ControllerService
    ControllerService._fw_integration_state = lambda self: d["fw_integration"]
    ControllerService._fw_units_enabled = lambda self: False
    if d["fw_status"] is not None:
        ControllerService.firewall_status = lambda self: dict(d["fw_status"])
    fwm.RECEIPT_PATH = d["fw_receipt"]
    updater_units._SYSTEM_ROOTS = tuple(Path(r) for r in d["systemd_roots"])
    cfgmod.HW_DEFAULT = d["hw_default"]
    ControllerService.display_available = staticmethod(lambda: True)
    backends.RealProcFs.cmdlines = lambda self: {}
    backends.RealProcFs.tcp_listeners = lambda self: []
    backends.RealProcFs.owner_pid = lambda self, inode, budget_s: (None, False)
    gps.local_gpsd_listening = lambda: False
    service_system.read_kernel_time_state = lambda: {"synced": True, "maxerror_us": 1000}

    def _refuse(url, *_a, **_k):
        raise AssertionError(f"a real binary-channel download from the web child: {url}")
    binary_install._http_get = binary_install._open_stream = _refuse
    ControllerService.ENDPOINT_VERIFY_TIMEOUT_S = d["waits"]["endpoint"]
    ControllerService.DAEMON_VERIFY_TIMEOUT_S = d["waits"]["daemon"]
    Lifecycle.OBSERVE_TIMEOUT_S = d["waits"]["observe"]
    depth = [0]

    def _spy(real):                # the outermost start/restart result → the record file
        def wrapper(self, *a, **k):
            depth[0] += 1
            try:
                res = real(self, *a, **k)
            finally:
                depth[0] -= 1
            if depth[0] == 0:
                Path(d["record"]).write_text(json.dumps(
                    {"refusal": classify(res), "next_commands": list(res.next_commands)}))
            return res
        return wrapper
    ControllerService.start = _spy(ControllerService.start)
    ControllerService.restart = _spy(ControllerService.restart)


def _rebuilt(d):
    from lhpc.core.probes.backends import FakeSystem
    golden = _golden()
    _install(d)
    fake = FakeSystem(unix_replies={"/tmp/loraconf433.sock": golden._READY},
                      cmdlines_data={int(k): v for k, v in d["cmdlines"].items()})
    fake.listeners = golden._TncEndpoint(d["port"])
    box = golden.KissBox(Path(d["root"]), fake, None, d["port"])
    box.listens, box.term = d["listens"], d["term"]
    procs: list = []
    tnc = box.spawn(procs)

    def wrap_spawn(_real):         # the box's TNC process instead of the component binary
        def _spawn(argv, log, cwd=None, env=None):
            pid = tnc(argv, log, cwd, env)
            with open(d["pids"], "a") as fh:
                fh.write(json.dumps(box.spawned[-1]) + "\n")
            return pid
        return _spawn
    return types.SimpleNamespace(system=fake.system, manifest_path=d["manifest"],
                                 wrap_spawn=wrap_spawn)
