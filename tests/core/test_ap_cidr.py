"""NA (APCIDR): one setting, `network.ap_cidr`, for the access point's IPv4 subnet.

The validator and the derived AP address (`lhpc.core.ap_subnet`), the stored setting
(`[network] ap_cidr` in config/local.toml), and the consumers it drives through the existing AP
apply path (`network_ap_now`): the AP profile's `ipv4.addresses`, the firewall's AP subnet and the
"open https://<box>:8443" hint. The unset default keeps today's 10.42.0.0/24 everywhere.

nmcli is a fake that holds the AP profile's address and changes it only on an
`nmcli connection modify` of that profile; no host network state is read.
"""

from __future__ import annotations

import ast
import importlib
import ipaddress
import json
import os
import random
import subprocess
import sys

import pytest
from repo_paths import REPO

from lhpc.core import config as cfgmod
from lhpc.core import lifecycle as lcmod
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService

TODAY = "10.42.0.0/24"


def _asn():
    """The helper module, imported per test so its absence fails each test, not the file."""
    return importlib.import_module("lhpc.core.ap_subnet")


# ---- an independent oracle (integer arithmetic only, no ipaddress) --------------------------------

def _int(a, b, c, d):
    return (a << 24) | (b << 16) | (c << 8) | d


_RFC1918 = ((_int(10, 0, 0, 0), 8), (_int(172, 16, 0, 0), 12), (_int(192, 168, 0, 0), 16))


def _oracle_valid(addr: int, prefix: int) -> bool:
    if not 16 <= prefix <= 28:
        return False
    host_mask = (1 << (32 - prefix)) - 1
    if addr & host_mask:
        return False                                    # host bits set: refused, not normalised
    last = addr | host_mask
    return any(base <= addr and last <= base + (1 << (32 - p)) - 1 for base, p in _RFC1918)


def _text(addr: int, prefix: int) -> str:
    return ".".join(str((addr >> s) & 255) for s in (24, 16, 8, 0)) + f"/{prefix}"


# ==== T-NA1: the validator ==========================================================================

def test_default_is_todays_subnet_and_valid():
    """T-NA1 / N1: the default is NetworkManager's own shared-profile subnet, so an unset value
    changes nothing."""
    asn = _asn()
    assert asn.DEFAULT == TODAY
    net = asn.validate(asn.DEFAULT)
    assert type(net) is ipaddress.IPv4Network and net == ipaddress.IPv4Network(TODAY)


class _StrSub(str):
    pass


def _deep(n):
    v = "10.43.0.0/24"
    for _ in range(n):
        v = [v]
    return v


@pytest.mark.parametrize("value", [
    True, False, 0, 24, 10.42, float("nan"), None, ["10.43.0.0/24"], ("10.43.0.0/24",),
    {"ap_cidr": "10.43.0.0/24"}, {"10.43.0.0/24"}, b"10.43.0.0/24", bytearray(b"10.43.0.0/24"),
    ipaddress.IPv4Network("10.43.0.0/24"), _StrSub("10.43.0.0/24"), _deep(50), [], {}, object(),
], ids=lambda v: type(v).__name__)
def test_validate_refuses_every_non_string_type(value):
    """T-NA1 / N2 (fixed by the interface): only an exact `str` is a candidate; a bool, number,
    null, container, bytes, network object or str subclass is refused with the reason."""
    asn = _asn()
    with pytest.raises(asn.ApSubnetError, match="string"):
        asn.validate(value)


@pytest.mark.parametrize("value", [
    "", " ", "\t", "\n", " 10.43.0.0/24", "10.43.0.0/24 ", "\t10.43.0.0/24", "10.43.0.0/24\n",
    "10.43.0.0/24\r\n", "\u00a010.43.0.0/24", "10.43.0.0/24\u200b", "10.43.0.0 /24", "10.43.0.0/ 24",
])
def test_validate_refuses_empty_and_surrounding_whitespace(value):
    """T-NA1 / N2: nothing is stripped; empty or padded text is refused."""
    asn = _asn()
    with pytest.raises(asn.ApSubnetError):
        asn.validate(value)


@pytest.mark.parametrize("value", [
    "\uff11\uff10.43.0.0/24",                   # fullwidth digits
    "\u0661\u0660.\u0664\u0663.\u0660.\u0660/\u0662\u0664",   # Arabic-Indic digits
    "10.43.0.0/\uff12\uff14", "10.43.0\u0301.0/24", "10.43.0.0/24\x00", "10.43.0.0\x00/24",
    "10.43.0.0/24\ud800", "\u200f10.43.0.0/24", "10\u3002" "43.0.0/24",
])
def test_validate_refuses_non_ascii_and_control_characters(value):
    """T-NA1 / N2: encodings and look-alike digits never pass as an address."""
    asn = _asn()
    with pytest.raises(asn.ApSubnetError):
        asn.validate(value)


@pytest.mark.parametrize("length", [65, 1000, 100_000])
def test_validate_refuses_very_long_text(length):
    """T-NA1 / N2: overlong text is refused."""
    asn = _asn()
    value = "10.43.0.0/24" + "0" * (length - len("10.43.0.0/24"))
    with pytest.raises(asn.ApSubnetError):
        asn.validate(value)


@pytest.mark.parametrize("value", [
    "10.43.0.0", "10.43.0.0/", "/24", "10.43.0/24", "10.43.0.0.0/24", "10.43.0.0/24/24",
    "10.43.0.0/33", "10.43.0.0/-1", "10.43.0.0/+24", "10.43.0.0/255.255.255.0", "ap.local/24",
    "fd00::/64", "::ffff:10.43.0.0/120", "010.43.0.0/24", "10.43.0.0/024", "256.43.0.0/24",
    "10.43.0.0/24,192.168.1.0/24", "0x0a.43.0.0/24",
])
def test_validate_refuses_bad_syntax_and_ipv6(value):
    """T-NA1 / N3: not an IPv4 network/prefix, IPv6, or a prefix written non-canonically."""
    asn = _asn()
    with pytest.raises(asn.ApSubnetError):
        asn.validate(value)


