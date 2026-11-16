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


def test_upstream_checksum_lookup_survives_a_reader_that_stops_at_the_first_match(tmp_path):
    """--from-upstream reads the release's checksums.txt and awk stops at the .deb's line. With
    the line first in a long file, a pipe from printf would leave the writer to be killed by
    SIGPIPE, and under pipefail the script would end with rc 141 and an empty stderr."""
    sha = "a" * 64
    ck = tmp_path / "checksums.txt"
    ck.write_text(f"{sha}  graywolf_0.15.0_arm64.deb\n"
                  + "".join(f"{i:064x}  graywolf_0.15.0_other{i}.tar.gz\n" for i in range(6000)))
    shims = tmp_path / "shims"
    shims.mkdir()
    for name, body in {**SHIMS, "curl": ('#!/bin/sh\ncase "$*" in *checksums.txt) cat "$CK_FILE";;'
                                         ' *) exit 22;; esac\n')}.items():
        (shims / name).write_text(body)
        (shims / name).chmod(0o755)
    r = subprocess.run(["bash", str(asset_path("scripts/graywolf-fetch.sh")), str(tmp_path / "gw"),
                        "0.15.0", "--from-upstream"], capture_output=True, text=True, check=False,
                       env={"PATH": f"{shims}:/usr/bin:/bin", "CK_FILE": str(ck)})
    assert r.returncode == 1, (r.returncode, r.stderr)
    assert f"upstream sha256 for graywolf_0.15.0_arm64.deb: {sha}" in r.stdout
    assert "download failed" in r.stderr
