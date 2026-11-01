"""`lhpc/data/scripts/rns-wheel.sh`, executed: the Reticulum node builds ONE wheel and installs it; each
client installs exactly that file. Real venvs, real pip, no network: the wheels are written with the
standard library, and the node's checkout builds through an in-tree PEP 517 backend that needs nothing.
"""
import base64
import hashlib
import os
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest
import repo_paths

SCRIPT = repo_paths.REPO / "lhpc" / "data" / "scripts" / "rns-wheel.sh"


def _record_line(name: str, data: bytes) -> str:
    digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
    return f"{name},sha256={digest},{len(data)}"


def _wheel(directory: Path, version: str, code: str) -> Path:
    """An `rns` wheel whose RNS/__init__.py holds `code`."""
    directory.mkdir(parents=True, exist_ok=True)
    info = f"rns-{version}.dist-info"
    files = {"RNS/__init__.py": code.encode(),
             f"{info}/METADATA": f"Metadata-Version: 2.1\nName: rns\nVersion: {version}\n".encode(),
             f"{info}/WHEEL": b"Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: true\nTag: py3-none-any\n"}
    record = "\n".join(_record_line(n, d) for n, d in files.items()) + f"\n{info}/RECORD,,\n"
    path = directory / f"rns-{version}-py3-none-any.whl"
    with zipfile.ZipFile(path, "w") as z:
        for name, data in files.items():
            z.writestr(name, data)
        z.writestr(f"{info}/RECORD", record)
    return path


BACKEND = '''import base64, hashlib, os, zipfile
def _rec(n, d):
    return n + ",sha256=" + base64.urlsafe_b64encode(hashlib.sha256(d).digest()).rstrip(b"=").decode() + "," + str(len(d))
def build_wheel(wheel_directory, config_settings=None, metadata_directory=None):
    info = "rns-%(v)s.dist-info"
    files = {"RNS/__init__.py": open("RNS/__init__.py", "rb").read(),
             info + "/METADATA": b"Metadata-Version: 2.1\\nName: rns\\nVersion: %(v)s\\n",
             info + "/WHEEL": b"Wheel-Version: 1.0\\nGenerator: test\\nRoot-Is-Purelib: true\\nTag: py3-none-any\\n"}
    name = "rns-%(v)s-py3-none-any.whl"
    with zipfile.ZipFile(os.path.join(wheel_directory, name), "w") as z:
        for n, d in files.items():
            z.writestr(n, d)
        z.writestr(info + "/RECORD", "\\n".join(_rec(n, d) for n, d in files.items()) + "\\n" + info + "/RECORD,,\\n")
    return name
'''


def _venv(path: Path) -> Path:
    subprocess.run([sys.executable, "-m", "venv", str(path)], check=True)
    return path


def _node(tmp_path: Path, version: str = "1.5.5", code: str = "NODE = 1\n") -> Path:
    """A node checkout with its venv: building it yields rns-<version> holding `code`."""
    src = tmp_path / "src" / "reticulum"
    (src / "RNS").mkdir(parents=True)
    (src / "RNS" / "__init__.py").write_text(code)
    (src / "backend.py").write_text(BACKEND % {"v": version})
    (src / "pyproject.toml").write_text('[build-system]\nrequires = []\nbuild-backend = "backend"\n'
                                        'backend-path = ["."]\n')
    _venv(src / ".venv")
    return src


def _run(cwd: Path, *args: str, env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", str(SCRIPT), *args], cwd=cwd, capture_output=True, text=True, check=False,
                          env={**os.environ, **(env or {})})


def _installed(venv_root: Path) -> tuple[str, str]:
    """(version, RNS/__init__.py text) of the rns in the venv at `venv_root/.venv`."""
    r = subprocess.run([str(venv_root / ".venv" / "bin" / "python"), "-c",
                        "import importlib.metadata as m; d = m.distribution('rns');"
                        "print(d.version); print(open(d.locate_file('RNS/__init__.py')).read(), end='')"],
                       capture_output=True, text=True, check=True)
    version, _, code = r.stdout.partition("\n")
    return version, code


