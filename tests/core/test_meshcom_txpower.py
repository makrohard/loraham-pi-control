"""RADIO-POWER item 4: MeshCom's node, not LHPC, configures the daemon's POWER at every XR connect (the bridge applies
what the firmware reports), so after the callsign LHPC sends the node `--txpower N`, N = the POWER the instance applies
to its daemon (its own set over the radio row's over the default). The LINE form (G2 B2-B4): `--info`'s whole power
line gives the node's value (two different ones prove nothing); equal -> no send; an ACK line counts only once a fresh
probe reports the value; a refusal line is named; the step then ends `unverified` and the node keeps running.
Driven through `lhpc stack poststart meshcom@r1` (the verb a start's post-start shares, `_run_post_start`) against a
stand-in net-console that answers as the firmware does (command_functions.cpp:5793-5815, :6782), on short schedules
under a watchdog (G2 N3: a regression ends the module, never a production-length wait)."""
import ast
import faulthandler
import dataclasses
import pathlib
import re
import socket
import subprocess
import sys
import threading
import time

import pytest
import radios_fixture

from lhpc.core import commands, instances
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem, Listener, RealCommandRunner
from lhpc.core.services import ControllerService
from seams import seed_framing_driver

CALL = "DL9XYZ-1"
# The console's real answer to `--info`: MeshCom 4.40a under QEMU, the firmware LHPC ships, recorded on a Zero 2 W on
# 2026-11-11 (the guest's UART log, lines 66-113: the reply between the console client's command and its disconnect;
# printfdeb output, which goes to the console and the UART alike). Its call and power are this test's.
INFO_REPLY = (pathlib.Path(__file__).parents[1] / "data" / "meshcom-4.40a-info-reply.txt").read_bytes().decode()


@pytest.fixture(autouse=True)
def _watchdog():
    """G2 N3: a row that would wait a production window instead dumps its stack and ends the run after 120 s."""
    faulthandler.dump_traceback_later(120, exit=True)
    yield
    faulthandler.cancel_dump_traceback_later()


class _Node:
    """The node's net-console: one command per connection, answered as the firmware answers it. The faults a G2 row
    needs: `info_extra` lines added to `--info`'s reply, `info_late` lines sent 0.5 s after it in a second write (a
    slow console's later chunk), `txpower_reply` replacing the `--txpower` reply (`None`: as
    the firmware), `sets` False: a `--txpower` that answers but never changes the node's power."""

    def __init__(self, power: int, call: str = CALL, top: int = 20, info_extra=(), txpower_reply=None, sets=True,
                 info_late=()):
        self.power, self.call, self.top, self.got = power, call, top, []
        self.info_extra, self.txpower_reply, self.sets = tuple(info_extra), txpower_reply, sets
        self.info_late = tuple(info_late)
        self.srv = socket.socket()
        self.srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.srv.bind(("127.0.0.1", 0))
        self.srv.listen(8)
        self.port = self.srv.getsockname()[1]
        threading.Thread(target=self._serve, daemon=True).start()

    def _answer(self, line: str) -> str:
        if line == "--info":           # printfdeb: the console gets it (command_functions.cpp:7553, :7648)
            return (INFO_REPLY.replace("Call: <XX0XXX-00>", f"Call: <{self.call}>")
                    .replace("TXPWR 17 dBm", f"TXPWR {self.power} dBm")) + "".join(ln + "\n" for ln in self.info_extra)
        if line == "--lora":           # a plain printf: the UART gets it, the console none of it (:6782-6794)
            return ""
        if m := re.fullmatch(r"--setcall (\S+)", line):
            self.call = m.group(1)
            return f"Call:{self.call} Short:x set\n"
        if m := re.fullmatch(r"--txpower (-?\d+)", line):
            n = int(m.group(1))
            if self.txpower_reply is not None:
                return self.txpower_reply.format(n=n, top=self.top)
            if not 0 <= n <= self.top:
                return f"{n}txpower {n} dBm not between 0 and max {self.top} dBm\n"
            if self.sets:
                self.power = n
            return f"{n}set txpower to {n} dBm\n"
        return "?\n"

    def _serve(self):
        while True:
            try:
                c, _a = self.srv.accept()
            except OSError:
                return
            with c:
                c.settimeout(5)
                buf = b""
                while not buf.endswith(b"\n"):
                    chunk = c.recv(256)
                    if not chunk:
                        break
                    buf += chunk
                line = buf.decode().strip()
                self.got.append(line)
                c.sendall(self._answer(line).encode())
                if line == "--info" and self.info_late:
                    time.sleep(0.5)          # wallclock: a slow console's later chunk, under the reader's 2 s of silence
                    try:
                        c.sendall("".join(ln + "\n" for ln in self.info_late).encode())
                    except OSError:
                        pass                                 # a reader that stopped early (the callsign's probe) has gone

    def sent(self, command: str) -> list:
        """The lines it got that are `command` (its first word)."""
        return [ln for ln in self.got if ln.split(" ", 1)[0] == command]

    def close(self):
        self.srv.close()


