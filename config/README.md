# Configuration

Five distinct configuration concerns, kept separate on purpose:

| Concern | File | Tracked in Git? | Notes |
|---|---|---|---|
| Tracked defaults | `manifest.example.toml` | yes | Central stack/component/resource definitions. |
| Known-good profiles | `profiles.example.toml` | yes | Recovery targets; rollback/repair read these. |
| User-local overrides | `local.toml` | **no** (git-ignored) | Machine-specific paths, ports, pins. |
| Secrets | `secrets.toml` | **no** (git-ignored, `0600`) | Passwords, HMAC keys, callsigns, tokens. |
| Generated runtime state | written under the runtime root | **no** | Never in the dev checkout. |

Rules:

- Never put passwords, tokens, callsigns, HMAC secrets or private keys into any
  `*.example.toml` or other tracked file.
- `local.toml` and `secrets.toml` are preserved by default during uninstall.
- The loader reads the tracked manifest/defaults, then merges the runtime-local
  `local.toml` (operator overrides) and reads `secrets.toml` separately.

The development checkout (`~/src/loraham-pi-control`) is **not** the runtime
installation root (`~/loraham-pi-control`). `repo_path` values in the manifest
are runtime-root-relative (e.g. `src/loraham-daemon`); the bootstrap/install
step resolves them. See `docs/architecture.md`.
