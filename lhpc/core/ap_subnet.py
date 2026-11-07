"""The access point's IPv4 subnet (`network.ap_cidr`): validation and the derived AP address.

Pure: no I/O, no state. The box's other networks are an argument of `overlap`, never read here.
"""

from __future__ import annotations

import ipaddress

from . import validators

DEFAULT = "10.42.0.0/24"            # NetworkManager's own address for an `ipv4.method shared` profile

_PREFIX_MIN, _PREFIX_MAX = 16, 28
_PRIVATE = tuple(ipaddress.IPv4Network(n) for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"))


class ApSubnetError(ValueError):
    """The value breaks a rule of the AP subnet; the message names the rule."""


def validate(value: object) -> ipaddress.IPv4Network:
    """Exactly a `str` holding a canonical IPv4 network/prefix, prefix 16..28, inside RFC 1918.
    Nothing is stripped or normalised: surrounding whitespace and host bits are refused."""
    if type(value) is not str:
        raise ApSubnetError(f"network.ap_cidr must be a string, got {type(value).__name__}")
    if not value or not value.isascii() or not value.isprintable() or value != value.strip():
        raise ApSubnetError("network.ap_cidr must be IPv4 network/prefix text without spaces "
                            "or other characters")
    try:
        normalised = validators.cidr(value, field="network.ap_cidr")
    except validators.ValidationError as exc:
        raise ApSubnetError(str(exc)) from None
    net = ipaddress.IPv4Network(normalised)
    if normalised != value:
        if ipaddress.IPv4Interface(value).ip != net.network_address:
            raise ApSubnetError(f"network.ap_cidr {value} has host bits set: "
                                f"the network is {normalised}")
        raise ApSubnetError(f"network.ap_cidr {value} is not written canonically: {normalised}")
    _check(net)
    return net


def _check(net: ipaddress.IPv4Network) -> None:
    if not _PREFIX_MIN <= net.prefixlen <= _PREFIX_MAX:
        raise ApSubnetError(f"network.ap_cidr {net}: the prefix must be /{_PREFIX_MIN} to "
                            f"/{_PREFIX_MAX}")
    if not any(net.subnet_of(p) for p in _PRIVATE):
        raise ApSubnetError(f"network.ap_cidr {net} is not a private (RFC 1918) network: "
                            "10.0.0.0/8, 172.16.0.0/12 or 192.168.0.0/16")


def _validated(net: object) -> ipaddress.IPv4Network:
    if type(net) is not ipaddress.IPv4Network:
        raise ApSubnetError(f"expected a validated IPv4 network, got {type(net).__name__}")
    _check(net)
    return net


def first_host(net: ipaddress.IPv4Network) -> ipaddress.IPv4Address:
    """The box's own address on the AP: the network address + 1."""
    return _validated(net).network_address + 1


def ap_address(net: ipaddress.IPv4Network) -> str:
    """`<first host>/<prefix>`, the AP profile's `ipv4.addresses` value."""
    return f"{first_host(net)}/{net.prefixlen}"


def overlap(net: ipaddress.IPv4Network, others: list[ipaddress.IPv4Network] | None) -> str:
    """`"overlap"`, `"clear"` or `"unknown"`. `others` is every IPv4 network the box has. `None`
    (they could not be read), an empty list (a read that returned nothing) or anything but a
    list of `IPv4Network` is `"unknown"`, never "no overlap"."""
    _validated(net)
    if (type(others) is not list or not others
            or any(type(o) is not ipaddress.IPv4Network for o in others)):
        return "unknown"
    return "overlap" if any(net.overlaps(o) for o in others) else "clear"