@pytest.mark.parametrize("value, network", [
    ("10.43.0.5/24", "10.43.0.0/24"), ("10.43.0.1/24", "10.43.0.0/24"),
    ("192.168.77.17/28", "192.168.77.16/28"), ("172.16.1.0/16", "172.16.0.0/16"),
])
def test_validate_refuses_host_bits_and_names_the_network(value, network):
    """T-NA1 / N3b (fixed by the interface): host bits set are refused, naming the network
    address; the value is never silently normalised."""
    asn = _asn()
    with pytest.raises(asn.ApSubnetError) as ei:
        asn.validate(value)
    assert network in str(ei.value) and "host bits" in str(ei.value)


def test_validate_accepts_exactly_prefixes_16_to_28():
    """T-NA1 / N4: "a private IPv4 /16-/28"; every prefix 0..32 on a private base."""
    asn = _asn()
    for prefix in range(33):
        value = f"10.0.0.0/{prefix}"
        if 16 <= prefix <= 28:
            assert asn.validate(value) == ipaddress.IPv4Network(value)
        else:
            with pytest.raises(asn.ApSubnetError):
                asn.validate(value)


@pytest.mark.parametrize("value", [
    "8.8.8.0/24", "11.0.0.0/24", "127.0.0.0/24", "169.254.0.0/24", "224.0.0.0/24", "0.0.0.0/24",
    "100.64.0.0/24", "240.0.0.0/24", "255.255.255.0/24", "172.15.255.0/24", "172.32.0.0/24",
    "192.167.255.0/24", "192.169.0.0/24", "9.255.255.0/24", "11.0.0.0/16", "0.0.0.0/16",
])
def test_validate_refuses_non_rfc1918_networks(value):
    """T-NA1 / N5 (RFC 1918 fixed by the interface): public, loopback, link-local, multicast,
    0/8, shared 100.64/10 and reserved networks are refused."""
    asn = _asn()
    with pytest.raises(asn.ApSubnetError):
        asn.validate(value)


@pytest.mark.parametrize("value", [
    "10.0.0.0/16", "10.255.255.240/28", "172.16.0.0/16", "172.31.255.0/24", "192.168.0.0/16",
    "192.168.255.240/28", "10.43.0.0/24", "172.20.0.0/16", "192.168.77.16/28",
])
def test_validate_accepts_rfc1918_edges(value):
    """T-NA1 / N5: the edges of the three private blocks are accepted, unchanged."""
    asn = _asn()
    net = asn.validate(value)
    assert type(net) is ipaddress.IPv4Network and str(net) == value


def test_validate_agrees_with_an_independent_oracle():
    """T-NA1 / N3b-N5 property: seeded random (address, prefix) texts, host bits included; the
    validator accepts exactly what the integer oracle accepts."""
    asn = _asn()
    rng = random.Random(0x0A2A)                          # noqa: S311 (seeded, not secret)
    blocks = [_int(10, 0, 0, 0), _int(172, 16, 0, 0), _int(192, 168, 0, 0), 0]
    accepted = refused = 0
    for _ in range(3000):
        base = rng.choice(blocks)
        addr = (base | rng.getrandbits(20)) if base else rng.getrandbits(32)
        prefix = rng.randint(0, 32)
        if rng.random() < 0.7:
            addr &= ~((1 << (32 - prefix)) - 1) & 0xFFFFFFFF
        text = _text(addr, prefix)
        if _oracle_valid(addr, prefix):
            assert asn.validate(text) == ipaddress.IPv4Network(text), text
            accepted += 1
        else:
            with pytest.raises(asn.ApSubnetError):
                asn.validate(text)
            refused += 1
    assert accepted > 300 and refused > 300                  # both sides of the rule were drawn


# ==== C3: the AP address ============================================================================

@pytest.mark.parametrize("cidr, first, address", [
    ("192.168.77.16/28", "192.168.77.17", "192.168.77.17/28"),
    ("172.20.0.0/16", "172.20.0.1", "172.20.0.1/16"),
    (TODAY, "10.42.0.1", "10.42.0.1/24"),
    ("10.43.0.0/24", "10.43.0.1", "10.43.0.1/24"),
])
def test_first_host_and_ap_address_for_16_24_28(cidr, first, address):
    """C3: the AP address is the network + 1 with the network's prefix (nmcli ipv4.addresses)."""
    asn = _asn()
    net = asn.validate(cidr)
    host = asn.first_host(net)
    assert type(host) is ipaddress.IPv4Address and str(host) == first
    got = asn.ap_address(net)
    assert type(got) is str and got == address


def test_first_host_property_over_random_valid_networks():
    """C3 property: for every valid network, the first host is network + 1, inside it, and
    neither the network nor the broadcast address."""
    asn = _asn()
    rng = random.Random(0x10AF)                          # noqa: S311 (seeded, not secret)
    for _ in range(500):
        prefix = rng.randint(16, 28)
        base, plen = rng.choice(_RFC1918)
        addr = (base | rng.getrandbits(32 - plen)) & ~((1 << (32 - prefix)) - 1) & 0xFFFFFFFF
        net = asn.validate(_text(addr, prefix))
        host = asn.first_host(net)
        assert int(host) == addr + 1
        assert host in net and host not in (net.network_address, net.broadcast_address)
        assert asn.ap_address(net) == f"{_text(addr + 1, prefix).split('/')[0]}/{prefix}"


