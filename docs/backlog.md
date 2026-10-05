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

**The first half exists:** the verifier accepts the next release's units as `compatible`
(`updater_units._NEXT_EDITS`): the nginx unit with a true comment on its firewall boot gate, and the
web and boot-restore units without the dead `-%h/.meshcore_nm` path. Boot restore runs on them, so
a box that goes back from the release that ships them still restores
([maintenance](maintenance.md), *Changing a unit*; `tests/repo/test_unit_templates_frozen.py`).

**Open — the release that ships them:**

* the edits become the templates, their digests join `tests/data/unit-templates-released.sha256`,
  and the previous bytes are accepted the same way;
* the update path still cannot write new units: `service_selfupdate._refresh_units_post_update()`
  repairs **in process** with the pre-update templates, and the systemd helper cannot write units
  at all (`ProtectHome=read-only`, writable paths limited to the runtime root and `/tmp`). The
  operator path is to run the repair through the new checkout's CLI; a failure leaves the units as
  they are, recorded *units-stale*. Until then an update that brings new bytes ends *units-stale*
  and `lhpc self-update --repair-integration` finishes it;
* that release proves the forward and mixed unit states on a box. A downgrade below the accepting
  release does not restore at boot until that release's own repair runs.

**Workaround for new writable paths:** redirect the state into the runtime root with an
environment variable instead of granting a HOME path (Sideband:
`KIVY_HOME={runtime}/state/sideband/kivy`).

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
