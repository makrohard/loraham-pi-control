"""M18: `clean --purge` is a full wipe of what a stack owns (maintainer, 2026-09-27): its saved
state, its own secrets (node identities, so a reinstall is a NEW node) and the config files LHPC
generates for it. `uninstall` keeps all of that. Files the stack does not declare (chat's
history), the operator's local.toml/secrets.toml, the controller's own secrets and every other
stack stay."""
import re
import shlex
import subprocess
import sys

import pytest

import repo_paths
from lhpc.core.manifest import ManifestError, load_manifest
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService

# What each stack owns under state/ and config/ (the boxes' listings of 2026-09-27, names only).
OWNED = {
    "graywolf": ["state/graywolf/graywolf.db", "state/graywolf/graywolf-admin.txt"],
    "meshtastic": ["state/meshtasticd/prefs/config.proto", "state/meshtasticd/ssl/private_key.pem",
                   "config/files/meshtasticd.yaml"],
    "meshcom": ["state/meshcom/node-flash.bin", "config/secrets/xr_pw"],
    "meshcore": ["state/meshcore/companion.db", "state/meshcore-webui/meshcore-webui.db",
                 "state/openhop/repeater.db", "config/secrets/meshcore_identity.key",
                 "config/secrets/openhop_repeater_identity.key",
                 "config/secrets/openhop_repeater_admin.txt",
                 "config/secrets/meshcore_webui_vapid.pem", "config/files/meshcore.toml"],
    "reticulum": ["state/reticulum/storage/transport_identity", "state/reticulum/config",
                  "state/meshchat/identity", "state/nomadnet/storage/identity",
                  "state/lxmd/storage/identity", "state/sideband/app_storage/sideband_config"],
    "chat": ["config/files/lorachat.conf"],
    "voice": ["config/files/loraham_voice.conf", "config/files/loraham_voice",
              "config/files/loraham_voice_cli"],
}
# Launcher links the stack's pre_steps publish (seeded as symlinks, dangling after the purge
# took the source they point into).
LINKS = {"config/files/loraham_voice", "config/files/loraham_voice_cli"}
# Never a stack's: shared runtime, the controller's and the operator's files, chat's history.
KEPT = ["state/loraham/instance-433.lock", "config/secrets/web_session.key",
        "config/secrets.toml", "config/local.toml", "config/files/lorachat.log"]


def _svc(tmp_path):
    (tmp_path / "config" / "stacks").mkdir(parents=True, exist_ok=True)
    return ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))


def _present(p):
    return p.exists() or p.is_symlink()


def _seed(tmp_path, rels):
    for rel in rels:
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        if rel in LINKS:
            p.symlink_to(tmp_path / "src" / "LoRaHAM_Voice" / p.name)
        else:
            p.write_text("x")


@pytest.mark.parametrize("sid", sorted(OWNED))
def test_purge_wipes_what_the_stack_owns_and_nothing_else(tmp_path, sid):
    svc = _svc(tmp_path)
    everything = [r for rels in OWNED.values() for r in rels]
    _seed(tmp_path, everything + KEPT)
    plan = svc.clean(sid)
    for rel in OWNED[sid]:
        top = rel if rel.startswith("config/") else "/".join(rel.split("/")[:2])
        assert any(f"[remove] {top}" in ln for ln in plan.details), (rel, plan.details)
    res = svc.clean(sid, apply=True, purge=True)
    assert res.ok, res.details
    for rel in OWNED[sid]:
        assert not (tmp_path / rel).is_symlink() and not (tmp_path / rel).exists(), \
            (rel, res.details)
    for rel in KEPT + [r for s, rels in OWNED.items() if s != sid for r in rels]:
        assert _present(tmp_path / rel), rel


@pytest.mark.parametrize("sid", sorted(OWNED))
def test_uninstall_keeps_what_the_stack_owns(tmp_path, sid):
    svc = _svc(tmp_path)
    _seed(tmp_path, OWNED[sid])
    svc.uninstall(sid, apply=True)
    for rel in OWNED[sid]:
        assert _present(tmp_path / rel), rel


def test_purge_names_the_identity_cost(tmp_path):
    plan = _svc(tmp_path).clean("meshcore")
    assert "identities" in plan.summary
    assert any("[remove] config/secrets/meshcore_identity.key" in ln for ln in plan.details)
    assert any("peers must forget" in ln for ln in plan.details), plan.details


def test_purge_refuses_a_symlinked_secret_and_keeps_its_target(tmp_path):
    svc = _svc(tmp_path)
    target = tmp_path / "elsewhere.key"
    target.write_text("keep me")
    link = tmp_path / "config" / "secrets" / "xr_pw"
    link.parent.mkdir(parents=True)
    link.symlink_to(target)
    res = svc.clean("meshcom", apply=True, purge=True)
    assert not res.ok
    assert any("[fail] config/secrets/xr_pw" in ln for ln in res.details), res.details
    assert target.read_text() == "keep me"


def _declared(svc):
    return {st.id: ({n for c in st.components for n in c.secret_files},
                    {c.state_root for c in st.components if c.state_root})
            for st in svc.stacks()}


