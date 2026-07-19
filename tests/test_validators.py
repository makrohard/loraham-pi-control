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


@pytest.mark.parametrize("good", [
    "{runtime}/state/meshtasticd/ssl/private_key.pem",   # the meshtastic ssl_key/ssl_cert case
    "{runtime}/state/x/certificate.pem",
    "{source}/build/out",
    "{runtime}/logs/{band}/app.log",
    "/tmp/loraconf.sock",                                 # a plain absolute path still works
])
def test_path_value_accepts_paths_and_controller_placeholders(good):
    # A path may contain `/` and the exact controller placeholders {runtime}/{source}/{band}
    # (expanded to real paths before use) — the value is returned unchanged for later expansion.
    assert V.path_value(good) == good


@pytest.mark.parametrize("bad", [
    "{foo}/x",        # a NON-controller placeholder: stray braces still rejected
    "x{runtime",      # partial/unbalanced brace
    "a}b",
    "{runtime}/../etc/passwd",   # traversal still blocked even with a placeholder
    "a;b", "a|b", "a$b", "a`b`", "a>b", "a(b)",
])
def test_path_value_still_rejects_unsafe(bad):
    with pytest.raises(ValidationError):
        V.path_value(bad)


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
    cfg = Config(operator=OperatorConfig(callsign=callsign))
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


@pytest.mark.needs_session  # spawns a real process; identity_complete needs sid>0 (skips under sid==0)
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


def test_audit_port_rejects_zero():
    # AUDIT IN4: port 0 passed field validation but parse_endpoint requires >0.
    import pytest
    from lhpc.core import validators
    with pytest.raises(validators.ValidationError):
        validators.port("0")
    assert validators.port("1") == "1" and validators.port("65535") == "65535"


def test_audit_float_kind_enforces_bounds():
    # AUDIT IN3: float kind ignored declared min/max.
    import pytest
    from lhpc.core import validators
    class P:
        name = "f"; kind = "float"; min = 1.0; max = 10.0; validator = ""
    assert validators.validate_param(P(), "5.5") == "5.5"
    with pytest.raises(validators.ValidationError):
        validators.validate_param(P(), "20")
    with pytest.raises(validators.ValidationError):
        validators.validate_param(P(), "0.5")


def test_audit_positional_free_text_rejects_leading_dash():
    # AUDIT S2: a positional (no arg, no named validator) value starting with '-' would
    # be parsed as an option by a GNU target.
    import pytest
    from lhpc.core import validators
    class P:
        name = "pos"; kind = "str"; validator = ""; arg = ""
    assert validators.validate_param(P(), "value") == "value"
    with pytest.raises(validators.ValidationError):
        validators.validate_param(P(), "--output=/etc/x")


# --- bind: no-auth service-port allow-list (IPv4 address or CIDR) --------------------------------

@pytest.mark.parametrize("val,want", [
    ("127.0.0.1", "127.0.0.1"),          # bare loopback stays bare
    ("192.168.0.0/24", "192.168.0.0/24"),
    ("192.168.0.5/24", "192.168.0.0/24"),  # host bits masked to the network
    ("0.0.0.0/0", "0.0.0.0/0"),          # public parses (danger surfaced by the dashboard, not blocked)
    ("10.0.0.5", "10.0.0.5"),            # a bare host
])
def test_bind_accepts_and_normalizes(val, want):
    assert V.bind(val) == want


@pytest.mark.parametrize("bad", ["", "::1", "::1/128", "2001:db8::/32", "junk",
                                 "127.0.0.1; rm -rf", "10.0.0.0/33", "1.2.3.4/24/8",
                                 # bare 0.0.0.0 is ambiguous (bind idiom means "everyone",
                                 # allow-list /32 matches nobody) -> refused with guidance;
                                 # the honest spellings 0.0.0.0/0 and 127.0.0.1 stay accepted.
                                 "0.0.0.0", "0.0.0.0/32"])
def test_bind_rejects(bad):
    with pytest.raises(ValidationError):
        V.bind(bad)


def test_bind_is_registered_as_a_named_validator():
    p = RunParam(name="b", kind="str", validator="bind")
    assert V.validate_param(p, "192.168.0.0/24") == "192.168.0.0/24"
    with pytest.raises(ValidationError):
        V.validate_param(p, "::1")
