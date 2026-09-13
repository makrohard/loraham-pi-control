"""`rflog_decode`: the decoder subprocess behind the Decrypt toggle and `lhpc rflog --decrypt`.
Driven with a fake decoder script, so this proves LHPC's side of the line protocol — the stack's
interpreter, the argv without key material, N-in/N-out with matching keys, the typed
decoder-level error, the bounded run, the per-job cache — never any real crypto (that is the
decoders' own suites, run in the stacks' venvs)."""

from __future__ import annotations

import json
import os
import sys
import threading

import pytest
import repo_paths

from lhpc.core import rflog
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService

LINE = '2026-09-12T18:36:49.968Z RX rssi=-67.00 snr=11.25 len=4 hex=01020304 ascii="...."'


def _svc(tmp_path):
    return ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))


def _records(n):
    return [rflog.parse_line(f"{LINE[:-1]}{i}\"") for i in range(n)]


def _fake(tmp_path, body):
    """A decoder script: `body` is Python run with `reqs` (the parsed stdin lines) in scope."""
    p = tmp_path / "fake_decoder.py"
    p.write_text("import json, sys\nreqs = [json.loads(l) for l in sys.stdin if l.strip()]\n" + body)
    return p


def _use(monkeypatch, script, *extra):
    monkeypatch.setattr(ControllerService, "_rflog_decoder_argv",
                        lambda self, e: ((sys.executable, str(script), *extra), ""))


ECHO = ("for r in reqs:\n"
        "    print(json.dumps({'key': r['key'], 'status': 'ok', 'kind': 'text', 'peer': 'p', 'decoded': 'hi ' + r['raw'][-3:]}))\n")


# ---- the argv: the stack's interpreter, a script, paths — never key material ------------------

def test_meshtastic_argv_is_the_managed_cli_interpreter_and_the_prefs_dir(tmp_path):
    svc = _svc(tmp_path)
    e = rflog.entry("meshtastic")
    argv, err = svc._rflog_decoder_argv(e)
    assert argv == () and "not built" in err                              # no venv: typed, no run
    py = tmp_path / "build" / "tools" / "meshtastic-cli" / ".venv" / "bin" / "python"
    py.parent.mkdir(parents=True)
    py.write_text("")
    argv, err = svc._rflog_decoder_argv(e)
    assert err == "" and argv[0] == str(py) and argv[1].endswith("/rfdecode/decode_meshtastic.py")
    assert argv[2:] == ("--runtime", str(tmp_path), "--prefs", str(tmp_path / "state" / "meshtasticd" / "prefs"))


@pytest.mark.parametrize("surface,rel,tail", [
    ("meshcore", "src/openhop-core", ("--mode", "chat", "--secrets", "{rt}/config/secrets",
                                      "--store", "{rt}/state/meshcore/companion.db",
                                      "--repeater-store", "{rt}/state/openhop")),
    ("reticulum", "src/reticulum", ("--config", "{rt}/state/reticulum",
                                    "--meshchat", "{rt}/state/meshchat")),
])
def test_source_stacks_use_their_own_venv_and_name_only_paths(tmp_path, surface, rel, tail):
    svc = _svc(tmp_path)
    e = rflog.entry(surface)
    assert "not built" in svc._rflog_decoder_argv(e)[1]
    py = tmp_path / rel / ".venv" / "bin" / "python"
    py.parent.mkdir(parents=True)
    py.write_text("")
    argv, err = svc._rflog_decoder_argv(e)
    assert err == "" and argv[0] == str(py) and argv[1].endswith(f"decode_{surface}.py")
    assert argv[4:] == tuple(x.replace("{rt}", str(tmp_path)) for x in tail)
    assert os.path.isfile(argv[1])                                        # shipped package data


def test_a_venv_interpreter_is_a_symlink_out_of_the_root_and_still_resolves(tmp_path):
    """`bin/python` in every venv points at the system interpreter. On a real box the managed CLI
    venv was refused as a symlink escape (`under()` is for paths LHPC writes); the decoder path
    is one LHPC executes, contained lexically."""
    svc = _svc(tmp_path)
    for rel, surface in (("build/tools/meshtastic-cli/.venv/bin/python", "meshtastic"),
                         ("src/openhop-core/.venv/bin/python", "meshcore"),
                         ("src/reticulum/.venv/bin/python", "reticulum")):
        py = tmp_path / rel
        py.parent.mkdir(parents=True)
        os.symlink(sys.executable, py)
        argv, err = svc._rflog_decoder_argv(rflog.entry(surface))
        assert err == "" and argv[0] == str(py), (surface, err)


