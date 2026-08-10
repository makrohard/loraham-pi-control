# LHPC maintenance

A living checklist for the future maintainer. What CI enforces, what is manual, what recurs, and the
known open work. Tick the per-release / per-pin-bump boxes as you go.

## What CI enforces automatically (every push · py 3.11/3.12/3.13 · GitHub runners)
- `compileall lhpc` + `bash -n install.sh uninstall.sh bootstrap-deps.sh`
- `ruff check lhpc` (the broad FROZEN ruleset) and `ruff check tests --select F,E9`
- `pytest -q` — the **whole** suite, but **no coverage**, no `-m` lane, not under `setsid`
- `bandit -q -r lhpc -lll` (high severity only) and `pip-audit . --strict` (dependency CVEs)
- a separate job: **every pinned source is validated against its live branch**

## What CI does NOT enforce — manual discipline
- [ ] **Coverage.** No `--cov-fail-under` on purpose. If you touch `lhpc/`, run it and check the total
      (~85.4 % branch-inclusive) doesn't drop:
      `pytest -q -p no:cacheprovider --basetemp="$HOME/pt-lhpc" --cov=lhpc --cov-branch; rm -rf -- "$HOME/pt-lhpc"`
      (Consider adding a `--cov-fail-under` step if you want it gated.)
- [ ] **Contract lane** runs inside `pytest -q` but isn't a separate gate. Consider adding
      `pytest -m contract` (~20 s) as a fast pre-flight.
- Everything Pi-specific below only bites you locally, never CI.

## Policy — keep upholding
- **Freeze the config, float the tool.** Dev tools are UNPINNED (`pytest`, `ruff`, `bandit`,
  `pytest-cov`, `zstandard`). Ruff's *rules* are pinned in `[tool.ruff.lint] select`/`ignore`; bandit
  runs `-lll`. When a floated tool complains, fix the code or adjust the config **with a reason** —
  **never re-pin the tool** (that's the trap the old `ruff==` pin was).
- **The contract is the map, the net is the protection.** Read `-m contract` to learn what LHPC
  promises; the full suite protects it. New capability → tag its widest-seam happy + boundary-refusal
  case `@pytest.mark.contract` (and `@pytest.mark.safety("id")` if it guards a safety invariant), keep
  `-m contract` green and < 30 s, and tag only isolation-robust cases.

## Recurring: upstream & pin tracking (the biggest burden)
`lhpc/data/manifest.example.toml` pins ~7 upstreams; each is a stream: `loraham_daemon`, `RadioLib`,
`meshtastic`/meshtasticd, `meshcore`, `loraham-kiss-tnc`, `meshcom-firmware`+`meshcom-qemu`,
`meshcom-loraham-bridge`.
- On an upstream release: bump `pin_commit`/`pin_tag` → rebuild → smoke the stack → refresh the
  known-working profile → run the pinned-source validation before pushing (a pin must be reachable on
  its declared branch or CI reddens).
- Watch for upstream **build-system** breakage, not just releases: **meshtasticd is built from source**
  (OBS binary repo is gone) and **meshcom-qemu builds qemu-system-xtensa from source** — a toolchain
  change upstream can break the recipe silently.

## Recurring: binary channel (`lhpc-binaries` repo) — recompiling a stack
The builder rebuilds *exactly* the manifest pin (it does not track "latest"). To ship a newer
`daemon`/`meshtastic`/`meshcom`:
1. Bump the stack's `pin_commit`/`pin_tag` in `manifest.example.toml` (meshcom: also confirm the QEMU
   overlay patch still applies — `apply-overlay.sh` fails closed if it drifted).
2. Commit + push this repo; note the commit SHA.
3. `lhpc-binaries` → Actions → **build-binary** → `stack`, `lhpc_ref = <that SHA>`, `source_commit` blank,
   `smoke_test=true` (CLI: `gh workflow run build.yml -f stack=… -f lhpc_ref=… -f smoke_test=true`). It
   runtime-tests + smoke-gates, then publishes the content-addressed asset + regenerates `index.json`/`SHA256SUMS`.
