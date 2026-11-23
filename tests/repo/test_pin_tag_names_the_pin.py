"""CI's pin validation ties each pin_tag to its pin_commit: a tag the remote has must peel to the pin; a
pin_tag that is no tag there (a `git describe` name "X-N-g<sha>" or a short sha) must end in a sha prefix of
the pin (>= 7 hex). The workflow's own pin checks (from the pin lookup to the checkout) run here against a
local bare remote."""
import re
import subprocess

import repo_paths

ROOT = repo_paths.REPO


def _pin_checks() -> str:
    text = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
    m = re.search(r'^ *git -C "\$dir" cat-file -e .*?(?=^ *git -C "\$dir" checkout -q "\$PIN")',
                  text, re.MULTILINE | re.DOTALL)
    assert m, "pin validation resolves the pin before its checkout"
    return m.group(0)


def test_the_pin_validation_step_ties_pin_tag_to_the_pin(tmp_path):
    remote = tmp_path / "remote.git"
    work = tmp_path / "work"
    ext = tmp_path / "ext"

    def git(repo, *a):
        return subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c",
                               "user.email=t@example.invalid", *a], check=True,
                              capture_output=True, text=True).stdout.strip()
    git(tmp_path, "init", "-q", "--bare", str(remote))
    git(tmp_path, "init", "-q", "-b", "main", str(work))
    sha = []
    for n in ("one", "two"):
        (work / "f").write_text(n)
        git(work, "add", "f")
        git(work, "commit", "-qm", n)
        sha.append(git(work, "rev-parse", "HEAD"))
    git(work, "tag", "-a", "-m", "v1.0", "v1.0", sha[1])          # annotated, on the newer commit
    git(work, "tag", "lw", sha[0])                                 # lightweight, on the older one
    git(work, "push", "-q", str(remote), "main", "v1.0", "lw")
    git(tmp_path, "init", "-q", str(ext))
    git(ext, "remote", "add", "origin", str(remote))
    git(ext, "fetch", "-q", "--no-tags", "origin", "main")
    step = _pin_checks()

    def run(pin, tag):
        env = {"dir": str(ext), "path": "src/x", "PIN": pin, "TAG": tag, "KCS": "", "BRANCH": "main",
               "REMOTE": str(remote), "RETRY_DELAY": "0", "HOME": str(tmp_path), "PATH": "/usr/bin:/bin"}
        script = f"set -euo pipefail\n. {ROOT / '.github' / 'scripts' / 'retry.sh'}\n{step}"
        return subprocess.run(["bash", "-c", script], env=env, capture_output=True, text=True, check=False)

    new, old = sha[1], sha[0]
    ok = {"annotated tag on the pin": (new, "v1.0"), "lightweight tag on the pin": (old, "lw"),
          "describe name ending in the pin": (new, f"1.0-1-g{new[:9]}"), "short sha of the pin": (new, new[:7]),
          "no pin_tag": (new, "")}
    for case, (pin, tag) in ok.items():
        r = run(pin, tag)
        assert r.returncode == 0, f"{case}: {r.stdout}{r.stderr}"
    bad = {"tag on another commit": (old, "v1.0", "is not the pinned commit"),
           "describe name ending in another commit": (new, f"1.0-1-g{old[:9]}", "does not end in a sha prefix"),
           "short sha below 7 hex": (new, new[:6], "does not end in a sha prefix"),
           "a version that is no tag": (new, "v9.9", "does not end in a sha prefix")}
    for case, (pin, tag, why) in bad.items():
        r = run(pin, tag)
        assert r.returncode != 0 and why in r.stdout, f"{case}: rc={r.returncode} {r.stdout}{r.stderr}"
