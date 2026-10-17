"""The Meshtastic web client as its own component (docs/provenance.md, "The web client"): three
selectors (binary = the manifest pin, stable / dev = a lab-smoked index-v3 entry), the selector kept
by a bare update or rebuild (D3), the pairing with the installed meshtasticd, and the move of the
served root out of the meshtasticd tree. Driven through the service with only the HTTP and
extraction seams stubbed, so the real index parser, selection and record run."""
from __future__ import annotations

import json

import pytest

from lhpc.core import binary_install as bi
from lhpc.core import web_client as wc
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService

DAEMON_A, DAEMON_B = "a" * 40, "b" * 40
_REAL_HTTP_GET = bi._http_get


def _svc(tmp_path, monkeypatch, manifest_path=None):
    monkeypatch.setattr(ControllerService, "binary_target", lambda self: "aarch64-trixie")
    return ControllerService(manifest_path=manifest_path, system=FakeSystem().system,
                             paths=Paths(runtime_root=tmp_path))


def _web(sha, channel, daemon, web_commit="c" * 40, version="2.8.0"):
    filename = f"meshtastic-web-{sha}.tar.zst"
    return {"filename": filename, "url": f"https://example.invalid/{filename}", "sha256": sha,
            "size": 10, "built_from": web_commit,
            "components": {"meshtastic-web": web_commit, "meshtastic": daemon},
            "lhpc_commit": "d" * 40, "builder_commit": "e" * 40, "target": "aarch64-trixie",
            "os": "trixie", "container_digest": "debian@sha256:" + "f" * 64, "runtime_deps": [],
            "smoke": {"mode": "mandatory", "result": "passed"}, "extract_to": "runtime-root",
            "inputs": {}, "channel": channel, "web_version": version,
            "published_at": "2026-10-16T08:00:00Z"}


def _serve(monkeypatch, entries):
    """The index (schema 3) and an extraction that lays down the client the entry names."""
    index = {"schema": 3, "stacks": {"meshtastic-web": entries}}
    monkeypatch.setattr(bi, "_http_get", lambda url, max_bytes: json.dumps(index).encode())
    monkeypatch.setattr(bi, "require_zstd", lambda: None)
    monkeypatch.setattr(bi, "download_artifact", lambda entry, dest: open(dest, "wb").close())

    def extract(tar_path, stage, roots):
        assert roots == (wc.ROOT_REL + "/web",)
        web = __import__("pathlib").Path(stage) / wc.ROOT_REL / "web"
        web.mkdir(parents=True)
        (web / "index.html").write_text(f"client from {tar_path}")
        return [wc.ROOT_REL + "/web/index.html"]
    monkeypatch.setattr(bi, "validate_and_extract", extract)


def _daemon(svc, binary_receipt, commit):
    """meshtasticd installed from a binary whose recorded commit is `commit`."""
    binary_receipt(svc, "meshtastic", commits=dict.fromkeys(svc.binary_spec("meshtastic").covers, commit))


def _binary_client(svc, version):
    """What the `binary` build leaves: the fetch script's files and record, then the controller's."""
    from lhpc.core.lifecycle import BUILD_MARKER_TEXT
    comp = wc.component(svc)
    root = svc._paths.runtime_root
    (root / wc.ROOT_REL / "web").mkdir(parents=True, exist_ok=True)
    (root / wc.ROOT_REL / "web" / "index.html").write_text("pinned client")
    (root / wc.PROVENANCE_REL).write_text(f"web_version={version}\nweb_sha256={'1' * 64}\n"
                                          "pinned=yes\nsource=lhpc-manifest\n")
    wc.record_binary(svc)
    svc.build_inputs_path(comp).write_text(svc.build_inputs_text(comp))
    (root / comp.build_marker).write_text(BUILD_MARKER_TEXT)


# ---- the component -------------------------------------------------------------------------

def test_the_web_client_is_its_own_component_outside_the_meshtasticd_tree(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch)
    st = svc.stack("meshtastic")
    web, daemon = st.component("meshtastic-web"), st.component("meshtastic")
    assert web.optional and not web.run_argv                    # never started with the stack
    assert web.build_marker == wc.ROOT_REL + "/.lhpc-built"     # static: D3
    assert web.build_root == wc.ROOT_REL
    assert wc.pin(web)[0] and len(wc.pin(web)[1]) == 64
    assert not any(wc.SCRIPT in " ".join(s["argv"]) for s in daemon.build_steps)
    assert "meshtastic-web" not in dict(daemon.build_inputs)    # a web move no longer rebuilds it
    root = next(p for p in daemon.config_file.params if p.key == "RootPath")
    assert root.default == "{runtime}/" + wc.ROOT_REL + "/web"
    assert "meshtastic-web" not in svc.binary_spec("meshtastic").covers


