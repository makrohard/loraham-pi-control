"""Row C of the slow-target build proof (plans/PLAN-F43.md §4, §5 change 5, §8).

Inside a container throttled to less than the Pi Zero 2 W (`docker run --cpus --memory`, the
CI job `slow-build`), every operation on the update path runs under the PRODUCTION limits and
is timed: each lane stack's install (clone, checkout, the Meshtastic CLI venv), each
component's `lhpc build`, the graywolf upstream fetch and a one-click self-update from the
previous release tag to this commit. Then the fixed calibration workload proves the container
is at least as slow as the Zero, and the budget rule of `lhpc.core.slow_target` judges the
fresh evidence against the limits and the baseline `tests/data/slow-target-builds.toml`.

What counts as evidence: exit 0, no `[stalled]` / `[timeout]` / `[fail]` line on stdout or
stderr (`_judged`, applied to every step the lane runs), and the operation's own timing line.
Anything else FAILS here, quoting the marker line, and an operation with no evidence fails the
budget case again by name. The evidence is written to
`$LHPC_SLOW_BUILD_OUT/slow-target-builds.toml` (default `/out`) with `source = "throttled-ci"`.

While the baseline holds no measured entry (bootstrap, before the first row A) the calibration
and budget cases SKIP, naming what is unmeasured, after every row-C case has judged this run;
the job's JUnit gate accepts those two skips in that state only.

Run in this order, without `-x`: the budget case needs every measurement the run could make.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import subprocess
import sys
import time
import tomllib
import uuid
from pathlib import Path

import pytest
from lhpc_testlab.release import on_binary
from lhpc_testlab.testing import LabServer, lab_env, run_lhpc

from lhpc.core import slow_target as stt
from lhpc.core.manifest import default_manifest_path, load_controller, load_manifest
from lhpc.version import __version__

pytestmark = pytest.mark.slow

REPO = next(p for p in Path(__file__).resolve().parents if (p / "lhpc" / "version.py").is_file())
BASELINE = tomllib.loads((REPO / "tests" / "data" / "slow-target-builds.toml").read_text())
OUT = Path(os.environ.get("LHPC_SLOW_BUILD_OUT", "/out"))
STACKS = load_manifest(default_manifest_path())
PYPROJECT = (REPO / "pyproject.toml").read_text()
# L8: the harness's own per-command limit. It must stay far above every product limit, or it
# becomes the limit under test.
HARNESS_S = 5 * 3600
# The stacks a release may move and the Zero runs from source or binary (plan §5 change 5).
# The daemon and RadioLib are the lab's fixtures; their entries come from row A only.
LANE_STACKS = ("kiss", "chat", "voice", "graywolf", "meshcore", "reticulum", "meshtastic")
_STACK_OF = {c.id: st.id for st in STACKS for c in st.components}
LANE_OPS = [(c, o) for c, o in stt.required(STACKS, BASELINE.get("excluded", {}))
            if _STACK_OF.get(c) in LANE_STACKS
            or c in (stt.SELFUPDATE_COMPONENT, stt.CLI_VENV_COMPONENT)]

EVIDENCE: dict[tuple[str, str], dict] = {}
CALIBRATION: list[dict] = []
FETCHED: dict[str, str] = {}
# calibrate.sh's work dir in the lane (its own default outside an install): named here so the
# disk throttle is proved on the disk that backs it (`_required_disks`).
CALIB_WORK = Path.home() / ".cache" / "lhpc-calib"
SYS_BLOCK = Path("/sys/dev/block")

_SECS = r"(\d+(?:\.\d+)?) s"
# The timing lines print one decimal (`.1f`): a step faster than 50 ms reads `0.0 s`. That is
# valid evidence of a fast step, recorded at this floor with a note, never as zero.
LOG_RESOLUTION_S = 0.1
BELOW_RESOLUTION = "below log resolution"
# Rejection markers, wherever a step prints them: `[stalled]`, `[timeout]`, `[fail]` and their
# longer forms (`[failed]`, the job log's `[TIMED OUT after …]`).
_BAD = re.compile(r"^[ \t]*\[(?:stalled|timeout|timed out|fail|failed)\b[^\]\n]*\]",
                  re.MULTILINE | re.IGNORECASE)
_QUIET = re.compile(rf"^\[progress\] longest quiet {_SECS}\s*$", re.MULTILINE)
_CLONE = re.compile(rf"^\[git\] clone {_SECS}\s*$", re.MULTILINE)
_CHECKOUT = re.compile(rf"^\[git\] checkout \S+ {_SECS}\s*$", re.MULTILINE)
_VENV = re.compile(rf"^\[venv\] {_SECS}\s*$", re.MULTILINE)
_PIP_SYNC_MARK = "[selfupdate] pip sync"
_PIP_SYNC = re.compile(rf"^{re.escape(_PIP_SYNC_MARK)} {_SECS}\s*$", re.MULTILINE)
# The rule for L4 on the release that introduces its timing line (docs/maintenance.md): the
# previous tag's helper runs the pip sync and cannot print it.
L4_INTRODUCING = ("lhpc-selfupdate selfupdate-pip (L4): no evidence on the introducing release — "
                  "measured from the next release on")
_CALIB = re.compile(r"^cpu=(\d+(?:\.\d+)?) io=(\d+(?:\.\d+)?) mem=(\d+(?:\.\d+)?) "
                    r"workload=(sha256:[0-9a-f]{64})\s*$", re.MULTILINE)


def _reject(what: str, text: str) -> None:
    """FAIL `what` on the first rejection marker in `text`, quoting its line."""
    m = _BAD.search(text or "")
    if m:
        end = text.find("\n", m.start())
        line = text[m.start():end if end != -1 else len(text)].strip()
        pytest.fail(f"{what}: rejection marker — not evidence: {line!r}", pytrace=False)


def _judged(what: str, r) -> str:
    """The ONE verdict on a lane step: no rejection marker on its stdout or its stderr, and exit
    status 0; anything else FAILS the case. Returns stdout and stderr together."""
    out = f"{r.stdout or ''}\n{r.stderr or ''}"
    _reject(what, out)
    if r.returncode != 0:
        pytest.fail(f"{what} failed (rc {r.returncode}) — not evidence: {out[-2000:]}",
                    pytrace=False)
    return out


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True,
                          timeout=600).stdout.strip()


def _toml(v) -> str:
    if isinstance(v, float):
        return f"{v:.1f}"
    if isinstance(v, (int, dt.date)):
        return str(v) if isinstance(v, int) else v.isoformat()
    return '"' + str(v).replace("\\", "\\\\").replace('"', '\\"') + '"'


def _write() -> None:
    """Rewrite the evidence file after every measurement, so a run that dies later still leaves
    what it measured."""
    parts = []
    for e in EVIDENCE.values():
        parts.append("[[measured]]\n" + "".join(f"{k} = {_toml(v)}\n" for k, v in e.items()))
    for c in CALIBRATION:
        parts.append("[[calibration]]\n" + "".join(f"{k} = {_toml(v)}\n" for k, v in c.items()))
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "slow-target-builds.toml").write_text("\n".join(parts))


def _record(component: str, op: str, seconds: float, quiet_s: float | None = None) -> None:
    problems = _env_problems()
    assert not problems, "not evidence — this is not the throttled box:\n" + "\n".join(problems)
    key = stt.current_key(component, op, STACKS, PYPROJECT, fetched=FETCHED.get(component, ""))
    assert key, f"no entry key for {component} {op} on this tree"
    entry = {"component": component, "op": op, "seconds": round(seconds, 1)}
    if entry["seconds"] < LOG_RESOLUTION_S:
        entry.update(seconds=LOG_RESOLUTION_S, note=BELOW_RESOLUTION)
    if quiet_s is not None:
        entry["quiet_s"] = round(quiet_s, 1)
    entry.update({"key": key, "source": "throttled-ci",
                  "host": (f"gha ubuntu-24.04-arm cpus={os.environ.get('SLOW_CPUS', '?')} "
                           f"mem={os.environ.get('SLOW_MEM', '?')} "
                           f"wiops={os.environ.get('SLOW_WRITE_IOPS', '?')} "
                           f"riops={os.environ.get('SLOW_READ_IOPS', '?')}"),
                  "lhpc": f"v{__version__} ({_git('-C', str(REPO), 'rev-parse', '--short', 'HEAD')})",
                  "date": dt.datetime.now(dt.UTC).date(),
                  "evidence": os.environ.get("LHPC_SLOW_BUILD_RUN_URL", "local run")})
    assert not stt.entry_errors(entry), stt.entry_errors(entry)
    EVIDENCE[(component, op)] = entry
    _write()


def _logs(env: dict) -> Path:
    return Path(env["LHPC_RUNTIME_ROOT"]) / "logs"


def _adoption(env: dict, component: str) -> None:
    """clone + checkout of `component`'s source tree, from the adoption log of whichever
    component sharing the tree adopted it."""
    for cid in stt.tree_components(STACKS, component):
        log = _logs(env) / f"adopt-{cid}.log"
        text = log.read_text(errors="replace") if log.is_file() else ""
        clone = _CLONE.findall(text)
        if not clone:
            continue
        _reject(log.name, text)
        checkout = _CHECKOUT.findall(text)
        assert checkout, f"{log.name} has no `[git] checkout <ref> <n> s` line — not evidence"
        _record(component, "clone", max(map(float, clone)))
        _record(component, "checkout", max(map(float, checkout)))
        return
    pytest.fail(f"no `[git] clone <n> s` line in the adoption log of {component}'s tree — "
                "not evidence")


def _build(env: dict, component: str) -> None:
    """The build's wall clock and its longest quiet gap (L1), from the logs THIS invocation
    wrote. A log already there is overwritten with a stale mark naming this invocation before
    the build; `run_job` truncates every log it writes, so a log still carrying the mark after
    the build is an older one and never evidence."""
    name = re.compile(rf"build-{re.escape(component)}(-\d+)?\.log")
    mark = f"[slow-build] stale: written before invocation {uuid.uuid4()}\n"
    logs_dir = _logs(env)
    for p in (logs_dir.iterdir() if logs_dir.is_dir() else ()):
        if name.fullmatch(p.name):
            p.write_text(mark)
    t0 = time.monotonic()
    r = run_lhpc(env, "build", component, "--yes", timeout=HARNESS_S)
    seconds = time.monotonic() - t0
    _judged(f"building {component}", r)
    logs = [p for p in (logs_dir.iterdir() if logs_dir.is_dir() else ())
            if name.fullmatch(p.name) and p.read_text(errors="replace") != mark]
    assert logs, f"{component}: no build log written by this invocation — not evidence"
    text = "\n".join(p.read_text(errors="replace") for p in logs)
    _reject(f"{component}: the build log", text)
    quiet = _QUIET.findall(text)
    assert quiet, (f"{component}: no `[progress] longest quiet <n> s` line in the build log — "
                   "not evidence (the L1 quantity)")
    _record(component, "build", seconds, quiet_s=max(map(float, quiet)))


def _deb_fetch(svc, component: str, tmp: Path) -> None:
    from lhpc.core.assets import asset_path
    stack = _STACK_OF[component]
    chk = svc.graywolf_upstream_check(stack)
    assert chk.ok, f"the upstream release check of {stack} failed: {chk.summary}"
    latest = svc.graywolf_upstream_state(stack).get("latest", "")
    assert latest, f"no upstream release of {stack} is known after the check"
    FETCHED[component] = latest
    t0 = time.monotonic()
    r = subprocess.run(["bash", str(asset_path("scripts/graywolf-fetch.sh")), str(tmp / "deb"),
                        latest, "--from-upstream"], capture_output=True, text=True,
                       timeout=HARNESS_S, check=False)
    seconds = time.monotonic() - t0
    _judged(f"the upstream fetch of {latest}", r)
    _record(component, "deb-fetch", seconds)


def _install(env: dict, stack: str):
    t0 = time.monotonic()
    r = run_lhpc(env, "install", stack, "--yes", timeout=HARNESS_S)
    _judged(f"installing {stack} ({time.monotonic() - t0:.0f} s)", r)
    return r


def _cli_venv(r) -> None:
    """L5 from the Meshtastic binary install's own `[venv] <n> s` lines."""
    out = _judged("the Meshtastic binary install (CLI venv)", r)
    venv = _VENV.findall(out)
    assert venv, "no `[venv] <n> s` line from the binary install — not evidence"
    _record(stt.CLI_VENV_COMPONENT, "cli-venv", max(map(float, venv)))


