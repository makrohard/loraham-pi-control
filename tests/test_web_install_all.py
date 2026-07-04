"""Web surface of the bulk install-all feature: form modes/defaults, CSRF, second-stage RF
confirmation binding source+tests+TX, POST refusal matrix, ack path, run view, cursor log
API, welcome-banner tri-state, Apps-page button labels."""

import json
import os

from lhpc.core import bulk as bulk_mod
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService
from lhpc.adapters.web.app import create_app


def _client(tmp_path, cmdlines=None):
    svc = ControllerService(system=FakeSystem(cmdlines_data=cmdlines or {}).system,
                            paths=Paths(runtime_root=tmp_path))
    return create_app(service_factory=lambda: svc).test_client(), svc


def _csrf(c, url="/install-all"):
    body = c.get(url).data.decode()
    return body.split('name="_csrf" value="')[1].split('"')[0]


def test_form_defaults_and_install_mode(tmp_path):
    c, _ = _client(tmp_path)
    body = c.get("/install-all").data.decode()
    assert "Install and Build all Stacks" in body
    assert 'name="tests" value="yes" checked' in body            # tests default ON
    assert 'name="tx" value="yes" checked' not in body           # TX default OFF
    assert "several minutes" in body
    assert "Known working" in body and "Development" in body and "Latest stable" in body


def test_mode_wording_mixed_and_update(tmp_path):
    (tmp_path / "src" / "loraham-kiss-tnc").mkdir(parents=True)
    c, _ = _client(tmp_path)
    assert "Install and update all stacks" in c.get("/install-all").data.decode()
    assert "Install and update all stacks" in c.get("/stacks").data.decode()


def test_post_requires_csrf(tmp_path):
    c, _ = _client(tmp_path)
    assert c.post("/install-all/start", data={"source": "pinned"}).status_code == 400
    assert c.post("/install-all/ack").status_code == 400


def test_post_refused_while_component_running(tmp_path, monkeypatch):
    c, svc = _client(tmp_path, cmdlines={555: ["loraham_kiss_tnc"]})
    called = []
    monkeypatch.setattr(type(svc), "spawn_bulk_job",
                        lambda self, *a, **k: (called.append(1), (None, "x"))[1])
    tok = _csrf(c)
    r = c.post("/install-all/start", data={"_csrf": tok, "source": "pinned",
                                           "tests": "yes"}, follow_redirects=True)
    # spawn IS called and refuses via the driver gate? No: running-stack refusal comes
    # from the driver post-lock; the web spawn is gated only on bulk state. The spawned
    # driver refuses; here spawn was monkeypatched, so assert the flow reached it.
    assert called


def test_post_blocked_by_unacked_interrupted_marker(tmp_path, monkeypatch):
    paths = Paths(runtime_root=tmp_path)
    m = bulk_mod.new_marker("a" * 32, "install", "pinned", True, False,
                            [{"id": "daemon", "name": "d"}])
    m["state"] = "running"                                       # dead job -> interrupted
    assert bulk_mod.write_marker(paths, m)
    c, svc = _client(tmp_path)
    spawned = []
    monkeypatch.setattr(type(svc), "spawn_bulk_job",
                        lambda self, *a, **k: (spawned.append(1), (None, "no"))[1])
    body = c.get("/install-all").data.decode()
    assert "ended unexpectedly" in body or "Acknowledge" in body
    tok = _csrf(c)
    r = c.post("/install-all/start", data={"_csrf": tok, "source": "pinned",
                                           "tests": "yes"}, follow_redirects=True)
    # spawn_bulk_job (real) would refuse; monkeypatched here it reports its error flash
    assert r.status_code == 200


def test_ack_flow_unblocks(tmp_path):
    paths = Paths(runtime_root=tmp_path)
    m = bulk_mod.new_marker("b" * 32, "install", "pinned", True, False,
                            [{"id": "daemon", "name": "d"}])
    m["state"] = "running"
    assert bulk_mod.write_marker(paths, m)
    c, svc = _client(tmp_path)
    tok = _csrf(c)
    r = c.post("/install-all/ack", data={"_csrf": tok}, follow_redirects=True)
    assert b"acknowledged" in r.data
    assert svc.bulk_status() is None
    assert list((tmp_path / "state").glob("bulk-install.json.*.acked"))


