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
from .service_base import ActionResult, SourceTxnBlocked as _SourceTxnBlocked

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


# How long the detached driver waits for the parent's job marker to appear before refusing (the child can
# start just before the parent persists it). Kept small; tests monkeypatch it shorter.
_HMAC_DRIVER_TRACK_TIMEOUT_S = 5.0


def _hmac_log_base(run_id: str) -> str:
    """The apply run's log/job base name — derived EXCLUSIVELY from a validated run id."""
    if not _RUN_ID_RE.match(run_id or ""):
        raise ValueError("invalid HMAC apply run id")
    return f"hmac-apply-{run_id}"


# A HMAC-apply per-step log leaf (window 2) — bound to the FULL 32-hex run id, controller-generated
# charset. One trailing segment covers every step: `build-<comp>-<i>`, `secret`, `bridge`, `node`. It never
# matches the run log itself (`hmac-apply-<run_id>.log`, no trailing `-segment`).
_HMAC_COMPONENT_LOG_RE = re.compile(r"^hmac-apply-[0-9a-f]{32}-[0-9A-Za-z._-]{1,64}\.log$")


def _is_hmac_component_log(run_id: str, name: str) -> bool:
    """True iff `name` is a well-formed HMAC per-step log leaf OWNED by `run_id` (mirror of
    bulk.is_component_log_for). Fail-closed on any other name."""
    if not _RUN_ID_RE.match(run_id or "") or not _HMAC_COMPONENT_LOG_RE.match(name or ""):
        return False
    return name.startswith(f"hmac-apply-{run_id}-")


class _StreamRedactor:
    """Stateful, byte-oriented, multi-pattern streaming redactor. Correct across EVERY read boundary
    (one-byte chunks, overlapping/shared-prefix patterns): patterns are masked LONGEST-first, and the
    last (maxlen-1) bytes of each flush are HELD as carry so a secret split over a chunk boundary is
    still masked before anything is written. Feeds/returns BYTES (redaction happens pre-decode)."""

    __slots__ = ("_pats", "_maxlen", "_carry")
    _MASK = b"****"

    def __init__(self, patterns):
        pats = sorted({p for p in patterns if p}, key=len, reverse=True)
        self._pats = tuple(pats)
        self._maxlen = max((len(p) for p in self._pats), default=0)
        self._carry = b""

    def _scan(self, buf: bytes, at_eof: bool) -> tuple[bytes, bytes]:
        """Left-to-right scan of `buf`: (emit, carry). At each position, if the remaining bytes are a
        proper PREFIX of some pattern (could still grow into a match) and we are NOT at EOF, HOLD from
        there — this is checked BEFORE masking, so a shorter pattern that is a prefix of a longer one is
        never masked prematurely (shared-prefix / overlapping correctness). Otherwise mask the
        longest full match, else emit one byte."""
        out = bytearray()
        i, n = 0, len(buf)
        while i < n:
            if not at_eof and any(n - i < len(p) and p.startswith(buf[i:]) for p in self._pats):
                break                                # remaining could still complete a (longer) pattern
            matched = next((p for p in self._pats if buf[i:i + len(p)] == p), None)
            if matched is not None:
                out += self._MASK
                i += len(matched)
            else:
                out += buf[i:i + 1]
                i += 1
        return bytes(out), buf[i:]

    def feed(self, chunk: bytes) -> bytes:
        if not self._pats:
            return chunk
        emit, self._carry = self._scan(self._carry + chunk, at_eof=False)
        return emit

    def flush(self) -> bytes:
        emit, self._carry = self._scan(self._carry, at_eof=True)
        return emit


# Cooperative-abort flag. The detached `_hmac-apply` driver's SIGTERM/SIGINT handler does ONLY a plain
# assignment to this module global — NO locks, filesystem, marker writes, printing, or termination (Python
# forbids sync primitives in signal handlers). The runner POLLS it. Process-global: exactly one apply runs
# per process (the detached driver, or a foreground CLI run).
_ABORT = False


def _request_hmac_abort(*_signal_args):
    global _ABORT
    _ABORT = True


def _hmac_abort_requested() -> bool:
    return _ABORT


def _reset_hmac_abort():
    global _ABORT
    _ABORT = False


