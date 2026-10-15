"""`.github/scripts/mirror-sources.py`: the lanes' git remotes are served from local mirrors.

The workflow fetches the mirrors before the lane and proves them complete with no network; these
cases pin what that proof stands on: the remote list derived from the manifest, the exact git
configuration the lane writes to ~/.gitconfig, that a clone through it resolves locally while a
remote without a mirror fails, and that the redirection survives the product's own command
runner, which keeps HOME and drops GIT_CONFIG_GLOBAL. A new invariant (new tooling): negative
cases instead of a parent-red.
"""
import os
import subprocess
import sys

import pytest

import repo_paths

SCRIPT = repo_paths.REPO / ".github" / "scripts" / "mirror-sources.py"
MANIFEST = repo_paths.REPO / "lhpc" / "data" / "manifest.example.toml"
GIT = ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", "-c", "commit.gpgsign=false"]


def _run(*args, **kw):
    return subprocess.run([sys.executable, str(SCRIPT), *map(str, args)], capture_output=True,
                          text=True, check=False, **kw)


# The 14 remotes the testlab and release-verify lanes cloned in green run 37426359349 (their
# `[clone] … from <remote>` lines): the derived list must cover at least these.
CLONED_IN_A_GREEN_RUN = {
    f"https://github.com/{r}.git" for r in (
        "makrohard/LoRaHAM_Daemon", "makrohard/loraham-kiss-tnc", "makrohard/LoRaHAM_Voice",
        "markqvist/LXMF", "liamcottle/reticulum-meshchat", "makrohard/meshcom-qemu-raspi",
        "meshcore-dev/meshcore-cli", "openhop-dev/openhop_core", "adradr/meshcore-webui",
        "markqvist/NomadNet", "openhop-dev/openhop_repeater", "markqvist/Reticulum",
        "makrohard/loraham-rns-interface", "markqvist/Sideband")}


def _manifest(tmp_path, components):
    """A manifest of one stack whose components are `{id: remote}`."""
    comps = "".join(f'[[stack.component]]\nid = "{c}"\n[stack.component.source]\nremote = "{r}"\n'
                    for c, r in components.items())
    path = tmp_path / "m.toml"
    path.write_text('[[stack]]\nid = "s"\n' + comps)
    return path