# ---- selection -----------------------------------------------------------------------------

def test_select_web_takes_the_first_entry_of_the_channel_paired_with_the_installed_daemon():
    entries = [bi._parse_entry("meshtastic-web", e, v3=True) for e in (
        _web("1" * 64, "dev", DAEMON_A), _web("2" * 64, "stable", DAEMON_B),
        _web("3" * 64, "stable", DAEMON_A), _web("4" * 64, "stable", DAEMON_A))]
    assert bi.select_web(entries, "stable", DAEMON_A).sha256 == "3" * 64
    assert bi.select_web(entries, "dev", DAEMON_A).sha256 == "1" * 64
    assert entries[0].provenance["channel"] == "dev" and entries[0].provenance["web_version"] == "2.8.0"
    with pytest.raises(bi.BinaryInstallError, match="--source binary"):
        bi.select_web(entries, "dev", DAEMON_B)


def test_pinned_is_refused_the_web_client_has_no_compiled_lane(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch)
    res = svc.update("meshtastic-web", apply=True, source="pinned")
    assert not res.ok and res.data["refusal"] == "no_compiled_lane"
    assert "lhpc update meshtastic-web --source binary --yes" in res.next_commands


def test_stable_installs_the_entry_paired_with_the_installed_daemon(tmp_path, monkeypatch, binary_receipt):
    svc = _svc(tmp_path, monkeypatch)
    _daemon(svc, binary_receipt, DAEMON_A)
    _serve(monkeypatch, [_web("5" * 64, "stable", DAEMON_B), _web("6" * 64, "stable", DAEMON_A)])
    res = svc.update("meshtastic-web", apply=True, source="stable")
    assert res.ok, res.summary
    prov = wc.read_provenance(svc._paths)
    assert (prov["selector"], prov["meshtasticd"], prov["web_sha256"]) == ("stable", DAEMON_A, "6" * 64)
    assert (tmp_path / wc.ROOT_REL / "web" / "index.html").is_file()
    assert svc.is_built(wc.component(svc))
    assert "(stable, differs)" in wc.status(svc)


def test_no_paired_entry_is_refused_before_any_download(tmp_path, monkeypatch, binary_receipt):
    svc = _svc(tmp_path, monkeypatch)
    _daemon(svc, binary_receipt, DAEMON_A)
    _serve(monkeypatch, [_web("5" * 64, "stable", DAEMON_B)])
    monkeypatch.setattr(bi, "download_artifact", lambda *a: pytest.fail("must refuse first"))
    res = svc.update("meshtastic-web", apply=True, source="stable")
    assert not res.ok and res.data["refusal"] == "none_paired"
    assert "lhpc update meshtastic-web --source binary --yes" in res.next_commands


@pytest.mark.parametrize("cause, said", [("malformed", "malformed published_at"),
                                         ("unreachable", "download failed"),
                                         ("no-zstd", "zstd is not installed")])
def test_any_other_index_error_is_a_plain_failure_not_the_typed_refusal(tmp_path, monkeypatch, binary_receipt,
                                                                        cause, said):
    """Only "no pair" is the typed `none_paired`; a malformed entry, an unreachable index and a
    missing zstd fail plainly."""
    import urllib.error
    real_zstd = bi.require_zstd
    svc = _svc(tmp_path, monkeypatch)
    _daemon(svc, binary_receipt, DAEMON_A)
    _serve(monkeypatch, [{**_web("5" * 64, "stable", DAEMON_A),
                          **({"published_at": "yesterday"} if cause == "malformed" else {})}])
    if cause == "unreachable":
        class _Down:
            def open(self, req, timeout):
                raise urllib.error.URLError("network unreachable")
        monkeypatch.setattr(bi, "_http_get", _REAL_HTTP_GET)          # the real fetch, its network down
        monkeypatch.setattr(bi.urllib.request, "build_opener", lambda *a: _Down())
    if cause == "no-zstd":
        monkeypatch.setattr(bi, "require_zstd", real_zstd)
        monkeypatch.setattr(bi.shutil, "which", lambda name: None)
    res = svc.update("meshtastic-web", apply=True, source="stable")
    assert not res.ok and res.data == {"binary_failed": True}
    assert said in res.summary


