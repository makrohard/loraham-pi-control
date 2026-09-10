# Backlog — accepted deferrals

Known gaps that were reviewed, judged not release-blocking, and deliberately left
for a later change. Each entry says what holds the line today, so the next person
knows what they are relying on before they touch it.

## Contents

- [Two-stage unit-template migration](#two-stage-unit-template-migration)
- [Transitive build-dependency source locks](#transitive-build-dependency-source-locks)
- [SX1262 on 868 — not run on hardware](#sx1262-on-868--not-run-on-hardware)
- [On-air coverage](#on-air-coverage)
- [Independent review](#independent-review)
- [Contract gaps](#contract-gaps)
- [Safety invariant IDs](#safety-invariant-ids)
- [No `--live` interface](#no---live-interface)
- [Operator self-update prints steps it already took](#operator-self-update-prints-steps-it-already-took)
- [Chat source path moves when the daemon repin lands](#chat-source-path-moves-when-the-daemon-repin-lands)

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

**Holding the line:** `tests/host/test_updater_units.py::test_unit_bytes_are_the_frozen_render`
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
`tests/stacks/test_reticulum_stack.py::test_changing_a_consumed_source_invalidates_the_completed_receipt`
drives the real `is_built()` against a real marker file.

**What a real fix needs:** acquire source locks for the component *and every
transitive build dependency*, hold them for the build's lifetime, and derive the
final receipt only once all are held.

## SX1262 on 868 — not run on hardware

The 868 path of the direct-SPI driver is code-complete and shares everything but the band profile
with the on-air-proven 433 path, so treat a first 868 run as a hardware test, not a regression
check. Which profile has run on silicon: [stacks/reticulum.md](stacks/reticulum.md).

## On-air coverage

Each RF stack has been accepted on air against a real vendor peer at near-field range, so nothing
is proven about range. Still never exercised, and accepted as such:

- MeshCom is proven at packet level (an MHeard entry); message **content** has not been verified
  end to end.
- Graywolf's beacon is proven **on demand**; a scheduled beacon has never been observed across a
  slot boundary. No defect is established.
- MeshCore's repeater is proven forwarding its own traffic only — not between two third-party
  nodes — and group messaging and telemetry are untested.

## Independent review

None of the safety model in [architecture.md](architecture.md) has been reviewed by anyone
outside the project. The tests named next to each guarantee are the only evidence.

## Contract gaps

Promises whose widest-seam case is missing — the priority list for the next test-quality pass
(`tests/README.md` has the tiers):

1. no real `POST /firewall/configure` route test that observes an applied effect (only
   GET-redirect + settings-render exist; apply/fail-closed is proven only at the `ActionResult`
   seam);
2. boot restore has NO isolation-safe case — the whole `tests/core/test_boot_restore.py` is
   `needs_session` at module scope (split out the pure route-toggle tests to give it a
   sandbox-safe contract case);
3. no route-level binary-channel SWITCH test (only the install confirmation's channel selection);
4. no `/action` POST test for `op=uninstall`/`op=clean` refuse-while-running at the web seam;
5. no direct `/hardware` setup POST test (only `/hardware/probe`);
6. lhpc's own suite has no route-table gate; the coverage matrix lives in
   [testlab](testlab.md) and runs in that package's CI lane;
7. no single composite "TX opt-in + tests + callsign" gate test (covered by several separate ones).

## Safety invariant IDs

The safety model has no enumerated invariant registry; `@pytest.mark.safety` ids are descriptive
slugs (the set in use is in `tests/`). A canonical invariant table next to the safety model in
[architecture.md](architecture.md) would let the ids map cleanly.

## No `--live` interface

`--live` is deliberately absent from the CLI and the service params: that interface is not
frozen, so it is not offered. Live daemon tuning stays the whitelisted CONF-socket path
([stacks/daemon.md](stacks/daemon.md)).

## Operator self-update prints steps it already took

`lhpc self-update --apply` in an operator shell stops `lhpc-web`, applies, syncs the venv and
starts the console again (`service_selfupdate.self_update_apply_operator`), but it returns the
result built for the request/service path — so it still prints *"restart the web console to load
it"*, the editable-install command and the *"Dependencies changed"* note for work it has already
done. The wording is service-blind as well: `selfupdate.restart_instructions` reads
`INVOCATION_ID` of the *calling* shell, so a box with the managed unit installed is told to press
Ctrl-C and re-run `lhpc web`.

Observed on the reference box on 2026-09-10 taking it from 0.3.1 to 0.3.11: the pip sync that
followed the advice was a no-op and the console was already serving the new version.

**Holding the line:** only the guidance is stale. What the path *does* is covered by its own
tests, and following the printed steps is harmless — the editable install is idempotent and so is
a restart.

## Chat source path moves when the daemon repin lands

**`lorachat_ncurses_113.c` has moved to `clients/chat/lorachat_ncurses_113.c`** in the
LoRaHAM_Daemon restructure. It is on that repo's `dev` (2e9c7e0) only; `main` is still
`dbd2998b7e69`, which is what both our pins name, so nothing is broken today.

What holds the line: `src/LoRaHAM_Daemon` is `track = "manual"` in the release bot's
`policy.toml`, so the bot cannot move that pin on its own. Chat's `build`/`build_steps` still
name the root path, and at the pinned commit that is where the file is.

The trap is that the two changes are one change. Editing the recipe before the pin moves breaks
chat at the current pin; moving the pin before editing the recipe breaks chat at the new one. So
when the daemon repin lands, the manifest edit goes in the **same commit**:

```toml
build       = "gcc clients/chat/lorachat_ncurses_113.c -o loraham_chat -lncurses -lpthread"
build_steps = [ { argv = ["gcc", "clients/chat/lorachat_ncurses_113.c", "-o", "loraham_chat",
                          "-lncurses", "-lpthread"], attributable = true } ]
```

Nothing else changes: the binary is still written as `loraham_chat` at the source root, so `bin`,
`run`, `run_argv`, `run_cwd`, the source `path` and `attributable = true` are all untouched. The
recipe above was compiled from the new path by the author of the move before it was written down.

`loraham_daemon/build.sh` — the other path we consume from that repo — is **not** moving; that
was settled before the restructure started, because we encode it in `build`, `publish_roots`,
`proof_paths` and `probes`.

One related invariant, recorded because it is easy to break by accident from the other side:
chat's component is `artifact = true`, so its non-pinned selectors resolve to **default-branch
HEAD**. That repo's default branch must stay `main`; pointing it at `dev` would put in-progress
restructure work onto real boxes through a selector nobody thought they were changing.
