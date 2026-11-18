"""testlab/hardware/mcset.py, the pre-step M helper, through its change path: a fake `lhpc` on the runtime path (scripted
list/get/set/reset answers, every call logged), a fake ControllerService (the job's parameters) and a local HTTPS page (the
Config page the helper checks before and after). Each row: the verdict line, and which `lhpc config` calls ran."""
import datetime
import http.server
import ipaddress
import json
import ssl
import subprocess
import sys
import textwrap
import threading
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

HELPER = Path(__file__).resolve().parents[2] / "hardware" / "mcset.py"

FAKE_LHPC = textwrap.dedent('''\
    import json, sys
    st = sys.argv[0].rsplit("/venv/", 1)[0] + "/state.json"
    s = json.load(open(st))
    a = sys.argv[1:]
    open(s["log"], "a").write(" ".join(a) + "\\n")
    assert a[0] == "config" and a[1] == s["inst"], a
    a = a[2:]; v = s["vals"]; rc = 0
    if a == ["list"]:
        print("OK list"); [print(f"    {k} = {v[k]}") for k in sorted(v)]
    elif a == ["--reset", "--yes"]:
        if not s.get("fail_undo"): s["vals"] = dict(s["pre"])
        print("OK reset")
    elif len(a) == 1:
        g = s.get("get_bad", {}).get(a[0])                      # [rc, first word]: a read that fails or is not an OK answer
        print(f"{g[1] if g else 'OK'} {a[0]}"); print(f"  value   = {v[a[0]] or '(empty)'}"); rc = g[0] if g else 0
    else:
        n, x = a; c = s["sets"][n] = s["sets"].get(n, 0) + 1
        if c == 1 and n in s.get("fail_set", []): rc = 1; print("refused")
        elif c > 1 and s.get("fail_undo"): print("OK (ignored)")
        else:
            v[n] = s.get("wrong", {}).get(n, x) if c == 1 else x
            for o, y in s.get("side", {}).get(n, []): v[o] = y
            print("OK set")
    json.dump(s, open(st, "w")); sys.exit(rc)
    ''')

FAKE_SERVICES = textwrap.dedent('''\
    import json, os
    class ControllerService:
        def config_param_fields(self, sid):
            open(os.environ["FAKE_STATE"] + ".used", "a").close()
            return json.load(open(os.environ["FAKE_STATE"]))["fields"]
    ''')


@pytest.fixture(scope="module")
def page_server(tmp_path_factory):
    """A local HTTPS Config page: each field `name="<field>" value="<the instance's value>"`; with page_missing, every
    page after the first shows the field without its value."""
    d = tmp_path_factory.mktemp("tls")
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "127.0.0.1")])
    now = datetime.datetime.now(datetime.UTC)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(1).not_valid_before(now - datetime.timedelta(days=1)).not_valid_after(now + datetime.timedelta(days=1))
            .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), critical=False)
            .sign(key, hashes.SHA256()))
    (d / "c.pem").write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    (d / "k.pem").write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                                serialization.NoEncryption()))
    box = {"state": None, "hits": 0}

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            s = json.loads(Path(box["state"]).read_text())
            box["hits"] += 1
            show = not (s.get("page_missing") and box["hits"] > 1)
            body = "".join(f'<input name="{f["field"]}"' + (f' value="{s["vals"].get(f["name"], "")}"' if show else "") + ">"
                           for f in s["fields"])
            self.send_response(200)
            self.end_headers()
            self.wfile.write(body.encode())

        def log_message(self, *a):
            pass
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(d / "c.pem", d / "k.pem")
    srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv.server_address[1], box
    srv.shutdown()


def run(tmp_path, page_server, job, sid, rid, keys, src, vals, fields, override="-", inst_file=None, glob_file=None, **state):
    port, box = page_server
    rt = tmp_path / "rt"
    (rt / "config" / "stacks").mkdir(parents=True)
    (rt / "venv" / "lhpc" / "bin").mkdir(parents=True)
    lh = rt / "venv" / "lhpc" / "bin" / "lhpc"
    lh.write_text(f"#!{sys.executable}\n" + FAKE_LHPC)
    lh.chmod(0o755)
    pkg = tmp_path / "pkg" / "lhpc" / "core"
    pkg.mkdir(parents=True)
    (pkg.parent / "__init__.py").write_text("")
    (pkg / "__init__.py").write_text("")
    (pkg / "services.py").write_text(FAKE_SERVICES)
    srcname, srctext = src
    (rt / "config" / "stacks" / srcname).write_text(srctext)
    if inst_file is not None:
        (rt / "config" / "stacks" / f"{sid}@{rid}.toml").write_text(inst_file)
    if glob_file is not None:
        (rt / "config" / "stacks" / f"{sid}.toml").write_text(glob_file)
    log = tmp_path / "lhpc.log"
    st = {"inst": f"{sid}@{rid}", "vals": dict(vals), "pre": dict(vals), "sets": {}, "log": str(log),
          "fields": [{"key": k, "kind": kind, "name": k, "field": f"f_{k}" if kind == "file" else f"c_{k}", "flag": False}
                     for k, kind in fields], **state}
    (rt / "state.json").write_text(json.dumps(st))
    box["state"], box["hits"] = str(rt / "state.json"), 0
    text = f"OVERRIDE = '{override}'\n" + HELPER.read_text()
    # cwd: the test's own directory (the stdin script's sys.path[0] is the cwd: the repo's real lhpc must not be imported)
    p = subprocess.run([sys.executable, "-", job, sid, srcname, rid, keys, str(port), str(rt)], input=text, capture_output=True,
                       cwd=tmp_path, text=True, timeout=60, check=False, env={"PATH": "/usr/bin:/bin", "PYTHONPATH": str(tmp_path / "pkg"),
                                                                "FAKE_STATE": str(rt / "state.json")})
    assert p.returncode == 0, p.stdout + p.stderr
    sets = [ln for ln in log.read_text().splitlines() if len(ln.split()) >= 4 or "--reset" in ln] if log.exists() else []
    end = json.loads((rt / "state.json").read_text())["vals"]
    return p.stdout.splitlines()[-1], sets, end