def _helper(lhpc: Path, env: dict) -> tuple[float, str]:
    """The self-update helper body, timed whole: (seconds, stdout + stderr). It runs the way
    its systemd unit runs it: `--run-service` is unit plumbing, refused (rc 2) without the
    systemd invocation marker `INVOCATION_ID`."""
    t0 = time.monotonic()
    r = subprocess.run([str(lhpc), "self-update", "--run-service"],
                       env={**env, "INVOCATION_ID": "slow-build-lane"},
                       capture_output=True, text=True, timeout=HARNESS_S, check=False)
    seconds = time.monotonic() - t0
    return seconds, _judged("the self-update helper", r)


def _calibrate() -> str:
    r = subprocess.run(["bash", str(REPO / "testlab" / "slowbuild" / "calibrate.sh"),
                        "--work-dir", str(CALIB_WORK)],
                       capture_output=True, text=True, timeout=HARNESS_S, check=False)
    return _judged("calibrate.sh", r)


def _env_problems() -> list[str]:
    """Why this box is not the throttled production box the evidence must come from."""
    out = []
    overrides = sorted(k for k in os.environ if k.startswith("LHPC_BUILD_"))
    if overrides:
        out.append(f"limit overrides are set — no run with them is evidence: {overrides}")
    try:
        cpus = float(os.environ.get("SLOW_CPUS", ""))
    except ValueError:
        cpus = 0.0
    if not 0 < cpus <= 1:
        out.append(f"SLOW_CPUS={os.environ.get('SLOW_CPUS')!r}: the lane needs 0 < cpus <= 1")
    cg = Path("/sys/fs/cgroup")
    try:
        quota, period = (cg / "cpu.max").read_text().split()
        mem = (cg / "memory.max").read_text().strip()
    except (OSError, ValueError) as exc:
        return [*out, f"the cgroup limits are unreadable: {exc}"]
    if quota == "max" or int(quota) / int(period) > cpus + 1e-6:
        out.append(f"cpu.max {quota} {period}: the CPU throttle to {cpus} CPUs is not in force")
    if mem == "max" or int(mem) > 416 * 2**20:
        out.append(f"memory.max {mem}: the 416 MiB memory cap is not in force")
    required, unresolved = _required_disks()
    out += unresolved + _io_problems(cg, required)
    if not os.cpu_count():
        out.append("nproc unreadable")
    return out


