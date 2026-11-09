"""L4/K4b: the instance context of every resolved template (A §2.3, DEC-W327: no slots). `{port:BASE}` is the
instance's radio's port, BASE + 133 x (n - 1) for rN (DEC-W348; r1 keeps BASE, r1..r64), `{port_of:STACK:BASE}`
STACK's port on the same radio, and `{instance_dir}` the radio's path level `<rid>/`, r1's too. The manifest's
template values (no radio, commands.TEMPLATE) are the bases without a radio level (T-P2's unit half)."""

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
    kw = {} if inst is None else {"inst": inst}     # no instance: the template values
    return commands.expand_argv(tokens, _C(), params or {}, _Op(), "/rt", "/src", "", **kw)


@pytest.mark.parametrize("tokens, today", [
    (["--port", "{port:8798}"], ["--port", "8798"]),
    (["-http", "127.0.0.1:{port:8080}"], ["-http", "127.0.0.1:8080"]),
    (["{runtime}/state/{instance_dir}meshcore"], ["/rt/state/meshcore"]),
    (["/dev/shm/lhpc-meshcore-gps{instance_suffix}.nmea"], ["/dev/shm/lhpc-meshcore-gps.nmea"]),
    (["{param:kiss_port}"], ["--port", "8001"]),
    (["--tnc", "127.0.0.1:{port_of:kiss:8001}"], ["--tnc", "127.0.0.1:8001"]),
])
def test_the_template_resolves_every_token_to_its_base(tokens, today):
    assert _argv(tokens) == today


@pytest.mark.parametrize("tokens, r1", [
    (["--port", "{port:8798}"], ["--port", "8798"]),                     # r1 keeps its base ports (DEC-W348)
    (["{runtime}/state/{instance_dir}meshcore"], ["/rt/state/r1/meshcore"]),   # and its paths sit under r1/ (K4b)
    (["/dev/shm/lhpc-meshcore-gps{instance_suffix}.nmea"], ["/dev/shm/lhpc-meshcore-gps-r1.nmea"]),
])
def test_r1_keeps_its_base_ports_and_has_its_own_paths(tokens, r1):
    assert _argv(tokens, Instance("r1")) == r1


@pytest.mark.parametrize("tokens, slot3_r12", [
    (["--port", "{port:8798}"], ["--port", "10261"]),      # + 133 x 11
    (["-http", "127.0.0.1:{port:8080}"], ["-http", "127.0.0.1:9543"]),
    (["{runtime}/state/{instance_dir}meshcore"], ["/rt/state/r12/meshcore"]),
    (["/dev/shm/lhpc-meshcore-gps{instance_suffix}.nmea"], ["/dev/shm/lhpc-meshcore-gps-r12.nmea"]),   # B3-D21a
    (["{param:kiss_port}"], ["--port", "9464"]),           # a param default follows the instance
    (["--tnc", "127.0.0.1:{port_of:kiss:8001}"], ["--tnc", "127.0.0.1:9464"]),   # kiss on the same radio (W331)
])
def test_an_instance_resolves_its_radios_ports_and_its_radio_level(tokens, slot3_r12):
    assert _argv(tokens, Instance("r12")) == slot3_r12


def test_the_port_is_the_radios():
    # DEC-W348: every instance's port is its radio's (K4b: no slot keeps the base on another radio).
    assert _argv(["--port", "{port:8798}"], Instance("r2")) == ["--port", "8931"]
    assert _argv(["{port:8001}"], Instance("r64")) == ["16380"]              # the top radio
    with pytest.raises(CommandError, match=r"radio r65 has no ports: .* r1\.\.r64"):
        _argv(["{port:8001}"], Instance("r65"))


