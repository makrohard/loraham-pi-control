"""The post-step kind `log_wait` (B2a, his C): ready when a line of the log file at `path` contains `pattern`. The path
is opened fresh on every poll, so a guest reboot that moves the `uart-latest.log` symlink is read from its new
boot; an absent file is "not yet"; one budget runs from the step's start and a reboot never restarts it; the whole
file is read, in bounded chunks; the waits run on the monotonic clock, so a wall-clock step does not end them.
MeshCom's start waits on its firmware's console line, and the bounds nest."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time

import pytest

from lhpc.core import commands
from lhpc.core.lifecycle import REQUIRED_POST_TIMEOUT_S
from lhpc.core.manifest import load_manifest

LINE = "[CON ]...Console started on port 2323\n"


class _C:
    run_params = []


class _Op:
    callsign = "N0CALL"


def _launch(tmp_path, steps, binding=None):
    root = tmp_path / "rt"
    (root / "state" / "post").mkdir(parents=True, exist_ok=True)
    rp = root / "state" / "post" / "r.json"
    script = commands.render_post_launcher(steps, _C(), {}, _Op(), str(root), str(tmp_path), "",
                                           result_path=str(rp), binding=binding)
    path = tmp_path / "launcher.py"
    path.write_text(script)
    return subprocess.Popen([sys.executable, str(path)], stderr=subprocess.PIPE, text=True), rp


def _wait(tmp_path, link, timeout, required=True, binding=None, after=None):
    p, rp = _launch(tmp_path, [{"kind": "log_wait", "path": str(link), "pattern": "Console started on port 2323",
                                "timeout": timeout, "label": "firmware boot", "required": required, "optional": not required}],
                       binding)
    if after:
        threading.Thread(target=after, daemon=True).start()
    _out, err = p.communicate(timeout=timeout + 30)
    return p.returncode, json.loads(rp.read_text())["steps"][0], err


def _later(delay, fn):
    def run():
        time.sleep(delay)              # wallclock: the guest's log events on the runner's real 2 s poll clock
        fn()
    return run


def _boot(tmp_path, link, name, text=""):
    """A guest boot as run.sh makes it: a new uart-<stamp>.log and the symlink moved onto it."""
    log = tmp_path / name
    log.write_text(text)
    tmp = tmp_path / "next.lnk"
    os.symlink(log.name, tmp)
    os.replace(tmp, link)
    return log


@pytest.mark.slow                       # real waits of a few seconds
def test_the_line_written_after_a_delay_is_ready(tmp_path):
    link = tmp_path / "uart-latest.log"
    log = _boot(tmp_path, link, "uart-1.log", "rst:0x1 (POWERON_RESET)\n")
    rc, st, err = _wait(tmp_path, link, 20, after=_later(2, lambda: log.open("a").write(LINE)))
    assert (rc, st["outcome"]) == (0, "ready") and "firmware boot: ready after" in err, err


@pytest.mark.slow
def test_a_reboot_moves_the_wait_to_the_new_boots_log(tmp_path):
    link = tmp_path / "uart-latest.log"
    _boot(tmp_path, link, "uart-1.log", "rst:0x1 (POWERON_RESET)\n")            # this boot never gets the line
    rc, st, _err = _wait(tmp_path, link, 20, after=_later(2, lambda: _boot(tmp_path, link, "uart-2.log", LINE)))
    assert (rc, st["outcome"]) == (0, "ready")


@pytest.mark.slow
def test_an_absent_log_is_not_yet_and_its_creation_is_ready(tmp_path):
    link = tmp_path / "uart-latest.log"                                         # provisioning: no log at all
    rc, st, err = _wait(tmp_path, link, 20, after=_later(3, lambda: _boot(tmp_path, link, "uart-1.log", LINE)))
    assert (rc, st["outcome"]) == (0, "ready"), err


@pytest.mark.slow
def test_no_line_times_out_and_fails_a_required_step_naming_the_log(tmp_path):
    link = tmp_path / "uart-latest.log"
    _boot(tmp_path, link, "uart-1.log", "rst:0x1 (POWERON_RESET)\n")
    rc, st, err = _wait(tmp_path, link, 3)
    assert rc != 0 and st["outcome"] == "timeout" and "firmware boot: not ready after" in err and f"(log {link})" in err
    rc, st, _err = _wait(tmp_path, link, 3, required=False)
    assert (rc, st["outcome"]) == (0, "timeout")


@pytest.mark.slow
@pytest.mark.wallclock("the one budget's real end against a reboot near it is what is measured")
def test_a_reboot_near_the_end_of_the_budget_times_out_its_clock_is_not_restarted(tmp_path):
    link = tmp_path / "uart-latest.log"
    _boot(tmp_path, link, "uart-1.log")

    def reboot_then_line():
        new = _boot(tmp_path, link, "uart-2.log")               # at 3 s: the new boot, its line only at 6 s
        time.sleep(3)
        new.open("a").write(LINE)
    rc, st, _err = _wait(tmp_path, link, 5, after=_later(3, reboot_then_line))
    assert rc != 0 and st["outcome"] == "timeout"


@pytest.mark.slow
@pytest.mark.wallclock("a real main process that ends mid-wait")
def test_the_bound_main_ending_mid_wait_is_main_gone(tmp_path):
    link = tmp_path / "uart-latest.log"
    _boot(tmp_path, link, "uart-1.log")
    main = subprocess.Popen(["sleep", "60"], start_new_session=True)
    with open(f"/proc/{main.pid}/stat", "rb") as f:
        rest = f.read().rsplit(b") ", 1)[1].split()
    binding = {"main_launch_id": "m", "main_pid": main.pid, "main_starttime": int(rest[19]),
               "main_pgid": int(rest[2]), "main_sid": int(rest[3])}
    try:
        rc, st, _err = _wait(tmp_path, link, 20, binding=binding, after=_later(2, main.kill))
    finally:
        main.kill()
        main.wait()
    assert rc != 0 and st["outcome"] == "main-gone" and st["elapsed_s"] < 10      # at once, not at the budget's end


def test_a_main_gone_before_a_log_wait_records_it_as_never_run(tmp_path):
    # the bound main is already gone: an earlier optional step stops the run, and the log_wait left records main-gone
    p, rp = _launch(tmp_path, [{"kind": "exec", "argv": ["true"], "optional": True},
                               {"kind": "log_wait", "path": str(tmp_path / "x.log"), "pattern": "p", "timeout": 5,
                                "label": "firmware boot", "required": True},
                               {"kind": "exec", "argv": ["true"], "label": "after", "required": True}],
                    binding={"main_launch_id": "m", "main_pid": 999999, "main_starttime": 1, "main_pgid": 1,
                             "main_sid": 1})
    p.communicate(timeout=30)
    steps = json.loads(rp.read_text())["steps"]
    assert p.returncode != 0
    assert [(st["kind"], st["outcome"]) for st in steps] == [("exec", "main-gone"), ("log_wait", "main-gone"),
                                                             ("exec", "main-gone")], steps


@pytest.mark.parametrize("before", [100, (1 << 20) + 100, (1 << 20) - LINE.index("Console") - 10],
                         ids=["first-chunk", "after-the-first-mib", "across-the-chunk-edge"])
def test_the_line_is_found_anywhere_in_a_log(tmp_path, before):
    link = tmp_path / "uart-latest.log"
    _boot(tmp_path, link, "uart-1.log", "x" * before + LINE + "y" * (1 << 20))
    rc, st, err = _wait(tmp_path, link, 3)
    assert (rc, st["outcome"]) == (0, "ready"), err


def _endless(tmp_path):
    """A log no scan reaches the end of within a test's deadline, and a regular file (a device is never opened)."""
    path = tmp_path / "endless.log"
    with open(path, "wb") as f:
        f.truncate(1 << 40)
    return str(path)


