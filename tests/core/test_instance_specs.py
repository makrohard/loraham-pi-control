"""T-R5 at L3 (PROOF r16, A §2.3): every process spec names its instance by an exact argv token
pair, or is in the named exemption table. Each declared token matches its component's own
resolved slot-0 argv, and not a neighbouring value. Slots 1–7 come at L4, rids at L5."""
import pytest

from lhpc.core import commands
from lhpc.core.config import Config
from lhpc.core.manifest import load_manifest
from lhpc.core.probes.process import NO_INSTANCE_ARG, has_instance_token, matches

RT, SRC = "/rt", "/rt/src/x"
SPECS = [c for st in load_manifest() for c in st.components if c.process is not None]


def _argv(comp):
    """The real argv of the process the spec matches, at slot 0 with default params."""
    argv = commands.expand_argv(comp.run_argv, comp, {}, Config().operator, RT, SRC, comp.band)
    if comp.id == "meshcore-webui":
        # meshcore-webui-run.sh:68 execs `"$venv/bin/python" -m uvicorn app.main:app --host
        # "$bind" --port "$port"`, with bind/port the wrapper's last two arguments.
        return ["/v/bin/python", "-m", "uvicorn", "app.main:app", "--host", argv[-2], "--port", argv[-1]]
    if comp.id == "meshcore-cli":
        # meshcli-run.sh :12 shifts the lock path off and :25 runs `"$@"`: meshcli's own argv is
        # the wrapper's after its script and lock path.
        return argv[2:]
    if comp.id == "meshcom-qemu":
        # meshcom-qemu-raspi scripts/run.sh :127 at the pin 647c2b59: `-drive
        # "file=$NODE_IMAGE,if=mtd,format=raw"`, NODE_IMAGE = the `--node-image` LHPC passes (:44).
        image = argv[argv.index("--node-image") + 1]
        return ["qemu-system-xtensa", "-drive", "file=/x/efuse.bin,if=none",
                "-drive", f"file={image},if=mtd,format=raw"]
    return argv


def test_every_spec_carries_an_instance_arg_or_is_named_exempt():
    ids = {c.id for c in SPECS}
    assert set(NO_INSTANCE_ARG) <= ids, set(NO_INSTANCE_ARG) - ids
    missing = [c.id for c in SPECS if not c.process.instance_arg and c.id not in NO_INSTANCE_ARG]
    assert missing == []
    assert not [c.id for c in SPECS if c.process.instance_arg and c.id in NO_INSTANCE_ARG]


@pytest.mark.parametrize("comp", [c for c in SPECS if c.process.instance_arg], ids=lambda c: c.id)
def test_the_instance_token_matches_its_own_slot0_argv_only(comp):
    opt, tpl = comp.process.instance_arg
    value = commands.instance_value(tpl, comp, {}, RT, SRC, comp.band)
    argv = _argv(comp)
    assert has_instance_token(argv, opt, value), (opt, value, argv)
    assert matches(comp.process, argv, (opt, value))
    assert not has_instance_token(argv, opt, value + "0")        # a neighbouring value


def test_the_gps_bridge_takes_only_its_own_state_dir(tmp_path, capsys):
    from lhpc.core import gps_bridge
    from lhpc.core.paths import Paths
    paths = Paths(runtime_root=tmp_path)
    assert gps_bridge.run("meshtastic", paths, state=str(tmp_path / "state/gps/meshcom")) \
        == gps_bridge.EXIT_CONFIG
    assert "is not meshtastic's state dir" in capsys.readouterr().err
    gps_bridge.run("meshtastic", paths, state=str(tmp_path / "state/gps/meshtastic"))
    assert "state dir" not in capsys.readouterr().err        # its own dir passes the check


def test_voice_runs_one_instance_per_box(tmp_path, monkeypatch):
    # W98: until L5 voice and voice_cli run on ONE band at a time; today's refusal
    # (service_lifecycle_ops.py:956-969) pins it. Exercised on one band: 433 running, 868 asked.
    from lhpc.core.paths import Paths
    from lhpc.core.probes.backends import FakeSystem
    from lhpc.core.services import ControllerService
    monkeypatch.setattr(ControllerService, "running_band", lambda self, sid, default="": "433")
    monkeypatch.setattr(ControllerService, "_band_owner_is_up", lambda self, sid: True)
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    assert svc.set_operator_identity(callsign="XX0XXA").ok
    res = svc.start("voice", apply=True, band="868")
    assert not res.ok and "'voice' is already running on 433 MHz" in res.summary, res.summary
