# Backlog — accepted deferrals

Known gaps that were reviewed, judged not release-blocking, and deliberately left
for a later change. Each entry says what holds the line today, so the next person
knows what they are relying on before they touch it.

## Contents

- [Two-stage unit-template migration](#two-stage-unit-template-migration)
- [Transitive build-dependency source locks](#transitive-build-dependency-source-locks)
- [SX1262 on 868 — not run on hardware](#sx1262-on-868--not-run-on-hardware)
- [Independent review](#independent-review)
- [Contract gaps](#contract-gaps)
- [Coverage-hostile flaky test](#coverage-hostile-flaky-test)
- [Safety invariant IDs](#safety-invariant-ids)
- [No `--live` interface](#no---live-interface)

## Two-stage unit-template migration

**The managed systemd unit templates in `lhpc/core/updater_units.py` are frozen.**
Changing any byte — including a comment — strands every already-installed box:
`verify()` compares byte-for-byte, a non-canonical unit makes boot restore refuse,
and the box comes back from a power cycle with nothing running.

Nothing in the update path repairs this:

* the repair in `service_selfupdate._refresh_units_post_update()` runs **in
  process**, so it renders the *pre-update* templates;
* on the systemd-helper route it cannot write units at all
  (`ProtectHome=read-only`, writable paths limited to the runtime root and
  `/tmp`).

What exists today is *detection*, not repair: verification runs out of process
against the new checkout, and a failure makes the update visibly partial instead
of silently disabling boot restore.

**Holding the line:** `tests/test_updater_units.py::test_unit_bytes_are_the_frozen_render`
pins the rendered bytes of all seven units.

**Workaround for new writable paths:** redirect the state into the runtime root
with an environment variable instead of granting a HOME path. Sideband does this
with `KIVY_HOME={runtime}/state/sideband/kivy`.

**What a real fix needs:** a migration that can write units with the *new*
templates from a context that is allowed to write them — i.e. staged outside the
sandboxed helper, applied before boot restore next evaluates canonicality, and
able to roll back. Design it before the first release that must change a unit.

## Transitive build-dependency source locks

Detached builds acquire a source lock for the component's **own** checkout only.
A component's declared `build_requires` dependency can therefore move while its
build is running, wasting or disturbing that build.

**Holding the line:** the build marker is a *receipt*. `is_built()` recomputes the
consumed source SHAs and compares, so if a dependency ended at a different SHA the
receipt no longer matches, the component reads **not built**, and it cannot start
as a valid completed build. The dangerous case — Sideband reading "built" while
holding an obsolete copied plugin — is closed by
`build_requires = ["rns-lora-interface"]`.

Regression:
`tests/test_reticulum_stack.py::test_changing_a_consumed_source_invalidates_the_completed_receipt`
drives the real `is_built()` against a real marker file.

**What a real fix needs:** acquire source locks for the component *and every
transitive build dependency*, hold them for the build's lifetime, and derive the
final receipt only once all are held.

## SX1262 on 868 — not run on hardware

Tested on the air: the LoRaHAM Pi HAT dual-module controller, the Uputronics dual stack and
the Waveshare SX1262 433M. **Not tested on silicon: the Waveshare SX1262 868M.** The 868 path
of the direct-SPI driver is code-complete and shares everything but the band profile with the
433 path; treat a first 868 run as a hardware test, not a regression check. Dated evidence for the LoRaHAM Pi HAT runs:
[live tests](live-test.md); the driver's hardware notes: [stacks/reticulum.md](stacks/reticulum.md).

## Independent review

None of the safety model in [architecture.md](architecture.md) has been reviewed by anyone
outside the project. The tests named next to each guarantee are the only evidence.

## Contract gaps

Promises whose widest-seam case is missing — the priority list for the next test-quality pass
(`tests/README.md` has the tiers):

1. no real `POST /firewall/configure` route test that observes an applied effect (only
   GET-redirect + settings-render exist; apply/fail-closed is proven only at the `ActionResult`
   seam);
2. boot restore has NO isolation-safe case — the whole `tests/test_boot_restore.py` is
   `needs_session` at module scope (split out the pure route-toggle tests to give it a
   sandbox-safe contract case);
3. no route-level binary-channel SWITCH test (only the install confirmation's channel selection);
4. no `/action` POST test for `op=uninstall`/`op=clean` refuse-while-running at the web seam;
5. no direct `/hardware` setup POST test (only `/hardware/probe`);
6. lhpc's own suite has no route-table gate — the coverage matrix over every route operation,
   form, CLI leaf and stack phase lives in the separate testlab package
   ([testlab.md](testlab.md)) and runs in that package's CI lane;
7. no single composite "TX opt-in + tests + callsign" gate test (covered by several separate ones).

## Coverage-hostile flaky test

`tests/test_stack_params.py::test_same_process_claim_retries_while_ownership_is_unpublished`
flakes UNDER the coverage tracer (0.2 s / 0.05 s threading windows); green without `--cov`.
If a coverage run red-flags only it, deselect it from the `--cov` run and verify it separately
without instrumentation.

## Safety invariant IDs

The safety model has no enumerated invariant registry; `@pytest.mark.safety` ids are
descriptive slugs (`RF-TX-opt-in`, `firewall-fail-closed`, `exposure-fail-closed`, `P0.5`
uninstall, `P0.6` GET-no-network). A canonical invariant table next to the safety model in
[architecture.md](architecture.md) would let the ids map cleanly.

## No `--live` interface

`--live` is deliberately absent from the CLI and the service params: that interface is not
frozen, so it is not offered. Live daemon tuning stays the whitelisted CONF-socket path
([stacks/daemon.md](stacks/daemon.md)).
