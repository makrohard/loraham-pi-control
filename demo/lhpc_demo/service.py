"""DemoService — a ControllerService that SIMULATES the stack lifecycle in memory. It keeps
a per-stack model {installed, built, running} and makes the REAL console reflect it two ways:
the low-level predicates (is_installed/is_built/stack_running) read the model, and
build_snapshot() — the single source of truth the dashboard/apps use for run-state — is
post-processed so Start/Stop are actually visible. No git, gcc, processes, or hardware;
everything else (rendering, config, CSRF, routing) is the unmodified lhpc app."""
from __future__ import annotations

from lhpc.core.service_base import ActionResult
from lhpc.core.services import ControllerService

_KEYS = ("installed", "built", "running")


class DemoService(ControllerService):
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
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
        return d

    def seed_all_installed(self) -> None:
        """Default demo state: every stack installed + built, none running — so a visitor
        lands on a fully set-up box (like a provisioned Codespace) and just starts/stops."""
        try:
            for s in self.stacks():
                if s.id == "loraham-pi-control":
                    continue
                d = self._d(s.id)
                d["installed"], d["built"], d["running"] = True, True, False
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

    # --- run-state is rendered from build_snapshot(); reflect the model there so Start/Stop
    #     are visible everywhere the console reads run_state (dashboard, apps, dash poll). --
    def build_snapshot(self, *a, **k):
        from lhpc.core.model import RunState, SourceState
        snap = super().build_snapshot(*a, **k)
        for ss in snap.stacks:
            d = self._d(ss.stack.id)
            for cid, cs in ss.components.items():
                if d["installed"]:
                    # installed -> source present (drives radio_overview's daemon_installed
                    # and every source_state-based "installed" check the dashboard reads).
                    cs.source_state = SourceState.MATCH
                if cs.run_state == RunState.NOT_APPLICABLE:
                    continue                 # libraries/firmware have no run state
                comp = ss.stack.component(cid)
                optional = bool(getattr(comp, "optional", False)) if comp else False
                if not d["installed"]:
                    cs.run_state = RunState.NOT_INSTALLED
                elif d["running"] and not optional:
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

    def _daemon_start_bands(self, k) -> list:
        """Which bands a daemon start/restart affects: the explicit `band` if given, else
        every configured radio-mode band (a bandless start serves the whole configured set —
        matching real dual/single mode), falling back to both."""
        b = str((k or {}).get("band") or "")
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

    def daemon_view(self, band: str):
        from lhpc.core.daemon_control import DaemonView
        if not self._daemon_up_on(band):
            return DaemonView(band=band, reachable=False)
        from . import daemon_sim
        v = DaemonView(band=band, reachable=True)
        v.status, v.stats, v.channel = (daemon_sim.status(band), daemon_sim.stats(band),
                                        daemon_sim.channel(band))
        return v

    def daemon_feed(self, band: str, lines: int = 40) -> list:
        if not self._daemon_up_on(band):
            return []
        from . import daemon_sim
        return daemon_sim.feed(band, lines)

    def daemon_socket_line(self, band: str) -> str:
        if not self._daemon_up_on(band):
            return ""
        from . import daemon_sim
        return "STATUS " + " ".join(f"{k}={v}" for k, v in daemon_sim.status(band).items())

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

    def auto_install_welcome(self):
        # A pre-provisioned demo box is never "fresh" — suppress the first-run
        # "Nothing is installed yet" banner (it reads the real filesystem, which the demo
        # does not populate; the model is the source of truth here).
        if any(isinstance(v, dict) and v.get("installed") for v in self._demo.values()):
            return None
        return super().auto_install_welcome()

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

    def running_band(self, stack_id: str, default: str = "") -> str:
        """Override the inherited band marker (the demo never writes real markers): the band a
        RUNNING stack occupies is the one recorded in the model. This is what the dashboard's
        _effective_band reads to PLACE the stack under a radio, so it must agree with the band
        our lifecycle used (else graywolf shows under 433 while the daemon is up on 868)."""
        d = self._d(self._sid(stack_id))
        rb = d.get("band")
        return rb if (d.get("running") and rb in ("433", "868")) else default

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

    def _require_and_start_deps(self, sid, apply, band=""):
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
                r = self.start(dep, apply=True, band=dep_band)   # start re-persists the band -> moves it
                if not r.ok:
                    for s in started:              # roll back this call's partial starts
                        self._d(s)["running"] = False
                    return ActionResult(False, f"Cannot start {sid}: its dependency "
                                        f"'{dep}' failed to start ({r.summary})."), []
                started.append(dep)
        return None, started

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
                return ActionResult(False, f"'{raw}' is an optional component — the demo "
                                    "starts stacks, not individual optional parts.")
        if sid == "daemon":
            bands = self._daemon_start_bands(k)
            if apply:
                self._set_dbands(self._dbands() | set(bands))
            verb = "Started" if apply else "Would start"
            return ActionResult(True, f"{verb} the daemon on "
                                f"{' + '.join(bands)} MHz (simulated).")
        # The band this start TARGETS: the selector for a band-switchable stack, else the
        # stack's current/declared band. Resolved for BOTH the dry run and apply so the confirm
        # preview reasons about the SAME radio Apply will use (persist only on apply). Starting
        # graywolf on 868 must not touch 433 — plan or apply.
        req_band = str((k or {}).get("band") or "")
        band = req_band if req_band in self._bandswitchable(sid) else self._band(sid)
        if apply and band in ("433", "868"):
            d["band"] = band
        # Full-stack: the dependency chain (graywolf needs the KISS TNC, etc.) must be
        # SATISFIABLE — refuse the parent if a required provider is absent, unbuilt, or fails to
        # start; a band-switchable provider follows the parent onto its band (moved if running
        # on the wrong one).
        err, started_deps = self._require_and_start_deps(sid, apply, band)
        if err:
            return err
        pending = started_deps if apply else self._deps_needing_start(sid, band)
        owners = self._band_owners(sid, band)          # competitors on the TARGET band
        if apply:
            # One stack per radio band: stop whoever currently holds it (lhpc's stop-owners),
            # so e.g. starting meshcom stops a running graywolf on 433 first.
            for o in owners:
                self._d(o)["running"] = False
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
        return ActionResult(True, f"{verb} {sid} (simulated).{note}")

    def _running_on_bands(self, bands) -> list:
        """Running non-daemon stacks whose band is in `bands` (they use that radio)."""
        return [o for o in list(self._demo)
                if o != "daemon" and self._d(o)["running"] and self._band(o) in bands]

    def stop(self, target: str, apply: bool = False, **k):
        sid = self._sid(target)
        # An optional component can't be stopped on its own in the stack-level model — refuse
        # (symmetric with start), instead of tearing down the whole stack.
        raw = str(getattr(target, "id", target) or "")
        if raw and raw != sid and self.stack(sid) is not None:
            comp = self.stack(sid).component(raw)
            if comp is not None and getattr(comp, "optional", False):
                return ActionResult(False, f"'{raw}' is an optional component — the demo "
                                    "stops stacks, not individual optional parts.")
        if sid == "daemon":
            b = str((k or {}).get("band") or "")
            # The daemon is 'up' on a band via an explicit start OR a stack running there.
            # Stopping it takes those bands down — which means CASCADING to the stacks on them
            # (they lose the radio), or the daemon view would keep reading reachable.
            served = sorted(self._dbands() | {self._band(o) for o in self._running_on_bands(("433", "868"))})
            bands = [b] if b in ("433", "868") else served
            cascaded = self._running_on_bands(bands)
            if apply:
                self._set_dbands(self._dbands() - set(bands))
                for o in cascaded:
                    self._d(o)["running"] = False
            verb = "Stopped" if apply else "Would stop"
            note = f" (also stopped {', '.join(cascaded)})" if cascaded else ""
            if not bands:                           # nothing was served — don't claim "all bands"
                return ActionResult(True, "The daemon is not serving any band (nothing to stop).")
            return ActionResult(True,
                                f"{verb} the daemon on {' + '.join(bands)} MHz{note} (simulated).")
        dependents = self._dependents(sid)          # stacks that lose their provider
        if apply:
            self._d(sid)["running"] = False
            for dep in dependents:
                self._d(dep)["running"] = False
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
        if sid == "daemon":
            bands = self._daemon_start_bands(k)
            if apply:
                self._set_dbands(self._dbands() | set(bands))
            verb = "Restarted" if apply else "Would restart"
            return ActionResult(True, f"{verb} the daemon on "
                                f"{' + '.join(bands)} MHz (simulated).")
        # restart brings the FULL stack back and uses the SAME hardened dependency check as
        # start: refuse if a dependency is absent, unbuilt, or won't start (the audit's
        # "restart graywolf while KISS is missing"). Honour the band selector too (dry run + apply).
        req_band = str((k or {}).get("band") or "")
        band = req_band if req_band in self._bandswitchable(sid) else self._band(sid)
        if apply and band in ("433", "868"):
            d["band"] = band
        err, started_deps = self._require_and_start_deps(sid, apply, band)
        if err:
            return err
        pending = started_deps if apply else self._deps_needing_start(sid, band)
        if apply:
            for o in self._band_owners(sid, band):
                self._d(o)["running"] = False
            d["running"] = True
        note = (f" (also started {', '.join(pending)})" if (apply and pending)
                else (f" (will also start {', '.join(pending)})" if pending else ""))
        return ActionResult(True, f"Restarted {sid} (simulated).{note}" if apply
                            else f"Would restart {sid} (simulated).{note}")

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
                        cascade: bool = False):
        """The web Start/Restart is a DETACHED job in production; Pyodide cannot spawn one, so run
        the simulated operation synchronously and return the `blocked` shape with the real summary
        (the web flashes it and returns to the stack)."""
        res = self.run_action(op, target, apply=True, stop_owners=stop_owners, band=band,
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
            d["installed"] = d["built"] = d["running"] = False
            for dep in dependents:
                self._d(dep)["running"] = False
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
            d["built"] = d["running"] = False
            if purge:
                d["installed"] = False
            for dep in dependents:
                self._d(dep)["running"] = False
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
