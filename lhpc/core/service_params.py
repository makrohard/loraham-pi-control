"""Param & config resolution, saves, config-file generation, and daemon-parameter application.

Mixin of ControllerService (state/constants on the facade). Adapters import lhpc.core.services only."""
from __future__ import annotations

import os
import time
from contextlib import contextmanager
from pathlib import Path

from .snapshot_memo import invalidates_snapshot
from . import daemon_control
from . import runtime_fs
from . import validators
from .lifecycle import GROUP_MISSING_HINT, GROUP_RESTART_HINT
from .config import (
    ConfigError,
    apply_config_transaction,
    merge_stack_values,
    _patch_local_table,
    render_local_tables,
    render_stack_config,
    update_stack_config,
    _load_runtime_toml,
    _stack_config_path,
    load_stack_config,
    render_keyval,
    update_toml,
    update_yaml,
)
from .model import ComponentKind, RunState
from .paths import PathContainmentError
from .service_base import ActionResult, ConfigWrite


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
        from . import daemon_params, config as cfgmod
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
        idf = self._identity_field(target)
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
                default = self._op_subst(dict(p.band_defaults).get(cfg_band or band, p.default))
                value = self._resolved_param_value(target, kind, c.id, p.name, cfg_band)
                field = self._config_field(target, kind, c.id, p.name)
                key = self._param_key(target, kind, c.id, p.name)
                is_id = bool(idf and idf["comp"] == c.id and idf["name"] == p.name
                             and idf["kind"] == kind)
                row = self._param_row(p, field, kind, value, value, default, is_id,
                                      c.name, key, c.id)
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
                return None, None, f"unknown {kind} parameter {key!r}"
            return c, p, None
        owners = [(c, _pget(c, key)) for c in comps if _pget(c, key) is not None]
        if not owners:
            return None, None, f"unknown {kind} parameter {key!r}"
        if len(owners) == 1:
            return owners[0][0], owners[0][1], None
        return None, None, (f"{kind} parameter {key!r} is declared by multiple components — qualify "
                            f"it as '<component>.{key}'")

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

    def _resolved_param_value(self, target: str, kind: str, comp_id: str, name: str,
                              band: str = "") -> str:
        """The component's OWN persisted value (component-scoped, else a UNIQUE flat legacy) or its
        operator-substituted default — never masked by a same-named sibling."""
        cfg_band = self._config_band(target, band)
        owner = self._owner_stack(target)
        stored = load_stack_config(self._paths, self._owner_stack_id(target), cfg_band)
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
        from . import selfupdate, manifest as manifest_mod
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
        `mandatory` = required by a non-optional component (mandatory wins on dedup)."""
        life = self._lifecycle()
        s = self.stack(target)
        out, by_key = [], {}
        for c in (s.components if s else ()):
            missing = life.missing_requirements(c)
            for req in c.requires:
                key = req.install or req.cmd or req.check_file or req.absent_file
                if not key:
                    continue
                if key in by_key:
                    # A dep shared by several components is mandatory if ANY requiring
                    # component is non-optional (mandatory wins over an optional sibling).
                    by_key[key]["mandatory"] = by_key[key]["mandatory"] or not c.optional
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
                         # a MANAGED tool provisioned into the runtime root by the build/setup step does
                         # not exist until `lhpc build`, so it must NOT block install (still gates start).
                         "provisioned": bool(req.provisioned),
                         "mandatory": not c.optional}
                by_key[key] = entry
                out.append(entry)
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
        optional = [{"id": c.id, "name": c.name, "purpose": c.purpose,
                     "autostart": stored.get(f"autostart_{c.id}") == "on",
                     "running": live.get(c.id) in up}
                    for c in members if c.optional
                    and c.kind not in (ComponentKind.LIBRARY, ComponentKind.FIRMWARE)]
        main = s.main_component if s else None
        # Operator identity is only relevant to stacks that actually substitute
        # {callsign} into a run/pre command (e.g. iGate) — not the daemon.
        # Stacks that edit their callsign in their own config (operator_box=false) don't show the
        # shared Operator box — the callsign lives in their config/run params instead.
        uses_operator = (s is not None and s.operator_box) and any(
            tok in (c.run_cmd or "") or tok in (c.pre_cmd or "")
            or any(tok in (p.default or "") for p in c.run_params)
            or any(tok in (p.default or "") for p in (c.config_file.params if c.config_file else ()))
            for c in members for tok in ("{callsign}",))
        sources = [{"id": c.id, "name": c.name,
                    "remote": cfg.remotes.get(c.id) or c.source.remote,
                    "default": c.source.remote,
                    "overridden": c.id in cfg.remotes}
                   for c in members if c.source and c.source.remote]
        bands = self.stack_bands(target)
        cfg_band = self._config_band(target, band)
        view = {
            "operator": ({"callsign": cfg.operator.callsign}
                         if uses_operator else None),
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
                    callsign: str | None = None,
                    band: str = "") -> ActionResult:
        """Save operator identity (if supplied) and the stack's run parameters as ONE
        all-or-recoverable transaction (via `save_config_bundle`): if the stack config fails to
        validate/persist, operator identity is NOT partially written."""
        return self.save_config_bundle(target, values=values, callsign=callsign,
                                       band=band)

    @invalidates_snapshot
    def save_config_bundle(self, target: str, *, values: dict | None = None,
                           callsign: str | None = None,
                           band: str = "", remotes: dict | None = None,
                           _allow_managed_params: frozenset = frozenset()) -> ActionResult:
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
        op_change = callsign is not None
        cs = None
        if op_change:
            try:
                cs = validators.callsign(callsign or "", field="callsign").upper()
            except validators.ValidationError as exc:
                errors.append(str(exc))
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
        if op_change or remotes is not None:
            def _render_local(p, opc=op_change, _cs=cs, patch=remote_patch,
                              do_remotes=(remotes is not None)):
                # Read the LATEST local.toml INSIDE the transaction lock and MERGE — preserving
                # every unrelated table and every other component's remote override. A malformed
                # local.toml raises here -> the transaction rolls back and preserves it.
                existing = _load_runtime_toml(self._paths, local_path)   # no-follow; ConfigError on corrupt
                data = dict(existing)                     # keep root scalars + every other table
                if opc:
                    # Patch ONLY callsign — preserve any other [operator] scalar keys.
                    _patch_local_table(data, "operator", {"callsign": _cs})
                if do_remotes:
                    # Patch owned component keys only (None clears); other remotes preserved. A
                    # non-table `operator`/`remotes` value is rejected here -> transaction rollback.
                    _patch_local_table(data, "remotes",
                                       {vid: (vurl or None) for vid, vurl in patch.items()})
                return render_local_tables(data)          # type-safe, preserves root scalars
            targets.append(("local", local_path, _render_local, 0o600))
        cfg_band = self._config_band(target, band)
        # Apply-mode hints (restart/build) from CHANGED run AND file params, compared per-component
        # against the PRE-SAVE effective value (never masked by a same-named sibling), BEFORE the write.
        modes = {p.apply_mode for kind, c, p, v in clean_params
                 if self._resolved_param_value(target, "run" if kind == "r" else "file",
                                               c.id, p.name, band) != str(v)}
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
            if str(v) == self._param_default_canon(p, cfg_band, band):
                to_remove.add(key)                                          # at default -> not persisted
            else:
                to_set[key] = v                                             # override -> persisted
        # The stack file is written as a MERGE rendered INSIDE the transaction lock: overlay the
        # override keys (keeping daemon-profile dp_*, other bands + unrelated manual scalars), then
        # drop the at-default keys. A raise here (unsupported manual value) rolls the transaction back.
        def _render_stack(pth, tgt=sid, b=cfg_band, setv=to_set, rmv=to_remove):
            merged = merge_stack_values(pth, tgt, b, setv, clear_empty=False)
            for k in rmv:
                merged.pop(k, None)
            return render_stack_config(tgt, merged)
        targets.append(("stack", _stack_config_path(self._paths, sid, cfg_band), _render_stack, 0o644))
        if (auto_set or auto_remove) and cfg_band:
            # SECOND transactional target: autostart flags land in the band-less file.
            def _render_auto(pth, tgt=sid, setv=dict(auto_set), rmv=set(auto_remove)):
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
        marker_modes = modes & {"restart", "build"}
        if marker_modes and self.stack_running(target):
            import json as _json
            payload = _json.dumps({
                "version": 1, "stack": sid,
                "mode": "build" if "build" in marker_modes else "restart",
                "params": sorted(
                    p.name for kind, c, p, v in clean_params
                    if p.apply_mode in marker_modes
                    and self._resolved_param_value(target, "run" if kind == "r" else "file",
                                                   c.id, p.name, band) != str(v)),
                "band": cfg_band, "created_at": time.time()})
            targets.append(("state", self._restart_marker_path(sid), payload, 0o600))
        try:
            if self._holds_config_exclusive():
                # Inside the auto-install auto-install boundary this thread ALREADY holds the config lock EXCLUSIVELY
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
                            details=self._apply_hints(target, modes) + remote_notes,
                            next_commands=[f"lhpc stack start {target}"])

    def set_operator_identity(self, callsign: str | None = None) -> ActionResult:
        """Set the GLOBAL operator identity (`[operator]` in local.toml) — the shared callsign every
        licensed stack inherits via each identity param's `{callsign}` default. An explicit empty
        string clears it. Validates format; preserves any other `[operator]` scalar keys."""
        from . import config as _config
        from .validators import callsign as _v_call, ValidationError
        if callsign is None:
            return ActionResult(False, "nothing to set — pass --callsign")
        try:
            new_call = _v_call(callsign).upper()
        except ValidationError as exc:
            return ActionResult(False, f"invalid operator identity: {exc}")
        try:
            _config.save_operator_config(self._paths, new_call)
        except (OSError, _config.ConfigError) as exc:
            return ActionResult(False, f"could not save operator identity: {exc}")
        self._invalidate_config()
        return ActionResult(True, "operator identity saved",
                            details=[f"  callsign = {new_call or '(unset)'}"])

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

    def save_stack_config(self, target: str, values: dict, band: str = "") -> ActionResult:
        """Validate and persist a stack/band's run + file configuration via the CANONICAL bundle
        path (`save_config_bundle`). `values` keys are the same canonical API keys the Config/Start
        pages use: `name`/`file_<name>` when unique, `component.name`/`file_<component.name>` when the
        name is duplicated across the stack. Unknown fields and unqualified duplicate names are typed
        failures with NO mutation; unique names keep their flat-key/field compatibility; daemon-profile
        `dp_*`, autostart, remotes and the transactional semantics are preserved."""
        if self.stack(target) is None:
            return self._unknown_stack(target)
        return self.save_config_bundle(target, values=values, band=band)

    def _apply_hints(self, target: str, modes: set) -> list[str]:
        """Human guidance on how a saved config change takes effect."""
        running = self.stack_running(target)
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
            save_component_remotes(self._paths, {cid: url for cid in group})
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

    def _identity_field(self, target: str) -> dict | None:
        """The operator-identity field CALL/node enforcement guards, or None. Scoped to the target:
        a STACK target inspects every component; a direct COMPONENT target inspects ONLY that
        component. The daemon is exempt. The FIRST run/file param with a callsign/node validator
        wins; a callsign (licensed) is preferred over a node (unlicensed) if both are declared."""
        if self._is_daemon_target(target):
            return None
        found = None
        for c in self._target_components(target):
            candidates = [("run", p) for p in c.run_params]
            candidates += [("file", p) for p in (c.config_file.params if c.config_file else ())]
            for kind, p in candidates:
                enforce = self._IDENTITY_ENFORCE.get(getattr(p, "validator", ""))
                if not enforce:
                    continue
                rec = {"comp": c.id, "name": p.name, "kind": kind, "enforce": enforce,
                       "field": self._param_field(target, kind, c.id, p.name)}
                if enforce == "licensed":
                    return rec                       # licensed wins immediately
                found = found or rec                 # remember an unlicensed node field
        return found

    def _identity_config_hint(self, target: str) -> str:
        """A copy-pasteable `lhpc config` command that sets the callsign/node param blocking a start,
        e.g. `lhpc config chat call <YOURCALL>`. Falls back to the plain list command if the target
        has no identity field. The token is `_param_key`, so a duplicated name is already qualified."""
        idf = self._identity_field(target)
        if not idf:
            return f"lhpc config {target}"
        token = self._param_key(target, idf["kind"], idf["comp"], idf["name"])
        placeholder = "<YOURCALL>" if idf["enforce"] == "licensed" else "<NODENAME>"
        return f"lhpc config {target} {token} {placeholder}"

    def _identity_value(self, target: str, band: str, params, file_over) -> str:
        """The value of the SELECTED identity field, read from ITS OWN component (never masked by a
        same-named sibling): the ephemeral override for that component's key, else its own
        component-scoped/unique-flat stored value, else its operator-substituted default."""
        idf = self._identity_field(target)
        if not idf:
            return ""
        kind = "run" if idf["kind"] == "run" else "file"
        comp_id, name = idf["comp"], idf["name"]
        key = self._param_key(target, kind, comp_id, name)   # bare or component-qualified
        val = ((params if kind == "run" else file_over) or {}).get(key)
        if val is None:
            val = self._resolved_param_value(target, kind, comp_id, name, band)
        return str(self._op_subst(val or "")).strip()

    def enforce_identity(self, target: str, band: str = "", params: dict | None = None,
                         file_over: dict | None = None) -> tuple[bool, str, str]:
        """Whether `target` may start given its CALL/node rule. Returns (ok, field, message).
        Licensed stacks refuse an empty or `N0CALL` callsign; unlicensed stacks refuse only an
        empty node name (the default name is accepted). `field` is the confirm input to highlight."""
        idf = self._identity_field(target)
        if not idf:
            return (True, "", "")
        val = self._identity_value(target, band, params, file_over)
        if idf["enforce"] == "licensed":
            # Refuse empty and the reserved placeholder N0CALL, including any N0CALL-<SSID>.
            if not val or val.split("-", 1)[0].upper() == "N0CALL":
                return (False, idf["field"], f"A valid callsign is required to start '{target}' "
                        f"(licensed) — set '{idf['name']}' to your callsign (not empty or N0CALL).")
        elif not val:
            return (False, idf["field"], f"A node name is required to start '{target}' — "
                    f"set '{idf['name']}'.")
        return (True, idf["field"], "")

    def _form_run_params(self, comp):
        """The component's run params EXPOSED in generic Config / Start parameter forms. HMAC-managed
        params (`password_file`) are excluded — they are edited ONLY through the HMAC Enable/Disable/
        Renew flow, never generic config (a blank generic submission would silently restore open auth)."""
        return [p for p in comp.run_params if not self._is_hmac_managed_param(comp, p)]

    def _stack_param_components(self, target: str):
        """Components whose run/file params make up the editable 'Stack parameters' set (never the
        daemon — its radio params are the separate daemon panel). Target-scoped: a stack target
        exposes all components, a direct component target only itself."""
        if self._is_daemon_target(target):
            return []
        return self._target_components(target)

    def stack_start_params(self, target: str, band: str = "", params: dict | None = None,
                           file_over: dict | None = None) -> list[dict]:
        """Rows for the Start-confirm 'Stack parameters' panel: every editable run + file param of
        the stack (never repo source / operator box / autostart), prefilled with the value that WILL
        be used for this start (ephemeral override, else saved config, else operator-substituted
        default). `field` is the confirm input name (`p_`/`pf_`); `default` is the manifest default
        (for the client Reset-to-defaults button)."""
        idf = self._identity_field(target)
        cfg_band = self._config_band(target, band)
        rows: list[dict] = []
        for c in self._stack_param_components(target):
            for kind, p in ([("run", p) for p in self._form_run_params(c)]
                            + [("file", p) for p in (c.config_file.params if c.config_file else ())
                               if not getattr(p, "hidden", False)]):
                # Each (component, kind, name) carries its OWN field + API key (bare when unique,
                # component-qualified when the name collides) and its OWN saved value — colliding
                # components never share a field name or flatten into one value.
                default = self._op_subst(dict(p.band_defaults).get(cfg_band or band, p.default))
                saved = self._resolved_param_value(target, kind, c.id, p.name, band)
                key = self._param_key(target, kind, c.id, p.name)
                field = self._param_field(target, kind, c.id, p.name)
                cur = ((params if kind == "run" else file_over) or {}).get(key, saved)
                is_id = bool(idf and idf["comp"] == c.id and idf["name"] == p.name
                             and idf["kind"] == kind)
                rows.append(self._param_row(p, field, kind, cur, saved, default, is_id,
                                            c.name, key, c.id))
        return rows

    def start_param_fields(self, target: str, band: str = "") -> list[dict]:
        """Every editable run + file parameter as {component, name, kind ('run'|'file'), field, key,
        flag, saved} — covering BOTH the savable panel params and plain start-option params (e.g. the
        daemon's radio/debug). The single source of truth for the web to read each submitted form
        field into its correctly-scoped API key (bare when unique, `component.name` when duplicated)."""
        out: list[dict] = []
        for c in self._target_components(target):
            for kind, p in ([("run", p) for p in self._form_run_params(c)]
                            + [("file", p) for p in (c.config_file.params if c.config_file else ())]):
                out.append({
                    "component": c.id, "name": p.name, "kind": kind, "flag": p.kind == "flag",
                    "field": self._param_field(target, kind, c.id, p.name),
                    "key": self._param_key(target, kind, c.id, p.name),
                    "saved": self._resolved_param_value(target, kind, c.id, p.name, band)})
        return out

    def stack_start_param_groups(self, target: str, band: str = "", params: dict | None = None,
                                 file_over: dict | None = None) -> list[dict]:
        """The Start-confirm panel rows GROUPED for display: a first 'Required' group with the
        identity (CALL/node) field(s) on top, then one group per component (header = component name)
        with that component's remaining params. [] when the stack has no editable params."""
        rows = self.stack_start_params(target, band, params, file_over)
        groups: list[dict] = []
        required = [r for r in rows if r["is_identity"]]
        if required:
            groups.append({"header": "Required", "rows": required})
        by_comp: dict[str, list] = {}
        order: list[str] = []
        for r in rows:
            if r["is_identity"]:
                continue
            if r["comp_name"] not in by_comp:
                by_comp[r["comp_name"]] = []
                order.append(r["comp_name"])
            by_comp[r["comp_name"]].append(r)
        for name in order:
            groups.append({"header": name, "rows": by_comp[name]})
        return groups

    @staticmethod
    def _param_row(p, field: str, kind: str, value, saved: str, default: str, is_identity: bool,
                   comp_name: str = "", key: str = "", component: str = "") -> dict:
        return {"field": field, "name": p.name, "key": key, "component": component,
                "kind": p.kind, "comp_name": comp_name,
                "choices": list(p.choices), "label": p.label or p.name,
                "value": "" if value is None else str(value),
                "config_value": "" if saved is None else str(saved), "default": default,
                "advanced": bool(getattr(p, "advanced", False)),
                "validator": getattr(p, "validator", ""),
                "is_identity": bool(is_identity),
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
        id_ok, id_field, id_msg = self.enforce_identity(target, band, params, file_over)
        if not id_ok:
            return None, None, ActionResult(
                False, f"Cannot {op} '{target}': {id_msg}", data={"enforce_field": id_field},
                next_commands=[self._identity_config_hint(target)])
        return params, file_over, None

    def write_config_files(self, target: str, band: str = "",
                           overrides: dict | None = None) -> list["ConfigWrite"]:
        """(Re)generate every file-config component's config file from the stored
        (per-band) values. Returns a STRUCTURED result per component (written /
        linked-readonly / no-base / failed) so an auto-start can block on a generation
        failure rather than silently launching with stale or absent configuration.

        `overrides` are EPHEMERAL per-start file values ({param_name: value}, this launch only,
        never persisted) taken from the Start-confirm 'Stack parameters' panel — validated by the
        caller via `_normalize_file_overrides` before they reach here."""
        op = self.config().operator
        runtime = str(self._paths.runtime_root)
        cfg_band = self._config_band(target, band)
        # Validate operator identity; fall back to safe placeholders if invalid so a
        # corrupted local.toml can never inject into a generated config file.
        try:
            call = validators.callsign(op.callsign or "N0CALL") or "N0CALL"
        except validators.ValidationError:
            call = "N0CALL"

        def subst(text: str) -> str:
            return (text.replace("{callsign}", call)
                        .replace("{runtime}", runtime)
                        .replace("{band}", cfg_band))    # for per-band config keys

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
            for p in fc.params:
                raw = over.get(p.name, stored.get(p.name, p.default))
                try:
                    values[p.name] = validators.validate_param(p, raw)
                except validators.ValidationError:
                    values[p.name] = p.default
            # THREE explicit destination policies (P1 generated-config containment):
            dest = self._resolve_config_dest(c, fc.path)
            if dest.status != "ok":
                written.append(ConfigWrite(c.id, dest.detail_path, dest.status, dest.detail))
                continue
            out_path = dest.path
            if fc.fmt in ("toml-update", "yaml-update"):
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
                updater = update_toml if fc.fmt == "toml-update" else update_yaml
                text = updater(base_text, fc.params, values, subst)
            elif fc.fmt == "env":
                # KEY=value (no spaces, no header) for split-on-'=' parsers.
                text = render_keyval(fc.params, values, subst, sep="=", comment=False)
            else:
                text = render_keyval(fc.params, values, subst)
            try:
                if dest.policy == "runtime":
                    runtime_fs.atomic_write(self._paths, out_path, text, 0o644)
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
        """Resolve a FileConfig path/base into one of three policies:
          * runtime  — `{runtime}/...` only, resolved through `Paths.under` (containment);
          * source   — a RELATIVE path under the managed source root (rejects linked);
          * (reject) — an arbitrary absolute path, unknown placeholder, or traversal.
        Returns a small result with `.status` ("ok"/"failed"/"linked-readonly"),
        `.policy`, `.path`, `.detail`, `.detail_path`."""
        from types import SimpleNamespace
        if raw == "{runtime}" or raw.startswith("{runtime}/"):
            rel = raw[len("{runtime}"):].lstrip("/")
            parts = [p for p in rel.split("/") if p]
            try:
                p = self._paths.under(*parts) if parts else self._paths.runtime_root
            except PathContainmentError as exc:
                return SimpleNamespace(status="failed", policy="runtime", path=None,
                                       detail=f"runtime path escapes root: {exc}", detail_path=raw)
            return SimpleNamespace(status="ok", policy="runtime", path=p, detail="", detail_path=raw)
        if raw.startswith("/") or raw.startswith("{") or ".." in raw.split("/"):
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
        """Read a config base safely: a runtime base via runtime_fs (no-follow); a managed
        source base via the SAME descriptor-anchored, O_NOFOLLOW traversal as the writer
        (no check-then-open — a base file or parent swapped to a symlink after a check
        cannot be followed)."""
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
                      if k in run_names or k.startswith("file_") or k.startswith("autostart_")
                      or k.startswith("__r__") or k.startswith("__f__")]
            if normal:
                # Clear ONLY the normal-owned keys under the config lock; dp_* + unrelated stay.
                update_stack_config(self._paths, target, {k: "" for k in normal}, cfg_band)
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
