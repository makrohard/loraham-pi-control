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


# The wall clock steps an hour forward right after the launcher's first reading of it (a first-boot time sync): the
# rendered launcher, run under a wrapper that replaces time.time before the launcher's own code runs.
_JUMP = ("import runpy, sys, time\n_real, _n = time.time, [0]\n"
         "def _stepped():\n    _n[0] += 1\n    return _real() + (3600 if _n[0] > 1 else 0)\n"
         "time.time = _stepped\nrunpy.run_path(sys.argv[1], run_name='__main__')\n")


def _jumped(tmp_path, steps):
    """The steps in a real launcher under a forward wall-clock step: (rc, the step records, stderr)."""
    root = tmp_path / "rt"
    (root / "state" / "post").mkdir(parents=True, exist_ok=True)
    rp = root / "state" / "post" / "r.json"
    (tmp_path / "launcher.py").write_text(commands.render_post_launcher(steps, _C(), {}, _Op(), str(root),
                                                                        str(tmp_path), "", result_path=str(rp)))
    p = subprocess.run([sys.executable, "-c", _JUMP, str(tmp_path / "launcher.py")], capture_output=True, text=True,
                       timeout=60, check=False)
    return p.returncode, json.loads(rp.read_text())["steps"], p.stderr


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