_GONE = object()


@pytest.mark.parametrize("bad", [
    {"web_version": _GONE}, {"web_version": ""}, {"web_version": "2.8.0 beta"},
    {"web_version": "2.8.0\nselector=binary"}, {"web_version": "v" * 65},
    {"channel": _GONE}, {"channel": "nightly"},
    {"components": {"meshtastic": DAEMON_A}},
    {"components": {"meshtastic-web": "c" * 9, "meshtastic": DAEMON_A}},
    {"components": {"meshtastic-web": "c" * 40}},
], ids=["no-version", "empty-version", "space-version", "newline-version", "long-version", "no-channel",
        "other-channel", "no-web-commit", "short-web-commit", "no-daemon-commit"])
@pytest.mark.parametrize("where", ["matching", "later"])
def test_a_web_entry_without_valid_metadata_fails_plainly_before_any_download(
        tmp_path, monkeypatch, binary_receipt, bad, where):
    """The web fields are checked while the index is parsed: a malformed entry, the one that
    would match or a LATER one, refuses the read plainly; nothing is downloaded or recorded."""
    svc = _svc(tmp_path, monkeypatch)
    _daemon(svc, binary_receipt, DAEMON_A)
    broken = {**_web("5" * 64, "stable", DAEMON_A), **bad}
    broken = {k: v for k, v in broken.items() if v is not _GONE}
    _serve(monkeypatch, [broken] if where == "matching" else [_web("6" * 64, "stable", DAEMON_A), broken])
    monkeypatch.setattr(bi, "download_artifact", lambda *a: pytest.fail("must refuse first"))
    res = svc.update("meshtastic-web", apply=True, source="stable")
    assert not res.ok and res.data == {"binary_failed": True}
    assert "lacks a valid channel, web_version or component commit" in res.summary
    assert not (tmp_path / wc.PROVENANCE_REL).exists()


@pytest.mark.parametrize("channel, label", [("stable", "v2.7.2"), ("dev", "nightly-v2.7.2-53ffb84b3c82")])
def test_the_publishers_version_labels_install(tmp_path, monkeypatch, binary_receipt, channel, label):
    """lhpc-binaries publishes upstream's release labels (its lib_index.py: `[A-Za-z0-9.@+_-]{1,64}`);
    the two the live index carries parse, install and are recorded as published."""
    svc = _svc(tmp_path, monkeypatch)
    _daemon(svc, binary_receipt, DAEMON_A)
    _serve(monkeypatch, [_web("6" * 64, channel, DAEMON_A, version=label)])
    assert svc.update("meshtastic-web", apply=True, source=channel).ok
    assert wc.read_provenance(svc._paths)["web_version"] == label


# ---- D3: the choice survives; a pin move changes only the known-working entry ----------------

def test_a_bare_update_keeps_the_recorded_selector(tmp_path, monkeypatch, binary_receipt):
    svc = _svc(tmp_path, monkeypatch)
    _daemon(svc, binary_receipt, DAEMON_A)
    _serve(monkeypatch, [_web("6" * 64, "dev", DAEMON_A)])
    assert svc.update("meshtastic-web", apply=True, source="dev").ok
    assert svc.update_default_channel("meshtastic-web") == ""      # the CLI's bare update
    _serve(monkeypatch, [_web("7" * 64, "dev", DAEMON_A), _web("6" * 64, "dev", DAEMON_A)])
    assert svc.update("meshtastic-web", apply=True, source="").ok
    prov = wc.read_provenance(svc._paths)
    assert (prov["selector"], prov["web_sha256"]) == ("dev", "7" * 64)


def test_a_pin_move_leaves_a_stable_client_built_and_makes_a_binary_one_read_behind(
        tmp_path, monkeypatch, binary_receipt):
    svc = _svc(tmp_path, monkeypatch)
    _daemon(svc, binary_receipt, DAEMON_A)
    pin = wc.pin(wc.component(svc))[0]
    _binary_client(svc, pin)
    assert "(binary, current)" in wc.status(svc)
    _binary_client(svc, "0.0.1")                                    # the pin moved past it
    line = wc.status(svc)
    assert "(binary, behind; pin " + pin in line and svc.is_built(wc.component(svc))
    _serve(monkeypatch, [_web("6" * 64, "stable", DAEMON_A, version="0.0.1")])
    assert svc.update("meshtastic-web", apply=True, source="stable").ok
    assert "(stable, differs)" in wc.status(svc)      # never behind: not the pin's
    assert svc.is_built(wc.component(svc))