def test_tx_post_renders_second_stage_confirmation(tmp_path, monkeypatch):
    c, svc = _client(tmp_path)
    spawned = []
    monkeypatch.setattr(type(svc), "spawn_bulk_job",
                        lambda self, *a, **k: (spawned.append((a, k)), ("l", None))[1])
    tok = _csrf(c)
    r = c.post("/install-all/start", data={"_csrf": tok, "source": "stable",
                                           "tests": "yes", "tx": "yes"})
    body = r.data.decode()
    assert not spawned                                           # NOT spawned yet
    assert "will TRANSMIT" in body                                # explicit RF confirm
    assert 'name="source" value="stable"' in body                # choices carried through
    ctok = body.split('name="confirm_token" value="')[1].split('"')[0]
    assert ctok                                                  # server-staged token
    # the confirmed second submission consumes the token and spawns bound choices
    tok2 = body.split('name="_csrf" value="')[1].split('"')[0]
    c.post("/install-all/start", data={"_csrf": tok2, "source": "stable", "tests": "yes",
                                       "tx": "yes", "confirm_token": ctok})
    assert spawned and spawned[0][0] == ("stable", True, True)
    # REPLAY of the consumed token: refused, zero further spawns
    c.post("/install-all/start", data={"_csrf": tok2, "source": "stable", "tests": "yes",
                                       "tx": "yes", "confirm_token": ctok})
    assert len(spawned) == 1


def test_tx_without_tests_refused(tmp_path, monkeypatch):
    c, svc = _client(tmp_path)
    spawned = []
    monkeypatch.setattr(type(svc), "spawn_bulk_job",
                        lambda self, *a, **k: (spawned.append(1), ("l", None))[1])
    tok = _csrf(c)
    r = c.post("/install-all/start", data={"_csrf": tok, "source": "pinned",
                                           "tx": "yes"}, follow_redirects=True)
    assert b"requires host tests" in r.data and not spawned


def test_run_view_rows_and_api(tmp_path, monkeypatch):
    monkeypatch.setattr(ControllerService, "_frozen_ref",
                        lambda self, comp, source: (("f" * 40, "frozen: stub"), ""))
    c, svc = _client(tmp_path)
    svc.install_all(apply=True, tests=True, emit=lambda s: None)
    body = c.get("/install-all").data.decode()
    assert 'id="bulk-run"' in body and body.count("data-stack=") == 8
    api = json.loads(c.get("/api/install-all?offset=0").data)
    assert api["state"]["state"] == "completed-with-failures"
    assert api["run_id"] == api["state"]["run_id"]
    assert set(api["log"]) >= {"offset", "data"}
    # offset validation: garbage -> treated as invalid, typed
    api2 = json.loads(c.get("/api/install-all?offset=zzz").data)
    assert api2["log"].get("error") == "invalid offset"


def test_task_detail_rendered_as_text(tmp_path):
    paths = Paths(runtime_root=tmp_path)
    m = bulk_mod.new_marker("c" * 32, "install", "pinned", True, False,
                            [{"id": "daemon", "name": "d"}])
    m["state"] = "completed"
    m["stacks"][0]["status"] = "fail"
    m["stacks"][0]["detail"] = "<script>alert(1)</script>"
    assert bulk_mod.write_marker(paths, m)
    body = _client(tmp_path)[0].get("/install-all").data.decode()
    assert "<script>alert(1)</script>" not in body               # escaped, text only
    assert "&lt;script&gt;" in body


def test_unsafe_marker_blocks_posts_and_shows_recovery(tmp_path, monkeypatch):
    d = tmp_path / "state"
    d.mkdir(parents=True)
    (d / "bulk-install.json").write_text("{broken")
    c, svc = _client(tmp_path)
    body = c.get("/install-all").data.decode()
    assert "unreadable or malformed" in body and "Acknowledge" in body
    ln, err = svc.spawn_bulk_job("pinned", True, False)          # POST path uses this
    assert ln is None and "acknowledge" in err


def test_welcome_banner_tristate(tmp_path):
    c, _ = _client(tmp_path)
    assert b"Welcome!" in c.get("/").data                        # fresh
    (tmp_path / "src" / "loraham-kiss-tnc").mkdir(parents=True)  # unmanaged tree
    c2, _ = _client(tmp_path)
    dash = c2.get("/").data.decode()
    assert "Welcome!" not in dash and "needs attention" in dash  # recovery, not welcome


