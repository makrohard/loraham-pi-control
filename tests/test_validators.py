"""P0.1 — input validation: no user value can inject into a shell, create files,
alter argv structure, or escape a config path."""

import pytest

from lhpc.core import validators as V
from lhpc.core.validators import ValidationError
from lhpc.core.model import RunParam, FileParam, Component, ComponentKind, Stack
from lhpc.core.lifecycle import Lifecycle
from lhpc.core.config import Config, OperatorConfig
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem


# --- malicious values are rejected by the typed validators -------------------

@pytest.mark.parametrize("bad", [
    "N0CALL; rm -rf /", "N0CALL`reboot`", "N0CALL$(id)", "N0CALL|sh", "A&B",
    "../../etc/passwd", "a/b", "call\ninject", "call\x00", "x" * 300, "N0CALL>f",
])
def test_callsign_rejects_injection(bad):
    with pytest.raises(ValidationError):
        V.callsign(bad, allow_empty=False)


def test_callsign_accepts_real():
    assert V.callsign("N0CALL-10") == "N0CALL-10"
    assert V.callsign("", allow_empty=True) == ""


@pytest.mark.parametrize("bad", ["433; rm", "1e9", "abc", "433/x", "99999", "0"])
def test_freq_rejects_bad(bad):
    with pytest.raises(ValidationError):
        V.freq(bad)


def test_freq_accepts_real():
    assert V.freq("433.775") == "433.775"


@pytest.mark.parametrize("bad", ["10.0.0.1; rm", "a b", "host|x", "h$x", "../x", "a/b"])
def test_host_rejects_bad(bad):
    with pytest.raises(ValidationError):
        V.host(bad)


def test_host_accepts_real():
    assert V.host("10.0.2.2") == "10.0.2.2"
    assert V.host("::1") == "::1"


@pytest.mark.parametrize("bad", ["0x10", "70000", "-1", "80 80", "8001;rm"])
def test_port_rejects_bad(bad):
    with pytest.raises(ValidationError):
        V.port(bad)


def test_node_name_rejects_injection():
    with pytest.raises(ValidationError):
        V.node_name("node; rm -rf /")
    assert V.node_name("LoRaHAM Pi-1") == "LoRaHAM Pi-1"


@pytest.mark.parametrize("bad", ["433/x", "../x", "x;y", "both;rm", "434"])
def test_band_rejects_bad(bad):
    with pytest.raises(ValidationError):
        V.band(bad)


def test_band_accepts():
    assert V.band("433") == "433" and V.band("both") == "both"
    with pytest.raises(ValidationError):
        V.band("both", allow_both=False)


@pytest.mark.parametrize("bad", ["..", "a/b", "a\\b", "x;y", "id\x00", ".", ""])
def test_path_component_rejects(bad):
    with pytest.raises(ValidationError):
        V.path_component(bad)


# --- safe_text is the default str validator and blocks shell metacharacters ---

@pytest.mark.parametrize("bad", [";", "|", "&", "$", "`", ">", "<", "(", ")",
                                  "\\", '"', "'", "/", "\n", "\x00", "*", "?"])
def test_safe_text_blocks_metacharacters(bad):
    with pytest.raises(ValidationError):
        V.safe_text(f"value{bad}here")


def test_validate_param_dispatch():
    assert V.validate_param(RunParam("radio", kind="enum", choices=("433", "868")), "433") == "433"
    with pytest.raises(ValidationError):
        V.validate_param(RunParam("radio", kind="enum", choices=("433", "868")), "999")
    with pytest.raises(ValidationError):
        V.validate_param(RunParam("p", kind="int", min=0, max=10), "11")
    # str with a named validator
    assert V.validate_param(RunParam("call", kind="str", validator="callsign"), "N0CALL") == "N0CALL"
    with pytest.raises(ValidationError):
        V.validate_param(FileParam("call", "CALL", kind="str", validator="callsign"), "x;rm")


# --- execution path: an invalid value fails the launch, nothing reaches sh ----

def _life(tmp_path, callsign="N0CALL"):
    from conftest import real_spawn
    cfg = Config(operator=OperatorConfig(callsign=callsign, locator=""))
    return Lifecycle(Paths(runtime_root=tmp_path), (), cfg, FakeSystem().system,
                     spawn=real_spawn)


def test_start_rejects_malicious_callsign_before_shell(tmp_path):
    # Structured run that uses the operator callsign as its own argv token.
    comp = Component(id="c", name="c", kind=ComponentKind.SERVICE,
                     run_argv=("./app", "-c", "{operator:callsign}"))
    life = _life(tmp_path, callsign="N0CALL; touch /tmp/pwned")
    res = life.start(Stack(id="s", name="s", main="c"), comp)
    assert not res.ok and "invalid configuration" in res.detail


def test_start_rejects_malicious_runparam(tmp_path):
    comp = Component(id="c", name="c", kind=ComponentKind.SERVICE,
                     run_argv=("./app", "--host", "{param:host}"),
                     run_params=(RunParam("host", kind="str", validator="host", default="10.0.2.2"),))
    life = _life(tmp_path)
    res = life.start(Stack(id="s", name="s", main="c"), comp, params={"host": "x$(id)"})
    assert not res.ok and "invalid" in res.detail.lower()


def test_start_allows_valid_values(tmp_path):
    comp = Component(id="c", name="c", kind=ComponentKind.SERVICE,
                     run_argv=("./app", "-c", "{operator:callsign}"))
    life = _life(tmp_path, callsign="N0CALL-7")
    res = life.start(Stack(id="s", name="s", main="c"), comp)
    assert res.ok


def test_sync_word_validator():
    import lhpc.core.validators as V
    assert V.sync_word("0x12") == "0x12" and V.sync_word("0xFF") == "0xFF"
    assert V.sync_word("") == ""                              # blank = source default
    for bad in ("zz", "0x1FF", "18", "0x", "garbage", "0xGG"):
        with pytest.raises(V.ValidationError):
            V.sync_word(bad)


def test_voice_params_all_validate_defaults():
    # Every voice config-file param must be validated by its declared kind/validator, and its
    # own default (operator-substituted) must pass — no silently-unvalidated param.
    from lhpc.core.services import ControllerService
    from lhpc.core import validators as V
    svc = ControllerService()
    comp = next(c for c in svc.stack("voice").components if c.config_file)
    op = svc.config().operator
    for p in comp.config_file.params:
        raw = (p.default or "").replace("{callsign}", op.callsign or "N0CALL")
        V.validate_param(p, raw or "0")                       # must not raise
    names = {p.name for p in comp.config_file.params}
    assert {"preamble", "sync", "ldro"} <= names              # present, not missing
    assert next(p for p in comp.config_file.params if p.name == "freq").validator == "freq"
    assert next(p for p in comp.config_file.params if p.name == "sync").validator == "sync"
