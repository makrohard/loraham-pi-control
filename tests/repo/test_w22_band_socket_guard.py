"""The band-socket guard's canaries (Q16, package item 22): the guard is loaded, and it reports every 0.13.x band form,
raw, framed and CONF, both bands, both socket directories, whether a fake declares the reply or serves it as the
product calls it: a request, a send, a reply given to one payload only, a reply given once, a fake that raises on
another payload. A canary fails when the guard is not loaded (a silent no-run) or misses a form or a way of
serving; the controls fail when it reports what it must not; an exemption covers its exact test and sockets only.
"""
from __future__ import annotations

import dataclasses

import band_socket_guard as guard
import pytest

from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem

FORMS = [f"{d}/{name}" for d in ("/tmp", "/run/loraham") for b in ("433", "868")
         for name in (f"lora{b}.sock", f"lora{b}f.sock", f"loraconf{b}.sock")]


@dataclasses.dataclass
class _Serving(FakeSystem):
    """Answers a request on the paths in `serve` (only to payloads starting with `only`, when set; at most `left`
    times, when set; raising AssertionError on any other payload when `strict`), and refuses every other path."""
    serve: tuple = ()
    only: bytes = b""
    left: int = -1
    strict: bool = False

    def request(self, path, payload, timeout, max_bytes):
        if path not in self.serve:
            raise ConnectionRefusedError(path)
        if self.only and not payload.startswith(self.only):
            if self.strict:
                raise AssertionError(f"unexpected payload {payload!r}")
            raise ConnectionRefusedError(path)
        if self.left == 0:
            raise ConnectionRefusedError(path)
        self.left -= 1
        return b"OK\n"


@dataclasses.dataclass
class _Accepting(FakeSystem):
    """Accepts a send on the paths in `serve` and refuses every other one."""
    serve: tuple = ()

    def send(self, path, payload, timeout):
        if path not in self.serve:
            raise ConnectionRefusedError(path)
        self.sent.append((path, payload))


def _list(root):
    from lhpc.core import radios
    (root / "config").mkdir(parents=True, exist_ok=True)
    (root / "config" / "radios.toml").write_text(radios.render((radios.Radio("r1", "low", "loraham", "433"),)))


def test_the_guard_is_loaded_for_this_test():
    assert guard.WATCH.active is True


def test_the_guard_covers_every_band_form():
    assert sorted(guard.FORMS) == sorted(FORMS)


@pytest.mark.parametrize("kind", ["declared", "request", "send"])
@pytest.mark.parametrize("sock", FORMS)
def test_canary_a_faked_band_socket_under_a_list_is_a_hit(tmp_path, sock, kind):
    _list(tmp_path)
    if kind == "declared":
        FakeSystem(unix_replies={sock: b"STATUS RADIO=READY\n"}).system
    elif kind == "request":
        assert _Serving(serve=(sock,)).system.unix.request(sock, b"GET STATUS\n", 1.0, 4096) == b"OK\n"
    else:
        _Accepting(serve=(sock,)).system.unix.send(sock, b"frame", 1.0)
    assert guard.hits(tmp_path) == [sock]
    guard.expect_hit(sock)                                 # and the teardown agrees


SOCK = "/tmp/loraconf433.sock"


def test_canary_a_reply_given_to_one_payload_only_is_a_hit(tmp_path):
    """A fake that acknowledges SET but refuses GET STATUS (B1 F1: the teardown probe asked GET STATUS only)."""
    _list(tmp_path)
    assert _Serving(serve=(SOCK,), only=b"SET ").system.unix.request(SOCK, b"SET CADWAIT=200\n", 1.0, 64) == b"OK\n"
    assert guard.hits(tmp_path) == [SOCK]
    guard.expect_hit(SOCK)


def test_canary_a_reply_given_once_is_a_hit(tmp_path):
    """A one-use reply, consumed by the product before the teardown could ask."""
    _list(tmp_path)
    assert _Serving(serve=(SOCK,), left=1).system.unix.request(SOCK, b"GET STATUS\n", 1.0, 64) == b"OK\n"
    assert guard.hits(tmp_path) == [SOCK]
    guard.expect_hit(SOCK)


