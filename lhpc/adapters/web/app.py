"""Local operator web console (Flask, server-rendered).

The web layer is thin: every route renders fresh state or dispatches an action
through `ControllerService` (the same service layer as the CLI — it never shells
out to the CLI). GET routes are read-only; state-changing actions are POST-only,
CSRF-protected, and show a plan + confirmation before applying.

Security posture:
  * loopback bind only (enforced in `run_server`); never 0.0.0.0;
  * GET = read-only; mutations = POST + CSRF token; unknown stack -> 404;
  * local-console security headers on every response (incl. CSP default-src 'self');
  * Jinja autoescaping escapes all untrusted data;
  * each request builds a fresh snapshot (no stale controller process state).
"""

from __future__ import annotations

import secrets as _secrets
import time
from typing import Callable

from flask import (
    Flask, abort, flash, jsonify, redirect, render_template, request, session, url_for,
)

from lhpc.core.outcomes import manual_required_only
from lhpc.core.services import ControllerService
from lhpc.core.status import rollup_states, stack_dependencies, summarize
from lhpc.version import __version__

_RUNNING = ("running", "degraded")

_LOOPBACK_HOSTS = {"127.0.0.1", "::1"}

_SECURITY_HEADERS = {
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    # Explicit directives so same-origin script + fetch (live polling) are
    # unambiguously allowed while everything else stays locked down.
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; connect-src 'self'; "
        "style-src 'self'; img-src 'self'; form-action 'self'; "
        "base-uri 'none'; frame-ancestors 'none'"
    ),
}

ServiceFactory = Callable[[], ControllerService]


def _parse_start_daemon_overrides(form):
    """STRICTLY parse the Start-confirm `dp_*` fields into a per-band map ``{band: {PARAM: value}}``
    (or None). Returns ``(per_band_or_None, error)``. Every field whose name starts with `dp_` must
    have the exact shape ``dp_<band>_<PARAM>`` (both parts non-empty) and appear once — a malformed
    or duplicated field name is rejected here (error != None) so the start fails BEFORE any launch.
    Unknown band / unknown parameter / value validity are enforced by the service normalizer
    (`_normalize_ephemeral_overrides`); this parser does not duplicate those rules. A blank value is
    carried through (the service treats blank/absent as "no override")."""
    per_band: dict = {}
    for name in form.keys():
        if not name.startswith("dp_"):
            continue
        if len(form.getlist(name)) > 1:                       # duplicated/conflicting field
            return None, f"duplicated daemon field {name!r}"
        parts = name.split("_", 2)                            # ["dp", band, PARAM]
        if len(parts) != 3 or not parts[1] or not parts[2]:   # malformed shape (dp_bad, dp_433_, dp_)
            return None, f"malformed daemon field {name!r}"
        _, band, param = parts
        per_band.setdefault(band, {})[param] = form[name]
    return (per_band or None), None


