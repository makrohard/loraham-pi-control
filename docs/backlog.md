# Backlog — accepted deferrals

Known gaps reviewed and left for a later change. Each entry says what holds the line meanwhile.

## Contents

- [Two-stage unit-template migration](#two-stage-unit-template-migration)
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

**Holding the line:** `tests/repo/test_unit_templates_frozen.py` (hashes in `tests/data/unit-templates.sha256`).

**Workaround for new writable paths:** redirect the state into the runtime root with an
environment variable instead of granting a HOME path (Sideband:
`KIVY_HOME={runtime}/state/sideband/kivy`).

**What a real fix needs:** a migration that writes units from the *new* templates in a context
allowed to write them — staged outside the sandboxed helper, applied before boot restore next
evaluates canonicality, and able to roll back. Design it before the first release that must
change a unit.

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

Promises whose widest-seam case is missing — the next test-quality pass ([markers](../tests/README.md#markers)):

1. `tests/core/test_boot_restore.py` is `needs_session` at module scope — no isolation-safe case
   (split out the pure route-toggle tests);
2. no render/CSRF sweep of the route table in lhpc's own suite (`tests/web/test_web.py` sweeps it
   for systemd calls only); that sweep is the lab's `test_http_smoke.py`
   ([testlab](testlab.md#running-the-verification-lanes)).

## Safety invariant IDs

There is no enumerated invariant registry; `@pytest.mark.safety` ids are a P0.x id where one exists,
else a descriptive slug (the set in use is in `tests/`). The
[invariant table](architecture.md#invariant-table) has no id column, so the ids do not map to it.

## No `--live` interface

`--live` is absent from the CLI and the service params because that interface is not frozen.
Live daemon tuning is the whitelisted CONF-socket path ([stacks/daemon.md](stacks/daemon.md)).

## Gates that still fetch at test time

Three test gates reach the network while they run:
[maintenance](maintenance.md#gates-that-still-fetch-at-test-time).
