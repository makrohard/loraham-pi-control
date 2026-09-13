"""Packaged data assets resolve via importlib.resources, so the controller works as an
installed wheel, not only from a source checkout. (The full isolated wheel-install smoke test is
in docs/maintenance.md / the milestone commands.)"""

import os
import subprocess
import tomllib

import pytest

import repo_paths
from lhpc.core.assets import asset_path, asset_text
from lhpc.core.manifest import default_manifest_path, load_manifest


def test_data_assets_resolve():
    for name in ("manifest.example.toml", "defaults.toml",
                 "local.example.toml", "secrets.example.toml"):
        assert asset_path(name).exists(), name
    assert "[[stack]]" in asset_text("manifest.example.toml")


def test_default_manifest_is_package_data_not_repo_root():
    p = default_manifest_path()
    assert p.exists()
    # The default manifest now lives inside the package (lhpc/data), never at a
    # repo-root ../config reachable only from a source checkout.
    assert p.parts[-2:] == ("data", "manifest.example.toml")
    assert "lhpc" in p.parts


def test_manifest_loads_from_package_data():
    stacks = load_manifest()
    assert any(s.id == "daemon" for s in stacks)


def test_every_shipped_asset_tree_is_in_the_package_data_allow_list():
    """`package-data` is an explicit allow-list, so a new asset tree that nobody adds to it ships
    in a SOURCE CHECKOUT and vanishes from the BUILT WHEEL — the frontend is simply absent and the
    stack serves nothing. A source-tree check cannot catch that, which is why this reads
    pyproject.toml: every directory under lhpc/data/ that looks like a shipped tree must be
    claimed, and every constraints file with it.

    This guards the DECLARATION. That the wheel really carries the files is proven by the
    isolated wheel-install smoke test in docs/maintenance.md, which the suite deliberately does
    not run (see this module's header)."""
    root = repo_paths.REPO
    patterns = tomllib.loads((root / "pyproject.toml").read_text())["tool"]["setuptools"][
        "package-data"]["lhpc"]
    data = root / "lhpc" / "data"

    for tree in sorted(p for p in data.iterdir() if p.is_dir() and p.name.endswith("-dist")):
        assert f"data/{tree.name}/**/*" in patterns, (
            f"{tree.name} ships in a source checkout but would be missing from a built wheel — "
            f"add 'data/{tree.name}/**/*' to [tool.setuptools.package-data]")

    for cons in sorted(data.glob("*constraints.txt")):
        assert f"data/{cons.name}" in patterns, (
            f"{cons.name} is not in package-data; a wheel install would build against "
            f"unpinned versions instead of the measured ones")


def test_the_meshchat_frontend_is_present_and_looks_built():
    """The bundle is prebuilt package data (upstream gitignores /public/ and builds it with vite;
    no npm on the box). Assert the entry point exists rather than a file count, which would churn
    on every upstream bump."""
    from lhpc.core.assets import asset_path
    index = asset_path("meshchat-dist/index.html")
    assert index.exists(), "meshchat-dist/index.html missing — the UI would 404"
    assert (index.parent / "assets").is_dir(), "meshchat-dist/assets missing — index.html is inert"


def test_every_file_in_a_shipped_asset_tree_is_tracked_by_git():
    """What a fresh clone gets must equal what the developer sees. `.gitignore` carries generic
    build rules (dist/, build/, *.log, *.key …) that also match paths INSIDE a vendored bundle:
    `dist/` silently swallowed two files of MeshChat's rnode-flasher, `git add -A` said nothing,
    and the wheel built from the working tree still contained them — so only a box installing
    from a clone would have found the UI incomplete. Compare tracked files against the disk."""
    root = repo_paths.REPO
    if not (root / ".git").exists():
        # A source export has nothing to compare; CI runs from actions/checkout and MUST have it.
        if os.environ.get("CI"):
            pytest.fail("no .git in this checkout — CI must run from a git checkout for this guard")
        pytest.skip("not a git checkout")
    for tree in sorted(p for p in (root / "lhpc" / "data").iterdir()
                       if p.is_dir() and p.name.endswith("-dist")):
        rel = tree.relative_to(root)
        tracked = set(subprocess.run(["git", "-C", str(root), "ls-files", "-z", "--", str(rel)],
                                     capture_output=True, text=True, check=True)
                      .stdout.split("\0")) - {""}
        on_disk = {str(p.relative_to(root)) for p in tree.rglob("*") if p.is_file()}
        assert on_disk - tracked == set(), (
            f"{rel}: present in this checkout but NOT tracked — a clone ships without them; "
            f"a .gitignore rule matched a path inside the bundle")