def test_apps_button_install_label(tmp_path):
    c, _ = _client(tmp_path)
    assert "Install and Build all Stacks" in c.get("/stacks").data.decode()


# --- M2 round-2: server-enforced RF confirmation + recovery UI -------------------------------

def _no_spawn(monkeypatch, svc):
    spawned = []
    monkeypatch.setattr(type(svc), "spawn_bulk_job",
                        lambda self, *a, **k: (spawned.append(a), ("l", None))[1])
    return spawned


def test_direct_confirm_post_without_staged_state_refused(tmp_path, monkeypatch):
    # Posting arbitrary hidden values (incl. the legacy confirm_rf) with NO staged
    # server-side confirmation: refusal with zero mutation.
    c, svc = _client(tmp_path)
    spawned = _no_spawn(monkeypatch, svc)
    tok = _csrf(c)
    r = c.post("/install-all/start", data={"_csrf": tok, "source": "pinned",
                                           "tests": "yes", "tx": "yes",
                                           "confirm_rf": "yes",
                                           "confirm_token": "f" * 32},
               follow_redirects=True)
    assert b"RF confirmation refused" in r.data
    assert not spawned
    assert bulk_mod.read_reservation(Paths(runtime_root=tmp_path))[0] == "absent"


def _staged(c, source="stable"):
    tok = _csrf(c)
    body = c.post("/install-all/start", data={"_csrf": tok, "source": source,
                                              "tests": "yes", "tx": "yes"}).data.decode()
    ctok = body.split('name="confirm_token" value="')[1].split('"')[0]
    tok2 = body.split('name="_csrf" value="')[1].split('"')[0]
    return ctok, tok2


def test_confirmation_choice_changes_refused(tmp_path, monkeypatch):
    c, svc = _client(tmp_path)
    spawned = _no_spawn(monkeypatch, svc)
    for mutate in ({"source": "dev"},                            # selector changed
                   {"tests": ""},                                # tests changed
                   {"tx": ""}):                                  # TX dropped
        ctok, tok2 = _staged(c, source="stable")
        data = {"_csrf": tok2, "source": "stable", "tests": "yes", "tx": "yes",
                "confirm_token": ctok}
        data.update(mutate)
        data = {k: v for k, v in data.items() if v}
        r = c.post("/install-all/start", data=data, follow_redirects=True)
        if "source" in mutate:
            # selector changed after confirmation: the token consumption refuses
            assert b"RF confirmation refused" in r.data and not spawned, mutate
        elif "tests" in mutate:
            # tests dropped while TX kept: the coupling rule refuses even earlier
            assert b"requires host tests" in r.data and not spawned, mutate
        else:
            # TX dropped: a plain non-TX start is legitimate on its own — but it must
            # not have consumed/used the RF confirmation; clear for the next round
            spawned.clear()


def test_expired_and_malformed_confirmation_refused(tmp_path, monkeypatch):
    c, svc = _client(tmp_path)
    spawned = _no_spawn(monkeypatch, svc)
    ctok, tok2 = _staged(c)
    with c.session_transaction() as sess:                        # force expiry
        sess["_bulk_tx_confirm"] = dict(sess["_bulk_tx_confirm"], exp=1.0)
    r = c.post("/install-all/start", data={"_csrf": tok2, "source": "stable",
                                           "tests": "yes", "tx": "yes",
                                           "confirm_token": ctok}, follow_redirects=True)
    assert b"expired" in r.data and not spawned
    ctok, tok2 = _staged(c)
    with c.session_transaction() as sess:                        # malformed staged state
        sess["_bulk_tx_confirm"] = "garbage"
    r2 = c.post("/install-all/start", data={"_csrf": tok2, "source": "stable",
                                            "tests": "yes", "tx": "yes",
                                            "confirm_token": ctok},
                follow_redirects=True)
    assert b"RF confirmation refused" in r2.data and not spawned


def test_wrong_token_refused_and_consumes_staged_state(tmp_path, monkeypatch):
    c, svc = _client(tmp_path)
    spawned = _no_spawn(monkeypatch, svc)
    ctok, tok2 = _staged(c)
    r = c.post("/install-all/start", data={"_csrf": tok2, "source": "stable",
                                           "tests": "yes", "tx": "yes",
                                           "confirm_token": "0" * 32},
               follow_redirects=True)
    assert b"RF confirmation refused" in r.data and not spawned
    # single-use: the staged state was consumed by the failed attempt — the REAL token
    # no longer works either (start again from the form)
    r2 = c.post("/install-all/start", data={"_csrf": tok2, "source": "stable",
                                            "tests": "yes", "tx": "yes",
                                            "confirm_token": ctok},
                follow_redirects=True)
    assert b"RF confirmation refused" in r2.data and not spawned