def _svc(tmp_path, row_dp=()):
    """r1 on 433 with `row_dp`, meshcom-qemu's two endpoints (web UI, net-console) up: its node runs. The post-start
    launcher runs for real (python3), against the stand-in console."""
    radios_fixture.write(tmp_path, (dataclasses.replace(radios_fixture.R1, dp=tuple(row_dp)),))
    seed_framing_driver(tmp_path)
    (tmp_path / "src" / "meshcom-qemu-raspi").mkdir(parents=True, exist_ok=True)        # the launcher's cwd: the node's checkout
    up = [Listener(family="ipv4", ip="127.0.0.1", port=port, inode=i) for i, port in enumerate((18083, 12323), 1)]
    system = dataclasses.replace(FakeSystem(listeners=up).system, runner=RealCommandRunner())   # the runner runs
    return ControllerService(system=system, paths=Paths(runtime_root=tmp_path))


def _on_console(svc, port, schedule=None):
    """meshcom-qemu's own tcp_send post-steps, on the stand-in console's port (its waits on the guest's boot log and
    provisioning marker left out: there is no guest). `schedule` shortens the windows where a test needs the end."""
    stacks = []
    for st in svc.stacks():
        comps = []
        for c in st.components:
            steps = [{**s, "port": port, **({"schedule": schedule} if schedule else {})}
                     for s in c.post_steps if s["kind"] == "tcp_send"]
            comps.append(dataclasses.replace(c, post_steps=tuple(steps)) if c.id == "meshcom-qemu" else c)
        stacks.append(dataclasses.replace(st, components=tuple(comps)))
    svc._stacks = tuple(stacks)


def _main(svc):
    """meshcom-qemu's main on r1: a REAL process, recorded as a start records it."""
    life = svc._lifecycle()
    stack = svc.stack("meshcom")
    p = subprocess.Popen([sys.executable, "-c", "import sys; print('up', flush=True); sys.stdin.read()"],
                         stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, start_new_session=True)
    assert p.stdout.readline() == "up\n"
    assert life.record_launch(stack, stack.component("meshcom-qemu"), p.pid, "",       # a one-band stack: no band key
                              ident=life._capture_identity(p.pid), role="", extra={"rid": "r1"})
    return p


def _poststart(tmp_path, node_power, row_dp=(), own=None, schedule=((3, 0.1),), call=CALL, top=20, **faults):
    """`lhpc stack poststart meshcom@r1` against a node reporting `node_power` -> (result, the node, the service); the
    node's `status` holds the post-start lines `lhpc status` shows for it. Three attempts 0.1 s apart (N3): the
    production window is the manifest row's."""
    svc = _svc(tmp_path, row_dp)
    r1 = instances.parse_target(svc, "meshcom@r1", "start")[1]
    assert svc.save_config_bundle("meshcom", values={"mc_callsign": CALL}, inst=r1).ok
    if own is not None:
        assert svc.save_daemon_params("meshcom", {"POWER": own}, rid="r1").ok
    node = _Node(node_power, call=call, top=top, **faults)
    _on_console(svc, node.port, [list(x) for x in schedule])
    p = _main(svc)
    try:
        res = svc.poststart("meshcom", apply=True, inst=r1)
        node.status = svc._required_post_outcomes("meshcom-qemu", r1)          # what `lhpc status` shows, main alive
    finally:
        p.stdin.close()
        p.wait()
        p.stdout.close()
        node.close()
    return res, node, svc


def _default_power(svc):
    from lhpc.core import daemon_params, radio_start
    return daemon_params.default_value("meshcom", "433", "POWER", radio_start.power_window(svc, "r1")[0])


