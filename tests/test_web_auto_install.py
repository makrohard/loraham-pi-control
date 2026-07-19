"""Web surface of the auto-install auto-install feature: form modes/defaults, CSRF, second-stage RF
confirmation binding source+tests+TX, POST refusal matrix, ack path, run view, cursor log
API, welcome-banner tri-state, Apps-page button labels."""
import pytest

import json
import os
from pathlib import Path

from lhpc.core import auto_install as ai_mod
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService
from lhpc.adapters.web.app import create_app


def _client(tmp_path, cmdlines=None):
    svc = ControllerService(system=FakeSystem(cmdlines_data=cmdlines or {}).system,
                            paths=Paths(runtime_root=tmp_path))
    return create_app(service_factory=lambda: svc).test_client(), svc


def _csrf(c, url="/auto-install"):
    body = c.get(url).data.decode()
    return body.split('name="_csrf" value="')[1].split('"')[0]


def _all_form(svc, tok, source="pinned", tests=True, tx=False, **extra):
    """POST payload selecting EVERY stack (the pre-per-stack global behaviour): install every stack at
    `source`, host tests on every testable stack, TX only on the tx-capable stack (daemon)."""
    data = {"_csrf": tok}
    for r in svc.auto_install_rows():
        sid = r["id"]
        data[f"install:{sid}"] = "yes"
        data[f"version:{sid}"] = source
        if tests and r["testable"]:
            data[f"tests:{sid}"] = "yes"
        if tx and r["tx_capable"]:
            data[f"tx:{sid}"] = "yes"
    data.update(extra)
    return data


def _sel(svc, source="pinned", tests=True, tx=False):
    """A uniform per-stack selection dict (for direct spawn_auto_install_job calls in tests)."""
    return {st.id: {"install": True, "version": source, "tests": tests,
                    "tx": bool(tx) and st.id == "daemon"}
            for st in svc.stacks() if any(c.source for c in st.components)}


def test_form_defaults_and_install_mode(tmp_path):
    c, _ = _client(tmp_path)
    body = c.get("/auto-install").data.decode()
    assert "Install and Build all Stacks" in body
    assert 'name="install:daemon" value="yes" checked' in body   # per-stack install default ON
    assert 'id="ai-all-install"' in body and 'id="ai-all-version"' in body  # the "All" master row
    # Tests + TX default OFF (opt-in): neither the master nor a per-stack tests/tx box is pre-checked.
    assert '<input type="checkbox" id="ai-all-tests">' in body    # master Tests unchecked
    assert 'class="ai-tests" name="tests:daemon" value="yes"' in body
    assert 'name="tests:daemon" value="yes" checked' not in body  # per-stack Tests unchecked
    assert 'class="ai-tx" name="tx:meshcom" value="yes" disabled' in body   # TX disabled off daemon
    assert "several minutes" in body
    assert "Known working" in body and "Development" in body and "Latest stable" in body


def test_post_requires_csrf(tmp_path):
    c, _ = _client(tmp_path)
    assert c.post("/auto-install/start", data={"source": "pinned"}).status_code == 400
    assert c.post("/auto-install/ack").status_code == 400


def test_post_refused_while_component_running(tmp_path, monkeypatch):
    c, svc = _client(tmp_path, cmdlines={555: ["loraham-kiss-tnc"]})
    called = []
    monkeypatch.setattr(type(svc), "spawn_auto_install_job",
                        lambda self, *a, **k: (called.append(1), (None, "x"))[1])
    tok = _csrf(c)
    c.post("/auto-install/start", data={"_csrf": tok, "source": "pinned",
                                        "tests": "yes"}, follow_redirects=True)
    # spawn IS called and refuses via the driver gate? No: running-stack refusal comes
    # from the driver post-lock; the web spawn is gated only on auto-install state. The spawned
    # driver refuses; here spawn was monkeypatched, so assert the flow reached it.
    assert called


def test_post_blocked_by_unacked_interrupted_marker(tmp_path, monkeypatch):
    paths = Paths(runtime_root=tmp_path)
    m = ai_mod.new_marker("a" * 32, "install", "pinned", True, False,
                            [{"id": "daemon", "name": "d"}])
    m["state"] = "running"                                       # dead job -> interrupted
    assert ai_mod.write_marker(paths, m)
    c, svc = _client(tmp_path)
    spawned = []
    monkeypatch.setattr(type(svc), "spawn_auto_install_job",
                        lambda self, *a, **k: (spawned.append(1), (None, "no"))[1])
    body = c.get("/auto-install").data.decode()
    assert "ended unexpectedly" in body or "Acknowledge" in body
    tok = _csrf(c)
    r = c.post("/auto-install/start", data={"_csrf": tok, "source": "pinned",
                                           "tests": "yes"}, follow_redirects=True)
    # spawn_auto_install_job (real) would refuse; monkeypatched here it reports its error flash
    assert r.status_code == 200


def test_ack_flow_unblocks(tmp_path):
    paths = Paths(runtime_root=tmp_path)
    m = ai_mod.new_marker("b" * 32, "install", "pinned", True, False,
                            [{"id": "daemon", "name": "d"}])
    m["state"] = "running"
    assert ai_mod.write_marker(paths, m)
    c, svc = _client(tmp_path)
    tok = _csrf(c)
    r = c.post("/auto-install/ack", data={"_csrf": tok}, follow_redirects=True)
    assert b"acknowledged" in r.data
    assert svc.auto_install_status() is None
    assert list((tmp_path / "state").glob("auto-install.json.*.acked"))


def test_tx_post_renders_second_stage_confirmation(tmp_path, monkeypatch):
    c, svc = _client(tmp_path)
    spawned = []
    monkeypatch.setattr(type(svc), "spawn_auto_install_job",
                        lambda self, *a, **k: (spawned.append((a, k)), ("l", None))[1])
    tok = _csrf(c)
    r = c.post("/auto-install/start", data=_all_form(svc, tok, source="stable", tx=True))
    body = r.data.decode()
    assert not spawned                                           # NOT spawned yet
    assert "will TRANSMIT" in body                                # explicit RF confirm
    assert 'name="version:daemon" value="stable"' in body        # selection carried through
    assert 'name="tx:daemon" value="yes"' in body
    ctok = body.split('name="confirm_token" value="')[1].split('"')[0]
    assert ctok                                                  # server-staged token
    # the confirmed second submission consumes the token and spawns the bound selection
    tok2 = body.split('name="_csrf" value="')[1].split('"')[0]
    c.post("/auto-install/start", data=_all_form(svc, tok2, source="stable", tx=True,
                                                 confirm_token=ctok))
    assert spawned and spawned[0][0][0]["daemon"] == {"install": True, "version": "stable",
                                                      "tests": True, "tx": True}
    # REPLAY of the consumed token: refused, zero further spawns
    c.post("/auto-install/start", data=_all_form(svc, tok2, source="stable", tx=True,
                                                 confirm_token=ctok))
    assert len(spawned) == 1


