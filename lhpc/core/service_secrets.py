"""`lhpc secrets backup` / `restore`: the locks, the stacks and the texts (A5). The files are read,
checked and written by `secrets_backup`."""
from __future__ import annotations

import errno
import hashlib
import json
import os
import re
import socket
import stat
import time
from pathlib import Path

from .service_base import ActionResult
from .snapshot_memo import invalidates_snapshot

_CONSOLE_STOP = "systemctl --user stop lhpc-web"
_CONSOLE_START = "systemctl --user start lhpc-web"
_LOCK_BOUND_S = 2.0
_CLIENT_CA = "config/tls/client-ca/ca.crt"


def _real_dir(p: Path) -> bool:
    """A folder itself, not a symlink to one (`lstat`)."""
    try:
        return stat.S_ISDIR(os.lstat(p).st_mode)
    except OSError:
        return False


def _cert_der(pem: bytes | None) -> bytes | None:
    """The certificate itself (DER), so a re-encoded PEM of the same certificate compares equal."""
    from cryptography import x509
    from cryptography.hazmat.primitives.serialization import Encoding
    try:
        return x509.load_pem_x509_certificate(pem).public_bytes(Encoding.DER) if pem else None
    except ValueError:
        return pem


def _read_regular(p: Path) -> bytes | None:
    """A regular file's bytes, never through a final symlink; None when absent or unreadable."""
    try:
        fd = os.open(p, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except OSError:
        return None
    with os.fdopen(fd, "rb") as f:
        return f.read() if stat.S_ISREG(os.fstat(f.fileno()).st_mode) else None


class SecretsOpsMixin:

    def _secrets_state_roots(self) -> list:
        """Every declared `state_root` (the set `clean --purge` collects, all stacks)."""
        return sorted({c.state_root for s in self.stacks() for c in s.components if c.state_root})

    def _secrets_running(self) -> list:
        """The stacks with a state root that are running (they write their state and secrets)."""
        return sorted(s.id for s in self.stacks()
                      if any(c.state_root for c in s.components) and self.stack_running(s.id))

    def _secrets_locked(self, op: str, *, console: bool):
        """The locks in the product's fixed order, entered into one ExitStack: the task admission
        FIRST, (restore) the controller-runtime lock EXCLUSIVE, the config lock (bounded), the PKI
        lock. Each busy one raises; the caller turns it into a refusal before any write."""
        import contextlib

        from . import config as _config
        from . import selfupdate
        stack = contextlib.ExitStack()
        try:
            self._admit(stack, op)
            if console:
                stack.enter_context(selfupdate.controller_runtime_lock(self._paths, exclusive=True))
            stack.enter_context(_config.config_lock(self._paths, timeout=_LOCK_BOUND_S))
            stack.enter_context(self._pki_lock(op))
        except BaseException:
            stack.close()
            raise
        return stack

    def _secrets_busy(self, exc) -> ActionResult | None:
        from . import config as _config
        from . import reslock, selfupdate
        from .service_base import AdmissionRefused, admission_refusal
        if isinstance(exc, AdmissionRefused):
            return admission_refusal(exc)
        if isinstance(exc, selfupdate.ControllerRuntimeBusy):
            return ActionResult(False, "The console is running: stop it first, then run the restore "
                                "again, and start it afterwards.",
                                next_commands=[_CONSOLE_STOP, _CONSOLE_START])
        if isinstance(exc, (selfupdate.ControllerRuntimeLockError, reslock.LockOpenError)):
            return ActionResult(False, f"The controller's lock could not be opened ({exc}); "
                                "nothing was backed up or restored.")
        if isinstance(exc, _config.ConfigRecoveryRequired):
            return ActionResult(False, f"{exc}; nothing was backed up or restored.")
        if isinstance(exc, (reslock.ResourceBusy, _config.ConfigLockBusy)):
            return ActionResult(False, "Another operation holds the controller's locks; nothing was "
                                "backed up or restored. Try again shortly.")
        return None

    # ---- backup ----------------------------------------------------------------------------

    @invalidates_snapshot
    def secrets_backup(self, dest: str | None = None) -> ActionResult:
        """Write ONE plain tar of config/tls, config/secrets, config/secrets.toml and every declared
        state root (their modes, no timestamps or ownership) to `dest` or to $HOME, never over an
        existing file and never inside the runtime root."""
        from ..version import __version__
        from . import secrets_backup as sb
        root = Path(self._paths.runtime_root)
        if not self._paths.runtime_root_exists or not (root / sb.SECRETS).is_dir():
            return ActionResult(False, "The runtime root is not bootstrapped yet; there is nothing to back up.",
                                next_commands=["lhpc bootstrap"])
        host = socket.gethostname()
        if dest:
            target = Path(dest).expanduser()
        else:
            stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
            safe = re.sub(r"[^A-Za-z0-9._-]", "-", host) or "box"
            target = Path.home() / f"lhpc-secrets-{safe}-{stamp}.tar"
        name = target.name
        if name in ("", ".", ".."):
            return ActionResult(False, f"{dest!r} names no file.")
        try:
            dirfd = os.open(target.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
        except OSError as exc:
            return ActionResult(False, f"The folder {target.parent} cannot be opened ({exc.strerror}).")
        try:
            folder = os.readlink(f"/proc/self/fd/{dirfd}")
            real_root = os.path.realpath(root)
            if folder == real_root or folder.startswith(real_root.rstrip("/") + "/"):
                return ActionResult(False, "The backup file must not be inside the runtime root "
                                    f"({folder}); choose a folder outside it, for example your home.")
            out = f"{folder.rstrip('/')}/{name}"
            try:
                with self._secrets_locked("secrets-backup", console=False):
                    return self._secrets_backup_locked(root, dirfd, name, out, host, __version__)
            except Exception as exc:
                busy = self._secrets_busy(exc)
                if busy is None:
                    raise
                return busy
        finally:
            os.close(dirfd)

    def _secrets_backup_locked(self, root, dirfd, name, out, host, version) -> ActionResult:
        from . import secrets_backup as sb
        running = self._secrets_running()
        if running:
            return ActionResult(False, "A stack that keeps its state in the backup is running: "
                                f"{', '.join(running)}. Stop it first; no backup file was written.",
                                next_commands=[f"lhpc stack stop {s}" for s in running])
        roots = self._secrets_state_roots()
        try:
            members, absent = sb.inventory(root, roots)
        except (sb.SecretsError, OSError) as exc:
            return ActionResult(False, f"Backup refused: {exc}. No backup file was written.")
        st = os.fstatvfs(dirfd)
        need = sb.estimated_size(members)
        if st.f_bavail * st.f_frsize < need * 1.1:
            return ActionResult(False, f"Not enough free space for the backup (about {need // 1024} KiB "
                                "plus 10 % needed). No backup file was written.")
        meta = {"lhpc_version": version, "hostname": host,
                "created_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        try:
            fd = sb.create_file(dirfd, name)
        except FileExistsError:
            return ActionResult(False, f"{out} already exists (or is a symlink); the backup never "
                                "replaces a file. Choose another name.")
        except OSError as exc:
            return ActionResult(False, f"{out} cannot be created ({exc.strerror or exc}).")
        try:                                   # from here on the file is ours: remove it on failure
            sb.write_backup(fd, dirfd, root, members, meta, absent)
            sb.recheck(root, members)
            size, digest = sb.file_digest(dirfd, name)
        except (sb.SecretsError, OSError) as exc:
            try:
                os.unlink(name, dir_fd=dirfd)
            except OSError:
                pass
            reason = str(exc) if isinstance(exc, sb.SecretsError) else (exc.strerror or str(exc))
            return ActionResult(False, f"Backup refused: {reason}. No file was left behind.")
        def _n(k):
            return f"{k} file" + ("" if k == 1 else "s")

        def _files(t):
            return sum(1 for m in members if m.kind == "file" and m.name.startswith(t + "/"))
        present = [r for r in roots if r not in absent]
        details = [f"  file: {out}", "  mode: 0600", f"  size: {size} bytes", f"  sha256: {digest}",
                   f"  config/tls: {_n(_files(sb.TLS))}" if sb.TLS not in absent else "  config/tls: absent",
                   f"  config/secrets: {_n(_files(sb.SECRETS))}",
                   f"  config/secrets.toml: {'1 file' if any(m.name == sb.SECRETS_TOML for m in members) else 'none'}",
                   f"  stack state folders: {len(present)}"
                   + (f" ({', '.join(r.split('/', 1)[1] for r in present)})" if present else "")]
        if absent:
            details.append(f"  absent here (listed as absent in the file): {', '.join(absent)}")
        details += ["", sb.CLEAR_TEXT_LINE, f"Note: {sb.BUNDLE_LINE}."]
        return ActionResult(True, "Secrets backup written.", details=details,
                            data={"path": out, "size": size, "sha256": digest})

    # ---- restore ---------------------------------------------------------------------------

    def _secrets_console_running(self) -> bool:
        from . import selfupdate
        try:
            with selfupdate.controller_runtime_lock(self._paths, exclusive=True):
                return False
        except selfupdate.ControllerRuntimeBusy:
            return True
        except selfupdate.ControllerRuntimeLockError:
            return False

    def _secrets_plan(self, root, snapfd, archive, only_pki: bool) -> tuple[dict, list]:
        """The plan (the classification and the warnings) and its printed lines."""
        from . import pki as _pki
        from . import secrets_backup as sb
        roots = self._secrets_state_roots()
        cls = sb.classify(root, archive, roots, only_pki)
        warnings = []
        theirs, mine = str(archive.manifest.get("hostname", "")), socket.gethostname()
        if not only_pki and theirs != mine:
            warnings.append("a full restore gives two boxes the same node identities on air; on "
                            "another box use --only pki")
        revived = []
        raw = sb.member_bytes(snapfd, sb._INDEX) if archive.carries(sb._INDEX) else None
        if raw:
            try:
                active = {c.get("serial"): c.get("label", "?") for c in json.loads(raw).get("certs", [])
                          if isinstance(c, dict) and c.get("state") == "active"}
            except (ValueError, AttributeError):
                active = {}
            revived = sorted(active[c.get("serial")] for c in _pki.list_client_certs(self._paths)
                             if c.get("state") != "active" and c.get("serial") in active)
        if revived:
            warnings.append("the backup's client index revives certificates revoked on this box: "
                            f"{', '.join(revived)}; revoke them again with lhpc webserver cert revoke <label>")
        # The file's content is part of what was shown: the manifest lists every member's sha256.
        digest = hashlib.sha256(json.dumps(archive.manifest, sort_keys=True).encode()).hexdigest()
        plan = {**cls, "warnings": warnings, "hostname": theirs, "archive": digest}

        def _list(ts):
            return ", ".join(ts) if ts else "(none)"
        lines = [f"  OVERWRITTEN ({len(cls['overwritten'])}): {_list(cls['overwritten'])}",
                 f"  CREATED ({len(cls['created'])}): {_list(cls['created'])}"]
        if only_pki:
            lines.append("  LEFT AS IT IS: everything else on this box")
        else:
            lines.append(f"  LEFT AS IT IS ({len(cls['left'])}): {_list(cls['left'])}")
        lines += [f"  the backup is from {theirs or '(unknown)'}; this box is {mine}",
                  *[f"  WARNING: {w}" for w in warnings],
                  "  Make a backup of this box first: lhpc secrets backup",
                  f"  Note: {sb.BUNDLE_LINE}."]
        return plan, lines

    @invalidates_snapshot
    def secrets_restore(self, file: str, *, only_pki: bool = False, choice: str = "",
                        expected_plan: dict | None = None) -> ActionResult:
        """Check the backup (pass 1, on a private snapshot), print the plan; with `choice` "yes"
        (no target exists) or "overwrite", apply it (pass 2) under the locks, after classifying
        the box AGAIN and refusing if it differs from the plan shown (`expected_plan`, or the one
        computed here before the locks)."""
        from . import secrets_backup as sb
        root = Path(self._paths.runtime_root)
        state = root / "state"
        if not self._paths.runtime_root_exists or not _real_dir(state):
            return ActionResult(False, "The runtime root is not bootstrapped yet.",
                                next_commands=["lhpc bootstrap"])
        try:
            fd = os.open(Path(file).expanduser(), os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        except OSError as exc:
            why = "is a symlink" if exc.errno == errno.ELOOP else f"cannot be opened ({exc.strerror})"
            return ActionResult(False, f"{file} {why}; no target was changed.")
        snap = -1
        try:                                   # owns the snapshot from its creation to the end
            try:
                st = os.fstat(fd)
                if not stat.S_ISREG(st.st_mode):
                    return ActionResult(False, f"{file} is not a regular file; no target was changed.")
                sdir = os.open(state, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
                try:
                    vfs = os.fstatvfs(sdir)
                    if vfs.f_bavail * vfs.f_frsize < 2 * st.st_size * 1.1:
                        return ActionResult(False, "Not enough free space in the runtime root for the "
                                            "restore (twice the file's size plus 10 %); no target was changed.")
                    snap = os.open(state, os.O_TMPFILE | os.O_RDWR | os.O_CLOEXEC, 0o600)
                    os.fchmod(snap, 0o600)     # the mode is not left to the umask
                finally:
                    os.close(sdir)
                while b := os.read(fd, 1 << 20):
                    sb._write_all(snap, b)
            except OSError as exc:
                return ActionResult(False, f"The backup could not be copied for checking "
                                    f"({exc.strerror or exc}); no target was changed.")
            finally:
                os.close(fd)
            return self._secrets_restore_snap(root, snap, only_pki, choice, expected_plan)
        finally:
            if snap >= 0:
                os.close(snap)

    def _secrets_restore_snap(self, root, snap, only_pki, choice, expected_plan) -> ActionResult:
        from . import secrets_backup as sb
        roots = self._secrets_state_roots()
        try:
            archive = sb.read_archive(snap, roots)
        except sb.SecretsError as exc:
            return ActionResult(False, f"Restore refused: {exc}. No target was changed.")
        if only_pki:
            if sb.TLS in archive.absent or not all(archive.carries(t) for t in sb.PKI_ONLY):
                return ActionResult(False, "Restore refused: the backup carries no complete PKI "
                                    "(config/tls/server-ca and client-ca). No target was changed.")
            if archive.carries(sb._MARKER):
                return ActionResult(False, "Restore refused: the backup's PKI is still provisional "
                                    "(made without a verified clock); --only pki would leave its CRL "
                                    "provisional on this box. No target was changed.")
            if not os.path.lexists(root / sb.TLS):      # a symlink here is refused below, by name
                return ActionResult(False, "--only pki needs this box's webserver PKI: lhpc webserver "
                                    "init first. No target was changed.",
                                    next_commands=["lhpc webserver init"])
        try:
            for t in sb.targets(archive, only_pki):
                sb.check_ancestors(root, t)
        except sb.SecretsError as exc:
            return ActionResult(False, f"Restore refused: {exc}. No target was changed.")
        plan, lines = self._secrets_plan(root, snap, archive, only_pki)
        shown = expected_plan if expected_plan is not None else plan
        head = [*lines]
        if self._secrets_console_running():
            head.append(f"  The console must be stopped first: {_CONSOLE_STOP}; start it again "
                        f"afterwards: {_CONSOLE_START}")
        if choice not in ("yes", "overwrite"):
            return ActionResult(True, "Restore plan (no target was changed). To apply it: --yes when no "
                                "target exists here, else --overwrite.", details=head,
                                data={"plan": plan})
        if choice == "yes" and shown["overwritten"]:
            return ActionResult(False, "Restore refused: targets exist on this box; --yes only "
                                "creates. To replace them: --overwrite.", details=head,
                                data={"plan": plan})
        try:
            with self._secrets_locked("secrets-restore", console=True):
                running = self._secrets_running()
                if running:
                    return ActionResult(False, f"A stack is running: {', '.join(running)}. Stop it "
                                        "first; no target was changed.", details=head,
                                        next_commands=[f"lhpc stack stop {s}" for s in running])
                now, _ = self._secrets_plan(root, snap, archive, only_pki)
                if now != shown:
                    return ActionResult(False, "state changed; run restore again. No target was "
                                        "changed.", details=head)
                if choice == "yes" and now["overwritten"]:
                    return ActionResult(False, "Restore refused: targets exist on this box; --yes "
                                        "only creates. To replace them: --overwrite.", details=head)
                try:
                    for t in sb.targets(archive, only_pki):
                        sb.check_ancestors(root, t)          # again, under the locks
                except sb.SecretsError as exc:
                    return ActionResult(False, f"Restore refused: {exc}. No target was changed.",
                                        details=head)
                old_ca = _read_regular(root / _CLIENT_CA)
                try:
                    done = sb.apply(root, snap, archive, now, only_pki)
                except sb.ApplyFailed as exc:
                    if exc.current is None and not exc.done:
                        return ActionResult(False, f"Restore refused: {exc.reason}. No target was "
                                            "changed.", details=head)
                    partial = (f"{exc.current} is partial" if exc.current is not None
                               else "the next target was not touched")
                    return ActionResult(False, f"Restore STOPPED part-way: {exc.reason}. This box is "
                                        f"now MIXED: restored {', '.join(exc.done) or '(nothing)'}; "
                                        f"{partial}; the others are this box's own. "
                                        "There is no automatic undo: restore the backup you made "
                                        "of this box before.", details=head)
        except Exception as exc:
            busy = self._secrets_busy(exc)
            if busy is None:
                raise
            busy.details = head + busy.details
            return busy
        ca_changed = old_ca is not None and _cert_der(old_ca) != _cert_der(sb.member_bytes(snap, _CLIENT_CA))
        return self._secrets_restored(done, now, only_pki, head, ca_changed)

    def _secrets_restored(self, done, plan, only_pki, head, ca_changed) -> ActionResult:
        from . import secrets_backup as sb
        nxt, details = [], [*head, "", f"  restored: {', '.join(done) or '(nothing)'}"]
        other_box = plan["hostname"] != socket.gethostname()
        if only_pki:
            nxt += ["lhpc webserver tls-renew", "lhpc webserver apply"]
            if ca_changed:                   # the box had another client CA: its certificates end
                details.append("  Client certificates signed by this box's previous client CA stop "
                               "working; issue new ones or install the restored ones.")
        elif sb.TLS in done:
            if other_box:
                nxt += ["lhpc webserver configure --dns … --ip …", "lhpc webserver tls-renew"]
            nxt.append("lhpc webserver apply")
        nxt.append(_CONSOLE_START)
        return ActionResult(True, "Secrets restored.", details=details, next_commands=nxt,
                            data={"restored": done})
