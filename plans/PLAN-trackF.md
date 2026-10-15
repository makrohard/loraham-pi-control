# Plan — Track F: install channels without nagging, the Meshtastic web client as its own component

Base `origin/dev` 254bbae3. Decisions: DECISIONS-channels-and-web-client; analysis MT-WEB-CLIENT
(61e18c6d). Repos: loraham-pi-control (C), lhpc-binaries (B), lhpc-release-bot (R). binary = the
prebuilt known-working composition, quick, no questions; pinned/dev/stable = compiled or fetched.

## F1 — The measured case: "I had to compile MeshCom by hand"

Evidence, e293 field-test harvest (0.12.9), `runlogs/auto-install-11c3b495.log`: `[blocked] meshcom:
… src/meshcom-qemu-raspi is at 3c30c07e4, the pin is c2335d12f — the artifact's run scripts must come
from the pinned checkout. — install from source with: lhpc install meshcom --source pinned --yes`;
then `build-meshcom-qemu.log` (the compile).

- **Which refusal:** NOT the index pin check (all 6 covered components of the live index equal the
  dev pins today) but the `clone_required` stale-checkout refusal, `service_binary_ops.py:286`: the
  run scripts' checkout sat at 3c30c07e4 from the box's previous binary install
  (`install-meshcom.log`: `meshcom-qemu@3c30c07e4`); 0.12.9 pins c2335d12f.
- **Why it fired here and not on the CLI:** a top-level `binary_install` moves a clean stale clone
  to its pin first (`_stale_clean_clones`, `service_binary_ops.py:196` `if not locked:`; 3aed16fe,
  2026-09-27). The auto-install run calls `binary_install(..., locked=True)`
  (`service_auto_install.py:1265`), so the move is skipped and the refusal fires.
