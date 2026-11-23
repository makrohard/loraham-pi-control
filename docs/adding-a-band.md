# Adding a band

For any future coder, human or agent: how LHPC knows its radio bands, and what to change to add one (for example
915 MHz), to take one away from a stack, or to switch one off in an upstream app. Each step names the file and the
symbol; no line numbers. The worked examples are the 433/868 split of Voice and Reticulum (0.14.0).

## Contents

- [How it fits together](#how-it-fits-together)
- [Add a band](#add-a-band)
- [One band left: a fixed-band stack](#one-band-left-a-fixed-band-stack)
- [A frequency on a band a stack already has](#a-frequency-on-a-band-a-stack-already-has)
- [Switch a band off in an upstream app](#switch-a-band-off-in-an-upstream-app)
- [How to verify](#how-to-verify)

## How it fits together

| layer | what it holds | where |
|---|---|---|
| Band list | every band LHPC knows, by name (MHz) | `lhpc/core/known_bands.py` `ALL` — the ONE list; code reads `known_bands.ALL` / `known_bands.is_known()` when it runs, so one edit reaches every check, regex and message |
| Hardware | the bands each radio model and board preset can tune | `lhpc/data/radio_models.toml` (`bands`, a variant's `band`); `lhpc/core/config.py` `HW_SETUPS` (a preset per band) |
| Daemon | one LoRaHAM daemon per radio, started with `--radio <band>`; its base values and each client stack's profile per band | the daemon itself (outside LHPC: it must accept the band and open its sockets, `lhpc/core/daemon_control.py` `conf_socket`); `lhpc/core/daemon_params.py` `_BASE`, `STACK_DEFAULTS` |
| Claims | a radio, daemon-socket or profile claim per band | manifest `[[stack.component.resource]]` keys such as `loraham.radio.{radio:868}`; `lhpc/core/resources.py` reads them |
| Stack | the bands a stack runs on, its settings per band | manifest component `band` (its default band) and `bands` (a choice); `lhpc/core/instances.py` `stack_bands`, `radios_of` |
| Per-band setting | a setting whose default depends on the band | manifest param `band_defaults = { "433" = "…", "868" = "…" }` beside `default`; `{band}` in a file key or default renders the instance's band |
| Licence | ham or free per stack (manifest `license`) | it does **not** pick the band: "ham 433 / free 868" is today's fact per stack, not a rule in code |

The dual-board SPI rule (`lhpc/core/service_lifecycle_ops.py` `_spi_shared_warning`, `SPI_SHARED_WARNING`) names
433 and 868 because the LoRaHAM board's two modules share SPI bus 0: a hardware fact, not the band list.

## Add a band

1. **Daemon first.** The LoRaHAM daemon must accept `--radio <band>`; without that nothing below starts.
2. **Band list.** Add the band to `known_bands.ALL`. That is the only list in code.
3. **Values per band** (the list does not give them; each place needs one):
   - `lhpc/data/radio_models.toml`: `bands` of each model that can tune it; `lhpc/core/config.py` `HW_SETUPS`
     presets for the boards.
   - `lhpc/core/daemon_params.py`: `_BASE` (its `FREQ`) and each client profile in `STACK_DEFAULTS` that runs there.
   - The manifest: each stack that offers the band gets it in `bands`, a claim for it where the stack claims per
     band, and a value in every `band_defaults`. `lhpc/core/manifest.py` checks only `band`/`bands`; a missing
     `band_defaults` entry silently falls back to `default` (`lhpc/core/model.py` `band_default`), so give every
     per-band setting a value. Keys may be quoted (`"433" = …`) or bare (`433 = …`).
   - Data files read by the apps: `lhpc/data/bases/reticulum.conf`, `lhpc/data/bases/meshcore.toml`.
   - The demo: `demo/lhpc_demo/daemon_sim.py` gives values per band (its `else` means "not 433 = 868": extend it).
   - Test-side regexes written by hand: `tests/repo/test_radios_named_by_id_not_band.py` (`BAND_SOCKET`,
     `LEGACY_FORMS`), `tests/repo/test_band_socket_guard_canaries.py`, `demo/tests/probe.py` (its log-name and band
     loops read `known_bands` already; check its asserts).
4. **Rules.** Power caps and duty cycle are regional: record the source (regulator, upstream region table) in the
   manifest comment beside each value. Frequency bounds work by param kind: `min`/`max` only for
   `kind = "int"`/`"float"` (Hz ints: MeshCore, Reticulum); a `kind = "str"` param with `validator = "freq"` (MHz,
   Voice) is bounded only to 1-6000 MHz (`lhpc/core/validators.py` `freq`).
5. **Tests.** See [How to verify](#how-to-verify).

## One band left: a fixed-band stack

Taking a band away from a stack (0.14.0: Voice keeps 433, Reticulum keeps 868):

1. Delete `bands` on **every** component of the stack; each keeps its `band`. With no `bands` the web band chooser
   disappears (`lhpc/core/service_lifecycle_ops.py`: `multi = bool(self.stack_bands(...))`), launch records carry no
   band, and boot restore skips an old record on the dropped band with its why (`lhpc/core/service_boot_restore.py`).
2. `{band}` now renders as "" (a stack without `bands`): write the band literally in keys and defaults
   (Voice: `433_freq`; Reticulum: the interface `band = "868"`), and drop the row that kept that `{band}` in
   `KEPT_BAND` (`tests/repo/test_radios_named_by_id_not_band.py`).
3. Drop every `band_defaults`; `default` holds the remaining band's value. Reword labels that name the other band. A
   hidden band-switch param the app reads is pinned to the kept band (Voice `active_band`: `default = "0"`, `max = 0`).
4. Remove the stack's claims for the other band, and its profile for that band in `daemon_params.STACK_DEFAULTS`.
5. Tighten the frequency `min` to the band it keeps where the param kind allows it (MeshCore: `min = 863000000`).
6. Update `docs/stacks/<stack>.md` and the band table in `docs/stacks/daemon.md`. A proxied web page moves to the
   kept band's radio, so its suggested port moves too: update the Codespace forwards
   (`.devcontainer/devcontainer.json`) and the port lists in `docs/webserver.md`, `docs/testlab.md`, `docs/firewall.md`.
7. Tests that used the stack as the band-switchable example move to a stack that stays switchable (kiss, graywolf,
   meshtastic) with the same scenario; none is deleted or narrowed silently.
8. CHANGELOG: tell the operator to stop the stack on the dropped band before upgrading. No migration code is needed:
   `instances.radios_of` lists only radios of the stack's bands, and a still-running old instance keeps its recorded
   band until it is stopped.

The band stays in `known_bands.ALL`: other stacks still use it.

## A frequency on a band a stack already has

Another frequency on the same band (for example another 70 cm channel) is a **setting, not a band**: no change to
the band list, the hardware or the claims. Change the stack's `band_defaults` value (or `default` for a fixed-band
stack), or make the frequency a choice (`kind = "enum"`, or a region param like Meshtastic's) so it is picked per
radio in Settings. Keep it inside what the radio model can tune and inside the licence's limits.

## Switch a band off in an upstream app

An app with its own band switch (LoRaHAM Voice: `active_band`, its `lora868` keys and band row) is changed in its
fork, reversibly: a build switch, off by default, under which the band's code still compiles; LHPC keeps writing
the hidden `active_band = 0`. Then the LHPC pin follows the fork (binary first, `lhpc-binaries`).

## How to verify

- `tests/core/test_known_bands.py`: every band the data names is in the list (manifest, radio models, `HW_SETUPS`,
  daemon profiles), and one patch of `known_bands.ALL` with a third band reaches every caller.
- `tests/repo/test_one_band_list.py`: no copy of the list in code (comments and docstrings ignored); its exemptions
  are named by file and symbol.
- `git grep -nE '"433",\s*"868"|433\|868' -- lhpc demo testlab/lhpc_testlab` shows only band-keyed values and docs.
- Run the stack tests of every stack whose bands changed, `tests/golden/`, and the demo tests (`demo/tests/`).
