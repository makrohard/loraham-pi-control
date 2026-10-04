"""A5: `lhpc secrets backup` / `restore` — one plain tar of config/tls, config/secrets,
config/secrets.toml and every declared state root; the restore checks the whole file on a private
snapshot, prints OVERWRITTEN / CREATED / LEFT AS IT IS, and applies with --yes (nothing exists)
or --overwrite, under the locks, after classifying the box again."""
from __future__ import annotations

import contextlib
import gzip
import hashlib
import io
import json
import os
import stat
import tarfile
from pathlib import Path

import pytest

from lhpc.core import config, reslock, selfupdate
from lhpc.core import secrets_backup as sb
from lhpc.core import service_secrets
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService

PROBE = b"SECRET-PROBE-7f3a"


def _root(tmp_path: Path, name: str) -> Path:
    r = tmp_path / name
    for d in ("config/secrets", "config/stacks", "state"):
        (r / d).mkdir(parents=True, exist_ok=True)
    return r


def _svc(root: Path) -> ControllerService:
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=root))
    svc._SELF_LOCK_WAIT_S = 0.1                   # a held admission fails fast in the tests
    return svc


def _seed(root: Path, *, pki: bool = True) -> ControllerService:
    """A box: a PKI (optional), two secrets, secrets.toml, graywolf's state with a nested empty
    0700 folder, meshtasticd's state with a 0755 folder."""
    svc = _svc(root)
    if pki:
        assert svc.webserver_init(dns_sans=["box.invalid"], ip_sans=[]).ok
    (root / "config/secrets/xr_pw").write_bytes(PROBE)
    os.chmod(root / "config/secrets/xr_pw", 0o600)
    (root / "config/secrets/web_session.key").write_bytes(b"k" * 48)
    (root / "config/secrets.toml").write_text("[x]\n")
    (root / "state/graywolf/sub/empty").mkdir(parents=True)
    os.chmod(root / "state/graywolf/sub/empty", 0o700)
    (root / "state/graywolf/graywolf.db").write_bytes(b"db" * 5000)
    (root / "state/meshtasticd/prefs").mkdir(parents=True)
    os.chmod(root / "state/meshtasticd/prefs", 0o755)
    (root / "state/meshtasticd/prefs/config.proto").write_bytes(b"\x01\x02")
    return svc


def _tree(root: Path) -> dict:
    """{relative name: (kind, mode, bytes)} of everything under the restore targets."""
    out = {}
    roots = _svc(root)._secrets_state_roots() if root.exists() else []
    for top in ("config/tls", "config/secrets", "config/secrets.toml", *roots):
        base = root / top
        if not os.path.lexists(base):
            continue
        items = [base] + (sorted(base.rglob("*")) if base.is_dir() else [])
        for p in items:
            st = p.lstat()
            rel = str(p.relative_to(root))
            out[rel] = (("dir", stat.S_IMODE(st.st_mode), b"") if p.is_dir()
                        else ("file", stat.S_IMODE(st.st_mode), p.read_bytes()))
    return out


def _backup(svc, out: Path):
    r = svc.secrets_backup(str(out))
    assert r.ok, r.summary
    return r


def _names(tar_path: Path) -> list:
    with tarfile.open(tar_path, "r:") as t:
        return [m.name for m in t.getmembers()]


# ---- backup ---------------------------------------------------------------------------------

def test_backup_holds_exactly_the_members_with_modes_manifest_last(tmp_path):
    a = _root(tmp_path, "a")
    svc = _seed(a)
    out = tmp_path / "out.tar"
    _backup(svc, out)
    names = _names(out)
    assert names[-1] == sb.MANIFEST and names.count(sb.MANIFEST) == 1
    want = {n for n in _tree(a)}
    assert set(names[:-1]) == want
    with tarfile.open(out, "r:") as t:
        man = json.loads(t.extractfile(sb.MANIFEST).read())
        members = {m.name: m for m in t.getmembers()[:-1]}
    assert [e["name"] for e in man["members"]] == names[:-1]            # one-to-one, in order
    tree = _tree(a)
    for e in man["members"]:
        kind, mode, data = tree[e["name"]]
        assert (e["type"], e["mode"]) == (kind, mode) and members[e["name"]].mode == mode
        if kind == "file":
            assert e["size"] == len(data) and e["sha256"] == hashlib.sha256(data).hexdigest()
    assert "state/reticulum" in man["absent"] and "config/tls" not in man["absent"]
    assert members["state/graywolf/sub/empty"].isdir()


def test_backup_file_is_0600_and_never_replaces_a_file_or_a_symlink(tmp_path):
    a = _root(tmp_path, "a")
    svc = _seed(a)
    out = tmp_path / "out.tar"
    _backup(svc, out)
    assert stat.S_IMODE(out.stat().st_mode) == 0o600
    before = out.read_bytes()
    r = svc.secrets_backup(str(out))
    assert not r.ok and "already exists" in r.summary and out.read_bytes() == before
    victim = tmp_path / "victim"
    victim.write_text("keep")
    (tmp_path / "link.tar").symlink_to(victim)
    r = svc.secrets_backup(str(tmp_path / "link.tar"))
    assert not r.ok and victim.read_text() == "keep"


def test_backup_refuses_a_folder_inside_the_runtime_root_also_through_a_symlink(tmp_path):
    a = _root(tmp_path, "a")
    svc = _seed(a)
    r = svc.secrets_backup(str(a / "state" / "b.tar"))
    assert not r.ok and "inside the runtime root" in r.summary
    (tmp_path / "sneaky").symlink_to(a / "state")
    r = svc.secrets_backup(str(tmp_path / "sneaky" / "b.tar"))
    assert not r.ok and "inside the runtime root" in r.summary
    assert not (a / "state" / "b.tar").exists()


