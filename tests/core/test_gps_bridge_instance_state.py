"""K4b: a GPS feed runs as its stack's instance, its argv token `--state <runtime>/state/<rid>/gps/<consumer>` (A §2.3);
the bridge takes that token as its consumer's own state dir, and refuses any other consumer's or radio-less path. With no
GPS configured, an accepted token goes on to "GPS source is off"; a refused one stops at the state check."""
import pytest

from lhpc.core import gps_bridge, manifest
from lhpc.core.commands import Instance
from lhpc.core.paths import Paths

OFF = "GPS source is off"
REFUSED = "is not meshcore's state dir"


def _token(root, rid):
    comp = next(c for s in manifest.load_manifest() for c in s.components if c.id == "meshcore-gps")
    opt, tpl = comp.process.instance_arg
    assert opt == "--state"
    return Instance(rid).subst(tpl).replace("{runtime}", str(root))


@pytest.mark.parametrize("state,said", [
    (lambda root: _token(root, "r2"), OFF),                          # the instance's rendered token
    (lambda root: _token(root, "r64"), OFF),
    (lambda root: f"{root}/state/gps/meshcore", OFF),                 # the consumer's own dir itself
    (lambda root: f"{root}/state/r2/gps/meshtastic", REFUSED),        # another consumer's
    (lambda root: f"{root}/state/x2/gps/meshcore", REFUSED),          # not a radio id
    (lambda root: f"{root}/state/r0/gps/meshcore", REFUSED),
    (lambda root: f"{root}/other/r2/gps/meshcore", REFUSED),          # outside state/
])
def test_the_bridge_takes_its_instances_state_token_and_refuses_any_other(tmp_path, capsys, state, said):
    assert gps_bridge.run("meshcore", Paths(runtime_root=tmp_path), state=state(tmp_path)) == gps_bridge.EXIT_CONFIG
    err = capsys.readouterr().err
    other = REFUSED if said == OFF else OFF
    assert said in err and other not in err, err
