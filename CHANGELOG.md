# Changelog

## 0.4.5

- The access point is documented where it is configured. The path to remote access set up neither
  the allowed source range nor the certificate address for it, so a box followed the controller's
  own instruction to open `https://10.42.0.1:8443` and answered `403`. Boxes without an AP —
  Desktop and hand-built — are no longer told to configure one.
- Written down because they cost time: `apply` never re-issues the server certificate, so a SAN
  added without `tls-renew` is saved and not served; `cert revoke` refuses without
  `--confirm-label`.
- openhop_core is built pristine — the noise-floor patch is upstream as PR #133, and until it
  merges the noise floor is not reported. An already-patched checkout now reads `dirty` and
  refuses the overwrite; `docs/stacks/meshcore.md` carries the one-time migration.
- A refused source update says to preserve or reconcile the modifications first, and makes
  discarding them an explicit choice rather than the implied one.

## 0.4.4

- openhop-core: 8a3921da1 -> c95a68445 (v1.0.10-413-gc95a684), used by meshcore-node
- openhop-repeater: c02b3cb73 -> 9e375da77 (1.1.4-7-g9e375da), used by openhop-repeater-src

## 0.4.3

- LoRaHAM_Voice: c0b22ddca -> 8e1af01bf (8e1af01), used by loraham-voice, loraham-voice-cli

  The Voice repository rewrote its history and the pinned commit stopped being reachable from its
  `main`, so `pin-validation` failed and the release-verification lane could not clone the source
  at all. The new pin carries the GPLv3 relicence of the Voice sources, so this is a real content
  change and not only a reachability fix. No binary is affected: Voice is covered by no published
  artifact and builds from source on the box.

- A third-party outage no longer decides a release. The headless Pyodide gate fetched `micropip`
  from a CDN while it ran, because the npm pyodide package ships no wheels — one such fetch failed
  and reddened a release whose diff touched no file under `demo/`. Those two wheels are now
  committed under `demo/vendor/` and served through `packageCacheDir`, which Pyodide checks before
  the network. That removes the fetch that failed and nothing more: the gate still resolves the
  lhpc wheel's own dependencies from PyPI, which is recorded in the backlog with the measurement
  that proves it.

- Acquisition steps across CI, testlab and Pages retry three times. Only acquisition: package
  installs, `git fetch` of the pinned remotes, the browser download. `pip-audit` is deliberately
  excluded, because it exits non-zero on a real vulnerability and a retry cannot tell that from a
  network fault; so are the test commands themselves, because a gate that fails once and passes
  twice is telling you something. A test enforces that rule by reading which program each retry
  invokes.

- The Pages deployment job is restricted to `main`. It had no branch guard, so the manual dispatch
  used to verify a change on its own branch would have published that branch to GitHub Pages.

- Documentation that told the reader to do something the project no longer does: the test suite is
  run with `python -m pytest` (CI switched when coverage moved to the checkout), the documented
  local gate no longer names a parallel plugin that is not a dependency, a maintainer patch lands
  on `dev` rather than branching from `main`, and the deploy-script tests no longer claim to need
  no network while running a real `pip install`.

## 0.4.2

- LoRaHAM_Daemon: 82c82c3f1 -> ff3c26a42 (v0.9.0), used by loraham-daemon, loraham-chat

  The daemon repository merged its upstream and published v0.9.0. The merge resolved two
  restructure conflicts and changed no file, so the pinned tree is byte-identical to the one
  0.4.0's test matrix measured; the artifact is republished regardless, because provenance names
  a commit and that commit has to be the one the manifest pins.

- The daemon `pin_tag` names a tag that exists. It read `v112-7-g82c82c3`, but `v112` is not in
  that commit's ancestry: the daemon's history rewrite re-ided 64 commits and orphaned every
  release tag it had, so `git describe` — the convention every other pin follows — yielded
  `110-137-g82c82c3` instead. Nothing enforced this, because CI validates `pin_commit` and never
  the tag, so a provenance label had been naming an unreachable object since the pin moved. The
  daemon repository now carries a reachable `v0.9.0`, which is what both blocks and both stack
  pages record.

## 0.4.1

- MeshCom-Firmware: 674413ce3 -> 6edc74997 (v4.35t), used by meshcom-firmware

## 0.4.0

- The release test matrix ran on the reference box: nine of the twelve rows, the cross-cutting
  checks, a full purge-and-`auto-install` pass (9/9 stacks, 0 blocked, 0 failed), a boot restore
  (3 restored, 0 failed) and the host tests. Every source component ends at its manifest pin and
  every binary stack at `built_from == pin`, with no OOM anywhere in the run. Measured numbers and
  the rows that remain owed are in [live-test.md](docs/live-test.md).

