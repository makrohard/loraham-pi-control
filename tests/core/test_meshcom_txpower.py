"""RADIO-POWER item 4: MeshCom's node, not LHPC, configures the daemon's POWER at every XR connect (the bridge applies
what the firmware reports), so after the callsign LHPC sends the node `--txpower N`, N = the POWER the instance applies
to its daemon (its own set over the radio row's over the default). Ack-aware and idempotent like the callsign:
`--info` reports the node's power (`TXPWR N dBm`); equal -> no send; a refusal ends `unverified` and the node keeps running. Driven
through `lhpc stack poststart meshcom@r1` (the verb a start's post-start shares, `_run_post_start`) against a stand-in
net-console that answers as the firmware does (command_functions.cpp:5793-5815, :6782)."""
import ast
import dataclasses
import pathlib
import re
import socket
import subprocess
import sys
import threading

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


class _Node:
    """The node's net-console: one command per connection, answered as the firmware answers it."""

    def __init__(self, power: int, call: str = CALL, top: int = 20):
        self.power, self.call, self.top, self.got = power, call, top, []
        self.srv = socket.socket()
        self.srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.srv.bind(("127.0.0.1", 0))
        self.srv.listen(8)
        self.port = self.srv.getsockname()[1]
        threading.Thread(target=self._serve, daemon=True).start()

    def _answer(self, line: str) -> str:
        if line == "--info":           # printfdeb: the console gets it (command_functions.cpp:7553, :7648)
            return (INFO_REPLY.replace("Call: <XX0XXX-00>", f"Call: <{self.call}>")
                    .replace("TXPWR 17 dBm", f"TXPWR {self.power} dBm"))
        if line == "--lora":           # a plain printf: the UART gets it, the console none of it (:6782-6794)
            return ""
        if m := re.fullmatch(r"--setcall (\S+)", line):
            self.call = m.group(1)
            return f"Call:{self.call} Short:x set\n"
        if m := re.fullmatch(r"--txpower (-?\d+)", line):
            n = int(m.group(1))
            if not 0 <= n <= self.top:
                return f"{n}txpower {n} dBm not between 0 and max {self.top} dBm\n"
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


def _poststart(tmp_path, node_power, row_dp=(), own=None, schedule=None, call=CALL, top=20):
    """`lhpc stack poststart meshcom@r1` against a node reporting `node_power` -> (result, the node, the service); the
    node's `status` holds the post-start lines `lhpc status` shows for it."""
    svc = _svc(tmp_path, row_dp)
    r1 = instances.parse_target(svc, "meshcom@r1", "start")[1]
    assert svc.save_config_bundle("meshcom", values={"mc_callsign": CALL}, inst=r1).ok
    if own is not None:
        assert svc.save_daemon_params("meshcom", {"POWER": own}, rid="r1").ok
    node = _Node(node_power, call=call, top=top)
    _on_console(svc, node.port, schedule)
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
    line = (r"post-start: tx power UNVERIFIED — not confirmed \(the node may use 7\): the node answered but never "
            r"reported the expected value within [0-9.]+s \(2 attempts\); re-apply with: lhpc stack poststart meshcom@r1")
    assert any(re.fullmatch(line, ln) for ln in node.status), node.status
    assert any(re.search(line[len("post-start: "):].split(":", 1)[0], d) for d in res.details), res.details
    log = next(d.split(" -> tail -f ", 1)[1] for d in res.details if " -> tail -f " in d)
    assert "value never confirmed in 2 attempt(s); the node may use its own value '7'" in pathlib.Path(log).read_text()


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


def test_meshcoms_power_step_follows_the_callsign_with_the_same_window():
    from lhpc.core.manifest import load_manifest
    comp = next(c for s in load_manifest() for c in s.components if c.id == "meshcom-qemu")
    labels = [s.get("label") for s in comp.post_steps]
    assert labels[-2:] == ["callsign", "tx power"], labels
    call, power = comp.post_steps[-2], comp.post_steps[-1]
    assert power["schedule"] == call["schedule"] and power["on_exhausted"] == "warn" and power["required"] is True
    assert "skip_if_param" not in power
    # its probe is one the console answers: `--lora` prints to the UART only (a plain printf, command_functions.cpp:6782-
    # 6794); `--info`'s TXPWR line is printfdeb (:7648), the line the callsign's probe reads too (testlab 43991217519:
    # 25 `--lora` probes "incomplete", the start cut at 600 s)
    assert (power["probe"], power["probe_value"]) == ("--info\n", "TXPWR (-?[0-9]+) dBm")
