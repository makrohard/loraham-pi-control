"""What `lhpc self-update --apply` does to a RUNNING console: it is stopped only right before the
checkout is changed, and started again only when this apply stopped it.

A refusal or a no-op ("No changes were made", "Already up to date") leaves the console alone — on
box H an identity refusal still restarted lhpc-web (RESULT-GO1 finding 2). The real CLI, service and
git run underneath; only `systemctl` is answered (the console is active) and recorded.
"""
from __future__ import annotations

import pytest

import gitrepo
from lhpc.core import selfupdate
from lhpc.core.probes.backends import CommandResult

STOP = ["systemctl", "--user", "stop", "lhpc-web.service"]
START = ["systemctl", "--user", "start", "lhpc-web.service"]


# The locks the apply holds before the stop (task admission with its owner record, the update lock) and the
# console's own lock file are bookkeeping, not a change; any other file under state/locks/ counts.
_LOCK_BOOKKEEPING = {"state/locks/controller-task-admission.lock", "state/locks/controller-task-admission.owner",
                     "state/locks/selfupdate.lock", "state/locks/controller-runtime"}


def _tree(root):
    """Every file under state/ and config/ with its bytes, except the lock bookkeeping above."""
    return {rel: p.read_bytes()
            for d in ("state", "config") if (root / d).is_dir()
            for p in sorted((root / d).rglob("*"))
            if p.is_file() and (rel := str(p.relative_to(root))) not in _LOCK_BOOKKEEPING}


class _Calls(list):
    """The systemctl calls; `locked`: (step, controller-runtime lock held EXCLUSIVE then) per mutation step and
    start; `answer`: verb -> a replacement answer for that systemctl verb (a result, or raises; a replaced start
    is a console that never answers); `console`: the console's SHARED hold while it runs."""


def _exclusive_held(paths) -> bool:
    try:
        with selfupdate.controller_runtime_lock(paths, exclusive=False):
            return False
    except selfupdate.ControllerRuntimeBusy:
        return True


@pytest.fixture()
def cli(env, monkeypatch):
    """`main` with the console ACTIVE, holding the controller-runtime lock SHARED until it is stopped: each
    systemctl call is recorded with the HEAD and the state/ + config/ files at that moment; the venv sync
    and the unit refresh are stubbed (not this behaviour) and record whether the lock is held EXCLUSIVE.
    A start brings the console up: it answers HTTP on its unix socket (a short path: a socket path is <= 107
    bytes)."""
    import tempfile
    import threading

    from lhpc.adapters.cli.main import main
    from lhpc.core.probes.backends import RealCommandRunner
    from lhpc.core.services import ControllerService
    root = env["paths"].runtime_root
    monkeypatch.setenv("LHPC_RUNTIME_ROOT", str(root))
    monkeypatch.delenv("INVOCATION_ID", raising=False)
    calls = _Calls()
    calls.locked, calls.answer = [], {}
    console = calls.console = [selfupdate.controller_runtime_lock(env["paths"], exclusive=False)]
    console[0].__enter__()
    sock_path, listeners, up = tempfile.mkdtemp(prefix="lhpc-web-") + "/lhpc-web.sock", [], threading.Event()
    threads = []
    calls.sock_path = sock_path
    monkeypatch.setattr(ControllerService, "_web_socket_path", lambda self: sock_path, raising=False)
    real = RealCommandRunner.run

    def run(self, argv, timeout=None, *a, **kw):
        if argv[0] != "systemctl":
            return real(self, argv, timeout, *a, **kw)
        calls.append((list(argv), gitrepo.git(env["work"], "rev-parse", "HEAD"), _tree(root)))
        if argv[2] == "stop" and console:
            console.pop().__exit__(None, None, None)          # the stopped console lets go of its hold
        if argv[2] == "start":
            calls.locked.append(("start", _exclusive_held(env["paths"])))
        if (answer := calls.answer.get(argv[2])) is not None:
            return answer()
        if argv[2] == "start" and not listeners:            # the console comes up and answers
            listeners.append(_listen(sock_path))
            up.set()
            threads.append(threading.Thread(target=_answer_http, args=(listeners[0], up), daemon=True))
            threads[0].start()
        return CommandResult(0, "", "")                      # is-active: active; stop/start: done

    def step(name, result):
        calls.locked.append((name, _exclusive_held(env["paths"])))
        return result
    monkeypatch.setattr(RealCommandRunner, "run", run)
    monkeypatch.setattr(selfupdate, "venv_sync", lambda *a, **k: step("venv-sync", CommandResult(0, "", "")))
    monkeypatch.setattr(ControllerService, "_refresh_units_post_update", lambda self: step("units", (True, "n/a")))
    yield main, calls
    if console:
        console.pop().__exit__(None, None, None)
    up.clear()
    for thread in threads:                    # it has stopped using the socket before the socket goes
        thread.join(timeout=5)
    for listener in listeners:
        listener.close()


