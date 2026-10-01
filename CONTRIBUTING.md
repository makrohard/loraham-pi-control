# Contributing

Pull requests are welcome. One that meets everything below is likely to be taken as is; one
that does not may still be taken, but the chances are lower and it will take longer.

## Contents

- [Branches](#branches)
- [Commits](#commits)
- [What should be green](#what-should-be-green)
- [What a good change looks like](#what-a-good-change-looks-like)
- [Adding a stack](#adding-a-stack)

## Branches

- **Open your PR against `dev`**, from a topic branch rebased on `dev`; it lands as **one
  commit** (squash-merge).
- The branch model: [branches and releases](docs/maintenance.md#branches-and-releases); release
  checklists and incidents: [MAINTAINING](MAINTAINING.md).

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

Run these locally in a venv with `pip install -e .[dev]` before opening the PR. CI runs them and
more on every PR: [what CI enforces](docs/maintenance.md#what-ci-enforces).

| gate | command |
|---|---|
| unit + contract suite | `python -m pytest -q -p no:cacheprovider tests` (variants and why `python -m`: [tests/README](tests/README.md#how-to-run)). `-n` (`pytest-xdist`, not a dev dependency) is a local convenience, never release evidence |
| lint, frozen ruleset | `ruff check lhpc testlab` and `ruff check tests --select F,E9` |
| security | `bandit -q -r lhpc -lll` |
| testlab unit lane | `python -m pytest -q testlab/tests/unit` — the simulator itself; the acceptance and browser lanes are opt-in, see [testlab](docs/testlab.md) |
| docs | run inside the suite; what it pins: [maintenance](docs/maintenance.md#what-ci-does-not-enforce) |
| shipped snapshot | `lhpc deps --script` must equal `bootstrap-deps.sh` when `lhpc/core/deps.py` changed |

The suite should be fully green locally: no failures, nothing skipped on an ordinary developer
machine. Coverage is not a gate ([what CI does not
enforce](docs/maintenance.md#what-ci-does-not-enforce)); don't let it drop when you touch `lhpc/`.

## What a good change looks like

- **Tests that fail on the unfixed code.** A bug fix carries a regression test; a new capability
  tags its widest-seam happy and refusal case `@pytest.mark.contract`. Where a test goes and the
  rules it follows: [tests/README](tests/README.md).
- **Docs in the same commit.** The CLI reference, the operator docs and `CHANGELOG.md` change
  with the code. Docs state the current contract; history goes in the changelog, live runs in
  `docs/live-tests/`. Numbers in docs are measured, never estimated.
- **Both READMEs.** A factual change to `README.md` is mirrored in `README.de.md`.
- **No architecture change without a discussion first.** Open an issue; the
  [architecture](docs/architecture.md) doc is the model to argue against.
- **Prefer deletion.** Compatibility shims, feature flags and do-nothing knobs are not taken.

## Adding a stack

[docs/adding-a-stack.md](docs/adding-a-stack.md) is the recipe; the manifest is the contract.
A new stack comes with its `docs/stacks/<stack>.md`, a row in the README's stacks table and a row
in the [release test matrix](docs/test-matrix.md).