def test_a_folder_swapped_after_it_was_opened_still_gets_the_file_where_the_descriptor_points(
        tmp_path, monkeypatch):
    a = _root(tmp_path, "a")
    svc = _seed(a)
    (tmp_path / "out").mkdir()
    (tmp_path / "elsewhere").mkdir()
    real = sb.inventory

    def swap(*args, **kw):             # after the folder is opened, before the file is created
        (tmp_path / "out").rename(tmp_path / "moved")
        (tmp_path / "out").symlink_to(tmp_path / "elsewhere")
        return real(*args, **kw)
    monkeypatch.setattr(sb, "inventory", swap)
    assert svc.secrets_backup(str(tmp_path / "out" / "b.tar")).ok
    assert (tmp_path / "moved" / "b.tar").is_file()
    assert not (tmp_path / "elsewhere" / "b.tar").exists()


def test_backup_is_refused_while_an_included_stack_runs(tmp_path, monkeypatch):
    a = _root(tmp_path, "a")
    svc = _seed(a)
    monkeypatch.setattr(type(svc), "stack_running", lambda self, s: s == "graywolf")
    r = svc.secrets_backup(str(tmp_path / "b.tar"))
    assert not r.ok and "lhpc stack stop graywolf" in r.next_commands
    assert not (tmp_path / "b.tar").exists()


@pytest.mark.parametrize("held", ["admission", "config", "pki"])
def test_backup_is_refused_while_a_lock_is_held(tmp_path, held):
    a = _root(tmp_path, "a")
    svc = _seed(a)
    p = Paths(runtime_root=a)
    cm = {"admission": lambda: reslock.operation_lock(p, ControllerService.ADMISSION_KEY, "x"),
          "config": lambda: config.config_lock(p, timeout=0),
          "pki": lambda: reslock.operation_lock(p, "pki", "x")}[held]
    with cm():
        r = svc.secrets_backup(str(tmp_path / "b.tar"))
    assert not r.ok and not (tmp_path / "b.tar").exists()


def _assert_busy_refusal(seen, other):
    """The one start attempted inside was refused on the held task admission; the same start
    afterwards is not (it fails later, on graywolf's missing callsign)."""
    assert len(seen) == 1 and not seen[0].ok
    assert ControllerService.ADMISSION_KEY in seen[0].summary, seen[0].summary
    assert ControllerService.ADMISSION_KEY not in other.start("graywolf", apply=True).summary


def test_a_start_attempted_during_the_backup_is_refused(tmp_path, monkeypatch):
    a = _root(tmp_path, "a")
    svc = _seed(a)
    other = _svc(a)
    seen = []
    real = sb.write_backup

    def during(*args, **kw):
        seen.append(other.start("graywolf", apply=True))
        return real(*args, **kw)
    monkeypatch.setattr(sb, "write_backup", during)
    assert svc.secrets_backup(str(tmp_path / "b.tar")).ok
    _assert_busy_refusal(seen, other)


def test_a_symlink_or_fifo_among_the_members_is_refused(tmp_path):
    a = _root(tmp_path, "a")
    svc = _seed(a)
    (a / "state/graywolf/link").symlink_to(a / "config/secrets/xr_pw")
    r = svc.secrets_backup(str(tmp_path / "b.tar"))
    assert not r.ok and "symlink or a special file" in r.summary
    (a / "state/graywolf/link").unlink()
    os.mkfifo(a / "state/graywolf/fifo")
    assert not svc.secrets_backup(str(tmp_path / "b.tar")).ok
    assert not (tmp_path / "b.tar").exists()


def test_low_disk_is_refused_and_a_failure_part_way_leaves_no_file(tmp_path, monkeypatch):
    a = _root(tmp_path, "a")
    svc = _seed(a)
    real = os.fstatvfs

    class _Full:
        f_bavail, f_frsize = 1, 512
    monkeypatch.setattr(os, "fstatvfs", lambda fd: _Full())
    r = svc.secrets_backup(str(tmp_path / "b.tar"))
    assert not r.ok and "free space" in r.summary and not (tmp_path / "b.tar").exists()
    monkeypatch.setattr(os, "fstatvfs", real)

    def boom(*args, **kw):
        raise OSError(5, "I/O error")
    monkeypatch.setattr(sb, "recheck", boom)
    r = svc.secrets_backup(str(tmp_path / "b.tar"))
    assert not r.ok and "No file was left behind" in r.summary and not (tmp_path / "b.tar").exists()


def test_a_member_changed_after_it_was_streamed_is_caught_by_the_recheck(tmp_path, monkeypatch):
    a = _root(tmp_path, "a")
    svc = _seed(a)
    real = sb.recheck

    def change(root, members):
        (a / "config/secrets/xr_pw").write_bytes(PROBE[:-1] + b"X")        # same size
        return real(root, members)
    monkeypatch.setattr(sb, "recheck", change)
    r = svc.secrets_backup(str(tmp_path / "b.tar"))
    assert not r.ok and "changed during the backup" in r.summary
    assert not (tmp_path / "b.tar").exists()


def test_the_output_holds_no_member_content_or_digest_and_names_the_passphrases(tmp_path):
    a = _root(tmp_path, "a")
    svc = _seed(a)
    r = _backup(svc, tmp_path / "b.tar")
    text = "\n".join([r.summary, *r.details, *r.next_commands])
    assert PROBE.decode() not in text and hashlib.sha256(PROBE).hexdigest() not in text
    assert "in clear" in text
    assert "passphrases are not in this file" in text  # no passphrase in the backup
    assert "lhpc webserver cert reissue" in text


def test_a_hard_linked_pair_becomes_two_regular_members_and_restores(tmp_path):
    a = _root(tmp_path, "a")
    svc = _seed(a)
    os.link(a / "state/graywolf/graywolf.db", a / "state/graywolf/twin.db")
    out = tmp_path / "b.tar"
    _backup(svc, out)
    with tarfile.open(out, "r:") as t:
        types = {m.name: m.type for m in t.getmembers()}
    assert types["state/graywolf/twin.db"] == types["state/graywolf/graywolf.db"] == tarfile.REGTYPE
    b = _root(tmp_path, "b")
    assert _svc(b).secrets_restore(str(out), choice="overwrite").ok
    assert (b / "state/graywolf/twin.db").read_bytes() == (a / "state/graywolf/twin.db").read_bytes()