def _client(tmp_path: Path, name: str = "nomadnet") -> Path:
    root = tmp_path / "src" / name
    root.mkdir(parents=True)
    _venv(root / ".venv")
    return root


@pytest.mark.slow                # real venvs (ensurepip) and real pip runs
def test_build_makes_one_wheel_and_installs_it_into_the_node(tmp_path):
    """The log's last line names the node venv's own install, not the checkout's in-tree metadata (a real
    `pip install .` leaves an rns.egg-info in the cwd)."""
    node = _node(tmp_path)
    (node / "rns.egg-info").mkdir()
    (node / "rns.egg-info" / "PKG-INFO").write_text("Metadata-Version: 2.1\nName: rns\nVersion: 0.0.1\n")
    r = _run(node, "build", ".venv/lhpc-wheel")
    assert r.returncode == 0, r.stderr
    assert sorted(p.name for p in (node / ".venv" / "lhpc-wheel").iterdir()) == ["rns-1.5.5-py3-none-any.whl"]
    assert _installed(node) == ("1.5.5", "NODE = 1\n")
    report = [line for line in r.stdout.splitlines() if line.startswith("rns-wheel: rns ")]
    version, path = report[-1].removeprefix("rns-wheel: rns ").split(" ", 1)
    assert version == "1.5.5" and Path(path).is_relative_to(node / ".venv"), r.stdout


@pytest.mark.slow                # real venvs (ensurepip) and real pip runs
def test_build_removes_a_stale_higher_wheel(tmp_path):
    node = _node(tmp_path)
    _wheel(node / ".venv" / "lhpc-wheel", "9.9.9", "STALE = 1\n")
    r = _run(node, "build", ".venv/lhpc-wheel")
    assert r.returncode == 0, r.stderr
    assert sorted(p.name for p in (node / ".venv" / "lhpc-wheel").iterdir()) == ["rns-1.5.5-py3-none-any.whl"]


@pytest.mark.slow                # real venvs (ensurepip) and real pip runs
def test_build_replaces_a_symlinked_wheel_dir_without_following_it(tmp_path):
    """A symlink to a directory holding a stale higher wheel: the link goes, a real directory with
    the node's one wheel takes its place, and the link's target is left as it was."""
    node = _node(tmp_path)
    elsewhere = tmp_path / "elsewhere"
    stale = _wheel(elsewhere, "9.9.9", "STALE = 1\n")
    (node / ".venv" / "lhpc-wheel").symlink_to(elsewhere)
    r = _run(node, "build", ".venv/lhpc-wheel")
    assert r.returncode == 0, r.stderr
    wheel_dir = node / ".venv" / "lhpc-wheel"
    assert not wheel_dir.is_symlink()
    assert sorted(p.name for p in wheel_dir.iterdir()) == ["rns-1.5.5-py3-none-any.whl"]
    assert sorted(elsewhere.iterdir()) == [stale]


@pytest.mark.slow                # real venvs (ensurepip) and real pip runs
@pytest.mark.skipif(os.geteuid() == 0, reason="root removes a read-only directory's entries anyway")
def test_build_fails_when_the_old_wheel_dir_cannot_be_removed(tmp_path):
    node = _node(tmp_path)
    locked = node / ".venv" / "lhpc-wheel" / "locked"
    _wheel(locked, "9.9.9", "STALE = 1\n")
    locked.chmod(0o555)
    try:
        r = _run(node, "build", ".venv/lhpc-wheel")
        assert r.returncode != 0
        assert (locked / "rns-9.9.9-py3-none-any.whl").exists()
    finally:
        locked.chmod(0o755)


def _node_wheel(tmp_path: Path) -> Path:
    return _wheel(tmp_path / "node-wheel", "1.5.5", "NODE = 1\n").parent


