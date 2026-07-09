"""Shared application/service layer — the single entry point for all behaviour.

The CLI adapter and the web adapter both call ONLY this module, guaranteeing
identical validation, status interpretation and results. Read methods are bounded
and read-only; mutating methods print a plan and apply only when confirmed.

`build_snapshot()` is the single probing path; both `status()` (CLI text) and the
web adapter call it, so a page load and a CLI run see the same fresh evidence.
"""

from __future__ import annotations

import json
import os
import shlex
import sys
import threading
import time
import uuid
import contextlib
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from . import daemon_control
from . import manifest as manifest_mod
from . import resources as resources_mod
from . import validators
from .config import (
    Config,
    ConfigError,
    apply_config_transaction,
    conditional_clear_stack_config,
    merge_stack_values,
    _patch_local_table,
    render_local_tables,
    render_stack_config,
    update_stack_config,
    _load_runtime_toml,
    _stack_config_path,
    load_config,
    load_stack_config,
    render_keyval,
    save_component_remote,
    save_stack_config,
    update_toml,
    update_yaml,
)
from .install import Installer, Plan
from .lifecycle import Lifecycle
from .model import (
    ComponentKind,
    ResourceMode,
    RunState,
    SourceState,
    Stack,
    TxState,
    emit_param,
)
from . import procident
from . import runtime_fs
from .outcomes import CompResult, Outcome, applied_ok
from .paths import Paths, PathContainmentError, resolve_paths
from .probes import RealSystem, System
from .probes import hardware
from .status import Snapshot, StatusProber, rollup_states, summarize

_SPI_DEV = "/dev/spidev0.0"
_GPIO_DEV = "/dev/gpiochip0"
_UNSET = object()                # sentinel: "not yet resolved" (distinct from None)


def _canon_git_url(url: str) -> str:
    """Normalize a git remote URL for identity comparison: the `git@host:path` / `ssh://` /
    `https://` forms are reduced to a common `host/path` (so an approved canonical origin
    matches regardless of transport), trailing `.git`/`/` stripped. Only the HOST is
    lowercased — the path is left case-exact, since on case-sensitive hosts `host/Foo` and
    `host/foo` are DIFFERENT repos (avoids a cross-repo false-accept). Returns `""` for a
    degenerate/empty input (the caller must reject an empty canonical, never treat two
    empties as a match)."""
    u = (url or "").strip()
    low = u.lower()
    for pre in ("https://", "http://", "ssh://", "git://"):
        if low.startswith(pre):
            u = u[len(pre):]
            break
    else:
        if low.startswith("git@"):
            u = u[len("git@"):].replace(":", "/", 1)
    if "@" in u.split("/", 1)[0]:                # strip user@ credentials in host part
        u = u.split("@", 1)[1]
    u = u.rstrip("/")
    if u.lower().endswith(".git"):
        u = u[:-4]
    u = u.rstrip("/")
    host, sep, rest = u.partition("/")
    return host.lower() + sep + rest             # host case-folded; path preserved


class _StopRun(Exception):
    """Internal control-flow signal to break out of the run-service body to the record/release
    stage (keeps the finalization in one place)."""


def _proc_start_time(pid: int) -> int:
    """Field 22 of /proc/<pid>/stat (starttime, clock ticks since boot) — the stable half of a
    (pid, start_time) process identity. 0 if unavailable."""
    try:
        with open(f"/proc/{pid}/stat", "rb") as fh:
            data = fh.read()
        # comm (field 2) is parenthesized and may contain spaces/parens — split after the LAST ')'.
        fields = data[data.rindex(b")") + 2:].split()
        return int(fields[19])                            # starttime = 22nd overall; index 19 here
    except (OSError, ValueError, IndexError):
        return 0


def _proc_ceased(pid, start_time) -> bool:
    """True when the recorded (pid, start_time) process no longer exists (dead, or the PID was
    reused by a different process). Conservative: unknown/malformed identity → NOT ceased."""
    if not isinstance(pid, int) or pid <= 0:
        return False
    if not os.path.exists(f"/proc/{pid}"):
        return True
    return _proc_start_time(pid) != (start_time if isinstance(start_time, int) else -1)


class SourceTxnBlocked(Exception):
    """Raised by the source-operation guard when an unresolved source-transaction journal
    is present — every source-mutating op fails closed until an operator resolves it."""


@dataclass(frozen=True)
class ConfigWrite:
    """Structured result of generating one component's config file."""

    component: str
    path: str
    status: str            # "written" | "linked-readonly" | "no-base" | "failed"
    detail: str = ""


@dataclass
class ActionResult:
    """Uniform result object rendered identically by every adapter."""

    ok: bool
    summary: str
    details: list[str] = field(default_factory=list)
    next_commands: list[str] = field(default_factory=list)
    data: dict = field(default_factory=dict)
    # Typed per-component lifecycle results (start/stop/restart). Adapters may render
    # from these; `ok` for an applied lifecycle action is derived from them.
    results: tuple = field(default_factory=tuple)