def test_a_root_without_config_tls_lists_it_absent_and_restores_so(tmp_path):
    a = _root(tmp_path, "a")
    svc = _seed(a, pki=False)
    out = tmp_path / "b.tar"
    _backup(svc, out)
    with tarfile.open(out, "r:") as t:
        assert "config/tls" in json.loads(t.extractfile(sb.MANIFEST).read())["absent"]
    b = _root(tmp_path, "b")
    assert _svc(b).secrets_restore(str(out), choice="overwrite").ok
    assert not (b / "config/tls").exists()


# ---- the console's session key ------------------------------------------------------------

def test_the_session_key_write_takes_the_config_lock_and_is_ephemeral_while_it_is_busy(tmp_path):
    a = _root(tmp_path, "a")
    p = Paths(runtime_root=a)
    key = a / "config/secrets/web_session.key"
    with config.config_lock(p, timeout=0):
        s = config.web_session_secret(p)
    assert len(s) >= 32 and not key.exists()                 # busy: this run's key, nothing written
    s2 = config.web_session_secret(p)
    assert key.read_bytes() == s2 and config.web_session_secret(p) == s2


def test_the_session_key_on_an_absent_root_writes_nothing_not_even_the_lock(tmp_path):
    p = Paths(runtime_root=tmp_path / "absent")
    assert len(config.web_session_secret(p)) >= 32
    assert not (tmp_path / "absent").exists()


# ---- restore --------------------------------------------------------------------------------

def test_restore_round_trips_onto_an_empty_root(tmp_path):
    a = _root(tmp_path, "a")
    _backup(_seed(a), tmp_path / "b.tar")
    b = _root(tmp_path, "b")
    (b / "config/secrets").rmdir()
    r = _svc(b).secrets_restore(str(tmp_path / "b.tar"), choice="yes")
    assert r.ok, r.summary
    assert _tree(b) == _tree(a)


def test_overwrite_leaves_each_target_holding_exactly_the_archive(tmp_path):
    a = _root(tmp_path, "a")
    _backup(_seed(a), tmp_path / "b.tar")
    b = _root(tmp_path, "b")
    _seed(b)
    (b / "config/secrets/only-here").write_text("x")
    (b / "state/graywolf/only-here").write_text("x")
    assert _svc(b).secrets_restore(str(tmp_path / "b.tar"), choice="overwrite").ok
    assert _tree(b) == _tree(a)


def test_the_plan_lists_the_three_groups_and_left_ones_stay(tmp_path):
    a = _root(tmp_path, "a")
    svc = _seed(a)
    (a / "config/secrets.toml").unlink()
    _backup(svc, tmp_path / "b.tar")                       # meshtasticd present, reticulum absent
    b = _root(tmp_path, "b")
    (b / "state/reticulum").mkdir()
    (b / "state/reticulum/identity").write_text("mine")
    (b / "config/secrets.toml").write_text("mine")
    before = _tree(b)
    r = _svc(b).secrets_restore(str(tmp_path / "b.tar"))
    text = "\n".join(r.details)
    assert r.ok and "OVERWRITTEN (1): config/secrets" in text
    assert "CREATED (3): config/tls, state/graywolf, state/meshtasticd" in text
    assert "LEFT AS IT IS (2): state/reticulum, config/secrets.toml" in text
    assert "passphrases are not in this file" in text  # no passphrase in the backup
    assert "lhpc webserver cert reissue" in text
    assert _tree(b) == before                                          # no flag: nothing changed
    assert _svc(b).secrets_restore(str(tmp_path / "b.tar"), choice="overwrite").ok
    assert (b / "state/reticulum/identity").read_text() == "mine"
    assert (b / "config/secrets.toml").read_text() == "mine"


def test_yes_refuses_over_an_existing_target_and_names_overwrite(tmp_path):
    a = _root(tmp_path, "a")
    _backup(_seed(a), tmp_path / "b.tar")
    b = _root(tmp_path, "b")
    before = _tree(b)
    r = _svc(b).secrets_restore(str(tmp_path / "b.tar"), choice="yes")
    assert not r.ok and "--overwrite" in r.summary and _tree(b) == before


# ---- restore: the archive checks (pass 1) ------------------------------------------------

def _craft(path: Path, entries, *, manifest=None, absent=("config/tls",), tail=(), mangle=None,
           manifest_name=sb.MANIFEST):
    """A tar of `entries` [(name, kind, mode, data)], then the manifest (built from them unless
    given), then `tail` entries. `mangle(man)` edits the built manifest."""
    def ti(name, kind, mode, size=0):
        t = tarfile.TarInfo(name)
        t.type = {"dir": tarfile.DIRTYPE, "file": tarfile.REGTYPE, "link": tarfile.SYMTYPE,
                  "dev": tarfile.CHRTYPE}[kind]
        t.mode, t.size = mode, size
        if kind == "link":
            t.linkname = "/etc/passwd"
        return t
    man = manifest or {"format": sb.FORMAT, "version": sb.FORMAT_VERSION, "hostname": "h",
                       "members": [{"name": n, "type": k if k in ("dir", "file") else "file", "mode": m,
                                    **({"size": len(d), "sha256": hashlib.sha256(d).hexdigest()}
                                       if k == "file" else {})} for n, k, m, d in entries],
                       "absent": list(absent)}
    if mangle:
        mangle(man)
    raw = json.dumps(man).encode()
    with tarfile.open(path, "w", format=tarfile.PAX_FORMAT) as t:
        for n, k, m, d in entries:
            t.addfile(ti(n, k, m, len(d) if k == "file" else 0), io.BytesIO(d) if k == "file" else None)
        mi = ti(manifest_name, "dir" if manifest_name.endswith("/") else "file", 0o600, len(raw))
        t.addfile(mi, io.BytesIO(raw))
        for n, k, m, d in tail:
            t.addfile(ti(n, k, m, len(d) if k == "file" else 0), io.BytesIO(d) if k == "file" else None)