@pytest.mark.slow                       # the 2 s budget runs on the real clock
def test_a_log_that_never_ends_stops_the_scan_at_the_deadline(tmp_path):
    """An endless log (a 1 TiB sparse regular file: every read returns a full MiB) with no line in it: the scan stops
    at the step's deadline and the wait records its timeout, instead of reading past it."""
    p, rp = _launch(tmp_path, [{"kind": "log_wait", "path": _endless(tmp_path), "pattern": "Console started on port 2323",
                                "timeout": 2, "label": "firmware boot", "required": True}])
    try:
        p.communicate(timeout=20)
    finally:
        p.kill()
    st = json.loads(rp.read_text())["steps"][0]
    assert p.returncode != 0 and st["outcome"] == "timeout" and st["elapsed_s"] < 10, st


@pytest.mark.slow
@pytest.mark.wallclock("a real main process that ends during the scan of an endless log")
def test_the_bound_main_ending_during_an_endless_scan_is_main_gone_at_once(tmp_path):
    main = subprocess.Popen(["sleep", "60"], start_new_session=True)
    with open(f"/proc/{main.pid}/stat", "rb") as f:
        rest = f.read().rsplit(b") ", 1)[1].split()
    binding = {"main_launch_id": "m", "main_pid": main.pid, "main_starttime": int(rest[19]),
               "main_pgid": int(rest[2]), "main_sid": int(rest[3])}
    try:
        rc, st, _err = _wait(tmp_path, _endless(tmp_path), 8, binding=binding, after=_later(2, main.kill))
    finally:
        main.kill()
        main.wait()
    assert rc != 0 and st["outcome"] == "main-gone" and st["elapsed_s"] < 5, st      # not at the 8 s deadline


# The wall clock steps an hour forward right after the launcher's first reading of it (a first-boot time sync): the
# rendered launcher, run under a wrapper that replaces time.time before the launcher's own code runs.
_JUMP = ("import runpy, sys, time\n_real, _n = time.time, [0]\n"
         "def _stepped():\n    _n[0] += 1\n    return _real() + (3600 if _n[0] > 1 else 0)\n"
         "time.time = _stepped\nrunpy.run_path(sys.argv[1], run_name='__main__')\n")


# A virtual monotonic clock: it moves only by the launcher's own sleeps, so a schedule of minutes runs at once.
_VIRTUAL = ("import runpy, sys, time\n_now = [0.0]\n"
            "def _sleep(s):\n    _now[0] += s\n"
            "time.monotonic, time.sleep = (lambda: _now[0]), _sleep\nrunpy.run_path(sys.argv[1], run_name='__main__')\n")


def _jumped(tmp_path, steps, wrapper=_JUMP):
    """The steps in a real launcher under `wrapper` (default: a forward wall-clock step): (rc, the step records,
    stderr)."""
    root = tmp_path / "rt"
    (root / "state" / "post").mkdir(parents=True, exist_ok=True)
    rp = root / "state" / "post" / "r.json"
    (tmp_path / "launcher.py").write_text(commands.render_post_launcher(steps, _C(), {}, _Op(), str(root),
                                                                        str(tmp_path), "", result_path=str(rp)))
    p = subprocess.run([sys.executable, "-c", wrapper, str(tmp_path / "launcher.py")], capture_output=True, text=True,
                       timeout=60, check=False)
    return p.returncode, json.loads(rp.read_text())["steps"], p.stderr


@pytest.mark.parametrize("first, second, elapsed", [("g", "g", 40.0), ("g", "h", 100.0), (None, None, 100.0)],
                         ids=["one-group", "two-groups", "no-group"])
def test_waits_that_share_a_clock_end_by_the_groups_one_deadline(tmp_path, first, second, elapsed):
    """Two waits of one `shares_clock` group: the second ends by the first's start + its timeout (60 s of the 100 s
    went on a delay between them), so the group counts once in the post-start budget; other waits keep their own."""
    (tmp_path / "boot.log").write_text(LINE)

    def wait(path, clock):
        return {"kind": "log_wait", "path": str(path), "pattern": "Console started on port 2323", "timeout": 100,
                "optional": True, **({"shares_clock": clock} if clock else {})}
    rc, steps, err = _jumped(tmp_path, [wait(tmp_path / "boot.log", first), {"kind": "delay", "seconds": 60},
                                        wait(tmp_path / "marker", second)], wrapper=_VIRTUAL)
    assert [st["outcome"] for st in steps] == ["ready", "timeout"] and steps[1]["elapsed_s"] == elapsed, (steps, err)


def test_a_shares_clock_that_is_not_a_name_is_refused():
    with pytest.raises(commands.CommandError, match="shares_clock"):
        commands.render_post_launcher([{"kind": "log_wait", "path": "/x", "pattern": "p", "timeout": 5,
                                        "shares_clock": ""}], _C(), {}, _Op(), "/rt", "/src", "")


def test_a_wall_clock_step_does_not_end_a_log_wait(tmp_path):
    (tmp_path / "uart.log").write_text(LINE)
    rc, steps, err = _jumped(tmp_path, [{"kind": "log_wait", "path": str(tmp_path / "uart.log"), "timeout": 20,
                                         "pattern": "Console started on port 2323", "required": True}])
    assert (rc, steps[0]["outcome"]) == (0, "ready") and steps[0]["elapsed_s"] < 20, (steps, err)


def test_a_wall_clock_step_does_not_stretch_an_execs_elapsed_time(tmp_path):
    rc, steps, err = _jumped(tmp_path, [{"kind": "exec", "argv": ["true"], "label": "probe", "required": True}])
    assert (rc, steps[0]["outcome"]) == (0, "ok") and steps[0]["elapsed_s"] < 20, (steps, err)


def test_a_wall_clock_step_does_not_end_a_tcp_wait(tmp_path):
    import socket
    with socket.socket() as srv:
        srv.bind(("127.0.0.1", 0))
        srv.listen(4)
        rc, steps, err = _jumped(tmp_path, [{"kind": "tcp_wait", "host": "127.0.0.1", "port": srv.getsockname()[1],
                                             "timeout": 20, "required": True}])
    assert (rc, steps[0]["outcome"]) == (0, "ready") and steps[0]["elapsed_s"] < 20, (steps, err)


def test_a_wall_clock_step_does_not_end_a_tcp_sends_window(tmp_path):
    srv, seen = _console("ok\r\n")
    try:
        rc, steps, err = _jumped(tmp_path, [{"kind": "tcp_send", "host": "127.0.0.1", "port": srv.getsockname()[1],
                                             "data": "x\n", "repeat": 2, "interval": 0.5, "required": True}])
    finally:
        srv.close()
    assert (rc, steps[0]["attempts"], len(seen)) == (0, 2, 2) and steps[0]["elapsed_s"] < 20, (steps, err)


def _meshcom_steps(rid="r1"):
    comp = next(c for s in load_manifest() for c in s.components if c.id == "meshcom-qemu")
    inst = commands.Instance(rid, band="433")
    return commands.render_post_launcher(comp.post_steps, comp, {"mc_callsign": "DL1ABC"}, _Op(), "/rt", "/src/mc", "",
                                         inst=inst)


def _rendered(script):
    return eval(script.split("STEPS = ", 1)[1].split("\n", 1)[0])          # noqa: S307 - our own rendered literal