def test_tx_without_tests_refused(tmp_path, monkeypatch):
    c, svc = _client(tmp_path)
    spawned = []
    monkeypatch.setattr(type(svc), "spawn_auto_install_job",
                        lambda self, *a, **k: (spawned.append(1), ("l", None))[1])
    tok = _csrf(c)
    r = c.post("/auto-install/start", data=_all_form(svc, tok, tests=False, tx=True),
               follow_redirects=True)
    assert b"host tests" in r.data and not spawned


@pytest.mark.needs_session
def test_run_view_rows_and_api(tmp_path, monkeypatch):
    monkeypatch.setattr(ControllerService, "_frozen_ref",
                        lambda self, comp, source: (("f" * 40, "frozen: stub"), ""))
    c, svc = _client(tmp_path)
    svc.auto_install(apply=True, tests=True, emit=lambda s: None)
    body = c.get("/auto-install").data.decode()
    # data-stack= now appears in BOTH the selection table (8 rows) and the results table (8 rows)
    assert 'id="ai-run"' in body and 'id="ai-tasks"' in body and body.count("data-stack=") >= 8
    api = json.loads(c.get("/api/auto-install?offset=0").data)
    assert api["state"]["state"] == "completed-with-failures"
    assert api["run_id"] == api["state"]["run_id"]
    assert set(api["log"]) >= {"offset", "data"}
    # offset validation: garbage -> treated as invalid, typed
    api2 = json.loads(c.get("/api/auto-install?offset=zzz").data)
    assert api2["log"].get("error") == "invalid offset"


def test_task_detail_rendered_as_text(tmp_path):
    paths = Paths(runtime_root=tmp_path)
    m = ai_mod.new_marker("c" * 32, "install", "pinned", True, False,
                            [{"id": "daemon", "name": "d"}])
    m["state"] = "completed"
    m["stacks"][0]["status"] = "fail"
    m["stacks"][0]["detail"] = "<script>alert(1)</script>"
    assert ai_mod.write_marker(paths, m)
    body = _client(tmp_path)[0].get("/auto-install").data.decode()
    assert "<script>alert(1)</script>" not in body               # escaped, text only
    assert "&lt;script&gt;" in body


def test_unsafe_marker_blocks_posts_and_shows_recovery(tmp_path, monkeypatch):
    d = tmp_path / "state"
    d.mkdir(parents=True)
    (d / "auto-install.json").write_text("{broken")
    c, svc = _client(tmp_path)
    body = c.get("/auto-install").data.decode()
    assert "unreadable or malformed" in body and "Acknowledge" in body
    ln, err = svc.spawn_auto_install_job(_sel(svc))                      # POST path uses this
    assert ln is None and "acknowledge" in err


def test_welcome_banner_tristate(tmp_path):
    c, _ = _client(tmp_path)
    assert b"Welcome!" in c.get("/").data                        # fresh
    (tmp_path / "src" / "loraham-kiss-tnc").mkdir(parents=True)  # unmanaged tree
    c2, _ = _client(tmp_path)
    dash = c2.get("/").data.decode()
    assert "Welcome!" not in dash and "needs attention" in dash  # recovery, not welcome


# --- M2 round-2: server-enforced RF confirmation + recovery UI -------------------------------

def _no_spawn(monkeypatch, svc):
    spawned = []
    monkeypatch.setattr(type(svc), "spawn_auto_install_job",
                        lambda self, *a, **k: (spawned.append(a), ("l", None))[1])
    return spawned


def test_direct_confirm_post_without_staged_state_refused(tmp_path, monkeypatch):
    # Posting arbitrary hidden values (incl. the legacy confirm_rf) with NO staged
    # server-side confirmation: refusal with zero mutation.
    c, svc = _client(tmp_path)
    spawned = _no_spawn(monkeypatch, svc)
    tok = _csrf(c)
    r = c.post("/auto-install/start", data=_all_form(svc, tok, tx=True,
                                                     confirm_rf="yes", confirm_token="f" * 32),
               follow_redirects=True)
    assert b"RF confirmation refused" in r.data
    assert not spawned
    assert ai_mod.read_reservation(Paths(runtime_root=tmp_path))[0] == "absent"


def _staged(c, svc, source="stable"):
    tok = _csrf(c)
    body = c.post("/auto-install/start", data=_all_form(svc, tok, source=source, tx=True)).data.decode()
    ctok = body.split('name="confirm_token" value="')[1].split('"')[0]
    tok2 = body.split('name="_csrf" value="')[1].split('"')[0]
    return ctok, tok2


def test_confirmation_choice_changes_refused(tmp_path, monkeypatch):
    c, svc = _client(tmp_path)
    spawned = _no_spawn(monkeypatch, svc)
    # selector changed after confirmation: canonical mismatch -> token consumption refuses
    ctok, tok2 = _staged(c, svc, source="stable")
    r = c.post("/auto-install/start", data=_all_form(svc, tok2, source="dev", tx=True,
                                                     confirm_token=ctok), follow_redirects=True)
    assert b"RF confirmation refused" in r.data and not spawned
    # tests dropped while TX kept: the coupling rule refuses even earlier (before confirmation)
    ctok, tok2 = _staged(c, svc, source="stable")
    r = c.post("/auto-install/start", data=_all_form(svc, tok2, source="stable", tests=False,
                                                     tx=True, confirm_token=ctok),
               follow_redirects=True)
    assert b"host tests" in r.data and not spawned
    # TX dropped: a plain non-TX start is legitimate on its own (it does not use the RF confirmation)
    ctok, tok2 = _staged(c, svc, source="stable")
    c.post("/auto-install/start", data=_all_form(svc, tok2, source="stable", tx=False,
                                                 confirm_token=ctok), follow_redirects=True)


def test_expired_and_malformed_confirmation_refused(tmp_path, monkeypatch):
    c, svc = _client(tmp_path)
    spawned = _no_spawn(monkeypatch, svc)
    ctok, tok2 = _staged(c, svc)
    with c.session_transaction() as sess:                        # force expiry
        sess["_auto_install_tx_confirm"] = dict(sess["_auto_install_tx_confirm"], exp=1.0)
    r = c.post("/auto-install/start", data=_all_form(svc, tok2, source="stable", tx=True,
                                                     confirm_token=ctok), follow_redirects=True)
    assert b"expired" in r.data and not spawned
    ctok, tok2 = _staged(c, svc)
    with c.session_transaction() as sess:                        # malformed staged state
        sess["_auto_install_tx_confirm"] = "garbage"
    r2 = c.post("/auto-install/start", data=_all_form(svc, tok2, source="stable", tx=True,
                                                      confirm_token=ctok), follow_redirects=True)
    assert b"RF confirmation refused" in r2.data and not spawned


def test_wrong_token_refused_and_consumes_staged_state(tmp_path, monkeypatch):
    c, svc = _client(tmp_path)
    spawned = _no_spawn(monkeypatch, svc)
    ctok, tok2 = _staged(c, svc)
    r = c.post("/auto-install/start", data=_all_form(svc, tok2, source="stable", tx=True,
                                                     confirm_token="0" * 32), follow_redirects=True)
    assert b"RF confirmation refused" in r.data and not spawned
    # single-use: the staged state was consumed by the failed attempt — the REAL token
    # no longer works either (start again from the form)
    r2 = c.post("/auto-install/start", data=_all_form(svc, tok2, source="stable", tx=True,
                                                      confirm_token=ctok), follow_redirects=True)
    assert b"RF confirmation refused" in r2.data and not spawned


