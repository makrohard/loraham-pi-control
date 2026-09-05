"""Param & config resolution, saves, config-file generation, and daemon-parameter application.

Mixin of ControllerService (state/constants on the facade). Adapters import lhpc.core.services only."""
from __future__ import annotations

import os
import re
import time
from contextlib import contextmanager
from pathlib import Path
from typing import ClassVar

from . import daemon_control, runtime_fs, validators
from . import meshcore_identity as _meshcore_identity
from . import meshcore_mode as _meshcore_mode
from .config import (
    ConfigError,
    _load_runtime_toml,
    _patch_local_table,
    _stack_config_path,
    apply_config_transaction,
    load_secrets,
    load_stack_config,
    merge_stack_values,
    render_keyval,
    render_local_tables,
    render_stack_config,
    update_ini,
    update_stack_config,
    update_toml,
    update_yaml,
)
from .lifecycle import GROUP_MISSING_HINT, GROUP_RESTART_HINT
from .model import RunState
from .paths import PathContainmentError
from .service_base import ActionResult, ConfigWrite
from .snapshot_memo import invalidates_snapshot

# Params that are a property of the STACK, not of the band it happens to be on, and are
# therefore stored in the band-less config file (like autostart). Both the writer
# (`save_config_bundle`) and the reader (`_resolved_param_value`) must agree on this set —
# disagreeing is what made the switch read as its default while being saved as "on".
_BANDLESS_STACK_PARAMS = ("use_gps",)

# MeshCore's position is controller-owned: a LIVE source feeds it through the meshcore-gps
# bridge, and `fixed` writes static coordinates. Either way the values come from the one
# global plan, never from saved config or a start override.
MESHCORE_COMPONENT = "meshcore-node"
# The repeater's controller-minted secrets (see meshcore_identity) and the rows that select the
# node's mode — saved settings only, never per-launch overrides (see _normalize_file_overrides).
_REPEATER_SECRETS = (_meshcore_identity.REPEATER_IDENTITY_FILENAME,
                     _meshcore_identity.REPEATER_ADMIN_FILENAME)
_IDENTITY_SECRET = _meshcore_identity.IDENTITY_FILENAME      # the Companion's key
_MESHCORE_SAVED_ONLY = ("mode", "repeater_name", "repeater_mode")
_POSITION_PARAMS = ("lat", "lon")