def _listen(path):
    import socket
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(path)
    listener.listen()
    listener.settimeout(0.2)                  # here, not in the thread: the thread may run after teardown
    return listener


def _answer_http(listener, up):
    """The console serving: each connection gets an HTTP status line, until the test ends."""
    import contextlib
    while up.is_set():
        try:
            conn, _ = listener.accept()
        except OSError:
            continue
        with conn, contextlib.suppress(OSError):             # a client that hung up gets no answer
            conn.recv(1024)
            conn.sendall(b"HTTP/1.0 200 OK\r\nContent-Length: 0\r\n\r\n")


def _service_calls(calls):
    return [c[0] for c in calls if c[0][2] in ("stop", "start")]


def _diverge(env):
    gitrepo.upstream_commit(env["up"])
    (env["work"] / "diverge.txt").write_text("local commit\n")
    gitrepo.git(env["work"], "add", "-A")
    gitrepo.git(env["work"], "commit", "-m", "local")


def _dirty(env):
    gitrepo.upstream_commit(env["up"])
    (env["work"] / "pyproject.toml").write_text("# mine\n")


def _unsafe_identity(env, monkeypatch):
    from types import SimpleNamespace

    from lhpc.core.services import ControllerService
    gitrepo.upstream_commit(env["up"])
    monkeypatch.setattr(ControllerService, "controller", lambda self: SimpleNamespace(
        branch="main", remote="https://example.invalid/loraham-pi-control.git"))
    monkeypatch.setattr(ControllerService, "controller_identity_live", lambda self: {
        "status": "unsafe", "reason": "checkout is in detached HEAD"})


def _unreachable(env):
    gitrepo.upstream_commit(env["up"])
    gitrepo.git(env["work"], "remote", "set-url", "origin", str(env["work"].parent / "gone.git"))


@pytest.mark.parametrize("make, rc, said", [
    (lambda env, mp: _unsafe_identity(env, mp), 1, "Self-update blocked: unsafe controller identity"),
    (lambda env, mp: None, 0, "Already up to date."),
    (lambda env, mp: _dirty(env), 1, "Local changes are present"),
    (lambda env, mp: _diverge(env), 1, "the local branch has diverged from upstream"),
    (lambda env, mp: _unreachable(env), 1, "Could not reach upstream"),
], ids=["identity", "already", "dirty", "diverged", "unreachable"])
def test_a_refusal_or_a_no_op_neither_stops_nor_starts_the_console(env, cli, monkeypatch, capsys, make, rc, said):
    main, calls = cli
    make(env, monkeypatch)
    head = gitrepo.git(env["work"], "rev-parse", "HEAD")
    assert main(["self-update", "--apply", "--yes"]) == rc
    out = capsys.readouterr().out
    assert said in out, out
    assert _service_calls(calls) == [], calls
    assert gitrepo.git(env["work"], "rev-parse", "HEAD") == head


def test_the_diverged_refusal_keeps_its_remedy(env, cli, capsys):
    """Refused before the console is touched, the divergence still offers the overwrite (the only remedy)."""
    main, _calls = cli
    _diverge(env)
    assert main(["self-update", "--apply", "--yes"]) == 1
    out = capsys.readouterr().out
    assert "lhpc self-update --apply --overwrite" in out and "discards exactly those local" in out, out


