"""The testlab lane takes its transfers from copies acquired before it.

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

import repo_paths

REPO = repo_paths.REPO
WORKFLOW = (REPO / ".github" / "workflows" / "testlab.yml").read_text()
LANE_INSTALL = REPO / ".github" / "scripts" / "lab-lane-install.sh"
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
    installs = [ln for ln in LANE_INSTALL.read_text().splitlines() if ln.startswith(PIP_INSTALL)]
    assert len(installs) == 3 and all(NO_INDEX in ln for ln in installs), installs


def test_the_product_pip_steps_do_not_inherit_no_index():
    """The lane's no-index is on its own installs only. A pip configuration (a pip.conf in the
    lab user's HOME, which the product's command runner passes on) also reached the product's
    pip steps, and the acceptance lane's meshcore builds found no wheel (testlab 42493471891)."""
    text = LANE_INSTALL.read_text() + _step("Run the lab lanes in the image")
    for config in ("pip.conf", ".config/pip", "pip config", "PIP_", "XDG_CONFIG_HOME"):
        assert config not in text, f"the lane writes a pip configuration ({config})"


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
                       capture_output=True, text=True, check=False)
    assert r.returncode == 0, r.stderr
    url, sha = r.stdout.split()
    script = (REPO / "lhpc" / "data" / "scripts" / "meshtastic-web-assets.sh").read_text()
    template = re.search(r'URL="([^"]+)"', script).group(1)
    manifest = (REPO / "lhpc" / "data" / "manifest.example.toml").read_text()
    step = re.search(r'meshtastic-web-assets\.sh", "[^"]+", "([^"]+)", "([0-9a-f]{64})"', manifest)
    assert url == template.replace("${VERSION}", step.group(1)) and sha == step.group(2)


def test_mirror_sources_runs_alone_as_the_proof_steps_run_it():
    """The proof steps mount only .github/scripts, at /scripts: the script runs as
    /scripts/mirror-sources.py, one level below the root. On 2026-11-08 a module-level path
    computed from its own location (`parents[2]`) failed every verb there with an IndexError
    (testlab 42490401009). Its source runs here under that very file name."""
    src = (REPO / ".github" / "scripts" / "mirror-sources.py").read_text()
    run = ("import sys; src = sys.stdin.read(); "
           "sys.argv = ['/scripts/mirror-sources.py', 'name', 'https://github.com/x/y.git']; "
           "g = {'__name__': '__main__', '__file__': '/scripts/mirror-sources.py'}; "
           "exec(compile(src, '/scripts/mirror-sources.py', 'exec'), g)")
    r = subprocess.run([sys.executable, "-c", run], input=src, capture_output=True, text=True,
                       check=False)
    assert r.returncode == 0 and r.stdout.strip() == "github.com_x_y.git", r.stderr[-400:]


def test_the_web_client_tarball_reaches_a_step_through_the_command_runner(tmp_path):
    """The product's command runner drops variables it does not list; the web client script's
    own offline input must reach it, or the lab's copy is ignored and the step fetches live."""
    probe = ("from lhpc.core.probes.backends import RealCommandRunner; "
             "print(RealCommandRunner().run(['sh', '-c', 'echo \"[$LHPC_MESHTASTIC_WEB_TARBALL]\"'],"
             " 30).stdout, end='')")
    env = {**os.environ, "LHPC_MESHTASTIC_WEB_TARBALL": str(tmp_path / "build.tar")}
    out = subprocess.run([sys.executable, "-c", probe], env=env, capture_output=True, text=True,
                         timeout=60, check=False).stdout
    assert out.strip() == f"[{tmp_path / 'build.tar'}]"