GW = ("J2", "graywolf", "r1", "call", ("graywolf@433.toml", 'call = "N0CALL-3"\n'))
MT = ("J3", "meshtastic", "r2", "node_name,node_short", ("meshtastic@868.toml", 'node_name = "LAB"\nnode_short = "LB"\n'))
MTF = [("node_name", "run"), ("node_short", "run")]


def test_a_new_value_is_set_read_back_and_kept(tmp_path, page_server):
    v, sets, end = run(tmp_path, page_server, *GW, {"call": "OLD", "other": "Z"}, [("call", "run")], inst_file='x = 1\n')
    assert v == "JOB J2 graywolf@r1 PASS: set call"
    assert (tmp_path / "rt" / "state.json.used").exists()          # the fake ControllerService answered, not the repo's
    assert sets == ["config graywolf@r1 call N0CALL-3"] and end["call"] == "N0CALL-3"


def test_an_equal_value_is_not_set(tmp_path, page_server):
    v, sets, _ = run(tmp_path, page_server, *GW, {"call": "N0CALL-3"}, [("call", "run")])
    assert v == "JOB J2 graywolf@r1 PASS: nothing to set (kept or equal)" and sets == []


def test_a_value_the_instance_file_holds_wins_and_is_not_set(tmp_path, page_server):
    # a24 G10: without the KEEP skip the source's value overwrote the instance's own entry
    v, sets, end = run(tmp_path, page_server, *GW, {"call": "MINE"}, [("call", "run")], inst_file='call = "MINE"\n')
    assert v == "JOB J2 graywolf@r1 PASS: nothing to set (kept or equal)" and sets == [] and end["call"] == "MINE"


def test_a_failed_set_runs_the_undo_and_stops_with_pre_restored(tmp_path, page_server):
    # a24 G9: without the undo the first key stayed changed and the verdict hid it
    v, sets, end = run(tmp_path, page_server, *MT, {"node_name": "A", "node_short": "a"}, MTF, inst_file='x = 1\n',
                       fail_set=["node_short"])
    assert v == "JOB J3 meshtastic@r2 STOP: M2: set node_short rc 1; the undo restored PRE"
    assert sets == ["config meshtastic@r2 node_name LAB", "config meshtastic@r2 node_short LB",
                    "config meshtastic@r2 node_name A", "config meshtastic@r2 node_short a"]
    assert end == {"node_name": "A", "node_short": "a"}


def test_an_undo_that_does_not_restore_pre_is_unresolved(tmp_path, page_server):
    v, _, end = run(tmp_path, page_server, *MT, {"node_name": "A", "node_short": "a"}, MTF, inst_file='x = 1\n',
                    fail_set=["node_short"], fail_undo=True)
    assert v == "JOB J3 meshtastic@r2 UNRESOLVED: M2: set node_short rc 1; the undo did NOT restore PRE"
    assert end["node_name"] == "LAB"


def test_without_an_instance_file_or_autostart_the_undo_is_the_reset(tmp_path, page_server):
    v, sets, end = run(tmp_path, page_server, *MT, {"node_name": "A", "node_short": "a"}, MTF, fail_set=["node_short"])
    assert v == "JOB J3 meshtastic@r2 STOP: M2: set node_short rc 1; the undo restored PRE"
    assert sets[-1] == "config meshtastic@r2 --reset --yes" and end == {"node_name": "A", "node_short": "a"}