def test_an_advance_stops_the_console_before_anything_changes_then_starts_it(env, cli, capsys):
    """The stop comes after the gates and before the first change: at the stop the checkout is at the old
    head and nothing under state/ or config/ differs from before the apply; then the console starts."""
    main, calls = cli
    root = env["paths"].runtime_root
    old = gitrepo.git(env["work"], "rev-parse", "HEAD")
    new = gitrepo.upstream_commit(env["up"])
    before = _tree(root)
    assert main(["self-update", "--apply", "--yes"]) == 0, capsys.readouterr().out
    assert _service_calls(calls) == [STOP, START], calls
    stop = next(c for c in calls if c[0] == STOP)
    assert stop[1] == old and stop[2] == before
    assert gitrepo.git(env["work"], "rev-parse", "HEAD") == new


def _retry_clock(monkeypatch, on_wait=lambda: None):
    """The lock retry's clock moves only by its own waits (no real clock); a retry past ten waits fails the test
    instead of hanging it. Returns the waits."""
    import time as _time
    waits, clock = [], [0.0]

    def wait(s):
        waits.append(s)
        clock[0] += s
        assert len(waits) <= 10, "the retry never reached its deadline"
        on_wait()
    monkeypatch.setattr(_time, "sleep", wait)
    monkeypatch.setattr(_time, "monotonic", lambda: clock[0])
    return waits


def test_a_late_release_of_the_console_lock_is_waited_for(env, cli, monkeypatch, capsys):
    """The stopped console lets go of its SHARED lock a moment late: the bounded retry takes it and the
    update goes on. The release happens in the retry's own wait (no real clock)."""
    main, calls = cli
    holder = selfupdate.controller_runtime_lock(env["paths"], exclusive=False)
    holder.__enter__()
    waits = _retry_clock(monkeypatch, lambda: holder.__exit__(None, None, None))
    new = gitrepo.upstream_commit(env["up"])
    assert main(["self-update", "--apply", "--yes"]) == 0, capsys.readouterr().out
    assert len(waits) == 1 and _service_calls(calls) == [STOP, START], (waits, calls)
    assert gitrepo.git(env["work"], "rev-parse", "HEAD") == new


def test_another_holder_of_the_console_lock_is_busy_and_the_console_is_started_again(env, cli, monkeypatch, capsys):
    """Something else keeps the controller-runtime lock past the retry: the apply refuses with nothing
    changed, without hanging (the retry's clock moves only by its own waits: 2 s of 0.5 s waits), and starts the
    console it had stopped."""
    from lhpc.core.services import ControllerService
    main, calls = cli
    monkeypatch.setattr(ControllerService, "_LOCK_WAIT_S", 2.0)
    waits = _retry_clock(monkeypatch)
    old = gitrepo.git(env["work"], "rev-parse", "HEAD")
    gitrepo.upstream_commit(env["up"])
    with selfupdate.controller_runtime_lock(env["paths"], exclusive=False):
        assert main(["self-update", "--apply", "--yes"]) == 1
    out = capsys.readouterr().out
    assert "did not release the controller-runtime lock — no changes made." in out, out
    assert waits == [0.5] * 4 and _service_calls(calls) == [STOP, START], (waits, calls)
    assert gitrepo.git(env["work"], "rev-parse", "HEAD") == old


def test_a_resumed_venv_sync_stops_the_console_first(env, cli, monkeypatch, capsys):
    """Up to date, but the venv is behind the checkout: the sync the apply resumes changes what the running
    console runs, so the console is stopped before the sync and started after it."""
    main, calls = cli
    monkeypatch.setattr(selfupdate, "venv_unsynced", lambda root: "installed 0.0.1, checkout 0.0.2")
    monkeypatch.setattr(selfupdate, "venv_sync", lambda *a, **k: (
        calls.append((["venv-sync"], None, None)), CommandResult(0, "", ""))[1])
    assert main(["self-update", "--apply", "--yes"]) == 0, capsys.readouterr().out
    assert [c[0] for c in calls if c[0][0] == "venv-sync" or c[0][2] in ("stop", "start")] == [
        STOP, ["venv-sync"], START], calls


def test_the_console_lock_is_held_exclusive_through_the_venv_sync_and_the_unit_refresh(env, cli, capsys):
    """An advance changes the venv and the units after the checkout: the controller-runtime lock taken
    before the first change is still held EXCLUSIVE there, so no console can start on a half-synced venv."""
    main, calls = cli
    gitrepo.upstream_commit(env["up"])
    assert main(["self-update", "--apply", "--yes"]) == 0, capsys.readouterr().out
    assert calls.locked == [("venv-sync", True), ("units", True), ("start", False)], calls.locked
    assert _service_calls(calls) == [STOP, START], calls