## 0.3.16

- LoRaHAM_Daemon: dbd2998b7 -> 82c82c3f1 (v112-7-g82c82c3), used by loraham-daemon, loraham-chat
- LoRaHAM_Voice: 143b83f25 -> c0b22ddca (c0b22dd), used by loraham-voice, loraham-voice-cli
- openhop-repeater: 4705c99c3 -> c02b3cb73 (1.1.4-5-gc02b3cb), used by openhop-repeater-src

  The Voice move carries no code: 143b83f2 and c0b22dd have the identical tree 780298165b6c.
  It is recorded because Voice is tip-tracked, so the release bot would otherwise read it as a
  real move and spend a version number and an image build on nothing.

- Chat builds from `clients/chat/lorachat_ncurses_113.c`. Chat is an artifact source, so it follows
  the daemon repository's default branch rather than its manifest pin, and that repository moved its
  client programs into `clients/`. The compile was still naming the old root path.

- Adopting a local source no longer fails when git repacks the checkout while it is being
  copied. `copytree` lists a directory then reads it, and git packing loose objects prunes
  their fan-out directories in between, so an entry vanishes mid-copy — the same repository
  in a different physical representation. The `.git` copy is now retried once, and only when
  every collected failure is a missing path; a mixed failure, or a vanishing working-tree
  file, still fails the adoption as before.

## 0.3.15

- loraham-kiss-tnc: 3c4461e4f -> e7646c12f (v0.5.1-2-ge7646c1), used by loraham-kiss-tnc, loraham-kiss-serial
- meshcom-loraham-bridge: f0189206a -> 35a9348a0 (35a9348), used by meshcom-bridge

## 0.3.14

- openhop-repeater: 47e49e64a -> 4705c99c3 (1.1.4-3-g4705c99), used by openhop-repeater-src

## 0.3.13

- A build whose every command succeeded but whose completion marker could not be written no longer looks like that component's own build failure. It used to return the last SUCCESSFUL step's log path, and the release lane decides attribution from exactly that identity — so a full disk during Reticulum's build produced evidence against five upstream pins that had built perfectly. Such a result now carries no step identity at all; the log stays named in the failure text, where diagnostics belong.
- The artifact-portability guard runs in CI instead of skipping there. It compares what this controller adds to a published artifact against the publish roots RELEASED controllers declare, read from their tags — and the CI checkout had no tags, so a required job was proving nothing. CI fetches them now, the guard fails rather than skips when they are missing, and it records which tags it compared.
- The daemon and Chat pins move to `dbd2998` (`v112-6-gdbd2998`). Upstream rewrote its history to remove three attribution trailers, which gave 64 commits new ids and left the old pin off `main`'s ancestry — `pin-validation` catches exactly that. Five test-only commits landed ahead of the rewrite (a strict-build flag fix, three test-hygiene fixes, and six daemon-spawning tests that now skip where there is no `/dev/spidev0.*` instead of dying), so the daemon's own behaviour is unchanged; the binary is republished from the new commit regardless, because provenance names a commit that has to exist.
- The attribution rule moves to `lhpc.core.build_regression`, with `tools/build_regression.py` as its command-line form. The binary builder has to answer the same question about the same `lhpc build` output and cannot import the test lab; two copies of this rule would drift, and the direction they drift in is freezing an upstream pin over a broken package index.

## 0.3.12

