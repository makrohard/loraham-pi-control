"""T-G1 at L3 (PROOF §1, A §2.3): the resolved argv of every component's slot 0 equals the golden
base, dev after step D (0af1d413), token for token, with the TWO named exceptions of the L3
process tokens and nothing else:
- each `_gps-bridge` feed gains `--state <state/gps/<stack>>`;
- chat gains `--data-socket` and `--conf-socket` with slot 0's paths (the daemon's /tmp sockets).

The fixture `argv_slot0_0af1d413.json` was resolved at 0af1d413 with this module's `_resolve`; the
package's own directory reads `<lhpc>` and the controller's interpreter `<python>`, so neither the
checkout location nor the venv matters. The daemon's argv
is built per radio at spawn (`_ensure_daemon`, stage L5), not from defaults, and is outside this table."""
import json
from pathlib import Path

import lhpc
from lhpc.core import commands
from lhpc.core.config import Config
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService

GOLDEN = Path(__file__).with_name("argv_slot0_0af1d413.json")
RT = Path("/rt")
# component id -> the tokens L3 appends to its slot-0 argv (the only allowed difference)
EXCEPTIONS = {
    "meshcom-gps": ["--state", "/rt/state/gps/meshcom"],
    "meshcore-gps": ["--state", "/rt/state/gps/meshcore"],
    "meshtastic-gps": ["--state", "/rt/state/gps/meshtastic"],
}
# Stage L5 (W3, an I11 difference, A:142): a daemon client's socket pairs are its radio's. They are compared apart
# from the rest of the argv: each client carries exactly these pairs (on r1), and every other token is the base's.
SOCKETS = {
    "loraham-chat": [("--data-socket", "/tmp/lora-r1.sock"), ("--conf-socket", "/tmp/loraconf-r1.sock")],
    "loraham-kiss-tnc": [("--data-socket", "/tmp/loraf-r1.sock"), ("--conf-socket", "/tmp/loraconf-r1.sock")],
    "meshcom-bridge": [("--data-socket", "/tmp/loraf-r1.sock"), ("--conf-socket", "/tmp/loraconf-r1.sock")],
    "loraham-voice": [("--data-socket", "/tmp/lora-r1.sock"), ("--conf-socket", "/tmp/loraconf-r1.sock")],
    "loraham-voice-cli": [("--data-socket", "/tmp/lora-r1.sock"), ("--conf-socket", "/tmp/loraconf-r1.sock")],
}


def _without_sockets(argv):
    out, skip = [], False
    for t in argv:
        if skip:
            skip = False
        elif t in ("--data-socket", "--conf-socket"):
            skip = True
        else:
            out.append(t)
    return out


def _pairs(argv):
    return [(t, argv[i + 1]) for i, t in enumerate(argv[:-1]) if t in ("--data-socket", "--conf-socket")]


def _resolve() -> dict:
    """Every component's slot-0 argv with default params on its own band."""
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=RT))
    pkg, python = str(Path(lhpc.__file__).parent), commands._controller_python()
    out = {}
    for st in svc.stacks():
        for c in st.components:
            if not c.run_argv or c.id == svc.DAEMON_ID:
                continue
            src = str(svc._paths.resolve_source(c.source.path)) if c.source else str(RT)
            # stage L5 (W3): a daemon client runs as its radio's instance; slot 0 on r1
            inst = commands.Instance(0, "r1") if any("{rid}" in str(p.default or "") for p in c.run_params) else commands.SLOT0
            argv = commands.expand_argv(c.run_argv, c, svc.stack_config(c.id, c.band),
                                        Config().operator, str(RT), src, c.band, inst=inst)
            out[c.id] = ["<python>" if t == python else t.replace(pkg, "<lhpc>") for t in argv]
    return out


def test_slot0_argv_equals_the_base_but_for_the_two_named_exceptions():
    """intended: slot 0 launches what it launched at the base, plus the two named L3 tokens."""
    base, now = json.loads(GOLDEN.read_text()), _resolve()
    # Stage L5 (W7-8, an I11 difference, A:142): the daemon's argv exists only per radio, with the radio's own
    # RF-log path `{runtime}/logs/<rid>/rf-daemon.log`; it has no slot-0 form and leaves this table (T-D1 holds it).
    assert set(now) == set(base) - {"loraham-daemon"}
    for cid, argv in now.items():
        extra = EXCEPTIONS.get(cid, [])
        assert argv[len(argv) - len(extra):] == extra, cid       # each exception is really there
        rest = argv[:len(argv) - len(extra)]
        assert _pairs(rest) == SOCKETS.get(cid, []), cid             # the radio's sockets, exactly
        assert _without_sockets(rest) == _without_sockets(base[cid]), cid   # and nothing else differs
