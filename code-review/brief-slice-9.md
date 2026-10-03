# CLOUD BRIEF · code review of loraham-pi-control, slice 9 of 10 (one Claude Code cloud session)

You review a fixed set of files of this repository for defects. READ-ONLY on the code: you change no file under
`lhpc/`, `testlab/`, `demo/`, `tests/` or `tools/`. Your only output is ONE commit on this routine's branch (the
environment names it; `git push` once at the end) that adds the file `code-review/findings-slice-9.md`. Never open a
pull request, never touch `main` or `dev` or any other branch, never tag. No Co-Authored-By or AI-attribution line.

## Base
`git fetch origin && git checkout -B <this routine's branch> origin/main`. Assert `git rev-parse HEAD` ==
`c60028f08a87a98373646f4364f7988e5cf6d58d` (v0.11.9); if not, stop and write only that into the findings file.

## The files of this slice (read every line of every one; nothing else is in scope for findings)
- `lhpc/__init__.py` (0 KB)
- `lhpc/__main__.py` (0 KB)
- `lhpc/core/__init__.py` (0 KB)
- `lhpc/core/abortflag.py` (0 KB)
- `lhpc/core/assets.py` (4 KB)
- `lhpc/core/build_launcher_runtime.py` (17 KB)
- `lhpc/core/build_regression.py` (2 KB)
- `lhpc/core/gps.py` (51 KB)
- `lhpc/core/gps_bridge.py` (40 KB)
- `lhpc/core/meshcore_identity.py` (11 KB)
- `lhpc/core/meshcore_mode.py` (3 KB)
- `lhpc/core/meshcore_plugins.py` (4 KB)
- `lhpc/core/meshtastic_tool.py` (21 KB)
- `lhpc/core/outcomes.py` (4 KB)
- `lhpc/core/paths.py` (4 KB)
- `lhpc/core/power.py` (2 KB)
- `lhpc/core/probes/__init__.py` (0 KB)
- `lhpc/core/probes/backends.py` (41 KB)
- `lhpc/core/probes/endpoints.py` (4 KB)
- `lhpc/core/probes/hardware.py` (4 KB)
- `lhpc/core/probes/process.py` (2 KB)
- `lhpc/core/probes/source.py` (6 KB)
- `lhpc/core/probes/systemd.py` (2 KB)
- `lhpc/core/probes/unixsock.py` (3 KB)
- `lhpc/core/procident.py` (4 KB)
- `lhpc/core/resources.py` (3 KB)
- `lhpc/core/reticulum_interfaces.py` (4 KB)
- `lhpc/core/rflog.py` (9 KB)
- `lhpc/core/sdnotify.py` (2 KB)
- `lhpc/core/service_base.py` (5 KB)
- `lhpc/core/snapshot_memo.py` (1 KB)
- `lhpc/core/webjob_gate.py` (3 KB)
- `lhpc/core/wrapper_runtime.py` (2 KB)
- `lhpc/version.py` (0 KB)
- `install.sh` (21 KB)
- `uninstall.sh` (24 KB)
- `bootstrap-deps.sh` (57 KB)

You MAY read any other file of the repository to understand a call, a contract or a test — but a finding must
point at a line inside this slice. `docs/architecture.md` (the model and the safety invariants), `docs/gps.md` and `README.md` (install) are the frame; read them first. For the three shell scripts the questions are: a failure mid-way that leaves the box half-installed or half-removed, `set -e` gaps, unquoted paths, a `rm` whose target can be wrong, and a step that runs as root more than it must. `tests/README.md` names the house rules of the tests.

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
3. Run nothing that needs hardware or root. `python -m pytest -q -p no:cacheprovider <the slice's test files>` is allowed to confirm a claim (if pytest is missing, `pip install pytest` first); `shellcheck` on the three scripts if available; `ruff check lhpc` for information only.

## The findings file (`code-review/findings-slice-9.md`)
Header: slice number, the files with their line counts, the base commit, the date (UTC), the model.
Then ONE table, one row per finding:
`| id | file:line | severity | the claim (what the code/doc says it guarantees) | the defect (what actually happens) | how to see it (a reproduction in words, or the name of the test that would be red — do not write the test) | confidence (high/medium/low) |`
ids `CR9-1`, `CR9-2`, …; severity S1 = safety or data loss, S2 = wrong behaviour the operator meets, S3 =
robustness (a path that fails only under an unusual condition). Severity S1 findings first.
Then "Checked and fine" (≤ 15 lines) and "Not read / not understood" (be honest; an unread file is listed here).
No addresses, call signs, node ids, home paths or secrets anywhere. Keep it short: facts, file:line, no narrative.

## Adversarial self-review before the push (mandatory)
When everything is green, re-read your whole diff once more AS A HOSTILE REVIEWER who will be paid per finding: for every hunk ask what input, timing, caller or platform breaks it; what the old code handled that the new code does not; which test only passes because of the fake; which claim in your report you have not actually run. Fix what you find, re-run the gates, and list in the report what this pass found and changed (or 'nothing').

## Review-packet header (mandatory wording)
Every review packet you write MUST start with: a request paragraph that names what to judge and the answer form — `| commit | verdict (OK / FINDING) | what |` and a final line GREEN / GREEN WITH NOTES / RED — and the sentence "This file is your whole input: you have no repository access; use no connector, tool or web lookup."
