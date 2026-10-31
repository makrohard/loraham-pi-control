#!/usr/bin/env python3
"""The wheels the slow-build lane's timed builds install, fetched before the lane (decision a).

    build-wheels.py steps <manifest> <baseline>
        the timed builds' `pip install` steps: one line per step, `<component> <args...>`
    build-wheels.py fetch <manifest> <baseline> <mirrors> <asset> <wheels> <transfers>
        check out each component's pinned source from <mirrors>, run every step of `steps` as
        `pip wheel -w <wheels>` from that source (plus the build requirements of every local
        project it names) and append each component's wall time to <transfers>

A timed build installs from these wheels with no index (the lane's pip.conf), so its timing holds
local work only. Every build the slow-target row times is covered: all components but the
baseline's `excluded` ones, a superset of the lane's stacks (an extra build only costs time).
Runs inside the lab image, so the wheels match the lane's Python. Stdlib only.
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
import tempfile
import time
import tomllib
from pathlib import Path

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("mirror_sources", HERE / "mirror-sources.py")
mirror_sources = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mirror_sources)

# `pip install` options with no `pip wheel` meaning. --no-build-isolation: the lane's venv has the
# build requirements installed by an earlier step; `pip wheel` builds in isolation instead.
NOT_FOR_WHEEL = {"--force-reinstall", "--no-build-isolation"}
# setuptools' own default when a project declares no [build-system] (PEP 518).
DEFAULT_BUILD_REQUIRES = ["setuptools>=40.8.0", "wheel"]


def _components(manifest: Path, baseline: Path):
    excluded = tomllib.loads(baseline.read_text()).get("excluded", {})
    for stack in tomllib.loads(manifest.read_text())["stack"]:
        yield from (c for c in stack.get("component", ()) if c["id"] not in excluded)


def steps(manifest: Path, baseline: Path) -> list[tuple[str, list[str]]]:
    """(component, the arguments after `install`) of every timed build's `<venv>/pip install`."""
    out = []
    for comp in _components(manifest, baseline):
        for step in comp.get("build_steps", ()):
            argv = [str(a) for a in step.get("argv", ())]
            if len(argv) > 1 and argv[0].endswith("/pip") and argv[1] == "install":
                out.append((comp["id"], argv[2:]))
    return out


RNS_WHEEL = ["bash", "{asset}/scripts/rns-wheel.sh"]
RNS_CONSTRAINT = ".venv/lhpc-rns-constraint.txt"


def wheel_installs(manifest: Path, baseline: Path) -> dict[str, str]:
    """{component: the node's wheel dir} of every timed build that runs rns-wheel.sh: `build <dir>` in the
    node's own checkout, `install <dir>` in a client's. In the build that step writes
    <checkout>/.venv/lhpc-rns-constraint.txt (`rns @ file://<the wheel>`), and the component's later pip
    installs run with `-c` on it."""
    out = {}
    for comp in _components(manifest, baseline):
        for step in comp.get("build_steps", ()):
            argv = [str(a) for a in step.get("argv", ())]
            if argv[:2] == RNS_WHEEL and len(argv) == 4 and argv[2] == "install":
                out[comp["id"]] = argv[3]
            elif argv[:2] == RNS_WHEEL and len(argv) == 4 and argv[2] == "build":
                out[comp["id"]] = f"{{runtime}}/{comp['source']['path']}/{argv[3]}"
    return out


def _node_wheel(project: Path, scratch: Path) -> Path:
    """The one wheel `project` (the node's checkout) builds: what rns-wheel.sh installs."""
    scratch.mkdir(parents=True, exist_ok=True)
    _run([sys.executable, "-m", "pip", "wheel", "-q", "--no-deps", "-w", str(scratch), str(project)])
    found = sorted(scratch.glob("rns-*.whl"))
    if len(found) != 1:
        raise SystemExit(f"expected one rns wheel from {project}, got {[p.name for p in found]}")
    return found[0]


def _local_projects(args: list[str], cwd: Path) -> list[Path]:
    """The arguments that name a local directory (not an option, not a -c/-r file)."""
    out, skip = [], False
    for a in args:
        if skip or a.startswith("-"):
            skip = a in ("-c", "-r", "--constraint", "--requirement")
            continue
        if (cwd / a).is_dir():
            out.append(cwd / a)
    return out