4. On the Pi: `lhpc install <stack> --source binary` fetches the new `index.json` (no on-box build;
   `update --source binary` is deliberately conservative and won't cross a pin bump).

Bump the pin **before** publishing — the pins-must-match gate rejects a binary whose `components` ≠ the
manifest pins. meshcom firmware isn't bit-reproducible (new sha each build — expected); keep consumption
(fetch → verify sha → extract → source-fallback) in lockstep with any `lib_index` change; Actions are
SHA-pinned and the container digest-pinned. Builder internals: [lhpc-binaries README](https://github.com/makrohard/lhpc-binaries#updating-a-binary).

## Per-release
- [ ] Version bump (`pyproject.toml` **and** `lhpc/version.py`, now `0.1.10`) + `CHANGELOG.md` + tag
- [ ] Refresh known-working pins to the run-proven set
- [ ] **From-zero acceptance** on fresh hardware — `bootstrap-deps → install.sh → auto-install` on a
      freshly flashed Pi Zero 2W (~4 h) and Pi 5 (~44 min). This is the real net for the install path
      (CI can't run it). Watch the Zero's Wi-Fi (brcmfmac throttles under sustained compile) and the
      `/tmp` tmpfs (ENOSPC — always use an SD-card basetemp).
- [ ] If the deployment layout changed: update the byte-exact unit/nginx renders AND the self-update
      migration path (old boxes carry forward on update).

## Recurring: dependencies & platform
- [ ] `pip-audit` red / new ruff or bandit finding → fix or justify-in-config (don't pin).
- Runtime deps are floors, not pins (`flask<4`, `werkzeug>=3.1`, `waitress<4`, `cryptography>=42`) —
  watch a breaking major (Werkzeug Host parsing, Flask 4).
- Python matrix 3.11–3.13: add 3.14 when it ships, drop 3.11 when no longer targeted.
- OS/kernel drift (Raspberry Pi OS / Trixie): meshtasticd + qemu-from-source are the most fragile to
  toolchain bumps; a kernel change once flipped the `in0_input` voltage-file path.
- **PKI has no auto-renewal** — server/client certs default to 825 days; rotate before expiry on
  long-lived deployments.

## Security posture (don't erode)
- Managed firewall (nftables) fail-closed + receipt trust (owner-identity, cgroup-leaf gating,
  `O_NOFOLLOW` receipt reads) — audit-hardened; don't loosen.
- Exposure stays opt-in (`enable-remote`/`enable-remote-danger`), loopback fail-safe, mTLS.
  `meshtasticd 4403/9443` is the one unconditional `0.0.0.0` exposure with no upstream knob — keep it
  firewall-contained.
- HMAC apply/abort/recover transactional; token never leaks. `bandit -lll` + `pip-audit` are the
  automated floor.

## Docs to keep in sync (some are test-enforced)
`docs/`: architecture, cli (enforced — add a CLI verb → update `cli.md` or `test_cli` reddens),
deployment(+migration), firewall, hardening-0.1, operations, provenance, stacks/, webserver,
wifi-access-point, adding-a-stack (update when the manifest/source model changes). The main `README.md`
is guarded by `test_readme_not_drifted`; `tests/README.md` documents the test tiers and safety areas.

## Known open follow-ups (the actual backlog)
- **Test hermeticity vs a live daemon** — the `RealSystem`-backed binary-switch tests in
  `test_binary_install.py` read the GLOBAL `/tmp/loraconf*.sock`, so with a real daemon running on the
  box they report "component(s) running" and `pytest -m "not slow"` goes red (correct code, non-hermetic
  test). Fix: route the CONF-socket path through a per-test override for those tests. Until then:
  **stop the daemon before a full local run.**
- **Tier 0 CONTRACT-GAPS** (promises whose widest-seam case is missing — the priority list for the next
  test-quality pass):
  1. no real `POST /firewall/configure` route test that observes an applied effect (only GET-redirect
     + settings-render exist; apply/fail-closed is proven only at the ActionResult seam);
  2. boot-restore has NO isolation-safe case — the whole `test_boot_restore.py` is `needs_session` at
     module scope (split out the pure route-toggle tests to give it a sandbox-safe contract case);
  3. no route-level binary-channel SWITCH test (only confirm-page channel selection);
  4. no `/action` POST test for `op=uninstall`/`op=clean` refuse-while-running at the web seam;
  5. no direct `/hardware` setup POST test (only `/hardware/probe`);
  6. `test_get_routes_make_no_network_calls` walks 11 GET paths, not the full ~54-route table;
  7. no single composite "TX opt-in + tests + callsign" gate test (covered by several separate ones).
- **One coverage-hostile flaky test** — `test_stack_params::test_same_process_claim_retries_while_ownership_is_unpublished`
  flakes UNDER the coverage tracer (0.2 s/0.05 s threading windows); green without `--cov`. If a coverage
  run red-flags only it, deselect it from the `--cov` run and verify it separately without instrumentation.
- **Safety invariant IDs** — `docs/hardening-0.1.md` has no enumerated P0.x/P1.x registry; only `P0.5`
  (uninstall) and `P0.6` (GET-no-network) are stated verbatim. TX/firewall/exposure safety cases use
  descriptive slugs (`RF-TX-opt-in` / `firewall-fail-closed` / `exposure-fail-closed`). Consider adding a
  canonical invariant table to hardening-0.1.md so `@pytest.mark.safety` ids map cleanly.

## Local-run gotchas (Pi)
- Always `--basetemp="$HOME/pt-lhpc"` (SD card) + `rm -rf` after — never the `/tmp` tmpfs (fills → ENOSPC).
  Occasionally sweep stray `lpt-*` under `/var/tmp` (list before removing).
- Run under `setsid` or `needs_session` tests silently SKIP (you lose boot-restore/ownership coverage).
- `zstd` must be installed or `requires_zstd` tests skip; don't run as root or `needs_nonroot` tests skip.
- Serialize heavy jobs: 1-min watchdog, no memory cgroup — one full-suite/coverage run at a time
  (full `--cov` ~13 min, fast lane ~8 min on a Pi 5).
- **Stop any real daemon before a full local run** (see hermeticity follow-up above).
