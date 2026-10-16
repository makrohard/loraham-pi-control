"""ControllerService mixin: GPS settings, the GPS monitor, and the GPS plan (who owns and who consumes a position).

Adapters import lhpc.core.services only. Split from service_params.py and services.py unchanged."""
from __future__ import annotations

import time
from typing import ClassVar

from . import meshcore_mode as _meshcore_mode
from .config import ConfigError
from .gps import USE_GPS_PARAM
from .model import ComponentKind, RunState
from .paths import PathContainmentError
from .service_base import ActionResult
from .snapshot_memo import invalidates_snapshot


class GpsOpsMixin:

    def _gps_config_values(self, target: str) -> dict:
        """Controller-owned `{gps_*}` values for a stack's generated config.

        These come from the ONE global plan, which is why the consuming params are `hidden`
        and non-editable: a stack must not be able to select its own position source. A
        saved or hand-edited per-stack value can never win, because the value is not read
        from the stack's config at all — it is substituted here.

        `{gps_device}` differs per consumer by design:
          * `gpsd` -> Meshtastic gets the bridge PTY (meshtasticd cannot speak gpsd);
          * `nmea` -> the REAL device, so meshtasticd detects an actual chip instead of
            sweeping baud rates for ~37 s against a stream nothing can answer;
          * `fixed`/`off` -> empty, and the param is `omit_if_empty`.
        Sideband needs no device path for gpsd — its plugin is a native gpsd client.
        """
        from .gps import CONSUMER_MESHCORE, CONSUMER_MESHTASTIC, bridge_endpoint_path
        # TARGET-AWARE: the global source says WHERE, the stack's switch says WHETHER. Using
        # the bare global plan here rendered a device into a stack whose GPS was off.
        plan = self.gps_plan(target)
        owner = self.gps_owner_stack(target)
        device = ""
        if plan.enabled:
            if owner == CONSUMER_MESHCORE:
                # ALWAYS the bridge PTY, never the real receiver: MeshCore reads a device
                # continuously, so pointing it at the hardware would put two readers on one
                # receiver. The bridge owns the device (and holds the exclusive claim) and
                # republishes it. Empty for `fixed`, which needs no feed at all.
                device = (bridge_endpoint_path(self._paths.runtime_root, CONSUMER_MESHCORE)
                          if plan.needs_bridge(CONSUMER_MESHCORE) else "")
            elif plan.source == "nmea":
                device = plan.device
            elif owner == CONSUMER_MESHTASTIC and plan.needs_bridge(CONSUMER_MESHTASTIC):
                device = bridge_endpoint_path(self._paths.runtime_root, CONSUMER_MESHTASTIC)
        return {
            "{gps_source}": plan.source,
            "{gps_host}": plan.host or "127.0.0.1",
            "{gps_port}": str(plan.port or 2947),
            "{gps_device}": device,
            "{gps_nmea_device}": plan.device,
            "{gps_baud}": str(plan.nmea_baud or 9600),
            "{gps_lat}": plan.fixed_lat,
            "{gps_lon}": plan.fixed_lon,
            "{gps_alt}": plan.fixed_alt,
        }

    # Per-stack position keys that `[gps]` supersedes (reported when still saved on disk).
    _LEGACY_GPS_KEYS: ClassVar[tuple] = (
        "location_source", "gpsd_host", "gpsd_port", "nmea_device", "nmea_baud",
        "fixed_lat", "fixed_lon", "fixed_alt")

    def legacy_gps_values(self) -> dict:
        """Stale per-stack position values still saved on disk -> {stack: [keys]}.

        They take no effect: the params are `hidden` and filled from `[gps]`, so a saved
        value cannot override the global source. Leaving them silently in place would
        mislead anyone reading the config, so they are REPORTED (ignored with a clear
        diagnostic, never silently overriding).
        """
        found = {}
        for sid in ("reticulum",):
            try:
                cfg = self._stack_config_cached(sid)
            except ConfigError as exc:
                # `load_stack_config` is FAIL-CLOSED: a malformed/unreadable stack file raises
                # ConfigError, which is NOT an OSError or ValueError. Catching the wrong types
                # let it escape into a status GET and the console's settings view as a
                # traceback/500. Report it as a named diagnostic instead — the operator needs
                # to know the file is broken, and nothing here may fall back to defaults.
                found[sid] = [f"unreadable stack config: {exc}"]
            except OSError as exc:
                found[sid] = [f"unreadable stack config: {exc}"]
            else:
                keys = sorted(k for k in self._LEGACY_GPS_KEYS
                              if f"file_{k}" in cfg or k in cfg)
                if keys:
                    found[sid] = keys
        return found

    # A GPS change is blocked by any of these. UNKNOWN is included deliberately: lhpc represents
    # a probe that could not answer as UNKNOWN, not as an exception, so treating it as "not
    # running" is precisely the fail-open this guards against.
    _GPS_LIVE_STATES: ClassVar[tuple] = (RunState.RUNNING, RunState.DEGRADED, RunState.UNKNOWN)

    def gps_liveness_blockers(self, stack_ids, snap=None, require_enabled: bool = False) -> list:
        """Components that must stop before a GPS setting may change, from ONE snapshot.

        Looks at each owner stack's position CONSUMERS and its FEED — never just the stack's
        main component. A feed running on its own holds the receiver claim and an endpoint built
        from the CURRENT settings while its stack's main reads stopped, and a consumer is the
        thing whose rendered config and claims were derived from them.

        Pass `snap` to evaluate both GPS transactions against the SAME fresh picture; omit it and
        a fresh one is taken. A snapshot that cannot be built at all is itself a blocker.
        """
        watch = self._gps_consumer_ids() | self._all_gps_feed_ids()
        if not self._meshcore_consumes_position(_meshcore_mode.STACK_ID,
                                                self.meshcore_running_mode()):
            watch = watch - {_meshcore_mode.NODE_ID}     # a live repeater reads no position
        wanted = {s for s in stack_ids if s}
        if require_enabled:
            # A stack running with its switch OFF takes no position from the global setting, so
            # it is not affected by a source change and must not block one. (Its FEED could not
            # be running in that state anyway — a live feed IS the stack using GPS.)
            wanted = {s for s in wanted if self.gps_enabled_for(s)}
        try:
            snap = snap if snap is not None else self.build_snapshot(fresh=True)
        except Exception:
            return sorted(f"{sid} (state unknown)" for sid in wanted)
        live = []
        for ss in snap.stacks:
            if getattr(ss.stack, "id", "") not in wanted:
                continue
            for cid, st in ss.components.items():
                if cid in watch and getattr(st, "run_state", None) in self._GPS_LIVE_STATES:
                    live.append(f"{cid} (state unknown)"
                                if st.run_state is RunState.UNKNOWN else cid)
        return sorted(set(live))

    def _gps_fresh_snapshot(self):
        """A forced recompute, or None when it cannot be built (the callers treat that as a
        blocker rather than as 'nothing is running')."""
        try:
            return self.build_snapshot(fresh=True)
        except Exception:
            return None

    def gps_consumers_running(self, snap=None) -> list:
        """Everything that must stop before the GLOBAL position source may change.

        Changing the source under a running consumer would leave it reading a device or a gpsd
        that the new plan no longer describes — its claims and its rendered config were both
        derived from the OLD plan. Refusing is simpler and safer than re-planning a running
        stack, and it is what the operator would have to do by hand anyway.

        DERIVED from the manifest: this was the third hardcoded GPS stack list,
        and it drifted like the other two — graywolf was missing, so `lhpc gps --source ...`
        went through while a GPS-enabled graywolf ran, leaving its live process on the old
        source while the saved plan (and, with direct NMEA, the receiver claim) described the
        new one.
        """
        return self.gps_liveness_blockers(
            tuple(sorted(self._gps_stacks())), snap=snap,
            require_enabled=True)

    def gps_settings(self) -> dict:
        """The global GPS view for the CLI and the console — ONE source of truth, so the two
        surfaces can never show different sources."""
        from .gps import plan_from_config
        cfg = self.config()
        g = cfg.gps
        plan = plan_from_config(cfg)
        return {"source": g.source, "host": g.host, "port": g.port, "device": g.device,
                "nmea_baud": g.nmea_baud, "fixed_lat": g.fixed_lat, "fixed_lon": g.fixed_lon,
                "fixed_alt": g.fixed_alt, "valid": g.valid, "reason": g.reason,
                "local_gpsd": g.local_gpsd, "claims_serial": g.claims_local_serial,
                "device_key": plan.device_key, "plan_reason": plan.reason,
                # What `auto` actually became right now ("gpsd" or "off"); == source otherwise.
                # This is the honest display line — the saved setting alone cannot say whether
                # a box with `auto` is currently reporting position.
                "resolved_source": plan.source,
                # The SECTION can parse cleanly and still not resolve — a `nmea` device that is
                # missing or is not a character device. That plan is represented as `off`, so
                # without this the surfaces reported a healthy "source: nmea" for a source no
                # stack can actually use.
                "plan_valid": plan.valid}

    def gps_view(self) -> dict:
        """Console view of the global position source. Built from `gps_settings()` so the web
        card and `lhpc gps` can never disagree about what is configured."""
        from .config import GPS_BAUDS, GPS_SOURCES
        labels = {"off": "Off — no position",
                  "auto": "Auto — use gpsd if one runs on this box (default)",
                  "gpsd": "gpsd (USB / HAT / network GPS server)",
                  "nmea": "Direct NMEA device (not shared with gpsd)",
                  "fixed": "Fixed position (station does not move)"}
        v = self.gps_settings()
        v["sources"] = [(s, labels.get(s, s)) for s in GPS_SOURCES]
        v["bauds"] = list(GPS_BAUDS)
        # Stack display names for the card prose, DERIVED like the set itself — a hand-written
        # list in the template is the same drifting literal the derived set replaced.
        v["consumers"] = [s.name for s in self.stacks() if s.id in self._gps_stacks()]
        # DISPLAY only — the memoized snapshot, never a forced recompute. A page render calls the
        # stack helpers ~15×, and making this one reassess turned every render into two full
        # assessments (re-scanning /proc and re-running git for every source). The authoritative,
        # forced-fresh check belongs in the two config transactions, not on a GET.
        v["running"] = self.gps_consumers_running(snap=self._snapshot_or_none())
        v["legacy"] = self.legacy_gps_values()
        return v

    # ---- GPS Monitor ------------------------------------------------------------------------
    # The status half of the GPS block (docs/gps.md, "Monitor"). Reads gpsd's own JSON on demand
    # for gpsd sources, the configured values for `fixed`, and for a direct-NMEA receiver one of
    # three states: through the owning feed's monitor socket, "held" while something reads the
    # device, or ONE bounded sample under the lifecycle's own claim when nobody does. It mutates
    # no configuration or lifecycle state and logs nothing.

    _MONITOR_HOLD_BUDGET_S = 4.5       # the whole hold must fit under the lifecycle's 5 s wait
    _MONITOR_SAMPLE_S = 2.5
    _MONITOR_FEED_BUDGET_S = 1.0

    def gps_monitor(self) -> dict:
        """The Monitor snapshot for the console and `lhpc gps --monitor`. Fails soft: never an
        exception into a page. See `gps.gpsd_snapshot` / `gps.NmeaSnapshot` for the shapes."""
        from . import gps as _gps
        v = self.gps_settings()
        out = {"source": v["source"], "resolved_source": v["source"], "available": False,
               "state": "off", "label": "", "note": "", "error": "", "nmea_ok": False,
               "devices": [], "device": None, "mode": None, "lat": None, "lon": None,
               "alt": None, "alt_kind": None, "time": None, "time_has_date": False,
               "sats_used": None, "sats_seen": None, "satellites": [], "nmea": []}
        try:
            if not v["valid"]:
                out.update(state="off", error=v["reason"])
            elif v["source"] == "off":
                out.update(state="off")
            elif v["source"] == "fixed":
                out.update(state="fixed", available=True, resolved_source="fixed")
                for k, src in (("lat", "fixed_lat"), ("lon", "fixed_lon"), ("alt", "fixed_alt")):
                    raw = str(v.get(src) or "").strip()
                    try:
                        out[k] = float(raw) if raw else None
                    except ValueError:
                        out[k] = None
                out["alt_kind"] = "msl" if out["alt"] is not None else None
            elif v["source"] == "auto" and not _gps.local_gpsd_listening():
                # The Monitor's OWN live probe: current availability, not the lifecycle's frozen
                # verdict; it changes no cached config (refresh_gps_auto() at the request boundary
                # stays what it is).
                out.update(state="auto-off", resolved_source="off")
            elif v["source"] in ("auto", "gpsd"):
                host, port = ((_gps.AUTO_GPSD_HOST, _gps.AUTO_GPSD_PORT) if v["source"] == "auto"
                              else (v["host"], int(v["port"])))
                out.update(resolved_source="gpsd", available=True)
                snap = _gps.gpsd_snapshot(host, port)
                out.update({k: snap[k] for k in ("state", "devices", "device", "mode", "lat",
                                                  "lon", "alt", "alt_kind", "time",
                                                  "time_has_date", "sats_used", "sats_seen",
                                                  "satellites", "nmea_ok", "note", "error")})
                if not snap["ok"]:
                    out.update(state="unavailable", nmea_ok=False)
            elif v["source"] == "nmea":
                out.update(resolved_source="nmea", available=True)
                out.update(self._gps_monitor_nmea())
        except Exception as exc:
            out.update(state="unavailable", error=f"monitor failed ({type(exc).__name__})")
        out["label"] = _gps.monitor_label(out["state"])
        return out

    def gps_nmea(self) -> list[str]:
        """gpsd's NMEA output for a gpsd source, bounded. `[]` for every other source — for a
        direct receiver the sample's own tail rides in `gps_monitor()`, so this never opens a
        serial device (one reader, one sample)."""
        from . import gps as _gps
        v = self.gps_settings()
        try:
            if v["source"] == "gpsd" and v["valid"]:
                return _gps.gpsd_nmea_window(v["host"], int(v["port"]))
            if v["source"] == "auto" and v["valid"] and _gps.local_gpsd_listening():
                return _gps.gpsd_nmea_window(_gps.AUTO_GPSD_HOST, _gps.AUTO_GPSD_PORT)
        except Exception:
            return []
        return []

    # -- direct NMEA: holders, the feed socket, the one sample ------------------------------

    def _gps_positively_disabled(self, stack_id: str) -> bool:
        """True ONLY when the stack's `use_gps` is readable and exactly off. An unreadable or
        malformed config is NOT a disabled stack — `gps_enabled_for()` answers False there, which
        would drop a native reader that still holds the open device (audit F1)."""
        try:
            cfg = self._stack_config_cached(stack_id)
        except Exception:
            return False
        return str(cfg.get("use_gps", "")).strip().lower() == "off"

    def _gps_monitor_holders(self, snap=None) -> list[dict]:
        """Everything that may hold the direct receiver right now, DERIVED: every live
        (RUNNING/DEGRADED/UNKNOWN) GPS consumer or feed from the unfiltered liveness set,
        minus native consumers with a positively established opt-out. Each entry:
        {"cid", "stack", "kind": "feed"|"native", "unknown"}."""
        from .gps import consumer_for_component
        live = self.gps_liveness_blockers(tuple(sorted(self._gps_stacks())), snap=snap,
                                          require_enabled=False)
        feeds = self._all_gps_feed_ids()
        out = []
        for entry in live:
            cid = entry.split(" ", 1)[0]
            unknown = "(state unknown)" in entry
            sid = self._owner_stack_id(cid) or cid
            kind = "feed" if cid in feeds else "native"
            if kind == "native" and not unknown and self._gps_positively_disabled(sid):
                continue
            out.append({"cid": cid, "stack": sid, "kind": kind, "unknown": unknown,
                        "consumer": consumer_for_component(cid) if kind == "feed" else ""})
        return out

    def _gps_runtime_holders(self, deadline: float) -> list[dict] | None:
        """`_gps_monitor_holders` for the re-check UNDER the receiver claim: the same consumer and
        feed ids, judged from process and unit evidence alone — no snapshot build, no source or
        git probe, nothing that can outlive `deadline`. Live = a unit active or the process
        present; a component whose evidence cannot be read counts as a holder. Returns None when
        the check itself runs out of time (the caller fails closed: held)."""
        from .gps import consumer_for_component
        from .probes.process import probe_process
        from .probes.systemd import _TIMEOUT_S as _UNIT_PROBE_S
        from .probes.systemd import UnitState, probe_unit
        try:
            watch = self._gps_consumer_ids() | self._all_gps_feed_ids()
            if not self._meshcore_consumes_position(_meshcore_mode.STACK_ID,
                                                    self.meshcore_running_mode()):
                watch = watch - {_meshcore_mode.NODE_ID}
            feeds = self._all_gps_feed_ids()
            index = self._component_index()
            out = []
            for cid in sorted(watch):
                if time.monotonic() >= deadline:
                    return None
                hit = index.get(cid)
                if hit is None:
                    continue
                sid, comp = hit[0].id, hit[1]
                if comp.kind in (ComponentKind.LIBRARY, ComponentKind.FIRMWARE) or (
                        comp.kind == ComponentKind.ONESHOT and not comp.interactive):
                    continue
                states = []
                for u in comp.units:
                    # a unit probe may hang for its whole timeout: never start one that could
                    # end past the deadline — fail closed instead
                    if time.monotonic() + _UNIT_PROBE_S > deadline:
                        return None
                    states.append(probe_unit(self._system, u.name, u.scope).state)
                matched = comp.process is not None and probe_process(self._system, comp.process).matched
                live = matched or any(st is UnitState.ACTIVE for st in states)
                unknown = (not live and bool(states)
                           and all(st in (UnitState.UNAVAILABLE, UnitState.TIMEOUT) for st in states))
                if not live and not unknown:
                    continue
                kind = "feed" if cid in feeds else "native"
                if kind == "native" and not unknown and self._gps_positively_disabled(sid):
                    continue
                out.append({"cid": cid, "stack": sid, "kind": kind, "unknown": unknown,
                            "consumer": consumer_for_component(cid) if kind == "feed" else ""})
            return out
        except Exception:
            return None

    def _gps_feed_monitor(self, consumer: str) -> dict | None:
        """The owning feed's monitor snapshot, or None when its evidence cannot be trusted
        (stale marker, dead pid, missing or broken socket)."""
        import json
        import os as _os
        import socket as _socket
        from pathlib import Path

        from . import runtime_fs
        from .gps import bridge_state_dir, marker_is_fresh, marker_owner_pid
        state_dir = bridge_state_dir(self._paths.runtime_root, consumer)
        try:
            marker = json.loads(runtime_fs.read_text(self._paths, Path(state_dir) / "readiness.json"))
        except Exception:
            return None
        if not isinstance(marker, dict) or not marker_is_fresh(marker, time.time()):
            return None
        pid = marker_owner_pid(marker)
        if pid is None:
            return None
        try:
            _os.kill(pid, 0)
        except OSError:
            return None
        deadline = time.monotonic() + self._MONITOR_FEED_BUDGET_S
        try:
            with _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM) as sock:
                sock.settimeout(self._MONITOR_FEED_BUDGET_S)
                sock.connect(_os.path.join(state_dir, "monitor.sock"))
                buf = b""
                while b"\n" not in buf and len(buf) < 65536 and time.monotonic() < deadline:
                    chunk = sock.recv(8192)
                    if not chunk:
                        break
                    buf += chunk
            line = buf.split(b"\n", 1)[0]
            got = json.loads(line) if line else None
            return got if isinstance(got, dict) and got.get("ok") else None
        except Exception:
            return None

    def _gps_monitor_nmea(self) -> dict:
        """The three states for a direct receiver (docs/gps.md, "Monitor")."""
        from . import gps as _gps
        cfg = self.config()
        plan = _gps.plan_from_config(cfg)
        if not plan.valid or plan.source != "nmea" or not plan.device:
            return {"state": "unavailable", "error": plan.reason or "direct source not resolvable"}
        held = {"state": "held", "device": plan.device, "nmea_ok": False}
        holders = self._gps_monitor_holders()
        feeds = [h for h in holders if h["kind"] == "feed"]
        natives = [h for h in holders if h["kind"] == "native"]
        if feeds:
            h = feeds[0]
            got = None if h["unknown"] else self._gps_feed_monitor(h["consumer"])
            if got is None:
                held["note"] = f"feed running ({h['cid']}); monitor unavailable"
                return held
            res = {k: got.get(k) for k in ("state", "mode", "lat", "lon", "alt", "alt_kind", "time",
                                            "time_has_date", "sats_used", "sats_seen",
                                            "satellites", "nmea")}
            res.update(device=plan.device, nmea_ok=True,
                       note=f"via the {h['stack']} GPS feed",
                       feed_state=got.get("state"))
            res["state"] = got.get("state") or "no-data"
            return res
        if natives:
            names = ", ".join(sorted({h["stack"] for h in natives}))
            held["note"] = (f"device {plan.device} is read by {names} directly; nothing else can "
                            "read a serial port at the same time")
            return held
        return self._gps_direct_sample(plan)

    def _gps_direct_sample(self, plan) -> dict:
        """ONE bounded read of the idle receiver, in this order and no step skipped: the gpsd
        ownership gate -> the lifecycle's own `claim.gps.<key>` lock (non-blocking, operation
        `gps-monitor`) -> a FRESH derived holder re-check while holding it -> open with the pump's
        identity check -> read -> close -> release. The whole hold carries one absolute deadline
        under the lifecycle's 5 s wait; exhaustion abandons the sample and releases the claim."""
        from . import gps as _gps
        from . import reslock
        held = {"state": "held", "device": plan.device, "nmea_ok": False}
        deadline = time.monotonic() + self._MONITOR_HOLD_BUDGET_S
        owned, detail = _gps.gpsd_owns_device(plan.device, _gps.AUTO_GPSD_HOST,
                                              _gps.AUTO_GPSD_PORT, timeout=1.0)
        if owned is not False:
            held["note"] = ("cannot establish that the device is free" +
                            (f" ({detail})" if detail else "") +
                            " — use the gpsd source to monitor a receiver gpsd owns")
            return held
        key = f"claim.{plan.device_key}" if plan.device_key.startswith("gps.") \
            else f"claim.gps.{plan.device_key}"
        try:
            with reslock.operation_lock(self._paths, key, self.GPS_MONITOR_OP, "gps"):
                # the re-check under the claim: a start may have begun between the first check
                # and the lock. RUNTIME EVIDENCE ONLY — a snapshot build here would run the
                # source probes (git, 3 s each per checkout) while holding the receiver claim a
                # starting stack waits on, and the budget would be a number, not a bound.
                holders = self._gps_runtime_holders(deadline)
                if holders is None or time.monotonic() >= deadline:
                    held["note"] = "monitor budget exhausted before the device could be read"
                    return held
                if holders:
                    held["note"] = "a GPS consumer started while the monitor was checking"
                    return held
                remaining = min(self._MONITOR_SAMPLE_S, deadline - time.monotonic())
                if remaining <= 0.2:
                    held["note"] = "monitor budget exhausted before the device could be read"
                    return held
                return self._gps_read_device(plan, remaining)
        except reslock.ResourceBusy as busy:
            who = busy.holder.get("operation", "") if isinstance(busy.holder, dict) else ""
            held["note"] = "device claimed by " + (who or "another operation")
            return held

    def _gps_read_device(self, plan, budget: float) -> dict:
        """The read itself: the pump's open flags and identity check, no command sent to the
        receiver, bounded, closed afterwards. Not hardware-inert (a tty is configured)."""
        import os as _os
        import stat as _stat

        from . import gps as _gps
        from .gps_bridge import _configure_port
        snap = _gps.NmeaSnapshot()
        fd = -1
        try:
            fd = _os.open(plan.device, _os.O_RDONLY | _os.O_NOCTTY | _os.O_NONBLOCK)
            st = _os.fstat(fd)
            if not _stat.S_ISCHR(st.st_mode):
                return {"state": "unavailable", "error": "not a character device"}
            if plan.device_key and \
                    f"serial.dev.{_os.major(st.st_rdev)}:{_os.minor(st.st_rdev)}" != plan.device_key:
                return {"state": "unavailable",
                        "error": "device now resolves to a different receiver than the one claimed"}
            _configure_port(fd, plan.nmea_baud)
            end = time.monotonic() + budget
            buf = b""
            raw = 0
            while time.monotonic() < end:
                try:
                    chunk = _os.read(fd, 4096)
                except BlockingIOError:
                    time.sleep(0.05)
                    continue
                if not chunk:
                    break
                raw += len(chunk)
                buf += chunk
                lines = buf.split(b"\n")
                buf = lines.pop() if len(lines[-1]) <= 4096 else b""
                for line in lines:
                    if line.startswith(b"$"):
                        snap.feed(line)
        except OSError as exc:
            return {"state": "unavailable", "error": f"device unreadable ({type(exc).__name__})"}
        finally:
            if fd >= 0:
                try:
                    _os.close(fd)
                except OSError:
                    pass
        got = snap.snapshot()
        got.update(device=plan.device, nmea_ok=True, note="one sample of the idle receiver")
        if got["state"] == "no-data":
            got["note"] = ("data but no NMEA sentences — receiver in binary mode? (a u-blox left in "
                           "UBX mode by gpsd does this)" if raw > 0 and snap.sentences == 0
                           else "no data from the device in the sampling window")
        return got

    @invalidates_snapshot
    def set_gps(self, **fields) -> ActionResult:
        """Show or set the GLOBAL position source (`[gps]` in local.toml).

        This is THE source for every stack that can use a position; per-stack settings only
        turn GPS on or off. With no fields it reports the current setting.
        """
        from . import config as _config
        from .gps import gpsd_owns_device
        if not fields:
            v = self.gps_settings()
            det = [f"  source:  {v['source']}"]
            if v["source"] == "auto":
                det.append("  auto:    using gpsd on localhost:2947  (found running)"
                           if v["resolved_source"] == "gpsd" else
                           "  auto:    no gpsd on this box — running without position")
            if v["source"] == "gpsd":
                det.append(f"  gpsd:    {v['host']}:{v['port']}"
                           + ("  (local)" if v["local_gpsd"] else "  (remote)"))
            if v["source"] == "nmea":
                det.append(f"  device:  {v['device']} @ {v['nmea_baud']} baud")
            if v["source"] == "fixed":
                det.append("  fixed position configured")     # never echo coordinates
                if "graywolf" in self._gps_stacks():
                    # graywolf has no fixed GPS mode (its API takes
                    # serial/gpsd/none only), so `fixed` reaches it as GPS OFF while its
                    # use_gps switch reads on. Not a refusal — a fixed box would then never
                    # start graywolf with the default switch — but it must be SAID.
                    det.append("  note: graywolf has no fixed GPS mode — it runs with GPS off "
                               "under this source; a fixed station's coordinates belong to "
                               "its beacons (set them in graywolf's web UI)")
            if not v["valid"]:
                det.append(f"  INVALID: {v['reason']} — position is DISABLED (fail closed)")
            elif not v.get("plan_valid", True):
                det.append(f"  UNUSABLE: {v['plan_reason']} — no stack can start with GPS on")
            for sid, keys in self.legacy_gps_values().items():
                det.append(f"  note: {sid} still has old per-stack position values on disk "
                           f"({', '.join(keys)}) — IGNORED; this setting is authoritative")
            det.append("  choose with: lhpc gps --source <" + "|".join(_config.GPS_SOURCES) + ">")
            return ActionResult(True, f"position source: {v['source']}", details=det)

        # The liveness check runs UNDER the config lock, not before it: a start completing in
        # between would leave a running stack whose claims and generated config were derived
        # from the source we just replaced.
        blocked: list = []

        def _recheck() -> str:
            # ONE fresh snapshot, taken here — under the config lock — so a start that completed
            # between the caller's read and this point is seen, and every component is judged
            # against the same picture.
            blocked[:] = self.gps_consumers_running(snap=self._gps_fresh_snapshot())
            if not blocked:
                return ""
            return ("in use by: " + ", ".join(blocked))

        try:
            _config.save_gps(self._paths, recheck=_recheck, **fields)
        except _config.ConfigError as exc:
            if blocked:
                return ActionResult(
                    False,
                    "cannot change the position source while it is in use by: "
                    + ", ".join(blocked),
                    details=["  a running stack holds claims and a config derived from the "
                             "CURRENT source",
                             *(f"  stop it first:  lhpc stack stop {sid}" for sid in blocked)])
            return ActionResult(False, f"could not save GPS settings: {exc}")
        except OSError as exc:
            return ActionResult(False, f"could not save GPS settings: {exc}")
        self._invalidate_config()

        v = self.gps_settings()
        details = [f"  source: {v['source']}"]
        if not v["valid"]:
            # Saved values validate individually, so this means the SECTION is inconsistent.
            return ActionResult(False, f"GPS settings rejected on reload: {v['reason']}")
        # A direct device that gpsd already owns is the one combination that looks fine and
        # then fails intermittently — two readers on one receiver. Warn at set time.
        if v["source"] == "nmea" and v["device"]:
            owned, detail = gpsd_owns_device(v["device"], v["host"], v["port"])
            if owned is True:
                details.append(f"  WARNING: local gpsd already owns {detail} — starting a stack in "
                               "this mode will be refused; use --source gpsd instead")
            elif owned is None:
                details.append(f"  note: could not check whether gpsd owns {v['device']} ({detail})")
        return ActionResult(True, f"position source set to {v['source']}", details=details)

    def gps_plan(self, target: str = ""):
        """THE resolved GPS plan. One object, computed from `[gps]` PLUS the target stack's
        persisted `use_gps` switch, shared by run order, claims, config rendering, post-steps,
        status, stop and boot restore — so no two of them can act on a different idea of where
        position comes from, or of whether this stack uses it at all.

        Without `target` the GLOBAL source is reported as-is (what `lhpc gps` shows).
        """
        from .gps import plan_from_config
        plan = plan_from_config(self.config())
        if target and plan.enabled and not self.gps_enabled_for(target):
            # The stack opted out: it opens nothing, claims nothing, renders no device and
            # pushes "no GPS" to its node. Same shape as a global `off`, scoped to one stack.
            return plan.disabled_for_stack()
        return plan

    def refresh_gps_auto(self) -> None:
        """Request-boundary freshness for the `auto` GPS verdict.

        The verdict is FROZEN into each loaded config (one operation = one decision), which a
        long-lived console would otherwise hold forever — a gpsd installed later would never
        be noticed until an unrelated save dropped the cache. Called per web request (like
        `invalidate_snapshot`): re-probe, and if the verdict changed, drop the cached config
        so THIS request loads a fresh one. Skips silently while any transition or save holds
        the config lock — an applied start keeps its frozen verdict to the end.
        """
        cfg = self._config
        g = getattr(cfg, "gps", None) if cfg is not None else None
        if g is None or g.source != "auto":
            return
        from .gps import local_gpsd_listening
        if bool(g.auto_listening) == local_gpsd_listening():
            return
        import fcntl

        from . import runtime_fs
        try:
            fh = runtime_fs.open_lock(self._paths, self._paths.under("config", ".lock"))
        except (OSError, PathContainmentError):
            return
        try:
            try:
                fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                return                  # a transition/save is running — its verdict stands
            self._invalidate_config()
        finally:
            fh.close()

    def gps_enabled_for(self, target: str) -> bool:
        """The stack's PERSISTED `use_gps` switch (default = the manifest's, which is "on").

        Stored and read BANDLESSLY. "Does this box report its position" is a property of the
        stack, not of the frequency it happens to be on. Reading it per band made the switch
        revert on a band change; reading "on if ANY band says on" then made turning it OFF on
        one band ineffective. One value, one answer.

        `target` may be a component id — it is normalized to its owner stack, so a direct
        component start is gated exactly like a stack start.

        A saved setting only (as every setting is): a per-launch value would let a launch differ
        from the saved state that claims and generated config were derived from.
        """
        stack_id = self.gps_owner_stack(target)
        if not stack_id:
            return False
        try:
            cfg = self._stack_config_cached(stack_id)          # bandless
        except (OSError, ValueError, KeyError, ConfigError):
            return False
        raw = str(cfg.get("use_gps", "")).strip().lower()
        if raw in ("on", "off"):
            return raw == "on"
        # Unset = the MANIFEST default, not a hardcoded "off": the switch defaults to "on"
        # (position via `auto` when a gpsd exists), and the Settings page already
        # renders that default — reading unset as off here would make the pages show "on"
        # while every start behaved as off.
        from .gps import use_gps_default
        return use_gps_default(self.stacks(), stack_id) == "on"

    # The GPS switch param. A stack is GPS-capable IFF one of its components declares it —
    # see _gps_stacks(); there is deliberately no second list to keep in sync.
    GPS_PARAM: ClassVar[str] = USE_GPS_PARAM

    def _gps_stacks(self) -> frozenset:
        """Stacks whose components can consume a position: those declaring the `use_gps` param.

        DERIVED, never hardcoded. A literal list drifted the moment a stack gained the param:
        graywolf declared `use_gps`, the list did not name it, so `gps_enabled_for` answered
        False for a box whose saved value was "on" — the start form's honest echo of "on" then
        looked like a per-start change and EVERY start was refused. The manifest is the only
        place that knows, so it is the only place that decides.

        Cached for the life of `self._stacks` (memoized, never invalidated): this sits under
        `gps_enabled_for`/`gps_plan`, which run dozens of times per page render and several
        times per start — a per-call manifest scan turned an O(1) membership test into the
        hottest loop on a Pi Zero.
        """
        if self._gps_stacks_cache is None:
            self._gps_stacks_cache = frozenset(
                s.id for s in self.stacks()
                for c in s.components
                if any(p.name == self.GPS_PARAM for p in c.run_params))
        return self._gps_stacks_cache

    def gps_owner_stack(self, target: str) -> str:
        """The GPS-capable stack a target belongs to, or "".

        A start may name a COMPONENT (`sideband`, `meshcom-qemu`, `meshtastic-gps`). Without
        normalizing, every GPS decision — feed admission, the start gate, the receiver claim —
        silently did nothing for those targets.
        """
        if not target:
            return ""
        gps_stacks = self._gps_stacks()
        if target in gps_stacks:
            return target
        try:
            owner = self._owner_stack_id(target)
        except (KeyError, AttributeError):
            owner = ""
        return owner if owner in gps_stacks else ""

    def _gps_components_for(self, target: str) -> set:
        """GPS feed components this stack must run under the current plan.

        Empty unless the stack has a consumer that needs a device-shaped stream: Meshtastic
        only for `source = gpsd` (it reads `nmea` straight off the real device and uses its
        own fixed-position support), MeshCom whenever GPS is on (its pinned relay speaks only
        to a LOCAL gpsd and cannot serve remote gpsd, direct NMEA, or a fixed position).
        """
        # Accepts a stack OR a component id: a direct consumer start resolves the same feed set
        # as its stack, so both paths describe one plan.
        target = self.gps_owner_stack(target)
        if not target:
            return set()
        if not self._meshcore_consumes_position(target):
            return set()
        plan = self.gps_plan(target)
        if not plan.enabled:
            return set()
        cid = self._production_feeds().get(target)
        if not cid or not plan.needs_bridge(target):
            return set()
        # Only admit a component the stack actually declares — a manifest without the feed
        # must not silently gain one.
        s = self.stack(target)
        return {cid} if s and any(c.id == cid for c in s.components) else set()

    def _production_feeds(self) -> dict:
        """Stack id -> the component that carries its PRODUCTION position feed."""
        from .gps import FEED_COMPONENTS
        return dict(FEED_COMPONENTS)

    def _all_gps_feed_ids(self) -> set:
        """Every production feed component id — what a direct start must be checked against."""
        return set(self._production_feeds().values())

    def _gps_consumer_ids(self) -> frozenset:
        """Components that actually READ a position — `reads_position = true` in the manifest.

        A GPS stack contains plenty that does not: `meshcom-bridge` is a TCP relay to the
        daemon and `meshcom-firmware` is a build artifact. Treating "belongs to a GPS stack"
        as "consumes position" started a feed — and claimed the receiver — for components that
        never read one.

        DERIVED, like `_gps_stacks()`, and for the same reason: this was a hardcoded set, and
        it drifted the same way — graywolf became a consumer and the set did not name it, so
        `gps_block()` returned early and graywolf started position-blind past the very gate
        built to refuse that. The flag lives on the component because the `use_gps` param
        cannot say who reads: reticulum declares it on `rns`, but `sideband` is the reader.
        """
        if self._gps_consumers_cache is None:
            self._gps_consumers_cache = frozenset(
                c.id for s in self.stacks() for c in s.components if c.reads_position)
        return self._gps_consumers_cache

    def _gps_run_order_uses_position(self, target: str) -> bool:
        """Does what this start would ACTUALLY bring up read a position, or feed one?

        The owner stack is the wrong unit. `meshcom-bridge` and `meshcom-firmware` belong to a
        GPS stack and read nothing; the fixture relay replays a checked-in file; and a Reticulum
        start whose run order is just `rns` — Sideband not selected — touches no receiver either.
        Gating and claiming on stack membership refused those starts over settings they never
        use, and took the receiver away from something that would have used it.
        """
        order = self._run_order(target)
        if not order:
            return False
        ids = {c.id for _s, c in order}
        consumers = self._gps_consumer_ids()
        if not self._meshcore_consumes_position(self.gps_owner_stack(target)):
            consumers = consumers - {_meshcore_mode.NODE_ID}
        return bool(ids & (consumers | self._all_gps_feed_ids()))

    def _meshcore_consumes_position(self, stack_id: str, mode: str | None = None) -> bool:
        """False only for the MeshCore stack in repeater-only mode (the SAVED mode, which is
        what a start renders; pass the RUNNING mode for questions about a live process).
        `reads_position` on the node is static; the mode decides whether anything reads it."""
        if stack_id != _meshcore_mode.STACK_ID:
            return True
        return _meshcore_mode.position_consumed(
            self.meshcore_mode() if mode is None else mode)

    # The MeshCom fixture relay replays a CHECKED-IN synthetic NMEA file. It is a test
    # facility, never a position source, and it writes to the same UART socket as the
    # production feed.
    _FIXTURE_FEEDS: ClassVar[dict] = {"meshcom": "meshcom-gps-relay"}

    def optional_role(self, c) -> str:
        """THE single rule for an optional component's place on the operator's surfaces —
        "hidden" | "listed" | "tickable":
          * hidden   — nothing an operator runs: a library/firmware (nothing to run), a production
                       GPS feed (the resolved position plan is its only admitter) or a `test_fixture`
                       (runs deliberately, by name, never with the stack);
          * listed   — shown on the Settings card as "run on demand": a one-shot/interactive
                       CLIENT (the MeshCore CLI, a REPL in the operator's terminal) is run by name,
                       never "with the stack";
          * tickable — a SERVICE the operator may auto-start with the stack (`autostart_<id>`;
                       an INTERACTIVE service such as nomadnet stays a choice: the start plans it
                       and prints its launch line, MANUAL_REQUIRED, instead of spawning it).
        A saved `autostart_<id>` flag counts ONLY for a tickable component — the Settings list
        and the run-order admission both derive it from here (one rule, so a stale FIXTURE tick
        can never replay a synthetic position and no surface offers a tick another never shows).
        Non-optional -> "hidden"."""
        from .model import ComponentKind
        if (not c.optional or c.kind in (ComponentKind.LIBRARY, ComponentKind.FIRMWARE)
                or c.test_fixture or c.id in self._all_gps_feed_ids()):
            return "hidden"
        return "tickable" if c.kind == ComponentKind.SERVICE else "listed"

    def _gps_components_excluded(self, target: str) -> set:
        """Feed components that must not run under the current plan.

        The fixture relay runs only when named directly (`lhpc stack start meshcom-gps-relay`)
        — that explicitness is what makes it "test-only"; it is never an auto-start choice
        (see `optional_role`). What it must never do is run BESIDE the production
        feed: both write the same UART socket, so the node would receive a synthetic position
        interleaved with the real one and there would be no way to tell which it beaconed.

        When the global source is configured, production wins. Kept as a belt even though the
        fixture can no longer enter `allowed_optional` — the rule is about the UART socket,
        not about how the fixture got into the order.
        """
        target = self.gps_owner_stack(target)                 # stack OR component id
        cid = self._FIXTURE_FEEDS.get(target)
        if not cid:
            return set()
        return {cid} if self._gps_components_for(target) else set()

    def _gps_device_claim(self, target: str) -> str:
        """The exclusive resource key for the local receiver this stack would open, or "".

        Empty for every source that opens no local device (off / fixed / remote gpsd) and for
        stacks with no GPS consumer — claiming a device they never touch would refuse
        combinations that are perfectly valid.
        """
        raw, target = target, self.gps_owner_stack(target)
        if not target:
            return ""
        # Claim only what this start actually brings up. Stack membership is not enough: a
        # Reticulum start without Sideband, or a `meshcom-bridge`/`meshcom-firmware`/fixture run,
        # reads no position and must not take the receiver from something that would.
        if not self._gps_run_order_uses_position(raw):
            return ""
        try:
            plan = self.gps_plan(target)
        except (OSError, ValueError, AttributeError):
            return ""
        if not plan.enabled or not plan.claims_device or not plan.device_key:
            return ""
        return f"gps.{plan.device_key}"