def _hmac_now_utc() -> str:
    """Canonical persisted UTC timestamp — the SAME format the bulk marker uses."""
    import time as _t
    return _t.strftime("%Y-%m-%dT%H:%M:%SZ", _t.gmtime())


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
                ("running", "done", "failed", "interrupted", "unsafe")):
            return {"unsafe": True, "reason": "HMAC apply marker malformed (schema)"}
        if d["phase"] == "running" and not d.get("finished"):
            try:
                job = _hmac_log_base(d["run_id"]) + ".log"
            except ValueError:
                return {"unsafe": True, "reason": "HMAC apply marker malformed (run id)"}
            if not self.log_running(d.get("sid", ""), job=job):
                # The driver is GONE but the run was never finalized.
                if d.get("startup_unverified"):
                    # The detached driver's spawn was NEVER accounted for (tracking failed; it refused at the
                    # gate without clearing this flag OR the explicit orphan-unsafe write also failed). Fail
                    # closed: BLOCKING unsafe, never retryable — the driver MIGHT still be executing.
                    d = dict(d, phase="unsafe", derived_unsafe=True,
                             unsafe_scope="escaped-or-output-unverified",
                             detail=("the apply driver spawn could not be verified — it MIGHT still be "
                                     "running (building/restarting). Inspect processes (ps), then Recover."))
                # If a step is still `running`, the process for that step (a detached build/restart session)
                # may OUTLIVE the driver — BLOCKING unsafe, never a retryable `interrupted`. Also the safety
                # net for a failed unsafe terminal write: the block survives without any successful write.
                elif any(isinstance(s, dict) and s.get("state") == "running" for s in d.get("steps", [])):
                    d = dict(d, phase="unsafe", derived_unsafe=True,
                             unsafe_scope="escaped-or-output-unverified",
                             detail=("the apply driver vanished while a step was still running — the build/"
                                     "restart MIGHT still be executing. Inspect processes (ps), then Recover."))
                else:
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

    def _hmac_component_log_list(self, st: dict) -> list:
        """Validated (title, filename) tuples from the marker's run-owned `component_logs` — each leaf
        bound to the FULL run id (browser never influences this list)."""
        out = []
        for e in (st.get("component_logs") or []):
            if isinstance(e, dict):
                title, log = e.get("title"), e.get("log")
                if (isinstance(title, str) and isinstance(log, str)
                        and _is_hmac_component_log(st.get("run_id", ""), log)):
                    out.append((title, log))
        return out

    def hmac_component_log_chunk(self, run_id: str, index: int, offset: int) -> dict:
        """LIVE sequential stream over the run's registered (already-redacted) build log(s): cursor =
        (index, byte offset), each framed by title. GET-safe, byte-capped, fail-closed (never raises)."""
        try:
            st = self.hmac_apply_status()
            if (not st or st.get("unsafe") or st.get("run_id") != run_id
                    or not isinstance(index, int) or index < 0 or index > 4096
                    or not isinstance(offset, int) or offset < 0 or offset > (1 << 40)):
                return {"index": 0, "offset": 0, "data": ""}
            logs = self._hmac_component_log_list(st)
            parts, budget, hops = [], 512 * 1024, 0
            while index < len(logs) and budget > 0 and hops < 8:
                hops += 1
                title, fname = logs[index]
                nbytes, text, size = self._read_named_log_chunk(fname, offset, budget)
                if nbytes == -2:                     # ABSENT (future step) — wait here
                    break
                if nbytes == -1:                     # UNSAFE leaf — frame a notice, advance if possible
                    if offset == 0:
                        parts.append(self._bulk_log_frame(title, f"logs/{fname} — [unavailable]"))
                    if index < len(logs) - 1:
                        index, offset = index + 1, 0
                        continue
                    break
                if nbytes:
                    if offset == 0:
                        parts.append(self._bulk_log_frame(title, f"logs/{fname}"))
                    # the file is byte-scrubbed at write; read-time redact is kept as defense-in-depth
                    parts.append(self._hmac_redact(text))
                    offset += nbytes
                    budget -= nbytes
                    continue
                if offset >= size and index < len(logs) - 1 \
                        and self._read_named_log_chunk(logs[index + 1][1], 0, 1)[0] != -2:
                    index, offset = index + 1, 0
                    continue
                break
            return {"index": index, "offset": offset, "data": "".join(parts)}
        except Exception:                            # noqa: BLE001 — a GET must never 500
            return {"index": 0, "offset": 0, "data": "",
                    "error": "component-log stream temporarily unavailable"}

    def hmac_component_log_seed(self, run_id: str) -> str:
        """Server-side seed of the detailed-log window for a finished run — a bounded drain of the live
        cursor API (inherits its validation/framing/safety). Terminates when the cursor stops advancing."""
        parts, total, idx, off, reads = [], 0, 0, 0, 0
        while reads < 4000 and total < 1_000_000:
            reads += 1
            ch = self.hmac_component_log_chunk(run_id, idx, off)
            if ch.get("error"):
                break
            data = ch.get("data", "")
            if ch["index"] == idx and ch["offset"] == off and not data:
                break
            parts.append(data)
            total += len(data)
            idx, off = ch["index"], ch["offset"]
        return "".join(parts)

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
                # A prior UNSAFE (unverified-stop) run BLOCKS a new one until it is proven clear or
                # explicitly acknowledged (the build might still be executing).
                st = self.hmac_apply_status()
                # FAIL CLOSED on unreadable/malformed state: never overwrite the corrupt evidence with a new
                # run — the operator must archive it (Recover) first.
                if st and st.get("unsafe"):
                    return ActionResult(False, "The HMAC apply state is unreadable or malformed — refusing "
                                        "to start (evidence preserved). Use Recover to archive it, then retry.",
                                        next_commands=[f"lhpc hmac recover {stack_id}"])
                if st and st.get("phase") == "unsafe":
                    if not self._hmac_try_auto_clear(st):
                        return ActionResult(False, "The previous HMAC apply ended UNSAFELY — the build "
                                            "could not be proven stopped. Inspect processes (ps) and use "
                                            "Recover before starting a new run.",
                                            next_commands=[f"lhpc hmac recover {stack_id}"])
                run_id = uuid.uuid4().hex
                # `startup_unverified`: the detached driver clears it only AFTER it proves it was
                # identity-tracked (see `_hmac_verify_tracked`). If tracking never lands (orphan), the driver
                # refuses and never clears it, so the run stays BLOCKING — even if the explicit unsafe write
                # below also fails (see `hmac_apply_status`'s derive).
                marker = {"run_id": run_id, "sid": stack_id, "action": action,
                          "phase": "running", "finished": False,
                          "steps": self._hmac_initial_steps(), "startup_unverified": True}
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
                # Capture the driver identity at the SAME instant `_track_or_terminate` does (reuse-proof).
                from . import procident
                ident = procident.proc_identity(pid)
                err = self._track_or_terminate(life, ln, pid, stack_id, "hmac-apply")
                if err:
                    # Mirror the bulk spawn path (service_bulk.py): an UNPROVEN-cessation tracking failure
                    # ("ORPHAN RISK") means the driver MIGHT still be building — a BLOCKING unsafe state, never
                    # an ordinary retryable `failed`. A proven-terminated failure stays ordinary `failed`.
                    if "ORPHAN RISK" in err:
                        self._hmac_mark_unsafe_orphan(
                            marker, ident,
                            "the apply driver could not be identity-tracked and its stop is UNPROVEN — it "
                            "MIGHT still be running (building/restarting). Inspect processes (ps) and "
                            "terminate any stray build, then Recover.")
                        return ActionResult(False, err,
                                            next_commands=[f"lhpc hmac recover {stack_id}"])
                    self._hmac_mark_failed(marker, None, err)
                    return ActionResult(False, err)
                return ActionResult(True, f"HMAC {action} started.", data={"run_id": run_id})
        except reslock.ResourceBusy:
            return ActionResult(False, "An HMAC apply is already starting (start lock contended).")

    def _hmac_verify_tracked(self, stack_id: str, run_id: str, emit) -> int:
        """DETACHED-driver admission GATE: prove the parent identity-tracked THIS process BEFORE any
        mutation. Bounded-poll our own job marker (the child can start just before the parent writes it);
        require op/target/pid/identity to match. On failure REFUSE — touch nothing — so the parent's
        `startup_unverified` marker (and any orphan-unsafe marker) stays blocking. Returns 0 to proceed,
        1 to refuse. Only the detached driver calls this; the foreground CLI writes its own marker first."""
        import os
        import time
        from . import procident
        try:
            job = _hmac_log_base(run_id) + ".log"
        except ValueError:
            emit("HMAC driver: invalid run id — refusing (no mutation).")
            return 1
        mypid = os.getpid()
        deadline = time.monotonic() + _HMAC_DRIVER_TRACK_TIMEOUT_S
        while True:
            raw = self._read_job_marker(job)
            if raw is not None:
                try:
                    ok = (raw.get("op") == "hmac-apply" and raw.get("target") == stack_id
                          and int(raw.get("pid")) == mypid
                          and procident.identity_matches(raw, mypid))
                except (TypeError, ValueError):
                    ok = False
                if ok:
                    return 0                              # tracked -> safe to proceed
                emit("HMAC driver: the job marker does not match this process — refusing (no mutation).")
                return 1
            if time.monotonic() >= deadline:
                emit("HMAC driver: parent tracking not confirmed within the startup window — "
                     "refusing (no mutation).")
                return 1
            time.sleep(0.1)

    def hmac_apply_cli(self, stack_id: str, action: str, emit) -> int:
        """FOREGROUND apply for the CLI: same step runner as the detached driver, streaming to
        stdout. Participates in the SAME single-flight admission + job-identity marker as detached runs
        (so its running marker is never read as `interrupted`), installs+RESTORES cooperative SIGINT/
        SIGTERM handlers (Ctrl-C aborts), and retires the job marker ONLY after the terminal marker."""
        from . import reslock, procident, runtime_fs, validators
        import os
        import signal
        import threading
        if not self.hmac_applies(stack_id):
            emit(f"HMAC password does not apply to '{stack_id}'.")
            return 1
        if action not in ("enable", "disable", "renew"):
            emit(f"unknown HMAC action: {action!r}")
            return 1
        if not self._paths.runtime_root_exists:
            emit("Runtime root is not bootstrapped yet. Run 'lhpc bootstrap' first.")
            return 1
        try:
            with reslock.operation_lock(self._paths, "hmac-apply", stack_id, ""):
                if self.hmac_apply_running():
                    emit("An HMAC apply is already running — wait for it to finish.")
                    return 1
                st = self.hmac_apply_status()
                # FAIL CLOSED on unreadable/malformed state (never overwrite the corrupt evidence).
                if st and st.get("unsafe"):
                    emit("The HMAC apply state is unreadable or malformed — refusing to start. Run "
                         "'lhpc hmac recover' to archive it, then retry.")
                    return 1
                if st and st.get("phase") == "unsafe" and not self._hmac_try_auto_clear(st):
                    emit("The previous HMAC apply ended UNSAFELY — inspect processes (ps) then run "
                         "'lhpc hmac recover' before starting a new run.")
                    return 1
                run_id = uuid.uuid4().hex
                job = _hmac_log_base(run_id) + ".log"
                ident = procident.proc_identity(os.getpid())
                if not self._write_job_marker(job, os.getpid(), stack_id, "hmac-apply", ident=ident):
                    emit("Could not identity-track the foreground run — aborting.")
                    return 1
                handlers = None
                if threading.current_thread() is threading.main_thread():
                    handlers = (signal.getsignal(signal.SIGTERM), signal.getsignal(signal.SIGINT))
                    signal.signal(signal.SIGTERM, _request_hmac_abort)
                    signal.signal(signal.SIGINT, _request_hmac_abort)
                try:
                    return self._hmac_run_steps(stack_id, action, run_id, emit)
                finally:
                    if handlers is not None:
                        signal.signal(signal.SIGTERM, handlers[0])
                        signal.signal(signal.SIGINT, handlers[1])
                    # retire the job marker ONLY after the terminal HMAC marker has been written
                    try:
                        slug = validators.path_component(job, field="job log")
                        runtime_fs.unlink(self._paths, self._paths.under("state", "jobs", slug + ".job"))
                    except (OSError, PathContainmentError, validators.ValidationError):
                        pass
        except reslock.ResourceBusy:
            emit("An HMAC apply is already starting (start lock contended).")
            return 1

    # ---- abort / recover ------------------------------------------------------------------------

    def _hmac_reconstruct_token(self, ident):
        from .proctree import SessionToken
        if not isinstance(ident, dict):
            return None
        try:
            return SessionToken(int(ident["pid"]), int(ident["starttime"]),
                                int(ident["sid"]), int(ident["pgid"]))
        except (KeyError, ValueError, TypeError):
            return None

    def _hmac_downgrade_unsafe(self, marker: dict, note: str) -> bool:
        """Clear an UNSAFE block: rewrite the marker to a normal terminal (aborted `failed`), dropping the
        session identity/scope so it no longer refuses new runs. Returns whether the terminal marker was
        DURABLY written — the caller must NOT report recovery (or admit a new run) on a False."""
        m = dict(marker)
        m.update(phase="failed", finished=True)
        m.pop("unsafe_scope", None)
        m.pop("session_ident", None)
        m.pop("driver_ident", None)
        m.pop("startup_unverified", None)          # recovered -> retryable, never re-derived as blocking
        m["detail"] = (note + " " + str(m.get("detail", "")))[:400]
        return self._hmac_write_marker(m)

    def _hmac_archive_corrupt(self) -> str:
        """Resolve a MALFORMED/UNREADABLE apply marker. Returns a typed outcome:
          "archived" — readable corrupt bytes were DURABLY copied to state/hmac_apply.corrupt.json AND the
                       live marker removed (evidence preserved, exactly as the page promises);
          "removed"  — the marker was unreadable/non-regular/empty (NOTHING to archive) and was removed as an
                       explicit operator acknowledgement — never claimed as "archived";
          ""         — could NOT resolve (the copy or the removal failed): the live marker is PRESERVED.
        Readable evidence is NEVER removed unless its archive copy is durably created first."""
        from . import runtime_fs
        marker = self._hmac_marker_path()
        try:
            raw = runtime_fs.read_bytes(self._paths, marker)     # O_NOFOLLOW: a symlink leaf is refused here
        except FileNotFoundError:
            return "removed"                                     # already gone -> resolved
        except (OSError, PathContainmentError):
            raw = b""                                            # unreadable/non-regular -> nothing to copy
        if raw:
            # DURABLE archive FIRST — refuse (keep the evidence) if it cannot be written.
            try:
                runtime_fs.atomic_write_bytes(
                    self._paths, self._paths.under("state", "hmac_apply.corrupt.json"), raw, 0o600)
            except (OSError, PathContainmentError):
                return ""                                        # copy failed -> preserve, do NOT remove
            try:
                runtime_fs.unlink(self._paths, marker)
            except FileNotFoundError:
                return "archived"
            except (OSError, PathContainmentError):
                return ""                                        # copied but not removed -> report failure
            return "archived"
        # Unreadable/non-regular/empty: no evidence to copy — remove as an explicit acknowledgement.
        try:
            runtime_fs.unlink(self._paths, marker)
        except FileNotFoundError:
            return "removed"
        except (OSError, PathContainmentError):
            return ""
        return "removed"

    def _hmac_try_auto_clear(self, st: dict) -> bool:
        """AUTO-clear an unsafe block ONLY for the `session-unverified` scope and ONLY when the stored
        session is PROVEN ceased (never on driver-exit alone). Returns whether it is now clear."""
        import os
        from . import proctree
        if st.get("phase") != "unsafe":
            return True
        if st.get("unsafe_scope") != "session-unverified":
            return False                                   # escaped-or-output: explicit ack only
        token = self._hmac_reconstruct_token(st.get("session_ident"))
        if token is not None and proctree.session_ceased(token, os.getpid()):
            # Clear ONLY if the terminal marker is durably written — a failed rewrite keeps the block.
            return self._hmac_downgrade_unsafe(st, "auto-cleared: the build session was proven stopped.")
        return False

    def hmac_apply_abort(self, stack_id: str, run_id: str) -> ActionResult:
        """REQUEST-ONLY abort: validate the EXACT live run + the driver job identity, then SIGTERM the
        DRIVER PID only (never killpg — the build is a separate session). Writes NO terminal marker — the
        driver's handler stops the build and writes the truthful terminal state."""
        import os
        import signal
        from . import procident
        if not self.hmac_applies(stack_id):
            return ActionResult(False, f"HMAC password does not apply to '{stack_id}'")
        st = self.hmac_apply_status()
        if not (st and not st.get("unsafe") and st.get("phase") == "running"
                and st.get("run_id") == run_id and st.get("sid") == stack_id):
            return ActionResult(False, "No live HMAC apply run matches — nothing to abort.")
        raw = self._read_job_marker(_hmac_log_base(run_id) + ".log")
        if not raw:
            return ActionResult(False, "The apply driver's job marker is missing or unreadable.")
        if raw.get("op") != "hmac-apply" or raw.get("target") != stack_id:
            return ActionResult(False, "The job marker does not match this HMAC run — refusing to signal.")
        try:
            pid = int(raw["pid"])
        except (KeyError, ValueError, TypeError):
            return ActionResult(False, "The job marker pid is invalid — refusing to signal.")
        if not procident.identity_matches(raw, pid):
            return ActionResult(False, "The apply driver identity could not be verified (recycled pid?) "
                                "— refusing to signal.")
        try:
            os.kill(pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError, OSError) as exc:
            return ActionResult(False, f"Could not signal the apply driver (pid {pid}): {exc}")
        return ActionResult(True, "Abort requested — the driver is stopping the run.")

    def hmac_apply_recover(self, stack_id: str, run_id: str) -> ActionResult:
        """Clear an UNSAFE block. STALE-run protection is in the SERVICE (under the single-flight lock):
        `run_id` must equal the CURRENT marker's. `session-unverified` clears only when the stored session
        is proven empty; `escaped-or-output-unverified` clears on the operator's explicit acknowledgement."""
        from . import reslock
        if not self.hmac_applies(stack_id):
            return ActionResult(False, f"HMAC password does not apply to '{stack_id}'")
        try:
            with reslock.operation_lock(self._paths, "hmac-apply", stack_id, ""):
                st = self.hmac_apply_status()
                # MALFORMED/UNREADABLE marker (schema-unsafe sentinel: top-level `unsafe`, no run_id): the
                # explicit archive path — move the corrupt evidence aside so a fresh run can proceed, never a
                # silent overwrite. This is the ONLY thing that resolves a fail-closed malformed state.
                if st and st.get("unsafe"):
                    outcome = self._hmac_archive_corrupt()
                    if outcome == "archived":
                        return ActionResult(True, "Archived the unreadable/malformed HMAC state (saved as "
                                            "state/hmac_apply.corrupt.json) — you can start a fresh run now.")
                    if outcome == "removed":
                        return ActionResult(True, "The HMAC state was unreadable and had nothing to archive; "
                                            "it was REMOVED as your acknowledgement — you can start a fresh "
                                            "run now.")
                    return ActionResult(False, "The malformed HMAC state could not be resolved (evidence "
                                        "preserved) — inspect state/hmac_apply.json by hand.")
                if not (st and st.get("run_id") == run_id and st.get("sid") == stack_id):
                    return ActionResult(False, "No matching HMAC run to recover (stale or already resolved).")
                if st.get("phase") != "unsafe":
                    return ActionResult(False, "The run is not in an unsafe state — nothing to recover.")
                if st.get("unsafe_scope") == "session-unverified":
                    if self._hmac_try_auto_clear(st):
                        return ActionResult(True, "Recovered: the build session was proven stopped.")
                    return ActionResult(False, "The build session is STILL alive (or the terminal marker "
                                        "could not be written) — inspect/terminate it (ps), then retry Recover.")
                # escaped-or-output-unverified: the Recover call IS the explicit post-inspection ack. Only
                # report success once the terminal marker is DURABLY written (else the block must persist).
                if not self._hmac_downgrade_unsafe(st, "acknowledged by operator after process inspection."):
                    return ActionResult(False, "Could not clear the unsafe state — the terminal marker was "
                                        "not written. Retry Recover.")
                return ActionResult(True, "Acknowledged — the unsafe block was cleared. Ensure no build "
                                    "process remains before applying again.")
        except reslock.ResourceBusy:
            return ActionResult(False, "HMAC recovery is contended (start lock) — try again shortly.")

    def _hmac_mark_failed(self, marker: dict, step_key, detail: str) -> bool:
        """Terminalize a run as `failed` (startup/spawn faults). Returns whether the marker was durably
        written — a False leaves the prior state on disk; the caller still reports failure. Drops
        `startup_unverified` so a successfully persisted ordinary failure stays RETRYABLE."""
        steps = []
        for s in marker.get("steps", []):
            s = dict(s)
            if step_key is not None and s["key"] == step_key:
                s["state"] = "failed"
            elif s.get("state") == "running":
                s["state"] = "failed"
            steps.append(s)
        m = dict(marker, steps=steps, phase="failed", finished=True, detail=detail[:400])
        m.pop("startup_unverified", None)
        return self._hmac_write_marker(m)

    def _hmac_mark_unsafe_orphan(self, marker: dict, ident, detail: str) -> bool:
        """Terminalize an ORPHAN-RISK startup as BLOCKING `unsafe`: the driver could not be identity-tracked
        and its stop is UNPROVEN, so it MIGHT still be executing. Recovery is EXPLICIT acknowledgement
        (`escaped-or-output-unverified`). The captured identity is the DRIVER's — stored as `driver_ident`
        (evidence only, never `session_ident`) so it can never reach `session_ceased()` / session-unverified
        auto-recovery (the firmware BUILD is a separate session the driver identity cannot vouch for)."""
        from . import procident
        steps = [dict(s, state="failed") if s.get("state") == "running" else dict(s)
                 for s in marker.get("steps", [])]
        m = dict(marker, steps=steps, phase="unsafe", finished=True, finished_at=_hmac_now_utc(),
                 unsafe_scope="escaped-or-output-unverified", detail=detail[:400])
        m.pop("startup_unverified", None)
        if procident.identity_complete(ident):
            m["driver_ident"] = ident
        return self._hmac_write_marker(m)

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
        _reset_hmac_abort()                  # a fresh run is never pre-aborted (process-global flag)
        _raw_emit = emit
        def emit(line):                       # scrub ALL emitted lines (log + CLI stdout)  # noqa: E306
            _raw_emit(_scrub(line))

        marker = {"run_id": run_id, "sid": stack_id, "action": action, "phase": "running",
                  "finished": False, "steps": self._hmac_initial_steps(), "component_logs": []}

        def _set(step_key, state):
            """Apply a step transition and DURABLY persist it. Returns False when the write failed — the
            caller MUST stop before the next mutation (never run on stale/absent state, P1)."""
            for st in marker["steps"]:
                if st["key"] == step_key:
                    st["state"] = state
            return self._hmac_write_marker(marker)

        def _persist_failed(where):
            """A nonterminal/initial marker write failed: durable run state is stale/absent. Record a
            best-effort terminal `failed` (may itself fail) and STOP — never proceed to the next mutation."""
            emit(f"==== FAILED: run state could not be persisted ({where}) — stopped before the next step ====")
            for st in marker["steps"]:
                if st.get("state") == "running":
                    st["state"] = "failed"
            marker.update(phase="failed", finished=True, finished_at=_hmac_now_utc(),
                          detail=f"run state could not be persisted ({where}).")
            self._hmac_write_marker(marker)
            return 1

        def _register_step_log(title, leaf, text):
            """Frame a non-build step's output in the second (task-log) window: write the SCRUBBED text to a
            run-owned leaf and register it via the SAME {title, log} mechanism the firmware build uses — so
            every step appears end-to-end with a header, in execution order (secret → firmware → bridge →
            node). Best-effort: a frame that cannot be written never fails the run."""
            if not _is_hmac_component_log(run_id, leaf):
                return
            try:
                fh = runtime_fs.open_log_truncate(self._paths, self._paths.under("logs", leaf))
                try:
                    fh.write(_scrub(text).rstrip("\n") + "\n")
                finally:
                    fh.close()
            except (OSError, PathContainmentError):
                return
            marker["component_logs"].append({"title": title, "log": leaf})
            self._hmac_write_marker(marker)

        def _terminal(step_key, phase, detail, **extra):
            """Write a TERMINAL marker. Returns whether it was durably persisted — a caller that MUST stay
            blocking (unsafe) relies on this: when False, the leftover `running` marker + in-flight step is
            surfaced as blocking-unsafe by hmac_apply_status (never a retryable interrupted)."""
            for st in marker["steps"]:
                if st["key"] == step_key and st["state"] not in ("done", "skipped"):
                    st["state"] = "failed"
            marker.update(phase=phase, finished=True, finished_at=_hmac_now_utc(),
                          detail=_scrub(detail)[:400], **extra)
            return self._hmac_write_marker(marker)

        def _fail(step_key, detail):
            if not _terminal(step_key, "failed", detail):
                emit("  (warning: the terminal failure state could not be persisted)")
            emit(f"==== FAILED: {detail} ====")
            return 1

        def _aborted(step_key):
            if not _terminal(step_key, "failed",
                             "aborted by operator — the run did not finish; re-run to complete "
                             "(the meshcom link may be down until then)."):
                emit("  (warning: the terminal aborted state could not be persisted)")
            emit("==== ABORTED: the run was cancelled by the operator ====")
            return 1

        def _unsafe(step_key, detail, scope, session_ident):
            # The build's cessation/draining was NOT proven — this MUST stay blocking. If the unsafe marker
            # cannot be persisted, the leftover `running` marker with this step still `running` is re-derived
            # as blocking-unsafe by hmac_apply_status (see the driver-gone branch) — never a retryable state.
            if not _terminal(step_key, "unsafe", detail + " — the build MIGHT still be running; inspect "
                             "processes (ps) then use Recover before retrying.",
                             unsafe_scope=(scope or "session-unverified"), session_ident=session_ident):
                emit("  (warning: the UNSAFE state could not be persisted — the block is preserved via the "
                     "unfinished run marker; Recover is still required)")
            emit(f"==== UNSAFE: {detail} — inspect processes before retrying ====")
            return 1

        if not self._hmac_write_marker(marker):             # INITIAL write — no mutation before it succeeds
            return _persist_failed("initial")
        emit(f"==== HMAC {action} on {stack_id} ====")

        # 1. secret -------------------------------------------------------------------------------
        if _hmac_abort_requested():
            return _aborted("secret")
        if not _set("secret", "running"):
            return _persist_failed("secret=running")        # abort BEFORE hmac_set_secret
        emit("Updating the password secret…")
        r = self.hmac_set_secret(stack_id, action)          # never returns the secret value
        if not r.ok:
            return _fail("secret", r.summary)
        _note_secret()                       # add the NEW secret (enable/renew); disable left nothing new
        _register_step_log("Update the password secret",
                           f"hmac-apply-{run_id}-secret.log", r.summary)
        if not _set("secret", "done"):
            return _persist_failed("secret=done")           # abort BEFORE the firmware build
        emit(f"  {r.summary}")

        # 2. firmware rebuild (bakes the current secret) — the long, cancellable step -------------
        if _hmac_abort_requested():
            return _aborted("firmware")
        if not _set("firmware", "running"):
            return _persist_failed("firmware=running")      # abort BEFORE the build
        emit(f"Rebuilding the firmware ({node.id}) — this takes several minutes…")
        # FREEZE the complete old+new secret set into an immutable byte-pattern tuple BEFORE the build; the
        # runner's drain thread scrubs the raw byte stream through it before ANYTHING is persisted.
        redactor = _StreamRedactor(tuple(sec.encode("utf-8", "replace") for sec in secrets_seen))

        def _register(title, log):
            # DURABLE registration BEFORE the log leaf is created; if it cannot be persisted, DO NOT launch.
            if not _is_hmac_component_log(run_id, log):
                return
            marker["component_logs"].append({"title": title, "log": log})
            if not self._hmac_write_marker(marker):
                raise _SourceTxnBlocked("HMAC apply: component-log registration could not be persisted")

        b = self.build(node.id, apply=True, on_component_log=_register,
                       log_base_override=f"hmac-apply-{run_id}",
                       redactor=redactor, should_cancel=_hmac_abort_requested)
        for line in b.details:
            emit(line)
        meta = b.data or {}
        if meta.get("unsafe"):
            return _unsafe("firmware", "the firmware build could not be proven stopped",
                           meta.get("unsafe_scope", ""), meta.get("session_ident"))
        if meta.get("cancelled"):             # cancelled AND proven stopped -> a normal aborted terminal
            return _aborted("firmware")
        if not b.ok:
            return _fail("firmware", b.summary)
        if not _set("firmware", "done"):
            return _persist_failed("firmware=done")         # abort BEFORE the restarts
        emit(f"  {b.summary}")

        # 3 + 4. restart the live link (only if it is actually up — else the rebuilt firmware +
        # secret take effect on the next start). Captured ONCE before touching either process.
        was_running = self.stack_running(stack_id)
        for key, target, what in (("bridge", c.id, "bridge"), ("node", node.id, "node")):
            if not was_running:
                if not _set(key, "skipped"):
                    return _persist_failed(f"{key}=skipped")
                emit(f"  {what} not running — the new firmware/secret applies on next start.")
                continue
            if _hmac_abort_requested():
                return _aborted(key)
            if not _set(key, "running"):
                return _persist_failed(f"{key}=running")    # abort BEFORE the restart
            emit(f"Restarting the {what} ({target})…")
            rr = self.restart(target, apply=True)
            # Detailed restart output goes to the second (task-log) window, framed with a header — the
            # narration keeps only the high-level line + summary (mirrors the firmware step + install-all).
            _register_step_log(f"Restart the {what} ({target})",
                               f"hmac-apply-{run_id}-{key}.log",
                               "\n".join(rr.details) or rr.summary)
            if not rr.ok:
                return _fail(key, rr.summary)
            if not _set(key, "done"):
                return _persist_failed(f"{key}=done")       # abort BEFORE the next restart
            emit(f"  {rr.summary}")

        marker.update(phase="done", finished=True, finished_at=_hmac_now_utc())
        if not self._hmac_write_marker(marker):             # TERMINAL success write — must not claim success
            emit("==== FAILED: the run finished but its completion could not be persisted — treat as "
                 "incomplete ====")
            return 1
        verb = {"enable": "enabled", "disable": "disabled", "renew": "renewed"}[action]
        emit(f"==== DONE: HMAC password {verb} ====")
        return 0