@pytest.mark.parametrize("value", [
    "10.43.0.0/24", None, True, ipaddress.IPv6Network("fd00::/64"),
    ipaddress.IPv4Address("10.43.0.1"), ipaddress.IPv4Network("8.8.8.0/24"),
    ipaddress.IPv4Network("10.43.0.0/29"), ipaddress.IPv4Interface("10.43.0.1/24"),
], ids=repr)
def test_first_host_and_ap_address_refuse_unvalidated_input(value):
    """C3: the address is a function of the validated network only."""
    asn = _asn()
    with pytest.raises(asn.ApSubnetError):
        asn.first_host(value)
    with pytest.raises(asn.ApSubnetError):
        asn.ap_address(value)


# ==== N6 / N6b / N7: overlap with the box's networks (injected, never read) ===========================

def _n(*texts):
    return [ipaddress.IPv4Network(t) for t in texts]


def _tri(asn, net, others):
    got = asn.overlap(net, others)
    assert type(got) is str and got in ("overlap", "clear", "unknown")
    return got


@pytest.mark.parametrize("others", [
    None, tuple(_n("192.168.178.0/24")), set(_n("192.168.178.0/24")),
    {"lan": ipaddress.IPv4Network("192.168.178.0/24")}, "192.168.178.0/24",
    (n for n in _n("192.168.178.0/24")), 0, False, True,
], ids=lambda v: type(v).__name__)
def test_overlap_unknown_when_the_networks_could_not_be_read(others):
    """N6b (fixed by the interface): the box's networks unread (None) or not a list is UNKNOWN,
    never "no overlap"."""
    asn = _asn()
    assert _tri(asn, asn.validate("10.43.0.0/24"), others) == "unknown"


@pytest.mark.parametrize("bad", [
    "192.168.1.0/24", None, False, ipaddress.IPv4Address("192.168.1.1"),
    ipaddress.IPv6Network("fd00::/64"), _n("192.168.1.0/24"), ipaddress.IPv4Interface("10.43.0.1/24"),
], ids=repr)
def test_overlap_unknown_on_any_malformed_element(bad):
    """N6b: one element that is not an IPv4 network makes the whole answer UNKNOWN, even beside
    an element that overlaps."""
    asn = _asn()
    net = asn.validate("10.43.0.0/24")
    assert _tri(asn, net, [*_n("192.168.178.0/24"), bad]) == "unknown"
    assert _tri(asn, net, [bad, *_n("10.43.0.0/24")]) == "unknown"


@pytest.mark.parametrize("lan", ["10.43.0.0/24", "10.43.0.128/25", "10.43.5.0/24", "10.0.0.0/8",
                                 "10.43.0.7/32"])
def test_overlap_found_for_equal_inner_and_outer_networks(lan):
    """N6: the LAN equals, lies inside or contains the candidate."""
    asn = _asn()
    assert _tri(asn, asn.validate("10.43.0.0/16"), _n(lan)) == "overlap"


def test_overlap_checks_every_network_given():
    """N7 (helper level): a LAN, a VPN and a Docker bridge; any one overlapping is an overlap,
    duplicates change nothing."""
    asn = _asn()
    others = _n("192.168.178.0/24", "10.8.0.0/24", "172.17.0.0/16", "172.17.0.0/16")
    assert _tri(asn, asn.validate("172.17.5.0/24"), others) == "overlap"
    assert _tri(asn, asn.validate("10.8.0.0/28"), others) == "overlap"
    assert _tri(asn, asn.validate("10.43.0.0/24"), others) == "clear"


def test_overlap_clear_when_disjoint_including_adjacent():
    """N6/N7: disjoint networks, the adjacent ones included, are clear."""
    asn = _asn()
    net = asn.validate("10.42.0.0/24")
    assert _tri(asn, net, _n("10.42.1.0/24", "10.41.255.0/24", "192.168.0.0/16")) == "clear"


def test_overlap_unknown_for_an_empty_list():
    """N6b: an empty route read is UNKNOWN, not "no overlap" (fail closed: the caller may pass
    the parsed, empty list)."""
    asn = _asn()
    assert _tri(asn, asn.validate("10.43.0.0/24"), []) == "unknown"


@pytest.mark.parametrize("net", ["10.43.0.0/24", ipaddress.IPv4Network("8.8.8.0/24"), None],
                         ids=repr)
def test_overlap_refuses_an_unvalidated_candidate(net):
    asn = _asn()
    with pytest.raises(asn.ApSubnetError):
        asn.overlap(net, _n("192.168.178.0/24"))


# ==== the helper is pure and not wired yet ==========================================================

def test_ap_subnet_imports_only_the_cidr_validator():
    """The helper loads no product module beyond the existing CIDR validator's own imports
    (a fresh interpreter, so this run's imports do not mask it)."""
    code = ("import sys, json; import {m}; "
            "print(json.dumps(sorted(k for k in sys.modules if k.startswith('lhpc'))))")
    env = {**os.environ, "PYTHONPATH": str(REPO)}

    def loaded(module):
        out = subprocess.run([sys.executable, "-c", code.format(m=module)], cwd=str(REPO),
                             capture_output=True, text=True, timeout=60, check=True,
                             env=env).stdout
        return set(json.loads(out))

    assert loaded("lhpc.core.ap_subnet") == loaded("lhpc.core.validators") | {"lhpc.core.ap_subnet"}


def test_no_existing_module_imports_ap_subnet():
    """The module is added unwired: it exists, and no module under lhpc/ imports it. A whole-
    package negative invariant no driven path can prove (tests/README rule 1 exception); the
    change that wires it into the consumers replaces this test."""
    assert (REPO / "lhpc" / "core" / "ap_subnet.py").is_file()
    importers = []
    for path in sorted((REPO / "lhpc").rglob("*.py")):
        if path.name == "ap_subnet.py":
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""] + [a.name for a in node.names]
            if any(n == "ap_subnet" or n.endswith(".ap_subnet") for n in names):
                importers.append(str(path.relative_to(REPO)))
    assert importers == []