- **What the user saw:** the row replaces the refusal's own way out (`lhpc update meshcom-qemu
  --source pinned --yes`, a checkout move) by a full compile (`service_auto_install.py:1275`).
- **Second nag class (same family):** an older controller after a bot release → `BinaryPinMismatch`
  (`binary_install.py:179-204`) → "self-update, source, or `--accept-pin-mismatch`"; MeshCom refuses
  the override outright (`service_binary_ops.py:521-528`), so offline only a compile is left.
- **Tests pinning today's behaviour:** `test_binary_install.py::test_a_clean_clone_at_an_older_pin_is_moved_to_the_pin`
  (CLI path only), `::test_a_dirty_clone_at_an_older_pin_is_still_refused_not_moved`; the override
  family, 63 tests in five files (Numbers).

## F2 — Binary without nagging

**Compatible** (defined): an index entry is compatible with this controller when, for every covered
component, (1) its commit equals the manifest pin (`check_pins`, unchanged, exact), and (2) its
recorded build-input digest equals `sha256(build_inputs_text(comp))` computed by THIS controller
(the byte-exact sidecar `is_built` already compares, `service_lifecycle_ops.py:5148-5170`).

1. **His case (packet C1, small):** inside an auto-install run the stale clean `clone_required`
   checkout is moved through `update(cid, source="pinned", exact_pin=True,
   auto_install_ctx=<the run's context>)` — the composed-op path that already validates the held
   locks (`_auto_install_ctx_error`). Dirty, foreign or unproven trees keep the refusal (that is
   "absolutely necessary"). A blocked binary row names the refusal's own `next_commands[0]`, never a
   compile it did not ask for. Red-before: an auto-install run over a stale clean clone reads
   `blocked` today; after, `success` and the clone at the pin.
2. **Newest compatible artifact (C2 + B1):** the publisher keeps a bounded history per stack (newest
   first, 20 entries; the assets are already retained: 112 on the `binaries` release). The box takes
   the FIRST compatible entry. None compatible → ONE typed refusal, `data.binary_refusal =
   "none_compatible"`, details naming the newest published entry's commits vs the pins, next
   commands `lhpc self-update --apply` then `lhpc install <stack> --source pinned --yes`.
3. **Information line, not a question:** a chosen entry older than the newest published one adds
   "a newer binary is published for a newer LHPC (lhpc self-update --apply)" to plan and result.
4. **`--accept-pin-mismatch` leaves (recommend REMOVE, not hide):** with history + exact match the
   flag has no case left, and a hidden flag keeps 93 references in 13 Python files alive. Removed: the CLI
   flag (`main.py:584, 857`), the consent token (`binary_install.py:206-217`), the web checkbox and
   job token (`app.py:1527-1713`, `service_lifecycle_ops.py:3824`), the auto-install override hint
   (`service_auto_install.py:1278-1280`), the receipt writer. KEPT: the receipt reader of
   `override` (boxes that accepted once still read their receipt; status shows it as lagging, as
   today, `service_binary_channel.py:157-183`). Tests: the 63 override tests are deleted except the
   receipt-reader cases, which stay as "an old receipt with an override reads lagging".
5. **Why a new index file, not a field in `index.json`:** older controllers read `stacks[<id>]`
   only. Once the web client leaves the meshtastic artifact (F3) its sidecar changes, so a new-shape
   artifact in `index.json` would install on an older controller and then read *behind* forever.
   New controllers read `index-v3.json` (schema 3: `stacks: {<id>: [entry, …]}`, each entry the
   schema-2 fields + `inputs: {<component>: <sha256>}`); `[stack.binary] index_url` names it. The
   builder publishes into the index file the `lhpc_ref` manifest names, so bot patches cut from an
   old `main` keep feeding `index.json` until this lands on `main`. No knob, no registry.
6. **0.3.12 guarantee, how the gate still proves it:** nothing is installed whose commits differ from
   the pins — stronger than today (no override). The inputs digest adds the web/CLI/asset inputs to
   the same pre-download gate; the launch gate (`is_built` byte-compare) is unchanged behind it.

Tests (`install/`, typed fields): red-before for 1 (row status + clone commit); new invariant +
negatives for 2 (newest entry lags, an older matches → the older installed, its sha256 in the
receipt; none matches → `none_compatible`, nothing downloaded; pin-equal entry with other inputs
skipped). Golden (`tests/golden`): none touched — `test_golden_start.py` uses meshtastic only as a
process holder; no golden case drives a binary install.

## F3 — The Meshtastic web client as its own component

**Manifest:** component `meshtastic-web` in the meshtastic stack, modelled on graywolf (a fetched
package; precedent, no new mechanism): build step = today's `meshtastic-web-assets.sh` with
destination `{runtime}/build/tools/meshtastic-web` (OUTSIDE the meshtasticd publish root
`build/tools/meshtasticd`, `manifest.example.toml:1095`, which a binary install replaces);
version-bearing marker `.lhpc-built-<version>`; `build_root` for clean; provenance file
`web.provenance` (version, sha256, selector, origin) as the script writes today. The `web_root`
FileParam default (`manifest.example.toml:1423`) and the base comment (`bases/meshtasticd.yaml:228`)
move to the new path. The meshtasticd component loses the web step (`:1183`) and its
`meshtastic-web` build input (`:1210`); the CLI and crypto inputs stay.

| selector | what the box installs | trust anchor | lab proof |
|---|---|---|---|
| `binary` (default, known-working) | the release `build.tar` of the manifest pin, from upstream | manifest sha256 (as today) | release-verify lane |
| `stable` | the newest upstream release, as republished by lhpc-binaries | index-v3 sha256 | the entry's smoke |
| `dev` | the named upstream commit lhpc-binaries built on request | index-v3 sha256 | the entry's smoke |

- `stable`/`dev` come from index-v3 (stack key `meshtastic-web`) because upstream publishes no
  digest and the guard needs a lab proof. `pinned`/compiled lanes: refused "no compiled lane".
- **The lab check (fail-closed):** the web builder's mandatory smoke starts meshtasticd from the
  index-v3 meshtastic entry of the `lhpc_ref` pin with the testlab SimRadio config
  (`testlab/lhpc_testlab/data/meshtasticd-sim.yaml` at `lhpc_ref`), `RootPath` = the staged client,
  and requires (a) `GET /` serves an index whose JS carries the entry's version or commit, (b)
  `GET /api/v1/fromradio` answers 200. The entry records `components = {meshtastic-web: <web
  commit>, meshtastic: <meshtasticd commit>}`. The box takes a `stable`/`dev` entry only when
  `meshtastic` equals its installed meshtasticd commit (receipt or ownership record); otherwise a
  typed refusal naming `--source binary`.
- **dev on request:** `build-binary` dispatch, `stack = meshtastic-web`, `web_ref = <commit>`
  (upstream's `pnpm install --frozen-lockfile && pnpm run build && pnpm run package`; Node on the
  runner, never on a box).
- **Known-working moves by the bot after the lanes:** the bot moves the manifest web pin (version +
  sha256, `bot/manifest.py:208-289`) when the release lane passes; a web move no longer republishes
  meshtastic (`_MARKER_RECORDED` loses `extra.meshtastic-web`, `bot/cli.py:818`); `autofreeze` maps
  `meshtastic-web` to the component. On a new upstream release and on every meshtasticd pin move
  the bot dispatches the web builder for the stable ref, so `stable` stays lab-proven.
- **Status:** `lhpc status --versions` shows version + selector from `web.provenance` (`stable`/
  `dev` read `differs`): no claimed client that is not served (0.3.12 (a)); the lane proves (c).
- **A pin move** reads the component *not built* (version-bearing marker): the next build fetches the
  new pin, also over a `stable`/`dev` choice (graywolf's rule; stated in the docs).
- **Migration:** an upgraded box reads `meshtastic-web` *not built* once → one 2 MB fetch
  (`lhpc build meshtastic-web`, or the stack's Install tab). The meshtastic sidecar loses one line,
  so an installed artifact reads *behind* once: binary boxes reinstall it (~53 MB, the index-v3
  entry published before the release), source boxes rebuild (PlatformIO incremental; measured in the
  live row). The old `build/tools/meshtasticd/web` goes with the next artifact; on a source box it is
  left (2 MB, named in the CHANGELOG).

## F4 — Graywolf vocabulary

`--source binary` = the manifest's pinned `.deb` (today's build step; the default). `--source
stable` = today's `--upstream` (latest release, verified against its `checksums.txt`). `--upstream`
stays accepted as an alias of `--source stable` (help hidden), so no script breaks. `pinned`/`dev`:
typed refusal "graywolf has no compiled lane" — NOT decided, carried as a ToDo. Unchanged: the fetch
script, the checksum verification, `release_repo`, the marker, the upstream check, auto-install.

## F5 — Packets, order, proofs, live row, docs

| # | repo | packet | files (≤90 KB each) | proof |
|---|---|---|---|---|
| C1 | C | auto-install moves a stale clean clone; the row names the refusal's own command | service_binary_ops.py, service_auto_install.py, tests/install | CI `test` |
| B1 | B | schema-3 index, history (20), `inputs`, publish into the index the `lhpc_ref` names | lib_index.py, publish.sh, build.sh, tests | B `tests.yml` + one dispatch |
| C2 | C | index-v3 reader, newest compatible, `none_compatible`, override removed | binary_install.py, service_binary_ops.py, CLI, web, manifest index_url, tests, docs | CI + testlab `release_verify` dispatch |
| B2 | B | `meshtastic-web` builder + lab smoke | builders/meshtastic-web.sh, smoke.sh, STACK_PATHS | B dispatch on 6eefbaf8 |
| R1 | R | index named by the manifest; web move no republish; stable dispatch | bot/cli.py, manifest.py, autofreeze.py, tests | R CI |
| C3 | C | `meshtastic-web` component + selectors + migration | manifest, script, service (fetched path), status, tests, docs | CI + `release_verify` |
| C4 | C | graywolf vocabulary | CLI, service_maintenance.py, web label, tests, docs | CI |

Order: C1 (independent; also fit for a 0.12.x patch) → B1 → C2 → B2 → R1 → C3; C4 any time.

**Live row (Pi 5, granted slot):** meshtastic running; `lhpc update meshtastic-web --source` stable,
dev, binary; after each the index JS carries the expected version, `/api/v1/fromradio` 200, the
meshtasticd PID and receipt sha256 unchanged; auto-install over a meshcom-qemu clone at an older pin → `[ok]`.

**Docs (one place per fact):** provenance.md "Selections" (fetched packages: binary/stable/dev) and
"The binary channel" (newest compatible, no override); cli.md synopses; stacks/meshtastic.md (web
client); stacks/graywolf.md; maintenance.md "Two pins are not commits"; operations.md (link only).
CHANGELOG (Unreleased): C1 "Auto-install moves MeshCom's run-script checkout to its pin itself
instead of asking for a source build." C2 "The binary channel installs the newest binary published
for this LHPC; `--accept-pin-mismatch` is gone." C3 "The Meshtastic web client is its own component:
Known working, Latest stable or Development, switched in seconds without touching meshtasticd."
C4 "Graywolf: `--source stable` installs the latest upstream release (`--upstream` still works)."

## F6 — Simplicity, risks, numbers

**Guardrails:** no registry; the web component is the graywolf pattern; one new format (index
schema 3: history + isolation of older controllers, F2.5); one new builder (decision 2); selection
is a plain function `binary_install.select_compatible`, the service family does not grow.
**Risks:** (1) one reinstall/rebuild per meshtastic box at the release (measured in the live row);
(2) SimRadio meshtasticd on the B runner is unproven — if it fails, the smoke moves to a testlab
dispatch writing the same proof (decided at B2); (3) index-v3 needs an entry for every pin before C2
reaches `main` (release ordering, checked by R1); (4) override receipts stay readable (F2.4).

## Numbers, measured

| figure | command | output |
|---|---|---|
| base | `git rev-parse HEAD` | 254bbae3a6d24d829932f1dcb1a66e7c6b56dc6d |
| live index vs dev pins | scratch `pins.py` over `index.json` + `load_manifest` | 6/6 components MATCH |
| the e293 refusal | `grep blocked runlogs/auto-install-11c3b495.log` | "src/meshcom-qemu-raspi is at 3c30c07e4, the pin is c2335d12f" |
| locked call, row rewrite | `grep -n "locked=True\|install from source with" service_auto_install.py` | `:1265`, `:1275` |
| override refs | `grep -rn[l] 'accept-pin-mismatch\|accept_pin_mismatch\|pin_mismatch\|consent' lhpc --include=*.py \| wc -l` | 93 lines, 13 files |
| override tests | `grep -c 'def test'` over the five files | 32+16+6+6+3 = 63 |
| retained assets | `gh release view binaries --json assets -q '.assets\|length'` | 112 |
| web pin, publish root | `grep -n` manifest | `:1183`, `:1210`, `:1423`, `:1095` |