@pytest.mark.needs_session
def test_the_node_gets_the_radios_power_after_its_callsign(tmp_path):
    # the node reports 9, its call differs: the callsign is set first, then `--txpower <the instance's POWER>`.
    # red on 17fd52e8: no --txpower is ever sent
    res, node, svc = _poststart(tmp_path, 9, call="XX0XXA-2")
    n = _default_power(svc)
    assert res.ok, (res.summary, res.details)
    assert node.sent("--txpower") == [f"--txpower {n}"], node.got
    assert node.got.index(f"--setcall {CALL}") < node.got.index(f"--txpower {n}"), node.got
    assert node.power == int(n)


@pytest.mark.needs_session
def test_a_node_that_already_has_the_power_gets_no_send(tmp_path):
    # the node reports the instance's POWER -> probed with --info, zero sends (each one costs a flash write and an XR
    # reconnect). red on 17fd52e8: no probe either
    svc = _svc(tmp_path)
    n = int(_default_power(svc))
    res, node, _svc2 = _poststart(tmp_path, n)
    assert res.ok, (res.summary, res.details)
    assert node.got.count("--info") >= 2 and node.sent("--txpower") == [], node.got     # the callsign's, then the power's


@pytest.mark.needs_session
@pytest.mark.parametrize("row_dp, own, want", [
    ((("POWER", "12"),), None, "12"),            # the radio row's POWER over the default
    ((("POWER", "12"),), "11", "11"),            # the instance's own set over the row's
], ids=["row-over-default", "own-over-row"])
def test_the_power_is_the_one_the_instance_applies_to_its_daemon(tmp_path, row_dp, own, want):
    # N is the daemon apply's own value (`_daemon_param_applies`), never a second default. red on 17fd52e8: nothing sent
    res, node, svc = _poststart(tmp_path, 9, row_dp=row_dp, own=own)
    assert res.ok, (res.summary, res.details)
    assert svc._daemon_param_applies("meshcom", "r1")["POWER"] == want
    assert node.sent("--txpower") == [f"--txpower {want}"], node.got


@pytest.mark.needs_session
def test_a_refused_power_is_unverified_and_the_node_keeps_running(tmp_path):
    # a firmware whose range ends below the instance's POWER (a node built 0..8 here; the Meshstick's 21/22 against
    # 0..20 in the field) refuses every send: the step ends `unverified`, the run passes (warn), the node keeps its
    # 7. red on 17fd52e8: nothing sent
    res, node, svc = _poststart(tmp_path, 7, schedule=[[2, 0.1]], top=8)
    n = _default_power(svc)
    assert int(n) > 8 and node.sent("--txpower") and set(node.sent("--txpower")) == {f"--txpower {n}"}, node.got
    assert node.power == 7
    assert res.ok, (res.summary, res.details)
    # N2 (the coordinator's decision): a warning, not a refusal, that names the power the node may still use: in
    # `lhpc status`, in the poststart's answer and in the post-start log
    # plan E13: the line also names the firmware's own refusal line (refused_line, command_functions.cpp:5803), without
    # the number printdeb echoes before it on the UART
    refused = f"txpower {n} dBm not between 0 and max 8 dBm"
    line = (r"post-start: tx power UNVERIFIED — not confirmed \(the node may use 7\): the node answered but never "
            r"reported the expected value within [0-9.]+s \(2 attempts\); it refused: '" + refused + r"'; "
            r"re-apply with: lhpc stack poststart meshcom@r1")
    assert any(re.fullmatch(line, ln) for ln in node.status), node.status
    # G2 B5: the poststart's answer carries the WHOLE warning, the refusal and the re-apply command included
    assert any(re.fullmatch(r"  \[verified\] meshcom-qemu: " + line + r" \(log \S+\)", d) for d in res.details), res.details
    log = next(d.split(" -> tail -f ", 1)[1] for d in res.details if " -> tail -f " in d)
    assert (f"value never confirmed in 2 attempt(s); the node may use its own value '7'; it refused: '{refused}'"
            in pathlib.Path(log).read_text())


def _rendered(step, daemon):
    class _C:
        run_params = []

    class _Op:
        callsign = CALL
    script = commands.render_post_launcher([step], _C(), {}, _Op(), "/rt", "/src", "", daemon=daemon)
    node = next(n for n in ast.parse(script).body if isinstance(n, ast.Assign) and n.targets[0].id == "STEPS")
    return ast.literal_eval(node.value)


