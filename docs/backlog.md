# Backlog — accepted deferrals

Known gaps reviewed and left for a later change. Each entry says what holds the line meanwhile.

## Contents

- [Two-stage unit-template migration](#two-stage-unit-template-migration)
- [Transitive build-dependency source locks](#transitive-build-dependency-source-locks)
- [SX1262 on 868 — not run on hardware](#sx1262-on-868--not-run-on-hardware)
- [On-air coverage](#on-air-coverage)
- [Independent review](#independent-review)
- [Contract gaps](#contract-gaps)
- [Safety invariant IDs](#safety-invariant-ids)
- [No `--live` interface](#no---live-interface)
- [Gates that still fetch at test time](#gates-that-still-fetch-at-test-time)

## Two-stage unit-template migration

**The unit templates in `lhpc/core/updater_units.py` cannot change yet**
([the units](deployment.md#run-it-under-systemd)): a changed byte, even a comment, strands every
installed box — a non-canonical unit makes boot restore refuse, so the box comes back from a
power cycle with nothing running. The update path cannot repair this:

* `service_selfupdate._refresh_units_post_update()` runs **in process**, so it renders the
  *pre-update* templates;
* the systemd helper cannot write units at all (`ProtectHome=read-only`, writable paths limited
  to the runtime root and `/tmp`).

Only detection exists: verification runs out of process against the new checkout, and a failure
makes the update visibly partial.

**Holding the line:** `tests/host/test_updater_units.py::test_unit_bytes_are_the_frozen_render`.

**Workaround for new writable paths:** redirect the state into the runtime root with an
environment variable instead of granting a HOME path (Sideband:
`KIVY_HOME={runtime}/state/sideband/kivy`).

**What a real fix needs:** a migration that writes units from the *new* templates in a context
allowed to write them — staged outside the sandboxed helper, applied before boot restore next
evaluates canonicality, and able to roll back. Design it before the first release that must
change a unit.

## Transitive build-dependency source locks

Detached builds lock only the component's **own** checkout, so a `build_requires` dependency can
move while the build runs.

**Holding the line:** the build marker is a *receipt*. `is_built()` recomputes the consumed
source SHAs; if a dependency ended at a different SHA the component reads **not built** and
cannot start as a completed build. Sideband reading "built" with an obsolete copied plugin is
closed by `build_requires = ["rns-lora-interface"]`. Regression:
`tests/stacks/test_reticulum_stack.py::test_changing_a_consumed_source_invalidates_the_completed_receipt`.

**What a real fix needs:** lock the component *and every transitive build dependency* for the
build's lifetime, and derive the receipt only once all are held.

## SX1262 on 868 — not run on hardware

The direct-SPI driver's 868 path is code-complete and differs from the on-air-proven 433 path
only in the band profile; treat a first 868 run as a hardware test. Which profile has run on
silicon: [stacks/reticulum.md](stacks/reticulum.md).

## On-air coverage

Each RF stack is accepted on air against a real vendor peer at near-field range; nothing is
proven about range. Never exercised:

- Graywolf's scheduled beacon across a slot boundary (the on-demand beacon is proven; no defect
  is established).
- MeshCore's repeater forwarding between two third-party nodes (only its own traffic is proven).
- Removing the daemon's TX guard delays (433: 10 + 10 ms before TX; 868: 50 ms after TX): their
  need cannot be measured within a 1 % duty cycle, so they stay.

## Independent review

Nobody outside the project has reviewed the [safety model](architecture.md#safety-model); the
tests named next to each guarantee are the only evidence.

## Contract gaps

Promises whose widest-seam case is missing — the next test-quality pass (`tests/README.md` has
the tiers):

1. no `POST /firewall/configure` route test observing an applied effect (apply/fail-closed is
   proven only at the `ActionResult` seam);
2. `tests/core/test_boot_restore.py` is `needs_session` at module scope — no isolation-safe case
   (split out the pure route-toggle tests);
3. no route-level binary-channel SWITCH test (only the install confirmation's channel selection);
4. no `/action` POST test for `op=uninstall`/`op=clean` refuse-while-running;
5. no direct `/hardware` setup POST test (only `/hardware/probe`);
6. no route-table gate in lhpc's own suite; the coverage matrix is in [testlab](testlab.md) and
   runs in that package's CI lane;
7. no single composite "TX opt-in + tests + callsign" gate test (covered by several separate ones).

## Safety invariant IDs

There is no enumerated invariant registry; `@pytest.mark.safety` ids are descriptive slugs (the
set in use is in `tests/`). A canonical invariant table in the
[safety model](architecture.md#safety-model) would let the ids map to it.

## No `--live` interface

`--live` is absent from the CLI and the service params because that interface is not frozen.
Live daemon tuning is the whitelisted CONF-socket path ([stacks/daemon.md](stacks/daemon.md)).

## Gates that still fetch at test time

Three test gates reach the network while they run:
[maintenance](maintenance.md#gates-that-still-fetch-at-test-time).
