"""The instance's identity in argv (A §2.3): an exact `(option, value)` token pair, never a
substring. The adversarial cases are the plan's: neighbouring ports, rids that prefix each other,
a path inside a longer path, the QEMU drive pair."""
import pytest

from lhpc.core.manifest import ManifestError, _parse_process
from lhpc.core.model import ProcessSpec
from lhpc.core.probes.process import has_instance_token, matches

IMG = "/rt/state/meshcom/node-flash.bin"


@pytest.mark.parametrize("argv,inst,ok", [
    (["x", "--port", "8788"], ("--port", "8788"), True),
    (["x", "--port=8788"], ("--port", "8788"), True),
    (["x", "--port", "18788"], ("--port", "8788"), False),       # a longer port
    (["x", "--port", "87880"], ("--port", "8788"), False),
    (["x", "--state", "/x/8788/y"], ("--port", "8788"), False),   # the value inside another token
    (["x", "8788", "--port"], ("--port", "8788"), False),         # wrong order
    (["x", "--port"], ("--port", "8788"), False),                 # the option without a value
    (["x", "-s", "/tmp/loraf-r12.sock"], ("-s", "/tmp/loraf-r1.sock"), False),
    (["x", "-s", "/tmp/loraf-r1.sock"], ("-s", "/tmp/loraf-r1.sock"), True),
    (["x", "--node-image", IMG + ".bak"], ("--node-image", IMG), False),
    (["x", "--node-image", "/rt/state/r2/meshcom/node-flash.bin"], ("--node-image", IMG), False),
    (["qemu", "-drive", "file=/rt/efuse.bin,if=none", "-drive", f"file={IMG},if=mtd,format=raw"],
     ("-drive", f"file={IMG},if=mtd,format=raw"), True),
    (["qemu", "-drive", f"file={IMG}.bak,if=mtd,format=raw"],
     ("-drive", f"file={IMG},if=mtd,format=raw"), False),
])
def test_the_instance_token_is_exact(argv, inst, ok):
    assert has_instance_token(argv, *inst) is ok


def test_matches_needs_the_stack_identity_and_the_instance_token():
    spec = ProcessSpec(exec_name="loraham_daemon", instance_arg=("--radio", "{param:radio}"))
    argv = ["/x/loraham_daemon", "--radio", "433", "--hw", "loraham"]
    assert matches(spec, argv)                                     # no instance: the stack only
    assert matches(spec, argv, ("--radio", "433"))
    assert not matches(spec, argv, ("--radio", "868"))
    assert not matches(spec, ["/x/other", "--radio", "433"], ("--radio", "433"))


@pytest.mark.parametrize("bad", [["--port"], ["--port", ""], ["--port", 8788], "--port 8788",
                                 ["-a", "b", "c"], "", {}, 0, False, None])    # falsey ones too (G2-B2 N4)
def test_a_malformed_instance_arg_is_refused(bad):
    with pytest.raises(ManifestError, match="instance_arg"):
        _parse_process({"exec_name": "x", "instance_arg": bad})


def test_no_instance_arg_is_the_absent_key_or_an_empty_list():
    for raw in ({"exec_name": "x"}, {"exec_name": "x", "instance_arg": []}):
        assert _parse_process(raw).instance_arg == ()


def test_the_instance_arg_is_parsed():
    assert _parse_process({"exec_name": "x", "instance_arg": ["--port", "{port:8788}"]}) \
        .instance_arg == ("--port", "{port:8788}")