@pytest.mark.parametrize("rid, run_dir", [("r1", ".run/"), ("r3", ".run/2/")])
def test_meshcoms_start_waits_on_its_own_guests_console_line(rid, run_dir):
    wait = next(s for s in _rendered(_meshcom_steps(rid)) if s["kind"] == "log_wait")
    assert wait["path"] == f"/src/mc/{run_dir}uart-latest.log" and wait["pattern"] == "Console started on port 2323"


def _wait_budget(raw_steps):
    """The waits' worst time: every wait's timeout summed, except waits that declare the same `shares_clock` (they end
    by one external deadline: MeshCom's, run.sh:243, :274), which count once, as their longest."""
    clocks, total = {}, 0.0
    for s in raw_steps:
        if s.get("kind") != "log_wait":
            continue
        if s.get("shares_clock"):
            clocks[s["shares_clock"]] = max(clocks.get(s["shares_clock"], 0.0), float(s["timeout"]))
        else:
            total += float(s["timeout"])
    return total + sum(clocks.values())


@pytest.mark.parametrize("rid, state_dir", [("r1", "r1/"), ("r3", "r3/")])
def test_meshcoms_start_waits_on_its_own_nodes_verified_marker_as_a_required_step(rid, state_dir):
    steps = _rendered(_meshcom_steps(rid))
    marker = next(s for s in steps if s.get("label") == "provisioning verified")
    assert marker["path"] == f"/rt/state/{state_dir}meshcom/node-flash.bin.provisioned" and marker["optional"] is False
    assert [s["label"] for s in steps].index("provisioning verified") < [s["label"] for s in steps].index("callsign")


def test_the_waits_nest_inside_the_required_post_starts_cap():
    comp = next(c for s in load_manifest() for c in s.components if c.id == "meshcom-qemu")
    send = next(s for s in _rendered(_meshcom_steps()) if s["kind"] == "tcp_send")
    assert sum(send["intervals"]) == 904.0                     # the documented worst Zero boot + 60 s
    assert _wait_budget(comp.post_steps) + sum(send["intervals"]) + 64 <= REQUIRED_POST_TIMEOUT_S


def test_only_waits_declaring_one_clock_count_once():
    w = {"kind": "log_wait", "timeout": 1300}
    assert _wait_budget([w, dict(w)]) == 2600                                      # unrelated waits still sum
    assert _wait_budget([{**w, "shares_clock": "a"}, {**w, "shares_clock": "b"}]) == 2600
    assert _wait_budget([{**w, "shares_clock": "a"}, {**w, "timeout": 900, "shares_clock": "a"}]) == 1300


def _console(replies):
    """A fake MeshCom console: answers every connection with `replies`; the connection times are recorded."""
    import socket
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(8)
    seen = []

    def serve():
        while True:
            try:
                conn, _ = srv.accept()
            except OSError:
                return
            seen.append(time.time())        # wallclock: when the launcher connects is what is measured
            try:
                conn.recv(256)
                conn.sendall(replies.encode())
            finally:
                conn.close()
    threading.Thread(target=serve, daemon=True).start()
    return srv, seen


def _meshcom_runner(tmp_path, marker_after):
    """MeshCom's real post steps (its console pointed at a fake one) in a real launcher: the boot's console line is
    in uart-latest.log; the harness's marker is written `marker_after` s after the start (0: already there, a normal
    restart). Returns (seconds from the start to each console connection, the step outcomes by label)."""
    comp = next(c for s in load_manifest() for c in s.components if c.id == "meshcom-qemu")
    srv, seen = _console("...Call: <DL1ABC>\r\n")
    steps = [{**s, "port": srv.getsockname()[1]} if s.get("kind") == "tcp_send" else dict(s) for s in comp.post_steps]
    src, root = tmp_path / "src", tmp_path / "rt"
    (src / ".run").mkdir(parents=True)
    (src / ".run" / "uart-latest.log").write_text(LINE)                            # the supervised boot's console
    marker = root / "state" / "r1" / "meshcom" / "node-flash.bin.provisioned"
    marker.parent.mkdir(parents=True)
    (root / "state" / "post").mkdir(parents=True)
    rp = root / "state" / "post" / "r.json"
    script = commands.render_post_launcher(steps, comp, {"mc_callsign": "DL1ABC"}, _Op(), str(root), str(src), "",
                                           result_path=str(rp), inst=commands.Instance("r1", band="433"))
    (tmp_path / "launcher.py").write_text(script)

    def write_marker():
        marker.write_text("2026-11-15T01:00:00Z abc\n")
    if not marker_after:
        write_marker()
    t0 = time.time()                    # wallclock: the console connections are timed from the launcher's start
    p = subprocess.Popen([sys.executable, str(tmp_path / "launcher.py")], stderr=subprocess.PIPE, text=True)
    if marker_after:
        threading.Thread(target=_later(marker_after, write_marker), daemon=True).start()
    try:
        p.communicate(timeout=90)
    finally:
        srv.close()
    return [t - t0 for t in seen], {s["label"]: s["outcome"] for s in json.loads(rp.read_text())["steps"]}


@pytest.mark.slow                       # real waits of a few seconds
@pytest.mark.wallclock("the real launcher's first console connect against the marker's appearance is measured")
def test_meshcoms_callsign_step_waits_until_the_harness_has_verified_the_node(tmp_path):
    # MESHCOM-VERIFY: on a provisioning start the console line is there while the harness still polls the console
    # (it takes over one client at a time): no console connection before the marker, the probe after it
    seen, outcomes = _meshcom_runner(tmp_path, marker_after=6)
    assert seen and min(seen) >= 6, seen                                           # nothing before the marker
    assert outcomes.get("provisioning verified") == "ready", outcomes


@pytest.mark.wallclock("the real launcher's first console connect on a kept marker is measured")
def test_a_normal_restart_with_its_marker_kept_probes_the_callsign_at_once(tmp_path):
    # G1-MCV-CODE NOTE: a restart keeps the marker (no provisioning): the wait passes on its first read and the
    # callsign probe runs right away (the runner polls every 2 s)
    seen, outcomes = _meshcom_runner(tmp_path, marker_after=0)
    assert seen and min(seen) < 4, seen
    assert outcomes.get("provisioning verified") == "ready", outcomes
    assert outcomes.get("callsign") not in (None, "timeout"), outcomes


@pytest.mark.parametrize("step, why", [
    ({"kind": "log_wait", "path": "x", "pattern": "", "timeout": 10}, "pattern"),
    ({"kind": "log_wait", "path": "x", "pattern": "p", "timeout": REQUIRED_POST_TIMEOUT_S + 1}, "timeout"),
    ({"kind": "log_wait", "path": "x", "pattern": "p", "timeout": 0}, "timeout"),
    ({"kind": "log_wait", "pattern": "p", "timeout": 10}, "path"),                     # no path
    ({"kind": "log_wait", "path": "x", "pattern": "p", "timeout": "1300"}, "timeout"),  # a string, not a number
    ({"kind": "log_wait", "path": "x", "pattern": "p", "timeout": True}, "timeout"),    # a bool is no number here
])
def test_a_log_wait_without_its_fields_or_beyond_the_cap_is_refused(step, why):
    with pytest.raises(commands.CommandError, match=why):
        commands.render_post_launcher([step], _C(), {}, _Op(), "/rt", "/src", "")


def test_meshcoms_guest_gets_the_longer_provisioning_deadline(tmp_path):
    comp = next(c for s in load_manifest() for c in s.components if c.id == "meshcom-qemu")
    env = commands.build_env(comp.run_env, str(tmp_path), "/src/mc", "", inst=commands.Instance("r1"))
    assert env.get("PROVISION_TIMEOUT") == "1200"