_OK = [("config/secrets", "dir", 0o700, b""), ("config/secrets/a", "file", 0o600, b"aa")]


@pytest.mark.parametrize("case", [
    "absolute", "dotdot", "dot", "link", "device", "outside", "tls-missing", "tls-file",
    "root-both", "absent-root-with-member", "no-parent", "duplicate", "two-manifests",
    "manifest-not-last", "listed-missing", "unlisted", "changed-byte", "manifest-is-dir",
    "manifest-below-a-root", "undeclared-root", "gzip"])
def test_a_malformed_archive_is_refused_with_nothing_written(tmp_path, case):
    p = tmp_path / "x.tar"
    e = list(_OK)
    kw = {}
    if case == "absolute":
        e.append(("/config/secrets/b", "file", 0o600, b"b"))
    elif case == "dotdot":
        e.append(("config/secrets/../../x", "file", 0o600, b"b"))
    elif case == "dot":
        e.append(("config/secrets/./b", "file", 0o600, b"b"))
    elif case == "link":
        e.append(("config/secrets/l", "link", 0o777, b""))
    elif case == "device":
        e.append(("config/secrets/d", "dev", 0o600, b""))
    elif case == "outside":
        e.append(("config/local.toml", "file", 0o600, b"b"))
    elif case == "tls-missing":
        kw["absent"] = ()
    elif case == "tls-file":
        kw["absent"] = ()
        e.append(("config/tls", "file", 0o600, b"b"))
    elif case == "root-both":
        e.append(("state/graywolf", "dir", 0o700, b""))
        kw["absent"] = ("config/tls", "state/graywolf")
    elif case == "absent-root-with-member":
        e.append(("state/graywolf/db", "file", 0o600, b"b"))
        kw["absent"] = ("config/tls", "state/graywolf")
    elif case == "no-parent":
        e.append(("config/secrets/sub/b", "file", 0o600, b"b"))
    elif case == "duplicate":
        e.append(("config/secrets/a", "file", 0o600, b"aa"))
    elif case == "two-manifests":
        kw["tail"] = [(sb.MANIFEST, "file", 0o600, b"{}")]
    elif case == "manifest-not-last":
        kw["tail"] = [("config/secrets/late", "file", 0o600, b"b")]
    elif case == "listed-missing":
        kw["mangle"] = lambda m: m["members"].append({"name": "config/secrets/gone", "type": "file",
                                                        "mode": 0o600, "size": 1, "sha256": "0" * 64})
    elif case == "unlisted":
        kw["mangle"] = lambda m: m["members"].pop()
    elif case == "changed-byte":
        kw["mangle"] = lambda m: m["members"][1].update(sha256=hashlib.sha256(b"ab").hexdigest())
    elif case == "manifest-is-dir":
        kw["manifest_name"] = sb.MANIFEST + "/"
    elif case == "manifest-below-a-root":
        kw["manifest_name"] = "config/secrets/" + sb.MANIFEST
    elif case == "undeclared-root":
        e.append(("state/not-a-stack", "dir", 0o700, b""))
    _craft(p, e, **kw)
    if case == "gzip":                 # an otherwise valid backup, gzip-wrapped (over 512 bytes)
        valid = tmp_path / "v.tar"
        _craft(valid, e + [("config/secrets/noise", "file", 0o600, os.urandom(4096))])
        p.write_bytes(gzip.compress(valid.read_bytes()))
        assert len(p.read_bytes()) > 512
    b = _root(tmp_path, "b")
    (b / "config/secrets").rmdir()
    before = _tree(b)
    r = _svc(b).secrets_restore(str(p), choice="overwrite")
    assert not r.ok and "No target was changed" in r.summary, r.summary
    if case == "gzip":
        assert "not an uncompressed LHPC secrets backup" in r.summary
    assert _tree(b) == before


def test_a_valid_archive_passes_even_naming_fewer_state_roots(tmp_path):
    p = tmp_path / "x.tar"
    _craft(p, _OK + [("state/graywolf", "dir", 0o700, b"")])      # names only graywolf
    b = _root(tmp_path, "b")
    (b / "state/reticulum").mkdir()
    r = _svc(b).secrets_restore(str(p))
    assert r.ok, r.summary
    assert "state/reticulum" in "\n".join(r.details).split("LEFT AS IT IS")[1]
    assert _svc(b).secrets_restore(str(p), choice="overwrite").ok
    assert (b / "state/reticulum").is_dir() and (b / "config/secrets/a").read_bytes() == b"aa"


def test_restore_refuses_a_symlinked_backup_path(tmp_path):
    a = _root(tmp_path, "a")
    _backup(_seed(a), tmp_path / "b.tar")
    (tmp_path / "l.tar").symlink_to(tmp_path / "b.tar")
    r = _svc(_root(tmp_path, "b")).secrets_restore(str(tmp_path / "l.tar"), choice="overwrite")
    assert not r.ok and "symlink" in r.summary


