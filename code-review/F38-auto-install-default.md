# F38 — Auto-Install version selector shows "Known working" instead of "latest-dev"

Base: origin/main `e5187f7` (v0.11.10). Read-only analysis; no code changed.
Label mapping (console): `pinned` = "Known working", `dev` = "Development", `stable` = "Latest stable",
`binary` = "Binary (prebuilt)" — `lhpc/adapters/web/app.py:508-509`.

## TL;DR

* **Nothing flipped on the box, and no 0.11.x commit changed the default.** The selector is never stored:
  each row is computed on every page render from `default_channel()`, which has returned
  "binary where published, else `pinned`" since **0.3.6** (`063a475`, 2026-09-09; was `"dev"`). That
  was a deliberate change, guarded by `tests/install/test_binary_channel.py:50`
  (`test_default_channel_is_never_the_branch_tip`).
* **What changed in 0.11.0 is only what the page shows.** Up to v0.10.x the "All" row's version select was
  hard-coded `selected` on `dev` ("Development"), while every stack row underneath already preselected
  and submitted `pinned`/`binary`. Commit **`debca71`** (2026-09-27, in v0.11.0) replaced the false
  "Development" with a "Set all versions…" placeholder. After upgrading, the operator sees the real
  default ("Known working") for the first time, and it looks like a flip.
* The maintainer's stated intent ("always latest-dev") **contradicts the code's documented intent since
  0.3.6**: the `default_channel` docstring, the CLI help, the image builder note and three test files.
  Decide that before applying any fix (§5).

## 1. Where the selector is defined, defaulted, persisted and read

| What | Where |
|---|---|
| Allowed selectors | `lhpc/core/services.py:2785` `SOURCE_CHOICES=("pinned","dev","stable")` with comment "pinned = production-safe default"; `:2789-2790` `BINARY_CHANNEL`, `CHANNEL_CHOICES` |
| Per-stack default | `lhpc/core/service_binary_channel.py:68-78` `default_channel()`: `binary` if `binary_available()`, else `"pinned"` |
| Binary availability (platform only) | `service_binary_channel.py:49-61`: needs `[stack.binary]` (daemon, meshtastic, meshcom: `manifest.example.toml:38,1093,1606`) and `aarch64-trixie` from `/etc/os-release` |
| Allowed options per row | `service_binary_channel.py:63-66` `allowed_channels()` |
| Rows fed to the page | `lhpc/core/service_auto_install.py:845` `"default_channel": self.default_channel(st.id)` |
| Console row preselection | `lhpc/adapters/web/templates/auto_install.html:103-105` (`selected` where `key == r.default_channel`) |
| Console "All" select | `auto_install.html:82-88` (placeholder, "Each stack's default", then the labels); JS `lhpc/adapters/web/static/auto_install.js:155-181` |
| POST parse (fallback when a field is missing) | `app.py:589-603` `_parse_ai_selection` → `row["default_channel"]` |
| Spawn normalisation | `service_auto_install.py:594-600` → `default_channel` |
| CLI `auto-install --source` | `lhpc/adapters/cli/main.py:571-573` default `""` = per stack ("binary where published, else pinned") |
| Driver with no selection/plan | `service_auto_install.py:1017-1027` `source or self.default_channel(...)`; the plan text is at `:951-954` |
| `auto_install()` signature default | `service_auto_install.py:926` `source="pinned"` (the CLI always passes `""`, so it only matters to direct callers) |
| Per-run persistence only | `state/auto-install-plan.json` / `state/auto-install.json` (`lhpc/core/auto_install.py:34-37`, `SELECTORS` `:48`): written per run, never read back to preselect the form (`auto_install.html:174` only displays the last run) |
| What "Known working" resolves to | `lhpc/core/install.py:488-508` `_pinned_expected`: newest operator-confirmed composition in `profiles/known-working/<stack>.json` (`lhpc/core/known_working.py:37,134-144`), else the manifest pin |
| Settings/config | No auto-install source key in `config.py`, `defaults.toml` or `manifest.example.toml`. The only default is the code above. Self-update and boot restore do not touch it (grep shows no writer). |

## 2. Every path that can write or change the effective selector

1. **Operator action**: a row select, the "All" select (`auto_install.js:155-181`) or `--source` (`main.py:571`). These are explicit.
2. **Platform fallback**: `binary_available()` false (not aarch64, `/etc/os-release` unreadable or not trixie,
   `service_binary_channel.py:34-61`) turns daemon/meshtastic/meshcom from `binary` to `pinned`
   **with no notice**. On a Pi this is the only silent per-box change I found. It is not an upgrade path.
3. **No migration or upgrade default** writes a selector, because nothing is persisted (§1).
4. **Bare `update` forces `pinned` on any non-binary stack, including one installed from `dev`**:
   CLI `main.py:1558-1563`, console `app.py:1589-1596` (since **`46d5561`**, v0.11.10, CR8-1, which aligned the
   web with the CLI; before that, web Update used `default_channel` and could switch to binary). Likewise an auto-install row
   for an installed `dev` stack preselects `pinned`. **A `dev` checkout is therefore moved to Known working
   by a default Update or auto-install run.** This is the real "silent switch". It predates 0.11
   (the CLI rule ships with 0.3.6, `063a475`); I did not trace the update planner end to end to confirm the checkout moves without a prompt.
5. Known-working compositions (0.11.x confirm and composition changes, e.g. CHANGELOG 0.11.3 lines 121-123,
   0.11.10 line 15) change **what `pinned` resolves to**, not which selector is chosen.
