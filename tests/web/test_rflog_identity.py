"""#74 (box H, rc0: "meshcore identity unreadable: …/config/secrets/meshcore_identity.key: [Errno 2]"): an RF log's
decoder reads its node's identity from that radio's own dirs, and a radio whose node never started there has none yet.
Through the log page's data route GET /api/rflog/<target>/decoded; the decoder child is recorded, never run:
- MeshCore: the decoder's own rule, repeater mode the repeater's key, every other mode the Companion's
  (config/secrets/<rid>/…); missing → the note "no identity yet for rN", no decoder run, no errno.
- Meshtastic: the instance's prefs state/<rid>/meshtasticd/prefs (its node's `-d` dir), not the stack's.
- "not built" is still the first answer; the band-less file without a top-level key gets the no-radio note."""
import pytest
import radios_fixture

from lhpc.core import config as cfgmod
from lhpc.core import radios
from lhpc.core.commands import Instance
from lhpc.core.paths import Paths
from lhpc.core.services import ControllerService

LINE = "2026-09-12T16:01:00.000Z RX rssi=-60.00 snr=9.00 len=3 hex=112233"
ROWS = (radios.Radio("r2", "a", "uputronics", "868", ce="ce0"), radios.Radio("r4", "b", "uputronics", "868", ce="ce1"),
        radios.Radio("r5", "c", "meshstick-1262", "868", usb_serial="26073012"))


def _box(tmp_path, monkeypatch, *, job="r5/rf-meshcore.log", built=True, keys=(), mode=""):
    """Radios r2/r4/r5, one line in `job`'s file, the MeshCore venv (`built`), `keys` = paths under the runtime root;
    `mode` saved on r5. Returns the recorded decoder argvs."""
    radios_fixture.write(tmp_path, ROWS)
    log = tmp_path / "logs" / job
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(LINE + "\n")
    for rel in ("src/openhop-core/.venv/bin/python", "build/tools/meshtastic-cli/.venv/bin/python") if built else ():
        py = tmp_path / rel
        py.parent.mkdir(parents=True, exist_ok=True)
        py.write_text("")
    for rel in keys:
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_bytes(b"00")
    if mode:
        cfgmod.update_stack_config(Paths(runtime_root=tmp_path), "meshcore",
                                   {"file_mode": mode, "file_repeater_name": "R5"}, inst=Instance("r5"))
    seen = []
    monkeypatch.setattr(ControllerService, "_rflog_run", lambda self, argv, misses: (seen.append(list(argv)), ({}, ""))[1])
    return seen


def _decoded(web, target, job):
    r = web().get(f"/api/rflog/{target}/decoded?job={job}")
    assert r.status_code == 200, r.status_code
    return r.get_json()["error"]


R4_KEY = "config/secrets/r4/meshcore_identity.key"            # box H: r4's node started, r5's never did


@pytest.mark.parametrize("mode, key", [
    ("", "config/secrets/r5/openhop_repeater_identity.key"),                   # chat: the Companion's key is missing
    ("chat+repeater", "config/secrets/r5/openhop_repeater_identity.key"),      # C1: chat+repeater reads the Companion's
    ("repeater", "config/secrets/r5/meshcore_identity.key"),                   # repeater: the repeater's key is missing
])
def test_a_radio_whose_node_never_started_gets_the_note_and_no_decoder_run(web, tmp_path, monkeypatch, mode, key):
    # red-before (0188b31c): the decoder ran, and its errno for config/secrets/r5/… was the page's error
    seen = _box(tmp_path, monkeypatch, keys=(R4_KEY, key), mode=mode)
    error = _decoded(web, "meshcore-node", "r5/rf-meshcore.log")
    assert error == "no identity yet for r5: meshcore has not started on this radio; the frames show undecoded"
    assert seen == []


@pytest.mark.parametrize("mode, key", [("", "config/secrets/r5/meshcore_identity.key"),
                                       ("chat+repeater", "config/secrets/r5/meshcore_identity.key"),
                                       ("repeater", "config/secrets/r5/openhop_repeater_identity.key")])
def test_the_radios_own_key_lets_the_decoder_run_with_that_radios_secrets(web, tmp_path, monkeypatch, mode, key):
    seen = _box(tmp_path, monkeypatch, keys=(key,), mode=mode)
    assert _decoded(web, "meshcore-node", "r5/rf-meshcore.log") == ""
    [argv] = seen
    assert dict(zip(argv, argv[1:]))["--secrets"] == str(tmp_path / "config" / "secrets" / "r5")


def test_not_built_is_still_the_first_answer(web, tmp_path, monkeypatch):
    # C2: neither the venv nor the key: the decoder is not built (as before), not "no identity yet"
    seen = _box(tmp_path, monkeypatch, built=False)
    assert "meshcore is not built" in _decoded(web, "meshcore-node", "r5/rf-meshcore.log") and seen == []


def test_the_band_less_file_without_a_top_level_key_gets_the_no_radio_note(web, tmp_path, monkeypatch):
    # the finding's page (job=rf-meshcore.log): box H never had config/secrets/meshcore_identity.key
    seen = _box(tmp_path, monkeypatch, job="rf-meshcore.log", keys=(R4_KEY,))
    assert _decoded(web, "meshcore-node", "rf-meshcore.log") == \
        "no identity yet for this log (no radio); the frames show undecoded"
    assert seen == []


def test_a_radios_meshtastic_log_is_decoded_with_that_nodes_prefs(web, tmp_path, monkeypatch):
    # red-before (0188b31c): --prefs state/meshtasticd/prefs, the stack's, for every radio's log
    seen = _box(tmp_path, monkeypatch, job="r2/rf-meshtastic.log",
                keys=("state/r2/meshtasticd/prefs/channels.proto", "state/meshtasticd/prefs/channels.proto"))
    assert _decoded(web, "meshtastic", "r2/rf-meshtastic.log") == ""
    [argv] = seen
    assert dict(zip(argv, argv[1:]))["--prefs"] == str(tmp_path / "state" / "r2" / "meshtasticd" / "prefs")


def test_a_radios_meshtastic_log_without_that_nodes_prefs_gets_the_note(web, tmp_path, monkeypatch):
    # the stack's prefs exist, r2's node never started: never decoded with another node's channels
    seen = _box(tmp_path, monkeypatch, job="r2/rf-meshtastic.log", keys=("state/meshtasticd/prefs/channels.proto",))
    assert _decoded(web, "meshtastic", "r2/rf-meshtastic.log") == \
        "no identity yet for r2: meshtastic has not started on this radio; the frames show undecoded"
    assert seen == []
