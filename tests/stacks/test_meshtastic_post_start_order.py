"""The Meshtastic start's post-steps: the node identity goes LAST.

The node identity push (`--set-owner`) makes meshtasticd send its node-info at once, and every
config save retunes the radio at once, cutting a packet that is on air. LHPC's own later
`--set position.gps_mode` did exactly that to a fresh node's node-info (DEBUG box log, firmware
v2.7.26). So no LHPC save may follow the identity push. The fixed-position step comes first: its
save does not re-arm meshtasticd's 7 s reboot, so after every save that does, the next step
follows at once.
"""
from pathlib import Path

from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService

# Every argv flag the manifest's meshtastic post-steps use that SAVES config on the node, the
# identity step's own flags included (so a second identity push after the first is refused too).
# It is not the CLI's full list; a new step fails the exact-order test above anyway.
SAVING_FLAGS = ("--set", "--set-owner", "--set-owner-short", "--setlat", "--setlon", "--setalt",
                "--remove-position", "{gps_fixed_args}")


def _steps(tmp_path):
    fake = FakeSystem(paths=set(), commands={})
    svc = ControllerService(system=fake.system, paths=Paths(runtime_root=Path(tmp_path)))
    comp = next(c for s in svc.stacks() for c in s.components if c.id == "meshtastic")
    return comp.post_steps


def test_the_post_steps_run_in_the_decided_order(tmp_path):
    # The complete shape, the leading delay included: every step, in order.
    shape = [("delay", s.get("seconds")) if s.get("kind") == "delay" else (s.get("kind"), s.get("label"))
             for s in _steps(tmp_path)]
    assert shape == [("delay", 12), ("exec", "fixed position"), ("exec", "region"),
                     ("exec", "gps mode"), ("exec", "node identity")]


def test_no_saving_step_follows_the_node_identity(tmp_path):
    steps = [s for s in _steps(tmp_path) if s.get("kind") == "exec"]
    idx = next(i for i, s in enumerate(steps) if s.get("label") == "node identity")
    later = [s["label"] for s in steps[idx + 1:]
             if any(str(a) in SAVING_FLAGS for a in s.get("argv", ()))]
    assert later == [], f"a config save after the node identity push: {later}"
