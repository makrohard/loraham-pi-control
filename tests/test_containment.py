"""Containment: LHPC never reads or writes outside the runtime root. The shipped manifest
has no external link sources, no /tmp artifacts of its own, and no {runtime}/.. escapes;
the local-adoption fallback is off by default and must be in-root when configured."""

import tomllib
from pathlib import Path

import pytest

from lhpc.core import manifest as manifest_mod
from lhpc.core.config import Config
from lhpc.core.install import Installer
from lhpc.core.model import Component, ComponentKind, SourceSpec, Stack
from lhpc.core.paths import Paths
from lhpc.core.probes import RealSystem


def _manifest_dict():
    from lhpc.core.config import asset_path
    return tomllib.load(open(asset_path("manifest.example.toml"), "rb"))


def test_shipped_manifest_has_zero_link_strategies():
    d = _manifest_dict()
    for st in d["stack"]:
        for c in st.get("component", []):
            assert c.get("source", {}).get("strategy", "") != "link", \
                f"{c['id']}: link strategy shipped"
    stacks = manifest_mod.load_manifest()                        # and it LOADS
    assert len(stacks) == 8


def test_link_strategy_refused_at_manifest_load(tmp_path):
    bad = tmp_path / "m.toml"
    bad.write_text('''
[[stack]]
id = "s"
name = "s"
main = "c"
  [[stack.component]]
  id = "c"
  name = "c"
  kind = "service"
  readiness = "manual"
  interactive = true
  run = "true"
    [stack.component.source]
    path = "src/c"
    strategy = "link"
''')
    with pytest.raises(manifest_mod.ManifestError, match="link.*not permitted"):
        manifest_mod.load_manifest(bad)


def test_no_tmp_or_root_escape_tokens_in_manifest():
    # Durable regression sweep: no LHPC-side artifact path in the manifest names /tmp or
    # escapes the root. Allowlist: the external daemon's own socket ADDRESSES (client
    # connects) — endpoint addresses and *socket* param defaults.
    d = _manifest_dict()
    offenders = []
    def walk(o, path=""):
        if isinstance(o, dict):
            if "socket" in str(o.get("name", "")) or "socket" in str(o.get("key", "")):
                return                           # daemon-socket param: client-connect decl
            for k, v in o.items():
                walk(v, f"{path}.{k}")
        elif isinstance(o, list):
            for i, v in enumerate(o):
                walk(v, f"{path}[{i}]")
        elif isinstance(o, str):
            allow = ("endpoint" in path or "socket" in path or ".note" in path
                     or ".purpose" in path or "comment" in path)
            if "/tmp/" in o and not allow:
                offenders.append((path, o))
            if "{runtime}/.." in o:
                offenders.append((path, o))
    for st in d["stack"]:
        walk(st, st["id"])
    assert not offenders, offenders


def test_python_stacks_have_in_tree_venv_build_steps():
    d = _manifest_dict()
    comps = {c["id"]: c for st in d["stack"] for c in st.get("component", [])}
    for cid in ("meshcore-pi", "meshcore-nodegui", "meshcore-cli"):
        steps = comps[cid].get("build_steps", [])
        assert steps and steps[0]["argv"][:3] == ["python3", "-m", "venv"], cid
        if cid == "meshcore-pi":
            # system-site venv (OS-shipped GPIO bindings; never compiles lgpio/swig)
            assert "--system-site-packages" in steps[0]["argv"]
            assert not any("rpi-lgpio" in a for a in steps[1]["argv"])
        assert steps[1]["argv"][0] == ".venv/bin/pip", cid
    # meshcom-qemu is self-sufficient from a FRESH clone: workspace setup scripts run
    # before build.sh (live finding: linked trees carried a pre-built .work/)
    q_steps = [st["argv"][0] for st in comps["meshcom-qemu"]["build_steps"]]
    assert q_steps == ["scripts/setup.sh", "scripts/apply-overlay.sh",
                       "scripts/prepare-openeth.sh", "scripts/build.sh"]
    # meshcom secret is in-root and fail-closed
    q = comps["meshcom-qemu"]["build_steps"][-1]["env"]["XR_PASSWORD"]
    assert q == "@file?:{runtime}/config/secrets/xr_pw"          # OPTIONAL secret (legacy
    # `$(cat … 2>/dev/null)` semantics: absent -> HMAC disabled, never a blocked build)
    # meshcore-pi's config BASE lives in-root (self-sufficient regardless of what the
    # pinned clone ships — live finding: the old base only existed untracked in ~/src)
    base = comps["meshcore-pi"]["config_file"]["base"]
    assert base.startswith("{runtime}/config/files/"), base


