# LHPC test suite

A test must fail because LHPC did the wrong thing — never because a name, a refactor, a CSS class,
a sentence or an equivalent JavaScript rewrite changed.

## What each layer proves

| layer | proves | where |
|---|---|---|
| ordinary tests | LHPC's own behaviour, against injected fakes. No radio, no root, no network, no browser. | this directory |
| testlab **unit** | the simulator itself: provider, runner, scenarios, fake systemd/NetworkManager, its own safety. | `testlab/tests/unit` |
| testlab **acceptance** | the real `lhpc` executable and the real server, end to end against a simulated host. | `testlab/tests/acceptance` |
| testlab **browser** | the console in real headless Chromium: JavaScript, DOM, navigation, layout. | `testlab/tests/browser` |
| testlab **release** | every stack a pin release may move: installed on its default channel, built, started, and proved to BE the candidate manifest's commits. | `testlab/tests/release` |
| meshcore host tests | LHPC's adapter against the pinned real openHop API. | `lhpc/data/meshcore_host` |
| release / live matrix | a real Pi, kernel and radios. Nothing below replaces it. | [docs/test-matrix.md](../docs/test-matrix.md) |

Three rules decide where something belongs:

1. **Test the cheapest stable seam that actually proves the behaviour.** A browser test that
   re-proves core logic is in the wrong place.
2. **One canonical owner per behaviour.** Overlap is justified only where the upper layer adds a
   behaviour of its own — a distinct safety invariant, say, not merely a second look at the same one.
3. **Be exact only where exactness itself is the contract.**

## Where a new test goes

By the behaviour it protects, not by how it is written:

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

Put a test where a maintainer would look when changing that behaviour. Never in a file named after
when or how a defect was found.

## The rules

1. **Behaviour, not implementation.** No `inspect.getsource`, no reading production `.py`/`.js`/`.css`
   to assert what it contains, no pinning statement order or helper names. The rare exception is a
   negative invariant over a whole module ("this file spawns nothing") that no driven path can prove;
   it says so in its docstring and has a behavioural twin.
2. **Exact where exactness is the contract.** Systemd units, nginx config, firewall rules, generated
   argv, config rendering, persisted schemas, receipts, permissions and canonical paths are compared
   byte for byte on purpose. Human sentences are not: assert the typed field, the state, or the
   command token an operator is told to run.
3. **One canonical owner per behaviour.** Fold true permutations into `parametrize`. Never merge
   cases that cross a safety boundary, mutate versus not, or hold different locks.
4. **No ambient host or repository dependencies.** A test must not care which sibling repositories are
   cloned, whether a daemon happens to run here, or what the developer's `$HOME` contains. Live-remote
   pin checking belongs to CI's `pin-validation` lane.
5. **Structural HTML queries.** Use `htmlq` (`doc.by_id(...)`, `doc.field_default(...)`) or a readable
   regex. Not `body.split(...)`, not exact tag strings, not CSS class spelling.
6. **Browser behaviour goes in a real browser.** Playwright with `headless=True`, waiting on observable
   state. No `wait_for_timeout`, no sleeps, no hand-built DOM.
7. **No sibling-test imports and no `sys.path` edits.** Share through a fixture in the nearest
   `conftest.py`, or one of the two plain helper modules here (`repo_paths.py`, `htmlq.py`).
8. **Autouse fixtures isolate the host, and say so.** They give the test a temporary runtime root,
   HOME and firewall state, refuse real downloads and real `pip install`, and reap spawned helpers.
   The two that supply a product baseline — radio hardware and a graphical session — are opt-out by
   marker (`no_default_hardware`, `no_default_display`), because nearly every test wants a working box.
9. **Prefer the injected `System` to patching a private method.** `FakeSystem(commands=…, files=…)` is
   the seam. Patch a private only to stub a collaborator, and say why in a comment.
10. **A test must be able to fail.** No `assert True` fallback, no conditional body that can do
    nothing, no skip that hides functionality CI actually supports.

**Coverage is diagnostic, not the design target.** A new test should normally accompany a
behaviour or a defect; do not add one because a line is uncovered, and do not keep one that only
covers lines.

## Markers

`contract` and `safety` are the two lanes worth reading. `slow` excludes the real-venv builds and
timed loops. `requires_zstd`, `needs_session`, `needs_nonroot`, `no_default_hardware` and
`no_default_display` state a genuine environmental requirement or opt-out. All of them are declared in
`pyproject.toml`, and `--strict-markers` rejects a typo.

`-m contract` is the readable core: each case goes through the widest public seam available — a CLI
verb, a Flask route, or a typed `ActionResult` — and states a happy path or the one refusal that
defines a boundary. `-m safety` is every case guarding a named invariant from the safety model in
[docs/architecture.md](../docs/architecture.md), which is the source of truth for those guarantees.
The two lanes overlap; neither is a subset of the other.

## How to run

Use the **console script**, not `python -m pytest`: the `-m` form puts the working directory on
`sys.path` and hides an import that would die in CI.

```sh
.venv/bin/pytest -q -p no:cacheprovider tests/web/test_webserver.py   # one file
.venv/bin/pytest -q -p no:cacheprovider -m contract                   # the readable core (~12 s)
.venv/bin/pytest -q -p no:cacheprovider -m safety                     # the invariant set
.venv/bin/pytest -q -p no:cacheprovider --basetemp="$HOME/pt-lhpc"    # everything
rm -rf -- "$HOME/pt-lhpc"
```

Always give the full suite a dedicated basetemp and delete exactly that path. On a Pi Zero 2 W the
default lands on a 208 MB tmpfs and the run fills it.

CI measures branch coverage and publishes it; it does not gate on a threshold. A drop is judged in
review.

The lab lanes are off unless asked for:

```sh
LHPC_ACCEPTANCE=1     pytest testlab/tests/acceptance -q
LHPC_BROWSER=1        pytest testlab/tests/browser -q   # pip install -e ./testlab[browser]
LHPC_RELEASE_VERIFY=1 pytest testlab/tests/release -q -x   # installs and builds every stack; -x is required
```

Chromium is needed only for that browser lane. Never install it to run `tests/`, and never on a
release box.

The release lane runs with `-x` wherever a release reads it: its cases chain over one radio pair,
so after the first failure nothing later is judged in a state that means anything — and the
automated release freezes a stack from that JUnit.