def _disk_of(path: Path) -> str | None:
    """The whole disk behind `path` (or its nearest existing parent) as MAJ:MIN, resolved as the
    job's `disk_of()` does: the filesystem's device, a partition's parent disk. None when it
    cannot be resolved — an overlay or other device-less filesystem, no sysfs entry."""
    while not path.exists() and path != path.parent:
        path = path.parent
    try:
        dev = path.stat().st_dev
        node = (SYS_BLOCK / f"{os.major(dev)}:{os.minor(dev)}").resolve(strict=True)
        if (node / "partition").exists():
            node = node.parent
        mm = (node / "dev").read_text().strip()
    except OSError:
        return None
    return mm if os.major(dev) and re.fullmatch(r"\d+:\d+", mm) else None


def _on_writable_layer(path: Path) -> bool:
    """`path` is on the container's root filesystem, its writable layer."""
    return path.stat().st_dev == Path("/").stat().st_dev


def _required_disks() -> tuple[dict[str, str], list[str]]:
    """The disks that MUST be throttled, {what: MAJ:MIN}, and why any of them is unknown.

    The container's writable layer has no block device inside the container (overlay): the job
    measures it outside (the overlay upperdir a lab container reports for its root) and names its
    disk in SLOW_IO_ROOT_DISK. The calibration
    work dir resolves here; on the writable layer it is that same disk. Unknown is a problem,
    never a skip."""
    found, problems = {}, []
    root = os.environ.get("SLOW_IO_ROOT_DISK", "")
    layer = bool(re.fullmatch(r"\d+:\d+", root))
    if layer:
        found["the container's writable layer"] = root
    else:
        problems.append(f"SLOW_IO_ROOT_DISK={root or None!r}: the disk behind the container's "
                        "writable layer is not named — the throttle cannot be proved")
    work = f"the calibration work dir ({CALIB_WORK})"
    near = next(p for p in (CALIB_WORK, *CALIB_WORK.parents) if p.exists())
    if layer and _on_writable_layer(near):
        found[work] = root
    elif mm := _disk_of(CALIB_WORK):
        found[work] = mm
    else:
        problems.append(f"STOP: backing device of {near} (the calibration work dir {CALIB_WORK}) "
                        "could not be resolved — the throttle cannot be applied")
    return found, problems


