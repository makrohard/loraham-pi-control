# testlab/x86

The helpers of the x86_64 (amd64) test-lab workflow, `.github/workflows/testlab-x86.yml`: the three
lanes of `testlab.yml` on the hosted `ubuntu-24.04` runner. That workflow is not gating yet and has
not been dispatched yet; its run time and disk use are unmeasured.

## Contents

- [Files](#files)
- [How the x86 lanes differ from the arm lanes](#how-the-x86-lanes-differ-from-the-arm-lanes)
- [Checking the workflow](#checking-the-workflow)

## Files

| file | what it does | exit codes |
|---|---|---|
| `build-image.sh <tag> <record-file>` | builds the lab image from `.devcontainer/Dockerfile` natively on x86_64; pulls the base once, pins the build to that pull's digest, checks the image is amd64 and writes the record (tag, image id, arch, base, pinned digest, Dockerfile sha256) | 0 built, 1 refused (reason on stderr), 2 usage |
| `amd64-remotes.py <manifest>` | the git remotes the x86 lanes mirror: `mirror-sources.py list` plus meshtastic and meshcom, which build from source on x86_64 | 0 list, 1 refused, 2 usage |
| `graywolf-amd64-sha256.sh <version>` | the sha256 `lhpc/data/scripts/graywolf-fetch.sh` records for that release's amd64 .deb | 0 printed, 1 refused, 2 usage |
| `check_workflow.py` | static checks of the workflow file (below) | 0 pass, 1 fail, 2 cannot check |

## How the x86 lanes differ from the arm lanes

- **Runner:** `ubuntu-24.04` (x86_64); the first step of each job stops it unless `uname -m` is
  `x86_64`.
- **No binary channel:** it is aarch64-only (`lhpc/core/service_binary_channel.py`), so every stack
  a lane installs builds from source, and the meshtastic and meshcom remotes are mirrored too.
- **Graywolf:** the slow-build lane fetches the amd64 .deb and checks it against the recorded sha256.
- **Names:** jobs end in `-x86`, artifacts and the concurrency group start with `testlab-x86`.
- **Triggers:** a push to `cons/**` runs `testlab-x86`; a dispatch with `release_verify=true` also
  runs `release-verify-x86` and `slow-build-x86`.
- **Not yet switched for x86_64 (outside these files):** the lab reset installs neither meshcom nor
  meshtastic on x86_64, the meshcom start leg skips there, the release lane's first case expects an
  aarch64 binary target and the slow-build lane expects meshtastic on the binary channel.

## Checking the workflow

```sh
python3 testlab/x86/check_workflow.py
```

A1 the runner label, A2 no arm label, A3 the triggers, A4 no secrets, A5 every `uses:` pinned to a
full sha and copied from `testlab.yml`, A6 the concurrency group, A7 the artifact names, A8 no
`workflow_call`/`workflow_run` and `needs` only within the file, A9 `contents: read`, A10 the job
names, A11 the helper files exist, A12 a second parse with PyYAML when it is importable (else NOT RUN),
A13 every job opens with the x86_64 guard and has no job container or services (so the guard runs
before the checkout and the workflow's own acquisition steps; the runner fetches the actions first).