def _fetch_svc(tmp_path, monkeypatch, rc):
    """A service whose `binary` fetch step (the web-assets script) returns `rc`; nothing else is
    faked, so the real build loop runs."""
    from lhpc.core.probes.backends import CommandResult

    class _Fetch(dict):
        def get(self, argv, default=None):
            web = len(argv) > 1 and argv[1].endswith("/" + wc.SCRIPT)
            return CommandResult(rc, "", "" if rc == 0 else "curl: (6) could not resolve host") if web else default
    monkeypatch.setattr(ControllerService, "binary_target", lambda self: "aarch64-trixie")
    return ControllerService(system=FakeSystem(commands=_Fetch()).system, paths=Paths(runtime_root=tmp_path))


def test_a_failed_switch_to_binary_keeps_the_stable_client_and_its_record(tmp_path, monkeypatch, binary_receipt):
    """The `binary` choice is recorded only by a build that succeeded: a failed fetch leaves the
    stable client AND its record as they were (never "binary, current" over stable bytes)."""
    svc = _fetch_svc(tmp_path, monkeypatch, rc=1)
    _daemon(svc, binary_receipt, DAEMON_A)
    _serve(monkeypatch, [_web("6" * 64, "stable", DAEMON_A)])
    assert svc.update("meshtastic-web", apply=True, source="stable").ok
    files = (tmp_path / wc.ROOT_REL / "web" / "index.html", tmp_path / wc.PROVENANCE_REL)
    before = {p: p.read_bytes() for p in files}
    res = svc.update("meshtastic-web", apply=True, source="binary")
    assert not res.ok
    assert {p: p.read_bytes() for p in files} == before
    assert "(stable, differs)" in wc.status(svc)


def test_a_switch_to_binary_builds_the_pin_and_records_it_under_the_web_lock(tmp_path, monkeypatch, binary_receipt):
    """With stable recorded, `--source binary` runs the pin's fetch (not the stable re-install), and
    the `binary` record is written while the web lock is still held, so no stable/dev install can
    land between the build and the record."""
    from lhpc.core import reslock
    svc = _fetch_svc(tmp_path, monkeypatch, rc=0)
    _daemon(svc, binary_receipt, DAEMON_A)
    _serve(monkeypatch, [_web("6" * 64, "stable", DAEMON_A)])
    assert svc.update("meshtastic-web", apply=True, source="stable").ok
    monkeypatch.setattr(bi, "download_artifact", lambda *a: pytest.fail("the stable client was fetched again"))
    real, held = wc.record_binary, []

    def record_binary(s):
        try:
            with wc.lock(s._paths):
                held.append(False)
        except reslock.ResourceBusy:
            held.append(True)
        real(s)
    monkeypatch.setattr(wc, "record_binary", record_binary)
    res = svc.update("meshtastic-web", apply=True, source="binary")
    assert res.ok, (res.summary, res.details)
    assert held == [True]
    assert wc.selector(wc.read_provenance(svc._paths)) == "binary"


# ---- the pairing with meshtasticd ------------------------------------------------------------

def test_a_daemon_change_reads_unproven_in_status_and_doctor(tmp_path, monkeypatch, binary_receipt):
    svc = _svc(tmp_path, monkeypatch)
    _daemon(svc, binary_receipt, DAEMON_A)
    _serve(monkeypatch, [_web("6" * 64, "stable", DAEMON_A)])
    assert svc.update("meshtastic-web", apply=True, source="stable").ok
    assert "unproven" not in wc.status(svc)
    _daemon(svc, binary_receipt, DAEMON_B)                          # meshtasticd replaced
    assert "unproven for this meshtasticd (bbbbbbbbb)" in wc.status(svc)
    assert any("unproven" in d for d in svc.doctor().details)
    assert any("unproven" in d for d in svc.status("meshtastic").details)


