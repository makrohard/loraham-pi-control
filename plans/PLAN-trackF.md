# Plan v3.8 — Track F: install channels without nagging, the Meshtastic web client as its own component

Base `origin/dev` 816a2842 (post-C1; line numbers read on 254bbae3); analysis MT-WEB-CLIENT (61e18c6d). Repos:
loraham-pi-control (C), lhpc-binaries (B), lhpc-release-bot (R). binary = prebuilt known-working; rest compiled/fetched.

| change | where |
|---|---|
| v1–v3.2: gate 1 rounds 1–3 and the maintainer's D1–D3 (frozen copies PLAN-trackF-v1 … v3.2) | — |
| W2a F1: every shipped binary stack covered by index-v3 is a BLOCKING precondition before C2 reaches `main` | F2.4, F5 |
| W2a F2: history keeps the newest entry per distinct composition, never a count | F2.2 |
| W2a F3: the web smoke is a real node-info exchange in a browser, with a broken-client control | F3 |
| W2a F4: the tested meshtasticd commit is persisted; a daemon change re-checks the pairing | F3 |
| W2a F5: RootPath migration specified (measured) and both upgrade paths proven live | F3, F5 |
| C1 split off and LANDED on dev 816a2842 (gate 1 GREEN, Pi 5 row passed); v3.4: second-read wording; v3.5: base and migration wording; v3.6 (W3): the web smoke's "served + API reachable" fallback removed; v3.7 (5b r1): B1 live proof = bootstrap ref K + the blocking C2 release_verify; v3.8: index.json byte-unchanged kept | F2.1, F2.4, F3, F5 |

## F1 — The measured case: "I had to compile MeshCom by hand" (fixed by C1, on dev)

e293 (0.12.9), `auto-install-11c3b495.log`: `[blocked] meshcom: … src/meshcom-qemu-raspi is at 3c30c07e4,
the pin is c2335d12f … — install from source with: …`, then the compile. Not the index pin check but
the `clone_required` stale-checkout refusal, reached because the auto-install run calls `binary_install(
…, locked=True)` and so skips the CLI's pre-lock move; the row hid the refusal's own way out. Reproduced
on the Pi 5 (0.12.9); C1 (F2.1) passes the same row. Second nag class, for C2: an older controller after a bot
release → `BinaryPinMismatch` (`binary_install.py:179-204`); MeshCom refuses the override outright.

## F2 — Binary without nagging

**Compatible:** an entry whose commits equal the manifest pins for every covered component (`check_pins`,
exact) AND whose build-input digest equals `sha256(build_inputs_text(comp))` computed here (the
byte-exact sidecar `is_built` compares, `service_lifecycle_ops.py:5148-5170`).

1. **C1 (on dev 816a2842, ships with 0.12.10):** in an auto-install run (`locked`) a clean, proven run-script
   checkout at another commit is re-adopted at the pin (`adopt_source`, `force=True`); dirty/unproven
   refused; CLI path unchanged; the blocked row names the refusal's command. gate1-CODE-trackF-c1-v2
   (8c7b1112, GREEN round 4); Pi 5: 0.12.9 `[blocked]` → C1 `[ok]`, checkout at c2335d12f.
2. **Newest compatible (C2 + B1):** the publisher keeps, per stack, the newest entry of every DISTINCT
   composition (components map + inputs digests); a rebuild of a composition replaces its older entry, so
   the history grows by one entry per release composition and is never cut by a count (assets are
   retained: 112 on the `binaries` release). Newest first; the box takes the FIRST compatible entry. None → ONE typed refusal, `data.binary_refusal = "none_compatible"`, details: the
   newest entry's commits vs the pins; next: `lhpc self-update --apply`, then `--source pinned`.
3. **Information line, no question:** an entry older than the newest adds "a newer binary is
   published for a newer LHPC (lhpc self-update --apply)". Override flag: D1; index format: D2.