def _not_mirrored():
    import importlib.util
    spec = importlib.util.spec_from_file_location("mirror_sources", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.NOT_MIRRORED


def test_the_mirror_list_covers_every_remote_the_lanes_cloned():
    r = _run("list", MANIFEST)
    remotes = r.stdout.split()
    assert r.returncode == 0 and len(remotes) == len(set(remotes))
    assert CLONED_IN_A_GREEN_RUN <= set(remotes)


def _excluded_components():
    return {c: f"https://example.invalid/excluded/{c}.git" for c in sorted(_not_mirrored())}


def test_a_remote_added_to_the_manifest_is_mirrored_by_default(tmp_path):
    new = "https://example.invalid/owner/new-stack.git"
    manifest = _manifest(tmp_path, {**_excluded_components(), "new-stack": new})
    assert _run("list", manifest).stdout.split() == [new]


def test_a_new_component_on_an_excluded_components_remote_is_mirrored(tmp_path):
    """The twin: exclusion is by component, so a lane component reusing the remote of an
    excluded one (here the meshtastic firmware) does not escape the mirror."""
    reused = "https://github.com/meshtastic/firmware.git"
    assert reused not in _run("list", MANIFEST).stdout.split()
    manifest = tmp_path / "m.toml"
    manifest.write_text(MANIFEST.read_text() + '\n[[stack]]\nid = "new"\n[[stack.component]]\n'
                        f'id = "new-firmware"\n[stack.component.source]\nremote = "{reused}"\n')
    assert reused in _run("list", manifest).stdout.split()


def test_an_exclusion_the_manifest_no_longer_names_is_an_error(tmp_path):
    excluded = _excluded_components()
    gone = sorted(excluded)[0]
    r = _run("list", _manifest(tmp_path, {c: u for c, u in excluded.items() if c != gone}))
    assert r.returncode == 1 and r.stdout == "" and gone in r.stderr


def test_the_git_config_is_exact(tmp_path):
    remotes = tmp_path / "remotes.txt"
    remotes.write_text("https://github.com/a/b.git\nhttps://example.invalid/c\n")
    assert _run("config", remotes, "/srcmirror").stdout == (
        "[safe]\n\tdirectory = *\n"
        '[url "file:///srcmirror/github.com_a_b.git"]\n\tinsteadOf = https://github.com/a/b.git\n'
        '[url "file:///srcmirror/example.invalid_c.git"]\n\tinsteadOf = https://example.invalid/c\n')


def _mirrored(tmp_path, remote):
    """A mirror directory as the acquisition step leaves it, serving one commit for `remote`."""
    upstream = tmp_path / "upstream"
    subprocess.run([*GIT, "init", "-q", str(upstream)], check=True)
    (upstream / "f").write_text("x\n")
    subprocess.run([*GIT, "-C", str(upstream), "add", "f"], check=True)
    subprocess.run([*GIT, "-C", str(upstream), "commit", "-q", "-m", "c"], check=True)
    mirror = tmp_path / "srcmirror"
    mirror.mkdir()
    name = _run("name", remote).stdout.strip()
    subprocess.run(["git", "clone", "-q", "--mirror", str(upstream), str(mirror / name)], check=True)
    (mirror / "remotes.txt").write_text(remote + "\n")
    (mirror / "gitconfig").write_text(_run("config", mirror / "remotes.txt", mirror).stdout)
    return mirror


def test_a_clone_through_the_config_resolves_to_the_mirror_and_a_missing_one_fails(tmp_path):
    # `.invalid` never resolves: only the `insteadOf` rewrite can make this clone succeed.
    remote = "https://example.invalid/owner/up.git"
    mirror = _mirrored(tmp_path, remote)
    ok = _run("verify", mirror)
    assert ok.returncode == 0 and ok.stdout == f"ok  {remote}\n"
    (mirror / "remotes.txt").write_text(remote + "\nfile:///nonexistent/not-mirrored.git\n")
    bad = _run("verify", mirror)
    assert bad.returncode == 1 and "NOT MIRRORED  file:///nonexistent/not-mirrored.git" in bad.stdout


# The product clones with RealCommandRunner, whose children get `_FIXED_ENV` plus the call's own
# env: the lane's config reaches git only through what that runner forwards.
RUNNER_CLONE = ("import sys; from lhpc.core.probes.backends import RealCommandRunner; "
                "sys.exit(RealCommandRunner().run(['git', 'clone', '-q', *sys.argv[1:]], 60)"
                ".returncode)")


@pytest.mark.parametrize("via, redirected", [("home", True), ("git_config_global", False)])
def test_only_a_gitconfig_in_home_redirects_a_clone_through_the_products_runner(
        tmp_path, via, redirected):
    """Run 38823954555 passed with GIT_CONFIG_GLOBAL exported, but the runner drops it, so the
    product's clones never used the mirrors. The remote does not exist: only the redirection can
    make the clone succeed, and no network is touched either way."""
    remote = "file:///nonexistent/upstream/up.git"
    mirror = _mirrored(tmp_path, remote)
    home = tmp_path / "home"
    home.mkdir()
    env = {"PATH": os.environ["PATH"], "PYTHONPATH": str(repo_paths.REPO), "HOME": str(home)}
    if via == "home":
        (home / ".gitconfig").write_text((mirror / "gitconfig").read_text())
    else:
        env["GIT_CONFIG_GLOBAL"] = str(mirror / "gitconfig")
    clone = subprocess.run([sys.executable, "-c", RUNNER_CLONE, remote, str(tmp_path / "co")],
                           env=env, capture_output=True, text=True, check=False)
    assert (clone.returncode == 0) is redirected
    assert (tmp_path / "co" / "f").is_file() is redirected
    check = _run("resolved", mirror, env=env)
    expected = (f"ok  {remote}\n" if redirected
                else f"NOT REDIRECTED  {remote}  (resolves to {remote})\n")
    assert (check.returncode, check.stdout) == (0 if redirected else 1, expected), check.stderr


def test_the_slow_build_acquisition_reads_its_versions_from_the_manifest():
    """New tooling (an invariant, a negative case below): the pinned Graywolf release and the
    Meshtastic CLI venv's requirements, exactly as the manifest's build steps carry them."""
    gw = _run("graywolf-version", MANIFEST)
    cli = _run("cli-requirements", MANIFEST)
    assert gw.returncode == 0 and gw.stdout.strip().count(".") == 2
    assert f'"{gw.stdout.strip()}"' in MANIFEST.read_text()
    reqs = cli.stdout.split()
    assert cli.returncode == 0 and [r.split("==")[0] for r in reqs] == ["meshtastic", "pycryptodomex"]


def test_a_manifest_without_those_steps_is_an_error(tmp_path):
    manifest = tmp_path / "m.toml"
    manifest.write_text('[[stack]]\nid = "s"\n[[stack.component]]\nid = "c"\n')
    gw, cli = _run("graywolf-version", manifest), _run("cli-requirements", manifest)
    assert gw.returncode == 1 and f"no graywolf-fetch.sh step in {manifest}" in gw.stderr
    assert cli.returncode == 1 and f"no `pip install meshtastic==…` step in {manifest}" in cli.stderr
