"""start/stop/restart/build/test orchestration, job/log management, and dashboard/status views.

Mixin of ControllerService (state/constants on the facade). Adapters import lhpc.core.services only."""
from __future__ import annotations

import contextlib
import dataclasses
import json
import os
import re as _re
import time
from pathlib import Path

from . import config as _config
from . import (
    daemon_control,
    gps,
    procident,
    runtime_fs,
    validators,
)
from . import meshcore_mode as _meshcore_mode
from . import openhop_store as _ohs
from . import resources as resources_mod
from . import restart_required as _rr
from . import reticulum_interfaces as _ri
from . import rflog as _rflog
from .daemon_control import ALLOWED_BANDS
from .model import ComponentKind, ResourceMode, RunState
from .outcomes import CompResult, Outcome
from .paths import PathContainmentError
from .service_base import (
    ActionResult,
    AdmissionRefused,
    SourceTxnBlocked,
    admission_refusal,
)
from .snapshot_memo import invalidates_snapshot
from .start_plan import (
    StartRun,
    prepare_start,
    residual_reason,
    rollback_ids,
    verify_start,
)

# docs/architecture.md, SPI policy: the maintainer's wording, shown where the pair is admitted.
SPI_SHARED_WARNING = ("daemon 433 and meshtastic 868 share one SPI bus; transmitting at the same "
                      "moment can crash meshtasticd (measured) — avoid simultaneous transmission")

# A HMAC-apply build-log base is a strict controller-generated prefix bound to the FULL 32-hex run id;
# validated in build() BEFORE any path is constructed (marker-time validation alone is too late).
_HMAC_LOG_BASE_RE = _re.compile(r"^hmac-apply-[0-9a-f]{32}$")


def _daemon_presence(cmdlines) -> str:
    """"none" | "some" | "indeterminate" from ONE process-table snapshot. An empty table is
    indeterminate (`cmdlines()` returns {} when /proc cannot be listed; a readable /proc always
    holds the caller). Any argv token whose basename is `loraham_daemon` is some daemon, whatever
    its `--radio` and whatever argv[0] is (a wrapper)."""
    import posixpath
    if not cmdlines:
        return "indeterminate"
    if any(posixpath.basename(tok) == "loraham_daemon"
           for argv in cmdlines.values() for tok in argv):
        return "some"
    return "none"