# ==== the stored setting (config/local.toml [network] ap_cidr) =======================================

def _local(tmp_path, text):
    d = tmp_path / "config"
    d.mkdir(parents=True, exist_ok=True)
    p = d / "local.toml"
    p.write_text(text, encoding="utf-8")
    os.chmod(p, 0o600)
    return p


def test_unset_setting_is_todays_default(tmp_path):
    """T-NA3 / N1: no [network] table -> the effective setting is 10.42.0.0/24."""
    cfg = cfgmod.load_config(Paths(runtime_root=tmp_path))
    assert cfg.network == cfgmod.NetworkConfig(ap_cidr=TODAY)
    assert type(cfg.network.ap_cidr) is str
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    assert svc.ap_cidr() == TODAY


@pytest.mark.parametrize("literal", [
    "true", "false", "0", "24", "10.42", "nan", '""', '" 10.43.0.0/24"', '"10.43.0.5/24"',
    '"8.8.8.0/24"', '"10.0.0.0/8"', '"10.43.0.0/29"', '"fd00::/64"', '"10.43.0.0"',
    '["10.43.0.0/24"]', '[[[["10.43.0.0/24"]]]]', "{ cidr = \"10.43.0.0/24\" }",
    "1979-05-27T07:32:00Z", "1979-05-27", '"\uff11\uff10.43.0.0/24"',
])
def test_an_invalid_stored_value_is_a_config_error_naming_the_key(tmp_path, literal):
    """T-NA1 through the loader: an invalid stored value of any TOML type is the loader's config
    error naming network.ap_cidr, never a silent default."""
    _local(tmp_path, f"[network]\nap_cidr = {literal}\n")
    with pytest.raises(cfgmod.ConfigError) as ei:
        cfgmod.load_config(Paths(runtime_root=tmp_path))
    assert "network.ap_cidr" in str(ei.value)


@pytest.mark.parametrize("value", ["10.43.0.0/24", "192.168.77.16/28", "172.20.0.0/16"])
def test_a_valid_stored_value_is_the_effective_setting(tmp_path, value):
    """The stored value is the one source: the loader and ap_cidr() return it unchanged."""
    _local(tmp_path, f'[network]\nap_cidr = "{value}"\n')
    assert cfgmod.load_config(Paths(runtime_root=tmp_path)).network.ap_cidr == value
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    assert svc.ap_cidr() == value


@pytest.mark.parametrize("bad", [True, 24, 10.42, None, ["10.43.0.0/24"], {"a": 1}, b"10.43.0.0/24",
                                 "", " 10.43.0.0/24", "10.43.0.5/24", "8.8.8.0/24", "10.0.0.0/8",
                                 "fd00::/64", "DEFAULT", "default "], ids=repr)
def test_set_network_writes_removes_and_refuses_without_touching_the_file(tmp_path, bad):
    """The writer validates first: a refused value leaves local.toml byte-identical; a valid one
    is stored and returned; "default" removes the key (the effective value is today's)."""
    paths = Paths(runtime_root=tmp_path)
    p = _local(tmp_path, '[operator]\ncallsign = "XX0XXA"\n')
    got = cfgmod.set_network(paths, ap_cidr="10.43.0.0/24")
    assert type(got) is str and got == "10.43.0.0/24"
    assert cfgmod.load_config(paths).network.ap_cidr == "10.43.0.0/24"
    before = p.read_bytes()
    with pytest.raises((ValueError, cfgmod.ConfigError)):
        cfgmod.set_network(paths, ap_cidr=bad)
    assert p.read_bytes() == before
    assert cfgmod.set_network(paths, ap_cidr="default") == TODAY
    cfg = cfgmod.load_config(paths)
    assert cfg.network.ap_cidr == TODAY and "ap_cidr" not in (cfg.values.get("network") or {})
    assert cfg.operator.callsign == "XX0XXA"


def test_the_last_written_value_is_the_one_setting(tmp_path):
    """N9d (writer level): the console and the CLI both write through set_network; one stored
    value, the last successful write wins, a refused write in between changes nothing."""
    paths = Paths(runtime_root=tmp_path)
    assert cfgmod.set_network(paths, ap_cidr="10.43.0.0/24") == "10.43.0.0/24"
    assert cfgmod.set_network(paths, ap_cidr="192.168.77.16/28") == "192.168.77.16/28"
    with pytest.raises((ValueError, cfgmod.ConfigError)):
        cfgmod.set_network(paths, ap_cidr="8.8.8.0/24")
    cfg = cfgmod.load_config(paths)
    assert cfg.network.ap_cidr == "192.168.77.16/28"
    assert cfg.values["network"] == {"ap_cidr": "192.168.77.16/28"}


# ==== T-NA2 / T-NA3 / T-NA4: the consumers through the existing AP apply path ==========================

NM = "/usr/bin/nmcli"
AP_UUID = "AP-UUID-1"
_LISTING = f"{AP_UUID}:lhpc-ap:802-11-wireless:yes:0\nCL-UUID-2:HomeNet:802-11-wireless:yes:10\n"
_PERMS = ("org.freedesktop.NetworkManager.network-control:yes\n"
          "org.freedesktop.NetworkManager.settings.modify.system:yes\n"
          "org.freedesktop.NetworkManager.wifi.share.open:yes\n"
          "org.freedesktop.NetworkManager.wifi.share.protected:yes\n")