6. Voice and Chat `artifact` flag removal: **not 0.11.x**. Voice was 0.3.10 and Chat 0.8.1 (CHANGELOG:668, :324).
   Before those releases, "pinned" on Voice/Chat silently built the branch tip. After them, Known working really
   is the pin. That could be why an older box "felt" like dev. Manifest at v0.11.0…v0.11.10: 3 `[stack.binary]`, 0 `artifact =` (checked per tag).

## 3. Commits since v0.11.0 that touch the default (78 commits in v0.11.0..v0.11.10)

* `debca71` (v0.11.0) — UI only: "All" select no longer `selected` on `dev` (old template
  `v0.10.0:auto_install.html:83`; new `auto_install.html:82-88`). CHANGELOG 0.11.0 entry (CHANGELOG.md:191-192).
  Test `tests/web/test_web_auto_install.py:377-382` now asserts `dev` is **never** selected; `:1137-1144`.
* `46d5561` (v0.11.10) — bare web Update → `binary` if on binary else `pinned` (`app.py:1589-1596`).
* `default_channel` body is identical at v0.11.0, v0.11.5, v0.11.9 and v0.11.10 (checked with `git show <tag>:…`). Pickaxe on
  `else "pinned"` finds only `063a475` (0.3.6), with diff `-return ... else "dev"` / `+... else "pinned"` and
  `service_auto_install.py` `.get("version","dev")` → default_channel.

## 4. Stored value vs. effective default; what the operator sees

* **Stored:** nothing to flip. The plan and marker are per-run; `profiles/known-working/*` holds commits, not a selector.
* **Effective:** unchanged in 0.11.x, `binary` or `pinned`.
* **Console ≤0.10.x:** "All" showed **Development**; rows showed Binary/Known working (and submitted those).
  **≥0.11.0:** "All" shows "Set all versions…"; rows show the same Binary/Known working as before.
* **CLI:** `lhpc auto-install` (dry run) prints `source: per stack (binary where published, else pinned)`
  (`service_auto_install.py:953-954`; test `tests/install/test_auto_install.py:96`); `--help` says the same
  (`main.py:572-573`). `lhpc update --help`: "default: pinned; a stack installed from the binary stays on it" (`main.py:822-824`).

## 5. Simplest fix (if the maintainer confirms "latest-dev" is the intent)

Decision needed first: should **binary-capable** stacks also default to `dev`, which means compiling for hours on a Pi?
The brief says "ALWAYS latest-dev". The minimal reading below keeps binary where published and replaces only the
`pinned` fallback. Say if binary stacks should go to `dev` too.

1. `service_binary_channel.py:78` → `return self.BINARY_CHANNEL if ok else "dev"`, and rewrite the docstring `:69-76`.
   Optionally set `services.py:2785` comment and `service_auto_install.py:926` `source=""`.
2. **Never silently switch** on update: `main.py:1562-1563` and `app.py:1593` should default to the stack's
   **recorded** selector. The provenance `selector` is written at `install.py:975` and read at `:999-1019`; binary uses
   `on_binary_channel`; fall back to `default_channel` only when nothing is installed. Auto-install rows for
   installed stacks (`service_auto_install.py:845`) should preselect that same recorded selector.
3. Surface the platform fallback (§2.2): render `binary_reason` beside a non-binary row (already in the row dict, `:843-844`).
4. Text: CLI help `main.py:554-555,572-573,822-824`, the dry-run line `service_auto_install.py:953`, comments
   `app.py:597,1589-1590`, `manifest.example.toml:301`, `docs/cli.md`, and a CHANGELOG entry.
5. **Image builder and release lane depend on the bare default being `pinned`** (`063a475` message; testlab
   `release` lane installs "on its default channel"). They must pass `--source pinned` explicitly, or the image
   ships branch tips. The image builder script is **not in this repo**, so I could not check it.

Regression tests:
* Fresh install: `default_channel(sid) == "dev"` for every non-binary stack; binary stack on aarch64-trixie
  `== "binary"`. Replace `test_binary_channel.py:46,50-57,68` (they currently assert the opposite).
* Console GET `/auto-install` on a fresh root: each non-binary row has `option[value=dev][selected]`. Invert
  `test_web_auto_install.py:377-382` for rows; keep the "All" placeholder test `:1137-1144`.
* Console form round-trip: POST with the version fields **omitted** → the plan records `dev`
  (`app.py:597` fallback); POST with the rendered defaults → plan equals the rendered selection.
  Update `tests/web/test_web.py:595` (`…preselects_pinned_where_no_binary…`).
* Upgrade from each 0.11.x: parametrize over v0.11.0…v0.11.10 runtime fixtures (state/auto-install*.json with
  `version: pinned`, `profiles/known-working/*.json` present). The rendered default must stay `dev`, so
  per-run markers and compositions never seed it.
* No silent switch: a stack whose provenance selector is `dev` → bare `lhpc update` and bare web Update plan
  `dev` (extend `test_web.py:577-592` `test_bare_update_keeps_the_installed_channel_like_the_cli` with `(dev→dev)`, `(pinned→pinned)`).
* CLI text: `test_auto_install.py:96`, `tests/cli/test_cli.py:679`.

## Could not establish

* The affected box's state (version history, architecture, provenance selectors). I cannot tell whether its rows
  were `binary` before (§2.2) or whether an Update moved a `dev` checkout (§2.4).
* The image builder and release-bot inputs (not in the repo).
* I did not confirm end to end that the update planner moves a `dev` checkout to `pinned` without a prompt.
* Tests: `pytest tests/install/test_binary_channel.py tests/web/test_web_auto_install.py -k "default or channel or version or pinned"`
  → 125 passed, 1 failed (`test_doctor_is_quiet_for_a_healthy_binary_install`). The failure looks environment-related
  and unrelated to F38; I did not investigate it.