def test_reticulum_prefers_the_lxmf_venv_which_carries_rns_and_lxmf(tmp_path):
    svc = _svc(tmp_path)
    e = rflog.entry("reticulum")
    lx = tmp_path / "src" / "lxmf" / ".venv" / "bin" / "python"
    lx.parent.mkdir(parents=True)
    lx.write_text("")
    assert svc._rflog_decoder_argv(e)[0][0] == str(lx)
    rn = tmp_path / "src" / "reticulum" / ".venv" / "bin" / "python"
    rn.parent.mkdir(parents=True)
    rn.write_text("")
    assert svc._rflog_decoder_argv(e)[0][0] == str(lx)                   # both: LXMF's wins
    lx.unlink()
    assert svc._rflog_decoder_argv(e)[0][0] == str(rn)                   # RNS alone still decodes


def test_a_plaintext_job_is_refused_without_running_anything(tmp_path, monkeypatch):
    svc = _svc(tmp_path)
    monkeypatch.setattr(ControllerService, "_rflog_decoder_argv",
                        lambda self, e: pytest.fail("must not resolve a decoder"))
    res = svc.rflog_decode("loraham-kiss-tnc", "rf-kiss.log", _records(1))
    assert res["error"] == "not a decodable RF log" and res["records"] == _records(1)
    assert svc.rflog_decode("meshcore-node", "rf-meshtastic.log", [])["error"]    # not this writer's


# ---- the line protocol ------------------------------------------------------------------------

def test_n_lines_in_n_results_out_keyed_and_in_order(tmp_path, monkeypatch):
    svc = _svc(tmp_path)
    _use(monkeypatch, _fake(tmp_path, ECHO))
    recs = _records(3)
    res = svc.rflog_decode("meshtastic", "rf-meshtastic.log", recs)
    assert res["error"] == ""
    assert [r["decoded"] for r in res["records"]] == ['hi .0"', 'hi .1"', 'hi .2"']
    assert res["records"][0] == {**recs[0], "status": "ok", "kind": "text", "peer": "p", "decoded": 'hi .0"'}


@pytest.mark.parametrize("body,error", [
    ("print(json.dumps({'key': reqs[0]['key'], 'status': 'ok'}))\n", "decoder protocol violation (1 results for 2 records)"),
    ("for r in reqs: print(json.dumps({'key': 'x', 'status': 'ok'}))\n", "decoder protocol violation (key mismatch)"),
    ("for r in reqs: print(json.dumps({'key': r['key'], 'status': 'weird'}))\n", "decoder protocol violation (status)"),
    ("for r in reqs: print('plaintext leaked')\n", "decoder protocol violation (not JSON)"),
    ("sys.stderr.write('meshtastic channels unreadable: prefs/channels.proto\\nTraceback...\\n'); sys.exit(3)\n",
     "meshtastic channels unreadable: prefs/channels.proto"),
    ("sys.stderr.write('Traceback (most recent call last)\\nsecret stuff\\n'); sys.exit(1)\n", "decoder failed (exit 1)"),
])
def test_a_broken_decoder_is_one_typed_error_and_no_record(tmp_path, monkeypatch, body, error):
    svc = _svc(tmp_path)
    _use(monkeypatch, _fake(tmp_path, body))
    res = svc.rflog_decode("meshtastic", "rf-meshtastic.log", _records(2))
    assert res["error"] == error
    assert all(r["status"] == "" and r["decoded"] == "" for r in res["records"])
    assert "secret" not in json.dumps(res) and "Traceback" not in json.dumps(res)


def test_the_run_is_bounded_in_time_and_output(tmp_path, monkeypatch):
    svc = _svc(tmp_path)
    monkeypatch.setattr(ControllerService, "RFLOG_DECODE_TIMEOUT_S", 0.5)
    _use(monkeypatch, _fake(tmp_path, "import time; time.sleep(5)\n"))
    assert svc.rflog_decode("meshtastic", "rf-meshtastic.log", _records(1))["error"] == "decoder timed out"
    monkeypatch.setattr(ControllerService, "RFLOG_DECODE_MAX_OUT", 100)
    _use(monkeypatch, _fake(tmp_path, ECHO.replace("'hi ' + ", "'x' * 200 + ")))
    assert svc.rflog_decode("meshtastic", "rf-meshtastic.log", _records(1))["error"] == "decoder output too large"
    missing = tmp_path / "gone.py"
    monkeypatch.setattr(ControllerService, "_rflog_decoder_argv",
                        lambda self, e: ((str(tmp_path / "no-such-python"), str(missing)), ""))
    assert svc.rflog_decode("meshtastic", "rf-meshtastic.log", _records(1))["error"].startswith("decoder could not start")


