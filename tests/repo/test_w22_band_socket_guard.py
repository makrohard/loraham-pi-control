"""The band-socket guard's canaries (Q16, package item 22): the guard is loaded, and it reports every 0.13.x band form,
raw, framed and CONF, both bands, both socket directories, whether a fake declares the reply or serves it from an
overridden request or send. A canary fails when the guard is not loaded (a silent no-run) or misses a form; the
controls fail when it reports what it must not."""
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
    """Answers a request on the paths in `serve` and refuses every other one."""
    serve: tuple = ()

    def request(self, path, payload, timeout, max_bytes):
        if path not in self.serve:
            raise ConnectionRefusedError(path)
        return b"STATUS RADIO=READY\n"


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
    fake = {"declared": lambda: FakeSystem(unix_replies={sock: b"STATUS RADIO=READY\n"}),
            "request": lambda: _Serving(serve=(sock,)),
            "send": lambda: _Accepting(serve=(sock,))}[kind]()
    fake.system                                            # handed to the product
    assert guard.hits(tmp_path) == [sock]
    guard.expect_hit(sock)                                 # and the teardown agrees


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
    _Serving(serve=(sock,)).system
    assert guard.hits(tmp_path) == []


def test_control_the_radios_own_sockets_under_a_list_are_no_hit(tmp_path):
    _list(tmp_path)
    FakeSystem(unix_replies={"/tmp/loraconf-r1.sock": b"STATUS RADIO=READY\n"}).system
    _Serving(serve=("/tmp/loraconf-r1.sock", "/tmp/lora-r1.sock")).system
    _Accepting(serve=("/tmp/lora-r1.sock",)).system
    FakeSystem().system                                    # replies b"" everywhere, accepts every send: no answer
    assert guard.hits(tmp_path) == []


def test_control_a_fake_never_handed_to_the_product_is_not_judged(tmp_path):
    _list(tmp_path)
    FakeSystem(unix_replies={"/tmp/loraconf433.sock": b"STATUS RADIO=READY\n"})
    assert guard.hits(tmp_path) == []


HIT = ["/tmp/loraconf433.sock"]


@pytest.mark.parametrize("found,expected,nodeid,fails", [
    (HIT, set(), "tests/web/test_x.py::test_y", True),                                  # an unnamed hit
    (HIT, set(), "tests/stacks/test_daemon_readiness.py::test_verify_band_up_reads_the_bands_radio[0.0]", False),
    ([], set(), "tests/web/test_x.py::test_y", False),                                  # no hit
    (HIT, set(HIT), "tests/web/test_x.py::test_y", False),                              # a canary's expected hit
    ([], set(HIT), "tests/web/test_x.py::test_y", True),                                # a canary that saw nothing
    (HIT + ["/tmp/lora433.sock"], set(HIT), "tests/web/test_x.py::test_y", True),       # one more than expected
])
def test_the_teardown_verdict(found, expected, nodeid, fails):
    assert bool(guard.verdict(found, expected, nodeid)) is fails
