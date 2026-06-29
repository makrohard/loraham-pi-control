"""P0.8 — packaged data assets resolve via importlib.resources, so the controller
works as an installed wheel, not only from a source checkout. (The full isolated
wheel-install smoke test is in docs/hardening-0.1.md / the milestone commands.)"""

from lhpc.core.assets import asset_path, asset_text
from lhpc.core.manifest import default_manifest_path, load_manifest


def test_data_assets_resolve():
    for name in ("manifest.example.toml", "defaults.toml", "profiles.example.toml",
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