def test_meshcom_secret_env_resolves_in_root(tmp_path):
    from lhpc.core import commands
    stacks = manifest_mod.load_manifest()
    comp = next(c for s in stacks if s.id == "meshcom"
                for c in s.components if c.id == "meshcom-qemu")
    step = comp.build_steps[-1]                     # the build.sh step carries env
    sec = tmp_path / "config" / "secrets"
    sec.mkdir(parents=True)
    (sec / "xr_pw").write_text("hunter2\n")
    env = commands.build_env(list(step.get("env", {}).items()), str(tmp_path),
                             str(tmp_path / "src"), "")
    assert env["XR_PASSWORD"] == "hunter2"
    (sec / "xr_pw").unlink()
    env2 = commands.build_env(list(step.get("env", {}).items()), str(tmp_path),
                              str(tmp_path / "src"), "")
    assert env2["XR_PASSWORD"] == ""             # optional: absent -> disabled, build runs


def _inst(tmp_path, search=""):
    comp = Component(id="app", name="app", kind=ComponentKind.SERVICE,
                     source=SourceSpec(path="src/app", local_dir="app"))
    values = {"install": {"adopt_search_root": search}} if search else {}
    stacks = (Stack(id="s", name="s", main="app", components=(comp,)),)
    inst = Installer(Paths(runtime_root=tmp_path / "rt"), stacks,
                     Config(values=values), RealSystem())
    (tmp_path / "rt").mkdir(exist_ok=True)
    return inst, comp


def test_default_no_fallback_clone_failure_is_typed(tmp_path):
    # Default (blank) adopt_search_root: no fallback dir exists AT ALL — a clone failure
    # is a typed selector refusal, never an outside-root read.
    inst, comp = _inst(tmp_path)                                 # no remote, no fallback
    a = inst.adopt_source(comp, source="dev")
    assert a.status == "failed"
    assert "active source untouched" in a.detail or "unavailable" in a.detail
    assert not (tmp_path / "rt" / "src" / "app").exists()


def test_outside_root_search_root_refused(tmp_path):
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (outside / "app").mkdir()
    inst, comp = _inst(tmp_path, search=str(outside))
    a = inst.adopt_source(comp, source="dev")
    assert a.status == "failed" and "escapes the runtime root" in a.detail
    assert not (tmp_path / "rt" / "src" / "app").exists()        # zero mutation


def test_in_root_search_root_works(tmp_path):
    import subprocess, os
    local = tmp_path / "rt" / "checkouts" / "app"
    local.mkdir(parents=True)
    (local / "f").write_text("x")
    env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
    for args in (("init", "-q"), ("add", "-A"), ("commit", "-qm", "c")):
        subprocess.run(("git", "-C", str(local)) + args, env=env, check=True,
                       capture_output=True)
    inst, comp = _inst(tmp_path, search=str(tmp_path / "rt" / "checkouts"))
    a = inst.adopt_source(comp, source="dev")
    assert a.status == "done", a.detail
    assert (tmp_path / "rt" / "src" / "app" / "f").exists()


def test_radiolib_builds_in_root_and_daemon_pins_it():
    # LIVE FINDING: the daemon's build.sh silently fell back to the EXTERNAL
    # ~/src/RadioLib (prebuilt) because the managed in-root clone was never built.
    # The library now has its own in-root build_steps and the daemon's step pins
    # RADIOLIB_DIR to the managed clone.
    import tomllib
    from lhpc.core import manifest as mf
    with mf.default_manifest_path().open("rb") as fh:
        raw = tomllib.load(fh)
    comps = {c["id"]: c for st in raw["stack"] for c in st["component"]}
    rl = comps["radiolib"]
    assert [s["argv"][0] for s in rl["build_steps"]] == ["cmake", "cmake"]
    dm = comps["loraham-daemon"]
    env = dm["build_steps"][0]["env"]
    assert env["RADIOLIB_DIR"] == "{runtime}/src/RadioLib"       # in-root, never ~/src


def test_stack_build_includes_buildable_libraries_dep_first(tmp_path):
    # Stack build plans must include non-runnable buildable sources (libraries) and
    # order build_requires providers FIRST (fresh root: libRadioLib.a before build.sh).
    from lhpc.core.probes.backends import FakeSystem
    from lhpc.core.services import ControllerService
    from lhpc.core.paths import Paths
    svc = ControllerService(system=FakeSystem(cmdlines_data={}).system,
                            paths=Paths(runtime_root=tmp_path))
    plan = svc.build("daemon", apply=False)
    assert plan.ok
    order = [d.split()[1].rstrip(":") for d in plan.details if d.strip().startswith("[build]")]
    assert "radiolib" in order and "loraham-daemon" in order
    assert order.index("radiolib") < order.index("loraham-daemon")