class _FakeNm:
    """nmcli with one piece of state: the lhpc-ap profile's ipv4.addresses ("" = NM's shared
    default). The box is a client of HomeNet (192.168.178.0/24) on wlan0."""

    def __init__(self, addr=""):
        self.addr = addr
        self.calls: list = []

    def _fields(self):
        return {"connection.interface-name": "wlan0", "ipv4.method": "shared",
                "ipv4.addresses": self.addr, "connection.id": "lhpc-ap",
                "connection.uuid": AP_UUID}

    def reply(self, args):
        joined = " ".join(args)
        if "modify" in args:
            rest = args[args.index("modify") + 1:]
            if rest[:1] in (["id"], ["uuid"]):
                rest = rest[1:]
            if rest and rest[0] in ("lhpc-ap", AP_UUID):
                props = rest[1:]
                for key, value in zip(props[0::2], props[1::2], strict=False):
                    if key.lstrip("+") == "ipv4.addresses":
                        self.addr = value
            return 0, "", ""
        if "general permissions" in joined:
            return 0, _PERMS, ""
        if "connection show --active" in joined:
            return 0, "CL-UUID-2:HomeNet:802-11-wireless:wlan0\n", ""
        if "UUID,NAME,TYPE,AUTOCONNECT,AUTOCONNECT-PRIORITY" in joined:
            return 0, _LISTING, ""
        if "connection show" in joined and ("lhpc-ap" in args or AP_UUID in args):
            flag = "-g" if "-g" in args else ("-f" if "-f" in args else None)
            if flag is None:
                return 0, "".join(f"{k}:{v}\n" for k, v in self._fields().items()), ""
            names = args[args.index(flag) + 1].split(",")
            vals = self._fields()
            if flag == "-g":
                return 0, "".join(f"{vals.get(n, '')}\n" for n in names), ""
            return 0, "".join(f"{n}:{vals.get(n, '')}\n" for n in names), ""
        if "IP4.ADDRESS" in joined and "device show" in joined:
            return 0, "192.168.178.20/24\n", ""
        if "station dump" in joined:
            return 0, "", ""
        if "reapply" in args or "up" in args:
            return 0, "", ""
        return 1, "", "no fake nmcli reply"

    def effective_network(self):
        return ipaddress.ip_interface(self.addr or "10.42.0.1/24").network


def _with_fake_nm(system, nm):
    """Route every nmcli/iw call of `system` to `nm`; other commands go to the FakeSystem."""
    orig = system.runner.run

    def run(argv, timeout=None, *a, **k):
        if argv and os.path.basename(argv[0]) in ("nmcli", "iw"):
            nm.calls.append(list(argv))
            rc, out, err = nm.reply(list(argv[1:]))

            class R:
                returncode, stdout, stderr = rc, out, err
            return R
        return orig(argv, timeout, *a, **k)
    system.runner.run = run
    return nm


def _modified(nm):
    return [c for c in nm.calls if "modify" in c and ("lhpc-ap" in c or AP_UUID in c)]


def _ap_box(tmp_path, monkeypatch, *, local=None, profile_addr="", live_for):
    """An AP-managed box on HomeNet with the managed firewall installed. The AP rules count as
    live only for (wlan0, `live_for`) — the stubs below stand in for the root helper's receipt,
    as the CO-16 tests in tests/host do; the finalize helper is not spawned."""
    if local is not None:
        _local(tmp_path, local)
    (tmp_path / "config" / "stacks").mkdir(parents=True, exist_ok=True)
    (tmp_path / "state").mkdir(exist_ok=True)
    svc = ControllerService(system=FakeSystem(files={NM: "x", "/proc/uptime": "100.0 200.0\n"}).system,
                            paths=Paths(runtime_root=tmp_path))
    nm = _with_fake_nm(svc._system, _FakeNm(profile_addr))
    # collaborators of the root firewall helper (its receipt), stubbed as in tests/host
    monkeypatch.setattr(svc, "_fw_integration_present", lambda: True)
    monkeypatch.setattr(svc, "firewall_status", lambda: {"config_ok": False, "live_ok": False})
    monkeypatch.setattr(svc, "_fw_ap_live", lambda iface, cidr: (iface, cidr) == ("wlan0", live_for))
    monkeypatch.setattr(lcmod, "current_boot_id", lambda: "boot-1")
    spawned: list = []
    monkeypatch.setattr(lcmod.Lifecycle, "_real_spawn",
                        lambda self, argv, log, cwd=None, env=None: spawned.append(list(argv)) or 4242)
    return svc, nm, spawned


@pytest.mark.parametrize("fw_saved", [None, "10.43.0.0/24"], ids=["fw-unsaved", "fw-saved-candidate"])
@pytest.mark.parametrize("profile_addr", ["", "10.42.0.1/24"])
def test_stored_setting_and_ap_apply_agree_on_the_candidate(tmp_path, monkeypatch, profile_addr,
                                                            fw_saved):
    """T-NA4 (C2a, behavioural route C2b; C9's PROCEED case): network.ap_cidr = 10.43.0.0/24 is
    stored and the firewall's AP rules are live for the CANDIDATE (C9: the gate holds for the new
    subnet, the firewall first); then the existing AP apply path runs. Afterwards the AP profile's
    address and the firewall's AP subnet BOTH lie in the candidate network; an apply that leaves
    either in 10.42.0.0/24 fails."""
    svc, nm, spawned = _ap_box(tmp_path, monkeypatch, local='[network]\nap_cidr = "10.43.0.0/24"\n',
                               profile_addr=profile_addr, live_for="10.43.0.0/24")
    if fw_saved is not None:
        cfgmod.save_firewall_config(svc._paths, ap_enabled=True, ap_interface="wlan0", ap_cidr=fw_saved)
        svc._invalidate_config()
    res = svc.network_ap_now(apply=True)
    assert nm.addr == "10.43.0.1/24", "the AP profile's address is not in the candidate network"
    assert svc.firewall_candidate()["ap"]["cidr"] == "10.43.0.0/24"
    assert res.ok, res.summary
    # C9 (OPEN-1 reading): the gate is evaluated after the operation's firewall apply step and holds when the
    # firewall's AP subnet is SAVED for the candidate and LIVE for it (the stub `live_for`); the saved half:
    svc._invalidate_config()
    assert cfgmod.load_config(svc._paths).firewall.ap_cidr == "10.43.0.0/24", "the firewall AP subnet is not saved for the candidate"
    assert spawned and AP_UUID in spawned[0]


