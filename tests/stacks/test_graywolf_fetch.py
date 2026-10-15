"""graywolf-fetch.sh's download base: GitHub by default, `GRAYWOLF_BASE_URL` when set (the test
lab's slow-build serves a pre-fetched copy, so the timed fetch measures local work). The script
runs for real with `curl`, `dpkg` and `dpkg-deb` replaced on PATH by recorders: no network, and
the URL it asked for is read back exactly.
"""
import subprocess

import pytest

from lhpc.core.assets import asset_path

SHIMS = {
    "curl": '#!/bin/sh\nprintf "%s\\n" "$@" >> "$CURL_LOG"\nexit 22\n',   # every download fails
    "dpkg": "#!/bin/sh\necho arm64\n",
    "dpkg-deb": "#!/bin/sh\nexit 0\n",
}


def _fetch(tmp_path, env_extra):
    shims = tmp_path / "shims"
    shims.mkdir()
    for name, body in SHIMS.items():
        (shims / name).write_text(body)
        (shims / name).chmod(0o755)
    log = tmp_path / "curl.log"
    r = subprocess.run(["bash", str(asset_path("scripts/graywolf-fetch.sh")), str(tmp_path / "gw"),
                        "0.14.14"], capture_output=True, text=True, check=False,
                       env={"PATH": f"{shims}:/usr/bin:/bin", "CURL_LOG": str(log), **env_extra})
    return r, log.read_text().split() if log.exists() else []


@pytest.mark.parametrize("env_extra, url", [
    ({}, "https://github.com/chrissnell/graywolf/releases/download/v0.14.14/graywolf_0.14.14_arm64.deb"),
    ({"GRAYWOLF_BASE_URL": "file:///srv/gw"}, "file:///srv/gw/v0.14.14/graywolf_0.14.14_arm64.deb"),
], ids=["default-github", "base-url-override"])
def test_the_download_base_is_github_unless_overridden(tmp_path, env_extra, url):
    r, curl_args = _fetch(tmp_path, env_extra)
    assert r.returncode == 1 and "download failed" in r.stderr
    assert curl_args[-1] == url
