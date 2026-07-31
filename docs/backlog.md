# Backlog — accepted deferrals

Known gaps that were reviewed, judged not release-blocking, and deliberately left
for a later change. Each entry says what holds the line today, so the next person
knows what they are relying on before they touch it.

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

**Holding the line:** `tests/test_updater_units.py::test_unit_bytes_unchanged_since_0_1_6`
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

## SX1262 on 433

Hardware-verified on 868 (TX and RX against an SX1276 peer). The 433 path is
code-complete but has not been run on hardware. See `docs/test-matrix.md`.