@pytest.mark.contract
@pytest.mark.parametrize("daemon", [None, {}, {"power": ""}, {"power": "1\n--setcall X"}, {"power": "abc"}],
                         ids=["none", "empty", "blank", "a-second-line", "not-a-number"])
def test_no_power_refuses_the_render_and_nothing_is_sent(daemon):
    # a rid that is no enabled radio has no POWER: the line is never rendered (never `--txpower ` without a number,
    # never a second console command riding in the value)
    step = {"kind": "tcp_send", "port": 1, "data": "--txpower {daemon:power}\n", "required": True}
    with pytest.raises(commands.CommandError, match=r"no \{daemon:power\} value"):
        _rendered(step, daemon)
    assert _rendered(step, {"power": "14"})[0]["data"] == "--txpower 14\n"


_LINE = {"probe": "--info\n", "probe_expect": "{daemon:power}", "ack_line": "set txpower to {daemon:power} dBm",
         "probe_line": "...FREQ {float} MHz TXPWR {value} dBm RXBOOST {word}",
         "refused_line": "txpower {int} dBm not between {int} and max {int} dBm"}


@pytest.mark.contract
@pytest.mark.parametrize("change, why", [
    ({"refused_line": None}, r"the line form needs probe, probe_expect, probe_line, ack_line and refused_line"),
    ({"probe": None}, r"the line form needs"),
    ({"probe_line": "TXPWR {int} dBm"}, r"probe_line needs 1 \{value\} slot"),
    ({"ack_line": "set {value}"}, r"ack_line needs 0 \{value\} slot"),
    ({"stop_on": "set"}, r"the line form takes no probe_value, probe_stop_on, stop_on or refused_on"),
    ({"refused_on": "x"}, r"the line form takes no"),
    ({"ack_line": "a\nb"}, r"ack_line must be one line of 1\.\.120 characters"),
    ({"ack_line": "x" * 121}, r"ack_line must be one line"),
], ids=["no-refused_line", "no-probe", "no-value-slot", "a-value-slot-in-ack", "with-stop_on", "with-refused_on",
        "two-lines", "too-long"])
def test_the_line_form_is_checked_at_render(change, why):
    step = {"kind": "tcp_send", "port": 1, "data": "--txpower {daemon:power}\n",
            **{k: v for k, v in {**_LINE, **change}.items() if v is not None}}
    with pytest.raises(commands.CommandError, match=why):
        _rendered(step, {"power": "14"})


@pytest.mark.contract
def test_a_template_never_becomes_a_pattern_that_can_stall():
    # G2 B4: the verdict's `(a+)+$` against 32 x "a" + "!" stalled a regex search; a template is literal text and
    # bounded slots, so the same text is matched as text, at once, and the {daemon:power} value is expanded
    step = {"kind": "tcp_send", "port": 1, "data": "--txpower {daemon:power}\n", **_LINE,
            "refused_line": "(a+)+$ {int}", "ack_line": "set txpower to {daemon:power} dBm"}
    d = _rendered(step, {"power": "14"})[0]
    assert re.fullmatch(d["refused_line"], "a" * 32 + "!") is None          # at once: the module's watchdog bounds it
    assert re.fullmatch(d["refused_line"], "a" * 5000 + "!") is None
    assert re.fullmatch(d["refused_line"], "(a+)+$ 12").group(1) == "(a+)+$ 12"
    assert re.fullmatch(d["ack_line"], "14set txpower to 14 dBm").group(1) == "set txpower to 14 dBm"
    assert re.fullmatch(d["ack_line"], "set txpower to 15 dBm") is None and d["probe_expect"] == "14"


@pytest.mark.needs_session
def test_a_second_power_line_proves_nothing_and_a_spoofed_one_is_no_line(tmp_path):
    # G2 B2: the node's power comes from WHOLE `...FREQ … TXPWR N dBm RXBOOST …` lines only. Another field holding
    # the text (a NAME line) is no such line; two power lines that disagree are no proof, so the power is sent.
    n = _default_power(_svc(tmp_path / "a"))
    spoof = [f"...NAME: ...FREQ 433.1750 MHz TXPWR {n} dBm RXBOOST off"]
    res, node, _ = _poststart(tmp_path / "b", 9, info_extra=spoof)
    assert res.ok and node.sent("--txpower") == [f"--txpower {n}"], node.got          # the spoof did not skip it
    twice = [f"...FREQ 433.1750 MHz TXPWR {n} dBm RXBOOST off"]                           # 9 and n: no proof
    res, node, _ = _poststart(tmp_path / "c", 9, info_extra=twice, sets=False)
    assert node.sent("--txpower") and not any("confirmed on attempt" in ln for ln in node.status), node.status
    log = next(d.split(" -> tail -f ", 1)[1] for d in res.details if " -> tail -f " in d)
    assert "the probe reply names different values (attempt 1/3) -> no proof, send" in pathlib.Path(log).read_text()
    # the second line in a LATER write of the same reply (a slow console): the probe reads the reply to its end
    res, node, _ = _poststart(tmp_path / "d", 9, info_late=twice, sets=False)
    log = next(d.split(" -> tail -f ", 1)[1] for d in res.details if " -> tail -f " in d)
    assert "the probe reply names different values (attempt 1/3) -> no proof, send" in pathlib.Path(log).read_text()


