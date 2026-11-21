"""DemoService — a ControllerService that SIMULATES the stack lifecycle in memory. It keeps
a per-stack model {installed, built, running} and makes the REAL console reflect it two ways:
the low-level predicates (is_installed/is_built/stack_running) read the model, and
build_snapshot() — the single source of truth the dashboard/apps use for run-state — is
post-processed so Start/Stop are actually visible. No git, gcc, processes, or hardware;
everything else (rendering, config, CSRF, routing) is the unmodified lhpc app."""
from __future__ import annotations

import re

from lhpc.core.service_base import ActionResult
from lhpc.core.services import ControllerService


class _DaemonSocket:
    """The demo daemons' CONF sockets: GET STATUS / GET STATS on a radio's loraconf-<rid>.sock (under /run/loraham or
    /tmp) answer daemon_sim's line while that radio's daemon is up (DemoService._radio_up), else nothing, as a socket
    nobody serves. Every other request (SET included) and send() go to the stock fake unchanged."""
    _PATH = re.compile(r"/(?:run/loraham|tmp)/loraconf-(r[0-9]+)\.sock")

    def __init__(self, svc, inner):
        self._svc, self._inner = svc, inner

    def request(self, path, payload, timeout, max_bytes):
        m = self._PATH.fullmatch(path)
        if not (m and payload in (b"GET STATUS\n", b"GET STATS\n")):
            return self._inner.request(path, payload, timeout, max_bytes)
        band = self._svc._radio_up(m.group(1))
        if not band:
            return b""
        from . import daemon_sim
        key = payload.split()[1].decode()
        fields = self._svc._sim_status(band, m.group(1)) if key == "STATUS" else daemon_sim.stats(band)
        return f"{key} {' '.join(f'{k}={v}' for k, v in fields.items())}\n".encode()[:max_bytes]

    def send(self, path, payload, timeout):
        return self._inner.send(path, payload, timeout)


class _DemoProcfs:
    """The processes a box would show for the demo's model (DemoService._projection), computed at each read, so they
    follow every start, stop, reset, restore and hardware-list save without a hook: their argv and the TCP listeners
    they own (inode -> pid). Everything else: the stock fake."""

    def __init__(self, svc, inner):
        self._svc, self._inner = svc, inner

    def cmdlines(self):
        return {**self._inner.cmdlines(), **{p[4]: p[5] for p in self._svc._projection()}}

    def _owned(self) -> dict:
        from lhpc.core.probes.backends import Listener
        out = {}
        for p in self._svc._projection():
            for i, (kind, addr) in enumerate(p[6]):
                if kind == "tcp":
                    ip, _, port = addr.rpartition(":")
                    out[Listener("ipv4", ip, int(port), p[4] * 16 + i)] = p[4]
        return out

    def tcp_listeners(self):
        return [*self._inner.tcp_listeners(), *self._owned()]

    def owner_pid(self, inode, budget_s):
        return next(((pid, False) for li, pid in self._owned().items() if li.inode == inode), None) \
            or self._inner.owner_pid(inode, budget_s)

    def __getattr__(self, name):
        return getattr(self._inner, name)


class _DemoFs:
    """The demo's filesystem as the console probes it: a projected process's unix socket exists while it runs (the
    daemon's loraconf-<rid>.sock; probes.unixsock.probe_socket reads `exists` then `is_socket`). Everything else: the
    stock fake."""

    def __init__(self, svc, inner):
        self._svc, self._inner = svc, inner

    def _sock(self, path) -> bool:
        return any(k == "unix" and a == path for p in self._svc._projection() for k, a in p[6])

    def exists(self, path):
        return self._sock(path) or self._inner.exists(path)

    def is_socket(self, path):
        return self._sock(path) or self._inner.is_socket(path)

    def __getattr__(self, name):
        return getattr(self._inner, name)


_KEYS = ("installed", "built", "running")