# ---- One cancellable scan (gate-2 R3): the rendered launcher under a hook wrapper. The wrapper counts every chunk read
# of the log (a read of 64 KiB or more, through open().read or os.pread alike), can act before or after the n-th one
# (block it, move the symlink, rewrite the file in place), can run a virtual monotonic clock (moved by sleeps, by a
# cost per chunk read, per connect and per clock reading), can kill the bound main at a virtual time or right after the
# first main check that follows the chunk read holding the line, and writes the opens of the log path and its virtual
# end to a trace file.
_HOOKED = r'''
import builtins, json, os, runpy, signal, socket, sys, threading, time
cfg = json.loads(sys.argv[2])
_now, _n, _hit, _killed = [0.0], [0], [False], [False]
_main, _logs, _pat = cfg.get("main_pid"), set(cfg.get("logs", [])), cfg.get("pattern", "").encode()
_trace = open(cfg["trace"], "a", buffering=1)
def _kill():
    if _main and not _killed[0]:
        _killed[0] = True
        os.kill(_main, signal.SIGKILL)
        for _ in range(100000):                     # until the kernel shows it dead: the virtual clock never waits
            try:
                with _ropen("/proc/%d/stat" % _main, "rb") as f:
                    if f.read().rsplit(b") ", 1)[1].split()[0] in (b"Z", b"X"):
                        break
            except OSError:
                break
if cfg.get("virtual"):
    def _mono():
        t = _now[0]
        _now[0] += cfg.get("delta", 0.0)
        if cfg.get("kill_at") is not None and t >= cfg["kill_at"]:
            _kill()
        return t
    def _sleep(s):
        if s < 0 and cfg.get("strict_sleep"):
            raise ValueError("sleep length must be non-negative")      # the real sleep's rule
        _now[0] += max(0.0, s)
    time.monotonic, time.sleep = _mono, _sleep
    _cc = socket.create_connection
    def _connect(*a, **k):
        _trace.write("connect %.2f\n" % _now[0])
        _now[0] += cfg.get("connect_cost", 0.0)
        return _cc(*a, **k)
    socket.create_connection = _connect
def _do(a):
    if a[0] == "retarget":
        os.symlink(a[2], a[1] + ".next")
        os.replace(a[1] + ".next", a[1])
    elif a[0] == "rewrite":
        with _ropen(a[1], "r+b") as f:
            f.truncate(0)
            f.write(b"".join(c.encode() * k for c, k in a[2]))
    elif a[0] == "kill":
        _kill()
    elif a[0] == "remove":
        os.unlink(a[1])
    elif a[0] == "arm":                           # the actions to run after the next stat or pin of the link
        _armed[:] = a[1]
def _act(when):
    acts = cfg.get("on_read", {}).get(str(_n[0]))
    if not acts:
        return
    for a in (acts if isinstance(acts[0], list) else [acts]):
        if (a[0] == "block") != (when == "block"):
            continue
        if a[0] == "block":
            threading.Event().wait()
        else:
            _do(a)
_armed = list(cfg.get("arm_at_start", []))
def _touched(p):
    if _armed and p == cfg.get("link"):
        acts, _armed[:] = list(_armed), []
        for a in acts:
            _do(a)
def _chunk(read):
    _n[0] += 1
    _trace.write("read %.2f\n" % _now[0])
    _act("block")
    d = read()
    _trace.write("got %d %d\n" % (len(d), int(bool(_pat) and _pat in d)))
    if cfg.get("virtual"):
        _now[0] += cfg.get("read_cost", 0.0)
    if _pat and _pat in d:
        _hit[0] = True
    if cfg.get("append_each"):                    # a writer appending to the log during every read
        with _ropen(cfg["append_each"], "ab") as f:
            f.write(b"z" * 61)
    _act("after")
    return d
class _Log:
    def __init__(s, f): s.f = f
    def read(s, n=-1): return _chunk(lambda: s.f.read(n)) if n < 0 or n >= 65536 else s.f.read(n)
    def __enter__(s): return s
    def __exit__(s, *a): s.f.close()
    def __getattr__(s, k): return getattr(s.f, k)
class _Stat:
    def __init__(s, f): s.f = f
    def read(s, n=-1):
        d = s.f.read(n)
        _kill()
        return d
    def __enter__(s): return s
    def __exit__(s, *a): s.f.close()
    def __getattr__(s, k): return getattr(s.f, k)
_ropen, _rpread, _rosopen = builtins.open, os.pread, os.open
def _open(p, *a, **k):
    if isinstance(p, str) and p in _logs:
        _trace.write("open %s\n" % p)
        _now[0] += cfg.get("open_cost", 0.0)
    f = _ropen(p, *a, **k)
    if isinstance(p, str) and p in _logs:
        return _Log(f)
    if _main and cfg.get("kill_after_hit") and _hit[0] and p == "/proc/%d/stat" % _main:
        return _Stat(f)
    return f
def _osopen(p, flags=os.O_RDONLY, *a, **k):
    if isinstance(p, str) and p in _logs:
        _now[0] += cfg.get("open_cost", 0.0)
    if isinstance(p, str) and p.startswith("/proc/self/fd/") and os.path.realpath(p) in _logs:
        _now[0] += cfg.get("reopen_cost", 0.0)            # the readable reopen through the pin
    if isinstance(p, str) and not flags & getattr(os, "O_PATH", 0):     # a pin opens nothing: not traced
        target = os.path.realpath(p)
        if target in _logs or p in _logs:
            _trace.write("open %s\n" % (target if target in _logs else p))
    fd = _rosopen(p, flags, *a, **k)
    if isinstance(p, str) and flags & getattr(os, "O_PATH", 0):
        _touched(p)
    return fd
_rstat, _before = os.stat, [list(cfg.get("before_stat", []))]
def _stat(p, *a, **k):
    if _before[0] and p == cfg.get("link"):       # actions just before the post-read look at the path, once
        acts, _before[0] = _before[0], []
        for a_ in acts:
            _do(a_)
    if cfg.get("trace_stat") and p == cfg.get("link"):
        _trace.write("stat %.2f\n" % _now[0])
    r = _rstat(p, *a, **k)
    if isinstance(p, str):
        _touched(p)
    return r
def _pread(fd, n, off):
    if cfg.get("short"):
        n = min(n, cfg["short"])                          # the kernel may return less than asked
    counted = n >= 65536 or off == 0 or cfg.get("short")  # with short pieces, every piece is a read
    return _chunk(lambda: _rpread(fd, n, off)) if counted else _rpread(fd, n, off)
_rclose, _eio = os.close, [False]
def _close(fd):
    target = ""
    try:
        target = os.readlink("/proc/self/fd/%d" % fd)
    except OSError:
        pass
    _rclose(fd)
    if cfg.get("close_eio") and not _eio[0] and target in _logs:
        _eio[0] = True
        raise OSError(5, "Input/output error")            # released, but the close reports an error
def _leaks():
    for name in os.listdir("/proc/self/fd"):
        try:
            t = os.readlink("/proc/self/fd/" + name)
        except OSError:
            continue
        if t in _logs or os.path.realpath(t) in _logs:
            _trace.write("leak %s\n" % t)
builtins.open, os.open, os.pread, os.stat, os.close = _open, _osopen, _pread, _stat, _close
import atexit
atexit.register(lambda: (_leaks(), _trace.write("end %.1f\n" % _now[0])))
runpy.run_path(sys.argv[1], run_name="__main__")
'''


