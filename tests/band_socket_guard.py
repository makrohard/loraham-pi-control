"""The band-socket guard (Q16): a test with a hardware list must not fake a 0.13.x band socket.

With `config/radios.toml` in a runtime root, the daemon serves its radios' sockets only (`loraconf-<rid>.sock`,
`lora-<rid>.sock`); a band-form socket (`lora<band>.sock`, `lora<band>f.sock`, `loraconf<band>.sock`, in /tmp or
/run/loraham) is never that radio's. A FakeSystem that answers on one there lets a test pass on a reply the real
daemon never gives. Every FakeSystem handed to the product (through its `system` property) is watched:
- declared: a non-empty `unix_replies` entry for a band form (checked at teardown);
- served: each call of its unix client is recorded WHEN IT HAPPENS: a `request` on a band form that returned
  bytes, or a `send` on a band form that returned, of a fake whose `send` is its own (the inherited one accepts
  everything and answers nothing). A reply given once, or only to one payload, is caught as it is given.
A hit fails the test at teardown, unless DELIBERATE names that exact test with the sockets it may fake and why;
any other socket still fails it. A runtime root counts when it is `tmp_path`'s tree or the root of a `Paths`
built during the test.

Loaded for every test by `tests/conftest.py`. `tests/repo/test_w22_band_socket_guard.py` holds the canaries,
which fail when the guard is not loaded or misses a form or a way of serving. A fake that never goes through
`.system`, or a `System` built by hand, is outside it.
"""
from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from lhpc.core.paths import Paths
from lhpc.core.probes import backends

FORMS = tuple(f"{d}/{name}" for d in ("/tmp", "/run/loraham") for b in ("433", "868")
              for name in (f"lora{b}.sock", f"lora{b}f.sock", f"loraconf{b}.sock"))
_FORMS = frozenset(FORMS)

_DECOY = "a decoy: a READY on the band socket must NOT read as the radio's"
_NO_ONE_RADIO = "the band names no one enabled radio, so the band socket is the one asked"
# exact test id -> (the band sockets it may fake, why)
DELIBERATE = {
    **{f"tests/stacks/test_daemon_readiness.py::test_verify_band_up_reads_the_bands_radio[{t}]":
       (frozenset({"/tmp/loraconf433.sock"}), _DECOY) for t in ("0.0", "0.5")},
    "tests/core/test_g2_fixes.py::test_r1_control_two_enabled_radios_on_a_band_apply_the_stacks_value":
        (frozenset({"/tmp/loraconf868.sock"}), _NO_ONE_RADIO),
    **{f"tests/core/test_g2_fixes.py::test_r2_r3_r5_control_a_band_without_its_one_radio_applies_the_stacks_value"
       f"[{c}]": (frozenset({"/tmp/loraconf433.sock"}), _NO_ONE_RADIO) for c in ("invalid list", "radio disabled")},
}


@dataclasses.dataclass
class _Watch:
    active: bool = False
    fakes: list = dataclasses.field(default_factory=list)
    roots: list = dataclasses.field(default_factory=list)
    served: set = dataclasses.field(default_factory=set)
    expected: set = dataclasses.field(default_factory=set)


WATCH = _Watch()


class _Client:
    """The unix client a watched fake hands to the product: the fake's own calls, each band-form reply recorded."""

    def __init__(self, fake):
        self._fake = fake

    def request(self, path, payload, timeout, max_bytes):
        out = self._fake.request(path, payload, timeout, max_bytes)
        if out and path in _FORMS and WATCH.active:
            WATCH.served.add(path)
        return out

    def send(self, path, payload, timeout):
        self._fake.send(path, payload, timeout)
        if path in _FORMS and WATCH.active and type(self._fake).send is not backends.FakeSystem.send:
            WATCH.served.add(path)


_system = backends.FakeSystem.system
_paths_init = Paths.__init__


def _watched_system(self):
    if not WATCH.active:
        return _system.fget(self)
    if not any(f is self for f in WATCH.fakes):
        WATCH.fakes.append(self)
    return dataclasses.replace(_system.fget(self), unix=_Client(self))


def _watched_paths_init(self, *a, **kw):
    _paths_init(self, *a, **kw)
    if WATCH.active:
        WATCH.roots.append(Path(self.runtime_root))


backends.FakeSystem.system = property(_watched_system)
Paths.__init__ = _watched_paths_init


def declared(fake) -> set:
    """The band forms `fake` declares a reply for."""
    return {p for p, reply in (fake.unix_replies or {}).items() if p in _FORMS and reply}


def hits(tmp_root: Path | None) -> list[str]:
    """The band forms a watched fake declared or served, when a watched root holds config/radios.toml; else []."""
    roots = list(WATCH.roots) + ([tmp_root] if tmp_root is not None else [])
    listed = any((r / "config" / "radios.toml").is_file() for r in roots) or (
        tmp_root is not None and any(p.parent.name == "config" for p in tmp_root.rglob("radios.toml")))
    if not listed:
        return []
    return sorted(set(WATCH.served).union(*(declared(f) for f in WATCH.fakes)))


def expect_hit(path: str) -> None:
    """For a canary: this band form must be a hit of the current test (teardown fails if it is not)."""
    WATCH.expected.add(path)


@pytest.fixture(autouse=True)
def band_socket_guard(request):
    """Q16 for every test: fails it at teardown when a band-form socket is faked under a hardware list."""
    tmp_root = request.getfixturevalue("tmp_path") if "tmp_path" in request.fixturenames else None
    WATCH.active, WATCH.fakes, WATCH.roots, WATCH.served, WATCH.expected = True, [], [], set(), set()
    try:
        yield WATCH
        found = hits(tmp_root)
    finally:
        WATCH.active = False
    msg = verdict(found, set(WATCH.expected), request.node.nodeid)
    if msg:
        pytest.fail(msg, pytrace=False)


def verdict(found: list, expected: set, nodeid: str) -> str:
    """"" when the test passes the guard, else why not: with expected hits (a canary), exactly those; else no hit
    beyond the sockets DELIBERATE allows that exact test."""
    if expected:
        if set(found) != expected:
            return f"band-socket guard: expected hits {sorted(expected)}, found {found}"
        return ""
    allowed = DELIBERATE.get(nodeid, (frozenset(), ""))[0]
    if extra := sorted(set(found) - allowed):
        return (f"band-socket guard: a hardware list is set and a fake answers on {extra}; the L5 daemon serves "
                "its radios' sockets only. Fake the radio's socket, or name the test, the socket and the reason in "
                "tests/band_socket_guard.py DELIBERATE.")
    return ""
