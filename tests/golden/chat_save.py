"""The chat-stack save scenario shared by the golden save module and the harness self-test."""

from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService

FILE = "config/stacks/chat.toml"
WRITES = ["lock:config", "recheck:config-journal", "mutate:write:state/config-txn.json",
          "mutate:write:" + FILE]
SAVED = {"ok": True, "summary": "Config saved for 'chat'.", "data_keys": [],
         "next_commands": ["lhpc stack start chat"], "heads": [], "outcomes": []}


def chat_svc(root, running=False):
    cmd = {555: ["loraham_chat"]} if running else {}
    svc = ControllerService(system=FakeSystem(cmdlines_data=cmd).system,
                            paths=Paths(runtime_root=root))
    seeded = svc.bootstrap(apply=True)
    assert seeded.ok, f"seeding: bootstrap failed: {seeded.summary}"
    return svc
