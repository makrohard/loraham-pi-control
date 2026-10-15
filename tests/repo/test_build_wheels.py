"""`.github/scripts/build-wheels.py`: the wheels every timed slow-build build installs are fetched
before the lane (decision a). These cases pin which steps it covers and how each one becomes a
`pip wheel` from the pinned source; the fetch itself is the slow-build job's acquisition step. A
new invariant (new tooling): negative cases instead of a parent-red.
"""
import importlib.util
import re
import subprocess
import sys

import repo_paths

SCRIPT = repo_paths.REPO / ".github" / "scripts" / "build-wheels.py"
MANIFEST = repo_paths.REPO / "lhpc" / "data" / "manifest.example.toml"
BASELINE = repo_paths.REPO / "tests" / "data" / "slow-target-builds.toml"
GIT = ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", "-c", "commit.gpgsign=false"]


def _module():
    spec = importlib.util.spec_from_file_location("build_wheels", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_every_pip_step_of_a_timed_build_is_covered_and_no_excluded_one():
    import tomllib
    excluded = tomllib.loads(BASELINE.read_text())["excluded"]
    want = []
    for stack in tomllib.loads(MANIFEST.read_text())["stack"]:
        for comp in stack.get("component", ()):
            for step in comp.get("build_steps", ()):
                argv = [str(a) for a in step["argv"]]
                if argv[0].endswith("/pip") and argv[1] == "install" and comp["id"] not in excluded:
                    want.append(f"{comp['id']} {' '.join(argv[2:])}")
    r = subprocess.run([sys.executable, str(SCRIPT), "steps", MANIFEST, BASELINE],
                       capture_output=True, text=True, check=True)
    assert r.stdout.splitlines() == want and len(want) == 13
    # sideband's build installs with pip too, but the row excludes it (GTK/Kivy only).
    assert "sideband" in excluded and not any(s.startswith("sideband ") for s in want)


def _repo(path, files):
    subprocess.run([*GIT, "init", "-q", str(path)], check=True)
    for name, text in files.items():
        (path / name).write_text(text)
    subprocess.run([*GIT, "-C", str(path), "add", "."], check=True)
    subprocess.run([*GIT, "-C", str(path), "commit", "-q", "-m", "c"], check=True)
    return subprocess.run(["git", "-C", str(path), "rev-parse", "HEAD"], capture_output=True,
                          text=True, check=True).stdout.strip()


def test_a_build_step_becomes_pip_wheel_from_its_pinned_source(tmp_path, monkeypatch):
    """One step that names its own source, another pinned source and a constraints file: run
    from the component's checkout, with --force-reinstall dropped, then the build requirements of
    both local projects (the default one, and hatchling where pyproject declares it)."""
    bw = _module()
    mirrors = tmp_path / "mirrors"
    mirrors.mkdir()
    pins = {}
    for name, files in (("a", {"setup.py": ""}),
                        ("b", {"pyproject.toml": '[build-system]\nrequires = ["hatchling"]\n'})):
        pins[name] = _repo(tmp_path / f"up-{name}", files)
        remote = f"https://example.invalid/x/{name}.git"
        subprocess.run(["git", "clone", "-q", "--mirror", str(tmp_path / f"up-{name}"),
                        str(mirrors / bw.mirror_sources.mirror_name(remote))], check=True)
    manifest = tmp_path / "m.toml"
    manifest.write_text(
        '[[stack]]\nid = "s"\n'
        '[[stack.component]]\nid = "a"\n'
        '[stack.component.source]\npath = "src/a"\nremote = "https://example.invalid/x/a.git"\n'
        f'pin_commit = "{pins["a"]}"\n'
        '[[stack.component.build_steps]]\nargv = [".venv/bin/pip", "install", '
        '"--force-reinstall", ".", "{runtime}/src/b", "-c", "{asset}/c.txt"]\n'
        '[[stack.component]]\nid = "b"\n'
        '[stack.component.source]\npath = "src/b"\nremote = "https://example.invalid/x/b.git"\n'
        f'pin_commit = "{pins["b"]}"\n'
        '[[stack.component]]\nid = "skipped"\n'
        '[[stack.component.build_steps]]\nargv = [".venv/bin/pip", "install", "x"]\n')
    baseline = tmp_path / "b.toml"
    baseline.write_text('[excluded]\nskipped = "not timed"\n')
    calls = []
    # Stubs the collaborator, the network fetch (pip wheel under the backoff): records it.
    monkeypatch.setattr(bw, "_run", lambda argv, cwd=None: calls.append((argv, cwd)))
    monkeypatch.setattr(bw.tempfile, "mkdtemp", lambda prefix: str(tmp_path / "rt"))
    wheels, transfers = tmp_path / "w", tmp_path / "transfers.toml"
    bw.fetch(manifest, baseline, mirrors, tmp_path / "asset", wheels, transfers)
    rt = tmp_path / "rt"
    pip = [sys.executable, "-m", "pip", "wheel", "-q", "-w", str(wheels)]
    assert calls == [
        ([*pip, ".", f"{rt}/src/b", "-c", f"{tmp_path}/asset/c.txt"], rt / "src" / "a"),
        ([*pip, "setuptools>=40.8.0", "wheel"], None),
        ([*pip, "hatchling"], None),
    ]
    for name in ("a", "b"):
        head = subprocess.run(["git", "-C", str(rt / "src" / name), "rev-parse", "HEAD"],
                              capture_output=True, text=True, check=True).stdout.strip()
        assert head == pins[name]
    assert re.fullmatch(r'\[\[transfer\]\]\nwhat = "wheels for the build of a"\nseconds = '
                        r"\d+\.\d\n\n", transfers.read_text())