4. **Before C2 reaches `main` (blocking):** index-v3 holds, for EVERY shipped binary stack (daemon,
   meshtastic, meshcom), an entry compatible with the release sha. Enforced where `main` already
   gates: `release_verify=true` is dispatched on the C2 candidate sha and must be green BEFORE the ff
   to `main`; it installs each binary stack through `select_compatible` (`none_compatible` = red). R1's
   bot check stays as ongoing enforcement.
5. **0.3.12 guarantee:** nothing is installed whose commits differ from the pins (D1 removes the
   only exception); the inputs digest adds web/CLI/asset inputs to the pre-download gate; the launch
   gate (`is_built`) is unchanged behind it.

Tests (`install/`): red-before for 1; new invariant + negatives for 2 (older entry matches → installed,
sha256 in receipt; none → `none_compatible`, no download; other inputs → skipped); B1: an old
composition stays selectable after 25 newer publications of other compositions; a rebuild of the same
composition replaces it (one entry). Golden: none
touched (`test_golden_start.py` uses meshtastic only as a process holder).

## F3 — The Meshtastic web client as its own component

**Manifest:** component `meshtastic-web` in the meshtastic stack on the graywolf pattern (a fetched
package): build step = today's `meshtastic-web-assets.sh` into `{runtime}/build/tools/meshtastic-web`
(OUTSIDE the meshtasticd publish root `build/tools/meshtasticd`, `manifest.example.toml:1095`, which a
binary install replaces); static marker `.lhpc-built` (D3); `build_root`;
`web.provenance` (version, sha256, selector, origin). `web_root` default (`:1423`) and
`bases/meshtasticd.yaml:228` move to the new path; meshtasticd loses the web step (`:1183`) and its
`meshtastic-web` input (`:1210`); the CLI and crypto inputs stay.