- The Meshtastic web-client and CLI pins are recorded (`build_inputs`) in a file beside the built artifact. Moving one used to change nothing an installed box could see: the firmware checkout stayed put, so the completion marker stayed valid, the stack still read *built*, and it kept serving the old client and running the old CLI while the manifest claimed the new ones. Beside the artifact and not inside the marker, because an artifact has to be installable by controllers older than itself: the marker's content is compared byte for byte by every controller that ever shipped, and a file outside the publish roots a released manifest declares is refused outright.
- `lhpc status` no longer calls such an artifact current. Binary freshness compares the artifact's own marker as well as its component commits, still without touching the network.
- A binary-installed stack that reads *not built* is now pointed at `lhpc install <stack> --source binary --yes`. The console offered `lhpc build`, which the binary channel refuses — a dead end.
- The manifest refuses to load unless a recorded input is exactly what the build step consumes, so the two copies of a version cannot drift apart. Each entry names the step that consumes it and the argv token it fills (`command = "pip"`, `token = "meshtastic=={value}"`), and that step must carry the rendered token exactly once: a recorded `2.7.11` is not satisfied by a step installing `2.7.110`, nor by another command that carries `meshtastic==2.7.11` verbatim.
- The release-verification lane proves the installed web client is the pinned one, and that the artifact records the manifest's build inputs — a moved pin whose binary was not republished now stops the release.
- That lane names the cases it must report, so a renamed or dropped case fails it instead of passing a count. Each interactive component must now draw something only a working one draws, and a missing Sideband GUI capability fails rather than printing a note.
- A release-lane failure at a stack's own build, start or readiness carries a `STACK-REGRESSION stack=… phase=…` line in its JUnit failure text, so an automated release can freeze exactly the stack that regressed. A build is attributed only where LHPC typed a component's build as failed AT A STEP THE RECIPE DECLARES ITS OWN — a manifest build step now says so with `attributable = true`, which only a compile, a patch or a check over what earlier steps already fetched may carry. A fetching step never is: pip failing on an unreachable package index is typed exactly like a broken recipe, and freezing four MeshCore pins over it is not recoverable in a week. An install never is either, because a clone that could not resolve a host and an upstream that dropped the pinned commit read the same. Everything else — a stop, the lab's fake daemon, a display-dependent predicate, an artifact that was not republished, the identity check over all stacks — stays deliberately unattributed and reports as an ordinary failure.
- The release lane runs with `-x` and stops at its first failure: its cases chain over one radio pair, so a run that continues either buries a genuine freeze under an unmarked prerequisite failure or hands an innocent stack its own marker. A failing case still releases the stacks it started, and a stop that could not stop something is now a visible teardown error rather than a swallowed one — a run whose lab is broken attributes nothing. A case whose prerequisite was never up still says so instead of failing at its own attributed assertion.
- Both Voice variants START in the lane: the GTK app with the display the lane owns, the terminal variant with that display taken down — the box LHPC actually offers it on. Neither is accepted on build evidence any more.
- The MeshCore repeater refusal now names a route the CLI can take. It said *"set repeater_name in the same save"*, but `lhpc config` saves one parameter per call and rejects two with *"too many arguments"* — only the console can save both at once. From a shell the order is `repeater_name` first, then `mode`, and the message says so.
- **Upgrading:** Meshtastic reads *Build required* once after this release, because no existing artifact records these values yet. On the binary channel that is a reinstall of a republished artifact; from source it is a rebuild, largely incremental. The recorded values live in a file beside the completion marker rather than inside it, so a republished artifact still reads *built* to a controller that predates them — without that, neither publishing the artifact first nor releasing the controller first was safe.

## 0.3.11

- CI lints the test lab. `CONTRIBUTING.md` has always named `ruff check lhpc testlab` as the gate, but the workflow ran `ruff check lhpc` only, so findings in `testlab/` could sit on `main` unnoticed — five did, until 0.3.10 fixed them. The workflow and `maintenance.md` now match what contributors are told to run.

## 0.3.10

- Voice is an ordinary pinned source. Its `artifact` flag meant an ordinary pinned install or update took the branch tip and skipped the identity check, so the manifest pin was decorative. It now installs and verifies the pinned commit like every other source.
- Internal: five lint findings in the test lab's release lane are fixed (regex flag aliases spelled out, one import block sorted). No behaviour change; `ruff check testlab` is green again.

## 0.3.9

- **Files you add to a managed source checkout now survive an update.** A stack's own logs and generated settings, or a file you put there yourself, are carried into the new source instead of blocking the update; the old checkout is discarded only once each of them is proven to be there, and a path the new upstream version also ships is a refusal naming the file rather than a merge. LHPC's own regenerable output (`build/`, `.pio/`, `.venv/`, `.work/`, `.run/`, `__pycache__/`, `node_modules/` and a declared built binary) is the exception: it neither blocks an update nor survives one. Editing, deleting or staging an upstream-tracked file still makes the checkout dirty and blocks the update — to run a modified stack, fork it and point the component's remote and pin at your fork. Uninstall and clean are unchanged and keep their existing dirty-tree protection; unlike source updates, they do not carry additions forward.

## 0.3.8