def test_build_launcher_never_bakes_secret_plaintext():
    # AUDIT S1: build-step @file secrets were resolved at RENDER time and baked cleartext
    # into the on-disk launcher .py (which is never pruned). The launcher must carry the
    # UNRESOLVED token and resolve on-host at exec time.
    import tempfile
    from lhpc.core import commands
    secret = tempfile.NamedTemporaryFile("w", delete=False, suffix="-xrpw")
    secret.write("TOPSECRETpw\n")
    secret.close()
    steps = [{"argv": ["scripts/build.sh"],
              "env": {"XR_PASSWORD": f"@file:{secret.name}", "XR_HOST": "10.0.2.2"}}]
    script = commands.render_build_launcher(steps, "/rt", "/rt/src/x")
    assert "TOPSECRETpw" not in script                    # secret NOT baked
    assert "@file:" in script                             # token carried instead
    assert "10.0.2.2" in script                           # non-secret literal fine


def test_open_source_parent_refuses_intermediate_symlink(tmp_path):
    # AUDIT FS1: opening the resolved source root in one os.open guarded only its final
    # component; an intermediate `src` symlink escaped the root. The walk now starts at
    # the runtime root and refuses a swapped intermediate at the syscall.
    import os
    from lhpc.core.paths import Paths, PathContainmentError
    from lhpc.core.probes.backends import FakeSystem
    from lhpc.core.services import ControllerService
    from lhpc.core.model import Component, ComponentKind, SourceSpec
    rt = tmp_path / "rt"
    (rt / "src" / "app").mkdir(parents=True)
    svc = ControllerService(system=FakeSystem(cmdlines_data={}).system, paths=Paths(runtime_root=rt))
    comp = Component(id="app", name="app", kind=ComponentKind.SERVICE,
                     source=SourceSpec(path="src/app"))
    # happy path writes inside the tree
    svc._write_source_config(comp, "conf/x.toml", "ok=1\n")
    assert (rt / "src" / "app" / "conf" / "x.toml").read_text() == "ok=1\n"
    # swap `src` for a symlink escaping the root -> refused at the walk
    outside = tmp_path / "evil"; outside.mkdir()
    import shutil
    shutil.rmtree(rt / "src")
    os.symlink(outside, rt / "src")
    try:
        svc._write_source_config(comp, "conf/y.toml", "pwned=1\n")
        assert False, "escape not refused"
    except (PathContainmentError, OSError):
        pass
    assert not (outside / "app" / "conf" / "y.toml").exists()   # nothing written outside


def test_norm_survives_hostile_daemon_value():
    # AUDIT IN1: int(float("1e400")) raised uncaught OverflowError, crashing a mutating
    # action on a garbled daemon reply.
    from lhpc.core import daemon_control as dc
    for v in ("1e400", "inf", "-inf", "9" * 400):
        assert isinstance(dc._norm(v), str)               # no crash
    assert dc._norm("433.0") == "433" and dc._norm("LORA") == "LORA"


def test_source_config_works_through_symlinked_runtime_root(tmp_path):
    # RE-AUDIT F1: the FS1 walk over-applied O_NOFOLLOW to the runtime ROOT, breaking the
    # documented symlinked-root setup (writes went via atomic_write and worked, but reads
    # via _open_source_parent raised ELOOP — asymmetric). The root is the trusted anchor
    # and may be a symlink; only components UNDER it are O_NOFOLLOW.
    import os
    from lhpc.core.paths import Paths
    from lhpc.core.probes.backends import FakeSystem
    from lhpc.core.services import ControllerService
    from lhpc.core.model import Component, ComponentKind, SourceSpec
    real = tmp_path / "real"; (real / "src" / "app").mkdir(parents=True)
    link = tmp_path / "link"; os.symlink(real, link)
    svc = ControllerService(system=FakeSystem(cmdlines_data={}).system,
                            paths=Paths(runtime_root=link))
    comp = Component(id="app", name="app", kind=ComponentKind.SERVICE,
                     source=SourceSpec(path="src/app"))
    svc._write_source_config(comp, "conf/x.toml", "ok=1\n")
    assert svc._read_source_base(comp, "conf/x.toml").strip() == "ok=1"   # read works too


def test_open_marker_excl_no_fd_leak_when_dup_fails(tmp_path, monkeypatch):
    # RE-AUDIT F2: hoisting os.dup(parent_fd) before the try leaked file_fd if os.dup
    # raised under fd exhaustion. The dup is now guarded; file_fd is always closed.
    import os
    from lhpc.core import runtime_fs
    closed = []
    real_close = os.close
    monkeypatch.setattr(os, "close", lambda fd: (closed.append(fd), real_close(fd))[1])
    real_dup = os.dup
    def boom(fd):
        raise OSError(24, "EMFILE")               # simulate fd exhaustion at dup
    monkeypatch.setattr(os, "dup", boom)
    try:
        runtime_fs.open_marker_excl(Paths_(tmp_path), tmp_path / "m.marker", "x")
        assert False, "should have raised"
    except OSError:
        pass
    monkeypatch.setattr(os, "dup", real_dup)
    assert closed, "file_fd was not closed on the dup-failure path"


def Paths_(rt):
    from lhpc.core.paths import Paths
    return Paths(runtime_root=rt)
