"""The lane-B guest helpers (.github/lane-b/*.sh) as the jobs run them, against a stubbed guest: each must fail
closed without a VM (the reboot counts only with a new boot id)."""
import subprocess

import pytest

import repo_paths

LANE = repo_paths.REPO / ".github" / "lane-b"
_B0, _B1 = "0f6a1c2e-1111-4a5b-8c9d-000000000000", "7d2e9b40-2222-4c6d-9e0f-111111111111"


def _wait_reboot(tmp_path, answers, bound):
    """guest.sh's wait_reboot against a stubbed guest: each ssh call takes the next 'rc value' answer (the last one
    repeats); sleep is a no-op, so the bound is reached by the clock only when it is 0."""
    (tmp_path / "answers").write_text("".join(f"{rc} {v}\n" for rc, v in answers))
    script = f"""set -euo pipefail
WORK={tmp_path}; EV={tmp_path}; . {LANE / "guest.sh"}
sleep() {{ :; }}
g() {{ local n a; n=$(( $(cat {tmp_path}/n 2>/dev/null || echo 0) + 1 )); echo "$n" > {tmp_path}/n   # g runs in $( )
      a=$(sed -n "${{n}}p" {tmp_path}/answers); [ -n "$a" ] || a=$(tail -n 1 {tmp_path}/answers)
      [ "${{a%% *}}" = 0 ] || return "${{a%% *}}"; echo "${{a#* }}"; }}
b=$(wait_reboot {bound} {_B0}); echo "after=$b"
"""
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True, check=False)


@pytest.mark.parametrize("answers, bound, after", [
    ([(0, _B0)], 0, None),                                      # the same boot answers: no reboot happened
    ([(255, ""), (255, "")], 0, None),                          # no answer at all: not a reboot either
    ([(255, ""), (0, _B0), (255, ""), (0, _B1)], 600, _B1),     # down, the old boot still, down, a new boot
    ([(0, "")], 0, None),                                       # an empty read is not a boot id
])
def test_the_reboot_counts_only_with_a_new_boot_id(tmp_path, answers, bound, after):
    p = _wait_reboot(tmp_path, answers, bound)
    if after is None:
        assert p.returncode != 0 and "after=" not in p.stdout, p.stdout + p.stderr
    else:
        assert p.returncode == 0 and f"after={after}" in p.stdout, p.stdout + p.stderr


def test_the_reboot_step_reads_the_boot_id_before_and_waits_for_a_new_one():
    lines = (LANE / "u0b.sh").read_text().splitlines()
    at = [next(i for i, ln in enumerate(lines) if ln.startswith(s)) for s in (
        'b0=$(g cat /proc/sys/kernel/random/boot_id) && [ -n "$b0" ] || fail', "t0=$(date +%s); g 'sudo systemctl reboot'",
        'b1=$(wait_reboot "$BOUND" "$b0") || fail')]
    assert at == sorted(at) and at[2] == at[1] + 1


def _guest_install(tmp_path, frm, block):
    """guest-install.sh as the guest runs it, against stubs: curl writes a no-op bootstrap, sudo runs its command, the
    clone already exists, and the README's manual block is `block` (after its clone line)."""
    home, bin_ = tmp_path / "home", tmp_path / "bin"
    src = home / "loraham-pi-control" / "src" / "loraham-pi-control"
    (src / ".git").mkdir(parents=True)
    (src / "bootstrap-deps.sh").write_text("true\n")             # where an earlier form of step 4 read it
    (src / "README.md").write_text("### Manual — clone / venv / bootstrap\n\n```bash\n"
                                   "git clone https://github.com/makrohard/loraham-pi-control.git x\n" + block + "\n```\n")
    bin_.mkdir()
    stubs = {"curl": 'while [ $# -gt 1 ]; do [ "$1" = -o ] && echo true > "$2"; shift; done',
             "sudo": 'exec "$@"', "git": 'echo stub'}
    for name, body in stubs.items():
        (bin_ / name).write_text(f"#!/bin/bash\n{body}\n")
        (bin_ / name).chmod(0o755)
    p = subprocess.run(["bash", str(LANE / "guest-install.sh"), frm], capture_output=True, text=True, check=False,
                       cwd=home, env={"HOME": str(home), "PATH": f"{bin_}:/usr/bin:/bin"})
    return p, (home / ".local" / "bin" / "lhpc").is_symlink()


@pytest.mark.parametrize("frm, block, linked", [
    ("v0.13.2", "true", True),                                  # a released tag's block lacks the link: the lane adds it
    ("7da3c7dff23f9bc5ad7dbf61b53dfa857ebd56be", "true", False),   # a topic's block lacks it: its defect stays visible
])
def test_the_cli_link_is_added_only_for_a_released_tag(tmp_path, frm, block, linked):
    p, is_link = _guest_install(tmp_path, frm, block)
    assert p.returncode == 0, p.stdout + p.stderr
    assert is_link is linked