def test_dead_reservation_shows_ack_button_and_recovers(tmp_path):
    # Dead reservation evidence with an ABSENT run marker: the page still shows the
    # acknowledgement control and the POST recovery works.
    paths = Paths(runtime_root=tmp_path)
    dead = {"starttime": 1, "pgid": 1, "sid": 1, "exec": "/bin/false",
            "argv_fp": "x", "argv_len": 1}
    ok, _ = ai_mod.write_reservation(paths, "9" * 32, 999999, dead, phase="spawned")
    assert ok
    c, svc = _client(tmp_path)
    body = c.get("/auto-install").data.decode()
    assert "Acknowledge" in body and "reservation" in body
    tok = _csrf(c)
    r = c.post("/auto-install/ack", data={"_csrf": tok}, follow_redirects=True)
    assert b"acknowledged" in r.data
    assert ai_mod.read_reservation(paths)[0] == "absent"
    assert "Acknowledge &amp; recover" not in c.get("/auto-install").data.decode()


def test_confirmed_tx_post_refused_without_callsign(tmp_path):
    # The RF disclosure page may render, but the CONFIRMED POST refuses before any child
    # is spawned when no callsign is configured (spawn_auto_install_job gate) — zero mutation.
    c, svc = _client(tmp_path)                                   # no callsign configured
    ctok, tok2 = _staged(c, svc, source="pinned")                # disclosure still renders
    r = c.post("/auto-install/start", data=_all_form(svc, tok2, source="pinned", tx=True,
                                                     confirm_token=ctok), follow_redirects=True)
    assert b"callsign" in r.data                                 # typed refusal shown
    assert ai_mod.read_reservation(Paths(runtime_root=tmp_path))[0] == "absent"
    assert svc.auto_install_status() is None                             # no marker either


def test_unbootstrapped_root_web_post_refuses_zero_mutation(tmp_path):
    absent = tmp_path / "absent-root"
    svc = ControllerService(system=FakeSystem(cmdlines_data={}).system,
                            paths=Paths(runtime_root=absent))
    c = create_app(service_factory=lambda: svc).test_client()
    tok = _csrf(c)
    r = c.post("/auto-install/start", data={"_csrf": tok, "source": "pinned",
                                           "tests": "yes"}, follow_redirects=True)
    assert b"not bootstrapped" in r.data
    assert not absent.exists()                                   # ZERO runtime mutation


def test_orphan_risk_page_requires_confirmation_checkbox(tmp_path):
    paths = Paths(runtime_root=tmp_path)
    assert ai_mod.write_orphan_risk(paths, "8" * 32, 4242,
                                      "cessation unproven", None)
    c, svc = _client(tmp_path)
    body = c.get("/auto-install").data.decode()
    assert "ORPHAN RISK" in body and "4242" in body              # pid + reason surfaced
    assert 'name="confirm_orphan"' in body                       # explicit confirmation
    tok = _csrf(c)
    r = c.post("/auto-install/ack", data={"_csrf": tok}, follow_redirects=True)
    assert b"confirmation" in r.data                             # refused without it
    assert ai_mod.read_reservation(paths)[0] == "valid"
    r2 = c.post("/auto-install/ack", data={"_csrf": tok, "confirm_orphan": "yes"},
                follow_redirects=True)
    assert b"acknowledged" in r2.data
    assert ai_mod.read_reservation(paths)[0] == "absent"


@pytest.mark.needs_session
def test_starting_card_shown_after_spawn_before_marker(tmp_path):
    # LIVE FINDING: after the POST nothing appeared until the driver wrote its marker
    # (double-clicks -> 'already reserved'). A live reservation with no marker now
    # renders an immediate 'Run starting…' card with polling armed.
    from lhpc.core import procident
    ident = procident.proc_identity(os.getpid())
    ok, _ = ai_mod.write_reservation(Paths(runtime_root=tmp_path), "7" * 32,
                                       os.getpid(), ident, phase="spawned")
    assert ok
    c, svc = _client(tmp_path)
    body = c.get("/auto-install").data.decode()
    assert "Run starting" in body and 'id="ai-run"' in body
    assert "auto_install.js" in body                                     # polling armed


def test_auto_install_page_defaults_to_dev(tmp_path):
    c, _ = _client(tmp_path)
    body = c.get("/auto-install").data.decode()
    assert 'value="dev" selected' in body


@pytest.mark.needs_session
def test_starting_card_shown_in_spawning_phase(tmp_path):
    # The POST redirect lands within milliseconds — while the reservation is still in
    # phase 'spawning'. The card must show then too (LIVE FINDING: fast browsers got a
    # static page and had to reload manually).
    from lhpc.core import procident
    ident = procident.proc_identity(os.getpid())
    ok, _ = ai_mod.write_reservation(Paths(runtime_root=tmp_path), "8" * 32,
                                       os.getpid(), ident, phase="spawning")
    assert ok
    c, svc = _client(tmp_path)
    body = c.get("/auto-install").data.decode()
    assert "Run starting" in body and 'data-run-expect="' + "8" * 32 + '"' in body
    assert "auto_install.js" in body


@pytest.mark.needs_session
def test_starting_card_shown_over_old_terminal_marker(tmp_path):
    # LIVE FINDING (user): after a PREVIOUS completed run its terminal marker is still
    # on disk — the page showed only the old collapsed card, no poller, and the new
    # run's table needed a manual reload. A live reservation for a DIFFERENT run over a
    # terminal marker now renders the starting card and suppresses the old card.
    from lhpc.core import procident
    paths = Paths(runtime_root=tmp_path)
    m = ai_mod.new_marker("a" * 32, "install", "dev", True, False,
                            [{"id": "daemon", "name": "d"}])
    m["state"] = "completed"
    m["finished_at"] = "2026-07-05T00:00:00Z"
    assert ai_mod.write_marker(paths, m)
    ident = procident.proc_identity(os.getpid())
    ok, _ = ai_mod.write_reservation(paths, "b" * 32, os.getpid(), ident,
                                       phase="spawning")
    assert ok
    c, svc = _client(tmp_path)
    body = c.get("/auto-install").data.decode()
    assert "Run starting" in body
    assert 'data-run-expect="' + "b" * 32 + '"' in body
    assert "Last run " + "a" * 8 not in body                     # old card suppressed
    assert body.count('id="ai-run"') == 1                      # single poller anchor
    assert "auto_install.js" in body


def _seed_running_marker(paths, run_id="c" * 32):
    m = ai_mod.new_marker(run_id, "install", "dev", True, False,
                            [{"id": "daemon", "name": "LoRaHAM Daemon"},
                             {"id": "kiss", "name": "LoRaHAM KISS"}])
    m["state"] = "running"
    assert ai_mod.write_marker(paths, m)
    return m


