"""T-G1 at L3 (PROOF §1, A §2.3): the resolved argv of every component's r1 instance equals the golden
base, dev after step D (0af1d413), token for token, with the named exceptions and nothing else:
- each `_gps-bridge` feed gains `--state <state/gps/<stack>>` (L3);
- a daemon client carries its radio's `--data-socket` / `--conf-socket` (r1's, stage L5);
- K4b (no slots): an instance's own paths sit under `<rid>/` on r1 too (K4B_PATHS, each base token named).

The fixture `argv_r1_0af1d413.json` was resolved at 0af1d413 with this module's `_resolve`; the
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

GOLDEN = Path(__file__).with_name("argv_r1_0af1d413.json")
RT = Path("/rt")
# component id -> the tokens L3 appends to its r1 argv (the only allowed difference)
EXCEPTIONS = {
    "meshcom-gps": ["--state", "/rt/state/gps/meshcom"],
    "meshcore-gps": ["--state", "/rt/state/gps/meshcore"],
    "meshtastic-gps": ["--state", "/rt/state/gps/meshtastic"],
    # K7 (DEC-W384s/af): each guest's run.sh instance, host ports and per-connection bridge redirect (the template: r1's)
    "meshcom-qemu": ["--instance", "0", "--ports", "18083:12323", "--xr-redirect", "7000:7000"],
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


# K4b: the base token -> r1's (its {instance_dir} path is `r1/` now, no instance keeps the base path)
K4B_PATHS = {
    "loraham-voice": {"/rt/config/files/loraham_voice": "/rt/config/files/r1/loraham_voice"},
    "loraham-voice-cli": {"/rt/config/files/loraham_voice_cli": "/rt/config/files/r1/loraham_voice_cli"},
    "loraham-kiss-tnc": {"/rt/logs/rf-kiss.log": "/rt/logs/r1/rf-kiss.log"},
    "meshcom-bridge": {"/rt/logs/rf-meshcom.log": "/rt/logs/r1/rf-meshcom.log"},
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
    """Every component's r1-instance argv with default params on its own band."""
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=RT))
    pkg, python = str(Path(lhpc.__file__).parent), commands._controller_python()
    out = {}
    for st in svc.stacks():
        for c in st.components:
            if not c.run_argv or c.id == svc.DAEMON_ID:
                continue
            src = str(svc._paths.resolve_source(c.source.path)) if c.source else str(RT)
            # stage L5 (W3): a daemon client runs as its radio's instance, here r1's
            inst = commands.Instance("r1") if any("{rid}" in str(p.default or "") for p in c.run_params) else commands.TEMPLATE
            argv = commands.expand_argv(c.run_argv, c, svc.stack_config(c.id, c.band),
                                        Config().operator, str(RT), src, c.band, inst=inst)
            out[c.id] = ["<python>" if t == python else t.replace(pkg, "<lhpc>") for t in argv]
    return out


def test_r1_argv_equals_the_base_but_for_the_two_named_exceptions():
    """intended: the r1 instance launches what it launched at the base, plus the two named L3 tokens."""
    base, now = json.loads(GOLDEN.read_text()), _resolve()
    # Stage L5 (W7-8, an I11 difference, A:142): the daemon's argv exists only per radio, with the radio's own
    # RF-log path `{runtime}/logs/<rid>/rf-daemon.log`; it has no template form and leaves this table (T-D1 holds it).
    assert set(now) == set(base) - {"loraham-daemon"}
    for cid, argv in now.items():
        extra = EXCEPTIONS.get(cid, [])
        assert argv[len(argv) - len(extra):] == extra, cid       # each exception is really there
        rest = argv[:len(argv) - len(extra)]
        assert _pairs(rest) == SOCKETS.get(cid, []), cid             # the radio's sockets, exactly
        moved = K4B_PATHS.get(cid, {})
        assert set(moved) <= set(base[cid]), cid                      # each named path is really the base's
        assert _without_sockets(rest) == [moved.get(t, t) for t in _without_sockets(base[cid])], cid   # nothing else
