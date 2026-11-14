"""The post-step kind `log_wait` (B2a, his C): ready when a line of the log file at `path` contains `pattern`. The path
is opened fresh on every poll, so a guest reboot that moves the `uart-latest.log` symlink is read from its new
boot; an absent file is "not yet"; one budget runs from the step's start and a reboot never restarts it; only the
first 1 MB of a file is read. MeshCom's start waits on its firmware's console line, and the bounds nest."""
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


@pytest.mark.slow
@pytest.mark.parametrize("before, outcome", [(100, "ready"), ((1 << 20) + 100, "timeout")])
def test_only_the_first_megabyte_of_a_log_is_read(tmp_path, before, outcome):
    link = tmp_path / "uart-latest.log"
    _boot(tmp_path, link, "uart-1.log", "x" * before + LINE + "y" * (1 << 20))
    _rc, st, _err = _wait(tmp_path, link, 3, required=False)
    assert st["outcome"] == outcome


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


def test_the_waits_nest_inside_the_required_post_starts_cap():
    steps = _rendered(_meshcom_steps())
    wait = next(s for s in steps if s["kind"] == "log_wait")
    send = next(s for s in steps if s["kind"] == "tcp_send")
    assert sum(send["intervals"]) == 904.0                     # the documented worst Zero boot + 60 s
    assert wait["timeout"] + sum(send["intervals"]) + 64 <= REQUIRED_POST_TIMEOUT_S


@pytest.mark.parametrize("step, why", [
    ({"kind": "log_wait", "path": "x", "pattern": "", "timeout": 10}, "pattern"),
    ({"kind": "log_wait", "path": "x", "pattern": "p", "timeout": REQUIRED_POST_TIMEOUT_S + 1}, "timeout"),
    ({"kind": "log_wait", "path": "x", "pattern": "p", "timeout": 0}, "timeout"),
])
def test_a_log_wait_without_a_pattern_or_beyond_the_cap_is_refused(step, why):
    with pytest.raises(commands.CommandError, match=why):
        commands.render_post_launcher([step], _C(), {}, _Op(), "/rt", "/src", "")


def test_meshcoms_guest_gets_the_longer_provisioning_deadline(tmp_path):
    comp = next(c for s in load_manifest() for c in s.components if c.id == "meshcom-qemu")
    env = commands.build_env(comp.run_env, str(tmp_path), "/src/mc", "", inst=commands.Instance("r1"))
    assert env.get("PROVISION_TIMEOUT") == "1200"