@pytest.mark.needs_session
@pytest.mark.parametrize("reply, refusal", [
    ("12:00:01 RX-LoRa2 040 : xAB DL1AB>*:set txpower to {n} dBm HW:1\n", None),   # someone's text: not the ACK line
    ("{n}set txpower to {n} dBm\n", None),                                         # an ACK the node never applied
    ("{n}set txpower to {n} dBm\n{n}txpower {n} dBm not between 0 and max {top} dBm\n",
     "txpower {n} dBm not between 0 and max {top} dBm"),                             # a stale ACK, then the refusal
    ("{n}txpower {n} dBm not between 0 and max {top} dBm", None),                   # a line never ended: a cut reply
], ids=["rx-line-with-the-text", "ack-not-applied", "refusal-after-ack", "unterminated-refusal"])
def test_only_a_fresh_probe_confirms_the_power(tmp_path, reply, refusal):
    # G2 B3: the ACK line is no proof by itself; a fresh `--info` must report the value; a refusal line in the reply,
    # after an ACK too, is named
    res, node, svc = _poststart(tmp_path, 7, txpower_reply=reply, sets=False)
    n = _default_power(svc)
    assert res.ok and node.power == 7 and node.sent("--txpower"), node.got
    assert not any("confirmed on attempt" in ln for ln in node.status), node.status
    line = next(ln for ln in node.status if " UNVERIFIED " in ln)
    assert "(the node may use 7)" in line
    want = refusal and refusal.format(n=n, top=20)
    assert (f"it refused: '{want}'" in line) if want else ("it refused" not in line), line


def test_meshcoms_power_step_follows_the_callsign_with_the_same_window():
    from lhpc.core.manifest import load_manifest
    comp = next(c for s in load_manifest() for c in s.components if c.id == "meshcom-qemu")
    labels = [s.get("label") for s in comp.post_steps]
    assert labels[-2:] == ["callsign", "tx power"], labels
    call, power = comp.post_steps[-2], comp.post_steps[-1]
    # the production window is the callsign's (the rows above run three attempts 0.1 s apart)
    assert power["schedule"] == call["schedule"] and power["on_exhausted"] == "warn" and power["required"] is True
    assert "skip_if_param" not in power
    # its probe is one the console answers: `--lora` prints to the UART only (a plain printf, command_functions.cpp:6782-
    # 6794); `--info`'s power line is printfdeb (:7648), the reply the callsign's probe reads too (testlab 43991217519:
    # 25 `--lora` probes "incomplete", the start cut at 600 s)
    d = _rendered({"kind": "tcp_send", "port": 1, **{k: power[k] for k in ("data", "probe", "probe_expect",
                                                                           "probe_line", "ack_line", "refused_line")}},
                  {"power": "14"})[0]
    assert d["probe"] == "--info\n" and d["probe_expect"] == "14"
    # each template matches the recorded firmware's own line: --info's (the recording), the ACK (:5809) and the
    # refusal (:5803), the last two with and without the number printdeb echoes on the UART
    assert [re.fullmatch(d["probe_line"], ln).group(1) for ln in INFO_REPLY.splitlines()
            if re.fullmatch(d["probe_line"], ln)] == ["17"]
    for echo in ("", "14", "22"):
        assert re.fullmatch(d["ack_line"], f"{echo}set txpower to 14 dBm").group(1) == "set txpower to 14 dBm"
        assert re.fullmatch(d["refused_line"], f"{echo}txpower 22 dBm not between 0 and max 20 dBm").group(1) == \
            "txpower 22 dBm not between 0 and max 20 dBm"