def test_the_daemon_install_re_pairs_the_kept_selector_in_the_same_run(tmp_path, monkeypatch, binary_receipt):
    svc = _svc(tmp_path, monkeypatch)
    _daemon(svc, binary_receipt, DAEMON_A)
    _serve(monkeypatch, [_web("6" * 64, "stable", DAEMON_A)])
    assert svc.update("meshtastic-web", apply=True, source="stable").ok
    _daemon(svc, binary_receipt, DAEMON_B)
    _serve(monkeypatch, [_web("8" * 64, "stable", DAEMON_B), _web("6" * 64, "stable", DAEMON_A)])
    from lhpc.core.service_base import ActionResult
    res = wc.after_daemon(svc, ActionResult(True, "Installed 'meshtastic' from the published binary."))
    assert res.ok and any("web client" in d for d in res.details)
    prov = wc.read_provenance(svc._paths)
    assert (prov["selector"], prov["meshtasticd"], prov["web_sha256"]) == ("stable", DAEMON_B, "8" * 64)


def test_no_entry_for_the_new_daemon_keeps_the_client_named_unproven(tmp_path, monkeypatch, binary_receipt):
    svc = _svc(tmp_path, monkeypatch)
    _daemon(svc, binary_receipt, DAEMON_A)
    _serve(monkeypatch, [_web("6" * 64, "stable", DAEMON_A)])
    assert svc.update("meshtastic-web", apply=True, source="stable").ok
    _daemon(svc, binary_receipt, DAEMON_B)
    from lhpc.core.service_base import ActionResult
    res = wc.after_daemon(svc, ActionResult(True, "Installed 'meshtastic' from the published binary."))
    assert res.ok and any("[unproven]" in d for d in res.details)
    assert wc.read_provenance(svc._paths)["web_sha256"] == "6" * 64   # the installed client stays
    assert (tmp_path / wc.ROOT_REL / "web" / "index.html").is_file()


# ---- the migration of a saved root -----------------------------------------------------------

def _rendered_root(svc) -> str:
    svc.write_config_files("meshtastic", band="868")
    text = (svc._paths.runtime_root / "config/files/meshtasticd.yaml").read_text()
    return next(ln.split(":", 1)[1].strip() for ln in text.splitlines()
                if ln.strip().startswith("RootPath:"))


@pytest.mark.parametrize("saved", ["{runtime}/build/tools/meshtasticd/web", "RENDERED", None])
def test_a_saved_old_root_serves_from_the_new_default(tmp_path, monkeypatch, saved):
    """Saved settings normally carry no `web_root` (None: the default renders at every start).
    A saved OLD managed root, literal or rendered, follows the move too — whichever way the box
    was upgraded, since the rule runs at every render."""
    from lhpc.core.config import save_stack_config
    svc = _svc(tmp_path, monkeypatch)
    if saved is not None:
        value = str(tmp_path / wc.OLD_ROOT_REL) if saved == "RENDERED" else saved
        save_stack_config(svc._paths, "meshtastic", {"file_web_root": value}, "868")
    assert _rendered_root(svc) == str(tmp_path / wc.ROOT_REL / "web")


def test_an_operators_own_root_is_kept(tmp_path, monkeypatch):
    from lhpc.core.config import save_stack_config
    svc = _svc(tmp_path, monkeypatch)
    save_stack_config(svc._paths, "meshtastic", {"file_web_root": "/srv/my-client"}, "868")
    assert _rendered_root(svc) == "/srv/my-client"


def test_status_says_not_served_when_an_operator_root_selects_another_directory(tmp_path, monkeypatch,
                                                                                binary_receipt):
    from lhpc.core.config import save_stack_config
    svc = _svc(tmp_path, monkeypatch)
    _daemon(svc, binary_receipt, DAEMON_A)
    _binary_client(svc, wc.pin(wc.component(svc))[0])
    assert "not served" not in wc.status(svc)
    save_stack_config(svc._paths, "meshtastic", {"file_web_root": "/srv/my-client"}, "868")
    svc.invalidate_snapshot()
    assert _rendered_root(svc) == "/srv/my-client"
    assert "not served: meshtasticd's RootPath is /srv/my-client" in wc.status(svc)
    save_stack_config(svc._paths, "meshtastic", {"file_web_root": str(tmp_path / wc.OLD_ROOT_REL)}, "868")
    svc.invalidate_snapshot()
    assert "not served" not in wc.status(svc)            # the old managed root follows the move