def test_a_saved_port_param_is_r1s_port_and_rn_derives_from_it():
    # A configured port param is the base (r1's); rN derives from it (+ 133 x (n - 1), DEC-W348): the rule the
    # firewall rows follow (tests/host/test_firewall_instances.py). argv and process specs take the same port.
    saved = {"kiss_port": "8099"}
    assert _argv(["{param:kiss_port}"], params=saved) == ["--port", "8099"]
    assert _argv(["{param:kiss_port}"], Instance("r1"), saved) == ["--port", "8099"]
    assert _argv(["{param:kiss_port}"], Instance("r2"), saved) == ["--port", "8232"]
    assert _argv(["{param:kiss_port}"], Instance("r12"), saved) == ["--port", "9562"]
    assert commands.instance_value("{param:kiss_port}", _C(), saved, "/rt", "/src", "",
                                   inst=Instance("r12")) == "9562"


@pytest.mark.parametrize("rid", ["r0", "r100", "x1", "r1x", "R1", " r1"])
def test_an_instance_on_no_radio_id_is_refused(rid):
    with pytest.raises(CommandError):
        Instance(rid)


@pytest.mark.parametrize("base", [1023, 65536])
def test_a_base_outside_the_port_range_is_refused(base):
    with pytest.raises(CommandError, match="is outside 1024..65535"):
        _argv([f"{{port:{base}}}"])


def test_a_post_step_port_follows_the_instance():
    steps = [{"kind": "tcp_wait", "port": "{port:12323}"}, {"kind": "tcp_send", "port": 12323, "data": "x"}]
    script = commands.render_post_launcher(steps, _C(), {}, _Op(), "/rt", "/src", "",
                                           inst=Instance("r3"))     # r3: + 266
    rendered = next(ast.literal_eval(n.value) for n in ast.parse(script).body
                    if isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") == "STEPS")
    assert [s["port"] for s in rendered] == [12589, 12323]   # a literal int stays what it says


def test_the_template_pre_steps_env_and_post_steps_without_an_instance():
    # A caller without an instance argument (a manifest-level reader) gets the template values.
    pre = commands.normalize_pre_steps([{"kind": "mkdir", "path": "{runtime}/state/{instance_dir}x"}],
                                       "/rt", "/src")
    env = commands.build_env([("STATE", "{runtime}/state/{instance_dir}x")], "/rt", "/src")
    script = commands.render_post_launcher([{"kind": "tcp_wait", "port": "{port:12323}"}], _C(), {},
                                           _Op(), "/rt", "/src", "")
    rendered = next(ast.literal_eval(n.value) for n in ast.parse(script).body
                    if isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") == "STEPS")
    assert (pre[0][1], env, rendered[0]["port"]) == ("/rt/state/x", {"STATE": "/rt/state/x"}, 12323)


def test_pre_steps_and_env_take_the_instance_level():
    pre = commands.normalize_pre_steps([{"kind": "mkdir", "path": "{runtime}/state/{instance_dir}x"}],
                                       "/rt", "/src", inst=Instance("r2"))
    env = commands.build_env([("STATE", "{runtime}/state/{instance_dir}x")], "/rt", "/src",
                             inst=Instance("r2"))
    assert pre[0][1] == "/rt/state/r2/x" and env == {"STATE": "/rt/state/r2/x"}



def test_a_saved_port_whose_rn_port_leaves_the_range_is_refused_in_argv():
    # B3-D15: rN = saved + 133 x (n - 1); a derived port above 65535 is refused with an error naming the radio and
    # the saved port (never the param validator's bare "above maximum 65535").
    with pytest.raises(CommandError, match="65530 on r2 gives 65663"):
        _argv(["{param:kiss_port}"], Instance("r2"), {"kiss_port": "65530"})
    assert _argv(["{param:kiss_port}"], Instance("r2"), {"kiss_port": "65402"}) == ["--port", "65535"]


def test_an_rn_firewall_port_that_leaves_the_range_is_refused():
    # B3-D15, the firewall rows: the same rule refuses the derived port instead of writing a port above 65535.
    from lhpc.core import firewall
    with pytest.raises(CommandError, match="65530 on r2 gives 65663"):
        firewall.instance_port(65530, Instance("r2"))
    assert firewall.instance_port(65530, commands.TEMPLATE) == 65530
