"""Helpers for the release lane, importable as `lhpc_testlab.release` — no path hacks, and
no importing a sibling test module (the suite's rule 7).

They drive the REAL `lhpc` executable and read LHPC's own predicates. Nothing here decides what
"correct" means; that is in the test cases, next to the evidence they assert.
"""
from __future__ import annotations

import os
import re
import signal
import socket
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

from lhpc_testlab.testing import run_lhpc


def stop(env: dict, *stacks: str, require: bool = True) -> None:
    """Release the bands before the next stack claims them.

    A stop that failed is not housekeeping: the next stack will collide with whatever is still
    holding the band, and the failure it then reports will name the wrong thing. `require=False`
    is for stacks that may not be running at all.
    """
    for sid in stacks:
        r = run_lhpc(env, "stack", "stop", sid, "--yes", timeout=300)
        if require and r.returncode != 0:
            raise AssertionError(f"stopping {sid} failed (rc {r.returncode}): "
                                 f"{r.stdout[-800:]}")


def state_of(env: dict, stack: str) -> str:
    """The stack's own run state as `lhpc status` reports it, e.g. `running` or `stopped`.

    Parsed from the stack's header line — `[kiss] LoRaHAM KISS TNC  (running)` — because merely
    finding the stack's NAME in the output is true of a stopped stack too, and of one that was
    never installed.
    """
    out = run_lhpc(env, "status", timeout=120).stdout
    m = re.search(rf"^\[{re.escape(stack)}\][^\n(]*\(([a-z-]+)\)", out, re.MULTILINE)
    return m.group(1) if m else ""


def running(env: dict, stack: str) -> bool:
    """Running, and not merely mentioned. `degraded` is not running: it is the state LHPC uses
    when the process is up but an endpoint it promised is not."""
    return state_of(env, stack) == "running"


def install_build(env: dict, stack: str, *, build: bool = True,
                  timeout: float = 1800.0) -> None:
    """Install on the DEFAULT channel (binary where published, else the pin) and build.

    A binary install has no source tree, so `lhpc build` is deliberately not run there —
    the binary channel refuses it, and a refusal is not evidence of anything."""
    run_lhpc(env, "install", stack, "--yes", check=True, timeout=timeout)
    if build and not on_binary(env, stack):
        run_lhpc(env, "build", stack, "--yes", check=True, timeout=timeout)


def on_binary(env: dict, stack: str) -> bool:
    """Installed from an artifact? The receipt is the authority — the same file
    `lhpc status --versions` renders `binary@…` from."""
    root = Path(env["LHPC_RUNTIME_ROOT"])
    return (root / "state" / "binary" / f"{stack}.json").is_file()


def wait_tcp(port: int, timeout: float, host: str = "127.0.0.1") -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            socket.create_connection((host, port), timeout=5).close()
            return True
        except OSError:
            time.sleep(1.0)
    return False


def wait_http(url: str, timeout: float, accept=(200,)) -> int:
    """Last status seen; 0 when the endpoint never answered."""
    deadline = time.monotonic() + timeout
    last = 0
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=5) as r:
                last = r.status
                if r.status in accept:
                    return r.status
        except urllib.error.HTTPError as exc:
            last = exc.code
            if exc.code in accept:
                return exc.code
        except OSError:
            pass
        time.sleep(2.0)
    return last