@pytest.mark.parametrize("saved, live_for", [(TODAY, TODAY), ("10.43.0.0/24", None), (TODAY, "10.43.0.0/24")],
                         ids=["old-rules-saved-and-live", "candidate-saved-not-live", "old-saved-candidate-live"])
@pytest.mark.parametrize("profile_addr", ["", "10.42.0.1/24"])
def test_the_ap_start_refuses_a_candidate_the_live_rules_do_not_cover(tmp_path, monkeypatch,
                                                                       profile_addr, saved, live_for):
    """C9 (the maintainer's CO-16 ruling: a non-usable AP is a bad bug): the gate must hold for the
    stored candidate 10.43.0.0/24 (saved AND live) before anything is modified. Two refusals: the
    rules saved and live cover only the OLD subnet 10.42.0.0/24; or the rules are saved for the
    candidate but not live; or the rules are live for the candidate while the saved (by hand) firewall AP
    subnet is the OLD one (the saved half of the gate: a subnet saved by hand is never overridden). Either way the switch is REFUSED naming the candidate, and nothing
    changes: no nmcli modify of the AP profile, its address unchanged, no finalize helper spawned,
    no pending record, the stored firewall config and the firewall's AP subnet as before."""
    svc, nm, spawned = _ap_box(tmp_path, monkeypatch, local='[network]\nap_cidr = "10.43.0.0/24"\n',
                               profile_addr=profile_addr, live_for=live_for)
    cfgmod.save_firewall_config(svc._paths, ap_enabled=True, ap_interface="wlan0", ap_cidr=saved)
    svc._invalidate_config()
    fw_before = cfgmod.load_config(svc._paths).firewall
    ap_before = svc.firewall_candidate()["ap"]["cidr"]
    res = svc.network_ap_now(apply=True)
    assert not res.ok, "the switch went on while the AP rules do not cover the candidate subnet"
    assert _modified(nm) == [] and nm.addr == profile_addr
    assert spawned == [] and not svc._net_pending_path().exists()
    svc._invalidate_config()
    assert cfgmod.load_config(svc._paths).firewall == fw_before, "the refusal changed the firewall config"
    assert svc.firewall_candidate()["ap"]["cidr"] == ap_before == saved
    assert "10.43.0.0/24" in " ".join([res.summary, *res.details]), "the refusal does not name the candidate"


@pytest.mark.parametrize("cidr, first, address", [
    ("10.43.0.0/24", "10.43.0.1", "10.43.0.1/24"),
    ("192.168.77.16/28", "192.168.77.17", "192.168.77.17/28"),
    ("172.20.0.0/16", "172.20.0.1", "172.20.0.1/16"),
])
def test_one_setting_drives_the_profile_firewall_and_hint(tmp_path, monkeypatch, cidr, first, address):
    """T-NA2 (C1, consumers 1, 2 and 4): one value renders the AP profile's ipv4.addresses
    (<first host>/<prefix>), the firewall's AP subnet and the "open https://<first host>:8443"
    hint; nothing names 10.42.0.1. The AP rules are live for the candidate (C9's PROCEED case);
    no firewall AP subnet was saved by hand, so consumer (2) is the setting's default."""
    svc, nm, _ = _ap_box(tmp_path, monkeypatch, local=f'[network]\nap_cidr = "{cidr}"\n',
                         live_for=cidr)
    dry = svc.network_ap_now(apply=False)
    assert dry.ok
    assert any(f"https://{first}:8443" in d for d in dry.details), dry.details
    assert not any("10.42.0.1" in d for d in dry.details)
    assert nm.addr == ""                                           # a dry run changes nothing
    res = svc.network_ap_now(apply=True)
    assert res.ok, res.summary
    assert nm.addr == address
    assert svc.firewall_candidate()["ap"]["cidr"] == cidr
    svc._invalidate_config()                                       # C9's saved half, as T-NA4
    assert cfgmod.load_config(svc._paths).firewall.ap_cidr == cidr
    assert f"https://{first}:8443" in res.summary and "10.42.0.1" not in res.summary


def _phrase(svc):
    """The apply's own confirm phrase, read from the service (the literal is the service's, not the test's)."""
    return type(svc).AP_CIDR_CONFIRM


def test_an_auto_saved_old_subnet_follows_the_apply_and_the_ap_start_proceeds(tmp_path, monkeypatch):
    """C9 rule (round 5): the AP start of an earlier version saved the profile's subnet 10.42.0.0/24 into the
    firewall config. It equals the previous effective setting (the default), so the one apply moves it to the
    candidate with the setting; the AP start then proceeds. (A 0.13.x box upgrading is this case.)"""
    svc, nm, spawned = _ap_box(tmp_path, monkeypatch, profile_addr="10.42.0.1/24", live_for="10.43.0.0/24")
    cfgmod.save_firewall_config(svc._paths, ap_enabled=True, ap_interface="wlan0", ap_cidr=TODAY)
    svc._invalidate_config()
    res = svc.network_set_ap_cidr("10.43.0.0/24", _phrase(svc))
    assert res.ok, res.summary
    svc._invalidate_config()
    assert cfgmod.load_config(svc._paths).firewall.ap_cidr == "10.43.0.0/24"
    assert cfgmod.load_config(svc._paths).network.ap_cidr == "10.43.0.0/24"
    res = svc.network_ap_now(apply=True)
    assert res.ok, res.summary
    assert nm.addr == "10.43.0.1/24" and spawned and AP_UUID in spawned[0]