class ControllerService:
    """Facade over the core. Construct once per process; cheap and stateless.

    `system` and `paths` are injectable so tests drive it with fakes.
    """

    def __init__(
        self,
        manifest_path: Path | None = None,
        system: System | None = None,
        paths: Paths | None = None,
    ) -> None:
        self._manifest_path = manifest_path
        self._system = system or RealSystem()
        self._paths = paths or resolve_paths()
        self._stacks: tuple[Stack, ...] | None = None
        self._controller = _UNSET       # controller spec (None = none declared); lazy
        self._config: Config | None = None
        # The config cache is shared by the (threaded) web app; guard it so a save on one
        # thread is visible to the next read on any thread (no stale callsign/remote).
        self._config_lock = threading.RLock()
        self._config_mtime = None               # local.toml mtime the cache was built from
        # THREAD-LOCAL re-entrancy bookkeeping: this service is shared by the (possibly
        # threaded) web app, so lock ownership is scoped to the CURRENT thread. Only
        # nested calls in the SAME thread skip re-acquisition; an independent thread
        # contends through `reslock`. Recursion COUNTS (not a flat set) so a nested
        # lifecycle call cannot prematurely release an outer guard's lock.
        self._lock_state = threading.local()
        # Per-thread re-entrancy for the SHARED configuration-stability guard held across an applied
        # start/restart (see `_config_stable`).
        self._cfg_stable_state = threading.local()

    @contextmanager
    def _config_stable(self):
        """Hold saved configuration STABLE for the duration of an applied lifecycle transition — a
        SHARED read lock on the runtime config lock file. Config MUTATIONS take the EXCLUSIVE
        `config_lock` (LOCK_EX), so a concurrent save (this process or another) WAITS until the
        protected transition completes, and a start WAITS for an in-progress save. Independent starts
        share the lock and never serialise. RE-ENTRANT per thread, so a public start/restart nests
        with the internal `_start_impl`/`_restart_impl` (and stop/start within a restart) without
        self-deadlock. LOCK ORDER: this guard is acquired BEFORE any lifecycle/resource lock; a
        lock/read failure raises here so the caller fails typed BEFORE any lifecycle side effect."""
        import fcntl
        from . import runtime_fs
        st = self._cfg_stable_state
        depth = getattr(st, "depth", 0)
        if depth == 0:
            fh = runtime_fs.open_lock(self._paths, self._paths.under("config", ".lock"))
            try:
                fcntl.flock(fh, fcntl.LOCK_SH)
            except OSError:
                fh.close()
                raise
            st.fh = fh
        st.depth = depth + 1
        try:
            yield
        finally:
            st.depth -= 1
            if st.depth == 0:
                try:
                    fcntl.flock(st.fh, fcntl.LOCK_UN)
                finally:
                    st.fh.close()
                    st.fh = None

    # ---- config / installer ---------------------------------------------

    def config(self) -> Config:
        with self._config_lock:
            # AUDIT CC4: reload when local.toml's mtime changed since the cache was built.
            # A long-lived web process otherwise served a stale callsign/remotes forever
            # after an out-of-band hand-edit (a scenario the loader explicitly supports),
            # and an in-lock plan could verify identity against the wrong effective remote.
            mtime = self._local_config_mtime()
            if self._config is None or mtime != self._config_mtime:
                self._config = load_config(self._paths)
                self._config_mtime = mtime
            return self._config

    def _local_config_mtime(self):
        try:
            return os.stat(self._paths.runtime_root / "config" / "local.toml").st_mtime
        except OSError:
            return None

    def web_session_secret(self) -> bytes:
        """The persistent web-console session secret (generated once, 0600, survives restart;
        not cleared by 'Reset to default'). Thin delegation to config — the web adapter calls
        this instead of reaching into runtime paths."""
        from . import config as _config
        return _config.web_session_secret(self._paths)

    # ---- webserver (controller-owned component; NOT a managed stack) ----------
    #
    # Thin delegation to pki/webserver/config. Every mutation validates before writing and
    # fails closed; status reads cached evidence only. These are controller-owned and are
    # NEVER routed through the generic stack/component verbs (install/build/test/...): the
    # Webserver "component" is presentation only, so controller isolation is unaffected.

    def webserver_monitor(self) -> "ActionResult":
        """READ-ONLY cached status (Monitor/GET): desired config + last-proven effective
        evidence + PKI state + warnings. No probing, no mutation."""
        from . import webserver as _ws
        view = _ws.monitor_view(self._paths, self.config().webserver)
        return ActionResult(True, "webserver monitor", data=view)

    def webserver_verify(self) -> "ActionResult":
        """Explicit verification: assemble + persist the effective-evidence checklist."""
        from . import webserver as _ws
        ev = _ws.verify(self._system, self._paths, self.config().webserver)
        failed = [k for k, v in ev["checks"].items() if v == "failed"]
        ok = not failed
        summary = "webserver verified" if ok else f"verification found issues: {', '.join(failed)}"
        return ActionResult(ok, summary, data=ev)

    def webserver_init(self, *, dns_sans=None, ip_sans=None, confirm=False) -> "ActionResult":
        """First-time bootstrap (correction #2): create BOTH CAs, the server leaf, and an
        initial (empty) CRL. Remote exposure stays disabled until explicitly enabled + proven.
        RE-initializing when a CA already exists is DESTRUCTIVE (invalidates every issued
        certificate) and requires explicit `confirm`."""
        from . import pki as _pki
        st = _pki.pki_status(self._paths)
        if (st["server_ca"].get("present") or st["client_ca"].get("present")) and not confirm:
            return ActionResult(False, "PKI already exists — recreating the CAs is DESTRUCTIVE "
                                "(invalidates all issued client/server certificates). Confirm to "
                                "proceed.", next_commands=["lhpc webserver init --confirm-recreate"])
        cfg = self.config().webserver
        dns = list(dns_sans) if dns_sans is not None else list(cfg.dns_sans)
        ips = list(ip_sans) if ip_sans is not None else list(cfg.ip_sans)
        if not dns and not ips:
            dns = ["localhost"]                    # usable loopback default SANs — must match the
            ips = ["127.0.0.1"]                    # advertised https://127.0.0.1:8443/ endpoint
        # Persist the SANs into DESIRED config (correction 3) so productive trusted-host
        # enforcement AND `tls-renew` use them. FAIL CLOSED (correction A): if persistence fails
        # for ANY reason (validation, ConfigError/lock, unsafe path, malformed local.toml, I/O)
        # we abort BEFORE touching any PKI material — no CA/cert/CRL/inventory is created or
        # replaced, and no success is reported.
        from . import config as _config
        try:
            _config.save_webserver_config(self._paths, dns_sans=dns, ip_sans=ips)
        except Exception as exc:
            return ActionResult(False, f"webserver init aborted — could not persist SANs to "
                                f"config ({exc}); no PKI was created or replaced")
        self._invalidate_config()
        try:
            _pki.init_server_ca(self._paths, force=True)
            _pki.init_client_ca(self._paths, force=True)
            _pki.issue_server_cert(self._paths, dns_sans=dns, ip_sans=ips,
                                   days=cfg.server_cert_days)
            _pki.build_crl(self._paths)
        except _pki.PKIError as exc:
            return ActionResult(False, f"webserver init failed: {exc}")
        return ActionResult(True, "webserver PKI initialized (two CAs + server cert + CRL)",
                            next_commands=["lhpc webserver verify"])

    def webserver_configure(self, **fields) -> "ActionResult":
        from . import config as _config
        from .validators import ValidationError
        try:
            _config.save_webserver_config(self._paths, **fields)
        except (ValidationError, _config.ConfigError) as exc:
            return ActionResult(False, f"invalid webserver config: {exc}")
        self._invalidate_config()
        return ActionResult(True, "webserver configuration saved (desired; run verify/apply)",
                            next_commands=["lhpc webserver verify"])

    def webserver_expose(self, cidrs, *, access_mode=None, confirm=False,
                         confirm_public=False) -> "ActionResult":
        """Enable remote exposure. Requires >=1 CIDR; a public default route (0.0.0.0/0) or
        a no-auth remote mode needs elevated confirmation. Writes desired config only — the
        listener is not proven active until verify/apply."""
        from . import config as _config, webserver as _ws
        from .config import WebserverConfig
        from .validators import ValidationError
        cidrs = list(cidrs or [])
        mode = access_mode or self.config().webserver.access_mode
        probe = WebserverConfig(bind="0.0.0.0", port=self.config().webserver.port,
                                access_mode=mode, remote_exposed=True,
                                allowed_cidrs=tuple(cidrs))
        plan = _ws.plan_exposure(probe)
        if plan["problems"]:
            return ActionResult(False, "cannot enable remote exposure",
                                details=plan["problems"])
        if plan["danger"] == "elevated" and not confirm_public:
            what = "no client authentication" if plan.get("no_auth") else "a public source range (0.0.0.0/0)"
            return ActionResult(False, f"remote exposure with {what} needs elevated confirmation",
                                details=["re-run with the elevated confirmation to proceed"])
        if not confirm:
            return ActionResult(False, "remote exposure needs explicit confirmation",
                                details=["re-run with confirmation to proceed"])
        try:
            _config.save_webserver_config(self._paths, bind="0.0.0.0", remote_exposed=True,
                                          allowed_cidrs=cidrs, access_mode=mode)
        except (ValidationError, _config.ConfigError) as exc:
            return ActionResult(False, f"invalid exposure config: {exc}")
        self._invalidate_config()
        return ActionResult(
            True, "remote exposure enabled (desired) — now APPLY to rebind the listener to "
            f"0.0.0.0:{self.config().webserver.port} and reload nginx (until then it stays on "
            "loopback and remote clients get connection refused)",
            details=["lhpc webserver apply           # reload a running nginx with the new bind",
                     "lhpc webserver start-service   # if nginx is not running yet"],
            next_commands=["lhpc webserver apply"])

    def webserver_disable_remote(self) -> "ActionResult":
        from . import config as _config
        _config.save_webserver_config(self._paths, bind="127.0.0.1", remote_exposed=False)
        self._invalidate_config()
        return ActionResult(True, "remote exposure disabled (bind reset to loopback) — "
                            "verify to prove the remote listener has ceased",
                            next_commands=["lhpc webserver verify"])

    def webserver_reset_defaults(self) -> "ActionResult":
        """Reset to safe defaults AND prove remote exposure has ceased. Writes DESIRED defaults
        (loopback:8443, local unauthenticated, remote off, CIDRs cleared), stages + VALIDATES a
        loopback-only nginx config, and — if a proven LHPC-owned nginx master exists — reloads
        it (a successful reload of the loopback-only config is the cessation proof: the new
        config has no remote listener). Reports success ONLY when cessation is proven; otherwise
        stays truthful ('reset requested; remote cessation unproven'). NEVER deletes CA keys,
        certificates, CRL, revocation history, `.p12` exports, or the session secret."""
        from . import config as _config, runtime_fs as _rfs, webserver as _ws
        _config.save_webserver_config(self._paths, bind="127.0.0.1", port=8443,
                                      access_mode="local-open-remote-auth",
                                      remote_exposed=False, allowed_cidrs=[])
        self._invalidate_config()
        cfg = self.config().webserver
        # Stage + validate the loopback-only config; promote only on success (never clobber a
        # proven live config with an invalid one).
        ok, msg, _staged = _ws.stage_and_validate(self._system, self._paths, cfg)
        ev = _ws.verify(self._system, self._paths, cfg)
        if not ok:
            return ActionResult(False, "reset requested; loopback config invalid — remote "
                                f"cessation UNPROVEN ({msg})", data=ev)
        _ws.promote_config(self._paths)
        if _ws.nginx_master_active(self._paths):
            state, rmsg = _ws.reload(self._system, self._paths)
            if state == "reloaded":
                ev["effective"] = {**ev.get("effective", {}),
                                   "remote_listener": False, "remote_cessation_proven": True}
                _ws.write_evidence(self._paths, ev)
                return ActionResult(True, "webserver reset to defaults — remote exposure ceased "
                                    "(loopback-only config reloaded and proven)", data=ev)
            return ActionResult(False, f"reset requested; nginx reload failed — remote cessation "
                                f"UNPROVEN ({rmsg})", data=ev)
        ev["effective"] = {**ev.get("effective", {}), "remote_cessation_proven": False}
        _ws.write_evidence(self._paths, ev)
        return ActionResult(False, "reset requested; no active nginx master to reload — remote "
                            "cessation UNPROVEN (start/repair the service to prove it)",
                            next_commands=["lhpc webserver verify"], data=ev)

    def webserver_tls_renew(self) -> "ActionResult":
        from . import pki as _pki
        cfg = self.config().webserver
        try:
            summ = _pki.issue_server_cert(self._paths, dns_sans=list(cfg.dns_sans),
                                          ip_sans=list(cfg.ip_sans), days=cfg.server_cert_days)
        except _pki.PKIError as exc:
            return ActionResult(False, f"server certificate renewal failed: {exc}")
        return ActionResult(True, f"server certificate renewed (serial {summ['serial']})",
                            data=summ)

    def webserver_cert_issue(self, label, passphrase) -> "ActionResult":
        from . import pki as _pki
        cfg = self.config().webserver
        try:
            summ = _pki.issue_client_cert(self._paths, label, days=cfg.client_cert_days,
                                          passphrase=passphrase)
        except Exception as exc:
            return ActionResult(False, f"client certificate issue failed: {exc}")
        return ActionResult(True, f"issued client certificate '{summ['label']}'",
                            details=[f"export: {summ['export']}",
                                     f"sha256: {summ['export_sha256']}",
                                     f"expires: {summ['not_after']}"], data=summ)

    def webserver_cert_reissue(self, label, passphrase) -> "ActionResult":
        from . import pki as _pki
        cfg = self.config().webserver
        try:
            summ = _pki.reissue_client_cert(self._paths, label, days=cfg.client_cert_days,
                                            passphrase=passphrase)
        except Exception as exc:
            return ActionResult(False, f"reissue failed: {exc}")
        return ActionResult(True, f"reissued client certificate '{summ['label']}'", data=summ)

    def webserver_cert_list(self) -> "ActionResult":
        from . import pki as _pki
        return ActionResult(True, "client certificates",
                            data={"certs": _pki.list_client_certs(self._paths)})

    def webserver_cert_revoke(self, label) -> "ActionResult":
        from . import pki as _pki
        try:
            _pki.revoke_client_cert(self._paths, label)
        except Exception as exc:
            return ActionResult(False, f"revoke failed: {exc}")
        return ActionResult(True, f"revocation RECORDED for '{label}' and CRL regenerated — "
                            "not proven effective until the proxy reloads and rejects it",
                            next_commands=["lhpc webserver verify"])

    def webserver_cert_discard_export(self, label) -> "ActionResult":
        from . import pki as _pki
        removed = _pki.discard_export(self._paths, label)
        return ActionResult(True, f"export {'discarded' if removed else 'already absent'} for '{label}'")

    def webserver_cert_export_bytes(self, label) -> "bytes | None":
        """Raw `.p12` bytes for a label (or None). The WEB route must gate this on a
        loopback-origin session; the CLI locates the file directly."""
        from . import pki as _pki
        return _pki.read_export(self._paths, label)

    def webserver_apply(self) -> "ActionResult":
        """Activate the DESIRED config: render + validate the nginx config FIRST (never
        activate an invalid one), then reload an already-running LHPC-owned nginx master
        (never systemctl, never start the unit), then verify + persist evidence. A missing/
        inactive master returns a typed 'service not active / repair required' result — the
        web process performs no start and no package install."""
        from . import webserver as _ws
        if not _ws.nginx_installed(self._system):
            return ActionResult(False, "nginx is not installed — required system dependency for "
                                "the production webserver", details=[_ws.NGINX_INSTALL_CMD],
                                next_commands=[_ws.NGINX_INSTALL_CMD])
        cfg = self.config().webserver
        # Stage + validate BEFORE touching the live config; promote atomically only on success
        # (a failed nginx -t leaves the previous proven live config byte-for-byte intact).
        ok, msg, _staged = _ws.stage_and_validate(self._system, self._paths, cfg)
        if not ok:
            return ActionResult(False, "nginx config validation failed; previous proven "
                                f"configuration remains active ({msg})")
        _ws.promote_config(self._paths)
        state, rmsg = _ws.reload(self._system, self._paths)
        ev = _ws.verify(self._system, self._paths, cfg)
        if state == "repair_required":
            return ActionResult(False, "config valid but the nginx service is not active — "
                                "repair required (operator context)",
                                details=[rmsg], data=ev)
        if state == "failed":
            return ActionResult(False, f"nginx reload failed: {rmsg}", data=ev)
        return ActionResult(True, "webserver configuration applied and nginx reloaded", data=ev)

    def webserver_start_service(self) -> "ActionResult":
        """OPERATOR-CONTEXT bootstrap (correction 1): generate + validate + promote the nginx
        config, then ENABLE + START the rootless nginx user unit via `systemctl --user`. This is
        the only path that STARTS nginx — it REFUSES to run from a managed unit (the web process
        never starts a listener), so after `init` the operator runs this once to bring the HTTPS
        console up. Prerequisites (nginx installed, server cert present, config valid) are
        checked and reported truthfully."""
        import os as _os
        from . import pki as _pki, webserver as _ws
        if _os.environ.get("INVOCATION_ID"):
            return ActionResult(False, "refusing to start nginx from a managed unit — run "
                                "`lhpc webserver start-service` from an interactive operator shell")
        if not _ws.nginx_installed(self._system):
            return ActionResult(False, "nginx is not installed", details=[_ws.NGINX_INSTALL_CMD],
                                next_commands=[_ws.NGINX_INSTALL_CMD])
        if not _pki.pki_status(self._paths)["server_cert"].get("present"):
            return ActionResult(False, "no HTTPS server certificate — run `lhpc webserver init` "
                                "first", next_commands=["lhpc webserver init"])
        cfg = self.config().webserver
        ok, msg, _staged = _ws.stage_and_validate(self._system, self._paths, cfg)
        if not ok:
            return ActionResult(False, f"nginx config invalid — not starting ({msg})")
        _ws.promote_config(self._paths)
        r = self._system.runner.run(
            ["systemctl", "--user", "enable", "--now", "lhpc-nginx.service"], 20.0)
        if getattr(r, "not_found", False) or r.returncode != 0:
            detail = (r.stderr or r.stdout or "systemctl failed").strip().splitlines()
            return ActionResult(False, "could not enable/start lhpc-nginx.service",
                                details=[detail[-1] if detail else "systemctl failed"],
                                next_commands=["systemctl --user enable --now lhpc-nginx.service"])
        ev = _ws.verify(self._system, self._paths, cfg)
        return ActionResult(True, f"nginx enabled + started — HTTPS console at "
                            f"https://{cfg.bind}:{cfg.port}/", data=ev)

    def _invalidate_config(self) -> None:
        """Drop the cached Config so the NEXT read (any thread) reloads from disk. Called
        after every successful config mutation so a saved callsign/locator/remote/param is
        immediately visible to subsequent web AND CLI service actions (no stale cache)."""
        with self._config_lock:
            self._config = None

    def _installer(self) -> Installer:
        return Installer(self._paths, self.stacks(), self.config(), self._system)

    @contextmanager
    def _source_operation_guard(self, source_paths, op: str = "source-op"):
        """ONE atomic source-operation boundary (P0.1) — no preflight/acquire gap:
          1. acquire the source-transaction INDEX lock;
          2. recover + validate journals;
          3. block (raise `SourceTxnBlocked`) if ANY unresolved journal remains;
          4. acquire ALL affected source-path locks (stable sorted) WHILE STILL HOLDING
             the index lock — a handoff, so no journal can appear between the check and
             the lock and the source is already locked before the index is released;
          5. release the index lock and yield with the source locks held for the op.
        Raises `reslock.ResourceBusy` if the index or a source lock is contended.

                RE-ENTRANT per THREAD (shared `_held_counts` with the lifecycle guard): a source key
        already held by an OUTER boundary in this thread — e.g. the bulk-operation lease —
        is not re-flocked, the index/recovery step is skipped for fully-covered nests (the
        outer boundary performed it and holds the locks, so no foreign journal can appear
        for a covered path), and a nested exit never releases the outer flocks. Independent
        threads/processes contend through `reslock` unchanged."""
        from . import reslock
        inst = self._installer()
        keys = sorted({reslock.source_lock_key(sp) for sp in source_paths})
        counts = self._held_counts()
        missing = [k for k in keys if counts.get(k, 0) == 0]
        bumped: list = []
        try:
            with contextlib.ExitStack() as src_stack:
                if missing:
                    # Index held across recovery + the source-lock handoff, then released.
                    with reslock.operation_lock(self._paths, inst._index_key(), op, ""):
                        inst._recover_scan()
                        if inst._pending_journals():
                            raise SourceTxnBlocked(
                                "an unresolved source-transaction journal is present — "
                                "resolve it before any source operation")
                        for k in missing:
                            self._acquire_key(src_stack, k, op, "")
                # Index released; source lock(s) remain held by src_stack for the operation.
                for k in keys:
                    counts[k] = counts.get(k, 0) + 1
                    bumped.append(k)
                yield
        finally:
            for k in bumped:
                counts[k] -= 1
                if counts[k] <= 0:
                    counts.pop(k, None)

    # ---- bulk run: status, gates, log, ack, spawn, driver (M2.1) -----------

    BULK_OP = "install-all"

    def bulk_status(self) -> dict | None:
        """Tri-state run state for GETs (file + /proc only, never mutates): None (absent),
        {"unsafe": True, reason}, or the marker dict — with a preparing/running marker
        whose identity-tracked job is provably GONE presented as `interrupted`."""
        from . import bulk as bulk_mod
        state, d = bulk_mod.read_marker(self._paths)
        if state == "absent":
            return None
        if state == "unsafe":
            return {"unsafe": True, "reason": d["reason"]}
        if d["state"] in ("preparing", "running"):
            job = bulk_mod.log_name_for(d["run_id"]) + ".log"
            if not self.log_running("all", job=job):
                d = dict(d, state="interrupted", derived_interrupted=True)
        return d

    def bulk_running(self) -> bool:
        st = self.bulk_status()
        return bool(st and not st.get("unsafe")
                    and st.get("state") in ("preparing", "running"))

    def _bulk_bootstrap_refusal(self) -> ActionResult:
        return ActionResult(
            ok=False,
            summary="Runtime root is not bootstrapped yet.",
            details=[f"Run 'lhpc bootstrap' to create {self._paths.runtime_root}."],
            next_commands=["lhpc bootstrap"],
        )

    def _bulk_gate(self) -> str:
        """Typed reason a NEW bulk run must not start; "" when clear. A DEAD lease, a
        dead/foreign bulk-start reservation, and an interrupted/unsafe marker are all
        MUTATION-BLOCKING until explicitly acknowledged."""
        from . import bulk as bulk_mod, procident
        rstate, res = bulk_mod.read_reservation(self._paths)
        if rstate == "unsafe":
            return ("the bulk-start reservation is unreadable or malformed — acknowledge "
                    "(recover) it before starting a new run")
        if rstate == "valid":
            if res.get("phase") == "spawning":
                # `spawning` is an IN-LOCK transition only: a persisted record is always
                # recovery evidence, never a live web-server-owned run.
                if res.get("child") == "none":
                    return ("a previous bulk start did not complete (no child process "
                            "remains) — acknowledge (recover) it before starting a "
                            "new run")
                return ("a previous bulk start may have spawned a child that was never "
                        "confirmed (ORPHAN RISK"
                        f"{', pid ' + str(res.get('pid')) if res.get('pid', 0) > 1 else ''}"
                        ") — inspect/terminate any such process, then acknowledge "
                        "(recover) with the confirmation")
            if res.get("phase") == "orphan-risk":
                return ("a previous bulk start left a child whose termination could not "
                        f"be proven (ORPHAN RISK{', pid ' + str(res.get('pid')) if res.get('pid', 0) > 1 else ''}"
                        f"): {res.get('reason', '')} — inspect/terminate the process, "
                        "then acknowledge (recover) with the confirmation")
            if procident.identity_matches(res.get("ident", {}), res.get("pid", -1)):
                return "a bulk run is already reserved/in progress"
            return ("a previous bulk start died holding its reservation — acknowledge "
                    "(recover) it before starting a new run")
        lstate, lease = bulk_mod.read_lease(self._paths)
        if lstate == "unsafe":
            return ("the bulk-operation lease is unreadable or malformed — acknowledge "
                    "(recover) it before starting a new run")
        if lstate == "valid":
            if procident.identity_matches(lease.get("ident", {}), lease.get("pid", -1)):
                return "a bulk run is already in progress (lease held)"
            return ("a previous bulk run died while holding its operation lease — "
                    "acknowledge (recover) it before starting a new run")
        st = self.bulk_status()
        if st is None:
            return ""
        if st.get("unsafe"):
            return ("the bulk run state is unreadable or malformed — acknowledge "
                    "(recover) it before starting a new run")
        if st["state"] in ("preparing", "running"):
            return "a bulk run is already in progress"
        if st["state"] == "interrupted":
            return ("the previous bulk run was interrupted — acknowledge (recover) it "
                    "before starting a new run")
        return ""

    def _bulk_claim(self, run_id: str) -> str:
        """Claim (or, for a manual CLI run, create) the bulk-start reservation for this
        driver process under the dedicated bulk-start lock. Returns "" when the slot is
        bound to us, else a typed refusal. Handles every reservation state fail-closed."""
        from . import bulk as bulk_mod, procident, reslock
        ident = procident.proc_identity(os.getpid()) or {}
        if not procident.identity_complete(ident):
            return "bulk run refused: process identity incomplete"
        try:
            with reslock.operation_lock(self._paths, "bulk-start", "install-all", ""):
                rstate, res = bulk_mod.read_reservation(self._paths)
                if rstate == "unsafe":
                    return ("the bulk-start reservation is unreadable or malformed — "
                            "acknowledge (recover) it before starting a new run")
                if rstate == "valid":
                    if res.get("run_id") != run_id:
                        if procident.identity_matches(res.get("ident", {}),
                                                      res.get("pid", -1)):
                            return "a bulk run is already reserved/in progress"
                        return ("a previous bulk start died holding its reservation — "
                                "acknowledge (recover) it before starting a new run")
                    # OUR run_id: the slot must be in phase `spawned` and bound to
                    # EXACTLY THIS process — a foreign or stale reservation is never
                    # overwritten by a claim.
                    if res.get("phase") != "spawned":
                        return ("the bulk-start reservation is not in the spawned phase "
                                "— refusing to claim (stale or foreign slot)")
                    if not (res.get("pid") == os.getpid()
                            and procident.identity_matches(res.get("ident", {}),
                                                           os.getpid())):
                        return ("the bulk-start reservation is bound to a different "
                                "process — refusing to claim a foreign slot")
                    if not bulk_mod.bind_reservation(self._paths, run_id,
                                                     os.getpid(), ident, "claimed"):
                        return ("the bulk-start reservation could not be claimed — "
                                "refusing to run unbound")
                    return ""
                # absent -> manual CLI start: gate, then create our own reservation
                gate = self._bulk_gate()
                if gate:
                    return f"Refusing to start the bulk run: {gate}"
                ok, why = bulk_mod.write_reservation(self._paths, run_id,
                                                     os.getpid(), ident,
                                                     phase="claimed")
                return "" if ok else f"bulk run refused: {why}"
        except reslock.ResourceBusy:
            return "a bulk start is already in progress (start lock contended)"

    def bulk_recovery_reason(self) -> str:
        """SAFE-SIDE recovery signal for GET rendering: the typed reason acknowledgement
        is required — derived from DEAD/UNSAFE reservation or lease evidence and from
        unsafe/interrupted run markers, EVEN when the run marker is absent or terminal.
        "" when nothing blocks. File + /proc reads only; never mutates."""
        gate = self._bulk_gate()
        if gate and "acknowledge" in gate:
            return gate
        return ""

    def bulk_log_chunk(self, run_id: str, offset: int) -> dict:
        """Byte-capped, cursor-based read of the primary run log for the run view. The
        filename is derived EXCLUSIVELY from the validated run_id (marker log fields are
        never opened); offsets are bounded non-negative ints. File-only, no-follow.

        FULLY FAIL-CLOSED (never raises through a GET route): path CONSTRUCTION (`under`
        can raise PathContainmentError when `logs/` is an escaping symlink), the no-follow
        parent walk, the O_NOFOLLOW open, and fstat/lseek/read are ALL guarded, and the
        whole body is wrapped as a backstop. An escaping/symlinked/non-regular/unreadable
        log yields bounded safe `error` data — the external target is never followed or
        read. Both /install-all and /api/install-all stay GET-safe (HTTP 200)."""
        import stat as stat_mod
        from . import bulk as bulk_mod
        try:
            try:
                name = bulk_mod.log_name_for(run_id) + ".log"
            except ValueError:
                return {"error": "invalid run id", "offset": 0, "data": ""}
            if not isinstance(offset, int) or offset < 0 or offset > (1 << 40):
                return {"error": "invalid offset", "offset": 0, "data": ""}
            fd = -1
            try:
                # Path CONSTRUCTION is inside the guard: `under` raises
                # PathContainmentError for an escaping/symlinked `logs/` parent.
                path = self._paths.under("logs", name)
                with runtime_fs._walk_parent(self._paths, path, create=False) as (pfd, leaf):
                    fd = os.open(leaf, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=pfd)
            except FileNotFoundError:
                return {"offset": 0, "data": "", "size": 0}
            except (OSError, PathContainmentError, ValueError) as exc:
                return {"error": f"log unreadable ({exc})", "offset": 0, "data": ""}
            try:
                stt = os.fstat(fd)
                if not stat_mod.S_ISREG(stt.st_mode):
                    return {"error": "log is not a regular file",
                            "offset": 0, "data": ""}
                size = stt.st_size
                if offset > size:
                    offset = 0                   # truncated/new run: client restarts
                os.lseek(fd, offset, os.SEEK_SET)
                data = os.read(fd, 64 * 1024)    # byte cap per poll
                return {"offset": offset + len(data),
                        "data": data.decode("utf-8", "replace"), "size": size}
            except OSError as exc:
                return {"error": f"log unreadable ({exc})", "offset": 0, "data": ""}
            finally:
                if fd >= 0:
                    try:
                        os.close(fd)
                    except OSError:
                        pass
        except Exception:                        # noqa: BLE001 — a GET must never 500
            return {"error": "run log temporarily unavailable", "offset": 0, "data": ""}

    def _bulk_component_log_list(self, st) -> list:
        """The run's ordered (title, filename) component build/test logs read DIRECTLY
        from the marker's DURABLE, run-owned `component_logs` — recorded in exact creation
        order as each log was about to be written under a RUN-SPECIFIC name. Membership and
        order come ONLY from this list; there is NO mtime/timestamp/glob/manifest inference,
        so a prior run's generic log can never appear, the list is append-only (a new log
        only ever extends the end), and identical timestamps are irrelevant. Fail-closed:
        `bulk.component_logs` validates each entry's run-id-bound filename and SKIPS (never
        raises on) any malformed/foreign one — the browser never influences this list."""
        from . import bulk as bulk_mod
        return bulk_mod.component_logs(st)

    @staticmethod
    def _bulk_log_frame(title: str, path: str) -> str:
        """The optical separator between streamed logs: an ASCII frame naming the
        component/log and its path."""
        width = 74
        def row(text: str) -> str:
            return "| " + text[:width - 4].ljust(width - 4) + " |"
        bar = "+" + "=" * (width - 2) + "+"
        return f"\n{bar}\n{row(title)}\n{row(path)}\n{bar}\n"

    def _read_named_log_chunk(self, fname: str, offset: int, cap: int) -> tuple:
        """Descriptor-safe, O_NOFOLLOW, byte-capped read of logs/<fname> from offset:
        returns (raw_byte_count, text, size); (-1, "", 0) when unreadable. FAIL-CLOSED:
        path CONSTRUCTION (`under` can raise PathContainmentError), the no-follow parent
        walk, and open/stat/read are ALL inside the guard; `fname` must additionally be a
        single safe leaf. A symlinked/escaping/malformed logs parent or leaf yields the
        unreadable sentinel — it is never followed and never raised to the caller."""
        import stat as stat_mod
        fd = -1
        try:
            # Defense-in-depth: even though marker entries are already run-id-validated,
            # never build a path from a name with separators/`..`/NULs.
            validators.path_component(fname, field="component log")
            path = self._paths.under("logs", fname)
            with runtime_fs._walk_parent(self._paths, path, create=False) as (pfd, leaf):
                fd = os.open(leaf, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=pfd)
        except FileNotFoundError:
            # ABSENT: the log leaf does not exist YET. A bulk component's step logs are
            # registered in the marker before they are created (created one at a time as
            # the build runs), so an absent leaf is a FUTURE log — distinct from an unsafe
            # one, and the stream must WAIT at it, never frame or advance past it.
            return (-2, "", 0)
        except (OSError, PathContainmentError, ValueError, validators.ValidationError):
            return (-1, "", 0)                    # UNSAFE: present but symlink/non-regular/escaping
        try:
            stt = os.fstat(fd)
            if not stat_mod.S_ISREG(stt.st_mode):
                return (-1, "", 0)
            size = stt.st_size
            if offset > size:
                return (0, "", size)
            os.lseek(fd, offset, os.SEEK_SET)
            data = os.read(fd, cap)
            return (len(data), data.decode("utf-8", "replace"), size)
        except OSError:
            return (-1, "", 0)
        finally:
            if fd >= 0:
                try:
                    os.close(fd)
                except OSError:
                    pass

    def bulk_component_log_chunk(self, run_id: str, index: int, offset: int) -> dict:
        """LIVE sequential stream over the run's DURABLE, run-owned component build/test
        logs (from the marker, run-id-bound): cursor = (index, byte offset) into that
        ordered list; each log begins with its ASCII-framed title, and a DRAINED log
        advances to the next. Stateless, GET-safe, byte-capped, NO mutation, NO network.

        FAIL-CLOSED (never raises through a GET route): the whole body is wrapped; an
        UNREADABLE log leaf (symlinked/malformed/escaping) is framed once with a bounded
        '[log unavailable — unsafe or unreadable]' notice and skipped if a successor
        exists, else surfaced as an explicit safe `error` — the browser is never given a
        500 and no unsafe evidence is followed or trusted."""
        try:
            st = self.bulk_status()
            if (not st or st.get("unsafe") or st.get("run_id") != run_id
                    or not isinstance(index, int) or index < 0 or index > 4096
                    or not isinstance(offset, int) or offset < 0 or offset > (1 << 40)):
                return {"index": 0, "offset": 0, "data": ""}
            logs = self._bulk_component_log_list(st)
            parts = []
            error = ""
            budget = 512 * 1024                  # keep up with verbose builds (PIO)
            hops = 0
            while index < len(logs) and budget > 0 and hops < 8:
                hops += 1
                title, fname = logs[index]
                nbytes, text, size = self._read_named_log_chunk(fname, offset, budget)
                if nbytes == -2:
                    # ABSENT: this log's step has not run yet — the live frontier. WAIT
                    # here (no frame, no advance); it is framed with its first bytes once
                    # created. This is what stops (a) re-framing the last registered step
                    # every poll and (b) skipping earlier steps before their content exists.
                    break
                if nbytes == -1:
                    # UNSAFE leaf (present but symlink/non-regular/escaping): frame a
                    # bounded notice ONCE, then advance past it if a successor exists
                    # (never stall, never follow it); no successor -> explicit safe error.
                    if offset == 0:
                        parts.append(self._bulk_log_frame(
                            title, f"logs/{fname} — [log unavailable — unsafe or "
                                   f"unreadable]"))
                    if index < len(logs) - 1:
                        index += 1
                        offset = 0
                        continue
                    error = "a component log is unavailable (unsafe or unreadable)"
                    break
                if nbytes:
                    # The frame is emitted EXACTLY ONCE per file — with its first bytes
                    # (never for a still-empty live tail, which would re-frame each poll).
                    if offset == 0:
                        parts.append(self._bulk_log_frame(title, f"logs/{fname}"))
                    parts.append(text)
                    offset += nbytes
                    budget -= nbytes
                    continue                     # maybe more of THIS file next loop
                # DRAINED (nbytes == 0, at EOF). Advance to the next log ONLY once the
                # successor actually EXISTS — because logs are created sequentially, a
                # created successor proves THIS step finished. If the successor is still
                # absent, THIS file is the live frontier: wait for more of it rather than
                # advancing past a step that may still be producing output.
                if offset >= size and index < len(logs) - 1:
                    succ_present = self._read_named_log_chunk(
                        logs[index + 1][1], 0, 1)[0] != -2
                    if succ_present:
                        if offset == 0:
                            # A COMPLETE empty file: frame it once while passing over it.
                            parts.append(self._bulk_log_frame(title, f"logs/{fname}"))
                        index += 1               # drained and a successor exists
                        offset = 0
                        continue
                break                            # live tail / frontier: wait for more bytes
            out = {"index": index, "offset": offset, "data": "".join(parts)}
            if error:
                out["error"] = error
            return out
        except Exception:                        # noqa: BLE001 — a GET must never 500
            return {"index": 0, "offset": 0, "data": "",
                    "error": "component-log stream temporarily unavailable"}

    # Keep in sync with COMPLOG_MAX in static/bulk.js (the live window's scrollback cap, 1.5 MB):
    # a historical seed must not be larger than what the live view would keep.
    _COMPLOG_SEED_MAX_BYTES = 1_500_000
    _COMPLOG_SEED_MAX_READS = 512            # hard iteration bound (normal runs drain in <10 reads)

    def bulk_component_log_seed(self, run_id: str) -> str:
        """SERVER-SIDE seed of the historical component-log window (the '#bulk-complog' second
        window): a bounded DRAIN of the live `bulk_component_log_chunk` cursor API for a FINISHED
        run, so it inherits that method's run-id validation, safe no-follow reads, ASCII framing and
        unsafe-leaf handling (it never opens component logs / paths itself). Terminates when the
        cursor stops advancing (the chunk API exposes no explicit done flag — for a terminal run this
        coincides with empty data) or on a returned `error`. Hard-bounded by BOTH a byte cap and a
        read-count cap; front-trims with a visible notice on overflow. Fail-closed: returns "" (or the
        framed diagnostic the chunk API already produced) — never raises through a GET."""
        parts: list[str] = []
        total = 0
        index, offset = 0, 0
        truncated_reads = False
        try:
            for _ in range(self._COMPLOG_SEED_MAX_READS):
                chunk = self.bulk_component_log_chunk(run_id, index, offset)
                data = chunk.get("data", "")
                if data:
                    parts.append(data)
                    total += len(data)
                ni, no = chunk.get("index", index), chunk.get("offset", offset)
                if chunk.get("error"):
                    break                                   # diagnostic already in `data`; stop
                if ni == index and no == offset:            # cursor did not advance -> drained
                    break
                index, offset = ni, no
                if total >= self._COMPLOG_SEED_MAX_BYTES:
                    break
            else:
                truncated_reads = True                      # exhausted the read cap without draining
        except Exception:                                   # noqa: BLE001 — a GET must never 500
            return "".join(parts)
        seed = "".join(parts)
        if len(seed) > self._COMPLOG_SEED_MAX_BYTES:        # front-trim, keep the tail (matches bulk.js)
            keep = self._COMPLOG_SEED_MAX_BYTES - 200_000
            cut = len(seed) - keep
            nl = seed.find("\n", cut)
            seed = "[… older output trimmed …]\n" + seed[(nl + 1) if nl >= 0 else cut:]
        if truncated_reads:
            seed += "\n[… stream truncated (read cap) …]\n"
        return seed

    def bulk_ack(self, confirm_orphan: bool = False) -> ActionResult:
        """EXPLICIT recovery/acknowledgement of dead/unsafe bulk state, SERIALIZED with
        launches: the dedicated bulk-start lock is held from the liveness re-validation
        of reservation/lease/marker/job through the archival of every bulk runtime leaf
        (LOCK ORDER: bulk-start -> source-txn index; no code path acquires them in the
        reverse order). A start racing this either completed first — then the LIVE
        reservation/lease makes this refuse — or waits on the lock and starts fresh
        afterwards. A live run's evidence is NEVER archived."""
        from . import bulk as bulk_mod, procident, reslock
        inst = self._installer()
        try:
            with reslock.operation_lock(self._paths, "bulk-start", "bulk-ack", ""):
                lstate, lease = bulk_mod.read_lease(self._paths)
                if lstate == "valid" and procident.identity_matches(
                        lease.get("ident", {}), lease.get("pid", -1)):
                    return ActionResult(False, "Cannot acknowledge: the bulk run is "
                                        "still alive.")
                rstate, res = bulk_mod.read_reservation(self._paths)
                needs_confirm = rstate == "valid" and (
                    res.get("phase") == "orphan-risk"
                    or (res.get("phase") == "spawning"
                        and res.get("child") != "none"))
                if needs_confirm and not confirm_orphan:
                    return ActionResult(
                        False, "Cannot acknowledge automatically: a spawned child's "
                        "termination was never proven (ORPHAN RISK"
                        + (f", pid {res.get('pid')}" if res.get("pid", 0) > 1 else "")
                        + "). Inspect/terminate the process manually, then acknowledge "
                        "WITH the explicit confirmation.")
                if rstate == "valid" \
                        and res.get("phase") not in ("orphan-risk", "spawning") \
                        and procident.identity_matches(
                        res.get("ident", {}), res.get("pid", -1)):
                    return ActionResult(False, "Cannot acknowledge: the bulk start is "
                                        "still alive (reservation held by a live "
                                        "process).")
                st = self.bulk_status()
                if st and not st.get("unsafe") and st["state"] in ("preparing",
                                                                   "running"):
                    return ActionResult(False, "Cannot acknowledge: the bulk run is in "
                                        "progress.")
                with reslock.operation_lock(self._paths, inst._index_key(),
                                            "bulk-ack", ""):
                    inst._recover_scan()
                    if inst._pending_journals():
                        return ActionResult(False, "Cannot acknowledge: an unresolved "
                                            "source transaction journal exists — "
                                            "resolve it first (see lhpc status).")
                    ok1, d1 = bulk_mod.archive(self._paths, bulk_mod.MARKER, "run")
                    ok2, d2 = bulk_mod.archive(self._paths, bulk_mod.LEASE, "lease")
                    ok3, d3 = bulk_mod.archive(self._paths, bulk_mod.RESERVATION,
                                               "start")
        except reslock.ResourceBusy as busy:
            return ActionResult(False, f"Cannot acknowledge: {busy}")
        ok = ok1 and ok2 and ok3
        return ActionResult(ok, "Bulk run state acknowledged and archived." if ok else
                            "Acknowledgement INCOMPLETE.",
                            details=[f"  marker: {d1}", f"  lease: {d2}",
                                     f"  reservation: {d3}"])

    def spawn_bulk_job(self, source: str, tests: bool, tx: bool) -> tuple:
        """Spawn the detached bulk driver (`python -u -m lhpc install-all …`) with an
        identity-tracked job marker. Returns (log_name, error)."""
        from . import bulk as bulk_mod
        if source not in self.SOURCE_CHOICES:
            return None, f"unknown source choice {source!r}"
        if tx and not tests:
            return None, "the TX test requires host tests to be enabled"
        if tx and not getattr(self.config().operator, "callsign", ""):
            return None, ("TX requested but no operator callsign is configured — set it "
                          "in Settings before a transmitting run")
        if not self._paths.runtime_root_exists:
            return None, ("Runtime root is not bootstrapped yet. "
                          "Run 'lhpc bootstrap' first.")
        from . import procident, reslock
        # ONE cross-process bulk-start critical section: gate -> reservation (no-clobber,
        # run_id-bound) -> spawn -> job claim, all under the dedicated bulk-start lock. A
        # second concurrent POST/CLI start is refused typed BEFORE it can spawn a child.
        try:
            with reslock.operation_lock(self._paths, "bulk-start", "install-all", ""):
                gate = self._bulk_gate()
                if gate:
                    return None, gate
                run_id = uuid.uuid4().hex
                ident = procident.proc_identity(os.getpid()) or {}
                if not procident.identity_complete(ident):
                    return None, "bulk start refused: process identity incomplete"
                ok, why = bulk_mod.write_reservation(self._paths, run_id,
                                                     os.getpid(), ident,
                                                     phase="spawning")
                if not ok:
                    return None, f"bulk start refused: {why}"
                argv = [sys.executable, "-u", "-m", "lhpc", "install-all", "--yes",
                        "--source", source, "--run-id", run_id]
                if not tests:
                    argv.append("--no-tests")
                if tx:
                    argv.append("--tx")
                # EXCEPTION-SAFE SETTLEMENT: from here, EVERY outcome — including
                # ordinary exceptions from spawn, identity capture, rebinding, tracking,
                # orphan-risk persistence, or clearing — settles the slot into exactly
                # one durable state before the lock releases: bound to the child,
                # safely removed, or a recovery-required record. A residual `spawning`
                # record is NEVER a live web-server-owned run.

                def settle_gone(msg: str) -> str:
                    """No child was created, or its cessation is identity-PROVEN."""
                    if bulk_mod.clear_reservation(self._paths):
                        return msg
                    if bulk_mod.mark_reservation_child(self._paths, run_id,
                                                       os.getpid(), ident, "none"):
                        return (msg + " — the reservation could not be removed; "
                                "acknowledge (recover) it before the next run")
                    return (msg + " — the reservation could not be removed or marked; "
                            "acknowledge (recover) with the confirmation")

                def settle_unproven(pid0, cident, msg: str) -> str:
                    """A child may exist and cessation is UNPROVEN: durable orphan-risk
                    evidence (child identity where available); if even that cannot be
                    persisted, the residual `spawning`+uncertain record itself is the
                    mutation-blocking evidence."""
                    if not bulk_mod.write_orphan_risk(
                            self._paths, run_id, pid0 or 0,
                            msg, cident):
                        return (msg + " — ORPHAN RISK; the orphan-risk record could "
                                "not be persisted either; the residual reservation "
                                "blocks new runs; acknowledge (recover) with the "
                                "confirmation")
                    return (msg + " — ORPHAN RISK; new bulk runs stay blocked; "
                            "inspect/terminate the process, then acknowledge "
                            "(recover) with the confirmation")

                pid = None
                child_ident = None
                try:
                    if not bulk_mod.mark_reservation_child(self._paths, run_id,
                                                           os.getpid(), ident,
                                                           "uncertain"):
                        # cannot durably record spawn INTENT -> do not spawn at all
                        return None, settle_gone(
                            "bulk start refused: spawn intent could not be recorded")
                    life = self._lifecycle()
                    ln, pid = life.spawn_job(bulk_mod.log_name_for(run_id), argv,
                                             str(self._paths.runtime_root))
                    if ln is None:
                        pid = None
                        return None, settle_gone("could not spawn the bulk run "
                                                 "(see logs)")
                    child_ident = procident.proc_identity(pid)
                    bound = (bool(child_ident)
                             and procident.identity_complete(child_ident)
                             and bulk_mod.bind_reservation(self._paths, run_id, pid,
                                                           child_ident, "spawned"))
                    if bound:
                        err = self._track_or_terminate(life, ln, pid, "all",
                                                       self.BULK_OP)
                        if not err:
                            return ln, None
                        if "ORPHAN RISK" in err:
                            return None, settle_unproven(
                                pid, child_ident,
                                "job tracking failed and cessation is unproven")
                        return None, settle_gone(err)
                    # identity capture or bind failed: SIGTERM-ONLY containment via the
                    # identity-verified primitive (never a signal to an unproven pid,
                    # never SIGKILL); cessation is either PROVEN or truthfully not.
                    if life._terminate_unobserved(pid, child_ident):
                        return None, settle_gone(
                            "spawned bulk run could not be identity-bound — SIGTERM "
                            "sent and child exit PROVEN")
                    return None, settle_unproven(
                        pid, child_ident,
                        "child identity could not be captured/bound after spawn and "
                        f"cessation is unproven (pid {pid})")
                except Exception as exc:            # noqa: BLE001 — settlement boundary
                    if pid is None:
                        return None, settle_gone(
                            f"bulk start failed before any child existed ({exc})")
                    proven = False
                    try:
                        proven = life._terminate_unobserved(pid, child_ident)
                    except Exception:               # noqa: BLE001
                        proven = False
                    if proven:
                        return None, settle_gone(
                            f"bulk start failed ({exc}) — SIGTERM sent and child "
                            "exit PROVEN")
                    return None, settle_unproven(
                        pid, child_ident,
                        f"bulk start failed ({exc}) and child cessation is unproven "
                        f"(pid {pid})")
        except reslock.ResourceBusy:
            return None, "a bulk start is already in progress"

    def install_all(self, source: str = "pinned", tests: bool = True, tx: bool = False,
                    run_id: str = "", apply: bool = False, emit=print) -> ActionResult:
        """THE bulk driver ("Install and Build all Stacks"): one outer bulk boundary
        (config-stable + all source locks + durable lease), one immutable global plan,
        per-source-group reconciliation, dependency-aware continuation, durable run
        marker at every transition (a write failure STOPS the run), disclosed TX phase.
        stdout (`emit`) is the narrative log."""
        from . import bulk as bulk_mod, reslock
        if source not in self.SOURCE_CHOICES:
            return ActionResult(False, f"Unknown source choice {source!r}.")
        if tx and not tests:
            return ActionResult(False, "Refusing: the TX test requires host tests to be "
                                "enabled (--tx without --no-tests).")
        if run_id and not bulk_mod.RUN_ID_RE.match(run_id):
            return ActionResult(False, "Refusing: invalid --run-id (32 lowercase hex).")
        scope = self._bulk_scope()
        if not scope:
            return ActionResult(False, "No stacks with managed sources in the manifest.")
        if not apply:
            details = [f"  [{self.bulk_mode()}] {st.id}: "
                       f"{', '.join(c.id for c in comps)}" for st, comps in scope]
            details.append(f"  host tests: {'on' if tests else 'off'}; "
                           f"TX test: {'ON (real RF!)' if tx else 'off'}; "
                           f"source: {source}")
            if not self._paths.runtime_root_exists:
                details.append("  NOTE: runtime root is not bootstrapped yet — apply "
                               "requires 'lhpc bootstrap' first")
            return ActionResult(True, f"Bulk install/update plan: {len(scope)} stack(s) "
                                "in dependency order. This can take several minutes.",
                                details=details, data={"changes": len(scope)},
                                next_commands=["lhpc install-all --yes"])
        if not self._paths.runtime_root_exists:
            # BEFORE any reservation/lease/marker/source/log/job mutation.
            return self._bulk_bootstrap_refusal()
        run_id = run_id or uuid.uuid4().hex
        claim_err = self._bulk_claim(run_id)
        if claim_err:
            return ActionResult(False, claim_err if claim_err.startswith("Refusing")
                                else f"Refusing to start the bulk run: {claim_err}")
        self._lock_state.bulk_cleanup_failed = ""
        res = None
        try:
            if tx and not getattr(self.config().operator, "callsign", ""):
                # EARLY, NON-MUTATING: no boundary, no running marker, no source action —
                # only the short-lived launch reservation, released by the finally below.
                res = ActionResult(False, "Refusing the TX-enabled bulk run: no operator "
                                   "callsign is configured — set it in Settings first.")
            else:
                res = self._install_all_claimed(scope, source, tests, tx, run_id, emit)
        finally:
            # ONE converging cleanup path for EVERY claimed exit — pre-boundary refusals,
            # plan conflicts, post-lock refusals, marker-write aborts, lock contention,
            # and exceptions alike. A failed reservation/lease clear is never silent.
            failed = getattr(self._lock_state, "bulk_cleanup_failed", "")
            if not failed:
                if not bulk_mod.clear_reservation(self._paths):
                    failed = "bulk-start reservation"
                    self._lock_state.bulk_cleanup_failed = failed
        failed = getattr(self._lock_state, "bulk_cleanup_failed", "")
        if failed:
            detail = (f"bulk cleanup INCOMPLETE ({failed} could not be cleared) — "
                      "the next run is blocked until you acknowledge (recover)")
            # best-effort SAFE-SIDE marker downgrade; status stays safe-side via the
            # lease/reservation evidence even if this final rewrite also fails.
            mstate, m = bulk_mod.read_marker(self._paths)
            if mstate == "valid" and m.get("state") in ("completed",
                                                        "completed-with-failures"):
                m["state"] = "completed-with-failures"
                m["error"] = (m.get("error", "") + " " + detail).strip()
                bulk_mod.write_marker(self._paths, m)
            base = res.summary if res is not None else "Bulk run did not complete."
            return ActionResult(False, f"{base} {detail}",
                                details=list(res.details) if res is not None else [],
                                next_commands=["lhpc status"])
        return res

    def _install_all_claimed(self, scope, source, tests, tx, run_id, emit) -> ActionResult:
        from . import bulk as bulk_mod, reslock
        # cheap pre-lock preflight (typed early refusal; authoritative recheck post-lock)
        pre_running = self._bulk_running_components(scope)
        if pre_running:
            return self._bulk_running_refusal(pre_running)
        stacks_ids = [st.id for st, _ in scope]
        all_paths = sorted({c.source.path for _, comps in scope for c in comps})

        class _Abort(Exception):
            pass

        marker = None

        def bw() -> None:
            if not bulk_mod.write_marker(self._paths, marker):
                emit("FATAL: run marker could not be persisted — stopping (no work "
                     "without durable progress evidence)")
                raise _Abort()

        def register_log(title: str, log: str) -> None:
            # DURABLE, append-only registration of a component build/test log the run is
            # ABOUT to create — persisted (bw) before the file exists under its
            # run-specific name, so the live stream only ever shows this run's own logs.
            if bulk_mod.is_component_log_for(run_id, log):
                marker["component_logs"].append({"title": title, "log": log})
                bw()
        try:
            with self._bulk_boundary(run_id, stacks_ids, all_paths) as ctx:
                # AUTHORITATIVE post-lock stopped recheck: zero mutation on refusal
                # (no run marker either — nothing was started).
                running = self._bulk_running_components(scope)
                if running:
                    return self._bulk_running_refusal(running)
                # own job marker (manual CLI runs; web spawns already tracked this pid)
                job = bulk_mod.log_name_for(run_id) + ".log"
                if not self.log_running("all", job=job):
                    if not self._write_job_marker(job, os.getpid(), "all", self.BULK_OP):
                        return ActionResult(False, "Refusing: the bulk run could not be "
                                            "identity-tracked (job marker not persisted).")
                # ONE immutable global plan (frozen selectors/remotes) + reconciliation —
                # conflicts refuse BEFORE any marker/candidate/source mutation.
                items = [(st, c) for st, comps in scope for c in comps]
                groups, conflicts = self._plan_source_groups(items, source, freeze=True)
                if conflicts:
                    return ActionResult(False, "Refusing the bulk run: incompatible "
                                        "source resolutions for a shared checkout.",
                                        details=[f"  {c}" for c in conflicts])
                plan = {}                        # path -> (action, reason, comp, resolved)
                for path, comp, resolved in groups:
                    action, reason = self._reconcile_group(path, comp)
                    plan[path] = (action, reason, comp, resolved)
                # STRICT TX ADMISSION GATE (tx=True): validated after the boundary +
                # immutable plan, BEFORE any candidate/install/update/build/test. The
                # run itself proceeds; an inadmissible TX is refused HERE — durable,
                # actionable, and terminal-truthful (completed-with-failures).
                tx_refused = ""
                if tx:
                    dstack = next(((st, comps) for st, comps in scope
                                   if st.id == "daemon"), None)
                    if not getattr(self.config().operator, "callsign", ""):
                        tx_refused = ("no operator callsign is configured — set it in "
                                      "Settings")
                    elif dstack is None:
                        tx_refused = "the daemon stack is not part of this run"
                    else:
                        blocked = [f"{c.source.path}: {plan[c.source.path][1]}"
                                   for c in dstack[1]
                                   if plan[c.source.path][0] == "blocked"]
                        if blocked:
                            tx_refused = ("the daemon source group is blocked — "
                                          + "; ".join(blocked))
                        elif not any(c.build_steps for c in dstack[1]):
                            tx_refused = "the daemon has no host build planned"
                        elif not any(c.test_argv for c in dstack[1]):
                            tx_refused = "the daemon has no host test planned"
                mode = self.bulk_mode()
                mode = {"mixed": "mixed"}.get(mode, mode)
                rows = [{"id": st.id, "name": st.name,
                         "op": "+".join(sorted({plan[c.source.path][0] for c in comps}))}
                        for st, comps in scope]
                marker = bulk_mod.new_marker(run_id, mode, source, tests, tx, rows)
                if tx_refused:
                    marker["tx_phase"] = {"status": "fail",
                                          "detail": f"TX refused before source work: "
                                                    f"{tx_refused}"}
                    drow0 = next((r0 for r0 in marker["stacks"]
                                  if r0["id"] == "daemon"), None)
                    if drow0 is not None:
                        drow0["tx"] = {"ran": False, "ok": False,
                                       "detail": f"refused: {tx_refused}"}
                    emit(f"==== TX REFUSED before source work: {tx_refused} ====")
                bw()                             # 'preparing' BEFORE the first mutation
                marker["state"] = "running"
                bw()
                row = {r["id"]: r for r in marker["stacks"]}
                _, edges = self._bulk_scope_edges()
                processed: dict = {}             # path -> (ok, detail)
                failed_stacks: set = set()
                mutated: list = []
                inst = self._installer()
                for st, comps in scope:
                    r = row[st.id]
                    bad_deps = sorted(edges.get(st.id, set()) & failed_stacks)
                    if bad_deps:
                        r["status"] = "blocked"
                        r["detail"] = f"dependency failed: {', '.join(bad_deps)}"
                        failed_stacks.add(st.id)
                        emit(f"==== {st.id}: BLOCKED ({r['detail']}) ====")
                        bw()
                        continue
                    emit(f"==== {st.id}: sources ====")
                    r["status"] = "downloading"
                    bw()
                    ok = True
                    for c in comps:
                        path = c.source.path
                        if path not in processed:
                            action, reason, comp, resolved = plan[path]
                            if action == "blocked":
                                processed[path] = (False, f"blocked: {reason}")
                            else:
                                a = self._adopt_dev_fallback(
                                    inst, st, comp, source, resolved,
                                    force=(action == "update"), locked=True)
                                emit(f"  [{a.status}] {path}: {a.detail}")
                                # every non-failed adopt outcome is OK: done (mutated),
                                # exists (already healthy), skipped (benign no-op, e.g.
                                # a linked dev tree left as-is) — only "failed" fails.
                                processed[path] = (a.status != "failed",
                                                   f"{action}: {a.detail}")
                                if a.status == "done" and action == "update":
                                    mutated.append(path)
                        p_ok, p_detail = processed[path]
                        if not p_ok:
                            ok = False
                            r["detail"] = p_detail
                    if not ok:
                        r["status"] = ("blocked"
                                       if r["detail"].startswith("blocked:") else "fail")
                        failed_stacks.add(st.id)
                        bw()
                        continue
                    missing = self.missing_system_deps(st.id)
                    if missing:
                        cmds = "; ".join(sorted({m.get("install", "") for m in missing
                                                 if m.get("install")}))
                        r["status"] = "blocked"
                        r["detail"] = f"missing system deps — run: {cmds or 'see doctor'}"
                        failed_stacks.add(st.id)
                        emit(f"  [blocked] {st.id}: {r['detail']}")
                        bw()
                        continue
                    # LINKED external trees: adoption may be a truthful no-op, but a
                    # linked stack with DECLARED build/test work that bulk intentionally
                    # refuses to execute is NOT a success — the row is blocked and the
                    # run cannot end fully `completed`.
                    linked_with_work = [c.id for c in comps
                                        if (c.source.strategy or "") == "link"
                                        and (c.build_steps or c.test_argv)]
                    if linked_with_work:
                        r["status"] = "blocked"
                        r["detail"] = ("sources linked ✓ — linked external tree: "
                                       "build/test must be performed in that checkout "
                                       f"({', '.join(linked_with_work)}); deliberate "
                                       "skip, LHPC never writes into your dev trees")
                        r["tests"] = {"ran": False, "ok": None,
                                      "detail": "skipped (linked source)"}
                        failed_stacks.add(st.id)
                        emit(f"  [blocked] {st.id}: {r['detail']}")
                        bw()
                        continue
                    linked = [c.id for c in comps
                              if (c.source.strategy or "") == "link"]
                    buildable = [c for c in comps if c.build_steps
                                 and (c.source.strategy or "") != "link"]
                    if buildable:
                        emit(f"==== {st.id}: build ====")
                        r["status"] = "building"
                        bw()
                        b = self.build(st.id, apply=True, bulk_ctx=ctx,
                                       on_component_log=register_log)
                        for line in b.details:
                            emit(line)
                        if not b.ok:
                            r["status"], r["detail"] = "fail", b.summary
                            failed_stacks.add(st.id)
                            bw()
                            continue
                    elif linked:
                        r["detail"] = ("linked external tree — LHPC never builds/tests "
                                       "into it (build it in that checkout)")
                    testable = [c for c in comps if c.test_argv
                                and (c.source.strategy or "") != "link"]
                    # Integration tests that need the stack RUNNING can't run in a build sweep
                    # (nothing is started) — they are DEFERRED, never failed, here.
                    auto = [c for c in testable if not c.test_requires_running]
                    deferred = len(testable) - len(auto)
                    if tests and testable:
                        emit(f"==== {st.id}: host tests ====")
                        r["status"] = "testing"
                        bw()
                        t = self.test(st.id, tx=False, apply=True, bulk_ctx=ctx,
                                      on_component_log=register_log)   # runs `auto`, defers the rest
                        for line in t.details:
                            emit(line)
                        if auto:
                            detail = "passed" if t.ok else "FAILED"
                            if deferred:
                                detail += (f"; {deferred} deferred (run `lhpc test {st.id}` "
                                           "with it started)")
                            r["tests"] = {"ran": True, "ok": bool(t.ok), "detail": detail}
                            if not t.ok:
                                r["status"], r["detail"] = "fail", t.summary
                                failed_stacks.add(st.id)
                                bw()
                                continue
                        else:   # only integration tests -> deferred, NOT "no host tests"
                            r["tests"] = {"ran": False, "ok": None,
                                          "detail": (f"deferred — {deferred} test(s) need the "
                                                     f"running stack (run `lhpc test {st.id}` "
                                                     "after starting it)")}
                    else:
                        r["tests"] = {"ran": False, "ok": None,
                                      "detail": ("skipped (tests disabled)" if not tests
                                                 else "skipped (no host tests)")}
                    r["status"] = "success"
                    bw()
                # candidate retirement for updated groups BEFORE the boundary releases
                extra: list = []
                if not self._retire_candidates_for_paths(mutated, extra):
                    for line in extra:
                        emit(line)
                    marker["error"] = "candidate-marker cleanup incomplete"
                # DISCLOSED TX phase (the only start this run performs) — ELIGIBLE
                # only when not already refused at admission, the daemon row is
                # `success`, its required host test PASSED, and required cleanup is
                # complete. Otherwise: no daemon start, no transmission — a truthful
                # refusal with an actionable detail.
                if tx and marker["tx_phase"]["status"] == "pending":
                    drow = next((r0 for r0 in marker["stacks"]
                                 if r0["id"] == "daemon"), None)
                    reason = ""
                    if drow is None or drow["status"] != "success":
                        reason = ("the daemon stack did not complete successfully "
                                  f"({(drow or {}).get('status', 'missing')}: "
                                  f"{(drow or {}).get('detail', '')})".strip())
                    elif not (drow["tests"].get("ran") and drow["tests"].get("ok")):
                        reason = ("the daemon host test did not pass "
                                  f"({drow['tests'].get('detail', 'not run')})")
                    elif marker["error"]:
                        reason = f"required cleanup incomplete ({marker['error']})"
                    if reason:
                        marker["tx_phase"] = {"status": "fail",
                                              "detail": "TX refused (no daemon start, "
                                                        f"no transmission): {reason}"}
                        if drow is not None:
                            drow["tx"] = {"ran": False, "ok": False,
                                          "detail": f"refused: {reason}"}
                            # the row is NEVER `success` while requested TX was refused;
                            # host build/test evidence stays intact in the tests field.
                            if drow["status"] == "success":
                                drow["status"] = "fail"
                                drow["detail"] = f"requested TX was refused: {reason}"
                        emit(f"==== TX REFUSED: {reason} ====")
                        bw()
                    else:
                        self._bulk_tx_phase(marker, ctx, emit, bw)
                elif tx and marker["tx_phase"]["status"] == "fail":
                    # TX was refused at ADMISSION (before source work): if the daemon
                    # nevertheless completed its host work successfully, the row must
                    # still not read `success` — flip it with the actionable detail,
                    # preserving the separate host-test evidence.
                    drow = next((r0 for r0 in marker["stacks"]
                                 if r0["id"] == "daemon"), None)
                    if drow is not None and drow["status"] == "success":
                        drow["status"] = "fail"
                        drow["detail"] = ("requested TX was refused: "
                                          + marker["tx_phase"].get("detail", ""))
                        bw()
                # TRUTHFUL terminal state: `completed` ONLY when every row is success,
                # TX is skipped/successful, and required cleanup is complete. Blocked
                # rows are NOT success — the run did not do everything it was asked to.
                any_bad = (any(r2["status"] != "success" for r2 in marker["stacks"])
                           or marker["tx_phase"]["status"] == "fail"
                           or bool(marker["error"]))
                marker["state"] = ("completed-with-failures" if any_bad
                                   else "completed")
                marker["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                                      time.gmtime())
                bw()
        except _Abort:
            return ActionResult(False, "Bulk run ABORTED: durable progress evidence "
                                "could not be persisted.")
        except SourceTxnBlocked as blocked:
            return ActionResult(False, f"Bulk run refused: {blocked}")
        except reslock.ResourceBusy as busy:
            return ActionResult(False, f"Bulk run refused: {busy}")
        cleanup_failed = getattr(self._lock_state, "bulk_cleanup_failed", "")
        if cleanup_failed:
            # A retained lease/reservation blocks the NEXT run until acknowledged: the
            # result must be a durable INCOMPLETE, never a silent success.
            marker["state"] = "completed-with-failures"
            marker["error"] = (marker.get("error", "") +
                               f" boundary cleanup failed ({cleanup_failed}) — "
                               "acknowledge before the next run").strip()
            bulk_mod.write_marker(self._paths, marker)
        ok = marker["state"] == "completed"
        done = sum(1 for r2 in marker["stacks"] if r2["status"] == "success")
        blocked_n = sum(1 for r2 in marker["stacks"] if r2["status"] == "blocked")
        failed_n = sum(1 for r2 in marker["stacks"] if r2["status"] == "fail")
        summary = (f"Bulk run {marker['state']}: {done}/{len(marker['stacks'])} stack(s) "
                   f"successful, {blocked_n} blocked, {failed_n} failed."
                   + ("" if ok else " Successful stacks REMAIN installed and built."))
        if marker.get("error"):
            summary += f" ({marker['error']})"
        emit(f"==== {summary} ====")
        return ActionResult(ok, summary,
                            details=[f"  [{r2['status']}] {r2['id']}: {r2['detail']}"
                                     for r2 in marker["stacks"]],
                            next_commands=["lhpc status --versions"])

    def _bulk_running_components(self, scope) -> list:
        snap = self.build_snapshot()
        up = (RunState.RUNNING, RunState.DEGRADED)
        ids = {c.id for st, _ in scope for c in st.components}
        return sorted(cid for ss in snap.stacks for cid, cst in ss.components.items()
                      if cid in ids and cst.run_state in up)

    def _bulk_running_refusal(self, running) -> ActionResult:
        owners = sorted({self._owner_stack_id(cid) for cid in running})
        return ActionResult(
            False, "Refusing to start the bulk run: component(s) are running — this run "
            "never stops anything itself.",
            details=[f"  running: {', '.join(running)}"],
            next_commands=[f"lhpc stack stop {o} --yes" for o in owners])

    def _bulk_scope_edges(self) -> tuple:
        """(ordered stack ids, {stack -> set(dependency stacks)}) from the manifest graph."""
        stacks = [st for st in self.stacks() if any(c.source for c in st.components)]
        by_comp = {c.id: st.id for st in self.stacks() for c in st.components}
        edges = {st.id: set() for st in stacks}
        for st in stacks:
            for c in st.components:
                for dep in tuple(c.depends_on or ()) + tuple(c.build_requires or ()):
                    owner = by_comp.get(dep)
                    if owner and owner != st.id and owner in edges:
                        edges[st.id].add(owner)
        return [st.id for st in stacks], edges

    def _bulk_tx_phase(self, marker, ctx, emit, bw) -> None:
        """Disclosed temporary daemon start -> ONE bounded TX test -> guaranteed stop
        attempt. EVERY failure path — missing callsign, start failure, TX-test failure,
        or a failed final stop — marks the DAEMON ROW fail with a precise actionable
        detail AND the tx outcome, and persists the state while marker persistence is
        available. No task list may show the daemon successful with a failed TX phase."""
        daemon_row = next((r for r in marker["stacks"] if r["id"] == "daemon"), None)

        def fail_tx(detail: str, ran: bool) -> None:
            marker["tx_phase"] = {"status": "fail", "detail": detail}
            if daemon_row is not None:
                daemon_row["tx"] = {"ran": ran, "ok": False, "detail": detail}
                daemon_row["status"] = "fail"
                daemon_row["detail"] = f"TX phase failed: {detail}"
            bw()                                 # persisted before return when available

        op = self.config().operator
        if not getattr(op, "callsign", ""):
            fail_tx("operator callsign not configured — set it in Settings; refusing to "
                    "transmit unidentified", ran=False)
            return
        emit("==== TX phase: starting the daemon TEMPORARILY (disclosed; real RF) ====")
        marker["tx_phase"] = {"status": "running", "detail": ""}
        bw()
        started = False
        stop_failed = ""
        try:
            rs = self.start("daemon", apply=True, bulk_ctx=ctx)
            emit(rs.summary)
            if not rs.ok:
                fail_tx(f"temporary daemon start failed: {rs.summary}", ran=False)
                return
            started = True
            rt = self.test("daemon", tx=True, apply=True, bulk_ctx=ctx)
            for line in rt.details:
                emit(line)
            if not rt.ok:
                fail_tx(f"TX test failed: {rt.summary}", ran=True)
                return
            marker["tx_phase"] = {"status": "success", "detail": rt.summary}
            if daemon_row is not None:
                daemon_row["tx"] = {"ran": True, "ok": True, "detail": "passed"}
        finally:
            if started:
                rstop = self.stop("daemon", apply=True, bulk_ctx=ctx)
                emit(rstop.summary)
                if not rstop.ok:
                    prior = marker["tx_phase"].get("detail", "")
                    fail_tx((prior + " — " if prior and
                             marker["tx_phase"]["status"] == "fail" else "") +
                            "final daemon stop FAILED — the daemon may still be "
                            "RUNNING; stop it: lhpc stack stop daemon --yes", ran=True)
                    stop_failed = "stop"
            if not stop_failed:
                bw()

    # ---- bulk reconciliation + global plan (M2.0b) -------------------------

    def _reconcile_group(self, path: str, comp) -> tuple:
        """Per-SOURCE-GROUP action decision (never `is_installed(stack)` guessing):
        absent leaf -> install; registered + identity-valid -> update; anything partial,
        unowned, unsafe, dirty, or otherwise unprovable -> ("blocked", typed reason).
        Driver-side (may run git identity checks under the held boundary)."""
        from . import source_fs, source_registry
        try:
            dest = self._paths.resolve_source(path)
            kind = source_fs.leaf_kind(self._paths, dest)
        except PathContainmentError as exc:
            return "blocked", f"unsafe source path ({exc})"
        rec_state, rec, rec_why = source_registry.record_state(self._paths, path)
        if rec_state == "unsafe":
            return "blocked", f"unsafe ownership record — {rec_why}"
        if kind == "absent":
            if rec_state == "valid":
                return "blocked", ("ownership record exists but the source is absent — "
                                   "run uninstall to clear the orphaned record")
            return "install", ""
        if kind in ("file", "special"):
            return "blocked", f"unexpected {kind} leaf at the managed source path"
        if rec_state != "valid":
            return "blocked", ("present but UNOWNED (no ownership record) — LHPC never "
                               "overwrites an unmanaged tree; move it away or Clean")
        vrec, why = source_registry.verify_identity(
            self._paths, self._system, self.config(), comp, dest,
            components=tuple(sorted(self._source_consumers().get(path, {comp.id}))))
        if vrec is None:
            return "blocked", f"identity not provable — {why}"
        if kind == "dir":
            inst = self._installer()
            dirty = inst.dirty_report(dest, path)
            if dirty:
                return "blocked", ("local changes present — commit/stash or Clean before "
                                   "a bulk update touches this checkout")
        return "update", ""

    def bulk_mode(self) -> str:
        """FILE-ONLY page-mode aggregate for GET routes: 'install' (nothing present),
        'update' (all present), or 'mixed'. Uses leaf existence only — the authoritative
        per-group reconciliation runs in the driver under the held boundary."""
        from . import source_fs
        actions = set()
        for st in self.stacks():
            for c in st.components:
                if c.source is None or c.optional:
                    continue
                try:
                    kind = source_fs.leaf_kind(self._paths,
                                               self._paths.resolve_source(c.source.path))
                except PathContainmentError:
                    kind = "special"
                actions.add("install" if kind == "absent" else "update")
        if actions == {"install"} or not actions:
            return "install"
        if actions == {"update"}:
            return "update"
        return "mixed"

    def bulk_welcome(self) -> dict | None:
        """First-start banner decision, FILE-ONLY and tri-state: {"fresh": True} only when
        NO managed installed state exists AND everything is safely readable; an unsafe
        registry record, unresolved source transaction, or unowned present source returns
        {"fresh": False, "recovery": reason} — recovery guidance, never a misleading
        fresh-install welcome. None -> installed state exists (no banner)."""
        from . import source_fs, source_registry
        txn_dir = self._paths.under("state", "source-txn")
        try:
            names = [n for n, _ in runtime_fs.scandir_nofollow(self._paths, txn_dir)]
            if any(n.endswith(".json") for n in names):
                return {"fresh": False, "recovery":
                        "an unresolved source transaction exists — see lhpc status"}
        except FileNotFoundError:
            pass
        except (OSError, PathContainmentError):
            return {"fresh": False, "recovery": "runtime state is not safely readable"}
        for st in self.stacks():
            for c in st.components:
                if c.source is None:
                    continue
                try:
                    kind = source_fs.leaf_kind(self._paths,
                                               self._paths.resolve_source(c.source.path))
                except PathContainmentError:
                    return {"fresh": False, "recovery":
                            f"unsafe source path for {c.id} — inspect the runtime root"}
                state, rec, why = source_registry.record_state(self._paths, c.source.path)
                if state == "unsafe":
                    return {"fresh": False, "recovery":
                            f"unsafe ownership record for {c.source.path} — {why}"}
                if kind != "absent":
                    if state == "valid":
                        return None                       # managed install exists
                    return {"fresh": False, "recovery":
                            f"unmanaged tree at {c.source.path} — move it away or Clean"}
                if kind == "absent" and state == "valid":
                    return {"fresh": False, "recovery":
                            f"orphaned ownership record for {c.source.path} — run "
                            "uninstall to clear it"}
        return {"fresh": True}

    def _bulk_scope(self) -> list:
        """(stack, [components-with-sources]) for every stack in DEPENDENCY order
        (manifest graph: depends_on + build_requires stack edges; stable manifest order
        among independents). OPTIONAL components are INCLUDED — the bulk run installs and
        builds every declared source under <root>/src (they are only excluded from
        auto-START, which stays autostart-gated). This also keeps the boundary's lock set
        aligned with what build()/test() cover (a stack build covers ALL its comps)."""
        stacks = [st for st in self.stacks()
                  if any(c.source for c in st.components)]
        by_comp = {c.id: st.id for st in self.stacks() for c in st.components}
        edges = {st.id: set() for st in stacks}
        for st in stacks:
            for c in st.components:
                for dep in tuple(c.depends_on or ()) + tuple(c.build_requires or ()):
                    owner = by_comp.get(dep)
                    if owner and owner != st.id and owner in edges:
                        edges[st.id].add(owner)
        ordered, seen = [], set()
        def visit(sid, chain=()):
            if sid in seen or sid in chain:
                return
            for dep in sorted(edges.get(sid, ())):
                visit(dep, chain + (sid,))
            seen.add(sid)
            ordered.append(sid)
        for st in stacks:
            visit(st.id)
        by_id = {st.id: st for st in stacks}
        out = []
        for sid in ordered:
            st = by_id[sid]
            comps = [c for c in st.components if c.source]
            if comps:
                out.append((st, comps))
        return out

    # ---- bulk-operation boundary (M2.0) ----------------------------------

    def _current_bulk_ctx(self):
        return getattr(self._lock_state, "bulk_ctx", None)

    def _bulk_ctx_error(self, bulk_ctx, source_paths) -> str:
        """Fail-closed validation of an EXPLICIT outer bulk-operation context: it must BE
        this thread's active boundary and COVER the operation's source paths. Returns ""
        when valid (or when no context is supplied — the op runs standalone)."""
        if bulk_ctx is None:
            return ""
        if bulk_ctx is not self._current_bulk_ctx():
            return ("bulk operation context is not the active boundary of this thread — "
                    "refusing (locks not provably held)")
        if not bulk_ctx.covers(source_paths):
            missing = sorted(set(source_paths) - set(bulk_ctx.source_paths))
            return ("bulk operation context does not cover source path(s) "
                    f"{', '.join(missing)} — refusing (locks not provably held)")
        return ""

    @contextmanager
    def _bulk_boundary(self, run_id: str, stacks, source_paths):
        """The ONE outer boundary of a bulk run, held for its whole lifetime:
        config-stable (shared; a concurrent remote/config save waits) → source-txn
        index/recovery → ALL affected source-path locks (same coordination locks
        Start/Restart contend on) → durable LEASE bound to this process's full identity →
        the explicit `BulkOperationContext` active for this thread. Composed ops nest via
        the re-entrant guards and validate the context; the lease is cleared and the
        context deactivated before the locks release. Lease-write failure aborts typed —
        the boundary never operates without durable evidence."""
        from . import bulk as bulk_mod, procident
        with self._config_stable():
            with self._source_operation_guard(sorted(source_paths), op="install-all"):
                ident = procident.proc_identity(os.getpid()) or {}
                if not procident.identity_complete(ident):
                    raise SourceTxnBlocked(
                        "bulk lease refused: own process identity incomplete")
                if not bulk_mod.write_lease(self._paths, run_id, os.getpid(), ident,
                                            stacks, source_paths):
                    raise SourceTxnBlocked("bulk lease could not be persisted — refusing "
                                           "to operate without durable evidence")
                ctx = bulk_mod.BulkOperationContext(run_id, source_paths)
                self._lock_state.bulk_ctx = ctx
                try:
                    self._lock_state.bulk_cleanup_failed = ""
                    yield ctx
                finally:
                    self._lock_state.bulk_ctx = None
                    fails = []
                    if not bulk_mod.clear_lease(self._paths):
                        fails.append("lease")
                    if not bulk_mod.clear_reservation(self._paths):
                        fails.append("bulk-start reservation")
                    if fails:
                        # retained evidence blocks the next run until acknowledged; the
                        # driver reads this flag and reports a truthful INCOMPLETE result.
                        self._lock_state.bulk_cleanup_failed = " + ".join(fails)

    # ---- manifest --------------------------------------------------------

    def stacks(self) -> tuple[Stack, ...]:
        if self._stacks is None:
            self._stacks = manifest_mod.load_manifest(self._manifest_path)
        return self._stacks

    def stack(self, stack_id: str) -> Stack | None:
        for s in self.stacks():
            if s.id == stack_id:
                return s
        return None

    def controller(self):
        """LHPC's own checkout as a dedicated controller identity (or None). Parsed via
        the SEPARATE `load_controller` accessor — never through stack machinery."""
        if self._controller is _UNSET:
            self._controller = manifest_mod.load_controller(self._manifest_path)
        return self._controller

    def _controller_deps_sync_cmd(self) -> str:
        """The EXACT editable-install command for the self-hosted controller after a
        `deps_changed` update: the DEPLOYMENT interpreter (`<root>/venv/lhpc/bin/python`)
        against the controller CHECKOUT (`<root>/<source_path>`), shell-quoted so a path
        with spaces/metacharacters is safe to paste. Empty when no controller is declared —
        the caller then falls back to the dev `pip install -e .`."""
        spec = self.controller()
        if spec is None:
            return ""
        root = self._paths.runtime_root
        python_bin = root / "venv" / "lhpc" / "bin" / "python"
        checkout = root.joinpath(*Path(spec.source_path).parts)
        return (f"{shlex.quote(str(python_bin))} -m pip install -e "
                f"{shlex.quote(str(checkout))}")

    def _controller_refusal(self, target) -> "ActionResult | None":
        """CENTRAL guard: a generic verb (install/update/uninstall/clean/build/test/
        start/stop) targeting the controller id returns a typed refusal BEFORE any target
        resolution or mutation. The CLI/web adapters only RENDER this — they hold no guard
        logic of their own. `lhpc update <controller-id>` is NOT an alias for self-update."""
        c = self.controller()
        if c is not None and target == c.id:
            return ActionResult(
                False, "LHPC's own checkout is controller-managed. Use: lhpc self-update",
                next_commands=["lhpc self-update"])
        return None

    def controller_identity_live(self) -> dict:
        """LIVE controller-identity proof (git subprocesses) — used ONLY at startup
        refresh, explicit "check now", and immediately before self-update apply. Returns a
        TRI-STATE verdict `{checked_at, status, ok, reason}` where `status` is:
          * `not_applicable` — the deployment is NOT self-hosted (lhpc does not run from the
            in-root `src/loraham-pi-control` checkout: a bootstrap-only root, a plain/dev
            install, etc.). NEUTRAL, not a failure — self-update proceeds via the normal
            `repo_root()` mechanism and apply is NOT blocked.
          * `unsafe` — the deployment IS self-hosted but the in-root checkout/layout is
            tampered/misconfigured (symlink leaf, group/other-writable, wrong branch/origin,
            repo/package mismatch). Apply IS blocked.
          * `ok` — self-hosted and every strict check passed.
        `ok` is the boolean `status == "ok"` for callers that only care about the green path.

        The strict (self-hosted) path is STRICTER than managed-source resolution
        (`resolve_source` permits a symlink to an external checkout; here that would let the
        deployment silently run from an outside tree). It is a detection boundary, NOT a
        same-account race-proof guarantee."""
        import stat as _stat

        import lhpc as _lhpc

        from . import selfupdate as _su
        now = int(time.time())

        def verdict(status: str, reason: str) -> dict:
            return {"checked_at": now, "status": status, "ok": status == "ok",
                    "reason": reason[:200]}

        spec = self.controller()
        if spec is None:
            return verdict("not_applicable", "no controller declared")
        if spec.source_path != manifest_mod.CONTROLLER_SOURCE_PATH:
            return verdict("unsafe", "controller source_path is not the fixed value")
        try:
            checkout = self._paths.under(*Path(spec.source_path).parts)   # contained, no-follow
        except PathContainmentError as exc:
            return verdict("unsafe", f"source path escapes runtime root ({exc})")

        # SELF-HOSTED? lhpc must actually run FROM the in-root checkout. If the checkout is
        # absent, or lhpc runs from a DIFFERENT tree (dev checkout / plain install / tangled
        # root), the controller-identity boundary does not apply -> NEUTRAL. This is the
        # common case for a bootstrap-only or non-migrated deployment and must NOT read as a
        # security failure or block self-update.
        repo = _su.repo_root()
        real_checkout = os.path.realpath(checkout)
        if repo is None or not os.path.exists(checkout):
            return verdict("not_applicable",
                           "not self-hosted: no controller checkout under the runtime root")
        if os.path.realpath(repo) != real_checkout:
            return verdict("not_applicable",
                           "not self-hosted: lhpc runs from a different checkout")

        # The deployment IS self-hosted -> strict tamper checks. A failure now is UNSAFE.
        root = self._paths.runtime_root
        for label, pth in (("runtime root", root), ("src", root / "src"),
                           ("checkout", checkout)):
            try:
                st = os.lstat(pth)
            except OSError:
                return verdict("unsafe", f"{label} is missing")
            if _stat.S_ISLNK(st.st_mode):
                return verdict("unsafe", f"{label} is a symlink (fixed layout required)")
            if not _stat.S_ISDIR(st.st_mode):
                return verdict("unsafe", f"{label} is not a directory")
            if st.st_uid != os.getuid():
                return verdict("unsafe", f"{label} not owned by the service user")
            if st.st_mode & 0o022:
                return verdict("unsafe", f"{label} is group/other-writable")
        real_root = os.path.realpath(root)
        if not (real_checkout == real_root or real_checkout.startswith(real_root + os.sep)):
            return verdict("unsafe", "checkout realpath escapes the runtime root")
        if os.path.realpath(str(Path(_lhpc.__file__).resolve().parents[1])) != real_checkout:
            return verdict("unsafe", "imported package repo != controller checkout")
        g = _su._git(self._system, Path(real_checkout), ["rev-parse", "--is-inside-work-tree"], 10.0)
        if g.returncode != 0 or g.stdout.strip() != "true":
            return verdict("unsafe", "not a git checkout")
        b = _su._git(self._system, Path(real_checkout), ["rev-parse", "--abbrev-ref", "HEAD"], 10.0)
        head_branch = b.stdout.strip() if b.returncode == 0 else ""
        if head_branch == "HEAD":
            return verdict("unsafe", "checkout is in detached HEAD")
        if head_branch != spec.branch:
            return verdict("unsafe", f"checkout branch {head_branch!r} != {spec.branch!r}")
        o = _su._git(self._system, Path(real_checkout), ["config", "--get", "remote.origin.url"], 10.0)
        origin = o.stdout.strip() if o.returncode == 0 else ""
        canon_spec = _canon_git_url(spec.remote)
        # Reject an EMPTY canonical on either side: a degenerate manifest remote (".git",
        # "/", "https://") canonicalizes to "" and would otherwise match a checkout with NO
        # origin (also "") — a false-accept. A valid, non-empty canonical must match.
        if not canon_spec or _canon_git_url(origin) != canon_spec:
            return verdict("unsafe", "origin is not the approved canonical remote")
        return verdict("ok", "identity ok")

    @property
    def runtime_root(self):
        """Absolute runtime installation root (display/resolution use)."""
        return self._paths.runtime_root

    # ---- the single probing path (used by CLI and web) -------------------

    def build_snapshot(self) -> Snapshot:
        """Fresh, bounded, read-only assessment of every stack. No caching. The
        confirmed-working map comes from the OPERATOR-CONFIRMED known-working compositions
        (file reads only): a component is confirmed-working when its clean source HEAD appears
        in a stored composition of its stack."""
        from . import known_working
        confirmed: dict = {}
        for s in self.stacks():
            comps = known_working.load(self._paths, s.id)
            for comp in comps:
                for cid, entry in comp["entries"].items():
                    if entry.get("commit"):
                        confirmed.setdefault(cid, set()).add(entry["commit"])
        return StatusProber(self._system, self._paths, confirmed).assess_stacks(self.stacks())

    # ---- read-only operations --------------------------------------------

    def list_stacks(self) -> ActionResult:
        stacks = self.stacks()
        details = [
            f"{s.id:10s} {len(s.components):2d} components  {s.summary}" for s in stacks
        ]
        return ActionResult(
            ok=True,
            summary=f"{len(stacks)} stacks defined in the manifest.",
            details=details,
            next_commands=["lhpc status", "lhpc explain <stack>"],
        )

    def status(self, stack_id: str | None = None) -> ActionResult:
        if stack_id and self.stack(stack_id) is None:
            return self._unknown_stack(stack_id)
        snap = self.build_snapshot()
        rollup = rollup_states(snap)
        details: list[str] = []
        if not stack_id:
            counts = summarize(snap)["states"]
            tally = ", ".join(f"{k}={v}" for k, v in sorted(counts.items()))
            details.append(f"{len(snap.stacks)} stacks, "
                           f"{summarize(snap)['components']} components: {tally}")
            details.append("")
        for ss in snap.stacks:
            if stack_id and ss.stack.id != stack_id:
                continue
            details.append(f"[{ss.stack.id}] {ss.stack.name}  ({rollup[ss.stack.id]})")
            for comp in ss.stack.components:
                st = ss.components[comp.id]
                details.extend(_render_component(comp, st))
        observed = self._observed_conflicts(snap)
        if observed:
            details.append("")
            details.append("Observed resource conflicts:")
            for c in observed:
                details.append(f"  ! {c.message}")
        flagged = [sid for sid in self.restart_required_stacks()
                   if not stack_id or sid == stack_id]
        if flagged:
            details.append("")
            for sid in flagged:
                marker = self.restart_required(sid) or {}
                if marker.get("unsafe"):
                    details.append(f"  ! RESTART REQUIRED (safe-side): '{sid}' — "
                                   f"{marker.get('reason', 'marker unreadable')}")
                else:
                    details.append(f"  ! RESTART REQUIRED: '{sid}' — saved settings differ "
                                   f"from the running stack (lhpc stack stop {sid} && "
                                   f"lhpc stack start {sid})")
        if not snap.runtime_root_exists:
            details.append("")
            details.append(
                "Note: runtime root not installed; managed sources report "
                "'not-installed' (expected before install)."
            )
        # Controller row — a DISTINCT non-stack entity (LHPC's own checkout). Cached-only
        # (no git/network/live check here); managed only via `lhpc self-update`.
        if not stack_id:
            cs = self.controller_status()
            if cs is not None:
                details.append("")
                idv = cs.get("identity")
                st_id = (idv or {}).get("status")
                if idv is None:
                    ident = "identity unchecked"
                elif st_id == "ok":
                    ident = "identity ok"
                elif st_id == "unsafe":
                    ident = f"identity UNSAFE ({idv.get('reason', '')})"
                else:
                    ident = "not self-hosted"
                upd = "update available" if cs["update_available"] else "up to date"
                head = f"@{cs['head_short']}" if cs["head_short"] else ""
                details.append(f"[controller] {cs['display_name']}  ({upd})")
                details.append(f"  v{cs['version']} {head}  {ident}  — manage with: "
                               f"{cs['self_update_cmd']}")
        # Probing succeeded; status is informational — exit success even when stopped.
        return ActionResult(
            ok=True,
            summary="Status collected (read-only; no network, no changes).",
            details=details,
            next_commands=["lhpc explain <stack>", "lhpc doctor", "lhpc status --versions"],
        )

    def status_versions(self) -> ActionResult:
        snap = self.build_snapshot()
        details: list[str] = []
        for ss in snap.stacks:
            for comp in ss.stack.components:
                if comp.source is None:
                    continue
                st = ss.components[comp.id]
                pin = (comp.source.pin_commit[:12] or "-") if comp.source else "-"
                tag = comp.source.pin_tag or "-"
                details.append(
                    f"  {comp.id:24s} {st.source_state.value:12s} "
                    f"pin={pin} tag={tag}"
                )
        return ActionResult(
            ok=True,
            summary="Source/pin status (local git only; no fetch). "
            "A pin match is NOT a confirmed-working judgement.",
            details=details,
            next_commands=["lhpc status", "lhpc doctor"],
        )

    def explain(self, stack_id: str) -> ActionResult:
        s = self.stack(stack_id)
        if s is None:
            return self._unknown_stack(stack_id)
        details = [s.summary, "", "Components (manual start order):"]
        ordered = sorted(
            s.components, key=lambda c: (c.start_order is None, c.start_order or 0)
        )
        for c in ordered:
            order = "-" if c.start_order is None else str(c.start_order)
            tx = "TX-capable" if c.tx_capable else "RX-only"
            band = f" {c.band}MHz" if c.band else ""
            details.append(f"  {order}. {c.id}{band} — {c.purpose} [{c.kind.value}, {tx}]")
            if c.depends_on:
                details.append(f"        depends on: {', '.join(c.depends_on)}")
            for r in c.resources:
                extra = f" = {r.requirement}" if r.requirement else ""
                details.append(f"        claims {r.key} ({r.mode.value}{extra})")
            if c.note:
                details.append(f"        note: {c.note}")
        return ActionResult(
            ok=True,
            summary=f"Stack '{s.id}': {s.name}",
            details=details,
            next_commands=[f"lhpc status {s.id}"],
        )

    def doctor(self) -> ActionResult:
        sys = self._system
        details: list[str] = []

        root = self._paths.runtime_root
        details.append(
            f"runtime root: {'present' if self._paths.runtime_root_exists else 'absent (run lhpc bootstrap)'} ({root})"
        )
        op = self.config().operator
        details.append(
            f"  operator: {op.callsign + ' (' + op.locator + ')' if op.configured else 'not configured (set in runtime config/local.toml)'}"
        )
        details.append(f"  systemctl: {hardware.check_systemctl(sys, user=False).detail}")
        details.append(f"  systemctl --user: {hardware.check_systemctl(sys, user=True).detail}")
        for dev in (_SPI_DEV, _GPIO_DEV):
            chk = hardware.check_char_device(sys, dev)
            details.append(f"  {dev}: {chk.detail}")

        # Configured source paths present?
        present = missing = 0
        for s in self.stacks():
            for c in s.components:
                if c.source is None:
                    continue
                p = str(self._paths.resolve_source(c.source.path))
                if sys.fs.exists(p):
                    present += 1
                else:
                    missing += 1
        details.append(f"  configured sources: {present} present, {missing} missing")

        # Itemized UNMET dependencies per stack (grouped): system prerequisites carry the
        # exact operator command — LHPC never installs system packages itself.
        from . import deps as deps_mod
        any_missing = False
        for s in self.stacks():
            groups = self.deps_report(s.id)
            unmet = [d for d in groups["system"] + groups["build"] if not d.satisfied]
            if not unmet:
                continue
            any_missing = True
            details.append(f"  {s.id}: unmet dependencies")
            for d in unmet:
                line = f"    [{d.kind}] {d.label} — {d.detail}"
                if d.install_cmd:
                    line += f" | run yourself: {d.install_cmd}"
                details.append(line)
        if not any_missing:
            details.append("  dependencies: all declared system/build prerequisites satisfied")
        details.append(f"  ({deps_mod.NOT_EXECUTED_NOTE})")

        # Run-state tally from a fresh snapshot.
        snap = self.build_snapshot()
        tally: dict[str, int] = {}
        for ss in snap.stacks:
            for st in ss.components.values():
                tally[st.run_state.value] = tally.get(st.run_state.value, 0) + 1
        details.append("  components: " + ", ".join(f"{k}={v}" for k, v in sorted(tally.items())))
        observed = self._observed_conflicts(snap)
        details.append(f"  observed resource conflicts: {len(observed)}")

        return ActionResult(
            ok=True,
            summary="doctor: bounded local checks only (no init, no network, no RF).",
            details=details,
            next_commands=["lhpc status", "lhpc status --versions"],
        )

    # ---- install / bootstrap ---------------------------------------------

    def bootstrap(self, apply: bool = False) -> ActionResult:
        inst = self._installer()
        plan = inst.plan_bootstrap()
        if not apply:
            return self._plan_result(plan, applied=False, next_apply="lhpc bootstrap --yes")
        plan = inst.apply_bootstrap(plan)
        return self._plan_result(plan, applied=True, next_apply=None)

    def install(self, stack_id: str | None = None, apply: bool = False,
                source: str = "pinned", bulk_ctx=None) -> ActionResult:
        if (_r := self._controller_refusal(stack_id)) is not None:
            return _r
        if stack_id and self.stack(stack_id) is None:
            return self._unknown_stack(stack_id)
        if not self._paths.runtime_root_exists:
            return ActionResult(
                ok=False,
                summary="Runtime root is not bootstrapped yet.",
                details=[f"Run 'lhpc bootstrap' to create {self._paths.runtime_root}."],
                next_commands=["lhpc bootstrap"],
            )
        # SHARED-SOURCE REMOTE COHERENCE gates BOTH planning and mutation: one checkout is
        # one clone with ONE effective remote — a legacy divergent per-component override
        # blocks install with ZERO candidate/source/registry/config mutation.
        planned_paths = sorted({c.source.path for st in self.stacks()
                                if not stack_id or st.id == stack_id
                                for c in st.components if c.source})
        conflicts = sorted({c for c in (self._shared_remote_conflict(p)
                                        for p in planned_paths) if c})
        if conflicts:
            return ActionResult(False, f"Refusing to install '{stack_id or 'all'}': "
                                "shared-source remote configuration is inconsistent.",
                                details=[f"  {c}" for c in conflicts])
        inst = self._installer()
        plan = inst.plan_install(stack_id)
        if not apply:
            cmd = f"lhpc install {stack_id} --yes" if stack_id else "lhpc install --yes"
            return self._plan_result(plan, applied=False, next_apply=cmd)
        from . import source_fs
        # ONE adoption per coherent source GROUP: each shared path is installed exactly once
        # (deterministic first declarer), never opportunistically re-attempted through
        # whichever consumer is encountered next.
        # ONE immutable plan for the whole install: known-working frozen per stack, one
        # adoption per shared source group, incompatible resolutions blocked up front.
        install_items = [(st, c) for st in self.stacks()
                         if not stack_id or st.id == stack_id
                         for c in st.components if c.source]
        ctx_err = self._bulk_ctx_error(bulk_ctx,
                                       {c.source.path for _, c in install_items})
        if ctx_err:
            return ActionResult(False, f"Refusing to install '{stack_id or 'all'}': "
                                f"{ctx_err}")
        groups, plan_conflicts = self._plan_source_groups(install_items, source)
        if plan_conflicts:
            return ActionResult(False, f"Refusing to install '{stack_id or 'all'}': "
                                "incompatible source resolutions for a shared checkout.",
                                details=[f"  {c}" for c in plan_conflicts])
        mutated_paths, extra_out = [], []
        for path, comp, resolved in groups:
            dest = self._paths.resolve_source(path)
            # DESCRIPTOR-PROVEN skip: only a healthy managed DIRECTORY is "already
            # installed". Anything else (absent, symlink, regular/special file) flows
            # into `adopt_source`, whose locked leaf checks install or refuse typed —
            # a dangling/unknown leaf is never silently treated as installed.
            try:
                if source_fs.leaf_kind(self._paths, dest) == "dir":
                    # HEALTHY SKIP: the leaf already serves this install. RE-JOIN the
                    # targeted consumers in the ownership record's live membership —
                    # otherwise a later sibling departure could remove a leaf this
                    # just-installed stack relies on.
                    from . import source_registry as _sreg
                    state, rec, _w = _sreg.record_state(self._paths, path)
                    targeted = {c2.id for _, c2 in install_items
                                if c2.source and c2.source.path == path}
                    if state == "valid" and not targeted <= set(rec.components):
                        if _sreg.update_components(self._paths, path,
                                                   set(rec.components) | targeted):
                            extra_out.append(f"  [re-joined] {path}: shared checkout now "
                                             "serves this stack again")
                        else:
                            extra_out.append(f"  [warn] {path}: shared-consumer record "
                                             "could not be updated — re-run install")
                    continue
            except PathContainmentError:
                pass                       # unsafe parent -> adopt_source refuses typed
            st_of = next((st2 for st2 in self.stacks()
                          if any(c2.id == comp.id for c2 in st2.components)), None)
            result = self._adopt_dev_fallback(inst, st_of, comp, source, resolved,
                                              force=False,
                                              locked=bulk_ctx is not None)
            if result.status == "done":
                mutated_paths.append(path)
            for a in plan.actions:
                if a.target == str(dest):
                    a.status, a.detail = result.status, result.detail
                    a.provenance = result.provenance
        retire_ok = self._retire_candidates_for_paths(mutated_paths, extra_out)
        res = self._plan_result(plan, applied=True, next_apply=None)
        if not retire_ok:
            return ActionResult(False, res.summary + " (candidate cleanup INCOMPLETE)",
                                details=list(res.details) + extra_out,
                                next_commands=res.next_commands)
        return res

    def _plan_result(self, plan: Plan, *, applied: bool, next_apply: str | None) -> ActionResult:
        details = [
            f"  [{a.status}] {a.description}" + (f" — {a.detail}" if a.detail else "")
            for a in plan.actions
        ]
        failed = [a for a in plan.actions if a.status == "failed"]
        # Expose the per-source provenance state in the result data (activated sources only).
        provenance = {a.target: a.provenance for a in plan.actions if a.provenance}
        if applied:
            done = sum(1 for a in plan.actions if a.status == "done")
            summary = (f"{plan.title}: applied {done} action(s)."
                       if not failed else
                       f"{plan.title}: completed with {len(failed)} failure(s).")
            return ActionResult(ok=not failed, summary=summary, details=details,
                                next_commands=["lhpc status", "lhpc doctor"],
                                data={"provenance": provenance} if provenance else {})
        n = len(plan.changes)
        summary = f"{plan.title}: {n} change(s) planned (dry run)."
        return ActionResult(ok=True, summary=summary, details=details,
                            next_commands=[next_apply] if next_apply and n else [],
                            data={"changes": n})

    # ---- lifecycle operations: build/start/stop/logs/test ----------------

    def _lifecycle(self) -> Lifecycle:
        return Lifecycle(self._paths, self.stacks(), self.config(), self._system)

    def _resolve(self, target: str):
        """Resolve a target to an ordered list of (stack, component). A stack id
        expands to its runnable components in start order; a component id is one."""
        s = self.stack(target)
        if s is not None:
            runnable = [c for c in s.components if c.run_argv]
            runnable.sort(key=lambda c: (c.start_order is None, c.start_order or 0))
            return [(s, c) for c in runnable], None
        for st in self.stacks():
            c = st.component(target)
            if c is not None:
                return [(st, c)], None
        return [], f"Unknown stack or component '{target}'."

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

    DAEMON_ID = "loraham-daemon"
    # After auto-starting the daemon, wait up to this long for its CONF socket to
    # answer before reporting success (the daemon inits the radio asynchronously).
    DAEMON_VERIFY_TIMEOUT_S = 4.0
    DAEMON_VERIFY_POLL_S = 0.5
    # For readiness="endpoint": wait up to this long for every ready=true endpoint.
    ENDPOINT_VERIFY_TIMEOUT_S = 6.0
    ENDPOINT_VERIFY_POLL_S = 0.3

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

    def _component_index(self):
        return {c.id: (s, c) for s in self.stacks() for c in s.components}

    # -- target resolution: a target is either a STACK id or a direct COMPONENT id --------------
    # For a direct component target the OWNER STACK provides persisted config / per-band selection /
    # config-file storage, while only the TARGETED component contributes editable fields + identity.

    def _owner_stack(self, target: str):
        """The stack that owns `target` for config/per-band/config-file storage — the stack itself
        for a stack target, or the owning stack for a direct component target; None if unknown."""
        s = self.stack(target)
        if s is not None:
            return s
        hit = self._component_index().get(target)
        return hit[0] if hit else None

    def _owner_stack_id(self, target: str) -> str:
        s = self._owner_stack(target)
        return s.id if s is not None else target

    def _target_components(self, target: str) -> list:
        """The components whose run/file params + identity a target exposes: ALL of a stack's
        components, or JUST the one component for a direct component target."""
        s = self.stack(target)
        if s is not None:
            return list(s.components)
        hit = self._component_index().get(target)
        return [hit[1]] if hit else []

    def _is_daemon_target(self, target: str) -> bool:
        """A target is daemon-scoped (identity/param-panel exempt) when its owner stack's main IS
        the daemon (a daemon stack target, or a direct daemon-component target)."""
        owner = self._owner_stack(target)
        return owner is not None and owner.main == self.DAEMON_ID

    def _run_order(self, target: str):
        """Ordered (stack, component) list to bring `target` up: the target's
        non-optional components plus their transitive dependencies, deps first."""
        idx = self._component_index()
        s = self.stack(target)
        if s is not None:
            # Optional components are soft: included only when the operator has
            # opted into auto-starting them (even via another component's depends_on).
            cfg = load_stack_config(self._paths, target)
            allowed_optional = {c.id for c in s.components
                                if c.optional and cfg.get(f"autostart_{c.id}") == "on"}
            seeds = [c.id for c in s.components if not c.optional]
            if s.main and s.main not in seeds:
                seeds.append(s.main)
            seeds += list(allowed_optional)
        elif target in idx:
            seeds = [target]
            allowed_optional = {target}   # an explicit component run is always allowed
        else:
            return None
        order, seen = [], set()

        def visit(cid: str):
            if cid in seen or cid not in idx:
                return
            comp = idx[cid][1]
            if comp.optional and cid not in allowed_optional:
                return                    # soft dependency the operator hasn't opted into
            seen.add(cid)
            for dep in comp.depends_on:
                visit(dep)
            order.append(cid)

        for sid in seeds:
            visit(sid)
        return [idx[cid] for cid in order]

    def _all_components_healthy(self, order, st_index, radio: str) -> bool:
        """True when EVERY requested component is already healthy — the daemon serving every needed
        band (READY), and each non-library service component RUNNING. Basis for a no-side-effect
        Start (no launch, no daemon CONF SET, no param apply). A missing band or a stopped client
        makes it False so the normal apply-once-then-start path runs."""
        need = ["433", "868"] if radio == "both" else ([radio] if radio in ("433", "868") else [])
        for _stack, comp in order:
            if comp.kind in (ComponentKind.LIBRARY, ComponentKind.FIRMWARE):
                continue
            if comp.id == self.DAEMON_ID:
                if not need or not all(self.daemon_view(b).ready for b in need):
                    return False
                continue
            if st_index[comp.id].run_state != RunState.RUNNING:
                return False
        return True

    def _daemon_needs(self, order, params, band: str = ""):
        """The daemon's required radio band + TX mode for this run order. `band`
        overrides the band for a band-switchable app stack. Returns (radio, tx); tx is
        None when no single value applies."""
        if not any(c.id == self.DAEMON_ID for _, c in order):
            return None, None
        if params and params.get("radio"):
            return params["radio"], None          # explicit (daemon stack) override
        if band in ("433", "868"):
            bands = {band}
        else:
            bands = {c.band for _, c in order if self.DAEMON_ID in c.depends_on and c.band}
        txs = {c.requires_daemon_tx for _, c in order
               if self.DAEMON_ID in c.depends_on and c.requires_daemon_tx}
        radio = "both" if len(bands) != 1 else next(iter(bands))
        tx = next(iter(txs)) if len(txs) == 1 else None
        return radio, tx

    def _effective_band(self, stack_id: str, fallback: str = "") -> str:
        """The band a stack is actually running on (start marker, or for an
        interactive app the band it was launched on)."""
        return (self.running_band(stack_id, "") or self.interactive_band(stack_id)
                or fallback)

    def _operation_bands(self, target: str, band: str = "", radio: str = "",
                         op: str = "") -> set:
        """THE authoritative radio band(s) a lifecycle op on `target` touches — one source of truth
        for radio locking + conflict detection.
          * START  — the REQUESTED bands (client: its chosen/declared band; daemon: `radio`, else
                     the saved daemon `radio`). Never inferred from the daemon's empty Component.band.
          * STOP   — the ACTUAL running bands: a client uses its running/interactive MARKER (falling
                     back to the declared band only when there is NO runtime evidence); the daemon
                     uses PROCESS TOPOLOGY — a per-band stop also locks the other band when the SAME
                     process serves it (a manual `--radio both`), and a whole-daemon stop locks
                     every band an owned/observed daemon PROCESS serves, even if that band's CONF
                     socket is unreachable / UNINITIALIZED / FAILED.
          * RESTART— the UNION of the actual STOP bands and the requested START bands."""
        order = self._run_order(target)
        if not order:
            return set()
        sid = self.stack_of(target) or target
        stk = self.stack(sid)
        is_daemon = stk is not None and stk.main == self.DAEMON_ID
        if op == "restart":
            return (self._operation_bands(target, band, "", "stop")
                    | self._operation_bands(target, band, radio, "start"))
        if is_daemon:
            if op == "stop":
                if band in ("433", "868"):
                    bands = {band}
                    other = "868" if band == "433" else "433"
                    # --radio both collateral: the SAME process also serves the other band -> lock
                    # it too, regardless of that band's CONF socket state (topology, not reachability).
                    if set(self._daemon_pids_for_band(band)) & set(self._daemon_pids_for_band(other)):
                        bands.add(other)
                    return bands
                # Whole-daemon stop: every band an owned/observed daemon PROCESS claims.
                return self._daemon_claimed_bands()
            r = radio or str(self.stack_config(sid).get("radio") or "both")
            return {"433", "868"} if r == "both" else ({r} if r in ("433", "868") else set())
        # Client.
        if op == "stop":
            eb = self._effective_band(sid, "")        # ACTUAL running band (marker/interactive)
            if eb in ("433", "868"):
                return {eb}
        cfg_band = self._config_band(target, band)    # declared/default (start, or stop w/o evidence)
        return {cfg_band} if cfg_band else {c.band for _, c in order if c.band}

    def _operation_resource_keys(self, target: str, band: str = "", radio: str = "",
                                 op: str = "") -> list[str]:
        """Canonical resource keys an EXCLUSIVE/PROVIDER operation on `target` touches —
        the basis for cross-stack operation locks so a start/stop/restart of one stack
        serializes against another stack claiming the SAME radio/port/socket. Radio claims
        are scoped by `_operation_bands` (band-aware, daemon-radio-aware). Mirrors `run_blockers`
        so the lock set equals the conflict set. CONSUMER/COOPERATIVE claims take no lock."""
        order = self._run_order(target)
        if not order:
            return []
        keys = set()
        for _, c in order:
            for r in c.resources:
                if (r.mode in (ResourceMode.EXCLUSIVE, ResourceMode.PROVIDER)
                        and not r.key.startswith("loraham.radio.")):
                    keys.add(r.key)
        for b in self._operation_bands(target, band, radio, op):
            keys.add(f"loraham.radio.{b}")
        return sorted(keys)

    def _operation_source_paths(self, target: str) -> list[str]:
        """Distinct managed source paths a start touches (generated config, command
        expansion, launch, post-start prep all read from them) — locked for the start so
        a concurrent update/uninstall cannot swap the tree mid-start. Sorted for a stable
        acquisition order; shared checkouts collapse to one key."""
        order = self._run_order(target)
        return sorted({c.source.path for _, c in order if c.source})

    def _lifecycle_lock_keys(self, op: str, target: str, band: str = "",
                             stop_owners: bool = False, cascade: bool = False,
                             radio: str = "") -> list[str]:
        """The COMPLETE lock bundle a lifecycle op must hold: the target's
        `lifecycle.<stack>` + `claim.<resource>` keys (+ source-path keys for start/
        restart), AND — for `stop_owners`/`cascade` — the owners'/dependents' keys too, so
        a cross-target mutation never bypasses another target's coordination. Radio claims are
        band-aware (`radio` carries the daemon's requested mode for a daemon start/restart).
        Returned de-duplicated; the caller acquires them in ONE stable sorted order."""
        from . import reslock
        keys: set[str] = set()

        def add(t: str, with_source: bool, scoped_band: str, scoped_radio: str, scoped_op: str) -> None:
            sid = self.stack_of(t) or t
            keys.add(f"lifecycle.{sid}")
            for rk in self._operation_resource_keys(t, scoped_band, scoped_radio, scoped_op):
                keys.add(f"claim.{rk}")
            if with_source:
                for sp in self._operation_source_paths(t):
                    keys.add(reslock.source_lock_key(sp))

        add(target, op in ("start", "restart"), band, radio, op)
        if stop_owners and op in ("start", "restart"):
            for b in self.run_blockers(target, band, radio):
                holder = b.get("holder_stack") or b.get("holder")
                if holder:
                    add(holder, False, "", "", "")   # holder is a running peer; its own bands apply
        if cascade and op == "stop":
            for dep in self._dependents_of(target):
                add(dep, False, "", "", "stop")
        return sorted(keys, key=reslock.canonical_key)

    def _dependents_of(self, target: str) -> list[str]:
        """Stack ids of RUNNING stacks that depend on `target` (for cascade stop)."""
        order_ids = {c.id for _, c in (self._run_order(target) or [])}
        out = set()
        for s in self.stacks():
            for c in s.components:
                if any(d in order_ids for d in (c.depends_on or ())):
                    out.add(s.id)
        return sorted(out)

    def _held_counts(self) -> dict:
        """Per-THREAD map of lock key -> recursion depth currently held by THIS thread."""
        st = self._lock_state
        counts = getattr(st, "counts", None)
        if counts is None:
            counts = st.counts = {}
        return counts

    # How long to WAIT for a resource claim held by our OWN controller process before failing.
    _SELF_LOCK_WAIT_S = 5.0

    def _acquire_key(self, stack, k: str, op: str, target: str) -> None:
        """Enter one reslock key into `stack`. A claim held by ANOTHER process is a real external
        conflict → fail fast (`ResourceBusy`). A claim held by our OWN controller process is a
        concurrent/overlapping controller op (this service is shared across waitress threads, and
        two lifecycle ops can touch a shared claim like `loraham.daemon-socket.433`) that releases
        shortly → wait BOUNDED, so the operator is never told their own stack is 'busy' on itself,
        while a genuinely hung holder still can't wedge us forever."""
        from . import reslock
        deadline = time.monotonic() + self._SELF_LOCK_WAIT_S
        while True:
            try:
                stack.enter_context(reslock.operation_lock(self._paths, k, op, target))
                return
            except reslock.ResourceBusy as busy:
                same_process = str(busy.holder.get("pid")) == str(os.getpid())
                if not same_process or time.monotonic() >= deadline:
                    raise
                time.sleep(0.1)

    @contextmanager
    def _lifecycle_guard(self, op: str, target: str, band: str = "",
                         stop_owners: bool = False, cascade: bool = False, radio: str = ""):
        """Acquire the lifecycle lock bundle. RE-ENTRANT per THREAD: a key already held by
        an outer guard in THIS thread is not re-flocked (so restart→stop+start and
        stop_owners→stop nest without self-contending), but an INDEPENDENT thread sharing
        this service contends through `reslock` and gets `ResourceBusy`. Recursion counts
        ensure a nested guard never releases an outer guard's flock."""
        from . import reslock
        keys = self._lifecycle_lock_keys(op, target, band, stop_owners, cascade, radio)
        counts = self._held_counts()
        bumped: list[str] = []
        # For a start/restart that acquires source locks FRESH (not nested inside an outer
        # guard that already holds them), do the index→recover→block→source handoff: hold
        # the INDEX lock across the journal check AND the source-lock acquisition, then
        # release it — so a start cannot pass a journal check then race a retained journal.
        fresh_source = any(k.startswith("source.") and counts.get(k, 0) == 0 for k in keys)
        do_handoff = op in ("start", "restart") and fresh_source
        try:
            with contextlib.ExitStack() as stack:
                idx_stack = contextlib.ExitStack()
                try:
                    if do_handoff:
                        inst = self._installer()
                        idx_stack.enter_context(
                            reslock.operation_lock(self._paths, inst._index_key(), op, target))
                        inst._recover_scan()
                        if inst._pending_journals():
                            raise SourceTxnBlocked(
                                "an unresolved source-transaction journal is present — "
                                "resolve it before starting")
                    for k in keys:
                        if counts.get(k, 0) == 0:   # not held by an outer guard in THIS thread
                            self._acquire_key(stack, k, op, target)
                        counts[k] = counts.get(k, 0) + 1
                        bumped.append(k)
                finally:
                    idx_stack.close()               # release index AFTER source held (or on error)
                yield
        finally:
            for k in bumped:
                counts[k] -= 1
                if counts[k] <= 0:
                    counts.pop(k, None)

    @contextmanager
    def _keys_guard(self, op: str, target: str, keys: list):
        """Acquire an explicit set of reslock keys, RE-ENTRANT per thread (sharing the same
        `_held_counts` as `_lifecycle_guard`, so a key already held by an enclosing start is not
        re-flocked). Raises `reslock.ResourceBusy` if an independent operation holds one."""
        from . import reslock
        counts = self._held_counts()
        bumped: list = []
        try:
            with contextlib.ExitStack() as stack:
                for k in sorted(set(keys), key=reslock.canonical_key):
                    if counts.get(k, 0) == 0:
                        self._acquire_key(stack, k, op, target)
                    counts[k] = counts.get(k, 0) + 1
                    bumped.append(k)
                yield
        finally:
            for k in bumped:
                counts[k] -= 1
                if counts[k] <= 0:
                    counts.pop(k, None)

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
        cfg_band = self._config_band(target, band)
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

    def start(self, target: str, apply: bool = False, params: dict | None = None,
              stop_owners: bool = False, band: str = "",
              daemon_overrides: dict | None = None,
              file_overrides: dict | None = None, bulk_ctx=None) -> ActionResult:
        """Public, LOCKED entry — acquires the full lifecycle lock bundle (incl. owners
        when stop_owners) so a DIRECT call gets the same coordination as CLI/web.
        `daemon_overrides`/`file_overrides` are ephemeral per-start values (this launch only, never
        persisted); None = apply the saved config, as the CLI does."""
        if (_r := self._controller_refusal(target)) is not None:
            return _r
        from . import reslock
        if bulk_ctx is not None:
            order = self._run_order(target) or []
            ctx_err = self._bulk_ctx_error(
                bulk_ctx, {c.source.path for _, c in order if c.source})
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
        # Hold saved configuration STABLE from lock planning through the whole applied start (LOCK
        # ORDER: config guard BEFORE the lifecycle/resource lock; re-entrant with _start_impl).
        try:
            with self._config_stable():
                # The daemon's REQUESTED radio mode determines which bands the lock bundle covers.
                _order = self._run_order(target)
                _radio = ""
                if _order:
                    _r, _ = self._daemon_needs(_order, params, self._config_band(target, band))
                    _radio = _r or ""
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
        launch_bands = ["433", "868"] if radio == "both" else ([radio] if radio in ("433", "868") else [])
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
                                    next_commands=[f"lhpc config {target}"])
        if not apply:
            details = []
            commands = []   # copyable commands the operator must run themselves
            for _, comp in order:
                if comp.id == self.DAEMON_ID:
                    details.append(f"  [daemon] start/ensure --radio {radio or 'both'}"
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
                record(comp, stack, Outcome.BLOCKED, "missing "
                       + "; ".join(f"{r.cmd} ({r.install})" for r in miss))
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
                           f"ready endpoint(s) never came up ({'; '.join(ev)}); cleanup: "
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
        ownership signal). `--radio 433` → {433}, `--radio 868` → {868}, `--radio both` (or a
        missing/unknown mode) → conservatively {433, 868}. A CONF socket that is unreachable /
        UNINITIALIZED / FAILED does NOT free the radio — the live process still owns it."""
        bands = set()
        for radio in self._daemon_radio_modes():
            if radio == "433":
                bands.add("433")
            elif radio == "868":
                bands.add("868")
            else:
                bands |= {"433", "868"}       # both / missing / unknown -> conservative
        return bands

    def _daemon_pids_for_band(self, band: str) -> list[int]:
        """PIDs of daemon instances that serve `band` — those launched with
        --radio <band> or --radio both. Lets a per-band Stop signal only that
        instance, leaving the other band's daemon running."""
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
            if radio in (band, "both") or radio not in ("433", "868"):
                out.append(pid)              # unknown mode -> conservatively serves the band
        return out

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
        # Bands this start must make ready. --radio both must ensure BOTH 433 and 868.
        needed = ["433", "868"] if (radio or "both") == "both" else [radio]
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
        if band in daemon_control.ALLOWED_BANDS:
            return band
        s = self._owner_stack(target)
        fixed = sorted({c.band for c in (s.components if s else ()) if c.band}
                       & set(daemon_control.ALLOWED_BANDS))
        return fixed[0] if fixed else "433"

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
        ONE band (the page's selected band, or the stack's fixed band). {} for direct-SPI /
        unknown stacks. Values are the effective config-file values (default + operator save)."""
        from . import daemon_params
        sid = self._owner_stack_id(target)                   # owner-stack daemon profile + storage
        is_daemon = self._is_daemon_target(target)
        if not (is_daemon or daemon_params.is_client(sid)):
            return {}
        b = self._effective_daemon_band(target, band)
        # "Apply live" only makes sense against a live daemon: the daemon page always, or an
        # app stack that is currently running (its daemon dependency is up).
        can_apply = is_daemon or self.stack_running(sid)
        return {"stack": target, "band": b, "is_daemon": is_daemon, "can_apply": can_apply,
                "rows": daemon_params.stack_view(sid, b, self._daemon_param_overrides(target, b))}

    def daemon_start_panels(self, target: str, params: dict | None = None, band: str = "",
                            display_overrides: dict | None = None) -> list:
        """Start-confirm panel view(s): ONE per band THIS launch will touch — two for a daemon
        `--radio both`, one for a single-band daemon or client start. Each panel carries its own
        band, source defaults + saved overrides, and (via the template) band-scoped input names
        `dp_<band>_<PARAM>`, so a 433 value never reaches 868. The radio mode comes from `params`
        (the daemon's `p_radio`), not just the URL band. `display_overrides` ({band: {PARAM: value}})
        are SUBMITTED-but-unsaved panel values shown on a re-render (so a failed Save & start keeps
        the operator's edits). [] for direct-SPI / unknown stacks."""
        from . import daemon_params
        is_daemon = self._is_daemon_target(target)
        if not (is_daemon or daemon_params.is_client(self._owner_stack_id(target))):
            return []
        radio, _tx = self._daemon_needs(self._run_order(target), params, self._config_band(target, band))
        if radio == "both":
            bands = ["433", "868"]
        elif radio in ("433", "868"):
            bands = [radio]
        else:
            bands = [self._effective_daemon_band(target, band)]

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

    def apply_daemon_params(self, target: str, band: str = "") -> ActionResult:
        """Apply this stack's effective daemon params to the RUNNING daemon now (the Apply button).

        Serializes against start/stop/restart and another Apply on the same band via the band
        lifecycle lock (re-entrant per thread). TRUTHFUL: `ok=True` only when every attempted set
        is applied; `ok=False` for total failure; a `PARTIAL` `ok=False` when some fail. The
        structured `data` reports band + attempted/applied/failed/confirmed/sent-unconfirmed keys —
        radio params the daemon does not echo are reported SENT (never claimed as read-back
        confirmed). Valid settings were persisted first (by the caller); a live-apply failure never
        rolls that back — `data['persisted']` says so."""
        from . import daemon_params, reslock
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

    def stop(self, target: str, apply: bool = False, cascade: bool = False,
             band: str = "", release_daemon: bool = True, bulk_ctx=None) -> ActionResult:
        """Public, LOCKED entry — acquires the lifecycle bundle (incl. dependents on
        cascade) so a DIRECT call gets the same coordination as CLI/web. `release_daemon=False`
        is the INTERNAL cascade path: a client stopped as part of a daemon cascade must not itself
        release the daemon (the outer daemon stop is the sole owner of daemon teardown)."""
        if (_r := self._controller_refusal(target)) is not None:
            return _r
        from . import reslock
        if bulk_ctx is not None:
            ctx_err = self._bulk_ctx_error(bulk_ctx, set())
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
        # stop includes its --radio both collateral; separate per-band instances are unaffected).
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
                results.append(cr); own_results.append(cr)
                details.append(f"  [blocked] {comp.id}: a dependent is still running — daemon left up")
                continue
            if comp.units and not comp.run_argv:
                # Externally supervised: LHPC cannot verify the stop -> MANUAL_REQUIRED.
                cr = CompResult(component=comp.id, stack=target, action="stop",
                    outcome=Outcome.MANUAL_REQUIRED,
                    summary=f"system service — stop as root: sudo systemctl stop {comp.units[0].name}")
                results.append(cr); own_results.append(cr)
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
            results.append(cr); own_results.append(cr)
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
        try:
            with self._config_stable():
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

    def build(self, target: str, apply: bool = False, bulk_ctx=None,
              on_component_log=None) -> ActionResult:
        if (_r := self._controller_refusal(target)) is not None:
            return _r
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
        ctx_err = self._bulk_ctx_error(bulk_ctx, src_paths)
        if ctx_err:
            return ActionResult(False, f"Refusing to build '{target}': {ctx_err}")
        try:
            with self._source_operation_guard(src_paths, op="build"):
                from . import bulk as bulk_mod
                details = []
                ok = True
                run_id = getattr(bulk_ctx, "run_id", "") if bulk_ctx else ""
                for _, comp in buildable:
                    # BULK: run-specific log base + DURABLE ordered registration BEFORE the
                    # build runs, so the live stream shows only this run's own logs (the
                    # file does not yet exist under a run-specific name -> no prior content).
                    log_base = None
                    if run_id and on_component_log is not None:
                        log_base = bulk_mod.component_log_base(run_id, f"build-{comp.id}")
                        n = len(comp.build_steps)
                        for i in range(n):
                            fn = f"{log_base}-{i}.log" if n > 1 else f"{log_base}.log"
                            title = (f"{comp.name} — Build log (step {i + 1}/{n})"
                                     if n > 1 else f"{comp.name} — Build log")
                            on_component_log(title, fn)
                    res = life.build(comp, log_base=log_base)
                    ok = ok and res.ok
                    details.append(f"  [{res.state.value}] build {comp.id} "
                                   f"(rc {res.returncode}, log {res.log_path})")
                    if not res.ok:
                        details.extend(f"      {ln}" for ln in res.tail[-6:])
        except SourceTxnBlocked as blocked:
            return ActionResult(False, f"Build blocked for '{target}': {blocked}",
                                next_commands=[f"lhpc status {target}"])
        except reslock.ResourceBusy as busy:
            return ActionResult(False, f"Build blocked for '{target}': {busy}",
                                next_commands=[f"lhpc status {target}"])
        self.prune_logs()
        return ActionResult(ok, f"Build {'succeeded' if ok else 'FAILED'} for '{target}'.",
                            details=details, next_commands=["lhpc status " + target])

    def log_tail(self, target: str, lines: int = 300, job: str | None = None,
                 band: str = ""):
        """Raw (path, lines) for `target`'s log — for the live web log view.

        With `job` (a logs/<name>.log filename) it tails that specific job log
        (e.g. a build/test run); otherwise it tails the component's process log —
        `band` selects the instance of a band-scoped component (empty = newest).
        """
        from .jobs import tail_log
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

    def webserver_log_tail(self, source: str = "error", lines: int = 300):
        """Raw (path, lines) for the LHPC-managed nginx front-end's on-disk logs. `source`
        selects the access or (default) error log — an unknown selector degrades to the error
        log so it can never name an arbitrary path. Read-only: a bounded, O_NOFOLLOW disk tail
        (same guard as `log_tail`), no systemctl/network probe."""
        from . import runtime_fs, webserver as _ws
        const = _ws._ACC_LOG if source == "access" else _ws._ERR_LOG
        try:
            n = max(1, min(int(lines), 5000))             # clamp to a sane bounded range
        except (TypeError, ValueError):
            n = 300
        try:
            p = self._paths.under(*const)
        except PathContainmentError:
            return "", []
        if p.is_symlink() or (p.exists() and not p.is_file()):
            return str(p), []
        return str(p), runtime_fs.tail(self._paths, p, n)

    def controller_log_tail(self, source: str = "web", lines: int = 300):
        """Raw (path, lines) for the controller's OWN process logs — the lhpc-web / lhpc-selfupdate
        units now log to on-disk FILES under logs/ (StandardOutput=append:), so the GUI reads them
        the same way as the nginx logs (the box's user journal is not reliably populated). `source`
        selects the file: 'selfupdate' -> lhpc-selfupdate.log, anything else -> lhpc-web.log. Same
        containment-safe, O_NOFOLLOW, bounded read as `webserver_log_tail`; never raises into GET."""
        from . import runtime_fs, updater_units
        const = updater_units.HELPER_LOG_REL if source == "selfupdate" else updater_units.WEB_LOG_REL
        try:
            n = max(1, min(int(lines), 5000))                 # clamp to a sane bounded range
        except (TypeError, ValueError):
            n = 300
        try:
            p = self._paths.under(*const)
        except PathContainmentError:
            return "", []
        if p.is_symlink() or (p.exists() and not p.is_file()):
            return str(p), []
        return str(p), runtime_fs.tail(self._paths, p, n)

    def spawn_web_job(self, op: str, target: str, source: str = "pinned"):
        """Spawn detached build/test/install job(s) for `target`; return
        (job_log_name, error). The web redirects to a live view of the log."""
        life = self._lifecycle()
        # Install runs as one logged subprocess so the operator sees clone output
        # live and when it finishes (the dash redirects to this log).
        if op == "install":
            import sys
            # Reject an invalid source selector — never silently fall back to 'dev'.
            if source not in self.SOURCE_CHOICES:
                return None, (f"invalid source '{source}' (choose "
                              f"{', '.join(self.SOURCE_CHOICES)})")
            argv = [sys.executable, "-m", "lhpc", "install", target, "--yes",
                    "--source", source]
            ln, pid = life.spawn_job(f"install-{target}", argv, str(self._paths.runtime_root))
            if not ln or not pid:
                return None, f"could not start install for '{target}'"
            err = self._track_or_terminate(life, ln, pid, target, "install")
            if err:
                return None, err            # spawned-but-untracked: reported, not silent
            return ln, None
        items, err = self._resolve(target)
        if err:
            return None, err
        # Build a shell-free launcher per component (resolves pkg-config + env, runs
        # the structured steps). A stack may build several components.
        from . import commands, reslock, runtime_fs
        inst_index_key = self._installer()._index_key()
        runtime = str(self._paths.runtime_root)
        runtime_fs.ensure_dir(self._paths, self._paths.under("state", "locks"))
        jobs = []
        for _, c in items:
            if op == "build" and c.build_steps:
                jobs.append((c, list(c.build_steps), f"build-{c.id}"))
            elif op == "test" and c.test_argv:
                jobs.append((c, [{"argv": list(c.test_argv)}], f"test-{c.id}"))
        if not jobs:
            return None, f"nothing to {op} for '{target}'"
        s = self.stack(target)
        main_id = s.main if s else None
        first = main_log = None
        errors: list[str] = []
        post_dir = self._paths.under("state", "jobs")   # containment-checked
        runtime_fs.ensure_dir(self._paths, post_dir)
        import sys, os, time as _time
        for c, steps, name in jobs:
            src = str(life.source_dir(c))
            # The launcher holds the canonical SOURCE-PATH lock for its whole lifetime,
            # so an update/uninstall of the same checkout cannot race a running job.
            lock_paths = ([str(reslock.lock_file_path(self._paths,
                          reslock.source_lock_key(c.source.path)))] if c.source else [])
            # P0.3: index-to-source handoff — the launcher holds the source-transaction
            # INDEX lock and verifies NO unresolved journal before acquiring the source
            # lock(s), so a detached job cannot race past a retained journal.
            index_lock = str(reslock.lock_file_path(self._paths, inst_index_key))
            txn_dir = str(self._paths.under("state", "source-txn"))
            # Fail-closed: a missing/empty @file secret, bad env, or unresolved token
            # blocks the build cleanly (no silent empty value, no shell).
            try:
                script = commands.render_build_launcher(steps, runtime, src, lock_paths,
                                                        index_lock=index_lock, txn_dir=txn_dir)
            except commands.CommandError as exc:
                return None, f"cannot {op} '{c.id}': {exc}"
            # Unique runtime-owned launcher name so concurrent jobs never overwrite
            # each other's spec; written atomically THROUGH the safe runtime FS.
            uid = f"{name}-{os.getpid()}-{_time.monotonic_ns()}"
            launcher = runtime_fs.write_launcher(self._paths, post_dir / f"{uid}.py", script)
            ln, pid = life.spawn_job(name, [sys.executable, str(launcher)], src)
            if not ln or not pid:
                # Never silently continue when a component job cannot spawn.
                errors.append(f"could not start {op} for '{c.id}'")
                continue
            terr = self._track_or_terminate(life, ln, pid, c.id, op)
            if terr:
                errors.append(terr)
                continue
            if first is None:
                first = ln
            if c.id == main_id:
                main_log = ln
        self.prune_logs()
        if errors and (main_log or first) is None:
            return None, "; ".join(errors)             # nothing usable started
        if errors:
            return (main_log or first), "; ".join(errors)   # partial: surface the failures
        return (main_log or first), None

    # Bounded log retention (no background supervisor — runs at operation boundaries).
    LOG_RETENTION = 200          # keep at most this many *.log files
    LOG_RETENTION_BYTES = 64 * 1024 * 1024   # …and at most this many bytes total

    def prune_logs(self) -> int:
        """Delete the oldest runtime logs beyond a bounded count/byte budget, NEVER
        touching a log that belongs to an active job (so live evidence is preserved)
        and never following a symlink. Returns the number removed. Called at operation
        boundaries; there is no background cleaner."""
        from .paths import PathContainmentError
        from . import bulk as bulk_mod
        protected = {j.get("log") for j in self.active_jobs() if j.get("log")}
        protected = {f"{n}.log" for n in protected} | {n for n in protected}
        # Protect the LIVE bulk run's own component build/test logs (requirement #7): its
        # durable descriptors are this run's evidence — a mid-run prune must never remove a
        # component log the stream still owns, even if the retention budget is exceeded.
        # Retired runs' logs carry no such protection and age out normally.
        try:
            st = self.bulk_status()
        except Exception:                        # noqa: BLE001 — pruning must never fail
            st = None
        bulk_prefix = ""
        if st and not st.get("unsafe") and st.get("state") in ("preparing", "running"):
            try:
                # FULL-run-id component-log prefix (`install-all-<run32>-`) — matches the
                # exact names the run writes; the 8-hex run-log prefix would not.
                bulk_prefix = bulk_mod.component_log_prefix(st["run_id"])
            except (ValueError, KeyError, TypeError):
                bulk_prefix = ""
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
            if name in protected or (bulk_prefix and name.startswith(bulk_prefix)):
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
                          ident: dict | None = None) -> bool:
        """Record a build/test job with a COMPLETE, PID-reuse-resistant identity. Returns
        True only when a complete identity was captured AND the marker was durably
        persisted; False means the just-spawned process is UNTRACKED and the caller must
        terminate it (no silent orphan). `ident`, when given, is the identity captured
        immediately after spawn — used as-is (never re-read a possibly-reused pid)."""
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
        body = (f'launch_id = "{slug}"\npid = {pid}\n'
                f'starttime = {int(ident["starttime"])}\n'
                f'pgid = {int(ident["pgid"])}\nsid = {int(ident["sid"])}\n'
                f'exec = "{ident["exec"]}"\nargv_fp = "{ident["argv_fp"]}"\n'
                f'argv_len = {int(ident["argv_len"])}\n'
                f'target = "{target}"\nop = "{op}"\nlog = "{slug}"\n')
        try:
            runtime_fs.write_marker(self._paths, path, body)
            return True
        except (OSError, PathContainmentError, validators.ValidationError):
            return False

    def _track_or_terminate(self, life, log_name: str, pid: int, cid: str, op: str) -> str:
        """Persist a job marker; if it cannot be persisted, terminate the (identity-
        verified) spawned session so it never leaks as an untracked orphan. Returns ""
        on success, else a visible error describing the outcome."""
        # Capture the identity IMMEDIATELY after spawn and use exactly that for both the
        # marker and any cleanup — never re-read a possibly-reused pid as the original job.
        ident = procident.proc_identity(pid)
        if self._write_job_marker(log_name, pid, cid, op, ident=ident):
            return ""
        killed = life._terminate_unobserved(pid, ident)
        if killed:
            return (f"{op} '{cid}' spawned but its job marker could not be persisted; "
                    "the process was terminated (not left orphaned).")
        return (f"{op} '{cid}' spawned but its job marker could not be persisted AND the "
                "process could NOT be confirmed stopped — ORPHAN RISK; check `ps` and kill it.")

    # A `.job` marker is a tiny TOML (pid + identity fields); anything larger is untrusted
    # diagnostic evidence, never read in full or treated as a live job.
    _JOB_MARKER_MAX = 64 * 1024

    def active_jobs(self) -> list[dict]:
        """Build/test jobs whose ORIGINAL process is still alive (identity-verified).
        A reused PID, a malformed marker, or a symlinked marker is never treated as a
        live job; proven-finished markers are cleaned through the safe API.

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
        try:
            entries = runtime_fs.scandir_nofollow(self._paths, d)
        except (OSError, PathContainmentError):
            return []
        out = []
        for name, is_link in sorted(entries):
            if is_link or not name.endswith(".job"):
                continue
            f = d / name
            # DESCRIPTOR-SAFE gate: regular file, and small enough to be a real marker.
            # An oversized marker is untrusted (a bounded read could truncate it to a
            # still-parseable prefix and be wrongly trusted) — skip it, do not read it.
            stt = runtime_fs.stat_leaf_nofollow(self._paths, f)
            if stt is None or not _stat.S_ISREG(stt.st_mode) \
                    or stt.st_size > self._JOB_MARKER_MAX:
                continue                        # non-regular/oversized -> untrusted, retain
            try:
                # BOUNDED, non-blocking, no-follow, regular-only read (never blocks on a
                # FIFO, never follows a swapped/symlinked marker, never reads unbounded).
                raw = tomllib.loads(runtime_fs.read_text_regular(
                    self._paths, f, max_bytes=self._JOB_MARKER_MAX))
                pid = int(raw["pid"])
            except (OSError, PathContainmentError, KeyError, ValueError,
                    tomllib.TOMLDecodeError):
                continue                        # malformed/unsafe -> retain for diagnosis
            if procident.identity_matches(raw, pid):
                out.append({"log": raw.get("log"), "target": raw.get("target"),
                            "op": raw.get("op"), "stack": self.stack_of(raw.get("target", ""))})
            else:
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

    def test(self, target: str, tx: bool = False,
             apply: bool = False, bulk_ctx=None, on_component_log=None) -> ActionResult:
        """Run host tests (RX-safe) or a bounded one-frame TX test (`tx=True`)."""
        if (_r := self._controller_refusal(target)) is not None:
            return _r
        items, err = self._resolve(target)
        if err:
            return ActionResult(False, err, next_commands=["lhpc list"])
        ctx_err = self._bulk_ctx_error(bulk_ctx,
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
            src_paths = sorted({c.source.path for _, c in items if c.source and c.test_argv})
            from . import bulk as bulk_mod
            run_id = getattr(bulk_ctx, "run_id", "") if bulk_ctx else ""
            try:
                with self._source_operation_guard(src_paths, op="host-test"):
                    for _, comp in items:
                        # An integration test needs the stack already running. A bulk/install-all
                        # sweep builds without starting, so DEFER it there (never a false failure);
                        # an explicit `lhpc test` (no bulk_ctx) runs it against the running stack.
                        if comp.test_argv and comp.test_requires_running and bulk_ctx is not None:
                            details.append(f"  [deferred] {comp.id}: needs the running stack "
                                           f"(run `lhpc test {target}` after starting it)")
                            continue
                        # BULK: run-specific test-log base + DURABLE ordered registration
                        # BEFORE the test runs (same guarantees as the build path).
                        log_base = None
                        if run_id and on_component_log is not None and comp.test_argv:
                            log_base = bulk_mod.component_log_base(run_id, f"test-{comp.id}")
                            on_component_log(f"{comp.name} — Test log", f"{log_base}.log")
                        res = life.host_test(comp, log_base=log_base)
                        if res is None:
                            details.append(f"  [skip] {comp.id}: no host test")
                            continue
                        ok = ok and res.ok
                        details.append(f"  [{res.state.value}] {comp.id} "
                                       f"(rc {res.returncode}, log {res.log_path})")
            except SourceTxnBlocked as blocked:
                return ActionResult(False, f"Host test blocked for '{target}': {blocked}",
                                    next_commands=[f"lhpc status {target}"])
            except reslock.ResourceBusy as busy:
                return ActionResult(False, f"Host test blocked for '{target}': {busy}",
                                    next_commands=[f"lhpc status {target}"])
            return ActionResult(ok, f"Host test {'passed' if ok else 'FAILED'} for '{target}'.",
                                details=details, next_commands=[f"lhpc status {target}"])

        # TX-capable test — explicit, gated, bounded. TX is a DAEMON operation; the
        # band(s) come from the target's components (or, for the daemon itself, the
        # bands it is serving), and we only test bands the daemon actually serves.
        wanted = []
        for _, c in items:
            for b in ([c.band] if c.band else []) + list(c.bands):
                if b and b not in wanted:
                    wanted.append(b)
        if not wanted:
            wanted = list(self.RADIO_BANDS)
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
        details = []
        ok = True
        for band in bands:
            res = life.run_daemon_tx_test(band, payload)
            ok = ok and res.ok
            details.append(f"  [{'ok' if res.ok else 'fail'}] band {res.band}: {res.detail} "
                           f"(TXOK {res.txok_before}->{res.txok_after})")
        return ActionResult(ok, f"TX test {'PASSED' if ok else 'did not confirm'} for '{target}'.",
                            details=details, next_commands=[f"lhpc status {target}", f"lhpc logs {target}"])

    # ---- unified action dispatch (used by the web control interface) -----

    # Web-exposed actions -> the same gated service methods the CLI calls.
    WEB_ACTIONS = ("install", "update", "uninstall", "start", "stop", "restart",
                   "build", "test", "test-tx", "clean")

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
        """Bands the operator may choose for `target` (empty = single fixed band). Owner-stack
        scoped, so a direct component target uses its stack's band choices."""
        s = self._owner_stack(target)
        for c in (s.components if s else ()):
            if c.bands:
                return c.bands
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
            for kind, p in ([("run", p) for p in c.run_params]
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
            rows = []
            for kind, p in ([("run", p) for p in c.run_params]
                            + [("file", p) for p in (c.config_file.params if c.config_file else ())
                               if not getattr(p, "hidden", False)]):
                default = self._op_subst(dict(p.band_defaults).get(cfg_band or band, p.default))
                value = self._resolved_param_value(target, kind, c.id, p.name, cfg_band)
                field = self._config_field(target, kind, c.id, p.name)
                key = self._param_key(target, kind, c.id, p.name)
                is_id = bool(idf and idf["comp"] == c.id and idf["name"] == p.name
                             and idf["kind"] == kind)
                rows.append(self._param_row(p, field, kind, value, value, default, is_id,
                                            c.name, key, c.id))
            if rows:
                groups.append({"id": c.id, "name": c.name, "is_dep": c.id != main_id,
                               "optional": bool(c.optional),
                               "rule_before": False, "rule_after": False, "rows": rows})
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

    def _run_migration(self, candidates: list, from_head: str) -> tuple:
        """Migrate one PROVEN transition's candidates race-safely. `from_head` is the TRANSITION
        record's pre-update commit; a key is deleted ONLY when its current stored value — canonicalised
        with the OLD (pre-update) parameter definition — equals that param's OLD default, both parsed
        from the manifest at `from_head` (`_prove_candidate`). The candidate's own `from_head`/`expected`
        never select the manifest or authorise deletion. Returns (migrated_count, remaining_candidates);
        an unprovable candidate is kept pending (never raw-value-deleted); a file whose write FAILS
        keeps all its candidates for retry."""
        from collections import defaultdict
        from .paths import PathContainmentError
        by_file: dict = defaultdict(dict)                                # (stack, band) -> {key: old_default}
        meta: dict = {}                                                  # (stack, band, key) -> (cand, old_param)
        remaining: list = []
        for cand in candidates:
            proven = self._prove_candidate(cand, from_head)
            if proven is None:
                remaining.append(cand)                                   # unprovable -> keep pending, never delete
                continue
            old_param, old_default = proven
            by_file[(cand["stack"], cand["band"])][cand["key"]] = old_default
            meta[(cand["stack"], cand["band"], cand["key"])] = (cand, old_param)
        migrated = 0
        for (stack_id, cfg_band), expected in by_file.items():
            def _matches(key, raw, exp, _s=stack_id, _b=cfg_band):
                _cand, old_param = meta[(_s, _b, key)]
                return self._canon_value(old_param, raw) == exp          # BOTH sides: OLD param semantics
            try:
                migrated += conditional_clear_stack_config(self._paths, stack_id, cfg_band,
                                                           expected, _matches)
            except (OSError, ConfigError, PathContainmentError, ValueError):
                remaining.extend(meta[(stack_id, cfg_band, k)][0] for k in expected)   # keep pending
        if migrated:
            self._invalidate_config()
        return migrated, remaining

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
        UNIQUE flat legacy value, else the per-band/manifest default (operator `{callsign}`/
        `{locator}` tokens substituted in DEFAULTS only — a saved value is used verbatim). An
        ambiguous flat legacy value is NOT applied here (the start blocks; see `_config_ambiguity`)."""
        cfg_band = self._config_band(target, band)
        owner = self._owner_stack(target)
        stored = load_stack_config(self._paths, self._owner_stack_id(target), cfg_band)
        run_counts, _ = self._owner_param_counts(owner)
        op = self.config().operator

        def _op_subst(v: str) -> str:
            return (str(v).replace("{callsign}", op.callsign or "")
                          .replace("{locator}", op.locator or ""))

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
        """Unsatisfied system dependencies (e.g. -dev packages) for a stack's
        components, with the command to install each. Empty = all satisfied."""
        return [d for d in self.system_deps(target) if not d["satisfied"]]

    def system_deps(self, target: str) -> list[dict]:
        """ALL declared system requirements for a stack (dev packages, headers,
        device nodes) with their satisfied state + install command — for the app
        tab ('Installed' vs a copyable install command) and the install gate."""
        life = self._lifecycle()
        s = self.stack(target)
        out, seen = [], set()
        for c in (s.components if s else ()):
            missing = life.missing_requirements(c)
            for req in c.requires:
                key = req.install or req.cmd or req.check_file
                if key and key not in seen:
                    seen.add(key)
                    out.append({"what": req.note or req.cmd or req.check_file,
                                "install": req.install,
                                "satisfied": req not in missing})
        return out

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

    def update_status(self, comp) -> str:
        """Is the installed source up to date with its GitHub remote branch?
        Returns "up-to-date", "update-available", or "unknown" (no remote/git/net).
        Uses a bounded `git ls-remote` — no fetch, no mutation."""
        if comp is None or comp.source is None or not comp.source.remote:
            return "unknown"
        src = self._paths.resolve_source(comp.source.path)
        if not src.is_dir():
            return "unknown"
        remote = self.config().remotes.get(comp.id) or comp.source.remote
        # Revalidate the (possibly hand-edited) remote IMMEDIATELY before git — an invalid
        # runtime override must never reach `git ls-remote`; treat it as unknown, not a check.
        from . import validators
        try:
            remote = validators.remote_url(remote or "", field="remote")
        except validators.ValidationError:
            return "unknown"
        if not remote:
            return "unknown"
        ref = comp.source.branch or "HEAD"
        run = self._system.runner.run
        rem = run(["git", "ls-remote", remote, ref], timeout=12.0)
        if rem.returncode != 0 or not rem.stdout.strip():
            return "unknown"
        remote_sha = rem.stdout.split()[0]
        loc = run(["git", "-C", str(src), "rev-parse", "HEAD"], timeout=5.0)
        if loc.returncode != 0 or not loc.stdout.strip():
            return "unknown"
        return "up-to-date" if loc.stdout.strip() == remote_sha else "update-available"

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

    RADIO_BANDS = ("433", "868")

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
                + ";B:" + ",".join(booting))

    def _build_artifact(self, comp):
        """Relative path of the built binary (explicit `bin`, else the process
        exec_name), or None when the component compiles nothing."""
        if not comp.build_cmd:
            return None
        rel = comp.bin or (comp.process.exec_name if comp.process else "")
        return rel or None

    def is_built(self, comp) -> bool:
        """True if the component needs no build, or its built artifact is present.
        The artifact path may carry run-param placeholders (e.g. {env} for the
        firmware build dir), substituted from the stack's saved config."""
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

    @staticmethod
    def _client_interfaces(status) -> list[dict]:
        """User-facing interfaces a client connects to (KISS TCP, web UIs, serial
        PTYs) that are currently present — NOT internal transport sockets."""
        if status is None:
            return []
        out = []
        for obs in status.endpoints:
            sp = obs.spec
            if not getattr(sp, "client", False) or not obs.present:
                continue
            link = f"{sp.scheme}://{sp.address}" if sp.scheme in ("http", "https") else ""
            out.append({"label": sp.description or sp.address, "address": sp.address,
                        "scheme": sp.scheme or "tcp", "link": link})
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
        for band in self.RADIO_BANDS:
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
                          "interfaces": ([] if booting else self._client_interfaces(live.get(c.id)))})
                entry = {"id": s.id, "name": s.name, "main": s.main, "components": comps,
                         "multi_band": multi}
                main_comp = s.component(s.main)

                # Interactive stacks (chat/voice): in the dropdown until the operator
                # "runs" them; after that a dismissable command block is shown in the
                # band column they were started on.
                if main_comp is not None and main_comp.interactive:
                    mark_band = self.interactive_band(s.id)        # None if not active
                    active = mark_band is not None or running_up
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
        return out

    def log_running(self, target: str, job: str | None = None) -> bool:
        """Whether the process behind a log is still alive: for a build/test `job`
        it checks the job marker's pid; for a process log it checks the target's
        main component run-state."""
        if job:
            import tomllib
            f = self._jobs_dir() / (job + ".job")
            try:
                # No-follow read (no check-then-open): a symlinked marker -> OSError -> False.
                raw = tomllib.loads(runtime_fs.read_text(self._paths, f))
                # FULL identity match (PID-reuse-safe), never bare PID liveness: a recycled
                # pid running an unrelated process is not this job.
                return procident.identity_matches(raw, int(raw["pid"]))
            except (OSError, KeyError, ValueError, tomllib.TOMLDecodeError):
                return False
        s = self.stack(target)
        cid = s.main_component.id if (s and s.main_component) else target
        for ss in self.build_snapshot().stacks:
            st = ss.components.get(cid)
            if st is not None:
                return st.run_state in (RunState.RUNNING, RunState.DEGRADED)
        return False

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
        # {callsign}/{locator} into a run/pre command (e.g. iGate) — not the daemon.
        # Stacks that edit their callsign in their own config (operator_box=false) don't show the
        # shared Operator box — the callsign lives in their config/run params instead.
        uses_operator = (s is not None and s.operator_box) and any(
            tok in (c.run_cmd or "") or tok in (c.pre_cmd or "")
            or any(tok in (p.default or "") for p in c.run_params)
            or any(tok in (p.default or "") for p in (c.config_file.params if c.config_file else ()))
            for c in members for tok in ("{callsign}", "{locator}"))
        sources = [{"id": c.id, "name": c.name,
                    "remote": cfg.remotes.get(c.id) or c.source.remote,
                    "default": c.source.remote,
                    "overridden": c.id in cfg.remotes}
                   for c in members if c.source and c.source.remote]
        bands = self.stack_bands(target)
        cfg_band = self._config_band(target, band)
        view = {
            "operator": ({"callsign": cfg.operator.callsign, "locator": cfg.operator.locator}
                         if uses_operator else None),
            # Each component carries its OWN field-name map (`fields`) and its OWN
            # component-scoped value map (`values`) so duplicate run/file names across components
            # never share a form field nor flatten into one value.
            "components": [{"id": c.id, "name": c.name, "params": list(c.run_params),
                            "fields": {p.name: self._config_field(target, "run", c.id, p.name)
                                       for p in c.run_params},
                            "values": {p.name: self._resolved_param_value(target, "run", c.id,
                                                                          p.name, cfg_band)
                                       for p in c.run_params}}
                           for c in comps],
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
            live_band = band if band in self.RADIO_BANDS else self.RADIO_BANDS[0]
            view["bands"] = list(self.RADIO_BANDS)
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

    def save_config(self, target: str, values: dict,
                    callsign: str | None = None, locator: str | None = None,
                    band: str = "") -> ActionResult:
        """Save operator identity (if supplied) and the stack's run parameters as ONE
        all-or-recoverable transaction (via `save_config_bundle`): if the stack config fails to
        validate/persist, operator identity is NOT partially written."""
        return self.save_config_bundle(target, values=values, callsign=callsign,
                                       locator=locator, band=band)

    def save_config_bundle(self, target: str, *, values: dict | None = None,
                           callsign: str | None = None, locator: str | None = None,
                           band: str = "", remotes: dict | None = None) -> ActionResult:
        """Validate the WHOLE Config-page submission, then persist it as ONE
        all-or-recoverable transaction (local.toml + the per-stack config file).
        Nothing is written unless every value validates; unknown fields are
        rejected; a malformed local.toml is preserved. (P0: replaces the previous
        per-remote sequential writes.)"""
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
            v = str(value)
            if v.strip() == "":
                # BLANK = clear the override (any kind), same rule as file params above.
                clean_params.append(("r", c, p, v))
                continue
            try:
                clean_params.append(("r", c, p, validators.validate_param(p, v)))
            except validators.ValidationError as exc:
                errors.append(str(exc))
        op_change = callsign is not None or locator is not None
        cs = loc = None
        if op_change:
            try:
                cs = validators.callsign(callsign or "", field="callsign").upper()
                loc = validators.locator(locator or "", field="locator")
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
            def _render_local(p, opc=op_change, _cs=cs, _loc=loc, patch=remote_patch,
                              do_remotes=(remotes is not None)):
                # Read the LATEST local.toml INSIDE the transaction lock and MERGE — preserving
                # every unrelated table and every other component's remote override. A malformed
                # local.toml raises here -> the transaction rolls back and preserves it.
                existing = _load_runtime_toml(self._paths, local_path)   # no-follow; ConfigError on corrupt
                data = dict(existing)                     # keep root scalars + every other table
                if opc:
                    # Patch ONLY callsign/locator — preserve any other [operator] scalar keys.
                    _patch_local_table(data, "operator", {"callsign": _cs, "locator": _loc})
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
            apply_config_transaction(self._paths, targets)
        except ConfigError as exc:
            return ActionResult(False, f"Config not saved for '{target}'.", details=[str(exc)])
        self._invalidate_config()               # saved operator/remotes visible immediately
        return ActionResult(True, f"Config saved for '{target}'.",
                            details=self._apply_hints(target, modes) + remote_notes,
                            next_commands=[f"lhpc stack start {target}"])

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

    def file_params_for(self, target: str):
        """Every file-config FileParam of a stack (for the web to collect `pf_<name>` inputs)."""
        return [p for c in self._file_config_components(target) for p in c.config_file.params]

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

    # -- Start-confirm "Stack parameters" panel + CALL/node enforcement ----------

    # A run/file param whose validator marks it the stack's operator identity: a "callsign"
    # validator => LICENSED (refuse empty / N0CALL); a "node" validator => UNLICENSED (refuse only
    # empty, the default name is accepted).
    _IDENTITY_ENFORCE = {"callsign": "licensed", "node": "unlicensed"}

    def _op_subst(self, text: str) -> str:
        op = self.config().operator
        return (str(text).replace("{callsign}", op.callsign or "")
                         .replace("{locator}", op.locator or ""))

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
            for kind, p in ([("run", p) for p in c.run_params]
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
            for kind, p in ([("run", p) for p in c.run_params]
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
                next_commands=[f"lhpc config {target}"])
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
        from pathlib import Path
        op = self.config().operator
        runtime = str(self._paths.runtime_root)
        cfg_band = self._config_band(target, band)
        # Validate operator identity; fall back to safe placeholders if invalid so a
        # corrupted local.toml can never inject into a generated config file.
        try:
            call = validators.callsign(op.callsign or "N0CALL") or "N0CALL"
        except validators.ValidationError:
            call = "N0CALL"
        try:
            loc = validators.locator(op.locator or "")
        except validators.ValidationError:
            loc = ""

        def subst(text: str) -> str:
            return (text.replace("{callsign}", call)
                        .replace("{locator}", loc)
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
        runtime = str(self._paths.runtime_root)
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

    SOURCE_CHOICES = ("pinned", "dev", "stable")   # pinned = production-safe default

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

    # ---- self-update (lhpc's own version/head/upstream) ----------------------

    def self_update_status(self) -> dict:
        """Cached, NETWORK-FREE self-update view for the footer/pages (reads the state marker only —
        never git, never network, safe for GET)."""
        from . import selfupdate
        return selfupdate.status_view(self._paths)

    def controller_status(self) -> dict | None:
        """Cached-only presentation of the controller (its OWN checkout) as a distinct
        NON-stack row for CLI status + the dashboard. Returns None if no controller is
        declared. Reads the cached self-update envelope ONLY — NO git, NO network, NO live
        identity check, NO blocking read (safe for GET). Points to `lhpc self-update`."""
        spec = self.controller()
        if spec is None:
            return None
        from . import selfupdate
        view = selfupdate.status_view(self._paths)        # cached envelope only
        return {
            "id": spec.id,
            "display_name": spec.display_name,
            "branch": spec.branch,
            "version": view.get("version", ""),
            "head_short": view.get("head_short", ""),
            "update_available": bool(view.get("update_available", False)),
            "identity": view.get("identity"),             # {ok, reason, checked_at} or None
            "self_update_cmd": "lhpc self-update",
        }

    def self_update_check(self) -> ActionResult:
        """Explicit upstream freshness check (NETWORK: `git fetch`) — refreshes the cached marker so
        the footer/pages reflect it. Serialized with apply through the self-update lock: if an apply is
        in progress it DEFERS (nonfatal) with the last cached status instead of racing its refs/cache.
        Under the lock it applies the SAME pure recovery-state gate as apply (`classify_journal`)
        BEFORE `refresh_cache`/`check_upstream`/fetch/cache write: an unreadable/corrupt/unsafe OR
        recovery-blocked journal blocks the check with NO fetch and NO cache/journal/config/source
        mutation. Fail-soft."""
        from . import selfupdate
        try:
            with selfupdate.update_lock(self._paths):
                status, _env, _head = selfupdate.classify_journal(self._paths, self._system)  # BEFORE any fetch
                if status == "blocked":
                    return ActionResult(False, "Self-update check blocked: the migration journal is "
                                        "unreadable, corrupt or unsafe. No upstream check was made — "
                                        "recovery needed (inspect state/selfupdate-migrate.json).",
                                        data={"journal_corrupt": True,
                                              **selfupdate.status_view(self._paths)})
                if status == "recovery_required":
                    return ActionResult(False, "Self-update check blocked: the checkout is at an "
                                        "unexpected commit for a recorded migration transition. No "
                                        "upstream check was made — recovery required (inspect "
                                        "state/selfupdate-migrate.json).",
                                        data={"recovery_required": True,
                                              **selfupdate.status_view(self._paths)})
                # Embed the LIVE controller-identity verdict into the SAME atomic envelope
                # write (a separate field could be dropped by a later refresh). GET/status
                # then renders the cached verdict only.
                identity = self.controller_identity_live() if self.controller() else None
                view = selfupdate.refresh_cache(self._system, self._paths, identity=identity)
        except selfupdate.SelfUpdateBusy:
            view = selfupdate.status_view(self._paths)        # no fetch, no cache write
            return ActionResult(True, "A self-update is in progress — showing the last known status.",
                                data={**view, "deferred": True})
        except selfupdate.UpdateLockError:
            return ActionResult(False, "Could not check upstream (unsafe runtime state).",
                                data=selfupdate.status_view(self._paths))
        if not view["is_git"]:
            return ActionResult(False, "Self-update is unavailable (lhpc is not a git checkout).",
                                data=view)
        if not view["have_upstream"]:
            return ActionResult(False, f"Could not reach upstream: {view.get('upstream_error', '')}.",
                                data=view)
        if view["update_available"]:
            return ActionResult(True, f"Update available — upstream {view['upstream_head_short']}"
                                f" (v{view['upstream_version'] or '?'}).", data=view)
        return ActionResult(True, "Up to date.", data=view)

    def self_update_apply(self, *, force: bool = False) -> ActionResult:
        """Apply the update as ONE serialized, fail-closed transaction (the interprocess self-update
        lock covers candidate capture, journal persistence, fetch/ref resolution, merge/reset/clean,
        cache writes, config migration and journal finalization). BLOCKED while an lhpc job is active;
        a concurrent apply returns 'busy' with zero mutation. A DIRTY tree is refused unless
        `force=True`. Legacy default-equal config is migrated to the new defaults only when the source
        transition it was captured against actually completed — recorded DURABLY before source changes
        and recovered from the journal after a crash. Cleanup failure on force is a truthful partial."""
        from . import selfupdate
        jobs = self.active_jobs()
        if jobs:
            return ActionResult(False, "An lhpc job is still running — finish it before self-updating.",
                                details=tuple(f"  {j.get('op', 'job')} {j.get('target', '')}"
                                              for j in jobs),
                                data={"blocked_by_jobs": True})
        # LIVE identity gate (recomputed here, NEVER trusting the cache): only a genuinely
        # UNSAFE self-hosted checkout (tampered layout: symlink / group-writable / wrong
        # branch-origin / repo mismatch) blocks apply before any mutation. A `not_applicable`
        # verdict (NOT self-hosted — a dev checkout or a plain/tangled deployment) does NOT
        # block: self-update proceeds via the normal `repo_root()` mechanism.
        if self.controller() is not None:
            idv = self.controller_identity_live()
            if idv.get("status") == "unsafe":
                return ActionResult(False, f"Self-update blocked: unsafe controller identity "
                                    f"({idv['reason']}). No changes were made.",
                                    data={"identity_unsafe": True, "identity": idv})
        # LOCK ORDER (fixed): controller-runtime EXCLUSIVE first (so the running web server —
        # which holds it SHARED — can never have its source mutated underneath it), THEN the
        # self-update lock. Both non-blocking; incompatible holders refuse promptly.
        try:
            with selfupdate.controller_runtime_lock(self._paths, exclusive=True):
                with selfupdate.update_lock(self._paths):
                    return self._self_update_locked(force)
        except selfupdate.ControllerRuntimeBusy:
            return ActionResult(
                False, "lhpc-web.service is running — stop it, update, then start it again.",
                details=["systemctl --user stop lhpc-web",
                         "lhpc self-update --apply",
                         "systemctl --user start lhpc-web",
                         "(or just click 'Update now' in the web console — it does all this)"],
                data={"web_running": True})
        except selfupdate.ControllerRuntimeLockError:
            return ActionResult(False, "Could not acquire the controller-runtime lock (unsafe runtime "
                                "state) — aborting without changes.", data={"lock_error": True})
        except selfupdate.SelfUpdateBusy:
            return ActionResult(False, "A self-update is already in progress — try again shortly.",
                                data={"busy": True})
        except selfupdate.UpdateLockError:
            return ActionResult(False, "Could not acquire the self-update lock (unsafe runtime state) "
                                "— aborting without changes.", data={"lock_error": True})

    def self_update_apply_operator(self, *, force: bool = False) -> ActionResult:
        """OPERATOR-CONTEXT `lhpc self-update --apply`: WARN-then-DO. If the managed web console is
        running it holds the controller-runtime lock SHARED, so an in-process apply refuses. Here —
        in an interactive operator shell — we STOP lhpc-web, apply, sync the venv on a real advance
        (mirroring the one-click helper), then START lhpc-web again. When the console is NOT running
        this is exactly the plain in-process apply (no service control). REFUSES inside a managed
        unit: a managed process must never drive systemctl."""
        import os as _os
        import sys as _sys
        from . import selfupdate, updater_units
        if _os.environ.get("INVOCATION_ID"):
            return ActionResult(False, "refusing to stop/start services from a managed unit — run "
                                "`lhpc self-update --apply` from an interactive operator shell")
        _S = 30.0
        act = self._system.runner.run(
            ["systemctl", "--user", "is-active", "--quiet", updater_units.WEB_UNIT], _S)
        web_active = (not getattr(act, "not_found", False)) and act.returncode == 0
        if not web_active:
            return self.self_update_apply(force=force)         # nothing to orchestrate
        stop = self._system.runner.run(["systemctl", "--user", "stop", updater_units.WEB_UNIT], _S)
        if getattr(stop, "not_found", False) or stop.returncode != 0:
            return ActionResult(False, "could not stop lhpc-web.service — stop it manually then retry",
                                details=["systemctl --user stop lhpc-web",
                                         "lhpc self-update --apply",
                                         "systemctl --user start lhpc-web"],
                                data={"stop_failed": True})
        restart_failed = False
        try:
            res = self.self_update_apply(force=force)
            # Venv sync on a real advance so the restarted console has any new deps (the managed
            # helper does the same). Only when we actually stopped the console (full managed-style
            # flow) — a no-op apply or the web-not-running path never touches the venv.
            if res.ok and not res.data.get("already"):
                root = selfupdate.repo_root()
                if root is not None:
                    pip = self._system.runner.run(
                        [_sys.executable, "-m", "pip", "install", "-e", str(root)],
                        self._PIP_SYNC_TIMEOUT_S)
                    if pip.returncode != 0:
                        detail = selfupdate._summarize_output(pip.stderr or pip.stdout)
                        res = ActionResult(False, "Update applied but the venv sync FAILED — run "
                                           f"{_sys.executable} -m pip install -e {root} manually."
                                           + (f" ({detail})" if detail else ""),
                                           data={**dict(res.data), "venv_sync_failed": True})
        finally:
            start = self._system.runner.run(["systemctl", "--user", "start", updater_units.WEB_UNIT], _S)
            restart_failed = getattr(start, "not_found", False) or start.returncode != 0
        if restart_failed:
            return ActionResult(res.ok, res.summary + "  WARNING: lhpc-web did NOT restart — run "
                                "`systemctl --user start lhpc-web`.",
                                details=tuple(res.details), data={**dict(res.data),
                                                                  "web_restart_failed": True})
        return res

    def _self_update_locked(self, force: bool) -> ActionResult:
        from . import selfupdate
        # 1. PURE classification of the untrusted envelope against the ACTUAL head (shared with the
        #    freshness check). A blocked/recovery-required state stops here BEFORE any fetch / source /
        #    cache / config / journal mutation.
        status, env, head_now = selfupdate.classify_journal(self._paths, self._system)
        if status == "blocked":
            return ActionResult(False, "Self-update blocked: the migration journal is missing-but-"
                                "present-unreadable, corrupt or unsafe. No changes were made — recovery "
                                "needed (inspect / remove state/selfupdate-migrate.json).",
                                data={"journal_corrupt": True})
        if status == "recovery_required":
            return ActionResult(False, "Self-update blocked: the checkout is at an unexpected commit for "
                                "a recorded migration transition. No changes were made — recovery "
                                "required (inspect state/selfupdate-migrate.json).",
                                data={"recovery_required": True})
        completed = env.get("completed") if env else None
        prepared = env.get("prepared") if env else None

        # 2. Reconcile a prior PREPARED attempt (classifier verified its ANCHOR + endpoint): its to_head
        #    reached -> promote (carrying the anchor txid); still at from_head -> the git never happened,
        #    so delete its anchor and drop it, keeping the prior completed intact.
        if prepared:
            if head_now == prepared["to_head"]:
                completed = self._promote(completed, prepared)          # keep the anchor for the completed slot
                self._write_envelope(completed, None)
            elif selfupdate.clear_migration_journal(self._paths):       # git never happened -> drop stale
                selfupdate.delete_anchor(self._system, prepared.get("txid"))   # prepared + its anchor

        # 3. Migrate the PRIOR completed transition FIRST, obtaining AUTHORITATIVE from_head + candidate
        #    payload from its durable ANCHOR (never the journal fields alone). The journal/anchor are
        #    IMMUTABLE while pending — migration is idempotent (already-removed keys no-op) — and are
        #    cleared only once fully resolved; otherwise the update is DEFERRED.
        migrated = 0
        if completed and completed["pending"]:
            anchor = selfupdate.anchored_record(self._system, completed)
            if anchor is None:                                           # defensive (classifier verified)
                return ActionResult(False, "Self-update blocked: the recorded migration transition is "
                                    "not authorised by a matching durable anchor. No changes were made "
                                    "— recovery required.", data={"recovery_required": True})
            m, remaining = self._run_migration(anchor["pending"], anchor["from_head"])
            migrated += m
            if remaining:
                return ActionResult(True, "Prior config migration is incomplete — deferring the update "
                                    "until it completes; it will be retried on the next self-update.",
                                    data={"migrated": migrated, "pending_migrations": len(remaining),
                                          "deferred_recovery": True})
            if selfupdate.clear_migration_journal(self._paths):         # clear FIRST; drop anchor only if
                selfupdate.delete_anchor(self._system, completed.get("txid"))   # the journal is really gone

        # 4. Attempt a NEW update. Fail-closed PREPARE hook creates the durable git ANCHOR, then the
        #    runtime journal referencing it, BOTH atomically BEFORE the checkout is advanced.
        new_candidates = self._migration_candidates()
        hook = {"written": False, "from": "", "to": "", "branch": "", "intent": [], "txid": ""}

        def _before_mutation(from_head, to_head, branch, _deps):
            intent = self._stamp(new_candidates, from_head)
            hook.update(**{"from": from_head, "to": to_head, "branch": branch, "intent": intent})
            if not intent:
                return
            txid = selfupdate.new_txid()
            payload = {"from_head": from_head, "to_head": to_head, "branch": branch, "pending": intent}
            if not selfupdate.create_anchor(self._system, txid, payload):     # durable provenance FIRST
                raise selfupdate.JournalPersistError()
            rec = {**payload, "txid": txid}
            if not selfupdate.write_migration_journal(self._paths, {"completed": None, "prepared": rec}):
                selfupdate.delete_anchor(self._system, txid)
                raise selfupdate.JournalPersistError()
            hook.update(written=True, txid=txid)

        try:
            res = selfupdate.apply_update(self._system, self._paths, force=force,
                                          before_mutation=_before_mutation)
        except selfupdate.JournalPersistError:
            return ActionResult(False, "Refusing to self-update: could not durably record the config-"
                                "migration intent before changing source. No changes were made.",
                                data={"journal_write_failed": True})

        # 5. On a REAL advance WITH candidates (hook.written -> a valid anchor + journal exist), promote
        #    the prepared transition to `completed` (keeping the anchor), then migrate. Fully resolved ->
        #    clear journal + delete anchor; else keep for retry. A NO-CANDIDATE advance wrote no anchor
        #    and no journal, so there is nothing to promote (never write a record with an empty txid).
        #    Failed/refused leaves config untouched and drops the fresh anchor + journal.
        remaining: list = []
        if res.get("ok") and not res.get("already") and hook["written"]:
            rec = {"from_head": hook["from"], "to_head": hook["to"], "branch": hook["branch"],
                   "pending": hook["intent"], "txid": hook["txid"]}
            self._write_envelope(rec, None)                              # promote prepared -> completed
            m, remaining = self._run_migration(hook["intent"], hook["from"])
            migrated += m
            if not remaining and selfupdate.clear_migration_journal(self._paths):
                selfupdate.delete_anchor(self._system, hook["txid"])
        elif not res.get("ok") and hook["written"]:          # git failed after prepare -> drop anchor+journal
            if selfupdate.clear_migration_journal(self._paths):
                selfupdate.delete_anchor(self._system, hook["txid"])

        instr = selfupdate.restart_instructions(res.get("deps_changed", False),
                                                self._controller_deps_sync_cmd())
        data = {**res, "restart": instr, "migrated": migrated, "pending_migrations": len(remaining)}
        migrated_note = f"{migrated} legacy default(s) migrated to the new defaults." if migrated else ""
        pending_note = (f"{len(remaining)} config default migration(s) could NOT be completed and will "
                        "be retried on the next self-update.") if remaining else ""

        if not res["ok"]:                                    # git failure: dirty refusal / diverged / fetch
            return ActionResult(False, res["message"], data=data)
        if res.get("cleanup_failed"):                        # updated, but untracked cleanup failed -> partial
            details = [res.get("cleanup_error", ""), "Restart the web console after cleaning up:"]
            details += ["  " + c for c in instr["commands"]]
            details += [n for n in (migrated_note, pending_note) if n]
            return ActionResult(False, res["message"], data=data,
                                details=tuple(d for d in details if d))
        if res.get("already"):                               # nothing to update; may have recovered pending
            details = tuple(n for n in (migrated_note, pending_note) if n)
            return ActionResult(True, res["message"], data=data, details=details)
        details = ["Restart the web console to load the new version:"]
        details += ["  " + c for c in instr["commands"]]
        details += [n for n in (migrated_note, pending_note) if n]
        return ActionResult(True, res["message"], data=data, details=tuple(details),
                            next_commands=list(instr["commands"]))

    # ============================================================================================
    # One-click self-update — ESCAPE-PROOF trigger. The running console cannot mutate its own code
    # (it holds the controller-runtime lock SHARED), and it has NO user-systemd bus (its unit
    # InaccessiblePaths=%t/bus %t/systemd/private). So the web writes an in-root request marker
    # under EXCLUSIVE admission; a static lhpc-selfupdate.path unit starts the sandboxed helper;
    # web stop/restart is declarative (Conflicts/After/OnSuccess/OnFailure). NOTHING here calls
    # systemctl except the OPERATOR-shell repair/recover ops. See lhpc/core/updater_units.py.
    # ============================================================================================
    _PIP_SYNC_TIMEOUT_S = 600.0
    _LOCK_WAIT_S = 30.0

    def _user_unit_dir(self):
        from pathlib import Path
        return Path(os.path.expanduser("~")) / ".config" / "systemd" / "user"

    def _marker_present(self, name: str) -> bool:
        from . import runtime_fs
        try:
            return runtime_fs.stat_leaf_nofollow(self._paths, self._paths.under(name)) is not None
        except Exception:
            return False

    def updater_integration(self) -> dict:
        """GET-safe (file reads only, no subprocess/bus): status of the managed web+updater unit
        set for THIS runtime root, plus request-state so the UI can surface 'recovery required'."""
        from . import updater_units
        root = str(self._paths.runtime_root)
        integ = updater_units.integration(self._user_unit_dir(), root)
        req = self.classify_request()
        if req in ("in_flight", "malformed") or self._marker_present(updater_units.UNINSTALL_GUARD):
            integ = dict(integ, status="recovery_required", request=req)
        else:
            integ["request"] = req
        # `fixable`: a non-canonical set the console can auto-migrate in one click — every unit is
        # ok/missing/modified_ours (this deployment's), and no recovery is pending.
        _fixable = (updater_units.OK, updater_units.MISSING, updater_units.MODIFIED_OURS)
        integ["fixable"] = (integ["status"] != "recovery_required"
                            and all(v in _fixable for v in integ["per_unit"].values()))
        # Is THIS console the managed systemd unit? (INVOCATION_ID is set only by systemd.) The unit
        # FILES can verify 'ok' while the console actually runs in a foreground shell — one-click
        # update and boot autostart both need the managed service, so surface the distinction.
        integ["managed"] = bool(os.environ.get("INVOCATION_ID"))
        return integ

    def self_update_local_dirty(self) -> bool:
        """FRESH local dirty check for the one-click confirm step (git status only — local,
        no network). POST-time only; GET rendering stays cached-only."""
        from . import selfupdate
        return bool(selfupdate.local_state(self._system).get("dirty") is True)

    def self_update_ff_blocked(self) -> bool:
        """FRESH check for the one-click confirm step: has the local history DIVERGED so a normal
        fast-forward update would be refused (only a force/reset can update)? Network-free (uses the
        already-fetched remote-tracking ref). POST-time only; GET rendering stays cached-only."""
        from . import selfupdate
        return selfupdate.ff_blocked(self._system)

    # ---- web trigger: write the exclusive request marker (NO systemctl, NO bus) ---------------

    def self_update_trigger(self, *, overwrite: bool = False) -> ActionResult:
        """WEB stage-2: admit exactly one update request by EXCLUSIVELY creating the in-root
        request marker (payload `normal`|`overwrite` — a 1-bit selector the helper re-validates).
        A static .path unit consumes it. Refuses unless this process is the MANAGED web unit
        (INVOCATION_ID) with a byte-exact integration, no active job, an available+safe checkout,
        and no pending/in-flight/uninstall evidence — so a foreground console or a tampered unit
        never writes a request nobody safely consumes."""
        from . import runtime_fs, updater_units
        if not os.environ.get("INVOCATION_ID"):
            return ActionResult(
                False, "One-click update needs the managed web service (systemd). This console is "
                "running in the foreground.",
                details=["  lhpc self-update --repair-integration   "
                         "# installs + enables + starts the service, and enables boot autostart",
                         "  lhpc self-update --apply                # then update"],
                next_commands=["lhpc self-update --repair-integration", "lhpc self-update --apply"],
                data={"not_managed": True})
        integ = self.updater_integration()
        if integ["status"] == "recovery_required":
            return ActionResult(False, "A previous update needs recovery first — run "
                                "`lhpc self-update --recover-request`.", data={"recovery_required": True})
        if integ["status"] != "ok":
            return ActionResult(False, "One-click update is unavailable — the web/updater units are "
                                f"not the canonical managed set ({integ['status']}). Run `lhpc "
                                "self-update --repair-integration`, or `lhpc self-update --apply`.",
                                data={"integration": integ["status"]})
        if self.active_jobs():
            return ActionResult(False, "An lhpc job is still running — finish it before self-updating.",
                                data={"blocked_by_jobs": True})
        st = self.self_update_status()
        if not st.get("available"):
            return ActionResult(False, "Self-update is unavailable — lhpc is not running from a "
                                "git checkout.", data={"unavailable": True})
        idv = st.get("identity")
        if isinstance(idv, dict) and idv.get("status") == "unsafe":
            return ActionResult(False, "Self-update blocked: unsafe controller identity "
                                f"({idv.get('reason', '')}).", data={"identity_unsafe": True})
        mode = "overwrite" if overwrite else "normal"
        try:
            m = runtime_fs.open_marker_excl(self._paths,
                                            self._paths.under(*updater_units.REQUEST_REL),
                                            mode + "\n")
            m.close()
        except FileExistsError:
            return ActionResult(False, "An update request is already pending — the console is about "
                                "to update.", data={"already_pending": True})
        except Exception as exc:                               # containment / fs error
            return ActionResult(False, f"Could not queue the update request: {exc}",
                                data={"trigger_failed": True})
        return ActionResult(True, "Update queued — the console will stop, update itself and come "
                            "back automatically.", data={"triggered": True, "mode": mode})

    # ---- the helper (unit ExecStart): claim -> apply -> sync -> record -> release -------------

    def self_update_run_service(self) -> ActionResult:
        """PLUMBING, run ONLY by lhpc-selfupdate.service. Claims the request (atomic rename to an
        in-flight record carrying the helper's process identity), applies (existing gates: web
        already stopped by Conflicts+After -> EXCLUSIVE lock free; live identity; dirty refusal
        unless overwrite), syncs the venv on a real advance, records the outcome, and releases the
        in-flight record LAST. NO systemctl — the unit's OnSuccess/OnFailure restarts the console.
        the strict record and the final release must BOTH succeed, else the run is INCOMPLETE and
        the in-flight record is retained (one-click stays blocked until --recover-request)."""
        import sys
        import time as _time
        from . import runtime_fs, selfupdate, updater_units
        req_path = self._paths.under(*updater_units.REQUEST_REL)
        inflight = self._paths.under(*updater_units.INFLIGHT_REL)
        # CLAIM: atomic NO-OVERWRITE rename request -> inflight. Absent request = stray start ->
        # clean no-op. A pre-existing in-flight record (prior interrupted run) means the claim
        # fails closed (FileExistsError) and BOTH markers are preserved for recovery — the helper
        # is the security boundary and never clobbers in-flight evidence.
        try:
            runtime_fs.rename_leaf(self._paths, req_path, inflight, replace=False)
        except FileNotFoundError:
            return ActionResult(True, "No update request to service.", data={"noop": True})
        except FileExistsError:
            return ActionResult(False, "A previous update is already in flight — recovery required "
                                "(`lhpc self-update --recover-request`).",
                                data={"recovery_required": True})
        except Exception as exc:
            return ActionResult(False, f"Could not claim the update request: {exc}",
                                data={"claim_failed": True})
        # Read mode, then overwrite the in-flight record with a process-identity claim.
        try:
            mode = runtime_fs.read_text_regular(self._paths, inflight, max_bytes=4096).strip()
        except Exception:
            mode = ""
        if mode not in ("normal", "overwrite"):
            runtime_fs.write_marker(self._paths, inflight,
                                    json.dumps({"mode": None, "error": "malformed-request"}))
            selfupdate.record_last_apply_strict(self._paths, ok=False,
                                                summary="Update request was malformed — recovery required.")
            return ActionResult(False, "Malformed update request — recovery required.",
                                data={"malformed": True})
        force = (mode == "overwrite")
        runtime_fs.write_marker(self._paths, inflight, json.dumps(self._helper_identity(mode)))

        res = ActionResult(False, "Self-update service did not run.", data={})
        try:
            # Web is stopped (Conflicts+After) so its SHARED lock is released; take EXCLUSIVE with
            # a short bounded retry to cover the stop-completion window, then apply.
            deadline = _time.monotonic() + self._LOCK_WAIT_S
            while True:
                try:
                    with selfupdate.controller_runtime_lock(self._paths, exclusive=True):
                        break
                except selfupdate.ControllerRuntimeBusy:
                    if _time.monotonic() >= deadline:
                        res = ActionResult(False, "The console did not release the controller-runtime "
                                           "lock — no changes made.", data={"web_running": True})
                        raise _StopRun()
                    _time.sleep(0.5)
                except selfupdate.ControllerRuntimeLockError:
                    res = ActionResult(False, "Could not acquire the controller-runtime lock "
                                       "(unsafe runtime state) — no changes made.",
                                       data={"lock_error": True})
                    raise _StopRun()
            res = self.self_update_apply(force=force)
            if res.ok and not res.data.get("already"):
                root = selfupdate.repo_root()
                if root is not None:
                    pip = self._system.runner.run(
                        [sys.executable, "-m", "pip", "install", "-e", str(root)],
                        timeout=self._PIP_SYNC_TIMEOUT_S)
                    if pip.returncode != 0:                     # P2: a failed sync FAILS the update
                        # First line of pip's diagnostics, stripped of box-drawing/ANSI so the
                        # persisted summary reads cleanly in the GUI flash (never a mid-box tail).
                        detail = selfupdate._summarize_output(pip.stderr or pip.stdout)
                        res = ActionResult(False, "Update applied, but the venv sync FAILED — run "
                                           f"{sys.executable} -m pip install -e {root} manually, then "
                                           f"restart the console." + (f" ({detail})" if detail else ""),
                                           data={**dict(res.data), "venv_sync_failed": True})
        except _StopRun:
            pass
        # Record the outcome DURABLY, then release the in-flight record. If the STRICT record does
        # not persist, retain in-flight and report incomplete (one-click blocked until recovery) —
        # never delete the evidence on an unrecorded outcome.
        if not selfupdate.record_last_apply_strict(self._paths, ok=bool(res.ok), summary=res.summary):
            return ActionResult(False, "Update outcome could not be recorded durably — recovery "
                                "required (`lhpc self-update --recover-request`).",
                                data={**dict(res.data), "record_failed": True})
        try:
            runtime_fs.unlink(self._paths, inflight)
        except Exception as exc:
            return ActionResult(False, res.summary + f" (in-flight marker cleanup FAILED: {exc} — "
                                "recovery required)", data={**dict(res.data), "cleanup_failed": True})
        return res

    def _helper_identity(self, mode: str) -> dict:
        """Bounded process-identity record stored in the in-flight marker so recovery can prove
        the original helper has ceased before clearing it (never age-based)."""
        import hashlib
        import sys
        import time as _time
        pid = os.getpid()
        return {"mode": mode, "pid": pid, "start_time": _proc_start_time(pid),
                "exe": (os.readlink(f"/proc/{pid}/exe") if os.path.exists(f"/proc/{pid}/exe") else ""),
                "argv_hash": hashlib.sha256(("\0".join(sys.argv)).encode()).hexdigest()[:16],
                "claimed_at": int(_time.time())}

    # ---- request-state recovery (operator shell) ---------------------------------------------

    def classify_request(self) -> str:
        """`absent | pending | in_flight | malformed` — file reads only (GET-safe)."""
        from . import runtime_fs, updater_units
        inflight = self._paths.under(*updater_units.INFLIGHT_REL)
        req = self._paths.under(*updater_units.REQUEST_REL)
        try:
            if runtime_fs.stat_leaf_nofollow(self._paths, inflight) is not None:
                try:
                    rec = json.loads(runtime_fs.read_text_regular(self._paths, inflight, max_bytes=4096))
                    if isinstance(rec, dict) and isinstance(rec.get("pid"), int) and rec.get("mode"):
                        return "in_flight"
                except Exception:
                    pass
                return "malformed"
            if runtime_fs.stat_leaf_nofollow(self._paths, req) is not None:
                return "pending"
        except Exception:
            return "malformed"
        return "absent"

    def self_update_recover_request(self) -> ActionResult:
        """OPERATOR: clear a stuck request/in-flight record SAFELY. A pending (unclaimed) request
        is cleared. An in-flight record is cleared ONLY when its recorded helper identity is
        proven ceased (never age-based); a missing/malformed identity stays recovery-required."""
        from . import runtime_fs, updater_units
        state = self.classify_request()
        if state == "absent":
            return ActionResult(True, "No stuck update request.", data={"state": "absent"})
        inflight = self._paths.under(*updater_units.INFLIGHT_REL)
        req = self._paths.under(*updater_units.REQUEST_REL)
        if state == "pending":
            runtime_fs.unlink(self._paths, req)
            return ActionResult(True, "Cleared a pending update request (it was never claimed).",
                                data={"cleared": "pending"})
        if state == "malformed":
            return ActionResult(False, "The in-flight update record is unreadable/malformed — its "
                                "helper cannot be proven stopped. Ensure lhpc-selfupdate.service is "
                                "not active, then remove state/selfupdate.inflight by hand.",
                                data={"state": "malformed"})
        # in_flight: verify the recorded process is gone.
        rec = json.loads(runtime_fs.read_text_regular(self._paths, inflight, max_bytes=4096))
        if not _proc_ceased(rec.get("pid"), rec.get("start_time")):
            return ActionResult(False, "An update is still running (helper process alive) — wait "
                                "for it to finish before recovering.", data={"state": "running"})
        # Record the interrupted outcome DURABLY *before* removing the evidence — if the strict
        # write fails, keep the in-flight marker so recovery can be retried (never silently clear).
        from . import selfupdate as _su
        if not _su.record_last_apply_strict(self._paths, ok=False,
                summary="A previous update was interrupted and did not complete."):
            return ActionResult(False, "Could not record the interrupted outcome durably — the "
                                "in-flight record is kept; try recovery again.",
                                data={"record_failed": True})
        runtime_fs.unlink(self._paths, inflight)
        return ActionResult(True, "Cleared an interrupted update (helper had stopped); recorded it "
                            "as incomplete.", data={"cleared": "in_flight"})

    # ---- integration repair (operator shell, HAS bus) ----------------------------------------

    def self_update_repair_integration(self, *, restart: bool = True) -> ActionResult:
        """OPERATOR / migration: install/restore the COMPLETE canonical unit set (web + helper +
        path) for this runtime root, then daemon-reload, verify the active fragments, enable the
        watcher (`--now`) + web. With `restart=True` (CLI default) also restart the console; with
        `restart=False` (the web self-repair bridge) leave the running console alone so the update
        itself bounces it. Refuses while an uninstall guard or request/in-flight evidence exists,
        or when an existing unit is not provably this deployment's."""
        from . import runtime_fs, updater_units
        root = str(self._paths.runtime_root)
        _, checkout, venv = updater_units.deployment_paths(root)
        import os.path as _op
        if not _op.isdir(_op.join(checkout, ".git")):
            return ActionResult(False, "Not a self-hosted deployment (no checkout at "
                                f"{checkout}) — cannot manage web/updater units.",
                                data={"not_self_hosted": True})
        if self._marker_present(updater_units.UNINSTALL_GUARD):
            return ActionResult(False, "An uninstall is in progress (.lhpc-uninstalling present) — "
                                "recover it first (`lhpc self-update --recover-request`).",
                                data={"uninstalling": True})
        if self.classify_request() != "absent":
            return ActionResult(False, "An update request is pending/in-flight — run "
                                "`lhpc self-update --recover-request` first.",
                                data={"request_present": True})
        ud = self._user_unit_dir()
        # The units log with StandardOutput=append:{root}/logs/... — systemd creates the FILE but not
        # the directory, and a repaired root may predate/have lost it (bootstrap normally makes it).
        # Without this the web unit fails to start on `append:` open.
        try:
            runtime_fs.mkdir(self._paths, "logs")
        except Exception:                                    # noqa: BLE001 — best effort, never fatal
            pass
        try:
            actions = updater_units.write_set(ud, root)
        except ValueError as exc:
            return ActionResult(False, str(exc), data={"write_refused": True})
        S = 20.0
        self._system.runner.run(["systemctl", "--user", "daemon-reload"], timeout=S)
        # Authoritative loader check (operator shell HAS the bus): the ACTIVE fragment must be our
        # file AND carry NO drop-ins — a drop-in can override the sandbox / ExecStart /
        # InaccessiblePaths of the vetted unit, so either condition FAILS the repair before we
        # enable/restart/write the marker.
        for kind in updater_units.ALL_UNITS:
            show = self._system.runner.run(
                ["systemctl", "--user", "show", "-p", "FragmentPath", "-p", "DropInPaths", kind],
                timeout=S)
            out = (show.stdout or "")
            props = dict(ln.split("=", 1) for ln in out.splitlines() if "=" in ln)
            want = str(ud / kind)
            if props.get("FragmentPath") != want:
                return ActionResult(False, f"After writing units, {kind} still loads a different "
                                    f"fragment ({out.strip()[:120]}). A higher-priority unit or "
                                    "mask shadows it — resolve manually.", data={"shadowed": kind})
            if props.get("DropInPaths", "").strip():
                return ActionResult(False, f"{kind} has an active drop-in override "
                                    f"({props['DropInPaths'].strip()[:120]}) — it can override the "
                                    "sandbox; remove it, then repair.", data={"dropin": kind})
        # Enable both, and START the watcher now so a request marker is caught even before the web
        # is (re)started under the new unit.
        en = self._system.runner.run(["systemctl", "--user", "enable", "--now",
                                      updater_units.PATH_UNIT], timeout=S)
        self._system.runner.run(["systemctl", "--user", "enable", updater_units.WEB_UNIT], timeout=S)
        # Migration mode (restart=False): the still-running OLD web does NOT pull the watcher up
        # via Wants=, so it MUST be active now — otherwise a queued request would never be consumed.
        # Fail BEFORE writing the root marker (and thus before the caller triggers).
        if not restart:
            act = self._system.runner.run(["systemctl", "--user", "is-active", "--quiet",
                                           updater_units.PATH_UNIT], timeout=S)
            if en.returncode != 0 or act.returncode != 0:
                return ActionResult(False, "Installed the units but could not start the request "
                                    "watcher (lhpc-selfupdate.path) — not proceeding. Check "
                                    "`systemctl --user status lhpc-selfupdate.path`.",
                                    data={"path_watcher_failed": True})
        ov_note = self._remove_stale_overwrite_unit(ud)
        self._write_root_marker()
        note = ""
        if restart:
            rst = self._system.runner.run(["systemctl", "--user", "restart", updater_units.WEB_UNIT],
                                          timeout=S)
            note = "" if rst.returncode == 0 else " (web restart returned nonzero — check journalctl)"
        details = [f"  {k}: {a}" for k, a in actions]
        if ov_note:
            details.append(f"  {ov_note}")
        details.append(self._enable_linger(S))
        return ActionResult(True, "Web + one-click updater integration installed/repaired." + note,
                            details=tuple(details), data={"actions": dict(actions)})

    def _enable_linger(self, timeout: float) -> str:
        """Boot autostart: a `systemctl --user` unit only starts at LOGIN unless the user lingers.
        Installed roots get this from install.sh; a repaired one did not — so repair enables it too.

        ALWAYS attempted (never gated on INVOCATION_ID): the web self-repair bridge runs from a
        managed LEGACY web unit that still has the user bus, and gating would silently deny it boot
        autostart. FAIL-SOFT by contract — a linger failure NEVER fails the repair/update; where the
        bus is unavailable (the canonical web unit blocks %t/bus) we return the shell command."""
        import getpass
        try:
            user = getpass.getuser()
        except Exception:                                    # noqa: BLE001 — no pwent / no env
            return "  linger: could not resolve the user — run: loginctl enable-linger $USER"
        r = self._system.runner.run(["loginctl", "enable-linger", user], timeout=timeout)
        if getattr(r, "not_found", False) or r.returncode != 0:
            return (f"  linger: NOT enabled (no user bus here) — for autostart at boot run: "
                    f"loginctl enable-linger {user}")
        return f"  linger: enabled for {user} — the console now autostarts at boot"

    def _remove_stale_overwrite_unit(self, ud) -> str | None:
        """Remove the obsolete `lhpc-selfupdate-overwrite.service` ONLY when it is PROVABLY this
        deployment's old overwrite helper variant — BOTH a same-root `LHPC_RUNTIME_ROOT` (literal
        or `%h`) AND the old variant's exact `ExecStart` shape
        (`<root>/venv/lhpc/bin/lhpc self-update --run-service --overwrite`). Anything else (edited /
        foreign / unreadable / symlinked) is LEFT untouched. Returns a manual-cleanup note when a
        same-named unit is present but not proven ours, else None."""
        from . import updater_units
        name = "lhpc-selfupdate-overwrite.service"
        p = ud / name
        try:
            text = updater_units._read_unit(p)               # no-follow, bounded; None if absent
        except Exception:
            return f"{name} is present but unreadable/symlinked — remove it by hand if unused."
        if text is None:
            return None
        home = os.path.expanduser("~")
        root = str(self._paths.runtime_root)
        lines = text.splitlines()
        envs = [ln[len("Environment=LHPC_RUNTIME_ROOT="):] for ln in lines
                if ln.startswith("Environment=LHPC_RUNTIME_ROOT=")]
        execs = [ln[len("ExecStart="):] for ln in lines if ln.startswith("ExecStart=")]
        want_exec = f"{root}/venv/lhpc/bin/lhpc self-update --run-service --overwrite"
        root_ours = any(updater_units._expand_h(v, home) == root for v in envs)
        exec_ours = any(updater_units._expand_h(v, home) == want_exec for v in execs)
        if not (root_ours and exec_ours):
            return (f"{name} is present but not the recognised old overwrite helper — left in "
                    "place; remove it by hand if unused.")
        self._system.runner.run(["systemctl", "--user", "disable", "--now", name], timeout=20.0)
        try:
            os.remove(str(p))                                # regular file proven by _read_unit
        except OSError:
            pass
        return None

    def self_update_repair_and_trigger(self, *, overwrite: bool = False) -> ActionResult:
        """WEB one-click that also MIGRATES a legacy same-root deployment (old/`%h` units, no
        `.path`) to the canonical set, then updates — in one click. Compatibility bridge ONLY: it
        needs the user bus, which succeeds only while the console runs the not-yet-hardened unit;
        once the canonical bus-blocked web unit is active the bus preflight fails and this returns
        shell guidance WITHOUT writing anything. Auto-repair is allowed ONLY for
        `missing`/`modified_ours` units — never `ambiguous`/`foreign`/`overridden`/`unsafe`/
        `unreadable`/recovery states."""
        from . import updater_units
        # Managed-service gate FIRST — the web->systemctl bridge must run only for the legacy
        # managed unit, never a foreground `lhpc web`, so no units/marker are written by one.
        if not os.environ.get("INVOCATION_ID"):
            return ActionResult(
                False, "One-click update needs the managed web service (systemd). This console is "
                "running in the foreground.",
                details=["  lhpc self-update --repair-integration   "
                         "# installs + enables + starts the service, and enables boot autostart",
                         "  lhpc self-update --apply                # then update"],
                next_commands=["lhpc self-update --repair-integration", "lhpc self-update --apply"],
                data={"not_managed": True})
        integ = self.updater_integration()
        status = integ["status"]
        if status == "ok":
            return self.self_update_trigger(overwrite=overwrite)         # nothing to migrate
        # Refuse recovery / pending-request / uninstall BEFORE any preflight or write.
        if status == "recovery_required":
            return ActionResult(False, "A previous update needs recovery first — run "
                                "`lhpc self-update --recover-request`.", data={"recovery_required": True})
        if self._marker_present(updater_units.UNINSTALL_GUARD):
            return ActionResult(False, "An uninstall is in progress — recover it first.",
                                data={"uninstalling": True})
        if self.classify_request() != "absent":
            return ActionResult(False, "An update request is already pending — the console is about "
                                "to update.", data={"request_present": True})
        # Fixable ONLY when every non-OK unit is missing/modified_ours (an ambiguous/foreign/
        # overridden/unsafe/unreadable unit is NOT auto-repairable).
        fixable_set = (updater_units.OK, updater_units.MISSING, updater_units.MODIFIED_OURS)
        per = integ.get("per_unit", {})
        bad = {k: v for k, v in per.items() if v not in fixable_set}
        if bad:
            detail = ", ".join(f"{k}: {v}" for k, v in bad.items())
            return ActionResult(False, "The web/updater units are not safely this deployment's "
                                f"({detail}) — resolve them manually, then update.",
                                data={"integration": status, "unfixable": bad})
        # Bus preflight — cheap, read-only. A hardened (bus-blocked) console fails here BEFORE any
        # write and gets shell guidance.
        probe = self._system.runner.run(["systemctl", "--user", "show", "-p", "Version"], timeout=20.0)
        if probe.returncode != 0:
            return ActionResult(False, "This console can't install systemd units itself (the user "
                                "bus is unavailable). From a shell on this machine run "
                                "`lhpc self-update --repair-integration`, then click Update.",
                                data={"bus_unavailable": True})
        rep = self.self_update_repair_integration(restart=False)
        if not rep.ok:
            return rep
        if self.updater_integration()["status"] != "ok":                # repair must have converged
            return ActionResult(False, "Unit repair did not fully converge — run "
                                "`lhpc self-update --repair-integration` from a shell.",
                                data={"repair_incomplete": True})
        return self.self_update_trigger(overwrite=overwrite)

    def _write_root_marker(self) -> None:
        from . import runtime_fs, updater_units
        import time as _time
        payload = json.dumps({"schema_version": 1, "root": str(self._paths.runtime_root),
                              "created": int(_time.time())})
        runtime_fs.write_marker(self._paths, self._paths.under(updater_units.ROOT_MARKER), payload)

    def _write_envelope(self, completed, prepared) -> None:
        """Persist the two-slot envelope (or clear it when both slots are empty). Best-effort at
        finalization: a failed write is self-healed on the next invocation's prepared-reconciliation,
        so a still-pending `completed` is never silently lost."""
        from . import selfupdate
        if not completed and not prepared:
            selfupdate.clear_migration_journal(self._paths)
        else:
            selfupdate.write_migration_journal(self._paths, {"completed": completed,
                                                             "prepared": prepared})

    @staticmethod
    def _promote(completed, prepared) -> dict:
        """Promote a prepared transition whose git DID complete (head reached its to_head) into the
        completed slot, carrying its durable-anchor `txid` and its (immutable, anchor-matched) pending
        payload. The classifier's invariant guarantees no prior completed pending coexists."""
        return {"from_head": prepared["from_head"], "to_head": prepared["to_head"],
                "branch": prepared["branch"], "pending": prepared["pending"],
                "txid": prepared.get("txid")}

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

    # Feed scan window. The RX/TX lines are a SMALL fraction of the daemon's stdout, so we must
    # scan far more than we display and filter FIRST — tailing 400 lines and filtering afterwards
    # made "recent" mean "within the last 400 log lines", not "recent in time", and a chatty igate
    # (beacons + digipeat + RX) evicted a seconds-old TX while a quiet chat kept it for minutes.
    # Bounded + no-follow; ~200 KB typical per 3 s poll, which stays cheap on a Pi.
    _FEED_SCAN_LINES = 2000
    _FEED_SCAN_BYTES = 512 * 1024

    def daemon_feed(self, band: str, lines: int = 40) -> list[str]:
        """Bounded tail of the daemon's activity, filtered to RX/TX lines. The v111a
        multi-instance daemon logs to stdout, which LHPC captures into the per-band process
        log `logs/start-<daemon>-<band>.log` (in-root; LHPC reads no external log files).

        EXACTLY ONE source file, never a concatenation: the per-band log when it exists, else
        the legacy band-less `start-<daemon>.log` — written by a pre-upgrade daemon that has not
        been restarted since the rename. Reading both would double-count every match, because the
        band-agnostic tokens ([TX], [RX], TXOK, ...) match either band's lines.
        """
        raws: list[str] = []
        for name in (f"start-{self.DAEMON_ID}-{band}.log",
                     f"start-{self.DAEMON_ID}.log"):
            try:
                p = self._paths.under("logs", name)
            except PathContainmentError:
                continue
            if not p.is_file():
                continue
            try:
                raws = runtime_fs.tail(self._paths, p, self._FEED_SCAN_LINES,
                                       self._FEED_SCAN_BYTES)
            except (OSError, PathContainmentError, ValueError):
                raws = []                        # symlinked/escaping -> no lines
            break                                # first existing candidate wins
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

    _VERSION_TAG_RE = None

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

        Returns (groups, error): groups = ordered [(path, comp, (expected, label))]."""
        from . import known_working, source_registry
        compositions: dict = {}
        if source == "pinned":
            for st in {s.id: s for s, _ in items}.values():
                compositions[st.id] = known_working.compatible_composition(
                    self._paths, st, lambda c: self._effective_remote(c))
        by_path: dict = {}
        for st, comp in items:
            spec = comp.source
            if source != "pinned" or spec.artifact:
                resolved = ("", "")
            else:
                entries = compositions.get(st.id)
                if entries and comp.id in entries:
                    resolved = (entries[comp.id]["commit"],
                                "known working (operator-confirmed composition)")
                else:
                    resolved = ("", "fallback: manifest pin — no known-working record")
            ident = (spec.strategy or "", bool(spec.artifact),
                     source_registry.norm_remote(self._effective_remote(comp)), resolved)
            by_path.setdefault(spec.path, []).append((st, comp, ident, resolved))
        groups, conflicts = [], []
        frozen_cache: dict = {}
        for path, members in by_path.items():
            idents = {m[2] for m in members}
            if len(idents) > 1:
                who = ", ".join(f"{st.id}/{c.id}" for st, c, _, _ in members)
                conflicts.append(f"shared source {path!r}: targeted consumers ({who}) "
                                 "resolve to incompatible source identities (strategy/"
                                 "remote/known-working) — resolve or re-confirm before "
                                 "installing/updating")
                continue
            st, comp, _, resolved = members[0]
            if freeze and not resolved[0] and (source != "pinned"
                                               or comp.source.artifact):
                # FROZEN selector resolution (bulk plan): one exact immutable commit per
                # group, resolved HERE — adoption never performs a second lookup.
                # ARTIFACT sources freeze their declared default-branch HEAD for EVERY
                # selector (incl. 'pinned' — they never use known-working entries): the
                # plan-time commit IS this run's immutable artifact identity.
                if path not in frozen_cache:
                    frozen_cache[path] = self._frozen_ref(comp, source)
                (fz, why) = frozen_cache[path]
                if fz[0] is None:
                    # DEFAULT POLICY: latest dev with a DISCLOSED fallback to the
                    # known-working composition (then the manifest pin) when dev is
                    # unreachable — never a silent substitute, never pinned-by-default.
                    fb = self._kw_fallback_expected(st, comp)
                    if source == "dev" and fb[0]:
                        resolved = fb
                    else:
                        conflicts.append(f"source {path!r}: exact {source} resolution "
                                         f"failed — {why}")
                        continue
                else:
                    resolved = fz
            groups.append((path, comp, resolved))
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

    def _running_source_consumers(self, paths: set) -> list:
        """Component ids that are RUNNING/DEGRADED and consume any of the given source paths —
        a source swap under a running process breaks it (deleted inodes / half-read files), so
        mutation of these paths is refused until the operator stops them."""
        consumers = self._source_consumers()
        affected = set()
        for p in paths:
            affected |= consumers.get(p, set())
        snap = self.build_snapshot()
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

    def known_working_offer(self, stack_id: str, snapshot=None) -> dict | None:
        """The 'Confirm this stack as working' offer for the stack page (FILE READS ONLY —
        no git, GET-safe). Present only when ALL hold: a last-start candidate exists; the
        stack is currently RUNNING (per the supplied/probed snapshot); every candidate entry
        still equals the CURRENT ownership-registry commit (the sources were not swapped since
        that start); and the composition is not already recorded."""
        from . import known_working, source_registry
        cand = known_working.read_candidate(self._paths, stack_id)
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
        (dedupe, keep the newest three). Re-validates everything the offer validated —
        AND, under the stack's SOURCE LOCKS, re-proves every component's CURRENT ownership +
        identity (leaf kind, HEAD, origin) against its registry record: a manually changed
        tree is a typed refusal, never a fabricated record."""
        from . import known_working, reslock, source_registry
        stack = self.stack(stack_id)
        if stack is None:
            return self._unknown_stack(stack_id)
        cand = known_working.read_candidate(self._paths, stack_id)
        if cand is None:
            return ActionResult(False, f"No healthy start is recorded for '{stack_id}' — "
                                "start the stack first.")
        offer = self.known_working_offer(stack_id)
        if offer is None:
            if cand["hash"] in known_working.hashes(self._paths, stack_id):
                return ActionResult(True, f"'{stack_id}' is already recorded as known working.")
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
                             "evidence": "healthy verified stack start + operator confirmation"}
                ok, msg = known_working.record(self._paths, stack_id, cand["entries"],
                                               validated)
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

    def update(self, target: str = "", apply: bool = False,
               source: str = "pinned", bulk_ctx=None) -> ActionResult:
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
        ctx_err = self._bulk_ctx_error(bulk_ctx, {c.source.path for _, c in items})
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
            with self._config_stable():
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
                    for path, c, resolved in groups:
                        r = self._adopt_dev_fallback(
                            inst, stacks_by_comp.get(c.id), c, source, resolved,
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

        from . import reslock, source_fs, source_registry
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
            with self._config_stable():
              with self._source_operation_guard(all_paths, op="uninstall"):
                self._op_seam("uninstall-locked")
                # AUTHORITATIVE running recheck AFTER all locks are held: a Start that
                # slipped in after the preflight refuses with ZERO mutation (no source,
                # config, log, marker, known-working, or registry change).
                snap = self.build_snapshot()
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
            with self._config_stable():
                with self._source_operation_guard(all_paths, op="clean"):
                    self._op_seam("clean-locked")
                    # AUTHORITATIVE running recheck AFTER all locks are held: a Start that
                    # slipped in after the preflight refuses with ZERO mutation — no
                    # source, config, log, marker, known-working, or registry cleanup.
                    snap = self.build_snapshot()
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

    # ---- helpers ---------------------------------------------------------

    def _unknown_stack(self, stack_id: str) -> ActionResult:
        known = ", ".join(s.id for s in self.stacks())
        return ActionResult(
            ok=False,
            summary=f"Unknown stack '{stack_id}'.",
            details=[f"Known stacks: {known}"],
            next_commands=["lhpc list"],
        )


def _render_component(comp, status) -> list[str]:
    band = f"band {comp.band}" if comp.band else "band -"
    line = (
        f"  {comp.id:24s} {status.run_state.value:14s} "
        f"[{comp.kind.value}] {band}  tx {status.tx_state.value}  "
        f"src {status.source_state.value}"
    )
    out = [line]
    for dep in status.dependencies:
        band_txt = f" on {dep.band} MHz" if dep.band else ""
        out.append(f"        depends on {dep.component_id}: {dep.run_state.value}{band_txt}")
    for obs in status.endpoints:
        out.append(f"        endpoint {obs.spec.address} {obs.spec.kind}: {obs.detail}")
    return out