def _io_problems(cg: Path, required: dict[str, str]) -> list[str]:
    """The disk throttle (SLOW_WRITE_IOPS / SLOW_READ_IOPS, the job's `--device-*-iops`) must be
    in force in this cgroup's io.max on EACH required disk (`_required_disks`): one unthrottled
    disk runs the io part of the calibration — or any IO-bound step — at the runner's SSD
    speed. A tight line for some other disk proves nothing."""
    if not required:
        return ["no disk is required to be throttled — the disk throttle cannot be proved"]
    want = {}
    for key, var in (("wiops", "SLOW_WRITE_IOPS"), ("riops", "SLOW_READ_IOPS")):
        try:
            want[key] = int(os.environ.get(var, ""))
        except ValueError:
            return [f"{var}={os.environ.get(var)!r}: the lane needs the disk throttle"]
    try:
        lines = (cg / "io.max").read_text().splitlines()
    except OSError as exc:
        return [f"io.max is unreadable — the disk throttle cannot be proved: {exc}"]
    tight = set()
    for line in lines:
        disk, *fields = line.split() or [""]
        kv = dict(f.split("=", 1) for f in fields if "=" in f)
        if all(kv.get(k, "max") != "max" and int(kv[k]) <= v for k, v in want.items()):
            tight.add(disk)
    return [f"io.max {lines!r}: {what}, disk {mm}, is not throttled to "
            f"wiops<={want['wiops']} riops<={want['riops']}"
            for what, mm in required.items() if mm not in tight]