def test_a_second_change_follows_again_and_the_ap_start_proceeds(tmp_path, monkeypatch):
    """C9 rule (round 5): a first change, then the AP start, which saves the first candidate as the firewall's AP
    subnet (nothing was saved); at the second change that saved subnet is the previous effective setting, so it
    follows again and the next AP start proceeds."""
    svc, nm, spawned = _ap_box(tmp_path, monkeypatch, profile_addr="10.42.0.1/24", live_for="10.43.0.0/24")
    live = {("wlan0", "10.43.0.0/24"), ("wlan0", "192.168.77.16/28")}     # the root apply made each live in turn
    monkeypatch.setattr(svc, "_fw_ap_live", lambda iface, cidr: (iface, cidr) in live)
    assert svc.network_set_ap_cidr("10.43.0.0/24", _phrase(svc)).ok
    assert svc.network_ap_now(apply=True).ok
    svc._invalidate_config()
    assert cfgmod.load_config(svc._paths).firewall.ap_cidr == "10.43.0.0/24"
    spawned.clear()
    svc._net_pending_path().unlink()      # the finalize helper (stubbed here) removes its record when it completes
    res = svc.network_set_ap_cidr("192.168.77.16/28", _phrase(svc))
    assert res.ok, res.summary
    svc._invalidate_config()
    assert cfgmod.load_config(svc._paths).firewall.ap_cidr == "192.168.77.16/28"
    res = svc.network_ap_now(apply=True)
    assert res.ok, res.summary
    assert nm.addr == "192.168.77.17/28" and spawned


def test_a_firewall_subnet_apart_from_the_setting_is_kept_and_the_ap_start_refuses_naming_both(tmp_path,
                                                                                           monkeypatch):
    """C9 rule (round 5): a firewall AP subnet that differs from the previous effective setting was chosen apart
    from the setting (`lhpc firewall --ap-cidr`). The apply keeps it; the AP start refuses, names both values and
    the command that aligns them, and changes nothing."""
    svc, nm, spawned = _ap_box(tmp_path, monkeypatch, profile_addr="10.42.0.1/24", live_for="10.43.0.0/24")
    cfgmod.save_firewall_config(svc._paths, ap_enabled=True, ap_interface="wlan0", ap_cidr="192.168.4.0/24")
    svc._invalidate_config()
    assert svc.network_set_ap_cidr("10.43.0.0/24", _phrase(svc)).ok
    svc._invalidate_config()
    fw_before = cfgmod.load_config(svc._paths).firewall
    assert fw_before.ap_cidr == "192.168.4.0/24"
    res = svc.network_ap_now(apply=True)
    assert not res.ok
    assert res.data.get("reason") == "ap-firewall-subnet-differs"
    text = " ".join([res.summary, *res.details, *(res.next_commands or [])])
    assert "10.43.0.0/24" in text and "192.168.4.0/24" in text, text
    assert "lhpc firewall --ap on --ap-interface wlan0 --ap-cidr 10.43.0.0/24" in text, text
    assert _modified(nm) == [] and nm.addr == "10.42.0.1/24"
    assert spawned == [] and not svc._net_pending_path().exists()
    svc._invalidate_config()
    assert cfgmod.load_config(svc._paths).firewall == fw_before


def test_the_open_hint_follows_the_setting(tmp_path, monkeypatch):
    """C14: the user-visible 10.42.0.1 literals of the AP switch
    (dev:service_network.py:909/:977 at 6c69faa2) read the setting."""
    svc, _, _ = _ap_box(tmp_path, monkeypatch, local='[network]\nap_cidr = "192.168.50.0/24"\n',
                        live_for="192.168.50.0/24")
    dry = svc.network_ap_now(apply=False)
    assert dry.ok and any("https://192.168.50.1:8443" in d for d in dry.details), dry.details
    res = svc.network_ap_now(apply=True)
    assert res.ok and "https://192.168.50.1:8443" in res.summary, res.summary


def test_unset_setting_keeps_todays_ap_firewall_and_hint(tmp_path, monkeypatch):
    """T-NA3 (GOLDEN, a control: it holds before and after the change): without the setting the
    AP stays on 10.42.0.0/24, the firewall's AP subnet is 10.42.0.0/24 and the hint names
    https://10.42.0.1:8443, as today."""
    svc, nm, spawned = _ap_box(tmp_path, monkeypatch, live_for=TODAY)
    dry = svc.network_ap_now(apply=False)
    assert dry.ok and any("https://10.42.0.1:8443" in d for d in dry.details)
    res = svc.network_ap_now(apply=True)
    assert res.ok, res.summary
    assert str(nm.effective_network()) == TODAY
    assert svc.firewall_candidate()["ap"]["cidr"] == TODAY
    assert "https://10.42.0.1:8443" in res.summary and spawned


# ==== the apply: refusals through the service and the CLI (C6b, N9c) =================================

_WRONG_PHRASE = "not-the-confirm-phrase"
_REFUSED_VALUES = ["10.43.0.5/24", "8.8.8.0/24", "10.0.0.0/8", "10.43.0.0/29", "fd00::/64", "",
                   " 10.43.0.0/24", "\uff11\uff10.43.0.0/24", "10.43.0.0", "x" * 300]


@pytest.mark.parametrize("value", _REFUSED_VALUES)
def test_the_apply_refuses_an_invalid_value_and_changes_nothing(tmp_path, monkeypatch, value):
    """N2-N5 through the one apply (network_set_ap_cidr): refused, nothing stored, the AP profile
    not modified."""
    svc, nm, _ = _ap_box(tmp_path, monkeypatch, live_for=TODAY)
    res = svc.network_set_ap_cidr(value, _WRONG_PHRASE)
    assert not res.ok
    assert cfgmod.load_config(Paths(runtime_root=tmp_path)).network.ap_cidr == TODAY
    assert _modified(nm) == [] and nm.addr == ""


