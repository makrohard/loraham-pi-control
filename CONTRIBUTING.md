# Contributing

Pull requests are welcome. A PR that satisfies everything below is likely to be taken as is; one
that does not may still be taken, but the chances are lower and it will take longer.

## Branches

- `main` is the latest release: every commit on it is tagged, and `install.sh`, `self-update` and
  the image builder read it. Nothing lands on `main` except a fast-forward from `dev` at a release.
- `dev` is where changes land. Open your PR against `dev`. `main` is never rewritten; `dev` only
  at a release, when the cycle is squashed into the release commit (rebase an open topic branch
  onto the new `dev` afterwards).
- Work on a topic branch off `dev`, rebase it on `dev` before the PR, and land it as **one
  commit** (squash-merge). The maintainer's own work follows the same path.
- A hotfix for the released version is the one exception; see
  [Branches and releases](docs/maintenance.md#branches-and-releases).

## Commits

- **One change, one commit**, and it is complete: code, its tests, the docs it changes and the
  `CHANGELOG.md` line, together. Fix-ups are squashed on the topic branch before it lands.
- **The subject names the change** in at most 72 characters, present tense, no prefix ritual
  (`firewall: bootstrap renders the operator scripts`, `known-working: a binary stack is told it
  has no source composition`). The body, when there is one, says why and what was measured; it
  never narrates the process.
- **No generated trailers**, no co-author lines, no ticket numbers in the subject.
- **Never bench identities**: no Wi-Fi credentials, other people's callsigns or private
  addresses in a message or a diff.

## What should be green

CI runs on every push to `dev` and on every PR (`.github/workflows/ci.yml`, `testlab.yml`). Run
the same gates locally before opening the PR; each one is a single command in a venv with
`pip install -e .[dev]`:

| gate | command |
|---|---|
| unit + contract suite | `pytest -q -n 12 --dist loadfile -p no:cacheprovider tests` (a serial `pytest -q tests` is what CI runs) |
| lint, frozen ruleset | `ruff check lhpc testlab` and `ruff check tests --select F,E9` |
| security | `bandit -q -r lhpc -lll` |
| console lane | `python -m pytest -q testlab/tests` (see [testlab](docs/testlab.md)) |
| docs | part of the suite: every Contents block, the docs index, `cli.md` per CLI verb, README drift (EN and DE), the hardware table |
| shipped snapshot | `lhpc deps --script` must equal `bootstrap-deps.sh` when `lhpc/core/deps.py` changed |
| pins | CI's `pin-validation` job checks every pinned source against its live branch |

Two tests need a real Meshtastic CLI in the venv and fail without it; that is the only accepted
local failure. Coverage is not gated; do not let it drop when you touch `lhpc/`.

## What a good change looks like

- **Tests that fail on the unfixed code.** A bug fix carries a regression; a new capability tags
  its widest-seam happy and refusal case `@pytest.mark.contract`.
- **Docs in the same commit.** The CLI reference, the operator docs and `CHANGELOG.md` change
  with the code. Docs state the current contract only; history lives in the changelog and
  measured evidence in `docs/live-test.md`. Numbers in docs are measured, never estimated.
- **Both READMEs.** A factual change to `README.md` is mirrored in `README.de.md`.
- **No architecture change without a discussion first.** Open an issue; the
  [architecture](docs/architecture.md) doc is the model to argue against.
- **Prefer deletion.** Compatibility shims, feature flags and do-nothing knobs are not taken.

## Adding a stack

[docs/adding-a-stack.md](docs/adding-a-stack.md) is the recipe; the manifest is the contract.
A new stack comes with its `docs/stacks/<stack>.md`, a row in the README's stacks table and,
where the release matrix applies, a row in `docs/test-matrix.md`.
