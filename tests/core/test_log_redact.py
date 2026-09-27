"""P1.14: the MeshCore login secret is redacted from LHPC's own start logs before the node starts.

Test payloads are FAKE hex, never a real login."""
import importlib.util
import os
from pathlib import Path

from lhpc.core import log_redact, runtime_fs
from lhpc.core.paths import Paths
from repo_paths import REPO as REPO_ROOT

FAKE = "00112233" + "746573742d6f6e6c79" + "00"            # timestamp + "test-only" + NUL


def _log(tmp_path, name, body, mode=0o640):
    d = tmp_path / "logs"; d.mkdir(exist_ok=True)
    p = d / name; p.write_bytes(body); os.chmod(p, mode)
    return p


def _body():
    return (b"2026-09-27 10:00:00 INFO RepeaterDaemon: advert sent\n"
            b"2026-09-27 10:00:01 INFO RepeaterDaemon: [LoginServer] Plaintext hex: " + FAKE.encode() + b"\n"
            b"2026-09-27 10:00:01 INFO RepeaterDaemon: [LoginServer] Plaintext length: 14 bytes\n"
            b"2026-09-27 10:00:02 INFO RepeaterDaemon: [LoginServer] Password hex: 746573742d6f6e6c79\n"
            b"\xff not utf-8 stays as it is\n")


def test_existing_payloads_are_redacted_and_everything_else_is_kept(tmp_path):
    paths = Paths(runtime_root=tmp_path)
    a = _log(tmp_path, "start-meshcore-node.log", _body())
    b = _log(tmp_path, "start-meshcore-node-433.log", _body(), mode=0o600)
    other = _log(tmp_path, "start-meshtastic.log", _body())
    assert log_redact.scrub_component_logs(paths, tmp_path / "logs", "meshcore-node") == []
    for p, mode in ((a, 0o640), (b, 0o600)):
        data = p.read_bytes()
        assert FAKE.encode() not in data and b"746573742d6f6e6c79" not in data
        assert data.count(b"<redacted by LHPC>") == 2
        assert b"advert sent" in data and b"Plaintext length: 14 bytes" in data
        assert b"\xff not utf-8 stays as it is\n" in data                # byte-exact elsewhere
        assert (p.stat().st_mode & 0o777) == mode                         # mode kept
    assert other.read_bytes() == _body()                                  # only this component


def test_the_console_s_log_view_shows_no_payload_after_the_scrub(tmp_path):
    paths = Paths(runtime_root=tmp_path)
    p = _log(tmp_path, "start-meshcore-node.log", _body())
    log_redact.scrub_component_logs(paths, tmp_path / "logs", "meshcore-node")
    shown = "\n".join(runtime_fs.tail(paths, p, 300))        # what log_tail renders
    assert FAKE not in shown and "<redacted by LHPC>" in shown


def test_the_scrub_is_idempotent(tmp_path):
    paths = Paths(runtime_root=tmp_path)
    p = _log(tmp_path, "start-meshcore-node.log", _body())
    log_redact.scrub_component_logs(paths, tmp_path / "logs", "meshcore-node")
    once, ino = p.read_bytes(), p.stat().st_ino
    log_redact.scrub_component_logs(paths, tmp_path / "logs", "meshcore-node")
    assert p.read_bytes() == once and p.stat().st_ino == ino             # nothing rewritten


def test_a_symlinked_log_is_left_alone(tmp_path):
    paths = Paths(runtime_root=tmp_path)
    outside = tmp_path / "elsewhere.log"; outside.write_bytes(_body())
    (tmp_path / "logs").mkdir()
    (tmp_path / "logs" / "start-meshcore-node.log").symlink_to(outside)
    log_redact.scrub_component_logs(paths, tmp_path / "logs", "meshcore-node")
    assert outside.read_bytes() == _body()