@pytest.mark.slow                # real venvs (ensurepip) and real pip runs
@pytest.mark.parametrize("scope", ["global", "site", "user", "env"])
def test_install_takes_the_node_wheel_whatever_pip_is_configured_with(tmp_path, scope):
    """A higher rns offered by every pip configuration scope (global through XDG_CONFIG_DIRS, the venv's
    own pip.conf, the user's, PIP_FIND_LINKS) never wins over the node's wheel."""
    wheel_dir, client = _node_wheel(tmp_path), _client(tmp_path)
    decoys = _wheel(tmp_path / "decoys", "9.9.9", "DECOY = 1\n").parent
    conf = f"[global]\nfind-links = {decoys}\n"
    env = {}
    if scope == "global":
        (tmp_path / "xdg-dirs" / "pip").mkdir(parents=True)
        (tmp_path / "xdg-dirs" / "pip" / "pip.conf").write_text(conf)
        env["XDG_CONFIG_DIRS"] = str(tmp_path / "xdg-dirs")
    elif scope == "site":
        (client / ".venv" / "pip.conf").write_text(conf)
    elif scope == "user":
        (tmp_path / "xdg-home" / "pip").mkdir(parents=True)
        (tmp_path / "xdg-home" / "pip" / "pip.conf").write_text(conf)
        env["XDG_CONFIG_HOME"] = str(tmp_path / "xdg-home")
    else:
        env["PIP_FIND_LINKS"] = str(decoys)
    r = _run(client, "install", str(wheel_dir), env=env)
    assert r.returncode == 0, r.stderr
    assert _installed(client) == ("1.5.5", "NODE = 1\n")


@pytest.mark.slow                # real venvs (ensurepip) and real pip runs
def test_install_ignores_a_pip_conf_that_redirects_the_install(tmp_path):
    """A venv's own pip.conf with `[install] target = <dir>` would put the explicit file somewhere the client
    never imports from; PIP_CONFIG_FILE=/dev/null keeps every config file out (find-links decoys alone cannot
    show it: an explicit file is not resolved)."""
    wheel_dir, client = _node_wheel(tmp_path), _client(tmp_path)
    (client / ".venv" / "pip.conf").write_text(f"[install]\ntarget = {tmp_path / 'elsewhere'}\n")
    r = _run(client, "install", str(wheel_dir))
    assert r.returncode == 0, r.stderr
    assert _installed(client) == ("1.5.5", "NODE = 1\n")
    assert not (tmp_path / "elsewhere").exists()


@pytest.mark.slow                # real venvs (ensurepip) and real pip runs
@pytest.mark.parametrize("version, code", [("2.0.0", "OTHER = 1\n"), ("1.5.5", "SAME_VERSION_OTHER_CODE = 1\n")])
def test_install_replaces_an_rns_the_client_already_has(tmp_path, version, code):
    """An rns already in the client (another version, or the same version with other code) is replaced."""
    wheel_dir, client = _node_wheel(tmp_path), _client(tmp_path)
    other = _wheel(tmp_path / "other", version, code)
    subprocess.run([str(client / ".venv" / "bin" / "pip"), "install", "-q", "--no-index", str(other)], check=True)
    assert _installed(client) == (version, code)
    r = _run(client, "install", str(wheel_dir))
    assert r.returncode == 0, r.stderr
    assert _installed(client) == ("1.5.5", "NODE = 1\n")


def _dist(directory: Path, name: str, version: str, requires: str) -> Path:
    """A pure wheel `name` that requires `requires` (e.g. "rns>=9")."""
    directory.mkdir(parents=True, exist_ok=True)
    info = f"{name}-{version}.dist-info"
    files = {f"{name}/__init__.py": b"",
             f"{info}/METADATA": f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\nRequires-Dist: {requires}\n".encode(),
             f"{info}/WHEEL": b"Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: true\nTag: py3-none-any\n"}
    record = "\n".join(_record_line(k, d) for k, d in files.items()) + f"\n{info}/RECORD,,\n"
    path = directory / f"{name}-{version}-py3-none-any.whl"
    with zipfile.ZipFile(path, "w") as z:
        for k, d in files.items():
            z.writestr(k, d)
        z.writestr(f"{info}/RECORD", record)
    return path


