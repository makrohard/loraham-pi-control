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
3. **One canonical test per behaviour + its real edge cases.** Fold near-duplicate permutations into a
   single `@pytest.mark.parametrize`. Don't add a second test that proves the same path with a trivially
   different input.
4. **Critical safety tests map to a known invariant** (the P0/P1 model in `docs/hardening-0.1.md`).
   Ordinary behavioural tests just need a clear purpose in their name/docstring.
5. **Organise by behaviour, not by dev milestone.**

## Untouchable safety areas — do not weaken these

These guard RF, exposure, destructive, and corruption invariants. Slim them only by parametrizing
genuine duplicates; never delete a distinct guard. `docs/hardening-0.1.md` is the spec.

- **RF / TX safety** — TX never auto-enabled; TX actions need explicit opt-in + passing tests + a
  callsign; daemon TXMODE apply/readback gating; bounded one-frame TX test.
  (`test_lifecycle`, `test_daemon_readiness`, `test_truthful_outcomes`, `test_auto_install` TX gates.)
- **Resource coordination** — one physical band/SPI owner at a time; conflicting starts refused;
  reslock serialization; recheck-running-after-locks. (`test_reslock`, `test_resource_coord`,
  `test_op_serialization`, `test_race_safety`.)
- **Exposure fail-closed** — remote exposure is opt-in with typed `enable-remote` /
  `enable-remote-danger`; nginx `_listen` loopback fail-safe; loopback-only bind; mTLS access modes.
  (`test_webserver_nginx`, `test_webserver_blockers`, `test_webserver_evidence`,
  `test_webserver_corrections`, `test_webserver_serve`, `test_web_error_boundary`.)
- **Destructive-action guards** — uninstall/clean refuse while running / on identity drift; typed
  stack-id confirmation; shared checkouts + config/secrets preserved. (`test_uninstall_safety`,
  `test_clean`, `test_source_fs`.)
- **Data integrity** — descriptor-anchored atomic writes (0600 where required); config-bundle
  all-or-recoverable transaction + journal recovery; path containment / no-follow / anchored runtime
  FS; manifest validation. (`test_runtime_fs*`, `test_config_bundle`, `test_containment` family,
  `test_manifest_*`.)
- **PKI / revocation** — two-CA independence, keys 0600, `0.0.0.0` never a SAN, transactional
  revocation (CRL-first, partial → pending). (`test_pki`, `test_webserver_corrections`.)
- **Process identity / kill safety** — signal only an LHPC-owned leader whose full identity matches;
  PID-reuse safe; never the controller's own group. (`test_process_ownership`, `test_proctree`.)
- **Byte-exact managed renders** — systemd unit / nginx config integrity + verify verdicts.
  (`test_updater_units`, `test_deployment`, `test_stackweb`, `test_webserver_nginx` fixtures.)
- **Read-only / bounded** — GET/page-load does no network/subprocess/mutation (P0.6); bounded runners
  and daemon parsers fail closed. (`test_web::test_get_routes_make_no_network_calls`,
  `test_bounded_runner`, `test_daemon_bounds`.)

## Running

```
.venv/bin/python -m pytest -q -p no:cacheprovider          # whole suite
.venv/bin/python -m pytest -q tests/test_web.py -p no:cacheprovider
```

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