def test_a_resumed_venv_sync_holds_the_console_lock(env, cli, monkeypatch, capsys):
    main, calls = cli
    monkeypatch.setattr(selfupdate, "venv_unsynced", lambda root: "installed 0.0.1, checkout 0.0.2")
    assert main(["self-update", "--apply", "--yes"]) == 0, capsys.readouterr().out
    assert calls.locked == [("venv-sync", True), ("units", True), ("start", False)], calls.locked


def test_with_the_console_not_running_the_lock_is_still_held_through_the_venv_sync(env, cli, capsys):
    main, calls = cli
    calls.answer["is-active"] = lambda: CommandResult(3, "", "")
    calls.console.pop().__exit__(None, None, None)
    gitrepo.upstream_commit(env["up"])
    assert main(["self-update", "--apply", "--yes"]) == 0, capsys.readouterr().out
    assert calls.locked == [("venv-sync", True), ("units", True)] and _service_calls(calls) == [], calls


def test_a_resumed_venv_sync_is_refused_while_another_holder_keeps_the_console_lock(env, cli, monkeypatch, capsys):
    """Another process keeps the lock SHARED past the retry: the resumed sync does not run under it, and
    the console this apply stopped is started again."""
    from lhpc.core.services import ControllerService
    main, calls = cli
    monkeypatch.setattr(ControllerService, "_LOCK_WAIT_S", 2.0)
    waits = _retry_clock(monkeypatch)
    monkeypatch.setattr(selfupdate, "venv_unsynced", lambda root: "installed 0.0.1, checkout 0.0.2")
    with selfupdate.controller_runtime_lock(env["paths"], exclusive=False):
        assert main(["self-update", "--apply", "--yes"]) == 1
    out = capsys.readouterr().out
    assert "did not release the controller-runtime lock — no changes made." in out, out
    assert waits == [0.5] * 4 and calls.locked == [("start", False)], (waits, calls.locked)
    assert _service_calls(calls) == [STOP, START], calls


def _stop_times_out():
    return CommandResult(1, "", "", timed_out=True)


def _stop_interrupted():
    raise KeyboardInterrupt


@pytest.mark.parametrize("stop, rc", [(_stop_times_out, 1), (_stop_interrupted, 130)], ids=["timeout", "ctrl-c"])
def test_a_stop_that_did_not_return_cleanly_still_starts_the_console_again(env, cli, capsys, stop, rc):
    """systemd took the stop, but `systemctl stop` timed out or the operator pressed Ctrl-C before it
    returned: the console may be stopped, so this apply starts it again; nothing changed."""
    main, calls = cli
    calls.answer["stop"] = stop
    old = gitrepo.git(env["work"], "rev-parse", "HEAD")
    gitrepo.upstream_commit(env["up"])
    assert main(["self-update", "--apply", "--yes"]) == rc
    assert _service_calls(calls) == [STOP, START], calls
    assert calls.locked == [("start", False)] and gitrepo.git(env["work"], "rev-parse", "HEAD") == old


@pytest.mark.parametrize("raised", [KeyboardInterrupt, OSError], ids=["ctrl-c", "error"])
def test_an_apply_that_raises_and_a_failed_restart_say_the_console_did_not_restart(env, cli, monkeypatch, capsys,
                                                                                  raised):
    """The venv sync is interrupted (Ctrl-C) or raises, and starting the console again fails: the operator
    is still told the console did NOT restart, with the command to start it."""
    main, calls = cli

    def sync(*a, **k):
        raise raised("pip interrupted")
    monkeypatch.setattr(selfupdate, "venv_sync", sync)
    calls.answer["start"] = lambda: CommandResult(1, "", "Job for lhpc-web.service failed.")
    gitrepo.upstream_commit(env["up"])
    try:
        rc = main(["self-update", "--apply", "--yes"])
    except Exception as exc:                                  # noqa: BLE001 - the red-before shape: it escaped
        rc = f"raised {type(exc).__name__}"
    cap = capsys.readouterr()
    assert rc == 1, (rc, cap)
    assert "lhpc-web did NOT restart" in cap.out and "systemctl --user start lhpc-web.service" in cap.out, cap
    assert _service_calls(calls) == [STOP, START] and calls.locked == [("start", False)], calls


