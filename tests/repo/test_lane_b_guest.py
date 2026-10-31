"""The lane-B guest helpers (.github/lane-b/*.sh) as the jobs run them, against a stubbed guest: each must fail
closed without a VM (the reboot counts only with a new boot id)."""
import re
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
    lines = (LANE / "topic.sh").read_text().splitlines()
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


def test_the_lane_adds_no_cli_link_to_the_commits_block(tmp_path):
    # A commit whose block does not make the link keeps that defect visible: the job's PATH check then fails.
    p, is_link = _guest_install(tmp_path, "7da3c7dff23f9bc5ad7dbf61b53dfa857ebd56be", "true")
    assert p.returncode == 0 and not is_link, p.stdout + p.stderr


@pytest.mark.parametrize("frm", ["v0.13.2", "7da3c7df", "main"])
def test_the_install_takes_a_full_commit_sha_only(tmp_path, frm):
    p, _ = _guest_install(tmp_path, frm, "true")
    assert p.returncode == 2 and "usage" in p.stderr


def test_the_lane_needs_no_state_of_main():
    # G2-LBS F3: a guard on main's commit stopped every job once main moved. The job's only input is FROM, a commit.
    text = {p.name: p.read_text() for p in LANE.glob("*.sh")}
    assert not {n for n, t in text.items() if re.search(r"\b[0-9a-f]{40}\b|origin/main|ls-remote", t)}


def _topic(tmp_path, fault):
    """topic.sh as the job runs it, with the VM steps stubbed and every ssh answer from a stub guest; `fault` names the
    one observation whose ssh call fails (rc 255)."""
    ws = tmp_path / "ws"
    (ws / ".github" / "lane-b").mkdir(parents=True)
    (ws / ".github" / "scripts").mkdir()
    for name in ("topic.sh", "guest-install.sh", "sampler.sh"):
        (ws / ".github" / "lane-b" / name).write_text((LANE / name).read_text())
    (ws / ".github" / "scripts" / "lane_b.py").write_text((LANE.parent / "scripts" / "lane_b.py").read_text())
    (ws / ".github" / "lane-b" / "guest.sh").write_text((LANE / "guest.sh").read_text() + f"""
prepare() {{ :; }}; boot() {{ :; }}; gcp() {{ :; }}; start_samplers() {{ :; }}; collect_samples() {{ :; }}
wait_ssh() {{ echo 1; }}; wait_up() {{ echo "1 running"; }}; wait_reboot() {{ echo {_B1}; }}
g() {{ local c="$*" f={fault!r}
  case "$c" in
    "uname -m") echo aarch64;; "uname -r") echo 6.12.0-guest;; "cat /etc/os-release") echo VERSION_CODENAME=trixie;;
    "test -f /etc/rpi-issue"*) echo yes;; "bash guest-install.sh "*) echo t_deps=1; echo t_install=1;;
    "cat /proc/sys/kernel/random/boot_id") [ "$f" = bootid ] && return 255; echo {_B0};;
    *"# installs the units"*) [ "$f" = unitsline ] && return 255; echo "lhpc self-update --repair-integration   # installs the units";;
    *"command -v lhpc"*) [ "$f" = commandv ] && return 255; echo /home/lhpc/.local/bin/lhpc;;
    *"is-active lhpc-web.service lhpc-nginx.service"*) echo active; echo active; if [ "$f" = isactive ]; then return 255; fi;;
    *) [ "$f" = "${{c%% *}}" ] && return 255; :;;
  esac; }}
""")
    ev, work = tmp_path / "ev", tmp_path / "work"
    ev.mkdir(); work.mkdir()
    return subprocess.run(["bash", str(ws / ".github" / "lane-b" / "topic.sh"), "a" * 40], capture_output=True,
                          text=True, check=False, env={"PATH": "/usr/bin:/bin", "GITHUB_WORKSPACE": str(ws),
                                                       "WORK": str(work), "EV": str(ev)})


@pytest.mark.parametrize("fault", ["", "bootid", "unitsline", "commandv", "isactive"])
def test_a_failed_observation_is_never_a_pass(tmp_path, fault):
    # G2-LBS F5's class in the lane's own job: an ssh call that fails is a failure, whatever it printed first.
    p = _topic(tmp_path, fault)
    if fault:
        assert p.returncode != 0 and "ROW TOPIC: PASS" not in p.stdout, p.stdout + p.stderr
    else:
        assert p.returncode == 0 and "ROW TOPIC: PASS" in p.stdout, p.stdout + p.stderr