def test_every_controller_minted_secret_file_is_declared_by_its_stack(tmp_path):
    """A secret LHPC writes for a stack but no `secret_files` names would survive the purge
    and bring the old identity back."""
    svc = _svc(tmp_path)
    decl = _declared(svc)
    for st in svc.stacks():
        for c in st.components:
            fc = c.config_file
            for p in (fc.params if fc else ()):
                if p.secret_file:
                    assert p.secret_file in decl[st.id][0], (st.id, p.secret_file)
            if c.ui_password_file.startswith("config/secrets/"):
                assert c.ui_password_file.split("/")[-1] in decl[st.id][0], c.id
    from lhpc.core import meshcore_identity as mi, service_hmac
    assert {mi.IDENTITY_FILENAME, mi.REPEATER_IDENTITY_FILENAME,
            mi.REPEATER_ADMIN_FILENAME} <= decl["meshcore"][0]
    assert service_hmac._XR_PW[-1] in decl["meshcom"][0]


def test_the_webui_launcher_mints_only_secrets_its_stack_declares(tmp_path):
    """The meshcore-webui launcher writes its VAPID key itself (no `config_file` param names
    it), so the launcher the manifest runs is driven here: a stand-in venv python runs the
    key helper with this interpreter and stops where uvicorn would start."""
    svc = _svc(tmp_path / "box")
    webui = next(c for st in svc.stacks() for c in st.components if c.id == "meshcore-webui")
    launcher = webui.run_argv[0].replace("{asset}", str(repo_paths.REPO / "lhpc" / "data"))
    src, rt = tmp_path / "src", tmp_path / "rt"
    py = src / "backend" / ".venv" / "bin" / "python"
    py.parent.mkdir(parents=True)
    py.write_text(f'#!/bin/sh\n[ "$1" = -m ] && exit 0\nexec {shlex.quote(sys.executable)} "$@"\n')
    py.chmod(0o755)
    subprocess.run(["bash", launcher, str(src), str(rt), str(tmp_path / "dist"), "127.0.0.1",
                    "5000", "127.0.0.1", "8788"], check=True, timeout=60)
    minted = {p.name for p in (rt / "config" / "secrets").iterdir()}
    assert minted and minted <= _declared(svc)["meshcore"][0], minted


def test_no_state_root_or_secret_is_claimed_by_two_stacks(tmp_path):
    decl = _declared(_svc(tmp_path))
    for kind in (0, 1):
        assert any(d[kind] for d in decl.values()), kind     # the check below is not vacuous
        seen = {}
        for sid, d in decl.items():
            for n in d[kind]:
                assert n not in seen, (n, seen.get(n), sid)
                seen[n] = sid
    assert "state/loraham" not in {r for d in decl.values() for r in d[1]}


def _manifest_declaring(tmp_path, value):
    """A minimal one-component manifest whose `secret_files` is `value` (TOML)."""
    path = tmp_path / "manifest.toml"
    path.write_text('[[stack]]\nid = "s"\nname = "s"\nmain = "app"\n'
                    '[[stack.component]]\nid = "app"\nname = "app"\nkind = "service"\n'
                    'run = "bin/app"\nreadiness = "manual"\ninteractive = true\n'
                    f"secret_files = {value}\n")
    return path


@pytest.mark.parametrize("bad", ['"../x"', '"a/b"', '""', '".hidden"', '"secrets.toml"', "3"])
def test_secret_files_must_be_bare_names(tmp_path, bad):
    with pytest.raises(ManifestError, match="secret_files"):
        load_manifest(_manifest_declaring(tmp_path, f"[{bad}]"))
    stacks = load_manifest(_manifest_declaring(tmp_path, '["webui_vapid.pem"]'))
    assert stacks[0].component("app").secret_files == ("webui_vapid.pem",)


def test_purge_of_a_never_used_stack_says_already_absent(tmp_path):
    res = _svc(tmp_path).clean("meshcom", apply=True, purge=True)
    assert any(ln.strip() == "[removed] config/secrets/xr_pw (already absent)"
               for ln in res.details), res.details


def test_purge_removes_reticulums_read_only_config(tmp_path):
    # LHPC writes state/reticulum/config 0400 (read-only even to its owner).
    svc = _svc(tmp_path)
    cfg = tmp_path / "state" / "reticulum" / "config"
    _seed(tmp_path, ["state/reticulum/config"])
    cfg.chmod(0o400)
    res = svc.clean("reticulum", apply=True, purge=True)
    assert res.ok, res.details
    assert not cfg.parent.exists()


def test_purge_of_a_running_stack_removes_nothing(tmp_path):
    rt = str(tmp_path)
    svc = ControllerService(system=FakeSystem(cmdlines_data={555: [
        f"{rt}/build/tools/graywolf/usr/bin/graywolf", "-config",
        f"{rt}/state/graywolf/graywolf.db", "-http", "127.0.0.1:8080"]}).system,
        paths=Paths(runtime_root=tmp_path))
    _seed(tmp_path, OWNED["graywolf"])
    res = svc.clean("graywolf", apply=True, purge=True)
    assert not res.ok and "running" in res.summary.lower(), res.summary
    for rel in OWNED["graywolf"]:
        assert _present(tmp_path / rel), rel


