# Plan v3.1 — Track F: install channels without nagging, the Meshtastic web client as its own component

Base `origin/dev` 254bbae3; analysis MT-WEB-CLIENT (61e18c6d). Repos: loraham-pi-control (C),
lhpc-binaries (B), lhpc-release-bot (R). binary = prebuilt known-working, no questions; rest compiled/fetched.

| v1 → v2 (gate 1 round 1, five findings) | where |
|---|---|
| 1 C1 narrowed to the adoption primitive the binary install already runs under the held locks; excerpt included | F2.1 |
| 2 `dev` = the newest built commit = upstream HEAD at build time; arbitrary named refs dropped | F3 |
| 3 packets strictly sequenced, shared files named; a live proof per packet | F5 |
| 4 nothing future is called measured ("to be measured") | F3, F6 |
| 5 the three decisions neutral, alternatives and trade-offs, recommendation on its own line | D1–D3 |
| v2 → v3 (round 2): `dev` named honestly = the latest lab-built HEAD snapshot; refresh cadence stated (weekly bot build + on request; v3.1: a cadence, not a bound) | F3 |

## F1 — The measured case: "I had to compile MeshCom by hand"

Evidence, e293 field-test harvest (0.12.9), `runlogs/auto-install-11c3b495.log`: `[blocked] meshcom:
… src/meshcom-qemu-raspi is at 3c30c07e4, the pin is c2335d12f — the artifact's run scripts must come
from the pinned checkout. — install from source with: lhpc install meshcom --source pinned --yes`;
then `build-meshcom-qemu.log` (the compile).

- **Which refusal:** NOT the index pin check (all 6 covered components of the live index equal the
  dev pins today) but the `clone_required` stale-checkout refusal, `service_binary_ops.py:283-291`:
  the run scripts' checkout sat at 3c30c07e4 from the previous binary install
  (`install-meshcom.log`: `meshcom-qemu@3c30c07e4`); 0.12.9 pins c2335d12f.
- **Why here and not on the CLI:** a top-level `binary_install` moves a clean stale clone first
  (`_stale_clean_clones` + `update()`, `service_binary_ops.py:196` `if not locked:`); the auto-install
  run calls `binary_install(..., locked=True)` (`service_auto_install.py:1265`), so it is skipped.