def pty_readiness(command: str, env: dict, *, ready_timeout: float = 60.0,
                  hold: float = 5.0, expect: str = "") -> bytes:
    """Run an INTERACTIVE component the way an operator does — on a real terminal — and prove
    it: it must draw its own screen, still be running afterwards, and exit CLEANLY when asked.
    Returns what it drew.

    `expect` is a regular expression the drawn output must match. Without it "wrote something"
    accepts a program that printed an error and stayed up, which is exactly the false pass this
    lane exists to avoid.

    Cleanup is part of the proof, not tidying: a component that has to be killed did not exit,
    and a lane that silently escalates to SIGKILL would hide a hung application.

    The controller never starts these itself, so the command comes from the production renderer
    (`manual_start_command`), not from a literal here.

    A real terminal means more than a pty pair: a curses program needs `TERM` and a non-zero
    window size, and exits immediately without them. CI has neither, so both are supplied here —
    otherwise this would measure the runner's environment instead of the build.
    """
    import fcntl
    import pty
    import struct
    import termios

    master, slave = pty.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 40, 120, 0, 0))
    env = dict(env)
    # Present-but-empty is the CI case, and ncurses reports it as TERM="unknown" and exits.
    if env.get("TERM", "") in ("", "unknown", "dumb"):
        env["TERM"] = "xterm-256color"
    env["LINES"], env["COLUMNS"] = "40", "120"
    # `manual_start_command` renders what the operator PASTES INTO A SHELL — it carries `cd`,
    # `&&` and a trailing `stty sane`. Run it through a shell on a real terminal, exactly as the
    # documentation tells the operator to; splitting it into argv would execute `cd` as a program.
    proc = subprocess.Popen(["/bin/bash", "-lc", command], stdin=slave, stdout=slave,
                            stderr=slave, env=env, start_new_session=True, close_fds=True)
    os.close(slave)
    drawn = b""
    try:
        import select
        deadline = time.monotonic() + ready_timeout
        while time.monotonic() < deadline and len(drawn) < 64:
            r, _, _ = select.select([master], [], [], 1.0)
            if r:
                try:
                    chunk = os.read(master, 4096)
                except OSError:
                    break
                if not chunk:
                    break
                drawn += chunk
            if proc.poll() is not None:
                break
        tail = drawn[-600:].decode("utf-8", "replace")
        assert drawn, f"{command!r} drew nothing on its terminal within {ready_timeout} s"
        if expect:
            assert re.search(expect, drawn.decode("utf-8", "replace"), re.IGNORECASE | re.DOTALL), (
                f"{command!r} did not draw anything matching {expect!r}. It drew: {tail!r}")
        time.sleep(hold)
        assert proc.poll() is None, (
            f"{command!r} exited on its own (rc={proc.returncode}) — not a running app. "
            f"It drew: {tail!r}")
        killed = False
    finally:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            proc.terminate()
        try:
            proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait(timeout=10)
            killed = True
        os.close(master)
    assert not killed, (f"{command!r} ignored SIGTERM and had to be killed — it does not exit "
                        f"cleanly")
    return drawn


def start_component(env: dict, component: str, timeout: float = 900.0) -> None:
    """Start ONE component by name.

    An `optional` component is not started by `lhpc stack start <stack>` — Sideband, LXMD and
    the MeshCore Web UI among them. Naming it is how an operator starts it, and it is the only
    way this lane can claim it starts at all.
    """
    r = run_lhpc(env, "stack", "start", component, "--yes", timeout=timeout)
    assert r.returncode == 0, (f"starting {component} failed (rc {r.returncode})",
                               r.stdout[-2000:], r.stderr[-500:])


def alive(env: dict, component: str) -> bool:
    """Is this COMPONENT running, as LHPC's own status reports it?"""
    out = run_lhpc(env, "status", timeout=120).stdout
    m = re.search(rf"^\s+{re.escape(component)}\s+([a-z-]+)", out, re.MULTILINE)
    return bool(m) and m.group(1) == "running"


def built(svc, stack_id: str, comp_id: str) -> bool:
    """Was this component BUILT? LHPC's own predicate, over the artifact rather than the
    checkout.

    It is the honest evidence for a component this box does not run. Voice ships two variants
    that share one source checkout and LHPC starts exactly one of them per box, so the other
    can only be proved to have been built — and a matching checkout proves neither."""
    st = svc.stack(stack_id)
    comp = next((c for c in st.components if c.id == comp_id), None)
    return bool(comp is not None and svc.is_built(comp))


def gui_startable(svc, stack_id: str, comp_id: str) -> bool:
    """LHPC's OWN predicate for 'can this GUI component run here'. Never re-derived: the
    build skip, the start gate and the image's composition check all read this one."""
    st = svc.stack(stack_id)
    comp = next((c for c in st.components if c.id == comp_id), None)
    if comp is None:
        return False
    if comp_id in svc.gui_unavailable_components(st):
        return False
    return not (svc.needs_display(comp) and not svc.display_available())