@pytest.mark.parametrize("where", ["rename", "provenance"])
def test_a_failed_swap_puts_the_previous_client_back(tmp_path, monkeypatch, binary_receipt, where):
    """At the rename, or after it (a sidecar write): the previous client, its record, its
    build-inputs sidecar and its marker are all back, so it still reads built."""
    import pathlib
    svc = _svc(tmp_path, monkeypatch)
    _daemon(svc, binary_receipt, DAEMON_A)
    _binary_client(svc, wc.pin(wc.component(svc))[0])
    comp = wc.component(svc)
    before = {p: p.read_text() for p in (tmp_path / wc.PROVENANCE_REL, svc.build_inputs_path(comp),
                                         tmp_path / comp.build_marker)}
    if where == "rename":
        real = pathlib.Path.rename

        def rename(self, target):
            if self.parts[-1] == "web" and "stage" in self.parts:
                raise OSError("disk full")
            return real(self, target)
        monkeypatch.setattr(pathlib.Path, "rename", rename)
    else:
        def write_provenance(paths, record):
            raise OSError("disk full")
        monkeypatch.setattr(wc, "write_provenance", write_provenance)
    _serve(monkeypatch, [_web("6" * 64, "stable", DAEMON_A)])
    res = svc.update("meshtastic-web", apply=True, source="stable")
    assert not res.ok and "disk full" in res.summary
    assert (tmp_path / wc.ROOT_REL / "web" / "index.html").read_text() == "pinned client"
    assert {p: p.read_text() for p in before} == before
    assert svc.is_built(comp)
    assert list((tmp_path / wc.ROOT_REL).glob(".web-*")) == []      # no leftover swap dir


@pytest.mark.skipif(__import__("os").geteuid() == 0, reason="root reads a mode-000 file")
def test_an_unreadable_sidecar_stops_the_swap_before_anything_moves(tmp_path, monkeypatch, binary_receipt):
    """An existing sidecar that cannot be read is not "absent": the swap never starts, so a later
    failure has nothing to roll back over it (a rollback would have deleted it)."""
    svc = _svc(tmp_path, monkeypatch)
    _daemon(svc, binary_receipt, DAEMON_A)
    _binary_client(svc, wc.pin(wc.component(svc))[0])
    comp = wc.component(svc)
    files = (tmp_path / wc.ROOT_REL / "web" / "index.html", tmp_path / wc.PROVENANCE_REL,
             svc.build_inputs_path(comp), tmp_path / comp.build_marker)
    before = {p: p.read_bytes() for p in files}
    monkeypatch.setattr(wc, "write_provenance", lambda paths, record: pytest.fail("the swap started"))
    _serve(monkeypatch, [_web("6" * 64, "stable", DAEMON_A)])
    (tmp_path / wc.PROVENANCE_REL).chmod(0)
    try:
        res = svc.update("meshtastic-web", apply=True, source="stable")
    finally:
        (tmp_path / wc.PROVENANCE_REL).chmod(0o644)
    assert not res.ok and "Permission denied" in res.summary
    assert {p: p.read_bytes() for p in files} == before
    assert list((tmp_path / wc.ROOT_REL).glob(".web-*")) == []


def test_the_web_install_nests_inside_the_auto_install_boundary(tmp_path, monkeypatch, binary_receipt):
    """Auto-install calls `binary_install(locked=True)` with its boundary held (admission,
    exclusive config stability, the source guard); the web client's build then runs inside it.
    The re-entrant guards must let it through, not block on the run's own locks."""
    from lhpc.core.probes.backends import CommandResult
    from lhpc.core.service_base import ActionResult

    class _FetchSucceeds(dict):                     # only the web fetch step is faked
        def get(self, argv, default=None):
            ok = len(argv) > 1 and argv[1].endswith("/" + wc.SCRIPT)
            return CommandResult(0, "", "") if ok else default
    monkeypatch.setattr(ControllerService, "binary_target", lambda self: "aarch64-trixie")
    svc = ControllerService(system=FakeSystem(commands=_FetchSucceeds()).system,
                            paths=Paths(runtime_root=tmp_path))
    _daemon(svc, binary_receipt, DAEMON_A)
    srcs = sorted({c.source.path for c in svc.stack("meshtastic").components if c.source})
    with (svc._admission_guard("auto-install"), svc._config_stable(exclusive=True),
          svc._source_operation_guard(srcs, op="auto-install")):
        res = wc.after_daemon(svc, ActionResult(True, "Installed 'meshtastic' from the published binary."))
    assert res.ok, (res.summary, res.details)
    assert any("build meshtastic-web" in d for d in res.details)
    assert wc.read_provenance(svc._paths).get("meshtasticd") == DAEMON_A