def test_restore_reads_a_private_snapshot_on_the_runtime_roots_filesystem(tmp_path, monkeypatch):
    a = _root(tmp_path, "a")
    _backup(_seed(a), tmp_path / "b.tar")
    b = _root(tmp_path, "b")
    seen = {}
    real = sb.read_archive

    def spy(snapfd, declared):
        st = os.fstat(snapfd)
        seen.update(mode=stat.S_IMODE(st.st_mode), links=st.st_nlink, dev=st.st_dev, fd=snapfd)
        with open(tmp_path / "b.tar", "r+b") as f:        # the original, changed IN PLACE
            f.seek(-10, os.SEEK_END)
            f.write(b"X" * 10)
        return real(snapfd, declared)
    monkeypatch.setattr(sb, "read_archive", spy)
    r = _svc(b).secrets_restore(str(tmp_path / "b.tar"), choice="overwrite")
    assert r.ok, r.summary
    assert seen["mode"] == 0o600 and seen["links"] == 0 and seen["dev"] == (b / "state").stat().st_dev
    assert _tree(b) == _tree(a)                        # the snapshot's bytes, the ones checked
    with pytest.raises(OSError):
        os.fstat(seen["fd"])                           # closed: the snapshot is gone


def test_a_copy_error_after_the_snapshot_was_made_leaves_no_snapshot_open(tmp_path, monkeypatch):
    a = _root(tmp_path, "a")
    _backup(_seed(a), tmp_path / "b.tar")
    b = _root(tmp_path, "b")
    seen = []

    def fail(fd, data):                    # the snapshot exists, the copy into it fails
        seen.append(fd)
        raise OSError(5, "I/O error")
    monkeypatch.setattr(sb, "_write_all", fail)
    r = _svc(b).secrets_restore(str(tmp_path / "b.tar"), choice="overwrite")
    assert not r.ok and "could not be copied" in r.summary
    with pytest.raises(OSError):
        os.fstat(seen[0])                  # closed: the snapshot is gone


def test_the_snapshot_is_0600_whatever_the_umask(tmp_path, monkeypatch):
    a = _root(tmp_path, "a")
    _backup(_seed(a), tmp_path / "b.tar")
    b = _root(tmp_path, "b")
    modes = []
    real = sb.read_archive

    def spy(snapfd, declared):
        modes.append(stat.S_IMODE(os.fstat(snapfd).st_mode))
        return real(snapfd, declared)
    monkeypatch.setattr(sb, "read_archive", spy)
    old = os.umask(0o277)                  # a umask that strips the owner's write bit
    try:
        assert _svc(b).secrets_restore(str(tmp_path / "b.tar")).ok
    finally:
        os.umask(old)
    assert modes == [0o600]


def test_too_little_space_for_the_snapshot_is_refused_before_it_is_made(tmp_path, monkeypatch):
    a = _root(tmp_path, "a")
    _backup(_seed(a), tmp_path / "b.tar")
    b = _root(tmp_path, "b")

    class _Full:
        f_bavail, f_frsize = 1, 512
    monkeypatch.setattr(os, "fstatvfs", lambda fd: _Full())
    monkeypatch.setattr(sb, "read_archive", lambda *a, **k: pytest.fail("no snapshot may be read"))
    r = _svc(b).secrets_restore(str(tmp_path / "b.tar"), choice="overwrite")
    assert not r.ok and "free space" in r.summary


def test_restore_is_refused_while_the_console_runs_and_names_both_commands(tmp_path):
    a = _root(tmp_path, "a")
    _backup(_seed(a), tmp_path / "b.tar")
    b = _root(tmp_path, "b")
    before = _tree(b)
    with selfupdate.controller_runtime_lock(Paths(runtime_root=b), exclusive=False):
        r = _svc(b).secrets_restore(str(tmp_path / "b.tar"), choice="overwrite")
    assert not r.ok and _tree(b) == before
    assert r.next_commands == ["systemctl --user stop lhpc-web", "systemctl --user start lhpc-web"]
    assert any("The console must be stopped first" in d for d in r.details)


def test_restore_is_refused_while_a_stack_runs(tmp_path, monkeypatch):
    a = _root(tmp_path, "a")
    _backup(_seed(a), tmp_path / "b.tar")
    b = _root(tmp_path, "b")
    before = _tree(b)
    svc = _svc(b)
    monkeypatch.setattr(type(svc), "stack_running", lambda self, s: s == "meshtastic")
    r = svc.secrets_restore(str(tmp_path / "b.tar"), choice="overwrite")
    assert not r.ok and "lhpc stack stop meshtastic" in r.next_commands and _tree(b) == before


def test_a_start_attempted_during_the_restore_is_refused(tmp_path, monkeypatch):
    a = _root(tmp_path, "a")
    _backup(_seed(a), tmp_path / "b.tar")
    b = _root(tmp_path, "b")
    other = _svc(b)
    seen = []
    real = sb.apply

    def during(*args, **kw):
        seen.append(other.start("graywolf", apply=True))
        return real(*args, **kw)
    monkeypatch.setattr(sb, "apply", during)
    assert _svc(b).secrets_restore(str(tmp_path / "b.tar"), choice="overwrite").ok
    _assert_busy_refusal(seen, other)


def test_a_failure_in_pass_2_stops_and_names_the_mixed_state(tmp_path, monkeypatch):
    a = _root(tmp_path, "a")
    _backup(_seed(a), tmp_path / "b.tar")
    b = _root(tmp_path, "b")
    real = sb._write_member_checked

    def fail_in_graywolf(pfd, rel, tar, info):
        if info.name.startswith("state/graywolf/"):
            raise OSError(5, "I/O error")
        return real(pfd, rel, tar, info)
    monkeypatch.setattr(sb, "_write_member_checked", fail_in_graywolf)
    r = _svc(b).secrets_restore(str(tmp_path / "b.tar"), choice="overwrite")
    assert not r.ok and "MIXED" in r.summary and "state/graywolf is partial" in r.summary
    assert "backup you made" in r.summary
    assert not (b / "state/meshtasticd").exists()        # no further target written


# ---- the race: the box changes between the printed plan and the locked apply -------------

def _between_plan_and_apply(monkeypatch, svc, action):
    real = type(svc)._secrets_locked

    def hook(self, op, *, console):
        action()
        return real(self, op, console=console)
    monkeypatch.setattr(type(svc), "_secrets_locked", hook)