class DemoService(ControllerService):
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self._system.unix = _DaemonSocket(self, self._system.unix)    # the cards read the daemon on its socket
        self._system.procfs = _DemoProcfs(self, self._system.procfs)  # ... its instances' processes + listeners
        self._system.fs = _DemoFs(self, self._system.fs)              # ... and their unix sockets
        self._demo: dict[str, dict] = {}
        self._comp2stack: dict[str, str] | None = None

    # --- model access (robust to untrusted/partial localStorage state) -----------------
    def _d(self, sid) -> dict:
        d = self._demo.get(str(sid))
        if not isinstance(d, dict):
            d = {}
            self._demo[str(sid)] = d
        for k in _KEYS:
            if not isinstance(d.get(k), bool):
                d[k] = False
        if not (isinstance(d.get("opt"), list) and all(isinstance(x, str) for x in d["opt"])):
            d["opt"] = []                            # the optional parts it runs (a restored state is checked too)
        return d

    def _mark_stopped(self, sid) -> None:
        """THE stop transition of a stack: not running, and none of its optional parts either (a later start brings
        only its required ones back). Every stop path uses it: stop, cascades, takeovers, clean, uninstall."""
        d = self._d(sid)
        d["running"], d["opt"] = False, []

    def _opt_running(self, sid, cid) -> bool:
        """Optional part `cid` of stack `sid` runs: started on its own (Run) while its stack runs."""
        d, comp = self._d(sid), self.stack(sid).component(cid) if self.stack(sid) else None
        return bool(d["running"] and comp is not None and comp.optional and cid in d["opt"])

    def seed_all_installed(self) -> None:
        """Default demo state: every stack installed + built, none running — so a visitor
        lands on a fully set-up box (like a provisioned Codespace) and just starts/stops."""
        try:
            for s in self.stacks():
                if s.id == "loraham-pi-control":
                    continue
                d = self._d(s.id)
                d["installed"], d["built"], d["running"], d["opt"] = True, True, False, []
                d.pop("band", None)                  # no stale per-band state: nothing runs, nothing is placed
            self._set_dbands(set())
        except Exception:
            pass

    def _comp_map(self) -> dict:
        if self._comp2stack is None:
            self._comp2stack = {}
            try:
                for s in self.stacks():
                    for c in getattr(s, "components", []) or []:
                        cid = getattr(c, "id", None)
                        if cid:
                            self._comp2stack[str(cid)] = s.id
            except Exception:
                pass
        return self._comp2stack

    def _sid(self, target) -> str:
        """Resolve a stack id OR a component id (or a component object) to its stack id."""
        cid = getattr(target, "id", target)
        if cid is None:
            return ""
        if self.stack(cid) is not None:      # already a stack id
            return str(cid)
        return self._comp_map().get(str(cid), str(cid))

    # --- dependencies: present a satisfied box. Mark every NON-GUI system dependency
    #     satisfied (mandatory + runtime), leaving GUI-only deps as reported, so the
    #     dashboard banner is green and install/build are never dep-blocked. --------------
    def system_deps(self, target: str):
        deps = super().system_deps(target)
        for d in deps:
            if not d.get("gui"):
                d["satisfied"] = True
        return deps

    def dependency_overview(self):
        ov = super().dependency_overview()
        secs = ov.get("sections", [])
        for sec in secs:
            for d in sec.get("deps", []):
                if not d.get("gui"):
                    d["satisfied"] = True
        ov["mandatory_missing"] = sum(1 for s in secs for d in s["deps"]
                                      if not d["satisfied"] and d.get("mandatory"))
        ov["optional_missing"] = sum(1 for s in secs for d in s["deps"]
                                     if not d["satisfied"] and not d.get("mandatory")
                                     and not d.get("gui"))
        return ov

    # --- state predicates: read the demo model -----------------------------------------
    def is_installed(self, target: str) -> bool:
        return self._d(self._sid(target))["installed"]

    def stack_running(self, target: str) -> bool:
        return self._d(self._sid(target))["running"]

    def is_built(self, comp) -> bool:
        return self._d(self._sid(comp))["built"]

    def install_blocker(self, comp) -> str:
        """The ONE start readiness (the web start gate, the stack page, the radio cards) from the demo model, so every
        start reader agrees with start() below: not installed, or not built (the daemon starts unbuilt there too)."""
        sid = self._sid(comp)
        if not self.is_installed(sid):
            return f"not installed — run: lhpc install {sid}"
        if sid != "daemon" and not self.is_built(comp):
            return f"not built — run: lhpc build {sid}"
        return ""

    # --- run-state is rendered from build_snapshot(); reflect the model there so Start/Stop
    #     are visible everywhere the console reads run_state (dashboard, apps, dash poll). --
    def build_snapshot(self, *a, **k):
        from lhpc.core.model import ProfileState, RunState, SourceState
        snap = super().build_snapshot(*a, **k)
        for ss in snap.stacks:
            d = self._d(ss.stack.id)
            for cid, cs in ss.components.items():
                comp = ss.stack.component(cid)
                if d["installed"]:
                    # installed -> source present (drives radio_overview's daemon_installed
                    # and every source_state-based "installed" check the dashboard reads), at the version a box's
                    # install leaves (the Apps row and Info show it).
                    cs.source_state = SourceState.MATCH
                    if comp is not None and comp.source is not None:
                        cs.source_state, cs.source_version = self._installed_version(ss.stack.id, comp)
                        cs.source_head, cs.profile_state = comp.source.pin_commit, ProfileState.INSTALLED_UNVALIDATED
                if cs.run_state == RunState.NOT_APPLICABLE:
                    continue                 # libraries/firmware have no run state
                optional = bool(getattr(comp, "optional", False)) if comp else False
                if not d["installed"]:
                    cs.run_state = RunState.NOT_INSTALLED
                elif d["running"] and (not optional or self._opt_running(ss.stack.id, cid)):
                    # A running stack runs its REQUIRED components. Optional parts are opt-in
                    # extras (e.g. the mutually-exclusive kiss-serial, or meshcom's GPS) and
                    # stay stopped — the demo has no per-component control, so it must not show
                    # them spuriously running as part of the stack.
                    cs.run_state = RunState.RUNNING
                else:
                    cs.run_state = RunState.STOPPED
        return snap

    # --- simulated daemon: make the radio panels LIVE. lhpc runs ONE daemon process PER
    #     radio (--radio 433 / --radio 868), so the daemon serves a band when it was started
    #     for THAT band, or when a stack running on that band auto-started it — never both
    #     bands from a single start. The per-band running set lives in the daemon model dict
    #     (`bands`) so it persists through localStorage like the rest of the demo state. -----
    def _dbands(self) -> set:
        raw = self._d("daemon").get("bands")
        return {b for b in raw if b in ("433", "868")} if isinstance(raw, list) else set()

    def _set_dbands(self, bands) -> None:
        d = self._d("daemon")
        d["bands"] = sorted(b for b in bands if b in ("433", "868"))
        d["running"] = bool(d["bands"])          # daemon stack row reflects "any band up"

    def _req_band(self, k) -> str:
        """The band a lifecycle call targets: its `band`, else its instance's radio (`inst`, the web's `<sid>@<rid>`,
        whose `@rid` the console strips before the call) by the hardware list; "" for neither."""
        from lhpc.core import radios
        b = str((k or {}).get("band") or "")
        rid = getattr((k or {}).get("inst"), "rid", "") or ""
        return b if b in ("433", "868") else next((r.band for r in radios.load(self._paths).radios
                                                  if rid and r.id == rid), "")

    def _daemon_start_bands(self, k) -> list:
        """Which bands a daemon start/restart affects: the explicit `band` if given, else
        every configured radio-mode band (a bandless start serves the whole configured set —
        matching real dual/single mode), falling back to both."""
        b = self._req_band(k)
        if b in ("433", "868"):
            return [b]
        try:
            bands = [x for x in self.active_bands() if x in ("433", "868")]
        except Exception:
            bands = []
        return bands or ["433", "868"]

    def _daemon_up_on(self, band: str) -> bool:
        if band not in ("433", "868"):
            return False
        if band in self._dbands():
            return True
        return any(self._d(o)["running"] and self._band(o) == band
                   for o in list(self._demo) if o != "daemon")

    def _radio_up(self, rid: str) -> str:
        """The band of radio `rid` while its daemon is up, else "": ONE rule for every reader (the CONF socket, and
        daemon_view / daemon_channel / daemon_channel_scan on a radio). An enabled radio of the hardware list, the only
        enabled one of its band, whose band the model has up. The demo models one daemon per band, so a second radio
        on a band reads offline everywhere (a demo limit)."""
        from lhpc.core import radios
        band = next((r.band for r in radios.load(self._paths).radios if r.enabled and r.id == rid), "")
        return band if band and self._band_rid(band) == rid and self._daemon_up_on(band) else ""

    def _band_rid(self, band: str) -> str:
        """The radio the demo runs `band` on: the band's only enabled radio of the hardware list, else ""."""
        from lhpc.core import radios
        on = [r.id for r in radios.load(self._paths).radios if r.enabled and r.band == band]
        return on[0] if len(on) == 1 else ""

    def _projection(self) -> list:
        """[(stack, rid, band, component, pid, argv, endpoints)] of each process a box would run now, from the model by the ONE
        radio rule: a radio's daemon while `_radio_up`, and each process component of a running stack on its band's
        radio (`_band_rid`). The procfs and the launch records the console reads are both rendered from it, at each
        read. The argv is the component's process spec (what probes.process.matches accepts) and the option of each
        start-time TCP port (the console reads a movable port from argv); the instance is named by
        the record (lhpc's own launches are recorded; a daemon has no instance argv at all). `endpoints`: its
        component's endpoints as (kind, address) rendered for its radio, the ones it serves while it runs."""
        import zlib

        from lhpc.core import commands, radios
        out = []
        for r in (r for r in radios.load(self._paths).radios if r.enabled):
            for sid in sorted(self._demo):
                stk = self.stack(sid)
                if stk is None or not (self._radio_up(r.id) == r.band if sid == "daemon" else
                                       self._d(sid)["running"] and self._band(sid) == r.band
                                       and self._band_rid(r.band) == r.id):
                    continue
                for c in stk.components:
                    if c.process is None or (c.optional and not self._opt_running(sid, c.id)):
                        continue
                    p, inst = c.process, commands.Instance(r.id)
                    argv = [p.exec_path or p.exec_name, *(("-m", p.entry) if p.entry else ()), *p.all_args,
                            *p.any_args[:1]]
                    eps = [(e.kind, inst.subst(getattr(e, "address_template", "") or e.address)) for e in c.endpoints]
                    for e, (kind, addr) in zip(c.endpoints, eps, strict=True):   # a start-time port shows in argv
                        name = getattr(getattr(e, "firewall", None), "port_param", "")
                        arg = next((q.arg for q in c.run_params if name and q.name == name and q.arg), "")
                        argv += [arg, addr.rpartition(":")[2]] if kind == "tcp" and arg else []
                    out.append((sid, r.id, r.band, c.id, 2000 + zlib.crc32(f"{sid}@{r.id}/{c.id}".encode()) % 30000,
                                argv, eps))
        return out

    def _write_logs(self) -> None:
        """The start log and RF log of each projected process, as files under logs/ by the product's own names
        (radio_start.start_log_name; the rflog registry's `<rid>/<file>`), written once and kept after a stop, as a
        box's launches leave them. The log pages tail them through runtime_fs like a box's. Called after every applied
        start/restart and after a state restore."""
        import time

        from lhpc.core import radio_start, rflog
        ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        for sid, rid, band, cid, _pid, _argv, _eps in self._projection():
            logs = {radio_start.start_log_name(cid, band, rid): [
                f"{ts} [demo] {cid} started for {sid}@{rid} on {band} MHz (simulated; no radio in the browser)",
                f"{ts} [demo] {cid}: ready"]}
            for e in self._rflog_entries():
                if e.writer == cid:
                    name = f"{rid}/{rflog.RADIO_FILE}" if e.per_radio else f"{rid}/{e.job()}" if e.instanced else e.job()
                    logs[name] = [f'{ts} RX rssi=-8{i}.00 snr=8.00 len=9 band={band} hex=64656d6f20706b74{i:02x} '
                                  f'ascii="demo pkt{i}"' for i in range(3)]
            for name, lines in logs.items():
                f = self._paths.under("logs", *name.split("/"))
                if not f.exists():
                    f.parent.mkdir(parents=True, exist_ok=True)
                    f.write_text("\n".join(lines) + "\n")

    def _lifecycle(self):
        """lhpc's Lifecycle with the launch records lhpc would have written for the projected processes, read through
        like the procfs and verified while projected: a daemon names its radio only by its record (no instance argv),
        and instances.instance_processes reads both."""
        lc = super()._lifecycle()
        lc.owned_inventory = lambda: ([{"launch_id": f"demo-{p[4]}", "stack": p[0], "rid": p[1], "band": p[2],
                                        "component": p[3], "pid": p[4], "role": "", "launched_at": 0.0, "version": 1}
                                       for p in self._projection()], [], "ok")
        lc.verify_owned = lambda rec: ((True, "verified") if any(rec.get("pid") == p[4] for p in self._projection())
                                       else (False, "process gone"))
        return lc

    def _up(self, band: str, rid: str) -> bool:
        """A radio's daemon (`rid`), or without one the band, as before."""
        return bool(self._radio_up(rid)) if rid else self._daemon_up_on(band)

    def _sim_status(self, band: str, rid: str = "") -> dict:
        """The simulated STATUS every reader shows (the CONF socket, daemon_view, the socket line): with a radio, its id
        and its saved chip family in the daemon's own CHIPFAMILY spelling (daemon_control._STATUS_FAMILY)."""
        from lhpc.core import daemon_control, radio_start

        from . import daemon_sim
        fam = (radio_start.chip_family(self, rid) or "") if rid else ""
        return daemon_sim.status(band, rid, next((k for k, v in daemon_control._STATUS_FAMILY.items() if v == fam), ""))

    def daemon_view(self, band: str, rid: str = ""):          # the core's signatures: the console passes the rid
        from lhpc.core.daemon_control import DaemonView
        if not self._up(band, rid):
            return DaemonView(band=band, reachable=False)
        from . import daemon_sim
        v = DaemonView(band=band, reachable=True)
        v.status, v.stats, v.channel = (self._sim_status(band, rid), daemon_sim.stats(band),
                                        daemon_sim.channel(band))
        return v

    def daemon_channel(self, band: str, rid: str = "") -> dict:
        """Passive channel read — the simulated counterpart of `GET CHANNEL NOSCAN`.

        The real controller no longer puts channel fields in `daemon_view()`, so the demo has to
        answer here too or the dashboard's RSSI telemetry simply vanishes in the browser. No scan
        is simulated: `CADSCAN=0` and the state says no verdict was taken, exactly as the daemon
        answers it."""
        if not self._up(band, rid):
            return {}
        from . import daemon_sim
        ch = dict(daemon_sim.channel(band))
        ch["CADSCAN"] = "0"
        ch["CAD"] = "0"
        ch["CADSTATE"] = "NOTSCANNED"
        return ch

    def daemon_channel_scan(self, band: str, rid: str = "") -> dict:
        """Operator-invoked scan — what "Scan now" calls. Here it is where the simulated CAD
        verdict actually appears, since that is the only thing a real scan buys."""
        if not self._up(band, rid):
            return {}
        from . import daemon_sim
        ch = dict(daemon_sim.channel(band))
        ch["CADSCAN"] = "1"
        ch["CAD"] = "1"
        return ch

    def daemon_feed(self, band: str, lines: int = 40, rid: str = "") -> list:
        if not self._up(band, rid):
            return []
        from . import daemon_sim
        return daemon_sim.feed(band, lines)

    def daemon_socket_line(self, band: str, rid: str = "") -> str:
        if not self._up(band, rid):
            return ""
        return "STATUS " + " ".join(f"{k}={v}" for k, v in self._sim_status(band, rid).items())

    # --- simulated GPS receiver: make the Monitor under Position LIVE. Synthetic NMEA goes through
    #     the product's own parser (gps.NmeaSnapshot), so the snapshot has the real shape. A
    #     browser has no gpsd and no serial port; the real probe would only report "no receiver".
    def _gps_sim_snapshot(self) -> dict:
        from lhpc.core import gps as _gps

        from . import gps_sim
        snap = _gps.NmeaSnapshot()
        for line in gps_sim.sentences():
            snap.feed(line)
        return snap.snapshot()

    def gps_monitor(self) -> dict:
        from lhpc.core import gps as _gps
        s = self._gps_sim_snapshot()
        out = {"source": "nmea", "resolved_source": "nmea", "available": True,
               "note": "Simulated receiver (demo): a fixed position and sky.", "error": "",
               "nmea_ok": True, "devices": [], "device": "simulated receiver (demo)"}
        out.update({k: s[k] for k in ("state", "mode", "lat", "lon", "alt", "alt_kind", "time",
                                      "time_has_date", "sats_used", "sats_seen", "satellites",
                                      "nmea")})
        out["label"] = _gps.monitor_label(out["state"])
        return out

    def gps_nmea(self) -> list[str]:
        return list(self._gps_sim_snapshot()["nmea"])

    # --- simulated clock: a box synced by systemd-timesyncd, so the product's own Time-row logic
    #     shows green "NTP" (a demo seen by new users must not show a fault it does not have). ---
    def _kernel_time_state(self):
        return {"synced": True, "maxerror_us": 20_000}

    def _time_daemons(self, fs):
        return ["systemd-timesyncd"]

    # --- simulated Wi-Fi client: the dashboard's Network row (and its signal pill) and the
    #     Network panel render the way they do on a box joined to a home network. -------------
    def network_view(self) -> dict:
        return {"supported": True, "authorized": True, "mode": "client",
                "active": {"uuid": "demo-wifi", "name": "demo-wifi", "device": "wlan0",
                           "address": "192.168.1.50/24"},
                "ethernet": {}, "stored": [], "preferred": {}, "hostname": "loraham-demo", "outcome": {},
                "scan": [], "pending": False}

    # --- simulated host metrics: make the System box LIVE. Feed synthetic RAW /proc text
    #     through the REAL parsers so the shape can never drift from the product; the browser
    #     derives rates from the growing counters between polls. -----------------------------
    def system_stats(self) -> dict:
        from lhpc.core import service_system as ss

        from . import system_sim
        out: dict = {"ts": system_sim.ts()}
        out["cpu"] = ss.parse_proc_stat(system_sim.proc_stat())
        out["load"] = ss.parse_loadavg(system_sim.loadavg())
        out["mem"] = ss.parse_meminfo(system_sim.meminfo())
        out["net"] = ss.parse_net_dev(system_sim.net_dev())
        out["wifi"] = ss.parse_proc_wireless(system_sim.proc_wireless())
        out["temp_mc"] = system_sim.temp_mc()
        out["uptime_s"] = ss.parse_uptime(system_sim.uptime())
        out["disk"] = system_sim.disk()
        out["power"] = system_sim.power()
        out["info"] = system_sim.info()
        # The clock IS real (time.time() in the browser); reuse the product's own time row so
        # local/utc/tz render, degrading gracefully when the demo fs lacks the sync sources.
        try:
            out["time"] = self._time_state(self._system.fs)
        except Exception:
            pass
        return out

    # --- auto-install and the binary channel, from the model: the demo models a Raspberry Pi 5 on Trixie (box H), whose
    #     binary target offers the manifest's prebuilt stacks; Pyodide's own platform (wasm32) is not a target. ---------
    def binary_target(self) -> str:                  # service_binary_channel.py:36 reads platform.machine()
        return "aarch64-trixie"

    def on_binary_channel(self, stack_id: str) -> bool:      # service_binary_channel.py:128 reads the binary receipt
        """An installed stack whose prebuilt binary this target is offered sits on the binary channel: the demo's install
        is the default one a box makes (binary where available, service_binary_channel default_channel). binary_covers
        and every binary gate read it."""
        return bool(self._d(stack_id)["installed"] and self.binary_available(stack_id)[0])

    def _inputs_current(self, comp) -> bool:                  # service_build.py:737 reads the build-input sidecars
        """A built component's build inputs are the ones it was built from (the demo builds nothing to drift)."""
        return self._d(self._sid(comp))["built"]

    def fetched_artifacts_present(self, stack_id: str) -> bool:     # a fetched package's tree on disk (app.py:1200)
        return self._d(self._sid(stack_id))["installed"]

    def _auto_install_stack_installed(self, st) -> bool:   # service_auto_install.py:767 reads the source leaves
        return self._d(st.id)["installed"]

    def auto_install_mode(self) -> str:              # service_auto_install.py:1693, the same three answers
        got = {self._d(st.id)["installed"] for st in self.stacks() if st.id != "loraham-pi-control"}
        return "update" if got == {True} else "install" if got <= {False} else "mixed"

    def _installed_version(self, sid, comp) -> tuple:
        """(source state, version) of an installed component, as a box's install leaves it: covered by its stack's
        prebuilt binary (binary_covers, the product's one primitive), BINARY "binary@<9 hex>"; else its
        manifest pin (the tag, else the short commit). The demo has no artifact index (no network), so the 9 hex
        are the manifest pin commit's, standing in for the artifact sha a box records (status.py:255-275)."""
        from lhpc.core.model import SourceState
        pin = comp.source
        if self.binary_covers(comp.id):
            return SourceState.BINARY, f"binary@{pin.pin_commit[:9]}"
        return SourceState.MATCH, pin.pin_tag or pin.pin_commit[:7]

    def source_check_view(self) -> dict:                      # service_maintenance.py:255, read by stackupdates.verdicts
        """The cached freshness view a box's last "Check for updates" leaves: each installed component current, on the
        head and lane key the snapshot reads (stackupdates.effective_status honours an entry only on those)."""
        import time

        from lhpc.core import stackupdates
        from lhpc.core.model import SourceState
        view, lhpc = super().source_check_view(), stackupdates.lhpc_heads(self._paths)
        comps = dict(view.get("components") or {})
        for st in self.stacks():
            for comp in (st.components if self._d(st.id)["installed"] else ()):
                if comp.source is not None:
                    binary = self._installed_version(st.id, comp)[0] == SourceState.BINARY
                    comps[comp.id] = {"status": stackupdates.UP_TO_DATE, "local_head_at_check": comp.source.pin_commit,
                                      "lane_key": stackupdates.lane_key(self, comp, binary, lhpc)}
        return {"checked_at": view.get("checked_at") or int(time.time()), "components": comps}

    def fetched_version_state(self, target: str) -> dict:     # service_maintenance.py:712: the stamp a fetch writes
        r = super().fetched_version_state(target)
        if r and not r.get("installed") and self._d(self._sid(target))["installed"]:
            r = {**r, "installed": r.get("pinned", "")}       # an installed fetched stack is at its pin
        return r

    def auto_install_welcome(self):
        # A pre-provisioned demo box is never "fresh" — suppress the first-run
        # "Nothing is installed yet" banner (it reads the real filesystem, which the demo
        # does not populate; the model is the source of truth here).
        if any(isinstance(v, dict) and v.get("installed") for v in self._demo.values()):
            return None
        return super().auto_install_welcome()

    # --- Reboot / Shut down: a box with the reboot/shutdown authorization installed offers both (box H does); the
    #     demo answers them as simulated and its box keeps running. -------------------------------------------------
    def power_supported(self, kind: str | None = None) -> bool:        # service_maintenance.py:559, read at app.py:663
        return True

    def power_action(self, kind: str, apply: bool = False, client: str = ""):
        word = {"reboot": "reboot", "poweroff": "shut down"}.get(kind, kind)
        return ActionResult(True, (f"{word.capitalize()} requested (simulated) — the demo box keeps running."
                                   if apply else f"{word.capitalize()} the box? (simulated in the demo)"))

    # --- lifecycle actions: mutate the model, honouring apply (apply=False is a dry run) --
    def install(self, stack_id=None, apply: bool = False, source: str = "pinned", **k):
        sid = self._sid(stack_id)
        if not sid:
            return ActionResult(False, "Pick a stack to install.")
        if self.stack(sid) is None:
            return self._unknown_stack(sid)
        if apply:
            self._d(sid)["installed"] = True
        return ActionResult(True, f"Installed {sid} (simulated)." if apply
                            else f"Would install {sid} (simulated).")

    def build(self, target: str, apply: bool = False, **k):
        sid = self._sid(target)
        d = self._d(sid)
        if not d["installed"]:
            return ActionResult(False, f"{sid} is not installed yet.")
        if apply:
            d["built"] = True
        return ActionResult(True, f"Built {sid} (simulated)." if apply
                            else f"Would build {sid} (simulated).")

    def _bandswitchable(self, sid) -> tuple:
        """The bands a band-switchable stack MAY run on (e.g. kiss/graywolf/meshtastic on
        ('433','868')); () for a fixed-band stack."""
        mc = getattr(self.stack(sid), "main_component", None) if self.stack(sid) else None
        return tuple(getattr(mc, "bands", None) or ()) if mc else ()

    def _band(self, sid) -> str:
        """The radio band a stack occupies (the resource that allows only ONE running stack at
        a time). A band-SWITCHABLE stack occupies the band it was STARTED with (persisted in the
        model); otherwise the main component's declared band. '' for band-less stacks."""
        rb = self._d(sid).get("band")               # the band this stack was started on
        if rb in ("433", "868"):
            return rb
        mc = getattr(self.stack(sid), "main_component", None) if self.stack(sid) else None
        if mc is None:
            return ""
        b = getattr(mc, "band", None)
        if not b:
            bands = getattr(mc, "bands", None) or []
            b = bands[0] if bands else None
        return b or ""

    def _launch_bands_of(self, stack_id: str) -> set:
        """Override the core's launch bands (the demo writes no instance records): a RUNNING
        stack's band is the one recorded in the model. launch_band and the dashboard's
        _effective_band read this to PLACE the stack under a radio, so it must agree with the
        band our lifecycle used (else graywolf shows under 433 while the daemon is up on 868)."""
        d = self._d(self._sid(stack_id))
        rb = d.get("band")
        return {rb} if (d.get("running") and rb in ("433", "868")) else set()

    def _band_owners(self, sid, band=None) -> list:
        """Running stacks that COMPETE for `band` (default: the target's band) — the ones a
        start must stop. Excludes the daemon, the target, and the target's own dependencies: a
        provider reached RF THROUGH (e.g. the KISS TNC under graywolf) shares the band but is a
        partner, not a rival (lhpc's _radio_competitors filter)."""
        band = self._band(sid) if band is None else band
        if not band:
            return []
        partners = set(self._dep_stacks(sid))
        return [o for o in self._demo
                if o not in (sid, "daemon") and o not in partners
                and self._d(o)["running"] and self._band(o) == band]

    def _dep_stacks(self, sid) -> list:
        """Stack ids this stack must have RUNNING (its components' `depends_on`, resolved to
        stacks) — e.g. graywolf -> the KISS TNC. The daemon is excluded (a running band-stack
        brings it up via _daemon_up_on); self is excluded."""
        s = self.stack(sid)
        if s is None:
            return []
        out: list = []
        for c in getattr(s, "components", []) or []:
            for dep in getattr(c, "depends_on", None) or ():
                dsid = self._sid(dep)
                if dsid and dsid not in (sid, "daemon") and dsid not in out:
                    out.append(dsid)
        return out

    def _dependents(self, sid) -> list:
        """RUNNING stacks that depend on `sid` (reverse of _dep_stacks) — e.g. graywolf on
        the KISS TNC. Stopping/uninstalling a provider must take these down too."""
        return [o for o in list(self._demo)
                if o != sid and self._d(o)["running"] and sid in self._dep_stacks(o)]

    def _deps_needing_start(self, sid, band) -> list:
        """The dependency stacks a start on `band` must (re)start: one that is not running, OR
        a band-switchable one running on the WRONG band (it must MOVE onto the parent's band —
        graywolf 868 must not leave its KISS TNC on 433)."""
        out = []
        for dep in self._dep_stacks(sid):
            dep_band = band if band in self._bandswitchable(dep) else ""
            dd = self._d(dep)
            if not dd["running"] or (dep_band and self._band(dep) != dep_band):
                out.append(dep)
        return out

    def _require_and_start_deps(self, sid, apply, band="", stop_owners=False):
        """SHARED by start + restart: the dependency chain must be SATISFIABLE — return an
        error ActionResult if a dependency is absent, unbuilt, or (on apply) fails to start —
        else start (or MOVE onto `band`) each dependency that needs it. Returns
        (error_or_None, started_list)."""
        for dep in self._dep_stacks(sid):
            dd = self._d(dep)
            if not dd["installed"]:
                return ActionResult(False, f"Cannot start {sid}: its dependency '{dep}' "
                                    "is not installed."), []
            if not dd["built"]:
                return ActionResult(False, f"Cannot start {sid}: its dependency '{dep}' "
                                    "is not built."), []
        started: list = []
        if apply:
            for dep in self._deps_needing_start(sid, band):
                dep_band = band if band in self._bandswitchable(dep) else ""
                r = self.start(dep, apply=True, band=dep_band,   # start re-persists the band -> moves it
                               stop_owners=stop_owners)
                if not r.ok:
                    for s in started:              # roll back this call's partial starts
                        self._mark_stopped(s)
                    return ActionResult(False, f"Cannot start {sid}: its dependency "
                                        f"'{dep}' failed to start ({r.summary})."), []
                started.append(dep)
        return None, started

    def _band_takeover(self, sid, band, apply, k):
        """The product's stop-owners contract for a start/restart onto `band`: (refusal or None,
        plan data). Without `stop_owners` an apply never takes the band from a running owner; the
        plan names the owners as `blockers`, so the web asks first."""
        owners = self._band_owners(sid, band)
        if apply and owners and not (k or {}).get("stop_owners"):
            return ActionResult(False, f"Cannot run '{sid}': {', '.join(owners)} must be stopped "
                                "first.", next_commands=[f"lhpc stack stop {o}" for o in owners]), {}
        return None, ({"blockers": [{"resource": f"loraham.radio.{band}", "holder_stack": o,
                                     "holder": o} for o in owners]} if owners else {})

    def _cascade_targets(self, sid) -> list:
        """SHARED by stop/uninstall/clean — the RUNNING stacks taken down when `sid` goes
        away: every band-stack for the daemon (it owns the whole radio), else the TRANSITIVE
        closure of dependents (a dependent's own dependents lose their indirect provider too)."""
        if sid == "daemon":
            return self._running_on_bands(("433", "868"))
        out, seen, frontier = [], {sid}, [sid]
        while frontier:
            for dep in self._dependents(frontier.pop()):
                if dep not in seen:
                    seen.add(dep)
                    out.append(dep)
                    frontier.append(dep)
        return out

    def start(self, target: str, apply: bool = False, params=None, **k):
        sid = self._sid(target)
        d = self._d(sid)
        if not d["installed"]:
            return ActionResult(False, f"{sid} is not installed yet.")
        # An unbuilt stack must not start (mirrors the real unbuilt-components gate).
        if sid != "daemon" and not d["built"]:
            return ActionResult(False, f"{sid} needs building before it can run.")
        # An optional component can't be run on its own in the stack-level model — be honest
        # instead of reporting a start the dashboard then shows as stopped.
        raw = str(getattr(target, "id", target) or "")
        if raw and raw != sid and self.stack(sid) is not None:
            comp = self.stack(sid).component(raw)
            if comp is not None and getattr(comp, "optional", False):
                return self._run_optional(sid, raw, True, apply)
        if sid == "daemon":
            bands = self._daemon_start_bands(k)
            if apply:
                self._set_dbands(self._dbands() | set(bands))
                self._write_logs()
            verb = "Started" if apply else "Would start"
            return ActionResult(True, f"{verb} the daemon on "
                                f"{' + '.join(bands)} MHz (simulated).")
        # The band this start TARGETS: the selector for a band-switchable stack, else the
        # stack's current/declared band. Resolved for BOTH the dry run and apply so the confirm
        # preview reasons about the SAME radio Apply will use (persist only on apply). Starting
        # graywolf on 868 must not touch 433 — plan or apply.
        req_band = self._req_band(k)
        # The production identity gate on the saved configuration: no resolvable callsign or
        # node name refuses the start, and the web sends the visitor to the Settings row.
        if (refused := self._identity_refusal(sid, self._launch_band_hint(sid, req_band),
                                              "start")) is not None:
            return refused
        band = req_band if req_band in self._bandswitchable(sid) else self._band(sid)
        refused, plan_data = self._band_takeover(sid, band, apply, k)   # before ANY mutation
        if refused is not None:
            return refused
        # Full-stack: the dependency chain (graywolf needs the KISS TNC, etc.) must be
        # SATISFIABLE — refuse the parent if a required provider is absent, unbuilt, or fails to
        # start; a band-switchable provider follows the parent onto its band (moved if running
        # on the wrong one).
        err, started_deps = self._require_and_start_deps(sid, apply, band,
                                                         bool((k or {}).get("stop_owners")))
        if err:
            return err
        if apply and band in ("433", "868"):         # committed only once the dependencies hold (a refused start
            d["band"] = band                         # keeps the band it had)
        pending = started_deps if apply else self._deps_needing_start(sid, band)
        owners = self._band_owners(sid, band)          # competitors on the TARGET band
        if apply:
            # One stack per radio band: stop whoever currently holds it (lhpc's stop-owners),
            # so e.g. starting meshcom stops a running graywolf on 433 first.
            for o in owners:
                self._mark_stopped(o)
            d["running"] = True
        note = ""
        if started_deps:
            note += f" (also started {', '.join(started_deps)})"
        elif pending and not apply:
            note += f" (will also start {', '.join(pending)})"
        if owners:
            who = ", ".join(owners)
            note += (f" (stopped {who} on band {band})" if apply
                     else f" (will stop {who} on band {band} — one stack per band)")
        verb = "Started" if apply else "Would start"
        if apply:
            self._write_logs()
        return ActionResult(True, f"{verb} {sid} (simulated).{note}",
                            data={} if apply else plan_data)

    def _running_on_bands(self, bands) -> list:
        """Running non-daemon stacks whose band is in `bands` (they use that radio)."""
        return [o for o in list(self._demo)
                if o != "daemon" and self._d(o)["running"] and self._band(o) in bands]

    def _run_optional(self, sid, cid, on: bool, apply: bool):
        """Run (`on`) or Stop optional part `cid` of stack `sid`, as the card's own Run/Stop does: it runs only inside its
        running stack (a box starts it as a process of that instance)."""
        d = self._d(sid)
        if on and not d["running"]:
            return ActionResult(False, f"'{cid}' runs inside {sid}: start {sid} first.")
        if apply:
            d["opt"] = sorted(set(d["opt"]) | {cid}) if on else [x for x in d["opt"] if x != cid]
            if on:
                self._write_logs()
        verb = ("Started" if on else "Stopped") if apply else ("Would start" if on else "Would stop")
        return ActionResult(True, f"{verb} {cid} (simulated).")

    def stop(self, target: str, apply: bool = False, **k):
        sid = self._sid(target)
        # An optional component can't be stopped on its own in the stack-level model — refuse
        # (symmetric with start), instead of tearing down the whole stack.
        raw = str(getattr(target, "id", target) or "")
        if raw and raw != sid and self.stack(sid) is not None:
            comp = self.stack(sid).component(raw)
            if comp is not None and getattr(comp, "optional", False):
                return self._run_optional(sid, raw, False, apply)
        if sid == "daemon":
            b = self._req_band(k)
            # The daemon is 'up' on a band via an explicit start OR a stack running there.
            # Stopping it takes those bands down — which means CASCADING to the stacks on them
            # (they lose the radio), or the daemon view would keep reading reachable.
            served = sorted(self._dbands() | {self._band(o) for o in self._running_on_bands(("433", "868"))})
            bands = [b] if b in ("433", "868") else served
            cascaded = self._running_on_bands(bands)
            if apply:
                self._set_dbands(self._dbands() - set(bands))
                for o in cascaded:
                    self._mark_stopped(o)
            verb = "Stopped" if apply else "Would stop"
            note = f" (also stopped {', '.join(cascaded)})" if cascaded else ""
            if not bands:                           # nothing was served — don't claim "all bands"
                return ActionResult(True, "The daemon is not serving any band (nothing to stop).")
            return ActionResult(True,
                                f"{verb} the daemon on {' + '.join(bands)} MHz{note} (simulated).")
        dependents = self._dependents(sid)          # stacks that lose their provider
        if apply:
            self._mark_stopped(sid)
            for dep in dependents:
                self._mark_stopped(dep)
        note = ""
        if dependents:
            note = (f" (also stopped {', '.join(dependents)})" if apply
                    else f" (will also stop dependents: {', '.join(dependents)})")
        return ActionResult(True, (f"Stopped {sid} (simulated).{note}" if apply
                                   else f"Would stop {sid} (simulated).{note}"))

    def restart(self, target: str, apply: bool = False, params=None, **k):
        sid = self._sid(target)
        d = self._d(sid)
        if not d["installed"]:
            return ActionResult(False, f"{sid} is not installed yet.")
        if sid != "daemon" and not d["built"]:
            return ActionResult(False, f"{sid} needs building before it can run.")
        # Same refusal as start/stop: restarting the STACK for an optional part would report a
        # restart the dashboard then shows as stopped.
        raw = str(getattr(target, "id", target) or "")
        if raw and raw != sid and self.stack(sid) is not None:
            comp = self.stack(sid).component(raw)
            if comp is not None and getattr(comp, "optional", False):
                return ActionResult(False, f"'{raw}' is an optional component — the demo "
                                    "restarts stacks, not individual optional parts.")
        if sid == "daemon":
            bands = self._daemon_start_bands(k)
            if apply:
                self._set_dbands(self._dbands() | set(bands))
                self._write_logs()
            verb = "Restarted" if apply else "Would restart"
            return ActionResult(True, f"{verb} the daemon on "
                                f"{' + '.join(bands)} MHz (simulated).")
        # restart brings the FULL stack back and uses the SAME hardened dependency check as
        # start: refuse if a dependency is absent, unbuilt, or won't start (the audit's
        # "restart graywolf while KISS is missing"). Honour the band selector too (dry run + apply).
        req_band = self._req_band(k)
        if (refused := self._identity_refusal(sid, self._launch_band_hint(sid, req_band),
                                              "restart")) is not None:
            return refused
        band = req_band if req_band in self._bandswitchable(sid) else self._band(sid)
        refused, plan_data = self._band_takeover(sid, band, apply, k)   # before ANY mutation
        if refused is not None:
            return refused
        err, started_deps = self._require_and_start_deps(sid, apply, band,
                                                         bool((k or {}).get("stop_owners")))
        if err:
            return err
        if apply and band in ("433", "868"):         # committed only once the dependencies hold (a refused start
            d["band"] = band                         # keeps the band it had)
        pending = started_deps if apply else self._deps_needing_start(sid, band)
        if apply:
            for o in self._band_owners(sid, band):
                self._mark_stopped(o)
            d["running"] = True
        note = (f" (also started {', '.join(pending)})" if (apply and pending)
                else (f" (will also start {', '.join(pending)})" if pending else ""))
        if apply:
            self._write_logs()
        return ActionResult(True, f"Restarted {sid} (simulated).{note}" if apply
                            else f"Would restart {sid} (simulated).{note}",
                            data={} if apply else plan_data)

    def spawn_web_job(self, op: str, target: str, source: str = "pinned"):
        """The web routes install/build/test through here as DETACHED jobs — which Pyodide
        cannot run, so they no-op yet reported "started". Run the simulated op SYNCHRONOUSLY
        (mutating the model) and return the (None, 'blocked', reason) shape, so the web flashes
        the real result and returns to the stack instead of an empty live-log view."""
        fn = {"install": lambda: self.install(target, apply=True, source=source),
              "build": lambda: self.build(target, apply=True),
              "test": lambda: self.test(target, apply=True)}.get(op)
        if fn is None:
            return None, "blocked", f"{op} is not simulated in the demo."
        return None, "blocked", fn().summary

    def spawn_start_job(self, op: str, target: str, band: str = "", stop_owners: bool = False,
                        cascade: bool = False, rid: str = "", plan_hash: str | None = None):   # the core's signature
        """The web Start/Restart is a DETACHED job in production; Pyodide cannot spawn one, so run
        the simulated operation synchronously and return the `blocked` shape with the real summary
        (the web flashes it and returns to the stack)."""
        from lhpc.core import commands
        res = self.run_action(op, target, apply=True, stop_owners=stop_owners,
                              band=band or self._req_band({"inst": commands.Instance(rid)} if rid else {}),
                              cascade=cascade)
        return None, "blocked", res.summary

    # The remaining visible actions must SIMULATE against the model too — inheriting the
    # production methods made e.g. "Uninstall applied" report success while the stack stayed
    # installed (the real code no-ops with no filesystem/process to act on). Each mutates the
    # in-memory model so the dashboard reflects it, honouring `apply` (dry-run vs commit).
    def uninstall(self, target: str, apply: bool = False, **k):
        sid = self._sid(target)
        dependents = self._cascade_targets(sid)     # daemon -> all band-stacks; else dependents
        if apply:
            d = self._d(sid)
            d["installed"] = d["built"] = False
            self._mark_stopped(sid)
            for dep in dependents:
                self._mark_stopped(dep)
            if sid == "daemon":
                self._set_dbands(set())
        note = ""
        if dependents:                              # preview the cascade on the dry-run too
            note = (f" (also stopped {', '.join(dependents)})" if apply
                    else f" (will also stop {', '.join(dependents)})")
        return ActionResult(True, f"Uninstalled {sid} (simulated).{note}" if apply
                            else f"Would uninstall {sid} (simulated).{note}")

    def update(self, target: str, apply: bool = False, source: str = "pinned", **k):
        sid = self._sid(target)
        d = self._d(sid)
        if not d["installed"]:
            return ActionResult(False, f"{sid} is not installed yet.")
        # A real update fetches new source but leaves the CURRENT build running until it is
        # rebuilt + restarted — so it must NOT flip built=False on a running stack (that made
        # build_snapshot show it RUNNING while restart refused it as unbuilt). The demo has no
        # "source differs" state, so update is a no-op with a note.
        return ActionResult(True, f"Updated {sid} (simulated) — rebuild + restart to apply." if apply
                            else f"Would update {sid} (simulated).")

    def clean(self, target: str, apply: bool = False, purge: bool = False, **k):
        sid = self._sid(target)
        # Cleaning removes the build (and stops the stack), so its running dependents lose
        # their provider — cascade them down too, same as stop/uninstall. Cleaning the daemon
        # takes the whole radio away.
        dependents = self._cascade_targets(sid)
        if apply:
            d = self._d(sid)
            d["built"] = False
            self._mark_stopped(sid)
            if purge:
                d["installed"] = False
            for dep in dependents:
                self._mark_stopped(dep)
            if sid == "daemon":
                self._set_dbands(set())
        note = ""
        if dependents:                              # preview the cascade on the dry-run too
            note = (f" (also stopped {', '.join(dependents)})" if apply
                    else f" (will also stop {', '.join(dependents)})")
        done, plan = ("Purged", "purge") if purge else ("Cleaned", "clean")
        return ActionResult(True, f"{done} {sid} (simulated).{note}" if apply
                            else f"Would {plan} {sid} (simulated).{note}")

    def test(self, target: str, apply: bool = False, tx: bool = False, **k):
        sid = self._sid(target)
        if not self._d(sid)["installed"]:
            return ActionResult(False, f"{sid} is not installed yet.")
        kind = "TX self-test" if tx else "self-test"
        # Truthful: there is no real radio in the browser, so nothing is transmitted/measured.
        return ActionResult(True, f"{kind} for {sid} is simulated — no real radio, "
                            "nothing was transmitted or measured.")
