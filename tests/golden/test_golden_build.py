"""Golden: `ControllerService.build` — what a CLI build does today, in order.

Phases: the static refusals (controller id, binary channel, not installed — no lock, no write) →
the plan (apply=False) → task admission → the source-txn index lock (recover-scan, refuse an
unresolved journal) → every source lock → per component: invalidate a stale completion marker,
run each step through the runner into its own log, then write the build-inputs sidecar and the
completion marker last. The detached web build is a different code path (a rendered launcher,
tests/core/test_build_launcher_runtime.py).
"""

import re

from lhpc.core import lifecycle as lifecycle_mod
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import CommandResult, FakeSystem
from lhpc.core.services import ControllerService

CHAT_STEP = ("gcc", "clients/chat/lorachat_ncurses_113.c", "-o", "loraham_chat", "-lncurses",
             "-lpthread")
LOCKS = ["admission", "lock:controller-task-admission", "lock:source-txn-index",
         "lock:source.src/LoRaHAM_Daemon"]
NOTHING = {"added": [], "removed": [], "changed": []}


class _EveryStepSucceeds(dict):
    """A runner table answering every argv with rc 0 — the build recipe itself is the manifest's."""

    def get(self, argv, default=None):
        return CommandResult(0, "ok\n", "")


def _box(root, commands, *, installed=True):
    fake = FakeSystem(commands=commands)
    svc = ControllerService(system=fake.system, paths=Paths(runtime_root=root))
    svc.bootstrap(apply=True)
    if installed:
        (root / "src" / "LoRaHAM_Daemon").mkdir(parents=True)
    return fake, svc


def _steps(fake):
    return [c for c in fake.calls if c[0] not in ("systemctl", "git")]


def test_plan_then_build(tmp_path, run_op):
    """intended: the plan lists each component's argv and changes nothing; the apply takes
    admission, the index lock and the source lock, then runs the one step into its log."""
    fake, svc = _box(tmp_path, {CHAT_STEP: CommandResult(0, "built\n", "")})
    plan = run_op(tmp_path, lambda: svc.build("chat"))
    assert plan.fields == {"ok": True, "summary": "Build plan for 'chat': 1 component(s).",
                           "data_keys": ["changes"], "next_commands": ["lhpc build chat --yes"],
                           "heads": ["[build] loraham-chat"], "outcomes": []}
    assert plan.phases == [] and plan.files == NOTHING and _steps(fake) == []

    run = run_op(tmp_path, lambda: svc.build("chat", apply=True))
    assert run.fields == {"ok": True, "summary": "Build succeeded for 'chat'.", "data_keys": [],
                          "next_commands": ["lhpc status chat"],
                          "heads": ["[log] loraham-chat", "[succeeded] build"], "outcomes": []}
    assert run.phases == LOCKS + ["mutate:build-step"]
    assert run.files == {"added": ["logs/build-loraham-chat.log"], "removed": [], "changed": []}
    assert (tmp_path / "logs/build-loraham-chat.log").read_text() == "built\n"
    assert _steps(fake) == [list(CHAT_STEP)]


def test_failed_step(tmp_path, run_op):
    """intended: a failing step fails the build with its rc and log tail; the log is the step's
    output."""
    _fake, svc = _box(tmp_path, {})
    run = run_op(tmp_path, lambda: svc.build("chat", apply=True))
    assert run.fields == {"ok": False, "summary": "Build FAILED for 'chat'.", "data_keys": [],
                          "next_commands": ["lhpc status chat"],
                          "heads": ["[log] loraham-chat", "[failed] build"], "outcomes": []}
    assert re.fullmatch(r"  \[failed\] build loraham-chat \(rc 127, log /\S+/logs/"
                        r"build-loraham-chat\.log\)", run.res.details[1])
    assert run.res.details[2:] == ["      no fake"]
    assert run.phases == LOCKS + ["mutate:build-step"]
    assert run.files == {"added": ["logs/build-loraham-chat.log"], "removed": [], "changed": []}