- **What the user saw:** the row replaced the refusal's own way out (`lhpc update meshcom-qemu
  --source pinned --yes`, a checkout move) by a full compile (`service_auto_install.py:1275`).
- **Second nag class:** older controller after a bot release → `BinaryPinMismatch` (`binary_install.py
  :179-204`); MeshCom refuses the override (`service_binary_ops.py:521-528`) → only a compile left.
- **Tests today:** `test_binary_install.py::test_a_clean_clone_at_an_older_pin_is_moved_to_the_pin`,
  `::test_a_dirty_clone_at_an_older_pin_is_still_refused_not_moved` (CLI path); override family 63.

## F2 — Binary without nagging

**Compatible:** an entry whose commits equal the manifest pins for every covered component (`check_pins`,
exact) AND whose build-input digest equals `sha256(build_inputs_text(comp))` computed here (the
byte-exact sidecar `is_built` compares, `service_lifecycle_ops.py:5148-5170`).

1. **C1, his case — narrowed.** The stale-pin refusal at `service_binary_ops.py:283-291` becomes the
   adoption the same loop already performs for a MISSING clone under the held locks (`:321`), with
   `force=True` because the directory exists:
   ```python
   act = inst.adopt_source(comp, source="pinned", locked=_guard_held, force=True,
                           pinned_expected=(comp.source.pin_commit, "manifest pin (the binary artifact's pin)"))
   ```
   `force=True` on an existing directory takes `install.py:447` (`if kind == "dir" and force:`):
   capture the leaf, `verify_identity` (`:458`, unproven → failed, nothing moved), `dirty_report`
   (`:476`, local changes → failed, nothing moved), then the journalled staged activation every
   source update uses. `update()` is NOT called: no known-working clearing, no build follow-up, no
   dependency scope. The CLI path is unchanged (its pre-lock move leaves the loop at the pin). A failed
   adoption returns the loop's adoption refusal (`:325-331`); the auto-install row then names the refusal's own
   `next_commands[0]` instead of `install --source pinned` (`service_auto_install.py:1275`).
   Red-before: auto-install (`locked=True`) over a clean clone at an older pin reads `blocked` today;
   after: `success`, ownership record at the pin. Dirty/unproven stay refused (+ both under `locked`).
2. **Newest compatible (C2 + B1):** the publisher keeps a bounded history per stack (newest first, 20
   entries; assets are already retained: 112 on the `binaries` release). The box takes the FIRST
   compatible entry. None → ONE typed refusal, `data.binary_refusal = "none_compatible"`, details: the
   newest entry's commits vs the pins; next: `lhpc self-update --apply`, then `--source pinned`.
3. **Information line, no question:** an entry older than the newest adds "a newer binary is
   published for a newer LHPC (lhpc self-update --apply)". Override flag: D1; index format: D2.
5. **0.3.12 guarantee:** nothing is installed whose commits differ from the pins (D1-a removes the
   only exception); the inputs digest adds web/CLI/asset inputs to the pre-download gate; the launch
   gate (`is_built`) is unchanged behind it.

Tests (`install/`): red-before for 1; new invariant + negatives for 2 (older entry matches → installed,
sha256 in receipt; none → `none_compatible`, no download; other inputs → skipped). Golden: none
touched (`test_golden_start.py` uses meshtastic only as a process holder).

## F3 — The Meshtastic web client as its own component

**Manifest:** component `meshtastic-web` in the meshtastic stack on the graywolf pattern (a fetched
package): build step = today's `meshtastic-web-assets.sh` into `{runtime}/build/tools/meshtastic-web`
(OUTSIDE the meshtasticd publish root `build/tools/meshtasticd`, `manifest.example.toml:1095`, which a
binary install replaces); version-bearing marker `.lhpc-built-<version>`; `build_root`;
`web.provenance` (version, sha256, selector, origin). `web_root` default (`:1423`) and
`bases/meshtasticd.yaml:228` move to the new path; meshtasticd loses the web step (`:1183`) and its
`meshtastic-web` input (`:1210`); the CLI and crypto inputs stay.

| selector | what the box installs | trust anchor | lab proof |
|---|---|---|---|
| `binary` (default, known-working) | the manifest pin's release `build.tar`, from upstream | manifest sha256 (today) | release-verify lane |
| `stable` | the newest upstream release, republished by lhpc-binaries | index-v3 sha256 | the entry's smoke |
| `dev` | the latest lab-built snapshot of upstream `main` HEAD (may trail HEAD, bounded below) | index-v3 sha256 | the entry's smoke |

- **dev = the latest lab-built HEAD snapshot.** The B2 builder takes NO ref: `git ls-remote
  https://github.com/meshtastic/web refs/heads/main` at run time, builds that commit (`pnpm install
  --frozen-lockfile && pnpm run build && pnpm run package`; Node on the runner, never a box). The box
  installs the newest `dev` entry and never triggers a build; plan and status show its commit and
  build date. Refresh: the weekly bot run (R1) builds when HEAD moved past the newest entry — normally
  within one weekly cycle plus build time; a failed build or smoke keeps the previous proven snapshot
  until a later success. On request: `build-binary`, `stack = meshtastic-web`. No arbitrary-ref lane.
- `stable`/`dev` come from index-v3 (key `meshtastic-web`): upstream has no digest, the guard needs a
  lab proof. `pinned` (compiled): typed refusal "no compiled lane".
- **Lab check (fail-closed), the web entry's mandatory smoke:** meshtasticd from the index-v3
  meshtastic entry of the `lhpc_ref` pin, SimRadio config `testlab/lhpc_testlab/data/
  meshtasticd-sim.yaml` at `lhpc_ref`, `RootPath` = the staged client; requires (a) `GET /` serves an
  index whose JS carries the entry's version or commit, (b) `GET /api/v1/fromradio` answers 200. The
  entry records `components = {meshtastic-web: <web commit>, meshtastic: <meshtasticd commit>}`; the
  box takes it only when `meshtastic` equals its installed meshtasticd commit, else a typed refusal
  naming `--source binary`.
- **Known-working moves by the bot after the lanes:** the bot moves the web pin (version + sha256,
  `bot/manifest.py:208-289`) when the release lane passes; a web move no longer republishes
  meshtastic (`_MARKER_RECORDED` loses `extra.meshtastic-web`, `bot/cli.py:818`); `autofreeze` maps
  `meshtastic-web` to the component; on a new upstream release and on a meshtasticd pin move the bot
  dispatches the builder for the release tag, so `stable` stays lab-proven.
- **Status:** version + selector from `web.provenance` (`stable`/`dev` read `differs`): no claimed
  client that is not served (0.3.12 (a)); the lane proves (c). Pin move: D3.
- **Migration:** `meshtastic-web` reads *not built* once → one 2 MB fetch; the meshtastic sidecar
  loses a line → binary boxes reinstall once (~53 MB), source boxes rebuild (time to be measured in
  the C3 live row); the old `build/tools/meshtasticd/web` (2 MB) stays on source boxes (CHANGELOG).

## F4 — Graywolf vocabulary

`--source binary` = the pinned `.deb` (today's build step; the default); `--source stable` = today's
`--upstream` (latest release, verified against its `checksums.txt`); `--upstream` stays as a hidden
alias. `pinned`/`dev`: typed refusal "graywolf has no compiled lane" — NOT decided, a ToDo. Rest unchanged.

## Decisions for the maintainer (neutral; recommendation on its own line)

- **D1 — `--accept-pin-mismatch`.** (a) Remove: flag, consent token (`binary_install.py:206-217`),
  web checkbox/job token (`app.py:1527-1713`, `service_lifecycle_ops.py:3824`), the auto-install hint
  (`service_auto_install.py:1278-1280`); keep the receipt reader (`service_binary_channel.py:157-183`).
  Less code (93 lines, 13 files; 63 tests shrink to the reader cases); no expert escape if no entry
  fits. (b) Hide (help suppressed): keeps the escape and all that code and tests. (c) Keep visible.
  Recommendation: (a).
- **D2 — index format.** (a) A second file `index-v3.json` (history + inputs) named by
  `[stack.binary] index_url`; the builder publishes into the file the `lhpc_ref` manifest names:
  older controllers never see a new-shape artifact; B and R read two files. (b) Fields added to
  `index.json`: one file, but older controllers read `stacks[<id>]` and would install the new-shape
  meshtastic artifact, then read *behind* until self-update. Recommendation: (a).
- **D3 — a web pin move over a `stable`/`dev` choice.** (a) Reset to the new pin at the next build
  (version-bearing marker, graywolf's rule): known-working stays the default, the choice is lost.
  (b) Keep the choice: a per-box saved selector the build honours — a new persisted setting.
  Recommendation: (a).

## F5 — Packets, strict order, proofs, docs

Strict sequence, each from the previous landing. Shared: C1/C2 `service_binary_ops.py`,
`service_auto_install.py`, `tests/install/`; C2/C3 manifest, provenance.md, cli.md; C2/C4 CLI, cli.md;
B1/B2 `publish.sh`, `lib_index.py`. Order: C1 (also fit for 0.12.10) → B1 → C2 → B2 → R1 → C3 → C4.

| # | repo | packet | lane proof | live proof |
|---|---|---|---|---|
| C1 | C | stale clean clone adopted under the held locks; the row names the refusal's command | CI `test` | Pi 5: auto-install over a meshcom-qemu clone reset to an older pin → `[ok]`, clone at pin |
| B1 | B | schema-3 index, history (20), `inputs`, publish into the index the `lhpc_ref` names | B `tests.yml` | dispatch daemon for the C2 ref → `index-v3.json` read back; `index.json` byte-unchanged |
| C2 | C | index-v3 reader, newest compatible, `none_compatible`, D1 | CI + `release_verify` | Pi 5: `install daemon --source binary` from v3 picks the matching entry |
| B2 | B | `meshtastic-web` builder (HEAD) + lab smoke | B `tests.yml` | dispatch → entry with both commits, smoke passed |
| R1 | R | index named by the manifest; web move no republish; stable + weekly dev dispatch | R CI | `release.yml` mode `dry-run` on the R1 ref → plan names index-v3, no meshtastic rebuild for a web move |
| C3 | C | `meshtastic-web` component + selectors + migration | CI + `release_verify` | Pi 5: stable, dev, binary; index JS version, `/api/v1/fromradio` 200, meshtasticd PID + receipt unchanged |
| C4 | C | graywolf vocabulary | CI | Pi 5: `update graywolf --source stable` = `--upstream` plan; `--source pinned` typed refusal |

**Docs:** provenance.md "Selections" + "The binary channel"; cli.md; stacks/meshtastic.md;
stacks/graywolf.md; maintenance.md "Two pins are not commits"; operations.md (link). CHANGELOG (Unreleased): C1 "Auto-install moves
MeshCom's run-script checkout to its pin itself instead of asking for a source build." C2 "The binary
channel installs the newest binary published for this LHPC." (+ D1's line) C3 "The Meshtastic web
client is its own component: Known working, Latest stable or Development, switched in seconds without
touching meshtasticd." C4 "Graywolf: `--source stable` installs the latest upstream release."

## F6 — Simplicity, risks, numbers

**Guardrails:** no registry; graywolf pattern; C1 reuses an existing call; one new format (D2), one
new builder (decision 2); selection = plain function `binary_install.select_compatible`.
**Risks:** (1) one reinstall/rebuild per meshtastic box at the release (cost to be measured in the
C3 live row); (2) SimRadio meshtasticd on the B runner is unproven — if it fails, the smoke moves to a
testlab dispatch writing the same proof (decided at B2); (3) index-v3 needs an entry for every pin
before C2 reaches `main` (release ordering, checked by R1).

## Numbers, measured

| figure | command | output |
|---|---|---|
| base | `git rev-parse HEAD` | 254bbae3a6d24d829932f1dcb1a66e7c6b56dc6d |
| live index vs dev pins | scratch `pins.py` over `index.json` + `load_manifest` | 6/6 components MATCH |
| the e293 refusal | `grep blocked runlogs/auto-install-11c3b495.log` | "…is at 3c30c07e4, the pin is c2335d12f" |
| primitive, call sites | `grep -n` adopt_source( / locked=True / "install from source with"; `kind == "dir" and force` / verify_identity( / blocks_update in install.py | `:321`; `:1265`, `:1275`; `:447`, `:458`, `:476` |
| override refs; tests | `grep -rn[l] 'accept-pin-mismatch\|accept_pin_mismatch\|pin_mismatch\|consent' lhpc --include=*.py \| wc -l`; `grep -c 'def test'` over the five files | 93 lines, 13 files; 32+16+6+6+3 = 63 |
| retained assets; web pin, root | `gh release view binaries --json assets -q '.assets\|length'`; `grep -n` manifest | 112; `:1183`, `:1210`, `:1423`, `:1095` |
