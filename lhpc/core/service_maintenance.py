"""Source update / uninstall / clean / known-working / source-check operations.

Mixin of ControllerService (state/constants on the facade). Adapters import lhpc.core.services only."""
from __future__ import annotations

import time
from pathlib import Path

from .snapshot_memo import invalidates_snapshot
from . import runtime_fs
from .model import RunState
from .paths import PathContainmentError
from .service_base import ActionResult, AdmissionRefused, SourceTxnBlocked


class MaintenanceOpsMixin:

    def _update_probe(self, comp) -> dict:
        """NETWORK. Bounded `git ls-remote` freshness probe for ONE component — no fetch, no
        mutation. Returns a `stackupdates` entry: status + the evidence it was computed from.

        `local_head_at_check` is what ties the verdict to the source it describes (a later
        Update/Clean invalidates it) — it costs nothing, the probe already runs `rev-parse HEAD`.

        UNKNOWN vs ERROR: "no remote / not installed / invalid remote" is `unknown` (nothing to
        compare), while a probe that RAN and failed (ls-remote or rev-parse) is `error`. Collapsing
        both — as the old `update_status` did — reported an unreachable network as "nothing to
        compare", which reads far too much like "fine".
        """
        from . import stackupdates
        entry = {"remote": "", "source_path": "", "local_head_at_check": "", "upstream_head": ""}
        if comp is None or comp.source is None or not comp.source.remote:
            return {**entry, "status": stackupdates.UNKNOWN}
        entry["source_path"] = comp.source.path
        src = self._paths.resolve_source(comp.source.path)
        if not src.is_dir():
            return {**entry, "status": stackupdates.UNKNOWN}      # not installed -> NO network
        remote = self.config().remotes.get(comp.id) or comp.source.remote
        # Revalidate the (possibly hand-edited) remote IMMEDIATELY before git — an invalid
        # runtime override must never reach `git ls-remote`; treat it as unknown, not a check.
        from . import validators
        try:
            remote = validators.remote_url(remote or "", field="remote")
        except validators.ValidationError:
            return {**entry, "status": stackupdates.UNKNOWN}
        if not remote:
            return {**entry, "status": stackupdates.UNKNOWN}
        entry["remote"] = remote
        ref = comp.source.branch or "HEAD"
        run = self._system.runner.run
        rem = run(["git", "ls-remote", remote, ref], timeout=12.0)
        if rem.returncode != 0 or not rem.stdout.strip():
            return {**entry, "status": stackupdates.ERROR}        # unreachable / no such ref
        remote_sha = rem.stdout.split()[0]
        loc = run(["git", "-C", str(src), "rev-parse", "HEAD"], timeout=5.0)
        if loc.returncode != 0 or not loc.stdout.strip():
            return {**entry, "status": stackupdates.ERROR}        # not a repo / broken checkout
        local_sha = loc.stdout.strip()
        entry["upstream_head"] = remote_sha
        entry["local_head_at_check"] = local_sha
        entry["status"] = (stackupdates.UP_TO_DATE if local_sha == remote_sha
                           else stackupdates.BEHIND)
        return entry

    def update_status(self, comp) -> str:
        """Is the installed source up to date with its GitHub remote branch?
        Returns "up-to-date", "update-available", or "unknown" (no remote/git/net).
        Uses a bounded `git ls-remote` — no fetch, no mutation.

        Thin wrapper over `_update_probe`, preserving the 3-value contract its callers expect
        (`update()`'s dry-run): a probe ERROR collapses back to "unknown" here."""
        from . import stackupdates
        status = self._update_probe(comp)["status"]
        if status == stackupdates.UP_TO_DATE:
            return "up-to-date"
        if status == stackupdates.BEHIND:
            return "update-available"
        return "unknown"                                          # incl. ERROR — legacy contract

    def _source_check_targets(self, target: str):
        """(components, error) for a source-freshness sweep. Unlike `_resolve`, this selects on
        SOURCE, not on `run_argv` — a library component like `radiolib` has a remote to compare but
        nothing to run, and must still be checked."""
        def _with_source(comps):
            return [c for c in comps if c.source and c.source.remote]
        if not target:
            out = []
            for s in self.stacks():
                out += _with_source(s.components)
            return out, None
        s = self.stack(target)
        if s is not None:
            return _with_source(s.components), None
        for st in self.stacks():
            c = st.component(target)
            if c is not None:
                return _with_source([c]), None
        return [], f"Unknown stack or component '{target}'."

    def source_check(self, target: str = "") -> ActionResult:
        """NETWORK (explicit): probe each component's remote and refresh the cached freshness
        marker. The ONLY writer of `state/stackupdates.json`, and never reached from a GET route
        (P0.6) — it is called by the `/source-check/<target>` POST and the background check thread.

        An uninstalled source costs no network at all (`_update_probe` returns UNKNOWN before any
        git call), so a sweep over a mostly-uninstalled box is nearly free.
        """
        from . import stackupdates
        comps, err = self._source_check_targets(target)
        if err:
            return ActionResult(False, err, next_commands=["lhpc list"])
        if not comps:
            return ActionResult(True, f"Nothing to check for '{target or 'all'}' — "
                                      "no component declares a source remote.")
        results, details = {}, []
        counts = {stackupdates.BEHIND: 0, stackupdates.UP_TO_DATE: 0,
                  stackupdates.UNKNOWN: 0, stackupdates.ERROR: 0}
        for c in comps:
            entry = self._update_probe(c)
            results[c.id] = entry
            counts[entry["status"]] = counts.get(entry["status"], 0) + 1
            details.append(f"  {c.id}: {entry['status'].replace('_', ' ')}")
        stackupdates.record(self._paths, results)
        n = len(comps)
        behind, errs = counts[stackupdates.BEHIND], counts[stackupdates.ERROR]
        unknown, uptodate = counts[stackupdates.UNKNOWN], counts[stackupdates.UP_TO_DATE]
        who = target or "all"
        # UNKNOWN is "nothing to compare" (no remote / not installed / invalid remote) — it is NOT
        # a passing check, and must never be summarized as, or flashed green like, "up to date".
        # `ok` therefore means "every component yielded a real comparison": a single unknown or
        # error downgrades the flash to a warning, even when nothing is behind.
        if errs:
            summary = (f"{errs} of {n} source(s) could not be checked for '{who}' — see details."
                       + (f" {behind} behind." if behind else ""))
        elif behind:
            summary = f"{behind} of {n} source(s) behind their remote for '{who}'."
            if unknown:
                summary += f" {unknown} not comparable."
        elif uptodate and unknown:
            summary = f"{uptodate} up to date, {unknown} unknown/not comparable for '{who}'."
        elif uptodate:
            summary = f"All checked sources are up to date for '{who}'."
        else:
            summary = (f"No installed/comparable sources could be checked for '{who}' — "
                       f"{unknown} unknown/not comparable.")
        return ActionResult(errs == 0 and unknown == 0, summary, details=details,
                            data={"counts": counts, "checked": n})

    def source_check_view(self) -> dict:
        """Cached, network-free freshness view for GET pages. `{checked_at, components}`."""
        from . import stackupdates
        return stackupdates.view(self._paths)


    def _with_source(self, target: str):
        """All (stack, component) under `target` that declare a source."""
        out = []
        for s in self.stacks():
            if target and s.id != target and s.component(target) is None:
                continue
            for c in s.components:
                if c.source is None:
                    continue
                if target and s.id != target and c.id != target:
                    continue
                out.append((s, c))
        return out

    def _source_consumers(self) -> dict:
        """Manifest-wide: every component id consuming each source path — direct source
        declarations AND `build_requires` edges (a component whose BUILD consumes a checkout
        references it: the daemon consumes src/RadioLib, so uninstalling radiolib alone is
        refused while the daemon's source is installed). The reference map used by
        update/uninstall/clean gates."""
        comp_index = {c.id: c for s in self.stacks() for c in s.components}
        consumers: dict[str, set] = {}
        for s in self.stacks():
            for c in s.components:
                if c.source:
                    consumers.setdefault(c.source.path, set()).add(c.id)
                for dep_id in c.build_requires:
                    dep = comp_index.get(dep_id)
                    if dep is not None and dep.source is not None:
                        # the build edge holds only while the CONSUMER's own source is
                        # installed (an uninstalled daemon no longer references RadioLib)
                        if c.source is None or self._paths.resolve_source(c.source.path).exists():
                            consumers.setdefault(dep.source.path, set()).add(c.id)
        return consumers

    def _path_declarers(self, source_path: str) -> list:
        """Every manifest component DECLARING `source_path` as its own source (the set whose
        effective remotes must agree — one checkout has ONE remote)."""
        return [c for st in self.stacks() for c in st.components
                if c.source and c.source.path == source_path]

    def _effective_remote(self, comp) -> str:
        return (self.config().remotes.get(comp.id)
                or (comp.source.remote if comp.source else "") or "")

    def _shared_remote_conflict(self, source_path: str) -> str | None:
        """A source path is ONE checkout and must have ONE effective remote. Returns a typed
        detail when the current consumers' normalized effective remotes diverge (e.g. a
        legacy hand-edited per-component override) — destructive operations and known-working
        confirmation must fail closed on it, with zero source mutation."""
        from . import source_registry
        seen: dict = {}
        for c in self._path_declarers(source_path):
            seen.setdefault(source_registry.norm_remote(self._effective_remote(c)),
                            []).append(c.id)
        if len(seen) <= 1:
            return None
        parts = "; ".join(f"{', '.join(cids)} -> {norm or '(none)'}"
                          for norm, cids in sorted(seen.items()))
        return (f"conflicting effective remotes for shared source {source_path!r} "
                f"({parts}) — set ONE remote for all of its components before mutating it")

    def _retire_candidates_for_paths(self, paths_mutated, out: list) -> bool:
        """After a source belonging to a stack was changed/removed, retire that stack's
        `last-start` candidate marker (an older composition must not remain eligible for a
        later confirmation). Returns False — the operation is INCOMPLETE — when a present
        marker could not be cleared; the failure is recorded as durable evidence in `out`."""
        from . import known_working
        if not paths_mutated:
            return True
        affected = sorted({st.id for st in self.stacks()
                           for c in st.components
                           if c.source and c.source.path in set(paths_mutated)})
        ok = True
        for sid in affected:
            cleared, why = known_working.clear_candidate_checked(self._paths, sid)
            if not cleared:
                out.append(f"  [fail] {why}")
                ok = False
        return ok

    def _op_seam(self, point: str) -> None:
        """DETERMINISTIC TEST SEAM for operation serialization — a no-op hook fired at
        defined points of applied update/uninstall/clean (e.g. after preflight, after the
        locks are held, between source groups). Tests monkeypatch it to inject concurrent
        events; it carries no production behaviour."""

    def _adopt_dev_fallback(self, inst, st, comp, source: str, resolved, force: bool,
                            locked: bool):
        """Adopt with the DEFAULT policy: on a FAILED `dev` adoption, retry ONCE at the
        known-working (else manifest-pin) identity — DISCLOSED in the action detail,
        never silent. Non-dev selectors and fallback-less failures return unchanged."""
        a = inst.adopt_source(comp, force=force, source=source,
                              pinned_expected=resolved, locked=locked)
        if a.status != "failed" or source != "dev":
            return a
        fb = self._kw_fallback_expected(st, comp)
        if not fb[0]:
            return a
        a2 = inst.adopt_source(comp, force=force, source="pinned",
                               pinned_expected=fb, locked=locked)
        if a2.status != "failed":
            a2.detail = (f"dev unreachable ({a.detail}) — FELL BACK to "
                         f"{fb[1]}: {a2.detail}")
        else:
            a2.detail = (f"dev failed ({a.detail}); known-working fallback also "
                         f"failed: {a2.detail}")
        return a2

    def _kw_fallback_expected(self, stack, comp) -> tuple:
        """DISCLOSED fallback identity when `dev` is unreachable: the stack's newest
        compatible known-working composition entry, else the manifest pin. ("", "")
        when neither exists (the dev refusal then stands — never a silent substitute)."""
        from . import known_working
        try:
            entries = known_working.compatible_composition(
                self._paths, stack, lambda c: self._effective_remote(c))
        except Exception:                        # noqa: BLE001 — fallback probe only
            entries = None
        if entries and comp.id in entries and entries[comp.id].get("commit"):
            return (entries[comp.id]["commit"],
                    "fallback: known-working (dev unreachable)")
        if comp.source.pin_commit:
            return (comp.source.pin_commit, "fallback: manifest pin (dev unreachable)")
        return ("", "")

    def _frozen_ref(self, comp, source: str) -> tuple:
        """Resolve ONE exact immutable commit for a dev/stable/artifact group at PLAN
        time (bounded `git ls-remote`, no fetch, no mutation). Returns
        ((sha, label), "") or ((None, None), typed-reason). Adoption receives the frozen
        sha and performs NO second selector lookup."""
        import re
        from . import validators
        spec = comp.source
        remote = self.config().remotes.get(comp.id) or spec.remote
        try:
            remote = validators.remote_url(remote or "", field="remote")
        except validators.ValidationError as exc:
            return (None, None), f"invalid remote ({exc})"
        if not remote:
            return (None, None), "no remote configured"
        run = self._system.runner.run
        if spec.artifact or source == "dev":
            # artifact: the declared artifact IS the maintainer's default branch;
            # dev: the configured development branch (strict — never another ref).
            ref = "HEAD" if spec.artifact else (spec.branch or "HEAD")
            out = run(["git", "ls-remote", remote, ref], timeout=15.0)
            if out.returncode != 0 or not out.stdout.strip():
                return (None, None), f"could not resolve {ref!r} on {remote}"
            sha = out.stdout.split()[0]
            label = ("declared artifact (default branch)" if spec.artifact
                     else f"development branch {ref}")
            return (sha, f"frozen: {label} @ {sha[:9]}"), ""
        # stable: newest VERSION-SHAPED tag (peeled commit), else default-branch HEAD —
        # resolved remotely so the whole run uses ONE exact commit.
        out = run(["git", "ls-remote", "--tags", remote], timeout=15.0)
        if out.returncode != 0:
            return (None, None), f"could not list tags on {remote}"
        if self._VERSION_TAG_RE is None:
            type(self)._VERSION_TAG_RE = re.compile(r"^v?(\d+(?:\.\d+)*)$")
        best, best_sha = None, ""
        plain, peeled = {}, {}
        for line in (out.stdout or "").splitlines():
            parts = line.split()
            if len(parts) != 2 or not parts[1].startswith("refs/tags/"):
                continue
            name = parts[1][len("refs/tags/"):]
            if name.endswith("^{}"):
                peeled[name[:-3]] = parts[0]
            else:
                plain[name] = parts[0]
        for name, sha in plain.items():
            m = self._VERSION_TAG_RE.match(name)
            if not m:
                continue
            key = tuple(int(x) for x in m.group(1).split("."))
            if best is None or key > best:
                best, best_sha = key, peeled.get(name, sha)
        if best is not None:
            return (best_sha, f"frozen: latest stable tag @ {best_sha[:9]}"), ""
        out2 = run(["git", "ls-remote", remote, "HEAD"], timeout=15.0)
        if out2.returncode != 0 or not out2.stdout.strip():
            return (None, None), f"could not resolve HEAD on {remote}"
        sha = out2.stdout.split()[0]
        return (sha, f"frozen: default branch @ {sha[:9]} (no version tags)"), ""

    def _plan_source_groups(self, items, source: str, freeze: bool = False) -> tuple:
        """ONE immutable operation plan for an install/update over `items` [(stack, comp)]:

          * known-working is resolved ONCE per affected stack from one complete compatible
            composition (never re-computed while iterating; a concurrent confirmation
            cannot alter this operation);
          * components are grouped by shared source path; every targeted consumer of a path
            must resolve to the SAME source identity — strategy, artifact form, normalized
            effective remote, and (for 'pinned') the same frozen commit/fallback;
          * incompatible resolutions block BEFORE any candidate/source/registry/config
            mutation.

        Returns (groups, error): groups = ordered [(path, comp, selector, (expected, label))].
        `selector` is the per-stack Version choice carried through to adoption."""
        from . import known_working, source_registry
        # `source` may be a uniform selector string OR a per-stack resolver `source_of(stack_id)`
        # (the auto-install driver passes the latter for per-stack Version). The SELECTOR is part
        # of the source identity below, so two stacks sharing a path with different selectors
        # conflict even if they resolve to the same commit.
        _sel = source if callable(source) else (lambda _sid, _s=source: _s)
        compositions: dict = {}
        for st in {s.id: s for s, _ in items}.values():
            if _sel(st.id) == "pinned":
                compositions[st.id] = known_working.compatible_composition(
                    self._paths, st, lambda c: self._effective_remote(c))
        by_path: dict = {}
        for st, comp in items:
            spec = comp.source
            sel = _sel(st.id)
            if sel != "pinned" or spec.artifact:
                resolved = ("", "")
            else:
                entries = compositions.get(st.id)
                if entries and comp.id in entries:
                    resolved = (entries[comp.id]["commit"],
                                "known working (operator-confirmed composition)")
                else:
                    resolved = ("", "fallback: manifest pin — no known-working record")
            ident = (sel, spec.strategy or "", bool(spec.artifact),
                     source_registry.norm_remote(self._effective_remote(comp)), resolved)
            by_path.setdefault(spec.path, []).append((st, comp, sel, ident, resolved))
        groups, conflicts = [], []
        frozen_cache: dict = {}
        for path, members in by_path.items():
            idents = {m[3] for m in members}
            if len(idents) > 1:
                who = ", ".join(f"{st.id}/{c.id}" for st, c, _, _, _ in members)
                conflicts.append(f"shared source {path!r}: targeted consumers ({who}) "
                                 "resolve to incompatible source identities (selector/strategy/"
                                 "remote/known-working) — resolve or re-confirm before "
                                 "installing/updating")
                continue
            st, comp, sel, _, resolved = members[0]
            if freeze and not resolved[0] and (sel != "pinned"
                                               or comp.source.artifact):
                # FROZEN selector resolution (auto-install plan): one exact immutable commit per
                # group, resolved HERE — adoption never performs a second lookup.
                # ARTIFACT sources freeze their declared default-branch HEAD for EVERY
                # selector (incl. 'pinned' — they never use known-working entries): the
                # plan-time commit IS this run's immutable artifact identity.
                if path not in frozen_cache:
                    frozen_cache[path] = self._frozen_ref(comp, sel)
                (fz, why) = frozen_cache[path]
                if fz[0] is None:
                    # DEFAULT POLICY: latest dev with a DISCLOSED fallback to the
                    # known-working composition (then the manifest pin) when dev is
                    # unreachable — never a silent substitute, never pinned-by-default.
                    fb = self._kw_fallback_expected(st, comp)
                    if sel == "dev" and fb[0]:
                        resolved = fb
                    else:
                        conflicts.append(f"source {path!r}: exact {sel} resolution "
                                         f"failed — {why}")
                        continue
                else:
                    resolved = fz
            groups.append((path, comp, sel, resolved))
        if conflicts:
            return None, conflicts
        return groups, None

    def deps_report(self, stack_id: str) -> dict:
        """Grouped dependency diagnosis for a stack ({system|build|runtime: [DepItem...]}),
        read-only and bounded — every unmet system prerequisite carries the exact operator
        command, clearly marked as NOT executed by LHPC."""
        from . import deps
        comp_index = {c.id: c for s in self.stacks() for c in s.components}
        report = deps.stack_report(self._lifecycle(), self._paths, self.stacks(),
                                   stack_id, comp_index)
        return deps.grouped(report)

    def _declared_dep_commands(self) -> list[str]:
        """EVERY declared SYSTEM (sudo/apt-level) dependency remediation command — the controller's own
        apt deps (git, nginx, python3-venv, ...) plus every stack component's `require` install (apt,
        OBS repo, SPI/config.txt, group grants, service-disable). Order: controller deps, then manifest
        order. Includes SATISFIED deps too — this is a fresh-install PRE-CLONE bootstrap, not a gap
        report. Venv-level `python -m pip install` commands are EXCLUDED: the venv does not exist before
        the clone, and install.sh provisions those into the venv it creates (never a bare/global pip).
        `provisioned` requires are EXCLUDED for the same reason one step further out: they are
        materialised INTO the runtime root by `lhpc build`, so their remediation ("lhpc build <stack>")
        is an lhpc command that does not exist yet at bootstrap time — emitting it would put a
        `command not found` into a script whose whole job is to run before lhpc is installed."""
        core, _gui = self._declared_dep_scopes()
        return core

    def _declared_gui_dep_commands(self) -> list[str]:
        """The GUI-ONLY remediation commands — everything the headless-safe default bootstrap must
        NOT run, and that `bootstrap-deps.sh --with-gui` installs instead. CORE WINS: a command also
        declared by any non-GUI requirement is absent from this list (see `_declared_dep_scopes`)."""
        _core, gui = self._declared_dep_scopes()
        return gui

    def _declared_dep_scopes(self) -> tuple[list[str], list[str]]:
        """Split every declared remediation command into (core, gui-only).

        A command's effective `gui_only` is the AND across ALL its declarations: one non-GUI
        declaration is enough to keep it in the default bootstrap (and to restore normal mandatory
        semantics elsewhere). That rule is what makes a shared package safe to mark `gui` on one
        component without stranding another component that genuinely needs it headless."""
        core: list[str] = []
        gui: list[str] = []
        gui_only: dict[str, bool] = {}

        def _add(cmd: str, is_gui: bool) -> None:
            if not cmd or "-m pip install" in cmd:    # venv-level, provisioned post-clone by install.sh
                return
            if cmd in gui_only:
                gui_only[cmd] = gui_only[cmd] and is_gui        # AND-merge: core wins
                return
            gui_only[cmd] = is_gui
            (gui if is_gui else core).append(cmd)

        for grp in self.controller_system_deps():
            for d in grp["deps"]:
                _add(d.get("install", ""), False)
        for s in self.stacks():
            for c in s.components:
                for req in c.requires:
                    if getattr(req, "provisioned", False):
                        continue
                    _add(req.install, bool(getattr(req, "gui", False)))
        # A command first seen as GUI and later declared core moves scope, so re-derive both lists
        # from the merged verdict rather than trusting insertion order.
        core = [c for c in core + gui if not gui_only[c]]
        gui = [c for c in gui if gui_only[c]]
        return core, gui

    def deps_script(self) -> str:
        """Render bootstrap-deps.sh — every declared prerequisite as ONE executable sudo script the
        operator runs BEFORE cloning/installing. lhpc only PRINTS it; it never runs privileged
        commands. The dep revision in the header fingerprints the declared command set."""
        import hashlib
        from . import deps
        core, gui = self._declared_dep_scopes()
        # The revision fingerprints COMMAND **and** SCOPE. Hashing the command strings alone would
        # leave the revision unchanged when a package merely moves core -> gui, which is a genuine
        # behaviour change (it disappears from the default bootstrap).
        scoped = [f"core:{c}" for c in core] + [f"gui:{c}" for c in gui]
        rev = hashlib.sha256("\n".join(sorted(set(scoped))).encode()).hexdigest()[:12]
        return deps.render_bootstrap_script(core, rev, gui_cmds=gui)

    def deps_declared(self) -> ActionResult:
        """Readable preview of what `lhpc deps --script` would render — the declared system
        prerequisites, deduplicated, each marked NOT executed by LHPC."""
        from . import deps as deps_mod
        seen: set[str] = set()
        core, gui = self._declared_dep_scopes()
        details = ["Declared system prerequisites (run `lhpc deps --script` for a runnable script):"]
        for cmd in core:
            c = (cmd or "").strip()
            if not c or c in seen:
                continue
            seen.add(c)
            details.append("  " + c.replace("\n", "\n    "))
        if gui:
            # GUI-only commands are NOT in the default bootstrap — say so where they are listed, so
            # the preview can never be mistaken for what a headless run installs.
            details.append("  GUI-only (opt-in: --with-gui) — omitted by default on headless systems:")
            for cmd in gui:
                c = (cmd or "").strip()
                if not c or c in seen:
                    continue
                seen.add(c)
                details.append("    " + c.replace("\n", "\n      "))
        details.append(f"  ({deps_mod.NOT_EXECUTED_NOTE})")
        return ActionResult(True, f"{len(seen)} declared system-dependency command(s).",
                            details=details,
                            next_commands=["lhpc deps --script > bootstrap-deps.sh"])

    _TASK_BANNER_EXPIRY_S = 60      # ONE server-side expiry constant (client never removes earlier)

    def _parse_utc(self, ts):
        """Bounded parse of the canonical persisted UTC timestamp -> epoch seconds, or None."""
        import time
        import calendar
        try:
            return calendar.timegm(time.strptime(str(ts), "%Y-%m-%dT%H:%M:%SZ"))
        except (ValueError, TypeError):
            return None

    # Success/failure hints per (op, outcome); ("*", …) is the op-agnostic fallback.
    _JOB_HINT = {
        ("install", "done"): "Next: Build.",
        ("build", "done"): "Next: Test.",
        ("test", "done"): "Ready.",
        ("build", "failed"): "Build failed — open the log to see the error.",
        ("*", "incomplete"): "Ended unexpectedly — check the log.",
        ("*", "unsafe"): "The build may still be running — inspect processes (ps).",
    }

    def _project_job(self, rec: dict, log: str):
        """Read-only projection of one web-job attempt marker → (state, hint). `state` ∈
        running/done/failed/unsafe (an `incomplete` child renders as `failed` with its own hint). Liveness is
        ATTEMPT-MATCHED (`webjob_gate.is_live_attempt`) — a stale same-log job marker for a DIFFERENT attempt
        never masks this attempt's derived unsafe. Read-only (never `active_jobs(cleanup=True)`)."""
        from . import webjob_gate
        op, st = rec.get("op", ""), rec.get("state")
        if st == "done":
            return "done", self._JOB_HINT.get((op, "done"))
        if st == "failed":
            return "failed", self._JOB_HINT.get((op, "failed"))
        if st == "unsafe":
            return "unsafe", self._JOB_HINT.get(("*", "unsafe"))
        # starting/running: a child alive in/through its gate (this SAME attempt) is NORMAL startup.
        live = webjob_gate.is_live_attempt(self._paths, log, rec.get("attempt_id", ""))
        if rec.get("startup_unverified"):
            return ("running", None) if live else ("unsafe", self._JOB_HINT.get(("*", "unsafe")))
        return ("running", None) if live else ("failed", self._JOB_HINT.get(("*", "incomplete")))

    def running_tasks(self) -> list:
        """STRICTLY READ-ONLY banner feed (auto-install + HMAC + detached build/test/install jobs). RUNNING
        never expires; `done` drops after the server-side expiry; `failed`/`unsafe` STAY (failed is
        ✕-dismissible, unsafe needs Recover). Every helper is file+/proc read only — no marker mutation
        (jobs use `active_jobs(cleanup=False)`/`log_running`/`jobresult.read_results`, all no-follow, bounded).
        Colours: running=yellow, done=green, failed/unsafe=red."""
        import time
        from . import jobresult
        now = int(time.time())
        out = []
        dismissed = self._task_dismissed_ids()

        def _done_within(ts):
            epoch = self._parse_utc(ts)
            if epoch is None or now - epoch >= self._TASK_BANNER_EXPIRY_S:
                return None
            return {"finished_ago_s": max(0, now - epoch)}

        def _failed_extra(ts):
            epoch = self._parse_utc(ts)
            return {"finished_ago_s": max(0, now - epoch)} if epoch is not None else {}

        def _bounded_reason(reason):
            # Never echo an unbounded/None reason into the banner.
            return (str(reason).strip()[:200]) if reason else "malformed or unreadable state — recovery required"

        try:
            bst = self.auto_install_status()
        except Exception:                          # noqa: BLE001 — a GET must never 500
            bst = None
        if bst and bst.get("unsafe"):
            # A MALFORMED / unreadable auto-install marker (top-level `unsafe`) MUST be surfaced as a
            # recovery-required task — it is the state an operator most needs to see. Never hidden,
            # never expires until resolved.
            out.append({"kind": "auto-install", "run_id": bst.get("run_id", ""),
                        "label": "Install / build all stacks", "href": "/auto-install",
                        "state": "unsafe", "hint": _bounded_reason(bst.get("reason"))})
        elif bst:
            rid, state = bst.get("run_id", ""), bst.get("state")
            base = {"kind": "auto-install", "run_id": rid, "label": "Install / build all stacks",
                    "href": "/auto-install"}
            if state in ("preparing", "running"):
                out.append({**base, "state": "running"})
            elif state in ("interrupted", "unsafe"):
                out.append({**base, "state": "unsafe"})            # blocking; no expiry
            elif state == "aborted":
                fin = _done_within(bst.get("finished_at", ""))     # clean operator stop -> transient
                if fin is not None:
                    out.append({**base, "state": "done", **fin})
            elif state == "completed":
                fin = _done_within(bst.get("finished_at", ""))
                if fin is not None:
                    out.append({**base, "state": "done", **fin})
            elif state == "completed-with-failures" and rid not in dismissed:
                out.append({**base, "state": "failed", **_failed_extra(bst.get("finished_at", ""))})

        try:
            hst = self.hmac_apply_status()
        except Exception:                          # noqa: BLE001
            hst = None
        if hst and hst.get("unsafe"):
            # Same as auto-install: a malformed/unreadable HMAC marker is surfaced as recovery-required
            # rather than silently dropped.
            sid, action = hst.get("sid", "meshcom"), hst.get("action", "enable")
            out.append({"kind": "hmac", "run_id": hst.get("run_id", ""),
                        "label": f"HMAC {action} on {sid}", "href": f"/stacks/{sid}/hmac/{action}",
                        "state": "unsafe", "hint": _bounded_reason(hst.get("reason"))})
        elif hst:
            rid, phase = hst.get("run_id", ""), hst.get("phase")
            sid, action = hst.get("sid", "meshcom"), hst.get("action", "enable")
            base = {"kind": "hmac", "run_id": rid, "label": f"HMAC {action} on {sid}",
                    "href": f"/stacks/{sid}/hmac/{action}"}
            if phase == "running":
                out.append({**base, "state": "running"})
            elif phase == "unsafe":
                out.append({**base, "state": "unsafe"})            # never expires until resolved
            elif phase == "done":
                fin = _done_within(hst.get("finished_at", ""))
                if fin is not None:
                    out.append({**base, "state": "done", **fin})
            elif phase == "failed" and rid not in dismissed:
                out.append({**base, "state": "failed", **_failed_extra(hst.get("finished_at", ""))})

        # ---- detached web build/test/install jobs (yellow → green/red) ----
        try:
            results = jobresult.read_results(self._paths)
        except Exception:                          # noqa: BLE001
            results = []
        for log, rec in results:
            target = rec.get("target", "")
            if not (self.stack_of(target) or self.stack(target)):   # MANIFEST-aware validation
                continue
            state, hint = self._project_job(rec, log)
            extra = {}
            if state == "done":
                fin = _done_within(rec.get("finished_at", ""))
                if fin is None:
                    continue                       # done aged out (banner display filter)
                extra = fin
            item = {"kind": "job", "run_id": log, "attempt_id": rec.get("attempt_id", ""),
                    "label": (f"{rec.get('op', '')} {target}").strip(),
                    "href": f"/logs/{target}?job={log}", "state": state, **extra}
            if hint:
                item["hint"] = hint
            out.append(item)
        return out

    # ---- banner dismiss / recover (all durable-confirmed; unsafe/running never ✕-dismissible) ----
    def _dismissed_path(self):
        return self._paths.under("state", "task_dismissed.json")

    def _task_dismissed_ids(self) -> set:
        import json
        try:
            data = json.loads(runtime_fs.read_text_regular(self._paths, self._dismissed_path(),
                                                            max_bytes=16384))
        except (FileNotFoundError, OSError, PathContainmentError, ValueError):
            return set()
        return {x for x in data if isinstance(x, str)} if isinstance(data, list) else set()

    def _task_dismiss_add(self, run_id: str) -> bool:
        import json
        ids = [x for x in self._task_dismissed_ids() if x != run_id]
        ids.append(run_id)
        try:
            runtime_fs.atomic_write(self._paths, self._dismissed_path(),
                                    json.dumps(ids[-50:]), 0o600)
            return True
        except (OSError, PathContainmentError):
            return False

    def task_dismiss(self, kind: str, run_id: str, attempt_id: str = "") -> bool:
        """Dismiss a FAILED (never unsafe/running) terminal banner. Durable; returns success only on a
        confirmed write/unlink."""
        from . import jobresult
        if not run_id:
            return False
        if kind == "job":
            rec = jobresult.read_one(self._paths, run_id)
            if rec is None or rec.get("attempt_id") != attempt_id:
                return False
            state, _ = self._project_job(rec, run_id)      # failed (incl. derived incomplete) only
            if state != "failed":
                return False
            return jobresult.remove(self._paths, run_id, attempt_id)
        if kind == "auto-install":
            try:
                st = self.auto_install_status()
            except Exception:                              # noqa: BLE001
                st = None
            if not (st and not st.get("unsafe") and st.get("state") == "completed-with-failures"):
                return False
            return self._task_dismiss_add(run_id)
        if kind == "hmac":
            try:
                st = self.hmac_apply_status()
            except Exception:                              # noqa: BLE001
                st = None
            if not (st and not st.get("unsafe") and st.get("phase") == "failed"):
                return False
            return self._task_dismiss_add(run_id)
        return False

    def task_recover(self, kind: str, run_id: str, attempt_id: str) -> bool:
        """Explicit-ack recovery of an UNSAFE build/test/install job (kind=job) → non-blocking failed.
        hmac/auto-install keep their own recover flows. Durable-confirmed."""
        from . import jobresult
        if kind != "job" or not run_id:
            return False
        rec = jobresult.read_one(self._paths, run_id)
        if rec is None or rec.get("attempt_id") != attempt_id:
            return False
        state, _ = self._project_job(rec, run_id)
        if state != "unsafe":
            return False
        if rec.get("state") == "unsafe":
            return jobresult.recover(self._paths, run_id, attempt_id)
        # derived unsafe (startup_unverified + child gone) → terminalize to a non-blocking failed
        return jobresult.terminalize(self._paths, run_id, attempt_id, "failed",
                                     detail="recovered — startup was unverified")

    def dependency_overview(self) -> dict:
        """Read-only, GET-safe aggregation for the Dependency Overview page + Stacks banner: LHPC's own
        dependencies (including the web-server nginx dep) plus every INSTALLED stack's, each normalized to
        one shape and classified mandatory vs optional. An unmet dep LHPC can satisfy itself carries an
        in-page action (op/target for the /action dispatcher); everything else carries a copyable command
        (LHPC never runs system-package commands). `mandatory_missing`/`optional_missing` drive the banner
        colour (yellow if any mandatory unmet, else green if only optional). Composes existing GET-safe
        probes only (shutil.which / fs.exists / find_spec / missing_requirements / is_dir) — no subprocess."""
        def norm(label, satisfied, mandatory, detail, install, runtime=False, note="",
                 restart_pending=False, gui=False):
            # NARROW action parse: ONLY `lhpc install <target>` / `lhpc build <target>` where the op is a
            # real web action and the target resolves to a known stack — anything else stays copyable.
            op = target = None
            parts = (install or "").split()
            if (len(parts) == 3 and parts[0] == "lhpc" and parts[1] in ("install", "build")
                    and parts[1] in self.WEB_ACTIONS and self.stack(parts[2]) is not None):
                op, target, install = parts[1], parts[2], ""
            # GUI-only deps are opt-in (--with-gui), so they are never a mandatory core miss on a
            # headless box — but they stay visible and carry the remediation.
            return {"label": label, "satisfied": bool(satisfied),
                    "mandatory": bool(mandatory) and not bool(gui),
                    "detail": detail or "", "install": install or "", "op": op, "target": target,
                    "runtime": bool(runtime), "note": note or "",
                    "restart_pending": bool(restart_pending), "gui": bool(gui)}

        sections: list = []
        # LHPC + web server: controller_system_deps groups carry the explicit required flag (nginx here).
        for grp in self.controller_system_deps():
            deps_ = [norm(d.get("what", ""), d.get("satisfied"), d.get("required", True),
                          d.get("purpose", ""), d.get("install", ""), note=d.get("note", ""))
                     for d in grp.get("deps", [])]
            sections.append({"title": grp.get("title", "LHPC"), "kind": "controller",
                             "stack": None, "deps": deps_})
        # Every INSTALLED stack, in manifest order. A dep of an OPTIONAL component is optional.
        comp_index = {c.id: c for s in self.stacks() for c in s.components}
        for s in self.stacks():
            if not self.is_installed(s.id):
                continue
            report = self.deps_report(s.id)          # {system, build, runtime: [DepItem]}
            deps_ = []
            for kind in ("system", "build"):         # runtime = always-satisfied ordering; omit
                for it in report.get(kind, []):
                    comp = comp_index.get(it.component)
                    mandatory = not (comp is not None and comp.optional)
                    deps_.append(norm(it.label, it.satisfied, mandatory, it.detail,
                                      it.install_cmd, runtime=it.runtime,
                                      restart_pending=it.restart_pending, gui=it.gui))
            sections.append({"title": s.name, "kind": "stack", "stack": s.id, "deps": deps_})

        # A restart-pending groups grant stays UNSATISFIED (start is still gated) but is NOT a mandatory
        # dependency "missing" — it is granted and only needs a session restart. Count it separately so
        # the page header says "restart pending" (yellow) instead of "mandatory dependency missing".
        restart_pending = sum(1 for sec in sections for d in sec["deps"]
                              if not d["satisfied"] and d.get("restart_pending"))
        mandatory_missing = sum(1 for sec in sections for d in sec["deps"]
                                if not d["satisfied"] and d["mandatory"] and not d.get("restart_pending"))
        # DISJOINT: a GUI-only miss is counted as GUI-only and NOT also as "optional", so the two
        # figures can be shown side by side without double-counting the same dependency.
        optional_missing = sum(1 for sec in sections for d in sec["deps"]
                               if not d["satisfied"] and not d["mandatory"]
                               and not d.get("gui") and not d.get("restart_pending"))
        gui_missing = sum(1 for sec in sections for d in sec["deps"]
                          if not d["satisfied"] and d.get("gui"))
        return {"sections": sections, "mandatory_missing": mandatory_missing,
                "optional_missing": optional_missing, "restart_pending": restart_pending,
                "gui_missing": gui_missing}

    def _running_source_consumers(self, paths: set) -> list:
        """Component ids that are RUNNING/DEGRADED and consume any of the given source paths —
        a source swap under a running process breaks it (deleted inodes / half-read files), so
        mutation of these paths is refused until the operator stops them. Always assesses FRESH:
        it gates a destructive source swap (update), so a cached read must never hide a process
        that started since the plan preview."""
        consumers = self._source_consumers()
        affected = set()
        for p in paths:
            affected |= consumers.get(p, set())
        snap = self.build_snapshot(fresh=True)
        up = (RunState.RUNNING, RunState.DEGRADED)
        return sorted(cid for ss in snap.stacks for cid, st in ss.components.items()
                      if cid in affected and st.run_state in up)

    # ---- known-working compositions (operator-confirmed) -------------------

    def _stack_composition_entries(self, stack_id: str) -> dict | None:
        """The stack's CURRENT coherent composition from the ownership registry (one entry per
        source component), with a local `git rev-parse` fallback for a pre-registry adoption.
        Returns None when any source component cannot be resolved — a PARTIAL composition is
        never captured (coherence over coverage). Mutation-context only (may run local git)."""
        from . import source_registry
        stack = self.stack(stack_id)
        if stack is None:
            return None
        consumers = self._source_consumers()
        entries: dict = {}
        for c in stack.components:
            if c.source is None:
                continue
            rel = c.source.path
            rec = source_registry.read_record(self._paths, rel)
            if rec is None or not rec.resolved_commit:
                # Pre-registry adoption: origin-verify + BACKFILL a legacy record here in the
                # mutation path (the same ownership proof update/uninstall require), so the
                # composition — and the later offer validation — rests on registry truth.
                dest = self._paths.resolve_source(rel)
                rec, _why = source_registry.verify_or_backfill(
                    self._paths, self._system, self.config(), c, dest,
                    components=tuple(sorted(consumers.get(rel, {c.id}))))
            if rec is None or not rec.resolved_commit:
                return None                              # unprovable component -> no composition
            entries[c.id] = {"commit": rec.resolved_commit, "selector": rec.selector,
                             "remote": rec.remote, "source_rel": rel,
                             "strategy": rec.strategy}
        return entries or None

    def _capture_start_composition(self, stack_id: str, band: str) -> None:
        """Persist the last-start candidate marker after a healthy stack start (best effort —
        a capture failure never degrades the start result; the confirm button simply does not
        appear)."""
        from . import known_working
        entries = self._stack_composition_entries(stack_id)
        if entries:
            known_working.write_candidate(self._paths, stack_id, entries, band or "")

    @staticmethod
    def _manual_only_stack(stack) -> bool:
        """True when LHPC cannot start ANY component of this stack (each is interactive or
        externally supervised) — no lhpc start ⇒ no last-start candidate can ever exist, so
        the known-working offer/confirm may rest on the live probe + ownership registry
        instead (F4: chat could never be confirmed)."""
        return not any(c.run_argv and not c.interactive for c in stack.components)

    def _registry_candidate(self, stack) -> dict | None:
        """A PROBE-BASIS candidate for a manual-only stack, synthesized from the ownership
        registry (FILE READS ONLY — GET-safe; never git). Same shape as a last-start
        candidate; `started_at` 0 states truthfully that no LHPC start produced it. Returns
        None when any source component lacks a registry-proven commit — a partial
        composition is never offered (coherence over coverage, like the start capture)."""
        from . import known_working, source_registry
        entries: dict = {}
        for c in stack.components:
            if c.source is None:
                continue
            rec = source_registry.read_record(self._paths, c.source.path)
            if rec is None or not rec.resolved_commit:
                return None
            entries[c.id] = {"commit": rec.resolved_commit, "selector": rec.selector,
                             "remote": rec.remote, "source_rel": c.source.path,
                             "strategy": rec.strategy}
        if not entries:
            return None
        return {"hash": known_working.composition_hash(entries), "entries": entries,
                "started_at": 0, "band": ""}

    def known_working_offer(self, stack_id: str, snapshot=None) -> dict | None:
        """The 'Confirm this stack as working' offer for the stack page (FILE READS ONLY —
        no git, GET-safe). Present only when ALL hold: a last-start candidate exists (for a
        manual-only stack: a registry-synthesized probe-basis candidate); the stack is
        currently RUNNING (per the supplied/probed snapshot); every candidate entry still
        equals the CURRENT ownership-registry commit (the sources were not swapped since
        that start); and the composition is not already recorded."""
        from . import known_working, source_registry
        cand = known_working.read_candidate(self._paths, stack_id)
        if cand is None:
            stack = self.stack(stack_id)
            if stack is None or not self._manual_only_stack(stack):
                return None
            cand = self._registry_candidate(stack)
        if cand is None:
            return None
        if cand["hash"] in known_working.hashes(self._paths, stack_id):
            return None                                  # already recorded -> no button
        snap = snapshot or self.build_snapshot()
        up = (RunState.RUNNING, RunState.DEGRADED)
        stack_running = any(
            st.run_state in up
            for ss in snap.stacks if ss.stack.id == stack_id
            for st in ss.components.values())
        if not stack_running:
            return None
        for entry in cand["entries"].values():
            rec = source_registry.read_record(self._paths, entry.get("source_rel", ""))
            if rec is None or rec.resolved_commit != entry.get("commit"):
                return None                              # sources changed since that start
        return {"hash": cand["hash"], "started_at": cand.get("started_at", 0),
                "band": cand.get("band", ""), "components": sorted(cand["entries"])}

    def confirm_known_working(self, stack_id: str) -> ActionResult:
        """OPERATOR ACTION: record the last-start candidate composition as known-working
        (dedupe, keep the newest three). For a MANUAL-ONLY stack (no lhpc-startable
        component, e.g. chat) there is never a start candidate: the composition is instead
        synthesized from the ownership registry and accepted only while the probe shows the
        stack RUNNING. Re-validates everything the offer validated —
        AND, under the stack's SOURCE LOCKS, re-proves every component's CURRENT ownership +
        identity (leaf kind, HEAD, origin) against its registry record: a manually changed
        tree is a typed refusal, never a fabricated record."""
        from . import known_working, reslock, source_registry
        stack = self.stack(stack_id)
        if stack is None:
            return self._unknown_stack(stack_id)
        cand = known_working.read_candidate(self._paths, stack_id)
        probe_basis = False
        if cand is None:
            if not self._manual_only_stack(stack):
                return ActionResult(False, f"No healthy start is recorded for '{stack_id}' — "
                                    "start the stack first.")
            # Manual-only stack: LHPC can never record a healthy start for it. Rest the
            # confirmation on the live probe (running NOW) + the ownership registry; the
            # handle-bound identity revalidation below is IDENTICAL to the candidate path.
            cand = self._registry_candidate(stack)
            if cand is None:
                return ActionResult(False, f"Cannot confirm '{stack_id}': its sources are "
                                    "not registry-proven — (re)install the stack first.")
            probe_basis = True
        offer = self.known_working_offer(stack_id)
        if offer is None:
            if cand["hash"] in known_working.hashes(self._paths, stack_id):
                return ActionResult(True, f"'{stack_id}' is already recorded as known working.")
            if probe_basis:
                return ActionResult(False, f"Cannot confirm '{stack_id}': the stack is not "
                                    "running — start it from its dashboard card, then "
                                    "re-confirm.")
            return ActionResult(False, f"Cannot confirm '{stack_id}': the stack is not running "
                                "or its sources changed since that start — start it again "
                                "and re-confirm.")
        # SOURCE-LOCKED identity revalidation: nothing may be recorded as known working while
        # any of its trees drifted from LHPC's registry truth. Runs under the same source-
        # operation boundary update/uninstall/clean use.
        consumers = self._source_consumers()
        src_paths = sorted({e.get("source_rel", "") for e in cand["entries"].values()
                            if e.get("source_rel")})
        comp_by_path = {c.source.path: c for c in stack.components if c.source}
        from . import source_fs
        handles: dict = {}
        try:
            with self._source_operation_guard(src_paths or [stack_id], op="confirm"):
                # HANDLE-BOUND confirmation: every candidate source leaf is captured
                # no-follow; ownership/origin/HEAD/link identity and the candidate-marker
                # commit are verified AGAINST THOSE HANDLES; the same handles are re-proven
                # immediately before the composition record is written. Any replacement,
                # mismatch, or capture failure writes NOTHING.
                for cid, entry in sorted(cand["entries"].items()):
                    rel = entry.get("source_rel", "")
                    comp = comp_by_path.get(rel)
                    if comp is None:
                        return ActionResult(False, f"Cannot confirm '{stack_id}': candidate "
                                            f"entry {cid} names an unknown source {rel!r}.")
                    conflict = self._shared_remote_conflict(rel)
                    if conflict:
                        return ActionResult(False, f"Cannot confirm '{stack_id}' as known "
                                            f"working: {conflict}")
                    dest = self._paths.resolve_source(rel)
                    try:
                        handles[rel] = source_fs.capture_leaf(self._paths, dest)
                    except (OSError, PathContainmentError) as exc:
                        return ActionResult(False, f"Cannot confirm '{stack_id}' as known "
                                            f"working: {rel}: leaf not capturable ({exc}).")
                    rec, why = source_registry.verify_identity(
                        self._paths, self._system, self.config(), comp, dest,
                        components=tuple(sorted(consumers.get(rel, {comp.id}))),
                        handle=handles[rel])
                    if rec is None:
                        return ActionResult(False, f"Cannot confirm '{stack_id}' as known "
                                            f"working: {rel}: {why}")
                    if rec.resolved_commit and rec.resolved_commit != entry.get("commit"):
                        return ActionResult(False, f"Cannot confirm '{stack_id}': {cid} no "
                                            "longer matches the composition captured at start "
                                            "— start the stack again and re-confirm.")
                # RE-PROVE every captured handle immediately before persisting.
                source_fs.race_seam("pre-confirm-record", stack_id)
                for rel, h in handles.items():
                    if not source_fs.verify_leaf_path(self._paths,
                                                      self._paths.resolve_source(rel), h):
                        return ActionResult(False, f"Cannot confirm '{stack_id}': {rel} was "
                                            "concurrently replaced — nothing recorded.")
                validated = {"started_at": cand.get("started_at", 0),
                             "band": cand.get("band", ""),
                             "confirmed_at": time.time(),
                             "evidence": ("probe-verified running manual-start stack + "
                                          "operator confirmation" if probe_basis else
                                          "healthy verified stack start + operator "
                                          "confirmation")}
                ok, msg = known_working.record(self._paths, stack_id, cand["entries"],
                                               validated)
        except AdmissionRefused as _adm:
            return ActionResult(False, _adm.reason, data={'admission_blocked': _adm.tag})
        except SourceTxnBlocked as blocked:
            return ActionResult(False, f"Cannot confirm '{stack_id}': {blocked}")
        except reslock.ResourceBusy as busy:
            return ActionResult(False, f"Cannot confirm '{stack_id}': another source "
                                f"operation is in progress ({busy}).")
        finally:
            for h in handles.values():
                h.close()
        if not ok:
            return ActionResult(False, f"Could not record '{stack_id}' as known working: {msg}")
        return ActionResult(True, f"Recorded '{stack_id}' as a known-working composition "
                            f"({msg}).",
                            details=[f"  {cid}: {e['commit'][:12]} ({e['selector'] or '?'})"
                                     for cid, e in sorted(cand["entries"].items())])

    @invalidates_snapshot
    def update(self, target: str = "", apply: bool = False,
               source: str = "pinned", auto_install_ctx=None) -> ActionResult:
        """Refresh the managed source(s) from GitHub (version per `source`:
        dev/stable/pinned), falling back to the local checkout on failure. Skips
        optional libs/firmware unless one is targeted directly.
        """
        if (_r := self._controller_refusal(target)) is not None:
            return _r
        all_items = self._with_source(target)
        if not all_items:
            return self._unknown_stack(target) if target else ActionResult(False, "No sources.")
        # A NAMED component updates exactly itself; a stack (or the empty "all"
        # target) skips its optional libs/firmware — EXCEPT hard build dependencies
        # (`build_requires`, e.g. the daemon's RadioLib), which are updated with their
        # consumer despite the optional flag.
        is_component = target != "" and self.stack(target) is None
        if is_component:
            items = all_items
        else:
            required = {dep for _, c in all_items if not c.optional for dep in c.build_requires}
            items = [(s, c) for s, c in all_items if not c.optional or c.id in required]
        ctx_err = self._auto_install_ctx_error(auto_install_ctx, {c.source.path for _, c in items})
        if ctx_err:
            return ActionResult(False, f"Refusing to update '{target or 'all'}': {ctx_err}")
        if not apply:
            # The dry-run is the explicit freshness check (`lhpc update --check`):
            # it is the ONLY place that contacts the remote (git ls-remote). GET web
            # routes never do this — they show "unknown" until this is run.
            details = []
            for _, c in items:
                fresh = self.update_status(c)
                details.append(f"  {c.id}: {fresh} — fetch newest from "
                               f"{c.source.remote or 'local checkout'}")
            return ActionResult(
                True, f"Update plan for '{target or 'all'}': refresh {len(items)} source(s) "
                "from GitHub (local fallback).",
                details=details,
                next_commands=[f"lhpc update {target} --yes"] if items else [],
                data={"changes": len(items)})
        # HARD GATE: an update swaps source trees on disk, so it requires the affected stacks
        # STOPPED — the target's components AND every other consumer of an affected SHARED
        # source path (chat running blocks igate's update of src/LoRaHAM_Daemon). Never
        # silently stops or restarts anything; typed refusal with the exact stop commands.
        affected = {c.source.path for _, c in items}
        # CHEAP PREFLIGHT (early typed refusal; NOT the authority — a Start may still land
        # before the locks). The AUTHORITATIVE recheck runs below with every lock held.
        running = self._running_source_consumers(affected)
        if running:
            owners = sorted({self._owner_stack_id(cid) for cid in running})
            return ActionResult(
                False, f"Refusing to update '{target or 'all'}': component(s) using the "
                "affected source(s) are running.",
                details=[f"  running: {', '.join(running)} — stop them first "
                         "(an update never stops or restarts a stack itself)"],
                next_commands=[f"lhpc stack stop {o} --yes" for o in owners])
        self._op_seam("update-preflight")
        from . import reslock
        # SERIALIZATION ORDER (whole applied operation): config-stable shared lock ->
        # source-txn index/recovery -> ALL affected source-path locks (one outer guard,
        # held through plan + every group mutation + candidate retirement) -> FRESH
        # runtime-state recheck -> plan -> mutate. A concurrent Start contends on the
        # same source locks; a concurrent config/remote save waits on config-stable.
        try:
            # LOCK ORDER #1: admission OUTSIDE config-stability (the inner source guard reuses it
            # reentrantly), so a source update never contends config/admission out of order.
            with self._admission_guard("update", target or ""), self._config_stable():
                with self._source_operation_guard(sorted(affected), op="update"):
                    self._op_seam("update-locked")
                    # AUTHORITATIVE running recheck AFTER all locks are held: a Start that
                    # slipped in after the preflight refuses the update with ZERO candidate/
                    # journal/source/registry/marker/config mutation.
                    running = self._running_source_consumers(affected)
                    if running:
                        owners = sorted({self._owner_stack_id(cid) for cid in running})
                        return ActionResult(
                            False, f"Refusing to update '{target or 'all'}': component(s) "
                            "using the affected source(s) started while the update was "
                            "acquiring its locks.",
                            details=[f"  running: {', '.join(running)} — stop them first"],
                            next_commands=[f"lhpc stack stop {o} --yes" for o in owners])
                    # ONE effective remote per shared checkout + ONE immutable plan — both
                    # built UNDER the configuration-stable and source locks (a concurrent
                    # remote save waits; the plan can never use a stale config snapshot).
                    conflicts = sorted({c for c in (self._shared_remote_conflict(p)
                                                    for p in affected) if c})
                    if conflicts:
                        return ActionResult(False, f"Refusing to update '{target or 'all'}': "
                                            "shared-source remote configuration is "
                                            "inconsistent.",
                                            details=[f"  {c}" for c in conflicts])
                    groups, plan_conflicts = self._plan_source_groups(items, source)
                    if plan_conflicts:
                        return ActionResult(False, f"Refusing to update '{target or 'all'}': "
                                            "incompatible source resolutions for a shared "
                                            "checkout.",
                                            details=[f"  {c}" for c in plan_conflicts])
                    inst = self._installer()
                    out, ok = [], True
                    mutated_paths = []
                    stacks_by_comp = {c2.id: st2 for st2 in self.stacks()
                                      for c2 in st2.components}
                    for path, c, selector, resolved in groups:
                        r = self._adopt_dev_fallback(
                            inst, stacks_by_comp.get(c.id), c, selector, resolved,
                            force=True, locked=True)
                        out.append(f"  [{r.status}] {c.id}: {r.detail}")
                        if r.status == "failed":
                            ok = False                    # incl. prior-dirty: NEVER success
                            if r.detail.startswith("prior-dirty:"):
                                # the NEW source IS active (record coherent) — its stacks'
                                # stale candidates must still be retired truthfully
                                mutated_paths.append(path)
                        elif r.status == "done":
                            mutated_paths.append(path)
                        self._op_seam("update-between-groups")
                    # Candidate retirement BEFORE the source locks release: a new healthy
                    # Start (which needs these locks) cannot write a fresh marker between
                    # the source mutation and this retirement — only stale pre-update
                    # markers are retired. A clear failure is a truthful INCOMPLETE.
                    ok = self._retire_candidates_for_paths(mutated_paths, out) and ok
        except AdmissionRefused as _adm:
            return ActionResult(False, _adm.reason, data={'admission_blocked': _adm.tag})
        except SourceTxnBlocked as blocked:
            return ActionResult(False, f"Update blocked for '{target or 'all'}': {blocked}",
                                next_commands=["lhpc status"])
        except reslock.ResourceBusy as busy:
            return ActionResult(False, f"Update blocked for '{target or 'all'}': {busy}",
                                next_commands=["lhpc status"])
        return ActionResult(ok, f"Update {'applied' if ok else 'INCOMPLETE'} for "
                            f"'{target or 'all'}'.", details=out,
                            next_commands=["lhpc status --versions"])

    def _remove_source_leaf(self, path: str, comp, consumers: dict, inst,
                            allow_dirty: bool) -> tuple:
        """RACE-SAFE destructive removal of one managed source leaf (uninstall / Clean all),
        under the caller's held source locks. Protocol:

          1. refuse while ORPHANED QUARANTINE evidence from an interrupted removal exists
             (retained, never auto-deleted — the operator inspects it first);
          2. CAPTURE the leaf (retained no-follow handle), then prove CURRENT ownership +
             identity — and cleanliness, unless `allow_dirty` (Clean) — AGAINST THE CAPTURED
             INODE;
          3. detach-then-remove via `source_fs.detach_and_remove`: the leaf is atomically
             detached to a controller-owned quarantine name ONLY while it is still the
             captured leaf, re-proven after the detach, and only then removed. An external
             substitution at any point is preserved and reported — never deleted.

        A linked source loses only its verified runtime symlink LEAF; the external target is
        never modified. Returns (removed, detail-lines)."""
        from . import source_fs, source_registry
        conflict = self._shared_remote_conflict(path)
        if conflict:
            return False, [f"  [refused] {path}: {conflict}"]
        dest = self._paths.resolve_source(path)
        stale = source_fs.quarantine_siblings(self._paths, dest)
        if stale:
            return False, [f"  [refused] {path}: quarantine evidence from an interrupted "
                           f"removal exists ({', '.join(stale)}) — inspect and remove it "
                           "manually before retrying"]
        handle = None
        try:
            try:
                handle = source_fs.capture_leaf(self._paths, dest)
            except (OSError, PathContainmentError) as exc:
                return False, [f"  [refused] {path}: {exc}"]
            rec, why = source_registry.verify_identity(
                self._paths, self._system, self.config(), comp, dest,
                components=tuple(sorted(consumers.get(path, {comp.id}))), handle=handle)
            if rec is None:
                return False, [f"  [refused] {path}: {why}"]
            final_check = None
            if rec.strategy != "link" and not allow_dirty:
                dirty = inst.dirty_report(Path(handle.pinned_path()), path)
                if dirty:
                    return False, ([f"  [refused] {path}: local changes present — "
                                    "not removed (use Clean to remove anyway)"]
                                   + dirty.lines())

                def final_check(h=handle, p=path):
                    # FINAL dirty recheck immediately before the irreversible detach —
                    # a file created after the initial check must preserve the source.
                    fresh = inst.dirty_report(Path(h.pinned_path()), p)
                    if fresh:
                        return ("local changes appeared before removal — source preserved "
                                "(commit/stash or remove the new files, then retry)")
                    return ""
            removed, msg = source_fs.detach_and_remove(self._paths, dest, handle,
                                                       final_check=final_check)
            if not removed:
                return False, [f"  [fail] {path}: {msg}"]
            # REOCCUPATION recheck: the ownership record may be dropped (and success
            # reported) only while the original destination is STILL absent — a leaf that
            # re-appeared during removal is unverified foreign content and must surface as
            # an incomplete/recovery outcome with truthful evidence, never silent success.
            try:
                if source_fs.leaf_kind(self._paths, dest) != "absent":
                    return False, [f"  [fail] {path}: destination was reoccupied during "
                                   "removal — ownership record retained; inspect the new "
                                   "leaf before retrying (recovery required)"]
            except PathContainmentError as exc:
                return False, [f"  [fail] {path}: destination unsafe after removal ({exc})"]
            return True, [f"  [removed] src/{path.split('/')[-1]}"]
        finally:
            if handle is not None:
                handle.close()

    def _classify_uninstall_paths(self, items, target_ids) -> tuple:
        """Remove / keep-shared / orphan classification for an uninstall — DESCRIPTOR-PROVEN
        leaf state (never `exists()`), manifest-wide consumers including `build_requires`
        edges; an ABSENT leaf with a lingering ownership record becomes an ORPHAN-cleanup
        item. The APPLY path calls this again UNDER the operation locks so the destructive
        set is derived from post-lock reality, never a stale preflight."""
        from . import source_fs as _sfs, source_registry as _sreg
        consumers = self._source_consumers()
        to_remove: dict = {}
        kept: list = []
        orphans: list = []
        for _, c in items:
            path = c.source.path
            try:
                kind = _sfs.leaf_kind(self._paths, self._paths.resolve_source(path))
            except PathContainmentError:
                kind = "special"
            if kind == "absent":
                if _sreg.read_record(self._paths, path) is not None and path not in orphans:
                    orphans.append(path)
                continue
            remaining = sorted(self._live_consumers(path, consumers) - target_ids)
            if remaining:
                if path not in {p for p, _ in kept}:
                    kept.append((path, remaining))
            else:
                to_remove.setdefault(path, c)
        return to_remove, kept, orphans, consumers

    def _live_consumers(self, path: str, consumers: dict) -> set:
        """LIVE consumer membership of a shared source path: the manifest declarers
        INTERSECTED with the ownership record's `components` (departures are decremented
        there by uninstall/clean, so a sibling that already departed no longer keeps the
        leaf alive). Absent/legacy record -> manifest fallback (safe-side keep)."""
        from . import source_registry as _sreg
        manifest = set(consumers.get(path, set()))
        state, rec, _why = _sreg.record_state(self._paths, path)
        if state == "valid" and rec.components:
            # Membership tracks DIRECT declarers only; DERIVED consumers (build_requires
            # edges, e.g. the daemon needing RadioLib) are live by construction and are
            # never intersected away.
            declarers = {c.id for c in self._path_declarers(path)}
            derived = manifest - declarers
            return (manifest & declarers & set(rec.components)) | derived
        return manifest

    def _depart_kept_paths(self, kept, target_ids, out: list) -> bool:
        """Durably record the departing stack's components leaving each KEPT shared
        path's ownership record (under the caller's held source locks). A failed rewrite
        is a truthful INCOMPLETE — the retry converges. Returns overall ok."""
        from . import source_registry as _sreg
        ok = True
        for path, remaining in kept:
            state, rec, _why = _sreg.record_state(self._paths, path)
            if state != "valid":
                continue                          # legacy/unowned: manifest fallback rules
            new_members = set(rec.components) - set(target_ids)
            if set(rec.components) == new_members:
                continue                          # nothing of ours recorded there
            if not new_members:
                continue                          # would be empty -> removal path owns it
            if _sreg.update_components(self._paths, path, new_members):
                out.append(f"  [departed] {path}: now used by "
                           f"{', '.join(sorted(new_members))}")
            else:
                out.append(f"  [fail] {path}: shared-consumer record could not be "
                           "updated — re-run to retry (the checkout is otherwise kept)")
                ok = False
        return ok

    def controller_uninstall_prep(self) -> ActionResult:
        """INTERNAL op invoked by uninstall.sh BEFORE it removes any controller code/state. Held under
        the ONE task-admission lock so a NEW task start CONTENDS (fails its own locked admission) while
        prep runs; it then proves quiescence from DURABLE evidence and stops the managed stacks with
        VERIFIED cessation. Fail CLOSED — any doubt refuses and leaves everything in place. (uninstall.sh
        already wrote the .lhpc-uninstalling guard, so prep does NOT run the strict self-check — it IS
        the uninstall — it inspects the durable job/auto-install/HMAC/snapshot evidence instead.)"""
        import contextlib
        from . import reslock
        with contextlib.ExitStack() as adm:
            try:
                self._acquire_key(adm, self.ADMISSION_KEY, "uninstall-prep", "")
            except reslock.ResourceBusy:
                return ActionResult(False, "A task is starting right now (admission lock contended) — "
                                    "retry the uninstall.", data={"prep_blocked": "busy"})
            return self._uninstall_prep_locked()

    def _uninstall_prep_locked(self) -> ActionResult:
        from .model import RunState
        LIVE = (RunState.RUNNING, RunState.DEGRADED)
        # 1) a controller self-update in flight is incompatible with tearing the controller down
        try:
            req = self.classify_request()
        except Exception as exc:                          # noqa: BLE001 — cannot prove safe -> refuse
            return ActionResult(False, f"Cannot verify self-update state ({exc}) — refusing to prepare "
                                "uninstall.", data={"prep_blocked": "unverifiable"})
        if req in ("pending", "in_flight", "malformed"):
            return ActionResult(False, "A controller self-update is pending or in progress — resolve it "
                                "before uninstalling.", data={"prep_blocked": "self_update"})
        # 2) active OR unprovable build/test/web-job markers
        jobs = self.active_jobs(include_unsafe=True)
        if jobs:
            return ActionResult(False, "Active or unprovable build/test/web jobs exist — resolve them "
                                "before uninstalling.",
                                details=tuple(f"  {j.get('name') or j.get('op', 'job')} "
                                              f"{j.get('reason') or j.get('target', '')}" for j in jobs),
                                data={"prep_blocked": "jobs"})
        # 3) unresolved auto-install / HMAC state
        try:
            ai_block = self._auto_install_gate()
        except Exception as exc:                          # noqa: BLE001 — cannot prove safe -> refuse
            ai_block = f"unverifiable ({exc})"
        if ai_block:
            return ActionResult(False, f"An auto-install run is unresolved — {ai_block}.",
                                data={"prep_blocked": "auto_install"})
        try:
            hst = self.hmac_apply_status()
            hmac_bad = bool(hst) and (hst.get("unsafe") or hst.get("phase") in ("running", "interrupted"))
        except Exception:                                 # noqa: BLE001 — cannot prove safe -> refuse
            hmac_bad = True
        if hmac_bad:
            return ActionResult(False, "HMAC apply state is running or unresolved/unsafe — resolve it "
                                "before uninstalling.", data={"prep_blocked": "hmac"})
        # 4) INITIAL fresh snapshot (an exception is fail-closed); an UNKNOWN component blocks
        try:
            snap = self.build_snapshot(fresh=True)
        except Exception as exc:                          # noqa: BLE001 — cannot prove state -> refuse
            return ActionResult(False, f"Could not assess component runtime state ({exc}) — refusing to "
                                "uninstall.", data={"prep_blocked": "snapshot"})
        unknown = [f"{ss.stack.id}/{cid}" for ss in snap.stacks
                   for cid, cs in ss.components.items() if cs.run_state == RunState.UNKNOWN]
        if unknown:
            return ActionResult(False, "Component runtime state is UNKNOWN (cannot prove it stopped) — "
                                "refusing to uninstall.", details=tuple(f"  {u}" for u in unknown),
                                data={"prep_blocked": "unknown"})
        # 5) stop CLIENT stacks BEFORE the shared daemon; check EVERY stop. A client stop that is not
        #    verified stopped returns IMMEDIATELY — the daemon is never stopped.
        details = []
        clients = [ss.stack.id for ss in snap.stacks if ss.stack.main != self.DAEMON_ID
                   and any(cs.run_state in LIVE for cs in ss.components.values())]
        daemons = [ss.stack.id for ss in snap.stacks if ss.stack.main == self.DAEMON_ID
                   and any(cs.run_state in LIVE for cs in ss.components.values())]
        for sid in clients:
            res = self.stop(sid, apply=True)
            details.append(f"  stop {sid}: {res.summary}")
            if not res.ok:
                return ActionResult(False, f"Client stack '{sid}' did not stop cleanly — NOT stopping "
                                    "the shared daemon, and refusing to remove controller state.",
                                    details=tuple(details), data={"prep_blocked": "client_stop_failed"})
        for sid in daemons:
            res = self.stop(sid, apply=True)
            details.append(f"  stop {sid}: {res.summary}")
            if not res.ok:
                return ActionResult(False, f"Daemon stack '{sid}' did not stop cleanly — refusing to "
                                    "remove controller state.", details=tuple(details),
                                    data={"prep_blocked": "daemon_stop_failed"})
        # 6) FINAL fresh snapshot (an exception is fail-closed); nothing may still be running/degraded/unknown
        try:
            snap2 = self.build_snapshot(fresh=True)
        except Exception as exc:                          # noqa: BLE001 — cannot prove state -> refuse
            return ActionResult(False, f"Could not re-assess component runtime state ({exc}) — refusing "
                                "to uninstall.", data={"prep_blocked": "snapshot"})
        still = [f"{ss.stack.id}/{cid}={cs.run_state.value}" for ss in snap2.stacks
                 for cid, cs in ss.components.items()
                 if cs.run_state in LIVE or cs.run_state == RunState.UNKNOWN]
        if still:
            return ActionResult(False, "Some components are still running/degraded/unknown after stop — "
                                "refusing to remove controller state.",
                                details=tuple(details) + tuple(f"  STILL {s}" for s in still),
                                data={"prep_blocked": "not_quiesced"})
        return ActionResult(True, "Quiescent: all managed stacks stopped and verified — safe to remove "
                            "controller state.", details=tuple(details), data={"quiescent": True})

    def controller_uninstall_guard_claim(self, pid: str, nonce: str, start_time: str) -> ActionResult:
        """Atomically CLAIM the uninstall guard for uninstall.sh — created O_CREAT|O_EXCL|O_NOFOLLOW via
        `open_marker_excl`, so a pre-existing guard of ANY kind (regular / symlink / special / stale) is
        NEVER truncated, followed or replaced. `FileExistsError` => a concurrent or interrupted uninstall
        already owns it => refused. Records the CALLER's (the shell's) pid + nonce + /proc start time so a
        live owner is distinguishable from PID reuse."""
        import json
        from . import runtime_fs, updater_units
        from .service_base import _proc_ceased
        guard = self._paths.under(updater_units.UNINSTALL_GUARD)
        payload = json.dumps({"pid": pid, "nonce": nonce, "start_time": start_time})
        try:
            m = runtime_fs.open_marker_excl(self._paths, guard, payload)
            m.close()
            return ActionResult(True, "uninstall guard claimed.", data={"nonce": nonce})
        except FileExistsError:
            pass                                          # a guard exists — decide below
        except Exception as exc:                          # noqa: BLE001 — containment/fs error
            return ActionResult(False, f"Could not claim the uninstall guard: {exc}",
                                data={"claim_failed": True})
        # A guard already exists. RECLAIM it ONLY if its recorded owner is PROVEN DEAD (an interrupted
        # uninstall — preserving a safe retry path); a LIVE owner is a real concurrent uninstall (refuse);
        # a malformed/unreadable/foreign-typed guard is refused (never blindly replaced).
        try:
            rec = json.loads(runtime_fs.read_text_regular(self._paths, guard, max_bytes=4096))
            owner_pid, owner_start = int(rec["pid"]), int(rec["start_time"])
        except Exception:                                 # noqa: BLE001 — cannot prove -> refuse
            return ActionResult(False, "An uninstall guard already exists and is unreadable/malformed "
                                "— refusing (verify no uninstall runs, then remove it).",
                                data={"guard_unsafe": True})
        if not _proc_ceased(owner_pid, owner_start):
            return ActionResult(False, "An uninstall is already in progress (its guard owner is alive) "
                                "— refusing.", data={"guard_live": True})
        try:
            runtime_fs.unlink(self._paths, guard)         # descriptor-safe, no-follow
            m = runtime_fs.open_marker_excl(self._paths, guard, payload)
            m.close()
        except Exception as exc:                          # noqa: BLE001
            return ActionResult(False, f"could not reclaim the stale uninstall guard: {exc}",
                                data={"reclaim_failed": True})
        return ActionResult(True, "reclaimed a STALE uninstall guard (a previous uninstall was "
                            "interrupted).", data={"nonce": nonce, "reclaimed": True})

    def controller_uninstall_guard_release(self, nonce: str) -> ActionResult:
        """Remove the uninstall guard ONLY if it is the one this invocation owns (its recorded `nonce`
        matches). Strict no-follow, regular-only, bounded read + nonce compare, then a descriptor-safe
        unlink. NEVER removes a pre-existing / foreign / replaced / unreadable guard. Absent = no-op."""
        import json
        from . import runtime_fs, updater_units
        from .paths import PathContainmentError
        path = self._paths.under(updater_units.UNINSTALL_GUARD)
        try:
            rec = json.loads(runtime_fs.read_text_regular(self._paths, path, max_bytes=4096))
        except FileNotFoundError:
            return ActionResult(True, "no uninstall guard to release.", data={"absent": True})
        except (OSError, PathContainmentError, ValueError):
            return ActionResult(False, "uninstall guard is unreadable/unsafe — NOT removing it "
                                "(foreign or tampered).", data={"unsafe": True})
        if not (isinstance(rec, dict) and rec.get("nonce") == nonce):
            return ActionResult(False, "uninstall guard is owned by a different invocation — NOT "
                                "removing it.", data={"foreign": True})
        try:
            runtime_fs.unlink(self._paths, path)
        except (OSError, PathContainmentError) as exc:
            return ActionResult(False, f"could not remove the uninstall guard: {exc}",
                                data={"unlink_failed": True})
        return ActionResult(True, "uninstall guard released.", data={"released": True})

    def uninstall(self, target: str, apply: bool = False) -> ActionResult:
        """Remove managed runtime sources for `target`. Refuses if a target
        component is running; never removes a source still referenced by another
        component (shared checkout); never touches config, secrets or profiles."""
        if (_r := self._controller_refusal(target)) is not None:
            return _r
        items = self._with_source(target)
        if not items:
            return self._unknown_stack(target) if target else ActionResult(False, "No sources.")
        target_ids = {c.id for _, c in items}

        # 1) Refuse while any target component is running.
        snap = self.build_snapshot()
        up = (RunState.RUNNING, RunState.DEGRADED)
        running = sorted(cid for ss in snap.stacks for cid, st in ss.components.items()
                         if cid in target_ids and st.run_state in up)
        if running:
            return ActionResult(
                False, f"Refusing to uninstall '{target or 'all'}': component(s) running.",
                details=[f"  running: {', '.join(running)} — stop them first"],
                next_commands=[f"lhpc stack stop {target} --yes"])

        # 2+3) remove vs keep-shared vs orphan classification (DESCRIPTOR-PROVEN leaf
        # state; consumers include build_requires edges). Computed here for the PLAN
        # preview — the APPLY path recomputes it fresh UNDER the operation locks.
        to_remove, kept, orphans, consumers = self._classify_uninstall_paths(items,
                                                                             target_ids)

        details = [f"  [remove] src/{p.split('/')[-1]} ({c.id})" for p, c in to_remove.items()]
        details += [f"  [keep — shared] src/{p.split('/')[-1]} still used by {', '.join(r)}"
                    for p, r in kept]
        details += [f"  [cleanup] orphaned ownership record for {p} (source already absent)"
                    for p in orphans]
        details.append("  (config, secrets and profiles are preserved)")
        if not apply:
            return ActionResult(
                True, f"Uninstall plan for '{target or 'all'}': remove {len(to_remove)}, "
                f"keep {len(kept)} shared.", details=details,
                next_commands=[f"lhpc uninstall {target} --yes"]
                if (to_remove or orphans) else [],
                data={"changes": len(to_remove) + len(orphans)})

        from . import reslock, source_registry
        inst = self._installer()
        out, ok = [], True
        # SERIALIZATION ORDER (whole applied operation): config-stable shared lock ->
        # source-txn index/recovery -> ALL of the target's source-path locks (including
        # paths KEPT because they are shared — a Start of the target stack needs those
        # locks, so holding them serializes against it) -> FRESH running recheck ->
        # fresh remove/keep/orphan classification -> destructive work -> candidate
        # retirement — all before any lock is released. A concurrent remote/config save
        # waits on config-stable; nothing here uses a stale configuration snapshot.
        self._op_seam("uninstall-preflight")
        all_paths = sorted({c.source.path for _, c in items})
        try:
            # LOCK ORDER #1: admission OUTSIDE config-stability (inner source guard reuses reentrantly).
            with self._admission_guard("uninstall", target or ""), self._config_stable():
              with self._source_operation_guard(all_paths, op="uninstall"):
                self._op_seam("uninstall-locked")
                # AUTHORITATIVE running recheck AFTER all locks are held: a Start that
                # slipped in after the preflight refuses with ZERO mutation (no source,
                # config, log, marker, known-working, or registry change).
                snap = self.build_snapshot(fresh=True)     # UNDER locks: never a cached read
                running = sorted(cid for ss in snap.stacks
                                 for cid, st in ss.components.items()
                                 if cid in target_ids and st.run_state in up)
                if running:
                    return ActionResult(
                        False, f"Refusing to uninstall '{target or 'all'}': component(s) "
                        "started while the uninstall was acquiring its locks.",
                        details=[f"  running: {', '.join(running)} — stop them first"],
                        next_commands=[f"lhpc stack stop {target} --yes"])
                # Recompute the destructive set from POST-LOCK reality.
                to_remove, kept, orphans, consumers = self._classify_uninstall_paths(
                    items, target_ids)
                removed_paths = []
                for path, c in to_remove.items():
                    removed, lines = self._remove_source_leaf(path, c, consumers, inst,
                                                              allow_dirty=False)
                    out.extend(lines)
                    if not removed:
                        ok = False
                        continue
                    removed_paths.append(path)
                    if not source_registry.remove_record(self._paths, path):
                        # Registry-record removal is REQUIRED cleanup: its failure makes the
                        # uninstall INCOMPLETE (truthful), and the orphan record is retried by
                        # a later explicit uninstall/clean (the identity verifier refuses to
                        # let it authorize any future tree at this path).
                        out.append(f"  [fail] {path}: source removed, but the ownership "
                                   "record could not be dropped — re-run uninstall to retry")
                        ok = False
                for path in orphans:
                    # Orphaned record at an ABSENT leaf (a prior record-removal failure):
                    # explicit retry clears it.
                    if source_registry.remove_record(self._paths, path):
                        out.append(f"  [cleaned] orphaned ownership record for {path}")
                    else:
                        out.append(f"  [fail] {path}: orphaned ownership record could not "
                                   "be removed")
                        ok = False
                # a removed source retires the affected stacks' last-start candidates —
                # an older composition must not stay eligible for later confirmation
                ok = self._retire_candidates_for_paths(removed_paths, out) and ok
                # KEPT shared paths: durably record this stack's departure so the LAST
                # sharer's uninstall removes the leaf (live membership, not manifest).
                ok = self._depart_kept_paths(kept, target_ids, out) and ok
        except AdmissionRefused as _adm:
            return ActionResult(False, _adm.reason, data={'admission_blocked': _adm.tag})
        except SourceTxnBlocked as blocked:
            return ActionResult(False, f"Uninstall blocked for '{target or 'all'}': {blocked}",
                                next_commands=["lhpc status"])
        except reslock.ResourceBusy as busy:
            return ActionResult(False, f"Uninstall blocked for '{target or 'all'}': {busy}",
                                next_commands=["lhpc status"])
        out += [f"  [kept — shared] src/{p.split('/')[-1]} (used by {', '.join(r)})"
                for p, r in kept]
        return ActionResult(ok, f"Uninstall {'applied' if ok else 'incomplete'} for "
                            f"'{target or 'all'}' (config preserved).",
                            details=out, next_commands=["lhpc status"])

    @invalidates_snapshot
    def clean(self, target: str, apply: bool = False, purge: bool = False) -> ActionResult:
        """DESTRUCTIVE per-stack purge ("Clean all"): removes every LHPC-OWNED trace of the
        stack — sources (still ownership-verified + shared-refcounted; DIRTY allowed here,
        this is the explicit escape hatch), config/stacks/<sid>*, state markers, known-working
        store, its components' logs + job logs (never an active job's), and registry records.
        Gates: a STACK target only; refused while anything runs; `apply` additionally requires
        `purge` (CLI double flag; the web adds a typed confirm). local.toml, secrets, and every
        other stack are untouched. All removal is descriptor-anchored/no-follow; a linked
        source loses only its runtime symlink leaf."""
        if (_r := self._controller_refusal(target)) is not None:
            return _r
        from . import known_working, reslock, source_fs, source_registry
        stack = self.stack(target)
        if stack is None:
            return self._unknown_stack(target)
        sid = stack.id
        comp_ids = {c.id for c in stack.components}

        snap = self.build_snapshot()
        up = (RunState.RUNNING, RunState.DEGRADED)
        running = sorted(cid for ss in snap.stacks for cid, st in ss.components.items()
                         if cid in comp_ids and st.run_state in up)
        if running:
            return ActionResult(False, f"Refusing to clean '{sid}': component(s) running.",
                                details=[f"  running: {', '.join(running)} — stop them first"],
                                next_commands=[f"lhpc stack stop {sid} --yes"])

        # Removal set (computed up front so the dry-run names EXACTLY what apply removes).
        consumers = self._source_consumers()
        from . import source_fs as _sfs, source_registry as _sreg
        src_remove, src_keep = [], []
        orphans: list = []                       # absent leaves with a stale ownership record
        for c in stack.components:
            if c.source is None:
                continue
            path = c.source.path
            if path in {p for p, _ in src_remove} or path in {p for p, _ in src_keep} \
                    or path in orphans:
                continue
            try:
                kind = _sfs.leaf_kind(self._paths, self._paths.resolve_source(path))
            except PathContainmentError:
                kind = "special"
            if kind == "absent":
                if _sreg.read_record(self._paths, path) is not None:
                    orphans.append(path)         # explicit retry clears the orphan record
                continue
            remaining = sorted(self._live_consumers(path, consumers) - comp_ids)
            (src_keep if remaining else src_remove).append((path, remaining))
        cfg_dir = self._paths.under("config", "stacks")
        cfg_files = []
        try:
            for name, is_link in runtime_fs.scandir_nofollow(self._paths, cfg_dir):
                if not is_link and (name == f"{sid}.toml" or name.startswith(f"{sid}@")):
                    cfg_files.append(name)
        except PathContainmentError:
            pass
        log_prefixes = tuple({f"install-{sid}"} | {f"{op}-{cid}" for op in ("build", "test",
                             "start", "post") for cid in comp_ids})
        markers = [self._interactive_marker(sid), self._band_marker(sid),
                   known_working.candidate_path(self._paths, sid),
                   self._restart_marker_path(sid),
                   known_working.store_path(self._paths, sid)]

        details = [f"  [remove] src/{p.split('/')[-1]}" for p, _ in src_remove]
        details += [f"  [keep — shared] src/{p.split('/')[-1]} (used by {', '.join(r)})"
                    for p, r in src_keep]
        details += [f"  [cleanup] orphaned ownership record for {p} (source already absent)"
                    for p in orphans]
        details += [f"  [remove] config/stacks/{n}" for n in cfg_files]
        details += [f"  [remove] logs matching {', '.join(sorted(log_prefixes))}*",
                    "  [remove] state markers, known-working history, ownership records",
                    "  (config/local.toml, secrets and other stacks are untouched)"]
        if not apply:
            return ActionResult(
                True, f"CLEAN plan for '{sid}': DESTRUCTIVE — removes sources, config, logs "
                "and history for this stack.", details=details,
                next_commands=[f"lhpc clean {sid} --purge --yes"],
                data={"changes": len(src_remove) + len(orphans) + len(cfg_files) + 1})
        if not purge:
            return ActionResult(False, f"Refusing to clean '{sid}': destructive purge "
                                "requires the explicit purge confirmation.",
                                next_commands=[f"lhpc clean {sid} --purge --yes"])

        out, ok = [], True
        self._op_seam("clean-preflight")
        # SERIALIZATION ORDER (whole applied purge): config-stable shared lock ->
        # source-txn index/recovery -> ALL of the stack's source-path locks (INCLUDING
        # kept/shared paths and even a source-less stack — a Start of this stack needs
        # these locks, so config/log/marker cleanup is serialized against it too) ->
        # FRESH running recheck -> fresh removal-set recompute -> destructive work.
        all_paths = sorted({c.source.path for c in stack.components if c.source}
                           | set(orphans)) or [sid]
        try:
            # LOCK ORDER #1: admission OUTSIDE config-stability (inner source guard reuses reentrantly).
            with self._admission_guard("clean", sid), self._config_stable():
                with self._source_operation_guard(all_paths, op="clean"):
                    self._op_seam("clean-locked")
                    # AUTHORITATIVE running recheck AFTER all locks are held: a Start that
                    # slipped in after the preflight refuses with ZERO mutation — no
                    # source, config, log, marker, known-working, or registry cleanup.
                    snap = self.build_snapshot(fresh=True)   # UNDER locks: never a cached read
                    running = sorted(cid for ss in snap.stacks
                                     for cid, st in ss.components.items()
                                     if cid in comp_ids and st.run_state in up)
                    if running:
                        return ActionResult(
                            False, f"Refusing to clean '{sid}': component(s) started while "
                            "the clean was acquiring its locks.",
                            details=[f"  running: {', '.join(running)} — stop them first"],
                            next_commands=[f"lhpc stack stop {sid} --yes"])
                    # Recompute the destructive sets from POST-LOCK reality (the dry-run
                    # preview above may predate the locks).
                    consumers = self._source_consumers()
                    src_remove, src_keep = [], []
                    orphans = []
                    for c in stack.components:
                        if c.source is None:
                            continue
                        path = c.source.path
                        if path in {p for p, _ in src_remove} \
                                or path in {p for p, _ in src_keep} or path in orphans:
                            continue
                        try:
                            kind = source_fs.leaf_kind(self._paths,
                                                       self._paths.resolve_source(path))
                        except PathContainmentError:
                            kind = "special"
                        if kind == "absent":
                            if source_registry.read_record(self._paths, path) is not None:
                                orphans.append(path)
                            continue
                        remaining = sorted(self._live_consumers(path, consumers)
                                           - comp_ids)
                        (src_keep if remaining else src_remove).append((path, remaining))
                    # 1) sources — race-safe capture/verify/detach removal under the held
                    # lock; dirty TRACKED/UNTRACKED changes are allowed (explicit purge), but
                    # a drifted commit/remote/leaf-type still refuses (not LHPC's anymore).
                    inst = self._installer()
                    removed_paths = []
                    for path, _ in src_remove:
                        comp = next(c for c in stack.components
                                    if c.source and c.source.path == path)
                        removed, lines = self._remove_source_leaf(path, comp, consumers, inst,
                                                                  allow_dirty=True)
                        out.extend(lines)
                        if not removed:
                            ok = False
                            continue
                        removed_paths.append(path)
                        if not source_registry.remove_record(self._paths, path):
                            out.append(f"  [fail] {path}: source removed, but the ownership "
                                       "record could not be dropped — re-run clean to retry")
                            ok = False
                    ok = self._retire_candidates_for_paths(removed_paths, out) and ok
                    ok = self._depart_kept_paths(src_keep, comp_ids, out) and ok
                    for path in orphans:
                        if source_registry.remove_record(self._paths, path):
                            out.append(f"  [cleaned] orphaned ownership record for {path}")
                        else:
                            out.append(f"  [fail] {path}: orphaned ownership record could "
                                       "not be removed")
                            ok = False
                    # 2) per-stack config files
                    for name in cfg_files:
                        try:
                            runtime_fs.unlink(self._paths, cfg_dir / name)
                            out.append(f"  [removed] config/stacks/{name}")
                        except (OSError, PathContainmentError) as exc:
                            out.append(f"  [fail] config/stacks/{name}: {exc}")
                            ok = False
                    # 3) markers + known-working history (no-follow unlink; missing = done)
                    for m in markers:
                        try:
                            runtime_fs.unlink(self._paths, m)
                        except (OSError, PathContainmentError) as exc:
                            out.append(f"  [fail] {m.name}: {exc}")
                            ok = False
                    out.append("  [removed] state markers + known-working history")
                    # 4) logs (never an active job's — live evidence is preserved)
                    protected = {j.get("log") for j in self.active_jobs() if j.get("log")}
                    protected = {f"{n}.log" for n in protected} | protected
                    removed_logs = 0
                    try:
                        entries = runtime_fs.scandir_nofollow(self._paths,
                                                              self._paths.under("logs"))
                    except PathContainmentError:
                        entries = []
                    for name, is_link in entries:
                        if is_link or name in protected:
                            continue
                        stem = name[:-len(".log")] if name.endswith(".log") else name
                        if any(stem == p or stem.startswith(p + "-") for p in log_prefixes):
                            try:
                                runtime_fs.unlink(self._paths,
                                                  self._paths.under("logs", name))
                                removed_logs += 1
                            except (OSError, PathContainmentError):
                                ok = False
                                out.append(f"  [fail] logs/{name}")
                    out.append(f"  [removed] {removed_logs} log file(s)")
        except AdmissionRefused as _adm:
            return ActionResult(False, _adm.reason, data={'admission_blocked': _adm.tag})
        except SourceTxnBlocked as blocked:
            return ActionResult(False, f"Clean blocked for '{sid}': {blocked}",
                                next_commands=["lhpc status"])
        except reslock.ResourceBusy as busy:
            return ActionResult(False, f"Clean blocked for '{sid}': {busy}",
                                next_commands=["lhpc status"])
        out += [f"  [kept — shared] src/{p.split('/')[-1]} (used by {', '.join(r)})"
                for p, r in src_keep]
        return ActionResult(ok, f"Clean {'applied' if ok else 'INCOMPLETE'} for '{sid}'.",
                            details=out, next_commands=["lhpc status"])
