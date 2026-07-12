"""ControllerService mixin: MeshCom HMAC-password STATE (enable / disable / renew).

Adapters import lhpc.core.services only. The secret is the first line of `<runtime>/config/secrets/xr_pw`;
the meshcom bridge reads it via the `password_file` run-param (`--password-file`, blank = open auth) and the
firmware bakes it at build. This mixin manages STATE ONLY (the secret file + the param override), atomically;
APPLYING it (firmware rebuild + restarts) is the apply driver. The secret value is NEVER returned or logged."""
from __future__ import annotations

import json
import re
import secrets as _secrets
import sys
import uuid

from .paths import PathContainmentError
from .service_base import ActionResult

# Secret file (first line = the password) and the bridge run-param that points at it. `{runtime}` in the
# param value is resolved by commands.expand_argv at start; an empty value emits no arg -> open auth.
_XR_PW = ("config", "secrets", "xr_pw")
_XR_PW_VALUE = "{runtime}/config/secrets/xr_pw"
_HMAC_PARAM = "password_file"

# --- apply-run infrastructure (modeled on the install-all bulk driver, scoped to one stack) ---------
_MARKER = ("state", "hmac_apply.json")
_RUN_ID_RE = re.compile(r"^[0-9a-f]{32}$")
_STEP_ORDER = ("secret", "firmware", "bridge", "node")
_STEP_LABELS = {
    "secret": "Update the password secret",
    "firmware": "Rebuild the MeshCom firmware (the long step)",
    "bridge": "Restart the bridge",
    "node": "Restart the node (load the new firmware)",
}


def _hmac_log_base(run_id: str) -> str:
    """The apply run's log/job base name — derived EXCLUSIVELY from a validated run id."""
    if not _RUN_ID_RE.match(run_id or ""):
        raise ValueError("invalid HMAC apply run id")
    return f"hmac-apply-{run_id}"


