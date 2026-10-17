"""Shared application/service layer — the single entry point for all behaviour.

The CLI adapter and the web adapter both call ONLY this module, guaranteeing
identical validation, status interpretation and results. Read methods are bounded
and read-only; mutating methods return a dry-run plan and apply only with `apply=True`.

`build_snapshot()` is the single probing path; both `status()` (CLI text) and the
web adapter call it, so a page load and a CLI run see the same fresh evidence.
"""

from __future__ import annotations

import contextlib
import os
import secrets
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import ClassVar

from . import binary_install as binary_install_mod
from . import binary_receipt as binary_receipt_mod
from . import manifest as manifest_mod
from . import meshcore_mode as _meshcore_mode
from . import spi_mode
from .config import (
    HW_SETUPS,
    Config,
    ConfigError,
    _stack_config_path,
    conditional_clear_stack_config,
    load_config,
    load_stack_config,
)
from .daemon_control import ALLOWED_BANDS
from .install import Installer, Plan
from .lifecycle import GUI_MISSING_HINT, Lifecycle
from .model import (
    ComponentKind,
    RunState,
    Stack,
)
from .paths import PathContainmentError, Paths, resolve_paths
from .probes import RealSystem, System, hardware
from .snapshot_memo import invalidates_snapshot
from .status import Snapshot, StatusProber, rollup_states, summarize

_SPI_DEV = "/dev/spidev0.0"
_GPIO_DEV = "/dev/gpiochip0"
_UNSET = object()                # sentinel: "not yet resolved" (distinct from None)


from .service_base import (
    ActionResult,
    AdmissionRefused,
    ConfigWrite,
    SourceTxnBlocked,
    _canon_git_url,
    _proc_ceased,
    _proc_start_time,
    _StopRun,
    _SwitchReplace,
    admission_refusal,
)

# Public import surface (the adapters + tests import these names FROM lhpc.core.services). Listing
# them in __all__ also marks the re-exports above as intentionally exported, so a name whose only
# in-module users have moved to a service_* mixin is not reported as an unused import.
__all__ = [
    "ActionResult",
    "ConfigWrite",
    "ControllerService",
    "SourceTxnBlocked",
    "_StopRun",
    "_canon_git_url",
    "_proc_ceased",
    "_proc_start_time",
]

# A SHARED config-stability acquire (start/restart) waits at most this long before a typed refusal: long
# enough to sail past an ordinary EXCLUSIVE config SAVE (milliseconds), short enough that it does NOT hang
# for the whole auto-install run when the auto-install boundary holds config EXCLUSIVE — it refuses instead.
_CONFIG_STABLE_SHARED_TIMEOUT_S = 3.0


def _qemu_boots_extradio(toks) -> bool:
    """Does this QEMU argv boot an EXTERNAL-RADIO MeshCom image?

    Reads the actual `-drive` specification — `-drive file=<path>,if=mtd` (and the `-drive=`
    spelling) — and requires the image to be a PlatformIO `extradio` build:
    `<...>/.pio/build/qemu-headless-extradio*/flash.bin`. The word appearing SOMEWHERE in the
    argv proves nothing: a `--env` label, a log path or an unrelated argument would match, while
    the guest still boots a plain image with no transmitter behind it."""
    import posixpath
    specs = []
    for i, t in enumerate(toks):
        if t == "-drive" and i + 1 < len(toks):
            specs.append(toks[i + 1])
        elif t.startswith("-drive="):
            specs.append(t.split("=", 1)[1])
    for spec in specs:
        for part in str(spec).split(","):
            if not part.startswith("file="):
                continue
            path = part.split("=", 1)[1]
            head, leaf = posixpath.split(path)
            head, env = posixpath.split(head)
            head, build = posixpath.split(head)
            if (leaf == "flash.bin" and build == "build"
                    and posixpath.basename(head) == ".pio"
                    and env.startswith("qemu-headless-extradio")):
                return True
    return False


from . import web_client
from .service_admission import AdmissionOpsMixin
from .service_auto_install import AutoInstallOpsMixin
from .service_binary_channel import BinaryChannelMixin
from .service_binary_ops import BinaryOpsMixin
from .service_boot_restore import BootRestoreOpsMixin
from .service_daemon_params import DaemonParamsMixin
from .service_firewall import FirewallOpsMixin
from .service_gps import GpsOpsMixin
from .service_hmac import HmacOpsMixin
from .service_jobs import JobsOpsMixin
from .service_lifecycle_ops import LifecycleOpsMixin
from .service_maintenance import MaintenanceOpsMixin
from .service_network import NetworkOpsMixin
from .service_params import ParamsConfigMixin
from .service_rflog import RflogOpsMixin
from .service_secrets import SecretsOpsMixin
from .service_selfupdate import SelfUpdateOpsMixin
from .service_stop import StopOpsMixin
from .service_system import SystemStatsMixin
from .service_webserver import WebserverOpsMixin


def _load_system_provider(paths):
    """Resolve $LHPC_SYSTEM_PROVIDER ("module:factory") and call factory(paths). Returns
    the provider (with .system / .manifest_path / .wrap_spawn), or None.

    FAIL CLOSED when a provider is REQUESTED but cannot be delivered. Production (env
    UNSET) returns None and yields RealSystem, byte-for-byte — a provider can never break
    a process that did not ask for one. But once the env NAMES a provider, an unusable spec
    or a factory that raises PROPAGATES: never silently fall back to the real command runner
    while a simulation harness believes it is sandboxed (a test lab must fail closed, not
    run real shutdown/nft/apt under a "SIMULATED HARDWARE" banner). A factory that returns
    None is an explicit "not active here" (e.g. the lab latch is not engaged) → RealSystem."""
    import importlib
    import os as _os
    spec = _os.environ.get("LHPC_SYSTEM_PROVIDER")
    if not spec:
        return None                                  # production: nothing requested
    if ":" not in spec:
        raise RuntimeError(f"LHPC_SYSTEM_PROVIDER {spec!r} is malformed (want 'module:factory')")
    mod_name, _, attr = spec.partition(":")
    try:
        factory = getattr(importlib.import_module(mod_name), attr)
    except Exception as exc:
        raise RuntimeError(
            f"LHPC_SYSTEM_PROVIDER {spec!r} could not be loaded ({exc}) — refusing to run "
            "against real hardware under a requested simulation provider") from exc
    return factory(paths)                            # None => not active; raising => propagates


