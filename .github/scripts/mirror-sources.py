#!/usr/bin/env python3
"""The upstream git sources the testlab and release-verify lanes clone, served from local mirrors.

    mirror-sources.py list <manifest>          every manifest remote but NOT_MIRRORED's
    mirror-sources.py name <remote>            the mirror directory name of one remote
    mirror-sources.py config <remotes> <dir>   a git config: each remote → file://<dir>/<name>
    mirror-sources.py verify <dir>             clone every remote in <dir>/remotes.txt with
                                               <dir>/gitconfig as ~/.gitconfig; exit 1 if any fails
    mirror-sources.py resolved <dir>           through lhpc's own command runner, every remote in
                                               <dir>/remotes.txt resolves to its mirror; exit 1 if not
    mirror-sources.py graywolf-version <manifest>   the Graywolf release the manifest pins
    mirror-sources.py cli-requirements <manifest>   the Meshtastic CLI venv's pip requirements

The last two serve the slow-build job's acquisition of the timed operations' transfers.

The workflow fetches each mirror before the lane (with the transfer backoff) and the lane copies
<dir>/gitconfig to the lab user's ~/.gitconfig, so the product's own `git clone <remote>` resolves
to the mirror through git's `insteadOf`, unchanged. The file, not GIT_CONFIG_GLOBAL: lhpc's
command runner gives its children a fixed environment that keeps HOME and drops
GIT_CONFIG_GLOBAL (`_FIXED_ENV`, lhpc/core/probes/backends.py). `verify` runs in a container with
no network, so a lane remote missing from the list fails there instead of reaching the network;
`resolved` runs in the lane and proves the redirection reaches git through that runner. Stdlib
only, except `resolved`, which imports lhpc: the rest runs on the CI host before the product is
installed.
"""
from __future__ import annotations

import re
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

# Every remote the manifest names is mirrored, except those of these components, each with its
# reason. Deriving the list from the manifest, not from a list of lane components, means a
# component added later is mirrored by default: an unneeded mirror only costs time, a missing one
# would clone live. Keyed by component, not by remote: a new component naming an excluded
# component's remote still gets it mirrored.
NOT_MIRRORED = {
    "radiolib": "the lab substitutes a local fake for RadioLib",
    "meshtastic": "meshtastic comes from the binary channel on the lab's aarch64 runner",
    "meshcom-firmware": "meshcom comes from the binary channel",
    "meshcom-bridge": "meshcom comes from the binary channel",
}


def lane_remotes(manifest: Path) -> list[str]:
    """Every distinct remote of a component not in NOT_MIRRORED. An exclusion naming a component
    the manifest no longer has is an error, so the table cannot silently go stale."""
    found, ids = set(), set()
    for stack in tomllib.loads(manifest.read_text())["stack"]:
        for comp in stack.get("component", ()):
            ids.add(comp.get("id"))
            remote = (comp.get("source") or {}).get("remote", "")
            if remote and comp.get("id") not in NOT_MIRRORED:
                found.add(remote)
    stale = sorted(set(NOT_MIRRORED) - ids)
    if stale:
        raise SystemExit(f"NOT_MIRRORED names components {manifest} no longer has: {', '.join(stale)}")
    return sorted(found)


def mirror_name(remote: str) -> str:
    """`https://github.com/owner/repo.git` → `github.com_owner_repo.git` (one directory per remote)."""
    name = re.sub(r"^[a-z]+://", "", remote).strip("/")
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", name)
    return name if name.endswith(".git") else name + ".git"


def git_config(remotes: list[str], directory: str) -> str:
    """`insteadOf` for every remote, and `safe.directory = *`: the mirrors are mounted read-only
    and owned by the runner's user, not by the lab user who clones from them."""
    out = ["[safe]\n\tdirectory = *\n"]
    for remote in remotes:
        out.append(f'[url "file://{directory}/{mirror_name(remote)}"]\n\tinsteadOf = {remote}\n')
    return "".join(out)


def _build_steps(manifest: Path):
    for stack in tomllib.loads(manifest.read_text())["stack"]:
        for comp in stack.get("component", ()):
            for step in comp.get("build_steps", ()):
                yield [str(a) for a in step.get("argv", ())]


def graywolf_version(manifest: Path) -> str:
    """The version argument of the manifest's `graywolf-fetch.sh <dest> <version>` step."""
    for argv in _build_steps(manifest):
        for i, tok in enumerate(argv):
            if tok.endswith("graywolf-fetch.sh") and len(argv) > i + 2:
                return argv[i + 2]
    raise SystemExit(f"no graywolf-fetch.sh step in {manifest}")


def cli_requirements(manifest: Path) -> str:
    """The requirements of the `pip install … meshtastic==<v> …` build step, space-separated."""
    for argv in _build_steps(manifest):
        if "install" in argv and any(a.startswith("meshtastic==") for a in argv):
            return " ".join(a for a in argv[argv.index("install") + 1:] if not a.startswith("-"))
    raise SystemExit(f"no `pip install meshtastic==…` step in {manifest}")


def _home_with(gitconfig: Path, home: str) -> dict:
    """The environment git sees in the lane: a $HOME whose .gitconfig is `gitconfig`."""
    Path(home, ".gitconfig").write_text(gitconfig.read_text())
    return {"HOME": home, "GIT_CONFIG_NOSYSTEM": "1", "GIT_TERMINAL_PROMPT": "0",
            "PATH": "/usr/bin:/bin"}


def verify(directory: Path) -> int:
    failed = 0
    with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as home:
        env = _home_with(directory / "gitconfig", home)
        for i, remote in enumerate((directory / "remotes.txt").read_text().split()):
            r = subprocess.run(["git", "clone", "--quiet", "--no-checkout", remote, f"{tmp}/{i}"],
                               capture_output=True, text=True, env=env, check=False)
            failed += r.returncode != 0
            print(f"{'ok' if r.returncode == 0 else 'NOT MIRRORED'}  {remote}"
                  + (f"  ({' '.join(r.stderr.split())[:200]})" if r.returncode else ""))
    return 1 if failed else 0


def resolved(directory: Path) -> int:
    """`git ls-remote --get-url` applies `insteadOf` without touching the network; run through
    the runner the product clones with, it prints what that clone would fetch from."""
    from lhpc.core.probes.backends import RealCommandRunner
    runner, failed = RealCommandRunner(), 0
    for remote in (directory / "remotes.txt").read_text().split():
        want = f"file://{directory}/{mirror_name(remote)}"
        got = runner.run(["git", "ls-remote", "--get-url", remote], 30).stdout.strip()
        failed += got != want
        print(f"{'ok' if got == want else 'NOT REDIRECTED'}  {remote}"
              + ("" if got == want else f"  (resolves to {got or 'nothing'})"))
    return 1 if failed else 0


def main(argv: list[str]) -> int:
    cmd, *args = argv or ["help"]
    if cmd == "list" and len(args) == 1:
        print("\n".join(lane_remotes(Path(args[0]))))
    elif cmd == "name" and len(args) == 1:
        print(mirror_name(args[0]))
    elif cmd == "config" and len(args) == 2:
        sys.stdout.write(git_config(Path(args[0]).read_text().split(), args[1]))
    elif cmd == "verify" and len(args) == 1:
        return verify(Path(args[0]))
    elif cmd == "resolved" and len(args) == 1:
        return resolved(Path(args[0]))
    elif cmd == "graywolf-version" and len(args) == 1:
        print(graywolf_version(Path(args[0])))
    elif cmd == "cli-requirements" and len(args) == 1:
        print(cli_requirements(Path(args[0])))
    else:
        print(__doc__, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