def test_slow_build_env():
    """The lane measures the PRODUCTION limits in a throttled box, or it measures nothing:
    every measurement refuses to record while any of this is wrong."""
    problems = _env_problems()
    assert not problems, "\n".join(problems)


@pytest.mark.parametrize("stack", LANE_STACKS)
def test_slow_build_stack(env, svc, stack, tmp_path):
    r = _install(env, stack)
    mine = [(c, o) for c, o in LANE_OPS if _STACK_OF.get(c) == stack]
    for component, op in mine:
        if op == "clone":
            _adoption(env, component)
        elif op == "build":
            _build(env, component)
        elif op == "deb-fetch":
            _deb_fetch(svc, component, tmp_path)
    if stack == "meshtastic":
        assert on_binary(env, stack), "meshtastic is not on the binary channel: no CLI venv"
        _cli_venv(r)


def _box_env(root: Path, home: Path) -> dict:
    """The lab env of `root` with its own $HOME, where the box's user units live: the lane never
    writes the container user's (or a developer's) $HOME/.config/systemd/user. The pip cache stays
    the user's, as on a box that installed before."""
    cache = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    home.mkdir(parents=True, exist_ok=True)
    return {**lab_env(root), "HOME": str(home), "XDG_CACHE_HOME": cache}


def _install_units(python: Path, root: Path, env: dict) -> list[str]:
    """The managed systemd units of a box, installed the way install.sh installs them: each unit
    of the installed release's `updater_units.ALL_UNITS`, rendered by that release's own
    `python -m lhpc.core.updater_units render <kind> <root> <checkout> <venv>` into
    $HOME/.config/systemd/user. No `systemctl --user` (the container has no user manager): the
    helper never calls it either — it is sandboxed and, after the update, only VERIFIES these
    files (`updater_units verify-set`, file reads), which is what the lane must exercise."""
    def py(*args: str) -> str:
        return subprocess.run([str(python), *args], env=env, capture_output=True, text=True,
                              check=True, timeout=600).stdout
    kinds, (r, checkout, venv) = json.loads(py(
        "-c", "import json, sys; from lhpc.core import updater_units as u; "
        "print(json.dumps([u.ALL_UNITS, u.deployment_paths(sys.argv[1])]))", str(root)))
    unit_dir = Path(env["HOME"]) / ".config" / "systemd" / "user"
    unit_dir.mkdir(parents=True, exist_ok=True)
    for kind in kinds:
        (unit_dir / kind).write_text(py("-m", "lhpc.core.updater_units", "render", kind, r,
                                        checkout, venv))
    return kinds