def test_completion_marker_is_written_last(tmp_path, run_op, phases, monkeypatch):
    """intended: per marker-gated component: the stale marker is invalidated before its first
    step, each step logs into its own file, and the inputs sidecar then the completion marker are
    stamped only after its last step (the consumed-source lines record each source's revision)."""
    _fake, svc = _box(tmp_path, _EveryStepSucceeds(), installed=False)
    real_write = lifecycle_mod.runtime_fs.atomic_write

    def stamp(paths, path, *a, **k):          # the build's two stamps, into the phase log
        name = str(path).rsplit("/", 1)[-1]
        if name in (".lhpc-build-inputs", ".lhpc-build-complete"):
            phases.append("final:stamp:" + name)
        return real_write(paths, path, *a, **k)
    monkeypatch.setattr(lifecycle_mod.runtime_fs, "atomic_write", stamp)
    for comp in svc.stack("meshcore").components:
        if comp.source:
            (tmp_path / comp.source.path).mkdir(parents=True, exist_ok=True)
    run = run_op(tmp_path, lambda: svc.build("meshcore", apply=True))
    assert run.fields["ok"] is True and run.fields["summary"] == "Build succeeded for 'meshcore'."
    assert [h for h in run.fields["heads"] if h.startswith("[succeeded]")] == [
        "[succeeded] build"] * 3
    per_comp = [["mutate:invalidate-marker"] + ["mutate:build-step"] * n
                + ["final:stamp:.lhpc-build-inputs", "final:stamp:.lhpc-build-complete"]
                for n in (5, 3, 3)]                   # meshcore-node, meshcore-webui, meshcore-cli
    assert run.phases == ["admission", "lock:controller-task-admission", "lock:source-txn-index",
                          "lock:source.src/meshcore-cli", "lock:source.src/meshcore-webui",
                          "lock:source.src/openhop-core"] + sum(per_comp, [])
    markers = {
        "src/openhop-core/.venv/.lhpc-build-complete":
            "lhpc build complete\nconsumed meshcore-node ok\nconsumed openhop-repeater-src ok\n",
        "src/meshcore-webui/backend/.venv/.lhpc-build-complete": "lhpc build complete\n",
        "src/meshcore-cli/.venv/.lhpc-build-complete": "lhpc build complete\n",
    }
    for rel, text in markers.items():
        assert (tmp_path / rel).read_text() == text
    assert sorted(f for f in run.files["added"] if not f.startswith("logs/")) == sorted(
        list(markers) + ["src/meshcore-cli/.venv/bin/.lhpc-build-inputs",
                         "src/meshcore-webui/backend/.venv/bin/.lhpc-build-inputs",
                         "src/openhop-core/.venv/bin/.lhpc-build-inputs"])
    assert [f for f in run.files["added"] if f.startswith("logs/")] == (
        [f"logs/build-meshcore-cli-{i}.log" for i in range(3)]
        + [f"logs/build-meshcore-node-{i}.log" for i in range(5)]
        + [f"logs/build-meshcore-webui-{i}.log" for i in range(3)])


def test_refused_not_installed(tmp_path, run_op):
    """intended: a component with no source is refused before admission — no lock, no write,
    no step run."""
    fake, svc = _box(tmp_path, {CHAT_STEP: CommandResult(0, "", "")}, installed=False)
    run = run_op(tmp_path, lambda: svc.build("chat", apply=True))
    assert run.fields == {"ok": False,
                          "summary": "Refusing to build 'chat': loraham-chat is not installed.",
                          "data_keys": [], "next_commands": ["lhpc install chat"],
                          "heads": ["[not-installed] loraham-chat"], "outcomes": []}
    assert run.phases == [] and run.files == NOTHING and _steps(fake) == []


def test_refused_by_admission(tmp_path, run_op, uninstall_guard):
    """intended: admission refuses (typed: data["admission_blocked"]) before the source locks."""
    fake, svc = _box(tmp_path, {CHAT_STEP: CommandResult(0, "", "")})
    uninstall_guard(tmp_path)
    run = run_op(tmp_path, lambda: svc.build("chat", apply=True))
    assert run.fields == {
        "ok": False, "data_keys": ["admission_blocked"], "next_commands": [], "heads": [],
        "outcomes": [],
        "summary": "A controller uninstall is in progress (.lhpc-uninstalling) — refusing to "
                   "start new work. Let it finish, or recover it."}
    assert run.res.data["admission_blocked"] == "uninstalling"
    assert run.phases == ["admission", "lock:controller-task-admission"]
    assert run.files == NOTHING and _steps(fake) == []


def test_refused_by_interrupted_install(tmp_path, run_op, interrupted_install):
    """intended: an unresolved source-transaction journal refuses under the index lock, before
    any source lock or step."""
    fake, svc = _box(tmp_path, {CHAT_STEP: CommandResult(0, "", "")})
    interrupted_install(tmp_path)
    run = run_op(tmp_path, lambda: svc.build("chat", apply=True))
    assert run.fields == {
        "ok": False, "data_keys": [], "next_commands": ["lhpc status chat"], "heads": [],
        "outcomes": [],
        "summary": "Build blocked for 'chat': an unresolved source-transaction journal is "
                   "present — resolve it before any source operation"}
    assert run.phases == ["admission", "lock:controller-task-admission", "lock:source-txn-index"]
    assert run.files == NOTHING and _steps(fake) == []


def test_contended_source_lock_refuses(tmp_path, run_op, held_lock):
    """intended: another process holding the source lock refuses the build at once, naming the
    holder, with no step run."""
    fake, svc = _box(tmp_path, {CHAT_STEP: CommandResult(0, "", "")})
    with held_lock(svc._paths, "source.src/LoRaHAM_Daemon"):
        run = run_op(tmp_path, lambda: svc.build("chat", apply=True))
    assert run.fields["ok"] is False and run.fields["outcomes"] == []
    assert re.fullmatch(r"Build blocked for 'chat': resource 'source\.src-loraham_daemon' is "
                        r"busy: golden on 'x' \(pid \d+\)", run.fields["summary"])
    assert run.phases == LOCKS
    assert run.files == NOTHING and _steps(fake) == []