- The release-verification lane no longer reports a pass it did not earn. A stack counts as running only when LHPC's own status says `running` for it, a failed stop is a failure rather than housekeeping, an interactive component must draw what it is supposed to draw and must exit cleanly rather than be killed, and the optional components a stack start deliberately leaves alone (Sideband, LXMD, the MeshCore Web UI) are started by name. Both Voice variants are proved, because they share one checkout and it proves neither.
- Its identity check fails closed: a mandatory component that is not installed is a failure, not a printed note, and a binary stack is compared against every component its artifact covers. Artifact integrity is checked before the stacks start, because an emulated node writes to its own flash as soon as it boots.
- `release-verify` runs on `main` pushes and explicit dispatch only. It used to run on every push, including `dev`, which spent half an hour proving a commit nobody was about to release.
- Documentation: how `main` advances by both release paths; that a binary row's build is refused rather than skipped; that a `dev` checkout reads `match` when the branch tip IS the pin; and the four different questions provenance answers, of which file integrity is the one that is time-sensitive.

## 0.3.7

- openhop-core: 8cdb04e73 -> 8a3921da1 (v1.0.10-410-g8a3921d), used by meshcore-node
- openhop-repeater: efc5616ec -> 47e49e64a (1.1.4-1-g47e49e6), used by openhop-repeater-src

## 0.3.6