class ControllerService(WebserverOpsMixin, AutoInstallOpsMixin, SelfUpdateOpsMixin, MaintenanceOpsMixin, ParamsConfigMixin, LifecycleOpsMixin, HmacOpsMixin, SystemStatsMixin, FirewallOpsMixin, BootRestoreOpsMixin,
                        BinaryChannelMixin, BinaryOpsMixin, NetworkOpsMixin, SecretsOpsMixin, RflogOpsMixin,
                        DaemonParamsMixin, GpsOpsMixin, AdmissionOpsMixin, JobsOpsMixin, StopOpsMixin):
    """Facade over the core: composes the service_* mixins into ONE object. Construct once per
    process and share it (the web app does, across threads) — it carries the manifest and
    config caches, the config lock, and per-thread re-entrancy bookkeeping for the on-disk
    admission and resource locks. Authoritative state lives on disk under the runtime root,
    never here.

    `system` and `paths` are injectable so tests drive it with fakes.
    """

    def __init__(
        self,
        manifest_path: Path | None = None,
        system: System | None = None,
        paths: Paths | None = None,
    ) -> None:
        self._paths = paths or resolve_paths()
        # Generic extension point: when the caller injected NOTHING, an out-of-tree
        # provider named by $LHPC_SYSTEM_PROVIDER ("module:factory") may supply the
        # System, an alternate manifest, and a spawn wrapper — used by simulation/test
        # harnesses that are not part of this package. Unset (production, always) costs
        # one getenv and yields RealSystem, byte-for-byte. Any explicit injection
        # (system= or manifest_path=, every existing test) bypasses the probe entirely.
        _ext = None
        if system is None and manifest_path is None:
            _ext = _load_system_provider(self._paths)
        self._manifest_path = manifest_path or (_ext.manifest_path if _ext else None)
        self._system = system or (_ext.system if _ext else RealSystem())
        self._ext = _ext
        self._stacks: tuple[Stack, ...] | None = None
        # Derived-from-manifest GPS sets, same lifetime as _stacks (which is never invalidated).
        self._gps_stacks_cache: frozenset | None = None
        self._gps_consumers_cache: frozenset | None = None
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
        # The snapshot memo is REQUEST/THREAD-local: one shared ControllerService is hit by concurrent
        # Waitress worker threads, so a process-wide cache would let one thread serve another thread's
        # pre-mutation snapshot (or have its invalidation clobbered). Each thread memoizes/invalidates
        # its own snapshot; `fresh=True` still bypasses.
        self._snapshot_state = threading.local()

    @contextmanager
    def _config_stable(self, exclusive: bool = False):
        """Hold saved configuration STABLE on the runtime config lock file. Two modes:
          * SHARED (default) — a read lock for the duration of an applied lifecycle transition. Config
            MUTATIONS take the EXCLUSIVE `config_lock`, so a concurrent save WAITS for the transition and a
            start WAITS for an in-progress save; independent starts share and never serialise.
          * EXCLUSIVE — the auto-install boundary holds `LOCK_EX` for the WHOLE run, so an atomic config
            write inside the boundary reuses this held lock (see `save_config_bundle`) instead of contending
            on a second descriptor. Acquired BOUNDED (an auto-install run must not hang on a stuck holder) → typed
            `SourceTxnBlocked` on timeout.
        RE-ENTRANT per thread. The OUTERMOST entry FIXES the mode and holds it UNCHANGED — nested entries are
        depth-only and NEVER convert it (SH↔EX conversion is not atomic on Linux). Nested exclusive-under-
        exclusive and shared-under-exclusive are allowed; a nested EXCLUSIVE beneath a SHARED guard is
        REJECTED (a config mutation must never run under a shared stability guard). LOCK ORDER: acquired
        BEFORE any lifecycle/resource lock; a failure raises here so the caller fails typed with no side
        effect. Thread-local mode/fd state is cleared on the outermost exit, including exceptional exits."""
        import fcntl

        from . import runtime_fs
        st = self._cfg_stable_state
        depth = getattr(st, "depth", 0)
        if depth == 0:
            fh = runtime_fs.open_lock(self._paths, self._paths.under("config", ".lock"))
            try:
                # BOTH modes acquire BOUNDED (LOCK_NB + poll) → typed busy, so a caller NEVER hangs
                # indefinitely: a SHARED reader (start/restart) that meets a long-running EXCLUSIVE holder
                # (the auto-install boundary) is REFUSED after a short wait rather than blocking for the
                # whole run; the EXCLUSIVE acquirer (the boundary) waits the full config timeout for
                # in-flight SHARED transitions to finish. Uncontended acquisition succeeds immediately.
                from .config import _CONFIG_LOCK_POLL_S, CONFIG_LOCK_TIMEOUT_S
                lock_op = (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH) | fcntl.LOCK_NB
                timeout = CONFIG_LOCK_TIMEOUT_S if exclusive else _CONFIG_STABLE_SHARED_TIMEOUT_S
                deadline = time.monotonic() + timeout
                while True:
                    try:
                        fcntl.flock(fh, lock_op)
                        break
                    except OSError:
                        if time.monotonic() >= deadline:
                            raise SourceTxnBlocked(
                                "config-stability lock is busy — a long-running config operation "
                                "holds it; try again shortly") from None
                        time.sleep(_CONFIG_LOCK_POLL_S)
            except BaseException:
                fh.close()
                raise
            st.fh = fh
            st.exclusive = exclusive
        elif exclusive and not getattr(st, "exclusive", False):
            # Never convert a held SHARED guard to EXCLUSIVE; a config mutation must not run beneath it.
            raise RuntimeError("config-stability: EXCLUSIVE requested beneath a SHARED guard")
        st.depth = depth + 1
        try:
            yield
        finally:
            st.depth -= 1
            if st.depth == 0:
                try:
                    try:
                        fcntl.flock(st.fh, fcntl.LOCK_UN)
                    finally:
                        st.fh.close()
                finally:
                    st.fh = None
                    st.exclusive = False

    @contextmanager
    def _config_unstable(self):
        """RELEASE a held SHARED config-stability guard across one long, config-INDEPENDENT step,
        then take it back. The ONE user is the synchronous REQUIRED post-start, which can
        legitimately retry for many minutes while a config save gives up after
        CONFIG_LOCK_TIMEOUT_S: everything the step consumes (`comp_cfg`, the rendered launcher, the
        GPS plan) is materialized BEFORE the release, so it runs on a frozen configuration and
        cannot observe a mid-flight write.

        NO-OP unless this thread holds the guard SHARED: with no guard there is nothing to release,
        and the EXCLUSIVE auto-install boundary owes its callers whole-run exclusivity.

        Re-acquisition BLOCKS, so this NEVER returns while `_cfg_stable_state` claims a guard the
        process does not hold — a caller that believes it is inside `_config_stable` always is. It
        cannot wedge: the only EXCLUSIVE holders are bounded config writes (CONFIG_LOCK_TIMEOUT_S),
        and the one long-lived holder, the auto-install boundary, can never overlap — it takes the
        same task-admission flock this operation holds, and takes it BEFORE config-exclusive, so no
        holder of this lock ever waits on us."""
        import fcntl

        st = self._cfg_stable_state
        if getattr(st, "depth", 0) == 0 or getattr(st, "exclusive", False):
            yield
            return
        fh = st.fh
        fcntl.flock(fh, fcntl.LOCK_UN)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_SH)

    def _holds_config_exclusive(self) -> bool:
        """True iff THIS thread currently holds the config-stability guard in EXCLUSIVE mode — the only
        state in which a config write may reuse the held lock (see `save_config_bundle`)."""
        st = self._cfg_stable_state
        return getattr(st, "depth", 0) > 0 and getattr(st, "exclusive", False)

    # ---- config / installer ---------------------------------------------

    def config(self) -> Config:
        with self._config_lock:
            # Reload when local.toml's mtime changed since the cache was built.
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

    def _invalidate_config(self) -> None:
        """Drop the cached Config so the NEXT read (any thread) reloads from disk. Called
        after every successful config mutation so a saved callsign/remote/param is
        immediately visible to subsequent web AND CLI service actions (no stale cache). The
        writing thread's per-request evidence memo (stack configs) is dropped with it."""
        with self._config_lock:
            self._config = None
        self._snapshot_state.memo = None

    def _installer(self) -> Installer:
        return Installer(self._paths, self.stacks(), self.config(), self._system)

    # ---- auto-install run: status, gates, log, ack, spawn, driver (M2.1) -----------

    AUTO_INSTALL_OP = "auto-install"

    # Keep in sync with COMPLOG_MAX in static/auto_install.js (the live window's scrollback cap, 1.5 MB):
    # a historical seed must not be larger than what the live view would keep.
    _COMPLOG_SEED_MAX_BYTES = 1_500_000
    _COMPLOG_SEED_MAX_READS = 512            # hard iteration bound (normal runs drain in <10 reads)

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
        from . import selfupdate
        return selfupdate.venv_sync_command(str(python_bin), str(checkout))

    def _controller_refusal(self, target) -> ActionResult | None:
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
            return verdict("unsafe", "checkout has no origin remote" if canon_spec and not origin
                           else "origin is not the approved canonical remote")
        return verdict("ok", "identity ok")

    @property
    def runtime_root(self):
        """Absolute runtime installation root (display/resolution use)."""
        return self._paths.runtime_root

    # ---- the single probing path (used by CLI and web) -------------------

    def build_snapshot(self, *, fresh: bool = False, sources: bool = True) -> Snapshot:
        """Bounded, read-only assessment of every stack. Memoized WITHIN a single request/operation
        only: a page render calls this ~15× (one per stack helper) — recomputing all of it each time
        re-scans /proc and re-runs git for every source (seconds). The cache is dropped at the START of
        every web request (`invalidate_snapshot` from a before_request hook) AND by every PUBLIC mutating
        service entry (`@invalidates_snapshot`, snapshot_memo.py — on entry AND exit, incl. refusal
        paths), so it is NEVER reused across a mutation or an HTTP request: a nested public `stop()`
        inside `start()` re-invalidates on exit, so the outer op's post-mutation read recomputes.

        `fresh=True` FORCES a recompute (and refreshes the cache) for the authoritative rechecks UNDER
        the operation locks (a concurrent op could have changed state since this op's cached read).
        `sources=False` (the polls, which read run states only) runs no git: this request's snapshot if
        cached, else one that is never cached, its sources only missing/not-a-repo/binary/unknown.

        Confirmed-working = a component's clean source HEAD appears in an OPERATOR-CONFIRMED stored
        known-working composition of its stack (file reads only)."""
        cached = getattr(self._snapshot_state, "cache", None)
        if cached is not None and not fresh:
            return cached
        from . import known_working, source_cache
        confirmed: dict = {}
        for s in self.stacks():
            comps = known_working.load(self._paths, s.id)
            for comp in comps:
                for cid, entry in comp["entries"].items():
                    if entry.get("commit"):
                        confirmed.setdefault(cid, set()).add(entry["commit"])
        # Components currently provided by a verified binary artifact — the ONE receipt read
        # per snapshot (cheap four-state, no hashing), passed to the prober so a binary stack
        # reports artifact provenance instead of "no git checkout".
        binary_cover: dict = {}
        for s in self.stacks():
            spec = getattr(s, "binary", None)
            if spec is None:
                continue
            state, rec, _why = self.binary_receipt_state(s.id)
            if state == "valid" and rec is not None:
                for cid in spec.covers:
                    binary_cover[cid] = rec
        snap = StatusProber(self._system, self._paths, confirmed,
                            binary_cover=binary_cover, sources=sources,
                            cache=None if fresh else source_cache.SHARED,
                            meshcore_mode=self.meshcore_running_mode()).assess_stacks(self.stacks())
        self._overlay_runtime_bands(snap)
        self._overlay_gui_unavailable(snap)
        self._overlay_licensed_tx_enabled(snap)
        if sources:
            self._snapshot_state.cache = snap
        return snap

    def _overlay_licensed_tx_enabled(self, snap) -> None:
        """Report TX as ENABLED for a licensed stack whose RF chain is PROVEN LIVE end to end.

        `TxState.ENABLED` has no other producer — the prober (`status.py`) emits only
        DISABLED/UNKNOWN — so a stack demonstrably on the air still read "tx unknown".

        POSITIVE proof, never "no negative marker was found". Starting from proven and merely
        rejecting known-bad markers greens every unrecognised argv; the three conditions below
        must ALL hold:

          1. every REQUIRED tx-capable component of the stack (`tx_capable`, has a run command,
             not `optional`) is RUNNING/DEGRADED with READABLE argv. Half a chain proves
             nothing: a MeshCom bridge without the emulated node transmits into nowhere, and an
             emulated node without the bridge has no path to the radio;
          2. no live argv selects a no-RF mode — `--backend` anything but `loraham` (or absent,
             which is the bridge's fake default), `--rx-only`, or a QEMU guest whose `-drive`
             does not boot an `extradio` flash image;
          3. a component that `depends_on` the LoRaHAM daemon has that daemon LIVE on its band.
             The daemon owns the radio; without it the client has no transmitter at all.

        A live-capability inference, NOT proof of a carrier: it never re-validates the callsign
        and must not be described as proving a transmission. Only licensed stacks, only
        UNKNOWN -> ENABLED. DISABLED is authoritative and is NEVER upgraded. Fail-soft like the
        sibling overlays — status must not break because this inference failed."""
        from .model import RunState, TxState
        up = (RunState.RUNNING, RunState.DEGRADED)
        # /proc scan is LAZY: cmdlines() lists /proc and reads a file per PID, and this overlay
        # runs on every build_snapshot (CLI `lhpc status` too). Most boxes have no licensed stack
        # running, and those must not pay for it.
        argv_cache: dict = {}

        def _argvs():
            if not argv_cache:
                try:
                    argv_cache.update(self._system.procfs.cmdlines() or {})
                except Exception:
                    pass
                argv_cache.setdefault(-1, [])           # mark as attempted
            return argv_cache

        daemon_bands: set | None = None
        # Component status by id across the WHOLE snapshot (see the cross-stack chain below).
        comp_status = {cid: st for s in snap.stacks for cid, st in s.components.items()}
        stack_of = {c.id: s.id for s in self.stacks() for c in s.components}
        for ss in snap.stacks:
            try:
                idf = self._identity_field(ss.stack.id)
            except Exception:
                continue          # identity resolution unavailable -> leave the probed truth
            if not idf or idf.get("enforce") != "licensed":
                continue
            # The chain the stack DECLARES. `optional` helpers (KISS's serial PTY) and build-only
            # artefacts (meshcom-firmware, kind="firmware", no run command) are not part of the
            # live RF path, so they must not gate it — and must not stand in for it either.
            #
            # The closure spans STACKS. A stack whose transmitter is another stack's component
            # (graywolf reaches RF only through loraham-kiss-tnc) would otherwise be judged on
            # its own process alone: every condition below would pass while nothing capable of
            # transmitting was running, and a licence-relevant "TX enabled" would be asserted
            # for a station with no transmitter. Pulling the declared dependencies in keeps the
            # inference POSITIVE and can only ever make it stricter.
            required = [c for c in self._tx_chain_components(ss.stack)
                        if c.tx_capable and c.run_argv and not c.optional]
            proven = bool(required)
            for c in required:
                # A chain member may live in ANOTHER stack's section, so resolve the status
                # snapshot-wide; `ss.components` alone would report every cross-stack member as
                # absent and no such stack could ever be proven.
                st = ss.components.get(c.id) or comp_status.get(c.id)
                if st is None or st.run_state not in up:
                    proven = False                      # chain incomplete
                    break
                toks = [t for t in (_argvs().get(pid) for pid in (st.pids or ())) if t]
                if not toks:
                    proven = False                      # no readable argv: we know nothing
                    break
                if any(self._argv_rf_verdict(t) == "no-rf" for t in toks):
                    proven = False
                    break
                if self.DAEMON_ID in (c.depends_on or ()):
                    if daemon_bands is None:
                        daemon_bands = self._live_daemon_bands(snap, _argvs())
                    try:
                        band = self._effective_band(stack_of.get(c.id, ss.stack.id), c.band)
                    except Exception:
                        band = c.band
                    if not daemon_bands or (band and band not in daemon_bands):
                        proven = False                  # no radio behind this client
                        break
            if not proven:
                continue
            for c in ss.stack.components:
                st = ss.components.get(c.id)
                if (c.tx_capable and st is not None and st.run_state in up
                        and st.tx_state is TxState.UNKNOWN):
                    st.tx_state = TxState.ENABLED

    def _tx_chain_components(self, stack) -> list:
        """`stack`'s own components plus every component reachable through `depends_on`,
        across stacks, de-duplicated and order-stable.

        Used only by the licensed-TX inference: the RF path of a stack may leave it (the
        graywolf stack transmits through the kiss stack's TNC), and a chain that is judged
        on half its members proves nothing. Resolution is best-effort — an unresolvable id
        is skipped rather than raised, because this is an inference, not a gate."""
        by_id = {c.id: c for s in self.stacks() for c in s.components}
        seen: set = set()
        out: list = []
        pending = [c.id for c in stack.components]
        while pending:
            cid = pending.pop(0)
            if cid in seen:
                continue
            seen.add(cid)
            comp = by_id.get(cid)
            if comp is None:
                continue
            out.append(comp)
            pending.extend(comp.depends_on or ())
        return out

    def _live_daemon_bands(self, snap, argvs) -> set:
        """Radio bands served by a LIVE LoRaHAM daemon, from this snapshot plus the daemon's own
        argv. Empty = no daemon is up, so no daemon-backed client can be transmitting.

        Same `--radio` topology reading as `_daemon_claimed_bands`, but scoped to the daemon
        component the snapshot already reports as running: a daemon that IS up whose band cannot
        be read from argv serves whatever it was started for, so both bands count (it is running
        — inventing a band mismatch would deny a chain that is demonstrably complete)."""
        from .model import RunState
        bands: set = set()
        for ss in snap.stacks:
            st = ss.components.get(self.DAEMON_ID)
            if st is None or st.run_state not in (RunState.RUNNING, RunState.DEGRADED):
                continue
            seen: set = set()
            for pid in (st.pids or ()):
                toks = list(argvs.get(pid) or ())
                for i, t in enumerate(toks):
                    if t == "--radio" and i + 1 < len(toks):
                        seen.add(toks[i + 1])
                    elif t.startswith("--radio="):
                        seen.add(t.split("=", 1)[1])
            bands |= (seen & set(ALLOWED_BANDS)) or set(ALLOWED_BANDS)
        return bands

    @staticmethod
    def _argv_rf_verdict(argv) -> str:
        """`"rf"` | `"no-rf"` | `"unknown"` for ONE live argv.

        Token-wise, never a substring match on a joined command line — a path or another
        process's arguments would match by accident. `"unknown"` is the honest answer for a
        process this has no rule for; the caller treats it as "no opinion", not as proof.

        Explicit no-RF modes:
          * `--backend <x>` / `--backend=<x>` where x is not `loraham` (MeshCom's `fake`);
          * a MeshCom bridge with NO `--backend` at all — upstream defaults to `fake`, so
            silence is not consent;
          * `--rx-only` (KISS declares it; harmless here until KISS is licensed);
          * a QEMU guest whose `-drive` does not select an `extradio` firmware image — without
            the external radio there is no transmitter behind the emulated node.
        """
        toks = list(argv or ())
        if not toks:
            return "unknown"
        backend_ok = False
        for i, t in enumerate(toks):
            if t == "--rx-only":
                return "no-rf"
            if t == "--backend" or t.startswith("--backend="):
                val = t.split("=", 1)[1] if "=" in t else (toks[i + 1] if i + 1 < len(toks) else "")
                if val != "loraham":
                    return "no-rf"
                backend_ok = True
        is_bridge = any("meshcom-loraham-bridge" in t for t in toks)
        if is_bridge:
            return "rf" if backend_ok else "no-rf"
        if any(t.endswith("qemu-system-xtensa") or "/qemu-system-xtensa" in t for t in toks):
            # The image the guest BOOTS decides, unconditionally — a stray `--backend loraham`
            # on the emulator command line must not stand in for the external-radio firmware.
            return "rf" if _qemu_boots_extradio(toks) else "no-rf"
        return "rf" if backend_ok else "unknown"

    def _overlay_gui_unavailable(self, snap) -> None:
        """A component that CANNOT work here because a GUI-only dependency is absent (headless box,
        bootstrap without `--with-gui`) reads NOT_APPLICABLE, not "not-installed" — "not-installed"
        invites an install that would refuse, and one skipped-by-design GUI helper must not roll an
        otherwise-healthy stack's badge to not-installed. Uses the SAME predicate as the planning
        skip (`gui_unavailable_components`), probed only for stacks that declare a gui requirement,
        so the state self-corrects once the GUI dependencies appear."""
        from .model import RunState
        for ss in snap.stacks:
            if not any(getattr(r, "gui", False)
                       for c in ss.stack.components for r in c.requires):
                continue
            try:
                skip = set(self.gui_unavailable_components(ss.stack))
            except Exception:
                continue      # probe layer is unavailable, show the un-overlaid truth instead
                              # of failing the snapshot (and every caller composing it).
            for c in ss.stack.components:
                st = ss.components.get(c.id)
                if c.id not in skip or not st:
                    continue
                # NOT_INSTALLED: the classic headless case (no checkout at all). STOPPED:
                # the checkout exists but the GUI toolkit doesn't — voice's GTK app on a
                # Lite box, whose SHARED source the terminal variant installed. Showing it
                # as a startable stopped app invites a Start that can only answer SKIPPED.
                if st.run_state in (RunState.NOT_INSTALLED, RunState.STOPPED):
                    st.run_state = RunState.NOT_APPLICABLE

    def _overlay_runtime_bands(self, snap) -> None:
        """Stamp the ACTUAL runtime band onto RUNNING components (dual-radio truth). The prober only
        knows the manifest default (`comp.band`), but a daemon-client stack can be started on 433 OR
        868 — the stack's running-band marker records which. Overlay it onto every RUNNING/DEGRADED
        single-band component of that stack, then rewrite each DependencyObservation's band from the
        referenced component's overlaid value, so "depends on X: running on N MHz" states the band X
        is ACTUALLY running on. STOPPED components keep the manifest label (the overlay is gated on
        run_state, so a stale marker never leaks)."""
        from .model import RunState
        live = (RunState.RUNNING, RunState.DEGRADED)
        for ss in snap.stacks:
            marker = self.running_band(ss.stack.id)
            if not marker:
                continue
            for comp in ss.stack.components:
                st = ss.components.get(comp.id)
                if st is not None and comp.band and st.run_state in live:
                    st.band = marker
        band_of = {cid: st.band for ss in snap.stacks for cid, st in ss.components.items() if st.band}
        for ss in snap.stacks:
            for st in ss.components.values():
                for dep in st.dependencies:
                    if band_of.get(dep.component_id):
                        dep.band = band_of[dep.component_id]

    def invalidate_snapshot(self) -> None:
        """Drop the memoized snapshot so the next `build_snapshot()` recomputes from scratch. Called once
        per HTTP request (web before_request) and on entry+exit of every PUBLIC mutating service entry
        (`@invalidates_snapshot`, snapshot_memo.py), so a snapshot is never served after state could
        change. Under-lock authoritative rechecks additionally use `build_snapshot(fresh=True)` to bypass
        a snapshot this same op cached before it acquired its lock. Thread-local (see `__init__`).
        The per-request evidence memo (`_request_memo`) shares this lifetime and is dropped with it."""
        self._snapshot_state.cache = None
        self._snapshot_state.memo = None

    def _request_memo(self, key, compute):
        """ONCE PER REQUEST: memoize a read-only piece of evidence for the
        rest of the current request/operation, in the SAME thread-local state as the snapshot —
        never an ordinary service attribute, because Waitress worker threads share this service.
        Cleared wherever the snapshot memo is cleared (every web request start, every public
        mutating entry) and by `_invalidate_config` (a config write in this thread). Only a
        successful `compute()` is memoized; an exception propagates and is retried next time."""
        st = self._snapshot_state
        memo = getattr(st, "memo", None)
        if memo is None:
            memo = st.memo = {}
        try:
            return memo[key]
        except KeyError:
            val = memo[key] = compute()
            return val

    def _stack_config_cached(self, stack_id: str, band: str = "") -> dict:
        """`load_stack_config` once per (stack, band) per request — the parameter views resolve
        every row through it (hundreds of reads of the same few files per render). The memo key
        carries the file's stat signature (inode, size, mtime), so a write by ANY path — a service
        save, a CLI in another process, a hand edit — is seen on the next read at the cost of one
        `stat` instead of a parse; the safe no-follow loader still reads the bytes. READ-ONLY: the
        returned dict is shared for the request; callers must not mutate it. A malformed file
        still raises `ConfigError` on every read (never memoized)."""
        try:
            st = os.stat(_stack_config_path(self._paths, stack_id, band))
            sig = (st.st_ino, st.st_size, st.st_mtime_ns)
        except OSError:
            sig = None
        return self._request_memo(("stack-config", stack_id, band, sig),
                                  lambda: load_stack_config(self._paths, stack_id, band))

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
            if ss.stack.id == _meshcore_mode.STACK_ID:
                # The ONE mode decision, as saved — and, while the stack runs with another one,
                # the mode the running launch was generated with (a change is restart-required).
                saved, live = self.meshcore_mode_display(), self.meshcore_running_mode()
                line = f"  mode: {saved or '(unreadable stack config)'}"
                if saved and live != saved and rollup[ss.stack.id] in ("running", "degraded"):
                    line += f"  (running: {live} — restart to apply)"
                details.append(line)
            _ov = self.binary_active_override(ss.stack.id)       # A3: {} unless in force
            for comp in ss.stack.components:
                st = ss.components[comp.id]
                details.extend(_render_component(comp, st))
                if comp.id == web_client.COMPONENT and (_wl := web_client.status(self)):
                    details.append(f"    {_wl}")
                if comp.id in _ov:
                    _b, _p = _ov[comp.id]
                    details.append(f"    installed over the pin check: built from {_b[:9]}, "
                                   f"pins {_p[:9]} (it may not work with this LHPC; "
                                   "lhpc self-update --apply)")
                # Terminal post-start outcome (e.g. the MeshCom callsign push: confirmed /
                # NOT applied + the re-apply command). Fail-soft: status must render even
                # when a result sidecar is unreadable. Shared loop → the line appears in
                # BOTH `lhpc status` and the scoped `lhpc status <stack>`.
                try:
                    details.extend(f"        {line}"
                                   for line in self._post_start_outcomes(comp.id))
                except Exception:
                    pass
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
        # A source update that is not finished (named states: prior-in-use, prior-dirty,
        # update-interrupted) — every source operation waits on it, so it is said here.
        try:
            pending = self._installer().pending_states()
        except Exception as exc:                # never let a diagnostic break status, nor hide one
            pending = [("state/source-txn", "recovery-required",
                        f"the source journals could not be read ({type(exc).__name__}) — "
                        "inspect them by hand")]
        if pending:
            details.append("")
            details.extend(f"  ! {rel}: {word} — {how}" for rel, word, how in pending)
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
                state, what = self.self_update_incomplete()
                details.append(f"[controller] {cs['display_name']}  ({upd}"
                               + (f", {state})" if state else ")"))
                details.append(f"  v{cs['version']} {head}  {ident}  — manage with: "
                               f"{cs['self_update_cmd']}")
                if state:
                    details.append(f"  ! {state}: {what} — resolve it with: "
                                   f"{self._incomplete_remedy(state)}")
                try:
                    from . import updater_units as _uu
                    if self.updater_integration()["status"] == _uu.COMPATIBLE:
                        details.append(f"  ! {_uu.COMPATIBLE_NOTE}")
                except Exception:               # a diagnostic line never breaks status
                    pass
        # Disk space: ONE line, only when the worst filesystem is not ok (the normal status stays
        # unchanged).
        if not stack_id:
            from .service_system import disk_figure, worst_disk_row
            worst = worst_disk_row(self.disk_health())
            if worst is not None and worst["level"] != "ok":
                details.append("")
                details.append(f"disk: {worst['level'].upper()} ({disk_figure(worst)})")
        # Probing succeeded; status is informational — exit success even when stopped.
        return ActionResult(
            ok=True,
            summary="Status collected (read-only; no network, no changes).",
            details=details,
            next_commands=["lhpc explain <stack>", "lhpc doctor", "lhpc status --versions"],
        )

    def _venv_versions(self, comp) -> dict[str, str]:
        """`venv_packages` -> the version installed in the component's `.venv`, read from the
        `.dist-info` directory names ("-" = not installed there)."""
        lib = self._paths.resolve_source(comp.source.path) / ".venv" / "lib"
        out = {}
        for name in comp.venv_packages:
            norm = name.lower().replace("-", "_")
            found = sorted({d.name[len(norm) + 1:-len(".dist-info")]
                            for d in lib.glob("python3*/site-packages/*.dist-info")
                            if d.name.lower().startswith(norm + "-")})
            out[name] = "/".join(found) or "-"
        return out

    def status_versions(self) -> ActionResult:
        snap = self.build_snapshot()
        details: list[str] = []
        for ss in snap.stacks:
            # package -> version -> components, to name a package the stack runs in two versions
            seen: dict[str, dict[str, list[str]]] = {}
            unbuilt = set(self.unbuilt_components(ss.stack.id))   # e.g. after a source update
            for comp in ss.stack.components:
                if comp.source is None:
                    continue
                st = ss.components[comp.id]
                pin = (comp.source.pin_commit[:12] or "-") if comp.source else "-"
                tag = comp.source.pin_tag or "-"
                if st.source_state.value == "binary":
                    # Binary channel: the artifact's own provenance is the honest answer —
                    # there is no checkout whose HEAD could be compared to the pin.
                    details.append(
                        f"  {comp.id:24s} {'binary':12s} "
                        f"{st.source_version} built_from={(st.source_head or '-')[:12]} "
                        f"pin={pin}"
                    )
                else:
                    versions = self._venv_versions(comp) if comp.venv_packages else {}
                    for name, ver in versions.items():
                        if ver != "-":
                            seen.setdefault(name, {}).setdefault(ver, []).append(comp.id)
                    details.append(
                        f"  {comp.id:24s} {st.source_state.value:12s} "
                        f"pin={pin} tag={tag}"
                        + (" needs-rebuild" if comp.id in unbuilt else "")
                        + "".join(f" {n}={v}" for n, v in versions.items())
                    )
            for name, by_ver in seen.items():
                if len(by_ver) > 1:
                    details.append(f"  {ss.stack.id}: {name} differs — " + ", ".join(
                        f"{v} ({', '.join(ids)})" for v, ids in sorted(by_ver.items())))
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
        gui_unavailable = set(self.gui_unavailable_components(s))
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
            if c.id in gui_unavailable:
                details.append(f"        UNAVAILABLE HERE: {GUI_MISSING_HINT}")
        return ActionResult(
            ok=True,
            summary=f"Stack '{s.id}': {s.name}",
            details=details,
            next_commands=[f"lhpc status {s.id}"],
        )

    def doctor(self) -> ActionResult:
        sys = self._system
        details: list[str] = []
        # Copyable install/grant commands for every UNSATISFIED dep (mandatory OR optional), collected
        # here and printed as one block at the VERY END. Shell commands only — `lhpc install`/`build`
        # action entries are NOT shell commands and are excluded.
        install_cmds: list[str] = []

        def _add_cmd(cmd: str) -> None:
            if cmd and cmd not in install_cmds and not cmd.startswith(("lhpc install", "lhpc build")):
                install_cmds.append(cmd)

        root = self._paths.runtime_root
        details.append(
            f"runtime root: {'present' if self._paths.runtime_root_exists else 'absent (run lhpc bootstrap)'} ({root})"
        )
        op = self.config().operator
        details.append(
            f"  operator: {op.callsign if op.configured else 'not configured (set in runtime config/local.toml)'}"
        )
        details.append(f"  systemctl: {hardware.check_systemctl(sys, user=False).detail}")
        details.append(f"  systemctl --user: {hardware.check_systemctl(sys, user=True).detail}")
        # The clock gate's verdict: every PKI refusal ("the clock is not synchronised …") sends the
        # operator here, so name the same predicate and its reason. Informational: it never makes
        # doctor non-OK (commissioning and running stacks do not need a verified clock).
        from .clock import CLOCK_OVERRIDE_FLAG
        from .service_system import clock_verified
        clock_ok, clock_why = clock_verified(sys.fs, self._paths.runtime_root)
        details.append("  clock: " + ("verified (certificates may be issued)" if clock_ok else
                       f"NOT verified — {clock_why}; certificate operations refuse while the clock "
                       "remains unverified; resolve the clock or verification problem, or use "
                       f"{CLOCK_OVERRIDE_FLAG}"))
        # Disk space: one line per filesystem, from the ONE classifier. `critical` on ANY row makes
        # doctor non-OK (a nearly full card breaks installs, builds and config writes); `low` is
        # informational.
        from .service_system import disk_figure
        disk_critical = False
        for row in self.disk_health():
            inodes = ("" if not row["total_inodes"] else
                      f", {100 - 100 * row['free_inodes'] // row['total_inodes']} % inodes used")
            details.append(f"  disk {row['path']}: {row['free_b'] / 1024 ** 3:.1f} GiB free of "
                           f"{row['total_b'] / 1024 ** 3:.0f} GiB{inodes}: {row['level']}"
                           + (f" ({disk_figure(row)})" if row["level"] != "ok" else ""))
            disk_critical = disk_critical or row["level"] == "critical"
        # Certificate expiry, from the ONE helper in pki (the same line as the console and
        # `webserver status`). An EXPIRED server certificate makes doctor non-OK (nginx then serves
        # a certificate browsers reject); everything else here is information.
        cert_expired = False
        if self._paths.runtime_root_exists:
            from . import pki as _pki
            try:
                pst = _pki.pki_status(self._paths)
            except (_pki.PKIError, OSError, ValueError) as exc:
                details.append(f"  certificates: unreadable ({exc})")
            else:
                sv = pst["server_cert"].get("expiry")
                if sv:
                    flag = "!" if (sv["state"] != "ok" or sv["over_cap"]) else " "
                    details.append(f" {flag}server certificate: {pst['server_cert']['expiry_text']}")
                    cert_expired = sv["state"] == "expired"
                for c in pst["clients"]:
                    if c.get("expiry_mark"):
                        details.append(f" !client certificate '{c.get('label', '?')}': "
                                       f"{c['expiry_mark']}: reissue it: lhpc webserver cert "
                                       f"reissue {c.get('label', '?')}")
                if pst.get("index_problem"):
                    details.append(f" !client-certificate list: repair needed: {pst['index_problem']}")
        # Controller's OWN system/runtime deps (same source as the /stacks System-dependencies panel).
        # A missing REQUIRED dep makes doctor non-OK (machine-actionable); optional ones never do.
        required_missing = False
        for grp in self.controller_system_deps():
            for d in grp["deps"]:
                if d["satisfied"]:
                    state = "present"
                elif d["required"]:
                    required_missing = True
                    state = f"MISSING — {d['install']}" if d["install"] else "MISSING"
                else:
                    hint = f": {d['install']}" if d["install"] else ""
                    state = f"not installed (optional — {d['purpose']}{hint})"
                if not d["satisfied"]:
                    _add_cmd(d["install"])
                details.append(f"  {d['what']}: {state}")
        for dev in (_SPI_DEV, _GPIO_DEV):
            chk = hardware.check_char_device(sys, dev)
            details.append(f"  {dev}: {chk.detail}")
        details.extend(f"  {x}" for x in spi_mode.doctor_lines(self._paths, self.stacks(), self._lifecycle().source_dir))

        # Configured source paths present?
        present = missing = covered = 0
        for s in self.stacks():
            for c in s.components:
                if c.source is None:
                    continue
                p = str(self._paths.resolve_source(c.source.path))
                if sys.fs.exists(p):
                    present += 1
                elif self.binary_covers(c.id):
                    # No checkout BY DESIGN — the artifact IS the build output. Counting these as
                    # "missing" made a healthy binary install read half-installed.
                    covered += 1
                else:
                    missing += 1
        _src = f"  configured sources: {present} present"
        if covered:
            _src += f", {covered} provided by a binary artifact"
        if missing:
            _src += f", {missing} missing"
        details.append(_src)

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
                    _add_cmd(d.install_cmd)
                details.append(line)
        if not any_missing:
            details.append("  dependencies: all declared system/build prerequisites satisfied")
        details.append(f"  ({deps_mod.NOT_EXECUTED_NOTE})")

        # BINARY-channel ownership: a receipt that no longer matches the disk (files removed
        # by a source adoption of a shared checkout, an interrupted transaction, a hand-edited
        # record) is the one binary-channel fault the ordinary status view cannot show — it
        # reads as an ordinary source state while the stack is in fact not installed
        # Name it here, with the reason and the way out.
        for s in self.stacks():
            if self.binary_spec(s.id) is None:
                continue
            state, _rec, why = self.binary_receipt_state(s.id)
            if state in ("unsafe", "superseded"):
                details.append(f"  {s.id}: binary install {state} — {why}")
                _cmd = f"lhpc install {s.id} --yes"
                details.append(f"    | run yourself: {_cmd}")
                _add_cmd(_cmd)
            elif state == "valid" and (_ov := self.binary_active_override(s.id, _rec)):
                # A3: a warning (informational), shown while the acceptance is still in force.
                details.append(f"  {s.id}: WARNING installed over the pin check — " + "; ".join(
                    f"{c} built from {b[:9]}, pins {p[:9]}" for c, (b, p) in sorted(_ov.items()))
                    + "; it may not work with this LHPC")
                details.append("    | run yourself: lhpc self-update --apply")
                _add_cmd("lhpc self-update --apply")
        if (_wl := web_client.status(self)) and "unproven" in _wl:
            details.append(f"  meshtastic: {_wl}")      # a daemon change since the pairing

        # The console's maintenance pass (`maintenance.py`): each task's last success and last
        # failure. Informational, like the clock line — but a task whose last pass failed, or a
        # record that cannot be read, is never shown as healthy.
        from . import maintenance
        m_state, m_tasks = maintenance.read(self._paths)
        if m_state == "absent":
            details.append("  maintenance: never run — the console runs it every 60 s or 300 s "
                           "while it runs; until then no log is cut on a schedule and nothing is "
                           "refreshed")
        for name, at, msg in maintenance.failing(self._paths):
            details.append(f" !maintenance {name}: FAILED{' at ' + at if at else ''} — {msg}")
        if m_state == "ok":
            for name, _task in maintenance.TASKS:
                e = m_tasks.get(name) or {}
                if e.get("last") != "ok":
                    continue
                ok_at = e.get("last_success", {}).get("at", "")
                fail = e.get("last_failure")
                details.append(f"  maintenance {name}: ok at {ok_at}"
                               + (f" (last failure {fail.get('at', '')}: {fail.get('message', '')})"
                                  if fail else ""))

        # Run-state tally from a fresh snapshot.
        snap = self.build_snapshot()
        tally: dict[str, int] = {}
        for ss in snap.stacks:
            for st in ss.components.values():
                tally[st.run_state.value] = tally.get(st.run_state.value, 0) + 1
        details.append("  components: " + ", ".join(f"{k}={v}" for k, v in sorted(tally.items())))
        observed = self._observed_conflicts(snap)
        details.append(f"  observed resource conflicts: {len(observed)}")

        # Boot restore silently STOPS working when the managed units stop matching what
        # this version renders — which every controller update that touches a unit
        # template causes. The gate itself is right (do not restore behind an unproven
        # console), but the operator only found out after a power cycle brought the box
        # up with nothing running. Say it here, while they can still fix it.
        try:
            proven, why = self._web_integration_proven()
        except Exception:                       # never let a diagnostic break doctor
            proven, why = True, ""
        if not proven:
            details.append("")
            details.append("  ! BOOT RESTORE WILL BE SKIPPED — " + why)
            details.append("    stacks will NOT come back after a reboot until this is repaired:")
            details.append("      lhpc self-update --repair-integration")
        elif why:                               # proven on a newer release's units: say so
            details.append("")
            details.append(f"  ! {why}")

        # An update that replaced the firewall helper leaves the installed one stale: the next boot
        # starts the console loopback-only until it is re-applied. Say it before that reboot.
        try:
            fw_note = self.firewall_reapply_notice()
        except Exception:                       # never let a diagnostic break doctor
            fw_note = None
        if fw_note:
            details.append("")
            details.append("  ! FIREWALL RE-APPLY REQUIRED — " + fw_note["text"])
            details.append("    run, before you reboot:")
            details.extend(f"      {c}" for c in fw_note["commands"])

        # GPS: a malformed [gps] has already disabled position (fail closed), and stale
        # per-stack values are inert but misleading. Both are quiet failures otherwise —
        # the operator believes a stack is reporting position when it is not.
        gcfg = getattr(self.config(), "gps", None)
        if gcfg is not None and not getattr(gcfg, "valid", True):
            details.append("")
            details.append(f"  ! POSITION SOURCE DISABLED — {gcfg.reason}")
            details.append("    stacks that would use GPS will start without a position:")
            details.append("      lhpc gps --source <auto|off|gpsd|nmea|fixed>")
        elif gcfg is not None and getattr(gcfg, "source", "") == "gpsd":
            # gpsd ANSWERING is not gpsd HAVING A RECEIVER. Debian's default is `DEVICES=""` with
            # `USBAUTO`, so gpsd depends on a udev hotplug event: restart it while the receiver is
            # already plugged in and it comes back owning nothing, accepts our connection happily,
            # and streams nothing forever. Every GPS source then silently yields no position, with
            # nothing anywhere naming the cause (hit exactly this on hardware).
            from .gps import gpsd_devices
            # `doctor` is documented as BOUNDED: one TOTAL deadline for the whole exchange, so a
            # configured-but-unhelpful gpsd cannot stall a health check.
            _devs, _err = gpsd_devices(gcfg.host, gcfg.port, timeout=1.0)
            # `systemctl`/`gpsdctl` act on the machine they run on. For a REMOTE gpsd, printing
            # them bare sends the operator to fix the wrong box.
            # `local_gpsd` is the CANONICAL answer (it knows 127.0.0.2, `localhost.`, and
            # expanded IPv6 loopback); a second hand-rolled check would drift from it.
            _where = "" if gcfg.local_gpsd else f"   # ON {gcfg.host}, not here"
            if _err:
                details.append("")
                details.append(f"  ! gpsd at {gcfg.host}:{gcfg.port} is not answering — {_err}")
                details.append("    a stack with GPS on will refuse to start:")
                details.append(f"      sudo systemctl status gpsd{_where}")
            elif not _devs:
                details.append("")
                details.append(f"  ! gpsd at {gcfg.host}:{gcfg.port} answers but owns NO receiver")
                details.append("    it will deliver no position at all; re-attach the device:")
                details.append(f"      sudo gpsdctl add /dev/ttyACM0{_where or '      # its path'}")
        for sid, keys in self.legacy_gps_values().items():
            details.append("")
            details.append(f"  ! {sid} has old per-stack position values on disk "
                           f"({', '.join(keys)})")
            details.append("    they are IGNORED — `lhpc gps` is authoritative for every stack")

        # Consolidated, copyable install/grant commands for everything unsatisfied — at the very end.
        if install_cmds:
            details.append("Install the missing dependencies:")
            details.extend(f"  {cmd}" for cmd in install_cmds)

        return ActionResult(
            ok=not (required_missing or disk_critical or cert_expired or fw_note),
            # The ONLY network access is a bounded ?DEVICES query to the gpsd the operator
            # configured, and only when the source IS gpsd — say so rather than promising
            # "no network" and then opening a socket.
            summary=("doctor: required dependencies missing; bounded checks only "
                     "(no init, no RF; contacts only a configured gpsd)." if required_missing
                     else "doctor: a filesystem is critically full; bounded checks only "
                          "(no init, no RF; contacts only a configured gpsd)." if disk_critical
                     else "doctor: the server certificate has expired; bounded checks only "
                          "(no init, no RF; contacts only a configured gpsd)." if cert_expired
                     else "doctor: the firewall must be re-applied after an update; bounded "
                          "checks only (no init, no RF; contacts only a configured gpsd)." if fw_note
                     else "doctor: bounded checks only "
                          "(no init, no RF; contacts only a configured gpsd)."),
            details=details,
            next_commands=["lhpc status", "lhpc status --versions"],
        )

    # ---- install / bootstrap ---------------------------------------------

    @invalidates_snapshot
    def bootstrap(self, apply: bool = False) -> ActionResult:
        from .install import PlanAction
        inst = self._installer()
        plan = inst.plan_bootstrap()
        # The firewall operator scripts exist from the first bootstrap on: the console, the
        # dashboard and `lhpc firewall` all name `firewall-apply.sh` as the apply command, so the
        # path they show must be a file (every later Firewall save re-renders it). Planned here,
        # applied here — the installer's action kinds stay filesystem-only.
        fw_path = self._fw_script_paths()["firewall-apply.sh"]
        fw_action = PlanAction("render", fw_path, "render the firewall operator scripts",
                               status="exists" if os.path.isfile(fw_path) else "planned")
        if not apply:
            plan.actions.append(fw_action)
            return self._plan_result(plan, applied=False, next_apply="lhpc bootstrap --yes")
        plan = inst.apply_bootstrap(plan)
        if fw_action.status == "planned":
            rendered = self.firewall_render()
            fw_action.status = "done" if rendered.ok else "failed"
            fw_action.detail = "" if rendered.ok else rendered.summary
        plan.actions.append(fw_action)
        return self._plan_result(plan, applied=True, next_apply=None)

    def _switch_records_missing(self, paths) -> list:
        """Source paths this switch adopted whose ownership record is not valid — the switch is
        not complete until every one of them is recorded."""
        from . import source_registry
        return [p for p in sorted(set(paths))
                if source_registry.record_state(self._paths, p)[0] != "valid"]

    def _resolve_switch(self, *, ok: bool, created) -> list:
        """THE single commit point of a binary -> source switch. Returns detail lines.

        `ok` is the verdict over the COMPLETE switch. On success the retirement becomes final
        and the local backups are dropped; on failure the source paths this switch CREATED are
        removed FIRST (a checkout that existed before the switch is never touched) and the
        previous binary — files, owned directories, receipt and authentication — is then
        restored from the transaction, with no network, no release lookup and no pin re-check."""
        if ok:
            if binary_install_mod.commit(self._paths):
                return []
            return ["  the retired artifact's backup could not be dropped — the next binary "
                    "operation resolves it"]
        # ORDER MATTERS: undo the new state before restoring the old one. The artifact's files
        # live INSIDE these source paths, so removing them afterwards would delete what the
        # restore just put back.
        undone = self._undo_created_sources(created)
        rb_ok, rb_why = self.binary_recover()
        if not rb_ok:
            return [*undone, f"  INCOMPLETE: the binary install could NOT be restored ({rb_why})" " — the recovery evidence is kept; resolve state/binary by hand"]
        return [*undone, "  restored the binary install from disk — the source switch changed " "nothing"]

    def _preserve_replaced_source(self, txn: str, rel: str) -> str:
        """Move a to-be-replaced checkout and its ownership record into the switch transaction.
        Returns "" or a typed reason. Both are restored together by `binary_recover()`."""
        from . import source_registry
        try:
            binary_install_mod.displace_dir(self._paths, txn, rel)
            binary_install_mod.displace(
                self._paths, txn,
                [str(source_registry.record_path(self._paths, rel).relative_to(
                    self._paths.runtime_root))])
        except (binary_install_mod.BinaryInstallError, OSError, PathContainmentError,
                ValueError) as exc:
            return f"the existing {rel} checkout could not be set aside ({exc})"
        return ""

    def _undo_created_sources(self, created) -> list:
        """Remove ONLY the checkouts (and their ownership records) this failed switch created —
        which includes the NEW tree at a path whose previous checkout was set aside above. A
        checkout that existed before the switch is restored, never left half-switched."""
        from . import source_fs, source_registry
        out = []
        for rel in sorted(set(created)):
            try:
                source_fs.rmtree_at(self._paths, self._paths.resolve_source(rel))
            except (OSError, PathContainmentError, ValueError) as exc:
                out.append(f"  INCOMPLETE: {rel} was created by this switch and could not be "
                           f"removed ({exc})")
                continue
            if not source_registry.remove_record(self._paths, rel):
                out.append(f"  INCOMPLETE: the ownership record this switch wrote for {rel} "
                           "could not be removed")
                continue
            out.append(f"  removed the {rel} checkout this switch created")
        return out

    def _note_recorded_selectors(self, res: ActionResult, stack_id: str | None,
                                 source: str) -> ActionResult:
        """`install` never changes a source that is already there (adopt_source skips an existing
        checkout), so `install --source pinned` over a checkout adopted from `dev` answers
        "Nothing to do" while the record still says `dev`. Say so, and name the command that
        switches it; the record itself stays as it is, which is true (that is how it was
        obtained)."""
        if not res.ok:
            return res                  # a failure's own remedy comes first; the note can wait
        from . import source_fs, source_registry
        seen, notes, cmds = set(), [], []
        for st in self.stacks():
            if stack_id and st.id != stack_id:
                continue
            for c in st.components:
                if c.source is None or c.source.path in seen:
                    continue
                seen.add(c.source.path)
                rec = source_registry.read_record(self._paths, c.source.path)
                if rec is None or rec.selector == source:
                    continue
                # A record whose checkout is gone (removed by hand) describes nothing installed:
                # the plan adopts the source anyway, so there is nothing to explain.
                try:
                    present = source_fs.leaf_kind(
                        self._paths, self._paths.resolve_source(c.source.path)) == "dir"
                except Exception:
                    present = False
                if not present:
                    continue
                at = f" (commit {rec.resolved_commit[:9]})" if rec.resolved_commit else ""
                notes.append(f"  [note] {c.id}: installed from '{rec.selector}'{at}; install does "
                             "not change an installed source")
                cmd = f"lhpc update {st.id} --source {source} --yes"
                if cmd not in cmds:
                    cmds.append(cmd)
        if not notes:
            return res
        notes.append("  switch it with: " + " ; ".join(cmds))
        return ActionResult(res.ok, res.summary, details=[*list(res.details), *notes],
                            next_commands=[*cmds, *res.next_commands], data=res.data)

    @invalidates_snapshot
    def install(self, stack_id: str | None = None, apply: bool = False,
                source: str = "pinned", auto_install_ctx=None, on_admit=None) -> ActionResult:
        if (_r := self._controller_refusal(stack_id)) is not None:
            return _r
        if stack_id and self.stack(stack_id) is None:
            return self._unknown_stack(stack_id)
        # ---- CHANNEL dispatch (before any source planning) -------------------------------
        if source == self.BINARY_CHANNEL:
            if not stack_id:
                return ActionResult(False, "The binary channel installs ONE stack at a time.",
                                    next_commands=["lhpc install <stack> --source binary"])
            return self.binary_install(stack_id, apply=apply)
        # A SOURCE install over a binary install must not be silently skipped ("destination
        # already exists") — the artifact is retired, but LATER: only once the runtime root,
        # shared-remote coherence and the adoption plan have all validated, and inside the
        # install's own guards. Retiring up here would destroy a working binary before a
        # source resolution failure that never adopts anything.
        _retire_note = ""
        # ANY receipt state but "absent" must be retired — a SUPERSEDED or drifted receipt still
        # names files this box owns, and `on_binary_channel` (valid receipts only) let those
        # bypass retirement entirely. An unreadable receipt is refused by
        # `binary_retire` itself, with the manual-resolution command.
        _retire_binary = bool(apply and stack_id
                              and self.binary_receipt_state(stack_id)[0] != "absent")
        if not self._paths.runtime_root_exists:
            return ActionResult(
                ok=False,
                summary="Runtime root is not bootstrapped yet.",
                details=[f"Run 'lhpc bootstrap' to create {self._paths.runtime_root}."],
                next_commands=["lhpc bootstrap"],
            )
        # SHARED-SOURCE REMOTE COHERENCE gates BOTH planning and mutation: one checkout is
        # one clone with ONE effective remote — a divergent per-component override
        # blocks install with ZERO candidate/source/registry/config mutation.
        planned_paths = sorted({c.source.path for st in self.stacks()
                                if not stack_id or st.id == stack_id
                                for c in st.components if c.source})
        # The install's own command, repeating the channel it resolved (the operator's
        # `--source`, else the default): a retry that dropped it could install from another one.
        retry = f"lhpc install {stack_id + ' ' if stack_id else ''}--source {source} --yes"
        conflicts = sorted({c for c in (self._shared_remote_conflict(p)
                                        for p in planned_paths) if c})
        if conflicts:
            return ActionResult(False, f"Refusing to install '{stack_id or 'all'}': "
                                "shared-source remote configuration is inconsistent.",
                                details=[*(f"  {c}" for c in conflicts),
                                         "  nothing to run here — give the shared source one "
                                         "remote in config/local.toml for all the components "
                                         "named above, then run the install again"],
                                next_commands=[retry])
        inst = self._installer()
        plan = inst.plan_install(stack_id)
        if not apply:
            res = self._plan_result(plan, applied=False, next_apply=retry)
            if stack_id and self.on_binary_channel(stack_id):
                # SWITCHING AWAY from the binary channel is real work even when every source
                # path already exists: the artifact must be retired (and its files removed)
                # first. Without counting it, the CLI's dry-run short-circuit reports "Nothing
                # to do" and the switch silently never happens.
                _d = dict(res.data)
                _d["changes"] = int(_d.get("changes", 0)) + 1
                return ActionResult(
                    res.ok, res.summary,
                    details=[f"  [switch] retire the binary install of '{stack_id}' " "(its files are removed, then the sources are adopted)", *list(res.details)],
                    next_commands=res.next_commands, data=_d)
            return self._note_recorded_selectors(res, stack_id, source)
        from . import source_fs
        # ONE adoption per coherent source GROUP: each shared path is installed exactly once
        # (deterministic first declarer), never opportunistically re-attempted through
        # whichever consumer is encountered next.
        # ONE immutable plan for the whole install: known-working frozen per stack, one
        # adoption per shared source group, incompatible resolutions blocked up front.
        install_items = [(st, c) for st in self.stacks()
                         if not stack_id or st.id == stack_id
                         for c in st.components if c.source]
        ctx_err = self._auto_install_ctx_error(auto_install_ctx,
                                       {c.source.path for _, c in install_items})
        if ctx_err:
            return ActionResult(False, f"Refusing to install '{stack_id or 'all'}': "
                                f"{ctx_err}",
                                details=["  nothing to run here — the auto-install's plan no "
                                         "longer matches the sources; start it again from the "
                                         "console"])
        groups, plan_conflicts = self._plan_source_groups(install_items, source)
        if plan_conflicts:
            return ActionResult(False, f"Refusing to install '{stack_id or 'all'}': "
                                "incompatible source resolutions for a shared checkout.",
                                details=[*(f"  {c}" for c in plan_conflicts),
                                         "  install them with one selection"],
                                next_commands=[f"lhpc install {stack_id} --source pinned --yes"
                                               if stack_id else
                                               "lhpc install --source pinned --yes"])
        mutated_paths, extra_out = [], []
        # WEB-JOB admission (P1-4): a detached web install must record `running` (authoritative admission)
        # only AFTER it holds its source guard, and must mutate nothing if it was superseded meanwhile.
        # Wrap the adoption loop in the ONE source boundary and adopt in the outer-held `locked` mode (exactly
        # as auto-install does under its auto-install boundary); a False `on_admit` releases the guard and installs nothing.
        _guard_paths = sorted({c.source.path for _, c in install_items if c.source})
        _locked_adopt = (auto_install_ctx is not None) or (on_admit is not None)
        # Lock order #1: hold task admission across the whole adoption. The web-job (`on_admit`) path's
        # source guard already acquires it reentrantly; the direct path acquires it here and releases it
        # when the `with` below exits.
        from . import reslock
        _adm_stack = contextlib.ExitStack()
        try:
            self._admit(_adm_stack, "install", stack_id or "")
        except AdmissionRefused as _a:
            _adm_stack.close()
            return admission_refusal(_a)
        except reslock.ResourceBusy:
            _adm_stack.close()
            return ActionResult(False, "A task is starting right now (admission contended) — retry the "
                                "install.", data={"contended": True},
                                next_commands=[f"lhpc install {stack_id} --source {source} --yes"])
        # An install re-adopts source trees, which can replace the template the MeshCore
        # identity may still live in. Copy it out before the first adoption — after every
        # plan/coherence refusal above, so a refused install still mints nothing.
        _id_err = self.meshcore_identity_guard([c for _s, c in install_items])
        if _id_err:
            _adm_stack.close()
            return ActionResult(False, f"Refusing to install '{stack_id or 'all'}': {_id_err}",
                                details=["  nothing to run here — the MeshCore identity "
                                         "could not be copied out of the source; the "
                                         "box's operator checks the file named above, "
                                         "then retries"])
        _guard = (self._source_operation_guard(_guard_paths, op="install")
                  if on_admit is not None else contextlib.nullcontext())
        _switch_txn = ""
        _sw_replace: set = set()          # checkouts the switch must REPLACE (wrong commit)
        # Source paths this switch CREATED from nothing — the only ones a rollback may remove
        # (a checkout that existed before the switch is never reset by it).
        _switch_created: list = []
        if _retire_binary:
            # SELECTOR ENFORCEMENT, before anything is set aside: an existing checkout must be
            # provably ours, clean, and at the commit the requested selector resolves to —
            # otherwise this "switch to dev" would leave a pinned tree in place and report
            # success. Refuse here, with the artifact untouched.
            _sw_owned = (self.binary_receipt_state(stack_id)[1] or None)
            _sw_replace, _sw_refusals = self.switch_source_plan(
                groups, owned_files=(_sw_owned.files if _sw_owned else ()))
            if _sw_refusals:
                _adm_stack.close()
                return ActionResult(
                    False,
                    f"Refusing to switch '{stack_id}' to the {source} source channel: the "
                    "existing checkout(s) cannot be taken over.",
                    details=[f"  {r}" for r in _sw_refusals],
                    next_commands=[f"lhpc status {stack_id}"])
            # Everything above validated; now it is safe to hand the paths back to the source
            # machinery. The artifact is retired INTO AN OPEN TRANSACTION: its files, its owned
            # directories and its receipt are moved aside locally, not deleted. If the adoption
            # below fails, `binary_recover()` puts the exact previous install back from disk —
            # re-downloading it would need the network, the release, and an artifact that still
            # matches the pins, which is precisely what an operator switching to source is
            # working around. `locked=False` so retirement rechecks running.
            _switch_txn = secrets.token_hex(8)
            try:
                binary_install_mod.open_txn(
                    self._paths, stack_id, _switch_txn,
                    old_receipt=binary_receipt_mod.read_raw(self._paths, stack_id))
            except binary_install_mod.BinaryInstallError as _exc:
                _adm_stack.close()
                return ActionResult(False, f"Refusing to install '{stack_id}': {_exc.message}",
                                    details=["  nothing to run here — state/binary/"
                                             "install.journal.json could not be written "
                                             "(free space / permissions)"],
                                    next_commands=[f"lhpc install {stack_id} --source {source} "
                                                   "--yes"])
            _ret = self.binary_retire(stack_id, txn=_switch_txn)
            if not _ret.ok:
                self.binary_recover()               # nothing moved, or everything goes back
                _adm_stack.close()
                return _ret
            _retire_note = _ret.summary
        with _adm_stack, _guard:
            if on_admit is not None and not on_admit():
                if _retire_note:
                    self.binary_recover()      # put the set-aside artifact back
                return ActionResult(False, "Install superseded before admission — nothing was changed.",
                                    details=["  nothing to run here — this console attempt was "
                                             "replaced or dismissed in the task banner before it "
                                             "was admitted; the banner shows the current one"])
            for path, comp, selector, resolved in groups:
                dest = self._paths.resolve_source(path)
                # DESCRIPTOR-PROVEN skip: only a healthy managed DIRECTORY is "already
                # installed". Anything else (absent, symlink, regular/special file) flows
                # into `adopt_source`, whose locked leaf checks install or refuse typed —
                # a dangling/unknown leaf is never silently treated as installed.
                try:
                    # On a SWITCH, a directory the pre-flight marked for replacement must go
                    # through adoption (forced) — never through the "already installed" skip.
                    if _retire_binary and path in _sw_replace:
                        raise _SwitchReplace
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
                except _SwitchReplace:
                    pass                       # fall through to the forced adoption below
                except PathContainmentError:
                    pass                       # unsafe parent -> adopt_source refuses typed
                if _retire_binary and path in _sw_replace:
                    # PRESERVE the checkout this switch is about to replace — the directory AND
                    # its ownership record — inside the switch transaction. The source
                    # transaction deletes the prior tree once its own activation succeeds, so
                    # without this a later failure could restore the binary while the checkout
                    # (and its NEW registry txn id) stayed on the source channel: the receipt's
                    # baseline no longer matched and the restored install read SUPERSEDED
                    # Adoption then runs against an absent destination.
                    _err = self._preserve_replaced_source(_switch_txn, path)
                    if _err:
                        # undo the earlier groups' checkouts, then restore the binary
                        return ActionResult(
                            False, f"Refusing to switch '{stack_id}' to the {source} source "
                                   f"channel: {_err}",
                            details=[*self._resolve_switch(ok=False, created=_switch_created),
                                     "  fix the path named above, then run the install again"],
                            next_commands=[f"lhpc install {stack_id} --source {source} --yes"])
                _pre_absent = False
                if _retire_binary:
                    try:
                        _pre_absent = source_fs.leaf_kind(self._paths, dest) == "absent"
                    except PathContainmentError:
                        _pre_absent = False
                st_of = next((st2 for st2 in self.stacks()
                              if any(c2.id == comp.id for c2 in st2.components)), None)
                # Announce the clone log BEFORE the (possibly minutes-long, off-TTY-silent)
                # adoption — same copy-pasteable watch line the auto-install run emits.
                if comp.source:
                    extra_out.append(f"  [log] {comp.id} -> tail -f "
                                     f"{self._paths.under('logs', f'adopt-{comp.id}.log')}")
                result = self._adopt_dev_fallback(
                    inst, st_of, comp, selector, resolved,
                    # A switch REPLACES a checkout that is at the wrong commit: the same forced
                    # adoption `lhpc update` uses (capture -> verify identity -> dirty check ->
                    # candidate -> atomic swap). Nothing binary-specific.
                    force=bool(_retire_binary and path in _sw_replace),
                    locked=_locked_adopt)
                if result.status == "done":
                    mutated_paths.append(path)
                    if _pre_absent:
                        _switch_created.append(path)
                for a in plan.actions:
                    if a.target == str(dest):
                        a.status, a.detail = result.status, result.detail
                        a.provenance = result.provenance
            # Password-auth by DEFAULT: after a successful source adoption and BEFORE the caller builds the
            # firmware, ensure the meshcom HMAC secret + param exist (idempotent — keeps an existing secret),
            # so the firmware bakes the shared secret in the same install. Covers per-stack + CLI install;
            # auto-install adopts+builds directly (its own hook, before its build). Skip on a failed adopt —
            # nothing gets built, so don't flip visible HMAC state on a broken install.
            hmac_err = ""
            if not any(a.status == "failed" for a in plan.actions):
                for sid in {st.id for st, _ in install_items}:
                    if self.hmac_applies(sid):
                        hr = self.hmac_set_secret(sid, "enable")
                        if not hr.ok:
                            # FAIL CLOSED: a failed enable must NOT report install success — the firmware would
                            # otherwise be built (by the caller) with an empty password while the operator
                            # believes auth is on.
                            hmac_err = f"{sid}: {self._hmac_redact(hr.summary)}"
                            break
            retire_ok = self._retire_candidates_for_paths(mutated_paths, extra_out)
            res = self._plan_result(plan, applied=True, next_apply=None)
            # ---- THE switch commit point ------------------------------------------------
            # The binary retirement becomes final ONLY when the WHOLE switch succeeded: every
            # source group adopted, every ownership record written, and (where it applies) the
            # MeshCom password enabled. Committing earlier left the operator with a failed
            # switch and no way back to the binary.
            _switch_note = []
            if _retire_note:
                _missing = self._switch_records_missing(mutated_paths)
                _switch_ok = bool(res.ok and not hmac_err and not _missing)
                _switch_note = self._resolve_switch(ok=_switch_ok, created=_switch_created)
                if not _switch_ok:
                    _why = ("the source adoption failed" if not res.ok else
                            f"the HMAC password could not be enabled ({hmac_err})" if hmac_err
                            else "the ownership record for "
                                 f"{', '.join(_missing)} is incomplete")
                    return ActionResult(
                        False,
                        f"Switch of '{stack_id}' to the {source} source channel FAILED — "
                        f"{_why}.",
                        details=[f"  {_retire_note}", *_switch_note, *list(res.details), *extra_out],
                        next_commands=[f"lhpc status {stack_id}"], data=res.data)
            if hmac_err:
                return ActionResult(False, res.summary + " — but the HMAC password could NOT be enabled "
                                    f"({hmac_err}); fix and re-run before starting the meshcom link.",
                                    details=_switch_note + list(res.details) + extra_out,
                                    next_commands=res.next_commands)
            if not retire_ok:
                return ActionResult(False, res.summary + " (candidate cleanup INCOMPLETE)",
                                    details=_switch_note + list(res.details) + extra_out,
                                    next_commands=res.next_commands)
            if _retire_note:
                # Say plainly what happened to the binary install, either way.
                return ActionResult(res.ok, res.summary,
                                    details=[f"  {_retire_note}", *_switch_note, *list(res.details)],
                                    next_commands=(res.next_commands if res.ok else
                                                   [f"lhpc install {stack_id} --source pinned "
                                                    "--yes"]),
                                    data=res.data)
            return self._note_recorded_selectors(res, stack_id, source)

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
        lc = Lifecycle(self._paths, self.stacks(), self.config(), self._system)
        # Stop verification probes the endpoints the RUNNING launch opened (MeshCore: by mode).
        running = self._request_memo(("meshcore-running-mode",), self.meshcore_running_mode)
        lc.expected_endpoints = lambda comp: _meshcore_mode.expected_endpoints(comp, running)
        wrap = getattr(self._ext, "wrap_spawn", None) if self._ext else None
        if wrap is not None:
            # A provider may wrap the detached-spawn path (the one call that bypasses the
            # command runner) — production leaves it untouched.
            lc._spawn = wrap(lc._real_spawn)
        return lc

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

    DAEMON_ID = "loraham-daemon"
    # After auto-starting the daemon, wait up to this long for its CONF socket to
    # answer before reporting success (the daemon inits the radio asynchronously).
    DAEMON_VERIFY_TIMEOUT_S = 4.0
    DAEMON_VERIFY_POLL_S = 0.5
    # For readiness="endpoint": wait up to this long for every ready=true endpoint.
    ENDPOINT_VERIFY_TIMEOUT_S = 6.0
    ENDPOINT_VERIFY_POLL_S = 0.3

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
            cfg = self._stack_config_cached(target)
            # Only a TICKABLE optional component (`optional_role`) honours its saved tick: a stale
            # `autostart_<feed>` or `autostart_<fixture>` flag must never force a feed the position
            # plan does not want (the whole stack then stopped starting) or silently replay a
            # synthetic position. The plan below is the ONLY feed admitter; the fixture runs only
            # when named directly.
            allowed_optional = {c.id for c in s.components
                                if cfg.get(f"autostart_{c.id}") == "on"
                                and self.optional_role(c) == "tickable"}
            # The GPS feed is NOT an operator auto-start choice: it is admitted from the ONE
            # resolved global GPS plan, computed HERE — before anything downstream acquires a
            # lock — so run order, claims and the rendered config all describe the same plan.
            # Without this, turning GPS on could never add the component: this order is built
            # from static manifest data plus saved autostart flags only.
            allowed_optional |= self._gps_components_for(target)
            allowed_optional -= self._gps_components_excluded(target)
            if s.id == _meshcore_mode.STACK_ID and not _meshcore_mode.clients_available(
                    self.meshcore_mode()):
                # Repeater-only: no Companion on TCP 5000, so a saved webui/cli auto-start must not
                # launch a client against nothing (same decision as status and readiness).
                allowed_optional -= set(_meshcore_mode.CLIENT_IDS)
            seeds = [c.id for c in s.components if not c.optional]
            if s.main and s.main not in seeds:
                seeds.append(s.main)
            seeds += list(allowed_optional)
        elif target in idx:
            seeds = [target]
            allowed_optional = {target}   # an explicit component run is always allowed
            # A DIRECT consumer start (`lhpc stack start meshcom-qemu`) must run under the same
            # GPS plan as its stack. Without this the consumer came up with no feed at all —
            # silently position-blind — because a component run order is seeds + dependencies
            # only, and the feed is admitted from the plan, never declared as a dependency.
            # The feed itself is not re-added when it IS the target (see `gps_block`).
            # Only a component that actually READS a position pulls the feed in. A feed (or the
            # fixture relay) must not drag the other one in either: both write the same endpoint,
            # and an explicit fixture run must stay possible.
            if target in self._gps_consumer_ids():
                _feeds = (self._gps_components_for(target)
                          - self._gps_components_excluded(target))
                allowed_optional |= _feeds
                seeds += sorted(_feeds)
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

    def _order_already_healthy(self, order, radio: str) -> bool:
        """`_all_components_healthy` against the CURRENT snapshot.

        One helper so the no-side-effect Start check and the callers that must run BEFORE it
        (the MeshCore static-position resolution, which a healthy no-op must not redo) can
        never drift apart. `build_snapshot` is memoized within an operation, so asking twice
        inside one start is cheap.
        """
        snap = self.build_snapshot()
        idx = {c.id: ss.components[c.id] for ss in snap.stacks for c in ss.stack.components}
        return self._all_components_healthy(order, idx, radio)

    def _all_components_healthy(self, order, st_index, radio: str) -> bool:
        """True when EVERY requested component is already healthy — the daemon serving every needed
        band (READY), and each non-library service component RUNNING. Basis for a no-side-effect
        Start (no launch, no daemon CONF SET, no param apply). A missing band or a stopped client
        makes it False so the normal apply-once-then-start path runs."""
        has_daemon = any(c.id == self.DAEMON_ID for _, c in order)
        # Served bands (never a dual value), arbitrated away from bands a running radio-direct stack
        # owns — so a daemon already serving only the free band(s) reads as healthy, not perpetually
        # "needs 868" while meshtastic owns it.
        need = self._daemon_arbitrated_bands(radio)[0] if has_daemon else []
        for _stack, comp in order:
            if comp.kind in (ComponentKind.LIBRARY, ComponentKind.FIRMWARE):
                continue
            # A NON-MAIN interactive sidecar is never RUNNING under lhpc (the operator
            # runs it in a terminal). Once its command HAS been presented (the
            # interactive marker exists), it must not veto the no-side-effect
            # already-healthy path — a second `start voice` stays a no-op. Before that
            # first presentation the start pass MUST run (generate the shared config,
            # write the marker, print the command) — a Lite box whose daemon is already
            # up would otherwise short-circuit to "already healthy" and never surface
            # the command at all. An interactive MAIN (chat) keeps the old behaviour
            # unconditionally: its start pass regenerates config every time.
            if comp.interactive and comp.id != _stack.main:
                # ONLY voice's terminal fallback (stack main is gui_optional): where the GUI
                # main is usable it is not offered and can never be marked, so it must never
                # veto — a healthy desktop stack would otherwise re-run its start pass
                # forever. Other interactive sidecars (nomadnet, meshcore-cli) keep their
                # long-standing marker rule unchanged.
                _m = next((c for c in _stack.components if c.id == _stack.main), None)
                if (_m is not None and _m.gui_optional
                        and not self.gui_fallback_active(_stack)):
                    continue
                if self.interactive_band(_stack.id) is not None:
                    continue
                return False
            if comp.gui_optional and (
                    any(getattr(r, "gui", False)
                        for r in self._lifecycle().missing_requirements(comp))
                    or (self.needs_display(comp) and not self.display_available())):
                continue
            if comp.id == self.DAEMON_ID:
                if not need or not all(self.daemon_view(b).ready for b in need):
                    return False
                continue
            if st_index[comp.id].run_state != RunState.RUNNING:
                return False
            # RUNNING is a PROCESS fact. For a component whose readiness is an endpoint,
            # a same-named foreign process satisfies it while the endpoint the driver
            # owns is absent — and the no-side-effect path then returned already_healthy
            # for something that never came up. Require the declared evidence too.
            if str(getattr(comp, "readiness", "")) == "endpoint" and not all(
                    e.present for e in (st_index[comp.id].endpoints or ())):
                return False
        return True

    def _daemon_needs(self, order, band: str = ""):
        """The daemon's required radio band + TX mode for this run order. `band`
        overrides the band for a band-switchable app stack. Returns (radio, tx); tx is
        None when no single value applies."""
        if not any(c.id == self.DAEMON_ID for _, c in order):
            return None, None
        if band in ALLOWED_BANDS:
            bands = {band}
        else:
            bands = {c.band for _, c in order if self.DAEMON_ID in c.depends_on and c.band}
        txs = {c.requires_daemon_tx for _, c in order
               if self.DAEMON_ID in c.depends_on and c.requires_daemon_tx}
        # "" = "no single band requested" -> the daemon serves all ACTIVE bands (one process each);
        # the daemon serves ONE band per process (dual radio = two processes).
        radio = "" if len(bands) != 1 else next(iter(bands))
        tx = next(iter(txs)) if len(txs) == 1 else None
        return radio, tx

    def _effective_band(self, stack_id: str, fallback: str = "") -> str:
        """The band a stack is actually running on (start marker, or for an
        interactive app the band it was launched on)."""
        return (self.running_band(stack_id, "") or self.interactive_band(stack_id)
                or fallback)

    def _dependents_of(self, target: str, bands=None) -> list[str]:
        """Stack ids of the stacks that depend on `target`, running or not: a cascade locks the
        stopped ones too, so none starts mid-cascade. With `bands`, a dependent that can run on
        none of them is left out."""
        order_ids = {c.id for _, c in (self._run_order(target) or [])}
        out = set()
        for s in self.stacks():
            if not any(d in order_ids for c in s.components for d in (c.depends_on or ())):
                continue
            can = {b for c in s.components for b in (c.bands or (c.band,)) if b}
            if bands and can and not (can & set(bands)):
                continue
            out.add(s.id)
        return sorted(out)

    # Bounded log retention (no background supervisor — runs at operation boundaries).
    LOG_RETENTION = 200          # keep at most this many *.log files
    LOG_RETENTION_BYTES = 64 * 1024 * 1024   # …and at most this many bytes total

    # A `.job` marker is a tiny TOML (pid + identity fields); anything larger is untrusted
    # diagnostic evidence, never read in full or treated as a live job.
    _JOB_MARKER_MAX = 64 * 1024

    # ---- unified action dispatch (used by the web control interface) -----

    # Web-exposed actions -> the same gated service methods the CLI calls.
    WEB_ACTIONS = ("install", "update", "uninstall", "start", "stop", "restart",
                   "build", "test", "test-tx", "clean")

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
            # IDENTITY params are never stale-default candidates. Filtered HERE, at the point of
            # use, and not only where candidates are chosen: the candidates may have been chosen
            # by pre-update code without this exclusion, so a
            # deliberately pinned local callsign equal to the global would be deleted by the very
            # update that ships this rule. Dropping it here is silent and correct —
            # the value simply stays as the operator set it.
            if self._is_identity_candidate(cand):
                continue
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

    RADIO_BANDS = ALLOWED_BANDS   # the FULL band universe — detection/read/manage (never narrowed)

    def hardware_setup(self) -> str:
        """The configured radio HARDWARE setup id ('unset' | 'loraham' | 'uputronics' | …)."""
        return self.config().radio.hardware

    def hardware_configured(self) -> bool:
        """True once the operator has picked a real hardware setup (not 'unset')."""
        return self.config().radio.configured

    def hw_preset_for_band(self, band: str) -> str:
        """The daemon `--hw` wire preset for a served band under the current setup, or '' if the
        setup does not serve that band / no hardware is configured."""
        return self.config().radio.hw_preset(band)

    def hw_setups(self) -> list:
        """The hardware-setup catalog for the UI/CLI: [(id, label), …] in display order."""
        return [(sid, label) for sid, (label, _map) in HW_SETUPS.items()]

    def radio_mode(self) -> str:
        """DERIVED band-mode for the dashboard narrowing / labels: 'both' | '433' | '868' | 'unset'."""
        return self.config().radio.radio_mode

    def active_bands(self) -> tuple:
        """The bands the current hardware setup OFFERS / SERVES / STARTS — a subset of RADIO_BANDS,
        and EMPTY () when no hardware is configured. Use this for what lhpc shows/serves/starts; use
        RADIO_BANDS for what lhpc can still detect/manage."""
        return self.config().radio.active_bands

    def band_active(self, band: str) -> bool:
        """True iff `band` is served by the current hardware setup (the SET/offer gate)."""
        return band in self.active_bands()

    def _daemon_serve_bands(self, radio: str = "") -> list:
        """The explicit single band(s) a daemon start SERVES, from a requested `radio` value — ALWAYS
        clamped to the active mode and ALWAYS explicit (lhpc runs one
        process per band). A single active band -> [that band]; anything else (empty, a dual-band value,
        or the excluded band) -> the active band(s). radio_mode='both' therefore serves TWO processes."""
        active = list(self.active_bands())
        return [radio] if radio in ALLOWED_BANDS and radio in active else active

    # -- CALL/node identity enforcement (plan and apply, on the saved configuration) ----

    # A run/file param whose validator marks it the stack's operator identity: a "callsign"
    # validator => LICENSED (refuse empty / N0CALL); a "node" validator => UNLICENSED (refuse only
    # empty, the default name is accepted).
    _IDENTITY_ENFORCE: ClassVar[dict] = {
        "callsign": "licensed", "callsign_voice": "licensed", "callsign_meshcom": "licensed",
        "node": "unlicensed", "node_long": "unlicensed", "node_short": "unlicensed"}

    SOURCE_CHOICES = ("pinned", "dev", "stable")   # pinned = production-safe default
    # The binary CHANNEL is a fourth selector alongside the three SOURCE selectors. It is
    # deliberately NOT in SOURCE_CHOICES: `adopt_source` and the git planners must never see it
    # (a binary install has no ref to resolve — it IS the pinned refs, precompiled).
    BINARY_CHANNEL = "binary"
    CHANNEL_CHOICES = (*SOURCE_CHOICES, BINARY_CHANNEL)

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

    # Feed scan window. The RX/TX lines are a SMALL fraction of the daemon's stdout, so we must
    # scan far more than we display and filter FIRST — tailing 400 lines and filtering afterwards
    # made "recent" mean "within the last 400 log lines", not "recent in time", and a chatty client
    # (beacons + digipeat + RX) evicted a seconds-old TX while a quiet chat kept it for minutes.
    # Bounded + no-follow; ~200 KB typical per 3 s poll, which stays cheap on a Pi.
    _FEED_SCAN_LINES = 2000
    _FEED_SCAN_BYTES = 512 * 1024

    # ---- helpers ---------------------------------------------------------

    def _unknown_stack(self, stack_id: str) -> ActionResult:
        known = ", ".join(s.id for s in self.stacks())
        # A COMPONENT id here is not a typo — it is the operator using the wrong
        # granularity. `install` adopts a whole stack (a lone component would leave
        # unmet build_requires and a broken run order); `update` refreshes ONE source.
        # Say which stack owns it instead of listing stacks and leaving them to guess.
        owner = self.stack_of(stack_id)
        if owner and owner != stack_id:
            return ActionResult(
                ok=False,
                summary=f"'{stack_id}' is a component of the '{owner}' stack, not a stack.",
                details=[f"  Install works on whole stacks: lhpc install {owner}",
                         f"  To refresh just this source:   lhpc update {stack_id}"],
                next_commands=[f"lhpc install {owner}", f"lhpc update {stack_id}"],
            )
        return ActionResult(
            ok=False,
            summary=f"Unknown stack '{stack_id}'.",
            details=[f"Known stacks: {known}"],
            next_commands=["lhpc list"],
        )


def _render_component(comp, status) -> list[str]:
    eff_band = status.band or comp.band       # runtime-actual band wins over the manifest default
    band = f"band {eff_band}" if eff_band else "band -"
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