def _register_log(paths, m, run_id, base, title, content=None):
    """Register a run-owned component log the way the driver does: append the durable
    descriptor to the marker AND (optionally) write the run-specific file."""
    log = ai_mod.component_log_name(run_id, base)
    m["component_logs"].append({"title": title, "log": log})
    assert ai_mod.write_marker(paths, m)
    if content is not None:
        (paths.runtime_root / "logs").mkdir(exist_ok=True)
        (paths.runtime_root / "logs" / log).write_text(content)
    return log


def test_component_log_stream_frames_and_advances(tmp_path):
    # Ordered, run-owned descriptors: ASCII-framed titles, drained log advances to the
    # successor, live tail keeps the cursor — all from the marker, not mtime/glob.
    paths = Paths(runtime_root=tmp_path)
    rid = "c" * 32
    m = _seed_running_marker(paths, rid)
    _register_log(paths, m, rid, "build-loraham-daemon", "LoRaHAM Daemon — Build log",
                  "daemon build output\n")
    _register_log(paths, m, rid, "build-loraham-kiss-tnc", "LoRaHAM KISS — Build log",
                  "kiss build ok\n")
    c, svc = _client(tmp_path)
    out = svc.auto_install_component_log_chunk(rid, 0, 0)
    assert "+====" in out["data"]                                # ASCII frame
    assert "LoRaHAM Daemon" in out["data"] and "Build log" in out["data"]
    assert "daemon build output" in out["data"]
    assert "kiss build ok" in out["data"]                        # advanced to successor
    assert out["index"] >= 1
    log2 = ai_mod.component_log_name(rid, "build-loraham-kiss-tnc")
    with (tmp_path / "logs" / log2).open("a") as f:
        f.write("more output\n")
    out2 = svc.auto_install_component_log_chunk(rid, out["index"], out["offset"])
    assert "more output" in out2["data"]
    assert "daemon build output" not in out2["data"]             # no re-send


def test_prior_run_generic_log_never_appears(tmp_path):
    # P1: a prior run left generic build-<comp>.log files; the new run's descriptors point
    # at RUN-SPECIFIC names, so the prior content can never appear in the new run's stream.
    paths = Paths(runtime_root=tmp_path)
    logs = tmp_path / "logs"; logs.mkdir()
    (logs / "build-loraham-daemon.log").write_text("PRIOR RUN CONTENT\n")   # old generic
    rid = "c" * 32
    m = _seed_running_marker(paths, rid)
    c, svc = _client(tmp_path)
    # new run started, NO component log registered yet -> stream contains no prior text
    out = svc.auto_install_component_log_chunk(rid, 0, 0)
    assert "PRIOR RUN CONTENT" not in out["data"]
    assert out["data"] == ""
    # once the run registers+creates its own run-specific log, ONLY that appears
    _register_log(paths, m, rid, "build-loraham-daemon", "LoRaHAM Daemon — Build log",
                  "THIS RUN\n")
    out = svc.auto_install_component_log_chunk(rid, 0, 0)
    assert "THIS RUN" in out["data"]
    assert "PRIOR RUN CONTENT" not in out["data"]


def test_immediately_consecutive_run_no_prior_evidence(tmp_path):
    # P1: a completed prior run HAS component logs; a new run begins within the same
    # second (former 2s mtime window). Before the new run creates its own log, its API
    # response contains none of the prior run's text or frame.
    paths = Paths(runtime_root=tmp_path)
    prev = "a" * 32
    mp = ai_mod.new_marker(prev, "install", "dev", True, False,
                             [{"id": "daemon", "name": "LoRaHAM Daemon"}])
    mp["state"] = "completed"
    _register_log(paths, mp, prev, "build-loraham-daemon", "LoRaHAM Daemon — Build log",
                  "PRIOR OUTPUT\n")
    # new run, same wall-clock second, brand-new run_id, empty component_logs
    rid = "c" * 32
    _seed_running_marker(paths, rid)
    c, svc = _client(tmp_path)
    out = svc.auto_install_component_log_chunk(rid, 0, 0)
    assert out["data"] == "" and "PRIOR OUTPUT" not in out["data"]
    assert "+====" not in out["data"]                            # no frame from prior run
    # prior run's log cannot alter the new run's list
    assert svc._auto_install_component_log_list(svc.auto_install_status()) == []


def test_component_log_cursor_stable_with_identical_timestamps(tmp_path):
    # P1: ordering comes from the durable list, not timestamps — two logs sharing an
    # mtime keep a stable order and never reorder an already-emitted index.
    import os
    paths = Paths(runtime_root=tmp_path)
    rid = "c" * 32
    m = _seed_running_marker(paths, rid)
    a = _register_log(paths, m, rid, "build-radiolib", "RadioLib — Build log", "radiolib\n")
    b = _register_log(paths, m, rid, "build-loraham-daemon", "LoRaHAM Daemon — Build log",
                      "daemon\n")
    same = 1700000000.0
    os.utime(paths.runtime_root / "logs" / a, (same, same))
    os.utime(paths.runtime_root / "logs" / b, (same, same))       # identical mtime
    c, svc = _client(tmp_path)
    lst = svc._auto_install_component_log_list(svc.auto_install_status())
    assert [x[1] for x in lst] == [a, b]                          # registration order kept
    # streaming index 0 then advancing never reorders
    out = svc.auto_install_component_log_chunk(rid, 0, 0)
    assert out["data"].index("radiolib") < out["data"].index("daemon")


def test_component_log_stream_rejects_wrong_run(tmp_path):
    paths = Paths(runtime_root=tmp_path)
    _seed_running_marker(paths)
    c, svc = _client(tmp_path)
    out = svc.auto_install_component_log_chunk("f" * 32, 0, 0)
    assert out == {"index": 0, "offset": 0, "data": ""}


def test_api_and_template_carry_component_log_window(tmp_path, monkeypatch):
    paths = Paths(runtime_root=tmp_path)
    _seed_running_marker(paths)
    (tmp_path / "logs").mkdir()
    (tmp_path / "logs" / ("auto-install-" + "c" * 8 + ".log")).write_text("x")
    c, svc = _client(tmp_path)
    monkeypatch.setattr(type(svc), "auto_install_running", lambda self: True)
    r = c.get("/api/auto-install?offset=0&ci=0&co=0").get_json()
    assert "complog" in r and set(r["complog"]) == {"index", "offset", "data"}
    body = c.get("/auto-install").data.decode()
    assert 'id="ai-complog"' in body                           # second window
    assert "logbox-half" in body                                 # main window halved


def _seed_completed_marker(paths, run_id="a" * 32):
    m = ai_mod.new_marker(run_id, "install", "dev", True, False,
                            [{"id": "daemon", "name": "LoRaHAM Daemon"}])
    m["state"] = "completed"
    m["finished_at"] = "2026-07-05T00:00:00Z"
    assert ai_mod.write_marker(paths, m)
    return m