@pytest.mark.parametrize("choice", ["yes", "overwrite"])
def test_a_created_target_that_appears_before_the_apply_is_refused(tmp_path, monkeypatch, choice):
    a = _root(tmp_path, "a")
    _backup(_seed(a), tmp_path / "b.tar")
    b = _root(tmp_path, "b")
    (b / "config/secrets").rmdir()
    svc = _svc(b)

    def appear():
        (b / "state/graywolf").mkdir()
        (b / "state/graywolf/theirs").write_text("keep")
    _between_plan_and_apply(monkeypatch, svc, appear)
    r = svc.secrets_restore(str(tmp_path / "b.tar"), choice=choice)
    assert not r.ok and r.summary.startswith("state changed; run restore again")
    assert (b / "state/graywolf/theirs").read_text() == "keep" and not (b / "config/secrets").exists()


def test_a_revocation_between_the_plan_and_the_apply_is_refused(tmp_path, monkeypatch):
    a = _root(tmp_path, "a")
    svc = _seed(a)
    assert svc.webserver_cert_issue("laptop", "one-time-pass").ok
    _backup(svc, tmp_path / "b.tar")
    shown = svc.secrets_restore(str(tmp_path / "b.tar")).data["plan"]
    assert shown["warnings"] == []                              # nothing revoked yet
    _between_plan_and_apply(monkeypatch, svc, lambda: svc.webserver_cert_revoke("laptop"))
    r = svc.secrets_restore(str(tmp_path / "b.tar"), choice="overwrite", expected_plan=shown)
    assert not r.ok and r.summary.startswith("state changed; run restore again")
    now = svc.secrets_restore(str(tmp_path / "b.tar")).data["plan"]
    assert any("revives" in w and "laptop" in w for w in now["warnings"])


def test_another_backup_file_between_the_shown_plan_and_the_apply_is_refused(tmp_path):
    """The terminal flow shows the plan, then applies: a file replaced by another valid backup
    for the same targets meanwhile is refused (the manifest's digest is part of the plan)."""
    a = _root(tmp_path, "a")
    svc = _seed(a)
    _backup(svc, tmp_path / "first.tar")
    (a / "config/secrets/xr_pw").write_bytes(b"other")
    _backup(svc, tmp_path / "second.tar")
    b = _root(tmp_path, "b")
    shown = _svc(b).secrets_restore(str(tmp_path / "first.tar")).data["plan"]
    before = _tree(b)
    r = _svc(b).secrets_restore(str(tmp_path / "second.tar"), choice="overwrite", expected_plan=shown)
    assert not r.ok and r.summary.startswith("state changed; run restore again") and _tree(b) == before


# ---- --only pki, the warnings, the next commands -----------------------------------------

def test_only_pki_changes_exactly_the_two_authorities(tmp_path):
    a = _root(tmp_path, "a")
    _backup(_seed(a), tmp_path / "b.tar")
    b = _root(tmp_path, "b")
    _seed(b)
    others = {p: (p.stat().st_ino, p.read_bytes()) for p in b.rglob("*") if p.is_file()
              and not str(p.relative_to(b)).startswith(("config/tls/server-ca", "config/tls/client-ca"))}
    r = _svc(b).secrets_restore(str(tmp_path / "b.tar"), only_pki=True, choice="overwrite")
    assert r.ok, r.summary
    assert sb.PKI_ONLY
    for ca in sb.PKI_ONLY:
        assert {k: v for k, v in _tree(b).items() if k.startswith(ca)} == \
               {k: v for k, v in _tree(a).items() if k.startswith(ca)}
    assert {p: (p.stat().st_ino, p.read_bytes()) for p in others} == others
    assert r.next_commands[:2] == ["lhpc webserver tls-renew", "lhpc webserver apply"]


def test_only_pki_is_refused_for_a_provisional_or_absent_pki_and_on_a_box_without_one(tmp_path):
    a = _root(tmp_path, "a")
    svc = _seed(a)
    (a / "config/tls/unverified-clock").write_text("")
    _backup(svc, tmp_path / "prov.tar")
    b = _root(tmp_path, "b")
    _seed(b)
    r = _svc(b).secrets_restore(str(tmp_path / "prov.tar"), only_pki=True, choice="overwrite")
    assert not r.ok and "provisional" in r.summary
    c = _root(tmp_path, "c")
    _backup(_seed(c, pki=False), tmp_path / "nopki.tar")
    r = _svc(b).secrets_restore(str(tmp_path / "nopki.tar"), only_pki=True, choice="overwrite")
    assert not r.ok and "no complete PKI" in r.summary
    (a / "config/tls/unverified-clock").unlink()
    _backup(svc, tmp_path / "ok.tar")
    d = _root(tmp_path, "d")
    _seed(d, pki=False)
    r = _svc(d).secrets_restore(str(tmp_path / "ok.tar"), only_pki=True, choice="overwrite")
    assert not r.ok and "lhpc webserver init" in r.summary and not (d / "config/tls").exists()


def test_the_other_hostname_warning_and_the_next_commands(tmp_path, monkeypatch):
    a = _root(tmp_path, "a")
    _backup(_seed(a), tmp_path / "b.tar")
    b = _root(tmp_path, "b")
    monkeypatch.setattr(service_secrets.socket, "gethostname", lambda: "another-box")
    r = _svc(b).secrets_restore(str(tmp_path / "b.tar"))
    assert any("same node identities on air; on another box use --only pki" in d for d in r.details)
    r = _svc(b).secrets_restore(str(tmp_path / "b.tar"), choice="overwrite")
    assert r.ok and r.next_commands == ["lhpc webserver configure --dns … --ip …",
                                        "lhpc webserver tls-renew", "lhpc webserver apply",
                                        "systemctl --user start lhpc-web"]