def create_app(service_factory: ServiceFactory | None = None) -> Flask:
    """Build the Flask app. `service_factory` is injectable for tests."""
    app = Flask(__name__)
    # Per-process secret for signed sessions / CSRF tokens (local console only).
    app.secret_key = _secrets.token_bytes(32)
    factory: ServiceFactory = service_factory or ControllerService
    service = factory()

    def _csrf_token() -> str:
        if "_csrf" not in session:
            session["_csrf"] = _secrets.token_hex(16)
        return session["_csrf"]

    def _csrf_ok() -> bool:
        sent = request.form.get("_csrf", "")
        return bool(sent) and _secrets.compare_digest(sent, session.get("_csrf", ""))

    app.jinja_env.globals["csrf_token"] = _csrf_token

    @app.context_processor
    def _inject_selfupdate():  # noqa: ANN202
        # Footer version/head indicator, on EVERY page. Reads the cached state marker ONLY — no git,
        # no network, no subprocess — so it never violates the no-network-GET rule. Fail-safe.
        try:
            return {"selfupdate": service.self_update_status()}
        except Exception:
            return {"selfupdate": {"version": __version__, "head_short": "", "available": False,
                                   "ver_color": "grey", "commit_color": "grey",
                                   "update_available": False}}

    @app.after_request
    def _set_headers(response):  # noqa: ANN001
        for key, value in _SECURITY_HEADERS.items():
            response.headers[key] = value
        return response

    def _runtime_root() -> str:
        return str(service.runtime_root)  # display only

    @app.get("/")
    def dashboard():  # noqa: ANN202
        # Radio-centric overview: one column per band (433/868) with daemon/radio
        # config, live monitor, the stack running on it, and a start-stack control.
        radios = service.radio_overview()
        # An interactive app that's been launched (marked) but isn't detected running
        # yet -> the operator is about to run it in a terminal; poll faster so the
        # dash flips to "running" quickly instead of waiting for the slow refresh.
        pending_interactive = any(not s.get("running") and not s.get("blocker")
                                  for r in radios for s in r["interactive"])
        return render_template(
            "dashboard.html", version=__version__, runtime_root=_runtime_root(),
            radios=radios, pending_interactive=pending_interactive,
            # Durable restart-required flags (file reads only): a yellow "Restart now"
            # action per flagged stack.
            restart_required=service.restart_required_stacks(),
            welcome=service.bulk_welcome(),
            # Controller (LHPC's own checkout) — CACHED only: version/head/identity/update
            # read from the self-update envelope, NEVER a live git/network/identity call.
            controller=service.controller_status(),
            dash_sig=service.dash_signature())

    @app.get("/api/dash-signature")
    def dash_signature_api():  # noqa: ANN202
        # Cheap structural-state signature; the dashboard reloads only when it changes.
        return jsonify(sig=service.dash_signature())

    _SRC_LABELS = (("pinned", "Known working"), ("dev", "Development"),
                   ("stable", "Latest stable"))

    def bulk_mod2_run_id_re():
        from lhpc.core import bulk as bulk_mod
        return bulk_mod.RUN_ID_RE

    def bulk_mod_terminal_ok():
        from lhpc.core import bulk as bulk_mod
        return bulk_mod.TERMINAL_OK

    @app.get("/install-all")
    def install_all_page():  # noqa: ANN202
        st = service.bulk_status()
        mode = service.bulk_mode()
        running = service.bulk_running()
        # A JUST-SPAWNED run: reservation live but the driver hasn't written its marker
        # yet — show a 'starting' card immediately (never a blank page after the POST).
        starting = False
        starting_run = ""
        # ... including when the previous run's TERMINAL marker is still on disk (a new
        # run may start over it without acknowledgement) and during the brief "spawning"
        # phase — the POST redirect lands within milliseconds of the spawn.
        if st is None or (not st.get("unsafe")
                          and st.get("state") in bulk_mod_terminal_ok()):
            from lhpc.core import bulk as bulk_mod, procident
            rstate, res = bulk_mod.read_reservation(service._paths)
            if (rstate == "valid"
                    and res.get("phase") in ("spawning", "spawned", "claimed")
                    and res.get("run_id") != (st or {}).get("run_id")
                    and procident.identity_matches(res.get("ident", {}),
                                                   res.get("pid", -1))):
                starting = True
                starting_run = res.get("run_id", "")
        # A spawned run that ended BEFORE claiming (typed preflight refusal): show its
        # actual output instead of silently falling back to the old run's card.
        spawn_failed = ""
        spawn_arg = request.args.get("spawn", "")
        if (spawn_arg and bulk_mod2_run_id_re().match(spawn_arg)
                and (st is None or st.get("run_id") != spawn_arg)):
            chunk = service.bulk_log_chunk(spawn_arg, 0)
            if chunk.get("data"):
                spawn_failed = chunk["data"][-4000:]
        gate = service._bulk_gate()
        recovery = service.bulk_recovery_reason()
        needs_ack = bool(recovery)
        orphan_risk = "ORPHAN RISK" in recovery
        chunk = {"offset": 0, "data": ""}
        if st and not st.get("unsafe"):
            chunk = service.bulk_log_chunk(st["run_id"], 0)
        return render_template(
            "install_all.html", version=__version__, runtime_root=_runtime_root(),
            st=st, mode=mode, running=running, gate=gate, needs_ack=needs_ack,
            recovery=recovery, orphan_risk=orphan_risk, starting=starting, starting_run=starting_run,
            spawn_failed=spawn_failed,
            log_seed=chunk.get("data", ""), src_labels=_SRC_LABELS)

    _TX_CONFIRM_TTL_S = 300.0

    def _stage_tx_confirmation(source: str, tests: bool) -> str:
        """SERVER-SIDE single-use RF confirmation: session-bound token tied to the exact
        source/tests/TX choices, the CSRF context, and a short expiry. Consumed atomically
        by the confirming POST — hidden-field values are never trusted on their own."""
        token = _secrets.token_hex(16)
        session["_bulk_tx_confirm"] = {"token": token, "source": source,
                                       "tests": bool(tests), "tx": True,
                                       "csrf": session.get("_csrf", ""),
                                       "exp": time.time() + _TX_CONFIRM_TTL_S}
        return token

    def _consume_tx_confirmation(token: str, source: str, tests: bool) -> str:
        """Validate + CONSUME the staged confirmation in one step (popped before any
        spawn — replay-proof). Returns "" when valid, else the typed refusal."""
        staged = session.pop("_bulk_tx_confirm", None)          # single-use: always consumed
        if not isinstance(staged, dict):
            return "no valid RF confirmation is staged — start again from the form"
        try:
            if not (isinstance(staged.get("token"), str) and staged["token"]
                    and isinstance(staged.get("exp"), (int, float))):
                return "the staged RF confirmation is malformed — start again"
            if time.time() > staged["exp"]:
                return "the RF confirmation has expired — start again"
            if not (token and _secrets.compare_digest(token, staged["token"])):
                return "the RF confirmation token does not match — start again"
            if not _secrets.compare_digest(session.get("_csrf", ""),
                                           staged.get("csrf", "")):
                return "the RF confirmation belongs to a different session — start again"
            if staged.get("source") != source or staged.get("tests") != bool(tests)                     or staged.get("tx") is not True:
                return ("the confirmed choices (version/tests/TX) changed after "
                        "confirmation — start again")
        except (TypeError, KeyError):
            return "the staged RF confirmation is malformed — start again"
        return ""

    @app.post("/install-all/start")
    def install_all_start():  # noqa: ANN202
        if not _csrf_ok():
            abort(400)
        source = request.form.get("source", "")
        tests = request.form.get("tests") == "yes"
        tx = request.form.get("tx") == "yes"
        if source not in service.SOURCE_CHOICES:
            flash("Unknown source choice.", "warn")
            return redirect(url_for("install_all_page"))
        if tx and not tests:
            flash("The TX test requires host tests to be enabled.", "warn")
            return redirect(url_for("install_all_page"))
        if tx:
            token = request.form.get("confirm_token", "")
            if not token:
                # FIRST TX-enabled POST: stage the server-side confirmation and render
                # the second page carrying the bound choices + one-time token.
                token = _stage_tx_confirmation(source, tests)
                return render_template(
                    "install_all_confirm.html", version=__version__,
                    runtime_root=_runtime_root(), source=source, tests=tests,
                    confirm_token=token,
                    src_label=dict(_SRC_LABELS).get(source, source))
            why = _consume_tx_confirmation(token, source, tests)
            if why:
                flash(f"RF confirmation refused: {why}.", "warn")
                return redirect(url_for("install_all_page"))
        job, err = service.spawn_bulk_job(source, tests, tx)
        if err:
            flash(err, "warn")
        else:
            flash("Bulk run started — this can take several minutes.", "ok")
        return redirect(url_for("install_all_page"))

    @app.post("/install-all/ack")
    def install_all_ack():  # noqa: ANN202
        if not _csrf_ok():
            abort(400)
        res = service.bulk_ack(
            confirm_orphan=request.form.get("confirm_orphan") == "yes")
        flash(res.summary, "ok" if res.ok else "warn")
        return redirect(url_for("install_all_page"))

    @app.get("/api/install-all")
    def install_all_api():  # noqa: ANN202
        st = service.bulk_status()
        out = {"state": st if st is not None else {"absent": True},
               "running": service.bulk_running(), "run_id": "", "log": {}}
        try:
            offset = int(request.args.get("offset", "0"))
        except ValueError:
            offset = -1
        # Whether a spawn reservation is LIVE (spawning/spawned/claimed, identity
        # proven): the starting card uses this to detect a child that ended BEFORE
        # writing its marker (e.g. the running-components preflight refusal).
        from lhpc.core import bulk as bulk_mod, procident
        rstate, res = bulk_mod.read_reservation(service._paths)
        out["spawn_live"] = bool(
            rstate == "valid"
            and res.get("phase") in ("spawning", "spawned", "claimed")
            and procident.identity_matches(res.get("ident", {}), res.get("pid", -1)))
        if st and not st.get("unsafe"):
            out["run_id"] = st["run_id"]
            out["log"] = service.bulk_log_chunk(st["run_id"], offset)
            # Second window: the sequential per-component build/test log stream.
            try:
                ci = int(request.args.get("ci", "0"))
                co = int(request.args.get("co", "0"))
            except ValueError:
                ci, co = 0, 0
            out["complog"] = service.bulk_component_log_chunk(st["run_id"], ci, co)
        return jsonify(**out)

    def _stack_groups():
        """Per-stack overview rows for the Stacks page."""
        snapshot = service.build_snapshot()  # fresh, read-only evidence each load
        rollup = rollup_states(snapshot)
        stack_deps = stack_dependencies([ss.stack for ss in snapshot.stacks])
        index = {}
        for ss in snapshot.stacks:
            for comp in ss.stack.components:
                index[comp.id] = (comp, ss.components[comp.id])

        groups = []
        for ss in snapshot.stacks:
            stack = ss.stack
            main = stack.main_component
            own = [(c, ss.components[c.id]) for c in stack.components
                   if not main or c.id != main.id]
            own.sort(key=lambda cs: (cs[0].start_order is None, cs[0].start_order or 0))
            cross_ids, seen = [], {c.id for c in stack.components}
            for c in stack.components:
                for dep in c.depends_on:
                    if dep not in seen and dep in index:
                        seen.add(dep)
                        cross_ids.append(dep)
            cross = [index[d] for d in cross_ids]
            main_status = index.get(main.id, (None, None))[1] if main else None
            has_source = bool(main and main.source)
            installed = bool(has_source and main_status and main_status.source_state.value
                             in ("match", "dirty", "differs", "unknown", "not-a-repo"))
            interactive = bool(main and main.interactive)
            groups.append({
                "stack": stack,
                "main": index.get(main.id) if main else None,
                "installed": installed,
                "has_source": has_source,
                "deps": own + cross,
                "state": rollup[stack.id],
                "running": rollup[stack.id] in _RUNNING,
                "dep_stacks": [(d, rollup.get(d, "unknown")) for d in stack_deps[stack.id]],
                "bands": sorted({c.band for c in stack.components if c.band}),
                "interactive": interactive,
                "command": service.manual_start_command(main) if interactive else "",
                # Per-stack Settings section rendered inline on the Apps list (same data the
                # stack-detail Settings uses): operator, component-scoped params, sources, autostart.
                "view": service.config_view(stack.id),
                "config_groups": service.config_param_groups(stack.id),
            })
        jobs_by_stack = {}
        for job in service.active_jobs():
            jobs_by_stack.setdefault(job.get("stack"), []).append(job)
        for g in groups:
            g["jobs"] = jobs_by_stack.get(g["stack"].id, [])
        groups.sort(key=lambda g: (not g["running"], len(g["dep_stacks"]), g["stack"].id))
        return groups, snapshot

    @app.get("/stacks")
    def stacks_overview():  # noqa: ANN202
        groups, snapshot = _stack_groups()
        return render_template(
            "stacks.html",
            version=__version__,
            runtime_root=_runtime_root(),
            snapshot=snapshot,
            summary=summarize(snapshot),
            groups=groups,
            bulk_mode=service.bulk_mode(),
            observed_conflicts=service.observed_conflicts(snapshot),   # band-aware (no false 433/868)
        )

    @app.get("/self-update")
    def self_update_page():  # noqa: ANN202
        # Read-only: cached status marker + local job list. No network, no git, no live
        # identity check — `controller_status()` reads the cached envelope only.
        return render_template("self_update.html", version=__version__, runtime_root=_runtime_root(),
                               st=service.self_update_status(), jobs=service.active_jobs(),
                               controller=service.controller_status(),
                               confirm=None, result=None, apply_data=None)

    @app.post("/self-update/check")
    def self_update_check():  # noqa: ANN202
        if not _csrf_ok():
            abort(400)
        res = service.self_update_check()          # NETWORK (explicit): git fetch, refresh cache
        flash(res.summary, "ok" if res.ok else "warn")
        return redirect(url_for("self_update_page"))

    @app.post("/self-update/apply")
    def self_update_apply():  # noqa: ANN202
        if not _csrf_ok():
            abort(400)
        st = service.self_update_status()
        jobs = service.active_jobs()
        force = request.form.get("overwrite") == "yes"
        if request.form.get("confirmed") != "yes":
            # Stage 1 — plan/confirm: warn that applying restarts the console (and, if the tree is
            # dirty, that local changes would be overwritten). A running job blocks the button.
            return render_template("self_update.html", version=__version__,
                                   runtime_root=_runtime_root(), st=st, jobs=jobs, result=None,
                                   apply_data=None, controller=service.controller_status(),
                                   confirm={"dirty": st.get("dirty"),
                                            "update_available": st.get("update_available")})
        # Stage 2 — apply (blocked if a job is active; dirty refused unless overwrite chosen).
        res = service.self_update_apply(force=force)
        flash(res.summary, "ok" if res.ok else "warn")
        return render_template("self_update.html", version=__version__, runtime_root=_runtime_root(),
                               st=service.self_update_status(), jobs=service.active_jobs(),
                               controller=service.controller_status(),
                               confirm=None, result=res, apply_data=res.data)

    @app.get("/stacks/<stack_id>")
    def stack_detail(stack_id: str):  # noqa: ANN202
        snapshot = service.build_snapshot()
        stack_status = snapshot.stack(stack_id)
        if stack_status is None:
            abort(404)
        member_ids = {x.id for x in stack_status.stack.components}
        main = stack_status.stack.main_component
        main_status = stack_status.components.get(main.id) if main else None
        has_source = bool(main and main.source)
        installed = bool(has_source and main_status and main_status.source_state.value
                         in ("match", "dirty", "differs", "unknown", "not-a-repo"))
        return render_template(
            "stack.html",
            version=__version__,
            runtime_root=_runtime_root(),
            stack=stack_status.stack,
            statuses=stack_status.components,
            installed=installed,
            has_source=has_source,
            # GET pages do NO network: freshness ("update available?") is an explicit
            # action (`lhpc update --check`), never a page-load git ls-remote.
            update_status="unknown",
            conflicts=[c for c in service.observed_conflicts(snapshot)   # band-aware (no false 433/868)
                       if any(h in member_ids for h in c.holders)],
            system_deps=service.system_deps(stack_id),
            # Grouped dependency diagnosis (system / build / runtime) — read-only, no network.
            deps=service.deps_report(stack_id),
            # Components installed but whose compiled binary is missing (e.g. dropped
            # by a fresh clone) -> they need a Build before they can run.
            needs_build=[c.id for c in stack_status.stack.components
                         if c.source and service._lifecycle().source_dir(c).exists()
                         and not service.is_built(c)],
            # Collapsed daemon-parameter panel, pre-populated from the config file.
            daemon_params=service.daemon_params_view(stack_id, request.args.get("band", "")),
            # Collapsed Settings section (the former per-stack Config page, moved here): operator,
            # component-scoped run/file params, source remotes, autostart, band switch + live radio.
            view=service.config_view(stack_id, request.args.get("band", "")),
            config_groups=service.config_param_groups(stack_id, request.args.get("band", "")),
            # Yellow "Confirm this stack as working" offer — FILE READS ONLY (candidate marker
            # + known-working store + the snapshot above); shown after a healthy start of a
            # composition that is not yet operator-confirmed.
            kw_offer=service.known_working_offer(stack_id, snapshot),
            # Durable restart-required flag (file read only): yellow "Restart now" action.
            restart_required=service.restart_required(stack_id),
        )

    @app.post("/stacks/<stack_id>/known-working/confirm")
    def known_working_confirm(stack_id: str):  # noqa: ANN202
        if not _csrf_ok():
            abort(400)
        if service.stack(stack_id) is None:
            abort(404)
        res = service.confirm_known_working(stack_id)
        flash(res.summary, "ok" if res.ok else "warn")
        return redirect(url_for("stack_detail", stack_id=stack_id))

    @app.post("/interactive/<stack_id>/dismiss")
    def interactive_dismiss(stack_id: str):  # noqa: ANN202
        if service.stack(stack_id) is None:
            abort(404)
        if not _csrf_ok():
            abort(400)
        service.dismiss_interactive(stack_id)
        return redirect(url_for("dashboard"))

    @app.get("/api/daemon/<band>")
    def daemon_api(band: str):  # noqa: ANN202
        if band not in ("433", "868"):
            abort(404)
        view = service.daemon_view(band)
        return jsonify(band=band, reachable=view.reachable, ready=view.ready,
                       radio_state=view.radio_state, status=view.status,
                       stats=view.stats, channel=view.channel,
                       feed=service.daemon_feed(band, 40))

    @app.get("/api/daemon/<band>/socket")
    def daemon_socket_api(band: str):  # noqa: ANN202
        # READ-ONLY live poll of the CONF socket for the "View Socket" monitor: one bounded,
        # sanitised status line per request (band validated -> never an arbitrary socket path;
        # fail-closed to '' when unreachable). The window/polling live entirely in the browser.
        if band not in ("433", "868"):
            abort(404)
        line = service.daemon_socket_line(band)
        return jsonify(band=band, line=line, reachable=bool(line))

    @app.post("/radio/<band>/set")
    def radio_set(band: str):  # noqa: ANN202
        # Apply a LIVE daemon setting (runtime) — same two-step plan + confirm as
        # every other mutation (P0.7). First POST shows the plan; a confirmed POST
        # applies. The key is whitelisted by the service; nothing transmits.
        if band not in ("433", "868"):
            abort(404)
        if not _csrf_ok():
            abort(400)
        key = request.form.get("key", "")
        value = request.form.get("value", "")
        if request.form.get("confirmed") != "yes":
            plan = service.daemon_set(band, key, value, apply=False)
            fsk = key.upper() == "MODE" and value.upper() == "FSK"
            return render_template("confirm_radio.html", version=__version__,
                                   runtime_root=_runtime_root(), band=band,
                                   key=key, value=value, plan=plan, fsk=fsk,
                                   warn_stacks=service.running_lora_stacks(band) if fsk else [])
        result = service.daemon_set(band, key, value, apply=True)
        # Truthful flash: the service summary already distinguishes "applied (confirmed)"
        # from "SENT (unconfirmed)". Never claim "Applied" for a setting the daemon does
        # not report back — an unconfirmed SET is flashed as a warning, not success.
        confirmed = bool(result.data.get("confirmed")) if result.data else False
        flash(result.summary, "ok" if (result.ok and confirmed) else "warn")
        return redirect(url_for("stack_detail", stack_id="daemon", cfg=1) + "#stack-settings")

    def _redirect_for(target: str):
        # Actions launched from the dashboard return to the dashboard; otherwise
        # land on the target's stack detail page.
        if request.form.get("from") == "dash":
            return redirect(url_for("dashboard"))
        sid = service.stack_of(target)
        return redirect(url_for("stack_detail", stack_id=sid) if sid else url_for("dashboard"))

    # START-confirm run/file/daemon params share the daemon-flag hide set + the confirm form.
    _HIDE_RUN = {"radio", "tx_433", "tx_868", "cadmon_433", "cadmon_868",
                 "cadrssi_433", "cadrssi_868"}

    def _collect_start_params(target: str, band: str):
        """(params, file_overrides, have_form) from the submitted confirm form, keyed by component-
        aware API key (bare when unique, `component.name` when the name collides). `have_form` marks
        a submission of the confirm form itself (`_params=1`) vs the initial dashboard POST — only
        then are unchecked flag checkboxes read as OFF and `pf_*` file overrides collected."""
        have_form = request.form.get("_params") == "1"
        params: dict = {}
        file_over = {} if have_form else None
        for f in service.start_param_fields(target, band):
            if f["kind"] == "run":
                if f["flag"] and have_form:
                    params[f["key"]] = "1" if request.form.get(f["field"]) else ""
                else:
                    params[f["key"]] = request.form.get(f["field"], f["saved"])
            elif have_form:                                   # file overrides only on a real submit
                if f["flag"]:
                    file_over[f["key"]] = "1" if request.form.get(f["field"]) else ""
                else:
                    v = request.form.get(f["field"])
                    if v is not None:
                        file_over[f["key"]] = v
        return params, file_over, have_form

    def _stack_save_values(target: str, band: str, params: dict, file_over: dict | None) -> dict:
        """Map the confirm 'Stack parameters' rows into a `save_config_bundle` values dict, keyed by
        component-aware API key (run -> `<key>`, file -> `file_<key>`; `key` is bare when unique,
        `component.name` when duplicated). Only the SAVABLE section rows are persisted — a stack
        without a savable section (the daemon, whose run params are ephemeral start options) saves
        nothing here."""
        values: dict = {}
        for r in service.stack_start_params(target, band, params, file_over):
            if r["field"].startswith("pf_"):
                values[f"file_{r['key']}"] = r["value"]
            else:
                values[r["key"]] = r["value"]
        return values

    def _save_daemon_confirm(target: str) -> dict:
        """Persist the inline daemon-radio panel values (per band) from the confirm form. Returns a
        STRUCTURED per-band result — {"parse_error": str|None, "bands": {band: bool}, "ok": bool} —
        so the caller can report EXACTLY which bands persisted (truthful partial persistence) and
        never over-claim. `ok` is True only when there was no parse error and every submitted band
        saved (a submission with no daemon values is trivially ok)."""
        per_band, err = _parse_start_daemon_overrides(request.form)
        if err:
            return {"parse_error": err, "bands": {}, "ok": False}
        bands: dict = {}
        for b in sorted((per_band or {}).keys()):
            bands[b] = service.save_daemon_params(target, b, per_band[b]).ok
        return {"parse_error": None, "bands": bands, "ok": all(bands.values()) if bands else True}

    def _render_confirm(op: str, target: str, band: str, params: dict, file_over,
                        source: str, frm: str, enforce_field: str = ""):
        """Stage-1 (and post-Save / enforcement re-render) of the confirm page."""
        run_params = service.run_params_for(target) if op == "start" else []
        hidden_params = [p for p in run_params if p.name in _HIDE_RUN]
        stack_params = (service.stack_start_params(target, band, params, file_over)
                        if op == "start" else None)
        stack_param_groups = (service.stack_start_param_groups(target, band, params, file_over)
                              if op == "start" else None)
        # Run params covered by the savable 'Stack parameters' panel; the rest (e.g. the daemon's
        # ephemeral `debug` start flag) render as PLAIN inputs — start options, never persisted.
        covered = {r["name"] for r in (stack_params or []) if r["field"].startswith("p_")}
        plain_params = [p for p in run_params
                        if p.name not in _HIDE_RUN and p.name not in covered] if op == "start" else []
        # Submitted-but-unsaved daemon-panel values (best-effort parse; a malformed field is ignored
        # for DISPLAY only) so a re-render after a failed Save/enforcement keeps the operator's edits.
        _dp_display, _ = (_parse_start_daemon_overrides(request.form) if op == "start" else (None, ""))
        plan = service.run_action(op, target, apply=False, params=params, source=source, band=band)
        return render_template(
            "confirm.html", version=__version__, runtime_root=_runtime_root(),
            op=op, target=target, plan=plan, tx=("tx" in op),
            hidden_params=hidden_params, plain_params=plain_params,
            params=params, source=source, band=band,
            stack_params=stack_params, stack_param_groups=stack_param_groups,
            enforce_field=enforce_field,
            blockers=(plan.data.get("blockers") if op == "start" else None),
            stop_deps=(plan.data.get("dependents") if op == "stop" else None),
            other_bands=(plan.data.get("other_bands") if op == "stop" else None),
            commands=(plan.data.get("commands") if op in ("start", "stop") else None),
            missing_deps=(service.missing_system_deps(target)
                          if op in ("install", "build") else None),
            daemon_panels=(service.daemon_start_panels(target, params, band, _dp_display)
                           if op == "start" else None),
            optional_starts=(service.optional_start_components(target)
                             if op == "start" else None),
            frm=frm,
            source_choices=service.SOURCE_CHOICES if op in ("install", "update") else None)

    @app.post("/action")
    def action():  # noqa: ANN202
        if not _csrf_ok():
            abort(400)
        op = request.form.get("op", "")
        target = request.form.get("target", "")
        if op not in service.WEB_ACTIONS:
            abort(400)
        band = request.form.get("band", "")    # chosen band for a band-switchable stack
        # Collect run + file params (only meaningful for start). The confirm 'Stack parameters'
        # panel submits p_<name> (run) / pf_<name> (file); defaults come from the saved config.
        params, file_over, _have = (_collect_start_params(target, band)
                                    if op == "start" else ({}, None, False))
        # Source version selector (only meaningful for install/update). A MISSING selector
        # defaults to the production-safe 'pinned' — never 'dev'. An INVALID selector is
        # rejected by run_action (never rewritten to dev).
        source = request.form.get("source", "dev")
        stop_owners = request.form.get("stop_owners") == "yes"
        cascade = request.form.get("cascade") == "yes"
        frm = request.form.get("from", "")     # origin page (e.g. "dash") for redirect
        save_mode = request.form.get("_save", "")           # "stack" | "daemon" | ""
        # Refuse to start an app that isn't installed or built yet — send the
        # operator to its page (which has the Install/Build buttons) with a warning
        # and the CLI command, rather than spawning a doomed start.
        if op == "start" and service.stack(target) is not None:
            if not service.is_installed(target):
                flash(f"'{target}' is not installed yet — install it on this page "
                      f"(or run: lhpc install {target}).", "warn")
                return redirect(url_for("stack_detail", stack_id=target))
            unbuilt = service.unbuilt_components(target)
            if unbuilt:
                flash(f"'{target}' needs building before it can run — its binary is "
                      f"missing ({', '.join(unbuilt)}). Build it on this page "
                      f"(or run: lhpc build {target}).", "warn")
                return redirect(url_for("stack_detail", stack_id=target))
        # Panel "Save" / "Save & start". FAIL CLOSED: for Save & start the requested start happens
        # ONLY after EVERY selected persistence succeeds. Any failure (parse, stack or per-band
        # daemon write) blocks the start and re-renders the confirm with the SUBMITTED values +
        # visible errors — nothing is started, reconfigured, generated or recorded.
        if op == "start" and save_mode in ("stack", "daemon", "all"):
            then_start = request.form.get("_save_then_start") == "1"
            # Validate the daemon form BEFORE the first write, so a malformed field fails closed
            # without persisting anything.
            if save_mode in ("daemon", "all"):
                _pb, _derr = _parse_start_daemon_overrides(request.form)
                if _derr:
                    flash(f"Cannot save daemon parameters: {_derr}", "warn")
                    return _render_confirm(op, target, band, params, file_over, source, frm)
            stack_ok = True
            stack_values = _stack_save_values(target, band, params, file_over)
            if save_mode in ("stack", "all") and stack_values:
                res = service.save_config_bundle(target, values=stack_values, band=band)
                flash(res.summary + (" " + "; ".join(res.details) if res.details else ""),
                      "ok" if res.ok else "warn")
                stack_ok = res.ok
            # Save & start SHORT-CIRCUIT: a failed stack save must NOT trigger the daemon save and
            # must NOT start — re-render immediately with the submitted values. (`_save_daemon_confirm`
            # / `save_daemon_params` are never reached.)
            if then_start and not stack_ok:
                flash("Not started — the stack configuration could not be saved.", "warn")
                return _render_confirm(op, target, band, params, file_over, source, frm)
            daemon_res = {"parse_error": None, "bands": {}, "ok": True}
            if save_mode in ("daemon", "all"):
                daemon_res = _save_daemon_confirm(target)
                if daemon_res["parse_error"]:
                    flash(f"Daemon parameters not saved: {daemon_res['parse_error']}", "warn")
                for _b, _bok in daemon_res["bands"].items():
                    flash(f"Daemon {_b} MHz: {'saved' if _bok else 'save FAILED'}",
                          "ok" if _bok else "warn")
            if not then_start:
                # Save-only: re-render with the (now saved) config — never starts.
                fresh = service.stack_config(target, band)
                return _render_confirm(op, target, band, dict(fresh), None, source, frm)
            if not (stack_ok and daemon_res["ok"]):
                # Save & start, but a later save failed (a per-band DAEMON save after a successful/
                # absent stack save) -> DO NOT start. Earlier successful saves are RETAINED. Report
                # PRECISELY what did and did not persist (never over-claim the stack save — during
                # `_save=daemon` no stack config is written), and re-render with the submitted values.
                saved, not_saved = [], []
                if save_mode in ("stack", "all") and stack_values:
                    (saved if stack_ok else not_saved).append("stack config")
                for _b, _bok in daemon_res["bands"].items():
                    (saved if _bok else not_saved).append(f"daemon {_b} MHz")
                parts = []
                if saved:
                    parts.append("saved: " + ", ".join(saved))
                if not_saved:
                    parts.append("NOT saved: " + ", ".join(not_saved))
                flash("Not started — a save failed" + (f" ({'; '.join(parts)})" if parts else "")
                      + ". Fix it and try again.", "warn")
                return _render_confirm(op, target, band, params, file_over, source, frm)
            # every selected save succeeded -> fall through to the apply below
        if request.form.get("confirmed") != "yes":
            # Stage 1: show the dry-run plan, options and a confirmation form.
            return _render_confirm(op, target, band, params, file_over, source, frm)
        # Stage 2: apply.
        # Optional-component start choices (KISS Serial, GPS relay, …): the checkbox IS
        # the durable auto-start config option — persist a CHANGED choice before the
        # start, so `_run_order` includes/excludes the component for this and every
        # later run. A failed save blocks the start (never a silently ignored choice).
        if op == "start" and service.stack(target) is not None:
            opts = service.optional_start_components(target)
            changed = {f"autostart_{o['id']}":
                       ("on" if request.form.get(f"opt_start_{o['id']}") == "on" else "")
                       for o in opts
                       if (request.form.get(f"opt_start_{o['id']}") == "on")
                       != o["autostart"]}
            if changed:
                saved = service.save_config_bundle(target, values=changed)
                if not saved.ok:
                    flash(f"Not started — the optional-component choice could not be "
                          f"saved: {saved.summary}", "warn")
                    return _render_confirm(op, target, band, params, file_over, source,
                                           frm)
        # DESTRUCTIVE clean: additionally requires the operator to TYPE the stack id —
        # a mismatch re-renders the confirm with ZERO mutation.
        purge = False
        if op == "clean":
            typed = (request.form.get("confirm_text") or "").strip()
            if typed != target:
                flash(f"Clean not applied: type the stack id '{target}' exactly to confirm "
                      "the destructive purge.", "warn")
                return _render_confirm(op, target, band, params, file_over, source, frm)
            purge = True
        # install/build/test run as detached jobs streaming to a log -> show it live.
        if op in ("install", "build", "test"):
            # Gate install/build on system dependencies: never proceed while a
            # required dev package / header / device node is missing.
            missing = service.missing_system_deps(target) if op in ("install", "build") else []
            if missing:
                flash("System dependencies missing — install them first: "
                      + "; ".join(d["install"] for d in missing if d["install"]), "warn")
                return _redirect_for(target)
            job, err = service.spawn_web_job(op, target, source=source)
            if err:
                flash(err, "warn")
                return _redirect_for(target)
            flash(f"{op} started — watch the live output below (it shows when it ends).", "ok")
            return redirect(url_for("logs_view", target=target, job=job))
        # Ephemeral PER-BAND daemon-param values from the confirm panel(s). STRICT parse of EVERY
        # dp_* field: a malformed/duplicated field shape is a visible start failure BEFORE any
        # launch; unknown band/param/value are validated by the service normalizer. Band-scoped
        # dp_<band>_<PARAM> keeps 433 and 868 separate; values are applied for THIS launch only.
        daemon_overrides = None
        if op == "start":
            daemon_overrides, dp_err = _parse_start_daemon_overrides(request.form)
            if dp_err:
                flash(f"Cannot start '{target}': {dp_err}", "warn")
                return _redirect_for(target)
            # CALL/node enforcement (UX): block the start and RE-RENDER the confirm with the
            # 'Stack parameters' panel expanded and the offending field highlighted, so the operator
            # can supply the call/node in place. The service layer enforces this authoritatively too.
            id_ok, id_field, id_msg = service.enforce_identity(target, band, params, file_over)
            if not id_ok:
                flash(id_msg, "warn")
                return _render_confirm(op, target, band, params, file_over, source, frm,
                                       enforce_field=id_field)
        result = service.run_action(op, target, apply=True, params=params, source=source,
                                    stop_owners=stop_owners, cascade=cascade, band=band,
                                    daemon_overrides=daemon_overrides, file_overrides=file_over,
                                    purge=purge)
        # An interactive/systemd start whose ONLY non-success is the expected MANUAL_REQUIRED (e.g.
        # chat: daemon up + readied, operator runs the TUI) is a success, not a warning.
        ok_flash = result.ok or manual_required_only(result.results)
        flash(f"{result.summary} {' '.join(result.details[:6])}",
              "ok" if ok_flash else "warn")
        # Start note(s) for a just-started component (boot expectations, connect hints):
        # YELLOW and LONG-LIVED (30 s) — a 1–2 min boot warning must outlast the quick
        # green flashes.
        if op == "start":
            for note in service.start_notes(result):
                flash(note, "warn transient-long")
        return _redirect_for(target)

    def _safe_job(value):
        # only a plain logs/<name>.log filename, never a path
        return value if value and "/" not in value and ".." not in value else None

    @app.get("/logs/<target>")
    def logs_view(target: str):  # noqa: ANN202
        if service.stack_of(target) is None:
            abort(404)
        job = _safe_job(request.args.get("job"))
        path, lines = service.log_tail(target, 300, job=job)
        return render_template("logs.html", version=__version__,
                               runtime_root=_runtime_root(), target=target, job=job,
                               stack_id=service.stack_of(target), path=path, lines=lines,
                               running=service.log_running(target, job))

    @app.get("/api/logs/<target>")
    def logs_api(target: str):  # noqa: ANN202
        if service.stack_of(target) is None:
            abort(404)
        job = _safe_job(request.args.get("job"))
        path, lines = service.log_tail(target, 300, job=job)
        return jsonify(target=target, path=path, lines=lines,
                       running=service.log_running(target, job))

    @app.post("/stacks/<stack_id>/config")
    def stack_config_save(stack_id: str):  # noqa: ANN202
        if service.stack(stack_id) is None:
            abort(404)
        if not _csrf_ok():
            abort(400)
        band = request.form.get("band", "")
        view = service.config_view(stack_id, band)
        # Fold each submitted Config-page field (`c_`/`f_`, component-qualified when the name
        # collides) into the canonical API key (`name` or `component.name`; file keys carry the
        # `file_` prefix) BEFORE save — so duplicate names never flatten and unqualified duplicates
        # are rejected by the canonical bundle path.
        values = {}
        for f in service.config_param_fields(stack_id, band):
            if f["flag"]:
                v = "1" if request.form.get(f["field"]) else ""
            else:
                v = request.form.get(f["field"], f["default"])
            values[f"file_{f['key']}" if f["kind"] == "file" else f["key"]] = v
        # Auto-start toggles for the stack's optional components.
        for opt in view["optional"]:
            values[f"autostart_{opt['id']}"] = (
                "on" if request.form.get("c_autostart_" + opt["id"]) else "")
        # Per-component GitHub remote overrides as a COMPLETE map (blank reverts to
        # the default). The whole submission is validated and persisted as ONE
        # all-or-recoverable transaction — no per-remote sequential writes.
        remotes = None
        if view["sources"]:
            remotes = {}
            for src in view["sources"]:
                field = request.form.get("remote_" + src["id"], src["remote"])
                remotes[src["id"]] = "" if field.strip() == src["default"] else field.strip()
        result = service.save_config_bundle(
            stack_id, values=values,
            callsign=request.form.get("op_callsign"),
            locator=request.form.get("op_locator"), band=band, remotes=remotes)
        flash(result.summary + (" " + "; ".join(result.details) if result.details else ""),
              "ok" if result.ok else "warn")
        return redirect(url_for("stack_detail", stack_id=stack_id, band=band or None, cfg=1) + "#stack-settings")

    def _daemon_param_form():  # (band, {PARAM: value}) from the submitted panel
        from lhpc.core import daemon_params as _dp
        band = request.form.get("band", "")
        return band, {name: request.form.get("dp_" + name, "") for name in _dp.ALL_PARAMS}

    def _dp_back(stack_id: str, band: str):  # redirect to the submitting page (local-only)
        nxt = request.form.get("next", "")
        if nxt.startswith("/") and not nxt.startswith("//") and "\\" not in nxt:
            sep = "&" if "?" in nxt else "?"                  # keep the panel expanded (dp=1)
            return redirect(nxt + sep + "dp=1")               # open-redirect-safe local path
        return redirect(url_for("stack_detail", stack_id=stack_id, band=band or None, cfg=1, dp=1) + "#stack-settings")

    @app.post("/stacks/<stack_id>/daemon-params")
    def daemon_params_save(stack_id: str):  # noqa: ANN202
        if service.stack(stack_id) is None:
            abort(404)
        if not _csrf_ok():
            abort(400)
        band, values = _daemon_param_form()
        result = service.save_daemon_params(stack_id, band, values)
        flash(result.summary, "ok" if result.ok else "warn")
        return _dp_back(stack_id, band)

    @app.post("/stacks/<stack_id>/daemon-params/apply")
    def daemon_params_apply(stack_id: str):  # noqa: ANN202
        if service.stack(stack_id) is None:
            abort(404)
        if not _csrf_ok():
            abort(400)
        band, values = _daemon_param_form()
        save = service.save_daemon_params(stack_id, band, values)   # persist first
        if not save.ok:
            flash(save.summary, "warn")
            return _dp_back(stack_id, band)
        result = service.apply_daemon_params(stack_id, band)         # then push live
        # Truthful: partial/total apply failure flashes as a warning (never green), and the
        # timing override stays saved regardless.
        flash("Saved. Apply live: " + result.summary
              + (" — " + "; ".join(result.details) if result.details else ""),
              "ok" if result.ok else "warn")
        return _dp_back(stack_id, band)

    @app.post("/stacks/<stack_id>/daemon-params/reset")
    def daemon_params_reset(stack_id: str):  # noqa: ANN202
        if service.stack(stack_id) is None:
            abort(404)
        if not _csrf_ok():
            abort(400)
        band = request.form.get("band", "")
        result = service.reset_daemon_params(stack_id, band)
        flash(result.summary, "ok" if result.ok else "warn")
        return _dp_back(stack_id, band)

    @app.post("/stacks/<stack_id>/config/reset")
    def stack_config_reset(stack_id: str):  # noqa: ANN202
        if service.stack(stack_id) is None:
            abort(404)
        if not _csrf_ok():
            abort(400)
        band = request.form.get("band", "")
        result = service.reset_config(stack_id, band)
        flash(result.summary, "ok" if result.ok else "warn")
        return redirect(url_for("stack_detail", stack_id=stack_id, band=band or None, cfg=1) + "#stack-settings")

    @app.get("/healthz")
    def healthz():  # noqa: ANN202
        # Cheap liveness: manifest parses; does NOT run the full probe sweep.
        stacks = service.stacks()
        return jsonify(status="ok", stacks=len(stacks), version=__version__)

    @app.errorhandler(404)
    def _not_found(_err):  # noqa: ANN001, ANN202
        return render_template("error.html", code=404, message="Not found"), 404

    @app.errorhandler(405)
    def _method_not_allowed(_err):  # noqa: ANN001, ANN202
        return render_template("error.html", code=405, message="Method not allowed"), 405

    @app.errorhandler(Exception)
    def _unexpected(err):  # noqa: ANN001, ANN202
        # HTTP errors (404/405/400 …) keep their own status/handling.
        from werkzeug.exceptions import HTTPException
        if isinstance(err, HTTPException):
            return err
        # Last-resort boundary: an UNEXPECTED escape (e.g. a runtime-root FS/containment
        # error not already typed at the service layer) renders a clean, typed message —
        # never a traceback (debug/reloader are off). Expected failures are still typed
        # ActionResults upstream; this only stops a stray exception leaking a stack trace.
        app.logger.exception("unexpected error handling %s", request.path)
        return render_template("error.html", code=500,
                               message="Internal error — see the server log."), 500

    return app


