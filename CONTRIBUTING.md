# Contributing

Pull requests are welcome. One that meets everything below is likely to be taken as is; one
that does not may still be taken, but the chances are lower and it will take longer.

## Contents

- [Before you change code, tests or docs](#before-you-change-code-tests-or-docs)
- [Branches](#branches)
- [Commits](#commits)
- [What should be green](#what-should-be-green)
- [What a good change looks like](#what-a-good-change-looks-like)
- [Adding a stack](#adding-a-stack)

## Before you change code, tests or docs

Read the rules for what you change first:

- **Tests:** [the test rules](tests/README.md#rules-by-area): where a test goes, what it asserts, how it stays
  deterministic and able to fail, and which rules CI checks.
- **Docs and CHANGELOG:** [the docs rules](docs/maintenance.md#docs-and-changelog).
- **House rules:** `python tools/check_houserules.py <base>..HEAD` passes before you push
  ([the check](docs/maintenance.md#policy)); CI runs it on every pull request and on pushes to `dev` and
  `main`, and a HARD line fails the job.

## Branches

- **Open your PR against `dev`**, from a topic branch rebased on `dev`; it lands as **one
  commit** (squash-merge).
- The branch model: [branches and releases](docs/maintenance.md#branches-and-releases); release
  checklists and incidents: [MAINTAINING](MAINTAINING.md).

## Commits

- **One change, one commit**, and it is complete: code, its tests and the docs it changes,
  together. It adds no `CHANGELOG.md` line: a release's section is written once, before its tag
  ([docs and CHANGELOG](docs/maintenance.md#docs-and-changelog)). Fix-ups are squashed on the
  topic branch before it lands.
- **The subject names the change** in at most 72 characters, present tense, no prefix ritual
  (`firewall: bootstrap renders the operator scripts`, `known-working: a binary stack is told it
  has no source composition`). The body, when there is one, says why and what was measured; it
  never narrates the process.
- **No generated trailers**, no co-author lines, no ticket numbers in the subject.
- **Never bench identities**: no Wi-Fi credentials, other people's callsigns or private
  addresses in a message or a diff.

## What should be green

Run these locally in a venv with `pip install -e .[dev]` and `pip install -e ./testlab` before
opening the PR. CI runs them and
more on every PR: [what CI enforces](docs/maintenance.md#what-ci-enforces).

| gate | command |
|---|---|
| unit + contract suite | `python -m pytest -q -p no:cacheprovider tests` (variants and why `python -m`: [tests/README](tests/README.md#how-to-run)). CI's test job runs the suite with `-n auto --dist loadfile` (`pytest-xdist`, a dev dependency) |
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
  tags its widest-seam happy and refusal case `@pytest.mark.contract`.
- **Docs in the same commit.** The CLI reference and the operator docs change with the code,
  by the [docs rules](docs/maintenance.md#docs-and-changelog).
- **Both READMEs.** `README.de.md` is a full translation: a change to `README.md` lands with its
  German twin.
- **No architecture change without a discussion first.** Open an issue; the
  [architecture](docs/architecture.md) doc is the model to argue against.
- **Prefer deletion.** Compatibility shims, feature flags and do-nothing knobs are not taken.

## Adding a stack

[docs/adding-a-stack.md](docs/adding-a-stack.md) is the recipe; the manifest is the contract.
A new stack comes with its `docs/stacks/<stack>.md`, a row in the README's stacks table and a row
in the [release test matrix](docs/test-matrix.md).