def _hooked(tmp_path, steps, binding=None, limit=20, source=None, **cfg):
    """The steps in a real launcher under the hook wrapper: (rc, the step records, stderr, the trace lines). A launcher
    that is still running at `limit` s is a hang: the test fails on it."""
    root = tmp_path / "rt"
    (root / "state" / "post").mkdir(parents=True, exist_ok=True)
    rp = root / "state" / "post" / "r.json"
    (tmp_path / "launcher.py").write_text(commands.render_post_launcher(steps, _C(), {}, _Op(), str(root),
                                                                        str(source or tmp_path), "",
                                                                        result_path=str(rp), binding=binding))
    trace = tmp_path / "trace"
    cfg = {"trace": str(trace), **cfg}
    if binding:
        cfg["main_pid"] = binding["main_pid"]
    try:
        p = subprocess.run([sys.executable, "-c", _HOOKED, str(tmp_path / "launcher.py"), json.dumps(cfg)],
                           capture_output=True, text=True, timeout=limit, check=False)
    except subprocess.TimeoutExpired:
        pytest.fail(f"the launcher still ran after {limit} s (a hang)")
    return p.returncode, json.loads(rp.read_text())["steps"], p.stderr, trace.read_text().splitlines()


def _bound_main():
    main = subprocess.Popen(["sleep", "60"], start_new_session=True)  # wallclock: a main to bind to; killed, never waited out
    with open(f"/proc/{main.pid}/stat", "rb") as f:
        rest = f.read().rsplit(b") ", 1)[1].split()
    return main, {"main_launch_id": "m", "main_pid": main.pid, "main_starttime": int(rest[19]),
                  "main_pgid": int(rest[2]), "main_sid": int(rest[3])}


def _lw(path, timeout, pattern="Console started on port 2323", **kw):
    return {"kind": "log_wait", "path": str(path), "pattern": pattern, "timeout": timeout, "label": "firmware boot",
            **({"optional": True} if kw.pop("optional", False) else {"required": True}), **kw}


_C1 = 1 << 20


@pytest.mark.parametrize("a_text, b_text, outcome", [
    ("x" * (10 * _C1), LINE, "ready"),                             # B1: the old log would eat the budget
    ("x" * (_C1 + 100) + LINE, "rst:0x1\n", "timeout"),            # B1 converse: the old log's line is obsolete
    (LINE, "rst:0x1\n", "timeout"),                                 # ... also when the chunk read holds it
], ids=["moved-to-the-ready-log", "obsolete-line-in-the-old-log", "obsolete-line-in-the-chunk-read"])
def test_a_log_moved_mid_scan_is_followed_and_the_old_ones_line_never_counts(tmp_path, a_text, b_text, outcome):
    link = tmp_path / "uart-latest.log"
    _boot(tmp_path, link, "uart-1.log", a_text)
    (tmp_path / "uart-2.log").write_text(b_text)
    rc, steps, err, _t = _hooked(tmp_path, [_lw(link, 5)], virtual=True, read_cost=1.0, logs=[str(link)],
                                 on_read={"1": ["retarget", str(link), "uart-2.log"]})
    assert steps[0]["outcome"] == outcome, (steps, err)


def test_a_group_wait_on_an_absent_log_does_not_sleep_past_its_own_deadline(tmp_path):
    """B2: the first wait of group g ends by its 0.1 s, so the second, which shares g's 1 s, still reads its log."""
    (tmp_path / "boot.log").write_text(LINE)
    rc, steps, err, _t = _hooked(tmp_path, [_lw(tmp_path / "absent.log", 0.1, optional=True, shares_clock="g"),
                                            _lw(tmp_path / "boot.log", 1, shares_clock="g")], virtual=True)
    assert (rc, [st["outcome"] for st in steps]) == (0, ["timeout", "ready"]), (steps, err)


@pytest.mark.parametrize("kind", ["fifo-no-writer", "fifo-held-open", "pty"])
def test_a_path_that_is_not_a_regular_file_is_never_opened_and_the_wait_keeps_its_deadline(tmp_path, kind):
    """B3 + D1: a FIFO (no writer: open blocks; a writer holding it: a read blocks) or a tty (opening it would make it
    the session leader's controlling terminal, a serial port's open resets a node) is never opened; it is said
    once per step, however many polls see it."""
    held = []
    if kind == "pty":
        master, slave = os.openpty()
        held += [master, slave]
        path = str(tmp_path / "uart.log")
        os.symlink(os.ttyname(slave), path)          # the leaf symlink leads to a tty
    else:
        path = str(tmp_path / "uart.log")
        os.mkfifo(path)
        if kind == "fifo-held-open":
            w = os.open(path, os.O_RDWR | os.O_NONBLOCK)
            os.write(w, LINE.encode())
            held.append(w)
    try:
        rc, steps, err, trace = _hooked(tmp_path, [_lw(path, 5)], logs=[path, os.path.realpath(path)], limit=15,
                                        virtual=True)
    finally:
        for fd in held:
            os.close(fd)
    assert (rc, steps[0]["outcome"], steps[0]["elapsed_s"]) == (1, "timeout", 5.0) and steps[0]["attempts"] >= 2, steps
    said = "is outside the runtime and source roots" if kind == "pty" else "is not a regular file"
    assert err.count(said) == 1 and not [ln for ln in trace if ln.startswith("open ")], (err, trace)  # rule-10: a trace line's first word is its kind


@pytest.mark.wallclock("a 3 s real deadline bounds the scan of the rewritten log")
def test_a_log_truncated_and_regrown_in_place_never_joins_old_and_new_bytes(tmp_path):
    """B4: the first MiB ends in READY; then the file is rewritten in place to C bytes of y + OK. Neither version
    holds READYOK; the old tail plus the new chunk would."""
    log = tmp_path / "uart.log"
    log.write_text("x" * (_C1 - 5) + "READY")
    rc, steps, err, _t = _hooked(tmp_path, [_lw(log, 3, pattern="READYOK")], logs=[str(log)],
                                 on_read={"1": ["rewrite", str(log), [["y", _C1], ["OK", 1]]]})
    assert steps[0]["outcome"] == "timeout", (steps, err)


@pytest.mark.wallclock("a 5 s real deadline bounds the wait")
def test_a_log_rewritten_in_place_with_the_line_before_the_read_offset_is_read_again(tmp_path):
    """Pin (green before the fix too: it re-read from 0 on every poll): after a full pass the log is rewritten in
    place, same inode, at least as long, with the line in its first MiB; the scan starts again at 0 and finds it."""
    log = tmp_path / "uart.log"
    log.write_text("a" * (2 * _C1 - 1) + "\n")          # the rewrite keeps the byte before the offset: a line end
    rest = 2 * _C1 - 1 - 100 - len(LINE)
    rc, steps, err, _t = _hooked(tmp_path, [_lw(log, 5)], logs=[str(log)],
                                 on_read={"1": ["rewrite", str(log), [["b", 100], [LINE, 1], ["b", rest], ["\n", 1],
                                                                       ["b", _C1]]]})
    assert (rc, steps[0]["outcome"]) == (0, "ready"), (steps, err)


def test_a_line_found_only_after_the_deadline_is_not_ready(tmp_path):
    """B5 (F8): the read that holds the line starts in time and ends after the 1 s deadline (it costs 2 s): the read
    happened, the line was in it, and the wait still timed out."""
    log = tmp_path / "boot.log"
    log.write_text(LINE)
    rc, steps, err, trace = _hooked(tmp_path, [_lw(log, 1)], virtual=True, read_cost=2.0, logs=[str(log)],
                                    pattern="Console started on port 2323")
    reads = [ln for ln in trace if ln.startswith(("read ", "got "))]  # rule-10: a trace line's first word is its kind
    assert reads == ["read 0.00", f"got {len(LINE)} 1"], trace           # the read happened and held the line
    assert (rc, steps[0]["outcome"]) == (1, "timeout"), (steps, err)