def test_the_restore_holds_its_locks_only_in_the_fixed_order(tmp_path, monkeypatch):
    """The admission first, then the runtime lock, the config lock, the PKI lock."""
    a = _root(tmp_path, "a")
    _backup(_seed(a), tmp_path / "b.tar")
    b = _root(tmp_path, "b")
    svc = _svc(b)
    order = []
    real_rt, real_cfg = selfupdate.controller_runtime_lock, config.config_lock
    real_admit, real_pki = type(svc)._admit, type(svc)._pki_lock
    monkeypatch.setattr(type(svc), "_admit", lambda self, st, op, t="": (order.append("admission"),
                                                                        real_admit(self, st, op, t))[1])

    @contextlib.contextmanager
    def rt(p, *, exclusive):
        order.append("runtime")
        with real_rt(p, exclusive=exclusive):
            yield
    monkeypatch.setattr(selfupdate, "controller_runtime_lock", rt)
    monkeypatch.setattr(config, "config_lock",
                        lambda p, timeout=15.0: (order.append("config"), real_cfg(p, timeout))[1])
    monkeypatch.setattr(type(svc), "_pki_lock", lambda self, op, t="": (order.append("pki"),
                                                                        real_pki(self, op, t))[1])
    assert svc.secrets_restore(str(tmp_path / "b.tar"), choice="overwrite").ok
    held = order[order.index("admission"):]           # before it: the plan's console probe only
    assert held == ["admission", "runtime", "config", "pki"], order


# ---- gate 2 of release 6: the five restore/backup findings --------------------------------

def _outside_tree(p: Path) -> dict:
    return {str(q.relative_to(p)): (q.read_bytes() if q.is_file() else None) for q in sorted(p.rglob("*"))}


@pytest.mark.parametrize("case", ["only-pki through a symlinked config/tls", "full through a symlinked config"])
def test_a_restore_never_writes_through_a_symlinked_ancestor(tmp_path, case):
    a = _root(tmp_path, "a")
    _backup(_seed(a), tmp_path / "b.tar")
    b = _root(tmp_path, "b")
    _seed(b)
    outside = tmp_path / "outside"
    outside.mkdir()
    moved = "config/tls" if case.startswith("only-pki") else "config"
    (b / moved).rename(outside / "moved")
    (b / moved).symlink_to(outside / "moved")
    before = _outside_tree(outside)
    r = _svc(b).secrets_restore(str(tmp_path / "b.tar"), only_pki=case.startswith("only-pki"),
                                choice="overwrite")
    assert not r.ok and "symlink" in r.summary and "No target was changed" in r.summary, r.summary
    assert _outside_tree(outside) == before and (b / moved).is_symlink()


def test_an_overwrite_under_a_restrictive_umask_restores_every_folder(tmp_path):
    a = _root(tmp_path, "a")
    _backup(_seed(a), tmp_path / "b.tar")
    b = _root(tmp_path, "b")
    _seed(b)
    assert _svc(b).secrets_restore(str(tmp_path / "b.tar"), choice="overwrite").ok   # the lock files exist
    (b / "state/graywolf/only-here").write_text("x")
    old = os.umask(0o277)                  # mkdir(0o700) would give 0o500: no writes below it
    try:
        r = _svc(b).secrets_restore(str(tmp_path / "b.tar"), choice="overwrite")
    finally:
        os.umask(old)
    assert r.ok, r.summary
    assert _tree(b) == _tree(a)


@pytest.mark.parametrize("rel, make", [
    ("state/graywolf", "file"), ("config/tls", "file"), ("config/secrets.toml", "folder")])
def test_a_backup_refuses_a_target_of_the_wrong_type(tmp_path, rel, make):
    """What a restore's pass 1 refuses, the backup refuses first: no file is written."""
    a = _root(tmp_path, "a")
    svc = _seed(a, pki=False)
    p = a / rel
    if p.is_dir():
        import shutil
        shutil.rmtree(p)
    elif p.exists():
        p.unlink()
    if make == "file":
        p.write_text("x")
    else:
        p.mkdir()
    r = svc.secrets_backup(str(tmp_path / "b.tar"))
    assert not r.ok and "a restore would refuse this backup" in r.summary, r.summary
    assert not (tmp_path / "b.tar").exists()


def test_a_failed_fchmod_after_creating_the_backup_file_leaves_no_file_and_no_descriptor(tmp_path, monkeypatch):
    a = _root(tmp_path, "a")
    svc = _seed(a)
    fds = set(os.listdir("/proc/self/fd"))

    real = os.fchmod

    def fail(fd, mode):                    # only the backup file's fchmod fails
        if os.readlink(f"/proc/self/fd/{fd}").endswith("b.tar"):
            raise OSError(1, "Operation not permitted")
        return real(fd, mode)
    monkeypatch.setattr(os, "fchmod", fail)
    r = svc.secrets_backup(str(tmp_path / "b.tar"))
    monkeypatch.undo()
    assert not r.ok and not (tmp_path / "b.tar").exists()
    assert set(os.listdir("/proc/self/fd")) <= fds


def test_the_client_ca_warning_only_when_the_client_ca_changes(tmp_path):
    a = _root(tmp_path, "a")
    svc_a = _seed(a)
    _backup(svc_a, tmp_path / "b.tar")
    same = svc_a.secrets_restore(str(tmp_path / "b.tar"), only_pki=True, choice="overwrite")
    assert same.ok and not any("previous client CA" in d for d in same.details)
    b = _root(tmp_path, "b")
    _seed(b)                                                    # its own, different client CA
    other = _svc(b).secrets_restore(str(tmp_path / "b.tar"), only_pki=True, choice="overwrite")
    assert other.ok and any("signed by this box's previous client CA stop working" in d
                            for d in other.details)


# ---- the second gate 2 of release 6: three residual findings ------------------------------