def _pip(client: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([str(client / ".venv" / "bin" / "pip"), "install", "--disable-pip-version-check", "--no-index",
                           *args], cwd=client, capture_output=True, text=True, check=False)


@pytest.mark.slow                # real venvs (ensurepip) and real pip runs
def test_install_writes_the_constraint_that_keeps_the_nodes_rns(tmp_path):
    """The client's own install runs with `-c .venv/lhpc-rns-constraint.txt`. A dependency whose newest release wants a
    newer rns (lxmf 1.2.1: rns>=1.5.7) then resolves to an older one and the node's rns stays; without the constraint the
    same install replaces the node's rns (the 0.13.6 VM finding, in miniature)."""
    wheel_dir = _node_wheel(tmp_path)
    index = tmp_path / "index"
    _dist(index, "lxmflike", "1.0", "rns>=1.5.5")
    _dist(index, "lxmflike", "2.0", "rns>=9")
    _wheel(index, "9.9.9", "NEWER = 1\n")
    guarded, control = _client(tmp_path, "guarded"), _client(tmp_path, "control")
    for client in (guarded, control):
        assert _run(client, "install", str(wheel_dir)).returncode == 0
    wheel = (wheel_dir / "rns-1.5.5-py3-none-any.whl").resolve()
    assert (guarded / ".venv" / "lhpc-rns-constraint.txt").read_text() == f"rns @ {wheel.as_uri()}\n"
    r = _pip(control, "--find-links", str(index), "lxmflike")
    assert r.returncode == 0 and _installed(control)[0] == "9.9.9", r.stdout       # the control: replaced
    r = _pip(guarded, "-c", ".venv/lhpc-rns-constraint.txt", "--find-links", str(index), "lxmflike")
    assert r.returncode == 0, r.stderr
    assert _installed(guarded) == ("1.5.5", "NODE = 1\n") and "lxmflike-1.0" in r.stdout


def _force_reinstall_from(venv_root: Path, index: Path) -> None:
    """The venv's own pip.conf: every later install reinstalls from `index` (gate 2 round 2's case)."""
    (venv_root / ".venv" / "pip.conf").write_text(f"[global]\nfind-links = {index}\n[install]\nforce-reinstall = true\n")


@pytest.mark.slow                # real venvs (ensurepip) and real pip runs
def test_a_forced_reinstall_takes_the_node_wheel_not_a_same_version_one(tmp_path):
    """A client's later install under a pip.conf that forces reinstallation, with an index offering an rns of the SAME
    version but other code: the constraint names the node's wheel file, so the node's code stays. A version-only
    constraint (rns==1.5.5, the control) lets the other wheel in, which a version check cannot see."""
    wheel_dir = _node_wheel(tmp_path)
    index = tmp_path / "index"
    _dist(index, "lxmflike", "1.0", "rns>=1.5.5")
    _wheel(index, "1.5.5", "SAME_VERSION_OTHER_CODE = 1\n")
    guarded, control = _client(tmp_path, "guarded"), _client(tmp_path, "control")
    for client in (guarded, control):
        assert _run(client, "install", str(wheel_dir)).returncode == 0
        _force_reinstall_from(client, index)
    (control / "version-only.txt").write_text("rns==1.5.5\n")
    r = _pip(control, "-c", "version-only.txt", "lxmflike")
    assert r.returncode == 0 and _installed(control) == ("1.5.5", "SAME_VERSION_OTHER_CODE = 1\n"), r.stdout
    r = _pip(guarded, "-c", ".venv/lhpc-rns-constraint.txt", "lxmflike")
    assert r.returncode == 0, r.stderr
    assert _installed(guarded) == ("1.5.5", "NODE = 1\n")


@pytest.mark.slow                # real venvs (ensurepip) and real pip runs
def test_the_nodes_later_install_keeps_its_own_wheel(tmp_path):
    """The node's sequence: `build`, then its later install of a package that admits a newer rns (loraham-rns-interface:
    rns>=1.4.0), under a forced reinstall from an index with a newer rns and a same-version one. With `-c` on the
    constraint `build` wrote, the node keeps its own wheel's code; without it (the control) the newer rns wins."""
    index = tmp_path / "index"
    _dist(index, "interfacelike", "1.0", "rns>=1.4.0")
    _wheel(index, "9.9.9", "NEWER = 1\n")
    _wheel(index, "1.5.5", "SAME_VERSION_OTHER_CODE = 1\n")
    node = _node(tmp_path)
    assert _run(node, "build", ".venv/lhpc-wheel").returncode == 0
    _force_reinstall_from(node, index)
    r = _pip(node, "interfacelike")
    assert r.returncode == 0 and _installed(node)[0] == "9.9.9", r.stdout          # the control: replaced
    assert _run(node, "build", ".venv/lhpc-wheel").returncode == 0
    r = _pip(node, "-c", ".venv/lhpc-rns-constraint.txt", "interfacelike")
    assert r.returncode == 0, r.stderr
    assert _installed(node) == ("1.5.5", "NODE = 1\n")


@pytest.mark.slow                # real venvs (ensurepip) and real pip runs
def test_a_client_needing_another_rns_fails_its_install(tmp_path):
    """Only a release that wants a newer rns exists: the constrained install fails, the node's rns untouched."""
    wheel_dir, client = _node_wheel(tmp_path), _client(tmp_path)
    index = tmp_path / "index"
    _dist(index, "lxmflike", "2.0", "rns>=9")
    _wheel(index, "9.9.9", "NEWER = 1\n")
    assert _run(client, "install", str(wheel_dir)).returncode == 0
    r = _pip(client, "-c", ".venv/lhpc-rns-constraint.txt", "--find-links", str(index), "lxmflike")
    assert r.returncode != 0 and "ResolutionImpossible" in r.stderr, r.stderr      # the conflict, not a missing file
    assert _installed(client) == ("1.5.5", "NODE = 1\n")


@pytest.mark.slow                # real venvs (ensurepip) and real pip runs
def test_install_fails_without_a_wheel_even_when_rns_is_present(tmp_path):
    client = _client(tmp_path)
    other = _wheel(tmp_path / "other", "2.0.0", "OTHER = 1\n")
    subprocess.run([str(client / ".venv" / "bin" / "pip"), "install", "-q", "--no-index", str(other)], check=True)
    (tmp_path / "empty").mkdir()
    for wheel_dir in (tmp_path / "empty", tmp_path / "absent"):
        r = _run(client, "install", str(wheel_dir))
        assert r.returncode != 0 and "expected exactly one rns wheel" in r.stderr
    assert _installed(client) == ("2.0.0", "OTHER = 1\n")


@pytest.mark.slow                # real venvs (ensurepip) and real pip runs
def test_install_refuses_two_wheels(tmp_path):
    wheel_dir, client = _node_wheel(tmp_path), _client(tmp_path)
    _wheel(wheel_dir, "1.5.6", "NEWER = 1\n")
    r = _run(client, "install", str(wheel_dir))
    assert r.returncode != 0 and "found 2" in r.stderr


# ---- G2-RETI-WHEEL-3 B1: a later pip step's configuration can move WHERE it installs --------------------------------

def _client_sequence(comp_id: str):
    """The manifest's own steps of `comp_id` that run rns-wheel.sh, in order, as (mode, wheel dir template)."""
    from lhpc.core.manifest import load_manifest
    comp = next(c for st in load_manifest() for c in st.components if c.id == comp_id)
    return [tuple(s["argv"][2:4]) for s in comp.build_steps
            if len(s.get("argv") or []) >= 4 and s["argv"][1].endswith("scripts/rns-wheel.sh")]


def _moving_conf(venv_root: Path, index: Path, prefix: Path) -> None:
    """The venv's own pip.conf: every later install reinstalls, and installs under `prefix`, not the venv."""
    (venv_root / ".venv" / "pip.conf").write_text(
        f"[global]\nfind-links = {index}\n[install]\nforce-reinstall = true\nprefix = {prefix}\n")


@pytest.mark.slow                # real venvs (ensurepip) and real pip runs
@pytest.mark.parametrize("comp_id", ["nomadnet", "lxmd", "sideband"])
@pytest.mark.parametrize("moved", [True, False], ids=["prefix-moves-the-install", "plain"])
def test_a_client_whose_venv_no_longer_runs_the_node_wheel_fails_its_build(tmp_path, comp_id, moved):
    """The client's whole sequence as the manifest runs it: the wheel install, the client's own install with `-c` (a
    package needing rns), then every later step the manifest has that calls rns-wheel.sh. Under a per-venv pip.conf with
    an [install] prefix and force-reinstall, the client's own install uninstalls the venv's RNS and puts the node's
    wheel under the prefix: the build must fail, naming it. Without that configuration the same sequence passes."""
    wheel_dir = _wheel(tmp_path / "node-wheel", "1.5.5", 'NODE = 1\n__version__ = "1.5.5"\n').parent
    index, prefix = tmp_path / "index", tmp_path / "elsewhere"
    _dist(index, "clientlike", "1.0", "rns>=1.5.5")
    client = _client(tmp_path, comp_id)
    steps = _client_sequence(comp_id)
    assert steps[0] == ("install", "{runtime}/src/reticulum/.venv/lhpc-wheel"), steps
    assert _run(client, "install", str(wheel_dir)).returncode == 0
    if moved:
        _moving_conf(client, index, prefix)
    r = _pip(client, "-c", ".venv/lhpc-rns-constraint.txt", "--find-links", str(index), "clientlike")
    assert r.returncode == 0, r.stderr
    if moved:                    # the hazard itself: the venv's RNS is gone, the wheel landed under the prefix
        assert subprocess.run([str(client / ".venv" / "bin" / "python"), "-I", "-c", "import RNS"],
                              capture_output=True).returncode != 0
        assert list(prefix.rglob("RNS/__init__.py"))
    later = [_run(client, mode, str(wheel_dir)) for mode, _d in steps[1:]]
    failed = [p for p in later if p.returncode != 0]
    if moved:
        assert failed and "rns-wheel: check failed" in failed[0].stderr, [p.stderr for p in later]
    else:
        assert not failed and later, [p.stderr for p in later]


@pytest.mark.slow                # real venvs (ensurepip) and real pip runs
@pytest.mark.parametrize("moved", [True, False], ids=["prefix-moves-the-install", "plain"])
def test_the_node_whose_venv_no_longer_runs_its_wheel_fails_its_build(tmp_path, moved):
    """The node's sequence: `build`, its later install with `-c` (under a moving pip.conf, or plain), then the
    manifest's later rns-wheel.sh steps of the node. Moved: the venv's RNS is gone, the wheel sits under the prefix, and
    the build fails naming it. Plain: the same sequence passes."""
    index, prefix = tmp_path / "index", tmp_path / "elsewhere"
    _dist(index, "interfacelike", "1.0", "rns>=1.4.0")
    node = _node(tmp_path, code='NODE = 1\n__version__ = "1.5.5"\n')
    steps = _client_sequence("rns")
    assert steps[0] == ("build", ".venv/lhpc-wheel"), steps
    assert _run(node, "build", ".venv/lhpc-wheel").returncode == 0
    if moved:
        _moving_conf(node, index, prefix)
    r = _pip(node, "-c", ".venv/lhpc-rns-constraint.txt", "--find-links", str(index), "interfacelike")
    assert r.returncode == 0, r.stderr
    if moved:                    # the hazard itself: the venv's RNS is gone, the wheel landed under the prefix
        assert subprocess.run([str(node / ".venv" / "bin" / "python"), "-I", "-c", "import RNS"],
                              capture_output=True).returncode != 0
        assert list(prefix.rglob("RNS/__init__.py"))
    later = [_run(node, mode, ".venv/lhpc-wheel") for mode, _d in steps[1:]]
    failed = [p for p in later if p.returncode != 0]
    if moved:
        assert failed and "rns-wheel: check failed" in failed[0].stderr, [p.stderr for p in later]
    else:
        assert not failed and later, [p.stderr for p in later]


# ---- G2-RETI-WHEEL-5 N3: the check's place in each component's whole recipe ----------------------------------------

NODE_WHEEL = ".venv/lhpc-wheel"
CLIENT_WHEEL = "{runtime}/src/reticulum/.venv/lhpc-wheel"


def _steps(comp_id: str) -> list:
    from lhpc.core.manifest import load_manifest
    comp = next(c for st in load_manifest() for c in st.components if c.id == comp_id)
    return [list(s.get("argv") or []) for s in comp.build_steps]


def test_a_pip_install_is_recognised_in_both_forms():
    assert _is_pip_install([".venv/bin/pip", "install", "x"]) and _is_pip_install([".venv/bin/pip3", "install", "x"])
    assert _is_pip_install([".venv/bin/python", "-m", "pip", "install", "x"])
    assert _is_pip_install(["python3", "-m", "pip", "install", "-c", ".venv/lhpc-rns-constraint.txt", "x"])
    assert not _is_pip_install([".venv/bin/pip", "list"]) and not _is_pip_install([".venv/bin/python", "-c", "pass"])
    assert not _is_pip_install(["install", "-D", "a", "b"]) and not _is_pip_install([])


def _is_pip_install(argv: list) -> bool:
    """A pip install in either form the manifest could use: `<venv>/bin/pip install …` or `<python> -m pip install …`."""
    exe = argv[0].rsplit("/", 1)[-1] if argv else ""
    if exe.startswith("pip"):
        return argv[1:2] == ["install"]
    return exe.startswith("python") and argv[1:4] == ["-m", "pip", "install"]


def _constrained(argv: list) -> bool:
    return "-c" in argv and argv[argv.index("-c") + 1:argv.index("-c") + 2] == [".venv/lhpc-rns-constraint.txt"]


def _wheel_step(argv: list, mode: str) -> bool:
    return len(argv) >= 4 and argv[1].endswith("scripts/rns-wheel.sh") and argv[2] == mode


@pytest.mark.parametrize("comp_id, first, wheel", [("rns", "build", NODE_WHEEL), ("nomadnet", "install", CLIENT_WHEEL),
                                                   ("lxmd", "install", CLIENT_WHEEL), ("sideband", "install", CLIENT_WHEEL)])
def test_each_component_checks_its_wheel_once_after_its_final_pip_install(comp_id, first, wheel):
    """The whole recipe, not the filtered rns-wheel.sh steps: exactly one `check`, on the same wheel directory the
    component's `build`/`install` step used, placed RIGHT AFTER the last pip install of the component (or that wheel
    step, which installs with pip itself): nothing runs between them; every pip install after the wheel step is held to
    its constraint."""
    steps = _steps(comp_id)
    checks = [i for i, a in enumerate(steps) if _wheel_step(a, "check")]
    firsts = [i for i, a in enumerate(steps) if _wheel_step(a, first)]
    pips = [i for i, a in enumerate(steps) if _is_pip_install(a)]
    assert len(checks) == 1 and len(firsts) == 1, steps
    assert steps[checks[0]][3] == steps[firsts[0]][3] == wheel, steps
    assert checks[0] == max(pips + firsts) + 1, steps
    assert all(_constrained(a) for i, a in enumerate(steps) if _is_pip_install(a) and i > firsts[0]), steps
