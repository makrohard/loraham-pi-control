"""The testlab lane takes its transfers from copies acquired before it; the one transfer policy.

The lane's Python packages, Chromium and the Meshtastic web client's build.tar are acquired in a
workflow step of their own (with `backoff`), proved complete with no network, and read by the lane
with no index, through PLAYWRIGHT_BROWSERS_PATH and through LHPC_MESHTASTIC_WEB_TARBALL. On
2026-11-08 the browser lane's reset fetched the web client live and a GitHub 504 failed all 53 of
its tests (testlab 42477209850, first attempt).
"""
from __future__ import annotations

import os
import re
import subprocess
import sys

import pytest

import repo_paths

REPO = repo_paths.REPO
WORKFLOW = (REPO / ".github" / "workflows" / "testlab.yml").read_text()
LANE_INSTALL = REPO / ".github" / "scripts" / "lab-lane-install.sh"
POLICY = REPO / "lhpc" / "core" / "transfer.py"
PIP_INSTALL = "pip install"            # the command the guards look for (or must not find)
NO_INDEX = f"{PIP_INSTALL} -q --no-index --find-links /xfer/wheels"


def _step(name: str) -> str:
    """The text of one workflow step, from its `- name:` line to the next step's."""
    m = re.search(rf"^      - name: {re.escape(name)}\n(.*?)(?=^      - name: |^  \S|\Z)",
                  WORKFLOW, re.MULTILINE | re.DOTALL)
    assert m, f"no step {name!r} in testlab.yml"
    return m.group(1)


def test_the_testlab_lane_fetches_no_package_browser_or_web_client_itself():
    lane = _step("Run the lab lanes in the image")
    assert "export PLAYWRIGHT_BROWSERS_PATH=/xfer/ms-playwright" in lane
    assert "export LHPC_MESHTASTIC_WEB_TARBALL=/xfer/meshtastic-web/build.tar" in lane
    assert ". /tmp/repo/.github/scripts/lab-lane-install.sh" in lane
    assert '-v "$PWD/xfer":/xfer:ro' in lane
    assert PIP_INSTALL not in lane and "playwright install" not in lane


def test_the_lane_install_reads_only_the_acquired_wheels():
    assert LANE_INSTALL.is_file(), "no lab-lane-install.sh: the lane installs from an index"
    text = LANE_INSTALL.read_text()
    conf = "no-index = true\\nfind-links = /xfer/wheels"
    assert conf in text and PIP_INSTALL in text
    assert text.index(conf) < text.index(PIP_INSTALL)
    assert "~/.config/pip" in text            # in HOME: the product runner passes HOME on


def test_the_acquisition_and_its_offline_proof_come_before_the_lane():
    names = ("Acquire the lane's Python packages, Chromium and web client (transfer backoff)",
             "Prove the lane's packages, Chromium and web client are complete (no network)",
             "Run the lab lanes in the image")
    assert all(f"      - name: {n}" in WORKFLOW for n in names)
    order = [WORKFLOW.index(f"      - name: {n}") for n in names]
    assert order == sorted(order)
    proof = _step("Prove the lane's packages, Chromium and web client are complete (no network)")
    assert "--network none" in proof and ". .github/scripts/lab-lane-install.sh" in proof
    acquire = _step("Acquire the lane's Python packages, Chromium and web client (transfer backoff)")
    assert 'sha256sum -c' in acquire and "mirror-sources.py web-tarball" in acquire


def test_the_slow_build_lane_installs_with_no_index():
    lane = _step("Slow-target build")
    for target in ("-e .", "-e ./testlab", "pytest"):
        assert f"{NO_INDEX} {target}" in lane
    assert f"backoff {PIP_INSTALL}" not in lane


