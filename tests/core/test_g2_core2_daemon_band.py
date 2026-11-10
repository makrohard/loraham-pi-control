"""Gate 2 (VERDICT-G2-0.14.0-COMB 46e21ad5) finding 3: boot reconciliation reads each radio's daemon on the band its
own command line names. A daemon started with --id keeps its --radio band; one whose band its argv does not tell is
refused by name, never compared as an empty band. Read through the real process table (/proc, the parser)."""
import radios_fixture

from lhpc.core import boot_restore, radio_start
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService


def _svc(tmp_path, argvs):
    radios_fixture.write(tmp_path, (radios_fixture.R1, radios_fixture.R2_868))       # r1 on 433, r2 on 868
    fake = FakeSystem(cmdlines_data=argvs,
                      files={f"/proc/{p}/comm": "loraham_daemon\n" for p in argvs},
                      links={f"/proc/{p}/exe": "/usr/local/bin/loraham_daemon" for p in argvs})
    return ControllerService(system=fake.system, paths=Paths(runtime_root=tmp_path))


def test_each_radios_running_daemon_is_served_on_its_own_band(tmp_path):
    # daemon@r1 --radio 433 and daemon@r2 --radio 868 run: each item is served on its recorded band. red on 01d3d09e:
    # "daemon@r1 runs on  MHz, recorded on 433" (the parser dropped --radio beside a valid --id)
    svc = _svc(tmp_path, {101: ["loraham_daemon", "--radio", "433", "--id", "r1"],
                          102: ["loraham_daemon", "--radio", "868", "--id", "r2"]})
    procs = radio_start._daemon_procs(svc)
    assert boot_restore.daemon_served(procs, "r1", ["433"]) == ({"433"}, "")
    assert boot_restore.daemon_served(procs, "r2", ["868"]) == ({"868"}, "")


def test_a_radios_daemon_whose_argv_names_no_band_is_refused_by_name(tmp_path):
    # daemon@r1 runs with --id but no --radio: its band cannot be established, so the item fails by name (never an
    # empty band compared with the record)
    svc = _svc(tmp_path, {101: ["loraham_daemon", "--id", "r1"]})
    served, why = boot_restore.daemon_served(radio_start._daemon_procs(svc), "r1", ["433"])
    assert served == set() and why == "daemon@r1 runs on a band its command line does not tell (no one --radio 433|868)", why
