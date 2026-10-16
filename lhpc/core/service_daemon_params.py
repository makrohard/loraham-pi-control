"""ControllerService mixin: the daemon's radio parameters, power, TX mode and feed, per stack and band.

Adapters import lhpc.core.services only. Split from service_params.py and service_lifecycle_ops.py unchanged."""
from __future__ import annotations

import time
from pathlib import Path

from . import daemon_control, runtime_fs
from .paths import PathContainmentError
from .service_base import ActionResult
from .snapshot_memo import invalidates_snapshot


class DaemonParamsMixin:

    def _apply_tx_mode(self, band: str, tx: str) -> tuple[bool, str]:
        """Apply a REQUIRED daemon TX mode and verify it by READBACK. Returns
        (ok, detail). A failed SET, or a readback that is absent/mismatched/
        malformed/timed-out, is a failure that gates every dependent — never a
        warning-success. Skips the SET only when the mode already matches."""
        want = tx.upper()
        view = daemon_control.read_view(self._system, band)
        if not view.ready:
            state = view.radio_state or ("unreachable" if not view.reachable else "unknown")
            return False, f"{band} radio not READY for TX-mode set (RADIO={state})"
        if view.status.get("TXMODE", "").upper() == want:
            return True, f"TXMODE already {want}"
        ok, _confirmed, detail = daemon_control.apply_set(self._system, band, "TXMODE", tx)
        if not ok:
            return False, f"SET TXMODE={want} rejected: {detail}"
        waited = 0.0
        while True:                              # bounded read-only readback
            v = daemon_control.read_view(self._system, band)
            got = v.status.get("TXMODE", "").upper() if v.reachable else ""
            if got == want:
                return True, f"SET TXMODE={want} confirmed by readback"
            if self.DAEMON_VERIFY_TIMEOUT_S <= 0 or waited >= self.DAEMON_VERIFY_TIMEOUT_S:
                return False, f"TXMODE readback {got or 'absent'} != {want} (not applied)"
            time.sleep(self.DAEMON_VERIFY_POLL_S)
            waited += self.DAEMON_VERIFY_POLL_S

    @staticmethod
    def _cadidle_eq(got, want) -> bool:
        """Numeric equality of two CADIDLE values (daemon reports `CADIDLE=<ms>`); a
        missing/non-numeric reading never matches."""
        try:
            return got is not None and int(got) == int(want)
        except (TypeError, ValueError):
            return False

    def _apply_conf_param(self, band: str, key: str, want) -> tuple[bool, str]:
        """Apply a numeric daemon LBT param (CADIDLE/CADWAIT, ms) and verify by READBACK.
        Returns (ok, detail). NON-GATING tuning: a failure is only reported (never blocks the
        dependent) because the daemon still does LBT at its current value. Skips the SET when
        the value already matches."""
        want = str(want).strip()
        view = daemon_control.read_view(self._system, band)
        if not view.ready:
            state = view.radio_state or ("unreachable" if not view.reachable else "unknown")
            return False, f"{band} radio not READY for {key} set (RADIO={state})"
        if self._cadidle_eq(view.status.get(key), want):
            return True, f"{key} already {want}ms"
        ok, _confirmed, detail = daemon_control.apply_set(self._system, band, key, want)
        if not ok:
            return False, f"SET {key}={want} rejected: {detail}"
        waited = 0.0
        while True:                              # bounded read-only readback
            v = daemon_control.read_view(self._system, band)
            got = v.status.get(key) if v.reachable else None
            if self._cadidle_eq(got, want):
                return True, f"SET {key}={want}ms confirmed by readback"
            if self.DAEMON_VERIFY_TIMEOUT_S <= 0 or waited >= self.DAEMON_VERIFY_TIMEOUT_S:
                return False, f"{key} readback {got or 'absent'} != {want} (not applied)"
            time.sleep(self.DAEMON_VERIFY_POLL_S)
            waited += self.DAEMON_VERIFY_POLL_S

    # -- per-stack daemon radio parameters (see core/daemon_params.py) ------

    def _apply_daemon_param(self, band: str, key: str, value: str) -> tuple[bool, str]:
        """Apply one daemon param at `band`. TXMODE and CAD timing are confirmed by readback;
        radio params (FREQ/SF/BW/…) are SET once — the daemon never echoes them, so they are
        reported SENT-but-unconfirmed (non-gating)."""
        if key == "TXMODE":
            return self._apply_tx_mode(band, value)
        if key in ("CADWAIT", "CADIDLE"):
            return self._apply_conf_param(band, key, value)
        view = daemon_control.read_view(self._system, band)
        if not view.ready:
            state = view.radio_state or ("unreachable" if not view.reachable else "unknown")
            return False, f"{band} radio not READY for {key} (RADIO={state})"
        if key == "POWER":
            # The live predicate: the RUNNING family and permission decide what may be sent,
            # never the saved hardware setup. `apply_set` re-validates against the union only.
            err = self._live_power_error(band, value, view.status)
            if err:
                return False, err
        ok, _confirmed, detail = daemon_control.apply_set(self._system, band, key, value)
        return ok, detail

    def _live_power_error(self, band: str, value: str, status: dict) -> str | None:
        """One shared predicate for every LIVE POWER request (direct SET, profile apply, the
        start preflight) so they cannot disagree: `daemon_control.live_power_error` on the
        daemon's own reported family and permission plus this band's SAVED switch."""
        return daemon_control.live_power_error(
            value, daemon_control.chip_family_from_status(status),
            daemon_control.high_power_from_status(status), self.high_power_for_band(band))

    def high_power_for_band(self, band: str) -> bool:
        """The band's SAVED high-power switch (`hipower_<band>` on the daemon stack): exactly the
        canonical `on`. SAVED INTENT ONLY — it authorises the next launch and is never the running
        value; `daemon_control.high_power_from_status` is the running value. An explicit string
        compare, never `bool(value)`: `bool("off")` is True."""
        if band not in daemon_control.ALLOWED_BANDS:
            return False
        # LITERAL: the strict manifest enum stores exactly "on"; anything else -- "ON", " on ",
        # "true", "1", a hand-edited file -- is not the permission. No stripping, no case folding:
        # a normaliser here would quietly widen what the enum refused at save time (audit P1).
        return self.stack_config("daemon").get(f"hipower_{band}") == "on"

    def high_power_state(self, band: str, view=None) -> dict:
        """What the console shows for a band's high-power permission — saved intent beside the
        RUNNING daemon's own report, never one masquerading as the other:
          saved       the `hipower_<band>` switch
          live        HIGHPOWER= from STATUS (True/False), None = unknown/unreachable/older daemon
          family      the RUNNING family from CHIPFAMILY= ("" = unknown); saved_family beside it
          warn        the SX127x hazard banner is due: running family sx127x AND live permission on
          mismatch    a sentence when saved and running disagree, else ""
        `view` is an already-read DaemonView (passive STATUS); read here when absent."""
        if view is None:
            view = daemon_control.read_view(self._system, band)
        status = view.status if view.reachable else {}
        saved = self.high_power_for_band(band)
        live = daemon_control.high_power_from_status(status) if view.reachable else None
        fam = daemon_control.chip_family_from_status(status) if view.reachable else ""
        saved_family = self.chip_family_for_band(band)
        mismatch = ""
        # The permission is inert on a running SX1262, so saved/running disagreement about it is
        # not worth a line there; a family disagreement always is.
        if view.reachable and fam and saved_family and fam != saved_family:
            mismatch = (f"the running daemon is {fam} while the saved hardware setup is "
                        f"{saved_family} — restart the daemon")
        elif view.reachable and fam != "sx1262" and live is True and not saved:
            mismatch = ("saved off, but the running daemon still holds the +20 dBm permission — "
                        "restart the daemon to revoke it")
        elif view.reachable and fam != "sx1262" and live is False and saved:
            mismatch = "saved on, but the running daemon was started without it — restart the daemon to enable it"
        elif view.reachable and live is None and saved:
            mismatch = "saved on, but the running daemon does not report HIGHPOWER= (older daemon) — update it"
        return {"band": band, "saved": saved, "live": live, "family": fam,
                "saved_family": saved_family, "reachable": view.reachable,
                "warn": bool(fam == "sx127x" and live is True), "mismatch": mismatch}

    @invalidates_snapshot
    def set_high_power(self, band: str, value: str) -> ActionResult:
        """Save the band's high-power switch (`hipower_<band>` = `off`/`on`) through the ordinary
        `save_config_bundle()` transaction, which writes the value and the restart-required marker
        together and restarts NOTHING (`lhpc config daemon` exposes no daemon start option; the CLI
        for this is `lhpc hardware --high-power <band> on|off`). The setting authorises the
        daemon's next launch (`--high-power` in its argv); the running process keeps whatever
        permission it was started with until it is restarted. `value` is taken literally -- the
        service boundary agrees with the manifest enum instead of holding a second, looser
        definition of "strict"."""
        if band not in daemon_control.ALLOWED_BANDS:
            return ActionResult(False, f"Invalid band '{band}' (allowed: "
                                f"{', '.join(daemon_control.ALLOWED_BANDS)}).")
        v = value
        if v not in ("off", "on"):
            return ActionResult(False, f"high-power switch must be 'off' or 'on', not {value!r}")
        r = self.save_config_bundle("daemon", values={f"hipower_{band}": v})
        if not r.ok:
            return r
        note = ("saved; takes effect when the daemon on this band is next started (restart it) — "
                "at +20 dBm the transmit duty cycle must not exceed 1 %, keep the chip cooled; "
                "LHPC does not measure or enforce this; warranty void if disregarded"
                if v == "on" else
                "saved; a running daemon keeps its permission until it is restarted")
        return ActionResult(True, f"high-power permission for {band} MHz: {v} — {note}",
                            details=list(r.details or []), data=dict(r.data or {}))

    def _effective_daemon_band(self, target: str, band: str = "") -> str:
        """The single band the daemon-params panel/apply uses: the requested band if valid, else
        the stack's fixed band (from its band-component), else the first served band; "" when no hardware is configured."""
        active = self.active_bands()                              # served bands (radio mode)
        if not active:
            return ""                                             # no hardware configured -> no band
        if band in active:
            return band
        s = self._owner_stack(target)
        fixed = sorted({c.band for c in (s.components if s else ()) if c.band} & set(active))
        return fixed[0] if fixed else active[0]

    def chip_family_for_band(self, band: str) -> str:
        """Chip family of the board serving `band` ("sx127x" / "sx1262"), or "" when no hardware
        is configured or the preset is unknown.

        The accepted POWER range depends on it: daemon 1.0.0 enforces 2..17 on SX127x (below 2
        RadioLib drives RFO instead of PA_BOOST, and 18..20 are refused) against 0..20 on SX1262.
        "" deliberately narrows nothing — see `daemon_control.int_range`."""
        from . import config as cfgmod
        return cfgmod.hw_preset_family(self.hw_preset_for_band(band))

    def _daemon_feed_source(self, band: str) -> Path | None:
        """The SINGLE feed source log for a band, or None: the per-band log
        `logs/start-<daemon>-<band>.log` (the daemon runs one instance per band). Never a
        concatenation — the band-agnostic tokens ([TX], [RX], TXOK, ...) match either band."""
        try:
            p = self._paths.under("logs", f"start-{self.DAEMON_ID}-{band}.log")
        except PathContainmentError:
            return None
        return p if p.is_file() else None

    def _daemon_feed_floor_path(self, band: str) -> Path:
        return self._paths.under("state", f"daemon-feed-floor-{band}")

    def _read_daemon_feed_floor(self, band: str) -> int:
        """The RX/TX window's clear FLOOR (a byte offset into the source log), or 0 when unset/
        unreadable. Set by `clear_daemon_feed` at each start/stop boundary."""
        try:
            txt = runtime_fs.read_text_regular(self._paths, self._daemon_feed_floor_path(band),
                                               max_bytes=64)
        except (OSError, PathContainmentError, ValueError):
            return 0
        try:
            return max(0, int(txt.strip()))
        except ValueError:
            return 0

    def clear_daemon_feed(self, band: str) -> None:
        """Clear the RX/TX activity window for a band: record the current daemon-log size as the feed
        FLOOR so `daemon_feed` returns nothing until fresh activity is appended. Non-destructive — the
        append-only daemon log (and its logs view) is untouched. Best-effort; a failure is swallowed so
        it can never break a start/stop. Called at every start (daemon OR any stack) and daemon stop."""
        if not daemon_control.is_valid_band(band):
            return
        p = self._daemon_feed_source(band)
        st = runtime_fs.stat_leaf_nofollow(self._paths, p) if p is not None else None
        size = st.st_size if st is not None else 0
        try:
            runtime_fs.write_marker(self._paths, self._daemon_feed_floor_path(band), str(size))
        except (OSError, PathContainmentError):
            pass

    def daemon_feed(self, band: str, lines: int = 40) -> list[str]:
        """Bounded tail of the daemon's activity, filtered to RX/TX lines, from the single per-band
        source (`_daemon_feed_source`). Only content beyond the clear FLOOR is shown, so the window is
        emptied at each start/daemon-stop boundary without touching the underlying append-only log."""
        p = self._daemon_feed_source(band)
        if p is None:
            return []
        try:
            raws = runtime_fs.tail_since(self._paths, p, self._read_daemon_feed_floor(band),
                                         self._FEED_SCAN_LINES, self._FEED_SCAN_BYTES)
        except (OSError, PathContainmentError, ValueError):
            raws = []                            # symlinked/escaping -> no lines
        out: list[str] = []
        for raw in raws:
            if any(tok in raw for tok in (f"[TX{band}]", f"[RX{band}]", "TX_RESULT",
                                          "RX_PACKET", "[TX]", "[RX]", "TXOK",
                                          f"TX {band}", f"RX {band}")):
                out.append(raw)
        return out[-lines:]

    @invalidates_snapshot
    def daemon_set(self, band: str, key: str, value: str, apply: bool = False) -> ActionResult:
        # Validate the band at the service boundary too (not only in web routes): a
        # direct CLI/service caller must never reach a constructed arbitrary socket path.
        if not daemon_control.is_valid_band(band):
            return ActionResult(False, f"Invalid band '{band}' (allowed: "
                                f"{', '.join(daemon_control.ALLOWED_BANDS)}).")
        if key.upper() == "POWER":
            # Live POWER is decided by the RUNNING daemon's family and permission, not by the
            # saved hardware setup (which may have changed since the process started).
            err = daemon_control.validate_set(key, value)              # syntax, union range
            if not err:
                view = daemon_control.read_view(self._system, band)
                err = (self._live_power_error(band, value, view.status) if view.reachable
                       else f"POWER={value}: daemon not serving {band} MHz — a live POWER is "
                            "checked against the running daemon; start it first")
        else:
            err = daemon_control.validate_set(key, value, self.chip_family_for_band(band))
        if err:
            return ActionResult(False, f"Invalid setting: {err}",
                                next_commands=[f"lhpc daemon {band}"])
        confirmable = daemon_control.is_confirmable(key)
        if not apply:
            note = ("This changes live daemon behaviour (non-RF tuning)."
                    if confirmable else
                    "This is a radio param the daemon does NOT report back — it can be "
                    "SENT but NOT confirmed over the socket.")
            return ActionResult(
                True,
                f"Will apply SET {key.upper()}={value.upper()} to the {band} daemon (live).",
                details=[note, "It does not transmit by itself."],
                next_commands=[f"lhpc daemon {band} --set {key}={value} --yes"],
                data={"changes": 1, "confirmable": confirmable})
        ok, confirmed, detail = daemon_control.apply_set(self._system, band, key, value)
        # Truthful outcome: only a read-back-confirmed SET is "applied"; an unconfirmable
        # radio param that was accepted is "SENT (unconfirmed)", never "applied".
        if not ok:
            verb = "FAILED"
        elif confirmed:
            verb = "applied (confirmed)"
        else:
            verb = "SENT (unconfirmed)"
        return ActionResult(ok, f"SET {key.upper()}={value.upper()}: {verb}.",
                            details=[detail], next_commands=[f"lhpc daemon {band}"],
                            data={"confirmed": confirmed})

    def _daemon_param_applies(self, stack_id: str, band: str) -> dict:
        """The daemon params lhpc APPLIES for this stack+band — the effective value (source
        default merged with the persisted operator override) of EVERY param that has one. Applied
        ONCE after the daemon is up and before the stack's own components start; the app then
        overwrites the radio params it owns.
        Ordered radio-first. Empty for non-daemon stacks."""
        from . import daemon_params
        if not self._has_daemon_params(stack_id):
            return {}
        if band not in daemon_control.ALLOWED_BANDS:
            return {}
        ov = self._daemon_param_overrides(stack_id, band)          # persisted config overrides
        out: dict[str, str] = {}
        for name in daemon_params.ALL_PARAMS:
            eff = ov.get(name) or daemon_params.default_value(stack_id, band, name)
            if eff:
                out[name] = eff
        return out

    def _apply_stack_daemon_params(self, stack_id: str, band: str) -> tuple[list, bool, str]:
        """Apply the stack's SAVED daemon params to a READY band, once. Returns (lines, tx_ok,
        gate): TXMODE is gating (the app needs its mode); radio params (sent-unconfirmed) and CAD
        tuning are non-gating. `gate` names a refusal that is about THIS stack's configuration,
        not the daemon (the POWER=20 permission): the daemon is fine and untouched, the stack
        is not started — so the caller must not report the daemon as failed."""
        lines, tx_ok = [], True
        applies = self._daemon_param_applies(stack_id, band)
        # Preflight an explicit POWER=20 BEFORE the batch: a permission mismatch is known from
        # the daemon's own STATUS without touching the radio, and refusing here avoids a
        # half-retuned daemon. This one failure is START-GATING — the whole point of the
        # high-power switch is explicit authorisation, so knowingly starting the app without it
        # would not be truthful configuration. Every other radio-param failure stays warn-only.
        if str(applies.get("POWER", "")).strip() == "20":
            view = daemon_control.read_view(self._system, band)
            err = (self._live_power_error(band, "20", view.status) if view.reachable
                   else f"daemon not serving {band} MHz")
            if err:
                lines.append(f"  [fail] {band}: {stack_id} requests POWER=20 but {err} "
                             f"(saved switch {'on' if self.high_power_for_band(band) else 'off'}, "
                             f"running HIGHPOWER={view.status.get('HIGHPOWER', '?')})")
                return lines, False, (f"{stack_id} requests POWER=20 on {band} MHz without the "
                                      "running daemon's high-power permission")
        for key, val in applies.items():
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
        return lines, tx_ok, ""

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
                "high_power": self.high_power_state(b),
                "rows": daemon_params.stack_view(sid, b, self._daemon_param_overrides(target, b),
                                                 self.chip_family_for_band(b),
                                                 self.high_power_for_band(b))}

    @invalidates_snapshot
    def save_daemon_params(self, target: str, band: str, values: dict) -> ActionResult:
        """Persist operator overrides for a stack's daemon params (band-scoped). Semantics:
          * a param NOT present in `values` is left UNCHANGED (direct callers patch a subset);
          * an explicitly BLANK value clears ONLY that param's override;
          * a supplied value is validated, then CANONICALISED (enum upper-cased, integer
            normalised) before compare/store — an equivalent-to-default value clears rather than
            persisting a redundant override, and e.g. `fsk` is stored/displayed as `FSK`.
        Persisted via the LOCKED merge, so normal params, other-band dp_*, remotes and autostart
        all survive. Never applies live."""
        from . import config as cfgmod
        from . import daemon_params
        sid = self._owner_stack_id(target)                     # persist into the OWNER stack config
        band = self._effective_daemon_band(target, band)
        if not self._has_daemon_params(target):
            return ActionResult(False, f"{target} has no configurable daemon parameters")
        if not band:
            # No served band -> the keys would be `dp__<PARAM>`: stored, never read, reported saved.
            return ActionResult(False, f"Cannot save daemon parameters for '{target}': no radio "
                                       "hardware configured — choose your board in the daemon "
                                       "Hardware settings first")
        updates: dict[str, str] = {}
        for name in daemon_params.ALL_PARAMS:
            if name not in values:
                continue                                           # omitted -> leave unchanged
            key = f"dp_{band}_{name}"
            raw = str(values[name]).strip()
            if raw == "":
                updates[key] = ""                                  # explicit blank -> clear this key
                continue
            err = daemon_control.validate_set(name, raw, self.chip_family_for_band(band),
                                              self.high_power_for_band(band))
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
                # Re-checked under the band's claim, held until the last SET: a start that takes the
                # band after the checks above is refused or waits, and never has its band retuned.
                if not (is_daemon or self.stack_running(sid)):
                    return ActionResult(False, f"Apply live is only available while {target} is running",
                                        data={"band": b, "persisted": True})
                # An app stack's profile never retunes a band another running stack uses.
                if not is_daemon and (others := self._band_used_by_others(target, b)):
                    return ActionResult(False, f"Cannot apply {target}'s daemon parameters on {b} MHz: "
                                               f"that band serves {', '.join(others)}",
                                        data={"band": b, "in_use_by": others, "persisted": True})
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

    @invalidates_snapshot
    def reset_daemon_params(self, target: str, band: str) -> ActionResult:
        """Clear all daemon-param overrides for a stack+band (back to source defaults)."""
        from . import daemon_params
        return self.save_daemon_params(target, band, dict.fromkeys(daemon_params.ALL_PARAMS, ""))