def _prev_tag() -> str:
    """The release tag before this commit: the one a one-click update starts from."""
    cand = _git("-C", str(REPO), "rev-parse", "HEAD")
    return _git("-C", str(REPO), "describe", "--tags", "--abbrev=0", "--match", "v*", f"{cand}^")


def _introducing(prev: str) -> bool:
    """True when `prev`'s helper has no pip sync line, so L4 cannot be measured on this release
    (docs/maintenance.md). Read from the tag itself, never from a helper run."""
    return _PIP_SYNC_MARK not in _git("-C", str(REPO), "show",
                                      f"{prev}:lhpc/core/service_selfupdate.py")


def _harden_box(root: Path, checkout: Path) -> None:
    """Owner-only 0700 on the runtime root, `src/` and the controller checkout, as a box's
    bootstrap leaves them (`Installer.plan_bootstrap`, its three `harden` actions). A clone made
    under a group-writable umask (the lab user's) fails the helper's controller-identity check
    otherwise: "checkout is group/other-writable"."""
    for d in (root, root / "src", checkout):
        d.chmod(0o700)


def test_slow_build_selfupdate(tmp_path):
    """L3 + L4: a one-click self-update from the previous release tag to this commit — the
    helper body (`lhpc self-update --run-service`) timed whole, its pip sync from its own line.
    The runtime is laid out as a box: the checkout at <root>/src/loraham-pi-control, the venv at
    <root>/venv/lhpc and the release's canonical units in $HOME (`_install_units`), so the
    helper's own post-update unit verification runs as on a box instead of failing on units no
    box lacks. The throttle (cpus <= 1) is already tighter than the unit's CPUQuota=150%. On the
    release that introduces the pip sync line the previous tag's helper cannot print it: L4 then
    has no evidence, by name (L4_INTRODUCING), and the budget case says so."""
    cand = _git("-C", str(REPO), "rev-parse", "HEAD")
    prev = _prev_tag()
    root = tmp_path / "runtime"
    LabServer(root).init_and_reset()
    env = _box_env(root, tmp_path / "home")
    remote = tmp_path / "remote.git"
    co, venv = root / "src" / "loraham-pi-control", root / "venv" / "lhpc"
    _git("clone", "--quiet", "--bare", str(REPO), str(remote))
    _git("-C", str(remote), "update-ref", "refs/heads/main", cand)
    _git("clone", "--quiet", "--branch", "main", str(remote), str(co))
    _git("-C", str(co), "reset", "--quiet", "--hard", prev)
    _harden_box(root, co)
    # A box's origin is the approved canonical remote, which the helper's controller-identity
    # check demands of an in-root checkout; the lane serves it from the local candidate remote
    # (git's own `insteadOf`, set in this checkout only), so `origin` reads as on a box.
    canonical = load_controller(co / "lhpc" / "data" / "manifest.example.toml").remote
    _git("-C", str(co), "remote", "set-url", "origin", canonical)
    _git("-C", str(co), "config", f"url.{remote}.insteadOf", canonical)
    subprocess.run([sys.executable, "-m", "venv", str(venv)], check=True, timeout=600)
    pip = [str(venv / "bin" / "python"), "-m", "pip", "install", "-q"]
    for target in (co, co / "testlab"):          # release-1 and its own lab provider
        subprocess.run([*pip, "-e", str(target)], check=True, timeout=HARNESS_S)
    _install_units(venv / "bin" / "python", root, env)
    (root / "state" / "selfupdate.request").write_text("normal\n")
    seconds, out = _helper(venv / "bin" / "lhpc", env)
    assert _git("-C", str(co), "rev-parse", "HEAD") == cand, f"{prev} was not updated to {cand}"
    _record(stt.SELFUPDATE_COMPONENT, "selfupdate-helper", seconds)
    sync = _PIP_SYNC.findall(out)
    if not sync and _introducing(prev):
        print(f"{L4_INTRODUCING} ({prev} has no pip sync line)")
        return
    assert sync, "no `[selfupdate] pip sync <n> s` line from the helper — not evidence"
    _record(stt.SELFUPDATE_COMPONENT, "selfupdate-pip", max(map(float, sync)))


