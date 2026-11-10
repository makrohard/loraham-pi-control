"""Gate 2 held candidate (b), DEC-W409aq/ax: on a box with NO hardware list and the old [radio] configuration, a start
of `<stack>@<rid>` is refused with the old-configuration reason and its next step (`lhpc hardware`), the W1-8 reason
rule radio_daemon._unstartable states, not the instance parser's "rN is not a radio of the hardware list"."""
import pytest

from lhpc.core import radio_daemon
from lhpc.core.paths import Paths


@pytest.mark.no_default_hardware
def test_a_start_on_an_old_config_box_says_to_set_up_the_hardware(tmp_path, capsys, monkeypatch):
    # red on 01d3d09e: "ERR daemon@r1: r1 is not a radio of the hardware list", no next step
    from lhpc.adapters.cli import main as cli
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "local.toml").write_text('[radio]\nhardware = "loraham"\n')
    assert radio_daemon.old_config_state(Paths(runtime_root=tmp_path)) is radio_daemon.Tri.YES
    monkeypatch.setenv("LHPC_RUNTIME_ROOT", str(tmp_path))
    rc = cli.main(["stack", "start", "daemon@r1", "--yes"])
    err = capsys.readouterr().err
    assert rc == 1 and f"daemon@r1: {radio_daemon.OLD_CONFIG_TEXT} (next: lhpc hardware)" in err, err


@pytest.mark.no_default_hardware
def test_a_start_on_a_box_without_a_list_says_there_is_none(tmp_path, capsys, monkeypatch):
    # no list and no old config: the reason is "no hardware list", next lhpc hardware
    from lhpc.adapters.cli import main as cli
    (tmp_path / "config").mkdir()
    monkeypatch.setenv("LHPC_RUNTIME_ROOT", str(tmp_path))
    rc = cli.main(["stack", "start", "kiss@r1", "--yes"])
    err = capsys.readouterr().err
    assert rc == 1 and "kiss@r1: no hardware list (next: lhpc hardware)" in err, err
