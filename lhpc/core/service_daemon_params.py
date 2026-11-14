"""ControllerService mixin: the daemon's radio parameters (per radio, and per instance for a client stack), power, TX mode
and feed.

Adapters import lhpc.core.services only. Split from service_params.py and service_lifecycle_ops.py unchanged."""
from __future__ import annotations

import time
from pathlib import Path

from . import daemon_control, radio_start, runtime_fs
from .paths import PathContainmentError
from .service_base import ActionResult
from .snapshot_memo import invalidates_snapshot


class DaemonParamsMixin:

    def _apply_tx_mode(self, band: str, tx: str, rid: str = "") -> tuple[bool, str]:
        """Apply a REQUIRED daemon TX mode and verify it by READBACK. Returns
        (ok, detail). A failed SET, or a readback that is absent/mismatched/
        malformed/timed-out, is a failure that gates every dependent — never a
        warning-success. Skips the SET only when the mode already matches."""
        want = tx.upper()
        view = daemon_control.read_view(self._system, radio_start.daemon_key(self, rid or band))
        if not view.ready:
            state = view.radio_state or ("unreachable" if not view.reachable else "unknown")
            return False, f"{band} radio not READY for TX-mode set (RADIO={state})"
        if view.status.get("TXMODE", "").upper() == want:
            return True, f"TXMODE already {want}"
        ok, _confirmed, detail = daemon_control.apply_set(self._system, radio_start.daemon_key(self, rid or band), "TXMODE", tx)
        if not ok:
            return False, f"SET TXMODE={want} rejected: {detail}"
        waited = 0.0
        while True:                              # bounded read-only readback
            v = daemon_control.read_view(self._system, radio_start.daemon_key(self, rid or band))
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

    def _apply_conf_param(self, band: str, key: str, want, rid: str = "") -> tuple[bool, str]:
        """Apply a numeric daemon LBT param (CADIDLE/CADWAIT, ms) and verify by READBACK.
        Returns (ok, detail). NON-GATING tuning: a failure is only reported (never blocks the
        dependent) because the daemon still does LBT at its current value. Skips the SET when
        the value already matches."""
        want = str(want).strip()
        view = daemon_control.read_view(self._system, radio_start.daemon_key(self, rid or band))
        if not view.ready:
            state = view.radio_state or ("unreachable" if not view.reachable else "unknown")
            return False, f"{band} radio not READY for {key} set (RADIO={state})"
        if self._cadidle_eq(view.status.get(key), want):
            return True, f"{key} already {want}ms"
        ok, _confirmed, detail = daemon_control.apply_set(self._system, radio_start.daemon_key(self, rid or band), key, want)
        if not ok:
            return False, f"SET {key}={want} rejected: {detail}"
        waited = 0.0
        while True:                              # bounded read-only readback
            v = daemon_control.read_view(self._system, radio_start.daemon_key(self, rid or band))
            got = v.status.get(key) if v.reachable else None
            if self._cadidle_eq(got, want):
                return True, f"SET {key}={want}ms confirmed by readback"
            if self.DAEMON_VERIFY_TIMEOUT_S <= 0 or waited >= self.DAEMON_VERIFY_TIMEOUT_S:
                return False, f"{key} readback {got or 'absent'} != {want} (not applied)"
            time.sleep(self.DAEMON_VERIFY_POLL_S)
            waited += self.DAEMON_VERIFY_POLL_S

    # -- per-stack daemon radio parameters (see core/daemon_params.py) ------

    def _apply_daemon_param(self, band: str, key: str, value: str, rid: str = "") -> tuple[bool, str]:
        """Apply one daemon param at `band`. TXMODE and CAD timing are confirmed by readback;
        radio params (FREQ/SF/BW/…) are SET once — the daemon never echoes them, so they are
        reported SENT-but-unconfirmed (non-gating)."""
        if key == "TXMODE":
            return self._apply_tx_mode(band, value, rid)
        if key in ("CADWAIT", "CADIDLE"):
            return self._apply_conf_param(band, key, value, rid)
        view = daemon_control.read_view(self._system, radio_start.daemon_key(self, rid or band))
        if not view.ready:
            state = view.radio_state or ("unreachable" if not view.reachable else "unknown")
            return False, f"{band} radio not READY for {key} (RADIO={state})"
        if key == "POWER":
            # The live predicate: the RUNNING family and permission decide what may be sent,
            # never the saved hardware setup. `apply_set` re-validates against the union only.
            err = self._live_power_error(band, value, view.status, rid)
            if err:
                return False, err
        ok, _confirmed, detail = daemon_control.apply_set(self._system, radio_start.daemon_key(self, rid or band), key, value)
        return ok, detail

    def _live_power_error(self, band: str, value: str, status: dict, rid: str = "") -> str | None:
        """One shared predicate for every LIVE POWER request (direct SET, profile apply, the
        start preflight) so they cannot disagree: `daemon_control.live_power_error` on the
        daemon's own reported family and permission plus the radio's grant (`_granted`)."""
        return daemon_control.live_power_error(
            value, daemon_control.chip_family_from_status(status),
            daemon_control.high_power_from_status(status), self._granted(band, rid))

    def _granted(self, band: str, rid: str = "") -> bool:
        """The +20 dBm grant of radio `rid`, else of `band`'s one enabled radio (radio_start.effective_high_power,
        W311); False when the band names no one radio: default denied, never a per-band switch."""
        rid = self._one_rid(band, rid)
        return bool(rid) and radio_start.effective_high_power(self, rid)

    def _one_rid(self, band: str, rid: str = "") -> str:
        """`rid`, else `band`'s one enabled radio, else "" (the per-band console entries, carried over W409aa)."""
        t = radio_start.band_rid(self, band) if not rid and band in daemon_control.ALLOWED_BANDS else None
        return rid or (t.rid if t is not None and t.outcome == "proceed" else "")

    @invalidates_snapshot
    def set_ocp(self, rid: str, value: str) -> ActionResult:
        """Save radio `rid`'s PA over-current limit (`ocp_ma` in its row: `auto` or mA) under the hardware list's lock;
        restarts nothing. The list's own check accepts only a value the radio's chip can represent (the daemon's rule,
        0 = protection off on SX127x included)."""
        ma = daemon_control.ocp_ma(value)
        if value != "auto" and ma is None:
            return ActionResult(False, f"OCP for {rid} must be 'auto' or mA, not {value!r}")
        return radio_start.set_row_fields(self, rid, lambda row: {"ocp_ma": ma},          # auto clears it: unset = auto
                                          f"OCP for {rid}: {value} — saved; takes effect when {rid}'s daemon is next "
                                          "started (restart it)", restart=("ocp_ma",))

    def high_power_state(self, band: str, view=None, rid: str = "") -> dict:
        """What the console shows for a radio's high-power permission (radio `rid`, else `band`'s one radio) — the
        grant beside the RUNNING daemon's own report, never one masquerading as the other:
          saved       the grant (`_granted`)
          live        HIGHPOWER= from STATUS (True/False), None = unknown/unreachable/older daemon
          family      the RUNNING family from CHIPFAMILY= ("" = unknown); saved_family beside it
          warn        the SX127x hazard banner is due: running family sx127x AND live permission on
          mismatch    a sentence when saved and running disagree, else ""
        `view` is an already-read DaemonView (passive STATUS); read here when absent."""
        if view is None:
            view = daemon_control.read_view(self._system, radio_start.daemon_key(self, rid or band))
        status = view.status if view.reachable else {}
        saved = self._granted(band, rid)
        live = daemon_control.high_power_from_status(status) if view.reachable else None
        fam = daemon_control.chip_family_from_status(status) if view.reachable else ""
        saved_family = self.radio_chip_family(self._one_rid(band, rid))
        mismatch = ""
        # The permission is inert on a running SX1262, so saved/running disagreement about it is
        # not worth a line there; a family disagreement always is.
        if view.reachable and fam and saved_family and fam != saved_family:
            mismatch = (f"the running daemon is {fam} while the hardware list's radio is "
                        f"{saved_family} — restart the daemon")
        elif view.reachable and fam != "sx1262" and live is True and not saved:
            mismatch = ("not granted, but the running daemon still holds the +20 dBm permission — "
                        "restart the daemon to revoke it")
        elif view.reachable and fam != "sx1262" and live is False and saved:
            mismatch = "granted, but the running daemon was started without it — restart the daemon to enable it"
        elif view.reachable and live is None and saved:
            mismatch = "granted, but the running daemon does not report HIGHPOWER= (older daemon) — update it"
        return {"band": band, "rid": rid, "saved": saved, "live": live, "family": fam,
                "saved_family": saved_family, "reachable": view.reachable,
                "warn": bool(fam == "sx127x" and live is True), "mismatch": mismatch}

    @invalidates_snapshot
    def set_high_power(self, rid: str, value: str) -> ActionResult:
        """Grant or withdraw radio `rid`'s +20 dBm permission (`off`/`on`, taken literally) in its row, with the band it
        is given on (`high_power_band`: a hand-moved band drops it, W311), under the hardware list's lock; restarts
        NOTHING (`lhpc hardware --high-power <rid> on|off`). The grant authorises the daemon's next launch
        (`--high-power` in its argv); the running process keeps whatever permission it was started with until it is
        restarted. A model without the switch is refused by the list's own check."""
        if value not in ("off", "on"):
            return ActionResult(False, f"high-power switch must be 'off' or 'on', not {value!r}")
        on, fields = value == "on", ("high_power", "high_power_band")
        note = ("saved; takes effect when its daemon is next started (restart it) — at +20 dBm the transmit duty cycle "
                "must not exceed 1 %, keep the chip cooled; LHPC does not measure or enforce this; warranty void if "
                "disregarded" if on else "saved; a running daemon keeps its permission until it is restarted")
        return radio_start.set_row_fields(self, rid, lambda row: dict(zip(fields, (on, row.band if on else None), strict=True)),
                                          f"high-power permission for {rid} ({{band}} MHz): {value} — {note}", fields)

    def radio_chip_family(self, rid: str) -> str:
        """Chip family of radio `rid` ("sx127x" / "sx1262"), or "" when the hardware list has no such
        radio or the preset is unknown: its row's model (W5-2; per radio, W400).
        The accepted POWER range depends on it: daemon 1.0.0 enforces 2..17 on SX127x (below 2
        RadioLib drives RFO instead of PA_BOOST, and 18..20 are refused) against 0..20 on SX1262.
        "" deliberately narrows nothing — see `daemon_control.int_range`."""
        fam = radio_start.chip_family(self, rid)
        return fam or ""

    def _daemon_feed_source(self, band: str, rid: str = "") -> Path | None:
        """The SINGLE feed source log of one radio's daemon (item 16: radio_start.start_log_name), or None. Never a
        concatenation: the band-agnostic tokens ([TX], [RX], TXOK, ...) match any radio's lines."""
        try:
            p = self._paths.under("logs", radio_start.start_log_name(self.DAEMON_ID, band, rid))       # item 16
        except PathContainmentError:
            return None
        return p if p.is_file() else None

    def _daemon_feed_floor_path(self, band: str, rid: str = "") -> Path:
        return self._paths.under("state", f"daemon-feed-floor-{band}" + (f"-{rid}" if rid else ""))

    def _read_daemon_feed_floor(self, band: str, rid: str = "") -> int:
        """The RX/TX window's clear FLOOR (a byte offset into the source log), or 0 when unset/
        unreadable. Set by `clear_daemon_feed` at each start/stop boundary."""
        try:
            txt = runtime_fs.read_text_regular(self._paths, self._daemon_feed_floor_path(band, rid),
                                               max_bytes=64)
        except (OSError, PathContainmentError, ValueError):
            return 0
        try:
            return max(0, int(txt.strip()))
        except ValueError:
            return 0

    def clear_daemon_feed(self, band: str, rid: str = "") -> None:
        """Clear the RX/TX activity window for a band: record the current daemon-log size as the feed
        FLOOR so `daemon_feed` returns nothing until fresh activity is appended. Non-destructive — the
        append-only daemon log (and its logs view) is untouched. Best-effort; a failure is swallowed so
        it can never break a start/stop. Called at every start (daemon OR any stack) and daemon stop."""
        if not daemon_control.is_valid_band(band):
            return
        for r in [rid] if rid else radio_start.band_rids(self._paths, band) or [""]:   # item 16: each radio's window
            p = self._daemon_feed_source(band, r)
            st = runtime_fs.stat_leaf_nofollow(self._paths, p) if p is not None else None
            try:
                runtime_fs.write_marker(self._paths, self._daemon_feed_floor_path(band, r), str(st.st_size if st else 0))
            except (OSError, PathContainmentError):
                pass

    def daemon_feed(self, band: str, lines: int = 40, rid: str = "") -> list[str]:
        """Bounded tail of the daemon's activity, filtered to RX/TX lines, from the single per-band
        source (`_daemon_feed_source`). Only content beyond the clear FLOOR is shown, so the window is
        emptied at each start/daemon-stop boundary without touching the underlying append-only log."""
        rid = rid or "".join(radio_start.band_rids(self._paths, band)[:1])     # the band's radio when it has one
        p = self._daemon_feed_source(band, rid)
        if p is None:
            return []
        try:
            raws = runtime_fs.tail_since(self._paths, p, self._read_daemon_feed_floor(band, rid),
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
    def daemon_set(self, rid: str, key: str, value: str, apply: bool = False) -> ActionResult:
        """A live SET on radio `rid`'s daemon (its band, chip and grant), plan or apply; a rid that is no enabled radio
        is refused by name, so no caller reaches an arbitrary socket path."""
        _inst, band, why = radio_start.daemon_radio(self, rid)
        if why:
            return ActionResult(False, f"Cannot SET {key.upper()} on {rid}: {why}")
        if key.upper() == "POWER":
            # Live POWER is decided by the RUNNING daemon's family and permission, not by the
            # saved hardware setup (which may have changed since the process started).
            err = daemon_control.validate_set(key, value)              # syntax, union range
            if not err:
                view = daemon_control.read_view(self._system, rid)
                err = (self._live_power_error(band, value, view.status, rid) if view.reachable
                       else f"POWER={value}: the daemon of {rid} is not running — a live POWER is "
                            "checked against the running daemon; start it first")
        else:
            err = daemon_control.validate_set(key, value, self.radio_chip_family(rid))
        if err:
            return ActionResult(False, f"Invalid setting: {err}",
                                next_commands=[f"lhpc daemon {rid}"])
        confirmable = daemon_control.is_confirmable(key)
        if not apply:
            note = ("This changes live daemon behaviour (non-RF tuning)."
                    if confirmable else
                    "This is a radio param the daemon does NOT report back — it can be "
                    "SENT but NOT confirmed over the socket.")
            return ActionResult(
                True,
                f"Will apply SET {key.upper()}={value.upper()} to the daemon of {rid} ({band} MHz, live).",
                details=[note, "It does not transmit by itself."],
                next_commands=[f"lhpc daemon {rid} --set {key}={value} --yes"],
                data={"changes": 1, "confirmable": confirmable})
        ok, confirmed, detail = daemon_control.apply_set(self._system, rid, key, value)
        # Truthful outcome: only a read-back-confirmed SET is "applied"; an unconfirmable
        # radio param that was accepted is "SENT (unconfirmed)", never "applied".
        if not ok:
            verb = "FAILED"
        elif confirmed:
            verb = "applied (confirmed)"
        else:
            verb = "SENT (unconfirmed)"
        return ActionResult(ok, f"SET {key.upper()}={value.upper()}: {verb}.",
                            details=[detail], next_commands=[f"lhpc daemon {rid}"],
                            data={"confirmed": confirmed})

    def _daemon_param_applies(self, stack_id: str, rid: str) -> dict:
        """The daemon params lhpc APPLIES for this stack on radio `rid` — the effective value (the instance's own set
        `<sid>@<rid>` over the radio's row dp_* over the source default; DEC-W384az) of EVERY param that has one.
        Applied ONCE after the daemon is up and before the stack's own components start; the app then overwrites the
        radio params it owns. Ordered radio-first. Empty for non-daemon stacks and a rid that is no enabled radio."""
        from . import daemon_params
        _inst, band, why = radio_start.daemon_radio(self, rid)
        if why or not self._has_daemon_params(stack_id):
            return {}
        ov = {**radio_start.row_dp(self, rid), **radio_start.instance_dp(self, stack_id, rid)}   # the instance's set wins
        out: dict[str, str] = {}
        for name in daemon_params.ALL_PARAMS:
            eff = ov.get(name) or daemon_params.default_value(stack_id, band, name, radio_start.power_max(self, rid))
            if eff:
                out[name] = eff
        return out

    def _apply_stack_daemon_params(self, stack_id: str, band: str, rid: str = "") -> tuple[list, bool, str]:
        """Apply the stack's SAVED daemon params to a READY band, once. Returns (lines, tx_ok,
        gate): TXMODE is gating (the app needs its mode); radio params (sent-unconfirmed) and CAD
        tuning are non-gating. `gate` names a refusal that is about THIS stack's configuration,
        not the daemon (the POWER=20 permission): the daemon is fine and untouched, the stack
        is not started — so the caller must not report the daemon as failed."""
        lines, tx_ok = [], True
        applies = self._daemon_param_applies(stack_id, rid)
        # Preflight an explicit POWER=20 BEFORE the batch: a permission mismatch is known from
        # the daemon's own STATUS without touching the radio, and refusing here avoids a
        # half-retuned daemon. This one failure is START-GATING — the whole point of the
        # high-power switch is explicit authorisation, so knowingly starting the app without it
        # would not be truthful configuration. Every other radio-param failure stays warn-only.
        if str(applies.get("POWER", "")).strip() == "20":
            view = daemon_control.read_view(self._system, radio_start.daemon_key(self, rid or band))
            err = (self._live_power_error(band, "20", view.status, rid) if view.reachable
                   else f"daemon not serving {band} MHz")
            if err:
                lines.append(f"  [fail] {band}: {stack_id} requests POWER=20 but {err} "
                             f"(grant {'on' if self._granted(band, rid) else 'off'}, "
                             f"running HIGHPOWER={view.status.get('HIGHPOWER', '?')})")
                return lines, False, (f"{stack_id} requests POWER=20 on {band} MHz without the "
                                      "running daemon's high-power permission")
        for key, val in applies.items():
            ok, detail = self._apply_daemon_param(band, key, val, rid)
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

    def daemon_params_view(self, target: str, *, rid: str) -> dict:
        """Web view for the daemon-params panel of `target` on radio `rid` (DEC-W384az, radio_start.params_view): the
        daemon's own = rid's row; a client stack's = its instance set `<sid>@<rid>` over the row. {} for a target
        without daemon params, a rid that is no enabled radio, or a client that cannot run on it."""
        return radio_start.params_view(self, target, rid)

    @invalidates_snapshot
    def save_daemon_params(self, target: str, values: dict, *, rid: str) -> ActionResult:
        """Persist `target`'s daemon params on radio `rid` (DEC-W384az, radio_start.save_params): the daemon's own into
        rid's row, a client stack's into its instance set `<sid>@<rid>`. A param NOT in `values` is left unchanged; a
        BLANK clears it; a value is validated for rid's chip and grant, then canonicalised (`fsk` -> `FSK`). Never
        applies live."""
        if not self._has_daemon_params(target):
            return ActionResult(False, f"{target} has no configurable daemon parameters")
        return radio_start.save_params(self, target, rid, values)

    @invalidates_snapshot
    def apply_daemon_params(self, target: str, *, rid: str) -> ActionResult:
        """Apply `target`'s effective daemon params on radio `rid` to its RUNNING daemon now (the Apply button): the
        daemon's own (its row), or a client instance's `<sid>@<rid>` (its set over the row) while it runs there.

        Serializes against start/stop/restart and another Apply on the same radio via the stack's
        lifecycle lock and the radio's claim (re-entrant per thread). TRUTHFUL: `ok=True` only when every attempted set
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
        _sid, _d, b, why = radio_start.dp_target(self, target, rid)
        if why:
            return ActionResult(False, f"Cannot apply {target}'s daemon parameters: {why}")
        name = target if is_daemon else f"{sid}@{rid}"
        if not (is_daemon or radio_start.radio_clients(self, rid, sid)[1]):
            return ActionResult(False, f"Apply live is only available while {name} is running")
        keys = [f"lifecycle.{sid}", "claim." + radio_start.claim_key(self._paths, rid)]   # vs start/stop on its radio
        try:
            with self._keys_guard("apply", target, keys):        # re-entrant per thread
                # Re-checked under the radio's claim, held until the last SET: a start that takes the
                # radio after the checks above is refused or waits, and never has its radio retuned.
                others, runs = radio_start.radio_clients(self, rid, sid)
                if not (is_daemon or runs):
                    return ActionResult(False, f"Apply live is only available while {name} is running",
                                        data={"band": b, "rid": rid, "persisted": True})
                # An app instance's set never retunes a radio another running stack's instance uses.
                if not is_daemon and others:
                    return ActionResult(False, f"Cannot apply {name}'s daemon parameters on {rid}: "
                                               f"that radio serves {', '.join(others)}",
                                        data={"band": b, "rid": rid, "in_use_by": others, "persisted": True})
                if not daemon_control.read_view(self._system, radio_start.daemon_key(self, rid)).reachable:
                    return ActionResult(False, radio_start.socket_refusal(self._paths, b, rid) or f"{rid}'s daemon is not running — start it first",
                                        data={"band": b, "rid": rid, "persisted": True})
                applies = self._daemon_param_applies(sid, rid)
                applied, failed, confirmed, unconfirmed, details = [], [], [], [], []
                for key, val in applies.items():
                    ok, detail = self._apply_daemon_param(b, key, val, rid)
                    if ok:
                        applied.append(key)
                        (confirmed if daemon_control.is_confirmable(key) else unconfirmed).append(key)
                    else:
                        failed.append(key)
                    details.append(f"{key}={val}: {detail}")
        except reslock.ResourceBusy as busy:
            return ActionResult(False, f"radio {rid} is busy ({busy}); try again",
                                data={"band": b, "busy": True, "persisted": True})
        data = {"band": b, "attempted": list(applies), "applied": applied, "failed": failed,
                "confirmed": confirmed, "sent_unconfirmed": unconfirmed, "persisted": True}
        n = len(applies)
        if n == 0:
            return ActionResult(True, f"no daemon parameters to apply on {rid} ({b} MHz)", data=data)
        if not failed:
            return ActionResult(True, f"applied {n}/{n} on {rid} ({b} MHz) ({len(confirmed)} confirmed, "
                                f"{len(unconfirmed)} sent-unconfirmed)", details=details, data=data)
        if not applied:
            return ActionResult(False, f"FAILED to apply any of {n} daemon params on {rid} ({b} MHz) "
                                "(saved profile unchanged)", details=details, data=data)
        return ActionResult(False, f"PARTIAL: applied {len(applied)}/{n}, {len(failed)} FAILED on "
                            f"{b} MHz (saved profile unchanged)", details=details, data=data)

    @invalidates_snapshot
    def reset_daemon_params(self, target: str, *, rid: str) -> ActionResult:
        """Clear `target`'s daemon params on radio `rid`: the daemon's own row dp_*, or a client instance's set (it then
        inherits the row and the source defaults)."""
        from . import daemon_params
        return self.save_daemon_params(target, dict.fromkeys(daemon_params.ALL_PARAMS, ""), rid=rid)