def test_an_error_is_a_warning_never_an_exception(tmp_path, monkeypatch):
    paths = Paths(runtime_root=tmp_path)
    _log(tmp_path, "start-meshcore-node.log", _body())
    monkeypatch.setattr(runtime_fs, "rewrite_lines_atomic",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")))
    warnings = log_redact.scrub_component_logs(paths, tmp_path / "logs", "meshcore-node")
    assert len(warnings) == 1 and "disk full" in warnings[0]


def test_the_controller_and_the_node_redact_the_same_prefixes():
    spec = importlib.util.spec_from_file_location(
        "lr", Path(REPO_ROOT) / "lhpc/data/meshcore_host/meshcore_host/login_redact.py")
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    assert tuple(log_redact.PREFIXES["meshcore-node"]) == tuple(mod.PREFIXES)
    assert log_redact.REDACTED == mod.REDACTED


def test_a_start_scrubs_the_log_before_the_process_opens_it(tmp_path):
    # The scrub runs in the launch, BEFORE the spawn opens the log (a rename under an open
    # append descriptor would lose the new output).
    from lhpc.core.config import Config
    from lhpc.core.lifecycle import Lifecycle
    from lhpc.core.model import Component, ComponentKind, Stack
    from lhpc.core.probes.backends import FakeSystem
    p = _log(tmp_path, "start-meshcore-node.log", _body())
    seen = []

    def spawn(argv, log, cwd=None, env=None):
        seen.append(Path(log).read_bytes())
        raise OSError("stop here")
    life = Lifecycle(Paths(runtime_root=tmp_path), (), Config(), FakeSystem().system, spawn=spawn)
    comp = Component(id="meshcore-node", name="n", kind=ComponentKind.SERVICE, run_argv=("true",))
    res = life.start(Stack(id="meshcore", name="m", main="meshcore-node"), comp)
    assert not res.ok and seen and FAKE.encode() not in seen[0]
    assert b"<redacted by LHPC>" in p.read_bytes()


def test_a_failing_scrub_never_blocks_the_start(tmp_path, monkeypatch):
    from lhpc.core.config import Config
    from lhpc.core.lifecycle import Lifecycle
    from lhpc.core.model import Component, ComponentKind, Stack
    from lhpc.core.probes.backends import FakeSystem
    _log(tmp_path, "start-meshcore-node.log", _body())
    monkeypatch.setattr(log_redact, "scrub_component_logs",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    spawned = []
    life = Lifecycle(Paths(runtime_root=tmp_path), (), Config(), FakeSystem().system,
                     spawn=lambda argv, log, cwd=None, env=None: spawned.append(1) or None)
    comp = Component(id="meshcore-node", name="n", kind=ComponentKind.SERVICE, run_argv=("true",))
    life.start(Stack(id="meshcore", name="m", main="meshcore-node"), comp)
    assert spawned == [1]                                   # the spawn happened anyway



# --- audit P1.14 round 1: bounded memory, whatever the size of a never-rotated start log ----------

def test_the_scrub_streams_and_never_loads_the_whole_log(tmp_path, monkeypatch):
    # A start log is never rotated. The scrub must not read it whole (read_bytes / splitlines): a
    # 256 MiB log on a 512 MiB Pi Zero 2 W would OOM. Forbid the whole-file read and prove it works.
    paths = Paths(runtime_root=tmp_path)
    p = _log(tmp_path, "start-meshcore-node.log", b"x" * 100 + b"\n" + _body() * 50)
    monkeypatch.setattr(runtime_fs, "read_bytes",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("whole-file read")))
    assert log_redact.scrub_component_logs(paths, tmp_path / "logs", "meshcore-node") == []
    data = p.read_bytes()
    assert FAKE.encode() not in data and data.count(b"<redacted by LHPC>") == 100


def test_the_rewrite_holds_at_most_one_piece_in_memory(tmp_path):
    # rewrite_lines_atomic hands the transform pieces of at most line_limit bytes, whatever the
    # file size: the memory bound the audit asked for.
    paths = Paths(runtime_root=tmp_path)
    p = _log(tmp_path, "big.log", (b"y" * 5000 + b"\n") * 40)
    seen = []
    changed = runtime_fs.rewrite_lines_atomic(paths, p, lambda piece: seen.append(len(piece)) or piece,
                                              line_limit=1024)
    assert not changed and max(seen) <= 1024 and sum(seen) == p.stat().st_size


def test_a_redacted_line_longer_than_a_piece_loses_all_of_its_payload(tmp_path):
    paths = Paths(runtime_root=tmp_path)
    secret = b"ab" * 3000                                   # a long FAKE payload, split into pieces
    p = _log(tmp_path, "start-meshcore-node.log",
             b"INFO RepeaterDaemon: [LoginServer] Plaintext hex: " + secret + b"\nnext line\n")
    red = log_redact._Redactor(tuple(x.encode() for x in log_redact.PREFIXES["meshcore-node"]))
    runtime_fs.rewrite_lines_atomic(paths, p, red, line_limit=1024)
    assert p.read_bytes() == (b"INFO RepeaterDaemon: [LoginServer] Plaintext hex: <redacted by LHPC>\n"
                              b"next line\n")


def test_a_secret_line_without_a_final_newline_is_redacted(tmp_path):
    paths = Paths(runtime_root=tmp_path)
    p = _log(tmp_path, "start-meshcore-node.log",
             b"first\nINFO RepeaterDaemon: [LoginServer] Plaintext hex: " + FAKE.encode())   # no \n
    log_redact.scrub_component_logs(paths, tmp_path / "logs", "meshcore-node")
    assert p.read_bytes() == b"first\nINFO RepeaterDaemon: [LoginServer] Plaintext hex: <redacted by LHPC>"


def _temps_created(monkeypatch):
    made, real = [], os.open

    def spy(name, flags, *a, **k):
        if isinstance(name, str) and ".tmp-" in name and flags & os.O_CREAT:
            made.append(name)
        return real(name, flags, *a, **k)
    monkeypatch.setattr(runtime_fs.os, "open", spy)
    return made


def test_a_clean_log_is_read_but_never_copied(tmp_path, monkeypatch):
    # P1.22 (audit P1.14 round 2, the note): the clean case used to write a full temp copy at every
    # start and delete it again.
    paths = Paths(runtime_root=tmp_path)
    p = _log(tmp_path, "start-meshcore-node.log", b"2026-09-27 INFO RepeaterDaemon: advert sent\n" * 1000)
    made = _temps_created(monkeypatch)
    assert log_redact.scrub_component_logs(paths, tmp_path / "logs", "meshcore-node") == []
    assert made == [] and p.read_bytes().count(b"advert sent") == 1000


def test_an_already_scrubbed_log_is_not_copied_again(tmp_path, monkeypatch):
    # A redacted line still holds the prefix; a plain "prefix present?" scan would copy every time.
    paths = Paths(runtime_root=tmp_path)
    p = _log(tmp_path, "start-meshcore-node.log", _body())
    log_redact.scrub_component_logs(paths, tmp_path / "logs", "meshcore-node")
    once = p.read_bytes()
    made = _temps_created(monkeypatch)
    log_redact.scrub_component_logs(paths, tmp_path / "logs", "meshcore-node")
    assert made == [] and p.read_bytes() == once


def test_a_dirty_log_is_still_rewritten_with_the_probe(tmp_path, monkeypatch):
    paths = Paths(runtime_root=tmp_path)
    p = _log(tmp_path, "start-meshcore-node.log", b"x\n" * 500 + _body())
    made = _temps_created(monkeypatch)
    log_redact.scrub_component_logs(paths, tmp_path / "logs", "meshcore-node")
    assert len(made) == 1 and FAKE.encode() not in p.read_bytes()
    assert p.read_bytes().startswith(b"x\n" * 500)                    # the rewrite read from offset 0