class HmacOpsMixin:
    """MeshCom HMAC-password state: applies?/status + an atomic, rollback-safe enable/disable/renew."""

    def _hmac_component(self, stack_id: str):
        """The component in `stack_id` carrying the HMAC `password_file` run-param, or None."""
        s = self.stack(stack_id)
        if s is None:
            return None
        return next((c for c in s.components
                     if any(p.name == _HMAC_PARAM for p in c.run_params)), None)

    def hmac_applies(self, stack_id: str) -> bool:
        return self._hmac_component(stack_id) is not None

    def hmac_status(self, stack_id: str):
        """None when HMAC does not apply here; else whether password-auth is ENABLED (the resolved
        `password_file` param is non-blank)."""
        c = self._hmac_component(stack_id)
        if c is None:
            return None
        return bool((self._resolved_param_value(stack_id, "run", c.id, _HMAC_PARAM) or "").strip())

    def hmac_default_stack(self):
        """The single stack HMAC applies to (meshcom), or None — the CLI's default target."""
        return next((s.id for s in self.stacks() if self.hmac_applies(s.id)), None)

    def _xr_pw_path(self):
        return self._paths.runtime_root.joinpath(*_XR_PW)

    def hmac_set_secret(self, stack_id: str, action: str) -> ActionResult:
        """ONE atomic/rollback-safe state change (config override + secret file). On ANY failure the visible
        HMAC state is EXACTLY as before. Does NOT apply (rebuild/restart) — that is the driver's job. Never
        returns the secret value."""
        from . import runtime_fs
        c = self._hmac_component(stack_id)
        if c is None:
            return ActionResult(False, f"HMAC password does not apply to '{stack_id}'")
        if action not in ("enable", "disable", "renew"):
            return ActionResult(False, f"unknown HMAC action: {action!r}")
        path = self._xr_pw_path()
        # --- snapshot for rollback ---
        try:
            old_secret = runtime_fs.read_bytes(self._paths, path)
        except (FileNotFoundError, OSError):
            old_secret = None
        old_resolved = self._resolved_param_value(stack_id, "run", c.id, _HMAC_PARAM)
        # --- 1. config change FIRST (validates + recoverable; writes nothing on failure) ---
        want = "" if action == "disable" else _XR_PW_VALUE
        r = self.save_config_bundle(stack_id, values={_HMAC_PARAM: want})
        if not r.ok:
            return ActionResult(False, f"could not {action} HMAC password: {r.summary}", details=r.details)
        # --- 2. secret-file change (atomic writes: a failed write leaves the OLD file intact) ---
        try:
            if action == "disable":
                if path.exists():
                    runtime_fs.unlink(self._paths, path)
            elif action == "renew":
                runtime_fs.atomic_write(self._paths, path, _secrets.token_hex(16) + "\n", 0o600)
            else:                                            # enable: idempotent — keep an existing secret
                if not path.exists():
                    runtime_fs.atomic_write(self._paths, path, _secrets.token_hex(16) + "\n", 0o600)
        except Exception as exc:                             # noqa: BLE001 — roll BOTH back
            self.save_config_bundle(stack_id, values={_HMAC_PARAM: old_resolved})
            try:
                if old_secret is None:
                    if path.exists():
                        runtime_fs.unlink(self._paths, path)
                else:
                    runtime_fs.atomic_write_bytes(self._paths, path, old_secret, 0o600)
            except Exception:                                # noqa: BLE001
                pass
            return ActionResult(False, f"could not {action} HMAC password (secret file): {exc} — rolled back")
        verb = {"enable": "enabled", "disable": "disabled", "renew": "renewed"}[action]
        return ActionResult(True, f"HMAC password {verb}")

    # ---- auto-apply flow: rebuild firmware + restart the link, with a live progress run ----------
    #
    # A change is SLOW (the firmware bakes the secret at build), so applying it runs as a detached
    # driver (`lhpc _hmac-apply <sid> <action> <run_id>`) writing a marker `state/hmac_apply.json` +
    # a run log `logs/hmac-apply-<run_id>.log`, exactly like install-all. The SAME step runner backs
    # the CLI foreground run. The secret value NEVER reaches the marker, the log (redacted at read),
    # or any ActionResult.

    def _hmac_marker_path(self):
        return self._paths.under(*_MARKER)

    def _hmac_write_marker(self, d: dict) -> bool:
        """Durable transition evidence. False on failure — the driver aborts rather than run
        untracked."""
        from . import runtime_fs
        try:
            runtime_fs.write_marker(self._paths, self._hmac_marker_path(), json.dumps(d, indent=1))
            return True
        except (OSError, PathContainmentError):
            return False

    def hmac_apply_status(self) -> dict | None:
        """Tri-state run state for GETs (file + /proc only, never mutates): None (absent),
        {"unsafe": True, reason}, or the marker dict — a `running` marker whose driver job is
        provably GONE is surfaced as `interrupted` (derived, the file is not rewritten)."""
        from . import runtime_fs
        try:
            raw = runtime_fs.read_text_regular(self._paths, self._hmac_marker_path())
        except FileNotFoundError:
            return None
        except (OSError, PathContainmentError, ValueError) as exc:
            return {"unsafe": True, "reason": f"HMAC apply marker unreadable ({exc})"}
        try:
            d = json.loads(raw)
        except json.JSONDecodeError:
            return {"unsafe": True, "reason": "HMAC apply marker malformed (not JSON)"}
        if not (isinstance(d, dict) and _RUN_ID_RE.match(d.get("run_id", ""))
                and isinstance(d.get("steps"), list) and d.get("phase") in
                ("running", "done", "failed", "interrupted")):
            return {"unsafe": True, "reason": "HMAC apply marker malformed (schema)"}
        if d["phase"] == "running" and not d.get("finished"):
            try:
                job = _hmac_log_base(d["run_id"]) + ".log"
            except ValueError:
                return {"unsafe": True, "reason": "HMAC apply marker malformed (run id)"}
            if not self.log_running(d.get("sid", ""), job=job):
                d = dict(d, phase="interrupted", derived_interrupted=True)
        return d

    def hmac_apply_running(self) -> bool:
        st = self.hmac_apply_status()
        return bool(st and not st.get("unsafe") and st.get("phase") == "running")

    def _hmac_redact(self, text: str) -> str:
        """Replace the CURRENT secret with `****` before any bytes leave the process — the
        firmware build's streamed output could otherwise surface a baked/echoed token."""
        from . import runtime_fs
        try:
            secret = runtime_fs.read_bytes(self._paths, self._xr_pw_path()).decode(
                "utf-8", "replace").strip()
        except (FileNotFoundError, OSError):
            secret = ""
        if secret and secret in text:
            text = text.replace(secret, "****")
        return text

    def hmac_apply_log_chunk(self, run_id: str, offset: int) -> dict:
        """Byte-cursored read of the apply run log, with the secret REDACTED. Fully fail-closed
        (never raises through a GET): a bad id/offset or an unreadable/symlinked log yields a
        bounded safe result. The offset advances by RAW bytes consumed (redaction changes the
        rendered length, never the file cursor)."""
        try:
            if not _RUN_ID_RE.match(run_id or ""):
                return {"error": "invalid run id", "offset": 0, "data": ""}
            if not isinstance(offset, int) or offset < 0 or offset > (1 << 40):
                return {"error": "invalid offset", "offset": 0, "data": ""}
            raw, text, size = self._read_named_log_chunk(
                f"hmac-apply-{run_id}.log", offset, 64 * 1024)
            if raw <= 0:                         # absent (future)/unreadable/at-EOF -> no new bytes
                return {"offset": offset, "data": "", "size": size if size > 0 else 0}
            return {"offset": offset + raw, "data": self._hmac_redact(text), "size": size}
        except Exception:                        # noqa: BLE001 — a GET must never 500
            return {"error": "run log temporarily unavailable", "offset": 0, "data": ""}

    def _hmac_initial_steps(self) -> list:
        return [{"key": k, "label": _STEP_LABELS[k], "state": "pending"} for k in _STEP_ORDER]

    def hmac_apply_start(self, stack_id: str, action: str) -> ActionResult:
        """Reserve + spawn the detached apply driver. Single-flight: refused while a run is live.
        Returns the run id in data on success."""
        from . import reslock
        if not self.hmac_applies(stack_id):
            return ActionResult(False, f"HMAC password does not apply to '{stack_id}'")
        if action not in ("enable", "disable", "renew"):
            return ActionResult(False, f"unknown HMAC action: {action!r}")
        if not self._paths.runtime_root_exists:
            return ActionResult(False, "Runtime root is not bootstrapped yet.",
                                next_commands=["lhpc bootstrap"])
        try:
            with reslock.operation_lock(self._paths, "hmac-apply", stack_id, ""):
                if self.hmac_apply_running():
                    return ActionResult(False, "An HMAC apply is already running — wait for it "
                                        "to finish.")
                run_id = uuid.uuid4().hex
                marker = {"run_id": run_id, "sid": stack_id, "action": action,
                          "phase": "running", "finished": False,
                          "steps": self._hmac_initial_steps()}
                if not self._hmac_write_marker(marker):
                    return ActionResult(False, "Could not record the HMAC apply run — aborted "
                                        "(no state written).")
                argv = [sys.executable, "-u", "-m", "lhpc", "_hmac-apply",
                        stack_id, action, run_id]
                life = self._lifecycle()
                ln, pid = life.spawn_job(_hmac_log_base(run_id), argv,
                                         str(self._paths.runtime_root))
                if ln is None:
                    self._hmac_mark_failed(marker, None, "could not spawn the apply driver")
                    return ActionResult(False, "Could not spawn the HMAC apply driver (see logs).")
                err = self._track_or_terminate(life, ln, pid, stack_id, "hmac-apply")
                if err:
                    self._hmac_mark_failed(marker, None, err)
                    return ActionResult(False, err)
                return ActionResult(True, f"HMAC {action} started.", data={"run_id": run_id})
        except reslock.ResourceBusy:
            return ActionResult(False, "An HMAC apply is already starting (start lock contended).")

    def hmac_apply_cli(self, stack_id: str, action: str, emit) -> int:
        """FOREGROUND apply for the CLI: same step runner as the detached driver, streaming to
        stdout. Single-flight against a live web/CLI run. Returns a process exit code."""
        if not self.hmac_applies(stack_id):
            emit(f"HMAC password does not apply to '{stack_id}'.")
            return 1
        if action not in ("enable", "disable", "renew"):
            emit(f"unknown HMAC action: {action!r}")
            return 1
        if not self._paths.runtime_root_exists:
            emit("Runtime root is not bootstrapped yet. Run 'lhpc bootstrap' first.")
            return 1
        if self.hmac_apply_running():
            emit("An HMAC apply is already running — wait for it to finish.")
            return 1
        return self._hmac_run_steps(stack_id, action, uuid.uuid4().hex, emit)

    def _hmac_mark_failed(self, marker: dict, step_key, detail: str) -> None:
        steps = []
        for s in marker.get("steps", []):
            s = dict(s)
            if step_key is not None and s["key"] == step_key:
                s["state"] = "failed"
            elif s.get("state") == "running":
                s["state"] = "failed"
            steps.append(s)
        self._hmac_write_marker(dict(marker, steps=steps, phase="failed",
                                     finished=True, detail=detail[:400]))

    def _hmac_run_steps(self, stack_id: str, action: str, run_id: str, emit) -> int:
        """The SHARED step runner (detached web driver AND CLI foreground). Sequential, fail-fast;
        writes the marker at every transition and streams human output through `emit`. The secret is
        never emitted. Returns a process exit code (0 ok, 1 on any failed step)."""
        from . import runtime_fs
        c = self._hmac_component(stack_id)
        s = self.stack(stack_id)
        node = s.main_component if s else None
        if c is None or node is None:
            emit(f"HMAC apply refused: it does not apply to '{stack_id}'.")
            return 1

        # RUN-SCOPED redaction: track EVERY secret sensitive during this run, not just the current file.
        # The OLD secret matters even after `disable` deletes xr_pw (a build/restart could echo it), so we
        # snapshot it up front and KEEP it in the set; the new secret is added after step 1 (enable/renew).
        secrets_seen = set()

        def _note_secret():
            try:
                sec = runtime_fs.read_bytes(self._paths, self._xr_pw_path()).decode(
                    "utf-8", "replace").strip()
                if sec:
                    secrets_seen.add(sec)
            except (FileNotFoundError, OSError):
                pass

        def _scrub(text):
            text = str(text)
            for sec in secrets_seen:
                text = text.replace(sec, "****")
            return text

        _note_secret()                       # PRE-RUN old secret (kept even if disable removes the file)
        _raw_emit = emit
        def emit(line):                       # scrub ALL emitted lines (log + CLI stdout)  # noqa: E306
            _raw_emit(_scrub(line))

        marker = {"run_id": run_id, "sid": stack_id, "action": action,
                  "phase": "running", "finished": False, "steps": self._hmac_initial_steps()}

        def _set(step_key, state):
            for st in marker["steps"]:
                if st["key"] == step_key:
                    st["state"] = state
            self._hmac_write_marker(marker)

        def _fail(step_key, detail):
            for st in marker["steps"]:
                if st["key"] == step_key:
                    st["state"] = "failed"
            marker["phase"], marker["finished"] = "failed", True
            marker["detail"] = _scrub(detail)[:400]
            self._hmac_write_marker(marker)
            emit(f"==== FAILED: {detail} ====")

        self._hmac_write_marker(marker)
        emit(f"==== HMAC {action} on {stack_id} ====")

        # 1. secret -------------------------------------------------------------------------------
        _set("secret", "running")
        emit("Updating the password secret…")
        r = self.hmac_set_secret(stack_id, action)          # never returns the secret value
        if not r.ok:
            _fail("secret", r.summary)
            return 1
        _note_secret()                       # add the NEW secret (enable/renew); disable left nothing new
        _set("secret", "done")
        emit(f"  {r.summary}")

        # 2. firmware rebuild (bakes the secret) --------------------------------------------------
        _set("firmware", "running")
        emit(f"Rebuilding the firmware ({node.id}) — this takes several minutes…")
        b = self.build(node.id, apply=True)
        for line in b.details:
            emit(line)
        if not b.ok:
            _fail("firmware", b.summary)
            return 1
        _set("firmware", "done")
        emit(f"  {b.summary}")

        # 3 + 4. restart the live link (only if it is actually up — else the rebuilt firmware +
        # secret take effect on the next start). Captured ONCE before touching either process.
        was_running = self.stack_running(stack_id)
        for key, target, what in (("bridge", c.id, "bridge"), ("node", node.id, "node")):
            if not was_running:
                _set(key, "skipped")
                emit(f"  {what} not running — the new firmware/secret applies on next start.")
                continue
            _set(key, "running")
            emit(f"Restarting the {what} ({target})…")
            rr = self.restart(target, apply=True)
            for line in rr.details:
                emit(line)
            if not rr.ok:
                _fail(key, rr.summary)
                return 1
            _set(key, "done")

        marker["phase"], marker["finished"] = "done", True
        self._hmac_write_marker(marker)
        verb = {"enable": "enabled", "disable": "disabled", "renew": "renewed"}[action]
        emit(f"==== DONE: HMAC password {verb} ====")
        return 0