@pytest.mark.parametrize("phrase", ["", _WRONG_PHRASE], ids=["missing", "wrong"])
def test_the_apply_refuses_a_wrong_confirm_phrase_and_changes_nothing(tmp_path, monkeypatch, phrase):
    """N9: the apply needs the confirm phrase of the other network writes: a missing or wrong
    phrase is refused, stores nothing and modifies no profile."""
    svc, nm, _ = _ap_box(tmp_path, monkeypatch, live_for="10.43.0.0/24")
    res = svc.network_set_ap_cidr("10.43.0.0/24", phrase)
    assert not res.ok
    assert cfgmod.load_config(Paths(runtime_root=tmp_path)).network.ap_cidr == TODAY
    assert _modified(nm) == [] and nm.addr == ""


_CLI_NM: list = []


def _cli_provider(paths):
    """LHPC_SYSTEM_PROVIDER factory: every ControllerService the CLI builds gets a FakeSystem
    whose nmcli is the test's fake (no real nmcli, no host network state)."""
    import types
    fake = FakeSystem(files={NM: "x", "/proc/uptime": "100.0 200.0\n"})
    _with_fake_nm(fake.system, _CLI_NM[-1])
    return types.SimpleNamespace(system=fake.system, manifest_path=None, wrap_spawn=None)


def _cli(argv):
    from lhpc.adapters.cli.main import main
    try:
        return main(argv)
    except SystemExit as exc:                          # argparse: unknown command or flag
        return exc.code


@pytest.fixture
def cli_box(tmp_path, monkeypatch, capsys):
    rt = tmp_path / "rt"
    monkeypatch.setenv("LHPC_RUNTIME_ROOT", str(rt))
    monkeypatch.setenv("LHPC_SYSTEM_PROVIDER", f"{__name__}:_cli_provider")
    monkeypatch.setattr(sys.modules[__name__], "_CLI_NM", [_FakeNm("")])
    assert _cli(["bootstrap", "--yes"]) == 0
    capsys.readouterr()
    return rt, _CLI_NM[-1]


def _refused_cleanly(capsys):
    """The refusal is the command's own: no traceback and not the generic stored-config failure
    (main's ConfigError path, which also exits 1). Returns the printed text."""
    out = capsys.readouterr()
    text = out.out + out.err
    assert "Traceback" not in text
    assert "stored configuration file is unreadable or malformed" not in text
    return text


@pytest.mark.parametrize("value", _REFUSED_VALUES)
def test_the_network_command_refuses_an_invalid_value(cli_box, capsys, value):
    """N9c (decided: `lhpc network --ap-cidr <CIDR|default> --confirm-phrase <phrase>`): the
    command parses, refuses an invalid value with exit 1 and its own reason (the validator's
    rule names network.ap_cidr; the phrase literal is not known here, so a refusal naming the
    confirm phrase first is accepted too), stores nothing and modifies no profile."""
    rt, nm = cli_box
    rc = _cli(["network", "--ap-cidr", value, "--confirm-phrase", _WRONG_PHRASE])
    text = _refused_cleanly(capsys)
    assert rc == 1
    assert "network.ap_cidr" in text or "confirm" in text.lower(), text
    assert cfgmod.load_config(Paths(runtime_root=rt)).network.ap_cidr == TODAY
    assert _modified(nm) == []


@pytest.mark.parametrize("phrase_args", [[], ["--confirm-phrase", ""], ["--confirm-phrase", _WRONG_PHRASE]],
                         ids=["absent", "empty", "wrong"])
def test_the_network_command_refuses_without_the_confirm_phrase(cli_box, capsys, phrase_args):
    """N9c: a valid value without the confirm phrase (absent, empty or wrong) is refused with
    exit 1 and a reason naming the confirm phrase; nothing is stored and no profile is
    modified."""
    rt, nm = cli_box
    rc = _cli(["network", "--ap-cidr", "10.43.0.0/24", *phrase_args])
    text = _refused_cleanly(capsys)
    assert rc == 1
    assert "confirm" in text.lower(), text
    assert cfgmod.load_config(Paths(runtime_root=rt)).network.ap_cidr == TODAY
    assert _modified(nm) == []


@pytest.mark.parametrize("local", [None, '[network]\nap_cidr = "10.43.0.0/24"\n'], ids=["unset", "set"])
def test_a_hand_edited_profile_still_shows_the_firewall_gap(tmp_path, monkeypatch, local):
    """C2 (GOLDEN, a control: it holds before and after the change): the profile was edited by
    hand to 192.168.4.1/24 while the firewall's AP rules are saved for 10.42.0.0/24. The existing
    gap report names the profile's own interface and subnet, as today; a stored setting does not
    mask the hand edit."""
    svc, nm, _ = _ap_box(tmp_path, monkeypatch, local=local, profile_addr="192.168.4.1/24",
                         live_for=TODAY)
    cfgmod.save_firewall_config(svc._paths, ap_enabled=True, ap_interface="wlan0", ap_cidr=TODAY)
    svc._invalidate_config()
    gap = svc.ap_firewall_gap()
    assert (gap["interface"], gap["cidr"], gap["saved"], gap["live"]) == (
        "wlan0", "192.168.4.0/24", False, False)
    assert gap["commands"][0] == "lhpc firewall --ap on --ap-interface wlan0 --ap-cidr 192.168.4.0/24"
    assert nm.addr == "192.168.4.1/24" and _modified(nm) == []          # a read changes nothing
