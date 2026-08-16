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
    # S1: the real routes render under Pyodide
    for p in ("/", "/stacks", "/healthz"):
        R[p] = c.get(p).status_code
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
    # one stack per radio band: starting meshcom (433) stops a running graywolf (433)
    assert svc.start("meshcom", apply=True).ok
    assert svc.stack_running("meshcom") and svc.stack_running("graywolf") is False
    R["band_conflict"] = "meshcom start stopped graywolf on 433"
    # simulated daemon: a running 433 stack brings the 433 daemon up (READY) with live stats;
    # 868 has no running stack so its daemon stays offline (per-band).
    dv = svc.daemon_view("433")
    assert dv.reachable and dv.ready and dv.stats.get("RSSI") and dv.channel.get("FREQ")
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
    # dry run reasons about the SELECTED band, and running_band exposes it to the dashboard
    assert svc.running_band("graywolf", "") == "868" and svc._effective_band("graywolf", "") == "868"
    # dashboard PLACEMENT: graywolf + kiss render under the 868 card, not 433
    html = c.get("/").get_data(as_text=True)
    i8 = html.find('data-radio-band="868"')
    i4 = html.find('data-radio-band="433"')
    seg8 = html[i8:(html.find('data-radio-band=', i8 + 10) if i8 >= 0 else 0)]
    assert i8 >= 0 and "graywolf" in seg8.lower(), "graywolf must render under the 868 card"
    if i4 >= 0:
        seg4 = html[i4:(html.find('data-radio-band=', i4 + 10) if i4 >= 0 else 0)]
        assert "graywolf" not in seg4.lower(), "graywolf must NOT render under 433"
    R["band_select"] = "graywolf 868: kiss follows/moves to 868; dashboard places both under 868"
    R["lifecycle"] = "ok"
    R["render_with_state"] = c.get("/stacks").status_code
except Exception:
    R["FATAL"] = traceback.format_exc()[-2500:]
_report = "DEMO_BOOT " + json.dumps(R, indent=2, default=str)
print(_report)
_report  # noqa: B018  (last expression -> runPythonAsync return; not stdout)