def test_a_start_that_returns_0_while_the_console_never_comes_up_says_it_did_not_restart(env, cli, monkeypatch,
                                                                                       capsys):
    """`systemctl start` of the simple unit returns 0 once the process is launched; a console that then exits at
    once (a dependency the update broke) never answers on its socket: the bounded wait says it did NOT restart."""
    from lhpc.core.services import ControllerService
    main, calls = cli
    monkeypatch.setattr(ControllerService, "_WEB_UP_S", 2.0, raising=False)
    calls.answer["start"] = lambda: CommandResult(0, "", "")
    gitrepo.upstream_commit(env["up"])
    waits = _retry_clock(monkeypatch)
    assert main(["self-update", "--apply", "--yes"]) == 1
    out = capsys.readouterr().out
    assert "lhpc-web did NOT restart" in out and "systemctl --user start lhpc-web.service" in out, out
    assert waits == [0.5] * 4 and _service_calls(calls) == [STOP, START], (waits, calls)


def _start_raises(exc):
    def start():
        raise exc("start interrupted")
    return start


@pytest.mark.parametrize("sync_raises, start_raises, said", [
    (KeyboardInterrupt, KeyboardInterrupt, "The update stopped (KeyboardInterrupt"),
    (OSError, OSError, "The update stopped (OSError"),
    (None, KeyboardInterrupt, "Update applied"),
    (None, OSError, "Update applied"),
], ids=["ctrl-c-twice", "error-twice", "start-ctrl-c", "start-error"])
def test_a_restart_that_raises_still_says_the_console_did_not_restart(env, cli, monkeypatch, capsys,
                                                                     sync_raises, start_raises, said):
    """A second Ctrl-C (or a launch error) during the restart keeps the first failure's report and still says
    the console did NOT restart, with the command to start it."""
    main, calls = cli
    if sync_raises is not None:
        def sync(*a, **k):
            raise sync_raises("pip interrupted")
        monkeypatch.setattr(selfupdate, "venv_sync", sync)
    calls.answer["start"] = _start_raises(start_raises)
    gitrepo.upstream_commit(env["up"])
    try:
        rc = main(["self-update", "--apply", "--yes"])
    except BaseException as exc:                              # noqa: BLE001 - the red-before shape: it escaped
        rc = f"raised {type(exc).__name__}"
    cap = capsys.readouterr()
    assert rc == 1, (rc, cap)
    assert said in cap.out and "lhpc-web did NOT restart" in cap.out, cap
    assert "systemctl --user start lhpc-web.service" in cap.out and _service_calls(calls) == [STOP, START], cap


def test_a_console_that_listens_but_never_answers_http_did_not_restart(env, cli, monkeypatch, capsys):
    """The console binds its socket and then stops (an exit after the bind): a connection is accepted by the
    kernel, but no HTTP answer comes, so the restart is not taken as proven."""
    from lhpc.core.services import ControllerService
    main, calls = cli
    monkeypatch.setattr(ControllerService, "_WEB_UP_S", 1.0, raising=False)
    held = []

    def start():
        held.append(_listen(calls.sock_path))                 # listening, never accepting or answering
        return CommandResult(0, "", "")
    calls.answer["start"] = start
    gitrepo.upstream_commit(env["up"])
    waits = _retry_clock(monkeypatch)
    try:
        assert main(["self-update", "--apply", "--yes"]) == 1
    finally:
        for listener in held:
            listener.close()
    out = capsys.readouterr().out
    assert "lhpc-web did NOT restart" in out and "systemctl --user start lhpc-web.service" in out, out
    assert waits and _service_calls(calls) == [STOP, START], (waits, calls)