def _build_requires(project: Path) -> list[str]:
    pyproject = project / "pyproject.toml"
    if not pyproject.is_file():
        return DEFAULT_BUILD_REQUIRES
    system = tomllib.loads(pyproject.read_text()).get("build-system", {})
    return system.get("requires", DEFAULT_BUILD_REQUIRES)


def _run(argv: list[str], cwd: Path | None = None) -> None:
    """With the transfer backoff (retry.sh): a fetch fails the job only after it."""
    subprocess.run(["bash", "-c", f'. "{HERE}/retry.sh" && backoff "$@"', "backoff", *argv],
                   cwd=cwd, check=True)


def fetch(manifest: Path, baseline: Path, mirrors: Path, asset: Path, wheels: Path,
          transfers: Path) -> None:
    runtime = Path(tempfile.mkdtemp(prefix="build-wheels-"))
    sources = {c["id"]: c["source"] for c in _components(manifest, baseline)
               if (c.get("source") or {}).get("pin_commit")}
    todo = steps(manifest, baseline)
    # Each step runs from its component's source and may name other pinned sources by path.
    need = {sources[c]["path"] for c, _ in todo}
    installs = wheel_installs(manifest, baseline)
    need |= {s["path"] for s in sources.values()
             if any(f"{{runtime}}/{s['path']}" in a for _, args in todo for a in args)
             or any(d.startswith(f"{{runtime}}/{s['path']}/") for d in installs.values())}
    for src in {s["path"]: s for s in sources.values()}.values():
        if src["path"] in need:
            dest = runtime / src["path"]
            mirror = mirrors / mirror_sources.mirror_name(src["remote"])
            subprocess.run(["git", "-c", "safe.directory=*", "clone", "--quiet", str(mirror),
                            str(dest)], check=True)
            subprocess.run(["git", "-C", str(dest), "-c", "advice.detachedHead=false",
                            "checkout", "--quiet", src["pin_commit"]], check=True)
    # The build's rns-wheel.sh steps are not pip steps, so they are not replayed; the constraint they write is (a
    # wheel built here from the node's checkout), so the components' `-c` installs resolve here as they will in the
    # timed build.
    built: dict[Path, Path] = {}
    for component, wheel_dir in installs.items():
        project = Path(wheel_dir.replace("{runtime}", str(runtime))).parent.parent
        if project not in built:
            built[project] = _node_wheel(project, runtime / ".rns-wheel" / str(len(built)))
        constraint = runtime / sources[component]["path"] / RNS_CONSTRAINT
        constraint.parent.mkdir(parents=True, exist_ok=True)
        constraint.write_text(f"rns @ {built[project].resolve().as_uri()}\n")
    seconds: dict[str, float] = {}
    for component, step in todo:
        cwd = runtime / sources[component]["path"]
        args = [a.replace("{runtime}", str(runtime)).replace("{asset}", str(asset))
                .replace("{source}", str(cwd)) for a in step if a not in NOT_FOR_WHEEL]
        t0 = time.monotonic()
        _run([sys.executable, "-m", "pip", "wheel", "-q", "-w", str(wheels), *args], cwd=cwd)
        for project in _local_projects(args, cwd):
            _run([sys.executable, "-m", "pip", "wheel", "-q", "-w", str(wheels),
                  *_build_requires(project)])
        seconds[component] = seconds.get(component, 0.0) + time.monotonic() - t0
    with transfers.open("a") as f:
        for component, s in seconds.items():
            f.write(f'[[transfer]]\nwhat = "wheels for the build of {component}"\n'
                    f"seconds = {s:.1f}\n\n")


def main(argv: list[str]) -> int:
    cmd, *args = argv or ["help"]
    if cmd == "steps" and len(args) == 2:
        for component, rest in steps(Path(args[0]), Path(args[1])):
            print(component, *rest)
    elif cmd == "fetch" and len(args) == 6:
        fetch(*map(Path, args))
    else:
        print(__doc__, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