@pytest.mark.slow
@pytest.mark.wallclock("a real main process that dies between the last check and the commit")
def test_a_line_found_after_main_died_is_not_ready(tmp_path):
    """B5: the bound main dies during the read that holds the line: no ready."""
    (tmp_path / "boot.log").write_text(LINE)
    main, binding = _bound_main()
    try:
        rc, steps, err, _t = _hooked(tmp_path, [_lw(tmp_path / "boot.log", 5)], binding=binding,
                                     logs=[str(tmp_path / "boot.log")], on_read={"1": ["kill"]})
    finally:
        main.kill()
        main.wait()
    assert (rc, steps[0]["outcome"]) == (1, "main-gone"), (steps, err)


@pytest.mark.parametrize("wait", ["log_wait", "tcp_wait"])
def test_main_lost_mid_wait_stops_the_rest_at_once(tmp_path, wait):
    """B6 (+ the same shape in tcp_wait): an optional wait, a 60 s delay, a required exec; main dies at virtual t=1.
    The wait records main-gone, the delay never runs, the exec is recorded as never run, and the launcher fails."""
    main, binding = _bound_main()
    port = _closed_port()
    first = (_lw(tmp_path / "absent.log", 10, optional=True) if wait == "log_wait" else
             {"kind": "tcp_wait", "host": "127.0.0.1", "port": port, "timeout": 10, "optional": True})
    try:
        rc, steps, err, trace = _hooked(tmp_path, [first, {"kind": "delay", "seconds": 60},
                                                   {"kind": "exec", "argv": ["true"], "label": "after",
                                                    "required": True}],
                                        binding=binding, virtual=True, kill_at=1.0)
    finally:
        main.kill()
        main.wait()
    end = float(trace[-1].split()[1])
    assert rc == 1 and [st["outcome"] for st in steps] == ["main-gone", "main-gone"] and end < 5, (steps, trace, err)


def _closed_port():
    import socket
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_a_tcp_wait_does_not_sleep_past_its_deadline(tmp_path):
    rc, steps, err, _t = _hooked(tmp_path, [{"kind": "tcp_wait", "host": "127.0.0.1", "port": _closed_port(),
                                             "timeout": 1, "required": True}], virtual=True)
    assert (steps[0]["outcome"], steps[0]["elapsed_s"]) == ("timeout", 1.0), (steps, err)


def test_a_tcp_connect_that_completes_after_the_deadline_is_not_ready(tmp_path):
    import socket
    with socket.socket() as srv:
        srv.bind(("127.0.0.1", 0))
        srv.listen(4)
        rc, steps, err, _t = _hooked(tmp_path, [{"kind": "tcp_wait", "host": "127.0.0.1",
                                                 "port": srv.getsockname()[1], "timeout": 1, "required": True}],
                                     virtual=True, connect_cost=2.0)
    assert (rc, steps[0]["outcome"]) == (1, "timeout"), (steps, err)


@pytest.mark.parametrize("timeout", [0.35, 0.6, 0.65, 0.7])
@pytest.mark.parametrize("wait", ["log_wait", "tcp_wait"])
def test_a_deadline_that_passes_between_the_check_and_the_sleep_still_records_the_wait(tmp_path, wait, timeout):
    """Every clock reading costs 0.1 s and the sleep keeps the real one's rule (a negative length raises): when the
    deadline passes between the last check and the sleep's own reading, the wait still records its timeout."""
    first = (_lw(tmp_path / "absent.log", timeout) if wait == "log_wait" else
             {"kind": "tcp_wait", "host": "127.0.0.1", "port": _closed_port(), "timeout": timeout, "required": True})
    rc, steps, err, _t = _hooked(tmp_path, [first], virtual=True, delta=0.1, strict_sleep=True)
    assert (rc, [st["outcome"] for st in steps]) == (1, ["timeout"]), (steps, err)


@pytest.mark.parametrize("slow", ["open_cost", "reopen_cost"], ids=["the-pin", "the-readable-reopen"])
def test_no_read_is_requested_after_a_slow_open_used_up_the_deadline(tmp_path, slow):
    """B3 (R3d): pinning the log, or reopening it readable through the pin, takes 2 s of a 1 s budget; no read of
    it starts after the deadline: each read's start is traced on the virtual clock."""
    log = tmp_path / "uart.log"
    log.write_text("x" * (3 * _C1))
    rc, steps, err, trace = _hooked(tmp_path, [_lw(log, 1)], virtual=True, logs=[str(log)], **{slow: 2.0})
    late = [ln for ln in trace if ln.startswith("read ") and float(ln.split()[1]) >= 1.0]
    assert steps[0]["outcome"] == "timeout" and not late, (steps, trace, err)


def test_a_rotation_away_and_back_with_the_line_now_at_the_start_is_read_again(tmp_path):
    """F1: A is read once (no line); the link moves to B and, after the next look at the path, back to A, which was
    rewritten meanwhile with the line at its start, same size and same last bytes. The scan reads A again from 0."""
    link = tmp_path / "uart-latest.log"
    a = _boot(tmp_path, link, "uart-1.log", "a" * _C1)
    (tmp_path / "uart-2.log").write_text("rst:0x1\n")
    back = [["rewrite", str(a), [["READY", 1], ["a", _C1 - 5]]], ["retarget", str(link), "uart-1.log"]]
    rc, steps, err, _t = _hooked(tmp_path, [_lw(link, 7, pattern="READY")], virtual=True, read_cost=0.1,
                                 logs=[str(link)], link=str(link),
                                 on_read={"1": [["retarget", str(link), "uart-2.log"], ["arm", back]]})
    assert (rc, steps[0]["outcome"]) == (0, "ready"), (steps, err)


def test_a_same_size_rewrite_that_keeps_the_last_bytes_is_read_again(tmp_path):
    """F2: the log is read to its end; then rewritten in place, same inode and size, READY at offset 0 and the same
    last bytes. The next pass reads it from 0."""
    log = tmp_path / "uart.log"
    log.write_text("a" * (2 * _C1))
    rc, steps, err, _t = _hooked(tmp_path, [_lw(log, 7, pattern="READY")], virtual=True, read_cost=0.1,
                                 logs=[str(log)], on_read={"1": ["rewrite", str(log), [["READY", 1], ["a", 2 * _C1 - 5]]]})
    assert (rc, steps[0]["outcome"]) == (0, "ready"), (steps, err)


@pytest.mark.parametrize("text, outcome", [(LINE, "ready"), ("rst:0x1\n", "timeout")])
def test_no_descriptor_of_the_log_is_left_open(tmp_path, text, outcome):
    """F5: after a ready and after a timeout, the launcher holds no descriptor of the log."""
    log = tmp_path / "uart.log"
    log.write_text(text)
    rc, steps, err, trace = _hooked(tmp_path, [_lw(log, 3, optional=True), {"kind": "delay", "seconds": 1}],
                                    virtual=True, logs=[str(log)])
    assert steps[0]["outcome"] == outcome and not [ln for ln in trace if ln.startswith("leak ")], (steps, trace)  # rule-10: a trace line's first word is its kind


def test_a_tty_swapped_in_after_the_path_was_looked_at_is_never_opened(tmp_path):
    """F7: the path names a regular log when it is first looked at (stat or pin); right then the link moves to a
    tty. The tty is never opened."""
    master, slave = os.openpty()
    tty = os.ttyname(slave)
    try:
        link = tmp_path / "uart-latest.log"
        _boot(tmp_path, link, "uart-1.log", "rst:0x1\n")
        os.symlink(tty, tmp_path / "tty.lnk")
        rc, steps, err, trace = _hooked(tmp_path, [_lw(link, 3)], virtual=True, link=str(link),
                                        logs=[str(link), tty], limit=15,
                                        arm_at_start=[["retarget", str(link), "tty.lnk"]])
    finally:
        os.close(master)
        os.close(slave)
    assert f"open {tty}" not in trace and steps[0]["outcome"] == "timeout", (steps, trace, err)