def test_slow_build_calibrated():
    """§4b: the container is at least as slow as the Zero on the fixed workload."""
    out = _calibrate()
    m = _CALIB.search(out)
    assert m, f"calibrate.sh printed no `cpu= io= mem= workload=` line: {out[-500:]}"
    measured = {"cpu_s": float(m.group(1)), "io_s": float(m.group(2)), "mem_s": float(m.group(3))}
    CALIBRATION.append({"workload": m.group(4), "source": "throttled-ci", **measured,
                        "date": dt.datetime.now(dt.UTC).date()})
    _write()
    fails = stt.calibration_failures(BASELINE.get("calibration", []), m.group(4), measured)
    if fails and fails[0].startswith("uncalibrated") and not BASELINE.get("measured"):
        pytest.skip(f"{stt.BOOTSTRAP} — the Zero calibration is unmeasured; this run's "
                    f"cpu/io/mem are in the evidence: {fails[0]}")
    assert not fails, "\n".join(fails)


def _waived(fails: list[str], measured, intro: bool) -> tuple[list[str], str]:
    """(the failures that stand, the bootstrap skip reason or ""). On the release that introduces
    the pip sync line (`intro`: the previous tag's helper has none AND `stt.waiver` holds for this
    version) nobody can measure L4, here or on the Zero: its missing evidence and its missing Zero
    baseline are waived, by name, in and past bootstrap — the same waiver the coverage test
    applies. Bootstrap (no [[measured]] entry yet): every missing Zero baseline is waived too, by
    name. The evidence and the twice-the-measurement rule still judge this run's own numbers."""
    l4 = f"{stt.SELFUPDATE_COMPONENT} selfupdate-pip"
    if intro:
        fails = [f for f in fails if not f.startswith((f"no row C evidence for {l4}",
                                                       f"no Zero baseline for {l4}"))]
    unmeasured = [f.removeprefix("no Zero baseline for ").removesuffix(": run row A")
                  for f in fails if f.startswith("no Zero baseline for ")]
    boot = stt.bootstrap_reason(measured, unmeasured)
    if boot:
        fails = [f for f in fails if not f.startswith("no Zero baseline for ")]
        boot += f"; {L4_INTRODUCING}" if intro else ""
    return fails, boot


def test_slow_build_budget():
    """§8 over every operation this lane measures; every failure named, the E/Z ratios printed
    to `slow-build-summary.md` for the job summary."""
    this_minor = stt.version_minor(__version__)
    fails, lines = [], []
    for component, op in LANE_OPS:
        try:
            lim = stt.limit(op)
        except stt.LimitUnavailable as exc:
            fails.append(f"{component} {op}: {exc}")
            continue
        ev = EVIDENCE.get((component, op))
        key = ev["key"] if ev else stt.current_key(component, op, STACKS, PYPROJECT)
        f, line = stt.compare(component, op, lim, BASELINE.get("measured", []), ev, key,
                              this_minor)
        fails += f
        lines.append(line)
    # The release introducing the pip sync line has no L4 evidence (docs/maintenance.md).
    l4 = f"no row C evidence for {stt.SELFUPDATE_COMPONENT} selfupdate-pip"
    intro = (_introducing(_prev_tag()) and any(f.startswith(l4) for f in fails)
             and bool(stt.waiver(stt.SELFUPDATE_COMPONENT, "selfupdate-pip", __version__)))
    # The waiver first, then the verdict word: a waived failure never reads **FAIL**.
    standing, boot = _waived(fails, BASELINE.get("measured", []), intro)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "slow-build-summary.md").write_text(
        "### slow-build: E (row C) vs Z (Zero baseline)\n\n"
        + "\n".join(f"- {s}" for s in lines
                     + [f"**FAIL** {f}" if f in standing else f"**WAIVED** {f}" for f in fails]
                     + ([f"**NO EVIDENCE** {L4_INTRODUCING}"] if intro else [])) + "\n")
    assert LANE_OPS, "the lane measures nothing"
    fails = standing
    assert not fails, f"{len(fails)} budget failure(s):\n" + "\n".join(fails)
    if boot:
        pytest.skip(boot)
