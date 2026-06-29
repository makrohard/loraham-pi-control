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

import re
import secrets as _secrets
from typing import Callable

from flask import (
    Flask, abort, flash, jsonify, redirect, render_template, request, session, url_for,
)

from lhpc.core import daemon_control
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

    def _param_layout(run_params):
        """Group params: globals one-per-row; *_433/*_868 ones into band columns."""
        bands, groups, globals_ = [], {}, []
        for p in run_params:
            for b in ("433", "868"):
                if p.name.endswith("_" + b):
                    base = p.name[: -len(b) - 1]
                    label = re.sub(r"\s*(433|868)\s*", " ", p.label or base).strip()
                    groups.setdefault(base, {"label": label, "byband": {}})
                    groups[base]["byband"][b] = p
                    if b not in bands:
                        bands.append(b)
                    break
            else:
                globals_.append(p)
        return {"globals": globals_, "bands": sorted(bands), "groups": list(groups.values())}

    app.jinja_env.globals["param_layout"] = _param_layout

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
            dash_sig=service.dash_signature())

    @app.get("/api/dash-signature")
    def dash_signature_api():  # noqa: ANN202
        # Cheap structural-state signature; the dashboard reloads only when it changes.
        return jsonify(sig=service.dash_signature())

    def _stack_groups():
        """Per-stack overview rows shared by the Stacks page and the Config page."""
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
            observed_conflicts=[c for c in snapshot.conflicts if c.observed],
        )

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
            update_status=(service.update_status(main) if installed else "unknown"),
            conflicts=[c for c in snapshot.conflicts
                       if any(h in member_ids for h in c.holders)],
            system_deps=service.system_deps(stack_id),
            # Components installed but whose compiled binary is missing (e.g. dropped
            # by a fresh clone) -> they need a Build before they can run.
            needs_build=[c.id for c in stack_status.stack.components
                         if c.source and service._lifecycle().source_dir(c).exists()
                         and not service.is_built(c)],
        )

    @app.post("/interactive/<stack_id>/dismiss")
    def interactive_dismiss(stack_id: str):  # noqa: ANN202
        if service.stack(stack_id) is None:
            abort(404)
        if not _csrf_ok():
            abort(400)
        service.dismiss_interactive(stack_id)
        return redirect(url_for("dashboard"))

    @app.get("/config")
    def config_index():  # noqa: ANN202
        # Config hub: the same per-stack list as the Stacks page, linking to config.
        groups, _ = _stack_groups()
        return render_template("config_index.html", version=__version__,
                               runtime_root=_runtime_root(), groups=groups)

    @app.get("/api/daemon/<band>")
    def daemon_api(band: str):  # noqa: ANN202
        if band not in ("433", "868"):
            abort(404)
        view = service.daemon_view(band)
        return jsonify(band=band, reachable=view.reachable, status=view.status,
                       stats=view.stats, channel=view.channel,
                       feed=service.daemon_feed(band, 40))

    @app.post("/radio/<band>/set")
    def radio_set(band: str):  # noqa: ANN202
        # Apply a LIVE daemon setting (runtime) from the daemon config page.
        if band not in ("433", "868"):
            abort(404)
        if not _csrf_ok():
            abort(400)
        key = request.form.get("key", "")
        value = request.form.get("value", "")
        result = service.daemon_set(band, key, value, apply=True)
        if not result.ok:
            flash(result.summary, "warn")
        else:
            flash(f"Applied {key}={value} on {band} MHz (live).", "ok")
        return redirect(url_for("stack_config_view", stack_id="daemon"))

    def _redirect_for(target: str):
        # Actions launched from the dashboard return to the dashboard; otherwise
        # land on the target's stack detail page.
        if request.form.get("from") == "dash":
            return redirect(url_for("dashboard"))
        sid = service.stack_of(target)
        return redirect(url_for("stack_detail", stack_id=sid) if sid else url_for("dashboard"))

    @app.post("/action")
    def action():  # noqa: ANN202
        if not _csrf_ok():
            abort(400)
        op = request.form.get("op", "")
        target = request.form.get("target", "")
        if op not in service.WEB_ACTIONS:
            abort(400)
        band = request.form.get("band", "")    # chosen band for a band-switchable stack
        # Collect run parameters (only meaningful for start); defaults come from
        # the stack's saved config for the chosen band, overridable on the confirm.
        run_params = service.run_params_for(target) if op == "start" else []
        stored = service.stack_config(target, band) if run_params else {}
        params = {p.name: request.form.get("p_" + p.name, stored.get(p.name, p.default))
                  for p in run_params}
        # Source version selector (only meaningful for install/update).
        source = request.form.get("source", "dev")
        stop_owners = request.form.get("stop_owners") == "yes"
        cascade = request.form.get("cascade") == "yes"
        frm = request.form.get("from", "")     # origin page (e.g. "dash") for redirect
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
        if request.form.get("confirmed") != "yes":
            # Stage 1: show the dry-run plan, options and a confirmation form.
            plan = service.run_action(op, target, apply=False, params=params, source=source,
                                      band=band)
            return render_template("confirm.html", version=__version__,
                                   runtime_root=_runtime_root(), op=op, target=target,
                                   plan=plan, tx=("tx" in op), run_params=run_params,
                                   params=params, source=source, band=band,
                                   blockers=(plan.data.get("blockers") if op == "start" else None),
                                   stop_deps=(plan.data.get("dependents") if op == "stop" else None),
                                   other_bands=(plan.data.get("other_bands") if op == "stop" else None),
                                   commands=(plan.data.get("commands") if op in ("start", "stop") else None),
                                   missing_deps=(service.missing_system_deps(target)
                                                 if op in ("install", "build") else None),
                                   frm=frm,
                                   source_choices=service.SOURCE_CHOICES if op in ("install", "update") else None)
        # Stage 2: apply.
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
        result = service.run_action(op, target, apply=True, params=params, source=source,
                                    stop_owners=stop_owners, cascade=cascade, band=band)
        flash(f"{result.summary} {' '.join(result.details[:6])}",
              "ok" if result.ok else "warn")
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

    @app.get("/stacks/<stack_id>/config")
    def stack_config_view(stack_id: str):  # noqa: ANN202
        if service.stack(stack_id) is None:
            abort(404)
        return render_template("config.html", version=__version__,
                               runtime_root=_runtime_root(), stack_id=stack_id,
                               view=service.config_view(stack_id, request.args.get("band", "")))

    @app.post("/stacks/<stack_id>/config")
    def stack_config_save(stack_id: str):  # noqa: ANN202
        if service.stack(stack_id) is None:
            abort(404)
        if not _csrf_ok():
            abort(400)
        band = request.form.get("band", "")
        run_params = service.run_params_for(stack_id)
        values = {}
        for p in run_params:
            if p.kind == "flag":
                values[p.name] = "1" if request.form.get("c_" + p.name) else ""
            else:
                values[p.name] = request.form.get("c_" + p.name, p.default)
        view = service.config_view(stack_id, band)
        # File-config values (written to the app's own config file on save).
        for fp in view["file_params"]:
            if fp.kind == "flag":
                values[f"file_{fp.name}"] = "1" if request.form.get("f_" + fp.name) else ""
            else:
                values[f"file_{fp.name}"] = request.form.get("f_" + fp.name, fp.default)
        # Auto-start toggles for the stack's optional components.
        for opt in view["optional"]:
            values[f"autostart_{opt['id']}"] = (
                "on" if request.form.get("c_autostart_" + opt["id"]) else "")
        # Per-component GitHub remote overrides (blank reverts to the default).
        for src in view["sources"]:
            field = request.form.get("remote_" + src["id"])
            if field is not None and field.strip() != src["remote"]:
                service.save_component_remote(src["id"],
                                              "" if field.strip() == src["default"] else field)
        # Pass operator only when the form actually carried it (None = leave as-is),
        # so saving a stack that doesn't use a callsign never clobbers it.
        result = service.save_config(
            stack_id, values,
            callsign=request.form.get("op_callsign"),
            locator=request.form.get("op_locator"), band=band)
        flash(result.summary + (" " + "; ".join(result.details) if result.details else ""),
              "ok" if result.ok else "warn")
        return redirect(url_for("stack_config_view", stack_id=stack_id, band=band or None))

    @app.post("/stacks/<stack_id>/config/reset")
    def stack_config_reset(stack_id: str):  # noqa: ANN202
        if service.stack(stack_id) is None:
            abort(404)
        if not _csrf_ok():
            abort(400)
        band = request.form.get("band", "")
        result = service.reset_config(stack_id, band)
        flash(result.summary, "ok" if result.ok else "warn")
        return redirect(url_for("stack_config_view", stack_id=stack_id, band=band or None))

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
    app = create_app()
    print(f"OK   LoRaHAM Pi Control console at http://{host}:{port}/")
    print("     Loopback-only. Press Ctrl-C to stop.")
    # threaded=True so live monitor polling and actions don't block each other.
    app.run(host=host, port=port, debug=False, use_reloader=False, threaded=True)
    return 0
