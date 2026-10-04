"""Controller self-update orchestration + updater integration (helper units, markers, promote).

Mixin of ControllerService (state/constants on the facade). Adapters import lhpc.core.services only."""
from __future__ import annotations

import json
import os
from os.path import exists as _op_exists
from os.path import join as _op_join
from typing import ClassVar

from .paths import PathContainmentError
from .service_base import ActionResult, _proc_ceased, _proc_start_time, _StopRun
from .snapshot_memo import invalidates_snapshot

_RETRY = "lhpc self-update --apply"
_WRITABLE = " is group/other-writable"


def _identity_remedy(reason: str, root, spec) -> tuple[list[str], list[str]]:
    """(next_commands, details) for an unsafe controller identity, chosen by the cause
    `controller_identity_live` names in `reason`. Text only: the refusal does not change."""
    checkout = f"{root}/src/loraham-pi-control"
    branch, remote = getattr(spec, "branch", "main"), getattr(spec, "remote", "")
    if reason == "checkout is in detached HEAD" or reason.startswith("checkout branch "):
        return ([f"git -C {checkout} switch {branch}", _RETRY],
                [f"  the checkout is off its branch {branch}; git refuses the switch while it "
                 "would overwrite a change in the checkout — the box's operator commits or "
                 "removes that change first"])
    if reason == "checkout has no origin remote":
        return ([f"git -C {checkout} remote add origin {remote}", _RETRY],
                [f"  the checkout has no origin; lhpc updates from {remote}"])
    if reason == "origin is not the approved canonical remote":
        return ([f"git -C {checkout} remote set-url origin {remote}", _RETRY],
                [f"  the checkout's origin is not {remote}"])
    if reason.endswith(_WRITABLE):
        label = reason[:-len(_WRITABLE)]
        path = {"runtime root": f"{root}", "src": f"{root}/src"}.get(label, checkout)
        return [f"chmod go-w {path}", _RETRY], []
    return [], [f"  nothing to run here — {reason}: not the layout install.sh makes; the box's "
                f"operator restores that layout (install.sh again), then runs `{_RETRY}`"]