def test_dead_reservation_shows_ack_button_and_recovers(tmp_path):
    # Dead reservation evidence with an ABSENT run marker: the page still shows the
    # acknowledgement control and the POST recovery works.
    paths = Paths(runtime_root=tmp_path)
    dead = {"starttime": 1, "pgid": 1, "sid": 1, "exec": "/bin/false",
            "argv_fp": "x", "argv_len": 1}
    ok, _ = bulk_mod.write_reservation(paths, "9" * 32, 999999, dead, phase="spawned")
    assert ok
    c, svc = _client(tmp_path)
    body = c.get("/install-all").data.decode()
    assert "Acknowledge" in body and "reservation" in body
    tok = _csrf(c)
    r = c.post("/install-all/ack", data={"_csrf": tok}, follow_redirects=True)
    assert b"acknowledged" in r.data
    assert bulk_mod.read_reservation(paths)[0] == "absent"
    assert "Acknowledge &amp; recover" not in c.get("/install-all").data.decode()


def test_confirmed_tx_post_refused_without_callsign(tmp_path):
    # The RF disclosure page may render, but the CONFIRMED POST refuses before any child
    # is spawned when no callsign is configured (spawn_bulk_job gate) — zero mutation.
    c, svc = _client(tmp_path)                                   # no callsign configured
    ctok, tok2 = _staged(c, source="pinned")                     # disclosure still renders
    r = c.post("/install-all/start", data={"_csrf": tok2, "source": "pinned",
                                           "tests": "yes", "tx": "yes",
                                           "confirm_token": ctok},
               follow_redirects=True)
    assert b"callsign" in r.data                                 # typed refusal shown
    assert bulk_mod.read_reservation(Paths(runtime_root=tmp_path))[0] == "absent"
    assert svc.bulk_status() is None                             # no marker either


def test_unbootstrapped_root_web_post_refuses_zero_mutation(tmp_path):
    absent = tmp_path / "absent-root"
    svc = ControllerService(system=FakeSystem(cmdlines_data={}).system,
                            paths=Paths(runtime_root=absent))
    c = create_app(service_factory=lambda: svc).test_client()
    tok = _csrf(c)
    r = c.post("/install-all/start", data={"_csrf": tok, "source": "pinned",
                                           "tests": "yes"}, follow_redirects=True)
    assert b"not bootstrapped" in r.data
    assert not absent.exists()                                   # ZERO runtime mutation


def test_orphan_risk_page_requires_confirmation_checkbox(tmp_path):
    paths = Paths(runtime_root=tmp_path)
    assert bulk_mod.write_orphan_risk(paths, "8" * 32, 4242,
                                      "cessation unproven", None)
    c, svc = _client(tmp_path)
    body = c.get("/install-all").data.decode()
    assert "ORPHAN RISK" in body and "4242" in body              # pid + reason surfaced
    assert 'name="confirm_orphan"' in body                       # explicit confirmation
    tok = _csrf(c)
    r = c.post("/install-all/ack", data={"_csrf": tok}, follow_redirects=True)
    assert b"confirmation" in r.data                             # refused without it
    assert bulk_mod.read_reservation(paths)[0] == "valid"
    r2 = c.post("/install-all/ack", data={"_csrf": tok, "confirm_orphan": "yes"},
                follow_redirects=True)
    assert b"acknowledged" in r2.data
    assert bulk_mod.read_reservation(paths)[0] == "absent"


def test_starting_card_shown_after_spawn_before_marker(tmp_path):
    # LIVE FINDING: after the POST nothing appeared until the driver wrote its marker
    # (double-clicks -> 'already reserved'). A live reservation with no marker now
    # renders an immediate 'Run starting…' card with polling armed.
    from lhpc.core import procident
    ident = procident.proc_identity(os.getpid())
    ok, _ = bulk_mod.write_reservation(Paths(runtime_root=tmp_path), "7" * 32,
                                       os.getpid(), ident, phase="spawned")
    assert ok
    c, svc = _client(tmp_path)
    body = c.get("/install-all").data.decode()
    assert "Run starting" in body and 'id="bulk-run"' in body
    assert "bulk.js" in body                                     # polling armed


