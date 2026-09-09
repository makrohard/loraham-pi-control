# Contributing

Pull requests are welcome. A PR that satisfies everything below is likely to be taken as is; one
that does not may still be taken, but the chances are lower and it will take longer.

## Branches

- **Open your PR against `dev`.** Work on a topic branch off `dev`, rebase it on `dev` before the
  PR, and land it as **one commit** (squash-merge). The maintainer's own work follows the same path.
- `dev` is rewritten once per MINOR release, when the cycle is squashed into the release commit —
  rebase an open topic branch onto the new `dev` afterwards. A patch release branches from `main`
  and comes back as a fast-forward or a pull request, so it never rewrites `dev`.
- The branch model, the release procedure and the hotfix path:
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

Run these locally before opening the PR — each is one command in a venv with
`pip install -e .[dev]`. CI runs on every push to `main`/`dev` and on every PR, and adds
`pip-audit` and pin validation:
[what CI enforces](docs/maintenance.md#what-ci-enforces).

| gate | command |
|---|---|
| unit + contract suite | `pytest -q -n 12 --dist loadfile -p no:cacheprovider tests` (CI runs it serially, with coverage) |
| lint, frozen ruleset | `ruff check lhpc testlab` and `ruff check tests --select F,E9` |
| security | `bandit -q -r lhpc -lll` |
| testlab unit lane | `pytest -q testlab/tests/unit` — the simulator itself; the acceptance and browser lanes are opt-in, see [testlab](docs/testlab.md) |
| docs | run inside the suite; what it pins: [maintenance](docs/maintenance.md#what-ci-does-not-enforce) |
| shipped snapshot | `lhpc deps --script` must equal `bootstrap-deps.sh` when `lhpc/core/deps.py` changed |

The suite is expected to be fully green locally: no failures, and nothing skipped on an ordinary
developer machine. Coverage is measured, not gated — it is a diagnostic, so do not chase the
percentage, and do not let it drop when you touch `lhpc/`.

## What a good change looks like

- **Tests that fail on the unfixed code.** A bug fix carries a regression; a new capability tags
  its widest-seam happy and refusal case `@pytest.mark.contract`.
- **Docs in the same commit.** The CLI reference, the operator docs and `CHANGELOG.md` change
  with the code. Docs state the current contract only; history lives in the changelog, and
  [live-test.md](docs/live-test.md) holds the newest live run. Numbers in docs are measured,
  never estimated.
- **Both READMEs.** A factual change to `README.md` is mirrored in `README.de.md`.
- **No architecture change without a discussion first.** Open an issue; the
  [architecture](docs/architecture.md) doc is the model to argue against.
- **Prefer deletion.** Compatibility shims, feature flags and do-nothing knobs are not taken.

## Adding a stack

[docs/adding-a-stack.md](docs/adding-a-stack.md) is the recipe; the manifest is the contract.
A new stack comes with its `docs/stacks/<stack>.md`, a row in the README's stacks table and a row
in the [release test matrix](docs/test-matrix.md).
