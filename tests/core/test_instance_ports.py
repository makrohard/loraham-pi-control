"""L4: the instance context of every resolved template (A §2.3, §2.4). `{port:BASE}` is BASE + 10 x the
instance's slot, `{port_of:STACK:BASE}` the port of STACK's instance on the same radio, and
`{instance_dir}` a per-radio path level ("" at slot 0). Slot 0 resolves every template to today's
value (T-P2's unit half); slot k needs a radio id. Until L5 every runtime caller resolves slot 0."""

import ast

import pytest

from lhpc.core import commands
from lhpc.core.commands import CommandError
from lhpc.core.model import RunParam


def Instance(*args):                      # resolved at call time: each test fails on its own at dev
    return commands.Instance(*args)


class _C:
    run_params = (RunParam("kiss_port", kind="int", default="{port:8001}", arg="--port", min=1, max=65535),)


class _Op:
    callsign = "N0CALL"


def _argv(tokens, inst=None, params=None):
    return commands.expand_argv(tokens, _C(), params or {}, _Op(), "/rt", "/src", "",
                                inst=inst or commands.SLOT0)


@pytest.mark.parametrize("tokens, today", [
    (["--port", "{port:8798}"], ["--port", "8798"]),
    (["-http", "127.0.0.1:{port:8080}"], ["-http", "127.0.0.1:8080"]),
    (["{runtime}/state/{instance_dir}meshcore"], ["/rt/state/meshcore"]),
    (["{param:kiss_port}"], ["--port", "8001"]),
    (["--tnc", "127.0.0.1:{port_of:kiss:8001}"], ["--tnc", "127.0.0.1:8001"]),
])
def test_slot_0_resolves_every_template_to_today(tokens, today):
    assert _argv(tokens) == today
    # which radio holds slot 0 changes no name (its kiss, for {port_of:}, at slot 0 too)
    assert _argv(tokens, Instance(0, "r2", {"kiss": 0})) == today


@pytest.mark.parametrize("tokens, slot3_r12", [
    (["--port", "{port:8798}"], ["--port", "8828"]),
    (["-http", "127.0.0.1:{port:8080}"], ["-http", "127.0.0.1:8110"]),
    (["{runtime}/state/{instance_dir}meshcore"], ["/rt/state/r12/meshcore"]),
    (["{param:kiss_port}"], ["--port", "8031"]),           # a param default follows the instance
    (["--tnc", "127.0.0.1:{port_of:kiss:8001}"], ["--tnc", "127.0.0.1:8011"]),   # kiss is slot 1 there
])
def test_slot_k_resolves_its_ports_and_its_radio_level(tokens, slot3_r12):
    assert _argv(tokens, Instance(3, "r12", {"kiss": 1})) == slot3_r12


def test_a_saved_port_param_is_taken_as_saved():
    assert _argv(["{param:kiss_port}"], Instance(3, "r12"), {"kiss_port": "8099"}) == ["--port", "8099"]


def test_port_of_a_stack_without_an_instance_on_that_radio_is_refused():
    with pytest.raises(CommandError, match="no kiss instance on radio r2"):
        _argv(["{port_of:kiss:8001}"], Instance(1, "r2"))


@pytest.mark.parametrize("slot, rid", [(8, "r1"), (-1, "r1"), (True, "r1"), ("1", "r1"),
                                       (1, ""), (1, "r0"), (1, "r100"), (1, "x1"), (0, "r1x")])
def test_an_instance_outside_slots_0_to_7_or_without_a_radio_id_is_refused(slot, rid):
    with pytest.raises(CommandError):
        Instance(slot, rid)


@pytest.mark.parametrize("base", [1023, 65536 - 70])
def test_a_base_that_leaves_the_port_range_at_slot_7_is_refused(base):
    with pytest.raises(CommandError, match="leaves 1024..65535"):
        _argv([f"{{port:{base}}}"])


def test_a_post_step_port_follows_the_instance():
    steps = [{"kind": "tcp_wait", "port": "{port:12323}"}, {"kind": "tcp_send", "port": 12323, "data": "x"}]
    script = commands.render_post_launcher(steps, _C(), {}, _Op(), "/rt", "/src", "",
                                           inst=Instance(2, "r3"))
    rendered = next(ast.literal_eval(n.value) for n in ast.parse(script).body
                    if isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") == "STEPS")
    assert [s["port"] for s in rendered] == [12343, 12323]   # a literal int stays what it says


def test_pre_steps_and_env_take_the_instance_level():
    pre = commands.normalize_pre_steps([{"kind": "mkdir", "path": "{runtime}/state/{instance_dir}x"}],
                                       "/rt", "/src", inst=Instance(1, "r2"))
    env = commands.build_env([("STATE", "{runtime}/state/{instance_dir}x")], "/rt", "/src",
                             inst=Instance(1, "r2"))
    assert pre[0][1] == "/rt/state/r2/x" and env == {"STATE": "/rt/state/r2/x"}