def test_ctrl_c_while_waiting_for_the_console_exits_130_and_says_the_restart_was_not_verified(env, cli, monkeypatch,
                                                                                             capsys):
    """The operator presses Ctrl-C while the apply waits for the started console to answer: the start may
    still complete, so it says the restart was not verified (not that it failed) and ends as interrupted."""
    import time as _time
    main, calls = cli

    def ctrl_c(s):
        raise KeyboardInterrupt

    def start():                                               # started; not answering yet, and the wait is cut
        monkeypatch.setattr(_time, "sleep", ctrl_c)
        return CommandResult(0, "", "")
    calls.answer["start"] = start
    gitrepo.upstream_commit(env["up"])
    assert main(["self-update", "--apply", "--yes"]) == 130
    cap = capsys.readouterr()
    assert "not verified" in cap.err and "systemctl --user status lhpc-web.service" in cap.err, cap
    assert "did NOT restart" not in cap.out + cap.err and _service_calls(calls) == [STOP, START], (cap, calls)


@pytest.mark.parametrize("sync_raises", [None, KeyboardInterrupt], ids=["normal", "exc-path"])
def test_a_readiness_wait_that_raises_is_still_reported(env, cli, monkeypatch, capsys, sync_raises):
    """The wait for the started console itself raises (socket() hits EMFILE): the restart is not established,
    the report stays, and the apply's own exception is not replaced."""
    from lhpc.core.services import ControllerService
    main, calls = cli

    def no_socket(self):
        raise OSError(24, "Too many open files")
    monkeypatch.setattr(ControllerService, "_web_answers", no_socket)
    if sync_raises is not None:
        def sync(*a, **k):
            raise sync_raises("pip interrupted")
        monkeypatch.setattr(selfupdate, "venv_sync", sync)
    gitrepo.upstream_commit(env["up"])
    try:
        rc = main(["self-update", "--apply", "--yes"])
    except BaseException as exc:                              # noqa: BLE001 - the red-before shape: it escaped
        rc = f"raised {type(exc).__name__}"
    cap = capsys.readouterr()
    assert rc == 1 and "lhpc-web did NOT restart" in cap.out, (rc, cap)
    assert "systemctl --user start lhpc-web.service" in cap.out, cap
    assert sync_raises is None or "The update stopped (KeyboardInterrupt" in cap.out, cap


def test_a_console_that_accepts_and_hangs_up_without_an_answer_did_not_restart(env, cli, monkeypatch, capsys):
    """Accepting the connection is not an answer: a console that closes it without an HTTP status line (an empty
    read) is not taken as restarted."""
    import contextlib
    import threading

    from lhpc.core.services import ControllerService
    main, calls = cli
    monkeypatch.setattr(ControllerService, "_WEB_UP_S", 1.0, raising=False)
    held, up = [], threading.Event()

    def hang_up(listener):                                    # reads the request, then closes: an empty read
        listener.settimeout(0.2)
        while up.is_set():
            with contextlib.suppress(OSError):
                conn = listener.accept()[0]
                with conn:
                    conn.recv(1024)

    def start():
        held.append(_listen(calls.sock_path))
        up.set()
        threading.Thread(target=hang_up, args=(held[0],), daemon=True).start()
        return CommandResult(0, "", "")
    calls.answer["start"] = start
    gitrepo.upstream_commit(env["up"])
    waits = _retry_clock(monkeypatch)
    try:
        assert main(["self-update", "--apply", "--yes"]) == 1
    finally:
        up.clear()
        for listener in held:
            listener.close()
    out = capsys.readouterr().out
    assert "lhpc-web did NOT restart" in out and waits, (out, waits)


def test_an_apply_that_raises_then_ctrl_c_while_waiting_says_the_restart_was_not_verified(env, cli, monkeypatch,
                                                                                          capsys):
    """The apply raised, the console was started again, and Ctrl-C cut the wait for its answer: the report names
    the apply's exception and says the restart was not verified (it may still come up)."""
    import time as _time
    main, calls = cli

    def sync(*a, **k):
        raise OSError("pip failed to launch")
    monkeypatch.setattr(selfupdate, "venv_sync", sync)

    def ctrl_c(s):
        raise KeyboardInterrupt

    def start():
        monkeypatch.setattr(_time, "sleep", ctrl_c)
        return CommandResult(0, "", "")
    calls.answer["start"] = start
    gitrepo.upstream_commit(env["up"])
    assert main(["self-update", "--apply", "--yes"]) == 1
    out = capsys.readouterr().out
    assert "The update stopped (OSError" in out and "restart not verified" in out, out
    assert "did NOT restart" not in out and _service_calls(calls) == [STOP, START], (out, calls)
