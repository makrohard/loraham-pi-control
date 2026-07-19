"""start/stop/restart/build/test orchestration, job/log management, and dashboard/status views.

Mixin of ControllerService (state/constants on the facade). Adapters import lhpc.core.services only."""
from __future__ import annotations

import time
from pathlib import Path

from .snapshot_memo import invalidates_snapshot
from . import daemon_control
from . import procident
from . import resources as resources_mod
from . import runtime_fs
from . import validators
from .config import load_stack_config
from .model import ComponentKind, ResourceMode, RunState
from .outcomes import CompResult, Outcome, applied_ok
from .paths import PathContainmentError
from .service_base import ActionResult, AdmissionRefused, SourceTxnBlocked

import re as _re
# A HMAC-apply build-log base is a strict controller-generated prefix bound to the FULL 32-hex run id;
# validated in build() BEFORE any path is constructed (marker-time validation alone is too late).
_HMAC_LOG_BASE_RE = _re.compile(r"^hmac-apply-[0-9a-f]{32}$")


class LifecycleOpsMixin:

    def _band_limited_running(self, snap):
        """(limited_fn, running_components, running_ids) with each running component's
        `loraham.radio.<band>` claims restricted to the band(s) it ACTUALLY uses — the daemon to
        the bands it currently serves, a band-switchable app to its effective band, a fixed-band
        app to its band. So a daemon on 433 and meshtastic on 868 are NOT seen as one radio."""
        import dataclasses
        # Daemon radio ownership is PROCESS topology (a dead CONF socket does not free the radio),
        # not socket reachability.
        served = self._daemon_claimed_bands()

        def limited(c, eff_bands):
            if c.id != self.DAEMON_ID and not c.bands:
                return c                                  # single-fixed-band: unchanged
            # Keep only radio claims for the band(s) this component actually uses. When the band is
            # unknown (empty eff_bands), STRIP every radio claim — a band-switchable app must never
            # claim BOTH radios just because its running-band marker is missing.
            keep = [r for r in c.resources
                    if not (r.key.startswith("loraham.radio.")
                            and r.key.rsplit(".", 1)[-1] not in eff_bands)]
            return dataclasses.replace(c, resources=tuple(keep))

        running, running_ids = [], set()
        for ss in snap.stacks:
            for c in ss.stack.components:
                if ss.components[c.id].run_state not in (RunState.RUNNING, RunState.DEGRADED):
                    continue
                if c.id == self.DAEMON_ID:
                    eff = served
                elif c.bands:
                    # Actual running band, else the component's DECLARED band — never "both".
                    eb = self._effective_band(ss.stack.id, c.band)
                    eff = {eb} if eb in ("433", "868") else set()
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
        limited, running, running_ids = self._band_limited_running(snap)
        target = limited(comp, {band} if band else set())
        conflicts = resources_mod.interpret_conflicts(running + [target], running_ids | {comp.id})
        return [c.message for c in conflicts if comp.id in c.holders and c.observed]

    def _ready_endpoints_present(self, comp) -> tuple[bool, list[str]]:
        """Probe a component's `ready = true` endpoints (bounded). Returns
        (all_present, evidence-lines). Only endpoints explicitly marked ready
        participate — reference/client/data endpoints never gate."""
        from .probes.endpoints import tcp_endpoint_present
        from .probes.unixsock import probe_socket
        ready = [e for e in comp.endpoints if e.ready]
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
        claims: dict[str, str] = {}
        for _, c in order:
            for r in c.resources:
                if (r.mode in (ResourceMode.EXCLUSIVE, ResourceMode.PROVIDER)
                        and not r.key.startswith("loraham.radio.")):
                    claims[r.key] = c.id
        for b in needed_bands:
            claims.setdefault(f"loraham.radio.{b}", target)

        snap = self.build_snapshot()
        # Daemon radio ownership is PROCESS topology (a dead CONF socket does not free the radio),
        # not socket reachability.
        served = self._daemon_claimed_bands()
        blockers, seen = [], set()

        def add(stack_id, holder, resource):
            key = (stack_id, resource)
            if key not in seen:
                seen.add(key)
                blockers.append({"resource": resource, "holder": holder,
                                 "holder_stack": stack_id})

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
                    eb = self._effective_band(sid, c.band)   # actual band, else declared (never both)
                    active = {eb} if eb in ("433", "868") else set()
                else:
                    active = {c.band} if c.band else set()
                for r in c.resources:
                    if r.mode not in (ResourceMode.EXCLUSIVE, ResourceMode.PROVIDER):
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
                comp_band = self._effective_band(sid, c.band) if multi else c.band
                if (not target_is_daemon and sid != target_stack
                        and sid != self.stack_of(self.DAEMON_ID)
                        and comp_band and comp_band in needed_bands):
                    add(sid, c.id, f"radio {comp_band} MHz")
        return blockers

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

    def probe_hardware(self, band: str, hw_preset: str):
        """Bounded, safe hardware probe for the daemon Hardware page: spawn the daemon for
        (band, hw_preset) and report present / absent (+ the daemon's chip diagnostic) / busy. Guards:
        valid band, known preset, daemon BUILT. Reuses the real runtime lock dir so an already-running
        daemon on the band reports BUSY, never gets its radio stolen."""
        from . import config as _config
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
        return hwprobe.probe_radio(self._system, binary, str(src), band, hw_preset,
                                   runtime_dir=runtime_dir, label=_config.hw_preset_label(hw_preset))

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

    @invalidates_snapshot
    def start(self, target: str, apply: bool = False, params: dict | None = None,
              stop_owners: bool = False, band: str = "",
              daemon_overrides: dict | None = None,
              file_overrides: dict | None = None, auto_install_ctx=None) -> ActionResult:
        """Public, LOCKED entry — acquires the full lifecycle lock bundle (incl. owners
        when stop_owners) so a DIRECT call gets the same coordination as CLI/web.
        `daemon_overrides`/`file_overrides` are ephemeral per-start values (this launch only, never
        persisted); None = apply the saved config, as the CLI does."""
        if (_r := self._controller_refusal(target)) is not None:
            return _r
        from . import reslock
        if auto_install_ctx is not None:
            order = self._run_order(target) or []
            ctx_err = self._auto_install_ctx_error(
                auto_install_ctx, {c.source.path for _, c in order if c.source})
            if ctx_err:
                return ActionResult(False, f"Refusing to start '{target}': {ctx_err}")
        if not apply:
            return self._start_impl(target, apply=False, params=params,
                                    stop_owners=stop_owners, band=band,
                                    daemon_overrides=daemon_overrides,
                                    file_overrides=file_overrides)
        # Validate + canonicalize ordinary run params + file overrides BEFORE any lock — including
        # the config-stability guard. This is config-INDEPENDENT (it validates against the manifest,
        # not stored config), so an unqualified duplicate name, a non-mapping payload, or an unknown/
        # invalid value fails TYPED here — before config-stability/lifecycle locks, daemon work, owner
        # stops, config writes, spawn, or post-start. The canonical values feed lock planning and
        # `_start_impl` (which re-validates as a defensive boundary for internal/direct callers).
        params, pv_err = self._normalize_run_params(target, params)
        if pv_err:
            return ActionResult(False, f"Cannot start '{target}': invalid parameter — {pv_err}",
                                next_commands=[f"lhpc status {target}"])
        file_overrides, fo_err = self._normalize_file_overrides(target, file_overrides)
        if fo_err:
            return ActionResult(False, f"Cannot start '{target}': invalid parameter — {fo_err}",
                                next_commands=[f"lhpc status {target}"])
        # LOCK ORDER #1: task admission is acquired OUTSIDE the config-stability guard and BEFORE any
        # mutation (incl. clear_daemon_feed) — a start refused by a pending self-update/uninstall
        # touches nothing. The inner _lifecycle_guard reuses this admission reentrantly.
        try:
            with self._admission_guard("start", target), self._config_stable():
                # The daemon's REQUESTED radio mode determines which bands the lock bundle covers.
                _order = self._run_order(target)
                _radio = ""
                if _order:
                    _r, _ = self._daemon_needs(_order, params, self._config_band(target, band))
                    _radio = _r or ""
                # Clear the RX/TX window at the start boundary (BEFORE any spawn): record the pre-start
                # floor so the feed shows only THIS run's activity on a daemon (re)start, and clears
                # stale activity whenever ANY stack is started while the daemon is already running.
                for _b in self.active_bands():
                    self.clear_daemon_feed(_b)
                try:
                    with self._lifecycle_guard("start", target, band,
                                               stop_owners=stop_owners, radio=_radio):
                        return self._start_impl(target, apply=True, params=params,
                                                stop_owners=stop_owners, band=band,
                                                daemon_overrides=daemon_overrides,
                                                file_overrides=file_overrides)
                except SourceTxnBlocked as blocked:
                    return ActionResult(False, f"Cannot start '{target}': {blocked}",
                                        next_commands=[f"lhpc status {target}"])
                except reslock.ResourceBusy as busy:
                    return ActionResult(False, f"Cannot start '{target}': {busy}",
                                        next_commands=[f"lhpc status {target}"])
        except AdmissionRefused as _adm:
            return ActionResult(False, _adm.reason, data={'admission_blocked': _adm.tag})
        except reslock.ResourceBusy as busy:
            return ActionResult(False, f"Cannot start '{target}': {busy}",
                                next_commands=[f"lhpc status {target}"])
        except SourceTxnBlocked as blocked:
            # The config-stability guard itself was busy (e.g. a auto-install auto-install run holds config
            # EXCLUSIVE for its whole lifetime) — refuse typed rather than hang or crash.
            return ActionResult(False, f"Cannot start '{target}': {blocked}",
                                next_commands=[f"lhpc status {target}"])
        except (OSError, PathContainmentError) as exc:
            return ActionResult(False, f"Cannot start '{target}': configuration guard unavailable "
                                f"({exc})", next_commands=[f"lhpc status {target}"])

    def _start_impl(self, target: str, apply: bool = False, params: dict | None = None,
                    stop_owners: bool = False, band: str = "",
                    daemon_overrides: dict | None = None,
                    file_overrides: dict | None = None) -> ActionResult:
        """Applied starts run under the configuration-stability guard so saved config is a stable
        snapshot from the first read through generation/launch/post-start — a direct/internal
        apply=True call cannot bypass it. Dry-run holds no long-lived guard. A guard/read failure is
        a TYPED failure returned BEFORE any lifecycle side effect."""
        if not apply:
            return self._start_impl_inner(target, apply=False, params=params,
                                          stop_owners=stop_owners, band=band,
                                          daemon_overrides=daemon_overrides,
                                          file_overrides=file_overrides)
        try:
            with self._config_stable():                          # re-entrant (no-op if start() holds it)
                return self._start_impl_inner(target, apply=True, params=params,
                                              stop_owners=stop_owners, band=band,
                                              daemon_overrides=daemon_overrides,
                                              file_overrides=file_overrides)
        except AdmissionRefused as _adm:
            return ActionResult(False, _adm.reason, data={'admission_blocked': _adm.tag})
        except SourceTxnBlocked as blocked:
            # The config-stability guard itself was busy (e.g. a auto-install auto-install run holds config
            # EXCLUSIVE for its whole lifetime) — refuse typed rather than hang or crash.
            return ActionResult(False, f"Cannot start '{target}': {blocked}",
                                next_commands=[f"lhpc status {target}"])
        except (OSError, PathContainmentError) as exc:
            return ActionResult(False, f"Cannot start '{target}': configuration guard unavailable "
                                f"({exc})", next_commands=[f"lhpc status {target}"])

    def _start_impl_inner(self, target: str, apply: bool = False, params: dict | None = None,
                          stop_owners: bool = False, band: str = "",
                          daemon_overrides: dict | None = None,
                          file_overrides: dict | None = None) -> ActionResult:
        order = self._run_order(target)
        if order is None:
            return ActionResult(False, f"Unknown stack or component '{target}'.",
                                next_commands=["lhpc list"])
        if not self._paths.runtime_root_exists:
            return ActionResult(False, "Runtime root not bootstrapped.",
                                next_commands=["lhpc bootstrap"])
        # Hardware not configured: a fresh box has no radio board selected, so the daemon (and any
        # radio stack) must refuse to start until the operator picks one. Checked BEFORE radio-mode.
        hw_block = self.hardware_block(target)
        if hw_block:
            return ActionResult(False, f"Cannot start '{target}': {hw_block}",
                                next_commands=["lhpc hardware"])
        # Radio-mode availability: a stack whose every band is excluded by the current mode cannot
        # run (remedy = change the mode, not stop a holder). Refused for both dry-run and apply.
        rm_block = self.radio_mode_block(target)
        if rm_block:
            return ActionResult(False, f"Cannot start '{target}': {rm_block}",
                                next_commands=["lhpc hardware"])
        # THE authoritative validation of ordinary ephemeral run params — BEFORE daemon-band
        # calculation, lifecycle-lock selection, conflict/owner handling, any daemon launch, CONF
        # change, config generation, client launch or post-start. Scoped to the target (a stack's
        # exposed params, or a direct component's own). An invalid/unknown override is a typed
        # failure; a dry-run plan surfaces it too, but apply fails before ANY lifecycle side effect.
        params, pv_err = self._normalize_run_params(target, params)
        if pv_err:
            return ActionResult(False, f"Cannot start '{target}': invalid parameter — {pv_err}",
                                next_commands=[f"lhpc status {target}"])
        # Band-switchable stack: resolve the chosen band (default = first allowed).
        cfg_band = self._config_band(target, band)
        life = self._lifecycle()
        radio, tx = self._daemon_needs(order, params, cfg_band)
        # The stack whose daemon params to apply once the daemon is up (a direct component target
        # resolves to its owning stack).
        start_sid = self._owner_stack_id(target)
        # THE authoritative boundary for ephemeral Start-confirm overrides: validate + canonicalise
        # per band BEFORE any daemon launch, CONF mutation or client launch. An invalid override is
        # a typed failure (never silently discarded in favour of a saved/default value).
        launch_bands = self._daemon_serve_bands(radio) if radio or self._is_daemon_target(target) else []
        daemon_overrides, ov_err = self._normalize_ephemeral_overrides(
            start_sid, launch_bands, daemon_overrides)
        if ov_err:
            return ActionResult(False, f"Cannot start '{target}': invalid daemon parameter — {ov_err}",
                                next_commands=[f"lhpc status {target}"])
        # Ephemeral file-config overrides (Start-confirm 'Stack parameters'): validated here, then
        # applied for THIS launch only when the config file is (re)generated. Invalid = typed fail.
        file_over, fo_err = self._normalize_file_overrides(target, file_overrides)
        if fo_err:
            return ActionResult(False, f"Cannot start '{target}': invalid parameter — {fo_err}",
                                next_commands=[f"lhpc status {target}"])
        # CALL/node enforcement (authoritative backstop; the web also guards for UX): a licensed
        # stack refuses an empty/N0CALL callsign, an unlicensed stack refuses an empty node name.
        # Only the actual APPLY is blocked — the dry-run PLAN still renders so the confirm page can
        # show the 'Stack parameters' panel where the operator supplies the call/node.
        if apply:
            id_ok, id_field, id_msg = self.enforce_identity(target, band, params, file_over)
            if not id_ok:
                return ActionResult(False, f"Cannot start '{target}': {id_msg}",
                                    data={"enforce_field": id_field},
                                    next_commands=[self._identity_config_hint(target)])
        if not apply:
            details = []
            commands = []   # copyable commands the operator must run themselves
            for _, comp in order:
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
                else:
                    details.append(f"  [start] {comp.id} (band {cfg_band or comp.band or '-'})")
            blockers = self.run_blockers(target, band, radio)
            for bl in blockers:
                details.append(f"  [conflict] {bl['resource']} is held by running stack "
                               f"'{bl['holder_stack']}' ({bl['holder']})")
            return ActionResult(True, f"Run plan for '{target}': {len(order)} component(s) in order.",
                                details=details,
                                next_commands=[f"lhpc stack start {target} --yes"],
                                data={"changes": len(order), "blockers": blockers,
                                      "commands": commands})

        # No-side-effect Start FIRST — BEFORE any owner handling: if EVERY requested component is
        # already healthy, return ALREADY_HEALTHY immediately. Never run blockers for mutation,
        # never stop owners (even with stop_owners=True), never launch/write config/apply params/
        # CONF SET/touch markers for an already-healthy target.
        if apply:
            _hsnap = self.build_snapshot()
            _hidx = {c.id: ss.components[c.id] for ss in _hsnap.stacks for c in ss.stack.components}
            if self._all_components_healthy(order, _hidx, radio):
                _hres = [CompResult(component=comp.id, stack=stack.id, action="start",
                             outcome=Outcome.ALREADY_HEALTHY, summary="already running")
                         for stack, comp in order
                         if comp.kind not in (ComponentKind.LIBRARY, ComponentKind.FIRMWARE)]
                return ActionResult(True, f"'{target}' already healthy — nothing to start.",
                    details=[f"  [already_healthy] {r.component}: already running" for r in _hres],
                    results=tuple(_hres), next_commands=[f"lhpc status {target}"])

        # A component about to start must never SILENTLY inherit an AMBIGUOUS flat legacy value (a
        # run/file param name declared by >= 2 owner-stack components, with a flat value present and
        # no component-scoped value). Fail TYPED here — BEFORE any owner stop, daemon launch, daemon
        # mutation, config-file write, process spawn or post-start scheduling.
        _amb = self._config_ambiguity(target, order, band)
        if _amb is not None:
            return ActionResult(False, f"Cannot start '{target}': {_amb}",
                                next_commands=[f"lhpc config {target}"])

        # Ownership check: if a needed resource is held by another running stack,
        # either stop that stack first (stop_owners) or refuse and report it.
        blockers = self.run_blockers(target, band, radio)
        if blockers:
            owners = sorted({bl["holder_stack"] for bl in blockers})
            if not stop_owners:
                details = [f"  {bl['resource']} held by running stack '{bl['holder_stack']}'"
                           for bl in blockers]
                return ActionResult(
                    False,
                    f"Cannot run '{target}': {', '.join(owners)} must be stopped first.",
                    details=details,
                    next_commands=[f"lhpc stack stop {o}" for o in owners])
            prelude = []
            unstopped = []
            for o in owners:
                ores = self.stop(o, apply=True)
                if ores.ok:
                    prelude.append(f"  [stopped] conflicting stack '{o}'")
                else:
                    unstopped.append(o)
                    prelude.append(f"  [blocked] conflicting stack '{o}' did not stop "
                                   f"(verified): {ores.summary}")
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

        snap = self.build_snapshot()
        st_index = {c.id: ss.components[c.id]
                    for ss in snap.stacks for c in ss.stack.components}
        out = list(prelude)
        results: list[CompResult] = []   # TYPED per-component outcomes (source of truth)
        daemon_ok = True                # gate dependents on verified daemon readiness

        def record(comp, stack, outcome, summary):
            results.append(CompResult(component=comp.id, stack=stack.id, action="start",
                                      outcome=outcome, summary=summary))
            out.append(f"  [{outcome.value}] {comp.id}: {summary}")

        # Config generation + launch config are COMPONENT-scoped so a direct component start never
        # writes a sibling's config nor leaks the target's ephemeral overrides / run params into a
        # dependency: the explicit target's overrides apply ONLY to the target (or every component
        # of a stack target); each dependency uses its OWN saved/default values.
        _target_is_stack = self.stack(target) is not None
        def _comp_overrides(comp_id):
            if not (_target_is_stack or comp_id == target):
                return None
            return self._overrides_for_comp(target, "file", file_over, comp_id)

        for stack, comp in order:
            state = st_index[comp.id].run_state
            running = state == RunState.RUNNING        # DEGRADED is NOT healthy
            # A DEGRADED component (process up but a ready endpoint missing) must not
            # be treated as healthy and must not trigger a duplicate launch.
            if state == RunState.DEGRADED and comp.id != self.DAEMON_ID:
                record(comp, stack, Outcome.BLOCKED, "running but DEGRADED (a ready "
                       "endpoint is missing) — stop it (verified) and re-run")
                continue
            if comp.id == self.DAEMON_ID:
                dlines, dok = self._ensure_daemon(life, stack, comp, running, radio, params,
                                                  start_sid, daemon_overrides)
                out.extend(dlines)
                results.append(CompResult(component=comp.id, stack=stack.id, action="start",
                    outcome=(Outcome.VERIFIED if dok else Outcome.FAILED),
                    summary="daemon ready" if dok else "daemon readiness/TX gating failed"))
                daemon_ok = dok
                continue
            # A dependent must NOT start when the daemon it needs failed readiness.
            if not daemon_ok and self.DAEMON_ID in comp.depends_on:
                record(comp, stack, Outcome.BLOCKED, "daemon not ready — not started")
                continue
            if running:
                record(comp, stack, Outcome.ALREADY_HEALTHY, "already running")
                continue
            if comp.source and not life.source_dir(comp).exists():
                record(comp, stack, Outcome.BLOCKED, f"not installed (lhpc install {stack.id})")
                continue
            if comp.interactive:
                # Never auto-start an interactive TUI — the operator runs it in a terminal.
                # But its required runtime config must be generated FIRST: if generation
                # fails (failed/no-base/unsafe source) or the source is a read-only linked
                # tree, DO NOT write the interactive marker and DO NOT present a manual
                # command as ready-to-run — return a typed block/manual-required instead.
                if comp.config_file:
                    cw = self.write_config_files(comp.id, cfg_band, _comp_overrides(comp.id))
                    mine = [w for w in cw if w.component == comp.id]
                    bad = next((w for w in mine if w.status in ("failed", "no-base")), None)
                    if bad:
                        record(comp, stack, Outcome.BLOCKED,
                               f"interactive start blocked — required config could not be "
                               f"generated ({bad.path}: {bad.detail})")
                        continue
                    linked = next((w for w in mine if w.status == "linked-readonly"), None)
                    if linked:
                        record(comp, stack, Outcome.MANUAL_REQUIRED,
                               f"linked source is read-only — generate {comp.id}'s config in "
                               f"your own checkout before starting it ({linked.path})")
                        continue
                marked = self.mark_interactive(stack.id, cfg_band)
                blocker = self.install_blocker(comp)
                marker_note = ("" if marked else
                               " (note: interactive marker could not be persisted — the "
                               "dashboard may not show its command block)")
                if blocker:
                    record(comp, stack, Outcome.BLOCKED, f"interactive but {blocker}")
                else:
                    # The start COMMAND is shown on the app's dashboard card (and the
                    # interactive marker drives that) — don't duplicate it here.
                    record(comp, stack, Outcome.MANUAL_REQUIRED,
                           f"interactive — start it from its card on the dashboard{marker_note}")
                continue
            if comp.units and not comp.run_argv:
                # Externally supervised (systemd, root) — lhpc observes, never starts.
                record(comp, stack, Outcome.MANUAL_REQUIRED,
                       f"system service — start with: sudo systemctl start {comp.units[0].name}")
                continue
            miss = life.missing_requirements(comp)
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
                       f"not built — build it first (lhpc build {stack.id})")
                continue
            # Regenerate any config file this component reads (per the chosen band). A
            # generation FAILURE for this component blocks the launch — never start with
            # stale or absent configuration.
            if comp.config_file:
                cw = self.write_config_files(comp.id, cfg_band, _comp_overrides(comp.id))
                mine = [w for w in cw if w.component == comp.id]
                bad = next((w for w in mine if w.status in ("failed", "no-base")), None)
                if bad:
                    record(comp, stack, Outcome.BLOCKED,
                           f"config generation failed ({bad.path}: {bad.detail})")
                    continue
                # A linked external source is read-only to lhpc: it cannot generate the
                # required config, so the operator must provide it in their own checkout.
                # This is MANUAL_REQUIRED, never a silent start with absent config.
                linked = next((w for w in mine if w.status == "linked-readonly"), None)
                if linked:
                    record(comp, stack, Outcome.MANUAL_REQUIRED,
                           f"linked source is read-only — generate {comp.id}'s config in "
                           f"your own checkout ({linked.path})")
                    continue
            # COMPONENT-scoped launch config (this component's OWN run params from the owner-stack
            # store) so a stored sibling run parameter can never leak into another component's argv
            # through a name collision. Ephemeral confirm-page params (this start only) override it
            # for BOTH the launch and post-start — but only for the explicit target (or every
            # component of a stack target), never leaking the target's values into a dependency.
            comp_cfg = dict(self.stack_config(comp.id, cfg_band))
            if _target_is_stack or comp.id == target:
                comp_cfg.update(self._overrides_for_comp(target, "run", params, comp.id))
            res = life.start(stack, comp, comp_cfg, band=cfg_band)
            if not res.ok:
                # A launch that couldn't be owned AND couldn't be proven ceased is a
                # typed UNVERIFIED (residual process), not a clean FAILED.
                record(comp, stack,
                       Outcome.UNVERIFIED if res.unverified else Outcome.FAILED,
                       f"start failed: {res.detail} (log {res.log_path})")
                continue
            # readiness="endpoint": VERIFIED only once every ready=true endpoint is up;
            # otherwise SIGTERM the just-launched owned session (verified cleanup) and
            # report UNVERIFIED — no post-start work runs.
            if comp.readiness == "endpoint":
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
            required_ok, post_summary = self._run_post_start(life, stack, comp, comp_cfg, cfg_band)
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
            if cfg_band and self.stack_bands(stack.id):
                band_ok = self._set_running_band(stack.id, cfg_band)
            if band_ok:
                record(comp, stack, Outcome.VERIFIED, summary)
            else:
                record(comp, stack, Outcome.UNVERIFIED,
                       summary + "; running-band marker could not be persisted — "
                       "operational state may be inconsistent")
        self.clear_stale_interactive(keep=self.stack_of(target) or target)
        # ok derives ENTIRELY from typed outcomes. A MANUAL_REQUIRED for an OPTIONAL
        # component does not block; every other non-success outcome does.
        optional_ids = {c.id for _, c in order if c.optional}
        def blocks(r):
            if r.outcome == Outcome.MANUAL_REQUIRED and r.component in optional_ids:
                return False
            return not r.ok
        blocking = [r for r in results if blocks(r)]
        required_manual = [r.component for r in blocking if r.outcome == Outcome.MANUAL_REQUIRED]
        failed = [r.component for r in blocking if r.outcome != Outcome.MANUAL_REQUIRED]
        ok = not blocking
        if failed:
            summary = f"Run FAILED for '{target}': {', '.join(failed)} did not start/verify."
        elif required_manual:
            summary = (f"Run for '{target}': manual start required for "
                       f"{', '.join(required_manual)} — see the dashboard.")
        else:
            summary = f"Run applied for '{target}'."
        # HEALTHY STACK START: persist the last-start CANDIDATE composition (durable, written
        # here in the mutation path so GET pages never need git). It is NOT a known-working
        # record — the operator confirms it explicitly ("Confirm this stack as working").
        # A successful start also satisfies any restart-required flag: the processes now
        # run the saved config.
        if ok and self.stack(target) is not None:
            self._capture_start_composition(target, cfg_band)
            self._clear_restart_required(target)
        return ActionResult(ok, summary, details=out, results=tuple(results),
                            next_commands=[f"lhpc status {target}", f"lhpc logs {target}",
                                           f"lhpc stack stop {target}"])

    def _run_post_start(self, life, stack, comp, comp_cfg, band) -> tuple[bool | None, str]:
        """Run post-start steps. Returns (required_ok, summary):
          * (None, "")               — no post-start;
          * (None, "…scheduled")     — OPTIONAL steps scheduled detached (never gates);
          * (True,  "…completed")    — REQUIRED steps ran synchronously and PASSED;
          * (False, "…failed (rc N)")— REQUIRED steps FAILED → caller blocks VERIFIED
                                        and invokes verified cleanup.
        `required_ok is False` is the only blocking case (an explicit bool, not an
        enum attribute)."""
        if not comp.post_steps:
            return None, ""
        if life.has_required_post_start(comp):
            jr = life.run_required_post_start(stack, comp, comp_cfg, band=band)
            if jr.ok:
                return True, "required post-start completed"
            return False, f"required post-start failed (rc {jr.returncode})"
        # OPTIONAL: scheduling never gates the start, but its typed result makes any
        # scheduling failure VISIBLE in the details (it is no longer swallowed by
        # `spawn_post_start`, so no blanket catch is needed here).
        sched = life.spawn_post_start(stack, comp, comp_cfg, band=band)
        if sched.ok:
            return None, "optional post-start scheduled"
        if getattr(sched, "unverified", False):
            # Lifecycle-INTEGRITY failure: a spawned runner we can neither own nor prove stopped.
            # This GATES the main VERIFIED result (unlike an ordinary optional transport failure).
            return False, f"post-start runner integrity failure: {sched.detail}"
        return None, f"optional post-start could NOT be scheduled: {sched.detail}"

    def _daemon_radio_modes(self) -> list:
        """`--radio` mode of every OBSERVED daemon process (by command line). A missing/unknown
        mode is returned as None so callers can treat it conservatively."""
        import posixpath
        modes = []
        for _pid, argv in self._system.procfs.cmdlines().items():
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
                bands |= {"433", "868"}       # missing / unknown -> conservative
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
            if radio == band or radio not in ("433", "868"):
                out.append(pid)              # unknown mode -> conservatively serves the band
        return out

    def _direct_radio_owners(self, bands) -> dict:
        """For each band in `bands`, the id of a RUNNING radio-direct owner of that band — a non-daemon
        component holding an EXCLUSIVE `loraham.radio.<band>` claim on the band it is ACTUALLY using.
        Keyed off resource metadata, NOT process names. Bands with no such owner are absent from the
        result. `meshtastic-<band>` and `daemon-<band>` are the same physical radio, so a running
        meshtastic on 868 makes 868 owned; a serve-all daemon start uses this to serve only free bands
        instead of colliding (which would fail-closed and take the working band down too)."""
        want = {b for b in bands if b in ("433", "868")}
        if not want:
            return {}
        owners: dict[str, str] = {}
        for ss in self.build_snapshot().stacks:
            sid = ss.stack.id
            multi = bool(self.stack_bands(sid))
            for c in ss.stack.components:
                if c.id == self.DAEMON_ID:
                    continue                             # the daemon PROVIDES the radio; not a direct owner
                if ss.components[c.id].run_state not in (RunState.RUNNING, RunState.DEGRADED):
                    continue
                if multi:
                    eb = self._effective_band(sid, c.band)
                    active = {eb} if eb in ("433", "868") else set()
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
        if radio in ("433", "868"):
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

    def _ensure_daemon(self, life, stack, comp, running, radio, params, start_sid,
                       daemon_overrides=None):
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
            return ["  [BLOCKED] daemon: no radio hardware configured (lhpc hardware)"], False
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
        lines, ok_all = [], True
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
            return lines, False
        # Note when a single radio mode narrows an all-bands request (only when nothing was arbitrated
        # away — the skip line already explains a band-owned narrowing).
        if (radio or "") == "" and not owned and set(needed) != {"433", "868"}:
            lines.append(f"  [note] radio mode {self.radio_mode()}: daemon serves {'+'.join(needed)}")
        for b in not_ready:
            ok_all = False
            lines.append(f"  [fail] daemon on {b}: reachable but RADIO="
                         f"{views[b].radio_state or 'unknown'} (not READY) — dependent launch blocked")
        for b in claimed_down:
            ok_all = False
            lines.append(f"  [fail] daemon on {b}: CONF socket unreachable but a daemon process "
                         f"still holds the radio — not relaunched (resolve the stuck instance first)")
        started: set = set()
        if absent:
            if comp.source and not life.source_dir(comp).exists():
                lines.append("  [skip] daemon: not installed (lhpc install daemon)")
                return lines, False
            if not self.is_built(comp):
                lines.append("  [BLOCKED] daemon: not built — build it first (lhpc build daemon)")
                return lines, False
            # Start ONE per-band instance for EACH missing band — lhpc NEVER launches `--radio
            # both` (a legacy mode the operator may still start manually); it runs an independent
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
                if params and params.get("debug"):
                    dparams["debug"] = "1"
                res = life.start(stack, comp, dparams, band=b)
                base = f"start daemon --radio {b}"
                if not res.ok:
                    lines.append(f"  [fail] {base}: {res.detail}")
                    return lines, False
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
            plines, tx_ok = self._apply_stack_daemon_params(start_sid, b,
                                                            (daemon_overrides or {}).get(b))
            lines.extend(plines)
            ok_all = ok_all and tx_ok
        return lines, ok_all

    def _daemon_param_applies(self, stack_id: str, band: str, overrides: dict | None = None) -> dict:
        """The daemon params lhpc APPLIES for this stack+band — the effective value (source
        default merged with the persisted operator override) of EVERY param that has one. Applied
        ONCE after the daemon is up and before the stack's own components start; the app then
        overwrites the radio params it owns. `overrides` are EPHEMERAL per-start values (e.g. from
        the start-confirm panel) that take precedence for this apply only and are NOT persisted —
        so a confirm-page "Reset to defaults" changes what is applied now, never the saved config.
        Ordered radio-first. Empty for non-daemon stacks."""
        from . import daemon_params
        if not self._has_daemon_params(stack_id):
            return {}
        if band not in daemon_control.ALLOWED_BANDS:
            return {}
        ov = self._daemon_param_overrides(stack_id, band)          # persisted config overrides
        # Ephemeral this-start values (already validated + canonicalised at the start boundary,
        # `_normalize_ephemeral_overrides`) take precedence for this apply only; never persisted.
        for key, val in (overrides or {}).items():
            if key in daemon_params.ALL_PARAMS and str(val) != "":
                ov[key] = str(val)
        out: dict[str, str] = {}
        for name in daemon_params.ALL_PARAMS:
            eff = ov.get(name) or daemon_params.default_value(stack_id, band, name)
            if eff:
                out[name] = eff
        return out

    def _normalize_ephemeral_overrides(self, target: str, launch_bands: list, raw):
        """THE service-side validation/normalization boundary for ephemeral Start-confirm daemon
        overrides. `raw` is a per-band map ``{band: {PARAM: value}}`` (or None). Returns
        ``({band: {PARAM: canonical}}, None)`` on success, or ``({}, error)`` — REJECTING an unknown
        param key, an unknown band, a band not part of THIS launch, and any malformed / out-of-range
        / invalid-enum value (identifying the band + param). Accepted values are canonicalised
        exactly like a persisted save (`fsk`→`FSK`, `028`→`28`); `MODE=FSK` is accepted (browser-
        warning-only). A BLANK value = no override for that key (absent key = same); Reset submits
        explicit default values, so a blank can never resurrect a saved override."""
        from . import daemon_params
        if not raw:
            return {}, None
        if not isinstance(raw, dict):
            return {}, "malformed daemon override payload"
        if not self._has_daemon_params(target):
            return {}, f"{target} has no configurable daemon parameters"
        allowed = set(launch_bands)
        out: dict = {}
        for band, params in raw.items():
            if band not in daemon_control.ALLOWED_BANDS:
                return {}, f"unknown radio band {band!r}"
            if band not in allowed:
                return {}, f"band {band} MHz is not part of this start"
            if not isinstance(params, dict):
                return {}, f"malformed override for band {band}"
            canon: dict = {}
            for name, val in params.items():
                if name not in daemon_params.ALL_PARAMS:
                    return {}, f"unknown daemon parameter {name!r} ({band} MHz)"
                v = str(val).strip()
                if v == "":
                    continue                                       # blank -> no override
                err = daemon_control.validate_set(name, v)
                if err:
                    return {}, f"{band} MHz {name}: {err}"
                canon[name] = daemon_control.canonical_value(name, v)
            if canon:
                out[band] = canon
        return out, None

    def _apply_stack_daemon_params(self, stack_id: str, band: str,
                                   overrides: dict | None = None) -> tuple[list, bool]:
        """Apply the stack's daemon params to a READY band, once (ephemeral `overrides` take
        precedence for this start only). Returns (lines, tx_ok): TXMODE is gating (the app needs
        its mode); radio params (sent-unconfirmed) and CAD tuning are non-gating."""
        lines, tx_ok = [], True
        for key, val in self._daemon_param_applies(stack_id, band, overrides).items():
            ok, detail = self._apply_daemon_param(band, key, val)
            gating = key == "TXMODE"                     # the app needs its mode; radio/CAD not
            if gating:
                tx_ok = ok
            tag = "ok" if ok else ("fail" if gating else "warn")
            # Radio params aren't echoed by the daemon; keep the start log concise (no verbose
            # "SENT but UNCONFIRMED …" explanation for each one).
            if ok and not daemon_control.is_confirmable(key):
                detail = f"{key}={val} sent"
            lines.append(f"  [{tag}] {band}: {detail}")
        return lines, tx_ok

    def daemon_params_view(self, target: str, band: str = "") -> dict:
        """Web view for the daemon-params panel: the grouped, editable radio-parameter rows for
        ONE band, plus the in-panel band chooser (`all_bands` = both toggle options, `bands` = the
        ones this stack can use; the daemon and band-switchable clients get both, a fixed-band stack
        only its own). {} for direct-SPI / unknown stacks. Values are the effective config-file
        values (default + operator save)."""
        from . import daemon_params
        sid = self._owner_stack_id(target)                   # owner-stack daemon profile + storage
        is_daemon = self._is_daemon_target(target)
        if not (is_daemon or daemon_params.is_client(sid)):
            return {}
        # Applicable bands = the SAME source config_view uses for view.bands (never new band logic).
        if is_daemon:
            applicable = list(self.active_bands())                   # the daemon serves the active band(s)
        else:
            applicable = [b for b in (list(self.stack_bands(target))
                                      or [self._effective_daemon_band(target, "")]) if b]
        if not applicable:
            return {}            # no hardware configured -> no served band -> nothing to tune
        b = band if band in applicable else applicable[0]            # CLAMP to a served band (radio mode)
        # "Apply live" only makes sense against a live daemon: the daemon page always, or an
        # app stack that is currently running (its daemon dependency is up).
        can_apply = is_daemon or self.stack_running(sid)
        return {"stack": target, "band": b, "bands": applicable, "all_bands": list(self.RADIO_BANDS),
                "is_daemon": is_daemon, "can_apply": can_apply,
                "rows": daemon_params.stack_view(sid, b, self._daemon_param_overrides(target, b))}

    def daemon_start_panels(self, target: str, params: dict | None = None, band: str = "",
                            display_overrides: dict | None = None) -> list:
        """Start-confirm panel view(s): ONE per band THIS launch will serve — two in radio-mode `both`
        (the daemon runs one process per band), one for a single band or a client start. Each panel
        carries its own band, source defaults + saved overrides, and (via the template) band-scoped
        input names `dp_<band>_<PARAM>`, so a 433 value never reaches 868. The served band(s) come from
        `params` (the daemon's `p_radio`) clamped to the active mode. `display_overrides` ({band: {PARAM: value}})
        are SUBMITTED-but-unsaved panel values shown on a re-render (so a failed Save & start keeps
        the operator's edits). [] for direct-SPI / unknown stacks."""
        from . import daemon_params
        is_daemon = self._is_daemon_target(target)
        if not (is_daemon or daemon_params.is_client(self._owner_stack_id(target))):
            return []
        radio, _tx = self._daemon_needs(self._run_order(target), params, self._config_band(target, band))
        # Always explicit served band(s), clamped to the active mode (M-1) — never a `both` panel.
        bands = self._daemon_serve_bands(radio) if (is_daemon or radio) \
            else [self._effective_daemon_band(target, band)]

        sid = self._owner_stack_id(target)                   # owner-stack daemon profile + overrides
        def _rows(b):
            over = dict(self._daemon_param_overrides(target, b))
            sub = (display_overrides or {}).get(b) if display_overrides else None
            if sub:                                          # show non-blank submitted values
                over.update({k: v for k, v in sub.items() if str(v).strip() != ""})
            return daemon_params.stack_view(sid, b, over)
        return [{"stack": target, "band": b, "is_daemon": is_daemon, "rows": _rows(b)}
                for b in bands]

    def save_daemon_params(self, target: str, band: str, values: dict) -> ActionResult:
        """Persist operator overrides for a stack's daemon params (band-scoped). Semantics:
          * a param NOT present in `values` is left UNCHANGED (direct callers patch a subset);
          * an explicitly BLANK value clears ONLY that param's override;
          * a supplied value is validated, then CANONICALISED (enum upper-cased, integer
            normalised) before compare/store — an equivalent-to-default value clears rather than
            persisting a redundant override, and e.g. `fsk` is stored/displayed as `FSK`.
        Persisted via the LOCKED merge, so normal params, other-band dp_*, remotes and autostart
        all survive. Never applies live."""
        from . import daemon_params, config as cfgmod
        sid = self._owner_stack_id(target)                     # persist into the OWNER stack config
        band = self._effective_daemon_band(target, band)
        if not self._has_daemon_params(target):
            return ActionResult(False, f"{target} has no configurable daemon parameters")
        updates: dict[str, str] = {}
        for name in daemon_params.ALL_PARAMS:
            if name not in values:
                continue                                           # omitted -> leave unchanged
            key = f"dp_{band}_{name}"
            raw = str(values[name]).strip()
            if raw == "":
                updates[key] = ""                                  # explicit blank -> clear this key
                continue
            err = daemon_control.validate_set(name, raw)
            if err:
                return ActionResult(False, f"{name}: {err}")
            canon = daemon_control.canonical_value(name, raw)
            updates[key] = "" if canon == daemon_params.default_value(sid, band, name) else canon
        try:
            # Merge ONLY the dp_<band>_<PARAM> keys into the OWNER stack config (clear_empty=True);
            # sibling run/file params, other-band dp_*, remotes and autostart all survive untouched.
            cfgmod.update_stack_config(self._paths, sid, updates)
        except (OSError, cfgmod.ConfigError, PathContainmentError) as exc:
            return ActionResult(False, f"could not save daemon params: {exc}")
        self._invalidate_config()
        return ActionResult(True, f"saved daemon params for {target} ({band})")

    @invalidates_snapshot
    def apply_daemon_params(self, target: str, band: str = "") -> ActionResult:
        """Apply this stack's effective daemon params to the RUNNING daemon now (the Apply button).

        Serializes against start/stop/restart and another Apply on the same band via the band
        lifecycle lock (re-entrant per thread). TRUTHFUL: `ok=True` only when every attempted set
        is applied; `ok=False` for total failure; a `PARTIAL` `ok=False` when some fail. The
        structured `data` reports band + attempted/applied/failed/confirmed/sent-unconfirmed keys —
        radio params the daemon does not echo are reported SENT (never claimed as read-back
        confirmed). Valid settings were persisted first (by the caller); a live-apply failure never
        rolls that back — `data['persisted']` says so."""
        from . import reslock
        sid = self._owner_stack_id(target)                       # owner-stack profile + lock scope
        if not self._has_daemon_params(target):
            return ActionResult(False, f"{target} has no configurable daemon parameters")
        # Apply live only on the daemon itself or a running app stack (defence in depth: the
        # UI disables the button, the service enforces it).
        is_daemon = self._is_daemon_target(target)
        if not (is_daemon or self.stack_running(sid)):
            return ActionResult(False, f"Apply live is only available while {target} is running")
        b = self._effective_daemon_band(target, band)
        keys = [f"lifecycle.{sid}", f"claim.loraham.radio.{b}"]   # serialize vs start/stop on band
        try:
            with self._keys_guard("apply", target, keys):        # re-entrant per thread
                if not daemon_control.read_view(self._system, b).reachable:
                    return ActionResult(False, f"daemon not serving {b} MHz — start it first",
                                        data={"band": b, "persisted": True})
                applies = self._daemon_param_applies(sid, b)
                applied, failed, confirmed, unconfirmed, details = [], [], [], [], []
                for key, val in applies.items():
                    ok, detail = self._apply_daemon_param(b, key, val)
                    if ok:
                        applied.append(key)
                        (confirmed if daemon_control.is_confirmable(key) else unconfirmed).append(key)
                    else:
                        failed.append(key)
                    details.append(f"{key}={val}: {detail}")
        except reslock.ResourceBusy as busy:
            return ActionResult(False, f"radio {b} MHz is busy ({busy}); try again",
                                data={"band": b, "busy": True, "persisted": True})
        data = {"band": b, "attempted": list(applies), "applied": applied, "failed": failed,
                "confirmed": confirmed, "sent_unconfirmed": unconfirmed, "persisted": True}
        n = len(applies)
        if n == 0:
            return ActionResult(True, f"no daemon parameters to apply on {b} MHz", data=data)
        if not failed:
            return ActionResult(True, f"applied {n}/{n} on {b} MHz ({len(confirmed)} confirmed, "
                                f"{len(unconfirmed)} sent-unconfirmed)", details=details, data=data)
        if not applied:
            return ActionResult(False, f"FAILED to apply any of {n} daemon params on {b} MHz "
                                "(saved profile unchanged)", details=details, data=data)
        return ActionResult(False, f"PARTIAL: applied {len(applied)}/{n}, {len(failed)} FAILED on "
                            f"{b} MHz (saved profile unchanged)", details=details, data=data)

    def reset_daemon_params(self, target: str, band: str) -> ActionResult:
        """Clear all daemon-param overrides for a stack+band (back to source defaults)."""
        from . import daemon_params
        return self.save_daemon_params(target, band, {k: "" for k in daemon_params.ALL_PARAMS})

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

    def _uncertain_daemon_dependents(self, target: str) -> list[str]:
        """Running daemon-dependent stacks whose ACTIVE radio band cannot be trusted for a PER-BAND
        daemon stop — band-switchable stacks with NO valid running/interactive marker. A per-band
        stop must never guess such a peer's band (it could stop or spare the wrong one)."""
        tstack = self.stack(target)
        if tstack is None:
            return []
        member_ids = {c.id for c in tstack.components}
        up = (RunState.RUNNING, RunState.DEGRADED)
        out = []
        for ss in self.build_snapshot().stacks:
            if ss.stack.id == tstack.id:
                continue
            run_comps = [c for c in ss.stack.components if ss.components[c.id].run_state in up]
            if not run_comps:
                continue
            if not any(d in member_ids for c in ss.stack.components for d in c.depends_on):
                continue
            if self.stack_bands(ss.stack.id) and not self._effective_band(ss.stack.id):
                out.append(ss.stack.id)
        return out

    def stop_dependents(self, target: str, bands=None) -> list[str]:
        """Running stacks that would be orphaned if `target` stops (they depend on one of
        its components) — e.g. stopping the daemon orphans kiss/igate/…

        When `bands` is given (the radio band(s) actually being stopped), a dependent is
        included ONLY if it is running on one of those bands: stopping the daemon's 433
        instance does NOT orphan an 868 dependent."""
        tstack = self.stack(target)
        if tstack is None:
            return []
        member_ids = {c.id for c in tstack.components}
        up = (RunState.RUNNING, RunState.DEGRADED)
        want = set(bands) if bands else None
        out = []
        for ss in self.build_snapshot().stacks:
            if ss.stack.id == tstack.id:
                continue
            run_comps = [c for c in ss.stack.components
                         if ss.components[c.id].run_state in up]
            if not run_comps:
                continue
            if not any(d in member_ids for c in ss.stack.components for d in c.depends_on):
                continue
            if want is not None:
                dep_bands = self._running_bands_of(ss, run_comps)
                if dep_bands and not (dep_bands & want):
                    continue                               # different band -> not orphaned
            out.append(ss.stack.id)
        return out

    def _daemon_bands_to_release(self, stk, sid, active_bands) -> tuple[str, list]:
        """(daemon_stack_id, bands) a stopping CLIENT stack no longer needs — the band(s) it was
        ACTUALLY running on (`active_bands`, resolved from its running marker BEFORE deletion) that
        NO other running daemon-dependent stack still uses and where the daemon is still up. Empty
        for the daemon stack itself or a non-daemon-dependent stack. A band-switchable client (KISS/
        Voice) thus releases only the band it ran on, never both."""
        daemon_sid = next((s.id for s in self.stacks() if s.main == self.DAEMON_ID), None)
        if stk is None or not daemon_sid or sid == daemon_sid:
            return daemon_sid or "", []
        if not any(self.DAEMON_ID in (c.depends_on or ()) for c in stk.components):
            return daemon_sid, []
        release = []
        for b in sorted(bb for bb in active_bands if bb in ("433", "868")):
            others = [d for d in self.stop_dependents(daemon_sid, bands={b}) if d != sid]
            if not others and self.daemon_view(b).reachable:
                release.append(b)
        return daemon_sid, release

    @invalidates_snapshot
    def stop(self, target: str, apply: bool = False, cascade: bool = False,
             band: str = "", release_daemon: bool = True, auto_install_ctx=None) -> ActionResult:
        """Public, LOCKED entry — acquires the lifecycle bundle (incl. dependents on
        cascade) so a DIRECT call gets the same coordination as CLI/web. `release_daemon=False`
        is the INTERNAL cascade path: a client stopped as part of a daemon cascade must not itself
        release the daemon (the outer daemon stop is the sole owner of daemon teardown)."""
        if (_r := self._controller_refusal(target)) is not None:
            return _r
        from . import reslock
        if auto_install_ctx is not None:
            ctx_err = self._auto_install_ctx_error(auto_install_ctx, set())
            if ctx_err:
                return ActionResult(False, f"Refusing to stop '{target}': {ctx_err}")
        # A daemon stop is FORCED-cascade — resolve that BEFORE acquiring the lock bundle so the
        # dependents' lifecycle/resource locks are part of the outer guard (no dependent races the
        # cascade), and so a blocking dependent can gate the daemon stop.
        _sid0 = self.stack_of(target)
        _stk0 = self.stack(_sid0) if _sid0 else None
        if _stk0 is not None and _stk0.main == self.DAEMON_ID:
            cascade = True
        if not apply:
            return self._stop_impl(target, apply=False, cascade=cascade, band=band,
                                   release_daemon=release_daemon)
        try:
            with self._lifecycle_guard("stop", target, band, cascade=cascade):
                return self._stop_impl(target, apply=True, cascade=cascade, band=band,
                                       release_daemon=release_daemon)
        except reslock.ResourceBusy as busy:
            return ActionResult(False, f"Cannot stop '{target}': {busy}",
                                next_commands=[f"lhpc status {target}"])

    def _stop_impl(self, target: str, apply: bool = False, cascade: bool = False,
                   band: str = "", release_daemon: bool = True) -> ActionResult:
        items, err = self._resolve(target)
        if err:
            return ActionResult(False, err, next_commands=["lhpc list"])
        life = self._lifecycle()
        # Stop in reverse start order.
        items = list(reversed(items))
        _sid = self.stack_of(target)
        _stk = self.stack(_sid) if _sid else None
        target_is_daemon = bool(_stk and _stk.main == self.DAEMON_ID)
        # The daemon is shared infrastructure: stopping it ALWAYS orphans its dependents, so the
        # cascade is forced (a client is never left pointing at a dead daemon).
        if target_is_daemon:
            cascade = True
        # Bands ACTUALLY being stopped — from the authoritative topology resolver (per-band daemon
        # stop includes its dual-band collateral; separate per-band instances are unaffected).
        _daemon_band_stop = bool(target_is_daemon and band in ("433", "868"))
        stopped_bands = self._operation_bands(target, band, "", "stop") if _daemon_band_stop else None
        other_bands = sorted((stopped_bands or set()) - {band}) if _daemon_band_stop else []
        # Fail closed: a PER-BAND daemon stop must not guess the band of a running band-switchable
        # dependent that has no trustworthy marker — block, name it, and stop NOTHING.
        if _daemon_band_stop:
            uncertain = self._uncertain_daemon_dependents(target)
            if uncertain:
                results = [CompResult(component=d, stack=d, action="stop", outcome=Outcome.BLOCKED,
                    summary="active radio band unknown (no running-band marker) — cannot safely "
                            "scope a per-band daemon stop; stop it explicitly first")
                    for d in uncertain]
                det = [f"  [blocked] dependent '{d}': active radio band unknown — daemon and all "
                       "dependents left untouched" for d in uncertain]
                return ActionResult(False, f"Per-band daemon stop for '{target}' ({band}) blocked — "
                                    f"dependent(s) with unknown active band: {', '.join(uncertain)}",
                                    details=det, results=tuple(results),
                                    next_commands=[f"lhpc status {target}"])
        # Orphaned dependents are scoped to the band(s) actually being stopped.
        dependents = self.stop_dependents(target, bands=stopped_bands)
        # systemd services lhpc doesn't own (e.g. meshtasticd) — stop them as root.
        sysd = [f"sudo systemctl stop {c.units[0].name}"
                for _, c in items if c.units and not c.run_argv]
        if not apply:
            details = [f"  [stop] {comp.id}" for _, comp in items]
            for cmd in sysd:
                details.append(f"    {cmd}")
            return ActionResult(True, f"Stop plan for '{target}': {len(items)} component(s).",
                                details=details,
                                next_commands=[f"lhpc stack stop {target} --yes"],
                                data={"changes": len(items), "dependents": dependents,
                                      "commands": sysd, "other_bands": other_bands})
        details = []
        results: list[CompResult] = []          # every result (dependents + own + daemon release)
        own_results: list[CompResult] = []       # ONLY the target's own components

        # Forced daemon cascade: stop dependents FIRST. An interactive/manual dependent (preflighted
        # before ANY automatic stop), or a dependent whose automatic stop fails / does not verify,
        # BLOCKS the daemon stop — the daemon is never stopped while a dependent is still running.
        dep_block = False
        if cascade:
            interactive_deps = [dep for dep in dependents
                                if (self.stack(dep) and self.stack(dep).main_component
                                    and self.stack(dep).main_component.interactive)]
            if interactive_deps:
                for dep in interactive_deps:
                    results.append(CompResult(component=dep, stack=dep, action="stop",
                        outcome=Outcome.MANUAL_REQUIRED,
                        summary="interactive dependent — stop it yourself before this stack"))
                    details.append(f"  [manual_required] dependent '{dep}' is interactive — "
                                   "stop it yourself first (daemon left running)")
                dep_block = True                 # preflight block: stop NO automatic dependent
            else:
                for dep in dependents:
                    # release_daemon=False: the OUTER daemon stop owns teardown — a dependent must
                    # not recursively stop the daemon (it just clears its own marker on cessation).
                    dep_res = self.stop(dep, apply=True, release_daemon=False)
                    results.append(CompResult(component=dep, stack=dep, action="stop",
                        outcome=Outcome.STOPPED if dep_res.ok else Outcome.UNVERIFIED,
                        summary=dep_res.summary))
                    details.append(f"  [{'stopped' if dep_res.ok else 'unverified'} dependent] {dep}")
                    if not dep_res.ok:
                        dep_block = True

        for _, comp in items:
            # Never stop the daemon while a dependent is still running / not verified stopped.
            if target_is_daemon and comp.id == self.DAEMON_ID and dep_block:
                cr = CompResult(component=comp.id, stack=target, action="stop",
                    outcome=Outcome.BLOCKED,
                    summary="not attempted — a dependent is still running or not verified stopped")
                results.append(cr)
                own_results.append(cr)
                details.append(f"  [blocked] {comp.id}: a dependent is still running — daemon left up")
                continue
            if comp.units and not comp.run_argv:
                # Externally supervised: LHPC cannot verify the stop -> MANUAL_REQUIRED.
                cr = CompResult(component=comp.id, stack=target, action="stop",
                    outcome=Outcome.MANUAL_REQUIRED,
                    summary=f"system service — stop as root: sudo systemctl stop {comp.units[0].name}")
                results.append(cr)
                own_results.append(cr)
                details.append(f"  [manual_required] {comp.id}: stop it as root: "
                               f"sudo systemctl stop {comp.units[0].name}")
                continue
            # The daemon is multi-instance (one process per band). A per-band stop
            # signals ONLY the owned instance(s) serving that band. Record-driven +
            # identity-verified inside Lifecycle.stop, which returns a typed result.
            if comp.id == self.DAEMON_ID and band in ("433", "868"):
                cr = life.stop(comp, band=band)
            else:
                cr = life.stop(comp)
            results.append(cr)
            own_results.append(cr)
            details.append(f"  [{cr.outcome.value}] {comp.id}: {cr.summary}"
                           + (f" (pid {cr.pid})" if cr.pid else ""))

        # The target's OWN cessation (independent of dependents / daemon-release outcome).
        own_ok = applied_ok(own_results) if own_results else True
        sid = self.stack_of(target)
        # Resolve the client's ACTUAL active band BEFORE clearing its running marker (topology
        # resolver: running/interactive marker, else declared band).
        active_bands = set()
        if own_ok and _stk is not None and not target_is_daemon:
            active_bands = self._operation_bands(target, "", "", "stop")
        # Clear band/interactive markers after the target's OWN verified cessation — even if the
        # later daemon-release fails, a stopped client must never look running.
        if sid and own_ok:
            self._safe_unlink(self._band_marker(sid))
            self.dismiss_interactive(sid)
        # A CLIENT stop also releases the daemon band it used — only where no other running
        # dependent needs it. Its typed result feeds the aggregate success (a failed release makes
        # the whole client stop non-success). The just-stopped client is excluded from that check,
        # so there is no recursive re-stop of it.
        if own_ok and not target_is_daemon and release_daemon:
            daemon_sid, release = self._daemon_bands_to_release(_stk, sid, active_bands)
            for b in release:
                dres = self.stop(daemon_sid, apply=True, band=b)
                results.append(CompResult(component=self.DAEMON_ID, stack=daemon_sid, action="stop",
                    outcome=Outcome.STOPPED if dres.ok else Outcome.UNVERIFIED,
                    summary=(f"released {daemon_sid} {b} (no other stack needs it)" if dres.ok
                             else f"{daemon_sid} {b} release NOT verified — {dres.summary}")))
                details.append(f"  [{'stopped' if dres.ok else 'unverified'} daemon] "
                               f"{daemon_sid} {b}: no other stack needs it")
        # A verified daemon stop clears the RX/TX activity window for the band(s) it served — a
        # per-band stop clears that band (+ its dual-band collateral), a whole-daemon stop clears
        # every served band. (A client that releases a daemon band recurses here as a daemon stop.)
        if target_is_daemon and own_ok:
            for _b in ({band, *other_bands} if band in ("433", "868")
                       else set(self.active_bands())):
                self.clear_daemon_feed(_b)
        # ok only when every result (dependents + own + daemon release) is a verified stop.
        ok = applied_ok(results) if results else True
        summary = (f"Stop applied for '{target}'." if ok else
                   f"Stop for '{target}' is NOT fully verified — see details.")
        # A VERIFIED stack stop retires the last-start candidate (the running state it
        # captured no longer exists, so the confirm-known-working offer must disappear) and
        # clears the restart-required flag (the stale processes are gone; the next start uses
        # the saved config).
        if ok and apply and self.stack(target) is not None:
            from . import known_working
            # AUDIT ER4: report a candidate-clear failure instead of swallowing it. A
            # still-present candidate marker keeps the "confirm this stack as working"
            # offer eligible for a stack that was just stopped — the operator could
            # confirm a no-longer-running composition. `read_candidate` does not check
            # liveness, so the "re-validated on read" rationale of the silent path is
            # false. A failed clear downgrades the stop to NOT-fully-verified.
            cleared, why = known_working.clear_candidate_checked(self._paths, target)
            if not cleared:
                ok = False
                summary = (f"Stop for '{target}' applied but the known-working candidate "
                           f"could not be retired — see details.")
                details = list(details) + [f"  [candidate] not cleared: {why}"]
            self._clear_restart_required(target)
        return ActionResult(ok, summary, details=details, results=tuple(results),
                            next_commands=[f"lhpc status {target}"])

    @invalidates_snapshot
    def restart(self, target: str, apply: bool = False, params: dict | None = None,
                stop_owners: bool = False, band: str = "",
                file_overrides: dict | None = None) -> ActionResult:
        """Public, LOCKED entry — holds ONE bundle across the internal stop+start so a
        DIRECT call gets the same coordination as CLI/web."""
        from . import reslock
        if not apply:
            return self._restart_impl(target, apply=False, params=params,
                                      stop_owners=stop_owners, band=band,
                                      file_overrides=file_overrides)
        # A non-daemon restart with NO explicit band restarts on the band it is ACTUALLY running on
        # (not the configured default) — resolve it BEFORE the guard so locking and the restart use
        # the same band (KISS/Voice on 868, restart no-band → lock 868 only, not 433).
        _rband = band
        _sid = self.stack_of(target)
        _stk = self.stack(_sid) if _sid else None
        if not band and _stk is not None and _stk.main != self.DAEMON_ID and self.stack_bands(_sid):
            _rband = self._effective_band(_sid, "") or band
        # Reject invalid / unqualified-duplicate params + file overrides BEFORE any lock (config-
        # independent validation) — so an unqualified duplicate name never acquires the config-
        # stability/lifecycle lock or stops the target. The preflight below re-validates (idempotent)
        # and adds identity enforcement, which needs the stable config read.
        params, _pv = self._normalize_run_params(target, params)
        if _pv:
            return ActionResult(False, f"Cannot restart '{target}': invalid parameter — {_pv}",
                                next_commands=[f"lhpc status {target}"])
        file_overrides, _fo = self._normalize_file_overrides(target, file_overrides)
        if _fo:
            return ActionResult(False, f"Cannot restart '{target}': invalid parameter — {_fo}",
                                next_commands=[f"lhpc status {target}"])
        # Hold saved configuration STABLE from PREFLIGHT through the whole stop→start transition, so
        # a valid target is never stopped and then rejected by a concurrently-mutated config (LOCK
        # ORDER: config guard BEFORE the lifecycle/resource lock; re-entrant with _restart_impl).
        # LOCK ORDER #1: task admission OUTSIDE config-stability and BEFORE preflight/any stop — a
        # restart refused by a pending self-update/uninstall never stops the running stack. The inner
        # _lifecycle_guard (and the nested stop's) reuse this admission reentrantly.
        try:
            with self._admission_guard("restart", target), self._config_stable():
                # PREFLIGHT all start inputs BEFORE lock planning, the guard, owner handling or any
                # stop. A failed preflight is a typed failure that never acquires a lock or touches
                # lifecycle state. Canonical values feed lock/radio planning + _restart_impl.
                params, file_over, _pf_err = self._preflight_start_inputs(
                    target, _rband, params, file_overrides, "restart")
                if _pf_err is not None:
                    return _pf_err
                _order = self._run_order(target)
                _radio = ""
                if _order:
                    _r, _ = self._daemon_needs(_order, params, self._config_band(target, _rband))
                    _radio = _r or ""
                try:
                    with self._lifecycle_guard("restart", target, _rband,
                                               stop_owners=stop_owners, radio=_radio):
                        return self._restart_impl(target, apply=True, params=params,
                                                  stop_owners=stop_owners, band=_rband,
                                                  file_overrides=file_over)
                except SourceTxnBlocked as blocked:
                    return ActionResult(False, f"Cannot restart '{target}': {blocked}",
                                        next_commands=[f"lhpc status {target}"])
                except reslock.ResourceBusy as busy:
                    return ActionResult(False, f"Cannot restart '{target}': {busy}",
                                        next_commands=[f"lhpc status {target}"])
        except AdmissionRefused as _adm:
            return ActionResult(False, _adm.reason, data={'admission_blocked': _adm.tag})
        except reslock.ResourceBusy as busy:
            return ActionResult(False, f"Cannot restart '{target}': {busy}",
                                next_commands=[f"lhpc status {target}"])
        except SourceTxnBlocked as blocked:
            return ActionResult(False, f"Cannot restart '{target}': {blocked}",
                                next_commands=[f"lhpc status {target}"])
        except (OSError, PathContainmentError) as exc:
            return ActionResult(False, f"Cannot restart '{target}': configuration guard unavailable "
                                f"({exc})", next_commands=[f"lhpc status {target}"])

    def _restart_impl(self, target: str, apply: bool = False, params: dict | None = None,
                      stop_owners: bool = False, band: str = "",
                      file_overrides: dict | None = None) -> ActionResult:
        """Applied restarts run the WHOLE preflight→stop→start transition under the configuration-
        stability guard, so a concurrent save can never change the inputs mid-transition (and a valid
        target is never stopped only to be rejected by the later start). A direct/internal apply=True
        call cannot bypass it; dry-run holds no long-lived guard."""
        if not apply:
            return self._restart_impl_inner(target, apply=False, params=params,
                                             stop_owners=stop_owners, band=band,
                                             file_overrides=file_overrides)
        try:
            with self._config_stable():                          # re-entrant (no-op if restart() holds it)
                return self._restart_impl_inner(target, apply=True, params=params,
                                                stop_owners=stop_owners, band=band,
                                                file_overrides=file_overrides)
        except AdmissionRefused as _adm:
            return ActionResult(False, _adm.reason, data={'admission_blocked': _adm.tag})
        except SourceTxnBlocked as blocked:
            return ActionResult(False, f"Cannot restart '{target}': {blocked}",
                                next_commands=[f"lhpc status {target}"])
        except (OSError, PathContainmentError) as exc:
            return ActionResult(False, f"Cannot restart '{target}': configuration guard unavailable "
                                f"({exc})", next_commands=[f"lhpc status {target}"])

    def _restart_impl_inner(self, target: str, apply: bool = False, params: dict | None = None,
                            stop_owners: bool = False, band: str = "",
                            file_overrides: dict | None = None) -> ActionResult:
        """Stop then start a target — used to apply a config change to a running stack.
        With no band given, keep the band the stack is currently running on (so a
        restart doesn't move a band-switchable stack back to its default band)."""
        if not band:
            band = self._effective_band(self.stack_of(target) or target)
        if not apply:
            res = self.start(target, apply=False, params=params, band=band,
                             file_overrides=file_overrides)
            return ActionResult(res.ok, f"Restart plan for '{target}': stop then run.",
                                details=res.details, data=res.data,
                                next_commands=[f"lhpc stack restart {target} --yes"])
        # Defensive: an internal/direct apply=True call must validate BEFORE its stop() — never stop
        # a running target and only then discover the start inputs are invalid.
        params, file_overrides, _pf_err = self._preflight_start_inputs(
            target, band, params, file_overrides, "restart")
        if _pf_err is not None:
            return _pf_err
        stopped = self.stop(target, apply=True, band=band)
        if not stopped.ok:
            # Strict transition: never start after an unverified/failed stop. Preserve
            # the failed-stop typed results as the restart evidence.
            return ActionResult(False,
                                f"Restart aborted for '{target}': stop was not verified.",
                                details=list(stopped.details) + ["  [aborted] not starting "
                                "after an unverified stop — resolve the stop first"],
                                results=tuple(stopped.results),
                                next_commands=[f"lhpc status {target}"])
        time.sleep(1.0)  # let sockets/locks release before re-starting
        res = self.start(target, apply=True, params=params, stop_owners=stop_owners, band=band,
                         file_overrides=file_overrides)
        # Restart's typed results are the stop results followed by the start results.
        return ActionResult(res.ok, f"Restarted '{target}'. {res.summary}",
                            details=res.details,
                            results=tuple(stopped.results) + tuple(res.results),
                            next_commands=res.next_commands)

    @invalidates_snapshot
    def build(self, target: str, apply: bool = False, auto_install_ctx=None,
              on_component_log=None, log_base_override: str = "",
              redactor=None, should_cancel=None) -> ActionResult:
        if (_r := self._controller_refusal(target)) is not None:
            return _r
        # A caller-supplied run-specific log-base prefix (HMAC apply) is validated BEFORE any path is
        # constructed — a strict controller pattern bound to the FULL 32-hex run id (never marker-time only).
        if log_base_override and not _HMAC_LOG_BASE_RE.match(log_base_override):
            return ActionResult(False, f"Refusing to build '{target}': invalid log-base prefix")
        items, err = self._resolve(target)
        if err:
            return ActionResult(False, err, next_commands=["lhpc list"])
        # _resolve returns RUNNABLE components; the build must ALSO cover buildable
        # non-runnable sources (libraries like RadioLib — their artifacts are consumed
        # via build_requires, so skipping them silently pushed builds onto external
        # fallbacks outside the runtime root).
        st_full = self.stack(target)
        if st_full is not None:
            have = {c.id for _, c in items}
            items = items + [(st_full, c) for c in st_full.components
                             if c.build_steps and c.id not in have]
        life = self._lifecycle()
        buildable = [(s, c) for s, c in items if c.build_steps]
        # BUILD-DEPENDENCY order: a component's build_requires providers build FIRST
        # (fresh root: RadioLib's libRadioLib.a must exist before the daemon's build.sh
        # consumes it). Stable within equal rank (manifest order preserved).
        by_id = {c.id: c for _, c in buildable}   # BEFORE sort: the list is empty
        def _rank(c, seen=None):                  # during sorting (CPython list.sort)
            seen = seen or set()
            if c.id in seen:
                return 0                         # defensive: cycle -> flat
            seen.add(c.id)
            deps = [d for d in (c.build_requires or ()) if d in by_id]
            if not deps:
                return 0
            return 1 + max(_rank(by_id[d], seen) for d in deps)
        buildable.sort(key=lambda sc: _rank(sc[1]))
        if not apply:
            details = [f"  [build] {c.id}: "
                       + " ; ".join(" ".join(str(t) for t in st.get("argv", []))
                                    for st in c.build_steps) for _, c in buildable]
            return ActionResult(True, f"Build plan for '{target}': {len(buildable)} component(s).",
                                details=details,
                                next_commands=[f"lhpc build {target} --yes"] if buildable else [],
                                data={"changes": len(buildable)})
        # P0.1: ONE atomic guard — index lock, recover, block on any unresolved journal,
        # then the source-path lock(s) (handoff) held for the whole build. No
        # preflight/acquire race: a journal that appears after a failed transaction is
        # caught under the index lock before the source locks are taken.
        from . import reslock
        src_paths = sorted({c.source.path for _, c in buildable if c.source})
        ctx_err = self._auto_install_ctx_error(auto_install_ctx, src_paths)
        if ctx_err:
            return ActionResult(False, f"Refusing to build '{target}': {ctx_err}")
        try:
            with self._source_operation_guard(src_paths, op="build"):
                from . import auto_install as ai_mod
                details = []
                ok = True
                run_id = getattr(auto_install_ctx, "run_id", "") if auto_install_ctx else ""
                build_meta: dict = {}
                for _, comp in buildable:
                    # auto-install / HMAC: run-specific log base + DURABLE ordered registration BEFORE the
                    # build runs, so the live stream shows only this run's own logs (the file does not
                    # yet exist under a run-specific name -> no prior content).
                    log_base = None
                    if run_id and on_component_log is not None:
                        log_base = ai_mod.component_log_base(run_id, f"build-{comp.id}")
                    elif log_base_override:
                        log_base = f"{log_base_override}-build-{comp.id}"
                    if log_base is not None and on_component_log is not None:
                        n = len(comp.build_steps)
                        for i in range(n):
                            fn = f"{log_base}-{i}.log" if n > 1 else f"{log_base}.log"
                            title = (f"{comp.name} — Build log (step {i + 1}/{n})"
                                     if n > 1 else f"{comp.name} — Build log")
                            on_component_log(f"{title} · logs/{fn}", fn)   # per-step path shown in the web view
                    res = life.build(comp, log_base=log_base,
                                     redactor=redactor, should_cancel=should_cancel,
                                     on_log_open=self._log_announcer(comp.id, details))
                    ok = ok and res.ok
                    details.append(f"  [{res.state.value}] build {comp.id} "
                                   f"(rc {res.returncode}, log {res.log_path})")
                    if not res.ok:
                        details.extend(f"      {ln}" for ln in res.tail[-6:])
                    # Surface a cooperative-cancellation / UNVERIFIED-stop outcome so the caller (the HMAC
                    # driver) can persist the correct terminal state; stop the sweep on it.
                    if getattr(res, "cancelled", False) or getattr(res, "unsafe", False):
                        build_meta = {"cancelled": res.cancelled, "unsafe": res.unsafe,
                                      "unsafe_scope": res.unsafe_scope,
                                      "session_ident": res.session_ident}
                        ok = False
                        break
        except AdmissionRefused as _adm:
            return ActionResult(False, _adm.reason, data={'admission_blocked': _adm.tag})
        except SourceTxnBlocked as blocked:
            return ActionResult(False, f"Build blocked for '{target}': {blocked}",
                                next_commands=[f"lhpc status {target}"])
        except reslock.ResourceBusy as busy:
            return ActionResult(False, f"Build blocked for '{target}': {busy}",
                                next_commands=[f"lhpc status {target}"])
        self.prune_logs()
        return ActionResult(ok, f"Build {'succeeded' if ok else 'FAILED'} for '{target}'.",
                            details=details, next_commands=["lhpc status " + target],
                            data=build_meta)

    def log_tail(self, target: str, lines: int = 300, job: str | None = None,
                 band: str = ""):
        """Raw (path, lines) for `target`'s log — for the live web log view.

        With `job` (a logs/<name>.log filename) it tails that specific job log
        (e.g. a build/test run); otherwise it tails the component's process log —
        `band` selects the instance of a band-scoped component (empty = newest).
        """
        if job:
            # A web-supplied job selector may name ONLY an approved logs/<name>.log
            # file: a single path component, .log suffix, contained under logs/, and
            # never a symlink leaf. Anything else returns empty (no traversal/leak).
            try:
                name = validators.path_component(job, field="job log")
            except validators.ValidationError:
                return "", []
            if not name.endswith(".log"):
                return "", []
            try:
                p = self._paths.under("logs", name)
            except PathContainmentError:
                return "", []
            # Don't surface a symlinked/non-regular log path to the UI (the READ itself is
            # already O_NOFOLLOW-safe via runtime_fs.tail; this just refuses the display).
            if p.is_symlink() or not p.is_file():
                return "", []
            from . import runtime_fs
            return str(p), runtime_fs.tail(self._paths, p, lines)
        s = self.stack(target)
        if s is not None and s.main_component:
            comp = s.main_component
        else:
            idx = self._component_index()
            if target not in idx:
                return "", []
            comp = idx[target][1]
        return self._lifecycle().logs(comp, lines, band=band)

    _WEB_ADMIT_TIMEOUT_S = 3.0

    def _web_admit_timeout(self) -> float:
        import os
        try:
            t = float(os.environ.get("LHPC_WEB_ADMIT_TIMEOUT_S", str(self._WEB_ADMIT_TIMEOUT_S)))
            return t if t > 0 else self._WEB_ADMIT_TIMEOUT_S
        except (TypeError, ValueError):
            return self._WEB_ADMIT_TIMEOUT_S

    def _web_admit_handshake(self, log: str, attempt_id: str):
        """Bounded-poll the primary attempt marker for its admission outcome. Returns
        ("admitted", "") | ("blocked", detail) | ("pending", ""). The parent reserved THIS exact attempt
        before spawning, so an absent/mismatched-attempt marker means it was superseded → blocked immediately.
        Admission is decided by the persisted `admitted` flag (NOT a free-text detail prefix)."""
        from . import jobresult
        import time as _t
        deadline = _t.monotonic() + self._web_admit_timeout()
        while True:
            rec = jobresult.read_one(self._paths, log)
            if rec is None or rec.get("attempt_id") != attempt_id:
                return "blocked", "job attempt was superseded"
            st = rec.get("state")
            if st == "running":
                return "admitted", ""
            if st == "unsafe":                     # orphan/untracked — blocking, needs Recover
                return "blocked", (rec.get("detail") or "the job could not be tracked")
            if st in ("done", "failed"):
                # A child that reached authoritative admission (admitted=True) RAN — surface it (live log +
                # red banner). A terminal that never admitted (lock/journal block) is truly "blocked".
                if rec.get("admitted"):
                    return "admitted", ""
                return "blocked", (rec.get("detail") or "the job could not start")
            if _t.monotonic() >= deadline:         # still `starting`
                return "pending", ""
            _t.sleep(0.05)

    def _web_unsafe_source_block(self, src_keys) -> str:
        """Fail-closed by source IDENTITY: refuse a new web job whose source_keys intersect any attempt that
        PROJECTS to unsafe — persisted `unsafe` OR the derived case (startup_unverified + no live matching
        attempt: the gate-fail / failed-orphan-write fallback). Uses the SAME read-only projection as the
        banner (`_project_job`). "" when clear; Recover is the only unblock."""
        from . import jobresult
        want = set(src_keys or [])
        if not want:
            return ""
        for log, rec in jobresult.read_results(self._paths):
            keys = set(rec.get("source_keys") or [])
            # manifest plausibility: the recorded target must still resolve to a known component/stack.
            if not (self.stack_of(rec.get("target", "")) or self.stack(rec.get("target", ""))):
                continue
            if not (want & keys):
                continue
            state, _hint = self._project_job(rec, log)
            if state == "unsafe":
                return (f"blocked — a previous {rec.get('op', 'job')} on this source ended unsafe; "
                        "Recover it first")
        return ""

    def spawn_web_job(self, op: str, target: str, source: str = "pinned"):
        """Spawn detached build/test/install job(s) for `target`. Returns `(job_log_name, admission, reason)`
        with `admission ∈ {"admitted","blocked","pending"}` — the web adapter flashes per that and NEVER
        infers "started" from a returned log. PRIMARY-FIRST: the main job's admission is handshaked before
        any secondary component job is spawned; a blocked primary spawns none. Each job carries an attempt
        reservation (green/red banner) gated on parent identity tracking."""
        from . import commands, reslock, runtime_fs, jobresult, procident
        import sys
        import os
        import uuid
        import time as _time
        life = self._lifecycle()
        import contextlib as _contextlib
        _adm_stack = _contextlib.ExitStack()
        try:
            self._admit(_adm_stack, "web-job", target)
        except AdmissionRefused as _adm:
            _adm_stack.close()
            return None, "blocked", _adm.reason
        except reslock.ResourceBusy:
            _adm_stack.close()
            return None, "blocked", "a task is starting right now (admission contended) — retry"
        try:
            if op == "install" and source not in self.SOURCE_CHOICES:
                return None, "blocked", (f"invalid source '{source}' (choose "
                                         f"{', '.join(self.SOURCE_CHOICES)})")
            items, err = self._resolve(target)
            if err:
                return None, "blocked", err
            comps = [c for _, c in items]
            # BUILD-REQUIRES ordering for DETACHED (parallel, unsequenceable) jobs: a provider like RadioLib
            # must have its artifact built BEFORE the daemon's build.sh runs. `_resolve` returns only RUNNABLE
            # components, so a non-runnable library provider is otherwise never built by the web job at all.
            # Pull the owning stack's declared providers in; if any is not built yet, build the MISSING
            # provider(s) THIS round and defer the consumer (the operator builds again once they finish) —
            # never build a provider and its consumer in the same parallel round (the consumer would race the
            # provider's artifact). When the providers are already built, build the consumer(s) only.
            build_dep_note = ""
            if op == "build":
                provider_ids = {d for c in comps for d in (c.build_requires or ())}
                if provider_ids:
                    sid = self.stack_of(target)
                    st_full = self.stack(sid) if sid else None
                    have = {c.id for c in comps}
                    if st_full is not None:
                        comps = comps + [c for c in st_full.components
                                         if c.id in provider_ids and c.build_steps and c.id not in have]
                    unbuilt_prov = [c for c in comps if c.id in provider_ids and not self.is_built(c)]
                    if unbuilt_prov:
                        comps = unbuilt_prov
                        names = ", ".join(c.name for c in unbuilt_prov)
                        build_dep_note = (f"Building the build dependency first ({names}) — run Build again "
                                          "to build the rest once it finishes.")
                    else:
                        comps = [c for c in comps if c.id not in provider_ids]
            src_paths = {c.source.path for c in comps if c.source}
            src_keys = sorted({reslock.source_lock_key(c.source.path) for c in comps if c.source})

            # ---- Part G: refuse up-front (auto-install lease / undismissed-unsafe same-source / source-txn busy) ----
            gate = self._auto_install_gate()
            if gate:
                return None, "blocked", f"blocked — {gate}"
            ublk = self._web_unsafe_source_block(src_keys)
            if ublk:
                return None, "blocked", ublk
            pre = self._web_source_precheck(src_paths)
            if pre:
                return None, "blocked", f"blocked — {pre}"

            runtime = str(self._paths.runtime_root)
            runtime_fs.ensure_dir(self._paths, self._paths.under("state", "locks"))
            post_dir = self._paths.under("state", "jobs")
            runtime_fs.ensure_dir(self._paths, post_dir)
            index_lock = str(reslock.lock_file_path(self._paths, self._installer()._index_key()))
            txn_dir = str(self._paths.under("state", "source-txn"))

            def _settle_track(log, aid, pid, terr) -> str:
                """Turn a `_track_or_terminate` outcome into a terminal reservation + a TYPED code the orchestrator
                trusts: "" tracked-ok; "orphan" (ORPHAN RISK ⇒ blocking `unsafe`); "terminated" (proven-terminated
                ⇒ ordinary `failed`). Never inferred later from free text."""
                if not terr:
                    return ""
                if "ORPHAN RISK" in terr:
                    jobresult.terminalize(self._paths, log, aid, "unsafe",
                                          detail="the job could not be identity-tracked and its stop is "
                                                 "UNPROVEN — inspect processes (ps) then Recover",
                                          driver_ident=procident.proc_identity(pid))
                    return "orphan"
                jobresult.terminalize(self._paths, log, aid, "failed", detail=terr[:200])
                return "terminated"

            def _spawn_install():
                name = f"install-{target}"
                log, aid = name + ".log", uuid.uuid4().hex
                if not jobresult.reserve(self._paths, log, aid, "install", target,
                                         self.stack_of(target) or "", src_keys):
                    return None, aid, "could not record the install job (a live attempt exists)"
                argv = [sys.executable, "-m", "lhpc", "install", target, "--yes", "--source", source,
                        "--web-result", log, "--attempt-id", aid]
                ln, pid = life.spawn_job(name, argv, runtime)
                if not ln or not pid:
                    jobresult.terminalize(self._paths, log, aid, "failed", detail="could not start")
                    return None, aid, f"could not start install for '{target}'"
                return log, aid, _settle_track(
                    log, aid, pid, self._track_or_terminate(life, ln, pid, target, "install", attempt_id=aid))

            def _spawn_build(c, steps, name):
                log, aid = name + ".log", uuid.uuid4().hex
                src = str(life.source_dir(c))
                ckeys = sorted({reslock.source_lock_key(c.source.path)}) if c.source else []
                lock_paths = ([str(reslock.lock_file_path(self._paths, ckeys[0]))] if ckeys else [])
                if not jobresult.reserve(self._paths, log, aid, op, c.id,
                                         self.stack_of(c.id) or "", ckeys):
                    return None, aid, f"could not record the {op} job for '{c.id}' (a live attempt exists)"
                try:
                    script = commands.render_build_launcher(
                        steps, runtime, src, lock_paths, index_lock=index_lock, txn_dir=txn_dir,
                        result_name=log, attempt_id=aid, op=op, target=c.id, stack=self.stack_of(c.id) or "")
                except commands.CommandError as exc:
                    jobresult.terminalize(self._paths, log, aid, "failed", detail=str(exc)[:200])
                    return None, aid, f"cannot {op} '{c.id}': {exc}"
                uid = f"{name}-{os.getpid()}-{_time.monotonic_ns()}"
                launcher = runtime_fs.write_launcher(self._paths, post_dir / f"{uid}.py", script)
                ln, pid = life.spawn_job(name, [sys.executable, str(launcher)], src)
                if not ln or not pid:
                    jobresult.terminalize(self._paths, log, aid, "failed", detail="could not start")
                    return None, aid, f"could not start {op} for '{c.id}'"
                return log, aid, _settle_track(
                    log, aid, pid, self._track_or_terminate(life, ln, pid, c.id, op, attempt_id=aid))

            # ---- ordered job list (primary first) ----
            if op == "install":
                spawn_fns = [_spawn_install]
            else:
                bj = []
                for c in comps:
                    if op == "build" and c.build_steps:
                        bj.append((c, list(c.build_steps), f"build-{c.id}"))
                    elif op == "test" and c.test_argv:
                        bj.append((c, [{"argv": list(c.test_argv)}], f"test-{c.id}"))
                if not bj:
                    return None, "blocked", f"nothing to {op} for '{target}'"
                s = self.stack(target)
                main_id = s.main if s else None
                pi = next((i for i, (c, _, _) in enumerate(bj) if c.id == main_id), 0)
                ordered = [bj[pi]] + [j for i, j in enumerate(bj) if i != pi]
                spawn_fns = [(lambda c=c, st=st, nm=nm: _spawn_build(c, st, nm)) for (c, st, nm) in ordered]

            # ---- primary: spawn + handshake; a blocked primary spawns NO secondaries ----
            plog, paid, pout = spawn_fns[0]()
            if plog is None:                        # reserve/spawn/render failed
                self.prune_logs()
                return None, "blocked", f"blocked — {pout}"
            if pout == "orphan":                    # PROVEN: tracking failed, cessation UNPROVEN → blocking unsafe
                self.prune_logs()
                return None, "blocked", "blocked — the job could not be tracked; Recover it first"
            if pout == "terminated":                # PROVEN: driver terminated before it ran → ordinary failed
                self.prune_logs()
                return None, "blocked", "blocked — the job process was terminated before it ran"
            admission, reason = self._web_admit_handshake(plog, paid)
            if admission == "blocked":
                self.prune_logs()
                return None, "blocked", (f"blocked — {reason}" if reason and not reason.startswith("blocked")
                                         else (reason or "blocked"))
            for fn in spawn_fns[1:]:            # admitted/pending: start the secondary component jobs
                fn()
            self.prune_logs()
            return plog, admission, build_dep_note

        finally:
            _adm_stack.close()   # release admission held across the spawn

    def prune_logs(self) -> int:
        """Delete the oldest runtime logs beyond a bounded count/byte budget, NEVER
        touching a log that belongs to an active job (so live evidence is preserved)
        and never following a symlink. Returns the number removed. Called at operation
        boundaries; there is no background cleaner."""
        from .paths import PathContainmentError
        from . import auto_install as ai_mod, jobresult
        protected = {j.get("log") for j in self.active_jobs() if j.get("log")}
        protected = {f"{n}.log" for n in protected} | {n for n in protected}
        # Housekeeping: drop `done` job-result markers older than the banner expiry (failed/unsafe stay),
        # and PROTECT the log of every undismissed failed/unsafe/incomplete-derived result so its banner's
        # View → /logs link is never dead. Naturally bounded — result markers reuse the finite job-log
        # names. All best-effort/read-only; a corrupt jobresults dir never breaks pruning.
        try:
            import time as _t
            jobresult.prune_done(self._paths, int(_t.time()), 60)
            live = {j.get("log") for j in self.active_jobs(cleanup=False) if j.get("log")}
            for log, rec in jobresult.read_results(self._paths):
                # `done` ages out; failed/unsafe (and starting/running that are no longer live → derived
                # incomplete) keep their log while the banner is up.
                if rec.get("state") != "done" or log in live:
                    protected.add(log)
        except Exception:                                # noqa: BLE001 — pruning must never fail
            pass
        # Protect the LIVE auto-install run's own component build/test logs (requirement #7): its
        # durable descriptors are this run's evidence — a mid-run prune must never remove a
        # component log the stream still owns, even if the retention budget is exceeded.
        # Retired runs' logs carry no such protection and age out normally.
        try:
            st = self.auto_install_status()
        except Exception:                        # noqa: BLE001 — pruning must never fail
            st = None
        auto_install_prefix = ""
        if st and not st.get("unsafe") and st.get("state") in ("preparing", "running"):
            try:
                # FULL-run-id component-log prefix (`auto-install-<run32>-`) — matches the
                # exact names the run writes; the 8-hex run-log prefix would not.
                auto_install_prefix = ai_mod.component_log_prefix(st["run_id"])
            except (ValueError, KeyError, TypeError):
                auto_install_prefix = ""
        # Protect the LIVE HMAC apply run's detailed build log(s), bound to the FULL run id — while the run
        # is RUNNING or UNSAFE/unresolved (they may be needed to diagnose an unverified cancellation).
        hmac_prefix = ""
        try:
            hst = self.hmac_apply_status()
        except Exception:                            # noqa: BLE001 — pruning must never fail
            hst = None
        import re as _re2
        if (hst and not hst.get("unsafe") and hst.get("phase") in ("running", "unsafe")
                and _re2.fullmatch(r"[0-9a-f]{32}", hst.get("run_id", ""))):
            # Protect EVERY per-step leaf of the live run (build- AND secret/bridge/node frames), not just
            # `-build-`: `build()` prunes at the end of the firmware step and the earlier secret frame (older
            # mtime) would otherwise be an eviction candidate, blanking it from the second window mid-run.
            hmac_prefix = f"hmac-apply-{hst['run_id']}-"
        # FAIL-CLOSED logs-root resolution + enumeration (no `is_dir()`/`glob`/`is_symlink`):
        # `under` resolves the path (an ESCAPING `logs/` symlink raises PathContainmentError
        # DURING construction — this was outside the guard and could 500 via build()/
        # spawn_web_job()), the no-follow scandir refuses a symlinked/swapped logs dir, and
        # a missing/unreadable directory is an ordinary OSError. Any of these returns 0
        # safely — nothing outside the runtime root is ever read, followed, or deleted.
        try:
            d = self._paths.under("logs")
            entries = runtime_fs.scandir_nofollow(self._paths, d)
        except (OSError, PathContainmentError, ValueError):
            return 0
        import stat as _stat
        logs = []
        for name, is_link in entries:
            if is_link or not name.endswith(".log"):
                continue
            f = d / name
            # REGULAR FILES ONLY, via a DESCRIPTOR-SAFE stat (a path-based lstat could
            # follow a `logs/` parent swapped after enumeration). A directory named
            # `bad.log`, a FIFO/socket/device node, an unreadable leaf, or any
            # uncertainty (`None`) is RETAINED and skipped — never a deletion candidate
            # (an `os.unlink` on a directory would raise IsADirectoryError and escape).
            stt = runtime_fs.stat_leaf_nofollow(self._paths, f)
            if stt is None or not _stat.S_ISREG(stt.st_mode):
                continue
            logs.append((stt.st_mtime, name, f, stt.st_size))
        logs.sort(reverse=True)                                    # newest first
        removed, kept, total = 0, 0, 0
        for _mtime, name, f, size in logs:
            if (name in protected or (auto_install_prefix and name.startswith(auto_install_prefix))
                    or (hmac_prefix and name.startswith(hmac_prefix))):
                continue
            kept += 1
            total += size
            if kept > self.LOG_RETENTION or total > self.LOG_RETENTION_BYTES:
                try:
                    runtime_fs.unlink(self._paths, f)  # descriptor-safe, refuses a symlink
                    removed += 1
                except (OSError, PathContainmentError):
                    pass                               # refused/failed delete -> retain,
                    # never raise, never increment the count (a leaf swapped to a dir or
                    # symlink between stat and unlink lands here safely)
        # AUDIT ER1: the transient launcher scripts (`state/jobs/<uid>.py`,
        # `state/post/<uid>.py`) were created every build/start and NEVER pruned —
        # unbounded inode growth, and (before the secrets-at-exec fix) a resting place for
        # baked secrets. Python reads a launcher wholly at interpreter start, so once its
        # process is running the file is no longer needed; keep only the newest few.
        for sub in ("jobs", "post"):
            removed += self._prune_ephemeral(("state", sub), ".py", self.LOG_RETENTION)
        return removed

    def _prune_ephemeral(self, subdir: tuple, suffix: str, keep: int) -> int:
        """Keep only the newest `keep` REGULAR `suffix`-files under a runtime subdir; remove
        the rest. FULLY FAIL-CLOSED (P2-B): path CONSTRUCTION (`under` raises
        PathContainmentError for an escaping/symlinked `state/jobs`|`state/post`), no-follow
        enumeration, descriptor-safe metadata, and deletion are ALL guarded — an unsafe
        subdir returns a safe zero for that subdir and leaves external sentinels untouched.
        Only regular files are candidates; any uncertainty is retained."""
        import stat as _stat
        from .paths import PathContainmentError
        try:
            d = self._paths.under(*subdir)
            entries = runtime_fs.scandir_nofollow(self._paths, d)
        except (OSError, PathContainmentError, ValueError):
            return 0
        items = []
        for name, is_link in entries:
            if is_link or not name.endswith(suffix):
                continue
            f = d / name
            stt = runtime_fs.stat_leaf_nofollow(self._paths, f)    # descriptor-safe, no-follow
            if stt is None or not _stat.S_ISREG(stt.st_mode):
                continue                                           # regular files only
            items.append((stt.st_mtime, f))
        items.sort(reverse=True)                                   # newest first
        removed = 0
        for _mtime, f in items[keep:]:
            try:
                runtime_fs.unlink(self._paths, f)
                removed += 1
            except (OSError, PathContainmentError):
                pass                                               # retain, never raise
        return removed

    def _jobs_dir(self):
        return self._paths.runtime_root / "state" / "jobs"

    def _write_job_marker(self, log_name: str, pid: int, target: str, op: str,
                          ident: dict | None = None, attempt_id: str = "") -> bool:
        """Record a build/test job with a COMPLETE, PID-reuse-resistant identity. Returns
        True only when a complete identity was captured AND the marker was durably
        persisted; False means the just-spawned process is UNTRACKED and the caller must
        terminate it (no silent orphan). `ident`, when given, is the identity captured
        immediately after spawn — used as-is (never re-read a possibly-reused pid).
        `attempt_id` (a controller-generated hex string, "" for non-web jobs) lets a detached
        web child prove — via `webjob_gate` — that THIS exact attempt was tracked."""
        try:
            slug = validators.path_component(log_name, field="job log")
            path = self._paths.under("state", "jobs", slug + ".job")
        except (validators.ValidationError, PathContainmentError):
            return False
        if ident is None:
            ident = procident.proc_identity(pid) or {}
        # Refuse an incomplete identity via the ONE shared predicate — a marker is never
        # written with sentinel (-1)/blank fields; the caller then terminates the spawn.
        if not (isinstance(pid, int) and pid > 0 and procident.identity_complete(ident)):
            return False
        aid = attempt_id if _re.fullmatch(r"[0-9a-f]{0,64}", attempt_id or "") else ""
        body = (f'launch_id = "{slug}"\npid = {pid}\n'
                f'starttime = {int(ident["starttime"])}\n'
                f'pgid = {int(ident["pgid"])}\nsid = {int(ident["sid"])}\n'
                f'exec = "{ident["exec"]}"\nargv_fp = "{ident["argv_fp"]}"\n'
                f'argv_len = {int(ident["argv_len"])}\n'
                f'target = "{target}"\nop = "{op}"\nlog = "{slug}"\n'
                f'attempt_id = "{aid}"\n')
        try:
            runtime_fs.write_marker(self._paths, path, body)
            return True
        except (OSError, PathContainmentError, validators.ValidationError):
            return False

    def _track_or_terminate(self, life, log_name: str, pid: int, cid: str, op: str,
                            attempt_id: str = "") -> str:
        """Persist a job marker; if it cannot be persisted, terminate the (identity-
        verified) spawned session so it never leaks as an untracked orphan. Returns ""
        on success, else a visible error describing the outcome (the literal 'ORPHAN RISK'
        marks the unproven-cessation case)."""
        # Capture the identity IMMEDIATELY after spawn and use exactly that for both the
        # marker and any cleanup — never re-read a possibly-reused pid as the original job.
        ident = procident.proc_identity(pid)
        if self._write_job_marker(log_name, pid, cid, op, ident=ident, attempt_id=attempt_id):
            return ""
        killed = life._terminate_unobserved(pid, ident)
        if killed:
            return (f"{op} '{cid}' spawned but its job marker could not be persisted; "
                    "the process was terminated (not left orphaned).")
        return (f"{op} '{cid}' spawned but its job marker could not be persisted AND the "
                "process could NOT be confirmed stopped — ORPHAN RISK; check `ps` and kill it.")

    def active_jobs(self, cleanup: bool = True, *, include_unsafe: bool = False) -> list[dict]:
        """Build/test jobs whose ORIGINAL process is still alive (identity-verified).
        A reused PID, a malformed marker, or a symlinked marker is never treated as a
        live job; proven-finished markers are cleaned through the safe API.

        `cleanup=True` (op-boundary callers) clears the marker of a finished/reused job.
        `cleanup=False` is STRICTLY READ-ONLY (no unlink) — the GET-path running-task
        projection uses it so `/api/tasks` never mutates state.

        `include_unsafe=True` (the SELF-UPDATE/uninstall admission gate) ADDITIONALLY reports every
        marker that cannot be PROVEN safe — an unreadable jobs directory, a symlinked/non-regular/
        oversized/uninspectable/malformed marker — as `{"unsafe": True, "name", "reason"}`. The gate
        must block on any such entry: an update/uninstall proceeding past an unprovable job marker
        could tear down a still-running build. (`cleanup=False` is implied when include_unsafe is set —
        the gate never mutates.)

        UNTRUSTED-STATE SAFE (P2): each marker is inspected non-blocking, no-follow,
        regular-only, and BYTE-BOUNDED. A FIFO/device (would block), a directory, a
        symlink/swapped leaf, an oversized or malformed marker is treated as diagnostic
        evidence — never blocks, never trusted as active, never followed, and never
        auto-deleted merely for being malformed. No exception escapes into the callers
        (`prune_logs`/`build`/`test`/`spawn_web_job`)."""
        import tomllib
        import stat as _stat
        from .paths import PathContainmentError
        d = self._jobs_dir()
        # Descriptor-safe enumeration (no `is_dir()`/`glob`): a symlinked/escaping jobs dir
        # fails closed (no trusted jobs); a symlinked marker LEAF is diagnostic evidence,
        # never treated as a live job.
        if include_unsafe:
            cleanup = False                     # the admission gate is strictly read-only
        try:
            entries = runtime_fs.scandir_nofollow(self._paths, d)
        except (OSError, PathContainmentError) as exc:
            # An unreadable/escaping jobs directory cannot prove NO job is running -> a blocker.
            return [{"unsafe": True, "reason": f"jobs directory unreadable: {exc}"}] if include_unsafe else []
        out = []
        for name, is_link in sorted(entries):
            if not name.endswith(".job"):
                continue
            if is_link:
                if include_unsafe:
                    out.append({"unsafe": True, "name": name, "reason": "job marker is a symlink"})
                continue
            f = d / name
            # DESCRIPTOR-SAFE gate: regular file, and small enough to be a real marker.
            # An oversized marker is untrusted (a bounded read could truncate it to a
            # still-parseable prefix and be wrongly trusted) — skip it, do not read it.
            stt = runtime_fs.stat_leaf_nofollow(self._paths, f)
            if stt is None or not _stat.S_ISREG(stt.st_mode) \
                    or stt.st_size > self._JOB_MARKER_MAX:
                if include_unsafe:
                    why = ("uninspectable" if stt is None
                           else "non-regular" if not _stat.S_ISREG(stt.st_mode) else "oversized")
                    out.append({"unsafe": True, "name": name, "reason": f"job marker {why}"})
                continue                        # non-regular/oversized -> untrusted, retain
            try:
                # BOUNDED, non-blocking, no-follow, regular-only read (never blocks on a
                # FIFO, never follows a swapped/symlinked marker, never reads unbounded).
                raw = tomllib.loads(runtime_fs.read_text_regular(
                    self._paths, f, max_bytes=self._JOB_MARKER_MAX))
                pid = int(raw["pid"])
            except (OSError, PathContainmentError, KeyError, ValueError,
                    tomllib.TOMLDecodeError) as exc:
                if include_unsafe:
                    out.append({"unsafe": True, "name": name,
                                "reason": f"job marker malformed/unreadable: {exc}"})
                continue                        # malformed/unsafe -> retain for diagnosis
            if procident.identity_matches(raw, pid):
                out.append({"log": raw.get("log"), "target": raw.get("target"),
                            "op": raw.get("op"), "stack": self.stack_of(raw.get("target", "")),
                            "attempt_id": raw.get("attempt_id", "")})
            elif cleanup:
                # Finished/reused -> clear the marker. If it RACED into a symlink OR a
                # DIRECTORY (IsADirectoryError) between the no-follow read and now, the
                # safe unlink refuses/fails: retain it as evidence rather than letting this
                # public read path throw into prune_logs()/build()/test()/spawn_web_job().
                try:
                    runtime_fs.unlink(self._paths, f)
                except (OSError, PathContainmentError):
                    pass
        return out

    def logs(self, target: str, lines: int = 200, band: str = "") -> ActionResult:
        items, err = self._resolve(target)
        if err:
            return ActionResult(False, err, next_commands=["lhpc list"])
        life = self._lifecycle()
        details = []
        for _, comp in items:
            path, tail = life.logs(comp, lines, band=band)
            if not path:
                details.append(f"[{comp.id}] no log found")
                continue
            details.append(f"[{comp.id}] {path} (last {len(tail)} lines):")
            details.extend(f"  {ln}" for ln in tail)
        return ActionResult(True, f"Logs for '{target}' (bounded tail).", details=details,
                            next_commands=[f"lhpc status {target}"])

    @invalidates_snapshot
    def test(self, target: str, tx: bool = False,
             apply: bool = False, auto_install_ctx=None, on_component_log=None,
             should_cancel=None) -> ActionResult:
        """Run host tests (RX-safe) or a bounded one-frame TX test (`tx=True`). `should_cancel`
        (auto-install Abort) is polled while each host test runs."""
        if (_r := self._controller_refusal(target)) is not None:
            return _r
        items, err = self._resolve(target)
        if err:
            return ActionResult(False, err, next_commands=["lhpc list"])
        ctx_err = self._auto_install_ctx_error(auto_install_ctx,
                                       {c.source.path for _, c in items if c.source})
        if ctx_err:
            return ActionResult(False, f"Refusing to test '{target}': {ctx_err}")
        life = self._lifecycle()
        if not tx:
            # RX-safe host tests.
            if not apply:
                details = [f"  [host-test] {c.id}: "
                           + (" ".join(str(t) for t in c.test_argv) or "(no host test)")
                           for _, c in items]
                return ActionResult(True, f"Host-test plan for '{target}' (TX-safe).",
                                    details=details,
                                    next_commands=[f"lhpc test {target} --yes"],
                                    data={"changes": sum(1 for _, c in items if c.test_argv)})
            # P0.1: ONE atomic guard (index→recover→block→source-lock handoff) held for
            # the whole host-test run — a host test depends on the source's build
            # artifacts, so a concurrent update can't swap the tree mid-test, and a
            # retained journal blocks it with no preflight race.
            from . import reslock
            details = []
            ok = True
            test_meta: dict = {}
            src_paths = sorted({c.source.path for _, c in items if c.source and c.test_argv})
            from . import auto_install as ai_mod
            run_id = getattr(auto_install_ctx, "run_id", "") if auto_install_ctx else ""
            try:
                with self._source_operation_guard(src_paths, op="host-test"):
                    for _, comp in items:
                        # An integration test needs the stack already running. A auto-install/auto-install
                        # sweep builds without starting, so DEFER it there (never a false failure);
                        # an explicit `lhpc test` (no auto_install_ctx) runs it against the running stack.
                        if comp.test_argv and comp.test_requires_running and auto_install_ctx is not None:
                            details.append(f"  [deferred] {comp.id}: needs the running stack "
                                           f"(run `lhpc test {target}` after starting it)")
                            continue
                        # auto-install: run-specific test-log base + DURABLE ordered registration
                        # BEFORE the test runs (same guarantees as the build path).
                        log_base = None
                        if run_id and on_component_log is not None and comp.test_argv:
                            log_base = ai_mod.component_log_base(run_id, f"test-{comp.id}")
                            on_component_log(f"{comp.name} — Test log · logs/{log_base}.log", f"{log_base}.log")
                        res = life.host_test(comp, log_base=log_base, should_cancel=should_cancel,
                                             on_log_open=self._log_announcer(comp.id, details))
                        if res is None:
                            details.append(f"  [skip] {comp.id}: no host test")
                            continue
                        ok = ok and res.ok
                        details.append(f"  [{res.state.value}] {comp.id} "
                                       f"(rc {res.returncode}, log {res.log_path})")
                        # Surface cooperative-cancellation / UNVERIFIED-stop (mirror build()) so the
                        # auto-install driver persists the correct terminal state; stop the sweep on it.
                        if getattr(res, "cancelled", False) or getattr(res, "unsafe", False):
                            test_meta = {"cancelled": res.cancelled, "unsafe": res.unsafe,
                                         "unsafe_scope": res.unsafe_scope,
                                         "session_ident": res.session_ident}
                            ok = False
                            break
            except AdmissionRefused as _adm:
                return ActionResult(False, _adm.reason, data={'admission_blocked': _adm.tag})
            except SourceTxnBlocked as blocked:
                return ActionResult(False, f"Host test blocked for '{target}': {blocked}",
                                    next_commands=[f"lhpc status {target}"])
            except reslock.ResourceBusy as busy:
                return ActionResult(False, f"Host test blocked for '{target}': {busy}",
                                    next_commands=[f"lhpc status {target}"])
            return ActionResult(ok, f"Host test {'passed' if ok else 'FAILED'} for '{target}'.",
                                details=details, next_commands=[f"lhpc status {target}"],
                                data=test_meta)

        # TX-capable test — explicit, gated, bounded. TX is a DAEMON operation; the
        # band(s) come from the target's components (or, for the daemon itself, the
        # bands it is serving), and we only test bands the daemon actually serves.
        wanted = []
        for _, c in items:
            for b in ([c.band] if c.band else []) + list(c.bands):
                if b and b not in wanted:
                    wanted.append(b)
        wanted = [b for b in wanted if self.band_active(b)]       # served bands only (radio mode)
        if not wanted:
            wanted = list(self.active_bands())
        # A TX test drives real RF, so it requires the radio to be READY (not merely a
        # reachable CONF socket): a FAILED/UNINITIALIZED radio must never be TX-tested.
        bands = [b for b in wanted if self.daemon_view(b).ready]
        if not bands:
            return ActionResult(
                False, f"Cannot TX-test '{target}': the daemon isn't serving a READY radio on "
                f"{' or '.join(wanted)} MHz — start the daemon and wait for RADIO=READY first.",
                next_commands=[f"lhpc status {target}"])
        op = self.config().operator
        payload = f"LHPC TX TEST{(' DE ' + op.callsign) if op.configured else ''}"
        if not apply:
            details = ["TX-CAPABLE TEST — this transmits real RF.",
                       "Ensure the antennas are on the connected DUMMY LOADS.", ""]
            for band in bands:
                plan = life.plan_tx_test(target, band, payload)
                details += [f"  band      : {plan.band} MHz",
                            f"  parameters: {plan.parameters}",
                            f"  payload   : {plan.payload!r}",
                            f"  expected  : {plan.expected}", ""]
            return ActionResult(True, f"TX test plan for '{target}' (ONE frame per band).",
                                details=details,
                                next_commands=[f"lhpc test {target} --tx --yes"],
                                data={"changes": len(bands)})
        # An applied TX test transmits REAL RF — hold task admission (lock order #1) across the whole
        # transmit loop so it can never run concurrently with a controller self-update/uninstall. A
        # refusal returns BEFORE any frame is transmitted.
        from . import reslock as _reslock
        try:
            with self._admission_guard("tx-test", target):
                details = []
                ok = True
                attempted_bands: list = []
                for band in bands:
                    # Operator Abort between frames: stop BEFORE the next transmit. A bare `break` would
                    # be UNtruthful (ok stays True → "PASSED"); return a non-success cancelled result.
                    if should_cancel and should_cancel():
                        return ActionResult(
                            False, f"TX test cancelled by operator (Abort) after {len(attempted_bands)} "
                            "band attempt(s).", details=details,
                            data={"cancelled": True, "attempted_bands": attempted_bands})
                    attempted_bands.append(band)           # a frame may transmit even if unconfirmed
                    res = life.run_daemon_tx_test(band, payload)
                    ok = ok and res.ok
                    details.append(f"  [{'ok' if res.ok else 'fail'}] band {res.band}: {res.detail} "
                                   f"(TXOK {res.txok_before}->{res.txok_after})")
                return ActionResult(ok, f"TX test {'PASSED' if ok else 'did not confirm'} for '{target}'.",
                                    details=details,
                                    next_commands=[f"lhpc status {target}", f"lhpc logs {target}"])
        except AdmissionRefused as _adm:
            return ActionResult(False, _adm.reason, data={"admission_blocked": _adm.tag})
        except _reslock.ResourceBusy as busy:
            return ActionResult(False, f"Cannot TX-test '{target}': {busy}",
                                next_commands=[f"lhpc status {target}"])

    def run_params_for(self, target: str):
        """Run parameters offered for `target` (from its main/own components)."""
        s = self.stack(target)
        comps = s.components if s else (
            [self._component_index()[target][1]] if target in self._component_index() else [])
        params = []
        for c in comps:
            params.extend(c.run_params)
        return params

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
            main = s.main_component if s else None
            if not (main and live.get(main.id) in up):
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

    def is_installed(self, target: str) -> bool:
        """Whether a stack's main source is present (nothing to install if it
        declares no source)."""
        s = self.stack(target)
        main = s.main_component if s else None
        if not main or not main.source:
            return True
        return self._paths.resolve_source(main.source.path).is_dir()

    def unbuilt_components(self, target: str) -> list[str]:
        """Component ids in `target` whose source is installed but whose compiled
        binary is missing (need a Build before they can run). Empty = all ready."""
        life = self._lifecycle()
        s = self.stack(target)
        return [c.id for c in (s.components if s else ())
                if c.source and life.source_dir(c).exists() and not self.is_built(c)]

    def unbuilt_build_deps(self, target: str) -> list[str]:
        """The subset of `unbuilt_components` that are BUILD DEPENDENCIES — a `build_requires` provider
        of another component in the stack (e.g. RadioLib, consumed by the daemon's build.sh). These MUST
        be built BEFORE their consumer; the daemon build otherwise fails 'RADIOLIB_DIR not usable'."""
        s = self.stack(target)
        if s is None:
            return []
        providers = {d for c in s.components for d in (c.build_requires or ())}
        unbuilt = set(self.unbuilt_components(target))
        return [c.id for c in s.components if c.id in providers and c.id in unbuilt]

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

    def dash_signature(self) -> str:
        """A compact signature of the dashboard's STRUCTURAL state — which
        components are running, which bands the daemon serves, and which
        interactive apps are marked. The web polls this cheaply and only does a
        full reload when it changes, so live-monitor fields (RSSI/feed/…) update in
        place without the whole page reflowing. Excludes fast-changing telemetry."""
        snap = self.build_snapshot()
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
        rr = self.restart_required_stacks()      # dashboard reloads when the yellow flag flips
        # BOOTING components (post-start runner still applying settings, e.g. MeshCom's
        # callsign push): the yellow 'booting' state must flip the signature when it
        # clears, or the dash keeps showing 'booting' after the node is serving.
        booting = sorted(cid for cid in running if self._component_booting(cid))
        return ("R:" + ",".join(running) + ";D:" + ",".join(usable)
                + ";I:" + ",".join(marks) + ";RR:" + ",".join(rr)
                + ";B:" + ",".join(booting)
                + ";M:" + self.radio_mode())      # mode switch reflows the columns -> force a reload

    def _build_artifact(self, comp):
        """Relative path of the built artifact (the explicit `bin`), or None when the component
        compiles nothing OR declares no build output to check. A component that compiles via
        `build_steps` (e.g. the RadioLib library, or a venv-based Python node) declares its real
        output via `bin` (the .a, or `.venv/bin/python`) so `is_built` is honest. `exec_name` is a
        process-match NAME, never a build-output PATH, so it is NOT used here — using it made venv
        components (exec_name="python") check a non-existent `<src>/python` and read "not built"
        forever."""
        if not (comp.build_cmd or comp.build_steps):
            return None
        return comp.bin or None

    def is_built(self, comp) -> bool:
        """True if the component needs no build, or its built artifact is present.
        The artifact path may carry run-param placeholders (e.g. {env} for the
        firmware build dir), substituted from the stack's saved config.

        A `build_marker` (written by build() ONLY after the last step succeeds) takes precedence over
        `bin`: it is the authoritative completion signal, so a build killed mid-way — leaving `bin`
        present but the build incomplete (e.g. a venv whose interpreter exists but whose pip installs
        never finished) — correctly reads NOT built."""
        if comp.build_marker and (comp.build_cmd or comp.build_steps):
            from . import runtime_fs
            from .lifecycle import BUILD_MARKER_TEXT, _BUILD_MARKER_MAX
            marker = self._lifecycle().source_dir(comp) / comp.build_marker
            try:
                # Descriptor-anchored O_NOFOLLOW regular-file read: a symlink, directory, FIFO/device,
                # oversize/malformed content, or an unsafe/escaping parent all read as NOT built (never
                # a traceback / web 500). Only a regular file whose ENTIRE content is the exact marker
                # text counts as built.
                return runtime_fs.read_text_regular(self._paths, marker,
                                                    max_bytes=_BUILD_MARKER_MAX) == BUILD_MARKER_TEXT
            except (FileNotFoundError, OSError, PathContainmentError):
                return False
        rel = self._build_artifact(comp)
        if rel is None:
            return True
        if "{" in rel:
            cfg = self.stack_config(self.stack_of(comp.id) or "")
            for p in comp.run_params:
                rel = rel.replace("{" + p.name + "}", cfg.get(p.name, p.default))
        return (self._lifecycle().source_dir(comp) / rel).exists()

    def install_blocker(self, comp) -> str:
        """Why a component can't be launched yet ("" = ready): its source isn't
        installed, or it compiles to a binary that isn't built. Avoids handing the
        operator (or the spawner) a command that points at a missing binary."""
        life = self._lifecycle()
        sid = self.stack_of(comp.id) or comp.id
        if comp.source and not life.source_dir(comp).exists():
            return f"not installed — run: lhpc install {sid}"
        if not self.is_built(comp):
            return f"not built — run: lhpc build {sid}"
        return ""

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
        cmd = commands.display_command(comp, op, runtime, src)
        if not cmd:
            return "(no run command)"
        cwd = (commands._paths_subst(comp.run_cwd, runtime, src, "")
               if comp.run_cwd else src)
        return f"cd {shlex.quote(cwd)} && {cmd}"

    def _client_interfaces(self, status, stack_id: str = "") -> list[dict]:
        """User-facing interfaces a client connects to (KISS TCP, web UIs, serial
        PTYs) that are currently present — NOT internal transport sockets.

        A proxied web UI links to the PROXY, not to its loopback upstream: handing a remote browser
        `http://127.0.0.1:18083` is a dead link. The proxy link tracks the proxy's EFFECTIVE listen
        scope (via `_stack_listen_scope`), so a loopback-only proxy is labelled as such and a
        remotely-bound one links to the request host — reflecting reality, not the saved `mode`."""
        if status is None:
            return []
        swc = self.config().stackweb.get(stack_id) if stack_id else None
        out = []
        for obs in status.endpoints:
            sp = obs.spec
            if not getattr(sp, "client", False) or not obs.present:
                continue
            is_web = sp.scheme in ("http", "https")
            link = f"{sp.scheme}://{sp.address}" if is_web else ""
            label = sp.description or sp.address
            entry = {"label": label, "address": sp.address, "scheme": sp.scheme or "tcp",
                     "link": link, "proxy_port": 0, "proxy_scheme": "", "proxy_remote": False,
                     "pending": False}
            if is_web and swc is not None and swc.enabled:
                # Truthful link: the reachable path is the nginx proxy on swc.port, so key off its
                # EFFECTIVE live scope (/proc/net/tcp), NOT the desired `mode`. A stale 0.0.0.0
                # listener (local-mode saved but Apply not run) is honestly shown as remote; a
                # public mode not yet applied is honestly shown as loopback. `pending` marks any such
                # desired-vs-live drift (an Apply will reconcile it).
                entry["link"] = f"{swc.scheme}://127.0.0.1:{swc.port}/"   # loopback/no-request fallback
                scope = self._stack_listen_scope(swc)
                if scope == "exposed":
                    # Reached at the SAME host the browser used for the console (the adapter fills it
                    # from request.host, correct via LAN IP / hostname / WAN — `local_ip()` can only
                    # guess one interface).
                    entry.update(proxy_port=swc.port, proxy_scheme=swc.scheme, proxy_remote=True,
                                 pending=not swc.remote)
                elif scope == "loopback":
                    entry["label"] = f"{label} (local only)"
                    entry["pending"] = bool(swc.remote)
                else:                                # absent: proxy enabled but not listening yet
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

    def radio_overview(self) -> list[dict]:
        """Per-band view for the radio dashboard: daemon/radio config (if running),
        which stack (+ its components) is up on that band, and which stacks can be
        started on it. Live RSSI/CAD/feed are polled separately via /api/daemon."""
        snap = self.build_snapshot()
        live = {cid: st for ss in snap.stacks for cid, st in ss.components.items()}
        up = (RunState.RUNNING, RunState.DEGRADED)
        dstat = live.get(self.DAEMON_ID)
        daemon_proc = dstat.run_state.value if dstat else "unknown"
        daemon_up = bool(dstat and dstat.run_state in up)
        daemon_installed = bool(dstat and dstat.source_state.value
                                in ("match", "dirty", "differs", "unknown", "not-a-repo"))
        dvs = {b: self.daemon_view(b) for b in self.RADIO_BANDS}
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
                if s.id == self.DAEMON_ID:
                    continue
                # Bands this stack can run on (multi-band stacks list several).
                sbands = set()
                for c in s.components:
                    sbands |= set(c.bands) if c.bands else ({c.band} if c.band else set())
                if band not in sbands:
                    continue
                multi = bool(self.stack_bands(s.id))
                running_up = any(c.id in live and live[c.id].run_state in up
                                 for c in s.components if c.band or c.bands)
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
                          "configurable": bool(c.run_params or c.config_file),
                          "writes_log": bool(c.log_paths) or bool(c.run_argv and not c.interactive),
                          "state": ("booting" if booting
                                    else (live[c.id].run_state.value if c.id in live else "unknown")),
                          # No web-UI/client link while booting — it isn't serving yet.
                          "interfaces": ([] if booting
                                         else self._client_interfaces(live.get(c.id), s.id))})
                entry = {"id": s.id, "name": s.name, "main": s.main, "components": comps,
                         "multi_band": multi}
                main_comp = s.component(s.main)

                # Interactive stacks (chat/voice): in the dropdown until the operator
                # "runs" them; after that a dismissable command block is shown in the
                # band column they were started on.
                if main_comp is not None and main_comp.interactive:
                    mark_band = self.interactive_band(s.id)        # None if not active
                    # The box stays only while the app is actually up, OR its marker's daemon band is
                    # still USABLE. An interactive app here is daemon-backed (it reaches this code only
                    # via a band component), so it cannot be running once that daemon is stopped —
                    # stopping the daemon therefore closes a lingering-marker box instead of leaving it.
                    marker_live = mark_band is not None and mark_band in usable_bands
                    active = running_up or marker_live
                    if not active:
                        startable.append(entry)                    # in the dropdown
                        continue
                    col = mark_band or (self.running_band(s.id, sorted(sbands)[0])
                                        if running_up else sorted(sbands)[0])
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
                    if self.running_band(s.id, sorted(sbands)[0]) != band:
                        continue
                    is_up = True
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
                    "liverssi": dv.channel.get("LIVERSSI") if dv.reachable else None,
                },
                "running": running,
                "startable": startable,
                "interactive": interactive,
                # Two+ stacks sharing one radio (e.g. a manually-started chat plus a
                # running iGate) fight over the daemon's tuning — flag it red.
                "conflict": ([s["name"] for s in running]
                             + [s["name"] for s in interactive if s.get("running")])
                            if len(running) + sum(1 for s in interactive if s.get("running")) > 1
                            else [],
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
                    "liverssi": dv.channel.get("LIVERSSI") if dv.reachable else None,
                },
                "running": [], "startable": [], "interactive": [], "conflict": [],
            })
        return out

    def _read_job_marker(self, job: str) -> dict | None:
        """THE centralized bounded, no-follow, regular-file-ONLY reader of a job identity marker
        (`state/jobs/<job>.job`) — reused by `log_running`, active-job inspection, and HMAC abort. Returns
        the parsed dict, or None for a missing/symlinked/oversized/malformed marker. NEVER deletes or
        mutates evidence (a caller that needs to signal validates identity/op/target on the returned dict
        immediately before acting)."""
        import tomllib
        import stat as _stat
        try:
            slug = validators.path_component(job, field="job log")
            f = self._paths.under("state", "jobs", slug + ".job")
        except (validators.ValidationError, PathContainmentError):
            return None
        stt = runtime_fs.stat_leaf_nofollow(self._paths, f)
        if stt is None or not _stat.S_ISREG(stt.st_mode) or stt.st_size > self._JOB_MARKER_MAX:
            return None
        try:
            return tomllib.loads(runtime_fs.read_text_regular(
                self._paths, f, max_bytes=self._JOB_MARKER_MAX))
        except (OSError, PathContainmentError, ValueError, tomllib.TOMLDecodeError):
            return None

    def log_running(self, target: str, job: str | None = None) -> bool:
        """Whether the process behind a log is still alive: for a build/test `job`
        it checks the job marker's pid; for a process log it checks the target's
        main component run-state."""
        if job:
            raw = self._read_job_marker(job)
            if not raw or "pid" not in raw:
                return False
            try:
                # FULL identity match (PID-reuse-safe), never bare PID liveness: a recycled
                # pid running an unrelated process is not this job.
                return procident.identity_matches(raw, int(raw["pid"]))
            except (KeyError, ValueError, TypeError):
                return False
        s = self.stack(target)
        cid = s.main_component.id if (s and s.main_component) else target
        for ss in self.build_snapshot().stacks:
            st = ss.components.get(cid)
            if st is not None:
                return st.run_state in (RunState.RUNNING, RunState.DEGRADED)
        return False

    def start_notes(self, result: "ActionResult") -> list[str]:
        """Per-component `start_note` strings for components that actually started
        (verified / already-healthy) in this result — e.g. how to connect a just-
        launched GUI to its node. Shown as a transient green dashboard note."""
        started = {r.component for r in result.results
                   if getattr(r, "outcome", None) is not None
                   and r.outcome.value in ("verified", "started", "already_healthy")}
        out = []
        for s in self.stacks():
            for c in s.components:
                if c.id in started and c.start_note:
                    out.append(c.start_note)
        return out

    def run_action(self, op: str, target: str, apply: bool = False,
                   params: dict | None = None, source: str = "pinned",
                   stop_owners: bool = False, cascade: bool = False,
                   band: str = "", daemon_overrides: dict | None = None,
                   file_overrides: dict | None = None, purge: bool = False) -> ActionResult:
        """Dispatch a named action to its service method (plan when apply=False)."""
        # An invalid source selector is a typed failure — NEVER silently rewritten to 'dev'.
        if op in ("install", "update") and source not in self.SOURCE_CHOICES:
            return ActionResult(False, f"Invalid source '{source}' (choose "
                                f"{', '.join(self.SOURCE_CHOICES)}).",
                                next_commands=[f"lhpc {op} {target} --source pinned"])
        ops = {
            "install": lambda: self.install(target, apply=apply, source=source),
            "update": lambda: self.update(target, apply=apply, source=source),
            "uninstall": lambda: self.uninstall(target, apply=apply),
            "start": lambda: self.start(target, apply=apply, params=params,
                                        stop_owners=stop_owners, band=band,
                                        daemon_overrides=daemon_overrides,
                                        file_overrides=file_overrides),
            "stop": lambda: self.stop(target, apply=apply, cascade=cascade, band=band),
            "restart": lambda: self.restart(target, apply=apply, params=params,
                                            stop_owners=stop_owners, band=band,
                                            file_overrides=file_overrides),
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
        """Read-only STATUS/STATS/CHANNEL for a band (RSSI bars, counters)."""
        return daemon_control.read_view(self._system, band)

    def daemon_socket_line(self, band: str) -> str:
        """One raw, bounded, sanitised CONF-socket status line for the live 'View Socket' monitor
        ('' when the band is invalid or the socket is unreachable). Read-only, fail-closed."""
        return daemon_control.read_socket_line(self._system, band)

    def optional_start_components(self, target: str) -> list:
        """Optional, non-interactive SERVICE components of a stack (e.g. KISS Serial,
        the MeshCom GPS relay) with their saved auto-start choice — rendered as
        checkboxes on the Confirm:start page. File-only read."""
        s = self.stack(target)
        if s is None:
            return []
        cfg = load_stack_config(self._paths, target)
        return [{"id": c.id, "name": c.name,
                 "autostart": cfg.get(f"autostart_{c.id}") == "on"}
                for c in s.components
                if c.optional and c.kind == ComponentKind.SERVICE
                and not getattr(c, "interactive", False)]