| selector | what the box installs | trust anchor | lab proof |
|---|---|---|---|
| `binary` (default, known-working) | the manifest pin's release `build.tar`, from upstream | manifest sha256 (today) | release-verify lane |
| `stable` | the newest upstream release, republished by lhpc-binaries | index-v3 sha256 | the entry's smoke |
| `dev` | the latest lab-built snapshot of upstream `main` HEAD (may trail HEAD; refresh below) | index-v3 sha256 | the entry's smoke |

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
  meshtastic entry of the `lhpc_ref` pin with the testlab SimRadio config at `lhpc_ref` (owner long name
  `LHPC-SMOKE`), `RootPath` = the staged client; headless Chromium opens the client, connects over its
  HTTP transport to that daemon and must render the node's long name `LHPC-SMOKE` (a real want-config /
  node-info exchange) within 120 s. Control (B2's tests): the same client with its main bundle truncated
  fails the smoke. Both are publication requirements, with no weaker fallback: an exchange that cannot run
  reliably on the B runner moves to a testlab dispatch writing the same proof (F6 risk 2), never to a lesser check.
- **Admission and pairing:** the entry records `components = {meshtastic-web: <web commit>,
  meshtastic: <meshtasticd commit>}`; the box takes it only when `meshtastic` equals its installed
  meshtasticd commit (receipt or ownership record), else a typed refusal naming `--source binary`.
  `web.provenance` persists that tested meshtasticd commit. A plain status function compares it with
  the installed meshtasticd; on a daemon change (binary reinstall, source update) status and doctor
  read "web client unproven for this meshtasticd"; the update that moves meshtasticd re-resolves the web
  entry for the KEPT selector against the new daemon (`binary`: the manifest pin, lane-proven; `stable`/
  `dev`: the newest entry naming the new daemon; none → the installed client stays, named as unproven,
  with `--source binary` as the way), in that same run. Tests: a web-pin-only move (a `binary` client reads *behind*, a
  `stable` one untouched) apart from a daemon replacement (`stable` reads unproven, the update re-pairs
  and keeps `stable`).
- **Known-working moves by the bot after the lanes:** the bot moves the web pin (version + sha256,
  `bot/manifest.py:208-289`) when the release lane passes; a web move no longer republishes
  meshtastic (`_MARKER_RECORDED` loses `extra.meshtastic-web`, `bot/cli.py:818`); `autofreeze` maps
  `meshtastic-web` to the component; on a new upstream release and on a meshtasticd pin move the bot
  dispatches the builder for the release tag, so `stable` stays lab-proven.
- **Status:** version + selector from `web.provenance` (`stable`/`dev` read `differs`): no claimed
  client that is not served (0.3.12 (a)); the lane proves (c). Pin move: D3 (a `binary` client reads *behind*).
- **Migration (box state measured on the Pi 5, 0.12.9; the migration is proved in C3):** saved settings do not carry `web_root`
  (`config/stacks/meshtastic@868.toml` has no such key); `RootPath` is rendered into
  `config/files/meshtasticd.yaml` from the default at every start, so the new default reaches every box at
  its next start. A saved `web_root` equal to the OLD managed path (`{runtime}/build/tools/meshtasticd/web`,
  literal or rendered) is rewritten to the new default by the config migration (one rule; any other
  explicit value is the operator's and kept). `meshtastic-web` reads *not built* once → one 2 MB fetch; the
  meshtastic sidecar loses a line → binary boxes reinstall once (~53 MB), source boxes rebuild (time to
  be measured); the old `build/tools/meshtasticd/web` (2 MB) stays on source boxes (CHANGELOG).
  Live: both upgrades (F5); the "PID + receipt unchanged" baseline is taken AFTER the one-time migration.

## F4 — Graywolf vocabulary

`--source binary` = the pinned `.deb` (today's build step; the default); `--source stable` = today's
`--upstream` (latest release, verified against its `checksums.txt`); `--upstream` stays as a hidden
alias. `pinned`/`dev`: typed refusal "graywolf has no compiled lane" — NOT decided, a ToDo. Rest unchanged.

## Decisions (the maintainer, 2026-10-15, DECISIONS-channels-and-web-client)

- **D1 — REMOVE `--accept-pin-mismatch`:** the flag, the consent token (`binary_install.py:206-217`),
  the web checkbox/job token (`app.py:1527-1713`, `service_lifecycle_ops.py:3824`), the auto-install
  hint (`service_auto_install.py:1278-1280`); the receipt reader stays (`service_binary_channel.py:157-183`).
- **D2 — YES, a second index file** `index-v3.json` (history + inputs), named by `[stack.binary]
  index_url`; the builder publishes into the file the `lhpc_ref` manifest names.
- **D3 — NO reset:** an operator's `stable`/`dev` web choice survives a pin move; the move changes
  only the known-working entry. How: the marker is the static `.lhpc-built` (not version-bearing);
  the choice is the selector recorded in `web.provenance`, and a bare `lhpc update meshtastic-web`
  (or rebuild) keeps it — the rule a source checkout already follows ("a bare `update` keeps the
  installed channel", provenance.md:23). A pin move makes only a `binary` client read *behind*
  (version ≠ pin, a plain status function) until `lhpc update meshtastic-web` fetches the new pin.
  Red-before-style tests: a `stable` client after a pin move stays `stable` and *built*; a `binary`
  client reads *behind*, then the update serves the new pin.

## F5 — Packets, strict order, proofs, docs

Strict sequence, each from the previous landing. Shared: C1/C2 `service_binary_ops.py`,
`service_auto_install.py`, `tests/install/`; C2/C3 manifest, provenance.md, cli.md; C2/C4 CLI, cli.md;
B1/B2 `publish.sh`, `lib_index.py`. Order: C1 (also fit for 0.12.10) → B1 → C2 → B2 → R1 → C3 → C4.

| # | repo | packet | lane proof | live proof |
|---|---|---|---|---|
| C1 | C | stale clean clone adopted under the held locks; the row names the refusal's command | CI `test` | Pi 5: auto-install over a meshcom-qemu clone reset to an older pin → `[ok]`, clone at pin |
| B1 | B | schema-3 index, newest entry per composition, `inputs`, publish into the index the `lhpc_ref` names | B `tests.yml` | bootstrap: a controller topic K that names index-v3.json (cut only after the 0.12.10 pin bump lands), the three dispatches run with K's ref; C2's release_verify then blocks on the real entries; `index.json` byte-unchanged |
| C2 | C | index-v3 reader, newest compatible, `none_compatible`, D1 | CI + `release_verify` (every binary stack via `select_compatible`, blocking) | Pi 5: `install daemon --source binary` from v3 picks the matching entry |
| B2 | B | `meshtastic-web` builder (HEAD) + lab smoke | B `tests.yml` | dispatch → entry with both commits, smoke passed |
| R1 | R | index named by the manifest; web move no republish; stable + weekly dev dispatch | R CI | `release.yml` mode `dry-run` on the R1 ref → plan names index-v3, no meshtastic rebuild for a web move |
| C3 | C | `meshtastic-web` component + selectors + pairing + migration | CI + `release_verify` | Pi 5: upgrade a 0.12.x binary install AND a source install → the new component served; then (baseline after the migration) stable, dev, binary: node name rendered, meshtasticd PID + receipt unchanged |
| C4 | C | graywolf vocabulary | CI | Pi 5: `update graywolf --source stable` = `--upstream` plan; `--source pinned` typed refusal |

**Docs:** provenance.md "Selections" + "The binary channel"; cli.md; stacks/meshtastic.md;
stacks/graywolf.md; maintenance.md "Two pins are not commits"; operations.md (link). CHANGELOG (Unreleased): C2 "The binary
channel installs the newest binary published for this LHPC." (+ D1's line) C3 "The Meshtastic web
client is its own component: Known working, Latest stable or Development, switched in seconds without
touching meshtasticd." C4 "Graywolf: `--source stable` installs the latest upstream release."

## F6 — Simplicity, risks, numbers

**Guardrails:** no registry; graywolf pattern; C1 reuses an existing call; one new format (D2), one
new builder (decision 2); selection = plain function `binary_install.select_compatible`.
**Risks:** (1) one reinstall/rebuild per meshtastic box at the release (cost to be measured in the
C3 live row); (2) SimRadio meshtasticd on the B runner is unproven — if it fails, the smoke moves to a
testlab dispatch writing the same proof (decided at B2); (3) a source-install upgrade row on the Pi 5
needs a meshtasticd build (time to be measured).

## Numbers, measured

| figure | command | output |
|---|---|---|
| base | `git rev-parse HEAD` | 254bbae3a6d24d829932f1dcb1a66e7c6b56dc6d |
| live index vs dev pins | scratch `pins.py` over `index.json` + `load_manifest` | 6/6 components MATCH |
| the e293 refusal | `grep blocked runlogs/auto-install-11c3b495.log` | "…is at 3c30c07e4, the pin is c2335d12f" |
| primitive, call sites | `grep -n` adopt_source( / locked=True / "install from source with"; `kind == "dir" and force` / verify_identity( / blocks_update in install.py | `:321`; `:1265`, `:1275`; `:447`, `:458`, `:476` |
| override refs; tests | `grep -rn[l] 'accept-pin-mismatch\|accept_pin_mismatch\|pin_mismatch\|consent' lhpc --include=*.py \| wc -l`; `grep -c 'def test'` over the five files | 93 lines, 13 files; 32+16+6+6+3 = 63 |
| retained assets; web pin, root | `gh release view binaries --json assets -q '.assets\|length'`; `grep -n` manifest | 112; `:1183`, `:1210`, `:1423`, `:1095` |
| saved `web_root` on a box | Pi 5 0.12.9: `grep -rn "web_root\|RootPath" config/stacks/ config/files/` | only `config/files/meshtasticd.yaml:228` (rendered); none in `config/stacks/` |
