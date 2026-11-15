# LHPC test suite

A test must fail because LHPC did the wrong thing — never because a name, a refactor, a CSS class,
a sentence or an equivalent JavaScript rewrite changed.

## Contents

- [What each layer proves](#what-each-layer-proves)
- [Where a new test goes](#where-a-new-test-goes)
- [The rules](#the-rules)
- [Markers](#markers)
- [How to run](#how-to-run)
- [Rules by area](#rules-by-area)

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
  `golden/test_harness.py` checks the harness itself: a write moved outside its lock is reported.
  `golden/test_golden_argv_r1.py` holds every component's resolved r1-instance argv to the base
  recorded in `golden/argv_r1_0af1d413.json`, except for the tokens its `EXCEPTIONS` names.

`tools/check_houserules.py` refuses (HARD) a new or renamed test file outside these folders and their
`testlab/tests/` counterparts; which of them a test belongs in is the table above. A test of lhpc code
belongs in `tests/<area>`, not in `testlab/tests/unit`, which tests the simulator itself.

A transaction whose state another run must recover (a journal, receipt or marker) is also driven
through `interrupts.py`: its module pins the operation's durable writes and fails each one in turn
(disk full, I/O error, Ctrl-C), then proves recovery and a retry (`core/test_config_interruption.py`).

## The rules

1. **Behaviour, not implementation.** No `inspect.getsource`, no reading production `.py`/`.js`/`.css`
   to assert its contents, no pinning statement order or helper names. Exception: a whole-module
   negative invariant ("this file spawns nothing") no driven path can prove, saying so in its
   docstring and with a behavioural twin. Second exception: a documentation-integrity test may
   assert that a symbol named by `docs/` is defined in its file; it asserts existence, never
   behaviour or source text.
2. **Exact where exactness is the contract.** Systemd units, nginx config, firewall rules, generated
   argv, config rendering, persisted schemas, receipts, permissions and canonical paths are compared
   byte for byte. Human sentences are not: assert the typed field, the state, or the command token
   an operator is told to run. A sentence assert beside a typed assert is allowed: it checks the
   message, not the decision. The CLI layer's only output is text: there the exit code and the
   command tokens are the typed fields.
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
13. **The service family does not grow.** `repo/test_module_budgets.py` caps the lines and public
    methods of `services.py` and every `service_*` module it imports relatively (a repository
    invariant, so it may read their source). Review rules the test does not check: new behaviour
    goes into a plain function module, not a mixin; an item that touches the family removes at
    least what it adds; a budget is raised only with `# <item id>: <reason>` on its line. Lowering a
    budget is free; the test enforces fixed caps and never lowers one by itself.

**Exemptions.** `tools/check_houserules.py` counts a WARN pattern's hit on a test line unless the line's comment
carries the tag of that pattern's rule with a reason, `# rule-<n>: <reason>`: source-read is rule 1, network rule 4,
startswith rule 10. A wrong or unknown rule number or an empty reason exempts nothing, and one tag exempts only
its own pattern. The older `# rule-1 exception` exempts `inspect.getsource` and source-read only; `assert True`
has no exemption. The checker lists every exemption in its patterns row. An exemption never hides a failing or
flaky test.

**Coverage is diagnostic, not the target:** a new test should normally accompany a behaviour or a
defect; no test added or kept only because it covers lines.

## Markers

All markers are declared in `pyproject.toml`; `--strict-markers` rejects a typo.

- `-m contract` is the readable core: each case goes through the widest public seam — a CLI verb, a
  Flask route, or a typed `ActionResult` — and states a happy path or the one refusal that defines a
  boundary.
- `-m safety` is every case guarding a named invariant of the
  [safety model](../docs/architecture.md). The two lanes overlap; neither is a subset of the other.
- `wallclock("reason")` marks a test that uses a real clock on purpose (`time.time`, `time.monotonic`,
  `time.sleep`, a `sleep N` child or fake-script body); everything else takes the `fake_clock` fixture of
  `conftest.py`. The checker refuses an unmarked one; a line outside any test carries `# wallclock: <reason>`.
- `slow` marks the real-venv builds, the timed loops and the timed waits (a test that spends less than half its
  wall time on CPU).
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
python -m pytest -q -p no:cacheprovider --basetemp="$HOME/pt-lhpc" tests testlab/tests   # everything
rm -rf -- "$HOME/pt-lhpc"
```

Give the full suite a dedicated basetemp and delete exactly that path —
[why](../docs/maintenance.md#running-on-a-pi). The lab lanes are opt-in:
[docs/testlab.md](../docs/testlab.md#running-the-verification-lanes). Chromium is needed only for
the browser lane; never install it to run `tests/`, and never on a release box.

## Rules by area

Every rule of this suite in one list, with an id (A1 … I5) a review can cite. Where a section above states a rule
in full, the line links to it. The mark after a rule says what enforces it:

- **CI HARD**: checked by CI with `tools/check_houserules.py` over the pushed range; a hit fails the job.
- **CI WARN**: checked by CI with the same tool; the review judges each hit, and an exemption needs its tag
  ([Exemptions](#the-rules)).
- **test**: a test of the suite or the pytest configuration fails.
- No mark: the review checks it.

### A. Placement

- **A1** A test goes by the behaviour it protects, into the folder a maintainer looks in when changing it
  ([Where a new test goes](#where-a-new-test-goes)).
- **A2** Never in a file named after when or how a defect was found: no round or item id in a file name.
- **A3** A new or renamed test file sits in a layer folder, a `testlab/tests/` lane or one of the two in-package
  suites. **CI HARD**
- **A4** `testlab/tests/unit` tests the simulator itself, `acceptance` the real server and executable, `browser`
  the console in headless Chromium, `release` a release's evidence, `slowbuild` the throttled target.
- **A5** Test the cheapest stable seam that proves the behaviour; a browser test that re-proves core logic is
  misplaced.
- **A6** One canonical owner per behaviour ([rule 3](#the-rules)).
- **A7** `golden/` is the one place that names coordinator steps (`ORDER_SEAMS`); each case's docstring opens
  with `intended:` or `known defect <id>:`.
- **A8** A transaction another run must recover is also driven through `interrupts.py`.

### B. What a test asserts

- **B1** Behaviour, not implementation ([rule 1](#the-rules)). **CI HARD** for `inspect.getsource(`, **CI WARN**
  for `read_text(` or `open(` of a literal `lhpc/…` `.py`/`.js`/`.css` path
- **B2** Exact only where exactness is the contract; a sentence never decides ([rule 2](#the-rules)).
- **B3** Structural HTML queries ([rule 5](#the-rules)).
- **B4** Browser behaviour in a real browser, waiting on observable state ([rule 6](#the-rules)).
- **B5** A decision-bearing value (a command, a lock key, a unit line, a hash) is compared whole, not by its
  prefix ([rule 10](#the-rules)). **CI WARN** for `assert ….startswith(`
- **B6** Coverage is diagnostic: no test is added or kept only because it covers lines.

### C. Determinism and time

- **C1** A real clock in a test (`time.time`, `time.monotonic`, `time.sleep`, a `sleep N` child or fake-script
  body) carries `@pytest.mark.wallclock("reason")`, a helper line outside a test `# wallclock: <reason>`;
  everything else takes the `fake_clock` fixture ([Markers](#markers)). **CI HARD**
- **C2** No sleep as synchronisation: a wait ends on an event (a refused `flock`, a marker, a successful
  connect).
- **C3** A server is ready when `connect()` succeeds or it writes its own ready marker; a socket file is not
  readiness.
- **C4** A test is never re-run because it failed, and no retry wraps an assertion.
- **C5** A transfer (a download, a clone, a package fetch) is not our code: it is fetched before the lane (a
  mirror) or retried long, with backoff, around the transfer only; still failing, it reports `EXTERNAL-UNAVAILABLE`,
  never a regression ([testlab](../docs/testlab.md#running-the-verification-lanes)).
- **C6** An intermittent failure: first the facts, then the change that brought it, then its mechanism, named
  as fact or inference with the observation that confirms it; the fix goes where the change lives, and no mitigation comes before the
  mechanism. A red outside one's own change is checked against the fixes already on `dev` before anyone calls it
  flaky.
- **C7** A red run stays red until a cause and a fix answer it; it is not re-run to green.
- **C8** An unhandled exception in a test's thread fails the test (`pyproject.toml`). **test**
- **C9** An AF_UNIX socket path comes from `short_tmp_path` or a short basetemp; the suite also passes with a
  long basetemp.

### D. Isolation

- **D1** No ambient host or repository dependency ([rule 4](#the-rules)).
- **D2** Autouse fixtures isolate the host, and say so ([rule 8](#the-rules)).
- **D3** The injected `System` before a patched private ([rule 9](#the-rules)).
- **D4** A fake keeps the real signature ([rule 11](#the-rules)). **test**
- **D5** No sibling-test imports, no `sys.path` edits, no browser import in `tests/` ([rule 7](#the-rules)).
  **test** for an `import` or an absolute `from` whose root is `tests`, a `test_…` module, `playwright`,
  `selenium` or `pyppeteer`, in a `tests/` module other than `conftest.py`
- **D6** No network in a test. **CI WARN** for `requests.`, `urllib.request` or `socket.create_connection`
- **D7** A suite that uses git never runs under `git rebase --exec` or with an inherited `GIT_*` environment.

### E. A test must be able to fail

- **E1** No `assert True`, no conditional body that can do nothing, no skip that hides what CI supports; a
  relied-on `ActionResult` is asserted ([rule 10](#the-rules)). **CI HARD** for `assert True`
- **E2** A regression test is red before its fix, run against the fix's parent and stated in the commit
  ([rule 12](#the-rules)). **CI WARN** for a commit that adds a module-level `def test_` without "red-before" in
  its message
- **E3** The red-before runs through the unchanged public caller; a `TypeError` from a changed signature is no
  red-before.
- **E4** Mutation check: with each check under test removed, a test fails; every mutant runs with a fresh
  bytecode cache (`PYTHONPYCACHEPREFIX=$(mktemp -d)`).
- **E5** An absence assertion carries an anchor: the same response shows a present sibling (a 200 and an element
  that must be there).
- **E6** No test is weakened (fewer rows, extra stubs, a narrowed setup), deleted or skipped to go green; a
  narrowing that is needed is listed with its reason.
- **E7** `needs_git_checkout` skips in a source export and fails under CI.

### F. Markers

- **F1** Every marker is declared in `pyproject.toml`; `--strict-markers` rejects a typo. **test**
- **F2** `contract` and `safety(id)` as defined in [Markers](#markers).
- **F3** `slow` marks a test that spends less than half its wall time on CPU; `-m 'not slow'` excludes it.
  **CI WARN** for a new test that builds a venv, runs `pip install` or loops on `monotonic` without it
- **F4** `requires_zstd`, `needs_session`, `needs_nonroot`, `needs_git_checkout`, `no_default_hardware` and
  `no_default_display` state an environment need or an opt-out.

### G. Lanes and how to run

- **G1** `python -m pytest`, never the console script; a full run gets a dedicated basetemp, deleted exactly
  afterwards ([How to run](#how-to-run)).
- **G2** A full suite is `tests` and `testlab/tests`.
- **G3** The lab lanes are opt-in (`LHPC_ACCEPTANCE`, `LHPC_BROWSER`, `LHPC_RELEASE_VERIFY`, `LHPC_SLOW_BUILD`)
  and run from the checkout under test with `PYTHONPATH=$PWD:$PWD/testlab`
  ([testlab](../docs/testlab.md#running-the-verification-lanes)).
- **G4** Chromium only for the browser lane: never to run `tests/`, never on a release box.
- **G5** On one machine the unit suite and the acceptance, browser and lab lanes run one after the other, never
  side by side (one lock file for all of them).
- **G6** Every full, acceptance, browser or lab run keeps its whole output in a log file of that run, not only
  its tail.
- **G7** CI on the candidate's own commit is the authority; local runs serve the red-before and focused checks.

### H. Mechanical checks

What a machine enforces today. The checker's other rows (changelog, docs, attribution) are not test rules.

| check | fails on | severity |
|---|---|---|
| checker `tests` row | a new or renamed test file outside the layer folders (A3) | CI HARD |
| checker `tests` row | a commit that adds a module-level `def test_` without "red-before" in its message (E2) | CI WARN |
| checker `patterns` row | `inspect.getsource(`, `assert True` (B1, E1) | CI HARD |
| checker `patterns` row | `read_text(`/`open(` of a literal `lhpc/…` `.py`/`.js`/`.css` path (B1), `assert ….startswith(` (B5), `requests.`/`urllib.request`/`socket.create_connection` (D6), each unless its line carries its rule tag; `time.sleep(` in the in-package suites, without an exemption | CI WARN |
| checker `markers` row | a new test that builds a venv, runs `pip install` or loops on `monotonic`, without `slow` (F3) | CI WARN |
| checker `wallclock` row | a real clock on an added line without `wallclock` (C1) | CI HARD |
| checker `modules` row | the service family over a budget, or grown without a budget raise that carries its reason ([rule 13](#the-rules)) | CI HARD |
| `repo/test_suite_hygiene.py` | in a `tests/` module other than `conftest.py`: an `import` or an absolute `from` whose root is `tests`, a `test_…` module, `playwright`, `selenium` or `pyppeteer` (D5) | test |
| `tests/conftest.py`, `pyproject.toml` | a fake called in a way the real signature refuses (D4); an unknown marker (F1); an unhandled exception in a test's thread (C8) | test |
| ruff | `ruff check lhpc testlab`; `ruff check tests --select F,E9` | CI |

CI runs the checker from the floor commit in `.github/workflows/ci.yml` (`HR_FLOOR`) over the pushed range.

### I. How a cleanup is done

- **I1** Every finding is checked against the code before any change and sorted fix-now, when-touching or
  dropped, each with its file:line and a drop with its reason.
- **I2** A test cleanup commits tests only, one commit per part; a finding that needs production code is listed
  for the maintainer, not fixed in it.
- **I3** A move is a pure rename: `git mv`, content unchanged in the move commit.
- **I4** The simplest change that removes the finding: no new mechanism, option or retry.
- **I5** A cleanup plan opens with these rules and is reviewed before any code; each commit or slice gets its own
  review.