@pytest.mark.parametrize("at, outcome", [((16 << 20) - len(LINE) - 1, "ready"), ((16 << 20) + 10, "timeout")],
                         ids=["line-just-inside", "line-just-beyond"])
def test_a_log_larger_than_the_cap_is_scanned_to_the_cap_and_says_so(tmp_path, at, outcome):
    """A 32 MiB log: a line ending just inside the first 16 MiB is found; one just beyond is not, and the wait
    says it scanned only the first 16 MiB."""
    log = tmp_path / "uart.log"
    with open(log, "wb") as f:
        f.truncate(32 << 20)
        f.seek(at)
        f.write(LINE.encode())
    rc, steps, err, _t = _hooked(tmp_path, [_lw(log, 1)], virtual=True, logs=[str(log)])
    assert steps[0]["outcome"] == outcome, (steps, err)
    if outcome == "timeout":
        assert steps[0]["detail"] == "scanned the first 16 MiB of a larger log", steps
        assert "scanned the first 16 MiB of a larger log" in err, err


def test_a_pinned_file_outside_the_roots_is_never_read(tmp_path):
    """The path lies under the source root, but its leaf symlink leads outside both roots: the pinned file is not
    read (said once), even though it holds the line."""
    outside = tmp_path.parent / (tmp_path.name + "-outside.log")
    outside.write_text(LINE)
    link = tmp_path / "uart-latest.log"
    os.symlink(outside, link)
    rc, steps, err, trace = _hooked(tmp_path, [_lw(link, 3)], virtual=True, logs=[str(link), str(outside)])
    assert steps[0]["outcome"] == "timeout" and err.count("is outside the runtime and source roots") == 1, (steps, err)
    assert not [ln for ln in trace if ln.startswith(("open ", "read "))], trace  # rule-10: a trace line's first word is its kind


@pytest.mark.parametrize("step, why", [
    ({"kind": "log_wait", "path": "/etc/hostname", "pattern": "p", "timeout": 5}, "under the runtime or source root"),
    ({"kind": "log_wait", "path": "{source}/../x.log", "pattern": "p", "timeout": 5}, "under the runtime or source root"),
    ({"kind": "log_wait", "path": "/srcx/x.log", "pattern": "p", "timeout": 5}, "under the runtime or source root"),
    ({"kind": "tcp_wait", "port": 1, "host": "localhost"}, "IP address"),
    ({"kind": "tcp_wait", "port": 1, "host": 2130706433}, "IP address"),      # an int: no connect would take it
    ({"kind": "tcp_send", "port": 1, "host": "example.org", "data": "x"}, "IP address"),
])
def test_a_wait_outside_the_roots_or_on_a_host_name_is_refused(step, why):
    with pytest.raises(commands.CommandError, match=why):
        commands.render_post_launcher([step], _C(), {}, _Op(), "/rt", "/src", "")


def test_a_source_root_linked_elsewhere_still_counts_as_the_source_root(tmp_path):
    """Adopt-by-link: the stack's source dir is a symlink to a checkout elsewhere; its log, pinned there, lies under
    the source root as the renderer resolves it, so the wait reads it."""
    elsewhere = tmp_path.parent / (tmp_path.name + "-checkout")
    (elsewhere / ".run").mkdir(parents=True)
    (elsewhere / ".run" / "uart.log").write_text(LINE)
    src = tmp_path / "src"
    os.symlink(elsewhere, src)
    rc, steps, err, _t = _hooked(tmp_path, [_lw(src / ".run" / "uart.log", 3)], virtual=True, source=str(src))
    assert (rc, steps[0]["outcome"]) == (0, "ready"), (steps, err)


def test_a_log_read_in_short_pieces_is_read_to_its_end_before_deciding(tmp_path):
    """The kernel may return less than asked: the read goes on to the size (or EOF) and the line in the pieces
    counts."""
    log = tmp_path / "uart.log"
    log.write_text(LINE + "y" * 1000)
    rc, steps, err, _t = _hooked(tmp_path, [_lw(log, 3)], virtual=True, logs=[str(log)], short=16)
    assert (rc, steps[0]["outcome"]) == (0, "ready"), (steps, err)


def test_a_log_rewritten_during_the_read_is_judged_by_a_consistent_read(tmp_path):
    """B1/B4: the read holds the line, but the file is rewritten in place (same inode and size, a new mtime) while
    it is read: that read is not one version, so it is read again, and the new version has no line."""
    log = tmp_path / "uart.log"
    log.write_text(LINE + "y" * 100)
    n = len(LINE) + 100
    rc, steps, err, _t = _hooked(tmp_path, [_lw(log, 3)], virtual=True, logs=[str(log)],
                                 on_read={"1": ["rewrite", str(log), [["x", n]]]})
    assert steps[0]["outcome"] == "timeout", (steps, err)


def test_an_error_closing_one_descriptor_still_closes_the_other_and_keeps_the_record(tmp_path):
    """F5: closing the readable descriptor reports EIO (a flush error); the pin is closed all the same and the wait
    records its outcome."""
    log = tmp_path / "uart.log"
    log.write_text(LINE)
    rc, steps, err, trace = _hooked(tmp_path, [_lw(log, 3, optional=True), {"kind": "delay", "seconds": 1}],
                                    virtual=True, logs=[str(log)], close_eio=True)
    assert [st["kind"] for st in steps] == ["log_wait"] and steps[0]["outcome"] in ("ready", "timeout"), (steps, err)
    assert len([ln for ln in err.splitlines() if "failed: Input/output error" in ln]) == 1, err
    assert not [ln for ln in trace if ln.startswith("leak ")], trace  # rule-10: a trace line's first word is its kind


def test_an_expired_budget_starts_no_connect(tmp_path):
    """The stop check passes at 0.5 s of a 1 s budget, but by the connect's own reading the budget is spent: no
    connect starts (not even one with a token timeout)."""
    rc, steps, err, trace = _hooked(tmp_path, [{"kind": "tcp_wait", "host": "127.0.0.1", "port": _closed_port(),
                                                "timeout": 1, "required": True}], virtual=True, delta=0.5)
    assert steps[0]["outcome"] == "timeout" and not [ln for ln in trace if ln.startswith("connect ")], (steps, trace)  # rule-10: a trace line's first word is its kind


def test_a_log_appended_to_during_every_read_is_still_read(tmp_path):
    """R1: the UART log grows by appends (QEMU's output, `>>`), here during every read; an append keeps the bytes read
    before it valid, so the line at the log's start counts."""
    log = tmp_path / "uart.log"
    log.write_text(LINE + "y" * 600_000)
    rc, steps, err, _t = _hooked(tmp_path, [_lw(log, 3)], virtual=True, logs=[str(log)], append_each=str(log))
    assert (rc, steps[0]["outcome"]) == (0, "ready"), (steps, err)


def test_a_log_rewritten_after_the_read_before_the_path_is_looked_at_again_is_read_again(tmp_path):
    """R2: the read holds the line; then, before the path is looked at again, the file is rewritten in place (same
    inode and size) without it: the line read is no longer the file's, and the next read has none."""
    log = tmp_path / "uart.log"
    log.write_text(LINE + "y" * 100)
    rc, steps, err, _t = _hooked(tmp_path, [_lw(log, 3)], virtual=True, logs=[str(log)], link=str(log),
                                 before_stat=[["rewrite", str(log), [["x", len(LINE) + 100]]]])
    assert steps[0]["outcome"] == "timeout", (steps, err)