class LifecycleOpsMixin:

    def _band_limited_running(self, snap, conservative: bool = False):
        """(limited_fn, running_components, running_ids) with each running component's
        `loraham.radio.<band>` claims restricted to the band(s) it ACTUALLY uses — the daemon to
        the bands it currently serves, a band-switchable app to its effective band, a fixed-band
        app to its band. So a daemon on 433 and meshtastic on 868 are NOT seen as one radio.

        `conservative` selects what an unmarked multi-band owner claims, and the two callers
        genuinely differ:
          * DISPLAY (`_observed_conflicts`) -> False. Its declared band. Claiming both would
            render false 433+868 conflicts for a stack that is plainly on one of them — a
            reported bug, and a scary red banner is not a safety measure.
          * ADMISSION (`_running_conflicts`) -> True. EVERY band it could be on, because
            admitting a second exclusive owner onto a radio already in use is a real
            collision. Unknown must cost a refusal, not a silent overlap.
        """
        # Daemon radio ownership is PROCESS topology (a dead CONF socket does not free the radio),
        # not socket reachability.
        served = self._daemon_claimed_bands()

        def limited(c, eff_bands):
            if c.id != self.DAEMON_ID and not c.bands:
                return c                                  # single-fixed-band: unchanged
            # Only the band(s) this component actually uses; an unknown band strips every radio claim.
            return resources_mod.limit_radio_claims(c, eff_bands)

        running, running_ids = [], set()
        for ss in snap.stacks:
            for c in ss.stack.components:
                if ss.components[c.id].run_state not in (RunState.RUNNING, RunState.DEGRADED):
                    continue
                if c.id == self.DAEMON_ID:
                    eff = served
                elif c.bands:
                    # Actual running band; with none, per `conservative` above.
                    if conservative:
                        eff = self._live_bands(ss.stack.id, c.band)
                    else:
                        eb = self._effective_band(ss.stack.id, c.band)
                        eff = {eb} if eb in ALLOWED_BANDS else set()
                else:
                    eff = {c.band} if c.band else set()
                running.append(limited(c, eff))
                running_ids.add(c.id)
        return limited, running, running_ids

    def _observed_conflicts(self, snap=None):
        """Band-aware observed resource conflicts (both claimants running, radio claims limited to
        the band each actually uses). Replaces the raw, band-blind `snap.conflicts` for display."""
        snap = snap if snap is not None else self.build_snapshot()
        _, running, running_ids = self._band_limited_running(snap)
        return [c for c in resources_mod.interpret_conflicts(running, running_ids) if c.observed]

    def observed_conflicts(self, snap=None):
        """PUBLIC read-only band-aware observed resource conflicts — the single source of truth for
        every UI (CLI status, doctor, /stacks, /stacks/<id>). Never renders a false 433/868 conflict
        (a daemon serving only 433 does not conflict with a direct-radio owner on 868)."""
        return self._observed_conflicts(snap)

    def _running_conflicts(self, comp, band: str = "") -> list[str]:
        """Observed conflicts that would block starting `comp` right now. Radio
        claims are matched by the band each side actually uses, so a multi-band app
        on 868 does not conflict with a daemon serving only 433 (and vice-versa)."""
        snap = self.build_snapshot()
        # conservative=True: this decides whether a start is BLOCKED. An unmarked
        # multi-band owner must be treated as occupying every band it could be on.
        limited, running, running_ids = self._band_limited_running(snap, conservative=True)
        target = limited(comp, {band} if band else set())
        conflicts = resources_mod.interpret_conflicts([*running, target], running_ids | {comp.id})
        # Advisory conflicts (e.g. the Companion client slot) are shown but never block a start —
        # arbitration is at runtime (the WebUI yields to a running CLI via a lock), so admission
        # must let the start through. observed_conflicts() still reports them for the UI banner.
        return [c.message for c in conflicts
                if comp.id in c.holders and c.observed and not c.advisory]

    def gps_feed_state(self, comp) -> dict:
        """The feed's own readiness marker, or {} when absent/unreadable.

        Read from the marker rather than probed: whether a POSITION is flowing is something
        only the feed knows, and the endpoint path exists from the instant it is created.
        """
        from .gps import bridge_state_dir, consumer_for_component
        # DERIVED from the ONE feed mapping, never a private copy — see gps.FEED_COMPONENTS.
        consumer = consumer_for_component(comp.id)
        if not consumer:
            return {}
        marker = Path(bridge_state_dir(self._paths.runtime_root, consumer)) / "readiness.json"
        try:
            raw = runtime_fs.read_text_regular(self._paths, marker, max_bytes=4096)
            got = runtime_fs.loads_json(raw)
        except (OSError, ValueError, PathContainmentError):
            return {}
        if not isinstance(got, dict):
            return {}
        # A marker from a PREVIOUS run must never speak for this one. A persisted
        # `state=ready` (with `updated` in the past, or from a pid that is gone) would
        # otherwise approve a start whose feed had not delivered anything at all.
        if not self._gps_marker_is_current(got):
            return {}
        return got

    def _gps_marker_is_current(self, got: dict) -> bool:
        """Is this readiness marker written by a live feed, recently? Fresh AND a usable owner
        pid AND that pid alive (signal 0) — the same three rules status.py applies."""
        if not gps.marker_is_fresh(got, time.time()):
            return False
        pid = gps.marker_owner_pid(got)
        return pid is not None and procident.pid_exists_signal0(pid)

    def _gps_feed_admission(self, comp) -> tuple[bool, str]:
        """The START gate's verdict on a GPS feed: `_gps_feed_ready`, softened for `auto`.

        Freezing the auto verdict was not enough — a gpsd that disappeared (or
        owns no receiver and streams nothing) after admission left the feed in
        starting/source-lost, the gate refused it, the feed was STOPPED, and the stack did
        not start: a refusal produced purely by `auto`, which promises "no gpsd → run
        without position, never a refusal". Under `auto` only, a LIVE feed whose upstream is
        missing is NON-GATING: the stack starts without position and the feed stays up (its
        pump keeps reconnecting, so a gpsd or receiver appearing later starts delivering
        without a restart). An EXPLICIT gpsd source keeps today's fail-closed refusal, and a
        dead feed process or unpublished marker still fails normally — only the two
        upstream-missing states are soft.
        """
        ready_ok, ev = self._gps_feed_ready(comp)
        if ready_ok:
            return True, ev
        if getattr(getattr(self.config(), "gps", None), "source", "") == "auto":
            st = self.gps_feed_state(comp) or {}
            if str(st.get("state", "")) in ("starting", "source-lost"):
                return True, ("running WITHOUT position (auto: "
                              f"{st.get('detail') or st.get('state')}; the feed stays up "
                              "and reconnects if gpsd delivers later)")
        return False, ev

    def _gps_feed_ready(self, comp) -> tuple[bool, str]:
        """(ok, evidence) for a GPS feed at START time, polled within the readiness budget."""
        budget = getattr(comp, "readiness_timeout", 0.0) or self.ENDPOINT_VERIFY_TIMEOUT_S
        waited, last = 0.0, "no readiness marker"
        while True:
            st = self.gps_feed_state(comp)
            state = str(st.get("state", "")) if st else ""
            if state == "ready":
                return True, f"position source live ({st.get('sentences', 0)} sentences)"
            if state == "connected":
                # A receiver that has been off or indoors can need minutes for a first fix;
                # refusing here would make a cold start look like a broken one.
                last = "source reachable, waiting for a fix"
            elif state:
                last = f"source not reachable ({st.get('detail') or state})"
            if budget <= 0 or waited >= budget:
                return (state == "connected"), last
            time.sleep(self.ENDPOINT_VERIFY_POLL_S)
            waited += self.ENDPOINT_VERIFY_POLL_S

    def _meshcore_node_live(self) -> bool:
        """Is the MeshCore node process up (RUNNING/DEGRADED in the current snapshot)?"""
        from . import meshcore_mode as _mm
        snap = self._snapshot_or_none()
        for ss in (snap.stacks if snap is not None else ()):
            if ss.stack.id == _mm.STACK_ID:
                st = ss.components.get(_mm.NODE_ID)
                return getattr(st, "run_state", None) in (RunState.RUNNING, RunState.DEGRADED)
        return False

    def _meshcore_mode_refusal(self, target: str):
        return _meshcore_mode.start_refusal(self, target)      # the rules: meshcore_mode.py

    def _ready_endpoints_present(self, comp) -> tuple[bool, list[str]]:
        """Probe a component's `ready = true` endpoints (bounded). Returns
        (all_present, evidence-lines). Only endpoints explicitly marked ready
        participate — reference/client/data endpoints never gate."""
        from . import meshcore_mode as _mm
        from .probes.endpoints import tcp_endpoint_present
        from .probes.unixsock import probe_socket
        # The MeshCore node's ready endpoints depend on its mode (5000 in chat modes, 8000 in
        # repeater modes) — the SAME selection ongoing status applies.
        ready = [e for e in _mm.expected_endpoints(comp, self.meshcore_mode()) if e.ready]
        if not ready:
            return True, []
        def snapshot() -> tuple[bool, list[str]]:
            ev, ok_all = [], True
            for e in ready:
                if e.kind == "tcp":
                    # Host/family-aware: a wrong-family/host listener on the same port
                    # does NOT satisfy readiness.
                    present, line = tcp_endpoint_present(self._system, e.address)
                    ev.append(line)
                elif getattr(e, "external", False):
                    # External endpoints are observe-only and NEVER gate readiness.
                    continue
                else:
                    # A RELATIVE unix/path endpoint address is runtime-root-relative —
                    # contained by construction (the manifest never names outside-root
                    # paths LHPC-side; the daemon's own /tmp sockets are `external`).
                    addr = e.address
                    if not Path(addr).is_absolute():
                        try:
                            addr = str(self._paths.under(*Path(addr).parts))
                        except PathContainmentError:
                            addr = ""
                    if not addr or not self._paths.contains(Path(addr)):
                        # A ready endpoint must be runtime-contained unless explicitly
                        # external — an outside-root endpoint can never gate.
                        present = False
                        ev.append(f"{e.address}: rejected (ready endpoint not "
                                  "runtime-contained)")
                    elif e.kind == "unix":
                        present = probe_socket(self._system, addr).is_socket
                        ev.append(f"{addr}: {'present' if present else 'absent'}")
                    else:
                        present = self._system.fs.exists(addr)
                        ev.append(f"{addr}: {'present' if present else 'absent'}")
                ok_all = ok_all and present
            return ok_all, ev
        # A slow-booting app (e.g. a Python node that imports heavy libs before opening its
        # port) can need longer than the global default to become ready. Honour a
        # per-component `readiness_timeout` override when set, so one slow component gets a
        # longer window WITHOUT lengthening every other component's start-failure latency.
        budget = getattr(comp, "readiness_timeout", 0.0) or self.ENDPOINT_VERIFY_TIMEOUT_S
        waited = 0.0
        while True:
            ok_all, ev = snapshot()
            if ok_all or budget <= 0 or waited >= budget:
                return ok_all, ev
            time.sleep(self.ENDPOINT_VERIFY_POLL_S)
            waited += self.ENDPOINT_VERIFY_POLL_S

    def _radio_not_ready_hint(self, band: str) -> str:
        """Why a reachable daemon can report RADIO=FAILED, ordered by what we can actually observe.
        Bounded and GET-safe: one snapshot read, no subprocess."""
        from .model import ResourceKind, RunState
        try:
            snap = self.build_snapshot()
            # Anything LIVE that drives the radio hardware itself — the SPI bus or a band — except
            # the daemon. meshtasticd is the real-world case: it claims the BAND (not the bus), so a
            # bus-only test would miss exactly the process that matters. DEGRADED counts as live:
            # the process is running with an endpoint/readiness missing, and it still holds the
            # hardware — the same rule every other ownership path here applies.
            hw = (ResourceKind.SPI_BUS, ResourceKind.RADIO_BAND)
            live = (RunState.RUNNING, RunState.DEGRADED)
            spi_users = sorted({
                c.id for s in snap.stacks for c in s.stack.components
                if c.id != "loraham-daemon"
                and (st := s.components.get(c.id)) is not None
                and st.run_state in live
                and any(r.kind in hw for r in (getattr(c, "resources", ()) or ()))})
        except Exception:
            spi_users = []
        if spi_users:
            return ("another process is on the shared SPI bus (" + ", ".join(spi_users) +
                    ") — its transfers can corrupt the daemon's chip probe; stop it, start this "
                    "stack, then start it again")
        return (f"the {band} module did not answer its probe: check the board is seated and, if it "
                "worked before, POWER-CYCLE the Pi — a wedged RF front-end survives a warm reboot")

    def _conflicting_service_hint(self, comp) -> str:
        """When a component declares an `absent_file` requirement (a must-not-run systemd unit) yet a
        start still reached readiness-failure, the unit can be ACTIVE without being ENABLED — its
        wants-symlink is absent, so `missing_requirements` let start proceed, but the running root
        service still holds the shared radio. Probe `systemctl is-active` for the derived unit and, if
        up, name it as the likely cause with the copyable disable command. Bounded; returns "" when
        nothing conflicting is active (or systemctl is unavailable), so it only ever ADDS signal."""
        for req in comp.requires:
            if not req.absent_file:
                continue
            unit = req.absent_file.rstrip("/").rsplit("/", 1)[-1]   # .../meshtasticd.service -> unit name
            res = self._system.runner.run(["systemctl", "is-active", "--quiet", unit], timeout=3.0)
            if res.returncode == 0 and not res.not_found and not res.timed_out:
                cmd = f" ({req.install})" if req.install else ""
                return (f"; likely cause: the {unit} system service is ACTIVE — "
                        f"{req.note or 'disable it'}{cmd}")
        return ""

    def run_blockers(self, target: str, band: str = "", radio: str = "") -> list[dict]:
        """REAL resource conflicts only: exclusive/provider resources this run would
        use that a RUNNING component of another stack is *actually* using. Radio
        bands are matched by the band each side really uses (a multi-band stack on
        868 does not block another stack on 433; the daemon only conflicts on the
        bands it currently serves). A daemon start's bands come from its REQUESTED radio
        mode (`radio`), not its empty `Component.band`."""
        order = self._run_order(target)
        if not order:
            return []
        order_ids = {c.id for _, c in order}
        target_stack = self.stack_of(target)
        target_is_daemon = bool(target_stack and self.stack(target_stack)
                                and self.stack(target_stack).main == self.DAEMON_ID)
        # Bands this run actually uses (band-aware + daemon-radio-aware).
        needed_bands = self._operation_bands(target, band, radio, "start")
        # Non-radio exclusive/provider claims (ports, sockets, …) + only the radio
        # band(s) the run really needs.
        # An ADVISORY claim never blocks (model.Resource.advisory): the conflict is shown, and the
        # sides arbitrate at runtime — e.g. the MeshCore WebUI yields the node's one Companion slot
        # to a running meshcore-cli. Advisory on EITHER side is enough, as in interpret_conflicts.
        claims: dict[str, str] = {}
        for _, c in order:
            for r in c.resources:
                if r.advisory:
                    continue
                if (r.mode in (ResourceMode.EXCLUSIVE, ResourceMode.PROVIDER)
                        and not r.key.startswith("loraham.radio.")):
                    claims[r.key] = c.id
        for b in needed_bands:
            claims.setdefault(f"loraham.radio.{b}", target)
        # The GPS receiver is a DYNAMIC claim (which device, if any, comes from the resolved
        # plan — not from a static manifest resource). It must be in the CONFLICT set and not
        # only the lock set: the lock is released when the start completes, so locks alone let
        # a second stack start afterwards and open the same receiver.
        _gps_key = self._gps_device_claim(target)
        if _gps_key:
            claims.setdefault(_gps_key, target)

        snap = self.build_snapshot()
        # Daemon radio ownership is PROCESS topology (a dead CONF socket does not free the radio),
        # not socket reachability.
        served = self._daemon_claimed_bands()
        blockers, seen = [], set()

        def add(stack_id, holder, resource):
            key = (stack_id, resource)
            if key not in seen:
                seen.add(key)
                # The radio band a held `loraham.radio.<band>` is on, so a daemon holder can be
                # stopped (and named) per band; "" for any other resource.
                rband = resource.rsplit(".", 1)[-1] if resource.startswith("loraham.radio.") else ""
                blockers.append({"resource": resource, "holder": holder,
                                 "holder_stack": stack_id, "band": rband})

        for ss in snap.stacks:
            sid = ss.stack.id
            multi = bool(self.stack_bands(sid))
            for c in ss.stack.components:
                if c.id in order_ids:
                    continue
                if ss.components[c.id].run_state not in (RunState.RUNNING, RunState.DEGRADED):
                    continue
                # Which radio band(s) is THIS running component actually using?
                if c.id == self.DAEMON_ID:
                    active = served
                elif multi:
                    active = self._live_bands(sid, c.band)   # actual band, else ALL of them
                else:
                    active = {c.band} if c.band else set()
                # A RUNNING GPS consumer holds its receiver for as long as it runs, and that
                # claim exists nowhere in the manifest — derive it, or the second stack sees no
                # holder and opens the device too.
                #
                # From the LIVE COMPONENT, never from its owner stack: the stack's claim is
                # derived from the run order a FUTURE start would use, which is not what is
                # running now. With `autostart_sideband` off, a directly started Sideband is
                # reading the receiver while `reticulum`'s prospective order is just `rns` — so
                # the stack claim was empty and MeshCom was admitted onto the same device the
                # moment Sideband's start lock was released. Turning that autostart off while
                # Sideband ran did the same thing to an already-running reader.
                if sid != target_stack:
                    _owner_gps = self._gps_device_claim(c.id)
                    if _owner_gps and _owner_gps in claims:
                        add(sid, c.id, _owner_gps)
                for r in c.resources:
                    if r.advisory or r.mode not in (ResourceMode.EXCLUSIVE, ResourceMode.PROVIDER):
                        continue
                    if r.key.startswith("loraham.radio."):
                        rb = r.key.rsplit(".", 1)[-1]
                        if rb in active and r.key in claims:
                            add(sid, c.id, r.key)
                    elif r.key in claims:
                        add(sid, c.id, r.key)
                # same-frequency rule: another APP stack competing for a band we need. Exclude the
                # daemon STACK (it provides the radio). Also skip it entirely when the TARGET is the
                # daemon: its own dependent clients are consumers of the radio it provides, not
                # competitors — a real competitor (a direct-radio EXCLUSIVE owner like meshtastic)
                # is already caught by the exclusive-claim check above.
                comp_bands = (self._live_bands(sid, c.band) if multi
                              else ({c.band} if c.band else set()))
                if (not target_is_daemon and sid != target_stack
                        and sid != self.stack_of(self.DAEMON_ID)
                        and comp_bands & needed_bands):
                    add(sid, c.id,
                        f"radio {'/'.join(sorted(comp_bands & needed_bands))} MHz")
        return blockers

    def _spi_shared_warning(self, target: str, band: str = "", radio: str = "") -> str | None:
        """The maintainer's "allow, but warn" for the one shared-SPI pair LHPC admits
        (docs/architecture.md, SPI policy): the daemon on 433 beside a component that drives the
        bus WITHOUT the daemon's spi0.lock (claims `spi.bus.0.unlocked`, not `spi.bus.0`:
        meshtasticd) on 868. Both directions: this start brings one side up while the other runs."""
        def unlocked(c):
            keys = {r.key for r in c.resources}
            return "spi.bus.0.unlocked" in keys and "spi.bus.0" not in keys
        order = self._run_order(target) or []
        if any(unlocked(c) for _, c in order) and self._config_band(target, band) == "868" \
                and "433" in self._daemon_claimed_bands():
            return SPI_SHARED_WARNING
        if any(c.id == self.DAEMON_ID for _, c in order) and "433" in self._daemon_serve_bands(radio):
            for ss in self.build_snapshot().stacks:
                for c in ss.stack.components:
                    if unlocked(c) and ss.components[c.id].run_state in (
                            RunState.RUNNING, RunState.DEGRADED) and "868" in (
                            self._live_bands(ss.stack.id, c.band)
                            if self.stack_bands(ss.stack.id) else {c.band}):
                        return SPI_SHARED_WARNING
        return None

    def gps_block(self, target: str) -> tuple[str, list]:
        """(reason, next_commands) when this stack must NOT start under the current GPS plan.

        Three refusals, all fail-closed:

        * the `[gps]` section is malformed — position is already disabled, and a stack the
          operator believes is GPS-enabled would start silently blind;
        * direct NMEA on a device the local gpsd already owns — two readers on one receiver
          do not fail cleanly, they lose fixes intermittently;
        * direct NMEA whose ownership cannot be PROVEN either way — inability to prove it safe
          is a refusal, never an assumption that it is free.

        Empty for stacks with no GPS consumer, and for `off`/`fixed`/remote gpsd, which open
        no local device at all.
        """
        from .gps import gpsd_owns_device
        # A start may name a COMPONENT (sideband, meshcom-qemu). Normalizing to the owner
        # stack is what makes a direct component start obey the same gate as its stack.
        raw = target
        target = self.gps_owner_stack(target)
        if not target:
            return "", []
        # GPS policy applies to what this start ACTUALLY brings up, not to stack membership.
        # `meshcom-bridge`, `meshcom-firmware` and the fixture relay read no position, and a
        # Reticulum start without Sideband reads none either — refusing those over GPS settings
        # they never consult blocks perfectly valid work.
        if not self._gps_run_order_uses_position(raw):
            return "", []
        # A PRODUCTION FEED started on its own is only meaningful when the stack's plan actually
        # calls for it. Run outside that plan it publishes an endpoint and claims the receiver
        # for a consumer that is not coming — and, with the source off, delivers nothing while
        # looking healthy. The fixture relay is deliberately NOT covered here: it runs when
        # named directly, which is its whole contract (see `optional_role`).
        if (raw != target and raw in self._all_gps_feed_ids()
                and raw not in self._gps_components_for(target)):
            return (f"'{raw}' is the GPS feed for '{target}', and the current position plan "
                    f"does not use it — starting it alone would claim the receiver for a "
                    f"consumer that is not running",
                    ["lhpc gps", f"lhpc config {target} use_gps on",
                     f"lhpc stack start {target}"])
        cfg = self.config()
        g = getattr(cfg, "gps", None)
        if g is not None and not getattr(g, "valid", True):
            return (f"the global position source is invalid ({g.reason}) and has been disabled — "
                    "fix it or set it to off", ["lhpc gps"])
        # `use_gps = on` with the source `off` (or `auto` finding nothing) is NOT a refusal:
        # with the switch defaulting to ON, "on" does not prove the operator asked
        # for position, so refusing would break every stack on a box with no gpsd — the stack
        # starts WITHOUT position instead, and the GPS card/`lhpc gps` say why. Fail-closed
        # protection now lives with EXPLICIT intent: a named source that cannot be used
        # (malformed section above, unresolvable plan below) still refuses.
        plan = self.gps_plan(target)
        # VALIDITY FIRST. An unresolvable plan (a `nmea` device that does not exist, or is not a
        # character device) is REPRESENTED as `source = off`, so testing `enabled` first returned
        # success for it: the stack started with no feed, no receiver claim and no warning —
        # silently position-blind — while `lhpc gps` still reported `source: nmea`.
        if not plan.valid:
            return (f"the global position source could not be resolved: {plan.reason}",
                    ["lhpc gps"])
        if not plan.enabled:
            return "", []
        if not plan.claims_device:
            return "", []
        # Ask the LOCAL gpsd, never the retained host. In direct-NMEA mode `host`/`port` are
        # leftovers from whenever gpsd was last configured; if that was a remote box, querying
        # it would ask a machine that cannot possibly hold THIS receiver and cheerfully report
        # the device free while a local gpsd was holding it.
        owned, detail = gpsd_owns_device(plan.device, "127.0.0.1", 2947)
        if owned is True:
            return (f"the position source reads {plan.device} directly, but the local gpsd already "
                    "owns that receiver — two readers on one device lose fixes intermittently; "
                    "use the gpsd source instead",
                    ["lhpc gps --source gpsd"])
        if owned is None:
            return (f"cannot prove that {plan.device} is free for direct use ({detail}) — refusing "
                    "rather than risking two readers on one receiver",
                    ["lhpc gps --source gpsd"])
        return "", []

    def hardware_block(self, target: str) -> str:
        """A reason string when NO radio hardware is configured and this stack needs a radio, else "".
        Fresh installs default to 'unset' — the daemon must not start until the operator picks a board
        in the daemon Hardware settings. The daemon itself and any band-declaring stack are gated; a
        stack that needs no radio is not. Remedy = configure hardware, not stop a holder."""
        if self.hardware_configured():
            return ""
        order = self._run_order(target)
        if order is None:
            return ""
        needs_radio = self._is_daemon_target(target) or any((c.band or c.bands) for _, c in order)
        if not needs_radio:
            return ""
        return ("no radio hardware configured — choose your board in the daemon Hardware settings "
                "before starting a radio stack")

    @invalidates_snapshot
    def probe_hardware(self, band: str, hw_preset: str):
        """Bounded, safe hardware probe for the daemon Hardware page: spawn the daemon for
        (band, hw_preset) and report present / absent (+ the daemon's chip diagnostic) / busy. Guards:
        valid band, known preset, daemon BUILT. Reuses the real runtime lock dir so an already-running
        daemon on the band reports BUSY, never gets its radio stolen. A direct-radio stack (meshtastic,
        reticulum) holds no daemon lock, so the probe takes the band's start locks like a daemon start
        and refuses, naming the stack, while one owns the band."""
        from . import config as _config
        from . import reslock
        from .probes import hardware as hwprobe
        if band not in self.RADIO_BANDS:
            return hwprobe.ProbeResult(False, False, f"invalid band {band!r}")
        if hw_preset not in _config.HW_PRESETS:
            return hwprobe.ProbeResult(False, False, f"unknown hardware preset {hw_preset!r}")
        s = self.stack("daemon")
        comp = s.main_component if s else None
        if comp is None or comp.source is None:
            return hwprobe.ProbeResult(False, False, "daemon component not found")
        if not self.is_built(comp):
            return hwprobe.ProbeResult(False, False, "daemon not built — build it first (lhpc build daemon)")
        life = self._lifecycle()
        src = life.source_dir(comp)
        binary = str(src / comp.bin) if comp.bin else str(src / "loraham_daemon" / "loraham_daemon")
        runtime_dir = str(self._paths.runtime_root / "state" / "loraham")
        try:
            with self._keys_guard("hardware-probe", "daemon",
                                  ["lifecycle.daemon", f"claim.loraham.radio.{band}"]):
                owner = self._direct_radio_owners({band}).get(band)
                if owner:
                    return hwprobe.ProbeResult(
                        False, True, f"{band} MHz is in use by {self.stack_of(owner) or owner} — "
                                     "stop it before detecting")
                return hwprobe.probe_radio(self._system, binary, str(src), band, hw_preset,
                                           runtime_dir=runtime_dir,
                                           label=_config.hw_preset_label(hw_preset))
        except reslock.ResourceBusy as busy:
            return hwprobe.ProbeResult(False, True, f"{band} MHz is busy ({busy}); try again")

    def radio_mode_block(self, target: str) -> str:
        """A reason string when the active radio mode excludes EVERY band this stack can use, else "".
        A band-switchable stack stays available if ANY of its bands is active (uses the RAW declared
        component bands, not the mode-narrowed `stack_bands`). This is NOT a resource conflict — the
        remedy is to change the radio mode, not stop a holder."""
        order = self._run_order(target)
        if not order:
            return ""
        needed = {b for _, c in order
                  for b in (([c.band] if c.band else []) + list(c.bands)) if b}
        active = set(self.active_bands())
        if needed and not (needed & active):
            missing = "/".join(sorted(needed))
            return (f"requires the {missing} MHz radio — radio mode is {self.radio_mode()}-only; "
                    "change it in the daemon Hardware settings")
        return ""

    def _start_static_refusal(self, target: str, op: str):
        """The start refusals that do not depend on whether the target runs (runtime root,
        hardware, GPS, radio mode), or None — so a restart takes them BEFORE its stop, with the
        same wording as the start."""
        if not self._paths.runtime_root_exists:
            return ActionResult(False, "Runtime root not bootstrapped.",
                                next_commands=["lhpc bootstrap"])
        # Hardware not configured: a fresh box has no radio board selected, so the daemon (and any
        # radio stack) must refuse to start until the operator picks one. Checked BEFORE band checks.
        hw_block = self.hardware_block(target)
        if hw_block:
            return ActionResult(False, f"Cannot {op} '{target}': {hw_block}",
                                next_commands=["lhpc hardware"])
        # GPS admission — BEFORE any lock is taken, so the plan that decides run order, claims
        # and config rendering is the same one validated here. A direct-NMEA source is the case
        # that fails intermittently rather than cleanly (two readers on one receiver), so it is
        # refused up front rather than discovered as flaky position later.
        gps_block, gps_next = self.gps_block(target)
        if gps_block:
            return ActionResult(False, f"Cannot {op} '{target}': {gps_block}",
                                next_commands=gps_next or ["lhpc gps"])
        # Radio-mode availability: a stack whose every band is excluded by the current mode cannot
        # run (remedy = change the mode, not stop a holder). Refused for both dry-run and apply.
        rm_block = self.radio_mode_block(target)
        if rm_block:
            return ActionResult(False, f"Cannot {op} '{target}': {rm_block}",
                                next_commands=["lhpc hardware"])
        return None

    @invalidates_snapshot
    def start(self, target: str, apply: bool = False, stop_owners: bool = False,
              band: str = "", auto_install_ctx=None, *,
              _before_start_locked=None, _operator: bool = True, _ensured: bool = False,
              position: dict | None = None, position_note: str = "") -> ActionResult:
        """Public, LOCKED entry — acquires the full lifecycle lock bundle (incl. owners
        when stop_owners) so a DIRECT call gets the same coordination as CLI/web.
        A start runs EXACTLY the saved configuration: there are no per-launch values (Settings is the only place configuration changes). The plan (`apply=False`) and the apply take
        every decision alike, identity enforcement included, so a refused start is known before
        anything is queued or mutated.

        `_before_start_locked` (PRIVATE, keyword-only; ordinary CLI/web callers pass None) is the
        boot-restore claim hook: invoked ONLY on the apply path, AFTER admission +
        config-stability + the lifecycle/resource bundle are all held and BEFORE any mutation
        (incl. the feed clear) — it revalidates the restore evidence and durably journals the
        attempt; returning an ActionResult cancels the start with ZERO side effects. It must
        never be routed through ambient/thread-local state (Waitress shares this service across
        threads). Lock order (admission → config → lifecycle) is enforced by
        test_hook_runs_after_every_lock_before_any_mutation (tests/test_boot_restore.py)."""
        if (_r := self._controller_refusal(target)) is not None:
            return _r
        if (_r := self._gui_fallback_refusal(target)) is not None:
            return _r
        if (_r := self._meshcore_mode_refusal(target)) is not None:
            return _r
        from . import reslock
        if auto_install_ctx is not None:
            order = self._run_order(target) or []
            ctx_err = self._auto_install_ctx_error(
                auto_install_ctx, {c.source.path for _, c in order if c.source})
            if ctx_err:
                return ActionResult(False, f"Refusing to start '{target}': {ctx_err}")
        if (_bref := self.band_refusal(target, band, "start")) is not None:
            return _bref            # plan and apply take the SAME decision on an explicit band
        if not apply:
            # The plan takes the apply's outer decision (identity, MeshCore position) at the same
            # point and in the same order, so a start the apply would refuse never plans "ok".
            _ob = self.operation_band(target, band)
            _order, _radio = self._order_radio(target, _ob)
            if (_r := self._start_outer_refusal(target, _order, _ob, _radio, position,
                                                position_note)[0]) is not None:
                return _r
            return self._start_impl(target, apply=False, stop_owners=stop_owners, band=band)
        # The guards: admission FIRST (outside the config-stability guard and before every
        # mutation, so a start refused by a pending self-update/uninstall changes NOTHING), then
        # config stability, then the lifecycle/resource bundle.
        try:
            with self._admit_and_acquire_guards("start", target, band, stop_owners,
                                                _ensured) as (_op_band, _order, _radio):
                # The outer recheck under every lock: identity enforcement
                # FIRST, then the MeshCore position snapshot (the one position decision of this
                # start, passed down rather than re-queried, so a restart asks exactly once) —
                # before the boot-restore claim, the feed clearing and any other mutation.
                # The inner PREPARE remains the authoritative backstop for direct callers.
                _r, position, _pos_note = self._start_outer_refusal(
                    target, _order, _op_band, _radio, position, position_note)
                if _r is not None:
                    return _r
                # BOOT-RESTORE CLAIM HOOK: every lock held, nothing mutated yet. A refusal
                # cancels the start with zero side effects.
                if _before_start_locked is not None:
                    _hook_refusal = _before_start_locked()
                    if _hook_refusal is not None:
                        return _hook_refusal
                # The launch's five phases (`_start_coordinator`), on the IMMUTABLE resolved
                # band — never the raw argument a marker change could re-resolve differently.
                _res = self._start_impl(target, apply=True, stop_owners=stop_owners,
                                        band=_op_band, position=position, position_note=_pos_note)
                # The operator's stop intent, from the launch's result.
                if _operator and self._start_supersedes_stop_intent(target, _res):
                    self._clear_stop_intent(target)
                return _res
        except (AdmissionRefused, reslock.ResourceBusy, SourceTxnBlocked, OSError,
                PathContainmentError, _ohs.OneStoreError) as exc:
            return self._guard_refusal("start", target, exc)

    def _restart_ensure_band(self, target: str, band: str) -> str:
        """A restart's band, resolved under admission, after the openHop-file step that must run before
        the restart's shared config-stability guard (`openhop_store.restart_ensure`)."""
        _ohs.restart_ensure(self, target)
        return self.operation_band(target, band)

    @contextlib.contextmanager
    def _admit_and_acquire_guards(self, op: str, target: str, band: str, stop_owners: bool,
                                  ensured: bool = False):
        """The guards of an applied start: task admission → config stability → the lifecycle/
        resource bundle (LOCK ORDER), yielding `(op_band, order, radio)` resolved between them.
        ONE effective band for the WHOLE operation, resolved ONCE under admission and never
        recomputed (a dismiss of the interactive marker in a later window must not move it); ""
        only for a bandless stack. The daemon's REQUESTED radio, read under config stability,
        decides which bands the bundle covers. Raises what the guards raise
        (`_guard_refusal` types it)."""
        with self._admission_guard(op, target):
            op_band = self.operation_band(target, band)
            if not ensured:                              # issue #5: admitted, before the shared guard
                _ohs.start_ensure(self, target, nested=bool(getattr(self._cfg_stable_state, "depth", 0)))
            with self._config_stable():
                order, radio = self._order_radio(target, op_band)
                with self._lifecycle_guard(op, target, op_band, stop_owners=stop_owners,
                                           radio=radio):
                    yield op_band, order, radio

    def _start_supersedes_stop_intent(self, target: str, res: ActionResult) -> bool:
        """Whether an OPERATOR stack start ends a standing stop intent. A REFUSED start (nothing
        launched) does not; a PARTIAL start (e.g. manual_required on an interactive component,
        ok=False but components launched) does — the stack is running, a stale tombstone would
        skip-and-prune it at the next boot. Only rows of the TARGET stack count (the daemon's
        always-appended VERIFIED ensure row cleared the target's tombstone when nothing of the
        target launched at all), and a component a FAILED start stopped again reads its stop
        outcome (`_roll_back_start`), so it does not count either. An INTERNAL start
        (auto-install's daemon ensure, `_operator=False`) is not asked: it must not clear an
        operator's tombstone its paired internal stop never rewrites."""
        return self.stack(target) is not None and (
            res.ok or any(r.action == "start" and r.outcome in
                          (Outcome.STARTED, Outcome.VERIFIED, Outcome.ALREADY_HEALTHY)
                          and (self.stack_of(r.component) or r.stack) == target
                          for r in (res.results or ())))

    @staticmethod
    def _guard_refusal(op: str, target: str, exc: BaseException) -> ActionResult:
        """The typed refusal of a start/restart whose guard could not be taken or held, or whose guarded
        body failed with an OSError/PathContainmentError (mapped to "configuration guard unavailable",
        as before): a refused admission (typed `admission_blocked`), a busy lock or source-txn block."""
        if isinstance(exc, AdmissionRefused):
            return admission_refusal(exc)
        if isinstance(exc, (OSError, PathContainmentError)):
            return ActionResult(False, f"Cannot {op} '{target}': configuration guard unavailable "
                                f"({exc})", next_commands=[f"lhpc status {target}"])
        return ActionResult(False, f"Cannot {op} '{target}': {exc}",
                            next_commands=[f"lhpc status {target}"])

    def _start_impl(self, target: str, apply: bool = False, stop_owners: bool = False,
                    band: str = "", position: dict | None = None,
                    position_note: str = "") -> ActionResult:
        """Applied starts run under the configuration-stability guard so saved config is a stable
        snapshot from the first read through generation/launch/post-start — a direct/internal
        apply=True call cannot skip it. Dry-run holds no long-lived guard. A guard/read failure is
        a TYPED failure returned BEFORE any lifecycle side effect."""
        if not apply:
            prep = self._prepare_from_fresh_evidence(target, band, stop_owners, position,
                                                     position_note, apply=False)
            return prep if isinstance(prep, ActionResult) else self._start_preview(prep)
        try:
            return self._start_coordinator(target, stop_owners, band, position, position_note)
        except (AdmissionRefused, SourceTxnBlocked, OSError, PathContainmentError) as exc:
            # The config-stability guard itself busy (e.g. an auto-install run holds config
            # EXCLUSIVE for its whole lifetime) — refused typed rather than hang or crash.
            return self._guard_refusal("start", target, exc)

    def band_refusal(self, target: str, band: str, op: str):
        """An EXPLICITLY requested band this box cannot serve — or that this stack cannot run on —
        is a TYPED REFUSAL, never a silent remap (`--band 433` on 868-only hardware
        planned and launched 868, and a daemon preview promised 433 while the launch clamped it to
        868). Returns a refusal or None; a bandless request keeps resolving normally, and plan and
        apply take this same decision because both go through the public entry."""
        if not band:
            return None
        active = self.active_bands()
        if active and band not in active:
            return ActionResult(
                False, f"Cannot {op} '{target}' on {band} MHz: this box serves "
                f"{' and '.join(active)}.",
                details=[f"  [blocked] {band} MHz is not served by the configured hardware"],
                next_commands=["lhpc hardware"])
        allowed = self.stack_bands(target)
        if allowed and band not in allowed:
            return ActionResult(
                False, f"Cannot {op} '{target}' on {band} MHz: it runs on "
                f"{' or '.join(allowed)}.", next_commands=[f"lhpc status {target}"])
        return None

    def _launch_band_hint(self, target: str, band: str) -> str:
        """THE band an operation actually applies to, resolved ONCE and shared by identity
        enforcement, materialization, daemon-needs planning, lock/feed scoping and the
        launch itself: the explicit band, else the band the stack is ALREADY running on,
        else — for a stack whose GUI fallback is ACTIVE on this box — the band its
        interactive marker was presented on (a Voice Lite operator who started on 868 must
        not have a bandless second start silently judged/locked on the primary 433), else
        "" (the config resolver then falls back to the declared primary). A stale
        interactive marker never overrides a usable desktop GTK main: the marker is only
        consulted while `gui_fallback_active` says the fallback is what runs here."""
        sid = self.stack_of(target) or target
        got = band or self.running_band(sid, "")
        if got:
            return got
        st = self.stack(sid)
        if st is not None and self.gui_fallback_active(st):
            return self.interactive_band(sid) or ""
        return ""

    def _gui_fallback_refusal(self, target: str):
        """PREFLIGHT for a DIRECT start of voice's terminal fallback (a non-main interactive
        component whose stack main is gui_optional): refused before ANY side effect — the
        boot hook, feed clearing, owner stops, daemon ensure and the already-healthy
        shortcut all come later, and none of them may run for a target whose start can only
        ever be refused. Guards the public entry AND the start PREPARE
        (`_prepare_from_fresh_evidence`), so internal callers (restart, boot restore) are
        covered too. Returns None for every other target."""
        if self.stack(target) is not None:
            return None
        for stack in self.stacks():
            for c in stack.components:
                if c.id != target:
                    continue
                main = next((m for m in stack.components if m.id == stack.main), None)
                if (c.interactive and c.id != stack.main
                        and main is not None and main.gui_optional):
                    return ActionResult(
                        False,
                        f"Cannot start '{target}': it shares {stack.main}'s configuration "
                        f"(callsign, audio device and radio params), which only a stack "
                        f"start generates — run: lhpc stack start {stack.id}",
                        next_commands=[f"lhpc stack start {stack.id}"])
                return None
        return None

    def _start_coordinator(self, target: str, stop_owners: bool, band: str,
                           position: dict | None, position_note: str) -> ActionResult:
        """THE APPLIED START as its five phases in order. PHASE 1 here is the configuration-
        stability guard, re-entrant: the public `start` already holds admission, config
        stability and the lifecycle bundle; a direct `_start_impl` apply holds this one only.
        Each destructive step, its precondition and its failure outcome:
          * PREPARE (`_prepare_from_fresh_evidence`) itself calls no stop, spawn or config-file
            generation (its firewall gate renders the FW-R8 apply script); it
            refuses, or returns the already-healthy no-op, before any stop or spawn.
          * EXECUTE (`_execute_start`): conflicting owners stopped (`stop_owners`) — one that
            does not verify stopped refuses the start with nothing of the target launched; the
            feed floor of its bands reset; then
            the daemon ensure and, per component in run order, its gates, config files,
            pre-steps / interactive marker, RF-log roll, spawn, readiness, post-start and
            running-band marker. A component that fails its readiness, post-start or marker
            check is stopped again on the spot (typed UNVERIFIED). An OSError or
            PathContainmentError escaping it is a FAILED row, and what it launched is rolled
            back like any failed start (`run` is the ledger it fills).
          * VERIFY (`verify_start`) decides the outcome from the typed outcomes alone.
          * A FAILED start rolls back what it launched (`_roll_back_start`).
          * FINALIZE (`_finalize_start`) clears only the state this start satisfied.
        Before this runs, the public `start` takes its guards, its outer recheck (identity,
        MeshCore position) and the boot-restore claim."""
        with self._config_stable():                                             # PHASE 1
            prep = self._prepare_from_fresh_evidence(target, band, stop_owners, position,
                                                     position_note, apply=True)  # PHASE 2
            if isinstance(prep, ActionResult):
                return prep                 # a refusal, or the already-healthy no-op
            run = StartRun(out=[], results=[], presented=set(), daemon_launched=set(),
                           life=self._lifecycle(), pre_marker=None)
            try:
                stopped = self._execute_start(prep, run)                        # PHASE 3
            except (OSError, PathContainmentError) as exc:
                # An error after a launch: what the ledger holds is rolled back below, and the
                # error is the failed row — never the bare guard refusal, which left it running.
                run.results.append(CompResult(
                    component=prep.target, stack=prep.start_sid, action="start",
                    outcome=Outcome.FAILED, summary=f"the start stopped on an error: {exc}"))
                run.out.append(f"  [failed] {prep.target}: the start stopped on an error ({exc})")
                stopped = run
            if isinstance(stopped, ActionResult):
                return stopped              # a conflicting owner did not verify stopped
            failed, required_manual = verify_start(prep, run.results,
                                                   run.presented)               # PHASE 4
            if failed:
                # ROLL BACK (finding 64): a failed start stops again what IT launched — or a
                # typed row names what stayed up and why. A start blocked only on a manual step
                # (chat's TUI) is not failed and keeps its daemon up for the operator.
                run.out += self._roll_back_start(run.life, prep.order, run.results,
                                                 prep.cfg_band, run.daemon_launched)
            return self._finalize_start(prep, run, failed, required_manual)       # PHASE 5

    def _prepare_from_fresh_evidence(self, target: str, band: str, stop_owners: bool,
                                     position: dict | None, position_note: str, *,
                                     apply: bool):
        """PHASE 2 PREPARE: every input of the start resolved once from the saved configuration — on
        the apply under the stability guard `_start_coordinator` takes first, on the plan (`_start_impl`,
        `apply=False`) without it — and the launch's refusals, before its first stop or spawn: the
        PreparedStart, or the ActionResult that ends the start here. The plan stops after the checks it
        shares with the apply; the apply adds the already-healthy no-op and the preflight (firewall gate,
        which renders the FW-R8 apply script; ambiguity; band owners unless `stop_owners`)."""
        order = self._run_order(target)
        if order is None:
            return ActionResult(False, f"Unknown stack or component '{target}'.",
                                next_commands=["lhpc list"])
        if (_r := self._gui_fallback_refusal(target)) is not None:
            return _r
        if (_r := self._meshcore_mode_refusal(target)) is not None:
            return _r
        if (_r := self._start_static_refusal(target, "start")) is not None:
            return _r
        # Band-switchable stack: resolve the chosen band (default = first allowed).
        # An explicit band wins; otherwise inherit the band the stack is ALREADY running
        # on, and only then fall back to the declared primary — otherwise starting an
        # optional client (sideband) inside a stack running on 433 would resolve to the
        # primary (868) and OVERWRITE the running-band marker, and the console would show,
        # and configure, the wrong band for a live 433 node.
        cfg_band = self._config_band(target, self._launch_band_hint(target, band))
        # The OPERATING band (what the radios actually do) is NOT the config-store band. The daemon
        # has no per-band store, so `_config_band` flattens its concrete band to "" — and a start
        # planned, locked and feed-cleared for 433 then asked the daemon for ALL active bands
        # (`_ensure_daemon` received radio="" for `start daemon --band 433`). The two
        # values are equal for every client stack; they differ only for the daemon, which is exactly
        # the case this separation exists for.
        op_band = self.operation_band(target, band)
        # A band-switchable stack runs on ONE band. Asking for the other one while its
        # band owner is up must not be a silent no-op (every component reads
        # already_healthy and the radio stays put). Refuse with the reason and the commands that actually switch it.
        _sid = self.stack_of(target) or target
        _live = self.running_band(_sid, "")
        if band and _live and band != _live and self.stack_bands(_sid) \
                and self._band_owner_is_up(_sid):
            return ActionResult(
                False,
                f"'{_sid}' is already running on {_live} MHz — refusing to start it on "
                f"{band} MHz.",
                details=["  [blocked] a band-switchable stack runs on ONE band at a time",
                         f"  Stop it first, then start it on {band} MHz."],
                next_commands=[f"lhpc stack stop {_sid} --yes",
                               f"lhpc stack start {_sid} --yes"])
        # The SAME rule, one stack removed: a chain member of ANOTHER stack already up on a
        # different band. Checked on the band this start will ACTUALLY use (cfg_band), not only
        # an explicitly requested one — the CLI has no band flag, so a bandless `lhpc stack
        # start graywolf` resolves to the declared primary and must not bypass this gate into
        # the cross-band state the rule exists to refuse.
        _dep_block = self._dep_band_block(target, order, band or cfg_band)
        if _dep_block is not None:
            return _dep_block
        radio, tx = self._daemon_needs(order, op_band)
        # The stack whose daemon params to apply once the daemon is up (a direct component target
        # resolves to its owning stack).
        start_sid = self._owner_stack_id(target)
        # CALL/node enforcement on the SAVED configuration — for the PLAN and the apply alike (a
        # licensed stack refuses an empty/N0CALL callsign, an unlicensed stack an empty node name),
        # so a refused start is known before anything is queued; the public `start()` re-checks it
        # under the locks as the authoritative, race-safe backstop immediately before mutation.
        _id_band = self._launch_band_hint(target, band)
        id_ok, id_fields, id_msg = self.enforce_identity(target, _id_band)
        if not id_ok:
            return ActionResult(False, f"Cannot start '{target}': {id_msg}",
                                data={"enforce_fields": id_fields},
                                next_commands=self._identity_config_hints(target, _id_band))
        # The approved identity must actually LAUNCH: a licensed field that inherits the global
        # operator callsign carries it as a launch value (never persisted) so argv/config-file
        # rendering and post-steps use what enforcement just approved — on the launch band.
        params, file_over = self._materialize_inherited_identity(target, _id_band)
        # SAVED launch values the launch would reject refuse here too — plan and apply alike,
        # before owner stops or config generation (a restart preflights the same check).
        if (_cfg_err := self._saved_launch_refusal(target, band, "start")) is not None:
            return _cfg_err
        prep = prepare_start(
            target=target, band=band, cfg_band=cfg_band, op_band=op_band, order=order,
            radio=radio, tx=tx, start_sid=start_sid, target_is_stack=self.stack(target) is not None,
            params=params, file_over=file_over, position=position, position_note=position_note,
            stop_owners=stop_owners)
        if not apply:
            return prep
        # No-side-effect Start FIRST — BEFORE any owner handling: if EVERY requested component is
        # already healthy, return ALREADY_HEALTHY immediately. Never run blockers for mutation,
        # never stop owners (even with stop_owners=True), never launch/write config/apply params/
        # CONF SET/touch markers for an already-healthy target.
        if self._order_already_healthy(order, radio):
            # Only components the health predicate actually JUDGED may be reported 'already running':
            # a non-main interactive component was satisfied by its marker (its command was presented —
            # nothing is running under lhpc), and a GUI-skipped gui_optional component is not runnable
            # here at all. Fabricating ALREADY_HEALTHY for either is a lie; they are simply omitted
            # (matching _all_components_healthy, which skipped them).
            def _judged(stack, comp):
                if comp.kind in (ComponentKind.LIBRARY, ComponentKind.FIRMWARE):
                    return False
                if comp.interactive and comp.id != stack.main:
                    return False
                return not (comp.gui_optional and (
                    comp.id in self.gui_unavailable_components(stack)
                    or (self.needs_display(comp) and not self.display_available())))
            _hres = [CompResult(component=comp.id, stack=stack.id, action="start",
                                outcome=Outcome.ALREADY_HEALTHY, summary="already running")
                     for stack, comp in order if _judged(stack, comp)]
            return ActionResult(True, f"'{target}' already healthy — nothing to start.",
                details=[f"  [already_healthy] {r.component}: already running" for r in _hres]
                + self._lifecycle().spi_running_warnings(order),
                results=tuple(_hres), next_commands=[f"lhpc status {target}"])

        # FW-R8 exposure gate: if the managed firewall is installed and this start would bind a
        # non-loopback listener whose containment is not live-verified against the current saved
        # intent, REFUSE before stopping owners, launching, or writing config — otherwise an
        # externally reachable stack listener could come up with no verified firewall protecting
        # it (the compatibility-mode hole). No-op when firewall integration is absent or nothing
        # non-loopback is exposed; resolved from the ACTUAL launch plan (the saved config on the
        # launch band). A component about to start must never SILENTLY inherit an AMBIGUOUS flat
        # value either. Ownership check: if a needed resource is held by another running stack,
        # either stop that stack first (stop_owners) or refuse and report it.
        if (_r := self._start_preflight_refusal(target, order, band, radio, "start",
                                                check_blockers=not stop_owners,
                                                render=True)) is not None:
            return _r
        return prep

    def _start_preview(self, prep) -> ActionResult:
        """The start PLAN from the prepared start: what the apply would do, per component, plus
        the apply's firewall gate and ambiguity refusal behind its healthy shortcut and the
        running holders as `[conflict]` details. Advisory only: the apply decides everything
        again under its own guards and never reads this result."""
        target, band, order, radio, tx, cfg_band = (prep.target, prep.band, prep.order,
                                                    prep.radio, prep.tx, prep.cfg_band)
        details = []
        commands = []   # copyable commands the operator must run themselves
        for _stack, comp in order:
            if comp.id == self.DAEMON_ID:
                kept, owned = self._daemon_arbitrated_bands(radio)
                if owned:
                    served = "+".join(kept) if kept else "none"
                    skips = ", ".join(f"{b} owned by {owned[b]}" for b in sorted(owned))
                    details.append(f"  [daemon] start/ensure --radio {served} ({skips} — skipped)"
                                   + (f", TXMODE={tx}" if tx else ""))
                else:
                    details.append(f"  [daemon] start/ensure --radio {radio or 'all bands'}"
                                   + (f", TXMODE={tx}" if tx else ""))
            elif comp.interactive:
                # The PLAN obeys the same fallback policy as the apply path: where the
                # GUI main is usable, voice's terminal variant is not offered — neither
                # the CLI plan nor the web may render its command.
                _st = self.stack_of(comp.id)
                _stk = self.stack(_st) if _st else None
                _m = (next((c for c in _stk.components if c.id == _stk.main), None)
                      if _stk else None)
                if (comp.id != getattr(_stk, "main", None)
                        and _m is not None and _m.gui_optional
                        and not self.gui_fallback_active(_stk)):
                    details.append(f"  [skip] {comp.id}: {_stk.main} runs on this box — "
                                   "the terminal variant is the fallback for boxes "
                                   "without a graphical environment")
                    continue
                cmd = self.manual_start_command(comp)
                details.append(f"  [manual] {comp.id} is interactive — the daemon is "
                               "ensured, then run it yourself in a terminal:")
                details.append(f"    {cmd}")
                commands.append(cmd)
            elif comp.units and not comp.run_argv:
                cmd = f"sudo systemctl start {comp.units[0].name}"
                details.append(f"  [manual] {comp.id} is a system service — start it with:")
                details.append(f"    {cmd}")
                commands.append(cmd)
            elif comp.gui_optional and (
                    comp.id in self.gui_unavailable_components(_stack)
                    or (self.needs_display(comp) and not self.display_available())):
                # The apply types this component SKIPPED (no GUI toolkit or no display) —
                # the same predicate the already-healthy check uses — so the plan must not
                # promise to start it.
                details.append(f"  [skip] {comp.id}: GUI component — not applicable on this "
                               "box (no graphical toolkit or display)")
            else:
                details.append(f"  [start] {comp.id} (band {cfg_band or comp.band or '-'})")
        # The apply's firewall gate and ambiguity refusal, behind the apply's own healthy
        # shortcut; blockers stay plan details (the web's owner-confirm flow reads them).
        if not self._order_already_healthy(order, radio) and (
                _r := self._start_preflight_refusal(target, order, band, radio, "start",
                                                    check_blockers=False,
                                                    render=False)) is not None:
            return _r
        blockers = self.run_blockers(target, band, radio)
        for bl in blockers:
            details.append(f"  [conflict] {bl['resource']} is held by running stack "
                           f"'{bl['holder_stack']}' ({bl['holder']})")
        if not blockers and (_spi := self._spi_shared_warning(target, band, radio)):
            details.append(f"  [warning] {_spi}")
        details += [f"  [warning] {w}" for w in _ohs.start_warnings(self, target)]
        return ActionResult(True, f"Run plan for '{target}': {len(order)} component(s) in order.",
                            details=details,
                            next_commands=[f"lhpc stack start {target}"
                                           + (f" --band {band}" if band else "") + " --yes"],
                            data={"changes": len(order), "blockers": blockers,
                                  "commands": commands})

    def _execute_start(self, prep, run):
        """PHASE 3 EXECUTE in explicit order: conflicting owners stopped (`stop_owners`; one that
        does not verify stopped refuses here, before anything of the target runs), the feed floor
        of the start's bands reset, the process observation taken, then per component in run order its gates (typed BLOCKED / SKIPPED /
        MANUAL_REQUIRED), config files, pre-steps / interactive marker, RF-log roll, spawn — each
        spawn followed at once by its readiness, post-start and running-band checks, a failed one
        stopping that launch again (typed UNVERIFIED) — and last the stale interactive markers of
        other stacks cleared. Fills `run` (the coordinator's ledger, so a launch an error cuts short
        is still rolled back) and returns it, or the refusal."""
        target, band, order, radio = prep.target, prep.band, prep.order, prep.radio
        cfg_band, position, start_sid = prep.cfg_band, prep.position, prep.start_sid
        params, file_over = prep.params, prep.file_over
        # Ownership scope of THIS public operation: recorded on every launch it causes
        # (incl. an ensured daemon), threaded EXPLICITLY — never ambient/thread-local. A
        # component-scoped start must never be widened into a whole-stack boot restore.
        _req_target, _req_scope = target, prep.scope
        life = run.life
        blockers = self.run_blockers(target, band, radio) if prep.stop_owners else []
        if blockers:
            prelude = []
            unstopped = []

            def stop_owner(o, b=""):
                ores = self.stop(o, apply=True, _operator=False, band=b)
                name = f"'{o}'" + (f" on {b}" if b else "")
                if ores.ok:
                    prelude.append(f"  [stopped] conflicting stack {name}")
                else:
                    unstopped.append(o)
                    prelude.append(f"  [blocked] conflicting stack {name} did not stop "
                                   f"(verified): {ores.summary}")
                    prelude.extend(ores.details)
            # The app stacks first: a whole-stack stop of the daemon would take down every client
            # on every band (finding 140). Then the daemon only on the radio bands still held —
            # stopping an app that ran on the daemon often releases its band already.
            daemon_sid = self.stack_of(self.DAEMON_ID) or "daemon"
            for o in sorted({bl["holder_stack"] for bl in blockers} - {daemon_sid}):
                stop_owner(o)
            if not unstopped and any(bl["holder_stack"] == daemon_sid for bl in blockers):
                left = [bl for bl in self.run_blockers(target, band, radio)
                        if bl["holder_stack"] == daemon_sid]
                bands = sorted({bl.get("band", "") for bl in left})
                # One daemon process serving both bands stops both on a per-band stop: refuse
                # rather than stop the clients of a band this start does not need.
                needed = set(self._operation_bands(target, band, radio, "start"))
                for b in [b for b in bands if b]:
                    extra = set(self._operation_bands(daemon_sid, b, "", "stop")) - needed - {b}
                    hit = self.stop_dependents(daemon_sid, bands=extra) if extra else []
                    if hit:
                        apps = sorted({bl["holder_stack"] for bl in blockers} - {daemon_sid})
                        return ActionResult(
                            False,
                            f"Cannot run '{target}': the daemon on {b} also serves "
                            f"{', '.join(sorted(extra))} (one process); stopping it would stop "
                            f"{', '.join(hit)} too. "
                            + (f"{', '.join(apps)} {'was' if len(apps) == 1 else 'were'} stopped; "
                               if apps else "") + "the daemon was not.",
                            details=[*prelude, f"  [blocked] daemon {b}: one process with "
                                     f"{', '.join(sorted(extra))} — {', '.join(hit)} left running"],
                            next_commands=[*(f"lhpc stack stop {h}" for h in hit),
                                           f"lhpc stack start {target} --yes"])
                for b in ([""] if "" in bands else bands):
                    stop_owner(daemon_sid, b)
            if unstopped:
                # Do not launch the target while a conflicting owner is still up.
                return ActionResult(
                    False,
                    f"Cannot run '{target}': conflicting stack(s) {', '.join(unstopped)} "
                    "could not be verified stopped.",
                    details=prelude,
                    next_commands=[f"lhpc status {o}" for o in unstopped])
            time.sleep(1.0)  # let sockets/locks release
            # (The nested public `self.stop()` above is @invalidates_snapshot — its exit drops the memo,
            # so the build_snapshot() below and the launch-loop recheck already recompute fresh.)
        else:
            prelude = []
        # The feed-floor reset, after PREPARE and the owner stops (a refused start writes
        # nothing) and before the first spawn: the RX/TX window is cleared at the start boundary,
        # SCOPED to the band set the lock bundle covers — a 433 client clears 433 only, an
        # all-active daemon start every arbitrated band, a non-radio start nothing.
        for _b in sorted(self._operation_bands(target, band, radio, "start")):
            self.clear_daemon_feed(_b)

        snap = self.build_snapshot()
        st_index = {c.id: ss.components[c.id]
                    for ss in snap.stacks for c in ss.stack.components}
        out = run.out
        out.extend(prelude)
        results = run.results            # TYPED per-component outcomes (source of truth)
        daemon_ok = True                # gate dependents on verified daemon readiness
        daemon_gate = ""                # a refusal of THIS stack's daemon config (not a daemon failure)
        daemon_launched = run.daemon_launched   # daemon bands this start launched (roll-back scope)

        presented = run.presented        # components whose copy-paste start command was shown
        def record(comp, stack, outcome, summary, command="", note=""):
            # A copy-paste `command` goes on a line of its own, and the `note` on the next one:
            # a note appended to the command made the pasted line a shell syntax error (F-C2).
            if command:
                presented.add(comp.id)
            results.append(CompResult(component=comp.id, stack=stack.id, action="start",
                                      outcome=outcome,
                                      summary=f"{summary} {command}" if command else summary))
            out.append(f"  [{outcome.value}] {comp.id}: {summary}")
            if command:
                out.append(f"    {command}")
            if note:
                out.append(f"    ({note})")

        # Config generation + launch config are COMPONENT-scoped so a direct component start never
        # writes a sibling's config nor leaks the target's run params into a dependency: each
        # component renders its OWN saved/default values (plus its own inherited identity).
        _target_is_stack = prep.target_is_stack
        # The successful-start tail cleared restart-required UNCONDITIONALLY, so a
        # save landing while the required post-start had released config stability had its warning
        # deleted by the very launch that did NOT apply it — saved config said B, the radio still
        # identified as A, and nothing said so. Remember the marker HERE, while configuration is
        # still stable and before any release window, and clear only THIS state (see the tail).
        _pre_marker = self.restart_required(target) if _target_is_stack else None
        def _comp_overrides(comp_id):
            if not (_target_is_stack or comp_id == target):
                return None
            return self._overrides_for_comp(target, "file", file_over, comp_id)

        for stack, comp in order:
            state = st_index[comp.id].run_state
            running = state == RunState.RUNNING        # DEGRADED is NOT healthy
            # A DEGRADED component (process up but a ready endpoint missing) must not
            # be treated as healthy and must not trigger a duplicate launch. A GPS feed is judged
            # by the rule a fresh start applies (`_gps_feed_admission`): under `auto` a feed whose
            # source is missing runs without position, so it is already running, left as it is.
            if state == RunState.DEGRADED and comp.id != self.DAEMON_ID:
                if str(getattr(comp, "readiness", "")) == "gps-feed":
                    feed_ok, ev = self._gps_feed_admission(comp)
                    if feed_ok:
                        record(comp, stack, Outcome.ALREADY_HEALTHY, f"already running ({ev})")
                        continue
                record(comp, stack, Outcome.BLOCKED, "running but DEGRADED (a ready "
                       "endpoint is missing) — stop it (verified) and re-run")
                continue
            if comp.id == self.DAEMON_ID:
                # The daemon bands THIS start launched: the ownership records the ensure added
                # (its VERIFIED row reads the same for a band that was already served).
                _known = {r["launch_id"] for r in life.owned_records(comp.id)}
                dlines, dok, dgate = self._ensure_daemon(life, stack, comp, running, radio,
                                                         start_sid, requested_target=_req_target,
                                                         start_scope=_req_scope)
                daemon_launched |= {r["band"] for r in life.owned_records(comp.id)
                                    if r["launch_id"] not in _known}
                out.extend(dlines)
                results.append(CompResult(component=comp.id, stack=stack.id, action="start",
                    outcome=(Outcome.VERIFIED if dok else Outcome.FAILED),
                    summary="daemon ready" if dok else self._daemon_failure_reason(dlines)))
                daemon_ok = dok and not dgate
                daemon_gate = dgate
                continue
            # A dependent must NOT start when the daemon it needs failed readiness — or when the
            # daemon is fine but this stack's own daemon config was refused (the POWER=20
            # permission): then the dependent carries THAT reason, not a daemon failure.
            if not daemon_ok and self.DAEMON_ID in comp.depends_on:
                record(comp, stack, Outcome.BLOCKED,
                       f"not started: {daemon_gate}" if daemon_gate
                       else "daemon not ready — not started")
                continue
            # ...and the same must hold for EVERY dependency, not just the daemon.
            # lxmd/nomadnet/sideband use the config dir `rns` owns, so one of them
            # starting after `rns` failed can become the shared-instance owner and
            # initialise the LoRa interface itself — taking the radio behind our back.
            unmet = self._unmet_dependencies(comp, order, results)
            if unmet:
                record(comp, stack, Outcome.BLOCKED,
                       f"depends on {', '.join(unmet)}, which did not start — not started")
                continue
            if running:
                # RUNNING is a PROCESS fact. For an endpoint-readiness component a
                # same-named foreign process satisfies it while the evidence the driver
                # owns (its ready marker / listeners) is absent — the radio proof was
                # bypassed. Demand the declared evidence before calling it healthy.
                st_now = st_index[comp.id]
                if str(getattr(comp, "readiness", "")) == "endpoint" and not all(
                        e.present for e in (st_now.endpoints or ())):
                    record(comp, stack, Outcome.BLOCKED,
                           "a process is running but its readiness evidence is absent "
                           "(foreign or half-started) — stop it (verified) and re-run")
                    continue
                record(comp, stack, Outcome.ALREADY_HEALTHY, "already running")
                continue
            # A GUI app cannot work without a display. Voice is kept off headless rigs
            # by its GUI-only build deps, but a box CAN have those deps (installed for
            # another stack, or a desktop image driven over SSH) and still have no
            # session — Sideband was then launched into nothing. Skip it the same way:
            # a typed SKIPPED, not a failure, and never for a non-GUI component.
            # A receipt-bearing component whose consumed sources moved since its last
            # build must not launch: the venv still holds the OLD driver, and starting
            # it would run code the manifest no longer describes. Web starts and
            # auto-install already gate on unbuilt_components; the CLI start did not.
            if comp.build_marker and comp.build_requires and not self.is_built(comp):
                record(comp, stack, Outcome.BLOCKED,
                       "its sources changed since the last build — rebuild first "
                       f"({self.build_remedy(stack.id, comp.id)})")
                continue
            # A GUI component that cannot run HERE is typed-SKIPPED: either its toolkit is
            # absent (gui_optional on a box bootstrapped without --with-gui — the same
            # predicate that drops it from build/auto-install, so start agrees with the
            # gates that admitted the stack) or no graphical session is running.
            _toolkit_missing = comp.gui_optional and any(
                getattr(r, "gui", False) for r in life.missing_requirements(comp))
            if _toolkit_missing or (self.needs_display(comp) and not self.display_available()):
                # Still (re)generate any config file this component OWNS before skipping:
                # config generation needs no display, and a sibling may read the same file
                # (voice's terminal variant runs the GTK component's loraham_voice.conf).
                # Skipping first would hand the operator a manual command with a stale or
                # absent config — the same three-status handling as the sibling write sites.
                if comp.config_file:
                    cw = self.write_config_files(comp.id, cfg_band, _comp_overrides(comp.id),
                                                 position=position)
                    mine = [w for w in cw if w.component == comp.id]
                    bad = next((w for w in mine if w.status in ("failed", "no-base")), None)
                    if bad:
                        record(comp, stack, Outcome.BLOCKED,
                               f"config generation failed ({bad.path}: {bad.detail})")
                        continue
                record(comp, stack, Outcome.SKIPPED,
                       "GUI toolkit not installed — headless-safe default (--with-gui "
                       "installs it on a machine with a display)" if _toolkit_missing else
                       "needs a graphical session; none is running on this box "
                       "(start it from the desktop, not over SSH)")
                continue
            if comp.source and not self._source_present(comp):
                record(comp, stack, Outcome.BLOCKED, f"not installed (lhpc install {stack.id})")
                continue
            # An installed binary artifact built from other commits than the manifest pins is
            # an install-state problem like "not installed": refused here, before any
            # requirement or build check, so a stale binary is never launched with argv it
            # does not know.
            if (_behind := self.binary_behind(comp)):
                record(comp, stack, Outcome.BLOCKED, _behind)
                continue
            if comp.interactive:
                # Never auto-start an interactive TUI — the operator runs it in a terminal.
                # ONLY voice's terminal variant is a GUI FALLBACK: a non-main interactive
                # component whose stack MAIN is gui_optional (manifest-derived — no other
                # stack has that shape). It shares the main's config (licensed callsign) and
                # exclusive audio device, so it earns extra gates. Every OTHER interactive
                # component (chat, nomadnet, meshcore-cli) keeps its long-standing behaviour.
                _main = next((c for c in stack.components if c.id == stack.main), None)
                _fallback = (comp.id != stack.main
                             and _main is not None and _main.gui_optional)
                if _fallback:
                    # INACTIVE fallback first: where the GTK main is usable the terminal
                    # variant is not offered AT ALL, so no gate may turn that skip into a
                    # failure (a GTK app legitimately holding the audio device is not a
                    # 'resource conflict' of a component that is not on offer).
                    if not self.gui_fallback_active(stack):
                        record(comp, stack, Outcome.SKIPPED,
                               f"{stack.main} runs on this box and owns the shared "
                               "configuration and audio device — the terminal variant is the "
                               "fallback for boxes without a graphical environment")
                        continue
                    # EVERY readiness/blocker gate BEFORE pre-steps, marker and command.
                    blocker = self.install_blocker(comp)
                    if blocker:
                        record(comp, stack, Outcome.BLOCKED, f"interactive but {blocker}")
                        continue
                    miss = self.start_blocking_requirements(comp)
                    if miss:
                        from .lifecycle import req_remediation
                        record(comp, stack, Outcome.BLOCKED,
                               "; ".join(req_remediation(r, bool(r.groups)
                                                         and life.group_grant_pending(r))
                                         for r in miss))
                        continue
                    # The exclusive audio claim is REAL for a terminal variant too.
                    if self._running_conflicts(comp, cfg_band):
                        record(comp, stack, Outcome.BLOCKED, "resource conflict")
                        continue
                    if not self.is_built(comp):
                        record(comp, stack, Outcome.BLOCKED,
                               "not built — build it first "
                               f"({self.build_remedy(stack.id, comp.id)})")
                        continue
                    # The shared-config OWNER must have completed in THIS run: a BLOCKED
                    # owner (config generation failed) must not yield a presented command
                    # over an absent or stale shared config.
                    owner = next((r for r in results if r.component == stack.main), None)
                    if owner is None or owner.outcome not in (
                            Outcome.SKIPPED, Outcome.ALREADY_HEALTHY,
                            Outcome.STARTED, Outcome.VERIFIED):
                        _why = owner.outcome.value if owner is not None else "was not visited"
                        record(comp, stack, Outcome.BLOCKED,
                               f"{stack.main} owns the shared configuration and did not "
                               f"complete ({_why}) — resolve that first")
                        continue
                # Its OWN required runtime config (the voice fallback shares the owner's and
                # declares none): if generation fails, DO NOT write the marker and DO NOT
                # present a command as ready-to-run.
                if comp.config_file:
                    cw = self.write_config_files(comp.id, cfg_band, _comp_overrides(comp.id),
                                                 position=position)
                    mine = [w for w in cw if w.component == comp.id]
                    bad = next((w for w in mine if w.status in ("failed", "no-base")), None)
                    if bad:
                        record(comp, stack, Outcome.BLOCKED,
                               f"interactive start blocked — required config could not be "
                               f"generated ({bad.path}: {bad.detail})")
                        continue
                # The manual command must be genuinely READY TO RUN. An interactive
                # component's pre_steps otherwise never execute: their only caller is
                # `life.start`, which is exactly what this branch skips. Voice's terminal
                # variant resolves its shared loraham_voice.conf from dirname(argv0), so
                # without its `config/files` symlink the command we print here fails with
                # ENOENT.
                if comp.pre_steps:
                    from . import commands
                    try:
                        commands.run_pre_steps(comp.pre_steps,
                                               str(self._paths.runtime_root),
                                               str(life.source_dir(comp)), cfg_band)
                    except commands.CommandError as exc:
                        record(comp, stack, Outcome.BLOCKED,
                               f"interactive start blocked — pre-start setup failed: {exc}")
                        continue
                marked = self.mark_interactive(stack.id, cfg_band)
                blocker = self.install_blocker(comp)
                marker_note = ("" if marked else
                               " (note: interactive marker could not be persisted — the "
                               "dashboard may not show its command block)")
                if blocker:
                    record(comp, stack, Outcome.BLOCKED, f"interactive but {blocker}")
                else:
                    # For a CLI/SSH operator this result IS the surface — there is no dashboard
                    # card in a terminal — so the copy-paste command is printed for the
                    # interactive main (chat, F-C1) as for a sidecar (voice-cli). lhpc cannot
                    # restart an operator-run TUI, so a live one keeps its old config after a
                    # regeneration — say so instead of implying it applied.
                    record(comp, stack, Outcome.MANUAL_REQUIRED,
                           f"interactive — run it yourself in a terminal{marker_note}:",
                           command=self.manual_start_command(comp),
                           note="an already-running instance keeps its previous config "
                                "until you restart it")
                continue
            if comp.units and not comp.run_argv:
                # Externally supervised (systemd, root) — lhpc observes, never starts.
                record(comp, stack, Outcome.MANUAL_REQUIRED,
                       f"system service — start with: sudo systemctl start {comp.units[0].name}")
                continue
            miss = self.start_blocking_requirements(comp)
            if miss:
                # A groups grant that is merely restart-PENDING (configured but not yet effective in this
                # process) must advise a RESTART, not re-show the already-run `usermod` (see req_remediation).
                from .lifecycle import req_remediation
                # req_remediation owns the "missing"/"granted" framing per requirement — do NOT prefix a
                # blanket "missing " (it would contradict a granted-but-restart-pending groups grant).
                record(comp, stack, Outcome.BLOCKED,
                       "; ".join(req_remediation(r, bool(r.groups) and life.group_grant_pending(r))
                                 for r in miss))
                continue
            if self._running_conflicts(comp, cfg_band):
                record(comp, stack, Outcome.BLOCKED, "resource conflict")
                continue
            if not self.is_built(comp):
                record(comp, stack, Outcome.BLOCKED,
                       "not built — build it first "
                       f"({self.build_remedy(stack.id, comp.id)})")
                continue
            # Regenerate any config file this component reads (per the chosen band). A
            # generation FAILURE for this component blocks the launch — never start with
            # stale or absent configuration.
            if comp.config_file:
                cw = self.write_config_files(comp.id, cfg_band, _comp_overrides(comp.id),
                                             position=position)
                mine = [w for w in cw if w.component == comp.id]
                bad = next((w for w in mine if w.status in ("failed", "no-base")), None)
                if bad:
                    record(comp, stack, Outcome.BLOCKED,
                           f"config generation failed ({bad.path}: {bad.detail})")
                    continue
            self._rflog_roll_at_start(comp.id)
            # COMPONENT-scoped launch config (this component's OWN run params from the owner-stack
            # store) so a stored sibling run parameter can never leak into another component's argv
            # through a name collision. The only launch-time overlay is an inherited identity
            # (`_materialize_inherited_identity`), handed to its own component only.
            comp_cfg = dict(self.stack_config(comp.id, cfg_band))
            if _target_is_stack or comp.id == target:
                comp_cfg.update(self._overrides_for_comp(target, "run", params, comp.id))
            # ONE announcer per component (dedup within it): the start capture log, the
            # required post-start log, and the detached post runner's log all get a
            # copy-pasteable `[log] <comp> -> tail -f <path>` line the moment they exist.
            announcer = self._log_announcer(comp.id, out)
            res = life.start(stack, comp, comp_cfg, band=cfg_band,
                             requested_target=_req_target, start_scope=_req_scope)
            if res.log_path:
                announcer(f"start-{comp.id}", res.log_path)
            if not res.ok:
                # A launch that couldn't be owned AND couldn't be proven ceased is a
                # typed UNVERIFIED (residual process), not a clean FAILED.
                record(comp, stack,
                       Outcome.UNVERIFIED if res.unverified else Outcome.FAILED,
                       f"start failed: {res.detail} (log {res.log_path})")
                continue
            # Live pre-readiness note (run-2/3 finding: two "not working" misdiagnoses while the
            # emulated MeshCom node was simply booting): surface the component's start_note BEFORE
            # the readiness poll, so the operator sees the expected wait AS IT BEGINS — clearly a
            # heads-up, never presented as readiness. The line goes into the component's OWN start
            # log too (descriptor-safe append + flush) so a `tail -f` watcher sees it live, and the
            # same text still appears in the eventual result details / post-success notes as before.
            if comp.start_note and comp.readiness == "endpoint":
                note_line = f"  [note] {comp.id}: {comp.start_note}"
                out.append(note_line)
                if res.log_path:
                    try:
                        with runtime_fs.open_log_append(self._paths, Path(res.log_path)) as fh:
                            fh.write((note_line.strip() + "\n").encode("utf-8"))
                            fh.flush()
                    except (OSError, PathContainmentError):
                        pass        # best-effort live line; the result detail above already has it
            # readiness="endpoint": VERIFIED only once every ready=true endpoint is up;
            # otherwise SIGTERM the just-launched owned session (verified cleanup) and
            # report UNVERIFIED — no post-start work runs.
            if comp.readiness == "gps-feed":
                # A feed whose SOURCE never came up is not started, it is inert: it would sit
                # publishing an endpoint that delivers nothing, and the app reading it would
                # report no position with no indication why. Verified against the marker the
                # feed writes from its upstream, never against the endpoint path existing.
                # (`auto` softens exactly the upstream-missing states — see _gps_feed_admission.)
                ready_ok, ev = self._gps_feed_admission(comp)
                if not ready_ok:
                    cleanup = life.stop(comp, band=cfg_band)
                    record(comp, stack, Outcome.UNVERIFIED,
                           f"GPS feed never reached its source ({ev}); cleanup: "
                           + ("stopped" if cleanup.outcome == Outcome.STOPPED
                              else "cessation NOT verified — ownership retained"))
                    continue
                summary = f"started; {ev}"
            elif comp.readiness == "endpoint":
                ready_ok, ev = self._ready_endpoints_present(comp)
                if not ready_ok:
                    cleanup = life.stop(comp, band=cfg_band)
                    record(comp, stack, Outcome.UNVERIFIED,
                           f"ready endpoint(s) never came up ({'; '.join(ev)})"
                           + self._conflicting_service_hint(comp)
                           + "; cleanup: "
                           + ("stopped" if cleanup.outcome == Outcome.STOPPED
                              else "cessation NOT verified — ownership retained"))
                    continue
                summary = f"started; ready endpoint(s) up ({'; '.join(ev)})"
            else:
                summary = f"started (log {res.log_path})"
            # Required post-start must complete before VERIFIED; optional is scheduled.
            # `required_ok` is True (required passed), False (required failed), or None
            # (no required post-start) — explicit, no enum-attribute confusion.
            required_ok, post_summary = self._run_post_start(life, stack, comp, comp_cfg, cfg_band,
                                                             announce=announcer)
            if required_ok is False:
                cleanup = life.stop(comp, band=cfg_band)
                record(comp, stack, Outcome.UNVERIFIED,
                       f"required post-start failed: {post_summary}; cleanup: "
                       + ("stopped" if cleanup.outcome == Outcome.STOPPED
                          else "cessation NOT verified — ownership retained"))
                continue
            if post_summary:
                summary += f"; {post_summary}"
            # Persist the running-band marker BEFORE declaring VERIFIED: it drives
            # multi-band decisions + dashboard state, so a write failure must surface in
            # the typed result (UNVERIFIED), not hide behind a clean VERIFIED.
            band_ok = True
            # Only a BAND-CARRYING component may define the stack's band: an optional
            # client started with band=868 must not write that marker while `rns` is
            # tuned to 433, or status, the dashboard, arbitration and per-band config
            # all describe a band the radio is not on.
            if cfg_band and self.stack_bands(stack.id) and (comp.band or comp.bands):
                band_ok = self._set_running_band(stack.id, cfg_band)
            if band_ok:
                record(comp, stack, Outcome.VERIFIED, summary)
            else:
                # Leaving the owner up without durable band evidence is the dangerous
                # outcome: later arbitration falls back to the DECLARED band, so a node
                # actually on 433 reads as 868 and a second 433 owner can be admitted.
                # Stop what we just started rather than run a radio we cannot account for.
                cleanup = life.stop(comp, band=cfg_band)
                record(comp, stack, Outcome.UNVERIFIED,
                       summary + "; running-band marker could not be persisted — "
                       + ("started process stopped again (no durable band evidence)"
                          if cleanup.outcome == Outcome.STOPPED
                          else "cessation NOT verified — ownership retained"))
        self.clear_stale_interactive(keep=self.stack_of(target) or target)
        run.pre_marker = _pre_marker
        return run

    def _finalize_start(self, prep, run, failed, required_manual) -> ActionResult:
        """PHASE 5 FINALIZE: the summary from the verdict; on a successful STACK start the
        last-start candidate composition and the restart-required marker — cleared only when this
        launch satisfied it (unchanged since `execute` read it, and no component of the stack
        kept running its old configuration); the position note; the SPI warning once the new
        side came up."""
        target, band, radio, out, results = (prep.target, prep.band, prep.radio, run.out,
                                             run.results)
        ok = not failed and not required_manual
        if failed:
            summary = f"Run FAILED for '{target}': {', '.join(failed)} did not start/verify."
        elif required_manual:
            summary = (f"Run for '{target}': manual start required for "
                       f"{', '.join(required_manual)} — see the dashboard.")
        else:
            summary = f"Run applied for '{target}'."
        # HEALTHY STACK START: persist the last-start CANDIDATE composition (durable, written here in the
        # mutation path so GET pages never need git). It is NOT a known-working record — the operator confirms
        # it ("Confirm this stack as working"). It also satisfies any restart-required flag (saved config runs).
        if ok and prep.target_is_stack:
            self._capture_start_composition(target, prep.cfg_band)
            # Clear ONLY a marker this launch actually satisfied. Two ways it can fail to:
            #   * CREATED or CHANGED while the required post-start ran unguarded — it describes
            #     configuration this launch never applied;
            #   * a component of THIS stack was ALREADY_HEALTHY, so it still runs the OLD configuration. A
            #     partly-up stack (main healthy, a sidecar down) reported ok=True and erased the warning its own
            #     identity save had just written — the node kept transmitting the previous callsign with nothing
            #     to say so. A DEPENDENCY left running (the shared daemon) must not keep the marker alive.
            _kept_old = any(r.outcome == Outcome.ALREADY_HEALTHY
                            and (self.stack_of(r.component) or r.stack) == target
                            for r in results)
            if not _kept_old and self.restart_required(target) == run.pre_marker:
                _rr.clear_marker(self._paths, target)
        # What this start left running as it was gets the SPI warning of the already-healthy answer.
        _kept = {r.component for r in results if r.outcome == Outcome.ALREADY_HEALTHY or (r.component
                 == self.DAEMON_ID and r.outcome == Outcome.VERIFIED and not run.daemon_launched)}
        out = [*out, *run.life.spi_running_warnings([(s, c) for s, c in prep.order if c.id in _kept])]
        if prep.position_note:
            # `auto` could not get a fix. Never gating, but never silent either: the node is
            # on the air without a position and the operator has to be able to see that.
            out = [*out, f"  [gps] {prep.position_note}"]
        warned = {}
        if ok and (_spi := self._spi_shared_warning(target, band, radio)):
            out = [*out, f"  [warning] {_spi}"]   # only once the new side came up
            warned = {"spi_warning": _spi}        # the console's start banner shows it too
        if ok and (_ohw := _ohs.start_warnings(self, target)):
            out = [*out, *(f"  [warning] {w}" for w in _ohw)]
            warned = {**warned, "openhop_warnings": _ohw}   # the stack page's start note too
        return ActionResult(ok, summary, details=out, results=tuple(results), data=warned,
                            next_commands=[f"lhpc status {target}", f"lhpc logs {target}",
                                           f"lhpc stack stop {target}"])

    def _roll_back_start(self, life, order, results, band: str, daemon_launched) -> list[str]:
        """The roll-back of a FAILED start (finding 64): stop again what THIS start launched,
        last started first, and return the detail lines.

        * A component this start launched and verified (`rollback_ids`) gets the
          identity-verified stop of `Lifecycle.stop` (SIGTERM only), and its `start` row in
          `results` is replaced in place: the outcome becomes the stop's (STOPPED, or
          STILL_RUNNING / UNVERIFIED / ENDPOINT_STILL_PRESENT with ownership retained) and the
          summary gains "; rolled back: <the stop's summary>" — so nothing downstream reads a
          stopped component as up.
        * A daemon band this start launched (`daemon_launched`) is stopped only when no other
          component of the run order holds an ownership record any more — never beneath a client
          that may still run. Each band appends one `stop` row: the stop's own, or BLOCKED naming
          the band and those components. The daemon's `start` row is left as it was.
        * Nothing that ran before this start (ALREADY_HEALTHY, a band already served) is
          touched, and no marker: a running-band marker this start wrote stays until the next
          stop of its stack retires it."""
        by_id = {c.id: c for _, c in order}
        lines: list[str] = []

        def note(cr):
            lines.append(f"  [rollback] [{cr.outcome.value}] {cr.component}: {cr.summary}")
        rolled = rollback_ids(results, keep={self.DAEMON_ID})
        for cid in rolled:
            i = next(i for i, r in enumerate(results)
                     if r.component == cid and r.action == "start")
            cr = life.stop(by_id[cid], band=band)
            results[i] = dataclasses.replace(
                results[i], outcome=cr.outcome,
                summary=f"{results[i].summary}; rolled back: {cr.summary}")
            note(cr)
        if daemon_launched and self.DAEMON_ID in by_id:
            residual = sorted(c.id for _, c in order
                              if c.id != self.DAEMON_ID and life.owned_records(c.id))
            why = residual_reason(residual, rolled, results)
            for b in sorted(daemon_launched):
                if residual:
                    cr = CompResult(component=self.DAEMON_ID, action="stop",
                                    outcome=Outcome.BLOCKED,
                                    summary=f"{b} MHz instance this start launched left running: "
                                            f"{why}")
                else:
                    cr = life.stop(by_id[self.DAEMON_ID], band=b)
                results.append(cr)
                note(cr)
        return lines

    def _run_post_start(self, life, stack, comp, comp_cfg, band,
                        announce=None, strict=False, require_all=False) -> tuple[bool | None, str]:
        """Run post-start steps. Returns (required_ok, summary):
          * (None, "")               — no post-start;
          * (None, "…scheduled")     — OPTIONAL steps scheduled detached (never gates);
          * (True,  "…completed")    — REQUIRED steps ran synchronously and PASSED;
          * (False, "…failed (rc N)")— REQUIRED steps FAILED → caller blocks VERIFIED
                                        and invokes verified cleanup.
        `required_ok is False` is the only blocking case (an explicit bool, not an
        enum attribute). `announce` (a `_log_announcer` callback) gets the post-start
        log path the moment it exists — required synchronously via run_job's
        on_log_open, detached from the schedule result — so the operator can tail the
        (possibly minutes-long) retry window.

        `strict=True` (the `poststart` verb, whose ONLY job is this work) also reports a
        failed OPTIONAL scheduling as `False`. The START path leaves it False so a
        scheduling failure stays visible-but-non-gating there."""
        if not comp.post_steps:
            return None, ""
        if life.has_required_post_start(comp):
            # The retry window can legitimately run for minutes (MeshCom waits for a guest
            # console). Everything it consumes — comp_cfg, the rendered launcher, the GPS plan —
            # is materialized under the guard BEFORE the run, so the guard is released across the
            # EXECUTION: a config save during a long post-start is accepted and reported through
            # restart-required, exactly as a save during any other part of a launch already is.
            jr = life.run_required_post_start(stack, comp, comp_cfg, band=band,
                                              on_log_open=announce, require_all=require_all,
                                              while_running=self._config_unstable())
            if jr.ok:
                return True, "required post-start completed"
            return False, f"required post-start failed (rc {jr.returncode})"
        # OPTIONAL: scheduling never gates the start, but its typed result makes any
        # scheduling failure VISIBLE in the details (it is no longer swallowed by
        # `spawn_post_start`, so no blanket catch is needed here).
        sched = life.spawn_post_start(stack, comp, comp_cfg, band=band)
        if sched.ok:
            if announce is not None and sched.log_path:
                announce(f"post-{comp.id}", sched.log_path)
            return None, "optional post-start scheduled"
        if getattr(sched, "unverified", False):
            # Lifecycle-INTEGRITY failure: a spawned runner we can neither own nor prove stopped.
            # This GATES the main VERIFIED result (unlike an ordinary optional transport failure).
            return False, f"post-start runner integrity failure: {sched.detail}"
        return (False if strict else None), \
            f"optional post-start could NOT be scheduled: {sched.detail}"

    @invalidates_snapshot
    def poststart(self, target: str, apply: bool = False, band: str = "",
                  require_all: bool = False) -> ActionResult:
        """Re-run a RUNNING stack's post-start steps WITHOUT restarting it — e.g. re-apply the
        MeshCom callsign after the console-readiness retry window expired (a restart would cost
        another multi-minute QEMU boot). Reuses the exact readiness-gated senders the start path
        uses: required steps run synchronously, optional steps are re-scheduled detached. Any
        LIVE post runner is cancelled FIRST — the guest console serves one exchange per
        connection and must never see two concurrent senders.

        `require_all=True` escalates normally-OPTIONAL steps (e.g. the node-identity `--set-owner`) to
        gating for THIS run, so `.ok` proves every LHPC-owned value was reasserted — used by the
        `lhpc meshtastic` broad-mutation reconvergence, which must not report a false success. The
        default leaves ordinary start/poststart semantics unchanged."""
        if (_r := self._controller_refusal(target)) is not None:
            return _r
        order = self._run_order(target)
        if not order:
            return ActionResult(False, f"Unknown stack or component '{target}'.",
                                next_commands=["lhpc status"])
        if not any(comp.post_steps for _stack, comp in order):
            return ActionResult(False, f"'{target}' declares no post-start steps.",
                                next_commands=[f"lhpc status {target}"])
        if not apply:
            lines = [f"  re-run post-start: {comp.id} ({len(comp.post_steps)} step(s))"
                     for _stack, comp in order if comp.post_steps]
            return ActionResult(True, f"Would re-run post-start for '{target}'.", details=lines,
                                data={"changes": len(lines)})
        # IDENTITY GATE: a post-start step can transmit/apply an
        # identity (MeshCom --setcall, Meshtastic --set-owner), so its payload must never
        # come from raw {callsign}-substituted stack_config — a global
        # that a normal start refuses could ride straight into --setcall, and a missing
        # unlicensed identity slipped through. Enforce on the ACTUAL running band BEFORE
        # any runner cancellation or command execution.
        # THE one operation-band rule (explicit -> running -> active Lite marker -> primary),
        # so a post-start judges the identity on the same band the launch used — a hand-rolled
        # `band or running_band(...)` here missed the Voice-on-Lite case the rest of the feature
        # handles.
        _pid_band = self.operation_band(target, band)
        id_ok, id_fields, id_msg = self.enforce_identity(target, _pid_band)
        if not id_ok:
            return ActionResult(False, f"Cannot re-run post-start for '{target}': {id_msg}",
                                data={"enforce_fields": id_fields},
                                next_commands=self._identity_config_hints(target,
                                                                          _pid_band))
        from . import reslock
        try:
            with self._admission_guard("poststart", target), self._config_stable():
                return self._poststart_impl(target, order, band, require_all=require_all)
        except AdmissionRefused as _adm:
            return admission_refusal(_adm)
        except reslock.ResourceBusy as busy:
            return ActionResult(False, f"Cannot re-run post-start for '{target}': {busy}",
                                next_commands=[f"lhpc status {target}"])
        except SourceTxnBlocked as blocked:
            return ActionResult(False, f"Cannot re-run post-start for '{target}': {blocked}",
                                next_commands=[f"lhpc status {target}"])
        except (OSError, PathContainmentError) as exc:
            return ActionResult(False, f"Cannot re-run post-start for '{target}': configuration "
                                f"guard unavailable ({exc})",
                                next_commands=[f"lhpc status {target}"])

    def _poststart_impl(self, target: str, order, band: str,
                        require_all: bool = False) -> ActionResult:
        life = self._lifecycle()
        snap = self.build_snapshot()
        st_index = {c.id: ss.components[c.id]
                    for ss in snap.stacks for c in ss.stack.components}
        out: list[str] = []
        results: list[CompResult] = []

        def record(comp, stack, outcome, summary):
            results.append(CompResult(component=comp.id, stack=stack.id, action="poststart",
                                      outcome=outcome, summary=summary))
            out.append(f"  [{outcome.value}] {comp.id}: {summary}")

        # IDENTITY PRE-PASS: resolve + validate EVERY identity-bearing
        # post-start component on its actual running band UNDER this stable-config guard,
        # BEFORE any runner cancellation or step — the public preflight is UX only, and a
        # config change between it and this locked path must refuse with ZERO mutation
        _id_stash: dict = {}
        _id_problems: list = []
        _id_fields: list = []
        _id_band = band                     # the band the FAILING judgment used (for remedies)
        for stack, comp in order:
            if not comp.post_steps or st_index[comp.id].run_state != RunState.RUNNING:
                continue
            cfg_band = self.running_band(stack.id) if self.stack_bands(stack.id) else (band or "")
            rows = [r for r in self.identity_resolution(comp.id, cfg_band)
                    if r["comp"] == comp.id]
            _id_stash[comp.id] = rows
            if any(r["problem"] for r in rows) and not _id_problems:
                _id_band = cfg_band          # remedies target the judged band, not the raw arg
            _id_problems += [r["problem"] for r in rows if r["problem"]]
            _id_fields += [r["field"] for r in rows if r["problem"]]
        if _id_problems:
            return ActionResult(False, f"Cannot re-run post-start for '{target}': "
                                + "; ".join(dict.fromkeys(_id_problems)),
                                data={"enforce_fields": _id_fields},
                                next_commands=self._identity_config_hints(target, _id_band))
        for stack, comp in order:
            if not comp.post_steps:
                out.append(f"  [skip] {comp.id}: no post-start steps")
                continue
            state = st_index[comp.id].run_state
            if state != RunState.RUNNING:
                record(comp, stack, Outcome.BLOCKED,
                       f"not running ({state.value}) — start it first "
                       f"(lhpc stack start {stack.id})")
                continue
            cfg_band = self.running_band(stack.id) if self.stack_bands(stack.id) else (band or "")
            notes, unverified = life._cancel_post_runners(comp, cfg_band or None)
            out.extend(f"    {n}" for n in notes)
            if unverified:
                record(comp, stack, Outcome.UNVERIFIED,
                       "a live post-start runner could not be verified stopped — refusing to "
                       "run a second sender against the same console")
                continue
            comp_cfg = dict(self.stack_config(comp.id, cfg_band))
            # The payload carries the AUTHORITATIVE effective identity from the pre-pass
            # stash (validated under THIS guard, before any cancellation) — never the raw
            # {callsign} substitution stack_config performs.
            for _r in _id_stash.get(comp.id, ()):
                if _r["effective"]:
                    comp_cfg[_r["name"]] = _r["effective"]
            required_ok, summary = self._run_post_start(
                life, stack, comp, comp_cfg, cfg_band,
                announce=self._log_announcer(comp.id, out), strict=True, require_all=require_all)
            if required_ok is False:
                record(comp, stack, Outcome.FAILED, summary)
            elif required_ok is None:
                # DETACHED: scheduled is NOT applied, so the typed outcome is STARTED, never
                # VERIFIED. The runner probes a possibly-minutes-long readiness window and its
                # terminal sidecar is the only proof of "applied" — STARTED keeps `.ok` True
                # (the scheduling itself succeeded) while `.verified` stays False until that
                # sidecar says otherwise.
                record(comp, stack, Outcome.STARTED,
                       "post-start re-scheduled — outcome will appear in "
                       f"`lhpc status {stack.id}`")
            else:
                record(comp, stack, Outcome.VERIFIED, summary)   # required steps ran synchronously
        ok = bool(results) and all(r.ok for r in results)
        if not results:
            return ActionResult(False, f"Cannot re-run post-start for '{target}': no component "
                                "with post-start steps was found.", details=out,
                                next_commands=[f"lhpc status {target}"])
        # Never claim "applied": a detached re-run has only been SCHEDULED at this point.
        summary = (f"Post-start re-scheduled for '{target}' — the outcome will appear in "
                   f"`lhpc status {target}`." if ok else
                   f"Post-start re-run for '{target}' did not fully apply.")
        return ActionResult(ok, summary, details=out, results=tuple(results),
                            next_commands=[f"lhpc status {target}", f"lhpc logs {target}"])

    def _daemon_radio_modes(self) -> list:
        """`--radio` mode of every OBSERVED daemon process (by command line). A missing/unknown
        mode is returned as None so callers can treat it conservatively."""
        import posixpath
        modes = []
        for argv in self._system.procfs.cmdlines().values():
            if not argv or posixpath.basename(argv[0]) != "loraham_daemon":
                continue
            radio = None
            for i, tok in enumerate(argv):
                if tok == "--radio" and i + 1 < len(argv):
                    radio = argv[i + 1]
                elif tok.startswith("--radio="):
                    radio = tok.split("=", 1)[1]
            modes.append(radio)
        return modes

    def _daemon_claimed_bands(self) -> set:
        """Radio bands CLAIMED by observed daemon PROCESSES (command-line topology — the authoritative
        ownership signal). `--radio 433` → {433}, `--radio 868` → {868}, any missing/unknown mode →
        conservatively {433, 868}. A CONF socket that is unreachable / UNINITIALIZED / FAILED does NOT
        free the radio — the live process still owns it."""
        bands = set()
        for radio in self._daemon_radio_modes():
            if radio == "433":
                bands.add("433")
            elif radio == "868":
                bands.add("868")
            else:
                bands |= set(ALLOWED_BANDS)       # missing / unknown -> conservative
        return bands

    def _daemon_pids_for_band(self, band: str) -> list[int]:
        """PIDs of daemon instances that serve `band` — those launched with `--radio <band>`. Lets a
        per-band Stop signal only that instance, leaving the other band's daemon running."""
        import posixpath
        out = []
        for pid, argv in self._system.procfs.cmdlines().items():
            if not argv or posixpath.basename(argv[0]) != "loraham_daemon":
                continue
            radio = None
            for i, tok in enumerate(argv):
                if tok == "--radio" and i + 1 < len(argv):
                    radio = argv[i + 1]
                elif tok.startswith("--radio="):
                    radio = tok.split("=", 1)[1]
            if radio == band or radio not in ALLOWED_BANDS:
                out.append(pid)              # unknown mode -> conservatively serves the band
        return out

    def _direct_radio_owners(self, bands) -> dict:
        """For each band in `bands`, the id of a RUNNING radio-direct owner of that band — a non-daemon
        component holding an EXCLUSIVE `loraham.radio.<band>` claim on the band it is ACTUALLY using.
        Keyed off resource metadata, NOT process names. Bands with no such owner are absent from the
        result. `meshtastic-<band>` and `daemon-<band>` are the same physical radio, so a running
        meshtastic on 868 makes 868 owned; a serve-all daemon start uses this to serve only free bands
        instead of colliding (which would fail-closed and take the working band down too)."""
        want = {b for b in bands if b in ALLOWED_BANDS}
        if not want:
            return {}
        owners: dict[str, str] = {}
        # fresh=True: this runs right AFTER the stop, so a snapshot cached before it
        # would still show the just-stopped owner as running and keep the marker alive.
        for ss in self.build_snapshot(fresh=True).stacks:
            sid = ss.stack.id
            multi = bool(self.stack_bands(sid))
            for c in ss.stack.components:
                if c.id == self.DAEMON_ID:
                    continue                             # the daemon PROVIDES the radio; not a direct owner
                if ss.components[c.id].run_state not in (RunState.RUNNING, RunState.DEGRADED):
                    continue
                if multi:
                    active = self._live_bands(sid, c.band)
                else:
                    active = {c.band} if c.band else set()
                for r in c.resources:
                    if r.mode == ResourceMode.EXCLUSIVE and r.key.startswith("loraham.radio."):
                        rb = r.key.rsplit(".", 1)[-1]
                        if rb in want and rb in active and rb not in owners:
                            owners[rb] = c.id
        return owners

    def _daemon_arbitrated_bands(self, radio: str = ""):
        """`(kept, skipped)`: the bands a daemon start should actually SERVE, and the bands it SKIPS
        because a running radio-direct stack already owns them (`band -> owner id`). An EXPLICIT
        single-band request (`radio` == '433'/'868') is NEVER arbitrated away — the operator asked for
        that band, so a genuine conflict is surfaced by the normal blocker path, not silently skipped.
        Only a serve-all start (empty `radio`) is arbitrated to the free bands."""
        base = self._daemon_serve_bands(radio)
        if radio in ALLOWED_BANDS:
            return list(base), {}
        owners = self._direct_radio_owners(base)
        kept = [b for b in base if b not in owners]
        return kept, owners

    def _log_announcer(self, comp_id: str, details: list, seen: set | None = None):
        """An `on_log_open(name, path)` callback that records a copy-pasteable
        `[log] <comp> -> tail -f <path>` line into `details` AND emits it LIVE (via `self._progress`,
        which the interactive CLI sets) the MOMENT a job's log file is created — so a long, silent
        build/host-test can be followed from another terminal instead of guessing which file to tail
        (or following a stale unsuffixed one)."""
        seen = seen if seen is not None else set()

        def _cb(_name, path):
            if not path or path in seen:
                return
            seen.add(path)
            line = f"  [log] {comp_id} -> tail -f {path}"
            details.append(line)
            emit = getattr(self, "_progress", None)
            if emit is not None:
                try:
                    emit(line.strip())
                except Exception:
                    pass
        return _cb

    @staticmethod
    def _daemon_failure_reason(lines) -> str:
        """The typed reason `_ensure_daemon` printed (its last `[BLOCKED]`/`[fail]` line), so the
        start result, and the boot-restore record built from it, name the real cause (e.g. a binary
        artifact behind the manifest) instead of a generic readiness failure."""
        for line in reversed(lines):
            text = line.strip()
            for tag in ("[BLOCKED] ", "[fail] "):
                if text.startswith(tag) and text[len(tag):]:
                    return text[len(tag):]
        return "daemon readiness/TX gating failed"

    def _ensure_daemon(self, life, stack, comp, running, radio, start_sid, *,
                       requested_target: str = "", start_scope: str = ""):
        """Ensure the daemon is up FOR THE NEEDED BAND before the app starts.

        "Running" means the band's CONF socket is reachable, not merely that a
        daemon process exists. The daemon is MULTI-INSTANCE (independent process +
        lock per band), so a daemon serving only the OTHER band does not block us —
        we just start a separate instance for the band we need:
          * serving the band      -> just SET the needed TX mode (no restart);
          * not serving the band   -> start a daemon instance with --radio <band>
            (works alongside an instance already serving the other band).
        """
        # Defense-in-depth: never launch the daemon with no hardware configured (the start-path gate
        # already refuses, but a daemon-only/internal caller must fail closed here too).
        if not self.hardware_configured():
            return ["  [BLOCKED] daemon: no radio hardware configured (lhpc hardware)"], False, ""
        # Bands this start must make ready — always explicit single band(s) clamped to the active mode
        # (M-1): lhpc never serves an excluded band; it always spawns one process per band. Each needed
        # band is spawned as an explicit `--radio <band>` process below.
        needed, owned = self._daemon_arbitrated_bands(radio)
        views = {b: daemon_control.read_view(self._system, b) for b in needed}
        # Classify each band from CONF readiness AND process topology. A CONF socket that is
        # unreachable does NOT mean the radio is free: an observed daemon PROCESS may still hold it.
        #   READY (retain) | reachable-not-READY (fail) | CONF-down-but-process-claims-it (fail, do
        #   NOT relaunch/SET) | truly absent (safe to launch).
        claimed = self._daemon_claimed_bands()
        not_ready = [b for b in needed if views[b].reachable and not views[b].ready]
        claimed_down = [b for b in needed if not views[b].reachable and b in claimed]
        absent = [b for b in needed if not views[b].reachable and b not in claimed]
        lines, ok_all, gate = [], True, ""
        # Band arbitration: skip bands a running radio-direct stack owns (meshtastic-<band> and
        # daemon-<band> are the SAME physical radio) — serve only the free band(s) instead of colliding,
        # which would fail-closed and take the already-working band down too.
        for b in sorted(owned):
            lines.append(f"  [skip] {b} owned by {owned[b]} — daemon serving "
                         f"{'+'.join(needed) if needed else 'no free band'} only")
        if not needed:
            # Every active band is owned by a running radio-direct stack -> clean refusal, no launch.
            lines.append("  [BLOCKED] daemon: all active radio bands are owned by a running radio-"
                         "direct stack — stop it, or start the daemon on a free band")
            return lines, False, ""
        # Note when a single radio mode narrows an all-bands request (only when nothing was arbitrated
        # away — the skip line already explains a band-owned narrowing).
        if (radio or "") == "" and not owned and set(needed) != set(ALLOWED_BANDS):
            lines.append(f"  [note] radio mode {self.radio_mode()}: daemon serves {'+'.join(needed)}")
        for b in not_ready:
            ok_all = False
            # A bare RADIO=FAILED leaves the operator with nothing to do (a MeshCom start
            # blocked on it for an hour). Name the two causes that actually produce it on this
            # hardware: another process clocking the SHARED SPI bus while the daemon probes the chip
            # (the daemon's /run/lock/loraham serialises its own instances, never a foreign binary),
            # and an RF front-end wedged from an earlier run — which only a power cycle clears.
            lines.append(f"  [fail] daemon on {b}: reachable but RADIO="
                         f"{views[b].radio_state or 'unknown'} (not READY) — dependent launch blocked")
            lines.append(f"    | {self._radio_not_ready_hint(b)}")
        for b in claimed_down:
            ok_all = False
            lines.append(f"  [fail] daemon on {b}: CONF socket unreachable but a daemon process "
                         f"still holds the radio — not relaunched (resolve the stuck instance first)")
        started: set = set()
        if absent:
            if comp.source and not self._source_present(comp):
                lines.append("  [BLOCKED] daemon: not installed (lhpc install daemon)")
                return lines, False, ""
            # The daemon takes this separate path, so the same refusal as the generic start:
            # an installed binary artifact behind the manifest pins is never spawned — it
            # would be launched with argv it does not know (the RF-log options were the first
            # case) — whether the daemon itself or a dependent stack asked for it.
            if (_behind := self.binary_behind(comp)):
                lines.append(f"  [BLOCKED] daemon: {_behind}")
                return lines, False, ""
            if not self.is_built(comp):
                lines.append("  [BLOCKED] daemon: not built — build it first (lhpc build daemon)")
                return lines, False, ""
            # Start ONE per-band instance for EACH missing band — lhpc NEVER launches `--radio
            # both` (a mode the operator may still start manually); it runs an independent
            # `--radio <band>` per band. The TX mode is applied LIVE once the socket is up.
            for b in sorted(absent):
                dparams = dict(self.stack_config("daemon"))
                dparams["radio"] = b
                # v112: one process per band with plain (non band-suffixed) flags. `--hw` comes from
                # the hardware setup; --tx-mode/--cad-monitor/--cad-rssi carry THIS band's stored value.
                dparams["hw"] = self.hw_preset_for_band(b)
                dparams["txmode"] = dparams.get(f"tx_{b}", "managed")
                dparams["cadmon"] = dparams.get(f"cadmon_{b}", "off")
                dparams["cadrssi"] = dparams.get(f"cadrssi_{b}", "-90")
                # The +20 dBm permission: the injected `hipower` is a flag-kind param that emits
                # the bare `--high-power` when on and NO token when off. ONE source of truth --
                # `high_power_for_band`, a literal compare against the canonical `on` -- so a
                # stray or hand-edited stored value can never enable a safety-relevant mode
                # through the permissive flag emitter (the daemon is skipped by the generic
                # saved-launch refusal; this is its gate).
                dparams["hipower"] = "on" if self.high_power_for_band(b) else "off"
                # The PA over-current limit (daemon 1.3.0 `--ocp`): a saved value this band's chip
                # cannot represent is refused here, never sent or clamped.
                ocp = self.ocp_for_band(b)
                if ocp is None:
                    lines.append(f"  [BLOCKED] daemon on {b}: ocp_{b} = "
                                 f"{dparams.get(f'ocp_{b}')!r} is not a PA limit this band's chip "
                                 f"can represent — set it with: lhpc hardware --ocp {b} auto|MA")
                    return lines, False, ""
                dparams["ocp"] = str(ocp)
                # The RF-log switch is STACK-level (band-less) while the file it names is per
                # band (`{band}` in the run line); the path itself is never a stored value.
                dparams[_rflog.RF_LOG_PARAM] = self._resolved_param_value(
                    "daemon", "run", self.DAEMON_ID, _rflog.RF_LOG_PARAM, b)
                res = life.start(stack, comp, dparams, band=b,
                                 requested_target=requested_target,
                                 start_scope=start_scope)
                base = f"start daemon --radio {b}"
                if not res.ok:
                    lines.append(f"  [fail] {base}: {res.detail}")
                    return lines, False, ""
                lines.append(f"  [ok] {base}")
                started.add(b)
        # Verify + apply, once per band that should now be READY (retained or freshly started;
        # not-ready bands were already failed above and are skipped).
        for b in needed:
            if b in not_ready or b in claimed_down:
                continue                      # failed bands: no retain, no CONF SET
            if b in started:
                if not self._verify_band_up(b):
                    ok_all = False
                    lines.append(f"  [fail] {b} CONF socket never came up — the daemon failed "
                                 f"to init on {b} (radio/SPI busy or a stale lock); see its log.")
                    continue
            else:
                lines.append(f"  [ok] daemon already serving {b}")
                # A band already serving ANOTHER running stack must NOT be reconfigured — a daemon
                # (re)start in a new mode (e.g. FSK) must never disrupt a client already using this
                # band (its config is whatever that client needs). Freshly-started bands still get
                # this start's params applied.
                daemon_sid = self.stack_of(self.DAEMON_ID) or "daemon"
                others = [d for d in self.stop_dependents(daemon_sid, bands={b}) if d != start_sid]
                if others:
                    lines.append(f"  [keep] {b} in use by {', '.join(others)} — daemon "
                                 f"config left unchanged")
                    continue
                # The SAME rule for THIS stack's own running components. The params below are
                # applied once before a stack's components start, and the app then overwrites the
                # radio params it owns; when a component is ALREADY running on this band there is
                # no such overwrite, so re-applying would move the radio off the frequency that app
                # tuned — the band goes silent while every status still reads healthy, and only a
                # restart of the app recovers it. Starting a second component (kiss-serial next to
                # a running kiss-tnc) must therefore leave the band's config alone.
                if self._stack_running_on_band(start_sid, b):
                    lines.append(f"  [keep] {b} already served for '{start_sid}' — daemon "
                                 f"config left unchanged")
                    continue
            plines, tx_ok, pgate = self._apply_stack_daemon_params(start_sid, b)
            lines.extend(plines)
            if pgate:
                gate = gate or pgate            # the stack's config, not the daemon: not a failure here
            else:
                ok_all = ok_all and tx_ok
        return lines, ok_all, gate

    def _verify_band_up(self, band: str) -> bool:
        """Poll a band's CONF socket until the daemon reports RADIO=READY, up to the
        verify timeout. A reachable daemon that is still UNINITIALIZED or FAILED is NOT
        up: the radio inits asynchronously, so we wait for readiness (not mere socket
        reachability). With the timeout disabled (tests) it is a single bounded check."""
        if self.DAEMON_VERIFY_TIMEOUT_S <= 0:
            return daemon_control.read_view(self._system, band).ready
        waited = 0.0
        while waited < self.DAEMON_VERIFY_TIMEOUT_S:
            time.sleep(self.DAEMON_VERIFY_POLL_S)
            waited += self.DAEMON_VERIFY_POLL_S
            if daemon_control.read_view(self._system, band).ready:
                return True
        return False

    def _running_bands_of(self, ss, run_comps) -> set:
        """Radio band(s) the RUNNING components of a snapshot stack actually use: the
        effective band for a band-switchable stack, else the components' declared bands."""
        if self.stack_bands(ss.stack.id):                  # band-switchable -> running band
            eb = self._effective_band(ss.stack.id)
            return {eb} if eb else set()
        return {c.band for c in run_comps if c.band}

    def _stack_running_on_band(self, stack_id: str, band: str) -> bool:
        """Does `stack_id` ALREADY have a running component on `band`? Read from live run state
        (the band marker alone can outlive the process it describes)."""
        up = (RunState.RUNNING, RunState.DEGRADED)
        for ss in self.build_snapshot().stacks:
            if ss.stack.id != stack_id:
                continue
            run_comps = [c for c in ss.stack.components
                         if ss.components[c.id].run_state in up]
            return bool(run_comps) and band in self._running_bands_of(ss, run_comps)
        return False

    def _band_used_by_others(self, target: str, band: str) -> list[str]:
        """Running daemon-client stacks OTHER than `target`'s own on `band` — the rule `_ensure_daemon`
        keeps a band's config by. A client's daemon-param apply and TX test refuse such a band."""
        sid = self._owner_stack_id(target)
        daemon_sid = self.stack_of(self.DAEMON_ID) or "daemon"
        return [d for d in self.stop_dependents(daemon_sid, bands={band}) if d != sid]

    def _saved_launch_refusal(self, target: str, band: str, op: str):
        """The typed refusal for SAVED launch values the launch itself would reject, or None.
        A dry expansion of every run-order component's argv through the launch's own expander
        (`commands.expand_argv`, the same validators, the same component-scoped config the launch
        builds) — so an obsolete or hand-edited stored value is found by the PLAN and by the apply
        before any mutation (owner stops, config generation, a restart's stop leg), never by the
        launch after the running stack is already down. Only a parameter VALUE problem refuses
        here; anything else the launch reports typed as it always did. No pre-step runs."""
        from . import commands
        order = self._run_order(target)
        if not order:
            return None
        hint = self._launch_band_hint(target, band)
        cfg_band = self._config_band(target, hint)
        run_over, _file_over = self._materialize_inherited_identity(target, hint)
        operator, runtime = self.config().operator, str(self._paths.runtime_root)
        is_stack = self.stack(target) is not None
        for _stack, comp in order:
            # The daemon's argv is built by `_ensure_daemon` per band (radio/hw/txmode/cadmon/
            # cadrssi injected at spawn, the stored keys never read) — its stored file is no
            # launch input, so a stale key there must not refuse every client stack.
            if not comp.run_argv or comp.id == self.DAEMON_ID:
                continue
            cfg = dict(self.stack_config(comp.id, cfg_band))
            if is_stack or comp.id == target:
                cfg.update(self._overrides_for_comp(target, "run", run_over, comp.id))
            # The source path only feeds `{source}` token substitution here — values, not paths,
            # are what this seam judges — so it is resolved from Paths, never from a Lifecycle.
            try:
                src = (str(self._paths.resolve_source(comp.source.path)) if comp.source
                       else runtime)
                commands.expand_argv(comp.run_argv, comp, cfg, operator, runtime, src, cfg_band)
            except validators.ValidationError as exc:
                sid = self.stack_of(comp.id) or comp.id
                return ActionResult(False, f"Cannot {op} '{target}': invalid saved configuration "
                                           f"for {comp.id} — {exc}",
                                    next_commands=[f"lhpc config {sid}"])
            except (commands.CommandError, OSError, PathContainmentError):
                continue                     # not a stored value; the launch reports it typed
        # CONFIG-FILE cross-field rules, which argv expansion cannot see. Config GENERATION is
        # the authoritative check and stays the last one, but on a RESTART it runs after the
        # node has already been stopped: a half-configured Internet IFAC saved cleanly (the save
        # can only judge the endpoint, not the passphrase) would take a working node down and
        # leave it down. So the same rule refuses here, before any mutation.
        why, reason = self._reticulum_internet_verdict(order, cfg_band)
        if why:
            return ActionResult(False, f"Cannot {op} '{target}': {why}",
                                next_commands=[f"lhpc config {_ri.STACK_ID}"],
                                data={"reason": reason})
        # RNode framing needs the driver that implements it. A driver built before the switch
        # existed ignores the unknown key and starts BARE — the box would look up and talk to
        # nobody. So the switch requires the BUILT driver's framing module (what runs, not what
        # the manifest names: a known-working composition may still hold the old driver).
        if (any(c.id == _ri.NODE_ID for _stack, c in order)
                and _ri.enabled(self._resolved_param_value(_ri.STACK_ID, "file", _ri.NODE_ID,
                                                       _ri.RNODE_FRAMING, cfg_band) or "")
                and not self._rns_driver_has_framing()):
            return ActionResult(
                False, f"Cannot {op} '{target}': RNode framing is on, but the built LoRa driver "
                       "predates it and would transmit bare — update the driver and rebuild",
                next_commands=["lhpc update rns-lora-interface", f"lhpc build {_ri.STACK_ID}"],
                data={"reason": _ri.REASON_FRAMING_DRIVER_OLD})
        return None

    def _rns_driver_has_framing(self) -> bool:
        """Whether the LoRa driver BUILT into the RNS node's venv carries the RNode framing
        module (the file the node imports; `loraham_rns/framing.py` arrived with the switch)."""
        stack = self.stack(_ri.STACK_ID)
        node = stack.component(_ri.NODE_ID) if stack else None
        if node is None or node.source is None:
            return False
        venv = self._paths.resolve_source(node.source.path) / ".venv" / "lib"
        return any(venv.glob("python3*/site-packages/loraham_rns/framing.py"))

    def _reticulum_internet_preflight(self, order, band: str) -> str:
        """The sentence of `_reticulum_internet_verdict` ("" = nothing to refuse)."""
        return self._reticulum_internet_verdict(order, band)[0]

    def _reticulum_internet_verdict(self, order, band: str) -> tuple[str, str]:
        """The Internet interface's cross-field verdict for a run order that contains the RNS
        node, as `(sentence, reason token)` — both "" when there is nothing to refuse or the
        node is not in this order. The token is `reticulum_interfaces.reason()`'s, or
        "secrets-unreadable" when the secrets file itself was the refusal. Resolved exactly the way
        GENERATION resolves it, because anything this seam judges differently is a node stopped
        by a restart and then refused — which is the whole defect this preflight exists for:

          * the passphrase counts only as a NON-EMPTY STRING. A bare number in `secrets.toml` is
            absent to generation, so `str()`-ing it here would pass a config generation rejects;
          * a secrets file the loader refuses (group/other-readable) is refused HERE too. Its
            message is predictable and safe — it names the file and the `chmod` remedy, never a
            value — so there is no reason to let the stop happen first.

        The enabled check comes before the load: a disabled interface needs no secret, and a
        broken secrets file must not refuse a start that would never have read it."""
        if not any(c.id == _ri.NODE_ID for _stack, c in order):
            return "", ""
        vals = {n: (self._resolved_param_value(_ri.STACK_ID, "file", _ri.NODE_ID, n, band) or "")
                for n in (_ri.ENABLED, _ri.HOST, _ri.PORT, _ri.NETNAME)}
        if not _ri.enabled(vals[_ri.ENABLED]):
            return "", ""
        try:
            secrets = _config.load_secrets(self._paths)
        except _config.ConfigError as exc:
            return str(exc), "secrets-unreadable"
        key = (secrets.get(_ri.STACK_ID) or {}).get(_ri.NETKEY)
        vals[_ri.NETKEY] = key if isinstance(key, str) else ""
        return _ri.problem(vals), _ri.reason(vals)

    def _identity_refusal(self, target: str, band: str, op: str):
        """The typed refusal for an unusable SAVED identity, or None — the one wording the start
        plan, the restart preflight and the locked apply-side recheck share."""
        id_ok, id_fields, id_msg = self.enforce_identity(target, band)
        if id_ok:
            return None
        return ActionResult(False, f"Cannot {op} '{target}': {id_msg}",
                            data={"enforce_fields": id_fields},
                            next_commands=self._identity_config_hints(target, band))

    def _order_radio(self, target: str, op_band: str):
        """`(run order, the daemon's requested radio)` for an operation on `op_band` — the radio
        decides which bands the lock bundle covers and what the healthy shortcut judges."""
        order = self._run_order(target)
        radio = ""
        if order:
            _r, _ = self._daemon_needs(order, op_band)
            radio = _r or ""
        return order, radio

    def _position_refusal(self, target: str, op: str):
        """`(refusal or None, position, note)` from the MeshCore position decision — the one
        wording the start and restart plans and applies share."""
        position, note = self.meshcore_position(target)
        if position is None:
            return (ActionResult(False, f"Cannot {op} '{target}': {note}",
                                 next_commands=[f"lhpc status {target}"]), None, note)
        return None, position, note

    def _start_outer_refusal(self, target: str, order, band: str, radio: str,
                             position: dict | None, note: str):
        """`(refusal or None, position, note)`: the public start's identity and MeshCore position
        decision, taken by the plan and the apply at the same point and in the same order.
        `order` is None/empty for an unknown target or nothing to run — `_start_impl` reports
        that properly, so no snapshot is taken then; nor for an already-healthy no-op."""
        if order and (_r := self._identity_refusal(target, band, "start")) is not None:
            return _r, position, note
        if position is None and order and not self._order_already_healthy(order, radio):
            return self._position_refusal(target, "start")
        return None, position, note

    def _start_preflight_refusal(self, target: str, order, band: str, radio: str, op: str, *,
                                 check_blockers: bool, render: bool,
                                 exclude_holders=frozenset()):
        """The one firewall/ambiguity/blocker wording the start plan, the start apply and the
        restart (plan and apply) share, or None. The FW-R8 exposure gate first (`render=False`
        for a plan: no apply-script write); then an AMBIGUOUS flat value (a run/file param name
        declared by >= 2 owner-stack components, with a flat value present and no component-scoped
        value); then, only if `check_blockers`, the running holders of a needed resource —
        except `exclude_holders` (stack ids a restart's own stop takes down)."""
        _fw_ok, _fw_msg, _fw_cmds = self.firewall_gate_stack_start(target, band=band,
                                                                   render=render, op=op)
        if not _fw_ok:
            return ActionResult(False, f"Cannot {op} '{target}': {_fw_msg}",
                                next_commands=_fw_cmds, data={"firewall_gate": "pending"})
        _amb = self._config_ambiguity(target, order, band)
        if _amb is not None:
            return ActionResult(False, f"Cannot {op} '{target}': {_amb}",
                                next_commands=[f"lhpc config {target}"])
        if not check_blockers:
            return None
        blockers = [bl for bl in self.run_blockers(target, band, radio)
                    if bl["holder_stack"] not in exclude_holders]
        if not blockers:
            return None
        owners = [lbl for lbl, _ in self.owner_stops(blockers)]
        details = [f"  {bl['resource']} held by running stack '{bl['holder_stack']}'"
                   for bl in blockers]
        return ActionResult(
            False,
            f"Cannot run '{target}': {', '.join(owners)} must be stopped first.",
            details=details,
            next_commands=self.owner_stop_commands(blockers))

    def _safe_unlink(self, path) -> bool:
        """Best-effort delete of a runtime-owned marker/leaf (contained, no symlink-
        follow). NON-THROWING: `Paths.safe_unlink` can raise ordinary FS errors
        (PermissionError, etc.) as well as a containment error — stale-marker cleanup runs
        AFTER lifecycle work, so it must never convert a completed start into an unhandled
        exception. Returns False if the leaf could not be removed."""
        try:
            self._paths.safe_unlink(path)
            return True
        except (OSError, PathContainmentError):
            return False

    def _safe_marker_write(self, path, text: str) -> bool:
        """Write a small runtime-owned marker atomically THROUGH the safe runtime FS
        (containment + no-follow leaf + fsync). Returns False if it could NOT be persisted
        (a symlink-leaf/escaping path or an I/O error) so a caller whose operational truth
        depends on the marker can reflect the failure in its typed result."""
        from . import runtime_fs
        from .paths import PathContainmentError as _PCE
        try:
            runtime_fs.write_marker(self._paths, path, text)
            return True
        except (OSError, _PCE):
            return False

    def _interactive_marker(self, stack_id: str):
        sid = validators.path_component(stack_id, field="stack id")
        return self._paths.under("state", "interactive", f"{sid}.show")

    def mark_interactive(self, stack_id: str, band: str = "") -> bool:
        """Remember that the operator asked to run an interactive app (so the dash
        shows its terminal-command block); stores the chosen band. Returns False if the
        marker could not be persisted."""
        return self._safe_marker_write(self._interactive_marker(stack_id), band)

    def interactive_band(self, stack_id: str) -> str | None:
        """The band an interactive app was started on, or None if not active."""
        from . import runtime_fs
        try:
            return runtime_fs.read_text(self._paths, self._interactive_marker(stack_id)).strip()
        except (OSError, ValueError):       # missing/unreadable/symlinked -> not active
            return None

    @invalidates_snapshot
    def dismiss_interactive(self, stack_id: str) -> None:
        self._safe_unlink(self._interactive_marker(stack_id))

    def clear_stale_interactive(self, keep: str = "") -> list[str]:
        """Drop interactive markers for apps that aren't actually running — they
        were launched/marked but never run (or since stopped). Called when another
        stack starts so the dash doesn't keep showing a prior interactive app's
        command block. `keep` is the stack just started (its marker is preserved)."""
        idir = self._paths.runtime_root / "state" / "interactive"
        if not idir.exists():
            return []
        up = (RunState.RUNNING, RunState.DEGRADED)
        live = {cid: st.run_state for ss in self.build_snapshot().stacks
                for cid, st in ss.components.items()}
        cleared = []
        for f in idir.glob("*.show"):
            sid = f.stem
            if sid == keep:
                continue
            s = self.stack(sid)
            # Judge the marker by the COMMAND-BEARING component: the interactive main
            # (chat), or the stack's interactive sidecar when the main is not interactive
            # (voice's terminal variant — its GTK main is never RUNNING on a Lite box, and
            # judging by it dismissed a freshly presented command whenever any other stack
            # started).
            comp = None
            if s is not None:
                main = s.main_component
                comp = main if (main and main.interactive) else next(
                    (c for c in s.components if c.interactive), None)
            if not (comp and live.get(comp.id) in up):
                self.dismiss_interactive(sid)
                cleared.append(sid)
        return cleared

    def _band_marker(self, stack_id: str):
        sid = validators.path_component(stack_id, field="stack id")
        return self._paths.under("state", "running", f"{sid}.band")

    def _set_running_band(self, stack_id: str, band: str) -> bool:
        """Persist the running band (drives multi-band decisions + dashboard). Returns
        False if it could not be written."""
        return self._safe_marker_write(self._band_marker(stack_id), band)

    def running_band(self, stack_id: str, default: str = "") -> str:
        from . import runtime_fs
        try:
            return runtime_fs.read_text(self._paths, self._band_marker(stack_id)).strip() or default
        except (OSError, ValueError):
            return default

    def _live_bands(self, stack_id: str, declared: str) -> set:
        """The band(s) a LIVE multi-band component must be arbitrated against.

        With trustworthy evidence (marker / interactive launch band) that is the one
        band it is on. WITHOUT it, every band it could be on: falling back to the
        DECLARED band here let a node actually running on 433 with a lost marker be
        treated as an 868 holder, so a second exclusive 433 owner could be admitted
        onto the same radio. Unknown is not the default band; unknown is all of them.
        """
        eb = self._effective_band(stack_id, "")
        if eb in ALLOWED_BANDS:
            return {eb}
        allowed = self.stack_bands(stack_id)
        if len(allowed) > 1:
            return set(allowed)
        return {declared} if declared else set()

    def _dep_band_block(self, target: str, order, band: str):
        """ActionResult when a chain member of ANOTHER stack is live on a different band, else None.

        graywolf on 868 pulls in the KISS TNC and the daemon; with that chain live on 433 every
        member read already_healthy, so nothing was started, nothing was blocked, and graywolf
        came up on 868 talking to a 433 TNC — one radio, two bands claimed, no warning. Members
        are skipped as conflict holders precisely BECAUSE they are in this run order, so the
        band mismatch has to be caught here or not at all.

        `band` is the band this start will ACTUALLY use (explicit request or the resolved
        config band); "" = nothing to judge. Called from the start path and from restart's
        preflight — the refusal must fire BEFORE restart's stop, or restart degrades to
        stop-only and leaves the stack down.

        ONE fresh snapshot at most, shared across all candidate dependencies (each
        `_band_owner_is_up` probe is a full system scan — expensive on a Pi Zero) and built
        only when a marker actually mismatches.
        """
        if not band:
            return None
        _sid = self.stack_of(target) or target
        snap = None
        for _dep in dict.fromkeys(self.stack_of(c.id) for _, c in order):
            if not _dep or _dep == _sid:
                continue
            _dlive = self.running_band(_dep, "")
            if not (_dlive and _dlive != band and self.stack_bands(_dep)):
                continue
            if snap is None:
                snap = self.build_snapshot(fresh=True)
            if not self._band_owner_is_up(_dep, snap):
                continue
            return ActionResult(
                False,
                f"Cannot start '{target}' on {band} MHz: it needs '{_dep}', which is "
                f"already running on {_dlive} MHz.",
                details=["  [blocked] the radio serves ONE band at a time, and this "
                         f"start would need '{_dep}' on {band} MHz",
                         f"  Stop '{_dep}' first, or start '{target}' on {_dlive} MHz "
                         "instead."],
                # `lhpc stack start` takes no --band (the band is chosen in the console),
                # so the remedy is the same shape as the own-stack refusal: stop the
                # holder, then start. Naming a flag that does not exist would be worse
                # than naming none.
                next_commands=[f"lhpc stack stop {_dep} --yes",
                               f"lhpc stack start {target} --yes"])
        return None

    def _band_owner_is_up(self, stack_id: str, snap=None) -> bool:
        """Whether a band-carrying component of this stack is running right now.
        `snap` reuses a caller-built snapshot; None probes fresh (a full system scan)."""
        up = (RunState.RUNNING, RunState.DEGRADED)
        for ss in (snap or self.build_snapshot(fresh=True)).stacks:
            if ss.stack.id != stack_id:
                continue
            for c in ss.stack.components:
                st = ss.components.get(c.id)
                if (c.band or c.bands) and st is not None and st.run_state in up:
                    return True
        return False

    @staticmethod
    def needs_display(comp) -> bool:
        """Whether this component is a GUI app. Derived from its own declarations: a
        component with a GUI-only requirement (`gui = true`) is one that needs a
        display — the same predicate the build-time GUI skip already uses."""
        return any(getattr(r, "gui", False) for r in (comp.requires or ()))

    @staticmethod
    def display_available() -> bool:
        """Whether a graphical session is actually running HERE.

        Not `DISPLAY` in our environment: the controller runs as a systemd user
        service and never inherits it. The honest evidence is a live compositor
        socket — Wayland in the user runtime dir, or an X11 socket.
        """
        import glob

        uid = os.getuid()
        if glob.glob(f"/run/user/{uid}/wayland-[0-9]*"):
            return True
        return bool(glob.glob("/tmp/.X11-unix/X[0-9]*"))

    @staticmethod
    def _unmet_dependencies(comp, order, results) -> list:
        """Dependencies of `comp` (transitively, within this run order) that did NOT
        reach a successful outcome. Empty when every one of them is up.

        """
        by_id = {c.id: c for _, c in order}
        want, seen = list(comp.depends_on or ()), set()
        while want:                                  # transitive closure
            dep = want.pop()
            if dep in seen:
                continue
            seen.add(dep)
            want.extend(by_id[dep].depends_on or () if dep in by_id else ())
        good = {Outcome.VERIFIED, Outcome.STARTED, Outcome.ALREADY_HEALTHY}
        outcome = {r.component: r.outcome for r in results}
        # A dependency that was never part of this run is not judged here: it is either
        # already running (the snapshot gate) or outside the requested scope.
        return sorted(d for d in seen
                      if d in outcome and outcome[d] not in good)

    def runs_on_band(self, stack_id: str, band: str) -> bool:
        """Whether a MULTI-band stack is running on THIS band.

        A multi-band stack runs on exactly one band at a time, so "is a band-carrying
        component up?" is the same answer in every column — it rendered the stack as
        running on 433 while it was actually on 868. Evidence order: the start marker,
        then the band an interactive app was launched on, then the DECLARED primary.
        Never "every band it could use".
        """
        return self._effective_band(stack_id, self._config_band(stack_id, "")) == band

    def running_lora_stacks(self, band: str) -> list:
        """Daemon-client (LoRa) stacks currently running on `band` — the ones a live switch to
        MODE=FSK would break. Used to warn on the daemon's live-setting confirm."""
        from . import daemon_params
        out = []
        for sid in daemon_params.CLIENT_STACKS:
            if self.stack_running(sid) and \
                    self._effective_daemon_band(sid, self.running_band(sid)) == band:
                out.append(sid)
        return out

    def stack_bands(self, target: str) -> tuple:
        """Bands the operator may choose for `target` (empty = single fixed band), NARROWED to the
        active radio mode (in 'both' this is identity). Owner-stack scoped, so a direct component
        target uses its stack's band choices."""
        s = self._owner_stack(target)
        active = self.active_bands()
        for c in (s.components if s else ()):
            if c.bands:
                return tuple(b for b in c.bands if b in active)
        return ()

    def _config_band(self, target: str, band: str) -> str:
        """The band key used for per-band config storage ("" for single-band stacks).
        With no explicit band, defaults to the stack's declared primary band (the
        band-component's `band`, e.g. 868 for meshtastic) rather than the first
        allowed band."""
        allowed = self.stack_bands(target)
        if not allowed:
            return ""
        if band in allowed:
            return band
        s = self._owner_stack(target)
        for c in (s.components if s else ()):
            if c.bands and c.band in allowed:
                return c.band
        return allowed[0]

    def _source_present(self, comp) -> bool:
        """The ONE presence policy for a component's managed source (`source_fs.source_present`:
        a real directory leaf, no-follow) — build, test, start and the install planner all ask
        this, never a symlink-following `exists()`."""
        from . import source_fs
        return source_fs.source_present(self._paths, self._lifecycle().source_dir(comp))

    def is_installed(self, target: str) -> bool:
        """Whether a stack's managed material is present (nothing to install if it declares no
        source). A binary-covered main component counts as installed on its RECEIPT, not on a
        source directory it has no reason to own."""
        from . import source_fs
        s = self.stack(target)
        main = s.main_component if s else None
        if not main or not main.source:
            return True
        if self.binary_covers(main.id):
            return True
        return source_fs.source_present(self._paths, self._paths.resolve_source(main.source.path))

    def _radio_competitors(self, entries: list[dict]) -> list[str]:
        """Names of the stacks in `entries` that genuinely compete for one radio's tuning.

        Sharing a band is not the same as competing for it. A stack whose RF path runs THROUGH
        another stack in the same group is that stack's CLIENT — graywolf transmits by handing
        frames to loraham-kiss-tnc, which owns the tuning — so listing both as a conflict is
        simply wrong, and it is the kind of red banner an operator learns to ignore. Drop any
        entry that depends (directly or transitively) on a component of another entry, and a
        group that collapses to one claimant is no conflict at all.
        """
        if len(entries) < 2:
            return []
        stack_of = {c.id: st.id for st in self.stacks() for c in st.components}
        ids = {e["id"] for e in entries}
        competitors = []
        for e in entries:
            st = self.stack(e["id"])
            upstream = {stack_of.get(c.id) for c in self._tx_chain_components(st)} if st else set()
            if ids & (upstream - {e["id"]}):
                continue                      # reaches the radio through another listed stack
            competitors.append(e["name"])
        return competitors if len(competitors) > 1 else []

    def stack_running(self, target: str) -> bool:
        """True if the stack's main component is currently running/degraded."""
        s = self.stack(target)
        main = s.main_component if s else None
        if not main:
            return False
        for ss in self.build_snapshot().stacks:
            if ss.stack.id == s.id:
                st = ss.components.get(main.id)
                return bool(st and st.run_state in (RunState.RUNNING, RunState.DEGRADED))
        return False

    def dash_signature(self, restart_required=None) -> str:
        """A compact signature of the dashboard's STRUCTURAL state — which
        components are running, which bands the daemon serves, and which
        interactive apps are marked. The web polls this cheaply and only does a
        full reload when it changes, so live-monitor fields (RSSI/feed/…) update in
        place without the whole page reflowing. Excludes fast-changing telemetry."""
        snap = self.build_snapshot(sources=False)            # run states only: no git per poll
        up = (RunState.RUNNING, RunState.DEGRADED)
        running = sorted(cid for ss in snap.stacks for cid, st in ss.components.items()
                         if st.run_state in up)
        # D: = bands with a USABLE radio (RADIO=READY), NOT merely reachable — a FAILED or
        # UNINITIALIZED daemon must never appear "served".
        usable = [b for b in self.RADIO_BANDS if self.daemon_view(b).ready]
        from . import runtime_fs
        from .paths import PathContainmentError
        idir = self._paths.runtime_root / "state" / "interactive"
        marks = []
        # Descriptor-safe enumeration (no Path.exists()/glob): a symlinked/escaping marker
        # dir or a symlinked marker leaf is skipped, never followed.
        try:
            entries = runtime_fs.scandir_nofollow(self._paths, idir)
        except PathContainmentError:
            entries = []
        for name, is_link in sorted(entries):
            if is_link or not name.endswith(".show"):
                continue
            stem = name[:-len(".show")]
            try:
                marks.append(f"{stem}={runtime_fs.read_text(self._paths, idir / name).strip()}")
            except (OSError, ValueError):
                marks.append(stem)
        # dashboard reloads when the yellow flag flips; the route hands in the list it already read
        rr = self.restart_required_stacks() if restart_required is None else list(restart_required)
        # BOOTING components (post-start runner still applying settings, e.g. MeshCom's
        # callsign push): the yellow 'booting' state must flip the signature when it
        # clears, or the dash keeps showing 'booting' after the node is serving.
        booting = sorted(cid for cid in running if self._component_booting(cid))
        return ("R:" + ",".join(running) + ";D:" + ",".join(usable)
                + ";I:" + ",".join(marks) + ";RR:" + ",".join(rr)
                + ";B:" + ",".join(booting)
                + ";M:" + self.radio_mode())      # mode switch reflows the columns -> force a reload

    def manual_start_command(self, comp) -> str:
        """The command the operator runs in a terminal to start an interactive
        component (the controller never starts these itself). Rendered from the
        SAME structured command spec, shell-quoted — values are individual argv
        tokens, never interpolated into shell syntax."""
        import shlex

        from . import commands
        if not comp.run_argv:
            return "(no run command)"
        op = self.config().operator
        runtime = str(self._paths.runtime_root)
        src = str(self._paths.resolve_source(comp.source.path)) if comp.source else runtime
        # The saved values a start would use, resolved as `_saved_launch_refusal` does: the band
        # the stack runs (or was presented) on, its stored config and the inherited identity.
        sid = self.stack_of(comp.id) or comp.id
        hint = self._launch_band_hint(sid, "")
        band = self._config_band(sid, hint)
        params = dict(self.stack_config(comp.id, band))
        run_over, _file_over = self._materialize_inherited_identity(sid, hint)
        params.update(self._overrides_for_comp(sid, "run", run_over, comp.id))
        cmd = commands.display_command(comp, op, runtime, src, band, params)
        if not cmd:
            return "(no run command)"
        cwd = (commands._paths_subst(comp.run_cwd, runtime, src, "")
               if comp.run_cwd else src)
        # Restore the terminal afterwards. A full-screen TUI (NomadNet) puts the tty
        # in raw mode; interrupted with Ctrl+C it exits WITHOUT restoring it, leaving
        # ECHO and ICANON off — the shell then looks dead, echoing nothing. Measured:
        # ECHO/ICANON stay cleared after SIGINT. `stty sane` costs nothing on a clean
        # exit and is silenced when there is no tty (piped/captured).
        return f"cd {shlex.quote(cwd)} && {cmd}; stty sane 2>/dev/null"

    def _client_interfaces(self, status, stack_id: str = "") -> list[dict]:
        """User-facing interfaces a client connects to (KISS TCP, web UIs, serial
        PTYs) that are currently present — NOT internal transport sockets.

        Every pin must point at an address that WORKS for the current viewer. A proxied web UI links
        to the PROXY, not its loopback upstream (handing a remote browser `http://127.0.0.1:18083` is
        a dead link); its scope comes from `_stack_listen_scope`. For a NON-proxied listener the host
        is decided from the LIVE socket table per port (`listener_scope`) — the ground truth, NOT the
        saved bind policy: a fixed-loopback service with no bind knob (MeshCom QEMU forwards 127.0.0.1)
        stays loopback, and a pending-restart bind change does not move the link until the RUNNING
        listener actually changes. Only an "exposed" listener sets `remote_ok`, so the template points
        the pin at `req_host` (127.0.0.1 locally, the AP/LAN IP remotely); loopback/absent stay
        loopback-only (no dead remote link)."""
        if status is None:
            return []
        from . import webserver as _ws
        # A status is per COMPONENT, so its web endpoint belongs to exactly one proxied page (a
        # stack may have several): that page's saved policy — never a sibling's — decides "proxied".
        pages = self.stack_web_pages(stack_id) if stack_id else ()
        page = next((p for p in pages if p.component_id == status.component_id), None)
        swc = self.config().stackweb.get(page.page_id) if page is not None else None
        try:
            # ONE /proc read per REQUEST for every component's pins (the Dashboard asks for each
            # running component).
            snap = self._request_memo(("tcp-listeners",),
                                      self._system.procfs.tcp_listeners)
        except Exception:
            snap = []
        out = []
        for obs in status.endpoints:
            sp = obs.spec
            if not getattr(sp, "client", False) or not obs.present:
                continue
            is_web = sp.scheme in ("http", "https")
            port = sp.address.rsplit(":", 1)[-1] if ":" in sp.address else ""
            link = f"{sp.scheme}://{sp.address}" if is_web else ""   # loopback fallback
            label = sp.description or sp.address
            proxied = bool(is_web and swc is not None and swc.enabled)
            # LIVE listener scope for THIS port from /proc/net/tcp (authoritative, per-port). Only a
            # genuinely off-loopback listener earns a request-host link; a loopback/absent one never
            # does (that was the dead-remote-link bug for fixed-loopback services like MeshCom QEMU).
            scope = ""
            if port.isdigit() and not proxied:
                try:
                    scope = _ws.listener_scope(self._system, int(port), snap)
                except Exception:
                    scope = ""
            entry = {"label": label, "address": sp.address, "scheme": sp.scheme or "tcp",
                     "link": link, "port": port, "is_web": is_web,
                     "remote_ok": scope == "exposed",
                     "proxy_scope": "", "proxy_port": 0, "proxy_scheme": "", "proxy_remote": False,
                     "pending": False}
            if proxied:
                # Truthful link: the reachable path is the nginx proxy on swc.port, so key off its
                # EFFECTIVE live scope (/proc/net/tcp), NOT the desired `mode`. Carry the scope so the
                # template links per viewer and NEVER turns a known-absent proxy into an anchor. A
                # stale 0.0.0.0 listener reads "exposed"; a public mode not applied reads "loopback";
                # `pending` marks any such desired-vs-live drift (an Apply reconciles it).
                scope = self._stack_listen_scope(swc)
                entry.update(proxy_scope=scope, proxy_scheme=swc.scheme, proxy_port=swc.port)
                if scope == "exposed":
                    entry["link"] = f"{swc.scheme}://127.0.0.1:{swc.port}/"   # loopback/no-request fallback
                    entry.update(proxy_remote=True, pending=not swc.remote)
                elif scope == "loopback":
                    entry["link"] = f"{swc.scheme}://127.0.0.1:{swc.port}/"   # works for a LOCAL viewer
                    entry["label"] = f"{label} (local only)"
                    entry["pending"] = bool(swc.remote)
                else:                                # absent: proxy enabled but not listening yet
                    entry["link"] = ""               # NO dead URL — the template shows an Apply link
                    entry["label"] = f"{label} (proxy not active — Apply)"
                    entry["pending"] = True
            out.append(entry)
        return out

    def _component_booting(self, comp_id: str) -> bool:
        """True while a detached post-start runner (e.g. MeshCom's `--setcall` retry) is STILL
        applying settings to a just-launched component. Surfaced as a transient 'booting' state
        (yellow, no client link yet) until the callsign lands and the runner finishes — then the
        component reads 'running' (green) with its web-UI link."""
        life = self._lifecycle()
        for rec in life.owned_records(comp_id, role="post"):
            if not life._original_ceased(rec):        # the runner process is still alive
                return True
        return False

    # An outcome value read off disk is interpolated into CLI/web lines — bound its shape.
    _OUTCOME_RE = _re.compile(r"[a-z][a-z-]{0,23}")

    @staticmethod
    def _safe_label(value, fallback: str = "step") -> str:
        """A sidecar label is data from a file, not a trusted string: keep it printable, single
        line and short before it reaches a status line."""
        s = "".join(c for c in str(value or "") if c.isprintable())[:32].strip()
        return s or fallback

    def _render_post_steps(self, steps, log_hint: str, stack_hint: str) -> list[str]:
        """Render typed post-start step records into human status lines. Shared by the detached
        and the synchronous (required) feeds so both report identically.

        NOTE: the `detail` field (a bounded, sanitised excerpt of a child's stderr) is
        DELIBERATELY never rendered — the full text lives in the post-start log, which the
        operator opens on purpose. Nothing here can leak a traceback into the CLI or the web UI."""
        lines: list[str] = []
        for st in steps:
            if not isinstance(st, dict):
                continue
            label = self._safe_label(st.get("label") or st.get("kind"))
            n = st.get("attempts", "?")
            el = st.get("elapsed_s", "?")
            oc = st.get("outcome")
            rc = st.get("rc")
            if oc == "acked":
                lines.append(f"post-start: {label} confirmed on attempt {n} after {el}s")
            elif oc == "probe-matched":
                lines.append(f"post-start: {label} already set "
                             f"(probe matched on attempt {n} after {el}s)")
            elif oc == "sent-unacked":
                lines.append(f"post-start: {label} sent (no acknowledgement) "
                             f"after {n} attempts/{el}s")
            elif oc == "exhausted":
                lines.append(f"post-start: {label} NOT applied — console never became "
                             f"ready within {el}s ({n} attempts); re-apply with: "
                             f"lhpc stack poststart {stack_hint}")
            elif oc == "unverified":
                lines.append(f"post-start: {label} UNVERIFIED — the node answered but never "
                             f"reported the expected value within {el}s ({n} attempts); "
                             f"re-apply with: lhpc stack poststart {stack_hint}")
            elif oc == "ok":
                lines.append(f"post-start: {label} applied after {el}s")
            elif oc == "failed":
                lines.append(f"post-start: {label} FAILED "
                             f"(exit {rc if isinstance(rc, int) else '?'}) after {el}s — see "
                             f"{log_hint}; re-apply with: lhpc stack poststart {stack_hint}")
            elif oc == "timeout":
                lines.append(f"post-start: {label} timed out after {el}s (see {log_hint})")
            elif oc == "ready":
                lines.append(f"post-start: {label} endpoint ready after {el}s")
            elif oc == "main-gone":
                lines.append(f"post-start: {label} aborted — main process was "
                             "replaced/stopped mid-window")
            elif isinstance(oc, str) and self._OUTCOME_RE.fullmatch(oc):
                # A future/unknown outcome must be VISIBLE, never silently dropped.
                lines.append(f"post-start: {label} outcome '{oc}' (see {log_hint})")
        return lines

    def _required_post_outcomes(self, comp_id: str) -> list[str]:
        """Status lines from the SYNCHRONOUS (required) post-start run — e.g. the Meshtastic region
        push, which is required and therefore start-blocking.

        That path writes a sidecar but NO ownership record, so the file is found by recomputing its
        leaf from the CURRENT verified main binding. The binding must be looked up on the band the
        stack is ACTUALLY running: a multi-band stack (meshtastic serves 433 or 868) records its
        ownership band-scoped, so a band-less lookup could never match a real record.

        Three independent checks must all pass before anything is shown, and every one of them
        fails soft (returns no lines, never raises into status):
          * the sidecar's meta must name THIS component and THIS band — never another band's file;
          * the recorded binding must equal the current verified launch — never a stale launch's;
          * the payload must be well-formed.
        """
        life = self._lifecycle()
        stack_id = self.stack_of(comp_id) or comp_id
        # A multi-band stack persists which band it came up on; a single-band/bandless stack has
        # no marker and correctly resolves to "" (unchanged behaviour for those).
        band = self.running_band(stack_id) if self.stack_bands(stack_id) else ""
        binding = life._binding_for(comp_id, band)
        if binding is None:
            return []
        try:
            path = life.paths.under("state", "post", life.required_result_leaf(binding))
            data = json.loads(runtime_fs.read_text(life.paths, Path(path)))
        except (OSError, ValueError, PathContainmentError):
            return []
        meta = data.get("meta") if isinstance(data, dict) else None
        if not isinstance(meta, dict) or meta.get("role") != "required":
            return []
        # The leaf is derived from the launch id alone, so it does not by itself prove WHICH
        # component/band wrote it — check that explicitly rather than trusting the filename.
        if meta.get("comp") != comp_id or (meta.get("band") or "") != (band or ""):
            return []
        rec_binding = data.get("binding")
        if not isinstance(rec_binding, dict) or any(
                rec_binding.get(k) != binding.get(k)
                for k in ("main_launch_id", "main_pid", "main_starttime")):
            return []
        steps = data.get("steps")
        if not isinstance(steps, list):
            return []
        return self._render_post_steps(steps, "the post-start log", stack_id)

    def _post_start_outcomes(self, comp_id: str) -> list[str]:
        """Human status lines for a component's post-start work — the TERMINAL outcome of e.g.
        the MeshCom callsign push ('confirmed on attempt N' / 'NOT applied — console never became
        ready') or the Meshtastic region/identity pushes. Reads BOTH feeds: the uid-scoped sidecar
        each detached runner writes, and the binding-scoped sidecar the synchronous required run
        writes. Fail-soft everywhere: a missing/invalid sidecar degrades to 'outcome unknown',
        never an exception into status. A render-time-skipped step (empty/N0CALL callsign)
        produces NO line — an intentionally-unsent setting is not a failure."""
        life = self._lifecycle()
        lines: list[str] = []
        for rec in life.owned_records(comp_id, role="post"):
            # Only trust a result bound to the CURRENT verified main launch — a record whose
            # main was replaced describes a previous run (belt-and-braces on top of the
            # cancel-time sidecar cleanup).
            binding = life._binding_for(comp_id, rec.get("band", ""))
            if binding is not None and (
                    rec.get("main_pid") != binding.get("main_pid")
                    or rec.get("main_starttime") != binding.get("main_starttime")):
                continue
            log_hint = rec.get("log_path") or "the post-start log"
            if not life._original_ceased(rec):
                lines.append(f"post-start: applying — retry window active (log {log_hint})")
                continue
            rp = rec.get("result_path")
            data = None
            if rp:
                try:
                    data = json.loads(runtime_fs.read_text(life.paths, Path(rp)))
                except (OSError, ValueError, PathContainmentError):
                    data = None
            steps = (data or {}).get("steps")
            if not isinstance(steps, list):
                lines.append(f"post-start: outcome unknown (see {log_hint})")
                continue
            lines.extend(self._render_post_steps(steps, log_hint,
                                                 rec.get("stack", comp_id)))
        lines.extend(self._required_post_outcomes(comp_id))
        return lines

    def radio_overview(self) -> list[dict]:
        """Per-band view for the radio dashboard: daemon/radio config (if running),
        which stack (+ its components) is up on that band, and which stacks can be
        started on it, and the RF logs of the band (`rflogs`). Live RSSI/CAD/feed are polled
        separately via /api/daemon."""
        snap = self.build_snapshot()
        live = {cid: st for ss in snap.stacks for cid, st in ss.components.items()}
        up = (RunState.RUNNING, RunState.DEGRADED)
        dstat = live.get(self.DAEMON_ID)
        daemon_proc = dstat.run_state.value if dstat else "unknown"
        daemon_up = bool(dstat and dstat.run_state in up)
        # "Installed" is not only a SOURCE tree: the daemon can be installed from the binary
        # channel (source-less), and anything RUNNING is installed by definition. Keying this off
        # source_state alone made a binary-installed, running daemon render "Daemon not installed".
        daemon_installed = bool(dstat) and (
            dstat.source_state.value in ("match", "dirty", "differs", "unknown", "not-a-repo")
            or self.binary_covers(self.DAEMON_ID)
            or daemon_up)
        dvs = {b: self.daemon_view(b) for b in self.RADIO_BANDS}
        # LIVERSSI only, and this renders on every dashboard load -> the PASSIVE read. A scan here
        # would destroy an arriving frame just to draw a signal bar.
        dch = {b: (self.daemon_channel(b) if dvs[b].reachable else {}) for b in self.RADIO_BANDS}
        # OCCUPIED = reachable (may physically hold SPI, used for conflict reasoning);
        # USABLE = RADIO=READY (a working radio service). User-facing "served" summaries are
        # USABLE — a FAILED/UNINITIALIZED band is never presented as served.
        occupied_bands = [b for b, v in dvs.items() if v.reachable]
        usable_bands = [b for b, v in dvs.items() if v.ready]
        out = []
        for band in self.active_bands():                 # only the mode's radios get a normal column
            dv = dvs[band]
            other_served = [b for b in usable_bands if b != band]
            running, startable, interactive = [], [], []
            for s in self.stacks():
                sbands = self._declared_bands(s)
                if band not in sbands:
                    continue
                multi = bool(self.stack_bands(s.id))
                running_up = any(c.id in live and live[c.id].run_state in up
                                 for c in s.components if c.band or c.bands)
                # A MULTI-band stack runs on exactly ONE band at a time, so "is a
                # band-carrying component up?" is not enough — that answer is the same
                # in every column and rendered the stack as "Running on 433" while it
                # was actually on 868. Scope it to the band it is really on: the start
                # marker, else the launch band, else its DECLARED primary — never "all
                # bands it could use".
                up_elsewhere = False
                if running_up and multi:
                    running_up = self.runs_on_band(s.id, band)
                    up_elsewhere = not running_up
                comps = []
                for c in s.components:
                    # A running component whose post-start runner is still applying settings reads
                    # 'booting' (no client link yet) until the callsign lands (e.g. MeshCom).
                    booting = (c.id in live and live[c.id].run_state in up
                               and self._component_booting(c.id))
                    comps.append({"id": c.id, "name": c.name, "optional": c.optional,
                          "runnable": c.kind not in (ComponentKind.LIBRARY, ComponentKind.FIRMWARE),
                          "interactive": c.interactive,
                          # Interactive components (GUI/CLI/REPL) are run by the
                          # operator, never by lhpc — show the command to copy.
                          "command": self.manual_start_command(c) if c.interactive else "",
                          "blocker": self.install_blocker(c) if c.interactive else "",
                          # Has tunables -> a config link; lhpc captures a start log
                          # (non-interactive run) or it declares its own -> a log link.
                          "configurable": (c.show_config_link if c.show_config_link is not None
                                           else bool(c.run_params or c.config_file)),
                          "writes_log": (c.show_log_link if c.show_log_link is not None
                                         else bool(c.log_paths)
                                         or bool(c.run_argv and not c.interactive)),
                          "state": ("booting" if booting
                                    else (live[c.id].run_state.value if c.id in live else "unknown")),
                          # No web-UI/client link while booting — it isn't serving yet.
                          "interfaces": ([] if booting
                                         else self._client_interfaces(live.get(c.id), s.id))})
                entry = {"id": s.id, "name": s.name, "main": s.main, "components": comps,
                         "multi_band": multi}
                main_comp = s.component(s.main)
                # The command-bearing component: the interactive MAIN (chat, nomadnet), or
                # — when the main is a non-interactive GUI app that cannot run here — the
                # stack's interactive sidecar (voice's terminal variant on a Lite box), so
                # the "Interactive apps" flow serves it exactly like chat.
                if main_comp is not None and not main_comp.interactive:
                    _side = next((c for c in s.components if c.interactive), None)
                    _main_unusable = (
                        (main_comp.gui_optional and any(
                            getattr(r, "gui", False)
                            for r in self._lifecycle().missing_requirements(main_comp)))
                        or (self.needs_display(main_comp) and not self.display_available()))
                    if _side is not None and _main_unusable:
                        main_comp = _side

                # Interactive stacks (chat/voice): in the dropdown until the operator
                # "runs" them; after that a dismissable command block is shown in the
                # band column they were started on.
                if main_comp is not None and main_comp.interactive:
                    mark_band = self.interactive_band(s.id)        # None if not active
                    # The box stays only while the app is actually up, OR its marker's daemon band is
                    # still USABLE. An interactive app here is daemon-backed (it reaches this code only
                    # via a band component), so it cannot be running once that daemon is stopped —
                    # stopping the daemon therefore closes a lingering-marker box instead of leaving it.
                    # `interactive_band` returns None = NO marker and "" = marked but band-less
                    # (mark_interactive stores cfg_band, which is "" for a stack that is not
                    # band-switchable, e.g. chat). That distinction IS the gate, so the
                    # `is not None` test must stay: collapsing it would pin a permanent command
                    # card on every never-started interactive stack. Only the band-less case
                    # falls back to the stack's own band — the same fallback `col` uses below.
                    marker_live = (mark_band is not None
                                   and (mark_band or min(sbands)) in usable_bands)
                    active = running_up or marker_live
                    if not active:
                        startable.append(entry)                    # in the dropdown
                        continue
                    col = mark_band or (self.running_band(s.id, min(sbands))
                                        if running_up else min(sbands))
                    if col != band:
                        continue                                   # block lives in its own column
                    entry["running"] = running_up
                    entry["command"] = self.manual_start_command(main_comp)
                    entry["blocker"] = self.install_blocker(main_comp)
                    interactive.append(entry)
                    continue

                # A running multi-band stack belongs to the column of the band it
                # was actually started on (tracked at start time).
                if multi and running_up:
                    if self.running_band(s.id, min(sbands)) != band:
                        continue
                    is_up = True
                elif up_elsewhere:
                    continue          # running on its other band -> not startable here
                elif multi:
                    is_up = False     # not running -> startable on every allowed band
                else:
                    is_up = any(c.id in live and live[c.id].run_state in up
                                for c in s.components if c.band == band)
                (running if is_up else startable).append(entry)
            out.append({
                "band": band,
                "stray": False, "shared_with": [],
                "daemon": {
                    "reachable": dv.reachable,     # CONF socket answered (daemon live)
                    # OCCUPIED: reachable — the daemon may physically hold the radio/SPI even
                    # if RADIO != READY (used for resource-conflict reasoning).
                    "occupied": dv.reachable,
                    "ready": dv.ready,             # ...AND RADIO=READY (serves a usable band)
                    # USABLE: only a READY radio is a usable service for dependents/TX.
                    "usable": dv.ready,
                    "radio_state": dv.radio_state or None,   # READY/FAILED/UNINITIALIZED
                    # A truthful one-word state for the UI: offline / occupied / usable.
                    "state_label": ("usable" if dv.ready else
                                    "occupied" if dv.reachable else "offline"),
                    "process": daemon_proc,        # process run-state (band-independent)
                    "process_up": daemon_up,
                    "installed": daemon_installed,
                    "other_served": other_served,  # other USABLE band(s) (RADIO=READY)
                    "served": usable_bands,        # usable bands (READY) — never FAILED/UNINIT
                    "occupied_bands": occupied_bands,   # reachable (may hold SPI) — conflicts
                    "usable_bands": usable_bands,       # RADIO=READY — dependent-start/TX
                    "radio": dv.status.get("RADIO") if dv.reachable else None,
                    "txmode": dv.status.get("TXMODE") if dv.reachable else None,
                    "cadrssi": dv.status.get("CADRSSI") if dv.reachable else None,
                    "cadwait": dv.status.get("CADWAIT") if dv.reachable else None,
                    "liverssi": dch[band].get("LIVERSSI") if dv.reachable else None,
                    # daemon 1.2.0 witnesses: the RUNNING permission and family, off the
                    # daemon's own STATUS — the SX127x +20 dBm banner keys on these, never on
                    # the saved switch (a stray band after a Hardware change renders it too).
                    "high_power": self.high_power_state(band, dv),
                },
                "running": running,
                "startable": startable,
                "interactive": interactive,
                "rflogs": self._rflogs_for_band(band, running),
                # Two+ stacks sharing one radio (e.g. a manually-started chat plus a
                # running chat) fight over the daemon's tuning — flag it red. Stacks that
                # reach RF THROUGH another one are filtered out first: they are its client,
                # not its rival (see _radio_competitors).
                "conflict": self._radio_competitors(
                    running + [e for e in interactive if e.get("running")]),
            })
        # Strays (§6): a daemon still live on a band the current mode EXCLUDES — the likely leftover
        # the day after a mode switch. Surface it so it stays visible + stoppable (lhpc never auto-
        # kills). M-2: if it is the SAME process as an active band's daemon (a dual-band PID),
        # disclose that stopping it stops the active band too.
        active = list(self.active_bands())
        active_pids = {b: set(self._daemon_pids_for_band(b)) for b in active}
        for band in self.RADIO_BANDS:
            if band in active:
                continue
            dv = dvs[band]
            if not dv.reachable:
                continue                                   # nothing live on this excluded band
            shared_with = sorted(b for b, pids in active_pids.items()
                                 if pids & set(self._daemon_pids_for_band(band)))
            out.append({
                "band": band, "stray": True, "shared_with": shared_with,
                "daemon": {
                    "reachable": dv.reachable, "occupied": dv.reachable, "ready": dv.ready,
                    "usable": dv.ready, "radio_state": dv.radio_state or None,
                    "state_label": ("usable" if dv.ready else
                                    "occupied" if dv.reachable else "offline"),
                    "process": daemon_proc, "process_up": daemon_up, "installed": daemon_installed,
                    "other_served": [b for b in usable_bands if b != band], "served": usable_bands,
                    "occupied_bands": occupied_bands, "usable_bands": usable_bands,
                    "radio": dv.status.get("RADIO") if dv.reachable else None,
                    "txmode": dv.status.get("TXMODE") if dv.reachable else None,
                    "cadrssi": dv.status.get("CADRSSI") if dv.reachable else None,
                    "cadwait": dv.status.get("CADWAIT") if dv.reachable else None,
                    "liverssi": dch[band].get("LIVERSSI") if dv.reachable else None,
                    # daemon 1.2.0 witnesses: the RUNNING permission and family, off the
                    # daemon's own STATUS — the SX127x +20 dBm banner keys on these, never on
                    # the saved switch (a stray band after a Hardware change renders it too).
                    "high_power": self.high_power_state(band, dv),
                },
                "running": [], "startable": [], "interactive": [], "conflict": [],
                "rflogs": self._rflogs_for_band(band, []),
            })
        return out

    @staticmethod
    def _declared_bands(s) -> set:
        """Bands a stack's components declare (`bands`, else `band`) — the dashboard's per-band
        membership and the RF-log page's stack row read the same set."""
        out = set()
        for c in s.components:
            out |= set(c.bands) if c.bands else ({c.band} if c.band else set())
        return out

    def start_notes(self, result: ActionResult) -> list[str]:
        """Per-component `start_note` strings for components that actually started
        (verified / already-healthy) in this result — e.g. how to connect a just-
        launched GUI to its node — and the shared-SPI warning of a start that came up
        beside the other side. Shown as a transient dashboard note."""
        started = {r.component for r in result.results
                   if getattr(r, "outcome", None) is not None
                   and r.outcome.value in ("verified", "started", "already_healthy")}
        out = []
        for s in self.stacks():
            for c in s.components:
                if c.id in started and c.start_note:
                    out.append(c.start_note)
        if (result.data or {}).get("spi_warning"):
            out.append(result.data["spi_warning"])
        out += list((result.data or {}).get("openhop_warnings") or ())
        return out

    @invalidates_snapshot
    def run_action(self, op: str, target: str, apply: bool = False, source: str = "pinned",
                   stop_owners: bool = False, cascade: bool = False,
                   band: str = "", purge: bool = False) -> ActionResult:
        """Dispatch a named action to its service method (plan when apply=False)."""
        # An invalid source selector is a typed failure — NEVER silently rewritten to 'dev'.
        if op in ("install", "update") and source not in self.CHANNEL_CHOICES:
            return ActionResult(False, f"Invalid source '{source}' (choose "
                                f"{', '.join(self.SOURCE_CHOICES)}).",
                                next_commands=[f"lhpc {op} {target} --source pinned"])
        ops = {
            "install": lambda: self.install(target, apply=apply, source=source),
            "update": lambda: self.update(target, apply=apply, source=source,
                                          build=False),     # the console builds it detached
            "uninstall": lambda: self.uninstall(target, apply=apply),
            "start": lambda: self.start(target, apply=apply, stop_owners=stop_owners, band=band),
            "stop": lambda: self.stop(target, apply=apply, cascade=cascade, band=band),
            "restart": lambda: self.restart(target, apply=apply, stop_owners=stop_owners,
                                            band=band, cascade=cascade),
            "poststart": lambda: self.poststart(target, apply=apply, band=band),
            "build": lambda: self.build(target, apply=apply),
            "test": lambda: self.test(target, apply=apply),
            "test-tx": lambda: self.test(target, tx=True, apply=apply),
            "clean": lambda: self.clean(target, apply=apply, purge=purge),
        }
        fn = ops.get(op)
        if fn is None:
            return ActionResult(False, f"Unknown action '{op}'.",
                                next_commands=["lhpc help"])
        # Lifecycle coordination now lives in the PUBLIC start/stop/restart methods (the
        # authoritative locked entry points), so a DIRECT service call is guarded
        # identically to a CLI/web call. install/build/update/uninstall lock internally on
        # their source paths. run_action simply dispatches.
        return fn()

    def stack_of(self, target: str) -> str | None:
        """The stack id that owns `target` (a stack id or a component id)."""
        for s in self.stacks():
            if s.id == target or s.component(target) is not None:
                return s.id
        return None

    # ---- daemon monitoring + live settings -------------------------------

    def daemon_view(self, band: str) -> daemon_control.DaemonView:
        """Read-only STATUS/STATS for a band — passive, never touches the radio.

        `.channel` is empty by design; ask for channel data explicitly (see below)."""
        return daemon_control.read_view(self._system, band)

    def daemon_channel(self, band: str) -> dict[str, str]:
        """CHANNEL fields WITHOUT a CAD scan — safe to call on a timer."""
        return daemon_control.read_channel_passive(self._system, band)

    def daemon_channel_scan(self, band: str) -> dict[str, str]:
        """CHANNEL fields WITH a real CAD scan — DESTRUCTIVE to a frame in flight.

        Operator-invoked only (`lhpc daemon <band>`, the console's "Scan now"). Never on a timer."""
        return daemon_control.read_channel_scan(self._system, band)

    def daemon_socket_line(self, band: str) -> str:
        """One raw, bounded, sanitised CONF-socket status line for the live 'View Socket' monitor
        ('' when the band is invalid or the socket is unreachable). Read-only, fail-closed."""
        return daemon_control.read_socket_line(self._system, band)

