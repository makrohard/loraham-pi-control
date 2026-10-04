# LHPC test suite

A test must fail because LHPC did the wrong thing — never because a name, a refactor, a CSS class,
a sentence or an equivalent JavaScript rewrite changed.

## Contents

- [What each layer proves](#what-each-layer-proves)
- [Where a new test goes](#where-a-new-test-goes)
- [The rules](#the-rules)
- [Markers](#markers)
- [How to run](#how-to-run)

## What each layer proves

| layer | proves | where |
|---|---|---|
| ordinary tests | LHPC's own behaviour, against injected fakes. No radio, no root, no network, no browser. | this directory |
| testlab, five lanes | the simulator; the real executable and server over a simulated host; the console in headless Chromium; a release's evidence; build times on a throttled slow target — [docs/testlab.md](../docs/testlab.md#running-the-verification-lanes). | `testlab/tests/` |
| meshcore host tests | LHPC's adapter against the pinned real openHop API. | `lhpc/data/meshcore_host` |
| RF-log decoder tests | the three decoders against each stack's pinned libraries (meshtastic, openHop, RNS/LXMF): generated keys plus the bench-recorded frames. | `lhpc/data/rfdecode/tests` |
| release / live matrix | a real Pi, kernel and radios; nothing else replaces it. | [docs/test-matrix.md](../docs/test-matrix.md) |

Three principles decide where something belongs:

1. **Test the cheapest stable seam that proves the behaviour.** A browser test that re-proves core
   logic is in the wrong place.
2. **One canonical owner per behaviour.** An upper layer overlaps only where it adds a behaviour of
   its own (a distinct safety invariant, say), not a second look at the same one.
3. **Be exact only where exactness itself is the contract.**

## Where a new test goes

By the behaviour it protects, in the directory a maintainer would look in when changing it — never
in a file named after when or how a defect was found:

- **`core/`** — the controller service: lifecycle, admission, locks, jobs, config, params, status,
  resources, process identity, runtime filesystem.
- **`stacks/`** — the nine radio stacks and the manifest that describes them, GPS included.
- **`web/`** — the Flask console: routes, pages, forms, sessions, CSRF, HMAC, web jobs.
- **`cli/`** — the `lhpc` command's output and exit status.
- **`install/`** — getting code onto the box and keeping it current: source, pins, binary channel,
  auto-install, self-update.
- **`host/`** — the machine LHPC runs on: firewall, network, power, PKI, systemd units, host metrics,
  deployment scripts.
- **`repo/`** — invariants of the repository itself: packaging, versions, README drift, suite hygiene.
- **`golden/`** — the golden set: one characterization module per coordinating operation (start,
  restart, stop, save_config_bundle, build, boot-restore), recording for a few fixed scenarios the
  result fields, the files written/removed and the ORDER of its phases. Every refactor of those
  operations runs it first. Each case's docstring opens with `intended:` (the behaviour is the
  contract) or `known defect <finding id>:` (recorded as is, changed only by the fix of that
  finding). Pinning the step order is its purpose, so it is the one place that names coordinator
  steps: through `ORDER_SEAMS` in its `conftest.py`, which a refactor that renames a step updates.
  Beside it, `golden/test_same_decision_across_entry_paths.py` drives each start decision through
  every entry path (the CLI, the console route with its detached job, the job runner alone, the
  boot-restore unit) and compares the decisions: a rendering difference is `intended`, a decision
  difference is a `known defect` naming one id per difference (`known defect T3-F4, T1-F1:`).
  Its helper `golden/entry_host.py` is the `LHPC_SYSTEM_PROVIDER` that hands every process —
  the console's detached child included, a separate process — the test's box.

A transaction whose state another run must recover (a journal, receipt or marker) is also driven
through `interrupts.py`: its module pins the operation's durable writes and fails each one in turn
(disk full, I/O error, Ctrl-C), then proves recovery and a retry (`core/test_config_interruption.py`).

## The rules

1. **Behaviour, not implementation.** No `inspect.getsource`, no reading production `.py`/`.js`/`.css`
   to assert its contents, no pinning statement order or helper names. Exception: a whole-module
   negative invariant ("this file spawns nothing") no driven path can prove, saying so in its
   docstring and with a behavioural twin.
2. **Exact where exactness is the contract.** Systemd units, nginx config, firewall rules, generated
   argv, config rendering, persisted schemas, receipts, permissions and canonical paths are compared
   byte for byte. Human sentences are not: assert the typed field, the state, or the command token
   an operator is told to run.
3. **One canonical owner per behaviour** (above). Fold true permutations into `parametrize`; never
   merge cases that cross a safety boundary, mutate versus not, or hold different locks.
4. **No ambient host or repository dependencies.** A test must not care which sibling repositories are
   cloned, whether a daemon runs here, or what the developer's `$HOME` contains. Live-remote pin
   checking belongs to CI's `pin-validation` lane.
5. **Structural HTML queries.** Use `htmlq` (`doc.by_id(...)`, `doc.field_default(...)`) or a readable
   regex. Not `body.split(...)`, not exact tag strings, not CSS class spelling.
6. **Browser behaviour goes in a real browser.** Playwright with `headless=True`, waiting on observable
   state. No `wait_for_timeout`, no sleeps, no hand-built DOM.
7. **No sibling-test imports and no `sys.path` edits.** Share through a fixture in the nearest
   `conftest.py` or a helper module here (`repo_paths.py`, `htmlq.py`, `seams.py`; a directory may
   carry its own, e.g. `install/gitrepo.py`, for its tests only). Exception: the two in-package
   suites (`lhpc/data/meshcore_host/tests`, `lhpc/data/rfdecode/tests`) run under a stack's own
   interpreter, so their `conftest.py` inserts their package on `sys.path` — that insert only.
8. **Autouse fixtures isolate the host, and say so.** They give the test a temporary runtime root,
   HOME, firewall state and an empty host process/socket table, refuse real binary-channel downloads and
   real `pip install`, and reap spawned helpers. The two that supply a product baseline — radio hardware
   and a graphical session — are opt-out by marker (`no_default_hardware`, `no_default_display`).
   Two sanctioned gaps, both in `host/test_deploy_scripts.py` and stated there: the `slow`
   full-install tests run `install.sh` as shipped, so pip reaches PyPI from a bash subprocess the
   in-process guard cannot see ([the planned fix](../docs/maintenance.md#gates-that-still-fetch-at-test-time));
   and the module skips on a host with real LHPC firewall state under `/etc/lhpc`, because
   `uninstall.sh`'s preflight reads the canonical roots and would judge that host, not the script.
9. **Prefer the injected `System` to patching a private method.** `FakeSystem(commands=…, files=…)` is
   the seam. Patch a private only to stub a collaborator, and say why in a comment.
10. **A test must be able to fail.** No `assert True` fallback, no conditional body that can do
    nothing, no skip that hides functionality CI supports. A call whose `ActionResult` the test
    relies on is asserted (`.ok`, or the refusal it expects), never discarded; a decision-bearing
    value (a command, a lock key, a unit line, a hash) is compared whole, not by its prefix.
11. **A fake keeps the real signature.** `tests/conftest.py` binds every call of a
    `monkeypatch.setattr` fake of an LHPC function to the real signature and fails the test on a
    call the real one would refuse (`repo/test_fake_signatures.py`).
12. **A regression test is red before its fix.** Run it once against the parent of the fix and say
    so in the commit; a case that passes on both sides is a control and is labelled as one.

**Coverage is diagnostic, not the target:** a new test should normally accompany a behaviour or a
defect; no test added or kept only because it covers lines.

## Markers

All markers are declared in `pyproject.toml`; `--strict-markers` rejects a typo.

- `-m contract` is the readable core: each case goes through the widest public seam — a CLI verb, a
  Flask route, or a typed `ActionResult` — and states a happy path or the one refusal that defines a
  boundary.
- `-m safety` is every case guarding a named invariant of the
  [safety model](../docs/architecture.md). The two lanes overlap; neither is a subset of the other.
- `slow` marks the real-venv builds and timed loops.
- `requires_zstd`, `needs_session`, `needs_nonroot`, `needs_git_checkout`, `no_default_hardware` and
  `no_default_display` state an environmental requirement or opt-out; `needs_git_checkout` skips in a
  source export and fails under CI (`CI` set).

## How to run

Use **`python -m pytest`**, as CI does: `-m` puts the working directory on `sys.path`, so the
imported tree and the tree `--cov=lhpc` measures are the same; the console script imports the
installed package and coverage of the checkout reads near zero.

```sh
python -m pytest -q -p no:cacheprovider tests/web/test_webserver.py   # one file
python -m pytest -q -p no:cacheprovider -m contract                   # the readable core
python -m pytest -q -p no:cacheprovider -m safety                     # the invariant set
python -m pytest -q -p no:cacheprovider --basetemp="$HOME/pt-lhpc"    # everything
rm -rf -- "$HOME/pt-lhpc"
```

Give the full suite a dedicated basetemp and delete exactly that path —
[why](../docs/maintenance.md#running-on-a-pi). The lab lanes are opt-in:
[docs/testlab.md](../docs/testlab.md#running-the-verification-lanes). Chromium is needed only for
the browser lane; never install it to run `tests/`, and never on a release box.
