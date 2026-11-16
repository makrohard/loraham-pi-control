"""testlab/hardware/fake_peer.py, the dry run's stand-in for peer.py: the same CLI and exit codes, no device module, and one
FRAME line per frame the real action would send (the dispatch test counts them)."""
import importlib.util
import pathlib
import subprocess
import sys
import types

import pytest

FAKE = pathlib.Path(__file__).parents[2] / "hardware" / "fake_peer.py"
spec = importlib.util.spec_from_file_location("fake_peer_under_test", FAKE)
fake = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fake)
DEVICE_MODULES = {"serial", "meshtastic", "meshcore", "RNS", "LXMF", "bleak", "pubsub"}
# What the fake loads for one action, run as the dry run runs it: a fresh `python3 -I`, empty stdin.
PROBE = ("import runpy, sys\nsys.argv = sys.argv[1:]\ntry:\n    runpy.run_path(sys.argv[0], run_name='__main__')\n"
         "except SystemExit as e:\n    code = e.code\nprint('MODULES', ' '.join(sorted(sys.modules)))\n")


def run(*args, timeout=30):
    return subprocess.run([sys.executable, "-I", str(FAKE), *args], capture_output=True, text=True, stdin=subprocess.DEVNULL,
                          check=False, timeout=timeout)


def frames(out):
    return [line for line in out.splitlines() if line.startswith("FRAME |")]


@pytest.mark.parametrize("act", [["meshcom-send", "n1"], ["mt-power", "1"], ["mc-read", "n1", "5"], ["aprs-read", "n1", "5"]])
def test_it_loads_no_device_module(act):
    p = subprocess.run([sys.executable, "-I", "-c", PROBE, str(FAKE), *act], capture_output=True, text=True,
                       stdin=subprocess.DEVNULL, check=True, timeout=30)
    loaded = set(p.stdout.split("MODULES", 1)[1].split())
    assert not {m.split(".")[0] for m in loaded} & DEVICE_MODULES


@pytest.mark.parametrize("act,peer", [("meshcom-send", "tdeck"), ("mt-send", "g2"), ("mc-send", "tdpro")])
def test_a_send_prints_one_frame_of_its_peer_and_sent(act, peer):
    p = run(act, "n1")
    assert p.returncode == 0 and frames(p.stdout) == [f"FRAME | PC:{peer} | n1"]
    assert p.stdout.splitlines()[-1].split()[:2] == ["SENT", "n1"]


@pytest.mark.parametrize("act", [["meshcom-read", "n1", "5"], ["aprs-read", "n1", "5"], ["mt-read", "n1", "5"],
                                 ["mc-read", "n1", "5"]])
def test_a_read_prints_seen_and_no_frame(act):
    p = run(*act)
    assert p.returncode == 0 and not frames(p.stdout) and p.stdout.split()[:2] == ["SEEN", "n1"]


@pytest.mark.parametrize("args,env,want", [(["3"], {}, "POWER 3"), ([], {"DRY_POWER_g2": "5"}, "POWER 5"), ([], {}, "POWER 2")])
def test_a_power_action_prints_its_readback_and_no_frame(monkeypatch, capsys, args, env, want):
    monkeypatch.delenv("DRY_POWER_g2", raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    assert fake.main(["mt-power", *args]) == 0
    assert capsys.readouterr().out.strip() == want


@pytest.mark.parametrize("act", [[], ["nope-send", "n1"], ["aprs-send", "n1"], ["mt-send"], ["mt-read", "n1"],
                                 ["rns-exchange", "a", "b", "5", "d", "o"]])
def test_an_unknown_action_or_wrong_arguments_exit_2(act):
    assert run(*act).returncode == 2


class Clock:
    def __init__(self, go=None, at=None):
        self.t, self.go, self.at = 0.0, go, at

    def monotonic(self):
        return self.t

    def sleep(self, s):
        self.t += s
        if self.go is not None and self.t >= self.at:
            self.go.write_text("")


def exchange(monkeypatch, capsys, tmp_path, clock):
    monkeypatch.setattr(fake.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(fake.time, "sleep", clock.sleep)
    rc = fake.main(["rns-exchange", "in1", "LHPC", "60", str(tmp_path / "dest"), "out1", str(tmp_path / "go")])
    return rc, capsys.readouterr().out


def test_rns_exchange_sends_its_message_only_after_the_release_file(monkeypatch, capsys, tmp_path):
    rc, out = exchange(monkeypatch, capsys, tmp_path, Clock(go=tmp_path / "go", at=3.0))
    assert rc == 0 and frames(out) == ["FRAME | PC:rnode | control", "FRAME | PC:rnode | out1", "FRAME | PC:rnode | control"]
    assert "SEEN in1 " in out and not (tmp_path / "dest").exists()


def test_rns_exchange_without_the_release_file_sends_only_its_announce(monkeypatch, capsys, tmp_path):
    clock = Clock()
    rc, out = exchange(monkeypatch, capsys, tmp_path, clock)
    assert rc == fake.NOT_SEEN and frames(out) == ["FRAME | PC:rnode | control"] and "TX2-NOT-GATED" in out
    assert fake.RELEASE_WAIT_S <= clock.t < fake.RELEASE_WAIT_S + 1       # the bound is its own, not the caller's 60 s


def test_a_release_file_after_the_bound_sends_nothing_more(monkeypatch, capsys, tmp_path):
    rc, out = exchange(monkeypatch, capsys, tmp_path, Clock(go=tmp_path / "go", at=fake.RELEASE_WAIT_S + 2))
    assert rc == fake.NOT_SEEN and frames(out) == ["FRAME | PC:rnode | control"]


DRIVER = FAKE.with_name("peer.py")
INVALID = [["rns-send", "n1"], ["rns-read", "n1", "5"], ["meshcom-send"], ["mc-power", "garbage"], ["mt-read", "n1", "x"],
           ["aprs-send", "n1"], ["aprs-power"], ["mt-send", "n1", "extra"], ["rns-exchange", "a", "b", "5", "d", "o"], []]
VALID = [["meshcom-power"], ["mt-power", "1"], ["mc-power", "-2"], ["meshcom-send", "n1"], ["mt-send", "n1"],
         ["mc-send", "n1"], ["meshcom-read", "n1", "5"], ["aprs-read", "n1", "2.5"], ["mt-read", "n1", "5"],
         ["mc-read", "n1", "5"]]


class NoDevice(types.ModuleType):
    def __getattr__(self, name):
        raise AssertionError(f"a device module was used ({self.__name__}.{name})")


@pytest.mark.parametrize("act", INVALID, ids=lambda a: " ".join(a) or "(none)")
def test_a_call_the_driver_refuses_the_fake_refuses_alike(monkeypatch, act):
    # The same exit code before any device: peer.py and the fake share one action table.
    spec = importlib.util.spec_from_file_location("peer_parity", DRIVER)
    peer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(peer)
    for name in ("serial", "meshtastic", "meshcore", "RNS", "LXMF"):
        monkeypatch.setitem(sys.modules, name, NoDevice(name))
    assert (peer.main(act), run(*act).returncode) == (2, 2)


@pytest.mark.parametrize("act", VALID, ids=" ".join)
def test_a_call_the_driver_takes_the_fake_takes(act):
    assert run(*act).returncode == 0