def test_a_set_that_changes_another_line_is_undone(tmp_path, page_server):
    # a24 G11: without the M3 other-lines check a side effect on another parameter passed
    v, sets, end = run(tmp_path, page_server, *GW, {"call": "OLD", "other": "Z"}, [("call", "run")], inst_file='x = 1\n',
                       side={"call": [["other", "Y"]]})
    assert v == "JOB J2 graywolf@r1 UNRESOLVED: M3: 2 other list line(s) changed; the undo did NOT restore PRE"
    assert sets == ["config graywolf@r1 call N0CALL-3", "config graywolf@r1 call OLD"] and end == {"call": "OLD", "other": "Y"}


def test_a_value_that_does_not_read_back_is_undone(tmp_path, page_server):
    # a24 G14: without the M3 read-back a wrong stored value passed
    v, sets, end = run(tmp_path, page_server, *GW, {"call": "OLD"}, [("call", "run")], inst_file='x = 1\n', wrong={"call": "BAD"})
    assert v == "JOB J2 graywolf@r1 STOP: M3: call does not read back its value; the undo restored PRE"
    assert sets == ["config graywolf@r1 call N0CALL-3", "config graywolf@r1 call OLD"] and end == {"call": "OLD"}


def test_a_page_without_the_value_stops_and_keeps_the_set(tmp_path, page_server):
    v, sets, end = run(tmp_path, page_server, *GW, {"call": "OLD"}, [("call", "run")], inst_file='x = 1\n', page_missing=True)
    assert v == "JOB J2 graywolf@r1 STOP: M4: the field c_call does not show the value (the set is kept; analysis first)"
    assert sets == ["config graywolf@r1 call N0CALL-3"] and end == {"call": "N0CALL-3"}


MC = ("J4", "meshcore", "r2", "file_node_name")
MCF = [("node_name", "file"), ("mode", "file")]
LAB = 'file_node_name = "LAB"\nfile_mode = "chat+repeater"\n'      # the lab's source (2026-11-18): its mode is never copied


@pytest.mark.parametrize("mode", ["chat+repeater", "repeater", "", "Chat"])
def test_meshcore_whose_node_is_not_in_chat_mode_stops_before_any_set(tmp_path, page_server, mode):
    # a24 M1 / handler DEC: the job reads the instance's mode (read only) and stops unless it is chat: row 4 then is NOT RUN
    v, sets, end = run(tmp_path, page_server, *MC, ("meshcore.toml", LAB), {"node_name": "OLD", "mode": mode}, MCF,
                       inst_file='x = 1\n')
    assert v == (f"JOB J4 meshcore@r2 STOP: M1: the node's mode reads '{mode}', not chat: row 4's node would relay frames "
                 "the kit never counts; nothing set"), v
    assert sets == [] and end["mode"] == mode


def test_meshcore_in_chat_mode_copies_the_name_and_never_writes_the_mode(tmp_path, page_server):
    # the lab's case (the source says chat+repeater, the node runs chat): only the name is written. At 61dd5667 the kit's J4
    # also listed file_mode and stopped on it (red: test_m_runs_one_job_per_stack_row_after_r1_in_the_full_lane)
    v, sets, end = run(tmp_path, page_server, *MC, ("meshcore.toml", LAB), {"node_name": "OLD", "mode": "chat"}, MCF,
                       inst_file='x = 1\n')
    assert v == "JOB J4 meshcore@r2 PASS: set node_name"
    assert sets == ["config meshcore@r2 node_name LAB"] and end == {"node_name": "LAB", "mode": "chat"}


@pytest.mark.parametrize("bad", [[1, "OK"], [0, "ERROR"]])
def test_a_mode_read_that_fails_stops_even_with_a_chat_value_line(tmp_path, page_server, bad):
    # a24 M1 (r4): only `cur not in ok` was tested; a refused read (rc 1, or no OK answer) that still prints chat is no check
    v, sets, _ = run(tmp_path, page_server, *MC, ("meshcore.toml", LAB), {"node_name": "OLD", "mode": "chat"}, MCF,
                     inst_file='x = 1\n', get_bad={"mode": bad})
    assert v == ("JOB J4 meshcore@r2 STOP: M1: the node's mode reads 'chat', not chat: row 4's node would relay frames "
                 "the kit never counts; nothing set"), v
    assert sets == []


def test_a_job_that_asks_to_copy_the_mode_stops(tmp_path, page_server):
    v, sets, _ = run(tmp_path, page_server, "J4", "meshcore", "r2", "file_node_name,file_mode", ("meshcore.toml", LAB),
                     {"node_name": "OLD", "mode": "chat"}, MCF)
    assert v == "JOB J4 meshcore@r2 STOP: M1: file_mode is checked read-only, never copied; nothing set" and sets == []


def test_without_one_mode_parameter_the_check_stops(tmp_path, page_server):
    v, sets, _ = run(tmp_path, page_server, *MC, ("meshcore.toml", LAB), {"node_name": "OLD"}, [("node_name", "file")])
    assert v == "JOB J4 meshcore@r2 STOP: M1: the mode check maps to 0 parameters of meshcore; nothing set" and sets == []
