"""The band-socket guard (Q16): a test with a hardware list must not fake a 0.13.x band socket.

With `config/radios.toml` in a runtime root, the daemon serves its radios' sockets only (`loraconf-<rid>.sock`,
`lora-<rid>.sock`); a band-form socket (`lora<band>.sock`, `lora<band>f.sock`, `loraconf<band>.sock`, in /tmp or
/run/loraham) is never that radio's. A FakeSystem that answers on one there lets a test pass on a reply the real
daemon never gives. After each test the guard asks every FakeSystem the test handed to the product (each one
read through its `system` property) whether it answers on each band form:
- declared: a non-empty `unix_replies` entry;
- answered: an overridden `request` that returns bytes, or an overridden `send` that does not raise.
A hit fails the test at teardown, unless the test is in DELIBERATE, which names its reason. A runtime root counts
when it is `tmp_path`'s tree or the root of a `Paths` built during the test.

Loaded for every test by `tests/conftest.py`. `tests/repo/test_w22_band_socket_guard.py` holds the canaries,
which fail when the guard is not loaded or misses a form. A fake that never goes through `.system`, or a `System`
built by hand, is outside it.
"""
from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from lhpc.core.paths import Paths
from lhpc.core.probes import backends

FORMS = tuple(f"{d}/{name}" for d in ("/tmp", "/run/loraham") for b in ("433", "868")
              for name in (f"lora{b}.sock", f"lora{b}f.sock", f"loraconf{b}.sock"))

# nodeid prefix -> why its band-socket fake is deliberate
DELIBERATE = {
    "tests/stacks/test_daemon_readiness.py::test_verify_band_up_reads_the_bands_radio":
        "a decoy: a READY on the band socket must NOT read as the radio's",
    "tests/core/test_g2_fixes.py::test_r1_control_":
        "the band has two enabled radios, so it names no one radio and the band socket is the one asked",
    "tests/core/test_g2_fixes.py::test_r2_r3_r5_control_":
        "the list names no enabled radio for the band, so the band socket is the one asked",
}


@dataclasses.dataclass
class _Watch:
    active: bool = False
    fakes: list = dataclasses.field(default_factory=list)
    roots: list = dataclasses.field(default_factory=list)
    expected: set = dataclasses.field(default_factory=set)


WATCH = _Watch()

_system = backends.FakeSystem.system
_paths_init = Paths.__init__


def _watched_system(self):
    if WATCH.active and not any(f is self for f in WATCH.fakes):
        WATCH.fakes.append(self)
    return _system.fget(self)


def _watched_paths_init(self, *a, **kw):
    _paths_init(self, *a, **kw)
    if WATCH.active:
        WATCH.roots.append(Path(self.runtime_root))


backends.FakeSystem.system = property(_watched_system)
Paths.__init__ = _watched_paths_init


def answers(fake, path: str) -> bool:
    """Does `fake` answer on `path`: a declared reply, or an overridden request/send that serves it."""
    if (fake.unix_replies or {}).get(path):
        return True
    cls = type(fake)
    if cls.request is not backends.FakeSystem.request:
        try:
            if fake.request(path, b"GET STATUS\n", 0.1, 4096):
                return True
        except Exception:
            pass
    if cls.send is not backends.FakeSystem.send:
        try:
            fake.send(path, b"\n", 0.1)
            return True
        except Exception:
            pass
    return False


def hits(tmp_root: Path | None) -> list[str]:
    """The band forms some watched fake answers on, when a watched root holds config/radios.toml; else []."""
    roots = list(WATCH.roots) + ([tmp_root] if tmp_root is not None else [])
    listed = any((r / "config" / "radios.toml").is_file() for r in roots) or (
        tmp_root is not None and any(p.parent.name == "config" for p in tmp_root.rglob("radios.toml")))
    if not listed:
        return []
    return sorted({p for f in WATCH.fakes for p in FORMS if answers(f, p)})


def expect_hit(path: str) -> None:
    """For a canary: this band form must be a hit of the current test (teardown fails if it is not)."""
    WATCH.expected.add(path)


@pytest.fixture(autouse=True)
def band_socket_guard(request):
    """Q16 for every test: fails it at teardown when a band-form socket is faked under a hardware list."""
    tmp_root = request.getfixturevalue("tmp_path") if "tmp_path" in request.fixturenames else None
    WATCH.active, WATCH.fakes, WATCH.roots, WATCH.expected = True, [], [], set()
    try:
        yield WATCH
        found = hits(tmp_root)
    finally:
        WATCH.active = False
    msg = verdict(found, set(WATCH.expected), request.node.nodeid)
    if msg:
        pytest.fail(msg, pytrace=False)


def verdict(found: list, expected: set, nodeid: str) -> str:
    """"" when the test passes the guard, else why not: with expected hits (a canary), exactly those; else no hit,
    unless DELIBERATE names the test."""
    if expected:
        if set(found) != expected:
            return f"band-socket guard: expected hits {sorted(expected)}, found {found}"
        return ""
    if found and not any(nodeid.startswith(k) for k in DELIBERATE):
        return (f"band-socket guard: a hardware list is set and a fake answers on {found}; the L5 daemon serves "
                "its radios' sockets only. Fake the radio's socket, or name the test and its reason in "
                "tests/band_socket_guard.py DELIBERATE.")
    return ""