def test_stdin_carries_key_and_raw_only_and_stdout_is_capped_per_field(tmp_path, monkeypatch):
    svc = _svc(tmp_path)
    seen = tmp_path / "seen.json"
    _use(monkeypatch, _fake(tmp_path, f"open({str(seen)!r}, 'w').write(json.dumps(reqs))\n"
                            "for r in reqs: print(json.dumps({'key': r['key'], 'status': 'ok', 'kind': 'k' * 99, 'decoded': 'd' * 9000}))\n"))
    rec = svc.rflog_decode("meshtastic", "rf-meshtastic.log", _records(1))["records"][0]
    assert json.loads(seen.read_text()) == [{"key": _records(1)[0]["key"], "raw": _records(1)[0]["raw"]}]
    assert len(rec["kind"]) == 40 and len(rec["decoded"]) == 4000


# ---- the cache: per job, by record key; `no-key` never sticks; meshcore's mode resets it -------

def test_decoded_records_are_cached_per_job_and_no_key_expires(tmp_path, monkeypatch):
    svc = _svc(tmp_path)
    counter = tmp_path / "runs"
    body = (f"open({str(counter)!r}, 'a').write(str(len(reqs)) + '\\n')\n"
            "for r in reqs:\n"
            "    st = 'no-key' if r['raw'].endswith('1\"') else 'ok'\n"
            "    print(json.dumps({'key': r['key'], 'status': st, 'decoded': st}))\n")
    _use(monkeypatch, _fake(tmp_path, body))
    recs = _records(3)
    svc.rflog_decode("meshtastic", "rf-meshtastic.log", recs)
    svc.rflog_decode("meshtastic", "rf-meshtastic.log", recs + _records(4)[3:])
    # first run: 3 misses; second: only the new record — the no-key answer is kept for the TTL
    assert counter.read_text().splitlines() == ["3", "1"]
    svc.rflog_decode("rns", "rf-reticulum.log", recs)                     # another job: its own cache
    assert counter.read_text().splitlines() == ["3", "1", "3"]
    monkeypatch.setattr(ControllerService, "RFLOG_NOKEY_TTL_S", 0.0)     # expired: the no-key frame is retried
    svc.rflog_decode("meshtastic", "rf-meshtastic.log", recs)
    assert counter.read_text().splitlines() == ["3", "1", "3", "1"]


def test_the_cache_is_bounded_and_the_meshcore_mode_resets_it(tmp_path, monkeypatch):
    svc = _svc(tmp_path)
    monkeypatch.setattr(ControllerService, "RFLOG_LRU_MAX", 2)
    counter = tmp_path / "runs"
    _use(monkeypatch, _fake(tmp_path, f"open({str(counter)!r}, 'a').write(str(len(reqs)) + '\\n')\n" + ECHO))
    recs = _records(3)
    svc.rflog_decode("meshcore-node", "rf-meshcore.log", recs)
    svc.rflog_decode("meshcore-node", "rf-meshcore.log", recs)
    assert counter.read_text().splitlines() == ["3", "1"]                 # 2 kept, 1 evicted
    assert svc.save_config_bundle("meshcore", values={"file_repeater_name": "LAB-REP", "file_mode": "repeater"}).ok
    svc.rflog_decode("meshcore-node", "rf-meshcore.log", recs[1:])
    assert counter.read_text().splitlines() == ["3", "1", "2"]            # new mode: nothing reused


def test_simultaneous_identical_polls_share_one_decoder_run(tmp_path, monkeypatch):
    """Two browser polls arriving together: the second waits on the per-job lock and then finds
    the first's answers in the cache — one child, not two."""
    svc = _svc(tmp_path)
    counter = tmp_path / "runs"
    _use(monkeypatch, _fake(tmp_path, f"import time; time.sleep(0.4); open({str(counter)!r}, 'a').write('run\\n')\n" + ECHO))
    recs = _records(4)
    results = []
    ts = [threading.Thread(target=lambda: results.append(svc.rflog_decode("meshtastic", "rf-meshtastic.log", recs)))
          for _ in range(3)]
    for th in ts:
        th.start()
    for th in ts:
        th.join()
    assert counter.read_text().count("run") == 1
    assert all(r["error"] == "" and [x["status"] for x in r["records"]] == ["ok"] * 4 for r in results)


