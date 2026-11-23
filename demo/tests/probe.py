import json
import os
import sys
import traceback

sys.path.insert(0, "/demo")
R = {}
try:
    os.environ["LHPC_SYSTEM_PROVIDER"] = "lhpc_demo.provider:build"
    os.environ["LHPC_RUNTIME_ROOT"] = "/tmp/lhpcroot"
    os.makedirs("/tmp/lhpcroot", exist_ok=True)
    from lhpc_demo.app import build_app
    app, svc = build_app()
    c = app.test_client()
    # the radio-board half of the demo's boot seed (bridge._seed): a configured dual-radio box,
    # as visitors see it. Its identity half comes after the identity gate below is checked —
    # with a node name already saved, that gate could never refuse.
    svc.set_hardware_setup("loraham")
    # S1: the real routes render under Pyodide
    for p in ("/", "/stacks", "/healthz"):
        R[p] = c.get(p).status_code
    # Every page of the built demo renders: the fixed pages, each stack row's page and lazy body, and every menu page
    # the Apps page links. A 500 is a page a visitor cannot open (0.14.0: a missing `grp` broke nine of them).
    import re as _re
    _apps = c.get("/stacks").get_data(as_text=True)
    _sids = sorted(set(_re.findall(r'id="stackrow-([A-Za-z0-9_.-]+)"', _apps)))
    _menus = sorted(set(_re.findall(r'href="(/stacks/[^"/]+/menu/[^"#?]+)', _apps)))
    assert len(_sids) == len(svc.stacks()) and _menus, (_sids, _menus)     # floor: an empty scan cannot pass
    _pages = ["/", "/stacks", "/healthz", "/stacks/menus", "/dependencies", "/hardware", "/hardware/add",
              "/hardware/radio/r1", "/hardware/radio/r2", "/logs/daemon", "/controller/logs", "/webserver/logs",
              "/firewall/logs"] + [p for s in _sids for p in (f"/stacks/{s}", f"/stacks/{s}/body")] + _menus
    _bad = {p: st for p in _pages if (st := c.get(p, follow_redirects=True).status_code) != 200}   # as the bridge
    assert not _bad, _bad
    R["pages"] = f"{len(_pages)} pages render"
    # apply gating: dry run does not mutate, apply commits (real ControllerService contract)
    assert svc.is_installed("kiss") is False
    assert svc.install("kiss", apply=False).ok and svc.is_installed("kiss") is False
    assert svc.install("kiss", apply=True).ok and svc.is_installed("kiss") is True
    assert svc.build("kiss", apply=True).ok and svc.start("kiss", apply=True).ok
    assert svc.stack_running("kiss") is True
    assert all(svc.is_built(x) for x in (getattr(svc.stack("kiss"), "components", []) or []))
    # run-state surfaces through build_snapshot() (what the dashboard reads), not just the predicate
    from lhpc.core.model import RunState
    _kiss = svc.build_snapshot().stack("kiss")
    assert any(cs.run_state == RunState.RUNNING for cs in _kiss.components.values())
    assert svc.stop("kiss", apply=True).ok and svc.stack_running("kiss") is False
    assert svc.restart("chat", apply=True).ok is False   # never installed -> guarded

    # DEFAULT demo state: seed_all_installed -> every stack installed+built, none running,
    # and mandatory dependencies satisfied (only GUI deps may remain missing).
    svc.seed_all_installed()
    ids = [s.id for s in svc.stacks() if s.id != "loraham-pi-control"]
    assert ids and all(svc.is_installed(i) for i in ids)
    assert all(svc.is_built(c2) for i in ids for c2 in (getattr(svc.stack(i), "components", []) or []))
    assert not any(svc.stack_running(i) for i in ids)      # installed, none running
    dov = svc.dependency_overview()
    assert dov["mandatory_missing"] == 0 and dov["optional_missing"] == 0
    R["default_state"] = f"{len(ids)} installed, 0 running, {dov['mandatory_missing']} mandatory-missing"
    # The PRODUCTION identity gate: without a node name (Meshtastic and MeshCore never inherit the
    # callsign) the start is refused with the fields the web sends the visitor to; the demo's own
    # boot seed configures the box so every stack starts.
    _plan = svc.run_action("start", "meshtastic", apply=False)
    assert _plan.ok is False and _plan.data.get("enforce_fields"), _plan.summary
    assert svc.start("meshtastic", apply=True).ok is False and not svc.stack_running("meshtastic")
    assert svc.restart("meshtastic", apply=True).ok is False and not svc.stack_running("meshtastic")
    from lhpc_demo.bridge import _seed_identities
    _seed_identities(svc)                                # the boot seed's identities (bridge._seed)
    R["identity_gate"] = "meshtastic refused without a node name"
    # An optional component is refused for RESTART exactly as for start/stop — never a
    # "Restarted meshcore" that runs the stack while the part asked for stays stopped.
    assert svc.restart("meshcore-webui", apply=True).ok is False
    assert not svc.stack_running("meshcore"), "restarting an optional part must not start its stack"
    # FULL STACK: starting graywolf auto-starts its KISS TNC dependency (a partner it reaches
    # RF through, NOT a band rival to stop), and the optional/mutually-exclusive kiss-serial
    # stays stopped — a running stack runs its REQUIRED parts, not spuriously its optional ones.
    assert svc.start("graywolf", apply=True).ok and svc.stack_running("graywolf")
    assert svc.stack_running("kiss"), "graywolf start must bring up the KISS TNC (full stack)"
    _k = svc.build_snapshot().stack("kiss").components
    assert _k["loraham-kiss-tnc"].run_state == RunState.RUNNING
    assert _k["loraham-kiss-serial"].run_state == RunState.STOPPED
    R["full_stack"] = "graywolf -> KISS TNC up; optional kiss-serial stays stopped"
    # stopping one part does NOT tear down the shared TNC (kiss survives graywolf's stop)
    assert svc.stop("graywolf", apply=True).ok and svc.stack_running("kiss")
    assert svc.start("graywolf", apply=True).ok
    # one stack per radio band, as the product: the meshcom (433) plan names the running 433
    # owners as blockers (the web asks first), an unconfirmed start takes nothing, and only the
    # confirmed one (stop_owners) stops graywolf
    _plan = svc.start("meshcom")
    assert _plan.ok and {b["holder_stack"] for b in _plan.data["blockers"]} == {"graywolf", "kiss"}
    assert svc.start("meshcom", apply=True).ok is False
    assert svc.stack_running("graywolf") and not svc.stack_running("meshcom")
    assert svc.start("meshcom", apply=True, stop_owners=True).ok
    assert svc.stack_running("meshcom") and svc.stack_running("graywolf") is False
    R["band_conflict"] = "meshcom start refused while graywolf holds 433; confirmed start stops it"
    # simulated daemon: a running 433 stack brings the 433 daemon up (READY) with live stats;
    # 868 has no running stack so its daemon stays offline (per-band).
    dv = svc.daemon_view("433")
    assert dv.reachable and dv.ready and dv.stats.get("RSSI") and dv.channel.get("LIVERSSI")
    assert not {"FREQ", "SF", "BW", "POWER"} & set(dv.channel), ("CHANNEL has no radio params (1.5.0: RADIO)", dv.channel)
    assert svc.daemon_feed("433", 10) and svc.daemon_view("868").reachable is False
    # every RX-TX Monitor key the console reads must be present (the "?"-cell fix): server-
    # rendered LIVERSSI/CADRSSI + the dash.js-polled CADSTATE/PACKETRSSI/TX/RX/TXOK/UPTIME.
    assert (dv.channel.get("LIVERSSI") and dv.channel.get("CADSTATE")
            and dv.channel.get("PACKETRSSI") and dv.status.get("TX") is not None
            and dv.status.get("CADRSSI") and dv.stats.get("RX")
            and dv.stats.get("TXOK") is not None and dv.stats.get("UPTIME") is not None), \
        "daemon monitor missing live keys"
    R["daemon_sim"] = (f"433 READY liverssi={dv.channel.get('LIVERSSI')} "
                       f"rx={dv.stats.get('RX')} cad={dv.channel.get('CADSTATE')}, 868 offline")
    # ONE DAEMON PER RADIO: free 433, then start the daemon on 868 ONLY -> 868 up, 433 stays
    # down. A single-band start must never bring the other band up.
    assert svc.stop("meshcom", apply=True).ok
    assert svc.daemon_view("433").reachable is False
    assert svc.start("daemon", apply=True, band="868").ok
    assert svc.daemon_view("868").reachable and svc.daemon_view("433").reachable is False
    R["daemon_per_radio"] = "daemon start 868 -> 868 up, 433 stays down"
    # simulated host metrics: the System box backend fills live, advancing counters
    s1 = svc.system_stats()
    assert (s1["cpu"]["cores"] and s1["mem"]["total_kb"] > 0 and s1["net"]["rx_bytes"] > 0
            and s1["disk"]["root"]["total_b"] > 0 and isinstance(s1.get("temp_mc"), int)), \
        "system_stats missing live metrics"
    R["system_sim"] = (f"cpu cores={s1['cpu']['cores']} mem={s1['mem']['total_kb']}kB "
                       f"up={int(s1['uptime_s'])}s temp={s1['temp_mc'] // 1000}C")
    # simulated GPS receiver (R1): the Monitor's /api/gps answers a live 3D fix with a sky, in
    # exactly the product's snapshot shape — before, the page sat at "loading…" for ever.
    g = json.loads(c.get("/api/gps").get_data(as_text=True))
    assert g["state"] == "3d" and g["available"] and g["lat"] and g["lon"], g
    assert g["sats_used"] and g["sats_seen"] and len(g["satellites"]) == g["sats_seen"], g
    assert any(sat["used"] for sat in g["satellites"]) and g["nmea_ok"] and g["nmea"], g
    # the product's own snapshot (it fails soft without a receiver) defines the shape
    from lhpc.core.services import ControllerService as _CS
    assert set(_CS.gps_monitor(svc)) == set(g), sorted(set(_CS.gps_monitor(svc)) ^ set(g))
    R["gps_sim"] = f"{g['label']}: {g['sats_used']} of {g['sats_seen']} satellites"
    # ROUTE-LEVEL: drive the REAL /action endpoint (CSRF + dispatch + spawn_web_job), not
    # only direct service calls — the web path is where impossible states slipped through.
    import re as _re
    svc.seed_all_installed()

    def _csrf():
        b = c.get("/stacks").get_data(as_text=True)
        m = _re.search(r'name="_csrf"\s+value="([0-9a-f]+)"', b)
        return m.group(1) if m else ""

    def _action(**form):
        form["_csrf"] = _csrf()
        return c.post("/action", data=form, follow_redirects=True)
    svc.uninstall("kiss", apply=True)
    assert _action(op="start", target="graywolf", confirmed="yes").status_code == 200
    assert svc.stack_running("graywolf") is False, "route: graywolf must NOT start without KISS"
    svc.install("kiss", apply=True)                 # installed, not built
    assert _action(op="build", target="kiss", confirmed="yes").status_code == 200
    assert svc.is_built(svc.stack("kiss").components[0]), "route: web Build must actually build"
    R["route_action"] = "POST /action: graywolf refused sans KISS; web Build mutates the model"
    # The web start asks the ONE start readiness (install_blocker); the demo answers it from its model.
    svc.seed_all_installed()
    assert _action(op="start", target="kiss@r1").status_code == 200 and svc.stack_running("kiss"), "route: kiss@r1"
    svc.stop("kiss", apply=True)
    svc._d("kiss")["built"] = False
    _page = _action(op="start", target="kiss@r1").get_data(as_text=True)
    assert "not built" in _page and not svc.stack_running("kiss"), "route: an unbuilt kiss is refused"
    svc.seed_all_installed()
    svc._d("daemon")["built"] = False                   # start() lets the daemon run unbuilt: the readiness agrees
    assert all(svc.install_blocker(x) == "" for x in svc.stack("daemon").components) \
        and svc.start("daemon", apply=False).ok, "N1: the daemon is judged alike"
    R["route_start_ready"] = "web start kiss@r1 runs; an unbuilt kiss is refused; the daemon is judged alike"
    # Restart + Clean must honour the same dependency integrity as Start/Stop, THROUGH /action.
    svc.seed_all_installed()
    svc.uninstall("kiss", apply=True)
    assert _action(op="restart", target="graywolf", confirmed="yes").status_code == 200
    assert svc.stack_running("graywolf") is False, "route: restart graywolf refused sans KISS"
    svc.seed_all_installed()
    svc.start("graywolf", apply=True)               # kiss+graywolf up
    # /action clean requires the typed confirm_text and is a purge; it must cascade dependents
    assert _action(op="clean", target="kiss", confirm_text="kiss",
                   confirmed="yes").status_code == 200
    assert svc.stack_running("graywolf") is False, "route: clean KISS cascades graywolf down"
    svc.seed_all_installed()
    svc.start("meshcom", apply=True)
    assert _action(op="clean", target="daemon", confirm_text="daemon",
                   confirmed="yes").status_code == 200
    assert svc._daemon_up_on("433") is False, "route: clean daemon takes the radio down"
    R["route_restart_clean"] = "route restart refused sans dep; route clean cascades (kiss, daemon)"
    # BAND-SWITCHABLE stacks honour the selector: starting graywolf on 868 occupies 868 (and its
    # KISS TNC follows there), and must NOT register on 433 (the "graywolf 868 starts 433" bug).
    svc.seed_all_installed()
    svc._set_dbands(set())
    assert svc.start("graywolf", apply=True, band="868").ok
    assert svc._band("graywolf") == "868" and svc._band("kiss") == "868"
    assert svc.daemon_view("868").reachable and svc.daemon_view("433").reachable is False
    # 433 -> stop -> 868 transition: the running KISS dependency MOVES onto 868 (not left on 433)
    svc.seed_all_installed()
    svc._set_dbands(set())
    svc.start("graywolf", apply=True, band="433")
    assert svc._band("kiss") == "433"
    svc.stop("graywolf", apply=True)
    svc.start("graywolf", apply=True, band="868")
    assert svc._band("graywolf") == "868" and svc._band("kiss") == "868", "KISS must move to 868"
    # dry run reasons about the SELECTED band, and launch_band exposes it to the dashboard
    assert svc.launch_band("graywolf", "") == "868" and svc._effective_band("graywolf", "") == "868"
    # dashboard PLACEMENT: graywolf + kiss render under the 868 card, not 433
    html = c.get("/").get_data(as_text=True)
    i8 = html.find('data-radio-band="868"')
    i4 = html.find('data-radio-band="433"')
    seg8 = html[i8:(html.find('data-radio-band=', i8 + 10) if i8 >= 0 else 0)]
    assert i8 >= 0 and "graywolf" in seg8.lower(), "graywolf must render under the 868 card"
    assert i4 >= 0, "the seeded dual-radio box must render a 433 card"
    seg4 = html[i4:html.find('data-radio-band=', i4 + 10)]
    assert "graywolf" not in seg4.lower(), "graywolf must NOT render under 433"
    R["band_select"] = "graywolf 868: kiss follows/moves to 868; dashboard places both under 868"
    # the demo's boot seed configures every identity the gate asks for: every stack can start
    from lhpc_demo.bridge import _seed
    _seed(svc)
    _refused = [i for i in ids if not svc.run_action("start", i, apply=False).ok]
    assert not _refused, _refused
    R["identity_seed"] = "after the demo seed every stack's start plan passes the identity gate"
    # ROUTE: an unconfirmed web start of meshcom shows the takeover confirm (its stop_owners
    # choice) and leaves graywolf on 433; the confirmed choice takes the band
    svc.seed_all_installed()
    svc._set_dbands(set())
    assert svc.start("graywolf", apply=True, band="433").ok
    _page = _action(op="start", target="meshcom@r1").get_data(as_text=True)
    assert 'name="stop_owners"' in _page, "route: the takeover must be confirmed first"
    assert svc.stack_running("graywolf") and not svc.stack_running("meshcom")
    _action(op="start", target="meshcom@r1", confirmed="yes", stop_owners="yes")
    assert svc.stack_running("meshcom") and not svc.stack_running("graywolf")
    R["route_takeover"] = "web start of meshcom asks before stopping graywolf on 433"
    # the same contract on the RESTART path: a restart of graywolf onto 433 while meshcom holds it
    # names meshcom as the blocker, an unconfirmed restart takes nothing, and only the confirmed
    # one (stop_owners) stops meshcom
    _plan = svc.restart("graywolf")
    assert _plan.ok and {b["holder_stack"] for b in _plan.data["blockers"]} == {"meshcom"}, _plan.data
    assert svc.restart("graywolf", apply=True).ok is False
    assert svc.stack_running("meshcom") and not svc.stack_running("graywolf")
    assert svc.restart("graywolf", apply=True, stop_owners=True).ok
    assert svc.stack_running("graywolf") and not svc.stack_running("meshcom")
    R["restart_takeover"] = "restart of graywolf refused while meshcom holds 433; confirmed one stops it"
    # One daemon per radio, on the CONF socket every card reads (daemon_control.read_view, the real parser) and in the
    # demo's own views: r1 (433) up after a start, r2 (868) not; a second enabled 433 radio reads offline everywhere.
    from lhpc.core import daemon_control as _dc
    from lhpc.core import radios as _radios
    svc.seed_all_installed()
    svc._set_dbands(set())
    _u = svc._system.unix
    assert not _dc.read_view(svc._system, "r1").reachable, "socket: band down -> offline"
    assert svc.start("kiss", apply=True, band="433").ok
    _v = _dc.read_view(svc._system, "r1")
    assert _v.reachable and _v.ready and "RX" in _v.stats, ("socket: r1 up", _v.status, _v.stats)
    assert not _dc.read_view(svc._system, "r2").reachable and not _dc.read_view(svc._system, "r99").reachable
    for _p in ("/run/loraham/loraconf-r1.sock", "/tmp/loraconf-r1.sock"):        # either socket dir the daemon uses
        assert _u.request(_p, b"GET STATUS\n", 1.0, 4096).startswith(b"STATUS "), _p
    assert len(_u.request("/tmp/loraconf-r1.sock", b"GET STATS\n", 1.0, 8)) == 8, "socket: the read cap"
    assert _u.request("/tmp/loraconf433.sock", b"GET STATUS\n", 1.0, 4096) == b""      # the 0.13 band socket: none
    _j = c.get("/api/daemon/r1").get_json()
    assert _j["reachable"] and _j["stats"], _j
    # Daemon 1.5.0's GET RADIO (daemon plan v17 §13): STATUS carries RADIOERR (Guard A), RADIO answers its eleven fields
    # in order, the Monitor's RADIO view gets them, and a radio param SET is confirmed by it in the daemon's print form.
    _rl = _u.request("/tmp/loraconf-r1.sock", b"GET RADIO\n", 1.0, 4096).decode()
    assert [t.split("=")[0] for t in _rl.split()] == ["RADIO", "RADIO", "MODE", *_dc._RADIO_FIELDS[2:]] \
        and _rl.endswith("\n") and "RADIOERR" in _v.status, ("GET RADIO", _rl, _v.status)
    _jr = c.get("/api/daemon/r1?radio=1").get_json()                       # the RADIO view; every other poll asks none
    assert _j["radio"] == {} and _jr["radio"] == _dc.read_radio(svc._system, "r1", _v.status) \
        and _jr["radio"]["FREQ"] == "433.775", (_j, _jr)
    assert _u.request("/tmp/loraconf-r2.sock", b"GET RADIO\n", 1.0, 4096) == b"", "GET RADIO: a down radio answers none"
    _u.request("/tmp/loraconf-r2.sock", b"SET POWER=5\n", 1.0, 64)
    assert "r2" not in svc._radio_sets, "a down daemon keeps no SET"
    for _k, _set, _shown in (("FREQ", "433.900", "433.9"), ("SYNC", "18", "0x12"), ("BW", "62.5", "62.5"),
                             ("LDRO", "auto", "AUTO"), ("POWER", "10", "10")):
        _ok, _conf, _det = _dc.apply_set(svc._system, "r1", _k, _set)
        assert (_ok, _conf) == (True, True) and c.get("/api/daemon/r1?radio=1").get_json()["radio"][_k] == _shown, (_k, _det)
    assert _dc.read_view(svc._system, "r1").status["FREQ"] == "433.9", "STATUS reports the SET FREQ too"
    assert _u.request("/tmp/loraconf-r1.sock", b"SET SF=x\n", 1.0, 64) == b"OK\n" \
        and _dc.read_radio(svc._system, "r1", _v.status)["SF"] == "12", "a value the daemon would refuse is not kept"
    svc._radio_sets.clear()
    R["get_radio"] = "1.5.0 STATUS RADIOERR, GET RADIO 11 fields, SETs confirmed by RADIO, 868 down answers none"
    _badge = lambda: _re.search(r'id="rd-badge-r1"[^>]*>([^<]*)<', c.get("/").get_data(as_text=True)).group(1)  # noqa: E731
    assert _badge() == "daemon READY", _badge()                                # the Home card
    assert _u.request("/tmp/loraconf-r1.sock", b"SET CADWAIT 1500\n", 1.0, 64) == b"OK\n"   # the stock fake's SET
    _u.send("/tmp/other.sock", b"PING\n", 1.0)
    assert _u._inner.sent[-2:] == [("/tmp/loraconf-r1.sock", b"SET CADWAIT 1500\n"), ("/tmp/other.sock", b"PING\n")]
    _u._inner.unix_errors["/tmp/other.sock"] = "refused"
    try:
        _u.request("/tmp/other.sock", b"GET STATUS\n", 1.0, 64)
        raise AssertionError("socket: a configured error must raise")
    except OSError:
        pass
    del _u._inner.unix_errors["/tmp/other.sock"]
    # The radio's instances and their processes (PLAN-DEMO2 L1) follow the same one rule: published while r1 is up,
    # gone everywhere once a second 433 radio makes it offline (a hardware-list save, no lifecycle call in between).
    def _card(rid):                         # radio `rid`'s Home card: from its id to the next radio card
        h = c.get("/").get_data(as_text=True)
        h = h[h.find(f'id="radio-{rid}"'):]
        return h[:h.find('id="radio-r', 10)] if h.find('id="radio-r', 10) > 0 else h
    _evid = lambda: "process.pids" in c.get("/stacks/daemon/body").get_data(as_text=True)  # noqa: E731
    assert 'data-stack="daemon@r1"' in _card("r1"), "instances: r1 up -> daemon@r1 on its card"
    assert 'data-stack="kiss@r1"' in _card("r1"), "instances: r1 up -> kiss@r1 on its card"
    assert _evid(), "instances: r1 up -> the daemon's process evidence (process.pids)"
    _st = _radios.load(svc._paths)
    _r3 = _radios.Radio("r3", "rfm98 433", "ch341-rfm98pw-433", "433", usb_serial="CHE1W433")
    assert _radios.save(svc._paths, (*_st.radios, _r3)) == []
    assert [r.id for r in _radios.load(svc._paths).radios] == ["r1", "r2", "r3"]
    assert not _re.search(r'data-stack="[a-z-]+@r1"', _card("r1")), "a second 433 radio: no r1 instance"
    assert not _evid(), "a second 433 radio: no process evidence"
    for _rid in ("r1", "r3"):
        assert not _dc.read_view(svc._system, _rid).reachable and not svc.daemon_view("433", _rid).reachable \
            and svc.daemon_channel("433", _rid) == {} and svc.daemon_channel_scan("433", _rid) == {} \
            and svc.daemon_feed("433", rid=_rid) == [] and svc.daemon_socket_line("433", _rid) == "", \
            ("a second 433 radio: offline everywhere", _rid)
    assert not c.get("/api/daemon/r1").get_json()["reachable"] and _badge() == "daemon offline"
    assert svc.daemon_view("433", "").reachable, "no radio: the band view, as before"
    assert _radios.save(svc._paths, _st.radios) == []                           # the seeded list back
    R["daemon_socket"] = "r1 up on its socket after a start (read_view, /api/daemon/r1, Home card); r2, r99 offline"
    # The radio a dashboard button names reaches the lifecycle (PLAN-DEMO2 L3): the console strips "@r1" and passes the
    # radio (spawn_start_job's rid, stop's inst), so "Start daemon (r1)" brings up r1 only and "Stop daemon (r1)"
    # takes down r1 only — the rendered forms' own fields, through /action.
    _rb = lambda rid: _re.search(rf'id="rd-badge-{rid}"[^>]*>([^<]*)<', c.get("/").get_data(as_text=True)).group(1)  # noqa: E731
    svc.start("daemon", apply=True, band="868")
    svc.seed_all_installed()
    assert svc._dbands() == set() and not any("band" in svc._d(i) for i in ids), "reseed: no stale per-band state"
    _action(op="start", target="daemon@r1", **{"from": "dash"})
    assert (_rb("r1"), _rb("r2")) == ("daemon READY", "daemon offline"), ("Start daemon (r1): r1 up, r2 offline",
                                                                           _rb("r1"), _rb("r2"))
    _action(op="start", target="daemon@r2", **{"from": "dash"})
    _action(op="stop", target="daemon@r1", confirmed="yes", **{"from": "dash"})          # its confirm page's Apply
    assert (_rb("r1"), _rb("r2")) == ("daemon offline", "daemon READY"), ("Stop daemon (r1): r1 down, r2 stays",
                                                                           _rb("r1"), _rb("r2"))
    _action(op="restart", target="daemon@r1", confirmed="yes", **{"from": "dash"})
    assert (_rb("r1"), _rb("r2")) == ("daemon READY", "daemon READY"), "Restart daemon (r1): r1 up again"
    # a refused start keeps the band its stack had (the band is committed only after its dependencies hold)
    svc.seed_all_installed()
    assert svc.start("graywolf", apply=True, band="433").ok and svc.stop("graywolf", apply=True).ok
    svc.uninstall("kiss", apply=True)
    assert svc.start("graywolf", apply=True, band="868").ok is False and svc._band("graywolf") == "433", \
        ("a refused start keeps the band", svc._band("graywolf"))
    # Reboot / Shut down are offered as on a box with their authorization (PLAN-DEMO2 L6); the confirm page, then the
    # confirmed POST answers simulated and nothing stops.
    _home = c.get("/").get_data(as_text=True)
    assert "Reboot…" in _home and "Shut down…" in _home, "power: Reboot / Shut down offered"
    _tok = _re.search(r'name="_csrf"\s+value="([0-9a-f]+)"', _home).group(1)
    assert 'name="confirmed"' in c.post("/power/reboot", data={"_csrf": _tok}).get_data(as_text=True), "power: confirm page"
    _model = json.dumps(svc._demo, sort_keys=True)
    _done = c.post("/power/reboot", data={"_csrf": _tok, "confirmed": "yes"}, follow_redirects=True).get_data(as_text=True)
    assert "simulated" in _done and json.dumps(svc._demo, sort_keys=True) == _model, \
        "power: the confirmed reboot is simulated, the demo box unchanged"
    R["radio_targets"] = "Start/Stop/Restart daemon (r1) act on r1 only; a refused start keeps its band"
    # P0 PARITY (PLAN-DEMO2): box H's own texts (PARITY-DEMO-v1 row #; class A rows only, no box-specific data) on the
    # demo's rendered pages while daemon@r1, kiss@r1 and graywolf@r1 run. It runs in every Pages workflow, so a later
    # product change that breaks parity fails the gate (the recurring parity sweep). A str must (True) / must not
    # (False) occur; a compiled pattern is searched. Scope "r1": only that radio's Home card; "t" / "r1t": the page / that
    # card as text (tags dropped, whitespace collapsed), for labels the markup splits.
    from lhpc.core.probes.process import matches as _matches
    svc.seed_all_installed()
    svc._set_dbands(set())
    assert svc.start("daemon", apply=True, band="433").ok and svc.start("graywolf", apply=True, band="433").ok
    _proj = svc._projection()
    assert sorted((p[0], p[1]) for p in _proj) == [("daemon", "r1"), ("graywolf", "r1"), ("kiss", "r1")], _proj
    assert len({p[4] for p in _proj}) == len(_proj), ("distinct pids", _proj)
    for _sid in ("daemon", "kiss", "graywolf"):                  # each instance exactly once on its card
        assert _card("r1").count(f'data-stack="{_sid}@r1"') == 1, f"r1 card: {_sid}@r1 exactly once"
    _ln = _u.request("/tmp/loraconf-r1.sock", b"GET STATUS\n", 1.0, 4096).decode()     # as a 1.2 daemon names itself
    _tok = dict(t.split("=", 1) for t in _ln.split()[1:])
    assert {k: _tok.get(k) for k in ("ID", "CHIPFAMILY", "HIGHPOWER")} == {"ID": "r1", "CHIPFAMILY": "SX127X",
                                                                         "HIGHPOWER": "0"} \
        and all(_tok.get(k) for k in ("FREQ", "SF", "POWER")), ("STATUS names its radio", _ln)
    _recs = svc._lifecycle().owned_inventory()[0]                # each instance once, its record pid = its process pid
    assert sorted((r["stack"], r["rid"], r["pid"]) for r in _recs) == sorted((p[0], p[1], p[4]) for p in _proj), _recs
    assert all(_matches(svc.stack(p[0]).component(p[3]).process, p[5]) for p in _proj), "argv the spec accepts"
    PARITY = [
        ("#1", "/", "r1", True, 'data-stack="daemon@r1"'),
        ("#1", "/", "r1", True, "LoRaHAM Daemon"),
        ("#2", "/", "r1", True, '<span class="pill">kiss@r1</span>'),
        ("#2", "/", "r1", True, _re.compile(r'value="restart">\s*<input type="hidden" name="target" value="kiss@r1"')),
        ("#2", "/", "r1", False, "No launch record or argv names the radio"),
        ("#3", "/", "r1", True, '<span class="pill">graywolf@r1</span>'),
        ("#17", "/hardware/radio/r1", "", True, "stop graywolf@r1 and stop kiss@r1"),
        ("#3", "/", "r1t", True, "Graywolf APRS station Web UI ↗ running"),   # the web UI pill in the row's box header
        ("#3", "/", "r1", True, '<a class="webpill" href="http://127.0.0.1:8080"'),
        ("#3", "/", "r1", True, _re.compile(r'data-stack="graywolf@r1"(?:(?!data-stack=).)*?<a class="webpill" '
                                            r'href="http://127\.0\.0\.1:8080"', _re.S)),   # in graywolf@r1's own block
        ("#2", "/", "r1t", True, "kiss: 127.0.0.1:8001"),
        ("#6", "/", "t", True, "LoRaHAM KISS TNC @r1 127.0.0.1:8001 · local"),
        ("#6", "/", "t", True, "Graywolf APRS @r1 http://127.0.0.1:8080 not proxied"),
        ("#14", "/stacks/kiss/menu/stackrow-kiss", "t", True, "127.0.0.1:8001 (tcp) — 127.0.0.1:8001: present"),
        ("#14", "/stacks/kiss/menu/stackrow-kiss", "t", True, "Instances kiss@r1 running"),
        ("#14", "/stacks/kiss/menu/stackrow-kiss", "t", True, _re.compile(r"PIDs \d+")),
        ("#14", "/stacks/daemon/menu/stackrow-daemon", "t", True, "loraconf-r1.sock (unix) — RADIO=READY"),
        ("#14", "/stacks/daemon/menu/stackrow-daemon", "t", True, "Instances daemon@r1 running"),
        ("#5", "/", "r1t", True, "RF log: daemon r1 (433) · graywolf r1"),
        ("#16", "/stacks/daemon/menu/stackrow-daemon", "t", True, "Save running daemon: permission OFF (sx127x)"),
        ("#9", "/stacks", "t", True, _re.compile(r"page › LoRaHAM daemon running binary@[0-9a-f]{9} ")),
        ("#9", "/stacks", "t", True, f"page › LoRaHAM KISS TNC running {svc.stack('kiss').main_component.source.pin_tag} "),
        ("#9", "/stacks", "t", True, _re.compile(r"page › Graywolf APRS running deb [0-9]")),
        ("#10", "/stacks/daemon/menu/stackrow-daemon", "t", True, "Installed version: current"),
        ("#10", "/stacks/daemon/menu/stackrow-daemon", "t", True, "Source / profile binary, current / installed-unvalidated"),
        ("#10", "/stacks/kiss/menu/stackrow-kiss", "t", True, "Source / profile current / installed-unvalidated"),
        ("#11", "/stacks/graywolf/menu/stackrow-graywolf", "t", False, "Rebuild available"),
        ("#11", "/stacks/graywolf/menu/stackrow-graywolf", "t", True, "Check upstream"),
        ("#12", "/stacks/daemon/menu/stackrow-daemon", "t", True, "installed radiolib source checkout (src/RadioLib)"),
        ("#15", "/stacks/kiss/menu/stackrow-kiss", "t", True, "loraham-daemon: running"),
        ("#15", "/stacks/graywolf/menu/stackrow-graywolf", "t", True, "loraham-kiss-tnc: running (433 MHz)"),
        ("#19", "/stacks", "t", True, "satisfied git · required"),
        ("#19", "/stacks", "t", True, "satisfied pip · required"),
        ("#19", "/stacks", "t", False, "LHPC_TIME_SOURCE"),
        ("#20", "/dependencies", "t", False, _re.compile(r"satisfied [^·]{1,200}· missing")),
        ("#22", "/stacks", "t", True, "identity ok"),
        ("#22", "/stacks", "t", True, "Up to date."),
        ("#22", "/stacks", "t", False, "not running from a git checkout"),
        ("#21", "/auto-install", "t", True, "Update all Stacks"),
        ("#21", "/auto-install", "t", False, "Install and Build all Stacks"),
        ("#18", "/logs/loraham-daemon?radio=r1", "t", True, "process running"),
        ("#18", "/logs/loraham-daemon?radio=r1", "t", True, "logs/start-loraham-daemon-433-r1.log"),
        ("#18", "/logs/loraham-daemon?radio=r1", "t", False, "(no log file yet)"),
        ("#18", "/logs/loraham-kiss-tnc?radio=r1", "t", True, "logs/start-loraham-kiss-tnc-433-r1.log"),
        ("#5", "/logs/loraham-daemon?job=r1/rf-daemon.log&radio=r1", "t", True, "logs/r1/rf-daemon.log"),
        ("#5", "/logs/loraham-daemon?job=r1/rf-daemon.log&radio=r1", "t", False, "(no log file yet)"),
    ]
    _seen = {}

    def _page(path, scope):
        h = _seen.setdefault(path, c.get(path, follow_redirects=True).get_data(as_text=True))
        h = _card("r1") if scope.startswith("r1") else h
        return " ".join(_re.sub(r"<[^>]+>", " ", h).split()) if scope.endswith("t") else h
    _miss = [(row, path, getattr(txt, "pattern", txt)) for row, path, scope, want, txt in PARITY
             if bool(txt.search(_page(path, scope)) if hasattr(txt, "search") else txt in _page(path, scope)) != want]
    assert not _miss, ("parity with box H", _miss)
    try:                                                           # #22: the bundled commit, as assemble.sh wrote it
        from lhpc_demo._build import SHA as _sha
    except ImportError:
        _sha = ""
    _st = " ".join(_re.sub(r"<[^>]+>", " ", _page("/stacks", "")).split())
    assert _sha and f"Commit @{_sha} {_sha}" in _st and f"@{_sha}" in _page("/", "t"), \
        ("self-update: the bundled commit on the LHPC row and in the footer", _sha)
    _chk = c.post("/self-update/check", data={"_csrf": _csrf()}, follow_redirects=True).get_data(as_text=True)
    assert "Up to date (simulated)" in _chk and "no origin URL" not in _chk, "self-update: Check for updates is simulated"
    _ai = c.get("/auto-install").get_data(as_text=True)                # #21: every stack installed, binary where offered
    _opts = {sid: _re.findall(r">([^<]+)</option>", _re.search(rf'name="version:{sid}"[^>]*>(.*?)</select>', _ai, _re.DOTALL)
                              .group(1)) for sid in ("daemon", "meshtastic", "meshcom", "kiss", "graywolf")}
    assert all("Binary (prebuilt)" in _opts[x] for x in ("daemon", "meshtastic", "meshcom")) \
        and not any("Binary (prebuilt)" in _opts[x] for x in ("kiss", "graywolf")), ("auto-install: Binary options", _opts)
    _ait = " ".join(_re.sub(r"<[^>]+>", " ", _ai).split())
    _no = [st.name for st in svc.stacks() if st.id != "loraham-pi-control" and f"{st.name} yes" not in _ait]
    assert not _no, ("auto-install: INSTALLED yes for every stack", _no)
    R["parity"] = f"{len(PARITY)} box-H rows hold"
    # A restored state (a returning visitor's localStorage) shows its running instances' logs as their starts left them.
    import shutil as _sh

    from lhpc_demo import bridge as _br
    _br.boot("")
    assert _br._svc.start("kiss", apply=True, band="433").ok
    _saved = _br.dump_state()
    _sh.rmtree(_br._svc._paths.under("logs"))
    _br.boot(_saved)
    assert "logs/start-loraham-kiss-tnc-433-r1.log" in json.loads(_br.handle("GET", "/logs/loraham-kiss-tnc?radio=r1"))["body"], \
        "restore: the running kiss@r1's start log is there again"
    # The page's lhpc_open cookie reaches the app through the bridge (it renders a remembered Apps row open); the
    # bridge's own signed CSRF cookie stays, so a CSRF-protected form posts after it (PLAN-DEMO2 v5 L14).
    _row = lambda body: _re.search(r'<details class="stackrow" id="stackrow-daemon"([^>]*)>', body).group(1)  # noqa: E731
    _b1 = json.loads(_br.handle("GET", "/stacks", "", "theme=dark; lhpc_open=daemon"))["body"]
    assert " open" in _row(_b1), "cookie: lhpc_open=daemon renders the daemon row open"
    _b2 = json.loads(_br.handle("GET", "/stacks", "", ""))["body"]
    assert " open" not in _row(_b2), "cookie: no lhpc_open, the row is closed again"
    _tokn = _re.search(r'name="_csrf"\s+value="([0-9a-f]+)"', _b2).group(1)
    _br._svc.stop("kiss", apply=True)
    _post = json.loads(_br.handle("POST", "/action", json.dumps({"_csrf": _tokn, "op": "start", "target": "kiss@r1"}), ""))
    assert _post["status"] == 200 and _br._svc.stack_running("kiss"), \
        ("cookie: a CSRF form posts after the cookie pass (the CSRF cookie survives)", _post["status"])
    # An optional part runs on its own inside its running stack (PLAN-DEMO2 L13): the card's Run and Stop; every stop
    # path of its stack clears it; a restored state keeps it.
    _txt = lambda h: " ".join(_re.sub(r"<[^>]+>", " ", h).split())  # noqa: E731
    _off, _on = "KISS serial (PTY) opt stopped logs Run", "KISS serial (PTY) opt running logs Stop"   # name, state; action last
    svc.seed_all_installed()
    _action(op="start", target="kiss@r1")
    assert _off in _txt(_card("r1")), "optional: kiss@r1 up -> serial stopped + Run"
    _action(op="start", target="loraham-kiss-serial@r1")
    assert _on in _txt(_card("r1")), "optional: Run -> serial running + Stop"
    assert "loraham-kiss-serial service · 433 MHz optional running" in _txt(
        c.get("/stacks/kiss/menu/stackrow-kiss").get_data(as_text=True)), "optional: the kiss page shows the serial running"
    _action(op="stop", target="loraham-kiss-serial@r1", confirmed="yes")
    assert _off in _txt(_card("r1")), "optional: Stop -> serial stopped + Run"
    _action(op="start", target="loraham-kiss-serial@r1")
    _action(op="stop", target="daemon@r1", confirmed="yes", **{"from": "dash"})      # a cascade stops kiss
    _action(op="start", target="kiss@r1")
    assert _off in _txt(_card("r1")), "optional: a daemon-stop cascade clears it (kiss back without it)"
    _br.boot("")
    assert _br._svc.start("kiss", apply=True, band="433").ok and _br._svc.start("loraham-kiss-serial", apply=True).ok
    _br.boot(_br.dump_state())
    _home = json.loads(_br.handle("GET", "/"))["body"]
    assert _on in _txt(_home[_home.find('id="radio-r1"'):_home.find('id="radio-r2"')]), "optional: restored, still running"
    R["lifecycle"] = "ok"
    R["render_with_state"] = c.get("/stacks").status_code
    # DEMO-LOGS (plan v3): the log windows show a fixed, anonymized copy of real box logs (lhpc_demo/fixtures).
    # P1, PRE-START on its OWN fresh runtime root (nothing the demo writes can be newer than a fixture there): every
    # (panel, source) pair of the 34 windows is in a rendered page and answers its fixture.
    import html as _html
    import ipaddress as _ip
    import os as _os
    from pathlib import Path as _Path

    import lhpc_demo as _ld
    from lhpc_demo.bridge import _seed as _seed1

    from lhpc.core import firewall as _fw
    from lhpc.core import known_bands as _kb
    from lhpc.core import rflog as _rflog
    _FX = _Path(_ld.__file__).parent / "fixtures"                 # read by path: a red-before fails at P1, not an import
    _run = "auto-install-aecd7e452f7445a385eee71bccf61a0e-"
    _prog = {"log-loraham-kiss-serial": "start-loraham-kiss-serial-433.log", "log-loraham-voice": "adopt-loraham-voice.log",
             "log-lxmd": "adopt-lxmd.log", "log-meshchat": "adopt-meshchat.log", "log-rns": "adopt-rns.log",
             "log-sideband": "adopt-sideband.log", "log-meshcom-bridge": "start-meshcom-bridge-r3.log",
             "log-meshcom-gps": "start-meshcom-gps-r3.log", "log-meshcom-qemu": "post-meshcom-qemu-81768-245314158461776.log",
             "log-meshcore-cli": "adopt-meshcore-cli.log", "log-meshcore-gps": "start-meshcore-gps-r4.log",
             "log-meshcore-node": "start-meshcore-node-r4.log", "log-meshcore-webui": "adopt-meshcore-webui.log",
             "build-loraham-chat": "build-loraham-chat.log", "build-loraham-voice": _run + "build-loraham-voice.log",
             "build-loraham-voice-cli": _run + "build-loraham-voice-cli.log", "build-lxmd": _run + "build-lxmd-3.log",
             "build-meshchat": _run + "build-meshchat-2.log", "build-meshcom-qemu": "adopt-meshcom-qemu.log",
             "build-meshcore-cli": _run + "build-meshcore-cli-2.log", "build-meshcore-node": _run + "build-meshcore-node-4.log",
             "build-meshcore-webui": _run + "build-meshcore-webui-2.log", "build-meshtastic-web": "build-meshtastic-web.log",
             "build-nomadnet": _run + "build-nomadnet-3.log", "build-openhop-repeater-src": "adopt-openhop-repeater-src.log",
             "build-rns": _run + "build-rns-6.log", "build-rns-lora-interface": "adopt-rns-lora-interface.log",
             "build-sideband": _run + "build-sideband-7.log"}
    _prev = {("controller-logs", "controller-web"): ("/controller/logs?src=web", "lhpc-web.log"),
             ("controller-logs", "controller-selfupdate"): ("/controller/logs?src=selfupdate", "lhpc-selfupdate.log"),
             ("controller-logs", "controller-boot-restore"): ("/controller/logs?src=boot-restore", "lhpc-boot-restore.log"),
             ("webserver-row--logs", "webserver-error"): ("/webserver/logs?src=error", "nginx-error.log"),
             ("webserver-row--logs", "webserver-access"): ("/webserver/logs?src=access", "nginx-access.log"),
             ("firewall-row--log", "firewall"): ("/firewall/logs", "firewall.log")}
    assert len(_prog) + len(_prev) == 34
    _root0 = _os.environ["LHPC_RUNTIME_ROOT"]
    _os.environ["LHPC_RUNTIME_ROOT"] = "/tmp/lhpcroot-p1"          # under the /tmp/lhpcroot prefix (dev redirect)
    _os.makedirs("/tmp/lhpcroot-p1", exist_ok=True)
    try:
        _app1, _svc1 = build_app()
    finally:
        _os.environ["LHPC_RUNTIME_ROOT"] = _root0
    _seed1(_svc1)
    _svc1.seed_all_installed()
    _c1 = _app1.test_client()
    _panels = {}                                                    # panel id -> [source url, ...] as rendered
    for _p in ["/stacks"] + [f"/stacks/{s}" for s in _sids]:
        _h = _c1.get(_p, follow_redirects=True).get_data(as_text=True)
        for _m in _re.finditer(r'<details class="advcfg logpanel" id="([^"]+)"[^>]*data-src="([^"]+)"(.*?)</details>', _h, _re.DOTALL):
            _panels.setdefault(_m.group(1), [_html.unescape(u) for u in
                                             [_m.group(2), *_re.findall(r'<option value="([^"]+)"', _m.group(3))]])
    for _pid, _name in _prog.items():
        assert _pid in _panels, ("demo-logs: panel not rendered", _pid)
        _j = _c1.get(_panels[_pid][0]).get_json()
        assert _j["lines"] and _j["path"].endswith("/logs/" + _name), ("demo-logs: window", _pid, _j["path"], len(_j["lines"]))
    for (_pid, _src), (_page, _name) in _prev.items():
        _url = f"/api/logpreview/{_src}"
        assert _pid in _panels and _url in _panels[_pid], ("demo-logs: preview source not rendered", _pid, _src)
        _pv = _c1.get(_url).get_json()["lines"]
        _ph = _c1.get(_page).get_data(as_text=True)
        _path = _html.unescape(_re.search(r'<p class="muted logpath">([^<]*)</p>', _ph).group(1))
        _box = _html.unescape(_re.search(r'<pre id="logbox" class="logbox">(.*?)</pre>', _ph, _re.DOTALL).group(1)).splitlines()
        assert _pv and _path.endswith("/" + _name) and _pv == _box[-30:], ("demo-logs: preview vs page", _src, _path, len(_pv))
    # Boot: the firewall fixture sits under the demo-owned prefix (app.py), never a host /run; it has no mtime in the
    # MANIFEST and the build installed it anyway.
    _fwlog = _Path(_fw.RECEIPT_PATH).parent / "firewall.log"
    assert str(_fwlog).startswith("/tmp/lhpcroot/fw/") and not _fw.RECEIPT_PATH.startswith("/run/") and _fwlog.is_file(), \
        ("demo-logs: firewall fixture outside the demo prefix", str(_fwlog))
    _man = json.loads((_FX / "MANIFEST.json").read_text())
    assert _man["firewall.log"]["mtime"] is None and _c1.get("/api/logpreview/firewall").get_json()["lines"]
    # P2 generic residue over the INSTALLED files: only documentation addresses, the placeholders, no stop class.
    _inst = {r: (_svc1._paths.under("logs", *r.split("/")[1:]) if r != "firewall.log"
                 else _Path(_fw.RECEIPT_PATH).parent / "firewall.log") for r in _man}
    assert len(_man) == 34 == len(list(_FX.rglob("*.log"))) and all(p.is_file() for p in _inst.values())
    for _r, _p in _inst.items():
        for _ln in _p.read_text(errors="replace").splitlines():
            for _m in _re.finditer(r"(?<![\w.])(\d{1,3}(?:\.\d{1,3}){3})(?![\w.])", _ln):
                _a = _m.group(1)
                _ver = (_ln[:_m.start(1)].endswith("Chrome/") and _a.endswith(".0.0.0")) or (
                    _ln.startswith("Requirement already satisfied: ") and _ln.endswith(f"({_a})"))
                assert _a in ("127.0.0.1", "0.0.0.0") or _a.startswith("192.0.2.") or _ver, ("demo-logs: IPv4", _r, _a)
            for _t in _re.findall(r"[0-9A-Fa-f:]{2,39}", _ln):
                if _t.count(":") >= 2:
                    try:
                        assert _ip.IPv6Address(_t).is_loopback, ("demo-logs: IPv6", _r, _t)
                    except _ip.AddressValueError:
                        pass
            for _rx, _what in ((r"(?i)\b(?:[0-9a-f]{2}[:-]){5}[0-9a-f]{2}\b", "MAC"),
                               (r"(?i)\b[a-z]{1,2}\d[a-z]{1,4}(?:-\d{1,2})?\b", "callsign"),
                               (r"/home/[a-z_][\w-]*", "home path"),
                               (r"-?\d{1,2}\.\d{4,}\s*[,; /]\s*-?\d{1,3}\.\d{4,}", "decimal lat/lon"),
                               (r"(?i)\b(lat|lon|lng|latitude|longitude)\b\s*[=:\"]", "lat/lon key"),
                               (r"\$G[PNL][A-Z]{3},", "NMEA"), (r"\b[A-R]{2}\d{2}[a-x]{2}\b", "grid locator"),
                               (r"[\w.+-]+@[\w-]+\.[A-Za-z]{2,}", "e-mail"),
                               (r"(?i)\b(ssid|serial(?:number|_no)?|cookie|set-cookie|authorization|bearer|passw\w*|token"
                                r"|secret|api[_-]?key|psk|pin)\b\s*[=:]\s*\S+", "secret keyword"),
                               (r"(?i)[?&](key|token|auth|sig|signature|password|pass|secret|sess(?:ion)?|sid)=", "query secret"),
                               (r"(?i)/dev/serial/by-id/\S+", "USB serial")):
                for _m in _re.finditer(_rx, _ln):
                    _v = _m.group(0)
                    assert (_what == "MAC" and _v.upper().startswith("00:00:5E:00:53:")) or (
                        _what == "callsign" and _re.fullmatch(r"N0CALL(-\d{1,2})?", _v)) or (
                        _what == "home path" and _v == "/home/user"), ("demo-logs: " + _what, _r, _v[:40])
    # P3 no override, POST-START (main app): a name the demo can write for ANY radio id is no fixture's (static), and
    # after every stack started on 433 and 868 (+ its optional parts) each such file holds the demo's own lines.
    _bandless = {e.job() for e in _rflog.REGISTRY if e.job()}
    _demo_shape = lambda n: bool((_m := _re.fullmatch(r"start-[a-z0-9-]+-([0-9]+)-r\d+\.log", n)) and _kb.is_known(_m.group(1))) \
        or bool(_re.fullmatch(r"r\d+/rf-[a-z]+\.log", n)) or n in _bandless  # noqa: E731
    assert not [r for r in _man if _demo_shape(r.split("/", 1)[1] if r.startswith("logs/") else r)], "demo-logs: fixture in a demo name"
    _seed1(svc)                                     # its own state: identities + every stack installed, none running
    svc.seed_all_installed()
    for _s in svc.stacks():
        if _s.id in ("daemon", "loraham-pi-control"):
            continue
        for _b in _kb.ALL:
            if not svc.start(_s.id, apply=True, band=_b).ok:
                svc.stop("daemon", apply=True)
                svc.start(_s.id, apply=True, band=_b, stop_owners=True)
            for _cp in _s.components:
                if _cp.optional and _cp.process is not None:
                    svc.start(_cp.id, apply=True)
            svc.stop(_s.id, apply=True)
    _logs = svc._paths.under("logs")
    _written = [p for p in _logs.rglob("*.log") if _demo_shape(str(p.relative_to(_logs)))]
    # floor 36: Voice starts on 433 only and Reticulum on 868 only, so their other-band starts write no log (F34)
    assert len(_written) >= 36 and all(("[demo]" in p.read_text() or "demo pkt" in p.read_text()) for p in _written), \
        ("demo-logs: a demo-written log does not hold the demo's lines", len(_written))
    _kj = c.get("/api/logs/loraham-kiss-tnc").get_json()      # a no-radio window MAY now show the newer demo log
    assert _kj["path"].split("/logs/")[-1].startswith("start-loraham-kiss-tnc-"), _kj["path"]
    R["demo_logs"] = f"{len(_prog)} program/build + {len(_prev)} preview windows, {len(_written)} demo logs own"
except Exception:
    R["FATAL"] = traceback.format_exc()[-2500:]
_report = "DEMO_BOOT " + json.dumps(R, indent=2, default=str)
print(_report)
_report  # noqa: B018  (last expression -> runPythonAsync return; not stdout)