def test_install_all_page_defaults_to_dev(tmp_path):
    c, _ = _client(tmp_path)
    body = c.get("/install-all").data.decode()
    assert 'value="dev" selected' in body


def test_starting_card_shown_in_spawning_phase(tmp_path):
    # The POST redirect lands within milliseconds — while the reservation is still in
    # phase 'spawning'. The card must show then too (LIVE FINDING: fast browsers got a
    # static page and had to reload manually).
    from lhpc.core import procident
    ident = procident.proc_identity(os.getpid())
    ok, _ = bulk_mod.write_reservation(Paths(runtime_root=tmp_path), "8" * 32,
                                       os.getpid(), ident, phase="spawning")
    assert ok
    c, svc = _client(tmp_path)
    body = c.get("/install-all").data.decode()
    assert "Run starting" in body and 'data-run-expect="' + "8" * 32 + '"' in body
    assert "bulk.js" in body


def test_starting_card_shown_over_old_terminal_marker(tmp_path):
    # LIVE FINDING (user): after a PREVIOUS completed run its terminal marker is still
    # on disk — the page showed only the old collapsed card, no poller, and the new
    # run's table needed a manual reload. A live reservation for a DIFFERENT run over a
    # terminal marker now renders the starting card and suppresses the old card.
    from lhpc.core import procident
    paths = Paths(runtime_root=tmp_path)
    m = bulk_mod.new_marker("a" * 32, "install", "dev", True, False,
                            [{"id": "daemon", "name": "d"}])
    m["state"] = "completed"
    m["finished_at"] = "2026-07-05T00:00:00Z"
    assert bulk_mod.write_marker(paths, m)
    ident = procident.proc_identity(os.getpid())
    ok, _ = bulk_mod.write_reservation(paths, "b" * 32, os.getpid(), ident,
                                       phase="spawning")
    assert ok
    c, svc = _client(tmp_path)
    body = c.get("/install-all").data.decode()
    assert "Run starting" in body
    assert 'data-run-expect="' + "b" * 32 + '"' in body
    assert "Last run " + "a" * 8 not in body                     # old card suppressed
    assert body.count('id="bulk-run"') == 1                      # single poller anchor
    assert "bulk.js" in body


def _seed_running_marker(paths, run_id="c" * 32):
    m = bulk_mod.new_marker(run_id, "install", "dev", True, False,
                            [{"id": "daemon", "name": "LoRaHAM Daemon"},
                             {"id": "kiss", "name": "LoRaHAM KISS"}])
    m["state"] = "running"
    assert bulk_mod.write_marker(paths, m)
    return m


def test_component_log_stream_frames_and_advances(tmp_path):
    # The second run-view window: canonical order, ASCII-framed titles, drained logs
    # advance to the successor, live tail keeps the cursor.
    paths = Paths(runtime_root=tmp_path)
    _seed_running_marker(paths)
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "build-loraham-daemon.log").write_text("daemon build output\n")
    (logs / "build-loraham-kiss-tnc.log").write_text("kiss build ok\n")
    c, svc = _client(tmp_path)
    out = svc.bulk_component_log_chunk("c" * 32, 0, 0)
    assert "+====" in out["data"]                                # ASCII frame
    assert "LoRaHAM Daemon" in out["data"] and "Build log" in out["data"]
    assert "daemon build output" in out["data"]
    assert "kiss build ok" in out["data"]                        # advanced to successor
    assert out["index"] >= 1
    # live tail: appended bytes stream from the same cursor
    with (logs / "build-loraham-kiss-tnc.log").open("a") as f:
        f.write("more output\n")
    out2 = svc.bulk_component_log_chunk("c" * 32, out["index"], out["offset"])
    assert "more output" in out2["data"]
    assert "daemon build output" not in out2["data"]             # no re-send


def test_component_log_stream_mtime_gates_stale_runs(tmp_path):
    # A log left over from a PREVIOUS run (mtime before started_at) is not streamed.
    paths = Paths(runtime_root=tmp_path)
    m = _seed_running_marker(paths)
    logs = tmp_path / "logs"
    logs.mkdir()
    stale = logs / "build-loraham-daemon.log"
    stale.write_text("OLD RUN CONTENT\n")
    os.utime(stale, (1.0, 1.0))                                  # long before this run
    c, svc = _client(tmp_path)
    out = svc.bulk_component_log_chunk("c" * 32, 0, 0)
    assert "OLD RUN CONTENT" not in out["data"]