def test_purge_refuses_a_directory_where_a_secret_file_belongs(tmp_path):
    svc = _svc(tmp_path)
    d = tmp_path / "config" / "secrets" / "xr_pw"
    (d / "inner").mkdir(parents=True)
    res = svc.clean("meshcom", apply=True, purge=True)
    assert not res.ok
    assert any("[fail] config/secrets/xr_pw" in ln for ln in res.details), res.details
    assert (d / "inner").is_dir()


def test_purge_refuses_a_symlinked_state_root_and_keeps_its_target(tmp_path):
    svc = _svc(tmp_path)
    target = tmp_path / "elsewhere"
    target.mkdir()
    (target / "graywolf.db").write_text("keep me")
    (tmp_path / "state").mkdir()
    (tmp_path / "state" / "graywolf").symlink_to(target)
    res = svc.clean("graywolf", apply=True, purge=True)
    assert not res.ok
    assert any("[fail] state/graywolf" in ln for ln in res.details), res.details
    assert (target / "graywolf.db").read_text() == "keep me"


def test_no_other_stack_uses_a_state_root_or_secret_a_purge_removes(tmp_path):
    """Purging one stack must not pull state from under another: every component that names
    a declared root or secret file belongs to the stack that declares it."""
    svc = _svc(tmp_path)
    decl = _declared(svc)
    for st in svc.stacks():
        for c in st.components:
            tokens = _path_tokens(c)
            secret_params = {p.secret_file for p in (c.config_file.params if c.config_file else ())}
            for owner, (secrets_, roots) in decl.items():
                if owner == st.id:
                    continue
                for r in roots:
                    assert not any(re.search(rf"{re.escape(r)}(?![A-Za-z0-9_.-])", t)
                                   for t in tokens), (c.id, r)
                for n in secrets_:
                    assert n not in secret_params, (c.id, n)
                    assert not any(f"secrets/{n}" in t for t in tokens), (c.id, n)


def _strings(v):
    if isinstance(v, str):
        return [v]
    if isinstance(v, dict):
        return [s for x in v.values() for s in _strings(x)]
    if isinstance(v, (list, tuple)):
        return [s for x in v for s in _strings(x)]
    return []


def _path_tokens(c):
    """Every typed field of a component that can name a file it reads or writes: its argv,
    environment, working folder, steps, config file, logs, password file, endpoints and the
    process match."""
    fc, proc = c.config_file, c.process
    return _strings([c.run_argv, c.run_cwd, c.run_env, c.test_argv, c.log_paths,
                     c.ui_password_file, c.pre_steps, c.post_steps, c.build_steps,
                     [e.address for e in c.endpoints],
                     [fc.path, fc.base] if fc else [],
                     [proc.all_args, proc.any_args] if proc else []])


def test_purge_refuses_a_regular_file_where_a_launcher_link_belongs(tmp_path):
    svc = _svc(tmp_path)
    f = tmp_path / "config" / "files" / "loraham_voice"
    f.parent.mkdir(parents=True)
    f.write_text("operator's own file")
    res = svc.clean("voice", apply=True, purge=True)
    assert not res.ok
    assert any("[fail] config/files/loraham_voice" in ln for ln in res.details), res.details
    assert f.read_text() == "operator's own file"


def test_a_parent_swapped_after_the_containment_check_cannot_redirect_the_removal(tmp_path,
                                                                                   monkeypatch):
    """The state tree is removed relative to held no-follow fds: replacing `state/` with a
    symlink AFTER `Paths.under` approved the path must not delete the symlink's target."""
    svc = _svc(tmp_path)
    _seed(tmp_path, ["state/graywolf/graywolf.db"])
    outside = tmp_path.parent / (tmp_path.name + "-outside")
    (outside / "graywolf").mkdir(parents=True)
    (outside / "graywolf" / "graywolf.db").write_text("not LHPC's")
    real_under = type(svc._paths).under
    swapped = []

    def under(self, *parts):
        path = real_under(self, *parts)
        if parts == ("state", "graywolf") and not swapped:
            swapped.append(True)                 # the swap, right after the check ...
            (tmp_path / "state").rename(tmp_path / "state.real")
            (tmp_path / "state").symlink_to(outside)
        elif parts == ("logs",) and swapped and (tmp_path / "state").is_symlink():
            (tmp_path / "state").unlink()        # ... undone at the next step, so the
            (tmp_path / "state.real").rename(tmp_path / "state")   # lock release works
        return path
    monkeypatch.setattr(type(svc._paths), "under", under)
    res = svc.clean("graywolf", apply=True, purge=True)
    assert swapped, "the removal never asked for state/graywolf"
    assert not res.ok
    assert any("[fail] state/graywolf" in ln for ln in res.details), res.details
    assert (outside / "graywolf" / "graywolf.db").read_text() == "not LHPC's"