def test_the_web_tarball_is_the_scripts_own_url_and_the_manifests_pin():
    r = subprocess.run([sys.executable, str(REPO / ".github" / "scripts" / "mirror-sources.py"),
                        "web-tarball", str(REPO / "lhpc" / "data" / "manifest.example.toml")],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    url, sha = r.stdout.split()
    script = (REPO / "lhpc" / "data" / "scripts" / "meshtastic-web-assets.sh").read_text()
    template = re.search(r'URL="([^"]+)"', script).group(1)
    manifest = (REPO / "lhpc" / "data" / "manifest.example.toml").read_text()
    step = re.search(r'meshtastic-web-assets\.sh", "[^"]+", "([^"]+)", "([0-9a-f]{64})"', manifest)
    assert url == template.replace("${VERSION}", step.group(1)) and sha == step.group(2)


def test_the_web_client_tarball_reaches_a_step_through_the_command_runner(tmp_path):
    """The product's command runner drops variables it does not list; the web client script's
    own offline input must reach it, or the lab's copy is ignored and the step fetches live."""
    probe = ("from lhpc.core.probes.backends import RealCommandRunner; "
             "print(RealCommandRunner().run(['sh', '-c', 'echo \"[$LHPC_MESHTASTIC_WEB_TARBALL]\"'],"
             " 30).stdout, end='')")
    env = {**os.environ, "LHPC_MESHTASTIC_WEB_TARBALL": str(tmp_path / "build.tar")}
    out = subprocess.run([sys.executable, "-c", probe], env=env, capture_output=True, text=True,
                         timeout=60).stdout
    assert out.strip() == f"[{tmp_path / 'build.tar'}]"


# --- lhpc/core/transfer.py as the workflows run it: `transfer.py next <retries> <used> <file>...` ---

def _next(tmp_path, *texts, retries=0, used=0, **env):
    files = []
    for i, text in enumerate(texts):
        f = tmp_path / f"s{i}"
        f.write_text(text)
        files.append(str(f))
    return subprocess.run([sys.executable, str(POLICY), "next", str(retries), str(used), *files],
                          capture_output=True, text=True,
                          env={k: v for k, v in {**os.environ, **env}.items()
                               if not (k.startswith("LHPC_TRANSFER_") and k not in env)})


# The shapes the wrapped tools end a failure with (pip, git, curl, gh, apt in docker build, npm,
# playwright, lhpc's own install), each a transfer or not.
SHAPES = [
    ("curl-503", "curl: (22) The requested URL returned error: 503", True),
    ("curl-404", "curl: (22) The requested URL returned error: 404", False),
    ("curl-dns", "curl: (6) Could not resolve host: github.com", True),
    ("gh-504", "HTTP 504: Gateway Timeout (https://api.github.com/repos/x/y/releases)", True),
    ("gh-404", "HTTP 404: Not Found (https://api.github.com/repos/x/y/releases)", False),
    ("git-503", "fatal: unable to access 'https://github.com/x/y/': The requested URL returned "
                "error: 503", True),
    ("git-gone", "fatal: repository 'https://github.com/x/y/' not found", False),
    ("pip-exhausted",
     "WARNING: Retrying (Retry(total=0, connect=None, read=None)) after connection broken by "
     "'NewConnectionError(x)': /simple/foo/\n"
     "ERROR: Could not find a version that satisfies the requirement foo (from versions: none)\n"
     "ERROR: No matching distribution found for foo", True),
    ("pip-no-such-version",
     "ERROR: Could not find a version that satisfies the requirement foo==9 (from versions: 1)\n"
     "ERROR: No matching distribution found for foo==9", False),
    ("pip-hashes", "ERROR: THESE PACKAGES DO NOT MATCH THE HASHES FROM THE REQUIREMENTS FILE.", False),
    ("pip-read-timeout", "ERROR: Could not install packages due to an OSError: "
                         "HTTPSConnectionPool(host='files.pythonhosted.org', port=443): Max retries "
                         "exceeded (Caused by ReadTimeoutError(x))", True),
    ("docker-apt-503",
     "#5 3.2 E: Failed to fetch http://deb.debian.org/debian/pool/x.deb  503  Service Unavailable\n"
     "#5 3.3 E: Unable to fetch some archives, maybe run apt-get update or try with --fix-missing?\n"
     'ERROR: failed to solve: process "/bin/sh -c apt-get install" did not complete '
     "successfully: exit code: 100", True),
    ("docker-apt-404",
     "#5 3.2 E: Failed to fetch http://deb.debian.org/debian/pool/x.deb  404  Not Found\n"
     'ERROR: failed to solve: process "/bin/sh -c apt-get install" did not complete '
     "successfully: exit code: 100", False),
    ("docker-dockerfile",
     'ERROR: failed to solve: dockerfile parse error on line 3: unknown instruction: RUNN', False),
    ("npm-reset", "npm error code ECONNRESET\nnpm error network aborted\n"
                  "npm error network This is a problem related to network connectivity.\n"
                  "npm error A complete log of this run can be found in: /x.log", True),
    ("npm-404", "npm error code E404\nnpm error 404 Not Found - GET https://registry.npmjs.org/x\n"
                "npm error A complete log of this run can be found in: /x.log", False),
    ("playwright-503",
     "Error: Download failed: server returned code 503 body 'x'. URL: https://cdn.playwright.dev/x\n"
     "Failed to install browsers\nError: Failed to download Chromium 141.0.7390.37, caused by",
     True),
    ("lhpc-install-dns",
     "ERR   install kiss failed\n  [failed] clone loraham-kiss-tnc — fatal: unable to access "
     "'https://h.invalid/k.git/': Could not resolve host: h.invalid", True),
    ("lhpc-refusal", "ERR   Refusing to install 'kiss': missing mandatory system dependencies.\n"
                     "  1. sudo apt install x\n\nNext:\n  lhpc install kiss", False),
]


@pytest.mark.parametrize("text,transfer", [(t, x) for _, t, x in SHAPES],
                         ids=[n for n, _, _ in SHAPES])
def test_each_tools_failure_shape_is_classified(tmp_path, text, transfer):
    r = _next(tmp_path, text)
    assert (r.returncode, r.stdout.strip()) == ((0, "0") if transfer else (1, "not a transfer failure"))


def test_a_503_on_one_stream_and_a_404_on_the_other_is_not_a_transfer(tmp_path):
    r = _next(tmp_path, "ERROR: 404 Not Found", "curl: (22) The requested URL returned error: 503")
    assert r.returncode == 1


def test_the_schedule_and_the_retry_start_deadline(tmp_path):
    net = "curl: (22) The requested URL returned error: 503"
    waits = [_next(tmp_path, net, retries=n, used=u).stdout.strip()
             for n, u in ((0, 0), (1, 0), (2, 60), (3, 180), (4, 480), (5, 1080))]
    assert waits == ["0", "60", "120", "300", "600", "600"]
    late = _next(tmp_path, net, retries=6, used=1680)          # 1680 + 600 > 1800
    assert late.returncode == 1 and "retry-start deadline (1800 s)" in late.stdout
    assert _next(tmp_path, net, retries=0, used=1801).returncode == 1