class SelfUpdateOpsMixin:

    def controller_log_tail(self, source: str = "web", lines: int = 300):
        """Raw (path, lines) for the controller's OWN process logs — the lhpc-web / lhpc-selfupdate
        units log to on-disk FILES under logs/ (StandardOutput=append:), so the GUI reads them
        the same way as the nginx logs (the box's user journal is not reliably populated). `source`
        selects the file: 'web', 'selfupdate' or 'boot-restore'; any other source yields ("", []). Same
        containment-safe, O_NOFOLLOW, bounded read as `webserver_log_tail`; never raises into GET."""
        from . import runtime_fs, updater_units
        # EXPLICIT immutable source map — an unknown source is rejected (empty result), never
        # silently aliased to the web log (P1-6).
        const = {"web": updater_units.WEB_LOG_REL,
                 "selfupdate": updater_units.HELPER_LOG_REL,
                 "boot-restore": updater_units.BOOT_RESTORE_LOG_REL}.get(source)
        if const is None:
            return "", []
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

    def controller_system_deps(self) -> list[dict]:
        """LHPC's OWN system/runtime dependencies (git, nginx, systemd, install-time tools, venv deps),
        grouped, each with presence + install command. The SINGLE source of truth for both the
        controller System-dependencies panel (/stacks) and `lhpc doctor`, so the two never drift.
        GET-SAFE: presence probes only — `shutil.which` / `System.fs.exists` / `importlib.util.find_spec`
        — never a subprocess (git/nginx/systemctl are NOT executed). ONE documented exception:
        the Power-controls entry consults the CACHED logind verdict (bounded busctl, at most
        one probe per minute — see its comment; a file probe cannot work there)."""
        import getpass as _getpass
        import importlib.util
        import shutil
        import sys

        from . import deps as _deps_mod
        from . import webserver as _ws
        # Python venv deps must be (re)installed into the SAME interpreter that runs LHPC — never a bare
        # `pip install` (wrong env / PEP-668). Version floors mirror pyproject.toml.
        _pipi = f"{sys.executable} -m pip install"
        fs = self._system.fs

        def have_cmd(cmd: str, *fallbacks: str) -> bool:
            # PATH first (a managed unit's PATH can be narrower than a shell), then safe absolute-path
            # fallbacks via the injectable fs.exists — NEVER executes the binary. No subprocess.
            return shutil.which(cmd) is not None or any(fs.exists(p) for p in fallbacks)

        def have_mod(mod: str) -> bool:
            try:
                return importlib.util.find_spec(mod) is not None
            except (ImportError, ValueError):
                return False

        return [
            {"title": "System packages (apt)", "deps": [
                {"what": "git", "required": True,
                 "satisfied": have_cmd("git", "/usr/bin/git", "/usr/local/bin/git"),
                 "install": "sudo apt install -y git",
                 "purpose": "self-update fast-forward, initial clone, source adoption"},
                {"what": "nginx", "required": False,
                 "satisfied": have_cmd("nginx", "/usr/sbin/nginx", "/usr/bin/nginx"),
                 "install": _ws.NGINX_INSTALL_CMD,
                 "purpose": "HTTPS + mTLS front-end — the console runs over loopback without it; "
                            "exposed/HTTPS access needs it"},
                {"what": "zstd", "required": False,
                 "satisfied": have_cmd("zstd", "/usr/bin/zstd", "/bin/zstd"),
                 "install": "sudo apt install -y zstd",
                 # OPTIONAL controller capability: without it the binary CHANNEL cannot unpack a
                 # published artifact (the source channels are unaffected), so its absence
                 # degrades one install path rather than blocking the controller.
                 "purpose": "unpack prebuilt binary artifacts (binary install channel)"},
                {"what": "nftables", "required": False,
                 "satisfied": have_cmd("nft", "/usr/sbin/nft", "/sbin/nft"),
                 "install": "sudo apt install -y nftables",
                 # OPTIONAL controller capability: absence degrades ONLY the managed firewall
                 # feature (its dashboard row reads "nftables not present"); no unrelated lhpc
                 # operation depends on it. Preinstalled on Raspberry Pi OS trixie.
                 "purpose": "managed firewall — closes stack ports lhpc cannot gate "
                            "(meshtasticd 4403/9443); the feature is unavailable without it"},
                {"what": "iw", "required": False,
                 "satisfied": have_cmd("iw", "/usr/sbin/iw", "/sbin/iw"),
                 "install": "sudo apt install -y iw",
                 # OPTIONAL: the AP watchdog retries the preferred Wi-Fi only when the AP
                 # provably has no associated station (iw station dump). Without it the
                 # automatic retry stays deferred; the console's Retry still works.
                 "purpose": "AP client detection — the automatic preferred-Wi-Fi retry runs "
                            "only while no client is connected to the box's AP"},
                {"what": "systemd (systemctl, loginctl)", "required": False,
                 "satisfied": (have_cmd("systemctl", "/usr/bin/systemctl", "/bin/systemctl")
                               and have_cmd("loginctl", "/usr/bin/loginctl", "/bin/loginctl")),
                 # NOT installable by command: if systemctl/loginctl are absent this host is not systemd,
                 # and `apt install systemd` is not the fix. Explain instead of offering nonsense advice.
                 "install": "",
                 "note": "managed-service mode is unavailable here — provide systemd (boot the OS with "
                         "systemd / enable the `systemctl --user` session), or run without it: `lhpc web` "
                         "in the foreground now, then `lhpc self-update --repair-integration` once systemd "
                         "is available. No package can add systemd (`apt install systemd` is not the fix).",
                 "purpose": "the managed --user service + boot linger (only for managed-service mode)"},
            ]},
            {"title": "Install-time", "deps": [
                {"what": "python3 (>= 3.11)", "required": True,
                 "satisfied": have_cmd("python3", "/usr/bin/python3", "/usr/local/bin/python3"),
                 "install": "sudo apt install -y python3", "purpose": "the controller runtime"},
                {"what": "python3-venv", "required": True,
                 "satisfied": have_mod("venv") and have_mod("ensurepip"),
                 "install": "sudo apt install -y python3-venv",
                 "purpose": "builds the LHPC virtualenv (venv + ensurepip)"},
                {"what": "pip", "required": True, "satisfied": have_mod("pip"),
                 "install": "sudo apt install -y python3-pip",
                 "purpose": "editable install + venv sync on self-update"},
            ]},
            {"title": "Python venv dependencies (pip, in venv/lhpc)", "deps": [
                {"what": "flask", "required": True, "satisfied": have_mod("flask"),
                 "install": f"{_pipi} 'flask>=3,<4'", "purpose": "web console"},
                {"what": "waitress", "required": True, "satisfied": have_mod("waitress"),
                 "install": f"{_pipi} 'waitress>=3,<4'",
                 "purpose": "production WSGI server (no dev-server fallback)"},
                {"what": "cryptography", "required": True, "satisfied": have_mod("cryptography"),
                 "install": f"{_pipi} 'cryptography>=42'", "purpose": "all PKI (CA / cert / PKCS#12 / CRL)"},
            ]},
            # Network controls: emitted ONLY on AP-managed boxes (the lhpc-ap capability
            # gate) — a desktop never sees the entry at all. Same bootstrap:False leak
            # exclusion and cached-verdict probe rationale as the power entry below.
            *([{"title": "Network controls", "deps": [
                {"what": "Wi-Fi join authorization (polkitd + polkit rule)",
                 "required": False, "bootstrap": False,
                 "satisfied": self._network_authorized(),
                 "install": _deps_mod.network_rule_install_cmd(_getpass.getuser()),
                 "purpose": "the Apps page's Network panel (join Wi-Fi with AP fallback; "
                            "actions stay refused until this is installed)"},
            ]}] if self.network_supported() else []),
            # Time source: the copybox for an existing box. `bootstrap: False` for the same
            # reason as the two rules below — it has its own (default-on) scaffold in
            # bootstrap-deps.sh and must never be folded into the generated script twice.
            # The NMEA refusal lives in `deps.time_source_offer`, not here, so every surface
            # that renders this inherits it; the controller already knows its own GPS source,
            # so nothing re-parses the config.
            {"title": "Time source", "deps": [
                {"what": "clock discipline (chrony + gpsd)",
                 "required": False, "bootstrap": False,
                 "satisfied": self._time_source_present(),
                 # `dependencies.html` renders this command only when NOT satisfied, so what keeps
                 # the repair reachable is the per-run stamp: a setup that failed partway leaves
                 # the row unsatisfied and the command visible. The command itself is idempotent,
                 # and re-running it IS the documented repair.
                 "install": _deps_mod.time_source_offer(
                     self._gps_source_for_offer(), _getpass.getuser())[1],
                 "purpose": "a Pi has no battery-backed clock; chrony sets it from NTP, or from "
                            "GPS when nothing better is reachable. Re-running the command below "
                            "is also the repair when setup failed partway"},
            ]},
            {"title": "Power controls", "deps": [
                # `bootstrap: False` — this copybox embeds THIS box's username and has its own
                # dedicated (opt-out) scaffold in bootstrap-deps.sh, so `_declared_dep_scopes`
                # must never fold it into the generated script. `satisfied` is the CACHED
                # logind CanReboot verdict (see `_power_authorized`) — the ONE deliberate
                # exception to this method's no-subprocess rule (bounded busctl, at most one
                # per minute while unauthorized, cached permanently once yes): a file-presence
                # probe CANNOT work here — Debian ships /etc/polkit-1/rules.d 0750 root:polkitd,
                # unreadable to this process even when the rule is correctly installed.
                {"what": "reboot/shutdown authorization (polkitd + polkit rule)",
                 "required": False, "bootstrap": False,
                 "satisfied": self.power_supported(),
                 "install": _deps_mod.power_rule_install_cmd(_getpass.getuser()),
                 "purpose": "the dashboard's Reboot / Shut down buttons (logind authorization; "
                            "buttons stay hidden until this is installed)"},
            ]},
        ]

    def _gps_source_for_offer(self) -> str:
        """The configured GPS source, for the copybox decision only. Defensive because this
        method is also called in contexts that have no real runtime root (the README drift
        check builds one from a plain string), and a dependency LISTING must never be the thing
        that raises.

        A failure returns the UNKNOWN sentinel, not "". Those are different states: "" is an
        absent [gps] section, which legitimately means `auto` and is the fresh-image case;
        unknown means ownership cannot be determined, and `time_source_offer` refuses to render
        an installable command for it. The earlier version returned "" for both and so offered
        an `apt install` on a box whose configuration it had just failed to read."""
        from . import deps as _d
        try:
            return self.config().gps.source or ""
        except Exception:
            return _d.TIME_SOURCE_SOURCE_UNKNOWN

    def _time_source_present(self) -> bool:
        """Are the time source's own files on this box? File reads only, no subprocess.

        The witness is `TIME_SOURCE_STAMP_PATH`, which the setup REMOVES before it touches
        anything and writes only after its verdict passes — so it means "the most recent run
        completed", not "a run once completed". /usr/lib/clock-epoch cannot serve here: it is a
        persistent boot floor by design, so after one good install it survives every later failed
        re-run, and re-running bootstrap is the documented recovery path. Reporting satisfied then
        hides the repair copybox (`dependencies.html` renders it only when NOT satisfied), which
        is exactly when the operator needs it.

        A chrony installed by someone else, without our refclock, is still NOT this feature.
        Whether the daemons are actually healthy is `lhpc doctor`'s question and the setup's own
        loud nonzero exit; this row only says whether the files are there.
        """
        from . import deps as _deps_mod
        try:
            fs = self._system.fs
            # The fake-hwclock default is LHPC's own file, written by the same setup: a box set up
            # before fake-hwclock joined the time source lacks it, so its row offers the repair.
            return (fs.exists(_deps_mod.CHRONY_DROPIN_PATH)
                    and fs.exists(_deps_mod.FAKE_HWCLOCK_DEFAULT_PATH)
                    and fs.exists(_deps_mod.TIME_SOURCE_STAMP_PATH))
        except Exception:
            return False

    @invalidates_snapshot
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
                                              **selfupdate.status_view(self._paths)},
                                        details=["  nothing to run here — "
                                                 "state/selfupdate-migrate.json is damaged; the "
                                                 "box's operator inspects it and removes it, then "
                                                 "retries"])
                if status == "recovery_required":
                    return ActionResult(False, "Self-update check blocked: the checkout is at an "
                                        "unexpected commit for a recorded migration transition. No "
                                        "upstream check was made — recovery required (inspect "
                                        "state/selfupdate-migrate.json).",
                                        data={"recovery_required": True,
                                              **selfupdate.status_view(self._paths)},
                                        details=["  nothing to run here — the checkout is not at "
                                                 "the commit state/selfupdate-migrate.json "
                                                 "records (it was moved by hand); the box's "
                                                 "operator inspects that file and the checkout "
                                                 "before anything else"])
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
                                data=selfupdate.status_view(self._paths),
                                details=["  nothing to run here — lhpc could not open its lock "
                                         "file under state/locks/: a filesystem error, or a path "
                                         "there that is a symlink or escapes the runtime root; "
                                         "the box's operator checks state/locks/, then retries"])
        if not view["is_git"]:
            return ActionResult(False, "Self-update is unavailable (lhpc is not a git checkout).",
                                data=view,
                                details=["  nothing to run here — lhpc is installed without git "
                                         "(a copied folder or a package); only an install made by "
                                         "install.sh can self-update — the box's operator "
                                         "reinstalls it that way"])
        if not view["have_upstream"]:
            return ActionResult(False, f"Could not reach upstream: {view.get('upstream_error', '')}.",
                                data=view,
                                next_commands=["lhpc self-update"])
        if view["update_available"]:
            msg = (f"Update available — upstream {view['upstream_head_short']}"
                   f" (v{view['upstream_version'] or '?'}).")
            if view.get("ff_blocked"):
                msg += (" — local HEAD is not an ancestor of the current upstream; a normal "
                        "fast-forward update will be REFUSED. Review the divergence, then use "
                        "`--overwrite` (or the web confirmation) to reset onto upstream.")
            return ActionResult(True, msg, data=view)
        return ActionResult(True, "Up to date.", data=view)

    @invalidates_snapshot
    def self_update_apply(self, *, force: bool = False) -> ActionResult:
        """Apply the update as ONE serialized, fail-closed transaction (the interprocess self-update
        lock covers candidate capture, journal persistence, fetch/ref resolution, merge/reset/clean,
        cache writes, config migration and journal finalization). BLOCKED while an lhpc job is active;
        a concurrent apply returns 'busy' with zero mutation. A DIRTY tree is refused unless
        `force=True`. Default-equal config is migrated to the new defaults only when the source
        transition it was captured against actually completed — recorded DURABLY before source changes
        and recovered from the journal after a crash. Cleanup failure on force is a truthful partial."""
        from . import reslock, selfupdate
        from .service_base import AdmissionRefused
        # LOCK ORDER: (1) task admission FIRST — held across the WHOLE mutation so no task can be
        # reserved/spawned/started while the checkout + venv are changing; (2) controller-runtime +
        # self-update locks. `_admission_guard`'s strict check ALSO enforces "direct/operator apply
        # requires the request state ABSENT" (a pending/in-flight/malformed request blocks) and refuses
        # during an uninstall — with zero mutation. An already-admitted task -> typed busy.
        try:
            with self._admission_guard("self-update-apply"):
                # Re-run ALL authoritative blocker checks AFTER admission is held (nothing can have
                # started a job/auto-install/HMAC in the acquisition window).
                blk = self._self_update_blockers()
                if blk:
                    return ActionResult(False, f"Self-update blocked: {blk[0]} — resolve it before "
                                        "self-updating.", data={f"blocked_by_{blk[1]}": True},
                                        details=["  wait for that work to finish (or recover it "
                                                 "on its page in the console), then retry"],
                                        next_commands=["lhpc self-update --apply"])
                # LIVE identity gate (recomputed here, NEVER trusting the cache): only a genuinely
                # UNSAFE self-hosted checkout blocks apply before any mutation.
                if self.controller() is not None:
                    idv = self.controller_identity_live()
                    if idv.get("status") == "unsafe":
                        cmds, why = _identity_remedy(idv["reason"], self._paths.runtime_root,
                                                     self.controller())
                        return ActionResult(False, f"Self-update blocked: unsafe controller identity "
                                            f"({idv['reason']}). No changes were made.",
                                            data={"identity_unsafe": True, "identity": idv},
                                            next_commands=cmds, details=why)
                # controller-runtime EXCLUSIVE (so the running web server, holding it SHARED, can never
                # have its source mutated underneath it), THEN the self-update lock. Both non-blocking.
                with (selfupdate.controller_runtime_lock(self._paths, exclusive=True),
                      selfupdate.update_lock(self._paths)):
                    return self._self_update_locked(force)
        except AdmissionRefused as _adm:
            return ActionResult(False, _adm.reason, data={"admission_blocked": _adm.tag})
        except reslock.ResourceBusy:
            return ActionResult(False, "A task is starting right now (admission contended) — try the "
                                "update again shortly.", data={"contended": True},
                                next_commands=["lhpc self-update --apply"])
        except selfupdate.ControllerRuntimeBusy:
            return ActionResult(
                False, "lhpc-web.service is running — stop it, update, then start it again.",
                details=["(or just click 'Update now' in the web console — it does all this)"],
                next_commands=["systemctl --user stop lhpc-web",
                               "lhpc self-update --apply",
                               "systemctl --user start lhpc-web"],
                data={"web_running": True})
        except selfupdate.ControllerRuntimeLockError:
            return ActionResult(False, "Could not acquire the controller-runtime lock (unsafe runtime "
                                "state) — aborting without changes.", data={"lock_error": True},
                                details=["  nothing to run here — lhpc could not open its lock "
                                         "file under state/locks/: a filesystem error, or a path "
                                         "there that is a symlink or escapes the runtime root; "
                                         "the box's operator checks state/locks/, then retries"])
        except selfupdate.SelfUpdateBusy:
            return ActionResult(False, "A self-update is already in progress — try again shortly.",
                                data={"busy": True},
                                next_commands=["lhpc self-update --apply"])
        except selfupdate.UpdateLockError:
            return ActionResult(False, "Could not acquire the self-update lock (unsafe runtime state) "
                                "— aborting without changes.", data={"lock_error": True},
                                details=["  nothing to run here — lhpc could not open its lock "
                                         "file under state/locks/: a filesystem error, or a path "
                                         "there that is a symlink or escapes the runtime root; "
                                         "the box's operator checks state/locks/, then retries"])

    def _refresh_units_post_update(self):
        """VERIFY the managed units against the NEW checkout, out of process. Returns
        (ok, detail).

        Verification is a subprocess because it MUST be: the running interpreter still
        holds the PRE-update `updater_units`, so an in-process check renders the OLD
        templates and cheerfully approves stale units.

        The repair attempt below is deliberately best-effort and is NOT a migration
        mechanism. It runs in-process, so it too renders pre-update templates; and on the
        systemd-helper route it cannot write at all (`ProtectHome=read-only` — verified:
        `touch ~/.config/systemd/user/...` -> EROFS). It therefore only ever fixes units
        that drifted for some OTHER reason, never units whose template changed in the
        update being applied.

        What makes that acceptable TODAY is an invariant, not a mechanism: no release so far
        has changed any unit's bytes (see tests/test_updater_units.py::
        test_unit_bytes_are_the_frozen_render). The moment a release does change them,
        this is not enough — a real two-stage migration is required, and until it exists
        the verification below is what turns a silent boot-restore outage into a visible
        partial update. See docs/backlog.md.
        """
        import subprocess
        import sys as _sys

        from . import updater_units
        # Only units that are provably THIS deployment's may be rewritten. A foreign,
        # ambiguous or overridden set is someone else's file — leave it, and do not
        # blame the update for it.
        try:
            integ = self.updater_integration()
            ours = integ.get("fixable")
            helper = (integ.get("request") == "in_flight" and self._helper_owns_inflight()
                      and not self.uninstall_guard_blocks())
            if helper:
                # The one-click helper's OWN in-flight record (proven this process's) is not a
                # recovery condition: judge the units on their own verdicts. The helper only
                # verifies: its sandbox cannot write units, and the repair refuses on that record.
                ours = all(v in (updater_units.OK, updater_units.MISSING,
                                 updater_units.MODIFIED_OURS) for v in integ["per_unit"].values())
            if not ours:
                return True, "units not this deployment's — left untouched"
        except Exception as exc:
            return True, f"integration state unavailable ({type(exc).__name__}) — units left untouched"
        root = str(self._paths.runtime_root)
        _r, _checkout, venv = updater_units.deployment_paths(root)
        py = _op_join(venv, "bin", "python")
        if not _op_exists(py):
            py = _sys.executable
        try:
            rep = None if helper else self.self_update_repair_integration(restart=False)
        except Exception as exc:
            return False, f"unit refresh raised {type(exc).__name__}: {exc}"[:160]
        try:
            chk = subprocess.run([py, "-m", "lhpc.core.updater_units", "verify-set", root],
                                 capture_output=True, text=True, timeout=60, check=False)
        except (OSError, subprocess.SubprocessError) as exc:
            return False, f"unit verification could not run: {exc}"[:160]
        if chk.returncode == 0:
            return True, "units canonical"
        detail = (chk.stdout or chk.stderr or "").strip()[:140]
        if rep is None:
            return False, f"units not canonical: {detail}"
        return False, f"units still not canonical after repair ({rep.summary[:60]}): {detail}"

    @staticmethod
    def _source_advanced(res: ActionResult) -> bool:
        """THE one test both self-update paths (operator `_apply_and_sync` and the one-click helper)
        use for "the checkout moved, so sync the venv and refresh the units". A forced update whose
        `reset --hard` succeeded but whose `git clean` failed HAS advanced (`cleanup_failed`, set only
        after a successful reset in `apply_update`), though its result is a truthful ok=False."""
        return (res.ok or bool(res.data.get("cleanup_failed"))) and not res.data.get("already")

    # The one-click helper's unit restarts the console itself (OnSuccess=/OnFailure=lhpc-web.service,
    # updater_units), so its cleanup-only partial must not ask for a restart that already happened.
    _ONECLICK_CLEANUP_SUMMARY = ("Update aligned to upstream, but some untracked files could NOT be "
                                 "removed — delete them manually; the console restarts automatically.")

    # Kept on a later failure's summary, so the manual remedy for the leftovers is not lost.
    _CLEANUP_NOTE = " Also delete the untracked files the update could not remove."

    def _with_cleanup_note(self, res: ActionResult, summary: str) -> str:
        return summary + self._CLEANUP_NOTE if res.data.get("cleanup_failed") else summary

    def _apply_and_sync(self, force: bool) -> ActionResult:
        """Apply the source update, then — ONLY on a REAL advance — synchronize the editable venv install
        with the SAME `sys.executable -m pip install -e <repo-root>` the managed helper runs, so a
        dependency change never leaves the install unusable. MUST be called with task admission already
        HELD (by the operator flow) so nothing starts between apply and sync. A no-op/already-current or a
        failed/refused apply runs NO pip. On a real advance the result carries `update_applied=True`; a
        pip failure returns `ok=False` + `venv_sync_failed=True`, preserving the apply-result data."""
        import dataclasses as _dc
        import sys as _sys

        from . import selfupdate
        res = self.self_update_apply(force=force)
        if not (self._source_advanced(res) or self._resumes_sync(res)):
            return res                                        # no-op / already-current / failed / refused
        res = _dc.replace(res, data={**res.data, "update_applied": True})   # a real source advance
        root = selfupdate.repo_root()
        if root is not None:
            pip = self._system.runner.run(
                [_sys.executable, "-m", "pip", "install", "-e", str(root)], self._PIP_SYNC_TIMEOUT_S)
            if pip.returncode != 0:
                detail = selfupdate._summarize_output(pip.stderr or pip.stdout)
                # Named and recorded, or — the record not written — the cause and the command
                # to run by hand (`_incomplete_outcome`); ok=False either way.
                summary, nxt, extra = self._incomplete_outcome(
                    res, "venv-unsynced",
                    "the venv sync FAILED" + (f" ({detail})" if detail else ""))
                return _dc.replace(res, ok=False, summary=summary, next_commands=nxt,
                                   data={**res.data, "venv_sync_failed": True, **extra})
            self._incomplete_clear("venv-unsynced")

        # Re-render the managed units. An update whose new version changes a unit TEMPLATE
        # leaves the installed unit non-canonical, and boot restore then refuses to run —
        # the box comes back from a power cycle with NOTHING started, and nothing points at
        # the updater as the cause. Boot restore cannot repair it itself (it runs with
        # ProtectHome=read-only and cannot write unit files), so the updater — which caused
        # the drift and has the context — must leave them canonical. Only when they are
        # provably ours; no restart here (the update flow owns that).
        ok_units, unit_detail = self._refresh_units_post_update()
        res = _dc.replace(res, data={**res.data, "units_refreshed": ok_units,
                                     "units_refresh_detail": unit_detail})
        if not ok_units:
            # VISIBLE, not buried in data: a successful-looking update that left stale
            # units disables boot restore, and the operator has no reason to suspect it.
            summary, nxt, extra = self._units_stale_outcome(res, unit_detail)
            res = _dc.replace(
                res, ok=False, data={**res.data, "reason": "units-refresh-failed", **extra},
                next_commands=nxt,
                summary=summary)
        else:
            self._units_stale_clear()
        return res

    @invalidates_snapshot
    def self_update_apply_operator(self, *, force: bool = False) -> ActionResult:
        """OPERATOR-CONTEXT `lhpc self-update --apply`: WARN-then-DO under a CONTINUOUSLY-held task
        admission lock. If the managed web console is running it holds the controller-runtime lock
        SHARED, so we STOP lhpc-web, apply + sync the venv, then START it again. When the console is NOT
        running we STILL apply AND sync the venv (a dependency change must never leave it unusable) — the
        only difference is no service control. Admission is NEVER released between apply and sync in
        either case. REFUSES inside a managed unit (a managed process must never drive systemctl)."""
        import os as _os

        from . import reslock, updater_units
        from .service_base import AdmissionRefused
        if _os.environ.get("INVOCATION_ID"):
            return ActionResult(False, "refusing to stop/start services from a managed unit — run "
                                "`lhpc self-update --apply` from an interactive operator shell",
                                data={"reason": "managed-unit"},
                                next_commands=["lhpc self-update --apply"])
        _S = 30.0
        act = self._system.runner.run(
            ["systemctl", "--user", "is-active", "--quiet", updater_units.WEB_UNIT], _S)
        web_active = (not getattr(act, "not_found", False)) and act.returncode == 0
        try:
            with self._admission_guard("self-update-operator"):
                if not web_active:
                    # No service to orchestrate — but STILL apply AND sync the venv, under held admission.
                    return self._operator_outcome(self._apply_and_sync(force), restarted=False)
                stop = self._system.runner.run(["systemctl", "--user", "stop", updater_units.WEB_UNIT], _S)
                if getattr(stop, "not_found", False) or stop.returncode != 0:
                    return ActionResult(False, "could not stop lhpc-web.service — stop it manually then retry",
                                        next_commands=["systemctl --user stop lhpc-web",
                                                       "lhpc self-update --apply",
                                                       "systemctl --user start lhpc-web"],
                                        data={"stop_failed": True})
                try:
                    res = self._apply_and_sync(force)         # admission still held; venv synced here
                finally:
                    start = self._system.runner.run(
                        ["systemctl", "--user", "start", updater_units.WEB_UNIT], _S)
                    restart_failed = getattr(start, "not_found", False) or start.returncode != 0
                if restart_failed:
                    # A failed REQUIRED restart is ALWAYS ok=False (the console is unavailable). Distinguish
                    # partial success: the source update may have applied (update_applied) — preserve that
                    # AND any venv_sync failure. The summary gives the exact recovery command, no raw output.
                    return ActionResult(False, res.summary + "  — AND lhpc-web did NOT restart. Recover "
                                        "with: systemctl --user start lhpc-web.service",
                                        details=tuple(res.details),
                                        data={**dict(res.data), "web_restart_failed": True,
                                              "update_applied": bool(res.data.get("update_applied"))},
                                        next_commands=["systemctl --user start lhpc-web.service"])
                return self._operator_outcome(res, restarted=True)
        except AdmissionRefused as _adm:
            return ActionResult(False, _adm.reason, data={"admission_blocked": _adm.tag})
        except reslock.ResourceBusy:
            return ActionResult(False, "A task is starting right now (admission contended) — retry the "
                                "update.", data={"contended": True},
                                next_commands=["lhpc self-update --apply"])

    @staticmethod
    def _operator_outcome(res: ActionResult, *, restarted: bool) -> ActionResult:
        """The operator flow has already synced the venv and (when the console was running)
        restarted it, so the request path's advice — "restart the web console", the venv-sync
        command, the dependencies note — would describe steps already taken. Replace it with what
        happened; every other line (migrations, firewall) stays. Only a successful real advance."""
        import dataclasses as _dc

        from . import selfupdate
        partial = bool(res.data.get("cleanup_failed"))
        if not ((res.ok or partial) and res.data.get("update_applied")):
            return res
        instr = res.data.get("restart") or {}
        done = {instr.get("note", ""), "Restart the web console to load the new version:",
                "Restart the web console after cleaning up:"}
        done |= {"  " + c for c in instr.get("commands", ())}
        details = [d for d in res.details if d and d not in done]
        details.append("The web console was restarted on the new version." if restarted else
                       "The web console is not running; it loads the new version when started.")
        summary = "Update applied." if res.summary == selfupdate.APPLIED_RESTART_MESSAGE else res.summary
        if partial and res.summary == res.data.get("message"):
            # The restart advice in apply_update's partial message is already done here.
            summary = ("Update applied, but some untracked files could NOT be removed — delete them "
                       "manually (no restart needed).")
        return _dc.replace(res, summary=summary, details=tuple(details), next_commands=[])

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
                                data={"journal_corrupt": True},
                                details=["  nothing to run here — state/selfupdate-migrate.json "
                                         "is damaged; the box's operator inspects it and removes "
                                         "it, then retries"])
        if status == "recovery_required":
            return ActionResult(False, "Self-update blocked: the checkout is at an unexpected commit for "
                                "a recorded migration transition. No changes were made — recovery "
                                "required (inspect state/selfupdate-migrate.json).",
                                data={"recovery_required": True},
                                details=["  nothing to run here — the checkout is not at the "
                                         "commit state/selfupdate-migrate.json records (it was "
                                         "moved by hand); the box's operator inspects that file "
                                         "and the checkout before anything else"])
        completed = env.get("completed") if env else None
        prepared = env.get("prepared") if env else None

        # 2. Reconcile a prior PREPARED attempt (classifier verified its ANCHOR + endpoint): its to_head
        #    reached -> promote (carrying the anchor txid); still at from_head -> the git never happened,
        #    so delete its anchor and drop it, keeping the prior completed intact.
        if prepared:
            if head_now == prepared["to_head"]:
                completed = self._promote(prepared)          # keep the anchor for the completed slot
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
                                    "— recovery required.", data={"recovery_required": True},
                                    details=["  nothing to run here — the migration recorded in "
                                             "state/selfupdate-migrate.json has no matching "
                                             "record in the checkout's git; the box's operator "
                                             "inspects that file before anything else"])
            m, remaining = self._run_migration(anchor["pending"], anchor["from_head"])
            migrated += m
            if remaining:
                return ActionResult(True, "Prior config migration is incomplete — deferring the update "
                                    "until it completes; it will be retried on the next self-update.",
                                    data={"migrated": migrated, "pending_migrations": len(remaining),
                                          "deferred_recovery": True})
            if selfupdate.clear_migration_journal(self._paths):         # clear FIRST; drop anchor only if
                selfupdate.delete_anchor(self._system, completed.get("txid"))   # the journal is really gone

        # An anchor the journal no longer names (a stop before the journal write, a failed delete)
        # is reached by nothing else: drop it before a new attempt anchors its own.
        selfupdate.sweep_anchors(self._system, {r.get("txid") for r in (completed, prepared) if r})

        # 4. Attempt a NEW update. Fail-closed PREPARE hook creates the durable git ANCHOR, then the
        #    runtime journal referencing it, BOTH atomically BEFORE the checkout is advanced.
        new_candidates = self._migration_candidates()
        hook = {"written": False, "from": "", "to": "", "branch": "", "intent": [], "txid": ""}

        class _FWAbort(Exception):
            def __init__(self, result):
                self.result = result

        def _before_mutation(from_head, to_head, branch, _deps):
            # FW P1-2C: BEFORE the checkout advances, refuse if the managed firewall is installed,
            # remote web is exposed, and the nginx unit carrying the boot gate cannot be brought
            # current this run — otherwise a reboot could start remote nginx ungated.
            fw_abort = self.firewall_update_nginx_preflight()
            if fw_abort is not None:
                raise _FWAbort(fw_abort)
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

        helper_rev = self._fw_packaged_helper_rev()
        try:
            res = selfupdate.apply_update(self._system, self._paths, force=force,
                                          before_mutation=_before_mutation)
        except _FWAbort as abort:                            # firewall preflight refused the advance
            return abort.result
        except selfupdate.JournalPersistError:
            return ActionResult(False, "Refusing to self-update: could not durably record the config-"
                                "migration intent before changing source. No changes were made.",
                                data={"journal_write_failed": True},
                                details=["  nothing to run here — the migration journal "
                                         "(state/selfupdate-migrate.json) or its git anchor in "
                                         "the checkout could not be written; the box's operator "
                                         "checks state/ and the checkout's .git, then retries"])

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
            # ...only when HEAD is positively still at from_head (git never moved the ref): a git
            # killed AFTER the ref moved left HEAD at to_head, and an unreadable HEAD proves nothing,
            # so either keeps this record for the next run to promote and migrate, or report.
            if (selfupdate.local_state(self._system).get("head", "") == hook["from"]
                    and selfupdate.clear_migration_journal(self._paths)):
                selfupdate.delete_anchor(self._system, hook["txid"])

        # FW P1-2 B/C: on a real advance, the firewall scripts + LHPC-owned nginx unit must be
        # regenerated with the NEW templates. This process imported the old modules BEFORE the
        # update, so it must NOT do that here (it would emit the previous version) — instead mark
        # it, and the freshly-restarted (new-code) console reconciles on startup.
        fw_notes, data_fw = [], False
        if res.get("ok") and not res.get("already"):
            self._fw_mark_post_update()
            if self._fw_integration_state() != "absent":
                fw_notes = ["Firewall integration will be refreshed automatically when the "
                            "console restarts under the new version."]
                # The helper file is read from disk, so this (old) process sees the new one. A
                # changed helper leaves the installed one stale until re-applied — and the next
                # boot starts the console loopback-only. Say it now, before that reboot.
                if self._fw_packaged_helper_rev() != helper_rev:
                    data_fw = True
                    fw_notes.append(
                        "This update changes the firewall helper — re-apply the firewall before you "
                        "reboot: " + "; ".join(["lhpc firewall --script > /dev/null",
                                               *self._fw_apply_lines()]) + "."
                        + (" Until then a reboot starts the console LOOPBACK-ONLY (remote access "
                           "off)." if self._fw_remote_web_exposed() else ""))

        instr = selfupdate.restart_instructions(res.get("deps_changed", False),
                                                self._controller_deps_sync_cmd())
        data = {**res, "restart": instr, "migrated": migrated, "pending_migrations": len(remaining),
                "firewall_reapply_required": data_fw}
        migrated_note = f"{migrated} default(s) migrated to the new defaults." if migrated else ""
        pending_note = (f"{len(remaining)} config default migration(s) could NOT be completed and will "
                        "be retried on the next self-update.") if remaining else ""

        if not res["ok"]:                                    # git failure: dirty refusal / diverged / fetch
            # On a DIRTY refusal, name the paths a force would discard. `message` stays single-line
            # (it is flashed verbatim); the evidence rides in details.
            refusal = [f"  {ln}" for ln in res.get("changes", ())]
            if refusal:
                refusal.insert(0, "These paths would be discarded by 'overwrite local changes':")
            nxt = []
            if res.get("detached"):
                # A developer moved the checkout off its branch: put it back, then update.
                nxt = [f"git -C {selfupdate.repo_root()} switch "
                       f"{getattr(self.controller(), 'branch', 'main')}", "lhpc self-update --apply"]
            elif res.get("unreachable"):
                refusal = ["  nothing to run here — the box could not reach the checkout's origin "
                           "(network, DNS or the remote itself); once it can, run the same "
                           "command again"]
            elif res.get("needs_overwrite") or refusal:
                refusal.append("Resetting the checkout to upstream discards exactly those local "
                               "commits/changes and keeps nothing of them.")
                nxt = ["lhpc self-update --apply --overwrite"]
            return ActionResult(False, res["message"], details=refusal, next_commands=nxt,
                                data=data)
        if res.get("cleanup_failed"):                        # updated, but untracked cleanup failed -> partial
            details = [res.get("cleanup_error", ""), instr.get("note", ""),
                       "Restart the web console after cleaning up:"]
            details += ["  " + c for c in instr["commands"]]
            details += [n for n in (migrated_note, pending_note) if n]
            details += list(fw_notes)
            return ActionResult(False, res["message"], data=data,
                                details=tuple(d for d in details if d),
                                next_commands=list(instr["commands"]))
        if res.get("already"):                               # nothing to update; may have recovered pending
            details = tuple(n for n in (migrated_note, pending_note) if n)
            return ActionResult(True, res["message"], data=data, details=details)
        details = [n for n in (instr.get("note", ""),) if n]
        details += ["Restart the web console to load the new version:"]
        details += ["  " + c for c in instr["commands"]]
        details += [n for n in (migrated_note, pending_note) if n]
        details += list(fw_notes)
        return ActionResult(True, res["message"], data=data, details=tuple(details),
                            next_commands=list(instr["commands"]))

    @staticmethod
    def _fw_packaged_helper_rev() -> str:
        """The packaged firewall helper's revision as it is on disk now ("" if unreadable)."""
        from . import firewall as _fw
        try:
            return _fw.integration_rev()
        except (OSError, ValueError):
            return ""

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
        if req in ("in_flight", "malformed") or self.uninstall_guard_blocks():
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

    def self_update_local_changes(self, limit: int = 20) -> tuple:
        """The paths an overwrite would discard (`git status --porcelain`). Local git, POST-time
        only — the confirm must SHOW what it is about to reset, not just assert that it must."""
        from . import selfupdate
        return selfupdate.local_changes(self._system, limit)

    def self_update_divergence(self) -> tuple:
        """`(ahead, behind)` of the local history vs the fetched upstream ref. Local git, POST-time
        only. Names the size of the divergence the confirm otherwise only alludes to."""
        from . import selfupdate
        return selfupdate.divergence(self._system)

    def self_update_branch(self) -> str:
        """The checkout's branch, for naming the upstream ref in the confirm (`origin/<branch>`)."""
        from . import selfupdate
        return str(selfupdate.local_state(self._system).get("branch") or "main")

    # ---- web trigger: write the exclusive request marker (NO systemctl, NO bus) ---------------

    @invalidates_snapshot
    def self_update_trigger(self, *, overwrite: bool = False, queue: bool = True) -> ActionResult:
        """WEB stage-2: admit exactly one update request by EXCLUSIVELY creating the in-root
        request marker (payload `normal`|`overwrite` — a 1-bit selector the helper re-validates).
        A static .path unit consumes it. Refuses unless this process is the MANAGED web unit
        (INVOCATION_ID) with a byte-exact integration, no active job, an available+safe checkout,
        and no pending/in-flight/uninstall evidence — so a foreground console or a tampered unit
        never writes a request nobody safely consumes. `queue=False` is the preflight: every gate
        runs the same way, and it returns just before the marker would be written (nothing is)."""
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
                                "`lhpc self-update --recover-request`.", data={"recovery_required": True},
                                next_commands=["lhpc self-update --recover-request"])
        if integ["status"] != "ok":
            return ActionResult(False, "One-click update is unavailable — the web/updater units are "
                                f"not the canonical managed set ({integ['status']}). Run `lhpc "
                                "self-update --repair-integration`, or `lhpc self-update --apply`.",
                                data={"integration": integ["status"]},
                                next_commands=["lhpc self-update --repair-integration",
                                               "lhpc self-update --apply"])
        st = self.self_update_status()
        if not st.get("available"):
            return ActionResult(False, "Self-update is unavailable — lhpc is not running from a "
                                "git checkout.", data={"unavailable": True},
                                details=["  nothing to run here — lhpc is installed without git "
                                         "(a copied folder or a package); only an install made by "
                                         "install.sh can self-update — the box's operator "
                                         "reinstalls it that way"])
        idv = st.get("identity")
        if isinstance(idv, dict) and idv.get("status") == "unsafe":
            cmds, why = _identity_remedy(str(idv.get("reason", "")), self._paths.runtime_root,
                                         self.controller())
            return ActionResult(False, "Self-update blocked: unsafe controller identity "
                                f"({idv.get('reason', '')}).", data={"identity_unsafe": True},
                                next_commands=cmds, details=why)
        mode = "overwrite" if overwrite else "normal"
        import contextlib

        from . import reslock
        from .service_base import AdmissionRefused
        # Hold task admission through the EXCLUSIVE request-marker creation (lock order #1): a new task
        # cannot start while we create it. Recheck the uninstall guard + request state AND run the
        # complete strict blocker scan UNDER the lock, so nothing slips in between the checks and the
        # atomic marker create.
        with contextlib.ExitStack() as adm:
            try:
                self._acquire_key(adm, self.ADMISSION_KEY, "self-update-trigger", "")
            except reslock.ResourceBusy:
                return ActionResult(False, "A task is starting right now (admission contended) — retry "
                                    "the update.", data={"contended": True},
                                    next_commands=["lhpc self-update --apply"])
            except AdmissionRefused as _adm:
                # The power-pending gate lives INSIDE _acquire_key (one choke point): a
                # reboot/shutdown in flight must not admit a self-update it would kill.
                return ActionResult(False, _adm.reason, data={"admission_blocked": _adm.tag})
            if self.uninstall_guard_blocks():
                return ActionResult(False, "A controller uninstall is in progress — cannot self-update.",
                                    data={"uninstalling": True},
                                    next_commands=["lhpc self-update --recover-request"])
            if self.classify_request() != "absent":
                return ActionResult(False, "An update request is already pending — the console is about "
                                    "to update.", data={"already_pending": True},
                                    details=["  nothing to run here — the queued update starts by "
                                             "itself; wait for the console to come back"])
            blk = self._self_update_blockers()
            if blk:
                return ActionResult(False, f"Self-update blocked: {blk[0]}.",
                                    data={f"blocked_by_{blk[1]}": True},
                                    details=["  wait for that work to finish (or recover it on "
                                             "its page in the console), then retry"],
                                    next_commands=["lhpc self-update --apply"])
            if not queue:
                return ActionResult(True, "Update can be queued.", data={"preflight": True,
                                                                          "mode": mode})
            try:
                m = runtime_fs.open_marker_excl(self._paths,
                                                self._paths.under(*updater_units.REQUEST_REL),
                                                mode + "\n")
                m.close()
            except FileExistsError:
                return ActionResult(False, "An update request is already pending — the console is about "
                                    "to update.", data={"already_pending": True},
                                    details=["  nothing to run here — the queued update starts by "
                                             "itself; wait for the console to come back"])
            except Exception as exc:                           # containment / fs error
                return ActionResult(False, f"Could not queue the update request: {exc}",
                                    data={"trigger_failed": True},
                                    details=["  nothing to run here — the error named above "
                                             "kept lhpc from creating state/selfupdate.request; "
                                             "the box's operator fixes what it names, then "
                                             "retries"])
        return ActionResult(True, "Update queued — the console will stop, update itself and come "
                            "back automatically.", data={"triggered": True, "mode": mode})

    # ---- the helper (unit ExecStart): claim -> apply -> sync -> record -> release -------------

    @invalidates_snapshot
    def self_update_run_service(self) -> ActionResult:
        """PLUMBING, run ONLY by lhpc-selfupdate.service. Holds task ADMISSION across the COMPLETE
        transaction (claim -> apply -> venv sync -> durable record -> in-flight release) so no task can
        be started while the checkout/venv are still changing (closing the post-update window).
        Admission is acquired RAW — the helper OWNS the in-flight record it is about to write, so the
        strict request self-check would wrongly refuse it — but it is still reentrant, so the inner
        `self_update_apply` reuses the SAME lock. A concurrent holder -> typed busy, zero mutation."""
        import contextlib

        from . import reslock
        with contextlib.ExitStack() as adm:
            try:
                self._admit_raw(adm, "self-update-helper")
            except reslock.ResourceBusy:
                return ActionResult(False, "A task is starting right now (admission contended) — the "
                                    "update helper will retry on the next request.", data={"contended": True},
                                    details=["  nothing to run here — the update helper takes the "
                                             "next request; click Update again in a moment"])
            return self._self_update_run_service_locked()

    def _self_update_run_service_locked(self) -> ActionResult:
        """The helper body, run UNDER the held task-admission lock (see `self_update_run_service`):
        claim -> prove ownership -> apply -> venv sync -> durable record -> in-flight release."""
        import dataclasses as _dc
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
                                data={"recovery_required": True},
                                next_commands=["lhpc self-update --recover-request"])
        except Exception as exc:
            return ActionResult(False, f"Could not claim the update request: {exc}",
                                data={"claim_failed": True},
                                next_commands=["lhpc self-update --recover-request"])
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
                                data={"malformed": True},
                                next_commands=["lhpc self-update --recover-request"])
        force = (mode == "overwrite")
        runtime_fs.write_marker(self._paths, inflight, json.dumps(self._helper_identity(mode)))
        # PROVE this exact helper owns the in-flight record it just wrote (durable PID + /proc start
        # time) — defends against a concurrent clobber between claim and write, and against a foreign/
        # forged/PID-reused owner. Unproven -> block, retain evidence, recovery required.
        if not self._helper_owns_inflight():
            selfupdate.record_last_apply_strict(
                self._paths, ok=False,
                summary="In-flight update ownership could not be proven — recovery required.")
            return ActionResult(False, "In-flight update ownership could not be proven — recovery "
                                "required (`lhpc self-update --recover-request`).",
                                data={"ownership_unproven": True},
                                next_commands=["lhpc self-update --recover-request"])

        res = ActionResult(False, "Self-update service did not run.", data={},
                           next_commands=["lhpc self-update --recover-request"])
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
                                           "lock — no changes made.", data={"web_running": True},
                                           next_commands=["lhpc self-update --apply"])
                        raise _StopRun() from None
                    _time.sleep(0.5)
                except selfupdate.ControllerRuntimeLockError:
                    res = ActionResult(False, "Could not acquire the controller-runtime lock "
                                       "(unsafe runtime state) — no changes made.",
                                       data={"lock_error": True},
                                       details=["  nothing to run here — lhpc could not open "
                                                "its lock file under state/locks/: a filesystem "
                                                "error, or a path there that is a symlink or "
                                                "escapes the runtime root; the box's operator "
                                                "checks state/locks/, then retries"])
                    raise _StopRun() from None
            res = self.self_update_apply(force=force)
            if self._source_advanced(res) or self._resumes_sync(res):   # incl. reset+clean-failed
                root = selfupdate.repo_root()
                if root is not None:
                    t0 = _time.monotonic()
                    pip = self._system.runner.run(
                        [sys.executable, "-m", "pip", "install", "-e", str(root)],
                        timeout=self._PIP_SYNC_TIMEOUT_S)
                    # The slow-target budget's L4 quantity; the unit appends stderr to
                    # logs/lhpc-selfupdate.log (docs/test-matrix.md#slow-target-baseline).
                    print(f"[selfupdate] pip sync {_time.monotonic() - t0:.1f} s",
                          file=sys.stderr, flush=True)
                    if pip.returncode != 0:                     # P2: a failed sync FAILS the update
                        # First line of pip's diagnostics, stripped of box-drawing/ANSI so the
                        # persisted summary reads cleanly in the GUI flash (never a mid-box tail).
                        detail = selfupdate._summarize_output(pip.stderr or pip.stdout)
                        summary, nxt, extra = self._incomplete_outcome(
                            res, "venv-unsynced",
                            "the venv sync FAILED" + (f" ({detail})" if detail else ""))
                        res = ActionResult(False, summary, next_commands=nxt,
                                           data={**dict(res.data), "venv_sync_failed": True,
                                                 **extra})
                    else:
                        self._incomplete_clear("venv-unsynced")
                        # Refresh the managed units with the NEW code. This path applies
                        # inline (it does not go through _apply_and_sync), so without this
                        # a one-click update left the OLD units installed: the new version
                        # then reads its own integration as non-canonical, one-click
                        # updating goes away and boot restore is skipped.
                        ok_u, det_u = self._refresh_units_post_update()
                        if ok_u:
                            self._units_stale_clear()
                            summary, nxt, extra = (
                                self._ONECLICK_CLEANUP_SUMMARY if res.data.get("cleanup_failed")
                                else res.summary), [], {}
                        else:
                            summary, nxt, extra = self._units_stale_outcome(res, det_u)
                        res = ActionResult(
                            bool(res.ok) and ok_u, summary,
                            data={**dict(res.data), "units_refreshed": ok_u,
                                  "units_refresh_detail": det_u, **extra},
                            next_commands=nxt)
        except _StopRun:
            pass
        # The record keeps the summary only, and the console shows that after the restart: the
        # re-apply warning (details carry the commands) must ride in it, on every branch above.
        if res.data.get("firewall_reapply_required"):
            res = _dc.replace(res, summary=res.summary + " Re-apply the firewall before you reboot: "
                              "this update changed the firewall helper.")
        # Record the outcome DURABLY, then release the in-flight record. If the STRICT record does
        # not persist, retain in-flight and report incomplete (one-click blocked until recovery) —
        # never delete the evidence on an unrecorded outcome.
        if not selfupdate.record_last_apply_strict(self._paths, ok=bool(res.ok), summary=res.summary):
            return ActionResult(False, "Update outcome could not be recorded durably — recovery "
                                "required (`lhpc self-update --recover-request`).",
                                data={**dict(res.data), "record_failed": True},
                                next_commands=["lhpc self-update --recover-request"])
        try:
            runtime_fs.unlink(self._paths, inflight)
        except Exception as exc:
            return ActionResult(False, res.summary + f" (in-flight marker cleanup FAILED: {exc} — "
                                "recovery required)", data={**dict(res.data), "cleanup_failed": True},
                                next_commands=["lhpc self-update --recover-request"])
        return res

    def _helper_owns_inflight(self) -> bool:
        """True iff the in-flight record's identity matches THIS process — its recorded PID AND that
        PID's current /proc start time both equal this process's. Fail-closed: a missing/malformed/
        unreadable record, a foreign PID, or a PID whose start time differs (PID reuse) all return
        False. Never trusts PID alone."""
        from . import runtime_fs, updater_units
        try:
            rec = json.loads(runtime_fs.read_text_regular(
                self._paths, self._paths.under(*updater_units.INFLIGHT_REL), max_bytes=4096))
        except Exception:
            return False
        if not isinstance(rec, dict):
            return False
        pid = rec.get("pid")
        return (pid == os.getpid()
                and rec.get("start_time") == _proc_start_time(os.getpid()))

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

    def uninstall_guard_blocks(self) -> bool:
        """STRICT, fail-CLOSED uninstall-guard inspection for admission/teardown decisions. Absence is
        the ONLY 'not uninstalling' result: a present guard of ANY kind (regular, symlink, directory,
        FIFO, device) blocks new work, and an inspection failure (cannot prove absent) ALSO blocks.
        Descriptor-anchored, no-follow. Do NOT use the fail-soft `_marker_present` (which returns False
        on any exception, and cannot tell absent from unreadable) for a security decision."""
        from . import runtime_fs, updater_units
        return runtime_fs.guard_state(
            self._paths, self._paths.under(updater_units.UNINSTALL_GUARD)) != "absent"

    def _task_admission_blocked(self) -> tuple[str, str] | None:
        """STRICT reason a NEW task start is refused under admission — `(reason, tag)` or None. Blocks
        when the uninstall guard is present/unsafe (strict) OR a self-update request is pending/in
        flight/malformed. Runs UNDER the held admission lock. Absent runtime root -> no markers -> None
        (callers skip the whole guard when the root is absent, so there is zero filesystem mutation)."""
        if self.uninstall_guard_blocks():
            return ("A controller uninstall is in progress (.lhpc-uninstalling) — refusing to start "
                    "new work. Let it finish, or recover it.", "uninstalling")
        try:
            req = self.classify_request()
        except Exception as exc:
            return (f"Could not verify controller update state ({exc}) — refusing to start new work.",
                    "unverifiable")
        if req in ("pending", "in_flight", "malformed"):
            return ("A controller self-update is pending or in progress — refusing to start new work "
                    "until it completes (`lhpc self-update --recover-request` if stuck).", req)
        return None

    def _self_update_blockers(self) -> tuple[str, str] | None:
        """The COMPLETE strict blocker scan SHARED by self_update_trigger and self_update_apply so both
        gate on identical logic. Blocks on: an active OR unprovable job (active_jobs(include_unsafe=True)
        — unsafe jobs dir / symlinked / non-regular / oversized / disappeared / malformed marker),
        unresolved auto-install, and running/interrupted/malformed/unsafe HMAC. ANY inspection exception
        fails CLOSED. Returns (reason, tag) or None. It does NOT check the request/uninstall markers —
        those are the admission (trigger) / request-ownership (apply) concern of each caller."""
        try:
            jobs = self.active_jobs(include_unsafe=True)
        except Exception as exc:
            return (f"could not inspect running jobs ({exc})", "jobs")
        if jobs:
            return ("an lhpc build/test/web job is running or its state cannot be proven safe", "jobs")
        try:
            ai = self._auto_install_gate()
        except Exception as exc:
            ai = f"unverifiable ({exc})"
        if ai:
            return (f"an auto-install run is unresolved — {ai}", "auto_install")
        try:
            hst = self.hmac_apply_status()
            if hst and (hst.get("unsafe") or hst.get("phase") in ("running", "interrupted")):
                return ("an HMAC apply is running or its state is unresolved/unsafe", "hmac")
        except Exception as exc:
            return (f"could not inspect HMAC state ({exc})", "hmac")
        return None

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

    @invalidates_snapshot
    def self_update_recover_request(self) -> ActionResult:
        """OPERATOR: one invocation inspects BOTH recoverable states — the update request/in-flight
        record AND the uninstall guard — never returning early after handling only one; a partial
        recovery (one cleared, the other still blocked) is reported truthfully. Request semantics
        are unchanged: pending is cleared; in-flight only when the recorded helper identity is
        proven ceased (never age-based). The uninstall guard is released ONLY when its recorded
        pid + process start time PROVE the owner ceased (a refused/interrupted uninstall leaves a
        guard whose owner is dead — this is the documented escape for a kept guard)."""
        # HALF-ISOLATION: an unexpected exception in one half (e.g. a corrupt in-flight record
        # raising out of json.loads, or an unlink OSError) must NEVER prevent the other half from
        # being attempted — each converts to a typed failed result instead of propagating.
        try:
            req_res = self._recover_update_state()
        except Exception as exc:
            req_res = ActionResult(False, f"Update-state recovery failed unexpectedly: {exc}",
                                   data={"request_recovery_error": True},
                                   details=["  nothing to run here — recovery stopped on the "
                                            "unexpected error above; the box's operator checks "
                                            "state/selfupdate.request and "
                                            "state/selfupdate.inflight by hand and reports the "
                                            "error"])
        try:
            guard_res = self._recover_uninstall_guard()
        except Exception as exc:
            guard_res = ActionResult(False, f"Uninstall-guard recovery failed unexpectedly: {exc}",
                                     data={"guard": "error"},
                                     details=["  nothing to run here — recovery stopped on the "
                                              "unexpected error above; the box's operator checks "
                                              "the .lhpc-uninstalling guard in the runtime root "
                                              "by hand and reports the error"])
        if guard_res is None:
            return req_res                          # no guard: request-only result and wording
        ok = req_res.ok and guard_res.ok
        return ActionResult(ok, f"{req_res.summary} {guard_res.summary}",
                            data={**req_res.data, **guard_res.data})

    def _recover_uninstall_guard(self):
        """Release a STALE uninstall guard, identity-proven: `pid` + `start_time` as positive
        integers (strict `_guard_owner_ints`) — malformed/unprovable keeps the guard with its
        path named. The whole
        read -> prove -> unlink sequence runs under the ONE per-root guard lock that also serializes
        claim/reclaim/release, so the guard proven stale is GUARANTEED to be the same guard removed —
        recovery can never delete a replacement guard a concurrent uninstall just claimed. Returns
        None when no guard exists (callers keep the request-only wording)."""
        from . import reslock, runtime_fs, updater_units
        from .paths import PathContainmentError
        from .service_base import _guard_owner_ints
        path = self._paths.under(updater_units.UNINSTALL_GUARD)
        try:
            with reslock.operation_lock(self._paths, "uninstall.guard", "guard-recover"):
                try:
                    raw = runtime_fs.read_text_regular(self._paths, path, max_bytes=4096)
                except FileNotFoundError:
                    return None
                except (OSError, PathContainmentError):
                    return ActionResult(False, f"An uninstall guard exists but is unreadable/unsafe "
                                        f"— NOT removing it ({path}).", data={"guard": "unsafe"},
                                        details=["  nothing to run here — the box's operator "
                                                 "makes sure no uninstall runs, then removes the "
                                                 "guard file named above by hand"])
                try:
                    rec = json.loads(raw)
                    pid, start = _guard_owner_ints(rec)
                except (ValueError, TypeError, KeyError):
                    return ActionResult(False, f"An uninstall guard exists but its owner record is "
                                        f"malformed — cannot prove the owner ceased; verify no "
                                        f"uninstall is running, then remove {path} by hand.",
                                        data={"guard": "malformed"},
                                        details=["  nothing to run here — the box's operator does "
                                                 "the check and the removal named above"])
                if not _proc_ceased(pid, start):
                    return ActionResult(False, "An uninstall guard is held by a LIVE process (an "
                                        "uninstall may be running) — not removing it.",
                                        data={"guard": "live"},
                                        details=["  nothing to run here — an uninstall is "
                                                 "running; it removes the guard itself when it "
                                                 "ends — wait for it"])
                try:
                    runtime_fs.unlink(self._paths, path)
                except (OSError, PathContainmentError) as exc:
                    return ActionResult(False, f"Could not remove the stale uninstall guard: {exc}",
                                        data={"guard": "unlink_failed"},
                                        details=["  nothing to run here — a filesystem error, "
                                                 "or a path under the runtime root that is a "
                                                 "symlink or escapes it (the error is named "
                                                 "above); the box's operator fixes it, then "
                                                 "retries"])
                return ActionResult(True, f"Cleared a stale uninstall guard (pid {pid} proven "
                                    "ceased).", data={"guard": "cleared"})
        except reslock.ResourceBusy as busy:
            return ActionResult(False, f"another uninstall-guard operation is in progress ({busy}) — "
                                "retry.", data={"guard": "contended"},
                                next_commands=["lhpc self-update --recover-request"])

    def _recover_update_state(self) -> ActionResult:
        """The request/in-flight half of recovery."""
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
                                data={"state": "malformed"},
                                details=["  nothing to run here — the box's operator does the "
                                         "check and the removal named above"])
        # in_flight: verify the recorded process is gone.
        rec = json.loads(runtime_fs.read_text_regular(self._paths, inflight, max_bytes=4096))
        if not _proc_ceased(rec.get("pid"), rec.get("start_time")):
            return ActionResult(False, "An update is still running (helper process alive) — wait "
                                "for it to finish before recovering.", data={"state": "running"},
                                details=["  nothing to run here — the update finishes by itself; "
                                         "wait for it, then retry"])
        # Record the interrupted outcome DURABLY *before* removing the evidence — if the strict
        # write fails, keep the in-flight marker so recovery can be retried (never silently clear).
        from . import selfupdate as _su
        if not _su.record_last_apply_strict(self._paths, ok=False,
                summary="A previous update was interrupted and did not complete."):
            return ActionResult(False, "Could not record the interrupted outcome durably — the "
                                "in-flight record is kept; try recovery again.",
                                data={"record_failed": True},
                                next_commands=["lhpc self-update --recover-request"])
        runtime_fs.unlink(self._paths, inflight)
        return ActionResult(True, "Cleared an interrupted update (helper had stopped); recorded it "
                            "as incomplete.", data={"cleared": "in_flight"})

    # ---- integration repair (operator shell, HAS bus) ----------------------------------------

    @invalidates_snapshot
    def self_update_repair_integration(self, *, restart: bool = True) -> ActionResult:
        """The one command that resolves `units-stale` (`units_stale`): its success clears that
        record and no other — a state it did not verify (`venv-unsynced`, `recovery-required`)
        stays recorded and is named in its result; see `_repair_integration_steps`."""
        import dataclasses as _dc
        res = self._repair_integration_steps(restart=restart)
        if res.ok:
            self._units_stale_clear()
            state, what = self.self_update_incomplete()
            if state:
                res = _dc.replace(res, summary=(
                    f"{res.summary} Still recorded, not cleared by this repair — {state}: {what}. "
                    f"Resolve it with: {self._incomplete_remedy(state)}"))
        return res

    # ---- the named states an applied self-update can leave -------------------------------------
    #
    # The checkout moved, but a step after it failed. Undoing the update is not one existing
    # operation (the previous checkout, its venv and the config migrations already run would all
    # have to come back), so the update ends in a NAMED state instead — journaled here, shown by
    # `lhpc status` with its word, resolved by exactly one command, whose success clears it:
    #   * `venv-unsynced` — the venv sync failed: `lhpc self-update --apply` (an apply finding the
    #     checkout current runs the sync and the unit refresh it skipped);
    #   * `units-stale` — the unit refresh failed: `lhpc self-update --repair-integration` (also
    #     cleared by a later update whose refresh succeeds).
    # A record that cannot be read or names no such state is `recovery-required` (which step
    # failed is unknown: never read as one of the two, never cleared by either command). A record
    # that cannot be WRITTEN leaves no state at all, so that update fails with the cause and the
    # command to run by hand (`_incomplete_outcome`).

    _INCOMPLETE_REMEDY: ClassVar[dict[str, str]] = {
        "venv-unsynced": "lhpc self-update --apply",
        "units-stale": "lhpc self-update --repair-integration"}

    def _pip_sync_cmd(self) -> str:
        import sys as _sys

        from . import selfupdate
        return f"{_sys.executable} -m pip install -e {selfupdate.repo_root() or '<checkout>'}"

    def _incomplete_remedy(self, state: str) -> str:
        if state in self._INCOMPLETE_REMEDY:
            return self._INCOMPLETE_REMEDY[state]
        return (f"by hand — sync the venv ({self._pip_sync_cmd()}), then lhpc self-update "
                f"--repair-integration, then remove {self._incomplete_path()}")

    def _incomplete_path(self):
        from . import updater_units
        return self._paths.under(*updater_units.SELFUPDATE_INCOMPLETE_REL)

    def self_update_incomplete(self) -> tuple[str, str]:
        """`(state, detail)` of the recorded named state, or `("", "")` (GET-safe: one read)."""
        from . import runtime_fs
        try:
            raw = runtime_fs.read_text_regular(self._paths, self._incomplete_path(),
                                               max_bytes=4096)
        except FileNotFoundError:
            return "", ""
        except (OSError, PathContainmentError, ValueError):
            return "recovery-required", "the self-update state record is unreadable"
        try:
            d = json.loads(raw)
            if d.get("state") not in self._INCOMPLETE_REMEDY:
                raise ValueError("no known state")
            return d["state"], str(d.get("detail") or "")
        except (ValueError, AttributeError, TypeError):    # TypeError: an unhashable "state"
            return "recovery-required", ("the self-update state record is invalid — which step "
                                         "of the last update failed is unknown")

    def units_stale(self) -> str:
        state, detail = self.self_update_incomplete()
        return (detail or "units not refreshed") if state == "units-stale" else ""

    def _incomplete_mark(self, state: str, detail: str) -> str:
        """Record `state`: "" when written, else the cause."""
        from . import runtime_fs
        try:
            runtime_fs.write_marker(self._paths, self._incomplete_path(),
                                    json.dumps({"state": state, "detail": detail}))
            return ""
        except (OSError, PathContainmentError, ValueError) as exc:
            return f"{type(exc).__name__}: {' '.join(str(exc).split())}"

    def _incomplete_clear(self, state: str) -> None:
        """Clear the record when it names `state` (a later step's success clears only its own)."""
        from . import runtime_fs
        if self.self_update_incomplete()[0] != state:
            return
        try:
            runtime_fs.unlink(self._paths, self._incomplete_path())
        except (OSError, PathContainmentError, ValueError):
            pass                       # still recorded: `status` keeps saying so, which is safe

    def _units_stale_clear(self) -> None:
        self._incomplete_clear("units-stale")

    def _incomplete_outcome(self, res: ActionResult, state: str,
                            what: str) -> tuple[str, list, dict]:
        """`(summary, next_commands, data)` of an update that ended in a named state (both
        self-update paths; the result is ok=False either way). Recorded: the state, its word and
        its one command. NOT recorded: no state exists for `status` or the command to find, so
        the result is recovery-required — the cause, where the checkout is, and what to run by
        hand: for a failed venv sync the sync AND the unit refresh it skipped, in that order
        (`--apply` resumes them only from the record; without it, it finds the checkout current
        and does nothing)."""
        err = self._incomplete_mark(state, what)
        if not err:
            cmd = self._INCOMPLETE_REMEDY[state]
            return (self._with_cleanup_note(
                        res, f"Update applied — {state}: {what}. Resolve it with: {cmd}."),
                    [cmd], {"state": state})
        cmds = ([self._pip_sync_cmd(), self._INCOMPLETE_REMEDY["units-stale"]]
                if state == "venv-unsynced" else [self._INCOMPLETE_REMEDY[state]])
        how = (f"Resolve it by hand, in this order: {cmds[0]}; then {cmds[1]} (the unit refresh "
               "this update skipped). `lhpc self-update --apply` cannot resume it without the "
               "record" if state == "venv-unsynced" else f"Resolve it with: {cmds[0]}")
        head = res.data.get("new_head_short") or "the new version"
        return (self._with_cleanup_note(
                    res, f"recovery-required: the update was applied, but {what}; the "
                         f"self-update state record could not be written ({err}), so no {state} "
                         f"state exists and `lhpc status` cannot show it. The checkout is at "
                         f"{head}. {how}."),
                cmds, {"state_record_error": err})

    def _units_stale_outcome(self, res: ActionResult, detail: str) -> tuple[str, list, dict]:
        return self._incomplete_outcome(
            res, "units-stale", f"the managed systemd units could NOT be refreshed ({detail}); "
                                "boot restore is skipped until this is repaired")

    def _resumes_sync(self, res: ActionResult) -> bool:
        """An apply that found the checkout current while `venv-unsynced` is recorded runs the
        sync (and the unit refresh) it skipped: the one command that resolves that state."""
        return (bool(res.ok) and bool(res.data.get("already"))
                and self.self_update_incomplete()[0] == "venv-unsynced")

    def _repair_integration_steps(self, *, restart: bool = True) -> ActionResult:
        """OPERATOR / migration: install/restore the COMPLETE canonical unit set (`updater_units.ALL_UNITS`) for this runtime root, then daemon-reload, verify the active fragments, enable both request watchers (`--now`), the web unit and the boot-restore unit. With `restart=True` (CLI default) also restart the console; with
        `restart=False` (the web self-repair bridge) leave the running console alone so the update
        itself bounces it. Refuses while an uninstall guard or request/in-flight evidence exists,
        or when an existing unit is not provably this deployment's."""
        from . import runtime_fs, updater_units
        root = str(self._paths.runtime_root)
        _, checkout, _venv = updater_units.deployment_paths(root)
        import os.path as _op
        if not _op.isdir(_op.join(checkout, ".git")):
            return ActionResult(False, "Not a self-hosted deployment (no checkout at "
                                f"{checkout}) — cannot manage web/updater units.",
                                data={"not_self_hosted": True},
                                details=["  nothing to run here — lhpc runs from a dev checkout "
                                         "or a copy, not the self-hosted layout install.sh makes; "
                                         "the web/updater units are managed only there"])
        if self.uninstall_guard_blocks():
            return ActionResult(False, "An uninstall is in progress (.lhpc-uninstalling present) — "
                                "recover it first (`lhpc self-update --recover-request`).",
                                data={"uninstalling": True},
                                next_commands=["lhpc self-update --recover-request"])
        if self.classify_request() != "absent":
            return ActionResult(False, "An update request is pending/in-flight — run "
                                "`lhpc self-update --recover-request` first.",
                                data={"request_present": True},
                                next_commands=["lhpc self-update --recover-request"])
        ud = self._user_unit_dir()
        # The units log with StandardOutput=append:{root}/logs/... — systemd creates the FILE but not
        # the directory, and a repaired root may predate/have lost it (bootstrap normally makes it).
        # Without this the web unit fails to start on `append:` open.
        try:
            runtime_fs.mkdir(self._paths, "logs")
        except Exception:
            pass
        try:
            actions = updater_units.write_set(ud, root)
        except ValueError as exc:
            return ActionResult(False, str(exc), data={"write_refused": True},
                                details=["  nothing to run here — the box's operator moves the "
                                         "unit file(s) named above out of the user unit folder, "
                                         "then repairs again"])
        S = 20.0
        reload_res = self._system.runner.run(["systemctl", "--user", "daemon-reload"], timeout=S)
        if reload_res.returncode != 0:
            return ActionResult(False, "systemctl --user daemon-reload failed after writing the units "
                                "— not proceeding (the units are on disk but not activated). Check "
                                "`systemctl --user status`.", data={"daemon_reload_failed": True},
                                next_commands=["systemctl --user status",
                                               "lhpc self-update --repair-integration"])
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
                                    "mask shadows it — resolve manually.", data={"shadowed": kind},
                                    next_commands=[f"systemctl --user cat {kind}",
                                                   "lhpc self-update --repair-integration"])
            if props.get("DropInPaths", "").strip():
                return ActionResult(False, f"{kind} has an active drop-in override "
                                    f"({props['DropInPaths'].strip()[:120]}) — it can override the "
                                    "sandbox; remove it, then repair.", data={"dropin": kind},
                                    next_commands=[f"systemctl --user cat {kind}",
                                                   "lhpc self-update --repair-integration"])
        # Enable both, and START the watcher now so a request marker is caught even before the web
        # is (re)started under the new unit. Every step's return code is CHECKED and fails the repair
        # truthfully — a partial integration is never reported as success, and the root marker is
        # written ONLY after all required steps prove they succeeded.
        en = self._system.runner.run(["systemctl", "--user", "enable", "--now",
                                      updater_units.PATH_UNIT], timeout=S)
        if en.returncode != 0:
            return ActionResult(False, "Installed the units but could not enable/start the request "
                                "watcher (lhpc-selfupdate.path) — not proceeding. Check "
                                "`systemctl --user status lhpc-selfupdate.path`.",
                                data={"path_watcher_failed": True},
                                next_commands=["systemctl --user status lhpc-selfupdate.path",
                                               "lhpc self-update --repair-integration"])
        # Same for the nginx-restart watcher (the web console's bind-change escape hatch). NOTE the
        # deliberate startup-recovery semantics: `--now` with a stale request present fires ONE
        # restart immediately — marker consumed, fresh nginx (rate-limited; chosen, not accidental).
        ren = self._system.runner.run(["systemctl", "--user", "enable", "--now",
                                       updater_units.RESTART_PATH_UNIT], timeout=S)
        if ren.returncode != 0:
            return ActionResult(False, "Installed the units but could not enable/start the "
                                "nginx-restart watcher (lhpc-nginx-restart.path) — not proceeding. "
                                "Check `systemctl --user status lhpc-nginx-restart.path`.",
                                data={"restart_watcher_failed": True},
                                next_commands=["systemctl --user status lhpc-nginx-restart.path",
                                               "lhpc self-update --repair-integration"])
        web_en = self._system.runner.run(["systemctl", "--user", "enable", updater_units.WEB_UNIT],
                                         timeout=S)
        if web_en.returncode != 0:
            return ActionResult(False, "Could not enable the web service (lhpc-web.service) — not "
                                "proceeding.", data={"web_enable_failed": True},
                                next_commands=["systemctl --user status lhpc-web.service",
                                               "lhpc self-update --repair-integration"])
        # Boot restore: plain `enable` (NEVER --now — enabling must not trigger a restore run;
        # restoration is additionally gated on [boot] restore + a canonical enabled web unit).
        # Systemd is demonstrably available at this point, so an enable failure is a REPAIR
        # FAILURE — never fail-soft-and-report-success for an autonomous process starter.
        br_en = self._system.runner.run(["systemctl", "--user", "enable",
                                         updater_units.BOOT_RESTORE_UNIT], timeout=S)
        if br_en.returncode != 0:
            return ActionResult(False, "Could not enable the boot-restore unit "
                                "(lhpc-boot-restore.service) — not proceeding.",
                                data={"boot_restore_enable_failed": True},
                                next_commands=["systemctl --user status lhpc-boot-restore.service",
                                               "lhpc self-update --repair-integration"])
        # The watcher MUST be active now — in BOTH modes (a migration's still-running OLD web does not
        # pull it up via Wants=, and a CLI repair must not silently leave it down) — otherwise a queued
        # request is never consumed. Fail BEFORE writing the root marker / restarting.
        act = self._system.runner.run(["systemctl", "--user", "is-active", "--quiet",
                                       updater_units.PATH_UNIT], timeout=S)
        if act.returncode != 0:
            return ActionResult(False, "The update path watcher (lhpc-selfupdate.path) is not active "
                                "after enable --now — not proceeding. Check "
                                "`systemctl --user status lhpc-selfupdate.path`.",
                                data={"path_watcher_failed": True},
                                next_commands=["systemctl --user status lhpc-selfupdate.path",
                                               "lhpc self-update --repair-integration"])
        ract = self._system.runner.run(["systemctl", "--user", "is-active", "--quiet",
                                        updater_units.RESTART_PATH_UNIT], timeout=S)
        if ract.returncode != 0:
            return ActionResult(False, "The nginx-restart watcher (lhpc-nginx-restart.path) is not "
                                "active after enable --now — not proceeding. Check "
                                "`systemctl --user status lhpc-nginx-restart.path`.",
                                data={"restart_watcher_failed": True},
                                next_commands=["systemctl --user status lhpc-nginx-restart.path",
                                               "lhpc self-update --repair-integration"])
        if restart:
            rst = self._system.runner.run(["systemctl", "--user", "restart", updater_units.WEB_UNIT],
                                          timeout=S)
            if rst.returncode != 0:
                # The unit appends the console's output to this file (StandardOutput=append:), so
                # it is where the reason is; the operator's own user journal may not be readable.
                web_log = self._paths.runtime_root.joinpath(*updater_units.WEB_LOG_REL)
                return ActionResult(False, "Installed and enabled the units but the web console "
                                    "restart FAILED — the repair is NOT marked complete. Check "
                                    f"`systemctl --user status {updater_units.WEB_UNIT}` and "
                                    f"`tail -n 50 {web_log}`.",
                                    data={"web_restart_failed": True},
                                    next_commands=[f"systemctl --user status {updater_units.WEB_UNIT}",
                                                   f"tail -n 50 {web_log}"])
        self._write_root_marker()          # ONLY after every required integration step succeeded
        details = [f"  {k}: {a}" for k, a in actions]
        details.append(self._enable_linger(S))
        return ActionResult(True, "Web + one-click updater integration installed/repaired.",
                            details=tuple(details), data={"actions": dict(actions)})

    def _enable_linger(self, timeout: float) -> str:
        """Boot autostart: a `systemctl --user` unit only starts at LOGIN unless the user lingers.
        Installed roots get this from install.sh; a repaired one did not — so repair enables it too.

        ALWAYS attempted (never gated on INVOCATION_ID): the web self-repair bridge runs from a
        managed non-canonical web unit that still has the user bus, and gating would silently deny it boot
        autostart. FAIL-SOFT by contract — a linger failure NEVER fails the repair/update; where the
        bus is unavailable (the canonical web unit blocks %t/bus) we return the shell command."""
        import getpass
        try:
            user = getpass.getuser()
        except Exception:
            return "  linger: could not resolve the user — run: loginctl enable-linger $USER"
        r = self._system.runner.run(["loginctl", "enable-linger", user], timeout=timeout)
        if getattr(r, "not_found", False) or r.returncode != 0:
            return (f"  linger: NOT enabled (no user bus here) — for autostart at boot run: "
                    f"loginctl enable-linger {user}")
        return f"  linger: enabled for {user} — the console now autostarts at boot"

    @invalidates_snapshot
    def self_update_repair_and_trigger(self, *, overwrite: bool = False,
                                       queue: bool = True) -> ActionResult:
        """WEB one-click that also MIGRATES a non-canonical same-root deployment (old/`%h` units, no
        `.path`) to the canonical set, then updates — in one click. Compatibility bridge ONLY: it
        needs the user bus, which succeeds only while the console runs the not-yet-hardened unit;
        once the canonical bus-blocked web unit is active the bus preflight fails and this returns
        shell guidance WITHOUT writing anything. Auto-repair is allowed ONLY for
        `missing`/`modified_ours` units — never `ambiguous`/`foreign`/`overridden`/`unsafe`/
        `unreadable`/recovery states."""
        from . import updater_units
        # Managed-service gate FIRST — the web->systemctl bridge must run only for the non-canonical
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
            return self.self_update_trigger(overwrite=overwrite, queue=queue)   # nothing to migrate
        # Refuse recovery / pending-request / uninstall BEFORE any preflight or write.
        if status == "recovery_required":
            return ActionResult(False, "A previous update needs recovery first — run "
                                "`lhpc self-update --recover-request`.", data={"recovery_required": True},
                                next_commands=["lhpc self-update --recover-request"])
        if self.uninstall_guard_blocks():
            return ActionResult(False, "An uninstall is in progress — recover it first.",
                                data={"uninstalling": True},
                                next_commands=["lhpc self-update --recover-request"])
        if self.classify_request() != "absent":
            return ActionResult(False, "An update request is already pending — the console is about "
                                "to update.", data={"request_present": True},
                                details=["  nothing to run here — the queued update starts by "
                                         "itself; wait for the console to come back"])
        # Fixable ONLY when every non-OK unit is missing/modified_ours (an ambiguous/foreign/
        # overridden/unsafe/unreadable unit is NOT auto-repairable).
        fixable_set = (updater_units.OK, updater_units.MISSING, updater_units.MODIFIED_OURS)
        per = integ.get("per_unit", {})
        bad = {k: v for k, v in per.items() if v not in fixable_set}
        if bad:
            detail = ", ".join(f"{k}: {v}" for k, v in bad.items())
            return ActionResult(False, "The web/updater units are not safely this deployment's "
                                f"({detail}) — resolve them manually, then update.",
                                data={"integration": status, "unfixable": bad},
                                details=["  nothing to run here — the units named above were "
                                         "changed or replaced outside lhpc; the box's operator "
                                         "restores or removes them from a shell, then runs the "
                                         "update again"])
        # Bus preflight — cheap, read-only. A hardened (bus-blocked) console fails here BEFORE any
        # write and gets shell guidance.
        probe = self._system.runner.run(["systemctl", "--user", "show", "-p", "Version"], timeout=20.0)
        if probe.returncode != 0:
            return ActionResult(False, "This console can't install systemd units itself (the user "
                                "bus is unavailable). From a shell on this machine run "
                                "`lhpc self-update --repair-integration`, then click Update.",
                                data={"bus_unavailable": True},
                                next_commands=["lhpc self-update --repair-integration"])
        rep = self.self_update_repair_integration(restart=False)
        if not rep.ok:
            return rep
        if self.updater_integration()["status"] != "ok":                # repair must have converged
            return ActionResult(False, "Unit repair did not fully converge — run "
                                "`lhpc self-update --repair-integration` from a shell.",
                                data={"repair_incomplete": True},
                                next_commands=["lhpc self-update --repair-integration"])
        return self.self_update_trigger(overwrite=overwrite, queue=queue)

    def _write_root_marker(self) -> None:
        import time as _time

        from . import runtime_fs, updater_units
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
    def _promote(prepared) -> dict:
        """Promote a prepared transition whose git DID complete (head reached its to_head) into the
        completed slot, carrying its durable-anchor `txid` and its (immutable, anchor-matched) pending
        payload. The classifier's invariant guarantees no prior completed pending coexists."""
        return {"from_head": prepared["from_head"], "to_head": prepared["to_head"],
                "branch": prepared["branch"], "pending": prepared["pending"],
                "txid": prepared.get("txid")}
