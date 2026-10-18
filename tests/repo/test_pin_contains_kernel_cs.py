"""A box judges a build at the manifest's pin by the pin (spi_mode.provenance_refusal): a binary
install has no history to ask whether it contains `kernel_cs_since`. CI's pin validation, which
full-fetches every pinned source, holds that instead: tools/manifest_pin.py names the commit (KCS)
and the job fails a pin that does not contain it."""
import re
import subprocess
import sys
import tomllib

import repo_paths

ROOT = repo_paths.REPO
TOOL = ROOT / "tools" / "manifest_pin.py"


def _shell(path):
    p = subprocess.run([sys.executable, str(TOOL), path], capture_output=True, text=True, check=True)
    return dict(line.split("=", 1) for line in p.stdout.splitlines())


def test_the_tool_names_each_pinned_sources_kernel_cs_since():
    data = tomllib.loads((ROOT / "lhpc" / "data" / "manifest.example.toml").read_text())
    declared = {c["source"]["path"]: c["kernel_cs_since"] for st in data["stack"]
                for c in st.get("component", []) if c.get("kernel_cs_since") and c.get("source")}
    assert declared, "the daemon and the Reticulum interface declare kernel_cs_since"
    for path, since in declared.items():
        assert _shell(path)["KCS"] == since
    plain = next(c["source"]["path"] for st in data["stack"] for c in st.get("component", [])
                 if c.get("source", {}).get("pin_commit") and c["source"]["path"] not in declared)
    assert _shell(plain)["KCS"] == ""


def test_the_pin_validation_step_fails_a_pin_below_kernel_cs_since(tmp_path):
    """The workflow's own lines, run on a local repository old -> since -> newer."""
    text = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
    m = re.search(r'^( *)if \[ -n "\$\{KCS:-\}" \].*?^\1fi$', text, re.MULTILINE | re.DOTALL)
    assert m, "pin validation checks KCS"
    step = m.group(0)
    repo = tmp_path / "ext"
    repo.mkdir()

    def git(*a):
        return subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c",
                               "user.email=t@example.invalid", *a], check=True,
                              capture_output=True, text=True).stdout.strip()
    git("init", "-q")
    sha = {}
    for n in ("old", "since", "newer"):
        (repo / "f").write_text(n)
        git("add", "f")
        git("commit", "-qm", n)
        sha[n] = git("rev-parse", "HEAD")

    def run(pin, kcs):
        env = {"dir": str(repo), "path": "src/x", "PIN": pin, "KCS": kcs, "PATH": "/usr/bin:/bin"}
        return subprocess.run(["bash", "-c", "set -euo pipefail\n" + step], env=env,
                              capture_output=True, text=True, check=False)
    assert run(sha["newer"], sha["since"]).returncode == 0
    assert run(sha["since"], sha["since"]).returncode == 0
    assert run(sha["newer"], "").returncode == 0
    low = run(sha["old"], sha["since"])
    assert low.returncode != 0 and "does not contain kernel_cs_since" in low.stdout