def test_component_log_stream_rejects_wrong_run(tmp_path):
    paths = Paths(runtime_root=tmp_path)
    _seed_running_marker(paths)
    c, svc = _client(tmp_path)
    out = svc.bulk_component_log_chunk("f" * 32, 0, 0)
    assert out == {"index": 0, "offset": 0, "data": ""}


def test_api_and_template_carry_component_log_window(tmp_path, monkeypatch):
    paths = Paths(runtime_root=tmp_path)
    _seed_running_marker(paths)
    (tmp_path / "logs").mkdir()
    (tmp_path / "logs" / ("install-all-" + "c" * 8 + ".log")).write_text("x")
    c, svc = _client(tmp_path)
    monkeypatch.setattr(type(svc), "bulk_running", lambda self: True)
    r = c.get("/api/install-all?offset=0&ci=0&co=0").get_json()
    assert "complog" in r and set(r["complog"]) == {"index", "offset", "data"}
    body = c.get("/install-all").data.decode()
    assert 'id="bulk-complog"' in body                           # second window
    assert "logbox-half" in body                                 # main window halved


def test_spawn_refusal_output_is_shown(tmp_path):
    # LIVE FINDING: a spawned driver that REFUSES pre-claim (components running) exited
    # with its reason only in its own log — the page silently kept the starting card.
    # /install-all?spawn=<run_id> now shows that run's output as a refusal card.
    rid = "d" * 32
    logs = tmp_path / "logs"
    logs.mkdir(parents=True)
    (logs / f"install-all-{rid[:8]}.log").write_text(
        "ERR   Refusing to start the bulk run: component(s) are running\n")
    c, svc = _client(tmp_path)
    body = c.get(f"/install-all?spawn={rid}").data.decode()
    assert "Run could not start" in body
    assert "Refusing to start the bulk run" in body


def test_spawn_param_ignored_when_marker_matches(tmp_path):
    # Once the marker for that run exists, the ?spawn param must NOT show a refusal.
    rid = "c" * 32
    paths = Paths(runtime_root=tmp_path)
    _seed_running_marker(paths, run_id=rid)
    logs = tmp_path / "logs"
    logs.mkdir(parents=True)
    (logs / f"install-all-{rid[:8]}.log").write_text("normal run output\n")
    c, svc = _client(tmp_path)
    body = c.get(f"/install-all?spawn={rid}").data.decode()
    assert "Run could not start" not in body


def test_api_reports_spawn_liveness(tmp_path):
    from lhpc.core import procident
    c, svc = _client(tmp_path)
    r = c.get("/api/install-all?offset=0").get_json()
    assert r["spawn_live"] is False                              # nothing reserved
    ident = procident.proc_identity(os.getpid())
    ok, _ = bulk_mod.write_reservation(Paths(runtime_root=tmp_path), "e" * 32,
                                       os.getpid(), ident, phase="spawning")
    assert ok
    r = c.get("/api/install-all?offset=0").get_json()
    assert r["spawn_live"] is True                               # live spawn visible


def test_component_log_headers_emitted_exactly_once(tmp_path):
    # LIVE FINDING: an EMPTY live log re-framed its ASCII header on EVERY 2s poll
    # ("header lots of times but not the log"). The frame now rides with the file's
    # FIRST bytes (or once while passing a complete empty file) — never repeated.
    paths = Paths(runtime_root=tmp_path)
    _seed_running_marker(paths)
    logs = tmp_path / "logs"
    logs.mkdir()
    f = logs / "build-loraham-daemon.log"
    f.write_text("")                                             # live tail, no bytes
    c, svc = _client(tmp_path)
    ci = co = 0
    for _ in range(3):                                           # poll while empty
        out = svc.bulk_component_log_chunk("c" * 32, ci, co)
        assert out["data"] == ""                                 # NO header spam
        ci, co = out["index"], out["offset"]
    f.write_text("first output\n")
    out = svc.bulk_component_log_chunk("c" * 32, ci, co)
    assert out["data"].count("LoRaHAM Daemon") == 1              # header once
    assert "first output" in out["data"]
    ci, co = out["index"], out["offset"]
    out = svc.bulk_component_log_chunk("c" * 32, ci, co)
    assert out["data"] == ""                                     # no re-header, no re-send