- A default install lands on the composition this release proved: without `--source`, install, update and auto-install take the published binary where there is one, else `pinned` (was `dev`). The image builder runs that same bare auto-install, so a fresh image carries the release's pins instead of the branch tips of the day. `dev` and `stable` stay available as explicit choices.
- New test-lab lane `release` (CI job `release-verify`): every stack an automated pin release may move is installed on its default channel, built, started and verified by its own state, then re-proved to BE the candidate manifest's commits. Interactive components run on a real terminal; GUI ones where LHPC's own predicate says they can. The daemon and RadioLib are the lab's fixtures and are never proved there — they need the radio.
- Release policy: a patch release (pins or a fix) branches from `main` and has two producers, the maintainer and the [release bot](https://github.com/makrohard/lhpc-release-bot); a minor release comes from `dev` with the full box matrix. An image follows every release. A patch returns to `dev` as a fast-forward or a pull request, never a rewrite.
- The lab lanes upload their own build, start and state logs as run artifacts, so a red lane says which component failed.

## 0.3.5

- Tests fail only for LHPC behaviour now. The JavaScript source-arithmetic and hand-built-DOM harnesses are replaced by real headless Chromium, the deployment tests run the shipped scripts, the testlab coverage matrix gives way to enumerating the app's own routes, and the remaining source scans are replaced by the behavioural seams they stood in for.
- The suite is organised by the behaviour it protects (`core`, `stacks`, `web`, `cli`, `install`, `host`, `repo`), the test lab has three named lanes (`unit`, `acceptance`, `browser`), and the suite runs from any working directory, on a machine with no browser, with nothing skipped.
- No product change: the only non-test edit in this release removes a test-only hook from `system.js` that the deleted Node harness needed.

## 0.3.4

- LHPC's own tests for the MeshCore host application ran in no gate at all; they now run in CI against the manifest-pinned openHop core. No external project's own test suite runs in LHPC CI.
- `--source stable` resolves through one rule instead of two: the newest version-shaped tag, else the default-branch HEAD. The local and the remote (auto-install) paths used different regexes and different fallbacks, so the same selector could install different commits of one component.

## 0.3.3

- Documentation: one canonical owner per subject, short context and a link everywhere else; `live-test.md` keeps the newest live run only (git history holds the rest). No behaviour change.
- The release rule now says which releases run the full [test matrix](docs/test-matrix.md): a minor release (`0.X.0`) does, a patch release runs the live checks its own change calls for.

## 0.3.2

- Internal: core service coupling reduced — logic moved into plain core modules (`restart_required`, `power`, `jobs`, `resources`, `gps`, `procident`); no behaviour change. Public surface, CLI and routes identical; live re-proof of every moved flow in `docs/live-test.md`.
- CI measures and publishes branch coverage (summary and `coverage.xml` per Python version); no threshold.

## 0.3.1

- Meshtastic serves the newest web client: LHPC pins the meshtastic/web release itself (v2.7.2, sha256-verified on every install) instead of the firmware's `bin/web.version`, which had stayed at 2.6.7 across the 2.7.x/2.8.0 firmware lines. The binary artifact ships the client, so a republish follows the pin.
- Branch model: `main` is the latest release, `dev` is where changes land; CI and testlab run on `dev` too. `CONTRIBUTING.md` says what should be green.

## 0.3.0

Breaking pre-1.0 cleanup; a fresh image or a clean final-0.2.10 install is the supported path. Pins
unchanged since 0.2.10. Release test: `docs/live-test.md` (from-zero install, all stacks, boot restore
on a Zero 2 W).

- **iGate removed;** Graywolf is the APRS station (RF↔APRS-IS through the KISS TNC, with a web UI).
- **Source `strategy` and the `link`/`linked` states removed;** every managed source is a clone under
  the runtime root, and a symlink is never a managed source. Stale read tolerances and the old MeshCore
  identity rescue are gone with them.
- **Uninstall keeps operator data** (`config/`, `backups/`, `profiles/`, the stacks' app data under
  `state/`); `--purge` removes everything. A checkout without an ownership record is refused, never
  adopted silently.
- **Binary channel:** updates go through the index; a binary auto-install row that cannot start is
  reported blocked, never successful.
- **Console truth:** a saved proxy Disable stays visible until Apply removes the listener; a
  gate-deferred Webserver Apply is announced on every page until the firewall is applied; the firewall
  scripts exist from bootstrap on and every apply sequence ends with `lhpc webserver apply`.
- **Manifest:** shell-era build/run/test strings removed; shell shorthand refused. `bootstrap-deps.sh`
  never invokes sudo. Stored passwords are masked on the stack page with a Show button.
- **Docs:** consolidated to one home per fact, READMEs rewritten and ground-truthed in both languages,
  the release procedure in `docs/test-matrix.md`, dead code and history wording removed throughout.

## 0.2.10

- Reticulum 1.5.2; MeshCom firmware at the `dev` tip 674413c (QEMU overlay rebased, GPS UART drain bounded under QEMU); Meshtastic follows the stable tag v2.7.26 (binary republished).
- Known-working works on headless boxes: an optional GUI sidecar never adopted on a Lite install no longer blocks the composition.
- The release test matrix (`docs/test-matrix.md`) is the leading pre-release live test.

## 0.2.9

- Start means start: a web Start or Restart runs the saved configuration; Settings is the only place configuration changes; only a resource conflict or a dependent to stop gets a confirmation.
- Detached web Start/Restart as a tracked job with the task banner; identity refusals redirect to the Settings row.
- The stack page's Password section shows the stored password with a copy button; MeshCom's HMAC password included.
- The managed Meshtastic CLI is listed on the Dashboard as an on-demand component.
- Faster pages: every piece of evidence is read once per request.

## 0.2.8

- MeshCore repeater: the meshcore stack (*MeshCore (OpenHop)*) gains a `mode` setting — `chat`, `chat+repeater`, `repeater` — hosting the upstream openHop repeater beside the companion node.
- One proxied web page per component; proxy deny lists tolerate spelling variants; denied paths answer 404.
- The MeshCore build consumes the pinned repeater checkout: after the update run `lhpc install meshcore` once.

## 0.2.7

- AP fallback: the 10-minute retry of the preferred Wi-Fi no longer takes the AP down while a client is connected to it (`iw` station table; a missing `iw` defers the automatic retry, the console's Retry still works), and a disarmed `lhpc-ap` profile is re-armed by the network watchdog. `iw` joins the default bootstrap
- A webserver Apply that the firewall gate refused is remembered and completes automatically once the firewall is verified — no second click after the sudo step. The Firewall panel shows it while it is owed, the dashboard "(pending Apply)" badge links to the Webserver panel. Only the policy that was deferred is activated; a later webserver or proxy edit needs its own Apply

## 0.2.6

- Coherent identities: an optional global base operator callsign that licensed stacks inherit while their local callsign is empty; Meshtastic and MeshCore node identities never inherit; a start without a resolvable identity is refused before anything changes.
- A global callsign carrying an SSID is not inheritable; per-stack SSIDs stay local.

## 0.2.5

- **Stacks WebGUIs:** one Webserver subpanel applies a common proxy policy (access, scheme, auth, CIDRs) to every eligible stack web UI at once — ports stay per-stack (existing kept, missing get the normal suggested default), all-or-nothing validation, one atomic save, one nginx apply; the console's own settings live under "LHPC WebGUI"
- auto-install no longer blocks a stack over its gui_optional GUI component's missing toolkit (v0.2.4 Lite image build failure)

## 0.2.4

- **Voice on headless/Lite boxes:** the same source built with `-DNO_GTK` as `loraham-voice-cli`, a pure ncurses TUI with zero graphical linkage — `lhpc stack start voice` prints the exact terminal command; codec2/ALSA moved into the standard bootstrap, GTK stays behind `--with-gui`
- The GTK app is `gui_optional`: absent toolkit/display drops it from build/start/auto-install and status instead of failing the stack; on a desktop it runs exactly as before and the terminal variant is not offered
- The terminal variant is a guarded fallback: direct start/restart refused (its config — incl. the callsign — belongs to the GTK component), exclusive audio enforced, offered only where the GUI cannot run; plan/preview and no-op results tell the same truth
- Interactive components run their pre-start steps, so the printed command actually works (live-found ENOENT)
- `bootstrap-deps.sh --dry-run` no longer rejects its own `libasound2-dev` (ALSA is not an audio server; PulseAudio stays denied)

## 0.2.3

- `lhpc meshtastic <args>`: a guarded passthrough to the managed Meshtastic CLI against the local node.
- MeshCore runs on openHop Core (a reviewable patch on the LoRaHAM daemon, never a fork) with a browser GUI through the LHPC TLS/PKI proxy — replacing the retired fork and its Tk Node Manager; the one Companion slot is shared safely between the CLI and the WebUI.
- New hardware profile `uputronics-x` (crossed modules); MeshCore `txmaxpower` ceiling 20 dBm, default `txpower` 14.

## 0.2.2

- **MeshCore identity is LHPC-owned:** the node's private key lives in `config/secrets/meshcore_identity.key` (0600) and is adopted, never re-minted on config regeneration; the generated `meshcore-pi.toml` that carried it is 0600
- **MeshCore position follows the box:** the global GPS source (`use_gps`, default on) feeds the node continuously through a `meshcore-gps` bridge instead of freezing at start; `fixed` still writes static coordinates
- meshcore-pi repinned `640978e`: the companion port no longer drops an idle client every ~90 s, current v1 routing/path encoding, a malformed packet or hostile trace can no longer take the node down; daemon defaults corrected to `POWER=14`/`PREAMBLE=16`
- All external software repinned to current upstream (graywolf 0.14.13, meshcore-cli v1.6.3, RadioLib 7.7.1-57, meshtastic v2.7.26-32, Reticulum 1.5.1, Sideband 2.1.0, meshcom-qemu 54c3ec3, MeshCom firmware v4.35p.08.29) — several upstream tags/history had moved and broke a fresh build
- An absent optional component no longer fails the whole stack, and `lhpc build` on an uninstalled component says so instead of failing with rc 127
- Validated on hardware (identity across restart, live GPS, stable Companion, a real advert on 868); peer-to-peer RF is covered by tests against the current wire format only, not field-validated

## 0.2.1

- **"Back to AP mode" no longer refused:** re-activating the box's own shared AP needs the NetworkManager `wifi.share.open`/`.protected` polkit actions the network rule omitted, so it (and a failed join's AP fallback) failed with "Not authorized to share connections via wifi" — stranding the box when the AP was its only way home. The rule now grants them and the auth preflight checks them; re-run the copybox or `bootstrap-deps.sh` on existing boxes

## 0.2.0

- **Interactive in-browser demo** (GitHub Pages): the real console compiled to WebAssembly with Pyodide, driven against a pure in-browser simulation backend — browse the dashboard/Apps and install → build → start → stop any stack with one-stack-per-band handoff and a live radio panel, no Pi, no server, no sign-in. Badge in the README; see `demo/`
- **Codespaces test lab** (`lhpc-testlab`): the real console + CLI + real stack processes (kiss, graywolf, meshcore, meshcom, …) against deterministic fake hardware/OS backends, one click in a GitHub Codespace — fault scenarios, RX/TX injection, simulated reboot, and a coverage-matrix gate. Ships nothing in the lhpc wheel or the Pi image. Badge in the README; see `docs/testlab.md`
- Generic extension point behind both: `ControllerService` honors `$LHPC_SYSTEM_PROVIDER` (`module:factory`) to supply an alternate System/manifest/spawn for out-of-tree simulation harnesses; unset (production, always) it is byte-identical to before
- `{multiarch}` token in manifest `check_file` paths (libslirp) — resolves to the aarch64 literal on the Pi (unchanged), truthful on x86

## 0.1.17

- New **Network** panel (AP-managed boxes only): join an existing Wi-Fi from the console; the box's own AP stays the automatic fallback, and a **preferred** network is re-joined whenever it reappears. Console follows onto the joined network (cert + nginx allowlist stay the gate); expired-CRL self-heal; second polkit rule via bootstrap (opt-out `--no-network-controls`)
- Power buttons now show on a correctly authorized box: visibility asks logind directly (per-action, cached)
- The Reboot confirm page warns that the AP vanishes for a minute or two mid-reboot

## 0.1.16

- Stopping a stack now also tears down the dependency stacks it alone was using (stop graywolf → kiss stops → daemon released); a dependency another running stack still needs stays up
- The Start-confirm page also shows the dependency stacks the start pulls up (kiss under graywolf) — fully editable like the target's own: per-start overrides reach the dependency's launch, and Save persists into the dependency's own config
- Audit hardening: stopping a not-running stack never tears down its dependencies; the stop plan discloses the collateral; an override for an already-running dependency warns instead of vanishing; partial saves report exactly what persisted; graywolf's upstream update preserves an operator stop mid-fetch and refuses admission contention cleanly
- The dashboard's system card gains **Reboot / Shut down** buttons (confirm page, graceful via logind): authorized by a polkit rule that bootstrap-deps installs (opt-out `--no-power-controls`) or the dependency panel's copybox adds on existing boxes; buttons stay hidden until then, and a pending power action blocks new builds/updates until it fires

## 0.1.15

- graywolf's 433 TX default is now **433.775** (single-channel, same as stock ESP32 trackers — they never listened on the old 433.900 split, live-found); the RX/TX split stays available by config

## 0.1.14

- Certificate fetch helpers (the `scp` copyboxes) render in **every serving mode** again — they are operator conveniences addressed at the box's live IP, not secret material, so a plain no-auth box can bootstrap cert auth from them; still offered only for active certificates

## 0.1.13

- graywolf gains an **upstream check**: a network probe of its GitHub releases and a one-click **Update** to the latest — the new `.deb` verified against that release's own `checksums.txt`. The default image/auto-install fetch stays on the reviewed, pinned checksum

## 0.1.12

- **Boot-restore honors an explicit operator stop**: a stack stopped before a reboot stays stopped, even when the stop could not verify the process gone (live-found with Voice restarting on every boot); the next `stack start` makes it restorable again. Scoped precisely to a direct whole-stack stop — internal cascades, band switches and component stops never tombstone
- **Certificates panel** gains fetch helpers under where each is created: paste-ready `scp` commands (Linux PC) addressed at the box's own current IP, plus a plain **Download ca.crt** link for browsers and phones (public certificate, no key). Shown only to a trusted session; offered only for active certificates
- Fetched-package stacks (`graywolf`) show their installed version in the row and offer **Uninstall / Clean all**, plus **Update** — naming the new version — when the manifest pin moves

## 0.1.11

- GPS works out of the box: the global source defaults to `auto`, every stack's `use_gps` defaults to on; a source change is blocked while a GPS consumer runs.
- One radio, one band, enforced across the chain; console start fixes; fetched-binary stacks get Uninstall/Clean; `meshcore-cli` repinned.

## 0.1.10

- New `graywolf` stack replaces `igate`: the same RF↔APRS-IS job through the KISS TNC plus a web UI and a searchable packet log; it follows the global position source.
- LoRaHAM daemon, chat and iGate repinned to `v112-1-g10f4107` (relicensed to plain GPLv3); `loraham-kiss-tnc` v0.5.1 (AX.25 command/response bit fix); `meshcore-cli` and MeshCom firmware repinned.
- Sideband is no longer installed on headless systems (it gates on the `--with-gui` marker).

## 0.1.9

- MeshCom firmware now tracks canonical upstream (icssw-org) at release `v4.35p.08.03` — the external-radio backend merged upstream (PR #1072), retiring the fork pin; QEMU overlay + build surface unchanged

## 0.1.8

- **One global position source** (`lhpc gps`): gpsd local or remote, a receiver read directly, or a fixed position — shared by Meshtastic, MeshCom and Sideband, with a per-stack on/off switch, an exclusive claim on the receiver, and readiness that follows the source rather than the endpoint
- Fixes: an unrelated `socat` is no longer claimed as the KISS serial bridge; `lhpc doctor` reports a gpsd that answers but owns no receiver; the binary-switch tests no longer read the host's process list
- **Time** row in the System panel: local time, UTC, timezone and a sync-state pin — report-only, LHPC never sets or disciplines the clock

## 0.1.7

- Reticulum (RNS) stack: a node that drives the LoRa radio **directly over SPI** — no rnoded, no RNode firmware, no KISS. Owns its band exclusively, shares the SPI bus with the daemon through `spi0.lock`, and refuses to run without a verified radio
- Driver in its own pinned repo ([loraham-rns-interface](https://github.com/makrohard/loraham-rns-interface)): SX127x proven on air on two boards, SX1262 proven on air on 868 (untested on 433); pins/chip/TCXO/PA come from the selected hardware setup, not free-form config
- Restart-safe duty-cycle accounting (reserved before TX, persisted), per-band legal defaults (868: 25 mW/1 %, 433: 10 mW/10 % on a clear 434.500 MHz)
- Generated configs gain a declared file mode, and a secret may be sourced only from `config/secrets.toml` — never from `local.toml`, a default or a band default
- Nested-INI config generation (`ini-update`) with ConfigObj-safe quoting

## 0.1.6

- Binary install channel: prebuilt, smoke-gated artifacts for daemon/meshtastic/meshcom — minutes instead of hours, and the default where published (`--source binary`); pins must match the manifest, switching back to source is non-destructive
- Managed firewall: nftables default-deny you apply with one sudo command, with per-listener choices, an access-point mode (DHCP/DNS on the AP interface) and three honest status dimensions — policy is now settable from the CLI too (`lhpc firewall --mode/--ap/--ssh-ports/--allow-endpoints/--recommended`), so a headless box needs no console
- System monitor
- Boot auto-restore: stacks that were running come back after a reboot, through the normal start path
- Field-validated from zero on a Pi Zero 2W: binary install → mTLS console → stack proxies → own access point with a phone client certificate
- Test hygiene

## 0.1.5
- Hardware setups: `lhpc hardware` selects the radio rig (LoRaHAM / Uputronics dual / Waveshare); daemon v112 multi-hardware, per-band arbitration
- Built-from-source runtime: headless QEMU and server-only meshtasticd compiled from pinned sources into the runtime root
- Headless by default: GUI stacks and their packages are opt-in (`--with-gui`)
- auto-install: per-stack selection, abort and recovery — from-zero proven on Pi Zero 2W and Pi 5
- Self-update hardened: sandboxed CPU-throttled helper unit, nginx-restart escape hatch for bind changes, handles force-pushed upstreams
- bootstrap-deps.sh: dry-run gate, LAN-aware Wi-Fi power-save handling, auto swapfile, persistent journal
- MeshCom HMAC auth + running-task indicators
- Start confirm: per-band stack parameters + callsign enforcement
- Web GUI: dark mode, dependency overview + checks, per-stack daemon params/frequency, unified webserver controls
- Audit + stabilization pass; known-good pins refreshed to the run-proven set (Zero 2W + Pi 5 acceptance runs)

## 0.1.4
- Make web-GUI, meshcom and meshtastic GUI remote exposable With TLS and certificate-auth
- CLI consistency — `lhpc config` (per-stack settings, callsign, daemon params, operator identity), `stack restart`, `webserver proxy`, `cert export`; every next-step hint points at a real command
- per-component update availability indicator
- GUI polishing
- Docs: auto-install flow, expose-with-mTLS + browser client-cert runbook, backup/restore, per-file tables of contents
- Cleanup: slimmed, behaviour-focused test suite; removed dead code (no functional change)

## 0.1.3
- self-hosting
- auto-install
- stack lifecycle
- GUI changes

## 0.1.2

- Full containment: managed clones replace linked dev trees (meshcom/meshcore — in-tree venvs built by `lhpc build`); secret and PTY paths move in-root (`config/secrets/xr_pw`, `state/loraham_kiss`); the local adoption fallback is off by default and must be in-root when set; `strategy="link"` is refused at manifest load.
- Hardening & bugfixes: independent per-band daemons (never launches `--radio both`; safe legacy-both teardown), band-isolated topology-truth conflict gating, SIGTERM-only ownership/PID-safe lifecycle under config-stability locking, and identity-bound post-start runners.
- Daemon & stack parameters: per-stack/per-band daemon radio settings (Save/Apply-live/Reset, browser-only FSK warning) and fully component-scoped run/file config so duplicate parameter names never collide.
- Daemon monitoring: live dashboard plus per-band **View Socket** / **RX·TX** viewers (read-only CONF-socket status, RSSI/CAD/stats).
- GUI structure: per-stack collapsible **Settings** replaces the standalone Config page; reworked header/Apps navigation.
- Self-update: coloured footer version/head freshness, a Self-Update page and Apps entry, and a guarded git fast-forward with durable git-anchored config migration to the new defaults.

## 0.1.1 — hardening

Hardening:

- Descriptor-anchored source transactions, fail-closed session tokens, thin launcher runtime, owned journals; dead-code/docs cleanup; MIT license.

## 0.1.0 — initial version

Terminal CLI and local web console to install, configure and run the LoRaHAM Pi
LoRa stacks (daemon, chat, igate, voice, kiss, meshtastic, meshcom, meshcore).
Adopts and builds each stack's source, starts/stops in dependency order with
per-band radio-conflict gating, writes each app's config, and monitors and
live-tunes the daemon. Bounded read-only status probes; explicit gated mutations;
one-frame TX test on dummy loads. Loopback-only web console (CSRF, CSP).
Validated live on the Raspberry Pi.