def test_the_meshcore_decoder_follows_the_running_mode_until_a_restart(tmp_path, monkeypatch):
    """Saving `mode repeater` while the chat node runs must not switch the decoder's identity
    and stores under the live writer: the running mode wins until the restart, then the cache
    resets once."""
    svc = _svc(tmp_path)
    real_argv = ControllerService._rflog_decoder_argv
    py = tmp_path / "src" / "openhop-core" / ".venv" / "bin" / "python"
    py.parent.mkdir(parents=True)
    py.write_text("")
    assert svc.save_config_bundle("meshcore", values={"file_repeater_name": "LAB-REP", "file_mode": "repeater"}).ok
    live = {"running": True, "mode": "chat"}
    monkeypatch.setattr(ControllerService, "stack_running", lambda self, s: live["running"] and s == "meshcore")
    monkeypatch.setattr(ControllerService, "meshcore_running_mode", lambda self: live["mode"])
    argv, err = svc._rflog_decoder_argv(rflog.entry("meshcore"))
    assert err == "" and argv[argv.index("--mode") + 1] == "chat"          # saved repeater, running chat
    counter = tmp_path / "runs"
    _use(monkeypatch, _fake(tmp_path, f"open({str(counter)!r}, 'a').write(str(len(reqs)) + '\\n')\n" + ECHO))
    recs = _records(2)
    svc.rflog_decode("meshcore-node", "rf-meshcore.log", recs)
    svc.rflog_decode("meshcore-node", "rf-meshcore.log", recs)
    assert counter.read_text().splitlines() == ["2"]                       # cached: the mode did not move
    live["mode"] = "repeater"                                              # the restart happened
    monkeypatch.setattr(ControllerService, "_rflog_decoder_argv", real_argv)
    assert svc._rflog_decoder_argv(rflog.entry("meshcore"))[0][5] == "repeater"
    _use(monkeypatch, _fake(tmp_path, f"open({str(counter)!r}, 'a').write(str(len(reqs)) + '\\n')\n" + ECHO))
    svc.rflog_decode("meshcore-node", "rf-meshcore.log", recs)
    svc.rflog_decode("meshcore-node", "rf-meshcore.log", recs)
    assert counter.read_text().splitlines() == ["2", "2"]                  # reset once, then cached again
    live["running"] = False                                                # stopped: the saved mode applies
    monkeypatch.setattr(ControllerService, "_rflog_decoder_argv", real_argv)
    assert svc._rflog_decoder_argv(rflog.entry("meshcore"))[0][5] == "repeater"


def test_one_decoder_at_a_time_per_job(tmp_path, monkeypatch):
    svc = _svc(tmp_path)
    marker = tmp_path / "overlap"
    body = (f"import os, time\np = {str(marker)!r}\n"
            "assert not os.path.exists(p), 'two decoders ran at once'\n"
            "open(p, 'w').close(); time.sleep(0.3); os.unlink(p)\n" + ECHO)
    _use(monkeypatch, _fake(tmp_path, body))
    errors = []

    def go(i):
        errors.append(svc.rflog_decode("meshtastic", "rf-meshtastic.log", _records(5)[i:i + 1])["error"])
    ts = [threading.Thread(target=go, args=(i,)) for i in range(4)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert errors == ["", "", "", ""]


def test_the_decoder_scripts_ship_and_take_no_key_on_argv():
    """A textual negative invariant over the three shipped decoder scripts (the README's declared
    exception to rule 1): no driven path can prove that NO code in a script takes a key on argv
    or from the environment. Its behavioural twin is
    `test_stdin_carries_key_and_raw_only_and_stdout_is_capped_per_field` (what actually crosses
    the process boundary) plus the argv tests above (paths only)."""
    d = repo_paths.REPO / "lhpc" / "data" / "rfdecode"
    for e in rflog.REGISTRY:
        if e.decoder:
            src = (d / f"decode_{e.decoder}.py").read_text()
            assert "--key" not in src and "--psk" not in src and "os.environ" not in src


def test_a_store_path_that_escapes_the_root_is_a_typed_error_not_a_500(tmp_path):
    svc = _svc(tmp_path)
    py = tmp_path / "build" / "tools" / "meshtastic-cli" / ".venv" / "bin" / "python"
    py.parent.mkdir(parents=True)
    py.write_text("")
    (tmp_path / "state").mkdir()
    (tmp_path / "state" / "meshtasticd").mkdir()
    os.symlink("/tmp", tmp_path / "state" / "meshtasticd" / "prefs")          # a symlink out of the root
    argv, err = svc._rflog_decoder_argv(rflog.entry("meshtastic"))
    assert argv == () and "refused" in err
    res = svc.rflog_decode("meshtastic", "rf-meshtastic.log", _records(1))
    assert res["error"] == err and res["records"][0]["status"] == ""