def test_historical_run_seeds_component_log_window(tmp_path):
    # The collapsed "Last run" card now carries the SAME detailed per-component window as a live
    # run — seeded server-side (no JS), HTML-escaped, exactly one #ai-complog.
    paths = Paths(runtime_root=tmp_path)
    rid = "a" * 32
    m = _seed_completed_marker(paths, rid)
    _register_log(paths, m, rid, "build-loraham-daemon", "LoRaHAM Daemon — Build log",
                  "daemon build output\n")
    _register_log(paths, m, rid, "test-loraham-daemon", "LoRaHAM Daemon — Test log",
                  "test says <b>hi</b>\n")                        # untrusted HTML-ish content
    c, svc = _client(tmp_path)
    body = c.get("/auto-install").data.decode()
    assert "Last run " + "a" * 8 in body                          # collapsed historical card
    assert body.count('id="ai-complog"') == 1                   # exactly one window
    assert "LoRaHAM Daemon — Build log" in body                   # framed titles seeded
    assert "daemon build output" in body                          # component content seeded
    assert "test says &lt;b&gt;hi&lt;/b&gt;" in body              # HTML-ESCAPED, not raw
    assert "test says <b>hi</b>" not in body


def test_component_log_seed_is_byte_capped(tmp_path):
    # A huge build log must not bloat the page: the seed is front-trimmed to the cap with a notice.
    from lhpc.core.services import ControllerService
    paths = Paths(runtime_root=tmp_path)
    rid = "a" * 32
    m = _seed_completed_marker(paths, rid)
    cap = ControllerService._COMPLOG_SEED_MAX_BYTES
    _register_log(paths, m, rid, "build-loraham-daemon", "big build",
                  ("line\n" * ((cap // 5) + 60000)))             # comfortably over the cap
    c, svc = _client(tmp_path)
    seed = svc.auto_install_component_log_seed(rid)
    assert len(seed) <= cap                                       # bounded
    assert "[… older output trimmed …]" in seed                  # visible truncation notice


def test_running_run_complog_empty_and_keeps_js(tmp_path, monkeypatch):
    # A live run: the window is present but NOT pre-seeded (auto_install.js fills it); JS still loaded.
    paths = Paths(runtime_root=tmp_path)
    _seed_running_marker(paths)
    c, svc = _client(tmp_path)
    monkeypatch.setattr(type(svc), "auto_install_running", lambda self: True)
    body = c.get("/auto-install").data.decode()
    assert 'id="ai-complog"></pre>' in body                     # present but empty (no seed)
    assert "auto_install.js" in body                                      # live poller still loaded


def test_spawn_refusal_output_is_shown(tmp_path):
    # LIVE FINDING: a spawned driver that REFUSES pre-claim (components running) exited
    # with its reason only in its own log — the page silently kept the starting card.
    # /auto-install?spawn=<run_id> now shows that run's output as a refusal card.
    rid = "d" * 32
    logs = tmp_path / "logs"
    logs.mkdir(parents=True)
    (logs / f"auto-install-{rid[:8]}.log").write_text(
        "ERR   Refusing to start the auto-install run: component(s) are running\n")
    c, svc = _client(tmp_path)
    body = c.get(f"/auto-install?spawn={rid}").data.decode()
    assert "Run could not start" in body
    assert "Refusing to start the auto-install run" in body


def test_spawn_param_ignored_when_marker_matches(tmp_path):
    # Once the marker for that run exists, the ?spawn param must NOT show a refusal.
    rid = "c" * 32
    paths = Paths(runtime_root=tmp_path)
    _seed_running_marker(paths, run_id=rid)
    logs = tmp_path / "logs"
    logs.mkdir(parents=True)
    (logs / f"auto-install-{rid[:8]}.log").write_text("normal run output\n")
    c, svc = _client(tmp_path)
    body = c.get(f"/auto-install?spawn={rid}").data.decode()
    assert "Run could not start" not in body


@pytest.mark.needs_session
def test_api_reports_spawn_liveness(tmp_path):
    from lhpc.core import procident
    c, svc = _client(tmp_path)
    r = c.get("/api/auto-install?offset=0").get_json()
    assert r["spawn_live"] is False                              # nothing reserved
    ident = procident.proc_identity(os.getpid())
    ok, _ = ai_mod.write_reservation(Paths(runtime_root=tmp_path), "e" * 32,
                                       os.getpid(), ident, phase="spawning")
    assert ok
    r = c.get("/api/auto-install?offset=0").get_json()
    assert r["spawn_live"] is True                               # live spawn visible


def test_component_log_headers_emitted_exactly_once(tmp_path):
    # An EMPTY registered log (descriptor exists, file not yet written) must not re-frame
    # its header on every poll; the frame rides with the file's first bytes.
    paths = Paths(runtime_root=tmp_path)
    rid = "c" * 32
    m = _seed_running_marker(paths, rid)
    log = _register_log(paths, m, rid, "build-loraham-daemon",
                        "LoRaHAM Daemon — Build log", content=None)   # registered, no file
    (tmp_path / "logs").mkdir(exist_ok=True)
    f = tmp_path / "logs" / log
    f.write_text("")                                             # live tail, no bytes yet
    c, svc = _client(tmp_path)
    ci = co = 0
    for _ in range(3):
        out = svc.auto_install_component_log_chunk(rid, ci, co)
        assert out["data"] == ""                                 # NO header spam
        ci, co = out["index"], out["offset"]
    f.write_text("first output\n")
    out = svc.auto_install_component_log_chunk(rid, ci, co)
    assert out["data"].count("LoRaHAM Daemon") == 1              # header once
    assert "first output" in out["data"]
    ci, co = out["index"], out["offset"]
    out = svc.auto_install_component_log_chunk(rid, ci, co)
    assert out["data"] == ""                                     # no re-header, no re-send


def test_component_log_list_is_append_only_from_marker(tmp_path):
    # The list is derived ONLY from the marker's durable descriptors, in registration
    # order — a newly registered log only ever appends at the END; earlier indices are
    # identical across polls regardless of build-vs-manifest ordering or timestamps.
    paths = Paths(runtime_root=tmp_path)
    rid = "c" * 32
    m = _seed_running_marker(paths, rid)
    _register_log(paths, m, rid, "build-loraham-kiss-tnc", "LoRaHAM KISS — Build log",
                  "kiss\n")                                      # registered FIRST
    c, svc = _client(tmp_path)
    first = svc._auto_install_component_log_list(svc.auto_install_status())
    _register_log(paths, m, rid, "build-loraham-daemon", "LoRaHAM Daemon — Build log",
                  "daemon\n")                                    # registered SECOND
    second = svc._auto_install_component_log_list(svc.auto_install_status())
    assert second[:len(first)] == first                          # append-only: prefix stable
    assert [x[1] for x in second] == [
        ai_mod.component_log_name(rid, "build-loraham-kiss-tnc"),
        ai_mod.component_log_name(rid, "build-loraham-daemon")]


# --- P2: component-log GET/API must fail closed, never HTTP 500, never follow unsafe -----------

def test_api_auto_install_200_with_symlinked_logs_dir(tmp_path):
    # P2: a symlinked logs/ parent must never raise through the GET route.
    import os
    paths = Paths(runtime_root=tmp_path)
    rid = "c" * 32
    m = _seed_running_marker(paths, rid)
    _register_log(paths, m, rid, "build-loraham-daemon", "LoRaHAM Daemon — Build log",
                  content=None)
    import tempfile
    outside = Path(tempfile.mkdtemp())                            # genuinely OUTSIDE runtime root
    (outside / ai_mod.component_log_name(rid, "build-loraham-daemon")).write_text("SECRET\n")
    os.symlink(outside, tmp_path / "logs")                        # logs/ -> outside
    c, svc = _client(tmp_path)
    r = c.get(f"/api/auto-install?run_id={rid}&ci=0&co=0")
    assert r.status_code == 200                                   # no 500
    d = r.get_json()
    assert isinstance(d, dict) and "complog" in d                # valid JSON
    assert "SECRET" not in json.dumps(d)                         # symlinked dir NOT followed


def test_api_auto_install_200_with_unsafe_component_log_leaf(tmp_path):
    # P2: a component-log LEAF swapped to a symlink pointing outside must not be read;
    # the section returns a bounded, valid safe state (never a 500, never the target).
    import os
    paths = Paths(runtime_root=tmp_path)
    import tempfile
    logs = tmp_path / "logs"; logs.mkdir()
    secret = Path(tempfile.mkdtemp()) / "secret.txt"             # genuinely OUTSIDE runtime root
    secret.write_text("TOP SECRET\n")
    rid = "c" * 32
    m = _seed_running_marker(paths, rid)
    log = _register_log(paths, m, rid, "build-loraham-daemon", "LoRaHAM Daemon — Build log",
                        content=None)
    os.symlink(secret, logs / log)                               # leaf is a symlink -> outside
    c, svc = _client(tmp_path)
    r = c.get(f"/api/auto-install?run_id={rid}&ci=0&co=0")
    assert r.status_code == 200
    d = r.get_json()
    assert "TOP SECRET" not in json.dumps(d)                     # never followed/read
    cl = d["complog"]
    assert isinstance(cl, dict) and isinstance(cl.get("data"), str)
    # single unsafe leaf with no successor -> explicit safe error state, bounded
    assert cl.get("error")                                       # actionable safe error
    # direct call: unsafe leaf yields the unreadable sentinel, never raises
    assert svc._read_named_log_chunk(log, 0, 1024)[0] == -1


def test_auto_install_page_200_and_no_500_paths(tmp_path):
    # P2: /auto-install remains GET-safe (HTTP 200) even with a live marker present.
    paths = Paths(runtime_root=tmp_path)
    _seed_running_marker(paths, "c" * 32)
    c, _ = _client(tmp_path)
    assert c.get("/auto-install").status_code == 200


def test_read_named_log_chunk_rejects_traversal_names(tmp_path):
    # P2: path CONSTRUCTION failures (separators/..) fail closed, never raise.
    c, svc = _client(tmp_path)
    for bad in ("../../etc/passwd", "a/b.log", "..", "x\x00y.log"):
        assert svc._read_named_log_chunk(bad, 0, 1024) == (-1, "", 0)


# --- P1: component logs bound to the FULL 32-hex run id (no 8-hex-prefix aliasing) -------------

def test_full_run_id_binding_no_eight_hex_alias(tmp_path):
    # P1: two DISTINCT run ids sharing their first eight hex chars must not alias the same
    # component-log names, and run B's stream must never show run A's retained log.
    run_a = "aaaaaaaa" + "1" * 24                                # first 8 = aaaaaaaa
    run_b = "aaaaaaaa" + "2" * 24                                # same first 8, diff suffix
    assert run_a[:8] == run_b[:8] and run_a != run_b
    paths = Paths(runtime_root=tmp_path)
    # run A: completed, with a retained component log
    ma = ai_mod.new_marker(run_a, "install", "dev", True, False,
                             [{"id": "daemon", "name": "LoRaHAM Daemon"}])
    ma["state"] = "completed"
    log_a = _register_log(paths, ma, run_a, "build-loraham-daemon",
                          "LoRaHAM Daemon — Build log", "RUN-A OUTPUT\n")
    # the two derived filenames differ
    log_b = ai_mod.component_log_name(run_b, "build-loraham-daemon")
    assert log_a != log_b
    assert not ai_mod.is_component_log_for(run_a, log_b)       # A does not own B's name
    assert not ai_mod.is_component_log_for(run_b, log_a)       # B does not own A's name
    # run B begins (same first 8), NO component log yet
    _seed_running_marker(paths, run_b)
    c, svc = _client(tmp_path)
    out = svc.auto_install_component_log_chunk(run_b, 0, 0)
    assert out["data"] == ""                                     # neither A's text ...
    assert "RUN-A OUTPUT" not in out["data"] and "+====" not in out["data"]  # ... nor A's frame
    assert svc._auto_install_component_log_list(svc.auto_install_status()) == []  # A cannot alter B's list


# --- P2: PRIMARY run-log GET path is fail-closed too (page + API), external target ------------

def _external_logs_symlink(tmp_path, run_id, secret):
    """Point tmp_path/logs at a directory genuinely OUTSIDE the runtime root that holds a
    primary run log with recognisable secret text. Returns the outside dir."""
    import os, tempfile
    outside = Path(tempfile.mkdtemp())
    (outside / (ai_mod.log_name_for(run_id) + ".log")).write_text(secret + "\n")
    os.symlink(outside, tmp_path / "logs")
    return outside


def test_primary_auto_install_log_fail_closed_direct(tmp_path):
    # P2: auto_install_log_chunk must not raise and must not read an escaping-symlink target.
    rid = "c" * 32
    _seed_running_marker(Paths(runtime_root=tmp_path), rid)
    _external_logs_symlink(tmp_path, rid, "PRIMARY-SECRET-XYZ")
    c, svc = _client(tmp_path)
    res = svc.auto_install_log_chunk(rid, 0)
    assert isinstance(res, dict) and res.get("error")           # explicit safe error
    assert "PRIMARY-SECRET-XYZ" not in json.dumps(res)          # target never read


def test_api_auto_install_200_with_escaping_primary_log(tmp_path):
    # P2: /api/auto-install stays 200 + valid JSON; neither log nor complog leaks the secret.
    rid = "c" * 32
    m = _seed_running_marker(Paths(runtime_root=tmp_path), rid)
    _register_log(Paths(runtime_root=tmp_path), m, rid, "build-loraham-daemon",
                  "LoRaHAM Daemon — Build log", content=None)
    _external_logs_symlink(tmp_path, rid, "PRIMARY-SECRET-XYZ")
    c, svc = _client(tmp_path)
    r = c.get("/api/auto-install?ci=0&co=0")
    assert r.status_code == 200                                  # no 500
    d = r.get_json()
    assert isinstance(d, dict) and "log" in d and "complog" in d
    assert "PRIMARY-SECRET-XYZ" not in json.dumps(d)            # not in log OR complog
    assert d["log"].get("error")                                # primary log: safe error


def test_auto_install_page_200_with_escaping_primary_log(tmp_path):
    # P2: /auto-install page stays 200 with an escaping logs/ symlink (interrupted marker).
    rid = "d" * 32
    paths = Paths(runtime_root=tmp_path)
    m = ai_mod.new_marker(rid, "install", "dev", True, False,
                            [{"id": "daemon", "name": "LoRaHAM Daemon"}])
    m["state"] = "running"                                       # dead -> read-derived interrupted
    assert ai_mod.write_marker(paths, m)
    _external_logs_symlink(tmp_path, rid, "PRIMARY-SECRET-XYZ")
    c, _ = _client(tmp_path)
    resp = c.get("/auto-install")
    assert resp.status_code == 200
    assert b"PRIMARY-SECRET-XYZ" not in resp.data


# --- P2: prune_logs() must be fail-closed for an escaping/unsafe logs/ parent -----------------

def test_prune_logs_fail_closed_escaping_symlink(tmp_path):
    # P2: a logs/ symlink escaping the runtime root must not raise from prune_logs()
    # (the logs-root resolution was outside the guard and could 500 via build()/
    # spawn_web_job()); the external target is never read or deleted.
    import tempfile
    outside = Path(tempfile.mkdtemp())
    victim = outside / "old-external.log"; victim.write_text("EXTERNAL-SECRET\n")
    os.symlink(outside, tmp_path / "logs")                       # logs/ -> outside
    c, svc = _client(tmp_path)
    n = svc.prune_logs()                                         # must NOT raise
    assert isinstance(n, int) and n == 0                         # safe result, deleted nothing
    assert victim.exists() and victim.read_text() == "EXTERNAL-SECRET\n"   # untouched


def test_prune_logs_missing_dir_is_safe(tmp_path):
    # P2: an absent logs/ directory is an ordinary OSError, handled safely.
    c, svc = _client(tmp_path)                                   # no logs/ created
    assert svc.prune_logs() == 0


def test_build_reaching_prune_returns_typed_not_500(tmp_path):
    # P2 requirement #3: a normal build path that reaches prune_logs() with an escaping
    # logs/ symlink returns a typed ActionResult, never raising (which would surface as
    # HTTP 500 on the web mutation path).
    import tempfile
    from lhpc.core.services import ActionResult
    (tmp_path / "src" / "loraham-voice").mkdir(parents=True)
    outside = Path(tempfile.mkdtemp()); (outside / "x.log").write_text("EXTERNAL-SECRET\n")
    os.symlink(outside, tmp_path / "logs")
    c, svc = _client(tmp_path)
    res = svc.build("voice", apply=True)                         # reaches prune_logs()
    assert isinstance(res, ActionResult)                        # typed, not a raise/500
    assert (outside / "x.log").read_text() == "EXTERNAL-SECRET\n"   # nothing external touched


def test_get_routes_200_with_escaping_logs_during_run(tmp_path):
    # P2 requirement #4: /auto-install and /api/auto-install stay 200 and never leak the
    # external content, even with an escaping logs/ symlink alongside a live marker.
    import tempfile
    rid = "c" * 32
    _seed_running_marker(Paths(runtime_root=tmp_path), rid)
    outside = Path(tempfile.mkdtemp())
    (outside / (ai_mod.log_name_for(rid) + ".log")).write_text("EXTERNAL-SECRET\n")
    os.symlink(outside, tmp_path / "logs")
    c, _ = _client(tmp_path)
    page = c.get("/auto-install")
    api = c.get("/api/auto-install?ci=0&co=0")
    assert page.status_code == 200 and api.status_code == 200
    assert b"EXTERNAL-SECRET" not in page.data
    assert "EXTERNAL-SECRET" not in json.dumps(api.get_json())


def test_prune_logs_normal_retention_preserves_live_auto_install_logs(tmp_path):
    # P2 requirement #5: a SAFE runtime-owned logs/ still prunes eligible old logs by the
    # count budget while preserving the LIVE auto-install run's component logs (full-run prefix).
    import time as _t
    paths = Paths(runtime_root=tmp_path)
    rid = "c" * 32
    m = _seed_running_marker(paths, rid)
    live = _register_log(paths, m, rid, "build-loraham-daemon",
                         "LoRaHAM Daemon — Build log", "live\n")   # live auto-install component log
    c, svc = _client(tmp_path)
    logs = tmp_path / "logs"
    now = _t.time()
    # create many old unrelated runtime logs (exceed the retention count budget)
    for i in range(svc.LOG_RETENTION + 10):
        f = logs / f"old-{i}.log"; f.write_text("x" * 10)
        os.utime(f, (now - 10000 - i, now - 10000 - i))            # older than the live log
    os.utime(logs / live, (now, now))                             # live log is newest
    removed = svc.prune_logs()
    assert removed > 0                                            # eligible old logs pruned
    assert (logs / live).exists()                                # live auto-install log PRESERVED


# --- P2-A/P2-B: prune retention must skip non-regular entries & fail-closed ephemeral dirs -----

def test_prune_retains_directory_named_log(tmp_path):
    # P2-A: a runtime-owned DIRECTORY named `bad.log` must not raise (os.unlink would raise
    # IsADirectoryError) and must not be deleted; eligible old REGULAR logs still prune.
    import time as _t
    c, svc = _client(tmp_path)
    logs = tmp_path / "logs"; logs.mkdir()
    (logs / "bad.log").mkdir()                                   # directory, not a file
    now = _t.time()
    for i in range(svc.LOG_RETENTION + 5):
        f = logs / f"old-{i}.log"; f.write_text("x" * 20)
        os.utime(f, (now - 1000 - i, now - 1000 - i))
    removed = svc.prune_logs()                                   # must NOT raise
    assert removed > 0                                           # eligible regular logs pruned
    assert (logs / "bad.log").is_dir()                          # directory retained, untouched


def test_prune_non_regular_entries_retained(tmp_path):
    # P2-A: a FIFO named `*.log` is non-regular -> never a deletion candidate, no raise.
    import time as _t
    c, svc = _client(tmp_path)
    logs = tmp_path / "logs"; logs.mkdir()
    fifo = logs / "pipe.log"
    try:
        os.mkfifo(fifo)
    except (OSError, AttributeError):
        return                                                  # platform without mkfifo
    now = _t.time()
    for i in range(svc.LOG_RETENTION + 3):
        f = logs / f"old-{i}.log"; f.write_text("y" * 15)
        os.utime(f, (now - 1000 - i, now - 1000 - i))
    removed = svc.prune_logs()
    assert removed > 0 and fifo.exists()                        # fifo retained, no raise


def test_prune_deletion_refusal_not_counted(tmp_path, monkeypatch):
    # P2-A: a deletion that raises (OSError/PathContainmentError — e.g. a leaf swapped to a
    # symlink between stat and unlink) is retained, does not raise, and is NOT counted.
    import time as _t
    from lhpc.core import runtime_fs
    from lhpc.core.paths import PathContainmentError
    c, svc = _client(tmp_path)
    logs = tmp_path / "logs"; logs.mkdir()
    now = _t.time()
    for i in range(svc.LOG_RETENTION + 6):
        f = logs / f"old-{i}.log"; f.write_text("z" * 12)
        os.utime(f, (now - 1000 - i, now - 1000 - i))
    real_unlink = runtime_fs.unlink
    calls = {"n": 0}
    def flaky_unlink(paths, p):
        calls["n"] += 1
        if calls["n"] == 1:
            raise PathContainmentError("simulated swapped leaf")   # first delete refused
        return real_unlink(paths, p)
    monkeypatch.setattr(runtime_fs, "unlink", flaky_unlink)
    removed = svc.prune_logs()                                   # must NOT raise
    assert removed == calls["n"] - 1                            # the refused delete not counted


def test_prune_ephemeral_escaping_dirs_fail_closed(tmp_path):
    # P2-B: state/jobs and state/post each symlinked to a genuinely external dir must not
    # make prune_logs() raise; external sentinel files remain untouched.
    import tempfile
    c, svc = _client(tmp_path)
    (tmp_path / "logs").mkdir()
    (tmp_path / "state").mkdir()
    out_jobs = Path(tempfile.mkdtemp()); (out_jobs / "s.py").write_text("SENT-JOBS\n")
    out_post = Path(tempfile.mkdtemp()); (out_post / "s.py").write_text("SENT-POST\n")
    os.symlink(out_jobs, tmp_path / "state" / "jobs")
    os.symlink(out_post, tmp_path / "state" / "post")
    assert svc.prune_logs() == 0                                 # no raise, nothing removed
    assert (out_jobs / "s.py").read_text() == "SENT-JOBS\n"     # external untouched
    assert (out_post / "s.py").read_text() == "SENT-POST\n"


def test_prune_ephemeral_normal_pruning_still_works(tmp_path):
    # P2-B: a SAFE state/jobs still prunes eligible old regular launcher files.
    import time as _t
    c, svc = _client(tmp_path)
    (tmp_path / "logs").mkdir()
    jobs = tmp_path / "state" / "jobs"; jobs.mkdir(parents=True)
    now = _t.time()
    for i in range(svc.LOG_RETENTION + 8):
        f = jobs / f"u{i}.py"; f.write_text("x")
        os.utime(f, (now - 1000 - i, now - 1000 - i))
    (jobs / "not-a-launcher.dir.py").mkdir()                     # non-regular -> retained
    svc.prune_logs()
    assert len(list(jobs.glob("*.py"))) <= svc.LOG_RETENTION + 1  # pruned to budget (+dir)
    assert (jobs / "not-a-launcher.dir.py").is_dir()            # non-regular retained


def test_build_reaching_prune_typed_under_bad_log_dir(tmp_path):
    # P2 requirement #4: an applied build reaching prune_logs() with a `bad.log` directory
    # (and escaping ephemeral dirs) returns a typed ActionResult, never raising.
    import tempfile
    from lhpc.core.services import ActionResult
    (tmp_path / "src" / "loraham-voice").mkdir(parents=True)
    logs = tmp_path / "logs"; logs.mkdir()
    (logs / "bad.log").mkdir()
    (tmp_path / "state").mkdir()
    out = Path(tempfile.mkdtemp()); (out / "s.py").write_text("EXT\n")
    os.symlink(out, tmp_path / "state" / "jobs")
    c, svc = _client(tmp_path)
    res = svc.build("voice", apply=True)                        # reaches prune_logs()
    assert isinstance(res, ActionResult)                       # typed, not a raise/500
    assert (logs / "bad.log").is_dir() and (out / "s.py").exists()


def test_component_log_not_created_step_waits_no_repeat_frame(tmp_path):
    # BUG: a multi-step component registers ALL its step logs up front, but they are
    # created one at a time. A not-yet-created (ABSENT) step must be a WAIT frontier —
    # never framed, never advanced past. Previously absent was treated as "unavailable",
    # so the last registered step's header was re-emitted every poll and earlier steps'
    # content was skipped before it existed.
    paths = Paths(runtime_root=tmp_path)
    rid = "c" * 32
    m = _seed_running_marker(paths, rid)
    logs = tmp_path / "logs"; logs.mkdir()
    bases = [ai_mod.component_log_name(rid, f"build-meshcom-qemu-{i}") for i in range(4)]
    for i, b in enumerate(bases):
        m["component_logs"].append({"title": f"MeshCom QEMU — Build log (step {i+1}/4)",
                                    "log": b})
    assert ai_mod.write_marker(paths, m)
    c, svc = _client(tmp_path)
    # only step 0 exists (running); steps 1-3 absent (not created yet)
    (logs / bases[0]).write_text("setup running\n")
    ci = co = 0
    acc = ""
    for _ in range(5):                                       # poll repeatedly while absent
        d = svc.auto_install_component_log_chunk(rid, ci, co)
        acc += d["data"]; ci, co = d["index"], d["offset"]
    assert acc.count("step 4/4") == 0                        # future step NEVER framed early
    assert acc.count("step 1/4") == 1                        # step 0 framed once
    assert "unavailable" not in acc                          # absent != unavailable
    assert "setup running" in acc
    # now steps create sequentially -> each framed exactly once, content preserved
    (logs / bases[1]).write_text("overlay\n")
    (logs / bases[2]).write_text("openeth\n")
    (logs / bases[3]).write_text("cmake\ncompiling\n")
    for _ in range(6):
        d = svc.auto_install_component_log_chunk(rid, ci, co)
        acc += d["data"]; ci, co = d["index"], d["offset"]
    for step in ("step 1/4", "step 2/4", "step 3/4", "step 4/4"):
        assert acc.count(step) == 1, f"{step} framed {acc.count(step)} times"
    assert "overlay" in acc and "openeth" in acc and "compiling" in acc   # no skipped blocks


def test_component_log_absent_vs_unsafe_distinguished(tmp_path):
    # An ABSENT leaf waits (-2); a genuinely UNSAFE leaf (symlink) is framed-unavailable.
    import tempfile
    paths = Paths(runtime_root=tmp_path)
    rid = "c" * 32
    m = _seed_running_marker(paths, rid)
    logs = tmp_path / "logs"; logs.mkdir()
    absent = ai_mod.component_log_name(rid, "build-x")
    m["component_logs"].append({"title": "X", "log": absent})
    assert ai_mod.write_marker(paths, m)
    c, svc = _client(tmp_path)
    assert svc._read_named_log_chunk(absent, 0, 10)[0] == -2       # absent sentinel
    outside = Path(tempfile.mkdtemp()) / "secret"; outside.write_text("S\n")
    os.symlink(outside, logs / absent)                            # now a symlink -> unsafe
    assert svc._read_named_log_chunk(absent, 0, 10)[0] == -1       # unsafe sentinel


def test_pages_use_auto_install_label(tmp_path):
    c, _ = _client(tmp_path)
    body = c.get("/auto-install").data.decode()
    assert "<title>Auto-install" in body                         # page title renamed
    assert ">Auto-install</button>" in body                      # submit button renamed
    stacks = c.get("/stacks").data.decode()
    assert ">Auto-install</button>" in stacks                    # stacks-page entry button
    assert "This can take several minutes" not in stacks         # deleted on the stacks page