class ParamsConfigMixin:

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
        ok, _confirmed, detail = daemon_control.apply_set(self._system, band, key, value)
        return ok, detail

    def _effective_daemon_band(self, target: str, band: str = "") -> str:
        """The single band the daemon-params panel/apply uses: the requested band if valid, else
        the stack's fixed band (from its band-component), else 433."""
        active = self.active_bands()                              # served bands (radio mode)
        if not active:
            return ""                                             # no hardware configured -> no band
        if band in active:
            return band
        s = self._owner_stack(target)
        fixed = sorted({c.band for c in (s.components if s else ()) if c.band} & set(active))
        return fixed[0] if fixed else active[0]

    def _daemon_param_overrides(self, stack_id: str, band: str) -> dict:
        """Persisted operator overrides for a stack's daemon params, read from the stack's
        runtime-local config as flat `dp_<band>_<PARAM>` keys (dot-free, so TOML never nests
        them). Only validated keys are returned; {} when none."""
        from . import config as cfgmod
        from . import daemon_params
        stored = cfgmod.load_stack_config(self._paths, self._owner_stack_id(stack_id))
        out: dict[str, str] = {}
        for name in daemon_params.ALL_PARAMS:
            v = stored.get(f"dp_{band}_{name}")
            if v not in (None, "") and daemon_control.validate_set(name, str(v)) is None:
                out[name] = str(v)
        return out

    def _has_daemon_params(self, target: str) -> bool:
        """True when `target` gets a daemon-param panel: a daemon-client stack/component, the daemon
        component, or the daemon stack. Owner-stack scoped, so a direct daemon-backed COMPONENT
        target (e.g. meshcom-qemu, meshcore-pi) resolves through its owning stack."""
        from . import daemon_params
        return daemon_params.is_client(self._owner_stack_id(target)) or self._is_daemon_target(target)

    # -- component-scoped persisted keys (collision-free, inside the owner stack config) ---------
    # A run/file value can be stored either FLAT (legacy `<name>` / `file_<name>`) or COMPONENT-
    # SCOPED (`__r__<comp>__<name>` / `__f__<comp>__<name>`). A direct component save writes SCOPED
    # keys; a stack save keeps FLAT keys. On read, a scoped value wins; else a flat legacy value is
    # honoured ONLY when its param name is UNIQUE for that kind across the owner stack (otherwise it
    # is ambiguous and never silently applied — the start fails typed, see `_config_ambiguity`).

    @staticmethod
    def _scoped_key(kind: str, comp_id: str, name: str) -> str:
        return f"__{kind}__{comp_id}__{name}"

    def _owner_param_counts(self, owner) -> tuple[dict, dict]:
        """(run_counts, file_counts): how many owner-stack components declare each run/file param
        name — a name with count >= 2 is AMBIGUOUS for a flat legacy value."""
        run_c: dict = {}
        file_c: dict = {}
        for c in (owner.components if owner is not None else ()):
            for p in c.run_params:
                run_c[p.name] = run_c.get(p.name, 0) + 1
            for p in (c.config_file.params if c.config_file else ()):
                file_c[p.name] = file_c.get(p.name, 0) + 1
        return run_c, file_c

    def _resolve_stored(self, stored: dict, kind: str, comp_id: str, name: str,
                        count: int) -> tuple:
        """(value_or_None, ambiguous). A component-SCOPED key wins; else a FLAT legacy key ONLY when
        the name is UNIQUE (count <= 1). `ambiguous` is True when a flat legacy value exists but the
        name is declared by >= 2 components and no scoped value overrides it — a value that must NOT
        be silently applied."""
        sk = self._scoped_key(kind, comp_id, name)
        if sk in stored:
            return stored[sk], False
        flat = name if kind == "r" else f"file_{name}"
        if flat in stored:
            if count <= 1:
                return stored[flat], False               # unique legacy -> backward compatible
            return None, True                            # ambiguous flat -> never silently applied
        return None, False

    # -- component identity through the parameter pipeline (form/API/overrides/save) -------------
    # Every editable value is (component_id, kind, name). A name UNIQUE within the target's own
    # components keeps its bare representation (`name`, `p_<name>`/`pf_<name>`) — backward compatible;
    # a DUPLICATED name is component-qualified: API/CLI key `component_id.name`, web field
    # `p_<component_id>__<name>` / `pf_<component_id>__<name>`. This keeps colliding components'
    # values distinct end to end instead of flattening them into one `{name: value}` map.

    def _dup_names(self, target: str) -> tuple[set, set]:
        """(dup_run, dup_file): run/file parameter names declared by MORE THAN ONE component of the
        TARGET's own scope (a direct component target has one component, so never any duplicates)."""
        rc: dict = {}
        fc: dict = {}
        for c in self._target_components(target):
            for p in c.run_params:
                rc[p.name] = rc.get(p.name, 0) + 1
            for p in (c.config_file.params if c.config_file else ()):
                fc[p.name] = fc.get(p.name, 0) + 1
        return {n for n, k in rc.items() if k > 1}, {n for n, k in fc.items() if k > 1}

    def _param_key(self, target: str, kind: str, comp_id: str, name: str) -> str:
        """The API/CLI key for one (component, kind, name): bare `name` when unique, else the
        component-qualified `component_id.name`."""
        dup_run, dup_file = self._dup_names(target)
        dup = dup_run if kind == "run" else dup_file
        return f"{comp_id}.{name}" if name in dup else name

    def _param_field(self, target: str, kind: str, comp_id: str, name: str) -> str:
        """The Start-confirm form field for one (component, kind, name): `p_<name>`/`pf_<name>` when
        unique, else `p_<component_id>__<name>` / `pf_<component_id>__<name>`."""
        prefix = "p_" if kind == "run" else "pf_"
        dup_run, dup_file = self._dup_names(target)
        dup = dup_run if kind == "run" else dup_file
        return f"{prefix}{comp_id}__{name}" if name in dup else f"{prefix}{name}"

    def _config_field(self, target: str, kind: str, comp_id: str, name: str) -> str:
        """The permanent Config-page form field for one (component, kind, name): `c_<name>`/`f_<name>`
        when unique, else `c_<component_id>__<name>` / `f_<component_id>__<name>` (same qualification
        rule + canonical API key as Start-confirm, just the Config page's `c_`/`f_` prefixes)."""
        prefix = "c_" if kind == "run" else "f_"
        dup_run, dup_file = self._dup_names(target)
        dup = dup_run if kind == "run" else dup_file
        return f"{prefix}{comp_id}__{name}" if name in dup else f"{prefix}{name}"

    def config_param_fields(self, target: str, band: str = "") -> list[dict]:
        """Config-page editable run + file parameters as {component, name, kind ('run'|'file'), field
        (`c_`/`f_` scheme), key (canonical API key), flag, default} — the single source of truth for
        the Config POST parser to fold each submitted field into its canonical API key. [] for a
        daemon target (its radio params are ephemeral start options, not persisted config)."""
        if self._is_daemon_target(target):
            return []
        out: list[dict] = []
        for c in self._target_components(target):
            for kind, p in ([("run", p) for p in self._form_run_params(c)]
                            + [("file", p) for p in (c.config_file.params if c.config_file else ())
                               if not getattr(p, "hidden", False)]):
                out.append({
                    "component": c.id, "name": p.name, "kind": kind, "flag": p.kind == "flag",
                    "field": self._config_field(target, kind, c.id, p.name),
                    "key": self._param_key(target, kind, c.id, p.name),
                    "default": p.default})
        return out

    def config_param_groups(self, target: str, band: str = "") -> list[dict]:
        """Per-component Config rows (run THEN file params) for the settings-page 3-col panel — each
        row carries its OWN component-aware field (`c_`/`f_` scheme), API key, and component-scoped
        value (never masked by a same-named sibling). A group is flagged `is_dep` when it is not the
        stack's MAIN component, so the UI can rule a line before/after the dependency components.
        [] for a daemon target (its radio params are the separate daemon-parameter panel)."""
        if self._is_daemon_target(target):
            return []
        s = self.stack(target)
        main_id = s.main if s is not None else None
        # IDENTITY rows on the SETTINGS surface follow the same truthfulness rules as the
        # start-confirm panel (audit-found: this surface still prefilled the substituted
        # global, so merely saving another setting persisted it as a local override —
        # copy-on-save through the ordinary Config page): stored-only value, empty form
        # default (reset = inherit), inheritance as placeholder/note, never in the input.
        id_rows = {(r["comp"], r["kind"], r["name"]): r
                   for r in self.identity_resolution(target, band)}
        cfg_band = self._config_band(target, band)
        groups: list[dict] = []
        for c in self._target_components(target):
            # Rows keyed by settings sub-group ("" = the component's own block). A param's `group`
            # (e.g. "Network exposure") renders as its OWN titled block, right after the component's
            # main block — same shape as the surrounding config sections, in both the settings page
            # and the confirm:start panel (both consume this method).
            by_group: dict[str, list] = {}
            for kind, p in ([("run", p) for p in self._form_run_params(c)]
                            + [("file", p) for p in (c.config_file.params if c.config_file else ())
                               if not getattr(p, "hidden", False)]):
                idrec = id_rows.get((c.id, kind, p.name))
                default = ("" if idrec else
                           self._op_subst(dict(p.band_defaults).get(cfg_band or band,
                                                                    p.default)))
                value = (self._stored_param_value(target, kind, c.id, p.name, cfg_band)
                         if idrec else
                         self._resolved_param_value(target, kind, c.id, p.name, cfg_band))
                field = self._config_field(target, kind, c.id, p.name)
                key = self._param_key(target, kind, c.id, p.name)
                id_hint, id_note = self._identity_note(idrec, value)
                row = self._param_row(p, field, kind, value, value, default,
                                      idrec is not None, c.name, key, c.id,
                                      identity_hint=id_hint, identity_note=id_note)
                by_group.setdefault(getattr(p, "group", "") or "", []).append(row)
            is_dep = c.id != main_id
            common = {"is_dep": is_dep, "optional": bool(c.optional),
                      "rule_before": False, "rule_after": False}
            default_rows = by_group.pop("", [])
            if default_rows:
                groups.append({"id": c.id, "name": c.name, "rows": default_rows, **common})
            for gname, grows in by_group.items():           # named sub-groups, stable order
                groups.append({"id": f"{c.id}::{gname}", "name": gname, "rows": grows, **common})
        # Rule a horizontal line BEFORE the first dependency-component group and AFTER the last one,
        # so the dependency components are visually bracketed off from the stack's main component.
        dep_idx = [i for i, g in enumerate(groups) if g["is_dep"]]
        if dep_idx:
            groups[dep_idx[0]]["rule_before"] = True
            groups[dep_idx[-1]]["rule_after"] = True
        # An OPTIONAL component's settings (e.g. the MeshCom GPS relay, KISS serial) are
        # additionally separated from the preceding group by their own rule.
        for i, g in enumerate(groups):
            if g["optional"] and i > 0:
                g["rule_before"] = True
        return groups

    def _param_ref(self, target: str, kind: str, key: str):
        """Resolve an API/CLI override key to (component, param, err). A `component_id.name` key is
        component-qualified; a bare key is the NAME and must be UNIQUE within the target's scope — a
        duplicated bare name is a TYPED error (the caller must qualify it). `err` is None on success."""
        comps = self._target_components(target)

        def _pget(c, nm):
            ps = (c.run_params if kind == "run"
                  else (c.config_file.params if c.config_file else ()))
            return next((p for p in ps if p.name == nm), None)

        if "." in key:
            cid, nm = key.split(".", 1)
            c = next((c for c in comps if c.id == cid), None)
            p = _pget(c, nm) if c is not None else None
            if p is None:
                return self._dep_param_ref(target, kind, key)
            return c, p, None
        owners = [(c, _pget(c, key)) for c in comps if _pget(c, key) is not None]
        if not owners:
            return self._dep_param_ref(target, kind, key)
        if len(owners) == 1:
            return owners[0][0], owners[0][1], None
        return None, None, (f"{kind} parameter {key!r} is declared by multiple components — qualify "
                            f"it as '<component>.{key}'")

    def _dep_param_components(self, target: str) -> list[tuple[str, object]]:
        """(dep_stack_id, component) pairs a STACK target's start pulls up from OTHER stacks
        (its run order, the daemon excluded — the daemon panel is its own channel): the
        confirm page shows their params and the start accepts component-qualified ephemeral
        overrides for them. [] for a non-stack target — a direct component run keeps its
        narrow scope."""
        if self.stack(target) is None:
            return []
        sid = self.stack_of(target)
        out: list[tuple[str, object]] = []
        seen: set[str] = set()
        for _, c in self._run_order(target):
            dsid = self.stack_of(c.id)
            if (not dsid or dsid == sid or self._is_daemon_target(dsid)
                    or c.id in seen or getattr(c, "test_fixture", False)):
                continue
            seen.add(c.id)
            out.append((dsid, c))
        return out

    def _dep_param_ref(self, target: str, kind: str, key: str):
        """Dependency-scope fallback for `_param_ref` (reached only when the target's OWN scope
        does not resolve the key): a STACK target's start also accepts overrides for the
        dependency components its run order pulls up (kiss under graywolf) — the qualified
        `component_id.name`, or a bare name unique among the dependencies. Operator ruling:
        starting one app or a stack of apps must offer the same control, so a value editable
        when starting kiss directly is editable when graywolf pulls kiss up."""
        deps = self._dep_param_components(target)

        def _pget(c, nm):
            ps = (c.run_params if kind == "run"
                  else (c.config_file.params if c.config_file else ()))
            return next((p for p in ps if p.name == nm), None)

        if "." in key:
            cid, nm = key.split(".", 1)
            c = next((c for _s, c in deps if c.id == cid), None)
            p = _pget(c, nm) if c is not None else None
            if p is None:
                return None, None, f"unknown {kind} parameter {key!r}"
            return c, p, None
        owners = [(c, _pget(c, key)) for _s, c in deps if _pget(c, key) is not None]
        if not owners:
            return None, None, f"unknown {kind} parameter {key!r}"
        if len(owners) == 1:
            return owners[0][0], owners[0][1], None
        return None, None, (f"{kind} parameter {key!r} is declared by multiple dependency "
                            f"components — qualify it as '<component>.{key}'")

    def _overrides_for_comp(self, target: str, kind: str, overrides, comp_id: str) -> dict:
        """The subset of ephemeral overrides (API-key form) that target `comp_id`, as {name: value}
        — so a qualified duplicate value overlays ONLY its named component and never a sibling."""
        out: dict = {}
        for k, v in (overrides or {}).items():
            if v is None:
                continue
            c, p, err = self._param_ref(target, kind, k)
            if err is None and c.id == comp_id:
                out[p.name] = v
        return out

    def _stored_param_value(self, target: str, kind: str, comp_id: str, name: str,
                            band: str = "") -> str:
        """Like `_resolved_param_value` but STORED-ONLY: "" when nothing was explicitly saved,
        never the (operator-substituted) default. Identity fields resolve through this so an
        inherited value is never mistaken for — or round-tripped into — a saved local one."""
        cfg_band = self._config_band(target, band)
        owner = self._owner_stack(target)
        use_band = "" if (cfg_band and name in _BANDLESS_STACK_PARAMS) else cfg_band
        stored = load_stack_config(self._paths, self._owner_stack_id(target), use_band)
        rc, fc = self._owner_param_counts(owner)
        k = "r" if kind == "run" else "f"
        count = (rc if kind == "run" else fc).get(name, 0)
        val, _amb = self._resolve_stored(stored, k, comp_id, name, count)
        return "" if val is None else str(val)

    def _resolved_param_value(self, target: str, kind: str, comp_id: str, name: str,
                              band: str = "") -> str:
        """The component's OWN persisted value (component-scoped, else a UNIQUE flat legacy) or its
        operator-substituted default — never masked by a same-named sibling."""
        cfg_band = self._config_band(target, band)
        owner = self._owner_stack(target)
        stored = load_stack_config(self._paths, self._owner_stack_id(target), cfg_band)
        if cfg_band and name in _BANDLESS_STACK_PARAMS:
            # STACK-LEVEL params live in the BAND-LESS file (see `save_config_bundle`). Read from
            # the banded file they resolve to their DEFAULT — which the console and `lhpc config`
            # then display, and which the next bundle save writes back as "at default -> remove",
            # silently clearing the switch while editing something unrelated.
            stored = load_stack_config(self._paths, self._owner_stack_id(target), "")
        rc, fc = self._owner_param_counts(owner)
        k = "r" if kind == "run" else "f"
        count = (rc if kind == "run" else fc).get(name, 0)
        val, _amb = self._resolve_stored(stored, k, comp_id, name, count)
        if val is not None:
            return str(val)
        _c, p, _e = self._param_ref(target, kind, f"{comp_id}.{name}")
        bd = dict(p.band_defaults).get(cfg_band or band, p.default) if p is not None else ""
        return self._op_subst(bd)

    def _param_default_canon(self, p, cfg_band: str, band: str) -> str:
        """The canonical (validated, operator-substituted) DEFAULT for a run/file param on a band —
        used by `save_config_bundle` to store OVERRIDES ONLY (a submitted value equal to this is not
        persisted, so it always follows the current/updated manifest default). Fail-soft: a
        non-validating default is compared as its raw substituted string."""
        eff = self._op_subst(dict(p.band_defaults).get(cfg_band or band, p.default))
        if p.kind == "int" and eff.strip() == "":
            return ""                                   # an unset optional int
        try:
            return str(validators.validate_param(p, eff))
        except validators.ValidationError:
            return eff

    # -- legacy-default migration (self-update) --------------------------------------------------
    # Pre-feature LHPC stored a run/file value verbatim under EVERY valid representation: the
    # component-SCOPED key (`__r__/__f__<comp>__<name>`, written even for a unique name saved through a
    # direct-component target) and, when the name is UNIQUE at owner-stack scope, the FLAT legacy key
    # (`name` / `file_<name>`). After a successful self-update, a value still equal to its canonical
    # PRE-UPDATE default must be removed so it adopts the new default — but only under the config lock,
    # conditionally, so a concurrent genuine override survives. Ambiguous flat names, `dp_*`, autostart
    # and manual scalars are never touched.

    @staticmethod
    def _canon_value(p, raw: str) -> str:
        """A stored value in canonical form for comparison against a canonical default; a value that
        fails validation compares as its raw string (fail-soft)."""
        try:
            return str(validators.validate_param(p, raw))
        except validators.ValidationError:
            return raw

    def _is_identity_candidate(self, cand: dict) -> bool:
        """Does this stale-default migration candidate name an identity field?

        Matched on the candidate's OWN (kind, comp, name) — the exact triple both this version and
        0.2.5 emit — so it cannot exempt an unrelated parameter that merely shares a name with an
        identity field on another component, and it is independent of how the stored key was
        spelled (scoped `__r__comp__name`, flat `name`, flat file `file_name`) or which band the
        config belongs to. The `key` fallback exists only for a candidate that predates those
        fields; it recognises all three spellings rather than assuming one."""
        stack_id = str(cand.get("stack") or "")
        if not stack_id:
            return False
        recs = self._identity_fields(stack_id)
        kind, comp, name = cand.get("kind"), cand.get("comp"), cand.get("name")
        if kind and comp and name:
            return any(("r" if r["kind"] == "run" else "f") == kind
                       and r["comp"] == comp and r["name"] == name for r in recs)
        key = str(cand.get("key") or "")
        if not key:
            return False
        for r in recs:
            k = "r" if r["kind"] == "run" else "f"
            if key in (self._scoped_key(k, r["comp"], r["name"]),
                       r["name"] if k == "r" else f"file_{r['name']}"):
                return True
        return False

    def _migration_candidates(self) -> list:
        """Snapshot every persisted run/file value that is SEMANTICALLY EQUAL to its canonical
        pre-update default, across all stacks/bands, in EVERY valid legacy representation (scoped for
        each component + unique flat). Each candidate carries enough to re-check + remove it safely
        later: {stack, band, key, kind, comp, name, expected(=canonical default)}. Genuine overrides
        (including intentional empty ones) are excluded; ambiguous flat names are skipped."""
        out: list = []
        for stack in self.stacks():
            run_counts, file_counts = self._owner_param_counts(stack)
            for band in (self.stack_bands(stack.id) or ("",)):
                cfg_band = self._config_band(stack.id, band)
                stored = load_stack_config(self._paths, stack.id, cfg_band)
                if not stored:
                    continue
                for c in stack.components:
                    items = ([("r", p) for p in c.run_params]
                             + [("f", p) for p in (c.config_file.params if c.config_file else ())])
                    for kind, p in items:
                        # IDENTITY params are NEVER stale-default candidates: their only
                        # "default" is empty (= inherit), and a stored value equal to the
                        # substituted {callsign} is a DELIBERATE local pin (fix persisted by
                        # save_config_bundle's identical carve-out) — snapshotting it here
                        # would silently revert the stack to inherit at the next self-update
                        # (audit-found).
                        if getattr(p, "validator", "") in self._IDENTITY_ENFORCE:
                            continue
                        default = self._param_default_canon(p, cfg_band, band)
                        keys = [self._scoped_key(kind, c.id, p.name)]     # scoped: valid for any comp
                        counts = run_counts if kind == "r" else file_counts
                        if counts.get(p.name, 0) <= 1:                    # unique -> flat legacy too
                            keys.append(p.name if kind == "r" else f"file_{p.name}")
                        for key in keys:
                            if key in stored and self._canon_value(p, str(stored[key])) == default:
                                out.append({"stack": stack.id, "band": cfg_band, "key": key,
                                            "kind": kind, "comp": c.id, "name": p.name,
                                            "expected": default})
        return out

    @staticmethod
    def _stamp(candidates: list, from_head: str) -> list:
        """Attach the live transition's `from_head` provenance to freshly-snapshotted candidates so
        their pre-update default can later be proven from source."""
        return [{**c, "from_head": from_head} for c in candidates]

    def _prove_candidate(self, cand: dict, from_head: str):
        """Prove `cand` against a PROVEN transition whose pre-update source is at `from_head` — the
        TRANSITION RECORD's from_head (validated against the actual checkout by the classifier), NEVER
        the candidate's own `from_head` field, which must not independently select a manifest. Returns
        `(old_param, old_default_canon)` derived from the OLD (pre-update) manifest — used for BOTH
        sides of the old-default comparison — or None (→ keep pending, never delete) when the
        transition manifest is untracked/unreadable OR the parameter/band is not still safely
        identifiable + owned in the CURRENT manifest (current metadata is a safety gate only)."""
        import tomllib
        from pathlib import Path

        from . import manifest as manifest_mod
        from . import selfupdate
        root = selfupdate.repo_root()
        if root is None:
            return None
        man = self._manifest_path or manifest_mod.default_manifest_path()
        try:
            rel = Path(man).resolve().relative_to(root.resolve())        # manifest must be tracked here
        except (ValueError, OSError):
            return None
        kind, name = cand["kind"], cand["name"]

        def _params(comp):
            return comp.run_params if kind == "r" else (comp.config_file.params if comp.config_file else ())

        def _param_in(stacks, comp_id):
            st = next((s for s in stacks if s.id == cand["stack"]), None)
            comp = next((c for c in st.components if c.id == comp_id), None) if st else None
            return (st, comp, next((p for p in _params(comp) if p.name == name), None) if comp else None)

        # Current-manifest SAFETY gate — a FRESH parse of the POST-UPDATE source tree (NEVER
        # `self._stacks`, which may have been populated before the transition). The parameter must be
        # still safely identifiable + owned + its band still valid in the current manifest, else the
        # candidate is retained pending (removed / renamed / unreconcilable -> never delete).
        try:
            with open(man, "rb") as fh:
                cur_stacks = manifest_mod.parse_manifest(tomllib.load(fh))
        except Exception:
            return None
        cur_stack, _cur_comp, cur_p = _param_in(cur_stacks, cand["comp"])
        if cur_stack is None or cur_p is None:
            return None
        cur_bands = next((c.bands for c in cur_stack.components if c.bands), ())
        if cand["band"] not in (set(cur_bands) | {""}):
            return None

        # Authoritative OLD manifest at the proven `from_head` — used for old-default SEMANTICS.
        r = self._system.runner.run(["git", "-C", str(root), "show",
                                     f"{from_head}:{rel.as_posix()}"], timeout=5.0)
        if getattr(r, "not_found", False) or r.returncode != 0:
            return None
        try:
            old_stack = next((s for s in manifest_mod.parse_manifest(tomllib.loads(r.stdout))
                              if s.id == cand["stack"]), None)
        except Exception:
            return None
        if old_stack is None:
            return None
        # OLD-manifest key eligibility (mirrors legacy candidate generation):
        #  * a SCOPED key must map to that EXACT old component + parameter;
        #  * a FLAT key is eligible ONLY when the name was UNIQUE for its kind across the OLD owner
        #    stack — an ambiguous/absent flat name is never proven (kept pending).
        is_flat = cand["key"] == (name if kind == "r" else f"file_{name}")
        if is_flat:
            run_counts, file_counts = self._owner_param_counts(old_stack)
            if (run_counts if kind == "r" else file_counts).get(name, 0) != 1:
                return None                                              # ambiguous / absent flat name
            old_comp = next((c for c in old_stack.components if any(p.name == name for p in _params(c))), None)
            if old_comp is None or old_comp.id != cand["comp"]:          # comp must be the unique declarer
                return None
        else:
            old_comp = next((c for c in old_stack.components if c.id == cand["comp"]), None)
            if old_comp is None:
                return None
        old_p = next((p for p in _params(old_comp) if p.name == name), None)
        if old_p is None:
            return None
        return old_p, self._param_default_canon(old_p, cand["band"], cand["band"])

    def _config_ambiguity(self, target: str, order, band: str = ""):
        """A message naming the first AMBIGUOUS legacy value a started component would rely on — a
        run/file param name declared by >= 2 owner-stack components, stored as a flat legacy value,
        with no component-scoped value for that component. None when every started component resolves
        unambiguously. Used to fail a start TYPED (never silently apply a value to the wrong
        component). `order` is the resolved [(stack, comp), …] launch order."""
        owner = self._owner_stack(target)
        if owner is None:
            return None
        stored = load_stack_config(self._paths, self._owner_stack_id(target),
                                   self._config_band(target, band))
        run_counts, file_counts = self._owner_param_counts(owner)
        owner_comp_ids = {c.id for c in owner.components}
        for _stack, comp in order:
            if comp.id not in owner_comp_ids:
                continue                                     # a dependency from another stack
            for p in comp.run_params:
                _v, amb = self._resolve_stored(stored, "r", comp.id, p.name,
                                               run_counts.get(p.name, 0))
                if amb:
                    return (f"run parameter '{p.name}' is ambiguous — declared by more than one "
                            f"component and stored only as a flat legacy value; set a "
                            f"component-scoped value for '{comp.id}'")
            for p in (comp.config_file.params if comp.config_file else ()):
                _v, amb = self._resolve_stored(stored, "f", comp.id, p.name,
                                               file_counts.get(p.name, 0))
                if amb:
                    return (f"file parameter '{p.name}' is ambiguous — declared by more than one "
                            f"component and stored only as a flat legacy value; set a "
                            f"component-scoped value for '{comp.id}'")
        return None

    def stack_config(self, target: str, band: str = "") -> dict:
        """Effective run config for `target` (and band): the component-scoped saved value, else a
        UNIQUE flat legacy value, else the per-band/manifest default (operator `{callsign}`
        tokens substituted in DEFAULTS only — a saved value is used verbatim). An
        ambiguous flat legacy value is NOT applied here (the start blocks; see `_config_ambiguity`)."""
        cfg_band = self._config_band(target, band)
        owner = self._owner_stack(target)
        stored = load_stack_config(self._paths, self._owner_stack_id(target), cfg_band)
        run_counts, _ = self._owner_param_counts(owner)
        op = self.config().operator

        def _op_subst(v: str) -> str:
            return str(v).replace("{callsign}", op.callsign or "")

        out = {}
        for c in self._target_components(target):
            for p in c.run_params:
                val, _amb = self._resolve_stored(stored, "r", c.id, p.name,
                                                 run_counts.get(p.name, 0))
                if val is not None:
                    out[p.name] = str(val)                    # saved value (scoped/unique), verbatim
                else:
                    bd = dict(p.band_defaults).get(cfg_band or band, p.default)
                    out[p.name] = _op_subst(bd)               # default with operator tokens
        return out

    def gui_unavailable_components(self, stack) -> tuple:
        """Component ids of `stack` that CANNOT work here because a GUI-only dependency of theirs is
        absent. GET-safe (composes `missing_requirements` only).

        This is the ONE predicate behind every GUI skip — auto-install planning and direct `lhpc
        build` both consult it BEFORE locks, markers or steps, so an unavailable GUI component is
        never discovered by letting pkg-config or an import fail mid-build."""
        out = []
        life = self._lifecycle()
        for c in stack.components:
            missing = life.missing_requirements(c)
            if any(getattr(r, "gui", False) for r in missing):
                out.append(c.id)
        return tuple(out)

    def gui_fallback_active(self, stack) -> bool:
        """True when `stack`'s MAIN component cannot run on THIS box because its GUI toolkit or
        a display is absent — the ONE condition under which an interactive terminal FALLBACK
        sibling may be offered.

        Where the GUI main IS usable it owns the shared configuration and the exclusive audio
        device, so the fallback is never presented, marked or pre-stepped: desktop behaviour
        stays exactly what it was before the fallback existed. Start and `_all_components_healthy`
        both consult this, so the two can never disagree about whether the fallback exists here."""
        main = next((c for c in stack.components if c.id == stack.main), None)
        if main is None or not main.gui_optional:
            return False
        if any(getattr(r, "gui", False)
               for r in self._lifecycle().missing_requirements(main)):
            return True
        return self.needs_display(main) and not self.display_available()

    def gui_skipped_stack(self, stack) -> bool:
        """True when a MANDATORY component of the stack is GUI-unavailable — the whole stack is then
        recorded skipped. An OPTIONAL one (meshcore-nodegui) only removes itself: MeshCore stays
        fully usable headless through its CLI."""
        skip = set(self.gui_unavailable_components(stack))
        return any(c.id in skip and not (c.optional or c.gui_optional)
                   for c in stack.components)

    def missing_system_deps(self, target: str) -> list[dict]:
        """Unsatisfied INSTALL-time system dependencies (e.g. -dev packages) for a stack's components,
        with the command to install each. Empty = all satisfied. Run-time capabilities (`runtime`, e.g.
        group membership) are EXCLUDED — they gate start, not install/build, so they never block the
        install gate; they still surface in `system_deps`."""
        return [d for d in self.system_deps(target)
                if not d["satisfied"] and not d["runtime"] and not d.get("provisioned")]

    def install_dep_gate(self, target: str) -> dict:
        """Split the unsatisfied INSTALL-time deps of a stack into a hard-block set and a warn-only set.

        `block` = missing deps of a MANDATORY (non-optional) component — install must not proceed until
        they are satisfied. `warn` = missing deps of an OPTIONAL component — advisory only, the operator
        may proceed. Run-time capabilities (groups) are excluded from both (they gate start, not install).
        The SINGLE classifier reused by the CLI, web and auto-install gates. GET-safe (no subprocess)."""
        missing = self.missing_system_deps(target)
        # Unknown optionality defaults to mandatory (fail-safe: block rather than silently skip).
        return {"block": [d for d in missing if d.get("mandatory", True)],
                "warn": [d for d in missing if not d.get("mandatory", True)]}

    def system_deps(self, target: str) -> list[dict]:
        """ALL declared system requirements for a stack (dev packages, headers,
        device nodes) with their satisfied state + install command — for the app
        tab ('Installed' vs a copyable install command) and the install gate.
        `mandatory` = declared by a component that is BOTH non-optional and non-GUI. `gui` = EVERY
        declaration marks it GUI-only (core wins). Both are derived from all declarations after the
        sweep, so neither depends on manifest declaration order."""
        life = self._lifecycle()
        s = self.stack(target)
        out, by_key, decls = [], {}, {}
        for c in (s.components if s else ()):
            missing = life.missing_requirements(c)
            for req in c.requires:
                key = req.install or req.cmd or req.check_file or req.absent_file
                if not key:
                    continue
                # CONDITIONAL soft dependency: `gpsd` is only real when the configured
                # position source is a gpsd on THIS box. Dropped entirely otherwise — a
                # remote-gpsd operator has nothing to install here, and surfacing it made the
                # install/start gate demand a package the box does not need.
                if getattr(req, "gps", False) and not self._local_gpsd_needed():
                    continue
                is_gui = bool(getattr(req, "gui", False))
                # ORDER-INDEPENDENT MERGE: collect the raw declaration facts here and derive
                # `gui` / `mandatory` from ALL of them after the sweep (below). Deriving them
                # incrementally made the verdict depend on which component declared the dep first.
                decls.setdefault(key, []).append((is_gui, bool(c.optional)))
                if key in by_key:
                    continue
                sat = req not in missing
                # Groups req: append the STATE-specific hint (never both). "granted, restart pending"
                # (configured but not yet effective) suppresses the usermod command — re-granting is not
                # the fix; "not a member" keeps it. (Still runtime=True, so never in the install gate.)
                pending = (not sat) and bool(req.groups) and life.group_grant_pending(req)
                what = req.note or req.cmd or req.check_file or req.absent_file
                if req.groups and not sat:
                    what = f"{what} — {GROUP_RESTART_HINT if pending else GROUP_MISSING_HINT}"
                entry = {"what": what,
                         "install": "" if pending else req.install,
                         "satisfied": sat,
                         # run-time capabilities (group membership AND a must-not-run service) gate start,
                         # not install -> excluded from the install gate, still surfaced here.
                         "runtime": bool(req.groups or req.absent_file),
                         # GUI-ONLY: opt-in by design, so it is WARN-only in the install gate —
                         # visible and actionable, but a headless box is not "missing a mandatory
                         # dependency" for lacking a toolkit it can never use.
                         "gui": is_gui,
                         # a MANAGED tool provisioned into the runtime root by the build/setup step does
                         # not exist until `lhpc build`, so it must NOT block install (still gates start).
                         "provisioned": bool(req.provisioned),
                         "mandatory": False}                # derived below from ALL declarations
                by_key[key] = entry
                out.append(entry)
        # CORE WINS, order-independently: a dep is GUI-only only when EVERY declaration marks it gui
        # (AND-merge), and it is mandatory only when some declaration is BOTH non-optional AND
        # non-GUI. One plain declaration therefore restores full mandatory semantics no matter where
        # it appears in the manifest.
        for key, entry in by_key.items():
            ds = decls.get(key, ())
            entry["gui"] = bool(ds) and all(g for g, _o in ds)
            entry["mandatory"] = any((not g) and (not o) for g, o in ds)
        return out

    def config_view(self, target: str, band: str = "") -> dict:
        """Structured config for the Config page: operator identity (always shown
        on top) plus per-component run parameters and their effective values. For
        band-switchable stacks `band` selects which band's config is shown."""
        s = self.stack(target)
        members = s.components if s else ()
        comps = [c for c in members if c.run_params]
        # The daemon's run params (radio/debug/tx-mode/CAD/RSSI) are START options,
        # always chosen on confirm:start — not persistent config. Keep the daemon
        # Config page to its live tuning + sources only.
        if s is not None and s.main == self.DAEMON_ID:
            comps = []
        cfg = self.config()
        stored = load_stack_config(self._paths, target)
        live = {cid: st.run_state for ss in self.build_snapshot().stacks
                for cid, st in ss.components.items()}
        up = (RunState.RUNNING, RunState.DEGRADED)
        # Libraries/firmware are build/flash artifacts — never "started", so they
        # get no autostart toggle or Run button.
        # A production GPS feed is NOT an operator start choice: whether it runs is decided by
        # the stack's `use_gps` switch plus the global source, and starting one directly is
        # refused ("the current position plan does not use it"). Offering a checkbox let the
        # operator contradict the position plan, and showed a second GPS entry next to the
        # fixture on the confirm page.
        optional = [{"id": c.id, "name": c.name, "purpose": c.purpose,
                     "autostart": stored.get(f"autostart_{c.id}") == "on",
                     "running": live.get(c.id) in up,
                     # False -> listed, but run on demand (no auto-start control)
                     "startable": not self._never_operator_autostart(c)}
                    for c in members if self._optional_listed(c)]
        main = s.main_component if s else None
        # The GLOBAL operator callsign is edited ONLY on its own card (LHPC main card) and
        # via `lhpc config operator` — never through a stack page, which made a stack look
        # like the owner of a global setting. Stack pages carry the per-stack identity
        # params themselves (with inheritance shown truthfully by the identity rows).
        sources = [{"id": c.id, "name": c.name,
                    "remote": cfg.remotes.get(c.id) or c.source.remote,
                    "default": c.source.remote,
                    "overridden": c.id in cfg.remotes}
                   for c in members if c.source and c.source.remote]
        bands = self.stack_bands(target)
        cfg_band = self._config_band(target, band)
        view = {
            # Each component carries its OWN field-name map (`fields`) and its OWN
            # component-scoped value map (`values`) so duplicate run/file names across components
            # never share a form field nor flatten into one value.
            "components": [{"id": c.id, "name": c.name, "params": rparams,
                            "fields": {p.name: self._config_field(target, "run", c.id, p.name)
                                       for p in rparams},
                            "values": {p.name: self._resolved_param_value(target, "run", c.id,
                                                                          p.name, cfg_band)
                                       for p in rparams}}
                           for c in comps
                           for rparams in (self._form_run_params(c),) if rparams],
            "optional": optional,
            "sources": sources,
            # File params GROUPED by component (like run params) — each with its own fields/values.
            "file_components": [{"id": c.id, "name": c.name,
                                 "params": [p for p in c.config_file.params if not p.hidden],
                                 "fields": {p.name: self._config_field(target, "file", c.id, p.name)
                                            for p in c.config_file.params},
                                 "values": {p.name: self._resolved_param_value(target, "file", c.id,
                                                                               p.name, cfg_band)
                                            for p in c.config_file.params}}
                                for c in members if c.config_file
                                and any(not p.hidden for p in c.config_file.params)],
            "file_params": [p for c in members if c.config_file
                            for p in c.config_file.params if not p.hidden],
            "file_values": self.file_config_values(target, cfg_band),
            "bands": list(bands),       # allowed bands ([] = single-band stack)
            "band": cfg_band,           # the band currently being edited
            "values": self.stack_config(target, cfg_band),
            "running": bool(main and live.get(main.id) in up),
            "radios": [],
        }
        # GPS is GLOBAL, but it is shown on EVERY stack that can use a position — beside that
        # stack's own `use_gps` switch. Putting it only on the daemon page (where it first
        # lived) meant an operator turning Meshtastic's GPS on had no way to reach the source
        # that switch depends on, and the daemon does not use a position at all. Every copy
        # edits the SAME setting and says so, so they cannot contradict each other.

        # The daemon's config page carries its LIVE (runtime) settings for ONE band,
        # chosen by a 433/868 switch at the top. The repo/RadioLib remotes + save/
        # restore below apply to both bands.
        if s is not None and s.main == self.DAEMON_ID:
            # The Hardware subsection: the real hardware-setup selector. Always shown (even when
            # unconfigured) so a fresh box can be set up here.
            view["hardware"] = self.hardware_setup()
            view["hardware_configured"] = self.hardware_configured()
            view["hw_setups"] = self.hw_setups()
            active = self.active_bands()
            if active:                                   # only a configured setup has tunable radios
                live_band = band if band in active else active[0]
                view["bands"] = list(active)             # only the setup's radios are tunable
                view["band"] = live_band
                view["live_band"] = True       # band switch selects the LIVE band, not config
                dv = self.daemon_view(live_band)
                # Populate each control with the daemon's REAL current value: STATUS + CHANNEL are
                # what it actually reports; the configured daemon-param value is the fallback for
                # radio params the daemon does not echo (FREQ/SF/BW/…).
                actual = {**dv.channel, **dv.status}
                cfg = self._daemon_param_applies("daemon", live_band) if dv.reachable else {}
                view["radios"] = [{"band": live_band, "reachable": dv.reachable,
                                   "error": dv.error, "status": actual, "config": cfg}]
                # Order the live per-parameter controls the same as the daemon-params panel
                # below (shared keys in that order; any daemon-only extras after).
                from . import daemon_params
                allowed = daemon_control.allowed_settings()
                order = ([k for k in daemon_params.ALL_PARAMS if k in allowed]
                         + [k for k in allowed if k not in daemon_params.ALL_PARAMS])
                view["live_settings"] = {k: allowed[k] for k in order}
        return view

    @invalidates_snapshot
    def save_config(self, target: str, values: dict,
                    band: str = "") -> ActionResult:
        """Save the stack's run parameters (via `save_config_bundle`). The global operator
        callsign is NOT settable here — `set_operator_identity` is the one authoritative
        mutation path (it alone computes and commits the restart-required markers
        atomically with the change)."""
        return self.save_config_bundle(target, values=values, band=band)

    @invalidates_snapshot
    def save_config_bundle(self, target: str, *, values: dict | None = None,
                           band: str = "", remotes: dict | None = None,
                           _allow_managed_params: frozenset = frozenset(),
                           _identity_guard: bool = False) -> ActionResult:
        """Validate the WHOLE Config-page submission, then persist it as ONE
        all-or-recoverable transaction (local.toml + the per-stack config file).
        Nothing is written unless every value validates; unknown fields are
        rejected; a malformed local.toml is preserved. (P0: replaces the previous
        per-remote sequential writes.)

        `_allow_managed_params` is an INTERNAL trust token: HMAC-managed run params (e.g.
        `password_file`) are rejected from generic submissions and may be written ONLY by the managed
        path (`hmac_set_secret`) which lists them here — this is what keeps generic config from clearing
        the override and silently restoring open auth."""
        owner = self._owner_stack(target)
        if owner is None:
            return self._unknown_stack(target)
        # A direct COMPONENT target persists into its OWNER stack config, but may edit ONLY its own
        # run/file fields — never sibling components' fields, remotes, or autostart (those are
        # whole-stack concerns, allowed only for a stack target).
        is_stack_target = self.stack(target) is not None
        sid = owner.id
        errors: list[str] = []
        optional_ids = ({c.id for c in owner.components if c.optional} if is_stack_target else set())
        # Each submitted value carries component identity: a run value key is the API key
        # (`name`/`component.name`); a file value key is `file_<apikey>`. `_param_ref` resolves it to
        # (component, param) and REJECTS an unqualified duplicate — so colliding names never flatten.
        clean_params: list = []      # (kind 'r'|'f', component, param, value)
        clean_auto: dict = {}        # autostart_<id> -> "on"/""  (stack target only, flat)
        # `_param_ref` also resolves DEPENDENCY components' params (the start-override channel);
        # a persisted config write must stay in the component's OWN stack store, so anything the
        # dependency fallback resolved is refused here with a pointer to the right stack.
        own_ids = {c.id for c in self._target_components(target)}

        def _own(c, key):
            if c.id in own_ids:
                return True
            errors.append(f"{key!r} belongs to dependency component '{c.id}' — save it on its "
                          f"own stack ('{self.stack_of(c.id)}')")
            return False
        for key, value in (values or {}).items():
            if key.startswith("autostart_"):
                if key[len("autostart_"):] in optional_ids:
                    clean_auto[key] = "on" if str(value) in ("on", "1", "true", "yes") else ""
                else:
                    errors.append(f"unknown config field: {key!r}")
                continue
            if key.startswith("file_"):
                c, p, err = self._param_ref(target, "file", key[len("file_"):])
                if err:
                    errors.append(f"unknown config field: {key!r}" if err.startswith("unknown")
                                  else err)
                    continue
                if not _own(c, key):
                    continue
                vf = str(value)
                if vf.strip() == "":
                    # BLANK = "clear this override / use the default" — never validated
                    # as a literal value (an empty txpower/frequency is not an error).
                    clean_params.append(("f", c, p, vf))
                    continue
                try:
                    clean_params.append(("f", c, p, validators.validate_param(p, vf)))
                except validators.ValidationError as exc:
                    errors.append(str(exc))
                continue
            c, p, err = self._param_ref(target, "run", key)
            if err:
                errors.append(f"unknown config field: {key!r}" if err.startswith("unknown") else err)
                continue
            if not _own(c, key):
                continue
            if self._is_hmac_managed_param(c, p) and p.name not in _allow_managed_params:
                errors.append(self._HMAC_MANAGED_PARAM_MSG)     # never clearable via generic config
                continue
            v = str(value)
            if v.strip() == "":
                # BLANK = clear the override (any kind), same rule as file params above.
                clean_params.append(("r", c, p, v))
                continue
            try:
                clean_params.append(("r", c, p, validators.validate_param(p, v)))
            except validators.ValidationError as exc:
                errors.append(str(exc))
        # The global operator callsign is NOT settable through a stack save — the one
        # authoritative mutation path is `set_operator_identity` (atomic restart-marker
        # handling); routing it through here bypassed that (audit-found). So this save only ever
        # patches `[remotes]` in local.toml.
        # A remote submission is a PATCH for THIS stack's own source components only (enforced in
        # the service, not the web form): validated non-blank -> set, blank -> clear that
        # component's override. A component id not declared by `target` (unknown, another stack's,
        # or one without a source remote) is REJECTED. Other stacks' overrides are untouched.
        stack_remote_cids = ({c.id for c in owner.components if c.source and c.source.remote}
                             if is_stack_target else set())
        remote_patch: dict = {}
        if remotes is not None:
            for cid, url in remotes.items():
                try:
                    vid = validators.path_component(cid, field="component id")
                except validators.ValidationError as exc:
                    errors.append(str(exc))
                    continue
                if vid not in stack_remote_cids:
                    errors.append(f"remote override not allowed for {vid!r} — not a source "
                                  f"component of '{target}'")
                    continue
                try:
                    remote_patch[vid] = validators.remote_url(url or "", field="remote")   # "" clears
                except validators.ValidationError as exc:
                    errors.append(str(exc))
        # ONE remote per shared checkout: a submission giving two components of the same
        # source path DIFFERENT remotes is rejected whole; a coherent value is expanded
        # ATOMICALLY to every declarer of that path (explicitly disclosed), so divergence
        # can never be saved — not even for consumers in other stacks.
        remote_notes: list = []
        if remote_patch:
            comp_index = {c.id: c for st in self.stacks() for c in st.components}
            by_path: dict = {}
            for vid, vurl in remote_patch.items():
                c = comp_index.get(vid)
                if c is None or c.source is None:
                    continue
                by_path.setdefault(c.source.path, {})[vid] = vurl
            for pth, vals in by_path.items():
                if len(set(vals.values())) > 1:
                    errors.append(f"conflicting remotes submitted for shared source {pth!r} "
                                  f"({', '.join(sorted(vals))}) — one checkout has ONE remote")
                    continue
                url = next(iter(vals.values()))
                group = [d.id for d in self._path_declarers(pth)]
                extra = sorted(set(group) - set(vals))
                for did in group:
                    remote_patch[did] = url
                if extra:
                    remote_notes.append(f"shared checkout {pth}: the same remote was applied "
                                        f"to {', '.join(extra)}")
        if errors:                                  # reject the whole bundle — zero mutation
            return ActionResult(False, f"Config not saved for '{target}'.", details=errors)

        targets: list = []
        local_path = self._paths.runtime_root / "config" / "local.toml"
        if remotes is not None:
            def _render_local(p, patch=remote_patch):
                # Read the LATEST local.toml INSIDE the transaction lock and MERGE — preserving
                # every unrelated table and every other component's remote override. A malformed
                # local.toml raises here -> the transaction rolls back and preserves it.
                existing = _load_runtime_toml(self._paths, local_path)   # no-follow; ConfigError on corrupt
                data = dict(existing)                     # keep root scalars + every other table
                # Patch owned component keys only (None clears); other remotes preserved. A
                # non-table `remotes` value is rejected here -> transaction rollback.
                _patch_local_table(data, "remotes",
                                   {vid: (vurl or None) for vid, vurl in patch.items()})
                return render_local_tables(data)          # type-safe, preserves root scalars
            targets.append(("local", local_path, _render_local, 0o600))
        cfg_band = self._config_band(target, band)
        # Apply-mode hints (restart/build) come from the SAME change set the marker decision
        # computes inside the transaction — one identity-aware comparison instead of two that
        # could disagree (review-found: an identity no-op said "Restart the stack to apply" while
        # the marker correctly decided nothing had changed). Filled in by `_render_marker`.
        modes: set = set()
        _live_seen: dict = {}
        # OVERRIDES-ONLY persistence: a value equal to its CURRENT default is NOT stored, so it always
        # follows the current/updated manifest default (and a self-update that changes a default takes
        # effect for values still at the old default) — exactly what daemon params already do. A value
        # that DIFFERS from the default is stored (even if empty — a genuine "unset" override).
        # Persisted-key form: a DIRECT component target, or a DUPLICATED stack-target name, is stored
        # COMPONENT-SCOPED (`__r__/__f__<comp>__<name>`); a UNIQUE stack-target name keeps its FLAT
        # legacy key. Autostart (stack-only): "on" is an override; "" (off) is the default -> cleared.
        dup_run, dup_file = self._dup_names(target)

        def _store_key(kind, c, p):
            dup = dup_run if kind == "r" else dup_file
            if is_stack_target and p.name not in dup:
                return p.name if kind == "r" else f"file_{p.name}"          # unique -> flat legacy
            return self._scoped_key(kind, c.id, p.name)                     # scoped

        to_set: dict = {}
        to_remove: set = set()
        auto_set: dict = {}
        auto_remove: set = set()
        for k, av in clean_auto.items():                                     # autostart
            # Autostart is a STACK-LEVEL flag: it must live in the BAND-LESS stack file —
            # `_run_order` reads it band-independently. (Live finding: stored in the
            # band-suffixed file, the option never took effect for band-switchable
            # stacks like kiss.)
            if av == "on":
                auto_set[k] = av
            else:
                auto_remove.add(k)
        for kind, c, p, v in clean_params:
            key = _store_key(kind, c, p)
            # IDENTITY params: the only "default" is EMPTY (= inherit/required). Comparing a
            # submitted callsign against the operator-substituted {callsign} default made
            # saving a local value IDENTICAL to the global silently un-save it — breaking
            # the documented migration (set the SSID per stack, then change the global to
            # its base) whenever the legacy global equalled the value being localized.
            ident = getattr(p, "validator", "") in self._IDENTITY_ENFORCE
            canon = "" if ident else self._param_default_canon(p, cfg_band, band)
            if str(v) == canon:
                to_remove.add(key)                                          # at default -> not persisted
            else:
                to_set[key] = v                                             # override -> persisted
        # `use_gps` is a STACK-LEVEL switch, exactly like autostart above: "does this box report
        # its position" is a property of the stack, not of the band it happens to be on. Stored
        # per band it silently reverted on a band change — and the band-less read then saw
        # nothing at all, so the switch did nothing. (Live-found on the Zero: the CLI wrote
        # `use_gps` into `meshtastic@868.toml` while every GPS decision read `meshtastic.toml`.)
        # Routed here, in the canonical bundle path, so the CLI, the console and the API all
        # agree — an intercept on any single surface only fixes that surface.
        #
        # A COMPONENT target (`lhpc config meshcom-qemu use_gps on`) would otherwise store the
        # component-scoped `__r__meshcom-qemu__use_gps`, which no GPS reader looks at — the
        # switch would appear to save and then do nothing. It is one switch per STACK, so it is
        # normalized to the owner stack's FLAT band-less key whichever target names it.
        _GPS_SW = "use_gps"
        _gps_now, _want = False, ""
        for _k in [k for k in to_set if k == _GPS_SW or k.endswith(f"__{_GPS_SW}")]:
            # The WANTED state comes from the VALUE, never from which bucket the key landed in.
            # Reading it as "in to_set => on" made `use_gps=""` — an override that differs from
            # the default and so lands in to_set — look like "on": it matched the current "on",
            # was seen as no change, skipped the running-stack refusal, and then disabled GPS,
            # because every reader compares against the literal "on".
            _gps_now = True
            _want = "on" if str(to_set.pop(_k)).strip().lower() == "on" else "off"
        # The switch's default comes from the MANIFEST ("on" since the source gained `auto`) —
        # hardcoding "off" here was a fourth copy of the drifted literal: submitting the default
        # value landed in `to_remove`, was read back as "off", looked like a CHANGE, and the
        # running-stack refusal fired on a save that changed nothing.
        from .gps import use_gps_default
        _gps_default = use_gps_default(self.stacks(), sid) if sid else "off"
        for _k in [k for k in to_remove if k == _GPS_SW or k.endswith(f"__{_GPS_SW}")]:
            to_remove.discard(_k)
            _gps_now, _want = True, _gps_default          # removing the key = back to default
        if _gps_now:
            # Store the CANONICAL value: only a deviation FROM THE DEFAULT is an override;
            # the default itself is cleared, so the file never holds a third state.
            if _want != _gps_default:
                auto_set[_GPS_SW] = _want
                auto_remove.discard(_GPS_SW)
            else:
                auto_remove.add(_GPS_SW)
                auto_set.pop(_GPS_SW, None)
        # Flipping the switch under a RUNNING stack would leave its feed, its resource claims and
        # its generated config describing a different plan than the one that launched — the same
        # reason the global source is locked while in use. Refused BEFORE anything is written;
        # rechecked authoritatively inside the transaction (`_gps_recheck`), because between here
        # and the write a concurrent start can bring the stack up.
        if _gps_now and _want != ("on" if self.gps_enabled_for(sid) else "off"):
            # Same rule as the authoritative recheck below, so the fast refusal and the one under
            # the lock can never disagree about what counts as "in use". Cheap read here — the
            # forced-fresh recompute belongs inside the transaction, not on the fast path.
            _live = self.gps_liveness_blockers([sid], snap=self._snapshot_or_none())
            if _live:
                return ActionResult(
                    False, f"Config not saved for '{target}': cannot change use_gps while "
                           f"{', '.join(_live)} {'is' if len(_live) == 1 else 'are'} running.",
                    details=["  its feed, claims and generated config came from the "
                             "CURRENT setting"],
                    next_commands=[f"lhpc stack stop {sid}"])

        def _gps_recheck(_pth=None):
            """AUTHORITATIVE recheck, run inside the exclusive config transaction.

            The pre-check above reads state before the lock is taken, so a start that begins in
            between would have its feed, claims and generated config derived from the OLD switch
            while the new one lands underneath it. Here the config lock is held, the saved value
            is re-read from the file being merged, and the running probe is FORCED FRESH — so a
            concurrent start that has completed is seen. Fail-closed: if the probe cannot answer,
            the save is refused rather than assumed safe. Raising rolls the transaction back.
            """
            if not _gps_now:
                return
            try:
                _cur = str(_load_runtime_toml(
                    self._paths, _stack_config_path(self._paths, sid, "")
                ).get(_GPS_SW, "")).strip().lower() or _gps_default
            except (OSError, ValueError, KeyError, ConfigError) as exc:
                raise ConfigError(f"could not re-read the saved use_gps for '{sid}': {exc}") from exc
            if _cur == _want:
                return                              # not a change -> nothing to serialize against
            # ONE fresh, strict snapshot — never a snapshot cached before the lock — judged over
            # the stack's CONSUMER and FEED components. Checking only the stack's main component
            # let `use_gps=off` succeed while `meshcom-gps` was running on its own, orphaning a
            # feed that holds the receiver claim.
            _blockers = self.gps_liveness_blockers([sid], snap=self._gps_fresh_snapshot())
            if _blockers:
                raise ConfigError(
                    f"cannot change use_gps while {', '.join(_blockers)} "
                    f"{'is' if len(_blockers) == 1 else 'are'} running — its feed, claims and "
                    f"generated config came from the CURRENT setting (lhpc stack stop {sid})")
        # The stack file is written as a MERGE rendered INSIDE the transaction lock: overlay the
        # override keys (keeping daemon-profile dp_*, other bands + unrelated manual scalars), then
        # drop the at-default keys. A raise here (unsupported manual value) rolls the transaction back.
        def _render_stack(pth, tgt=sid, b=cfg_band, setv=to_set, rmv=to_remove):
            # AUTHORITATIVE RECHECK, inside the transaction (audit-found race: the caller's
            # accept/refuse decision was taken before this lock, so a concurrent global-identity
            # clear could land in between and the submission was persisted and only THEN refused).
            # Raising here rolls the whole transaction back — nothing is persisted.
            if _identity_guard:
                # THE authoritative identity decision, re-taken INSIDE this transaction: a
                # concurrent `set_operator_identity` may have cleared the global a blank was about
                # to inherit, and refusing here rolls the whole submission back rather than
                # persisting first and refusing after (audit-found).
                self._invalidate_config()          # the COMMITTED global, never a cached one
                if (_again := self.identity_refusal_for_values(target, band, values or {})):
                    raise ConfigError(_again.summary)
            _gps_recheck(pth)               # no-op unless this save touches the switch
            merged = merge_stack_values(pth, tgt, b, setv, clear_empty=False)
            for k in rmv:
                merged.pop(k, None)
            if sid == _meshcore_mode.STACK_ID and (_why := self._meshcore_mode_save_refusal(merged)):
                raise ConfigError(_why)         # rolls the whole submission back, like identity
            return render_stack_config(tgt, merged)
        targets.append(("stack", _stack_config_path(self._paths, sid, cfg_band), _render_stack, 0o644))
        if (auto_set or auto_remove) and cfg_band:
            # SECOND transactional target: autostart flags land in the band-less file.
            def _render_auto(pth, tgt=sid, setv=None, rmv=None):
                _gps_recheck(pth)
                if rmv is None:
                    rmv = set(auto_remove)
                if setv is None:
                    setv = dict(auto_set)
                merged = merge_stack_values(pth, tgt, "", setv, clear_empty=False)
                for k in rmv:
                    merged.pop(k, None)
                return render_stack_config(tgt, merged)
            targets.append(("stack", _stack_config_path(self._paths, sid, ""),
                            _render_auto, 0o644))
        else:
            to_set.update(auto_set)
            to_remove |= auto_remove
        # DURABLE restart-required marker — written INSIDE the same transaction (config-txn
        # journal kind "state", pre-image journaled): a restart/build-mode param changed while
        # the stack is RUNNING means the running processes no longer match the saved config.
        # Atomic with the config change: if the marker cannot be persisted the whole save
        # rolls back — a running stack can never hold changed restart-mode settings without
        # the warning.
        # THE RESTART-MARKER DECISION IS TAKEN INSIDE THE TRANSACTION (audit-found race: it used
        # to be taken during this pre-lock build, so a stack that went live while the save queued
        # for the exclusive lock committed a changed identity with NO warning, and an edit to
        # ANOTHER band's store marked a live instance whose own configuration had not changed).
        # Everything the decision needs is re-derived under the lock: the stored values, hence the
        # parameters that ACTUALLY change; the apply-modes of those changes; a FORCED-FRESH live
        # consumer; and whether the committed change is even on the band that consumer reads.
        # Every payload is rendered before any is written, so this reads the pre-save values
        # regardless of where it sits in the target list.
        if clean_params:
            def _render_marker(_paths, tgt=target, _sid=sid, _band=band, _cfg=cfg_band,
                               _params=tuple(clean_params)):
                id_recs = {(r["kind"], r["comp"], r["name"]): r
                           for r in self._identity_fields(tgt)}

                def _post_effective(kind, c, p, v) -> str:
                    """What the stack will ACTUALLY use once this value is stored. A cleared
                    identity does not become empty — a licensed one falls back to the global
                    callsign (audit-found: comparing the pre-save EFFECTIVE value against the RAW
                    submitted blank marked an identity change where the running process still
                    matches the resulting configuration)."""
                    raw = str(v)
                    rec = id_recs.get(("run" if kind == "r" else "file", c.id, p.name))
                    if rec is not None and not raw.strip():
                        return (self.inheritable_global(rec)[0]
                                if rec["enforce"] == "licensed" else "")
                    return raw

                def _pre_effective(kind, c, p) -> str:
                    """What the stack uses RIGHT NOW — resolved the same way as the post-save
                    value, so the two sides of the comparison mean the same thing. Review-found:
                    the stored side went through `{callsign}` substitution (the RAW global) while
                    the submitted side went through inheritance (the VALIDATED global), so with a
                    legacy SSID-bearing or lowercase global — the state every existing installation
                    upgrades from — a save that wrote nothing still flagged the stack
                    restart-required and told the operator to restart."""
                    rec = id_recs.get(("run" if kind == "r" else "file", c.id, p.name))
                    stored = self._stored_param_value(
                        tgt, "run" if kind == "r" else "file", c.id, p.name, _band)
                    if rec is not None and not stored.strip():
                        return (self.inheritable_global(rec)[0]
                                if rec["enforce"] == "licensed" else "")
                    if rec is not None:
                        return stored
                    return self._resolved_param_value(
                        tgt, "run" if kind == "r" else "file", c.id, p.name, _band)
                changed = [(kind, c, p) for kind, c, p, v in _params
                           if _pre_effective(kind, c, p) != _post_effective(kind, c, p, v)]
                modes.update(p.apply_mode for _k, _c, p in changed)
                live_modes = {p.apply_mode for _k, _c, p in changed} & {"restart", "build"}
                if not live_modes:
                    return None                       # nothing that needs a restart changed
                live, live_band = self.active_config_consumer(_sid, fresh=True)
                _live_seen["v"] = live         # reused by the apply hints — never probed twice
                if not live:
                    return None                       # nobody is reading this configuration
                if _cfg and live_band and _cfg != live_band:
                    return None                       # another band's store — the live one is intact
                names = sorted(p.name for _k, _c, p in changed if p.apply_mode in live_modes)
                return self.restart_marker_payload(
                    _sid, names, _cfg or live_band,
                    mode="build" if "build" in live_modes else "restart")
            targets.append(("state", self._restart_marker_path(sid), _render_marker, 0o600))
        try:
            if self._holds_config_exclusive():
                # Inside the auto-install boundary this thread ALREADY holds the config lock EXCLUSIVELY
                # (config-stability), so reuse it via the module-private locked body rather than contending
                # on a second descriptor ("config busy"). The assert forbids the locked path under a SHARED
                # guard — a config mutation must never run beneath a shared stability guard.
                from .config import _apply_config_transaction_locked
                assert self._holds_config_exclusive(), "locked config txn requires the EXCLUSIVE guard"
                _apply_config_transaction_locked(self._paths, targets)
            else:
                apply_config_transaction(self._paths, targets)
        except ConfigError as exc:
            return ActionResult(False, f"Config not saved for '{target}'.", details=[str(exc)])
        self._invalidate_config()               # saved operator/remotes visible immediately
        return ActionResult(True, f"Config saved for '{target}'.",
                            details=self._apply_hints(target, modes,
                                                      live=_live_seen.get("v")) + remote_notes,
                            next_commands=[f"lhpc stack start {target}"])

    def operator_callsign_correction(self) -> str:
        """A VALIDATED base-callsign suggestion derived from a legacy stored global, or ""
        when no valid base can be derived (the card then shows a generic example instead —
        a printed remedy must never itself fail validation)."""
        from . import validators
        call = str(self.config().operator.callsign or "").strip()
        derived = call.split("-")[0].split("/")[0]
        try:
            return validators.callsign_base(derived)
        except validators.ValidationError:
            return ""

    def operator_callsign_legacy(self) -> bool:
        """True when a STORED global operator callsign is not a valid BASE callsign (a
        pre-upgrade value carrying an SSID/suffix): it no longer inherits, and the global
        card shows the correction. Read-only."""
        from . import validators
        call = str(self.config().operator.callsign or "").strip()
        if not call:
            return False
        try:
            validators.callsign_base(call)
            return False
        except validators.ValidationError:
            return True

    def set_operator_identity(self, callsign: str | None = None) -> ActionResult:
        """THE ONE authoritative global-identity mutation. Under a single exclusive config
        critical section (`config_lock` — the same file starts hold SHARED via
        `_config_stable`, so a start either completes before we acquire, and is seen
        running, or waits and then reads the NEW global): reload fresh state, determine the
        RUNNING licensed stacks that inherit (judged on each stack's ACTUAL running band),
        then commit the `[operator]` patch AND every restart-required marker in ONE
        all-or-recoverable transaction — no partial result (audit-found: the previous
        snapshot-before-lock + best-effort markers were racy, band-blind and non-atomic).
        An explicit empty string clears the global; other `[operator]` keys are preserved."""
        from . import config as _config
        from .validators import ValidationError
        from .validators import callsign_base as _v_call
        if callsign is None:
            return ActionResult(False, "nothing to set — pass --callsign")
        try:
            new_call = _v_call(callsign)
        except ValidationError as exc:
            return ActionResult(False, f"invalid operator identity: {exc}")
        local_path = self._paths.runtime_root / "config" / "local.toml"
        keep_unsafe: list = []
        try:
            with _config.config_lock(self._paths):
                # PENDING-JOURNAL RECOVERY FIRST (audit-found): an interrupted earlier
                # transaction may have left local.toml partially written — reading/patching
                # it before recovery would resurrect rolled-back data or drop restored keys.
                # Everything below reads the RECOVERED state.
                if _config.recover_config_transaction(self._paths) == "":
                    return ActionResult(False, "a pending configuration transaction could "
                                        "not be recovered — nothing was changed "
                                        "(journal retained; see lhpc doctor)")
                self._invalidate_config()          # fresh, lock-consistent, recovered
                old_call = str(self.config().operator.callsign or "").strip()
                # Per-stack INHERITED value before the change, so the affected set reflects what
                # each stack actually uses. Review-found: comparing the normalized new global
                # against the RAW stored one flagged every running licensed stack restart-required
                # when a legacy or lowercase global was merely re-saved unchanged — the state an
                # existing installation upgrades into, with no effective identity change at all.
                _before = {}
                for _st in self.stacks():
                    for _rec in self._identity_fields(_st.id):
                        if _rec["enforce"] == "licensed":
                            _before[_st.id] = self.inheritable_global(_rec)[0]
                            break
                affected: list = []                # (sid, run_band)
                if new_call != old_call:
                    for st in self.stacks():
                        # ACTIVE = main running, OR the GUI fallback is what runs here and
                        # its interactive marker is presented (Voice on Lite: the main is
                        # gui-skipped, the operator runs the TUI — its shared config still
                        # carries the inherited callsign; audit-found miss).
                        active, run_band = self.active_config_consumer(st.id)
                        if not active:
                            continue
                        rows = self.identity_resolution(st.id, run_band)
                        if not any(r["enforce"] == "licensed" and not r["explicit"]
                                   for r in rows):
                            continue           # nothing here inherits the global
                        # ...and only when what it INHERITS actually changes. Re-saving a legacy
                        # or lowercase global unchanged leaves every effective identity identical,
                        # so there is nothing to restart for (review-found).
                        after = next((self.inheritable_global(r, new_call)[0] for r in rows
                                      if r["enforce"] == "licensed"), "")
                        if after != _before.get(st.id, ""):
                            affected.append((st.id, run_band))
                # ONE transaction: the [operator] patch + every marker, all-or-recoverable.
                data = dict(_config._load_runtime_toml(self._paths, local_path))
                _config._patch_local_table(data, "operator", {"callsign": new_call})
                targets = [("local", local_path,
                            _config.render_local_tables(data), 0o600)]
                for sid, run_band in affected:
                    _pre = self.restart_required(sid)
                    if _pre is not None and _pre.get("unsafe"):
                        keep_unsafe.append(sid)      # left byte-identical, and disclosed below
                        continue
                    targets.append((
                        "state", self._restart_marker_path(sid),
                        self.restart_marker_payload(
                            sid, ["callsign (inherited global)"], run_band), 0o600))
                _config._apply_config_transaction_locked(self._paths, targets)
        except _config.ConfigLockBusy as exc:
            return ActionResult(False, f"configuration is busy — try again shortly ({exc})")
        except (OSError, _config.ConfigError) as exc:
            return ActionResult(False, f"could not save operator identity: {exc}")
        self._invalidate_config()
        notes = [f"  {sid}: ACTIVE with the old inherited callsign"
                 + (f" (band {rb})" if rb else "")
                 + " — restart it to apply the new one" for sid, rb in affected
                 if sid not in keep_unsafe]
        notes += [f"  WARNING: {sid}'s existing restart-required marker is unreadable/"
                  "malformed — left untouched (safe-side); it already demands attention"
                  for sid in keep_unsafe]
        return ActionResult(True, "operator identity saved",
                            details=[f"  callsign = {new_call or '(unset)'}", *notes],
                            next_commands=[f"lhpc stack restart {sid}"
                                           for sid, _rb in affected])

    def boot_restore_enabled(self) -> tuple[bool, str]:
        """(enabled, reason). FAIL-CLOSED: enabled only when the [boot] config is VALID and
        restore is true — a malformed/mistyped switch disables restoration with the reason."""
        cfg = self.config().boot
        if not cfg.valid:
            return False, cfg.reason or "invalid [boot] config"
        return bool(cfg.restore), "" if cfg.restore else "disabled by [boot] restore"

    def set_boot_restore(self, enabled: bool) -> ActionResult:
        """Persist the boot auto-restore switch (applies at the NEXT boot)."""
        from .config import ConfigError, save_boot_restore
        if not isinstance(enabled, bool):
            return ActionResult(False, "boot restore switch must be a boolean")
        try:
            save_boot_restore(self._paths, enabled)
        except (OSError, ConfigError) as exc:
            return ActionResult(False, f"could not save the boot restore switch: {exc}")
        self._invalidate_config()
        state = "ON" if enabled else "OFF"
        return ActionResult(True, f"Boot auto-restore switched {state} — applies at the next boot.")

    def set_hardware_setup(self, setup_id: str | None = None) -> ActionResult:
        """Set the radio HARDWARE setup (`[radio].hardware` in local.toml) — e.g. 'loraham',
        'uputronics', 'waveshare-433'. No arg reports the current setup + served bands. 'unset' means
        no hardware is configured: the daemon refuses to start until a real setup is chosen. The setup
        fixes which band(s) are served and the daemon `--hw` preset each one launches with."""
        from . import config as _config
        setups = dict(self.hw_setups())
        if setup_id is None:
            cur = self.hardware_setup()
            return ActionResult(True, f"hardware setup: {cur} ({setups.get(cur, cur)})",
                                details=[f"  served band(s): {', '.join(self.active_bands()) or '(none)'}",
                                         "  choose from: " + ", ".join(setups)])
        if setup_id not in setups:
            return ActionResult(False, f"unknown hardware setup {setup_id!r}",
                                details=["  choose from: " + ", ".join(setups)])
        try:
            _config.save_hardware_setup(self._paths, setup_id)
        except (OSError, _config.ConfigError) as exc:
            return ActionResult(False, f"could not save hardware setup: {exc}")
        self._invalidate_config()
        return ActionResult(True, f"hardware setup set to {setup_id}",
                            details=[f"  served band(s): {', '.join(self.active_bands()) or '(none)'}"])

    def meshcore_identity_candidates(self) -> tuple:
        """Files that may already hold the MeshCore identity, newest-authority first.

        Resolved from the MANIFEST (never hardcoded paths): the generated config, then the
        upstream template it is built from — the only two places a key could be living
        before LHPC owned it, the second because pinning the identity used to mean
        hand-editing that template.
        """
        hit = self._component_index().get(MESHCORE_COMPONENT)
        comp = hit[1] if hit else None
        fc = getattr(comp, "config_file", None) if comp else None
        if fc is None:
            return ()
        out = []
        linked = bool(comp.source) and self._lifecycle().is_linked_source(comp)
        for raw, is_base in ((fc.path, False), (fc.base, True)):
            if not raw:
                continue
            # An lhpc-shipped asset base is OUR read-only packaged file: it never
            # carries an identity, and resolving it against the runtime root would
            # (correctly) refuse containment — which must not block adoption.
            if is_base and raw.startswith("{asset}"):
                continue
            # A LINKED source is a symlink into someone's checkout, and every runtime read is
            # descriptor-anchored (O_NOFOLLOW per component), so reading through it raises
            # PathContainmentError rather than returning "no key". `_resolve_config_dest`
            # only applies its linked-readonly guard when `not for_base`, so the BASE
            # candidate must be skipped here — otherwise adopt_identity turns that read
            # error into a refusal and blocks install/update/uninstall/clean outright,
            # leaving an operator with a linked meshcore-pi no way out. The generated config
            # (the first candidate) still covers the normal upgrade.
            if is_base and linked:
                continue
            try:
                dest = self._resolve_config_dest(comp, raw, for_base=is_base)
            except (OSError, PathContainmentError, ValueError):
                continue
            if dest.status == "ok" and dest.path is not None:
                out.append(dest.path)
        # LEGACY: an install upgrading from the meshcore-pi era may hold its identity
        # only in that generation's config file, which the current manifest no longer
        # names. candidate_key() knows its `[device.companion] privatekey` location, so
        # keep the file as a last candidate for the rescue path.
        try:
            legacy = self._paths.under("config", "files", "meshcore-pi.toml")
        except (OSError, PathContainmentError, ValueError):
            return tuple(out)
        if legacy.exists() and legacy not in out:
            out.append(legacy)
        return tuple(out)

    def meshcore_position(self, target: str) -> tuple[dict | None, str]:
        """The STATIC coordinates for a MeshCore start — `(position, message)`.

          * `({...}, "")`     — write these coordinates into the config;
          * `({}, note)`      — write none, and strip any stale pair;
          * `(None, reason)`  — REFUSE the start; nothing has been touched yet.

        Only `fixed` produces coordinates. Every LIVE source is served by the `meshcore-gps`
        bridge instead, which feeds NMEA to the node continuously — so its position follows
        the box instead of freezing at whatever it was when the stack started. Writing a
        snapshot as well would hand the node a stale fallback to revert to the moment the
        live feed aged out, defeating the staleness clearing on both sides.

        Liveness for a live source is therefore the FEED's business, gated by the bridge's
        readiness exactly as it is for Meshtastic and MeshCom — not by a refusal here.
        """
        from .gps import CONSUMER_MESHCORE, plan_from_config
        # `_run_order` is None for an unknown target — the caller reports that properly a
        # moment later, so say "no position" rather than raising out of the start path.
        if not any(c.id == MESHCORE_COMPONENT for _s, c in (self._run_order(target) or ())):
            return {}, ""                              # not a MeshCore start
        if not self._meshcore_consumes_position(self.gps_owner_stack(target)):
            return {}, ""                              # repeater-only: nothing reads it
        if not self.gps_enabled_for(target):
            return {}, ""                              # use_gps off — omit, never refuse
        plan = plan_from_config(self.config())
        if not plan.valid:
            # A broken [gps] table is NOT an ordinary "off": the operator asked for a
            # position and we cannot tell what they meant.
            return None, (f"the global position source is not usable: "
                          f"{plan.reason or 'invalid [gps] configuration'}")
        if plan.needs_bridge(CONSUMER_MESHCORE):
            return {}, ""                              # the live feed owns the position
        if plan.is_fixed:
            # NORMALISE, never pass the saved strings through: `[gps]` validates with
            # `float()`, which accepts forms TOML rejects — a European `fixed_lon = "007.6"`
            # is a valid position and an invalid TOML number.
            try:
                return ({"lat": f"{float(plan.fixed_lat):.7f}",
                         "lon": f"{float(plan.fixed_lon):.7f}"}, "")
            except (TypeError, ValueError):
                return None, ("the configured fixed position is not a pair of numbers — "
                              "set it again with `lhpc gps --lat <n> --lon <n>`")
        return {}, ""                                  # deliberately off

    def meshcore_identity_guard(self, comps) -> str:
        """Rescue the MeshCore identity before an operation replaces or removes its source.

        Returns "" to proceed, or a reason to REFUSE with. Call it after the authoritative
        refusal/running checks, with the operation's locks held, and before the first source
        or config deletion — an identity that still lives only in a file we are about to
        overwrite has to be copied out first, or the node silently comes back as a stranger.

        Adopts only: an operator updating or uninstalling a MeshCore they never ran must not
        end up with a freshly minted key. A candidate that is present but invalid refuses,
        rather than letting the destructive step proceed over an identity we could not read.
        """
        if not any(getattr(c, "id", "") == MESHCORE_COMPONENT for c in comps):
            return ""
        from . import meshcore_identity
        try:
            meshcore_identity.adopt_identity(self._paths, self.meshcore_identity_candidates())
        except meshcore_identity.MeshCoreIdentityError as exc:
            return str(exc)
        except (OSError, PathContainmentError) as exc:
            return f"could not preserve the MeshCore identity: {exc}"
        return ""

    def _controller_secret(self, name: str) -> str:
        """Resolve a `secret_file` FileParam to its value, creating it if this is first use.

        Config generation is the ONE place allowed to mint a MeshCore identity: every other
        caller adopts only. An unusable existing key raises rather than being replaced.
        """
        from . import meshcore_identity
        if name == meshcore_identity.IDENTITY_FILENAME:
            return meshcore_identity.ensure_identity(self._paths,
                                                     self.meshcore_identity_candidates())
        if name == meshcore_identity.REPEATER_IDENTITY_FILENAME:
            # The repeater is a DISTINCT MeshCore node: its own key, minted once, never adopted
            # from anywhere (nothing before this feature could hold one).
            return meshcore_identity.ensure_identity(self._paths, (), filename=name)
        if name == meshcore_identity.REPEATER_ADMIN_FILENAME:
            return meshcore_identity.ensure_password(self._paths, name)
        raise ValueError(f"unknown controller secret {name!r}")

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
            elif plan.source == "gpsd" and owner == CONSUMER_MESHTASTIC:
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

    # Position keys Sideband used to own per-stack, before `[gps]` became authoritative.
    _LEGACY_GPS_KEYS: ClassVar[tuple] = (
        "location_source", "gpsd_host", "gpsd_port", "nmea_device", "nmea_baud",
        "fixed_lat", "fixed_lon", "fixed_alt")

    def legacy_gps_values(self) -> dict:
        """Stale per-stack position values still saved on disk -> {stack: [keys]}.

        These no longer take effect: the params are `hidden` and filled from `[gps]`, so a
        saved value cannot override the global source. But leaving them silently in place
        would mislead anyone reading the config, so they are REPORTED — the audit's
        "migrated, or ignored with a clear diagnostic, never silently overriding".
        """
        found = {}
        for sid in ("reticulum",):
            try:
                cfg = load_stack_config(self._paths, sid)
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

    def _snapshot_or_none(self):
        """The CURRENT (possibly memoized) snapshot, or None when it cannot be built — the
        callers treat that as a blocker rather than as "nothing is running"."""
        try:
            return self.build_snapshot()
        except Exception:
            return None

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

        DERIVED from the manifest (audit-found): this was the third hardcoded GPS stack list,
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
                    # AUDIT-FOUND honesty gap: graywolf has no fixed GPS mode (its API takes
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

    def save_stack_config(self, target: str, values: dict, band: str = "") -> ActionResult:
        """Validate and persist a stack/band's run + file configuration via the CANONICAL bundle
        path (`save_config_bundle`). `values` keys are the same canonical API keys the Config/Start
        pages use: `name`/`file_<name>` when unique, `component.name`/`file_<component.name>` when the
        name is duplicated across the stack. Unknown fields and unqualified duplicate names are typed
        failures with NO mutation; unique names keep their flat-key/field compatibility; daemon-profile
        `dp_*`, autostart, remotes and the transactional semantics are preserved.

        `use_gps` needs no special handling here: `save_config_bundle` routes it to the band-less
        file and refuses a change under a running stack, so every surface behaves identically.
        (It once WAS handled here — which fixed nothing, because the CLI and the console both
        call `save_config_bundle` directly.)"""
        if self.stack(target) is None:
            return self._unknown_stack(target)
        return self.save_config_bundle(target, values=values, band=band)

    def _apply_hints(self, target: str, modes: set, live: bool | None = None) -> list[str]:
        """Human guidance on how a saved config change takes effect."""
        # THE same live-consumer predicate the restart-required marker uses (review-found: this
        # asked only `stack_running`, so a presented interactive command — Chat's, or Voice on
        # Lite — was told "applies on the next Run" while the dashboard raised restart-required
        # for the very command still carrying the old identity).
        running = self.stack_running(target) if live is None else live
        hints = []
        if "build" in modes:
            hints.append("Compile-time change — Rebuild (Build) the stack to apply.")
        if "restart" in modes:
            hints.append("Restart the stack to apply." if running
                         else "Start-time change — applies on the next Run.")
        if "live" in modes:
            hints.append("Runtime change — applied live.")
        return hints

    # ---- durable restart-required state -------------------------------------

    def _restart_marker_path(self, stack_id: str):
        return self._paths.under("state", "restart-required",
                                 f"{validators.path_component(stack_id, field='stack')}.json")

    def active_config_consumer(self, stack_id: str, *, fresh: bool = False) -> tuple[bool, str]:
        """Is this stack a LIVE consumer of its saved configuration, and on which band?

        ONE predicate for every writer that must warn "the running thing no longer matches
        what is saved" — the global identity setter AND ordinary config/identity saves
        (audit-found: they disagreed, so changing a local callsign while Voice ran as the
        Lite TUI fallback saved silently and left the presented app on the old identity).
        Running main -> its running band. Otherwise a stack whose GUI fallback is what runs
        HERE and whose interactive marker is presented -> that marker's band."""
        if fresh:
            # Authoritative recheck UNDER the transaction lock: drop any snapshot this same
            # operation cached before it acquired the lock (audit-found: a stack that went live
            # while the save queued for the exclusive lock was committed with no marker).
            self.build_snapshot(fresh=True)
        if self.stack_running(stack_id):
            return True, self._effective_band(stack_id, "")
        st = self.stack(stack_id)
        main = st.main_component if st is not None else None
        # An INTERACTIVE main whose command has been prepared and presented is a live consumer too:
        # the operator can still run it, and it carries the identity that was generated FOR it
        # (audit-found: Chat fell through this gap — its presented command kept the old callsign
        # while the store said something else, with no warning). Voice's GUI fallback is the same
        # situation reached by another route.
        if st is not None and (getattr(main, "interactive", False)
                               or self.gui_fallback_active(st)):
            ib = self.interactive_band(stack_id)
            if ib is not None:
                return True, ib
        return False, ""

    def restart_marker_payload(self, sid: str, params, band: str,
                               mode: str = "restart") -> str:
        """The MERGED restart-marker JSON for `sid`. Called from inside the config transaction, so
        it reads the marker that is committed RIGHT NOW and a concurrent writer's reason is merged
        rather than overwritten.

        MERGE (audit-found: a blind replace destroyed a stronger build requirement, its reasons and
        its band): build outranks restart, reasons are unioned without duplication, an existing
        CONCRETE band is retained.

        Unreadable CONTENT is replaced; an unsafe PATH makes the transaction refuse and the whole
        save roll back, which is the invariant that a running stack never holds changed
        restart-mode settings without a warning. The one caller that must instead leave an
        unreadable marker untouched (the global identity setter) checks for that itself."""
        import json as _json
        cur = self.restart_required(sid)
        if cur is not None and cur.get("unsafe"):
            cur = None                         # unreadable content is replaced, never merged
        merged_mode = "build" if (mode == "build"
                                  or (cur or {}).get("mode") == "build") else "restart"
        return _json.dumps({
            "version": 1, "stack": sid, "mode": merged_mode,
            "params": list(dict.fromkeys([*(cur or {}).get("params", []), *params])),
            "band": (cur or {}).get("band") or band, "created_at": time.time()})

    def restart_required(self, stack_id: str) -> dict | None:
        """The durable restart-required marker (FILE READ ONLY, GET-safe, TRI-STATE): set
        atomically with a config save that changed restart/build-mode params while the stack
        ran; cleared on a verified stop or a successful start/restart.

          * SAFELY ABSENT (FileNotFoundError only)  -> None: no warning;
          * SAFELY VALID                            -> the marker dict;
          * PRESENT BUT UNSAFE (malformed, symlinked, a directory, special, inaccessible,
            or claiming another stack) -> {"unsafe": True, "stack": …, "reason": …}: the
            warning stays visible SAFE-SIDE with the explicit Restart action and a
            diagnostic — an unreadable marker must never look like "no restart required".
            The marker is NEVER silently cleared here (GET stays non-mutating)."""
        import json as _json

        def _unsafe(reason: str) -> dict:
            return {"unsafe": True, "stack": stack_id, "mode": "restart", "params": [],
                    "reason": reason}
        try:
            raw = runtime_fs.read_text_regular(self._paths, self._restart_marker_path(stack_id))
        except FileNotFoundError:
            return None                                   # SAFELY absent — proven
        except (OSError, PathContainmentError, ValueError) as exc:
            return _unsafe(f"restart-required marker is present but unreadable/unsafe "
                           f"({exc}) — treat as restart required; resolve the marker")
        try:
            d = _json.loads(raw)
        except (ValueError, TypeError):
            return _unsafe("restart-required marker is malformed — treat as restart "
                           "required; resolve the marker")
        if not isinstance(d, dict) or d.get("version") != 1 or d.get("stack") != stack_id:
            return _unsafe("restart-required marker fails validation — treat as restart "
                           "required; resolve the marker")
        # FULL field schema (audit-found: a structurally-invalid-but-parseable marker was
        # trusted — an unknown mode silently downgraded to restart, a string params value
        # iterated character-by-character in the merge, an integer raised uncaught). ONE
        # schema, here: every consumer (display AND the global-change merge) reads through
        # this method, and any invalid shape stays byte-identical, safe-side UNSAFE.
        if d.get("mode") not in ("restart", "build"):
            return _unsafe("restart-required marker carries an unknown mode — treat as "
                           "restart required; resolve the marker")
        params_v = d.get("params")
        if (not isinstance(params_v, list)
                or not all(isinstance(x, str) and x
                           and not any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in x)
                           for x in params_v)):
            return _unsafe("restart-required marker carries malformed params — treat as "
                           "restart required; resolve the marker")
        band_v = d.get("band", "")
        if band_v != "":
            try:
                validators.band(str(band_v), allow_both=False)
            except validators.ValidationError:
                return _unsafe("restart-required marker carries an invalid band — treat "
                               "as restart required; resolve the marker")
        if not isinstance(d.get("created_at"), (int, float)):
            return _unsafe("restart-required marker carries an invalid timestamp — treat "
                           "as restart required; resolve the marker")
        return d

    def restart_required_stacks(self) -> list:
        """All stacks currently flagged restart-required — including SAFE-SIDE unsafe markers
        (for the dashboard + CLI status + dash signature)."""
        return [s.id for s in self.stacks() if self.restart_required(s.id) is not None]

    def _clear_restart_required(self, stack_id: str) -> None:
        """Clear the marker (best effort — a stale marker is safe-side: the operator sees a
        yellow action that a fresh restart simply satisfies)."""
        try:
            runtime_fs.unlink(self._paths, self._restart_marker_path(stack_id))
        except (OSError, PathContainmentError):
            pass

    def save_component_remote(self, component_id: str, url: str) -> ActionResult:
        """Override (or clear, if url is blank) a component's GitHub remote. A shared source
        path is ONE checkout with ONE remote: the change is applied ATOMICALLY to EVERY
        component declaring the same source path (one locked write), and the propagation is
        explicitly disclosed in the result — per-component divergence is never left behind."""
        from .config import save_component_remotes
        comp = next((c for st in self.stacks() for c in st.components
                     if c.id == component_id), None)
        group = ([d.id for d in self._path_declarers(comp.source.path)]
                 if comp is not None and comp.source else [component_id])
        try:
            save_component_remotes(self._paths, dict.fromkeys(group, url))
        except validators.ValidationError as exc:
            return ActionResult(False, "Remote override rejected.", details=[str(exc)])
        except ConfigError as exc:
            return ActionResult(False, "Remote override not saved.",
                                details=[f"local.toml is malformed and was preserved: {exc}"])
        self._invalidate_config()               # new remote visible to the next read
        extra = sorted(set(group) - {component_id})
        details = ([f"  shared checkout — the same remote was applied to: {', '.join(extra)}"]
                   if extra else [])
        return ActionResult(True, "Remote override saved." if url.strip()
                            else "Remote override cleared.", details=details)

    # ---- file-based component config (writes the app's own config file) -----

    def _file_config_components(self, target: str):
        # Owner-stack scoped for a stack target; JUST the targeted component for a direct component.
        return [c for c in self._target_components(target) if c.config_file]

    def file_config_values(self, target: str, band: str = "") -> dict:
        """Stored file-config values (component-scoped `__f__<comp>__<name>`, else a UNIQUE flat
        legacy `file_<name>`), falling back to the per-band default, then the FileParam default. An
        ambiguous flat legacy value is NOT applied here (the start blocks; see `_config_ambiguity`)."""
        cfg_band = self._config_band(target, band)
        owner = self._owner_stack(target)
        stored = load_stack_config(self._paths, self._owner_stack_id(target), cfg_band)
        _, file_counts = self._owner_param_counts(owner)
        out = {}
        for c in self._file_config_components(target):
            for p in c.config_file.params:
                bd = dict(p.band_defaults).get(cfg_band or band, p.default)
                val, _amb = self._resolve_stored(stored, "f", c.id, p.name,
                                                 file_counts.get(p.name, 0))
                out[p.name] = str(val) if val is not None else bd
        return out

    def _op_subst(self, text: str) -> str:
        op = self.config().operator
        return str(text).replace("{callsign}", op.callsign or "")

    # Retired generic first-start identities: a saved value EQUAL to one of these is a
    # generated default that survived a form round-trip, not a deliberate identity — it must
    # not satisfy the deliberate-configuration requirement. (A user genuinely wanting one of
    # these names is indistinguishable from the default; the contract resolves that in favor
    # of refusing.)
    _IDENTITY_PLACEHOLDERS: tuple = ("LoRaHAM Pi", "LHPi", "pyMC", "N0CALL", "XX0XXX-00",
                                     "NODENAME", "SHORTNAME", "YOURCALL")

    def _is_identity_placeholder(self, value: str) -> bool:
        """REVIEW-FOUND: the membership test was case-SENSITIVE, so the retired generated default
        `LHPi` was refused while `lhpi` sailed through and the node advertised the very placeholder
        the rule exists to prevent. Node names are free text — only callsigns get upper-cased —
        so the comparison has to fold case itself."""
        return value.strip().casefold() in {p.casefold() for p in self._IDENTITY_PLACEHOLDERS}

    def _identity_fields(self, target: str) -> list[dict]:
        """EVERY operator-identity field CALL/node enforcement guards (a stack may have more
        than one — Meshtastic requires both its long and short node names). Scoped to the
        target: a STACK target inspects every component; a direct COMPONENT target only that
        component. The daemon is exempt. Licensed fields sort first (the primary remedy)."""
        if self._is_daemon_target(target):
            return []
        out: list[dict] = []
        for c in self._target_components(target):
            candidates = [("run", p) for p in c.run_params]
            candidates += [("file", p) for p in (c.config_file.params if c.config_file else ())]
            for kind, p in candidates:
                enforce = self._IDENTITY_ENFORCE.get(getattr(p, "validator", ""))
                if not enforce:
                    continue
                out.append({"comp": c.id, "name": p.name, "kind": kind, "enforce": enforce,
                            "validator": getattr(p, "validator", ""),
                            "label": p.label or p.name,
                            "field": self._param_field(target, kind, c.id, p.name),
                            "default": str(getattr(p, "default", "") or "")})
        from . import meshcore_mode as _mm
        if (any(r["comp"] == _mm.NODE_ID for r in out)
                and not _mm.chat_identity_required(self.meshcore_mode())):
            # Repeater-only: no Companion runs, so the chat node's name is not an identity to
            # enforce (the repeater's own name is checked by `_meshcore_mode_refusal`).
            out = [r for r in out if not (r["comp"] == _mm.NODE_ID and r["name"] == "node_name")]
        out.sort(key=lambda r: r["enforce"] != "licensed")
        return out

    def meshcore_mode(self) -> str:
        """The MeshCore stack's SAVED (desired) mode (`meshcore_mode.MODES`), read from the node's
        `mode` setting exactly like every other param — the mode the next start runs in. `chat`
        (the default, the mode every box ran before the setting existed) when the stack or the
        param is absent (a synthetic manifest). A corrupt stack config raises `ConfigError` like
        every other read of it: the fail-closed contract, never a silent default."""
        from . import meshcore_mode as _mm
        hit = self._component_index().get(_mm.NODE_ID)
        fc = getattr(hit[1], "config_file", None) if hit else None
        if fc is None or not any(p.name == "mode" for p in fc.params):
            return _mm.DEFAULT_MODE
        return _mm.normalize(self._resolved_param_value(_mm.STACK_ID, "file", _mm.NODE_ID, "mode"))

    def meshcore_running_mode(self) -> str:
        """The mode the node is RUNNING in: the `[repeater] role` of the GENERATED config, which
        write_config_files renders only on the start path — so a saved-but-not-yet-restarted
        change does not flip a healthy node's status, and a stop verifies the endpoints the
        launch actually opened. Falls back to the saved mode when nothing was generated yet."""
        import tomllib

        from . import meshcore_mode as _mm
        hit = self._component_index().get(_mm.NODE_ID)
        fc = getattr(hit[1], "config_file", None) if hit else None
        if fc is not None:
            try:
                dest = self._resolve_config_dest(hit[1], fc.path, for_base=False)
                if dest.status == "ok" and dest.path is not None:
                    raw = runtime_fs.read_bytes(self._paths, dest.path)
                    role = (tomllib.loads(raw.decode("utf-8", "replace"))
                            .get("repeater", {}) or {}).get("role")
                    if role:
                        return _mm.normalize(role)
            except (OSError, PathContainmentError, ValueError, UnicodeError):
                pass                       # unreadable/absent generated file: the saved mode
        return self.meshcore_mode_display() or _mm.DEFAULT_MODE

    def meshcore_mode_display(self) -> str:
        """The SAVED mode for read-only surfaces (status, the console, stop verification): ""
        when the stack config cannot be read. Those surfaces must keep working for every stack
        with one broken MeshCore file; the START path reads `meshcore_mode()` itself and fails
        closed on the same file."""
        try:
            return self.meshcore_mode()
        except ConfigError:
            return ""

    def _meshcore_mode_save_refusal(self, merged: dict) -> str:
        """Why a MeshCore config save must be refused ("" = fine): a repeater mode needs the
        repeater's own node name IN THE SAME saved state — a saved repeater mode without a name
        would refuse every later start (boot restore included). Same rule the host applies."""
        from . import meshcore_mode as _mm
        mode, _ = self._resolve_stored(merged, "f", _mm.NODE_ID, "mode", 1)
        if not _mm.repeater_on(mode):
            return ""
        name, _ = self._resolve_stored(merged, "f", _mm.NODE_ID, "repeater_name", 1)
        try:
            validators.node_name(name or "", field="repeater name")
        except validators.ValidationError as exc:
            return (f"mode '{_mm.normalize(mode)}' needs the repeater's own node name — {exc} "
                    f"(set repeater_name in the same save, or keep mode chat)")
        return ""

    def _identity_field(self, target: str) -> dict | None:
        """The PRIMARY identity field (compat: first licensed, else first node field)."""
        fields = self._identity_fields(target)
        return fields[0] if fields else None

    def _identity_cmd(self, target: str, rec: dict, value: str, band: str = "") -> str:
        """THE copyable `lhpc config` command for one identity field — key derived from the
        param model, `--band` appended for a band-switchable stack when a band was judged.
        Every displayed remedy (hints AND resolution problem text) renders through here, so
        a correction always targets the exact field and band that was refused."""
        kind = "run" if rec["kind"] == "run" else "file"
        key = self._param_key(target, kind, rec["comp"], rec["name"])
        suffix = (f" --band {band}"
                  if band and self.stack_bands(self.stack_of(target) or target) else "")
        return f"lhpc config {target} {key} {value}{suffix}"

    def operation_band(self, target: str, band: str = "") -> str:
        """THE ONE CONCRETE BAND of a start/restart, for every consumer outside the locked launch
        section: the band the operation will actually run on (explicit -> running -> ACTIVE
        fallback marker -> primary), mapped to its config band — so a band-switchable stack gets a
        CONCRETE band and "" survives only for a genuinely bandless stack, which nothing can
        reinterpret. Web render, hidden confirm state, Save, Apply, identity persistence, ordinary
        and dependency params, plan, locks, stop and launch all read this ONE value.

        Audit-found, twice: the panel prefilled identity from the RUNNING band while ordinary rows
        (frequency, SF, bandwidth, preamble, TX power) came from the RAW band — a bandless Web
        restart of a stack running on 868 could show the 868 callsign beside 433 radio values, save
        the 868 callsign into the 433 store, and launch the 868 operation with 433 radio
        parameters."""
        # `_config_band` maps to the per-band CONFIG STORE. The DAEMON has no such store yet
        # genuinely operates per band, so flattening there would DISCARD an explicit `--band 433`.
        # Only the daemon keeps the raw hint: a FIXED-band client (chat/igate/meshcom on 433,
        # meshcore on 868) also has no store, and honouring a raw band for it would ensure, lock
        # and clear the daemon on a band the node cannot run on (review-found) — it flattens, as
        # it always did, and the fixed band its manifest declares is what actually runs.
        hint = self._launch_band_hint(target, band)
        cb = self._config_band(target, hint)
        return cb or (hint if self._is_daemon_target(target) else "")

    def extract_identity_submission(self, target: str, params, file_over):
        """PHASE 1 of the persisted-only identity model — PURE, no I/O, no locks. Lifts every
        submitted identity field out of the ephemeral launch inputs and returns
        (params, file_over, submission). Runs on the RAW inputs, BEFORE `_normalize_*`, because
        normalization drops blank file values and a blank identity is the deliberate
        "clear and inherit" (review-found). A malformed payload is left untouched for the
        normalizer's typed refusal. The values are SAVED later, inside task admission, by
        `save_identity_submission` — the one authoritative write."""
        if (params is not None and not isinstance(params, dict)) or (
                file_over is not None and not isinstance(file_over, dict)):
            return params, file_over, []
        recs = self._identity_fields(target)
        if not recs:
            return params, file_over, []
        p = dict(params or {})
        fo = dict(file_over) if file_over is not None else None
        values: dict = {}
        for rec in recs:
            kind = "run" if rec["kind"] == "run" else "file"
            key = self._param_key(target, kind, rec["comp"], rec["name"])
            src = p if kind == "run" else (fo or {})
            if key not in src:
                continue
            values[self._identity_bundle_key(target, rec)] = str(src.get(key) or "").strip()
            (p if kind == "run" else fo).pop(key, None)
        return p, fo, values

    def _identity_bundle_key(self, target: str, rec: dict) -> str:
        """The `save_config_bundle` key for one identity field — THE single spelling used by the
        extractor, the policy and the writer."""
        kind = "run" if rec["kind"] == "run" else "file"
        key = self._param_key(target, kind, rec["comp"], rec["name"])
        return key if kind == "run" else f"file_{key}"

    def identity_refusal_for_values(self, target: str, band: str, values: dict,
                                    op: str = "save"):
        """THE identity contract for submitted values, in one place, for every writer — the
        Start/Restart panel (Apply and Save), the launch, and the in-transaction recheck.

        A value is judged only if the bundle actually carries it. The manifest validator alone
        accepts the retired generic defaults; and a blank means "inherit" ONLY for a LICENSED
        field with a global it can actually inherit — for an unlicensed node identity, which
        never inherits, or a licensed one with no usable global, a blank means MISSING, and
        storing it would delete the stack's only identity and refuse the very next launch."""
        bad: list[dict] = []
        for rec in self._identity_fields(target):
            # A bundle addresses a param bare (unique in its stack) or component-qualified; a
            # DEPENDENCY bundle is ALWAYS qualified (review-found: seeking only the bare name
            # made this a silent no-op there).
            key = next((k for k in (self._identity_bundle_key(target, rec),
                                    ("" if rec["kind"] == "run" else "file_")
                                    + f"{rec['comp']}.{rec['name']}") if k in values), "")
            if not key:
                continue
            val = str(values.get(key) or "").strip()
            inherited, why = ("", "") if val else self.inheritable_global(rec)
            if (val and self._is_identity_placeholder(val)) or (
                    not val and (rec["enforce"] != "licensed" or not inherited)):
                bad.append({**rec, "_v": bool(val), "_why": why,
                            "_lic": not val and rec["enforce"] == "licensed"})
        if not bad:
            return None
        return ActionResult(
            False, f"Cannot {op} '{target}': "
            + "; ".join(
                f"{r['label']} is a placeholder, not an identity" if r["_v"]
                else ((f"{r['label']} cannot be cleared — {r['_why']}" if r.get("_why")
                       else f"{r['label']} cannot be cleared — there is no global operator "
                            "callsign to inherit") if r.get("_lic")
                      else f"{r['label']} is required — a local node identity, never inherited")
                for r in bad),
            data={"enforce_fields": [r["field"] for r in bad]},
            next_commands=self._identity_config_hints(target, band))

    def save_identity_submission(self, target: str, band: str, op: str, values: dict):
        """PHASE 2 — the authoritative write, called with task admission HELD and the ONE
        concrete operation band, before the shared config-stability guard (the save needs the
        EXCLUSIVE config lock, which cannot nest under that shared guard). Returns a refusal
        ActionResult, or None when the identity is persisted.

        Every submitted key goes into the bundle verbatim — empty included. There is no
        "unchanged, skip the save" shortcut: that compared against a value read outside the
        config transaction, so a concurrent edit in the window made the helper strip the
        submitted identity without ever writing it and the launch silently used the other
        value (audit-found). `save_config_bundle` is the single validating, locked,
        cache-invalidating path and decides for itself whether the write is a no-op."""
        if not values:
            return None
        if (refusal := self.identity_refusal_for_values(target, band, values, op)) is not None:
            return refusal
        r = self.save_config_bundle(target, values=values, band=band, _identity_guard=True)
        if not r.ok:
            _detail = " ".join(r.details[:2])
            # A refusal raised by the in-transaction recheck already reads as a full sentence —
            # do not wrap it in "identity not saved — Config not saved — ..." three deep.
            _why = (_detail.split("rolled back: ", 1)[1] if "rolled back: " in _detail
                    else f"identity not saved — {r.summary} {_detail}")
            return ActionResult(
                False, _why if _why.startswith("Cannot") else f"Cannot {op} '{target}': {_why}",
                # enforce_fields keeps the web on the confirm page with the operator's typed
                # identity intact — a bare flash+redirect would lose it (review-found).
                data={"enforce_fields": [r_["field"] for r_ in self._identity_fields(target)
                                         if self._identity_bundle_key(target, r_) in values]},
                next_commands=self._identity_config_hints(target, band))
        return None

    def _identity_config_hints(self, target: str, band: str = "") -> list[str]:
        """The copy-pasteable remedies for a start refused over missing/invalid identities:
        ONE `lhpc config <stack> <field> <value>` per problem field — a fresh Meshtastic
        refusal prints BOTH node_name and node_short in the same attempt (audit-found:
        first-problem-only forced two failed starts). Field names derive from the manifest/
        param model (`_param_key`), never a hand-kept map. For a licensed stack the optional
        global fallback rides along as a comment on the first line, never the lead."""
        # BAND-AWARE (audit-found): the refusal that prints these is judged on the
        # running/launch band, so the remedy must target THAT band's store — a hint
        # without --band saved into the primary band and the same refusal repeated.
        rows = self.identity_resolution(target, band)
        bad = [r for r in rows if r["problem"]] or rows
        if not bad:
            return [f"lhpc config {target}"]
        out = []
        for n, r in enumerate(bad):
            # SHELL-SAFE replace-me templates (audit-found: <angle-bracket> placeholders
            # are shell redirection operators, and quoted ones reach validation literally).
            # Every placeholder is an UPPERCASE marked token the trailing comment names.
            example, what = {
                "callsign": ("YOURCALL-10", "YOURCALL-10 = your callsign (+optional SSID)"),
                "callsign_voice": ("YOURCALL", "YOURCALL = your callsign (or portable form)"),
                "callsign_meshcom": ("YOURCALL-99",
                                     "YOURCALL-99 = your callsign (+optional suffix)"),
                "node_short": ("SHORTNAME", "SHORTNAME = your short node name (max 4)"),
            }.get(r["validator"], ("NODENAME", "NODENAME = your node name"))
            cmd = self._identity_cmd(target, r, example, band) + f"   # {what}"
            if n == 0 and r["enforce"] == "licensed":
                cmd += ("; or once for every licensed stack: "
                        "lhpc config operator --callsign YOURCALL")
            out.append(cmd)
        return out

    def _identity_explicit(self, target: str, band: str, rec: dict, params, file_over) -> str:
        """The EXPLICIT local value of one identity field — the ephemeral override for that
        component's key, else its own component-scoped/unique-flat STORED value. Never the
        manifest default: an empty local field MEANS "inherit" (licensed) or "missing"
        (unlicensed), and resolution must see that emptiness, not a substituted default."""
        kind = "run" if rec["kind"] == "run" else "file"
        key = self._param_key(target, kind, rec["comp"], rec["name"])
        val = ((params if kind == "run" else file_over) or {}).get(key)
        if val is None:
            val = self._stored_param_value(target, kind, rec["comp"], rec["name"], band)
        return str(val or "").strip()

    def inheritable_global(self, rec: dict, candidate: str | None = None) -> tuple:
        """`(value, reason)`: the global operator callsign AS THIS FIELD WOULD INHERIT IT, or
        `("", why)` when there is no global or this stack's protocol cannot carry it. THE single
        accept-decision, shared by `identity_resolution` (what a stack effectively uses) and
        `identity_refusal_for_values` (whether clearing a local callsign is safe) — audit-found:
        the Web pre-gate refused a licensed clear while the service persisted it first, so the
        same submission had two different persistence semantics. The reason is returned rather
        than swallowed so the caller can word its remedy without recomputing the failure."""
        from . import validators
        cand = (str(self.config().operator.callsign or "") if candidate is None
                else str(candidate)).strip()
        if not cand:
            return "", ""
        validate = validators._NAMED.get(rec["validator"])
        try:
            base = validators.callsign_base(cand)
            return (validate(base) if validate else base), ""
        except validators.ValidationError as exc:
            return "", str(exc)

    def identity_resolution(self, target: str, band: str = "", params: dict | None = None,
                            file_over: dict | None = None) -> list[dict]:
        """THE authoritative effective-identity resolution — every consumer (start gates,
        confirm pages, config views, CLI hints, TX tests) reads precedence from here.

        Per identity field: {**rec, "explicit", "effective", "source", "problem"} where
        `source` is "local" | "global" | "missing" and `problem` ("" = fine) is the
        actionable refusal text. Rules:
          * licensed: non-empty local -> local; else a configured global operator callsign
            -> inherited (validated by THIS stack's own validator — a global that this
            stack's protocol cannot carry is a named problem, never silently used);
            else missing.
          * unlicensed: local only — the global operator callsign is NEVER consulted; a
            retired generic default (LoRaHAM Pi/LHPi/pyMC/...) does not count as configured.
        The local field is never populated by inheritance — it stays empty and keeps
        meaning "inherit"."""
        from . import validators
        out = []
        global_call = str(self.config().operator.callsign or "").strip()
        for rec in self._identity_fields(target):
            explicit = self._identity_explicit(target, band, rec, params, file_over)
            validate = validators._NAMED.get(rec["validator"])
            row = dict(rec)
            row.update({"explicit": explicit, "effective": "", "source": "missing",
                        "problem": ""})
            if rec["enforce"] == "licensed":
                cand, src = (explicit, "local") if explicit else (global_call, "global")
                if not cand:
                    row["problem"] = (f"a callsign is required to start '{target}' — set "
                                      f"'{rec['name']}' (or the global operator callsign)")
                elif src == "global":
                    # The GLOBAL is a BASE callsign only. A legacy stored value carrying an
                    # SSID/suffix (e.g. N0CALL-12) must NOT inherit — it would stamp the
                    # SAME SSID onto every licensed stack, the exact problem per-stack
                    # identities exist to solve. Reported with the correction, never
                    # silently used or rewritten (operator ruling). Every derived remedy is
                    # VALIDATED before it is printed (audit-found: N0CALL/8-char legacy
                    # values produced corrections that themselves failed validation).
                    inherited, why = self.inheritable_global(rec)
                    if inherited:
                        row["effective"], row["source"] = inherited, "global"
                    else:
                        remedies = []
                        try:                       # can this stack carry the legacy value AS-IS?
                            if validate:
                                validate(cand)
                            remedies.append("keep per-stack SSIDs local ("
                                            + self._identity_cmd(target, rec, cand, band)
                                            + ")")
                        except validators.ValidationError:
                            pass
                        # ONE base-correction rule, shared with the dashboard card. Never printed
                        # unless the derived value itself validates (audit-found).
                        base = self.operator_callsign_correction()
                        remedies.append(
                            f"set the global to its base (lhpc config operator --callsign {base})"
                            if base else
                            "set the global to your bare base callsign (lhpc config operator "
                            "--callsign YOURCALL — YOURCALL = your own call)")
                        row["problem"] = (f"global operator callsign for '{rec['name']}': "
                                          f"{why} — " + " and ".join(remedies))
                else:
                    try:
                        row["effective"] = validate(cand) if validate else cand
                        row["source"] = "local"
                    except validators.ValidationError as exc:
                        row["problem"] = f"saved value for '{rec['name']}': {exc}"
            else:
                if not explicit or self._is_identity_placeholder(explicit):
                    row["problem"] = (f"'{rec['name']}' is required to start '{target}' — a "
                                      "local node identity (the global operator callsign is "
                                      "never used here)")
                else:
                    try:
                        row["effective"] = validate(explicit) if validate else explicit
                        row["source"] = "local"
                    except validators.ValidationError as exc:
                        row["problem"] = f"saved value for '{rec['name']}': {exc}"
            out.append(row)
        return out

    def _materialize_inherited_identity(self, target: str, band: str, params, file_over):
        """AFTER enforcement approved a start/restart: return (params, file_over) carrying the
        EFFECTIVE value for every licensed identity field that inherits the global operator
        callsign — as EPHEMERAL launch inputs only, never persisted. Without this the argv/
        config-file build reads the stored/submitted (empty) local value, an empty ephemeral
        override masks the '{callsign}' default, and the station launches WITHOUT the
        identity the refusal gate just approved (audit-found, reproduced live)."""
        p = dict(params or {})
        fo = dict(file_over) if file_over is not None else None
        for r in self.identity_resolution(target, band, params, file_over):
            if r["source"] != "global":
                continue
            kind = "run" if r["kind"] == "run" else "file"
            key = self._param_key(target, kind, r["comp"], r["name"])
            if kind == "run":
                p[key] = r["effective"]
            else:
                if fo is None:
                    fo = {}
                fo[key] = r["effective"]
        return p, fo

    def effective_identity(self, target: str, band: str = "") -> str:
        """The stack's primary effective identity value ("" when unresolvable) — for TX-test
        payloads and displays; never a hidden placeholder."""
        rows = self.identity_resolution(target, band)
        return rows[0]["effective"] if rows else ""

    def enforce_identity(self, target: str, band: str = "", params: dict | None = None,
                         file_over: dict | None = None) -> tuple[bool, list, str]:
        """Whether `target` may start given its identity rules. Returns (ok, fields, message):
        `fields` is EVERY confirm input to highlight (Meshtastic has two required names) and
        `message` names every problem. Delegates entirely to `identity_resolution` — the one
        precedence/validation path all consumers share."""
        rows = self.identity_resolution(target, band, params, file_over)
        bad = [r for r in rows if r["problem"]]
        if not bad:
            return (True, [r["field"] for r in rows], "")
        return (False, [r["field"] for r in bad], "; ".join(r["problem"] for r in bad))

    def _form_run_params(self, comp):
        """The component's run params EXPOSED in generic Config / Start parameter forms. HMAC-managed
        params (`password_file`) are excluded — they are edited ONLY through the HMAC Enable/Disable/
        Renew flow, never generic config (a blank generic submission would silently restore open auth)."""
        return [p for p in comp.run_params if not self._is_hmac_managed_param(comp, p)]

    def _stack_param_components(self, target: str):
        """Components whose run/file params make up the editable 'Stack parameters' set (never the
        daemon — its radio params are the separate daemon panel). Target-scoped: a stack target
        exposes all components, a direct component target only itself.

        A `test_fixture` component is skipped for a STACK target, the same rule the optional-
        component list already applies: a stack start never runs the fixture, so its knobs on the
        confirm page were a second, non-functional GPS entry beside the real switch. Named
        DIRECTLY (`lhpc stack start meshcom-gps-relay`) it still exposes its own params — that is
        the deliberate path the manifest documents."""
        if self._is_daemon_target(target):
            return []
        comps = self._target_components(target)
        if self.stack(target):
            comps = [c for c in comps if not c.test_fixture]
        return comps

    def stack_start_params(self, target: str, band: str = "", params: dict | None = None,
                           file_over: dict | None = None) -> list[dict]:
        """Rows for the Start-confirm 'Stack parameters' panel: every editable run + file param of
        the stack (never repo source / operator box / autostart), prefilled with the value that WILL
        be used for this start (ephemeral override, else saved config, else operator-substituted
        default). `field` is the confirm input name (`p_`/`pf_`); `default` is the manifest default
        (for the client Reset-to-defaults button).

        IDENTITY rows are special-cased for truthfulness: their input prefills the EXPLICIT
        local value only (empty = inheriting/missing — never the substituted global, which a
        Save would silently copy into local config), with the effective value and its source
        carried as a placeholder hint + note from `identity_resolution`."""
        # EVERY row — identity, ordinary and dependency — resolves on the band the operation will
        # RUN on, which is also the band it writes to (see `operation_band`). This panel renders
        # the values the browser posts BACK, so a mixed-band form here becomes a mixed-band save.
        op_band = self.operation_band(target, band)
        id_rows = {(r["comp"], r["kind"], r["name"]): r
                   for r in self.identity_resolution(target, op_band, params, file_over)}
        cfg_band = op_band
        rows: list[dict] = []
        for c in self._stack_param_components(target):
            for kind, p in ([("run", p) for p in self._form_run_params(c)]
                            + [("file", p) for p in (c.config_file.params if c.config_file else ())
                               if not getattr(p, "hidden", False)]):
                # Each (component, kind, name) carries its OWN field + API key (bare when unique,
                # component-qualified when the name collides) and its OWN saved value — colliding
                # components never share a field name or flatten into one value.
                idrec = id_rows.get((c.id, kind, p.name))
                # An identity row's FORM default is empty — 'Reset to defaults' must return
                # the local field to "inherit", never inject the substituted global into it
                # (audit-found copy-on-reset).
                default = ("" if idrec else
                           self._op_subst(dict(p.band_defaults).get(cfg_band or band,
                                                                    p.default)))
                saved = (self._stored_param_value(target, kind, c.id, p.name, op_band)
                         if idrec else
                         self._resolved_param_value(target, kind, c.id, p.name, op_band))
                key = self._param_key(target, kind, c.id, p.name)
                field = self._param_field(target, kind, c.id, p.name)
                cur = ((params if kind == "run" else file_over) or {}).get(key, saved)
                is_id = idrec is not None
                # SAVED-ONLY rows render as a pill, never as an input: the GPS switch, and the
                # MeshCore mode rows. A mode change re-classifies which identities exist (the chat
                # name is no identity in `repeater`), so it is made BEFORE a start — the Mode
                # switch on the stack page or Settings → Repeater — never inside the start's
                # persist-and-launch transaction (`_normalize_file_overrides` refuses it there).
                locked = ((kind == "run" and p.name == self.GPS_PARAM)
                          or (kind == "file" and c.id == MESHCORE_COMPONENT
                              and p.name in _MESHCORE_SAVED_ONLY))
                # The switch is stored per STACK (bandless), so the hint must name the stack even
                # when the param is declared on a component (`meshcom-qemu` -> `meshcom`). It
                # points at the WEB path — the operator reading it is already in the web console.
                _owner = self.gps_owner_stack(target) or target
                _owner_name = (self.stack(_owner).name if self.stack(_owner) else _owner)
                hint = (f"saved setting — change it under Apps → {_owner_name} → "
                        "Settings") if locked else ""
                if locked and kind == "file":
                    hint = (f"saved setting — change it with the Mode switch on the "
                            f"{_owner_name} page or under its Settings → Repeater, then start")
                id_hint, id_note = self._identity_note(idrec, cur)
                rows.append(self._param_row(p, field, kind, cur, saved, default, is_id,
                                            c.name, key, c.id, locked, hint,
                                            identity_hint=id_hint, identity_note=id_note))
        rows.extend(self._dep_param_rows(target, band, params, file_over))
        return rows

    def _dep_param_rows(self, target: str, band: str = "", params: dict | None = None,
                        file_over: dict | None = None) -> list[dict]:
        """Start-confirm rows for the DEPENDENCY components a stack start pulls up (kiss under
        graywolf) — EDITABLE exactly like the target's own rows (operator ruling: starting one
        app or a stack of apps must offer the same control; the earlier saved-only rendering
        hid the KISS TX frequency behind another page). The start accepts these as component-
        qualified ephemeral overrides, and Save persists them into the dependency's OWN stack
        config. Fields/keys are ALWAYS component-qualified (`p_<comp>__<name>` / `<comp>.<name>`)
        so they can never collide with the target's own fields; each row carries `dep_stack` so
        the save path routes it to the dependency's config. The GPS switch keeps its saved-only
        rendering — the one-frozen-decision rule is stack-level, the same as everywhere else."""
        rows: list[dict] = []
        # A dependency launches as part of THIS operation, so it follows the same concrete band.
        op_band = self.operation_band(target, band)
        for dep_sid, c in self._dep_param_components(target):
            dep = self.stack(dep_sid)
            dep_band = self._config_band(dep_sid, op_band)
            for kind, p in ([("run", p) for p in self._form_run_params(c)]
                            + [("file", p) for p in (c.config_file.params if c.config_file else ())
                               if not getattr(p, "hidden", False)]):
                default = self._op_subst(dict(p.band_defaults).get(dep_band or op_band, p.default))
                saved = self._resolved_param_value(dep_sid, kind, c.id, p.name, op_band)
                key = f"{c.id}.{p.name}"
                field = f"{'p_' if kind == 'run' else 'pf_'}{c.id}__{p.name}"
                cur = ((params if kind == "run" else file_over) or {}).get(key, saved)
                locked = kind == "run" and p.name == self.GPS_PARAM
                hint = (f"saved setting — change it under Apps → "
                        f"{dep.name if dep else dep_sid} → Settings") if locked else ""
                row = self._param_row(p, field, kind, cur, saved, default, False,
                                      c.name, key, c.id, locked, hint)
                row["dep_stack"] = dep_sid
                rows.append(row)
        return rows

    def start_param_fields(self, target: str, band: str = "") -> list[dict]:
        """Every editable run + file parameter as {component, name, kind ('run'|'file'), field, key,
        flag, saved} — covering BOTH the savable panel params and plain start-option params (e.g. the
        daemon's radio/debug). The single source of truth for the web to read each submitted form
        field into its correctly-scoped API key (bare when unique, `component.name` when duplicated)."""
        out: list[dict] = []
        # ONE concrete operation band for EVERY row (see `operation_band`).
        op_band = self.operation_band(target, band)
        id_keys = {(r["comp"], r["kind"], r["name"]) for r in self._identity_fields(target)}
        for c in self._target_components(target):
            for kind, p in ([("run", p) for p in self._form_run_params(c)]
                            + [("file", p) for p in (c.config_file.params if c.config_file else ())]):
                # IDENTITY fields backfill STORED-ONLY (empty = inherit): the resolved value
                # here becomes the dashboard-POST param, which the confirm form then showed
                # as a local value and Save persisted — silently copying the inherited global
                # into local config (audit-found). Launch-time inheritance is materialized
                # separately, after enforcement.
                ident = (c.id, kind, p.name) in id_keys
                out.append({
                    "component": c.id, "name": p.name, "kind": kind, "flag": p.kind == "flag",
                    "field": self._param_field(target, kind, c.id, p.name),
                    "key": self._param_key(target, kind, c.id, p.name),
                    "saved": (self._stored_param_value(target, kind, c.id, p.name, op_band)
                              if ident else
                              self._resolved_param_value(target, kind, c.id, p.name, op_band))})
        # Dependency components (kiss under graywolf): ALWAYS component-qualified field/key —
        # the same addressing `_dep_param_rows` renders, so the confirm POST parser and the
        # save path read them like every other field.
        for dep_sid, c in self._dep_param_components(target):
            for kind, p in ([("run", p) for p in self._form_run_params(c)]
                            + [("file", p) for p in (c.config_file.params if c.config_file else ())]):
                out.append({
                    "component": c.id, "name": p.name, "kind": kind, "flag": p.kind == "flag",
                    "field": f"{'p_' if kind == 'run' else 'pf_'}{c.id}__{p.name}",
                    "key": f"{c.id}.{p.name}",
                    "saved": self._resolved_param_value(dep_sid, kind, c.id, p.name, op_band),
                    "dep_stack": dep_sid})
        return out

    def stack_start_param_groups(self, target: str, band: str = "", params: dict | None = None,
                                 file_over: dict | None = None) -> list[dict]:
        """The Start-confirm panel rows GROUPED for display: a first 'Required' group with the
        identity (CALL/node) field(s) on top, then one group per component (header = component name)
        with that component's remaining params — rows that carry a manifest `group` (the Settings
        card's sub-heading, e.g. MeshCore's "Repeater") follow under "<component> — <group>", so
        both surfaces show the same headings. [] when the stack has no editable params."""
        rows = self.stack_start_params(target, band, params, file_over)
        groups: list[dict] = []
        required = [r for r in rows if r["is_identity"]]
        if required:
            groups.append({"header": "Required", "rows": required})
        by_comp: dict[str, list] = {}
        order: list[str] = []
        for r in rows:
            if r["is_identity"] or r.get("dep_stack"):
                continue
            header = r["comp_name"] + (f" — {r['group']}" if r.get("group") else "")
            if header not in by_comp:
                by_comp[header] = []
                order.append(header)
            by_comp[header].append(r)
        # Right after Required: the group holding saved-only rows (MeshCore's "Repeater", where
        # the Mode lives), then the other named groups, then the plain rows — the operator sees
        # the mode before the long tail of chat rows.
        order.sort(key=lambda h: (0 if " — " in h and any(r["locked"] for r in by_comp[h])
                                  else 1 if " — " in h else 2))
        for name in order:
            groups.append({"header": name, "rows": by_comp[name]})
        # Dependency stacks the run order pulls up render AFTER the target's own groups, one
        # group per dependency stack — editable like everything else (see _dep_param_rows).
        dep_by: dict[str, list] = {}
        dep_order: list[str] = []
        for r in rows:
            dsid = r.get("dep_stack") or ""
            if not dsid:
                continue
            if dsid not in dep_by:
                dep_by[dsid] = []
                dep_order.append(dsid)
            dep_by[dsid].append(r)
        for dsid in dep_order:
            dep = self.stack(dsid)
            groups.append({"header": f"{dep.name if dep else dsid} (dependency)",
                           "rows": dep_by[dsid]})
        return groups

    def _identity_note(self, idrec: dict | None, value: str) -> tuple:
        """`(hint, note)` for one identity row — ONE wording used by BOTH the Settings page and
        the Start/Restart panel. They had drifted, each being right about one thing: Settings
        only claimed "overrides the global" when a value was actually present, while the panel
        alone surfaced an unlicensed field's problem text. Both behaviours are kept here."""
        if not idrec:
            return "", ""
        value = (value or "").strip()
        if idrec["enforce"] != "licensed":
            return "", (idrec["problem"] if (idrec["problem"] and value) else
                        "local node identity — required; never the operator callsign")
        if idrec["source"] == "global" and not value:
            return (f"inherits global {idrec['effective']}",
                    f"empty = inherits the global operator callsign ({idrec['effective']})")
        if idrec["problem"]:
            return "", idrec["problem"]
        if not value:
            return "", ""
        # Only claim an override when there is actually a global to override (review-found).
        return "", ("overrides the global operator callsign"
                    if str(self.config().operator.callsign or "").strip() else "")

    @staticmethod
    def _param_row(p, field: str, kind: str, value, saved: str, default: str, is_identity: bool,
                   comp_name: str = "", key: str = "", component: str = "",
                   locked: bool = False, locked_hint: str = "",
                   identity_hint: str = "", identity_note: str = "") -> dict:
        # `locked` = SAVED-ONLY: shown so the operator knows what this start will use, but with no
        # form input at all, so the page cannot post it. It is the only honest rendering — the value
        # is refused as a per-start override, so an editable control offered a change that turned
        # into a failed start, and the row still had to appear or the setting was invisible.
        return {"field": field, "name": p.name, "key": key, "component": component,
                "kind": p.kind, "comp_name": comp_name,
                "group": getattr(p, "group", "") or "",      # the Settings card's sub-heading
                "locked": bool(locked), "locked_hint": locked_hint,
                "choices": list(p.choices), "label": p.label or p.name,
                "value": "" if value is None else str(value),
                "config_value": "" if saved is None else str(saved), "default": default,
                "advanced": bool(getattr(p, "advanced", False)),
                "validator": getattr(p, "validator", ""),
                "is_identity": bool(is_identity),
                "identity_hint": identity_hint, "identity_note": identity_note,
                "min": getattr(p, "min", None), "max": getattr(p, "max", None)}

    def _normalize_file_overrides(self, target: str, raw: dict | None) -> tuple[dict, str]:
        """Validate ephemeral file-config overrides ({name: value}) against the TARGET's FileParams
        (a stack's whole set, or a direct component's own). Returns (clean, err); a non-mapping
        payload, an UNKNOWN name, or an invalid value is a TYPED start failure — never silently
        discarded. A blank NON-FLAG value is treated as ABSENT (skipped -> the base/preset default
        applies), mirroring `update_toml`'s blank rule — so e.g. leaving the Start-page Frequency
        field empty lets the selected RF preset own the frequency instead of failing int validation.
        Flags pass through (blank = off)."""
        if raw is None:
            return {}, ""
        if not isinstance(raw, dict):
            return {}, "file overrides must be a mapping"
        clean: dict[str, str] = {}
        for key, val in raw.items():
            _c, p, err = self._param_ref(target, "file", key)      # rejects unqualified duplicates
            if err:
                return {}, f"unknown file parameter {key!r}" if err.startswith("unknown") else err
            if getattr(p, "kind", "") != "flag" and str(val).strip() == "":
                continue                # blank non-flag -> no override (base/preset default wins)
            if _c is not None and _c.id == MESHCORE_COMPONENT and p.name in _MESHCORE_SAVED_ONLY:
                # The mode rows are SAVED settings only (same rule as `use_gps`): readiness,
                # status, identity enforcement and the client seeding all read the saved state,
                # so a per-launch override would make the launch and the controller disagree.
                # No surface posts them for a launch (the confirm page shows them as pills).
                return {}, (f"{p.name} cannot be changed for a single start — it is a saved "
                            f"setting (lhpc config {_meshcore_mode.STACK_ID} {p.name} <value>)")
            try:
                clean[key] = validators.validate_param(p, str(val))
            except validators.ValidationError as exc:
                return {}, f"{p.name}: {exc}"
        return clean, ""

    def _normalize_run_params(self, target: str, raw) -> tuple[dict | None, str]:
        """THE authoritative validation/canonicalisation boundary for ordinary ephemeral run params
        — parallel to `_normalize_ephemeral_overrides` (daemon) and `_normalize_file_overrides`
        (file). Validates each supplied value against the TARGET's own run params (a stack's whole
        exposed set, or a direct component's own) and returns (clean, err). `None` passes through
        (means: use saved config). A non-mapping payload, an UNKNOWN parameter name, or an invalid
        value is a TYPED failure — a requested override is NEVER silently ignored."""
        if raw is None:
            return None, ""
        if not isinstance(raw, dict):
            return {}, "run parameters must be a mapping"
        clean: dict[str, str] = {}
        for key, val in raw.items():
            _c, p, err = self._param_ref(target, "run", key)       # rejects unqualified duplicates
            if err:
                return {}, f"unknown parameter {key!r}" if err.startswith("unknown") else err
            if self._is_hmac_managed_param(_c, p):                  # no ephemeral bypass of the gate
                return {}, self._HMAC_MANAGED_PARAM_MSG
            # The GPS switch is PERSISTED only. Accepting a DIFFERENT value per launch would
            # let a start run with GPS on while the saved state says off — and the resource
            # claims, the generated config and the post-start push all come from the SAVED
            # state, so the launch and what the box actually holds would disagree.
            #
            # An echo of the CURRENT value is not an override: the console's start form posts
            # every parameter it renders, so rejecting the value outright blocked every start
            # from the web. Only a real change is refused.
            if p.name == "use_gps":
                current = "on" if self.gps_enabled_for(target) else "off"
                if str(val).strip().lower() == current:
                    continue
                return {}, ("use_gps cannot be changed for a single start — it is a saved "
                            f"setting (lhpc config {target} use_gps on|off)")
            try:
                clean[key] = validators.validate_param(p, val)
            except validators.ValidationError as exc:
                return {}, f"{p.name}: {exc}"
        return clean, ""

    def _preflight_start_inputs(self, target: str, band: str, params, file_overrides, op: str):
        """Validate ALL supplied start inputs — ordinary `params`, file overrides, and CALL/node
        identity (using the canonicalized ephemeral values + owner-stack persisted values) — BEFORE
        any lifecycle side effect. Returns (params_canon, file_canon, err) where `err` is a TYPED
        failed ActionResult (or None on success). Used by `restart()` before lock planning/stop and
        by `_restart_impl()` before its `stop()`; a non-mapping/unknown/invalid input NEVER raises,
        acquires a lock, or stops/alters any process/marker/config/daemon/owner."""
        params, pv_err = self._normalize_run_params(target, params)
        if pv_err:
            return None, None, ActionResult(
                False, f"Cannot {op} '{target}': invalid parameter — {pv_err}",
                next_commands=[f"lhpc status {target}"])
        file_over, fo_err = self._normalize_file_overrides(target, file_overrides)
        if fo_err:
            return None, None, ActionResult(
                False, f"Cannot {op} '{target}': invalid parameter — {fo_err}",
                next_commands=[f"lhpc status {target}"])
        id_ok, id_fields, id_msg = self.enforce_identity(target, band, params, file_over)
        if not id_ok:
            return None, None, ActionResult(
                False, f"Cannot {op} '{target}': {id_msg}", data={"enforce_fields": id_fields},
                next_commands=self._identity_config_hints(target, band))
        params, file_over = self._materialize_inherited_identity(target, band, params, file_over)
        return params, file_over, None

    def write_config_files(self, target: str, band: str = "",
                           overrides: dict | None = None,
                           position: dict | None = None) -> list[ConfigWrite]:
        """(Re)generate every file-config component's config file from the stored
        (per-band) values. Returns a STRUCTURED result per component (written /
        linked-readonly / no-base / failed) so an auto-start can block on a generation
        failure rather than silently launching with stale or absent configuration.

        `overrides` are EPHEMERAL per-start file values ({param_name: value}, this launch only,
        never persisted) taken from the Start-confirm 'Stack parameters' panel — validated by the
        caller via `_normalize_file_overrides` before they reach here.

        `position` is MeshCore's STATIC position ({"lat": …, "lon": …}) from
        `meshcore_position`, set only for a `fixed` source. Absent or empty means "no static
        position" — a live source is fed by the meshcore-gps bridge instead — and the
        coordinate keys are then omitted, which also strips a stale pair from an earlier start."""
        cfg = self.config()
        op = cfg.operator
        runtime = str(self._paths.runtime_root)
        # Secrets are a SEPARATE trust layer from local.toml — read only here, and
        # only if a parameter actually declares one: an unsafe or unreadable
        # secrets.toml must not break generation for a stack that has no secrets.
        _secrets_box = {}

        def secrets():
            if "v" not in _secrets_box:
                _secrets_box["v"] = load_secrets(self._paths)
            return _secrets_box["v"]
        cfg_band = self._config_band(target, band)
        # INJECTION guard only (not identity policy — enforcement already refused a start
        # that needs a callsign this box does not have): a corrupted local.toml value that no
        # callsign shape accepts must never reach a generated config file. Empty stays empty —
        # a licensed identity param then renders its own explicit local value or nothing,
        # never a hidden placeholder.
        call = str(op.callsign or "").strip()
        if call and not re.fullmatch(r"[A-Za-z0-9/-]{1,11}", call):
            call = ""

        # The radio hardware setup is a GLOBAL controller setting, not a per-stack
        # parameter: an operator picks the board with `lhpc hardware`, and the stack's
        # pins/chip follow from it. Injecting it here keeps it out of reach of saved or
        # ephemeral user values.
        hardware = str(getattr(getattr(cfg, "radio", None), "hardware", "") or "")

        # The GPS device a consumer should read, derived from the ONE resolved plan — the same
        # object lifecycle planning and the resource claims use, so a rendered config can never
        # describe a source the lifecycle did not plan for. Empty when GPS is off or when the
        # consumer needs no device (`fixed` uses the app's own fixed-position support), and the
        # param is `omit_if_empty`, so the key then vanishes rather than being written blank.
        gps_values = self._gps_config_values(target)

        def subst(text: str) -> str:
            out = (text.replace("{callsign}", call)
                       .replace("{runtime}", runtime)
                       .replace("{hardware}", hardware)
                       .replace("{band}", cfg_band))    # for per-band config keys
            if "{gps_" in out:
                for token, value in gps_values.items():
                    out = out.replace(token, value)
            return out

        stored = self.file_config_values(target, band)
        over = overrides or {}
        written: list[ConfigWrite] = []
        for c in self._file_config_components(target):
            fc = c.config_file
            # Validate every stored value against its FileParam; an invalid value
            # (e.g. hand-edited TOML) reverts to the manifest default — never written
            # raw into the app's config file. An ephemeral override (already validated by
            # `_normalize_file_overrides`) takes precedence for THIS launch only.
            values = {}
            secret_error = ""
            # The MeshCore mode this render describes — the SAVED mode (a per-launch override of
            # it is refused by `_normalize_file_overrides`), decided ONCE before the rows because
            # the identity row precedes the mode row in manifest order.
            rendered_mode = ""
            if c.id == MESHCORE_COMPONENT:
                rendered_mode = _meshcore_mode.normalize(
                    stored.get("mode", _meshcore_mode.DEFAULT_MODE))
            for p in fc.params:
                if getattr(p, "secret_ref", ""):
                    # Separate trust layer: never `over`, never `stored`, never a default.
                    table, _, key = p.secret_ref.partition(".")
                    try:
                        loaded = secrets()
                    except ConfigError as exc:
                        # A refused secrets file is a config-generation FAILURE, not an
                        # exception escaping to the caller: the start boundary catches
                        # OSError/PathContainmentError only, so this used to surface as
                        # a traceback (web 500) instead of a blocked, typed result.
                        secret_error = str(exc)
                        break
                    val = (loaded.get(table) or {}).get(key)
                    if not isinstance(val, str) or not val.strip():
                        # NEVER fall back to an empty value — that would silently ship an
                        # unauthenticated interface that looks configured. Either omit the
                        # key entirely (opt-in feature left off) or block the write.
                        if getattr(p, "omit_if_empty", False):
                            values[p.name] = ""
                            continue
                        secret_error = (f"required secret [{table}] {key} is missing or empty in "
                                        f"config/secrets.toml")
                        break
                    values[p.name] = val
                    continue
                if p.hidden and p.name in _POSITION_PARAMS and c.id == MESHCORE_COMPONENT:
                    # CONTROLLER-OWNED: only the start's snapshot may set these. Reading
                    # `over`/`stored` here would let a per-start override or a hand-edited
                    # stack config put a position on the air that the global plan never
                    # authorised. Empty -> `omit_if_empty` drops the key (and any stale one).
                    values[p.name] = str((position or {}).get(p.name, "") or "")
                    continue
                if getattr(p, "secret_file", ""):
                    # CONTROLLER-managed secret (vs secret_ref's operator-authored
                    # secrets.toml). Same trust rule: never `over`, never `stored`, never a
                    # default. The MeshCore node identity must EXIST by generation time — this
                    # is the one call site allowed to mint it. Each secret is resolved only when
                    # the rendered mode runs the node that owns it: the REPEATER's key and
                    # password only in the repeater modes, the CHAT identity only when the
                    # Companion runs. A mode with no use for a secret neither mints it nor can be
                    # blocked by a fault in its file — the same rule as the chat name, GPS and
                    # the clients: what does not run does not gate the start.
                    if c.id == MESHCORE_COMPONENT and (
                            (p.secret_file in _REPEATER_SECRETS
                             and not _meshcore_mode.repeater_on(rendered_mode))
                            or (p.secret_file == _IDENTITY_SECRET
                                and not _meshcore_mode.chat_identity_required(rendered_mode))):
                        values[p.name] = ""            # blank -> the base is left untouched
                        continue
                    try:
                        values[p.name] = self._controller_secret(p.secret_file)
                    except (OSError, PathContainmentError, ValueError) as exc:
                        secret_error = str(exc)
                        break
                    continue
                raw = over.get(p.name, stored.get(p.name, p.default))
                try:
                    values[p.name] = validators.validate_param(p, raw)
                except validators.ValidationError:
                    values[p.name] = p.default
            if secret_error:
                # The message names the LOCATION of the secret, never its value.
                written.append(ConfigWrite(c.id, str(fc.path), "failed", secret_error))
                continue
            # THREE explicit destination policies (P1 generated-config containment):
            dest = self._resolve_config_dest(c, fc.path)
            if dest.status != "ok":
                written.append(ConfigWrite(c.id, dest.detail_path, dest.status, dest.detail))
                continue
            out_path = dest.path
            if fc.fmt in ("toml-update", "yaml-update", "ini-update"):
                base = self._resolve_config_dest(c, fc.base, for_base=True)
                if base.status != "ok":
                    written.append(ConfigWrite(c.id, base.detail_path,
                                   "failed" if base.status != "linked-readonly" else base.status,
                                   base.detail))
                    continue
                try:
                    base_text = self._read_contained(c, base)
                except (OSError, PathContainmentError) as exc:
                    written.append(ConfigWrite(c.id, str(base.path), "no-base", str(exc)))
                    continue
                updater = {"toml-update": update_toml, "yaml-update": update_yaml,
                           "ini-update": update_ini}[fc.fmt]
                try:
                    text = updater(base_text, fc.params, values, subst)
                except ValueError as exc:
                    written.append(ConfigWrite(c.id, str(out_path), "failed", str(exc)))
                    continue
            elif fc.fmt == "env":
                # KEY=value (no spaces, no header) for split-on-'=' parsers.
                text = render_keyval(fc.params, values, subst, sep="=", comment=False)
            else:
                text = render_keyval(fc.params, values, subst)
            try:
                if dest.policy == "runtime":
                    runtime_fs.atomic_write(self._paths, out_path, text, fc.mode)
                else:
                    # Pass the RELATIVE path so containment is proven component-by-
                    # component before any directory is created (P1.1).
                    self._write_source_config(c, dest.detail_path, text)
                written.append(ConfigWrite(c.id, str(out_path), "written"))
            except (OSError, PathContainmentError) as exc:
                # NEVER silently continue — a config we could not write must be visible
                # so an auto-start can block rather than launch with stale/absent config.
                written.append(ConfigWrite(c.id, str(out_path), "failed", str(exc)))
        return written

    def _resolve_config_dest(self, c, raw: str, for_base: bool = False):
        """Resolve a FileConfig path/base into one of four policies:
          * asset    — `{asset}/...`: a template SHIPPED as lhpc package data (bases only);
          * runtime  — `{runtime}/...` only, resolved through `Paths.under` (containment);
          * source   — a RELATIVE path under the managed source root (rejects linked);
          * (reject) — an arbitrary absolute path, unknown placeholder, or traversal.
        Returns a small result with `.status` ("ok"/"failed"/"linked-readonly"),
        `.policy`, `.path`, `.detail`, `.detail_path`."""
        from types import SimpleNamespace
        if raw == "{asset}" or raw.startswith("{asset}/"):
            # A packaged base needs no clone and no seeding: it is read from lhpc's own data
            # at GENERATION time, so it can never go stale against the installed version (a
            # bootstrap-seeded copy would, since seeding preserves an existing file). Only a
            # BASE may be an asset — package data is read-only, never a generated destination.
            rel = raw[len("{asset}"):].lstrip("/")
            parts = [p for p in rel.split("/") if p]
            if not for_base:
                return SimpleNamespace(status="failed", policy="reject", path=None, detail_path=raw,
                                       detail="{asset} is read-only package data — it cannot be a "
                                              "generated config destination")
            if not parts or any(p in ("..", ".") for p in parts):
                return SimpleNamespace(status="failed", policy="reject", path=None, detail_path=raw,
                                       detail="unsafe packaged-base path")
            return SimpleNamespace(status="ok", policy="asset", path=Path("/".join(parts)),
                                   detail="", detail_path=raw)
        if raw == "{runtime}" or raw.startswith("{runtime}/"):
            rel = raw[len("{runtime}"):].lstrip("/")
            parts = [p for p in rel.split("/") if p]
            try:
                p = self._paths.under(*parts) if parts else self._paths.runtime_root
            except PathContainmentError as exc:
                return SimpleNamespace(status="failed", policy="runtime", path=None,
                                       detail=f"runtime path escapes root: {exc}", detail_path=raw)
            return SimpleNamespace(status="ok", policy="runtime", path=p, detail="", detail_path=raw)
        if raw.startswith(("/", "{")) or ".." in raw.split("/"):
            return SimpleNamespace(status="failed", policy="reject", path=None, detail_path=raw,
                                   detail="config path must be {runtime}/... or a relative source path")
        # relative -> managed source destination
        if not c.source:
            return SimpleNamespace(status="failed", policy="reject", path=None, detail_path=raw,
                                   detail="a relative config path requires a managed source")
        if not for_base and self._lifecycle().is_linked_source(c):
            return SimpleNamespace(status="linked-readonly", policy="source", path=None,
                                   detail="linked source is read-only — generate config in your checkout",
                                   detail_path=raw)
        src_dir = self._paths.resolve_source(c.source.path)
        return SimpleNamespace(status="ok", policy="source", path=src_dir / raw,
                               detail="", detail_path=raw)

    def _read_contained(self, c, dest) -> str:
        """Read a config base safely: a packaged base via importlib.resources (zip-safe, and
        outside the runtime root by construction — no traversal to guard); a runtime base via
        runtime_fs (no-follow); a managed source base via the SAME descriptor-anchored,
        O_NOFOLLOW traversal as the writer (no check-then-open — a base file or parent
        swapped to a symlink after a check cannot be followed)."""
        if dest.policy == "asset":
            from .assets import asset_text
            return asset_text(str(dest.path))
        if dest.policy == "runtime":
            from . import runtime_fs
            return runtime_fs.read_text(self._paths, dest.path)
        return self._read_source_base(c, dest.detail_path)

    @contextmanager
    def _open_source_parent(self, c, rel_path: str, *, create: bool):
        """Descriptor-anchored descent under the managed source root, immune to a symlink-
        swap race: each path component is opened RELATIVE TO ITS PARENT fd with
        `O_DIRECTORY|O_NOFOLLOW` (a component that is — or was just swapped to — a symlink
        or non-directory is refused at the syscall). With `create=True` intermediate dirs
        are created one component at a time. Yields (parent_fd, leaf_name)."""
        # Walk EVERY component from the runtime root — including the source path's own
        # parts (`src`, `<comp>`) — each opened O_NOFOLLOW relative to its parent fd, so a
        # symlink swapped in at an INTERMEDIATE dir (e.g. `src` -> /elsewhere) is refused
        # at the syscall. Opening the pre-resolved source root in one os.open would guard
        # only its final component and follow such an intermediate symlink (P1 escape).
        src_parts = [p for p in Path(c.source.path).parts if p not in ("", ".")]
        leaf_parts = [p for p in Path(rel_path).parts if p not in ("", ".")]
        parts = src_parts + leaf_parts
        if not leaf_parts or any(p in ("..", "/") for p in parts):
            raise PathContainmentError(f"unsafe source config path: {rel_path!r}")
        # The runtime ROOT is the trusted anchor — it may itself legitimately be a symlink
        # in the operator's setup (mirror runtime_fs._walk_parent), so it is NOT opened
        # O_NOFOLLOW; every component UNDER it (incl. `src`) is.
        fds = [os.open(str(self._paths.runtime_root), os.O_RDONLY | os.O_DIRECTORY)]
        try:
            for comp in parts[:-1]:
                if create:
                    try:
                        os.mkdir(comp, 0o755, dir_fd=fds[-1])
                    except FileExistsError:
                        pass
                try:
                    fds.append(os.open(comp, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                       dir_fd=fds[-1]))
                except OSError as exc:
                    raise PathContainmentError(
                        f"source config path component {comp!r} is a symlink or not a "
                        f"directory: {exc}") from exc
            yield fds[-1], parts[-1]
        finally:
            for fd in reversed(fds):
                try:
                    os.close(fd)
                except OSError:
                    pass

    def _read_source_base(self, c, rel_path: str) -> str:
        """Read a managed-source base file with O_NOFOLLOW at the leaf, anchored to its
        parent directory fd — no check-then-open."""
        with self._open_source_parent(c, rel_path, create=False) as (parent_fd, leaf):
            fd = os.open(leaf, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent_fd)
            with os.fdopen(fd, "rb") as fh:
                return fh.read().decode("utf-8")

    def _write_source_config(self, c, rel_path: str, text: str) -> None:
        """Atomically write a generated config into the managed SOURCE tree via the shared
        `runtime_fs.atomic_write`: a full parent NO-FOLLOW walk from the runtime root (a
        symlink swapped in at ANY component, incl. `src`, is refused at the syscall), a
        UNIQUE `O_EXCL`+random-nonce temp, mode-set, atomic rename, and a parent fsync for
        durability (AUDIT FS3 — the old hand-rolled temp reused a `pid`-named, non-`O_EXCL`
        leaf that two waitress threads writing the same file could corrupt, and skipped the
        parent fsync). Linked external sources fail the no-follow walk and are refused."""
        from . import runtime_fs
        leaf_parts = [p for p in Path(rel_path).parts if p not in ("", ".")]
        if not leaf_parts or any(p in ("..", "/") for p in leaf_parts):
            raise PathContainmentError(f"unsafe source config path: {rel_path!r}")
        target = self._paths.resolve_source(c.source.path)
        for p in leaf_parts:
            target = target / p
        runtime_fs.atomic_write(self._paths, target, text, 0o644)

    def reset_config(self, target: str, band: str = "") -> ActionResult:
        """Reset a stack/band's NORMAL Config-page settings (run params, file config, autostart)
        to defaults. Owns ONLY those keys — daemon-profile `dp_*` overrides, another band's
        overrides, and unrelated manual scalars are PRESERVED (use the daemon panel's own Reset
        for `dp_*`)."""
        if self.stack(target) is None:
            return self._unknown_stack(target)
        from . import validators
        from .paths import PathContainmentError
        cfg_band = self._config_band(target, band)
        label = f"'{target}'" + (f" ({cfg_band})" if cfg_band else "")
        run_names = {p.name for p in self.run_params_for(target)}
        try:
            stored = load_stack_config(self._paths, target, cfg_band)
            normal = [k for k in stored
                      if k in run_names or k.startswith(("file_", "autostart_", "__r__", "__f__"))]
            if normal:
                # Clear ONLY the normal-owned keys under the config lock; dp_* + unrelated stay.
                update_stack_config(self._paths, target, dict.fromkeys(normal, ""), cfg_band)
                self._invalidate_config()
        except (ConfigError, PathContainmentError, validators.ValidationError, OSError) as exc:
            return ActionResult(False, f"Config reset blocked for {label}: unsafe/malformed "
                                f"config (refused, not modified): {exc}")
        return ActionResult(True,
                            f"Config reset to defaults for {label}." if normal
                            else f"{label} already at defaults.",
                            next_commands=[f"lhpc stack start {target}"])

    def _daemon_feed_source(self, band: str) -> Path | None:
        """The SINGLE feed source log for a band, or None. EXACTLY ONE file, never a concatenation:
        the per-band log `logs/start-<daemon>-<band>.log` when it exists, else the legacy band-less
        `start-<daemon>.log` (a pre-upgrade daemon not restarted since the rename). Reading both would
        double-count, because the band-agnostic tokens ([TX], [RX], TXOK, ...) match either band."""
        for name in (f"start-{self.DAEMON_ID}-{band}.log", f"start-{self.DAEMON_ID}.log"):
            try:
                p = self._paths.under("logs", name)
            except PathContainmentError:
                continue
            if p.is_file():
                return p
        return None

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

    def daemon_set(self, band: str, key: str, value: str, apply: bool = False) -> ActionResult:
        # Validate the band at the service boundary too (not only in web routes): a
        # direct CLI/service caller must never reach a constructed arbitrary socket path.
        if not daemon_control.is_valid_band(band):
            return ActionResult(False, f"Invalid band '{band}' (allowed: "
                                f"{', '.join(daemon_control.ALLOWED_BANDS)}).")
        err = daemon_control.validate_set(key, value)
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