def run_server(host: str = "127.0.0.1", port: int = 8770) -> int:
    """Run the console, refusing any non-loopback bind. No debug, no reloader."""
    if host not in _LOOPBACK_HOSTS:
        print(
            f"ERR  refusing to bind '{host}': the operator console is loopback-only "
            f"(allowed: {', '.join(sorted(_LOOPBACK_HOSTS))}).\n"
            "     Non-loopback access would require explicit opt-in, auth and HTTPS\n"
            "     (or a documented trusted reverse proxy)."
        )
        return 1
    # Controller-runtime lock (B6): acquire it SHARED, NON-BLOCKING, BEFORE the startup
    # self-check or binding the socket, and hold it for the ENTIRE serving lifetime. If a
    # self-update holds it EXCLUSIVE, fail CLOSED here — never serve with unlocked source
    # that an apply could `reset --hard`/`clean` underneath the running process. The lock is
    # released in the contextmanager's finally on ALL exit paths, incl. startup failure.
    from lhpc.core import selfupdate as _su
    from lhpc.core.paths import resolve_paths as _resolve_paths
    try:
        _lock_cm = _su.controller_runtime_lock(_resolve_paths(), exclusive=False)
        _lock_cm.__enter__()
    except _su.ControllerRuntimeBusy:
        print("ERR  a self-update is in progress (controller-runtime lock held) — "
              "not starting the web server. Retry once self-update finishes.")
        return 1
    except _su.ControllerRuntimeLockError as exc:
        print(f"ERR  could not acquire the controller-runtime lock ({exc}) — not starting.")
        return 1
    try:
        app = create_app()
        # Best-effort, NON-BLOCKING upstream freshness check at startup (this is process startup, NOT a
        # GET route) so the footer's version/head indicator becomes meaningful shortly after boot. Any
        # failure is swallowed — the footer simply stays grey/"unknown".
        import threading

        def _startup_selfcheck():
            try:
                ControllerService().self_update_check()
            except Exception:
                pass

        threading.Thread(target=_startup_selfcheck, name="lhpc-selfcheck", daemon=True).start()
        print(f"OK   LoRaHAM Pi Control console at http://{host}:{port}/")
        print("     Loopback-only. Press Ctrl-C to stop.")
        # Supported deployment uses a production-capable WSGI server (waitress): one process,
        # multi-threaded, no debug, no reloader. The Flask dev server is a fallback for bare
        # interactive use only (still loopback-only, no debug, no reloader). waitress is NOT a
        # hard dependency — if it is absent we fall back and say so.
        try:
            from waitress import serve as _waitress_serve
        except ImportError:
            _waitress_serve = None
        if _waitress_serve is not None:
            # Single listening process; threads let live monitor polling + actions overlap.
            _waitress_serve(app, host=host, port=port, threads=8, ident="lhpc")
        else:
            print("WARN waitress not installed — using the Flask dev server (OK for local "
                  "interactive use only).\n"
                  "     For the supported systemd deployment, install 'waitress' in the venv "
                  "(see docs/deployment.md).")
            app.run(host=host, port=port, debug=False, use_reloader=False, threaded=True)
        return 0
    finally:
        _lock_cm.__exit__(None, None, None)