def test_no_piece_of_a_read_starts_after_the_deadline(tmp_path):
    """R3: a read returned in short pieces, each taking 0.3 s of a 1 s budget: the stop check runs before every piece,
    so none starts at or after the deadline."""
    log = tmp_path / "uart.log"
    log.write_text("y" * 200)
    rc, steps, err, trace = _hooked(tmp_path, [_lw(log, 1)], virtual=True, logs=[str(log)], short=16, read_cost=0.3)
    starts = [float(ln.split()[1]) for ln in trace if ln.startswith("read ")]  # rule-10: a trace line's first word is its kind
    assert steps[0]["outcome"] == "timeout" and starts and max(starts) < 1.0, (steps, trace)


def test_a_read_that_overlaps_a_rewrite_is_read_once_more_in_the_same_pass(tmp_path):
    """R4: the first read overlaps an in-place rewrite (same size, a new mtime) that puts the line in: the file is read
    once more in the same pass, and that consistent read holds the line - ready at the first attempt."""
    log = tmp_path / "uart.log"
    log.write_text("x" * 200)
    rc, steps, err, _t = _hooked(tmp_path, [_lw(log, 5)], virtual=True, logs=[str(log)],
                                 on_read={"1": ["rewrite", str(log), [[LINE, 1], ["y", 200 - len(LINE)]]]})
    assert (rc, steps[0]["outcome"], steps[0]["attempts"]) == (0, "ready", 1), (steps, err)


def test_a_log_truncated_during_the_read_is_read_again_and_counts(tmp_path):
    """R5: the log shrinks during a read returned in pieces: the read stops at the new end (no spinning on empty
    reads), is not taken as the file's version, and the read that follows finds the line."""
    log = tmp_path / "uart.log"
    log.write_text(LINE + "y" * (1 << 17))
    rc, steps, err, _t = _hooked(tmp_path, [_lw(log, 3)], virtual=True, logs=[str(log)], short=1 << 16,
                                 read_cost=0.01, on_read={"1": ["rewrite", str(log), [[LINE, 1]]]})
    assert (rc, steps[0]["outcome"]) == (0, "ready"), (steps, err)


def test_no_look_at_the_path_starts_after_the_deadline(tmp_path):
    """N1: the read that holds the line ends past the deadline: the path is not looked at again (no stat after the
    stop), and the step ends in timeout."""
    log = tmp_path / "uart.log"
    log.write_text(LINE)
    rc, steps, err, trace = _hooked(tmp_path, [_lw(log, 1)], virtual=True, logs=[str(log)], link=str(log),
                                    trace_stat=True, read_cost=1.5)
    stats = [ln for ln in trace if ln.startswith("stat ")]  # rule-10: a trace line's first word is its kind
    assert steps[0]["outcome"] == "timeout" and stats == [], (steps, trace)


def test_a_rotated_log_leaves_no_old_size_in_the_record(tmp_path):
    """H3: the first pass reads a log larger than the cap; the path then names no file. The timeout record and its
    stderr line speak of what the last pass saw, not of the old file's size."""
    log = tmp_path / "uart.log"
    log.write_bytes(b"y" * ((16 << 20) + 1))              # one byte over the launcher's _CAP
    rc, steps, err, _t = _hooked(tmp_path, [_lw(log, 3)], virtual=True, logs=[str(log)],
                                 on_read={"1": ["remove", str(log)]})
    assert steps[0]["outcome"] == "timeout" and "detail" not in steps[0] and "MiB" not in err, (steps, err)


def _guest(behavior):
    """A fake console: each connection's first line goes to `behavior(line)`, whose return is the reply (then the
    connection is closed). Returns (port, the lines received, stop)."""
    import socket
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(8)
    got = []

    def serve():
        while True:
            try:
                conn, _ = srv.accept()
            except OSError:
                return
            with conn:
                line = conn.recv(256).decode()
                got.append(line)
                conn.sendall(behavior(line).encode())
    threading.Thread(target=serve, daemon=True).start()
    return srv.getsockname()[1], got, srv.close


def _send(port, **kw):
    return {"kind": "tcp_send", "host": "127.0.0.1", "port": port, "data": "--setcall DL1AB-1\n", "required": True,
            "label": "callsign", **kw}


def test_no_send_reaches_a_main_replaced_during_the_probe(tmp_path):
    """H1: the main ends while the console answers the probe (a read of up to 30 s): no --setcall is sent to what
    runs now, and the required step ends main-gone."""
    main, binding = _bound_main()
    try:
        port, got, stop = _guest(lambda ln: (main.kill(), main.wait(), "Call: <OTHER>\r\n")[2] if ln.startswith("--info")
                                 else "Call:DL1AB-1 Short:X set\n")
        rc, steps, err, _t = _hooked(tmp_path, [_send(port, probe="--info\n", probe_value="Call: <([^>]*)>",
                                                      probe_expect="DL1AB-1", stop_on="Call:DL1AB-1")], binding)
        stop()
    finally:
        main.kill()
        main.wait()
    assert (rc, steps[0]["outcome"]) == (1, "main-gone") and not [g for g in got if g.startswith("--setcall")], \
        (steps, got, err)


def test_an_ack_from_a_main_gone_during_the_read_is_not_counted(tmp_path):
    """H2: the main ends while the console sends its ACK: the step ends main-gone, not acked (exit 1, not 0)."""
    main, binding = _bound_main()
    try:
        port, got, stop = _guest(lambda ln: (main.kill(), main.wait(), "Call:DL1AB-1 Short:X set\n")[2])
        rc, steps, err, _t = _hooked(tmp_path, [_send(port, stop_on="Call:DL1AB-1")], binding)
        stop()
    finally:
        main.kill()
        main.wait()
    assert (rc, steps[0]["outcome"]) == (1, "main-gone"), (steps, err)


def test_an_exec_whose_main_ended_meanwhile_is_not_ok(tmp_path):
    """H2: a required exec completes (rc 0) after the main has ended: its result is not the main's, main-gone. The exec
    returns only once the main has exited (its pidfd turns readable), so the order is fixed, not raced."""
    main, binding = _bound_main()
    end_main = ("import os, select, signal, sys; p = int(sys.argv[1]); fd = os.pidfd_open(p); os.kill(p, signal.SIGKILL);"
                " select.select([fd], [], [])")
    try:
        rc, steps, err, _t = _hooked(tmp_path, [{"kind": "exec", "argv": [sys.executable, "-c", end_main, str(main.pid)],
                                                 "required": True}], binding)
    finally:
        main.kill()
        main.wait()
    assert (rc, steps[0]["outcome"]) == (1, "main-gone"), (steps, err)


@pytest.mark.parametrize("reply, outcome", [
    ("Call:DL1AB-12 Short:X set\n", "sent-unacked"),      # another call that starts with ours
    ("Call:DL1AB-1 Short:X set\n", "acked"),
    ("Call:DL1AB-1", "acked"),                             # the answer ends right after the call
])
def test_an_ack_counts_only_for_the_whole_call(tmp_path, reply, outcome):
    """H4: stop_on has no closing character; "Call:DL1AB-1" must not be read in "Call:DL1AB-12"."""
    port, got, stop = _guest(lambda ln: reply)
    rc, steps, err, _t = _hooked(tmp_path, [_send(port, stop_on="Call:DL1AB-1")])
    stop()
    assert steps[0]["outcome"] == outcome, (steps, err)
