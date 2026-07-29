# LHPC test suite — what we test and how

The suite protects **behaviour and safety invariants**, not the exact spelling of the UI or messages.
Keep it that way: a test should fail when the *system does the wrong thing*, never merely because some
markup, CSS class, or wording changed.

## Principles

1. **Assert behaviour and contracts, not presentation or wording.** Prefer a status code, a redirect,
   a typed `ActionResult`, a persisted effect, or a structural HTML query over a raw-markup substring.
2. **No markup / CSS / JS-implementation pins.** Don't assert exact `<tag ...>` strings, CSS class
   tokens (`flash-bad`, `col-version`), `data-*` spelling, or the contents of `.js`/`.css` files. To
   check a page structurally, use `tests/htmlq.py`:
   ```python
   from htmlq import parse
   doc = parse(resp.get_data(as_text=True))
   assert doc.by_id("stack-settings-igate").has_attr("open")   # panel open when ?cfg requires it
   assert doc.field_default("dp_MODE") == "FSK"                # rendered default / selected option
   ```
   (`htmlq` is intentionally tiny — if a plain `re.search` reads clearly, that's fine too.)
3. **One canonical test per behaviour; fold true permutations.** When a cluster of tests shares the
   SAME setup, action, observable contract AND side-effect assertions and differs only in input →
   expected, fold it into a single `@pytest.mark.parametrize` with an explicit `pytest.param(..., id=)`
   per case (keep each case's message fragments and state checks in its param). NEVER combine cases
   that cross a safety boundary, mutate-vs-not, raise different exception classes, or hold different
   locks — "they both reject input" is not enough kinship.
4. **Prefer a public or injected dependency over patching a private implementation detail.** Introduce
   a production seam ONLY for a genuine external dependency (an OS/filesystem/subprocess boundary), and
   drive it through the injected `System` (`FakeSystem(commands=…, paths=…, files=…)`) rather than a
   `monkeypatch.setattr(svc, "_private", …)`. A justified private-method patch is fine when it stubs a
   COLLABORATOR to isolate a different unit under test, encodes a DELIBERATE timeout (`_SELF_LOCK_WAIT_S`),
   or exercises a private SAFETY boundary directly — say so in a comment. Do not add a seam merely to
   remove a `monkeypatch` if it makes the test more complex or routes a security boundary
   (receipt/ownership/`O_NOFOLLOW`/fd reads) through a fake.
5. **A regression test guards a bug CLASS or folds into a contract** — not a one-off reproduction that
   duplicates an existing path with a trivially different input.
6. **Optional host-tool tests skip explicitly; mandatory deps are installed, not skipped.** A test that
   needs a host binary the product also needs at runtime (e.g. `zstd` for artifact extraction) carries
   `@pytest.mark.requires_zstd` and skips with a reason when it is absent — it never silently passes.
7. **Critical safety tests map to a known invariant** (the P0/P1 model in `docs/hardening-0.1.md`).
   Ordinary behavioural tests just need a clear purpose in their name/docstring.
8. **Organise by behaviour, not by dev milestone.**

## Untouchable safety areas — do not weaken these

These guard RF, exposure, destructive, and corruption invariants. Slim them only by parametrizing
genuine duplicates; never delete a distinct guard. `docs/hardening-0.1.md` is the spec.

- **RF / TX safety** — TX never auto-enabled; TX actions need explicit opt-in + passing tests + a
  callsign; daemon TXMODE apply/readback gating; bounded one-frame TX test.
  (`test_lifecycle`, `test_daemon_readiness`, `test_post_start` (P0.3 truthful outcomes),
  `test_auto_install` TX gates.)
- **Resource coordination** — one physical band/SPI owner at a time; conflicting starts refused;
  reslock serialization; recheck-running-after-locks. (`test_reslock`, `test_resource_coord`,
  `test_op_serialization`, `test_source` (race-safe destructive ops).)
- **Exposure fail-closed** — remote exposure is opt-in with typed `enable-remote` /
  `enable-remote-danger`; nginx `_listen` loopback fail-safe; loopback-only bind; mTLS access modes.
  (`test_webserver` (apply/nginx/blockers/evidence/corrections/serve), `test_web_error_boundary`.)
- **Destructive-action guards** — uninstall/clean refuse while running / on identity drift; typed
  stack-id confirmation; shared checkouts + config/secrets preserved. (`test_uninstall_safety`,
  `test_clean`, `test_source` (fs guards).)
- **Data integrity** — descriptor-anchored atomic writes (0600 where required); config-bundle
  all-or-recoverable transaction + journal recovery; path containment / no-follow / anchored runtime
  FS; manifest validation. (`test_runtime_fs` (incl. anchored/hardening/containment), `test_config`
  (incl. bundle), `test_manifest_*`.)
- **PKI / revocation** — two-CA independence, keys 0600, `0.0.0.0` never a SAN, transactional
  revocation (CRL-first, partial → pending). (`test_pki`, `test_webserver` (corrections).)
- **Process identity / kill safety** — signal only an LHPC-owned leader whose full identity matches;
  PID-reuse safe; never the controller's own group. (`test_process_ownership`, `test_proctree`.)
- **Byte-exact managed renders** — systemd unit / nginx config integrity + verify verdicts.
  (`test_updater_units`, `test_deployment`, `test_stackweb`, `test_webserver` (nginx fixtures).)
- **Read-only / bounded** — GET/page-load does no network/subprocess/mutation (P0.6); bounded runners
  and daemon parsers fail closed. (`test_web::test_get_routes_make_no_network_calls`,
  `test_bounded_runner`, `test_daemon_bounds`.)

## Layout (after the 2026 consolidation)

Tests are grouped by SUBJECT into ~90 files. Notable consolidated homes:

| file | covers |
|---|---|
| `test_config.py` | layered config: bundle, containment, fail-closed, safety, stable, typed |
| `test_webserver.py` | web console: apply, nginx, evidence, gui, service, serve, blockers, corrections, hardening, cli |
| `test_stackweb.py` | per-stack web exposure: config, service, verify |
| `test_source.py` | managed source: registry, fs, selection, check, transactions, linked, snapshot cache, race-safe destructive ops |
| `test_probes.py` | probes: process/net, unix sockets, systemd, source |
| `test_runtime_fs.py` | anchored runtime FS, hardening, wrapper-anchored, path containment, containment |
| `test_binary_channel.py` | binary channel: receipt, status, predicates, hmac+firewall |
| `test_binary_install.py` | binary install transaction, switching, switch selector |
| `test_task_admission.py` | task-admission contention (incl. admission "holes") |
| `test_post_start.py` | post-start truthful outcomes (P0.3) |

`test_services_hardening.py` intentionally stays standalone — it is an audit-regression bucket spanning
several unrelated subjects, so it is not force-merged into any one subject file.

## Tier 0 — the contract

`-m contract` is the **readable core**: a single lane, ~100 cases, that states what LHPC *promises* —
install/auto-install/start/stop, TX safety, the binary channel, config/params, hardware, firewall,
exposure, HMAC, self-update, boot-restore, uninstall/clean, and the GET-no-mutation guarantee. Every
case is an EXISTING test tagged `@pytest.mark.contract`, chosen to go through the widest public seam
available (a CLI verb, a Flask route, or a typed `ActionResult`) and to state either a happy path or
the one refusal that defines a boundary. Read this lane to learn the system; it runs in ~20s.

`-m safety` is the **invariant set** — the subset of contract cases that guard a named safety
invariant (`@pytest.mark.safety("<id>")`): RF/TX opt-in, firewall fail-closed, exposure opt-in,
uninstall-while-running (P0.5), and GET-no-network (P0.6). Every safety case is also a contract case.

Everything else is the **net**: the full suite is a thorough regression net that nobody is expected to
read top-to-bottom. A change is understood through the contract; it is *protected* by the net.

```
.venv/bin/python -m pytest -q -p no:cacheprovider -m contract    # the readable core (~20s)
.venv/bin/python -m pytest -q -p no:cacheprovider -m safety      # the invariant subset
```

The contract lane is deliberately tagged on isolation-robust cases only. Two promises still lack a
widest-seam case: a real firewall-apply route test, and a sandbox-safe boot-restore case.

## Running — three tiers

1. **Focused (inner loop)** — one file or a `-k` subset while iterating:
   ```
   .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_webserver.py
   ```
2. **Fast lane** — the whole suite minus the genuinely-slow tests (real-bash full-venv installs and
   timed loops carry `@pytest.mark.slow`):
   ```
   .venv/bin/python -m pytest -q -p no:cacheprovider -m "not slow" --basetemp="$HOME/pt-lhpc"
   rm -rf -- "$HOME/pt-lhpc"
   ```
3. **Complete coverage gate** — everything, INCLUDING `slow` and every env-supported test, with
   coverage. This is the gate a change must pass (coverage must not regress):
   ```
   .venv/bin/python -m pytest -q -p no:cacheprovider --cov=lhpc --cov-branch \
       --basetemp="$HOME/pt-lhpc"
   rm -rf -- "$HOME/pt-lhpc"
   ```
   Coverage is NOT enforced via a global `--cov-fail-under` (kept out of `pyproject.toml` on purpose so
   the fast lane and focused runs aren't held to a total); the gate compares coverage against the prior
   baseline instead.

Markers (`contract`, `safety`, `slow`, `requires_zstd`, plus `needs_session` / `needs_nonroot` /
`no_default_hardware`) are
registered once in `tests/conftest.py`.

### Basetemp discipline (a Pi5 once held 19 GB of stray pytest dirs)

Run the suite with a **dedicated, fixed basetemp** and remove exactly that path afterwards — never a
broad glob:

```
.venv/bin/python -m pytest -q -p no:cacheprovider --basetemp="$HOME/pt-lhpc"
rm -rf -- "$HOME/pt-lhpc"
```

On a Pi Zero 2W this is mandatory anyway: the default basetemp lands on the 208 MB `/tmp` tmpfs and
the full suite fills it (ENOSPC). For legacy leftovers under `/var/tmp` (`lpt-*` from older runs):
stop all pytest processes first, then LIST before removing —

```
find /var/tmp -maxdepth 1 -uid "$(id -u)" -type d -name 'lpt-*'
```

review the output, then remove those directories explicitly. Do not delete unrelated `$HOME/pt-*`
paths.
