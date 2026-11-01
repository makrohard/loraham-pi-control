"""T-R5 at L3 (PROOF r16, A §2.3): every process spec names its instance by an exact argv token
pair, or is in the named exemption table. Each declared token matches its component's own
resolved slot-0 argv, and not a neighbouring value. At L4 (below) slots 1–7 too; rids at L5."""
import pytest

from lhpc.core import commands
from lhpc.core.config import Config
from lhpc.core.manifest import load_manifest
from lhpc.core.probes.process import NO_INSTANCE_ARG, has_instance_token, matches

RT, SRC = "/rt", "/rt/src/x"
SPECS = [c for st in load_manifest() for c in st.components if c.process is not None]


def _inst0(comp):
    """Slot 0's instance as a start renders it (radio_start.client_instance): a daemon client's is its band's radio's
    (here r1); any other component's has no radio."""
    rid = any("{rid}" in str(p.default or "") for p in comp.run_params)
    return commands.Instance(0, "r1") if rid else commands.SLOT0


def _argv(comp):
    """The real argv of the process the spec matches, at slot 0 with default params."""
    argv = commands.expand_argv(comp.run_argv, comp, {}, Config().operator, RT, SRC, comp.band, inst=_inst0(comp))
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
    value = commands.instance_value(tpl, comp, {}, RT, SRC, comp.band, inst=_inst0(comp))
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


# ---- T-R5 at L4: slots 1–7 (A §2.3, Q-B2-5) ---------------------------------------------------------
# A spec named by a path or a port follows its instance's slot; a spec named by its daemon socket follows the
# radio id, which comes at L5; MeshCom's GPS relay keeps its relative socket path until its slot-k path changes
# together with MeshCom's run.sh and QEMU serial (L8-MC, the coordinator's decision).
SLOT_FOLLOWING = {"graywolf", "meshtastic-gps", "meshtastic", "meshcom-bridge", "meshcom-gps", "meshcom-qemu",
                  "meshcore-gps", "meshcore-node", "meshcore-webui", "meshcore-cli"}
RID_AT_L5 = {"loraham-chat", "loraham-kiss-tnc"}
L8_MC = {"meshcom-gps-relay"}
# One stack has at most one instance per radio (I1), so its instances are on distinct radios: slots 1–7 on
# seven radios whose ids prefix each other (r1/r12, r2/r21, r9/r99), and slot 0 (today's names, any radio).
# Slot 0 is on its own radio r4 (stage L5: a daemon client's slot 0 names its radio; other components ignore it).
INSTANCES = [commands.Instance(k, rid) for k, rid in zip(range(1, 8), ("r1", "r12", "r2", "r21", "r9", "r99", "r3"))] \
    + [commands.Instance(0, "r4")]


def _slot_argv(comp, inst):
    real = commands.expand_argv
    try:
        commands.expand_argv = lambda *a, **kw: real(*a, **{**kw, "inst": inst})
        return _argv(comp)
    finally:
        commands.expand_argv = real


def test_every_instance_named_spec_is_classified_for_l4():
    named = {c.id for c in SPECS if c.process.instance_arg}
    assert named == SLOT_FOLLOWING | RID_AT_L5 | L8_MC


@pytest.mark.parametrize("comp", [c for c in SPECS if c.id in SLOT_FOLLOWING | RID_AT_L5], ids=lambda c: c.id)
def test_each_slot_k_token_matches_its_own_instance_only(comp):
    # every instance's token is in its own argv and in no other instance's: other slots, and r1 vs r12; the
    # rid-named specs (chat, KISS TNC) by their radio's daemon socket since stage L5 (W3)
    opt, tpl = comp.process.instance_arg
    argvs = {(i.slot, i.rid): _slot_argv(comp, i) for i in INSTANCES}
    for i in INSTANCES:
        value = commands.instance_value(tpl, comp, {}, RT, SRC, comp.band, inst=i)
        assert matches(comp.process, argvs[(i.slot, i.rid)], (opt, value)), (i.slot, i.rid, value)
        others = [k for k, argv in argvs.items() if k != (i.slot, i.rid) and has_instance_token(argv, opt, value)]
        assert others == [], (i.slot, i.rid, value, others)


def test_the_kiss_pty_link_follows_the_instance():
    # G1-3 B2: the optional socat PTY link is a path of its instance: slot 0 keeps today's link, every slot k gets
    # its radio's level, so two instances never share one link; the process match finds each instance's socat
    # and not the TNC (whose config file name also starts with loraham_kiss).
    comp = next(c for c in SPECS if c.id == "loraham-kiss-serial")
    links = {(i.slot, i.rid): next(t for t in _slot_argv(comp, i) if t.startswith("PTY,link=")) for i in INSTANCES}
    assert links[(0, "r4")] == f"PTY,link={RT}/state/loraham_kiss,raw,echo=0,waitslave,mode=666"
    assert links[(3, "r2")] == f"PTY,link={RT}/state/r2/loraham_kiss,raw,echo=0,waitslave,mode=666"
    assert len(set(links.values())) == len(INSTANCES)
    tnc = next(c for c in SPECS if c.id == "loraham-kiss-tnc")
    for inst in INSTANCES:
        assert all(a in " ".join(_slot_argv(comp, inst)) for a in comp.process.all_args)
    assert "loraham_kiss" in " ".join(_argv(tnc))            # the TNC names a loraham_kiss file: the check below bites
    assert not all(a in " ".join(_argv(tnc)) for a in comp.process.all_args)
