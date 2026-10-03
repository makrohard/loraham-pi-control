# CLOUD BRIEF · code review of loraham-pi-control, slice 10 of 10 (one Claude Code cloud session)

You review a fixed set of files of this repository for defects. READ-ONLY on the code: you change no file under
`lhpc/`, `testlab/`, `demo/`, `tests/` or `tools/`. Your only output is ONE commit on this routine's branch (the
environment names it; `git push` once at the end) that adds the file `code-review/findings-slice-10.md`. Never open a
pull request, never touch `main` or `dev` or any other branch, never tag. No Co-Authored-By or AI-attribution line.

## Base
`git fetch origin && git checkout -B <this routine's branch> origin/main`. Assert `git rev-parse HEAD` ==
`c60028f08a87a98373646f4364f7988e5cf6d58d` (v0.11.9); if not, stop and write only that into the findings file.

## The files of this slice (read every line of every one; nothing else is in scope for findings)
- `demo/lhpc_demo/__init__.py` (0 KB)
- `demo/lhpc_demo/app.py` (0 KB)
- `demo/lhpc_demo/bridge.py` (3 KB)
- `demo/lhpc_demo/daemon_sim.py` (3 KB)
- `demo/lhpc_demo/gps_sim.py` (2 KB)
- `demo/lhpc_demo/provider.py` (0 KB)
- `demo/lhpc_demo/service.py` (33 KB)
- `demo/lhpc_demo/shims.py` (0 KB)
- `demo/lhpc_demo/system.py` (0 KB)
- `demo/lhpc_demo/system_sim.py` (4 KB)
- `testlab/lhpc_testlab/__init__.py` (1 KB)
- `testlab/lhpc_testlab/__main__.py` (0 KB)
- `testlab/lhpc_testlab/cli.py` (2 KB)
- `testlab/lhpc_testlab/data/aprs_sink.py` (1 KB)
- `testlab/lhpc_testlab/data/loraham-daemon-fake/loraham_daemon/build.sh` (0 KB)
- `testlab/lhpc_testlab/data/loraham-daemon-fake/loraham_daemon/run_tests.sh` (0 KB)
- `testlab/lhpc_testlab/data/pyshims/gpiod/__init__.py` (1 KB)
- `testlab/lhpc_testlab/data/pyshims/gpiod/line.py` (0 KB)
- `testlab/lhpc_testlab/data/pyshims/spidev.py` (1 KB)
- `testlab/lhpc_testlab/gpsd.py` (3 KB)
- `testlab/lhpc_testlab/http_client.py` (2 KB)
- `testlab/lhpc_testlab/labweb.py` (0 KB)
- `testlab/lhpc_testlab/manifest_overlay.py` (5 KB)
- `testlab/lhpc_testlab/nm.py` (8 KB)
- `testlab/lhpc_testlab/ops.py` (25 KB)
- `testlab/lhpc_testlab/provider.py` (1 KB)
- `testlab/lhpc_testlab/release.py` (28 KB)
- `testlab/lhpc_testlab/release_lane.py` (8 KB)
- `testlab/lhpc_testlab/rules.py` (2 KB)
- `testlab/lhpc_testlab/scenarios.py` (4 KB)
- `testlab/lhpc_testlab/spawn.py` (2 KB)
- `testlab/lhpc_testlab/supervisor.py` (6 KB)
- `testlab/lhpc_testlab/sysd.py` (4 KB)
- `testlab/lhpc_testlab/system.py` (5 KB)
- `testlab/lhpc_testlab/testing.py` (4 KB)
- `testlab/lhpc_testlab/web.py` (5 KB)
- `tools/build_regression.py` (1 KB)
- `tools/manifest_pin.py` (4 KB)

You MAY read any other file of the repository to understand a call, a contract or a test — but a finding must
point at a line inside this slice. `docs/testlab.md`, `testlab/README.md`, `demo/README.md` and `docs/architecture.md` are the frame; read them first. These files are test infrastructure and the Pages demo: a defect here is a lane or a fake that can PASS when the real thing would fail (a fake daemon that accepts what the real one refuses, a readiness check that returns early, a fixture that swallows an error, a release lane that attributes a failure to the wrong pin), a fake whose wire protocol differs from the daemon's documented one, or a simulation in the demo that claims a behaviour the real code does not have. `tests/README.md` names the house rules of the tests.

## What counts as a finding
Defects only: wrong behaviour; a guarantee the code claims (docstring, comment, doc, CHANGELOG) but does not hold;
races between threads/processes/requests; unhandled errors on the paths an operator hits; resource leaks (fds,
processes, locks, temp files); secrets and file-permission handling; a safety invariant reachable without its check
(nothing transmits without the start gate: licence/call sign, band, power, clock; band arbitration between stacks);
boot restore and self-update that could leave a box unreachable; data loss on a crash mid-operation.
NOT findings: style, naming, wording, "could be simpler", missing type hints, long functions — unless they hide a
defect. Do not propose refactors.

## Method
1. Read docs/architecture.md, then the slice's files in the listed order, completely.
2. For every candidate finding: re-read the code once more and check the OTHER side (the caller, the test, the
   unit file, the doc) before you write it. Prefer one proven finding over five guesses. If a test already covers the
   case and is green, it is not a finding — say so in a "checked and fine" list at the end (short).
3. Run nothing that needs hardware or root. `python -m pytest -q -p no:cacheprovider <the slice's test files>` is
   allowed to confirm a claim; `ruff check lhpc` for information only.

## The findings file (`code-review/findings-slice-10.md`)
Header: slice number, the files with their line counts, the base commit, the date (UTC), the model.
Then ONE table, one row per finding:
`| id | file:line | severity | the claim (what the code/doc says it guarantees) | the defect (what actually happens) | how to see it (a reproduction in words, or the name of the test that would be red — do not write the test) | confidence (high/medium/low) |`
ids `CR10-1`, `CR10-2`, …; severity S1 = safety or data loss, S2 = wrong behaviour the operator meets, S3 =
robustness (a path that fails only under an unusual condition). Severity S1 findings first.
Then "Checked and fine" (≤ 15 lines) and "Not read / not understood" (be honest; an unread file is listed here).
No addresses, call signs, node ids, home paths or secrets anywhere. Keep it short: facts, file:line, no narrative.

## Adversarial self-review before the push (mandatory)
When everything is green, re-read your whole diff once more AS A HOSTILE REVIEWER who will be paid per finding: for every hunk ask what input, timing, caller or platform breaks it; what the old code handled that the new code does not; which test only passes because of the fake; which claim in your report you have not actually run. Fix what you find, re-run the gates, and list in the report what this pass found and changed (or 'nothing').