def _patch_header_mode(path: Path, name: str, mode: int) -> None:
    """Write `mode` (beyond what tarfile writes) into `name`'s ustar header; fix the checksum."""
    raw = bytearray(path.read_bytes())
    with tarfile.open(path, "r:") as t:
        off = t.getmember(name).offset
    raw[off + 100:off + 108] = b"%07o\0" % mode
    raw[off + 148:off + 156] = b" " * 8
    raw[off + 148:off + 156] = b"%06o\0 " % sum(raw[off:off + 512])
    path.write_bytes(bytes(raw))


@pytest.mark.parametrize("case", ["a NUL in a name", "a mode beyond 0o7777"])
def test_malformed_metadata_is_refused_before_any_target_changes(tmp_path, case):
    a = _root(tmp_path, "a")
    _backup(_seed(a), tmp_path / "good.tar")
    p = tmp_path / "x.tar"
    if case == "a NUL in a name":
        bad = "config/secrets/a\x00b"
        man = {"format": sb.FORMAT, "version": sb.FORMAT_VERSION, "hostname": "h", "absent": ["config/tls"],
               "members": [{"name": "config/secrets", "type": "dir", "mode": 0o700},
                           {"name": bad, "type": "file", "mode": 0o600, "size": 1,
                            "sha256": hashlib.sha256(b"x").hexdigest()}]}
        raw = json.dumps(man).encode()
        with tarfile.open(p, "w", format=tarfile.PAX_FORMAT) as t:
            d = tarfile.TarInfo("config/secrets")
            d.type, d.mode = tarfile.DIRTYPE, 0o700
            t.addfile(d)
            f = tarfile.TarInfo("config/secrets/ab")
            f.size, f.mode, f.pax_headers = 1, 0o600, {"path": bad}
            t.addfile(f, io.BytesIO(b"x"))
            m = tarfile.TarInfo(sb.MANIFEST)
            m.size = len(raw)
            t.addfile(m, io.BytesIO(raw))
    else:
        big = 0o7777777
        _craft(p, [("config/secrets", "dir", 0o700, b""), ("config/secrets/a", "file", 0o600, b"aa")],
               mangle=lambda m: m["members"][1].update(mode=big))
        _patch_header_mode(p, "config/secrets/a", big)
    b = _root(tmp_path, "b")
    _seed(b)
    before = _tree(b)
    r = _svc(b).secrets_restore(str(p), choice="overwrite")
    assert not r.ok and "No target was changed" in r.summary, r.summary
    assert _tree(b) == before


def test_a_symlink_swapped_in_after_the_last_check_cannot_redirect_the_restore(tmp_path, monkeypatch):
    import inspect
    a = _root(tmp_path, "a")
    _backup(_seed(a), tmp_path / "b.tar")
    b = _root(tmp_path, "b")
    _seed(b)
    outside = tmp_path / "outside"
    real = sb.check_ancestors

    def swap_after_the_check(root, target):
        real(root, target)
        # the caller, past the signature check that wraps every fake (tests/conftest.py)
        caller = next(f.function for f in inspect.stack()[1:] if f.function != "fake")
        if caller == "apply" and not outside.exists():
            (b / "config/tls").rename(outside)               # the real folder goes outside…
            (b / "config/tls").symlink_to(outside)           # …and a link takes its place
    monkeypatch.setattr(sb, "check_ancestors", swap_after_the_check)
    r = _svc(b).secrets_restore(str(tmp_path / "b.tar"), only_pki=True, choice="overwrite")
    assert not r.ok and "No target was changed" in r.summary, r.summary
    assert outside.is_dir() and (outside / "server-ca" / "ca.key").is_file()
    assert (outside / "server-ca" / "ca.crt").read_bytes() != (a / "config/tls/server-ca/ca.crt").read_bytes()


def test_a_re_encoded_client_ca_is_the_same_ca(tmp_path):
    a = _root(tmp_path, "a")
    svc = _seed(a)
    _backup(svc, tmp_path / "b.tar")
    ca = a / "config/tls/client-ca/ca.crt"
    ca.write_bytes(ca.read_bytes().replace(b"\n", b"\r\n"))   # the same certificate, other bytes
    r = svc.secrets_restore(str(tmp_path / "b.tar"), only_pki=True, choice="overwrite")
    assert r.ok and not any("previous client CA" in d for d in r.details)


@pytest.mark.needs_nonroot
def test_an_unopenable_controller_lock_is_named_not_reported_busy(tmp_path):
    """Finding 31: a lock file the restore cannot open (here the controller-runtime lock, 0400)
    was reported as "Another operation holds the controller's locks" — a false busy."""
    a = _root(tmp_path, "a")
    _backup(_seed(a), tmp_path / "b.tar")
    b = _root(tmp_path, "b")
    _seed(b)
    before = _tree(b)
    lock = b / "state" / "locks" / "controller-runtime"
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text("")
    lock.chmod(0o400)
    try:
        r = _svc(b).secrets_restore(str(tmp_path / "b.tar"), choice="overwrite")
    finally:
        lock.chmod(0o600)
    assert not r.ok and "lock could not be opened" in r.summary, r.summary
    assert "Another operation" not in r.summary
    assert _tree(b) == before


@pytest.mark.needs_nonroot
def test_a_read_only_lock_folder_is_named_not_a_traceback(tmp_path):
    """Finding 33: with `state/locks/` read-only (0500, no lock files) the task admission, taken
    BEFORE the runtime lock, could not create its lock file and the PermissionError escaped as a
    traceback. It is named like any lock that could not be opened, and nothing is written."""
    a = _root(tmp_path, "a")
    _backup(_seed(a), tmp_path / "b.tar")
    b = _root(tmp_path, "b")
    _seed(b)
    locks = b / "state" / "locks"
    for f in locks.glob("*"):
        f.unlink()
    before = _tree(b)
    locks.chmod(0o500)
    try:
        r = _svc(b).secrets_restore(str(tmp_path / "b.tar"), choice="overwrite")
    finally:
        locks.chmod(0o700)
    assert not r.ok and "lock could not be opened" in r.summary, r.summary
    assert "Another operation" not in r.summary
    assert _tree(b) == before

