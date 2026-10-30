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
    assert (guarded / ".venv" / "lhpc-rns-constraint.txt").read_text() == "rns==1.5.5\n"
    r = _pip(control, "--find-links", str(index), "lxmflike")
    assert r.returncode == 0 and _installed(control)[0] == "9.9.9", r.stdout       # the control: replaced
    r = _pip(guarded, "-c", ".venv/lhpc-rns-constraint.txt", "--find-links", str(index), "lxmflike")
    assert r.returncode == 0, r.stderr
    assert _installed(guarded) == ("1.5.5", "NODE = 1\n") and "lxmflike-1.0" in r.stdout


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