def test_canary_a_fake_that_raises_on_another_payload_is_still_a_hit(tmp_path):
    """It raises AssertionError on GET STATUS (the old teardown probe took that for "no answer") and serves SET."""
    _list(tmp_path)
    fake = _Serving(serve=(SOCK,), only=b"SET ", strict=True)
    assert fake.system.unix.request(SOCK, b"SET TXMODE=DIRECT\n", 1.0, 64) == b"OK\n"
    assert guard.hits(tmp_path) == [SOCK]
    guard.expect_hit(SOCK)


def test_canary_a_list_in_a_runtime_root_outside_tmp_path_counts(tmp_path_factory):
    root = tmp_path_factory.mktemp("elsewhere")
    _list(root)
    Paths(runtime_root=root)
    FakeSystem(unix_replies={"/tmp/loraconf433.sock": b"STATUS RADIO=READY\n"}).system
    assert guard.hits(None) == ["/tmp/loraconf433.sock"]
    guard.expect_hit("/tmp/loraconf433.sock")


@pytest.mark.parametrize("sock", ["/tmp/loraconf433.sock", "/tmp/lora868f.sock"])
def test_control_without_a_list_a_band_socket_is_no_hit(tmp_path, sock):
    FakeSystem(unix_replies={sock: b"STATUS RADIO=READY\n"}).system
    assert _Serving(serve=(sock,)).system.unix.request(sock, b"GET STATUS\n", 1.0, 64) == b"OK\n"
    assert guard.hits(tmp_path) == []


def test_control_the_radios_own_sockets_under_a_list_are_no_hit(tmp_path):
    _list(tmp_path)
    FakeSystem(unix_replies={"/tmp/loraconf-r1.sock": b"STATUS RADIO=READY\n"}).system
    _Serving(serve=("/tmp/loraconf-r1.sock",)).system.unix.request("/tmp/loraconf-r1.sock", b"GET STATUS\n", 1.0, 64)
    _Accepting(serve=("/tmp/lora-r1.sock",)).system.unix.send("/tmp/lora-r1.sock", b"frame", 1.0)
    base = FakeSystem().system.unix                        # replies b"" everywhere, accepts every send: no answer
    assert base.request(SOCK, b"GET STATUS\n", 1.0, 64) == b"" and base.send(SOCK, b"frame", 1.0) is None
    assert guard.hits(tmp_path) == []


def test_control_an_empty_declared_reply_is_no_answer(tmp_path):
    """b"" is what the product reads as "nothing there": declaring it for a band socket fakes no daemon."""
    _list(tmp_path)
    FakeSystem(unix_replies={SOCK: b""}).system
    assert guard.hits(tmp_path) == []


def test_control_a_fake_never_handed_to_the_product_is_not_judged(tmp_path):
    _list(tmp_path)
    FakeSystem(unix_replies={"/tmp/loraconf433.sock": b"STATUS RADIO=READY\n"})
    assert guard.hits(tmp_path) == []


HIT = ["/tmp/loraconf433.sock"]


DECOY = "tests/stacks/test_daemon_readiness.py::test_verify_band_up_reads_the_bands_radio[0.0]"


@pytest.mark.parametrize("found,expected,nodeid,fails", [
    (HIT, set(), "tests/web/test_x.py::test_y", True),                                  # an unnamed hit
    (HIT, set(), DECOY, False),                                                         # the named test, its socket
    (["/tmp/lora433.sock"], set(), DECOY, True),                                        # the named test, another socket
    (HIT, set(), DECOY[:-5], True),                                                     # a prefix of a named test
    (HIT, set(), DECOY.replace("[0.0]", "[9.9]"), True),                                # another case of it
    ([], set(), "tests/web/test_x.py::test_y", False),                                  # no hit
    (HIT, set(HIT), "tests/web/test_x.py::test_y", False),                              # a canary's expected hit
    ([], set(HIT), "tests/web/test_x.py::test_y", True),                                # a canary that saw nothing
    (HIT + ["/tmp/lora433.sock"], set(HIT), "tests/web/test_x.py::test_y", True),       # one more than expected
])
def test_the_teardown_verdict(found, expected, nodeid, fails):
    assert bool(guard.verdict(found, expected, nodeid)) is fails
