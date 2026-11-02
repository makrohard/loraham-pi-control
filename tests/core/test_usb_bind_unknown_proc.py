"""G2-0.14.0-1 item 2: the guards that judge "is this radio's daemon running" see every process. A process whose
identity cannot be read (no command line, exe or comm) was dropped by radio_start._daemon_procs before the refusal
rules ran, so a USB rebind saved while such a process might be the radio's daemon. It now reaches the rules' unknown
class: the bind is refused naming its pid and the hardware list stays byte-identical. Through radio_start.bind_usb,
the function the /hardware/bind route calls."""
import radios_fixture
import repo_paths

from lhpc.core import radio_start, radios, usb_access
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService
from lhpc.core.usb_access import Probe

STICKS = repo_paths.REPO / "tests" / "data" / "usb_sysfs" / "meshstick"     # the Meshstick on 1-1.3, no serial
ROWS = (radios.Radio("r1", "low", "loraham", "433"),
        radios.Radio("r2", "stick", "meshstick-1262", "868", usb_port="1-1.9"))


def _svc(tmp_path, monkeypatch, **fake):
    radios_fixture.write(tmp_path, ROWS)
    real_scan = usb_access.scan
    monkeypatch.setattr(usb_access, "scan", lambda *a, **k: real_scan(STICKS))
    monkeypatch.setattr(usb_access, "node_access", lambda *a, **k: Probe.PRESENT)
    return ControllerService(system=FakeSystem(**fake).system, paths=Paths(runtime_root=tmp_path))


def test_a_process_whose_identity_cannot_be_read_refuses_the_rebind(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch, dirs={"/proc": ["4242"]}, paths={"/proc/4242"})   # listed; nothing readable
    before = (tmp_path / "config" / "radios.toml").read_bytes()
    ok, msg, _copybox = radio_start.bind_usb(svc, "r2", "1-1.3")
    assert not ok and "4242" in msg, msg
    assert (tmp_path / "config" / "radios.toml").read_bytes() == before


def test_a_daemon_named_only_by_its_comm_refuses_the_rebind(tmp_path, monkeypatch):
    # argv[0] is not the binary's name (exec under another name); comm names it: it is the daemon, run without --id.
    svc = _svc(tmp_path, monkeypatch, cmdlines_data={4243: ["/opt/x/rfd", "--radio", "868"]},
               files={"/proc/4243/comm": "loraham_daemon\n"}, paths={"/proc/4243"})
    before = (tmp_path / "config" / "radios.toml").read_bytes()
    ok, msg, _copybox = radio_start.bind_usb(svc, "r2", "1-1.3")
    assert not ok and "4243" in msg, msg
    assert (tmp_path / "config" / "radios.toml").read_bytes() == before


def test_a_process_that_is_not_the_daemon_does_not_block_the_rebind(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch, cmdlines_data={4244: ["/usr/bin/sleep", "9"]},
               files={"/proc/4244/comm": "sleep\n"}, paths={"/proc/4244"}, dirs={"/proc": ["4244", "4245"]})
    ok, msg, _copybox = radio_start.bind_usb(svc, "r2", "1-1.3")       # 4245: gone by the reads (no /proc/4245)
    assert ok, msg
    assert next(r for r in radios.load(Paths(runtime_root=tmp_path)).radios if r.id == "r2").usb_port == "1-1.3"


def test_a_kernel_thread_and_another_users_process_do_not_block_the_rebind(tmp_path, monkeypatch):
    # G2-D12: a pid judged by any readable source that is not the daemon reads NO. A kernel thread: no command line
    # (absent from cmdlines), exe unreadable, comm "kworker/0:1" readable. Another user's process: exe unreadable,
    # argv and comm readable. Neither blocks, or a real box would refuse every bind.
    svc = _svc(tmp_path, monkeypatch, dirs={"/proc": ["2", "4246"]}, paths={"/proc/2", "/proc/4246"},
               cmdlines_data={4246: ["/usr/sbin/sshd", "-D"]},
               files={"/proc/2/comm": "kworker/0:1\n", "/proc/4246/comm": "sshd\n"})
    ok, msg, _copybox = radio_start.bind_usb(svc, "r2", "1-1.3")
    assert ok, msg
